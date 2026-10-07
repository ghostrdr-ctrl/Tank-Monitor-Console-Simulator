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
"""The ground temperature inputs, against resistors on the bench TLS-350.

On 2026-09-19 metered resistors were wired on the Probe/Thermistor
Interface Module's thermistor inputs 1 and 2, six times over, and B21 read
after each (`transcripts/thermistor*.log`). The replay puts the same
resistances on this console's inputs and compares every row. The rest of
the file needs no capture: an open input, one input asked for, the counter
starting again, the computer form, and the resistors surviving a restart.
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_controls import send                              # noqa: E402
from tls350sim import groundtemp, packed                          # noqa: E402
from tls350sim.console import Console                             # noqa: E402
from tls350sim.wire import Handler                                # noqa: E402

T = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "console_capture", "bench-2026-09-18", "transcripts")

# What was on inputs 1 and 2 for each log, as metered; 3 and 4 were open.
WIRED = {"thermistor": {1: 9710.0, 2: 469.0},
         "thermistor2": {1: 4670.0, 2: 469.0},
         "thermistor3": {1: 21730.0, 2: 469.0},
         "thermistor4": {1: 99500.0, 2: 469.0},
         "thermistor5": {1: 991.0, 2: 473000.0},
         "thermistor6": {1: 9995.0, 2: 1000000.0}}

ROW = re.compile(r"^ +(\d) +(\d+) +(\d+) +(\d+) +(\d+)$")


def _body(handler, command):
    return (send(handler, command).replace(chr(1), "").replace(chr(3), "")
            .strip(chr(13) + chr(10)))


def _rows(text):
    return [tuple(int(x) for x in m.groups())
            for m in map(ROW.match, text.splitlines()) if m]


def a_gt_console():
    c = Console(None)
    c.probe_gt = True
    return c, Handler(c, verbose=False)


@unittest.skipUnless(os.path.exists(os.path.join(T, "thermistor6.log")),
                     "the bench transcripts are not in this checkout")
class TheBenchResistors(unittest.TestCase):

    def test_every_row_reads_as_the_bench_read_it(self):
        compared = 0
        for name, wired in sorted(WIRED.items()):
            with open(os.path.join(T, name + ".log"), encoding="utf-8") as fh:
                bench = [r for r in _rows(fh.read()) if r[1] == 50]
            c, h = a_gt_console()
            for n, ohms in wired.items():
                c.thermistors[n] = ohms
            ours = {r[0]: r for r in _rows(_body(h, "IB2100"))}
            self.assertEqual(sorted(ours), [1, 2, 3, 4])
            for n, count, high, low, value in bench:
                mine = ours[n]
                self.assertEqual(mine[1], count, (name, n))
                self.assertIn(high, (mine[2], mine[2] + 1), (name, n))
                self.assertIn(low, (mine[3], mine[3] + 1), (name, n))
                if n in wired:
                    # the 470 drifted 373 -> 364 over twenty minutes
                    self.assertAlmostEqual(mine[4] / value, 1.0, delta=0.025,
                                           msg=(name, n, mine, value))
                else:
                    self.assertEqual(mine[4], value, (name, n))
                compared += 1
        self.assertGreaterEqual(compared, 34)


class TheInputs(unittest.TestCase):

    def test_an_open_input_reads_a_thousand_million(self):
        _c, h = a_gt_console()
        rows = _rows(_body(h, "IB2100"))
        self.assertEqual(rows, [(n, 50, 966, 199, 1000000000)
                                for n in range(1, 5)])

    def test_one_input_asked_for_answers_alone(self):
        """`IB2101` answered input 1's row and nothing else."""
        c, h = a_gt_console()
        c.thermistors[1] = 9710.0
        rows = _rows(_body(h, "IB2101"))
        self.assertEqual([r[0] for r in rows], [1])
        self.assertEqual(rows[0][4], 9577)

    def test_the_counter_starts_again_when_an_input_changes(self):
        c, h = a_gt_console()
        c.wire_thermistor(2, 473000.0)
        self.assertLess(_rows(_body(h, "IB2102"))[0][1], 50)
        c.thermistor_since[2] -= 60
        self.assertEqual(_rows(_body(h, "IB2102"))[0][1], 50)
        c.wire_thermistor(2, None)
        self.assertEqual(_rows(_body(h, "IB2102"))[0][4], 1000000000)

    def test_the_computer_form_is_five_floats_an_input(self):
        """Counter, high, low, the last sample and the average; the average
        is what the display prints, and the last sits a hair off it."""
        c, h = a_gt_console()
        c.thermistors[1] = 99500.0
        reply = send(h, "iB2100")
        data = reply.split("iB2100")[-1][10:].split("&&")[0]
        self.assertEqual(len(data), 4 * (4 + 5 * 8))
        one = data[:44]
        self.assertEqual(one[:4], "0105")
        fields = [packed.unhexfloat(one[4 + 8 * i:12 + 8 * i])
                  for i in range(5)]
        self.assertEqual(fields[:3], [50.0, 966.0, 199.0])
        self.assertAlmostEqual(fields[4], 102592.0, delta=1.0)
        self.assertNotEqual(fields[3], fields[4])
        self.assertAlmostEqual(fields[3] / fields[4], 1.0, delta=0.001)
        self.assertEqual(data[44:48], "0205")
        self.assertEqual(packed.unhexfloat(data[80:88]), 1e9)

    def test_the_curve_passes_through_every_measured_point(self):
        for ohms, value in groundtemp.MEASURED:
            self.assertAlmostEqual(groundtemp.read_as(ohms), value, places=3)

    def test_a_vlld_site_has_its_thermistor_on_input_one(self):
        """576013-879 Rev W p.60; inputs 2 to 4 stay open."""
        c, h = a_gt_console()
        c.set_module("vlld", 1)
        rows = _rows(_body(h, "IB2100"))
        self.assertLess(rows[0][4], 1000000)
        self.assertEqual([r[4] for r in rows[1:]], [1000000000] * 3)

    def test_the_resistors_are_hardware(self):
        """They survive a save and load, and a cold boot."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            c = Console(path)
            c.probe_gt = True
            c.wire_thermistor(1, 21730.0)
            c.save()
            again = Console(path)
            self.assertEqual(again.thermistors, {1: 21730.0})
            again.cold_boot()
            self.assertEqual(again.thermistors, {1: 21730.0})


if __name__ == "__main__":
    unittest.main()
