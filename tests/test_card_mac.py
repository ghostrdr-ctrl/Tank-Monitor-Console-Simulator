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
"""`--card-mac` is six bytes, or the command line refuses it.

It was `bytes.fromhex` on whatever was given, with only non-hex caught, so
`--card-mac 00-20-4A` built a card with a three-byte address. That reached
`xport.py`, where the card's derived address reads `mac[5]` and the discovery
reply's `r[24:30] = mac` shortens the frame instead of filling the field.
Non-hex was caught, printed and ignored, and the card started on the default
address. Both now stop at argparse, before any card is built.
"""
import argparse
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run                                                  # noqa: E402

ADDRESS = bytes((0x00, 0x20, 0x4A, 0x12, 0x34, 0x56))


class TheForms(unittest.TestCase):
    def test_colon_separated(self):
        self.assertEqual(run.card_mac("00:20:4A:12:34:56"), ADDRESS)

    def test_hyphen_separated(self):
        self.assertEqual(run.card_mac("00-20-4A-12-34-56"), ADDRESS)

    def test_twelve_bare_digits(self):
        self.assertEqual(run.card_mac("00204A123456"), ADDRESS)

    def test_lower_case_digits(self):
        self.assertEqual(run.card_mac("00:20:4a:12:34:56"), ADDRESS)

    def refused(self, *texts):
        for text in texts:
            with self.subTest(text=text):
                with self.assertRaises(argparse.ArgumentTypeError) as cm:
                    run.card_mac(text)
                self.assertIn("six bytes", str(cm.exception))

    def test_too_short(self):
        self.refused("00-20-4A-12-34", "00:20:4A", "00204A1234", "00204A12345",
                     "")

    def test_too_long(self):
        self.refused("00-20-4A-12-34-56-78", "00204A12345678",
                     "00204A1234567")

    def test_not_hex(self):
        self.refused("00-20-4A-12-34-5G", "00204A12345Z", "ZZ:ZZ:ZZ:ZZ:ZZ:ZZ")

    def test_malformed(self):
        self.refused(
            "00:20-4A:12-34:56",            # two kinds of separator
            "00-20-4A-12-34-56-",           # trailing separator
            "-00-20-4A-12-34-56",           # leading separator
            "00--20-4A-12-34-56",           # doubled separator
            "0020-4A12-3456",               # separators not between bytes
            "0-020-4A-12-34-56",
            "00 20 4A 12 34 56",            # spaces, which fromhex took
            "0x00204A123456",
            " 00204A123456",
            "00.20.4A.12.34.56")


class TheCommandLine(unittest.TestCase):
    """`run.main` with the card, its network and the console stood in for."""

    def main(self, mac):
        argv = ["run.py", "--headless", "--xport", "--quiet",
                "--card-mac", mac]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(run.xport, "XPortConfig") as config, \
                mock.patch.object(run.xportnet, "CardNetwork"), \
                mock.patch.object(run, "Console"), \
                mock.patch.object(run, "time") as clock, \
                contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()):
            # the headless loop sleeps until interrupted
            clock.sleep.side_effect = KeyboardInterrupt
            try:
                run.main()
                code = None
            except SystemExit as e:
                code = e.code
        return code, config, err.getvalue()

    def assertRefused(self, mac):
        code, config, err = self.main(mac)
        self.assertEqual(code, 2)
        self.assertIn("--card-mac", err)
        self.assertIn("six bytes", err)
        config.assert_not_called()

    def test_a_short_address_never_reaches_the_card(self):
        self.assertRefused("00-20-4A")

    def test_a_long_address_never_reaches_the_card(self):
        self.assertRefused("00-20-4A-12-34-56-78")

    def test_a_non_hex_address_stops_instead_of_being_ignored(self):
        self.assertRefused("00-20-4A-12-34-5G")

    def test_a_good_address_reaches_the_card_as_six_bytes(self):
        code, config, _ = self.main("00:20:4a:12:34:56")
        self.assertIsNone(code)
        self.assertEqual(config.return_value.mac, ADDRESS)


if __name__ == "__main__":
    unittest.main()
