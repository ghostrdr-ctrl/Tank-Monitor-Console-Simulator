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
"""The bench TLS-350's own site snapshot, loaded, and every reply compared.

On 2026-09-22 the bench console was dumped with its reports
(`snapshot.vrset`, Multitool `dump --computer --with-reports`) and then asked,
read only, every code it answers in both formats: at device 00 (`cap_now`,
970 replies) and, for the first time, at device 01 (`cap_dev01`, 966, the
5Fx family left out), with a handful more at 01 and 02 (`cap_dev`). Every
earlier capture asked device 00 alone, so what a console does with a device
number had never been measured.

The emulator here is the snapshot loaded through `sitebackup.load` and
nothing else -- no replay, no cage built by hand -- so this is also the test
of the import: a value the snapshot carries in a form the console does not
hold shows up as a report printing packed text. The console's clock is put
on each reply's own stamp before it is asked.

What differs is named below, with why. Floors rise and do not fall.

Not committed, the same as every capture: it skips without them.
"""
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_swept import (display_body, framed, packed_body,     # noqa
                              tail)

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "console_capture")
# kept for the scripts and tests that read the first day's files
DAY = os.path.join(BENCH, "bench-2026-09-22")
SNAPSHOT = os.path.join(DAY, "snapshot.vrset")

# the bench's state, which a snapshot does not carry
STATE = {
    # the cards' resistances and the PC board's run counters (FIDELITY S29)
    "I102", "i102", "I903", "i903",
    # the bench's resistors on the G.T. card's thermistor inputs
    "IB21", "iB21",
    # the session cut off mid-command on 2026-09-19, which 888 records
    "I888", "i888",
    # the glass, to the second
    "i5FA", "I5FA",
    # Q1's own history -- its 01-16 PLLD OPEN in I382, the days since its
    # last update in B7E, the A/D references in B81 -- which the snapshot,
    # taken with Q1 off, had no report of (the `on_*` captures)
    "I382", "i382", "IB7E", "iB7E", "iB81",
}
# and what is still this console's own (FIDELITY S38), as asked
OPEN = {
}


CAPTURES = {
    # (day, capture): (floor display, floor computer). Each day is replayed
    # through its own snapshot. 2026-09-24 is the same console with a WPLLD
    # Communications Module (330812-001) fitted in comm 3 and nothing else
    # changed: FIDELITY M24.
    ("bench-2026-09-22", "cap_now"): (481, 479),
    ("bench-2026-09-22", "cap_dev01"): (478, 477),
    ("bench-2026-09-22", "cap_dev02"): (477, 477),
    ("bench-2026-09-22", "cap_dev"): (13, 5),
    ("bench-2026-09-24", "cap_now"): (480, 479),
    ("bench-2026-09-24", "cap_dev01"): (478, 477),
    ("bench-2026-09-24", "cap_dev02"): (477, 477),
    # and with tank 1 and Q1 switched on over the wire, no probe and an open
    # transducer, the report families a running tank or line moves
    ("bench-2026-09-24", "on_now"): (325, 323),
    ("bench-2026-09-24", "on_dev01"): (324, 322),
    ("bench-2026-09-24", "on_dev02"): (326, 325),
}
#: the Sets the bench was sent before a capture, and when (`tankon.jsonl`)
SETS = {("bench-2026-09-24", name): ("0601240252",
                                     ["S601011", "S781011", "S00300"])
        for name in ("on_now", "on_dev01", "on_dev02")}

DSTAMP = re.compile(rb"\r\n([A-Z]{3} +\d+, \d{4} +\d+:\d\d [AP]M)\r\n")
CSTAMP = re.compile(rb"^\x01?[iI][0-9A-Z@]{5}(\d{10})")


#: {capture: {code: (the bench's bytes, ours)}}, filled by `replay`
RAWS = {}
_STAMP_LINE = re.compile(r"^[A-Z?]{3} [ 0-9]?\d, \d{4} [ 0-9]?\d:\d\d [AP]M$")


def exact_rows(data):
    """A display reply's lines as sent, trailing spaces and all; the stamp
    alone is normalised."""
    text = data.decode("latin-1").replace("\x01", "").replace("\x03", "")
    return ["<STAMP>" if _STAMP_LINE.match(r) else r for r in text.split("\r\n")]


def named(code):
    return code[:4] in STATE or code in OPEN


def have():
    return all(os.path.exists(os.path.join(BENCH, day, "snapshot.vrset"))
               and os.path.isdir(os.path.join(BENCH, day, name, "raw"))
               for day, name in CAPTURES)


def stamp_of(data):
    """The moment the bench answered, off the reply's own stamp."""
    m = CSTAMP.match(data)
    if m and data[:2] != b"\x01\r":
        return time.strptime(m.group(1).decode(), "%y%m%d%H%M")
    m = DSTAMP.search(data)
    if m:
        return time.strptime(re.sub(r" +", " ", m.group(1).decode()),
                             "%b %d, %Y %I:%M %p")
    return None


def replay(key):
    """{code: (the bench's body and tail, ours)} for one (day, capture)."""
    from tls350sim import sitebackup, wire
    from tls350sim.console import Console
    day, name = key
    c = Console(os.path.join(tempfile.mkdtemp(), "state.json"))
    sitebackup.load(c, os.path.join(BENCH, day, "snapshot.vrset"))
    h = wire.Handler(c, verbose=False)
    if key in SETS:
        at, sets = SETS[key]
        c.clock_offset = time.mktime(time.strptime(at, "%y%m%d%H%M")) \
            - time.time()
        for cmd in sets:
            h.handle(b"\x01" + cmd.encode("ascii") + b"\r")
    raw = os.path.join(BENCH, day, name, "raw")
    out, last = {}, None
    RAWS[key] = raws = {}
    for f in sorted(os.listdir(raw)):
        # the filesystem that took the first captures folded case, so the
        # computer replies are `c_i...`
        code = f[:-4].replace("c_", "")
        with open(os.path.join(raw, f), "rb") as fh:
            real = fh.read()
        when = stamp_of(real) or last
        if when:
            last = when
            c.clock_offset = time.mktime(
                when[:5] + (30,) + when[6:8] + (-1,)) - time.time()
        ours = framed(h, b"\x01" + code.encode("ascii"))
        raws[code] = (real, ours)
        body = packed_body if code[0] == "i" else display_body
        a, b = body(real), body(ours)
        if code[0] == "I":
            a, b = (a, tail(real)), (b, tail(ours))
        out[code] = (a, b)
    return out


@unittest.skipUnless(have(), "the 2026-09-22/24 bench captures are not here")
class TheBenchSnapshot(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rows = {name: replay(name) for name in CAPTURES}

    def test_what_differs_is_named(self):
        for name, rows in self.rows.items():
            with self.subTest(capture=name):
                wrong = {code for code, (a, b) in rows.items() if a != b}
                named_ = {code for code in wrong if named(code)}
                self.assertEqual(sorted(wrong - named_), [],
                                 "replies unlike the bench console's")

    def test_what_is_named_still_differs(self):
        """An exception that agrees now comes off the list."""
        for name, rows in self.rows.items():
            with self.subTest(capture=name):
                for code, (a, b) in rows.items():
                    if code in OPEN:
                        self.assertNotEqual(a, b, f"{code} agrees now")

    def test_a_reply_that_agrees_agrees_to_its_last_space(self):
        """Every comparison above strips each line, as the ratchets before it
        did, and so none of them saw the bench keep a heading's padding or
        60A's stray CR: 27 reports did (2026-09-23, `wiretables.pad_line`).
        Where a display reply agrees stripped, it agrees byte for byte."""
        for name, rows in self.rows.items():
            with self.subTest(capture=name):
                wrong = []
                for code, (a, b) in rows.items():
                    if code[0] != "I" or a != b:
                        continue
                    real, ours = RAWS[name][code]
                    if exact_rows(real) != exact_rows(ours):
                        wrong.append(code)
                self.assertEqual(wrong, [])

    def test_the_packed_configuration_names_each_card_as_the_bench_does(self):
        """i102 is on STATE for its resistances, which move, and that hid its
        module TYPES, which do not: the PLLD sensor board packed as 1B, the
        controller's code, and the controller and the satellite as 00, an
        empty slot (2026-09-24). Each record's position and type, readings
        left out."""
        def records(data):
            body = re.match(rb"^\x01i102\d\d\d{10}(.*)&&", data).group(1)
            body = body.decode("latin-1")
            count, body = int(body[:2], 16), body[2:]
            return [body[i:i + 4] for i in range(0, count * 20, 20)]
        for name in CAPTURES:
            for code, (real, ours) in RAWS[name].items():
                if code.startswith("i102"):
                    with self.subTest(capture=name, code=code):
                        self.assertEqual(records(ours), records(real))

    def test_the_floors(self):
        for name, (display, computer) in CAPTURES.items():
            rows = self.rows[name]
            with self.subTest(capture=name):
                same = {f: sum(1 for code, (a, b) in rows.items()
                               if code[0] == f and a == b) for f in "Ii"}
                self.assertGreaterEqual(same["I"], display)
                self.assertGreaterEqual(same["i"], computer)


def main():
    import difflib
    for name in CAPTURES:
        rows = replay(name)
        for f in "Ii":
            n = sum(1 for code, (a, b) in rows.items()
                    if code[0] == f and a == b)
            print(name, f, "same", n, "of",
                  sum(1 for code in rows if code[0] == f))
        for code, (a, b) in sorted(rows.items()):
            if a == b:
                continue
            print("DIFF", name, code,
                  "(named)" if named(code) else "")
            if "-v" in sys.argv:
                a, b = (a[0], b[0]) if code[0] == "I" else (a, b)
                for line in difflib.unified_diff(a, b, "bench", "ours",
                                                 lineterm="", n=0):
                    print("   " + line[:160])


if __name__ == "__main__":
    main()
