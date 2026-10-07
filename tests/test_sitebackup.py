# Tank Monitor Console Simulator -- a training simulator for TLS-350
# compatible tank monitor consoles.
# Copyright (C) 2026 Verbose Software
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License (LICENSE) for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""Loading a site snapshot: the programming, and the reports read back in.

The strongest check there is without a site's own file is a ROUND TRIP: a
console with a history behind it answers every report the multitool pulls,
those replies are written into a .vrset exactly as the multitool writes them,
and a fresh console that loads the file must then answer the same reports
the same way. Every parser in `sitebackup` reads a layout this console's own
renderer prints, so a parser that drifts from its renderer fails here.

The real console's own frames in `tests/console_capture/raw` are site data
and gitignored; the tests that read them skip without them.
"""
import os
import re
import sys
import tempfile
import time
import unittest
import unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tls350sim import delivery, leaktest, presets, sitebackup  # noqa: E402
from tls350sim import wire                                     # noqa: E402
from tls350sim.console import Console                          # noqa: E402

RAW = os.path.join(HERE, "console_capture", "raw")

# What the multitool's `--with-reports` pulls that this console reads back
PULLED = ["102", "201", "205", "301", "111", "112", "113", "202", "207",
          "208", "251", "373", "383"]
# ...and the reports that are DERIVED from what was read back, which have to
# agree as well or the import put a record in the wrong store
DERIVED = ["206", "302", "114", "20C"]


def _body(reply):
    """A reply without its frame, its echo, its stamp and its checksum."""
    text = re.sub(r"&&[0-9A-F]{4}", "", reply.decode("latin-1"))
    return text.splitlines()[3:]


def snapshot(console, path, codes=PULLED):
    """Write `console` out as a multitool site snapshot would."""
    console.archive_save(path)
    with open(path, encoding="utf-8") as fh:
        records = fh.read()
    h = wire.Handler(console, verbose=False)
    out = ["# VRSET v1  host=10.0.0.9 console=350 time=2026-09-22T10:00:00 "
           f"format=computer swver={console.version} "
           f"smodule={console.s_module_number()} snapshot=1", records]
    for code in codes:
        hx = h.handle(f"\x01I{code}00\r".encode()).hex()
        out.append(f"#R BEGIN {code} data Test|Report {code}")
        out += ["#R X " + hx[k:k + 128] for k in range(0, len(hx), 128)]
        out.append(f"#R END {code}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def busy_site(state, name):
    """A preset with something in every store the import fills."""
    c = Console(state)
    presets.load(c, name)
    c.tick()
    c.sensor_state[("liquid", "1")] = "fuel"
    c.compute_alarms()
    now = time.mktime(c.now())
    c.leaks._record(leaktest.Result("tank", 1, "periodic", leaktest.PASSED,
                                    0.2, 6, 5000, now - 3 * 86400))
    c.leaks._record(leaktest.Result("tank", 1, "gross", leaktest.PASSED,
                                    3.0, 0, 5100, now - 2 * 86400))
    c.leaks._record(leaktest.Result("tank", 2, "annual", leaktest.FAILED,
                                    0.1, 8, 4000, now - 86400))
    if c.has("plld"):
        c.leaks._record(leaktest.Result("plld", 1, "gross", leaktest.PASSED,
                                        3.0, 0, 0, now - 5400))
        c.leaks._record(leaktest.Result("plld", 1, "periodic",
                                        leaktest.FAILED, 0.2, 0, 0,
                                        now - 7200))
    if c.csld.enabled(1):
        c.csld.results[c.csld.primary(1)] = ("PASS", now - 4 * 86400)
    drop = delivery.Delivery(1, {"at": now - 7200, "volume": 3000.0,
                                 "tc": 2990.0, "water": 0.5, "temp": 60.1,
                                 "height": 30.2})
    drop.end = {"at": now - 3600, "volume": 8000.0, "tc": 7980.0,
                "water": 0.5, "temp": 58.3, "height": 60.4}
    c.deliveries.records[1] = [drop]
    # an alarm nothing in the model raises by itself, so it has to be posted
    c.post("02", "05", 2)
    c.compute_alarms()
    return c


class RoundTrip(unittest.TestCase):
    """A console's snapshot, loaded into another, answers the same."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # The wall clock held still, as `test_console.held_clock` does: the
        # source is a live console, and a leaking tank or a wandering
        # temperature read after the snapshot is a later site, not the one
        # the file holds.
        held = time.time()
        patcher = unittest.mock.patch("time.time", lambda: held)
        patcher.start()
        self.addCleanup(patcher.stop)

    def trip(self, name):
        src = busy_site(os.path.join(self.dir, "src.json"), name)
        path = os.path.join(self.dir, "site.vrset")
        snapshot(src, path)
        dst = Console(os.path.join(self.dir, "dst.json"))
        done = sitebackup.load(dst, path)
        return src, dst, done

    def assertSameReports(self, src, dst):
        a, b = wire.Handler(src, verbose=False), wire.Handler(dst, verbose=False)
        for code in PULLED + DERIVED:
            with self.subTest(code=code):
                self.assertEqual(_body(a.handle(f"\x01I{code}00\r".encode())),
                                 _body(b.handle(f"\x01I{code}00\r".encode())))

    def test_every_preset_round_trips(self):
        for name in presets.PRESETS:
            with self.subTest(site=name):
                src, dst, done = self.trip(name)
                self.assertEqual(done["skipped"], [])
                self.assertEqual(dst.modules, src.modules)
                self.assertEqual(dst.version, src.version)
                self.assertEqual(dst.s_module_number(), src.s_module_number())
                self.assertSameReports(src, dst)

    def test_the_first_look_logs_nothing_new(self):
        """The site's alarms are already in its history: the console's
        first scan after loading must not log them again at its own time."""
        _src, dst, _done = self.trip("Compliance site, CSLD and sensors")
        before = list(dst.alarm_log)
        dst.compute_alarms()
        self.assertEqual(dst.alarm_log, before)

    def test_what_the_model_cannot_raise_is_posted_and_clearable(self):
        _src, dst, done = self.trip("Two-tank retail site")
        self.assertIn("020502", dst.site_backup["posted"])
        self.assertIn("020502", dst.compute_alarms())
        self.assertEqual(sitebackup.clear_posted(dst), 1)
        self.assertNotIn("020502", dst.compute_alarms())
        self.assertEqual(dst.site_backup["posted"], [])

    def test_the_reports_survive_a_restart(self):
        _src, dst, done = self.trip("Two-tank retail site")
        again = Console(dst.state_path)
        kept = sitebackup.reports(again)
        self.assertEqual(len(kept), done["reports"])
        self.assertEqual([b.code for b in kept], PULLED)
        self.assertTrue(all(b.raw for b in kept))

    def test_a_reset_forgets_the_site(self):
        _src, dst, _done = self.trip("Two-tank retail site")
        dst.reset(keep_clock=True)
        self.assertEqual(sitebackup.reports(dst), [])

    def test_a_backup_without_reports_is_programming_only(self):
        src = busy_site(os.path.join(self.dir, "src.json"),
                        "Two-tank retail site")
        path = os.path.join(self.dir, "plain.vrset")
        src.archive_save(path)
        dst = Console(os.path.join(self.dir, "dst.json"))
        before = dict(dst.modules)
        done = sitebackup.load(dst, path)
        self.assertGreater(done["values"], 0)
        self.assertEqual(done["reports"], 0)
        # no 102, so the cards stay where they were rather than going
        self.assertEqual(dst.modules, before)
        self.assertTrue(any("SYSTEM CONFIGURATION" in s
                            for s in done["skipped"]))


class TheSitesSoftware(unittest.TestCase):
    """A backup's header names the chip the site runs, `sw=346123-100-C
    swver=23`, and the console takes it on: the version, and the part
    number as the site reported it rather than one derived from it."""

    def backup(self, header):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        path = os.path.join(d, "site.vrset")
        src.archive_save(path)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# VRSET v1  host=10.0.0.9 console=350 {header}\n"
                     + body)
        dst = Console(os.path.join(d, "dst.json"))
        return dst, sitebackup.load(dst, path)

    def test_the_version_and_the_part_number_are_the_sites(self):
        c, done = self.backup("sw=346123-100-C swver=23")
        self.assertEqual(c.version, 23)
        info = c.software_info()
        self.assertEqual(info["number"], "346123-100-C")
        self.assertTrue(info["version"].startswith("123."))
        reply = wire.Handler(c, verbose=False).handle(b"\x01I90200\r")
        self.assertIn(b"SOFTWARE# 346123-100-C", reply)
        self.assertIn("software version 23, SOFTWARE# 346123-100-C",
                      done["applied"])

    def test_it_survives_a_restart(self):
        c, _done = self.backup("sw=346123-100-C swver=23")
        c.save()
        back = Console(c.state_path)
        back.load()
        self.assertEqual(back.software_info()["number"], "346123-100-C")

    def test_a_part_for_another_version_is_not_taken(self):
        c, _done = self.backup("sw=346126-100-B swver=23")
        self.assertEqual(c.version, 23)
        self.assertNotEqual(c.software_info()["number"], "346126-100-B")

    def test_changing_the_version_forgets_it(self):
        c, _done = self.backup("sw=346123-100-C swver=23")
        c.set_version(26)
        self.assertNotEqual(c.software_info()["number"], "346123-100-C")


def with_reports(console, path, reports, header="swver=26"):
    """`console`'s programming plus report blocks written as given, the
    way a site snapshot carries them: {code: display-format text}."""
    console.archive_save(path)
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    out = [f"# VRSET v1  host=10.0.0.9 console=350 {header}", body]
    for code, text in reports.items():
        hx = ("\x01\r\nI" + code + "00\r\n" + text.replace("\n", "\r\n")
              + "\r\n\x03").encode("latin-1").hex()
        out.append(f"#R BEGIN {code} data Test|Report {code}")
        out += ["#R X " + hx[k:k + 128] for k in range(0, len(hx), 128)]
        out.append(f"#R END {code}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


# Made-up figures in the shape a version 23 console prints them
RESULTS_373 = """OCT  7, 2026  9:42 AM

PRESSURE LINE LEAK TEST RESULTS

Q 1:LINE ONE

 3.0 GAL/HR RESULTS:

LAST TEST:
OCT  7, 2026  9:30 AM PASS

NUMBER OF TESTS PASSED
  PREV 24 HOURS :    30
 SINCE MIDNIGHT :     8


0.10 GAL/HR RESULTS:

SEP 19, 2026 11:00 PM PASS
MAR 20, 2026 12:30 AM PASS
SEP 16, 2025 10:50 PM FAIL


0.20 GAL/HR RESULTS:

OCT  5, 2026  9:30 AM PASS
SEP 27, 2026  8:40 AM PASS
SEP  3, 2026 10:00 PM PASS


NO-VENT TEST ABORTS:
  2 OUT OF 10 TEST

"""

HISTORY_374 = """OCT  7, 2026  9:42 AM

PRESSURE LINE LEAK TEST HISTORY



Q 1:LINE ONE

LAST  3.0 PASS:               OCT  7, 2026  9:30 AM


FIRST 0.10 PASS EACH MONTH:   SEP 19, 2026 11:00 PM
                              MAR 20, 2026 12:30 AM


FIRST 0.20 PASS EACH MONTH:   OCT  5, 2026  9:30 AM
                              SEP  3, 2026 10:00 PM
                              AUG  3, 2026 12:00 AM

"""


class TheLineResults(unittest.TestCase):
    """373 and 374 come in whole: every result at each rate, the 3.0 pass
    counts, the first pass of each month, and the no-vent count. A version
    23 site's lines loaded with one result a rate and `PREV 24 HOURS : 1`
    where the site had sixty."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Truck stop, four tanks and BIR")
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"373": RESULTS_373, "374": HISTORY_374})
        self.c = Console(os.path.join(d, "dst.json"))
        # no 102 in this backup, so the cards are the ones already fitted
        self.c.modules = dict(src.modules)
        self.done = sitebackup.load(self.c, path)
        self.c.clock_offset = (sitebackup.parse_when("OCT  7, 2026  9:42 AM")
                               - time.time())
        self.h = wire.Handler(self.c, verbose=False)

    def report(self, code):
        return self.h.handle(f"\x01I{code}01\r".encode()).decode("latin-1")

    def test_every_precision_result_newest_first(self):
        text = self.report("373")
        for line in ("SEP 19, 2026 11:00 PM PASS", "MAR 20, 2026 12:30 AM PASS",
                     "SEP 16, 2025 10:50 PM FAIL", "SEP  3, 2026 10:00 PM PASS"):
            self.assertIn(line, text)
        self.assertLess(text.index("SEP 19, 2026"), text.index("MAR 20, 2026"))

    def test_the_latest_is_the_newest_and_not_the_last_listed(self):
        latest = self.c.leaks.result("plld", 1, "annual")
        self.assertEqual(latest.started,
                         sitebackup.parse_when("SEP 19, 2026 11:00 PM"))

    def test_the_pass_counts_are_the_sites(self):
        text = self.report("373")
        self.assertIn("  PREV 24 HOURS :    30", text)
        self.assertIn(" SINCE MIDNIGHT :     8", text)

    def test_the_no_vent_count_is_the_sites(self):
        self.assertIn("  2 OUT OF 10 TEST", self.report("373"))

    def test_the_month_lists_are_the_sites_newest_first(self):
        text = self.report("374")
        self.assertIn("LAST  3.0 PASS:               OCT  7, 2026  9:30 AM",
                      text)
        self.assertIn("FIRST 0.20 PASS EACH MONTH:   OCT  5, 2026  9:30 AM",
                      text)
        self.assertIn(" " * 30 + "AUG  3, 2026 12:00 AM", text)

    def test_a_busy_line_keeps_its_rare_results(self):
        """Hundreds of 3.0 passes do not push the 0.10s out of history."""
        now = time.mktime(self.c.now())
        for i in range(300):
            self.c.leaks._record(leaktest.Result(
                "plld", 1, "gross", leaktest.PASSED, 3.0, 0, 0, now + i))
        self.assertIn("MAR 20, 2026 12:30 AM PASS", self.report("373"))


STATUS_381 = """OCT  7, 2026  9:42 AM

PRESSURE LINE LEAK STATUS

LINE                      DISPENSING  TEST STATUS            PUMP    HANDLE
Q 1:LINE ONE              ENABLED     TEST COMPLETE          OFF     OFF

ACTIVE ALARMS:


Q 2:LINE TWO              ENABLED     TESTING 0.20 GAL/HR    OFF     OFF

ACTIVE ALARMS:

"""


class TheLineStatus(unittest.TestCase):
    """381 heads each line with its own ACTIVE ALARMS, and a line the site
    shows testing is testing when the backup has loaded."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Two-tank retail site")
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"381": STATUS_381})
        self.c = Console(os.path.join(d, "dst.json"))
        self.c.modules = dict(src.modules)
        self.done = sitebackup.load(self.c, path)

    def test_a_block_for_each_line(self):
        text = wire.Handler(self.c, verbose=False).handle(
            b"\x01I38100\r").decode("latin-1")
        self.assertEqual(text.count("ACTIVE ALARMS:"), 2)
        self.assertLess(text.index("Q 1:"), text.index("ACTIVE ALARMS:"))
        self.assertLess(text.index("ACTIVE ALARMS:"), text.index("Q 2:"))

    def test_the_line_the_site_was_testing_is_testing(self):
        two = self.c.lines.line("plld", 2)
        self.assertTrue(two.running())
        self.assertEqual(two.leg, "periodic")
        self.assertFalse(self.c.lines.line("plld", 1).running())
        self.assertTrue(any("Q2 testing at 0.20" in a
                            for a in self.done["applied"]))


TANK_206 = """OCT  7, 2026  9:42 AM

TANK ALARM HISTORY

TANK 1  TANK ONE

     HIGH WATER ALARM         JUL  7, 2026 12:34 PM
                              JUL  7, 2025  9:47 AM
                              JUL 11, 2024 11:22 AM

"""

LINES_382 = """OCT  7, 2026  9:42 AM

PRESSURE LINE LEAK ALARM HISTORY REPORT

Q 1:LINE ONE
     JUL 22, 2026 12:29 PM    PLLD SHUTDOWN ALARM
     JUL 22, 2026 12:29 PM    GROSS LINE FAIL
     JUL  7, 2025 11:16 AM    PLLD SHUTDOWN ALARM

"""


class TheDeviceHistories(unittest.TestCase):
    """206 and 382 outlast 111 and 112's fifty rows on a real console, and
    a backup brings them in: a version 23 site's tank alarms from 2023 and
    line shutdowns from 2024 loaded as NO ALARM HISTORY."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Two-tank retail site")
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"206": TANK_206, "382": LINES_382})
        self.c = Console(os.path.join(d, "dst.json"))
        self.c.modules = dict(src.modules)
        sitebackup.load(self.c, path)
        self.h = wire.Handler(self.c, verbose=False)

    def report(self, code):
        return self.h.handle(f"\x01I{code}00\r".encode()).decode("latin-1")

    def test_the_tank_history_comes_in(self):
        text = self.report("206")
        self.assertIn("HIGH WATER ALARM         JUL  7, 2026 12:34 PM", text)
        self.assertIn(" " * 30 + "JUL 11, 2024 11:22 AM", text)

    def test_the_line_history_comes_in(self):
        text = self.report("382")
        self.assertIn("JUL 22, 2026 12:29 PM    GROSS LINE FAIL", text)
        self.assertIn("JUL  7, 2025 11:16 AM    PLLD SHUTDOWN ALARM", text)

    def test_three_of_a_type_on_a_tank_and_ten_on_a_line(self):
        for i in range(5):
            self.c.keep_device_history({"aa": "02", "nn": "03", "tt": "01",
                                        "at": f"26080{i}1200", "state": "02"})
        for i in range(15):
            self.c.keep_device_history({"aa": "21", "nn": "01", "tt": "01",
                                        "at": f"2608{i:02d}1200",
                                        "state": "02"})
        water = [r for r in self.c.alarm_history("02")
                 if r["tt"] == "01" and r["nn"] == "03"]
        self.assertEqual(len(water), 3)
        line = [r for r in self.c.alarm_history("21") if r["tt"] == "01"]
        self.assertEqual(len(line), 10)

    def test_a_long_111_does_not_push_them_out(self):
        for i in range(120):
            self.c._log_alarms([f"0101{0:02d}"], "02")
        self.assertIn("JUL 11, 2024 11:22 AM", self.report("206"))


class TheTwoHistoriesSplit(unittest.TestCase):
    """Which of 111 and 112 an alarm goes in, where a real console's own
    filing settles it."""

    def test_printer_error_and_high_product_are_non_priority(self):
        c = Console()
        for aa, nn in (("01", "02"), ("02", "07")):
            self.assertFalse(c.priority({"aa": aa, "nn": nn, "tt": "01"}),
                             (aa, nn))


RESULTS_208 = """OCT  7, 2026  9:42 AM

PREVIOUS IN TANK LEAK TEST RESULTS

TANK 1    TANK ONE
TEST TYPE  START TIME              RESULT     RATE  HOURS  VOLUME
 ANNUAL    NO TEST DATA AVAILABLE
 PERIODIC  NO TEST DATA AVAILABLE
 GROSS     OCT  7, 2026  5:04 AM   PASSED    -0.04          4681

"""

HISTORY_207 = """OCT  7, 2026  9:42 AM

TANK LEAK TEST HISTORY

T 1:TANK ONE

LAST GROSS TEST PASSED:
TEST START TIME            HOURS    VOLUME   % VOLUME   TEST TYPE
OCT  7, 2026  5:04 AM                4681       47.1    STANDARD

LAST PERIODIC TEST PASS:
TEST START TIME            HOURS    VOLUME   % VOLUME   TEST TYPE
OCT  7, 2026  4:59 AM       30       8219       82.8      CSLD


FULLEST PERIODIC TEST
PASSED EACH MONTH:

TEST START TIME            HOURS    VOLUME   % VOLUME   TEST TYPE
JAN 25, 2026  4:07 AM       32       8597       86.6      CSLD
DEC  2, 2025 12:29 AM       31       8581       86.4      CSLD
"""


class TheTankLeakReports(unittest.TestCase):
    """208, 203 and 207 as a version 23 site prints them, and what its
    backup brings in: a negative gross rate, each test's own method and
    percentage, and the monthly list in month-of-year order."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Two-tank retail site")
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"208": RESULTS_208, "207": HISTORY_207})
        self.c = Console(os.path.join(d, "dst.json"))
        self.c.modules = dict(src.modules)
        sitebackup.load(self.c, path)
        # no 201 in this backup, so the tanks report what the source's did
        self.c.tank_level = {k: dict(v) for k, v in src.tank_level.items()}
        self.h = wire.Handler(self.c, verbose=False)

    def report(self, code):
        return self.h.handle(f"\x01I{code}01\r".encode()).decode("latin-1")

    def test_a_negative_gross_rate_comes_in(self):
        self.assertIn(" GROSS     OCT  7, 2026  5:04 AM   PASSED    -0.04"
                      "          4681", self.report("208"))

    def test_every_test_type_has_its_row(self):
        text = self.report("208")
        self.assertIn(" ANNUAL    NO TEST DATA AVAILABLE", text)
        self.assertIn(" PERIODIC  NO TEST DATA AVAILABLE", text)

    def test_a_tank_with_no_test_running(self):
        text = self.report("203")
        self.assertIn("\r\n    TEST STATUS: OFF\r\n"
                      "LEAK DATA NOT AVAILABLE ON THIS TANK\r\n", text)

    def test_207_keeps_each_tests_method_and_percent(self):
        text = self.report("207")
        self.assertIn("OCT  7, 2026  4:59 AM       30       8219       82.8"
                      "      CSLD", text)
        self.assertIn("OCT  7, 2026  5:04 AM                4681       47.1"
                      "    STANDARD", text)

    def test_207_lists_the_months_january_to_december(self):
        text = self.report("207")
        self.assertLess(text.index("JAN 25, 2026"), text.index("DEC  2, 2025"))

    def test_207_has_the_station_header_and_its_title(self):
        lines = self.report("207").split("\r\n")
        title = lines.index("TANK LEAK TEST HISTORY")
        self.assertEqual(lines[title - 2:title], ["", ""])
        self.assertEqual(lines[title + 1], "")
        self.assertTrue(lines[title + 2].startswith("T 1:"))
        self.assertEqual(lines[title + 3], "")


CSLD_251 = """OCT  7, 2026  9:41 AM

CSLD TEST RESULTS
TANK PRODUCT                RESULT
  1  TANK ONE               PER: OCT  7, 2026 PASS

"""


class TheCsldResults(unittest.TestCase):
    """A tank on CSLD is a CSLD tank whatever the length of its 611, and
    251 is its table. A version 23 site on CSLD loaded as CSLD NOT ENABLED
    on every tank, and its four passes were never read."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Compliance site, CSLD and sensors")
        src.values["S61101"] = "010207"      # 2 hours, 0.2, method 7: CSLD
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"251": CSLD_251})
        self.c = Console(os.path.join(d, "dst.json"))
        self.c.modules = dict(src.modules)
        sitebackup.load(self.c, path)
        self.c.tank_level = {k: dict(v) for k, v in src.tank_level.items()}

    def test_a_six_character_611_is_csld(self):
        self.assertEqual(self.c.values["S61101"], "010207")
        self.assertTrue(self.c.csld.enabled(1))

    def test_251_is_a_table_with_the_sites_result(self):
        text = wire.Handler(self.c, verbose=False).handle(
            b"\x01I25101\r").decode("latin-1")
        self.assertIn("CSLD TEST RESULTS\r\nTANK PRODUCT                RESULT",
                      text)
        row = next(l for l in text.split("\r\n") if l.startswith("  1  "))
        self.assertEqual(row[28:], "PER: OCT  7, 2026 PASS")


SHIFT_204 = """OCT  7, 2026  9:42 AM

 SHIFT REPORT

SHIFT 1 TIME:  4:30 AM

TANK PRODUCT

  1  TANK ONE               VOLUME TC VOLUME  ULLAGE  HEIGHT  WATER   TEMP
SHIFT  1 STARTING VALUES      6844      6773    3088   62.47   0.00  74.65
         ENDING VALUES        8175      8089    1757   73.63   0.00  74.82
         DELIVERY VALUE       2063
         TOTALS                732

"""


class TheLastShift(unittest.TestCase):
    """204 is System Setup's Last-Shift Inventory and answers without BIR;
    a backup brings the shift in, and TOTALS is the shift's sales."""

    def setUp(self):
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        presets.load(src, "Two-tank retail site")
        src.software.pop("bir", None)
        src.values["S50201"] = "0430"
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"204": SHIFT_204})
        self.c = Console(os.path.join(d, "dst.json"))
        self.c.modules = dict(src.modules)
        sitebackup.load(self.c, path)
        self.c.software.pop("bir", None)
        self.c.tank_level = {k: dict(v) for k, v in src.tank_level.items()}

    def test_the_shift_comes_in_and_prints_as_the_site_did(self):
        text = wire.Handler(self.c, verbose=False).handle(
            b"\x01I20401\r").decode("latin-1")
        for line in (" SHIFT REPORT", "SHIFT 1 TIME:  4:30 AM",
                     "SHIFT  1 STARTING VALUES      6844      6773    3088"
                     "   62.47   0.00  74.65",
                     "         ENDING VALUES        8175      8089    1757"
                     "   73.63   0.00  74.82",
                     "         DELIVERY VALUE       2063",
                     "         TOTALS                732"):
            self.assertIn(line, text)


class TheAdjustedDeliveryAndTankerReports(unittest.TestCase):
    """20A, 20B and 391 as a version 23 site with no BIR printed them:
    headings over nothing, 20B in its own columns, and the tanker load
    report titled, with NO DATA HISTORY under a tank with none."""

    def setUp(self):
        self.c = Console()
        presets.load(self.c, "Two-tank retail site")
        self.c.software.pop("bir", None)
        self.c.tick()
        self.h = wire.Handler(self.c, verbose=False)

    def report(self, code):
        return self.h.handle(f"\x01I{code}00\r".encode()).decode("latin-1")

    def test_20a_without_bir_has_headings_and_no_rows(self):
        lines = self.report("20A").split("\r\n")
        self.assertIn("ADJUSTED DELIVERY REPORT", lines)
        head = next(i for i, l in enumerate(lines) if l.startswith("TANK  1"))
        self.assertTrue(lines[head + 2].startswith("INCREASE DATE/TIME"))
        self.assertEqual(lines[head + 3:head + 6], ["", "", ""])

    def test_20b_is_its_own_report(self):
        text = self.report("20B")
        self.assertIn("BIR ADJUSTED DELIVERY REPORT", text)
        self.assertIn("DELIVERY START   DATE   DELIVERY  END    DATE  VOLUME"
                      "  VOLUME   DELIV   DELIV", text)

    def test_391_titled_and_says_when_there_is_nothing(self):
        text = self.report("391")
        self.assertIn("TANKER LOAD REPORT", text)
        self.assertIn("T 1:", text)
        self.assertIn("NO START DATE/TIME  GALLON  TEMP    END DATE/TIME"
                      "  GALLON  TEMP   TOTAL\r\nNO DATA HISTORY", text)


def cage_102(rows):
    """A 102 in the shape a version 23 site prints it, from (slot, name,
    POR, CURRENT); a slot of 0 or less is COMM -slot."""
    out = ["OCT  7, 2026  9:31 AM", "", "SYSTEM CONFIGURATION",
           "SLOT  BOARD TYPE                    POWER ON RESET     CURRENT"]
    for slot, name, por, now in rows:
        where = f"{slot:3d}   " if slot > 0 else f"      COMM {-slot} "
        out.append(f"{(where + name)[:36]:<36s}{por:9d}{now:17d}")
    return "\n".join(out) + "\n"


class TheSitesCage(unittest.TestCase):
    """102 places the cards where the site has them and reads what the
    site read; a version 23 site's PLLD power boards in slots 11 and 12
    came out at 9 and 10, and its empty slots at 15,000,000 against its own
    ten million."""

    def setUp(self):
        rows = [(1, "4 PROBE / G.T.", 164230, 162486),
                (2, "PLLD SENSOR BD", 3930, 3932)]
        rows += [(n, "UNUSED", 10010000 + n, 9600000 + n)
                 for n in range(3, 11)]
        rows += [(11, "PLLD POWER BD", 99375, 99366),
                 (12, "PLLD POWER BD", 100162, 100016)]
        rows += [(n, "UNUSED", 10010000 + n, 9600000 + n)
                 for n in range(13, 17)]
        rows += [(-1, "UNUSED", 9998474, 9603509),
                 (-2, "UNUSED", 9998474, 9603509),
                 (-3, "RS232 SERIAL BD", 14924, 14908)]
        rows += [(-n, "UNUSED", 9998474, 9603509) for n in (4, 5, 6)]
        d = tempfile.mkdtemp()
        src = Console(os.path.join(d, "src.json"))
        path = os.path.join(d, "site.vrset")
        with_reports(src, path, {"102": cage_102(rows)})
        self.c = Console(os.path.join(d, "dst.json"))
        sitebackup.load(self.c, path)

    def lines(self):
        return wire.Handler(self.c, verbose=False).handle(
            b"\x01I10200\r").decode("latin-1").split("\r\n")

    def test_the_cards_are_in_the_sites_slots(self):
        text = "\n".join(self.lines())
        self.assertIn(" 11   PLLD POWER BD                     99375"
                      "            99366", text)
        self.assertIn("  9   UNUSED", text)

    def test_the_empty_slots_read_what_the_site_read(self):
        self.assertIn("  3   UNUSED                         10010003"
                      "          9600003", "\n".join(self.lines()))

    def test_a_changed_cage_is_packed_again(self):
        self.c.modules["plldctl"] = 1
        self.assertIn("  9   PLLD POWER BD", "\n".join(self.lines()))


class WhatASiteAnswersEmpty(unittest.TestCase):
    """Four reports a version 23 site with tanks reporting, no BIR and no
    Fuel Manager answered with the frame and nothing in it, and this
    console filled."""

    def setUp(self):
        self.c = Console()
        presets.load(self.c, "Two-tank retail site")
        for key in ("bir", "fuelman"):
            self.c.software.pop(key, None)
        self.c.tick()
        self.h = wire.Handler(self.c, verbose=False)

    def body(self, cmd):
        reply = self.h.handle(("\x01" + cmd + "\r").encode()).decode("latin-1")
        self.assertNotIn("9999", reply, cmd)
        return [l for l in reply.strip("\x01\x03\r\n").split("\r\n")[2:]
                if l.strip()]

    def test_ticketed_deliveries_need_bir(self):
        self.assertEqual(self.body("I22100"), [])

    def test_a_tank_chart_needs_a_step(self):
        self.assertEqual(self.body("I21100"), [])
        self.assertNotEqual(self.body("I21100001000"), [])

    def test_no_inventory_is_stored(self):
        self.assertEqual(self.body("I2E200"), [])

    def test_the_fls_volume_table_needs_fuel_manager(self):
        self.assertEqual(self.body("I28200"), [])


class TheLineDiagnostics(unittest.TestCase):
    """B87 to B8A, which the Multitool backs up since its 830c86d, bring
    the measurements back that the 3.0, MID, 0.20 and 0.10 DIAG printouts
    are made of. Without them a loaded site's diagnostic printed nothing."""

    DIAG = ["B87", "B88", "B89", "B8A"]

    def setUp(self):
        held = time.time()
        patcher = unittest.mock.patch("time.time", lambda: held)
        patcher.start()
        self.addCleanup(patcher.stop)
        d = tempfile.mkdtemp()
        self.src = Console(os.path.join(d, "src.json"))
        presets.load(self.src, "Two-tank retail site")
        self.src.tick()
        self.src.lines.start("plld", 1, "annual")
        for _ in range(400):
            self.src.clock_offset += 30.0
            self.src.leaks.tick()
            if not self.src.lines.line("plld", 1).running():
                break
        path = os.path.join(d, "site.vrset")
        snapshot(self.src, path, codes=PULLED + self.DIAG)
        self.dst = Console(os.path.join(d, "dst.json"))
        self.done = sitebackup.load(self.dst, path)
        self.dst.clock_offset = self.src.clock_offset

    def test_there_was_something_to_carry(self):
        line = self.src.lines.line("plld", 1)
        self.assertTrue(line.readings["gross"])
        self.assertTrue(line.cycles["periodic"])

    def test_each_diagnostic_reads_as_the_sites(self):
        a = wire.Handler(self.src, verbose=False)
        b = wire.Handler(self.dst, verbose=False)
        for code in self.DIAG:
            with self.subTest(code=code):
                self.assertEqual(_body(a.handle(f"\x01I{code}00\r".encode())),
                                 _body(b.handle(f"\x01I{code}00\r".encode())))

    def test_the_3_0_diag_printout_has_the_tests(self):
        from tls350sim import printer
        text = "\n".join(str(l) for l in printer.line_diag(
            self.dst, "plld", 1, "gross"))
        # PON P1 P2 under 3.0 TEST PASSES, as the paper prints a reading
        passes = text.split("3.0 TEST PASSES")[1].split("3.0 TEST FAILS")[0]
        self.assertRegex(passes, r"\n\d+\.\d \d+\.\d \d+\.\d\n")
        self.assertTrue(any("line diagnostic measurement" in a
                            for a in self.done["applied"]))


class TheHistoriesSurviveARestart(unittest.TestCase):
    """What a backup brings in, and what the console records itself, is
    still there after the program is closed and started again -- a real
    console keeps it through a power cycle. It used to be held in memory
    alone, and only the raw report blocks were saved."""

    CODES = ["111", "112", "206", "207", "208", "202", "373", "374",
             "382", "204", "102", "B87", "B89"]

    def test_every_report_reads_the_same_after_a_restart(self):
        held = time.time()
        with unittest.mock.patch("time.time", lambda: held):
            d = tempfile.mkdtemp()
            src = busy_site(os.path.join(d, "src.json"),
                            "Truck stop, four tanks and BIR")
            src.values["S50201"] = "0430"
            src.shifts.begin(1, held - 3600)
            src.shifts.begin(1, held)
            src.lines.start("plld", 1, "periodic")
            for _ in range(400):
                src.clock_offset += 30.0
                src.leaks.tick()
                if not src.lines.line("plld", 1).running():
                    break
            src.save()
            back = Console(src.state_path)
            back.load()
            a = wire.Handler(src, verbose=False)
            b = wire.Handler(back, verbose=False)
            for code in self.CODES:
                with self.subTest(code=code):
                    self.assertEqual(
                        _body(a.handle(f"\x01I{code}00\r".encode())),
                        _body(b.handle(f"\x01I{code}00\r".encode())))


def modules_header(console, cap=24):
    """The `# INSTALLED MODULES` block the multitool (v0.62.0) writes: the
    102's non-blank lines, stripped, cut at `cap` with a count of the rest."""
    reply = wire.Handler(console, verbose=False).handle(b"\x01I10200\r")
    body = [ln.strip() for ln in sitebackup.report_lines(
        sitebackup.Block("102", "", "", "data", reply, None)) if ln.strip()]
    out = ["# INSTALLED MODULES -- the console's System Configuration "
           "report (I10200) at backup time:"]
    out += ["#   " + ln for ln in body[:cap]]
    if len(body) > cap:
        out.append(f"#   ... {len(body) - cap} more line(s)")
    return out


class ModulesBlock(unittest.TestCase):
    """A plain backup from multitool v0.62.0 on carries the cage too."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = Console(os.path.join(self.dir, "src.json"))
        presets.load(self.src, "Truck stop, four tanks and BIR")

    def load(self, header):
        path = os.path.join(self.dir, "plain.vrset")
        self.src.archive_save(path)
        with open(path, encoding="utf-8") as fh:
            records = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# VRSET v1  host=x console=350 modules=tank,sensor\n"
                     + "\n".join(header) + "\n" + records)
        dst = Console(os.path.join(self.dir, "dst.json"))
        return dst, sitebackup.load(dst, path)

    def test_the_whole_cage(self):
        dst, done = self.load(modules_header(self.src, cap=99))
        self.assertEqual(dst.modules, self.src.modules)
        self.assertEqual(dst.comm_layout(), self.src.comm_layout())
        self.assertEqual(done["skipped"], [])

    def test_cut_before_the_comm_bay_keeps_a_port(self):
        """24 lines is the station header, the title and the sixteen slots:
        the comm rows are what a cut loses, and without them the simulator
        would be left with no serial port to answer on."""
        dst, done = self.load(modules_header(self.src, cap=22))
        self.assertTrue(dst.serial_port_works())
        self.assertEqual({k: n for k, n in dst.modules.items()
                          if k in ("probe", "liquid", "plld", "io", "pump")},
                         {k: n for k, n in self.src.modules.items()
                          if k in ("probe", "liquid", "plld", "io", "pump")})
        self.assertTrue(any("communication bay" in s
                            for s in done["skipped"]))

    def test_the_corpus_shape_with_no_readings(self):
        """The multitool's own corpus writes `SLOT 1  INTERSTITIAL BD`: no
        readings, so the names alone have to place the cards."""
        dst, done = self.load([
            "# INSTALLED MODULES -- the console's System Configuration "
            "report (I10200) at backup time:",
            "#   SYSTEM CONFIGURATION",
            "#   SLOT 1  INTERSTITIAL BD",
            "#   SLOT 2  4 PROBE / G.T.",
            "#   COMM 1  RS232 SERIAL BD",
            "#   COMM 2  SERIAL SAT BD"])
        self.assertEqual(dst.modules, {"liquid": 1, "probe": 1,
                                       "rs232": 1, "ssat": 1})
        self.assertTrue(dst.probe_gt)
        self.assertEqual(done["skipped"], [])

    def test_a_cage_with_no_port_says_so(self):
        _dst, done = self.load([
            "# INSTALLED MODULES -- x:", "#   SYSTEM CONFIGURATION",
            "#   SLOT 1  4 PROBE / G.T.", "#   COMM 2  SERIAL SAT BD"])
        self.assertTrue(any("will not answer" in s for s in done["skipped"]))


class Refusals(unittest.TestCase):
    """A report the console refused is kept and read as nothing."""

    def test_a_refusal_has_no_lines(self):
        block = sitebackup.Block("2E2", "x", "g", "unsupported",
                                 b"\x019999FF1B\x03", None)
        self.assertEqual(sitebackup.report_lines(block), [])
        self.assertIn("9999", sitebackup.report_text(block))

    def test_odd_hex_is_dropped_not_raised(self):
        blocks = sitebackup.extract_blocks(
            "#R BEGIN 201 data G|Inv\n#R X 0g\n#R END 201\n")
        self.assertEqual(len(blocks), 1)
        self.assertIsNone(blocks[0].raw)

    def test_an_unterminated_block_keeps_what_it_had(self):
        blocks = sitebackup.extract_blocks(
            "#R BEGIN 201 data G|Inv\n#R X 0102\n")
        self.assertEqual(blocks[0].raw, b"\x01\x02")


class Dates(unittest.TestCase):
    def stamp(self, text, fmt="01"):
        return time.strftime("%y%m%d%H%M", time.localtime(
            sitebackup.parse_when(text, fmt)))

    def test_every_form_a_report_prints(self):
        self.assertEqual(self.stamp(" 1-16-06  8:00AM"), "0601160800")
        self.assertEqual(self.stamp("01-16-06  8:10 PM", "03"), "0601162010")
        self.assertEqual(self.stamp("16-01-06  8:10", "05"), "0601160810")
        self.assertEqual(self.stamp("06-01-16  8:10", "06"), "0601160810")
        self.assertEqual(self.stamp("JAN 16, 2006 12:20 PM"), "0601161220")
        self.assertEqual(self.stamp("JAN  1, 2007   8:02 AM"), "0701010802")
        self.assertEqual(self.stamp("JAN 16 2006 20:10", "02"), "0601162010")


def _raw(name):
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        raise unittest.SkipTest("no tests/console_capture (site data)")
    with open(path, "rb") as fh:
        return sitebackup.report_lines(
            sitebackup.Block(name[1:4], "", "", "data", fh.read(), None))


class RealConsole(unittest.TestCase):
    """The captured console's own frames, which no renderer here wrote."""

    def test_its_cage(self):
        modules, comm, flags, unplaced = sitebackup.parse_cage(
            _raw("I10200.bin"))
        self.assertEqual(modules, {"probe": 1, "plld": 1, "plldctl": 1,
                                   "rs232": 1, "ssat": 1})
        self.assertEqual(comm, {1: "rs232", 2: "ssat"})
        self.assertEqual(flags, {"probe_gt": True})
        self.assertEqual(unplaced, [])

    def test_its_alarm_history(self):
        rows, unplaced = sitebackup.parse_alarm_rows(_raw("I11100.bin"))
        self.assertEqual(unplaced, [])
        self.assertEqual([(r["aa"], r["nn"], r["at"], r["state"])
                          for r in rows],
                         [("01", "04", "0601160800", "01"),
                          ("01", "04", "0601160800", "02")])


def _snapshots():
    """Real site snapshots, from where `test_formats` looks for backups:
    tests/real_backups/ or $VR_BACKUPS. Site data, never committed."""
    d = (os.environ.get("VR_BACKUPS")
         or os.path.join(HERE, "real_backups"))
    if not os.path.isdir(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".vrset"):
            continue
        path = os.path.join(d, name)
        with open(path, encoding="utf-8", errors="replace") as fh:
            if sitebackup.REPORT_BEGIN in fh.read():
                out.append(path)
    return out


def _slot_names(lines):
    """A 102's board names by slot, without the two readings beside them,
    which are a resistor being measured and never read the same twice."""
    return [re.sub(r"\s+\d+\s+\d+\s*$", "", ln) for ln in lines
            if re.search(r"\s\d+\s+\d+\s*$", ln)]


class RealSnapshots(unittest.TestCase):
    """Every real site snapshot on this machine loads with nothing left
    over, and the reports read back into the model answer as the site did.

    `2026-09-22`, the bench TLS-350's own snapshot, is where two of the
    rules in `sitebackup` came from: its 113 printed a standing alarm with
    no date at all, and its 111 and 112 did not answer."""

    # reports whose every line follows from what the import read back
    SAME = ("101", "201", "205", "206")

    def test_each_one(self):
        paths = _snapshots()
        if not paths:
            self.skipTest("no site snapshots in tests/real_backups/")
        for path in paths:
            with self.subTest(snapshot=os.path.basename(path)):
                c = Console(os.path.join(tempfile.mkdtemp(), "s.json"))
                done = sitebackup.load(c, path)
                self.assertEqual(done["skipped"], [])
                h = wire.Handler(c, verbose=False)
                for block in sitebackup.reports(c):
                    if block.status == "empty":
                        # what the site had nothing to say in, this says
                        # nothing in either
                        now = h.handle(f"\x01I{block.code}00\r".encode())
                        self.assertEqual(
                            len(now), len(block.raw),
                            f"{block.code}: the site answered it empty")
                        continue
                    if block.status != "data" or block.code not in (
                            self.SAME + ("102",)):
                        continue
                    site = sitebackup.report_lines(block)[1:]
                    now = sitebackup.report_lines(sitebackup.Block(
                        block.code, "", "", "data",
                        h.handle(f"\x01I{block.code}00\r".encode()),
                        None))[1:]
                    if block.code == "102":
                        site, now = _slot_names(site), _slot_names(now)
                    self.assertEqual(site, now, block.code)


def _have_tk():
    try:
        import tkinter
        tkinter.Tk().destroy()
        return True
    except Exception:                               # pragma: no cover
        return False


@unittest.skipUnless(_have_tk(), "no display")
class Window(unittest.TestCase):
    """The Console menu's way in, and the window that shows the reports.
    One Tk app for the class; see test_panel's note on why."""

    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        cls.dir = tempfile.mkdtemp()
        try:
            cls.app = SimApp(Console(os.path.join(cls.dir, "app.json")),
                             10001)
        except Exception as exc:                   # pragma: no cover
            cls.app = None
            raise unittest.SkipTest(f"no usable Tk: {exc}")
        src = busy_site(os.path.join(cls.dir, "src.json"),
                        "Truck stop, four tanks and BIR")
        cls.src = src
        cls.path = os.path.join(cls.dir, "site.vrset")
        snapshot(src, cls.path)

    @classmethod
    def tearDownClass(cls):
        if cls.app is not None:
            cls.app.destroy()

    def menu_labels(self, menu):
        labels = []
        for i in range((menu.index("end") or 0) + 1):
            try:
                labels.append(menu.entrycget(i, "label"))
            except Exception:
                labels.append(None)             # a separator has no label
        return labels

    def test_the_console_menu_offers_it_twice(self):
        bar = self.app.nametowidget(self.app["menu"])
        console = bar.nametowidget(bar.entrycget(
            self.menu_labels(bar).index("Console"), "menu"))
        labels = self.menu_labels(console)
        self.assertIn("Load site backup (.vrset)...", labels)
        self.assertIn("Site reports...", labels)
        sites = console.nametowidget(console.entrycget(
            labels.index("Load example site"), "menu"))
        self.assertIn("From a site backup (.vrset)...",
                      self.menu_labels(sites))

    def test_loading_through_the_menu_and_reading_the_reports(self):
        from unittest import mock
        with mock.patch("tkinter.filedialog.askopenfilename",
                        return_value=self.path), \
                mock.patch("tkinter.messagebox.askyesno",
                           return_value=True) as asked:
            self.app._site_backup_asked()
        self.assertTrue(asked.called)
        c = self.app.console
        self.assertEqual(c.modules, self.src.modules)
        self.assertEqual(c.site_backup["source"], "site.vrset")
        win = self.app._site_win
        self.assertTrue(win.winfo_exists())
        texts = [w for w in _descendants(win) if w.winfo_class() == "Text"]
        boxes = [w for w in _descendants(win)
                 if w.winfo_class() == "Listbox"]
        self.assertEqual(boxes[0].size(), len(PULLED))
        # the first report is 102, and it came across whole: the site's and
        # this console's name the same board in every slot (the readings
        # beside them are a resistor measured afresh each time)
        site = texts[0].get("1.0", "end").splitlines()[1:]
        now = texts[1].get("1.0", "end").splitlines()[1:]
        self.assertIn("SYSTEM CONFIGURATION", site)
        self.assertEqual(_slot_names(site), _slot_names(now))
        # and the clear button takes the posted alarms down
        self.app._clear_site_alarms()
        self.assertEqual(c.site_backup["posted"], [])


def _descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)


if __name__ == "__main__":
    unittest.main()
