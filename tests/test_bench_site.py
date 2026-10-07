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
"""The bench TLS-350 with a pressure line on, and then programmed as a site.

Two captures after the computer-format sweep, every code in both formats:
`cap_q1on` with Q1 switched on and no transducer wired to it, and `cap_site`
with all four tanks on, labelled, given full volumes, diameters and low
limits, and Q1 on, labelled and on tank 1 (`transcripts/site.log`). Each is
replayed through every Set sent to the bench before it, in order.

The console's lines have their transducers OPEN, as the bench's had nothing
wired, and the replay moves the console's clock as the bench's moved --
between logged commands, and on to the capture's own stamp -- with the
ALARM/TEST `exp.py` sent after every experiment. What still differs is
named below: the line's alarm state and one reading's noise, FIDELITY S34.

Not committed, the same as every capture: it skips without the captures.
"""
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_bench_tank as tank                                         # noqa
import test_bench_csweep as csweep                                     # noqa
import test_bench_swept as swept                                       # noqa

# What is left of the unwired line (FIDELITY S34), since its transducers
# are OPEN here as the bench's were and the replay keeps the bench's clock:
# the line's alarm state and history -- this console's SETUP DATA WARNINGs
# from the replay's 501 feet, which the bench never raised, and its log of
# every PLLD OPEN where the bench kept one -- and the A/D references' last
# digits, which move read to read on the bench (2901.85 to 2902.42).
UNWIRED = {"I38100", "i38100", "I38200", "i38200", "iB8100"}
CAPTURES = {
    # capture: (the logs sent after the computer sweep, named, floors)
    "cap_q1on": (("tank1on", "tank1off", "q1on"),
                 UNWIRED | tank.KNOWN | {"IB2100"}, (472, 470)),
    # 472 until 2026-09-24: I205 now lists a probe-less tank with an alarm
    # standing, and the warning this console raises there is KNOWN
    # (`test_bench_tank`)
    "cap_site": (("tank1on", "tank1off", "q1on", "q1off", "finalcheck",
                  "site"),
                 UNWIRED | tank.KNOWN | {"IB2100"}, (471, 470)),
}


def have():
    return tank.have() and all(
        os.path.isdir(os.path.join(swept.BENCH, name, "raw"))
        for name in CAPTURES)


def _sent(name):
    """[(seconds since the command before, command)] from a transcript,
    with the ALARM/TEST `exp.py` sends at the end of every experiment
    (`=== auto S00300`), which the bench acted on as surely as the rest."""
    import time as _time
    out, last = [], None
    with open(os.path.join(csweep.T, name + ".log"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^=== (\S+ \S+)  SEND b'(.*)'  \(", line)
            if m:
                when = _time.mktime(_time.strptime(m.group(1),
                                                   "%Y-%m-%d %H:%M:%S"))
                gap = 0.0 if last is None else max(0.0, when - last)
                last = when
                out.append((gap, m.group(2).encode("latin-1").decode(
                    "unicode_escape")))
            elif line.startswith("=== auto S00300"):
                out.append((0.0, "S00300"))
    return out


def _stamp_of(path):
    """The console time a display reply was stamped with, or None."""
    try:
        with open(path, "rb") as fh:
            rows = fh.read().decode("latin-1").split("\r\n")
    except OSError:
        return None
    for row in rows:
        try:
            return time.mktime(time.strptime(" ".join(row.split()),
                                             "%b %d, %Y %I:%M %p"))
        except ValueError:
            continue
    return None


def replay(name):
    import json
    from tls350sim.wire import Handler
    logs = CAPTURES[name][0]
    with open(os.path.join(csweep.T, "csweep.jsonl"), encoding="utf-8") as fh:
        steps = [json.loads(line) for line in fh]
    c = swept.bench_console()
    h = Handler(c, verbose=False)
    raw = os.path.join(swept.BENCH, name, "raw")
    out = {}
    with swept.at_cold_start(c):
        sent = [st["cmd"] for st in swept.test_bench_sweep.steps()][
            :swept.SENT_BEFORE]
        chain = [(0.0, cmd) for cmd in
                 swept.test_bench_sweep.PREAMBLE + sent + swept.AFTER
                 + csweep.between() + [st["cmd"] for st in steps]]
        for log in logs:
            chain += _sent(log)
        for gap, cmd in chain:
            if gap:
                # the console's clock moves as the bench's did between the
                # two commands, so a test started on a line runs its course
                c.clock_offset += gap
                c.tick()
            h.handle(b"\x01" + cmd.encode("latin-1"))
        # and on to the moment the capture was taken, which was minutes
        # after the log's last command: the stamp on its I10100
        taken = _stamp_of(os.path.join(raw, "I10100.bin"))
        now = time.mktime(c.now())
        if taken and taken > now:
            c.clock_offset += taken - now
            c.tick()
        for fname in sorted(os.listdir(raw)):
            code = fname[:-4].replace("c_", "")
            with open(os.path.join(raw, fname), "rb") as fh:
                real = fh.read()
            ours = swept.framed(h, b"\x01" + code.encode("ascii"))
            body = swept.packed_body if code[0] == "i" else swept.display_body
            out[code] = (body(real), body(ours))
    return out


@unittest.skipUnless(have(), "the line and site captures are not here")
class ALineOnAndASiteProgrammed(unittest.TestCase):

    def check(self, name):
        _logs, known, floors = CAPTURES[name]
        rows = replay(name)
        wrong = {code for code, (a, b) in rows.items() if a != b}
        self.assertEqual(sorted(wrong - swept.KNOWN - known), [],
                         f"{name}: replies unlike the bench console's")
        for f, floor in zip("Ii", floors):
            same = sum(1 for code, (a, b) in rows.items()
                       if code[0] == f and a == b)
            self.assertGreaterEqual(same, floor, f"{name} {f}")

    def test_a_line_on(self):
        self.check("cap_q1on")

    def test_a_programmed_site(self):
        self.check("cap_site")


if __name__ == "__main__":
    for name in CAPTURES:
        rows = replay(name)
        for code, (a, b) in sorted(rows.items()):
            if a != b and code not in swept.KNOWN:
                print(name, "DIFF", code,
                      "(named)" if code in CAPTURES[name][1] else "")
