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
"""High Product, Overfill, Delivery and the two leak test minimums.

576013-635 Rev AA holds all five in GALLONS on every version, and the bench
console (version 26) heads I622 GALLONS and takes 101. 576013-623 Rev AN's
panel enters them as a percent, which is version 33's view of them. A
version 23 site backup holding 2483 in 629 loaded with DELIVERY NEEDED on
every tank while this console read the field as 2483% of the label volume.
"""
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import fieldio, printer, screens                 # noqa: E402
from tls350sim.console import Console                           # noqa: E402
from tls350sim.wire import Handler                              # noqa: E402


def packed(tank, value):
    return f"{tank:02d}" + struct.pack(">f", value).hex().upper()


def a_tank(version, volume=8000.0):
    """A 10,000 gallon tank programmed the way a real site's is: in gallons."""
    c = Console()
    c.version = version
    c.values["S60101"] = "011"
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S62801"] = packed(1, 10000.0)
    c.values["S62301"] = packed(1, 9000.0)
    c.values["S62201"] = packed(1, 9500.0)
    c.values["S62901"] = packed(1, 2500.0)
    c.values["S63601"] = packed(1, 2000.0)
    c.values["S62A01"] = packed(1, 5000.0)
    c.tank_level[1] = {"volume": volume, "water": 0.0}
    return c


def step(code):
    return {"code": code}


class TheFieldHoldsGallons(unittest.TestCase):
    def test_a_full_tank_does_not_need_a_delivery(self):
        """The site's case: 2483 in 629 is gallons, not 2483%."""
        for version in (23, 33):
            c = a_tank(version, volume=8000.0)
            self.assertNotIn("021101", c.conditions(), version)

    def test_a_tank_under_its_delivery_limit_needs_one(self):
        for version in (23, 33):
            c = a_tank(version, volume=2000.0)
            self.assertIn("021101", c.conditions(), version)

    def test_the_wire_answers_gallons(self):
        """`TANK   PRODUCT LABEL   GALLONS`, as the bench heads it."""
        for version in (23, 33):
            c = a_tank(version)
            reply = Handler(c, verbose=False).handle(b"\x01I62901\r")
            self.assertIn(b"GALLONS", reply)
            self.assertIn(b"2500", reply)


class ThePanel(unittest.TestCase):
    def test_version_33_draws_a_percent(self):
        c = a_tank(33)
        f = screens.field_of(c, step("S62901"), 1)
        self.assertEqual(screens.masked(f, screens.stored(c, step("S62901"),
                                                          1, f)), "025%")

    def test_before_33_it_draws_the_gallons(self):
        c = a_tank(23)
        f = screens.field_of(c, step("S62901"), 1)
        self.assertEqual(screens.masked(f, screens.stored(c, step("S62901"),
                                                          1, f)), "002500")

    def test_a_percent_typed_at_33_stores_its_gallons(self):
        c = a_tank(33)
        f = screens.field_of(c, step("S62201"), 1)
        data = fieldio.encode(f, "S62201", "96", c.values["S62201"],
                              console=c)
        c.values["S62201"] = data
        self.assertAlmostEqual(c.limit("622", 1), 9600.0, places=1)

    def test_gallons_typed_before_33_are_stored_as_typed(self):
        c = a_tank(23)
        f = screens.field_of(c, step("S62201"), 1)
        data = fieldio.encode(f, "S62201", "9600", c.values["S62201"],
                              console=c)
        c.values["S62201"] = data
        self.assertAlmostEqual(c.limit("622", 1), 9600.0, places=1)


class ThePaper(unittest.TestCase):
    def lines(self, c):
        return [str(l) for l in printer.setup(c)]

    def test_version_33_prints_the_percent_and_the_gallons(self):
        out = self.lines(a_tank(33))
        at = out.index("DELIVERY LIMIT")
        self.assertEqual(out[at + 1].split(":")[1].strip(), "25.0")
        self.assertEqual(out[at + 2].split(":")[1].strip(), "2500")

    def test_before_33_there_is_no_percent_to_print(self):
        out = self.lines(a_tank(23))
        at = out.index("DELIVERY LIMIT")
        self.assertIn("(GALLONS)", out[at + 1])
        self.assertEqual(out[at + 1].split(":")[1].strip(), "2500")
        self.assertNotIn("% MAX", out[at + 2])


class AStateFileFromBefore(unittest.TestCase):
    def test_a_saved_percent_comes_back_as_its_gallons(self):
        """A state file written while the fields held percents has no
        `limits_in_gallons` mark, and its 95 is read as 95%."""
        c = a_tank(33)
        c.values["S62201"] = packed(1, 95.0)
        c.values["S62301"] = packed(1, 9000.0)     # over 100, left alone
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"values": c.values, "version": 33}, fh)
            back = Console(path)
            back.load()
        self.assertAlmostEqual(back.limit("622", 1), 9500.0, places=1)
        self.assertAlmostEqual(back.limit("623", 1), 9000.0, places=1)

    def test_a_state_file_from_now_is_left_alone(self):
        c = a_tank(33)
        c.values["S62201"] = packed(1, 95.0)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            c.state_path = path
            c.save()
            back = Console(path)
            back.load()
        self.assertAlmostEqual(back.limit("622", 1), 95.0, places=1)


if __name__ == "__main__":
    unittest.main()
