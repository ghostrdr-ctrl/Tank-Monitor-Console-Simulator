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
"""Every code the bench TLS-350 answers, in BOTH formats, after the sweep.

`cap_coldstart` is display format out of a cold start: a blank console. On
2026-09-18, once the Set sweep had programmed it (`test_bench_sweep`), the
bench console was asked every code it answers in display format AND in
computer format (`cap_swept`, 485 codes, 970 replies) -- the first time the
computer format had been checked against hardware at all. 359 of the 485
computer replies differed, and 277 of the display ones once a refusal was
told apart from a bare frame.

This brings an emulator built as the bench console to the same state -- the
preamble, the 1,280 Sets in the order they were sent, and switching Q1 off
-- and compares every reply: display by its body under the stamp, computer
by its data between the stamp and the checksum, with the console's own
stamp inside the data (501 is the clock) read as the stamp. A refusal is
not a bare frame. Floors rise and do not fall; each exception is named.

Not committed, the same as every capture: it skips without the capture.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_console import (BENCH, at_cold_start, bench_console,  # noqa
                                lines)
import test_bench_sweep                                                # noqa

RAW = os.path.join(BENCH, "cap_swept", "raw")
#: the Sets that had been sent when the capture was taken; later probes
#: appended to the transcripts must not move the state it is compared in
SENT_BEFORE = 1280
#: and what was sent after them, before the capture
AFTER = ["S781010"]            # the line switched back off

# Replies that differ, and why.
KNOWN = {
    # the alarm history and status: the bench had hours of alarms behind
    # it, PAPER OUT among them and a latched PLLD OPEN ALARM
    "I10100", "i10100", "I11100", "i11100", "i11200", "I11300", "i11300",
    "I11400", "i11400", "I12100", "i12100",
    # the card resistances and the PC board's run counters: FIDELITY S29
    "I10200", "i10200", "I90300", "i90300",
    # the glass: the clock to the second and the standing alarm
    "I5FA00", "i5FA00",
    # 609's last bits were here, "where Table 6B is fitted, not exact".
    # They are exact now: 46 densities were set on the bench on 2026-09-19
    # and its own curve fitted to them, which is `density.py`'s constants
    # (FIDELITY S36, `tests/test_bench_density.py`).
    # the ground temperature card's low reference, 200 in this capture and
    # 199 in the thermistor logs: a reading (`groundtemp`)
    "iB2100",
}

DISPLAY_FLOOR = 477
COMPUTER_FLOOR = 475


def have():
    return os.path.isdir(RAW) and test_bench_sweep.have()


def packed_body(data):
    if not data:
        # No reply at all, which is not the same as a reply with an empty data
        # field -- and compared equal to one until 2026-09-19, so `i7B100`'s
        # silence went unnoticed in both sweeps (FIDELITY S36). It was not
        # silence: it waits for a terminator the captures never sent
        # (`framed`, `wire.WAITING_INQUIRIES`).
        return ["<NOTHING SENT>"]
    t = data.replace(b"\x01", b"").replace(b"\x03", b"").decode("latin-1")
    if "9999FF" in t:
        return ["9999FF"]
    m = re.match(r"^[is][0-9A-Z@]{5}(\d{10})?(.*?)(&&[0-9A-F]{4})?$", t, re.S)
    if not m:
        return [t]
    return [m.group(2).replace(m.group(1), "<STAMP>") if m.group(1)
            else m.group(2)]


def display_body(data):
    return ["9999FF"] if b"9999FF" in data else lines(data)


def tail(data):
    """The blank lines between a display reply's last text and ETX, which
    `lines()` strips and so no body comparison sees (FIDELITY S6)."""
    rows = data.replace(b"\x01", b"").replace(b"\x03", b"").decode(
        "latin-1").split("\r\n")
    if rows and rows[-1] == "":
        rows.pop()
    n = 0
    for row in reversed(rows):
        if row.strip():
            break
        n += 1
    return n


def framed(h, sent):
    """What a session sends back for `sent` with no terminator after it:
    nothing while the framer is still waiting for the field, the answer to
    the part that completed it if it did (the rest is dropped at the next
    SOH). The captures sent every command bare: the bench answered 77 short
    Sets and every inquiry in `wire.WAITING_INQUIRIES` with silence."""
    from tls350sim import wire
    done = wire._complete(sent)
    return h.handle(sent[:done]) if done else b""


def replay(tails=None):
    """{code: (the bench's body, ours)} for every captured reply; and, into
    `tails` if given, {code: (the bench's tail, ours)} for the display ones."""
    from tls350sim.wire import Handler
    c = bench_console()
    h = Handler(c, verbose=False)
    out = {}
    with at_cold_start(c):
        sent = [st["cmd"] for st in test_bench_sweep.steps()][:SENT_BEFORE]
        for cmd in test_bench_sweep.PREAMBLE + sent + AFTER:
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for name in sorted(os.listdir(RAW)):
            # the filesystem folds case, so computer replies are `c_i...`
            code = name[:-4].replace("c_", "")
            with open(os.path.join(RAW, name), "rb") as fh:
                real = fh.read()
            ours = framed(h, b"\x01" + code.encode("ascii"))
            body = packed_body if code[0] == "i" else display_body
            out[code] = (body(real), body(ours))
            if tails is not None and code[0] == "I":
                tails[code] = (tail(real), tail(ours))
    return out


@unittest.skipUnless(have(), "the swept capture is not here")
class TheSweptConsoleInBothFormats(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tails = {}
        cls.rows = replay(cls.tails)

    def test_every_reply_ends_as_the_bench_s_does(self):
        """The blank lines before ETX, every display code: a property of the
        code, the same in all four captures (`replytails`)."""
        wrong = sorted(code for code, (a, b) in self.tails.items() if a != b)
        self.assertEqual(wrong, [])
        self.assertEqual(len(self.tails), 485)

    def test_the_replay_reached_the_whole_capture(self):
        self.assertEqual(len(self.rows), 970)
        self.assertGreaterEqual(len(test_bench_sweep.steps()), SENT_BEFORE)

    def test_what_differs_is_named(self):
        wrong = {code for code, (a, b) in self.rows.items() if a != b}
        self.assertEqual(sorted(wrong - KNOWN), [],
                         "replies unlike the bench console's")
        self.assertEqual(sorted(KNOWN - wrong), [],
                         "these match now: take them off KNOWN")

    def test_the_floors(self):
        same = {f: sum(1 for code, (a, b) in self.rows.items()
                       if code[0] == f and a == b) for f in "Ii"}
        self.assertGreaterEqual(same["I"], DISPLAY_FLOOR)
        self.assertGreaterEqual(same["i"], COMPUTER_FLOOR)


def main():
    import difflib
    rows = replay()
    for f in "Ii":
        n = sum(1 for code, (a, b) in rows.items() if code[0] == f and a == b)
        print(f, "same", n, "of", sum(1 for code in rows if code[0] == f))
    for code, (a, b) in sorted(rows.items()):
        if a == b:
            continue
        print("DIFF", code, "(known)" if code in KNOWN else "")
        if "-v" in sys.argv:
            for line in difflib.unified_diff(a, b, "bench", "ours",
                                             lineterm="", n=0):
                print("   " + line[:160])


if __name__ == "__main__":
    main()
