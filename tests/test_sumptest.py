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
"""The Mag Sump Leak Test, 576013-610 Rev AC chapters 23 and 24.

099, 09A and 09B used to put a sensor into a phase that nothing ever took it
out of, the screens drew numbers fixed per sensor, and the history printed
its heading over `NO TEST PASSED` whatever had been run. FIDELITY U1b.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import printer, sumpreports, sumptest
from tls350sim.console import Console
from tls350sim.wire import Handler

START = time.mktime((2026, 3, 29, 9, 43, 0, 0, 1, -1))


def a_mag_site(water=12.0, temp=70.0):
    """One Mag sump sensor, a sump with water in it, and a clock to turn."""
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("smart", 1)
    c.values["S72301"] = "0103"                 # smart sensor category 03
    c.values["S72201"] = "01SUMP 1".ljust(22)
    clock = {"t": START}
    c.now = lambda: time.localtime(clock["t"])
    c.sumps.pour(1, water)
    c.sumps.set_temperature(1, temp)
    return c, clock


def advance(c, clock, seconds, step=None):
    """Move the console's clock, in one tick or in several."""
    step = step or seconds
    left = seconds
    while left > 0:
        clock["t"] += min(step, left)
        left -= step
        c.sumps.tick()


def measuring(c, clock, fill=600):
    """START, ten minutes of filling, START MEASURING HEIGHT."""
    c.sumps.start(1)
    advance(c, clock, fill)
    c.sumps.measure(1)
    return c.sumps.tests[1]


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


class ThePass(unittest.TestCase):
    """"If after 2 or more hours the leak rate is less than 0.0104 inches
    (0.264 mm) per hour ... then the test automatically passes." "A leak
    test will either pass or be aborted, it will not fail." """

    def test_a_sump_that_holds_passes_at_two_hours(self):
        c, clock = a_mag_site()
        test = measuring(c, clock)
        advance(c, clock, 7199, step=600)
        self.assertEqual(c.sumps.status(1), sumptest.MEASURING)
        advance(c, clock, 1)
        self.assertEqual(c.sumps.status(1), sumptest.PASSED)
        self.assertEqual(test.end_at - test.start_at, 7200)
        self.assertEqual(c.sumps.values(test)[4], 120.0)
        self.assertIs(c.sumps.last_passed(1), test)

    def test_one_tick_of_a_day_reaches_the_same_answer(self):
        """A reading at any past moment is exact, so a bench running fast
        gets the pass at the minute a slow one does."""
        c, clock = a_mag_site()
        test = measuring(c, clock)
        advance(c, clock, 24 * 3600)
        self.assertEqual(test.status, sumptest.PASSED)
        self.assertEqual(test.end_at - test.start_at, 7200)

    def test_a_rate_over_the_threshold_that_never_drops_a_quarter_passes_at_24(self):
        """"If after 24 hours the water height has not dropped 0.25 inches
        or more, then the test will pass even if the calculated leak rate is
        greater than or equal to the leak rate threshold." 0.01041 in/h is
        over 0.0104 and leaves 0.2498 in a day."""
        c, clock = a_mag_site()
        c.sumps.set_leak(1, 0.01041)
        test = measuring(c, clock)
        advance(c, clock, 23 * 3600, step=1800)
        self.assertEqual(test.status, sumptest.MEASURING)
        self.assertGreaterEqual(test.rate, sumptest.THRESHOLD)
        advance(c, clock, 3600, step=600)
        self.assertEqual(test.status, sumptest.PASSED)
        self.assertEqual(test.end_at - test.start_at, 24 * 3600)

    def test_a_pass_waits_for_the_temperature(self):
        """"Once the temperature is considered stable a leak rate will be
        calculated" -- and the stable screen swaps TEMP RATE for TMP STABLE."""
        c, clock = a_mag_site()
        c.sumps.set_drift(1, 6.0)
        test = measuring(c, clock)
        advance(c, clock, 3600, step=60)
        self.assertIsNone(test.stable_at)
        self.assertEqual(c.sump_screen("status", 1).split(chr(10))[1],
                         "STATUS: CHK TEMP STABLE")
        self.assertEqual(c.sump_screen("rates", 1),
                         "s 1: TEMP RATE: 6.0 F/HR" + chr(10)
                         + "LEAK RATE: 0.0000 IN./HR")
        c.sumps.set_drift(1, 0.0)
        advance(c, clock, 600, step=60)
        # the first look after the change: its earlier window still holds
        # four minutes of 6 F/h, which is 2.4 F/h between the two averages
        self.assertEqual(test.stable_at - test.start_at, 3900)
        self.assertEqual(c.sump_screen("status", 1).split(chr(10))[1],
                         "STATUS: MEASURING HEIGHT")
        advance(c, clock, 1200, step=60)
        self.assertEqual(c.sump_screen("rates", 1).split(chr(10))[0],
                         "s 1: TMP STABLE: 25 MINS")
        advance(c, clock, 1800, step=60)
        self.assertEqual(test.status, sumptest.PASSED)
        self.assertEqual(test.end_at - test.start_at, 7200)


class TheAborts(unittest.TestCase):
    """576013-610 Rev AC p.24-2's eleven, with 576013-635's numbers."""

    def aborted(self, test, reason):
        self.assertEqual((test.status, test.reason),
                         (sumptest.ABORTED, reason))

    def test_too_little_water_aborts_the_moment_measuring_starts(self):
        """Figure 24-2 is exactly this: WATER TOO LOW, DURATION 0 MINS."""
        c, clock = a_mag_site(water=5.71)
        test = measuring(c, clock)
        self.aborted(test, "02")
        self.assertEqual(c.sumps.values(test)[4], 0.0)
        rows = sumpreports.result_rows(c, test)
        self.assertIn("REASON:WATER TOO LOW", rows)
        self.assertIn("START HT:       5.710 IN", rows)
        self.assertIn("DURATION:         0 MINS", rows)
        self.assertEqual(c.sump_screen("status", 1).split(chr(10))[1],
                         "ABORT: WATER TOO LOW")

    def test_too_much_water_is_the_sensor_length_less_two(self):
        c, clock = a_mag_site(water=22.5)
        self.aborted(measuring(c, clock), "03")
        c, clock = a_mag_site(water=21.9)
        self.assertEqual(measuring(c, clock).status, sumptest.MEASURING)
        c, clock = a_mag_site(water=10.5)
        c.sumps.set_range(1, 12)
        self.aborted(measuring(c, clock), "03")

    def test_the_two_temperatures(self):
        c, clock = a_mag_site(temp=35.0)
        self.aborted(measuring(c, clock), "04")
        c, clock = a_mag_site(temp=116.0)
        self.aborted(measuring(c, clock), "05")

    def test_a_quarter_inch_down_is_water_decreased(self):
        c, clock = a_mag_site()
        c.sumps.set_leak(1, 0.2)
        test = measuring(c, clock)
        advance(c, clock, 3 * 3600)
        self.aborted(test, "07")
        self.assertEqual(test.end_at - test.start_at, 75 * 60)

    def test_a_tenth_up_is_water_increased(self):
        c, clock = a_mag_site()
        c.sumps.set_leak(1, -0.1)
        test = measuring(c, clock)
        advance(c, clock, 3 * 3600)
        self.aborted(test, "06")
        self.assertEqual(test.end_at - test.start_at, 3600)

    def test_two_hours_filling_is_a_test_phase_timeout(self):
        c, clock = a_mag_site()
        c.sumps.start(1)
        advance(c, clock, 5 * 3600)
        test = c.sumps.tests[1]
        self.aborted(test, "10")
        self.assertEqual(test.end_at - test.test_at, 7200)
        # "If test was aborted before measuring height phase, then all of
        # these values will be replaced with dashes (---)"
        self.assertIn("START HT:" + " " * 12 + "---",
                      sumpreports.result_rows(c, test))

    def test_four_hours_unsettled_is_a_temp_stable_timeout(self):
        c, clock = a_mag_site()
        c.sumps.set_drift(1, 6.0)
        test = measuring(c, clock)
        advance(c, clock, 5 * 3600)
        self.aborted(test, "11")
        self.assertEqual(test.end_at - test.start_at, 4 * 3600)
        line = c.sump_screen("status", 1).split(chr(10))[1]
        self.assertEqual(line, "ABORT:TMP STABLE TIMEOUT")
        self.assertLessEqual(len(line), 24)

    def test_stopping_early_is_insufficient_data(self):
        c, clock = a_mag_site()
        test = measuring(c, clock)
        advance(c, clock, 3600)
        c.sumps.stop(1)
        self.aborted(test, "08")

    def test_stopping_a_test_that_would_not_pass_is_leak_rate_too_high(self):
        c, clock = a_mag_site()
        c.sumps.set_leak(1, 0.05)
        test = measuring(c, clock)
        advance(c, clock, 2 * 3600 + 900)
        c.sumps.stop(1)
        self.aborted(test, "09")

    def test_any_other_mag_alarm_aborts_and_water_does_not(self):
        c, clock = a_mag_site()
        test = measuring(c, clock)
        c.sensor_state[("smart", "1")] = "water"
        advance(c, clock, 600)
        self.assertEqual(test.status, sumptest.MEASURING)
        c.sensor_state[("smart", "1")] = "fuel"
        advance(c, clock, 60)
        self.aborted(test, "01")


class TheWaterAlarms(unittest.TestCase):
    """"During the Test Phase water alarms/warnings are suppressed so that
    the user can fill the sump with water. Fuel alarms/warnings are not
    suppressed." And for 24 hours after, or until the sump is empty."""

    def test_the_water_in_the_sump_raises_them_against_s728(self):
        c, _clock = a_mag_site(water=3.0)
        self.assertIn("280601", c.conditions())          # WATER WARNING
        c.sumps.pour(1, 12.0)
        self.assertIn("280701", c.conditions())          # WATER ALARM
        c.sumps.pour(1, 1.0)
        self.assertFalse({"280601", "280701"} & set(c.conditions()))

    def test_a_test_holds_them_off_and_clears_what_was_held(self):
        c, clock = a_mag_site()
        c.latched.add("280701")
        c.sumps.start(1)
        self.assertNotIn("280701", c.latched)
        self.assertNotIn("280701", c.conditions())
        c.sensor_state[("smart", "1")] = "water"
        self.assertNotIn("280701", c.conditions())
        c.sensor_state[("smart", "1")] = "fuel"
        self.assertIn("280501", c.conditions())          # fuel is not

    def test_they_stay_off_a_day_unless_the_sump_is_emptied(self):
        c, clock = a_mag_site()
        measuring(c, clock)
        advance(c, clock, 3 * 3600)
        self.assertEqual(c.sumps.status(1), sumptest.PASSED)
        self.assertTrue(c.sumps.suppressed(1))
        c.sumps.pour(1, 0.0)
        advance(c, clock, 60)
        self.assertTrue(c.sumps.suppressed(1))
        advance(c, clock, 300)
        self.assertFalse(c.sumps.suppressed(1))

    def test_water_above_the_sensor_less_two_is_not_held_off(self):
        c, _clock = a_mag_site(water=23.0)
        c.sumps.start(1)
        self.assertFalse(c.sumps.suppressed(1))
        self.assertIn("280701", c.conditions())


class TheScreens(unittest.TestCase):
    """576013-610 Rev AC p.24-3's screens and their four states."""

    def test_the_rates_before_and_during_the_first_ten_minutes(self):
        c, clock = a_mag_site()
        self.assertEqual(c.sump_screen("rates", 1),
                         "s 1: TEMP RATE: UNKNOWN" + chr(10)
                         + "LEAK RATE: UNKNOWN")
        measuring(c, clock)
        advance(c, clock, 540, step=60)
        self.assertEqual(c.sump_screen("rates", 1),
                         "s 1: TEMP RATE: COMPUTING" + chr(10)
                         + "LEAK RATE: COMPUTING")

    def test_the_status_carries_the_phase_that_started(self):
        """"Date/Time of start of test phase" on FILL SUMP, "of measuring
        height phase" on the others."""
        c, clock = a_mag_site()
        self.assertEqual(c.sump_screen("status", 1),
                         "s 1: SUMP 1" + chr(10) + "NO TEST DATA AVALIABLE")
        c.sumps.start(1)
        advance(c, clock, 600)
        self.assertEqual(c.sump_screen("status", 1),
                         "s 1: 3-29-26      9:43AM" + chr(10)
                         + "STATUS: FILL SUMP")
        c.sumps.measure(1)
        advance(c, clock, 3 * 3600)
        self.assertEqual(c.sump_screen("status", 1),
                         "s 1: 3-29-26      9:53AM" + chr(10) + "TEST PASSED")
        self.assertEqual(c.sump_screen("last_passed", 1),
                         "s 1: 3-29-26      9:53AM" + chr(10)
                         + "LAST PASSED TEST")

    def test_the_height_and_temperature_are_the_sump_s(self):
        c, _clock = a_mag_site(water=7.678, temp=70.1)
        self.assertEqual(c.sump_screen("ht_temp", 1),
                         "  7.678 IN        70.1 F")
        self.assertEqual(c.diag_reading("ss_water_ht", 1), "WATER HT      7.7 IN.")
        self.assertEqual(c.diag_reading("ss_fluid_temp", 1),
                         "FLUID TEMP  70.1 DEG F")

    def test_the_two_functions_want_a_mag_sensor(self):
        """"This menu displays only if the console detects a Mag Sump Sensor
        capable of leak detection"."""
        c, _clock = a_mag_site()
        names = {f["function"] for f in c.available_operating()}
        self.assertTrue({"MAG SUMP LEAK TEST",
                         "MAG SUMP LK TEST RESULTS"} <= names)
        c.values["S72301"] = "0104"                     # a vacuum sensor
        names = {f["function"] for f in c.available_operating()}
        self.assertFalse({"MAG SUMP LEAK TEST",
                          "MAG SUMP LK TEST RESULTS"} & names)


class TheWire(unittest.TestCase):
    """576013-635 Rev AA pp.113-117."""

    def test_317_reports_the_running_test_in_its_own_rows(self):
        c, clock = a_mag_site(water=20.971, temp=76.1)
        h = Handler(c, verbose=False)
        measuring(c, clock)
        advance(c, clock, 17 * 60, step=60)
        lines = send(h, "I31701").splitlines()
        at = lines.index("IN PROGRESS")
        # p.113 sets a blank line under the sensor, and the frame draws it
        self.assertEqual(lines[at + 1:at + 13], [
            "s 1:SUMP 1",
            "",
            "STATUS:MEASURING HEIGHT",
            "START TIME:",
            " MAR 29, 2026  9:53 AM",
            "START HT:     20.971 IN.",
            "START TEMP:       76.1 F",
            "CURRENT HT:   20.971 IN.",
            "CURRENT TEMP:     76.1 F",
            "DURATION:        17 MINS",
            "TEMP RATE:      0.0 F/HR",
            "LEAK RATE: 0.0000 IN./HR"])

    def test_317s_computer_form_carries_the_abort_reason(self):
        c, clock = a_mag_site(water=5.0)
        h = Handler(c, verbose=False)
        measuring(c, clock)
        body = send(h, "i31701").strip(chr(1) + chr(3)).split("&&")[0]
        record = body[len("i31701") + 10:]
        self.assertEqual(record[:6], "010102")           # sensor, ABORTED, LOW
        self.assertEqual(record[16:18], "05")            # five floats follow

    def test_318_319_and_31a_after_a_pass(self):
        c, clock = a_mag_site(water=20.344, temp=75.4)
        h = Handler(c, verbose=False)
        measuring(c, clock)
        advance(c, clock, 3 * 3600)
        lines = send(h, "I31801").splitlines()
        self.assertIn("RESULT: TEST PASSED", lines)
        self.assertIn("DURATION:       120 MINS", lines)
        lines = send(h, "I31901").splitlines()
        at = lines.index("START DATE/TIME             HEIGHT    TEMP    HEIGHT"
                         "    TEMP   MINUTES")
        self.assertEqual(lines[at + 1],
                         "MAR 29, 2026  9:53 AM       20.344    75.4    "
                         "20.344    75.4       120")
        body = send(h, "i31A01").strip(chr(1) + chr(3)).split("&&")[0]
        self.assertEqual(body[len("i31A01") + 10:][:4], "0101")   # one test

    def test_all_sensors_is_the_mag_sensors(self):
        c, clock = a_mag_site()
        c.values["S72302"] = "0204"                     # a vacuum sensor
        h = Handler(c, verbose=False)
        send(h, "S09900149")
        self.assertEqual(c.sumps.status(1), sumptest.FILL_SUMP)
        self.assertEqual(c.sumps.status(2), sumptest.NO_DATA)


class ThePaper(unittest.TestCase):
    """Figures 24-1 and 24-2, p.23-1 and p.23-2."""

    def test_the_three_automatic_printouts(self):
        c, clock = a_mag_site()
        printer.automatic(c)
        c.sumps.start(1)
        advance(c, clock, 600)
        c.sumps.measure(1)
        advance(c, clock, 3 * 3600)
        notes = [(note, lines) for note, lines in printer.automatic(c)
                 if "mag sump" in note]
        self.assertEqual([n for n, _l in notes], [
            "-- PRINT: mag sump leak test test phase started, sensor 1",
            "-- PRINT: mag sump leak test measuring height started, sensor 1",
            "-- PRINT: mag sump leak test result, sensor 1"])
        self.assertIn("STATUS:FILL SUMP", notes[0][1])
        self.assertIn(printer.SETUP_RULE, notes[1][1])
        self.assertIn("STATUS:MEASURING HEIGHT", notes[1][1])
        self.assertIn("RESULT: TEST PASSED", notes[2][1])

    def test_the_history_has_both_sections(self):
        c, clock = a_mag_site()
        for _day in range(2):
            measuring(c, clock)
            advance(c, clock, 26 * 3600, step=3600)
        lines = printer.sump_history(c, 1)
        self.assertEqual(lines.count("START TIME:"), 3)   # two, then one year
        self.assertIn("LAST PASSED EACH YEAR:", lines)
        self.assertIn("START HT:      12.000 IN", lines)

    def test_the_last_passed_printout(self):
        c, clock = a_mag_site()
        self.assertIn("NO TEST DATA AVAILABLE", printer.sump_last_passed(c, 1))
        measuring(c, clock)
        advance(c, clock, 3 * 3600)
        lines = printer.sump_last_passed(c, 1)
        self.assertIn("RESULT:      TEST PASSED", lines)
        self.assertIn("START HT:      12.000 IN", lines)


if __name__ == "__main__":
    unittest.main()
