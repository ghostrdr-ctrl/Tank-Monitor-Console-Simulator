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
"""550, 551, and what a console holds for a setting nobody has changed.

FIDELITY S8 and S9. The two entries are one subject from two directions: a
console is never blank where it holds a default, and it never answers half a
record where the manual defines a whole one.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_controls import a_site, send                 # noqa: E402
from tls350sim.console import FIELDS                         # noqa: E402


def body(handler, command):
    return (send(handler, command).replace(chr(1), "").replace(chr(3), "")
        .strip(chr(13) + chr(10)))


class TheInventoryAlarmUnits(unittest.TestCase):
    """576013-635 Rev AA p.221 and p.222 draw one seven-line block for both
    codes and give them one computer format."""

    def test_the_display_block_is_the_manuals_seven_lines(self):
        _c, h = a_site()
        rows = body(h, "I55000").splitlines()[2:]
        rows = [r for r in rows if r.strip()]
        self.assertEqual(rows, ["INVENTORY ALARMS UNITS",
                                "CONFIG: STANDARD",
                                "MAX OR LABEL: VOLUME",
                                "HIGH PRODUCT: % MAX",
                                "OVERFILL    : % MAX",
                                "DELIV NEEDED: % MAX",
                                "LOW PRODUCT : VOLUME"])

    def test_551_answers_the_same_block(self):
        _c, h = a_site()
        self.assertEqual(body(h, "I55000").splitlines()[2:],
                         body(h, "I55100").splitlines()[2:])

    def test_the_computer_format_is_six_characters_not_one(self):
        """`CMHODL`. This sent `C` alone, so a tool reading fields 2 through
        6 was reading the checksum."""
        _c, h = a_site()
        answer = body(h, "i55000")
        data = answer.split("&&")[0][len("i55000") + 10:]
        self.assertEqual(data, "131113")        # standard: volume, three % max

    def test_a_wire_set_moves_the_panels_own_setting(self):
        """They were two stores that did not talk: setting Custom over the
        wire left the panel on STANDARD, and the report reads the panel."""
        c, h = a_site()
        self.assertFalse(body(h, "S550005").endswith("9999FF1B"))
        self.assertEqual(c.setting("inventory_units", 0, "STANDARD"), "CUSTOM")
        self.assertIn("CONFIG: CUSTOM", body(h, "I55000"))

    def test_551_sets_one_row(self):
        c, h = a_site()
        send(h, "S550005")                      # custom, so the rows are live
        self.assertFalse(body(h, "S5510023").endswith("9999FF1B"))
        self.assertEqual(c.setting("custom_high", 0, "%FULL"), "VOLUME")
        self.assertIn("HIGH PRODUCT: VOLUME", body(h, "I55100"))

    def test_max_product_cannot_be_per_cent_max(self):
        """Note 3 on p.222, and it is the only rule the pair carries."""
        _c, h = a_site()
        self.assertTrue(body(h, "S5510011").endswith("9999FF1B"))
        self.assertFalse(body(h, "S5510013").endswith("9999FF1B"))

    def test_a_panel_set_moves_what_the_wire_answers(self):
        """FIDELITY F9's own example, from the other end.

        The wire-to-panel direction was fixed first and tested above; this
        is the direction the entry actually quotes --
        `c.set_setting("custom_high", "VOLUME")` leaving `S55100` holding
        its old digits. Both directions now go through one store, so there
        is no second copy to disagree with.
        """
        c, h = a_site()
        c.set_setting("inventory_units", "CUSTOM", 0)
        c.set_setting("custom_high", "VOLUME", 0)
        self.assertIn("CONFIG: CUSTOM", body(h, "I55000"))
        self.assertIn("HIGH PRODUCT: VOLUME", body(h, "I55100"))
        data = body(h, "i55100").split("&&")[0][len("i55100") + 10:]
        self.assertEqual(data[0], "5")          # CUSTOM
        self.assertEqual(data[2], "3")          # high product: VOLUME

    def test_the_pair_keeps_no_second_copy_of_itself(self):
        """A write-only duplicate cannot be seen to be wrong, so it is the
        kind that drifts. `water_filter` on 642 keeps none, and F9 holds it
        up as the pattern; 550 and 551 used to write `values["S55000"]` and
        `values["S55100"]` that nothing ever read back."""
        c, h = a_site()
        send(h, "S550005")
        send(h, "S5510023")
        self.assertIsNone(c.values.get("S55000"))
        self.assertIsNone(c.values.get("S55100"))
        self.assertEqual(c.setting("custom_high", 0, "%FULL"), "VOLUME")


class TheReidVaporPressureChart(unittest.TestCase):
    """54C, whose whole twelve month block was hidden behind a typo: the
    manual echoes the code as `I54COO`, with two letter O's where the zeros
    belong, and the title tool read past it. FIDELITY Y8."""

    def test_the_twelve_months_print_the_manuals_block(self):
        """576013-635 Rev AA p.218, with the month at column 0 and the
        pressure at 16, zero padded to two figures before the point."""
        _c, h = a_site()
        send(h, "S54C00" + "14.014.012.012.011.010.0"
                           "08.004.005.006.009.012.0")
        rows = [r for r in body(h, "I54C00").splitlines()[2:] if r.strip()]
        self.assertEqual(rows[0], "CSLD EVAP CONSTANTS")
        self.assertEqual(rows[1], "REID VAPOR PRESSURE:")
        self.assertEqual(rows[2], "JAN             14.0")
        self.assertEqual(rows[8], "JUL             08.0")
        self.assertEqual(rows[13], "DEC             12.0")

    def test_a_pressure_outside_the_range_is_refused(self):
        """"The command will be rejected if any value is outside the range
        0.0 to 15.0"."""
        _c, h = a_site()
        self.assertTrue(body(h, "S54C00" + "16.0" * 12).endswith("9999FF1B"))
        self.assertFalse(body(h, "S54C00" + "15.0" * 12).endswith("9999FF1B"))

    def test_an_all_zero_table_is_no_table(self):
        """Which is how a site says it has not entered one, and the only way
        to clear the chart."""
        c, h = a_site()
        from tls350sim import wirelists
        send(h, "S54C00" + "08.0" * 12)
        self.assertEqual(len(wirelists.rvp_table(c)), 12)
        send(h, "S54C00" + "00.0" * 12)
        self.assertEqual(wirelists.rvp_table(c), [])

    def test_the_computer_format_is_twelve_floats(self):
        _c, h = a_site()
        send(h, "S54C00" + "08.0" * 12)
        data = body(h, "i54C00").split("&&")[0][len("i54C00") + 10:]
        self.assertEqual(len(data), 12 * 8)
        self.assertEqual(data[:8], "41000000")          # 8.0


class ADefaultIsNotSilence(unittest.TestCase):
    """"Nothing is stored for a setting nobody has changed, but the console
    is not blank there" -- `screens.shown`'s own docstring, which the panel
    followed and the wire did not. FIDELITY S9."""

    def test_every_documented_default_answers(self):
        c, h = a_site()
        for card in ("liquid", "vapor", "gw", "2wire", "3wire", "vlld",
                     "pump", "pumpmon", "io", "relay", "smart", "plld",
                     "wplld", "probe", "mt", "vmc"):
            c.modules.setdefault(card, 4)
        silent = []
        for key, field in sorted(FIELDS.items()):
            if not (key.startswith("S") and len(key) >= 6
                    and isinstance(field, dict)):
                continue
            if field.get("default") is None:
                continue
            reply = body(h, f"I{key[1:4]}{key[4:6]}")
            if reply.endswith("9999FF1B"):
                # a card this console has not got, which is a different
                # answer and the right one -- see S7a
                continue
            if not [l for l in reply.splitlines()[2:] if l.strip()]:
                silent.append(f"{key} (default {field['default']!r})")
        self.assertEqual(silent, [], "; ".join(silent))

    def test_the_meter_offset_is_the_panels_own(self):
        """F7 argued this for exactly this field: `+0.000` is what the panel
        SHOWS for an unprogrammed one, which is what a default is for."""
        _c, h = a_site()
        self.assertIn("OFFSET:  0.000%", body(h, "I7B200"))

    def test_a_part_field_carries_its_default_too(self):
        """881 holds a port's four UART settings as parts of one record, and
        a port nobody has been near still runs at 1200, none, one and eight."""
        _c, h = a_site()
        shown = body(h, "I88101")
        for want in ("BAUD RATE: 1200", "PARITY: NONE", "STOP BITS: 1 STOP",
                     "DATA LENGTH: 7 DATA"):
            self.assertIn(want, shown)


if __name__ == "__main__":
    unittest.main()
