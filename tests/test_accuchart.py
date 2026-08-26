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
"""AccuChart: the console teaching itself the shape of the tank.

Everything here runs on the console's own clock, because a calibration is a
56 day process and nobody is sitting through one.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import accuchart, printer                    # noqa: E402
from tls350sim.console import Console, describe_alarms      # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


def a_site(scheduling="1", meter_data=True, mag=True, linear=False):
    """One tank, a meter selling out of it, and BIR to license the lot."""
    c = Console()
    c.modules = {"probe": 1, "rs232": 1}
    c.software = {"bir": True, "csld": True}
    c.values["S60101"] = "011"
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S60701"] = "01" + struct.pack(">f", 96.0).hex().upper()
    code = "60A" if linear else "604"
    c.values["S" + code + "01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    if meter_data:
        c.values["S61501"] = "011"
    if mag:
        c.values["S62F01"] = "011"
    c.values["S61601"] = "01" + scheduling
    c.tank_level[1] = {"volume": 6000.0, "water": 0.0}
    c.meters = {1: 1}
    c.modules["edim"] = 1        # meter data needs a DIM
    c.meter_flow = {1: 300.0}
    c.tick()
    return c


def days(console, n):
    """Run n days of console time, keeping the tank from running dry."""
    for _ in range(n):
        console.clock_offset += 86400.0
        console.tick()
        console.tank_level[1]["volume"] = 6000.0


class WhenItRuns(unittest.TestCase):
    def test_a_tank_with_meter_data_and_a_mag_probe_calibrates(self):
        c = a_site()
        self.assertTrue(c.accuchart.enabled(1))
        self.assertEqual(c.accuchart.screen(1, "enabled"), "ACCU ENABLED")

    def test_no_meter_data_is_the_first_reason_it_does_not(self):
        c = a_site(meter_data=False)
        self.assertFalse(c.accuchart.enabled(1))
        self.assertIn("METER DATA", c.accuchart.disabled_because(1))

    def test_it_does_not_run_on_a_linear_tank(self):
        """"Accuchart is not capable of calibrating linear tanks"."""
        c = a_site(linear=True)
        self.assertIn("LINEAR", c.accuchart.disabled_because(1))

    def test_it_does_not_run_without_a_mag_probe(self):
        c = a_site(mag=False)
        self.assertIn("MAG", c.accuchart.disabled_because(1))

    def test_it_does_not_run_without_the_bir_key(self):
        c = a_site()
        c.software.pop("bir")
        self.assertFalse(c.accuchart.enabled(1))


class TheStateMachine(unittest.TestCase):
    def test_it_calibrates_for_56_days_and_then_monitors(self):
        c = a_site()
        days(c, 10)
        self.assertEqual(c.accuchart.state(1).mode, accuchart.CALIBRATE)
        days(c, 50)
        self.assertEqual(c.accuchart.state(1).mode, accuchart.MONITOR)
        self.assertIn("MONITOR", c.accuchart.screen(1, "mode"))

    def test_the_first_calibration_waits_two_weeks(self):
        """"Depending on throughput, the first COE calibration occurs after
        two weeks"."""
        c = a_site()
        days(c, 10)
        self.assertEqual(c.accuchart.state(1).updates, 0)
        days(c, 8)
        self.assertGreater(c.accuchart.state(1).updates, 0)

    def test_user_status_turns_on_once_a_chart_has_been_applied(self):
        c = a_site()
        self.assertIn("DISABLED", c.accuchart.screen(1, "status"))
        days(c, 20)
        self.assertIn("ENABLED", c.accuchart.screen(1, "status"))

    def test_the_chart_converges_and_the_fitness_falls(self):
        c = a_site()
        days(c, 20)
        first = c.accuchart.state(1).chart.fitness
        days(c, 40)
        self.assertLess(c.accuchart.state(1).chart.fitness, first)
        self.assertLess(c.accuchart.state(1).chart.fitness, 0.5)

    def test_a_reset_puts_it_back_to_the_programmed_tank(self):
        c = a_site()
        days(c, 30)
        self.assertGreater(c.accuchart.state(1).updates, 0)
        c.accuchart.restart(1)
        entry = c.accuchart.state(1)
        self.assertEqual(entry.updates, 0)
        self.assertEqual(entry.mode, accuchart.CALIBRATE)
        self.assertAlmostEqual(entry.chart.capacity, 10000.0, places=0)


class TheSchedule(unittest.TestCase):
    def test_never_calibrates_but_never_applies(self):
        """"AccuChart performs its 56-day tank calibration, but it never
        revises the Active Tank Profile"."""
        c = a_site(scheduling=accuchart.NEVER)
        days(c, 60)
        entry = c.accuchart.state(1)
        self.assertEqual(entry.updates, 0)
        self.assertNotEqual(round(entry.chart.capacity), 10000)

    def test_periodic_applies_twice(self):
        """"The first calibration update occurs 28 days ... and the second
        calibration update occurs 56 days"."""
        c = a_site(scheduling=accuchart.PERIODIC)
        days(c, 20)
        self.assertEqual(c.accuchart.state(1).updates, 0)
        days(c, 10)
        self.assertEqual(c.accuchart.state(1).updates, 1)
        days(c, 30)
        self.assertEqual(c.accuchart.state(1).updates, 2)

    def test_complete_applies_once(self):
        c = a_site(scheduling=accuchart.COMPLETE)
        days(c, 60)
        self.assertEqual(c.accuchart.state(1).updates, 1)

    def test_immediate_applies_every_time(self):
        c = a_site(scheduling=accuchart.IMMEDIATE)
        days(c, 60)
        self.assertGreater(c.accuchart.state(1).updates, 3)


class TheAlarm(unittest.TestCase):
    def test_a_tank_nobody_sells_out_of_never_calibrates(self):
        c = a_site()
        c.meter_flow = {}
        days(c, 60)
        self.assertTrue(c.accuchart.state(1).failed)
        self.assertIn("022401", c.compute_alarms())
        self.assertEqual(describe_alarms(["022401"])[0]["description"],
                         "AccuChart Calibration Warning")

    def test_a_tank_that_calibrates_raises_nothing(self):
        c = a_site()
        days(c, 60)
        self.assertNotIn("022401", c.compute_alarms())


class TheReports(unittest.TestCase):
    def send(self, console, command):
        return Handler(console, verbose=False).handle(
            (chr(1) + command + chr(13)).encode()).decode("ascii", "replace")

    def test_b91_reports_the_chart_it_has_worked_out(self):
        c = a_site()
        days(c, 30)
        out = self.send(c, "IB9100")
        self.assertIn("ACCU_CHART DIAGNOSTICS", out)
        self.assertIn("ENABLED", out)

    def test_b93_reports_the_state_machine(self):
        c = a_site()
        days(c, 60)
        self.assertIn("MONITOR", self.send(c, "IB9300"))

    def test_b94_reports_every_calibration(self):
        c = a_site()
        days(c, 40)
        out = self.send(c, "IB9401")
        self.assertIn("ACCU_CHART CALIBRATION HISTORY", out)

    def test_the_computer_format_packs_floats(self):
        c = a_site()
        days(c, 30)
        out = self.send(c, "iB9100")
        self.assertIn("&&", out)
        self.assertIn("0101", out)          # tank 01, status 01 enabled

    def test_891_restarts_one_tank_and_refuses_all_tanks(self):
        c = a_site()
        days(c, 30)
        self.assertIn("S89101", self.send(c, "S89101149"))
        self.assertEqual(c.accuchart.state(1).updates, 0)
        self.assertIn("9999", self.send(c, "S89100149"))

    def test_891_wants_its_verification_code(self):
        c = a_site()
        self.assertIn("9999", self.send(c, "S89101"))

    def test_a_console_with_no_probe_says_9999(self):
        c = a_site()
        c.modules.pop("probe")
        self.assertIn("9999", self.send(c, "IB9100"))

    def test_the_printer_prints_the_same_thing(self):
        c = a_site()
        days(c, 30)
        text = "\n".join(printer.accuchart(c, [1], "history"))
        self.assertIn("ACCU_CHART CALIBRATION HISTORY", text)
        text = "\n".join(printer.accuchart_update(c, 1, 0))
        self.assertIn("ACCUCHART CALIBRATION UPDATE", text)


class TheEepromCommands(unittest.TestCase):
    """851, 852 and 853, the archive over the wire."""

    def send(self, console, command):
        return Handler(console, verbose=False).handle(
            (chr(1) + command + chr(13)).encode()).decode("ascii", "replace")

    def test_save_then_restore_then_clear(self):
        c = a_site()
        c.state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_eeprom_test.json")
        try:
            self.assertIn("S85200", self.send(c, "S85200149"))
            self.assertTrue(c.archive_exists())
            c.values["S60201"] = "01WRONG               "
            self.assertIn("S85100", self.send(c, "S85100149"))
            self.assertEqual(c.values["S60201"], "01REGULAR UNLEADED   ")
            self.assertIn("S85300", self.send(c, "S85300149"))
            self.assertFalse(c.archive_exists())
        finally:
            for path in (c.archive_path(), c.state_path):
                if os.path.exists(path):
                    os.remove(path)

    def test_they_all_want_the_verification_code(self):
        c = a_site()
        for command in ("S85100", "S85200", "S85300"):
            self.assertIn("9999", self.send(c, command))

    def test_a_restore_with_nothing_in_the_chip_is_refused(self):
        c = a_site()
        c.state_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_empty_test.json")
        if os.path.exists(c.archive_path()):
            os.remove(c.archive_path())
        self.assertIn("9999", self.send(c, "S85100149"))


class TheDiagnosticScreens(unittest.TestCase):
    def test_every_screen_reads_a_value_and_not_an_x(self):
        c = a_site()
        days(c, 30)
        for what in ("enabled", "mode", "status", "updates", "duration",
                     "diameter", "length", "offset", "tilt", "shape",
                     "volume", "fitness", "data", "warn"):
            line = c.accuchart.screen(1, what)
            self.assertTrue(line, what)
            self.assertNotIn("XX", line, what)

    def test_a_disabled_tank_still_shows_the_parameters_in_use(self):
        c = a_site(meter_data=False)
        self.assertEqual(c.accuchart.screen(1, "enabled"), "ACCU DISABLED")
        self.assertIn("10000", c.accuchart.screen(1, "volume"))
