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
"""Leak tests: a test that measures, and an alarm that means something.

The engine works in console time, so these run the clock forward by hand
rather than sleeping.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import leaktest, printer                    # noqa: E402
from tls350sim.console import Console, describe_alarms      # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


def a_console(leak=0.0, volume=5000.0):
    c = Console()
    for key in ("probe", "plld", "wplld", "vlld"):
        c.modules[key] = True
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.tank_level[1] = {"volume": volume, "water": 0.0}
    c.tank_leak[1] = leak
    return c


def run_out(console, hours=100.0):
    """Jump the console's clock forward and let the engine catch up."""
    console.clock_offset += hours * 3600.0
    console.leaks.tick()


class Measuring(unittest.TestCase):
    def test_a_tight_tank_passes(self):
        c = a_console(leak=0.0)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        res = c.leaks.result("tank", 1, "periodic")
        self.assertEqual(res.result, leaktest.PASSED)
        self.assertEqual(c.compute_alarms(), [])

    def test_a_leak_smaller_than_the_test_rate_passes(self):
        c = a_console(leak=0.05)
        c.leaks.start("tank", 1, "periodic", hours=2.0)   # 0.2 gph test
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.PASSED)

    def test_the_same_leak_fails_the_tighter_test(self):
        c = a_console(leak=0.15)
        c.leaks.start("tank", 1, "periodic", hours=2.0)   # 0.2 gph
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.PASSED)
        c.leaks.start("tank", 1, "annual", hours=2.0)     # 0.1 gph
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "annual").result,
                         leaktest.FAILED)

    def test_a_failed_test_raises_the_alarm_the_manual_names(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        self.assertEqual(c.compute_alarms(), ["021401"])
        self.assertEqual(describe_alarms(c.compute_alarms())[0]["screen"],
                         "T 1:PERIODIC LEAK TEST FAIL")

    def test_a_fail_alarm_turned_off_in_setup_stays_off(self):
        c = a_console(leak=0.5)
        c.values["S62D01"] = "01100"          # gross on, periodic off
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.FAILED)
        self.assertEqual(c.compute_alarms(), [])

    def test_a_tank_below_its_minimum_volume_is_invalid(self):
        c = a_console(leak=0.5, volume=500.0)
        c.values["S63601"] = "01" + struct.pack(">f", 2000.0).hex().upper()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.INVALID)
        self.assertEqual(c.compute_alarms(), [])

    def test_a_test_in_progress_says_so_and_is_not_an_alarm(self):
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        self.assertEqual(c.compute_alarms(), ["022001"])
        self.assertIn("TEST ACTIVE", c.leaks.status_line("tank", 1, "periodic"))
        self.assertTrue(describe_alarms(c.conditions())[0]["description"]
                        .endswith("Active"))

    def test_stopping_a_timed_test_early_makes_it_invalid(self):
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        c.leaks.stop("tank", 1)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.INVALID)

    def test_a_manual_stop_test_finishes_when_it_is_stopped(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=12.0, manual_stop=True)
        c.clock_offset += 4 * 3600.0
        c.leaks.tick()
        self.assertIsNotNone(c.leaks.active("tank", 1))   # runs until stopped
        c.leaks.stop("tank", 1)
        res = c.leaks.result("tank", 1, "periodic")
        self.assertEqual(res.result, leaktest.FAILED)
        self.assertAlmostEqual(res.hours, 4.0, places=1)

    def test_the_tank_actually_loses_the_product(self):
        c = a_console(leak=100.0)
        c.tick()                          # the first look sets the baseline
        c.clock_offset += 3600.0          # an hour on the console's clock
        c.tick()
        self.assertLess(c.tank_level[1]["volume"], 4901.0)


class Scheduled(unittest.TestCase):
    def test_a_daily_test_starts_itself_at_the_time_programmed(self):
        c = a_console(leak=0.02)
        # S611: two hours, 0.2 gph, DAILY, at 02:00
        c.values["S61101"] = "01" + "02" + "0" + "5" + "0200"
        c.leaks.tick()
        self.assertEqual(c.leaks.running, {})
        c.clock_offset += 26 * 3600.0
        c.leaks.tick()
        run = c.leaks.active("tank", 1)
        self.assertIsNotNone(run)
        self.assertEqual((run.rate_key, run.hours), ("periodic", 2.0))
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.PASSED)

    def test_a_test_with_no_schedule_waits_to_be_started(self):
        c = a_console()
        c.clock_offset += 48 * 3600.0
        c.leaks.tick()
        c.clock_offset += 48 * 3600.0
        c.leaks.tick()
        self.assertEqual(c.leaks.running, {})

    def test_a_repetitive_line_test_runs_again_as_soon_as_it_is_free(self):
        c = a_console()
        c.values["S78C01"] = "011"          # 0.20 gph schedule: REPETITIVE
        c.leaks.tick()
        c.clock_offset += 60.0
        c.leaks.tick()
        self.assertIsNotNone(c.leaks.active("plld", 1))
        run_out(c, 2)
        self.assertEqual(c.leaks.result("plld", 1, "periodic").result,
                         leaktest.PASSED)
        c.leaks.tick()
        self.assertIsNotNone(c.leaks.active("plld", 1))   # and again


class Lines(unittest.TestCase):
    def a_line(self, leak, shutdown="02", method="0"):
        c = a_console()
        c.values["S78401"] = "01" + shutdown
        c.values["S55300"] = method
        c.line_leak[("plld", 1)] = leak
        return c

    def test_a_failed_line_test_shuts_the_line_down(self):
        c = self.a_line(0.5)
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertEqual(c.leaks.result("plld", 1, "periodic").result,
                         leaktest.FAILED)
        self.assertIn(("plld", 1), c.leaks.disabled)
        self.assertIn("210801", c.compute_alarms())

    def test_a_leak_under_the_shutdown_rate_fails_without_shutting_down(self):
        c = self.a_line(0.15, shutdown="02")     # shut down at 0.2 gph
        c.leaks.start("plld", 1, "annual")       # 0.1 gph test
        run_out(c, 12)
        self.assertEqual(c.leaks.result("plld", 1, "annual").result,
                         leaktest.FAILED)
        self.assertNotIn(("plld", 1), c.leaks.disabled)

    def test_pass_line_test_is_the_only_way_back_when_set_that_way(self):
        c = self.a_line(0.5, method="0")
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        c.acknowledge()
        self.assertIn(("plld", 1), c.leaks.disabled)
        c.line_leak[("plld", 1)] = 0.0
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertNotIn(("plld", 1), c.leaks.disabled)

    def test_acknowledge_alarm_re_enables_when_set_that_way(self):
        c = self.a_line(0.5, method="1")
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        c.acknowledge()
        self.assertNotIn(("plld", 1), c.leaks.disabled)


class OverTheWire(unittest.TestCase):
    def setUp(self):
        self.c = a_console(leak=0.05)
        self.h = Handler(self.c, verbose=False)

    def ask(self, command):
        return self.h.handle(command).decode("ascii")

    def test_a_tool_can_start_and_stop_a_test(self):
        self.ask(b"\x01S05201\r")
        self.assertIsNotNone(self.c.leaks.active("tank", 1))
        self.ask(b"\x01S05301\r")
        self.assertIsNone(self.c.leaks.active("tank", 1))

    def test_the_detect_report_shows_a_test_in_progress(self):
        self.ask(b"\x01S05201\r")
        self.assertIn("TEST STATUS: ON", self.ask(b"\x01I20301\r"))

    def test_the_results_report_carries_the_result(self):
        self.ask(b"\x01S05201\r")
        run_out(self.c, 5)
        report = self.ask(b"\x01I20801\r")
        self.assertIn("PREVIOUS IN TANK LEAK TEST RESULTS", report)
        self.assertIn("PASSED", report)

    def test_the_computer_format_matches_function_208(self):
        self.ask(b"\x01S05201\r")
        run_out(self.c, 5)
        # <SOH>i20801 YYMMDDHHmm TT NN tt mm YYMMDDHHmm RR rate hours volume
        body = self.ask(b"\x01i20801\r")[17:-7]  # less the &&CCCC
        self.assertTrue(body.startswith("0101"))       # tank 01, one result
        self.assertEqual(body[4:6], "00")              # 0.20 gal/hr test
        self.assertEqual(body[6:8], "00")              # not manifolded
        self.assertEqual(body[18:20], "01")            # passed
        self.assertEqual(len(body), 20 + 24)           # three floats follow

    def test_a_console_with_no_probe_refuses_to_start_one(self):
        self.c.modules["probe"] = False
        self.assertEqual(self.ask(b"\x01S05201\r"), "\x019999FF1B\x03")


class Printouts(unittest.TestCase):
    def test_the_report_prints_the_result(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        out = "\n".join(printer.leak_tests(c, "tank"))
        self.assertIn("IN-TANK LEAK TEST RESULTS", out)
        self.assertIn("FAILED", out)
        self.assertIn("REGULAR UNLEADED", out)

    def test_a_shut_down_line_says_so_on_the_report(self):
        c = a_console()
        c.values["S78401"] = "0102"
        c.line_leak[("plld", 1)] = 0.5
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertIn("LINE SHUT DOWN",
                      "\n".join(printer.leak_tests(c, "plld")))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LossLimits(unittest.TestCase):
    """The two limits a tank carries for what it loses DURING a test."""

    def a_tank_under_test(self, leak, sudden=25.0, alarm=8.0):
        c = a_console(leak=leak)
        c.values["S62501"] = "01" + struct.pack(">f", sudden).hex().upper()
        c.values["S62601"] = "01" + struct.pack(">f", alarm).hex().upper()
        c.tick()                            # the clock needs a starting point
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        return c

    def _drain(self, console, hours):
        """Let the console's own clock run, so the tank loses what it should."""
        console.clock_offset += hours * 3600.0
        console.tick()

    def test_a_tight_tank_trips_neither_limit(self):
        c = self.a_tank_under_test(leak=0.0)
        self._drain(c, 4)
        alarms = c.compute_alarms()
        self.assertNotIn("020201", alarms)
        self.assertNotIn("020601", alarms)

    def test_the_leak_alarm_limit_warns_before_the_test_finishes(self):
        """"A limit value of 8 gallons will warn of a 1 gph leak in 8 hours"."""
        c = self.a_tank_under_test(leak=1.0)
        self._drain(c, 4)
        self.assertNotIn("020201", c.compute_alarms())
        self._drain(c, 5)
        self.assertIn("020201", c.compute_alarms())
        self.assertEqual(describe_alarms(["020201"])[0]["description"],
                         "Leak Alarm")

    def test_a_sudden_loss_says_so_at_once(self):
        c = self.a_tank_under_test(leak=60.0)
        self._drain(c, 1)                   # sixty gallons gone in an hour
        alarms = c.compute_alarms()
        self.assertIn("020601", alarms)
        self.assertIn("020201", alarms)
        self.assertEqual(describe_alarms(["020601"])[0]["description"],
                         "Sudden Loss Alarm")

    def test_and_the_test_itself_fails_it(self):
        c = self.a_tank_under_test(leak=60.0)
        self._drain(c, 13)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.FAILED)


class TestNeeded(unittest.TestCase):
    """"the number of days after which you want the system to warn that a
    tank test has not been passed"."""

    def a_watched_tank(self):
        c = a_console()
        c.values["S60101"] = "011"
        c.values["S54600"] = "1"           # periodic test needed warning on
        c.values["S54700"] = "07"          # warn after seven days
        c.values["S54800"] = "14"          # alarm after fourteen
        c.tick()
        return c

    def test_a_fresh_console_is_not_yet_overdue(self):
        c = self.a_watched_tank()
        self.assertNotIn("021601", c.compute_alarms())

    def test_seven_days_without_a_pass_is_a_warning(self):
        c = self.a_watched_tank()
        c.clock_offset += 8 * 86400.0
        c.tick()
        self.assertIn("021601", c.compute_alarms())
        self.assertEqual(describe_alarms(["021601"])[0]["description"],
                         "Periodic Test Needed Warning")

    def test_fourteen_days_is_an_alarm_instead(self):
        c = self.a_watched_tank()
        c.clock_offset += 15 * 86400.0
        c.tick()
        alarms = c.compute_alarms()
        self.assertIn("021801", alarms)
        self.assertNotIn("021601", alarms)

    def test_passing_a_test_puts_the_clock_back_to_zero(self):
        c = self.a_watched_tank()
        c.clock_offset += 15 * 86400.0
        c.tick()
        self.assertIn("021801", c.compute_alarms())
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        c.tick()
        self.assertNotIn("021801", c.compute_alarms())
        self.assertNotIn("021601", c.compute_alarms())
