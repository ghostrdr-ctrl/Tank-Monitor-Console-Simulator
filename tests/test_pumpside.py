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
from tls350sim.console import Console, describe_alarms     # noqa: E402
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


class ItRunsAfterATestHoweverStarted(unittest.TestCase):
    """`record_line` is the pressure engine's door and a VLLD test never
    comes through it: one started at the panel finished in `_finish`, and
    the pump side went unmeasured. BENCH.md L6."""

    def test_a_panel_started_test_measures_the_pump_side_when_it_ends(self):
        c, _h = a_line()
        c.line_leak[("vlld", 1)] = 0.0
        c.pump_leak[("vlld", 1)] = 8.0
        c.tick()
        self.assertEqual(c.leaks.start("vlld", 1, "gross"), "TEST STARTED")
        self.assertNotIn("060901", c.compute_alarms())
        c.clock_offset += leaktest.LINE_SECONDS["gross"] + 60.0
        c.tick()
        self.assertFalse(c.leaks.active("vlld", 1))
        self.assertIn("060901", c.compute_alarms())
        self.assertEqual(c.leaks.pumpside_passes(1, "gross", 0), 0)
        # and a sound pump side passes it, and takes the alarm down
        c.pump_leak[("vlld", 1)] = 0.0
        c.leaks.start("vlld", 1, "gross")
        c.clock_offset += leaktest.LINE_SECONDS["gross"] + 60.0
        c.tick()
        self.assertEqual(c.leaks.pumpside_passes(1, "gross", 0), 1)
        # corrected, and waiting for ALARM/TEST like every other alarm
        c.acknowledge(True)
        self.assertNotIn("060901", c.compute_alarms())

    def test_not_where_the_site_never_enabled_it(self):
        c, _h = a_line(pumpside=False)
        c.pump_leak[("vlld", 1)] = 8.0
        c.tick()
        c.leaks.start("vlld", 1, "gross")
        c.clock_offset += leaktest.LINE_SECONDS["gross"] + 60.0
        c.tick()
        self.assertNotIn("060901", c.compute_alarms())


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


class ThePumpRelayMonitorDiagnostic(unittest.TestCase):
    """576013-818 Figure 6-16 draws this function twice over -- "If pump
    relay assigned" and "If pump relay = NONE" -- and annotates every value
    on it. This console drew both columns at once, with no reader at all:
    `999 SEC`, `HH:MM` and `HHH:MM` never moved and `PUMP (OUT)` and
    `RELAY (IN)` were permanently OFF. FIDELITY D1."""

    def a_console(self, assigned=True):
        c = Console()
        c.modules = {"probe": 1, "rs232": 1, "pumpmon": 1, "relay": 1}
        c.values["S7C501"] = "01STP 1 MONITOR      "
        if assigned:
            c.values["S7C601"] = "01" + "1101"      # watching relay 1
        return c

    def screens(self, c):
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "PUMP RELAY MONITOR DIAG"][0]
        out = []
        for sc in fn["screens"]:
            if not c.visible(sc, 1):
                continue
            head = c.diag_line(sc["l1"], 1)
            body = c.diag_value(sc["live"], 1)
            if chr(10) in body:
                head, body = body.split(chr(10), 1)
            out.append((head[:24], body[:24]))
        return out

    def test_the_assigned_branch_reads_the_relay_and_the_pump(self):
        c = self.a_console()
        c.relays[1] = True
        drawn = self.screens(c)
        self.assertEqual(len(drawn), 2)
        self.assertEqual(drawn[0], ("r 1: PUMP (OUT): ON",
                                    "R 1: RELAY (IN): ON"))
        self.assertTrue(drawn[1][0].startswith("r 1: STUCK RELAY:"))
        self.assertTrue(drawn[1][1].startswith("PUMP RUN TIME: "))
        c.relays[1] = False
        self.assertEqual(self.screens(c)[0], ("r 1: PUMP (OUT): OFF",
                                              "R 1: RELAY (IN): OFF"))

    def test_the_other_branch_is_the_label_over_the_pump(self):
        """"If pump relay = NONE": the label takes the top line and the
        pump state moves under it, and the run time is three digits."""
        c = self.a_console(assigned=False)
        drawn = self.screens(c)
        self.assertEqual(len(drawn), 2)
        self.assertEqual(drawn[0], ("r 1: STP 1 MONITOR", "PUMP (OUT): OFF"))
        self.assertEqual(drawn[1][0], "r 1: STP 1 MONITOR")
        self.assertRegex(drawn[1][1], r"^PUMP RUN TIME: \d\d\d:\d\d$")

    def test_the_two_branches_are_never_both_on_the_menu(self):
        for assigned in (True, False):
            c = self.a_console(assigned)
            drawn = self.screens(c)
            labels = [head for head, _b in drawn]
            self.assertEqual(len(drawn), 2, drawn)
            self.assertEqual(any("MONITOR" in l for l in labels), not assigned)

    def a_running_site(self):
        """A pump sense input on a tank being sold from, watched by the
        monitor: the one arrangement where PUMP (OUT) and RELAY (IN) can
        disagree, which is what the monitor is for."""
        c = self.a_console(assigned=False)
        c.modules.update({"pump": 1, "edim": 1})
        c.software["bir"] = True
        c.values["S7C601"] = "01" + "1501"          # watching pump sense 1
        c.values["S77201"] = "0101"                 # pump sense 1 -> tank 1
        c.tank_level[1] = {"volume": 500000.0, "water": 0.0}
        c.meters, c.meter_flow = {1: 1}, {1: 10.0}
        c.tick()
        return c

    def test_a_stuck_relay_counts_its_seconds_and_posts_the_alarm(self):
        """"If the pump continues to run after it is instructed to turn off,
        for longer than a 5 - 600 second selectable delay (Stuck Delay), an
        alarm is posted." Nothing raised it: `relay_stuck` was read by the
        status line alone. FIDELITY D1 and N1."""
        c = self.a_running_site()
        c.clock_offset += 30.0
        c.tick()
        self.assertEqual(describe_alarms(c.conditions()), [])
        self.assertIn("STUCK RELAY:  30 SEC",
                      c.diag_value("pumpmon_stuck", 1))
        c.clock_offset += 60.0
        c.tick()
        self.assertIn("r 1:PUMP RELAY MON ALM",
                      [a["screen"] for a in describe_alarms(c.conditions())])
        # and the correction is the relay closing, which is the condition
        c.relays[1] = True
        c.clock_offset += 10.0
        c.tick()
        self.assertEqual(describe_alarms(c.conditions()), [])
        self.assertIn("STUCK RELAY:   0 SEC",
                      c.diag_value("pumpmon_stuck", 1))

    def test_a_pump_that_never_stops_posts_the_same_alarm(self):
        """"Monitor the pump each time it switches on, and if it is still
        running after a 1 - 24 hour delay (Max Run Time delay), to post an
        alarm." Eight hours is the default."""
        c = self.a_running_site()
        c.relays[1] = True                     # not stuck; just running
        c.clock_offset += 7 * 3600.0
        c.tick()
        self.assertEqual(describe_alarms(c.conditions()), [])
        self.assertIn("PUMP RUN TIME: 007:00", c.diag_value("pumpmon_run", 1))
        c.clock_offset += 2 * 3600.0
        c.tick()
        self.assertIn("r 1:PUMP RELAY MON ALM",
                      [a["screen"] for a in describe_alarms(c.conditions())])


if __name__ == "__main__":
    unittest.main()
