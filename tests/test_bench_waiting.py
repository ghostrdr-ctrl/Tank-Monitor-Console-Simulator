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
"""What a restore of the bench's own dump, and a CR after an inquiry, found.

On 2026-09-22 the bench TLS-350's snapshot was restored onto this console
with the Multitool (`restore --commit --post-backup`) and read back: 1,261
records came back as they went in, and the rest were this console answering
positions the bench has not got, packing a record the bench does not, or
refusing the bench's own values. And four codes the dump read were on no
capture: the census had sent every inquiry bare, and 36 of them wait for a
terminator (`wire.WAITING_INQUIRIES`). Those were each sent again with a CR
after them, at 00 and at 01 (`bench-2026-09-22/waitcr*.jsonl`).

The first class replays those probes through the framer, and skips without
them. The rest need nothing: they are what the bench answered, by hand.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refusals import is_bare                                          # noqa
from test_bench_console import bench_console                         # noqa
from test_bench_swept import display_body, packed_body               # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = os.path.join(HERE, "console_capture", "bench-2026-09-22")
PROBES = [os.path.join(DAY, f"waitcr{n}.jsonl") for n in ("", "01")]


def probes():
    """Every probe record, 00 and 01."""
    out = []
    for path in PROBES:
        with open(path, encoding="utf-8") as fh:
            out += [json.loads(line) for line in fh]
    return out


def ask(h, cmd):
    return h.handle(b"\x01" + cmd.encode("latin-1")).decode("latin-1")


def data_of(reply):
    """A packed reply's data: after the echo and the stamp, before &&."""
    return reply[1 + 6 + 10:reply.index("&&")]


@unittest.skipUnless(all(os.path.exists(p) for p in PROBES)
                     and os.path.exists(os.path.join(DAY, "snapshot.vrset")),
                     "the 2026-09-22 bench probes are not here")
class AnInquiryThatWaitsForItsData(unittest.TestCase):

    def test_the_framer_waits_where_the_bench_did(self):
        from tls350sim import wire
        for r in probes():
            sent = b"\x01" + r["code"].encode()
            with self.subTest(code=r["code"]):
                self.assertEqual(wire._complete(sent) == 0, not r["bare"])
                # and a CR finishes it
                self.assertEqual(wire._complete(sent + b"\r"), len(sent) + 1)

    def test_what_a_lone_cr_is_answered_with(self):
        from tls350sim import sitebackup, wire
        from tls350sim.console import Console
        c = Console(os.path.join(tempfile.mkdtemp(), "s.json"))
        sitebackup.load(c, os.path.join(DAY, "snapshot.vrset"))
        h = wire.Handler(c, verbose=False)
        for r in probes():
            bench = (r["bare"] or r["after_cr"]).encode("latin-1")
            ours = h.handle(b"\x01" + r["code"].encode() + b"\r")
            body = packed_body if r["code"][0] == "i" else display_body
            with self.subTest(code=r["code"]):
                if not bench:
                    self.assertEqual(ours, b"")
                else:
                    self.assertEqual(body(ours), body(bench))


class APositionPastTheEnd(unittest.TestCase):
    """Header line 5 of 4, receiver 9 of 8, security port 7 of 6, a comm port
    with no board and a fourth pressure line: the frame alone, in both
    formats, on the bench (2026-09-22)."""

    def test_each(self):
        from tls350sim import wire
        h = wire.Handler(bench_console(), verbose=False)
        for code in ("50305", "52009", "52209", "53607", "5BC09", "53509",
                     "88103", "88503", "88903", "7BD04", "77F04"):
            for letter in "Ii":
                with self.subTest(code=letter + code):
                    self.assertTrue(is_bare(ask(h, letter + code)))

    def test_the_last_one_is_still_there(self):
        from tls350sim import wire
        h = wire.Handler(bench_console(), verbose=False)
        for code in ("50304", "52008", "53606", "88102", "7BD03"):
            with self.subTest(code=code):
                self.assertFalse(is_bare(ask(h, "i" + code)))


class WhatARestoreCameBackWith(unittest.TestCase):
    """Records the Multitool read back from this console after restoring the
    bench's dump onto it, each as the bench packs it (2026-09-22)."""

    def setUp(self):
        from tls350sim import wire
        self.c = bench_console()
        self.h = wire.Handler(self.c, verbose=False)

    def test_5e2_has_no_device_prefix(self):
        ask(self.h, "s5E201EE00")
        self.assertEqual(data_of(ask(self.h, "i5E201")), "EE00")

    def test_882_packs_the_port_s_881_record(self):
        self.assertEqual(data_of(ask(self.h, "i88201")),
                         data_of(ask(self.h, "i88101")))

    def test_881_packs_the_defaults_its_display_reads(self):
        """1200 baud and one ring, where this packed 300 and none."""
        record = data_of(ask(self.h, "i88101"))
        self.assertEqual((record[:5], record[9:11]), ("01200", "01"))
        self.assertIn(" BAUD RATE  : 1200", ask(self.h, "I88101"))

    def test_a_line_with_no_second_length_packs_zero(self):
        self.assertEqual(data_of(ask(self.h, "i77F01")), "01" + "0" * 8)

    def test_a_receiver_s_hangup_is_its_own_record(self):
        self.assertEqual(data_of(ask(self.h, "i53501")), "0100")

    def test_one_thermistor_input_is_its_own_block(self):
        whole = data_of(ask(self.h, "iB2100"))
        one = data_of(ask(self.h, "iB2102"))
        self.assertTrue(one.startswith("0205"))
        self.assertIn(one[:4], whole)


class TheMeterMapWasWaiting(unittest.TestCase):
    """`i7B100` was filed as the one code the bench answers with nothing:
    it waits for a terminator, and then packs `+99` (no BIR key)."""

    def test_it(self):
        from tls350sim import wire
        h = wire.Handler(bench_console(), verbose=False)
        self.assertEqual(wire._complete(b"\x01i7B100"), 0)
        self.assertEqual(data_of(ask(h, "i7B100\r")), "+99")
        # its display form answers at once, as it always did
        self.assertEqual(wire._complete(b"\x01I7B100"), 7)


class ATankSwitchedOnWithNoProbe(unittest.TestCase):
    """FIDELITY S35, with 2026-09-24's tank 1: switched on with no probe on
    an armed console, SETUP DATA WARNING and PROBE OUT at once, both in I205;
    none out of a cold start until the checks are armed."""

    def a_console(self):
        c = bench_console()
        c.values["S60201"] = "01REGULAR UNLEADED    "
        c.values["S60701"] = "0142C00000"          # 96 inches
        c.values["S60401"] = "01461C4000"          # 10000 gallons
        return c

    def alarms(self, c):
        return sorted(a for a in c.compute_alarms() if a[:2] == "02")

    def test_armed_it_posts_both_and_205_lists_them(self):
        from tls350sim import wire
        c = self.a_console()
        c.tank_checks = True
        h = wire.Handler(c, verbose=False)
        h.handle(b"\x01S601011\r")
        self.assertEqual(self.alarms(c), ["020101", "020901"])
        shown = h.handle(b"\x01I20500").decode("latin-1")
        self.assertIn("  1    REGULAR UNLEADED        SETUP DATA WARNING \r\n"
                      + " " * 31 + "PROBE OUT          ", shown)
        # and switched off, the condition goes (what stays on the glass until
        # ALARM/TEST is the latch's business)
        h.handle(b"\x01S601010\r")
        self.assertEqual([a for a in c.conditions() if a[:2] == "02"], [])

    def test_out_of_a_cold_start_it_posts_nothing_until_armed(self):
        from tls350sim import wire
        c = self.a_console()
        c.cold_boot()
        c.values["S60201"] = "01REGULAR UNLEADED    "
        c.values["S60701"] = "0142C00000"
        c.values["S60401"] = "01461C4000"
        h = wire.Handler(c, verbose=False)
        h.handle(b"\x01S601011\r")
        self.assertEqual(self.alarms(c), [])
        # a clock Set is one of the three things that arm them
        h.handle(b"\x01S501000601240300\r")
        self.assertEqual(self.alarms(c), ["020101", "020901"])


class AStandingPlldOpenIsAnOpenTransducer(unittest.TestCase):
    """The bench's snapshot, taken with Q1 off, held Q1's PLLD OPEN ALARM
    standing; switched on, Q1 tested TEST ABORTED as an open transducer
    does. An imported standing 21/06 opens that line's transducer."""

    def test_it(self):
        from tls350sim import sitebackup
        from tls350sim.console import Console
        path = os.path.join(tempfile.mkdtemp(), "site.vrset")
        rows = ["ACTIVE ALARMS REPORT",
                "ID  CATEGORY  DESCRIPTION          ALARM TYPE"
                "             DATE    TIME",
                "Q 1  OTHER    REG TURBINE          PLLD OPEN ALARM      "]
        raw = ("\x01\r\nI11300\r\nJAN 24, 2006  2:52 AM\r\n\r\n"
               + "\r\n".join(rows) + "\r\n\r\n\x03").encode("latin-1")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# VRSET v1 console=350 format=computer snapshot=1\n")
            fh.write("S78101\t" + "011".encode().hex() + "\n")
            fh.write("#R BEGIN 113 data Alarms|Active Alarms\n")
            fh.write("#R X " + raw.hex() + "\n")
            fh.write("#R END 113\n")
        c = Console(os.path.join(tempfile.mkdtemp(), "s.json"))
        c.modules.update({"plld": 1, "plldctl": 1})
        sitebackup.load(c, path)
        self.assertEqual(c.lines.line("plld", 1).transducer, "open")


if __name__ == "__main__":
    unittest.main()
