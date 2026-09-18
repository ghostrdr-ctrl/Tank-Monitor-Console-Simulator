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
"""The console emulation, against a real TLS-350.

Everything else in this suite checks the simulator against the manuals. This
checks it against hardware: a console running software 326.01, reached
through its TCP/IP card, asked every inquiry code and recorded verbatim.

It needs a capture, which takes a console. `tools/capture_console.py` makes
one:

    python tools/capture_console.py 172.30.9.14 --out tests/console_capture

The capture is not committed -- it is the console's own output, the same rule
the manuals under `reference/` follow -- so this skips without it. A green
suite on a machine with no console therefore proves less than one on a
machine with a console, which is worth remembering before trusting it.

The captured console had NO programming: no tanks, no sensors, nothing
configured. That still settles a great deal -- the framing of every reply,
the checksum, which function codes exist at all, and what the console says
when asked about equipment it does not have -- and it settles those better
than a programmed one would, because none of it depends on a particular
site's setup.
"""
import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.wire import checksum

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.environ.get("CONSOLE_CAPTURE") or os.path.join(
    HERE, "console_capture")

SOH = b"\x01"
ETX = b"\x03"


def files(sub):
    return sorted(glob.glob(os.path.join(CAPTURE, sub, "*.bin")))


def have(sub):
    return bool(files(sub))


@unittest.skipUnless(have("raw"),
                     "no console capture in %s; run tools/capture_console.py "
                     "against a console to make one" % CAPTURE)
class TheFraming(unittest.TestCase):
    """How a display reply is wrapped, measured rather than argued."""

    @classmethod
    def setUpClass(cls):
        cls.replies = []
        for path in files("raw"):
            with open(path, "rb") as f:
                data = f.read()
            if len(data) >= 16 and b"9999FF" not in data:
                cls.replies.append((os.path.basename(path)[:-4], data))

    def test_the_capture_has_something_in_it(self):
        self.assertGreater(len(self.replies), 50,
                           "a capture with almost nothing in it proves "
                           "nothing; re-run tools/capture_console.py")

    def test_every_display_reply_opens_soh_cr_lf(self):
        """The manuals draw a report's text, not its framing."""
        bad = [c for c, d in self.replies if not d.startswith(b"\x01\r\n")]
        self.assertEqual(bad, [], "replies not starting SOH CR LF: %s" % bad)

    def test_every_display_reply_closes_cr_lf_etx(self):
        bad = [c for c, d in self.replies if not d.endswith(b"\r\n\x03")]
        self.assertEqual(bad, [], "replies not ending CR LF ETX: %s" % bad)

    def test_the_emulator_frames_a_reply_the_same_way(self):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        out = Handler(Console(None), verbose=False).handle(b"\x01I90100")
        self.assertTrue(out.startswith(b"\x01\r\n"))
        self.assertTrue(out.endswith(b"\r\n\x03"))

    def test_the_second_line_is_the_echoed_code(self):
        for code, data in self.replies[:40]:
            line = data.split(b"\r\n")[1].decode("latin-1")
            self.assertEqual(line, code)


@unittest.skipUnless(have("raw"), "no console capture")
class AConsoleWithNothingProgrammed(unittest.TestCase):
    """What a console says when it has no equipment configured.

    The manuals only ever draw a site that HAS tanks, so none of this could
    be settled from paper. The captured console had four tank positions, all
    reading OFF, and answered accordingly.
    """

    def real(self, code):
        path = os.path.join(CAPTURE, "raw", code + ".bin")
        if not os.path.exists(path):
            self.skipTest("%s not in the capture" % code)
        with open(path, "rb") as f:
            return f.read()

    def sim(self, code):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        return Handler(Console(None), verbose=False).handle(
            ("\x01" + code).encode())

    def body(self, data):
        return data.split(b"\r\n")[3:]

    def test_a_tank_report_has_an_empty_body(self):
        """Not even the column header, and no invented tank 1.

        The simulator used to fall back to tank 1 whenever nothing was
        programmed, so it reported fuel in a console that had none.
        """
        self.assertEqual(self.real("I20100"), b"\x01\r\nI20100\r\n"
                         + self.real("I20100").split(b"\r\n")[2] + b"\r\n\r\n"
                         + b"\x03")
        self.assertEqual(self.body(self.sim("I20100")),
                         self.body(self.real("I20100")))

    def test_a_report_with_no_body_carries_no_header_block_either(self):
        """I20100 on a console with no tanks: code, stamp, and nothing.

        The header block belongs to a report, not to a reply. With no tanks
        there is no report, so there is no header block to hang it on.
        """
        sim = self.sim("I20100").decode("latin-1")
        after_stamp = sim.split("\r\n")[3:]
        self.assertEqual([r for r in after_stamp if r not in ("", "\x03")], [])
        self.assertEqual(self.body(self.sim("I20100")),
                         self.body(self.real("I20100")))

    def test_a_report_that_has_a_body_carries_four_blank_header_lines(self):
        """Even with none programmed. FIDELITY W5 was right after all.

        A first reading of this bench said a console with no header sends
        none. That came from I20100, which sends nothing for the different
        reason above. I10100 has a body, and on the same console it carries
        four blank lines before SYSTEM STATUS REPORT.
        """
        rows = self.real("I10100").decode("latin-1").split("\r\n")[3:7]
        self.assertEqual(rows, ["", "", "", ""])
        self.assertIn("SYSTEM STATUS REPORT",
                      self.real("I10100").decode("latin-1"))
        sim = self.sim("I10100").decode("latin-1").split("\r\n")[3:7]
        self.assertEqual(sim, ["", "", "", ""])

    def test_a_programmed_header_reaches_a_report_that_has_a_body(self):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console(None)
        c.values["S50301"] = "GREENFIELD SERVICE  "
        out = Handler(c, verbose=False).handle(b"\x01I10100")
        rows = out.decode("latin-1").split("\r\n")
        # [3] is the blank line the manual draws between the stamp and the
        # header block -- 128 samples out of 128, and the six blank lines a
        # real console sends before SYSTEM STATUS REPORT with nothing
        # programmed. FIDELITY S6.
        self.assertEqual(rows[3], "")
        self.assertEqual(rows[4].strip(), "GREENFIELD SERVICE")
        self.assertEqual(rows[5:8], ["", "", ""])


@unittest.skipUnless(have("computer"), "no computer-format capture")
class TheChecksum(unittest.TestCase):
    """The computer format's checksum, against the console's own."""

    def test_the_emulators_checksum_matches_the_consoles(self):
        checked = 0
        for path in files("computer"):
            with open(path, "rb") as f:
                data = f.read()
            if b"&&" not in data:
                continue
            body, rest = data.rsplit(b"&&", 1)
            want = rest.rstrip(ETX).decode("ascii").upper()
            self.assertEqual(checksum(body + b"&&").upper(), want,
                             os.path.basename(path))
            checked += 1
        self.assertGreater(checked, 0, "no checksummed replies in the capture")

    def test_a_computer_reply_does_not_carry_the_display_framing(self):
        for path in files("computer"):
            with open(path, "rb") as f:
                data = f.read()
            if not data:
                continue
            self.assertFalse(data.startswith(b"\x01\r\n"),
                             "%s: computer format has no CR LF after the SOH"
                             % os.path.basename(path))


@unittest.skipUnless(have("raw"), "no console capture")
class TheTitleEveryReportOpensWith(unittest.TestCase):
    """The first line of every captured reply, against this emulator's.

    `wiretitles.json` is read off the serial manual's own Display samples,
    and the manual is not the last word. Two of these titles disagree with
    the page: 60C is `TANK  STICK HEIGHT OFFSET` with two spaces where p.259
    sets one, and 530 is `SYSTEM BEEPER` with the value on its own line
    where p.204 prints the single line `BEEPER: ENABLED`.

    Both were known before this test existed, and one of them had been fixed
    BY HAND in the generated file -- so the next run of
    `tools/build_wire_titles.py` quietly put the manual's version back and
    nothing failed. The corrections live in `tools/console_corrections.json`
    now, and this is the guard that would have noticed either way.

    Only the codes where BOTH sides draw a body are compared. A console with
    no smart sensors in it says nothing about the title of a smart sensor
    report, and the captured console had almost no equipment: the "one
    answered and the other did not" pairs are a question about which report
    a bare console draws at all, which is a different question from what the
    report is CALLED.
    """

    # Site data, not defects: the captured console had these programmed and
    # a console out of the box has not, and the day count is part of the
    # title line on these six. 576013-623 Rev AN Table C-1 gives every one
    # of them a default of "Disabled" and no number at all, so there is no
    # number for an unprogrammed console to print but zero.
    #
    # 507 and 508 are 547 and 548 under their older names, and the captured
    # console prints the same 25 and 30 for each pair -- which is how the
    # pairing was found. See FIDELITY S17.
    # The six periodic-test day counts were here, because a one-line report
    # IS its own title: `PERIODIC TEST WARNING: DAYS =  25` is the whole
    # body. They were read as the captured console's programming and were
    # really the factory defaults this console had never been given, so all
    # six now match. See FIDELITY S18.
    PROGRAMMED = set()
    # I50400 was here: the console answers it with `232 SECURITY CODE` over
    # a six-row PORT/SECURITY CODE/STATUS table, which is the report the same
    # manual draws for 536, and this emulator answered 576013-635's own
    # `SYSTEM SECURITY CODE` over a single `CODE : 000000`. It does the
    # console's now. See FIDELITY S14.
    OPEN = set()

    @classmethod
    def setUpClass(cls):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        cls.handler = Handler(Console(None), verbose=False)

    @staticmethod
    def first(data):
        """The report's title: the first line after the framing, or ""."""
        parts = data.replace(ETX, b"").split(b"\r\n")
        for line in parts[3:]:
            if line.strip():
                return line.decode("latin-1").rstrip()
        return ""

    def pairs(self):
        for path in files("raw"):
            code = os.path.basename(path)[:-4]
            with open(path, "rb") as fh:
                data = fh.read()
            if b"9999FF" in data or len(data) < 16:
                continue          # the console refused: it has no such card
            mine = self.handler.handle(SOH + code.encode())
            real, got = self.first(data), self.first(mine)
            if not real or not got:
                continue          # only one of the two drew a body
            yield code, real, got

    def test_every_report_both_sides_draw_opens_with_the_same_title(self):
        wrong = [(code, real, got) for code, real, got in self.pairs()
                 if real != got and code not in self.PROGRAMMED | self.OPEN]
        self.assertEqual(wrong, [], "a report is called something else on a "
                         "real console")

    def test_the_measure_has_something_to_measure(self):
        self.assertGreater(len(list(self.pairs())), 50,
                           "almost nothing was compared; re-run "
                           "tools/capture_console.py")

    def test_the_ones_left_open_are_still_open(self):
        """Both directions, so one that starts matching leaves the list in
        the same commit rather than sitting here as a record of what WAS
        wrong."""
        codes = {code for code, real, got in self.pairs() if real != got}
        self.assertEqual(sorted(codes & self.OPEN), sorted(self.OPEN))
        self.assertEqual(sorted(codes & self.PROGRAMMED),
                         sorted(self.PROGRAMMED))


@unittest.skipUnless(have("raw"), "no console capture")
class TheWholeBodyAndNotJustTheTitle(unittest.TestCase):
    """Every line of every captured reply, against this emulator's.

    `TheTitleEveryReportOpensWith` compares one line. This compares all of
    them, and it is a stronger measure for one reason: a report can open with
    the right title and be wrong in every row under it. Nine defects came out
    of the first run of this comparison -- the ones FIDELITY S18 lists --
    and none of them moved the title measure at all.

    **What it can and cannot say.** A code is compared only where BOTH
    consoles drew a body, and what is left over then divides three ways:

    * the captured console had EQUIPMENT this one does not (a probe card,
      three pressure lines, a modem, two comm boards), so a report about a
      card is a fixture difference;
    * the captured console had SITE DATA -- a retry count, a delivery delay,
      an alarm in its history, four months of uptime -- and a console out of
      the box has none of it;
    * and the rest are defects.

    The floor is what this asserts. `MATCHED` only ever goes up, and the two
    lists below say which of the differences have been READ and what each one
    was found to be. A difference not on either list fails, whichever
    direction it appeared from.
    """

    #: Codes where the two bodies differ because the captured console had
    #: something PROGRAMMED and a console out of the box has not.
    SITE_DATA = {
        # The six periodic-test day counts USED to be here, read as that
        # console's own programming. They are not: 25 and 30 are what a
        # TLS-350 ships with, and the giveaway was that a console with no
        # tanks, no labels and no sensors was somehow carrying a pair of
        # hand-set day counts five apart. Giving the six fields the default
        # they had never had made all six match byte for byte, which is the
        # evidence. See FIDELITY S18.
        #
        # **And it has happened twice.** `I52600` -- the dial retry number
        # -- and `I61000` -- the delivery delay -- were on this list for
        # the same reason and were the same mistake: three retries and one
        # minute are what the manual says those fields ARE out of the box,
        # "enter a number between 3 and 99" and p.7-25's own
        # `DELIVERY DELAY: 01`. This console drew `0` for both, which is a
        # value neither field's validation would take, and giving them the
        # default made both match the captured console byte for byte. That
        # is two more capture differences that were never site data. See
        # CLOSED U43 and the setup-mode audit's SU31.
        #
        # **And three more on 2026-09-15.** `I52700`'s three-minute retry
        # delay and `I62500` and `I62600`'s limits of 99 are defaults too,
        # and a SECOND real console says so: the site tape prints
        # `RETRY DELAY: 3`, `LEAK ALARM LIMIT:     99` and
        # `SUDDEN LOSS LIMIT:    99`. A retry delay of 0 and a leak alarm
        # limit of 0 are values their own pages will not take -- 1 to 60
        # minutes, 1 to 99 gallons. See FIDELITY S18.
        #
        # its autodial receivers' port, 2 on all eight. It is not a factory
        # number: 576013-623 Rev AN p.6-8 makes it the comm-bay slot the
        # modem is in and draws `SELECT MODEM: 3`, and the site tape's one
        # receiver reads `PORT  NO: 1`.
        "I52500",
        # reconciliation limits of 0 and 1, where 576013-623 Rev AN states
        # "the default warning limit of 3" and "the default alarm limit of
        # 4", each with a minimum of 1
        "I63400", "I63500",
        # software 326.01 built in 2006, against this console's 333.02; and
        # 903's five counters, which count that console's own uptime
        "I90200", "I90300", "I90500",
        # two BATTERY IS OFF records in its alarm history. The ROW is
        # compared -- `Console.alarm_row` draws it character for character
        # now -- but the records themselves are that console's.
        "I11100", "I11400",
    }

    #: Codes where the two bodies differ because the captured console had
    #: CARDS this one does not. The 642 refusals are that console's parts
    #: list, and a fixture built from them is what would close these.
    FIXTURE = {
        "I10200",                    # a 4 PROBE / G.T. and a PLLD pair
        "I88800",                    # an RS-232 board and an S-SAT board
        "I61300", "I61400", "I61800",  # CSLD, which its S-Module did not
    }

    @classmethod
    def setUpClass(cls):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        cls.handler = Handler(Console(None), verbose=False)

    #: What matched when this was written. A floor, never a target.
    MATCHED = 70

    @staticmethod
    def lines(data):
        """The body: every line after the echoed code and the stamp."""
        import re
        stamp = re.compile(r"^[A-Z]{3} [ 0-9]?\d, \d{4} [ 0-9]?\d:\d\d [AP]M$")
        out = data.replace(ETX, b"").replace(SOH, b"").decode(
            "latin-1").split("\r\n")
        while out and not out[0].strip():
            out.pop(0)
        if out:
            out.pop(0)
        if out and stamp.match(out[0].strip()):
            out.pop(0)
        while out and not out[-1].strip():
            out.pop()
        return [one.rstrip() for one in out]

    def pairs(self):
        """(code, the console's body, ours) for every code both answered."""
        for path in files("raw"):
            code = os.path.basename(path)[:-4]
            with open(path, "rb") as fh:
                data = fh.read()
            if b"9999FF" in data or len(data) < 16:
                continue          # the console refused: it has no such card
            real = self.lines(data)
            mine = self.lines(self.handler.handle(SOH + code.encode()))
            if real and mine:
                yield code, real, mine

    def test_every_line_of_every_report_both_sides_draw(self):
        wrong = sorted(code for code, real, mine in self.pairs()
                       if real != mine)
        self.assertEqual([c for c in wrong
                          if c not in self.SITE_DATA | self.FIXTURE], [],
                         "a report reads differently on a real console")

    def test_the_ones_left_open_are_still_open(self):
        """Both directions, so a code that starts matching leaves the list in
        the same commit rather than sitting here as a record of what WAS
        wrong."""
        wrong = {code for code, real, mine in self.pairs() if real != mine}
        named = self.SITE_DATA | self.FIXTURE
        self.assertEqual(sorted(named - wrong), [],
                         "these match now: take them off the list and say so "
                         "in FIDELITY S18")

    def test_the_measure_does_not_fall(self):
        same = sum(1 for _code, real, mine in self.pairs() if real == mine)
        self.assertGreaterEqual(same, self.MATCHED,
                                "fewer reports match the console than did")

    def test_the_autodial_report_reads_as_the_console_reads_it(self):
        """`I52000` with the card in, which the measure above cannot reach.

        The comparison runs on a console with an empty cage, so 520 is a
        refusal there and never compared. Fit the modem the captured console
        had and it is comparable, and it was wrong in three ways: the rows
        were drawn with both value columns EMPTY, because a receiver nobody
        has autodialled from had no stored dial spec and the console is not
        blank there; the date was `01/16/06` where a console writes
        `JAN 16, 2006`; and START TIME sat at 41, which is where p.198's
        SAMPLE puts it and not where its own heading does.

        The stamp is this console's date and the captured one's is its own,
        so the date CELL is rewritten before comparing and nothing else is.
        See FIDELITY S18.
        """
        import re
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        console = Console(None)
        console.modules["modem"] = True
        console.tick()
        mine = self.lines(Handler(console, verbose=False).handle(
            SOH + b"I52000"))
        with open(os.path.join(CAPTURE, "raw", "I52000.bin"), "rb") as fh:
            real = self.lines(fh.read())
        today = re.compile(r"[A-Z]{3} [ 0-9]\d, \d{4}")
        self.assertEqual([today.sub("<DATE>", one) for one in mine],
                         [today.sub("<DATE>", one) for one in real])


@unittest.skipUnless(have("at"), "no capture of the @ reports")
class TheUndocumentedReports(unittest.TestCase):
    """The five "@" reports the Troubleshooting Guide names by hand."""

    def read(self, name):
        path = os.path.join(CAPTURE, "at", name + ".bin")
        if not os.path.exists(path):
            self.skipTest("%s not in the capture" % name)
        with open(path, "rb") as f:
            return f.read()

    def test_they_have_a_computer_format_after_all(self):
        """UNKNOWNS A10 said they did not. The console says otherwise."""
        data = self.read("computer_IatA900")
        self.assertTrue(data.startswith(b"\x01i@a900"))
        self.assertIn(b"&&", data)

    def test_the_echoed_code_keeps_the_case_it_was_sent_in(self):
        """i@a900 comes back i@a900, not i@A900.

        The emulator folded the token to upper case for dispatch and echoed
        the folded form. It shows only on these codes, because every other
        one is typed in a single case anyway.
        """
        self.assertTrue(self.read("computer_IatA900").startswith(b"\x01i@a900"))
        self.assertTrue(self.read("display_IatA900").startswith(b"\x01\r\nI@A900"))

    def sim(self, code):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        return Handler(Console(None), verbose=False).handle(
            ("\x01" + code).encode())

    def rows(self, data):
        return [r for r in data.decode("latin-1").split("\r\n")[3:]
                if r not in ("", "\x03")]

    def test_the_asr_buffer_is_titled_and_bodied_as_the_console_does(self):
        """FIDELITY S11: the title was a word short and the body was wrong.

        It read "NO ALARM HISTORY" -- W13's invented phrase, in a report
        that is not an alarm history. The console prints a column header and
        the single word EMPTY.
        """
        real = self.rows(self.read("display_IatA900"))
        self.assertEqual(real[0], "ASR ERROR EVENT HISTORY BUFFER")
        self.assertEqual(real[-1], "EMPTY")
        self.assertEqual(self.rows(self.sim("I@A900")), real)

    def test_the_meter_map_prints_the_ballot_not_the_result(self):
        """FIDELITY S11: this printed FP/METER/TANK/THROUGHPUT.

        That is what the mapping arrived at. The report exists to show it
        working -- the guide's first instruction under it is "look for
        unmapped or retired meters", and neither can appear in a result
        table.
        """
        real = self.rows(self.read("display_IatA002"))
        self.assertIn("**TANK_MAP_BALLOT**", real[1])
        self.assertEqual(self.rows(self.sim("I@A002")), real)

    def test_the_meter_map_answers_without_bir(self):
        """The captured console lists no BIR and answers it anyway."""
        self.assertNotIn(b"9999FF", self.sim("I@A002"))

    def test_the_emulator_echoes_the_case_it_was_sent_too(self):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        h = Handler(Console(None), verbose=False)
        self.assertTrue(h.handle(b"\x01i@a900").startswith(b"\x01i@a900"))


if __name__ == "__main__":
    unittest.main()
