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
"""Sections 7.5 and 7.6: reconciliation and variance analysis reports."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import struct                                              # noqa: E402

from tls350sim import bir, presets, printer, recon         # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.meterid import meter_key                    # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402
from tests.test_console import start_at                    # noqa: E402


def a_site(hours=15, at=10):
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    # Pinned, because a fixture that runs the clock forward from whenever
    # the suite happens to be run is a fixture that changes shape at a
    # particular time of day: fifteen hours from 15:00 crosses the site's
    # 06:00 daily close where fifteen hours from 14:00 does not, and the
    # reports below then carry an extra row per tank. 's own
    # docstring names this failure mode about midnight; it is not only
    # midnight.
    start_at(c, at)
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
    body = send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10)).replace(chr(13) + chr(10),
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
        body = send(h, "iC2000").strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0]
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


class BothEndsOfAShiftAreTheRowsOwnNumbers(unittest.TestCase):
    """FIDELITY X4. I204 prints VOLUME, TC VOLUME, ULLAGE, HEIGHT, WATER and
    TEMP against STARTING VALUES and again against ENDING VALUES.

    Three of the six came off the stored row and three did not: the TC volume
    and the temperature were the LIVE tank at both ends, so a shift that sold
    anything printed its opening volume beside the closing volume's
    correction. 576013-635 Rev AA p.65's sample cannot catch it -- nothing
    moved during that shift, and both of its rows read `8518 8492 1482 76.26
    0.00 64.57`.
    """

    def a_shift_that_moved(self):
        c, h = a_site()
        row = h._shift_rows(1)[-1]
        c.tank_level[1]["volume"] = row["opening"] - 2518.0
        c.tick()
        return c, h, h._shift_rows(1)[-1]

    def test_the_two_ends_are_two_different_corrections(self):
        c, h, row = self.a_shift_that_moved()
        start = h._gauges(1, row, "opening")
        end = h._gauges(1, row, "physical")
        self.assertNotEqual(row["opening"], row["physical"])
        self.assertEqual(start[0], row["opening"])
        self.assertEqual(end[0], row["physical"])
        self.assertAlmostEqual(
            start[2], c.tc_volume_at(1, row["opening"], row["temp_open"]), 6)
        self.assertAlmostEqual(
            end[2], c.tc_volume_at(1, row["physical"], row["temp_close"]), 6)
        self.assertNotEqual(round(start[2]), round(end[2]))

    def test_each_end_carries_its_own_temperature(self):
        _c, h, row = self.a_shift_that_moved()
        self.assertEqual(h._gauges(1, row, "opening")[5], row["temp_open"])
        self.assertEqual(h._gauges(1, row, "physical")[5], row["temp_close"])

    def test_a_shift_that_moved_nothing_still_prints_one_pair_of_rows(self):
        """Which is the manual's sample, and the reason this went unseen."""
        c, h = a_site()
        row = h._shift_rows(1)[-1]
        row = dict(row, physical=row["opening"], water=row["water_open"],
                   temp_close=row["temp_open"])
        start = h._gauges(1, row, "opening")
        end = h._gauges(1, row, "physical")
        self.assertEqual(start, end)
        del c


class TheDayAReportCovers(unittest.TestCase):
    """FIDELITY Q1. "To select a different date, press CHANGE, enter the
    date, and then press ENTER" -- so a daily report can be asked for a day
    that is not the one in progress, and the date meant is the CLOSING date:
    p.28-20 asks for "the desired closing date for the adjustment"."""

    def a_console_two_days_on(self):
        """The site's own daily close, run twice, rather than two by hand.

        Closing a day by hand mid-morning leaves TWO rows carrying the same
        closing date -- the hand one and the scheduled one -- and the whole
        point of a closing date is that it names one row. A site closes its
        day once, which is the case worth testing.
        """
        c, _h = a_site()
        for _ in range(2 * 96):                 # two days of quarter hours
            c.clock_offset += 900
            c.tick()
        rows = c.bir.closed[(1, "daily")]
        days = {time.strftime("%Y%m%d", time.localtime(r["closed"]))
                for r in rows}
        self.assertGreaterEqual(len(days), 2, rows)
        self.assertEqual(len(days), len(rows), "a day closed twice")
        return c, rows

    def test_a_day_picks_the_row_that_closed_on_it(self):
        c, rows = self.a_console_two_days_on()
        today = time.strftime("%Y%m%d", c.now())
        for row in rows:
            if time.strftime("%Y%m%d", time.localtime(row["closed"])) == today:
                continue                        # the day in progress owns it
            self.assertIs(c.bir.row_on(1, row["closed"]), row)

    def test_today_is_the_day_still_in_progress(self):
        c, rows = self.a_console_two_days_on()
        running = c.bir.row_on(1, time.mktime(c.now()))
        self.assertIsNotNone(running)
        self.assertNotIn(running, rows)

    def test_a_day_this_console_no_longer_holds_has_no_row(self):
        """The report prints its own no-data line for it, which is what it
        already did for a console that had never closed a period."""
        c, rows = self.a_console_two_days_on()
        self.assertIsNone(c.bir.row_on(1, rows[-1]["closed"] - 30 * 86400))

    def test_the_report_follows_the_day_it_was_given(self):
        c, rows = self.a_console_two_days_on()
        oldest = rows[-1]
        paper = chr(10).join(printer.reconcile(c, [1], "daily", False,
                                               oldest["closed"]))
        self.assertIn("DAILY RECONCILIATION", paper)
        self.assertNotIn("NO SHIFT DATA AVAILABLE", paper)
        # A day this console no longer holds prints its tank block and no
        # row under it. It used to print `NO SHIFT DATA AVAILABLE`, which
        # is one of the inventions UNKNOWNS A17 retired by name in another
        # report -- and FIDELITY S18 settled what an empty report does from
        # a capture: the heading, and nothing. See FIDELITY U5.
        blank = chr(10).join(printer.reconcile(
            c, [1], "daily", False, oldest["closed"] - 30 * 86400))
        self.assertIn("DAILY RECONCILIATION", blank)
        self.assertIn("T 1:", blank)
        self.assertNotIn("STARTING VALUES", blank)


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


class EachTankRecordsItsOwnTemperature(unittest.TestCase):
    """FIDELITY G2: `close()` iterated `for one in tanks:` and then read
    `product_temperature(tank)` -- its own PARAMETER, which is None on every
    scheduled close -- so all four tanks recorded one shared wrong value.

    It fed TEMP VAR, "change in volume related to change in temperature"
    (576013-610 p.28-14), and through it UNEX VAR. And it failed the worse
    way round: `current()` uses its real parameter and was right, so the
    CURRENT period's analysis was correct and the PREVIOUS period's -- the
    one a technician prints to investigate a variance -- was not.
    """

    def test_a_scheduled_close_gives_each_tank_its_own(self):
        c, _h = a_site()
        rows = c.bir.close("shift")
        self.assertGreater(len(rows), 1, "the preset has several tanks")
        want = [c.product_temperature(t) for t in sorted(c.tank_level)]
        self.assertEqual([row["temp_close"] for row in rows], want)
        self.assertGreater(len(set(row["temp_close"] for row in rows)), 1,
                           "four tanks sharing one temperature again")

    def test_the_period_it_reopens_does_too(self):
        c, _h = a_site()
        c.bir.close("shift")
        opens = {t: c.bir.period[(t, "shift")]["temp_open"]
                 for t in sorted(c.tank_level)}
        self.assertEqual(opens, {t: c.product_temperature(t) for t in opens})

    def test_the_closed_row_agrees_with_the_current_one(self):
        """The two paths disagreeing is what made this visible."""
        c, _h = a_site()
        for tank in sorted(c.tank_level):
            self.assertAlmostEqual(c.bir.close("daily", tank)[0]["temp_close"],
                                   c.bir.current(tank, "daily")["temp_close"],
                                   places=6)



class TheTCSettingReachesTheVolumes(unittest.TestCase):
    """FIDELITY G10. 576013-623 Rev AN p.17-4: "Select TC VOLUME if the
    meters are temperature compensated (the calculation of all BIR volumes
    will be based on the TC value)", and 576013-818 p.12-2: "Incorrect
    setting of this entry will result in variance errors."

    S79F was read in exactly one place, to choose the words VOLUMES ARE TC
    or VOLUMES ARE STANDARD on a header, and every volume under that header
    was the gross one. A header that names the basis, over numbers that are
    always on the same basis, is the SYSTEM CONFIGURATION defect again: two
    things drawn from one source on a report that exists to show them
    differ.
    """

    def a_tc_site(self, hours=15):
        c, h = a_site(hours)
        c.values["S79F00"] = "1"
        return c, h

    def test_standard_is_the_default_and_is_the_gross_volume(self):
        c, _h = a_site()
        self.assertFalse(c.bir.temperature_compensated())
        for tank in sorted(c.tank_level):
            self.assertEqual(c.bir.gauged(tank),
                             c.tank_level[tank]["volume"])

    def test_tc_volume_gauges_the_tank_as_the_tc_report_does(self):
        c, _h = self.a_tc_site()
        for tank in sorted(c.tank_level):
            self.assertEqual(c.bir.gauged(tank), c.tc_volume(tank))
            self.assertNotEqual(c.bir.gauged(tank),
                                c.tank_level[tank]["volume"],
                                "the bench tanks are not at 60 F")

    def test_the_closed_row_carries_the_tc_figures(self):
        c, _h = self.a_tc_site()
        row = c.bir.close("shift", 1)[0]
        self.assertAlmostEqual(row["physical"], c.tc_volume(1), places=6)

    def test_the_header_and_the_numbers_come_from_one_setting(self):
        from tls350sim import printer
        c, _h = a_site()
        self.assertIn("VOLUMES ARE STANDARD", printer._volumes_are(c))
        c.values["S79F00"] = "1"
        self.assertIn("VOLUMES ARE TC", printer._volumes_are(c))
        self.assertEqual(c.bir.gauged(1), c.tc_volume(1))

    def test_a_tc_site_still_reconciles_to_the_same_variance(self):
        """The point of the setting, and the check that the change is
        coherent rather than merely different. Everything BIR counts moves
        onto one basis together -- the gauge, the metered sales and the
        deliveries -- so a site whose product holds one temperature comes
        out at the same variance either way. Correcting the gauge and
        leaving the sales gross is exactly the "variance errors" p.12-2
        warns of.

        The temperature is HELD, and that is not tidying the test up: with
        it drifting, the two bases part company by a gallon or so over a
        shift, and the reason is Y8. A real tank's product expands as it
        warms, so its TC volume is constant while its gross volume is not;
        this console moves neither, so warming the product changes its TC
        volume instead. **The TC basis is where Y8 becomes measurable** --
        it turns "nothing expands" from a note into a variance.
        """
        plain, tc = Console(), Console()
        for one in (plain, tc):
            presets.load(one, "Truck stop, four tanks and BIR")
        tc.values["S79F00"] = "1"          # before a period opens, not after
        for one in (plain, tc):
            one.meter_flow = {1: 120.0, 2: 90.0}
            for tank in sorted(one.tank_level):
                one.hold_temperature(tank, 52.0)
        for _ in range(8 * 4):
            for one in (plain, tc):
                one.clock_offset += 900
                one.tick()
        selling = 0
        for tank in sorted(plain.tank_level):
            gross = plain.bir.current(tank, "shift")
            corrected = tc.bir.current(tank, "shift")
            if gross["sales"] <= 0.0:
                continue                    # a tank with no meter on it
            selling += 1
            self.assertNotAlmostEqual(gross["sales"], corrected["sales"],
                                      places=3)
            self.assertNotAlmostEqual(gross["physical"],
                                      corrected["physical"], places=3)
            self.assertAlmostEqual(gross["variance"], corrected["variance"],
                                   places=3)
        self.assertGreater(selling, 0, "the bench sold nothing")

    def test_a_delivery_lands_on_the_basis_the_site_is_set_to(self):
        """The rise comes off the delivery's own two snapshots, which each
        carry a TC figure, so a drop into a cold tank is corrected against
        the temperature it was AT."""
        c, _h = self.a_tc_site(hours=1)
        before = c.bir.current(1, "shift")["deliveries"]
        start = c.tank_level[1]["volume"]
        for volume in (start + 1000.0, start + 2000.0):
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 60.0
            c.tick()
        c.clock_offset += 900.0
        c.tick()
        record = c.deliveries.last(1)
        self.assertIsNotNone(record)
        gained = c.bir.current(1, "shift")["deliveries"] - before
        self.assertAlmostEqual(
            gained, record.tc_amount + c.bir.basis(1, record.sold), places=3)
        self.assertNotAlmostEqual(gained, record.amount + record.sold,
                                  places=3)



class MeterDataPresentPutsATankInTheReconciliation(unittest.TestCase):
    """FIDELITY G10. S615's only reader was AccuChart, and BIR opened all
    four periods for every tank in `tank_level` whatever the flag said.

    576013-818 p.12-2 gives the flag both of its consequences and both are
    about whether the tank is IN: "If there is meter data present and this
    entry is incorrectly set to NO, the map will never complete because the
    auto-meter mapping program will not assign this tank to a meter. If
    there is no meter data present and this entry is incorrectly set to YES,
    a BIR report will be generated for this tank. There will be large
    reconciliation errors because there is no sales information." A report
    exists for a tank because the flag says yes.
    """

    def a_site_with_one_tank_declared(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        for tank in sorted(c.tank_level):
            c.values.pop(f"S615{tank:02d}", None)
        c.values["S61501"] = "011"
        c.meter_flow = {1: 120.0}
        for _ in range(8):
            c.clock_offset += 900
            c.tick()
        return c

    def test_only_the_declared_tanks_reconcile(self):
        c = self.a_site_with_one_tank_declared()
        self.assertEqual(c.bir.tanks(), [1])
        self.assertTrue(c.bir.covers(1))
        self.assertFalse(c.bir.covers(2))

    def test_an_undeclared_tank_has_no_row_and_no_report(self):
        c = self.a_site_with_one_tank_declared()
        self.assertIsNotNone(c.bir.row(1, "shift"))
        self.assertIsNone(c.bir.row(2, "shift"))
        text = c.bir.report([1, 2])
        self.assertIn("NO SHIFT DATA AVAILABLE", text)

    def test_a_scheduled_close_closes_only_those_tanks(self):
        c = self.a_site_with_one_tank_declared()
        rows = c.bir.close("shift")
        self.assertEqual([row["tank"] for row in rows], [1])

    def test_the_fuel_still_moves_and_the_meter_still_counts(self):
        """The flag decides what RECONCILES, not what happens at the site.
        A tank wrongly set to NO goes on selling and its meter goes on
        totalling; the sales simply land in no period, which is the shape
        p.12-2's "large reconciliation errors" take from the other side."""
        c = self.a_site_with_one_tank_declared()
        c.meter_flow = {2: 90.0}
        c.meters[2] = 2
        before = c.tank_level[2]["volume"]
        for _ in range(4):
            c.clock_offset += 900
            c.tick()
        self.assertLess(c.tank_level[2]["volume"], before)
        self.assertGreater(c.bir.totals.get(2, 0.0), 0.0)
        self.assertEqual(c.bir.period.get((2, "shift")), None)


class TheAlarmThresholdIsComparedToSomething(unittest.TestCase):
    """FIDELITY G4: `threshold()` implemented 576013-818 p.12-3's formula
    with the manual's own defaults, and all five of its call sites were
    PRINT sites. Nothing compared a variance against it and no 20xx alarm
    was posted anywhere in the package.
    """

    def a_periodic_row(self, variance, sales=100000.0, enabled=True):
        c, _h = a_site()
        c.values["S79700"] = "02" if enabled else "01"
        c.bir.close("periodic")
        row = c.bir.closed[(1, "periodic")][0]
        row["variance"], row["sales"] = variance, sales
        return c, row

    def test_the_manuals_worked_example_is_the_limit(self):
        """"(0.01) x (100,000) + 130 = 1000 + 130 = 1130 gallons"."""
        c, row = self.a_periodic_row(0.0)
        self.assertAlmostEqual(c.bir.threshold(row), 1130.0, places=6)

    def test_under_the_limit_posts_nothing(self):
        c, _row = self.a_periodic_row(1129.0)
        self.assertEqual([a for a in c.conditions() if a.startswith("20")], [])

    def test_over_the_limit_posts_the_threshold_alarm(self):
        c, _row = self.a_periodic_row(1131.0)
        self.assertIn("200201", c.conditions())

    def test_a_loss_counts_as_much_as_a_gain(self):
        """"The polarity of the variance is either positive or negative", and
        the limit is a magnitude."""
        c, _row = self.a_periodic_row(-1131.0)
        self.assertIn("200201", c.conditions())

    def test_the_site_has_to_have_turned_it_on(self):
        """S797, "Periodic Reconciliation Alarm (Disabled/Enabled)"."""
        c, _row = self.a_periodic_row(5000.0, enabled=False)
        self.assertEqual([a for a in c.conditions() if a.startswith("20")], [])

    def test_a_shift_close_is_not_a_periodic_one(self):
        """The rule the manual states is about "the reconciliation period"."""
        c, _h = a_site()
        c.values["S79700"] = "02"
        c.bir.close("shift")
        c.bir.closed[(1, "shift")][0]["variance"] = 50000.0
        self.assertEqual([a for a in c.conditions() if a.startswith("20")], [])


class TwoClosesDueAtOnce(unittest.TestCase):
    """FIDELITY G3. `_scheduled`'s loop closed on the first due time and
    RETURNED, and `_before` then advanced past the rest -- so a second close
    due in the same window was lost for good.

    576013-623 p.17-2 tells a site to arrange exactly that collision --
    "Shift Closing Time #4 should match Shift Start Time #1" -- and the
    daily default is 2:00 AM. A site with shift 1 at 02:00 lost its daily
    close every day, and with it the weekly and the periodic, which only
    fire from inside the daily branch.
    """

    KINDS = ("shift", "daily", "weekly", "periodic")

    # 01:00, an hour before the daily close, so that exactly 24 hours later
    # the window has crossed 02:00 once and 06:00 once. Pinned rather than
    # taken from the wall clock, because how many times a window crosses a
    # closing time depends on where it opens -- and a count that moves with
    # the hour you run the suite at is the shape FIDELITY V0 is about.
    START = (2026, 9, 1, 1, 0, 0, 0, 1, -1)

    def closes_over_a_day(self, shift_time, hours=24):
        console = Console()
        presets.load(console, "Truck stop, four tanks and BIR")
        console.clock_offset = time.mktime(self.START) - time.time()
        console.values["S79300"] = "0200"                 # daily at 02:00
        console.values["S79401"] = "01" + shift_time      # shift 1
        console.tick()                                    # sets _before
        before = {k: len(console.bir.closed.get((1, k)) or [])
                  for k in self.KINDS}
        for _ in range(hours * 4):
            console.clock_offset += 900
            console.tick()
        return {k: len(console.bir.closed.get((1, k)) or []) - before[k]
                for k in self.KINDS}

    def test_a_shift_that_does_not_collide_closes_both(self):
        """The control: nothing about this was ever broken. One shift close
        for every daily one, whatever the window happens to cover."""
        got = self.closes_over_a_day("0600")
        self.assertEqual((got["shift"], got["daily"]), (1, 1))

    def test_the_daily_close_survives_a_shift_at_the_same_minute(self):
        """The same site with shift 1 moved onto the daily close's own
        minute, which is the arrangement p.17-2 asks for. It used to close
        the shift, return, and lose the day."""
        got = self.closes_over_a_day("0200")
        self.assertEqual((got["shift"], got["daily"]), (1, 1),
                         "the daily close vanished")

    def test_a_console_can_hold_both_pending_conditions(self):
        """576013-818 p.12-2 names `Close Daily Pending` and `Close Shift
        Pending` as two conditions at once, and `pending` was one slot."""
        console, _h = a_site()
        console.bir.pending = [(0.0, "shift"), (0.0, "daily")]
        got = console.bir.conditions()
        self.assertIn("011300", got)
        self.assertIn("011400", got)

    def test_a_close_waits_for_the_pumps_and_then_happens(self):
        """A close due while the site is dispensing is held, not dropped."""
        console = Console()
        presets.load(console, "Truck stop, four tanks and BIR")
        console.clock_offset = time.mktime(self.START) - time.time()
        console.values["S79300"] = "0200"
        console.values["S79401"] = "010200"
        console.tick()
        for meter in console.meters:
            console.meter_flow[meter] = 5.0
        before = {k: len(console.bir.closed.get((1, k)) or [])
                  for k in self.KINDS}
        for _ in range(8):                       # past 02:00, still selling
            console.clock_offset += 900
            console.tick()
        held = {kind for _due, kind in console.bir.pending}
        self.assertEqual(held, {"shift", "daily"}, console.bir.pending)
        self.assertEqual(len(console.bir.closed.get((1, "daily")) or []),
                         before["daily"], "a close happened mid-sale")
        for meter in console.meters:
            console.meter_flow[meter] = 0.0
        console.clock_offset += 900
        console.tick()
        self.assertEqual(console.bir.pending, [])
        for kind in ("shift", "daily"):
            self.assertGreater(len(console.bir.closed.get((1, kind)) or []),
                               before[kind], kind)


class ThePeriodicModeIsTheManualsWayRound(unittest.TestCase):
    """FIDELITY F11. "ss - Periodic Reconciliation Mode  2=Rolling
    1=Monthly", 576013-635 Rev AA under 795, and `consoledata.json` had
    `[["0","MONTHLY"], ["1","ROLLING"]]`.

    Zero-based made the first option unreachable over the wire -- every
    value a tool can send is 1 or 2, and both landed on ROLLING -- and
    `bir._week_and_period()` branched on the last character being "1" for
    rolling, which is the manual backwards BOTH ways round. A site
    programmed Monthly closed on a rolling window and a site programmed
    Rolling closed on the first of the month, which decides what every
    periodic report on the console covers.

    `S79700`, two codes away in the same block, is one-based and two
    characters and always was, which is what makes this a slip rather than
    a convention.
    """

    def test_both_options_can_be_set_over_the_wire(self):
        c, h = a_site()
        for value, want in (("01", "MONTHLY"), ("02", "ROLLING")):
            self.assertNotIn("9999", send(h, "S79500" + value), value)
            self.assertIn(want, send(h, "I79500"), value)

    def test_the_report_format_too(self):
        c, h = a_site()
        for value, want in (("01", "ROW"), ("02", "COLUMN")):
            self.assertNotIn("9999", send(h, "S79A00" + value), value)
            self.assertIn(want, send(h, "I79A00"), value)

    def closes_on(self, mode, day_of_month=None, length_days=31,
                  days_open=None):
        """Whether the periodic period closes when the daily one does.

        `_week_and_period` is the branch under test, called with the moment
        the daily close came due -- which is how `_scheduled` calls it.

        The two branches are asked two different questions, so there are two
        ways to say when the close came due. MONTHLY closes on a calendar
        day, so `day_of_month` names one. ROLLING closes when its own window
        is up, which is a DURATION and not a date, so `days_open` counts
        from the moment the period actually opened.

        Saying the rolling one as a day of the month is what broke this: the
        assertion was `day_of_month=17` against a one-day window, which is
        really "the 17th is at least a day after today" -- true only while
        the console's own day of the month is 16 or less. `a_site` pins the
        HOUR and not the DATE, so it passed until 2026-09-17 and failed
        every day after. Counting seconds from `opened` has no such expiry,
        and is not a DST hazard the way adding calendar days would be.
        """
        c, _h = a_site()
        c.values["S79500"] = mode
        c.values["S79600"] = f"{length_days:02d}"
        if days_open is not None:
            # the same `opened` the branch itself reduces over
            first = min((p["opened"] for (_t, k), p in c.bir.period.items()
                         if k == "periodic"), default=time.mktime(c.now()))
            due = first + days_open * 86400
        else:
            stamp = list(time.localtime(time.mktime(c.now())))
            stamp[2] = day_of_month
            due = time.mktime(time.struct_time(tuple(stamp)))
        before = len(c.bir.closed.get((1, "periodic")) or [])
        c.bir._week_and_period(due)
        return len(c.bir.closed.get((1, "periodic")) or []) > before

    def test_monthly_closes_on_the_first_of_the_month(self):
        """"A report will automatically print on the first day of" the
        period, and 1 is Monthly."""
        self.assertTrue(self.closes_on("01", day_of_month=1))
        self.assertFalse(self.closes_on("01", day_of_month=17))

    def test_rolling_does_not_care_what_day_it_is(self):
        """It closes when its own window is up, so a fresh period on the
        first of the month is not due."""
        self.assertFalse(self.closes_on("02", day_of_month=1))
        self.assertFalse(self.closes_on("02", day_of_month=17))

    def test_rolling_closes_when_its_window_is_up(self):
        """"the length of the reconciliation period" is the whole of it: the
        window is up when it has been open that many days, and not before.

        The sibling above proves a rolling period is not due on a calendar
        day; this proves the length is what decides, at the shortest window
        and at the 31-day default.
        """
        self.assertFalse(self.closes_on("02", days_open=0, length_days=1))
        self.assertTrue(self.closes_on("02", days_open=1, length_days=1))
        self.assertFalse(self.closes_on("02", days_open=30))
        self.assertTrue(self.closes_on("02", days_open=31))

    def test_a_one_character_value_means_the_same_digit(self):
        """A backup written before the width was fixed still restores to the
        setting it names, because 1 is Monthly at either width."""
        self.assertTrue(self.closes_on("1", day_of_month=1))
        self.assertFalse(self.closes_on("2", day_of_month=1))


class TheVarScreenCarriesItsOwnPercentage(unittest.TestCase):
    """FIDELITY Q2. 576013-610 Rev AC p.28-11 lists what STEP walks in
    Reconciliation Mode "one item at a time", and the last item is a PAIR:
    "book variance (difference between gauged volume and book inventory) AND
    % variance sales (book variance divided by sales)".

    The report on p.28-14 puts them on one row -- `VAR            : 800 GAL
    280.7%` -- and `printer.py` gets that right. The panel drew
    `VAR: 0 GALS`: the percentage was on the paper and not on the screen,
    though the manual lists it among the items the screens walk.
    """

    def a_tank_with_a_variance(self):
        """A book the gauge disagrees with, which is what a variance IS.

        Made by moving the GAUGE rather than the row: `bir.current()` builds
        its row out of the tank every time it is asked, so a row mutated in
        place is thrown away before anything reads it.
        """
        from tls350sim import printer
        c, _h = a_site()
        self.assertIsNotNone(c.bir.row(1, c.recon_kind, False),
                             "the fixture has no reconciliation row")
        # DOWN by 800: book variance is book minus gauged, so a tank 800
        # gallons SHORT of its book reads +800, which is the sample's sign
        # and the sign a loss report wants. See FIDELITY G9.
        c.tank_level[1]["volume"] -= 800.0
        self.assertAlmostEqual(
            c.bir.analysis(c.bir.row(1, c.recon_kind, False))["book_var"],
            800.0, places=3)
        return c, printer

    def test_the_screen_and_the_paper_read_one_row(self):
        c, printer = self.a_tank_with_a_variance()
        screen = c.recon_reading("book_var", 1)
        paper = [r for r in printer.book_variance(c, [1])
                 if r.startswith("VAR")]
        self.assertEqual(len(paper), 1)
        self.assertTrue(paper[0].endswith(screen),
                        f"{paper[0]!r} does not end with {screen!r}")

    def test_it_is_the_pair_and_not_just_the_gallons(self):
        c, _printer = self.a_tank_with_a_variance()
        screen = c.recon_reading("book_var", 1)
        self.assertIn(" GAL ", screen)
        self.assertTrue(screen.endswith("%"), screen)
        # and it still fits the display it is drawn on
        self.assertLessEqual(len("VAR: " + screen), 24, screen)


class TheVarianceAnalysisPrintsAllOfP2818(unittest.TestCase):
    """FIDELITY Q3. 576013-610 Rev AC p.28-18 draws the whole report and this
    console printed seven of its blocks: after `UNEX VAR` come `CHART ALM`,
    `CALIB FAIL`, the corrective actions, the leak test results and a
    `MONTHLY TANK TEST REPORT`, and three of those five were absent.

    Two of the three were already computed. The console keeps the AccuChart
    flags apart under the wire's own names -- "LLLLLLLL - failure to
    calibrate in 56 days" and "llllllll - tank chart alarm" -- which is
    exactly the pair the report prints on two rows.
    """

    def a_variance(self, tank=1):
        from tls350sim import printer
        c, _h = a_site()
        return c, printer

    def a_variance_of_800(self):
        c, printer = self.a_variance()
        c.tank_level[1]["volume"] -= 800.0       # short of book by 800
        return c, printer

    def test_the_rows_are_the_pages_own_geometry(self):
        """`BOOK VAR      : 800 GAL`: the label is padded so the colon lands
        in one column and the value follows it directly. These were right
        aligned into nine characters, so `800 GAL` carried six spaces the
        page has not got -- and the page settles it, because read by its
        word boxes `800 GAL` and `T 1` begin at the SAME column, which a
        right aligned field cannot do to a three character value and a seven
        character one."""
        c, printer = self.a_variance_of_800()
        rows = printer.variance_analysis(c, [1])
        self.assertIn("BOOK VAR      : 800 GAL", rows)
        for line in rows:
            if line[:4] in ("BOOK", "DLVY", "SALE", "TEMP", "WATE", "UNEX"):
                self.assertNotIn(":  ", line, line)

    def test_the_two_accuchart_rows_print_when_their_flag_stands(self):
        c, printer = self.a_variance()
        rows = printer.variance_analysis(c, [1])
        self.assertNotIn("CHART ALM     : T 1", rows)
        self.assertNotIn("CALIB FAIL    : T 1", rows)
        entry = c.accuchart.tanks.get(1)
        self.assertIsNotNone(entry, "the fixture has no AccuChart tank")
        entry.warn = True
        entry.failed = True
        rows = printer.variance_analysis(c, [1])
        self.assertIn("CHART ALM     : T 1", rows)
        self.assertIn("CALIB FAIL    : T 1", rows)
        # and they sit between UNEX VAR and the corrective actions
        unex = next(i for i, r in enumerate(rows)
                    if r.startswith("UNEX VAR "))
        self.assertLess(unex, rows.index("CHART ALM     : T 1"))
        self.assertLess(rows.index("CALIB FAIL    : T 1"),
                        rows.index("LEAK TEST RESULTS"))

    def test_the_monthly_tank_test_report_is_the_periodic_one(self):
        """"MONTHLY TANK TEST REPORT ... TEST TYPE: STANDARD / PERCENT
        VOLUME = 24.8". The monthly test is the 0.2 gal/hr one, so its type
        is `S62C`, Periodic Test Type, whose own choices are STANDARD and
        QUICK."""
        c, printer = self.a_variance()
        c.leaks.start("tank", 1, "periodic", hours=2.0)
        c.clock_offset += 3600.0 * 4
        c.tick()
        rows = printer.variance_analysis(c, [1])
        self.assertIn("MONTHLY TANK TEST REPORT", rows)
        self.assertIn("TEST TYPE: STANDARD", rows)
        percent = [r for r in rows if r.startswith("PERCENT VOLUME =")]
        self.assertEqual(len(percent), 1)
        self.assertNotIn("NO DATA", percent[0])
        c.values["S62C01"] = "011"                     # QUICK
        self.assertIn("TEST TYPE: QUICK",
                      printer.variance_analysis(c, [1]))

    def test_the_corrective_actions_read_the_alarm_numbers_decimal(self):
        """`conditions()` writes alarm numbers in decimal. This read "18"
        for the AccuChart calibration warning -- which is `02/18 PER TST
        NEEDED ALM` -- and hex for the three test failures, `0D`, `0E`, `0F`
        against records that say `13`, `14`, `15`. So RECALIBRATE TANK CHART
        came up on the wrong alarm and INVESTIGATE FAILED TANK TEST could not
        come up at all."""
        from tls350sim.console import STATUS_TYPES
        self.assertEqual(STATUS_TYPES["02"]["24"], "ACCUCHART CAL WARN")
        self.assertEqual(STATUS_TYPES["02"]["13"], "GROSS TEST FAIL")
        c, _printer = self.a_variance()
        row = c.bir.row(1, c.recon_kind, False)
        var = c.bir.analysis(row)
        self.assertEqual(c.corrective_actions(1, var), [])
        entry = c.accuchart.tanks.get(1)
        entry.failed = True
        self.assertIn("022401", c.compute_alarms())
        self.assertEqual(c.corrective_actions(1, var)[:2],
                         ["RECALIBRATE TANK CHART", "T1"])


class TheWeekClosesOnTheDayItCameDue(unittest.TestCase):
    """FIDELITY G14. The weekly and monthly closes asked the day the tick
    ran rather than the day the daily close was due."""

    KINDS = ("shift", "daily", "weekly", "periodic")

    def counts(self, c):
        return {k: len(c.bir.closed.get((1, k)) or []) for k in self.KINDS}

    def a_stop(self, at):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.clock_offset = time.mktime(at) - time.time()
        for meter in c.meters:
            c.meter_flow[meter] = 0.0
        return c

    def test_a_close_due_before_midnight_closes_its_own_week(self):
        start = (2026, 9, 12, 23, 0, 0, 0, 1, -1)
        c = self.a_stop(start)
        weekday = time.localtime(time.mktime(start)).tm_wday
        c.values["S79300"] = "2350"
        c.values["S51E00"] = str((weekday + 1) % 7)
        c.tick()
        before = self.counts(c)
        c.clock_offset += 90 * 60.0          # one tick, 23:00 to 00:30
        c.tick()
        after = self.counts(c)
        self.assertEqual((after["daily"] - before["daily"],
                          after["weekly"] - before["weekly"]), (1, 1))



if __name__ == "__main__":
    unittest.main()


def _at(y, mon, day, hh, mm):
    return time.mktime((y, mon, day, hh, mm, 0, 0, 0, -1))


class TheDailyHistoryIsATable(unittest.TestCase):
    """FIDELITY G12. C09 and I@A400 both print the BIR daily history and
    both printed ONE row of it -- and C09 printed the same height twice,
    because `STRT HT` and `END HT` were both `stick_height`, the reading
    NOW, on a report whose subject is a day's movement.

    576013-635 Rev AA p.588 draws two rows and they chain: the second row's
    start height and start volume are the first row's end height and end
    volume. 576013-818 p.12-5 draws twenty-four for one tank under a title
    that names thirty-one or sixty-two.
    """

    # p.588's own two rows, character for character. The extraction
    # interleaves them with the column header -- the trap UNKNOWNS section D
    # is about -- and the word boxes put the header above both.
    SAMPLE = [
        "9912311104 0001010130 45.737  48.000 4700.0  5000.0  0.0   300.0"
        "  0.0     0.0",
        "0001010130 0001010931 48.000  47.895 5000.0  4986.1  0.0     0.0"
        "  0.0   -13.9",
    ]
    HEAD = ("STRT TIME  END TIME   STRT HT END HT STRT VL END_VL SALES"
            "  DELIV OFFSET   VAR")

    def a_manual_console(self):
        """p.588's tank: 10,000 gallons and 96 inches, which is the tank
        every worked example on this shelf is written on."""
        c = Console()
        c.software["bir"] = True
        c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
        c.values["S60701"] = "01" + struct.pack(">f", 96.0).hex().upper()
        c.values["S60201"] = "01* MAG PROBE #1 *   "
        c.values["S61501"] = "011"              # METER DATA PRESENT: YES
        c.values["S62F01"] = "011"              # and a Mag probe to map to
        c.meters = {1: 1}                       # ... with a meter on it
        c.tank_level[1] = {"volume": 4986.1, "water": 0.0}
        c.bir.closed[(1, "daily")] = [
            self.row(_at(2000, 1, 1, 1, 30), _at(2000, 1, 1, 9, 31),
                     5000.0, 4986.1, 0.0, 0.0, -13.9),
            self.row(_at(1999, 12, 31, 11, 4), _at(2000, 1, 1, 1, 30),
                     4700.0, 5000.0, 0.0, 300.0, 0.0),
        ]
        return c, Handler(c, verbose=False)

    @staticmethod
    def row(opened, closed, opening, physical, sales, deliveries, variance):
        return {"tank": 1, "kind": "daily", "opened": opened,
                "closed": closed, "requested": opened, "opening": opening,
                "water_open": 0.0, "temp_open": 55.0, "temp_close": 55.0,
                "deliveries": deliveries, "ticketed": 0.0, "sales": sales,
                "adjust": 0.0, "calculated": physical - variance,
                "physical": physical, "water": 0.0, "variance": variance}

    def test_the_manuals_two_rows_replay_to_the_character(self):
        """The heights are not in the row: they come off the console's own
        chart at the volumes the row recorded, and 4700 gallons in this tank
        is 45.737 inches, 5000 is 48.000 and 4986.1 is 47.895 -- which are
        p.588's three heights to the digit."""
        _c, h = self.a_manual_console()
        got = lines(h, "IC0901")
        self.assertIn(self.HEAD, got)
        start = got.index(self.HEAD) + 1
        self.assertEqual(got[start:start + 2], self.SAMPLE)

    def test_the_two_height_columns_are_the_two_ends_of_the_day(self):
        """They were one reading printed twice, so they could never
        differ."""
        c, _h = self.a_manual_console()
        row = c.bir.closed[(1, "daily")][0]
        self.assertNotEqual(c.stick_height(1, row["opening"]),
                            c.stick_height(1, row["physical"]))
        self.assertAlmostEqual(c.stick_height(1, 4700.0), 45.737, places=3)
        self.assertAlmostEqual(c.stick_height(1, 5000.0), 48.000, places=3)

    def test_the_report_carries_every_day_it_holds(self):
        c, h = self.a_manual_console()
        got = lines(h, "IC0901")
        rows = [l for l in got if l[:4].isdigit()]
        self.assertEqual(len(rows), 3, "two closed days and the day running")

    def test_the_history_is_kept_to_the_depth_the_title_names(self):
        """"DAILY RECONCILIATION LIST FOR LAST 31 DAYS (62 ON NEWER
        VERSIONS)", against ten for every period alike."""
        self.assertEqual(bir.KEPT["daily"], 62)
        self.assertEqual(bir.KEPT.get("shift", bir.KEPT_DEFAULT), 10)
        c, _h = self.a_manual_console()
        for day in range(70):
            c.clock_offset += 60.0
            c.bir.close("daily", 1)
        self.assertEqual(len(c.bir.closed[(1, "daily")]), 62)

    def test_at_a4_prints_the_daily_list_and_not_the_shift_report(self):
        """I@A400's own title is the report: "DAILY RECONCILIATION LIST FOR
        LAST 31 DAYS". It printed the CURRENT SHIFT RECONCILIATION REPORT
        under it."""
        _c, h = self.a_manual_console()
        got = lines(h, "I@A401")
        self.assertIn("BASIC_RECONCILIATION HISTORY", got)
        self.assertIn("REQUEST ST  STRT TIME   END TIME STRT_VL  END_VL"
                      "  SALES  DELIV OFFSET VARIEN", got)
        self.assertNotIn("CURRENT SHIFT RECONCILIATION REPORT", got)
        rows = [l for l in got if l[:4].isdigit()]
        self.assertEqual(len(rows), 3)

    def test_at_a4_reproduces_the_guides_own_arithmetic(self):
        """p.12-5's rows close: END_VL - (STRT_VL - SALES + DELIV + OFFSET)
        is the VARIEN column on every one of them."""
        c, _h = self.a_manual_console()
        for row in c.bir.closed[(1, "daily")]:
            line = c.bir.history_line(row)
            got = [float(x) for x in
                   (line[32:40], line[40:48], line[48:55], line[55:62],
                    line[62:69], line[69:76])]
            start, end, sales, deliv, offset, var = got
            self.assertAlmostEqual(end - (start - sales + deliv + offset),
                                   var, places=1)

    def test_the_computer_format_counts_its_records_and_its_floats(self):
        """"rr - Number of records to follow (Hex)" and "NN - Number of
        eight character Data Fields to follow (Hex)", over three stamps per
        record rather than the two every other C-code sends."""
        _c, h = self.a_manual_console()
        body = send(h, "ic0901").replace(chr(1), "").replace(chr(3), "")
        body = body[len("ic0901") + 10:].split("&&")[0]
        self.assertEqual(body[:2], "01")            # tank 1
        self.assertEqual(body[2:4], "03")           # three records
        record = body[4:]
        self.assertEqual(record[:10], "9912311104")   # requested start
        self.assertEqual(record[10:20], "9912311104")
        self.assertEqual(record[20:30], "0001010130")
        self.assertEqual(record[30:32], "0A")       # ten floats
        self.assertEqual(len(record[32:32 + 80]), 80)

    def test_a_tank_the_site_has_not_declared_has_no_history(self):
        c, h = self.a_manual_console()
        del c.values["S61501"]
        self.assertEqual(c.bir.daily_history(1), [])
        self.assertIn("  NO DATA AVAILABLE", lines(h, "IC0901"))


class TheProbelessMeterIsTheThirdCondition(unittest.TestCase):
    """FIDELITY G8, and the third of p.12-8's three completeness conditions.

    576013-818 p.12-9: "In some applications the dispensing data sent from
    the POS terminal to the TLS Console will contain meter transactions from
    a tank(s) in which there is no probe. Unable to match the transaction
    with a corresponding height change, the tank-meter mapping algorithm will
    declare the map incomplete and BIR will be inhibited. You must manually
    map a 'probeless' meter into the tank/meter map before it will be
    declared complete and BIR can begin."

    The console asked `available()` about tank -1, which is not a tank and
    never becomes one, so the one action the page prescribes made no
    difference at all: the meter stayed unmapped, the map stayed incomplete,
    and the site never reconciled again.
    """

    def a_site_with_a_probeless_meter(self):
        c, h = a_site()
        c.meter_flow[91] = 50.0
        for _ in range(4):
            c.clock_offset += 900.0
            c.tick()
        return c, h

    def probeless(self, c, h, meter=91):
        key = c.meter_key(meter)
        send(h, "S7B100 %d %d %d %d -1"
             % (key.bus, key.slot, key.fp, key.meter))
        return key

    def test_it_stops_the_console_reporting_until_it_is_mapped(self):
        c, _h = self.a_site_with_a_probeless_meter()
        self.assertEqual(c.bir.unmapped_meters(), [c.meter_key(91)])
        self.assertFalse(c.bir.map_complete())

    def test_and_mapping_it_to_the_probeless_tank_completes_the_map(self):
        c, h = self.a_site_with_a_probeless_meter()
        self.probeless(c, h)
        self.assertEqual(c.bir.unmapped_meters(), [])
        self.assertTrue(c.bir.map_complete())

    def test_a_probeless_meter_is_never_retired(self):
        """"If an UNMAPPED meter has not been reported" -- it is mapped,
        even though what it is mapped to is not a tank."""
        c, h = self.a_site_with_a_probeless_meter()
        key = self.probeless(c, h)
        c.meter_flow.pop(key)
        c.clock_offset += 30 * 3600.0
        c.tick()
        self.assertFalse(c.bir.retired(key))
        self.assertTrue(c.bir.map_complete())

    def test_the_map_prints_it_as_x(self):
        """p.12-11's own symbol, and not `-1`."""
        c, h = self.a_site_with_a_probeless_meter()
        self.probeless(c, h)
        rows = [r for r in lines(h, "I7B100") if r[:1].isdigit()]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].endswith("X"), rows)


class AMissingDayIsMarked(unittest.TestCase):
    """FIDELITY G8. 576013-818 p.12-22's Example 4 is "Customer complaint:
    Missing days in reconciliation", and p.12-23 shows what the console
    prints about it: `---- MISSING DATA ----` between two rows whose
    REQUEST ST days are not consecutive. Its own table skips 960805,
    960808-09 and 960811-12 and carries three markers.

    The console can produce the state without being asked to: `_scheduled`
    looks one day back and no further, so a clock that moves several days in
    one tick closes nothing on the days in between.
    """

    def a_console_that_lost_days(self):
        c, h = a_site()
        for step in (1, 1, 5, 1, 1, 3, 1):
            c.clock_offset += step * 86400.0
            c.tick()
        return c, h

    def days(self, c, tank=1):
        return [c.bir._history_day(row) for row in c.bir.daily_history(tank)]

    def test_the_bench_can_lose_a_day_at_all(self):
        """Which is what makes the marker worth having: without a gap to
        mark this test would be checking a string nothing can produce."""
        c, _h = self.a_console_that_lost_days()
        days = self.days(c)
        self.assertTrue(any(b - a > 1 for a, b in zip(days, days[1:])),
                        days)

    def test_a_gap_gets_one_marker_however_wide_it_is(self):
        c, _h = self.a_console_that_lost_days()
        days = self.days(c)
        gaps = sum(1 for a, b in zip(days, days[1:]) if b - a > 1)
        printed = c.bir.history_report([1]).splitlines()
        self.assertEqual(printed.count(c.bir.MISSING_DATA), gaps)
        self.assertGreater(gaps, 1, "the fixture wants more than one gap")

    def test_it_sits_between_the_two_rows_it_separates(self):
        """A row, the marker, a row -- never at either end of the table,
        which is where a marker for the requested period would go and this
        is not that. p.12-23's own table opens and closes on a row."""
        c, _h = self.a_console_that_lost_days()
        printed = [row for row in c.bir.history_report([1]).splitlines()
                   if row[:4].isdigit() or row == c.bir.MISSING_DATA]
        where = printed.index(c.bir.MISSING_DATA)
        self.assertNotIn(where, (0, len(printed) - 1))
        self.assertTrue(printed[where - 1][:4].isdigit())
        self.assertTrue(printed[where + 1][:4].isdigit())
        # and the days either side really are more than one apart: the
        # marker's position in the printed table is the same as the gap's
        # position in the rows the table was built from
        rows = self.days(c)
        gap = [i for i, (a, b) in enumerate(zip(rows, rows[1:]))
               if b - a > 1][0]
        self.assertEqual(where, gap + 1)

    def test_a_console_that_missed_nothing_prints_no_marker(self):
        c, _h = a_site()
        for _ in range(4):
            c.clock_offset += 86400.0
            c.tick()
        self.assertNotIn(c.bir.MISSING_DATA, c.bir.history_report([1]))


class ThePeriodicReportListsItsDays(unittest.TestCase):
    """FIDELITY G12. `period_days` returned the periodic period's own single
    row, so `multi=True` on five C-codes printed one line and the TOTALS
    branch under it was unreachable. 576013-818 p.12-3 asks for the other
    thing: "An examination of the BIR daily history table will indicate
    whether a large periodic variance is a summation of smaller daily
    variances with the same sign or whether there are isolated instances of
    large daily variances."
    """

    def a_site_with_days_behind_it(self, days=4):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        # Pinned: this closes the day by hand four times and ticks two hours
        # between them, so a start after four in the afternoon walks the
        # clock across the site's own 06:00 automatic close and the period
        # gets a fifth closed day nobody asked for.
        start_at(c, 8)
        for _ in range(days):
            c.meter_flow = {1: 120.0}
            for _ in range(8):
                c.clock_offset += 900
                c.tick()
            c.meter_flow = {}
            c.tick()
            c.bir.close("daily")
        return c, Handler(c, verbose=False)

    def test_the_days_in_the_period_are_the_closed_days(self):
        c, _h = self.a_site_with_days_behind_it()
        rows = c.bir.period_days(1)
        self.assertEqual(len(rows), 5, "four closed days and the one open")
        for row in rows:
            self.assertEqual(row["kind"], "daily")

    def test_the_row_report_totals_them(self):
        """The TOTALS line was dead code: it prints only when the report has
        more than one row, and the report never had more than one."""
        c, h = self.a_site_with_days_behind_it()
        got = lines(h, "IC0700")
        self.assertTrue(any(l.startswith("TOTALS") for l in got), got)
        total = [l for l in got if l.startswith("TOTALS")][0]
        sales = sum(r["sales"] for r in c.bir.period_days(1))
        self.assertIn("%7.0f" % sales, total)

    def test_a_period_with_no_closed_day_still_prints_the_one_running(self):
        c, _h = self.a_site_with_days_behind_it(days=0)
        self.assertEqual(len(c.bir.period_days(1)), 1)


class RequestStartIsWhenTheCloseWasDue(unittest.TestCase):
    """FIDELITY G8's second half, from the side that could be settled. The
    history table prints REQUEST ST beside STRT TIME because they are not
    the same instant: the close is due on the programmed minute and happens
    when the console next looks -- and 576013-818 p.12-2 says a close due
    while the site is dispensing waits for it to go idle, which can be
    hours."""

    def test_a_close_that_waited_records_both_times(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.values["S79300"] = "0600"            # daily close at 6:00 AM
        c.meter_flow = {1: 120.0}              # and the site is busy
        start_at(c, 5)
        c.tick()
        c.clock_offset += 3600.0
        c.tick()
        self.assertTrue(c.bir.pending, "the close should be waiting")
        c.clock_offset += 3600.0
        c.meter_flow = {}
        c.tick()
        period = c.bir.period[(1, "daily")]
        self.assertLess(period["requested"], period["opened"])
        self.assertEqual(time.strftime("%H%M",
                                       time.localtime(period["requested"])),
                         "0600")


class AManifoldedSetIsOneReport(unittest.TestCase):
    """FIDELITY G11. 576013-610 p.28-2: "Note: Reconciliation Reports are
    generated as a single product report for a manifolded set", and
    576013-818 p.12-27 draws it -- `T1: BLUE WEST Primary` over `T2: BLUE
    EAST Secondary`, one column header, one table. p.12-8 says which tank it
    belongs to: "In the case of manifolded tanks, the meter is mapped to the
    primary tank. The primary tank is defined as the lowest numbered tank in
    the manifolded set."

    `console.manifolded()` existed and the adjusted delivery report was its
    only reader. Every reconciliation and variance report looped over bare
    tanks, so the secondary kept a period of its own, took whatever the
    siphon carried it and none of the sales, and showed a large permanent
    phantom variance.
    """

    def a_manifolded_site(self, hours=2):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.manifold_together(1, [2])
        c.values["S60201"] = "01BLUE WEST Primary   "
        c.values["S60202"] = "02BLUE EAST Secondary "
        c.meters = {1: 1, 2: 2}
        c.meter_flow = {1: 100.0, 2: 100.0}
        for _ in range(hours * 4):
            c.clock_offset += 900
            c.tick()
        return c, Handler(c, verbose=False)

    def test_the_primary_is_the_lowest_numbered_tank(self):
        c, _h = self.a_manifolded_site()
        self.assertEqual(c.bir.primary(2), 1)
        self.assertEqual(c.bir.primary(1), 1)
        self.assertEqual(c.bir.primary(3), 3)

    def test_only_the_primary_keeps_a_period(self):
        c, _h = self.a_manifolded_site()
        self.assertEqual(c.bir.tanks(), [1, 3, 4])
        self.assertFalse(c.bir.covers(2))
        self.assertIsNone(c.bir.row(2, "shift"))

    def test_the_set_reconciles_the_set_s_own_inventory(self):
        c, _h = self.a_manifolded_site()
        total = sum(c.tank_level[t]["volume"] for t in (1, 2))
        self.assertAlmostEqual(c.bir.gauged(1), total, places=6)
        self.assertAlmostEqual(c.bir.current(1, "shift")["physical"], total,
                               places=6)

    def test_both_meters_sell_out_of_one_period(self):
        """"the meter is mapped to the primary tank" -- and this console can
        have one wired to each half of the set."""
        c, _h = self.a_manifolded_site()
        row = c.bir.current(1, "shift")
        self.assertGreater(row["sales"], 0.0)
        self.assertIsNone(c.bir.row(2, "shift"))

    def test_the_report_prints_one_table_under_both_labels(self):
        c, h = self.a_manifolded_site()
        got = lines(h, "I@A400")
        self.assertEqual(got.count("BASIC_RECONCILIATION HISTORY"), 1)
        first = got.index("T 1:BLUE WEST Primary")
        self.assertEqual(got[first + 1], "T 2:BLUE EAST Secondary")
        self.assertEqual(got[first + 2], "")
        self.assertEqual(got[first + 3], c.bir.HISTORY_HEAD)
        self.assertEqual(len([l for l in got if l.startswith("T ")]), 4)

    def test_asking_for_the_secondary_gives_the_set(self):
        c, h = self.a_manifolded_site()
        self.assertEqual(c.bir.report_tanks([2]), [1])
        got = lines(h, "I@A402")
        self.assertIn("T 1:BLUE WEST Primary", got)
        self.assertIn("T 2:BLUE EAST Secondary", got)

    def test_an_empty_history_prints_the_guides_own_word(self):
        """p.12-27 draws the column header over the single word EMPTY,
        which is what I@A900 prints for an empty buffer too."""
        c, h = self.a_manifolded_site(hours=0)
        del c.values["S61501"]
        got = lines(h, "I@A401")
        self.assertIn("EMPTY", got)

    def test_the_set_finding_its_level_is_not_a_delivery(self):
        """A siphon carries fuel BETWEEN the tanks of a set, so one of them
        rising is not product arriving: the low tank fills out of the high
        one. This read as a delivery into the low tank and a dispense out of
        the high one, and with the set reconciled as one it showed up as a
        delivery the set never received."""
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.tank_level[1]["volume"] = 14000.0
        c.tank_level[2]["volume"] = 9000.0
        c.manifold_together(1, [2])
        for _ in range(8):
            c.clock_offset += 900
            c.tick()
        self.assertAlmostEqual(c.tank_level[1]["volume"],
                               c.tank_level[2]["volume"], places=0)
        self.assertEqual(c.deliveries.records, {})
        self.assertEqual(c.bir.current(1, "shift")["deliveries"], 0.0)

    def test_a_real_drop_into_one_half_is_still_a_delivery(self):
        """And it is measured from where the tank stood before the drop --
        refusing the siphon's rise has to keep BOOKING the reading, or the
        next real delivery measures itself from a level the tank left hours
        ago."""
        c, _h = self.a_manifolded_site(hours=2)
        c.meter_flow = {}
        before = c.tank_level[2]["volume"]
        c.tank_level[2]["volume"] += 3000.0
        for _ in range(3):
            c.clock_offset += 60.0
            c.tick()
        c.clock_offset += 600.0
        c.tick()
        record = c.deliveries.last(2)
        self.assertIsNotNone(record)
        self.assertAlmostEqual(record.start["volume"], before, places=0)
        self.assertIsNone(c.deliveries.last(1), "no phantom on the partner")


class TheVarianceSignsAreThePrintedSamples(unittest.TestCase):
    """FIDELITY G9. Both signed quantities on p.28-18 were the wrong way
    round, and the manual's prose is not unanimous about either -- which is
    why the SAMPLE decides.

    576013-610 Rev AC p.28-14 and 576013-623 p.5-6 make book variance book
    minus gauged; p.28-10's own bullet and p.28-11's STEP walk make it gauged
    minus book. Both printed samples show `BOOK INV 9704` over `GAUGED INV
    8904` with `VAR : 800`, so book minus gauged it is.

    Delivery variance is defined in opposite directions for two DIFFERENT
    reports, and each sample agrees with its own page: p.28-7's Delivery
    Variance report prints 99 for a ticket of 800 against a gauge of 899, and
    p.28-14's Variance Analysis prints -99 for the same pair.
    """

    #  p.28-18's own row, and the opening volume the other five figures give
    SAMPLE = dict(opening=9189.0, sales=285.0, ticketed=800.0,
                  deliveries=899.0, adjust=0.0, physical=8904.0)

    def a_sample_row(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        row = c.bir.current(1)
        row.update(self.SAMPLE)
        row["temp_close"] = row["temp_open"]      # TEMP VAR is its own row
        return c, row

    def test_the_variance_analysis_sample_replays(self):
        """`BOOK VAR : 800`, `DLVY VAR : -99`, `SALE VAR : 899`. The third is
        the check on the first two: "sales variance, difference between book
        variance and delivery variance" only comes to 899 with both signs
        right, and it came to -701."""
        c, row = self.a_sample_row()
        a = c.bir.analysis(row)
        self.assertAlmostEqual(a["book_var"], 800.0, places=6)
        self.assertAlmostEqual(a["delivery_var"], -99.0, places=6)
        self.assertAlmostEqual(a["sales_var"], 899.0, places=6)
        self.assertAlmostEqual(a["book_pct"], 280.7, places=1)

    def test_the_delivery_variance_report_takes_the_other_direction(self):
        """p.28-7: "difference between gauged and ticketed delivery volumes",
        and its sample prints `DLVY VAR : 99` with `% VAR SALES: 11.23%` --
        both positive, where this printed both negative."""
        c, row = self.a_sample_row()
        self.assertAlmostEqual(c.bir.analysis(row)["gauged_delivery_var"],
                               99.0, places=6)

    def test_the_two_reports_print_the_two_signs(self):
        from tls350sim import printer
        c, row = self.a_sample_row()
        c.bir.period[(1, c.recon_kind)].update(
            opening=row["opening"], sales=row["sales"],
            ticketed=row["ticketed"], deliveries=row["deliveries"],
            adjust=row["adjust"])
        c.tank_level[1]["volume"] = row["physical"]
        delivery = printer.delivery_variance(c, [1])
        analysis = printer.variance_analysis(c, [1])
        # p.28-10's own line, now that the report is drawn on that page's
        # grid rather than three characters wider than the roll -- the
        # sample's own 800, 899 and 99 land in the sample's own columns.
        # FIDELITY T13.
        self.assertIn("TICKET VOL :     800 GAL", delivery)
        self.assertIn("GAUGED VOL :     899 GAL", delivery)
        self.assertIn("DLVY VAR   :      99 GAL", delivery)
        self.assertIn("DLVY VAR      : -99 GAL", analysis)
        self.assertIn("BOOK VAR      : 800 GAL", analysis)
        self.assertIn("SALE VAR      : 899 GAL", analysis)

    def test_the_wire_and_the_paper_agree_about_the_sign(self):
        """`book_figures` subtracted the other way AND took the magnitude of
        the percentage, so C10 to C12 disagreed with the printed report about
        a number both read off one row."""
        c, row = self.a_sample_row()
        a = c.bir.analysis(row)
        figures = c.bir.book_figures(row)
        self.assertAlmostEqual(figures[7], a["book_var"], places=6)
        self.assertAlmostEqual(figures[8], a["book_pct"], places=6)
        self.assertGreater(figures[7], 0.0)


class ExampleFiveIsNoBirDataAtAll(unittest.TestCase):
    """FIDELITY G8. 576013-818 Rev AB p.12-26, "Example 5. Customer
    complaint: No BIR Data" -- a four tank site whose I@A400 prints EMPTY
    under every one of them, including the two that are perfectly healthy.

    The chain the example turns on: "A tank will be unavailable for mapping
    if any of the following conditions are true: ... It is manifolded and
    the console has 1XX software"; a meter that cannot be mapped leaves the
    map incomplete; and "BIR will not produce reports while the meter map is
    incomplete". None of those three states existed here, so the console
    reported unconditionally and the example could not be reproduced.

    Its own diagnosis is printed beside its I90200: "902 indicates software
    version is 1XX which does not support BIR for manifolded tanks. Version
    3XX software is required."
    """

    def a_1xx_site_with_a_manifold(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.board, c.version = "E1", 10        # the 1XX line, as 114.04 is
        c.manifold_together(1, [2])
        c.meter_flow = {1: 100.0, 3: 80.0}
        c.tick()
        return c, Handler(c, verbose=False)

    def test_the_manifolded_pair_is_unavailable_for_mapping(self):
        c, _h = self.a_1xx_site_with_a_manifold()
        self.assertEqual(c.family(), "1XX")
        self.assertFalse(c.bir.available(1))
        self.assertFalse(c.bir.available(2))
        self.assertTrue(c.bir.available(3), "SILVER and GOLD are fine")
        self.assertTrue(c.bir.available(4))

    def test_their_meters_are_unmapped_and_the_map_is_incomplete(self):
        c, h = self.a_1xx_site_with_a_manifold()
        self.assertEqual(c.bir.unmapped_meters(),
                         [meter_key(1), meter_key(3)])
        self.assertFalse(c.bir.map_complete())
        self.assertIn("MAP IS INCOMPLETE", lines(h, "I@A002"))

    def test_every_tank_prints_empty_including_the_healthy_ones(self):
        """The complaint is "No BIR Data", not "no BIR data for tank 1": one
        unmappable meter stops the whole console reporting."""
        c, h = self.a_1xx_site_with_a_manifold()
        got = lines(h, "I@A400")
        self.assertEqual(got.count("EMPTY"), 3, got)
        self.assertEqual(got.count(c.bir.HISTORY_HEAD), 3)
        self.assertIsNone(c.bir.row(3, "daily"), "tank 3 too")

    def test_the_manifolded_pair_still_prints_as_one_block(self):
        c, h = self.a_1xx_site_with_a_manifold()
        got = lines(h, "I@A400")
        first = got.index("T 1:DIESEL")
        self.assertEqual(got[first + 1], "T 2:DIESEL")

    def test_three_hundred_software_is_the_guides_prescription(self):
        """"Version 3XX software is required." The same console, same site,
        same manifold, on a 3XX board."""
        c, _h = self.a_1xx_site_with_a_manifold()
        c.board, c.version = "E7", 33
        c.tick()
        self.assertEqual(c.family(), "3XX")
        self.assertTrue(c.bir.available(1))
        self.assertTrue(c.bir.map_complete())
        self.assertIsNotNone(c.bir.row(1, "daily"))

    def test_a_console_that_has_mapped_nothing_has_an_incomplete_map(self):
        """Hardware rather than a reading of the page: a TLS-350 running
        326.01 with no meters answers I@A002 with MAP IS INCOMPLETE, where
        vacuous truth would call an empty map complete."""
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.meters = {}
        self.assertFalse(c.bir.map_complete())
        self.assertIsNone(c.bir.row(1, "daily"))

    def test_a_meter_pointing_at_a_tank_with_no_meter_data_is_unmapped(self):
        """The first of p.12-8's own five, and the one a site gets wrong
        most often: "In-tank programming parameter Meter Data Present set to
        NO"."""
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.meter_flow = {1: 100.0}
        del c.values["S61501"]
        self.assertFalse(c.bir.available(1))
        self.assertEqual(c.bir.unmapped_meters(), [meter_key(1)])
        self.assertFalse(c.bir.map_complete())


class ARetiredMeterSuspendsBir(unittest.TestCase):
    """FIDELITY G8's third map-completeness condition. 576013-818 p.12-8:
    "A previously 'retired' meter is reactivated. If an unmapped meter has
    not been reported by a POS within 24 hours of the last report, the meter
    is declared 'retired'. A retired meter may be a phantom meter incorrectly
    reported by the POS, or it may be a seldom heard from meter, such as one
    connected to a kerosene tank. Until the 'retired' meter is mapped, every
    time the meter is activated, and for 24 hours thereafter, BIR is
    suspended."

    One 24-hour window used both ways round: silence for that long writes a
    meter off, and its coming back costs the site that long again.
    """

    def a_site_with_a_phantom(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.meter_flow = {1: 100.0, 9: 50.0}       # 9 is on no tank
        for _ in range(4):
            c.clock_offset += 900.0
            c.tick()
        return c

    def quiet(self, c, hours):
        c.meter_flow = {1: 100.0}
        c.clock_offset += hours * 3600.0
        c.tick()

    def speaks(self, c):
        c.meter_flow = {1: 100.0, 9: 50.0}
        c.clock_offset += 900.0
        c.tick()

    def test_an_unmapped_meter_stops_the_console_reporting(self):
        c = self.a_site_with_a_phantom()
        self.assertEqual(c.bir.unmapped_meters(), [meter_key(9)])
        self.assertFalse(c.bir.map_complete())

    def test_a_day_of_silence_writes_it_off(self):
        """Which is the point of the state: a phantom the POS mentioned once
        would otherwise stop a site reconciling for ever."""
        c = self.a_site_with_a_phantom()
        self.quiet(c, 25)
        self.assertTrue(c.bir.retired(9))
        self.assertEqual(c.bir.unmapped_meters(), [])
        self.assertTrue(c.bir.map_complete())

    def test_its_coming_back_costs_the_site_another_day(self):
        c = self.a_site_with_a_phantom()
        self.quiet(c, 25)
        self.speaks(c)
        self.assertEqual(c.bir.suspended_meters(), [meter_key(9)])
        self.assertFalse(c.bir.map_complete())
        self.quiet(c, 20)
        self.assertEqual(c.bir.suspended_meters(), [meter_key(9)],
                         "20 hours is inside the window")
        self.quiet(c, 6)
        self.assertEqual(c.bir.suspended_meters(), [])
        self.assertTrue(c.bir.map_complete())

    def test_mapping_it_ends_the_suspension_at_once(self):
        """"Until the 'retired' meter is mapped" -- so the window is checked
        against the map and not only against the clock."""
        c = self.a_site_with_a_phantom()
        self.quiet(c, 25)
        self.speaks(c)
        self.assertFalse(c.bir.map_complete())
        c.meters[9] = 3
        self.assertEqual(c.bir.suspended_meters(), [])
        self.assertTrue(c.bir.map_complete())

    def test_a_mapped_meter_is_never_retired(self):
        """"If an UNMAPPED meter has not been reported" -- a kerosene tank's
        meter that sells twice a year is not written off if it has a tank."""
        c = self.a_site_with_a_phantom()
        c.meters[9] = 3
        self.quiet(c, 30)
        self.assertFalse(c.bir.retired(9))
        self.assertTrue(c.bir.map_complete())


class TheAlarmThresholdKeepsItsDocumentedRange(unittest.TestCase):
    """FIDELITY G10's smaller residue. 576013-623 Rev AN p.17-3: "Press
    CHANGE. Enter the percent of total meter sales (throughput). Note - the
    Alarm Threshold must be between 0.00 and 5.00 percent."

    `S79800` carried no bounds at all, so a site could program 99.99 and the
    Periodic Reconciliation Alarm would never fire -- the threshold is a
    percentage of throughput plus an offset, and a 99.99% one is a limit no
    real variance reaches. The neighbouring meter calibration offset has
    carried its own bounds all along.
    """

    def a_console(self):
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c, Handler(c, verbose=False)

    def set_threshold(self, h, text):
        return send(h, "S79800" + text)

    def test_the_page_s_own_two_ends_are_accepted(self):
        c, h = self.a_console()
        for text, want in (("00.00", 0.0), ("05.00", 5.0), ("01.00", 1.0)):
            self.assertNotIn("9999", self.set_threshold(h, text), text)
            self.assertAlmostEqual(c.limit("798", 0), want, places=4)

    def test_a_percent_over_five_is_refused(self):
        c, h = self.a_console()
        self.set_threshold(h, "01.00")
        for text in ("05.01", "07.00", "99.99"):
            self.assertIn("9999", self.set_threshold(h, text), text)
            self.assertAlmostEqual(c.limit("798", 0), 1.0, places=4,
                                   msg="a refused Set must not store")

    def test_the_default_is_the_manuals_own(self):
        """"1.00% of throughput plus 130 gallons (492 litres) offset"."""
        c, _h = self.a_console()
        row = c.bir.current(1, "periodic")
        row["sales"] = 100000.0
        self.assertAlmostEqual(c.bir.threshold(row), 1130.0, places=3)
