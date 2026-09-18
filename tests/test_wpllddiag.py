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
"""The WPLLD diag screens' Comm Module status, 577013-344 Rev H Figure 20.

"Top line of display shows WPLLD Comm Module status" -- and the switches
screen's `S` is the same message. Both drew something else. FIDELITY U14.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import pressure
from tls350sim.console import Console


def a_wplld_line():
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("wplld", 1)
    c.values["S7A201"] = "01UNLEADED".ljust(22)
    return c, c.lines.line("wplld", 1)


class TheTopLineIsTheCommModule(unittest.TestCase):

    def top(self, c):
        return c.line_diag("line_pressure", 1, "wplld").split(chr(10))[0]

    def test_at_rest_it_is_the_figures_own_line(self):
        c, ln = a_wplld_line()
        self.assertTrue(ln.programmed())
        self.assertEqual(self.top(c), "W 1: PENDING    PUMP OFF")

    def test_a_handle_up_is_dispensing(self):
        c, ln = a_wplld_line()
        ln.handle, ln.pump = True, True
        self.assertEqual(self.top(c), "W 1: DISPENSING  PUMP ON")

    def test_each_stage_of_a_test_has_its_word(self):
        c, ln = a_wplld_line()
        self.assertEqual(c.lines.start("wplld", 1, "gross"), "TEST STARTED")
        self.assertEqual(ln.comm_status(), "PRESSURIZING")
        for stage, word in (("t1", "DECAY"), ("t2", "DECAY"),
                            ("mid1", "DECAY"), ("spike1", "MEASUREMENT"),
                            ("spike2", "MEASUREMENT"), ("pad", "PENDING")):
            ln.stage = stage
            self.assertEqual(ln.comm_status(), word, stage)
        ln.stage = "spike1"
        self.assertEqual(self.top(c), "W 1: MEASUREMENT PUMP ON")
        self.assertEqual(len(self.top(c)), pressure.SCREEN)

    def test_plld_keeps_its_pressure(self):
        """Figure 19 is PLLD's, and it puts the pressure there."""
        c = Console()
        c.set_module("plld", 1)
        c.values["S78201"] = "01UNLEADED".ljust(22)
        head = c.line_diag("line_pressure", 1, "plld").split(chr(10))[0]
        self.assertRegex(head, r"^Q 1: +[0-9.]+ PSI +PUMP O")


class TheSwitchesScreenSaysPumpAndStatus(unittest.TestCase):
    """p.29: "P0 = pump off, P1 = pump on, H0 = handle off, H1 = handle on,
    S = WPLLD Comm Module status message (see above)"."""

    def test_p_is_the_pump_and_s_is_the_module(self):
        c, ln = a_wplld_line()
        top = c.line_diag("line_switches", 1, "wplld").split(chr(10))[0]
        # the line stands above 12 psi at rest, which is what P used to read
        self.assertGreater(ln.pressure, pressure.FLOOR)
        self.assertEqual(top, "W 1: P0 H0  S: PENDING")
        ln.handle, ln.pump = True, True
        top = c.line_diag("line_switches", 1, "wplld").split(chr(10))[0]
        self.assertEqual(top, "W 1: P1 H1  S: DISPENSIN")


if __name__ == "__main__":
    unittest.main()
