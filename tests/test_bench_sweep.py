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
"""The emulator's answers to 1,280 Sets, against the bench TLS-350's.

On 2026-09-18, after the experiments `test_bench_sets` replays, every
settable field the code manual documents was sent the edges of its range --
below, at, between, at and past the ends, and a word -- one display-format
Set at a time, each followed by that device's Inquire
(`transcripts/setsweep.jsonl`, 981 Sets). The ranges the sweep left open
were then bisected on the same console, and a few rules it turned up were
tried out (`transcripts/edges.jsonl`).

This replays both in the order they were sent, through an emulator built as
the bench console, and compares three things for each: whether the Set was
accepted, refused or answered bare; the whole of the Set's reply; and the
report its Inquire read back. Each is a ratchet: the floor may rise and not
fall, and each exception is named, held in both directions -- one that
starts to agree has to come off the list.

Not committed, the same as every capture: it skips without the transcripts.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_console import (BENCH, at_cold_start, bench_console,  # noqa
                                lines)

TRANSCRIPTS = os.path.join(BENCH, "transcripts")
FILES = ("setsweep.jsonl", "edges.jsonl")
# the state the sweep began in: e22's 200 feet on Q1, as test_bench_sets,
# and the coefficient the density experiments after the cold start left on
# tank 1 (dentc, 99.9999 API)
PREAMBLE = ["S78901200", "S609010.000915"]

# Sets answered unlike the bench, and why.
KNOWN_VERDICT = set()
# Set replies whose body is not the bench's, beyond the verdicts above.
KNOWN_REPLY = set()
# Inquires read back after a Set whose body is not the bench's: the
# verdicts above, carried into the value the next Inquire reads.
KNOWN_READ = set()

VERDICT_FLOOR = 1280
REPLY_FLOOR = 1280
READ_FLOOR = 1280


def have():
    return all(os.path.exists(os.path.join(TRANSCRIPTS, f)) for f in FILES)


def verdict(raw):
    if "9999FF" in raw:
        return "9999"
    body = lines(raw.encode("latin-1"))
    if not body:
        return "bare"
    if len(body) == 1 and set(body[0].strip()) == {"?"}:
        return "refused"
    return "accepted"


def steps():
    out = []
    for name in FILES:
        with open(os.path.join(TRANSCRIPTS, name), encoding="utf-8") as fh:
            out += [json.loads(line) for line in fh]
    return out


def replay():
    """One dict per step, in sending order: the command, and the bench's
    and our Set reply and read-back."""
    from tls350sim.wire import Handler
    c = bench_console()
    h = Handler(c, verbose=False)
    out = []
    with at_cold_start(c):
        for cmd in PREAMBLE:
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for st in steps():
            mine = h.handle(b"\x01" + st["cmd"].encode("latin-1"))
            asked = "I" + st["cmd"][1:6]
            read = h.handle(b"\x01" + asked.encode("latin-1"))
            out.append({"cmd": st["cmd"],
                        "set": lines(st["set"].encode("latin-1")),
                        "our_set": lines(mine),
                        "verdict": verdict(st["set"]),
                        "our_verdict": verdict(mine.decode("latin-1")),
                        "read": lines(st["read"].encode("latin-1")),
                        "our_read": lines(read)})
    return out


def differing(rows, a, b):
    return {r["cmd"] for r in rows if r[a] != r[b]}


@unittest.skipUnless(have(), "the bench sweep transcripts are not here")
class TheSweep(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = replay()

    def held(self, a, b, known, floor, what):
        wrong = differing(self.rows, a, b)
        self.assertEqual(sorted(wrong - known), [], what + " unlike the bench")
        self.assertEqual(sorted(known - wrong), [],
                         "named exceptions that now agree: take them off")
        self.assertGreaterEqual(len(self.rows) - len(wrong), floor)

    def test_verdicts(self):
        self.held("verdict", "our_verdict", KNOWN_VERDICT, VERDICT_FLOOR,
                  "Sets answered")

    def test_replies(self):
        self.held("set", "our_set", KNOWN_VERDICT | KNOWN_REPLY, REPLY_FLOOR,
                  "Set replies")

    def test_reads(self):
        self.held("read", "our_read", KNOWN_READ, READ_FLOOR, "read-backs")


def main():
    import difflib
    rows = replay()
    for a, b in (("verdict", "our_verdict"), ("set", "our_set"),
                 ("read", "our_read")):
        print(a, "agreeing", len(rows) - len(differing(rows, a, b)),
              "of", len(rows))
    verbose = "-v" in sys.argv
    for a, b, known in (("verdict", "our_verdict", KNOWN_VERDICT),
                        ("set", "our_set", KNOWN_VERDICT | KNOWN_REPLY),
                        ("read", "our_read", KNOWN_READ)):
        seen = set()
        for r in rows:
            if r[a] == r[b]:
                continue
            if r["cmd"][1:4] in seen and not verbose:
                continue
            seen.add(r["cmd"][1:4])
            print(a.upper(), r["cmd"], "(known)" if r["cmd"] in known else "")
            if a == "verdict":
                print("   bench=%s ours=%s" % (r[a], r[b]))
                continue
            for line in difflib.unified_diff(r[a], r[b], "bench", "ours",
                                             lineterm="", n=0):
                print("   " + line)


if __name__ == "__main__":
    main()
