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
"""The front panel, driven by its own key handlers.

Needs a display, so it skips itself where there is none.
"""
import gc
import os
import struct
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

from tls350sim.console import (Console,             # noqa: E402
                               SOFTWARE_MODULES)


def a_console():
    c = Console()
    # an NVMEM203 board, the one configuration of the manual's table that
    # carries Maintenance Tracker and ISD as well as everything else
    c.board = "E6"
    for key in ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart",
                "plld", "wplld", "vlld", "io", "relay", "pump", "pumpmon",
                "vmc", "mt", "rs232"):
        c.modules[key] = 1
    c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
    return c


@unittest.skipUnless(HAVE_TK, "no display")
class Panel(unittest.TestCase):
    # One Tk interpreter for the whole class, not one per test. Tk on Windows
    # will not survive seventy interpreters created and dropped inside a
    # single process: somewhere around the thirtieth, Tk() starts failing with
    # "Can't find a usable tk.tcl", which reads like a broken installation and
    # is really a resource that never came back. The panel keeps all of its
    # state in plain attributes, so a fresh console and a reset is the same
    # starting point a fresh window would have been, and the suite runs in a
    # second rather than a minute.
    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        try:
            cls.app = SimApp(a_console(), 10001)
        except Exception as exc:               # pragma: no cover
            # This machine's Tcl reads its own init.tcl intermittently, which
            # surfaces here as "couldn't read file init.tcl: No error". It is
            # not the simulator; skip the way the module-level probe does
            # rather than reporting seventy errors.
            cls.app = None
            raise unittest.SkipTest(f"no usable Tk: {exc}")

    @classmethod
    def tearDownClass(cls):
        if cls.app is None:                    # pragma: no cover
            return
        try:
            cls.app.quit()
        except Exception:
            pass
        cls.app.destroy()
        cls.app = None
        gc.collect()

    def setUp(self):
        self.c = a_console()
        self.app = type(self).app
        self.app.console = self.c
        self.app.reset_panel()
        # the roll of paper is part of the window rather than the panel, and
        # a shared window keeps what earlier tests printed on it
        self.app.paper.delete("1.0", "end")
        self.app._render()

    def test_every_screen_in_every_mode_renders(self):
        from tls350sim.ui import HEADER
        seen = 0
        for mode in range(3):
            self.app.mode = mode
            for f in range(len(self.app.functions())):
                self.app.func = f
                for s in range(HEADER, len(self.app.steps())):
                    self.app.step = s
                    self.app._render()
                    seen += 1
        self.assertGreater(seen, 300)

    def test_change_walks_the_choices_and_enter_saves(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "PRESSURE LINE LEAK SETUP"][0]
        self.app.step = 2                      # Piping Type
        seen = []
        for _ in range(3):
            self.app.k_change()
            seen.append(self.app._lines()[1].rstrip("_"))
        self.assertEqual(len(set(seen)), 3)
        self.app.k_enter()
        # An unprogrammed enum is not blank on the screen, it reads its
        # DEFAULT -- 576013-623 Rev AN p.130: "The default is Enviroflex
        # PP1501", which is pipe type 03 -- and CHANGE walks on from what is
        # displayed, so three presses land on 06.
        self.assertEqual(self.c.values["S78801"], "0106")

    def test_the_piping_type_rests_on_the_manuals_default(self):
        """576013-623 Rev AN p.130 draws `TYP: ENVIROFLEX PP1501` and says in
        so many words that it is the default."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "PRESSURE LINE LEAK SETUP"][0]
        self.app.step = 2
        self.assertEqual(self.app._lines()[1], "TYP: ENVIROFLEX PP1501")

    def test_leaving_an_entry_without_enter_discards_it(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.app.func, self.app.step = 0, 5    # a text field
        self.app.k_change()
        self.app.buf = "SOMETHING"
        self.app.k_step()
        self.assertNotIn("SOMETHING", str(self.c.values))
        # ...and silently: 576013-623 says the data is not saved, and no
        # manual shows any screen announcing it. The display carries the
        # next step, not a message.
        self.assertEqual(self.app.msg, "")

    def test_function_lands_on_the_functions_own_screen(self):
        from tls350sim.ui import HEADER, MODES
        self.app.mode = MODES.index("SETUP")
        self.app.k_function()
        self.assertEqual(self.app.step, HEADER)
        self.assertEqual(self.app._lines()[1], "PRESS <STEP> TO CONTINUE")
        self.app.k_step()
        self.assertEqual(self.app.step, 0)

    def test_backup_goes_step_to_function_to_function(self):
        from tls350sim.ui import HEADER, MODES
        self.app.mode = MODES.index("SETUP")
        self.app.func, self.app.step = 2, 3
        for expect in (2, 1, 0, HEADER):
            self.app.k_backup()
            self.assertEqual(self.app.step, expect)
        self.app.k_backup()
        self.assertEqual((self.app.func, self.app.step), (1, HEADER))

    def test_tank_sensor_moves_on_the_first_press_while_typing(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.app.func, self.app.step = 2, 1
        self.app.k_change()
        self.app.buf = "HALF TYPED"
        self.app.k_tank()
        self.assertEqual(self.app.device, 2)          # moved, not swallowed
        self.assertFalse(self.app.editing)
        self.assertNotIn("NOT SAVED", self.app.msg)

    def test_the_device_count_follows_the_card_not_a_flat_sixteen(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.assertEqual(self.app._device_count(), 4)      # a probe module
        for _ in range(4):
            self.app.k_tank()
        self.assertEqual(self.app.device, 1)               # round it goes
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "LIQUID SENSOR SETUP"][0]
        self.assertEqual(self.app._device_count(), 8)

    def test_enter_confirms_and_one_step_moves_on(self):
        """"DAYS = XX / PRESS <STEP> TO CONTINUE ... Press STEP. The system
        displays the message:" and what it displays next is the next step."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = 1                       # Product Label
        self.app.k_change()
        self.app.buf = "PREMIUM"
        self.app.k_enter()
        self.assertEqual(self.app._lines()[1], "PRESS <STEP> TO CONTINUE")
        self.assertIn("PREMIUM", self.app._lines()[0])
        self.app.k_step()
        self.assertIsNone(self.app.confirm)
        self.assertEqual(self.app.step, 2)      # one press, not two

    def test_change_keeps_the_value_and_puts_a_cursor_on_it(self):
        """"If you enter an incorrect character, you may use the arrow keys to
        move the cursor to the character, press CHANGE, and enter the correct
        character": the field is not blanked."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = 1                       # Product Label
        was = self.app._lines()[1]
        self.app.k_change()
        self.assertTrue(self.app.editing)
        self.assertIn("REGULAR UNLEADED", self.app.buf)
        self.assertEqual(self.app.cur, 0)
        self.app._blink = False
        self.assertIn("REGULAR UNLEADED", self.app._lines()[1])
        self.assertIn("REGULAR", was)
        # and CHANGE a second time rubs it out: "(To erase a label press
        # CHANGE again.)"
        self.app.k_change()
        self.assertEqual(self.app.buf, "")

    def test_the_clock_is_edited_the_way_the_manual_edits_it(self):
        """"SET: MONTH DAY YEAR / DATE: XX/XX/XXXX ... press CHANGE, enter the
        correct date by first entering the month then the day then the year
        following the format shown on the display, then press ENTER ... Press
        STEP to continue"."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Month Day Year")
        self.assertTrue(self.app._lines()[1].startswith("DATE: "))
        self.app.k_change()
        self.assertTrue(self.app.editing)
        # A date is the one field that BLANKS: on real hardware CHANGE puts
        # up "DATE: --/--/----" and you fill the template in. The clock, on
        # the same console, keeps every digit you do not type over.
        self.app._blink = False
        self.assertEqual(self.app._lines()[1], "DATE: --/--/----")
        for ch in "12252026":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["DATE: 12/25/2026", "PRESS <STEP> TO CONTINUE"])
        self.app.k_step()
        self.assertEqual(self.app._lines()[0], "SET TIME")
        self.assertEqual(self.c.values["S50100"][:6], "261225")

    def test_the_sign_key_is_the_cursor_key_on_a_field_with_no_sign(self):
        """"The Left-Arrow key lets you move the cursor to the left. The +/-
        is used to identify a positive or negative value": one key, and which
        job it does depends on whether the field has a negative half."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Month Day Year")
        self.app.k_change()
        self.app.cur = 2
        self.app.k_alnum("+")
        self.assertNotIn("-", self.app.buf)       # a date has no minus half
        self.assertEqual(self.app.cur, 1)

        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = [i for i, t in enumerate(texts) if "Tilt" in t][0]
        self.app.k_change()
        self.app.k_alnum("1")
        self.app.k_alnum("+")
        self.assertTrue(self.app.buf.startswith("-"), self.app.buf)

    def test_the_arrow_keys_pick_am_or_pm_on_a_clock(self):
        """"To change the time press CHANGE and enter the correct time.
        Select either AM or PM by using the arrow keys" - Operator's Quick
        Help, 576013-939 Rev D."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Time")
        self.app.k_change()
        self.app._blink = False
        self.assertIn(self.app.meridiem, ("AM", "PM"))
        was = self.app.meridiem
        self.app.k_alnum("+")
        self.assertNotEqual(self.app.meridiem, was)
        self.app.k_alnum(",")
        self.assertEqual(self.app.meridiem, was)
        # and the screen reads the way the manual draws it
        # Both halves of the day stay on the screen while you edit, which
        # is how the Setup Manual draws it and how the console in the video
        # shows it; the arrows pick between them.
        self.app.meridiem = "PM"
        self.app.buf, self.app.cur = "1234", 4
        self.assertEqual(self.app._lines()[1], "TIME: 12:34 PM AM")
        self.app.k_enter()
        self.assertEqual(self.c.values["S50100"][6:10], "1234")

    def test_a_clock_entered_as_am_is_stored_on_the_24_hour_clock(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Time")
        self.app.k_change()
        self.app.meridiem, self.app.buf, self.app.cur = "AM", "1234", 4
        self.app.k_enter()
        self.assertEqual(self.c.values["S50100"][6:10], "0034")

    def test_a_comm_board_setting_takes_change_enter_step(self):
        """"To accept 1, press STEP. To choose 2, press CHANGE and press
        ENTER. The system confirms your choice ... Press STEP to continue",
        and what STEP shows is DATA LENGTH."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "COMMUNICATION SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = [i for i, t in enumerate(texts) if "Stop Bit" in t][0]
        self.assertEqual(self.app._lines()[1], "STOP BIT: 1 STOP")
        self.app.k_change()
        self.app._blink = False            # catch the cursor on its dark half
        self.assertEqual(self.app._lines()[1], "STOP BIT: 2 STOP")
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["STOP BIT: 2 STOP", "PRESS <STEP> TO CONTINUE"])
        self.app.k_step()
        self.assertEqual(self.app._lines()[1], "DATA LENGTH: 7 DATA")

    def test_typing_replaces_one_character_and_leaves_the_rest(self):
        """Recorded off a real console: "TIME: [8:06 AM PM", type 0 then 3,
        and it reads "TIME: 03:06 AM PM" - the minutes are still the ones
        that were there. In-place overtype, not retype-the-whole-field."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Time")
        self.app.k_change()
        self.app.buf, self.app.cur, self.app.meridiem = "0806", 0, "AM"
        self.app.k_alnum("0")
        self.app.k_alnum("3")
        self.assertEqual(self.app.buf, "0306")     # the minutes survived
        self.assertEqual(self.app.cur, 2)

    def test_the_arrow_keys_walk_the_cursor_and_type_over_a_character(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = 1
        self.app.k_change()
        self.app.k_alnum(",")                   # right arrow
        self.app.k_alnum(",")
        self.assertEqual(self.app.cur, 2)
        self.app.k_alnum("2")                   # A B C 2, first press is A
        self.assertEqual(self.app.buf[:3], "REA")
        self.app.k_alnum("2")                   # same key again cycles in place
        self.assertEqual(self.app.buf[:3], "REB")
        self.app.k_alnum("+")                   # left arrow
        self.assertEqual(self.app.cur, 2)

    def test_a_tank_step_reads_the_way_the_manual_prints_it(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = 2                       # Product Code
        rows = self.app._lines()
        self.assertEqual(rows[0], "T1: REGULAR UNLEADED")
        self.assertTrue(rows[1].startswith("PRODUCT CODE:"))

    def go(self, function, want):
        """Stand on the screen whose top or bottom line says `want`."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.app.confirm = None
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == function][0]
        for step in range(len(self.app.steps())):
            self.app.step = step
            if any(want in line for line in self.app._lines()):
                return self.app._lines()
        self.fail(f"no {want} screen in {function}")

    def test_a_console_wide_screen_puts_the_value_on_the_second_line(self):
        """"SET TIME" over "TIME: 1:32 PM": not the prompt twice."""
        rows = self.go("SYSTEM SETUP", "SET TIME")
        self.assertEqual(rows[0], "SET TIME")
        self.assertTrue(rows[1].startswith("TIME: "), rows[1])
        self.assertNotIn("SET TIME", rows[1])

    def test_a_setting_nobody_changed_reads_its_default(self):
        """A console out of the box is not blank: it reads U.S., DISABLED."""
        self.assertEqual(self.go("SYSTEM SETUP", "SYSTEM UNITS")[1], "U.S.")
        self.assertEqual(self.go("SYSTEM SETUP", "SHIFT #1 START TIME")[1],
                         "TIME: DISABLED")

    def test_a_numbered_console_screen_writes_its_own_function(self):
        """AUTO SHIFT #3 CLOSING is S79403 wherever the panel is pointed."""
        self.go("RECONCILIATION SETUP", "AUTO SHIFT #3")
        self.app.k_change()
        self.app.meridiem = "AM"           # the arrows pick the half of day
        for ch in "0230":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.c.values.get("S79403"), "030230")
        self.assertIsNone(self.c.values.get("S79401"))
        self.assertEqual(self.app._lines(),
                         ["TIME: 2:30 AM", "PRESS <STEP> TO CONTINUE"])

    def test_a_repeating_console_screen_walks_with_tank_sensor(self):
        """Four header lines on one step, numbered as the manual has them."""
        self.go("SYSTEM SETUP", "ENTER STATION HEADER")
        self.app.k_tank()
        self.assertEqual(self.app._lines(), ["ENTER STATION HEADER", "#2:"])


    def recon(self, function):
        """Stand in Reconciliation Mode, on that function's first step."""
        from tls350sim.ui import MODES
        self.c.software["bir"] = True
        self.c.values["S53400"] = "100"      # delivery variance reports on
        self.c.tick()
        self.app.mode = MODES.index("RECONCILIATION")
        self.app._entered = True
        self.app.confirm = None
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == function][0]
        self.app.step = 0
        return self.app._lines()

    def test_reconciliation_mode_needs_the_bir_key(self):
        """"You must have the BIR software module key installed to access
        this mode": so MODE steps over it without one."""
        from tls350sim.ui import MODES
        self.c.software.pop("bir", None)
        self.assertEqual(self.c.available_reconciliation(), [])
        self.assertFalse(self.app._mode_offered(MODES.index("RECONCILIATION")))
        self.c.software["bir"] = True
        self.assertTrue(self.app._mode_offered(MODES.index("RECONCILIATION")))

    def test_the_mode_key_reaches_reconciliation(self):
        from tls350sim.ui import MODES, MODE_SCREEN
        self.c.software["bir"] = True
        self.app.mode = MODES.index("DIAGNOSTIC")
        self.app.k_mode()
        self.assertEqual(MODES[self.app.mode], "RECONCILIATION")
        self.assertEqual(self.app.step, MODE_SCREEN)
        self.assertEqual(self.app._lines(),
                         ["RECONCILIATION MODE", "PRESS <FUNCTION> TO CONT"])

    def test_the_report_screens_read_as_the_manual_walks_them(self):
        """"DISPLAY AND PRINT / REPORT TYPE: SHIFT", then "REPORT TYPE: SHIFT
        / PROD 1: (Product)", then "PROD 1: / SELECT SHIFT: CURRENT"."""
        rows = self.recon("DISPLAY AND PRINT")
        self.assertEqual(rows, ["DISPLAY AND PRINT", "REPORT TYPE: SHIFT"])
        self.app.step = 1
        self.assertEqual(self.app._lines()[0], "REPORT TYPE: SHIFT")
        self.assertTrue(self.app._lines()[1].startswith("PROD 1:"))
        self.app.step = 2
        self.assertEqual(self.app._lines()[1], "SELECT SHIFT: CURRENT")

    def test_the_period_chosen_renames_the_screens(self):
        """A daily report asks SELECT DAY, not SELECT SHIFT."""
        self.recon("DISPLAY AND PRINT")
        self.app.k_change()                        # SHIFT -> DAILY
        self.assertEqual(self.app._lines()[1], "REPORT TYPE: DAILY")
        self.assertEqual(self.c.recon_kind, "daily")
        self.app.step = 2
        self.assertTrue(self.app._lines()[1].startswith("SELECT DAY:"))

    def test_threshold_is_on_the_periodic_report_only(self):
        self.recon("DISPLAY AND PRINT")
        texts = [st["text"] for st in self.app.steps()]
        self.assertNotIn("THRESHOLD", texts)
        self.app.k_change()
        self.app.k_change()                        # SHIFT -> DAILY -> PERIODIC
        self.assertIn("THRESHOLD", [st["text"] for st in self.app.steps()])

    def test_manual_shift_close_closes_and_prints(self):
        """"To close out the current shift, press CHANGE ... press ENTER"."""
        rows = self.recon("MANUAL SHIFT CLOSE")
        self.assertEqual(rows, ["MANUAL SHIFT CLOSE", "SHIFT CLOSE NOW: NO"])
        self.app.k_change()
        self.assertEqual(self.app._lines()[1], "SHIFT CLOSE NOW: YES")
        self.app.k_enter()
        self.assertIsNotNone(self.c.bir.last(1, "shift"))
        self.assertIn("SHIFT RECONCILIATION", self.app.paper.get("1.0", "end"))

    def test_a_manual_adjustment_lands_in_every_period_holding_it(self):
        """"adding product back into inventory": the shift the operator was
        looking at, and the day and period that contain it."""
        self.recon("MANUAL ADJUSTMENTS")
        self.app.step = 2
        self.assertEqual(self.app._lines()[0], "T 1: REGULAR UNLEADED")
        self.app.k_change()
        for ch in "40":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["CURRENT SHFT ADJ VOL: 40",
                          "PRESS <STEP> TO CONTINUE"])
        self.assertEqual(self.c.bir.current(1, "shift")["adjust"], 40.0)
        self.assertEqual(self.c.bir.current(1, "periodic")["adjust"], 40.0)

    def test_print_gives_the_report_you_are_standing_in(self):
        self.recon("DISPLAY AND PRINT DLVY")
        self.app.k_print()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("DELIVERY VARIANCE", paper)
        self.assertIn("TICKET VOL", paper)
        self.assertIn("VOLUMES ARE STANDARD", paper)

    def test_a_variance_report_hides_until_setup_turns_it_on(self):
        self.c.software["bir"] = True
        self.c.values.pop("S53300", None)
        names = [f["function"] for f in self.c.available_reconciliation()]
        self.assertNotIn("BOOK VARIANCE", names)
        self.c.values["S53300"] = "100"
        names = [f["function"] for f in self.c.available_reconciliation()]
        self.assertIn("BOOK VARIANCE", names)


    def operating(self, function):
        """Stand in Operating Mode on that function's first step."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        self.app.confirm = None
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == function][0]
        self.app.step = 0
        return self.app._lines()

    def pin_clock(self):
        """Set the console clock to noon today, so a test that adds minutes
        of console time cannot straddle midnight and land its two loads on
        different calendar days. Without this, the load-numbering tests fail
        for the ~22 minutes before midnight, when the real clock happens to
        roll over between the two loads."""
        noon = list(time.localtime())
        noon[3:6] = [12, 0, 0]
        self.c.clock_offset = time.mktime(time.struct_time(noon)) - time.time()

    def a_load(self, gallons=3000.0):
        """Pump a road tanker full out of tank 1.

        The clock is put at a fixed hour first, because loads are numbered
        within a day: two of them started at 00:50 straddle midnight, and
        the second is #1 of tomorrow rather than #2 of today.
        """
        if not getattr(self, "_pinned", False):
            now = time.localtime()
            self.c.clock_offset = ((10 - now.tm_hour) * 3600
                                   - now.tm_min * 60 - now.tm_sec)
            self._pinned = True
        self.c.values["S51300"] = "1"
        self.c.tick()
        self.c.tank_level[1]["volume"] -= gallons
        self.c.clock_offset += 60
        self.c.tick()
        self.c.clock_offset += 600
        self.c.tick()

    def test_the_tanker_load_screens_read_as_the_manual_draws_them(self):
        """"T #: UNLEADED GASOLINE / PRESS <PRINT> FOR REPORT", then
        "T #: DATE #(LOAD NO.) / TOTAL = XXXX GALS"."""
        self.a_load()
        rows = self.operating("TANKER LOAD REPORT")
        self.assertEqual(rows[0], "T 1: REGULAR UNLEADED")
        self.assertEqual(rows[1], "PRESS <PRINT> FOR REPORT")
        self.app.step = 1
        rows = self.app._lines()
        self.assertIn("#1", rows[0])
        self.assertIn("TOTAL =", rows[1])
        self.assertIn("3000", rows[1])

    def test_the_arrow_keys_walk_the_load_numbers(self):
        self.pin_clock()
        self.a_load(3000.0)
        self.a_load(2000.0)
        self.operating("TANKER LOAD REPORT")
        self.app.step = 1
        self.assertIn("#2", self.app._lines()[0])
        self.app.k_alnum(",")                     # the right-arrow key
        self.assertIn("#1", self.app._lines()[0])

    def test_the_load_screen_prints_that_load_only(self):
        self.pin_clock()
        self.a_load(3000.0)
        self.a_load(2000.0)
        self.operating("TANKER LOAD REPORT")
        self.app.step = 1
        self.app.k_print()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("TANKER LOAD REPORT", paper)
        self.assertIn("NUMBER: 2", paper)
        self.assertNotIn("NUMBER: 1", paper)

    def test_the_start_test_screens_name_what_they_will_test(self):
        """"TEST CONTROL: ALL TANKS / 0.20 GAL/HR"."""
        self.operating("START IN-TANK LEAK TEST")
        self.assertEqual(self.app._lines(),
                         ["START LEAK TEST METHOD", "ALL TANKS"])
        self.app.step = 2
        self.assertEqual(self.app._lines(),
                         ["TEST CONTROL: ALL TANKS", "0.20 GAL/HR"])
        self.app.step = 0
        self.app.k_change()                        # SINGLE TANK
        self.app.device = 2
        self.app.step = 4
        self.assertEqual(self.app._lines()[0], "START LEAK TEST: TANK 2")

    def test_a_shared_choice_shows_only_what_this_screen_offers(self):
        """The line rate is 3.0 GPH on a PLLD and 0.20 GAL/HR on a VLLD, and
        one must never show the other's value."""
        self.operating("START LINE PRESSURE TEST")
        self.app.step = 1
        self.assertEqual(self.app._lines()[1], "3.0 GPH")
        self.operating("START LINE LEAK TEST")
        self.app.step = 1
        self.assertEqual(self.app._lines()[1], "0.20 GAL/HR")

    def test_the_air_purge_runs_six_selftests(self):
        """"Air Purge purges air from the VLLD Controller by performing six
        consecutive VLLD Controller 3.0 gph selftests"."""
        self.operating("START LINE LEAK TEST")
        self.app.step = 1
        self.app.k_change()
        self.app.k_change()
        self.assertEqual(self.app._lines()[1], "AIR PURGE PROCEDURE")
        self.app.step = 2
        self.app.k_enter()
        self.assertEqual(len(self.c.leaks.history[("vlld", 1)]), 6)
        self.assertIsNone(self.c.leaks.active("vlld", 1))

    def test_the_history_screen_prints_the_history_report(self):
        self.c.leaks.start("plld", 1, "gross", None, False)
        self.c.clock_offset += 3600
        self.c.tick()
        self.operating("PRESSURE LINE RESULTS")
        self.app.step = len(self.app.steps()) - 1
        self.assertEqual(self.app._lines()[1], "PRESS PRINT FOR HISTORY")
        self.app.k_print()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("PRESSURE LINE LEAK TEST HISTORY", paper)
        self.assertIn("LAST 3.0 GAL/HR PASS:", paper)
        self.assertIn("FIRST 0.20 GAL/HR PASS EACH MONTH:", paper)


    def test_the_vmc_report_reads_as_the_manual_prints_it(self):
        """"x 1: 005830 SIDE A / STATUS: IDLE"."""
        from tls350sim.ui import MODES
        self.c.modules["vmc"] = 1
        self.c.vmc_serials[1] = "005830"
        self.c.tick()
        self.operating("VMC REPORT")
        self.assertEqual(self.app._lines(), ["x 1: 005830", "PRESS <ENTER>"])
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["x 1: 005830 SIDE A", "STATUS: IDLE"])
        self.app.step = 6
        self.assertEqual(self.app._lines()[0], "x 1: 005830 SIDE B")
        self.app.k_print()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("VMC REPORT", paper)
        self.assertIn("RECOVER RATE: 0.0", paper)
        self.assertIn("SIDE B", paper)

    def test_a_vmc_serial_number_is_programmed_per_controller(self):
        from tls350sim.ui import MODES
        self.c.modules["vmc"] = 1
        self.app.mode = MODES.index("SETUP")
        self.app._entered = True
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "VMC SETUP"][0]
        # 576013-623 Rev AN ch.27 draws three branches, and the entry screen
        # under each is headed by the branch and carries the controller:
        # "ADD VMC SERIAL NUMBER" over "x 1: 111111".
        self.app.step = 1
        self.assertEqual(self.app._lines(),
                         ["ADD VMC SERIAL NUMBER", "x 1:"])
        self.app.k_change()
        for ch in "005830":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.c.vmc_serials, {1: "005830"})
        self.app.k_tank()                      # controller 2 has its own
        self.app.confirm = None
        self.assertEqual(self.app._lines(),
                         ["ADD VMC SERIAL NUMBER", "x 2:"])


    def setup_step(self, function, starts):
        """Stand on the setup step whose text starts with `starts`."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.app._entered = True
        self.app.confirm = None
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == function][0]
        for step in range(len(self.app.steps())):
            self.app.step = step
            if self.app.steps()[step]["text"].startswith(starts):
                return self.app._lines()
        return None

    def test_the_last_system_setup_screens_are_programmable(self):
        """The tail of chapter 5, screens the panel used to walk past."""
        self.assertEqual(self.setup_step("SYSTEM SETUP", "Euro Protocol"),
                         ["EURO PROTOCOL PREFIX", "S"])
        self.app.k_change()
        # "press CHANGE, then ENTER, to select d which is a special Euro
        # Protocol command prefix": lower case, as the manual writes it
        self.assertEqual(self.app._lines()[1], "d")
        self.assertEqual(self.setup_step("SYSTEM SETUP", "Alarm Reduction"),
                         ["ALARM REDUCTION", "ENABLED"])
        self.assertEqual(self.setup_step("SYSTEM SETUP", "Fiscal Height"),
                         ["FISCAL HEIGHT SECURITY", "PRESS <ENTER>"])
        self.assertEqual(self.setup_step("SYSTEM SETUP", "Bdim"),
                         ["BDIM TRANS ALARM DELAY", "HOURS: 024"])

    def test_custom_inventory_alarm_units_appear_only_when_custom(self):
        """"If Custom is selected then you can change one or more of the five
        Inventory Alarms to one of the selectable units."""
        rows = self.setup_step("SYSTEM SETUP", "Inventory Alarms")
        self.assertEqual(rows, ["INVENTORY ALARMS UNITS", "CONFIG: STANDARD"])
        self.assertIsNone(self.setup_step("SYSTEM SETUP", "Custom Threshold"))
        self.setup_step("SYSTEM SETUP", "Inventory Alarms")
        for _ in range(4):
            self.app.k_change()                    # ... to CUSTOM
        self.assertEqual(self.app._lines()[1], "CONFIG: CUSTOM")
        self.assertEqual(self.setup_step("SYSTEM SETUP", "Custom Threshold"),
                         ["INVENTORY ALARM CUSTOM", "MAX OR LABEL: %FULL"])

    def test_the_water_alarm_delay_belongs_to_the_off_filter(self):
        """"If a Water Alarm delay of less than 3 minutes is desired, select
        Off for the Water Alarm Filter ... programmable from 30 to 180."""
        self.assertEqual(self.setup_step("IN-TANK SETUP", "Water Alarm Filter"),
                         ["T1: REGULAR UNLEADED", "WATER ALARM FILTER: LOW"])
        self.assertIsNone(self.setup_step("IN-TANK SETUP", "Water Alarm Delay"))
        self.setup_step("IN-TANK SETUP", "Water Alarm Filter")
        for _ in range(3):
            self.app.k_change()                    # LOW -> MEDIUM -> HIGH -> OFF
        self.assertEqual(self.app._lines()[1], "WATER ALARM FILTER: OFF")
        self.assertEqual(self.setup_step("IN-TANK SETUP", "Water Alarm Delay"),
                         ["T1: REGULAR UNLEADED", "WATER ALARM DELAY: 180S"])
        self.app.k_change()
        for ch in "020":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertIn("INVALID", self.app._lines()[0])      # 30 to 180

    def test_blend_partners_belong_to_a_mechanical_blender(self):
        self.assertEqual(
            self.setup_step("PRESSURE LINE LEAK SETUP", "Mechanical Blender"),
            ["Q 1: PLLD NUMBER 1", "MECHANICAL BLENDER: NO"])
        self.assertIsNone(
            self.setup_step("PRESSURE LINE LEAK SETUP", "Blend Partners"))
        self.setup_step("PRESSURE LINE LEAK SETUP", "Mechanical Blender")
        self.app.k_change()
        self.assertEqual(
            self.setup_step("PRESSURE LINE LEAK SETUP", "Blend Partners"),
            ["Q 1: BLEND PARTNERS", "Q#: 00, 00"])

    def test_a_setting_is_kept_per_device_where_the_screen_is(self):
        self.setup_step("IN-TANK SETUP", "Water Alarm Filter")
        self.app.k_change()
        self.assertEqual(self.c.setting("water_filter", 1), "MEDIUM")
        self.app.k_tank()
        self.assertEqual(self.app._lines()[1], "WATER ALARM FILTER: LOW")


    def test_the_auto_transmit_times_follow_the_signals(self):
        """"The above message appears only if you chose Transmit for at least
        one of the items", and the repeat time only for Transmit/Repeat."""
        self.setup_step("COMMUNICATION SETUP", "Auto Transmit Setup")
        texts = [st["text"] for st in self.app.steps()]
        self.assertIn("Auto Leak Alarm Limit", texts)
        self.assertIn("Auto Sensor Out Alarm", texts)       # all twelve
        self.assertNotIn("Auto Delay Time", texts)
        self.setup_step("COMMUNICATION SETUP", "Auto Leak Alarm")
        self.app.k_change()                                  # TRANSMIT
        self.assertEqual(self.app._lines(),
                         ["AUTO LEAK ALARM LIMIT", "TRANSMIT"])
        self.assertEqual(self.setup_step("COMMUNICATION SETUP",
                                         "Auto Delay Time"),
                         ["AUTO TRANSMIT MESSAGE", "AUTO DELAY TIME: 005"])
        texts = [st["text"] for st in self.app.steps()]
        self.assertNotIn("Auto Repeat Time", texts)
        self.setup_step("COMMUNICATION SETUP", "Auto Leak Alarm")
        self.app.k_change()                                  # TRANSMIT/REPEAT
        self.assertEqual(self.setup_step("COMMUNICATION SETUP",
                                         "Auto Repeat Time"),
                         ["AUTO TRANSMIT MESSAGE", "AUTO REPEAT TIME: 005"])

    def test_every_setup_step_does_something(self):
        """A step you can only walk past is a step that is not finished."""
        from tls350sim.console import SETUP_MENU
        idle = [st["text"] for fn in SETUP_MENU for st in fn["steps"]
                if not (st.get("code") or st.get("console") or st.get("body")
                        or st.get("archive") or st.get("profile")
                        or st.get("point"))]
        self.assertEqual(idle, [])

    def test_the_config_screen_is_a_slot_editor(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = 0
        self.app._blink = False
        self.assertEqual(self.app._lines(),
                         ["TANK CONFIG - MODULE 1", "SLOT #: X X X X"])
        self.app.k_change()                       # position 1 on
        self.app.k_alnum(",")                     # the right-arrow key,
        self.app.k_alnum(",")                     # which arrives as ","
        self.app.k_change()                       # position 3 on
        self.assertEqual(self.app._lines()[1], "SLOT #: 1 X 3 X")
        self.app.k_enter()
        self.assertEqual(self.app._lines()[1], "PRESS <STEP> TO CONTINUE")
        self.assertEqual(self.c.values["S60101"], "011")
        self.assertEqual(self.c.values["S60102"], "020")
        self.assertEqual(self.c.values["S60103"], "031")

    def test_tank_sensor_walks_every_position_the_module_carries(self):
        """Programmed or not: "it changes to the next tank even if it is not
        programmed, on any screen, and cycles through all the tanks that
        module can support"."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.c.set_slots("601", "1 X X X")      # only tank 1 configured
        self.assertEqual(self.app._devices(), [1, 2, 3, 4])
        seen = []
        for _ in range(5):
            self.app.k_tank()
            seen.append(self.app.device)
        self.assertEqual(seen, [2, 3, 4, 1, 2])

    def test_the_walk_is_as_wide_as_the_module(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        for name, count in (("IN-TANK SETUP", 4),          # four probes
                            ("LIQUID SENSOR SETUP", 8),    # eight sensors
                            ("VAPOR SENSOR SETUP", 5),
                            ("PRESSURE LINE LEAK SETUP", 6),
                            ("WPLLD LINE LEAK SETUP", 3),
                            ("EXTERNAL INPUT SETUP", 2),   # two inputs
                            ("OUTPUT RELAY SETUP", 4)):
            self.app.func = [i for i, f in enumerate(fns)
                             if f["function"] == name][0]
            self.assertEqual(self.app._devices(),
                             list(range(1, count + 1)), name)

    def test_enter_descends_into_a_diagnostic_sub_screen(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("DIAGNOSTIC")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM DIAGNOSTIC"][0]
        top = len(self.app.steps())
        self.app.step = 3                      # SYSTEM CONFIGURATION
        self.app.k_enter()
        self.assertIsNotNone(self.app.sub)
        self.assertIn("SLOT 1", self.app._diag_screens(
            self.app.cur_function())[1]["text"])
        self.app.step = 0
        self.app.k_backup()
        self.assertIsNone(self.app.sub)
        self.assertEqual(len(self.app.steps()), top)

    def test_the_slot_screens_list_the_cards_in_the_cage(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("DIAGNOSTIC")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM DIAGNOSTIC"][0]
        self.app.step = 3
        self.app.k_enter()
        slots = [s["text"] for s in self.app.steps() if s["text"].startswith(
            "SLOT")]
        self.assertTrue(any("PROBE" in t for t in slots))
        # a half-empty cage shows the empty slots as the console does
        from tls350sim.console import Console
        bare = [l1 for l1, _l2 in Console().slot_report()]
        self.assertTrue(any("UNUSED" in t for t in bare))

    def test_a_security_code_guards_setup_and_diagnostic(self):
        from tls350sim.ui import MODES
        self.c.values["S50400"] = "123456"
        self.app.k_mode()
        self.assertTrue(self.app.locked)
        self.assertEqual(self.app._lines()[0], "SYSTEM SECURITY")
        for ch in "000000":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(MODES[self.app.mode], "NORMAL")     # turned away
        self.app.k_mode()
        for ch in "123456":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.app.msg = ""
        self.assertFalse(self.app.locked)
        self.assertEqual(MODES[self.app.mode], "SETUP")

    def test_no_code_programmed_no_prompt(self):
        from tls350sim.ui import MODES
        self.app.k_mode()
        self.assertFalse(self.app.locked)
        self.assertEqual(MODES[self.app.mode], "SETUP")

    def _archive(self):
        from tls350sim.ui import MODES
        self.app.ARCHIVE_SECONDS = 0        # do not make the test wait
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "ARCHIVE UTILITY"][0]

    def _answer_yes(self):
        # CHANGE, ENTER, STEP, twice, as the manual walks it: the answer,
        # then ARE YOU SURE?
        for key in (self.app.k_change, self.app.k_enter, self.app.k_step,
                    self.app.k_change, self.app.k_enter, self.app.k_step):
            key()

    def test_the_archive_utility_saves_and_restores(self):
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self.assertEqual(self.app._lines(),
                         ["ARCHIVE UTILITY", "SAVE SETUP DATA: NO"])
        self._answer_yes()
        self.assertTrue(os.path.exists(self.c.archive_path()))
        # somebody programmes over it, badly
        self.c.values["S60201"] = "01WRONG LABEL        "
        self.c.values["S60701"] = "01DEADBEEF"
        self.app.step = 1                       # RESTORE SETUP DATA
        self._answer_yes()
        self.assertEqual(self.c.values["S60201"], "01REGULAR UNLEADED   ")
        os.remove(self.c.archive_path())

    def test_a_restore_replaces_rather_than_merges(self):
        """"clear current system setup data and replace it with system setup
        data you stored previously"."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.c.values["S60202"] = "02SOMETHING NEW      "
        self.app.step = 1
        self._answer_yes()
        self.assertNotIn("S60202", self.c.values)
        os.remove(self.c.archive_path())

    def test_clear_setup_data_clears_the_eeprom_not_the_console(self):
        """"the system starts clearing all current setup information in the
        EEPROM": the archive goes, the site keeps running."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.assertTrue(self.c.archive_exists())
        self.app.step = 2                       # CLEAR SETUP DATA
        self._answer_yes()
        self.assertFalse(os.path.exists(self.c.archive_path()))
        self.assertEqual(self.c.values["S60201"], "01REGULAR UNLEADED   ")

    def test_a_restore_with_no_archive_says_so(self):
        import os
        self._archive()
        if os.path.exists(self.c.archive_path()):
            os.remove(self.c.archive_path())
        self.app.step = 1
        self._answer_yes()
        self.assertIn("NO ARCHIVE", self.app.msg.upper())

    def test_a_restore_prints_what_it_put_back(self):
        """"The system also prints a complete listing of all restored setup
        data"."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.app.paper.delete("1.0", "end")
        self.app.step = 1
        self._answer_yes()
        self.assertIn("SETUP", self.app.paper.get("1.0", "end").upper())
        os.remove(self.c.archive_path())

    def test_the_profile_step_asks_before_erasing_the_volumes(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index(
            "Tank Profile (1 Pt/4 Pts/20 Pts/linear/50 Pts)")
        # the fixture programmed a linear full volume (60A), so that is the
        # profile the tank is on
        self.assertEqual(self.app._lines()[1], "TANK PROFILE LINEAR")
        for _ in range(2):                       # LINEAR -> 50 PTS -> 1PT...
            self.app.k_change()
        while self.app.buf != "4 PTS":
            self.app.k_change()
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["CLEAR EXISTING PROFILE", "ARE YOU SURE? : NO"])
        self.app.k_change()
        self.app.k_enter()
        self.assertIn("4 PTS", self.app._lines()[0])
        self.app.k_step()
        self.assertEqual(self.c.tank_profile(1), "01")
        self.assertEqual(self.c.full_volume(1), 10000.0)   # volume carried over

    def test_full_volume_writes_the_function_the_profile_chose(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Full Volume")
        self.assertEqual(self.app.cur_code(), "S60A01")   # linear, from 60A
        self.c.set_tank_profile(1, "02")
        self.assertEqual(self.app.cur_code(), "S60601")   # twenty point

    def test_a_secured_chart_asks_for_its_passcode(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.c.set_chart_code("778899")
        self.c.set_tank_profile(1, "04")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Tank Capacity")
        self.assertEqual(self.app._lines()[0], "TANK PROFILE : 50 PTS")
        for ch in "000000":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertIn("INVALID", self.app.msg)
        self.app.msg = ""
        for ch in "778899":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.app.msg = ""
        self.assertTrue(self.app._lines()[1].startswith("TANK CAPACITY"))

    def test_the_security_code_screen_reads_as_the_manual_prints_it(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Tank Chart Security")
        self.assertEqual(self.app._lines(),
                         ["TANK CHART SECURITY", "CODE : 000000"])
        self.app.k_change()
        self.app.buf = "778899"
        self.app.k_enter()
        self.assertEqual(self.app._lines()[0], "CODE: ******")
        self.app.k_step()
        self.assertTrue(self.c.chart_secured())

    def test_print_puts_a_report_on_the_paper(self):
        self.app.k_print()
        self.assertIn("INVENTORY REPORT", self.app.paper.get("1.0", "end"))

    def test_the_mode_screen_prints_the_setup_data_report(self):
        """"To print a Setup Data Report, press the MODE key to display the
        Setup Mode main screen ... then press the PRINT key"."""
        from tls350sim.ui import MODES, MODE_SCREEN
        self.app.mode = MODES.index("SETUP")
        self.app.step = MODE_SCREEN
        self.assertEqual(self.app._lines(),
                         ["SETUP MODE", "PRESS <FUNCTION> TO CONT"])
        title, lines = self.app._report()
        self.assertEqual(title, "SETUP DATA REPORT")
        out = chr(10).join(lines)
        self.assertIn("IN-TANK SETUP", out)
        self.assertIn("REGULAR UNLEADED", out)

    def test_a_setup_function_prints_only_its_own(self):
        from tls350sim.ui import MODES, HEADER
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        self.app.step = HEADER
        title, lines = self.app._report()
        out = chr(10).join(lines)
        self.assertEqual(title, "IN-TANK SETUP")
        self.assertIn("ENTER PRODUCT LABEL", out)
        self.assertNotIn("SYSTEM SETUP", out)
        # the screens of THIS function, and the tank they are pointed at
        self.assertIn("T1: REGULAR UNLEADED", out)

    def test_an_operating_function_prints_what_the_table_says(self):
        from tls350sim.ui import MODES, HEADER
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        fns = self.app.functions()
        for name, wanted in (("IN-TANK INVENTORY", "INVENTORY REPORT"),
                             ("LIQUID STATUS", "LIQUID SENSOR STATUS"),
                             ("IN-TANK TEST RESULTS",
                              "IN-TANK LEAK TEST RESULTS")):
            found = [i for i, f in enumerate(fns) if f["function"] == name]
            if not found:
                continue
            self.app.func, self.app.step = found[0], HEADER
            title, lines = self.app._report()
            self.assertIn(wanted, chr(10).join(lines), name)

    def test_a_step_marked_for_one_device_prints_one(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        fns = self.app.functions()
        found = [i for i, f in enumerate(fns)
                 if f["function"] == "IN-TANK TEST RESULTS"]
        self.app.func = found[0]
        steps = self.app.steps()
        self.app.step = [i for i, st in enumerate(steps)
                         if st.get("print_scope") == "device"][0]
        self.assertIsNotNone(self.app.cur_step().get("print_scope"))

    def test_alarm_test_switches_a_relay_in_the_relay_test(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "TEST OUTPUT RELAYS"][0]
        self.app.k_alarm()
        self.assertTrue(self.c.relays[self.app.device])
        self.app.k_alarm()
        self.assertFalse(self.c.relays[self.app.device])

    def test_the_status_screen_shows_the_alarm_the_console_would(self):
        self.c.values["S62101"] = "01" + struct.pack(">f", 1000.0).hex().upper()
        self.c.tank_level[1]["volume"] = 500.0
        self.app._blink = False
        rows = self.app._lines()
        self.assertEqual(rows[1], "T 1:LOW PRODUCT ALARM")


if __name__ == "__main__":
    unittest.main(verbosity=2)
