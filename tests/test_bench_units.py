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
"""The same console in METRIC and in IMPERIAL units, in both formats.

After `cap_swept` the bench TLS-350 was switched to metric (`S51700201`) and
asked every code again in both formats (`cap_metric`), then to imperial
(`S51700301`, `cap_imperial`), then back to U.S., where every stored value
read its old bits -- the console stores U.S. units and converts on the way
in and out. `tls350sim/units.py` is what that measured; this holds it, the
swept state replayed with the unit Sets after it, against `test_bench_swept`'s
exceptions and the few below.

Not committed, the same as every capture: it skips without the captures.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import test_bench_swept as swept                                       # noqa

SYSTEMS = {
    # capture: the unit Sets sent after the swept state, before it
    "cap_metric": ["S51700201"],
    "cap_imperial": ["S51700201", "S51700301"],
}

# Beyond test_bench_swept.KNOWN:
KNOWN = {
    "cap_metric": {
        # 78F's 25 psi packs as 172.29 kPa and prints `7 KPA` -- the
        # firmware drawing a three-digit number in a one-digit cell
        "I78F00",
        # the thermistor's reference count moved from 966 to 967 between
        # the captures: a reading, not a setting
        "IB2100",
        # and its low reference 199 or 200 in the packed form: `groundtemp`
        # sends 199, which the thermistor logs read
        "iB2100",
    },
    "cap_imperial": {"IB2100", "iB2100"},
}
FLOORS = {"cap_metric": (475, 474), "cap_imperial": (476, 474)}


def have():
    return swept.have() and all(
        os.path.isdir(os.path.join(swept.BENCH, name, "raw"))
        for name in SYSTEMS)


def replay(name):
    from tls350sim.wire import Handler
    c = swept.bench_console()
    h = Handler(c, verbose=False)
    raw = os.path.join(swept.BENCH, name, "raw")
    out = {}
    with swept.at_cold_start(c):
        sent = [st["cmd"] for st in swept.test_bench_sweep.steps()][
            :swept.SENT_BEFORE]
        for cmd in (swept.test_bench_sweep.PREAMBLE + sent + swept.AFTER
                    + SYSTEMS[name]):
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for fname in sorted(os.listdir(raw)):
            code = fname[:-4].replace("c_", "")
            with open(os.path.join(raw, fname), "rb") as fh:
                real = fh.read()
            ours = swept.framed(h, b"\x01" + code.encode("ascii"))
            body = swept.packed_body if code[0] == "i" else swept.display_body
            out[code] = (body(real), body(ours))
    return out


@unittest.skipUnless(have(), "the unit captures are not here")
class TheSameConsoleInOtherUnits(unittest.TestCase):

    def check(self, name):
        rows = replay(name)
        known = swept.KNOWN | KNOWN[name]
        wrong = {code for code, (a, b) in rows.items() if a != b}
        self.assertEqual(sorted(wrong - known), [],
                         f"{name}: replies unlike the bench console's")
        self.assertEqual(sorted(KNOWN[name] - wrong), [],
                         f"{name}: these match now: take them off KNOWN")
        for f, floor in zip("Ii", FLOORS[name]):
            same = sum(1 for code, (a, b) in rows.items()
                       if code[0] == f and a == b)
            self.assertGreaterEqual(same, floor, f"{name} {f}")

    def test_metric(self):
        self.check("cap_metric")

    def test_imperial(self):
        self.check("cap_imperial")


if __name__ == "__main__":
    import difflib
    for name in SYSTEMS:
        rows = replay(name)
        for f in "Ii":
            n = sum(1 for code, (a, b) in rows.items() if code[0] == f and a == b)
            print(name, f, "same", n)
        for code, (a, b) in sorted(rows.items()):
            if a != b and code not in swept.KNOWN:
                print("DIFF", name, code, "(known)" if code in KNOWN[name] else "")
                if "-v" in sys.argv:
                    for line in difflib.unified_diff(a, b, "bench", "ours",
                                                     lineterm="", n=0):
                        print("   " + line[:150])
