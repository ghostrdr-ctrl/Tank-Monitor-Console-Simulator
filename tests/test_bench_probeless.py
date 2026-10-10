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
"""The bench TLS-350 with no probe card, 2026-10-09.

The probe card came out, the PLLD sensor board went into slot 1 with the
interstitial card left in slot 2, and the bench was cold started
(`bench-2026-10-09-plld`, every code in both formats straight after). Most
of what a missing probe card silences, it silences; but the in-tank tests
stay on the feature list, two console-wide 6xx settings still answer, and
the tank status and alarm history still head their tables. CLOSED S56.

Not committed, like every capture: the class skips without its data.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_console import at_cold_start, bench_console   # noqa: E402
from test_bench_liquid import body, framed, read, SOH         # noqa: E402
from test_bench_swept import tail                             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "console_capture", "bench-2026-10-09-plld", "raw")


def probeless_bench():
    """The bench's cage on 2026-10-09: no probe card, the PLLD sensor
    board and the interstitial card, the PLLD power board, and the WPLLD
    comm card in comm 3; nothing on any input."""
    c = bench_console()
    c.modules["probe"] = 0
    c.probe_gt = False
    c.modules["liquid"] = 1
    c.modules["wplldcom"] = 1
    c.comm_slots = {1: "rs232", 2: "ssat", 3: "wplldcom"}
    c.out_of_paper = False
    for n in range(1, 9):
        c.sensor_state[("liquid", str(n))] = "open"
    return c


@unittest.skipUnless(os.path.isdir(RAW),
                     "the 2026-10-09 probe-less capture is not here")
class TheProbelessBench(unittest.TestCase):
    """All 485 codes in both formats, against the emulator at the instant
    the bench came back from its cold start."""

    #: what differs, each for a reason that is not a defect this can see
    KNOWN = {
        # the cards' own ID resistors
        "I10200", "i10200",
        # the BATTERY IS OFF pair the cold start PROCEDURE logs
        "I11100", "i11100", "I11400", "i11400",
        # the clock to the second, and the PC board's own run counters
        "I5FA00", "i5FA00", "I90300", "i90300",
        # the A/D reference channels, which drift by a count or two
        "IB0100", "iB0100",
    }
    FLOOR = 958

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        c = probeless_bench()
        h = Handler(c, verbose=False)
        cls.rows, cls.tails = {}, {}
        with at_cold_start(c):
            for name in sorted(os.listdir(RAW)):
                code = name[:-4].replace("c_", "")
                real = read(os.path.join(RAW, name))
                ours = framed(h, SOH + code.encode("ascii"))
                cls.rows[code] = (body(code, real), body(code, ours))
                if code[0] == "I":
                    cls.tails[code] = (tail(real), tail(ours))

    def test_what_differs_is_named(self):
        wrong = {code for code, (a, b) in self.rows.items() if a != b}
        self.assertEqual(sorted(wrong - self.KNOWN), [])
        self.assertEqual(sorted(self.KNOWN - wrong), [],
                         "these match now: take them off KNOWN")

    def test_every_reply_ends_as_the_bench_s_does(self):
        wrong = sorted(code for code, (a, b) in self.tails.items() if a != b)
        self.assertEqual(wrong, [])

    def test_the_floor(self):
        same = sum(1 for a, b in self.rows.values() if a == b)
        self.assertGreaterEqual(same, self.FLOOR)

    def test_the_in_tank_tests_stay_on_the_feature_list(self):
        bench, ours = self.rows["I90200"]
        self.assertEqual(bench, ours)
        self.assertIn("  PERIODIC IN-TANK TESTS", bench)


class TheSystemSetupWalk(unittest.TestCase):
    """What the bench's glass walked in SYSTEM SETUP with no probe card
    (`bench-2026-10-09-plld/glass.jsonl`): the tank test warnings, TC
    volumes, temperature compensation and stick offset were all there, and
    the line tests read 0.20 and 0.10 GPH. CLOSED S60."""

    def test_the_screens_no_probe_card_hides(self):
        from tls350sim.console import SETUP_MENU
        c = probeless_bench()
        fn = [f for f in SETUP_MENU if f["function"] == "SYSTEM SETUP"][0]
        heads = [st.get("head") or st.get("text", "").upper()
                 for st in c.visible_steps(fn, 1)]
        texts = [st.get("text", "") for st in c.visible_steps(fn, 1)]
        for name in ("Tank Per Tst Needed Wrn (Disable/Enable)",
                     "Tank Ann Tst Needed Wrn (Disable/Enable)",
                     "Print Tc Volume", "Temp Compensation",
                     "Stick Height Offset (Disable/Enable)"):
            self.assertIn(name, texts)
        self.assertIn("0.20 GPH LINE TEST", heads)
        self.assertIn("0.10 GPH LINE TEST", heads)
