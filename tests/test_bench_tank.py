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
"""The bench TLS-350 with tank 1 configured and no probe wired to it.

After the computer-format Set sweep, tank 1 was switched on (`S601011`,
`transcripts/tank1on.log`) and every code asked in both formats
(`cap_tank1on`); then switched off again. No PROBE OUT was raised in the
two and a half minutes watched, nor during the capture. What it showed: a
63C Set had put the tank on the fifty point profile, 680 lists a configured
tank's average sales without Fuel Manager, 5BE and 5BF answer once custom
alarms are on, and I63B's tank line and columns.

Not committed, the same as every capture: it skips without the capture.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_bench_csweep as csweep                                     # noqa
import test_bench_swept as swept                                       # noqa

RAW = os.path.join(swept.BENCH, "cap_tank1on", "raw")

# Beyond test_bench_swept.KNOWN:
KNOWN = {
    # the alarm history: this console logs a SETUP DATA WARNING for the
    # tank and the line's 501 feet that the bench had not raised
    "I11200",
    # and the tank alarm history lists those same warnings, now that it
    # reads the tanks 601 has switched on rather than the tanks with a
    # probe reporting (FIDELITY S35)
    "I20600", "i20600",
    # and the tank status lists a probe-less tank once it has an alarm
    # standing, which that same warning is (2026-09-24)
    "I20500", "i20500",
}
# 475 and 474 until 2026-09-24, when I205 started listing a probe-less tank
# with an alarm standing -- here the SETUP DATA WARNING above -- and joined
# KNOWN: a match lost to naming one more face of the same gap
FLOORS = (474, 474)


def have():
    return csweep.have() and os.path.isdir(RAW)


def replay():
    from tls350sim.wire import Handler
    with open(os.path.join(csweep.T, "csweep.jsonl"), encoding="utf-8") as fh:
        steps = [json.loads(line) for line in fh]
    c = swept.bench_console()
    h = Handler(c, verbose=False)
    out = {}
    with swept.at_cold_start(c):
        sent = [st["cmd"] for st in swept.test_bench_sweep.steps()][
            :swept.SENT_BEFORE]
        for cmd in (swept.test_bench_sweep.PREAMBLE + sent + swept.AFTER
                    + csweep.between() + [st["cmd"] for st in steps]
                    + ["S601011\r"]):
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for fname in sorted(os.listdir(RAW)):
            code = fname[:-4].replace("c_", "")
            with open(os.path.join(RAW, fname), "rb") as fh:
                real = fh.read()
            ours = swept.framed(h, b"\x01" + code.encode("ascii"))
            body = swept.packed_body if code[0] == "i" else swept.display_body
            out[code] = (body(real), body(ours))
    return out


@unittest.skipUnless(have(), "the tank capture is not here")
class TankOneConfiguredWithNoProbe(unittest.TestCase):

    def test_every_reply(self):
        rows = replay()
        wrong = {code for code, (a, b) in rows.items() if a != b}
        self.assertEqual(sorted(wrong - swept.KNOWN - KNOWN), [],
                         "replies unlike the bench console's")
        self.assertEqual(sorted(KNOWN - wrong), [],
                         "these match now: take them off KNOWN")
        for f, floor in zip("Ii", FLOORS):
            same = sum(1 for code, (a, b) in rows.items()
                       if code[0] == f and a == b)
            self.assertGreaterEqual(same, floor, f)


if __name__ == "__main__":
    for code, (a, b) in sorted(replay().items()):
        if a != b and code not in swept.KNOWN:
            print("DIFF", code, "(known)" if code in KNOWN else "")
