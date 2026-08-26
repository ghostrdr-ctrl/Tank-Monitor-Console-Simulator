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
"""PLLD and WPLLD: a line that holds a pressure, and tests that measure it.

Everything here is checked against the PLLD & WPLLD Troubleshooting Guide,
577013-344 Rev H, whose Theory of Operation chapter gives the two valve
setpoints, the 12 psi floor, the order the tests run in, and the arithmetic
the precision tests do. The bulk modulus and gallons-per-foot behind the
pressure come from the Line Leak Application Guide, 577013-465.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import leaktest, pressure, printer                # noqa: E402
from tls350sim.console import Console                            # noqa: E402
from tls350sim.wire import Handler                               # noqa: E402

SOH = b"\x01"


def a_line(leak=0.0, pipe="03", length=501.0, kind="plld"):
    c = Console()
    for key in ("probe", "plld", "wplld"):
        c.modules[key] = True
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    # LINE CONFIG and a label, because that is what makes a position a line
    # the console admits to having -- programmed_lines()'s rule, and the
    # diagnostic reads no pressure off a position without it.
    config, label_code = Console.LINE_CODES[kind]
    c.values[f"S{config}01"] = "011"
    c.values[f"S{label_code}01"] = "01" + f"{kind.upper()} LINE 1".ljust(20)
    code = "788" if kind == "plld" else "7A8"
    length_code = "789" if kind == "plld" else "7A9"
    c.values[f"S{code}01"] = "01" + pipe
    c.values[f"S{length_code}01"] = "01" + struct.pack(">f", length).hex().upper()
    c.line_leak[(kind, 1)] = leak
    return c


def run(console, kind="plld", number=1, step=30.0, limit=900):
    """Let the console's clock run until the line is done testing."""
    ln = console.lines.line(kind, number)
    for _ in range(limit):
        console.clock_offset += step
        console.leaks.tick()
        if not ln.running():
            return ln
    return ln


class ThePipeDecidesTheArithmetic(unittest.TestCase):
    """dV/V = dP/K, and both numbers are published per pipe type."""

    def test_the_table_is_the_application_guide(self):
        self.assertEqual(pressure.PIPE["02"], (50000.0, 0.190))   # 2 in steel
        self.assertEqual(pressure.PIPE["01"], (25000.0, 0.204))   # 2 in glass
        self.assertEqual(pressure.PIPE["03"], (3500.0, 0.092))    # PP1501

    def test_line_volume_is_length_times_gallons_a_foot(self):
        ln = a_line(pipe="02", length=500.0).lines.line("plld", 1)
        self.assertAlmostEqual(ln.volume(), 95.0, places=3)

    def test_a_stiff_line_answers_a_leak_harder_than_a_soft_one(self):
        """Which is why the guide will not certify 0.1 gph past 1100 feet."""
        steel = a_line(pipe="02", length=500.0).lines.line("plld", 1)
        flex = a_line(pipe="06", length=500.0).lines.line("plld", 1)
        self.assertGreater(steel.psi_per_gallon(), flex.psi_per_gallon())

    def test_a_longer_line_of_the_same_pipe_moves_less(self):
        short = a_line(pipe="03", length=200.0).lines.line("plld", 1)
        long_ = a_line(pipe="03", length=1000.0).lines.line("plld", 1)
        self.assertGreater(short.psi_per_gallon(), long_.psi_per_gallon())


class TheGrossTest(unittest.TestCase):
    """"a pump-Off test that immediately follows the end of dispensing"."""

    def test_pon_p1_p2_come_out_where_the_manual_prints_them(self):
        """The samples read 30.2 21.3 20.0: pump pressure, then the relief valve.

        Pon is not a constant: Table 12-1 puts submersibles from 25 to 45 psi
        and no two of them sit at the same head, so this asks for the low
        thirties the sample printouts show rather than one number.
        """
        c = a_line(leak=0.05)
        c.leaks.start("plld", 1, "gross")
        ln = run(c)
        reading = ln.readings["gross"][-1]
        self.assertTrue(25.0 <= reading.pon <= 38.0, f"Pon was {reading.pon}")
        self.assertTrue(pressure.FLOOR < reading.p1 <= pressure.RELIEF_CLOSES,
                        f"P1 was {reading.p1}")
        self.assertLess(reading.p2, reading.p1)

    def test_two_stps_do_not_sit_at_the_same_pressure(self):
        c = a_line(leak=0.0)
        seen = {round(c.lines.pump_psi("plld", n), 3) for n in range(1, 5)}
        self.assertEqual(len(seen), 4)
        for psi in seen:
            self.assertTrue(24.0 <= psi <= 38.0, psi)

    def test_a_programmed_pump_pressure_is_taken_exactly(self):
        c = a_line(leak=0.0)
        c.values["S7B701"] = "01" + struct.pack(">f", 41.0).hex().upper()
        self.assertAlmostEqual(c.lines.pump_psi("plld", 1), 41.0, places=3)

    def test_a_line_below_the_floor_fails(self):
        """"If P2 is less than 12 psi the test fails"."""
        c = a_line(leak=8.0)
        c.leaks.start("plld", 1, "gross")
        run(c)
        self.assertEqual(c.leaks.result("plld", 1, "gross").result,
                         leaktest.FAILED)
        self.assertLess(c.lines.line("plld", 1).readings["gross"][-1].p2,
                        pressure.FLOOR)

    def test_a_low_p1_is_retested_before_it_is_believed(self):
        """"a retest is run to confirm the leak. If it fails yet again".

        The trigger is a PRESSURE, "if P1 is below 12 psi it is assumed there
        is a large leak", so how big a leak trips it depends on how long the
        line has had to fall. The Gross window is deliberately short now (see
        wait_times), so this wants a leak that empties the line inside it: 8
        gph did it when the window was two minutes and does not in thirty
        seconds, which is arithmetic rather than a regression.
        """
        c = a_line(leak=40.0)
        c.leaks.start("plld", 1, "gross")
        ln = run(c)
        self.assertTrue(ln.retried)

    def test_a_leak_under_three_gallons_an_hour_passes_it(self):
        for leak in (0.0, 0.1, 0.5, 1.0):
            c = a_line(leak=leak)
            c.leaks.start("plld", 1, "gross")
            run(c)
            self.assertEqual(c.leaks.result("plld", 1, "gross").result,
                             leaktest.PASSED, f"{leak} gph should pass 3.0")

    def test_it_runs_short_enough_to_sit_and_watch(self):
        """A DELIBERATE departure from "3.0 gph - several minutes".

        That figure is right for a console on a forecourt testing itself at
        four in the morning, and wrong for a panel somebody has just pressed
        ENTER on: a minute of a number that barely moves reads as a hang. The
        window is ten seconds of pump and then twenty to thirty of measuring.
        It is still two distinct readings T1 and T2 apart, and it still runs
        with the pipe's stiffness and volume; it is just scaled to be watched.
        """
        for pipe in ("01", "02", "03"):
            c = a_line(leak=0.02, pipe=pipe)
            c.leaks.start("plld", 1, "gross")
            run(c, step=1.0, limit=3000)
            minutes = c.leaks.result("plld", 1, "gross").hours * 60.0
            self.assertTrue(0.1 <= minutes <= 1.2,
                            f"pipe {pipe}: {minutes:.2f} minutes")


class ThePrecisionTests(unittest.TestCase):
    """"pump-On tests. The main component ... is the leak rate (LR) value"."""

    def test_the_measured_rate_is_the_rate_the_line_is_losing(self):
        """P1, P2 and the window give the leak back, because K/V works both ways.

        What the line is losing is the programmed leak AND the standing seep
        every line has. A transducer cannot tell one from the other -- it
        reads a pressure falling -- so the measurement is of the sum, and the
        sum is what has to come back out.
        """
        for leak in (0.05, 0.15, 0.5):
            c = a_line(leak=leak)
            losing = leak + c.lines.seep_gph("plld", 1)
            c.leaks.start("plld", 1, "periodic")
            run(c)
            got = c.leaks.result("plld", 1, "periodic").rate
            self.assertAlmostEqual(got, losing, places=2,
                                   msg=f"asked {losing}, measured {got}")

    def test_ratio_under_one_passes_and_over_one_fails(self):
        """"Ratio <1 Pass, >1 Fail"."""
        tight = a_line(leak=0.1)
        tight.leaks.start("plld", 1, "periodic")
        ln = run(tight)
        self.assertLess(ln.cycles["periodic"][-1].ratio, 1.0)
        self.assertEqual(tight.leaks.result("plld", 1, "periodic").result,
                         leaktest.PASSED)

        leaky = a_line(leak=0.4)
        leaky.leaks.start("plld", 1, "periodic")
        ln = run(leaky)
        self.assertGreater(ln.cycles["periodic"][-1].ratio, 1.0)
        self.assertEqual(leaky.leaks.result("plld", 1, "periodic").result,
                         leaktest.FAILED)

    def test_the_annual_test_is_the_periodic_one_with_tighter_thresholds(self):
        """0.15 gph passes a 0.2 gph test and fails a 0.1 gph one."""
        c = a_line(leak=0.15)
        c.leaks.start("plld", 1, "annual")
        run(c)
        self.assertEqual(c.leaks.result("plld", 1, "periodic").result,
                         leaktest.PASSED)
        self.assertEqual(c.leaks.result("plld", 1, "annual").result,
                         leaktest.FAILED)

    def test_a_periodic_run_is_a_gross_run_first(self):
        """"A 0.2 gph test is automatically preceded by a 3.0 gph test"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        run(c)
        self.assertIsNotNone(c.leaks.result("plld", 1, "gross"))

    def test_the_mid_test_runs_inside_the_periodic_one(self):
        """"At the end of the second leak rate measurement ... a pump-Off test"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        ln = run(c)
        self.assertEqual(len(ln.readings["mid"]), 1)

    def test_two_leak_rates_are_the_minimum(self):
        """"15 minutes to measure LR1 and another 15 minutes to measure LR2"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        ln = run(c)
        self.assertEqual(len(ln.cycles["periodic"]), 2)

    def test_the_leak_rates_land_fifteen_minutes_apart(self):
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        ln = run(c, step=15.0)
        at = [x.minutes for x in ln.cycles["periodic"]]
        self.assertEqual(at[1] - at[0], 15)

    def test_the_annual_adds_one_more_fifteen_minutes(self):
        """"The minimum test duration for an Annual test is 45 minutes"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "annual")
        ln = run(c, step=15.0)
        periodic = ln.cycles["periodic"][-1].minutes
        self.assertEqual(ln.cycles["annual"][-1].minutes - periodic, 15)


class ThermalInstability(unittest.TestCase):
    """"thermal instability results in longer 0.2 and 0.1 gph test times"."""

    def measure(self, thermal):
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        c.lines.line("plld", 1).thermal = thermal
        return c, run(c)

    def test_a_thermal_slope_lengthens_the_test(self):
        _c, steady = self.measure(0.0)
        _c, moving = self.measure(-20.0)
        self.assertGreater(len(moving.cycles["periodic"]),
                           len(steady.cycles["periodic"]))

    def test_the_rates_converge_until_two_of_them_agree(self):
        """Figure 8: LR1 != LR2 != LR3, then LR3 == LR4 and it is declared."""
        _c, ln = self.measure(-20.0)
        self.assertGreater(len(ln.rates), 2)
        self.assertLessEqual(abs(ln.rates[-1] - ln.rates[-2]),
                             pressure.STABLE["periodic"])
        self.assertGreater(abs(ln.rates[0] - ln.rates[1]),
                           pressure.STABLE["periodic"])

    def test_a_thermal_line_still_passes_because_it_waits_the_thermals_out(self):
        c, _ln = self.measure(-20.0)
        self.assertEqual(c.leaks.result("plld", 1, "periodic").result,
                         leaktest.PASSED)


class Dispensing(unittest.TestCase):
    """"A gross test always follows the completion of a dispense"."""

    def test_putting_the_handle_down_starts_a_gross_test(self):
        c = a_line(leak=0.02)
        c.lines.handle("plld", 1, True)
        self.assertTrue(c.lines.line("plld", 1).pump)
        c.lines.handle("plld", 1, False)
        run(c)
        self.assertIsNotNone(c.leaks.result("plld", 1, "gross"))

    def test_lifting_the_handle_aborts_a_running_test(self):
        """"If a dispense request occurs during any test, the test is aborted"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        c.clock_offset += 120.0
        c.leaks.tick()
        c.lines.handle("plld", 1, True)
        ln = c.lines.line("plld", 1)
        self.assertFalse(ln.running())
        # and the screen says what the line is doing now, not what it stopped
        self.assertEqual(ln.state, "DISPENSING")
        self.assertTrue(ln.pump)


class WhatTheScreensSay(unittest.TestCase):
    """The diagnostic a technician watches while the test runs."""

    def test_the_first_screen_is_a_pressure_and_two_switches(self):
        """"Q 1: XX.XXX PSI PUMP OFF" over "TEST COMPLETE HANDLE OFF"."""
        c = a_line(leak=0.02)
        head, tail = c.diag_value("line_pressure", 1, "plld").split(chr(10))
        self.assertRegex(head, r"^Q 1: +\d+\.\d\d\d PSI PUMP OFF$")
        self.assertEqual(tail, "TEST COMPLETE HANDLE OFF")

    def test_the_pressure_moves_while_the_test_runs(self):
        """A second at a time, because the whole test is thirty of them now."""
        c = a_line(leak=1.0)
        c.leaks.start("plld", 1, "gross")
        for _ in range(12):          # past the ten second pump, into T1
            c.clock_offset += 1.0
            c.leaks.tick()
        first = c.diag_value("line_pressure", 1, "plld")
        c.clock_offset += 1.0
        c.leaks.tick()
        self.assertNotEqual(first, c.diag_value("line_pressure", 1, "plld"))
        self.assertIn("TEST 3.0", first)

    def test_the_pressure_moves_while_the_pump_is_running_too(self):
        """"the STP is pushing fluid into the pipe": a pump that is filling a
        line does not hold one figure, and the screen used to sit on a single
        number for the whole ten seconds, which reads as a hang."""
        c = a_line()
        c.leaks.start("plld", 1, "gross")
        seen = set()
        for _ in range(9):           # inside the pump stage throughout
            c.clock_offset += 1.0
            c.leaks.tick()
            ln = c.lines.line("plld", 1)
            self.assertTrue(ln.pump, "still meant to be pumping")
            seen.add(round(ln.pressure, 3))
        self.assertGreater(len(seen), 1, "the reading should move")
        nominal = max(seen) - min(seen)
        self.assertLess(nominal, 3.0, "but stay recognisably the same pump")

    def test_a_precision_test_reads_pump_on(self):
        """"The precision leak tests ... are pump-On tests"."""
        c = a_line(leak=0.02)
        c.leaks.start("plld", 1, "periodic")
        seen = set()
        for _ in range(200):
            c.clock_offset += 30.0
            c.leaks.tick()
            seen.add(c.diag_value("line_pressure", 1, "plld")
                     .split(chr(10))[0].split("PSI")[1].strip())
            if not c.lines.line("plld", 1).running():
                break
        self.assertIn("PUMP ON", seen)

    def test_the_counts_screen_keeps_sns_between_lo_and_hi(self):
        """"SNS CNTS should always be in between the LO and HI reference counts.
        Also the HI counts should always be less than the LO counts."."""
        c = a_line()
        lo, hi, counts = c.lines.line("plld", 1).sensor_counts()
        self.assertLess(hi, lo)
        self.assertTrue(hi <= counts <= lo)


class ThePrintouts(unittest.TestCase):
    """577013-344 Figures 9 to 12."""

    def a_tested_line(self, leak=0.02):
        c = a_line(leak=leak)
        c.leaks.start("plld", 1, "periodic")
        run(c)
        return c

    def test_the_three_gph_printout_has_its_three_blocks(self):
        out = chr(10).join(printer.line_diag(self.a_tested_line(), "plld", 1,
                                             "gross"))
        self.assertIn("3.0 TEST PASSES", out)
        self.assertIn("3.0 TEST FAILS", out)
        self.assertIn("3.0 HI PRESSURE EVENTS", out)
        self.assertIn("PON  P1         P2", out)

    def test_the_precision_printout_prints_a_ratio_not_a_rate(self):
        out = chr(10).join(printer.line_diag(self.a_tested_line(), "plld", 1,
                                             "periodic"))
        self.assertIn("0.20 TEST DIAG", out)
        self.assertIn("PON RATIO DUR RESULT", out)
        self.assertIn("TOTAL PASSES:", out)
        self.assertIn("NO-VENT TEST ABORTS:", out)

    def test_a_failed_run_says_why(self):
        out = chr(10).join(printer.line_diag(self.a_tested_line(leak=0.5),
                                             "plld", 1, "periodic"))
        self.assertIn("RESULT: FAIL", out)

    def test_the_mid_printout_is_its_own_report(self):
        out = chr(10).join(printer.line_diag(self.a_tested_line(), "plld", 1,
                                             "mid"))
        self.assertIn("MID TEST PASSES", out)


class OverTheWire(unittest.TestCase):
    """Function codes 081 to 084, Start and Stop Pressure Line Leak Test."""

    def ask(self, console, command):
        return Handler(console, verbose=False).handle(
            SOH + command).decode("ascii", "replace")

    def test_it_starts_a_test_and_answers_with_the_status(self):
        c = a_line(leak=0.02)
        answer = self.ask(c, b"S08101149")
        self.assertIn("STATUS: RUNNING PUMP", answer)
        self.assertTrue(c.lines.line("plld", 1).running())

    def test_the_computer_format_is_the_line_and_a_status_code(self):
        """"QQ - sensor number", "tt - Test status", 05 = running pump."""
        c = a_line(leak=0.02)
        self.assertIn("0105", self.ask(c, b"s08101149"))

    def test_it_wants_the_verification_code(self):
        """"149 - This verification code must be sent to confirm the command"."""
        c = a_line(leak=0.02)
        self.assertIn("9999", self.ask(c, b"S08101"))
        self.assertFalse(c.lines.line("plld", 1).running())

    def test_stopping_aborts_it(self):
        c = a_line(leak=0.02)
        self.ask(c, b"S08101149")
        self.assertIn("STATUS: TEST ABORTED", self.ask(c, b"S08201149"))

    def test_a_console_with_no_line_leak_card_says_9999(self):
        c = a_line(leak=0.02)
        c.modules["plld"] = False
        self.assertIn("9999", self.ask(c, b"S08101149"))


class TheWirelessOnesAreTheSameTest(unittest.TestCase):
    """577013-344 documents PLLD and WPLLD together, because they are one test."""

    def test_a_wpll_line_measures_the_same_way(self):
        c = a_line(leak=0.4, kind="wplld")
        c.leaks.start("wplld", 1, "periodic")
        run(c, kind="wplld")
        got = c.leaks.result("wplld", 1, "periodic")
        self.assertEqual(got.result, leaktest.FAILED)
        self.assertAlmostEqual(got.rate, 0.4 + c.lines.seep_gph("wplld", 1),
                               places=2)

    def test_its_screens_are_headed_w(self):
        c = a_line(kind="wplld")
        self.assertTrue(c.diag_value("line_pressure", 1, "wplld")
                        .startswith("W 1:"))


class WhatALineReadsStandingStill(unittest.TestCase):
    """The first PLLD diagnostic screen, and what is on it when nothing runs."""

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c

    def head(self, c, number, kind="plld"):
        return c.diag_value("line_pressure", number, kind).split(chr(10))[0]

    def test_two_lines_do_not_read_the_same_pressure(self):
        """Three identical readings tell a technician nothing, and every line
        used to sit at 21.000 because that is one constant minus another."""
        c = self.a_site()
        seen = [c.lines.line("plld", n).pressure
                for n, in [(n,) for _k, n, _l in c.programmed_lines()]]
        self.assertGreater(len(set(round(p, 3) for p in seen)), 1, seen)
        for psi in seen:
            self.assertTrue(pressure.FLOOR < psi < pressure.RELIEF_CLOSES, psi)

    def test_the_switch_states_sit_hard_against_the_right_edge(self):
        """577013-344 draws this screen as twenty-four characters with the
        state at the end, and its WPLLD example is the proof:

            W 1: PENDING    PUMP OFF

        "PENDING" is seven characters and four spaces follow it, which is the
        one number that puts PUMP OFF at column 24. So the state is anchored
        right and a shorter status opens the gap rather than dragging it left.
        """
        c = self.a_site()
        ln = c.lines.line("plld", 1)
        for state in ("TEST COMPLETE", "TEST 3.0", "RUNNING PUMP",
                      "PRESSURE CHECK", "TEST PENDING"):
            ln.state = state
            head, tail = ln.screen()
            self.assertEqual(len(head), pressure.SCREEN, repr(head))
            self.assertEqual(len(tail), pressure.SCREEN, repr(tail))
            self.assertTrue(head.endswith("PUMP OFF"), repr(head))
            self.assertTrue(tail.endswith("HANDLE OFF"), repr(tail))
            # the status is what gives way when the two will not both fit
            self.assertTrue(state.startswith(tail.split("HANDLE")[0].strip()),
                            repr(tail))

    def test_it_matches_the_manuals_own_line_exactly(self):
        """The one screen the manual prints with a real status on it."""
        c = self.a_site()
        ln = c.lines.line("plld", 1)
        ln.state = "TEST COMPLETE"
        self.assertEqual(ln.screen()[1], "TEST COMPLETE HANDLE OFF")

    def test_an_unprogrammed_position_reads_no_pressure(self):
        """"Four unprogrammed PLLD positions are four pieces of pipe nobody
        has told the console about"."""
        c = self.a_site()
        c.modules["plld"] = 1
        spare = [n for n in range(1, c.capacity("plld") + 1)
                 if not any(k == "plld" and m == n
                            for k, m, _l in c.programmed_lines())]
        self.assertTrue(spare, "the preset leaves no spare position to check")
        got = self.head(c, spare[0])
        self.assertIn("PSI", got)
        self.assertNotRegex(got, r"\d+\.\d+ PSI")

    def test_a_programmed_position_does_read_one(self):
        c = self.a_site()
        self.assertRegex(self.head(c, 1), r"\d+\.\d+ PSI")

    def test_it_drops_slowly_and_settles_instead_of_emptying(self):
        """"Drop very slowly over time" -- and stop, because a line that ran
        to nothing would be unreadable by morning."""
        c = self.a_site()
        ln = c.lines.line("plld", 1)
        start = ln.pressure
        for _ in range(400):                       # a bit over three days
            c.clock_offset += 600.0
            c.leaks.tick()
        self.assertLess(ln.pressure, start, "it should have dropped")
        self.assertGreater(ln.pressure, pressure.FLOOR,
                           "a line nobody touched must still be testable")

    def test_the_seep_can_never_fail_the_tightest_test(self):
        """It is written as a fraction of the annual threshold precisely so
        that this holds on every line, whatever its pipe does to the psi."""
        c = self.a_site()
        for kind, number, _label in c.programmed_lines():
            self.assertLess(c.lines.seep_gph(kind, number),
                            pressure.THRESHOLD["annual"])
            ln = c.lines.line(kind, number)
            c.leaks.start(kind, number, "annual")
            for _ in range(3000):
                c.clock_offset += 30.0
                c.leaks.tick()
                if not ln.running():
                    break
            self.assertEqual(c.leaks.result(kind, number, "annual").result,
                             leaktest.PASSED, f"{kind} {number}")


class EnterRunsTheGrossTest(unittest.TestCase):
    """"PRESS <ENTER>" on the pressure screen, which is where a technician
    watching that screen would reach for it."""

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c

    def test_enter_starts_a_3_0_test_on_the_line_being_shown(self):
        c = self.a_site()
        self.assertEqual(c.start_line_test("plld", 2), "TEST STARTED")
        ln = c.lines.line("plld", 2)
        self.assertTrue(ln.running())
        self.assertEqual(ln.rate_key, "gross")

    def test_the_screen_says_test_3_0_while_it_runs(self):
        """The point of starting it from that screen is watching that screen."""
        c = self.a_site()
        c.start_line_test("plld", 1)
        ln = c.lines.line("plld", 1)
        said = set()
        pressures = []
        for _ in range(60):
            c.clock_offset += 12.0
            c.leaks.tick()
            head, tail = c.diag_value("line_pressure", 1, "plld").split(chr(10))
            said.add(tail.split("HANDLE")[0].strip())
            pressures.append(float(head.split(":")[1].split("PSI")[0]))
            if not ln.running():
                break
        self.assertIn("TEST 3.0", said)
        self.assertGreater(len(set(pressures)), 1,
                           "the pressure should move while the test runs")

    def test_an_unprogrammed_line_has_nothing_to_test(self):
        c = self.a_site()
        c.modules["plld"] = 1
        spare = [n for n in range(1, c.capacity("plld") + 1)
                 if not any(k == "plld" and m == n
                            for k, m, _l in c.programmed_lines())][0]
        self.assertEqual(c.start_line_test("plld", spare),
                         "LINE NOT PROGRAMMED")
        self.assertFalse(c.lines.line("plld", spare).running())

    def test_pressing_it_again_starts_it_again(self):
        """There is no "already running" to argue with. The key means run the
        test, and somebody who presses it twice wants the second one."""
        c = self.a_site()
        c.start_line_test("plld", 1)
        ln = c.lines.line("plld", 1)
        for _ in range(8):                      # get it well into the run
            c.clock_offset += 1.0
            c.leaks.tick()
        self.assertEqual(c.start_line_test("plld", 1), "TEST STARTED")
        self.assertTrue(ln.running())
        self.assertEqual(ln.rate_key, "gross")
        self.assertEqual(ln.state, "RUNNING PUMP", "it starts from the top")


if __name__ == "__main__":
    unittest.main()
