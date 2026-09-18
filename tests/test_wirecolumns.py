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
"""Every heading this console prints, against the manual's own.

FIDELITY S5 counted "about forty report headings differ in width", and the
reason it could only be counted was that nobody had the manual's columns to
compare against. `tools/build_wire_titles.py` reads them off the page now, so
the comparison is a test rather than an audit.

**The heading is the one line of a report that does not depend on the site**,
so it can be compared straight across: a product label is a site's own and
`SENSOR  LOCATION               STATUS` is Veeder-Root's. Matched on the
words and asserted on the columns, which is the only interesting part.
"""
import os
import re
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_controls import a_site, send                 # noqa: E402
from tls350sim import (leaktest, packed, wiresensors,        # noqa: E402
                       wiretables)


def a_full_site():
    """A console with a card in every bay, so a report has something to draw."""
    console, handler = a_site()
    for card in ("liquid", "vapor", "gw", "2wire", "3wire", "vlld", "pump",
                 "pumpmon", "io", "relay", "smart", "plld", "wplld", "probe"):
        console.modules.setdefault(card, 4)
    return console, handler


def body(handler, command):
    return (send(handler, command).replace(chr(1), "").replace(chr(3), "")
        .strip(chr(13) + chr(10)))


class EveryLineIsTheManualsOwn(unittest.TestCase):
    """A line that carries the manual's words has to carry its spacing.

    Not just the heading. `sample` holds every line of every response block
    the manual draws, so the comparison covers the rows and the summary lines
    too -- a heading in the right columns over a row in the wrong ones is the
    defect this set out to fix.
    """

    def lines_of(self, spec):
        seen = []
        for key in ("title", "heading"):
            if spec.get(key) and key in spec:
                seen.append(spec[key])
        return seen + list(spec.get("sample") or [])

    def test_no_line_drifts_from_the_page(self):
        _c, h = a_full_site()
        drifted, matched = [], 0
        for code, spec in sorted(wiretables.TITLES.items()):
            lines = body(h, f"I{code}00").splitlines()[2:]
            for want in self.lines_of(spec):
                if len(want.split()) < 2:
                    continue
                if any(line.rstrip() == want.rstrip() for line in lines):
                    matched += 1
                    continue
                for line in lines:
                    if line.split() == want.split():
                        drifted.append(f"{code} p.{spec['page']}\n"
                                       f"  manual |{want}|\n"
                                       f"  ours   |{line}|")
        self.assertEqual(drifted, [], "\n".join(drifted))
        # a floor, so that a fixture that stops programming anything cannot
        # turn this test green by printing nothing at all
        self.assertGreater(matched, 450, "sample lines printed")


class TheRowsUnderThemAreToo(unittest.TestCase):
    """Two rows checked character for character against the printed sample,
    because a heading in the right columns over a row in the wrong ones is
    the defect this set out to fix."""

    def test_208_prints_the_manuals_own_row(self):
        """576013-635 Rev AA p.71, with this site's product label in place of
        the sample's. The stamp is the TWENTY-TWO character one."""
        c, h = a_full_site()
        c.values["S60201"] = "01REGULAR UNLEADED"
        when = time.mktime((1995, 11, 21, 8, 34, 0, 0, 0, -1))
        c.leaks.results[("tank", 1)] = {
            "annual": leaktest.Result("tank", 1, "annual", "PASSED",
                                      0.0, 12.0, 9088.0, when)}
        rows = body(h, "I20801").splitlines()
        self.assertIn("TANK 1    REGULAR UNLEADED", rows)
        self.assertIn("TEST TYPE  START TIME              "
                      "RESULT     RATE  HOURS  VOLUME", rows)
        self.assertIn(" ANNUAL    NOV 21, 1995   8:34 AM  "
                      "PASSED     0.00    12    9088", rows)

    def test_621_prints_the_manuals_own_row(self):
        """p.278, and the value right against column 38 -- which is one short
        of GALLONS's own right edge, and is where the page puts it."""
        c, h = a_full_site()
        c.values["S60201"] = "01REGULAR UNLEADED"
        self.assertFalse(body(h, "S621011000").endswith("9999FF1B"))
        rows = body(h, "I62101").splitlines()
        self.assertIn("TANK   PRODUCT LABEL             GALLONS", rows)
        self.assertIn(" 1     REGULAR UNLEADED            1000", rows)


class ATableOfPartFields(unittest.TestCase):
    """A code whose value columns are its own part fields -- 605's four chart
    points, 606's twenty and 631's two flags. FIDELITY S1."""

    def test_605_prints_the_manuals_four_points(self):
        """p.252, character for character with this site's product label."""
        from tls350sim import packed
        c, h = a_full_site()
        c.values["S60201"] = "01REGULAR UNLEADED"
        c.values["S60501"] = "01" + "".join(
            packed.hexfloat(v) for v in (9728.0, 7296.0, 4864.0, 2432.0))
        rows = body(h, "I60501").splitlines()
        self.assertIn("TANK   PRODUCT LABEL                         GALLONS",
                      rows)
        self.assertIn(" 1     REGULAR UNLEADED              9728    7296"
                      "    4864    2432", rows)

    def test_606_wraps_twenty_points_onto_five_rows(self):
        """p.253 draws them four across with the tank and its label on the
        first row only."""
        from tls350sim import packed
        c, h = a_full_site()
        c.values["S60601"] = "01" + "".join(
            packed.hexfloat(486.0 * n) for n in range(20, 0, -1))
        rows = [r for r in body(h, "I60601").splitlines()[2:] if r.strip()]
        self.assertEqual(len(rows), 7)             # title, heading, five rows
        self.assertTrue(rows[2].startswith(" 1  "))
        for row in rows[3:]:
            self.assertEqual(row[:30].strip(), "")
        self.assertTrue(rows[-1].endswith("486"))

    def test_60E_prints_the_manuals_four_float_parameters(self):
        """p.260, character for character. This used to assert the opposite
        -- that the table was REFUSED -- because the console held one of the
        four columns and one value under four headings is worse than none.
        It holds all four now: FIDELITY R13.

        The refusal had a second cause worth keeping. `_part_rows` counted
        a label column into every one of these tables, and 60E has none:
        605, 606 and 631 head their second column PRODUCT LABEL and 60E
        heads its four straight across from the tank, so four values were
        being fitted to three columns and divided by nothing.
        """
        from tls350sim import packed
        c, h = a_full_site()
        c.values["S60E01"] = "01" + "".join(
            packed.hexfloat(v) for v in (-3.160, 0.270, 8.000, 0.750))
        rows = body(h, "I60E01").splitlines()
        self.assertIn("TANK    WATER OFFSET     FUEL OFFSET      "
                      "INVALID FUEL     WATER MINIMUM", rows)
        self.assertIn("  1        -3.160            0.270            "
                      "8.000            0.750", rows)

    def test_a_table_with_a_label_column_still_carries_its_label(self):
        """The other half of the same change: 605 has a PRODUCT LABEL and
        must keep it."""
        from tls350sim import packed
        c, h = a_full_site()
        c.values["S60201"] = "01REGULAR UNLEADED"
        c.values["S60501"] = "01" + "".join(
            packed.hexfloat(v) for v in (9728.0, 7296.0, 4864.0, 2432.0))
        self.assertIn(" 1     REGULAR UNLEADED              9728    7296"
                      "    4864    2432", body(h, "I60501").splitlines())


def a_smart_site():
    """A console with a mag sensor, a vapour valve and a pump relay monitor."""
    console, handler = a_full_site()
    console.set_module("smart", 4)
    console.values["S72301"] = "0103"                    # MAG SENSOR
    console.values["S72201"] = "01MAG SUMP #1".ljust(22)
    console.values["S72302"] = "0208"                    # VAPOR VALVE
    console.values["S72202"] = "02VAPOR VALVE #2".ljust(22)
    console.values["S7C601"] = "012101"                  # monitoring PLLD 1
    console.values["S7C501"] = "01PUMP RELAY UNLEADED"
    return console, handler


class TwoStackedHeadingsWereUpsideDown(unittest.TestCase):
    """FIDELITY L6. `322`, `B72` and `B39` stack their column heads over two
    lines, and all three had the lines swapped.

    Read off 576013-635 Rev AA pp.118, 549 and 537 by their word boxes --
    the plain text extraction interleaves these columns, which is the trap
    UNKNOWNS section D describes:

                                      PUMP   PUMP RELAY    STUCK     RUN
        DEVICE  LABEL                 (OUT)     (IN)       RELAY     TIME
             1  PUMP RELAY UNLEADED    OFF    Q 1: OFF     0 SEC    00:00

    The upper line is the one with PUMP on it. `_diag_header` gets the same
    shape right for the six resistance diagnostics, so these were slips.
    """

    def test_322_stacks_them_the_way_the_page_does(self):
        _c, h = a_smart_site()
        lines = body(h, "I32201").splitlines()
        at = lines.index("PUMP RELAY MONITOR STATUS REPORT")
        self.assertEqual(
            lines[at + 2], " " * 30 + "PUMP   PUMP RELAY")
        self.assertEqual(
            lines[at + 3],
            "DEVICE  LABEL                 (OUT)     (IN)       STATUS")
        self.assertEqual(
            lines[at + 4],
            "     1  PUMP RELAY UNLEADED    OFF    Q 1: OFF     NORMAL")

    def test_b72_carries_two_more_columns_in_the_same_grid(self):
        _c, h = a_smart_site()
        lines = body(h, "IB7201").splitlines()
        at = lines.index("PUMP RELAY MONITOR DIAGNOSTIC")
        self.assertEqual(
            lines[at + 2],
            " " * 30 + "PUMP   PUMP RELAY    STUCK     RUN")
        self.assertEqual(
            lines[at + 3],
            "DEVICE  LABEL                 (OUT)     (IN)       RELAY     TIME")
        # the run time is held RIGHT against 64 where the stuck delay runs
        # left from 51, which is what the sample's own row does
        row = lines[at + 4]
        self.assertTrue(row.startswith("     1  PUMP RELAY UNLEADED    OFF"),
                        repr(row))
        self.assertEqual(row[51:], "0 SEC    00:00")

    def test_b39_puts_duration_alone_on_the_upper_line(self):
        c, h = a_smart_site()
        c.values["S72301"] = "0104"                      # a VAC sensor
        lines = body(h, "IB3901").splitlines()
        at = lines.index("VAC SENSOR EVACUATION DIAGNOSTIC REPORT")
        self.assertEqual(lines[at + 4], " " * 23 + "DURATION")
        self.assertEqual(lines[at + 5],
                         "START DATE/TIME        HH:MM:SS")
        for row in lines[at + 6:]:
            if not row.strip():
                break
            self.assertEqual(len(row), 31, repr(row))
            self.assertRegex(row[23:], r"^ *\d+:\d\d:\d\d$")


class ThingsTwoReadersHaveToAgreeOn(unittest.TestCase):
    """The module's own rule, in its own words: a technician who reads 33.2
    inches on the panel and asks the same console for IB3301 has found a bug
    if the answer differs.
    """

    def test_the_sample_counter_is_one_counter(self):
        """FIDELITY L9. The wire counted the console's own clock and the
        panel printed a hardcoded `CNTR = 1` for liquid and `CNTR = 5` for
        2-wire, taken from the manual's figures -- where the first is a
        placeholder X and the second a captured reading."""
        c, _h = a_smart_site()
        counter = c.sample_counter()
        self.assertEqual(wiresensors._sample_counter(c), float(counter))
        for token in ("sensor_liquid", "sensor_2wire", "sensor_groundtemp"):
            self.assertTrue(c.diag_reading(token, 1).startswith(
                f"CNTR = {counter} "), c.diag_reading(token, 1))

    def test_the_mag_sensors_total_height_is_its_own_two_components(self):
        """FIDELITY L8. `TOTAL HT 15.0` over `FUEL HT 5.0` and `WATER HT
        10.0` -- the manual's sample is the sum. All three were generated
        independently, so a total of 38.8 stood over a fuel of 0.6 and a
        water of 2.1, on a sensor its own `B36` constants make 18 to 36
        inches long."""
        c, _h = a_smart_site()
        for number in (1, 2, 3, 4):
            total, fuel, water = wiresensors._mag_values(c, number)[:3]
            self.assertAlmostEqual(total, fuel + water, places=4)
            length = wiresensors._mag_constants(c, number)[1]
            self.assertLess(total, length,
                            f"sensor {number} holds {total} in {length}")

    def test_the_report_prints_the_sum_it_computes(self):
        _c, h = a_smart_site()
        rows = {}
        for line in body(h, "IB3301").splitlines():
            words = line.split()
            if words[:1] in (["TOTAL"], ["FUEL"], ["WATER"]) and len(words) > 2:
                rows[words[0]] = float(words[2])
        self.assertEqual(sorted(rows), ["FUEL", "TOTAL", "WATER"])
        # exact, and in TENTHS -- which is the unit the rows are printed in,
        # and the only unit the sum is exact in: 1.2 + 2.4 is not 3.6 in
        # binary floating point, and the console prints 3.6 over 1.2 and
        # 2.4 correctly. A places= tolerance would pass on the version that
        # does not add up at all, which is what this is here to catch.
        tenths = {k: round(v * 10) for k, v in rows.items()}
        self.assertEqual(tenths["TOTAL"], tenths["FUEL"] + tenths["WATER"])

    def test_a_vapour_valve_is_not_an_unknown_sensor(self):
        """FIDELITY L4. 723's own note ends "08=vapor valve", and the wire's
        type table stopped at 05 -- so a sensor the panel calls a vapour
        valve came back UNKNOWN/0000 from four reports, on a console that
        answers IB61 VAPOR VALVE DIAGNOSTIC for the same sensor."""
        self.assertEqual(wiresensors.SMART_TYPE["08"], ("000E", "VAPOR VALVE"))
        _c, h = a_smart_site()
        log = body(h, "I33300")
        self.assertIn("VAPOR VALVE", log)
        self.assertNotIn("UNKNOWN", log)


class TheVacSensorDiagnosticBlock(unittest.TestCase):
    """FIDELITY L7. Two manuals draw `B38`'s reading block identically --
    576013-635 Rev AA p.534 and 576013-818 Rev AB Figure 6-29 -- and this
    console drew it inside out.

    Read by word box, with the y positions, because the leading is half the
    answer. p.534's lines sit 8.6 apart:

        320.7  VCV: CLOSED
        337.9  4-12-04 11:28AM          <- 17.2, a blank line
        346.6  LEAK RATE:     0.123 GPH
        355.2  TIME TO NO VAC:
        363.9            150:20 HHHH:MM
        372.5  4-12-04 10:15AM          <- 8.6, no blank
        381.1  EVAC RATIO:5.2 @ -4.3PSI
        398.4  SENSOR FAULTS:           <- 17.2, a blank line
        407.0    RELIEF VALVE FAULT

    The date is a line of its own standing IN FRONT of the reading it
    stamps, and the label shares its line with the value. This console had
    it the other way round.
    """

    def a_vac_sensor(self):
        """...with a manual test on record, which is what puts the three
        stamped readings on the paper at all. B38 carries a validity flag
        for each of them and this block is the valid shape. FIDELITY L18."""
        console, handler = a_smart_site()
        console.values["S72301"] = "0104"                # VAC SENSOR
        console.vac_leak[1] = 1.5                        # so the three exist
        console.start_vac_test(1)
        console.finish_vac_tests()
        return console, handler

    def block(self, handler):
        lines = body(handler, "IB3801").splitlines()
        at = lines.index("VCV: CLOSED")
        return lines[at + 1:]

    def test_the_date_leads_the_reading_it_stamps(self):
        _c, h = self.a_vac_sensor()
        rows = self.block(h)
        self.assertEqual(rows[0], "")
        self.assertRegex(rows[1], r"^\d\d-\d\d-\d\d +\d?\d:\d\d[AP]M$")
        self.assertRegex(rows[2], r"^LEAK RATE: +\d+\.\d\d\d GPH$")
        self.assertEqual(len(rows[2]), 24)

    def test_time_to_no_vac_carries_no_date(self):
        """The manual leaves it bare, and this console stamped it."""
        _c, h = self.a_vac_sensor()
        rows = self.block(h)
        self.assertEqual(rows[3], "TIME TO NO VAC:")
        self.assertRegex(rows[4], r"^ +\d+:\d\d HHHH:MM$")
        self.assertEqual(len(rows[4]), 24)

    def test_the_second_date_belongs_to_the_evac_ratio(self):
        """A different measurement made at a different moment -- and with
        no blank line in front of it, where the first date has one."""
        _c, h = self.a_vac_sensor()
        rows = self.block(h)
        self.assertRegex(rows[5], r"^\d\d-\d\d-\d\d +\d?\d:\d\d[AP]M$")
        self.assertTrue(rows[6].startswith("EVAC RATIO:"), rows[6])

    def test_a_clean_sensor_still_prints_the_heading(self):
        """Figure 6-29 draws `SENSOR FAULTS:` over ` NONE`. This printed
        neither, so the one screen that answers "is anything wrong with this
        sensor" answered by stopping."""
        _c, h = self.a_vac_sensor()
        rows = self.block(h)
        self.assertEqual(rows[7], "")
        self.assertEqual(rows[8], "SENSOR FAULTS:")
        self.assertEqual(rows[9], " NONE")

    def test_a_faulted_sensor_names_its_fault_indented_two(self):
        c, h = self.a_vac_sensor()
        c.sensor_state[("smart", "1")] = "fault"
        rows = self.block(h)
        self.assertEqual(rows[8], "SENSOR FAULTS:")
        self.assertEqual(rows[9], "  RELIEF VALVE FAULT")

    def test_the_columns_above_it_are_the_pages_own(self):
        """`SERIAL NUMBER        24` holds its number right against 22 and
        `              -9.000 PSI` runs the figure to 19."""
        _c, h = self.a_vac_sensor()
        lines = body(h, "IB3801").splitlines()
        serial = next(l for l in lines if l.startswith("SERIAL NUMBER"))
        self.assertEqual(len(serial), 23)
        for line in lines:
            if line.endswith(" PSI"):
                self.assertEqual(len(line), 24, repr(line))
        self.assertIn(" VACUUM OK", lines)


class TwoReportsThatReadThemselvesTwice(unittest.TestCase):
    """The two smaller defects inside FIDELITY L10."""

    def test_b21s_last_reading_is_not_its_own_average(self):
        """"Last Reading" and "Current Average Value" are two of B21's five
        fields, and both were one expression -- so the two floats of its
        computer response came back bit-identical, where every other
        diagnostic on the card puts one A/D conversion's noise on one."""
        c, h = a_full_site()
        c.values["S71101"] = "011"
        reply = body(h, "iB2100")
        self.assertNotIn("9999", reply)
        # `iB21SSYYMMDDHHmmSSNNFFFFFFFF...`: the stamp, the device, the
        # field count, then counter, high ref, low ref, last reading and
        # current average -- the fourth and fifth of the five.
        rest = reply.split("iB2100")[-1][10:]
        self.assertEqual(rest[:4], "0105")
        floats = rest[4:]
        last, average = floats[24:32], floats[32:40]
        self.assertNotEqual(last, average)
        self.assertAlmostEqual(packed.unhexfloat(last),
                               packed.unhexfloat(average),
                               delta=packed.unhexfloat(average) * 0.02)

    def test_b35s_serial_does_not_shove_the_date_code_along(self):
        """p.530 holds the two numbers right against 52 and 63. The serial
        field was twelve wide, two past its column -- and this console's
        smart serials are eight digits where the sample's is six, so it
        overflowed and carried DATE CODE with it."""
        c, h = a_smart_site()
        rows = [l for l in body(h, "IB3500").splitlines()
                if l[:2].strip().isdigit()]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(len(row), 64, repr(row))
            serial, date_code = row[43:53], row[53:64]
            self.assertEqual(serial, serial.rstrip())    # right justified
            self.assertEqual(date_code, date_code.rstrip())
            self.assertTrue(serial.strip().isdigit(), repr(row))
        del c


class The50PointChartReport(unittest.TestCase):
    """FIDELITY X6. I216 printed `PAIR HEIGHT VOLUME SLOPE` and stopped.

    576013-635 Rev AA p.80's sample is fully derivable, which is what makes
    it worth replaying: the header slope is FULL VOLUME over DIAMETER, the
    volumes step full/50, and each height is that volume on the tank's own
    chart. It read `chart_points`, which holds the STRAPPED chart alone, so
    every tank nobody had strapped by hand printed a heading over nothing.
    """

    def a_linear_tank(self):
        """The manual's own: 96 inches, 10,000 gallons, strapped linear."""
        console, handler = a_site()
        for code, value in (("607", 96.0), ("63C", 10000.0)):
            console.values[f"S{code}01"] = "01" + struct.pack(
                ">f", value).hex().upper()
        for n in range(1, 50):
            volume = 10000 - 200 * n
            console.add_chart_point(1, 96.0 * volume / 10000.0, volume)
        return console, handler

    def test_it_replays_the_manuals_own_rows(self):
        """PAIR 1 is 94.08 at 9800 and PAIR 49 is 1.92 at 200, with the
        slope 104.17 on every row because the tank is linear."""
        console, _h = self.a_linear_tank()
        rows = console.chart_report_rows(1)
        self.assertEqual(len(rows), 49)
        for pair, height, volume in ((1, 94.08, 9800), (2, 92.16, 9600),
                                     (45, 9.60, 1000), (49, 1.92, 200)):
            got_h, got_v, got_s = rows[pair - 1]
            self.assertAlmostEqual(got_h, height, places=2)
            self.assertAlmostEqual(got_v, volume, places=0)
            self.assertAlmostEqual(got_s, 104.17, places=2)

    def test_the_slope_column_is_local_and_not_the_header_slope(self):
        """A constant would not need a column. On a cylinder the gallons
        per inch is largest at the middle of the tank and smallest at the
        ends, and the header's FULL VOLUME over DIAMETER is neither."""
        console, _h = a_site()
        rows = console.chart_report_rows(1)
        slopes = [s for _h, _v, s in rows]
        self.assertEqual(len(rows), 49)
        middle = slopes[len(slopes) // 2]
        self.assertGreater(middle, slopes[0])
        self.assertGreater(middle, slopes[-1])

    def test_the_report_prints_a_row_per_pair(self):
        console, handler = self.a_linear_tank()
        lines = body(handler, "I21601").splitlines()
        rows = [l for l in lines if l[:4].strip().isdigit()]
        self.assertEqual(len(rows), 49)
        self.assertEqual(rows[0].split(), ["1", "94.08", "9800", "104.17"])
        self.assertEqual(rows[-1].split(), ["49", "1.92", "200", "104.17"])
        del console

    def test_the_computer_format_carries_the_same_count(self):
        """The notes give the field order: `TTddddddddffffffffssssssssnn`
        then three floats a pair, with "nn - Number of Height/Volume Pairs
        to Follow (Hex)"."""
        console, handler = self.a_linear_tank()
        rest = body(handler, "i21601").split("i21601")[-1][10:]   # past YYMMDDHHmm
        self.assertEqual(rest[:2], "01")                          # tank
        for at, want in ((2, 96.0), (10, 10000.0), (18, 10000.0 / 96.0)):
            self.assertAlmostEqual(packed.unhexfloat(rest[at:at + 8]), want,
                                   places=2)
        self.assertEqual(rest[26:28], "31")                       # 49 pairs
        pairs = rest[28:]
        self.assertGreaterEqual(len(pairs), 49 * 24)
        self.assertAlmostEqual(packed.unhexfloat(pairs[:8]), 94.08, places=2)
        del console


class HighRefIsAColumnAndNotTheLargerNumber(unittest.TestCase):
    """FIDELITY L3. `wiresensors.REFERENCE` is `(high, low)` in the manual's
    COLUMN order, and on the three chlorine codes the high figure is the
    smaller one. All three were stored the other way round, so the console
    printed the two reference columns swapped -- tidied, at some point, by
    somebody meeting a row where HIGH REF held less than LOW REF.

    576013-635 Rev AA's own typical responses, under
    `SENSOR COUNTER HIGH REF LOW REF`:

        IB41    1       5     1815      7823         4193
        IB46    1       5     8900     32000         5200       100000
        IB4B    1       5     8900     32000         5200       100000

    The values wander +/-3% off the module's nominal, so this checks the
    band rather than the figure. What it is really asserting is which of the
    two numbers lands in which column, and on B41 they are a factor of four
    apart -- no wander can confuse them.
    """

    # code, high ref, low ref -- straight off the page, left column first.
    PAGE = {"B01": (1072.0, 193.0), "B06": (1080.0, 208.0),
            "B11": (5440.0, 930.0), "B21": (1086.0, 215.0),
            "B41": (1815.0, 7823.0), "B46": (8900.0, 32000.0),
            "B4B": (8900.0, 32000.0)}

    def test_the_table_is_the_manuals_column_order(self):
        self.assertEqual(wiresensors.REFERENCE, self.PAGE)

    def test_every_report_prints_them_in_that_order(self):
        c, h = a_full_site()
        # a_full_site fits no Universal Sensor Module, and without one
        # B4B correctly answers 9999 rather than a report.
        c.modules.setdefault("universal", 4)
        for code, (high, low) in sorted(self.PAGE.items()):
            if code == "B21":
                continue                  # a ground thermistor, not a sensor
            rows = body(h, f"I{code}01").splitlines()
            row = [r for r in rows if r.split()[:1] == ["1"]][-1]
            got_high, got_low = (float(n) for n in row.split()[2:4])
            for got, want, which in ((got_high, high, "HIGH"),
                                     (got_low, low, "LOW")):
                self.assertLess(abs(got - want) / want, 0.031,
                                f"{code} {which} REF: {got} is not "
                                f"within 3% of the manual's {want}"
                                f" -- {row!r}")


class TheColumnsAreReadOffThePage(unittest.TestCase):
    """The data itself, so that a rebuild that loses the geometry fails here
    rather than in one report at a time."""

    def test_the_geometry_is_there(self):
        titles = wiretables.TITLES
        self.assertGreater(len(titles), 450)
        tables = [t for t in titles.values() if t.get("columns")]
        self.assertGreater(len(tables), 140)
        self.assertGreater(sum(1 for t in titles.values() if t.get("line")),
                           40)

    def test_621s_columns_are_the_ones_the_page_prints(self):
        want = [{"head": "TANK", "start": 1, "end": 1, "align": "right"},
                {"head": "PRODUCT LABEL", "start": 7, "end": 22,
                 "align": "left"},
                {"head": "GALLONS", "start": 35, "end": 38, "align": "right"}]
        self.assertEqual(wiretables.columns("621"), want)

    def test_62f_is_left_aligned_because_two_rows_say_so(self):
        """`4.0 IN` and `4.0 IN PS` share a left edge, which is the only
        statement in the manual of which way that column is held."""
        self.assertEqual(wiretables.columns("62F")[-1]["align"], "left")
        self.assertEqual(wiretables.columns("621")[-1]["align"], "right")


if __name__ == "__main__":
    unittest.main()


class TheGroundTemperatureThermistorBelongsToVLLD(unittest.TestCase):
    """FIDELITY M4 and L10, which are one defect seen from two ends.

    576013-879 Rev W p.60: "When using volumetric line leak detection
    (VLLD), only one ground temperature thermistor is needed per site and
    the thermistor must be wired to thermistor position number 1 (positions
    2 - 4 are not used)."

    `diagdata.json` already read that correctly -- GROUND TEMP DIAGNOSTIC is
    `requires: vlld` -- while `wiresensors` gated the same screen on the
    GROUNDWATER card and enumerated its devices from groundwater config 711,
    on the strength of the word "ground" in two unrelated names. So the two
    halves of the console disagreed about one screen.
    """

    def fitted(self, **cards):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console(None)
        c.set_module("probe", 1)
        for card, count in cards.items():
            c.set_module(card, count)
        return c, Handler(c, verbose=False)

    def test_a_vlld_console_answers(self):
        """The console p.60 is describing: VLLD and a probe module."""
        _c, h = self.fitted(vlld=1)
        self.assertNotIn("9999", body(h, "IB2100"))

    def test_a_groundwater_console_does_not(self):
        """It used to be the only one that did."""
        _c, h = self.fitted(gw=1)
        self.assertIn("9999", body(h, "IB2100"))

    def test_the_panel_and_the_wire_agree_now(self):
        """Which is the whole point of the entry: the panel offered the
        diagnostic and IB2100 answered 9999 on the same console."""
        import json
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "diagdata.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        screen = next(d for d in data
                      if d.get("function") == "GROUND TEMP DIAGNOSTIC")
        self.assertEqual(screen.get("requires"), "vlld")
        _c, h = self.fitted(vlld=1)
        self.assertNotIn("9999", body(h, "IB2100"))

    def test_one_thermistor_on_position_one(self):
        """"only one ... is needed per site ... positions 2 - 4 are not
        used". The device list came off S711 and could run to four."""
        _c, h = self.fitted(vlld=1)
        rows = [l for l in body(h, "IB2100").splitlines()
                if l[:6].strip().isdigit()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:6].strip(), "1")

    def test_position_two_is_not_answered(self):
        _c, h = self.fitted(vlld=1)
        self.assertNotIn("9999", body(h, "IB2101"))
        rows = [l for l in body(h, "IB2102").splitlines()
                if l[:6].strip().isdigit()]
        self.assertEqual(rows, [])

    def test_only_one_row_however_many_cards_are_fitted(self):
        """"only one ground temperature thermistor is needed per site" is a
        SITE's rule, not a card's positions: a second VLLD card does not buy
        a second thermistor. The panel's own half of this is in
        `test_panel.py`, which walked four. FIDELITY M4."""
        _c, h = self.fitted(vlld=2)
        rows = [l for l in body(h, "IB2100").splitlines()
                if l[:6].strip().isdigit()]
        self.assertEqual(len(rows), 1)


# `9999FF` and not `9999`: the four digits alone appear inside real
# readings -- B34's channel grid prints them as a raw sample -- so a
# bare substring test called a working report a refusal.
REFUSED = "9999FF"


def _programmed(families, smart=()):
    """A console carrying those sensor families, one position programmed on
    each, and the smart sensors named as their own types."""
    from tls350sim.console import Console
    from tls350sim.wire import Handler
    console = Console(None)
    for module, code in families:
        console.set_module(module, 1)
        console.values["S" + code + "01"] = "011"
    for number, kind in smart:
        console.values["S721%02d" % number] = "%02d1" % number
        console.values["S723%02d" % number] = "%02d%s" % (number, kind)
    return console, Handler(console, verbose=False)


# The wired sensor families, and the smart sensors, on two consoles.
#
# **Two, because one real console cannot carry them all.** The I.S. bay holds
# a fixed number of cards, and a console with a probe, liquid, vapor,
# groundwater, 2-wire, 3-wire and universal card in it has no slot left for a
# smart sensor module -- `set_module("smart", 1)` returns False. Forcing it
# would mean writing `modules[...] = 1` past the bay's own limit, which is
# what `a_full_site()` does and is why four codes there answer 9999 for want
# of a slot rather than for want of programming.
WIRED = (("probe", "601"), ("liquid", "701"), ("vapor", "706"),
         ("gw", "711"), ("2wire", "741"), ("3wire", "746"),
         ("universal", "74B"), ("io", "801"), ("relay", "806"),
         ("vlld", "751"), ("plld", "781"), ("wplld", "7A1"),
         ("pumpmon", "7C4"))

# B33 is MAG, B37 is ATM P and B38 is VAC, and each walks only its own
# category -- so three positions, of three types, or three of the reports
# have nothing to draw.
SMART = (("probe", "601"), ("smart", "721"))
SMART_KINDS = ((1, "03"), (2, "04"), (3, "05"))


def a_configured_site():
    """A console with a POSITION PROGRAMMED in every wired sensor family.

    FIDELITY L1: the only test that reached these codes was the census
    sweep, whose console has every card fitted and zero positions
    programmed -- so `_devices()` returned an empty list for every family,
    every per-device loop body was skipped, and 37 of the module's 55
    functions were never called by anything. The census proves a code
    ANSWERS; it never looks at what it answers WITH.
    """
    return _programmed(WIRED)


def a_smart_configured_site():
    """The smart sensor half, on a console with room for the card."""
    return _programmed(SMART, SMART_KINDS)


class EverySensorReportDrawsABody(unittest.TestCase):
    """FIDELITY L1, closed as a test rather than as a fix.

    Nothing in `wiresensors.py` turned out to be broken -- every one of its
    codes answers in both formats once a console has the sensor on it. What
    was missing was any test that asked. This walks all of
    `wiresensors.CODES` and fails if one answers 9999 on both consoles,
    draws a title with nothing under it, or sends an empty computer payload.
    """

    def setUp(self):
        self.sites = [a_configured_site(), a_smart_configured_site()]

    def answers(self, code):
        """The first of the two consoles that has something to say."""
        best = ""
        for _console, handler in self.sites:
            text = body(handler, code)
            if REFUSED not in text:
                return text
            best = text
        return best

    def rows(self, code):
        return [r for r in self.answers(code).split(chr(13) + chr(10))[2:]
                if r.strip()]

    def test_the_sweep_covers_every_code_the_module_claims(self):
        self.assertGreater(len(wiresensors.CODES), 30)

    def test_no_report_refuses_a_console_that_has_the_card(self):
        refused = [tok for tok in sorted(wiresensors.CODES)
                   if REFUSED in self.answers("I" + tok + "00")]
        self.assertEqual(refused, [])

    def test_every_display_report_has_a_body_under_its_title(self):
        thin = [tok for tok in sorted(wiresensors.CODES)
                if len(self.rows("I" + tok + "00")) < 2]
        self.assertEqual(thin, [], "these draw a heading and nothing under "
                                   "it on a console that has the sensors")

    def test_every_computer_form_sends_a_payload(self):
        """The census never exercised the computer format at all."""
        empty = []
        for tok in sorted(wiresensors.CODES):
            code = "i" + tok + "00"
            payload = self.answers(code).split("&&")[0][len(code) + 10:]
            if not payload.strip():
                empty.append(tok)
        self.assertEqual(empty, [])

    def test_an_unprogrammed_console_is_why_this_was_never_caught(self):
        """Every card, no positions -- the census console. Its reports come
        back as a title and nothing else, and a sweep that only asks "did it
        answer" cannot tell that from a report that worked."""
        _bare, handler = a_full_site()
        thin = 0
        for tok in sorted(wiresensors.CODES):
            text = body(handler, "I" + tok + "00")
            got = [r for r in text.split(chr(13) + chr(10))[2:] if r.strip()]
            if len(got) < 2:
                thin += 1
        self.assertGreater(thin, 5, "if this console draws bodies now, the "
                                    "fixture has been programmed and L1's "
                                    "point no longer holds")


class TheVlldDiagnosticHistoryRows(unittest.TestCase):
    """FIDELITY H17. B51 and B52 draw a row per test under a heading this
    file already measures, and the row was measured by nobody.

    The ratchet below cannot see it: it matches a manual line to one of ours
    by its WORDS with digits masked, and a sample row carrying the sample
    site's date, temperatures and timings masks to a different shape from
    any row this console draws. So the heading was the page's and the row
    under it was not -- five of its eight fields ended one or two columns
    away from the words naming them. Asserted here against the heading,
    which the page has already been shown to agree with.
    """

    def a_line_with_history(self):
        console, handler = a_full_site()
        console.values["S75101"] = "0101"
        for rate in ("gross", "periodic", "annual"):
            console.leaks.start("vlld", 1, rate)
            console.clock_offset += leaktest.LINE_SECONDS[rate] + 5.0
            console.leaks.tick()
        return console, handler

    def field_ends(self, line):
        return [m.end() for m in re.finditer(r"\S+", line)]

    def test_every_row_sits_under_its_own_heading(self):
        _c, h = self.a_line_with_history()
        rows = 0
        for tok in ("B51", "B52"):
            lines = [l for l in body(h, f"I{tok}01").splitlines() if l.strip()]
            head = [l for l in lines if "DATE/TIME" in l][0]
            want = self.field_ends(head)
            for line in lines[lines.index(head) + 1:]:
                if "AVAILABLE" in line:
                    continue
                got = self.field_ends(line)
                # the stamp is five words where DATE/TIME is one, and RSLT
                # is a word where the row's result is a longer one
                self.assertEqual(got[5:-1], want[1:-1],
                                 f"{tok}\n  head |{head}|\n  row  |{line}|")
                self.assertEqual(line.index(line.split()[-1]), want[-1] - 4)
                rows += 1
        self.assertEqual(rows, 3)

    def test_the_lengths_are_the_test_types_own(self):
        """`LGTH` is a property of the test type, not a band a report draws
        from: 576013-849 Rev B Table 5 fixes it at 13.5, 326.0 and 794.0.
        This drew `readings.fixed(10.0, 794.0)`, the envelope of all
        fourteen rows redrawn per test, so a 0.2 gph test could print
        `LGTH 412.7` where two documents print `326.0`."""
        from tls350sim import wirelines
        _c, h = self.a_line_with_history()
        seen = {}
        for tok in ("B51", "B52"):
            for line in body(h, f"I{tok}01").splitlines():
                cells = line.split()
                if len(cells) == 13 and cells[-1] in ("PASSED", "FAILED"):
                    seen[int(cells[5])] = float(cells[9])
        self.assertEqual(seen, {3: 13.5, 7: 326.0, 11: 794.0})
        self.assertEqual(set(seen), set(wirelines.VLLD_LINE_TEST.values()))

    def test_the_test_column_may_exceed_the_length_and_still_pass(self):
        """p.543's `TYP 11` row is `LGTH 794.0 / TEST 794.1 / PASSED`, and
        `min(actual, allowed)` on a pass made that row unreachable."""
        from tls350sim import wirelines
        c, _h = self.a_line_with_history()
        overs = []
        for number in range(1, 5):
            for result in c.leaks.history.get(("vlld", 1)) or []:
                timings = wirelines._vlld_timings(c, number, result)
                overs.append(timings[5] >= timings[3])
        self.assertTrue(all(overs), overs)


def _manual_page(number, _cache={}):
    """One page of the serial manual in its own character grid, or None.

    The PDFs are gitignored -- they are Veeder-Root's -- so a clone has
    neither them nor PyMuPDF, and this measurement skips rather than fails.
    Cached because thirty-seven reports share sixteen pages.
    """
    if number in _cache:
        return _cache[number]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdf = os.path.join(here, "reference",
                       "576013-635_RevAA_SerialInterfaceManual.pdf")
    if not os.path.exists(pdf):
        _cache[number] = None
        return None
    sys.path.insert(0, os.path.join(here, "tools"))
    try:
        import pagelines
        grid = pagelines.character_grid(pdf, number)
    except Exception:                                       # noqa: BLE001
        _cache[number] = None
        return None
    _cache[number] = {_column_key(line): line
                      for _y, line in grid if line.strip()}
    return _cache[number]


def _column_key(line):
    """What makes two lines the same line: their words, digits masked."""
    return re.sub(r"\d", "#", " ".join(line.split())).upper()


def _anchors(line):
    """[(column, word)] -- a word with a DIGIT anchored where it ENDS and a
    word of letters where it STARTS.

    A numeric field on these reports is right-aligned and a label is
    left-aligned, so anchoring a number by its start would compare the width
    of the VALUE and not the width of the field: p.523's `5` and p.527's `50`
    sit in the same eight-column field and start a column apart, which is
    the console agreeing with the manual rather than differing from it.
    """
    out = []
    for m in re.finditer(r"\S+", line):
        out.append((m.end(), m.group()) if re.search(r"\d", m.group())
                   else (m.start(), m.group()))
    return out


class EveryReportSitsInTheColumnsItsOwnPageDraws(unittest.TestCase):
    """FIDELITY L11, which asked for an enumeration and got a count.

    S5 recorded "about forty report headings differ in width" and named
    none, so the claim could not be re-run: it could not go stale loudly,
    only quietly, which is what happened. L11 then measured this module,
    found sixteen, fixed four and could not say what the other twelve were,
    because it had not listed them either.

    This measures all thirty-seven, every time, against the manual's own
    pages -- so the number is never written down again. A line of ours is
    paired with the manual's line of the same words, digits masked, and
    every word's column compared. What it cannot see is a line the manual
    draws and this console does not; that is `test_coverage`'s job.

    **The origin validates itself.** Every one of these reports prints
    labels at column 0, and a page whose pitch or origin were read wrongly
    would shift those too and fail here first. So a run in which only VALUE
    columns differ is a run whose measurement is sound.
    """

    def setUp(self):
        if _manual_page(523) is None:
            raise unittest.SkipTest(
                "no reference/576013-635_RevAA PDF, or no PyMuPDF: this "
                "measures against the manual's own pages")

    def test_no_word_of_any_report_sits_in_a_column_the_page_does_not(self):
        import json
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "tls350sim", "wiretitles.json"),
                  encoding="utf-8") as fh:
            titles = json.load(fh)
        from tests.test_wirebodies import a_programmed_site
        _c, handler = a_programmed_site()
        wrong, compared, reports = [], 0, 0
        for tok in sorted(wiresensors.CODES):
            page = (titles.get(tok) or {}).get("page")
            manual = _manual_page(page) if page else None
            if not manual:
                continue
            seen = 0
            for line in body(handler, "I" + tok + "00").splitlines():
                if not line.strip():
                    continue
                want = manual.get(_column_key(line))
                if want is None:
                    continue
                ours, theirs = _anchors(line), _anchors(want)
                if len(ours) != len(theirs):
                    continue
                seen += 1
                for (got, word), (expect, _w) in zip(ours, theirs):
                    if got != expect:
                        wrong.append("%s p.%d  %-14s ours %3d  manual %3d"
                                     % (tok, page, word, got, expect))
            compared += seen
            reports += 1 if seen else 0
        self.assertEqual(wrong, [], "\n".join([""] + wrong))
        # a floor, so the measurement cannot quietly stop measuring
        self.assertGreater(reports, 30, "reports compared")
        self.assertGreater(compared, 90, "lines compared")
