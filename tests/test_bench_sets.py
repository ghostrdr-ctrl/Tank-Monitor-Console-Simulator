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
"""The emulator's answers to Sets, against the bench TLS-350's.

On 2026-09-18 the bench console was programmed over its TCP/IP card and every
command and reply was kept, raw, under
`tests/console_capture/bench-2026-09-18/transcripts/<experiment>/`. This
replays each experiment through an emulator built as that console and in the
state the console was in when the experiment began, and compares every reply
body. It is how the reply to an accepted Set (the device's report), the reply
to a refused one (a `?` per character), the ranges, and the label rules are
held to the hardware rather than to a reading of a page.

Not committed, the same as every capture: it skips without the transcripts.
"""
import difflib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_bench_console import (BENCH, at_cold_start, bench_console,  # noqa
                                lines)

TRANSCRIPTS = os.path.join(BENCH, "transcripts")

# What the bench console held when each experiment began, as the Sets that
# would put an emulator there. Before the cold start (e1 to e11) it had PLLD
# line Q1 switched on, labelled RUL, on tank 1, and a header line of twenty
# blanks, all set at its keypad; after it (e24 on) it had nothing but the
# 200 feet e22 gave Q1's length.
BEFORE_COLD_START = ["S781011", "S78201RUL", "S7850101",
                     "S50302" + " " * 20,
                     # every receiver's ON DATE held JAN 16, 2006 with the
                     # time disabled (`i52B01` packed `1060116EE00`), where a
                     # cold start stores zeros
                     "S52B001060116EE00"]
PREAMBLE = {
    "e1c_length": BEFORE_COLD_START + ["S78901501"],
    "e1d_bounds": BEFORE_COLD_START + ["S78901200"],
    "e4_refusalwidth": BEFORE_COLD_START + ["S78901501"],
    "e5_computerset": BEFORE_COLD_START + ["S78901020"],
    "e6_highbyte": BEFORE_COLD_START,
    "e7_wplld_autodial": BEFORE_COLD_START,
    "e8_weekly": BEFORE_COLD_START,
    "e11_density": BEFORE_COLD_START,
    "e24_families": ["S78901200"],
    "e26_77x": ["S78901200"],
    "e27_780": ["S78901200"],
}

# Replies that read the alarm log, the clock or the display, which a replay
# does not recreate: the console had hours of history behind it.
UNCOMPARED = ("I1", "i1", "I5FA", "i5FA", "S003", "s003")


def have():
    return os.path.isdir(TRANSCRIPTS)


def replay(name):
    """[(command, the console's body, ours)] for one experiment."""
    from tls350sim.wire import Handler
    folder = os.path.join(TRANSCRIPTS, name)
    c = bench_console()
    h = Handler(c, verbose=False)
    out = []
    with at_cold_start(c):
        for cmd in PREAMBLE.get(name, []):
            h.handle(b"\x01" + cmd.encode("latin-1"))
        for fname in sorted(os.listdir(folder),
                            key=lambda f: int(f.split("_", 1)[0])):
            sent = _sent(name, fname)
            if sent is None:
                continue
            with open(os.path.join(folder, fname), "rb") as fh:
                real = fh.read()
            mine = h.handle(b"\x01" + sent)
            text = sent.decode("latin-1")
            if text.startswith(UNCOMPARED) or not real:
                continue
            if text[:1] in "is":
                out.append((text, [real.rstrip(b"\x03")[:-4].decode("latin-1")[17:]],
                            [mine.rstrip(b"\x03")[:-4].decode("latin-1")[17:]]))
            else:
                out.append((text, lines(real), lines(mine)))
    return out


def _sent(name, fname):
    """The bytes an experiment sent, from its own log -- the file name has
    the command with every non-alphanumeric byte turned into `_`."""
    log = os.path.join(TRANSCRIPTS, name + ".log")
    index = int(fname.split("_", 1)[0])
    sent = []
    with open(log, encoding="utf-8") as fh:
        for line in fh:
            if "  SEND b" in line:
                sent.append(eval(line.split("  SEND ", 1)[1].split("  (")[0]))
    if index - 1 >= len(sent):
        return None
    raw = sent[index - 1]
    return raw.rstrip(b"\r")


@unittest.skipUnless(have(), "no bench transcripts in %s" % TRANSCRIPTS)
class TheBenchConsoleAnswersSets(unittest.TestCase):
    """Every reply the bench console gave in the programmed experiments."""

    #: What matched when this was last measured. A floor, never a target.
    MATCHED = 132

    def test_the_measure_does_not_fall(self):
        same = 0
        for name in PREAMBLE:
            if os.path.isdir(os.path.join(TRANSCRIPTS, name)):
                same += sum(1 for _c, a, b in replay(name) if a == b)
        self.assertGreaterEqual(same, self.MATCHED)


def main():
    show = "-v" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    total = same = 0
    for name in PREAMBLE:
        if only and name not in only:
            continue
        if not os.path.isdir(os.path.join(TRANSCRIPTS, name)):
            continue
        for cmd, a, b in replay(name):
            total += 1
            if a == b:
                same += 1
                continue
            print("DIFF %s %r" % (name, cmd))
            if show:
                for one in difflib.unified_diff(a, b, "bench", "ours",
                                                lineterm="", n=0):
                    print("   " + one)
    print("same %d of %d" % (same, total))


if __name__ == "__main__":
    main()
