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
"""Leak tests: a test that measures, and an alarm that means something.

The engine works in console time, so these run the clock forward by hand
rather than sleeping.
"""
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import leaktest, packed, printer            # noqa: E402
from tls350sim.clock import clock_date                    # noqa: E402
from tls350sim.console import Console, describe_alarms      # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


SOH = bytes([1])
CR = bytes([13])


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


class TheProbeHasAMinimumItCanMeasure(unittest.TestCase):
    """FIDELITY R12 and H11, which are one threshold with two consequences.

    576013-818 Rev AB Table 9-2, "Mag Probe Minimum Detected Fluid Levels",
    gives a minimum fuel and a minimum water level per circuit code and per
    float kit, and 577014-449 Rev G's Mag Plus specification gives the same
    pair for the same family. Below the fuel figure the product and water
    floats are too close together to be told apart.

    The console had neither number. A tank holding 20 gallons reported a
    height of 0.19 inches as fact, `02/08 INVALID FUEL LEVEL` was a name in
    the table with nothing behind it, and `LOW LEVEL TEST ERROR` -- the one
    invalidation flag this project has a photograph of -- could not fire.
    """

    def a_mag_tank(self, volume=5000.0, code="D005", size="1"):
        c = a_console(volume=volume)
        c.probe_fitted[1] = code
        c.values["S62F01"] = "01" + size
        c.values["S60701"] = "01" + struct.pack(">f", 96.0).hex().upper()
        return c

    def test_the_table_is_the_manuals_own_numbers(self):
        from tls350sim import readings
        # the row two documents agree on
        self.assertEqual(readings.MINIMUM_LEVELS["D005"]["1"], (3.23, 0.867))
        self.assertEqual(readings.MINIMUM_LEVELS["D005"]["0"], (3.04, 0.63))
        # the 4 inch ethanol-blended float, which only the 8463 family takes
        self.assertEqual(readings.MINIMUM_LEVELS["D005"]["4"], (7.000, 0.38))
        self.assertNotIn("4", readings.MINIMUM_LEVELS["D000"])
        # a one-float probe has no water float to have a minimum for
        self.assertEqual(readings.MINIMUM_LEVELS["D007"]["1"], (3.0, None))
        # and there is no 1 inch column on the page, anywhere
        self.assertEqual([code for code, row in readings.MINIMUM_LEVELS.items()
                          if "3" in row], [])

    def test_a_nearly_empty_tank_says_the_floats_are_too_close(self):
        """576013-939 Quick Help p.14: "T1: INVALID FUEL LEVEL ... CAUSE:
        Fuel and water level floats on the probe are too close together due
        to a lack of fuel in the tank." """
        c = self.a_mag_tank(volume=20.0)
        self.assertEqual(c.probe_minimums(1), (3.23, 0.867))
        self.assertTrue(c.fuel_below_minimum(1))
        self.assertIn("020801", c.compute_alarms())

    def test_a_tank_with_fuel_in_it_says_nothing(self):
        c = self.a_mag_tank(volume=5000.0)
        self.assertFalse(c.fuel_below_minimum(1))
        self.assertNotIn("020801", c.compute_alarms())

    def test_water_raises_the_minimum_by_its_own_depth(self):
        """577013-940 Rev F p.43, on the Invalid Fuel field: "The invalid
        fuel level assumes no water is present. If water is present, the
        invalid fuel level is increased by the water level reading."

        Which makes the test a SEPARATION between the two floats rather
        than a depth -- and that is what every cause line this alarm has
        ever carried describes. A tank whose product clears the bare
        minimum can still have its floats too close together, because the
        water underneath has lifted them both. UNKNOWNS A36.
        """
        c = self.a_mag_tank(volume=5000.0)
        self.assertEqual(c.probe_minimums(1), (3.23, 0.867))
        bare = c.probe_minimums(1)[0]
        height = c.height_at(1, 5000.0)
        self.assertGreater(height, bare, "the premise: product clears it dry")
        self.assertFalse(c.fuel_below_minimum(1))
        # now stand the same product on more water than the gap above it
        c.tank_level[1]["water"] = height - bare + 1.0
        self.assertGreater(c.water_height(1), 0.0, "the water is measurable")
        self.assertTrue(c.fuel_below_minimum(1))
        self.assertIn("020801", c.compute_alarms())

    def test_a_probe_the_table_does_not_cover_raises_nothing(self):
        """A minimum nobody published is not a minimum of zero. The CAP
        probes have no row, and neither has the 1 inch float."""
        c = self.a_mag_tank(volume=20.0, code="0001")
        self.assertEqual(c.probe_minimums(1), (None, None))
        self.assertFalse(c.fuel_below_minimum(1))
        c = self.a_mag_tank(volume=20.0, size="3")
        self.assertEqual(c.probe_minimums(1), (None, None))
        self.assertFalse(c.fuel_below_minimum(1))

    def test_a_test_run_on_that_tank_is_invalid_and_says_why(self):
        """H11. Table 29-4 has eleven flags in its own order and this
        console produced five of them; the one it could not produce is the
        one real 2024 paper had already shown this project."""
        c = self.a_mag_tank(volume=20.0)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        res = c.leaks.result("tank", 1, "periodic")
        self.assertEqual(res.result, leaktest.INVALID)
        self.assertIn("LOW LEVEL TEST ERROR", res.flags)

    def test_a_custom_float_brings_its_own_invalid_fuel_level(self):
        """FIDELITY R13. 576013-879 Rev W p.34: "for the Media-Isolated
        probe's stainless steel float you need to enter a Fuel Offset value
        of +1.000 and an Invalid Fuel value of +0003.300", and the figure
        beside it labels the same number "3.3\" Approximate lowest product
        level measured". Table 9-2 has no column for a custom float, so 60E
        carries its own.
        """
        c = self.a_mag_tank(volume=20.0, size="9")
        # nothing programmed: a custom float with no parameters has no
        # minimum, rather than a minimum of zero
        self.assertEqual(c.probe_minimums(1), (None, None))
        self.assertFalse(c.fuel_below_minimum(1))
        c.values["S60E01"] = "01" + "".join(
            packed.hexfloat(v) for v in (0.0, 1.000, 3.300, 0.0))
        self.assertAlmostEqual(c.probe_minimums(1)[0], 3.300, places=4)
        self.assertTrue(c.fuel_below_minimum(1))
        self.assertIn("020801", c.compute_alarms())

    def test_the_float_size_is_read_without_its_device_prefix(self):
        """S62F holds `01` and then the choice, so `values["S62F01"] == "4"`
        is never true on a console anything has programmed. `bench.py` drew
        the larger phase separation float on that comparison."""
        c = self.a_mag_tank(size="4")
        self.assertEqual(c.values["S62F01"], "014")
        self.assertEqual(c.float_size(1), "4")


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
        # three hours, not two: "a periodic test requires at least 2 hours
        # to complete (3 hours for an annual test)", and a two hour annual
        # test now comes back INVALID with LEAK TEST TOO SHORT against it
        c.leaks.start("tank", 1, "annual", hours=3.0)     # 0.1 gph
        run_out(c, 4)
        self.assertEqual(c.leaks.result("tank", 1, "annual").result,
                         leaktest.FAILED)

    def test_a_failed_test_raises_the_alarm_the_manual_names(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        self.assertEqual(c.compute_alarms(), ["021401"])
        self.assertEqual(describe_alarms(c.compute_alarms())[0]["screen"],
                         "T 1:PERIODIC TEST FAIL")

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
        # 20 PERCENT of the label volume, which 576013-623 Rev AN says this
        # field holds -- "enter the percent limit" -- and not 2000 gallons.
        # A 10,000 gallon tank holding 500 is well under it.
        c.values["S60401"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        c.values["S63601"] = "01" + struct.pack(">f", 20.0).hex().upper()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.INVALID)
        self.assertEqual(c.compute_alarms(), [])

    def test_a_test_in_progress_says_so_and_is_not_an_alarm(self):
        """FIDELITY H10. This used to run on a bare console, because
        `022001` was posted for every running test. It is a FEATURE:
        576013-623 Rev AN p.7-24, "when on, the Tank Test Notify feature
        triggers a warning, allowing the operator to set a relay to shut
        down the submersible", and its own default table gives it OFF."""
        c = a_console()
        c.values["S63001"] = "011"                    # TANK TEST NOTIFY: ON
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        self.assertEqual(c.compute_alarms(), ["022001"])
        self.assertIn("TEST ACTIVE", c.leaks.status_line("tank", 1, "periodic"))
        self.assertEqual(describe_alarms(c.conditions())[0]["description"],
                         "TANK TEST ACTIVE")

    def test_a_console_not_asked_to_notify_runs_its_test_quietly(self):
        """The other half, and the one that was wrong: out of the box this
        console raised a warning a real one would not. The test still RUNS
        -- the status line is unchanged, and so is the result -- which is
        what makes the setting a notification rather than a mode."""
        c = a_console()
        self.assertFalse(c.test_notify(1))
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        self.assertEqual(c.compute_alarms(), [])
        self.assertIn("TEST ACTIVE", c.leaks.status_line("tank", 1, "periodic"))
        self.assertIsNotNone(c.leaks.active("tank", 1))

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


def shutdown_code(code, gph):
    """The stored code for a shutdown RATE, off the field's own choices.

    Tests used to write the code, and that is how the defect below got into
    them: the three families number these differently -- 784's `02` is 3.00
    gal/hr where 757's is 0.20 -- so `shutdown="02"` in a PLLD fixture,
    commented "shut down at 0.2 gph", was programming 3.0. The test then
    passed for the wrong reason and stood as evidence for a mapping the
    manual does not have. Writing the rate and looking the code up cannot
    make that mistake. See FIDELITY U4.
    """
    from tls350sim.console import FIELDS
    for value, label in (FIELDS[f"S{code}01"].get("choices") or ()):
        head = str(label).split()[0]
        if head == "NONE" and gph is None:
            return str(value)
        try:
            if float(head) == gph:
                return str(value)
        except ValueError:
            continue
    raise AssertionError(f"S{code} offers no {gph} gph")


class Lines(unittest.TestCase):
    def a_line(self, leak, shutdown=0.2, method="0"):
        c = a_console()
        c.values["S78401"] = "01" + shutdown_code("784", shutdown)
        c.values["S55300"] = method
        c.line_leak[("plld", 1)] = leak
        return c

    def test_a_vlld_line_holds_the_gallons_its_two_lengths_hold(self):
        """577013-465 Rev AD works the arithmetic itself: "site has 150 feet
        of 2" fiberglass and 50 feet of 3" fiberglass pipe: Total line
        volume = [150 x 0.204] + [50 x 0.461] = 30.6 + 23.1 = 53.7
        gallons", under the rule "multiply the line length (in feet) times
        the 'gallons/foot' value for each pipe type and add the results".

        A VLLD line is programmed as two lengths, S753 for the 2 inch run
        and S754 for the 3 inch, and this returned 0.0 for every one of them
        -- printed as the test volume and then divided by for the
        percent-of-capacity column. FIDELITY R10.
        """
        import struct
        c = a_console()
        c.modules["vlld"] = 1

        def feet(code, value):
            c.values[code] = "01" + struct.pack(">f", value).hex().upper()

        feet("S75301", 150.0)
        feet("S75401", 50.0)
        c.values["S75601"] = "0102"                      # FIBERGLASS
        self.assertAlmostEqual(c.leaks._line_volume(1), 53.65, places=2)
        c.values["S75601"] = "0101"                      # STEEL, thicker wall
        self.assertAlmostEqual(c.leaks._line_volume(1), 42.80, places=2)
        # and a line nobody has programmed still holds nothing, which is the
        # honest answer rather than the old one
        c.values.pop("S75301")
        c.values.pop("S75401")
        self.assertEqual(c.leaks._line_volume(1), 0.0)

    def test_a_failed_line_test_shuts_the_line_down(self):
        c = self.a_line(0.5)
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertEqual(c.leaks.result("plld", 1, "periodic").result,
                         leaktest.FAILED)
        self.assertIn(("plld", 1), c.leaks.disabled)
        self.assertIn("210801", c.compute_alarms())

    def test_a_leak_under_the_shutdown_rate_fails_without_shutting_down(self):
        c = self.a_line(0.15, shutdown=0.2)      # shut down at 0.2 gph
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

    def test_a_shut_down_line_does_not_answer_the_handle(self):
        """The defect a technician finds in the first five minutes.

        A PLLD shutdown de-energizes the STP for that line. A handle is a
        REQUEST to the pump, and a shut-down pump does not answer it -- so
        lifting one gets no pump and no pressure, and the line stays down.

        This console had the shutdown driving every report of itself and
        nothing physical: the next handle up ran the pump straight through
        it and the pressure came back, which teaches the one recovery a
        line leak shutdown needs as though there were nothing to recover
        from. 576013-610 Rev AC makes "the next handle up will restart the
        pump" a property of the LOW PRESSURE ALARM row of Table 29-11 and of
        no other row on that page. See FIDELITY U4.
        """
        c = self.a_line(0.5)
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertIn(("plld", 1), c.leaks.disabled)
        line = c.lines.line("plld", 1)
        self.assertFalse(line.pump, "the STP is still running")

        c.lines.handle("plld", 1, True)
        self.assertFalse(line.pump, "a handle restarted a shut-down pump")
        run_out(c, 1)
        self.assertFalse(line.pump)
        self.assertLess(line.pressure, 20.0,
                        "the line came back up to pump pressure")

    def test_a_passing_test_gives_the_handle_its_pump_back(self):
        """The documented recovery, end to end: the line is down, the handle
        gets nothing, a passing test re-enables it, and the handle works.

        `PASS LINE TEST` is 553's default and what the setup step accepts on
        STEP -- "To re-enable a shutdown line only by a passed line test,
        press STEP", 576013-623 Rev AN p.5-10.
        """
        c = self.a_line(0.5, method="0")
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        line = c.lines.line("plld", 1)
        c.lines.handle("plld", 1, True)
        self.assertFalse(line.pump)
        c.lines.handle("plld", 1, False)

        c.line_leak[("plld", 1)] = 0.0
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertNotIn(("plld", 1), c.leaks.disabled)

        c.lines.handle("plld", 1, True)
        self.assertTrue(line.pump, "a re-enabled line still refuses a handle")

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


class OnABench(unittest.TestCase):
    """How long a line test costs the person sitting in front of it.

    The user reported a PLLD test as stuck on RUNNING. It is not: a real
    0.20 gph line test is two fifteen-minute cycles at least, so the console
    genuinely sits on TEST 0.20 for over half an hour, and this simulator is
    faithful to that. What was missing is any sign of it. The bench's own
    clock control turns half an hour into half a minute, and nothing pointed
    at it. FIDELITY U3.
    """

    def run_out(self, rate, leak=0.0, speed=1.0, wall_limit=210.0):
        """Poll at the panel's own 700ms until the test ends, or give up."""
        c = a_console()
        c.clock_speed = speed
        c.values["S78401"] = "01" + shutdown_code("784", 0.2)
        c.line_leak[("plld", 1)] = leak
        c.leaks.start("plld", 1, rate)
        ln = c.lines.line("plld", 1)
        for poll in range(1, int(wall_limit / 0.7)):
            # 700ms of wall clock is 0.7 console seconds at x1 and 42 at
            # x60, which is what the bench's clock control does
            c.clock_offset += 0.7 * speed
            c.tick()
            if not ln.running():
                return poll * 0.7, {k: v.result for k, v in
                                    (c.leaks.results.get(("plld", 1))
                                     or {}).items()}
        return None, None

    def test_a_gross_test_finishes_while_you_watch(self):
        """The one test that does fit a bench at real time, because its
        length is a third-party evaluation figure rather than a console
        specification and GROSS_TEST_MAX truncates the long end of it."""
        seconds, results = self.run_out("gross")
        self.assertIsNotNone(seconds, "the gross test never finished")
        self.assertLess(seconds, 90.0)
        self.assertEqual(results.get("gross"), leaktest.PASSED)

    def test_a_precision_test_needs_the_bench_clock(self):
        """Half an hour at x1, half a minute at x60. Both are right."""
        seconds, _r = self.run_out("periodic", wall_limit=200.0)
        self.assertIsNone(seconds,
                          "a 0.20 test finishing in 200s of real time means "
                          "its documented 15 minute cycles have been cut")
        seconds, results = self.run_out("periodic", speed=60.0)
        self.assertIsNotNone(seconds, "x60 should finish it")
        self.assertLess(seconds, 90.0)
        self.assertEqual(results.get("periodic"), leaktest.PASSED)

    def test_the_ladder_still_catches_what_it_should(self):
        _s, tight = self.run_out("periodic", leak=0.0, speed=60.0)
        self.assertEqual(tight, {"gross": leaktest.PASSED,
                                 "periodic": leaktest.PASSED})
        _s, small = self.run_out("periodic", leak=0.5, speed=60.0)
        self.assertEqual(small, {"gross": leaktest.PASSED,
                                 "periodic": leaktest.FAILED})
        # "tests always run in the order 3.0, 0.2, 0.1": a line that fails
        # the gross test never reaches the precision one
        _s, big = self.run_out("periodic", leak=5.0, speed=60.0)
        self.assertEqual(big, {"gross": leaktest.FAILED})


class Printouts(unittest.TestCase):
    def test_the_report_prints_the_result(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        out = "\n".join(printer.leak_tests(c, "tank"))
        # the slip a compliance inspector reads, measured off real paper
        self.assertIn("LEAK TEST REPORT", out)
        self.assertIn("T 1:REGULAR UNLEADED", out)
        self.assertIn("PROBE SERIAL NUM", out)
        self.assertIn("TEST STARTING TIME:", out)
        self.assertIn("TEST LENGTH =    2.0 HRS", out)
        self.assertIn("LEAK TEST RESULTS", out)
        self.assertIn(" 0.20 GAL/HR TEST FAIL", out)

    def test_a_test_prints_a_slip_when_it_starts(self):
        """Real paper, 2024: no station header, no rule, TEST LENGTH with no
        "=" and the word HOURS, then the tank's inventory block."""
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0, origin="schedule")
        kind, run = c.leaks.printed_slips[0]
        self.assertEqual(kind, "start")
        out = printer.leak_start_slip(c, run)
        self.assertEqual(out[0], "START IN-TANK LEAK TEST")
        self.assertEqual(out[1], "TEST BY PROGRAMMED TIME")
        self.assertIn("TEST LENGTH 2 HOURS", out)
        self.assertIn("T 1:REGULAR UNLEADED", out)
        self.assertTrue(any(l.startswith("VOLUME    =") for l in out), out)
        # a slip is not a report: nothing here is a header, a rule or an END
        self.assertNotIn(printer.SETUP_RULE, out)
        self.assertNotIn(printer.END_MARK, out)
        # the roll takes every line of it without folding
        self.assertEqual([l for l in out if len(l) > printer.WIDTH], [])

    def test_a_finished_test_prints_a_stop_slip_then_the_report(self):
        c = a_console(leak=0.5)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        c.leaks.printed_slips.clear()
        run_out(c, 3)
        kinds = [s[0] for s in c.leaks.printed_slips]
        self.assertEqual(kinds, ["stop"])
        _kind, device, when = c.leaks.printed_slips[0]
        out = printer.leak_stop_slip(c, device, when)
        self.assertEqual(out[0], "STOP IN-TANK LEAK TEST")
        self.assertEqual(out[1], "T 1:REGULAR UNLEADED")
        self.assertEqual(len(out), 3)

    def test_the_slip_is_one_firmware_era_all_the_way_down(self):
        """FIDELITY W17. Two compliance rolls thirty years apart print the
        same slip in two forms:

            1996-1998                    2024-2025
            0.2 GAL/HR TEST PASS         0.20 GAL/HR TEST PASS
            STRT VOLUME = 3725 GALS      STRT VOLUME = 1941.6 GAL
            TEST LENGTH =  4.3 HRS       TEST LENGTH =    2.0 HRS

        The newer firmware gains a decimal on both and drops the S from GALS
        to keep the line at twenty-four. Either is defensible and MIXING
        them is not, which is what this asserts: the console reports itself
        as the later software, so all three rows are the later form.
        """
        c = a_console(leak=0.0)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        out = printer.tank_leak_report(c)
        rows = {line.split("=")[0].strip(): line for line in out if "=" in line}
        self.assertRegex(rows["TEST LENGTH"], r"= +\d+\.\d HRS$")
        self.assertRegex(rows["STRT VOLUME"], r"= +\d+\.\d GAL$")
        self.assertIn(" 0.20 GAL/HR TEST", chr(10).join(out))
        self.assertNotIn(" 0.2 GAL/HR TEST", chr(10).join(out))
        self.assertEqual([l for l in out if len(l) > printer.WIDTH], [])

    def test_an_invalid_test_prints_why_in_the_manual_words(self):
        """Table 29-4's words, in the block real 2024 paper prints them in."""
        c = a_console(leak=0.15)
        c.leaks.start("tank", 1, "annual", hours=2.0)   # needs three
        run_out(c, 3)
        res = c.leaks.result("tank", 1, "annual")
        self.assertEqual(res.result, leaktest.INVALID)
        self.assertEqual(res.flags, ("LEAK TEST TOO SHORT",))
        out = printer.tank_leak_report(c)
        self.assertIn(" 0.10 GAL/HR TEST INVL", out)
        self.assertIn(" 0.10 GAL/HR FLAGS:", out)
        self.assertIn(" LEAK TEST TOO SHORT", out)

    def test_every_flag_the_console_raises_is_one_the_table_names(self):
        """The vocabulary is closed: Table 29-4 or nothing."""
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run = c.leaks.running[("tank", 1)]
        # a drop mid-test and a test cut short, and not temperature -- this
        # tank is following the season, which never leaves the high forties
        # to the low sixties. Holding one is the test below.
        c.tank_level[1]["volume"] = run.volume + 500.0
        raised = c.leaks.invalidations(run, 0.5)
        self.assertTrue(raised)
        self.assertEqual([f for f in raised if f not in leaktest.FLAGS], [])
        self.assertIn("LEAK TEST TOO SHORT", raised)
        self.assertIn("PRODUCT LEVEL INCREASE", raised)

    def test_a_tank_held_out_of_range_invalidates_its_own_test(self):
        """"Temperature reading is below 0 F (-17.8 C) or above 100 F
        (37.8 C)" -- Table 29-4, written into `invalidations` from the start
        and unreachable, because the product temperature wandered between 48
        and 62 F and there was no way to move it. The bench holds a tank at
        a temperature now. FIDELITY Y10."""
        for held in (110.0, -5.0):
            c = a_console()
            c.tank_temp[1] = held
            self.assertEqual(c.product_temperature(1), held)
            c.leaks.start("tank", 1, "periodic", hours=2.0)
            run_out(c, 3)
            res = c.leaks.result("tank", 1, "periodic")
            self.assertEqual(res.result, leaktest.INVALID, held)
            self.assertIn("TEMPERATURE OUT OF RANGE", res.flags)

    def test_and_inside_the_range_it_does_not(self):
        """Held is not the same as out of range: 90 F is not a site a
        console complains about."""
        c = a_console()
        c.tank_temp[1] = 90.0
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 3)
        res = c.leaks.result("tank", 1, "periodic")
        self.assertNotIn("TEMPERATURE OUT OF RANGE", res.flags)

    def test_letting_go_puts_the_tank_back_on_the_season(self):
        c = a_console()
        c.tank_temp[1] = 110.0
        c.tank_temp.pop(1)
        self.assertLess(c.product_temperature(1), 62.0)
        self.assertGreater(c.product_temperature(1), 48.0)

    def test_a_shut_down_line_says_so_on_the_report(self):
        c = a_console()
        c.values["S78401"] = "01" + shutdown_code("784", 0.2)
        c.line_leak[("plld", 1)] = 0.5
        c.leaks.start("plld", 1, "periodic")
        run_out(c, 2)
        self.assertIn("LINE SHUT DOWN",
                      "\n".join(printer.leak_tests(c, "plld")))


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
                         "LEAK ALARM")

    def test_a_sudden_loss_says_so_at_once(self):
        c = self.a_tank_under_test(leak=60.0)
        self._drain(c, 1)                   # sixty gallons gone in an hour
        alarms = c.compute_alarms()
        self.assertIn("020601", alarms)
        self.assertIn("020201", alarms)
        self.assertEqual(describe_alarms(["020601"])[0]["description"],
                         "SUDDEN LOSS ALARM")

    def test_and_the_test_itself_fails_it(self):
        c = self.a_tank_under_test(leak=60.0)
        self._drain(c, 13)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.FAILED)

    def an_unprogrammed_tank_under_test(self, leak):
        c = a_console(leak=leak)
        for code in ("S62501", "S62601"):
            c.values.pop(code, None)
        c.tick()
        c.leaks.start("tank", 1, "periodic", hours=12.0)
        return c

    def test_an_unprogrammed_tank_has_the_limits_it_reports(self):
        """A console out of the box reports a Sudden Loss Limit and a Leak
        Alarm Limit of 99 gallons -- a real one on I62500 and I62600, and
        the site tape's `LEAK ALARM LIMIT:     99` -- and enforced neither,
        because the engine read only what had been stored. FIDELITY S18."""
        under = self.an_unprogrammed_tank_under_test(leak=60.0)
        self._drain(under, 1)                  # sixty gallons: inside 99
        alarms = under.compute_alarms()
        self.assertNotIn("020601", alarms)
        self.assertNotIn("020201", alarms)
        over = self.an_unprogrammed_tank_under_test(leak=120.0)
        self._drain(over, 1)                   # a hundred and twenty: past it
        alarms = over.compute_alarms()
        self.assertIn("020601", alarms)
        self.assertIn("020201", alarms)


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
                         "PER TST NEEDED WRN")

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
        # The clock is back to zero, so the CONDITION is gone -- but the
        # message it already posted waits for ALARM/TEST like every other.
        # See console.latches and 576013-610 p.29-1.
        c.acknowledge()
        self.assertNotIn("021801", c.compute_alarms())
        self.assertNotIn("021601", c.compute_alarms())


class TwoOhSevenAndTwoTwelveAreOneReport(unittest.TestCase):
    """FIDELITY H14. 576013-635 Rev AA p.74 prints the SAME sample under 212
    as p.65 prints under 207. They differ only in the computer format, where
    each of 212's records carries two more fields:

        zz        - Number of 8 Byte Fields to Follow (Hex)
        mmmmmmmm  - In-Tank Leak Test Method (Hex),
                    00000000=Standard, 00000001=CSLD

    This console built 212 separately -- three LAST blocks and no FULLEST or
    monthly sections, a compressed header, a different row format, the
    report type and history number hardcoded, and the method byte hardcoded
    to Standard even on a CSLD tank, which is the one field 212 exists for.
    """

    def a_history(self, csld=False):
        c = a_console()
        c.modules["rs232"] = 1
        if csld:
            c.software["csld"] = True
            c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
            # "The CSLD option appears only when the tank is equipped with a
            # 0.1 gph (0.38 lph) Mag probe", 576013-623 p.8-2, and a tank
            # with a float size programmed has a float. FIDELITY K6.
            c.values["S62F01"] = "011"
        now = time.mktime(c.now())
        for rate_key, volume in (("gross", 2821.0), ("periodic", 2680.0)):
            c.leaks.history.setdefault(("tank", 1), []).append(
                leaktest.Result("tank", 1, rate_key, leaktest.PASSED,
                                0.05, 27.0, volume, now - 3600))
        return c, Handler(c, verbose=False)

    def ask(self, h, command):
        return h.handle(SOH + command + CR).decode("ascii")

    def test_the_display_format_is_the_same_report(self):
        _c, h = self.a_history()
        self.assertEqual(self.ask(h, b"I20701").replace("I20701", "X"),
                         self.ask(h, b"I21201").replace("I21201", "X"))

    def test_it_carries_the_fullest_and_monthly_blocks(self):
        """The three the separate build left out."""
        _c, h = self.a_history()
        got = self.ask(h, b"I21201")
        # p.65's own block titles, all of them cut to the 24-column roll:
        # `LAST PERIODIC TEST PASSED:` would be 26, and the monthly title is
        # two lines. There is no FULLEST PERIODIC block at all -- the
        # periodic fullest IS the monthly list. See FIDELITY H3.
        for block in ("LAST GROSS TEST PASSED:", "LAST PERIODIC TEST PASS:",
                      "FULLEST ANNUAL TEST PASS", "FULLEST PERIODIC TEST",
                      "PASSED EACH MONTH:"):
            self.assertIn(block, got)
        self.assertNotIn("FULLEST PERIODIC TEST PASSED EACH MONTH:", got)

    def test_the_computer_format_is_207s_plus_the_method(self):
        """Same records, each with `zz` and the eight character method after
        it, and nothing else different."""
        c, h = self.a_history()
        records = len(c.leak_history_records(1))
        self.assertGreater(records, 1, "the fixture should make several")
        seven = self.ask(h, b"i20701").split("&&")[0]
        twelve = self.ask(h, b"i21201").split("&&")[0]
        self.assertEqual(len(twelve) - len(seven), 10 * records)

    def test_the_method_byte_follows_the_tank(self):
        _c, standard = self.a_history()
        _c2, csld = self.a_history(csld=True)
        self.assertIn("0100000001", self.ask(csld, b"i21201"))
        self.assertNotIn("0100000001", self.ask(standard, b"i21201"))

    def test_the_test_type_column_is_per_test_not_per_tank(self):
        """p.65's sample prints both on one tank: the gross test STANDARD
        and the periodic CSLD. CSLD is a 0.2 gph continuous method, so it IS
        the periodic test; the gross one is discrete and Standard whatever
        the tank is programmed to."""
        c, _h = self.a_history(csld=True)
        lines = c.leak_history_lines(1)
        gross = lines[lines.index("LAST GROSS TEST PASSED:") + 2]
        periodic = lines[lines.index("LAST PERIODIC TEST PASS:") + 2]
        self.assertTrue(gross.rstrip().endswith("STANDARD"), gross)
        self.assertTrue(periodic.rstrip().endswith("CSLD"), periodic)


class ThePageSixtyFiveSampleReplays(unittest.TestCase):
    """FIDELITY H3. 576013-635 Rev AA p.65's `I207` sample is fully
    determined -- three results against one tank, with their volumes, their
    hours and their percentages printed:

        T 1:REGULAR UNLEADED
        LAST GROSS TEST PASSED:
        TEST START TIME            HOURS    VOLUME   % VOLUME   TEST TYPE
        JUL 29, 1997  6:02 AM                2821       48.9    STANDARD
        LAST ANNUAL TEST PASSED:
        NO TEST PASSED
        FULLEST ANNUAL TEST PASS
        NO TEST PASSED
        LAST PERIODIC TEST PASS:
        JUL 29, 1997  4:15 AM       27       2680       46.4      CSLD
        FULLEST PERIODIC TEST
        PASSED EACH MONTH:
        JUL 20, 1997  1:52 AM       25       2916       50.5      CSLD

    The tank is 5,772 gallons, which the three percentages agree on.
    """

    FULL = 5772.0

    def the_manuals_tank(self):
        c = a_console()
        c.modules["rs232"] = 1
        c.values["S62F01"] = "011"
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        c.values["S60A01"] = "01" + struct.pack(">f", self.FULL).hex().upper()
        c.tank_level[1] = {"volume": 2680.0, "water": 0.0}
        stamps = [
            ("periodic", 2916.0, 25.0, (1997, 7, 20, 1, 52)),
            ("periodic", 2680.0, 27.0, (1997, 7, 29, 4, 15)),
            ("gross", 2821.0, 0.0, (1997, 7, 29, 6, 2)),
        ]
        for rate_key, volume, hours, when in stamps:
            started = time.mktime(when + (0, 0, 1, -1))
            c.leaks.history.setdefault(("tank", 1), []).append(
                leaktest.Result("tank", 1, rate_key, leaktest.PASSED,
                                0.05, hours, volume, started))
        return c

    def rows(self, c):
        return [l for l in c.leak_history_lines(1) if l[:1].isalpha()
                and l[:4] in ("JUL ", "AUG ", "SEP ")]

    def test_all_three_percentages_come_out_to_the_tenth(self):
        c = self.the_manuals_tank()
        got = [line[41:52].strip() for line in self.rows(c)]
        self.assertEqual(got, ["48.9", "46.4", "50.5"])

    def test_the_blocks_are_the_samples_blocks_in_its_order(self):
        c = self.the_manuals_tank()
        titles = [l for l in c.leak_history_lines(1)
                  if l.startswith(("LAST ", "FULLEST ", "PASSED EACH"))]
        self.assertEqual(titles, [
            "LAST GROSS TEST PASSED:",
            "LAST ANNUAL TEST PASSED:",
            "FULLEST ANNUAL TEST PASS",
            "LAST PERIODIC TEST PASS:",
            "FULLEST PERIODIC TEST",
            "PASSED EACH MONTH:",
        ])

    def test_every_title_fits_the_roll(self):
        """Which is why they are worded the way they are: `LAST PERIODIC
        TEST PASSED:` is twenty-six characters."""
        c = self.the_manuals_tank()
        for line in c.leak_history_lines(1):
            if line.startswith(("LAST ", "FULLEST ", "PASSED EACH", "NO ")):
                self.assertLessEqual(len(line), 24, line)

    def test_there_is_no_fullest_periodic_block(self):
        """The sample has a passing periodic test and prints none: the
        periodic fullest is expressed AS the monthly list."""
        c = self.the_manuals_tank()
        self.assertNotIn("FULLEST PERIODIC TEST PASS",
                         c.leak_history_lines(1))

    def test_a_gross_tests_hours_column_is_blank(self):
        """In both the 207 and the 208 sample. This printed `0`."""
        c = self.the_manuals_tank()
        gross = self.rows(c)[0]
        self.assertEqual(gross[22:30].strip(), "")
        self.assertEqual(self.rows(c)[1][22:30].strip(), "27")

    def test_the_test_type_is_per_record(self):
        """The sample has STANDARD on the gross row and CSLD on the two
        periodic rows for the same tank -- which was H3's fourth defect and
        had already been fixed by H14 without this entry noticing."""
        c = self.the_manuals_tank()
        self.assertEqual([line[52:].strip() for line in self.rows(c)],
                         ["STANDARD", "CSLD", "CSLD"])


class TheTankResultsScreenShowsADate(unittest.TestCase):
    """FIDELITY H13. 576013-610 Rev AC pp.9-1 and 9-2 draw
    `T #: (Product Name)` over `GRS: (Date) (Results)`, three times, and say
    what the two halves are: "The system prints the date the test ran and
    the results (PASS, FAIL, or INVALID)".

    This console returned `PER: PASSED  0.00 GAL/HR` -- a rate, and no date
    -- where the LINE branch two lines above in the same method had had the
    right shape and its own citation all along.
    """

    def a_finished_test(self, leak=0.0, rate_key="periodic", hours=2.0):
        c = a_console()
        c.values["S62F01"] = "011"
        c.tank_leak[1] = leak
        c.leaks.start("tank", 1, rate_key, hours=hours)
        c.clock_offset += (hours + 0.5) * 3600.0
        c.tick()
        return c

    def test_a_passing_test_reads_as_a_date_and_pass(self):
        c = self.a_finished_test()
        line = c.leaks.status_line("tank", 1, "periodic")
        self.assertTrue(line.endswith(" PASS"), line)
        self.assertNotIn("GAL/HR", line)
        self.assertIn(clock_date(c.leaks.result("tank", 1, "periodic").started,
                                 year=False), line)

    def test_the_word_set_is_chapter_nines(self):
        """PASS and FAIL, not the engine's PASSED and FAILED -- which the
        LINE screens keep, because that is what pp.51 and 61 draw."""
        c = self.a_finished_test(leak=5.0)
        self.assertTrue(
            c.leaks.status_line("tank", 1, "periodic").endswith(" FAIL"))
        self.assertEqual(leaktest.TANK_WORDS[leaktest.PASSED], "PASS")
        self.assertEqual(leaktest.TANK_WORDS[leaktest.INVALID], "INVALID")

    def test_it_fits_the_panel_with_its_prefix(self):
        """`GRS: ` and then this, on a 24-column line -- which is why the
        date drops its year here where CSLD's `PER:` line keeps one."""
        c = self.a_finished_test(leak=5.0, hours=0.5)
        line = c.leaks.status_line("tank", 1, "periodic")
        self.assertTrue(line.endswith(" INVALID"), line)
        self.assertLessEqual(len("GRS: " + line), 24, line)


class PrintLeakHistoryPrintsTheHistory(unittest.TestCase):
    """FIDELITY H13. 576013-818 Rev AB Figure 6-9 annotates the three PRINT
    screens of IN-TANK LEAK RESULT separately: the two rate screens say
    "Printout contains static tank test results" and the third says "This
    printout gives the tank result for every month".

    Every screen of the function answered PRINT with the same leak-test
    report, so the monthly history was not reachable from the panel at all
    -- though `leak_history_lines` builds it correctly and `I207` has served
    it over the wire all along.
    """

    def a_console_with_history(self):
        c = a_console()
        c.values["S62F01"] = "011"
        c.modules["rs232"] = 1
        c.tank_leak[1] = 0.0
        for _ in range(2):
            c.leaks.start("tank", 1, "periodic", hours=2.0)
            c.clock_offset += 2.5 * 3600.0
            c.tick()
        return c

    def test_the_three_screens_carry_three_different_prints(self):
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "IN-TANK LEAK RESULT"][0]
        marks = {sc["l2"]: sc.get("diagprint") for sc in fn["screens"]}
        self.assertEqual(marks["PRINT 0.20 LEAK REPORT"], "tank_periodic")
        self.assertEqual(marks["PRINT 0.10 LEAK REPORT"], "tank_annual")
        self.assertEqual(marks["PRINT LEAK HISTORY"], "tank_history")

    def test_the_history_report_is_the_monthly_one(self):
        from tls350sim import printer
        c = self.a_console_with_history()
        rows = [str(x) for x in printer.tank_leak_history(c, 1)]
        self.assertIn("FULLEST PERIODIC TEST", rows)
        self.assertIn("PASSED EACH MONTH:", rows)
        self.assertIn("LAST PERIODIC TEST PASS:", rows)

    def test_and_it_is_not_the_static_report(self):
        from tls350sim import printer
        c = self.a_console_with_history()
        static = [str(x) for x in printer.leak_tests(c, "tank", 1)]
        history = [str(x) for x in printer.tank_leak_history(c, 1)]
        self.assertNotEqual(static, history)
        self.assertNotIn("PASSED EACH MONTH:", static)


class ThreeSetupFeaturesThatReachedNothing(unittest.TestCase):
    """FIDELITY H9. Each is specified to the number in 576013-623 Rev AN and
    each was stored and read by nothing."""

    def a_tank(self, **values):
        c = a_console()
        c.values["S62F01"] = "011"
        for key, value in values.items():
            c.values[key] = value
        return c

    # ---- S61A, Leak Test Early Stop ------------------------------------
    def test_early_stop_refuses_a_test_over_a_recent_delivery(self):
        """"When enabled this feature will prevent an In-Tank Leak Test from
        starting under the following conditions ... 2. It is less than 8
        hours from a delivery", p.8-8."""
        c = self.a_tank()
        now = time.mktime(c.now())

        class _Drop:
            start = {"at": now - 7200.0}
            end = {"at": now - 3600.0}

        c.deliveries.records.setdefault(1, []).append(_Drop())
        self.assertEqual(c.leaks.start("tank", 1, "periodic"), "TEST STARTED")
        c.leaks.stop("tank", 1)
        c.values["S61A01"] = "011"
        self.assertEqual(c.leaks.early_stop_refusals(1, "periodic"),
                         ["RECENT DELIVERY"])
        self.assertEqual(c.leaks.start("tank", 1, "periodic"),
                         "RECENT DELIVERY")
        self.assertNotIn(("tank", 1), c.leaks.running)

    def test_and_refuses_one_on_a_tank_that_is_too_cold(self):
        """"3. The product temperature is less than 0 F ... or more than
        +100 F"."""
        c = self.a_tank(S61A01="011")
        self.assertEqual(c.leaks.early_stop_refusals(1, "periodic"), [])
        c.tank_temp[1] = 120.0
        self.assertEqual(c.leaks.early_stop_refusals(1, "periodic"),
                         ["TEMPERATURE OUT OF RANGE"])

    def test_a_disabled_early_stop_refuses_nothing(self):
        c = self.a_tank()
        c.tank_temp[1] = 120.0
        self.assertEqual(c.leaks.early_stop_refusals(1, "periodic"), [])
        self.assertEqual(c.leaks.start("tank", 1, "periodic"), "TEST STARTED")

    def test_a_passing_test_is_completed_after_two_hours(self):
        """"If you have Leak Test Early Stop enabled and the console
        determines that an in-tank leak test has passed after the first two
        hours of the test, the test is completed, even though you had
        entered a Leak Test Duration of more than 2 hours"."""
        c = self.a_tank(S61A01="011")
        c.tank_leak[1] = 0.0
        c.leaks.start("tank", 1, "periodic", hours=6.0)
        c.clock_offset += 1.5 * 3600.0
        c.tick()
        self.assertIn(("tank", 1), c.leaks.running, "not yet two hours")
        c.clock_offset += 0.75 * 3600.0
        c.tick()
        self.assertNotIn(("tank", 1), c.leaks.running)
        got = c.leaks.result("tank", 1, "periodic")
        self.assertEqual(got.result, leaktest.PASSED)
        self.assertLess(got.hours, 6.0)

    def test_a_failing_test_runs_its_full_duration(self):
        """The feature completes a test that HAS PASSED, so a leaking tank
        gets the whole test rather than an early verdict."""
        c = self.a_tank(S61A01="011")
        c.tank_leak[1] = 5.0
        c.leaks.start("tank", 1, "periodic", hours=4.0)
        c.clock_offset += 3 * 3600.0
        c.tick()
        self.assertIn(("tank", 1), c.leaks.running)

    def test_and_without_the_feature_it_runs_the_whole_duration(self):
        c = self.a_tank()
        c.tank_leak[1] = 0.0
        c.leaks.start("tank", 1, "periodic", hours=4.0)
        c.clock_offset += 3 * 3600.0
        c.tick()
        self.assertIn(("tank", 1), c.leaks.running)

    # ---- S61B, Gross Test Auto-Confirm ---------------------------------
    def fail_a_gross_test(self, c):
        c.tank_leak[1] = 30.0
        c.leaks.start("tank", 1, "gross", hours=2.0)
        c.clock_offset += 2.5 * 3600.0
        c.tick()

    def test_the_first_gross_fail_posts_without_auto_confirm(self):
        c = self.a_tank()
        self.fail_a_gross_test(c)
        self.assertEqual(c.leaks.result("tank", 1, "gross").result,
                         leaktest.FAILED)
        self.assertIn("021301", c.compute_alarms())

    def test_auto_confirm_wants_two_fails_in_a_row(self):
        """"When enabled, two test fails in a row will be required before a
        Fail is posted", p.8-7."""
        c = self.a_tank(S61B01="011")
        self.fail_a_gross_test(c)
        self.assertEqual(c.leaks.result("tank", 1, "gross").result,
                         leaktest.FAILED, "the test still fails")
        self.assertNotIn("021301", c.compute_alarms(), "and says nothing yet")
        self.fail_a_gross_test(c)
        self.assertIn("021301", c.compute_alarms())

    def test_a_pass_between_two_fails_breaks_the_run(self):
        c = self.a_tank(S61B01="011")
        self.fail_a_gross_test(c)
        c.tank_leak[1] = 0.0
        c.leaks.start("tank", 1, "gross", hours=2.0)
        c.clock_offset += 2.5 * 3600.0
        c.tick()
        self.fail_a_gross_test(c)
        self.assertNotIn("021301", c.compute_alarms())

    def test_it_is_the_gross_test_and_only_the_gross_test(self):
        """The setup step is headed Gross Test Auto-Confirm and the cost it
        warns about is "the time needed to detect a gross leak"."""
        c = self.a_tank(S61B01="011")
        c.tank_leak[1] = 1.0
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        c.clock_offset += 2.5 * 3600.0
        c.tick()
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.FAILED)
        self.assertIn("021401", c.compute_alarms())

    # ---- S62C, Periodic Test Type --------------------------------------
    def test_a_quick_periodic_test_is_an_hour(self):
        """"Choose Standard to run a 2-hour periodic leak test. Choose Quick
        to perform a 0.2 gph (0.76 lph) test in one hour", p.7-22 -- and a
        Quick site's one-hour test was LEAK TEST TOO SHORT here."""
        c = self.a_tank()
        self.assertEqual(c.leaks.minimum_hours(1, "periodic"), 2.0)
        c.values["S62C01"] = "011"
        self.assertTrue(c.leaks.quick_periodic(1))
        self.assertEqual(c.leaks.minimum_hours(1, "periodic"), 1.0)
        self.assertEqual(c.leaks.minimum_hours(1, "annual"), 4.0,
                         "which is the annual test's, and is untouched")

    def test_and_the_hour_long_test_is_valid_on_a_quick_site(self):
        c = self.a_tank(S62C01="011")
        c.tank_leak[1] = 0.0
        c.leaks.start("tank", 1, "periodic", hours=1.0)
        c.clock_offset += 1.1 * 3600.0
        c.tick()
        got = c.leaks.result("tank", 1, "periodic")
        self.assertEqual(got.result, leaktest.PASSED)
        self.assertNotIn("LEAK TEST TOO SHORT", got.flags)


class TheAnnualMinimumDependsOnTheFloat(unittest.TestCase):
    """FIDELITY H7. 576013-610 Rev AC Table 20-1, *Minimum In-Tank Leak Test
    Times*:

        0.2 gph    0.1 or 0.2 Magnetostrictive    2 hours
        0.1 gph    0.1 Magnetostrictive           3 hours*
        *Add one extra hour if 2" floats are installed.

    `MIN_HOURS` had no float term, and `presets.py` programs every tank's
    float size as `2.0 IN.` -- so the shipped bench is a two-inch-float site
    where the annual minimum is four hours, and a 3.5 hour annual test was
    judged valid here and is LEAK TEST TOO SHORT on real iron.
    """

    def a_tank(self, size="1"):
        c = a_console()
        c.values["S62F01"] = "01" + size
        return c

    def test_the_footnote_is_on_the_annual_row_alone(self):
        c = self.a_tank()
        self.assertEqual(c.leaks.minimum_hours(1, "periodic"), 2.0)
        self.assertEqual(c.leaks.minimum_hours(1, "gross"), 2.0)
        self.assertEqual(c.leaks.minimum_hours(1, "annual"), 4.0)

    def test_a_four_inch_float_keeps_the_three(self):
        c = self.a_tank("0")                    # 4.0 IN.
        self.assertEqual(c.leaks.minimum_hours(1, "annual"), 3.0)

    def test_the_shipped_bench_is_a_two_inch_site(self):
        """Which is what makes this worth having rather than a footnote
        nobody meets: every preset programs the 2 inch kit."""
        from tls350sim import presets
        from tls350sim.console import Console
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        for tank in sorted(c.tank_level):
            self.assertEqual(c.float_size(tank), "1", tank)
            self.assertEqual(c.leaks.minimum_hours(tank, "annual"), 4.0)


class AManualTestRunsAndReportsWhatItRan(unittest.TestCase):
    """FIDELITY H5 and H6. 576013-610 Rev AC p.20-3, repeated on p.20-5:
    "NOTE: If you start an In-Tank Leak Test manually, you will need to stop
    the test manually. Otherwise, the test will run for 24 hours."

    `tick()` opened with `if run.manual_stop: continue`, so nothing ever
    ended one on a timer -- started and left alone, a manual test was still
    running nine hours later and would have run until the process stopped.
    And `_finish` recorded `min(elapsed, run.hours)`, where `run.hours` for
    a manual test is whatever the START walk's DURATION step held, so a test
    run for nine hours and stopped by hand printed `TEST LENGTH = 2.0 HRS`.
    """

    def a_manual_test(self, leak=0.05):
        c = a_console(leak=leak)
        c.leaks.start("tank", 1, "periodic", manual_stop=True)
        return c

    def test_it_does_not_end_on_its_programmed_duration(self):
        """Two hours is the DURATION step's default, and a manual test is
        not on it."""
        c = self.a_manual_test()
        run_out(c, 3)
        self.assertIn(("tank", 1), c.leaks.running)

    def test_but_it_does_end_at_twenty_four_hours(self):
        c = self.a_manual_test()
        run_out(c, 25)
        self.assertNotIn(("tank", 1), c.leaks.running)

    def test_and_records_the_twenty_four_it_ran(self):
        """The bench's clock can jump a week in one tick, and a test the
        console ended at 24 hours did not run for 25."""
        c = self.a_manual_test()
        run_out(c, 25)
        self.assertAlmostEqual(
            c.leaks.result("tank", 1, "periodic").hours,
            leaktest.MANUAL_STOP_HOURS, places=3)

    def test_stopped_by_hand_it_reports_what_it_actually_ran(self):
        """p.9-2's own sample is TEST LENGTH = 4.3 HRS, a figure that could
        only be measured."""
        c = self.a_manual_test()
        run_out(c, 4.3)
        c.leaks.stop("tank", 1)
        self.assertAlmostEqual(
            c.leaks.result("tank", 1, "periodic").hours, 4.3, places=1)

    def test_a_timed_test_still_reports_its_duration(self):
        """The clamp is right for a test that runs to its own length."""
        c = a_console(leak=0.05)
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, 5)
        self.assertAlmostEqual(
            c.leaks.result("tank", 1, "periodic").hours, 2.0, places=3)


class I208ReportsARow(unittest.TestCase):
    """FIDELITY H4. 576013-635 Rev AA p.71's own sample, three ways.

        TEST TYPE  START TIME              RESULT     RATE  HOURS  VOLUME
         ANNUAL    NOV 21, 1995  8:34 AM   PASSED     0.00     12    9088
         PERIODIC  NOV 21, 1995  8:34 AM   PASSED     0.00     12    9088
         GROSS     NOV 24, 1995  8:04 AM   PASSED     0.00           9088

    *Read from the PDF's word coordinates.* The plain extraction of this
    page reports ANNUAL/GROSS/PERIODIC, which is this console's own wrong
    order -- so a fix taken from the text would have confirmed the defect.
    """

    def three_results(self, console):
        for rate_key in ("gross", "periodic", "annual"):
            console.leaks.start("tank", 1, rate_key, hours=2.0)
            console.clock_offset += 4 * 3600.0
            console.leaks.tick()
            console.leaks.stop("tank", 1)

    def rows(self, console):
        return [line for line in console.leaks_results_report([1]).split(
            chr(10)) if line.startswith(" ")]

    def test_the_rows_run_by_rate_and_not_by_name(self):
        """0.10, then 0.20, then 3.0 -- and alphabetically GROSS is second,
        which is what `sorted(results.items())` gave."""
        c = a_console()
        self.three_results(c)
        self.assertEqual([row.split()[0] for row in self.rows(c)],
                         ["ANNUAL", "PERIODIC", "GROSS"])

    def test_the_gross_row_carries_no_hours(self):
        """The sample's other two print 12 under HOURS and its gross row
        leaves the column empty, which is the one thing on the page that
        says what a gross test is: a level read against a level."""
        c = a_console()
        self.three_results(c)
        rows = {row.split()[0]: row for row in self.rows(c)}
        self.assertEqual(rows["GROSS"][50:56].strip(), "")
        self.assertNotEqual(rows["ANNUAL"][50:56].strip(), "")
        # and every row is the same width, so the columns still line up
        self.assertEqual({len(r) for r in rows.values()}, {64})

    def test_the_computer_format_is_in_the_same_order(self):
        c = a_console()
        self.three_results(c)
        rest = c.leaks_results_record([1])
        self.assertEqual(rest[:4], "0103")
        # tt mm YYMMDDHHmm RR then three floats: forty characters a record
        types = [rest[4 + n * 40:6 + n * 40] for n in range(3)]
        self.assertEqual(types, [leaktest.TYPE_CODE["annual"],
                                 leaktest.TYPE_CODE["periodic"],
                                 leaktest.TYPE_CODE["gross"]])

    def test_the_manifold_byte_is_read_off_the_console(self):
        """"mm - In-Tank Leak Manifold Status: 00=Tank Not Manifolded During
        Leak Test, 01=Tank Manifolded", hardcoded `00` on a console that
        knows its siphon and line manifold sets."""
        c = a_console()
        c.tank_level[2] = {"volume": 5000.0, "water": 0.0}
        self.three_results(c)
        self.assertEqual(c.leaks_results_record([1])[6:8], "00")

        c = a_console()
        c.tank_level[2] = {"volume": 5000.0, "water": 0.0}
        c.manifold_together(1, [2])
        self.three_results(c)
        self.assertEqual(c.leaks_results_record([1])[6:8], "01")

    def test_it_is_what_the_tank_was_when_the_test_ran(self):
        """"During Leak Test". Manifolding a tank afterwards does not change
        a result already on the roll."""
        c = a_console()
        c.tank_level[2] = {"volume": 5000.0, "water": 0.0}
        self.three_results(c)
        c.manifold_together(1, [2])
        self.assertEqual(c.leaks_results_record([1])[6:8], "00")


class EveryTestFrequencySchedulesSomething(unittest.TestCase):
    """FIDELITY H8. `_tank_schedule` returned None for anything but DAILY,
    so ON DATE, ANNUALLY, MONTHLY and WEEKLY were stored, printed on the
    setup report and fired no test -- with their date fields fully modelled
    and the part offsets already worked out.

    576013-635 Rev AA writes the command out one line per method, and the
    schedule sits at offset 4 with the start time behind it: YYMMDD for ON
    DATE, MMWD annually, WD monthly, D weekly, nothing daily.
    """

    # The calendar these run against, rather than whatever day it happens to
    # be. `fires_on` used to open its window on the wall clock, and two of
    # these tests then passed or failed by the date you ran them on: MONTHLY
    # over forty days spans two months only if the window opens BEFORE that
    # month's fire, so from 8 Sep -- a day after the first Monday -- only
    # October fired and the test failed; and ANNUALLY on October needs a
    # window that reaches October at all, which eighty days from March does
    # not. 1 Sep 2026 is a Tuesday, so the first Monday is six days out and
    # every window below opens clear of it.
    START = (2026, 9, 1, 0, 0, 0, 0, 1, -1)

    def fires_on(self, method, schedule, days=40):
        """Which calendar days a tank on this schedule starts a test."""
        c = a_console()
        c.clock_offset = time.mktime(self.START) - time.time()
        c.values["S61101"] = "01" + "12" + "0" + method + schedule + "0600"
        seen = []
        for _ in range(days * 4):
            c.clock_offset += 6 * 3600.0
            c.leaks.tick()
            if ("tank", 1) in c.leaks.running:
                seen.append(time.strftime("%a %d %b %Y", c.now()))
                c.leaks.stop("tank", 1)
        return sorted(set(seen))

    def test_daily_is_every_day(self):
        self.assertGreater(len(self.fires_on("5", "", days=10)), 5)

    def test_weekly_is_the_day_of_week_it_names(self):
        """"D=Day of Week (1=Monday, 2=Tuesday, .. 7=Sunday)", which is the
        same encoding `fieldio.DAYS` reads, starting at MON."""
        for day, name in ((1, "Mon"), (3, "Wed"), (7, "Sun")):
            got = self.fires_on("4", str(day), days=21)
            self.assertTrue(got, f"weekly on {name} fired never")
            for when in got:
                self.assertTrue(when.startswith(name), when)

    def test_monthly_is_that_weekday_in_that_week_of_the_month(self):
        """"W=Week of Month (1-6)", so week 1 is the 1st to the 7th."""
        for week in (1, 2):
            got = self.fires_on("3", f"{week}1", days=40)
            self.assertTrue(got, f"monthly week {week} fired never")
            for when in got:
                self.assertTrue(when.startswith("Mon"), when)
                day = int(when.split()[1])
                self.assertEqual((day - 1) // 7 + 1, week, when)

    def test_monthly_fires_once_a_month(self):
        got = self.fires_on("3", "11", days=40)
        months = {when.split()[2] for when in got}
        self.assertGreater(len(months), 1, "the window should span two")
        self.assertEqual(len(got), len(months), got)

    def test_annually_adds_the_month(self):
        got = self.fires_on("2", "1021", days=80)
        self.assertTrue(got, "an annual test fired never")
        for when in got:
            self.assertIn("Oct", when)
            self.assertTrue(when.startswith("Mon"), when)

    def test_on_date_is_that_one_day(self):
        when = time.localtime(time.mktime(self.START) + 3 * 86400)
        got = self.fires_on("1", time.strftime("%y%m%d", when), days=20)
        self.assertEqual(got, [time.strftime("%a %d %b %Y", when)])

    def test_automatic_and_csld_are_not_on_a_calendar(self):
        """CSLD has its own engine, and AUTOMATIC is H8a -- the manual names
        the condition for the option to be OFFERED, not when it fires."""
        self.assertEqual(self.fires_on("6", "", days=20), [])
        self.assertEqual(self.fires_on("7", "", days=20), [])

    def test_a_malformed_schedule_schedules_nothing(self):
        self.assertEqual(self.fires_on("4", "x", days=14), [])
        self.assertEqual(self.fires_on("2", "99", days=14), [])


class EveryLineOnTheCardIsScheduled(unittest.TestCase):
    """FIDELITY H16. The repetitive test walked `range(1, 5)`, and a PLLD
    card carries six lines."""

    def test_a_repetitive_test_on_line_five_starts(self):
        c = a_console()
        self.assertGreaterEqual(c.capacity("plld"), 5)
        c.values["S78C05"] = "051"
        c.leaks.tick()
        c.clock_offset += 60.0
        c.leaks.tick()
        self.assertIsNotNone(c.leaks.active("plld", 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class WhereTheTestCameFrom(unittest.TestCase):
    """The START slip's second line, which was the STOP MODE flag.

    `TEST BY PROGRAMMED TIME` is on the real 2024 roll against a scheduled
    test, and `TEST BY EXTERN INTERFACE` is 576013-635's own display response
    to function 052 -- Revisions Y and AA and both TLS-450 serial manuals.
    The slip chose between them by reading `manual_stop`, which is what the
    panel's STOP MODE screen sets, so **a technician's own two-hour test
    printed a schedule that does not exist**. See FIDELITY W20 and H1.
    """

    def line(self, c):
        _kind, run = c.leaks.printed_slips[-1]
        out = printer.leak_start_slip(c, run)
        return out[1]

    def test_a_scheduled_test_says_programmed_time(self):
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0, origin="schedule")
        self.assertEqual(self.line(c), "TEST BY PROGRAMMED TIME")

    def test_a_serial_started_test_says_extern_interface(self):
        from tls350sim.wire import Handler
        c = a_console()
        h = Handler(c, verbose=False)
        h.handle(b"\x01S05201\r")
        self.assertTrue(c.leaks.running.get(("tank", 1)))
        self.assertEqual(self.line(c), "TEST BY EXTERN INTERFACE")

    def test_a_panel_started_test_says_neither(self):
        """On no paper this project has seen, so it says nothing -- and the
        line under it is the timestamp, not an invented phrase."""
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        self.assertNotIn("TEST BY", self.line(c))

    def test_a_fixed_length_hand_started_test_claims_no_schedule(self):
        """The defect itself: `manual_stop` False and the START at the
        panel."""
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0, manual_stop=False)
        self.assertNotIn("PROGRAMMED", self.line(c))

    def test_stop_mode_is_still_its_own_flag(self):
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=12.0, manual_stop=True,
                      origin="schedule")
        run = c.leaks.running[("tank", 1)]
        self.assertTrue(run.manual_stop)
        self.assertEqual(run.origin, "schedule")
        self.assertEqual(self.line(c), "TEST BY PROGRAMMED TIME")


class TheEmptyLeakResultSaysTheWholePhrase(unittest.TestCase):
    """FIDELITY D31. `tank_leak_when`, the second line of IN-TANK LEAK
    RESULT's stamp screen, answered an untested tank with `NO TEST DATA` --
    the one place in the package that dropped the second half of the phrase,
    against twenty-six that print it whole.

    576013-818 Rev AA Figure 6-9 draws that line as `MMM DD,YYYY HH:MM:SS xM`
    and draws no empty state at all, so what settles it is the package's own
    vocabulary: 576013-610 Rev AC p85 lists `NO TEST DATA AVAILABLE` among
    the Mag Sump status messages, `controls.py` and `sumpreports.py` both map
    result code `00` to it, and it is 22 characters into a 24 column glass.

    The short one survived a citation audit because it is a PREFIX of the
    long one, so the rule matched it to the same page.
    """

    def line(self, console):
        return console.diag_value("tank_leak_when", 1)

    def test_an_untested_tank_says_the_whole_phrase(self):
        c = a_console()
        self.assertEqual(self.line(c), "NO TEST DATA AVAILABLE")

    def test_and_it_fits_the_glass(self):
        self.assertLessEqual(len("NO TEST DATA AVAILABLE"), 24)

    def test_a_tested_tank_still_answers_with_its_stamp(self):
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        run_out(c, hours=3.0)
        self.assertNotIn("NO TEST DATA", self.line(c))

    def test_nothing_in_the_package_says_the_short_one(self):
        """The count that found it, kept as a ratchet: the bare phrase is a
        defect wherever it appears on its own."""
        import ast
        import glob
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bare = []
        for path in glob.glob(os.path.join(here, "tls350sim", "*.py")):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            docs = {id(ast.get_docstring(n, clean=False) and n.body[0].value)
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.ClassDef,
                                      ast.FunctionDef))}
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)) or id(node) in docs:
                    continue
                for part in node.value.split("NO TEST DATA")[1:]:
                    if not part.startswith((" AVAILABLE", " AVALIABLE")):
                        bare.append(f"{os.path.basename(path)}:"
                                    f"{node.lineno}")
        self.assertEqual(bare, [])


class TheVolumetricLineTestsTakeTheirTableFiveTimes(unittest.TestCase):
    """FIDELITY H17. 576013-849 Rev B p.39, Table 5, "Test Type Reference
    Numbers and Times", gives every VLLD test a `Test Length (Seconds)`:
    13.5 for the 3.0 GPH Line Test, 326 for the 0.2 GPH and 794 for the 0.1
    GPH -- rows 3, 7 and 11, which are the three reference numbers
    `wirelines.VLLD_LINE_TEST` has read out of the LEFT column of the same
    table all along. The right column had never been turned to.

    These were 0.05, 0.75 and 8.0 HOURS, so every test ran 13 to 36 times
    too long and a 0.1 gph line test was eight hours where the page gives
    thirteen minutes.
    """

    def a_line(self):
        c = a_console()
        c.modules["vlld"] = 1
        c.values["S75101"] = "0101"
        return c

    def test_the_three_lengths_are_table_five_s(self):
        self.assertEqual(leaktest.LINE_SECONDS,
                         {"gross": 13.5, "periodic": 326.0, "annual": 794.0})

    def test_a_started_test_runs_for_that_long(self):
        c = self.a_line()
        for rate, seconds in leaktest.LINE_SECONDS.items():
            c.leaks.start("vlld", 1, rate)
            run = c.leaks.running[("vlld", 1)]
            self.assertAlmostEqual(run.hours * 3600.0, seconds, places=6)
            c.leaks.stop("vlld", 1)

    def test_the_two_columns_of_the_table_agree_with_each_other(self):
        """`VLLD_LINE_TEST` is Table 5's reference number and
        `VLLD_TEST_LENGTH` is that row's length, so they are one table and
        have to name the same three rates."""
        from tls350sim import wirelines
        self.assertEqual(set(wirelines.VLLD_LINE_TEST),
                         set(leaktest.LINE_SECONDS))
        self.assertEqual(wirelines.VLLD_TEST_LENGTH, leaktest.LINE_SECONDS)

    def test_a_full_length_line_test_is_not_invalid(self):
        """The validity floor was a flat 0.01 hours -- 36 seconds -- which a
        13.5 second test cannot clear. It is the test's OWN length where
        that is shorter, so a full run is judged and a short one is not."""
        c = self.a_line()
        c.leaks.start("vlld", 1, "gross")
        run_out(c, hours=leaktest.LINE_SECONDS["gross"] / 3600.0 + 0.001)
        result = c.leaks.result("vlld", 1, "gross")
        self.assertEqual(result.result, leaktest.PASSED)

    def test_and_a_test_stopped_part_way_still_is(self):
        c = self.a_line()
        c.leaks.start("vlld", 1, "annual")
        run_out(c, hours=0.0005)
        c.leaks.stop("vlld", 1)
        self.assertEqual(c.leaks.result("vlld", 1, "annual").result,
                         leaktest.INVALID)

    def test_a_tank_test_keeps_the_floor_it_had(self):
        """Its shortest legitimate length is hours, so 0.01 still binds."""
        c = a_console()
        c.leaks.start("tank", 1, "periodic", hours=2.0, manual_stop=True)
        run_out(c, hours=0.005)
        c.leaks.stop("tank", 1)
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.INVALID)
