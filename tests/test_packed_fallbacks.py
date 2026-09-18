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
"""Three readers that fall back when stored text is not a packed float.

`_float_parameter`, `meter_offset` and `limit` try the eight-hex-digit form
first and fall back -- to None, to 0.0, to a decimal reading -- when the text
is not one. They caught Exception to do it, which also read a fault in the
parser as "not programmed". `packed.unhexfloat` raises ValueError for
everything malformed, struct's own error included, so ValueError is what
they catch now. What each returns for stored text is unchanged, and the
first tests in each class pin that; the last shows a fault getting out.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import packed                               # noqa: E402
from tls350sim.console import Console                      # noqa: E402

h = packed.hexfloat


def parser_fault():
    return mock.patch.object(packed, "unhexfloat",
                             side_effect=RuntimeError("parser fault"))


class FloatParameter(unittest.TestCase):
    """60E's four floats, eight hex digits each after the tank prefix."""

    def setUp(self):
        self.c = Console()

    def test_a_packed_float_is_read(self):
        self.c.values["S60E01"] = "01" + h(1) + h(2) + h(3) + h(4)
        self.assertEqual(self.c._float_parameter(1, 0), 1.0)
        self.assertEqual(self.c._float_parameter(1, 3), 4.0)

    def test_text_that_is_not_hex_is_no_float(self):
        self.c.values["S60E01"] = "01" + h(1) + h(2) + h(3) + "NOTAHEX!"
        self.assertIsNone(self.c._float_parameter(1, 3))

    def test_a_short_field_is_no_float(self):
        # four hex digits: struct's error, which unhexfloat hands on as
        # ValueError
        self.c.values["S60E01"] = "01" + h(1) + h(2) + h(3) + "3F80"
        self.assertIsNone(self.c._float_parameter(1, 3))

    def test_a_parser_fault_is_not_read_as_unprogrammed(self):
        self.c.values["S60E01"] = "01" + h(1) + h(2) + h(3) + h(4)
        with parser_fault(), self.assertRaises(RuntimeError):
            self.c._float_parameter(1, 3)


class MeterOffset(unittest.TestCase):
    """S7B200, the site-wide offset: a decimal, or a packed float."""

    def setUp(self):
        self.c = Console()

    def offset(self, raw):
        self.c.values["S7B200"] = raw
        return self.c.meter_offset(1)

    def test_a_decimal_is_read_as_one(self):
        self.assertEqual(self.offset("1.5"), 1.5)

    def test_a_packed_float_is_read(self):
        self.assertEqual(self.offset(h(-0.25)), -0.25)

    def test_text_that_is_neither_is_no_offset(self):
        self.assertEqual(self.offset("ZZZZ"), 0.0)
        self.assertEqual(self.offset("3F80"), 0.0)

    def test_a_parser_fault_is_not_read_as_no_offset(self):
        with parser_fault(), self.assertRaises(RuntimeError):
            self.offset(h(-0.25))


class Limit(unittest.TestCase):
    """A per-device setting, packed or decimal, device prefix off first."""

    def setUp(self):
        self.c = Console()

    def limit(self, tok, raw):
        self.c.values[f"S{tok}01"] = raw
        return self.c.limit(tok, 1)

    def test_a_packed_float_is_read(self):
        self.assertEqual(self.limit("774", "01" + h(12.5)), 12.5)

    def test_a_decimal_is_read_as_one(self):
        # FIDELITY R17's two: a 30 hour timeout, and 20.00 PSI
        self.assertEqual(self.limit("774", "0130"), 30.0)
        self.assertEqual(self.limit("776", "01020.00"), 20.0)

    def test_text_that_is_neither_is_no_limit(self):
        self.assertIsNone(self.limit("774", "01ZZZZ"))

    def test_a_parser_fault_is_not_read_as_a_decimal(self):
        with parser_fault(), self.assertRaises(RuntimeError):
            self.limit("774", "01" + h(12.5))


if __name__ == "__main__":
    unittest.main()
