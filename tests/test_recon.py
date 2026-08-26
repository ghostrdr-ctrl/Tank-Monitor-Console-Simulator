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
"""Sections 7.5 and 7.6: reconciliation and variance analysis reports."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, recon                       # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_site(hours=15):
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for _ in range(hours * 4):
        c.clock_offset += 900
        c.tick()
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def lines(h, cmd):
    """Every printed line of a reply.

    The frame separates its own parts with CR LF and the report inside it
    separates its rows with LF alone, so splitting on one of them leaves the
    whole report sitting in a single element.
    """
    body = send(h, cmd).strip(chr(1) + chr(3)).replace(chr(13) + chr(10),
                                                       chr(10))
    return body.split(chr(10))


class TheReportTypeFieldHasTwoBases(unittest.TestCase):
    """The trap in this block. `tt` is the report type on seven of these and
    it is NOT one enumeration: C07 and C08 number it from zero, and C10, C11,
    C20, C21 and C25 number it from one. Same field letter, same meaning, two
    bases, and a shared table would read every C07 "current" as a C10
    "previous".
    """

    def test_c07_numbers_from_zero(self):
        self.assertIs(recon.previous_wanted("C07", "00"), False)
        self.assertIs(recon.previous_wanted("C07", "01"), True)

    def test_c10_numbers_from_one(self):
        self.assertIs(recon.previous_wanted("C10", "01"), False)
        self.assertIs(recon.previous_wanted("C10", "02"), True)

    def test_the_same_digit_means_opposite_things(self):
        """01 is PREVIOUS on C07 and CURRENT on C10."""
        self.assertIs(recon.previous_wanted("C07", "01"), True)
        self.assertIs(recon.previous_wanted("C10", "01"), False)

    def test_a_value_outside_a_codes_own_base_is_refused(self):
        self.assertIsNone(recon.previous_wanted("C07", "02"))
        self.assertIsNone(recon.previous_wanted("C10", "00"))

    def test_the_wire_refuses_it_too(self):
        _c, h = a_site()
        self.assertIn("9999", send(h, "IC070002"))
        self.assertIn("9999", send(h, "IC100000"))
        self.assertNotIn("9999", send(h, "IC070001"))
        self.assertNotIn("9999", send(h, "IC100002"))

    def test_the_ones_with_no_selector_take_none(self):
        """C05 and C06 have no command notes at all -- always current."""
        for tok in ("C05", "C06"):
            self.assertIsNone(recon.RECON[tok]["select"])
            self.assertIs(recon.previous_wanted(tok, ""), False)


class RowAndColumnAreTwoLayouts(unittest.TestCase):
    """Not two names for one report -- which is what they were."""

    def test_the_shift_pair_no_longer_answer_identically(self):
        """C03 and C04 returned byte-identical text before this."""
        _c, h = a_site()
        row = send(h, "IC0300").split(chr(13) + chr(10), 2)[-1]
        col = send(h, "IC0400").split(chr(13) + chr(10), 2)[-1]
        self.assertNotEqual(row, col)

    def test_the_column_form_writes_labels_down_the_page(self):
        _c, h = a_site()
        col = send(h, "IC0200")
        for label in ("OPENING DATE", "OPENING VOLUME", "METERED SALES",
                      "CLOSING DATE", "CLOSING TIME"):
            self.assertIn(label, col, label)

    def test_the_row_form_does_not(self):
        _c, h = a_site()
        row = send(h, "IC0100")
        self.assertNotIn("OPENING VOLUME", row)
        self.assertIn("DATE TIME", row)

    def test_only_the_periodic_column_report_carries_a_threshold(self):
        """C06 and C08 print it; C02 does not."""
        _c, h = a_site()
        self.assertNotIn("THRESHOLD", send(h, "IC0200"))
        self.assertIn("THRESHOLD", send(h, "IC0600"))


class EveryTankGetsItsOwnFigures(unittest.TestCase):
    """A periodic report lists every tank and each has its own days. Sharing
    one tank's rows printed tank 1's figures four times under four labels."""

    def test_the_book_variance_rows_differ_per_tank(self):
        c, h = a_site()
        opens = {}
        for tank in sorted(c.tank_level):
            opens[tank] = c.bir.row(tank, "daily")["opening"]
        self.assertGreater(len(set(opens.values())), 1, "the preset differs")
        # a data row carries the "0=  0.00%" variance cell; the station
        # header lines above it do not
        got = [l for l in lines(h, "IC1000") if "=" in l and "%" in l]
        self.assertEqual(len(got), len(opens), got)
        openings = {l.split()[3] for l in got}
        self.assertGreater(len(openings), 1, got)


class TheFourFamilies(unittest.TestCase):

    def test_every_code_answers_in_both_formats(self):
        _c, h = a_site()
        for tok in recon.RECON:
            self.assertNotIn("9999", send(h, "I" + tok + "00"), tok)
            self.assertNotIn("9999", send(h, "i" + tok + "00"), tok)

    def test_all_fourteen_are_inquire_only(self):
        """There is no Set among them, and nothing in the C range closes or
        clears a period -- closing a shift is 79D."""
        _c, h = a_site()
        for tok in recon.RECON:
            self.assertIn("9999", send(h, "S" + tok + "00"), tok)

    def test_they_all_want_the_bir_key(self):
        c, h = a_site()
        c.software.pop("bir", None)
        for tok in recon.RECON:
            self.assertIn("9999", send(h, "I" + tok + "00"), tok)

    def test_the_book_family_reports_the_book_inventory(self):
        """Not the gauged deliveries -- so a ticket that never arrived shows
        up as variance instead of vanishing."""
        c, _h = a_site()
        row = c.bir.row(1, "daily")
        got = c.bir.book_figures(row)
        self.assertEqual(len(got), 9)
        self.assertAlmostEqual(got[4], c.bir.book(row), places=3)

    def test_the_analysis_family_carries_nine_and_two_masks(self):
        c, h = a_site()
        row = c.bir.row(1, "daily")
        self.assertEqual(len(c.bir.analysis_figures(row)), 9)
        body = send(h, "iC2000").strip(chr(1) + chr(3)).split("&&")[0]
        self.assertIn("00000000", body, "the two bit-encoded tank masks")

    def test_the_analysis_float_order_is_the_notes_order_not_the_column_order(self):
        """The manual's notes give book, delivery, sales, percent, TEMPERATURE,
        water, unexplained -- while its printed header reads
        BOOK DLVY SALES BK_VAR% MTR TEMP VAP WATER UNEX. They are not the same
        sequence and the wire follows the notes.
        """
        c, _h = a_site()
        row = c.bir.row(1, "daily")
        a = c.bir.analysis(row)
        f = c.bir.analysis_figures(row)
        self.assertAlmostEqual(f[3], a["book_pct"], places=6)
        self.assertAlmostEqual(f[4], a["temp_var"], places=6)
        self.assertAlmostEqual(f[6], a["unexplained"], places=6)

    def test_c09_is_keyed_by_tank_and_not_by_product(self):
        """The only one of the fourteen that is."""
        _c, h = a_site()
        shown = send(h, "IC0901")
        self.assertIn("INDIVIDUAL BASIC RECONCILIATION HISTORY", shown)
        self.assertIn("STRT TIME", shown)

    def test_c09_takes_a_delivery_source_flag(self):
        """"D - If 1, will use ticketed delivery else ... gauged delivery"."""
        _c, h = a_site()
        self.assertNotIn("9999", send(h, "IC09011"))
        self.assertNotIn("9999", send(h, "IC0901"))


class ThePeriodsThemselvesAreReal(unittest.TestCase):
    """NOTES said the daily and periodic periods on top of the shift were not
    modelled. They are -- and were before any of this."""

    def test_all_four_periods_accumulate(self):
        c, _h = a_site()
        for kind in ("shift", "daily", "weekly", "periodic"):
            row = c.bir.row(1, kind)
            self.assertIsNotNone(row, kind)
            self.assertEqual(row["kind"], kind)

    def test_the_day_opened_before_the_shift_it_contains(self):
        c, _h = a_site()
        self.assertLessEqual(c.bir.row(1, "daily")["opened"],
                             c.bir.row(1, "shift")["opened"])


if __name__ == "__main__":
    unittest.main()
