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
"""What a line feed, a tab, a NUL and an ESC are to the console.

On 2026-09-25 the bench TLS-350 was sent raw bytes over its tunnel
(`bench-2026-09-25/lf.jsonl`, inquiries only, and `lfset.jsonl`, tank 4's
label set to itself with a control byte in it and put back). A line feed is
a character like any other, not a terminator: `I61F00<LF>` is a delivery
type, refused a `?` a tank; `I61F<LF>00` an unknown code; `SUPER<LF>` a
label refused six `?`. The 61F inquiry is answered at its one-character type
with no terminator at all. In a label a tab is refused, a NUL ends it, and
an ESC abandons the Set. Each log is fed through one `wire.Framer`, as its
session was, and every reply compared byte for byte. It skips without the
logs. FIDELITY S38.
"""
import json
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "console_capture")
DAY = os.path.join(BENCH, "bench-2026-09-25")
SNAPSHOT = os.path.join(BENCH, "bench-2026-09-24", "snapshot.vrset")
LOGS = ("lf.jsonl", "lfset.jsonl",
        # 537 and 538, set on port 1 and on this session's own, with 531 on
        "etx.jsonl", "etxset.jsonl", "etxself.jsonl", "etxeom.jsonl")
STAMP = re.compile(r"JAN (\d+), 2006 +(\d+):(\d\d) ([AP]M)")
CSTAMP = re.compile(r"^\x01[is][0-9A-F]{5}(\d{10})")


def have():
    return os.path.exists(SNAPSHOT) and all(
        os.path.exists(os.path.join(DAY, log)) for log in LOGS)


def replay(log):
    """[(case, what was sent, the bench's bytes, ours)] for one session."""
    from tls350sim import sitebackup, wire
    from tls350sim.console import Console
    c = Console(os.path.join(tempfile.mkdtemp(), "state.json"))
    sitebackup.load(c, SNAPSHOT)
    # the tunnel comes in on comm board 2, the S-SAT: `I53799` answered as
    # port 2 and 888 put the cut session of 2026-09-19 there. This console
    # puts a session on its first RS-232 board (FIDELITY S38).
    c.rs232_port = lambda: 2
    h = wire.Handler(c, verbose=False)
    framer = wire.Framer()
    out = []
    with open(os.path.join(DAY, log), encoding="utf-8") as fh:
        steps = [json.loads(line) for line in fh]
    for step in steps:
        if "cmd" in step:
            # a `benchset.py` record: the command bare, a Set with its CR
            cmd = step["cmd"]
            step = {"case": cmd, "got": step["reply"],
                    "sent": "\x01" + cmd + ("\r" if cmd[0] in "Ss" else "")}
        got = step["got"]
        m, cm = STAMP.search(got), CSTAMP.match(got)
        at = (time.strptime("06 01 %s %s %s %s" % m.groups(),
                            "%y %m %d %I %M %p") if m else
              time.strptime(cm.group(1), "%y%m%d%H%M") if cm else None)
        if at:
            c.clock_offset = time.mktime(
                at[:5] + (30,) + at[6:8] + (-1,)) - time.time()
        sent = step["sent"].encode("latin-1")
        if log.startswith("etx"):
            # FF is telnet's IAC to this console's session, which strips it
            # so that someone at a telnet prompt can type at it (FIDELITY
            # S38, the telnet probe); the bench's raw tunnel took it as data
            # and stored `A`. The replay sends that `A`.
            sent = sent.replace(b"\xff", b"A")
        _sends, commands = framer.feed(sent)
        ours = b"".join(h.handle(cmd) or b"" for cmd in commands)
        out.append((step["case"], step["sent"], got.encode("latin-1"), ours))
    return out


@unittest.skipUnless(have(), "the 2026-09-25 bench logs are not here")
class ALineFeedIsACharacter(unittest.TestCase):

    def test_every_reply_agrees_to_the_byte(self):
        for log in LOGS:
            for case, sent, real, ours in replay(log):
                with self.subTest(log=log, case=case, sent=sent):
                    self.assertEqual(ours, real)


class ByHand(unittest.TestCase):
    """The same rules, held without the logs."""

    def commands(self, data):
        from tls350sim import wire
        return wire.Framer().feed(data)[1]

    def test_a_line_feed_ends_nothing(self):
        self.assertEqual(self.commands(b"\x01S60204SUPER\n"), [])
        self.assertEqual(self.commands(b"\x01I61F\n00"), [b"\x01I61F\n0"])

    def test_61F_is_answered_at_its_delivery_type(self):
        self.assertEqual(self.commands(b"\x01I61F002"), [b"\x01I61F002"])

    def test_esc_abandons_a_set(self):
        self.assertEqual(self.commands(b"\x01S60204SUPER\x1b\r"), [])

    def test_a_label_refuses_a_tab_and_ends_at_a_nul(self):
        from tls350sim import fieldio
        field = {"kind": "text", "maxlen": 20}
        with self.assertRaises(ValueError):
            fieldio.encode_value(field, "SUPER\t")
        with self.assertRaises(ValueError):
            fieldio.encode_value(field, "SUPER\n")
        self.assertEqual(fieldio.encode_value(field, "SU\x00PER").strip(),
                         "SU")


if __name__ == "__main__":
    unittest.main()
