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
"""Screens the audit against the manuals found missing or wrong.

Each of these was checked by hand against the manual named in its docstring
and then fixed; they are here so it stays fixed. The audit itself is in
AUDIT.md and needs the PDFs, which are not in this repository; these are the
part of it that can run.
"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False

from tls350sim import printer                               # noqa: E402
from tls350sim.clock import clock_date, clock_words          # noqa: E402
from tls350sim.console import (Console, DIAG_MENU,           # noqa: E402
                               NORMAL_MENU)
from tls350sim.wire import Handler                          # noqa: E402


def screens(function):
    fn = [f for f in DIAG_MENU if f["function"] == function][0]
    return [(s.get("l1", ""), s.get("l2", "")) for s in fn["screens"]]


def steps(function):
    fn = [f for f in NORMAL_MENU if f["function"] == function][0]
    return [s["text"] for s in fn["steps"]]


class Diagnostics(unittest.TestCase):
    """Troubleshooting Guide 576013-818, chapter 6."""

    def test_alarm_history_has_every_device_the_figure_has(self):
        """Figure 6-23: seventeen, including g (BIR) and r (pump relay)."""
        got = [l1.split(" ")[0] for l1, _l2 in screens("ALARM HISTORY REPORT")]
        for letter in ("T", "L", "V", "I", "P", "G", "C", "H", "D", "M", "E",
                       "F", "Q", "W", "g", "r"):
            self.assertIn(letter, got, letter)
        self.assertIn(("SYSTEM ALARM HISTORY", "PRESS <PRINT> FOR REPORT"),
                      screens("ALARM HISTORY REPORT"))

    def test_the_line_leak_diagnostics_are_the_plld_manuals_own(self):
        """576013-818 defers these to 577013-344, whose Figures 19 and 20 draw
        them. The referral itself was never a display line."""
        plld = screens("PRESSURE LINE LEAK DIAG")
        self.assertIn(("Q 1: (PRODUCT LABEL)", "3.0 DIAG PRESS <PRINT>"), plld)
        self.assertIn(("Q 1: (PRODUCT LABEL)", "P OFFSET TEST <ENTER>"), plld)
        self.assertIn(("Q 1:PRESS OFFSET TEST", "DONE - OFFSET: +XX.X PSI"),
                      plld)
        wplld = screens("WPLLD LINE LEAK DIAG")
        self.assertIn(("W 1: PENDING    PUMP OFF", "TEST COMPLETE HANDLE OFF"),
                      wplld)
        self.assertIn(("W 1: LAST READ=X.XXX PSI", "TOTAL MESSAGE: X"), wplld)
        for function in ("PRESSURE LINE LEAK DIAG", "WPLLD LINE LEAK DIAG"):
            for l1, l2 in screens(function):
                self.assertNotIn("SEE PLLD", l1 + l2)
                self.assertNotIn("577013-", l1 + l2)

    def test_the_isd_functions_are_there_with_the_key(self):
        """577013-819 Figures 4 and 6; 577013-800 for the setup function."""
        isd = screens("ISD DIAGNOSTIC")
        self.assertIn(("CLEAR TEST AFTER REPAIR", "PRESS <ENTER>"), isd)
        self.assertIn(("VAPOR COLLECTION TEST", "PRESS <ENTER>"), isd)
        pmc = screens("PMC DIAGNOSTIC")
        # 937-J draws two variants, the ECS membrane and the V-R polisher,
        # under one function name; the panel shows the one whose processor
        # is selected. Both defaults read AUTOMATIC.
        self.assertIn(("VAPOR PROCESSOR MODE", "AUTOMATIC"), pmc)
        self.assertIn(("HYDROCARBON SENSOR", "HC SENSOR      XX.XXX%"), pmc)
        self.assertIn(("VEEDER-ROOT POLISHER", "LOAD:       XX.X%"), pmc)

    def test_csld_diagnostics_has_both_months(self):
        """Figure 6-11 has a current and a previous month, and they are ONE
        screen: `SELECT: CURRENT MONTH` with a `C` to `SELECT: PREVIOUS
        MONTH`, which 576013-610 Rev AC p.27-3 walks as "Press CHANGE, then
        ENTER". They were two STEP screens and CHANGE did nothing on either.
        The previous month's line is drawn by the selection now, and the
        citation walk reaches it by walking both choices. FIDELITY D23."""
        fn = [f for f in DIAG_MENU if f["function"] == "CSLD DIAGNOSTICS"][0]
        pick = [sc for sc in fn["screens"] if sc.get("sel") == "csld_month"]
        self.assertEqual(len(pick), 1)
        self.assertEqual(pick[0]["l2"], "SELECT: CURRENT MONTH")
        self.assertEqual(pick[0]["choices"],
                         ["CURRENT MONTH", "PREVIOUS MONTH"])
        got = screens("CSLD DIAGNOSTICS")
        self.assertNotIn(("CSLD MONTHLY REPORT", "SELECT: PREVIOUS MONTH"),
                         got)
        self.assertIn(("T #: (Product Label)", "CUR CSLD MONTHLY <PRINT>"), got)
        self.assertIn(("T #: (Product Label)", "PRV CSLD MONTHLY <PRINT>"), got)

    def test_the_service_report_has_both_of_the_figure_s_branches(self):
        """Figure 6-3 splits this function in two columns, headed
        "Maintenance Tracker Enabled" and "Maintenance Tracker Not
        Enabled". Both ask for a service CODE and its label; only the
        console WITHOUT a Tracker asks for an ID, because a console with a
        key reader takes the technician's identity off the key.

        This file used to read the figure as "ENTER SERVICE ID, not ENTER
        SERVICE CODE" -- one column of a two-column figure. FIDELITY D3.
        """
        got = [l1 for l1, _l2 in screens("SERVICE REPORT")]
        for both in ("SERVICE CODE LIST", "ENTER SERVICE CODE",
                     "ENTER SERVICE CODE LABEL"):
            self.assertIn(both, got)
        self.assertIn("ENTER SERVICE ID", got)
        self.assertIn("ENTER SERVICE ID LABEL", got)

    def test_the_service_report_asks_who_before_it_asks_what(self):
        """And the ID pair comes FIRST. Figure 6-3's right-hand column walks
        SERVICE CODE LIST, ENTER SERVICE ID, ENTER SERVICE ID LABEL, ENTER
        SERVICE CODE, ENTER SERVICE CODE LABEL -- read off the page by word
        position, since both columns draw the same five headings and an
        extraction interleaves them: on p.6-4 the right column's y values run
        393.3, 445.2, 493.4, 540.5, 591.2 in that order. This console put the
        CODE pair before the ID pair, so a technician was asked what he did
        before he was asked who he was. FIDELITY D3."""
        from tls350sim.console import Console

        def walk(tracker):
            c = Console()
            c.set_module("probe", 1)
            if tracker:
                c.board = "E6"          # an ECPU2 with an NVMEM203 drives it
                c.set_module("mt", 1)
            fn = [f for f in DIAG_MENU
                  if f["function"] == "SERVICE REPORT"][0]
            return [sc["l1"] for sc in fn["screens"] if c.visible(sc, 1)]

        self.assertEqual(walk(tracker=False),
                         ["SERVICE CODE LIST",
                          "ENTER SERVICE ID", "ENTER SERVICE ID LABEL",
                          "ENTER SERVICE CODE", "ENTER SERVICE CODE LABEL"])
        # the Tracker branch takes the identity off the key and asks neither
        self.assertEqual(walk(tracker=True),
                         ["SERVICE CODE LIST",
                          "ENTER SERVICE CODE", "ENTER SERVICE CODE LABEL"])

    def test_every_accuchart_screen_fills_the_display(self):
        """Fig 6-10 puts every value against the right of the display, and
        the display is 24 characters. These were hand-padded one at a time:
        three landed on 24, five stopped at 20, one at 23, one at 19 -- and
        ACCU DURATION came to 25, a character wider than the screen it draws
        on. FIDELITY D5.
        """
        from tls350sim.console import Console
        c = Console()
        c.set_module("probe", 1)
        for token in ("accu_mode", "accu_status", "accu_updates",
                      "accu_duration", "accu_diameter", "accu_length",
                      "accu_offset", "accu_tilt", "accu_shape",
                      "accu_volume", "accu_fitness", "accu_data",
                      "accu_warn"):
            line = str(c.diag_reading(token, 1))
            self.assertEqual(len(line), 24, f"{token}: {line!r}")
            self.assertNotEqual(line[-1], " ", f"{token}: {line!r}")

    def test_no_diagnostic_screen_draws_a_blank_second_line(self):
        """A console never draws a blank line, and now none of them does.

        Five did. GROSS VOLUME CHANGE, which is the whole point of the power
        diagnostic and drew nothing; ENTER ID TO BLOCK, which asks for an
        `ID:`; and WORKING, which Fig 6-27 draws over a row of asterisks.

        The last two were called unsettleable and both figures settle them.
        Fig 6-4, p.6-5 draws `ARE YOU SURE?: YES` over
        `PRESS <STEP> TO CONTINUE` in BOTH of its columns, matching the
        `BLOCK: YES` and `ID: XXXXXX` confirmations above it. Fig 6-13,
        p.6-12 draws `O0 E0 T0 D1` under the second `P 1: (PRODUCT LABEL)`,
        annotated "In this display you can change the current setting of
        four of the VLLD Status Indicators". FIDELITY D2.
        """
        blank = [(f["function"], sc.get("l1"))
                 for f in DIAG_MENU for sc in f["screens"]
                 if not (sc.get("l2") or "").strip() and not sc.get("live")]
        self.assertEqual(blank, [])

    def test_the_editable_indicators_sit_under_the_four_they_edit(self):
        """Fig 6-13's two indicator screens are drawn in one box column
        whose text starts at x=127, and the editable row starts at x=168 --
        twelve characters in, at the same pitch, which is exactly where `O0`
        sits in the full row above it. The indent is the figure's way of
        saying which four of the eight this screen changes."""
        rows = [l2 for l1, l2 in screens("LINE LEAK DIAG DATA")
                if "O0" in l2]
        self.assertEqual(rows, ["I0 P0 F0 S0 O0 E0 T0 D1",
                                "            O0 E0 T0 D1"])
        self.assertEqual(rows[0].index("O0"), rows[1].index("O0"))
        for row in rows:
            self.assertLessEqual(len(row), 24)

    def test_accuchart_has_the_user_status_screen(self):
        """Figure 6-10."""
        self.assertIn("ACCU USR STATUS DISABLED",
                      [l2 for _l1, l2 in screens("ACCU_CHART DIAGNOSTICS")])

    def test_communication_is_figure_6_27s_own_walk(self):
        """Figure 6-27, p.6-22, read off the page by WORD POSITION rather
        than by reading order -- the screen column sits at x=120 and the
        S/C/E step markers at x=79.

        This figure is the one an extraction got wrong once already, and
        the wrong reading reached the console: FIDELITY D4 is the only entry
        whose previous "fix" made the console worse, transposing the first
        two screens. The figure starts on the board it found the modem on
        and says what it found second, and it RETURNS to that same screen at
        the end, annotated "Displayed when modem is configured" -- which is
        only coherent if that is where the walk starts.
        """
        #
        # **And the branch is keys, not screens.** Four of the ten screens
        # this list used to hold are what CHANGE and ENTER draw -- `AUTO
        # CONFIG MODEM: YES`, and two confirmations over PRESS <STEP> TO
        # CONTINUE -- and the last was the first screen come round again, so
        # STEP walked a trainee past `ARE YOU SURE? : YES` without anything
        # having been asked. The two screens left after AUTO CONFIG MODEM
        # are stages the panel reaches by answering it. FIDELITY D27.
        self.assertEqual(screens("COMMUNICATION DIAGNOSTIC"), [
            ("COMM BOARD: 1 S-LINK", "MODEM: VR TLS GSM MODEM"),
            ("c1: MODEM AUTO DETECTED", "VR TLS GSM MODEM"),
            ("COMM BOARD: 1 S-LINK", "RSSI: XX BER: XX"),
            ("COMM BOARD: 1 S-LINK", "AUTO CONFIG MODEM: NO"),
            ("AUTO CONFIG MODEM: YES", "ARE YOU SURE? : YES"),
            ("WORKING", "* * * * * * * *"),
        ])

    def test_the_modem_it_found_is_not_the_first_thing_it_says(self):
        """The half of D4 that was "fixed" the wrong way round, kept as its
        own assertion so a future reordering has to argue with the figure."""
        first, second = screens("COMMUNICATION DIAGNOSTIC")[:2]
        self.assertEqual(first[0], "COMM BOARD: 1 S-LINK")
        self.assertEqual(second[0], "c1: MODEM AUTO DETECTED")

    def test_the_mag_sensor_branch_is_behind_its_enter(self):
        """Figure 6-28 descends from MAG SENSOR DIAGS into six readings and
        three printouts."""
        got = [l2 for _l1, l2 in screens("SMART SENSOR DIAGNOSTIC")]
        for line in ("TOTAL HT      XX.X IN.", "FUEL HT       XX.X IN.",
                     "WATER HT      XX.X IN.", "INSTALL POS   XX.X IN.",
                     "FLUID TEMP  XX.X DEG F", "BOARD TEMP  XX.X DEG F",
                     "COMM DATA PRESS <PRINT>", "CONSTANTS PRESS <PRINT>",
                     "CHNNL PRESS <PRINT>"):
            self.assertIn(line, got, line)

    def test_the_four_conditions_of_figure_6_2(self):
        """FIDELITY D10. Chapter 6 opens with the rule -- "your system will
        display only the diagnostic functions of installed and configured
        modules and options" -- and Figure 6-2 names three cases this
        console showed regardless.

        "Appears with ECPU board only. The Peripheral Controller (PC) is the
        second processor (H8) on the ECPU board"; "this is the software
        version number of the WPLLD Comm Module" and "error count between
        console and WPLLD Comm module"; and the DIM block, "DIM software
        part number and creation date".
        """
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "SYSTEM DIAGNOSTIC"][0]

        def shown(c):
            return [sc["l1"] for sc in fn["screens"]
                    if not sc.get("when") or c.visible(sc, 1)]

        c = Console()
        c.board = "E7"                              # an ECPU2
        self.assertTrue(c.has_ecpu())
        self.assertIn("PC DIAGNOSTIC DATA", shown(c))
        self.assertNotIn("DIM DIAGNOSTIC DATA", shown(c))
        self.assertNotIn("WPLLD DIAGNOSTIC DATA", shown(c))

        c.board = "C0"                              # a plain CPU
        self.assertFalse(c.has_ecpu())
        for line in ("PC DIAGNOSTIC DATA", "PC SWARE# XXXXXX-XXX-X",
                     "PC ROM CHECKSUM=PASSED", "PC ROM ERRORS = X",
                     "MC ->PC COMMS =  XXXXX"):
            self.assertNotIn(line, shown(c))

        c.modules["edim"] = 1
        c.modules["wplldcom"] = 1
        self.assertIn("DIM DIAGNOSTIC DATA", shown(c))
        self.assertIn("WPLLD DIAGNOSTIC DATA", shown(c))

    def test_a_dim_screen_is_headed_by_which_kind_of_dim_it_is(self):
        """"M for MDIMs and E for all other DIMs", Figure 6-2's own note.
        Both screens hard-coded `M1:`, so a console with an EDIM in the comm
        bay headed them with the mechanical letter."""
        c = Console()
        c.modules["edim"] = 1
        self.assertEqual(c.dim_letter(), "E")
        self.assertTrue(c.diag_reading("dim_software", 1).startswith("E1:"))
        self.assertTrue(c.diag_reading("dim_errors", 1).startswith("E1:"))
        c.modules["mdim"] = 1
        self.assertEqual(c.dim_letter(), "M")
        self.assertTrue(c.diag_reading("dim_software", 1).startswith("M1:"))
        self.assertTrue(c.diag_reading("dim_errors", 1).startswith("M1:"))

    def test_enter_descends_into_the_branch_the_panel_is_standing_on(self):
        """The defect the gates uncovered. `_diag_children` counted over the
        raw screen list and `_diag_screens` indexed a FILTERED one, so on any
        console where a screen was hidden the two disagreed -- ENTER
        descended into a different branch from the one shown, or off the end
        of the list. Nothing hid a top-level diagnostic screen until D10's
        gates, which is why it had never shown."""
        from tls350sim.ui import SimApp, MODES
        from tls350sim.console import DIAG_MENU
        c = Console()
        c.board = "C0"                              # hides the whole PC block
        c.modules["edim"] = 1
        app = SimApp(c, 10098)
        try:
            app.mode = MODES.index("DIAGNOSTIC")
            fns = app.functions()
            fi = [i for i, f in enumerate(fns)
                  if f["function"] == "SYSTEM DIAGNOSTIC"]
            self.assertTrue(fi, "no SYSTEM DIAGNOSTIC on this console")
            app.func, app.sub = fi[0], None
            offered = app._diag_offered(fns[fi[0]])
            tops = [i for i, sc in enumerate(offered) if not sc.get("depth")]
            for step in range(len(tops)):
                app.sub, app.step = None, step
                parent = app._diag_children()
                if parent is None:
                    continue
                # the index has to name the screen the panel is standing on
                self.assertIs(offered[parent], offered[tops[step]])
                app.sub = parent
                self.assertTrue(app.steps())          # and not raise
                app.sub = None
        finally:
            app.destroy()

    def test_the_atmp_branch_is_behind_its_enter(self):
        """Figure 6-32."""
        got = [l2 for _l1, l2 in screens("SMART SENSOR DIAGNOSTIC")]
        self.assertIn("ATM PRESSURE: XX.XXX PSI", got)
        self.assertIn("CHANNELS PRESS <PRINT>", got)

    def test_no_annotation_was_scraped_in_as_a_screen(self):
        """Two lines of the manual's prose were sitting in the data as
        screens; nothing on a 24 character display is sentence case."""
        for fn in DIAG_MENU:
            for l1, _l2 in screens(fn["function"]):
                self.assertFalse(l1[:1].isupper() and l1[1:2].islower()
                                 and " " in l1 and l1.endswith(("s", "f", ".")),
                                 f"{fn['function']}: {l1!r}")


class EveryLiveTokenAnswers(unittest.TestCase):
    """FIDELITY D2. A screen carrying a `live` token draws whatever that
    token answers, so a token no branch answers draws a blank line -- and a
    console never draws a blank line.

    `line_passive` was the one token with no branch anywhere: WPLLD's
    `0.10 GPH          ACTIVE` went out empty. 577013-344 Rev H's WPLLD
    diagram says what it means -- "IDLE or ACTIVE (Active means 0.1 gph test
    is scheduled)" -- and S7AC is the line's own 0.1 gph scheduling.

    The other two that answered empty do so for a reason: A07's reference
    distances are a Mag probe's and "probe types 01=CAP0 and 02=CAP1 are not
    supported by this command". Those two are the panel's fallback case
    rather than a missing branch, so they are asserted from both sides.
    """

    def a_fitted_console(self):
        from tls350sim.console import SOFTWARE_MODULES
        c = Console(None)
        for key in list(c.modules):
            c.modules[key] = 1
        c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
        c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
        # and a meter that has run, because the three Meter Events screens
        # are gated on the table having anything in it: their readers answer
        # nothing on a console with an empty table, which is the state the
        # figure draws METER EVENTS TABLE EMPTY for. FIDELITY D14.
        c.modules["edim"] = 1        # metered sales arrive through a DIM
        c.meters = {1: 1}
        c.meter_flow = {1: 100.0}
        c.tick()                     # the first tick only starts the clock
        c.clock_offset += 60.0
        c.tick()
        # and smart sensor 1 is a VACUUM sensor with a finished manual test
        # on it. Its three result screens read the last test that ran and
        # answer nothing until one has, which is D2's fallback to the
        # figure's own line rather than a missing branch -- so the fixture
        # runs one instead of the list below growing by three.
        c.values["S72301"] = "0104"
        c.vac_leak[1] = 0.123
        c.start_vac_test(1)
        c.finish_vac_tests()
        return c

    def tokens(self):
        return [(f["function"], sc["l1"], sc["live"])
                for f in DIAG_MENU for sc in f["screens"] if sc.get("live")]

    def test_every_token_but_the_mag_probes_two_answers(self):
        c = self.a_fitted_console()
        empty = sorted({(fn, tok) for fn, _l1, tok in self.tokens()
                        if not str(c.diag_value(tok, 1)).strip()})
        self.assertEqual(empty, [("IN-TANK DIAGNOSTIC", "probe_ref_curr"),
                                 ("IN-TANK DIAGNOSTIC", "probe_ref_orig")])

    def test_and_those_two_answer_on_a_mag_probe(self):
        c = self.a_fitted_console()
        c.values["S62F01"] = "011"
        for token in ("probe_ref_orig", "probe_ref_curr"):
            self.assertTrue(str(c.diag_value(token, 1)).strip(), token)

    def test_the_passive_screen_reads_its_own_scheduling(self):
        """DISABLED and MANUAL are both IDLE; only AUTO schedules one."""
        c = self.a_fitted_console()
        self.assertEqual(c.diag_value("line_passive", 1),
                         "0.10 GPH            IDLE")
        c.values["S7AC01"] = "012"
        self.assertEqual(c.diag_value("line_passive", 1),
                         "0.10 GPH          ACTIVE")
        c.values["S7AC01"] = "013"
        self.assertEqual(c.diag_value("line_passive", 1),
                         "0.10 GPH            IDLE")

    def test_the_line_it_draws_is_the_width_of_the_display(self):
        c = self.a_fitted_console()
        c.values["S7AC01"] = "012"
        self.assertEqual(len(c.diag_value("line_passive", 1)), 24)


class TheDayOfTheMonthIsSpacePadded(unittest.TestCase):
    """FIDELITY W27. The console space padded the HOUR and zero padded the
    DAY, and the manuals write both the same way: `JAN  6, 1995  8:02 AM`.

    Counted across the whole reference shelf by word position -- the text
    extractions collapse runs of spaces, so a grep says the opposite -- a
    single-digit day is written with a space 694 times against 61. The count
    is not what settles it. 576013-635 Rev AA p.100 sets six consecutive
    rows of one monospace sample, and `OCT 10,` begins one character to the
    LEFT of `OCT 9,` with the comma and the year in the same columns in
    both: the day is right aligned in two columns, which is a space.
    """

    def a_console_on_the_sixth(self):
        c = Console(None)
        want = time.strptime("1996-01-06 15:06", "%Y-%m-%d %H:%M")
        c.clock_offset = time.mktime(want) - time.time()
        return c

    def test_the_date_and_the_hour_are_written_the_same_way(self):
        when = time.strptime("1996-01-06 15:06", "%Y-%m-%d %H:%M")
        self.assertEqual(clock_date(when), "JAN  6, 1996")
        self.assertEqual(clock_date(when, year=False), "JAN  6")
        self.assertEqual(clock_words(when), "JAN  6, 1996  3:06 PM")

    def test_a_two_digit_day_is_not_padded_at_all(self):
        when = time.strptime("1996-01-22 15:06", "%Y-%m-%d %H:%M")
        self.assertEqual(clock_words(when), "JAN 22, 1996  3:06 PM")

    def test_the_status_line_and_the_paper_carry_it(self):
        c = self.a_console_on_the_sixth()
        self.assertTrue(c.clock_text().startswith("JAN  6, 1996"),
                        c.clock_text())
        printed = [str(line) for line in printer.status(c)]
        self.assertTrue(any(line.startswith("JAN  6, 1996")
                            for line in printed), printed[:6])

    def test_nothing_the_console_can_print_zero_pads_a_day(self):
        """The whole point of one helper: the fix has to reach the reports
        and the wire, not just the status line."""
        c = self.a_console_on_the_sixth()
        h = Handler(c, verbose=False)
        text = chr(10).join(str(line) for report in
                            (printer.status(c), printer.inventory(c),
                             printer.setup(c))
                            for line in report)
        for code in ("I11100", "I11200", "I20100", "I50100"):
            reply = h.handle((chr(1) + code + chr(13)).encode("latin-1"))
            text += reply.decode("latin-1")
        self.assertEqual(re.findall(r"[A-Z]{3} 0\d", text), [])
        self.assertIn("JAN  6", text)


class Operating(unittest.TestCase):
    """Operator's Manual 576013-610, chapter 2 and chapter 8."""

    def test_in_tank_inventory_walks_chapter_fours_order(self):
        """Chapter 4 walks the function section by section, each headed
        "press STEP until you see the message": VOLUME p.4-1, HEIGHT 4-2,
        WATER VOL 4-2, WATER 4-3, TEMP 4-3, ULLAGE 4-3, TC VOLUME 4-4,
        **Delivery Increase Amount** 4-4, and then DENSITY, MASS, NEXT
        DELIVERY and LAST DELIVERY on 4-5 under "Density (Optional
        Feature)". This console stepped DENSITY and MASS before DELIVERY.
        A chapter's section order is the STEP order. FIDELITY O10.
        """
        got = steps("IN-TANK INVENTORY")
        self.assertEqual(got[:10],
                         ["VOLUME", "HEIGHT", "WATER VOL", "WATER", "TEMP",
                          "ULLAGE", "TC VOLUME", "DELIVERY", "DENSITY",
                          "MASS"])

    def test_delivery_adjustment_wants_ticketed_delivery_off(self):
        """p.8-2: "For consoles with Ticketed Delivery, this feature is
        available only if ticketed delivery is disabled in the Setup Mode."
        The step carried no condition at all. FIDELITY O12."""
        c = Console(None)
        c.modules["probe"] = 1
        fn = [f for f in NORMAL_MENU
              if f["function"] == "LAST-SHIFT INVENTORY"][0]
        names = [st["text"] for st in c.visible_steps(fn, 1)]
        self.assertIn("DELIVERY ADJUSTMENT", names)
        c.values["S51C00"] = "1"
        names = [st["text"] for st in c.visible_steps(fn, 1)]
        self.assertNotIn("DELIVERY ADJUSTMENT", names)
        self.assertIn("GROSS CHANGE", names)

    def test_last_shift_inventory_is_chapter_eights_five_screens(self):
        """METERED SALES and VARIANCE are Reconciliation Mode's, not this
        function's, chapter 8 has begin, end, adjustment, gross, close.

        The names are chapter 8's own. It heads itself "Last-Shift
        Inventory" with the hyphen; its third screen is Delivery
        Adjustment, drawn `DLVY ADJUSTMENT: XXXXXX`, where this had the two
        words the other way round; and the last is `CLOSE CURRENT SHIFT`
        over `CLOSE NOW: NO`, where GROSS was contamination from the GROSS
        CHANGE step above it and "(No/Yes)" a flow-chart note. FIDELITY O2.
        """
        self.assertEqual(steps("LAST-SHIFT INVENTORY"),
                         ["BEGINNING INVENTORY", "ENDING INVENTORY",
                          "DELIVERY ADJUSTMENT", "GROSS CHANGE",
                          "CLOSE CURRENT SHIFT"])

    @unittest.skipUnless(HAVE_TK, "no display")
    def test_the_delivery_screens_say_what_they_are_asking_for(self):
        """576013-610 Rev AC p.5-1 draws `SELECT: EDIT/VIEW` under EDIT/VIEW
        OR INSERT, and p.5-3 draws the inserted delivery's Bill of Lading
        screen as `BOL:`. This drew the bare choice on the first and the
        TICKET VOLUME line belonging to the screen BEFORE it on the second,
        so the panel showed a volume where a number was being typed.
        FIDELITY O7.
        """
        from tls350sim.ui import SimApp, MODES
        from tests.test_panel import a_console
        console = a_console()
        # "Before you use this function, Ticketed Delivery must be enabled in
        # the Setup Mode", 576013-610 Rev AC p.5-1. FIDELITY O23.
        console.values["S51C00"] = "1"
        try:
            app = SimApp(console, 10087)
        except tkinter.TclError as exc:              # pragma: no cover
            # This machine's Tcl loses init.tcl every few dozen runs --
            # "couldn't read file ... init.tcl: No error" -- which is the
            # same condition the module probe above and test_panel's own
            # HAVE_TK catch, arriving late. It is the display, not the
            # console, so it skips rather than fails.
            self.skipTest(f"no display: {exc}")
        try:
            app._entered = True
            app.mode = MODES.index("NORMAL")
            for i in range(60):
                app.func = i
                fn = app.cur_function()
                if fn and fn["function"] == "DELIVERY MAINTENANCE":
                    break
            else:
                self.fail("no DELIVERY MAINTENANCE on this console")
            def walk():
                seen, step = [], 0
                app.step = 0
                for _ in range(len(fn["steps"])):
                    app._sync_device()
                    seen.append(tuple(str(x)[:24] for x in app._lines()))
                    app.k_step()
                    if app.step == 0:            # back at the selection
                        break
                return seen

            drawn = walk()
            app.sel["dlv_mode"] = "INSERT"
            inserting = walk()
        finally:
            app.destroy()
        self.assertEqual(drawn[0],
                         ("EDIT/VIEW OR INSERT", "SELECT: EDIT/VIEW"))
        # p.5-3's inserted-delivery Bill of Lading screen, which is the last
        # of the INSERT chain rather than the last of a flat list of eight
        self.assertEqual(inserting[0],
                         ("EDIT/VIEW OR INSERT", "SELECT: INSERT"))
        # and p.5-2's next screen names the tank, as p.5-1's does for
        # EDIT/VIEW. FIDELITY U7.
        self.assertEqual(drawn[1][0], "SELECT: EDIT/VIEW")
        self.assertEqual(inserting[1][0], "SELECT: INSERT", inserting[1])
        self.assertTrue(inserting[1][1].startswith("T 1:"), inserting[1])
        # p.5-3 heads it with the tank and the delivery's date and time
        self.assertTrue(inserting[-1][0].startswith("T "), inserting[-1])
        self.assertTrue(inserting[-1][1].startswith("BOL:"), inserting[-1])

        # FIDELITY O7. "Press CHANGE to choose INSERT, then press ENTER for
        # your change to be accepted", and each answer has its own chain
        # after it: editing walks the delivery that is there, inserting
        # builds one that is not. This console offered one list of eight
        # steps and walked all of them whichever was selected, so choosing
        # INSERT and pressing STEP landed on the EDIT chain's TICKET VOLUME.
        first = [line for pair in drawn for line in pair]
        second = [line for pair in inserting for line in pair]
        self.assertIn("TICKET VOLUME", first)
        # and neither chain draws p.5-2's "Press STEP to view the previous
        # delivery for this tank" as a screen of its own: STEP off a BOL is
        # the older delivery's ticket screen. FIDELITY O7.
        self.assertNotIn("PRIOR DLVY FOR TANK", first + second)
        self.assertIn("ENTER DELIVERY DATE", second)
        self.assertIn("ENTER DELIVERY TIME", second)
        self.assertNotIn("ENTER DELIVERY DATE", first)
        self.assertNotIn("ENTER TICKET VOLUME", first)

    def test_the_relay_test_has_chapter_twenty_two_s_three_screens(self):
        """576013-610 Rev AC p.22-1 draws three and this console drew one,
        with its two lines the wrong way round and an `R 1:` prefix on the
        entry screen, which names no relay yet:

            TEST OUTPUT RELAYS / ENTER RELAY NUMBER #
            R 1: OVERFILL ALARM / PUSH ALARM/TEST KEY
            R 1: (Device Name)  / ON - PRESS ANY KEY

        The two screens the function exists for were the missing ones.
        FIDELITY O6.
        """
        got = steps("TEST OUTPUT RELAYS")
        self.assertEqual(got, ["ENTER RELAY NUMBER #",
                               "PUSH ALARM/TEST KEY",
                               "ON - PRESS ANY KEY"])

    def test_an_inserted_delivery_can_be_given_a_bol(self):
        """p2-2: INSERT DLVY BY TANK, DATE, TIME, TICKET VOLUME, BOL, and
        p.5-2 draws the two entry screens with the word spelled out --
        "ENTER DELIVERY DATE" over "DATE: XX/XX/XXXX". FIDELITY O7."""
        got = steps("DELIVERY MAINTENANCE")
        self.assertEqual(got[-3:], ["ENTER DELIVERY TIME",
                                    "ENTER TICKET VOLUME", "BOL"])


class ARealToolCanBackItUpAndPutItBack(unittest.TestCase):
    """The round trip a Veeder-Root tool actually does.

    A tool dumps every setup function with Inquire, keeps the data field, and
    writes it back with Set. Both halves have to agree or a backup is not a
    backup: verified end to end against the real tool, and pinned here.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("ascii", "replace")

    def data_of(self, reply, code):
        """The data field, the way a tool takes it: past the code and stamp."""
        body = reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).replace("\r\n", "\n")
        parts = [p for p in body.split("\n") if p]
        self.assertTrue(parts and parts[0] == code, reply)
        return parts[-1] if len(parts) > 1 else ""

    def test_every_stored_value_survives_a_dump_and_a_restore(self):
        c, h = self.a_site()
        before = dict(c.values)
        dumped = {}
        for code in sorted(before):
            token, device = code[1:4], code[4:6]
            reply = self.send(h, f"i{token}{device}")
            self.assertNotIn("9999", reply, code)
            field = reply.split(chr(1))[-1].split("&&")[0][6 + 10:]
            dumped[code] = field
        # now scramble it, the way a badly programmed console is scrambled
        for code in list(c.values):
            c.values[code] = "00"
        # ...and write the dump back, stripping the device prefix exactly as
        # a tool does
        for code, field in dumped.items():
            token, device = code[1:4], code[4:6]
            payload = field
            if c.is_prefixed(token) and payload[:2] == device:
                payload = payload[2:]
            reply = self.send(h, f"s{token}{device}{payload}")
            self.assertNotIn("9999", reply, code)
        self.assertEqual(c.values, before)

    def test_a_value_that_starts_with_its_own_device_number_survives(self):
        """S785 on line 1 is tank 01, so the stored value IS `0101`.

        Strip the prefix off that and you get `01`, which is what a bare
        value looks like too: the console cannot guess, so it always puts the
        prefix back and the two rules are exact inverses.
        """
        c, h = self.a_site()
        self.send(h, "s7850101")
        self.assertEqual(c.values["S78501"], "0101")

    def test_a_display_format_set_stores_what_a_computer_set_would(self):
        """"Display: <SOH>S60901c.cccccc" and "Computer: <SOH>s60901FFFFFFFF"
        are the same setting written two ways."""
        c, h = self.a_site()
        self.send(h, "S609010.000700")
        packed = c.values["S60901"]
        self.send(h, "s609023A378034")
        self.assertEqual(packed[2:], c.values["S60902"][2:])
        self.assertAlmostEqual(c.limit("609", 1), 0.0007, places=6)

    def test_a_display_format_set_refuses_a_value_out_of_range(self):
        c, h = self.a_site()
        self.assertIn("9999", self.send(h, "S609019.9"))

    def test_an_unknown_function_code_is_9999_and_never_silence(self):
        """A tool sweeps whole ranges, gaps included, and reads silence as a
        console that has fallen over: "12 consecutive timeouts" aborts it."""
        c, h = self.a_site()
        for token in ("5C0", "63E", "7CA", "8A1", "ZZZ"):
            reply = self.send(h, f"i{token}00")
            self.assertIn("9999", reply, token)


class InTankDiagnostics(unittest.TestCase):
    """Serial Interface Manual 576013-635, section 7.4.2.

    A01 is the first of the in-tank diagnostic reports and the one that says
    what the probe IS rather than what it is reading. Its computer format is
    "TTpPPKKKKFFFFFFFFSSSSSScccc" once per tank, and the point of these tests
    is that the two formats agree with each other and with the console.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def test_the_reference_distance_screens_carry_a_distance(self):
        """576013-818 Fig 6-6 draws ORIG REF DISTANCE and CURR REF DISTANCE
        as `MM/DD/YY            XX.XX` -- "Original reference distance
        reading (in inches or mm) recorded at date/time or serial number
        change". The panel drew the date alone while the same pair went out
        over A07, and comparing the two distances is how a probe swap or a
        shifted riser gets caught. FIDELITY D7.
        """
        c, _h = self.a_site()
        pair = c.probe_reference_distance(1)
        self.assertIsNotNone(pair)
        for token, (born, inches) in (("probe_ref_orig", pair[0]),
                                      ("probe_ref_curr", pair[1])):
            line = c.diag_reading(token, 1)
            self.assertEqual(len(line), 24, repr(line))
            self.assertTrue(line.startswith(
                f"{born[2:4]}/{born[4:6]}/{born[0:2]}"), repr(line))
            self.assertTrue(line.rstrip().endswith(f"{inches:.2f}"),
                            repr(line))
        # the two differ, which is the whole point of showing both
        self.assertNotEqual(c.diag_reading("probe_ref_orig", 1),
                            c.diag_reading("probe_ref_curr", 1))

    def records(self, reply):
        """The repeated 27 character block, past the code and the stamp."""
        body = reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0]
        body = body[len("iA0100") + len("YYMMDDHHmm"):]
        self.assertEqual(len(body) % 27, 0, body)
        return [body[i:i + 27] for i in range(0, len(body), 27)]

    def test_the_computer_format_says_what_the_console_says(self):
        from tls350sim import packed
        c, h = self.a_site()
        got = self.records(self.send(h, "iA0100"))
        self.assertEqual(len(got), len(sorted(c.tank_level)))
        for rec in got:
            tank = int(rec[0:2])
            self.assertEqual(rec[2], (c.text("603", tank) or " ")[:1])
            self.assertEqual(rec[3:5], c.probe_type_code(tank))
            self.assertEqual(rec[5:9], c.probe_circuit_code(tank))
            self.assertAlmostEqual(packed.unhexfloat(rec[9:17]),
                                   c.probe_length(tank), places=3)
            self.assertEqual(rec[17:23], c.probe_serial(tank))
            self.assertEqual(rec[23:27], c.probe_date_code(tank))

    def test_the_display_format_prints_the_manuals_own_columns(self):
        """"TYPE CODE LENGTH SERIAL NO. D/CODE", and a line for each tank."""
        c, h = self.a_site()
        reply = self.send(h, "IA0100")
        for column in ("TYPE", "CODE", "LENGTH", "SERIAL NO.", "D/CODE"):
            self.assertIn(column, reply)
        for tank in sorted(c.tank_level):
            self.assertIn(c.probe_serial(tank), reply)
            self.assertIn(c.probe_date_code(tank), reply)

    def test_one_tank_answers_for_that_tank_alone(self):
        """"TT - Tank Number (Decimal, 00=all)"."""
        c, h = self.a_site()
        got = self.records(self.send(h, "iA0102"))
        self.assertEqual(len(got), 1)
        self.assertEqual(int(got[0][0:2]), 2)
        self.assertEqual(got[0][17:23], c.probe_serial(2))

    def test_a_console_with_no_probe_card_answers_9999(self):
        """An in-tank diagnostic reads a probe, so it wants the card that
        drives one."""
        c, h = self.a_site()
        c.modules["probe"] = 0
        self.assertIn("9999", self.send(h, "iA0100"))

    def test_a_probe_keeps_its_identity_between_looks(self):
        """A serial number that changes when you glance away is not a serial
        number: readings.py's rule, and A01 is where a tool would notice."""
        c, h = self.a_site()
        first = self.records(self.send(h, "iA0100"))
        c.tick()
        self.assertEqual(first, self.records(self.send(h, "iA0100")))


class TheProbeCalibrationReports(unittest.TestCase):
    """576013-635 section 7.4.2, function codes A02 to A07.

    A02 to A06 share one computer format, "TTpPPNNFFFFFFFF", and differ in
    which numbers they carry. A07 is a different shape and Mag probes only.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.values["S62F03"] = ""      # no float size: tank 3 reads as a CAP0
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def fields(self, reply, code):
        """TT p PP NN and the floats, the way a tool takes them apart."""
        from tls350sim import packed
        body = reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0][len(code) + 10:]
        count = int(body[5:7], 16)
        return (body[0:2], body[2], body[3:5],
                [packed.unhexfloat(body[7 + i * 8:15 + i * 8])
                 for i in range(count)])

    def test_a_mag_probe_answers_a02_with_its_gradient_and_nothing_else(self):
        """"MAG GRADIENT= 178.1400" is the whole of that tank's line."""
        c, h = self.a_site()
        _tt, _p, pp, values = self.fields(self.send(h, "iA0201"), "iA0201")
        self.assertEqual(pp, "03")
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values[0], c.probe_gradient(1), places=2)
        self.assertIn("GRADIENT=", self.send(h, "IA0201"))

    def test_a_cap_probe_answers_two_references_and_a_segment_each(self):
        """A CAP0's example is eight numbers: 97 and 180, then six."""
        c, h = self.a_site()
        _tt, _p, pp, values = self.fields(self.send(h, "iA0203"), "iA0203")
        self.assertEqual(pp, "01")
        self.assertEqual(len(values), 2 + Console.CAP_SEGMENTS["CAP0"])
        self.assertEqual([round(v, 3) for v in values],
                         [round(v, 3) for v in c.probe_calibration(3)])

    def test_the_wets_read_higher_than_the_drys(self):
        """Which is what wetting a capacitance segment does to it, and what
        makes the difference between them a sensitivity."""
        c, _h = self.a_site()
        dry = c.probe_calibration(3, wet=False)
        wet = c.probe_calibration(3, wet=True)
        for d, w in zip(dry, wet):
            self.assertGreater(w, d)

    def test_a_mag_probe_has_no_updated_calibration_and_no_ratios(self):
        """A04, A05 and A06 print "TANK 1 REGULAR UNLEADED MAG" and stop."""
        c, h = self.a_site()
        for code in ("A04", "A05", "A06"):
            _tt, _p, _pp, values = self.fields(self.send(h, f"i{code}01"),
                                               f"i{code}01")
            self.assertEqual(values, [], code)
            shown = self.send(h, f"I{code}01")
            self.assertIn("MAG", shown)
            for title in ("UPDATED", "SENSITIVITY"):
                self.assertNotIn(title, shown, code)

    def test_the_updated_values_are_the_factory_ones_with_drift_on_them(self):
        """The manual's updated drys are its factory drys and one of its
        updated wets differs by a few counts: a probe recalibrated once."""
        c, _h = self.a_site()
        factory = c.probe_calibration(3, wet=False, updated=False)
        updated = c.probe_calibration(3, wet=False, updated=True)
        self.assertEqual(len(factory), len(updated))
        self.assertNotEqual(factory, updated)
        for f, u in zip(factory, updated):
            self.assertLess(abs(u - f), 5.0)

    def test_the_ratios_start_at_zero_and_sit_about_one(self):
        """Every example in the manual starts 0.000, and a probe whose
        positions all answer as they were built to reads about 1.000 across
        EVERY one of them.

        This used to skip position 1, because the old normalisation put a
        permanent outlier there -- the reference positions' spans are a
        quarter of a segment's, so dividing by the mean segment span left
        one reading 0.26 on every probe for ever. CLOSED X12.
        """
        c, h = self.a_site()
        ratios = c.probe_ratios(3)
        self.assertEqual(ratios[0], 0.0)
        self.assertEqual(len(ratios), len(c.probe_calibration(3)))
        for r in ratios[1:]:
            self.assertTrue(0.8 < r < 1.2, r)
        self.assertIn("SENSITIVITY RATIOS", self.send(h, "IA0603"))

    def test_no_position_stands_out_on_a_probe_that_is_not_faulty(self):
        """CLOSED X12. The screen exists to make ONE position stand out --
        CAP0's example is `0.000 1.023 0.279 0.971 1.010 1.003 1.010 0.988`
        and 0.279 is a segment answering at a quarter of what it was built
        to. A console that shows an outlier on every healthy probe has spent
        the screen before a technician gets to it.

        Both probe types, because the fault was in the reference positions
        and both types have two of them."""
        c, _h = self.a_site()
        for tank in range(1, 5):
            if c.probe_type(tank) == "MAG PROBE":
                continue
            ratios = c.probe_ratios(tank)
            far = [r for r in ratios[1:] if abs(r - 1.0) > 0.10]
            self.assertEqual(far, [], f"tank {tank}: {ratios}")

    def test_the_ratio_is_of_wet_constants_and_not_of_spans(self):
        """US 4,349,882, Veeder Industries' own predecessor: the
        microcomputer "calculates and stores 'wet' constant ratios" and uses
        one to carry a recalculated wet constant up to the interface
        segment. A wet constant, not a wet-minus-dry span."""
        c, _h = self.a_site()
        factory = c.probe_calibration(3, wet=True, updated=False)
        now = c.probe_calibration(3, wet=True, updated=True)
        want = [0.0] + [n / f for n, f in zip(now[1:], factory[1:])]
        for got, wanted in zip(c.probe_ratios(3), want):
            self.assertAlmostEqual(got, wanted, places=9)

    def test_a07_is_a_mag_command_and_says_so_to_a_cap(self):
        """"Probe types 01=CAP0 and 02=CAP1 are not supported by this
        command"."""
        c, h = self.a_site()
        self.assertIn("9999", self.send(h, "iA0703"))
        self.assertNotIn("9999", self.send(h, "iA0701"))

    def test_a07_carries_two_dated_readings_of_the_same_distance(self):
        """The point of the screen is the pair: what it read going in against
        what it reads now."""
        from tls350sim import packed
        c, h = self.a_site()
        code = "iA0701"
        body = self.send(h, code).strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0]
        body = body[len(code) + 10:]
        self.assertEqual(body[0:2], "01")
        self.assertEqual(body[3:5], "03")
        first, second = body[5:19], body[19:33]
        (d1, v1), (d2, v2) = c.probe_reference_distance(1)
        self.assertEqual(first[:6], d1)
        self.assertEqual(second[:6], d2)
        self.assertAlmostEqual(packed.unhexfloat(first[6:]), v1, places=2)
        self.assertAlmostEqual(packed.unhexfloat(second[6:]), v2, places=2)
        self.assertLess(abs(v2 - v1), 1.0, "a probe that has not moved")

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A02", "A03", "A04", "A05", "A06", "A07"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)


class ARestoreThatCarriesTheDevicePrefix(unittest.TestCase):
    """What a real console backup actually looks like coming back.

    Found against a dump off a live console. A tool that backs a console up
    stores what the INQUIRE gave it, and for a device-prefixed function that
    answer leads with the two digit device number. Writing it back verbatim
    therefore sends the prefix too -- and the Set path used to add a second
    one, so a tank came back labelled "01REGULAR" where the console says
    "REGULAR", and a line "03DIESEL  LINE" where it says "DIESEL  LINE".
    """

    def a_console(self):
        c = Console()
        for card in ("probe", "plld", "rs232", "liquid"):
            c.modules[card] = 4
        return c, Handler(c, verbose=False)

    def test_a_label_written_back_with_its_prefix_keeps_its_text(self):
        c, h = self.a_console()
        h.set_("602", "03", "03DIESEL              ", "s60203")
        self.assertEqual(c.text("602", 3), "DIESEL")

    def test_a_label_written_back_without_one_still_works(self):
        """The manual's own Set format has no prefix, "<SOH>S616TTf"."""
        c, h = self.a_console()
        h.set_("602", "03", "DIESEL              ", "s60203")
        self.assertEqual(c.text("602", 3), "DIESEL")

    def test_a_one_character_product_code_is_not_eaten(self):
        """maxlen 1, so "013" is two too many and "3" is exactly right."""
        c, h = self.a_console()
        h.set_("603", "01", "013", "s60301")
        self.assertEqual(c.text("603", 1), "3")
        h.set_("603", "02", "1", "s60302")
        self.assertEqual(c.text("603", 2), "1")

    def test_the_ambiguous_one_is_decided_by_length_not_by_content(self):
        """S785 holds a two digit tank number, so on line 1 both the prefix
        and the value can read "01". Four characters is prefix plus value;
        two is the value. That is the whole distinction and it is enough.
        """
        c, h = self.a_console()
        h.set_("785", "01", "0101", "s78501")     # prefix 01, tank 01
        h.set_("785", "02", "02", "s78502")       # no prefix, tank 02
        # both end up stored the one way the console reads them back
        self.assertEqual(c.values.get("S78501"), "0101")
        self.assertEqual(c.values.get("S78502"), "0202")

    def test_a_packed_float_is_left_alone(self):
        """Eight characters is a float and ten is a float with a prefix."""
        c, h = self.a_console()
        h.set_("789", "01", "01433E0000", "s78901")
        self.assertAlmostEqual(c.limit("789", 1), 190.0, places=3)
        h.set_("789", "02", "43160000", "s78902")
        self.assertAlmostEqual(c.limit("789", 2), 150.0, places=3)

    def test_it_still_round_trips_what_it_answers(self):
        """The property the prefix exists for: dump, restore, dump again."""
        c, h = self.a_console()
        h.set_("602", "01", "REGULAR             ", "s60201")
        first = h.handle((chr(1) + "i60201" + chr(13)).encode()).decode("latin-1")
        field = first.split(chr(1))[-1].split("&&")[0][6 + 10:]
        h.set_("602", "01", field, "s60201")
        again = h.handle((chr(1) + "i60201" + chr(13)).encode()).decode("latin-1")
        self.assertEqual(first.split("&&")[0][16:], again.split("&&")[0][16:])
        self.assertEqual(c.text("602", 1), "REGULAR")


class TheProbeSampleBuffers(unittest.TestCase):
    """576013-635 7.4.2, A10 to A13: the same channels through four windows.

    "TTpPPSSSSNNFFFFFFFF", where SSSS is the running sample number on A10 and
    A13 and the width of the average on A11 and A12.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        for _ in range(3):                # let the sample counter get going
            c.clock_offset += 3600.0
            c.tick()
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def fields(self, reply, code):
        """TT p PP SSSS NN and the floats."""
        from tls350sim import packed
        body = reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0][len(code) + 10:]
        window = int(body[5:9], 16)
        count = int(body[9:11], 16)
        return window, [packed.unhexfloat(body[11 + i * 8:19 + i * 8])
                        for i in range(count)]

    def test_a_mag_probe_answers_nineteen_channels(self):
        """Table 9-3's own list, and what probe_channel already models: the
        water float, ten reads of the product float, two references and six
        thermistors. A10's example prints exactly nineteen."""
        c, h = self.a_site()
        for code in ("A10", "A11", "A12", "A13"):
            _w, values = self.fields(self.send(h, f"i{code}01"), f"i{code}01")
            self.assertEqual(len(values), 19, code)

    def test_the_standard_average_is_the_number_the_guide_gave(self):
        """"Under normal operating conditions, this number should read 20",
        and the manual's example agrees: 20 on a Mag, 40 on a CAP."""
        c, h = self.a_site()
        window, _v = self.fields(self.send(h, "iA1201"), "iA1201")
        self.assertEqual(window, 20)
        c.values["S62F03"] = ""                    # tank 3 becomes a CAP0
        window, _v = self.fields(self.send(h, "iA1203"), "iA1203")
        self.assertEqual(window, 40)

    def test_the_fast_average_is_five(self):
        c, h = self.a_site()
        window, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        self.assertEqual(window, 5)

    def test_the_sample_number_runs_and_the_windows_do_not(self):
        """A10 and A13 report a counter; A11 and A12 report a width."""
        c, h = self.a_site()
        first, _v = self.fields(self.send(h, "iA1001"), "iA1001")
        fixed, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        c.clock_offset += 3600.0
        c.tick()
        later, _v = self.fields(self.send(h, "iA1001"), "iA1001")
        again, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        self.assertGreater(later, first, "the sample number should run on")
        self.assertEqual(again, fixed, "the averaging width should not")

    def test_a_wider_average_is_a_quieter_reading(self):
        """Which is the whole difference between these four reports: the
        manual prints 8587.000, then 8587.200, then 8587.450 on one channel."""
        c, _h = self.a_site()
        one = c.probe_buffer(1, 1)
        five = c.probe_buffer(1, 5)
        twenty = c.probe_buffer(1, 20)
        settled = c.probe_buffer(1, 10 ** 6)
        for n in range(19):
            near = abs(twenty[n] - settled[n])
            far = abs(one[n] - settled[n])
            self.assertLessEqual(near, far + 1e-9, f"channel {n}")
            self.assertLessEqual(abs(five[n] - settled[n]) + 1e-9, far + 1e-9)

    def test_the_long_term_buffer_lags_only_what_moves(self):
        """A13's example reads 695.555 against a live 694 on the water float
        and 38259 against 38250 on a temperature reference -- all but the
        same -- while its product float reads 9687 against a live 8587. A long
        term average lags the level and matches what does not change.
        """
        c, _h = self.a_site()
        live = c.probe_buffer(1, 1)
        longterm = c.probe_buffer(1, c.probe_sample_number(1), longterm=True)
        self.assertAlmostEqual(longterm[0], live[0], delta=abs(live[0]) * 0.01)
        for n in (11, 12, 17, 18):
            self.assertAlmostEqual(longterm[n], live[n],
                                   delta=abs(live[n]) * 0.01)
        moved = [n for n in range(1, 11)
                 if abs(longterm[n] - live[n]) > abs(live[n]) * 0.02]
        self.assertEqual(len(moved), 10, "every product channel should lag")

    def test_the_display_format_heads_each_tank_with_its_count(self):
        c, h = self.a_site()
        shown = self.send(h, "IA1001")
        self.assertIn("NUMBER OF SAMPLES=", shown)
        self.assertIn("MAG", shown)

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A10", "A11", "A12", "A13"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)


class TheRestOfTheProbeBlock(unittest.TestCase):
    """576013-635 7.4.2, A14, A15 and A20 to A23 -- what closes the section."""

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.tick()
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def body(self, reply, code):
        return reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0][len(code) + 10:]

    # ---- A14 ----------------------------------------------------------------
    def test_a14_is_one_flag_wide(self):
        """"TTNNL": tank, the number of option flags, and the flag."""
        c, h = self.a_site()
        got = self.body(self.send(h, "iA1401"), "iA1401")
        self.assertEqual(got, "01010")          # tank 01, one flag, NO
        self.assertIn("MAG PROBE OPTIONS TABLE", self.send(h, "IA1401"))

    def test_a14_answers_no_because_nothing_says_otherwise(self):
        """A low temperature probe is a special order and nothing in the setup
        data names one. A14's own example answers NO on all four tanks."""
        c, _h = self.a_site()
        self.assertFalse(c.probe_low_temp(1))

    # ---- A20 to A22 ---------------------------------------------------------
    def test_a_healthy_probe_sets_no_leak_test_flags(self):
        """Every example in all three reports prints the heading and nothing
        after it."""
        c, h = self.a_site()
        for code in ("A20", "A21", "A22"):
            got = self.body(self.send(h, f"i{code}01"), f"i{code}01")
            self.assertEqual(got[5:7], "00", f"{code} should set no flags")

    def test_the_headings_follow_the_probe_type(self):
        """A20 heads a Mag with both rates and a CAP0 with 0.2 alone."""
        c, h = self.a_site()
        mag = self.send(h, "IA2001")
        self.assertIn("0.1 GAL/HR FLAGS:", mag)
        self.assertIn("0.2 GAL/HR FLAGS:", mag)
        c.values["S62F03"] = ""                  # tank 3 becomes a CAP0
        cap = self.send(h, "IA2003")
        self.assertIn("0.2 GAL/HR FLAGS:", cap)
        self.assertNotIn("0.1 GAL/HR FLAGS:", cap)

    def test_a22_is_one_set_of_flags_not_two(self):
        c, h = self.a_site()
        self.assertIn("GROSS LEAK TEST FLAGS:", self.send(h, "IA2201"))
        self.assertNotIn("GAL/HR FLAGS:", self.send(h, "IA2201"))

    # ---- A23 ----------------------------------------------------------------
    def test_the_averaging_buffers_are_the_history_this_console_keeps(self):
        """The console already keeps every finished test for the history
        reports, so A23 is that log split by rate and cut to the buffer."""
        c, h = self.a_site()
        for _ in range(3):
            for key in ("periodic", "annual"):
                c.leaks.start("tank", 1, key, hours=2.0)
                for _ in range(400):
                    c.clock_offset += 60.0
                    c.tick()
                    if not c.leaks.active("tank", 1):
                        break
        got = c.probe_leak_buffer(1, "periodic")
        self.assertEqual(len(got), 3)
        self.assertGreater(got[0].started, got[-1].started, "newest first")
        shown = self.send(h, "IA2301")
        self.assertIn("0.20 GAL/HR LEAK TEST BUFFER", shown)
        self.assertIn("0.10 GAL/HR LEAK TEST BUFFER", shown)
        self.assertIn("AVERAGE", shown)

    def test_the_buffer_holds_five_at_most(self):
        """The manual's example shows five rows under 0.20."""
        c, _h = self.a_site()
        for _ in range(7):
            c.leaks.start("tank", 1, "periodic", hours=2.0)
            for _ in range(400):
                c.clock_offset += 60.0
                c.tick()
                if not c.leaks.active("tank", 1):
                    break
        self.assertEqual(len(c.probe_leak_buffer(1, "periodic")), 5)

    # ---- A15 ----------------------------------------------------------------
    def test_a15_is_every_other_report_on_one_sheet(self):
        """Nothing new is modelled in it, so every figure on it has to be the
        same figure the report it came from answers."""
        c, h = self.a_site()
        shown = self.send(h, "IA1501")
        self.assertIn(c.probe_serial(1), shown)                   # A01
        self.assertIn(c.probe_date_code(1), shown)                # A01
        self.assertIn(c.probe_circuit_code(1), shown)             # A01
        # the gradient WANDERS on the console clock, so comparing the printed
        # figure to a freshly computed one races the tick: read it back out
        # and check it is the same reading rather than the same string
        printed = [l for l in shown.split(chr(13) + chr(10))
                   if l.startswith("GRADIENT=")][0]
        self.assertAlmostEqual(float(printed.split("=")[1]),
                               c.probe_gradient(1), delta=0.5)      # A02
        # the four counters are held right against their columns, which is
        # what 576013-635 Rev AA p.505 draws -- `NUM SAMPLES=  20`, two
        # spaces. See FIDELITY S5.
        self.assertIn(f"NUM SAMPLES={c.probe_window(1, 'standard'):4d}",
                      shown)
        for line in ("IN-TANK DIAGNOSTIC", "PROBE DIAGNOSTICS",
                     "TEMP SENSOR DATA", "REF DISTANCE",
                     "SAMPLES READ=", "LAST ERROR  ="):
            self.assertIn(line, shown)

    def test_a15_prints_all_nineteen_channels_and_six_temperatures(self):
        c, h = self.a_site()
        shown = self.send(h, "IA1501")
        for n in range(19):
            self.assertIn(f"C{n:02d} ", shown, f"channel {n}")
        for n in range(1, 7):
            self.assertIn(f"T{n}: ", shown, f"temp {n}")

    def test_a15_spells_the_type_the_way_a15_spells_it(self):
        """"PROBE TYPE MAG 1" here, "MAG" in A01's column, "MAG7" in A07's
        heading. Three reports, three spellings, each followed where printed."""
        c, h = self.a_site()
        self.assertIn("PROBE TYPE MAG 1", self.send(h, "IA1501"))
        self.assertIn("MAG ", self.send(h, "IA0101"))

    def test_a15_computer_format_leads_with_the_probes_identity(self):
        from tls350sim import packed
        c, h = self.a_site()
        got = self.body(self.send(h, "iA1501"), "iA1501")
        self.assertEqual(got[0:2], "01")                    # TT
        self.assertEqual(got[2:6], "0003")                  # pppp, MAG1
        self.assertEqual(got[6:12], c.probe_serial(1))      # ssssss
        self.assertAlmostEqual(packed.unhexfloat(got[12:20]),
                               c.probe_length(1), places=3)
        self.assertEqual(got[20:24], c.probe_date_code(1))  # dddd

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A14", "A15", "A20", "A21", "A22", "A23"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)


class TheDeliveryReportsOwnColumns(unittest.TestCase):
    """576013-635 Rev AA prints these three with a sample apiece, in Courier
    at six points a character, so the columns can be counted off the rendered
    page rather than guessed. I202 (p.63) and I215 (p.83) do NOT share them,
    which is the trap this family sets twice.

    I202 ran its whole header onto the end of the `T n:` line and dropped
    `INCREASE   DATE / TIME` altogether -- so the headings rode on a
    variable-length product label and moved from tank to tank. I221 printed
    four columns of its own with all three temperatures missing and a BOL
    column that belongs to 222 in their place. FIDELITY S3 and S4.
    """

    def a_delivery(self):
        from tls350sim import presets
        c = Console(None)
        presets.load(c, "Two-tank retail site")
        c.modules["rs232"] = 1
        c.values["S61001"] = "0101"
        c.tick()
        c.tank_level[1]["volume"] += 3000.0
        c.tick()
        c.clock_offset += 6 * 60.0
        c.tick()
        return c

    def rows(self, c, code):
        out = Handler(c, verbose=False).handle(chr(1).encode()
                                                  + code).decode("latin-1")
        return out.replace(chr(13) + chr(10), chr(10)).split(chr(10))

    def test_i202_heads_its_own_line_in_the_sample_s_columns(self):
        c = self.a_delivery()
        rows = self.rows(c, b"I20201")
        head = [r for r in rows if r.startswith("INCREASE")][0]
        # the T n: line carries the label and nothing else
        tank = [r for r in rows if r.startswith("T 1:")][0]
        self.assertEqual(tank, "T 1:REGULAR UNLEADED")
        for word, col in (("INCREASE", 0), ("DATE", 11), ("TIME", 18),
                          ("GALLONS", 35), ("WATER", 54), ("HEIGHT", 73)):
            self.assertEqual(head.index(word), col, word)
        self.assertEqual(head.index("TC GALLONS"), 43)

    def test_and_its_rows_land_under_them(self):
        """The label right-aligns to 9 and the stamp starts at 11, on every
        row -- END:, START: and AMOUNT: all end in the same column."""
        c = self.a_delivery()
        rows = self.rows(c, b"I20201")
        for label in ("END:", "START:", "AMOUNT:"):
            row = [r for r in rows if r.strip().startswith(label)][0]
            self.assertEqual(row.index(label) + len(label), 10, label)
        end = [r for r in rows if r.strip().startswith("END:")][0]
        # Where the STAMP starts, which is what this test is about. It used
        # to probe the column with `end.index("SEP")`, so eleven months of
        # the year it raised `ValueError: substring not found` from inside
        # `index` -- not an assertion failure, and a message naming neither
        # the column nor the month. See FIDELITY V9.
        # The whole stamp shape, not a month name: the day is SPACE PADDED
        # (`APR  6, 2027`, W27's rule), so anything narrower than this misses
        # every single-digit day as well as every month but one.
        stamp = re.search(r"[A-Z]{3}\s+\d{1,2}, \d{4}", end)
        self.assertIsNotNone(stamp, end)
        self.assertEqual(stamp.start(), 11, end)

    def test_i215_keeps_its_own_columns_and_they_are_not_i202_s(self):
        c = self.a_delivery()
        head = [r for r in self.rows(c, b"I21501")
                if r.startswith("INCREASE")][0]
        for word, col in (("GALLONS", 33), ("MASS", 45), ("DENSITY", 52),
                          ("WATER", 60), ("TEMP", 68), ("HEIGHT", 74)):
            self.assertEqual(head.index(word), col, word)

    def test_i221_prints_all_six_of_the_manual_s_columns(self):
        c = self.a_delivery()
        c.deliveries.last(1).ticket = 3010.0
        rows = self.rows(c, b"I22101")
        # not "TICKET": the report's own title contains the word
        first = [r for r in rows if "EST DLVY" in r][0]
        second = rows[rows.index(first) + 1]
        for word, col in (("TICKET", 24), ("GAUGE", 33), ("DLVY", 45),
                          ("BEFORE", 52), ("AFTER", 60), ("EST DLVY", 67)):
            self.assertEqual(first.index(word), col, word)
        self.assertEqual(second.index("DELIVERY END DATE"), 0)
        self.assertEqual([i for i in range(len(second))
                          if second.startswith("TMP", i)], [53, 61, 69])

    def test_and_i221_has_no_bol_column(self):
        """It belongs to 222, whose Function Type is Bill of Lading Report
        and whose sample heads a NUMBER column under BOL."""
        c = self.a_delivery()
        c.deliveries.last(1).bol = "EXX23223"
        rows = self.rows(c, b"I22101")
        self.assertNotIn("BOL", chr(10).join(rows))

    def test_the_delivered_temperature_is_the_mixing_arithmetic(self):
        """EST DLVY TMP: the tank held `before` gallons at the before
        temperature and holds `after` at the after temperature, so the
        difference came in at whatever makes those two balance. The manual's
        three rows reproduce under it with opening volumes of 3445, 3374 and
        4299 gallons on a tank taking five and six thousand at a time."""
        c = self.a_delivery()
        record = c.deliveries.last(1)
        record.start["temp"], record.end["temp"] = 44.8, 42.4
        record.start["volume"] = 3444.6
        record.end["volume"] = 3444.6 + record.amount
        got = c.deliveries.delivered_temperature(record)
        want = ((record.end["volume"] * 42.4 - 3444.6 * 44.8)
                / record.amount)
        self.assertAlmostEqual(got, want, places=6)


class EveryDisplayReplyLeavesTheBlankLine(unittest.TestCase):
    """576013-635 draws a blank line between a display reply's header block
    -- the echoed code, the stamp, and the four station header lines where a
    report carries them -- and the body underneath.

    **How MANY is a property of the code, not a constant.** This class read
    "of 321 samples ... 304 leave a full line's gap and nine do not", and
    asserted the gap on every report anyway. Re-measured off the word boxes
    rather than the text, over every sample rather than the ones with a
    header: 512 of 541 leave one and **29 leave none** -- 613, 614, 902, 903,
    905, 881 and the whole AccuChart `A` family among them. A real console
    agrees with the page on every one of those that was captured, and this
    console left a blank line on all of them.

    So the number comes from `wiretitles.json`'s `head_gap` now, measured by
    `tools/build_wire_titles.py`, and this asserts the console draws what the
    page draws rather than that it always draws one.

    It looked as though the blank was already there, which is why it took a
    measurement to see. A site with three programmed station header lines
    sends an EMPTY fourth, and an empty fourth reads exactly like the missing
    blank; programme all four and the body ran straight onto the header.
    FIDELITY S6.
    """

    STAMP = re.compile(r"^[A-Z]{3} +\d{1,2}, \d{4} +\d{1,2}:\d\d [AP]M$")

    @staticmethod
    def head_gap(tok):
        """What the manual's own sample leaves under the frame, or one."""
        from tls350sim.wire import WIRE_TITLES
        from tls350sim import wiretables
        entry = WIRE_TITLES.get(wiretables.SHOWN_AS.get(tok, tok)) or {}
        return 1 if entry.get("head_gap") is None else int(entry["head_gap"])

    def a_console(self):
        from tls350sim.console import SOFTWARE_MODULES
        from tls350sim import presets
        c = Console(None)
        presets.load(c, "Truck stop, four tanks and BIR")
        c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
        for key in list(c.modules):
            c.modules[key] = 1
        c.modules["rs232"] = 1
        # all four station headers programmed, which is the state that shows
        # the defect: three of four hides it behind the empty one
        for n in range(1, 5):
            c.values[f"S503{n:02d}"] = f"STATION HEADER {n}"
        c.tick()
        return c

    def reply(self, h, code):
        out = h.handle(chr(1).encode() + code.encode()).decode("latin-1")
        return out.strip(chr(1) + chr(3) + chr(13) + chr(10)).replace(
            chr(13) + chr(10), chr(10)).split(chr(10))

    def test_every_report_this_console_answers_leaves_it(self):
        """The blank line sits after the HEADER BLOCK, and the block is not
        always five lines.

        This walked to `head + 5` -- the stamp plus four station header
        lines -- on every code, which was right only while every report drew
        a station header. Most do not: 576013-635's own samples give one to
        117 codes and none to 338, and this console now follows them
        (FIDELITY L5). The rule under test never was about the station
        header, as the test below it says in so many words, so it finds the
        end of the block rather than assuming its length.
        """
        from tls350sim.wire import REPORTS
        c = self.a_console()
        h = Handler(c, verbose=False)
        # and one blank line ABOVE the block, in all 128 samples that draw a
        # header at all -- which this console did not send. FIDELITY S6.
        station = [""] + [f"STATION HEADER {n}" for n in range(1, 5)]
        without, partial = [], []
        answered = 0
        for tok in sorted(REPORTS):
            lines = self.reply(h, "I" + tok + "00")
            if any("9999" in l for l in lines):
                continue
            head = next((i for i, l in enumerate(lines)
                         if self.STAMP.match(l.strip())), None)
            if head is None:
                continue
            block = head + 1
            if lines[block:block + 5] == station:
                block += 5
            elif lines[block:block + 2] == station[:2]:
                # all four or none of them: a site with one programmed line
                # still sends four blanks. See FIDELITY W5.
                partial.append(tok)
            if block + 1 >= len(lines):
                continue
            answered += 1
            want = self.head_gap(tok)
            got = 0
            while block + got < len(lines) and not lines[block + got].strip():
                got += 1
            if got != want:
                without.append(f"{tok}: {got} blank lines, page draws {want}")
        self.assertEqual(partial, [], "a station header block short of four")
        self.assertGreater(answered, 100)
        self.assertEqual(without, [])

    def test_the_station_header_is_the_manuals_own_answer_per_code(self):
        """FIDELITY L5. It used to be "is the code in `REPORTS`", which gave
        one to 67 codes whose sample draws none and withheld it from 50 whose
        sample draws one."""
        from tls350sim.wire import Handler as H
        # a diagnostic the manual draws bare, and a status report it does not
        self.assertIs(H._draws_station("B01"), False)
        self.assertIs(H._draws_station("217"), False)
        self.assertIs(H._draws_station("888"), False)
        self.assertIs(H._draws_station("101"), True)
        self.assertIs(H._draws_station("301"), True)
        # and the five diagnostics that DO draw one, which is why the
        # section number is not the rule
        for tok in ("A15", "A81", "A91", "B62", "BB1"):
            self.assertIs(H._draws_station(tok), True, tok)
        c = self.a_console()
        h = Handler(c, verbose=False)
        self.assertNotIn("STATION HEADER 1", self.reply(h, "IB0100"))
        self.assertIn("STATION HEADER 1", self.reply(h, "I10100"))

    def test_a_setup_value_leaves_it_too(self):
        """The rule is not about the station header block: a reply with no
        header block still leaves the line. "FISCALLY SEALED : NO" and
        "TANK STICK HEIGHT" are two of the manual's own."""
        c = self.a_console()
        lines = self.reply(Handler(c, verbose=False), "I62101")
        self.assertTrue(self.STAMP.match(lines[1].strip()), lines[1])
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "TANK LOW PRODUCT LIMIT")

    def test_an_acknowledgement_with_no_body_leaves_nothing(self):
        """A Set that answers with the stamp and nothing else does not get a
        blank line to be nothing under."""
        c = self.a_console()
        lines = self.reply(Handler(c, verbose=False), "S50100" + "0301291105")
        self.assertEqual([l for l in lines if l.strip()][2:], [])


if __name__ == "__main__":
    unittest.main()
