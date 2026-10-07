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
"""The active alarm reports, 113 and 121, with alarms switched on and off.

On 2026-09-25 the bench TLS-350's four tanks and Q2 were switched on and off
over the wire, and 113 and 121 read in both formats between the Sets
(`bench-2026-09-25/read.jsonl`, `p121.jsonl`, `order.jsonl`). Each log is
replayed through a console loaded from the 09-24 snapshot, every command at
the bench's own minute, and every reply to 113 and 121 has to agree. The
same day's samples S38 waited for (`SAMPLES`) are replayed whole. It skips
without the logs. FIDELITY S38.
"""
import json
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_swept import display_body, framed, packed_body       # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "console_capture")
DAY = os.path.join(BENCH, "bench-2026-09-25")
SNAPSHOT = os.path.join(BENCH, "bench-2026-09-24", "snapshot.vrset")
LOGS = ("read.jsonl", "p121.jsonl", "order.jsonl")
# the samples S38 waited on, every reply of which is compared: 639's four
# shapes and 62D's ENABLED on tank 4, and a 5E2 record enabled, under three
# DATE/TIME FORMATs; a dump's empty lists sent back, refused (test_formats);
# and Q3 switched on, off and muted: its warning goes, Q1's PLLD OPEN stays
SAMPLES = ("samples.jsonl", "samples5e2.jsonl", "fmt5e2.jsonl",
           "emptylists.jsonl", "q3.jsonl")
STAMP = re.compile(r"JAN (\d+), 2006 +(\d+):(\d\d) ([AP]M)")


def have():
    return os.path.exists(SNAPSHOT) and all(
        os.path.exists(os.path.join(DAY, log)) for log in LOGS + SAMPLES)


def replay(log, codes=("113", "121")):
    """[(code, the bench's body, ours)] for every inquiry in `log` whose
    code is in `codes`, or every one when `codes` is None."""
    from tls350sim import sitebackup, wire
    from tls350sim.console import Console
    c = Console(os.path.join(tempfile.mkdtemp(), "state.json"))
    sitebackup.load(c, SNAPSHOT)
    # nothing is wired to the PLLD card's transducer inputs (FIDELITY S34)
    for n in range(1, 4):
        c.lines.line("plld", n).transducer = "open"
    h = wire.Handler(c, verbose=False)
    out = []
    with open(os.path.join(DAY, log), encoding="utf-8") as fh:
        steps = [json.loads(line) for line in fh]
    for step in steps:
        cmd, real = step["cmd"], step["reply"].encode("latin-1")
        at = None
        if cmd[0] == "i" and step["reply"][7:17].isdigit():
            at = time.strptime(step["reply"][7:17], "%y%m%d%H%M")
        elif STAMP.search(step["reply"]):
            at = time.strptime("06 01 %s %s %s %s"
                               % STAMP.search(step["reply"]).groups(),
                               "%y %m %d %I %M %p")
        if at:
            c.clock_offset = time.mktime(
                at[:5] + (30,) + at[6:8] + (-1,)) - time.time()
        if cmd[0] in "Ss":
            framed(h, b"\x01" + cmd.encode("latin-1") + b"\r")
            # the running console's next look, which the bench took inside
            # the second (FIDELITY S35's timing)
            c.compute_alarms()
            continue
        ours = framed(h, b"\x01" + cmd.encode("latin-1"))
        if codes is not None and cmd[1:4] not in codes:
            continue
        if cmd[1:4] == "5FA":
            # the glass to the second, which the replay's minute cannot hold
            continue
        body = packed_body if cmd[0] == "i" else display_body
        out.append((cmd, body(real), body(ours)))
    return out


@unittest.skipUnless(have(), "the 2026-09-25 bench logs are not here")
class TheActiveAlarmReports(unittest.TestCase):

    def test_every_113_and_121_agrees(self):
        for log in LOGS:
            rows = replay(log)
            self.assertTrue(rows, log)
            for n, (code, real, ours) in enumerate(rows):
                with self.subTest(log=log, step=n, code=code):
                    self.assertEqual(ours, real)

    def test_every_reply_to_the_samples_agrees(self):
        for log in SAMPLES:
            rows = replay(log, None)
            self.assertTrue(rows, log)
            for n, (code, real, ours) in enumerate(rows):
                with self.subTest(log=log, step=n, code=code):
                    self.assertEqual(ours, real)


class TheRecordByHand(unittest.TestCase):
    """What the logs showed, held without them."""

    def setUp(self):
        from tls350sim.console import Console
        self.c = Console(os.path.join(tempfile.mkdtemp(), "state.json"))

    def test_nothing_standing_is_an_index_of_00(self):
        self.assertEqual(self.c.alarm_121_records()[-3:], "000")

    def test_a_latched_alarm_is_on_the_glass_and_not_on_113(self):
        c = self.c
        c._seen, c.latched = {"020101"}, {"020101"}
        c.conditions = lambda: []
        self.assertIn("020101", c.compute_alarms())
        self.assertEqual(c.active_alarm_records(), [])

    def test_113_orders_by_category_then_device(self):
        c = self.c
        c.conditions = lambda: ["210102", "020901", "210601", "020101"]
        c.reduce_alarms = set
        c.latches = lambda rec: False
        self.assertEqual([r["aa"] + r["nn"] + r["tt"]
                          for r in c.active_alarm_records()],
                         ["020101", "020901", "210601", "210102"])

    def test_a_remote_alarm_reset_clears_the_latched_and_not_an_open(self):
        """S00300 took Q3's cleared SETUP DATA WARNING off the bench's
        glass, and left Q1's PLLD OPEN ALARM standing (`q3.jsonl`)."""
        from tls350sim import wire
        c = self.c
        c.posted = {"210601"}
        c.latched = {"210601", "210103"}
        framed(wire.Handler(c, verbose=False), b"\x01S00300\r")
        self.assertIn("210601", c.compute_alarms())
        self.assertNotIn("210103", c.compute_alarms())

    def test_a_passing_line_test_takes_down_a_carried_open(self):
        c = self.c
        c.post("21", "06", 1)
        c.leaks.record_line("plld", 1, "gross", True, 0.0, time.time())
        self.assertNotIn("210601", c.posted)

    def test_an_inventory_record_time_prints_in_twelve_hours(self):
        from tls350sim import wire
        h = wire.Handler(self.c, verbose=False)
        for held, shown in (("1430", " 2:30 PM"), ("0005", "12:05 AM"),
                            ("EE00", "DISABLED")):
            self.c.values["S5E201"] = held
            self.assertIn(f"RECORD 1 : {shown}\r\n",
                          framed(h, b"\x01I5E201").decode("latin-1"))


if __name__ == "__main__":
    unittest.main()
