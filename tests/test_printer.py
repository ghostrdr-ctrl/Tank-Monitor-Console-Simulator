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
"""The printer behind the left door: what comes out, and how wide it is.

The roll is forty characters and everything has to arrive on it. Some reports
are shared with the serial port, which is wider than the roll, so those come
off it folded rather than running off the edge of the paper, and nothing
comes off it at all when the roll has run out, which the console says out loud.
"""
import os
import struct
import sys
import time
import re
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False

from tls350sim import delivery, presets, printer            # noqa: E402
from tls350sim.console import Console, describe_alarms      # noqa: E402


def a_site(name="Truck stop, four tanks and BIR"):
    c = Console()
    presets.load(c, name)
    return c


def every_report(console):
    """One of each, named, so a failure says which report was too wide."""
    return {
        "inventory": printer.inventory(console),
        "alarms": printer.alarms(console),
        "status": printer.status(console),
        "revision": printer.revision(console),
        "setup": printer.setup(console),
        "leak tests": printer.leak_tests(console),
        "deliveries": printer.deliveries(console),
        "csld": printer.csld(console),
        "shift": printer.shift(console),
        "meters": printer.meters(console),
        "sensors": printer.sensors(console),
        "fuel": printer.fuel(console),
        "fuel, every product": printer.fuel_all_products(console),
        "last-shift inventory": printer.last_shift(console),
        "relays": printer.relays(console),
        "service codes": printer.service_codes(console),
        "alarm history": printer.alarm_history(console, system=True),
        "reconciliation": printer.reconcile(console),
        "delivery variance": printer.delivery_variance(console),
        "book variance": printer.book_variance(console),
        "variance analysis": printer.variance_analysis(console),
        "loads": printer.loads(console),
        "leak history": printer.leak_history(console),
        "vmc": printer.vmc(console),
    }


class TheAlarmHistoryReports(unittest.TestCase):
    """FIDELITY W13 and W21 to W26: one report family, seven faults.

    The titles were settled first -- 576013-610 Rev AC Figure 32-2 prints
    function 119's `MAINTENANCE HISTORY` where the SCREEN says `MAINTENANCE
    REPORT`, so paper follows the serial manual's display format and not the
    screen prompt -- and everything below follows from reading those formats.
    """

    def a_console(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.alarm_log = [
            # I206's own sample: two LOW PRODUCT stamps around three
            # INVALID FUEL LEVEL ones, which is the arrangement that breaks
            # a walk that prints a name only on first sight
            {"aa": "02", "nn": "01", "tt": "01", "at": "9512221531",
             "state": "02"},
            {"aa": "02", "nn": "04", "tt": "01", "at": "9512201159",
             "state": "02"},
            {"aa": "02", "nn": "04", "tt": "01", "at": "9512201158",
             "state": "02"},
            {"aa": "02", "nn": "04", "tt": "01", "at": "9512201157",
             "state": "02"},
            {"aa": "02", "nn": "01", "tt": "01", "at": "9512191005",
             "state": "02"},
            {"aa": "02", "nn": "01", "tt": "02", "at": "9512221532",
             "state": "02"},
        ]
        return c

    def lines(self, c, *args, **kw):
        return [str(x) for x in printer.fit(printer.alarm_history(c, *args,
                                                                  **kw))]

    def test_the_groups_do_not_interleave(self):
        """W23. Bucket by name, then order the buckets by their newest
        stamp: the manual's sample puts LOW PRODUCT's two stamps together
        and INVALID FUEL LEVEL's three under their own name."""
        body = [x for x in self.lines(self.a_console(), "T", device=1)
                if x.strip()]
        names = [i for i, x in enumerate(body) if not x.startswith("  ")]
        stamps = [x for x in body if x.startswith("  ")]
        self.assertEqual(len(stamps), 5)
        # every stamp sits under the name above it, and no name repeats
        heads = [body[i] for i in names]
        self.assertEqual(len(heads), len(set(heads)))

    def test_print_gives_the_device_on_the_screen(self):
        """W24. "Press PRINT to print the report for the tank displayed."""
        c = self.a_console()
        one = self.lines(c, "T", device=1)
        self.assertIn("TANK 1  DIESEL", one)
        self.assertNotIn("TANK 2  DIESEL", one)
        self.assertIn("TANK 2  DIESEL", self.lines(c, "T"))

    def test_a_letter_no_category_maps_to_prints_nothing(self):
        """W21. `g` groundtemp and `F` BIR product are two of the seventeen
        screens and neither has an alarm category, so the filter came out
        empty -- and an empty filter printed EVERY alarm the console held,
        of every category, under a groundtemp heading."""
        c = self.a_console()
        for letter in ("g", "F"):
            out = self.lines(c, letter)
            self.assertIn("NO ALARM HISTORY", out)
            self.assertNotIn("  DEC 22, 1995  3:31 PM", out)

    def test_the_long_titles_fold_instead_of_being_clipped(self):
        """W13. Twelve of the fourteen are longer than the roll, and this
        report clipped its own title before `fit()` could fold it -- alone
        among the reports here."""
        out = self.lines(self.a_console(), "Q")
        self.assertIn("PRESSURE LINE LEAK ALARM", out)
        self.assertIn("HISTORY REPORT", out)

    def test_vmc_and_vmci_are_the_right_way_round(self):
        """W22. 35 is the VMCI and 36 is the VMC; the table had the titles
        swapped, so a VMCI alarm printed under the VMC's title."""
        c = self.a_console()
        self.assertIn("VMCI ALARM HISTORY", self.lines(c, "X"))
        self.assertIn("VMC ALARM HISTORY REPORT", self.lines(c, "x"))

    def test_two_families_print_no_title_at_all(self):
        """W25. Function 352 goes straight from the station header to
        `P 1:REGULAR UNLEADED`, and 402 to `INPUT   LOCATION`."""
        c = self.a_console()
        c.modules.update({"vlld": 1, "io": 1})
        c.alarm_log = [{"aa": aa, "nn": "01", "tt": "01",
                        "at": "9512221531", "state": "02"}
                       for aa in ("06", "05")]
        first = {"P": "P 1:", "I": "INPUT   LOCATION"}
        for letter, want in first.items():
            out = [x for x in self.lines(c, letter) if x.strip()]
            stamp = [i for i, x in enumerate(out) if "1995" not in x
                     and ", 20" in x]
            self.assertTrue(stamp, out)
            self.assertTrue(out[stamp[0] + 1].startswith(want), out)

    def test_each_family_has_its_own_identity_line(self):
        """W26. Six shapes, not two."""
        c = self.a_console()
        c.modules.update({"pumpmon": 1, "vmc": 1, "io": 1, "vlld": 1})
        c.alarm_log = [{"aa": aa, "nn": "01", "tt": "01",
                        "at": "9512221531", "state": "02"}
                       for aa in ("02", "12", "21", "35", "36")]
        self.assertIn("TANK 1  DIESEL", self.lines(c, "T"))
        self.assertIn("SENSOR  LOCATION", self.lines(c, "H"))
        self.assertIn("DEVICE  ALARMS", self.lines(c, "X"))
        self.assertIn("VMC   S/N    ALARMS", self.lines(c, "x"))
        # the line-leak families have no head, just the device and its label
        self.assertTrue([x for x in self.lines(c, "Q") if x.startswith("Q 1:")],
                        self.lines(c, "Q"))


class OnThePaper(unittest.TestCase):
    """Forty characters, and everything fits in them."""

    def test_every_report_comes_off_the_roll_within_its_width(self):
        for site in presets.PRESETS:
            console = a_site(site)
            for name, report in every_report(console).items():
                for line in printer.fit(report):
                    self.assertLessEqual(
                        len(line), printer.WIDTH,
                        f"{site} / {name}: {line!r}")

    # A report the console writes for itself ought to be written to fit, and
    # on real paper these do not. The roll is twenty four characters -- the
    # console's own rule on a real 2024 tape measures 24, and the real site
    # tape in tests/tape/ has a longest line of 24 -- and until that was
    # measured WIDTH said 40, which hid the overflow. Each of these is
    # FIDELITY T1, W1 or W3: a report built in a SERIAL layout and printed
    # onto paper. Take a name out when its report is narrowed.
    # `deliveries` came off when its invented empty-report line went: the
    # 28-column `  NO DELIVERY DATA AVAILABLE` was the only thing in that
    # report that did not fit the paper, so removing a string no console
    # prints also stopped a report overrunning the roll. See FIDELITY U5.
    TOO_WIDE = {"revision"}

    def test_the_console_own_reports_need_no_folding_at_all(self):
        """A report the console writes for itself is written to fit."""
        console = a_site()
        wide = {}
        # `shift` and `reconciliation` were NOT on this list, which is how
        # `_figure` kept a 27-character value line against a 24-column roll
        # for as long as it did. They are the same report in the same layout
        # now -- see `printer.shift` -- and both fit, so both are guarded.
        #
        # Nor were the three variance reports, which is how the other two of
        # `_figure`'s family kept theirs: `delivery variance` was 26 against
        # a sample that measures exactly 24 and `book variance` was 27 to 28
        # against one that measures 30 and fits no roll. That is 12 folded
        # lines from one and 32 from the other on a four-tank site, sitting
        # either side of `variance analysis`, which rendered none. All three
        # are guarded now, which is the whole of FIDELITY T13's lesson: a
        # pass that fixes one report of a family has to look sideways.
        for name in ("inventory", "alarms", "status", "revision", "setup",
                     "deliveries", "sensors", "leak tests",
                     "shift", "reconciliation",
                     "delivery variance", "book variance",
                     "variance analysis"):
            for line in every_report(console)[name]:
                for one in str(line).split(chr(10)):
                    if len(one) > printer.WIDTH:
                        wide[name] = max(wide.get(name, 0), len(one))
        self.assertEqual(sorted(set(wide) - self.TOO_WIDE), [],
                         f"a report stopped fitting the paper: {wide}")
        self.assertEqual(sorted(self.TOO_WIDE - set(wide)), [],
                         "a report in TOO_WIDE now fits: take it out")

    def test_print_gives_one_tape_with_one_header(self):
        """"PRINT - a copy of the system status [active alarms (if any),
        and in-tank inventory]" -- one header, both bodies."""
        console = a_site()
        out = printer.under_one_header(printer.status(console),
                                       printer.inventory(console))
        self.assertEqual(out.count("SYSTEM STATUS REPORT"), 1)
        self.assertEqual(out.count("INVENTORY REPORT"), 1)
        # the site's own header line, and the stamp, exactly once each
        self.assertEqual(out.count(console.text("503", 1)), 1)
        self.assertEqual(len([l for l in out
                              if re.match(r"^[A-Z]{3} +\d", str(l))]), 1)
        self.assertLess(out.index("SYSTEM STATUS REPORT"),
                        out.index("INVENTORY REPORT"))

    def test_a_figure_is_the_width_the_page_measures(self):
        """576013-610 Rev AC p.28-2 draws the whole SHIFT RECONCILIATION
        report. Its word boxes put every label at column 0 and the right
        edge of all eight values at column 23.1, in a 5.40pt monospace:

            OPENING VOLUME:
                          5511 GALS

        The value and its unit are one right-aligned field. This was
        `f"{value:22.0f} {unit}"`, which is 27 -- three over the roll -- and
        the docstring said it was laid out for a 40 column one. UNKNOWNS A19.
        """
        out = []
        printer._figure(out, "OPENING VOLUME", 5511.0)
        printer._figure(out, "WATER HEIGHT", 2.10, "INCH")
        self.assertEqual(out[0], "OPENING VOLUME:")
        self.assertEqual(out[1], "5511 GALS".rjust(23))
        self.assertEqual(out[4], "2.10 INCH".rjust(23))
        for line in out:
            self.assertLessEqual(len(line), printer.WIDTH, repr(line))

    def test_the_paper_report_is_not_the_wire_report(self):
        """`bir.report` is IC03, a seventy-character table of eight columns
        that the WIRE asks for, and the roll used to be handed it. Its
        column header and its 35-character SIGNATURE line -- which occurs in
        576013-635 and on no page of the Operator's Manual -- both folded.
        """
        console = a_site()
        paper = [str(x) for x in printer.fit(printer.shift(console))]
        for wrong in ("DLVRIES", "PHYSICL", "SIGNATURE"):
            self.assertNotIn(wrong, " ".join(paper), wrong)
        # and the title is printed once, not once per layout
        self.assertEqual(sum(1 for l in paper
                             if "SHIFT RECONCILIATION" in l), 1)

    def test_folding_keeps_every_word(self):
        wide = ["DATE TIME  OPENING DLVRIES   SALES  ADJUST  CALC'D PHYSICL"]
        folded = printer.fit(wide)
        self.assertGreater(len(folded), 1)
        self.assertEqual(" ".join(w for line in folded for w in line.split()),
                         " ".join(wide[0].split()))

    def test_a_block_of_lines_is_flattened_onto_the_roll(self):
        self.assertEqual(printer.fit(["ONE\nTWO", "THREE"]),
                         ["ONE", "TWO", "THREE"])

    def test_a_word_longer_than_the_paper_is_still_printed(self):
        folded = printer.fit(["X" * 95])
        self.assertTrue(all(len(line) <= printer.WIDTH for line in folded))
        self.assertEqual("".join(line.strip() for line in folded), "X" * 95)

    def test_the_setup_report_heads_a_device_once(self):
        """The tape opens a tank's block with "T 1:PREMIUM" and then runs
        every row under it. This used to draw the panel's two-line screen
        for each row, repeating the head twenty-one times, and to print
        ENTER PRODUCT LABEL over a second copy of it. FIDELITY T1."""
        console = a_site()
        out = printer.setup(console)
        self.assertNotIn("ENTER PRODUCT LABEL", out)
        self.assertNotIn("T1: DIESEL", out)       # the panel's form
        self.assertIn("T 1:DIESEL", out)          # the paper's
        # Once. The tape heads a tank once and runs every row of its setup
        # underneath, and the rows that used to interrupt it -- SIPHON
        # MANIFOLDED TANKS, PERIODIC TEST TYPE, ANNUAL TEST FAIL -- print
        # with no device in front of them, which is how the tape draws them.
        self.assertEqual(out.count("T 1:DIESEL"), 1)
        for row in ("PRODUCT CODE    :", "       FULL VOL :",
                    "SIPHON MANIFOLDED TANKS", "          ALARM DISABLED",
                    # a percent limit prints its percent AND the gallons,
                    # to the paper's one decimal place rather than the
                    # panel's `000%` mask -- FIDELITY T6
                    "          % MAX :   90.0", "      (GALLONS) :  18000"):
            self.assertTrue(any(str(l).startswith(row) for l in out), row)
        rows = out[out.index("T 1:DIESEL") + 1:]
        self.assertTrue(rows[0].startswith("PRODUCT CODE"), rows[:3])
        for line in out:
            self.assertLessEqual(len(line), printer.SETUP_COLS,
                                 f"off the screen: {line!r}")

    def test_the_service_code_list_lists_the_service_codes(self):
        """576013-610 Rev AC p.33-2: "Press Print to print out a list of all
        predefined and previously entered User Defined (99xx) service codes.
        (you can also refer to the Maintenance Service Codes Quick Help
        guide ...)". The pamphlet is an "also", not a substitute, and this
        printed a pointer to it and nothing else while the console held all
        129 codes and answered 8A2 with them.

        The shape is 8A2's own: the label left in twenty, the code at column
        21, under two headings. FIDELITY P1.
        """
        console = a_site()
        console.user_service_codes.append(("9902", "MAINTENANCE CALL"))
        out = [str(l) for l in printer.service_codes(console)]
        self.assertIn("STANDARD LABEL      CODE", out)
        self.assertIn("USER DEFINED LABEL  CODE", out)
        self.assertIn("REPROGRAMMED TLS    0101", out)
        self.assertIn("MAINTENANCE CALL    9902", out)
        self.assertNotIn("QUICK HELP 577013-874", out)
        self.assertGreaterEqual(len(out), len(console.SERVICE_CODES))
        for line in out:
            self.assertLessEqual(len(line), printer.SETUP_COLS, repr(line))

    def test_the_alarm_history_is_grouped_headed_and_three_deep(self):
        """I206's display format is the shape: a title, the device under
        it, then the alarms GROUPED BY TYPE with the description printed
        once and its stamps under it newest first.

            TANK ALARM HISTORY
            TANK 1  REGULAR UNLEADED
            LOW PRODUCT ALARM
              DEC 22, 1995  3:31 PM
              DEC 19, 1995 10:05 AM

        576013-610 Rev AC gives the depth: "record of the last three
        occurrences of each type of alarm or warning condition". This
        printed `ALARM HISTORY REPORT` -- the display PROMPT -- over a flat
        chronological dump with no device on it and no limit. FIDELITY W13.
        """
        import time
        console = a_site()
        base = time.mktime(console.now())

        def at(hours):
            return time.strftime("%y%m%d%H%M",
                                 time.localtime(base - 3600 * hours))

        for i in range(4):          # four of one type, three may print
            console.alarm_log.append({"aa": "02", "nn": "04", "tt": "01",
                                      "at": at(i + 1)})
        console.alarm_log.append({"aa": "02", "nn": "09", "tt": "01",
                                  "at": at(20)})
        out = [str(l) for l in printer.alarm_history(console, "T")]
        self.assertIn("TANK ALARM HISTORY", out)
        self.assertTrue(any(l.startswith("TANK 1") for l in out), out)
        self.assertEqual(out.count("OVERFILL ALARM"), 1)
        self.assertEqual(sum(1 for l in out if l.startswith("  SEP")
                             or l.startswith("  AUG")), 4)
        for line in out:
            self.assertLessEqual(len(line), printer.SETUP_COLS, repr(line))

    def test_every_printout_ends_with_the_station_and_the_status(self):
        console = a_site()
        out = printer.setup(console)
        self.assertEqual(out[-2], printer.SETUP_RULE)
        self.assertIn("SYSTEM STATUS REPORT", out[-3])

class TheMassAndDensityRows(unittest.TestCase):
    """FIDELITY X8. 576013-610 Rev AC p.4-2 draws MASS and DENSITY on the
    INVENTORY REPORT between TC VOLUME and HEIGHT, marked "Only appears if
    Mass/Density feature enabled". `inv_rows` omitted both, on a console
    whose `product_mass` already reproduces the sample's own figure.
    """

    def a_mass_site(self, on=True):
        console = a_site()
        console.values["S56000"] = "1" if on else "0"
        return console

    def rows_for(self, console, tank=1):
        out, seen = [], False
        for line in printer.fit(printer.inventory(console)):
            if line.startswith(f"T {tank}:"):
                seen = True
            elif seen and line.startswith("T "):
                break
            elif seen:
                out.append(line)
        return out

    def test_the_two_rows_appear_only_with_the_feature_enabled(self):
        """Function 560, Set Mass/Density Enable/Disable -- the same gate the
        panel's own DENSITY and MASS steps already carried."""
        off = " ".join(self.rows_for(self.a_mass_site(on=False)))
        self.assertNotIn("MASS", off)
        self.assertNotIn("DENSITY", off)
        on = " ".join(self.rows_for(self.a_mass_site()))
        self.assertIn("MASS", on)
        self.assertIn("DENSITY", on)

    def test_they_sit_between_tc_volume_and_height(self):
        """The sample's order, which is the only thing about their placement
        that is stated."""
        labels = [line.split("=")[0].strip()
                  for line in self.rows_for(self.a_mass_site()) if "=" in line]
        self.assertEqual(labels, ["VOLUME", "ULLAGE", "90% ULLAGE",
                                  "TC VOLUME", "MASS", "DENSITY", "HEIGHT",
                                  "WATER VOL", "WATER", "TEMP"])

    def test_mass_is_volume_times_density_and_truncated(self):
        """"MASS = 15290 LBS" against a density of 5.9987 -- a whole-number
        quantity, so the fraction is dropped like every other one."""
        console = self.a_mass_site()
        want = int(console.tank_level[1]["volume"] * console.product_density(1))
        row = [r for r in self.rows_for(console) if r.startswith("MASS")][0]
        self.assertEqual(row, printer.inv_row("MASS", str(want), "LBS"))

    def test_the_density_row_fits_the_roll_and_the_grid_could_not(self):
        """The one row of the block that is not the measured grid, because
        the grid cannot hold it: 10 + 1 + 6 + 1 + len("LBS/GAL") is 25 and
        the roll is 24. Drawn that way it folds between the value and its
        unit. 576013-610's own artwork, `DENSITY = 5.9987 LBS/GAL`, is
        exactly 24 -- and is the only rendering of this row anywhere.
        """
        console = self.a_mass_site()
        row = [r for r in self.rows_for(console) if r.startswith("DENSITY")][0]
        self.assertEqual(len(row), printer.WIDTH)
        self.assertEqual(row, f"DENSITY = {console.product_density(1):.4f}"
                              " LBS/GAL")
        # and the grid really cannot: this is the arithmetic, not an opinion
        self.assertGreater(len(printer.inv_row("DENSITY", "5.9987",
                                               "LBS/GAL")), printer.WIDTH)

    def test_the_whole_report_still_comes_off_the_roll(self):
        """Adding rows to a report that fits is how a report stops fitting."""
        for site in presets.PRESETS:
            console = a_site(site)
            console.values["S56000"] = "1"
            for line in printer.fit(printer.inventory(console)):
                self.assertLessEqual(len(line), printer.WIDTH,
                                     f"{site}: {line!r}")


class WhenTheRollRunsOut(unittest.TestCase):
    """"Printer out of Paper" is a system alarm, not a silence."""

    def test_it_is_a_condition_the_console_reports(self):
        c = a_site()
        self.assertNotIn("010100", c.compute_alarms())
        c.out_of_paper = True
        self.assertIn("010100", c.compute_alarms())
        self.assertIn("PAPER OUT",
                      [a["screen"] for a in describe_alarms(c.compute_alarms())])

    def test_loading_paper_is_not_enough_the_warning_waits_for_the_key(self):
        """PAPER OUT is a Warning in 576013-610 Table 29-2, so it follows that
        manual's general rule on p.29-1: the message stays after the cause is
        corrected until ALARM/TEST is pressed. 576013-623 p.2-2 says the same
        from the key's side -- ALARM/TEST "clears alarms that have returned to
        normal condition", which it could not do if they cleared themselves.
        """
        c = a_site()
        c.out_of_paper = True
        self.assertIn("010100", c.compute_alarms())
        c.out_of_paper = False
        self.assertIn("010100", c.compute_alarms())
        c.acknowledge()
        self.assertNotIn("010100", c.compute_alarms())

    def test_a_reset_console_has_paper_in_it(self):
        c = a_site()
        c.out_of_paper = True
        c.reset(keep_clock=True)
        self.assertFalse(c.out_of_paper)


@unittest.skipUnless(HAVE_TK, "no display")
class ThePaperOnTheConsole(unittest.TestCase):
    """The slip hanging out of the slot, and the two switches over it."""

    # One Tk interpreter for the class: see the note in test_panel.py.
    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        try:
            cls.app = SimApp(Console(), 10001)
        except Exception as exc:               # pragma: no cover
            cls.app = None
            raise unittest.SkipTest(f"no usable Tk: {exc}")

    @classmethod
    def tearDownClass(cls):
        if cls.app is None:                    # pragma: no cover
            return
        try:
            cls.app.quit()
        except Exception:
            pass
        cls.app.destroy()
        cls.app = None

    def setUp(self):
        self.c = Console()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        self.c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
        self.app = type(self).app
        self.app.console = self.c
        self.app.reset_panel()
        self.app.paper.delete("1.0", "end")
        self.app.update()

    def test_print_hangs_paper_out_of_the_slot(self):
        self.app.cut_paper()
        self.app.k_print()
        self.app.update()
        self.assertTrue(self.app.slip_out)
        self.assertTrue(self.app.slip.winfo_ismapped())
        self.assertIn("INVENTORY REPORT", self.app.slip_text.get("1.0", "end"))

    def test_the_paper_is_the_width_of_the_cutout(self):
        self.app.k_print()
        self.app.update()
        cut1, cut2, _y = self.app._slot
        self.assertEqual(self.app.slip.winfo_width(), cut2 - cut1)

    def test_forty_columns_fit_across_it(self):
        self.app.k_print()
        self.app.update()
        cut1, cut2, _y = self.app._slot
        from tls350sim.ui import PAPER_COLS, SLIP_PAD
        across = self.app.slip_font.measure("0") * PAPER_COLS
        self.assertLessEqual(across, cut2 - cut1 - SLIP_PAD * 2)

    def test_cut_takes_it_away_and_leaves_it_on_the_roll(self):
        self.app.k_print()
        self.app.update()
        self.app.cut_paper()
        self.app.update()
        self.assertFalse(self.app.slip_out)
        self.assertFalse(self.app.slip.winfo_ismapped())
        self.assertIn("INVENTORY REPORT", self.app.paper.get("1.0", "end"))

    def test_a_second_report_joins_the_one_hanging_there(self):
        self.app.cut_paper()
        self.app.k_print()
        first = self.app._slip_lines
        self.app.k_print()
        self.assertGreater(self.app._slip_lines, first)

    def test_nothing_prints_with_no_paper_in_it(self):
        self.app.cut_paper()
        self.app.paper.delete("1.0", "end")
        self.app.no_paper.set(True)
        self.app._set_paper()
        self.app.k_print()
        self.app.update()
        self.assertFalse(self.app.slip_out)
        self.assertEqual(self.app.paper.get("1.0", "end").strip(), "")
        # And the display says so in the CONSOLE's words, through the
        # console's own channel: `PAPER OUT` is alarm 01/01 and
        # `alarmlabels.json` has its screen text off the manual. This
        # asserted `OUT OF PAPER`, which came from a flash that phrased the
        # same condition a second way and no page draws. See FIDELITY U5.
        self.app.step = 0
        self.app._entered = False
        self.app._blink = False
        self.assertIn("PAPER OUT", "".join(self.app._lines()))

    def test_the_slip_can_be_switched_off_without_stopping_the_printer(self):
        self.app.cut_paper()
        self.app.paper.delete("1.0", "end")
        self.app.live_paper.set(False)
        self.app._set_live_paper()
        self.app.k_print()
        self.app.update()
        self.assertFalse(self.app.slip_out)
        self.assertIn("INVENTORY REPORT", self.app.paper.get("1.0", "end"))

    def test_the_paper_on_the_console_is_folded_to_the_roll(self):
        self.app.cut_paper()
        self.app.paper_out(["X" * 90, "SHORT"])
        for line in self.app.slip_text.get("1.0", "end").split("\n"):
            self.assertLessEqual(len(line), printer.WIDTH)


class WhatTheConsolePrintsWithoutBeingAsked(unittest.TestCase):
    """The automatic printouts, and the four that were never produced.

    The dispatcher for these lived in the UI's refresh loop, so a headless
    console printed none of them and the queues they drain grew without
    bound. `printer.automatic` is that dispatcher where the rendering is,
    and it carries the four printouts the manuals state and nothing in the
    package produced. See FIDELITY P2.
    """

    def a_console(self):
        """Primed, so the first ask is the empty one it should be."""
        c = a_site()
        printer.automatic(c)
        return c

    def in_a_minute(self, console, minutes=1):
        """A programmed HHmm the console's clock is about to reach."""
        later = time.localtime(time.mktime(console.now()) + minutes * 60)
        return time.strftime("%H%M", later)

    def notes(self, printed):
        return [note for note, _lines in printed]

    def report(self, printed, title):
        for _note, lines in printed:
            if any(str(x).strip() == title for x in lines):
                return [str(x) for x in lines]
        return None

    def a_csld_console(self, report_only=None):
        """A tank CSLD really runs on: the key, S611's method 7, and a float,
        which only a Mag probe has -- `test_console`'s own recipe."""
        from tests.test_console import a_tank, fitted
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        c.values["S62F01"] = "011"
        if report_only:
            c.values["S61C01"] = report_only
        return c

    def csld_printed(self, c, year, month, day):
        """The CSLD reports owed across eight o'clock on that day."""
        for minute, keep in ((7 * 60 + 59, False), (8 * 60 + 1, True)):
            c.clock_offset = time.mktime(
                (year, month, day, minute // 60, minute % 60, 0, 0, 1, -1)
            ) - time.time()
            printed = printer.automatic(c)
        return [lines for note, lines in printed
                if note == "-- PRINT: CSLD test results"]

    def test_csld_results_print_at_eight_every_morning(self):
        """576013-818 Rev AB ch.11: "Test results are provided automatically
        every 24 hours at 8:00 a.m." Nothing printed them. FIDELITY K6."""
        c = self.a_csld_console()
        slips = self.csld_printed(c, 2026, 9, 14)
        self.assertEqual(len(slips), 1)
        self.assertIn("CSLD TEST RESULTS", [str(x).strip() for x in slips[0]])

    def test_report_only_prints_on_its_days_and_not_between(self):
        """576013-623 Rev AN p.8-9: "Day 15 and End of Month (both at 8:00
        a.m.)"."""
        c = self.a_csld_console(report_only="2")
        self.assertEqual(self.csld_printed(c, 2026, 9, 14), [])
        self.assertEqual(len(self.csld_printed(c, 2026, 9, 15)), 1)
        self.assertEqual(self.csld_printed(c, 2026, 9, 25), [])
        self.assertEqual(len(self.csld_printed(c, 2026, 9, 30)), 1)

    def test_end_of_month_alone_waits_for_the_last_day(self):
        """"End of Month (at 8:00 a.m.)", and February's is the 28th."""
        c = self.a_csld_console(report_only="1")
        self.assertEqual(self.csld_printed(c, 2027, 2, 15), [])
        self.assertEqual(len(self.csld_printed(c, 2027, 2, 28)), 1)

    def test_a_tank_without_csld_prints_no_csld_report(self):
        c = self.a_csld_console()
        c.software["csld"] = False
        self.assertEqual(self.csld_printed(c, 2026, 9, 14), [])

    def an_isd_console(self):
        c = a_site()
        c.set_board("E6")
        c.software["isd"] = True
        printer.automatic(c)
        return c

    def test_an_isd_site_alarm_prints_its_own_slip(self):
        """577013-800 Rev P Figure 21, "Example Warning Posting": `---- ISD
        SITE ALARM ----` over the alarm and its time. FIDELITY I11."""
        c = self.an_isd_console()
        c.isd_force("leakage", "warn")
        printed = printer.automatic(c)
        self.assertEqual(self.notes(printed), ["-- PRINT: ISD site alarm"])
        slip = printed[0][1]
        self.assertEqual(slip[:2], ["---- ISD SITE ALARM ----",
                                    "ISD VAPOR LEAKAGE WARN"])
        self.assertEqual(len(slip), 3)

    def test_an_isd_hose_alarm_names_the_hose(self):
        """Figure 22: `h 1: FP1 SUPER` over `FLOW COLLECT FAIL`."""
        c = self.an_isd_console()
        hose = c.isd_add_hose()
        c.set_setting("evr_hose_label", "SUPER", hose)
        c.isd_force("collect_flow", "warn")
        slips = [lines for note, lines in printer.automatic(c)
                 if note == "-- PRINT: ISD hose alarm"]
        self.assertEqual(slips[0][:3], ["---- ISD HOSE ALARM ----",
                                        "h 1: FP1 SUPER",
                                        "FLOW COLLECT WARN"])

    def test_a_console_nobody_has_asked_owes_nothing(self):
        """The watermarks are primed from where the console already is, so
        the first ask does not reprint the day it has behind it."""
        self.assertEqual(printer.automatic(a_site()), [])

    def test_a_programmed_shift_start_time_prints_an_inventory_report(self):
        """576013-623 Rev AN, Shift Start Times: "At each programmed time,
        the system automatically prints a complete inventory report and
        stores it in memory"."""
        c = self.a_console()
        c.values["S50201"] = self.in_a_minute(c)
        c.clock_offset += 120
        printed = printer.automatic(c)
        self.assertIn("-- PRINT: shift 1 inventory", self.notes(printed))
        self.assertIsNotNone(self.report(printed, "INVENTORY REPORT"))

    def test_a_disabled_shift_start_time_prints_nothing(self):
        """The tape's console stores its three unused shifts as `EE00`,
        which is the disabled mark and not four o'clock."""
        c = self.a_console()
        for shift in range(1, 5):
            c.values["S502%02d" % shift] = "EE00"
        c.clock_offset += 3600
        self.assertEqual([n for n in self.notes(printer.automatic(c))
                          if "inventory" in n], [])

    def test_the_shift_inventory_prints_once_for_the_time_it_is_due(self):
        """A time is crossed once. Asking again in the same minute owes
        nothing, which is what stopped the queues growing headless."""
        c = self.a_console()
        c.values["S50201"] = self.in_a_minute(c)
        c.clock_offset += 120
        self.assertTrue(printer.automatic(c))
        self.assertEqual(printer.automatic(c), [])

    def test_the_fuel_management_report_prints_the_short_one(self):
        """576013-623 Rev AN p.128: "This display lets you set a daily time
        when the system will automatically print a fuel management report.
        The report printed is a 'Short Report'" -- so the seven average
        daily sales rows of the long one are not on it."""
        c = self.a_console()
        c.values["S68200"] = self.in_a_minute(c)
        c.clock_offset += 120
        printed = printer.automatic(c)
        self.assertIn("-- PRINT: fuel management report",
                      self.notes(printed))
        body = self.report(printed, "FUEL MANAGEMENT REPORT")
        self.assertTrue(any("DAYS FUEL REMAINING" in x for x in body))
        self.assertFalse([x for x in body if "AVG SALES" in x],
                         "the short report carries no average sales rows")

    def test_shift_bir_printouts_print_the_closed_shift(self):
        """576013-623 Rev AN: "Shift BIR Printouts enabled causes a BIR
        report to print at the end of every shift". The flag was stored,
        printed back on the setup report, and read by nothing."""
        c = self.a_console()
        c.values["S51100"] = "1"
        c.bir.close("shift")
        printed = printer.automatic(c)
        self.assertIn("-- PRINT: shift BIR report", self.notes(printed))
        self.assertIsNotNone(self.report(printed, "SHIFT RECONCILIATION"))

    def test_daily_bir_printouts_print_the_closed_day(self):
        """"Daily BIR Printouts enabled causes a BIR report to print at the
        end of the day's last shift"."""
        c = self.a_console()
        c.values["S51200"] = "1"
        c.bir.close("daily")
        printed = printer.automatic(c)
        self.assertIn("-- PRINT: daily BIR report", self.notes(printed))
        self.assertIsNotNone(self.report(printed, "DAILY RECONCILIATION"))

    def test_the_bir_printout_flags_are_read_at_all(self):
        """Both off, and a close prints nothing: the flags decide it."""
        c = self.a_console()
        c.values["S51100"] = c.values["S51200"] = "0"
        c.bir.close("shift")
        c.bir.close("daily")
        self.assertEqual([n for n in self.notes(printer.automatic(c))
                          if "BIR" in n], [])

    def test_a_closed_shift_prints_once(self):
        c = self.a_console()
        c.values["S51100"] = "1"
        c.bir.close("shift")
        self.assertTrue(printer.automatic(c))
        self.assertEqual(printer.automatic(c), [])

    def a_finished_load(self, console, tank=1):
        """One tanker load off the tank, recorded the way `Loads` records
        it: the newest first, with the volume it drew."""
        now = time.mktime(console.now())
        run = delivery.Load(tank, 1, delivery.snapshot(console, tank, now))
        console.tank_level[tank]["volume"] -= 9422.0
        run.end = delivery.snapshot(console, tank, now + 600)
        console.loads.records.setdefault(tank, []).insert(0, run)
        return run

    def test_a_dispensed_tanker_load_prints_its_report(self):
        """576013-623 Rev AN, Tanker Load Report: "The Tanker Load Report is
        an optional feature. In the ENABLE position, a report is printed
        after every tanker load is dispensed"."""
        c = self.a_console()
        c.values["S51300"] = "1"
        self.a_finished_load(c)
        printed = printer.automatic(c)
        self.assertIn("-- PRINT: tanker load report, tank 1",
                      self.notes(printed))
        body = self.report(printed, "TANKER LOAD REPORT")
        self.assertTrue(any(x.startswith("NUMBER:") for x in body))

    def test_a_tanker_load_prints_nothing_with_the_option_disabled(self):
        """"Press CHANGE to select DISABLE, then ENTER to not automatically
        print this report"."""
        c = self.a_console()
        c.values["S51300"] = "0"
        self.a_finished_load(c)
        self.assertEqual([n for n in self.notes(printer.automatic(c))
                          if "tanker" in n], [])

    def test_a_delivery_still_prints_itself(self):
        """The five that already worked come through the same door: "when
        the system recognizes that a delivery occurred, an adjusted delivery
        report is automatically printed"."""
        c = self.a_console()
        c.printed_deliveries.append((1, self.a_delivery(c)))
        printed = printer.automatic(c)
        self.assertTrue([n for n in self.notes(printed) if "delivery" in n])
        self.assertFalse(c.printed_deliveries, "the queue is drained")
        self.assertEqual(printer.automatic(c), [])

    def a_delivery(self, console, tank=1):
        now = time.mktime(console.now())
        run = delivery.Delivery(tank, delivery.snapshot(console, tank, now))
        console.tank_level[tank]["volume"] += 3000.0
        run.end = delivery.snapshot(console, tank, now + 600)
        return run


class TheTicketedDeliveryReport(unittest.TestCase):
    """576013-610 Rev AC p.5-4, which PRINT on Delivery Maintenance gives.

    It gave `DELIVERY REPORT`, gross and TC on one row and ticket and
    variance on the next -- no temperatures, no BOL, no basis line -- and
    the right title lived in `printer.ticketed`, over I221's serial columns,
    called by nothing. FIDELITY O7.
    """

    T0 = time.mktime((2026, 9, 14, 13, 0, 0, 0, 0, -1))

    def a_console(self):
        c = a_site()
        c.deliveries.records = {}
        c.values["S51D00"] = "0"
        return c

    def a_delivery(self, c, at, ticket=2500.0, bol="EXX23223", tank=1,
                   gross=2599.0, tc=2590.0):
        run = delivery.Delivery(tank, {"at": at - 900, "volume": 3000.0,
                                       "tc": 2990.0, "water": 0.0,
                                       "temp": 72.6, "height": 40.0})
        run.end = {"at": at, "volume": 3000.0 + gross, "tc": 2990.0 + tc,
                   "water": 0.0, "temp": 73.2, "height": 60.0}
        run.ticket, run.bol = ticket, bol
        c.deliveries.records.setdefault(tank, []).insert(0, run)
        return run

    def test_the_page_s_rows_on_the_roll(self):
        c = self.a_console()
        self.a_delivery(c, self.T0)
        got = printer.deliveries(c, [1])
        label = c.text("602", 1) or "TANK 1"
        # the mixed temperature: 5599 gallons at 73.2 less 3000 at 72.6,
        # over the 2599 that came in
        self.assertEqual(got, [
            f"T 1:{label}".rstrip(), "TICKETED DELIVERY REPORT",
            c.clock_stamp(), "VOLUMES ARE STANDARD", "",
            printer.clock_words(self.T0),
            "TICKET VOL    :2500 GALS",
            "GAUGED VOL    :2599 GALS",
            "DLVY VAR      : -99 GALS",
            "EST DLVY TEMP :   73.9 F",
            "PRE DLVY TEMP :   72.6 F",
            "POST DLVY TEMP:   73.2 F",
            "BOL           : EXX23223",
            ""])

    def test_the_tape_s_own_grid_where_the_values_fit_it(self):
        """`WATER WARNING   :    0.8` on the site tape: label and colon in
        sixteen, the value right-aligned to the last column."""
        self.assertEqual(printer.colon_row("PRE DLVY TEMP", "72.6 F"),
                         "PRE DLVY TEMP   : 72.6 F")
        self.assertEqual(len(printer.colon_row("BOL", "", 16)), 17)

    def test_an_inserted_delivery_has_no_gauge_and_no_temperatures(self):
        """"When you insert ticketed deliveries, gauged volume and
        temperature information appear as 'UNAVAIL'", p.5-3 -- and the
        variance is taken from the gauge."""
        c = self.a_console()
        c.deliveries.insert(1, self.T0, 2500.0, "EXX1")
        rows = dict(line.split(":", 1)
                    for line in printer.deliveries(c, [1])[6:13])
        self.assertEqual(rows["TICKET VOL    "], "2500 GALS")
        for name in ("GAUGED VOL", "DLVY VAR", "EST DLVY TEMP",
                     "PRE DLVY TEMP", "POST DLVY TEMP"):
            self.assertEqual(rows[name.ljust(14)].strip(), "UNAVAIL", name)

    def test_a_tc_ticketed_site_reports_on_the_tc_basis(self):
        """576013-623 Rev AN p.5-6, TC TICKETED DELIVERY: "you can choose
        whether the values you enter are standard (gross) volumes or
        temperature-compensated (TC) volumes"."""
        c = self.a_console()
        c.values["S51D00"] = "1"
        self.a_delivery(c, self.T0)
        got = printer.deliveries(c, [1])
        self.assertIn("VOLUMES ARE TC", got)
        self.assertIn("GAUGED VOL    :2590 GALS", got)
        self.assertIn("DLVY VAR      : -90 GALS", got)

    def test_a_day_prints_that_day_s_deliveries_alone(self):
        """"Press PRINT to print a Delivery Report for all deliveries for
        the day and tank shown", p.5-3."""
        c = self.a_console()
        self.a_delivery(c, self.T0 - 86400, bol="YESTERDAY")
        self.a_delivery(c, self.T0, bol="TODAY")
        every = chr(10).join(printer.deliveries(c, [1]))
        one = chr(10).join(printer.deliveries(c, [1], day=self.T0 + 3600))
        self.assertIn("YESTERDAY", every)
        self.assertIn("TODAY", one)
        self.assertNotIn("YESTERDAY", one)

    def test_five_digit_volumes_come_off_the_roll_unfolded(self):
        c = self.a_console()
        self.a_delivery(c, self.T0, ticket=12000.0, gross=12050.0,
                        bol="B" * 20)
        for line in printer.deliveries(c, [1]):
            self.assertLessEqual(len(line), printer.WIDTH, line)

    def test_a_tank_with_no_deliveries_prints_no_invented_line(self):
        c = self.a_console()
        self.assertEqual(printer.deliveries(c, [2])[1:4],
                         ["TICKETED DELIVERY REPORT", c.clock_stamp(),
                          "VOLUMES ARE STANDARD"])
        self.assertEqual(len(printer.deliveries(c, [2])), 5)


class TheAlarmReportCarriesWhenItHappened(unittest.TestCase):
    """576013-610 Rev AC p.29-1 counts three things and the slip had two.

        ALARM REPORTS
        If your system has a printer, it will print an alarm or warning
        report when it detects a warning or alarm condition. This report
        shows the type and location of the warning or alarm **and the date
        and time it occurred.**

    576013-939 Quick Help p.12 says it again independently. The only stamp
    on the slip was the header's, which is when the SLIP was printed --
    and the occurrence time is the half a technician writes on the work
    order. A7.
    """

    def test_the_stamp_is_when_the_alarm_came_not_when_the_slip_printed(self):
        c = a_site("Two-tank retail site")
        c.tick()
        c.out_of_paper = True
        c.tick()
        c.compute_alarms()
        happened = [r for r in c.alarm_log if r["state"] == "02"]
        self.assertTrue(happened, "nothing was logged as having occurred")
        # an hour later, with the cause still there
        c.clock_offset += 3600
        c.tick()
        rows = printer.alarms(c)
        body = rows[rows.index("ALARM/WARNING REPORT"):]
        self.assertIn("PAPER OUT", body)
        stamp = body[body.index("PAPER OUT") + 1]
        self.assertTrue(stamp.startswith("  "), body)
        self.assertNotIn(stamp.strip(), rows[:rows.index(
            "ALARM/WARNING REPORT")],
            "the alarm's stamp is the slip's own printing time")
        self.assertEqual(body[body.index("PAPER OUT") + 2], "  ACTIVE")

    def test_a_quiet_console_still_says_so(self):
        """The empty case has no alarm to stamp and must not grow one."""
        c = a_site("Two-tank retail site")
        c.tick()
        self.assertEqual(printer.alarms(c)[-1], "ALL FUNCTIONS NORMAL")


class TheSetupReportOnAConsoleNobodyHasProgrammed(unittest.TestCase):
    """The bare-console case `audits/README.md` names as the one nobody has
    been able to check. The Setup Data Report is the first thing a
    technician prints before touching a site's programming, and on an
    unprogrammed console it was a list of titles. CLOSED U35."""

    def a_full_cage(self):
        from tls350sim.console import SOFTWARE_MODULES
        c = Console()
        c.board = "E6"
        for key in ("probe", "liquid", "vapor", "gw", "2wire", "3wire",
                    "smart", "plld", "wplld", "vlld", "io", "relay", "pump",
                    "pumpmon", "vmc", "mt", "rs232"):
            c.modules[key] = 1
        c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
        return c

    def section(self, console, name):
        fn = [f for f in console.available_functions()
              if f["function"] == name]
        self.assertTrue(fn, f"{name} is not offered")
        return printer.setup_section(console, fn[0])

    def test_system_setup_prints_the_defaults_the_console_is_showing(self):
        """576013-623 Rev AN p.5-1: the report is "a record of all setup
        values entered into this system". This printed the title, the rule,
        and stopped -- while the glass one keypress away read `SYSTEM
        LANGUAGE / ENGLISH`, `SYSTEM UNITS / U.S.` and `SYSTEM DATE/TIME
        FORMAT`. `printer.setup`'s own docstring is the rule: "a setting
        nobody has programmed is still on it, reading the default the
        console reads"."""
        c = self.a_full_cage()
        rows = [r for r in self.section(c, "SYSTEM SETUP")[2:] if r.strip()]
        self.assertGreater(len(rows), 60, rows)
        for want in ("SYSTEM UNITS", " U.S.", "SYSTEM LANGUAGE", " ENGLISH",
                     "PRECISION TEST DURATION", "HOURS: 012"):
            self.assertIn(want, rows)

    def test_a_section_with_no_devices_programmed_is_still_empty(self):
        """The other twenty-four sections are per-device and a site with no
        devices programmed has nothing to print under them. Only a run with
        no device in it at all is ungated."""
        c = self.a_full_cage()
        for name in ("IN-TANK SETUP", "LIQUID SENSOR SETUP"):
            rows = [r for r in self.section(c, name)[2:] if r.strip()]
            self.assertEqual(rows, [], name)

    def test_a_programmed_console_prints_exactly_what_it_did_before(self):
        """The gate only ever fired on an unprogrammed run, so no site that
        has been programmed may move by a line. Measured against the three
        shipped presets.

        Two of the three figures moved by exactly six when those presets
        were given the BIR key -- the retail site 78 -> 84 and the
        compliance site 70 -> 76 -- and in both cases the six lines are
        the three setup steps that key unlocks, with their values: SHIFT
        BIR PRINTOUTS, DAILY BIR PRINTOUTS and QPLD MONTHLY PRINTOUT. A
        console that has BIR in it has those steps to print, so the report
        is right and the numbers moved for a reason rather than by
        accident. The truck stop already had the key and did not move.
        """
        for name, lines in (("Two-tank retail site", 84),
                            ("Truck stop, four tanks and BIR", 105),
                            ("Compliance site, CSLD and sensors", 76)):
            c = self.a_full_cage()
            presets.load(c, name)
            rows = [r for r in self.section(c, "SYSTEM SETUP")[2:]
                    if r.strip()]
            self.assertEqual(len(rows), lines, name)

    def test_the_vmc_screens_keep_their_own_titles(self):
        """`ADD VMC SERIAL NUMBER` over `x 1:`. The report identified the
        product-label step by whether the VALUE looked like a device head,
        and `x 1:` looks exactly like one, so all three VMC screens printed
        as a bare `x 1:` with nothing to say which was which. The test is
        `screens.is_label_step`, which asks the step.

        REMOVE is not among them any more. Its screen is not a serial
        number: 576013-623 Rev AN p.27-2 draws `x 1: 111111` over
        `REMOVE VMC: NO`, which is a question, and a report has no way to
        answer one -- the same rule the `PRESS <ENTER>` screens print
        under. See CLOSED U40."""
        c = self.a_full_cage()
        rows = [r for r in self.section(c, "VMC SETUP")[2:] if r.strip()]
        self.assertEqual(rows, ["ADD VMC SERIAL NUMBER", "x 1:",
                                "EDIT VMC SERIAL NUMBER", "x 1:"])


class TheTwoRelayReports(unittest.TestCase):
    """FIDELITY T12. 576013-635 Rev AA function 322 draws PUMP RELAY MONITOR
    STATUS with four columns -- the pump's own state, the relay LINE it is
    watching, and a STATUS of NORMAL or the standing alarm -- and
    `wiresensors` has rendered all four all along for `I322`. The paper
    built a row of its own instead: a label and ONE state, with the reason
    the report exists dropped.

    No page on this shelf draws that paper. 576013-610 Rev AC p.2091 indexes
    the function and its `PRINT - Status for all relays` and gives no
    sample, so the report is the wire's, folded onto the roll -- the rule
    D23 and D24 both settled for the same shape.
    """

    def a_monitor(self):
        c = Console()
        for key in ("probe", "relay", "io", "pump", "pumpmon"):
            c.modules[key] = 1
        c.values["S7C501"] = "01PUMP RELAY UNLEADED "
        c.values["S80701"] = "01HORN                "
        c.tick()
        return c

    def test_the_paper_is_the_wire_s_own_rows(self):
        from tls350sim import wiresensors
        c = self.a_monitor()
        rows = printer.relays(c, "pumpmon")
        wire = wiresensors.pumpmon_status_rows(c)
        self.assertEqual(rows[-len(wire) + 1:], wire[1:])
        self.assertIn("PUMP RELAY MONITOR STATUS REPORT", rows)

    def test_the_row_carries_the_status_the_report_exists_for(self):
        c = self.a_monitor()
        row = [r for r in printer.relays(c, "pumpmon")
               if r.strip().startswith("1 ")][0]
        self.assertIn("PUMP RELAY UNLEADED", row)
        self.assertTrue(row.rstrip().endswith("NORMAL"), row)

    def test_and_the_two_line_column_head_is_there(self):
        """The stacked head the paper had no line of at all."""
        rows = printer.relays(self.a_monitor(), "pumpmon")
        self.assertTrue(any("PUMP RELAY" in r and "PUMP" in r for r in rows))
        self.assertTrue(any(r.startswith("DEVICE") and r.endswith("STATUS")
                            for r in rows))

    def test_the_setup_report_stacks_its_state(self):
        """`R 1:HORN` over `ON`, the way `printer.sensors` draws the same
        shape: there is no column 21 the 24 character roll does not have.
        It was one 26 character line that folded to the same two by
        accident, and truncated to 18 where the roll has room for 20."""
        c = self.a_monitor()
        c.values["S80701"] = "01HORN AND BEACON 12345"[:23].ljust(23)
        rows = printer.relays(c, "relay")
        head = [r for r in rows if r.startswith("R 1:")][0]
        self.assertEqual(head, "R 1:" + c.text("807", 1))
        self.assertEqual(rows[rows.index(head) + 1], "OFF")

    def test_neither_report_has_a_row_the_roll_cannot_fold(self):
        """`fit` breaks at the last space that fits; a row with no space in
        its first 24 columns is chopped mid-word instead."""
        c = self.a_monitor()
        for kind in ("relay", "pumpmon"):
            for row in printer.relays(c, kind):
                if len(row) > printer.WIDTH:
                    self.assertIn(" ", row[:printer.WIDTH + 1],
                                  f"{kind}: {row!r}")


class TheTwoVarianceReportsOnTheirOwnGrid(unittest.TestCase):
    """FIDELITY T13. 576013-610 Rev AC typesets this family at two widths.

    p.28-10's DELIVERY VARIANCE, measured off its word boxes, is the roll's
    own 24 -- the label run to a colon in column 11 and the value and its
    unit right-aligned as one field:

        TICKET VOL :     800 GAL
        % VAR SALES:      11.23%

    p.28-14's BOOK VARIANCE measures 30 and fits no roll anybody has, so
    that block gives up its value field's padding the way the TICKETED
    DELIVERY REPORT's does and keeps the page's labels and its one colon
    column. A manual sample is typeset, not photographed.
    """

    def a_site_with_a_variance(self):
        c = a_site()
        c.tick()
        return c

    def a_sample_row(self, **values):
        """p.28-10's own figures, through the door the report reads."""
        c = a_site()
        c.bir.current(1)                    # opens the period the row is in
        c.bir.period[(1, c.recon_kind)].update(values)
        return c

    def test_the_delivery_rows_are_the_sample_s_own_columns(self):
        c = self.a_sample_row(opening=9704.0, sales=285.0, ticketed=800.0,
                              deliveries=899.0, adjust=0.0)
        c.tank_level[1]["volume"] = 8904.0
        rows = printer.delivery_variance(c, [1])
        self.assertIn("TICKET VOL :     800 GAL", rows)
        self.assertIn("GAUGED VOL :     899 GAL", rows)
        self.assertIn("DLVY VAR   :      99 GAL", rows)

    def test_every_row_of_both_reports_is_the_roll_s_width(self):
        """Not "folds tidily" -- fits. These were 26, and 27 to 28."""
        for site in presets.PRESETS:
            c = a_site(site)
            for name, report in (("delivery", printer.delivery_variance(c)),
                                 ("book", printer.book_variance(c)),
                                 ("analysis", printer.variance_analysis(c))):
                for line in report:
                    self.assertLessEqual(len(str(line)), printer.WIDTH,
                                         f"{site} / {name}: {line!r}")

    def test_the_book_block_keeps_one_colon_column(self):
        """What `colon_block` is for: the labels give up their padding
        TOGETHER, so the colons stay in one column when the values shrink
        the field."""
        rows = [r for r in printer.book_variance(self.a_site_with_a_variance())
                if ":" in r and r.split(":")[0].strip() in (
                    "OPN GAUG VOL", "METER SALES", "TICKET DLVY",
                    "MANUAL ADJ", "BOOK INV", "GAUGED INV", "WATER HT")]
        self.assertTrue(rows)
        self.assertEqual(len({r.index(":") for r in rows}), 1, rows)

    def test_neither_report_prints_minus_zero(self):
        """A variance that rounds to nothing from below printed `-0 GAL`.
        `variance_analysis` had guarded against it and its two neighbours
        had not -- the same sideways look T13 is about."""
        c = self.a_sample_row(opening=1000.0, sales=100.0, ticketed=0.0,
                              deliveries=0.0, adjust=0.0)
        c.tank_level[1]["volume"] = 900.0 - 1e-9
        for report in (printer.delivery_variance(c, [1]),
                       printer.book_variance(c, [1])):
            for line in report:
                self.assertNotIn("-0 GAL", str(line))
                self.assertNotIn("-0.00%", str(line))


class TheFiveFactsThisConsoleRenderedTwice(unittest.TestCase):
    """FIDELITY O27, found by rendering all 31 printer reports and all 569
    wire display answers on ONE console at ONE instant and diffing the pairs.

    **A console member that reaches exactly one surface is the signature of a
    second generator**, and it named every one of these.
    """

    def a_tank(self, **values):
        c = Console()
        for card in ("probe", "rs232", "liquid"):
            c.modules[card] = 4
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        c.values.update(values)
        c.tank_level[1] = {"volume": 1000.8, "water": 0.50}
        c.tick()
        return c

    def ask(self, c, code):
        from tls350sim.wire import Handler, SOH
        return Handler(c, verbose=False).handle(SOH + code).decode("latin-1")

    # ---- density and mass ------------------------------------------------

    def test_the_programmed_density_reaches_the_paper_and_the_wire(self):
        """`console.py`'s `live_reading` read `61E` and `product_density`
        did not, so the glass said 7.2500 LBS/GAL and the roll said 6.8045
        at the same instant. Two closed entries already assert this fix --
        Y10, and O13a's residue in UNKNOWNS -- and both were true of
        `live_reading` alone."""
        c = self.a_tank(S61E01="01" + struct.pack(">f", 7.25).hex().upper())
        self.assertAlmostEqual(c.product_density(1), 7.25, places=4)
        self.assertAlmostEqual(c.product_mass(1), 1000.8 * 7.25, places=2)
        self.assertIn("7.2500", c.live_reading("density", 1))

    def test_a_tank_with_no_programmed_density_keeps_its_own_reading(self):
        """"A value of 0 indicates that the density for the product in this
        tank has not been entered"."""
        c = self.a_tank()
        low, high = Console.DENSITY_BAND
        self.assertTrue(low <= c.product_density(1) <= high)

    # ---- water -----------------------------------------------------------

    def test_the_printed_inventory_applies_the_water_threshold(self):
        """It took the float's raw depth while the WATER VOL row two lines
        below it went through `water_volume`, which applies the threshold --
        so one report answered the same question both ways: `WATER VOL = 0
        GALS` over `WATER = 0.50 INCHES`."""
        c = self.a_tank(S64801="01" + struct.pack(">f", 0.8).hex().upper())
        rows = [str(r) for r in printer.inv_rows(c, 1, c.full_volume(1))]
        water = [r for r in rows if r.split("=")[0].strip() == "WATER"][0]
        volume = [r for r in rows
                  if r.split("=")[0].strip() == "WATER VOL"][0]
        self.assertIn("0.00", water)
        self.assertIn("0 GALS", volume)

    def test_and_so_does_the_wire_s_inventory(self):
        c = self.a_tank(S64801="01" + struct.pack(">f", 0.8).hex().upper())
        row = [r for r in self.ask(c, b"I20101").split("\r\n")
               if r.strip().startswith("1 ")][0]
        self.assertNotIn("0.50", row)

    def test_a_float_above_the_threshold_is_still_water(self):
        c = self.a_tank(S64801="01" + struct.pack(">f", 0.2).hex().upper())
        rows = [str(r) for r in printer.inv_rows(c, 1, c.full_volume(1))]
        self.assertIn("0.50", [r for r in rows
                               if r.split("=")[0].strip() == "WATER"][0])

    # ---- whole numbers ---------------------------------------------------

    def test_237_truncates_like_everything_else(self):
        """`%.0f` ROUNDS. On 1000.8 gallons that is 1001 against I201's
        1000, and the TOTAL compounded it. The manual's own arithmetic is
        truncation: p.4-2 prints 2549 x 5.9987 = 15290.68 as `MASS = 15290`.
        CLOSED Y11 states the rule; 237, 238, 214 and 2E2 never got it."""
        c = self.a_tank()
        c.tank_level[2] = {"volume": 2000.8, "water": 0.0}
        c.values["S60202"] = "02PREMIUM UNLEADED   "
        c.values["S60A02"] = "02" + struct.pack(">f", 10000.0).hex().upper()
        c.tick()
        rows = self.ask(c, b"I23700").split("\r\n")
        body = [r for r in rows if r.strip()[:1].isdigit()]
        # the VOLUME column, which is the one holding 1000.8 and 2000.8
        self.assertEqual([r.split()[-2] for r in body], ["1000", "2000"])
        # and every subtotal is the truncated sum of its own rows rather
        # than the rounded sum of the raw ones: 1000.8 + 2000.8 printed as
        # 3002 where the rows under it summed to 3000
        totals = [int(r.split()[-2]) for r in rows if "TOTAL:" in r]
        self.assertEqual(sum(totals), 3000)

    # ---- the sensor's status ---------------------------------------------

    def test_a_latched_sensor_alarm_is_on_the_paper_too(self):
        """576013-610 p.15-1: SENSOR NORMAL shows "if the sensor is
        functioning properly **and no alarm conditions exist**". This read
        the physical state alone, so a dried-out sensor with a latched FUEL
        ALARM printed SENSOR NORMAL on paper and answered FUEL ALARM on the
        wire -- one console with two stories about one sensor."""
        c = self.a_tank()
        c.values["S70301"] = "0104"          # a discriminating pan sensor
        c.values["S70201"] = "01SUMP 1              "
        c.sensor_state[("liquid", "1")] = "fuel"
        c.compute_alarms()
        self.assertEqual(c.sensor_reading("liquid", 1), "FUEL ALARM")
        c.sensor_state[("liquid", "1")] = "normal"
        self.assertEqual(c.sensor_reading("liquid", 1), "FUEL ALARM")
        self.assertIn("FUEL ALARM", "".join(printer.sensors(c)))

    def test_a_sensor_that_never_alarmed_reads_normal(self):
        c = self.a_tank()
        c.values["S70301"] = "0104"
        c.sensor_state[("liquid", "1")] = "normal"
        c.compute_alarms()
        self.assertEqual(c.sensor_reading("liquid", 1), "SENSOR NORMAL")


if __name__ == "__main__":
    unittest.main()
