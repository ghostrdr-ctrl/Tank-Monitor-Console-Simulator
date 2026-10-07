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
"""The probes sent to the bench TLS-350 between the captures, replayed.

After `cap_swept` a handful of questions were put to the bench one Set at a
time (`transcripts/probes.jsonl`, each Set with its Inquire): 52B's weekly
days, 535's hangup method, 525's ports, 77B's range on a USER DEFINED line,
and the ranges held in metric. And the devices a site has not got were
asked for (`transcripts/devices.log`). This replays them in the order they
were sent, with everything else sent between, and compares each reply.

Not committed, the same as every capture: it skips without the transcripts.
"""
import ast
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_bench_csweep as csweep                                     # noqa
import test_bench_swept as swept                                       # noqa

T = csweep.T

# Replies that differ, and why. None, since 2026-09-19.
KNOWN = set()
FLOOR = 146


def have():
    return csweep.have() and os.path.exists(os.path.join(T, "devices.log"))


def _replies(name):
    """(sent, reply) pairs of an experiment transcript."""
    out, sent = [], None
    with open(os.path.join(T, name + ".log"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^=== \S+ \S+  SEND b'(.*)'  \(", line)
            if m:
                sent = m.group(1).encode("latin-1").decode("unicode_escape")
                continue
            if sent is not None and line.startswith("b'"):
                out.append((sent, ast.literal_eval(line.strip()).decode(
                    "latin-1")))
                sent = None
    return out


def body(raw, cmd):
    data = raw.encode("latin-1")
    if cmd[:1] in "is":
        return swept.packed_body(data)
    return swept.display_body(data)


def replay():
    from tls350sim.wire import Handler
    with open(os.path.join(T, "probes.jsonl"), encoding="utf-8") as fh:
        probes = [json.loads(line) for line in fh]
    group = lambda *names: [p for p in probes if p["group"] in names]
    c = swept.bench_console()
    h = Handler(c, verbose=False)
    ask = lambda cmd: h.handle(b"\x01" + cmd.encode("latin-1")).decode("latin-1")
    rows = []

    def asked(pairs):
        for cmd, reply in pairs:
            rows.append((cmd.strip(), body(reply, cmd), body(ask(cmd), cmd)))

    def probed(entries):
        for p in entries:
            rows.append((p["cmd"], body(p["set"], p["cmd"]),
                         body(ask(p["cmd"] + "\r"), p["cmd"])))
            read = "I" + p["cmd"][1:6]
            rows.append((p["cmd"] + ">" + read, body(p["read"], read),
                         body(ask(read), read)))

    with swept.at_cold_start(c):
        sent = [st["cmd"] for st in swept.test_bench_sweep.steps()][
            :swept.SENT_BEFORE]
        for cmd in (swept.test_bench_sweep.PREAMBLE + sent + swept.AFTER
                    + csweep._sent("units") + csweep._sent("units2")):
            ask(cmd)
        asked(_replies("devices"))
        probed(group("weekdays", "weekdays2", "restore52b", "misc", "r77b",
                     "r77b2"))
        for cmd in csweep._sent("restore52b") + csweep._sent("lineon"):
            ask(cmd)
        probed(group("metricranges"))
    return rows


@unittest.skipUnless(have(), "the probe transcripts are not here")
class TheProbes(unittest.TestCase):

    def test_each_reply(self):
        rows = replay()
        wrong = {cmd.split(">")[0] for cmd, a, b in rows if a != b}
        self.assertEqual(sorted(wrong - KNOWN), [], "replies unlike the bench")
        self.assertEqual(sorted(KNOWN - wrong), [],
                         "these match now: take them off KNOWN")
        self.assertGreaterEqual(sum(1 for _c, a, b in rows if a == b), FLOOR)


if __name__ == "__main__":
    import difflib
    rows = replay()
    print(sum(1 for _c, a, b in rows if a == b), "of", len(rows), "alike")
    for cmd, a, b in rows:
        if a != b:
            print("DIFF", cmd, "(known)" if cmd.split(">")[0] in KNOWN else "")
            if "-v" in sys.argv:
                for line in difflib.unified_diff(a, b, "bench", "ours",
                                                 lineterm="", n=0):
                    print("   " + line[:150])
