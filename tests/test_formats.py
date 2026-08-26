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
"""Shape-checking the setup codes that have no field.

Every one of these tests exists because the check got it wrong first, and it
got it wrong in the direction that matters: refusing values a real console
takes. The regression test for this is not in this file -- it is replaying two
real store backups and requiring zero refusals, which is what caught all four
of the mistakes below.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import formats                              # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.wire import Handler, SETTABLE               # noqa: E402

# Real console backups (.vrset) are the sternest test of the format reader:
# a simulator that would refuse to restore what a real console actually
# wrote is not faithful. But a real backup carries a real site's programming
# -- its name, sometimes its address and network -- and none of that belongs
# in a public repository. So backups are NOT committed: drop any .vrset files
# into tests/real_backups/ (gitignored) and this test replays every one; with
# the directory empty it skips. $VR_BACKUPS can point somewhere else instead.
def _backup_dir():
    return (os.environ.get("VR_BACKUPS")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "real_backups"))


def _backups():
    d = _backup_dir()
    if not os.path.isdir(d):
        return []
    return [(os.path.join(d, name), name)
            for name in sorted(os.listdir(d))
            if name.lower().endswith(".vrset")]


def a_console():
    c = Console()
    for card in list(c.modules):
        c.modules[card] = 4
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def refused(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3)).startswith("9999")


class ItCatchesGarbage(unittest.TestCase):
    """The point of the whole thing. fieldio's docstring: "A simulator that
    accepts anything teaches you that a value is fine when the console would
    have rejected it.\""""

    def test_letters_where_digits_belong(self):
        _c, h = a_console()
        self.assertTrue(refused(h, "S50600" + "ZZZZ"))
        self.assertTrue(refused(h, "S50700" + "NONSENSE"))
        self.assertTrue(refused(h, "S61301" + "@@@@"))

    def test_and_the_right_values_still_go_in(self):
        _c, h = a_console()
        self.assertFalse(refused(h, "S50600" + "1"))
        self.assertFalse(refused(h, "S50700" + "25"))
        self.assertFalse(refused(h, "S61301" + "011"))

    def test_a_value_longer_than_its_field(self):
        """On an UNPREFIXED code, where there is only one reading."""
        self.assertTrue(formats.valid("507", "25"))
        self.assertFalse(formats.valid("507", "2599"))
        self.assertTrue(formats.valid("62B", "260821"))

    def test_hex_fields_take_hex_and_decimal_fields_do_not(self):
        self.assertTrue(formats.valid("52E", "1A"))
        self.assertFalse(formats.valid("52E", "1G"))
        self.assertFalse(formats.valid("507", "1A"))


class TheFourMistakesItMade(unittest.TestCase):
    """Each of these refused something a real console takes."""

    def test_a_device_prefixed_set_repeats_its_device(self):
        """S61301 carries "011" -- the tank number, then the flag."""
        self.assertTrue(formats.valid("613", "011"))

    def test_but_not_every_prefixed_code_does(self):
        """S89101 carries just "149". Stripping two characters
        unconditionally eats the "14" out of it, so the prefix is tried and
        not assumed."""
        self.assertTrue(formats.valid("891", "149"))
        _c, h = a_console()
        self.assertFalse(refused(h, "S89101" + "149"))

    def test_a_computer_format_float_is_hex_where_display_is_decimal(self):
        """Same code, same setting: "QQrr.rr" spelled out, "QQFFFFFFFF" as an
        ASCII Hex IEEE float. Checking a lowercase command against the
        uppercase template refuses every float a tool sends."""
        self.assertTrue(formats.valid("775", "0140400000", computer=True))
        self.assertTrue(formats.valid("775", "0103.00", computer=False))
        self.assertFalse(formats.valid("775", "01ZZZZZZZZ", computer=True))

    def test_a_multi_device_00_set_carries_every_device_at_once(self):
        """S61300 holds "011021031" for three tanks. It is not the template
        repeated -- each copy has its device in front."""
        self.assertFalse(formats.valid("613", "011021031"))
        self.assertTrue(formats.valid("613", "011021031", aggregate=True))

    def test_but_a_console_wide_setting_is_always_00_and_is_not_an_aggregate(self):
        """Treating every device 00 as an aggregate stops checking about a
        third of these, S50600 among them."""
        self.assertFalse(formats.valid("506", "ZZZZ", aggregate=False))
        _c, h = a_console()
        self.assertTrue(refused(h, "S50600" + "ZZZZ"))

    def test_the_last_field_may_be_short_and_the_others_may_not(self):
        """A tool sends a four digit volume into a six digit field and a real
        console takes it. Truncating the MIDDLE would shift every field after
        it, so that is still refused."""
        stamp = "2608211200"
        self.assertTrue(formats.valid("7B5", "01" + stamp + "5050"))
        self.assertTrue(formats.valid("7B5", "01" + stamp + "123456"))
        # a letter in the middle shifts nothing and is still plainly wrong
        self.assertFalse(formats.valid("7B5", "01" + "26082A1200" + "5050"))

    def test_a_disabled_time_is_not_a_time(self):
        """"HHmm=Hour, Minute (EE00=Disabled)", the same rule wirelists.py
        keeps for 52B and 75A."""
        self.assertTrue(formats.valid("5E2", "EE00"))
        self.assertTrue(formats.valid("5E2", "1430"))
        self.assertFalse(formats.valid("5E2", "AB00"))


class ItRefusesNothingARealSiteSends(unittest.TestCase):
    """The regression test that found every mistake above. If a change to
    `formats` refuses a line in either of these files, that change is wrong --
    breaking a real backup restore is worse than accepting a value the console
    would have argued with."""

    def replay(self, path):
        c = Console()
        for card in list(c.modules):
            c.modules[card] = 4
        rejected, seen = [], 0
        for line in io.open(path, encoding="utf-8", errors="replace"):
            line = line.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            code, _, hexdata = line.partition("\t")
            code, hexdata = code.strip(), hexdata.strip()
            if not code.startswith("S") or not hexdata:
                continue
            try:
                data = bytes.fromhex(hexdata).decode("latin-1")
            except ValueError:
                continue
            tok, dev = code[1:4], code[4:6]
            if tok not in SETTABLE:
                continue
            seen += 1
            if not formats.valid(tok, data,
                                 aggregate=(dev == "00" and c.is_multi(tok)),
                                 computer=True):
                rejected.append((tok, dev, data[:24]))
        return seen, rejected

    def test_real_backups_replay_clean(self):
        backups = _backups()
        if not backups:
            self.skipTest("no .vrset files in tests/real_backups/ (or "
                          "$VR_BACKUPS); nothing to replay")
        for path, label in backups:
            seen, rejected = self.replay(path)
            self.assertGreater(seen, 100, f"{label} looks empty")
            self.assertEqual(rejected, [], f"{label} would fail to restore")


class WhatTheToleranceCosts(unittest.TestCase):
    """Trying the value both with and without a device prefix cannot refuse
    anything valid, and that is the whole reason it is done. The price is
    that on a PREFIXED code an over-long value can match the shorter reading
    -- "26082199" against 62B's YYMMDD fails whole, then matches after two
    characters come off.

    That is the right way round. This check exists to catch a tool sending
    letters into a digit field, not to police a length the console itself
    may well accept, and the alternative failed to restore a real backup.
    """

    def test_an_overlong_prefixed_value_can_slip_through(self):
        self.assertTrue(formats.valid("62B", "26082199"))

    def test_but_the_character_class_still_holds(self):
        self.assertFalse(formats.valid("62B", "2608ZZ99"))
        self.assertFalse(formats.valid("62B", "ZZ260821"))


class TheFiftyPointChart(unittest.TestCase):
    """63B carries a count and then that many height/volume pairs, so its
    length is in its own data. Only the computer form is checked -- the manual
    spells the display form two different ways and picking one would be a
    guess."""

    PAIR = "01" + "42480000" + "447A0000"

    def test_a_count_that_matches_what_follows(self):
        self.assertTrue(formats.valid("63B", "01" + self.PAIR, computer=True))
        self.assertTrue(formats.valid("63B", "02" + self.PAIR * 2,
                                      computer=True))

    def test_a_count_that_lies(self):
        self.assertFalse(formats.valid("63B", "03" + self.PAIR, computer=True))

    def test_the_add_remove_flag_is_01_or_02(self):
        self.assertFalse(formats.valid("63B", "01" + "09" + "42480000"
                                       + "447A0000", computer=True))

    def test_fourteen_pairs_is_the_documented_maximum(self):
        """"A maximum of 14 pairs can be set per command to avoid
        overflowing the buffer"."""
        self.assertTrue(formats.valid("63B", "14" + self.PAIR * 14,
                                      computer=True))
        self.assertFalse(formats.valid("63B", "15" + self.PAIR * 15,
                                       computer=True))

    def test_the_display_form_is_left_alone(self):
        self.assertTrue(formats.valid("63B", "anything", computer=False))


class WhereItDoesNotGuess(unittest.TestCase):

    def test_an_unknown_code_is_allowed_through(self):
        """Not knowing the shape is not the same as knowing the value is
        wrong."""
        self.assertTrue(formats.valid("ZZZ", "anything at all"))

    def test_the_skipped_ones_are_skipped(self):
        for code in formats.SKIP:
            self.assertFalse(formats.known(code), code)
            self.assertTrue(formats.valid(code, "@@@@@@@@"), code)

    def test_two_of_them_are_skipped_because_a_real_dump_disagrees(self):
        """525 and 52F: the manual writes one width and a live site's console
        holds another. The manual describes the console, not the reverse."""
        self.assertIn("525", formats.SKIP)
        self.assertIn("52F", formats.SKIP)


if __name__ == "__main__":
    unittest.main()
