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
"""The RS-232 interface card, as 576013-635 describes it.

Not the protocol -- that is tested to the last function code elsewhere --
but the card: the security DIP switch that gates whether the console answers
at all, the end-of-message characters it appends to a computer-format reply,
the escape that abandons a part-typed command, and the plain fact that with
no comm card in the cage there is no serial port.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console
from tls350sim.wire import Handler, SOH, ETX


def a_console():
    c = Console()
    c.modules["rs232"] = 1
    return c


def send(h, security, body):
    return h.handle(SOH + security + body)


class Security(unittest.TestCase):
    """576013-635 p.267: "The system will not respond to a command without
    the proper security code, if the DIP switch is set to enable RS-232
    security." """

    def setUp(self):
        self.c = a_console()
        self.h = Handler(self.c, verbose=False)
        self.c.values["S50400"] = "123456"      # a code is programmed

    def test_a_code_alone_does_nothing_without_the_dip(self):
        self.assertFalse(self.c.rs232_enforces_security())
        self.assertNotEqual(send(self.h, b"", b"I10100"), b"")

    def test_the_dip_alone_does_nothing_without_a_code(self):
        self.c.values["S50400"] = ""
        self.c.rs232_security = True
        self.assertFalse(self.c.rs232_enforces_security())
        self.assertNotEqual(send(self.h, b"", b"I10100"), b"")

    def test_enabled_it_refuses_a_command_with_no_code(self):
        self.c.rs232_security = True
        self.assertEqual(send(self.h, b"", b"i10100"), b"")

    def test_enabled_it_refuses_a_wrong_code(self):
        self.c.rs232_security = True
        self.assertEqual(send(self.h, b"999999", b"i10100"), b"")

    def test_enabled_it_answers_the_right_code(self):
        self.c.rs232_security = True
        out = send(self.h, b"123456", b"i10100")
        self.assertTrue(out.startswith(SOH))
        self.assertNotEqual(out, b"")

    def test_refusal_is_silent_not_an_error_frame(self):
        # a caller without the code cannot even tell the console is there
        self.c.rs232_security = True
        self.assertEqual(send(self.h, b"", b"i10100"), b"")
        self.assertNotIn(b"9999", send(self.h, b"", b"i10100"))


class EndOfMessage(unittest.TestCase):
    """531 enables it, 537 sets the two characters, and it lands only on
    computer-format replies."""

    def setUp(self):
        self.c = a_console()
        self.h = Handler(self.c, verbose=False)
        self.c.values["S53100"] = "1"           # EOM enabled

    def test_two_characters_follow_the_etx(self):
        self.c.values["S53799"] = "0D0A"        # CR LF
        out = send(self.h, b"", b"i10100")
        self.assertTrue(out.endswith(ETX + b"\x0d\x0a"))

    def test_display_format_is_untouched(self):
        self.c.values["S53799"] = "0D0A"
        out = send(self.h, b"", b"I10100")      # display format
        self.assertTrue(out.endswith(ETX))
        self.assertFalse(out.endswith(ETX + b"\x0d"))

    def test_disabled_means_bare_etx(self):
        self.c.values["S53100"] = "0"
        self.c.values["S53799"] = "0D0A"
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX))

    def test_a_null_first_character_reverts_to_default(self):
        self.c.values["S53799"] = "000A"        # NUL then LF
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX))

    def test_a_null_second_character_sends_only_the_first(self):
        self.c.values["S53799"] = "2A00"        # '*' then NUL
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX + b"\x2a"))


class CardPresence(unittest.TestCase):
    """No comm card in the cage, no serial port to answer on."""

    def test_no_comm_card_is_silent(self):
        c = Console()
        c.modules = {"probe": 1}                 # no rs232 / modem / mt
        h = Handler(c, verbose=False)
        self.assertEqual(send(h, b"", b"I10100"), b"")

    def test_a_modem_card_is_a_port_too(self):
        c = Console()
        c.modules = {"probe": 1, "modem": 1}
        h = Handler(c, verbose=False)
        self.assertNotEqual(send(h, b"", b"I10100"), b"")


if __name__ == "__main__":
    unittest.main()
