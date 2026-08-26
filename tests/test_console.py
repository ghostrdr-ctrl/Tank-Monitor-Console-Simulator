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
"""What the console must get right, tested without a window.

Everything here was verified by hand against the manuals first; these lock it
in. Run with `python -m unittest discover tests` from the project root.
"""
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import (csld, fieldio, leaktest,             # noqa: E402
                       presets, printer, wirelists)
from tls350sim.console import (Console, DIAG_MENU, FIELDS,  # noqa: E402
                               NORMAL_MENU, SETUP_MENU, SOFTWARE_MODULES,
                               describe_alarms)
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
                "vmc", "mt", "modem", "rs232"):
        c.modules[key] = 1
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
        c = fitted()
        plain = self.steps(c, "PRESSURE LINE LEAK SETUP")
        c.values["S78801"] = "0118"                 # USER DEFINED
        defined = self.steps(c, "PRESSURE LINE LEAK SETUP")
        self.assertEqual(len(defined) - len(plain), 5)

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
        steps = self.steps(c, "COMMUNICATION SETUP")
        self.assertNotIn("Receiver Telephone Number", steps)
        self.assertIn("Baud Rate", steps)
        c.modules["modem"] = True
        self.assertIn("Receiver Telephone Number",
                      self.steps(c, "COMMUNICATION SETUP"))

    def test_select_tank_waits_until_a_tank_is_configured(self):
        c = fitted()
        steps = self.steps(c, "LINE LEAK DETECTOR SETUP")
        self.assertNotIn("Tank #", steps)
        a_tank(c)
        self.assertIn("Tank #",
                      self.steps(c, "LINE LEAK DETECTOR SETUP"))

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
        c = fitted()
        for code, profile in (("605", "01"), ("606", "02"), ("60A", "03"),
                              ("63C", "04")):
            c.values.clear()
            c.values[f"S{code}01"] = "01" + float_value(9728.0)
            self.assertEqual(c.tank_profile(1), profile, code)

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
        self.assertEqual(c.bay_free("comm"), 1)

    def test_the_comm_bay_holds_four_cards(self):
        c = Console()
        c.modules = {}
        c.set_module("rs232", 3)
        self.assertTrue(c.set_module("modem", 1))
        self.assertFalse(c.set_module("mt", 1))           # the bay is full

    def test_pulling_a_card_takes_its_functions_with_it(self):
        c = fitted()
        self.assertIn("COMMUNICATION SETUP",
                      [f["function"] for f in c.available_functions()])
        for key in ("rs232", "modem", "mt", "vmc"):
            c.set_module(key, 0)
        self.assertNotIn("COMMUNICATION SETUP",
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
        c = Console()
        c.modules = {}
        c.set_module("probe", 1)
        c.set_module("relay", 1)
        c.set_module("rs232", 1)
        screens = [l1 for l1, _l2 in c.slot_report()]
        self.assertIn("SLOT 1 4 PROBE", screens)
        self.assertIn("SLOT 1 4 RELAY", screens)
        self.assertIn("COMM 1 RS-232", screens)
        self.assertEqual(len(screens), 8 + 8 + 4)


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
        self.assertEqual([f["function"] for f in c.available_diagnostics()],
                         ["SYSTEM DIAGNOSTIC", "ALARM HISTORY REPORT",
                          "ARCHIVE"])

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
                         "T 1:MISSING DELIVERY TICKET")

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
        self.assertIn(b"EXX23223", report)

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


class ContinuousTesting(unittest.TestCase):
    """CSLD: tested while the tank is idle, reported every 24 hours."""

    def a_csld_tank(self, leak=0.0):
        c = fitted()
        a_tank(c)
        c.software["csld"] = True
        # S611: twelve hours, 0.2 gph, method 7 = CSLD
        c.values["S61101"] = "01" + "12" + "0" + "7" + "0000"
        c.tank_leak[1] = leak
        return c

    def days(self, c, hours=30):
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
        self.assertGreater(len(c.csld.samples[1]), 20)
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
                         "T 1:NO CSLD IDLE TIME WARNING")

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
                         "Periodic Leak Test Fail")

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

    def test_no_bir_key_no_meter_data(self):
        c = self.a_site()
        c.software["bir"] = False
        before = c.tank_level[1]["volume"]
        self.hours(c, 4)
        self.assertEqual(c.tank_level[1]["volume"], before)

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
                    value = fieldio.choices_of(f)[0][1]
                elif kind == "flag":
                    value = "1"
                elif kind == "time":
                    value = "0630"
                elif kind == "date":
                    value = "260819"
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
                data = fieldio.encode(f, code, value, c.values.get(code))
                self.assertIsNotNone(data, code)
                if kind == "profile":
                    continue          # the profile lives in the volume codes
                c.values[code] = data
                self.assertNotEqual(fieldio.decode(f, code, data), "", code)


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

    def test_correcting_the_cause_clears_an_unlatched_alarm(self):
        c = self.low_product()
        c.tank_level[1]["volume"] = 500.0
        c.compute_alarms()
        c.tank_level[1]["volume"] = 5000.0
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

    def test_maintenance_tracker_protects_every_alarm(self):
        c = self.low_product()
        # Maintenance Tracker wants the NVMEM203 board, so a console without
        # one has the card in it and no code to drive it
        c.board = "E6"
        c.modules["mt"] = True
        c.tank_level[1]["volume"] = 500.0
        c.compute_alarms()
        c.tank_level[1]["volume"] = 5000.0
        self.assertEqual(c.compute_alarms(), ["020501"])
        c.acknowledge(keyed=False)
        self.assertEqual(c.compute_alarms(), ["020501"])
        c.acknowledge(keyed=True)
        self.assertEqual(c.compute_alarms(), [])

    def test_an_alarm_is_described_the_way_the_console_shows_it(self):
        a = describe_alarms(["020501"])[0]
        self.assertEqual(a["screen"], "T 1:LOW PRODUCT ALARM")
        self.assertEqual(a["text"], "Tank 1: Low Product Alarm")


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
        self.assertTrue(self.ask("\x01I60201\r").endswith("REGULAR\x03"))

    def test_the_beeper_wants_its_verification_code(self):
        """"149 - This verification code must be sent to confirm the command",
        <SOH>S53000x149."""
        self.assertEqual(self.ask(SOH + "S530000" + CR), NOT_UNDERSTOOD_TEXT)
        self.assertNotIn("S53000", self.c.values)
        self.ask(SOH + "S530001149" + CR)
        self.assertEqual(self.c.values["S53000"], "1")
        # "BEEPER: ENABLED", the line the manual prints for it
        self.assertIn("BEEPER: ENABLED", self.ask(SOH + "I53000" + CR))

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
        lines = self.ask(SOH + "I10100" + CR).strip(SOH + ETX).split(SEP)
        self.assertEqual(lines[0], "I10100")
        self.assertEqual(lines[1], "JAN 29, 2003 11:05:00 AM")
        self.assertEqual(lines[2], "GREENFIELD SERVICE")
        self.assertEqual(lines[3], "SYSTEM STATUS REPORT")

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
        report = self.ask(b"\x01I90200\r")
        self.assertIn("VERSION", report)
        self.assertIn("0.10 REPETITIVE", report)     # a PLLD feature
        self.c.modules["plld"] = False
        self.assertNotIn("0.10 REPETITIVE", self.ask(b"\x01I90200\r"))

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
        self.assertIn("LAST PERIODIC TEST PASSED:", text)
        self.assertIn("FULLEST PERIODIC TEST PASSED EACH MONTH:", text)

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
        self.c.values["S60201"] = "01FIRST"
        self.c.values["S60202"] = "02SECOND"
        reply = self.ask("\x01I60200\r")
        self.assertIn("01FIRST02SECOND", reply)


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
        self.assertEqual(c.live_reading("height", 1).strip(), "24.00 INCHES")
        c.tank_level[1]["volume"] = 5000.0
        self.assertEqual(c.live_reading("volume", 1).strip(), "5000 GALS")

    def test_full_volume_comes_from_either_profile(self):
        c = fitted()
        c.values["S60401"] = "01" + float_value(8000.0)
        self.assertEqual(c.full_volume(1), 8000.0)
        c.values["S60A01"] = "01" + float_value(9000.0)
        self.assertEqual(c.full_volume(1), 9000.0)

    def test_a_sensor_reads_what_the_bench_is_driving(self):
        c = fitted()
        c.sensor_state[("liquid", "1")] = "fuel"
        self.assertEqual(c.live_reading("sensor_liquid", 1), "FUEL ALARM")
        self.assertEqual(c.live_reading("sensor_liquid", 2), "NORMAL")

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
        volume + manual adjustments"."""
        c = self.a_site()
        row = c.bir.current(1)
        row.update(opening=800.0, sales=285.0, ticketed=800.0, adjust=0.0,
                   physical=8904.0)
        self.assertEqual(c.bir.book(row), 1315.0)
        self.assertEqual(c.bir.analysis(row)["book_var"], 8904.0 - 1315.0)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)


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
        c.sensor_state[("liquid", "1")] = "fuel"
        self.assertIn("030301", c.compute_alarms())
        c.sensor_state[("liquid", "1")] = "water"
        self.assertNotIn("030601", c.compute_alarms())
        self.assertEqual(c.sensor_reading("liquid", 1), "NORMAL")
        c.sensor_state[("liquid", "1")] = "short"
        self.assertNotIn("030501", c.compute_alarms())

    def test_a_discriminating_pan_sensor_has_the_full_set(self):
        c = self.a_sensor(kind="4")
        self.assertEqual(set(c.sensor_states("liquid", 1)),
                         {"fuel", "out", "short", "high", "warn"})
        c.sensor_state[("liquid", "1")] = "short"
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

    def test_a_probe_is_a_standard_length_that_clears_the_tank(self):
        self.assertEqual(self.a_gauged_tank(diameter=96.0).probe_length(1),
                         96.0)
        self.assertEqual(self.a_gauged_tank(diameter=64.0).probe_length(1),
                         72.0)
        self.assertEqual(self.a_gauged_tank(diameter=120.0).probe_length(1),
                         120.0)

    def test_num_samples_is_twenty_on_a_mag_probe(self):
        """"Under normal operating conditions, this number should read 20"."""
        c = self.a_gauged_tank()
        self.assertEqual(c.diag_value("probe_samples", 1), "NUM SAMPLES: 20")


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

    def test_an_empty_slot_reads_open_circuit(self):
        """"UNUSED 10191362 10329900" in the intrinsically safe bay and
        "COMM 4-6 UNUSED 15000000 15000000" in the communication bay."""
        c = Console()
        c.modules = {"probe": 1}
        rows = c.slot_report()
        slots = [v for k, v in rows if k.startswith("SLOT") and "UNUSED" in k]
        comms = [v for k, v in rows if k.startswith("COMM") and "UNUSED" in k]
        self.assertTrue(slots and comms)
        self.assertIn("10200000", slots[0])
        self.assertIn("15000000", comms[0])


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
        self.assertIn("T 1:PROBE OUT ALARM", screens)

    def test_reset_plugs_every_probe_back_in(self):
        c = self.a_site()
        c.probe_out.add(1)
        c.reset(keep_clock=True)
        self.assertEqual(c.probe_out, set())
