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
"""The emulator, against the bench TLS-350 as it came out of a cold start.

`test_console_capture.py` compares against the same console with an EMPTY
cage on the emulator's side, so everything about a card was a "fixture
difference" there and never compared. This one fits the bench console's own
cage -- the 4 PROBE / G.T. board, the PLLD sensor and power boards, an RS-232
and a serial satellite board -- its S-Module's keys (CSLD, 0.10 and 0.20
Repetitive) and its software (3XX, version 26), pins the clock to the moment
the console came back from its cold start, and compares every report body.

The capture was taken on 2026-09-18, a few minutes after the console was cold
started: its clock reads JAN 16, 2006 8:00 AM, which is the firmware's own
CREATED date. It is not committed -- it is the console's own output -- so
this skips without it. `BENCH_CAPTURE` points somewhere else if wanted; the
default is `tests/console_capture/bench-2026-09-18/cap_coldstart`, and the
refusals come from the full census in `cap_unprogrammed` beside it, because
which codes a console refuses is decided by its cage and not its settings.
"""
import contextlib
import os
import re
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "console_capture", "bench-2026-09-18")
CAPTURE = os.environ.get("BENCH_CAPTURE") or os.path.join(BENCH,
                                                          "cap_coldstart")
CENSUS = os.path.join(BENCH, "cap_unprogrammed")

SOH, ETX = b"\x01", b"\x03"
STAMP = re.compile(r"^[A-Z?]{3} [ 0-9]?\d, \d{4} [ 0-9]?\d:\d\d [AP]M$")

# 2006-01-16 08:00, the instant the bench console's clock restarted from
COLD_START = (2006, 1, 16, 8, 0, 0)


def have():
    return os.path.isdir(os.path.join(CAPTURE, "raw"))


def lines(data):
    """The body: every line after the echoed code and the stamp."""
    out = data.replace(ETX, b"").replace(SOH, b"").decode(
        "latin-1").split("\r\n")
    while out and not out[0].strip():
        out.pop(0)
    if out:
        out.pop(0)
    if out and STAMP.match(out[0].strip()):
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return [one.rstrip() for one in out]


def answered(data):
    return bool(data) and b"9999FF" not in data and len(data) >= 16


def bench_console():
    """The bench console's cage, keys and software, nothing programmed."""
    from tls350sim.console import Console
    c = Console(None)
    for key in list(c.modules):
        c.modules[key] = 0
    c.modules.update({"probe": 1, "plld": 1, "plldctl": 1, "rs232": 1,
                      "ssat": 1})
    c.probe_gt = True
    for key in list(c.software):
        c.software[key] = False
    c.software.update({"csld": True, "plld010": True, "plld020": True})
    c.version, c.board = 26, "E5"
    # booted on the software it runs, so no ROM REVISION WARNING
    c.rom_at_boot = c.version
    # the roll was taken out before the cold start; PAPER OUT stands
    c.out_of_paper = True
    # and nothing is wired to the PLLD card's transducer inputs
    for n in range(1, 4):
        c.lines.line("plld", n).transducer = "open"
    return c


@contextlib.contextmanager
def at_cold_start(c):
    """The wall clock held, and the console's clock at COLD_START."""
    held = time.time()
    with unittest.mock.patch("time.time", lambda: held):
        target = time.mktime(COLD_START + (0, 0, -1))
        c.clock_offset = target - held
        c._last_tick = held
        yield


def real(code):
    path = os.path.join(CAPTURE, "raw", code + ".bin")
    with open(path, "rb") as fh:
        return fh.read()


def pairs(handler):
    """(code, the console's body, ours) for every code the console answered."""
    for name in sorted(os.listdir(os.path.join(CAPTURE, "raw"))):
        code = name[:-4]
        data = real(code)
        if not answered(data):
            continue
        yield code, body(data), body(handler.handle(SOH + code.encode()))


def body(data):
    """`lines()`, but a refusal is not the empty body a bare frame has:
    both came back as [] from `lines()`, and 210 refusals here passed as
    the bench's bare frames (2026-09-18, FIDELITY S33)."""
    return ["9999FF"] if b"9999FF" in data else lines(data)


def refused_by_the_bench():
    """Every code the full census had refused on this cage."""
    raw = os.path.join(CENSUS, "raw")
    if not os.path.isdir(raw):
        return []
    out = []
    for name in sorted(os.listdir(raw)):
        with open(os.path.join(raw, name), "rb") as fh:
            data = fh.read()
        if data and b"9999FF" in data:
            out.append(name[:-4])
    return out


@unittest.skipUnless(have(), "no bench capture in %s" % CAPTURE)
class TheBenchConsoleFromColdStart(unittest.TestCase):
    """Every report the cold-started bench console answered, body for body."""

    #: What matched when this was last measured. A floor, never a target.
    MATCHED = 482

    #: The reports that still differ, each for a reason that is not a defect
    #: this test can see, and the one code the emulator answers where the
    #: bench refuses. Both directions are held: a code that starts matching
    #: comes off the list in the same commit. See FIDELITY S29.
    KNOWN = {
        # the cards' own resistances, measured at power-on and now: another
        # console's cards, and this one simulates its own
        "I10200",
        # the BATTERY IS OFF alarm and clear the cold-start PROCEDURE logs --
        # the battery switch off and on again -- which a console built in a
        # test has not been through
        "I11100", "I11400",
        # the display to the second, taken five minutes into the cold start
        "I5FA00",
        # the PC diagnostic's counters, which are the bench's own
        # history (the chip's identity on I90200 and I90500 matches since
        # 2026-09-19)
        "I90300",
    }
    #: answered here and refused by the bench. 78B, a Version 16 0.10 GPH
    #: schedule date, was the one, and is refused now with WPLLD's 7AA on
    #: this software (`Handler.WITHHELD`) for a reason no page gives
    ANSWERED_WHERE_REFUSED = set()

    def test_the_ones_left_are_the_known_ones(self):
        wrong = {code for code, a, b in self.results if a != b}
        self.assertEqual(sorted(wrong - self.KNOWN), [],
                         "a report reads differently on the bench console")
        self.assertEqual(sorted(self.KNOWN - wrong), [],
                         "these match now: take them off KNOWN")

    def test_what_the_bench_refuses_is_refused(self):
        from tls350sim.wire import Handler
        c = bench_console()
        h = Handler(c, verbose=False)
        with at_cold_start(c):
            extra = {code for code in refused_by_the_bench()
                     if answered(h.handle(SOH + code.encode()))}
        self.assertEqual(extra, self.ANSWERED_WHERE_REFUSED)

    @classmethod
    def setUpClass(cls):
        from tls350sim.wire import Handler
        cls.console = bench_console()
        cls.handler = Handler(cls.console, verbose=False)
        with at_cold_start(cls.console):
            cls.results = list(pairs(cls.handler))

    def test_the_measure_does_not_fall(self):
        same = sum(1 for _c, a, b in self.results if a == b)
        self.assertGreaterEqual(same, self.MATCHED,
                                "fewer reports match the bench than did")


def main():
    """Print the comparison, for working through it by hand."""
    import difflib
    from tls350sim.wire import Handler
    c = bench_console()
    h = Handler(c, verbose=False)
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    show = "-v" in sys.argv
    same = differ = 0
    with at_cold_start(c):
        results = list(pairs(h))
        refused = refused_by_the_bench()
        extra = [code for code in refused
                 if answered(h.handle(SOH + code.encode()))]
    for code, a, b in results:
        if only and not any(code.startswith(o) for o in only):
            continue
        if a == b:
            same += 1
            continue
        differ += 1
        print("DIFF", code)
        if show:
            for one in difflib.unified_diff(a, b, "bench", "ours",
                                            lineterm="", n=1):
                print("   " + one)
    print("same %d, differ %d; answered where the bench refuses: %d %s"
          % (same, differ, len(extra), " ".join(extra[:40])))


if __name__ == "__main__":
    main()
