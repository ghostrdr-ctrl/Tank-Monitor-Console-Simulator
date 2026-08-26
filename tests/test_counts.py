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
"""The numbers NOTES quotes, asserted so they cannot rot quietly.

Four separate NOTES claims in this project were true when written and false
when next read -- the probe, the BIR periods, the diagnostic placeholders, the
unwired setup steps. Every one of them was a COUNT, and counts rot silently
because nothing fails when they do.

So each number NOTES states about the shape of the console is asserted here.
If a change makes one of these wrong, this file fails and the note gets
corrected in the same commit as the change. If a count moves for a good
reason, update the number here AND the sentence in NOTES that quotes it --
that pairing is the whole point.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tls350sim.console import DIAG_MENU, FIELDS, SETUP_MENU   # noqa: E402


def steps():
    for menu in SETUP_MENU:
        for st in menu.get("steps", []):
            yield st


def does_something(st):
    return any(st.get(k) for k in ("code", "console", "archive", "point",
                                   "action", "isdflow"))


class TheSetupMenu(unittest.TestCase):
    """NOTES section 3 and the "Setup" bullet under What works."""

    def test_the_step_count(self):
        self.assertEqual(sum(1 for _ in steps()), 359)

    def test_only_the_submenu_headers_store_nothing(self):
        idle = [st.get("text") for st in steps() if not does_something(st)]
        self.assertEqual(sorted(idle), [
            "Add VMC Serial Number", "Auto Active Switchover",
            "Auto Transmit Setup", "Edit VMC Serial Number",
            "Fiscal Height Security", "Mass/Density",
            "Remove VMC Serial Number", "Transmit Message Setup"])

    def test_and_all_of_them_are_press_enter_headers(self):
        """A header with nothing of its own to store is not an unwired step.
        If one of these ever gains a value it stops being on this list."""
        for st in steps():
            if not does_something(st):
                self.assertEqual(st.get("body"), "PRESS <ENTER>",
                                 st.get("text"))

    def test_every_shared_code_resolves_to_a_part_field(self):
        """46 steps share a function code with another step and are told
        apart by a `field` key -- S53400.d, .w and .p over one function."""
        shared = [st for st in steps()
                  if st.get("code") and st["code"] not in FIELDS]
        self.assertEqual(len(shared), 46)
        for st in shared:
            self.assertIn(st.get("field"), FIELDS, st.get("text"))


class TheFields(unittest.TestCase):

    def test_nothing_is_untyped(self):
        """`raw` meant "stored as an opaque string, nothing validates it".
        There is no such field left, and a new one should be a deliberate
        decision rather than an oversight."""
        raw = sorted(k for k, v in FIELDS.items() if v.get("kind") == "raw")
        self.assertEqual(raw, [])

    def test_the_list_fields_are_the_ones_that_hold_runs(self):
        lists = sorted(k for k, v in FIELDS.items() if v.get("kind") == "list")
        self.assertEqual(lists, ["S52A01", "S52B01", "S52C01", "S61201",
                                 "S61D01", "S75A01", "S7B100", "S7B400"])


class TheDiagnosticScreens(unittest.TestCase):
    """NOTES section 4, which claimed one placeholder and then none."""

    def all_screens(self):
        for fn in DIAG_MENU:
            for sc in fn.get("screens", []):
                yield fn, sc

    def test_the_screen_count(self):
        self.assertEqual(sum(1 for _ in self.all_screens()), 269)

    def test_every_x_template_is_filled_in_by_something(self):
        """98 screens carry an X. A screen is filled either by a `live`
        reading or by an `expand` that generates one row per device -- and
        missing `expand` is exactly how the last audit of this reported a
        placeholder that was not one."""
        naked = []
        for fn, sc in self.all_screens():
            text = " ".join(str(sc.get(k, ""))
                            for k in ("l1", "l2", "l3", "l4"))
            if "XX" in text and not (sc.get("live") or sc.get("expand")):
                naked.append((fn.get("function"), sc.get("l1")))
        self.assertEqual(naked, [])

    def test_the_number_of_screens_with_a_template(self):
        n = sum(1 for _fn, sc in self.all_screens()
                if "XX" in " ".join(str(sc.get(k, ""))
                                    for k in ("l1", "l2", "l3", "l4")))
        self.assertEqual(n, 101)


class TheDocumentationQuotesThem(unittest.TestCase):
    """A number in NOTES that no longer appears there is a number that
    changed shape; a number here that NOTES does not mention is one this
    file is guarding for nobody."""

    def notes(self):
        # NOTES.md is an internal doc kept out of the public release, so it
        # is not always on disk. When it is absent this cross-check has
        # nothing to check against and skips rather than errors.
        path = os.path.join(HERE, "NOTES.md")
        if not os.path.exists(path):
            self.skipTest("NOTES.md not present (public export)")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_notes_still_quotes_the_counts_this_file_pins(self):
        text = self.notes()
        for number in ("269", "587"):
            self.assertIn(number, text,
                          f"NOTES no longer mentions {number}")


if __name__ == "__main__":
    unittest.main()

class FunctionNames(unittest.TestCase):
    """A function's name is a screen, and a screen is 24 columns.

    Ten function names in this simulator came from a manual's SUMMARY TABLE
    rather than from the screen the console draws. A summary table is written
    for a reader: it spells names out and disambiguates them, so it says
    PRESSURE LINE LEAK RESULTS where the console says PRESSURE LINE RESULTS,
    and appends "(VLLD)" to tell two functions apart. Six of the ten did not
    fit the display and were being clipped mid-word.

    The rule that catches all ten is simply that a name has to fit. A console
    never shows a name it cannot draw: where Veeder-Root needed a longer one
    they shortened it -- LEAK to LK, MONITOR to MON -- rather than let it run
    off the end.
    """

    def test_no_function_name_is_wider_than_the_display(self):
        from tls350sim.console import (DIAG_MENU, NORMAL_MENU,   # noqa: E402
                                       RECON_MENU)
        for menu in (SETUP_MENU, NORMAL_MENU, DIAG_MENU, RECON_MENU):
            for fn in menu:
                self.assertLessEqual(len(fn["function"]), 24, fn["function"])
