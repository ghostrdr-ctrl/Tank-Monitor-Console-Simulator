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
"""What a stream nobody meant to send does to the console's ports.

CLOSED Z4. Each port holds what has arrived until it makes a whole command,
line or form, and none of them had a limit. The limits are asserted here, and
so is what the ports do with a damaged command: answer it or refuse it, and
never raise, hang or lose the session.

The mutator is seeded, so a failure is the same failure on every run and on
every machine, and the message carries the bytes that caused it.

It also found FIDELITY S27 -- a Set carrying a byte past 7F left its
computer-format inquiry refusing for ever after. The refusing half is fixed
and asserted below; what the console should STORE is still open, and nothing
here asserts that either way.
"""
import os
import random
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, wire, xport, xportmenu, xportweb   # noqa: E402
from tls350sim.console import Console                               # noqa: E402

SEED = 20260915
SOH, ETX = wire.SOH, wire.ETX

# The bytes that mean something to one of the parsers: SOH and ETX, the
# terminators, ESC and the rub-outs, NUL, and telnet's IAC, SB, SE and WILL.
MEANINGFUL = bytes([0x01, 0x03, 0x0A, 0x0D, 0x1B, 0x08, 0x7F, 0x00,
                    0xFF, 0xFA, 0xF0, 0xFB])


def corpus():
    """Every documented function, as an Inquire and a Set, in both formats."""
    out = []
    for token, doc in sorted(wire.DOCUMENTED.items()):
        if not doc:
            continue
        code = token.encode("ascii")
        if doc.get("inquire"):
            out += [b"I" + code + b"00", b"i" + code + b"01"]
        if doc.get("set"):
            out += [b"S" + code + b"01" + b"0" * 8,
                    b"s" + code + b"01" + b"1" * 8]
    return out


def mutate(rng, command, commands):
    """One to four damages: a byte changed, bytes put in or taken out, the
    tail cut off, a piece repeated, another command spliced in, a number no
    field holds, or a run of the bytes the parsers act on."""
    b = bytearray(command)
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(8)
        i = rng.randrange(len(b) + 1)
        if op == 0 and b:
            b[min(i, len(b) - 1)] = rng.randrange(256)
        elif op == 1:
            b[i:i] = bytes(rng.randrange(256)
                           for _ in range(rng.randint(1, 8)))
        elif op == 2 and b:
            del b[i:i + rng.randint(1, 6)]
        elif op == 3:
            del b[i:]
        elif op == 4 and b:
            j = rng.randrange(len(b))
            b[i:i] = b[j:j + rng.randint(1, 12)]
        elif op == 5:
            b[i:i] = rng.choice(commands)
        elif op == 6:
            b[i:i] = rng.choice([b"99999999", b"-1", b"00", b"FFFFFFFF",
                                 b"1e308"])
        elif op == 7:
            b[i:i] = bytes([rng.choice(MEANINGFUL)]) * rng.randint(1, 3)
    return bytes(b)


class TheSerialPortRefusesWhatItCannotRead(unittest.TestCase):
    """Damaged commands straight to `Handler`, on two programmed sites.

    `handle` already turns a raise into a refusal (test_rs232's
    ADefectIsARefusalNotAHangUp), so what this adds is the breadth: every
    documented function, damaged the ways a noisy line or a careless tool
    damages one, and a reply that is still a frame a tool can find.
    """

    ROUNDS = 1000

    def test_a_damaged_command_is_answered_or_refused(self):
        rng = random.Random(SEED)
        commands = corpus()
        for name in ("Two-tank retail site",
                     "Compliance site, CSLD and sensors"):
            c = Console()
            presets.load(c, name)
            h = wire.Handler(c, verbose=False)
            for _ in range(self.ROUNDS):
                raw = SOH + mutate(rng, rng.choice(commands), commands)
                began = time.perf_counter()
                try:
                    out = h.handle(raw)
                except Exception as exc:                    # noqa: BLE001
                    self.fail("%s: %r raised %r" % (name, raw, exc))
                took = time.perf_counter() - began
                self.assertLess(took, 2.0,
                                "%s: %r took %.1fs" % (name, raw, took))
                self.assertTrue(out is None or isinstance(out, bytes),
                                "%s: %r answered %r" % (name, raw, out))
                if out:
                    self.assertTrue(out.startswith(SOH),
                                    "%s: %r answered %r"
                                    % (name, raw, out[:40]))


class AByteItCannotSendIsNotAFunctionCodeItDoesNotKnow(unittest.TestCase):
    """FIDELITY S27. `parse_command` decodes with `errors="replace"`, so a
    byte past 7F reaches the store as U+FFFD and nothing on the way in
    refuses it. `_frame` then encoded a computer-format reply strictly, the
    encode raised, and `handle` turned the raise into `9999FF` -- the
    refusal the manual keeps for "a function code that it does not
    recognize". So one label set over the port put one inquiry into a
    permanent refusal, while its DISPLAY twin went on answering, because
    only one of the two branches of `_frame` had been made safe.

    What the console should store is still open -- clearing the high bit,
    refusing the Set, and keeping the byte are all guesses until one is
    recorded from hardware -- and nothing here asserts it.
    """

    def a_site(self):
        c = Console()
        presets.load(c, "Two-tank retail site")
        return c, wire.Handler(c, verbose=False)

    def test_both_forms_of_the_inquiry_still_answer(self):
        c, h = self.a_site()
        h.handle(SOH + b"S60201CAF\xc9\r")
        for code in (b"I60201", b"i60201"):
            out = h.handle(SOH + code + b"\r").decode("latin-1")
            self.assertNotIn("9999FF", out, code)
            self.assertIn(code.decode(), out)

    def test_the_checksum_covers_what_is_actually_sent(self):
        """The substitution happens before the sum, so a tool that checks
        the frame agrees with it. A reply that refused to encode and then
        summed the unencodable original would be worse than the refusal."""
        c, h = self.a_site()
        h.handle(SOH + b"S60201CAF\xc9\r")
        out = h.handle(SOH + b"i60201\r")
        body, sent = out.rstrip(ETX).split(b"&&")
        self.assertEqual(wire.checksum(body + b"&&"), sent.decode("ascii"))

    def test_every_settable_function_survives_a_high_byte(self):
        """Not just the one the mutator happened to find."""
        c, h = self.a_site()
        for tok in ("602", "60B", "605", "121", "503"):
            h.handle(SOH + f"S{tok}01AB".encode() + b"\xc9\r")
            out = h.handle(SOH + f"i{tok}01".encode() + b"\r")
            self.assertNotIn(b"9999FF", out, tok)


class TheSerialSessionHoldsACommandNotAStream(unittest.TestCase):
    """`wire.Framer`, the part of a session that is not the socket."""

    def assertWithinLimits(self, framer, commands, stream):
        self.assertLessEqual(len(framer.buf), wire.LONGEST_COMMAND, stream)
        self.assertLessEqual(len(framer.pending),
                             wire.LONGEST_SUBNEGOTIATION, stream)
        for raw in commands:
            self.assertTrue(raw.startswith(SOH), (raw[:40], stream[:80]))
            self.assertLessEqual(len(raw), wire.LONGEST_COMMAND, stream[:80])

    def test_a_damaged_stream_never_fills_either_buffer(self):
        rng = random.Random(SEED)
        commands = corpus()
        for _ in range(200):
            parts = []
            for _ in range(rng.randint(1, 6)):
                pick = rng.randrange(4)
                if pick == 0:
                    parts.append(SOH + mutate(rng, rng.choice(commands),
                                              commands))
                elif pick == 1:
                    parts.append(bytes(rng.choice(MEANINGFUL)
                                       for _ in range(rng.randint(1, 6))))
                elif pick == 2:
                    parts.append(bytes([rng.randrange(256)])
                                 * rng.randint(1, 2000))
                else:
                    parts.append(bytes([0xFF, 0xFA])
                                 + bytes(rng.randrange(256)
                                         for _ in range(rng.randint(0, 600))))
            stream = b"".join(parts)
            framer = wire.Framer()
            k = 0
            while k < len(stream):
                size = rng.randint(1, 4096)
                try:
                    sends, got = framer.feed(stream[k:k + size])
                except Exception as exc:                    # noqa: BLE001
                    self.fail("%r raised %r" % (stream[:80], exc))
                self.assertTrue(all(isinstance(s, bytes) for s in sends))
                self.assertWithinLimits(framer, got, stream)
                k += size

    def test_a_set_that_never_ends_is_dropped_at_the_limit(self):
        framer = wire.Framer()
        _sends, got = framer.feed(SOH + b"S60201" + b"A" * 5000)
        self.assertEqual(got, [])
        self.assertLessEqual(len(framer.buf), wire.LONGEST_COMMAND)
        _sends, got = framer.feed(SOH + b"I20100")
        self.assertEqual(got, [SOH + b"I20100"])

    def test_a_subnegotiation_that_never_ends_is_dropped_at_the_limit(self):
        framer = wire.Framer()
        framer.feed(bytes([0xFF, 0xFA]) + b"x" * 5000)
        self.assertLessEqual(len(framer.pending),
                             wire.LONGEST_SUBNEGOTIATION)
        _sends, got = framer.feed(SOH + b"I20100")
        self.assertEqual(got, [SOH + b"I20100"])

    def test_the_longest_real_commands_still_get_through(self):
        """A fourteen-pair tank chart with a security code, and the widest
        `formats` template run across sixteen devices, a byte at a time."""
        chart = (SOH + b"123456" + b"s63B0114"
                 + (b"01" + b"0" * 16) * 14 + b"\r")
        self.assertEqual(len(chart), 268)
        wide = (SOH + b"123456" + b"S5BF00"
                + b"".join(b"%02d" % d + b"X" * 30 for d in range(1, 17))
                + b"\r")
        self.assertEqual(len(wide), 526)
        for command in (chart, wide):
            framer, got = wire.Framer(), []
            for byte in command:
                got += framer.feed(bytes([byte]))[1]
            self.assertEqual(got, [command])


class TheSerialPortAnswersAfterASetThatNeverEnded(unittest.TestCase):
    """The same, through a socket, so `_session` is known to use it."""

    def test_the_inquiry_after_it_is_answered(self):
        c = Console()
        presets.load(c, "Two-tank retail site")
        got = []
        threading.Thread(target=wire.serve,
                         args=(c, "127.0.0.1", 0, False, None),
                         kwargs={"on_socket": got.append},
                         daemon=True).start()
        deadline = time.time() + 5.0
        while not got and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(got, "the server never came up")
        self.addCleanup(got[0].close)
        conn = socket.create_connection(
            ("127.0.0.1", got[0].getsockname()[1]), timeout=10.0)
        self.addCleanup(conn.close)
        # Before the limit, the inquiry became the tail of the Set the moment
        # its carriage return arrived, and was never answered as itself.
        conn.sendall(SOH + b"S60201" + b"A" * 65536)
        conn.sendall(SOH + b"I20100" + b"\r")
        buf = b""
        while ETX not in buf.replace(wire.PROBE, b""):
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        reply = buf.replace(wire.PROBE, b"")
        self.assertIn(b"I20100", reply)
        self.assertNotIn(wire.NOT_UNDERSTOOD, reply)


class TheSetupMenuKeepsALineNotAStream(unittest.TestCase):
    """The port-9999 menu's own line buffer."""

    def session(self):
        self.sent = bytearray()
        return xport.SetupSession(xport.XPortConfig(), self.sent.extend)

    def test_a_line_with_no_enter_stops_growing(self):
        s = self.session()
        before = len(self.sent)
        self.assertTrue(s.feed_bytes(b"A" * 100000))
        self.assertLessEqual(len(s._buf), xportmenu.LONGEST_LINE)
        self.assertLess(xportmenu.LONGEST_LINE, 100000)
        # what is not kept is not echoed either
        self.assertLessEqual(len(self.sent) - before, xportmenu.LONGEST_LINE)
        self.assertTrue(s.feed_bytes(b"\r"))
        self.assertTrue(s.entered)

    def test_a_damaged_walk_never_raises_and_never_overfills(self):
        rng = random.Random(SEED)
        alphabet = b"0123456789YNyn .\r\n\x08\x7f\x00\xff\xfb\x01AZaz"
        for _ in range(800):
            s = self.session()
            stream = bytes(rng.choice(alphabet)
                           for _ in range(rng.randint(1, 300)))
            if rng.random() < 0.1:
                stream += b"7" * rng.randint(100, 400)
            for k in range(0, len(stream), 7):
                try:
                    alive = s.feed_bytes(stream[k:k + 7])
                except Exception as exc:                    # noqa: BLE001
                    self.fail("%r raised %r" % (stream, exc))
                self.assertLessEqual(len(s._buf), xportmenu.LONGEST_LINE,
                                     stream)
                if not alive:
                    break


class TheWebManagerReadsNoMoreThanAForm(unittest.TestCase):
    """Z3 refused a length that was not a count. This is a count too large
    for any form the card serves."""

    def post(self, length, body=b""):
        srv = xportweb._Server(("127.0.0.1", 0), xport.XPortConfig())
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(srv.shutdown)
        crlf = b"\r\n"
        with socket.create_connection(srv.server_address[:2],
                                      timeout=5.0) as s:
            s.sendall(b"POST /secure/ltx_conf.htm HTTP/1.0" + crlf
                      + b"Content-Length: " + length + crlf + crlf + body)
            reply = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                reply += chunk
        return reply.split(crlf)[0]

    def test_a_length_past_any_form_is_answered_400(self):
        # a fixed count, so raising the limit cannot raise the test with it
        self.assertGreater(1 << 20, xportweb.LONGEST_FORM)
        status = self.post(b"%d" % (1 << 20))
        self.assertIn(b" 400", status)

    def test_a_form_that_fits_is_still_read(self):
        status = self.post(b"7", b"a=1&b=2")
        self.assertTrue(status.startswith(b"HTTP/"), status)
        self.assertNotIn(b" 400", status)


if __name__ == "__main__":
    unittest.main()
