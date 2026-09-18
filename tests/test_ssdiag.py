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
'''SS COMM DIAG, SS CONSTANTS DIAG and SS CHANNEL DIAG: an ISD sensor's three
diagnostic printouts, 577013-800 Rev P p.20-44. They were the generic sensor
status report. FIDELITY I11, UNKNOWNS A56.'''
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import printer, wiresensors
from tls350sim.console import Console


def an_isd_sensor(category="02"):
    c = Console()
    c.modules["rs232"] = 1
    c.set_module("smart", 1)
    c.values["S72301"] = "01" + category
    c.values["S72201"] = "01AFM1   FP1-2".ljust(22)
    return c


class TheThreePrintouts(unittest.TestCase):

    def test_comm_diag_is_the_pages_six_counters(self):
        lines = printer.ss_comm_diag(an_isd_sensor(), 1)
        at = lines.index("SS COMM DIAG")
        self.assertEqual(lines[at + 1], "-" * 23)
        self.assertEqual(lines[at + 2], "s 1: AFM1   FP1-2")
        self.assertEqual(lines[at + 3:at + 9], [
            "SAMPLES READ     0", "SAMPLES USED     0",
            "PARITY ERR       0", "PARTIAL READ     0",
            "COMM ERR         0", "RESTARTS         0"])

    def test_a_sensor_that_is_not_answering_is_losing_samples(self):
        c = an_isd_sensor()
        c.sensor_state[("smart", "1")] = "comm"
        read, used, _parity, _partial, comm, _restarts = (
            wiresensors.ss_comm_counts(c, 1))
        self.assertEqual(comm, 1)
        self.assertLessEqual(used, read)

    def test_constants_diag_names_the_sensor_it_is(self):
        lines = printer.ss_constants_diag(an_isd_sensor(), 1)
        at = lines.index("SS CONSTANTS DIAG")
        self.assertEqual(lines[at + 3], "")
        self.assertEqual(lines[at + 4], "VAPOR PRESSURE")
        self.assertTrue(lines[at + 5].startswith("SERIAL NUMBER"))
        self.assertEqual(len(lines[at + 5]), 22)
        self.assertEqual(len(lines[at + 6]), 22)
        self.assertIn("AIR FLOW METER",
                      printer.ss_constants_diag(an_isd_sensor("01"), 1))

    def test_channel_diag_is_six_rows_of_four_words(self):
        c = an_isd_sensor()
        lines = printer.ss_channel_diag(c, 1)
        at = lines.index("SS CHANNEL DIAG")
        self.assertRegex(lines[at + 3],
                         r"^[0-9]{2}-[0-9]{2}-[0-9]{2}  [0-9]{2}:[0-9]{2}:[0-9]{2}$")
        rows = lines[at + 4:at + 10]
        self.assertEqual([r[:3] for r in rows],
                         ["C00", "C04", "C08", "C12", "C16", "C20"])
        for row in rows:
            self.assertRegex(row, r"^C[0-9]{2}( [0-9A-F]{4}){4}$")
        first = int(wiresensors._float(wiresensors._channels(c, 1)[0]), 16)
        self.assertEqual(rows[0][4:13],
                         f"{first >> 16:04X} {first & 0xFFFF:04X}")


if __name__ == "__main__":
    unittest.main()
