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
"""The setup printout, against a real one.

On 2026-08-21 a real site printed its setup and gave up its backup within
twenty minutes of each other. `tape/site_20260821.txt` is the
printout, transcribed from the scan and checked line by line against the
scan's own geometry -- every line's indent, length and the blank lines
between them come off the paper, not off a reading of it.
`tape/site_20260821.vrset` is what the console was holding when it
printed, so the values on the report can be reproduced rather than guessed.

The backup covers S501 to S51E and nothing else, which is the SYSTEM SETUP
range: so SYSTEM SETUP is the section that can be driven from real
programming end to end, and it is the one this file holds the simulator to.
The other six sections on the tape have no backup behind them and are
compared by eye, not here.

This is a RATCHET, like `test_citations.py`. The simulator does not print
this report perfectly yet; what it must not do is print it worse than it
did. Raise the floor when you raise the match, and put what is still wrong
in `still_wrong` so it stays visible instead of merely failing.

Both files are a real site's: they carry its street address and its account
number, so they are NOT in the repository and never were pushed to it. They
live in `tests/tape/` (gitignored) on the machine that has them, or wherever
$VR_TAPE points, exactly as the real backups in `tests/real_backups/` do.
With them absent this whole file skips, so a clone without the data still
runs green -- and the ratchet below only guards on a machine that has the
paper to guard against.
"""
import difflib
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from tls350sim import printer                              # noqa: E402
from tls350sim.console import Console                      # noqa: E402

TAPE_DIR = os.environ.get("VR_TAPE") or os.path.join(HERE, "tape")
TAPE = os.path.join(TAPE_DIR, "site_20260821.txt")
BACKUP = os.path.join(TAPE_DIR, "site_20260821.vrset")


def setUpModule():
    """No paper, no test. The data is one machine's, not the repository's."""
    missing = [p for p in (TAPE, BACKUP) if not os.path.exists(p)]
    if missing:
        raise unittest.SkipTest(
            "the tape is not here (%s). It is a real site's printout and "
            "backup, kept out of the repository; put them in tests/tape/ or "
            "point $VR_TAPE at them to run this."
            % ", ".join(os.path.basename(p) for p in missing))

# Where the match stands. It is a floor, never a target: a change that makes
# the report read less like the paper is a change that has to explain itself.
SYSTEM_SETUP_FLOOR = 0.84

# The console stamps the report with the time it printed, so that one line
# can never match the paper's and is compared as the stamp it is.
STAMP = re.compile(r"^[A-Z]{3} \d\d, \d{4} +\d{1,2}:\d\d [AP]M$")


def stamps_aside(lines):
    return ["<the time it printed>" if STAMP.match(x) else x for x in lines]


def the_site():
    """The console the tape came off, as far as it can be known.

    The cage is what the tape shows the console serving: two probes, the
    liquid sensors L3 to L6, and the three comm boards under COMMUNICATIONS
    SETUP. BIR is licensed because RECONCILIATION SETUP is on the tape and
    SHIFT BIR PRINTOUTS is in its system setup; CSLD because LEAK TEST
    METHOD reads TEST CSLD.
    """
    c = Console(None)
    c.modules = {"probe": 2, "liquid": 8, "rs232": 1, "mt": 1}
    c.software = {"bir": True, "csld": True}
    c.seed(BACKUP)
    return c


def station_header():
    """The site's identity block, read out of the tape.

    Every printout opens with the date-format line, then a blank, then the
    lines the site programmed into S50B-S50E: account, name, street, city and
    registration. Found by position rather than by content, so no site's
    details are written down in this repository.
    """
    lines = open(TAPE, encoding="utf-8").read().split("\n")
    start = lines.index("MON DD YYYY HH:MM:SS xM") + 1
    while start < len(lines) and not lines[start].strip():
        start += 1
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line.rstrip())
    return block


def tape_section(name, after=None):
    """The tape's own lines for one section, blanks dropped."""
    lines = open(TAPE, encoding="utf-8").read().split("\n")
    start = lines.index(name)
    end = lines.index(after) if after else len(lines)
    return [x.rstrip() for x in lines[start:end] if x.strip()]


def printed_section(console, name):
    fn = [f for f in console.available_functions() if f["function"] == name]
    if not fn:
        raise AssertionError(f"this console has no {name}")
    return [x for x in (line.rstrip()
                        for line in printer.setup_section(console, fn[0]))
            if x]


class TheSetupPrintAgainstARealOne(unittest.TestCase):
    def setUp(self):
        self.console = the_site()
        self.tape = stamps_aside(
            tape_section("SYSTEM SETUP", "COMMUNICATIONS SETUP"))
        self.mine = stamps_aside(printed_section(self.console,
                                                 "SYSTEM SETUP"))

    def match(self):
        return difflib.SequenceMatcher(None, self.tape, self.mine).ratio()

    def test_the_paper_says_what_it_says(self):
        """The tape is data, so it is worth knowing it has not moved."""
        self.assertEqual(self.tape[0], "SYSTEM SETUP")
        self.assertEqual(self.tape[1], printer.SETUP_RULE)
        self.assertEqual(len(self.tape), 81)
        self.assertTrue(all(len(x) <= printer.SETUP_COLS for x in self.tape))

    def test_nothing_runs_off_the_display(self):
        """The report is the console's screen, so no line is wider."""
        for line in self.mine:
            self.assertLessEqual(len(line), printer.SETUP_COLS,
                                 f"off the screen: {line!r}")

    def test_the_lines_it_does_print_are_the_paper_s(self):
        """A ratchet on how much of the real report comes out of the sim."""
        got = self.match()
        self.assertGreaterEqual(
            got, SYSTEM_SETUP_FLOOR,
            f"the setup print reads less like the paper than it did: "
            f"{got:.1%} against a floor of {SYSTEM_SETUP_FLOOR:.1%}\n"
            + "\n".join(difflib.unified_diff(self.tape, self.mine,
                                             "tape", "sim", lineterm="",
                                             n=0)))

    def test_the_values_the_backup_carries_are_the_ones_it_prints(self):
        """The settings, whatever is still wrong with the layout.

        These are the four the console's own backup pins down and the
        report used to get wrong: two of them were being read two
        characters in, and the date format did not decode at all.
        """
        out = "\n".join(self.mine)
        self.assertIn("SHIFT TIME 1 :  4:00 AM", out)
        self.assertIn("SHIFT TIME 2 : DISABLED", out)
        self.assertIn("MAR   WEEK 2   SUN", out)
        self.assertIn("NOV   WEEK 1   SUN", out)
        self.assertIn("MON DD YYYY HH:MM:SS xM", out)

        # The station header -- the site's account, name, address and
        # registration -- comes off the tape rather than being written here,
        # so this file names no site and the identity lives only in the data.
        # Stronger than the four literals this replaced: it checks the whole
        # block, and it keeps checking it if the tape is ever swapped for a
        # different site's.
        header = station_header()
        self.assertTrue(header, "the tape has no station header block")
        for line in header:
            self.assertIn(line, out)

    def test_what_is_still_wrong_is_still_what_is_still_wrong(self):
        """The known gap, named, so it shrinks on purpose rather than by luck.

        Each of these is a line the paper has and the simulator does not, or
        the other way round. They are here so that fixing one is visible and
        breaking a new one is loud.
        """
        still_wrong = {
            # A line leak module. The tape's console serves S553, S556 and
            # S559, so it has one; which one is not settled, because the
            # cage that shows those three also shows PRECISION TEST DURATION
            # and the two auto-confirm screens, and the tape has none of
            # them. Until that is worked out the fixture carries no line
            # module and these four are missing.
            "LINE RE-ENABLE METHOD", "PASS LINE TEST",
            "LINE PER TST NEEDED WRN", "LINE ANN TST NEEDED WRN",
            # Screens this console does not show and the simulator does,
            # each needing its own citation before it can be gated:
            #   REMOTE PRINTER -- 576013-623 Rev AN: "for systems equipped
            #   with a Remote Printer Interface Module and Remote Printer
            #   only", and there is no such module in the cage yet
            "REMOTE PRINTER", "PAGE EJECT DISABLED",
            "TANKER LOAD REPORT", "PRINT PRECISION LINE",
            "TEST RESULTS: DISABLED", "QPLD MONTHLY PRINTOUT",
            "BDIM TRANS ALARM DELAY", "HOURS: 024", "BEEPER",
            # TANK CHART SECURITY is modelled as the passcode screen and
            # printed as one; the tape prints it as an enable flag, and
            # prints CUSTOM ALARMS, DELIVERY OVERRIDE and the ISO country
            # code the same way
            "CODE : 000000", "CUSTOM ALARMS", "DELIVERY OVERRIDE", "CODE:",
            "DISABLED", "ENABLED",
        }
        d = difflib.unified_diff(self.tape, self.mine, lineterm="", n=0)
        moved = {line[1:].strip() for line in d
                 if line[:1] in "+-" and line[:3] not in ("+++", "---")}
        self.assertEqual(
            sorted(moved - still_wrong), [],
            "a difference from the paper that was not there before")


if __name__ == "__main__":
    unittest.main()
