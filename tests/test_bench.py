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
"""The bench beside the console, and the console's own device lists.

These are the things that were wrong when somebody sat down and used the
simulator rather than reading it: a status screen reporting on sensors that
are not there, a leak box you could put a leak in on a line the console has
never been told about, and a keyboard that sent every keystroke to both.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, printer, ui                  # noqa: E402
from tls350sim.console import Console                       # noqa: E402


def a_site():
    c = Console(None)
    presets.load(c, "Two-tank retail site")
    return c


class PanelWindow(unittest.TestCase):
    """One Tk interpreter for the whole file.

    Tk on this platform does not survive being started and stopped once per
    test inside a single process; the symptom is a later Tk() failing to read
    its own init.tcl. `SimApp.reset_panel` puts the panel back to power-on
    without another window, which is the same starting point.
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.app = ui.SimApp(a_site(), 0)
        except Exception as exc:               # pragma: no cover
            cls.app = None
            raise unittest.SkipTest(f"no usable Tk: {exc}")
        cls.app.withdraw()

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

    def setUp(self):
        self.c = a_site()
        self.app = type(self).app
        self.app.console = self.c
        self.app.reset_panel()
        self.app.paper.delete("1.0", "end")


class WhichDevicesExist(PanelWindow):

    def _point_at(self, mode, function):
        self.app.mode = ui.MODES.index(mode)
        self.app._entered = True
        fns = self.app.functions()
        names = [f["function"] for f in fns]
        self.assertIn(function, names, names)
        self.app.func = names.index(function)
        self.app.step = 0

    def test_liquid_status_walks_the_sensors_the_site_has(self):
        """The preset programmes three sump sensors on an eight input card.

        "only the Functions/Steps relevant to your console and its installed
        options and CONNECTED detection systems will be accessible": walking
        eight and calling five of them NORMAL is reporting on sensors that
        are not wired to anything.
        """
        self._point_at("NORMAL", "LIQUID STATUS")
        self.assertEqual(self.app._devices(), [1, 2, 3])

    def test_setup_still_walks_all_eight_so_you_can_switch_them_on(self):
        self._point_at("SETUP", "LIQUID SENSOR SETUP")
        self.assertEqual(self.app._devices(), list(range(1, 9)))

    def test_the_status_screens_agree_with_the_printed_report(self):
        """The print was right and the screen was not; now they match."""
        self._point_at("NORMAL", "LIQUID STATUS")
        onscreen = set(self.app._devices())
        printed = {n for mod, n, _label in self.c.programmed_sensors()
                   if mod == "liquid"}
        self.assertEqual(onscreen, printed)

    def test_pointing_at_a_device_the_function_does_not_have_is_corrected(self):
        self._point_at("NORMAL", "LIQUID STATUS")
        self.app.device = 7
        self.app._render()
        self.assertEqual(self.app.device, 1)

    def test_in_tank_inventory_walks_the_programmed_tanks(self):
        self._point_at("NORMAL", "IN-TANK INVENTORY")
        self.assertEqual(self.app._devices(), [1, 2])


class WhichLinesExist(unittest.TestCase):
    def test_only_the_lines_the_console_has_been_told_about(self):
        """A PLLD controller carries six transducers; the preset programmes
        two of them, and the other four are pieces of pipe nobody has
        configured."""
        c = a_site()
        self.assertEqual([(k, n) for k, n, _ in c.programmed_lines()],
                         [("plld", 1), ("plld", 2)])

    def test_a_labelled_line_counts_even_without_its_config_flag(self):
        c = a_site()
        c.values["S78203"] = "03LINE 3              "
        self.assertIn(("plld", 3), [(k, n) for k, n, _ in c.programmed_lines()])

    def test_no_card_means_no_lines(self):
        c = a_site()
        c.modules.pop("plld")
        self.assertEqual(c.programmed_lines(), [])


class TheKeyboard(PanelWindow):
    """Tk sends a key to the focused widget and then up to this window."""

    def test_typing_in_a_bench_box_is_not_typing_on_the_keypad(self):
        import tkinter as tk
        box = tk.Entry(self.app)
        box.pack()
        self.app.update_idletasks()
        box.focus_set()
        self.app.update()
        if self.app.focus_get() is not box:
            self.skipTest("no focus in this environment")
        self.assertTrue(self.app._typing_on_the_bench())

    def test_with_the_panel_focused_the_keys_are_the_console_s(self):
        self.app.focus_set()
        self.app.update()
        self.assertFalse(self.app._typing_on_the_bench())


class TheArchiveTakesTime(PanelWindow):
    def test_a_busy_console_ignores_the_keypad(self):
        """"This process may take a minute or so": whatever the number, the
        console is not answering while it runs."""
        app = self.app
        try:
            app.busy_until = __import__("time").time() + 30
            self.assertTrue(app._busy())
            was = app.mode
            app._guard(app.k_mode)()
            self.assertEqual(app.mode, was)
            app.busy_until = 0.0
            app._guard(app.k_mode)()
            self.assertNotEqual(app.mode, was)
        finally:
            app.busy_until = 0.0
