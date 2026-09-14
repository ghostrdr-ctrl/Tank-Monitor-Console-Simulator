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
import time
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


class UserDefinedPipe(unittest.TestCase):
    """FIDELITY R8: the USER DEFINED pipe type exists so a site can enter its
    own bulk modulus, and `pipe()` read it out of `78B` -- which is Set
    Pressure Line Leak 0.10 GPH Test SCHEDULE. The modulus is `779`, which
    has a field and a setup step that writes it, so the panel stored the
    number where the manual says it goes and the model looked for it in a
    schedule. A site that programmed its own pipe got the default pipe's
    behaviour, silently.
    """

    def a_user_defined_line(self, modulus):
        c = a_line(pipe="18")
        c.values["S77901"] = "01" + struct.pack(">f", modulus).hex().upper()
        return c.lines.line("plld", 1)

    def test_the_modulus_comes_off_779(self):
        self.assertEqual(self.a_user_defined_line(3200.0).pipe()[0], 3200.0)

    def test_it_is_not_the_default_pipes(self):
        """3500.0 is Enviroflex PP1501, the default, and was what every
        user-defined line silently got."""
        self.assertNotEqual(self.a_user_defined_line(3200.0).pipe()[0],
                            pressure.PIPE[pressure.DEFAULT_PIPE][0])

    def test_the_number_reaches_the_arithmetic(self):
        """A stiffer user-defined pipe answers a leak harder, which is the
        whole reason the console asks for the modulus."""
        stiff = self.a_user_defined_line(40000.0)
        soft = self.a_user_defined_line(2000.0)
        self.assertGreater(stiff.psi_per_gallon(), soft.psi_per_gallon())

    def test_a_schedule_in_78b_does_not_become_a_modulus(self):
        """The code this used to read. Programming a 0.10 GPH schedule must
        not change the pipe."""
        c = a_line(pipe="18")
        c.values["S77901"] = "01" + struct.pack(">f", 3200.0).hex().upper()
        c.values["S78B01"] = "0112609020600"
        self.assertEqual(c.lines.line("plld", 1).pipe()[0], 3200.0)


class TheWaitTimesFollowThePipe(unittest.TestCase):
    """FIDELITY R8's other half. 577013-344 makes mis-programming the pipe
    type a troubleshooting procedure precisely because the waits should
    differ -- "in the case where a stiff line (steel or fiberglass) is
    programmed as a flex line the wait time will be excessively long ...
    when a soft flex line is programmed as a steel or fiberglass line the
    wait times will be too short".

    The precision leg used to return `min(60*scale, 180), min(150*scale,
    480)`, two invented ceilings that flattened the table: at 1000 feet
    sixteen of the eighteen pipe types came out identical, and the cap bound
    HARDER the longer the line got, which is backwards. The budget is the
    manual's own instead -- "15 minutes to measure LR1 and another 15
    minutes to measure LR2" -- and the pair is shrunk in proportion only
    when it will not fit, the same shape the gross leg already used.
    """

    def spread(self, length):
        """How many pipe types share the commonest wait pair."""
        groups = {}
        for key in sorted(pressure.PIPE):
            ln = a_line(pipe=key, length=length).lines.line("plld", 1)
            pair = tuple(round(x, 2) for x in ln.wait_times("periodic"))
            groups.setdefault(pair, []).append(key)
        return max(len(v) for v in groups.values())

    def test_a_soft_line_waits_longer_than_a_stiff_one(self):
        steel = a_line(pipe="02", length=400.0).lines.line("plld", 1)
        flex = a_line(pipe="06", length=400.0).lines.line("plld", 1)
        self.assertGreater(sum(flex.wait_times("periodic")),
                           sum(steel.wait_times("periodic")))

    def test_the_leg_fits_the_cycle_the_manual_gives_it(self):
        for key in sorted(pressure.PIPE):
            for length in (100.0, 500.0, 1000.0):
                ln = a_line(pipe=key, length=length).lines.line("plld", 1)
                self.assertLessEqual(sum(ln.wait_times("periodic")),
                                     pressure.CYCLE, f"{key} at {length}")

    # A RATCHET, not a target. The flattening is reduced and not gone: the
    # truncation still binds above a scale of four, which is over half the
    # table on a long line. These are the numbers as they stand, and the
    # figures the old ceilings gave are in the comment beside each -- so
    # improving the curve shows up here, and regressing it fails.
    FLATTEST = {100.0: 6,      # was 10 of 18
                200.0: 7,      # was 10
                400.0: 10,     # was 13
                1000.0: 14}    # was 16

    def test_the_table_is_no_flatter_than_it_was(self):
        for length, most in self.FLATTEST.items():
            self.assertLessEqual(self.spread(length), most, f"{length} ft")


class TheFourFaultsTheQuickHelpStates(unittest.TestCase):
    """FIDELITY N2. 577013-727 Rev B states a trigger for each of these in
    one sentence, this console carried the alarm NAMES for all four, and
    nothing posted any of them. Every state they turn on was already
    modelled -- Console.latches() even special-cased FUEL OUT to stop it
    latching, guarding a branch nothing could reach.
    """

    def live(self, c):
        return [a for a in c.conditions() if a[:2] in ("21", "26")]

    def test_a_negative_reading_is_an_open_transducer(self):
        """p.11: "when the pressure transducer is not connected to the PLLD
        Interface Module the pressure reading is negative"."""
        c = a_line()
        c.lines.line("plld", 1).pressure = -3.0
        self.assertIn("210601", self.live(c))

    def test_a_healthy_line_is_not_open(self):
        self.assertNotIn("210601", self.live(a_line()))

    def test_pump_on_equal_to_pump_off_in_band_is_a_short(self):
        """p.12: "when the pump-On and pump-Off pressures are reading the
        same value and are within the range of 5 to 15 psi"."""
        c = a_line()
        c.lines.line("plld", 1).readings["gross"].append(
            pressure.Reading(0.0, 9.0, 9.0, 9.0, True))
        self.assertIn("211501", self.live(c))

    def test_the_same_two_readings_outside_the_band_are_not(self):
        """A line resting at its pump pressure reads the same twice and is
        working perfectly. The band is what makes it a fault."""
        c = a_line()
        c.lines.line("plld", 1).readings["gross"].append(
            pressure.Reading(0.0, 30.0, 30.0, 30.0, True))
        self.assertNotIn("211501", self.live(c))

    def a_dry_line(self, volume=50.0, gross_failed=True):
        c = a_line()
        c.values["S78501"] = "0101"          # this line feeds tank 1
        c.lines.line("plld", 1).result["gross"] = not gross_failed
        c.tank_level[1] = {"volume": volume, "water": 0.0}
        return c

    def test_fuel_out_wants_both_halves(self):
        """p.9: "This alarm occurs when the fuel level is below 10 inches
        AND a gross line test has failed"."""
        c = self.a_dry_line()
        self.assertLess(c.height_at(1, 50.0), pressure.FUEL_OUT_INCHES)
        self.assertIn("211701", self.live(c))

    def test_it_clears_when_the_level_comes_back(self):
        """"The alarm will clear when the fuel level exceeds 10 inches" --
        which is why `latches()` exempts it."""
        c = self.a_dry_line()
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        self.assertNotIn("211701", self.live(c))
        self.assertFalse(c.latches("211701"))

    def test_a_low_tank_alone_is_not_fuel_out(self):
        c = self.a_dry_line()
        c.lines.line("plld", 1).result["gross"] = True
        self.assertNotIn("211701", self.live(c))

    def test_the_tank_number_is_read_past_its_device_prefix(self):
        """S785 stores `0101`, the prefix and then the tank. `limit()` only
        strips a prefix on values longer than eight characters, so it read
        that as one hundred and one and the alarm could never find a tank."""
        c = self.a_dry_line()
        self.assertEqual(c.text("785", 1).strip(), "01")

    def test_a_handle_up_for_a_shift_is_a_continuous_handle(self):
        """p.8: "A continuous pump-in signal will activate ... (Version 19
        and higher) A Continuous Handle alarm"."""
        c = a_line()
        c.lines.handle("plld", 1, True)
        self.assertNotIn("211601", self.live(c))
        c.clock_offset += pressure.HANDLE_ALARM_HOURS * 3600.0 + 60.0
        self.assertIn("211601", self.live(c))

    def test_it_is_sixteen_hours_and_not_the_pre_19_warning_s_eight(self):
        """577013-344 Rev H p.19 states the v19 alarm's own delay: "A
        continuous Pump-in signal will activate a Continuous Handle alarm
        after 16 hours."

        This console counted eight, borrowed from the PRE-19 warning on the
        reasoning that the v19 alarm had no stated delay -- so it posted an
        ALARM at the hour a console it cannot be would have posted a
        WARNING. Eight hours has to be quiet."""
        self.assertEqual(pressure.HANDLE_ALARM_HOURS, 16.0)
        c = a_line()
        c.lines.handle("plld", 1, True)
        c.clock_offset += 8.0 * 3600.0 + 60.0
        self.assertNotIn("211601", self.live(c))

    def test_the_timeout_is_programmable_per_line_at_774(self):
        """"Set Pressure Line Leak Continuous Handle Alarm Timeout", 774:
        "tt - Continuous Handle Alarm Timeout (Decimal, in hours, 1-16)".
        The field has been on this console the whole time and the alarm
        counted a literal instead. Programmed over the wire, because that
        is the only way a real console is programmed."""
        c = a_line()
        h = Handler(c, verbose=False)
        self.assertNotIn(b"9999", h.handle(SOH + b"S77401" + b"04" + b"\r"))
        c.lines.handle("plld", 1, True)
        c.clock_offset += 3.0 * 3600.0 + 60.0
        self.assertNotIn("211601", self.live(c))
        c.clock_offset += 3600.0
        self.assertIn("211601", self.live(c))

    def test_wplld_counts_its_own_7ae(self):
        """The WPLLD twin, same words and same range. Its field did not
        exist at all, so the timeout could be sent over the wire and land
        nowhere."""
        c = a_line(kind="wplld")
        h = Handler(c, verbose=False)
        self.assertNotIn(b"9999", h.handle(SOH + b"S7AE01" + b"02" + b"\r"))
        c.lines.handle("wplld", 1, True)
        c.clock_offset += 3600.0 + 60.0
        self.assertNotIn("261601", self.live(c))
        c.clock_offset += 3600.0
        self.assertIn("261601", self.live(c))

    def test_the_two_fields_hold_the_manual_s_range(self):
        """"1-16", on both codes. The PLLD field said 0 to 99."""
        from tls350sim.console import FIELDS
        for key in ("S77401", "S7AE01"):
            self.assertEqual((FIELDS[key]["min"], FIELDS[key]["max"]),
                             (1, 16), key)

    def test_putting_the_handle_back_clears_it(self):
        c = a_line()
        c.lines.handle("plld", 1, True)
        c.clock_offset += pressure.HANDLE_ALARM_HOURS * 3600.0 + 60.0
        c.lines.handle("plld", 1, False)
        self.assertNotIn("211601", self.live(c))
        self.assertIsNone(c.lines.line("plld", 1).handle_since)

    def test_the_pre_19_pair_cannot_arise_on_this_console(self):
        """The manual's other half -- a warning at 8 hours and an alarm at
        16, "(Pre 19)" -- needs a console older than PLLD itself, which the
        version tables put at 24. So 21/10 and 26/09 stay unproducible, and
        the code carries no branch it can never take."""
        from tls350sim import versions
        for kind in ("plld", "wplld"):
            arrives = next(v for v in range(1, 60)
                           if versions.supports(v, "E7", kind))
            self.assertGreater(arrives, 19, kind)

    def a_low_pressure_line(self, threshold="15", enabled=True):
        c = a_line()
        c.values["S77C01"] = "01" + ("1" if enabled else "0")
        c.values["S78F01"] = "01" + threshold.rjust(2, "0")
        return c

    def test_a_dispense_below_the_threshold_is_a_low_pressure_alarm(self):
        """576013-623 Rev AN p.10-6: "The Low Pressure Alarm Shutoff detects
        low pressure during a dispense ... When the pressure drops below the
        entered shutoff value, the pump shuts off"."""
        c = self.a_low_pressure_line()
        c.lines.handle("plld", 1, True)
        self.assertNotIn("211401", self.live(c))
        c.lines.line("plld", 1).pressure = 9.0
        self.assertIn("211401", self.live(c))

    def test_it_is_only_measured_during_a_dispense(self):
        c = self.a_low_pressure_line()
        c.lines.line("plld", 1).pressure = 9.0
        self.assertNotIn("211401", self.live(c))

    def test_a_threshold_of_zero_disables_it(self):
        """"A value of 0 will disable this alarm"."""
        c = self.a_low_pressure_line(threshold="0")
        c.lines.handle("plld", 1, True)
        c.lines.line("plld", 1).pressure = 1.0
        self.assertNotIn("211401", self.live(c))

    def test_the_flag_gates_the_value(self):
        """Two screens: `LOW PRESSURE SHUTOFF: NO` and `LOW PRESSURE: 5`.
        The console had the first and not the second, so the number the
        alarm is measured against could not be programmed at all."""
        c = self.a_low_pressure_line(enabled=False)
        c.lines.handle("plld", 1, True)
        c.lines.line("plld", 1).pressure = 9.0
        self.assertNotIn("211401", self.live(c))

    def test_the_value_has_a_panel_screen_now(self):
        from tls350sim.console import FIELDS, SETUP_MENU
        self.assertIn("S78F01", FIELDS)
        drawn = [st for fn in SETUP_MENU for st in fn.get("steps", [])
                 if st.get("code") == "S78F01"]
        self.assertEqual(len(drawn), 1)
        self.assertEqual(drawn[0]["l2"], "LOW PRESSURE:")
        # "programmable values from 0 - 25 psi"
        self.assertEqual(FIELDS["S78F01"]["min"], 0)
        self.assertEqual(FIELDS["S78F01"]["max"], 25)

    def test_wpll_has_no_low_pressure_alarm(self):
        """Category 26's 14 is High Pressure, and the shutoff's own codes
        are PLLD's alone."""
        self.assertNotIn("low", pressure.LINE_ALARMS["wplld"])

    def test_an_unprogrammed_position_raises_nothing(self):
        """A card in the cage is wires, not lines."""
        c = a_line()
        c.values["S78101"] = ""
        c.values["S78201"] = ""
        c.lines.line("plld", 1).pressure = -3.0
        self.assertEqual(self.live(c), [])


class TheLineResultsReport(unittest.TestCase):
    """FIDELITY W14. Function 373 and 576013-610 Rev AC p.11-1 draw the same
    report and it is a block per RATE, not a table: this console printed
    function 208's TEST TYPE / RESULT / RATE / HOURS / VOLUME columns, which
    is the tank report's mistake in a second place.
    """

    def a_tested_line(self, passes=3):
        c = a_line()
        for _ in range(passes):
            c.clock_offset += 3600.0
            c.leaks.start("plld", 1, "gross")
            for _ in range(200):
                c.clock_offset += 30.0
                c.leaks.tick()
        return c

    def lines(self, c):
        return [str(x) for x in printer.fit(printer.leak_tests(c, "plld", 1))]

    def test_a_block_per_rate_and_not_a_table(self):
        out = self.lines(self.a_tested_line())
        self.assertIn(" 3.0 GAL/HR RESULTS:", out)
        self.assertIn("0.20 GAL/HR RESULTS:", out)
        self.assertIn("0.10 GAL/HR RESULTS:", out)
        self.assertFalse([x for x in out if "TEST TYPE" in x], out)

    def test_the_counters_the_manual_asks_for(self):
        """"the number of 3.0 gph tests run in the previous 24 hours and
        since midnight of the current day"."""
        out = self.lines(self.a_tested_line())
        self.assertIn("LAST TEST:", out)
        self.assertIn("NUMBER OF TESTS PASSED", out)
        prev = [x for x in out if x.startswith("PREV 24 HOURS")]
        since = [x for x in out if x.startswith("SINCE MIDNIGHT")]
        self.assertTrue(prev and since, out)
        self.assertGreater(int(prev[0].split(":")[1]), 0)

    def test_the_counters_line_up(self):
        """The manual sets them one under the other with their numbers in a
        column: `PREV 24 HOURS :   149` over `SINCE MIDNIGHT :    76`."""
        out = self.lines(self.a_tested_line())
        prev = [x for x in out if x.startswith("PREV 24 HOURS")][0]
        since = [x for x in out if x.startswith("SINCE MIDNIGHT")][0]
        self.assertEqual(len(prev), len(since))

    def test_a_verdict_reads_the_way_the_report_prints_it(self):
        """Function 373's sample says PASS where the SCREEN says PASSED."""
        out = self.lines(self.a_tested_line())
        self.assertIn("PASS", out)
        self.assertFalse([x for x in out if x.strip() == "PASSED"], out)

    def test_a_line_with_no_tests_says_so_under_each_rate(self):
        out = self.lines(a_line())
        self.assertGreaterEqual(
            len([x for x in out if x == "NO TEST DATA AVAILABLE"]), 1)


class TheLineEquipmentFaultAlarm(unittest.TestCase):
    """FIDELITY N2a. 577013-727 Rev B Appendix A Table I gives the High
    Pressure warning and alarm a threshold per software version and ends
    "19: Not Applicable" -- read off p.15 by word position, because the
    table sets its version labels above their own limit columns and every
    text extraction interleaves them:

        version 15        40 psi warning   50 psi alarm & shutdown
        version 16 A & B  40 psi warning   50 psi alarm & shutdown
        version 16 C      29 psi warning   30 psi alarm & shutdown
        version 17 & 18   30 psi warning   40 psi alarm & shutdown
        version 19        Not Applicable

    From version 19 the Line Equipment Fault alarm replaces the pair, and
    the manuals' contents page says so in as many words. This console cannot
    be older than that with a pressurised line on it -- PLLD and WPLLD both
    arrive at version 24 in the version tables -- so the High Pressure pair
    is unreachable here for the same reason the pre-19 Continuous Pump On
    pair is, and the alarm to produce is this one.

    577013-344 Rev H pp.13-14 states it as two monitors, and this console
    already had both quantities.
    """

    def a_dispensed_line(self, pd, ref):
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.pd_ref = ref
        ln.handle = True
        ln.handle_since = time.mktime(c.now())
        ln.pressure = pd
        c.lines.handle("plld", 1, False)         # the handle goes down
        return c, ln

    def live(self, c):
        return [a for a in c.conditions() if a[:2] in ("21", "26")]

    def test_the_dispensing_monitor_wants_five_psi_for_a_month(self):
        """"If the current Pd value exceeds the Pd_ref value by 5 psi for a
        continuous period of one month, the Line Equipment Fault alarm will
        be posted"."""
        c, ln = self.a_dispensed_line(pd=30.0, ref=20.0)
        self.assertIsNotNone(ln.pd_high_since)
        self.assertNotIn("211801", self.live(c))
        c.clock_offset += 29 * 24 * 3600.0
        self.assertNotIn("211801", self.live(c))
        c.clock_offset += 2 * 24 * 3600.0
        self.assertIn("211801", self.live(c))

    def test_four_psi_over_is_not_five(self):
        c, ln = self.a_dispensed_line(pd=24.0, ref=20.0)
        self.assertIsNone(ln.pd_high_since)
        c.clock_offset += 60 * 24 * 3600.0
        self.assertNotIn("211801", self.live(c))

    def test_continuous_means_the_clock_starts_again(self):
        """A dispense that comes back inside the band is not a fault, and
        the month is not carried over from the last one."""
        c, ln = self.a_dispensed_line(pd=30.0, ref=20.0)
        c.clock_offset += 20 * 24 * 3600.0
        ln.handle, ln.handle_since = True, time.mktime(c.now())
        ln.pressure = 22.0
        c.lines.handle("plld", 1, False)
        self.assertIsNone(ln.pd_high_since)
        c.clock_offset += 20 * 24 * 3600.0
        self.assertNotIn("211801", self.live(c))

    def test_the_vent_monitor_wants_both_of_its_numbers(self):
        """"If Pv > 40 psi and Pd > 50 psi -> Line Equipment Fault alarm is
        posted", after a PASSING gross test. "The inclusion of the Pd value
        in the formula prevents the alarm from firing when the reason for
        the high Pv value is a restricted relief path"."""
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.pd = 60.0
        c.lines._finish_gross(ln, time.mktime(c.now()), 45.0, 45.0, True, 60.0)
        self.assertTrue(ln.vent_fault)
        self.assertIn("211801", self.live(c))

    def test_a_high_vent_pressure_alone_is_a_restricted_relief_path(self):
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.pd = 30.0
        c.lines._finish_gross(ln, time.mktime(c.now()), 45.0, 45.0, True, 60.0)
        self.assertFalse(ln.vent_fault)
        self.assertNotIn("211801", self.live(c))

    def test_it_only_runs_after_a_test_that_passed(self):
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.pd = 60.0
        c.lines._finish_gross(ln, time.mktime(c.now()), 45.0, 45.0, False,
                              60.0)
        self.assertFalse(ln.vent_fault)

    def test_the_offset_reset_starts_both_monitors_again(self):
        """"A manual reset should be performed after a transducer or pump
        has been replaced"."""
        c, ln = self.a_dispensed_line(pd=30.0, ref=20.0)
        ln.vent_fault = True
        ln.reset_offset()
        self.assertIsNone(ln.pd_high_since)
        self.assertFalse(ln.vent_fault)
        self.assertNotIn("211801", self.live(c))

    def test_the_wireless_line_has_its_own_number_for_it(self):
        c = a_line(kind="wplld")
        ln = c.lines.line("wplld", 1)
        ln.pd = 60.0
        c.lines._finish_gross(ln, time.mktime(c.now()), 45.0, 45.0, True, 60.0)
        self.assertIn("261801", self.live(c))


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
        """FIDELITY R17. The code read here was `7B7`, which is not a
        function code -- no field, no census entry, nowhere in 576013-635 --
        so nothing could ever store it and this branch was unreachable on
        every console. The test passed anyway by writing `S7B701` into
        `values` itself, which is the shape worth remembering: a test that
        pokes the store proves the READER and says nothing about whether
        anything can ever fill it."""
        c = a_line(leak=0.0)
        c.values["S77601"] = "01" + struct.pack(">f", 41.0).hex().upper()
        self.assertAlmostEqual(c.lines.pump_psi("plld", 1), 41.0, places=3)

    def test_the_reference_pressure_arrives_over_the_wire(self):
        """The half the entry above could not reach. 576013-635 Rev AA
        p.21239 enters 776 as `ppp.pp`, so a Set in display format has to be
        accepted, stored and read back as the same number."""
        from tls350sim.wire import Handler
        c = a_line(leak=0.0)
        h = Handler(c, verbose=False)
        reply = h.handle(b"\x01S77601041.00\r")
        self.assertNotIn(b"9999", reply)
        self.assertAlmostEqual(c.limit("776", 1), 41.0, places=3)
        self.assertAlmostEqual(c.lines.pump_psi("plld", 1), 41.0, places=3)
        # and the offset monitor's Pd Ref is the same quantity, so it reads
        # the same programmed number rather than generating its own
        self.assertAlmostEqual(c.lines.nominal_psi("plld", 1), 41.0, places=3)
        # WPLLD has no profile line test and no code for one
        self.assertIsNone(c.lines.programmed_psi("wplld", 1))

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

    def test_a_test_running_on_a_shut_down_line_is_visible(self):
        """Reported from the bench: "after pressing enter I should see it
        running the test, not DISABLE ALARM."

        `DISABLE ALARM` is the right word -- 577013-344 Rev H p.22 lists it
        among the diag screen's statuses, "one of the PLLD pump disable
        alarms is active", and 576013-635 numbers it `07` in the same
        enumeration `05` running pump belongs to. What was wrong was that
        it OUTRANKED the others: `status` asked `disabled` first and
        returned unconditionally, so no test on a shut-down line could ever
        be seen.

        And that is the one line a technician has to watch, because running
        a test on it is the documented way to get it back. 576013-623 Rev
        AN p.5-10: "This feature lets you choose how to re-enable a line
        shut down by a failing line leak test. To re-enable a shutdown line
        only by a passed line test, press STEP."
        """
        c = a_line()
        c.leaks.disabled.add(("plld", 1))
        ln = c.lines.line("plld", 1)
        self.assertEqual(ln.screen()[1], "DISABLE ALARM HANDLE OFF")
        c.lines.start("plld", 1, "gross")
        self.assertTrue(ln.running())
        self.assertEqual(ln.screen()[1], "RUNNING PUMP  HANDLE OFF")
        self.assertTrue(ln.pump)
        # and when the test is over the shutdown is still standing
        c.lines.stop("plld", 1)
        self.assertEqual(ln.screen()[1], "DISABLE ALARM HANDLE OFF")

    def test_a_wplld_line_says_the_word_its_own_figure_lists(self):
        """Figure 19 is PLLD's list of statuses and Figure 20 is WPLLD's,
        and they are not the same list: p.26 has no RUNNING PUMP and no
        PRESSURE CHECK on it. 576013-610 Rev AC draws the two start
        confirmations differently for the same reason -- `Q #: RUNNING
        PUMP` on p.11-4 and `W #: TEST PENDING` on p.12-4.

        The engine keeps one vocabulary and the glass translates, so the
        wire is untouched: `wirelines` maps both PLLD-only words onto
        WPLLD's `02` with a note that there is no code for them.
        """
        c = a_line(kind="wplld")
        c.leaks.start("wplld", 1, "gross")
        ln = c.lines.line("wplld", 1)
        self.assertEqual(ln.state, "RUNNING PUMP")
        self.assertEqual(ln.status(), "RUNNING PUMP")
        self.assertEqual(ln.shown_status(), "TEST PENDING")
        self.assertIn("TEST PENDING", ln.screen()[1])

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
        # The console's clock is `time.time() + clock_offset`, so REAL
        # elapsed time counts too: nine simulated seconds against a ten
        # second PUMP_TRAIL left one second of headroom, and on a loaded
        # machine the loop itself spent it. That is a race, not a reading --
        # it failed about once in three thousand runs, only ever inside a
        # full suite. So it samples while the pump is actually on and
        # requires enough samples to mean something.
        for _ in range(9):
            c.clock_offset += 1.0
            c.leaks.tick()
            ln = c.lines.line("plld", 1)
            if not ln.pump:
                break
            seen.add(round(ln.pressure, 3))
        self.assertGreaterEqual(len(seen), 5, "the pump stage was not sampled")
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
