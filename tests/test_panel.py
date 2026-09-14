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
import re
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
    # the volume alone says nothing about the profile -- 60A and 604 are
    # one number under two names -- so the panel's own selection is set
    # here as well. See Console.tank_profile.
    c.tank_profiles[1] = "03"
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
            # No usable display: skip the way the module-level probe does
            # rather than reporting seventy errors.
            #
            # This used to blame the machine's Tcl for reading its own
            # init.tcl intermittently. It was pytest's default `--capture=fd`,
            # which replaces file descriptors 1 and 2 while Tk wants a usable
            # stdio handle to start on; `pytest.ini` sets `--capture=sys` and
            # the probes pass every time. Kept, because a real headless run
            # still has to skip rather than fail.
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
        # The beeper is real now -- `_poll` sounds it while a condition is
        # standing -- and `winsound.Beep` BLOCKS for its whole duration, so
        # a suite that drives the poll would spend a fifth of a second
        # making a noise on every tick. Counted here instead, which is also
        # how the tests that care about it assert.
        self.beeps = []
        self.app._beep = lambda times=1: self.beeps.append(times)
        self.app._render()

    def _key_block(self):
        """Stand on MAINT HARDWARE KEY BLOCK, one level down in the list.

        Sub-step 0 is the first screen of the branch: ENTER lands there,
        where Figure 6-4's `E` arrow points, and the branch no longer opens
        with a second copy of the screen that offered it. DG3.
        """
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("MAINT HARDWARE KEY BLOCK")
        self.app.step = 0
        self.app.sub = self.app._diag_children()
        self.app.step = 0
        return self.app

    def test_the_key_block_list_is_the_console_s_own_keys(self):
        """It was three names out of Figure 6-4 -- J SMYTHE, J DOE and a
        J CLARKE with no key number at all -- on a console whose active key
        list was empty and stayed empty. FIDELITY D13."""
        app = self._key_block()
        self.assertEqual([st for st in app.steps() if st.get("ident")], [])
        self.c.present_tracker_key("A12345", "J SMYTHE")
        self.c.present_tracker_key("A98765", "J CLARKE")
        rows = [st["text"] for st in app.steps() if st.get("ident")]
        self.assertEqual(rows, ["J SMYTHE          A12345",
                                "J CLARKE          A98765"])
        self.assertTrue(all(len(r) == 24 for r in rows), rows)

    def test_blocking_a_key_from_its_own_row(self):
        """"To block J. Doe, press CHANGE", then ENTER, then STEP onto
        `BLOCK: A54321 / ARE YOU SURE?: NO` -- which used to draw the
        manual's literal XXXXXX because the screen's reader was unreachable
        behind its own action."""
        app = self._key_block()
        self.c.present_tracker_key("A54321", "J DOE")
        app.step = 0
        self.assertEqual(app._lines()[1], "BLOCK: NO")
        app.k_change()
        self.assertEqual(app._lines()[1], "BLOCK: YES")
        app.k_enter()                       # selects the key
        self.assertEqual(self.c.mt_pending, "A54321")
        app.confirm = None
        app.step = 1                        # ARE YOU SURE
        self.assertEqual(app._lines()[0], "BLOCK: A54321")
        app.k_change()
        app.k_enter()
        self.assertEqual(self.c.tracker_keys(), [])
        self.assertEqual(self.c.blocked_tracker_keys(), [("A54321", "J DOE")])

    def test_typing_an_id_into_the_other_branch(self):
        """Figure 6-4's second column: `ENTER ID TO BLOCK / PRESS <ENTER>`,
        which was missing entirely, and then the ID typed in."""
        from tls350sim.ui import MODES
        self.c.present_tracker_key("A12345", "J SMYTHE")
        self.app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("MAINT HARDWARE KEY BLOCK")
        self.app.sub, self.app.step = None, 1
        self.assertEqual(self.app._lines(), ["ENTER ID TO BLOCK",
                                             "PRESS <ENTER>"])
        self.app.sub = self.app._diag_children()
        self.app.step = 0
        self.assertEqual(self.app._lines(), ["ENTER ID TO BLOCK", "ID:"])
        self.app.k_change()
        for key in "A12345":
            self.app.k_alnum(key)
        self.assertEqual(self.app._lines()[1], "ID: A12345")
        self.app.k_enter()
        self.assertEqual(self.c.mt_pending, "A12345")
        self.app.confirm = None
        self.app.step = 1
        self.assertEqual(self.app._lines()[0], "BLOCK: A12345")
        self.app.k_change()
        self.app.k_enter()
        self.assertEqual(self.c.blocked_tracker_keys(),
                         [("A12345", "J SMYTHE")])

    def test_the_blue_key_runs_the_manual_s_log_in(self):
        """"Press the blue key on the front panel. The display will read
        'Disabled' or 'Enabled' ... Press Step ... You have one minute to
        plug your ID key into the MT Comm card and press Enter." It toggled
        a flag and flashed a hard-coded ID."""
        app = self.app
        app.key_id.set("A12345")
        app.key_label.set("J SMYTHE")
        app.key_expired.set(False)
        app.k_blue()
        self.assertEqual(app._mt_lines(), ["MAINTENANCE TRACKER", "DISABLED"])
        app.k_step()
        self.assertEqual(app._mt_lines(), ["INSERT KEY IN PORT",
                                           "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._mt_lines(), ["MAINTENANCE TRACKER",
                                           "LOGGED IN A12345"])
        self.assertTrue(self.c.mt_logged_in())
        app.k_enter()                        # any key, back to the console
        self.assertIsNone(app.mt_login)
        # and it reads ENABLED the second time
        app.k_blue()                         # takes the key out again
        app.k_blue()
        self.assertEqual(app._mt_lines(), ["MAINTENANCE TRACKER", "ENABLED"])

    def test_the_key_prompt_times_out_after_a_minute(self):
        """"or the system will timeout (and return to the operating mode
        main screen)"."""
        app = self.app
        app.k_blue()
        app.k_step()
        self.assertEqual(app.mt_login, "insert")
        self.c.clock_offset += 61.0
        app.k_enter()
        self.assertIsNone(app.mt_login)
        self.assertFalse(self.c.mt_logged_in())

    def test_a_protected_alarm_wants_the_key(self):
        """A console with Maintenance Tracker will not clear a protected
        alarm without one, which is what the log-in is FOR."""
        app = self.app
        self.c.tank_level[1] = {"volume": 200.0, "water": 0.0}
        self.c.values["S60401"] = "01" + struct.pack(">f", 9000.0).hex().upper()
        self.c.tick()
        app.key_id.set("A12345")
        app.k_blue(); app.k_step(); app.k_enter()
        self.assertTrue(self.c.mt_logged_in())

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

    def test_no_diagnostic_screen_draws_a_blank_second_line(self):
        """The data half of this is in test_conformance; this is the panel
        half, which is where the last two blanks were actually coming from.
        A screen whose `live` token answers nothing on THIS console -- ORIG
        and CURR REF DISTANCE on a capacitance probe, and this console has
        one -- drew an empty line where the manual draws its own. FIDELITY
        D2.
        """
        from tls350sim.ui import HEADER, MODES
        self.app.mode = MODES.index("DIAGNOSTIC")
        blank = []
        for f in range(len(self.app.functions())):
            self.app.func = f
            name = self.app.cur_function()["function"]
            for s in range(HEADER, len(self.app.steps())):
                self.app.step = s
                self.app._render()
                head, body = self.app._lines()[:2]
                if not body.strip():
                    blank.append((name, head))
        self.assertEqual(blank, [])

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

    def test_the_bdim_delay_takes_zero_or_five_hours_and_up(self):
        """576013-623 Rev AN p.51: "This feature lets you enter a delay (of
        from 5 to 999 hours) before posting a Block DIM Transaction Alarm
        ... Enter 000 to disable this feature." The field took 0 to 999
        flat, so it accepted one to four hours, which is neither the range
        nor the disable. FIDELITY N3.
        """
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        where = []
        for i in range(len(self.app.functions())):
            self.app.func = i
            where += [(i, j) for j, st in enumerate(self.app.steps())
                      if st.get("head") == "BDIM TRANS ALARM DELAY"]
        self.assertTrue(where, "this console has no BDIM delay step")
        self.app.func, self.app.step = where[0]
        def type(value):
            if not self.app.editing:
                self.app.k_change()
            self.app.buf = value
            self.app.k_enter()
        # The value is refused and the glass says nothing: `INVALID ENTRY`
        # is this project's screen and no console's. See FIDELITY U5.
        for bad in ("001", "004"):
            self.app.logbox.delete("1.0", "end")
            type(bad)
            self.assertEqual(self.app.msg, "")
            self.assertIn("entry refused", self.app.logbox.get("1.0", "end"))
            self.assertNotEqual(self.c.setting("bdim_delay", 0), bad)
        for good in ("005", "999", "000"):
            type(good)
            self.assertEqual(self.c.setting("bdim_delay", 0), good)

    def test_enter_on_a_selection_acknowledges_it(self):
        """FIDELITY O9, the largest single class in the live modes. The
        manual draws a two-line acknowledgement after EVERY selection change
        -- "Press CHANGE to choose INSERT, then press ENTER ... The system
        displays this message" -- and `k_enter` had no branch for a selection
        step at all, so ENTER on one was a no-op. Thirteen screen shapes and
        about thirty instances.

        The acknowledgement is the line the screen was already showing, over
        PRESS <STEP> TO CONTINUE, which is why this asserts the pair rather
        than a literal: Operating Mode draws some of them bare ("SINGLE
        TANK", "3.0 GPH") and Reconciliation Mode words its own ("SELECT
        SHIFT: CURRENT").
        """
        from tls350sim.ui import CONT_STEP, MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        seen = 0
        for i, fn in enumerate(self.app.functions()):
            self.app.func = i
            for j, st in enumerate(self.app.steps()):
                if not st.get("sel"):
                    continue
                self.app.step = j
                self.app.confirm = None
                self.app._render()
                shown = self.app._lines()[1]
                self.app.k_enter()
                self.assertEqual(self.app._lines(), [shown, CONT_STEP],
                                 f"{fn['function']} / {st['text']}")
                seen += 1
        self.assertGreater(seen, 5)

    def test_a_typed_duration_is_confirmed_the_same_way(self):
        """p.20-2 confirms it as `DURATION: (Time)` over PRESS <STEP> TO
        CONTINUE. This flashed `ENTERED` over `3 HOURS`, a screen no manual
        draws."""
        from tls350sim.ui import CONT_STEP, MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        for i, fn in enumerate(self.app.functions()):
            if fn["function"] != "START IN-TANK LEAK TEST":
                continue
            self.app.func = i
            for j, st in enumerate(self.app.steps()):
                if not st.get("entry"):
                    continue
                self.app.step = j
                self.app.k_change()
                self.app.buf = "4"
                self.app.k_enter()
                self.assertEqual(self.app._lines(),
                                 ["DURATION: 4", CONT_STEP])
                return
        self.fail("this console has no leak-test duration step")

    def test_typing_in_a_live_mode_keeps_the_head_and_the_label(self):
        """FIDELITY O11. The draw path returned early while editing, so the
        live-mode renderers never ran and three branches written for exactly
        this case were unreachable. p.20-2 draws `TEST DURATION: ALL TANKS`
        over `DURATION: XX` while the digits go in."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        for i, fn in enumerate(self.app.functions()):
            if fn["function"] != "START IN-TANK LEAK TEST":
                continue
            self.app.func = i
            for j, st in enumerate(self.app.steps()):
                if not st.get("entry"):
                    continue
                self.app.step = j
                self.app._render()
                head = self.app._lines()[0]
                self.app.k_change()
                self.app._render()
                self.assertEqual(self.app._lines()[0], head)
                self.assertTrue(self.app._lines()[1].startswith("DURATION:"),
                                self.app._lines())
                return
        self.fail("this console has no leak-test duration step")

    def test_a_delivery_density_can_be_typed_in(self):
        """FIDELITY O13. 576013-610 Rev AC p.4-5: "Press STEP until you see
        the Next Delivery display ... Press CHANGE and enter any one of the
        three values below from the product's delivery ticket". The two
        screens were read-only `live` steps with no code, so CHANGE did
        nothing and this was the console's only route to a delivery density.

        Four screens, in the manual's own order: the reading, the entry, the
        confirmation, and the reading again with the value in it.
        """
        from tls350sim.ui import CONT_STEP, MODES
        self.c.values["S56000"] = "1"          # mass/density enabled
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        for i, fn in enumerate(self.app.functions()):
            if fn["function"] != "IN-TANK INVENTORY":
                continue
            self.app.func = i
            for j, st in enumerate(self.app.steps()):
                if not st.get("density"):
                    continue
                self.app.step = j
                self.app.confirm = None
                self.app._render()
                self.assertEqual(self.app._lines(),
                                 ["T 1: NEXT DELIVERY", "DENSITY = 0.0000"])
                self.app.k_change()
                self.app.buf = "5.9972"
                self.app._render()
                self.assertEqual(self.app._lines()[0], "T 1: NEXT DELIVERY")
                self.assertTrue(
                    self.app._lines()[1].startswith("DENSITY         :"),
                    self.app._lines())
                self.app.k_enter()
                self.assertEqual(self.app._lines(),
                                 ["DENSITY         :5.9972", CONT_STEP])
                self.app.confirm = None
                self.app._render()
                self.assertEqual(self.app._lines(),
                                 ["T 1: NEXT DELIVERY", "DENSITY = 5.9972"])
                self.assertEqual(
                    self.c.delivery_density_value(1, "0"), 5.9972)
                return
        self.fail("this console has no delivery density step")

    def test_fuel_management_walks_products_not_tanks(self):
        """FIDELITY O4. "All information displayed is for products, not
        tanks", and the head is the product name alone -- `REGULAR
        UNLEADED` -- where this console drew `T 1: DIESEL`."""
        from tls350sim.ui import MODES
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        for i, fn in enumerate(self.app.functions()):
            if fn["function"] != "FUEL MANAGEMENT":
                continue
            self.app.func = i
            self.assertEqual(self.app._device_count(), 3)
            self.app.device = 1
            heads = []
            for j in range(len(self.app.steps())):
                self.app.step = j
                self.app._render()
                heads.append(self.app._lines()[0])
            self.assertEqual(heads[0], "FUEL MANAGEMENT")
            self.assertEqual(set(heads[1:]), {"DIESEL"})
            self.app.device = 2
            self.app.step = 1
            self.app._render()
            self.assertEqual(self.app._lines()[0], "REGULAR UNLEADED")
            return
        self.fail("this console has no FUEL MANAGEMENT")

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
        """"SET MONTH DAY YEAR / DATE: XX/XX/XXXX ... press CHANGE, enter the
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

    def _at_the_date(self):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "SYSTEM SETUP"][0]
        texts = [st["text"] for st in self.app.steps()]
        self.app.step = texts.index("Set Month Day Year")
        self.app._blink = False

    def _to_line_test_enter(self):
        """Walk START PRESSURE LINE TEST to its `PRESS <ENTER>` screen."""
        from tls350sim.ui import MODES
        self.app.msg = None
        self.app.confirm = None
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("START PRESSURE LINE TEST")
        self.app.step = 0
        for _ in range(8):
            if "PRESS <ENTER>" in (self.app._lines() + ["", ""])[1]:
                return True
            self.app.k_step()
        return False

    def test_all_lines_is_the_lines_the_console_has(self):
        """ALL LINES asked the CARD how many positions it could serve, so
        on a two-line site it started tests on six -- four of them on pipe
        nobody has told the console about. `programmed_lines` is the rule
        every other list of them already follows: a line exists once its
        position is switched on at LINE CONFIG or it has a label, and a card
        in the cage is wires."""
        self._program_two_lines()
        self.assertEqual(self.c.capacity("plld"), 6)
        mine = [n for k, n, _l in self.c.programmed_lines() if k == "plld"]
        self.assertEqual(mine, [1, 2])
        self.assertTrue(self._to_line_test_enter())
        self.app.k_enter()
        running = [n for n in range(1, 7)
                   if self.c.lines.line("plld", n).running()]
        self.assertEqual(running, [1, 2])

    def _program_two_lines(self):
        """LINE CONFIG and a label on positions 1 and 2, which is what makes
        a position a line the console admits to having. The card carries
        six; a site has told it about two."""
        for n in (1, 2):
            self.c.values[f"S781{n:02d}"] = f"{n:02d}1"
            self.c.values[f"S782{n:02d}"] = f"{n:02d}" + f"LINE {n}".ljust(20)
            self.c.values[f"S785{n:02d}"] = f"{n:02d}01"
        self.c.tick()

    def test_enter_always_lands_on_the_status_screen(self):
        """576013-610 Rev AC p.11-4: "Press ENTER to begin the test. The
        system displays the message: Q #: RUNNING PUMP / PRESS <STEP> TO
        CONTINUE." On this walk the status word IS the answer -- 577013-344
        Rev H p.22 lists DISPENSING, TEST 3.0, HANDLE ON, LINE LOCKOUT,
        DISABLE ALARM and the rest on that same line, and every one of them
        is a reason a test did not start.

        This counted only "TEST STARTED" and threw the rest away, so ENTER
        on a line that was dispensing, or already testing, changed nothing
        on the glass and printed nothing anywhere: a dead key. The STOP half
        of the same function has drawn the manual's own LEAK TEST NOT ACTIVE
        all along.
        """
        self._program_two_lines()
        self.assertTrue(self._to_line_test_enter())
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["Q 1: RUNNING PUMP", "PRESS <STEP> TO CONTINUE"])
        # and again, with the test already running: still an answer
        self.assertTrue(self._to_line_test_enter())
        self.app.k_enter()
        self.assertEqual(self.app._lines()[1], "PRESS <STEP> TO CONTINUE")
        self.assertTrue(self.app._lines()[0].startswith("Q "))

    def test_a_short_date_is_refused_and_not_reinterpreted(self):
        """Reported from the bench: entering a date put the year's digits
        in the wrong field.

        The template is eight cells and the buffer the panel hands the
        encoder is the digits alone -- no slashes, and there never were
        any. `_encode_value`'s short-form branch was guarded on finding two
        of them, so it could not fire from the keypad: six typed digits
        fell through it and were stored AS THOUGH they were the wire's
        YYMMDD. `010226` for the 2nd of January 2026 kept 01 as the year,
        02 as the month and 26 as the day, and the console then showed
        `02/26/2001`. It was silent about it whenever the digits passed the
        range check, which is most of the year.

        Every other fixed-width field here already refuses a short entry --
        the time field's is ENTER HHMM -- and a date does now too. Refused
        the way this console refuses anything: the value does not move and
        the glass keeps its screen. See FIDELITY U5.
        """
        self._at_the_date()
        self.c.values["S50100"] = "0301291105"
        self.app.k_change()
        for ch in "010226":
            self.app.k_alnum(ch)
        # the trailing cells are still unfilled, and say so
        self.assertEqual(self.app._lines()[1], "DATE: 01/02/26--")
        self.app.k_enter()
        self.assertEqual(self.c.values["S50100"], "0301291105")
        self.assertFalse(self.app.editing)

    def test_the_cursor_cannot_walk_off_the_end_of_the_template(self):
        """A ninth digit on an eight-cell date is not a digit. It used to
        grow the buffer while the display went on showing the first eight,
        so the screen read as a complete entry and ENTER refused it."""
        self._at_the_date()
        self.app.k_change()
        for ch in "122520266":
            self.app.k_alnum(ch)
        self.assertEqual(self.app.buf, "12252026")
        self.assertEqual(self.app._lines()[1], "DATE: 12/25/2026")
        self.app.k_enter()
        self.assertEqual(self.c.values["S50100"][:6], "261225")

    def test_the_wire_still_takes_the_six_digits_it_stores(self):
        """The panel and the wire are different callers. A Set carries
        YYMMDD, which is what the console holds, and must not be read as a
        short MMDDYY typed at a keypad."""
        from tls350sim import fieldio
        field = {"kind": "date"}
        self.assertEqual(fieldio.encode_value(field, "010226"), "010226")
        self.assertEqual(fieldio.encode_value(field, "12252026"), "261225")

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

    def test_holding_alarm_test_lights_everything_and_prints(self):
        """577013-814, TLS-3xx Audible and Visual Test Procedure: "1. Press
        and Hold Alarm Test button for a minimum 3 seconds. 2. Verify Green
        Power, Yellow Warning and Red Alarm LED indicators are lit and audible
        alarm sounds. 3. The printer will automatically print a System Status
        report."

        This is step one of the annual operability inspection, so it has to
        work from an idle console with nothing wrong with it.
        """
        beeps = []
        self.app._beep = lambda times=1: beeps.append(times)
        self.app.paper.delete("1.0", "end")
        self.app._lamp_test()
        for key in ("alarm", "warn", "power"):
            cv, oval, colour = self.app.led[key]
            self.assertEqual(cv.itemcget(oval, "fill"), colour, key)
        self.assertEqual(beeps, [1])
        self.assertIn("SYSTEM STATUS REPORT",
                      self.app.paper.get("1.0", "end"))

    def test_letting_go_early_runs_no_test(self):
        """A hold is three seconds; a press is not a hold."""
        self.app._alarm_held_start()
        self.assertIsNotNone(self.app._alarm_hold)
        self.app._alarm_held_stop()
        self.assertIsNone(self.app._alarm_hold)

    def test_the_beeper_obeys_its_setup_step(self):
        """"System Beeper (Enable/Disable)", S53000. A console told to be
        quiet stays quiet."""
        self.assertTrue(self.app.beeper_enabled())
        self.c.values["S53000"] = "0"
        self.assertFalse(self.app.beeper_enabled())

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
        # Both halves stay on the screen in a FIXED "AM PM" order, which is
        # how the Setup Manual draws it and how a real console reads in the
        # video: the chosen half is marked by the cursor, not by its position.
        self.app.meridiem = "PM"
        self.app.buf, self.app.cur = "1234", 4
        self.assertEqual(self.app._lines()[1], "TIME: 12:34 AM PM")
        # With the cursor on the day-half and the blink on, it sits on the
        # first letter of the chosen one: "AM _M" is PM chosen.
        self.app.on_meridiem = True
        self.app._blink = True
        self.assertEqual(self.app._lines()[1], "TIME: 12:34 AM _M")
        self.app.meridiem = "AM"
        self.assertEqual(self.app._lines()[1], "TIME: 12:34 _M PM")
        self.app._blink = False
        self.app.meridiem = "PM"
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
                         if f["function"] == "COMMUNICATIONS SETUP"][0]
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


    def plld_pressure_screen(self):
        """Stand on the PRESSURE LINE LEAK DIAG pressure screen, line 1."""
        from tls350sim.ui import MODES, HEADER
        self.c.modules["plld"] = 1
        self.c.values["S78101"] = "011"          # a line at position 1
        self.c.values["S78201"] = "01LINE 1              "
        self.c.values["S78501"] = "0101"         # on tank 1
        self.c.tick()
        self.app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("PRESSURE LINE LEAK DIAG")
        self.app.step = HEADER
        self.app.device = 1
        self.app.confirm = None
        for i, screen in enumerate(self.app.steps()):
            if screen.get("live") == "line_pressure":
                self.app.step = i
                return self.app
        self.fail("the PLLD diagnostic has no pressure screen")

    def drawn(self):
        """What is PAINTED on the LCD, which is not the same question as what
        `_lines()` would say if anybody asked it."""
        return [self.app.lcd.itemcget(rid, "text").rstrip()
                for rid in self.app._text_ids]

    def poll(self, seconds):
        """One turn of the panel's own poll, `seconds` of console time on."""
        self.c.clock_offset += seconds
        self.app._poll()
        pending = getattr(self.app, "_poll_id", None)
        if pending:
            self.app.after_cancel(pending)
            self.app._poll_id = None

    def psi(self):
        """The number off line one of the pressure screen, as PAINTED."""
        import re
        found = re.search(r"([0-9]+\.[0-9]+)", self.drawn()[0])
        return float(found.group(1)) if found else None

    def test_a_live_screen_is_one_the_poll_redraws(self):
        """The poll redrew the display in Operating Mode alone, so a screen
        anywhere else showing a live value was painted once when you stepped
        onto it and never again -- indistinguishable from a console that has
        hung. A `live` token is the whole of the rule."""
        app = self.plld_pressure_screen()
        self.assertTrue(app._live_screen())
        from tls350sim.ui import MODES, HEADER
        app.mode = MODES.index("SETUP")
        app.func, app.step = 0, HEADER
        self.assertFalse(app._live_screen(),
                         "a stored setting cannot change without a keypress")

    def test_the_gross_test_runs_the_pump_seats_and_bleeds(self):
        """ENTER on this screen starts the Gross test on the line it is
        showing, and the screen IS the test:

          * the pump runs and the line goes to about thirty psi, varying the
            way a submersible's head does rather than sitting on one figure;
          * ten seconds later the check valve seats it back to the pressure
            relief valve's setpoint -- "when the line pressure drops to the
            pressure relief valve's setpoint, the valve closes", which is why
            every P1 in 577013-344's samples is 20-point-something;
          * and it bleeds slowly down from there.

        All three were modelled and none of them reached the panel: this
        reads the LCD rather than the model, and drives the console's own
        poll rather than calling the renderer by hand.
        """
        app = self.plld_pressure_screen()
        app._render()
        self.assertIn("PUMP OFF", self.drawn()[0])
        app.k_enter()
        self.assertIn("PUMP ON", self.drawn()[0])
        self.assertIn("RUNNING PUMP", self.drawn()[1])

        # the pump stage: a painted reading that moves, not one that sits still
        running = []
        for _ in range(4):
            self.poll(1.5)
            running.append(self.psi())
        self.assertGreater(len(set(running)), 1,
                           f"the pump pressure sat on one figure: {running}")
        for psi in running:
            self.assertGreater(psi, 25.0, running)

        # the check valve seats it, and the screen says so
        for _ in range(8):
            self.poll(1.5)
        seated = self.psi()
        self.assertIn("PUMP OFF", self.drawn()[0])
        self.assertIn("TEST 3.0", self.drawn()[1])
        self.assertLess(seated, min(running) - 5.0,
                        f"it never dropped to the seating pressure: {seated}")

        # and then it bleeds, slightly
        for _ in range(20):
            self.poll(3.0)
        after = self.psi()
        self.assertLess(after, seated, "it did not bleed after seating")
        self.assertGreater(after, seated - 1.0,
                           "a tight line should bleed SLIGHTLY")

    def recon(self, function):
        """Stand in Reconciliation Mode, on that function's first step."""
        from tls350sim.ui import MODES
        self.c.software["bir"] = True
        self.c.values["S53400"] = "100"      # delivery variance reports on
        # S615, Meter Data Present: what puts a tank into the reconciliation
        # at all. Set HERE rather than in `a_console`, because it is also a
        # setup-menu condition -- "This message appears only if you select
        # METER DATA PRESENT: YES" gates END FACTOR -- and turning it on for
        # the whole class moves the In-Tank Setup walk under the tests that
        # count steps.
        self.c.values["S61501"] = "011"
        # and a mapped meter, because "BIR will not produce reports while
        # the meter map is incomplete" and a console that has mapped
        # nothing has an incomplete one -- which is what the captured
        # hardware answers. See FIDELITY G8.
        self.c.values["S62F01"] = "011"          # a Mag probe to map to
        self.c.meters = {1: 1}
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
                         ["RECONCILIATION MODE".center(24),
                          "PRESS <FUNCTION> TO CONT"])

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

    def test_a_day_selector_names_a_day_and_not_a_status_message(self):
        """"SELECT DAY: (Date)" (576013-610 Rev AC p.28-3), and p.28-20
        draws the same screen as "SELECT DAY: (Current Date)".

        It read the selected ROW's closing date, so on a console with no
        previous day the screen carried a status message the display then
        cut mid-word: `SELECT DAY: NO DATA AVAI`. FIDELITY Q1.
        """
        from tls350sim.clock import clock_date
        self.recon("DISPLAY AND PRINT DLVY")
        self.app.step = 2
        today = clock_date(self.c.now())
        self.assertEqual(self.app._lines()[1], f"SELECT DAY: {today}")
        self.app.k_change()                    # CURRENT -> PREVIOUS
        self.assertEqual(self.app.sel["which"], "PREVIOUS")
        shown = self.app._lines()[1]
        self.assertNotIn("NO DATA", shown)
        self.assertTrue(shown.startswith("SELECT DAY: "), shown)

    def test_the_daily_reconciliation_report_takes_a_typed_date(self):
        """"To print a Reconciliation Report for the selected day, press
        PRINT. To select a different date, press CHANGE, enter the date, and
        then press ENTER" -- 576013-610 Rev AC p.28-3, where the three
        variance chapters say "press CHANGE, then press ENTER" and mean the
        previous one. One model was used for both. FIDELITY Q1.
        """
        import time
        self.recon("DISPLAY AND PRINT")
        self.app.k_change()                    # SHIFT -> DAILY
        self.app.step = 2
        self.app.k_change()                    # into the date template
        self.assertTrue(self.app.editing)
        self.assertEqual(self.app.sel["which"], "CURRENT",
                         "CHANGE typed a date here, it did not toggle")
        self.assertIn("/", self.app._lines()[1])
        for ch in "01021996":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(
            time.strftime("%m%d%Y", time.localtime(self.app.sel["day"])),
            "01021996")
        self.app.confirm = None
        self.assertEqual(self.app._lines()[1], "SELECT DAY: JAN  2, 1996")

    def test_a_variance_day_selector_is_still_a_toggle(self):
        """"To select the previous month and day (as defined by the
        reconciliation period), press CHANGE, then press ENTER", p.28-7."""
        self.recon("DISPLAY AND PRINT DLVY")
        self.app.step = 2
        self.app.k_change()
        self.assertFalse(self.app.editing)
        self.assertEqual(self.app.sel["which"], "PREVIOUS")

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

    def select_step(self, function):
        self.recon(function)
        return [st for st in self.app.steps()
                if st.get("sel") == "variance_period"][0]

    def test_and_the_select_step_offers_only_the_periods_it_turned_on(self):
        """FIDELITY G10. `S533` is one enable flag PER PERIOD -- the console
        prints it as `PERIODIC DISABLED / WEEKLY DISABLED / DAILY ENABLED`
        -- and the function already disappears when all three are off. It
        offered all three periods whichever ones the site had enabled, so a
        console could be stepped onto a report its own setup had turned
        off."""
        self.c.values["S53300"] = "100"          # periodic only
        step = self.select_step("BOOK VARIANCE")
        self.assertEqual(self.app._choices(step), ["PERIODIC"])
        self.c.values["S53300"] = "101"          # periodic and daily
        self.assertEqual(self.app._choices(step), ["DAILY", "PERIODIC"])
        self.c.values["S53300"] = "111"
        self.assertEqual(self.app._choices(step),
                         ["DAILY", "WEEKLY", "PERIODIC"])

    def test_the_step_cannot_be_left_showing_a_period_that_is_off(self):
        """A selection shared between functions can hold a value this one
        does not offer, and the panel only ever shows a choice it has."""
        self.c.values["S53300"] = "111"
        step = self.select_step("BOOK VARIANCE")
        self.app.sel["variance_period"] = "WEEKLY"
        self.c.values["S53300"] = "100"
        self.assertEqual(self.app._choice(step), "PERIODIC")

    def test_change_cycles_only_the_periods_that_are_on(self):
        self.c.values["S53300"] = "101"
        self.select_step("BOOK VARIANCE")
        self.app.step = [i for i, st in enumerate(self.app.steps())
                         if st.get("sel") == "variance_period"][0]
        self.app.sel["variance_period"] = "DAILY"
        self.app.k_change()
        self.assertEqual(self.app.sel["variance_period"], "PERIODIC")
        self.app.k_change()
        self.assertEqual(self.app.sel["variance_period"], "DAILY")


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
        self.operating("START PRESSURE LINE TEST")
        self.app.step = 1
        self.assertEqual(self.app._lines()[1], "3.0 GPH")
        self.operating("START LINE LEAK TEST")
        self.app.step = 1
        self.assertEqual(self.app._lines()[1], "0.20 GAL/HR")

    def test_starting_a_line_test_draws_the_line_and_not_a_count(self):
        """576013-610 Rev AC p.11-4: "Press ENTER to begin the test. The
        system displays the message:" over `Q #: RUNNING PUMP` and
        `PRESS <STEP> TO CONTINUE`.

        This drew `{n} TEST(S) STARTED` over the rate -- a tally of devices,
        which the console does not report. Its sibling walk for a Mag sump
        sensor has drawn `s 1: FILL SUMP` in the manual's shape all along.
        See FIDELITY U5.
        """
        self.operating("START PRESSURE LINE TEST")
        self.app.step = len(self.app.steps()) - 1
        self.app.armed = True
        self.app.k_enter()
        self.assertFalse(self.app.msg, "a count was flashed")
        self.assertEqual(self.app._lines(),
                         ["Q 1: RUNNING PUMP", "PRESS <STEP> TO CONTINUE"])

    def test_stopping_a_line_test_draws_the_line_too(self):
        """The same page's stop walk: `Q #: TEST ABORTED`. See FIDELITY U5."""
        self.c.leaks.start("plld", 1, "gross", None, False)
        self.operating("STOP PRESSURE LINE TEST")
        self.app.step = len(self.app.steps()) - 1
        self.app.armed = True
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["Q 1: TEST ABORTED", "PRESS <STEP> TO CONTINUE"])

    def test_stopping_when_nothing_runs_is_the_manuals_own_screen(self):
        """576013-610 Rev AC p.21-1: "If all active tests are stopped, the
        system displays the message: LEAK TEST NOT ACTIVE / PRESS <FUNCTION>
        TO CONTINUE." This said `NO TEST / RUNNING`. See FIDELITY U5."""
        self.operating("STOP IN-TANK LEAK TEST")
        self.app.step = len(self.app.steps()) - 1
        self.app.armed = True
        self.app.k_enter()
        self.assertEqual(self.app._lines()[0], "LEAK TEST NOT ACTIVE")

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
        # The roll is 24 characters, so this title wraps -- and it wraps on
        # real paper in exactly the same place, with the continuation flush
        # at column 1: a real 1996 tape prints "PRESSURE LINE LEAK TEST"
        # over "RESULTS", measured to within half a point of the line above.
        self.assertIn("PRESSURE LINE LEAK TEST" + chr(10) + "HISTORY", paper)
        self.assertIn("LAST 3.0 GAL/HR PASS:", paper)
        # Wraps too, and a real 1996 roll wraps it in the same place:
        # "FIRST 0.20 GAL/HR PASS" over "EACH MONTH:".
        self.assertIn("FIRST 0.20 GAL/HR PASS" + chr(10) + "EACH MONTH:",
                      paper)


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
        # "ADD VMC SERIAL NUMBER" over "x 1: 111111". It is a level down --
        # "Press ENTER." -- not the branch's next step.
        self.app.step = 0
        self.assertEqual(self.app._lines(),
                         ["ADD VMC SERIAL NUMBER", "PRESS <ENTER>"])
        self.app.k_enter()
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
        """Stand on the setup step whose text starts with `starts`.

        Setup Mode has levels: a screen that says PRESS <ENTER> holds the
        screens below it and they are not steps of the function. So this
        descends into every branch it passes, which is the walk a
        technician does, rather than reading one level and reporting the
        rest missing.
        """
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.app._entered = True
        self.app.confirm = None
        self.app.sub = None
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == function][0]
        return self._setup_walk(starts)

    def _setup_walk(self, starts, depth=0):
        path = list(self.app.subs)
        for step in range(len(self.app.steps())):
            self.app.subs = list(path)
            self.app.step = step
            if self.app.steps()[step]["text"].startswith(starts):
                # and PAINT it: a test that reads the canvas rather than
                # `_lines()` would otherwise be reading whatever the walk
                # last drew on its way here
                self.app._render()
                return self.app._lines()
            if depth < 3 and self.app._branch_at() is not None:
                self.app.k_enter()
                self.app.confirm = None
                found = self._setup_walk(starts, depth + 1)
                if found is not None:
                    return found
        self.app.subs = list(path)
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
        self.app.logbox.delete("1.0", "end")
        self.app.k_change()
        for ch in "020":
            self.app.k_alnum(ch)
        self.app.k_enter()
        # 30 to 180, so 20 is refused -- and the glass keeps the step it was
        # on rather than gaining a screen no console has. See FIDELITY U5.
        self.assertIn("entry refused", self.app.logbox.get("1.0", "end"))
        self.assertEqual(self.app._lines()[1], "WATER ALARM DELAY: 180S")

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
        # the twelve limit screens are two levels down: p.6-5 descends from
        # AUTO TRANSMIT SETUP to TRANSMIT MESSAGE SETUP and again to these.
        self.setup_step("COMMUNICATIONS SETUP", "Auto Leak Alarm")
        texts = [st["text"] for st in self.app.steps()]
        self.assertIn("Auto Leak Alarm Limit", texts)
        self.assertIn("Auto Sensor Out Alarm", texts)       # all twelve
        # and the two times are siblings of TRANSMIT MESSAGE SETUP one level
        # up, not offered anywhere until something is set to transmit
        self.assertIsNone(self.setup_step("COMMUNICATIONS SETUP",
                                          "Auto Delay Time"))
        self.setup_step("COMMUNICATIONS SETUP", "Auto Leak Alarm")
        self.app.k_change()                                  # TRANSMIT
        self.assertEqual(self.app._lines(),
                         ["AUTO LEAK ALARM LIMIT", "TRANSMIT"])
        self.assertEqual(self.setup_step("COMMUNICATIONS SETUP",
                                         "Auto Delay Time"),
                         ["AUTO TRANSMIT MESSAGE", "AUTO DELAY TIME: 005"])
        texts = [st["text"] for st in self.app.steps()]
        self.assertNotIn("Auto Repeat Time", texts)
        self.setup_step("COMMUNICATIONS SETUP", "Auto Leak Alarm")
        self.app.k_change()                                  # TRANSMIT/REPEAT
        self.assertEqual(self.setup_step("COMMUNICATIONS SETUP",
                                         "Auto Repeat Time"),
                         ["AUTO TRANSMIT MESSAGE", "AUTO REPEAT TIME: 060"])

    def test_no_press_enter_screen_in_setup_is_inert(self):
        """The key the screen names is the key that has to work.

        Every `PRESS <ENTER>` screen in Setup Mode used to do nothing on
        ENTER and hand its child to STEP, which is a key sequence the real
        console does not use: 576013-623 Rev AN says "Press ENTER:" over
        every one of these figures. Diagnostic Mode already descended; this
        is the same mechanism in the mode that has 360 of the steps. See
        the setup-mode audit, SU12, and CLOSED U37 for the diagnostic one.
        """
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        inert = []
        for fi, fn in enumerate(self.app.functions()):
            self.app.reset_panel()
            self.app.mode, self.app.func = MODES.index("SETUP"), fi
            inert += self._enter_walk(fn["function"])
        self.assertEqual(inert, [])

    def _enter_walk(self, function, depth=0):
        """-> the PRESS <ENTER> screens on this level ENTER does nothing to."""
        bad, path = [], list(self.app.subs)
        for si in range(len(self.app.steps())):
            self.app.subs = list(path)
            self.app.step, self.app.confirm = si, None
            before = (self.app._lines(), si, list(path))
            if before[0][1].strip() != "PRESS <ENTER>":
                continue
            self.app.k_enter()
            after = (self.app._lines(), self.app.step, list(self.app.subs))
            # `_lines()` is blind to the ISD mapping flows, which `_render`
            # paints straight onto the canvas, so a started flow counts as
            # ENTER having done something.
            if after == before and not self.app.isdflow:
                bad.append(f"{function}: {before[0][0]}")
            self.app.isdflow = None
            if depth < 3 and self.app.subs != path:
                bad += self._enter_walk(function, depth + 1)
        self.app.subs = list(path)
        return bad

    def test_a_setup_branch_can_hold_a_branch(self):
        """p.6-5: ENTER on AUTO TRANSMIT SETUP shows TRANSMIT MESSAGE SETUP,
        and ENTER on that shows the twelve AUTO ... LIMIT screens. One index
        cannot say where the panel is standing on a page like that."""
        self.setup_step("COMMUNICATIONS SETUP", "Auto Transmit Setup")
        self.assertEqual(self.app._lines(),
                         ["AUTO TRANSMIT SETUP", "PRESS <ENTER>"])
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["TRANSMIT MESSAGE SETUP", "PRESS <ENTER>"])
        self.app.k_enter()
        self.assertEqual(self.app._lines(),
                         ["AUTO LEAK ALARM LIMIT", "DISABLED"])
        self.assertEqual(len(self.app.subs), 2)
        # "When you have finished selecting Auto Transmit for the above
        # items, press STEP until you see TRANSMIT MESSAGE SETUP message",
        # and then "Press STEP to return to the AUTO TRANSMIT SETUP
        # message": the last screen of a branch steps back OUT of it.
        for _ in range(11):
            self.app.k_step()
        self.assertEqual(self.app._lines()[0], "AUTO SENSOR OUT ALARM")
        self.app.k_step()
        self.assertEqual(self.app._lines(),
                         ["TRANSMIT MESSAGE SETUP", "PRESS <ENTER>"])
        self.app.k_step()
        self.assertEqual(self.app._lines(),
                         ["AUTO TRANSMIT SETUP", "PRESS <ENTER>"])
        self.app.k_step()
        self.assertNotEqual(self.app._lines()[0], "AUTO TRANSMIT SETUP")

    def test_backup_out_of_a_setup_branch_lands_on_its_own_parent(self):
        """"BACKUP will move through the hierarchy of commands", and the
        screen that offered a branch is one rung of that hierarchy."""
        self.setup_step("VMC SETUP", "Edit VMC Serial Number")
        self.assertEqual(self.app._lines(),
                         ["EDIT VMC SERIAL NUMBER", "PRESS <ENTER>"])
        self.app.k_enter()
        self.assertEqual(self.app._lines(), ["EDIT VMC SERIAL NUMBER", "x 1:"])
        self.app.k_backup()
        self.assertEqual(self.app._lines(),
                         ["EDIT VMC SERIAL NUMBER", "PRESS <ENTER>"])
        self.assertEqual(self.app.subs, [])
        # and not onto the first branch of the function, which is what
        # counting the parent off the raw step list would have given
        self.app.k_backup()
        self.assertEqual(self.app._lines(),
                         ["ADD VMC SERIAL NUMBER", "PRESS <ENTER>"])

    def test_the_relay_walk_asks_the_seventeen_group_questions(self):
        """576013-623 Rev AN ch.24 walks one screen per family of alarm.

        All four of these functions used to draw ONE step, whose second
        line was `RELAY ASSIGNMENTS - FOR ` -- the chapter-3 Setup Mode
        Programming Table's own row for OUTPUT RELAY SETUP, clipped to
        twenty-four columns and imported as though it were a screen. A
        table of contents is not a screen. See FIDELITY U6.
        """
        self.setup_step("OUTPUT RELAY SETUP", "Relay Assignments - In-Tank")
        self.assertEqual(self.app._lines(), ["R1:", "IN-TANK ALARMS: NO"])
        # "Continue to step through all of the available alarm groups",
        # p.24-5, and the order is the chapter's own.
        seen = []
        while True:
            line = self.app._lines()[1]
            if not line.endswith((": NO", ": YES")) or line in seen:
                break
            seen.append(line)
            self.app.k_step()
        self.assertEqual(seen[:5], ["IN-TANK ALARMS: NO",
                                    "LIQUID SENSOR ALMS: NO",
                                    "VAPOR SENSOR ALMS: NO",
                                    "EXTERNAL INPUTS: NO",
                                    "LINE LEAK ALARMS: NO"])
        self.assertEqual(seen[-2:], ["VMCI ALARM: NO", "VMC ALARM: NO"])
        # "Only installed components will display, so some of the alarm
        # groups may not appear" -- p.24-5. This cage has no DIM of either
        # side, so two of the seventeen are not on it.
        self.assertEqual(len(seen), 15)
        self.assertNotIn("POWER SIDE DIM ALM: NO", seen)
        self.assertNotIn("COMM SIDE DIM ALM: NO", seen)

    def test_the_line_disable_walk_is_headed_by_its_own_line(self):
        """p.25-1: "Whenever the prefix 'Q' appears in the display, it
        stands for the selected line in the PLLD system, 'W' stands for the
        selected line in the WPLLD system and 'P' stands for the selected
        line in the VLLD system." Two of the three were headed `T`, so the
        function that says which alarms shut a line down named a TANK."""
        for function, letter in (("PLLD LINE DISABLE SETUP", "Q"),
                                 ("WPLLD LINE DISABLE SETUP", "W"),
                                 ("VLLD LINE DISABLE SETUP", "P")):
            self.setup_step(function, "Line Disable - In-Tank")
            self.assertEqual(self.app._lines(),
                             [f"{letter}1:", "IN-TANK ALARMS: NO"])

    def test_a_group_says_yes_when_the_wire_has_assigned_one(self):
        """"you will first specify whether you want to assign an available
        alarm type ... by choosing Yes or No for that type of alarm",
        p.24-3 -- so the answer is not a second store, it is whether that
        category has anything on the relay's list. And turning it back to
        NO takes the category off: a screen reading NO over a list that
        still drove the coil would be a screen that lies."""
        self.c.assign_relay_alarm(1, "02", "02", "00", True)
        self.setup_step("OUTPUT RELAY SETUP", "Relay Assignments - In-Tank")
        self.assertEqual(self.app._lines(), ["R1:", "IN-TANK ALARMS: YES"])
        self.app.k_change()
        self.assertEqual(self.app._lines(), ["R1:", "IN-TANK ALARMS: NO"])
        self.assertEqual(self.c.relay_alarms[1], [])
        # and the relay beside it was never asked about
        self.assertEqual(self.c.setting("relay_alm_intank", 2, "NO"), "NO")

    def test_the_meter_map_row_dials_and_writes_where_7b1_writes(self):
        """576013-623 Rev AN p.17-5. `MODIFY TANK/METER MAP / PRESS <ENTER>`
        had nothing behind it: the five-field row the page draws under
        `BUS SLOT FUEL METER TANK` was a screen this console did not have,
        so the one panel route to the tank/meter map did not exist and BIR
        could only be told about a probeless tank over the wire."""
        self.c.software["bir"] = True
        self.setup_step("RECONCILIATION SETUP", "Modify Tank/Meter Map")
        self.assertEqual(self.app._lines(),
                         ["MODIFY TANK/METER MAP", "PRESS <ENTER>"])
        self.app.k_enter()
        self.app._blink = False
        self.app._render()
        self.assertEqual(self.app._lines(),
                         ["BUS SLOT FUEL METER TANK",
                          "X    XX   XX    XX    XX"])
        # "Press [arrow] to move to the field you want to change. Then
        # press CHANGE until the correct choice appears."
        self.app.k_change()                        # BUS: X -> 2
        self.app.k_change()                        # BUS: 2 -> 3
        self.app.k_alnum(",")
        self.app.k_change()                        # SLOT: XX -> 01
        self.app.k_alnum(",")
        for _ in range(19):
            self.app.k_change()                    # FUEL: 00 .. 18
        self.app.k_alnum(",")
        self.app.k_change()                        # METER: XX -> 00
        self.app.k_change()                        # METER: 00 -> 01
        self.app.k_alnum(",")
        self.app.k_change()
        self.app.k_change()                        # TANK: XX -> 00 -> 01
        self.app._blink = False
        self.app._render()
        self.assertEqual(self.app._lines()[1], "3    01   18    01    01")
        self.app.k_enter()
        # the manual's own worked command, `S7B100 3 1 18 1 1`, dialled in
        from tls350sim.meterid import MeterId
        from tls350sim.ui import CONT_STEP
        self.assertEqual(self.c.meter_map.get(MeterId(3, 1, 18, 1)),
                         {"tank": 1, "locked": True})
        self.assertEqual(self.app._lines(),
                         ["3    01   18    01    01", CONT_STEP])

    def test_the_meter_map_row_will_not_take_a_slot_off_another_bus(self):
        """"9 - 16 ... Slots on Type 2 bus", "1 - 3 ... Slots on Type 3 bus",
        and the wire judges a slot against its own bus. The keypad must not
        be able to store what the serial port would refuse."""
        self.c.software["bir"] = True
        self.setup_step("RECONCILIATION SETUP", "Modify Tank/Meter Map")
        self.app.k_enter()
        self.app.k_change()                        # BUS: X -> 2, the power bay
        self.app.k_alnum(",")
        self.app.k_change()                        # its first slot
        self.assertEqual(self.app.buf.split()[1], "09")
        self.app.k_alnum("+")
        self.app.k_change()                        # BUS: 2 -> 3, the comm bay
        # slot 09 is not a slot the comm bay has, so it goes back to X
        # rather than standing there as a row the command would reject
        self.assertEqual(self.app.buf.split()[1], "XX")

    def test_print_on_the_meter_map_row_prints_the_map(self):
        """"Press PRINT to output a report of the current meter map for
        reference before proceeding" -- p.17-5, said of this screen and of
        no other screen in Setup Mode."""
        self.c.software["bir"] = True
        self.c.map_meter(3, 1, 18, 1, 1)
        self.c.map_meter(3, 1, 18, 3, -1)
        self.setup_step("RECONCILIATION SETUP", "Modify Tank/Meter Map")
        self.app.k_enter()
        self.app.k_print()
        paper = self.app.paper.get("1.0", "end")
        # the paper's own 24-column block, headed the way the tape heads
        # it, and not the wire's 34-column one wrapped into nonsense
        self.assertIn("BUS SLOT FUEL METER TANK", paper)
        self.assertIn("3    01   18    01    01", paper)
        self.assertIn("3    01   18    03    99", paper)
        self.assertNotIn("RECONCILIATION SETUP", paper)

    def test_the_meter_offset_row_is_one_screen_a_meter_and_writes_7b4s_store(self):
        """p.17-6. The row drew a BLANK second line where the page draws
        `XX     XX    XX   +X.XX`, there was one of it where the page says
        "Press STEP to continue to the next meter", and ENTER stored eleven
        characters under `S7B400` in `values` -- which nothing reads, where
        BIR reads `meter_offsets`."""
        from tls350sim.ui import CONT_STEP
        self.c.software["bir"] = True
        self.c.values["S51C00"] = "1"                # ticketed delivery
        self.c.map_meter(3, 1, 18, 1, 1)
        self.c.map_meter(3, 1, 18, 3, -1)
        self.setup_step("RECONCILIATION SETUP", "Individual Meter Offset")
        self.app.k_enter()
        self.app._blink = False
        self.app._render()
        self.assertEqual(self.app._lines(),
                         ["FUEL METER TANK OFFSET", "18     01    01   +0.00"])
        self.assertEqual(len(self.app.steps()), 2)   # one screen a meter
        self.app.k_step()
        self.app._blink = False
        self.app._render()
        # "99: Tank with no probe" is the screen's word for the -1 the
        # command takes
        self.assertEqual(self.app._lines()[1], "18     03    99   +0.00")
        self.app.k_backup()
        self.app.k_enter()
        self.app.k_change()
        for ch in "0,25":                            # "," is the decimal key
            self.app.k_alnum(ch)
        self.app.k_alnum("+")                        # and "+" is the sign
        self.app._blink = False
        self.app._render()
        self.assertEqual(self.app._lines()[1], "18     01    01   -0.25")
        self.app.k_enter()
        self.assertEqual(self.c.meter_offsets[(18, 1)],
                         {"fp": 18, "tank": 1, "pct": -0.25})
        self.assertEqual(self.c.meter_offset((18, 1)), -0.25)
        self.assertEqual(self.app._lines(),
                         ["18     01    01   -0.25", CONT_STEP])

    def test_a_meter_offset_over_the_pages_range_is_refused(self):
        """"a maximum range of +/-9.99%", and the wire's `_offset_ok` holds
        the same rule from the other side."""
        self.c.software["bir"] = True
        self.c.values["S51C00"] = "1"
        self.c.map_meter(3, 1, 18, 1, 1)
        self.setup_step("RECONCILIATION SETUP", "Individual Meter Offset")
        self.app.k_enter()
        self.app.k_change()
        for ch in "10,00":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.c.meter_offsets, {})

    def test_a_tank_nobody_has_picked_reads_NONE(self):
        """`SX: SELECT TANK` over `NONE` (p.15-3), `P#: SELECT NBP PARTNER`
        over `NONE` (p.12-6), `Q1: (Pressure Line Label)` over `NONE`
        (p.10-7) and `s 1: SELECT PUMP #` over `NONE` (p.26-4). All four
        drew the number behind the word rather than the word -- and a bare
        number is a screen a citation cannot find a page for, which is how
        the first two were noticed at all. SU18 and SU27."""
        self.c.values["S72301"] = "0104"          # sensor 1 is a Vac sensor
        self.assertEqual(
            self._setup_step("PUMP SENSOR SETUP", "SELECT TANK")._lines(),
            ["S1: SELECT TANK", "NONE"])
        self.assertEqual(
            self._setup_step("LINE LEAK DETECTOR SETUP",
                             "SELECT NBP PARTNER")._lines(),
            ["P1: SELECT NBP PARTNER", "NONE"])
        self.assertEqual(
            self._setup_step("SMART SENSOR SETUP", "SELECT PUMP")._lines(),
            ["s 1: SELECT PUMP #", "NONE"])
        # and the PLLD one wears no prompt, because its page draws none
        self.c.values["S78201"] = "01MAIN LINE           "
        self.assertEqual(
            self._setup_step("PRESSURE LINE LEAK SETUP",
                             "Select Tank")._lines(),
            ["Q1: MAIN LINE", "NONE"])

    def test_a_generator_input_can_be_told_which_tanks_feed_it(self):
        """"You must identify which tanks supply fuel to the generator, so
        that the system will conduct a continuous leak test in these tanks
        while the generator is off" -- p.23-3. GENERATOR and PUMP SENSE
        both ended at their own type screen, so an input could be wired and
        named and not told what it was watching. SU23."""
        self.c.values["S80C01"] = "0121"              # GENERATOR
        app = self._setup_step("EXTERNAL INPUT SETUP", "Generator Tank")
        self.assertEqual(app._lines(), ["I1: SELECT TANK", "TANK #: ALL TANK"])
        # "If only one or some of the tanks connected to the system supply
        # fuel to this generator, enter the individual tank numbers", and
        # the screen under that sentence is `TANK #: X, X` -- a RUN, not a
        # number. CHANGE puts the run's own template on the glass, the way
        # SIPHON MANIFOLDED's does, and the right arrow steps over a comma.
        app.k_change()
        self.assertEqual(app.buf, "00,00,00,00")
        for ch in "02":
            app.k_alnum(ch)
        app.k_alnum(",")                              # the right-arrow key
        for ch in "03":
            app.k_alnum(ch)
        app.k_enter()
        self.assertEqual(self.c.values["S80C01"], "012102,03,00,00")
        self.assertEqual(self.c.inputs.tanks_of(1), [2, 3])
        app.confirm = None
        app._render()
        self.assertEqual(app._lines(),
                         ["I1: SELECT TANK", "TANK #: 02,03,00,00"])

    def test_a_pump_sense_input_gets_the_switchover_block(self):
        """p.23-4 and p.23-5, which are p.15-3's screens on another
        function: a tank, a dispense mode, and the switchover pair gated on
        the two manifolded-alternate modes. `S81201` and `S81301` carried
        p.23-5's own ranges and no step named either."""
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU
              if f["function"] == "EXTERNAL INPUT SETUP"][0]
        self.c.values["S80C01"] = "0131"              # PUMP SENSE
        app = self._setup_step("EXTERNAL INPUT SETUP", "Pump Sense Tank")
        self.assertEqual(app._lines(), ["I1: SELECT TANK", "NONE"])
        self.c.values["S80401"] = "012"               # MANIFOLDED: ALTERNATE
        texts = [st["text"] for st in self.c.visible_steps(fn, 1)]
        self.assertIn("Input Switchover Volume", texts)
        self.assertNotIn("Input Switchover Height", texts)
        self.c.values["S80401"] = "015"               # ALTERNATE-HT
        texts = [st["text"] for st in self.c.visible_steps(fn, 1)]
        self.assertIn("Input Switchover Height", texts)
        self.assertNotIn("Input Switchover Volume", texts)
        self.assertEqual(
            self._setup_step("EXTERNAL INPUT SETUP",
                             "Input Switchover Height")._lines(),
            ["I1: SWITCHOVER HEIGHT", "THRESHOLD: 0002.0"])
        # and the dispense mode reads the way the pump sensor's does,
        # which is the same page's list
        self.assertEqual(
            self._setup_step("EXTERNAL INPUT SETUP",
                             "Input Dispense Mode")._lines(),
            ["I1: ENTER DISPENSE MODE", "MANIFOLDED: ALTERNATE-HT"])

    def test_every_external_input_type_can_be_set_normally_closed(self):
        """p.23-2 draws `I1: SELECT ORIENTATION` / `NORMALLY OPEN` with no
        gate and only then branches on the type, so the screen belongs to
        all of them -- and a normally-closed STANDARD input is the
        commonest external input there is. It was offered on STANDARD ACK
        alone, because the FIELD was an enum of that type's own pair
        rather than the orientation digit every type carries. SU22."""
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU
              if f["function"] == "EXTERNAL INPUT SETUP"][0]
        for kind in ("11", "21", "31", "41", "51"):
            self.c.values["S80C01"] = "01" + kind
            texts = [st["text"] for st in self.c.visible_steps(fn, 1)]
            self.assertIn("Select Orientation (NO/NC)", texts, kind)
        # and CHANGE writes the second character, leaving the type alone
        self.c.values["S80C01"] = "0111"              # STANDARD
        app = self._setup_step("EXTERNAL INPUT SETUP", "SELECT ORIENTATION")
        self.assertEqual(app._lines()[1], "NORMALLY OPEN")
        app.k_change()
        app.k_enter()
        self.assertEqual(self.c.values["S80C01"], "0112")

    def test_the_smart_sensor_categories_are_the_chapters_own_words(self):
        """p.26-2 heads this `s 1: SELECT SS CATEGORY` and puts the
        category's own name under it -- AIR FLOW METER, VAPOR PRESSURE,
        MAG SENSOR, VAC SENSOR, ATMP SENSOR, UNKNOWN. This console drew a
        bare label line, a `CATEGORY: ` prefix the page does not have, and
        723's Notes' prose, three words of which were clipped mid-word.
        SU26."""
        app = self._setup_step("SMART SENSOR SETUP", "Select SS Category")
        words = [app._lines()]
        for _ in range(6):
            app.k_change()
            app._blink = False
            app._render()
            words.append(app._lines())
        self.assertEqual(words, [
            ["s 1: SELECT SS CATEGORY", "UNKNOWN"],
            ["s 1: SELECT SS CATEGORY", "AIR FLOW METER"],
            ["s 1: SELECT SS CATEGORY", "VAPOR PRESSURE"],
            ["s 1: SELECT SS CATEGORY", "MAG SENSOR"],
            ["s 1: SELECT SS CATEGORY", "VAC SENSOR"],
            ["s 1: SELECT SS CATEGORY", "ATMP SENSOR"],
            # `08`, which p.26-2's six do not include and this does not
            # rename off a page that does not name it
            ["s 1: SELECT SS CATEGORY", "VAPOR VALVE"]])

    def test_a_pipe_with_two_diameters_gets_a_length_for_each(self):
        """576013-623 Rev AN p.10-3 and p.11-3 both change what the LINE
        LENGTH screen IS when the pipe has two diameters. This console drew
        one ungated `LINE LENGTH` for all nineteen types, so choosing a
        dual pipe changed nothing and the second diameter's length -- which
        the leak algorithm needs -- could only be set over the wire. Every
        field involved already existed and no step named it. SU15 and SU17.
        """
        from tls350sim.console import SETUP_MENU
        rows = []
        for code, function, types in (
                ("S78801", "PRESSURE LINE LEAK SETUP",
                 ("03", "07", "01")),
                ("S7A801", "WPLLD LINE LEAK SETUP", ("03", "01"))):
            for kind in types:
                self.c.values[code] = "01" + kind
                fn = [f for f in SETUP_MENU
                      if f["function"] == function][0]
                rows.append([st.get("l2") for st
                             in self.c.visible_steps(fn, 1)
                             if "LEN" in (st.get("l2") or "")])
        self.assertEqual(rows, [
            ["LINE LENGTH:"],                       # a single-size pipe
            ["1.5 IN DIAM. LEN:", "LINE LENGTH:"],  # a dual flexible one
            ["2.0 IN DIAM. LEN:", "3.0 IN DIAM. LEN:"],     # fiberglass
            ["LINE LENGTH:"],
            ["2.0 IN DIAM. LEN:", "3.0 IN DIAM. LEN:"]])

    def test_a_user_defined_pipe_has_its_two_diameters(self):
        """p.10-4 walks USER DEFINED through two lengths, two DIAMETERS,
        two bulk moduli and a thermal coefficient. Five of the seven were
        here; `S77701` and `S77801` were fields no step named, which
        576013-635 Rev AA's `777` display format calls `1ST LINE
        DIAMETER`. SU16."""
        self.c.values["S78801"] = "0118"
        self.c.values["S78201"] = "01MAIN LINE           "
        app = self._setup_step("PRESSURE LINE LEAK SETUP", "1st Line Diameter")
        self.assertEqual(app._lines(),
                         ["Q1: MAIN LINE", "1ST LINE DIAMETER: 0.00"])
        app = self._setup_step("PRESSURE LINE LEAK SETUP", "2nd Line Diameter")
        self.assertEqual(app._lines()[1], "2ND LINE DIAMETER: 0.00")

    def test_the_evr_setup_has_the_branches_its_figure_draws(self):
        """577013-800 Rev P p.20-13's Figure 7 has four branches at the top
        level and two more inside one of them; this function walked eleven
        value screens in a line. The consequence a trainee meets first is
        that the airflow meter's screen and the pressure sensor's were
        indistinguishable -- both `LABEL:` over `SN#: DISABLED` -- because
        the screen that says which device you are on was one of the six
        missing ones. SU29."""
        app = self._setup_step("EVR/ISD SETUP", "Airflow Meter Select",
                               prompt=True)
        self.assertEqual(app._lines(),
                         ["AIRFLOW METER SELECT", "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines()[1], "SN#:            DISABLED")
        app.k_backup()
        app.k_step()
        self.assertEqual(app._lines(),
                         ["PRESSURE SENSOR SELECT", "PRESS <ENTER>"])
        # and the branch that holds two branches of its own
        app.k_step()
        self.assertEqual(app._lines(),
                         ["FUEL HOSE TABLE SETUP", "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines(),
                         ["EDIT FUEL HOSE LABELS", "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines()[0], "HOSE LABEL 1")
        app.k_backup()
        app.k_step()
        self.assertEqual(app._lines(), ["EDIT FUEL HOSE 1", "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines(), ["h1: FUEL POS LABEL", "01"])

    def test_the_airflow_meter_screen_names_the_device_it_is_on(self):
        """`SN#: (10 char)       DISABLED`, 577013-800 Rev P Figure 7, and
        it is exactly twenty-four columns with a ten character serial in
        the middle of it. The console HAS that serial -- V43's index table
        answers with it -- and built it inline where nothing else could
        reach it, so the panel drew `SN#:` with nothing after it on a
        console that could answer the same question over the port.

        And the ENABLE/DISABLE is V43's own in-use flag now. It was a panel
        setting of its own beside the wire's, so a technician who enabled an
        air flow meter on the glass left V43 answering that it was not in
        use -- which is what ISD raises MISSING VAPOR FLOW MTR against.
        FIDELITY F9's shape. See CLOSED I11a."""
        from tls350sim import wiresensors
        self.c.values["S72301"] = "01"                 # AIR FLOW METER
        self.c.values["S72302"] = "02"                 # VAPOR PRESSURE
        self.c.values["S72201"] = "VAPOR FLOW MTR 1"
        app = self._setup_step("EVR/ISD SETUP", "Airflow Meter Select",
                               prompt=True)
        app.k_enter()
        serial = wiresensors.isd_serial(self.c, 1)
        self.assertEqual(app._lines(),
                         ["LABEL: VAPOR FLOW MTR 1",
                          f"SN#: {serial} DISABLED"])
        self.assertEqual(len(app._lines()[1]), 24)
        # "Press Tank to view the next airflow meter" -- and the next
        # airflow meter is not the next smart sensor. Position 2 is the
        # vapour pressure sensor and is not on this walk.
        self.assertEqual(app._devices(), [1])
        app.k_change()
        app.k_enter()
        self.assertTrue(wiresensors.isd_in_use(self.c, 1))
        self.assertEqual(self.c.values["SV4301"], "1")

    def test_adding_a_fuel_hose_still_lands_on_its_own_label_screen(self):
        """That screen is two levels down now, and the flow that drops the
        operator onto it searched one. A search that assumes one level is a
        search that breaks the day a level is added."""
        app = self._setup_step("EVR/ISD SETUP", "Add New Fuel Hose",
                               prompt=True)
        self.assertEqual(app._lines()[1], "PRESS <ENTER>")
        app.k_enter()
        app._blink = False
        app._render()
        self.assertEqual(app._lines(), ["h1: FUEL POS LABEL", "01"])

    def test_the_sensor_categories_are_the_words_five_chapters_draw(self):
        """Four of the five category lists came from 709's Notes, which are
        prose about the wire value -- `MONITORING WELL`, clipped mid-word
        at 24 columns to `CATEGORY: MONITORING WEL`. Chapters 18, 19, 20
        and 22 all draw the same list and all draw `MONITOR WELL`, which
        fits, and the liquid sensor already had it. SU21."""
        for function in ("LIQUID SENSOR SETUP", "VAPOR SENSOR SETUP",
                         "GROUNDWATER SENSOR SETUP", "2-WIRE CL SENSOR SETUP",
                         "3-WIRE CL SENSOR SETUP"):
            app = self._setup_step(function, "Category")
            words = [app._lines()[1]]
            for _ in range(5):
                app.k_change()
                app._blink = False
                app._render()
                words.append(app._lines()[1])
            self.assertEqual(
                words,
                ["CATEGORY: OTHER SENSORS", "CATEGORY: ANNULAR SPACE",
                 "CATEGORY: DISPENSER PAN", "CATEGORY: MONITOR WELL",
                 "CATEGORY: STP SUMP", "CATEGORY: PIPING SUMP"], function)

    def test_a_field_whose_range_starts_above_zero_does_not_draw_zero(self):
        """`DIAL RETRY NUMBER` is "a number between 3 and 99" and drew `0`;
        `DELIVERY DELAY` has a minimum of 1 and drew `00` where p.7-25
        draws `01`. Two screens asserting a value the console would not
        take. SU31."""
        self.c.modules["modem"] = 1              # the receiver screens
        self.assertEqual(
            self._setup_step("COMMUNICATIONS SETUP",
                             "Retry Number")._lines()[1],
            "DIAL RETRY NUMBER: 03")
        self.assertEqual(
            self._setup_step("IN-TANK SETUP", "Delivery Delay")._lines()[1],
            "DELIVERY DELAY: 01")
        # and a signed field still rests at zero, which is why this lives
        # on the two fields and not in `shown()`
        self.assertEqual(
            self._setup_step("IN-TANK SETUP", "Tank Tilt")._lines()[1],
            "TANK TILT: +000.00")

    def test_the_printout_screens_are_in_the_manuals_order(self):
        """p.5-16 ends on RE-DIRECT LOCAL PRINTOUT and p.5-17 opens on QPLD
        MONTHLY PRINTOUT. The list was in function-code order, and the
        manual's order is not function-code order at this point -- and the
        two are gated differently, so on a console without BIR the visible
        order is identical either way and the defect is invisible. SU3."""
        self.c.software["bir"] = True
        app = self._setup_step("SYSTEM SETUP", "Re-direct Local")
        texts = [st["text"] for st in app.steps()]
        where = [i for i, t in enumerate(texts)
                 if t.startswith("Re-direct Local")][0]
        qpld = [i for i, t in enumerate(texts) if "QPLD" in t][0]
        self.assertLess(where, qpld)

    def test_an_archive_save_comes_back_to_the_function_screen(self):
        """"When the save is completed, the system returns the original
        message: ARCHIVE UTILITY / PRESS <STEP> TO CONTINUE" -- p.28-2, and
        the same sentence for the restore on p.28-3. It came back to the
        step the save was started from. SU30."""
        from tls350sim.ui import CONT_STEP
        app = self._setup_step("ARCHIVE UTILITY", "Save Setup Data")
        app.k_change()                      # SAVE SETUP DATA: YES
        app.k_enter()
        app.k_step()                        # ARE YOU SURE?: NO
        app.k_change()
        app.k_enter()
        app.k_step()                        # and the save runs
        self.assertEqual(app._lines(), ["ARCHIVE UTILITY", CONT_STEP])

    def test_the_wpplld_can_be_pointed_at_a_tank(self):
        """576013-623 Rev AN p.11-6 draws `W1: SELECT TANK` / `NONE` and
        this console had nine steps where chapter 11 has ten. It is the
        screen that lets a WPLLD act as a pump sense input for CSLD and
        automatic in-tank leak detection, and `7A5` was readable and
        writable over the wire all along -- only the panel screen was
        absent, which is a shape the serial census cannot see. SU13."""
        self.c.modules["wplld"] = 1
        self.c.values["S7A201"] = "01WIRELESS LINE       "
        app = self._setup_step("WPLLD LINE LEAK SETUP", "SELECT TANK")
        self.assertEqual(app._lines(), ["W1: SELECT TANK", "NONE"])
        # "NOTE: This option will only appear if a tank has been selected
        # for the WPLLD", and the chapter puts PRESSURE OFFSET above it
        texts = [st["text"] for st in app.steps()]
        self.assertNotIn("Dispense Mode", texts)
        self.assertLess(texts.index("Pressure Offset Value"),
                        [i for i, t in enumerate(texts)
                         if t.startswith("Select Tank")][0])
        self.c.values["S7A501"] = "0101"
        self.assertIn("Dispense Mode", [st["text"] for st in app.steps()])

    def test_the_vlld_wait_mode_and_pumpside_test_read_like_the_page(self):
        """`WAIT MODE: TEMP. MEAS.` and `PUMPSIDE TEST: ENABLED`, both
        drawn that way on p.12-5 and both confirmed by their own function
        codes' display formats. The console had the Notes' prose spellings,
        clipped mid-word at 24 columns, and no default at all on the
        pumpside test -- so a safety test that is on from the factory was
        off here. SU19 and SU20."""
        self.assertEqual(
            self._setup_step("LINE LEAK DETECTOR SETUP",
                             "Wait Mode")._lines()[1],
            "WAIT MODE: TEMP. MEAS.")
        self.app.k_change()
        self.app._blink = False
        self.app._render()
        # 26 columns on a 24-column display, so the console clips it --
        # which is what it does with every one of p.10-3's pipe types and
        # what AUDIT.md's `clip` rule is for. Shortening the manual's own
        # words to make them fit would be inventing console text.
        self.assertEqual(self.app._lines()[1], "WAIT MODE: VOL. CHG. MEA")
        self.assertEqual(
            self._setup_step("LINE LEAK DETECTOR SETUP",
                             "Pumpside Test")._lines()[1],
            "PUMPSIDE TEST: ENABLED")

    def test_every_setup_step_does_something(self):
        """A step you can only walk past is a step that is not finished."""
        from tls350sim.console import SETUP_MENU
        idle = [st["text"] for fn in SETUP_MENU for st in fn["steps"]
                if not (st.get("code") or st.get("console") or st.get("body")
                        or st.get("archive") or st.get("profile")
                        or st.get("point") or st.get("vmc"))]
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
        # ENTER lands on the FIRST SCREEN of the branch, which is where
        # Figure 6-2's `E` arrow points -- not on a second copy of the
        # screen that offered it. DG3.
        self.assertIn("SLOT 1", self.app._diag_screens(
            self.app.cur_function())[0]["text"])
        self.assertIn("SLOT 1", self.app._lines()[0])
        self.app.step = 0
        self.app.k_backup()
        self.assertEqual(self.app._lines(), ["SYSTEM CONFIGURATION",
                                             "PRESS <ENTER>"])
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

    def test_a_restore_with_no_archive_says_so_to_the_BENCH(self):
        """And not to the console, which has no screen for it.

        This asserted `NO ARCHIVE` on the glass. The Archive Utility draws
        one screen when it finishes and 576013-637 p.8 step 11 is it --
        `ARCHIVE UTILITY` over `PRESS <STEP> TO CONTINUE` -- and **the
        manuals draw no failure screen for this function at all**, which
        the code that prints its record already says in a comment.

        `_abandon` states the rule this follows: "the bench may say what the
        console must not." So the fact goes in the log under the panel and
        the glass reads what the page reads. See FIDELITY U5.
        """
        import os
        self._archive()
        if os.path.exists(self.c.archive_path()):
            os.remove(self.c.archive_path())
        self.app.logbox.delete("1.0", "end")
        self.app.step = 1
        self._answer_yes()
        self.assertEqual(
            self.app.msg,
            "ARCHIVE UTILITY" + chr(10) + "PRESS <STEP> TO CONTINUE")
        self.assertIn("archive restore",
                      self.app.logbox.get("1.0", "end"))

    def test_a_save_that_worked_draws_the_same_screen(self):
        """The console does not report a count either way -- `{n} VALUE(S)
        SAVED` was this project's line. See FIDELITY U5."""
        self._archive()
        self.app.step = 0
        self._answer_yes()
        self.assertEqual(
            self.app.msg,
            "ARCHIVE UTILITY" + chr(10) + "PRESS <STEP> TO CONTINUE")

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

    def test_a_save_prints_a_start_time_and_an_end_time_record(self):
        """576013-637 Rev M pp.4-5: "the printer prints" ARCHIVE UTILITY /
        SAVE SETUP DATA: / START TIME: and, when the writing is done, the
        same record with END TIME: and BYTES: XXXX under it."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.paper.delete("1.0", "end")
        self.app.step = 0
        self._answer_yes()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("ARCHIVE UTILITY", paper)
        self.assertIn("SAVE SETUP DATA:", paper)
        self.assertIn("START TIME:", paper)
        self.assertIn("END TIME:", paper)
        self.assertIn(f"BYTES: {self.c.archive_bytes()}", paper)
        os.remove(self.c.archive_path())

    def test_a_restore_prints_its_two_records_before_the_setup(self):
        """576013-637 Rev M pp.15-16 draws RESTORE SETUP DATA without the
        save's colon, and "this information is followed by a complete
        printout of the system setup"."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.app.paper.delete("1.0", "end")
        self.app.step = 1
        self._answer_yes()
        paper = self.app.paper.get("1.0", "end")
        self.assertIn("RESTORE SETUP DATA", paper)
        self.assertIn("START TIME:", paper)
        self.assertIn("END TIME:", paper)
        self.assertNotIn("BYTES:", paper)       # only a save counts bytes
        self.assertLess(paper.index("END TIME:"),
                        paper.upper().index("SYSTEM STATUS REPORT"))
        os.remove(self.c.archive_path())

    def test_a_clear_prints_nothing(self):
        """576013-637 draws a record for a save and a record for a restore
        and none at all for a clear."""
        import os
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.app.paper.delete("1.0", "end")
        self.app.step = 2
        self._answer_yes()
        self.assertNotIn("START TIME:", self.app.paper.get("1.0", "end"))
        if os.path.exists(self.c.archive_path()):
            os.remove(self.c.archive_path())

    def test_a_restore_after_a_reboot_waits_for_the_hardware(self):
        """"If you are restoring after a reboot (switching the console Off
        and then back On), the system will wait 5 minutes before processing
        your request to restore archived setup data"."""
        import os
        import time
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.c.values["S60201"] = "01WRONG LABEL        "
        self.c.reboot_at = time.time()          # somebody has just rebooted
        self.app.step = 1
        self._answer_yes()
        # the request is taken, and then sat on: nothing is back yet
        self.assertEqual(self.c.values["S60201"], "01WRONG LABEL        ")
        self.assertGreater(self.app.busy_until, time.time())
        self.app._finish_archive("restore")
        self.assertEqual(self.c.values["S60201"], "01REGULAR UNLEADED   ")
        os.remove(self.c.archive_path())

    def test_a_restore_on_a_console_nobody_rebooted_runs_at_once(self):
        """The hold is for "restoring after a reboot", and a console that
        has been up all morning has already initialised its hardware."""
        import os
        import time
        self._archive()
        self.c.values["S60201"] = "01REGULAR UNLEADED   "
        self.app.step = 0
        self._answer_yes()
        self.c.values["S60201"] = "01WRONG LABEL        "
        self.c.reboot_at = None
        self.app.step = 1
        self._answer_yes()
        self.assertEqual(self.c.values["S60201"], "01REGULAR UNLEADED   ")
        self.assertLessEqual(self.app.busy_until, time.time())
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
        # the fixture selected LINEAR on the panel, so that is the
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
        self.assertEqual(self.app.cur_code(), "S60A01")   # linear, 60A
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
        self.app.logbox.delete("1.0", "end")
        self.app.k_enter()
        # Refused, and the console says nothing: `INVALID PASSCODE` was this
        # project's screen. The chart stays shut, which is the answer.
        # See FIDELITY U5.
        self.assertFalse(self.app.msg)
        self.assertIn("passcode refused", self.app.logbox.get("1.0", "end"))
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
                         # centred, as photographed on a real console:
                         # seven blank cells before SETUP MODE, where a
                         # FUNCTION screen's name is hard against the left
                         ["SETUP MODE".center(24),
                          "PRESS <FUNCTION> TO CONT"])
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
        self.assertNotIn("SYSTEM SETUP", out)
        # the screens of THIS function, and the tank they are pointed at,
        # headed once in the paper's form rather than once per row
        self.assertIn("T 1:REGULAR UNLEADED", out)

    def test_an_operating_function_prints_what_the_table_says(self):
        from tls350sim.ui import MODES, HEADER
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        fns = self.app.functions()
        for name, wanted in (("IN-TANK INVENTORY", "INVENTORY REPORT"),
                             ("LIQUID STATUS", "LIQUID STATUS"),
                             ("IN-TANK TEST RESULTS",
                              "LEAK TEST REPORT")):
            found = [i for i, f in enumerate(fns) if f["function"] == name]
            if not found:
                continue
            self.app.func, self.app.step = found[0], HEADER
            title, lines = self.app._report()
            self.assertIn(wanted, chr(10).join(lines), name)

    def alarms_shown_over(self, polls):
        """Every distinct message the status line draws over `polls` polls."""
        seen = set()
        for i in range(polls):
            self.app._cycle, self.app._blink = i, bool(i % 2 == 0)
            line = str(self.app._lines()[1])
            if line:
                seen.add(line)
        return seen

    def test_the_status_line_flashes_every_alarm_not_just_one(self):
        """"If more than one condition exists, the display will alternately
        flash all messages."

        `_blink` and `_cycle` both advance once a poll, and the message was
        stepped once a poll too, so the two were locked together: with an
        EVEN number of alarms every message landed on the same half of the
        flash every time and half of them were blanked on every pass. Two
        alarms meant one of them was never drawn at all. FIDELITY U2.
        """
        import struct
        from tls350sim.ui import MODES

        def f(v):
            return struct.pack(">f", v).hex().upper()
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = False
        c = self.app.console
        c.values["S62101"] = "01" + f(3000.0)     # low product, gallons
        c.values["S62401"] = "01" + f(1.0)        # high water, inches
        c.tank_level[1] = {"volume": 500.0, "water": 3.0}
        # a water alarm waits out the Water Alarm Filter's three minutes
        # before the console posts it -- 576013-623 Rev AN p.98, FIDELITY N3
        self.app._alarms()
        c.clock_offset += 181.0
        want = {a["screen"] for a in self.app._alarms()}
        self.assertEqual(len(want), 2, "the fixture should raise two")
        self.assertEqual(self.alarms_shown_over(16), want,
                         "an alarm the console is holding is never drawn")
        # and with an odd count, which the old code happened to survive.
        # A third water alarm cannot be raised alongside high water -- the
        # ladder holds one at a time -- so this is a posted result rather
        # than a level condition.
        c.post("02", "20", 1)                     # tank test in progress
        want = {a["screen"] for a in self.app._alarms()}
        self.assertEqual(len(want), 3)
        self.assertEqual(self.alarms_shown_over(24), want)

    def test_every_screen_prints_its_own_report(self):
        """PRINT is per screen, and it was giving the system status.

        576013-610 annotates function after function with what PRINT does
        there -- "PRINT - Status for all sensors", "PRINT - Deliveries to
        all tanks" -- and 576013-818 Rev AB says the same for Diagnostic:
        "Press Function until In-Tank Diagnostics appear. Press Print. (If
        the console does not have a printer, manually record the diagnostic
        data from each diag screen)".

        154 of the 295 diagnostic screens printed the system status report
        instead, and so did all six of MAG SUMP LEAK TEST. Three more
        raised KeyError, so PRINT crashed the panel outright.
        FIDELITY U1.
        """
        from tls350sim.ui import MODES, HEADER
        wrong, crashed, total = [], [], 0
        for mode in ("NORMAL", "DIAGNOSTIC", "RECONCILIATION"):
            self.app.mode = MODES.index(mode)
            self.app._entered = True
            fns = self.app.functions()
            for i, f in enumerate(fns):
                for step in range(HEADER, len(f.get("steps") or [])):
                    self.app.func, self.app.step = i, step
                    total += 1
                    try:
                        _title, lines = self.app._report()
                    except Exception as exc:
                        crashed.append(f'{f["function"]} step {step}: {exc}')
                        continue
                    body = chr(10).join(str(l) for l in lines)
                    if "SYSTEM STATUS REPORT" in body:
                        wrong.append(f'{f["function"]} step {step}')
        self.assertGreater(total, 400, "the walk should reach every screen")
        self.assertEqual(crashed, [], "PRINT raised")
        self.assertEqual(wrong, [], "PRINT gave the system status report")

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

    def _relay_test(self):
        """Stand on TEST OUTPUT RELAYS, on its function screen."""
        from tls350sim.ui import MODES, HEADER
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "TEST OUTPUT RELAYS"][0]
        self.app.step = HEADER
        self.app._render()
        return self.app

    def test_alarm_test_switches_a_relay_in_the_relay_test(self):
        app = self._relay_test()
        app.k_step()                       # onto ENTER RELAY NUMBER
        self.assertEqual(app._lines()[1], "ENTER RELAY NUMBER #")
        app.k_alarm()
        self.assertTrue(self.c.relays[app.device])
        app.k_alarm()
        self.assertFalse(self.c.relays[app.device])

    def test_alarm_test_does_nothing_on_the_function_screen(self):
        """576013-610 Rev AC p.2-5 walks the whole function in four rows:
        FUNCTION to `TEST OUTPUT RELAYS`, STEP to `ENTER RELAY NUMBER`, then
        ALARM/TEST. The key is reached one screen after the header, and the
        header's own second line is `PRESS <STEP> TO CONTINUE`.

        This branch tested the mode, `_entered` and the function's name and
        not the step, so ALARM/TEST on the header closed relay 1 -- a
        physical contact that runs a pump, a horn or a shutdown. See
        FIDELITY U8.
        """
        app = self._relay_test()
        self.assertEqual(app._lines(), ["TEST OUTPUT RELAYS",
                                        "PRESS <STEP> TO CONTINUE"])
        app.k_alarm()
        self.assertEqual(self.c.relays, {})
        self.assertEqual(app._lines(), ["TEST OUTPUT RELAYS",
                                        "PRESS <STEP> TO CONTINUE"])

    def test_the_relay_number_is_typed_in(self):
        """576013-610 Rev AC p.22-1: "Enter the number of the relay you want
        to test, then press ENTER. The system displays the number and name
        of the relay you selected. For example: `R 1: OVERFILL ALARM` /
        `PUSH ALARM/TEST KEY`."

        The digits go straight in -- there is no CHANGE in that sentence --
        and nothing echoed, so the only route to relay 12 was to STEP past
        the screen that asks for a number and press TANK/SENSOR eleven
        times. A documented panel action that could not be performed at
        all. CLOSED U8, its first half.
        """
        self.c.modules["relay"] = 3            # twelve relays in the cage
        app = self._relay_test()
        app.k_step()
        self.assertEqual(app._lines()[1], "ENTER RELAY NUMBER #")
        app.k_alnum("1")
        self.assertEqual(app._lines(), ["TEST OUTPUT RELAYS",
                                        "ENTER RELAY NUMBER 1"])
        app.k_alnum("2")
        self.assertEqual(app._lines()[1], "ENTER RELAY NUMBER 12")
        app.k_enter()
        self.assertEqual(app.device, 12)
        self.assertEqual(app._lines(), ["R 12: RELAY 12",
                                        "PUSH ALARM/TEST KEY"])
        app.k_alarm()
        self.assertTrue(self.c.relays[12])

    def test_a_relay_the_console_has_not_got_is_refused_silently(self):
        """`_refuse`'s rule: the console takes the keystrokes it can use and
        says nothing about the ones it cannot. No page draws a refusal for
        this, so the screen stays where it is."""
        app = self._relay_test()
        app.k_step()
        app.k_alnum("9")
        app.k_enter()
        self.assertEqual(app.device, 1)
        self.assertEqual(app._lines()[1], "ENTER RELAY NUMBER #")

    def test_the_panel_walks_the_relays_the_console_carries(self):
        """"Repeat this procedure for any additional relays", and
        `outputs.count()` is the console's own answer to how many there
        are -- the same number OUTPUT RELAY SETUP prints. The panel walked
        a flat four: twelve on the paper and four on the glass with three
        relay modules fitted, two on the paper and four on the glass with
        only an I/O module. FIDELITY U8."""
        from tls350sim import printer
        for relay, io in ((1, 1), (3, 1), (0, 1)):
            self.c.modules["relay"] = relay
            self.c.modules["io"] = io
            app = self._relay_test()
            rows = [r for r in printer.relays(self.c) if r.startswith("R ")]
            self.assertEqual(app._devices(),
                             list(range(1, len(rows) + 1)),
                             f"relay={relay} io={io}")

    def test_any_key_after_a_relay_prints_the_relay_setup(self):
        """"Display reads: Relay X On/Off. **Press any key to printout Relay
        Setup**" -- p.2-5, the same four rows. The screen drew
        `ON - PRESS ANY KEY` and no key printed anything; only PRINT
        printed, and it printed the function's own report."""
        app = self._relay_test()
        app.k_step()
        app.k_alarm()
        self.assertEqual(app.msg, "R 1: RELAY 1" + chr(10)
                         + "ON - PRESS ANY KEY")
        before = app.paper.get("1.0", "end")
        app.k_step()
        roll = app.paper.get("1.0", "end")
        self.assertGreater(len(roll), len(before))
        self.assertIn("OUTPUT RELAY SETUP", roll)
        self.assertIn("R 1:RELAY 1", roll)
        # one printout, not one per key from here on
        app.k_step()
        self.assertEqual(app.paper.get("1.0", "end").count(
            "OUTPUT RELAY SETUP"), 1)

    def test_alarm_test_does_not_report_what_it_did(self):
        """576013-610 Rev AC p.3-2: "ALARM/TEST silences the alarm. **It does
        not clear the alarm message from the display** or disable the alarm."

        Four messages used to stand here and all four were this project's --
        `{n} CLEARED, {n} STILL / ACTIVE - CORRECT CAUSE`, `{n} ALARM(S)
        SILENCED / STILL ACTIVE`, `{n} ALARM(S) / ACKNOWLEDGED`, and
        `ALARM TEST / ALL FUNCTIONS NORMAL`. A real console reports no count
        of anything, and flashing any of them overwrote the very message
        p.3-2 says has to stay. See FIDELITY U5.
        """
        self.c.modules.pop("mt", None)      # its key prompt is a real screen
        self.c.values["S62101"] = "01" + struct.pack(">f", 1000.0).hex().upper()
        self.c.tank_level[1]["volume"] = 500.0
        self.app._blink = False
        self.app.k_alarm()
        self.assertEqual(self.app.msg, "",
                         "ALARM/TEST put a message on the glass")
        # and the alarm's own message is still there, which is the point
        self.assertEqual(self.app._lines()[1], "T 1:LOW PRODUCT ALARM")

    def test_alarm_test_with_nothing_standing_says_nothing_either(self):
        """The resting screen already reads ALL FUNCTIONS NORMAL -- p.29-2's
        own words for what the console shows once a cause is corrected and
        the alarm acknowledged -- so there is nothing for the keypress to
        announce. See FIDELITY U5."""
        self.c.modules.pop("mt", None)
        self.app.k_alarm()
        self.assertEqual(self.app.msg, "")
        self.assertIn("ALL FUNCTIONS NORMAL", self.app._lines()[1])

    def test_the_maintenance_tracker_key_prompt_is_a_real_screen(self):
        """The one thing ALARM/TEST still draws, and the only one the manual
        draws: 576013-610 Rev AC chapter 33 shows `INSERT KEY IN PORT` over
        `PRESS <ENTER>` on a console whose protected alarms cannot be
        acknowledged without a key. Its count line -- `{n} PROTECTED
        ALARM(S)` -- went with the other three. See FIDELITY U5."""
        self.c.modules["mt"] = 1
        self.c.values["S62101"] = "01" + struct.pack(">f", 1000.0).hex().upper()
        self.c.tank_level[1]["volume"] = 500.0
        self.app._blink = False
        self.app.k_alarm()
        self.assertEqual(self.app.msg,
                         "INSERT KEY IN PORT" + chr(10) + "PRESS <ENTER>")

    def test_every_flashed_string_is_one_the_manuals_draw(self):
        """`_flash` is the ONE door onto the console's 24x2 glass, which is
        what makes this countable.

        Forty-five strings came through it and not one was recorded in
        `UNKNOWNS.md`, `FIDELITY.md` or `CLOSED.md`: counts of what a
        keypress had just done, confirmations, refusals, developer text in
        lower case. Eight are left and each is named below with the page
        that draws it. A ninth is a missing FEATURE wearing a message's
        clothes and says so.

        This reads the SOURCE rather than driving the panel, because a walk
        cannot reach every branch and the point is the vocabulary rather
        than the flow. See FIDELITY U5.
        """
        import ast
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "ui.py"),
                  encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        # {the literal on the glass: the page that draws it}
        DRAWN = {
            "INVALID INSERT": "576013-610 Rev AC p.5-2",
            # the OTHER of the two error messages the manuals name,
            # on the same page and in the same sentence shape: "the
            # error message, 'DATE IS OUT OF RANGE,' will appear"
            "DATE IS OUT OF RANGE": "576013-610 Rev AC p.5-2",
            "TANK CHART SECURITY": "576013-623 Rev AN p.5-27",
            "ARCHIVE UTILITY": "576013-637 p.8 step 11",
            "PRESS <STEP> TO CONTINUE": "passim, and CONT_STEP",
            "INSERT KEY IN PORT": "576013-610 Rev AC ch.33",
            "PRESS <ENTER>": "576013-610 Rev AC ch.33",
            # a FEATURE this console does not have, not a message it invents
            "MAINT REPORT": "FIDELITY U5, chapter 32 is unbuilt",
            "NO HISTORY": "FIDELITY U5, chapter 32 is unbuilt",
            # fragments of the lines above, which an f-string splits: the
            # relay screen `R 1: OVERFILL ALARM` / `ON - PRESS ANY KEY` and
            # the passcode field `ENTER PASSCODE->______<`
            "R": "576013-610 Rev AC p.23",
            ":": "576013-610 Rev AC p.23",
            "- PRESS ANY KEY": "576013-610 Rev AC p.23",
            "ENTER PASSCODE->": "576013-623 Rev AN p.5-27",
            "<": "576013-623 Rev AN p.5-27",
        }

        # Every string literal inside a `self._flash(...)` call, found by
        # parsing rather than by grep: a regex over the source picked up
        # `getattr(self, "_flash_id", ...)` and an `e["action"]` subscript,
        # neither of which is a message.
        literals = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_flash"):
                continue
            for arg in node.args:
                # A dict key inside the argument -- `e["action"]` -- is not
                # a message, so subscript slices are skipped.
                keys = {id(sub.slice) for sub in ast.walk(arg)
                        if isinstance(sub, ast.Subscript)}
                for part in ast.walk(arg):
                    if not (isinstance(part, ast.Constant)
                            and isinstance(part.value, str)):
                        continue
                    if id(part) in keys:
                        continue
                    text = part.value.strip()
                    if text and not text.isspace():
                        literals.add(text)
        unaccounted = sorted(t for t in literals if t not in DRAWN)
        self.assertEqual(
            unaccounted, [],
            "a string reached the console's glass with no page behind it; "
            "either cite it in DRAWN or take it off the glass")

    def test_the_status_screen_shows_the_alarm_the_console_would(self):
        self.c.values["S62101"] = "01" + struct.pack(">f", 1000.0).hex().upper()
        self.c.tank_level[1]["volume"] = 500.0
        self.app._blink = False
        rows = self.app._lines()
        self.assertEqual(rows[1], "T 1:LOW PRODUCT ALARM")

    # ---- Figure 6-5, the Service Notice session ---------------------------
    def _service_notice(self):
        from tls350sim.ui import MODES
        self.c.values["S56600"] = "1"       # the FEATURE, or the function is
        self.app.mode = MODES.index("DIAGNOSTIC")   # not on the menu at all
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("SERVICE NOTICE SESSION")
        self.app.step = 0
        self.app._render()
        return self.app

    def test_the_service_notice_session_is_two_screens_not_three(self):
        """Figure 6-5 is nine cells and two screens: the last cell is the
        FIRST screen come round again, reading ENABLED because the walk just
        changed it. This console had DISABLED, DURATION and ENABLED as three
        consecutive steps, so it asserted both states at once. FIDELITY D9."""
        app = self._service_notice()
        self.assertEqual(len(app.steps()), 2)

    def test_walking_figure_6_5_end_to_end(self):
        """S, C, E, S, C, E, S, B -- every cell of the figure in order."""
        app = self._service_notice()
        self.assertEqual(app._lines(), ["SERVICE NOTICE SESSION", "DISABLED"])
        app.k_change()
        self.assertEqual(app._lines(), ["SERVICE NOTICE SESSION", "ENABLED"])
        self.assertIsNotNone(self.c.service_session())
        app.k_enter()
        self.assertEqual(app._lines(), ["ENABLED", "PRESS <STEP> TO CONTINUE"])
        app.k_step()
        self.assertEqual(app._lines(),
                         ["SERVICE NOTICE SESSION", "DURATION : 2"])
        app.k_change()
        app.k_alnum("1")
        self.assertEqual(app._lines(),
                         ["SERVICE NOTICE SESSION", "DURATION : 1"])
        app.k_enter()
        self.assertEqual(app._lines(),
                         ["DURATION : 1", "PRESS <STEP> TO CONTINUE"])
        self.assertEqual(self.c.service_session_hours(), 1)
        app.k_step()
        # and round to the first screen, which now reads ENABLED
        self.assertEqual(app._lines(), ["SERVICE NOTICE SESSION", "ENABLED"])

    def test_the_duration_takes_only_one_to_eight(self):
        app = self._service_notice()
        app.step = 1
        app.k_change()
        app.k_alnum("9")
        app.k_enter()
        # The value is refused and the glass says nothing about it:
        # `INVALID ENTRY` is this project's screen and no console's, and
        # what a real one draws where it draws anything is the manual's own
        # words -- `DISABLED DEL IN PROGRESS` two tests below is one.
        # The reason is in the bench log. See FIDELITY U5.
        self.assertEqual(app.msg, "")
        self.assertIn("entry refused", app.logbox.get("1.0", "end"))
        self.assertEqual(self.c.service_session_hours(), 2)

    def test_a_drop_in_progress_refuses_the_session_on_the_screen(self):
        """"If there is a delivery in progress, then cannot change to Enable,
        and it will display 'DISABLED DEL IN PROGRESS'" -- which is
        twenty-four characters, so it is the whole second line."""
        self.c.set_setting("delivery_override", "ENABLED", 0)
        app = self._service_notice()
        self.c.tick()
        self.c.tank_level[1]["volume"] += 600.0
        self.c.tick()
        app.k_change()
        self.assertEqual(app._lines(),
                         ["SERVICE NOTICE SESSION", "DISABLED DEL IN PROGRESS"])
        self.assertIsNone(self.c.service_session())

    def test_the_key_block_id_prompt_still_says_ID(self):
        """The duration screen and ENTER ID TO BLOCK share one editing
        branch, which used to hard-code `ID:`. The prompt comes off the
        field now, and the screen without one keeps the old default."""
        from tls350sim.ui import MODES
        app = self.app
        app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in app.functions()]
        app.func = names.index("MAINT HARDWARE KEY BLOCK")
        tops = [sc["text"] for sc in app.steps() if not sc.get("depth")]
        app.step = tops.index("ENTER ID TO BLOCK")
        app.sub = app._diag_children()
        app.step = 0
        self.assertEqual(app._lines()[0], "ENTER ID TO BLOCK")
        app.k_change()
        app.k_alnum("A")
        self.assertEqual(app._lines()[1], "ID: A")

    # ---- ARCHIVE UTILITY, photographed screen by screen -------------------
    #
    # Five photographs of a real bare TLS-350, in the order they were taken:
    #
    #     ARCHIVE UTILITY           ARCHIVE UTILITY
    #     PRESS <STEP> TO CONTINUE  SAVE SETUP DATA: NO
    #
    #     ARCHIVE UTILITY           RESTORE SETUP DATA: YES
    #     SAVE SETUP DATA: YES      PRESS <STEP> TO CONTINUE
    #
    #     RESTORE SETUP DATA: YES
    #     ARE YOU SURE? : YES
    #
    # Every one matched what this panel already drew except the last, which
    # carries a SPACE before the colon that no manual prints.

    def test_the_archive_function_announces_itself_first(self):
        """Photograph 1: the header screen, before any of its steps."""
        from tls350sim.ui import HEADER
        self._archive()
        self.app.step = HEADER
        self.assertEqual(self.app._lines(),
                         ["ARCHIVE UTILITY", "PRESS <STEP> TO CONTINUE"])

    def test_the_archive_steps_come_in_the_photographed_order(self):
        """Photograph 2 is the first of them. SAVE, RESTORE, CLEAR, and the
        function's own name stays on line 1 throughout."""
        from tls350sim.ui import HEADER
        self._archive()
        self.app.step = HEADER
        seen = []
        for _ in range(3):
            self.app.k_step()
            seen.append(self.app._lines())
        self.assertEqual([l2 for _l1, l2 in seen],
                         ["SAVE SETUP DATA: NO",
                          "RESTORE SETUP DATA: NO",
                          "CLEAR SETUP DATA: NO"])
        self.assertEqual({l1 for l1, _l2 in seen}, {"ARCHIVE UTILITY"})

    def test_change_turns_the_archive_answer_over(self):
        """Photograph 3, and the header stays put while it does."""
        self._archive()
        self.app.step = 0
        self.app.k_change()
        self.assertEqual(self.app._lines(),
                         ["ARCHIVE UTILITY", "SAVE SETUP DATA: YES"])

    def test_enter_confirms_the_archive_answer_and_step_asks_again(self):
        """Photographs 4 and 5, which are one step in two stages: the answer
        confirmed, then the console asking it a second time."""
        self._archive()
        self.app.step = 1                       # RESTORE SETUP DATA
        self.app.k_change()
        self.app.k_enter()
        self.assertEqual(self.app._lines(), ["RESTORE SETUP DATA: YES",
                                             "PRESS <STEP> TO CONTINUE"])
        self.app.k_step()
        self.assertEqual(self.app._lines()[0], "RESTORE SETUP DATA: YES")
        self.app.k_change()
        self.assertEqual(self.app._lines()[1], "ARE YOU SURE? : YES")

    def test_are_you_sure_carries_a_space_before_its_colon(self):
        """The one difference the five photographs found.

        The manual writes `ARE YOU SURE?: NO` and the glass does not. The
        mark measures to the centre of cell 14 within 0.05 of a cell, where
        a flush colon would sit in cell 13 -- a full cell away -- with the
        pitch taken locally from the five cells of `SURE?` beside it,
        because the photograph is at an angle and the pitch drifts 21%
        across the glass.

        Only the archive's screen is changed on this evidence. The other ARE
        YOU SURE screens keep the manual's flush colon until a photograph of
        each says otherwise; see UNKNOWNS B4.
        """
        self._archive()
        self.app.step = 0
        self.app.k_change()
        self.app.k_enter()
        self.app.k_step()
        line = self.app._lines()[1]
        self.assertTrue(line.startswith("ARE YOU SURE? : "), line)
        self.assertEqual(line.index(":"), 14)

    # ---- the chart passcode keeps its field -------------------------------

    def _secured_chart(self):
        """576013-623 Rev AN p.5-27's screen, with the chart shut."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        self.c.set_chart_code("778899")
        self.c.set_tank_profile(1, "04")
        fns = self.app.functions()
        self.app.func = [i for i, f in enumerate(fns)
                         if f["function"] == "IN-TANK SETUP"][0]
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Tank Capacity")
        self.app._blink = False
        return self.app

    def test_the_empty_passcode_field_is_the_manuals_line(self):
        """`ENTER PASSCODE->______<` -- six cells between the arrow and the
        closing bracket. The panel drew `ENTER PASSCODE->***`, with neither
        field nor bracket, and the citation audit accepted it only because a
        prefix of the manual's line is still a prefix of it. The other two
        places this project draws the same screen, `screens.py` and the
        panel's own `_change_into`, both had it right."""
        app = self._secured_chart()
        self.assertEqual(app._lines(),
                         ["TANK PROFILE : 50 PTS", "ENTER PASSCODE->______<"])

    def test_the_passcode_field_keeps_its_width_as_digits_arrive(self):
        """Two digits in is two stars and four underscores, still bracketed
        -- not two stars and a ragged end."""
        app = self._secured_chart()
        for ch in "77":
            app.k_alnum(ch)
        self.assertEqual(app._lines()[1], "ENTER PASSCODE->**____<")
        for ch in "8899":
            app.k_alnum(ch)
        self.assertEqual(app._lines()[1], "ENTER PASSCODE->******<")

    def test_the_passcode_cursor_stands_in_a_cell_of_the_field(self):
        """Blinking, the block covers the cell the next digit lands in, and
        the field is still six cells wide inside its brackets."""
        from tls350sim.facepanel import CURSOR
        app = self._secured_chart()
        app._blink = True
        for ch in "77":
            app.k_alnum(ch)
        line = app._lines()[1]
        self.assertEqual(line, "ENTER PASSCODE->**" + CURSOR + "___<")
        self.assertEqual(len(line), 23)

    def test_a_full_passcode_field_has_no_cursor_left_to_show(self):
        app = self._secured_chart()
        app._blink = True
        for ch in "778899":
            app.k_alnum(ch)
        self.assertEqual(app._lines()[1], "ENTER PASSCODE->******<")

    # ---- R2: the line results screen starts a test in the console's words --

    def _line_site(self):
        """A console with a PLLD line programmed, standing on its result."""
        from tls350sim.ui import MODES
        self.c.modules["plld"] = 1
        self.c.values["S78101"] = "01"           # line 1 configured
        self.c.values["S78201"] = "01PLLD #1            "
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("PRESSURE LINE RESULTS")
        self.app.device = 1
        self.app.step = 0
        self.app._render()
        return self.app

    def test_enter_on_a_programmed_line_result_does_nothing_at_all(self):
        """R2 fixed the SCREEN this used to draw -- `Q 1: GROSS` over `TEST
        STARTED`, every word of it this project's own -- and O15 removed
        the keypress. 576013-610 Rev AC ch.11 gives these screens STEP,
        TANK/SENSOR and PRINT; the test is chapter 12's own function.

        Written against a console with the line actually programmed, which
        is the fixture R2's two tests used and the one that would notice a
        test starting."""
        app = self._line_site()
        before = app._lines()
        self.assertTrue(before[0].startswith("Q 1:"), before)
        for _ in range(2):
            app.k_enter()
            self.assertEqual(app._lines(), before)
        self.assertEqual(dict(self.c.leaks.running), {})
        self.assertFalse(any(s.get("runtest") for s in app.steps()))

    # ---- R3: the white key is chapter 32, not an invented refusal ---------

    def test_the_white_key_says_nothing_on_a_console_without_the_feature(self):
        """`MAINT REPORT` / `NO HISTORY` is on no page. 576013-610 Rev AC
        p.32-1 gives the feature three conditions and says nothing at all
        about a console that fails them, so neither does this -- the same
        reading FIDELITY U5 settled for the chart passcode."""
        self.assertFalse(self.c.maintenance_report_ready())
        self.app.k_white()
        rows = self.app._lines()
        # line 1 is the clock and ticks between two reads, so the assertion
        # is on what the key DREW, which is nothing
        self.assertNotIn("NO HISTORY", chr(10).join(rows))
        self.assertNotIn("MAINT", chr(10).join(rows))
        self.assertEqual(rows[1], "ALL FUNCTIONS NORMAL".center(24))
        self.assertFalse(self.app.maint_report)

    def test_the_white_key_opens_chapter_32s_own_screen(self):
        """p.32-3: "Press the white (Maintenance Report) key on the front
        panel: MAINTENANCE REPORT / PRESS <PRINT>"."""
        self.c.board = "E6"
        self.c.values["S56500"] = "1"
        self.assertTrue(self.c.maintenance_report_ready())
        self.app.k_white()
        self.assertEqual(self.app._lines(),
                         ["MAINTENANCE REPORT", "PRESS <PRINT>"])

    def test_the_white_keys_screen_prints_the_maintenance_history(self):
        """The screen says PRESS <PRINT> and something has to answer it.
        Figure 32-2 prints function 119's own title, which is
        `MAINTENANCE HISTORY` on paper where the screen says REPORT."""
        self.c.board = "E6"
        self.c.values["S56500"] = "1"
        self.app.k_white()
        title, lines = self.app._report()
        self.assertEqual(title, "MAINTENANCE HISTORY")
        out = chr(10).join(lines)
        self.assertIn("MAINTENANCE HISTORY", out)
        self.assertIn("TYPE", out)
        self.assertIn("HISTORY ENABLED", out)

    def test_the_paper_and_the_wire_read_one_record_the_same_way(self):
        """They used to have a renderer each. `alarmreports.maintenance_words`
        is the one they share now."""
        from tls350sim import alarmreports
        from tls350sim.wire import Handler
        entry = {"at": "0601161723", "type": "0B", "data": "001234"}
        self.assertEqual(alarmreports.maintenance_words(entry),
                         Handler._maintenance_words(entry))

    # ---- the WARNING lamp is not case-sensitive --------------------------

    def _lamp_colours(self):
        """(warn, alarm) as the poll would set them, without a window.

        Through the panel's own `lamp_for`, not a copy of it: this helper
        used to restate the picker's logic here, so it agreed with the
        console by construction and could not have caught the picker being
        wrong."""
        from tls350sim.ui import describe_alarms
        lit = [self.app.lamp_for(a)
               for a in describe_alarms(self.c.conditions())]
        return "warning" in lit, "alarm" in lit

    def test_a_capitalised_warning_lights_the_yellow_lamp(self):
        """The picker tested `"Warning" in description`, case-sensitively,
        and the console's descriptions are not written in one case: fifteen
        are title case and thirty-one are capitals. So `SETUP DATA WARNING`
        was yellow under a liquid sensor and RED under a tank, a line and a
        pressure line -- one message, three devices, two lights.

        Photographed on a real console: `Q 1:SETUP DATA WARNING`, category
        21, capitals, with the YELLOW lamp lit and the red one dark. And
        576013-623 Rev AN p.5-1 names it, "the yellow warning light will
        flash"."""
        import json
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "consoledata.json"),
                  encoding="utf-8") as fh:
            types = json.load(fh).get("status_types") or {}
        caps = [(aa, tt) for aa, table in types.items()
                if isinstance(table, dict)
                for tt, desc in table.items()
                if isinstance(desc, str) and "WARNING" in desc.upper()
                and "Warning" not in desc]
        self.assertTrue(caps, "no capitalised warnings left to test")
        # every one of them lights the yellow lamp, asked of the picker
        for aa, tt in caps:
            desc = types[aa][tt]
            entry = {"aa": aa, "nn": tt, "description": desc}
            self.assertEqual(self.app.lamp_for(entry), "warning",
                             "%s/%s lights the wrong lamp: %r"
                             % (aa, tt, desc))

    def test_the_active_guard_is_not_case_sensitive_either(self):
        """`endswith("Active")` never fired: the only two descriptions that
        end in the word, `TANK TEST ACTIVE` and `RELAY ACTIVE`, are
        capitals, so neither was ever exempted from lighting a lamp."""
        self.assertTrue("TANK TEST ACTIVE".upper().endswith("ACTIVE"))
        self.assertFalse("TANK TEST ACTIVE".endswith("Active"))
        # and the exemption now only reaches rows no page has settled:
        # `TANK TEST ACTIVE` is one of Table 29-2's Warnings
        self.assertEqual(self.app.lamp_for(
            {"aa": "02", "nn": "20", "description": "TANK TEST ACTIVE"}),
            "warning")
        self.assertIsNone(self.app.lamp_for(
            {"aa": "34", "nn": "02", "description": "RELAY ACTIVE"}))

    def _lit(self, key):
        from tls350sim.ui import LED_OFF
        cv, oval, _colour = self.app.led[key]
        return cv.itemcget(oval, "fill") != LED_OFF

    def test_the_delivery_insert_fields_keep_their_labels(self):
        """576013-610 Rev AC p.5-2 draws all four of these screens and all
        four keep their label through the entry: `ENTER DELIVERY DATE` /
        `DATE: XX/XX/XXXX`, `ENTER DELIVERY TIME` / `TIME: XX:XX XX`,
        `TICKET VOLUME: XXX`, and p.5-3's `BOL: 23223`.

        The label, the colon and the separators all vanished on CHANGE and
        the date and time confirmations were bare digit runs. The EDIT/VIEW
        branch thirty lines down has kept its label all along, which is
        what makes this an oversight in one renderer. R5.
        """
        import time as _time
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.c.tick()
        app = self._operating("DELIVERY MAINTENANCE")
        app.step = 0
        app._render()
        app.k_change()                 # SELECT: INSERT
        app.k_enter()
        app.confirm = None
        today = _time.strftime("%m%d%Y", self.c.now())
        want = {"date": ("DATE: ", today, f"DATE: {today[:2]}/"
                         f"{today[2:4]}/{today[4:]}"),
                "time": ("TIME: ", "1430", "TIME: 02:30 PM"),
                "insert": ("TICKET VOLUME: ", "2500",
                           "TICKET VOLUME: 2500"),
                "insertbol": ("BOL: ", "EXQ", "BOL: EXQ")}
        seen = 0
        for i in range(len(app.steps())):
            app.step, app._blink = i, False
            app._render()
            what = (app.cur_step() or {}).get("dlv")
            if what not in want:
                continue
            seen += 1
            label, typed, confirmed = want[what]
            app.k_change()
            app._blink = False
            app._render()
            self.assertEqual(app._lines()[1].rstrip(), label.rstrip(), what)
            for ch in typed:
                app.k_alnum(ch)
            app._blink = False
            app._render()
            self.assertEqual(app._lines()[1].rstrip(), label + typed, what)
            app.k_enter()
            self.assertEqual(app._lines(), [confirmed,
                                            "PRESS <STEP> TO CONTINUE"], what)
            app.confirm = None
        self.assertEqual(seen, 4, "not every insert field was walked")

    def test_a_paper_out_clears_on_a_console_with_maintenance_tracker(self):
        """Driven on the panel, on the shipped preset that fits the board:
        pull the roll, put it back, press ALARM/TEST. It took a
        contractor's certification key -- and `INSERT KEY IN PORT` is a
        real screen in the wrong place, because p.33-1 draws it as the
        second screen of the BLUE key walk and no page draws it as the
        answer to ALARM/TEST. R6."""
        from tls350sim import presets
        presets.load(self.c, "Compliance site, CSLD and sensors")
        self.c.tick()
        self.assertTrue(self.c.has("mt"), "this preset has no MT board")
        self.c.out_of_paper = True
        self.c.tick()
        # an unacknowledged message FLASHES, so the frame is chosen: with
        # `_blink` false the second row carries the message
        self.app._poll()
        self.app._blink = False
        self.assertIn("PAPER OUT", self.app._lines()[1])
        self.c.out_of_paper = False       # the roll goes back in
        self.c.tick()
        self.app._poll()
        self.app._blink = False
        self.assertIn("PAPER OUT", self.app._lines()[1], "it should latch")
        self.app.k_alarm()
        self.app._poll()
        self.app._blink = False
        self.assertIn("ALL FUNCTIONS NORMAL", self.app._lines()[1])
        self.assertEqual(self.app.msg, "", "no key prompt from ALARM/TEST")

    def test_a_delivery_date_outside_every_period_is_refused_in_words(self):
        """576013-610 Rev AC p.5-2, and the console's own words: "If you
        enter a date that is out of the range of the current or previous
        reconciliation periods, the error message, **'DATE IS OUT OF
        RANGE,'** will appear."

        It is one of exactly TWO error messages the manuals name anywhere
        -- `INVALID INSERT` on the same page is the other -- and this
        console had the other one and not this. A date thirty-six years
        before its own clock was taken in silence and the walk went on and
        inserted the delivery. R4.
        """
        import time as _time
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.c.tick()
        self.app.device = 1
        now = _time.mktime(self.c.now())

        def day(offset):
            return _time.strftime("%m%d%Y", _time.localtime(now + offset))

        self.assertFalse(self.app._date_in_range("01011990"))
        self.assertFalse(self.app._date_in_range(day(86400)), "tomorrow")
        self.assertFalse(self.app._date_in_range(day(-70 * 86400)))
        # today, yesterday and last month are all in a period this console
        # holds -- every period opens at first touch, so a floor taken from
        # those alone would refuse yesterday's ticket on every bench
        for offset in (0, -86400, -7 * 86400, -40 * 86400):
            self.assertTrue(self.app._date_in_range(day(offset)), offset)
        # and the message reaches the glass
        self.app._insert.clear()
        self.app.buf, self.app.editing = "01011990", True
        self.app._enter_delivery({"dlv": "date"})
        self.assertEqual(self.app.msg, "DATE IS OUT OF RANGE")
        self.assertIsNone(self.app._insert.get("date"))

    def test_mass_density_posts_a_setup_data_warning_of_its_own(self):
        """576013-623 Rev AN p.7-5, the NOTE under the density screens: "A
        Setup Data Warning is posted from the time the Mass/Density feature
        is enabled until a density or thermal coefficient value is entered
        for that tank, clearing the alarm."

        The mechanism was there and correct for eleven other sources --
        probes, seven sensor categories, the Vac Sensor's three, a doubled
        VMCI and the three line-leak families -- so this was one missing
        row in a table that works. R9.
        """
        import struct
        from tls350sim import presets
        presets.load(self.c, "Two-tank retail site")
        self.c.tick()
        for tank in (1, 2):
            self.c.values.pop(f"S609{tank:02d}", None)   # no coefficient
        self.c.in_setup = False
        self.assertEqual(self.c.setup_warnings(), [])
        self.c.values["S56000"] = "1"                    # mass/density on
        self.assertEqual(self.c.setup_warnings(), ["020101", "020102"])
        # either value clears it, which is p.7-5's "density OR thermal
        # coefficient"
        self.c.values["S61E01"] = "01" + struct.pack(
            ">f", 5.9987).hex().upper()
        self.assertEqual(self.c.setup_warnings(), ["020102"])
        self.c.values["S60902"] = "02" + struct.pack(">f", 0.0).hex().upper()
        # **entered, not non-zero**: a coefficient programmed to zero is a
        # value somebody entered, and a tank nobody has touched has no
        # stored value at all
        self.assertEqual(self.c.setup_warnings(), [])
        self.c.values["S56000"] = "0"
        self.assertEqual(self.c.setup_warnings(), [])

    def test_no_lamp_lights_for_a_condition_nobody_has_posted(self):
        """576013-610 Rev AC p.29-1 keeps the message and the lamp
        together: "When no warning or alarm conditions exist, the system
        displays the ALL FUNCTIONS NORMAL message. If an alarm or warning
        condition does exist, the system displays the type and location of
        the condition."

        The lamps read `conditions()`, the raw list, where the glass is
        drawn from `compute_alarms()` -- and Alarm Reduction sits between
        them. A water alarm has a three minute filter: "the Water Alarm
        Filter allows the user to select from several filters that will
        delay the POSTING of a water alarm" (623 p.7-15). So the red lamp
        stood over `ALL FUNCTIONS NORMAL` for three minutes, which is the
        one state a real console does not have, and exactly the state a
        technician reads as "something is wrong and the display will tell
        me what". A2.
        """
        from tls350sim import presets
        presets.load(self.c, "Two-tank retail site")
        self.c.tick()
        window = self.c.water_alarm_window(1)[0]
        self.assertGreater(window, 60, "no filter to wait through")
        self.c.tank_level[1]["water"] = 3.0
        base = self.c.clock_offset
        for seconds in (0, 30, window - 10):
            self.c.clock_offset = base + seconds
            self.c.tick()
            self.app._blink = True
            self.app._poll()
            self.assertEqual(self.c.conditions(), ["020301"], seconds)
            self.assertIn("ALL FUNCTIONS NORMAL", self.app._lines()[1])
            self.assertFalse(self._lit("alarm"), f"red lamp at t={seconds}")
            self.assertFalse(self._lit("warn"), f"yellow lamp at t={seconds}")
        # and the lamp arrives with the message, not before it
        self.c.clock_offset = base + window + 10
        self.c.tick()
        self.app._blink = True
        self.app._poll()
        self.assertIn("HIGH WATER ALARM", self.app._lines()[1])
        self.assertTrue(self._lit("alarm"))

    def test_an_arriving_alarm_sounds_the_beeper_until_it_is_silenced(self):
        """576013-610 Rev AC p.29-1 gives it a heading of its own: "AUDIBLE
        ALARM / Press ALARM/TEST to silence the alarm." 576013-623 p.2-2
        says the key "shuts off audible alarm", and 577013-814's
        operability procedures start a dozen numbered steps with "press the
        Alarm/Test key to silence the beeper".

        `_beep` was called by the lamp test and by nothing else, so
        `console.silenced` was tracked exactly right and drove nothing
        audible -- there was no sound for ALARM/TEST to stop. A6.
        """
        self.c.modules.pop("mt", None)      # its key prompt is a real screen
        self.c.out_of_paper = True
        self.c.tick()
        self.beeps.clear()
        self.app._poll()
        self.assertTrue(self.beeps, "an alarm arrived and nothing sounded")
        # ALARM/TEST stops it, and the message stays
        self.app.k_alarm()
        self.assertTrue(self.c.silenced)
        self.beeps.clear()
        self.app._poll()
        self.assertEqual(self.beeps, [])
        self.assertTrue(self.c.conditions(), "the cause is still there")

    def test_a_new_condition_sounds_it_again(self):
        """"An alarm that comes back is a new alarm, not an acknowledged
        one" -- `compute_alarms` clears `silenced` on a fresh condition, and
        that has to reach the beeper."""
        self.c.modules.pop("mt", None)
        self.c.out_of_paper = True
        self.c.tick()
        self.app._poll()
        self.app.k_alarm()
        self.beeps.clear()
        self.app._poll()
        self.assertEqual(self.beeps, [], "still silenced")
        # a second, different condition
        self.c.printer_lever_open = True
        self.c.tick()
        self.app._poll()
        self.assertFalse(self.c.silenced)
        self.assertTrue(self.beeps)

    def test_a_console_told_to_be_quiet_stays_quiet(self):
        """"System Beeper (Enable/Disable)", S53000: the setting gates the
        sound, and the poll must not route around it."""
        self.app._beep = type(self.app)._beep.__get__(self.app)
        self.c.values["S53000"] = "0"
        self.assertFalse(self.app.beeper_enabled())
        self.c.out_of_paper = True
        self.c.tick()
        self.app._poll()          # would raise or sound if it ignored S530

    def test_the_ullage_screen_says_percent_and_the_paper_says_a_sign(self):
        """One field, two surfaces, two words, and both are Veeder-Root's.

        576013-623 Rev AN p.5-14 draws the SETUP SCREEN as `ULLAGE` over
        `90 PERCENT`, with the prose "Tank Ullage can be changed from the
        default value of 90 percent to 95 percent". The site's own tape
        prints `ULLAGE: 90%` and 576013-635 Rev AA p.230 gives the serial
        display format the same way.

        The screen was drawing the chapter-3 index row's `90%` -- the
        Setup Mode Programming Table, which is an index and which this
        project has been burned by before (FIDELITY U6). SU1.
        """
        from tls350sim import printer
        app = self._setup_step("SYSTEM SETUP", "Ullage")
        app._blink = False
        app._render()
        self.assertEqual(app._lines(), ["ULLAGE", "90 PERCENT"])
        app.k_change()
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "95 PERCENT")
        app.k_enter()
        self.assertEqual(app._lines()[0], "95 PERCENT")
        self.assertEqual(self.c.values["S56400"], "1",
                         "the wire's value must still be the choice code")
        app.confirm = None
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "95 PERCENT")
        # and the paper is untouched
        self.assertIn("ULLAGE: 95%",
                      [r for r in printer.setup(self.c) if "ULLAGE" in r])

    def test_the_temp_compensation_range_is_the_whole_sentence(self):
        """576013-623 Rev AN p.5-14: "To enter a different TC reference
        temperature, press CHANGE. **Enter a value between 0 and 120 F
        (-17 to +49 C).** Press ENTER to confirm your entry."

        The field carried 0 to 100, so 120 was refused and the entry
        reverted in silence -- and a metric console's floor was 0, which
        cannot express -17 at all. SU5.
        """
        app = self._setup_step("SYSTEM SETUP", "Temp Compensation")

        def typed(text):
            app.k_change()
            for ch in text:
                app.k_alnum(ch)
            app.k_enter()
            app.confirm = None
            app._blink = False
            app._render()
            return app._lines()[1]

        self.assertEqual(typed("120"), "VALUE (DEG F): +120.0")
        self.assertEqual(typed("121"), "VALUE (DEG F): +120.0", "refused")
        self.assertEqual(typed("0"), "VALUE (DEG F): +000.0")
        # the metric half of the same sentence, which has a NEGATIVE floor
        self.c.values["S51700"] = "2"
        self.assertEqual(typed("-17"), "VALUE (DEG F): -017.0")
        self.assertEqual(typed("50"), "VALUE (DEG F): -017.0", "refused")
        self.assertEqual(typed("49"), "VALUE (DEG F): +049.0")

    def test_the_eight_test_needed_day_fields_are_masked(self):
        """576013-623 Rev AN p.5-8 to p.5-12 draw all eight, two X's for
        the periodic pair and three for the annual: `DAYS = XX` and
        `DAYS = XXX`. The console drew `DAYS = 0`, and typing `030` echoed
        it and then settled to `DAYS = 30` on ENTER -- masked while you
        typed and unmasked the moment you stopped. SU4."""
        from tls350sim.ui import MODES
        for code in ("S54600", "S54900", "S55600", "S55900"):
            self.c.values[code] = "1"          # the four enables
        app = self.app
        app.mode = MODES.index("SETUP")
        names = [f["function"] for f in app.functions()]
        app.func = names.index("SYSTEM SETUP")
        app.step = 0
        found = {(s.get("code") or ""): i for i, s in enumerate(app.steps())}
        width = {"S54700": 2, "S54800": 2, "S54A00": 3, "S54B00": 3,
                 "S55700": 2, "S55800": 2, "S55A00": 3, "S55B00": 3}
        for code, wide in width.items():
            self.assertIn(code, found, code)
            app.step, app._blink = found[code], False
            app._render()
            digits = app._lines()[1].split("=")[-1].strip()
            self.assertEqual(len(digits), wide, f"{code}: {digits!r}")
        # and it stays masked through an entry
        app.step = found["S54A00"]
        app.k_change()
        for ch in "030":
            app.k_alnum(ch)
        app.k_enter()
        self.assertEqual(app._lines()[0], "DAYS = 030")
        app.confirm = None
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "DAYS = 030")

    def test_the_daylight_savings_times_carry_their_drawn_zero(self):
        """576013-623 Rev AN p.5-16 draws both of these `TIME: 02:00 AM`,
        and p.17-1 draws AUTOMATIC DAILY CLOSING `TIME: 2:00 AM`, in the
        same manual. Two screens, two drawings, so the field says which and
        the shared decoder stops asserting one for all of them. SU6, and
        UNKNOWNS B15 for the contradiction."""
        from tls350sim.ui import MODES
        self.c.values["S51A00"] = "1"          # daylight savings enabled
        app = self.app
        app.mode = MODES.index("SETUP")
        names = [f["function"] for f in app.functions()]
        app.func = names.index("SYSTEM SETUP")
        app.step = 0
        # by FIELD, because `SHIFT #1 START TIME` matches the words too
        found = {s.get("field"): i for i, s in enumerate(app.steps())}
        for field in ("S51B01.time", "S51B02.time"):
            self.assertIn(field, found)
            app.step, app._blink = found[field], False
            app._render()
            self.assertEqual(app._lines()[1], "TIME: 02:00 AM", field)

    def test_the_two_periodic_reconciliation_alarm_fields_are_masked(self):
        """576013-623 Rev AN p.17-4 draws the offset `ALARM OFFSET: 000130`
        and its confirmation `ALARM OFFSET: XXXXXX` -- six digits, zero
        padded, so 999999 at the top. It had no mask, no width and no range
        at all, so `999999999` was STORED and the field screen went on
        drawing `ALARM OFFSET: 1e+09`, which no TLS-350 can display.

        p.17-3 draws the threshold `1.00` and `X.XX`, and its range is in
        the prose on the same page -- "the Alarm Threshold must be between
        0.00 and 5.00 percent". The range was enforced and the two decimal
        places were not. R7 and R8."""
        app = self._setup_step("RECONCILIATION SETUP", "Periodic "
                               "Reconciliation Offset")
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "ALARM OFFSET: 000130")
        app.k_change()
        for ch in "999999999":
            app.k_alnum(ch)
        app.k_enter()
        app.confirm = None
        app._render()
        self.assertEqual(app._lines()[1], "ALARM OFFSET: 000130",
                         "a nine-digit offset was stored")
        app.k_change()
        for ch in "250":
            app.k_alnum(ch)
        app.k_enter()
        self.assertEqual(app._lines()[0], "ALARM OFFSET: 000250")

        app = self._setup_step("RECONCILIATION SETUP",
                               "Periodic Reconciliation Alarm Threshold")
        app.k_change()
        for ch in "2.5":
            app.k_alnum(ch)
        app.k_enter()
        self.assertEqual(app._lines()[0], "ALARM THRESHOLD: 2.50")
        app.confirm = None
        app._render()
        self.assertEqual(app._lines()[1], "ALARM THRESHOLD: 2.50")

    def test_every_numeric_field_on_the_panel_takes_digits(self):
        """The sweep OP12 asked for: every screen CHANGE opens an entry on,
        with one press of `6` against the value that was there.

        Three presets, all four modes. Where the value on the screen is a
        NUMBER and the key puts a letter in, the field is in the wrong set
        -- which is how the seven AVG SALES days were found after TEST
        DURATION. `PRODUCT CODE` is the one that looks like a number and is
        not: p.7-2 says "Enter the alphanumeric code used by a
        point-of-sale terminal", and a UK four-star code is `4*`.
        """
        import re
        from tls350sim import presets
        from tls350sim.ui import MODES
        number = re.compile(r"^[A-Z0-9 #/:.+-]*?[-+]?\d[\d.,:]*\s*$")
        app, wrong = self.app, []
        for name in presets.PRESETS:
            console = a_console()
            presets.load(console, name)
            console.tick()
            app.console = console
            app.reset_panel()
            app._beep = lambda times=1: None
            for mode in MODES:
                app.mode = MODES.index(mode)
                app._entered = True
                for fi, fn in enumerate(app.functions()):
                    app.func, app.sub, app.device = fi, None, 1
                    for si in range(len(app.steps())):
                        app.step, app.editing, app.buf = si, False, ""
                        app._blink = False
                        try:
                            app._render()
                            before = app._lines()
                            allowed = (app.cur_field() or {}).get("kind")
                            app.k_change()
                            if not app.editing:
                                continue
                            app._blink = False
                            app.k_alnum("6")
                            app._blink = False
                            app._render()
                            after = app._lines()
                        except Exception:
                            continue
                        # a label takes letters, a pick-list is
                        # walked by CHANGE, and a slot screen
                        # answers the arrows
                        if allowed in ("text", "slots", "enum",
                                       "flag"):
                            continue
                        if not number.match(before[1]) or "6" in after[1]:
                            continue
                        wrong.append(f"{fn.get('function')} step {si}: "
                                     f"{before[1]!r} -> {after[1]!r}")
        self.assertEqual(sorted(set(wrong)), [],
                         "a numeric field types letters")

    def test_the_test_duration_takes_a_digit_on_the_first_press(self):
        """576013-610 Rev AC p.20-2: "To change the duration of the test
        (the length of time the test will run in hours), press CHANGE,
        **enter the test duration**, then press ENTER."

        The `6` key went M, N, O, 6, so a twelve hour test took eight
        presses and the field spent three of every four showing a letter
        where the screen says hours. p.3-3 scopes the multi-tap rule to the
        other kind of field, and the same console already does it the other
        way one mode across: MANUAL ADJUSTMENTS' volume and Delivery
        Maintenance's ticket both take a digit on the first press. OP12.
        """
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.c.tick()
        app = self._operating("START IN-TANK LEAK TEST")
        app.step = 0
        found = [i for i, s in enumerate(app.steps()) if s.get("entry")]
        self.assertTrue(found, "no typed-entry step in this function")
        app.step = found[0]
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "DURATION: 2")
        app.k_change()
        app.k_alnum("6")
        app._blink = False
        app._render()
        # the trailing space is `_edit_text`'s cell for the cursor
        self.assertEqual(app._lines()[1].rstrip(), "DURATION: 6")
        app.k_enter()
        self.assertEqual(app.sel["hours"], "6")

    def test_a_finished_tank_test_takes_its_messages_off_the_glass(self):
        """Both of Table 29-3's self-clearing rows, driven end to end: a
        3.0 gph test started on a siphon-manifolded set and then stopped.
        Twenty minutes of console time later the messages were still there
        and only ALARM/TEST took them off. A4 and A5."""
        from tls350sim import presets
        presets.load(self.c, "Two-tank retail site")
        self.c.values["S63001"] = "011"       # tank test notify, tank 1
        self.c.values["S63201"] = "011"       # siphon break valve fitted
        self.c.values["S63202"] = "011"
        self.c.manifold_together(1, [2])
        self.c.tick()
        self.app._poll()
        self.assertIn("ALL FUNCTIONS NORMAL", self.app._lines()[1])
        self.c.leaks.start("tank", 1, "gross", 2.0)
        self.c.tick()
        self.app._poll()
        shown = set(self.c.compute_alarms())
        self.assertIn("022001", shown, "no TANK TEST ACTIVE to clear")
        self.assertIn("022201", shown, "no TANK SIPHON BREAK to clear")
        # the test ends, and nobody presses anything
        self.c.leaks.stop("tank", 1)
        self.c.clock_offset += 1200
        self.c.tick()
        self.app._poll()
        self.assertEqual(self.c.compute_alarms(), [])
        self.assertIn("ALL FUNCTIONS NORMAL", self.app._lines()[1])

    def test_the_lamp_comes_off_the_manuals_own_column(self):
        """576013-610 Rev AC chapter 29 heads every message table `Display
        Message | Front Panel Indicator | Cause | Action`, and the second
        column is the lamp. **It cannot be worked out from the message.**
        The picker was a substring test on the description, and these are
        the rows where the page disagrees with what a substring would say.
        A1, and the half CLOSED U23 left open."""
        for cat, desc, want in (
                # the word ALARM in the message, and a Warning on the page
                ("02", "HIGH PRODUCT ALARM", "warning"),
                # neither word in the message
                ("01", "PAPER OUT", "warning"),
                ("01", "PRINTER ERROR", "warning"),
                ("02", "TANK SIPHON BREAK", "warning"),
                ("02", "DELIVERY NEEDED", "warning"),
                ("02", "NO CSLD IDLE TIME", "warning"),
                ("01", "TOO MANY TANKS", "warning"),
                ("01", "REMOTE DISPLAY ERROR", "warning"),
                # and the ones a substring got right, which must stay right
                ("01", "BATTERY IS OFF", "alarm"),
                ("01", "PROTECTIVE COVER ALARM", "alarm"),
                ("02", "LOW PRODUCT ALARM", "alarm"),
                ("02", "SETUP DATA WARNING", "warning")):
            entry = {"aa": cat, "nn": "01", "description": desc}
            self.assertEqual(self.app.lamp_for(entry), want, desc)

    def test_two_messages_whose_lamp_depends_on_the_sensor_family(self):
        """`LIQUID WARNING` is a Warning in Table 29-5 and an Alarm in
        Table 29-17, and `WATER ALARM` is a Warning in 29-9 and 29-15 and
        an Alarm in 29-20. One message, two lamps, decided by which family
        raised it -- which is why the indicator table is keyed by category
        for these five rows and by message for the other 68."""
        cases = (("03", "LIQUID WARNING", "warning"),     # Liquid Sensor
                 ("12", "LIQUID WARNING", "alarm"),       # Type B / 3-wire
                 ("04", "WATER ALARM", "warning"),        # Vapor Sensor
                 ("08", "WATER ALARM", "warning"),        # Type A / 2-wire
                 ("28", "WATER ALARM", "alarm"))          # Smart Sensor
        for cat, desc, want in cases:
            entry = {"aa": cat, "nn": "01", "description": desc}
            self.assertEqual(self.app.lamp_for(entry), want,
                             f"{desc} on category {cat}")

    # ---- R1: a confirmation is drawn in its field's own mask -------------

    def _in_tank(self, label):
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("IN-TANK SETUP")
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index(label)
        self.app._render()
        return self.app, texts

    def _type(self, app, typed):
        app.k_change()
        for ch in typed:
            app.k_alnum(ch)
        app.k_enter()

    def test_the_confirmation_is_drawn_in_the_fields_own_mask(self):
        """576013-623 Rev AN p.7-5 draws `TANK DIAMETER: XXX.XX` over
        PRESS <STEP> TO CONTINUE, the same mask as the field one line above
        it on the same page. This drew `fieldio.decode`'s output, whose
        float branch is `"%g"` -- the shortest round-trip form, which is
        precisely what a fixed-width console field is not -- so the console
        said `96.5` on the confirmation and `096.50` one keypress later."""
        app, texts = self._in_tank("Tank Diameter")
        self._type(app, "96.5")
        self.assertEqual(app._lines()[0], "TANK DIAMETER: 096.50")
        app.k_step()
        app.step = texts.index("Tank Diameter")
        app._render()
        self.assertEqual(app._lines()[1], "TANK DIAMETER: 096.50")

    def test_the_confirmation_cannot_claim_precision_that_was_not_kept(self):
        """`499.995` was CONFIRMED as `499.995` and STORED as `500.00`, so
        the confirmation asserted a precision the console does not keep."""
        app, texts = self._in_tank("Tank Diameter")
        self._type(app, "499.995")
        self.assertEqual(app._lines()[0], "TANK DIAMETER: 500.00")
        app.k_step()
        app.step = texts.index("Tank Diameter")
        app._render()
        self.assertEqual(app._lines()[1], "TANK DIAMETER: 500.00")

    def test_the_confirmation_and_the_field_agree_on_every_masked_field(self):
        """The pattern was identical in twenty-three fields across nine
        functions, so this walks every MASKED field of a function rather
        than one of them. Label steps are left out: their confirmation is
        the device head, a different screen with a different shape."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("IN-TANK SETUP")
        steps = self.app.steps()
        checked = []
        for i, step in enumerate(steps):
            self.app.step = i
            self.app._render()
            if self._is_label_step(step) if hasattr(self, "_is_label_step")                     else self.app._is_label_step(step):
                continue
            if not (self.app.cur_field() or {}).get("mask"):
                continue
            self.app.k_change()
            self.app.k_alnum("1")
            self.app.k_enter()
            if not self.app.confirm:
                # the entry was refused, and a refusal draws no confirmation
                # at all -- line 1 is the field screen's own device head
                self.app.buf = ""
                self.app.editing = False
                continue
            confirmed = self.app._lines()[0]
            self.app.k_step()
            self.app.step = i
            self.app._render()
            now = self.app._lines()[1]
            self.assertEqual(confirmed, now,
                             "%r confirmed %r and reads %r"
                             % (step["text"], confirmed, now))
            checked.append(step["text"])
        self.assertGreater(len(checked), 3,
                           "nothing masked was actually compared")

    # ---- K1: ENTER without CHANGE stores nothing -------------------------

    def test_a_bare_enter_does_not_erase_the_setting(self):
        """No CHANGE, nothing typed, just ENTER. This wrote `self.buf` --
        the empty string -- and ERASED the value, on thirteen screens, under
        a PRESS <STEP> TO CONTINUE that reads like success. A technician
        pressing ENTER to see what a screen does lost the programming."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        if "FUEL MANAGEMENT SETUP" not in names:
            self.skipTest("no fuel management on this console")
        self.app.func = names.index("FUEL MANAGEMENT SETUP")
        moved = []
        for i, step in enumerate(self.app.steps()):
            if not step.get("console"):
                continue
            self.app.step = i
            self.app._render()
            before = self.app._lines()[1]
            self.app.k_enter()
            self.app.confirm = None
            self.app.step = i
            self.app._render()
            if self.app._lines()[1] != before:
                moved.append((step["text"], before, self.app._lines()[1]))
        self.assertEqual(moved, [])

    def test_change_then_an_empty_enter_still_removes_a_value(self):
        """The guard is on `editing`, not on the buffer, so a DELIBERATE
        empty entry still reaches the branch that treats it as "remove
        this" -- which is what the serial number screen wants, and is a
        different thing from never having pressed CHANGE."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        if "VMC SETUP" not in names:
            self.skipTest("no VMC on this console")
        self.app.func = names.index("VMC SETUP")
        # ch.27's entry screen is a level down: "Press ENTER." in response
        # to ADD VMC SERIAL NUMBER / PRESS <ENTER> is what reaches it.
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Add VMC Serial Number")
        self.app.k_enter()
        self.app._render()
        self.app.k_change()
        for ch in "005830":
            self.app.k_alnum(ch)
        self.app.k_enter()
        self.assertEqual(self.c.vmc_serials.get(1), "005830")
        self.app.confirm = None
        self.app._render()
        # CHANGE once keeps the value with the cursor on its first character;
        # "(To erase a label press CHANGE again.)" So erasing it deliberately
        # is CHANGE, CHANGE, ENTER -- and that still has to reach the branch
        # where an empty entry removes the record.
        self.app.k_change()
        self.app.k_change()
        self.app.k_enter()
        self.assertIsNone(self.c.vmc_serials.get(1))

    # ---- K3: TANK/SENSOR needs a device to advance to --------------------

    def test_tank_sensor_does_nothing_on_the_mode_screen(self):
        """"Press to change to the next tank or sensor" (576013-939 Quick
        Help p.2). On the bare SETUP MODE screen there is no tank shown and
        no next one -- but the pointer is one counter for the whole panel,
        so two presses there used to open IN-TANK SETUP on tank 3 with the
        technician believing they were programming tank 1."""
        from tls350sim.ui import MODES, MODE_SCREEN
        self.app.mode = MODES.index("SETUP")
        self.app.step = MODE_SCREEN
        self.app._render()
        self.app.k_tank()
        self.app.k_tank()
        self.assertEqual(self.app.device, 1)
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("IN-TANK SETUP")
        self.app.step = 0
        self.app._render()
        self.assertEqual(self.app.device, 1)

    def test_tank_sensor_does_nothing_on_a_console_wide_setup_screen(self):
        """SYSTEM LANGUAGE has no device on it. `_devices()` is scoped to
        the FUNCTION, and SYSTEM SETUP mixes console-wide steps with
        repeating ones, so its fallback handed four phantom devices to every
        screen in it."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("SYSTEM SETUP")
        texts = [s["text"] for s in self.app.steps()]
        which = [i for i, t in enumerate(texts) if t.startswith("System Units")]
        self.app.step = which[0]
        self.app._render()
        self.app.k_tank()
        self.assertEqual(self.app.device, 1)

    def test_the_repeating_console_screens_still_walk(self):
        """Station Header Line 1-4 and Shift #1-4 Start Time call themselves
        console-wide and repeat four times, and TANK/SENSOR is how you reach
        lines 2, 3 and 4. `screens.py` already told these apart with
        `console_step(step) and not step.get("repeat")`; the key uses the
        same test now."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        self.app.func = names.index("SYSTEM SETUP")
        texts = [s["text"] for s in self.app.steps()]
        for label in ("Station Header Line 1 (Line 2/Line 3/Line 4)",
                      "Shift #1 Start Time (Shift 2/Shift 3/Shift 4)"):
            self.app.device = 1
            self.app.step = texts.index(label)
            self.app._render()
            before = self.app._lines()
            self.app.k_tank()
            self.assertEqual(self.app.device, 2, label)
            self.assertNotEqual(self.app._lines(), before, label)

    def test_a_device_scoped_field_walks_even_on_a_console_step(self):
        """`ADD VMC SERIAL NUMBER` calls itself console-wide in the menu
        data and draws `x 1:` from a field scoped to the device. The step
        says one thing and the field says another, and the field is right."""
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        if "VMC SETUP" not in names:
            self.skipTest("no VMC on this console")
        self.app.func = names.index("VMC SETUP")
        # ch.27's entry screen is a level down: "Press ENTER." in response
        # to ADD VMC SERIAL NUMBER / PRESS <ENTER> is what reaches it.
        texts = [s["text"] for s in self.app.steps()]
        self.app.step = texts.index("Add VMC Serial Number")
        self.app.k_enter()
        self.app.device = 1
        self.app._render()
        self.app.k_tank()
        self.assertEqual(self.app.device, 2)

    # ---- the keys, walked key by key rather than screen by screen ---------
    #
    # `audits/2026-09-10-key-behaviour.md` is the walk these came out of:
    # every key on every kind of screen, 647 screens by 16 keys.

    def _glass(self):
        """The LCD itself, not `_lines()`.

        `_render` returns early for three screens and those rows never pass
        through `_lines`, and a confirmation can be BUILT without being
        painted -- which is one of the defects below. Read the canvas.
        """
        return [self.app.lcd.itemcget(rid, "text").rstrip()
                for rid in self.app._text_ids]

    def test_removing_a_vmc_walks_the_pages_six_screens(self):
        """576013-623 Rev AN p.27-2 draws six screens for REMOVE VMC SERIAL
        NUMBER and this console drew one: a serial-number ENTRY screen, the
        same one ADD and EDIT use, so the only way to remove a controller
        was to type nothing into it and press ENTER. The page's own walk is
        two questions and a STEP that ends on the parent."""
        from tls350sim.ui import CONT_STEP
        self.c.modules["vmc"] = 1
        self.c.vmc_serials[1] = "111111"
        self.setup_step("VMC SETUP", "Remove VMC Serial Number")
        self.assertEqual(self._glass(),
                         ["REMOVE VMC SERIAL NUMBER", "PRESS <ENTER>"])
        self.app.k_enter()
        self.assertEqual(self._glass(), ["x 1: 111111", "REMOVE VMC: NO"])
        self.app.k_change()
        self.assertEqual(self._glass(), ["x 1: 111111", "REMOVE VMC: YES"])
        self.app.k_enter()
        self.assertEqual(self._glass(), ["REMOVE VMC: YES", CONT_STEP])
        self.app.k_step()
        # no colon on this one, where the first question has one: the page
        # draws them differently and this follows it rather than tidying
        self.assertEqual(self._glass(),
                         ["REMOVE VMC: 111111", "ARE YOU SURE? NO"])
        self.assertEqual(self.c.vmc_serials, {1: "111111"})   # not yet
        self.app.k_change()
        self.assertEqual(self._glass()[1], "ARE YOU SURE? YES")
        self.app.k_step()
        self.assertEqual(self.c.vmc_serials, {})
        # "Press STEP:" and the page's last figure is the parent again
        self.assertEqual(self._glass(),
                         ["REMOVE VMC SERIAL NUMBER", "PRESS <ENTER>"])

    def test_a_vmc_survives_the_walk_it_was_not_confirmed_on(self):
        """Two questions, and either of them left on NO keeps the
        controller. What STEP off `ARE YOU SURE? NO` does is on no page, so
        it leaves the walk without removing anything."""
        self.c.modules["vmc"] = 1
        self.c.vmc_serials[1] = "111111"
        self.setup_step("VMC SETUP", "Remove VMC Serial Number")
        self.app.k_enter()
        self.app.k_step()                      # STEP off REMOVE VMC: NO
        self.assertEqual(self.c.vmc_serials, {1: "111111"})
        self.setup_step("VMC SETUP", "Remove VMC Serial Number")
        self.app.k_enter()
        self.app.k_change()
        self.app.k_enter()
        self.app.k_step()                      # onto ARE YOU SURE? NO
        self.app.k_step()
        self.assertEqual(self.c.vmc_serials, {1: "111111"})

    def test_disabling_the_beeper_asks_twice(self):
        """576013-623 Rev AN p.5-29 into p.5-30 draws six screens for the
        System Beeper and this console drew three: STEP off the
        confirmation went straight to the next function step, so the whole
        reconfirm was absent from the one setup action that silences the
        console. Read off the canvas, because a confirmation can be built
        and never painted. SU11."""
        from tls350sim.ui import CONT_STEP
        app = self._setup_step("SYSTEM SETUP", "BEEPER")
        app._blink = False
        app._render()
        self.assertEqual(self._glass(), ["BEEPER", "ENABLED"])
        app.k_change()
        app._blink = False
        app._render()
        self.assertEqual(self._glass(), ["BEEPER", "DISABLED"])
        app.k_enter()
        self.assertEqual(self._glass(), ["DISABLED", CONT_STEP])
        app.k_step()
        # spaced either side of the colon, which is how this page writes it
        self.assertEqual(self._glass(), ["DISABLED", "ARE YOU SURE? : NO"])
        app.k_change()
        self.assertEqual(self._glass(), ["DISABLED", "ARE YOU SURE? : YES"])
        app.k_enter()
        self.assertEqual(self._glass(), ["ARE YOU SURE? : YES", CONT_STEP])
        app.k_step()
        self.assertNotIn("ARE YOU SURE?", " ".join(self._glass()))
        self.assertEqual(self.c.values.get("S53000"), "0")

    def test_enabling_the_beeper_does_not(self):
        """The page draws the reconfirm on the answer that SILENCES the
        console and draws no such screen anywhere else. ENTER on NO is on
        no page either, so it does nothing rather than invent a re-enable."""
        from tls350sim.ui import CONT_STEP
        self.c.values["S53000"] = "0"
        app = self._setup_step("SYSTEM SETUP", "BEEPER")
        app.k_change()
        app._blink = False
        app._render()
        self.assertEqual(self._glass(), ["BEEPER", "ENABLED"])
        app.k_enter()
        self.assertEqual(self._glass(), ["ENABLED", CONT_STEP])
        app.k_step()
        self.assertNotIn("ARE YOU SURE?", " ".join(self._glass()))
        # and NO on the disable path holds its screen
        app = self._setup_step("SYSTEM SETUP", "BEEPER")
        app.k_change()
        app.k_enter()
        app.k_step()
        self.assertEqual(self._glass()[1], "ARE YOU SURE? : NO")
        app.k_enter()
        self.assertEqual(self._glass()[1], "ARE YOU SURE? : NO")

    def _setup_step(self, function, want, prompt=False):
        """Stand on the step of `function` whose head or text names `want`.

        Setup Mode has levels, so this descends into the branches. A
        branch and the screen below it share their head -- ISO 3166 COUNTRY
        is drawn twice, once as a prompt and once as the field -- so the
        screen with something ON it wins, and the prompt is taken only
        when nothing else answers to the name -- or when `prompt` asks for
        the prompt, which is the screen some of these tests are about.
        """
        from tls350sim.ui import MODES
        self.app.mode = MODES.index("SETUP")
        names = [f["function"] for f in self.app.functions()]
        if function not in names:
            self.skipTest(f"no {function} on this console")
        self.app.func = names.index(function)
        self.app.sub = None
        self.app.step = 0
        # an entry left open on the screen this test came FROM otherwise
        # walks along with it: `CHANGE` on one enum put its word under the
        # next screen's prompt
        self.app.editing, self.app.buf, self.app.confirm = False, "", None
        if prompt or not self._find_setup(want, prompts=False):
            self.app.sub = None
            self.app.step = 0
            self.assertTrue(self._find_setup(want, prompts=True, only=prompt),
                            f"{want} not among {function}'s steps")
        self.app._render()
        return self.app

    def _find_setup(self, want, prompts, only=False, depth=0):
        path = list(self.app.subs)
        for i in range(len(self.app.steps())):
            self.app.subs = list(path)
            self.app.step = i
            st = self.app.steps()[i]
            named = (want in (st.get("head") or "")
                     or want in (st.get("text") or ""))
            is_prompt = (st.get("body") or "").strip() == "PRESS <ENTER>"
            if named and (is_prompt if only else (prompts or not is_prompt)):
                return True
            parent = self.app._branch_at() if depth < 3 else None
            if parent is not None:
                self.app.subs = list(path) + [parent]
                if self._find_setup(want, prompts, only, depth + 1):
                    return True
        self.app.subs = list(path)
        return False

    def test_step_and_function_mid_entry_move_on_one_press(self):
        """576013-623 Rev AN p.3-1: "If you press the STEP, FUNCTION, or MODE
        key without pressing ENTER, the data will not be saved." It is a
        warning about navigating AWAY from an open entry -- and p.2-2 says
        outright that FUNCTION "will advance to the next Function".

        All three keys swallowed the first press to cancel the entry and
        went nowhere, and the glass did not even drop the typed digits, so
        nothing told the technician the key had done anything. MODE ignored
        `_abandon`'s answer and behaved correctly, which is what made the
        other two an oversight rather than a reading. K5.
        """
        for key in ("k_step", "k_function"):
            app = self._setup_step("SYSTEM SETUP", "PRECISION TEST DURATION")
            before = app._lines()
            app.k_change()
            for ch in "024":
                app.k_alnum(ch)
            self.assertEqual(app._lines()[1], "HOURS: 024")
            getattr(app, key)()
            self.assertNotEqual(app._lines(), before, key)
            self.assertFalse(app.editing, key)
            # and the data is not saved, which is what the sentence is about
            app = self._setup_step("SYSTEM SETUP", "PRECISION TEST DURATION")
            self.assertEqual(app._lines()[1], "HOURS: 012", key)

    def test_the_digit_keys_do_not_type_into_a_pick_list(self):
        """576013-623 Rev AN p.6-2 makes the baud rate a selection: "To
        choose another Baud Rate, press CHANGE until you see the correct
        baud rate." A technician typing 9600 got `BAUD RATE: WM00` -- 9 and
        6 multi-tap to W and M -- and then a silent refusal from ENTER. K6.
        """
        app = self._setup_step("COMMUNICATIONS SETUP", "Baud Rate")
        self.assertEqual(app.cur_field()["kind"], "enum")
        app.k_change()
        self.assertEqual(app._lines()[1], "BAUD RATE: 2400")
        for ch in "96":
            app.k_alnum(ch)
        self.assertEqual(app._lines()[1], "BAUD RATE: 2400")
        app.k_enter()
        self.assertEqual(app._lines(), ["BAUD RATE: 2400",
                                        "PRESS <STEP> TO CONTINUE"])
        self.assertEqual(self.c.values["S88101"][:5], "02400")

    def test_the_board_type_stays_in_the_head_while_choosing(self):
        """"COMM BOARD: 1 (Type)" over "PARITY: ODD" -- p.6-2 draws the card
        in the slot on the screen you are CHOOSING on, ODD being what CHANGE
        has just walked parity to. `screens.py` was fixed for this and the
        panel's own head was not, so `(RS-232)` left the glass on the first
        press of CHANGE and came back when you stepped away. K7."""
        app = self._setup_step("COMMUNICATIONS SETUP", "Parity")
        self.assertEqual(app._lines()[0], "COMM BOARD: 1 (RS-232)")
        app.k_change()
        self.assertEqual(app._lines(), ["COMM BOARD: 1 (RS-232)",
                                        "PARITY: ODD"])

    def test_a_typed_setup_value_is_acknowledged_on_the_glass(self):
        """The acknowledgement was built and never painted.

        `_poll` redraws in Operating Mode, mid-entry or on a live screen,
        and ENTER has just cleared `editing`; the S-code path did not
        render for itself. So in Setup Mode the `PRESS <STEP> TO CONTINUE`
        screen stood in `confirm` until the next key, and the next key
        clears `confirm` on its way past. It reads as correct from
        `_lines()`, which is what every other test here calls. K7.
        """
        app = self._setup_step("SYSTEM SETUP", "SYSTEM SECURITY")
        app.k_change()
        for ch in "123456":
            app.k_alnum(ch)
        app.k_enter()
        self.assertEqual(self._glass(), ["CODE: 123456",
                                         "PRESS <STEP> TO CONTINUE"])

    def _operating(self, function):
        """Stand on an Operating Mode function, on its function screen."""
        from tls350sim.ui import MODES, HEADER
        self.app.mode = MODES.index("NORMAL")
        self.app._entered = True
        names = [f["function"] for f in self.app.functions()]
        if function not in names:
            self.skipTest(f"no {function} on this console")
        self.app.func = names.index(function)
        self.app.step = HEADER
        self.app._render()
        return self.app

    def test_enter_on_a_line_results_screen_starts_nothing(self):
        """576013-610 Rev AC ch.11 gives every PRESSURE LINE RESULTS screen
        three keys and names each one: STEP to reach it, "to view 3.0 gph
        test results for other lines in the system, press TANK/SENSOR", "to
        print ... press PRINT". Starting a test is chapter 12, a separate
        function with its own three-screen confirmation.

        ENTER here started a real 3.0 gph test on the line whose result you
        were reading -- on hardware, from a screen that cannot do it, and a
        3.0 gph failure shuts a pump down. FIDELITY O15.
        """
        app = self._operating("PRESSURE LINE RESULTS")
        app.k_step()
        before = app._lines()
        self.assertEqual(before[0], "Q 1: PLLD #1")
        app.k_enter()
        self.assertEqual(app._lines(), before)
        self.assertEqual(dict(self.c.leaks.running), {})

    def test_the_vlld_start_walk_keeps_its_own_screen(self):
        """576013-610 Rev AC p.13-2: "Press ENTER to start the test. The
        system begins the line leak, displays the message: `START LEAK
        TEST: ALL LINES` / `PRESS <ENTER>`" -- the screen it was already
        on -- "and prints a report that the test has started."

        This answered `W 1: TEST COMPLETE`: the WPLLD letter on a VLLD
        line, the word COMPLETE at the instant a test starts, and a screen
        change chapter 13 does not draw. The report is UNKNOWNS A33.
        CLOSED U34.
        """
        self.c.values["S76001"] = "01LINE 1              "
        app = self._operating("START LINE LEAK TEST")
        for _ in range(3):
            app.k_step()
        self.assertEqual(app._lines(), ["START LEAK TEST: ALL LIN",
                                        "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines(), ["START LEAK TEST: ALL LIN",
                                        "PRESS <ENTER>"])
        self.assertIn(("vlld", 1), self.c.leaks.running)
        # and the stop walk the same way, p.13-5
        app = self._operating("STOP LINE LEAK TEST")
        for _ in range(2):
            app.k_step()
        self.assertEqual(app._lines(), ["STOP LEAK TEST: ALL LINE",
                                        "PRESS <ENTER>"])
        app.k_enter()
        self.assertEqual(app._lines(), ["STOP LEAK TEST: ALL LINE",
                                        "PRESS <ENTER>"])
        self.assertEqual(dict(self.c.leaks.running), {})

    def test_the_two_pressure_walks_still_answer_with_the_status(self):
        """Chapter 11 and chapter 12 DO draw a screen, and it is the line's
        own status: p.11-4 `Q #: RUNNING PUMP` / `PRESS <STEP> TO
        CONTINUE`. Different chapters, different cards, different answers
        -- so the VLLD exception above must not reach these."""
        for function, want in (("START PRESSURE LINE TEST",
                                "Q 1: RUNNING PUMP"),
                               ("START WPLLD LINE TEST", "W 1: TEST PENDING")):
            app = self._operating(function)
            for _ in range(3):
                app.k_step()
            app.k_enter()
            self.assertEqual(app._lines(), [want, "PRESS <STEP> TO CONTINUE"],
                             function)

    def test_a_vlld_line_is_headed_with_its_own_letter(self):
        """`Lines.code` named two families and fell through to the WPLLD
        letter for the third, where `printer.py` has carried the same
        three-way table in three places all along."""
        from tls350sim.pressure import Lines
        self.assertEqual([Lines.code(k) for k in ("plld", "wplld", "vlld")],
                         ["Q", "W", "P"])

    def test_the_tank_chart_passcode_can_be_set_from_the_keypad(self):
        """576013-623 Rev AN p.5-19: "If you have not entered a passcode,
        press CHANGE and enter a 6-digit numeric passcode, then press ENTER
        to accept your entry: `CODE: ******` / `PRESS <STEP> TO CONTINUE`."

        The whole feature was unreachable from the panel, with its ENTER
        handler written correctly and unsatisfiable: `_enter_console` keeps
        only `c.isdigit()` and refuses anything that is not six of them,
        and `chartcode` was in no numeric set, so the six digit keys
        multi-tapped to `QADGJM` and that refusal was guaranteed on every
        key sequence. `_chart_locked` -- the screen you meet when a
        passcode already exists -- had treated the field as numeric all
        along. SU8.
        """
        app = self._setup_step("SYSTEM SETUP", "Tank Chart Security")
        app._blink = False
        self.assertEqual(app._lines(), ["TANK CHART SECURITY",
                                        "CODE : 000000"])
        app.k_change()
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "CODE :")
        for ch in "123456":
            app.k_alnum(ch)
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "CODE : 123456")
        app.k_enter()
        self.assertEqual(app._lines(), ["CODE: ******",
                                        "PRESS <STEP> TO CONTINUE"])
        self.assertEqual(self.c.chart_code, "123456")
        self.assertTrue(self.c.chart_secured())

    def test_the_bir_clear_map_can_be_answered_yes(self):
        """576013-818 Rev AB Figure 6-24 gives the function two screens and
        annotates the second: "NO or YES. Selecting YES restarts BIR and
        clears the Adjusted Delivery Reports, BIR Reconciliation Records,
        and Meter Map. Selecting YES does not restart the AccuChart 56-day
        calibration."

        The screen carried no `act`, so CHANGE could not draw YES and ENTER
        did nothing: the console's BIR reset was a read-out. It both hid a
        real function and understated a destructive one. DG1.
        """
        from tls350sim.ui import MODES
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.c.tick()
        self.assertTrue(self.c.meters, "the preset maps no meters")
        app = self.app
        app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in app.functions()]
        self.assertIn("RECONCILIATION CLEAR MAP", names)
        app.func = names.index("RECONCILIATION CLEAR MAP")
        app.step = 0
        app._render()
        self.assertEqual(app._lines(), ["RECONCILIATION CLEAR MAP",
                                        "CLEAR TANK MAPS: NO"])
        app.k_change()
        self.assertEqual(app._lines()[1], "CLEAR TANK MAPS: YES")
        app.k_enter()
        self.assertEqual(app._lines(), ["CLEAR TANK MAPS: YES",
                                        "PRESS <STEP> TO CONTINUE"])
        self.assertEqual(dict(self.c.meters), {})
        self.assertFalse(self.c.bir.map_complete())
        self.assertEqual(self.c.bir.closed, {})

    def test_the_clear_map_leaves_the_accuchart_calibration_alone(self):
        """"Selecting YES does not restart the AccuChart 56-day
        calibration" -- the one thing the same sentence says it does NOT
        do, so it is worth a test of its own."""
        from tls350sim import presets
        presets.load(self.c, "Truck stop, four tanks and BIR")
        self.c.tick()
        tanks = sorted(self.c.tank_level)
        self.assertTrue(tanks)
        before = [(self.c.accuchart.state(t).started,
                   self.c.accuchart.state(t).mode_since) for t in tanks]
        self.assertTrue(any(b[0] is not None for b in before),
                        "no calibration clock to leave alone")
        self.c.diag_action("recon_clear_map")
        after = [(self.c.accuchart.state(t).started,
                  self.c.accuchart.state(t).mode_since) for t in tanks]
        self.assertEqual(after, before)

    def test_enter_into_a_branch_lands_on_its_first_screen(self):
        """In every chapter-6 figure the `E` arrow runs from the
        `PRESS <ENTER>` screen TO THE FIRST SCREEN OF THE BRANCH. Figure
        6-2: `SYSTEM CONFIGURATION / PRESS <ENTER>` -E-> `SLOT 1 4 PROBE /
        POR= XXXXXX C= XXXXXX`. Figure 6-25: `BIR METER MAP / PRESS
        <ENTER>` -E-> `BIR METER MAP / STATUS: COMPLETE`.

        The branch opened with a copy of the screen that offered it, so
        ENTER redrew what was already there: every branch in Diagnostic
        Mode cost one keypress more than the diagram, with "nothing
        happened" as the feedback for a correct press. DG3.

        And BACKUP out of the branch was invisible for the same reason --
        the screen either side of the boundary was the same two lines,
        which is K11 and closes with it.
        """
        from tls350sim.ui import HEADER, MODES
        app = self.app
        app.mode = MODES.index("DIAGNOSTIC")
        names = [f["function"] for f in app.functions()]
        app.func = names.index("SYSTEM DIAGNOSTIC")
        app.step, app.sub = HEADER, None
        for _ in range(20):
            app.k_step()
            if app._lines() == ["SYSTEM CONFIGURATION", "PRESS <ENTER>"]:
                break
        else:                                          # pragma: no cover
            self.fail("never reached SYSTEM CONFIGURATION")
        app.k_enter()
        self.assertIsNotNone(app.sub)
        self.assertEqual(app._lines()[0], "SLOT 1 4 PROBE")
        # and one BACKUP out of it, with the glass changing on the press
        app.k_backup()
        self.assertIsNone(app.sub)
        self.assertEqual(app._lines(), ["SYSTEM CONFIGURATION",
                                        "PRESS <ENTER>"])

    def test_a_console_setting_keeps_its_prompt_while_you_type(self):
        """`BDIM TRANS ALARM DELAY / HOURS: 024` lost the word HOURS on the
        first press of CHANGE -- the only word telling the technician what
        unit they were typing -- where PRECISION TEST DURATION and SYSTEM
        SECURITY on the same function keep theirs. Those are S-code fields
        and go through a different renderer; the Diagnostic Mode branch has
        read the prompt off the field all along. K10."""
        for want, prompt in (("BDIM TRANS ALARM DELAY", "HOURS:"),
                             ("ISO 3166 COUNTRY", "CODE:"),
                             ("PRECISION TEST DURATION", "HOURS:"),
                             ("SYSTEM SECURITY", "CODE:")):
            app = self._setup_step("SYSTEM SETUP", want)
            app._blink = False
            app.k_change()
            app._blink = False
            app._render()
            self.assertEqual(app._lines(), [want, prompt], want)

    def test_a_numeric_console_setting_clears_on_change(self):
        """"A plain numeric scalar CLEARS outright, with the cursor at the
        right-hand entry position and digits arriving calculator-fashion",
        filmed on a real console -- `_change_into` does it for every `int`,
        `float` and `digits` field. A console setting has no such kind, so
        BDIM seeded with `024` and one press of `1` overtyped it to `124`.
        `number` is the same fact under another name and is on the field.
        K10."""
        app = self._setup_step("SYSTEM SETUP", "BDIM TRANS ALARM DELAY")
        app._blink = False
        app.k_change()
        for ch in "12":
            app.k_alnum(ch)
        app._blink = False
        app._render()
        self.assertEqual(app._lines()[1], "HOURS: 12")
        app.k_enter()
        self.assertEqual(app._lines(), ["HOURS: 012",
                                        "PRESS <STEP> TO CONTINUE"])

    def test_a_console_pick_list_is_acknowledged_by_enter(self):
        """576013-623 Rev AN p.6-2: "Press ENTER to confirm your choice. The
        system displays the message: `PARITY: XXX` / `PRESS <STEP> TO
        CONTINUE`." CHANGE walks a console setting's list and stores as it
        goes, so `editing` is never set and ENTER fell into the guard that
        stops a bare ENTER erasing a field -- on a screen where there is
        nothing to erase."""
        app = self._setup_step("SYSTEM SETUP", "EURO PROTOCOL PREFIX")
        self.assertEqual(app._lines(), ["EURO PROTOCOL PREFIX", "S"])
        app.k_change()
        self.assertEqual(app._lines(), ["EURO PROTOCOL PREFIX", "d"])
        app.k_enter()
        self.assertEqual(app._lines(), ["d", "PRESS <STEP> TO CONTINUE"])

    def test_change_does_not_write_over_a_press_enter_prompt(self):
        """576013-623 Rev AN p.5-19 draws `CUSTOM ALARM LABELS` over
        `PRESS <ENTER>` and the screen's whole content is an instruction to
        press a key. CHANGE walked the `flag` field the step happens to
        carry and wrote `ENABLED`, then `DISABLED`, over the manual's
        prompt -- telling a technician the feature was disabled when no such
        state exists. `MODIFY TANK/METER MAP` blanked its prompt the same
        way from a `list` field. K8."""
        for function, want in (("SYSTEM SETUP", "CUSTOM ALARM LABELS"),
                               ("RECONCILIATION SETUP",
                                "MODIFY TANK/METER MAP")):
            app = self._setup_step(function, want, prompt=True)
            self.assertEqual(app._lines(), [want, "PRESS <ENTER>"])
            app.k_change()
            app.k_change()
            self.assertEqual(app._lines(), [want, "PRESS <ENTER>"], want)


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipUnless(HAVE_TK, "no display")
@unittest.skipUnless(HAVE_TK, "no display")
class TheTicketReachesTheBook(Panel):
    """A ticket typed at DELIVERY MAINTENANCE wrote `record.ticket` and
    never told BIR, so the reconciliation's TICKETED column stayed at zero
    however many tickets were entered. BENCH.md T3."""

    def a_drop(self):
        c = self.c
        c.values["S61001"] = "0101"
        c.values["S51C00"] = "1"
        c.tick()
        c.bir.current(1)
        for volume in (3500, 5500, 5500):
            c.tank_level[1]["volume"] = float(volume)
            c.clock_offset += 60.0
            c.tick()
        c.clock_offset += 400.0
        c.tick()
        record = c.deliveries.last(1)
        self.assertIsNotNone(record)
        self.assertIsNone(record.ticket)
        return record

    def test_the_ticket_is_booked_and_a_correction_corrects(self):
        record = self.a_drop()
        app = self.app
        app.device = 1
        app.buf, app.editing = "3050", True
        app._enter_delivery({"dlv": "ticket"})
        self.assertEqual(record.ticket, 3050.0)
        self.assertEqual(self.c.bir.current(1)["ticketed"], 3050.0)
        app.buf, app.editing = "3000", True
        app._enter_delivery({"dlv": "ticket"})
        self.assertEqual(record.ticket, 3000.0)
        self.assertEqual(self.c.bir.current(1)["ticketed"], 3000.0)


class TheTwoPowerUpSequences(unittest.TestCase):
    """FIDELITY M11. A warm boot showed nothing at all.

    576013-637 p.16, after replacing the setup storage device with the
    battery left on: "The display will cycle through the following warm boot
    screens: SYSTEM WARM START / SYSTEM SELF TEST / SYSTEM STARTUP COMPLETE.
    At this point front panel display reads: MMM DD, YYYY HH:MM:SS XM / ALL
    FUNCTIONS NORMAL." `breaker_on()` returned "warm", the panel wrote one
    log line and re-rendered, and `_boot_sequence()` was reached only on the
    cold branch -- so the technician who had just swapped a board saw
    nothing where the manual promises three screens.
    """

    def test_a_warm_boot_has_three_screens_and_a_cold_boot_four(self):
        from tls350sim.ui import SimApp
        self.assertEqual(SimApp.BOOT_SCREENS["warm"],
                         ["SYSTEM WARM START", "SYSTEM SELF TEST",
                          "SYSTEM STARTUP COMPLETE"])
        self.assertEqual(SimApp.BOOT_SCREENS["cold"][0], "CLEARING ALL RAM")
        self.assertEqual(len(SimApp.BOOT_SCREENS["cold"]), 4)

    def test_a_warm_boot_clears_nothing_so_it_announces_nothing(self):
        """"no CLEARING ALL RAM, no SYSTEM RESET printout, no RESTORE SETUP
        DATA prompt". Nothing was cleared, so there is nothing to announce
        and nothing to offer to put back."""
        from tls350sim.ui import SimApp
        warm = SimApp.BOOT_SCREENS["warm"]
        self.assertNotIn("CLEARING ALL RAM", warm)
        self.assertNotIn("SYSTEM COLD START", warm)

    def test_the_two_sequences_share_their_last_two_screens(self):
        """Both end SYSTEM SELF TEST then SYSTEM STARTUP COMPLETE; only the
        first screen tells the technician which boot happened."""
        from tls350sim.ui import SimApp
        self.assertEqual(SimApp.BOOT_SCREENS["warm"][-2:],
                         SimApp.BOOT_SCREENS["cold"][-2:])

    def test_the_modem_screen_is_conditional_on_the_modem_cards(self):
        """576013-623 p.33 captions it "appears only if you have one of the
        modem modules or WPLLD installed"; 577013-528 p.8 draws the same
        screen for a SiteFax or SiteLink. It is the modem initialising, so
        the two captions name one set of cards between them."""
        from tls350sim.ui import SimApp
        self.assertEqual(set(SimApp.MODEM_CARDS),
                         {"modem", "slink", "wplld", "wplldcom"})
        lines = SimApp.WORKING_SCREEN.split(chr(10))
        self.assertEqual(lines[0].strip(), "WORKING")
        self.assertEqual(lines[1].strip(), "*" * 20)

    def test_it_sits_between_the_self_test_and_the_startup(self):
        """623 p.33 draws it there, after SYSTEM SELF TEST and before
        SYSTEM STARTUP COMPLETE."""
        from tls350sim.ui import SimApp
        screens = list(SimApp.BOOT_SCREENS["cold"])
        screens.insert(-1, SimApp.WORKING_SCREEN)
        self.assertEqual(screens[2], "SYSTEM SELF TEST")
        self.assertEqual(screens[3], SimApp.WORKING_SCREEN)
        self.assertEqual(screens[4], "SYSTEM STARTUP COMPLETE")


@unittest.skipUnless(HAVE_TK, "no display")
class NoScreenIsCutMidWord(unittest.TestCase):
    """Every line the panel can draw, against the 24 columns it has.

    `test_fidelity.py`'s `OVERWIDE_LINES` does this for the lines that sit
    in the menu DATA. It cannot see a line the console BUILDS -- a label
    with a live reading formatted into it -- and that is where these are:
    `BEGIN INVENTORY: 14000 G`, a unit sliced in half; `GRS: NO TEST DATA
    AVAILA`, a word cut mid-letter. A trainee cannot tell whether a stray
    `G` is a unit, a flag, or a digit that did not fit.

    The walk is the same one the audits use -- every function of every
    mode, two devices deep, on each shipped preset and on a bare console --
    done twice, once with `COLS` at 24 and once with it wide, so the clip
    becomes a no-op and the full string is visible. Digits are masked to
    `#` so a shape does not depend on a site's numbers.

    **Every shape below is a line this console is cutting today**, and they
    are three kinds:

    * **The manual's own, wider than the display.** Figure 6-7 draws `LAST
      SALES-SUN: XXXX GALS` at 25 characters and p.13-2 draws `START LEAK
      TEST: ALL LINES` at 26. What a real console does with the extra
      character is on no page, and shortening them here would be inventing
      console text -- which `tests/test_citations.py` refused once already,
      the revert recorded in FIDELITY O14.
    * **The empty case: two cited halves that do not fit together.** `GRS:`
      is p.9-1's prefix and `NO TEST DATA AVAILABLE` is the shelf's own
      phrase for nothing-yet, and no page draws them on one line.
    * **RECONCILIATION's DISPLAY AND PRINT**, which 576013-610 p.28-2
      prints on TWO lines and this console draws on one. O14 again, and the
      whole of OP5 and OP6.

    A NEW one is a failure here rather than another line nobody noticed.
    """

    DIGITS = re.compile(r"\d")

    CUT = {
        # the manual's own, wider than the glass
        "LAST SALES-MON: #### GALS", "LAST SALES-TUE: #### GALS",
        "LAST SALES-WED: #### GALS", "LAST SALES-THR: #### GALS",
        "LAST SALES-FRI: #### GALS", "LAST SALES-SAT: #### GALS",
        "PRED SALES-MON: #### GALS", "PRED SALES-TUE: #### GALS",
        "PRED SALES-WED: #### GALS", "PRED SALES-THR: #### GALS",
        "PRED SALES-FRI: #### GALS", "PRED SALES-SAT: #### GALS",
        "START LEAK TEST: ALL LINES", "START LEAK TEST: ALL TANKS",
        "START LINE TEST: ALL LINES", "STOP LEAK TEST: ALL LINES",
        "STOP LEAK TEST: ALL TANKS", "STOP LINE TEST: ALL LINES",
        "W #: LAST READ=##.### PSI",
        # the empty case, two cited halves that do not fit on one line
        "GRS: NO TEST DATA AVAILABLE", "PER: NO TEST DATA AVAILABLE",
        "ANN: NO TEST DATA AVAILABLE", "PER: NO RESULTS AVAILABLE",
        # O14's family: p.28-2 prints these on two lines
        "CALCULATED INVNTRY: #### GALS", "CALCULATED INVNTRY: ##### GALS",
        "CALCULATED INVNTRY: NO DATA AVAILABLE",
        "CLOSING DATE: NO DATA AVAILABLE", "CLOSING DATE: SEP ##, ####",
        "CLOSING TIME: NO DATA AVAILABLE",
        "CUR SHFT OPEN: NO DATA AVAILABLE", "CUR SHFT OPEN: SEP ##, ####",
        "DELIVERIES: NO DATA AVAILABLE",
        "GAUGED INVNTRY: #### GALS", "GAUGED INVNTRY: ##### GALS",
        "GAUGED INVNTRY: NO DATA AVAILABLE",
        "MANUAL ADJUSTMENTS: # GALS",
        "MANUAL ADJUSTMENTS: NO DATA AVAILABLE",
        "METERED SALES: NO DATA AVAILABLE",
        "OPENING TIME: NO DATA AVAILABLE",
        "OPENING VOLUME: #### GALS", "OPENING VOLUME: ##### GALS",
        "OPENING VOLUME: NO DATA AVAILABLE",
        "VARIANCE: NO DATA AVAILABLE",
        "WATER HEIGHT: NO DATA AVAILABLE",
        # And five the walk could not SEE until 2026-09-14. It widened
        # `ui.COLS` alone, and every resting Setup screen is drawn by
        # `screens.py` against `screens.COLS` -- so the whole of Setup Mode
        # at rest was clipped to twenty-four before the measure looked at
        # it, and a line cut there was indistinguishable from one that fit.
        "CLIMATE FACTOR: ALL TANKS",
        "DUAL FLOAT DISCRIMINATING",
        "SUDDEN LOSS LIMIT: ######",
        "TNK TST SIPHON BREAK: OFF",
        "TST EARLY STOP: ALL TANKS",
    }

    # And what CHANGE walks onto, which is a screen the console draws and
    # the resting walk never sees. Thirteen of these fifteen are a pipe
    # TYPE or a PIPE TYPE -- 576013-623 Rev AN's own nineteen-entry list,
    # whose longest entry is `PETROTECHNIK UPP EXTRA 63 MM` at twenty-eight
    # characters, which `test_citations` already tolerates as an option
    # "longer than a whole line and a half". The other two are a label and
    # a value that fit on their own and not together.
    WALKED = {
        "#.## GPH TEST: REPETITIVE",
        "CUR PERI OPEN: NO DATA AVAILABLE",
        "DAY OPEN: NO DATA AVAILABLE",
        "ENTER PRESSURE LINE LABEL",
        "LOW PRESSURE SHUTOFF: YES",
        "PIPE TYPE: #-WALL FIBERGLASS",
        "TYP: #.# IN. ENVIRN GFLEXII",
        "TYP: #.# IN. ENVIRON GEOFLX D",
        "TYP: #.#/# IN. ENVIRON GFLXD",
        "TYP: #.#/#.# IN. FIBERGLASS",
        "TYP: ENVROFLX PP####/####",
        "TYP: PETROTECHNIK UPP EXTRA ## MM",
        "TYP: WFG COFLX#### RIBBED",
        "WAIT MODE: VOL. CHG. MEAS.",
        "WATER ALARM FILTER: MEDIUM",
    }

    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        try:
            cls.app = SimApp(a_console(), 10001)
        except Exception as exc:               # pragma: no cover
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

    SPACES = re.compile(r"\s+")

    def _rows(self, app):
        try:
            return [str(r) for r in app._lines()[:2]]
        except Exception as exc:                       # pragma: no cover
            return [f"!! {exc}", ""]

    def _descend(self, app, out, mode, fn, device, depth=0, path=()):
        """This level's screens, and the ones ENTER goes down into.

        The walk read one level, so every screen behind a `PRESS <ENTER>`
        was outside it -- the whole of EVR/ISD SETUP under its headers, the
        VMC serial walks, the auto-transmit limits. It finds nothing new
        today, and it is the hole that matters tomorrow: a screen added a
        level down was a screen this ratchet could not see.
        """
        app.subs = list(path)
        for si in range(len(app.steps())):
            app.subs = list(path)
            app.step = si
            out[(mode, fn.get("function", "?"), path, si, device)] = \
                self._rows(app)
            if depth < 2:
                try:
                    parent = app._branch_at()
                except Exception:                      # pragma: no cover
                    parent = None
                if parent is not None:
                    self._descend(app, out, mode, fn, device, depth + 1,
                                  tuple(path) + (parent,))
        app.subs = list(path)

    def walk(self, app):
        """{where: (line 1, line 2)} for every screen this console shows."""
        from tls350sim.ui import MODES
        out = {}
        for mode in MODES:
            app.mode = MODES.index(mode)
            app._entered = True
            for fi, fn in enumerate(app.functions()):
                app.func = fi
                for device in (app._devices() or [1])[:2]:
                    app.device = device
                    self._descend(app, out, mode, fn, device)
        return out

    def _wide(self, app):
        """The walk with BOTH display widths opened out.

        **`ui.COLS` alone is not the display.** A resting Setup screen is
        built in `screens.py` and clipped against `screens.COLS` before the
        panel ever sees it, so widening the panel's copy left every one of
        them cut to twenty-four -- and a cut line looks exactly like a line
        that fits. Five shapes were hiding behind that.
        """
        from tls350sim import screens, ui
        was_ui, was_screens = ui.COLS, screens.COLS
        ui.COLS = screens.COLS = 200
        try:
            return self.walk(app)
        finally:
            ui.COLS, screens.COLS = was_ui, was_screens

    def cuts(self, console):
        """The masked shape of every line this console is clipping."""
        app = type(self).app
        app.console = console
        app.reset_panel()
        app._beep = lambda times=1: None
        narrow = self.walk(app)
        wide = self._wide(app)
        found = {}
        for key, rows in narrow.items():
            for i, short in enumerate(rows):
                full = wide.get(key, ["", ""])[i].rstrip()
                # Collapsed, because a right-aligned value pads to the
                # display width: `AVG SALES FRI:` puts its value in column
                # 200 when the display is 200 wide, and that difference is
                # padding rather than a cut.
                if (self.SPACES.sub(" ", full)
                        != self.SPACES.sub(" ", short.rstrip())
                        and len(full) > 24):
                    found[self.DIGITS.sub("#", full)] = (key, full)
        return found

    @staticmethod
    def _clear(app):
        app.editing, app.buf, app.confirm, app.msg = False, "", None, ""

    def walked_onto(self, console):
        """Every line CHANGE can put on the glass, at full width.

        The other half of the blind spot AUDIT.md recorded and this file did
        not have: the walk above reads RESTING screens, and a value CHANGE
        walks onto is a line the console draws.
        """
        from tls350sim import screens, ui
        from tls350sim.facepanel import CURSOR
        from tls350sim.ui import MODES
        app = type(self).app
        app.console = console
        app.reset_panel()
        app._beep = lambda times=1: None
        out = set()
        for mode in MODES:
            app.mode = MODES.index(mode)
            app._entered = True
            for fi, fn in enumerate(app.functions()):
                app.func = fi
                for device in (app._devices() or [1])[:2]:
                    app.device = device
                    for si in range(len(app.steps())):
                        app.subs, app.step = [], si
                        self._clear(app)
                        first = None
                        for _ in range(40):
                            app._blink = False
                            try:
                                app.k_change()
                            except Exception:          # pragma: no cover
                                break
                            app._blink = False
                            was_ui, was_s = ui.COLS, screens.COLS
                            ui.COLS = screens.COLS = 200
                            try:
                                rows = [r.rstrip().replace(CURSOR, "")
                                        for r in self._rows(app)]
                            finally:
                                ui.COLS, screens.COLS = was_ui, was_s
                            if first is None:
                                first = tuple(rows)
                            elif tuple(rows) == first:
                                # back where CHANGE started: the list has
                                # been round once
                                break
                            out |= {self.DIGITS.sub("#", r) for r in rows
                                    if len(r) > 24}
                        self._clear(app)
        return out

    # Each walk is every screen of every function on five consoles, twice
    # over; each CHANGE walk presses the key at every one of them. Four
    # tests read two answers, so they are taken once and kept -- the same
    # reason this class shares one Tk interpreter.
    _cut = None
    _walked = None

    def cut_shapes(self):
        if type(self)._cut is None:
            from tls350sim import presets
            seen = {}
            for name in list(presets.PRESETS) + [None]:
                console = a_console()
                if name:
                    presets.load(console, name)
                console.tick()
                seen.update(self.cuts(console))
            type(self)._cut = seen
        return type(self)._cut

    def walked_shapes(self):
        if type(self)._walked is None:
            from tls350sim import presets
            seen = set()
            for name in list(presets.PRESETS) + [None]:
                console = a_console()
                if name:
                    presets.load(console, name)
                console.tick()
                seen |= self.walked_onto(console)
            type(self)._walked = seen - self.CUT
        return type(self)._walked

    def test_no_line_is_cut_that_is_not_already_known(self):
        seen = self.cut_shapes()
        new = sorted(set(seen) - self.CUT)
        self.assertEqual(new, [], "a NEW panel line is being cut mid-word: "
                         + "; ".join(f"{seen[s][0][1]} -> {seen[s][1]!r}"
                                     for s in new))

    def test_a_shape_that_now_fits_comes_off_the_list(self):
        """The other direction, which is what makes it a ratchet: this list
        was one longer until `BEGIN INVENTORY: 14000 GALS` stopped carrying
        a unit the manual does not draw (OP3)."""
        self.assertEqual(sorted(self.CUT - set(self.cut_shapes())), [],
                         "a shape on the list now fits: take it out")

    def test_no_new_value_change_walks_onto_is_cut(self):
        """The blind spot AUDIT.md recorded and this file did not have.

        Fifteen shapes CHANGE can put on the glass are over the display's
        width and none of them was visible here. One of the two that is not
        a pipe type was a screen that changed IDENTITY at the first press of
        the key: `TST EARLY STOP: ALL TANKS` over its value became the
        tank's product label over `LEAK TEST EARLY STOP: DISABLED`, thirty
        columns, because the edit path did not know that screen names its
        own scope. That one is fixed rather than listed.
        """
        new = sorted(self.walked_shapes() - self.WALKED)
        self.assertEqual(new, [], "a NEW value CHANGE walks onto is being "
                         "cut mid-word: " + "; ".join(repr(s) for s in new))

    def test_a_walked_shape_that_now_fits_comes_off_the_list(self):
        """The other direction, so WALKED is a ratchet like CUT."""
        self.assertEqual(sorted(self.WALKED - self.walked_shapes()), [],
                         "a shape on WALKED now fits: take it out")
