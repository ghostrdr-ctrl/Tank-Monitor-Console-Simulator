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
"""Last-Shift Inventory, on System Setup's shift start times. FIDELITY O22."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, printer                     # noqa: E402
from tls350sim.console import Console, NORMAL_MENU          # noqa: E402


def at(hh, mm=0, day=14):
    return time.mktime((2026, 9, day, hh, mm, 0, 0, 0, -1))


def a_site():
    """The truck stop, running three shifts: 06:00, 14:00 and 22:00."""
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    c.values["S50201"] = "0600"
    c.values["S50202"] = "1400"
    c.values["S50203"] = "2200"
    c.values["S50204"] = "EE00"
    return c


def clock(c, when):
    c.clock_offset = when - time.time()


class TheFunctionIsSystemSetups(unittest.TestCase):
    """576013-623 Rev AN p.5-4: "At least one Shift Start Time must be
    entered to activate the 'Last Shift Inventory' feature." It was gated on
    the BIR key, which chapter 8 never mentions."""

    FN = [f for f in NORMAL_MENU if f["function"] == "LAST-SHIFT INVENTORY"][0]

    def test_it_wants_a_shift_start_time(self):
        c = Console()
        c.modules["probe"] = 1
        self.assertFalse(c.visible(self.FN, 1))
        c.values["S50201"] = "EE00"                  # DISABLED
        self.assertFalse(c.visible(self.FN, 1))
        c.values["S50201"] = "0400"                  # the tape's shift 1
        self.assertTrue(c.visible(self.FN, 1))

    def test_and_not_the_bir_key(self):
        c = Console()
        c.modules["probe"] = 1
        c.values["S50201"] = "0400"
        self.assertFalse(c.licensed("bir"))
        self.assertTrue(c.visible(self.FN, 1))

    def test_the_disabled_shifts_are_not_programmed(self):
        self.assertEqual(a_site().shifts.programmed(), [1, 2, 3])


class TheShiftsBeginOnTheClock(unittest.TestCase):

    def test_a_start_time_begins_its_shift_and_ends_the_one_before(self):
        c = a_site()
        c.tank_level[1]["volume"] = 9000.0
        c.shifts.begin(1, at(6))
        c.tank_level[1]["volume"] = 7000.0
        c.shifts.begin(2, at(14))
        fig = c.shifts.figures(1, 1)
        self.assertEqual((fig["opening"], fig["physical"]), (9000.0, 7000.0))
        self.assertEqual(c.shifts.shown("opening", 1, 1), "9000")
        self.assertEqual(c.shifts.shown("physical", 1, 1), "7000")
        self.assertEqual(c.shifts.open["shift"], 2)

    def test_the_console_clock_starts_them(self):
        c = a_site()
        clock(c, at(5, 59))
        c.shifts.tick()                          # the first look
        clock(c, at(6, 1))
        c.shifts.tick()
        self.assertEqual(c.shifts.open["shift"], 1)
        self.assertEqual(c.shifts.open["start"]["at"], at(6))
        clock(c, at(14, 30))
        c.shifts.tick()
        self.assertEqual(c.shifts.open["shift"], 2)
        self.assertEqual(c.shifts.end_of(1)["at"], at(14))

    def test_gross_change_is_the_page_s_sum(self):
        """p.8-2: "the beginning shift inventory, minus the end shift
        inventory, plus any ticketed deliveries made during that shift".
        This was END less BEGINNING, off BIR's row."""
        c = a_site()
        c.tank_level[1]["volume"] = 9000.0
        c.shifts.begin(1, at(6))
        # "enter this adjustment during the shift in which the
        # delivery(ies) occurred"
        self.assertTrue(c.shifts.set_adjustment(1, 1, 2500.0))
        c.tank_level[1]["volume"] = 10000.0
        c.shifts.begin(2, at(14))
        fig = c.shifts.figures(1, 1)
        self.assertEqual(fig["gross"], 9000.0 - 10000.0 + 2500.0)
        self.assertEqual(c.shifts.shown("deliveries", 1, 1), "2500")

    def test_an_adjustment_is_the_total_off_the_tickets(self):
        c = a_site()
        c.shifts.begin(1, at(6))
        c.shifts.set_adjustment(1, 1, 1000.0)
        c.shifts.set_adjustment(1, 1, 2500.0)
        self.assertEqual(c.shifts.adjustment(1, 1), 2500.0)

    def test_a_manual_close_starts_the_next_shift_once_an_hour(self):
        """"This command can only be invoked once an hour. A shift
        inventory report is printed and the next shift automatically
        begins when you manually close a shift", p.8-2."""
        c = a_site()
        clock(c, at(6))
        c.shifts.begin(1, at(6))
        clock(c, at(7))
        self.assertTrue(c.shifts.close_now())
        self.assertEqual(c.shifts.open["shift"], 2)
        self.assertEqual(c.shifts.end_of(1)["at"], at(7))
        clock(c, at(7, 30))
        self.assertFalse(c.shifts.close_now())
        clock(c, at(8, 1))
        self.assertTrue(c.shifts.close_now())
        self.assertEqual(c.shifts.open["shift"], 3)


class TheLastShiftInventoryReport(unittest.TestCase):
    """576013-610 Rev AC p.8-1. PRINT gave SHIFT RECONCILIATION, which is
    Reconciliation Mode's report: the operating-mode audit's OP7."""

    def a_day(self):
        c = a_site()
        clock(c, at(15))
        c.tank_level[1]["volume"] = 9000.0
        c.shifts.begin(1, at(6))
        c.tank_level[1]["volume"] = 8000.0
        c.shifts.begin(2, at(14))
        return c

    def test_a_block_per_shift_that_has_started(self):
        lines = printer.last_shift(self.a_day())
        self.assertIn("SHIFT STARTING INV #1", lines)
        self.assertIn("SHIFT STARTING INV #2", lines)
        self.assertNotIn("SHIFT STARTING INV #3", lines)
        self.assertNotIn("SHIFT RECONCILIATION", lines)

    def test_the_page_s_rows_in_the_page_s_order(self):
        lines = printer.last_shift(self.a_day(), [1])
        block = lines[lines.index("SHIFT STARTING INV #1"):]
        self.assertEqual(block[1], printer.clock_words(at(6)))
        self.assertEqual(block[2], "")
        self.assertTrue(block[3].startswith("T 1: "), block[3])
        labels = [line.split(":")[0].strip() for line in block[4:18] if line]
        self.assertEqual(labels, [
            "VOLUME", "ULLAGE", "90% ULLAGE", "TC VOLUME", "WATER VOL",
            "WATER", "TEMP", "DLVY ADJUSTMENT", "GROSS CHANGE",
            "TC NET CHANGE", "HEIGHT"])
        rows = {line.split(":")[0].strip(): line for line in block[4:18]
                if line}
        # the values right-aligned to the roll's last column
        self.assertTrue(rows["VOLUME"].endswith(" 9000 GALS"), rows)
        self.assertTrue(rows["GROSS CHANGE"].endswith(" 1000"), rows)
        self.assertEqual({len(line) for line in rows.values()}, {24})

    def test_the_ending_report_is_the_closed_shifts(self):
        lines = printer.last_shift(self.a_day(), ending=True)
        self.assertIn("SHIFT ENDING INV #1", lines)
        self.assertNotIn("SHIFT ENDING INV #2", lines)
        self.assertTrue([line for line in lines if line.startswith("VOLUME")
                         and line.endswith(" 8000 GALS")], lines)

    def test_it_comes_off_the_roll_unfolded(self):
        for line in printer.last_shift(self.a_day()):
            self.assertLessEqual(len(line), printer.WIDTH, line)


if __name__ == "__main__":
    unittest.main()
