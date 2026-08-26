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

from tls350sim import presets                              # noqa: E402
from tls350sim.console import Console, FIELDS              # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("smart", "vmc", "mt", "modem", "universal", "probe"):
        c.modules[card] = 4
    c.software.update({"bir": True, "fuelman": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def body(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3))


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
        shown = "|".join(body(h, "I7B200").splitlines()[2:])
        self.assertEqual(shown, "METER CALIBRATION|OFFSET: 0.000%")

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


if __name__ == "__main__":
    unittest.main()
