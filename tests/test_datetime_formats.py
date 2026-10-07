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
"""The six DATE/TIME FORMATs (50F), as the bench TLS-350 prints each.

On 2026-09-19 the bench console was set to each format in turn and asked
the codes that carry a date (`transcripts/datefmt.log`, 20:29 to 20:30 on
its clock; the alarm raised at 8:10 in the morning). The strings below are
its own, copied out of the replies, so this runs without the transcript: a
reply's stamp, the display mirror's clock with seconds, and an alarm row's
DATE and TIME in the active alarm report (113) and the history (111).
"""
import contextlib
import os
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console                       # noqa: E402

# format: (reply stamp at 20:29, glass at 20:29:57, alarm at 08:10 in 113,
#          the same in 111)
BENCH = {
    "01": ("JAN 16, 2006  8:29 PM", "JAN 16, 2006  8:29:57 PM",
           " 1-16-06  8:10AM", " 1-16-06  8:10AM"),
    "02": ("JAN 16, 2006 20:29", " JAN 16, 2006 20:29:57  ",
           "JAN 16, 2006  8:10", "JAN 16 2006  8:10"),
    "03": ("    01-16-06  8:29 PM", "  01-16-06  8:29:57 PM  ",
           "01-16-06  8:10 AM", "01-16-06  8:10 AM"),
    "04": ("    01-16-06 20:29", "   01-16-06 20:29:57    ",
           "01-16-06  8:10", "01-16-06  8:10"),
    "05": ("    16-01-06 20:29", "   16-01-06 20:29:57    ",
           "16-01-06  8:10", "16-01-06  8:10"),
    "06": ("    06-01-16 20:29", "   06-01-16 20:29:57    ",
           "06-01-16  8:10", "06-01-16  8:10"),
}


@contextlib.contextmanager
def at(c, when):
    held = time.time()
    with unittest.mock.patch("time.time", lambda: held):
        c.clock_offset = time.mktime(when + (0, 0, -1)) - held
        c._last_tick = held
        yield


class EachFormatAsTheBenchPrintsIt(unittest.TestCase):

    def setUp(self):
        self.c = Console(None)

    def test_the_stamp_the_glass_and_the_alarm_rows(self):
        from tls350sim.wire import Handler
        morning = time.strptime("0601160810", "%y%m%d%H%M")
        for fmt, (stamp, glass, active, history) in BENCH.items():
            with self.subTest(format=fmt):
                self.c.values["S50F00"] = fmt
                with at(self.c, (2006, 1, 16, 20, 29, 57)):
                    self.assertEqual(self.c.clock_stamp(), stamp)
                    reply = Handler(self.c, verbose=False).handle(
                        b"\x01I5FA00").decode("latin-1")
                    self.assertIn(glass, reply)
                self.assertEqual(self.c.alarm_when(morning), active)
                self.assertEqual(self.c.alarm_when(morning, history=True),
                                 history)

    def test_a_new_format_is_answered_under_the_old_stamp(self):
        """`S50F0002` came back under `JAN 16, 2006  8:29 PM`, the format
        it was replacing, and the next reply under `JAN 16, 2006 20:29`."""
        from tls350sim.wire import Handler
        h = Handler(self.c, verbose=False)
        with at(self.c, (2006, 1, 16, 20, 29, 0)):
            self.assertIn(b"JAN 16, 2006  8:29 PM", h.handle(b"\x01S50F0002\r"))
            self.assertIn(b"JAN 16, 2006 20:29\r\n", h.handle(b"\x01I50F00"))


if __name__ == "__main__":
    unittest.main()
