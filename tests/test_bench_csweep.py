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
"""The Set sweep again, in COMPUTER format, against the bench TLS-350.

On 2026-09-19 the display sweep's 981 Sets were sent again packed -- a float
as eight hex digits, a whole number at its width, a word as it was -- each
followed by its computer Inquire (`transcripts/csweep.jsonl`). What that
settled is in `Handler._canonical` and `set_`: data short of its field's
width is not answered at all -- the console is still waiting for the rest,
and the next command's SOH drops it (`wire._complete`, which the replay
frames each command through, as a session does) -- whole numbers are held to their
range, a volume past six digits is taken and held at 999999, 77B and 77E
take a Set on any pipe, and a pipe type clears the settings it has no use
for.

The console was not where the sweep's replay leaves it: it had been through
the unit experiments and a handful of probes since `cap_swept`, and those
are replayed too, from their transcripts, in the order they were sent.
Floors rise and do not fall; each exception is named.

Not committed, the same as every capture: it skips without the transcripts.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_bench_swept as swept                                       # noqa

T = swept.test_bench_sweep.TRANSCRIPTS
LOGS = ("units", "units2", "restore52b", "lineon", "ctest")

# A code this software does not have is refused the moment its six
# characters are in -- `9999FF` to `s64801ABC` with no wait for the float --
# where the framer waits for the field's width, since it cannot ask the
# console which codes it knows. FIDELITY S38.
KNOWN_VERDICT = {"s63D01ABC", "s64801ABC", "s65101ABC", "s65201ABC",
                 "s65301ABC", "s65401ABC", "s65501ABC", "s7C701ABC",
                 "s7C801ABC", "s7D801ABC", "s7D901ABC", "s7DB01ABC",
                 "s7DC01ABC", "s81201ABC", "s81301ABC"}
# and 75F's and 80C's packed widths are longer than these, by how much is
# not measured: the bench waited on all of them, its cards being absent
KNOWN_REPLY = KNOWN_VERDICT | {
    "s75F010", "s75F011", "s75F012", "s75F019", "s80C0100", "s80C0111",
    "s80C0121", "s80C0131", "s80C0141", "s80C0151", "s80C0199"}
KNOWN_READ = set()
# The first two were 980 against a replay that sent each command straight to
# the handler, so a Set the bench was still waiting on counted as a bare
# echo. Framed as a session is (2026-09-22), they are these.
VERDICT_FLOOR = 966
REPLY_FLOOR = 955
READ_FLOOR = 981


def have():
    return swept.have() and all(
        os.path.exists(os.path.join(T, name)) for name in
        ["csweep.jsonl", "probes.jsonl"] + [n + ".log" for n in LOGS])


def _sent(name):
    """The SENDs of an experiment transcript, as they were sent."""
    out = []
    with open(os.path.join(T, name + ".log"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^=== \S+ \S+  SEND b'(.*)'  \(", line)
            if m:
                out.append(m.group(1).encode("latin-1").decode(
                    "unicode_escape"))
    return out


def between():
    """Every command from the swept capture to the computer sweep."""
    with open(os.path.join(T, "probes.jsonl"), encoding="utf-8") as fh:
        probes = [json.loads(line) for line in fh]
    group = lambda *names: [p["cmd"] for p in probes if p["group"] in names]
    return (_sent("units") + _sent("units2")
            + group("weekdays", "weekdays2", "restore52b", "misc", "r77b",
                    "r77b2")
            + _sent("restore52b") + _sent("lineon")
            + group("metricranges") + _sent("ctest"))


def verdict(raw):
    if "9999FF" in raw:
        return "9999"
    t = raw.replace("\x01", "").replace("\x03", "")
    m = re.match(r"^[is][0-9A-Z]{5}\d{10}(.*?)(&&[0-9A-F]{4})?$", t, re.S)
    data = m.group(1) if m else t
    if not data:
        return "bare"
    return "refused" if set(data) == {"?"} else "accepted"


def replay():
    from tls350sim.wire import Handler
    with open(os.path.join(T, "csweep.jsonl"), encoding="utf-8") as fh:
        steps = [json.loads(line) for line in fh]
    c = swept.bench_console()
    h = Handler(c, verbose=False)
    rows = []
    with swept.at_cold_start(c):
        sent = [st["cmd"] for st in swept.test_bench_sweep.steps()][
            :swept.SENT_BEFORE]
        for cmd in (swept.test_bench_sweep.PREAMBLE + sent + swept.AFTER
                    + between()):
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for st in steps:
            mine = swept.framed(h, b"\x01" + st["cmd"].encode("latin-1"))
            read = h.handle(b"\x01" + st["read"].encode("latin-1"))
            body = swept.packed_body
            rows.append({"cmd": st["cmd"],
                         "verdict": verdict(st["set"]),
                         "our_verdict": verdict(mine.decode("latin-1")),
                         "set": body(st["set"].encode("latin-1")),
                         "our_set": body(mine),
                         "read": body(st["read"].encode("latin-1")),
                         "our_read": body(read)})
    return rows


@unittest.skipUnless(have(), "the computer sweep transcripts are not here")
class TheComputerFormatSweep(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = replay()

    def held(self, a, b, known, floor):
        wrong = {r["cmd"] for r in self.rows if r[a] != r[b]}
        self.assertEqual(sorted(wrong - known), [], a + " unlike the bench")
        self.assertEqual(sorted(known - wrong), [],
                         "named exceptions that now agree: take them off")
        self.assertGreaterEqual(len(self.rows) - len(wrong), floor)

    def test_verdicts(self):
        self.held("verdict", "our_verdict", KNOWN_VERDICT, VERDICT_FLOOR)

    def test_replies(self):
        self.held("set", "our_set", KNOWN_REPLY, REPLY_FLOOR)

    def test_reads(self):
        self.held("read", "our_read", KNOWN_READ, READ_FLOOR)


if __name__ == "__main__":
    for r in replay():
        for a, b in (("verdict", "our_verdict"), ("set", "our_set"),
                     ("read", "our_read")):
            if r[a] != r[b]:
                print(a.upper(), r["cmd"], r[a], r[b])
