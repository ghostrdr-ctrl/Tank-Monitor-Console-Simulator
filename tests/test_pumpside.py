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
"""The pump side of the check valve.

"After the system conducts a line leak test, the line leak detector also runs
a pump side test for a pressure loss in the piping and connections between the
in-line check valve and the submersible pump."

That is a DIFFERENT piece of pipe from the one the line test measured. The
console used to derive the pumpside count from the line count, which meant a
sound line over a leaking pump-side joint reported a passing pumpside test --
the one failure the test exists to catch was the one it could not report. The
setup manual is blunt about the stakes: "Failure to provide leak detection
capability for components prior to the VLLD check valve could allow undetected
product leakage with possible contamination of the environment."
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import leaktest, presets                    # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_line(pumpside=True):
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("smart", "vlld", "modem"):
        c.modules[card] = 4
    h = Handler(c, verbose=False)
    h.handle((chr(1) + "S75801" + ("01" if pumpside else "00")
              + chr(13)).encode())
    return c, h


def report(h):
    return h.handle((chr(1) + "I35101" + chr(13)).encode()).decode("latin-1")


def run_line(c, passed=True, rate_key="gross"):
    c.leaks.record_line("vlld", 1, rate_key, passed, 0.0,
                        time.mktime(c.now()))


class ItIsItsOwnMeasurement(unittest.TestCase):

    def test_a_sound_line_over_a_leaking_pump_side_joint_fails_the_pump(self):
        """The case the whole feature exists for, and the case a derived
        count gets wrong."""
        c, _h = a_line()
        c.line_leak[("vlld", 1)] = 0.0
        c.pump_leak[("vlld", 1)] = 8.0
        run_line(c, passed=True)
        self.assertEqual(c.leaks.result("vlld", 1, "gross").result, leaktest.PASSED)
        self.assertEqual(
            c.leaks.result(leaktest.PUMP_KIND, 1, "gross").result, leaktest.FAILED)

    def test_the_report_shows_the_line_passing_and_the_pump_not(self):
        c, h = a_line()
        c.pump_leak[("vlld", 1)] = 8.0
        run_line(c, passed=True)
        row = [r for r in report(h).split("\r\n") if "PREV 24 HOURS" in r][0]
        line, self_, pump = row.split()[-3:]
        self.assertEqual((line, self_), ("1", "1"))
        self.assertEqual(pump, "0", "a derived count would read 1 here")

    def test_both_sound_counts_all_three(self):
        c, h = a_line()
        c.pump_leak[("vlld", 1)] = 0.0
        run_line(c, passed=True)
        row = [r for r in report(h).split("\r\n") if "PREV 24 HOURS" in r][0]
        self.assertEqual(row.split()[-3:], ["1", "1", "1"])

    def test_a_leaking_line_over_a_sound_pump_side_joint(self):
        """The other direction: the pump side can pass a test the line
        fails, because they are different pipe."""
        c, _h = a_line()
        c.pump_leak[("vlld", 1)] = 0.0
        run_line(c, passed=False)
        self.assertEqual(c.leaks.result("vlld", 1, "gross").result, leaktest.FAILED)
        self.assertEqual(
            c.leaks.result(leaktest.PUMP_KIND, 1, "gross").result, leaktest.PASSED)


class ItRaisesItsOwnAlarms(unittest.TestCase):
    """The console has carried these three codes since the status tables
    were written and nothing ever raised them."""

    def test_each_rate_posts_its_own_pump_alarm(self):
        for rate_key, nn in (("gross", "09"), ("periodic", "17"),
                             ("annual", "21")):
            c, _h = a_line()
            c.pump_leak[("vlld", 1)] = 99.0
            run_line(c, passed=True, rate_key=rate_key)
            posted = [a for a in c.compute_alarms() if a == "06" + nn + "01"]
            self.assertTrue(posted, f"{rate_key} should post 06{nn}")

    def test_a_passing_retest_takes_the_pump_alarm_back_down(self):
        """The condition clears. The LATCH does not, and that is the console
        being right: once compute_alarms has seen an alarm it stays latched
        until it is acknowledged, which is what a real one does with a test
        result."""
        c, _h = a_line()
        c.pump_leak[("vlld", 1)] = 8.0
        run_line(c)
        self.assertIn("060901", c.posted)
        self.assertIn("060901", c.compute_alarms())
        c.pump_leak[("vlld", 1)] = 0.0
        run_line(c)
        self.assertNotIn("060901", c.posted, "the condition should be gone")
        self.assertIn("060901", c.latched, "but the latch survives it")

    def test_the_line_alarm_and_the_pump_alarm_are_different_codes(self):
        self.assertNotEqual(leaktest.FAIL_ALARM["vlld"]["gross"],
                            leaktest.PUMP_FAIL_ALARM["gross"])


class OnlyWhereTheSiteEnabledIt(unittest.TestCase):

    def test_s758_off_means_no_pumpside_test_at_all(self):
        """"Pumpside tests will only occur if Pumpside Test is enabled in the
        VLLD Setup"."""
        c, _h = a_line(pumpside=False)
        c.pump_leak[("vlld", 1)] = 99.0
        run_line(c)
        self.assertIsNone(c.leaks.result(leaktest.PUMP_KIND, 1, "gross"))
        self.assertNotIn("060901", c.compute_alarms())

    def test_s758_on_is_what_turns_it_on(self):
        c, _h = a_line(pumpside=True)
        self.assertTrue(c.leaks.pumpside_enabled(1))
        c2, _h2 = a_line(pumpside=False)
        self.assertFalse(c2.leaks.pumpside_enabled(1))

    def test_the_results_never_mix_with_the_line_results(self):
        """A pumpside pass counted as a line pass would report a line test
        that never ran."""
        c, _h = a_line()
        run_line(c)
        line_log = c.leaks.history[("vlld", 1)]
        pump_log = c.leaks.history[(leaktest.PUMP_KIND, 1)]
        self.assertEqual(len(line_log), 1)
        self.assertEqual(len(pump_log), 1)
        self.assertNotEqual(line_log[0].kind, pump_log[0].kind)


if __name__ == "__main__":
    unittest.main()
