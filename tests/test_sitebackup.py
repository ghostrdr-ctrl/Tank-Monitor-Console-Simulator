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
