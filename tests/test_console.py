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
"""What the console must get right, tested without a window.

Everything here was verified by hand against the manuals first; these lock it
in. Run with `python -m unittest discover tests` from the project root.
"""
import contextlib
import os
import struct
import sys
import time
import unittest
import unittest.mock

from tests.test_controls import send

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import (console, csld, fieldio, leaktest,    # noqa: E402
                       packed, presets, printer, wirelists)
from tls350sim.console import (Console, DIAG_MENU, FIELDS,  # noqa: E402
                               NORMAL_MENU, SENSOR_STATE_NN, SETUP_MENU,
                               SOFTWARE_MODULES, describe_alarms)
from tls350sim.wire import Handler, parse_command           # noqa: E402

SOH, ETX, CR = chr(1), chr(3), chr(13)
NOT_UNDERSTOOD_TEXT = SOH + "9999FF1B" + ETX
SEP = chr(13) + chr(10)          # display format is CRLF between lines


def float_value(v):
    return struct.pack(">f", v).hex().upper()


def fitted():
    c = Console()
    # an NVMEM203 board, the one configuration of the manual's table that
    # carries Maintenance Tracker and ISD as well as everything else
    c.board = "E6"
    for key in ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart",
                "plld", "wplld", "vlld", "io", "relay", "pump", "pumpmon",
                "vmc", "mt4", "modem"):
        c.modules[key] = 1
    # and no RS-232 card, because the dual-port module in slot 4 carries an
    # RS-232 port on one of its two positions: the comm bay is four slots
    # and a fifth card would have nowhere to go.
    c.modules["rs232"] = 0
    # The comm bay is FOUR slots, three of them single-port, so a console
    # cannot carry a modem, a VMCI, an EDIM, an RS-232 port and a
    # Maintenance Tracker port as five separate cards. The dual-port MT
    # module 330586-017 is what a real site does about that: it takes slot 4
    # and answers on two positions, a general serial port on 5 and the
    # Maintenance Tracker on 6. See FIDELITY M7.
    for option, _name, _part in SOFTWARE_MODULES:
        c.software[option] = True
    return c


def start_at(console, hour):
    """Put the console's clock at a fixed hour of today.

    A test that runs the clock forward from whenever it happens to be run is
    a test that fails at midnight: advance six hours from 00:10 and the
    console crosses AUTOMATIC DAILY CLOSING at 2:00 AM, closes the BIR day,
    and the sales the test is counting go with it.
    """
    now = time.localtime()
    console.clock_offset = ((hour - now.tm_hour) * 3600
                            - now.tm_min * 60 - now.tm_sec)


@contextlib.contextmanager
def held_clock():
    """Hold the wall clock still for the length of a block.

    `start_at` above says WHICH HOUR the console thinks it is, and that is a
    different failure mode from this one. Every live reading on this console
    is a function of the clock and the clock is the wall clock, so a test
    that reads ONE value from two surfaces reads it at two instants and can
    see it change in between. Measured on the vapour concentration: 741,067
    reads over six seconds of wall clock returned SEVEN different numbers,
    about one change a second, and a test that asks the panel and then asks
    the wire loses that race whenever a change falls between the two.

    It is not a hypothetical -- it is the whole suite's one intermittent
    failure, and it took a full run to see once. See FIDELITY V5.
    """
    held = time.time()
    with unittest.mock.patch("time.time", lambda: held):
        yield


def a_tank(c, tank=1, volume=5000.0, water=0.0, full=10000.0):
    c.values[f"S60A{tank:02d}"] = f"{tank:02d}" + float_value(full)
    c.tank_level[tank] = {"volume": volume, "water": water}


class Menus(unittest.TestCase):
    def test_every_wired_step_has_a_field(self):
        for fn in SETUP_MENU:
            for st in fn["steps"]:
                if not st.get("code"):
                    continue
                fid = st.get("field") or st["code"]
                self.assertIn(fid, FIELDS,
                              f"{fn['function']} / {st['text'][:30]}")

    def test_codes_are_well_formed(self):
        for fn in SETUP_MENU:
            for st in fn["steps"]:
                code = st.get("code")
                if not code:
                    continue
                self.assertRegex(code, r"^S[0-9A-F]{3}[0-9]{2}$", code)

    def test_parts_of_one_function_do_not_overlap(self):
        """Two fields of one function must not claim the same bytes.

        Unless they are ALTERNATIVES: function code 611 packs a leak test
        schedule whose shape depends on the method, so the on-date, annual,
        monthly and weekly fields all start at offset 4 and only one of them
        is ever present. Those say so with `alt`, and are checked against
        each other only for sharing the same `alt` group.
        """
        claimed = {}
        for fid, f in FIELDS.items():
            part = f.get("part")
            if not part:
                continue
            code = fid.split(".")[0]
            off, ln = part
            alt = f.get("alt")
            for other, (o2, l2, alt2) in claimed.get(code, {}).items():
                if other == fid or (alt and alt == alt2):
                    continue
                self.assertFalse(off < o2 + l2 and o2 < off + ln,
                                 f"{fid} overlaps {other}")
            claimed.setdefault(code, {})[fid] = (off, ln, alt)

    def test_alternates_are_gated_so_only_one_is_ever_shown(self):
        """A field marked `alt` has to be gated, or the overlap is real."""
        gated = set()
        for menu in SETUP_MENU:
            for st in menu.get("steps", []):
                if st.get("when"):
                    gated.add(st.get("field") or st.get("code"))
        for fid, f in FIELDS.items():
            if f.get("alt"):
                self.assertIn(fid, gated, f"{fid} overlaps but is not gated")

    def test_enum_values_are_unique_within_a_field(self):
        for fid, f in FIELDS.items():
            if f.get("kind") != "enum":
                continue
            vals = [v for v, _ in fieldio.choices_of(f)]
            self.assertEqual(len(vals), len(set(vals)), fid)

    def test_every_mode_is_gated_by_its_module(self):
        empty = Console()
        empty.modules = {}
        for menu, got in ((SETUP_MENU, empty.available_functions()),
                          (DIAG_MENU, empty.available_diagnostics()),
                          (NORMAL_MENU, empty.available_operating())):
            self.assertLess(len(got), len(menu))
        full = fitted()
        # every diagnostic function but the one that also needs a setting
        self.assertEqual(len(full.available_diagnostics()), len(DIAG_MENU) - 1)
        full.values["S56600"] = "1"      # SERVICE NOTICE: ENABLED
        full.modules["pumpmon"] = 1      # its own card, not the pump sense one
        self.assertEqual(len(full.available_diagnostics()), len(DIAG_MENU))
        # the two Mag sump functions want a Mag sensor programmed, not just
        # the card: "This menu displays only if the console detects a Mag
        # Sump Sensor capable of leak detection". FIDELITY U1b. And
        # LAST-SHIFT INVENTORY wants a Shift Start Time: "At least one Shift
        # Start Time must be entered to activate the 'Last Shift Inventory'
        # feature". FIDELITY O22. And DELIVERY MAINTENANCE wants ticketed
        # delivery on: "Before you use this function, Ticketed Delivery must
        # be enabled in the Setup Mode". FIDELITY O23.
        self.assertEqual(len(full.available_operating()),
                         len(NORMAL_MENU) - 5)
        full.values["S72301"] = "0103"
        full.values["S50201"] = "0600"
        full.values["S51C00"] = "1"
        # and the one operating function that is an option in System Setup
        self.assertEqual(len(full.available_operating()),
                         len(NORMAL_MENU) - 1)
        full.values["S51300"] = "1"      # TANKER LOAD REPORT: ENABLED
        self.assertEqual(len(full.available_operating()), len(NORMAL_MENU))


class Conditions(unittest.TestCase):
    """Screens the manuals say only appear under a condition."""

    def steps(self, console, function, device=1):
        fn = [f for f in SETUP_MENU if f["function"] == function][0]
        return [s["text"] for s in console.visible_steps(fn, device)]

    def test_mass_density_hides_the_tank_density_step(self):
        c = fitted()
        self.assertNotIn("Tank Density", self.steps(c, "IN-TANK SETUP"))
        c.values["S56000"] = "1"
        self.assertIn("Tank Density", self.steps(c, "IN-TANK SETUP"))

    def test_end_value_waits_for_an_end_factor_of_other(self):
        c = fitted()
        c.values["S61501"] = "011"                  # meter data present
        steps = self.steps(c, "IN-TANK SETUP")
        self.assertIn("End Factor (None/Flat/Hemispherical/Other)", steps)
        self.assertNotIn("End Value", steps)
        c.values["S63901"] = "013"                  # OTHER
        self.assertIn("End Value", self.steps(c, "IN-TANK SETUP"))

    def test_meter_data_present_gates_the_accuchart_steps(self):
        c = fitted()
        steps = self.steps(c, "IN-TANK SETUP")
        self.assertNotIn("Calibration Update", steps)
        c.values["S61501"] = "011"
        self.assertIn("Calibration Update", self.steps(c, "IN-TANK SETUP"))

    def test_the_user_defined_pipe_steps_need_that_pipe_type(self):
        """Seven screens, and the generic LINE LENGTH is not one of them.

        p.10-4 walks USER DEFINED through two lengths, two DIAMETERS, two
        bulk moduli and a thermal coefficient; five of the seven were here
        and the plain LINE LENGTH screen was drawn beside them, so that
        type had two screens for one number. Net six. See CLOSED U45.
        """
        c = fitted()
        plain = self.steps(c, "PRESSURE LINE LEAK SETUP")
        self.assertIn("Line Length", plain)
        c.values["S78801"] = "0118"                 # USER DEFINED
        defined = self.steps(c, "PRESSURE LINE LEAK SETUP")
        self.assertNotIn("Line Length", defined)
        self.assertEqual(len(defined) - len(plain), 6)

    def test_a_relay_shows_orientation_or_tank_but_not_both(self):
        c = fitted()
        steps = self.steps(c, "OUTPUT RELAY SETUP")   # STANDARD by default
        self.assertIn("Select Relay Type - Select Orientation (NO/NC)", steps)
        self.assertNotIn("Select Relay Type - Select Tank (None/Tank #)",
                         steps)
        c.values["S80A01"] = "012"                    # PUMP CONTROL OUTPUT
        steps = self.steps(c, "OUTPUT RELAY SETUP")
        self.assertNotIn("Select Relay Type - Select Orientation (NO/NC)",
                         steps)
        self.assertIn("Select Relay Type - Select Tank (None/Tank #)", steps)

    def test_pump_comm_control_has_no_alarm_assignments(self):
        c = fitted()
        c.values["S80A01"] = "014"                    # PUMP COMM CONTROL
        self.assertFalse([t for t in self.steps(c, "OUTPUT RELAY SETUP")
                          if t.startswith("Relay Assignments")])

    def test_the_pump_relay_monitor_swaps_delay_for_run_time(self):
        c = fitted()
        steps = self.steps(c, "PUMP RELAY MONITOR SETUP")
        self.assertTrue([t for t in steps if t.startswith("Select Max Run")])
        self.assertFalse([t for t in steps if t.startswith("Stuck Delay")])
        c.values["S7C601"] = "012101"                 # a PLLD assigned
        steps = self.steps(c, "PUMP RELAY MONITOR SETUP")
        self.assertTrue([t for t in steps if t.startswith("Stuck Delay")])
        self.assertFalse([t for t in steps if t.startswith("Select Max Run")])

    def test_no_modem_board_no_phone_directory(self):
        c = fitted()
        c.modules["modem"] = False
        steps = self.steps(c, "COMMUNICATIONS SETUP")
        self.assertNotIn("Receiver Telephone Number", steps)
        self.assertIn("Baud Rate", steps)
        c.modules["modem"] = True
        self.assertIn("Receiver Telephone Number",
                      self.steps(c, "COMMUNICATIONS SETUP"))

    def test_select_tank_waits_until_a_tank_is_configured(self):
        c = fitted()
        steps = self.steps(c, "LINE LEAK DETECTOR SETUP")
        self.assertNotIn("Tank #", steps)
        a_tank(c)
        self.assertIn("Tank #",
                      self.steps(c, "LINE LEAK DETECTOR SETUP"))

    def test_both_alternate_modes_offer_the_switchover_group(self):
        """"If MANIFOLDED: ALTERNATE or MANIFOLDED: ALTERNATE-HT are
        selected for Pump Sense Dispense Mode, the console can be programmed
        to automatically switch ..." -- 576013-623 Rev AN p.15-3. The whole
        group was gated on ALTERNATE-HT alone, so on a console programmed
        ALTERNATE the feature the same page describes was invisible.

        And the two thresholds are one each: "If you selected Manifolded:
        Alternate you will see this message: S1: SWITCHOVER VOLUME"; "If you
        selected Manifolded: Alternate-Ht ... S1: SWITCHOVER HEIGHT". Both
        were offered under ALTERNATE-HT, which is the mode the volume one
        does not belong to. FIDELITY F1.
        """
        c = fitted()
        # `Auto Active Switchover` was on this list and is gone: those two
        # words are a SECTION HEADING in both chapters that print them,
        # over prose ending "Press STEP to continue:", and the console has
        # no `PRESS <ENTER>` screen there. See CLOSED U40.
        both = ["Active Switchover"]
        c.values["S77301"] = "1"                    # STANDARD
        shown = self.steps(c, "PUMP SENSOR SETUP")
        for text in both + ["Switchover Volume", "Switchover Height"]:
            self.assertNotIn(text, shown)
        c.values["S77301"] = "2"                    # MANIFOLDED: ALTERNATE
        shown = self.steps(c, "PUMP SENSOR SETUP")
        for text in both + ["Switchover Volume"]:
            self.assertIn(text, shown)
        self.assertNotIn("Switchover Height", shown)
        c.values["S77301"] = "5"                    # MANIFOLDED: ALTERNATE-HT
        shown = self.steps(c, "PUMP SENSOR SETUP")
        for text in both + ["Switchover Height"]:
            self.assertIn(text, shown)
        self.assertNotIn("Switchover Volume", shown)

    def test_pump_threshold_is_on_every_tank_and_carries_its_default(self):
        """FIDELITY F5. "This feature is for line manifolded tanks and is
        only enabled when you have the Dispense Mode set to Manifolded:
        Sequential" -- 576013-623 Rev AN p.7-26.

        That reads like a condition on the screen and is not one. The screen
        was gated on the tank's line's dispense mode, and one test caught
        it: the real tape prints `PUMP THRESHOLD  : 10.00%` in its IN-TANK
        SETUP block on a console whose setup report has no line leak section
        at all -- no pressure line, no wireless one, no volumetric one. "Is
        only ENABLED" is the feature; the screens this console hides say
        "this message does not appear".

        What the same page does give is the default: "The allowable
        threshold can be from 0 to 50 percent of the tank's full volume. The
        default value is 10 percent", drawn as `PUMP THRESHOLD : 10.00`.
        """
        from tls350sim.screens import shown
        c = fitted()
        a_tank(c)
        for module in ("plld", "wplld", "vlld"):
            c.modules[module] = 0
        self.assertIn("Pump Threshold", self.steps(c, "IN-TANK SETUP"))
        self.assertEqual(shown(c, FIELDS["S63A01"], ""), "10.00")
        # and the range is the page's own
        self.assertIsNotNone(fieldio.encode(FIELDS["S63A01"], "S63A01", "50"))
        with self.assertRaises(ValueError):
            fieldio.encode(FIELDS["S63A01"], "S63A01", "51")

    def test_mass_density_hides_the_inventory_readings_too(self):
        c = fitted()
        fn = [f for f in NORMAL_MENU
              if f["function"] == "IN-TANK INVENTORY"][0]
        shown = [s["text"] for s in c.visible_steps(fn, 1)]
        self.assertNotIn("MASS", shown)
        c.values["S56000"] = "1"
        self.assertIn("MASS", [s["text"] for s in c.visible_steps(fn, 1)])


class TankProfile(unittest.TestCase):
    """The profile IS which function holds the tank's volumes."""

    def test_a_fresh_tank_is_one_point(self):
        c = fitted()
        self.assertEqual(c.tank_profile(1), "00")

    def test_the_profile_follows_the_function_that_was_programmed(self):
        """For the TABLE profiles, which is where the inference is sound:
        four volumes in 605 could have come from nowhere but the four point
        profile."""
        c = fitted()
        for code, profile in (("605", "01"), ("606", "02"), ("63C", "04")):
            c.values.clear()
            c.tank_profiles.clear()
            c.values[f"S{code}01"] = "01" + float_value(9728.0)
            self.assertEqual(c.tank_profile(1), profile, code)

    def test_a_full_volume_alone_says_nothing_about_linear(self):
        """The rule this class used to carry, and 576013-635's own `I60A`
        sample falsifies it: p.253 answers 60A for a **1 PT** tank.

        60A is Set Tank Linear Calculated Full Volume and its payload is byte
        for byte 604's. Half the fixtures in this repository set S60A only to
        give a tank a capacity, and every one of them was reading as LINEAR.
        576013-818 Rev AB p.12-31, over a real console's dump: "The only way
        to determine that the profile is set to linear is to run the 60A
        command" -- which is a statement that the STORAGE cannot tell you.
        """
        c = fitted()
        c.values["S60A01"] = "01" + float_value(9728.0)
        self.assertEqual(c.tank_profile(1), "00")
        self.assertEqual(c.full_volume(1), 9728.0)
        c.tank_profiles[1] = "03"
        self.assertEqual(c.tank_profile(1), "03")
        self.assertEqual(c.full_volume(1), 9728.0)

    def test_changing_profile_moves_the_full_volume_and_erases_the_rest(self):
        c = fitted()
        c.values["S60401"] = "01" + float_value(9728.0)
        erased = c.set_tank_profile(1, "02")
        self.assertEqual(erased, 1)
        self.assertNotIn("S60401", c.values)
        self.assertEqual(c.tank_profile(1), "02")
        self.assertEqual(c.full_volume(1), 9728.0)

    def test_a_profile_with_no_volume_yet_is_still_remembered(self):
        c = fitted()
        c.set_tank_profile(1, "03")
        self.assertEqual(c.tank_profile(1), "03")
        self.assertEqual(c.full_volume(1, default=None), None)

    def test_the_four_point_volumes_live_in_one_function(self):
        c = fitted()
        c.set_tank_profile(1, "01")
        for fid, value in (("S60501", "9728"), ("S60501.p75", "7296"),
                           ("S60501.p50", "4864"), ("S60501.p25", "2432")):
            c.values["S60501"] = fieldio.encode(FIELDS[fid], "S60501", value,
                                                c.values.get("S60501"))
        body = c.values["S60501"][2:]
        self.assertEqual(len(body), 32)          # four ASCII-hex floats
        self.assertEqual(fieldio.decode(FIELDS["S60501.p25"], "S60501",
                                        c.values["S60501"]), "2432")

    def test_the_profile_only_shows_its_own_steps(self):
        c = fitted()
        fn = [f for f in SETUP_MENU if f["function"] == "IN-TANK SETUP"][0]

        def steps():
            return [s["text"] for s in c.visible_steps(fn, 1)]

        self.assertNotIn("75% Volume", steps())
        c.set_tank_profile(1, "01")
        self.assertIn("75% Volume", steps())
        self.assertNotIn("95% Volume", steps())
        c.set_tank_profile(1, "02")
        self.assertIn("95% Volume", steps())
        self.assertNotIn("75% Volume", [t for t in steps()][:6])
        c.set_tank_profile(1, "04")
        self.assertIn("Add Height/Vol Pts", steps())


class FiftyPointChart(unittest.TestCase):
    def test_a_strapped_pair_joins_the_chart(self):
        c = fitted()
        c.set_tank_profile(1, "04")
        self.assertEqual(c.chart_points(1), [])
        c.add_chart_point(1, 88.32, 9200)
        n = c.add_chart_point(1, 44.16, 4600)
        self.assertEqual(n, 2)
        heights = [round(h, 2) for h, _v in c.chart_points(1)]
        self.assertEqual(heights, [88.32, 44.16])      # tallest first

    def test_the_chart_is_stored_the_way_63b_holds_it(self):
        c = fitted()
        c.add_chart_point(1, 88.32, 9200)
        raw = c.values["S63B01"]
        self.assertTrue(raw.startswith("0101" + "01"))  # tank, count, added
        self.assertEqual(len(raw), 2 + 2 + 18)

    def test_a_fifty_point_chart_makes_the_tank_fifty_point(self):
        c = fitted()
        c.add_chart_point(1, 88.32, 9200)
        self.assertEqual(c.tank_profile(1), "04")


class ChartSecurity(unittest.TestCase):
    def test_all_zeros_disables_it(self):
        c = fitted()
        self.assertFalse(c.chart_secured())
        c.set_chart_code("000000")
        self.assertFalse(c.chart_secured())
        c.set_chart_code("778899")
        self.assertTrue(c.chart_secured())

    def test_a_secured_console_refuses_a_chart_set_over_the_wire(self):
        c = fitted()
        c.set_slots("601", "1 X X X")
        h = Handler(c, verbose=False)
        chart = b"\x01S63B01010142B00000461C4000\r"
        self.assertNotIn(b"9999", h.handle(chart))
        c.set_chart_code("778899")
        self.assertIn(b"9999", h.handle(chart))

    def test_the_status_and_the_audit_trails_answer(self):
        c = fitted()
        c.set_slots("601", "1 X X X")
        h = Handler(c, verbose=False)
        self.assertIn(b"0&&", h.handle(b"\x01i21900\r"))
        c.set_chart_code("778899")
        self.assertIn(b"1&&", h.handle(b"\x01i21900\r"))
        self.assertIn(c.chart_code_set.encode(), h.handle(b"\x01i56A00\r"))
        c.add_chart_point(1, 88.32, 9200)
        self.assertIn(b"TANK CHART AUDIT TRAIL", h.handle(b"\x01I21801\r"))

    def test_the_chart_report_carries_the_w_and_m_block_when_secured(self):
        c = fitted()
        c.set_slots("601", "1 X X X")
        c.serial_number, c.wm_office = "TLS350-004217", "COUNTY W&M 12"
        c.add_chart_point(1, 88.32, 9200)
        self.assertNotIn("WEIGHTS AND MEASURES", c.chart_report(1))
        c.set_chart_code("778899")
        report = c.chart_report(1)
        self.assertIn("WEIGHTS AND MEASURES", report)
        self.assertIn("TLS350-004217", report)
        self.assertIn("PROBE S/N", report)

    def test_every_chart_change_is_dated(self):
        c = fitted()
        c.set_slots("601", "1 X X X")
        for height in (88.32, 44.16, 22.08):
            c.add_chart_point(1, height, 1000)
        self.assertEqual(len(c.chart_audit[1]), 3)

    def test_the_w_and_m_steps_appear_only_on_a_secured_fifty_point_tank(self):
        c = fitted()
        fn = [f for f in SETUP_MENU if f["function"] == "IN-TANK SETUP"][0]

        def steps():
            return [s["text"] for s in c.visible_steps(fn, 1)]

        c.set_tank_profile(1, "04")
        self.assertNotIn("Tank Capacity", steps())
        c.set_chart_code("778899")
        self.assertIn("Tank Capacity", steps())
        self.assertIn("Probe S/N", steps())
        c.set_tank_profile(1, "00")
        self.assertNotIn("Tank Capacity", steps())

    def test_a_label_written_over_the_wire_reads_back_whole(self):
        c = fitted()
        Handler(c, verbose=False).handle(b"\x01S60201REGULAR UNLEADED\r")
        self.assertEqual(c.text("602", 1), "REGULAR UNLEADED")


class CardCage(unittest.TestCase):
    """Cards, part numbers, bays and how many of each will fit."""

    def test_a_module_carries_its_part_number_and_its_bay(self):
        from tls350sim.console import MODULE_BAY, MODULE_PART
        self.assertEqual(MODULE_PART["probe"], "329356-002")
        self.assertEqual(MODULE_PART["rs232"], "329362-001")
        self.assertEqual(MODULE_BAY["probe"], "is")       # intrinsically safe
        self.assertEqual(MODULE_BAY["relay"], "power")
        self.assertEqual(MODULE_BAY["rs232"], "comm")

    def test_more_of_a_card_is_more_devices(self):
        c = Console()
        c.modules = {}
        c.set_module("probe", 1)
        self.assertEqual(c.capacity("probe"), 4)
        c.set_module("probe", 2)
        self.assertEqual(c.capacity("probe"), 8)
        self.assertEqual(c.count("probe"), 2)

    def test_a_bay_runs_out_of_slots(self):
        c = Console()
        c.modules = {}
        self.assertTrue(c.set_module("liquid", 2))        # 2 of the 8 I.S.
        self.assertTrue(c.set_module("vapor", 3))
        self.assertTrue(c.set_module("gw", 3))            # 8 used
        self.assertEqual(c.bay_free("is"), 0)
        self.assertFalse(c.set_module("smart", 1))        # nowhere to put it
        self.assertEqual(c.count("smart"), 0)

    def test_a_card_has_its_own_maximum(self):
        c = Console()
        c.modules = {}
        self.assertFalse(c.set_module("rs232", 4))        # "Max 3/console"
        self.assertTrue(c.set_module("rs232", 3))
        # three single-port cards in a bay of four SLOTS, which is not the
        # six positions it answers on. M7.
        self.assertEqual(c.bay_free("comm"), 1)

    def test_the_comm_bay_is_four_slots_and_six_positions(self):
        """Six POSITIONS across four physical SLOTS. Function 102's own
        sample prints six -- it ends at `COMM 6 UNUSED` -- and Figure 6-2's
        SYSTEM DIAGNOSTIC walk ends on the same screen, while 577013-528
        heads its two tables "Comm Modules That Can Be Installed In Slots 1,
        2, or 3" and "... In Slot 4". Four cards, six ports. M6 and M7.
        """
        c = Console()
        c.modules = {}
        c.board = "E6"          # the multiport wants an ECPU2 and an NVMEM203
        c.set_module("rs232", 3)
        self.assertTrue(c.set_module("rs485", 1))         # the dual-port one
        self.assertEqual(c.bay_free("comm"), 0)
        self.assertFalse(c.set_module("slink", 1))        # the bay is full
        # three single-port cards on 1, 2 and 3, and the dual-port module in
        # slot 4 on the two positions above them
        self.assertEqual(c.comm_layout(),
                         {1: "rs232", 2: "rs232", 3: "rs232", 4: "rs485"})
        self.assertEqual(sorted(c.comm_positions()), [1, 2, 3, 5, 6])

    def test_a_second_dual_port_module_wants_the_double_harness(self):
        """"Two dual-port modules can be installed in slots 3 and 4 by using
        a double dual-port wiring harness (P/N 332609-001)", 577013-528 Rev
        G p.5 -- so without one, the bay takes a single dual-port module.
        M7."""
        c = Console()
        c.modules = {}
        c.board = "E6"
        self.assertTrue(c.set_module("rs485", 1))
        self.assertFalse(c.set_module("mt4", 1))
        c.double_harness = True
        self.assertTrue(c.set_module("mt4", 1))
        self.assertEqual(c.comm_layout(), {3: "mt4", 4: "rs485"})
        # and all six positions are in use, which is the only arrangement
        # that fills the bay: two dual-port modules and two singles
        self.assertTrue(c.set_module("rs232", 2))
        self.assertEqual(sorted(c.comm_positions()), [1, 2, 3, 4, 5, 6])

    def test_pulling_a_card_takes_its_functions_with_it(self):
        c = fitted()
        self.assertIn("COMMUNICATIONS SETUP",
                      [f["function"] for f in c.available_functions()])
        for key in ("rs232", "modem", "mt", "mt4", "vmc"):
            c.set_module(key, 0)
        self.assertNotIn("COMMUNICATIONS SETUP",
                         [f["function"] for f in c.available_functions()])

    def test_the_cage_lists_what_is_in_which_slot(self):
        c = Console()
        c.modules = {}
        c.set_module("probe", 2)
        c.set_module("rs232", 1)
        cage = c.cage()
        self.assertEqual([(bay, slot) for bay, slot, _k, _n, _p in cage],
                         [("is", 1), ("is", 2), ("comm", 1)])
        self.assertIn("329356-002", [part for *_rest, part in cage])

    def test_the_slot_screens_walk_every_bay(self):
        """FIDELITY X5. The I.S. bay is slots 1 to 8 and the power bay 9 to
        16, whatever is in either: function 102's own sample puts a lone
        4-probe at slot 1, UNUSED at 2 through 8, and the power bay's
        `4 INPUT BOARD` at slot 9. This walk restarted the count at 1 for
        each bay, so a technician stepping the card cage met two SLOT 1s --
        which this test asserted, because it was written from the walk.
        """
        c = Console()
        c.modules = {}
        c.set_module("probe", 1)
        c.set_module("relay", 1)
        c.set_module("rs232", 1)
        screens = [l1 for l1, _l2 in c.slot_report()]
        self.assertIn("SLOT 1 4 PROBE", screens)
        self.assertIn("SLOT 9 4 RELAY", screens)
        self.assertIn("COMM 1 RS-232", screens)
        self.assertEqual(len(screens), 8 + 8 + 6)
        # and no slot number twice, which is the defect stated plainly
        numbers = [line.split()[1] for line in screens
                   if line.startswith("SLOT ")]
        self.assertEqual(numbers, [str(n) for n in range(1, 17)])

    def test_i102_numbers_the_bays_the_same_way_the_panel_does(self):
        """One cage, one numbering. `slot_readings` ran a single counter
        through both bays, so the same relay card came out at slot 2 there
        and slot 1 on the panel; the manual puts it at 9."""
        c = Console()
        c.modules = {}
        c.set_module("probe", 1)
        c.set_module("relay", 1)
        panel = [line.split()[1] + " " + " ".join(line.split()[2:])
                 for line, _l2 in c.slot_report() if line.startswith("SLOT ")]
        report = [f"{slot} {name}" for slot, _key, name, _p, _c
                  in c.slot_readings() if slot > 0]
        self.assertEqual(panel, report)
        self.assertIn("9 4 RELAY", report)


class TheProbeFamilyIsTwoCards(unittest.TestCase):
    """FIDELITY M4. 576013-635 Rev AA's function 102 type list names both --
    "0A=Four Probe w/ Ground Temp Module" beside "01=Four Probe Module" --
    and 576013-818 Table 6-1 gives them their own resistors, `4 Probe 2K`
    and `4 Probe w/Temp Interface 160K`, which is a hundredfold apart.

    576013-879 Rev W p.60 is the card: `PROBE/THERMISTOR INTERFACE MODULE -
    I.S. BAY`, drawn `PROBE` then `THERMISTOR` over one row of eight
    terminals, and its device table's second row is the thermistor -- "only
    one ground temperature thermistor is needed per site and the thermistor
    must be wired to thermistor position number 1".

    This console had one probe card, so the ground temperature thermistor
    had nowhere to be and the cage could not say the site had one. The
    captured console in `tests/console_capture` is exactly this card.
    """

    def a_cage(self, gt=False):
        c = Console(None)
        c.set_module("probe", 1)
        c.probe_gt = gt
        return c

    def test_the_default_card_is_the_plain_one(self):
        c = self.a_cage()
        self.assertFalse(c.probe_gt)
        self.assertEqual(c.card("probe")[1], "329356-002")
        self.assertEqual(c.card("probe")[2], 2000)
        self.assertEqual(c.module_type("probe"), "01")

    def test_the_thermistor_card_is_its_own_type_and_its_own_resistor(self):
        c = self.a_cage(gt=True)
        label, part, ohms = c.card("probe")
        self.assertEqual(label, "Four-Input Probe/Thermistor Interface Module")
        self.assertEqual(ohms, 160000)
        self.assertEqual(c.module_type("probe"), "0A")
        # 576013-632 Rev C, which is not on this shelf: "the Veeder-Root
        # Probe/Thermistor Interface Module (P/N 847490-104)". The prefix is
        # one this cage already uses -- the Pump Relay Monitor is 847490-504.
        self.assertEqual(part, "847490-104")

    def test_both_cards_carry_four_probes(self):
        """Which is why it is a variant and not a second cage key: some
        forty serial codes gate on `has("probe")`, and the numbering
        objection M2 had does not arise when the count is the same."""
        for gt in (False, True):
            c = self.a_cage(gt=gt)
            self.assertEqual(c.wires("probe"), 4)
            self.assertEqual(c.capacity("probe"), 4)
            self.assertEqual(len(c.slot_text("601").split()), 4)

    def test_the_glass_and_the_paper_spell_it_differently(self):
        """576013-818 Rev AB Figure 6-2 draws `SLOT 1 4 PROBE/ G. T.` and
        576013-635 Rev AA's function 102 sample prints `4 PROBE / G.T.`.
        One name table served both surfaces, so whichever was chosen the
        other one was wrong."""
        c = self.a_cage(gt=True)
        self.assertEqual(c.slot_report()[0][0], "SLOT 1 4 PROBE/ G. T.")
        paper = [name for slot, _key, name, _p, _c in c.slot_readings()
                 if slot == 1]
        self.assertEqual(paper, ["4 PROBE / G.T."])

    def test_the_slot_reads_the_cards_own_id_resistor(self):
        """A hundredfold apart, so the two columns a technician compares
        cannot confuse the two cards. Table 6-1's own note: "the actual or
        measured resistance will differ slightly from the nominal value"."""
        for gt, nominal in ((False, 2000), (True, 160000)):
            c = self.a_cage(gt=gt)
            por, now = c.module_id_resistance("probe", 1)
            self.assertAlmostEqual(por / nominal, 1.0, delta=0.05)
            self.assertAlmostEqual(now / por, 1.0, delta=0.01)

    def test_the_captured_console_s_own_row_replays(self):
        """`1   4 PROBE / G.T.   164313   164181` is what a real TLS-350
        printed. The nominal is 160K and both figures sit just above it,
        which is the band this console already models."""
        c = self.a_cage(gt=True)
        row = next(line for line in c.configuration_lines()
                   if line.startswith("  1   "))
        self.assertTrue(row.startswith("  1   4 PROBE / G.T."), row)
        por, now = (float(n) for n in row.split()[-2:])
        for reading in (por, now):
            self.assertGreater(reading, 160000)
            self.assertLess(reading, 168000)

    def test_the_card_survives_a_cold_boot(self):
        """The cage is re-scanned at power-up; the programming is not."""
        c = self.a_cage(gt=True)
        c.cold_boot()
        self.assertTrue(c.probe_gt)
        self.assertEqual(c.module_type("probe"), "0A")

    def test_the_bay_listing_names_the_card_that_is_in_it(self):
        c = self.a_cage(gt=True)
        named = [label for _bay, _slot, key, label, _part in c.cage()
                 if key == "probe"]
        self.assertEqual(named,
                         ["Four-Input Probe/Thermistor Interface Module"])


class ThePrintedBoardNamesAreTheirOwnVocabulary(unittest.TestCase):
    """FIDELITY M20. `MODULE_SHORT` was read off 576013-818's Table 6-1 --
    the ID RESISTANCE table, which names cards for a technician holding a
    meter -- and served both the SLOT screen and function 102's printout.
    The printout has its own list, and two primary sources agree on it
    against this console: the sample in 576013-635 Rev AA p.49, unchanged
    from Rev U through Rev AA, and the captured console in
    `tests/console_capture`, ten years and one continent apart.

    Each name below is attested twice: the STRING is printed in one of the
    samples, and the two ID resistances beside it reproduce the Table 6-1
    nominal of the card this console maps that name to. A name and a
    resistance agreeing is what identifies the card, because the resistance
    is the only field on that report whose meaning does not depend on
    reading the name.
    """

    #: (the printed name, POR, CURRENT, this console's key) off the two
    #: samples. The band is `module_id_resistance`'s own.
    ATTESTED = [
        # tests/console_capture/raw/I10200.bin, JAN 16 2006
        ("PLLD SENSOR BD", 3878, 3881, "plld"),
        ("PLLD POWER BD", 100848, 100820, "plldctl"),
        ("RS232 SERIAL BD", 15051, 15059, "rs232"),
        ("SERIAL SAT BD", 482940, 482561, "ssat"),
        # 576013-635 Rev AA p.49, function 102's own sample
        ("FAXMODEM  BOARD", 47008, 47006, "modem"),
        ("ELEC DISP INT.", 100725, 100748, "edim"),
    ]

    def a_cage(self):
        c = Console(None)
        for key in ("probe", "plld", "plldctl", "rs232", "ssat", "modem",
                    "edim"):
            c.modules[key] = 1
        c.probe_gt = True
        return c

    def test_each_printed_name_reaches_the_paper(self):
        paper = "\n".join(self.a_cage().configuration_lines())
        for name, _por, _now, _key in self.ATTESTED:
            self.assertIn(name, paper, name)
        self.assertIn("4 PROBE / G.T.", paper)

    def test_and_the_glass_keeps_its_own(self):
        """The screen's names are Table 6-1's, which is what the citation
        audit sanctions on a `SLOT #` line -- so this is not one table
        replacing another, it is two tables where there was one."""
        glass = "\n".join(l1 for l1, _l2 in self.a_cage().slot_report())
        for name in ("6 PLLD SENSOR", "PLLD CNTRL", "RS-232", "S-SAT COMM",
                     "SITEFAX", "EDIM", "4 PROBE/ G. T."):
            self.assertIn(name, glass, name)
        for name, _por, _now, _key in self.ATTESTED:
            self.assertNotIn(name, glass, name)

    def test_the_resistance_beside_each_name_identifies_its_card(self):
        """The cross-check that makes the mapping evidence rather than a
        reading of the words: the sample's own two figures against the
        Table 6-1 nominal of the card this console attaches that name to."""
        c = self.a_cage()
        for name, por, now, key in self.ATTESTED:
            nominal = c.card(key)[2]
            self.assertAlmostEqual(por / nominal, 1.0, delta=0.05,
                                   msg=f"{name} against {key} {nominal}")
            self.assertAlmostEqual(now / por, 1.0, delta=0.02, msg=name)

    def test_the_satellite_is_the_serial_one_and_the_resistor_says_so(self):
        """`SERIAL SAT BD` could be either of two satellite boards by its
        name. Table 6-1 gives the serial one 475K and the Amoco one 332K,
        and the capture reads 482940."""
        c = self.a_cage()
        self.assertLess(abs(482940 / c.card("ssat")[2] - 1.0), 0.05)
        self.assertGreater(abs(482940 / c.card("asat")[2] - 1.0), 0.4)

    def test_a_card_with_no_attested_name_prints_the_screen_s(self):
        """Most of the cage appears in no sample at all. Inventing
        `8 LIQUID BD` from the pattern would be drawing a line on the one
        report a technician reads to find out what is in the console."""
        c = self.a_cage()
        c.modules["liquid"] = 1
        self.assertEqual(c.slot_name("liquid"), c.slot_name("liquid", True))
        self.assertEqual(c.slot_name("liquid"), "8 LIQUID")

    def test_every_attested_name_belongs_to_a_card_this_console_has(self):
        """`4 INPUT BOARD` is in the sample too, at slot 9, and is
        `2C=Four Input Module` -- a card in no row of Table 6-1 and no entry
        of this cage. It is deliberately absent rather than bent onto a
        card it is not."""
        from tls350sim.console import MODULE_PAPER, MODULE_SHORT
        self.assertNotIn("4 INPUT BOARD", MODULE_PAPER.values())
        for key in MODULE_PAPER:
            self.assertIn(key, MODULE_SHORT, key)


class TheSevenInputSmartCardReportsItself(unittest.TestCase):
    """The same defect on the other variant, found by building M4's.

    576013-635 Rev AA's type list names both of that family too --
    "2B=SmartSensor(7) Module" beside "28=SmartSensor(8) Module" -- and
    `MODULE_TYPE` is keyed by the cage key, so a console with the Press
    module in it reported the eight-input card down the port. FIDELITY M2's
    entry lists three things that had to start asking the console instead of
    a table; this is a fourth nobody had looked at.
    """

    def test_the_press_module_is_2b_and_the_other_is_28(self):
        c = Console(None)
        c.set_module("smart", 1)
        self.assertEqual(c.module_type("smart"), "28")
        c.smart_press = True
        self.assertEqual(c.module_type("smart"), "2B")

    def test_and_the_bay_listing_names_it(self):
        c = Console(None)
        c.set_module("smart", 1)
        c.smart_press = True
        named = [(label, part) for _b, _s, key, label, part in c.cage()
                 if key == "smart"]
        self.assertEqual(
            named, [("Seven-Input Smart Sensor/Pressure Module", "332250-001")])

    def test_but_its_slot_line_is_still_the_family_s(self):
        """No page draws either smart card's slot line, so the variant has
        no name of its own and inventing one would be drawing a screen the
        hardware may not have."""
        c = Console(None)
        c.set_module("smart", 1)
        c.smart_press = True
        self.assertEqual(c.slot_name("smart"), c.slot_name("smart", True))
        self.assertIn("SMART", c.slot_name("smart"))


class EightOfEverySensorCard(unittest.TestCase):
    """FIDELITY M1 and M15, and this retires an invention rather than fixing
    a defect, which is the rarer kind.

    `MODULES` allowed two of each eight-input card and three of each
    five-input one, and its own comment said that was "a simulator convention
    rather than a sourced figure". 577013-750 Rev AK's Console Compatibility
    table has a "# of Modules per Console" column and every TLS-350 row reads
    "Up to 8"; 576013-813 Rev D's I.S. bay figure says "Permissible Modules
    (Limit 8 per console)"; and the operator's manual states each family's
    report limit, which is the card's inputs times eight, five times over.
    """

    # 576013-610 Rev AC's own report limits, and the inputs that make them
    REPORT_LIMIT = {"liquid": (8, 64), "2wire": (8, 64), "gw": (5, 40),
                    "vapor": (5, 40), "3wire": (6, 48)}

    def test_eight_of_each_and_the_bay_has_room(self):
        for key in self.REPORT_LIMIT:
            c = Console()
            c.modules = {}
            self.assertTrue(c.set_module(key, 8), key)
            self.assertEqual(c.bay_free("is"), 0, key)
            self.assertFalse(c.set_module("probe", 1), key)

    def test_the_report_limits_are_the_inputs_times_eight(self):
        """"You can print a report for up to 48 sensors", and four more like
        it. A manual written for the operator, which never says "module"."""
        for key, (wires, limit) in self.REPORT_LIMIT.items():
            c = Console()
            c.modules = {}
            c.set_module(key, 8)
            self.assertEqual(c.capacity(key), limit, key)
            self.assertEqual(limit, wires * 8, key)

    def test_the_type_b_card_is_six_inputs(self):
        """It is named "Six-Input Type B Sensor Module" in this table and had
        five, so a sensor on position 6 could be neither configured nor
        wired. M15."""
        c = Console()
        c.modules = {"3wire": 1}
        self.assertEqual(c.capacity("3wire"), 6)
        self.assertEqual(len(c.slot_text("746", 6, 0).split()), 6)
        for n in range(1, 7):
            c.values[f"S746{n:02d}"] = f"{n:02d}1"
        self.assertEqual(c.configured("746"), [1, 2, 3, 4, 5, 6])

    def test_the_vmc_control_still_agrees_exactly(self):
        """The same manual states one more report limit -- "up to 18 VMC
        controllers" -- and this console already had that one right, which
        is what says the arithmetic is not a coincidence."""
        c = Console()
        c.modules = {"vmc": 1}
        self.assertEqual(c.capacity("vmc"), 18)


class WhatIsOnTheMenu(unittest.TestCase):
    """Nothing on a menu the console cannot serve."""

    def bare(self):
        c = Console()
        c.modules, c.software = {}, {}
        return c

    def test_an_empty_console_offers_almost_nothing(self):
        c = self.bare()
        self.assertEqual([f["function"] for f in c.available_functions()],
                         ["SYSTEM SETUP", "ARCHIVE UTILITY"])
        self.assertEqual(c.available_operating(), [])
        # SERVICE REPORT is on a bare console because Figure 6-3 gives it
        # two branches, and the one WITHOUT a Maintenance Tracker is the
        # one that asks for an ID. It used to require the Tracker card,
        # which took the function away from every console that had none.
        # FIDELITY D3.
        self.assertEqual([f["function"] for f in c.available_diagnostics()],
                         ["SYSTEM DIAGNOSTIC", "SERVICE REPORT",
                          "ALARM HISTORY REPORT", "ARCHIVE DIAGNOSTIC"])

    def test_only_a_console_without_a_tracker_asks_for_an_id(self):
        """Figure 6-3's two columns. A console with a Maintenance Tracker
        takes the technician's identity off the key, which is why the
        Enabled branch does not ask for one. FIDELITY D3."""
        c = self.bare()
        ask = {"when": {"no_module": "mt"}}
        self.assertTrue(c.visible(ask, 1))
        # the Tracker "installs only in consoles with an ECPU2 board, a
        # NVMEM203 board, and Version 27 or later software" (577013-528),
        # so fitting the card is not enough on a board that cannot drive it
        c.board = "E6"
        c.set_module("mt", 1)
        self.assertTrue(c.has("mt"))
        self.assertFalse(c.visible(ask, 1))

    def test_no_sensor_card_no_sensor_functions(self):
        c = self.bare()
        c.set_module("probe", 1)
        names = [f["function"] for f in c.available_operating()]
        self.assertNotIn("LIQUID STATUS", names)
        self.assertNotIn("VAPOR STATUS", names)
        self.assertIn("IN-TANK INVENTORY", names)
        c.set_module("liquid", 1)
        self.assertIn("LIQUID STATUS",
                      [f["function"] for f in c.available_operating()])

    def test_a_software_module_licenses_its_functions(self):
        c = self.bare()
        c.set_module("probe", 1)
        names = lambda: [f["function"] for f in c.available_operating()]
        self.assertNotIn("CSLD TEST RESULTS", names())
        self.assertNotIn("FUEL MANAGEMENT", names())
        c.software["csld"] = True
        self.assertIn("CSLD TEST RESULTS", names())
        self.assertNotIn("FUEL MANAGEMENT", names())
        c.software["fuelman"] = True
        self.assertIn("FUEL MANAGEMENT", names())

    def test_bir_licenses_reconciliation_setup(self):
        c = self.bare()
        setup = lambda: [f["function"] for f in c.available_functions()]
        self.assertNotIn("RECONCILIATION SETUP", setup())
        c.software["bir"] = True
        self.assertIn("RECONCILIATION SETUP", setup())

    def test_the_line_leak_keys_hide_the_test_schedules(self):
        c = self.bare()
        c.set_module("plld", 1)
        fn = [f for f in SETUP_MENU
              if f["function"] == "PRESSURE LINE LEAK SETUP"][0]
        steps = lambda: [s["text"][:8] for s in c.visible_steps(fn, 1)]
        self.assertNotIn("0.2 gph ", steps())
        c.software["plld020"] = True
        self.assertIn("0.2 gph ", steps())

    def test_the_system_setup_line_steps_want_a_line_card(self):
        c = self.bare()
        fn = [f for f in SETUP_MENU if f["function"] == "SYSTEM SETUP"][0]
        steps = lambda: [s["text"] for s in c.visible_steps(fn, 1)]
        self.assertFalse([t for t in steps() if t.startswith("Line Reenable")])
        c.set_module("vlld", 1)
        self.assertTrue([t for t in steps() if t.startswith("Line Reenable")])

    def test_the_features_list_is_cards_and_keys_together(self):
        c = self.bare()
        self.assertEqual(c.features(), [])
        c.set_module("probe", 1)
        self.assertIn("PERIODIC IN-TANK TESTS", c.features())
        c.software["fuelman"] = True
        self.assertIn("FUEL MANAGER", c.features())


class Deliveries(unittest.TestCase):
    """A delivery the console works out for itself, from the level."""

    def a_site(self, volume=2000.0, delay_minutes=1):
        c = fitted()
        a_tank(c, volume=volume)
        c.values["S60701"] = "01" + float_value(96.0)
        # S610 is minutes, two digits, not a float
        c.values["S61001"] = f"01{int(delay_minutes):02d}"
        c.deliveries.tick()
        return c

    def fill(self, c, *volumes):
        """Pour product in, a minute of console time between readings."""
        for volume in volumes:
            c.tank_level[1]["volume"] = float(volume)
            c.clock_offset += 60.0
            c.deliveries.tick()

    def test_a_drop_is_recorded_from_where_the_tank_started(self):
        c = self.a_site()
        self.fill(c, 3000, 4000, 5000, 5000)
        c.clock_offset += 300.0                  # the level settles
        c.deliveries.tick()
        record = c.deliveries.last(1)
        self.assertIsNotNone(record)
        self.assertEqual(record.start["volume"], 2000.0)
        self.assertEqual(record.end["volume"], 5000.0)
        self.assertEqual(record.amount, 3000.0)

    def test_the_delay_holds_the_report_until_the_level_settles(self):
        c = self.a_site(delay_minutes=30)
        self.fill(c, 4000, 4000)
        c.clock_offset += 300.0                  # five minutes of quiet
        c.deliveries.tick()
        self.assertIsNone(c.deliveries.last(1))  # not thirty yet
        self.assertIsNotNone(c.deliveries.in_progress(1))
        c.clock_offset += 1800.0
        c.deliveries.tick()
        self.assertIsNotNone(c.deliveries.last(1))

    def test_dispensing_is_not_a_delivery(self):
        c = self.a_site()
        self.fill(c, 1900, 1800, 1700)
        c.clock_offset += 600.0
        c.deliveries.tick()
        self.assertIsNone(c.deliveries.last(1))

    def test_a_dribble_is_not_a_delivery(self):
        c = self.a_site()
        self.fill(c, 2005, 2010)                 # ten gallons
        c.clock_offset += 600.0
        c.deliveries.tick()
        self.assertIsNone(c.deliveries.last(1))

    def test_the_console_prints_it_without_being_asked(self):
        c = self.a_site()
        self.fill(c, 5000, 5000)
        c.clock_offset += 300.0
        c.deliveries.tick()
        self.assertEqual(len(c.printed_deliveries), 1)
        tank, record = c.printed_deliveries[0]
        out = "\n".join(printer.delivery(c, tank, record))
        self.assertIn("INVENTORY INCREASE", out)
        self.assertIn("GROSS INCREASE", out)

    def test_a_delivery_invalidates_a_leak_test(self):
        c = self.a_site()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        self.fill(c, 5000, 5000)
        c.clock_offset += 300.0
        c.deliveries.tick()
        c.clock_offset += 3 * 3600.0
        c.leaks.tick()
        self.assertEqual(c.leaks.result("tank", 1, "periodic").result,
                         leaktest.INVALID)

    def test_a_tool_reads_and_clears_them(self):
        c = self.a_site()
        self.fill(c, 5000, 5000)
        c.clock_offset += 300.0
        c.deliveries.tick()
        h = Handler(c, verbose=False)
        report = h.handle(b"\x01I20201\r").decode()
        self.assertIn("DELIVERY REPORT", report)
        self.assertIn("AMOUNT", report)
        body = h.handle(b"\x01i20201\r").decode()[17:-7]
        self.assertTrue(body.startswith("01"))       # tank 01
        self.assertEqual(body[3:5], "01")            # one delivery
        h.handle(b"\x01S05101\r")
        self.assertIsNone(c.deliveries.last(1))


class TicketedDelivery(unittest.TestCase):
    """The ticket against the gauge, which is what the driver hands you."""

    def a_delivery(self):
        c = fitted()
        a_tank(c, volume=2000.0)
        c.values["S61001"] = "01" + float_value(1.0)
        c.values["S51C00"] = "1"                # ticketed delivery enabled
        c.deliveries.tick()
        for volume in (3000, 5000, 5000):
            c.tank_level[1]["volume"] = float(volume)
            c.clock_offset += 60.0
            c.deliveries.tick()
        c.clock_offset += 300.0
        c.deliveries.tick()
        return c, c.deliveries.last(1)

    def test_a_delivery_with_no_ticket_is_a_warning(self):
        c, _record = self.a_delivery()
        self.assertIn("022801", c.compute_alarms())
        self.assertEqual(describe_alarms(["022801"])[0]["screen"],
                         "T 1:MISSING TICKET WARN")

    def test_the_warning_goes_when_a_ticket_is_entered(self):
        c, record = self.a_delivery()
        record.ticket = 3050.0
        self.assertNotIn("022801", c.compute_alarms())
        self.assertEqual(record.variance(), 50.0)

    def test_a_zero_ticket_cancels_the_warning_too(self):
        """"Entering 0 volume will cancel ticketed delivery warning"."""
        c, record = self.a_delivery()
        record.ticket = 0.0
        self.assertNotIn("022801", c.compute_alarms())

    def test_no_ticketed_delivery_no_warning(self):
        c, _record = self.a_delivery()
        c.values["S51C00"] = "0"
        self.assertNotIn("022801", c.compute_alarms())

    def test_a_tool_sets_the_ticket_and_the_bol(self):
        import time as _t
        c, record = self.a_delivery()
        stamp = _t.strftime("%y%m%d%H%M", _t.localtime(record.end["at"]))
        h = Handler(c, verbose=False)
        h.handle(("{}S7B50101{}5050{}".format(chr(1), stamp, chr(13)))
                 .encode())
        h.handle(("{}S7B60101{}EXX23223{}".format(chr(1), stamp, chr(13)))
                 .encode())
        self.assertEqual(record.ticket, 5050.0)
        self.assertEqual(record.bol, "EXX23223")
        report = h.handle(("{}I22101{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"TICKETED DELIVERY REPORT", report)
        self.assertIn(b"5050.0", report)
        # and the BOL is NOT on 221. It belongs to 222, whose Function Type
        # is "Bill of Lading Report" and whose sample heads a column NUMBER
        # under BOL; 221's sample has no such column and three temperature
        # columns instead. This used to print a BOL column on 221 in the
        # place two of those three should have been. FIDELITY S3.
        self.assertNotIn(b"EXX23223", report)
        c.software["bir"] = True
        bol = h.handle(("{}I2220101{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"TICKETED AND BOL DELIVERY REPORT", bol)
        self.assertIn(b"EXX23223", bol)

    def test_a_delivery_can_be_entered_by_hand(self):
        c = fitted()
        a_tank(c, volume=2000.0)
        when = __import__("time").mktime(c.now())
        record = c.deliveries.insert(1, when, 4000.0, "BOL-1")
        self.assertIsNotNone(record)
        self.assertTrue(record.inserted)
        self.assertIsNone(c.deliveries.insert(1, when, 100.0))   # same minute
        report = c.deliveries.ticketed_report([1])
        self.assertIn("UNAVAIL", report)      # nothing gauged it
        self.assertIn("4000", report)


class ATruck(unittest.TestCase):
    """A delivery as an EVENT: a tanker that pours at a rate, over console
    time, and the console notices exactly as it does on a site. BENCH T1,
    T2, T3 and T5."""

    def a_site(self):
        c = fitted()
        a_tank(c, volume=2000.0)
        c.values["S61001"] = "0101"             # one minute of delay
        c.values["S51C00"] = "1"                # ticketed delivery
        c.software["bir"] = True
        c.meter_flow = {}
        c.tick()
        c.bir.current(1)                        # the period opens at 2000
        return c

    def minutes(self, c, n, step=1.0):
        """Move the console's clock, and ONLY the console's clock.

        `Console.now()` is `time.time()` plus the offset, so the REAL clock
        keeps running underneath a test that steps the offset -- and a
        truck pouring at 300 gallons a minute gains five gallons for every
        real second the test itself spends. The delta below is one gallon,
        which is a fifth of a second of headroom: it held for months and
        then one loaded full-suite run spent longer than that between two
        ticks and the pour came out at 3,505. `tools/allscreens.py` stops
        the clock for the whole walk for the same reason.
        """
        real = time.time
        frozen = real()
        time.time = lambda: frozen
        try:
            for _ in range(int(n / step)):
                c.clock_offset += 60.0 * step
                c.tick()
        finally:
            time.time = real

    def test_a_truck_pours_at_its_rate_over_console_time(self):
        c = self.a_site()
        c.drops.start(1, [3000.0], rate=300.0)
        self.minutes(c, 5)
        self.assertAlmostEqual(c.tank_level[1]["volume"], 3500.0, delta=1.0)
        self.assertIn("dropping", c.drops.describe(1))
        self.assertIsNotNone(c.deliveries.in_progress(1))
        self.minutes(c, 5)
        self.assertAlmostEqual(c.tank_level[1]["volume"], 5000.0, delta=1.0)
        self.assertEqual(c.drops.describe(1), "")     # the truck has gone
        self.minutes(c, 6)                           # and the level settles
        record = c.deliveries.last(1)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.amount, 3000.0, delta=1.0)

    def test_the_driver_hands_over_the_ticket(self):
        c = self.a_site()
        c.drops.start(1, [3000.0], rate=600.0, ticket=3050.0, bol="BOL-7")
        self.minutes(c, 12)
        record = c.deliveries.last(1)
        self.assertEqual(record.ticket, 3050.0)
        self.assertEqual(record.bol, "BOL-7")
        self.assertAlmostEqual(record.variance(), 50.0, delta=1.0)
        self.assertNotIn("022801", c.compute_alarms())
        # and the ticket reached the reconciliation, not just the report
        self.assertEqual(c.bir.current(1)["ticketed"], 3050.0)

    def test_a_truck_with_no_paperwork_is_a_missing_ticket(self):
        c = self.a_site()
        c.drops.start(1, [3000.0], rate=600.0)
        self.minutes(c, 12)
        self.assertIsNone(c.deliveries.last(1).ticket)
        self.assertIn("022801", c.compute_alarms())
        self.assertEqual(c.bir.current(1)["ticketed"], 0.0)

    def test_two_compartments_with_a_gap_are_one_delivery(self):
        """S610's own words: the delay "prevents generation of false
        reports during the intervals between multi-compartment drops"."""
        c = self.a_site()
        c.values["S61001"] = "0105"             # five minutes bridges a gap
        c.drops.start(1, [1500.0, 1500.0], rate=300.0)
        drop = c.drops.running[1]
        drop.gap = 3.0
        self.minutes(c, 5.5, step=0.5)          # first compartment down
        self.assertIn("next compartment", c.drops.describe(1))
        self.minutes(c, 3, step=0.5)            # the gap
        self.assertIsNone(c.deliveries.last(1))  # no false report
        self.minutes(c, 5, step=0.5)            # second compartment
        self.minutes(c, 6)
        self.assertEqual(len(c.deliveries.records[1]), 1)
        self.assertAlmostEqual(c.deliveries.last(1).amount, 3000.0, delta=1.0)

    def test_a_truck_stops_at_a_full_tank(self):
        c = self.a_site()
        c.drops.start(1, [20000.0], rate=1000.0)
        self.minutes(c, 15)
        self.assertAlmostEqual(c.tank_level[1]["volume"], 10000.0, delta=0.5)
        self.assertEqual(c.drops.describe(1), "")

    def test_a_truck_sent_away_leaves_what_it_dropped(self):
        c = self.a_site()
        c.drops.start(1, [3000.0], rate=300.0, ticket=3000.0)
        self.minutes(c, 2)
        c.drops.stop(1)
        self.assertAlmostEqual(c.tank_level[1]["volume"], 2600.0, delta=1.0)
        self.minutes(c, 8)
        record = c.deliveries.last(1)
        self.assertAlmostEqual(record.amount, 600.0, delta=1.0)
        self.assertEqual(record.ticket, 3000.0)     # the paperwork he came with

    def test_a_reboot_does_not_send_the_truck_away(self):
        c = self.a_site()
        c.drops.start(1, [3000.0], rate=300.0)
        self.minutes(c, 1)
        c.cold_boot()
        self.assertIn(1, c.drops.running)
        self.assertIs(c.drops.c, c)
        c.reset()
        self.assertEqual(c.drops.running, {})

    def test_a_silent_adjust_is_not_a_delivery_or_a_load(self):
        c = self.a_site()
        c.values["S51300"] = "1"                # tanker loads watched too
        c.adjust_volume(1, 3000.0)
        self.minutes(c, 10)
        self.assertIsNone(c.deliveries.last(1))
        self.assertIsNone(c.deliveries.in_progress(1))
        c.adjust_volume(1, -1500.0)
        self.minutes(c, 10)
        self.assertEqual(c.loads.records.get(1, []), [])
        self.assertAlmostEqual(c.tank_level[1]["volume"], 3500.0)
        # but the book still says 2000 sold nothing, so the gap is variance
        self.assertAlmostEqual(c.bir.current(1)["variance"], 1500.0, delta=1.0)


class TheCsldTimingSequence(unittest.TestCase):
    """FIDELITY K5 and K6. 576013-818 Figure 11-2 is a flow chart with every
    number on it, and this console had none of them.

    "Tank goes idle and must remain so for 8 minutes", then "one sample
    delay (30 seconds)", then "PRE-START: 5 minutes + Accept time", then
    "TEST IN PROGRESS: Minimum test time 15 minutes + Feedback time ... All
    probe samples received within this test period are used in the leak
    analysis", and "(Tank going active will abort test if insufficient data
    has been collected)". "CSLD test requires a minimum time of 28 minutes.
    A maximum test time of 3 hours is imposed."

    What this console did instead: an hour of quiet, then an instantaneous
    sample whose INTVL was the hour. A test with no length cannot be halted
    by dispensing and cannot be rejected for being short, which is two of
    the six acceptability codes and most of chapter 11's method.
    """

    def a_quiet_tank(self):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        # "The CSLD option appears only when the tank is equipped with a
        # 0.1 gph (0.38 lph) Mag probe", 576013-623 p.8-2. A tank with a
        # float size programmed has a float, and only a Mag has one.
        c.values["S62F01"] = "011"
        # a day of hourly volumes first: "CSLD Volume Table must be
        # complete" is one of the three conditions for recording a test
        for _ in range(26):
            c.clock_offset += 3600.0
            c.tick()
        return c

    def minutes(self, c, n, step=1.0):
        for _ in range(int(n / step)):
            c.clock_offset += step * 60.0
            c.tick()
        return c

    def a_fresh_sequence(self, c):
        """Put the tank active for a moment, and clear what it has banked.

        `a_quiet_tank` runs twenty-six hours of quiet to fill the volume
        table, and CSLD now WALKS an interval rather than taking one look
        at the end of it -- so those hours run the timing sequence through
        several whole tests, exactly as p.11-30 says they should ("CSLD
        will complete a test after 3 hours and start a new test if the tank
        remains idle"). A test that wants to measure ONE test's timing has
        to say where that one begins, and the banked tests also feed
        `accept`, whose 0-45 minutes would otherwise move the start of the
        next one.
        """
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.meter_flow = {1: 300.0}
        self.minutes(c, 2)
        c.meter_flow = {}
        c.csld.detail.pop(1, None)
        return c

    def test_nothing_is_recorded_until_the_volume_table_is_complete(self):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        # "The CSLD option appears only when the tank is equipped with a
        # 0.1 gph (0.38 lph) Mag probe", 576013-623 p.8-2. A tank with a
        # float size programmed has a float, and only a Mag has one.
        c.values["S62F01"] = "011"
        for _ in range(20):                     # twenty hours, not enough
            c.clock_offset += 3600.0
            c.tick()
        self.assertFalse(c.csld.volume_table_complete(1))
        self.assertEqual(c.csld.samples.get(1, []), [])
        for _ in range(10):
            c.clock_offset += 3600.0
            c.tick()
        self.assertTrue(c.csld.volume_table_complete(1))
        self.assertTrue(c.csld.samples.get(1))

    def busy(self, c, rate=300.0):
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.meter_flow = {1: rate}
        return c

    def fresh(self, c):
        """A tank that has just gone quiet with no test history behind it,
        so both control variables are zero and the lead is 8 + 0.5 + 5."""
        c.csld.detail[1] = []
        c.csld.samples[1] = []
        c.csld.testing.pop(1, None)
        self.minutes(self.busy(c), 2)         # the forecourt runs, then stops
        c.meter_flow = {}
        self.minutes(c, 1)                    # and the pre-delay starts here
        return c

    def test_the_pre_delay_and_pre_start_come_before_any_test(self):
        """8 + 0.5 + 5, and nothing is sampled inside them."""
        c = self.fresh(self.a_quiet_tank())
        self.assertEqual(c.csld.accept(1), 0.0)
        self.minutes(c, 9)
        self.assertIsNone(c.csld.testing.get(1),
                          "eight minutes is the PRE-DELAY, not the test")
        self.minutes(c, 6)
        self.assertIsNotNone(c.csld.testing.get(1))
        self.assertEqual(len(c.csld.samples.get(1, [])), 0,
                         "the test has begun and has not finished")

    def test_the_accept_variable_lengthens_the_pre_start(self):
        """"5 minutes + Accept time (dynamic variable of 0 - 45 minutes
        duration)", and it shortens the test by as much: p.11-30 says the
        eliminated first part "varies with the feedback variables"."""
        c = self.fresh(self.a_quiet_tank())
        short = c.csld._lead_minutes(1)
        c.csld.detail[1] = [{"state": csld.ACCEPTABLE, "interval": 174.5}]
        long = c.csld._lead_minutes(1)
        self.assertAlmostEqual(short, 5.5)
        self.assertAlmostEqual(long, 5.5 + csld.CONTROL_MAX_MINUTES)
        self.assertAlmostEqual(c.csld._max_test_minutes(1),
                               csld.MAX_SEQUENCE_MINUTES - long)

    def test_a_test_left_alone_runs_to_the_three_hour_cap(self):
        """"A maximum test time of 3 hours is imposed", and Figure 11-3
        prints 174.5 minutes three times -- 180 less the 5.5 of sample delay
        and pre-start that p.11-30 calls "the first part of a test"."""
        c = self.a_quiet_tank()
        self.minutes(c, 200, step=5.0)
        rows = c.csld.table(1)
        self.assertTrue(rows)
        self.assertAlmostEqual(rows[0]["interval"], 174.5, places=1)

    def test_the_tank_going_active_halts_the_test_where_it_stood(self):
        """"tests are halted by dispensing, not the 3-hour CSLD limit"."""
        c = self.a_fresh_sequence(self.a_quiet_tank())
        self.minutes(c, 14)                     # into the test
        self.minutes(c, 60, step=5.0)
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.meter_flow = {1: 300.0}
        self.minutes(c, 5)
        rows = c.csld.table(1)
        self.assertTrue(rows)
        self.assertLess(rows[-1]["interval"], 174.5)
        self.assertGreater(rows[-1]["interval"], 15.0)

    def test_a_short_test_is_rejected_for_its_duration(self):
        """"01=Rejected - less than minimum duration requirement", and the
        requirement is "15 minutes + Feedback time"."""
        c = self.a_fresh_sequence(self.a_quiet_tank())
        self.minutes(c, 14)                     # the test begins
        self.minutes(c, 5)                      # and runs five minutes
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.meter_flow = {1: 300.0}
        self.minutes(c, 2)
        rows = c.csld.table(1)
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["state"], csld.REJECT_DURATION)

    def test_a_new_test_starts_while_the_tank_stays_quiet(self):
        """"CSLD will complete a test after 3 hours and start a new test if
        the tank remains idle", which is what makes a day about eight
        tests."""
        c = self.a_quiet_tank()
        self.minutes(c, 12 * 60, step=10.0)
        rows = c.csld.table(1)
        self.assertGreaterEqual(len(rows), 3)
        self.assertLessEqual(len(rows), 6)

    def test_a_test_within_two_hours_of_a_delivery_is_rejected(self):
        """"All tests rejected with error code 2 started within 2 hours of a
        delivery", p.11-30 reading its own rate table. The window was an
        hour, and it was measured through `deliveries.during`, which adds
        the STATIC test's own eight-hour quiet period to whatever it is
        handed -- so the two hours would have been ten."""
        c = self.a_quiet_tank()
        now = time.mktime(c.now())
        self.assertIsNone(c.csld.delivery_hours(1, now))
        self.assertEqual(c.csld._state(1, 100.0), csld.ACCEPTABLE)

        class _Drop:
            start = {"at": now - 5400.0}
            end = {"at": now - 3600.0}       # an hour ago

        c.deliveries.records.setdefault(1, []).append(_Drop())
        self.assertAlmostEqual(c.csld.delivery_hours(1, now), 1.0, places=1)
        self.assertEqual(c.csld._state(1, 100.0), csld.REJECT_DELIVERY)

        class _Older(_Drop):
            end = {"at": now - 3 * 3600.0}   # three hours ago

        c.deliveries.records[1] = [_Older()]
        self.assertEqual(c.csld._state(1, 100.0), csld.ACCEPTABLE)

    def test_the_dispense_factor_weights_the_recent_hours(self):
        """p.11-30: "It is not as simple as the amount of gallons dispensed
        during the last 24 hours because the hourly volumes are weighted in
        such a way that the most recent dispensing value contributes more to
        the dispense factor than dispensing volume that has occurred 23
        hours ago." It was the plain total."""
        c = self.a_quiet_tank()
        rows = [(float(n), 10000.0) for n in range(24)]
        c.csld.hourly[1] = list(rows)
        self.assertEqual(c.csld.dispense_factor(1), 0.0)
        # the same 1000 gallons, sold at the start of the day and at the end
        early = list(rows)
        early[1:] = [(float(n), 9000.0) for n in range(1, 24)]
        c.csld.hourly[1] = early
        old = c.csld.dispense_factor(1)
        late = [(float(n), 10000.0) for n in range(23)] + [(23.0, 9000.0)]
        c.csld.hourly[1] = late
        new = c.csld.dispense_factor(1)
        self.assertGreater(new, old)
        self.assertAlmostEqual(c.csld.dispensed_today(1), 1000.0)


class TheThresholdIsFourNumbersNotOne(unittest.TestCase):
    """FIDELITY K4 and K2a. 576013-818 prints the same four twice -- p.11-11
    under ALARM: PERIODIC TEST FAIL and p.11-9 under ALARM: CSLD RATE INCR
    WARN:

        Single Tanks:      PD - 95% = +0.17 gph   PD - 99% = +0.16 gph
        Manifolded Tanks:  PD - 95% = +0.16 gph   PD - 99% = +0.15 gph

    `csld.RATE = 0.2` was flat, so a 0.17 gph loss passed here and failed on
    a real console at either setting -- and `S613`, the probability of
    detection, reached nothing at all.
    """

    def a_tank(self, tanks=(1,)):
        c = fitted()
        c.software["csld"] = True
        for tank in tanks:
            a_tank(c, tank)
            c.values["S611%02d" % tank] = "01" + "12" + "0" + "7" + "0000"
            c.values["S62F%02d" % tank] = "011"
        return c

    def a_siphoned_pair(self):
        """Tanks 1 and 2 on one siphon bar, which is what S612 holds."""
        c = self.a_tank((1, 2))
        c.values["S61201"] = "0102"
        c.values["S61202"] = "0201"
        return c

    def test_the_four_thresholds_are_the_manuals(self):
        c = self.a_tank()
        self.assertAlmostEqual(c.csld.threshold(1), 0.17)     # 95%, single
        c.values["S61301"] = "012"
        self.assertEqual(c.csld.probability(1), "2")
        self.assertAlmostEqual(c.csld.threshold(1), 0.16)     # 99%, single

    def test_a_console_nobody_has_told_reads_at_ninety_five(self):
        """"1=95%, 2=99%, 3=CUSTOM (Inquiry Command Only)", and CUSTOM
        cannot be SET, so an unset console is at the looser of the two."""
        c = self.a_tank()
        self.assertEqual(c.csld.probability(1), "1")
        c.values["S61301"] = "013"          # CUSTOM, which a Set cannot send
        self.assertEqual(c.csld.probability(1), "1")

    def verdict(self, c, rate, tank=1):
        now = time.mktime(c.now())
        c.csld.detail[tank] = [
            {"at": now - n * 3600.0, "rate": rate, "state": csld.ACCEPTABLE,
             "interval": 100.0, "volume": 5000.0} for n in range(4, 0, -1)]
        c.csld.reported[tank] = now - 25 * 3600.0
        c.csld._analyse(tank, now)
        return c.csld.result_of(tank)

    def test_a_seventeen_hundredths_loss_fails_where_it_used_to_pass(self):
        """The sweep this entry recorded: 0.10 to 0.19 all PASSED."""
        c = self.a_tank()
        self.assertEqual(self.verdict(c, 0.16), csld.PASS)
        self.assertEqual(self.verdict(c, 0.17), csld.FAIL)
        self.assertEqual(self.verdict(c, 0.19), csld.FAIL)

    def test_and_the_tighter_setting_fails_it_sooner(self):
        c = self.a_tank()
        c.values["S61301"] = "012"          # Pd 99%
        self.assertEqual(self.verdict(c, 0.15), csld.PASS)
        self.assertEqual(self.verdict(c, 0.16), csld.FAIL)

    def test_a_siphoned_set_is_judged_at_the_manifolded_threshold(self):
        c = self.a_siphoned_pair()
        self.assertTrue(c.csld.manifolded(1))
        self.assertEqual(c.csld.set_of(1), [1, 2])
        self.assertAlmostEqual(c.csld.threshold(1), 0.16)     # 95%, set
        c.values["S61301"] = "012"
        self.assertAlmostEqual(c.csld.threshold(1), 0.15)     # 99%, set

    def test_a_line_manifold_is_not_a_siphon(self):
        """A siphon carries product between the tanks and a line manifold
        does not: what CSLD measures is the level."""
        c = self.a_tank((1, 2))
        c.values["S61D01"] = "0102"
        self.assertFalse(c.csld.manifolded(1))
        self.assertEqual(c.csld.set_of(1), [1])

    def test_the_secondary_keeps_an_empty_rate_table(self):
        """"**The secondary tank in manifolded sets will have empty rate
        tables!**", p.11-19, annotating an IA51 that prints RATE TABLE
        EMPTY."""
        c = self.a_siphoned_pair()
        for _ in range(40):
            c.clock_offset += 3600.0
            c.tick()
        self.assertTrue(c.csld.table(1), "the primary is the one tested")
        self.assertEqual(c.csld.table(2), [])

    def test_and_the_set_has_one_result_between_it(self):
        """"Tanks programmed as manifolded would have a common result",
        p.11-26, annotating an I61200."""
        c = self.a_siphoned_pair()
        self.assertEqual(self.verdict(c, 0.30, tank=1), csld.FAIL)
        self.assertEqual(c.csld.result_of(2), csld.FAIL)
        self.assertEqual(c.csld.status_line(2), c.csld.status_line(1))
        # and only one alarm between them
        self.assertEqual(c.csld.conditions().count("021401"), 0)
        self.assertEqual([a for a in c.csld.conditions()
                          if a.endswith("02")], [])

    def test_the_same_number_warns_about_a_gain(self):
        """K2a said the rate-increase threshold was the simulator's own. It
        is on the page, one sentence after the one the entry quoted: "a
        higher than acceptable positive increase in product ... The
        threshold amounts are listed below."
        """
        c = self.a_tank()
        self.assertEqual(self.verdict(c, -0.16), csld.PASS)
        self.assertEqual(self.verdict(c, -0.17), csld.INCR)


class TheTwoCsldResultScreens(unittest.TestCase):
    """FIDELITY K7 and K8. 576013-610 p.10-2 draws both of them, and both
    take the same prefix the scheduled tests take: `T #: (Product Name)`
    over `PER: (Results)`."""

    def a_tank(self):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        c.values["S62F01"] = "011"
        return c

    def test_a_console_with_nothing_to_say_says_it_in_its_own_words(self):
        """`COLLECTING n TEST(S)` is not a TLS-350 string: grepped across
        all twenty-five documents, "COLLECTING" as a status appears only in
        577013-950, the TLS-450 serial manual. The TLS-350's word is
        `NO RESULTS AVAILABLE`, which has a section heading of its own at
        p.11-11 and a result code of its own."""
        c = self.a_tank()
        # `PER: (Results)`, and NO RESULTS AVAILABLE is a Results value with
        # a result code of its own -- the same shape the scheduled tests
        # take, `GRS: NO TEST DATA AVAILABLE`. FIDELITY O5.
        self.assertEqual(c.csld.status_line(1), "PER: NO RESULTS AVAILABLE")
        self.assertEqual(c.csld.result_code(1), "03")
        self.assertNotIn("COLLECTING", c.csld.status_line(1))
        c.software["csld"] = False              # not a CSLD tank at all
        self.assertEqual(c.csld.status_line(1), "NO RESULTS AVAILABLE")

    def test_the_result_line_carries_the_year(self):
        """"PER: JAN 22, 1996 PASS", 576013-635 p.62, and every other
        printed example on the shelf agrees. This formatted `%b %d`. The
        full form is 22 characters, so it fits the 24-column panel line."""
        c = self.a_tank()
        when = time.mktime(c.now())
        c.csld.results[1] = (csld.PASS, when)
        line = c.csld.status_line(1)
        self.assertTrue(line.startswith("PER: "), line)
        self.assertIn(time.strftime("%Y", time.localtime(when)), line)
        self.assertTrue(line.endswith(" PASS"), line)
        self.assertLessEqual(len(line), 24, line)

    def test_the_fullest_pass_is_the_fullest_and_not_the_last(self):
        """"This display refers to the one passed test out of all passed
        tests in the last month in which the tank was most full." This
        returned the most recent result if it happened to be a pass,
        ignoring both the volume and the month."""
        c = self.a_tank()
        now = time.mktime(c.now())
        c.csld._passes[1] = [(now - 10 * 86400.0, 9000.0),
                             (now - 5 * 86400.0, 4000.0),
                             (now - 1 * 86400.0, 2000.0)]
        fullest = c.csld.fullest_pass(1)
        self.assertEqual(fullest[1], 9000.0)
        from tls350sim.clock import clock_date
        self.assertIn(clock_date(now - 10 * 86400.0), c.csld.last_pass(1))

    def test_and_it_only_looks_a_month_back(self):
        c = self.a_tank()
        now = time.mktime(c.now())
        c.csld._passes[1] = [(now - 60 * 86400.0, 9000.0),
                             (now - 2 * 86400.0, 2000.0)]
        self.assertEqual(c.csld.fullest_pass(1)[1], 2000.0)
        c.csld._passes[1] = [(now - 60 * 86400.0, 9000.0)]
        self.assertIsNone(c.csld.fullest_pass(1))
        self.assertEqual(c.csld.last_pass(1), "PER: NO RESULTS AVAILABLE")

    def test_both_screens_take_the_per_prefix(self):
        """K8, which is O5's open half: p.10-2 draws `PER: (Results)` under
        both CSLD TEST RESULTS and CSLD FULLEST LAST PASS."""
        c = self.a_tank()
        now = time.mktime(c.now())
        c.csld.results[1] = (csld.PASS, now)
        c.csld._passes[1] = [(now, 5000.0)]
        self.assertTrue(c.live_reading("csld_current", 1).startswith("PER: "))
        self.assertTrue(c.live_reading("csld_last", 1).startswith("PER: "))

    def test_a_pass_is_recorded_with_what_the_tank_held(self):
        c = self.a_tank()
        now = time.mktime(c.now())
        c.csld.detail[1] = [{"at": now - n * 3600.0, "rate": 0.01,
                             "state": csld.ACCEPTABLE, "interval": 100.0,
                             "volume": 6000.0} for n in range(4, 0, -1)]
        c.csld.reported[1] = now - 25 * 3600.0
        c.csld._analyse(1, now)
        self.assertEqual(c.csld.result_of(1), csld.PASS)
        self.assertEqual(c.csld.passes(1)[-1][1], 6000.0)


class WhatCsldNeedsAndWhatSilencesIt(unittest.TestCase):
    """FIDELITY K6's last three, all of them settings the console stored and
    did not read."""

    def a_tank_with(self, probe=None):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        c.values["S62F01"] = "011"
        if probe:
            c.probe_fitted[1] = probe
        return c

    def test_csld_wants_a_point_one_gph_mag_probe(self):
        """576013-623 p.8-2, twice on two pages: "The CSLD option appears
        only when the tank is equipped with a 0.1 gph (0.38 lph) Mag probe,
        and the system has the CSLD software module key installed." This
        checked the key and the probe CARD, so a 0.2 gph probe ran a 0.2 gph
        test it cannot resolve."""
        self.assertTrue(self.a_tank_with("C000").csld.enabled(1))   # MAG1
        self.assertEqual(self.a_tank_with("C000").probe_leak_rating(1), "0.10")
        c = self.a_tank_with("C001")                                # MAG2
        self.assertEqual(c.probe_leak_rating(1), "0.20")
        self.assertFalse(c.csld.enabled(1))
        c = self.a_tank_with("D000")                                # MAG3
        self.assertEqual(c.probe_leak_rating(1), "none")
        self.assertFalse(c.csld.enabled(1))

    def test_a_capacitance_probe_cannot_run_it_either(self):
        c = self.a_tank_with()
        del c.values["S62F01"]              # no float, so not a Mag probe
        self.assertEqual(c.probe_type(1), "CAP0 PROBE")
        self.assertFalse(c.csld.enabled(1))

    def test_report_only_has_four_values_and_two_were_read_as_off(self):
        """576013-623 p.8-9: "Disabled/End of Month/Day 15 and End of
        Month/Day 25 and End of Month". `report_only` tested
        `raw.endswith("1")`, so 2 and 3 -- both ENABLED -- read as
        disabled, and nothing called it at all."""
        c = self.a_tank_with()
        self.assertFalse(c.csld.reporting_inhibited(1))
        for digit in ("1", "2", "3"):
            c.values["S61C01"] = "01" + digit
            self.assertEqual(c.csld.report_only(1), digit)
            self.assertTrue(c.csld.reporting_inhibited(1), digit)
        c.values["S61C01"] = "010"
        self.assertFalse(c.csld.reporting_inhibited(1))

    def test_report_only_inhibits_both_of_the_alarms_it_names(self):
        """"When enabled this feature inhibits the 'No CSLD Idle Time' and
        'CSLD Incr Rate' alarms"."""
        c = self.a_tank_with()
        c.csld.watching[1] = time.mktime(c.now()) - 48 * 3600.0
        c.csld.results[1] = (csld.INCR, time.mktime(c.now()))
        raised = c.csld.conditions()
        self.assertIn("022101", raised)
        self.assertIn("022301", raised)
        c.values["S61C01"] = "013"          # Day 25 and end of month
        self.assertEqual(c.csld.conditions(), [])

    def test_the_rate_table_is_twenty_eight_days_or_eighty_tests(self):
        """"This table contains the last 28 days of leak tests, or a maximum
        of 80 of the most recent tests", 576013-818 p.11-4. This kept 100
        with no age-out at all."""
        c = self.a_tank_with()
        now = time.mktime(c.now())
        c.csld.detail[1] = [{"at": now - n * 3600.0, "rate": 0.0,
                             "state": csld.ACCEPTABLE, "interval": 100.0}
                            for n in range(200, 0, -1)]
        c.csld.samples[1] = [(r["at"], r["rate"]) for r in c.csld.detail[1]]
        c.csld._age_out(1, now)
        self.assertEqual(len(c.csld.detail[1]), csld.CSLD.TABLE_TESTS)
        self.assertEqual(len(c.csld.samples[1]), csld.CSLD.TABLE_TESTS)

        # and the days bite first when the tests are thin on the ground
        c.csld.detail[1] = [{"at": now - n * 86400.0, "rate": 0.0,
                             "state": csld.ACCEPTABLE, "interval": 100.0}
                            for n in range(40, 0, -1)]
        c.csld.samples[1] = [(r["at"], r["rate"]) for r in c.csld.detail[1]]
        c.csld._age_out(1, now)
        self.assertEqual(len(c.csld.detail[1]), int(csld.CSLD.TABLE_DAYS))

    def test_the_result_is_the_average_the_report_prints(self):
        """The result came off `samples[-20:]`, whatever their
        acceptability, where IA52 averages the ACCEPTED tests -- so the
        console announced a verdict from a different average than its own
        report showed. The twenty-test window is `RJT`'s."""
        c = self.a_tank_with()
        now = time.mktime(c.now())
        rows = [{"at": now - n * 3600.0, "rate": 0.02,
                 "state": csld.ACCEPTABLE, "interval": 100.0,
                 "volume": 5000.0} for n in range(6, 0, -1)]
        # a run of rejected tests with a large loss on them, which the old
        # window would have averaged straight into a FAIL
        rows += [{"at": now - n * 60.0, "rate": 5.0,
                  "state": csld.REJECT_DURATION, "interval": 2.0,
                  "volume": 5000.0} for n in range(30, 0, -1)]
        c.csld.detail[1] = rows
        c.csld.samples[1] = [(r["at"], r["rate"]) for r in rows]
        c.csld.reported[1] = now - 25 * 3600.0
        c.csld._analyse(1, now)
        self.assertEqual(c.csld.result_of(1), csld.PASS)


class ContinuousTesting(unittest.TestCase):
    """CSLD: tested while the tank is idle, reported every 24 hours."""

    def a_csld_tank(self, leak=0.0):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        # S611: twelve hours, 0.2 gph, method 7 = CSLD
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        # "The CSLD option appears only when the tank is equipped with a
        # 0.1 gph (0.38 lph) Mag probe", 576013-623 p.8-2. A tank with a
        # float size programmed has a float, and only a Mag has one.
        c.values["S62F01"] = "011"
        c.tank_leak[1] = leak
        return c

    # Two days and a bit. "CSLD Volume Table must be complete" is one of
    # Figure 11-2's three conditions for recording a test, and the volume
    # table is 24 hourly samples, so a console records nothing on its first
    # day -- and the database is analysed every 24 hours after that. See
    # FIDELITY K5 and K6.
    def days(self, c, hours=50):
        for _ in range(hours):
            c.clock_offset += 3600.0
            c.tick()

    def test_it_takes_a_key_and_a_method(self):
        c = self.a_csld_tank()
        self.assertTrue(c.csld.enabled(1))
        c.software["csld"] = False
        self.assertFalse(c.csld.enabled(1))
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "5" + "0200"   # daily instead
        self.assertFalse(c.csld.enabled(1))

    def test_a_tight_tank_passes_without_being_shut_down(self):
        c = self.a_csld_tank(leak=0.02)
        self.days(c)
        self.assertEqual(c.csld.result_of(1), csld.PASS)
        # "CSLD will complete a test after 3 hours and start a new test if
        # the tank remains idle", so an idle day is about eight tests and
        # not one an hour. This asserted more than twenty, which is what a
        # console that sampled instantaneously every hour produced.
        self.assertGreaterEqual(len(c.csld.samples[1]), 6)
        self.assertLessEqual(len(c.csld.samples[1]), 10)
        self.assertEqual(c.leaks.running, {})      # nothing was shut down

    def test_a_leaking_tank_fails(self):
        c = self.a_csld_tank(leak=0.45)
        self.days(c)
        self.assertEqual(c.csld.result_of(1), csld.FAIL)

    def test_a_busy_tank_never_finds_its_idle_time(self):
        c = self.a_csld_tank()
        for hour in range(30):
            c.clock_offset += 3600.0
            # one hose at ten gallons a minute: six hundred an hour, sold
            # down and topped up again, so the tank is never quiet
            c.tank_level[1]["volume"] = 8000.0 - 600.0 * (hour % 12)
            c.tick()
        self.assertEqual(c.csld.samples.get(1, []), [])
        self.assertIn("022101", c.compute_alarms())
        self.assertEqual(describe_alarms(["022101"])[0]["screen"],
                         "T 1:NO CSLD IDLE TIME")

    def test_selling_through_the_meters_also_counts_as_busy(self):
        """The meter map is what the console really knows about dispensing."""
        c = self.a_csld_tank()
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.meter_flow = {1: 250.0}
        for _hour in range(30):
            c.clock_offset += 3600.0
            c.tick()
        self.assertEqual(c.csld.samples.get(1, []), [])

    def test_a_failed_csld_result_posts_the_periodic_fail_alarm(self):
        """CSLD tests at 0.2 gph, so a CSLD failure IS a failed periodic
        test, and a console that finds a leak and says nothing is no use."""
        c = self.a_csld_tank(leak=0.45)
        self.days(c)
        self.assertEqual(c.csld.result_of(1), csld.FAIL)
        self.assertIn("021401", c.compute_alarms())
        self.assertEqual(describe_alarms(["021401"])[0]["description"],
                         "PERIODIC TEST FAIL")

    def test_the_fail_alarm_can_be_switched_off_at_s62d(self):
        c = self.a_csld_tank(leak=0.45)
        c.values["S62D01"] = "01101"          # periodic fail alarm disabled
        self.days(c)
        self.assertEqual(c.csld.result_of(1), csld.FAIL)
        self.assertNotIn("021401", c.compute_alarms())

    def test_a_passing_csld_is_a_passing_periodic_test(self):
        """So the console does not then warn that none has been passed."""
        c = self.a_csld_tank(leak=0.0)
        c.values["S54600"] = "1"
        c.values["S54700"] = "07"
        self.days(c)
        self.assertEqual(c.csld.result_of(1), csld.PASS)
        self.assertNotIn("021601", c.compute_alarms())

    def test_a_big_leak_is_a_quiet_tank_and_csld_fails_it(self):
        """A 60 gph loss is not dispensing, however fast the level falls.

        The old idle threshold was one gallon an hour, so any leak worth
        finding looked like a busy forecourt and CSLD never took a sample.
        """
        c = self.a_csld_tank(leak=60.0)
        self.days(c)
        self.assertTrue(c.csld.samples.get(1))
        self.assertEqual(c.csld.result_of(1), csld.FAIL)

    def test_the_result_reads_on_the_panel_and_over_the_wire(self):
        c = self.a_csld_tank(leak=0.02)
        self.days(c)
        self.assertIn("PASS", c.csld.status_line(1))
        self.assertEqual(c.csld.result_code(1), "01")
        self.assertIn("PASS", c.live_reading("csld_current", 1))
        h = Handler(c, verbose=False)
        self.assertIn(b"0101&&", h.handle(("{}i25101{}".format(chr(1), chr(13)))
                                          .encode()))

    def test_the_rate_table_can_be_deleted_with_its_code(self):
        c = self.a_csld_tank(leak=0.02)
        self.days(c)
        h = Handler(c, verbose=False)
        refused = h.handle(("{}S05401{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"9999", refused)
        h.handle(("{}S05401149{}".format(chr(1), chr(13))).encode())
        self.assertEqual(c.csld.samples.get(1, []), [])


class ResetAndPresets(unittest.TestCase):
    def test_reset_leaves_a_console_out_of_its_box(self):
        c = fitted()
        a_tank(c)
        c.values["S60201"] = "01REGULAR"
        c.set_chart_code("778899")
        c.reset()
        self.assertEqual(c.values, {})
        self.assertEqual(c.tank_level, {})
        self.assertEqual(c.modules, {"probe": 1, "rs232": 1})
        self.assertFalse(c.chart_secured())
        self.assertEqual(c.compute_alarms(), [])

    def test_a_preset_is_a_whole_site(self):
        c = Console()
        self.assertTrue(presets.load(c, "Two-tank retail site"))
        self.assertEqual(sorted(c.programmed_tanks()), [1, 2])
        self.assertEqual(c.text("602", 1), "REGULAR UNLEADED")
        self.assertEqual(c.full_volume(1), 10000.0)
        self.assertTrue(c.has("plld"))
        self.assertTrue(c.licensed("plld020"))
        self.assertGreater(c.tank_level[1]["volume"], 0)
        self.assertIn("PRESSURE LINE LEAK SETUP",
                      [f["function"] for f in c.available_functions()])

    def test_every_preset_loads_cleanly_and_alarms_only_where_meant(self):
        for name in presets.PRESETS:
            c = Console()
            self.assertTrue(presets.load(c, name), name)
            c.in_setup = False
            # the programming is complete enough not to warn about itself
            self.assertEqual([a for a in c.compute_alarms()
                              if a[2:4] == "01"], [], name)

    def test_every_preset_tank_has_a_probe_its_software_can_have(self):
        """FIDELITY R11. `versions.py` gates the capacitance probes -- "Cap 1
        ends at version 8 and Cap 0 at version 17" -- and all three presets
        run version 33, so `supports("cap0")` is False on every one of them.
        Two of the three reported CAP0 PROBE anyway.

        The mechanism is `probe_type`'s honest proxy: with nothing fitted on
        the bench, "a tank programmed with a float size has a float, and only
        a Mag has one". `_tank` wrote no FLOAT SIZE, and the truck stop was
        the only preset that did -- which is why it was the only one that
        read MAG.

        It is not a label. A CAP0 tank reads the old 175-185 gradient band on
        A13 rather than 347-357, a forty sample window where a Mag reads
        twenty, and drops the 0.1 gal/hr row from A20 and A21. The default
        preset the bench opens with is the two-tank retail site, so this was
        what the console showed out of the box.
        """
        for name in presets.PRESETS:
            c = Console()
            self.assertTrue(presets.load(c, name), name)
            c.tick()
            self.assertFalse(c.supports("cap0"), name)
            tanks = sorted(c.programmed_tanks())
            self.assertTrue(tanks, name)
            for tank in tanks:
                self.assertEqual(c.probe_type(tank), "MAG PROBE",
                                 f"{name} tank {tank}")
                self.assertEqual(c.probe_window(tank, "standard"), 20, name)

    def test_a_preset_replaces_the_one_before_it(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        self.assertEqual(len(c.programmed_tanks()), 4)
        presets.load(c, "Two-tank retail site")
        self.assertEqual(len(c.programmed_tanks()), 2)
        self.assertFalse(c.has("vlld"))


class Reconciliation(unittest.TestCase):
    """BIR: what the meters sold against what the probe reads."""

    def a_site(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.meters = {1: 1, 2: 2}
        c.modules["edim"] = 1
        c.meter_flow = {1: 100.0, 2: 50.0}
        start_at(c, 8)
        c.tick()
        return c

    def hours(self, c, n):
        for _ in range(n):
            c.clock_offset += 3600.0
            c.tick()

    def test_a_meter_sells_from_the_tank_it_is_mapped_to(self):
        c = self.a_site()
        before = c.tank_level[1]["volume"]
        self.hours(c, 4)
        self.assertAlmostEqual(before - c.tank_level[1]["volume"], 400.0,
                               delta=1.0)
        self.assertAlmostEqual(c.bir.totals[1], 400.0, delta=1.0)

    def test_an_honest_site_reconciles_to_nothing(self):
        c = self.a_site()
        self.hours(c, 6)
        row = c.bir.current(1)
        self.assertAlmostEqual(row["sales"], 600.0, delta=1.0)
        self.assertAlmostEqual(row["variance"], 0.0, delta=0.5)

    def test_a_leak_turns_up_as_a_variance(self):
        c = self.a_site()
        c.tank_leak[2] = 4.0
        self.hours(c, 6)
        row = c.bir.current(2)
        self.assertLess(row["variance"], -20.0)

    def test_a_delivery_goes_into_the_shift(self):
        c = self.a_site()
        c.meter_flow = {}
        self.hours(c, 1)
        for volume in (1000, 3000, 3000):
            c.tank_level[3]["volume"] = c.tank_level[3]["volume"] + volume
            c.clock_offset += 600.0
            c.tick()
        c.clock_offset += 3600.0
        c.tick()
        self.assertGreater(c.bir.current(3)["deliveries"], 6000.0)
        self.assertAlmostEqual(c.bir.current(3)["variance"], 0.0, delta=5.0)

    def test_closing_a_shift_starts_the_next_one_where_it_left_off(self):
        c = self.a_site()
        self.hours(c, 4)
        rows = c.bir.close("shift")
        self.assertEqual(len(rows), 4)
        closed = c.bir.last(1)
        self.assertAlmostEqual(closed["sales"], 400.0, delta=1.0)
        self.assertEqual(c.bir.current(1)["opening"], closed["physical"])
        self.assertEqual(c.bir.current(1)["sales"], 0.0)

    def test_no_bir_key_no_meter_data_but_the_fuel_still_leaves(self):
        """The dispenser dispenses whether or not the console can account
        for it: the tank goes down on the handle alone, and what the key
        withholds is the booking -- no totals, no events, no period."""
        c = self.a_site()
        c.software["bir"] = False
        before = c.tank_level[1]["volume"]
        self.hours(c, 4)
        self.assertLess(c.tank_level[1]["volume"], before)
        self.assertEqual(sum(c.bir.totals.values()), 0.0)
        self.assertEqual(c.bir.events, [])
        self.assertEqual(c.bir.period, {})

    def test_a_tool_reads_the_shift_and_closes_it(self):
        c = self.a_site()
        self.hours(c, 4)
        h = Handler(c, verbose=False)
        report = h.handle((chr(1) + "IC0300" + chr(13)).encode())
        self.assertIn(b"SHIFT RECONCILIATION REPORT", report)
        self.assertIn(b"SIGNATURE", report)
        h.handle((chr(1) + "S09100" + chr(13)).encode())
        self.assertIsNotNone(c.bir.last(1))


class Fields(unittest.TestCase):
    def test_a_float_limit_is_stored_as_ascii_hex_ieee(self):
        f = FIELDS["S62101"]
        data = fieldio.encode(f, "S62101", "1000")
        self.assertEqual(data, "01" + float_value(1000.0))
        self.assertEqual(fieldio.decode(f, "S62101", data), "1000")

    def test_an_out_of_range_value_is_refused(self):
        f = FIELDS["S77D01"]           # altitude offset, +5.0 to -5.0 PSI
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S77D01", "9")

    def test_the_water_limits_have_two_ceilings(self):
        """"Enter the limit in inches (5.0 maximum) or millimeters (199
        maximum), depending on the units established in System Setup",
        576013-623 Rev AN on both the water warning and the high water
        limit. The metric ceiling was baked into both, so a US console took
        a water warning of 199 INCHES behind a `0.0` mask that cannot even
        draw it. FIDELITY F3.
        """
        f = FIELDS["S62701"]
        self.assertIsNotNone(fieldio.encode(f, "S62701", "4.0", None, False))
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S62701", "199", None, False)
        self.assertIsNotNone(fieldio.encode(f, "S62701", "199", None, True))
        c = Console()
        self.assertFalse(c.metric())
        c.values["S51700"] = "2" + (c.values.get("S51700") or "")[1:]
        self.assertTrue(c.metric())

    def test_the_ranges_the_manual_states_are_the_ranges(self):
        """Four the manual gives outright. FIDELITY F2 and F4."""
        for code, good, bad in (
                # "(between 1k and 100k ohms)", ch.19
                ("S70801", "50000", "10000000"),
                # "LEAK ALARM LIMIT: XX", p.7-18: two digits is the range
                ("S62601", "8", "99999"),
                # "Min. value is 1 litre and max. value is 400 litres"
                ("S63401", "300", "500"),
                ("S63501", "300", "500"),
                # "Allowable range 0.00 (default) to 0.20%"
                ("S63D01", "0.10", "1.0")):
            f = FIELDS[code]
            self.assertIsNotNone(fieldio.encode(f, code, good), code)
            with self.assertRaises(ValueError, msg=code):
                fieldio.encode(f, code, bad)

    def test_the_switchover_thresholds_have_both_ends(self):
        """"For MANIFOLDED:ALTERNATE, the selectable threshold range is 10
        to 999 gallons (37 to 3781 liters) ... For MANIFOLDED:ALTERNATE-HT,
        the selectable threshold range is 1 to 99 inches (25.4 to 2514 mm)"
        -- 576013-623 Rev AN, printed three times: p.10-8 for the pressure
        line leak copy, p.15-3 for the pump sensor one, p.23-5 for the
        external input one. All six fields ran from zero to whatever the
        wire's decimal width allowed, and the height one's ceiling was 200,
        which is the VOLUME default. FIDELITY F1.

        It is the first range whose metric MINIMUM differs: the water limits
        F3 fixed start at zero in both units, so a floor was never needed.
        """
        for code in ("S7D801", "S7DB01", "S81201"):
            f = FIELDS[code]
            self.assertIsNotNone(fieldio.encode(f, code, "200"), code)
            for bad in ("9", "1000"):
                with self.assertRaises(ValueError, msg=code):
                    fieldio.encode(f, code, bad)
            # 37 to 3781 litres: 10 is under the metric floor, 999 is not
            # over its ceiling
            self.assertIsNotNone(fieldio.encode(f, code, "757", None, True))
            self.assertIsNotNone(fieldio.encode(f, code, "3781", None, True))
            with self.assertRaises(ValueError, msg=code):
                fieldio.encode(f, code, "10", None, True)
        for code in ("S7D901", "S7DC01", "S81301"):
            f = FIELDS[code]
            self.assertIsNotNone(fieldio.encode(f, code, "2.0"), code)
            for bad in ("0.5", "100"):
                with self.assertRaises(ValueError, msg=code):
                    fieldio.encode(f, code, bad)
            self.assertIsNotNone(fieldio.encode(f, code, "50.8", None, True))
            self.assertIsNotNone(fieldio.encode(f, code, "2514", None, True))
            with self.assertRaises(ValueError, msg=code):
                fieldio.encode(f, code, "2.0", None, True)

    def test_a_value_outside_an_enumeration_is_refused(self):
        f = FIELDS["S78801"]           # PLLD piping material
        with self.assertRaises(ValueError):
            fieldio.encode(f, "S78801", "99")

    def test_an_int_is_padded_to_the_width_the_wire_wants(self):
        f = FIELDS["S54700"]           # dd, 00-30
        self.assertEqual(fieldio.encode(f, "S54700", "7"), "07")
        self.assertEqual(fieldio.decode(f, "S54700", "07"), "7")

    def test_parts_of_one_function_are_written_side_by_side(self):
        c = Console()
        for fid, value in (("S61101.duration", "12"),
                           ("S61101.rate", "0.10 GAL/HR"),
                           ("S61101.method", "DAILY"),
                           ("S61101.start", "0230")):
            c.values["S61101"] = fieldio.encode(
                FIELDS[fid], "S61101", value, c.values.get("S61101"))
        # TT DD R M HHmm, which is what S611 asks for. The start time lands
        # at offset 4 because the method is DAILY; on ON DATE the schedule
        # takes six bytes first and the time follows them (576013-635 Rev AA
        # p.259), which is what `part_when` is for.
        self.assertEqual(c.values["S61101"], "0112150230")

    def test_the_start_time_follows_whatever_the_schedule_took(self):
        """611 packs DDRM and then a schedule sized by M, so the start time
        does not sit at a fixed offset (576013-635 Rev AA p.259-260)."""
        c = Console()
        for fid, value in (("S61101.duration", "12"),
                           ("S61101.rate", "0.20 GAL/HR"),
                           ("S61101.method", "ON DATE"),
                           ("S61101.ondate", "09/01/2026"),
                           ("S61101.start", "0230")):
            c.values["S61101"] = fieldio.encode(
                FIELDS[fid], "S61101", value, c.values.get("S61101"))
        # TT DD R M YYMMDD HHmm
        self.assertEqual(c.values["S61101"], "011201" + "260901" + "0230")
        self.assertEqual(
            fieldio.decode(FIELDS["S61101.start"], "S61101",
                           c.values["S61101"]), "2:30 AM")
        # and on AUTOMATIC there is no schedule and no start time at all
        c.values["S61101"] = fieldio.encode(FIELDS["S61101.method"], "S61101",
                                            "AUTOMATIC", c.values["S61101"])
        self.assertEqual(
            fieldio.decode(FIELDS["S61101.start"], "S61101",
                           c.values["S61101"]), "")

    def test_writing_one_part_leaves_the_others_alone(self):
        c = Console()
        for fid in ("S62D01.gross", "S62D01.periodic", "S62D01.annual"):
            c.values["S62D01"] = fieldio.encode(FIELDS[fid], "S62D01", "1",
                                                c.values.get("S62D01"))
        self.assertEqual(c.values["S62D01"], "01111")
        c.values["S62D01"] = fieldio.encode(FIELDS["S62D01.periodic"],
                                            "S62D01", "0", c.values["S62D01"])
        self.assertEqual(c.values["S62D01"], "01101")

    def test_every_wired_step_round_trips(self):
        c = fitted()
        for fn in SETUP_MENU:
            for st in fn["steps"]:
                if not st.get("code"):
                    continue
                f = FIELDS[st.get("field") or st["code"]]
                kind = f.get("kind")
                if kind == "enum":
                    # with the console, because a `choices_from` field's
                    # list is this site's devices rather than the manual's
                    value = fieldio.choices_of(f, c)[0][1]
                elif kind == "flag":
                    value = "1"
                elif kind == "time":
                    value = "0630"
                elif kind == "date":
                    # what a keypad types, not what the console stores.
                    # `fieldio.encode` is the PANEL's door and a date typed
                    # at it is the eight cells of its template, MMDDYYYY --
                    # the six-digit stored form is the WIRE's, and reading
                    # one as the other is the bug this pair now guards.
                    value = "08192026"
                elif kind == "int":
                    value = str(f.get("min") if f.get("min") is not None else 1)
                elif kind == "float":
                    lo = f.get("min") if f.get("min") is not None else 0.0
                    value = str(lo)
                elif kind == "profile":
                    value = "4 PTS"
                elif kind == "digits":
                    allow = f.get("allow")
                    w = f.get("width") or 4
                    value = (allow[0] if allow else "00") + "0" * (w - 2)
                elif kind == "schedule":
                    value = fieldio.schedule_text(
                        "0" * (len(f.get("shape", "MWD"))
                               + ("M" in f.get("shape", "MWD"))),
                        f.get("shape", "MWD"))
                elif kind == "text":
                    value = "AB"[:int(f.get("maxlen") or 20)]
                elif kind == "list":
                    # these validate now, and "AB" is a refusal for every one
                    value = wirelists.sample(st["code"])
                else:
                    value = "AB"
                code = st["code"]
                data = fieldio.encode(f, code, value, c.values.get(code),
                                      False, c)
                self.assertIsNotNone(data, code)
                if kind == "profile":
                    continue          # the profile lives in the volume codes
                c.values[code] = data
                self.assertNotEqual(fieldio.decode(f, code, data, c), "",
                                    code)


class ADisplayInquireAnswersLikeTheManual(unittest.TestCase):
    """576013-635 answers a Display inquire with a titled table, and this
    console used to answer with the stored computer payload.

    The titles are read off the manual's own pages by
    tools/build_wire_titles.py, because they cannot be derived: 621's is
    TANK LOW PRODUCT LIMIT where its Function Type is "Set Tank Low LEVEL
    Limit".
    """

    def a_console(self):
        from tests.test_controls import a_site
        return a_site()

    def test_a_limit_comes_back_decoded_not_as_a_float(self):
        """Display is S621TTGGGGGG, six decimal digits; Computer is
        S621TTFFFFFFFF, an ASCII hex IEEE float. I621 used to answer the
        float."""
        _c, h = self.a_console()
        body = send(h, "I62101").replace(chr(1), "").replace(chr(3), "")
        self.assertIn("TANK LOW PRODUCT LIMIT", body)
        self.assertNotIn("0144FA0000", body)
        row = body.splitlines()[-1]
        self.assertRegex(row, r"^ \d ")
        self.assertRegex(row.split()[-1], r"^[0-9.]+$")

    def test_the_answer_is_the_manuals_table_and_not_a_bare_value(self):
        """576013-635 Rev AA p.278 draws the whole response, and it is a
        table: the title, a blank line, the heading and a row per tank. The
        columns are the page's own -- GALLONS begins at column 33 and the
        value right-aligns to 38, which is one column short of the heading's
        right edge and is what the page prints."""
        _c, h = self.a_console()
        rows = send(h, "I62101").replace(chr(1), "").replace(
            chr(3), "").strip(CR + chr(10)).splitlines()
        self.assertEqual(rows[2], "")
        self.assertEqual(rows[3], "TANK LOW PRODUCT LIMIT")
        self.assertEqual(rows[4], "")
        self.assertEqual(rows[5], "TANK   PRODUCT LABEL             GALLONS")
        self.assertEqual(rows[5].index("GALLONS"), 33)
        self.assertEqual(len(rows[6].rstrip()), 39)

    def test_the_display_side_of_device_00_is_a_row_per_tank(self):
        """The trap this closes: a device-00 inquire short-circuited to the
        computer aggregate before the display branch was reached, so I62100
        answered `0144FA00000244FA0000...` -- the computer format wearing the
        display's envelope."""
        c, h = self.a_console()
        rows = send(h, "I62100").replace(chr(1), "").replace(
            chr(3), "").strip(CR + chr(10)).splitlines()[6:]
        self.assertEqual(len(rows), len([n for n in range(1, 17)
                                         if c.values.get("S621%02d" % n)]))
        for n, row in enumerate(rows, start=1):
            self.assertEqual(row.split()[0], str(n))

    def test_the_value_carries_the_precision_the_sample_prints(self):
        """A float decodes with "%g", which drops a trailing zero, so a tank
        diameter of 120 inches came back `120` where 576013-635 Rev AA p.254
        prints `96.00`. The sample is the only statement of the precision the
        display side uses -- the Set mask is `III.hh` on both 607 and 50E and
        neither response agrees with it."""
        c, h = self.a_console()
        c.values["S60701"] = "01" + packed.hexfloat(120.0)
        row = send(h, "I60701").replace(chr(1), "").replace(
            chr(3), "").strip(CR + chr(10)).splitlines()[-1]
        self.assertEqual(row.split()[-1], "120.00")
        self.assertEqual(len(row.rstrip()), 39)

    def test_a_value_with_a_unit_on_it_is_left_alone(self):
        """62F's column prints one decimal and its values are `4.0 IN` and
        `4.0 IN PS`. Reformatting to one decimal would drop the unit, so the
        precision is only applied to a bare number."""
        _c, h = self.a_console()
        body = send(h, "I62F01").replace(chr(1), "").replace(chr(3), "")
        self.assertIn("MAG PROBE FLOAT SIZE", body)
        self.assertRegex(body.splitlines()[-1], r"[0-9]\.[0-9] IN")

    def test_every_title_is_the_manuals_own(self):
        from tls350sim.wire import WIRE_TITLES
        self.assertGreater(len(WIRE_TITLES), 400, "titles not built")
        for code, want in (("62F", "MAG PROBE FLOAT SIZE"),
                           ("621", "TANK LOW PRODUCT LIMIT"),
                           ("604", "TANK FULL VOLUME"),
                           ("101", "SYSTEM STATUS REPORT")):
            self.assertEqual(WIRE_TITLES[code]["title"], want, code)


class TheCommunicationsSetupReport(unittest.TestCase):
    """A comm board is a port, not a run of terminals, so every one of them
    carries a wire count of zero. `_setup_devices` took the largest wire
    count of the cards a function needs, fell back to MODULE_WIRES when that
    was zero, and MODULE_WIRES is zero for the same reason -- so range(1, 1)
    came out empty and the section printed as a title and a rule with no body
    on every console, whatever was fitted.
    """

    def a_console(self):
        from tests.test_controls import a_site
        return a_site()

    def the_function(self):
        from tls350sim.console import SETUP_MENU
        return [f for f in SETUP_MENU
                if f["function"] == "COMMUNICATIONS SETUP"][0]

    def test_a_fitted_comm_card_is_one_position(self):
        """The report walks the bay's POSITIONS, not its cards. The fixture
        has an RS-232, a modem and an EDIM in slots 1, 2 and 3, so it walks
        three -- and the EDIM is not a port anybody sets a baud rate on, so
        it is dropped by what is programmed rather than by being skipped
        here. This used to answer one: the bay was sized by the largest
        count of any ONE kind of card rather than by how many were in it.
        The modem board is FXMOD on the console and SiteFax on the box --
        576013-635 Rev AA prints `COMM BOARD  : 3 (FXMOD)`. M7."""
        from tls350sim import printer
        c, _h = self.a_console()
        self.assertTrue(c.count("rs232"), "fixture should have an RS-232")
        self.assertTrue(c.count("modem"), "fixture should have a modem")
        self.assertEqual(printer._setup_devices(c, self.the_function()),
                         [1, 2, 3])
        self.assertEqual([c.comm_board_name(n) for n in (1, 2, 3)],
                         ["RS-232", "FXMOD", "UNUSED"])
        heads = [l for l in printer.setup_section(c, self.the_function())
                 if str(l).startswith("COMM BOARD")]
        self.assertEqual(len(heads), 2, heads)

    def test_a_programmed_port_reaches_the_paper(self):
        from tls350sim import printer
        c, _h = self.a_console()
        c.values["S88101"] = "011200"
        out = printer.setup_section(c, self.the_function())
        self.assertGreater(len(out), 2, "title and rule with no body")
        self.assertTrue(any("BAUD RATE" in l for l in out))


class SettingATicketedDelivery(unittest.TestCase):
    """576013-635 Rev AA p.398, function 7B5, Set Ticketed Delivery. Its two
    command formats carry the volume differently:

        Display:  <SOH>S7B5TTeeYYMMDDHHmmGGGGGG
        Computer: <SOH>s7B5TTeeYYMMDDHHmmFFFFFFFF

    "GGGGGG" is six decimal digits; "FFFFFFFF" is an ASCII hex IEEE float.
    Both were read with float(), which throws on every well-formed computer
    message, so a host could not set a ticketed delivery at all.
    """

    def a_console(self):
        from tests.test_controls import a_site
        return a_site()

    def test_the_computer_form_reads_its_ieee_float(self):
        c, h = self.a_console()
        out = send(h, "s7B50102" + "2608011200" + "45FA0000")
        self.assertNotIn("9999", out)
        self.assertAlmostEqual(c.deliveries.find(1, "2608011200").ticket,
                               8000.0, places=3)

    def test_the_display_form_still_reads_its_digits(self):
        c, h = self.a_console()
        out = send(h, "S7B50102" + "2608021200" + "008000")
        self.assertNotIn("9999", out)
        self.assertAlmostEqual(c.deliveries.find(1, "2608021200").ticket,
                               8000.0, places=3)


class TheWaterStepsOfInTankSetup(unittest.TestCase):
    """576013-623 Rev AN chapter 7 runs Float Size (7-13), Water Warning
    (7-14), High Water Limit (7-15), Water Alarm Filter (7-15), then
    Programmable Minimum Water Threshold (7-16), then Max or Label Vol.

    This console used to put the threshold immediately after Float Size,
    four steps early, and to show it on a probe the manual says it is
    hidden for.
    """

    def steps(self, console):
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU if f["function"] == "IN-TANK SETUP"][0]
        return [st.get("text", "") for st in console.visible_steps(fn, 1)]

    def test_the_threshold_comes_after_the_alarm_filter(self):
        c = Console()
        order = self.steps(c)
        def at(name):
            return next(i for i, t in enumerate(order) if t.startswith(name))
        self.assertLess(at("Float Size"), at("Water Warning"))
        self.assertLess(at("Water Warning"), at("High Water Limit"))
        self.assertLess(at("High Water Limit"), at("Water Alarm Filter"))
        self.assertLess(at("Water Alarm Filter"), at("Water Minimum"))
        self.assertLess(at("Water Minimum"), at("Max Or Label Volume"))

    def test_a_one_float_probe_hides_the_water_screens(self):
        """576013-623 p.7-14 and p.7-15 annotate the water warning, the high
        water limit and the water alarm filter "This message does not appear
        for tanks in which high alcohol probes are installed", and 576013-610
        says the same of Water Volume and Water Height.

        A high alcohol probe is a one-float probe: Table 9-2 marks MAG4, 5,
        6, 10, 11 and 12 "Water Detect: No". D001 is a MAG4.
        """
        c = Console()
        c.probe_fitted[1] = "D005"          # MAG8, two floats, water yes
        shown = self.steps(c)
        for name in ("Water Warning", "High Water Limit", "Water Alarm Filter"):
            self.assertTrue(any(t.startswith(name) for t in shown), name)
        c.probe_fitted[1] = "D001"          # MAG4, one float, water no
        shown = self.steps(c)
        for name in ("Water Warning", "High Water Limit", "Water Alarm Filter"):
            self.assertFalse(any(t.startswith(name) for t in shown), name)

    def test_a_custom_float_hides_the_threshold(self):
        """"Note: This message does not appear when Float Type has been set
        to Custom." 62F's enumeration makes Custom the value 9."""
        c = Console()
        c.values["S62F01"] = "0"
        self.assertIn("Water Minimum", self.steps(c))
        c.values["S62F01"] = "9"
        self.assertNotIn("Water Minimum", self.steps(c))


class TheConfigurationScreen(unittest.TestCase):
    """I102, SYSTEM CONFIGURATION: what is in each slot and what it reads.

    "POR = ID resistor value of module in this slot read at last system reset.
    C = current ID resistor value." The two columns exist so a technician can
    see whether the card in the slot still reads like itself.
    """

    def a_cage(self):
        c = Console()
        for card in ("probe", "liquid", "smart", "plld", "plldctl",
                     "wplld", "wplldcom"):
            c.modules[card] = 1
        return c

    def test_each_slot_reads_its_own_table_6_1_resistance(self):
        """576013-818 Table 6-1, read off the rendered page: 4 Probe 2K,
        Interstitial/Liquid 200K, 8-Input Smart Sensor 39.2K, PLLD Sensor
        3.9K, PLLD Controller 100K, WPLLD AC Interface 162K, WPLLD Comm 200K.

        This screen used to print crc32 of the module's NAME, which is stable
        per card and means nothing at all.
        """
        nominal = {"probe": 2000, "liquid": 200000, "smart": 39200,
                   "plld": 3900, "plldctl": 100000, "wplld": 162000,
                   "wplldcom": 200000}
        c = self.a_cage()
        seen = {key: por for _s, key, _n, por, _cur in c.slot_readings() if key}
        for key, want in nominal.items():
            self.assertIn(key, seen, key)
            self.assertAlmostEqual(seen[key] / want, 1.0, delta=0.05, msg=key)

    def test_the_two_columns_are_not_the_same_reading(self):
        """A healthy card agrees to within a percent; identical columns would
        mean the console never re-measures, which is the opposite of why the
        screen prints both."""
        c = self.a_cage()
        pairs = [(por, cur) for _s, key, _n, por, cur in c.slot_readings()
                 if key]
        self.assertTrue(pairs)
        self.assertTrue(any(por != cur for por, cur in pairs))
        for por, cur in pairs:
            self.assertAlmostEqual(cur / por, 1.0, delta=0.02)


class Alarms(unittest.TestCase):
    def low_product(self):
        c = Console()
        a_tank(c, volume=5000.0)
        c.values["S62101"] = "01" + float_value(1000.0)
        return c

    def test_a_limit_you_programmed_raises_the_alarm(self):
        c = self.low_product()
        self.assertEqual(c.compute_alarms(), [])
        c.tank_level[1]["volume"] = 500.0
        self.assertEqual(c.compute_alarms(), ["020501"])

    def test_acknowledging_a_live_alarm_only_silences_it(self):
        c = self.low_product()
        c.tank_level[1]["volume"] = 500.0
        c.compute_alarms()
        shown, still, cleared = c.acknowledge()
        self.assertEqual((shown, still, cleared), (1, 1, 0))
        self.assertTrue(c.silenced)
        self.assertEqual(c.compute_alarms(), ["020501"])

    def test_correcting_the_cause_is_not_enough_on_its_own(self):
        """576013-610 Rev AC p.29-1, under MESSAGES: "Warning and Alarm
        Messages display until you correct the cause of the problem. After
        you correct the cause, you must press the ALARM/TEST button to
        acknowledge the alarm and clear the display."

        So refilling the tank silences nothing by itself. The lights follow
        the condition; the message waits for the key.
        """
        c = self.low_product()
        c.tank_level[1]["volume"] = 500.0
        c.compute_alarms()
        c.tank_level[1]["volume"] = 5000.0
        self.assertNotEqual(c.compute_alarms(), [])
        c.acknowledge()
        self.assertEqual(c.compute_alarms(), [])

    def test_a_test_result_latches_until_acknowledged(self):
        c = Console()
        c.conditions = lambda: ["021401"]      # periodic leak test fail
        self.assertTrue(c.latches("021401"))
        self.assertEqual(c.compute_alarms(), ["021401"])
        c.conditions = lambda: []
        self.assertEqual(c.compute_alarms(), ["021401"])
        c.acknowledge()
        self.assertEqual(c.compute_alarms(), [])

    def _with_tracker(self):
        c = self.low_product()
        # Maintenance Tracker wants the NVMEM203 board, so a console without
        # one has the card in it and no code to drive it
        c.board = "E6"
        c.modules["mt"] = True
        c.tank_level[1]["volume"] = 500.0
        c.compute_alarms()
        c.tank_level[1]["volume"] = 5000.0
        self.assertEqual(c.compute_alarms(), ["020501"])
        return c

    def test_maintenance_tracker_protects_the_alarms_on_its_list(self):
        """This test was named `..._protects_every_alarm` and asserted
        exactly that, and the manuals do not.

        576013-610 Rev AC ch.33 p.33-2 scopes the key requirement to a
        subset in one word -- step 3 of the work session is "Acknowledge
        any **protected** alarms" -- and p.32-1 says "protected maintenance
        alarms" as one kind among several. p.29-1 is unconditional the
        other way: "After you correct the cause, you must press the
        ALARM/TEST button to acknowledge the alarm and clear the display."
        Whatever the protected set is, that clear cannot be unreachable for
        every alarm on the console -- and it was, because the gate was the
        presence of the BOARD. A printer PAPER OUT on the shipped
        Compliance site preset needed a contractor's certification key.

        **No page on the shelf lists which alarms are protected**, so the
        list is empty and the mechanism is kept. See the refusals audit R6
        and UNKNOWNS B13.
        """
        c = self._with_tracker()
        self.assertEqual(c.PROTECTED_ALARMS, frozenset())
        c.acknowledge(keyed=False)
        self.assertEqual(c.compute_alarms(), [])

    def test_an_alarm_on_that_list_still_wants_the_key(self):
        """The mechanism, exercised with a list that is empty in the
        shipped console: put the alarm on it and the key is wanted again,
        so the day a page names one it is a one-line change."""
        c = self._with_tracker()
        c.PROTECTED_ALARMS = frozenset({"0205"})
        c.acknowledge(keyed=False)
        self.assertEqual(c.compute_alarms(), ["020501"])
        c.acknowledge(keyed=True)
        self.assertEqual(c.compute_alarms(), [])

    def test_an_alarm_is_described_the_way_the_console_shows_it(self):
        a = describe_alarms(["020501"])[0]
        self.assertEqual(a["screen"], "T 1:LOW PRODUCT ALARM")
        self.assertEqual(a["text"], "Tank 1: LOW PRODUCT ALARM")


class Clock(unittest.TestCase):
    def test_the_console_keeps_the_date_it_was_given(self):
        c = Console()
        c.values["S50100"] = "0301291105"
        self.assertTrue(c.set_clock())
        self.assertEqual(c.clock_text()[:20], "JAN 29, 2003 11:05:0")

    def test_the_display_format_is_the_one_programmed(self):
        c = Console()
        c.values["S50100"] = "0301291105"
        c.set_clock()
        c.values["S50F00"] = "05"
        self.assertTrue(c.clock_text().startswith("29-01-03"))

    def test_the_status_line_is_twenty_four_characters(self):
        c = Console()
        c.values["S50100"] = "0301291105"
        c.set_clock()
        self.assertEqual(len(c.clock_text()), 24)


class Wire(unittest.TestCase):
    def setUp(self):
        self.c = fitted()
        self.h = Handler(self.c, verbose=False)

    def ask(self, command):
        if isinstance(command, str):
            command = command.encode("ascii")
        return self.h.handle(command).decode("ascii")

    def test_a_command_it_cannot_read_gets_the_documented_answer(self):
        """"it will respond with a <SOH>9999FF1B<ETX>", which is what a
        hand-typed telnet session gets when it types half a command."""
        expected = chr(1) + "9999FF1B" + chr(3)
        self.assertEqual(self.ask(chr(1) + "299" + chr(13)), expected)
        self.assertEqual(self.ask(chr(1) + "X10100" + chr(13)), expected)
        self.assertEqual(self.ask(chr(1) + "I99900" + chr(13)), expected)

    def test_a_computer_format_reply_carries_its_checksum(self):
        """"SOH Function Code Data Field && Checksum ETX", and the check adds
        to zero."""
        reply = self.h.handle((chr(1) + "i10100" + chr(13)).encode())
        self.assertIn(b"&&", reply)
        body = reply[:-1]
        self.assertEqual((sum(body[:-4]) + int(body[-4:], 16)) & 0xFFFF, 0)

    def test_a_display_format_reply_does_not(self):
        reply = self.h.handle((chr(1) + "I10100" + chr(13)).encode())
        self.assertNotIn(b"&&", reply)

    def test_a_command_is_parsed_into_its_parts(self):
        self.assertEqual(parse_command(b"\x01123456S60201ABC\r"),
                         ("123456", "S", "602", "01", "ABC"))

    def test_a_value_set_over_the_wire_reads_back(self):
        self.ask("\x01S60201REGULAR\r".encode())
        # a display reply closes CR LF ETX, so the value is not the last
        # thing before the ETX
        self.assertTrue(self.ask("\x01I60201\r")
                        .rstrip("\x03").rstrip().endswith("REGULAR"))

    def test_the_beeper_wants_its_verification_code(self):
        """"149 - This verification code must be sent to confirm the command",
        <SOH>S53000x149."""
        self.assertEqual(self.ask(SOH + "S530000" + CR), NOT_UNDERSTOOD_TEXT)
        self.assertNotIn("S53000", self.c.values)
        self.ask(SOH + "S530001149" + CR)
        self.assertEqual(self.c.values["S53000"], "1")
        # The manual prints one line, `BEEPER: ENABLED`. A real console
        # prints a title, a blank line, and the value under it -- which is
        # what tests/console_capture/raw/I53000.bin holds, so that is what
        # this answers. See tools/console_corrections.json.
        self.assertIn("SYSTEM BEEPER\r\n\r\nENABLED",
                      self.ask(SOH + "I53000" + CR))

    def test_a_missing_module_answers_9999(self):
        self.c.modules["plld"] = False
        # "it will respond with a <SOH>9999FF1B<ETX>"
        self.assertEqual(self.ask("\x01I78101\r"), "\x019999FF1B\x03")

    def test_a_set_for_a_missing_module_is_rejected(self):
        self.c.modules["plld"] = False
        self.assertEqual(self.ask("\x01S781011\r"), "\x019999FF1B\x03")
        self.assertNotIn("S78101", self.c.values)

    def test_the_status_report_carries_the_alarms(self):
        a_tank(self.c, volume=500.0)
        self.c.values["S62101"] = "01" + float_value(1000.0)
        self.assertIn("020501", self.ask(SOH + "i10100" + CR))
        self.assertIn("LOW PRODUCT ALARM", self.ask(SOH + "I10100" + CR))

    def test_all_functions_normal_is_six_zeroes(self):
        self.assertIn("000000", self.ask(SOH + "i10100" + CR))
        self.assertIn("ALL FUNCTIONS NORMAL", self.ask(SOH + "I10100" + CR))

    def test_the_reply_is_stamped_with_the_console_clock(self):
        self.c.values["S50100"] = "0301291105"
        self.c.set_clock()
        self.assertIn("0301291105", self.ask(SOH + "i10100" + CR))

    def test_a_display_reply_carries_the_date_and_the_station_header(self):
        """"<SOH> I10100 / JUL 29, 1997 9:02 AM / STATION HEADER 1...."."""
        self.c.values["S50100"] = "0301291105"
        self.c.set_clock()
        self.c.values["S50301"] = "GREENFIELD SERVICE  "
        lines = self.ask(SOH + "I10100" + CR).strip(SOH + ETX + SEP).split(SEP)
        self.assertEqual(lines[0], "I10100")
        # No seconds. The docstring above quotes the manual's own sample,
        # "JUL 29, 1997 9:02 AM", and this line used to assert the seconds
        # the console was printing against it. 576013-635 draws
        # "MMM DD, YYYY HH:MM XM" over its responses 332 times against
        # twelve of the seconds form, and every one of those twelve
        # describes the DATE/TIME FORMAT setting rather than stamping a
        # report. Every stamp on the real tape agrees.
        self.assertEqual(lines[1], "JAN 29, 2003 11:05 AM")
        # ONE BLANK LINE between the stamp and the header block. The manual
        # draws it in all 128 samples that print a header, and a real
        # console's I10100 carries six blank lines before SYSTEM STATUS
        # REPORT where this sent five. FIDELITY S6.
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "GREENFIELD SERVICE")
        # All four header lines, programmed or not: 576013-635 draws its
        # samples with STATION HEADER 1 through 4 standing in, and the real
        # tape from a site that has set only the first feeds three blanks
        # after it. FIDELITY W5.
        self.assertEqual(lines[4:7], ["", "", ""])
        # and then ONE BLANK LINE before the body, which every one of the
        # manual's samples leaves and this console did not. It looked as
        # though it was already there: three programmed header lines send an
        # empty fourth, and an empty fourth reads exactly like the blank.
        # FIDELITY S6.
        self.assertEqual(lines[7], "")
        self.assertEqual(lines[8], "SYSTEM STATUS REPORT")

    def test_the_inventory_report_answers_i201(self):
        """The command the README tells you to type at a telnet session."""
        a_tank(self.c, volume=5329.0)
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        text = self.ask(SOH + "I20100" + CR)
        self.assertIn("TANK PRODUCT", text)
        self.assertIn("REGULAR UNLEADED", text)
        self.assertIn("5329", text)
        self.c.values["S60301"] = "011"                 # product code 1
        packed = self.ask(SOH + "i20100" + CR)
        # "TT p ssss NN": tank 1, product 1, nothing in progress, and
        # seven eight-character data fields to follow
        self.assertIn("011000007", packed)
        self.assertIn(float_value(5329.0), packed)

    def test_the_short_inventory_command_is_the_comms_check(self):
        """Veeder-Root's TCP/IP Interface Module manual, twice:

            3. Type: <ctrl+A>200
            4. Press Enter. The console's inventory will appear.

        Not in the Serial Interface Manual, which says six characters, but it
        is what a technician is taught and it is what a console does.
        """
        a_tank(self.c, volume=5329.0)
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        text = self.ask(SOH + "200" + CR)
        self.assertIn("TANK PRODUCT", text)
        self.assertIn("REGULAR UNLEADED", text)
        self.assertIn("5329", text)
        # the same with the line feed a telnet client sends after the return
        self.assertIn("TANK PRODUCT", self.ask(SOH + "200" + CR + chr(10)))

    def test_the_short_command_is_the_inventory_and_nothing_else_is_short(self):
        """Three digits is not a general shorthand: 200 is the one Veeder-Root
        documents, and inventing more would teach a technician a command the
        console in front of them may not have."""
        self.assertEqual(parse_command(SOH.encode() + b"200" + CR.encode()),
                         ("", "I", "201", "00", ""))
        for other in ("201", "101", "202"):
            self.assertEqual(self.ask(SOH + other + CR), NOT_UNDERSTOOD_TEXT)

    def test_an_inquiry_needs_no_carriage_return(self):
        """The command format has no terminator: six characters IS the code."""
        self.assertEqual(self.ask(SOH + "I10100"), self.ask(SOH + "I10100" + CR))

    def test_the_revision_report_lists_the_fitted_features(self):
        """`0.10 REPETITIV`, with no last letter, is the console's own word
        for it -- `tests/console_capture/raw/I90200.bin`. The Setup Manual
        spells it Repetitive and the S-Module licence line below it still
        does, which is why the two are not the same string."""
        report = self.ask(b"\x01I90200\r")
        self.assertIn("VERSION", report)
        self.assertIn("\r\nPLLD\r\n  0.10 REPETITIV\r\n", report)
        self.c.modules["plld"] = False
        self.assertNotIn("\r\nPLLD\r\n", self.ask(b"\x01I90200\r"))

    def test_a_feature_is_not_listed_under_two_names(self):
        """CSLD is both a probe-module line and an S-Module licence, and the
        report printed it twice -- `CSLD` and `CONTINUOUS STATISTICAL LEAK
        DETECTION`, one under the other. A real console prints the short one
        alone. See FIDELITY S18."""
        report = self.ask(b"\x01I90200\r")
        self.assertIn("  CSLD\r\n", report)
        self.assertNotIn("CONTINUOUS STATISTICAL LEAK DETECTION", report)

    def test_the_features_run_straight_under_their_heading(self):
        """No blank line between `SYSTEM FEATURES:` and the first of them,
        which is what p.488 draws and not what the console does."""
        self.assertIn("SYSTEM FEATURES:\r\n  PERIODIC IN-TANK TESTS",
                      self.ask(b"\x01I90200\r"))

    def test_the_shift_inventory_report_answers_i204(self):
        self.c.software["bir"] = True
        a_tank(self.c, volume=8518.0)
        self.c.tick()
        text = self.ask(SOH + "I20400" + CR)
        self.assertIn("SHIFT", text)
        self.assertIn("STARTING VALUES", text)
        self.assertIn("ENDING VALUES", text)
        self.assertIn("DELIVERY VALUE", text)
        self.assertIn("TOTALS", text)
        self.c.software.pop("bir")
        self.assertEqual(self.ask(SOH + "I20400" + CR), NOT_UNDERSTOOD_TEXT)

    def test_the_leak_history_report_answers_i207(self):
        a_tank(self.c, volume=5000.0)
        self.c.leaks.start("tank", 1, "periodic", 2.0, False)
        self.c.clock_offset += 3 * 3600
        self.c.tick()
        text = self.ask(SOH + "I20701" + CR)
        self.assertIn("TANK LEAK TEST HISTORY", text)
        # p.65's own titles, all cut to the 24-column roll: `LAST PERIODIC
        # TEST PASSED:` would be 26, and the monthly one is two lines. See
        # FIDELITY H3.
        self.assertIn("LAST PERIODIC TEST PASS:", text)
        self.assertIn("FULLEST PERIODIC TEST", text)
        self.assertIn("PASSED EACH MONTH:", text)

    def test_the_adjusted_delivery_report_counts_what_was_sold_during_it(self):
        """"The adjusted delivery report takes into consideration all
        dispensing that occurred during the delivery."""
        a_tank(self.c, volume=2000.0)
        self.c.software["bir"] = True
        self.c.meters = {1: 1}
        self.c.modules["edim"] = 1
        self.c.meter_flow = {1: 100.0}
        self.c.tick()
        self.c.tank_level[1]["volume"] += 1000.0
        self.c.tick()
        self.c.clock_offset += 3600
        self.c.tick()
        self.c.clock_offset += 3600
        self.c.tick()
        record = self.c.deliveries.last(1)
        self.assertIsNotNone(record)
        self.assertGreater(record.sold, 0.0)
        text = self.ask(SOH + "I20A01" + CR)
        self.assertIn("ADJUSTED DELIVERY REPORT", text)
        self.assertIn("ADJUSTMENT", text)

    def test_stick_height_answers_only_when_it_is_enabled(self):
        """"This command will respond only if stick height is enabled."""
        a_tank(self.c, volume=5000.0)
        self.assertEqual(self.ask(SOH + "I20D00" + CR), NOT_UNDERSTOOD_TEXT)
        self.c.values["S60B00"] = "1"
        self.assertIn("TANK STICK HEIGHT", self.ask(SOH + "I20D00" + CR))

    def test_the_stick_offset_moves_the_stick_height(self):
        a_tank(self.c, volume=5000.0)
        self.c.values["S60B00"] = "1"
        self.c.values["S60701"] = "01" + float_value(96.0)
        plain = self.c.stick_height(1)
        self.c.values["S60C01"] = "01" + float_value(2.0)
        self.assertAlmostEqual(self.c.stick_height(1), plain + 2.0, places=2)

    def test_the_tank_chart_report_walks_the_tank_at_the_step_asked_for(self):
        a_tank(self.c, volume=5000.0)
        self.c.values["S60701"] = "01" + float_value(96.0)
        text = self.ask(SOH + "I21101012000" + CR)     # 12.000 inch steps
        self.assertIn("TANK CALIBRATION CHART", text)
        self.assertIn("DEPTH   CAPACITY", text)
        pairs = self.c.chart_pairs(1, 12.0)
        self.assertEqual(len(pairs), 9)                # 0 to 96 inclusive
        self.assertEqual(pairs[0], (0.0, 0.0))
        self.assertAlmostEqual(pairs[-1][1], self.c.full_volume(1), places=0)

    def test_device_00_aggregates_every_device(self):
        """The COMPUTER side concatenates the lot. The display side of the
        same code answers with the manual's table instead, which is what
        `i` against `I` is for -- see FIDELITY S1."""
        self.c.values["S60201"] = "01FIRST"
        self.c.values["S60202"] = "02SECOND"
        self.assertIn("01FIRST02SECOND", self.ask("\x01i60200\r"))

    def test_the_display_side_of_device_00_is_not_the_aggregate(self):
        self.c.values["S60201"] = "01FIRST"
        self.c.values["S60202"] = "02SECOND"
        reply = self.ask("\x01I60200\r")
        self.assertNotIn("01FIRST02SECOND", reply)
        self.assertIn("TANK   PRODUCT LABEL", reply)
        self.assertIn(" 1     FIRST", reply)
        self.assertIn(" 2     SECOND", reply)


class Printouts(unittest.TestCase):
    def test_the_inventory_report_has_the_manuals_lines(self):
        c = fitted()
        a_tank(c, volume=2549.0)
        c.values["S60201"] = "01REGULAR UNLEADED"
        out = "\n".join(printer.inventory(c))
        for want in ("INVENTORY REPORT", "VOLUME", "ULLAGE", "TC VOLUME",
                     "HEIGHT", "WATER VOL", "WATER", "TEMP"):
            self.assertIn(want, out)
        self.assertIn("2549 GALS", out)

    def test_the_alarm_report_says_whether_the_cause_is_still_there(self):
        c = fitted()
        a_tank(c, volume=500.0)
        c.values["S62101"] = "01" + float_value(1000.0)
        self.assertIn("ACTIVE", "\n".join(printer.alarms(c)))
        self.assertIn("ALL FUNCTIONS NORMAL",
                      "\n".join(printer.alarms(Console())))

    def test_a_setup_report_prints_what_is_programmed(self):
        c = fitted()
        c.values["S78201"] = "01UNLEADED LINE 1"
        out = "\n".join(printer.setup(c, "PRESSURE LINE LEAK SETUP"))
        self.assertIn("UNLEADED LINE 1", out)
        # and nothing that was never programmed
        self.assertNotIn("Mechanical Blender", out)

    def test_the_setup_data_report_covers_every_function(self):
        c = Console()
        presets.load(c, "Two-tank retail site")
        out = "\n".join(printer.setup(c))
        # each function heads its own block, over the display format's rule;
        # there is no report-wide title above them
        self.assertIn("IN-TANK SETUP\n" + printer.SETUP_RULE, out)
        self.assertIn("PRESSURE LINE LEAK SETUP", out)
        self.assertIn("LIQUID SENSOR SETUP", out)
        self.assertIn("REGULAR UNLEADED", out)
        self.assertIn("STP SUMP 1", out)
        # a function the cage cannot serve is not in it
        self.assertNotIn("WPLLD LINE LEAK SETUP", out)


class Gauging(unittest.TestCase):
    def test_the_inventory_screen_follows_the_tank(self):
        c = fitted()
        a_tank(c, volume=2500.0, full=10000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        self.assertEqual(c.live_reading("volume", 1).strip(), "2500 GALS")
        # A quarter of the volume is not a quarter of the depth: a tank lying
        # on its side is widest in the middle, so a quarter full stands about
        # 30 per cent deep. 24.00 was the straight line this used to draw,
        # and 576013-610's own examples rule it out -- 9038 gallons in this
        # same 10,000 gallon, 96 inch tank stands 81.37 inches, where the
        # straight line says 86.76.
        self.assertEqual(c.live_reading("height", 1).strip(), "28.61 INCHES")
        c.tank_level[1]["volume"] = 5000.0
        self.assertEqual(c.live_reading("volume", 1).strip(), "5000 GALS")

    def test_full_volume_comes_from_either_profile(self):
        """604 and 60A are two names for one number, so 604 answers a tank
        whose volume was written through 60A and the other way about.

        576013-818 Rev AB p.12-32 is a real console doing exactly this:
        `I60A00` calls tanks 1 to 3 LINEAR and `I60400` returns the same
        10000, 6000 and 8000 for them. Neither reading overrides the other,
        because on the console there is only one number to read.
        """
        c = fitted()
        c.values["S60401"] = "01" + float_value(8000.0)
        self.assertEqual(c.full_volume(1), 8000.0)
        c.values.clear()
        c.values["S60A01"] = "01" + float_value(9000.0)
        self.assertEqual(c.full_volume(1), 9000.0)

    def test_a_sensor_reads_what_the_bench_is_driving(self):
        c = fitted()
        c.sensor_state[("liquid", "1")] = "fuel"
        self.assertEqual(c.live_reading("sensor_liquid", 1), "FUEL ALARM")
        self.assertEqual(c.live_reading("sensor_liquid", 2), "SENSOR NORMAL")

    def test_pulling_a_card_takes_its_devices_with_it(self):
        c = fitted()
        a_tank(c)
        c.sensor_state[("liquid", "1")] = "fuel"
        c.set_module("probe", False)
        c.set_module("liquid", False)
        self.assertEqual(c.tank_level, {})
        self.assertEqual(c.sensor_state, {})


class ReconciliationMode(unittest.TestCase):
    """The fourth mode: what the periods hold and what they print."""

    def a_site(self):
        c = fitted()
        c.software["bir"] = True
        a_tank(c, volume=5511.0)
        c.values["S60201"] = "01REGULAR UNLEADED   "
        # S615, Meter Data Present: "If dispenser data for this tank is
        # being reported to the DIM ... this parameter MUST be set to YES",
        # and it is what puts the tank in the reconciliation at all.
        c.values["S61501"] = "011"
        c.meters = {1: 1}
        c.tick()
        return c

    def test_closing_a_shift_leaves_the_day_running(self):
        """The periods do not close together: a shift close must not reset
        the day that contains it."""
        c = self.a_site()
        c.bir.adjust(1, 100.0)
        c.bir.close("shift")
        self.assertEqual(c.bir.current(1, "shift")["adjust"], 0.0)
        self.assertEqual(c.bir.current(1, "daily")["adjust"], 100.0)
        self.assertIsNone(c.bir.last(1, "daily"))

    def test_an_adjustment_reaches_a_shift_that_has_closed(self):
        """576013-610 Rev AC p.28-19: "You can adjust the volume for the
        previous or current shift or for any day in the period." Every
        adjustment landed in the open periods, so the previous shift could
        not be adjusted at all and forty gallons meant for it went into the
        running one. Its CALC'D INVNTRY and VARIANCE move with it, and so
        does the day that holds it. FIDELITY Q1."""
        c = self.a_site()
        c.bir.close("shift")
        closed = c.bir.last(1, "shift")
        calculated, variance = closed["calculated"], closed["variance"]
        self.assertIs(c.bir.adjust(1, 40.0, "shift", previous=True), closed)
        self.assertEqual(closed["adjust"], 40.0)
        self.assertEqual(closed["calculated"], calculated + 40.0)
        self.assertEqual(closed["variance"], variance - 40.0)
        self.assertEqual(c.bir.current(1, "shift")["adjust"], 0.0)
        self.assertEqual(c.bir.current(1, "daily")["adjust"], 40.0)

    def test_a_typed_closing_date_reaches_that_day(self):
        """p.28-20: "Enter the desired closing date for the adjustment"."""
        c = self.a_site()
        c.bir.close("daily")
        closed = c.bir.last(1, "daily")
        c.clock_offset += 86400.0
        c.bir.adjust(1, -25.0, "daily", day=closed["closed"])
        self.assertEqual(closed["adjust"], -25.0)
        self.assertEqual(c.bir.current(1, "daily")["adjust"], 0.0)
        self.assertEqual(c.bir.current(1, "shift")["adjust"], 0.0)

    def test_a_period_nobody_holds_takes_no_adjustment(self):
        c = self.a_site()
        self.assertIsNone(c.bir.adjust(1, 40.0, "shift", previous=True))
        self.assertIsNone(c.bir.adjust(1, 40.0, "daily",
                                       day=time.time() - 5 * 86400.0))
        for kind in ("shift", "daily", "weekly", "periodic"):
            self.assertEqual(c.bir.current(1, kind)["adjust"], 0.0)

    def test_sales_run_down_the_tank_and_into_every_period(self):
        c = self.a_site()
        c.modules["edim"] = 1
        c.meter_flow = {1: 300.0}
        c.clock_offset += 3600.0
        c.tick()
        self.assertAlmostEqual(c.tank_level[1]["volume"], 5211.0, places=0)
        for kind in ("shift", "daily", "weekly", "periodic"):
            self.assertAlmostEqual(c.bir.current(1, kind)["sales"], 300.0,
                                   places=0)

    def test_book_inventory_is_the_manuals_arithmetic(self):
        """"opening gauged volume - metered sales + total ticketed delivery
        volume + manual adjustments", on the manual's own printed row.

        576013-610 Rev AC p.28-14's sample: METER SALES 285, TICKET DLVY 800,
        MANUAL ADJ 0, BOOK INV 9704, GAUGED INV 8904, VAR 800 GAL 280.7%. The
        opening volume is the only figure it does not print and the other
        five give it: 9704 + 285 - 800 = 9189.
        """
        c = self.a_site()
        row = c.bir.current(1)
        row.update(opening=9189.0, sales=285.0, ticketed=800.0, adjust=0.0,
                   physical=8904.0)
        self.assertEqual(c.bir.book(row), 9704.0)
        analysis = c.bir.analysis(row)
        self.assertEqual(analysis["book_var"], 800.0)
        self.assertAlmostEqual(analysis["book_pct"], 280.7, places=1)

    def test_the_reconciliation_report_prints_the_manuals_lines(self):
        c = self.a_site()
        out = chr(10).join(printer.reconcile(c, [1]))
        for want in ("SHIFT RECONCILIATION", "OPENING DATE & TIME:",
                     "CLOSING DATE & TIME:", "OPENING VOLUME:", "DELIVERIES:",
                     "METERED SALES:", "MANUAL ADJUSTMENTS:",
                     "CALCULATED INVNTRY:", "GAUGED INVNTRY:", "WATER HEIGHT:",
                     "VARIANCE:"):
            self.assertIn(want, out)
        self.assertIn("5511 GALS", out)

    def test_the_periodic_report_carries_its_threshold(self):
        c = self.a_site()
        out = chr(10).join(printer.reconcile(c, [1], "periodic"))
        self.assertIn("PERIODIC RECONCILIATION", out)
        self.assertIn("THRESHOLD:", out)
        self.assertNotIn("THRESHOLD:", chr(10).join(printer.reconcile(c, [1])))

    def test_the_variance_reports_head_themselves_by_product(self):
        c = self.a_site()
        for report, title in ((printer.delivery_variance, "DELIVERY VARIANCE"),
                              (printer.book_variance, "BOOK VARIANCE"),
                              (printer.variance_analysis, "VARIANCE ANALYSIS")):
            out = chr(10).join(report(c, [1], "daily", False))
            self.assertIn("PROD 1:REGULAR UNLEADED", out)
            self.assertIn(title, out)
            self.assertIn("CURRENT DAILY", out)
            self.assertIn("VOLUMES ARE STANDARD", out)

    def test_the_adjusted_delivery_report_names_the_manifolded_set(self):
        """"an adjusted delivery report is automatically printed for single
        or manifolded tanks"."""
        c = self.a_site()
        a_tank(c, tank=2, volume=1000.0)
        c.values["S61201"] = "0102000000000000"        # tank 1 siphoned to 2
        c.deliveries._last[1] = (5511.0, 0.0)
        c.tank_level[1]["volume"] = 6711.0
        c.deliveries.tick()
        c.clock_offset += 600.0
        c.deliveries.tick()
        record = c.deliveries.last(1)
        self.assertIsNotNone(record)
        out = chr(10).join(printer.adjusted_delivery(c, 1, record))
        self.assertIn("ADJUSTED DELIVERY REPORT", out)
        self.assertIn("T 1:", out)
        self.assertIn("T 2:", out)
        self.assertIn("DELIVERY VOLUME = 1200", out)


class TankerLoads(unittest.TestCase):
    """"the volume of fluid pumped from a tank to a road tanker"."""

    def a_site(self):
        c = fitted()
        a_tank(c, volume=10000.0)
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.values["S51300"] = "1"          # the key-enabled option, on
        c.values["S61001"] = "0101"       # a one minute delivery delay
        c.tick()
        return c

    def pump(self, c, gallons):
        c.tank_level[1]["volume"] -= gallons
        c.clock_offset += 60
        c.tick()
        c.clock_offset += 120
        c.tick()

    def test_a_bulk_draw_is_a_load(self):
        c = self.a_site()
        self.pump(c, 3000.0)
        record = c.loads.load(1)
        self.assertIsNotNone(record)
        self.assertEqual(record.number, 1)
        self.assertAlmostEqual(record.total, 3000.0, places=0)

    def test_dispensing_is_not_a_load(self):
        c = self.a_site()
        self.pump(c, 30.0)
        self.assertEqual(c.loads.all(1), [])

    def test_the_option_key_gates_it(self):
        c = self.a_site()
        c.values["S51300"] = "0"
        self.pump(c, 3000.0)
        self.assertEqual(c.loads.all(1), [])

    def test_loads_are_numbered_in_order(self):
        c = self.a_site()
        self.pump(c, 1000.0)
        self.pump(c, 1000.0)
        self.assertEqual([r.number for r in c.loads.all(1)], [2, 1])

    def test_the_report_prints_the_manuals_lines(self):
        c = self.a_site()
        self.pump(c, 3000.0)
        out = chr(10).join(printer.loads(c, [1]))
        for want in ("TANKER LOAD REPORT", "NUMBER: 1", "LOAD START:",
                     "LOAD END:", "VOLUME    =", "TC VOLUME =", "TEMP      =",
                     "TOTAL     =", "TC TOTAL  ="):
            self.assertIn(want, out)


class LeakHistory(unittest.TestCase):
    """"the last 3.0 gph, the first 0.2 gph, and the first 0.1 gph test
    results for each month"."""

    def a_line(self):
        c = fitted()
        a_tank(c, volume=5000.0)
        return c

    def run_test(self, c, rate_key):
        c.leaks.start("plld", 1, rate_key, None, False)
        c.clock_offset += 3600.0
        c.tick()

    def test_every_test_lands_in_the_history(self):
        """Three results, because a 0.2 gph run is a 3.0 gph run first.

        "Tests always run in the order: 3.0 gph, 0.2 gph, and 0.1 gph" and
        "A 0.2 gph test is automatically preceded by a 3.0 gph test", so
        asking a line for a periodic test files a gross result as well as a
        periodic one.
        """
        c = self.a_line()
        self.run_test(c, "gross")
        self.run_test(c, "periodic")
        log = c.leaks.history[("plld", 1)]
        self.assertEqual(len(log), 3)
        self.assertEqual([r.rate_key for r in log],
                         ["gross", "gross", "periodic"])

    def test_only_the_first_pass_of_each_month_is_listed(self):
        c = self.a_line()
        self.run_test(c, "periodic")
        self.run_test(c, "periodic")
        self.assertEqual(len(c.leaks.first_pass_each_month("plld", 1,
                                                           "periodic")), 1)

    def test_the_report_names_the_line_and_its_passes(self):
        c = self.a_line()
        self.run_test(c, "gross")
        out = chr(10).join(printer.leak_history(c, "plld"))
        self.assertIn("PRESSURE LINE LEAK TEST HISTORY", out)
        self.assertIn("LAST 3.0 GAL/HR PASS:", out)
        self.assertNotIn("NO TEST HISTORY", out)


class TelnetSession(unittest.TestCase):
    """A person typing at the port, rather than a tool talking to it."""

    def serve(self):
        import socket
        import threading
        from tls350sim import presets, wire
        c = Console()
        presets.load(c, "Two-tank retail site")
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        threading.Thread(target=wire.serve,
                         args=(c, "127.0.0.1", port, False, None),
                         daemon=True).start()
        for _ in range(50):
            try:
                sock = socket.create_connection(("127.0.0.1", port), 0.2)
                sock.settimeout(1.0)
                return c, sock
            except OSError:
                time.sleep(0.02)
        self.fail("the server never came up")

    def read(self, sock, wait=0.4):
        time.sleep(wait)
        try:
            return sock.recv(65536)
        except Exception:
            return b""

    def test_the_opening_probe_cannot_be_mistaken_for_a_frame(self):
        """A tool scans for SOH, so the byte the console offers telnet with
        must not contain one."""
        _c, sock = self.serve()
        probe = self.read(sock, 0.2)
        self.assertNotIn(b"\x01", probe)
        sock.close()

    def test_a_tool_gets_the_frame_and_nothing_else(self):
        _c, sock = self.serve()
        self.read(sock, 0.2)
        sock.sendall(SOH.encode() + b"I10100" + CR.encode())
        reply = self.read(sock)
        self.assertTrue(reply.startswith(b"\x01"))
        self.assertTrue(reply.endswith(b"\x03"))
        sock.close()

    def answer_negotiation(self, sock):
        """What a telnet client does when the console offers it options."""
        self.read(sock, 0.2)
        sock.sendall(bytes([255, 251, 3]))          # IAC WILL SGA
        self.read(sock, 0.2)                        # its WILL ECHO back

    def test_a_terminal_sees_what_it_types_including_the_ctrl_a(self):
        """Ctrl-A has nothing to show for itself, and it is the keystroke a
        tech is most likely to have missed, so it echoes as ^A."""
        _c, sock = self.serve()
        self.answer_negotiation(sock)
        for byte in b"\x01200\r":
            sock.sendall(bytes([byte]))
        seen = self.read(sock).decode("ascii", "replace")
        self.assertIn("^A200", seen)
        self.assertIn("TANK PRODUCT", seen)         # "the console's inventory
        self.assertIn("REGULAR UNLEADED", seen)     #  will appear"
        sock.close()

    def test_a_terminal_gets_its_report_without_pressing_return(self):
        _c, sock = self.serve()
        self.answer_negotiation(sock)
        for byte in b"\x01I20100":
            sock.sendall(bytes([byte]))
        seen = self.read(sock).decode("ascii", "replace")
        self.assertIn("^AI20100", seen)
        self.assertIn("TANK PRODUCT", seen)
        self.assertIn("REGULAR UNLEADED", seen)
        sock.close()

    def test_rubbing_out_a_keystroke_rubs_it_out_of_the_command(self):
        _c, sock = self.serve()
        self.answer_negotiation(sock)
        for byte in b"\x01I201X\x08" + b"00":
            sock.sendall(bytes([byte]))
        seen = self.read(sock).decode("ascii", "replace")
        self.assertIn("TANK PRODUCT", seen)         # I20100 got through
        sock.close()

    def test_telnet_negotiation_never_reaches_the_command(self):
        from tls350sim.wire import strip_telnet
        data, saw, left = strip_telnet(bytes([255, 253, 3]) + b"\x01I20100")
        self.assertEqual(data, b"\x01I20100")
        self.assertTrue(saw)
        self.assertEqual(left, b"")
        # half a sequence waits for the rest rather than corrupting the buffer
        self.assertEqual(strip_telnet(b"AB\xff")[2], b"\xff")


class TechCommands(unittest.TestCase):
    """The Troubleshooting Guide's own list of what a technician collects.

    576013-818 chapter 11 prints it in the form they type it, "<Ctrl-A>
    I20100 INVENTORY REPORT": so every one of them has to answer, and none
    of them is three characters long.
    """

    CHEAT_SHEET = [
        ("IA5100", "CSLD RATE TABLE"),
        ("IA5200", "CSLD RATE TEST"),
        ("IA5300", "CSLD VOLUME TABLE"),
        ("IA5400", "CSLD MOVING AVERAGE TABLE"),
        ("I10100", "SYSTEM STATUS REPORT"),
        ("I10200", "SYSTEM CONFIGURATION REPORT"),
        ("I11100", "PRIORITY ALARM HISTORY"),
        ("I11200", "NON-PRIORITY ALARM HISTORY"),
        ("I20100", "INVENTORY REPORT"),
        ("I20200", "DELIVERY REPORT"),
        ("I20600", "TANK ALARM HISTORY REPORT"),
        ("I25100", "CSLD RESULTS"),
        ("I60900", "SET TANK THERMAL EXPANSION COEFFICIENT"),
        ("I61200", "SET TANK MANIFOLDED PARTNERS"),
        ("I61400", "COMMAND CLIMATE FACTOR"),
        ("I77100", "PUMP SENSE CONFIGURATION REPORT"),
        ("I77200", "PUMP SENSOR TANK ASSIGNMENT REPORT"),
        ("I77300", "PUMP SENSOR DISPENSE MODE REPORT"),
        ("IB7100", "PUMP SENSOR DIAGNOSTIC REPORT"),
        ("I78000", "PRESSURE LINE LEAK GENERAL SETUP INQUIRY"),
        ("I7A000", "WPLLD LINE LEAK GENERAL SETUP"),
        ("I75200", "SET VOLUMETRIC LINE LEAK TANK NUMBER"),
        ("I75D00", "SET VOLUMETRIC LINE LEAK DISPENSE MODE"),
    ]

    def a_console(self):
        c = fitted()
        presets.load(c, "Compliance site, CSLD and sensors")
        for card in ("pump", "plld", "wplld", "vlld", "probe", "liquid"):
            c.modules[card] = 1
        c.tick()
        return c

    def test_every_command_a_technician_is_told_to_send_answers(self):
        c = self.a_console()
        h = Handler(c, verbose=False)
        silent = []
        for command, name in self.CHEAT_SHEET:
            reply = h.handle((chr(1) + command + chr(13)).encode())
            if reply == NOT_UNDERSTOOD_TEXT.encode():
                silent.append(f"{command} {name}")
        self.assertEqual(silent, [])

    def test_every_command_on_the_list_is_six_characters(self):
        """"The function code is a six character command code": the whole
        list obeys it. The one short command a technician is taught, 200, is
        not on this list; it is in the TCP/IP module manual instead."""
        for command, _name in self.CHEAT_SHEET:
            self.assertEqual(len(command), 6, command)

    def test_the_configuration_report_lists_the_cage(self):
        c = self.a_console()
        text = Handler(c, verbose=False).handle(
            (chr(1) + "I10200" + chr(13)).encode()).decode()
        self.assertIn("SYSTEM CONFIGURATION", text)
        self.assertIn("POWER ON RESET", text)
        self.assertIn("4 PROBE", text)
        self.assertIn("UNUSED", text)
        c.modules["probe"] = 0
        text = Handler(c, verbose=False).handle(
            (chr(1) + "I10200" + chr(13)).encode()).decode()
        self.assertNotIn("4 PROBE", text)

    def test_the_alarm_history_records_the_clear_as_well_as_the_alarm(self):
        c = self.a_console()
        c.tank_level[3]["water"] = 3.0
        c.tick()
        c.compute_alarms()
        # the Water Alarm Filter holds a water alarm for three minutes
        # before posting it -- 576013-623 Rev AN p.98, FIDELITY N3
        c.clock_offset += 181.0
        c.compute_alarms()
        c.tank_level[3]["water"] = 0.0
        c.tick()
        c.compute_alarms()
        text = Handler(c, verbose=False).handle(
            (chr(1) + "I11100" + chr(13)).encode()).decode()
        self.assertIn("HIGH WATER ALARM", text)
        self.assertIn("ALARM", text)
        self.assertIn("CLEAR", text)

    def test_a_warning_is_not_priority_and_an_alarm_is(self):
        c = self.a_console()
        # 03 is Tank High Water ALARM, 11 is Tank Delivery Needed WARNING,
        # which the panel abbreviates to DELIVERY NEEDED
        self.assertTrue(c.priority({"aa": "02", "nn": "03", "tt": "01"}))
        self.assertFalse(c.priority({"aa": "02", "nn": "11", "tt": "01"}))


class AlarmReduction(unittest.TestCase):
    """577013-814 Rev N Appendix A, the filtered alarms.

    "In TLS-350 Software Version 32, Veeder-Root added filters to reduce
    nuisance alarms ... In the TLS-350 consoles this feature is called Alarm
    Reduction ... (Default is Enabled.)" The setting existed here, defaulted
    to ENABLED, printed on the setup report and was read by nothing.

    Appendix A is two delays per alarm -- how long the condition has to hold
    before the console posts it, and how long it has to be gone before the
    console drops it. Its label and time columns drift against each other in
    every text extraction of it, so the pairing below is read off pp.A-1 and
    A-2 by word position: the two time columns sit at x=381 and x=469 against
    the alarm names at x=73. FIDELITY N3.

    The CLEAR side is asserted against `reduce_alarms` rather than against
    `compute_alarms`, because those are two mechanisms and only one of them
    is on trial here: an alarm whose cause has gone is still LATCHED on the
    display until it is acknowledged, which is Table 29-2's rule and not
    Appendix A's.
    """

    def a_sensor(self, module="liquid", kind="4", code="703"):
        c = Console()
        c.modules[module] = 1
        c.values["S70101" if module == "liquid" else "S74101"] = "011"
        c.values[f"S{code}01"] = "01" + kind
        c.sensor_state[(module, "1")] = "normal"
        c.compute_alarms()
        return c

    def shown(self, c):
        return c.reduce_alarms(c.conditions())

    def test_a_short_takes_two_minutes_to_post_and_fifteen_to_clear(self):
        c = self.a_sensor()
        c.sensor_state[("liquid", "1")] = "short"
        self.assertNotIn("030501", c.compute_alarms())
        c.clock_offset += 119.0
        self.assertNotIn("030501", c.compute_alarms())
        c.clock_offset += 2.0
        self.assertIn("030501", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "normal"
        self.assertIn("030501", self.shown(c))
        c.clock_offset += 14 * 60.0
        self.assertIn("030501", self.shown(c))
        c.clock_offset += 61.0
        self.assertNotIn("030501", self.shown(c))

    def test_an_alarm_state_posts_at_once_and_still_takes_three_to_clear(self):
        """The whole first block of every family's rows is "Immediate"."""
        c = self.a_sensor()
        c.sensor_state[("liquid", "1")] = "fuel"
        self.assertIn("030301", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "normal"
        self.assertIn("030301", self.shown(c))
        c.clock_offset += 179.0
        self.assertIn("030301", self.shown(c))
        c.clock_offset += 2.0
        self.assertNotIn("030301", self.shown(c))

    def test_the_first_open_in_a_day_posts_at_once_and_the_next_waits(self):
        """"* when caused by open circuit and no open alarms within the last
        24-hours: Immediate". The console already keeps the answer: the
        alarm log's `02` rows are the alarms that occurred."""
        c = self.a_sensor()
        c.sensor_state[("liquid", "1")] = "out"
        self.assertIn("030401", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "normal"
        self.shown(c)
        c.clock_offset += 4 * 60.0
        self.assertNotIn("030401", self.shown(c))
        # the second open of the day is the one the filter is for
        c.sensor_state[("liquid", "1")] = "out"
        self.assertNotIn("030401", self.shown(c))
        c.clock_offset += 121.0
        self.assertIn("030401", self.shown(c))

    def test_a_probe_out_takes_two_minutes_and_a_dim_takes_six(self):
        c = Console()
        c.modules["probe"] = 1
        c.modules["edim"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.compute_alarms()
        c.probe_out.add(1)
        c.dim_fault = True
        self.assertNotIn("020901", c.compute_alarms())
        c.clock_offset += 121.0
        got = c.compute_alarms()
        self.assertIn("020901", got)
        self.assertNotIn("190301", got)        # six minutes, not two
        c.clock_offset += 240.0
        self.assertIn("190301", c.compute_alarms())

    def test_a_disabled_dim_is_the_other_alarm_and_the_other_side_of_it(self):
        """576013-610 Rev AC Table 29-19: COMMUNICATION ALARM is "No
        communication between DIM board and an external device", DISABLED
        DIM ALARM is "No communication between ECPU board and DIM board".
        Type 02 against type 03; the category is the DIM's side, 18 for an
        MDIM and 19 for an EDIM. UNKNOWNS A45."""
        from tls350sim.console import describe_alarms
        c = Console()
        c.modules["probe"] = 1
        c.modules["edim"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.dim_disabled.add(1)
        self.assertIn("190201", c.conditions())
        self.assertNotIn("190301", c.conditions())
        self.assertFalse(c.dim_link_ok())
        screens = [a["screen"] for a in describe_alarms(["190201"])]
        self.assertEqual(screens, ["E 1:DISABLED DIM ALARM"])
        c.modules = {"probe": 1, "mdim": 1}
        self.assertIn("180201", c.conditions())

    def test_a_tank_level_alarm_is_not_filtered_by_this(self):
        """Appendix A filters the sensor families, the probe and the two
        comm alarms, and nothing in it is about a tank's level. The one
        tank alarm that IS delayed, High Water, is delayed by its own older
        setting instead -- see TheWaterAlarmFilter."""
        c = Console()
        c.modules["probe"] = 1
        c.tank_level[1] = {"volume": 9500.0, "water": 0.0}
        c.values["S62801"] = "01" + float_value(10000.0)
        c.values["S62201"] = "01" + float_value(90.0)
        self.assertIn("020701", c.compute_alarms())

    def test_switching_it_off_makes_every_alarm_instant_again(self):
        c = self.a_sensor()
        c.set_setting("alarm_reduction", "DISABLED")
        c.sensor_state[("liquid", "1")] = "short"
        self.assertIn("030501", c.compute_alarms())

    def test_and_so_does_software_that_never_had_it(self):
        """"In TLS-350 Software Version 32, Veeder-Root added filters": a
        console running version 31 filters nothing, and the setting is not
        on its menu either."""
        c = self.a_sensor()
        c.version = 31
        self.assertFalse(c.supports("alarmreduce"))
        c.sensor_state[("liquid", "1")] = "short"
        self.assertIn("030501", c.compute_alarms())


class TheWaterAlarmFilter(unittest.TestCase):
    """576013-623 Rev AN p.98, and a different feature from Alarm Reduction.

    "To help prevent false water alarms during deliveries, the Water Alarm
    Filter allows the user to select from several filters that will delay
    the posting of a water alarm. The filter choices are: Low (default),
    Medium, High and Off ... The medium and high filter selections will
    inhibit the water alarm during a delivery ... The Low, Medium and High
    selections all use a 3 minute delay before posting a water alarm. If a
    Water Alarm delay of less than 3 minutes is desired, select Off for the
    Water Alarm Filter. The filter level Off does not inhibit alarms during
    the delivery and the delay time is programmable from 30 to 180 seconds
    (default)."

    Both settings existed, held the right options and the right range, and
    the console posted High Water the instant the level crossed the limit.
    FIDELITY N3.
    """

    def a_tank(self):
        c = Console()
        c.modules["probe"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.values["S62401"] = "01" + float_value(2.0)
        c.compute_alarms()
        return c

    def test_the_default_filter_holds_a_water_alarm_for_three_minutes(self):
        c = self.a_tank()
        c.tank_level[1]["water"] = 3.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 179.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 2.0
        self.assertIn("020301", c.compute_alarms())

    def test_off_is_not_no_filter_but_a_programmable_one(self):
        c = self.a_tank()
        c.set_setting("water_filter", "OFF", 1)
        c.set_setting("water_delay", "030", 1)
        c.tank_level[1]["water"] = 3.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 31.0
        self.assertIn("020301", c.compute_alarms())

    def test_and_off_left_alone_is_the_same_three_minutes(self):
        """Its own default is 180 seconds, which is the other three."""
        c = self.a_tank()
        c.set_setting("water_filter", "OFF", 1)
        c.tank_level[1]["water"] = 3.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 179.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 2.0
        self.assertIn("020301", c.compute_alarms())

    def a_delivery(self, c):
        """Pour product in until the console calls it a delivery."""
        c.values["S60701"] = "01" + float_value(96.0)
        # thirty minutes of quiet before the console calls the delivery
        # finished, so the test has room to look while one is running
        c.values["S61001"] = "0130"
        c.deliveries.tick()
        c.tank_level[1]["volume"] = 6000.0
        c.clock_offset += 60.0
        c.deliveries.tick()
        self.assertIsNotNone(c.deliveries.in_progress(1))

    def test_medium_and_high_inhibit_it_during_a_delivery(self):
        c = self.a_tank()
        c.set_setting("water_filter", "MEDIUM", 1)
        self.a_delivery(c)
        c.tank_level[1]["water"] = 3.0
        c.clock_offset += 600.0
        c.deliveries.tick()
        self.assertNotIn("020301", c.compute_alarms())
        # and the three minutes start when the delivery ends, rather than
        # having run through it
        c.clock_offset += 1860.0
        c.deliveries.tick()
        self.assertIsNone(c.deliveries.in_progress(1))
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 181.0
        self.assertIn("020301", c.compute_alarms())

    def test_low_does_not_inhibit_it_during_a_delivery(self):
        """"The default filter setting of Low provides a quick response and
        will not inhibit a water alarm during a delivery."""
        c = self.a_tank()
        self.a_delivery(c)
        c.tank_level[1]["water"] = 3.0
        self.assertNotIn("020301", c.compute_alarms())
        c.clock_offset += 181.0
        self.assertIsNotNone(c.deliveries.in_progress(1))
        self.assertIn("020301", c.compute_alarms())


class TheSixThermistorsAverageToTheAverage(unittest.TestCase):
    """Table 29-4 does not describe the reported temperature as a reading
    beside the channels; it describes it as the average OF them, "average
    temperature of all submerged thermistors". C12 to C17 were a second
    ladder built beside the first -- 780 counts a step, 1.8 F, 9.1 F across
    the six against A15's own sample of five -- and it only ever ADDED, so
    the mean of the six channels sat four and a half degrees above the
    average the console reported for the same tank at the same moment.
    FIDELITY Y5."""

    def a_site(self):
        from tls350sim import presets
        c = Console(None)
        presets.load(c, "Truck stop, four tanks and BIR")
        c.tick()
        return c

    def implied(self, c, tank):
        """The six channels read back as temperatures, which is what the
        counts are: 17000 at 48 F and 6000 counts across the band.

        C12 is the TOP of the probe and C17 the bottom, so this comes back
        top first -- the reverse of the ladder, which A15 prints bottom
        first.
        """
        return [48.0 + (c.probe_channel(tank, n) - 17000.0) / 6000.0 * 14.0
                for n in range(12, 18)]

    def implied_ladder(self, c, tank):
        """The same six, bottom first, so they line up with the ladder."""
        return list(reversed(self.implied(c, tank)))

    def test_the_submerged_channels_average_to_the_reported_temperature(self):
        """The whole of Table 29-4's sentence, SUBMERGED included: a
        half-empty tank is measuring only its own bottom half."""
        c = self.a_site()
        for tank in sorted(c.tank_level):
            temps = self.implied_ladder(c, tank)
            under = c.submerged_thermistors(tank)
            self.assertTrue(under, f"tank {tank}")
            self.assertAlmostEqual(sum(temps[n] for n in under) / len(under),
                                   c.product_temperature(tank), places=3,
                                   msg=f"tank {tank}")

    def test_a_full_tank_reads_what_it_always_did(self):
        """The ladder's offsets sum to zero, so with all six under the fuel
        the average is the wander itself -- which is what keeps this from
        being a change of reading dressed as a change of model."""
        c = self.a_site()
        tank = sorted(c.tank_level)[0]
        c.tank_level[tank]["volume"] = c.full_volume(tank)
        self.assertEqual(len(c.submerged_thermistors(tank)), 6)
        ladder = c.thermistor_ladder(tank)
        self.assertAlmostEqual(sum(ladder) / 6.0,
                               c.product_temperature(tank), places=6)

    def test_an_emptier_tank_reads_warmer(self):
        """The bottom of the probe is the warm end, so dropping the level
        takes the cool thermistors out of the average. Dropping a tank from
        6,200 gallons to 200 used to change not one count."""
        c = self.a_site()
        tank = sorted(c.tank_level)[0]
        c.tank_level[tank]["volume"] = c.full_volume(tank)
        full = c.product_temperature(tank)
        c.tank_level[tank]["volume"] = c.full_volume(tank) * 0.1
        self.assertGreater(c.product_temperature(tank), full)

    def test_the_spread_is_a15_s_own_five_degrees(self):
        """"72.6 at T6 down to 67.6 at T1, five degrees over the six"."""
        c = self.a_site()
        for tank in sorted(c.tank_level):
            temps = self.implied(c, tank)
            self.assertLess(max(temps) - min(temps), 7.0, f"tank {tank}")
            self.assertGreater(max(temps) - min(temps), 2.0, f"tank {tank}")

    def test_the_bottom_of_the_probe_is_the_warm_end(self):
        """The fuel has been longest out of the weather down there, which is
        the order A15 prints them in and the sign Figure 11-4 shows."""
        c = self.a_site()
        temps = self.implied(c, 1)
        self.assertLess(temps[0], temps[-1])          # C12 cooler than C17
        self.assertLess(c.probe_top_temperature(1), c.product_temperature(1))

    def test_the_counts_are_not_a_perfect_ladder(self):
        """A probe whose six channels are evenly spaced to the count is not
        a probe. The noise is centred rather than removed."""
        c = self.a_site()
        counts = c.thermistor_counts(1)
        steps = [b - a for a, b in zip(counts, counts[1:])]
        self.assertGreater(max(steps) - min(steps), 20.0)

    def test_the_temperature_list_and_the_counts_are_one_ladder(self):
        """They were two, which is F9's shape: two stores that never meet."""
        c = self.a_site()
        listed = c.probe_temperatures(1)
        implied = self.implied_ladder(c, 1)
        under = c.submerged_thermistors(1)
        # the SUBMERGED ones agree exactly, because that is the set Table
        # 29-4 averages and the set the measurement noise is centred on
        self.assertAlmostEqual(sum(listed[n] for n in under) / len(under),
                               sum(implied[n] for n in under) / len(under),
                               places=3)
        # and the whole six agree to within that centring
        self.assertAlmostEqual(sum(listed) / 6.0, sum(implied) / 6.0,
                               delta=1.0)
        # the same span to within the measurement noise on the counts, which
        # is +/- 0.38 F a channel and is deliberately still there
        self.assertAlmostEqual(max(listed) - min(listed),
                               max(implied) - min(implied), delta=0.8)


class BuriedProductMovesAtTheSpeedOfTheSeason(unittest.TestCase):
    """The console states its own limits, in the leak test's invalidation
    criteria -- 576013-610 Rev AC Table 29-4, p.29-6:

        TEMP CHANGE TOO LARGE     "average temperature of all submerged
                                   thermistors changed by more than 0.1 F
                                   (0.06 C) per hour"
        CHANGE IN TANK TEMP ZONE  "a submerged thermistor's temperature
                                   changed by more than 0.3 F per hour"

    and 576013-818 Figure 11-4 logs a real probe at about 0.027 F/hr. This
    bench moved at 0.82 F/hr hour over hour and 18.7 F/hr over five minutes,
    so every in-tank leak test on every shipped preset would have aborted the
    moment those criteria were implemented. FIDELITY Y4."""

    LIMIT = 0.1

    def a_tank(self):
        c = Console()
        c.modules["probe"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        return c

    def rates(self, c, window, count):
        """|dT| per hour over `count` consecutive windows of `window`
        seconds, asked of the reading itself rather than of a clock."""
        base = time.mktime(c.now())
        out = []
        for k in range(count):
            a = c.product_temperature(1, at=base + k * window)
            b = c.product_temperature(1, at=base + (k + 1) * window)
            out.append(abs(b - a) * 3600.0 / window)
        return out

    def test_hour_over_hour_is_inside_the_console_s_own_limit(self):
        c = self.a_tank()
        self.assertLess(max(self.rates(c, 3600.0, 48)), self.LIMIT)

    def test_and_so_is_a_five_minute_window(self):
        """The window matters: a sine sampled hourly can hide a much faster
        excursion inside the hour, and this one did -- 0.82 measured hour
        over hour against 18.7 measured over five minutes."""
        c = self.a_tank()
        self.assertLess(max(self.rates(c, 300.0, 288)), self.LIMIT)

    def test_it_lands_near_the_rate_a_real_probe_was_logged_at(self):
        """Figure 11-4, every thirty seconds for 22.5 minutes: about 0.027
        F/hr on the average. Not fitted to it -- the period was solved for
        half of Table 29-4's limit -- so this is a check, not a target."""
        c = self.a_tank()
        self.assertLess(max(self.rates(c, 3600.0, 48)), 0.05)

    def test_it_still_moves(self):
        """A reading pinned to a constant is the defect this one replaced."""
        c = self.a_tank()
        base = time.mktime(c.now())
        far = c.product_temperature(1, at=base + 40 * 86400.0)
        self.assertNotAlmostEqual(c.product_temperature(1, at=base), far,
                                  places=3)

    def test_every_thermistor_is_inside_its_own_limit_too(self):
        """"A submerged thermistor's temperature changed by more than 0.3 F
        per hour" is the looser of the two, and the six are drawn from the
        average with a fixed spread, so they move with it."""
        c = self.a_tank()
        base = time.mktime(c.now())
        for k in range(24):
            was = c.probe_top_temperature(1, at=base + k * 3600.0)
            now = c.probe_top_temperature(1, at=base + (k + 1) * 3600.0)
            self.assertLess(abs(now - was), 0.3)


class TheDeliveryReportWaitsForTheFuelToSettle(unittest.TestCase):
    """"There will be a delay of at least four minutes between the end of
    the delivery and the printing of the report while the console waits for
    the fuel level in the tank to stabilize" -- 576013-939 Rev F p.3. The
    programmable delay on top of it is a different thing: S610's own screen
    says it "prevents generation of false reports during the intervals
    between multi-compartment drops to one tank". This console had only the
    setting, so `DELIVERY DELAY: 01` wrote the report three minutes early.
    FIDELITY Y3."""

    def a_site(self, minutes=1, bir=False):
        from tls350sim import presets
        c = Console(None)
        presets.load(c, "Two-tank retail site")
        c.values["S61001"] = f"01{minutes:02d}"
        if bir:
            c.software["bir"] = True
            c.modules["edim"] = 1
            c.meters = {1: 1}
            c.values["S61501"] = "011"     # METER DATA PRESENT: YES
        c.tick()
        return c

    def drop(self, c, gallons=3000.0):
        c.tank_level[1]["volume"] += gallons
        c.tick()

    def test_one_minute_programmed_still_waits_four(self):
        c = self.a_site(minutes=1)
        self.assertEqual(c.delivery_delay(1), 1)
        self.assertEqual(c.settling_delay(1), 4)
        self.drop(c)
        for _ in range(3):
            c.clock_offset += 60.0
            c.tick()
        self.assertIsNone(c.deliveries.last(1))
        c.clock_offset += 120.0
        c.tick()
        self.assertIsNotNone(c.deliveries.last(1))

    def test_a_longer_setting_is_the_one_that_wins(self):
        """The floor is a floor, not the answer."""
        c = self.a_site(minutes=15)
        self.assertEqual(c.settling_delay(1), 15)
        self.drop(c)
        c.clock_offset += 10 * 60.0
        c.tick()
        self.assertIsNone(c.deliveries.last(1))
        c.clock_offset += 6 * 60.0
        c.tick()
        self.assertIsNotNone(c.deliveries.last(1))

    def test_the_setting_itself_is_untouched(self):
        """S610 is what the panel walks and the wire echoes, and it still
        reads back what was programmed."""
        c = self.a_site(minutes=2)
        self.assertEqual(c.delivery_delay(1), 2)

    def test_bir_is_given_the_amount_delivered_not_the_gauge_rise(self):
        """"Takes into consideration all dispensing that occurred during the
        delivery": the tank rose by less than was delivered, by exactly what
        was sold while the tanker was on the ground. I20A and the manifold
        report both added it back and BIR did not, so the shortfall came out
        again as unexplained variance."""
        c = self.a_site(bir=True)
        self.drop(c, 3000.0)
        record = c.deliveries.in_progress(1)
        self.assertIsNotNone(record)
        record.sold = 120.0                 # sold while the drop ran
        c.clock_offset += 5 * 60.0
        c.tick()
        done = c.deliveries.last(1)
        self.assertIsNotNone(done)
        self.assertAlmostEqual(
            c.bir.row(1)["deliveries"], done.amount + done.sold, places=3)


class TheTicketResultCodes(unittest.TestCase):
    """7B5 and 7B6 are the only codes on this shelf that document a RESULT
    code, and every rejection came back as the generic 9999 -- which the
    manual defines as "a function code that it does not recognize". The
    console recognised it perfectly well; it would not say what was wrong
    with the data. FIDELITY S2."""

    def a_site(self):
        c = fitted()
        a_tank(c, volume=2000.0)
        c.values["S61001"] = "01" + float_value(1.0)
        c.deliveries.tick()
        for volume in (3000, 5000, 5000):
            c.tank_level[1]["volume"] = float(volume)
            c.clock_offset += 60.0
            c.deliveries.tick()
        c.clock_offset += 400.0
        c.deliveries.tick()
        return c, c.deliveries.last(1)

    def stamp(self, record):
        return time.strftime("%y%m%d%H%M", time.localtime(record.end["at"]))

    def ask(self, c, text):
        return Handler(c, verbose=False).handle(
            (chr(1) + text).encode()).decode("latin-1")

    def result(self, c, text):
        """The RR out of a computer-format reply: SOH, the echoed code, the
        clock, then TT p PP RR."""
        body = self.ask(c, text).split("&&")[0]
        return body[1 + 6 + 10:][5:7]

    def test_a_good_command_answers_00_and_the_data_follows(self):
        c, record = self.a_site()
        got = self.result(c, "s7B50101" + self.stamp(record) + "459CA000")
        self.assertEqual(got, "00")
        self.assertGreater(record.ticket, 0.0)

    def test_bir_not_enabled_is_01(self):
        c, record = self.a_site()
        c.software["bir"] = False
        self.assertEqual(
            self.result(c, "s7B50101" + self.stamp(record) + "459CA000"),
            "01")

    def test_an_invalid_tank_is_02(self):
        c, record = self.a_site()
        self.assertEqual(
            self.result(c, "s7B59901" + self.stamp(record) + "459CA000"),
            "02")

    def test_a_missing_stamp_is_03_and_a_non_numeric_one_is_04(self):
        c, _record = self.a_site()
        self.assertEqual(self.result(c, "s7B50101" + "2609"), "03")
        self.assertEqual(self.result(c, "s7B50101" + "26XX041231"), "04")

    def test_an_invalid_date_is_05_and_an_invalid_time_is_06(self):
        """Two codes, and which half of the stamp is wrong decides which."""
        c, _record = self.a_site()
        self.assertEqual(self.result(c, "s7B50101" + "2699041231"), "05")
        self.assertEqual(self.result(c, "s7B50101" + "2609049999"), "06")

    def test_a_delivery_that_has_not_happened_yet_is_07(self):
        """"Date out of range of period": a stamp in the future is in no
        period at all."""
        c, _record = self.a_site()
        ahead = time.strftime("%y%m%d%H%M",
                              time.localtime(time.mktime(c.now()) + 86400))
        self.assertEqual(self.result(c, "s7B50101" + ahead + "459CA000"),
                         "07")

    def test_editing_a_delivery_that_is_not_there_is_08(self):
        """"If there is no matching time/date for edit" -- 01 is edit."""
        c, _record = self.a_site()
        past = time.strftime("%y%m%d%H%M",
                             time.localtime(time.mktime(c.now()) - 86400))
        self.assertEqual(self.result(c, "s7B50101" + past + "459CA000"), "08")

    def test_a_bad_volume_is_09(self):
        c, record = self.a_site()
        self.assertEqual(
            self.result(c, "s7B50101" + self.stamp(record) + "ZZZZZZZZ"),
            "09")
        # and the display form, whose volume is six decimal digits, says so
        # in words -- no error form is drawn for it anywhere on this shelf
        said = self.ask(c, "S7B50101" + self.stamp(record) + "ABCDEF")
        self.assertIn("RESULT CODE 09", said)
        self.assertNotIn("9999", said)

    def test_inserting_over_a_gauged_delivery_is_10(self):
        """"Try to insert when gauged exists" -- 02 is insert."""
        c, record = self.a_site()
        self.assertEqual(
            self.result(c, "s7B50102" + self.stamp(record) + "459CA000"),
            "10")

    def test_7b6_s_list_stops_at_08(self):
        """The last two codes are about a VOLUME, and 7B6 carries a Bill of
        Lading number instead. Two tables, not one."""
        from tls350sim.wire import Handler as H
        self.assertEqual(H.VOLUME_RESULTS, ("bad_volume", "already_gauged"))
        c, record = self.a_site()
        # inserting over a gauged delivery is not an error for 7B6
        self.assertEqual(
            self.result(c, "s7B60102" + self.stamp(record) + "EXX23223"),
            "00")
        self.assertEqual(record.bol, "EXX23223")

    def test_an_error_returns_the_code_and_nothing_after_it(self):
        """"If an error occurs, just error code will be returned"."""
        c, record = self.a_site()
        good = self.ask(c, "s7B50101" + self.stamp(record) + "459CA000")
        c.software["bir"] = False
        bad = self.ask(c, "s7B50101" + self.stamp(record) + "459CA000")
        self.assertLess(len(bad), len(good))
        self.assertEqual(bad.split("&&")[0][1 + 6 + 10:][5:7], "01")

    def test_nothing_answers_9999_any_more(self):
        c, record = self.a_site()
        for bad in ("s7B59901" + self.stamp(record) + "459CA000",
                    "s7B50101" + "2609",
                    "S7B50101" + self.stamp(record) + "ABCDEF"):
            self.assertNotIn("9999", self.ask(c, bad), bad)

    def test_the_display_form_prints_the_manual_s_report(self):
        c, record = self.a_site()
        out = self.ask(c, "S7B50101" + self.stamp(record) + "005050")
        rows = out.replace(chr(13) + chr(10), chr(10)).split(chr(10))
        self.assertIn("SET TICKETED DELIVERY", rows)
        head = [r for r in rows if "VARIANCE" in r][0]
        for word, col in (("TICKET", 25), ("GAUGE", 41), ("VARIANCE", 55)):
            self.assertEqual(head.index(word), col, word)
        # the reply closes CR LF ETX, so the last text row is not the last
        # element of the split
        row = [r for r in rows if r.rstrip(chr(3)).strip()][-1]
        row = row.rstrip(chr(3)).rstrip()
        self.assertTrue(row.endswith("2050.0"), repr(row))
        self.assertEqual(row.index("5050.0") + 6, 32)   # right to column 31


class TemperatureCompensationReadsItsTwoInputs(unittest.TestCase):
    """No manual on this shelf states the TC formula -- twenty-four were
    searched. What they state are the two INPUTS, and both were ignored: the
    reference temperature was a literal 60.0 in `tc_volume` while S50E held
    the site's, and the coefficient was a module constant applied to every
    tank while S609 held each tank's own. FIDELITY Y6."""

    def a_site(self, coeff=None, product="REGULAR UNLEADED", temp=None):
        c = Console()
        c.modules["probe"] = 1
        c.values["S60201"] = "01" + product.ljust(20)[:20]
        c.values["S60A01"] = "01" + float_value(10000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        if coeff is not None:
            c.values["S60901"] = "01" + float_value(coeff)
        if temp is not None:
            c.tank_temp = {1: temp}
        return c

    def test_the_reference_temperature_is_the_site_s(self):
        """"In the U.S., the reference temperature used to calculate TC
        volume is normally 60 F. In other countries, this value may differ.
        Canada, for example, uses 15 C." Fifteen Celsius is 59 F."""
        c = self.a_site()
        self.assertEqual(c.tc_reference(), 60.0)
        c.values["S50E00"] = float_value(59.0)
        self.assertEqual(c.tc_reference(), 59.0)

    def test_moving_the_reference_moves_the_corrected_volume(self):
        c = self.a_site(coeff=0.00070)
        was = c.tc_volume(1)
        c.values["S50E00"] = float_value(59.0)
        self.assertNotAlmostEqual(c.tc_volume(1), was, places=3)

    def test_the_coefficient_is_the_tank_s_own(self):
        """"You must enter the Coefficient of Thermal Expansion for the fuel
        in each tank"."""
        petrol = self.a_site(coeff=0.00070)
        diesel = self.a_site(coeff=0.00045)
        self.assertNotAlmostEqual(petrol.tc_volume(1), diesel.tc_volume(1),
                                  places=3)
        for c, coeff in ((petrol, 0.00070), (diesel, 0.00045)):
            away = c.product_temperature(1) - 60.0
            self.assertAlmostEqual(c.tc_volume(1), 5000.0 * (1 - coeff * away),
                                   places=4)

    def test_an_unprogrammed_tank_falls_back_and_says_so(self):
        c = self.a_site()
        self.assertAlmostEqual(c.tank_coefficient(1), Console.THERMAL_EXPANSION)

    def test_table_7_1_by_product_label(self):
        """576013-623 Rev AN Table 7-1, "Typical Thermal Coefficients"."""
        for label, want in (("DIESEL", 0.00045), ("REGULAR UNLEADED", 0.00070),
                            ("KEROSENE", 0.00050), ("LPG", 0.00160),
                            ("DEF", 0.00025), ("JET FUEL", 0.00047),
                            ("ALCOHOL", 0.00063)):
            self.assertEqual(Console.thermal_coefficient(label), want, label)

    def test_the_longest_match_wins(self):
        """PREMIUM UNLEADED is PREMIUM's row, not REGULAR UNLEADED's -- they
        agree here, so the one that proves it is the B100 against DIESEL."""
        self.assertEqual(Console.thermal_coefficient("BIODIESEL B100"), 0.00044)
        self.assertEqual(Console.thermal_coefficient("DIESEL #2"), 0.00045)
        self.assertIsNone(Console.thermal_coefficient("MOGAS"))

    def test_every_preset_tank_carries_its_own_product_s_coefficient(self):
        """It wrote unleaded's 0.00070 into every tank, so the truck stop's
        diesel was 56 percent high."""
        from tls350sim import presets
        for name in presets.PRESETS:
            c = Console(None)
            presets.load(c, name)
            for tank in sorted(c.tank_level):
                want = Console.thermal_coefficient(c.text("602", tank))
                if want is None:
                    continue
                self.assertAlmostEqual(c.tank_coefficient(tank), want,
                                       places=6, msg=f"{name} tank {tank}")

    def test_bir_s_temperature_variance_is_the_row_s_own_tank(self):
        """It read `limit("609", 1)` and applied tank 1's coefficient to
        every tank on the site, under a comment saying "the tank's own"."""
        from tls350sim import presets
        c = Console(None)
        presets.load(c, "Truck stop, four tanks and BIR")
        c.software["bir"] = True
        c.modules["edim"] = 1
        c.tick()
        diesel, petrol = c.bir.row(1), c.bir.row(3)
        self.assertEqual(diesel["tank"], 1)
        self.assertEqual(petrol["tank"], 3)
        for row, coeff in ((diesel, 0.00045), (petrol, 0.00070)):
            row = dict(row, physical=10000.0, temp_open=50.0, temp_close=70.0)
            self.assertAlmostEqual(c.bir.analysis(row)["temp_var"],
                                   10000.0 * coeff * 20.0, places=3)


class TheDebrisOnTheBottomOfTheTank(unittest.TestCase):
    """"When there is not water in the tank, but the water height
    measurement is not 0.0, the water float is resting on a layer of debris
    on the bottom of the tank. The Water Minimum Threshold sets the level."
    576013-623 Rev AN p.7-16, range 0.0 to 1.0 inches.

    The setting has been programmable from the panel, echoed on the wire and
    printed on the setup report since the field was written, and no reading
    ever looked at it. FIDELITY Y7.

    **It is `648` on this console and it was `60E`.** The two codes hold the
    same idea for two different floats: `648` Set Probe Water Minimum is the
    screen p.7-16 draws, and `60E`'s fourth column is the CUSTOM float's own
    -- "This message does not appear when Float Type has been set to Custom"
    against "CUSTOM float size must be chosen (Function Code 62F) for these
    parameters to be set and used". The panel step wrote 60E on a console
    with a 2 inch float, which is what the tape's own IN-TANK block prints.
    FIDELITY R13."""

    def a_tank(self, threshold=None, water=0.0, custom=False):
        c = Console()
        c.modules["probe"] = 1
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.values["S60A01"] = "01" + float_value(10000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        c.values["S62401"] = "01" + float_value(2.0)     # high water at 2in
        c.tank_level[1] = {"volume": 5000.0, "water": water}
        if custom:
            c.values["S62F01"] = "019"                   # FLOAT SIZE: CUSTOM
        if threshold is not None:
            if custom:
                c.values["S60E01"] = "01" + "0" * 24 + float_value(threshold)
            else:
                c.values["S64801"] = "01" + float_value(threshold)
        return c

    def test_a_custom_float_reads_its_own_column_instead(self):
        """Both halves at once, because a console reads exactly one of them:
        648 programmed on a custom console is ignored, and 60E's fourth
        column on a console that is not custom is ignored."""
        c = self.a_tank(threshold=0.8, water=0.6, custom=True)
        self.assertAlmostEqual(c.water_minimum(1), 0.8, places=5)
        c.values["S64801"] = "01" + float_value(0.2)
        self.assertAlmostEqual(c.water_minimum(1), 0.8, places=5)
        d = self.a_tank(threshold=0.8, water=0.6)
        d.values["S60E01"] = "01" + "0" * 24 + float_value(0.2)
        self.assertAlmostEqual(d.water_minimum(1), 0.8, places=5)

    def test_no_threshold_reads_the_float_where_it_is(self):
        c = self.a_tank(water=0.6)
        self.assertEqual(c.water_minimum(1), 0.0)
        self.assertAlmostEqual(c.water_height(1), 0.6)
        self.assertGreater(c.water_volume(1), 0.0)

    def test_below_the_threshold_there_is_no_water(self):
        c = self.a_tank(threshold=0.8, water=0.6)
        self.assertAlmostEqual(c.water_minimum(1), 0.8, places=5)
        self.assertEqual(c.water_height(1), 0.0)
        self.assertEqual(c.water_volume(1), 0.0)
        self.assertEqual(c.live_reading("water", 1).strip(), "0.00 INCHES")

    def test_above_it_the_float_is_water_again(self):
        c = self.a_tank(threshold=0.8, water=1.37)
        self.assertAlmostEqual(c.water_height(1), 1.37)
        self.assertAlmostEqual(c.water_volume(1), 28.8, places=1)

    def test_it_is_one_correction_and_the_alarms_follow_it(self):
        """A correction to the measurement, not a gate on the alarm: a
        console told where its debris is does not raise High Water on it."""
        c = self.a_tank(threshold=1.0, water=0.9)
        c.compute_alarms()
        c.clock_offset += 200.0
        self.assertNotIn("020301", c.compute_alarms())
        c.tank_level[1]["water"] = 3.0
        c.compute_alarms()
        c.clock_offset += 200.0
        self.assertIn("020301", c.compute_alarms())

    def test_the_range_is_the_manual_s_own(self):
        """"within the range of 0.0 to 1.0 inches", so a stored value
        outside it is clamped rather than believed."""
        c = self.a_tank(threshold=4.0, water=2.0)
        self.assertEqual(c.water_minimum(1), 1.0)
        self.assertAlmostEqual(c.water_height(1), 2.0)


class TheGroundThermistorCurve(unittest.TestCase):
    """FIDELITY X7. 576013-818 Rev AB Figure 6-22 prints THREE points on the
    same page as the screen, and this console was fitted to two of them.

        40 F  -> 26100
        70 F  -> 11880
        100 F ->  5820

    A two-parameter beta model pinned to the ends gave 11812 at 70 F, sixty
    eight ohms out. The comment defending it said 0.6% is "closer than the
    screen prints", which is true and is not the same as reproducing the
    figure: a manual gives three points because two do not determine the
    curve. `ln R = A + B/T + C/T^2` is the standard three-point thermistor
    model, written the way round that gives resistance from temperature.
    """

    FIGURE = ((40.0, 26100.0), (70.0, 11880.0), (100.0, 5820.0))

    def test_it_passes_through_all_three(self):
        for fahrenheit, ohms in self.FIGURE:
            self.assertAlmostEqual(console.ground_ohms(fahrenheit), ohms,
                                   delta=1.0, msg=f"{fahrenheit} F")

    def test_it_falls_all_the_way_down_the_range(self):
        """A thermistor's resistance drops as it warms, and the clamp runs
        -40 to 140 F. A curve that reproduces three points and turns round
        between them is not a thermistor."""
        readings = [console.ground_ohms(f) for f in range(-40, 141)]
        for cooler, warmer in zip(readings, readings[1:]):
            self.assertGreater(cooler, warmer)

    def test_a_healthy_thermistor_never_reads_as_a_fault(self):
        """"If Value = <1000 thermistor may be shorted ... >200,000 open."
        Ground under a forecourt is not at -26 F or 188 F, which is where
        this curve crosses those two."""
        for fahrenheit in range(0, 121):
            ohms = console.ground_ohms(fahrenheit)
            self.assertGreater(ohms, 1000.0, f"{fahrenheit} F reads shorted")
            self.assertLess(ohms, 200000.0, f"{fahrenheit} F reads open")


class SensorTypes(unittest.TestCase):
    """A sensor can only report what its resistance bands can distinguish."""

    def a_sensor(self, module="liquid", kind=None, code="703"):
        c = Console()
        c.modules[module] = 1
        c.values["S70101" if module == "liquid" else "S74101"] = "011"
        if kind is not None:
            c.values[f"S{code}01"] = "01" + kind
        c.sensor_state[(module, "1")] = "normal"
        return c

    def test_a_tri_state_sump_sensor_has_fuel_and_out_and_nothing_else(self):
        """794380-208, the sump sensor, is Tri-State: Normal, Fuel, Open."""
        c = self.a_sensor(kind="1")
        self.assertEqual(set(c.sensor_states("liquid", 1)), {"fuel", "out"})
        self.assertEqual(c.sensor_reading("liquid", 1), "SENSOR NORMAL")
        c.sensor_state[("liquid", "1")] = "water"
        self.assertEqual(c.sensor_reading("liquid", 1), "SENSOR NORMAL")
        c.sensor_state[("liquid", "1")] = "fuel"
        self.assertIn("030301", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "water"
        self.assertNotIn("030601", c.compute_alarms())
        # The FUEL ALARM two lines up is latched and unacknowledged, so the
        # reading is still FUEL ALARM -- what the console is SAYING, which
        # is what the wire has always answered and what 576013-610 p.15-1
        # requires of SENSOR NORMAL. What this test is about is that WATER
        # is not among the words a Tri-State sensor can produce.
        # FIDELITY O27.
        self.assertNotIn("WATER", c.sensor_reading("liquid", 1))
        c.sensor_state[("liquid", "1")] = "short"
        self.assertNotIn("030501", c.compute_alarms())

    def test_a_discriminating_pan_sensor_has_the_full_set(self):
        c = self.a_sensor(kind="4")
        self.assertEqual(set(c.sensor_states("liquid", 1)),
                         {"fuel", "out", "short", "high", "warn"})
        c.sensor_state[("liquid", "1")] = "short"
        # a SHORT is one of Appendix A's filtered alarms and takes two
        # minutes to post; see AlarmReduction below. FIDELITY N3.
        self.assertNotIn("030501", c.compute_alarms())
        c.clock_offset += 120.0
        self.assertIn("030501", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "warn"
        self.assertIn("031001", c.compute_alarms())
        self.assertEqual(c.sensor_reading("liquid", 1), "LIQUID WARNING")

    def test_a_hydrostatic_sensor_cannot_report_fuel(self):
        """A brine-filled interstice reads a level, not a hydrocarbon."""
        c = self.a_sensor(kind="3")
        self.assertNotIn("fuel", c.sensor_states("liquid", 1))
        self.assertIn("low", c.sensor_states("liquid", 1))
        c.sensor_state[("liquid", "1")] = "low"
        self.assertIn("030901", c.compute_alarms())

    def test_a_normally_closed_sensor_has_one_alarm(self):
        c = self.a_sensor(kind="2")
        self.assertEqual(set(c.sensor_states("liquid", 1)), {"fuel"})

    def test_a_groundwater_sensor_has_water_out_not_water(self):
        c = Console()
        c.modules["gw"] = 1
        self.assertIn("waterout", c.sensor_states("gw", 1))
        self.assertNotIn("water", c.sensor_states("gw", 1))
        c.sensor_state[("gw", "1")] = "waterout"
        self.assertIn("070701", c.compute_alarms())

    def test_a_vapor_sensor_has_water_but_no_liquid_levels(self):
        c = Console()
        c.modules["vapor"] = 1
        states = set(c.sensor_states("vapor", 1))
        self.assertEqual(states, {"fuel", "out", "short", "water"})

    def test_the_vapor_channel_follows_its_own_threshold(self):
        """576013-818 Fig 6-18: "Normal = 200 - Threshold Value; Fuel =
        >1.05 times the Threshold Value ... or > 4 times the Threshold
        Value", and "This Sensor Threshold value is used in the Value 2
        calculations above."

        The band was a fixed 200-900, below the 1k floor the threshold
        itself can be set to, so the channel sat pinned near the short
        boundary whatever the site programmed. FIDELITY R6.
        """
        import struct
        from tls350sim import readings
        c = Console()
        c.modules["vapor"] = 1

        def at(ohms):
            c.values["S70801"] = "01" + struct.pack(">f", ohms).hex().upper()
            return readings.sensor_value(c, "vapor", 1, "normal", 2)

        low, high = at(1000.0), at(100000.0)
        self.assertLess(low, 1000.0)
        self.assertGreater(high, 1000.0)
        self.assertGreater(high, low * 10)
        # and the short boundary is the manual's 200 whatever the threshold
        self.assertLess(readings.sensor_value(c, "vapor", 1, "short", 2),
                        200.0)
        # a fuel reading is four times the threshold, the arm of the rule
        # that does not need 24 hours of persistence to decide
        c.values["S70801"] = "01" + struct.pack(">f", 5000.0).hex().upper()
        self.assertGreater(readings.sensor_value(c, "vapor", 1, "fuel", 2),
                           4.0 * 5000.0 * 0.99)

    def test_the_ppm_screen_is_not_the_ohms_screen(self):
        """FIDELITY R7. 576013-818 Fig 6-18 draws two screens, `1 = XXXX
        2 = XXXXXX` and `XXXX  PPM`, and says what the second one is:
        "sensor detected hydrocarbon vapor (see value 2 above) CONVERTED TO
        PARTS PER MILLION". This printed value 2 itself with ` PPM` written
        after it -- the resistance under a heading that says it is not one,
        and at a width the manual draws two characters narrower.

        The two ends are the manual's. 576013-623 Rev AN p.19-2: "measure
        the resistance across the V and G terminals ... multiply the
        measured resistance by 4 to determine the vapor threshold value".
        So a clean well sits at a quarter of its own threshold, and the
        threshold is where the console starts calling it fuel. The scale
        between them is the simulator's: UNKNOWNS A30.
        """
        import struct
        from tls350sim import readings
        c = Console()
        c.modules["vapor"] = 1
        c.values["S70801"] = "01" + struct.pack(">f", 20000.0).hex().upper()

        self.assertEqual(readings.vapor_ppm(c, 1, 5000.0), 0.0)
        self.assertEqual(readings.vapor_ppm(c, 1, 200.0), 0.0)
        self.assertAlmostEqual(readings.vapor_ppm(c, 1, 12500.0), 5000.0,
                               delta=1.0)
        self.assertAlmostEqual(readings.vapor_ppm(c, 1, 20000.0),
                               readings.VAPOR_PPM_FULL, delta=0.01)
        # above the threshold it is already calling it fuel, and the screen
        # is four digits wide
        self.assertEqual(readings.vapor_ppm(c, 1, 80000.0),
                         readings.VAPOR_PPM_FULL)

        # and it follows the site's own threshold, which is the point of R6:
        # the same resistance means far less vapour in a well whose clean
        # reading was twice as high
        c.values["S70801"] = "01" + struct.pack(">f", 40000.0).hex().upper()
        self.assertAlmostEqual(readings.vapor_ppm(c, 1, 12500.0), 833.25,
                               delta=1.0)
        self.assertEqual(readings.vapor_ppm(c, 1, 10000.0), 0.0)

    def test_the_ppm_screen_and_the_wire_read_one_sensor(self):
        """`B07`'s VAPOR CONCENTRATION takes its number off the panel's own
        screen, so the two cannot drift -- which is only true while it keeps
        doing that."""
        import struct
        from tls350sim.wire import Handler
        c = Console()
        c.set_module("vapor", 1)
        c.values["S70601"] = "011"
        c.values["S70201"] = "01VAPOR WELL #1".ljust(22)
        c.values["S70801"] = "01" + struct.pack(">f", 20000.0).hex().upper()
        c.tick()
        with held_clock():
            screen = c.diag_reading("sensor_ppm", 1)
            ppm = screen.split()[0]
            self.assertNotEqual(ppm, "0")
            report = Handler(c, verbose=False).handle(b"\x01IB0701\r")
        self.assertIn(ppm.encode(), report)

    def a_universal(self, kind="6"):
        """A Universal Sensor Module with position 1 wired and labelled."""
        c = Console()
        c.modules["universal"] = 1
        c.values["S74B01"] = "011"                  # SENSOR CONFIG
        c.values["S74C01"] = "01STP SUMP NORTH      "
        c.values["S74D01"] = "01" + kind            # 6 = ULTRA/Z-1
        c.values["S74E01"] = "0101"                 # CATEGORY
        c.sensor_state[("universal", "1")] = "normal"
        return c

    def test_the_universal_sensor_reads_the_whole_alarm_list(self):
        """FIDELITY L2. 576013-635 Rev AA's 34B notes give this category the
        full list -- `0002=Sensor Fuel Alarm` through `0009=Sensor Liquid
        Warning` -- which is `SENSOR_STATE_NN` entire. `sensor_states`
        returned `()` for the module, so `sensor_alarm_allowed` refused
        every state and a universal sensor could only ever read NORMAL.
        """
        c = self.a_universal()
        self.assertEqual(set(c.sensor_states("universal", 1)),
                         set(SENSOR_STATE_NN))
        c.sensor_state[("universal", "1")] = "fuel"
        self.assertIn("130301", c.compute_alarms())
        self.assertEqual(c.sensor_reading("universal", 1), "FUEL ALARM")
        c.sensor_state[("universal", "1")] = "waterout"
        self.assertIn("130701", c.compute_alarms())

    def test_the_universal_sensor_has_a_type_and_a_bench_card(self):
        """74D is Set Universal Sensor Type and 74B/74C are its config and
        label. `SENSOR_TYPE_CODE` and `SENSOR_CODES` had every other family
        in them and not this one, so the type read as "" and the sensor
        never reached `programmed_sensors` -- which is what the bench builds
        its cards from.
        """
        c = self.a_universal(kind="7")                 # ULTRA/Z-1 HV
        self.assertEqual(c.sensor_type("universal", 1), "7")
        self.assertIn(("universal", 1, "STP SUMP NORTH"),
                      c.programmed_sensors())

    def test_an_unprogrammed_universal_sensor_warns_like_the_others(self):
        """A configured position with no category is a Setup Data Warning on
        every other family, and `SETUP_CHECK` skipped this one."""
        c = self.a_universal()
        self.assertEqual([w for w in c.setup_warnings()
                          if w.startswith("13")], [])
        c.values["S74E01"] = ""
        self.assertIn("130201", c.setup_warnings())

    def test_both_channels_read_ohms_and_not_nought_and_one(self):
        """`readings.BANDS` had no `universal` key, so `sensor_value` fell
        through to its `(0, 1)` default and the diagnostic report printed
        VALUE1 = 0 and VALUE2 = 1 where B4B's own sample prints 5200 and
        100000.
        """
        from tls350sim import readings
        c = self.a_universal()
        for channel, floor in ((1, 1000.0), (2, 10000.0)):
            value = readings.sensor_value(c, "universal", 1, "normal",
                                          channel)
            self.assertGreater(value, floor,
                               f"channel {channel} read {value}")

    def test_the_bands_move_with_the_state(self):
        """Which band the resistance falls in IS the sensor's state, which
        is the whole point of the diagnostic screen."""
        from tls350sim import readings

        def at(state, channel=1):
            return readings.sensor_value(self.a_universal(), "universal", 1,
                                         state, channel)

        self.assertLess(at("out"), at("normal"))
        self.assertGreater(at("short"), at("high"))
        self.assertGreater(at("fuel", 2), at("normal", 2) * 2)

    def test_a_two_wire_ultra_2_cannot_discriminate_water(self):
        c = Console()
        c.modules["2wire"] = 1
        c.values["S74301"] = "011"                  # ULTRA 2, two states
        self.assertNotIn("water", c.sensor_states("2wire", 1))
        c.values["S74301"] = "012"                  # the discriminating one
        self.assertIn("water", c.sensor_states("2wire", 1))

    def test_high_vapor_mode_holds_the_fuel_alarm_back(self):
        """"In High Vapor Mode, a Fuel alarm is posted only if a High liquid
        or a Liquid Warning condition also exists"."""
        c = Console()
        c.modules["3wire"] = 1
        c.values["S74801"] = "012"                  # ULTRA/Z-1 HV
        c.sensor_state[("3wire", "1")] = "fuel"
        self.assertNotIn("120301", c.compute_alarms())
        c.values["S74801"] = "011"                  # standard mode
        self.assertIn("120301", c.compute_alarms())

    def test_a_smart_sensor_uses_its_own_alarm_numbers(self):
        c = Console()
        c.modules["smart"] = 1
        c.values["S72301"] = "0103"                 # MAG SENSOR
        self.assertIn("install", c.sensor_states("smart", 1))
        c.sensor_state[("smart", "1")] = "install"
        self.assertIn("281401", c.compute_alarms())
        c.sensor_state[("smart", "1")] = "fuel"
        self.assertIn("280501", c.compute_alarms())   # 05, not 03
        c.values["S72301"] = "0104"                 # a Vac sensor instead
        self.assertNotIn("fuel", c.sensor_states("smart", 1))
        self.assertIn("novacuum", c.sensor_states("smart", 1))


class ProbeChannels(unittest.TestCase):
    """The nineteen channels behind IN-TANK DIAGNOSTIC.

    These used to be modelled as per-segment sensitivity ratios near 100,
    which is a different report altogether (IA06, and only a CAP probe has
    one). The Troubleshooting Guide's own template for the A12 command names
    them: WATER, then ten product heights, then two temperature references
    and six thermistors. They are raw counts.
    """

    def a_gauged_tank(self, diameter=96.0, volume=6000.0, water=0.0):
        c = Console()
        c.modules["probe"] = 1
        c.values["S60101"] = "011"
        c.values["S60701"] = "01" + struct.pack(">f", diameter).hex().upper()
        c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        c.values["S62F01"] = "011"                 # a Mag probe
        c.tank_level[1] = {"volume": volume, "water": water}
        c.tick()
        return c

    def test_a_height_channel_is_the_height_times_the_gradient(self):
        c = self.a_gauged_tank()
        gradient = c.probe_gradient(1)
        expected = c.stick_height(1) * gradient
        self.assertAlmostEqual(c.probe_channel(1, 1), expected, delta=20.0)

    def test_the_gradient_sits_in_the_manuals_own_band(self):
        """"Normal operating range 175 - 185 or 347 - 357"."""
        c = self.a_gauged_tank()
        self.assertTrue(347.0 <= c.probe_gradient(1) <= 357.0,
                        c.probe_gradient(1))

    def test_the_gradient_does_not_follow_the_product(self):
        """The manual's own site reads one gradient across three grades."""
        c = self.a_gauged_tank()
        c.values["S60301"] = "011"                 # regular
        petrol = c.probe_gradient(1)
        c.values["S60301"] = "013"                 # diesel
        self.assertEqual(c.probe_gradient(1), petrol)

    def test_the_water_channel_is_dry_under_1500(self):
        """"All Probes - C00 (No Water) - 0 - 1500"."""
        c = self.a_gauged_tank(water=0.0)
        self.assertLess(c.probe_channel(1, 0), 1500.0)

    def test_water_lifts_the_water_channel(self):
        dry = self.a_gauged_tank(water=0.0).probe_channel(1, 0)
        wet = self.a_gauged_tank(water=2.0).probe_channel(1, 0)
        self.assertGreater(wet, dry)

    def test_the_two_temperature_references_agree(self):
        """C11 and C18 are both TMP REF, and every real probe in the manual
        reads them within three counts of each other."""
        c = self.a_gauged_tank()
        self.assertAlmostEqual(c.probe_channel(1, 11), c.probe_channel(1, 18),
                               delta=4.0)

    def test_the_later_height_channels_lag_the_earlier_ones(self):
        """"Channels 00 - 05 will update every sample. Channels 06 - 18
        update only following a system-read"."""
        c = self.a_gauged_tank()
        self.assertNotEqual(round(c.probe_channel(1, 5)),
                            round(c.probe_channel(1, 6)))

    def test_the_thermistors_read_between_the_documented_bounds(self):
        c = self.a_gauged_tank()
        for n in range(12, 18):
            value = c.probe_channel(1, n)
            self.assertTrue(15000.0 < value < 27000.0, (n, value))

    def test_a_probe_is_a_standard_length_that_clears_the_tank_and_its_riser(self):
        """FIDELITY R14. 576013-879 Rev W p.17 measures the minimum probe
        length "from the bottom of the tank to the top of the probe manway",
        and the step after it requires the canister to sit inside a riser of
        "minimum length of 10 inches [254mm]". This passed the DIAMETER, so
        a 96 inch tank got a 96 inch probe where the manual wants 106 at
        least -- and `readings`' own module docstring had said so from the
        start: "a probe's length is the tank's diameter plus its riser"."""
        self.assertEqual(self.a_gauged_tank(diameter=96.0).probe_length(1),
                         108.0)
        self.assertEqual(self.a_gauged_tank(diameter=64.0).probe_length(1),
                         84.0)
        self.assertEqual(self.a_gauged_tank(diameter=48.0).probe_length(1),
                         60.0)

    def test_a_tank_deeper_than_ten_foot_gets_the_catalogues_longest(self):
        """`PROBE_LENGTHS` ran out at 120 inches, which is Table 9-3's last
        row -- and that table is a channel COUNT table running 4 to 10 foot,
        not a catalogue. Every float kit table in 577014-449 Rev G heads its
        column "Min/Max Probe Length" over `48" (1.2m) - 144" (3.66m)`, and
        tank diameter accepts anything up to 1000, so a 126 inch tank was
        given a probe six inches shorter than the fuel it stands in."""
        self.assertEqual(self.a_gauged_tank(diameter=126.0).probe_length(1),
                         144.0)
        self.assertEqual(self.a_gauged_tank(diameter=240.0).probe_length(1),
                         144.0)

    def test_num_samples_is_twenty_on_a_mag_probe(self):
        """"Under normal operating conditions, this number should read 20"."""
        c = self.a_gauged_tank()
        self.assertEqual(c.diag_value("probe_samples", 1), "NUM SAMPLES: 20")

    def test_samples_read_is_a_running_count_and_not_the_window(self):
        """FIDELITY R15. "rrrrrrrr - Samples Read (Hex)" is eight hex
        characters and A15's own example is a probe that read 2 and used 2 --
        a console two seconds old. This returned `probe_window(tank,
        "standard")` for both, which is A12's NUMBER OF SAMPLES: twenty on a
        Mag, forty on a CAP, and a different quantity that happens to be a
        number."""
        c = self.a_gauged_tank()
        c.clock_offset += 600.0
        c.tick()
        read, used, number, _when = c.probe_sample_health(1)
        self.assertNotEqual(read, 20)
        self.assertGreaterEqual(read, 600)
        self.assertEqual(read, used)          # nothing has gone wrong yet
        self.assertEqual(number, 0)           # "Last Error Sample Number"

    def test_a_probe_that_has_been_out_fails_the_guides_own_triage(self):
        """577014-348 step 2: "check samples_read / sample_used to see probe
        performance previous to being out. a. If there is a difference (>1%)
        between the 2 numbers, continue to Step 3. b. If there is little or
        no difference (<1%) ... refer to the ATG troubleshooting guide."

        The difference was structurally zero -- including for a probe the
        bench had unplugged -- so the document's primary triage step always
        took branch b and taught the opposite of what it is for. It is the
        only console-observable step in that whole guide.
        """
        c = self.a_gauged_tank()
        c.clock_offset += 600.0
        c.tick()
        c.probe_out.add(1)
        for _ in range(6):
            c.clock_offset += 600.0
            c.tick()
        read, used, number, _when = c.probe_sample_health(1)
        self.assertGreater(read, used)
        self.assertGreater((read - used) / read, 0.01)
        self.assertGreater(number, 0)

        # "PREVIOUS to being out": the pair is a history, so plugging the
        # probe back in does not tidy it away
        c.probe_out.discard(1)
        c.clock_offset += 3600.0
        c.tick()
        read, used, _number, _when = c.probe_sample_health(1)
        self.assertGreater((read - used) / read, 0.01)

    def test_a_probe_that_has_never_been_out_takes_the_other_branch(self):
        c = self.a_gauged_tank()
        c.tank_level[2] = {"volume": 1000.0, "water": 0.0}
        c.probe_out.add(1)
        for _ in range(6):
            c.clock_offset += 600.0
            c.tick()
        read, used, number, _when = c.probe_sample_health(1)
        self.assertGreater(read - used, 0)
        # tank 2 is not programmed, so it reads nothing at all rather than
        # borrowing tank 1's trouble
        self.assertEqual(c.probe_sample_health(2)[:3], (0, 0, 0))


class ModuleIdResistors(unittest.TestCase):
    """Table 6-1, Console Modules - ID Resistances."""

    def test_a_slot_reads_its_own_module_s_resistance(self):
        c = Console()
        c.modules = {"probe": 1, "rs232": 1}
        por, now = c.module_id_resistance("probe")
        self.assertTrue(1900 < por < 2100, por)      # "4 Probe 2K"
        por, now = c.module_id_resistance("gw")
        self.assertTrue(265000 < por < 279000, por)  # "Groundwater Sensor 270K"

    def test_the_two_columns_agree_to_within_a_percent(self):
        """"The actual or measured resistance will differ slightly from the
        nominal value", and POR against C is that difference twice."""
        c = Console()
        for module in ("probe", "liquid", "plld", "rs232"):
            por, now = c.module_id_resistance(module)
            self.assertLess(abs(now - por) / por, 0.01, module)

    def test_an_empty_slot_reads_the_rail_in_every_bay(self):
        """FIDELITY X5, corrected by M22.

        15,000,000 is a firmware constant: a 2015 capture's PACKED reply
        sends `4B64E1C0` -- exactly 15,000,000.0 -- for both columns of all
        nineteen of its empty slots, beside type code `00`, "Not used". A
        measured open circuit does not land on a round decimal twice.

        This used to assert a DIFFERENT number for the intrinsically safe
        bay, near 10.2 million and drifting, on X5's argument that the I.S.
        bay reads its open circuit through its barrier. Both real consoles
        this project can check print the rail in all sixteen slots. The
        sample's seven I.S. rows are still unexplained and are UNKNOWNS A75.
        """
        c = Console()
        c.modules = {"probe": 1}
        rows = [(k, v) for k, v in c.slot_report() if "UNUSED" in k]
        safe = [v for k, v in rows
                if k.startswith("SLOT") and int(k.split()[1]) <= 8]
        power = [v for k, v in rows
                 if k.startswith("SLOT") and int(k.split()[1]) > 8]
        comms = [v for k, v in rows if k.startswith("COMM")]
        self.assertEqual((len(safe), len(power), len(comms)), (7, 8, 6))
        for line in safe + power + comms:
            self.assertEqual(line, "POR=15000000 C=15000000")

    def test_and_the_captured_console_s_own_empty_rows_replay(self):
        """The rows this had never been compared against. `I10200` is on the
        capture conformance FIXTURE list because the captured console has
        cards this one does not -- which is honest about the card rows and
        was quietly covering the empty ones too. These are checked directly
        instead."""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "tests", "console_capture", "raw",
                            "I10200.bin")
        if not os.path.exists(path):
            raise unittest.SkipTest("no console capture in this tree")
        with open(path, "rb") as fh:
            real = fh.read().decode("latin-1")
        wanted = [line.rstrip() for line in real.splitlines()
                  if "UNUSED" in line]
        # that console has a probe card, a PLLD pair and two comm boards,
        # so what is left empty is I.S. 3-8, power 10-16 and COMM 3-6
        self.assertEqual(len(wanted), 6 + 7 + 4)
        c = Console()
        c.modules = {"probe": 1}
        mine = {line for line in c.configuration_lines() if "UNUSED" in line}
        for line in wanted:
            self.assertIn(line.strip(), [m.strip() for m in mine], line)


class ADeliveryTheBenchCanActuallyMake(unittest.TestCase):
    """FIDELITY Y1 and Y2, which between them made every delivery this
    simulator could produce unbelievable.

    Y1: the start snapshot was built by overwriting `start["volume"]` and
    then dividing by it, so the scaling cancelled and the start TC came out
    as the TC of the volume AFTER the rise -- a TC increase of exactly zero
    on every delivery -- and the start HEIGHT was never corrected at all.

    Y2: the 25-gallon threshold was compared against a `running` local that
    was rebuilt on every tick, so it was 25 gallons PER TICK -- about 2,140
    gallons a minute at the panel's 700 ms poll -- and a real tanker
    dropping 200 to 400 gallons a minute was invisible.
    """

    def a_site(self):
        c = Console()
        c.modules["probe"] = 1
        c.values["S60A01"] = "01" + float_value(10000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        c.values["S61001"] = "0101"          # a minute of quiet ends it
        c.tank_level[1] = {"volume": 2000.0, "water": 0.0}
        c.deliveries.tick()
        return c

    def settle(self, c):
        c.clock_offset += 300.0
        c.deliveries.tick()
        return c.deliveries.last(1)

    def test_a_drop_in_one_tick_reports_a_tc_increase(self):
        """The bench's only way to fill a tank is dragging the product
        float, which is one tick -- so this was every delivery a user of
        this simulator could make."""
        c = self.a_site()
        c.tank_level[1]["volume"] = 5000.0
        c.clock_offset += 60.0
        c.deliveries.tick()
        record = self.settle(c)
        self.assertIsNotNone(record)
        self.assertEqual(record.amount, 3000.0)
        self.assertGreater(record.tc_amount, 0.0)

    def test_and_a_start_height_below_the_end_one(self):
        """576013-939 Rev F p.3's own report draws HEIGHT = 44 INCHES
        rising to HEIGHT = 84 INCHES; this drew the same height twice."""
        c = self.a_site()
        c.tank_level[1]["volume"] = 5000.0
        c.clock_offset += 60.0
        c.deliveries.tick()
        record = self.settle(c)
        self.assertLess(record.start["height"], record.end["height"])
        self.assertAlmostEqual(record.start["height"],
                               c.height_at(1, 2000.0), places=2)

    def test_a_realistic_fill_is_a_delivery(self):
        """4,980 gallons in 200 ticks of 24.9 used to be MISSED, because the
        threshold was measured against the previous tick."""
        c = self.a_site()
        volume = 2000.0
        for _ in range(120):
            volume += 24.9
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 5.0
            c.deliveries.tick()
        record = self.settle(c)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.amount, volume - 2000.0, places=1)
        self.assertAlmostEqual(record.start["volume"], 2000.0, places=1)

    def test_a_fill_too_gradual_is_not_a_delivery_however_much_it_adds_up(self):
        """"the rate of fill can be too gradual for the system to recognize
        the increase as a delivery" (576013-623 Rev AN p.7-17), and the
        rate is the TLS-450's stated one, "increases by 25 Gallons per
        Minute" (577013-940 Rev F p.69). Five gallons a minute for forty
        minutes is 200 gallons and no delivery. UNKNOWNS A24."""
        c = self.a_site()
        volume = 2000.0
        for _ in range(40):
            volume += 5.0
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 60.0
            c.deliveries.tick()
        self.assertIsNone(self.settle(c))

    def test_and_the_high_water_filter_watches_for_six_a_minute(self):
        """"in this case the Console detects that a Delivery is occurring
        when the fuel level in the Tank increases by six Gallons per Minute
        (GPM)", p.71. Ten a minute is a delivery on High and not on Low."""
        for level, expect in (("LOW", False), ("HIGH", True)):
            c = self.a_site()
            c.set_setting("water_filter", level, 1)
            volume = 2000.0
            for _ in range(10):
                volume += 10.0
                c.tank_level[1]["volume"] = volume
                c.clock_offset += 60.0
                c.deliveries.tick()
            self.assertEqual(self.settle(c) is not None, expect, level)

    def test_a_tanker_load_is_the_same_two_fixes(self):
        """`Loads._watch` carried both defects, copied line for line."""
        c = self.a_site()
        c.values["S51300"] = "1"             # tanker load reports enabled
        volume = 8000.0
        c.tank_level[1] = {"volume": volume, "water": 0.0}
        c.loads.tick()
        for _ in range(20):
            volume -= 100.0
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 5.0
            c.loads.tick()
        c.clock_offset += 300.0
        c.loads.tick()
        load = (c.loads.records.get(1) or [None])[0]
        self.assertIsNotNone(load)
        self.assertAlmostEqual(load.total, 2000.0, places=1)
        self.assertGreater(load.tc_total, 0.0)


class FuelManagementIsPerProduct(unittest.TestCase):
    """FIDELITY O4. 576013-610 Rev AC p.7-1, and 576013-623 Rev AN p.9-1
    word for word: "The system displays the days of fuel remaining for all
    tanks containing the product. The product name displayed is of the
    lowest tank number containing the product. Note: The system assumes
    tanks with the same product code contain the same product. **All
    information displayed is for products, not tanks.**"

    Every one of the function's five screens was wrong, starting with the
    head: the manual heads them with the product label ALONE, which is what
    FUEL MANAGEMENT DIAG already did.
    """

    def a_site(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c

    def test_tanks_with_one_product_code_are_one_product(self):
        """The truck stop runs diesel in tanks 1 and 2."""
        c = self.a_site()
        self.assertEqual([tanks for _code, tanks in c.fuel_products()],
                         [[1, 2], [3], [4]])
        self.assertEqual(c.fuel_label(1), "DIESEL")
        self.assertEqual(c.fuel_tanks(1), [1, 2])

    def test_the_name_is_the_lowest_tanks_label(self):
        c = self.a_site()
        c.values["S60202"] = "02SOMETHING ELSE    "
        self.assertEqual(c.fuel_label(1), "DIESEL")

    def test_days_fuel_remaining_fits_the_display_and_has_a_number(self):
        """"DAYS FUEL REMAINING: 2.4" is twenty-four characters exactly,
        with no unit word after the number. This console formatted a value
        into a 34-character line and the panel clipped it at 24, so the one
        screen the function exists for displayed nothing."""
        c = self.a_site()
        line = c.fuel_reading("fuel_days", 1)
        self.assertLessEqual(len(line), 24)
        self.assertTrue(line.startswith("DAYS FUEL REMAINING: "), line)
        self.assertGreater(float(line.rsplit(" ", 1)[1]), 0.0)

    def test_it_counts_down_to_the_alarm_and_not_to_empty(self):
        """"how many days of fuel you have remaining BEFORE YOU RECEIVE A
        LOW PRODUCT ALARM", and "the Low Product alarm activates when the
        amount of fuel falls below the Low Product Limit". This computed
        volume over sales, which is days to empty."""
        c = self.a_site()
        without = float(c.fuel_reading("fuel_days", 1).rsplit(" ", 1)[1])
        for tank in c.fuel_tanks(1):
            c.values[f"S621{tank:02d}"] = f"{tank:02d}" + float_value(5000.0)
        with_limit = float(c.fuel_reading("fuel_days", 1).rsplit(" ", 1)[1])
        self.assertLess(with_limit, without)

    def test_the_readings_are_the_products_and_not_one_tanks(self):
        c = self.a_site()
        both = sum(c.tank_level[t]["volume"] for t in (1, 2))
        self.assertIn(f"{both:.0f}", c.fuel_reading("fuel_inventory", 1))

    def test_seven_average_sales_screens_and_thursday_is_thr(self):
        """A week is seven screens, and this console drew one. Thursday is
        THR: the only literal Thursday on this shelf is p.7-3's report row
        `AVG SALES-THR`, and the screens said THU against a reader keyed
        THR, so the lookup missed and Thursday read Wednesday's number."""
        from tls350sim.console import DAY_NAMES
        fn = [f for f in NORMAL_MENU
              if f["function"] == "FUEL MANAGEMENT"][0]
        names = [st["text"] for st in fn["steps"]]
        self.assertEqual(names[4:], [f"AVG SALES-{d}" for d in DAY_NAMES])
        self.assertIn("AVG SALES-THR", names)
        self.assertNotIn("AVG SALES-THU", names)

    def test_and_thursday_no_longer_reads_wednesdays_number(self):
        c = self.a_site()
        wed = c.fuel_reading("fuel_avg_WED", 1)
        thr = c.fuel_reading("fuel_avg_THR", 1)
        self.assertNotEqual(wed.rsplit(" ", 2)[-2], thr.rsplit(" ", 2)[-2])

    def test_no_screen_of_it_is_wider_than_the_display(self):
        c = self.a_site()
        for token in ("fuel_days", "fuel_inventory", "fuel_ullage95",
                      "fuel_avg_SUN", "fuel_avg_SAT"):
            line = c.fuel_reading(token, 1)
            self.assertLessEqual(len(line), 24, line)

    def test_the_short_report_is_the_tank_blocks_and_nothing_else(self):
        """p.7-1's report is DAYS FUEL REMAINING, INVENTORY and 95% ULLAGE
        per tank, in that order and colon-separated. This printed
        INVENTORY first, with `=`, and called the last row DAYS REMAIN."""
        c = self.a_site()
        out = [str(x) for x in printer.fuel(c, c.fuel_tanks(1), long=False)]
        body = [x for x in out if x.strip()]
        self.assertIn("T 1:DIESEL", body)
        self.assertIn("T 2:DIESEL", body)
        first = body.index("T 1:DIESEL")
        self.assertTrue(body[first + 1].startswith("DAYS FUEL REMAINING:"))
        self.assertTrue(body[first + 2].startswith("INVENTORY   :"))
        self.assertTrue(body[first + 3].startswith("95% ULLAGE  :"))
        self.assertFalse([x for x in body if x.startswith("AVG SALES")])

    def test_the_long_report_adds_the_seven_sales_rows(self):
        """p.7-3's example: the same blocks, then `AVG SALES-SUN : 983 GAL`
        seven times. This printed one AVG SALES row inside each tank."""
        c = self.a_site()
        out = [str(x) for x in printer.fuel(c, c.fuel_tanks(1))]
        rows = [x for x in out if x.startswith("AVG SALES")]
        self.assertEqual(len(rows), 7)
        self.assertTrue(rows[4].startswith("AVG SALES-THR:"), rows[4])
        for row in out:
            self.assertLessEqual(len(str(row)), 24, row)


class TheDeliveryDensity(unittest.TestCase):
    """FIDELITY O13. 576013-610 Rev AC p.4-5 draws four screens and this
    console drew two of them wrong and could not reach the other two.

    The store is 61F's own: "t - Delivery Type (0=next, 1=last)", and the
    value is kept as ENTERED -- "FFFFFFFF - Entered Density, relative,
    actual or API" -- because that is what the console reports back.
    """

    def a_tank(self):
        c = Console()
        c.modules["probe"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.values["S56000"] = "1"
        c.values["S60701"] = "01" + float_value(96.0)
        c.values["S61001"] = "0101"
        return c

    def wire(self, c, command):
        return Handler(c, verbose=False).handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def test_a_tank_with_no_entry_reads_zero(self):
        """"A value of 0 indicates that the density for the product in this
        tank has not been entered"."""
        c = self.a_tank()
        self.assertEqual(c.delivery_density_value(1, "0"), 0.0)
        self.assertEqual(c.live_reading("next_density", 1), "0.0000")

    def test_the_two_types_are_two_stores(self):
        c = self.a_tank()
        c.set_delivery_density(1, "0", 5.9987)
        c.set_delivery_density(1, "1", 6.1234)
        self.assertEqual(c.live_reading("next_density", 1), "5.9987")
        self.assertEqual(c.live_reading("last_density", 1), "6.1234")

    def test_printing_the_delivery_report_moves_next_to_last(self):
        """"This value will default to 0 after the delivery report is
        printed", and LAST is "the density entered in the Next delivery
        display prior to the printing of the delivery report"."""
        c = self.a_tank()
        c.set_delivery_density(1, "0", 5.9987)
        c.deliveries.tick()
        for volume in (8000.0, 8000.0):
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 60.0
            c.deliveries.tick()
        c.clock_offset += 600.0
        c.deliveries.tick()
        self.assertIsNotNone(c.deliveries.last(1))
        self.assertEqual(c.delivery_density_value(1, "1"), 5.9987)
        self.assertEqual(c.delivery_density_value(1, "0"), 0.0)

    def test_the_last_screen_waits_for_a_delivery(self):
        """"NOTE: this display is not shown if a delivery has not
        occurred"."""
        c = self.a_tank()
        fn = [f for f in NORMAL_MENU
              if f["function"] == "IN-TANK INVENTORY"][0]
        names = [st["text"] for st in c.visible_steps(fn, 1)]
        self.assertIn("NEXT DELIVER DENSITY", names)
        self.assertNotIn("LAST DELIVERY DENSITY", names)
        c.deliveries.tick()
        for volume in (8000.0, 8000.0):
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 60.0
            c.deliveries.tick()
        c.clock_offset += 600.0
        c.deliveries.tick()
        names = [st["text"] for st in c.visible_steps(fn, 1)]
        self.assertIn("LAST DELIVERY DENSITY", names)

    def test_the_wire_sets_and_reads_both_types(self):
        """"<SOH>S61FTTtdd.ddddd" and "<SOH>I61FTTt": the delivery type is
        in the DATA on a set and in the ADDRESS on an inquiry."""
        c = self.a_tank()
        self.assertIn("S61F01", self.wire(c, "S61F01005.9987"))
        self.assertEqual(c.delivery_density_value(1, "0"), 5.9987)
        said = self.wire(c, "I61F010")
        self.assertIn("NEXT DELIVERY DENSITY", said)
        self.assertIn("REGULAR UNLEADED", said)
        self.assertIn("5.9987", said)
        self.assertIn("I61F010", said)       # the type is echoed
        self.assertIn("LAST DELIVERY DENSITY", self.wire(c, "I61F011"))

    def test_and_the_computer_format_carries_the_type_in_the_data(self):
        """"i61FTTYYMMDDHHmmTTtFFFFFFFF&&CCCC"."""
        c = self.a_tank()
        self.wire(c, "S61F01005.9987")
        said = self.wire(c, "i61F010")
        self.assertIn("010" + packed.hexfloat(5.9987), said)

    def test_a_delivery_type_that_is_not_0_or_1_is_refused(self):
        c = self.a_tank()
        self.assertIn("9999", self.wire(c, "S61F01205.9987"))
        self.assertIn("9999", self.wire(c, "I61F012"))


class TheCommBayCardsThatWereMissing(unittest.TestCase):
    """FIDELITY M5, M8, M9 and M10, all off 577013-528 Rev G's two tables --
    "Comm Modules That Can Be Installed In Slots 1, 2, or 3" and "... In Slot
    4" -- which give a part number, an end plate, a spare, an ID resistor and
    a description for every comm card there is.
    """

    def a_bay(self, **cards):
        c = Console()
        c.modules = {}
        for key, n in cards.items():
            c.set_module(key, n)
        return c

    def test_the_two_satellites_are_two_boards(self):
        """"329362-003 ... 332 K ... Satellite, Amoco Applications" against
        "329362-004 ... 475 K ... Satellite, Shell Applications". The ID
        resistance is how the console tells cards apart, so two resistances
        is two boards -- and this table said they were one. M10."""
        from tls350sim.console import MODULE_PART
        self.assertEqual(MODULE_PART["ssat"], "329362-004")
        self.assertEqual(MODULE_PART["asat"], "329362-003")
        c = self.a_bay(ssat=1, asat=1)
        shell, _now = c.module_id_resistance("ssat")
        amoco, _now = c.module_id_resistance("asat")
        self.assertTrue(468000 < shell < 490000, shell)
        self.assertTrue(327000 < amoco < 342000, amoco)

    def test_and_the_console_calls_both_of_them_the_same_thing(self):
        """Table 6-1 has one name for the pair, "Serial Satellite Comm", so
        they read the same on a slot line and differ by their resistance.

        The SLOT LINE is the glass, which is where Table 6-1's names belong
        and what this has always been about; it used to ask `slot_readings`,
        which is the printout and is now a different vocabulary. M20."""
        c = self.a_bay(ssat=1, asat=1)
        names = [line.split(" ", 2)[2] for line, _l2 in c.slot_report()
                 if "S-SAT" in line]
        self.assertEqual(names, ["S-SAT COMM", "S-SAT COMM"])
        # `S-SAT ` with its own trailing space: the manual prints
        # `COMM BOARD  : 1 (S-SAT )` and so does a real console. It is a
        # character of the name, not a six-wide field being padded -- the
        # same manual prints `(FXMOD)` at five. See FIDELITY S18.
        self.assertEqual(c.comm_board_name(1), "S-SAT ")
        self.assertEqual(c.comm_board_name(2), "S-SAT ")

    def test_but_the_printout_tells_apart_the_one_it_has_a_name_for(self):
        """And that asymmetry is the EVIDENCE's, not the hardware's. A real
        console printed `SERIAL SAT BD` beside 482940 ohms, which is the
        Shell board's 475K and not the Amoco board's 332K -- so that name is
        attested for one of the pair and for the other nothing is. The
        unattested one keeps the screen's name rather than borrowing a
        printed one it was never seen to use. M20."""
        c = self.a_bay(ssat=1, asat=1)
        paper = {key: name for _slot, key, name, _p, _n in c.slot_readings()
                 if key in ("ssat", "asat")}
        self.assertEqual(paper["ssat"], "SERIAL SAT BD")
        self.assertEqual(paper["asat"], "S-SAT COMM")

    def test_sitelink_has_a_card_to_be_driven_by(self):
        """"330546-001 ... 267 K ... SiteLink, (Not intended for general
        purpose applications)", and Table 6-1's "SiteLink Comm 270K". The
        console knew SiteLink on three diagnostic screens, over the wire and
        in function 885, and had no card for it. M8."""
        from tls350sim.console import MODULE_PART
        self.assertEqual(MODULE_PART["slink"], "330546-001")
        c = self.a_bay(slink=1)
        self.assertEqual(c.comm_board_name(1), "S-LINK")
        por, _now = c.module_id_resistance("slink")
        self.assertTrue(262000 < por < 276000, por)

    def test_the_rs485_multiport_is_331944_001(self):
        """It is Table 6-1's `ISD Comm 82.5K`, which is why six weeks of
        searching for RS-485 missed it, and it is what the tape's console
        has in comm 5. M5."""
        from tls350sim.console import MODULE_PART
        self.assertEqual(MODULE_PART["rs485"], "331944-001")
        c = self.a_bay(rs485=1)
        c.board = "E6"
        # a dual-port module, so it sits in slot 4 and answers on 5 and 6 --
        # which is the tape's own `COMM BOARD  : 5 (RS-485)`. M7.
        self.assertEqual(c.comm_board_name(5), "RS-485")
        self.assertEqual(c.comm_board_name(6), "RS-232")
        self.assertEqual(c.comm_board_name(1), "UNUSED")
        por, _now = c.module_id_resistance("rs485")
        self.assertTrue(81000 < por < 86000, por)

    def test_and_it_wants_an_ecpu2_with_an_nvmem203_and_v24(self):
        """"Multiport modules require that the console be equipped with an
        ECPU2 board, a NVMEM203 memory module and software version 24 or
        higher" -- a gate Tables 3-1 to 3-5 have no row for at all."""
        c = self.a_bay(rs485=1)
        c.board, c.version = "E6", 24
        self.assertTrue(c.knows_module("rs485"))
        c.version = 23
        self.assertFalse(c.knows_module("rs485"))
        c.version, c.board = 33, "E7"          # ECPU2, but an NVMEM201
        self.assertFalse(c.knows_module("rs485"))
        c.board = "M6"                         # MSP ECPU2 with an NVMEM203
        self.assertTrue(c.knows_module("rs485"))

    def test_the_three_cards_that_read_an_invented_resistance(self):
        """M9. `module_id_resistance` ends in a fallback of 100000 and three
        FITTED cards reached it, each with a Table 6-1 row of its own -- so
        the screen that exists to show a card's ID drifting from its
        power-on value was drifting around an invented centre."""
        c = self.a_bay(rdu=1, edim=1, mdim=1)
        for key, low, high in (("rdu", 26000, 29000),      # 27.4K
                               ("mdim", 66000, 71000),     # 68K
                               ("edim", 98000, 104000)):   # 100K
            por, now = c.module_id_resistance(key)
            self.assertTrue(low < por < high, (key, por))
            self.assertLess(abs(now - por) / por, 0.01, key)


class ProbeOut(unittest.TestCase):
    """576013-635: In-Tank alarm type 09, "Tank Probe Out Alarm".

    Table 29-3 in the operator's manual calls it a hardware failure of the
    probe or its interconnection. On the bench it is made the way it is
    made in the field: the probe is unplugged at the riser.
    """

    def a_site(self):
        import struct
        c = fitted()
        c.values["S60101"] = "011"
        c.tank_level[1] = {"volume": 6000.0, "water": 20.0}
        c.values["S62401"] = "01" + struct.pack(">f", 10.0).hex().upper()
        return c

    def test_unplugging_the_probe_posts_0209(self):
        c = self.a_site()
        c.probe_out.add(1)
        self.assertIn("020901", c.conditions())

    def test_a_console_with_no_probe_cannot_see_the_water_either(self):
        """No readings means no level conditions: the High Water that was
        true a moment ago cannot be asserted by a console that cannot see
        the water any more."""
        c = self.a_site()
        self.assertIn("020301", c.conditions())         # high water, probe in
        c.probe_out.add(1)
        self.assertNotIn("020301", c.conditions())
        c.probe_out.discard(1)
        self.assertIn("020301", c.conditions())         # and it comes back

    def test_the_screen_reads_probe_out_alarm(self):
        from tls350sim.console import describe_alarms
        c = self.a_site()
        c.probe_out.add(1)
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("T 1:PROBE OUT", screens)

    def test_reset_plugs_every_probe_back_in(self):
        c = self.a_site()
        c.probe_out.add(1)
        c.reset(keep_clock=True)
        self.assertEqual(c.probe_out, set())


class ALossIsNegativeOnThePaper(unittest.TestCase):
    """FIDELITY K2. 576013-818 says it twice, once for the rate table and
    once for the compensated rate: "Leak rate in gph (negative number = a
    loss, no sign = a gain)", and every failing tank in chapter 11 prints a
    negative -- `LRATE -0.308`, `-0.270`, `-0.309`.

    `measured_rate` returned `max(0.0, ...)`, so a loss was positive and **a
    gain could not be represented at all** -- which matters because
    `CSLD RATE INCR WARN` exists for exactly one condition, "indicates fluid
    is entering the tank during the leak test", and the console fired it
    when a small drift appeared in a LOSS instead.
    """

    def a_csld_tank(self, leak):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        # "The CSLD option appears only when the tank is equipped with a
        # 0.1 gph (0.38 lph) Mag probe", 576013-623 p.8-2. A tank with a
        # float size programmed has a float, and only a Mag has one.
        c.values["S62F01"] = "011"
        c.tank_leak[1] = leak
        return c

    def hours(self, c, n):
        for _ in range(n):
            c.clock_offset += 3600.0
            c.tick()
        return c

    def test_a_gain_survives_the_reading(self):
        c = self.a_csld_tank(-0.3)
        self.assertEqual(c.leaks.measured_rate("tank", 1), -0.3)

    def test_the_rate_table_prints_a_loss_as_a_negative(self):
        """Chapter 11's own format."""
        # a day for the volume table, then long enough for a test to run
        c = self.hours(self.a_csld_tank(0.308), 28)
        rows = [r for r in c.csld_table_lines("A51", 1) if r[:2] == "26"]
        self.assertTrue(rows, "no CSLD rows")
        self.assertIn("-0.308", rows[0])

    def test_and_a_gain_with_no_sign(self):
        """"no sign = a gain"."""
        c = self.hours(self.a_csld_tank(-0.15), 28)
        rows = [r for r in c.csld_table_lines("A51", 1) if r[:2] == "26"]
        self.assertTrue(rows)
        self.assertIn(" 0.150", rows[0])
        self.assertNotIn("-0.150", rows[0])

    def test_a_gain_raises_the_rate_increase_warning(self):
        """p.11-9: "indicates fluid is entering the tank during the leak
        test ... a higher than acceptable positive increase in product".
        Chapter 11's Problem 3 is a siphon leaking product back."""
        c = self.hours(self.a_csld_tank(-0.30), 120)
        self.assertEqual(c.csld.result_of(1), csld.INCR)
        self.assertIn("022301", c.conditions())

    def test_a_loss_fails_and_does_not_raise_it(self):
        c = self.hours(self.a_csld_tank(0.30), 120)
        self.assertEqual(c.csld.result_of(1), csld.FAIL)
        self.assertNotIn("022301", c.conditions())

    def test_a_tight_tank_does_neither(self):
        c = self.hours(self.a_csld_tank(0.0), 120)
        self.assertEqual(c.csld.result_of(1), csld.PASS)
        self.assertNotIn("022301", c.conditions())

    def test_the_packed_record_carries_the_same_sign(self):
        from tls350sim import packed
        c = self.hours(self.a_csld_tank(0.308), 28)
        body = c.csld_table_records("A51", 1)
        self.assertIn(packed.hexfloat(-0.308).upper(), body.upper())


class TheFourCsldTablesAreFourReports(unittest.TestCase):
    """FIDELITY K1. 576013-818 chapter 11 diagnoses CSLD by reading four
    serial tables, and this console produced four reports with those names
    and none of those shapes: IA51 printed six of its thirteen columns, IA52
    fell through to IA51's branch entirely, IA53 printed rate samples where
    the manual prints an hourly volume grid, IA54 averaged leak RATES where
    the manual averages temperature compensated VOLUME, and all four packed
    one record shape where the manual gives four.

    Chapter 11's own glossary is what these are built from, because it
    defines every column where the serial manual only names it.
    """

    def a_csld_tank(self, leak=0.016, hours=36, pump_sense=False):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        c.modules["rs232"] = 1
        c.tank_leak[3] = leak
        if pump_sense:
            c.values["S77201"] = "0103"
        for _ in range(hours * 12):
            c.clock_offset += 300.0
            c.tick()
        return c

    # ---- IA51, the rate table ------------------------------------------
    def test_the_rate_table_has_all_thirteen_columns(self):
        c = self.a_csld_tank()
        head = c.csld_table_lines("A51", 3)[2]
        self.assertEqual(head.split(),
                         ["TIME", "ST", "LRT", "AVTMP", "TPTMP", "BDTMP",
                          "TMRT", "DSPNS", "VOL", "INTVL", "DEL", "ULLG",
                          "EVAP"])

    def test_and_thirteen_values_under_them(self):
        c = self.a_csld_tank()
        row = c.csld_table_lines("A51", 3)[3]
        self.assertEqual(len(row.split()), 13, row)

    def test_the_acceptability_codes_are_all_six(self):
        """Chapter 11: 0 valid, 1 duration too short, 2 start time too close
        to a delivery, 3 excessive dispensing prior to test, 4 excessive
        temperature change, 6 leak rate outlier. The console had three."""
        self.assertEqual(
            sorted({csld.ACCEPTABLE, csld.REJECT_DURATION,
                    csld.REJECT_DELIVERY, csld.REJECT_DISPENSING,
                    csld.REJECT_TEMPERATURE, csld.REJECT_DEVIATION}),
            ["00", "01", "02", "03", "04", "06"])

    def test_ullg_is_the_area_not_covered_by_fluid(self):
        """"ULLG -- Amount of surface area of the tank that is not covered
        by fluid", which is not the ullage VOLUME every other report means.
        It GROWS as the tank empties, which is the direction both manuals'
        samples run."""
        c = self.a_csld_tank()
        c.tank_level[3]["volume"] = 2000.0
        empty = c.uncovered_area(3)
        c.tank_level[3]["volume"] = 10000.0
        full = c.uncovered_area(3)
        self.assertGreater(empty, full)

    def test_evap_is_zero_without_a_reid_vapor_pressure_table(self):
        """"EVAP -- If the Reid Vapor Pressure table has been entered, the
        evaporation rate will be here." This console has no such table, and
        chapter 11's own four sample tanks all print 0.000."""
        c = self.a_csld_tank()
        row = c.csld_table_lines("A51", 3)[3]
        self.assertEqual(row.split()[-1], "0.000")

    # ---- IA52, the rate test -------------------------------------------
    def test_the_rate_test_is_its_own_report(self):
        c = self.a_csld_tank()
        head = c.csld_table_lines("A52", 3)[2]
        self.assertEqual(head.split(),
                         ["TK", "DATE", "LRATE", "INTVL", "ST", "AVLRTE",
                          "VOL", "C1", "C3", "FDBK", "ACPT", "THPUT",
                          "EVAP", "RJT"])

    def test_it_is_not_the_rate_tables_columns(self):
        c = self.a_csld_tank()
        self.assertNotEqual(c.csld_table_lines("A52", 3)[2],
                            c.csld_table_lines("A51", 3)[2])

    def test_c1_is_every_test_and_c3_the_acceptable_ones(self):
        """"C1 Total number of tests in the rate table", "C3 Number of
        acceptable tests" -- the pair chapter 11's Problems 8 and 9 are read
        from."""
        c = self.a_csld_tank()
        e = c.csld.evaluation(3)
        self.assertEqual(e["records"], len(c.csld.table(3)))
        self.assertEqual(e["accepted"],
                         sum(1 for r in c.csld.table(3)
                             if r["state"] == csld.ACCEPTABLE))
        self.assertLessEqual(e["accepted"], e["records"])

    def test_lrate_is_compensated_and_avlrte_is_not(self):
        """The one reading that is the opposite way round from its name.
        Chapter 11: "LRATE Compensated leak rate in gph", "AVLRTE
        Uncompensated Leak Rate, in gph"."""
        c = self.a_csld_tank()
        e = c.csld.evaluation(3)
        row = c.csld_table_lines("A52", 3)[3].split()
        self.assertAlmostEqual(float(row[2]), -e["compensated"], places=3)
        self.assertAlmostEqual(float(row[5]), -e["uncompensated"], places=3)

    def test_the_control_variables_stay_inside_their_range(self):
        """"FDBK Feedback control variable, range 0 to 45 minutes" and the
        same for ACPT."""
        c = self.a_csld_tank()
        e = c.csld.evaluation(3)
        for key in ("feedback", "acceptance"):
            self.assertGreaterEqual(e[key], 0.0)
            self.assertLessEqual(e[key], csld.CONTROL_MAX_MINUTES)

    def test_feedback_is_a_ramp_on_how_full_the_rate_table_is(self):
        """The manual gives FDBK a range and no formula, and its own sample
        rows give the formula: 45 x (C1 - 40) / 40, flat at zero until the
        80-row table is half full and reaching 45 exactly as it fills.

        These pairs are read off the IA52 figures in 576013-818 Rev AA and
        Rev AB, 577013-918 Rev D and both TLS-450 serial manuals -- 100 rows
        with no exception. C1=44 giving exactly 4.5 and C1=56 exactly 18.0
        leave no rounding slack for a different ramp. UNKNOWNS A51.
        """
        c = self.a_csld_tank()
        for c1, want in ((0, 0.0), (11, 0.0), (22, 0.0), (26, 0.0),
                         (44, 4.5), (49, 10.1), (53, 14.6), (56, 18.0),
                         (58, 20.3), (70, 33.8), (74, 38.3), (75, 39.4),
                         (80, 45.0)):
            c.csld.detail[3] = [{"at": 0.0, "rate": 0.01,
                                 "interval": 30.0,
                                 "state": csld.ACCEPTABLE}
                                for _ in range(c1)]
            self.assertAlmostEqual(c.csld.feedback(3), want, places=6,
                                   msg=f"C1={c1}")

    def test_feedback_breaks_a_tie_upwards_the_way_the_column_does(self):
        """Its steps are eighths of a minute, so half of them land on a tie,
        and the samples break every one upwards -- C1=74 is 38.25 and prints
        38.3. Python rounds a tie to even and would print 38.2."""
        c = self.a_csld_tank()
        for c1, want in ((74, 38.3), (58, 20.3), (70, 33.8)):
            c.csld.detail[3] = [{"at": 0.0, "rate": 0.01,
                                 "interval": 30.0,
                                 "state": csld.ACCEPTABLE}
                                for _ in range(c1)]
            self.assertAlmostEqual(c.csld.feedback(3), want, places=6,
                                   msg=f"C1={c1}")

    def test_a_tank_nobody_has_tested_reads_no_test(self):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        self.assertEqual(c.csld.evaluation(3)["status"], csld.A52_NO_TEST)

    # ---- IA53, the volume history table ---------------------------------
    def test_the_volume_table_is_hourly_volumes(self):
        """"This report contains volume samples collected once every hour",
        and the display is a grid of them."""
        c = self.a_csld_tank()
        rows = c.csld_table_lines("A53", 3)
        cells = [v for r in rows[3:] for v in r.split()]
        self.assertEqual(len(cells), csld.VOLUME_HOURS)
        for cell in cells:
            float(cell)

    def test_it_starts_with_the_most_recent(self):
        """"This list starts with the most recent volume and moves to the
        oldest volume from left to right and top to bottom"."""
        c = self.a_csld_tank()
        hourly = c.csld.hourly[3]
        first = c.csld_table_lines("A53", 3)[3].split()[0]
        self.assertAlmostEqual(float(first), hourly[-1][1], places=1)

    def test_last_hour_is_hours_and_sits_above_the_grid(self):
        """`LAST HOUR = 229664` heads each of chapter 11's four tanks, and
        229664 HOURS is March 1996 where 229664 seconds is still 1970."""
        c = self.a_csld_tank()
        rows = c.csld_table_lines("A53", 3)
        self.assertTrue(rows[2].startswith("LAST HOUR = "), rows[2])
        hours = int(rows[2].split("=")[1])
        self.assertAlmostEqual(hours, c.csld.hourly[3][-1][0] // 3600,
                               delta=1)

    # ---- IA54, the moving average table ---------------------------------
    def test_the_probe_table_is_thirty_seconds_apart(self):
        """"averaged probe data collected every 30 seconds"."""
        c = self.a_csld_tank()
        rows = c.csld.probe[3]
        self.assertGreater(len(rows), 2)
        for a, b in zip(rows, rows[1:]):
            self.assertAlmostEqual(b["at"] - a["at"], csld.PROBE_SECONDS,
                                   places=3)

    def test_it_has_the_manuals_seven_columns(self):
        c = self.a_csld_tank()
        head = c.csld_table_lines("A54", 3)[2]
        self.assertEqual(head.split(),
                         ["TIME", "SMPLS", "TCVOL", "HEIGHT", "AVGTEMP",
                          "TOPTEMP", "BDTEMP"])

    def test_the_moving_average_is_a_volume_not_a_rate(self):
        """It averaged leak RATES, which is a different quantity in
        different units. Chapter 11's sample has MOVING AVERAGE: 2091.64
        against TCVOL rows of 2091.6x."""
        c = self.a_csld_tank()
        average = c.csld.moving_volume(3)
        newest = c.csld.probe[3][-1]["tcvol"]
        self.assertAlmostEqual(average, newest, delta=abs(newest) * 0.05)

    def test_both_footers_go_under_the_rows(self):
        c = self.a_csld_tank()
        rows = c.csld_table_lines("A54", 3)
        self.assertTrue(rows[-2].startswith("MOVING AVERAGE:"), rows[-2])
        self.assertTrue(rows[-1].startswith("DISPENSE STATE:"), rows[-1])

    def test_the_star_means_pump_sense(self):
        """"* following ACTIVE = Pump sense available", chapter 11's key."""
        plain = self.a_csld_tank().csld_table_lines("A54", 3)[-1]
        self.assertFalse(plain.endswith("*"))
        c = self.a_csld_tank(pump_sense=True)
        meter = next(m for m, t in c.meters.items() if int(t) == 3)
        c.meter_flow[meter] = 6.0
        starred = c.csld_table_lines("A54", 3)[-1]
        self.assertEqual(starred, "DISPENSE STATE: ACTIVE *")

    def test_the_star_follows_active_and_only_active(self):
        """The key says so in three words, and every sample on the shelf
        agrees: `ACTIVE *`, `ACTIVE` and `IDLE` are the three shapes, across
        576013-818 Rev AA and Rev AB, 577013-918 Rev D and both revisions of
        the serial manual. `IDLE *` is in none of them, and this appended
        the mark to whichever word came out. See FIDELITY K5."""
        c = self.a_csld_tank(pump_sense=True)
        self.assertEqual(c.csld.moving_state(3), "IDLE")
        self.assertTrue(c.pump_tank_has_sense(3))
        self.assertEqual(c.csld_table_lines("A54", 3)[-1],
                         "DISPENSE STATE: IDLE")

    def test_the_pump_sense_module_decides_the_state_it_marks(self):
        """"Idle is determined by: 1) analysis of a group of probe samples
        from the 30 second average table, and 2) checking the pump sense
        module (if available)", Figure 11-2. The footer was clause 1 alone,
        under a mark whose own description says "CSLD is using a Pump Sense
        signal to determine" it. FIDELITY K5."""
        c = self.a_csld_tank(pump_sense=True)
        self.assertFalse(c.pump_running(3))
        self.assertEqual(c.csld.moving_state(3), "IDLE")
        meter = next(m for m, t in c.meters.items() if int(t) == 3)
        c.meter_flow[meter] = 6.0
        self.assertTrue(c.pump_running(3))
        self.assertEqual(c.csld.moving_state(3), "ACTIVE")

    # ---- and the four packed records ------------------------------------
    def test_the_four_records_are_four_shapes(self):
        """"All four share one packed record shape" was the defect."""
        c = self.a_csld_tank()
        got = {tok: c.csld_table_records(tok, 3)
               for tok in ("A51", "A52", "A53", "A54")}
        self.assertEqual(len(set(got.values())), 4)

    def test_the_rate_record_carries_fourteen_floats(self):
        c = self.a_csld_tank()
        body = c.csld_table_records("A51", 3)
        rows = len(c.csld.table(3))
        self.assertEqual(body[:4], f"03{rows:02d}")
        # ss NN tttttttt then fourteen eight-character floats
        self.assertEqual(len(body), 4 + rows * (2 + 2 + 8 + 14 * 8))

    def test_the_test_record_carries_ten(self):
        c = self.a_csld_tank()
        body = c.csld_table_records("A52", 3)
        self.assertEqual(len(body), 2 + 10 + 2 + 2 + 2 + 2 + 10 * 8)

    def test_the_volume_record_is_the_hourly_grid(self):
        c = self.a_csld_tank()
        body = c.csld_table_records("A53", 3)
        cells = len(c.csld.hourly[3])
        self.assertEqual(len(body), 2 + 2 + 8 + cells * 8)

    def test_the_average_record_has_the_extra_state_byte(self):
        """A54 is the one with a leading state byte and eight floats."""
        c = self.a_csld_tank()
        body = c.csld_table_records("A54", 3)
        rows = len(c.csld.probe[3])
        self.assertEqual(len(body), 2 + 2 + 2 + rows * (2 + 2 + 8 + 8 * 8))


class TheTwoCountingScreensOfFigure611(unittest.TestCase):
    """FIDELITY D6. Two of CSLD DIAGNOSTICS' screens count something, and
    both counted the wrong thing while the report behind them counted right.

    Figure 6-11 draws TOTAL TESTS as a PAIR and says what the two numbers
    are -- "YY = Total number of tests stored in CSLD Rate Table. XX = Total
    number of tests used to compute leak rate from CSLD Rate Table" -- and
    the console drew the stored count alone. POS REJECTS is "out of the last
    20 tests, this number indicates how many exceeded a positve leak rate of
    at least 0.4 gph", and the console counted every row in the whole table
    that had been rejected for any reason at all.

    Both numbers are IA52's, and IA52 has had them right since K1.
    """

    def a_csld_tank(self, rows=()):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        c.modules["rs232"] = 1
        for _ in range(24 * 12):
            c.clock_offset += 300.0
            c.tick()
        if rows:
            # the way `_sample` does it, because the two sets are not the
            # same one: every test COMPLETES, and only a test whose gain is
            # under the gate is RECORDED in the rate table. Putting an
            # over-gate row in the table models a console that cannot
            # exist. See UNKNOWNS A63.
            c.csld.completed[3] = [r["rate"] for r in rows][-csld.RJT_WINDOW:]
            c.csld.detail[3] = [r for r in rows
                                if r["rate"] >= -csld.POSITIVE_LEAK]
        return c

    def a_row(self, rate, state=csld.ACCEPTABLE):
        return {"at": 0.0, "rate": rate, "volume": 5000.0, "ullage": 0.0,
                "area": 0.0, "temp": 55.0, "toptemp": 55.0, "bdtemp": 55.0,
                "tmrt": 0.0, "dispns": 0.0, "interval": 30.0,
                "delivered": 0.0, "throughput": 0.0, "evap": 0.0,
                "state": state}

    def test_total_tests_is_the_used_count_over_the_stored_one(self):
        c = self.a_csld_tank()
        got = c.csld.evaluation(3)
        self.assertEqual(c.diag_value("csld_tests", 3).split()[-1],
                         f"{got['accepted']}/{got['records']}")
        self.assertLessEqual(got["accepted"], got["records"])

    def test_pos_rejects_counts_the_rate_and_not_the_rejection(self):
        """A test can be rejected for its duration, for a delivery, for a
        temperature swing -- none of which is a positive leak rate."""
        rows = [self.a_row(0.01, csld.REJECT_DURATION) for _ in range(10)]
        rows += [self.a_row(-0.6) for _ in range(3)]
        c = self.a_csld_tank(rows)
        self.assertEqual(c.diag_value("csld_rejects", 3).split()[-1], "3")

    def test_and_only_over_the_last_twenty(self):
        """"Out of the last 20 tests": the window is the point of it."""
        rows = [self.a_row(-0.6) for _ in range(5)]
        rows += [self.a_row(0.01) for _ in range(20)]
        c = self.a_csld_tank(rows)
        self.assertEqual(c.diag_value("csld_rejects", 3).split()[-1], "0")

    def test_a_gain_under_four_tenths_is_not_one(self):
        c = self.a_csld_tank([self.a_row(-0.39) for _ in range(5)])
        self.assertEqual(c.diag_value("csld_rejects", 3).split()[-1], "0")

    def test_an_over_gate_test_completes_and_is_never_recorded(self):
        """Figure 11-2: "Record test results in database if: ... 2) leak
        rate < +0.4 gph". So the two counts come from different sets, and
        RJT can be nonzero while the rate table it prints beside is empty.

        The evidence that a real console does this rather than recording
        and filtering afterwards: no rate table on the shelf has a row over
        the gate -- 100 sample rows across five manuals, the largest +0.395
        -- while the same figures print RJT counts of 5, 9 and 12. A
        recorded-then-excluded row would show in the column. UNKNOWNS A63.
        """
        c = self.a_csld_tank([self.a_row(-0.6) for _ in range(4)])
        self.assertEqual(c.csld.table(3), [])
        self.assertEqual(c.csld.evaluation(3)["records"], 0)
        self.assertEqual(c.csld.evaluation(3)["rejects"], 4)

    def test_no_rate_table_row_may_ever_be_over_the_gate(self):
        """The invariant the manuals' own 100 sample rows keep."""
        c = self.a_csld_tank([self.a_row(-0.6), self.a_row(0.02),
                              self.a_row(-0.41), self.a_row(-0.39)])
        self.assertTrue(all(r["rate"] >= -csld.POSITIVE_LEAK
                            for r in c.csld.table(3)), c.csld.table(3))
        self.assertEqual(len(c.csld.table(3)), 2)

    def test_both_lines_fill_the_display_and_end_at_its_edge(self):
        """D5's rule for the same manual's screens: the value goes against
        the right of the 24-column display."""
        c = self.a_csld_tank([self.a_row(-0.6) for _ in range(3)])
        for token in ("csld_tests", "csld_rejects"):
            line = c.diag_value(token, 3)
            self.assertEqual(len(line), 24, line)
            self.assertNotEqual(line[-1], " ", line)


class TheMonthlyReportHasADatabase(unittest.TestCase):
    """FIDELITY K3. `IA56` called `_csld_states`, which read
    `probe_leak_buffer(tank, "periodic")` -- the SCHEDULED leak-test buffer.
    A CSLD tank runs no scheduled tests, so that list was always empty, the
    fallback fired, and **no PASS or FAIL could ever appear in the monthly
    report** -- against the manual's own sample showing a month of
    `RESULT: PASS`, `RESULT: FAIL`, `RESULT: INCR` and `STATUS:` changes.
    """

    def a_site(self, leak=0.35, days=4):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        c.modules["rs232"] = 1
        c.tank_leak[3] = leak
        for _ in range(days * 96):
            c.clock_offset += 900.0
            c.tick()
        return c, Handler(c, verbose=False)

    def lines(self, h, command):
        raw = h.handle((SOH + command + CR).encode()).decode("latin-1")
        return raw.strip(SOH + ETX + SEP).replace(CR + chr(10), chr(10)).split(chr(10))

    def test_a_failing_tank_reports_its_failures(self):
        c, h = self.a_site()
        self.assertTrue(c.csld.history.get(3), "nothing was ever decided")
        got = [l for l in self.lines(h, "IA5603") if "RESULT:" in l]
        self.assertTrue(got, "the monthly report carries no result")
        for line in got:
            self.assertIn("RESULT: FAIL", line)

    def test_a_tight_tank_reports_passes(self):
        _c, h = self.a_site(leak=0.0)
        got = [l for l in self.lines(h, "IA5603") if "RESULT:" in l]
        self.assertTrue(got)
        for line in got:
            self.assertIn("RESULT: PASS", line)

    def test_the_history_is_the_csld_database_not_the_leak_buffer(self):
        """The buffer it used to read stays empty on a CSLD tank, which is
        why the report was silent."""
        c, _h = self.a_site()
        self.assertEqual(c.probe_leak_buffer(3, "periodic"), [])
        self.assertTrue(c.csld.history.get(3))

    def test_a_tank_with_no_verdict_yet_reports_a_status(self):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        c.modules["rs232"] = 1
        h = Handler(c, verbose=False)
        got = [l for l in self.lines(h, "IA5603") if "STATUS:" in l]
        self.assertEqual(len(got), 1, got)

    def test_the_previous_month_is_a_different_window(self):
        """The report has a CURRENT MONTH and a PREVIOUS MONTH branch, and
        both used to answer the same thing."""
        _c, h = self.a_site()
        self.assertNotEqual(self.lines(h, "IA5603"),
                            self.lines(h, "IA5613"))


class OverfillWantsTheDelivery(unittest.TestCase):
    """FIDELITY N7. 576013-623 Rev AN, Overfill Limit: "Overfill Limit warns
    of a potential overfill ONLY DURING A BULK DELIVERY. When the volume
    reaches this limit, the system can activate an on-site overfill alarm."

    `conditions()` tested the level against the limit and nothing else, so a
    tank that was simply full raised an overfill alarm and went on raising
    it, where a real console raises it while fuel is going in and then
    stops. `deliveries.in_progress(tank)` already existed and was consulted
    in six other places.
    """

    def a_site(self, share):
        """A 20,000 gallon tank, at `share` of its capacity, with the limits
        in Figure 7-1's own order: overfill at 90%, high product at 95%."""
        c = fitted()
        a_tank(c, 1, volume=20000.0 * share, full=20000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        c.values["S62801"] = "01" + float_value(20000.0)
        c.values["S62301"] = "01" + float_value(90.0)     # per cent
        c.values["S62201"] = "01" + float_value(95.0)     # per cent
        c.values["S61001"] = "0101"
        return c

    def filling(self, c, to=18500.0):
        c.deliveries.tick()
        c.tank_level[1]["volume"] = to
        c.clock_offset += 60.0
        c.deliveries.tick()
        return c

    def tank_alarms(self, c):
        return [a for a in c.conditions() if a.startswith("02")]

    def test_a_full_tank_standing_still_is_not_an_overfill(self):
        self.assertNotIn("020401", self.tank_alarms(self.a_site(0.92)))

    def test_the_same_tank_taking_a_delivery_is(self):
        c = self.filling(self.a_site(0.92))
        self.assertIsNotNone(c.deliveries.in_progress(1))
        self.assertIn("020401", self.tank_alarms(c))

    def test_high_product_carries_no_such_condition(self):
        """"Max or Label Volume warns when the level of fluid in the tank
        exceeds the volume you enter here", and High Product has no delivery
        clause at all. Only Overfill does."""
        self.assertIn("020701", self.tank_alarms(self.a_site(0.97)))

    def test_and_neither_does_max_or_label_volume(self):
        self.assertIn("021201", self.tank_alarms(self.a_site(1.0)))

    def test_the_band_below_overfill_is_quiet_either_way(self):
        for c in (self.a_site(0.5), self.filling(self.a_site(0.5), to=9000.0)):
            self.assertEqual([a for a in self.tank_alarms(c)
                              if a[:4] in ("0204", "0207", "0212")], [])


class ATankerLoadIsOneBulkDraw(unittest.TestCase):
    """A pending load opened on any fall and only a rise discarded it, so
    on a quiet forecourt a day of sales became one Tanker Load -- and the
    first car after midnight erased yesterday's."""

    # 23:00, pinned: the second test walks past midnight
    START = (2026, 9, 14, 23, 0, 0, 0, 1, -1)

    def a_site(self):
        c = fitted()
        a_tank(c, volume=10000.0)
        c.clock_offset = time.mktime(self.START) - time.time()
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.values["S51300"] = "1"
        c.values["S61001"] = "0105"
        c.tick()
        return c

    def test_forty_sales_twenty_minutes_apart_are_not_a_load(self):
        c = self.a_site()
        for _ in range(40):
            c.tank_level[1]["volume"] -= 15.0
            c.clock_offset += 60.0
            c.tick()
            c.clock_offset += 1140.0
            c.tick()
        self.assertEqual(c.loads.all(1), [])

    def test_a_sale_after_midnight_leaves_yesterdays_load(self):
        c = self.a_site()
        c.tank_level[1]["volume"] -= 3000.0
        c.clock_offset += 60.0
        c.tick()
        c.clock_offset += 600.0
        c.tick()
        self.assertEqual(len(c.loads.all(1)), 1)
        c.clock_offset += 2 * 3600.0
        c.tick()
        c.tank_level[1]["volume"] -= 15.0
        c.clock_offset += 60.0
        c.tick()
        self.assertEqual(len(c.loads.all(1)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
