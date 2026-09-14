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
"""The manuals' own worked examples, replayed.

`test_citations.py` proves every line this console draws traces to a page of
a manual. It cannot prove the line carries the right NUMBER, and for a long
time it did not: the SYSTEM CONFIGURATION screen built its two resistance
columns out of a hash of the module's name, and passed the citation audit
every time, because the words on the line were real words from a real page.

That is the difference between PROVENANCE and FIDELITY. This file is the
fidelity half. The manuals are full of worked examples -- a report printed
with real numbers in it -- and an example is a test somebody else already
wrote. Replaying them is the cheapest defence this project can have against
a value that looks plausible and means nothing.

It is a RATCHET, like `test_citations.py` and `test_tape.py`. Several of
these examples do not reproduce yet, and each one that does not is named in
`STILL_WRONG` against its entry in `FIDELITY.md`. Fixing one is visible,
because its name has to come out of that set; breaking a new one is loud,
because an unnamed failure fails the suite.
"""
import math
import re
import sys
import unittest
from os.path import dirname, abspath

sys.path.insert(0, dirname(dirname(abspath(__file__))))

from tls350sim.console import Console, FIELDS, SETUP_MENU     # noqa: E402
from tls350sim.wire import Handler, SOH                       # noqa: E402

with open(dirname(dirname(abspath(__file__)))
          + "/tls350sim/fieldwidths.json", encoding="utf-8") as _fh:
    import json as _json
    FIELD_WIDTHS = _json.load(_fh)["width"]
from tests.test_console import a_tank, float_value            # noqa: E402


def a_manual_tank(volume, water=0.0):
    """The tank the Operator's Manual works its examples on.

    10,000 gallons and 96 inches: 576013-610 Rev AC p.8-1 and the p.1-3
    inset both use it, and the serial manual's i201 example is the same
    shape. Every height and volume below comes off one of those pages.
    """
    c = Console()
    a_tank(c, 1, volume=volume, water=water, full=10000.0)
    c.values["S60701"] = "01" + float_value(96.0)
    return c


def cylinder_fraction(part):
    """How full a horizontal cylinder is at a depth ratio.

    Not the console's arithmetic -- the geometry a strapping chart follows,
    here so the manual's own heights can be checked against something other
    than the code under test.
    """
    part = max(0.0, min(1.0, part))
    return ((math.acos(1 - 2 * part)
             - (1 - 2 * part) * math.sqrt(max(0.0, 4 * part * (1 - part))))
            / math.pi)


# ---------------------------------------------------------------------------
# the examples, each returning (what the console gave, what the page gives)

def ex_height_9038():
    """576013-610 Rev AC p.1-3: 9038 gallons stands 81.37 inches deep."""
    c = a_manual_tank(9038.0)
    return c.volume_at(1, 81.37), 9038.0


def ex_height_8518():
    """576013-610 Rev AC p.8-1: 8518 gallons stands 76.26 inches deep."""
    c = a_manual_tank(8518.0)
    return c.volume_at(1, 76.26), 8518.0


def ex_water_volume_1_37():
    """p.1-3 inset: WATER 1.37 INCHES gives WATER VOL 28 GALS."""
    c = a_manual_tank(9038.0, water=1.37)
    return c.volume_at(1, 1.37), 28.0


def ex_tc_expands_cold_product():
    """576013-635 Rev AA p.58: 5329 gallons at 37.39 F is 5413 TC.

    Correcting product colder than the 60 F reference UP to it expands it,
    so TC is the larger number. The console's own temperature band is 48 to
    62 F, so a TC volume below gross is wrong for nearly all of it. Asserted
    as the relationship rather than the figure, because the temperature here
    wanders and cannot be pinned.
    """
    c = a_manual_tank(5329.0)
    if c.product_temperature(1) >= 60.0:
        return 1.0, 1.0                    # the one case where TC is smaller
    return (1.0 if c.tc_volume(1) > 5329.0 else 0.0), 1.0


def ex_ground_temp_is_not_a_short():
    """576013-818 Rev AB Fig 6-22: "If Value = <1000 thermistor may be
    shorted", on the same page as 26100 = 40F and 5820 = 100F.

    Whatever this console reports, it must not be a value the manual calls
    a fault on a healthy console.
    """
    c = Console()
    text = c.diag_reading("sensor_groundtemp", 1)
    found = re.search(r"VALUE\s*=\s*([0-9.]+)", str(text))
    got = float(found.group(1)) if found else 0.0
    return (1.0 if got >= 1000.0 else 0.0), 1.0


def a_wire(volume=5000.0, full=10000.0, diameter=96.0):
    """A console with a comm card in it, and one tank off a manual page."""
    c = Console()
    c.modules["rs232"] = 1
    a_tank(c, 1, volume=volume, full=full)
    c.values["S60701"] = "01" + float_value(diameter)
    return c


def a_report(c, command):
    """One Display-format report, as the wire draws it.

    Stripped of the end-of-message characters the card appends, so a column
    at the end of a line parses as the number it is.
    """
    out = Handler(c, verbose=False).handle(SOH + command).decode("latin-1")
    return "".join(ch for ch in out if ch >= " " or ch.isspace())


def ex_i21a_height_is_the_chart():
    """576013-635 Rev AA p.84, and it is self-consistent: `95% ULLAGE 596`
    fixes the capacity at 10,000, so the row is fully determined and its
    HEIGHT is 80.00.

    The report used to compute `fraction * diameter` -- a straight line
    through a cylinder -- and print 85.48, five and a half inches out, while
    `height_at()` sitting beside it had the chart right (X1).
    """
    c = a_wire(volume=8904.0)
    c.values["S56400"] = "1"                    # the 95% ullage S564 selects
    row = a_report(c, b"I21A01").splitlines()[-1]
    return float(row.split()[4]), 80.00


def ex_i21a_ullage_is_95_percent():
    """The same row's `95% ULLAGE 596`, which is what pins the capacity."""
    c = a_wire(volume=8904.0)
    c.values["S56400"] = "1"
    row = a_report(c, b"I21A01").splitlines()[-1]
    return float(row.split()[3]), 596.0


def _i216_head_row():
    """I216's chart heading, on a 20,000 gallon tank that is NOT on the
    LINEAR profile -- which is the only way to see the defect, because a
    linear tank holds its volume in the very code the wrong lookup asked."""
    c = a_wire(volume=8904.0, full=20000.0, diameter=120.0)
    c.set_tank_profile(1, "02")                 # 20 point, so 60A is empty
    # the figures are held right against their columns now, so the row does
    # not start with its own digit -- 576013-635 Rev AA p.84. FIDELITY S5.
    return [ln for ln in a_report(c, b"I21601").splitlines()
            if ln.strip()[:1].isdigit() and "." in ln][0]


def _i216_head():
    return float(_i216_head_row().split()[1])


def _i216_slope():
    return float(_i216_head_row().split()[2])


def ex_i216_heads_the_tanks_own_volume():
    """I216's chart heading asked function code 60A, which is the LINEAR
    profile's full volume alone, and fell back to a literal 10,000 on every
    other profile. A 20,000 gallon tank headed itself 10000 (X2).
    """
    return _i216_head(), 20000.0


def ex_i216_slope_is_the_tanks_own():
    """And the slope beside it: 20,000 over 120 inches is 166.67, not the
    83.33 the halved volume gave."""
    return _i216_slope(), 166.67


def ex_i215_amount_is_a_mass():
    """576013-635 Rev AA p.79: `AMOUNT: 3303 19813*`, where 3303 gallons at
    5.9987 pounds is 19,814 pounds.

    The AMOUNT row is shared with 213, whose second column IS the TC volume,
    so 215 printed a TC-volume delta -- the wrong quantity, sixteen and a
    half thousand pounds out. Asserted as the ratio, because this bench's
    density is its own rather than the page's (X3).
    """
    c = a_wire(volume=1157.0)
    c.values["S61001"] = "0101"                 # a one-minute settle
    c.deliveries.tick()
    for volume in (2500.0, 4460.0, 4460.0):
        c.tank_level[1]["volume"] = volume
        c.clock_offset += 60.0
        c.deliveries.tick()
    c.clock_offset += 300.0
    c.deliveries.tick()
    # the label is right-aligned to column 9 on this report, which is where
    # the manual's own sample puts it (S4)
    row = [ln.strip() for ln in a_report(c, b"I21501").splitlines()
           if ln.strip().startswith("AMOUNT:")][0]
    gallons, mass = row.split()[1], row.split()[2].rstrip("*")
    return float(mass) / float(gallons), c.product_density(1)


def ex_i215_amount_carries_the_density_flag():
    """And the manual's trailing `*`, the density-defaulted mark the
    computer format already emitted and the display format did not."""
    c = a_wire(volume=1157.0)
    c.values["S61001"] = "0101"
    c.deliveries.tick()
    for volume in (2500.0, 4460.0, 4460.0):
        c.tank_level[1]["volume"] = volume
        c.clock_offset += 60.0
        c.deliveries.tick()
    c.clock_offset += 300.0
    c.deliveries.tick()
    # the label is right-aligned to column 9 on this report, which is where
    # the manual's own sample puts it (S4)
    row = [ln.strip() for ln in a_report(c, b"I21501").splitlines()
           if ln.strip().startswith("AMOUNT:")][0]
    return (1.0 if row.rstrip().endswith("*") else 0.0), 1.0


def ex_stamp_has_no_seconds():
    """576013-635 draws MMM DD, YYYY HH:MM XM over its responses 332 times
    against twelve of the seconds form, and all twelve of those describe the
    format setting rather than stamping a report."""
    c = Console()
    return (0.0 if re.search(r"\d:\d\d:\d\d", c.clock_stamp()) else 1.0), 1.0


# tolerances are absolute, in the unit the example is measured in
EXAMPLES = {
    "height of 9038 gallons": (ex_height_9038, 5.0),
    "height of 8518 gallons": (ex_height_8518, 5.0),
    "water volume at 1.37 inches": (ex_water_volume_1_37, 2.0),
    "TC volume expands cold product": (ex_tc_expands_cold_product, 0.001),
    "ground temperature is not a short": (ex_ground_temp_is_not_a_short, 0.001),
    "a report stamp has no seconds": (ex_stamp_has_no_seconds, 0.001),
    "I21A's height is the chart": (ex_i21a_height_is_the_chart, 0.02),
    "I21A's ullage is 95 percent": (ex_i21a_ullage_is_95_percent, 1.0),
    "I216 heads the tank's own volume":
        (ex_i216_heads_the_tanks_own_volume, 1.0),
    "I216's slope is the tank's own": (ex_i216_slope_is_the_tanks_own, 0.01),
    "I215's AMOUNT is a mass": (ex_i215_amount_is_a_mass, 0.001),
    "I215's AMOUNT carries the density flag":
        (ex_i215_amount_carries_the_density_flag, 0.001),
}

# Each of these is an entry in FIDELITY.md. Take a name out when you fix it.
STILL_WRONG = {
}


class TheManualsWorkedExamples(unittest.TestCase):
    """A worked example is a test its author already wrote."""

    def failures(self):
        out = set()
        for name, (fn, tol) in EXAMPLES.items():
            got, want = fn()
            if abs(got - want) > tol:
                out.add(name)
        return out

    def test_the_gap_is_the_gap_it_was(self):
        """Fixing one is visible; breaking a new one is loud."""
        failed = self.failures()
        self.assertEqual(
            sorted(failed - set(STILL_WRONG)), [],
            "a manual's worked example stopped reproducing")
        self.assertEqual(
            sorted(set(STILL_WRONG) - failed), [],
            "an example named in STILL_WRONG now passes: take it out of the "
            "set and out of FIDELITY.md")

    def test_the_cylinder_is_what_the_manual_draws(self):
        """The check on the check: the geometry reproduces both of the
        manual's heights to a hundredth of an inch, so when the console
        disagrees it is the console that is wrong."""
        for volume, height in ((9038.0, 81.37), (8518.0, 76.26)):
            part = cylinder_fraction(height / 96.0) * 10000.0
            self.assertAlmostEqual(part, volume, delta=2.0,
                                   msg=f"{volume} at {height}")


class AWholeNumberIsTruncated(unittest.TestCase):
    """576013-610 Rev AC says so twice, on two pages, about two different
    quantities, and there is no sample on this shelf that rounding fits and
    truncation does not. `"%.0f"` rounds -- and rounds to EVEN at a half
    besides, so it was wrong in two directions at once. FIDELITY Y11."""

    def test_the_mass_row_of_the_p_4_2_example(self):
        """"MASS = 15290 LBS" for "VOLUME = 2549 GALS" at "DENSITY = 5.9987
        LBS/GAL". 2549 * 5.9987 is 15290.686."""
        c = a_manual_tank(2549.0)
        c.values["S61E01"] = "01" + float_value(5.9987)
        self.assertAlmostEqual(2549.0 * 5.9987, 15290.686, places=3)
        self.assertEqual(c.live_reading("mass", 1).strip(), "15290 LBS")

    def test_the_water_row_of_the_p_1_3_example(self):
        """"WATER VOL = 28 GALS" for 1.37 inches of water in a 10,000
        gallon, 96 inch tank. The chart reads 28.82 there."""
        c = a_manual_tank(9038.0, water=1.37)
        self.assertAlmostEqual(c.water_volume(1), 28.82, places=1)
        self.assertEqual(c.live_reading("water_vol", 1).strip(), "28 GALS")

    def test_the_paper_and_the_wire_say_what_the_display_says(self):
        """One rule in three places, because a console that truncates on the
        screen and rounds on the paper is two consoles."""
        from tls350sim import printer
        c = a_manual_tank(9038.0, water=1.37)
        c.modules["rs232"] = 1
        rows = printer.inv_rows(c, 1, c.full_volume(1))
        water = [r for r in rows if "WATER VOL" in r][0]
        self.assertIn("28 GALS", water)
        self.assertNotIn("29", water)
        report = Handler(c, verbose=False).handle(
            SOH + b"I20101").decode("latin-1")
        self.assertIn("9038", report)

    def test_it_truncates_toward_zero_rather_than_downward(self):
        """A console drops the digits it has no column for; it does not take
        a floor. -0.4 gallons is 0, not -1."""
        from tls350sim.masks import whole
        self.assertEqual(whole(-0.4), "0")
        self.assertEqual(whole(0.9), "0")
        self.assertEqual(whole(0.5), "0")
        self.assertEqual(whole(15290.686, 8), "   15290")


class ThingsTrueOfTheDataWithoutReadingAManual(unittest.TestCase):
    """Three invariants the field table has to satisfy on its own.

    They need no citation, which is what makes them cheap: a range nobody
    can enter, an option list that disagrees with its own twin, and a step
    that writes its condition in prose and does not implement it are all
    wrong on the face of the data.
    """

    def mask_ceiling(self, mask):
        """The largest value a mask of zeroes can draw.

        A mask can carry its unit -- "000%" is three digits and a percent
        sign -- and this used to bail out on anything that did, so five
        percent fields with a max of a million went unseen. All five held
        GALLONS in a field the setup manual says to enter a percent into,
        which is how a 20,000 gallon tank came to draw HIGH PRODUCT: 18000%.
        The suffix comes off before the digits are counted.
        """
        if not mask:
            return None
        body = re.sub(r"[^0-9.+-]+$", "", mask)
        if not body or set(body) - set("0.+-"):
            return None
        body = body.lstrip("+-")
        whole, _, frac = body.partition(".")
        if not whole or set(whole + frac) - {"0"}:
            return None
        return float("9" * len(whole) + ("." + "9" * len(frac) if frac else ""))

    # FIDELITY F3, F4 and the SUSPECT list: a max its own mask cannot draw.
    # S62401 and S62701, the two water limits, came off this list when the
    # ceiling stopped being the metric one on a US console: "in inches (5.0
    # maximum) or millimeters (199 maximum), depending on the units
    # established in System Setup", and 5.0 is drawable by `0.0`.
    # Down from fifteen to one. 576013-635 states a decimal field width for
    # every settable code in its Command Format block -- `S604TTGGGGGG`,
    # `S607TTIII.hh`, `S753PPLLL` -- and that is a ceiling independent of any
    # range a chapter states and of the panel mask. Twenty-three maxima and
    # two masks came off it; see FIDELITY F8.
    #
    # S61E01, tank density, is the one that is NOT a mistake to fix. Its two
    # numbers each have a citation and they disagree: 576013-623 Rev AN p.7-4
    # draws the screen `DENSITY            :0.0000`, one integer digit, and
    # 576013-635's decimal form is `dd.dddd`, two. The wire is not merely
    # more generous -- the setup manual says a density may be entered as "an
    # API number", and API gravity runs past sixty, which one digit cannot
    # hold. Narrowing the max to match the drawing would refuse a value the
    # same manual tells you to enter; widening the mask would draw a screen
    # no manual draws. It stays here until a page settles it.
    UNDRAWABLE = {
        "S61E01",
    }

    def test_a_max_is_drawable_by_its_own_mask(self):
        bad = set()
        for code, f in FIELDS.items():
            cap = self.mask_ceiling(f.get("mask"))
            if cap is not None and f.get("max") is not None and f["max"] > cap:
                bad.add(code)
        self.assertEqual(sorted(bad - self.UNDRAWABLE), [],
                         "a new field accepts a value it cannot draw")
        self.assertEqual(sorted(self.UNDRAWABLE - bad), [],
                         "a field in UNDRAWABLE is fixed: take it out")

    # FIDELITY S15. A Display sample that prints `16 HOURS` is printing a
    # unit, and this console printed the number alone on eighteen of the
    # twenty-three codes whose sample carries one. The mechanism was already
    # there -- a field's `wire_format`, "%.1f PSI" -- and three fields used
    # it. `wiretables.to_places` even documents the arrangement from the
    # other side: "`4.0 IN` and `12000 PSI` carry a unit from the field
    # tables, and reformatting the pair of them would drop it."
    #
    # The sample is the statement of both the unit and the precision, so
    # this measure reads the samples rather than a list somebody typed.
    UNIT_WORDS = ("HOURS", "MINUTES", "SECONDS", "PSI", "INCHES", "FEET",
                  "GPH", "GALLONS", "GAL", "DEG", "IN", "LPH", "LITERS",
                  "MM", "SEC", "MIN", "DAYS", "PERCENT", "VOLTS", "OHMS",
                  "KPA")
    # The four whose unit this cannot reach, each for its own reason:
    #
    # * `77F` prints TWO lengths on one row, `50 FEET` and `250 FEET`, out
    #   of one code's part fields. This console answers it with a bare `0`
    #   -- the whole row is missing, not the unit.
    # * `796` carries its unit in its MASK, `00 DAYS`, because the panel
    #   draws it there; the wire answers a bare stamp for it either way.
    # * `7AD`, the WPLLD secondary pipe length, has no field at all. It is
    #   one of the ninety-six in F10 that no panel screen reaches.
    # * `887` draws `DIAL TONE VALIDATION INTERVAL:   32 HOURS`, a labelled
    #   line rather than a table, and this console answers it with `0`.
    # Three came off and none of the three was ever about the unit, which is
    # what S15 said of all four of them.
    #
    # `887` is answered under a comm board HEADER over a labelled line and
    # this console had neither; the generator had filed the header as the
    # report's title, because that is what a first line looks like.
    #
    # `77F` heads two value columns, `1.5 IN DIAM LEN` and `2.5 IN DIAM LEN`,
    # and one of them is 789's value rather than its own -- so no row could
    # be built from the code that was asked, and the table renderer refused
    # a `tag` layout of three columns outright.
    #
    # `7AD` had no field at all, one of F10's ninety-six, and its twin 77F
    # has had one all along. Giving it the same one was the whole fix: its
    # own report is 7A9's shape and drew itself the moment there was a value.
    #
    # `796` is the one left, and it is a bare stamp rather than a missing
    # unit. See FIDELITY S15 and S18.
    NO_UNIT_YET = {"796"}

    @classmethod
    def sampled_units(cls):
        """{code: unit} for every Display sample whose value ends in one."""
        import json
        import os
        import re
        here = dirname(dirname(abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "wiretitles.json"),
                  encoding="utf-8") as fh:
            titles = json.load(fh)
        out = {}
        for tok, entry in sorted(titles.items()):
            for line in entry.get("sample") or []:
                hit = re.search(r"([0-9][0-9.,-]*)\s+([A-Z]+)\s*$", line)
                if hit and hit.group(2) in cls.UNIT_WORDS:
                    out[tok] = hit.group(2)
                    break
        return out

    def test_a_report_prints_the_unit_its_own_sample_prints(self):
        from tests.test_coverage import a_full_console
        want = self.sampled_units()
        self.assertGreater(len(want), 15, "the measure found nothing to check")
        c = a_full_console()
        h = Handler(c, verbose=False)
        missing = set()
        for tok, unit in want.items():
            reply = h.handle(SOH + f"I{tok}01".encode() + b"\r")
            if unit not in reply.decode("latin-1"):
                missing.add(tok)
        self.assertEqual(sorted(missing - self.NO_UNIT_YET), [],
                         "a report drops the unit its sample prints")
        self.assertEqual(sorted(self.NO_UNIT_YET - missing), [],
                         "a code in NO_UNIT_YET prints its unit now: take "
                         "it out")

    # FIDELITY S14. What a real console prints, where it disagrees with the
    # page. `tools/console_corrections.json` is the record and
    # `build_wire_titles.py` applies it; this asserts it was applied, so a
    # regeneration that drops the corrections fails HERE, on a clone with no
    # capture and no manuals, rather than silently.
    def test_the_console_corrections_are_in_the_generated_titles(self):
        import json
        import os
        here = dirname(dirname(abspath(__file__)))
        with open(os.path.join(here, "tools", "console_corrections.json"),
                  encoding="utf-8") as fh:
            fixes = json.load(fh)
        with open(os.path.join(here, "tls350sim", "wiretitles.json"),
                  encoding="utf-8") as fh:
            titles = json.load(fh)
        self.assertTrue([k for k in fixes if not k.startswith("_")],
                        "no corrections to check")
        for code, fix in fixes.items():
            if code.startswith("_"):
                continue
            entry = titles.get(code)
            self.assertIsNotNone(entry, code)
            for key, value in fix.items():
                if key.startswith("_"):
                    continue
                if value is None:
                    self.assertNotIn(key, entry, f"{code}.{key}")
                else:
                    self.assertEqual(entry.get(key), value, f"{code}.{key}")

    # FIDELITY F12. The codes and settings a console STORES and does not
    # have: programmable from the panel, round-tripping over the wire,
    # printed in the setup report, and named nowhere in the Python. The
    # entry carried this as a count somebody had taken by hand, and it
    # drifted -- it read "234 distinct function codes ... for 61 of them",
    # and by the time anybody re-measured it was 250 and 32. A count in
    # prose is a count nobody re-runs, so it is a ratchet now: reading one
    # of these takes its name out of the set in the same commit.
    #
    # The measure is static and errs the SAFE way. A code reached through an
    # f-string is counted as read, because its digits are in the source; a
    # code reached through a variable would be counted as unread. Twelve
    # entries below are the second case rather than the first -- see
    # UNREAD_SETTINGS.
    #
    # `75D`, `786` and `7A6` -- the three line-leak families' DISPENSE MODE
    # -- nearly came off this list and did not, which is worth the note.
    # Pump Threshold's condition was implemented against them and then taken
    # back out: real paper prints that row on a site with NO line leak module
    # at all, so the manual's "only enabled when ... Manifolded: Sequential"
    # is about the feature and not about the screen. See FIDELITY F5.
    UNREAD_CODES = {
        "50C", "50D", "514", "5BD", "60F", "633",
        "634", "63A", "63D", "755", "75C", "75D", "75E",
        # two of the three the measure had been hiding behind a longer
        # number: `516` Re-direct Local Printout and `536` the per-port
        # RS-232 security code. The third, `632` Tank Test Siphon Break,
        # was implemented the moment it became visible -- see FIDELITY H9.
        # `536` came off in its turn: a real console answers `I50400` with
        # 536's per-port table, so `wiretables.SHOWN_AS` reads it. See
        # FIDELITY S14.
        "516",
        # `75B`, `787` and `7A7` came off together: the three LINE DISABLE
        # ALARM ASSIGNMENT codes carry 52C's payload and 52C's report shape,
        # and `wirelists.LINE_DISABLE` renders all three. See FIDELITY S17.
        "75F", "77A", "77B", "77D", "77E", "786", "78A",
        "792", "79A", "7A6",
        # The four a PROSE-reading measure was hiding. `package_source` used
        # to hand this the whole text of every module, comments and
        # docstrings included, on the reading that a code named anywhere
        # counts -- so a code the console genuinely never reads came off the
        # list the moment somebody wrote its number in a comment ABOUT it.
        # 634 is how it was caught: a note explaining that its stored value
        # reached a report unmasked named the code, and the ratchet reported
        # a reader that did not exist.
        #
        # **Three of these four were never named by a comment at all.** A
        # function code and a manual PAGE are the same three digits, and
        # `p.515`, `p.519` and `p.554` are citations in `wire.py` and
        # `wirelines.py` that have nothing to do with 515, 519 or 554. The
        # measure read a page number as a reader. Only 631 is named by prose
        # actually about it.
        #
        # A comment is not a reader. See FIDELITY F12 and S18.
        "515", "519", "554", "631",
    }

    # The console's own settings, the ones no function code covers. The
    # twelve `autotx_*` are the measure erring safe and not a shortfall:
    # `autotx.py` reads every one of them, through `f"autotx_{signal}"`,
    # which is the one form this measure cannot see. Their delay and repeat
    # times came OUT of this set when the engine was written -- see
    # FIDELITY P3.
    UNREAD_SETTINGS = {
        "autotx_1", "autotx_2", "autotx_3", "autotx_4", "autotx_5",
        "autotx_6", "autotx_7", "autotx_8", "autotx_9", "autotx_10",
        "autotx_11", "autotx_12",
        "avg_sales_1", "avg_sales_2", "avg_sales_3", "avg_sales_4",
        "avg_sales_5", "avg_sales_6", "avg_sales_7",
        # `blend_partners` and `blender` came off together. Both were stored
        # by PLLD LINE LEAK SETUP's last two steps and read by nothing, and
        # 576013-623 p.10-11 says in one sentence what they are for: "When a
        # site has mechanical blenders, the lines can be assigned to a blend
        # set. This change affects the scheduling of precision line testing,
        # 0.2 and 0.1." `pressure.Lines.blend_set` reads both, and a
        # precision test on a set with a partner dispensing now waits
        # instead of starting -- which is 577013-344 p.21 cause 5, "If the
        # site is extremely busy, especially if blenders are present, there
        # may not be sufficient idle time to complete a Periodic or Annual
        # test unless the station is shut down". See FIDELITY F12.
        "custom_delivery", "custom_high", "custom_low", "custom_max",
        "custom_overfill", "euro_prefix",
        "evr_al_max", "evr_al_min", "evr_high_orvr", "evr_hose_name",
        "pmc_off", "pmc_on", "print_precision_line",
        # `country` joined them when the measure stopped reading comments:
        # the only three places the package names it are prose -- two in
        # `ifsf.py` explaining that the IFSF country element USED to come off
        # 51F and does not now, and one about a password. Nothing stores it
        # and nothing reads it. See FIDELITY F12.
        "country",
    }

    @classmethod
    def composed_settings(cls):
        """The settings whose NAME is built out of two halves.

        The same blind spot `gate_codes` was written for, one step further
        on. `alarmgroups.py` names all four function prefixes and all
        seventeen group keys, and `console._note_alarm_group` glues one of
        each together -- so sixty-eight settings that are written on every
        assignment and read on every one of these screens are spelled
        nowhere as a whole word. A measure that reads only whole words
        reported every one of them as dead. See FIDELITY F12.
        """
        from tls350sim import alarmgroups
        return {f"{prefix}_{key}"
                for prefix in alarmgroups.PREFIX.values()
                for key, _line, _aa in alarmgroups.GROUPS}

    @classmethod
    def reads_code(cls, code, source):
        """Does the package mention this function code at all?

        Still generous where it has to be -- a code reached through an
        f-string cannot be seen either way -- so the set this feeds can only
        ever be too SMALL. Two things it must not do, and it used to do
        both.

        It must not count a code as read because its three characters happen
        to fall inside a longer number, which is how `632`, Tank Test Siphon
        Break, was counted as read for as long as `GROUND_A` has been
        `-5.615958193304632`. `536` had the same accident against
        `4937.939536502947` and `65536`, and `516` against the colour
        `#15161a`. Three unread codes hidden by three unrelated constants.

        And it must not count a COMMENT as a reader. `package_source` handed
        this the whole text of the package, prose included, so writing a note
        about a code took that code off the defect list -- the ratchet went
        red for 634 because a comment explaining what was wrong with 634
        named it. Nine more codes were behind that one, and none of them is
        touched by any line of code. See FIDELITY F12.

        So: the key form `S632` counts wherever it appears, and the bare
        code counts only where it is not embedded in a run of digits. A
        hex range test like `0x7C4 <= fn <= 0x7C9` still counts, because
        `x` is not a digit -- which is the generous direction and the right
        one. See FIDELITY F12.
        """
        if "S" + code in source:
            return True
        return bool(re.search(r"(?<![0-9])" + code + r"(?![0-9])", source))

    @staticmethod
    def package_source():
        """Every module's CODE, with its comments and docstrings taken out.

        A comment naming a function code is a note about that code, not the
        console reading it, and this used to hand back the whole file. See
        `reads_code`.
        """
        import os
        import tokenize
        here = os.path.join(dirname(dirname(abspath(__file__))), "tls350sim")
        out = []
        for name in sorted(os.listdir(here)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                try:
                    toks = list(tokenize.generate_tokens(fh.readline))
                except tokenize.TokenError:      # pragma: no cover
                    fh.seek(0)
                    out.append(fh.read())
                    continue
            # A STRING that opens a statement is a docstring: nothing
            # consumes it. The depth test is what makes that true --
            # `"evr_vac_type": ("SV4E00", ...)` starts a line INSIDE a dict
            # literal, so it follows an NL exactly as a docstring does, and
            # dropping it lost a setting the console reads.
            opener = {tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                      tokenize.NL, tokenize.ENCODING}
            prev, depth = tokenize.INDENT, 0
            for tok in toks:
                if tok.type == tokenize.COMMENT:
                    continue
                if tok.type == tokenize.OP and tok.string in "([{":
                    depth += 1
                elif tok.type == tokenize.OP and tok.string in ")]}":
                    depth = max(depth - 1, 0)
                if (tok.type == tokenize.STRING and not depth
                        and prev in opener):
                    continue
                out.append(tok.string)
                out.append("\n")
                prev = tok.type
        return "".join(out)

    # The menu files that carry `when` gates. A code named in one of these
    # is READ -- the screen it gates is on the console or is not, which is
    # the console acting on the setting -- and the Python never spells it.
    # `consoledata.json` is in the list for its gates and not for its keys:
    # every code has a FIELDS entry there, so the whole file cannot be
    # searched as text or nothing would ever look unread.
    MENU_DATA = ("consoledata.json", "diagdata.json", "normaldata.json",
                 "recondata.json")

    @classmethod
    def gate_codes(cls):
        """Every setup code a screen's visibility hangs off.

        Five codes sat on `UNREAD_CODES` because this measure only ever
        looked at `.py` files, and a screen gate lives in the menu data:
        `51A` decides whether the four daylight-saving date screens exist,
        `759` picks which one of four schedule screens the line leak walk
        shows, `783` gates the passive 0.10 gph step, `80A` reshapes the
        whole relay walk, and `533` decides whether BOOK VARIANCE is a
        Reconciliation Mode function at all. See FIDELITY F12.
        """
        import json
        import os
        here = os.path.join(dirname(dirname(abspath(__file__))), "tls350sim")
        found = set()

        def gather(cond):
            code = cond.get("code")
            if isinstance(code, str) and re.match(r"^S[0-9A-F]{3}", code):
                found.add(code[1:4])
            for also in cond.get("and") or []:
                gather(also)

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("when", "choices_when") and isinstance(
                            value, dict):
                        gather(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for name in cls.MENU_DATA:
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                walk(json.load(fh))
        return found

    def test_the_settings_nothing_reads_are_the_ones_on_the_list(self):
        source = self.package_source()
        codes, settings = set(), set()
        for key in FIELDS:
            if key.startswith("set."):
                settings.add(key[4:])
            elif re.match(r"^S[0-9A-F]{3}", key):
                codes.add(key[1:4])
        gates = self.gate_codes()
        unread = {c for c in codes
                  if not self.reads_code(c, source) and c not in gates}
        self.assertEqual(sorted(unread - self.UNREAD_CODES), [],
                         "a setup code stopped being read: F12 grew")
        self.assertEqual(sorted(self.UNREAD_CODES - unread), [],
                         "a code in UNREAD_CODES has a reader now: "
                         "take it out and say so in FIDELITY F12")
        loose = {s for s in settings
                 if s not in source and s not in self.composed_settings()}
        self.assertEqual(sorted(loose - self.UNREAD_SETTINGS), [],
                         "a console setting stopped being read: F12 grew")
        self.assertEqual(sorted(self.UNREAD_SETTINGS - loose), [],
                         "a setting in UNREAD_SETTINGS has a reader now: "
                         "take it out and say so in FIDELITY F12")

    # FIDELITY N4. The alarms whose display label is still longer than the
    # display, each for a stated reason. Adopting 576013-623 Rev AN Table 5-1
    # took this from 41 to 15, because every one of the table's 140 labels
    # fits 24 columns and the serial manual's legend was never meant to.
    #
    #  * ten are APM, which Table 5-1 does not cover at all -- I8 explains
    #    why nothing on this shelf can help
    #  * three are the late system alarms 01/18 to 01/20, added after the
    #    revision of Table 5-1 that is on the shelf
    #  * CONTINUOUS HANDLE ON WARNING twice, at 21/10 and 26/09, where the
    #    same alarm at 06/06 does have a label, CONT HANDLE WARN. Borrowing
    #    it across is a judgement rather than a citation.
    OVERLONG_ALARMS = {
        # `0120` came out: Table 5-1 does not carry it, but 576013-610 Rev
        # AC Table 29-2 does, in a column headed `Display Message` -- `NO
        # MT COMM`, ten characters, with the p.1-10 Alarm Message Quick
        # Reference Index listing it under the same name. N4a had read
        # only Table 5-1 and concluded there was no display form anywhere.
        "0118", "0119",
        "3701", "3702", "3703", "3704", "3705",
        "3706", "3707", "3708", "3709", "3710",
    }

    # Screen lines the manuals draw WIDER than this console's 24 columns.
    # Every one is truncated on the panel today, and whether the real display
    # is wider or the real console spells them shorter is the open question
    # at the foot of FIDELITY.md.
    #
    # It is a ratchet because the count kept being used as evidence in that
    # argument while quietly moving: it was written as "counted rather than
    # sampled: there are 20 of them" and is 21, two having arrived since --
    # `COMM BOARD  : %d ({board})` and `r 1: <PUMP MONITOR LABEL>`, both of
    # which carry a placeholder that a real site's value may well fit inside.
    # One of the twenty turned out not to be a width question at all: a stray
    # DOUBLE SPACE in `PRESS  <STEP> TO CONTINUE`, one line among the seven
    # copies of it in the same file that have one space, and it had been
    # counted as evidence about the display width three times. Its `PRESS
    # <ENTER>` neighbour had the same typo and nothing had noticed, because
    # the citation audit normalises whitespace and cannot see it.
    #
    # A NEW over-wide line is a failure here rather than another data point.
    OVERWIDE_KEYS = ("head", "l1", "l2", "body")
    OVERWIDE_LINES = {
        # AccuChart's four, padded to a column that does not fit
        "ACCU DATA             X.XX",
        "ACCU DURATION         X.XX",
        "ACCU FITNESS          X.XX",
        "ACCU LENGTH        XXX.XX",
        # the fuel management pair, seven days each
        "LAST SALES-SUN: XXXX GALS", "PRED SALES-SUN: XXXX GALS",
        "LAST SALES-MON: XXXX GALS", "PRED SALES-MON: XXXX GALS",
        "LAST SALES-TUE: XXXX GALS", "PRED SALES-TUE: XXXX GALS",
        "LAST SALES-WED: XXXX GALS", "PRED SALES-WED: XXXX GALS",
        "LAST SALES-THR: XXXX GALS", "PRED SALES-THR: XXXX GALS",
        "LAST SALES-FRI: XXXX GALS", "PRED SALES-FRI: XXXX GALS",
        "LAST SALES-SAT: XXXX GALS", "PRED SALES-SAT: XXXX GALS",
        # and the four singles
        "P %d: LINE LEAK NUMBER %d",
        "r 1: STUCK RELAY: 999 SEC",
        "COMM BOARD  : %d ({board})",
        "r 1: <PUMP MONITOR LABEL>",
    }

    def test_the_over_wide_screen_lines_are_the_ones_on_the_list(self):
        import json
        import os
        here = os.path.join(dirname(dirname(abspath(__file__))), "tls350sim")
        found = set()

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if (key in self.OVERWIDE_KEYS and isinstance(value, str)
                            and len(value) > 24):
                        found.add(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for name in self.MENU_DATA:
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                walk(json.load(fh))
        self.assertEqual(sorted(found - self.OVERWIDE_LINES), [],
                         "a new screen line is wider than the display")
        self.assertEqual(sorted(self.OVERWIDE_LINES - found), [],
                         "a line in OVERWIDE_LINES fits now: take it out")

    def test_an_alarm_name_fits_the_display_it_is_drawn_on(self):
        """The display is 24 characters. A name wider than that clips
        mid-word on the panel and on a 24 column roll."""
        from tls350sim.console import STATUS_TYPES
        wide = {aa + nn for aa, types in STATUS_TYPES.items()
                for nn, name in types.items() if len(name) > 24}
        self.assertEqual(sorted(wide - self.OVERLONG_ALARMS), [],
                         "a new alarm name does not fit the display")
        self.assertEqual(sorted(self.OVERLONG_ALARMS - wide), [],
                         "an alarm in OVERLONG_ALARMS now fits: take it out")

    def test_every_label_the_manual_gives_is_the_one_the_console_shows(self):
        """576013-623 Rev AN Table 5-1's second column is headed
        "Display/print Alarm Label", so it IS what the console draws --
        which the serial manual corroborates by printing `INVALID FUEL
        LEVEL` in its own I206 sample where its legend says "Tank Invalid
        Fuel Level Alarm". Built by tools/build_alarm_labels.py."""
        import json
        import os
        from tls350sim.console import STATUS_TYPES
        path = os.path.join(dirname(dirname(abspath(__file__))),
                            "tls350sim", "alarmlabels.json")
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh)["label"]
        wrong = []
        for key, label in sorted(labels.items()):
            aa, nn = key[:2], key[3:]
            got = (STATUS_TYPES.get(aa) or {}).get(nn)
            if got is not None and got != label:
                wrong.append(f"{key}: {got!r} where the table says {label!r}")
        self.assertEqual(wrong, [])

    def test_every_label_the_manual_gives_has_an_alarm_to_hang_on(self):
        """The test above only compares the labels the console already
        carries, so an alarm the table names and this console has no type
        for was invisible to it. Eight were; the last three were `02/32`
        Fuel Quality, `21/19` Gross Test Needed and -- from the serial
        legend rather than the table -- `02/31` Density Warning. Zero is
        asserted rather than a ceiling, the way test_citations does."""
        import json
        import os
        from tls350sim.console import STATUS_TYPES
        path = os.path.join(dirname(dirname(abspath(__file__))),
                            "tls350sim", "alarmlabels.json")
        with open(path, encoding="utf-8") as fh:
            labels = json.load(fh)["label"]
        missing = [key for key in sorted(labels)
                   if (STATUS_TYPES.get(key[:2]) or {}).get(key[3:]) is None]
        self.assertEqual(missing, [])

    def test_the_density_probe_alarms_are_named_rather_than_numbered(self):
        """576013-635 Rev AA's category 02 legend ends `32=Fuel Quality
        Alarm 31=Density Warning 30=Delivery Density Warning`, and this
        console stopped at 30 -- so two of the three alarms a Mag-D probe
        exists to drive printed as `ALARM 0231` and `ALARM 0232`. Table 5-1
        gives the display form of 32; 31 is on no table here, so it is the
        legend's own words, which is what 30's row was made from too."""
        from tls350sim.console import STATUS_TYPES
        c = Console()
        self.assertEqual(c.alarm_name("02", "30"), "DELIVY DENSITY WARN")
        self.assertEqual(c.alarm_name("02", "31"), "DENSITY WARNING")
        self.assertEqual(c.alarm_name("02", "32"), "FUEL QUALITY ALARM")
        # 21/19 is PLLD only: Table 5-1 gives no 26/19 for WPLLD, and the
        # serial manual's category 21 legend stops at 18.
        self.assertEqual(c.alarm_name("21", "19"), "GRS TST NEEDED ALM")
        self.assertNotIn("19", STATUS_TYPES["26"])

    def test_the_self_clearing_alarms_are_named_by_number(self):
        """The rule used to read words out of the alarm's NAME -- "Idle
        Time" in the description -- and the names are the manual's now, so
        `No CSLD Idle Time Warning` became `NO CSLD IDLE TIME` and the test
        quietly stopped firing. A rule about which alarm it is belongs in
        the number, which no manual revision rewords."""
        from tls350sim.console import SELF_CLEARING, STATUS_TYPES
        for code in SELF_CLEARING:
            self.assertIn(code[2:], STATUS_TYPES.get(code[:2], {}), code)
        c = Console()
        self.assertFalse(c.latches("022101"))     # No CSLD Idle Time
        self.assertFalse(c.latches("062801"))     # Fuel Out
        self.assertTrue(c.latches("020501"))      # everything else

    def test_the_two_rows_whose_action_column_names_the_console(self):
        """576013-610 Rev AC Table 29-3 p.29-5. Every other row of that
        table tells the reader to do something; these two tell the reader
        what the console will do:

            TANK SIPHON BREAK | Warning | ... | Clears when tank test
              completes.
            TANK TEST ACTIVE  | Warning | ... | Do not dispense fuel from
              this tank until message disappears.

        Both latched, so twenty minutes after the test finished the
        messages were still on the glass waiting for a keypress the
        operator has not been told to make -- and a technician taught that
        TANK SIPHON BREAK clears itself reads a stale one as a stuck valve.
        `siphon_break_conditions`' own docstring quotes the first sentence.
        A4 and A5."""
        c = Console()
        self.assertFalse(c.latches("022201"))     # Tank Siphon Break
        self.assertFalse(c.latches("022001"))     # Tank Test Active

    # 530, the system beeper. p.204 prints the single line `BEEPER: ENABLED`
    # and `tests/console_capture/raw/I53000.bin` -- a running TLS-350 --
    # answers `SYSTEM BEEPER`, a blank line, and `ENABLED` under it. So this
    # one IS a title over its own value, and the console says so.
    TITLE_OVER_ITS_VALUE = ["530"]

    def test_no_display_response_answers_a_title_over_its_own_value(self):
        """FIDELITY N6a. `wiretitles.json` reads a report's title off the
        manual's own Display sample, and a response whose whole body is ONE
        line gives the extractor nothing to tell a title from a value.

        Seventy-four entries are a title and nothing else. Twenty-four of
        those bodies carry their label as the field's `wire_line`, which is
        what makes them answer one line with the site's value in it. Four
        did not, and answered the SAMPLE's line and then the console's value
        under it:

            I52900  ALL PHONES                 (the sample's answer)
                    SINGLE PHONE               (the site's)
            I55400  0.20 GPH LINE TEST AUTO-CONFIRM: ENABLED
                    DISABLED

        The second is the worse shape -- a report contradicting itself on
        two consecutive lines -- and 502's carried the sample's SHIFT
        NUMBER, so `I50203` answered "SHIFT TIME 1".

        `TITLE_OVER_ITS_VALUE` is the shape a real console genuinely draws,
        and it is a list of ONE. This measure reads the manual's samples, and
        a sample that is a single line cannot say whether it is a title or an
        answer; where a capture settles it the capture wins.
        """
        import json
        import os
        from tls350sim.console import MODULES, SOFTWARE_MODULES
        from tls350sim.wire import Handler

        here = dirname(dirname(abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "wiretitles.json"),
                  encoding="utf-8") as fh:
            titles = json.load(fh)
        bare = {tok: v["title"] for tok, v in titles.items()
                if v.get("title") and not any(
                    v.get(x) for x in ("columns", "heading", "sample",
                                       "lines", "leads"))}
        self.assertGreater(len(bare), 50, "the measure found nothing to check")

        c = Console()
        c.board = "E6"
        for key, _n, _p, _b, _w, _m in MODULES:
            c.modules[key] = 1
        c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
        handler = Handler(c, verbose=False)

        doubled = []
        for tok, title in sorted(bare.items()):
            dev = ("00" if f"S{tok}00" in FIELDS
                   else "01" if f"S{tok}01" in FIELDS else None)
            if dev is None:
                continue
            out = handler.handle(
                (chr(1) + f"I{tok}{dev}" + chr(13)).encode()).decode("latin-1")
            rows = [l.strip(chr(1) + chr(3)) for l in out.splitlines()]
            rows = [r for r in rows if r.strip()][2:]
            if len(rows) >= 2 and rows[0].strip() == title.strip():
                doubled.append(tok)
        self.assertEqual(doubled, self.TITLE_OVER_ITS_VALUE,
                         "a Display response answers its own sample's line "
                         "and then the console's value under it")

    def test_a_max_fits_the_wires_own_decimal_field(self):
        """576013-635 states a decimal width for every Set, in the Command
        Format block: `S604TTGGGGGG`, `S607TTIII.hh`, `S753PPLLL`. That is a
        ceiling on the value, independent of any range a chapter states and
        of the panel mask -- and where a panel mask exists the two agree.

        It catches what the mask invariant above cannot: six of the fifteen
        fields it found had no mask at all, so nothing had ever looked at
        them. Built by tools/build_field_widths.py, because the manuals are
        not in this repository. See FIDELITY F8.
        """
        import json
        import os
        path = os.path.join(dirname(dirname(abspath(__file__))),
                            "tls350sim", "fieldwidths.json")
        with open(path, encoding="utf-8") as fh:
            widths = json.load(fh)["width"]
        over = []
        for fid, field in sorted(FIELDS.items()):
            if not field or field.get("max") is None:
                continue
            row = widths.get(fid[1:4])
            if row and field["max"] > row[1] + 1e-9:
                over.append(f"{fid}: max {field['max']} in a {row[0]} field")
        self.assertEqual(over, [],
                         "a field accepts more than the wire can carry")

    def test_no_walkable_step_writes_a_code_its_software_lacks(self):
        """FIDELITY F13, and S10 from the panel's side.

        `available_functions` hides a whole CHAPTER that arrived with a later
        version -- "a chapter that arrived with a later version is not on
        FUNCTION at all before it". The steps INSIDE a chapter that has
        always existed had no such gate, so a Version 15 console with one of
        every card walked a Vapor Loss Factor from v29, a Probe Offset from
        v22, a System Beeper from v26 and seven more; 576013-635 heads every
        one of those codes with the version it arrived in.

        This is the measure rather than a count in prose, because F13's own
        count depended on which cage and which board it was taken with:
        asked through `available_functions` on an E6 with one of everything,
        a Version 15 console walked ten such steps and the Pump Relay
        Monitor's five had already gone with the card.
        """
        from tls350sim import versions
        from tls350sim.console import MODULES, SOFTWARE_MODULES

        def a_full_cage(version):
            c = Console()
            c.board = "E6"
            for key, _n, _p, _b, _w, _m in MODULES:
                c.modules[key] = 1
            c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
            c.set_version(version)
            return c

        for version in (1, 10, 15, 20, 25, 27, 30, 33):
            c = a_full_cage(version)
            walked = 0
            for fn in c.available_functions():
                for st in c.visible_steps(fn, 1):
                    walked += 1
                    code = (st.get("code") or "").upper()
                    if not code.startswith("S") or len(code) < 4:
                        continue
                    self.assertTrue(
                        versions.knows_token(code[1:4], c.version, c.board),
                        f"version {version} walks {code} "
                        f"({fn['function']}: {st.get('text')})")
            # and the walk is not empty, or the assertion above proves
            # nothing: a console with no functions passes it trivially
            self.assertGreater(walked, 40, version)

    def test_a_step_that_names_a_condition_implements_it(self):
        """A parenthetical such as "(Not Active For Tanks With High Alcohol
        Probes)" is the manual telling you the screen is conditional. If the
        step says so and carries no `when`, the console shows it anyway."""
        says = ("not active", "only appears", "only when", "does not appear",
                "must be enabled", "only if", "if installed", "not appear")
        bad = []
        for fn in SETUP_MENU:
            for st in fn.get("steps", []):
                text = st.get("text", "").lower()
                if any(w in text for w in says) and not st.get("when"):
                    bad.append(f"{fn['function']}: {st.get('text', '')[:44]}")
        self.assertEqual(bad, [], "a stated condition with no `when`")

    def test_a_setting_reads_back_as_the_number_that_was_written(self):
        """A fourth invariant, and it needs no manual either: program a
        numeric setting over the wire and `limit()` has to give it back.

        It was written after FIDELITY R17, where a line's reference pressure
        was read from `7B7` -- a code that does not exist -- and the entry
        turned out to be the smaller half. `limit()` took the device prefix
        off at `len(raw) > 8`, which is a property of the PACKED form rather
        than of the code: a decimal value wearing its prefix is short, so
        `S77401` holding a 30 hour timeout read as 130, and a `ppp.pp` value
        is exactly eight with the prefix on, so 20.00 PSI read as 1020.

        Every value here goes in through `Handler`, because that is the only
        way a real console is programmed. A test that writes `c.values`
        itself proves the reader and says nothing about whether anything can
        fill it -- which is how the `7B7` branch stayed green while being
        unreachable on every console ever built."""
        import os
        import re
        from tests.test_coverage import a_full_console
        root = dirname(dirname(abspath(__file__)))
        tokens = set()
        for name in sorted(os.listdir(os.path.join(root, "tls350sim"))):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, "tls350sim", name),
                      encoding="utf-8") as fh:
                src = fh.read()
            tokens |= set(re.findall(r'limit(?:_volume)?\(\s*"([0-9A-F]{3})"',
                                     src))
        self.assertGreater(len(tokens), 25, "the scan found nothing to check")
        c = a_full_console()
        h = Handler(c, verbose=False)
        want, unstorable = {}, []
        for tok in sorted(tokens):
            field = FIELDS.get("S" + tok + "01") or FIELDS.get("S" + tok + "00")
            if not field or field.get("kind") not in ("float", "int"):
                unstorable.append(tok)
                continue
            lo, hi = field.get("min", 0.0), field.get("max", 100.0)
            value = round(lo + (hi - lo) * 0.3, 2)
            if field.get("kind") == "int":
                value = int(value)
            dev = "01" if field["code"].endswith("01") else "00"
            # the display form is the one that carries the value in words,
            # and it is what a technician's terminal sends
            text = str(value)
            if field.get("kind") == "float":
                # `rr.rr`, `ppp.pp`: the manual's decimal forms are fixed
                # width, and a short one is refused
                row = FIELD_WIDTHS.get(tok)
                if row and "." in row[0]:
                    whole, frac = row[0].split(".", 1)
                    text = f"{value:0{len(whole) + len(frac) + 1}.{len(frac)}f}"
            reply = h.handle(SOH + ("S" + tok + dev + text + "\r").encode())
            self.assertNotIn(b"9999", reply, f"{tok} refused {text!r}")
            want[(tok, int(dev))] = value
        wrong = []
        for (tok, dev), value in sorted(want.items()):
            got = c.limit(tok, dev)
            # stored as a 32 bit float, so the tolerance is the format's
            if got is None or abs(float(got) - value) > max(1e-4, value * 1e-6):
                wrong.append(f"S{tok}{dev:02d}: wrote {value}, read {got}")
        self.assertEqual(wrong, [])
        self.assertEqual(unstorable, ["639"],
                         "a code read through limit() with no field to fill "
                         "it: nothing can ever program it")


if __name__ == "__main__":
    unittest.main()
