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
    """531 enables it; 537 holds the display format's characters and 538 the
    computer format's, per port, and they REPLACE the ETX. Measured on the
    bench TLS-350, 2026-09-25 (`test_bench_framing` replays the logs)."""

    def setUp(self):
        self.c = a_console()
        self.h = Handler(self.c, verbose=False)
        self.c.values["S53100"] = "1"           # EOM enabled
        self.port = self.c.rs232_port()

    def set(self, tok, data):
        send(self.h, b"", b"S" + tok + b"99" + data)

    def test_two_characters_take_the_place_of_the_etx(self):
        self.set(b"538", b"*#")
        out = send(self.h, b"", b"i10100")
        self.assertEqual(out[-8:-6], b"&&")       # && CCCC, then the two
        self.assertTrue(out.endswith(b"*#"))
        self.assertNotIn(ETX, out)

    def test_537_is_the_display_format_and_538_the_computer(self):
        self.set(b"537", b"+\x00")
        self.assertTrue(send(self.h, b"", b"I10100").endswith(b"+"))
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX))

    def test_disabled_means_the_etx(self):
        self.set(b"538", b"*#")
        self.c.values["S53100"] = "0"
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX))

    def test_a_null_first_character_clears_both(self):
        self.set(b"538", b"\x00#")
        self.assertEqual(self.c.values[f"S538{self.port:02d}"], "")
        self.assertTrue(send(self.h, b"", b"i10100").endswith(ETX))

    def test_a_null_second_character_sends_only_the_first(self):
        self.set(b"538", b"*\x00")
        out = send(self.h, b"", b"i10100")
        self.assertEqual(out[-7:-5], b"&&")       # && CCCC, then the one
        self.assertTrue(out.endswith(b"*"))
        self.assertNotIn(ETX, out)

    def test_the_table_prints_what_is_stored(self):
        self.set(b"537", b"*#")
        self.assertIn(b"\r\n 1       *    #\r\n",
                      send(self.h, b"", b"I53701"))
        self.assertIn(b"*#&&", send(self.h, b"", b"i53701"))
        # 01 to 06 and 99 only: 07 and 00 are bare
        self.assertNotIn(b"ETX CHARACTERS", send(self.h, b"", b"I53707"))
        self.assertNotIn(b"ETX CHARACTERS", send(self.h, b"", b"I53700"))


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


class ADefectIsARefusalNotAHangUp(unittest.TestCase):
    """`_session` caught `OSError` only, so anything a malformed command made
    raise inside the handler took the TCP connection down with it, and the
    tool's next send got `ConnectionAbortedError`."""

    def test_malformed_commands_are_refused(self):
        h = Handler(Console(), verbose=False)
        for cmd in ("I21300AB", "I21B00xx"):
            self.assertIn(b"9999FF1B", h.handle(SOH + cmd.encode()), cmd)
        # A Set whose VALUE is wrong is answered the way the bench TLS-350
        # answers one -- `?` in place of the field, or, for data short of the
        # field's width as this four-character float is, the bare echo -- not
        # with 9999, which is for a function code the console does not know.
        # Still an answer, still no hang-up, which is what this test is for.
        out = h.handle(SOH + b"s68301A100")
        self.assertTrue(out.startswith(SOH + b"s68301")
                        and b"9999" not in out, out)
        self.assertTrue(b"?" in h.handle(SOH + b"s68301ZZZZZZZZ"))

    def test_the_chart_of_no_tank_is_an_empty_one(self):
        """A probe card and no tank programmed: `tanks[0]` raised."""
        out = Handler(Console(), verbose=False).handle(SOH + b"i63B00")
        self.assertTrue(out.startswith(SOH + b"i63B00"), out)

    def test_the_session_survives_a_handler_that_raises(self):
        import socket
        import threading
        import time
        from unittest import mock
        from tls350sim import wire
        got = []
        threading.Thread(target=wire.serve,
                         args=(Console(), "127.0.0.1", 0, False, None),
                         kwargs={"on_socket": got.append},
                         daemon=True).start()
        deadline = time.time() + 5.0
        while not got and time.time() < deadline:
            time.sleep(0.01)
        self.addCleanup(got[0].close)
        conn = socket.create_connection(
            ("127.0.0.1", got[0].getsockname()[1]), timeout=5.0)
        self.addCleanup(conn.close)

        def ask(cmd):
            conn.sendall(SOH + cmd + chr(13).encode())
            buf = b""
            while ETX not in buf.replace(wire.PROBE, b""):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            return buf.replace(wire.PROBE, b"")

        with mock.patch.object(Handler, "inquire",
                               side_effect=ZeroDivisionError):
            first = ask(b"I20100")
        second = ask(b"I20100")
        self.assertIn(b"9999FF1B", first)
        self.assertIn(b"I20100", second)



class WhatTheBenchConsoleDidWithTheEdges(unittest.TestCase):
    """The bench TLS-350, security disabled on every port (2026-09-19,
    `transcripts/edge2`)."""

    def test_a_code_in_front_with_security_off_is_not_understood(self):
        """`000000I20100` -- the console's own code -- answered 9999FF: the
        six digits are read as the function code."""
        h = Handler(a_console(), verbose=False)
        self.assertEqual(send(h, b"000000", b"I20100"),
                         SOH + b"9999FF1B" + ETX)
        self.assertNotEqual(send(h, b"", b"I20100"),
                            SOH + b"9999FF1B" + ETX)

    def test_a_digit_then_a_letter_is_a_device_the_console_has_not_got(self):
        """`I2010A` answered the bare frame and `I201A0` 9999FF."""
        h = Handler(a_console(), verbose=False)
        bare = send(h, b"", b"I2010A")
        self.assertTrue(bare.startswith(SOH + b"\r\nI2010A\r\n"), bare)
        self.assertNotIn(b"9999", bare)
        self.assertEqual(send(h, b"", b"I201A0"), SOH + b"9999FF1B" + ETX)

    def test_the_clock_is_a_date_or_it_is_a_refusal(self):
        """`S501000602300100` (FEB 30) and `S501000601192400` (hour 24) were
        answered with ten `?` and the clock left alone, where FEB 28 and
        23:59 were taken (`transcripts/clockset4`). This read them through
        `mktime` and set the clock to MAR 2 and the next midnight."""
        h = Handler(a_console(), verbose=False)
        self.assertIn(b"0601192359", send(h, b"", b"S501000601192359"))
        for bad in (b"0602300100", b"0601192400", b"0601196000",
                    b"0601320045", b"ABCDEFGHIJ"):
            out = send(h, b"", b"S50100" + bad)
            self.assertIn(b"?" * 10, out, bad)
            self.assertNotIn(b"9999", out, bad)
            self.assertIn(b"JAN 19, 2006 11:59 PM", out, bad)

    def test_the_clock_set_answers_with_the_clock_it_leaves(self):
        """The echo, the stamp as it WAS, and the ten digits taken -- in both
        formats (`transcripts/clockset`). It had been a bare echo stamped
        with the new time."""
        h = Handler(a_console(), verbose=False)
        send(h, b"", b"S501000602280101")
        out = send(h, b"", b"S501000601192359")
        self.assertEqual(out.split(b"\r\n")[2:4],
                         [b"FEB 28, 2006  1:01 AM", b"0601192359"])
        packed = send(h, b"", b"s501000601190050")
        self.assertEqual(packed.split(b"&&")[0][1:],
                         b"s50100" + b"0601192359" + b"0601190050")

    def test_the_clock_ignores_the_device_number(self):
        """`S501010601190035` set the clock on the bench and answered
        `S50101`, where this stored it where nothing reads it."""
        h = Handler(a_console(), verbose=False)
        out = send(h, b"", b"S501010601190035")
        self.assertIn(b"S50101", out)
        self.assertIn(b"0601190035", out)
        self.assertIn(b"JAN 19, 2006 12:35 AM", send(h, b"", b"I50100"))

    def test_the_print_header_is_bare_for_device_00_in_both_formats(self):
        """With two lines programmed the bench answered `I50300` and
        `i50300` with the echo and the stamp alone, `i50301` with the twenty
        characters (`transcripts/header503b`). The packed form had been
        running every line's text together."""
        h = Handler(a_console(), verbose=False)
        send(h, b"", b"S50301ACME FUEL\r")
        send(h, b"", b"S50302SECOND LINE\r")
        self.assertNotIn(b"ACME", send(h, b"", b"I50300"))
        self.assertNotIn(b"ACME", send(h, b"", b"i50300"))
        self.assertIn(b"# 1:ACME FUEL           ", send(h, b"", b"I50301"))
        self.assertIn(b"ACME FUEL           &&", send(h, b"", b"i50301"))
        # and all four lines still reach a report's header block
        # each at its twenty, as a set header line prints (S41)
        self.assertIn(b"\r\nACME FUEL           \r\nSECOND LINE         \r\n",
                      send(h, b"", b"I10100"))

    def test_the_shift_times_answer_one_at_a_time_and_are_right_aligned(self):
        """Three shifts programmed on the bench: `I50200` bare, `I50201` one
        line, the value right-aligned in eight -- which is what DISABLED is
        (`transcripts/shifts2`, `shifts3`). `EE00` disables one."""
        h = Handler(a_console(), verbose=False)
        send(h, b"", b"S502010600")
        send(h, b"", b"S502032200")
        self.assertIn(b"SHIFT TIME 1 :  6:00 AM", send(h, b"", b"I50201"))
        self.assertIn(b"SHIFT TIME 3 : 10:00 PM", send(h, b"", b"I50203"))
        self.assertIn(b"SHIFT TIME 4 : DISABLED", send(h, b"", b"I50204"))
        for code in (b"I50200", b"i50200"):
            self.assertNotIn(b"0600", send(h, b"", code), code)
        self.assertIn(b"SHIFT TIME 1 : DISABLED",
                      send(h, b"", b"S50201EE00"))
        # and a device family still lists every position
        self.assertIn(b"   8 ", send(h, b"", b"I52B00"))

    def test_precision_print_is_a_setting(self):
        """55D, in no manual: `S55D001` ENABLED, `S55D000` DISABLED, packed
        1 and 0, and `S55D002` refused (`transcripts/p55d.log`). Asked
        of a console with a PLLD sensor board, as the bench had then: with
        it out, 55D answers bare (2026-10-08)."""
        c = a_console()
        c.modules["plld"] = 1
        h = Handler(c, verbose=False)
        self.assertIn(b"RESULTS: DISABLED", send(h, b"", b"I55D00"))
        self.assertIn(b"RESULTS: ENABLED", send(h, b"", b"S55D001\r"))
        self.assertIn(b"RESULTS: ENABLED", send(h, b"", b"I55D00"))
        self.assertTrue(send(h, b"", b"i55D00").split(b"&&")[0].endswith(b"1"))
        self.assertIn(b"?", send(h, b"", b"S55D002\r"))
        self.assertIn(b"RESULTS: DISABLED", send(h, b"", b"S55D000\r"))

    def test_the_tank_maximum_volume_limit_is_a_sixteen_bit_setting(self):
        """7C3, in no manual (`transcripts/p7c3*`): six digits read, a
        fraction dropped, held modulo 65536, and 0 refused."""
        c = a_console()
        c.modules["probe"] = 1
        h = Handler(c, verbose=False)

        def held(sent):
            send(h, b"", b"S7C301" + sent + b"\r")
            row = [r for r in send(h, b"", b"I7C301").split(b"\r\n")
                   if r.startswith(b" 1 ")][0]
            return row.split()[-1]
        self.assertEqual(held(b"200"), b"200")
        self.assertEqual(held(b"132.5"), b"132")
        self.assertEqual(held(b"9999999"), b"16959")
        self.assertEqual(held(b"65536"), b"0")
        self.assertEqual(held(b"65537"), b"1")
        self.assertIn(b"?", send(h, b"", b"S7C3010\r"))
        self.assertIn(b"?", send(h, b"", b"S7C301ABC\r"))


if __name__ == "__main__":
    unittest.main()
