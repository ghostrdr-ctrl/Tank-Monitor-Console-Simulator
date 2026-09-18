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
# Raised from 0.90 when the bay became the one the tape came off: a
# satellite in slot 1 and a dual-port Maintenance Tracker module in slot 4,
# on a board that cannot drive Maintenance Tracker. FIDELITY M7.
SYSTEM_SETUP_FLOOR = 0.914
# How much of the tape's IN-TANK block the simulator's ROW GEOMETRY
# reproduces. Shape rather than text, because the tape's own backup only
# captured the S50x range and carries no tank values to replay: what is
# being ratcheted is where the colon lands and how wide the field after it
# is, which is the whole of FIDELITY T1 for this section.
# Raised from 0.92 when the fixture stopped enabling ticketed delivery,
# which the tape's own backup has off: FIDELITY T6.
IN_TANK_SHAPE_FLOOR = 0.928
# The two sections that had no ratchet at all until they had one. Both
# are compared as TEXT rather than as shape: their values are words the
# paper spells out, so a fixture can hold what the site held.
# FIDELITY T1c.
RECONCILIATION_FLOOR = 1.0
LIQUID_SENSOR_FLOOR = 1.0
# And the same three sections measured with the BLANK LINES COUNTED,
# which is a different and harder question: a section can print every
# row right and still group them wrongly, and for as long as the blanks
# were unplaced nothing here could tell. FIDELITY T2.
# Raised from 0.88 when a step's gap stopped opening every line of a
# repeated run and went back to opening the run: the four station header
# lines and the four shift times print against each other on the paper.
SYSTEM_SETUP_BLANKS_FLOOR = 0.929
# Raised from 0.96, where the console had been printing 100% for long
# enough that nobody noticed: a floor four points under what is achieved is
# four points of ground that can be given back without anything failing.
# FIDELITY V3.
RECONCILIATION_BLANKS_FLOOR = 1.0
LIQUID_SENSOR_BLANKS_FLOOR = 0.97
# COMMUNICATIONS SETUP is the section that cannot be derived, and it is
# measured anyway so that it is a number rather than a shrug. Two facts are
# missing and both are named in `TheBlankLinesAreThePaperS`.
COMMUNICATIONS_BLANKS_FLOOR = 0.56

# The console stamps the report with the time it printed, so that one line
# can never match the paper's and is compared as the stamp it is. The day is
# space padded like the hour -- the tape's own AUG 21 does not show it, but
# a run on the third of a month does. FIDELITY W27.
STAMP = re.compile(r"^[A-Z]{3} +\d{1,2}, \d{4} +\d{1,2}:\d\d [AP]M$")


def stamps_aside(lines):
    return ["<the time it printed>" if STAMP.match(x) else x for x in lines]


def the_site():
    """The console the tape came off, as far as it can be known.

    The cage is what the tape shows the console serving: two probes, the
    liquid sensors L3 to L6, and the three comm boards under COMMUNICATIONS
    SETUP. BIR is licensed because RECONCILIATION SETUP is on the tape and
    SHIFT BIR PRINTOUTS is in its system setup; CSLD because LEAK TEST
    METHOD reads TEST CSLD.

    The bay is numbered `1`, `5`, `6`, which is a PORT list and not a slot
    list: a single-port satellite board in slot 1, and one dual-port module
    in slot 4 answering on both of the positions above it. The Maintenance
    Tracker one is 330586-017, whose DB-9 half is the only 402K Maintenance
    Tracker port in the catalogue and whose RJ-45 half is the `RS-485` the
    tape prints on 5. See FIDELITY M7.
    """
    c = Console(None)
    c.modules = {"probe": 2, "liquid": 8, "ssat": 1, "mt4": 1}
    c.software = {"bir": True, "csld": True}
    c.seed(BACKUP)
    # The backup covers S501 to S51E and the tape shows more than that.
    # SERVICE NOTICE reads ENABLED on the paper, which is also what puts
    # DELIVERY OVERRIDE under it -- "Menu will only appear if Service
    # Notice is enabled and there is a probe module", 576013-623 Rev AN.
    c.values["S56600"] = "1"
    return c


def the_site_with_its_receiver():
    """The tape's console with its one auto-dial destination programmed.

    `the_site` is built from a backup that covers S501 to S51E, so it knows
    nothing about the receiver the tape's COMMUNICATIONS SETUP prints. D 8
    is a computer on port 1, three retries three minutes apart, confirmation
    off and no location label, dialled weekly on a Thursday at 5:26 PM --
    read off the paper, as `TheAutoDialBlockOnTheTape` reads it.
    """
    c = the_site()
    for code, value in (("S52108", "081"),      # configured
                        ("S52208", "08"),       # no location label
                        ("S52408", "0803"),     # COMPUTER
                        ("S52508", "081"),      # port 1
                        ("S52608", "083"),      # 3 retries
                        ("S52708", "083"),      # 3 minutes
                        ("S52808", "080")):     # confirmation OFF
        c.values[code] = value
    c.receiver_dial[8] = "441726"               # weekly, Thursday, 17:26
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

        Each of these is a SCREEN the paper has and the simulator does not,
        or the other way round, and it is a screen rather than a line for a
        reason. This held bare strings -- `DISABLED`, `ENABLED`, `CODE:` --
        so a value regression that landed on one of those words passed
        silently: BEEPER could have flipped to ENABLED and nothing here
        would have moved. A head and the value under it are checked
        together now. FIDELITY T9.
        """
        still_wrong = {
            # A line leak module. The tape's console serves S553, S556 and
            # S559, so it has one; which one is not settled, because the
            # cage that shows those three also shows PRECISION TEST DURATION
            # and the two auto-confirm screens, and the tape has none of
            # them. Until that is worked out the fixture carries no line
            # module and these three are missing.
            ("LINE RE-ENABLE METHOD", "PASS LINE TEST"),
            ("LINE PER TST NEEDED WRN", "DISABLED"),
            ("LINE ANN TST NEEDED WRN", "DISABLED"),
            # Screens this console shows and the tape does not, each needing
            # its own citation before it can be gated. TANKER LOAD REPORT
            # and QPLD MONTHLY PRINTOUT are two of T6's six.
            ("TANKER LOAD REPORT", "DISABLED"),
            ("PRINT PRECISION LINE", "TEST RESULTS: DISABLED"),
            # Paired the other way round since the two printout screens
            # went into the manual's order (SU3): the tape has RE-DIRECT
            # and not QPLD, so the run the diff hands this heuristic now
            # opens on the value and closes on the head. Same one screen,
            # same reason it is here.
            ("DISABLED", "QPLD MONTHLY PRINTOUT"),
            # The tape prints no BEEPER screen at all, so this is a screen
            # this console draws and the paper does not, whatever it reads.
            # It read DISABLED until the beeper gained the default the setup
            # manual's own walk implies -- "If you want to disable the
            # console beeper, press CHANGE" over an ENABLED screen -- and a
            # real console's I53000 confirms: `SYSTEM BEEPER` / `ENABLED`.
            ("BEEPER", "ENABLED"),
            # BDIM TRANS ALARM DELAY was here and is gone -- **and the
            # second assertion below is what noticed**. It got the citation
            # the comment above asks for, a gate on an I/O or RS-232 module,
            # and the tape's console has neither; so the screen stopped
            # being drawn on this fixture and the entry stopped being true.
            # It sat here for weeks after that, because the ratchet only
            # asserted one way round. FIDELITY V2.
        }
        moved = set()
        for side in "-+":
            run = [line[1:].rstrip() for line in
                   difflib.unified_diff(self.tape, self.mine, lineterm="",
                                        n=0)
                   if line[:1] == side and line[:3] not in ("+++", "---")]
            # a screen is a head and the value under it, in that order
            moved |= {(run[i], run[i + 1] if i + 1 < len(run) else "")
                      for i in range(0, len(run), 2)}
        self.assertEqual(
            sorted(moved - still_wrong), [],
            "a difference from the paper that was not there before")
        # And the other direction, which this ratchet did not have. A screen
        # on the list that starts matching the paper has to LEAVE the list in
        # the same commit, or the set drifts from a record of what is wrong
        # into a record of what was once wrong -- which is exactly what
        # happened to `test_conformance`'s frozen list, where both remaining
        # items turned out to be settled by figures already on the shelf.
        # `test_fidelity`'s STILL_WRONG and `test_printer`'s TOO_WIDE have
        # both asserted both ways all along. FIDELITY V2.
        self.assertEqual(
            sorted(still_wrong - moved), [],
            "a screen on the still-wrong list matches the paper now: "
            "take it off the list and say so in FIDELITY")


def row_shape(lines):
    """A row with its value taken out: the label, the colon, and the width
    of the field after it. What is left is the geometry."""
    out = []
    for line in lines:
        line = str(line).rstrip()
        cut = line.find(":")
        out.append(f"{line[:cut + 1]}<{len(line) - cut - 1}>"
                   if cut >= 0 else line)
    return out


def first_tank(lines):
    """One tank's block, from its head to the next tank's."""
    start = next((i for i, l in enumerate(lines)
                  if re.match(r"^T ?1:", str(l))), None)
    if start is None:
        return []
    rest = lines[start + 1:]
    end = next((i for i, l in enumerate(rest)
                if re.match(r"^T ?\d+:", str(l))
                or str(l).startswith("TANK CONFIG")), len(rest))
    return [str(l).rstrip() for l in lines[start:start + 1 + end]]


class TheInTankPrintAgainstARealOne(unittest.TestCase):
    """IN-TANK SETUP is 84 of the tape's 153 rows, the biggest section."""

    def a_tank_like_the_tape_s(self):
        """The tape's tank 1: 96 inches, a 4 point chart, a 9995 label.

        The values are the tape's because the shape depends on them -- a
        1 point tank prints no chart rows at all -- and not because the
        numbers themselves are being compared.
        """
        import struct
        from tests.test_printer import a_site
        c = a_site()

        def f(v):
            return struct.pack(">f", v).hex().upper()
        c.values["S60701"] = "01" + f(96.0)
        c.values["S60501"] = "01" + f(9995.0) + f(8070.0) + f(5031.0)             + f(1983.0)
        c.values["S60401"] = "01" + f(9995.0)
        c.values["S62801"] = "01" + f(9995.0)
        # The tape's console had ticketed delivery DISABLED -- its backup
        # holds S51C00 = '0' and its paper prints TICKETED DELIVERY /
        # DISABLED -- and the preset behind a_site() enables it. VAPOR LOSS
        # FACTOR is gated on that flag, which is the manual's own condition
        # and correct, so the simulator printed a row the paper does not and
        # the console was right about it. Third time a difference from the
        # paper has been the fixture rather than the console. FIDELITY T6.
        c.values["S51C00"] = "0"
        return c

    def test_the_rows_are_the_shape_the_paper_prints(self):
        tape = first_tank(tape_section("IN-TANK SETUP", "LIQUID SENSOR SETUP"))
        self.assertTrue(tape, "the tape has no IN-TANK block")
        mine = first_tank(printed_section(self.a_tank_like_the_tape_s(),
                                          "IN-TANK SETUP"))
        a, b = row_shape(tape), row_shape(mine)
        got = difflib.SequenceMatcher(None, a, b).ratio()
        self.assertGreaterEqual(
            got, IN_TANK_SHAPE_FLOOR,
            f"the in-tank rows read less like the paper than they did: "
            f"{got:.1%} against a floor of {IN_TANK_SHAPE_FLOOR:.1%}\n"
            + "\n".join(difflib.unified_diff(a, b, "tape", "sim",
                                                     lineterm="", n=0)))

    def test_a_four_point_tank_prints_its_chart(self):
        """605 is "Set Tank 4 Point Full, 3/4, 1/2, 1/4 Volumes" and holds
        all four. The console held all four and printed one."""
        mine = printed_section(self.a_tank_like_the_tape_s(), "IN-TANK SETUP")
        for row in ("       FULL VOL :   9995", "  72.0 INCH VOL :   8070",
                    "  48.0 INCH VOL :   5031", "  24.0 INCH VOL :   1983"):
            self.assertIn(row, mine, row)


class TheCommunicationsPrintAgainstARealOne(unittest.TestCase):
    """The port block, which is the part of this section the tape pins."""

    def a_console(self):
        return the_site()

    def a_modem_site(self):
        from tests.test_controls import a_site
        c, _h = a_site()
        return c

    def test_the_bay_is_the_one_the_tape_came_off(self):
        """`1`, `5`, `6`. Three boards on six positions across four slots:
        the satellite in slot 1, and the dual-port Maintenance Tracker
        module in slot 4 answering RS-485 on 5 and MTCOMM on 6. 577013-819
        Rev F p.37 is what numbers it -- "for the RS-232 port of a Multiport
        module, which is installed in slot 4, this number would be 6" -- and
        this is the arrangement that reads back as the paper. M7.
        """
        c = self.a_console()
        self.assertEqual(c.comm_layout(), {1: "ssat", 4: "mt4"})
        heads = [str(l).rstrip() for l in printed_section(c, "COMMUNICATIONS SETUP")
                 if str(l).startswith("COMM BOARD")]
        self.assertEqual(heads, [str(l).rstrip() for l in self.tape_heads()])

    def tape_heads(self):
        lines = tape_section("COMMUNICATIONS SETUP", "AUTO DIAL ALARM SETUP")
        return [l for l in lines if str(l).startswith("COMM BOARD")]

    def tape_port_block(self):
        """The tape's first COMM BOARD block, head and settings."""
        lines = tape_section("COMMUNICATIONS SETUP", "AUTO DIAL ALARM SETUP")
        start = next(i for i, l in enumerate(lines)
                     if str(l).startswith("COMM BOARD"))
        out = [str(lines[start]).rstrip()]
        for line in lines[start + 1:]:
            # tape_section has already dropped the blank lines, so a block
            # ends where the next board's head begins
            if str(line).startswith("COMM BOARD"):
                break
            out.append(str(line).rstrip())
        return out

    def test_the_port_block_is_the_shape_the_paper_prints(self):
        """Real 2026 tape:

            COMM BOARD  : 1 (S-SAT )
             BAUD RATE  : 9600
             PARITY     : NONE
             STOP BIT   : 2 STOP
             DATA LENGTH: 8 DATA
            RS-232 SECURITY
            CODE : DISABLED
             DTR NORMAL STATE: HIGH

        The four settings are indented one and share the board head's colon
        column at twelve; the security code is a head over its value rather
        than a row. This console printed no COMMUNICATIONS SETUP body at
        all until the comm bay was sized properly. FIDELITY T1.
        """
        want = self.tape_port_block()
        c = self.a_console()
        mine = printed_section(c, "COMMUNICATIONS SETUP")
        start = next((i for i, l in enumerate(mine)
                      if str(l).startswith("COMM BOARD")), None)
        self.assertIsNotNone(start, "no port block on the report")
        block = [str(l).rstrip() for l in mine[start:start + len(want)]]
        self.assertEqual(row_shape(block), row_shape(want))

    def test_a_modem_screen_belongs_to_the_modem_slot(self):
        """DIAL TYPE, ANSWER ON and the three modem screens were gated on
        the CONSOLE owning a modem, so a site with a modem in slot 2 got
        all five on its RS-232 in slot 1 as well."""
        c = self.a_modem_site()
        self.assertTrue(c.count("modem"), "fixture should have a modem")
        self.assertEqual(c.comm_board_name(1), "RS-232")
        mine = [str(l) for l in printed_section(c, "COMMUNICATIONS SETUP")]
        second = next(i for i, l in enumerate(mine)
                      if l.startswith("COMM BOARD") and "FXMOD" in l)
        first = next(i for i, l in enumerate(mine)
                     if l.startswith("COMM BOARD"))
        for screen in ("DIAL TYPE", "ANSWER ON", "SELECT MODEM"):
            self.assertFalse(any(screen in l for l in mine[first:second]),
                             f"{screen} on an RS-232 port")
            self.assertTrue(any(screen in l for l in mine[second:]),
                            f"{screen} missing from the modem port")


def before_the_station_header(lines):
    """A block's own rows, without the identity block that closes a report.

    LIQUID SENSOR SETUP is not the last thing on the roll, so the sensor
    block runs into the station header, the stamp and the status report
    behind it. The header is found by position rather than by content, the
    way `station_header` finds it, so no site's details are written down
    here either.
    """
    head = station_header()
    return lines[:lines.index(head[0])] if head and head[0] in lines else lines


class TheReconciliationPrintAgainstARealOne(unittest.TestCase):
    """The section the tape closes with: 20 rows, and every one measurable.

    Unlike IN-TANK there is no shape-only compromise to make here. The
    backup does not reach S79x, but every value this block prints is on the
    paper in words -- a closing time, DISABLED, MONTHLY, STANDARD -- so the
    fixture can hold what the site held and the whole block be compared as
    text rather than as geometry.
    """

    def a_site_like_the_tape_s(self):
        """The tape's reconciliation programming, read off the tape.

        Daily closing at 2:00 AM, shifts 1 to 3 disabled and shift 4 at
        4:00 AM, monthly mode, the alarm off, standard temp compensation and
        a zero meter calibration offset. The shift codes carry their device
        number in the first two characters, which is why these are stored
        with it rather than as a bare HHmm.
        """
        c = the_site()
        for code, value in (("S79300", "0200"), ("S79401", "01EEEE"),
                            ("S79402", "02EEEE"), ("S79403", "03EEEE"),
                            # The paper reads MODE: MONTHLY, and Monthly is
                            # `01` -- "2=Rolling 1=Monthly", 576013-635 Rev AA
                            # under 795. It was `0` here because the console's
                            # own option list was zero-based and inverted;
                            # FIDELITY F11. The tape's backup stops at the
                            # S50x range and does not carry 795, so the paper
                            # names the SETTING and the manual numbers it.
                            ("S79404", "040400"), ("S79500", "01"),
                            ("S79700", "01"), ("S79F00", "0"),
                            ("S7B200", "+0.000")):
            c.values[code] = value
        return c

    def setUp(self):
        self.tape = tape_section("RECONCILIATION SETUP")
        self.mine = printed_section(self.a_site_like_the_tape_s(),
                                    "RECONCILIATION SETUP")

    def test_the_block_reads_like_the_paper(self):
        got = difflib.SequenceMatcher(None, self.tape, self.mine).ratio()
        self.assertGreaterEqual(
            got, RECONCILIATION_FLOOR,
            f"the reconciliation print reads less like the paper than it "
            f"did: {got:.1%} against a floor of {RECONCILIATION_FLOOR:.1%}\n"
            + "\n".join(difflib.unified_diff(self.tape, self.mine,
                                             "tape", "sim", lineterm="",
                                             n=0)))

    def test_nothing_is_still_wrong(self):
        """The section matches the paper. The last screen between them was
        REMOTE REPORT FORMAT -- 576013-623 Rev AN: "NOTE: This feature
        appears only when a remote printer is installed" -- and the cage
        holds a Remote Printer Interface Module now to gate it on.
        FIDELITY T8.
        """
        still_wrong = set()
        d = difflib.unified_diff(self.tape, self.mine, lineterm="", n=0)
        moved = {line[1:].strip() for line in d
                 if line[:1] in "+-" and line[:3] not in ("+++", "---")}
        self.assertEqual(sorted(moved - still_wrong), [],
                         "a difference from the paper that was not there "
                         "before")

    def test_the_alarm_rows_follow_the_alarm(self):
        """The threshold and the offset are on the report only when the
        alarm is on. The tape's console has ALARM: DISABLED and prints
        neither of them; this checks the other half, which the tape cannot
        show. FIDELITY T8.
        """
        out = "\n".join(str(l) for l in self.mine)
        self.assertIn("ALARM:          DISABLED", out)
        self.assertNotIn("ALARM THRESHOLD", out)
        self.assertNotIn("ALARM OFFSET", out)
        c = self.a_site_like_the_tape_s()
        c.values["S79700"] = "02"
        on = "\n".join(str(l) for l in
                       printed_section(c, "RECONCILIATION SETUP"))
        self.assertIn("ALARM:           ENABLED", on)
        self.assertIn("ALARM THRESHOLD", on)
        self.assertIn("ALARM OFFSET", on)

    def test_the_last_line_is_the_table_head(self):
        """The paper closes the roll with the four-column head of the
        tank/meter map, not with the panel's title for the step."""
        self.assertEqual(str(self.mine[-1]), "BUS SLOT FUEL METER TANK")


class TheLiquidSensorPrintAgainstARealOne(unittest.TestCase):
    """Three lines a sensor, and the tape has four sensors to check them on."""

    def a_site_like_the_tape_s(self):
        """The tape's four sensors: L3 to L6, all tri-state, two in an
        annular space and two in an STP sump.

        A liquid sensor's stored value carries its device number in the
        first two characters. A fixture that stores the bare label gets
        `L 3:L INTERSTITUAL` back for `RUL INTERSTITUAL`, which looks
        exactly like a console bug and is not one.
        """
        c = the_site()
        for n, label, category in ((3, "RUL INTERSTITUAL", "2"),
                                   (4, "RUL STP", "5"),
                                   (5, "PUL INT", "2"),
                                   (6, "PUL STP", "5")):
            c.values[f"S702{n:02d}"] = f"{n:02d}{label}"
            c.values[f"S703{n:02d}"] = f"{n:02d}1"       # TRI-STATE
            c.values[f"S704{n:02d}"] = f"{n:02d}{category}"
        return c

    def setUp(self):
        self.tape = before_the_station_header(
            tape_section("LIQUID SENSOR SETUP", "RECONCILIATION SETUP"))
        self.mine = printed_section(self.a_site_like_the_tape_s(),
                                    "LIQUID SENSOR SETUP")

    def test_the_block_is_the_paper_s(self):
        """Line for line, blanks aside. The floor is 100% because the
        section reached it: the type carries its parenthetical, the category
        its full name, the label the space before its colon, and the module
        configuration screen that used to head every sensor is off the
        report. FIDELITY T7.
        """
        got = difflib.SequenceMatcher(None, self.tape, self.mine).ratio()
        self.assertGreaterEqual(
            got, LIQUID_SENSOR_FLOOR,
            f"the liquid sensor print reads less like the paper than it "
            f"did: {got:.1%} against a floor of {LIQUID_SENSOR_FLOOR:.1%}\n"
            + "\n".join(difflib.unified_diff(self.tape, self.mine,
                                             "tape", "sim", lineterm="",
                                             n=0)))

    def test_no_module_configuration_screen(self):
        """The tape's 568 lines carry one CONFIG -- SYSTEM SETUP's
        `CONFIG: STANDARD` -- and one SLOT, RECONCILIATION's column head.
        The slot map is a way in, and it was printing once per sensor."""
        for line in self.mine:
            self.assertNotIn("SENSOR CONFIG", str(line))
            self.assertNotIn("SLOT #", str(line))

    def test_a_sensor_is_headed_once(self):
        heads = [str(l) for l in self.mine if str(l).startswith("L ")]
        self.assertEqual(heads, ["L 3:RUL INTERSTITUAL", "L 4:RUL STP",
                                 "L 5:PUL INT", "L 6:PUL STP"])


def printed_with_blanks(console, name):
    """A section as it comes off the roll, blank lines and all.

    `printed_section` drops them, which is what made the blank placement
    unmeasured for as long as it was wrong: 32 of the paper's SYSTEM SETUP
    lines are blank and they are structural. FIDELITY T2.
    """
    fn = [f for f in console.available_functions() if f["function"] == name]
    if not fn:
        raise AssertionError(f"this console has no {name}")
    out = [str(line).rstrip() for line in printer.setup_section(console, fn[0])]
    while out and not out[-1]:
        out.pop()                      # the feed after a block is not the block
    return out


def tape_lines(start, end):
    """The tape's own lines, blanks kept, by position on the roll."""
    lines = open(TAPE, encoding="utf-8").read().split("\n")
    out = [x.rstrip() for x in lines[start:end]]
    while out and not out[-1]:
        out.pop()
    return out


class TheBlankLinesAreThePaperS(unittest.TestCase):
    """What `printed_section` cannot see, because it drops them.

    Each floor is measured with the blanks counted, which is a harder test
    than the same section's text ratchet and a different one: a section can
    print every row correctly and still group them wrongly.
    """

    def match(self, tape, mine):
        return difflib.SequenceMatcher(None, stamps_aside(tape),
                                       stamps_aside(mine)).ratio()

    def test_system_setup(self):
        got = self.match(tape_lines(0, 125),
                         printed_with_blanks(the_site(), "SYSTEM SETUP"))
        self.assertGreaterEqual(
            got, SYSTEM_SETUP_BLANKS_FLOOR,
            f"the blank lines read less like the paper than they did: "
            f"{got:.1%} against {SYSTEM_SETUP_BLANKS_FLOOR:.1%}")

    def test_reconciliation(self):
        c = TheReconciliationPrintAgainstARealOne(
            "test_the_last_line_is_the_table_head").a_site_like_the_tape_s()
        got = self.match(tape_lines(538, 568),
                         printed_with_blanks(c, "RECONCILIATION SETUP"))
        self.assertGreaterEqual(got, RECONCILIATION_BLANKS_FLOOR,
                                f"{got:.1%} against "
                                f"{RECONCILIATION_BLANKS_FLOOR:.1%}")

    def test_liquid_sensor(self):
        """The one line short of the paper is the paper's own: it puts
        three blanks between two pairs of sensors and two between the
        other, and three is what two of the three say."""
        c = TheLiquidSensorPrintAgainstARealOne(
            "test_a_sensor_is_headed_once").a_site_like_the_tape_s()
        got = self.match(tape_lines(480, 503),
                         printed_with_blanks(c, "LIQUID SENSOR SETUP"))
        self.assertGreaterEqual(got, LIQUID_SENSOR_BLANKS_FLOOR,
                                f"{got:.1%} against "
                                f"{LIQUID_SENSOR_BLANKS_FLOOR:.1%}")

    def test_a_repeated_screen_prints_as_one_run(self):
        """A step's gap opens its RUN, not every line of it.

        The panel walks the station header with the arrow keys, line 1 to 4,
        and the report cannot, so all four are on it. The tape prints them
        against each other -- account, name, street, city and registration
        with nothing between them -- and the four shift times the same way,
        one blank in front of SHIFT TIME 1 and none after it. Emitting the
        gap once per repeat put six blanks into this section that are not on
        the paper. FIDELITY T2.
        """
        mine = printed_with_blanks(the_site(), "SYSTEM SETUP")
        head = mine.index(station_header()[0])
        self.assertEqual(mine[head:head + len(station_header())],
                         station_header())
        first = next(i for i, l in enumerate(mine)
                     if str(l).startswith("SHIFT TIME 1"))
        self.assertEqual([str(l)[:12] for l in mine[first:first + 4]],
                         ["SHIFT TIME 1", "SHIFT TIME 2", "SHIFT TIME 3",
                          "SHIFT TIME 4"])
        self.assertEqual(str(mine[first - 1]), "")
        self.assertNotEqual(str(mine[first - 2]), "")

    def test_communications_setup(self):
        """The section that cannot be derived, measured anyway.

        T2 said the blocker was the RS-485 card, and that is no longer the
        one: M7 found it as the RJ-45 half of the dual-port Maintenance
        Tracker module 330586-017, and `the_site` reproduces the tape's bay
        of `S-SAT `, `RS-485` and `MTCOMM` under their own numbers. Two
        facts are still missing and neither is on this shelf:

        * What the console prints where a receiver is NOT configured. The
          tape leaves eleven blank lines between CONFIRMATION REPORT: OFF
          and AUTO DIAL TIME SETUP:, and fourteen between RECEIVER REPORTS:
          and RS-232 END OF MESSAGE. D 8 is the only destination the site
          programmed, and no page says what the other seven leave behind.
        * The port settings themselves. The backup covers S501 to S51E, so
          the tape's baud rates, parities, stop bits and data lengths are
          not reproducible from it and the fixture prints its defaults.

        So this is a floor on a section that is not expected to reach the
        paper, kept as a number rather than a shrug. FIDELITY T2.
        """
        got = self.match(tape_lines(125, 251),
                         printed_with_blanks(the_site_with_its_receiver(),
                                             "COMMUNICATIONS SETUP"))
        self.assertGreaterEqual(got, COMMUNICATIONS_BLANKS_FLOOR,
                                f"{got:.1%} against "
                                f"{COMMUNICATIONS_BLANKS_FLOOR:.1%}")


class TheLeakTestPrintAgainstARealOne(unittest.TestCase):
    """The first section to match the paper exactly, blanks included.

    Nine rows and three blank lines, off a CSLD console testing all tanks.
    Two things had to be true before it could: the block prints ONCE for an
    ALL TANK console rather than once per tank position, and the CSLD walk
    ends at the climate factor, so the schedule screens are not on it.
    FIDELITY T4 and T5.
    """

    def a_site_like_the_tape_s(self):
        """CSLD at 95% in a moderate climate, early stop and report only
        off, a normal report. The frequency digit sits at offset 3 of the
        body and 7 is CSLD."""
        c = the_site()
        c.values["S61101"] = "01" + "02" + "0" + "7" + "000000"
        for code, value in (("S61301", "011"),      # Pd = 95%
                            ("S61401", "011"),      # MODERATE
                            ("S61A01", "010"),      # early stop disabled
                            ("S61C01", "010"),      # report only disabled
                            ("S63300", "0")):       # NORMAL
            c.values[code] = value
        return c

    def test_the_block_is_the_paper_s_line_for_line(self):
        tape = tape_lines(433, 446)
        mine = printed_with_blanks(self.a_site_like_the_tape_s(),
                                   "IN-TANK LEAK TEST SETUP")
        self.assertEqual(mine, tape, "\n".join(
            difflib.unified_diff(tape, mine, "tape", "sim", lineterm="")))

    def test_it_is_titled_the_way_the_paper_titles_it(self):
        """The console's function is IN-TANK LEAK TEST SETUP and the report
        heads the block LEAK TEST METHOD. FIDELITY T4."""
        mine = printed_with_blanks(self.a_site_like_the_tape_s(),
                                   "IN-TANK LEAK TEST SETUP")
        self.assertEqual(mine[0], "LEAK TEST METHOD")

    def test_all_tank_prints_one_block(self):
        """576013-623 Rev AN of the lines, and it means it of the tanks:
        "The only difference is that the SINGLE LINE method requires you to
        specify multiple test conditions, one for each line." This printed
        the block once per tank position whatever the method said, over
        screens that read ALL TANK. FIDELITY T5."""
        c = self.a_site_like_the_tape_s()
        self.assertEqual(printer._setup_devices(
            c, [f for f in c.available_functions()
                if f["function"] == "IN-TANK LEAK TEST SETUP"][0]), [1])
        c.set_setting("tank_test_method", "SINGLE TANK", 0)
        self.assertGreater(len(printer._setup_devices(
            c, [f for f in c.available_functions()
                if f["function"] == "IN-TANK LEAK TEST SETUP"][0])), 1)

    def test_the_csld_walk_ends_at_the_climate_factor(self):
        """"If you are setting up the CSLD test frequency for All Tanks,
        the setup is complete." So no start time, no rate, no duration --
        none of which the tape prints either."""
        mine = "\n".join(printed_with_blanks(self.a_site_like_the_tape_s(),
                                             "IN-TANK LEAK TEST SETUP"))
        for gone in ("START TIME", "TEST RATE", "TEST DURATION",
                     "GROSS TEST", "EVAP COMP", "STAGE II VAPOR"):
            self.assertNotIn(gone, mine, gone)


class TheReceiverBlockAgainstARealOne(unittest.TestCase):
    """The autodial destination, which was being printed once per comm port.

    COMMUNICATIONS SETUP is four runs of devices, not one: the port settings
    once per comm board, auto-transmit once for the console, the phone
    directory and auto-dial once per RECEIVER, and the end-of-message once
    for the console again. Walking all four over the comm ports printed the
    receiver block, the auto-transmit block and the end-of-message once a
    board. FIDELITY T4.
    """

    def a_site_like_the_tape_s(self):
        """The tape's only receiver: D 8, a computer on port 1, three
        retries three minutes apart, confirmation off, and no location
        label -- its block opens `D 8:` with nothing after the colon."""
        from tests.test_controls import a_site
        c, _h = a_site()
        for code, value in (("S52108", "081"),      # configured
                            ("S52208", "08"),       # no location label
                            ("S52408", "0803"),     # COMPUTER
                            ("S52508", "081"),      # port 1
                            ("S52608", "083"),      # 3 retries
                            ("S52708", "083"),      # 3 minutes
                            ("S52808", "080")):     # confirmation OFF
            c.values[code] = value
        return c

    def the_block(self, lines):
        start = next(i for i, l in enumerate(lines)
                     if str(l).startswith("RECEIVER SETUP"))
        end = next((i for i, l in enumerate(lines[start:], start)
                    if str(l).startswith("AUTO DIAL")), len(lines))
        return [str(l).rstrip() for l in lines[start:end]]

    def test_the_receiver_block_is_the_paper_s(self):
        want = ["RECEIVER SETUP:", "", "D 8:", "",
                "RCVR TYPE:  COMPUTER", "PORT  NO: 1", "RETRY NO: 3",
                "RETRY DELAY: 3", "CONFIRMATION REPORT: OFF", ""]
        mine = self.the_block(printer.setup_section(
            self.a_site_like_the_tape_s(),
            [f for f in self.a_site_like_the_tape_s().available_functions()
             if f["function"] == "COMMUNICATIONS SETUP"][0]))
        self.assertEqual([x for x in mine if x], [x for x in want if x],
                         "\n".join(difflib.unified_diff(want, mine, "tape",
                                                        "sim", lineterm="")))

    def test_it_prints_once_however_many_ports_there_are(self):
        """Two comm boards in the bay and one receiver programmed: one
        block, not two. And the auto-transmit limits once, not twice."""
        c = self.a_site_like_the_tape_s()
        fn = [f for f in c.available_functions()
              if f["function"] == "COMMUNICATIONS SETUP"][0]
        self.assertGreater(len(printer._setup_devices(c, fn)), 1,
                           "fixture should have more than one comm board")
        out = [str(l) for l in printer.setup_section(c, fn)]
        # twice: once heading the phone directory block and once heading
        # AUTO DIAL ALARM SETUP, which the tape prints as its own titled
        # block after RS-232 END OF MESSAGE
        self.assertEqual(sum(1 for l in out if l.startswith("D 8:")), 2)
        self.assertEqual(out[out.index("AUTO DIAL ALARM SETUP") + 1],
                         printer.SETUP_RULE)
        self.assertLess(out.index("RS-232 END OF MESSAGE"),
                        out.index("AUTO DIAL ALARM SETUP"))
        self.assertIn("- NO ALARM ASSIGNMENTS -", out)
        self.assertEqual(sum(1 for l in out
                             if l == "AUTO LEAK ALARM LIMIT"), 1)
        self.assertEqual(sum(1 for l in out
                             if l == "RS-232 END OF MESSAGE"), 1)

    def test_the_prompts_are_not_values(self):
        """RCVR CONFIG, the phone number, the report list and the dial
        method draw a prompt over what they hold; the tape's block prints
        none of the four."""
        c = self.a_site_like_the_tape_s()
        out = "\n".join(str(l) for l in printer.setup_section(
            c, [f for f in c.available_functions()
                if f["function"] == "COMMUNICATIONS SETUP"][0]))
        for gone in ("RCVR CONFIG", "ENTER RCVR PHONE NO.",
                     "RCVR REPORT LIST", "AUTO DIAL METHOD"):
            self.assertNotIn(gone, out, gone)

    def test_the_auto_dial_time_block_is_the_paper_s(self):
        """The tape's D 8 is called weekly on a Thursday at 5:26 PM:

            AUTO DIAL TIME SETUP:

            D 8:
            DIAL WEEKLY
            THR
            DIAL TIME :  5:26 PM

        52B's method digit decides the width of what follows it, and the
        day is the console's own three letters -- THR, not THU. FIDELITY T4.
        """
        c = self.a_site_like_the_tape_s()
        c.receiver_dial[8] = "441726"            # weekly, Thursday, 17:26
        out = [str(l).rstrip() for l in printer.setup_section(
            c, [f for f in c.available_functions()
                if f["function"] == "COMMUNICATIONS SETUP"][0])]
        start = out.index("AUTO DIAL TIME SETUP:")
        self.assertEqual([x for x in out[start:start + 7] if x],
                         ["AUTO DIAL TIME SETUP:", "D 8:", "DIAL WEEKLY",
                          "THR", "DIAL TIME :  5:26 PM"])

    def test_thursday_is_THR(self):
        """576013-635 Rev AA heads the fuel management report
        "SUN   MON   TUE   WED   THR   FRI   SAT" in seven printouts, and
        the tape prints THR under DIAL WEEKLY. This console said THU."""
        from tls350sim import fieldio
        self.assertIn("THR", fieldio.DAYS)
        self.assertNotIn("THU", fieldio.DAYS)

    def test_the_console_can_be_asked_for_the_port_it_dials(self):
        """Serial 525, "Set Receiver Port Number to Dial", answered with an
        empty body because the console had no such screen to store."""
        from tls350sim.wire import Handler
        c = self.a_site_like_the_tape_s()
        out = Handler(c).handle(b"\x01I52508").decode("ascii", "replace")
        # a display reply closes CR LF ETX, so the last line holds only the
        # ETX; take the last line that has anything on it
        rows = [r for r in out.rstrip("\x03").splitlines() if r.strip()]
        self.assertIn("1", rows[-1])


if __name__ == "__main__":
    unittest.main()
