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
"""The board in the console and the software on it.

A TLS-350, a PLUS and an R are the same box; the CPU board and its software
are the difference. These lock in what the Troubleshooting Guide's chapter 3
tables say a console can do, at every place a person or a tool can find out:
the menus, the revision report, the fourth mode, the sixteenth tank, and the
wire.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, versions                    # noqa: E402
from tls350sim.console import (Console, DEFAULT_BOARD,      # noqa: E402
                               DEFAULT_VERSION, DIAG_MENU, FIELDS,
                               NORMAL_MENU, SETUP_MENU, SOFTWARE_MODULES)
from tls350sim.wire import Handler                          # noqa: E402

SOH = b"\x01"
NOT_UNDERSTOOD = SOH + b"9999FF1B" + SOH.replace(b"\x01", b"\x03")


def fitted(version=None, board=None):
    """Every card and every key, so only the console decides anything."""
    c = Console()
    for key in ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart",
                "plld", "wplld", "vlld", "io", "relay", "pump", "pumpmon",
                "vmc", "mt", "modem", "rs232"):
        c.modules[key] = 1
    for option, _name, _part in SOFTWARE_MODULES:
        c.software[option] = True
    if version:
        c.version = version
    if board:
        c.board = board
    return c


def system_setup():
    return [f for f in SETUP_MENU if f["function"] == "SYSTEM SETUP"][0]


class TheTable(unittest.TestCase):
    """The manual's own chapter 3, parsed rather than typed in."""

    def test_it_is_the_manuals_versions(self):
        self.assertEqual(versions.NUMBERS[0], 1)
        self.assertEqual(versions.NUMBERS[-1], 34)
        self.assertNotIn(13, versions.NUMBERS)          # the manual skips it
        self.assertEqual(versions.RELEASED[1], "3/92")
        self.assertEqual(versions.RELEASED[27], "8/06")
        self.assertEqual(versions.RELEASED[33], "7/13")

    def test_every_gated_feature_names_a_row_of_it(self):
        for slug, row in versions.FEATURE_ROW.items():
            self.assertIn(row, versions.MATRIX, slug)
            self.assertIn(slug, versions.FEATURES, slug)

    def test_features_arrive_when_the_table_says(self):
        first = {f: versions.arrived_in(f) for f in versions.FEATURE_ROW}
        self.assertEqual(first["vlld"], 1)
        self.assertEqual(first["csld"], 2)
        self.assertEqual(first["sitefax"], 2)
        self.assertEqual(first["bir"], 6)
        self.assertEqual(first["fuelman"], 6)
        self.assertEqual(first["plld"], 7)
        self.assertEqual(first["wplld"], 12)
        self.assertEqual(first["birvariance"], 16)
        self.assertEqual(first["smart"], 24)
        self.assertEqual(first["isd"], 25)
        self.assertEqual(first["mt"], 27)
        self.assertEqual(first["service"], 28)
        self.assertEqual(first["fiscal"], 32)
        self.assertEqual(first["alarmreduce"], 32)
        self.assertEqual(first["invthresh"], 33)

    def test_nothing_gated_is_decorative(self):
        """Every slug has to gate something somebody can see."""
        gated = set(versions.MODULE_FEATURE.values())
        gated |= set(versions.SOFTWARE_FEATURE.values())
        gated |= set(versions.TOKEN_FEATURE.values())
        for menu in (SETUP_MENU, DIAG_MENU, NORMAL_MENU):
            for fn in menu:
                for screen in [fn] + fn.get("steps", []):
                    cond = screen.get("when") or {}
                    if cond.get("feature"):
                        gated.add(cond["feature"])
        for field in FIELDS.values():
            for choice in field.get("choices") or []:
                if isinstance(choice, (list, tuple)) and len(choice) > 2:
                    gated.add(choice[2])
        gated |= {"tanks16", "csldmanifold", "birmanifold", "birvariance",
                  "ethanol", "apm", "ifsf", "remotedisp"}   # gated in code
        self.assertEqual(set(versions.FEATURE_ROW) - gated, set())


class TheNumberOnTheScreen(unittest.TestCase):
    """346abb-Tvv-rrr, and what the platform digit means."""

    def test_the_platform_digit_is_the_software_family(self):
        self.assertTrue(versions.info(27, "E7")["number"].startswith("3463"))
        self.assertTrue(versions.info(27, "E4")["number"].startswith("3461"))
        self.assertTrue(versions.info(9, "C0")["number"].startswith("3460"))

    def test_the_console_in_the_photograph(self):
        """An ECPU2 with an NVMEM201 at version 27 is 346327-102-B."""
        info = versions.info(27, "E7")
        self.assertEqual(info["number"], "346327-102-B")
        self.assertEqual(info["version"], "327.02")

    def test_the_same_version_on_a_1xx_board_is_a_1xx_number(self):
        self.assertEqual(versions.info(27, "E4")["version"], "127.02")

    def test_the_report_says_what_the_console_is(self):
        c = fitted(27, "E7")
        report = c.revision_report()
        self.assertIn("VERSION 327.02", report)
        self.assertIn("SOFTWARE# 346327-102-B", report)
        self.assertIn("S-MODULE# 330160-115-A", report)

    def test_a_tool_reads_the_same_revision_over_the_wire(self):
        c = fitted(12, "E1")
        answer = Handler(c).handle(SOH + b"I90200").decode("ascii", "replace")
        self.assertIn("VERSION 112.02", answer)
        self.assertNotIn("MAINTENANCE TRACKER", answer)


class TheSecondRevisionReport(unittest.TestCase):
    """905, "System Revision Level Report II", Version 15.

    The Troubleshooting Guide p1-4 tells a technician to send I90200 on V14
    or earlier and I90500 on V15 or later, so both halves of that sentence
    have to be true of this console.
    """

    def ask(self, console, command):
        return Handler(console, verbose=False).handle(
            SOH + command).decode("ascii", "replace")

    def test_older_software_has_never_heard_of_it(self):
        """V14 or earlier is told to ask 902, because 905 is not there yet."""
        c = fitted(14, "E7")
        self.assertEqual(self.ask(c, b"I90500").encode("ascii"),
                         NOT_UNDERSTOOD)
        self.assertIn("VERSION 314.02", self.ask(c, b"I90200"))

    def test_both_codes_draw_the_same_block(self):
        """The display format is one report under two function codes."""
        c = fitted(33, "E7")
        two, five = self.ask(c, b"I90200"), self.ask(c, b"I90500")
        self.assertIn("SOFTWARE REVISION LEVEL", five)
        self.assertEqual(two.replace("I90200", ""), five.replace("I90500", ""))

    def test_the_computer_format_carries_the_flags_and_the_s_module(self):
        """Where 902 stops after the two identity lines, 905 goes on."""
        c = fitted(33, "E7")
        answer = self.ask(c, b"i90500")
        self.assertIn("SOFTWARE# 346333-102-B", answer)
        self.assertIn("CREATED - 13.07.11.09.42", answer)
        self.assertIn("S-MODULE# 330160-115-A", answer)
        self.assertNotIn("S-MODULE#", self.ask(c, b"i90200"))

    def test_nn_is_the_number_of_two_byte_values_that_follow(self):
        """LL is annotated "(Version 29)", so the count is not a constant."""
        for version, count in ((28, 11), (33, 12)):
            flags = fitted(version, "E7").revision_flags()
            self.assertEqual(len(flags), count)
            body = self.ask(fitted(version, "E7"), b"i90500")
            packed = body.split("CREATED - ")[1][14:]
            self.assertTrue(packed.startswith(f"{count:02X}"))
            self.assertTrue(packed[2:2 + count * 2].startswith("01"))

    def test_the_last_flag_is_unused_on_every_console(self):
        names = [n for n, _on in fitted(33, "E7").revision_flags()]
        self.assertEqual(names[-1], "UNUSED WAS PMC")
        self.assertEqual(dict(fitted(33, "E7").revision_flags())["UNUSED WAS PMC"],
                         False)

    def test_a_pulled_card_turns_its_flags_off(self):
        """The flags are the printed list enumerated, and gate the same way."""
        c = fitted(33, "E7")
        self.assertTrue(dict(c.revision_flags())["PERIODIC IN-TANK TESTS"])
        c.modules["probe"] = 0
        flags = dict(c.revision_flags())
        self.assertFalse(flags["PERIODIC IN-TANK TESTS"])
        self.assertFalse(flags["ANNUAL IN-TANK TESTS"])
        self.assertFalse(flags["CSLD"])

    def test_tanker_load_is_the_flag_the_report_itself_waits_for(self):
        """"Tanker Load Report is a key-enabled option", S513."""
        c = fitted(33, "E7")
        c.values["S51300"] = "0"
        self.assertFalse(dict(c.revision_flags())["TANKER LOAD"])
        c.values["S51300"] = "1"
        self.assertTrue(dict(c.revision_flags())["TANKER LOAD"])

class WhatAnOlderConsoleDoesNotOffer(unittest.TestCase):
    """"Only the functions relevant to your console", three gates deep."""

    def test_the_menus_only_ever_grow_on_one_board(self):
        last = None
        for n in [v for v in versions.NUMBERS if v <= 33]:
            c = fitted(n, "E7")
            c.values["S51300"] = "1"          # tanker load report enabled
            now = (len(c.available_functions()),
                   len(c.visible_steps(system_setup(), 1)),
                   len(c.available_operating()),
                   len(c.available_diagnostics()))
            if last is not None:
                self.assertTrue(all(a >= b for a, b in zip(now, last)),
                                f"V{n} offers less than the version before")
            last = now

    def test_version_one_has_no_pressurised_line_leak(self):
        c = fitted(1, "C0")
        names = [f["function"] for f in c.available_functions()]
        self.assertNotIn("PRESSURE LINE LEAK SETUP", names)
        self.assertNotIn("WPLLD LINE LEAK SETUP", names)
        self.assertNotIn("SMART SENSOR SETUP", names)
        self.assertIn("LINE LEAK DETECTOR SETUP", names)
        self.assertIn("IN-TANK SETUP", names)

    def test_a_card_the_console_cannot_drive_is_not_a_card(self):
        c = fitted(1, "C0")
        self.assertEqual(c.fitted("plld"), 1)      # it is in the slot
        self.assertEqual(c.count("plld"), 0)       # and it does nothing
        self.assertFalse(c.has("plld"))
        self.assertEqual(c.bay_used("power"), fitted().bay_used("power"))
        c.version = 7
        self.assertTrue(c.has("plld"))

    def test_a_key_that_was_never_cut(self):
        c = fitted(20, "E1")
        self.assertFalse(c.licensed("isd"))
        self.assertTrue(c.software["isd"])         # ticked, and worth nothing
        self.assertTrue(c.licensed("bir"))
        c.version = 5
        self.assertFalse(c.licensed("bir"))

    def test_csld_is_not_a_leak_test_method_before_csld(self):
        from tls350sim import fieldio
        field = FIELDS["S61101.method"]
        older = [lab for _v, lab in fieldio.choices_of(field, fitted(1, "C0"))]
        newer = [lab for _v, lab in fieldio.choices_of(field, fitted(2, "C0"))]
        self.assertNotIn("CSLD", older)
        self.assertIn("CSLD", newer)
        self.assertIn("DAILY", older)

    def test_a_screen_arrives_with_the_software_that_brought_it(self):
        def steps(c):
            return [s["text"] for s in c.visible_steps(system_setup(), 1)]
        self.assertNotIn("Alarm Reduction", steps(fitted(31, "E7")))
        self.assertIn("Alarm Reduction", steps(fitted(32, "E7")))
        self.assertNotIn("Service Notice", steps(fitted(27, "E7")))
        self.assertIn("Service Notice", steps(fitted(28, "E7")))


class WhichBoardIsInIt(unittest.TestCase):
    """The half of the question the version does not answer."""

    def test_reconciliation_mode_is_3xx_software(self):
        c = fitted(33, "E7")
        c.values["S60101"] = "011"
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        self.assertEqual(c.family(), "3XX")
        self.assertTrue(c.available_reconciliation())
        c.board = "E4"                              # same version, 1XX board
        self.assertEqual(c.family(), "1XX")
        self.assertEqual(c.available_reconciliation(), [])

    def test_maintenance_tracker_wants_the_nvmem203(self):
        self.assertTrue(fitted(33, "E6").has("mt"))
        self.assertFalse(fitted(33, "E7").has("mt"))
        self.assertTrue(fitted(33, "E6").licensed("isd"))
        self.assertFalse(fitted(33, "E7").licensed("isd"))

    def test_the_sixteenth_tank_wants_the_nvmem201(self):
        self.assertTrue(fitted(33, "E7").supports("tanks16"))
        self.assertFalse(fitted(33, "E6").supports("tanks16"))
        self.assertEqual(fitted(33, "E7").most("probe"), 4)     # 16 tanks
        self.assertEqual(fitted(33, "E6").most("probe"), 2)     # 8
        c = fitted(33, "E6")
        c.modules["probe"] = 0
        self.assertFalse(c.set_module("probe", 3))
        self.assertTrue(c.set_module("probe", 2))

    def test_a_cell_naming_two_boards_is_not_about_the_memory_card(self):
        """CSLD at V33 is "E4, E7", which is not "an NVMEM203 cannot gauge"."""
        self.assertEqual(versions.cell("csld", 33, "E7"), ["E4", "E7"])
        for board in ("E4", "E5", "E6", "E7"):
            self.assertTrue(versions.supports(33, board, "csld"), board)


class ItIsSavedAndItStays(unittest.TestCase):
    """Changing either one is a service call, not a power cut."""

    def test_a_reset_changes_neither(self):
        c = fitted(19, "E1")
        c.reset(keep_clock=True)
        self.assertEqual((c.version, c.board), (19, "E1"))
        self.assertEqual(c.modules, {"probe": 1, "rs232": 1})

    def test_an_example_site_brings_its_own(self):
        c = fitted(2, "C0")
        presets.load(c, "Truck stop, four tanks and BIR")
        self.assertEqual((c.version, c.board), (DEFAULT_VERSION, DEFAULT_BOARD))
        self.assertTrue(c.has("plld"))

    def test_the_compliance_site_brings_the_board_its_card_needs(self):
        c = Console()
        presets.load(c, "Compliance site, CSLD and sensors")
        self.assertEqual(c.board, "E6")
        self.assertTrue(c.has("mt"))
        self.assertTrue(c.licensed("csld"))

    def test_both_are_saved_with_everything_else(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "_version_state.json")
        try:
            c = Console(path)
            c.set_version(21)
            c.set_board("E1")
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
            self.assertEqual((blob["version"], blob["board"]), (21, "E1"))
            again = Console(path)
            self.assertEqual((again.version, again.board), (21, "E1"))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_it_refuses_what_the_manual_does_not_list(self):
        c = Console()
        self.assertFalse(c.set_version(13))        # the manual skips 13
        self.assertFalse(c.set_version(99))
        self.assertFalse(c.set_board("E9"))
        self.assertEqual((c.version, c.board), (DEFAULT_VERSION, DEFAULT_BOARD))


if __name__ == "__main__":
    unittest.main()
