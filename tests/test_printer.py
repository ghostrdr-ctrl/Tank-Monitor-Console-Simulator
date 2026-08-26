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
"""The printer behind the left door: what comes out, and how wide it is.

The roll is forty characters and everything has to arrive on it. Some reports
are shared with the serial port, which is wider than the roll, so those come
off it folded rather than running off the edge of the paper, and nothing
comes off it at all when the roll has run out, which the console says out loud.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False

from tls350sim import presets, printer                      # noqa: E402
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
        "ticketed": printer.ticketed(console),
        "csld": printer.csld(console),
        "shift": printer.shift(console),
        "meters": printer.meters(console),
        "sensors": printer.sensors(console),
        "fuel": printer.fuel(console),
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

    def test_the_console_own_reports_need_no_folding_at_all(self):
        """A report the console writes for itself is written to fit."""
        console = a_site()
        for name in ("inventory", "alarms", "status", "revision", "setup",
                     "deliveries", "sensors", "leak tests"):
            report = every_report(console)[name]
            for line in report:
                for one in str(line).split("\n"):
                    self.assertLessEqual(len(one), printer.WIDTH,
                                         f"{name}: {one!r}")

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

    def test_the_setup_report_is_the_console_screen_on_paper(self):
        """Two lines a step, the display's width, not a table of values."""
        console = a_site()
        out = printer.setup(console)
        self.assertIn("ENTER PRODUCT LABEL", out)
        self.assertIn(f"T{1}: DIESEL", out)
        for line in out:
            self.assertLessEqual(len(line), printer.SETUP_COLS,
                                 f"off the screen: {line!r}")

    def test_every_printout_ends_with_the_station_and_the_status(self):
        console = a_site()
        out = printer.setup(console)
        self.assertEqual(out[-2], printer.SETUP_RULE)
        self.assertIn("SYSTEM STATUS REPORT", out[-3])

    def test_a_value_with_no_room_left_goes_under_its_setting(self):
        row = printer._setup_row("A Setting With Quite A Long Name",
                                 "AND A VERY LONG VALUE INDEED")
        self.assertEqual(len(row), 2)
        self.assertTrue(all(len(line) <= printer.WIDTH for line in row))


class WhenTheRollRunsOut(unittest.TestCase):
    """"Printer out of Paper" is a system alarm, not a silence."""

    def test_it_is_a_condition_the_console_reports(self):
        c = a_site()
        self.assertNotIn("010100", c.compute_alarms())
        c.out_of_paper = True
        self.assertIn("010100", c.compute_alarms())
        self.assertIn("PRINTER OUT OF PAPER",
                      [a["screen"] for a in describe_alarms(c.compute_alarms())])

    def test_loading_paper_clears_it_by_itself(self):
        c = a_site()
        c.out_of_paper = True
        self.assertIn("010100", c.compute_alarms())
        c.out_of_paper = False
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
        self.assertIn("OUT OF PAPER", "".join(self.app._lines()))

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


if __name__ == "__main__":
    unittest.main()
