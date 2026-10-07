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
"""The PLLD sensor board's A/D, against resistors on the bench TLS-350.

On 2026-09-19 resistors from 470 ohms to 10k were wired on the bench's PLLD
transducer inputs Q1 to Q3 and `iB8100` read after each settled
(`transcripts/plld*.log`). Its four floats are, by 576013-635 Rev AA p.553,
the pressure, the low and high reference counts and the sensor counts.
Q1 carried a PRESSURE OFFSET (77D) of 5.0 psi, Q2 and Q3 none. Every point
is written down here, so this needs no capture.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_pressure import a_line                            # noqa: E402
from tls350sim import pressure                                    # noqa: E402

# line, ohms, sensor counts, psi, 77D offset
BENCH = [(1, 2200, 1693.12, 1.46, 5.0),
         (1, 1000, 701.38, 23.40, 5.0),
         (1, 1000, 445.88, 44.85, 5.0),    # read while the 1k settled
         (1, 680, 499.00, 38.59, 5.0),
         (2, 470, 410.12, 54.99, 0.0),
         (2, 1500, 1085.50, 15.15, 0.0),
         (3, 2200, 1714.88, 6.27, 0.0)]


def psi_from(counts, offset):
    lo, hi = pressure.COUNTS_LO, pressure.COUNTS_HI
    return 50.0 * (1 / counts - 1 / lo) / (1 / hi - 1 / lo) - offset


class TheCountsAreTheBenchs(unittest.TestCase):

    def test_the_references_are_the_benchs(self):
        """2902.2 to 2902.3 and 444.7 to 444.8 in every read, whatever was
        wired. Figure 6-12's 32768 and 8192 are a typeset sample."""
        self.assertAlmostEqual(pressure.COUNTS_LO, 2902.3, delta=0.1)
        self.assertAlmostEqual(pressure.COUNTS_HI, 444.8, delta=0.1)

    def test_every_bench_point_fits_the_reciprocal_scale(self):
        for line, ohms, counts, psi, offset in BENCH:
            self.assertAlmostEqual(psi_from(counts, offset), psi, delta=0.01,
                                   msg=(line, ohms, counts))

    def test_a_line_at_that_pressure_reads_those_counts(self):
        for line, ohms, counts, psi, offset in BENCH:
            c = a_line()
            c.values["S77D01"] = "01" + struct.pack(">f", offset).hex().upper()
            ln = c.lines.line("plld", 1)
            ln.pressure = psi
            lo, hi, got = ln.sensor_counts()
            self.assertLess(hi, lo)
            self.assertAlmostEqual(got / counts, 1.0, delta=0.001,
                                   msg=(line, ohms, psi, got))

    def test_no_transducer_reads_minus_one(self):
        """`lineon.log`: Q1 switched on with nothing wired packed -1.0 as
        its sensor counts."""
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.transducer = "open"
        self.assertEqual(ln.sensor_counts()[2], -1.0)

    def test_fifty_psi_is_the_high_reference(self):
        c = a_line()
        ln = c.lines.line("plld", 1)
        ln.pressure = 50.0
        self.assertAlmostEqual(ln.sensor_counts()[2], pressure.COUNTS_HI,
                               places=6)
        ln.pressure = 0.0
        self.assertAlmostEqual(ln.sensor_counts()[2], pressure.COUNTS_LO,
                               places=6)


if __name__ == "__main__":
    unittest.main()
