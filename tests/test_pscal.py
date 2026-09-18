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
"""CALIBRATE SMARTSENSOR, 577013-800 Rev P p.20-45 and 577013-937 Rev J
Figure 46. The walk was absent and V83's slope and offset were generated
per call with no procedure behind them. FIDELITY I11."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import printer
from tls350sim.console import Console, DIAG_MENU


def a_pressure_sensor(category="02"):
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("smart", 1)
    c.values["S72301"] = "01" + category
    c.values["S72201"] = "01VP: FP1-2".ljust(22)
    return c


def calibrate(c, zero=0.0, span=2.0):
    cal = c.calibrations
    cal.enter("zero", 1, zero)
    cal.capture("zero", 1)
    cal.enter("span", 1, span)
    cal.capture("span", 1)
    return cal.history(1)[0]


class TheTwoPoints(unittest.TestCase):

    def test_a_calibration_measures_the_sensors_own_error(self):
        c = a_pressure_sensor()
        _at, slope, offset, passed = calibrate(c)
        own_slope, own_offset = c.calibrations.factory(1)
        self.assertAlmostEqual(slope, own_slope, places=6)
        self.assertAlmostEqual(offset, own_offset, places=6)
        self.assertTrue(passed)
        self.assertEqual(c.diag_value("ps_calb", 1),
                         "CALB STATUS: PASS" + chr(10)
                         + "PRESS <STEP> TO CONTINUE")

    def test_one_reference_twice_is_no_slope_and_does_not_pass(self):
        c = a_pressure_sensor()
        _at, _slope, _offset, passed = calibrate(c, zero=1.0, span=1.0)
        self.assertFalse(passed)
        self.assertTrue(c.diag_value("ps_calb", 1).startswith(
            "CALB STATUS: FAIL"))

    def test_the_screens_read_the_pressure_applied_at_the_sensor(self):
        """"Enter reference pressure value from calibrated test device at
        pressure sensor": once it is there, the sensor reads it."""
        c = a_pressure_sensor()
        cal = c.calibrations
        cal.enter("zero", 1, 0.0)
        own_slope, own_offset = cal.factory(1)
        self.assertEqual(c.diag_value("ps_zero_ref", 1), "PRESSURE: +00.000")
        self.assertEqual(c.diag_value("ps_raw", 1),
                         f"PRESSURE: {(0.0 - own_offset) / own_slope:+07.3f}")
        cal.enter("span", 1, 2.0)
        self.assertEqual(c.diag_value("ps_span_ref", 1), "PRESSURE: +02.000")

    def test_the_history_ends_with_the_factory_calibration(self):
        c = a_pressure_sensor()
        self.assertEqual(len(c.calibration_history("smart", 1, 5)), 1)
        calibrate(c)
        rows = c.calibration_history("smart", 1, 5)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[-1][3])

    def test_the_printout(self):
        c = a_pressure_sensor()
        calibrate(c)
        lines = printer.ps_calibration(c, 1)
        self.assertIn("CALIBRATION HISTORY", lines)
        self.assertIn("s 1: VAPOR PRESSURE", lines)
        self.assertEqual(lines.count("CALB STATUS: PASS"), 2)
        self.assertTrue(any(line.startswith("SLOPE:  ") and len(line) == 16
                            for line in lines), lines)


class TheMenuWantsAPressureSensor(unittest.TestCase):
    """"This menu only appears if this Smartsensor type is a pressure
    sensor"."""

    def offered(self, c):
        fn = [f for f in DIAG_MENU
              if f["function"] == "SMART SENSOR DIAGNOSTIC"][0]
        return [(s["l1"], s.get("l2")) for s in fn["screens"]
                if not s.get("when") or c.visible(s, 1)]

    def test_on_a_pressure_sensor_and_not_on_an_air_flow_meter(self):
        shown = self.offered(a_pressure_sensor("02"))
        self.assertIn(("CALIBRATE SMARTSENSOR", "PRESS <ENTER>"), shown)
        self.assertIn(("READ SPAN VALUE", "PRESSURE: -XX.XXX"), shown)
        shown = self.offered(a_pressure_sensor("01"))
        self.assertNotIn(("CALIBRATE SMARTSENSOR", "PRESS <ENTER>"), shown)
        self.assertIn(("s 1: (Label)", "CHANNELS PRESS <PRINT>"), shown)
        shown = self.offered(a_pressure_sensor("03"))
        self.assertNotIn(("CALIBRATE SMARTSENSOR", "PRESS <ENTER>"), shown)


if __name__ == "__main__":
    unittest.main()
