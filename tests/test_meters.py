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
"""Meter detail: the map, the offsets, and what an offset actually does.

A calibration offset that nothing applies is a stored number, not a setting.
The observable consequence of one is a BIR variance -- the meter's figure and
the probe's disagree by the offset -- and that is the whole reason the setting
exists, so it is what these tests check.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, wirelists                   # noqa: E402
from tls350sim.console import Console, FIELDS              # noqa: E402
from tls350sim.meterid import MeterId                      # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("smart", "universal", "probe"):
        c.modules[card] = 4
    # The comm bay is FOUR slots, three of them single-port, so a console
    # cannot carry a modem, a VMCI, an EDIM, an RS-232 port and a
    # Maintenance Tracker port as five separate cards. The dual-port MT
    # module 330586-017 is what a real site does about that: it takes slot 4
    # and answers on two positions, a general serial port on 5 and the
    # Maintenance Tracker on 6. See FIDELITY M7.
    c.modules.update({"rs232": 0, "modem": 1, "vmc": 1, "edim": 1, "mt4": 1})
    c.software.update({"bir": True, "fuelman": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def body(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10))


def refused(h, cmd):
    return body(h, cmd).startswith("9999")


class ThePanelAndTheWireMeetOnDeviceZero(unittest.TestCase):
    """7B2's format is S7B200 -- device 00, not 01. The setup step used to
    write S7B201, so a value programmed on the panel was invisible over the
    wire and a value set over the wire was invisible on the panel. They were
    two settings wearing one name."""

    def test_the_field_is_on_device_00(self):
        self.assertIn("S7B200", FIELDS)
        self.assertNotIn("S7B201", FIELDS)

    def test_the_step_writes_the_code_the_manual_gives(self):
        from tls350sim.console import SETUP_MENU
        codes = [st.get("code") for menu in SETUP_MENU
                 for st in menu.get("steps", [])
                 if (st.get("code") or "").startswith("S7B2")]
        self.assertEqual(codes, ["S7B200"])

    def test_what_the_wire_writes_the_wire_reads(self):
        c, h = a_site()
        self.assertFalse(refused(h, "S7B200" + "+1.500"))
        self.assertEqual(c.meter_offset(1), 1.5)


class TheDisplayLineIsTheManualsOwn(unittest.TestCase):

    def test_7b2_prints_what_the_manual_prints(self):
        """"METER CALIBRATION / OFFSET: 0.000%" -- including the precision
        and the per-cent sign, which a bare %g drops."""
        _c, h = a_site()
        send(h, "S7B200" + "+0.000")
        # [3:], not [2:]: the code, the stamp, and then one blank line
        # before the body, which every display sample in the manual leaves
        # and this console did not. FIDELITY S6.
        shown = "|".join(body(h, "I7B200").splitlines()[3:])
        # TWO spaces, not one. 576013-635 Rev AA p.440 sets OFFSET: at
        # column 0 and 0.000% at column 9, and the hand-counted single space
        # here was the one column of difference. The columns come off the
        # page now; see FIDELITY S1.
        self.assertEqual(shown, "METER CALIBRATION|OFFSET:  0.000%")

    def test_the_computer_format_is_not_dressed_up(self):
        """A tool reads the float. Only the display line is formatted."""
        _c, h = a_site()
        send(h, "S7B200" + "+1.000")
        self.assertIn("3F800000", body(h, "i7B200"))

    def test_other_codes_print_their_manual_line_too(self):
        _c, h = a_site()
        send(h, "S56400" + "1")
        self.assertIn("ULLAGE: 95%", body(h, "I56400"))
        send(h, "S55600" + "0")
        self.assertIn("LINE PER TST NEEDED WRN: DISABLED", body(h, "I55600"))


class TheIndividualOffset(unittest.TestCase):

    def test_7b4_has_no_computer_format(self):
        """"Computer format is not supported" -- the third code that says so,
        after 680 and 7B1."""
        _c, h = a_site()
        self.assertFalse(refused(h, "I7B400"))
        self.assertTrue(refused(h, "i7B400"))
        self.assertTrue(refused(h, "s7B400" + "010101+0.00"))

    def test_it_wants_position_meter_tank_and_a_signed_percent(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S7B400" + "01" + "02" + "01" + "-2.50"))
        self.assertTrue(refused(h, "S7B400" + "01" + "02" + "01" + "2.50"))
        self.assertTrue(refused(h, "S7B400" + "0102" + "01" + "+2.5"))

    def test_the_percent_is_bounded_at_nine_nine_nine(self):
        """"Meter Offset, percent (Decimal +/-9.99)"."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S7B400" + "010101" + "+9.99"))
        self.assertTrue(refused(h, "S7B400" + "010101" + "+9.999"))

    def test_the_specific_offset_beats_the_site_one(self):
        c, h = a_site()
        send(h, "S7B200" + "+1.000")
        send(h, "S7B400" + "01" + "02" + "01" + "-2.50")
        self.assertEqual(c.meter_offset(1), 1.0)     # the site figure
        self.assertEqual(c.meter_offset(2), -2.5)    # its own


class AnOffsetMakesAVariance(unittest.TestCase):
    """The point of the setting, and the only way to tell it is applied."""

    def dispense(self, offset_cmd):
        c, h = a_site()
        if offset_cmd:
            send(h, offset_cmd)
        c.meters[1] = 1
        c.meter_flow[1] = 100.0
        before = c.tank_level[1]["volume"]
        # the fuel leaves through `Sales.draw` and BIR books what it drew,
        # in the order `Console.tick` runs them. `_dispense` alone moves
        # nothing any more, and read an empty book.
        c.sales.draw(1.0)
        c.bir._dispense(1.0)
        drop = before - c.tank_level[1]["volume"]
        return drop, c.bir.totals[1]

    def test_a_sound_meter_agrees_with_the_probe(self):
        drop, metered = self.dispense(None)
        self.assertAlmostEqual(drop, metered, places=6)

    def test_a_five_percent_meter_reports_five_percent_more(self):
        drop, metered = self.dispense("S7B200" + "+5.000")
        self.assertAlmostEqual(drop, 100.0, places=6)
        self.assertAlmostEqual(metered, 105.0, places=6)

    def test_a_negative_offset_reports_less(self):
        drop, metered = self.dispense("S7B200" + "-5.000")
        self.assertAlmostEqual(metered, 95.0, places=6)

    def test_the_tank_loses_the_same_fuel_either_way(self):
        """The offset changes what the METER says, not what left the tank.
        Getting this backwards would make a mis-calibrated meter move
        product, which is not what a calibration setting does."""
        plain, _ = self.dispense(None)
        offset, _ = self.dispense("S7B200" + "+5.000")
        self.assertAlmostEqual(plain, offset, places=6)


class TheManualsOwnMapCommands(unittest.TestCase):
    """FIDELITY G6. 576013-635 p.439 writes the command
    `<SOH>S7B100 B SS FP MM TT`, spaces and all, and 576013-818 pp.12-11 and
    12-12 use that form in five worked commands a technician is told to
    send. `wirelists.py` sliced a contiguous nine-character body, so **every
    meter-map command printed in the manuals was rejected** while the packed
    form nobody writes was accepted.
    """

    def empty(self):
        c, h = a_site()
        c.meter_map.clear()
        c.meters.clear()
        return c, h

    def test_the_worked_commands_are_accepted(self):
        """The three p.12-12 maps with, unpadded and spaced as it writes
        them: to a tank, to a probeless tank, and off the map again."""
        _c, h = self.empty()
        for cmd in ("S7B100 3 1 18 1 1", "S7B100 3 1 18 3 -1",
                    "S7B100 3 1 18 4 0"):
            self.assertFalse(refused(h, cmd), cmd)

    def test_a_set_answers_with_the_row_it_wrote(self):
        """Every one of p.12-12's four worked responses is the report's own
        head over the single row the command wrote -- `3  1  18  1  1` for
        `S7B100 3 1 18 1 1`. This answered a bare frame."""
        _c, h = self.empty()
        rows = body(h, "S7B100 3 1 18 1 1").splitlines()
        self.assertIn("FUELING POSITION - METER - TANK MAP", rows)
        self.assertEqual(rows[-1], wirelists.map_row("3", "1", "18", "1", "1"))

    def test_a_removal_answers_with_a_dash_and_the_probeless_one_an_x(self):
        """"Removing FP18/M4 from the map ... TANK  -", and the probeless
        response prints `X`. Neither is the map's own `?`, which is a meter
        the POS has reported and nobody has mapped."""
        _c, h = self.empty()
        self.assertTrue(
            body(h, "S7B100 3 1 18 3 -1").splitlines()[-1].endswith("X"))
        self.assertTrue(
            body(h, "S7B100 3 1 18 4 0").splitlines()[-1].endswith("-"))

    def test_the_packed_form_still_is(self):
        """A tool that sends the fixed columns is not broken by this."""
        _c, h = self.empty()
        self.assertFalse(refused(h, "S7B100303001001"))

    def test_the_manuals_own_rejected_command_is_echoed_not_refused(self):
        """FIDELITY G6a, and the entry that asked for it had the example
        backwards. 576013-818 p.12-12 heads `S7B100 3 1 108 3 2` "Example of
        A Rejected Command with the Fueling Position Out of Range" and
        answers it with the report, `??` where the position was: "All
        parameters are checked before the command is performed. If an error
        is detected, the command parameters will be repeated with the
        parameter in error replaced with ??". A 9999 has no parameter to
        point at, and both manuals bound the field -- "FP - Fueling Position
        (00-99)"."""
        c, h = self.empty()
        self.assertFalse(refused(h, "S7B100 3 1 108 3 2"))
        self.assertEqual(body(h, "S7B100 3 1 108 3 2").splitlines()[-1],
                         wirelists.map_row("3", "1", "??", "3", "2"))
        self.assertEqual(dict(c.meter_map), {},
                         "checked BEFORE the command is performed")

    def test_every_parameter_in_error_is_marked_at_once(self):
        """"All parameters are checked" -- all of them, so two mistakes come
        back as two `??`."""
        _c, h = self.empty()
        self.assertEqual(body(h, "S7B100 3 1 108 300 2").splitlines()[-1],
                         wirelists.map_row("3", "1", "??", "??", "2"))

    def test_a_bad_bus_is_echoed_too(self):
        """"B = bus (2 or 3)", and 9 is neither."""
        c, h = self.empty()
        self.assertEqual(body(h, "S7B100 9 1 18 1 1").splitlines()[-1],
                         wirelists.map_row("??", "1", "18", "1", "1"))
        self.assertEqual(dict(c.meter_map), {})

    def test_but_a_short_command_is_refused(self):
        """Five fields is what the command IS. Four of them is not a 7B1
        with a bad parameter in it, so there is no parameter to point at
        and the wire's own 9999 is the answer."""
        _c, h = self.empty()
        self.assertTrue(refused(h, "S7B100 3 1 18 1"))

    def test_an_empty_map_says_so_in_the_manuals_words(self):
        """"The response from the I7B100 command should be TANK MAP
        EMPTY", 576013-818 p.12-12 -- the check a technician is told to make
        after clearing the map."""
        _c, h = self.empty()
        self.assertIn("TANK MAP EMPTY", body(h, "I7B100"))

    def test_the_tank_column_is_the_manuals_symbols(self):
        """p.12-11 prints all four forms in one sample: `1` mapped, `?`
        reported and not mapped, `X` probeless, `R` retired, and `2*` mapped
        and locked."""
        self.assertEqual(wirelists.map_tank({"tank": 1}), "1")
        self.assertEqual(wirelists.map_tank({"tank": 2, "locked": True}), "2*")
        self.assertEqual(wirelists.map_tank({"tank": -1}), "X")
        self.assertEqual(wirelists.map_tank({"tank": 0}), "?")
        self.assertEqual(wirelists.map_tank({"tank": 4, "retired": True}), "R")

    def test_a_hand_mapped_meter_is_locked(self):
        """"A manually mapped meter is considered locked. Auto meter mapping
        will not change a locked meter"."""
        c, h = self.empty()
        send(h, "S7B100 3 1 18 1 1")
        self.assertTrue(c.meter_map[MeterId(3, 1, 18, 1)]["locked"])
        self.assertIn("1*", body(h, "I7B100"))

    def test_the_manuals_own_twenty_row_sample_all_fits(self):
        """FIDELITY G7. 576013-635 p.439's I7B100 sample maps meter 10 on
        seven fueling positions, four of them to tank 1 and three to tank 2.
        A store keyed on the meter number holds ONE of those rows; this held
        one, so every worked example in chapter 12 was unstorable."""
        c, h = self.empty()
        sample = [(0, 10, 1), (2, 12, 1), (5, 11, 3), (0, 11, 3), (0, 12, 2),
                  (1, 10, 1), (1, 11, 3), (1, 12, 2), (2, 10, 2), (2, 11, 3),
                  (3, 10, 2), (3, 11, 3), (3, 12, 1), (4, 10, 1), (4, 11, 3),
                  (4, 12, 2), (5, 10, 1), (5, 12, 2), (6, 10, 2), (6, 11, 3)]
        for fp, meter, tank in sample:
            self.assertFalse(refused(h, f"S7B100 3 3 {fp} {meter} {tank}"))
        self.assertEqual(len(c.meter_map), len(sample))
        # meter 10, on seven positions, going to two different tanks
        tens = {key.fp: tank for key, tank in c.meters.items()
                if key.meter == 10}
        self.assertEqual(tens, {0: 1, 1: 1, 2: 2, 3: 2, 4: 1, 5: 1, 6: 2})
        printed = [row for row in body(h, "I7B100").splitlines()
                   if row.strip().startswith("3")]
        self.assertEqual(len(printed), len(sample))

    def test_a_probeless_tank_prints_x_not_minus_one(self):
        c, h = self.empty()
        send(h, "S7B100 3 1 18 3 -1")
        printed = body(h, "I7B100")
        self.assertIn("X", printed)
        self.assertNotIn("-1", printed)


class TheBallotIsDrawnByFuelingPosition(unittest.TestCase):
    """FIDELITY S11, and it needed G7's key. 576013-818 p.12-14 draws
    `I@A002` as one two-line block per FUELING POSITION, six meter columns
    wide, and p.12-15's legend reads a cell for you: "the FP9 M0 voting
    ballet is M3>1:3/3/3 / M3 = mapped to tank 4 (3+1) / 3/3/3 = three votes
    for tank 4", under "Tank numbers are zero based". The digit after M is
    the TANK. This console drew one row per METER with the meter number
    there instead."""

    def a_mapped_site(self):
        c, h = a_site()
        c.meters.clear()
        c.meter_map.clear()
        for fp, meter, tank in ((0, 10, 1), (0, 11, 2), (1, 10, 3)):
            send(h, f"S7B100 3 3 {fp} {meter} {tank}")
        return c, h

    def grid(self, h):
        return [row for row in body(h, "I@A002").splitlines()
                if row and not row.startswith("---")]

    def labels(self, h):
        """The row labels of the grid: the numbered ones are positions."""
        return [row[:3] for row in self.grid(h)
                if row[3:4] == "|" and row[:3].strip().isdigit()]

    def test_a_row_is_a_fueling_position_not_a_meter(self):
        """Three meters, two positions, two rows -- where one row per meter
        would have drawn three."""
        _c, h = self.a_mapped_site()
        self.assertEqual(self.labels(h), ["  0", "  1"])

    def test_the_cell_carries_the_tank_zero_based(self):
        """`M0>3:0/0/0` is tank 1, not meter 0."""
        _c, h = self.a_mapped_site()
        rows = [row for row in self.grid(h) if row.startswith("  0|")]
        cells = rows[0][4:].split()
        self.assertEqual(cells[0], "M0>3:0/0/0")     # meter 10 -> tank 1
        self.assertEqual(cells[1], "M1>3:1/1/1")     # meter 11 -> tank 2
        self.assertEqual(cells[2], "-:-/-/-")

    def test_six_columns_to_a_position(self):
        """"The console is limited to 6 meters (M) per FP"."""
        _c, h = self.a_mapped_site()
        row = [r for r in self.grid(h) if r.startswith("  1|")][0]
        self.assertEqual(len(row[4:].split()), 6)
        # the label, six cells of ten and a space between them, which is
        # what the captured console's column header is spaced for
        self.assertEqual(len(row.rstrip()), 4 + 6 * 10 + 5)

    def test_an_empty_map_draws_no_grid_at_all(self):
        """Which is the captured console: a TLS-350 running 326.01 with no
        meters prints the header and the rule and stops."""
        c, h = a_site()
        c.meters.clear()
        printed = body(h, "I@A002")
        self.assertEqual(self.labels(h), [])
        self.assertIn("**TANK_MAP_BALLOT**", printed)
        self.assertEqual(printed.rstrip().splitlines()[-1],
                         c.bir.BALLOT_RULE)

    def test_an_unmapped_meter_reaches_the_summary_block(self):
        """The guide's first instruction under this report is "Look for
        unmapped or retired meters", and neither has a tank to put in a
        grid cell -- p.12-14 gives them a row of their own with the legend
        "U = unmapped, R = retired, X = probe"."""
        c, h = self.a_mapped_site()
        c.modules["edim"] = 1
        c.meter_flow[91] = 50.0            # on no tank at all
        for _ in range(4):
            c.clock_offset += 900.0
            c.tick()
        printed = body(h, "I@A002")
        self.assertIn("    Unmapped   Retired    Probeless", printed)
        self.assertIn(" 15|U >0:8/8/8", printed)

    def test_and_a_site_with_nothing_wrong_prints_no_summary(self):
        _c, h = self.a_mapped_site()
        self.assertNotIn("Unmapped", body(h, "I@A002"))


class TheMeterEventsTable(unittest.TestCase):
    """576013-818 Figure 6-25 branches twice on this table: whether it has
    anything in it, and whether the newest row is a Start or an End. The
    console had no table at all -- the events screen read the meter's
    LIFETIME total and printed it, unlabelled, on the line the figure gives
    to the map. FIDELITY D14, and D6's `FP: XX`."""

    def a_selling_site(self):
        c, _h = a_site()
        c.meters = {1: 1}
        c.modules["edim"] = 1
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.tick()                        # the first tick only starts the clock
        return c

    def run_meter(self, c, hours=1.0, rate=100.0):
        c.meter_flow = {1: rate}
        c.clock_offset += hours * 3600.0
        c.tick()

    def test_an_empty_table_is_the_screen_the_figure_draws(self):
        c = self.a_selling_site()
        self.assertEqual(c.bir.events, [])
        screens = {sc["l2"]: c.visible(sc, 1)
                   for sc in self.events_screens(c)}
        self.assertTrue(screens["METER EVENTS TABLE EMPTY"])
        self.assertFalse(screens["PRESS <ENTER>"])

    def events_screens(self, c):
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU if f["function"] == "BIR DIAGNOSTICS"][0]
        return [sc for sc in fn["screens"] if sc["l1"] == "BIR METER EVENTS"
                and not sc.get("depth")]

    def test_a_meter_that_starts_running_is_a_start_event(self):
        c = self.a_selling_site()
        self.run_meter(c)
        self.assertEqual([e["kind"] for e in c.bir.events], ["start"])
        self.assertEqual(c.diag_value("meter_events", 1),
                         "BIR METER EVENTS" + chr(10) + "FP: 01")
        self.assertEqual(c.diag_value("meter_end", 1), "START EVENT")
        screens = {sc["l2"]: c.visible(sc, 1)
                   for sc in self.events_screens(c)}
        self.assertFalse(screens["METER EVENTS TABLE EMPTY"])
        self.assertTrue(screens["PRESS <ENTER>"])

    def test_and_stopping_it_is_an_end_event_with_its_gallons(self):
        c = self.a_selling_site()
        self.run_meter(c)
        self.run_meter(c, rate=0.0)
        self.assertEqual([e["kind"] for e in c.bir.events], ["start", "end"])
        self.assertAlmostEqual(c.bir.events[-1]["gallons"], 100.0, places=0)
        self.assertEqual(c.diag_value("meter_events", 1),
                         "BIR METER EVENTS" + chr(10) + "FP: 01 M: 01 =T 1")
        self.assertEqual(c.diag_value("meter_end", 1), "END EVENT: 100 GALS")

    def test_the_time_of_the_last_meter_event(self):
        c = self.a_selling_site()
        self.run_meter(c)
        from tls350sim.clock import clock_words
        self.assertEqual(c.diag_value("meter_event_time", 1),
                         clock_words(c.bir.events[-1]["at"], seconds=True))

    def test_print_gives_the_last_four(self):
        """"Prints Last 4 Meter Events"."""
        from tls350sim import printer
        c = self.a_selling_site()
        for _ in range(3):
            self.run_meter(c)
            self.run_meter(c, rate=0.0)
        self.assertEqual(len(c.bir.events), 6)
        rows = [str(l) for l in printer.meter_events(c)]
        # newest first, and six events is three runs: the last four are
        # end, start, end, start
        self.assertEqual(sum(1 for r in rows if r.startswith("END EVENT")), 2)
        self.assertEqual(sum(1 for r in rows if r == "START EVENT"), 2)
        self.assertEqual(sum(1 for r in rows if r.startswith("FP:")), 4)

    def test_the_map_status_is_read_and_not_a_literal(self):
        """"COMPLETE or INCOMPLETE", which is whether every meter the
        console has heard of is mapped to a tank."""
        c = self.a_selling_site()
        self.assertEqual(c.diag_value("meter_map_status", 1),
                         "STATUS: COMPLETE")
        c.meter_flow[9] = 50.0            # a meter with nowhere to put it
        self.assertEqual(c.diag_value("meter_map_status", 1),
                         "STATUS: INCOMPLETE")

    def test_the_table_keeps_forty(self):
        c = self.a_selling_site()
        for _ in range(30):
            self.run_meter(c)
            self.run_meter(c, rate=0.0)
        self.assertEqual(len(c.bir.events), c.bir.EVENTS_KEPT)


if __name__ == "__main__":
    unittest.main()
