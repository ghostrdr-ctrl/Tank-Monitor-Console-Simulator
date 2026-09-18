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
"""The vacuum sensor: an interstitial space, and what a manual test says.

576013-818 Figures 6-29 and 6-30 annotate every reading on this sensor,
which is what makes it modellable rather than a placeholder list:

    LEAK RATE       "Rate in gph at which air is entering the interstitial
                     space. A Vac Warning Alarm will be posted if this rate
                     is >22.4 gph."
    TIME TO NO VAC  "Predicted time (in hours : minutes) it would take for
                     the interstitial pressure to equal -1 psi. A Vac Warning
                     Alarm will be posted if this rate is <8 hours."
    EVAC RATIO      "A Evac Ratio >1.0 is required or evacuation will abort."

Three of the screens were the manual's placeholder shipped as the screen,
two of them with no value on them at all. FIDELITY D1.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console, DIAG_MENU


def a_vac_site(leak=0.0):
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("smart", 1)
    c.values["S72301"] = "0104"                 # smart sensor category 04
    c.values["S72201"] = "01STP SUMP VAC       "
    c.vac_leak[1] = leak
    return c


def a_small_zone(leak=0.0):
    """577013-836 Rev N p.4-2's worked zone, 21.9 gallons, programmed."""
    from tls350sim import fieldio
    from tls350sim.console import FIELDS
    c = a_vac_site(leak=leak)
    c.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "21.9")
    return c


def with_a_result(c, number=1):
    c.start_vac_test(number)
    c.finish_vac_tests()
    return c


class TheSmartSensorFamilyIsTwoCards(unittest.TestCase):
    """FIDELITY M2. 577013-750 Rev AK p.49's secondary containment
    compatibility table lists both against the TLS-350 -- "0329356-004 | 8
    Input Smart Sensor Interface Module" and "0332250-001 | 7 Input Smart
    Sensor/Pressure Module | 1 for up to 7 Vac Sensors" -- and this cage had
    the eight-input one alone.

    The smaller card matters beyond one more row: 576013-623 Rev AN p.26-3
    says what its eighth channel is doing. "The atmospheric pressure [ATMP]
    sensor is resident in the Smart Sensor / Press Module. One ATMP sensor
    is required with Vac Sensor systems per site."
    """

    def a_cage(self):
        c = Console()
        c.modules["rs232"] = 1
        c.set_module("smart", 1)
        return c

    def test_the_default_card_is_the_eight_input_one(self):
        c = self.a_cage()
        self.assertFalse(c.smart_press)
        self.assertEqual(c.wires("smart"), 8)
        self.assertEqual(c.capacity("smart"), 8)
        self.assertEqual(c.card("smart")[1], "329356-004")

    def test_the_press_module_carries_seven(self):
        """"Smart Sensor / Press Modules (7 inputs)", p.26-1."""
        c = self.a_cage()
        c.smart_press = True
        self.assertEqual(c.wires("smart"), 7)
        self.assertEqual(c.capacity("smart"), 7)
        self.assertEqual(c.card("smart"),
                         ("Seven-Input Smart Sensor/Pressure Module",
                          "332250-001", 499000))

    def test_the_config_screen_draws_the_cards_own_positions(self):
        """"In this display you tell the system which positions on the
        module have been connected to sensors"."""
        c = self.a_cage()
        self.assertEqual(len(c.slot_text("721").split()), 8)
        c.smart_press = True
        self.assertEqual(len(c.slot_text("721").split()), 7)

    def test_the_slot_reads_the_cards_own_id_resistor(self):
        """576013-818 Table 6-1: "8-Input Smart Sensor 39.2K" and "Smart
        Sensor / Press Module 499K"."""
        c = self.a_cage()
        por, _now = c.module_id_resistance("smart", 1)
        self.assertLess(abs(por - 39200) / 39200.0, 0.05)
        c.smart_press = True
        por, _now = c.module_id_resistance("smart", 1)
        self.assertLess(abs(por - 499000) / 499000.0, 0.05)

    def test_the_card_survives_a_cold_boot(self):
        """The cage is re-scanned at power-up: a cold boot loses the
        programming, not which card is in the bay."""
        c = self.a_cage()
        c.smart_press = True
        c.cold_boot()
        self.assertTrue(c.smart_press)
        self.assertEqual(c.wires("smart"), 7)

    def test_every_other_card_keeps_its_own_count(self):
        c = self.a_cage()
        c.smart_press = True
        c.set_module("liquid", 1)
        self.assertEqual(c.wires("liquid"), 8)
        self.assertEqual(len(c.slot_text("701").split()), 8)


class SmartSensorSetupWalksItsTwoSubWalks(unittest.TestCase):
    """FIDELITY M3. SMART SENSOR SETUP had Config, Label and Category and
    stopped, where 576013-623 Rev AN chapter 26 carries two sub-walks under
    it -- the Mag sensor's thresholds and the Vac sensor's plumbing. Four
    function codes had no way onto the panel and two had no field at all.
    """

    def steps(self, c, device=1):
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU
              if f["function"] == "SMART SENSOR SETUP"][0]
        return [st["text"] for st in c.visible_steps(fn, device)]

    def a_smart_site(self, category):
        c = Console()
        c.modules["rs232"] = 1
        c.set_module("smart", 1)
        c.values["S72301"] = "01" + category
        return c

    def test_an_unprogrammed_sensor_gets_neither_walk(self):
        """"If the Mag Sump Sensor you installed has NO programmable
        features ... you will not be able to enter Mag Sensor Setup after
        entering Smart Sensor label and Sensor Category"."""
        got = self.steps(self.a_smart_site("00"))
        self.assertEqual(len(got), 3, got)
        self.assertNotIn("Mag Sensor Setup", got)
        self.assertNotIn("Vac Sensor Setup", got)

    def test_a_mag_sensor_walks_the_delay_and_the_two_heights(self):
        """p.26-3: `s 1: MAG SENSOR SETUP / PRESS <ENTER>`, then
        `ALM UPGRADE DELAY: XXXX`, then `s 1: WATER WARNING / WATER HT >
        0002.0` and `s 1: WATER ALARM / WATER HT > 0005.0`."""
        got = self.steps(self.a_smart_site("03"))
        self.assertEqual(got[3:], [
            "Mag Sensor Setup",
            "Alm Upgrade Delay (up to 9999 hours, 0 disables)",
            "Water Warning Height", "Water Alarm Height"])

    def test_the_two_heights_are_two_records_of_one_function(self):
        """576013-635 Rev AA p.329: "AA - Alarm Definition Record ID", and
        its sample's table makes 2 the water warning and 3 the water
        alarm."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = self.a_smart_site("03")
        for name, value in (("warning", "3.5"), ("alarm", "7.5")):
            c.values["S72801"] = fieldio.encode(
                FIELDS[f"S72801.{name}"], "S72801", value,
                c.values.get("S72801"))
        self.assertEqual(
            fieldio.decode(FIELDS["S72801.warning"], "S72801",
                           c.values["S72801"]), "3.5")
        self.assertEqual(
            fieldio.decode(FIELDS["S72801.alarm"], "S72801",
                           c.values["S72801"]), "7.5")

    def test_a_height_outside_the_sensors_own_limits_is_refused(self):
        """"Refer to your Smart Sensor setup printout's Min/Max Thresholds
        for the permissible range": the chapter's own printout carries
        `MIN THRESHOLD 0.0` and `MAX THRESHOLD 24.0`."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        f = FIELDS["S72801.warning"]
        self.assertIsNotNone(fieldio.encode(f, "S72801", "24"))
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S72801", "25")

    def test_a_vac_sensor_walks_its_pump_volume_and_relief_valve(self):
        """p.26-4, and the relief valve pressure appears only once the
        valve does: "If this Vac Sensor is monitoring a fiberglass tank's
        interstitial space, a relief valve is required"."""
        c = self.a_smart_site("04")
        got = self.steps(c)
        self.assertEqual(got[3:], [
            "Vac Sensor Setup", "Select Pump # (PLLD/WPLLD/Output Relay)",
            "Interstitial Volume (0.1 to 500 gallons)",
            "Relief Valve (Yes/No)"])
        c.values["S72B01"] = "011"
        self.assertIn("Relief Valve Pressure (-5 to -9 PSI)", self.steps(c))

    def test_the_pump_choices_are_this_sites_own_devices(self):
        """"press CHANGE until the correct pump's control device displays
        [QX (PLLD), WX (WPLLD), or RX (Output Relay)]", and 729's sample
        draws one as `Q 1:UNLEADED REGULAR`."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = self.a_smart_site("04")
        self.assertEqual(fieldio.choices_of(FIELDS["S72901"], c),
                         [("0000", "NONE")])
        c.set_module("plld", 1)
        c.values["S78101"] = "011"
        c.values["S78201"] = "01UNLEADED REGULAR   "
        self.assertEqual(fieldio.choices_of(FIELDS["S72901"], c),
                         [("0000", "NONE"), ("2101", "Q 1:UNLEADED REGULAR")])
        data = fieldio.encode(FIELDS["S72901"], "S72901",
                              "Q 1:UNLEADED REGULAR", None, False, c)
        self.assertEqual(data, "012101")
        self.assertEqual(fieldio.decode(FIELDS["S72901"], "S72901", data, c),
                         "Q 1:UNLEADED REGULAR")

    def test_a_volume_the_manual_refuses_is_refused(self):
        """"The permitted range is 1 to 500 gallons. Default is 501." The
        max was 501 -- the sentinel the console holds until somebody enters
        one, which is not a value a site can enter."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        f = FIELDS["S72A01"]
        self.assertIsNotNone(fieldio.encode(f, "S72A01", "500"))
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S72A01", "501")

    def test_and_the_floor_is_the_sensors_own_tenth_of_a_gallon(self):
        """576013-623 Rev AN says 1 to 500; the sensor's own manuals say
        "The permitted range is 0.1 to 500 gallons (0.378 to 1892.7
        litres)" -- 577013-836 Rev N p.4-4 and 577013-873 Rev E p.4-2 --
        and 836's worked example is a 2.88 gallon sump zone, so the tenth
        is the bound. UNKNOWNS B25."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        f = FIELDS["S72A01"]
        self.assertIsNotNone(fieldio.encode(f, "S72A01", "0.1"))
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S72A01", "0")
        c = self.a_smart_site("04")
        c.values["S72901"] = "012101"
        c.values["S72302"] = "0205"
        c.values["S72A01"] = fieldio.encode(f, "S72A01", "0.5")
        self.assertNotIn("280101", c.setup_warnings())

    def test_the_three_vac_setup_warnings_the_chapter_states(self):
        """"You must select the pump ... or a Setup Data Warning will be
        posted for this Vac Sensor"; "A Setup Data Warning alarm will
        activate if a volume between 1 and 500 is not entered"; "A Setup
        Data Warning will be posted for all Vac Sensors if an ATMP sensor
        is not present and configured"."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = self.a_smart_site("04")
        c.set_module("plld", 1)
        c.values["S78101"] = "011"
        c.values["S78201"] = "01UNLEADED REGULAR   "
        self.assertIn("280101", c.setup_warnings())

        c.values["S72901"] = fieldio.encode(
            FIELDS["S72901"], "S72901", "Q 1:UNLEADED REGULAR", None,
            False, c)
        self.assertIn("280101", c.setup_warnings())      # still no volume
        c.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "200")
        self.assertIn("280101", c.setup_warnings())      # still no ATMP
        c.values["S72302"] = "0205"                      # an ATMP sensor
        self.assertNotIn("280101", c.setup_warnings())

    def test_a_sensor_that_is_not_a_vac_sensor_is_not_asked(self):
        c = self.a_smart_site("03")
        self.assertEqual(c.vac_setup_warnings(), [])


class TheFigureSOwnNumbersReplay(unittest.TestCase):
    """The strongest thing that can be said for a derived constant is that
    the manual's own worked values come back out of it."""

    def test_the_leak_rate_line_is_figure_6_29_character_for_character(self):
        c = with_a_result(a_vac_site(leak=0.123))
        self.assertEqual(c.diag_value("vac_rate", 1).split(chr(10))[1],
                         "LEAK RATE:    0.123 GPH")

    def test_and_the_time_it_predicts_is_the_zone_volumes(self):
        """This used to read back Figure 6-29's `150:20` from a derived
        44.27 gallon constant. 577013-873 Rev E p.4-11 prints the same
        figure with `100:00` in that cell, so the number was typesetting,
        and 577013-836 Rev N p.4-2 says where the volume really comes from:
        "you would enter 21.9 (round to nearest tenth of a gallon) as the
        calculated zone volume". With that zone, 0.123 gph, and Figure
        6-30's -7.14 psi, t = (-1 - P) * V / (14.7 * R) is 74.37 hours."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = a_vac_site(leak=0.123)
        c.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "21.9")
        with_a_result(c)
        self.assertEqual(c.diag_value("vac_no_vac", 1).split(chr(10))[1],
                         "NO VAC TIME:       74:22")

    def test_and_the_ratio(self):
        c = with_a_result(a_vac_site(leak=0.123))
        line = c.diag_value("vac_ratio", 1).split(chr(10))[1]
        self.assertTrue(line.startswith("EVAC RATIO:5.2 @"), line)

    def test_every_result_line_fits_the_display(self):
        c = with_a_result(a_vac_site(leak=0.123))
        for token in ("vac_state", "vac_rate", "vac_no_vac", "vac_ratio"):
            for line in c.diag_value(token, 1).split(chr(10)):
                self.assertLessEqual(len(line), 24, (token, line))


class TheInterstitialSpaceFills(unittest.TestCase):
    def test_a_sensor_with_no_leak_holds_its_vacuum(self):
        c = a_vac_site()
        c.vac_tick(100.0)
        self.assertAlmostEqual(c.vac_psi(1), c.VAC_HELD_PSI)
        self.assertEqual(c.vac_conditions(), [])

    def test_air_getting_in_raises_the_pressure(self):
        """Air at atmosphere into a fixed volume: 14.7 psi for every
        interstitial volume of it."""
        c = a_vac_site(leak=1.0)
        was = c.vac_psi(1)
        c.vac_tick(1.0)
        self.assertAlmostEqual(c.vac_psi(1) - was,
                               14.7 / c.vac_volume(1), places=6)

    def test_it_stops_at_atmosphere(self):
        c = a_vac_site(leak=50.0)
        c.vac_tick(100.0)
        self.assertEqual(c.vac_psi(1), 0.0)

    def test_the_prediction_shortens_as_the_space_fills(self):
        c = a_vac_site(leak=1.0)
        first = c.vac_time_to_no_vac(1)
        c.vac_tick(2.0)
        self.assertLess(c.vac_time_to_no_vac(1), first)

    def test_a_space_that_is_not_filling_has_no_predicted_time(self):
        """The manual gives the reading a value and a threshold and says
        nothing about a space that is not filling, so the console does not
        predict a moment that never arrives."""
        c = with_a_result(a_vac_site(leak=0.0))
        self.assertIsNone(c.vac_time_to_no_vac(1))
        self.assertEqual(c.diag_value("vac_no_vac", 1).split(chr(10))[1],
                         "NO VAC TIME:      ---:--")


class TheVolumeIsTheZonesOwn(unittest.TestCase):
    """577013-836 Rev N p.4-2: "Use the Containment Volume index to
    calculate a zone's interstice volume in gallons ... you would enter 21.9
    (round to nearest tenth of a gallon) as the calculated zone volume."
    The space the console works its leak rate against is S72A, not a
    constant of the sensor. UNKNOWNS A27."""

    def test_an_unprogrammed_sensor_holds_the_manuals_501(self):
        """"Default is 501", which is also the state its Setup Data Warning
        is posted in."""
        c = a_vac_site()
        self.assertEqual(c.vac_volume(1), 501.0)

    def test_a_programmed_volume_is_the_one_the_physics_uses(self):
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = a_vac_site(leak=1.0)
        c.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "21.9")
        self.assertAlmostEqual(c.vac_volume(1), 21.9, places=4)
        was = c.vac_psi(1)
        c.vac_tick(1.0)
        self.assertAlmostEqual(c.vac_psi(1) - was, 14.7 / 21.9, places=6)

    def test_a_larger_volume_is_a_longer_time_for_the_same_leak(self):
        """"if the volume is programmed significantly larger than the
        actual volume, a small leak will be calculated as being much larger
        by the TLS" -- 577013-873 Rev E p.4-5. The console holds the rate
        here and the bench sets it, so the same sentence reads the other
        way round: the bigger the space, the longer the same leak takes."""
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        small = a_vac_site(leak=1.0)
        small.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "20")
        big = a_vac_site(leak=1.0)
        big.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "400")
        self.assertAlmostEqual(big.vac_time_to_no_vac(1),
                               20 * small.vac_time_to_no_vac(1))


class TheGuidesOwnPostingConditions(unittest.TestCase):
    """577013-873 Rev E p.4-4: "A Vacuum Warning will be posted under the
    following conditions: Leak Rate > 22.4 GPH for 40 minutes; Evacuation
    ratio of less than 1.0 during a manual evacuation when vacuum level has
    not reached -4.0." And p.4-6 on the alarm: "posted when compensated
    pressure is greater than -1.0 psi ... The alarm will clear when vacuum
    pressure is less than -1.1 psi"."""

    def a_wide_zone(self, leak):
        from tls350sim import fieldio
        from tls350sim.console import FIELDS
        c = a_vac_site(leak=leak)
        c.values["S72A01"] = fieldio.encode(FIELDS["S72A01"], "S72A01", "500")
        return c

    def test_over_22_4_gph_is_not_a_warning_until_forty_minutes(self):
        c = self.a_wide_zone(leak=22.5)         # 9.3 hours of vacuum left
        self.assertGreater(c.vac_time_to_no_vac(1), c.VAC_TIME_WARN_HOURS)
        self.assertEqual(c.vac_conditions(), [])
        c.clock_offset += 39 * 60
        self.assertEqual(c.vac_conditions(), [])
        c.clock_offset += 60
        self.assertEqual(c.vac_conditions(), ["281601"])

    def test_a_rate_that_drops_back_starts_the_forty_minutes_again(self):
        c = self.a_wide_zone(leak=22.5)
        c.vac_conditions()
        c.clock_offset += 30 * 60
        c.vac_leak[1] = 10.0
        self.assertEqual(c.vac_conditions(), [])
        c.vac_leak[1] = 22.5
        c.clock_offset += 30 * 60
        self.assertEqual(c.vac_conditions(), [])

    def test_a_manual_evacuation_that_makes_no_headway_is_a_warning(self):
        """"not 'making headway' (the vacuum level is not increasing or it
        is increasing very slowly as indicated by an 'Evac Ratio' less than
        1.0)", p.4-4 -- and only while the space is still above -4.0."""
        c = self.a_wide_zone(leak=2.0)          # ratio 0.32
        c.vac_pressure[1] = -3.0
        self.assertEqual(c.vac_conditions(), [])
        with_a_result(c)
        self.assertLess(c.vac_result(1)["ratio"], 1.0)
        self.assertEqual(c.vac_conditions(), ["281601"])
        c.vac_pressure[1] = -5.0                # past -4.0: the test reads clean
        with_a_result(c)
        self.assertEqual(c.vac_conditions(), [])

    def test_no_vacuum_posts_above_minus_one_and_clears_below_minus_one_point_one(self):
        c = self.a_wide_zone(leak=0.0)
        c.vac_pressure[1] = -0.5
        self.assertEqual(c.vac_conditions(), ["281701"])
        c.vac_pressure[1] = -1.05               # between the two: still posted
        self.assertEqual(c.vac_conditions(), ["281701"])
        c.vac_pressure[1] = -1.2                # "the 'Vacuum OK' threshold"
        self.assertEqual(c.vac_conditions(), [])
        c.vac_pressure[1] = -1.05               # and from below it is not
        self.assertEqual(c.vac_conditions(), [])


class TheTwoStatedThresholds(unittest.TestCase):
    def test_under_eight_hours_left_is_a_vacuum_warning(self):
        c = a_small_zone(leak=3.0)              # ~3 hours from -7.14 psi
        self.assertLess(c.vac_time_to_no_vac(1), c.VAC_TIME_WARN_HOURS)
        self.assertEqual(c.vac_conditions(), ["281601"])

    def test_a_healthy_space_raises_nothing(self):
        c = a_vac_site(leak=0.123)              # 150 hours of margin
        self.assertEqual(c.vac_conditions(), [])

    def test_above_minus_one_psi_is_the_no_vacuum_alarm(self):
        """"No Vacuum Alarm above -1 psi", which was in this file's own
        comment on SMART_CATEGORY_STATES and produced by nothing."""
        c = a_vac_site(leak=50.0)
        c.vac_tick(100.0)
        self.assertEqual(c.vac_conditions(), ["281701"])

    def test_the_no_vacuum_alarm_replaces_the_warning_rather_than_joins_it(self):
        c = a_vac_site(leak=50.0)
        c.vac_tick(100.0)
        self.assertNotIn("281601", c.vac_conditions())

    def test_the_alarms_reach_the_console(self):
        c = a_small_zone(leak=3.0)
        self.assertIn("281601", c.compute_alarms())

    def test_a_sensor_that_is_not_a_vac_sensor_has_none_of_this(self):
        c = a_vac_site(leak=50.0)
        c.values["S72301"] = "0103"             # a Mag sensor instead
        self.assertEqual(c.vac_sensors(), [])
        self.assertEqual(c.vac_conditions(), [])


class TheManualTest(unittest.TestCase):
    def test_the_result_screens_say_nothing_until_a_test_has_run(self):
        """Which drops the renderer back on the figure's own placeholder
        line rather than a blank -- D2's rule."""
        c = a_vac_site(leak=0.123)
        for token in ("vac_rate", "vac_no_vac", "vac_ratio"):
            self.assertEqual(c.diag_value(token, 1), "", token)

    def test_a_test_stopped_by_hand_writes_nothing(self):
        """There is no "test complete" screen anywhere in Figure 6-29: the
        walk offers START and STOP, and STOP's acknowledgement reads
        ABORTED."""
        c = a_vac_site(leak=0.123)
        c.start_vac_test(1)
        self.assertEqual(c.stop_vac_test(1), "s 1: MANUAL TEST ABORTED")
        c.finish_vac_tests()
        self.assertIsNone(c.vac_result(1))

    def test_the_record_is_the_state_when_the_reading_was_taken(self):
        c = a_vac_site(leak=1.0)
        with_a_result(c)
        first = dict(c.vac_result(1))
        c.vac_leak[1] = 4.0
        c.vac_tick(1.0)
        self.assertEqual(c.vac_result(1), first)   # still the old test
        with_a_result(c)
        self.assertNotEqual(c.vac_result(1), first)

    def test_the_stamp_is_when_the_reading_was_taken(self):
        c = with_a_result(a_vac_site(leak=0.123))
        head = c.diag_value("vac_rate", 1).split(chr(10))[0]
        self.assertRegex(head, r"^s 1: \d\d-\d\d-\d\d  [ \d]\d:\d\d [AP]M$")

    def test_the_panel_runs_it_and_the_figure_acknowledges(self):
        c = a_vac_site(leak=0.123)
        self.assertEqual(c.diag_action("vac_test_start", 1),
                         "ALL: MANUAL TEST STARTED")
        c.finish_vac_tests()
        self.assertIsNotNone(c.vac_result(1))
        self.assertEqual(c.diag_action("vac_test_stop", 1),
                         "ALL: MANUAL TEST ABORTED")

    def test_evac_hold_opens_the_valve_and_the_screen_says_so(self):
        c = a_vac_site()
        self.assertIn("VCV: CLOSED", c.diag_value("vac_state", 1))
        self.assertEqual(c.diag_action("vac_hold_start", 1),
                         "ALL: EVAC HOLD STARTED")
        self.assertIn("VCV: OPEN", c.diag_value("vac_state", 1))
        self.assertEqual(c.diag_action("vac_hold_stop", 1),
                         "ALL: EVAC HOLD ABORTED")
        self.assertIn("VCV: CLOSED", c.diag_value("vac_state", 1))

    def test_the_state_screen_follows_the_pressure(self):
        c = a_vac_site(leak=50.0)
        self.assertTrue(c.diag_value("vac_state", 1).startswith("s 1: VACUUM OK"))
        c.vac_tick(100.0)
        self.assertTrue(c.diag_value("vac_state", 1).startswith("s 1: NO VACUUM"))

    def test_the_four_run_screens_are_the_figure_s_four(self):
        fn = [f for f in DIAG_MENU
              if f["function"] == "SMART SENSOR DIAGNOSTIC"][0]
        runs = {sc["l1"]: sc["run"] for sc in fn["screens"] if sc.get("run")}
        self.assertEqual(runs, {
            "START MANUAL TEST: ALL": "vac_test_start",
            "STOP MANUAL TEST: ALL": "vac_test_stop",
            "START EVAC HOLD: ALL": "vac_hold_start",
            "STOP EVAC HOLD: ALL": "vac_hold_stop"})

    def test_with_no_vac_sensor_the_action_says_so(self):
        c = a_vac_site()
        c.values["S72301"] = "0103"
        self.assertEqual(c.diag_action("vac_test_start", 1), "NO VAC SENSORS")


class ThePortReportsTheSensorTheConsoleModels(unittest.TestCase):
    """FIDELITY L18. Every number in `B38` belongs to the vacuum sensor and
    this console models all of them for the panel -- and `_vac_diagnostic`
    read none of them. Both pressures were a `readings.wander` band near -9
    psi whatever the bench was doing; the valve was CLOSED unless the
    evacuation state was one the code never set, so an evac hold read
    `VCV: OPEN` on the screen and `VCV: CLOSED` on the port; and the leak
    rate, the minutes to no vacuum and the ratio were generated, so a sensor
    that had never run a manual test answered with all three.
    """

    def report(self, c, code=b"\x01IB3801\r"):
        from tls350sim.wire import Handler
        return Handler(c, verbose=False).handle(code).decode("ascii",
                                                             "replace")

    def test_the_two_pressures_are_the_sump_the_bench_is_driving(self):
        c = a_vac_site(leak=1.0)
        c.vac_pressure[1] = -0.4
        lines = self.report(c).splitlines()
        self.assertIn("              -0.400 PSI", lines)
        self.assertIn("-0.40 PSI", c.diag_value("vac_state", 1))

    def test_the_uncompensated_reading_is_the_atmosphere_added_back(self):
        """"COMPENSATED PRESSURE ... Pressure sensor value minus ATMP sensor
        value", 576013-818 Rev AB Figure 6-29 -- so the uncompensated one is
        the compensated one plus whatever the ATM P sensor reads."""
        from tls350sim import wiresensors
        c = a_vac_site()
        c.values["S72302"] = "0205"                  # an ATMP sensor on 2
        atm = float(c.diag_reading("ss_atm", 2).split(":")[1].split()[0])
        low, high = wiresensors._vac_pressures(c, 1)
        self.assertAlmostEqual(low, c.vac_psi(1), places=6)
        self.assertAlmostEqual(high - low, atm, places=6)

    def test_the_valve_on_the_port_is_the_valve_on_the_glass(self):
        c = a_vac_site()
        c.start_evac_hold(1)
        lines = self.report(c).splitlines()
        self.assertIn("VCV: OPEN", lines)
        self.assertIn(" EVACUATION HOLD", lines)
        self.assertIn("VCV: OPEN", c.diag_value("vac_state", 1))

    def test_a_sensor_with_no_test_sends_three_zero_validity_flags(self):
        """"V - Valid Leak Rate flag ... v - Valid Time to No Vacuum flag ...
        f - Valid Evac Ratio flag", 576013-635 Rev AA pp.531-532. All three
        were hard-coded 1, which is the console asserting three measurements
        it had not made."""
        c = a_vac_site(leak=1.0)
        self.assertEqual(self.flags(c), ["0", "0", "0"])
        with_a_result(c)
        self.assertEqual(self.flags(c), ["1", "1", "1"])

    def test_a_space_that_is_not_filling_has_no_time_and_no_ratio(self):
        """Two of the three are valid and one is not: the rate was measured
        and it was zero, which is not the same as no prediction."""
        c = with_a_result(a_vac_site(leak=0.0))
        self.assertEqual(self.flags(c), ["1", "0", "0"])

    def flags(self, c):
        """V, v and f out of the computer response, by their offsets: the
        block is SS NNNNNNNN e F c V, then a stamp, the rate and v, then a
        stamp, the minutes and f. 576013-635 Rev AA p.531."""
        raw = self.report(c, b"\x01iB3801\r").strip("\x01\x03\r\n")
        body = raw.split("&&")[0][len("iB3801") + 10:]
        return [body[13], body[32], body[51]]

    def test_the_display_form_dashes_what_it_has_not_measured(self):
        """Not a page: 576013-818 draws Figure 6-29 fully populated and no
        manual on this shelf says what an unrun test looks like. The dashes
        are the panel's own form, so the screen and the paper agree, and the
        date line goes with the reading it stamps. UNKNOWNS A73."""
        lines = self.report(a_vac_site(leak=1.0)).splitlines()
        self.assertIn("LEAK RATE:       --- GPH", lines)
        self.assertIn("          ---:-- HHHH:MM", lines)
        self.assertIn("EVAC RATIO:--- @ ---PSI", lines)
        self.assertEqual(self.stamps(lines), [])

    def stamps(self, lines):
        """The date lines the reading block stands in front of its readings,
        `4-12-04 11:28AM` -- not the response's own header stamp, which is
        `JAN 22, 2004  3:25 PM` and is there whatever the sensor has done."""
        import re
        return [l for l in lines if re.match(r"^\d\d-\d\d-\d\d ", l)]

    def test_the_stamp_is_when_the_reading_was_taken(self):
        """It was an offset from now, so every report was stamped with the
        hour it was asked for rather than the hour of the test."""
        import time
        c = with_a_result(a_vac_site(leak=1.0))
        took = time.localtime(c.vac_result(1)["at"])
        c.clock_offset += 6 * 3600.0                 # six hours later
        want = time.strftime("%m-%d-%y %I:%M%p", took)
        self.assertEqual(self.stamps(self.report(c).splitlines()),
                         [want, want])

    def test_the_wire_s_own_evac_hold_reaches_the_panel(self):
        """097 wrote a phase and nothing else, so an evacuation hold started
        over the port left the valve shut on the glass and on B38."""
        c = a_vac_site()
        self.report(c, b"\x01S09701149\r")
        self.assertTrue(c.vac_valve_open(1))
        self.assertIn("VCV: OPEN", c.diag_value("vac_state", 1))
        self.report(c, b"\x01S09801149\r")
        self.assertFalse(c.vac_valve_open(1))

    def test_the_wire_s_own_manual_test_records_a_result(self):
        c = a_vac_site(leak=1.0)
        self.assertIsNone(c.vac_result(1))
        self.report(c, b"\x01S09501149\r")
        c.finish_vac_tests()
        self.assertIsNotNone(c.vac_result(1))


class TheAtmPressureIsADifference(unittest.TestCase):
    """FIDELITY D11. 576013-818 Rev AB Figure 6-32 says what the reading is:
    "Current atmospheric pressure relative to sea level as measured by the
    ATM P Sensor", over `ATM PRESSURE:      0.062 PSI`, and both revisions
    of 576013-635 print the same figure in `B37`.

    This console returned one atmosphere, absolute -- `ATM PRESSURE: 14.433
    PSI`, a different quantity two hundred times the size. The band is the
    simulator's (UNKNOWNS A28); what the manual settles is the ORDER, and
    that is what this asserts rather than a number no page carries.
    """

    def an_atmp_site(self):
        c = a_vac_site()
        c.values["S72301"] = "0105"             # ATMP is smart category 05
        return c

    def test_it_is_tenths_of_a_psi_and_not_one_atmosphere(self):
        c = self.an_atmp_site()
        for number in range(1, 5):
            text = c.diag_reading("ss_atm", number)
            psi = float(text.split(":")[1].split()[0])
            self.assertLess(psi, 1.0, text)
            self.assertGreater(psi, 0.0, text)

    def test_the_sign_is_positive_because_the_citation_audit_needs_it(self):
        """The one sample on the shelf is positive, and a console drawing
        `ATM PRESSURE: -0.140 PSI` would be drawing a line no page carries.
        `test_citations` asserts zero uncited, so the band's lower bound is
        part of that audit rather than a taste."""
        from tls350sim.console import ATM_BAND
        self.assertGreater(ATM_BAND[0], 0.0)
        self.assertLess(ATM_BAND[1], 1.0)

    def test_the_screen_and_the_wire_read_the_same_sensor(self):
        """`wiresensors`' B37 takes its number off this screen, so one fix
        serves both -- which is only true while it keeps doing that."""
        from tests.test_console import held_clock
        from tls350sim.wire import Handler
        c = self.an_atmp_site()
        # Both readings at one instant. This test and its twin in
        # `test_console.py` were written from the same template and have the
        # same race: the value moves about once a second and they ask for it
        # twice. See `held_clock` and FIDELITY V5.
        with held_clock():
            screen = c.diag_reading("ss_atm", 1).split(":")[1].split()[0]
            report = Handler(c, verbose=False).handle(b"\x01IB3701\r")
        self.assertIn(screen.encode(), report)


if __name__ == "__main__":
    unittest.main()
