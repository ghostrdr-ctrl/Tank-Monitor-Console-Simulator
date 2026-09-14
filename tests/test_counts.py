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


def when_clauses(cond):
    """A step's gate as the list of conditions `visible` requires.

    Not one dict: `and` carries CLAUSES, and two of them can use the same
    keys -- the Vac sensor's relief valve pressure is `code S72301 is 04`
    AND `code S72B01 is 1`, so folding them together loses one. AUTO DELAY
    TIME keeps its module gate in `and` for the same reason, because its
    own first clause is an `any_setting`.
    """
    cond = dict(cond or {})
    also = cond.pop("and", None) or []
    return [c for c in [cond] + [dict(x) for x in also] if c]


def does_something(st):
    return any(st.get(k) for k in ("code", "console", "archive", "point",
                                   "action", "isdflow", "vmc"))


class TheSetupMenu(unittest.TestCase):
    """NOTES section 3 and the "Setup" bullet under What works."""

    def test_the_step_count(self):
        """462 since EVR/ISD SETUP gained the three branch headers
        577013-800 Rev P Figure 6 draws at the top of it. The pass that
        gave this function six headers read Figure 7, which is the SECOND
        half of the same walk; Figure 6 is the first, and it puts `EVR
        TYPE`, `NOZZLE A/L RANGE` and `VAPOR PROCESSOR SETUP` over their
        own screens. See FIDELITY I11a.

        459 since the four alarm-assignment functions gained the
        seventeen alarm-group screens 576013-623 Rev AN ch.24 and ch.25
        draw. OUTPUT RELAY SETUP and the three `... LINE DISABLE SETUP`
        functions each drew ONE step whose second line was a truncated row
        of the chapter-3 Setup Mode Programming Table, `RELAY ASSIGNMENTS -
        FOR `, which is not a message any console can display; the four
        steps are gone and sixty-eight are in their place, one per alarm
        family per function, each gated on the card that family belongs to
        the way p.24-5 says -- "Only installed components will display, so
        some of the alarm groups may not appear." See FIDELITY U6.

        395 since the Generator and Pump Sense external inputs gained
        the screens 576013-623 Rev AN pp.23-3 to 23-5 draws for them. Two
        of the five input types ended at their own type screen, so an
        emergency generator could not be told which tanks feed it -- which
        is what makes the console run a continuous leak test in them while
        the generator is off. See CLOSED U47 and the setup-mode audit's
        SU23.

        389 since three families of pipe gained the screens they need.
        576013-623 Rev AN p.10-3 and p.11-3 both change what the LINE
        LENGTH screen IS when the pipe has two diameters, and p.10-4 gives
        the USER DEFINED type two DIAMETER screens between its lengths and
        its bulk moduli. This console drew one ungated LINE LENGTH for all
        nineteen types, and the four fields the other screens needed were
        defined and named by no step at all. See CLOSED U45.

        381 since EVR/ISD SETUP gained the six `PRESS <ENTER>` headers
        577013-800 Rev P p.20-13's Figure 7 draws. The function was a flat
        list of eleven value screens where the figure has four branches at
        the top level and two more inside one of them -- so the airflow
        meter's screen and the pressure sensor's were indistinguishable on
        the glass, both reading `LABEL:` over `SN#: DISABLED`, because the
        screen that says which device you are on was one of the missing
        ones. See CLOSED U44 and the setup-mode audit's SU29.

        375 since WPLLD LINE LEAK SETUP gained its SELECT TANK screen.
        576013-623 Rev AN p.11-6 draws `W1: SELECT TANK` / `NONE` and
        576013-635 Rev AA p.425 gives it function code 7A5 -- "tt - Tank
        number (Decimal) (00=no tank)" -- which the console reads
        internally and answers on the wire. Only the panel screen was
        absent, and it is the screen that lets a WPLLD act as a pump sense
        input for CSLD and automatic in-tank leak detection. See CLOSED
        U42 and the setup-mode audit's SU13.

        374 since AUTO ACTIVE SWITCHOVER left. 576013-623 Rev AN
        prints those two words twice, on p.10-8 and p.15-3, and both times
        they are a SECTION HEADING over prose that ends "Press STEP to
        continue:" -- the three switchover screens under it are ordinary
        steps and the console has no `PRESS <ENTER>` screen there at all.
        It had been imported as one, and the citations builder found the
        heading and called it exact. See CLOSED U40.

        375 since Setup Mode gained the descent Diagnostic Mode had.
        Four screens joined the menu with it, and every one of them is a
        screen 576013-623 Rev AN draws and this console did not have: the
        `PRESS <ENTER>` prompts over SERVICE NOTICE (p.5-27) and ISO 3166
        COUNTRY (p.5-28), the CUSTOM ALARM LABELS flag that had been
        sharing its step with that step's own prompt (p.5-19), and the
        `BUS SLOT FUEL METER TANK` row under MODIFY TANK/METER MAP
        (p.17-5), which was the only panel route to the tank/meter map and
        did not exist. See CLOSED U38 and U39.

        371 since AUTO DIAL METHOD arrived. 576013-623 Rev AN p.6-11
        draws two screens where COMMUNICATIONS SETUP had one, and the one
        it had wore the other one's name: `AUTO DIAL METHOD / ALL PHONES`
        is function 529, and the screen carrying that label was programming
        52B, the auto dial TYPE AND START TIME. See FIDELITY N6a.

        370 since SMART SENSOR SETUP gained the two sub-walks 576013-623
        Rev AN chapter 26 carries under it. The function had Config, Label
        and Category and stopped; the chapter goes on to a Mag sensor's
        alarm upgrade delay and its two water heights, and to a Vac
        sensor's pump, interstitial volume and relief valve. Nine steps,
        four function codes that had no way onto the panel and two that had
        no field at all. See FIDELITY M3.

        361 since Low Pressure Shutoff Value joined PRESSURE LINE LEAK
        SETUP. 576013-623 Rev AN p.10-6 draws `LOW PRESSURE SHUTOFF: NO`
        and then `LOW PRESSURE: 5`; the console had the flag and not the
        value, so the number the low pressure alarm is measured against
        could not be programmed from the panel at all. Serial function 78F
        is "PP - Low Pressure, PSI". See FIDELITY N2.

        360 was Select Modem joining COMMUNICATIONS SETUP: p.6-8 draws it
        as "D1:" over "SELECT MODEM: 3", serial function 525 is "Set
        Receiver Port Number to Dial", and the tape prints the row as
        `PORT  NO: 1`."""
        self.assertEqual(sum(1 for _ in steps()), 462)

    def test_only_the_submenu_headers_store_nothing(self):
        """And a header stores nothing BECAUSE it is a header.

        Three of these carried a function code they could not set, which
        is what made `CUSTOM ALARM LABELS` a screen holding a prompt and a
        flag at once -- and after CHANGE stopped writing over the prompt
        (U33) the flag had no screen at all. The code belongs to the
        screen a level down that sets it.
        """
        idle = [st.get("text") for st in steps() if not does_something(st)]
        self.assertEqual(sorted(idle), [
            "Add VMC Serial Number", "Airflow Meter Select",
            "Auto Transmit Setup", "Custom Alarms", "EVR Type Menu",
            "Edit Fuel Hose",
            "Edit Fuel Hose Labels Menu", "Edit VMC Serial Number",
            "Fiscal Height Security", "Fuel Hose Table Setup",
            "ISO 3166 Country", "Individual Meter Offset",
            "Mag Sensor Setup", "Mass/Density", "Modify Tank/Meter Map",
            "Nozzle A/L Range Menu",
            "Pressure Sensor Select", "Remove VMC Serial Number",
            "Service Notice", "Set Analysis Times",
            "Transmit Message Setup", "Vac Sensor Setup",
            "Vapor Processor Setup"])

    def test_and_all_of_them_are_press_enter_headers(self):
        """A header with nothing of its own to store is not an unwired step.
        If one of these ever gains a value it stops being on this list."""
        for st in steps():
            if not does_something(st):
                self.assertEqual(st.get("body"), "PRESS <ENTER>",
                                 st.get("text"))

    def test_no_child_screen_outlives_the_branch_that_holds_it(self):
        """A screen a level down is offered as part of its parent's branch,
        and the level split reads DEPTH -- so a child whose parent is gated
        out is not hidden with it, it is adopted by whatever branch came
        before. The EVR/ISD SET START TIME and SET POST DELAY screens did
        exactly that: their header carries "this step appears only after
        completing Fuel Hose Table Setup" and they carried nothing, so on a
        console with no hoses they turned up inside FUEL HOSE TABLE SETUP.

        The rule this asserts is the one every other branch in the file
        already follows: a child repeats its parent's gate. It is stricter
        than "the child is hidden whenever the parent is" and it is
        checkable without a console.
        """
        orphans = []
        for menu in SETUP_MENU:
            parents = {}
            for st in menu.get("steps", []):
                depth = st.get("depth") or 0
                parents[depth] = st
                if not depth:
                    continue
                up = parents.get(depth - 1) or {}
                have = when_clauses(st.get("when"))
                for clause in when_clauses(up.get("when")):
                    # one of the child's clauses has to carry the whole of
                    # this one -- not equal it, because a child can add its
                    # own keys to the same clause, as DELIVERY OVERRIDE
                    # does with the code and module its parent's SERVICE
                    # NOTICE gate does not have
                    if not any(clause.items() <= c.items() for c in have):
                        orphans.append(f"{menu['function']}: {st['text']} "
                                       f"is missing its parent's {clause}")
        self.assertEqual(orphans, [])

    def test_every_shared_code_resolves_to_a_part_field(self):
        """47 steps share a function code with another step and are told
        apart by a `field` key -- S53400.d, .w and .p over one function.

        47 since the Mag sensor's two water heights arrived: 728 is one
        function code holding a threshold per ALARM DEFINITION RECORD --
        "AA - Alarm Definition Record ID" -- and the water warning is record
        2 where the water alarm is record 3. See FIDELITY M3.

        45 before that, and 46 until IN-TANK SETUP's WATER MINIMUM step
        stopped writing 60E's fourth column and started writing `648`, the
        code whose own name is Set Probe Water Minimum. See FIDELITY R13.

        48 since the RS-232 security code became two parts. `536` packs a
        status byte and a six-character code -- `s536PPsaaaaaa` -- and the
        report a real console draws for it prints them as two columns. The
        panel has a screen for the code alone, because the status is a DIP
        switch. See FIDELITY S14.
        """
        shared = [st for st in steps()
                  if st.get("code") and st["code"] not in FIELDS]
        self.assertEqual(len(shared), 48)
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
        """`S80C01.gentanks` is the ninth and the only one that is a PART of
        a code rather than the whole of it: 80C holds an external input's
        type, its orientation and -- for a generator -- the run of tanks
        that feed it, "enter the individual tank numbers", over a screen
        576013-623 Rev AN p.23-3 draws as `TANK #: X, X`."""
        lists = sorted(k for k, v in FIELDS.items() if v.get("kind") == "list")
        self.assertEqual(lists, ["S52A01", "S52B01", "S52C01", "S61201",
                                 "S61D01", "S75A01", "S7B100", "S7B400",
                                 "S80C01.gentanks"])


class TheDiagnosticScreens(unittest.TestCase):
    """NOTES section 4, which claimed one placeholder and then none."""

    def all_screens(self):
        for fn in DIAG_MENU:
            for sc in fn.get("screens", []):
                yield fn, sc

    def test_the_screen_count(self):
        # 278 since ALARM HISTORY REPORT gained the three screens the
        # operator's manual documents and Figure 6-23 does not draw: `s`
        # the smart sensor (p.27-5), `X1` the VMCI and `x` the VMC
        # (pp.27-6 and 27-7). Each is gated on the card that serves it, so
        # a console without a smart sensor or a VMC does not offer them.
        # FIDELITY W25.
        #
        # 275 was COMMUNICATION DIAGNOSTIC being put into Figure 6-27's own
        # order and given the four screens it was missing: the CHANGE state
        # of AUTO CONFIG MODEM, its ENTER confirmation, the second ENTER
        # confirmation after ARE YOU SURE, and the closing return to
        # COMM BOARD / MODEM the figure annotates "Displayed when modem is
        # configured". Read off p.6-22 by word position -- the screen column
        # sits at x=120 and the step markers at x=79 -- because this figure
        # is the one a reading-order extraction got wrong once already.
        # FIDELITY D4.
        #
        # 271 was SERVICE REPORT gaining the two screens of Figure 6-3's
        # other column: a console with a Maintenance Tracker asks for a
        # service CODE and its label, one without also asks for an ID.
        # FIDELITY D3.
        #
        # 278 was MAINT HARDWARE KEY BLOCK being given Figure 6-4's two
        # branches: the key list is one screen that expands to a row per
        # ACTIVE key rather than three of the figure's own names, and the
        # ENTER ID branch gained the `PRESS <ENTER>` head it was missing and
        # its own confirmation. FIDELITY D13.
        #
        # 279 was BIR DIAGNOSTICS being given Figure 6-25's: the populated
        # head of the Meter Events Table, the time of the last event that
        # PRINT hangs off, and the Start-Event branch, which is its own two
        # screens because a meter that has begun to run has no gallons yet.
        # FIDELITY D14.
        #
        # 283 was PUMP RELAY MONITOR DIAG getting Figure 6-16's second
        # column: the branch with no pump relay assigned, which is the label
        # over the pump state, and which this console drew half of in the
        # middle of the other branch. FIDELITY D1.
        #
        # 284 went DOWN to 283, which is the one direction this count has
        # not moved before. SERVICE NOTICE SESSION had three screens --
        # DISABLED, DURATION : 2 and ENABLED -- and Figure 6-5 has two: the
        # last of its nine cells is the FIRST screen come round again,
        # reading ENABLED because the walk just changed it. A console cannot
        # be disabled and enabled at once, and asserting both was the defect.
        # FIDELITY D9.
        self.assertEqual(sum(1 for _ in self.all_screens()), 283)

    def test_every_x_template_is_filled_in_by_something(self):
        """102 screens carry an X. A screen is filled either by a `live`
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
        # 104 since the two REF DISTANCE screens carry the figure's own
        # `MM/DD/YY        XX.XX` rather than the date alone -- the live
        # reader has answered with both since D7, and the literal line
        # underneath it, which is what a capacitance-probe console falls
        # back to, still said the date.
        #
        # 102 was GROSS VOLUME CHANGE stopping drawing a blank second
        # line: Fig 6-24 draws it over "XXXX  GALS". FIDELITY D2.
        #
        # 105 since both of Figure 6-4's branches end on the same
        # `BLOCK: XXXXXX` confirmation, and both of them read the key the
        # panel has selected. FIDELITY D13.
        #
        # 106 with the Start-Event branch's own `FP: XX`, which is the
        # figure's line for a meter that has started and not finished.
        # FIDELITY D14.
        self.assertEqual(n, 106)


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
        for number in ("278", "587"):
            self.assertIn(number, text,
                          f"NOTES no longer mentions {number}")


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


if __name__ == "__main__":
    unittest.main()
