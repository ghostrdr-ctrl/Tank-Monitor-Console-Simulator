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
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, printer, ui                  # noqa: E402
from tls350sim import bench                                 # noqa: E402
from tls350sim.console import Console                       # noqa: E402


def a_site():
    c = Console(None)
    presets.load(c, "Two-tank retail site")
    return c


# One Tk interpreter for the whole FILE, and it has to live out here rather
# than on the base class: `setUpClass` runs once per CLASS, so a base class
# that builds a window builds one for every subclass of it, which is what this
# file's four subclasses were quietly doing. Tk on this platform does not
# survive many interpreters started and stopped inside one process -- the
# symptom is a later Tk() failing to read its own init.tcl, which reads like a
# broken installation and is really a resource that never came back.
_APP = []


class PanelWindow(unittest.TestCase):
    """One Tk interpreter for the whole file, shared by every subclass.

    `SimApp.reset_panel` puts the panel back to power-on without another
    window, which is the same starting point a fresh one would have been.
    """

    @classmethod
    def setUpClass(cls):
        if not _APP:
            try:
                app = ui.SimApp(a_site(), 0)
            except Exception as exc:           # pragma: no cover
                _APP.append(None)
                raise unittest.SkipTest(f"no usable Tk: {exc}")
            app.withdraw()
            _APP.append(app)
        cls.app = _APP[0]
        if cls.app is None:                    # pragma: no cover
            raise unittest.SkipTest("no usable Tk")

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


class TheVmcTiles(PanelWindow):
    """A VMC alarm is a status the controller reports, so the bench sets the
    status and the console does the rest. FIDELITY M12."""

    def test_a_controller_with_a_serial_gets_a_tile_per_side(self):
        app = self.app
        c = app.console
        c.set_module("vmc", 1)
        c.vmc_serials.clear()
        c.vmc_serials[1] = "005830"
        c.vmc_serials[2] = "005831"
        app._refresh_site()
        tiles = self._tiles(app)
        self.assertEqual([(t.number, t.side) for t in tiles],
                         [(1, "A"), (1, "B"), (2, "A"), (2, "B")])

    def test_picking_an_alarm_word_posts_the_alarm(self):
        app = self.app
        c = app.console
        c.set_module("vmc", 1)
        c.vmc_serials.clear()
        c.vmc_serials[1] = "005830"
        app._refresh_site()
        tile = self._tiles(app)[0]
        tile._pick("FP SHUTDOWN ALARM")
        self.assertIn("360401", c.conditions())
        tile._pick("IDLE")
        self.assertNotIn("360401", c.conditions())

    def test_no_vmci_module_no_section(self):
        app = self.app
        c = app.console
        c.modules.pop("vmc", None)
        app._refresh_site()
        self.assertEqual(self._tiles(app), [])

    @staticmethod
    def _tiles(app):
        found = []

        def walk(widget):
            if isinstance(widget, bench.VmcTile):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(app)
        return sorted(found, key=lambda t: (t.number, t.side))


class TheLineCards(PanelWindow):
    """BENCH.md L1 and L2. `pressure.Lines.handle()` was called by tests
    alone: nothing on the bench could make a line DISPENSE, so the gross
    test that follows a dispense, the abort a dispense causes and the
    Continuous Handle alarm were all written and all unreachable."""

    def a_line(self):
        app = self.app
        c = app.console
        c.set_module("plld", 1)
        c.values["S78101"] = "011"
        c.values["S78201"] = "01UNLEADED REGULAR   "
        c.values["S78501"] = "0101"
        c.tick()
        app._refresh_site()
        cards = {(k.kind, k.n): k for k in self._cards(app)}
        # the preset may carry lines of its own; line 1 is the one under test
        self.assertIn(("plld", 1), cards)
        return c, cards[("plld", 1)]

    @staticmethod
    def _cards(app):
        found = []

        def walk(widget):
            if isinstance(widget, bench.LineCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(app)
        return sorted(found, key=lambda k: (k.kind, k.n))

    def test_lifting_the_handle_makes_the_console_dispense(self):
        c, card = self.a_line()
        ln = c.lines.line("plld", 1)
        self.assertFalse(ln.handle)
        self.assertEqual(card.pill.cget("text"), "HANDLE DOWN")
        card.toggle()
        self.assertTrue(ln.handle)
        self.assertEqual(ln.state, "DISPENSING")
        self.assertTrue(ln.pump)
        self.assertEqual(card.pill.cget("text"), "HANDLE UP")
        self.assertEqual(card.pump.cget("text"), "PUMP ON")

    def test_hanging_it_up_starts_the_gross_test(self):
        """"A gross test always follows the completion of a dispense"."""
        c, card = self.a_line()
        card.toggle()
        card.toggle()
        ln = c.lines.line("plld", 1)
        self.assertFalse(ln.handle)
        self.assertTrue(ln.running(), "no test followed the dispense")
        self.assertEqual(ln.rate_key, "gross")
        self.assertEqual(card.pill.cget("text"), "HANDLE DOWN")

    def test_the_card_reads_what_the_diagnostic_screen_reads(self):
        """One pressure, drawn twice: the card must never disagree with the
        PRESSURE LINE LEAK DIAG screen about the line it is standing for."""
        c, card = self.a_line()
        card.toggle()
        c.clock_offset += 3.0
        c.tick()
        card.sync()
        ln = c.lines.line("plld", 1)
        screen = ln.screen()[0]                  # "Q 1: 32.801 PSI  PUMP ON"
        self.assertIn(f"{ln.pressure:6.3f} PSI", screen)
        self.assertIn(f"{ln.pressure:6.3f} PSI", card.psi.cget("text"))
        self.assertEqual(card.state.cget("text"), ln.state)

    def test_a_handle_left_up_for_a_shift_posts_the_alarm(self):
        """577013-344 p.19: "A continuous Pump-in signal will activate a
        Continuous Handle alarm after 16 hours"."""
        from tls350sim import pressure
        c, card = self.a_line()
        card.toggle()
        self.assertNotIn("211601", c.conditions())
        c.clock_offset += pressure.HANDLE_ALARM_HOURS * 3600.0 + 60.0
        c.tick()
        self.assertIn("211601", c.conditions())
        card.toggle()
        self.assertNotIn("211601", c.conditions())


class TheVlldCardHasAPumpSide(PanelWindow):
    """BENCH.md L6: `pump_leak` was written by tests alone, so the three
    PUMP TEST FAIL alarms could be named and never raised."""

    def a_vlld_card(self):
        c = self.c
        c.set_module("vlld", 1)
        c.values["S75101"] = "011"
        c.values["S76001"] = "01DIESEL"
        c.values["S75801"] = "0101"
        c.tick()
        self.app._refresh_site()
        cards = {(k.kind, k.n): k for k in TheLineCards._cards(self.app)}
        self.assertIn(("vlld", 1), cards)
        return c, cards[("vlld", 1)]

    def test_only_a_vlld_card_carries_the_pump_entry(self):
        c, card = self.a_vlld_card()
        self.assertTrue(hasattr(card, "pump_entry"))
        c.set_module("plld", 1)
        c.values["S78101"] = "011"
        c.tick()
        self.app._refresh_site()
        cards = {(k.kind, k.n): k for k in TheLineCards._cards(self.app)}
        self.assertFalse(hasattr(cards[("plld", 1)], "pump_entry"))

    def test_the_entry_is_the_pump_side_leak(self):
        c, card = self.a_vlld_card()
        card.pump_entry.delete(0, "end")
        card.pump_entry.insert(0, "8")
        card.set_pump_leak()                 # what the key release does
        self.assertEqual(c.pump_leak[("vlld", 1)], 8.0)
        c.leaks.start("vlld", 1, "gross")
        c.clock_offset += 600.0
        c.tick()
        self.assertIn("060901", c.compute_alarms())


class TheTankCardsSendTrucks(PanelWindow):
    """BENCH.md T1, T2, T3 and T5. The only way to make a delivery on the
    bench was to drag the float, which is a jump; the console watches the
    LEVEL, and the manual warns a jump may never register as a drop."""

    def a_card(self):
        # the console's clock starts on its first tick; a truck sent before
        # it has one would pour into a minute that never passed
        self.c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.TankCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return sorted(found, key=lambda c: c.n)[0]

    def minutes(self, n):
        for _ in range(n):
            self.c.clock_offset += 60.0
            self.c.tick()
            for sync in self.app._site_sync:
                sync()

    def test_the_card_starts_idle(self):
        card = self.a_card()
        self.assertEqual(card.truck.cget("text"), "SEND TRUCK")
        self.assertEqual(card.truck_lbl.cget("text"), "truck")

    def test_a_truck_pours_and_the_card_says_so(self):
        card = self.a_card()
        self.c.meter_flow = {}
        before = self.c.tank_level[card.n]["volume"]
        drop = card.send_truck(1200, 300, ticket=1200, bol="B-1")
        self.assertIsNotNone(drop)
        self.assertEqual(card.truck.cget("text"), "DROPPING")
        self.minutes(2)
        # the clock is console time on top of REAL time, so a slow machine
        # pours a gallon or two more than the two minutes say
        self.assertAlmostEqual(self.c.tank_level[card.n]["volume"],
                               before + 600.0, delta=10.0)
        self.assertEqual(card.truck_lbl.cget("text"),
                         f"{drop.dropped:,.0f}/1,200")
        self.assertIsNotNone(self.c.deliveries.in_progress(card.n))
        self.minutes(2)
        self.assertEqual(card.truck.cget("text"), "SEND TRUCK")
        self.minutes(8)
        record = self.c.deliveries.last(card.n)
        self.assertIsNotNone(record)
        self.assertEqual(record.ticket, 1200.0)
        self.assertEqual(record.bol, "B-1")

    def test_between_compartments_the_hose_is_off(self):
        card = self.a_card()
        card.send_truck(1200, 300, compartments=2, gap=3.0)
        self.minutes(1)
        self.assertEqual(card.truck.cget("text"), "DROPPING")
        self.minutes(2)                 # the first 600 are down; hose off
        self.assertEqual(card.truck.cget("text"), "HOSE OFF")
        self.minutes(3)                 # the second hose goes on
        self.assertEqual(card.truck.cget("text"), "DROPPING")

    def test_clicking_a_dropping_truck_sends_it_away(self):
        card = self.a_card()
        card.send_truck(3000, 300)
        self.minutes(1)
        card._truck_click()
        self.assertNotIn(card.n, self.c.drops.running)
        self.assertEqual(card.truck.cget("text"), "SEND TRUCK")
        self.assertEqual(card.truck_lbl.cget("text"), "truck")

    def test_the_quiet_adjust_moves_the_level_and_no_watcher(self):
        card = self.a_card()
        before = self.c.tank_level[card.n]["volume"]
        card.adjust(-400)
        self.assertAlmostEqual(self.c.tank_level[card.n]["volume"],
                               before - 400.0)
        card.adjust(+900)
        self.minutes(10)
        self.assertIsNone(self.c.deliveries.last(card.n))
        self.assertIsNone(self.c.deliveries.in_progress(card.n))


class TheInputTiles(PanelWindow):
    """BENCH.md P1. Category 05 had no producer, so nothing on the bench
    could close a contact and the generator log had no caller."""

    def tiles(self, kind="11", tanks=""):
        c = self.c
        c.set_module("io", 1)
        c.values["S80101"] = "011"
        c.values["S80201"] = "01BURGLAR ALM"
        c.values["S80C01"] = "01" + kind + tanks
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.InputTile):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return {t.n: t for t in found}

    def test_one_tile_per_configured_input(self):
        tiles = self.tiles()
        self.assertEqual(sorted(tiles), [1])
        self.assertEqual(tiles[1].pill.cget("text"), "OFF")

    def test_clicking_closes_the_contact_and_the_alarm_posts(self):
        tile = self.tiles()[1]
        tile.click()
        self.assertTrue(self.c.inputs.is_on(1))
        self.assertEqual(tile.pill.cget("text"), "ON")
        self.assertIn("050301", self.c.compute_alarms())
        tile.click()
        self.assertFalse(self.c.inputs.is_on(1))
        self.assertEqual(tile.pill.cget("text"), "OFF")

    def test_a_generator_tile_says_running(self):
        tile = self.tiles("21", "01")[1]
        self.assertEqual(tile.note.cget("text"), "generator \u00b7 T1")
        self.c.tick()
        self.assertTrue(self.c.leaks.active("tank", 1))
        tile.click()
        self.assertEqual(tile.pill.cget("text"), "ON")
        self.assertFalse(self.c.leaks.active("tank", 1))

    def test_the_remote_button_is_momentary(self):
        tile = self.tiles("41")[1]
        self.assertEqual(tile.pill.cget("text"), "PRESS")
        tile.click()
        self.assertFalse(self.c.inputs.is_on(1))
        self.assertEqual(tile.pill.cget("text"), "PRESS")


class TheSensorTilesShowTheWholeWord(PanelWindow):
    """Reported from the bench: "the words are cut off and not visible".

    Two causes, and only one of them was about sensors. The tile put the
    type and the state side by side on one 178px line and hard-cut the
    location label at sixteen characters, so a twenty-character label lost
    its last four and the STATE -- the one thing being read -- was given
    whatever width the type had not already taken. And every dimension in
    `bench.py` was measured at 96 DPI on a screen that is not at 96 DPI:
    at 150% the fonts come out half again as large and the tiles do not,
    so the words no longer fit in them whatever they say.
    """

    def tiles(self, label="STP SUMP TANK 1 UNLE", kind="3"):
        c = self.c
        c.set_module("liquid", 1)
        c.values["S70101"] = "011"
        c.values["S70201"] = "01" + label.ljust(20)
        c.values["S70301"] = "01" + kind
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.SensorTile):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return found

    def words(self, tile):
        out = []

        def walk(widget):
            try:
                text = str(widget.cget("text"))
            except Exception:
                text = ""
            if text.strip():
                out.append(text.strip())
            for child in widget.winfo_children():
                walk(child)

        walk(tile)
        return out

    def test_the_whole_location_label_is_on_the_tile(self):
        """Twenty characters is what S702 holds and what the console
        prints, so twenty characters is what the tile has to show."""
        tile = self.tiles()[0]
        self.assertIn("STP SUMP TANK 1 UNLE", self.words(tile))

    def test_the_whole_type_is_on_the_tile(self):
        tile = self.tiles()[0]
        self.assertTrue(any("dual float hydrostatic" in w
                            for w in self.words(tile)), self.words(tile))

    def test_the_position_is_there_even_once_it_has_a_name(self):
        """A location label hides which sensor this is, and every screen
        and every serial reply names it by the position."""
        tile = self.tiles(label="MW-1 NORTH")[0]
        self.assertTrue(any(w.startswith("LIQUID 1") for w in
                            self.words(tile)), self.words(tile))

    def test_the_longest_state_the_sensor_can_report_still_fits(self):
        """The pill is asked for its own worst case rather than the state
        it happens to be in: a tile that fits NORMAL and clips WATERWARN
        is a tile that breaks the first time it is used for anything."""
        tile = self.tiles()[0]
        import tkinter.font as tkfont
        font = tkfont.Font(root=self.app, font=tile.pill.cget("font"))
        states = ["normal"] + list(self.c.sensor_states("liquid", 1))
        widest = max(font.measure(s.upper()) for s in states)
        room = tile.winfo_reqwidth() - 2 * int(tile.pill.cget("padx")) - 18
        self.assertGreaterEqual(room, widest)

    def test_a_tile_is_as_wide_as_what_is_written_on_it(self):
        """No constant: the label is the widest thing on it, and the tile
        has to be at least that wide however the fonts come out."""
        tile = self.tiles()[0]
        import tkinter.font as tkfont
        font = tkfont.Font(root=self.app, font=bench.FONT_HEAD)
        self.assertGreaterEqual(tile.winfo_reqwidth(),
                                font.measure("STP SUMP TANK 1 UNLE"))


class TheBenchKnowsWhatAPixelIsWorth(PanelWindow):
    """Every dimension in `bench.py` was measured on a 96 DPI screen. The
    console face has had `SimApp.S()` for this since it was drawn from a
    photograph; the bench went without, and clipped its own words on any
    machine Windows is scaling."""

    def tearDown(self):
        bench.set_scale(self.app.px)

    def test_a_bench_pixel_follows_the_screen(self):
        bench.set_scale(1.0)
        self.assertEqual(bench.S(178), 178)
        bench.set_scale(1.5)
        self.assertEqual(bench.S(178), 267)
        bench.set_scale(2.0)
        self.assertEqual(bench.S(178), 356)

    def test_it_never_shrinks_what_was_measured(self):
        """The constants are minimums off a real layout. A screen that
        reports less than 96 DPI does not make the words narrower."""
        bench.set_scale(0.5)
        self.assertEqual(bench.S(178), 178)

    def test_the_app_tells_the_bench_before_a_tile_is_built(self):
        self.assertAlmostEqual(bench.S(100) / 100.0, self.app.px, places=1)

    def test_the_traffic_curve_draws_to_the_canvas_it_got(self):
        """The canvas is made at S(W) x S(H) for the screen in use. A sync
        laying its bars out against the raw 92 left the bottom 68 of a
        138px widget blank, with every bar squashed into the top half."""
        curve = bench.TrafficCurve(self.app, self.app)
        self.c.traffic.on = True
        self.c.traffic.set_level("BUSY")
        curve.sync()
        boxes = [curve.coords(i) for i in curve.find_all()
                 if len(curve.coords(i)) == 4]
        self.assertTrue(boxes)
        floor = max(b[3] for b in boxes)
        # the bars stand on a floor near the bottom of the real canvas,
        # not near the bottom of a 96 DPI one
        self.assertGreater(floor, bench.S(curve.H) * 0.6)
        self.assertLessEqual(floor, bench.S(curve.H))
        curve.destroy()

    def test_a_grid_told_a_width_scales_it_and_one_told_none_measures(self):
        bench.set_scale(2.0)
        told = bench.FlowGrid(self.app, 100)
        self.assertEqual(told.tile_w, 200)
        measured = bench.FlowGrid(self.app)
        self.assertIsNone(measured.tile_w)
        told.destroy()
        measured.destroy()


class TheTrafficViewSaysWhyNothingIsHappening(PanelWindow):
    """Reported from the bench: BUSY, 60x, and "never saw the pressure
    increase, never saw handle on, or any traffic".

    The console it was reported from had no DIM in it, so it had no meter,
    so there was nothing for a car to buy -- and the view said RUNNING in
    green and drew the curve anyway. The banner is worth a test of its own
    because `_poll` calls every sync inside a bare `except: pass`, so a
    banner that threw would go back to saying nothing at all.
    """

    def bare(self):
        c = self.c
        c.software["bir"] = False
        c.modules.pop("edim", None)
        c.modules.pop("mdim", None)
        c.meters.clear()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        self.app.refresh_traffic()
        self.app._sync_traffic_head()
        return self.words(self.app._traffic_why)

    def words(self, widget, out=None):
        out = [] if out is None else out
        try:
            text = str(widget.cget("text")).strip()
        except Exception:
            text = ""
        if text:
            out.append(text)
        for child in widget.winfo_children():
            self.words(child, out)
        return out

    def test_it_says_running_and_nothing_is_being_sold(self):
        said = self.bare()
        self.assertTrue(any("NOTHING IS BEING SOLD" in w for w in said), said)
        self.assertTrue(any("DIM" in w for w in said), said)

    def test_each_reason_offers_the_view_that_fixes_it(self):
        self.app._switch.select("Site")
        said = self.bare()
        self.assertIn("MODULES \u2192", said)

    def test_it_does_not_offer_a_link_to_the_view_you_are_on(self):
        """The blend reason is fixed on this view, and a link to the view
        somebody is already looking at is the kind of dead click this
        banner exists to stop."""
        self.c.blends[self.c.meter_key(1)] = {"label": "MID", "parts": []}
        self.app._switch.select("Traffic")
        said = self.bare()
        self.assertIn("BELOW \u2193", said)
        self.assertNotIn("TRAFFIC \u2192", said)

    def test_it_clears_itself_when_the_console_is_fixed(self):
        """Mapping a meter happens on the SITE view, which does not rebuild
        this one -- so the banner has to notice on its own."""
        self.assertTrue(self.bare())
        self.c.software["bir"] = True
        self.c.set_module("edim", 1)
        self.c.meters[self.c.meter_key(1)] = 1
        self.app._sync_traffic_head()
        self.assertEqual(self.words(self.app._traffic_why), [])

    def test_a_closed_site_is_reported_as_a_state_and_not_a_fault(self):
        self.c.software["bir"] = True
        self.c.set_module("edim", 1)
        self.c.meters[self.c.meter_key(1)] = 1
        self.c.traffic.on = True
        self.c.traffic.set_level("CLOSED")
        self.app.refresh_traffic()
        self.app._sync_traffic_head()
        said = self.words(self.app._traffic_why)
        self.assertTrue(any("THE SITE IS CLOSED" in w for w in said), said)

    def test_switched_off_says_what_off_means(self):
        self.c.traffic.on = False
        self.app.refresh_traffic()
        self.app._sync_traffic_head()
        said = self.words(self.app._traffic_why)
        self.assertTrue(any("GENERATOR IS OFF" in w for w in said), said)


class TheBlendCardsDoNotDeadEnd(PanelWindow):
    """Reported from the bench: "I was unable to pick a blended grade, I
    could not select anything."

    A blend runs component METERS, and a console with no DIM in it has
    none -- so `+ COMPONENT` found an empty list and returned in silence.
    It is the one pill on the Traffic view that looks like it could be set
    up without a DIM, which is exactly why it is the one that got clicked.
    """

    def card(self):
        self.c.blends[self.c.meter_key(1)] = {"label": "MID", "parts": []}
        self.app.refresh_traffic()
        found = []

        def walk(widget):
            if isinstance(widget, bench.BlendCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return found[0]

    def test_with_no_meters_the_click_says_why_instead_of_nothing(self):
        self.c.meters.clear()
        card = self.card()
        before = self.app.logbox.get("1.0", "end")
        card._add_part()
        said = self.app.logbox.get("1.0", "end")[len(before):]
        self.assertEqual(self.c.blends[card.meter]["parts"], [])
        self.assertIn("no meter is mapped", said)

    def test_and_the_card_itself_says_it_too(self):
        self.c.meters.clear()
        card = self.card()
        words = []

        def walk(widget):
            try:
                words.append(str(widget.cget("text")))
            except Exception:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(card.rows)
        self.assertTrue(any("no meter is mapped" in w for w in words), words)
        self.assertEqual(card.add.cget("fg"), bench.FAINT)

    def test_with_a_meter_mapped_it_adds_one(self):
        self.c.set_module("edim", 1)
        self.c.meters[self.c.meter_key(3)] = 1
        card = self.card()
        card._add_part()
        self.assertEqual(len(self.c.blends[card.meter]["parts"]), 1)
        self.assertEqual(card.add.cget("fg"), bench.ACCENT)


class TheDayTallyCanActuallyBeRead(PanelWindow):
    """Every counter on this view has been added by fixing a counter that
    could not be read, and the header kept absorbing them.

    With all of them populated the single right-anchored label wanted
    1,563px and was given 1,198 on a 1400px window -- so the dry-tank count
    and yesterday's closing figure were invisible at any window size, the
    two most recently added. The day's exceptions now have their own
    wrapped line.
    """

    def a_busy_day(self):
        c = self.c
        c.software["bir"] = True
        c.set_module("edim", 1)
        c.meters[c.meter_key(1)] = 1
        c.tick()
        c.traffic.on = True
        today = c.traffic._tally()
        today.cars, today.gallons = 12345, 98765.0
        today.balked, today.blocked = 123, 45
        today.dry, today.deliveries = 67, 8
        was = c.traffic._tally(time.mktime(c.now()) - 86400.0)
        was.cars, was.gallons = 13579, 111222.0
        self.app.refresh_traffic()
        self.app._sync_traffic_head()
        return (str(self.app._traffic_now.cget("text")),
                str(self.app._traffic_tally.cget("text")))

    def test_every_tally_appears_somewhere(self):
        now, tally = self.a_busy_day()
        both = now + "  " + tally
        for want in ("12,345", "98,765", "123", "45", "67", "8",
                     "13,579", "111,222"):
            self.assertIn(want, both, want)

    def test_the_exceptions_are_not_crammed_onto_the_first_line(self):
        """The line the RUNNING pill shares stays short enough to fit."""
        now, tally = self.a_busy_day()
        for stray in ("queued away", "shutdown", "tank empty", "yesterday"):
            self.assertNotIn(stray, now, stray)
            self.assertIn(stray, tally, stray)

    def test_the_second_line_wraps_rather_than_running_off(self):
        self.a_busy_day()
        label = self.app._traffic_tally
        self.assertEqual(str(label.cget("justify")), "left")
        # `_rewrap` is what keeps it inside whatever room it is given
        self.assertTrue(label.bind("<Configure>"))

    def test_a_quiet_day_says_nothing_extra(self):
        c = self.c
        c.traffic.on = True
        today = c.traffic._tally()
        today.cars, today.gallons = 40, 480.0
        today.balked = today.blocked = today.dry = today.deliveries = 0
        self.app.refresh_traffic()
        self.app._sync_traffic_head()
        self.assertEqual(str(self.app._traffic_tally.cget("text")), "")


class TheGeneratorSwitchLeavesYourOwnNozzleAlone(PanelWindow):
    """`Traffic`'s docstring promises that OFF means "the bench behaves
    exactly as it did before it existed", and the handler's own comment
    says it hangs up "every nozzle THIS GENERATOR is holding up".

    It stopped every sale on the site. A technician's own fill, lifted by
    hand from the meter card before the switch was touched, was hung up at
    zero gallons by a switch that had nothing to do with it.
    """

    def a_forecourt(self):
        c = self.c
        c.software["bir"] = True
        c.set_module("edim", 1)
        c.meters[c.meter_key(1)] = 1
        c.meters[c.meter_key(2)] = 1
        c.tick()
        return c

    def test_switching_it_off_does_not_hang_up_a_hand_lifted_nozzle(self):
        c = self.a_forecourt()
        mine = c.meter_key(1)
        c.sales.start(mine, 300.0)              # a big fill, by hand
        self.assertIsNone(c.sales.running[mine].by)
        self.app._traffic_off()                 # generator on
        self.app._traffic_off()                 # and off again
        sale = c.sales.running.get(mine)
        self.assertIsNotNone(sale, "the bench's own fill was hung up")
        self.assertEqual(sale.gallons, 300.0)

    def test_it_does_hang_up_its_own(self):
        c = self.a_forecourt()
        c.sales.start(c.meter_key(2), 300.0, by="traffic")
        self.app._traffic_off()
        self.app._traffic_off()
        self.assertNotIn(c.meter_key(2), c.sales.running)

    def test_a_car_is_marked_as_the_generator_s(self):
        """Every nozzle the generator lifts carries its name.

        **Two wall clocks were in this and both of them are gone.** The
        arrival rate is the rate at THIS HOUR of the console's day, and the
        DAY curve's 03:00 weight is 2 against the 17:00 peak's 84 -- so a
        run at three in the morning earned about one car in the twenty
        console minutes this walks, and Poisson's answer to a mean of one
        is nothing rather often. The clock is pinned to the evening peak.
        And the assertion read `sales.running` AFTER the last tick, which
        is a snapshot of one instant on a forecourt where a sale lasts a
        couple of minutes: twenty-two cars went through and the check still
        failed whenever the last of them had hung up. It watches the whole
        run now. See NOTES, "a test that reads the wall clock".
        """
        c = self.a_forecourt()
        peak = list(time.localtime())
        peak[3:6] = [17, 0, 0]
        c.clock_offset = time.mktime(time.struct_time(peak)) - time.time()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        lifted = set()
        for _ in range(40):
            c.clock_offset += 30.0
            c.tick()
            lifted.update(s.by for s in c.sales.running.values())
        self.assertTrue(lifted, "no nozzle went up at BUSY")
        self.assertEqual(lifted, {"traffic"})


class TheBlendCardAddsRealComponents(PanelWindow):
    """Two siblings of the dead `+ COMPONENT` click, and a card whose
    percentages were not the percentages."""

    def a_card(self):
        c = self.c
        c.software["bir"] = True
        c.set_module("edim", 1)
        for n in (1, 2, 3):
            c.meters[c.meter_key(n)] = 1
        c.blends[c.meter_key(9)] = {"label": "E15", "parts": []}
        c.tick()
        self.app.refresh_traffic()
        found = []

        def walk(w):
            if isinstance(w, bench.BlendCard):
                found.append(w)
            for ch in w.winfo_children():
                walk(ch)

        walk(self.app)
        return found[0]

    def test_the_shares_are_an_even_split_that_sums_to_a_hundred(self):
        """Every component used to be added at 50 -- the ternary that
        chose the number had the same value in both arms -- so three
        components showed three rows of "50% of" totalling 150 while the
        engine quietly ran them at a third each."""
        card = self.a_card()
        for expect in (1, 2, 3):
            card._add_part()
            parts = self.c.blends[card.meter]["parts"]
            self.assertEqual(len(parts), expect)
            # a tenth of a percent of slack: three components round to
            # 33.3 each, which is what any card shows for a third
            self.assertAlmostEqual(sum(p[1] for p in parts), 100.0,
                                   delta=0.5)
        # and what the card shows is what the engine runs
        for (_meter, frac), row in zip(self.c.blend_meters(card.meter),
                                       self.c.blends[card.meter]["parts"]):
            self.assertAlmostEqual(frac * 100.0, row[1], places=1)

    def test_running_out_of_meters_says_so_instead_of_nothing(self):
        card = self.a_card()
        mapped = len(self.c.meters)
        for _ in range(mapped):
            card._add_part()
        before = self.app.logbox.get("1.0", "end")
        card._add_part()
        said = self.app.logbox.get("1.0", "end")[len(before):]
        self.assertEqual(len(self.c.blends[card.meter]["parts"]), mapped)
        self.assertIn("already a component", said)

    def test_a_component_whose_meter_went_away_keeps_its_own_name(self):
        """It used to fall back to the FIRST caption in the list, so two
        rows claimed the same meter and editing either one silently
        repointed the blend at a meter nobody chose."""
        card = self.a_card()
        card._blend()["parts"] = [[str(self.c.meter_key(3)), 50.0],
                                  [str(self.c.meter_key(1)), 50.0]]
        self.c.meters.pop(self.c.meter_key(3), None)
        card._draw_parts()
        shown = []

        def walk(w):
            try:
                text = str(w.cget("text")).strip()
                if text:
                    shown.append(text)
            except Exception:
                pass
            for ch in w.winfo_children():
                walk(ch)

        walk(card.rows)
        self.assertTrue(any("NOT MAPPED" in s for s in shown), shown)
        self.assertEqual(self.c.blends[card.meter]["parts"][0][0],
                         str(self.c.meter_key(3)))


class TheRelayTiles(PanelWindow):
    """BENCH.md P2 and P3: the relays follow what they are assigned to,
    and a pump relay's contactor can weld."""

    def tiles(self, kind="1", tank=""):
        c = self.c
        c.set_module("relay", 1)
        c.set_module("io", 1)
        c.values["S80101"] = "011"
        c.values["S80C01"] = "0111"
        c.values["S80601"] = "011"
        c.values["S80701"] = "01HORN"
        c.values["S80A01"] = "01" + kind
        # 808 holds a LIST now: an input alarm on every device. See U48.
        c.assign_relay_alarm(1, "05", "03", "01")
        if tank:
            c.values["S80B01"] = "01" + tank
        c.tick()
        c.compute_alarms()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.RelayTile):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return {t.n: t for t in found}

    def test_both_windows_carry_the_programs_own_icon(self):
        """Nothing set one. `assets/icon.ico` reached the EXE's resource
        and the installer and never the WINDOW, so the console ran under
        Tk's default feather in the title bar, in Alt-Tab and on the
        taskbar.

        Asked of Windows rather than of Tk: `wm iconbitmap` reports only
        what was set on that one window and says nothing about whether it
        took, where `WM_GETICON` is what the taskbar itself reads. It came
        back null on both windows, and `iconbitmap(default=...)` alone --
        the documented application-wide form -- left it null.
        """
        if sys.platform != "win32":
            self.skipTest("the icon is asked of the Windows window manager")
        import ctypes
        u = ctypes.windll.user32
        u.GetAncestor.restype = ctypes.c_void_p
        u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u.SendMessageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_void_p, ctypes.c_void_p]

        def icon(window):
            window.update_idletasks()
            top = u.GetAncestor(ctypes.c_void_p(window.winfo_id()), 2)
            return u.SendMessageW(top, 0x007F, ctypes.c_void_p(1), None)

        for name, window in (("console", self.app),
                             ("bench", self.app.bench_win)):
            self.assertTrue(icon(window), f"{name} window has no icon")

    def test_a_standard_relay_follows_its_alarm(self):
        tile = self.tiles()[1]
        self.assertEqual(tile.pill.cget("text"), "OFF")
        self.c.inputs.set(1, True)
        self.c.compute_alarms()
        tile.sync()
        self.assertEqual(tile.pill.cget("text"), "ON")

    def test_clicking_a_standard_relay_holds_it_on(self):
        tile = self.tiles()[1]
        tile.click()
        self.assertTrue(self.c.relays[1])
        self.assertEqual(tile.pill.cget("text"), "HELD")
        tile.click()
        self.assertFalse(self.c.relays[1])
        self.assertEqual(tile.pill.cget("text"), "OFF")

    def test_clicking_a_pump_relay_welds_the_contactor(self):
        tile = self.tiles(kind="2", tank="01")[1]
        self.assertEqual(tile.note.cget("text"), "pump out \u00b7 T1")
        tile.click()
        self.assertTrue(self.c.outputs.is_welded("11", 1))
        self.assertEqual(tile.pill.cget("text"), "WELDED")
        tile.click()
        self.assertFalse(self.c.outputs.is_welded("11", 1))


class TheMeterCardsSell(PanelWindow):
    """BENCH.md D2: a sale is a nozzle lifted and hung up, not a rate."""

    def a_card(self):
        c = self.c
        c.software["bir"] = True
        c.set_module("edim", 1)
        c.meters = {1: 1}
        c.meter_flow = {}
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.MeterCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return {k.number: k for k in found}[1]

    def test_the_cards_are_on_the_first_edim_by_default(self):
        card = self.a_card()
        self.assertEqual((card.meter.bus, card.meter.slot), (3, 1))
        self.assertEqual(self.app._dim_boards(), [("EDIM 1", 3, 1)])

    def test_choosing_the_mdim_shows_its_own_meters(self):
        """BENCH.md D4: a second board's meter 1 is not the first's, and the
        MDIM on the power bus carries meters 9 to 16."""
        self.a_card()
        self.c.set_module("mdim", 1)
        self.app._refresh_site()
        self.assertEqual(self.app._dim_boards(),
                         [("EDIM 1", 3, 1), ("MDIM", 2, 1)])
        self.app._pick_dim("MDIM")
        found = []

        def walk(widget):
            if isinstance(widget, bench.MeterCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        numbers = sorted(k.number for k in found)
        self.assertEqual(numbers, list(range(9, 17)))
        card = [k for k in found if k.number == 9][0]
        self.assertEqual(card.meter, bench.MeterId(2, 1, 5, 9))
        # mapping it maps THAT meter, and not the default board's 9
        card.tank_var.set("1")
        card.remap()
        self.assertEqual(self.c.meters.get(bench.MeterId(2, 1, 5, 9)), 1)
        self.assertIsNone(self.c.meters.get(9))

    def test_lifting_the_nozzle_sells_the_gallons_in_the_box(self):
        card = self.a_card()
        self.assertEqual(card.pill.cget("text"), "LIFT NOZZLE")
        card.sale_var.set("10")
        card.nozzle()
        self.assertIn(1, self.c.sales.running)
        self.assertEqual(card.pill.cget("text"), "HANG UP")
        self.c.clock_offset += 30.0
        self.c.tick()
        card.sync()
        self.assertTrue(card.progress.cget("text").startswith("5."))
        self.c.clock_offset += 40.0
        self.c.tick()
        card.sync()
        self.assertEqual(card.pill.cget("text"), "LIFT NOZZLE")
        self.assertAlmostEqual(self.c.bir.totals[1], 10.0, delta=0.5)

    def test_clicking_again_hangs_up(self):
        card = self.a_card()
        card.nozzle()
        card.nozzle()
        self.assertNotIn(1, self.c.sales.running)
        self.assertEqual(card.pill.cget("text"), "LIFT NOZZLE")


class TheSmallControls(PanelWindow):
    """BENCH.md T11, L7, L10, V6: a siphon valve on the tank card, a thermal
    slope and a transducer fault on the line card, a run switch on the
    processor."""

    def a_line_card(self):
        c = self.c
        c.set_module("plld", 1)
        c.values["S78101"] = "011"
        c.values["S78501"] = "0101"
        c.tick()
        self.app._refresh_site()
        cards = {(k.kind, k.n): k for k in TheLineCards._cards(self.app)}
        return c, cards[("plld", 1)]

    def test_the_thermal_entry_puts_a_slope_on_the_line(self):
        c, card = self.a_line_card()
        card.thermal_var.set("-1.5")
        card.set_thermal()
        self.assertEqual(c.lines.line("plld", 1).thermal, -1.5)

    def test_the_transducer_pill_faults_the_wire(self):
        c, card = self.a_line_card()
        self.assertEqual(card.xducer.cget("text"), "TRANSDUCER OK")
        card.fault_transducer("open")
        self.assertEqual(c.lines.line("plld", 1).transducer, "open")
        self.assertEqual(card.xducer.cget("text"), "TRANSDUCER OPEN")
        self.assertTrue(card.psi.cget("text").startswith("-1.000"))
        card.fault_transducer(None)
        self.assertEqual(card.xducer.cget("text"), "TRANSDUCER OK")

    def test_a_wplld_card_offers_noise_and_a_plld_card_does_not(self):
        c = self.c
        c.set_module("wplld", 1)
        c.values["S7A101"] = "011"
        c.values["S7A501"] = "0101"
        _c, plld = self.a_line_card()
        cards = {(k.kind, k.n): k for k in TheLineCards._cards(self.app)}
        wplld = cards[("wplld", 1)]
        wplld.fault_transducer("noise")
        self.assertTrue(c.lines.line("wplld", 1).noise)
        self.assertEqual(wplld.xducer.cget("text"), "COMM NOISE")
        self.assertIn("260701", c.conditions())
        wplld.fault_transducer(None)
        self.assertFalse(c.lines.line("wplld", 1).noise)
        self.assertEqual(plld.kind, "plld")

    def test_the_lever_on_the_bench_menu(self):
        app = self.app
        app.lever_down.set(True)
        app._set_lever()
        self.assertTrue(self.c.printer_lever_open)
        self.assertIn("010200", self.c.conditions())
        app.lever_down.set(False)
        app._set_lever()
        self.assertFalse(self.c.printer_lever_open)

    def test_the_siphon_valve_shows_on_a_siphoned_pair_with_the_valve(self):
        c = self.c
        c.values["S61201"] = "0102"
        c.values["S61202"] = "0201"
        c.values["S63201"] = "011"
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.TankCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        cards = {k.n: k for k in found}
        self.assertIsNotNone(cards[1].siphon)
        self.assertEqual(cards[1].siphon.cget("text"), "VALVE OPEN")
        cards[1].toggle_siphon()
        self.assertIn(1, c.siphon_shut)
        self.assertIn(2, c.siphon_shut)
        cards[2].sync()
        self.assertEqual(cards[2].siphon.cget("text"), "VALVE SHUT")
        self.assertIn("022202", c.conditions())

    def test_no_valve_no_pill(self):
        c = self.c
        c.values["S61201"] = "0102"
        c.values["S61202"] = "0201"
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.TankCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        self.assertTrue(all(k.siphon is None for k in found))

    def test_the_processor_switch(self):
        c = self.c
        c.software["isd"] = True
        c.values["SV4000"] = "0001"
        c.tick()
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.ProcessorTile):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        if not found:
            self.skipTest("this preset has no ISD section to hang it on")
        tile = found[0]
        self.assertEqual(tile.pill.cget("text"), "STOPPED")
        tile.toggle()
        self.assertIsNotNone(c.vp_started)
        self.assertEqual(tile.pill.cget("text"), "RUNNING")
        tile.toggle()
        self.assertIsNone(c.vp_started)


class TheOneWidgetControls(PanelWindow):
    """BENCH.md P5, P9, P10, P11: each one widget against one attribute."""

    def test_dip_sw2_4_is_the_fiscal_height_switch(self):
        app = self.app
        app._sw_fiscal.set(True)
        app._set_fiscal_height()
        self.assertTrue(self.c.fiscal_height_switch)
        app._sw_fiscal.set(False)
        app._set_fiscal_height()
        self.assertFalse(self.c.fiscal_height_switch)

    def test_the_comm_error_control_writes_the_record(self):
        app = self.app
        port = int(app._err_port.get())
        app._err_word.set(" 3 MODEM TIMED OUT")
        app._log_comm_error()
        self.assertEqual(self.c.comm_errors[port][-1]["error"], 3)
        self.assertIn(port, self.c.comm_error_at)
        app._clear_comm_errors()
        self.assertNotIn(port, self.c.comm_errors)

    def test_the_visit_is_logged_under_the_key_in_the_box(self):
        app = self.app
        app.key_id.set("B77777")
        app.service_var.set("0101 REPROGRAMMED TLS")
        app._log_visit()
        self.assertEqual(self.c.service_log()[0]["id"], "B77777")
        self.assertEqual(self.c.service_log()[0]["code"], "0101")

    def test_the_identity_plate_reaches_the_audit_trail(self):
        app = self.app
        app.serial_var.set("SN-000123")
        app.wm_var.set("COUNTY SEALER")
        app._set_identity()
        self.assertEqual(self.c.serial_number, "SN-000123")
        self.assertIn("COUNTY SEALER", self.c.audit_report(1))


class TheBenchAndTheConsoleAgree(PanelWindow):
    """`bench.py`'s module docstring states the invariant: "the probe's own
    arithmetic stays the console's ... what the bench draws and what the
    console reports never disagree". It drew the product float at
    `volume / full * D`, a straight line, against the console's `height_at`,
    the circular chart -- they met at half full and nowhere else, by nearly
    seven inches at the ends. FIDELITY Y9."""

    def _cards(self):
        # the cards bind to the console they were built against, and setUp
        # hands the app a fresh one without rebuilding the site view
        self.app._refresh_site()
        found = []

        def walk(widget):
            if isinstance(widget, bench.TankCard):
                found.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(self.app)
        return sorted(found, key=lambda c: c.n)

    def test_the_float_is_drawn_where_the_console_says_it_is(self):
        card = self._cards()[0]
        full = self.c.full_volume(card.n)
        self.assertTrue(full)
        for part in (0.05, 0.13, 0.25, 0.5, 0.75, 0.95):
            volume = full * part
            self.c.tank_level[card.n]["volume"] = volume
            _st, fuel_h, _water_h = card._levels()
            self.assertAlmostEqual(fuel_h,
                                   self.c.height_at(card.n, volume),
                                   places=6, msg=f"{part:.0%}")

    def test_and_it_is_not_the_straight_line_it_used_to_be(self):
        """A circular tank is only linear at half full, so a test that
        passed on both rules would be testing nothing."""
        card = self._cards()[0]
        full = self.c.full_volume(card.n)
        self.c.tank_level[card.n]["volume"] = full * 0.05
        _st, fuel_h, _w = card._levels()
        self.assertGreater(abs(fuel_h - 0.05 * card.D), 2.0)

    def test_dragging_the_float_puts_back_what_the_console_would_read(self):
        card = self._cards()[0]
        self.c.tank_level[card.n]["water"] = 0.0
        card.dragging = "p"
        for depth in (12.0, 24.0, 72.0):
            card._drag(type("E", (), {"y": card._y(depth)})())
            volume = self.c.tank_level[card.n]["volume"]
            self.assertAlmostEqual(self.c.height_at(card.n, volume), depth,
                                   places=4)
        card.dragging = None
