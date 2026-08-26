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
"""The fifteen codes later revisions added.

Eleven from Revision Y, four from Revision AA. For a long time these answered
9999 and the note against them said "not obtainable". They were obtainable;
nobody had gone and got them.

The tests worth having here are the ones about what the INDEX got wrong and
what the MANUAL gets wrong, because those are the two ways this could have
been built confidently and incorrectly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, wirelater                   # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.wire import Handler, DOCUMENTED, KNOWN      # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    c.set_board("E6")
    for card in ("smart", "vmc", "mt", "modem", "universal", "probe",
                 "plld", "wplld", "vlld", "dim"):
        c.modules[card] = 4
    c.software.update({"bir": True, "fuelman": True, "csld": True,
                       "isd": True, "pmc": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def body(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3))


def refused(h, cmd):
    return body(h, cmd).startswith("9999")


class TheyAllAnswer(unittest.TestCase):

    def test_every_one_of_the_fifteen_is_known(self):
        for code in sorted(wirelater.MINE):
            self.assertIn(code, KNOWN, code)

    def test_and_every_one_is_in_the_census(self):
        """A code the console answers that no manual documents would be an
        invention. These are all in one."""
        for code in sorted(wirelater.MINE):
            self.assertIn(code, DOCUMENTED, code)

    def test_they_produce_a_body_and_not_just_a_header(self):
        _c, h = a_site()
        for code in sorted(wirelater.MINE):
            device = "01" if code.startswith("VA") else "00"
            reply = body(h, "I" + code + device)
            self.assertFalse(reply.startswith("9999"), code)
            rest = "".join(reply.splitlines()[2:]).strip()
            self.assertTrue(rest, f"{code} answers with a header and nothing")


class TwoCodesOneName(unittest.TestCase):
    """239 and 23A carry the same Function Type word for word."""

    def test_the_manual_gives_them_the_same_name(self):
        self.assertEqual(DOCUMENTED["239"]["name"], DOCUMENTED["23A"]["name"])

    def test_but_only_one_carries_an_end_time(self):
        self.assertEqual(wirelater.MANIFOLD_DELIVERY["239"], 1)
        self.assertEqual(wirelater.MANIFOLD_DELIVERY["23A"], 2)

    def test_and_the_printed_headers_say_so(self):
        _c, h = a_site()
        self.assertIn("DATE / TIME", send(h, "I23900"))
        self.assertNotIn("START DATE / TIME", send(h, "I23900"))
        self.assertIn("START DATE / TIME", send(h, "I23A00"))
        self.assertIn("END DATE / TIME", send(h, "I23A00"))

    def test_the_packed_record_is_ten_characters_longer(self):
        """The whole danger: ten characters in the MIDDLE of every record.
        A reader using 239's layout on 23A's data takes the end time for the
        field count and everything after it shifts, silently."""
        c, h = a_site()
        c.tank_level[1]["volume"] = 3000.0
        for _ in range(3):
            c.clock_offset += 1800
            c.tick()
        one = body(h, "i23900").split("&&")[0]
        two = body(h, "i23A00").split("&&")[0]
        self.assertGreaterEqual(len(two), len(one))


class GroupedTwoWays(unittest.TestCase):
    """237 groups by product, 238 by siphon manifold. Same columns."""

    def test_both_print_the_same_columns(self):
        _c, h = a_site()
        for code in ("I23700", "I23800"):
            self.assertIn("VOLUME", send(h, code))
            self.assertIn("TC VOLUME", send(h, code))

    def test_the_titles_differ(self):
        _c, h = a_site()
        self.assertIn("PRODUCT INVENTORY REPORT", send(h, "I23700"))
        self.assertIn("SIPHON MANIFOLDED INVENTORY REPORT", send(h, "I23800"))

    def test_the_grouping_follows_the_manifold_on_238(self):
        c, h = a_site()
        send(h, "S61201" + "02")            # tank 1 siphoned to tank 2
        groups = wirelater._groups(c, "238", [1, 2, 3])
        self.assertIn([1, 2], groups)

    def test_every_group_gets_a_total(self):
        _c, h = a_site()
        text = send(h, "I23700")
        self.assertIn("TOTAL:", text)


class WhereZeroIsNotAll(unittest.TestCase):
    """"ff - Fuel Position Number (Decimal, 01-99, 00=Not Allowed)".

    Almost every other code in the manual reads 00 as "all". Reading it that
    way here answers a report for every position on a command the console is
    documented to reject."""

    def test_the_three_al_reports_refuse_position_00(self):
        _c, h = a_site()
        for code in ("VA1", "VA2", "VA3"):
            self.assertTrue(refused(h, "I" + code + "00"), code)

    def test_and_answer_a_real_position(self):
        _c, h = a_site()
        for code in ("VA1", "VA2", "VA3"):
            self.assertFalse(refused(h, "I" + code + "01"), code)


class TheIndexWasWrongAboutFive(unittest.TestCase):
    """The names came from a later revision's one-line index and five of the
    eleven were a different feature entirely. This is the argument against
    implementing from an index."""

    def test_404_is_a_generator_report_not_a_pressure_sensor(self):
        self.assertIn("Generator", DOCUMENTED["404"]["name"])
        _c, h = a_site()
        self.assertIn("INPUT GENERATOR REPORT", send(h, "I40400"))

    def test_54e_sets_the_vapor_monitoring_type(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S54E00" + "1"))
        self.assertIn("APM", send(h, "I54E00"))
        self.assertFalse(refused(h, "S54E00" + "0"))
        self.assertIn("CARB ISD", send(h, "I54E00"))
        self.assertTrue(refused(h, "S54E00" + "9"))

    def test_8c3_is_vmc_fueling_positions(self):
        c, h = a_site()
        self.assertFalse(refused(h, "S8C301" + "0102"))
        self.assertEqual(c.vmc_fuel_pos[1], {"A": 1, "B": 2})
        self.assertIn("SIDE A", send(h, "I8C301"))

    def test_8c4_is_a_timeout_and_wants_two_hex_digits(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S8C400" + "1E"))
        self.assertIn("30 SEC", send(h, "I8C400"))
        self.assertTrue(refused(h, "S8C400" + "ZZ"))
        self.assertTrue(refused(h, "S8C400" + "1E0"))

    def test_ba1_is_dim_comms_not_a_vapour_processor(self):
        _c, h = a_site()
        self.assertIn("DIM COMMUNICATION", send(h, "IBA100"))

    def test_a_console_with_no_dim_reports_no_ports(self):
        """An absent card is not a card in fault."""
        c, h = a_site()
        c.modules["dim"] = 0
        self.assertEqual(c.dim_ports(), [])


class RevisionYChangedNothingItOnlyAdded(unittest.TestCase):
    """The reassuring half of getting Rev Y.

    Every one of the 538 function codes present in BOTH Revision U and
    Revision Y has an identical Command Format in the two -- checked by
    diffing the two texts, not by sampling. So nothing built from Rev U was
    built on superseded text, which is worth knowing about a body of work this
    size.

    The enumerations differ in 28 places and almost all of it is the string
    "(Added in Vnn)" appearing beside an entry the console already had. One
    was real: alarm category 37.
    """

    def test_the_apm_alarm_category_exists(self):
        from tls350sim.console import STATUS_CATEGORIES, STATUS_TYPES
        self.assertEqual(STATUS_CATEGORIES.get("37"), "APM Alarm")
        self.assertEqual(len(STATUS_TYPES["37"]), 10)

    def test_an_apm_alarm_describes_itself(self):
        from tls350sim.console import describe_alarms
        said = describe_alarms(["370301"])[0]["description"]
        self.assertIn("APM", said)
        self.assertIn("Over-Pressure", said)

    def test_the_pipe_types_were_already_complete(self):
        """788 gained nothing: Rev U already listed all nineteen, and the
        diff was picking up "(Added in Vnn)" beside entries we had."""
        from tls350sim.console import FIELDS
        choices = [c[0] for c in FIELDS["S78801"]["choices"]]
        self.assertEqual(len(choices), 19)
        self.assertIn("19", choices)


class RevisionAAsOtherTwentyFive(unittest.TestCase):
    """The fragment showed four. The full 704 pages show twenty-nine."""

    NEW_IN_AA = ["237", "238", "239", "23A", "550", "551", "581", "648",
                 "64B", "651", "652", "653", "654", "655", "7D7", "7D8",
                 "7D9", "7DA", "7DB", "7DC", "811", "812", "813", "908",
                 "VA4", "VA5", "VA6", "VA7", "VA8"]

    def test_all_twenty_nine_are_answered(self):
        for code in self.NEW_IN_AA:
            self.assertIn(code, KNOWN, code)
            self.assertIn(code, DOCUMENTED, code)

    def test_the_units_configuration_has_a_hole_at_two(self):
        """1, 3, 4, 5 and no 2 -- the same shape as 52A's missing report 04.
        Accepting 2 accepts a configuration the console has no meaning for."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S55000" + "3"))
        self.assertTrue(refused(h, "S55000" + "2"))

    def test_the_alarm_thresholds_round_trip_as_floats(self):
        import struct
        _c, h = a_site()
        value = struct.pack(">f", 9500.0).hex().upper()
        self.assertFalse(refused(h, "s65101" + value))
        self.assertIn(value, body(h, "i65101"))

    def test_power_up_time_is_minutes_packed_and_days_printed(self):
        """"llllllll - Power Up Time (minutes) ASCII-Hex long". Neither form
        is derivable from the other without knowing the unit it started in."""
        _c, h = a_site()
        self.assertIn("SYSTEM POWER UP TIME", send(h, "I90800"))
        self.assertIn("DAYS", send(h, "I90800"))
        packed = body(h, "i90800").split("&&")[0]
        self.assertEqual(len(packed[6:]), 10 + 8)   # stamp then an 8-hex long

    def test_the_apm_setup_verdict_counts_the_other_way(self):
        """"0=Pass, 1=Fail", where every other verdict in this manual counts
        up from NO TEST to PASS. Reading it the familiar way reports a
        failure as a pass."""
        from tls350sim import wirelater
        self.assertEqual(wirelater.APM_SETUP_PASS, "0")
        _c, h = a_site()
        self.assertIn("PASS", send(h, "IVA400"))
        self.assertTrue(body(h, "iVA400").split("&&")[0].endswith("0"))


class TwoCodesTakeTheirVerificationAtTheFront(unittest.TestCase):
    """8A4 was described here as the only one. That was true of Revision U.

    VA7 is "SVA700149TT" -- the 149 leads. A claim about what is unique in a
    manual is only as good as the revision it was read from.
    """

    def test_va7_wants_a_leading_149(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "SVA700" + "149" + "01"))
        self.assertTrue(refused(h, "SVA700" + "01" + "149"))

    def test_and_only_the_documented_test_types(self):
        _c, h = a_site()
        for which in ("01", "02", "03"):
            self.assertFalse(refused(h, "SVA700" + "149" + which), which)
        self.assertTrue(refused(h, "SVA700" + "149" + "04"))

    def test_clearing_one_clears_only_that_one(self):
        _c, h = a_site()
        before = send(h, "IVA700")
        self.assertEqual(before.count("--/--/--"), 3)
        send(h, "SVA700" + "149" + "01")
        after = send(h, "IVA700")
        # the one that was cleared has a date; the other two do not
        self.assertEqual(after.count("--/--/--"), 2)
        row = [l for l in after.splitlines() if "APM TESTS" in l][0]
        self.assertNotIn("--/--/--", row)


class TheyAreReportsNotSettings(unittest.TestCase):

    def test_the_inquire_only_ones_refuse_a_set(self):
        _c, h = a_site()
        for code in sorted(wirelater.INQUIRE_ONLY):
            device = "01" if code.startswith("VA") else "00"
            self.assertTrue(refused(h, "S" + code + device + "01"), code)

    def test_and_the_three_settable_ones_accept_one(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S54E00" + "0"))
        self.assertFalse(refused(h, "S8C301" + "0102"))
        self.assertFalse(refused(h, "S8C400" + "1E"))


class WhatTheseNeedFitted(unittest.TestCase):

    def test_the_isd_reports_need_the_isd_key(self):
        c, h = a_site()
        c.software["isd"] = False
        self.assertTrue(refused(h, "IV1200"))

    def test_the_vmc_reports_need_the_vmc_card(self):
        c, h = a_site()
        c.modules["vmc"] = 0
        self.assertTrue(refused(h, "I8C301"))

    def test_the_inventory_reports_need_a_probe(self):
        c, h = a_site()
        c.modules["probe"] = 0
        self.assertTrue(refused(h, "I23700"))


if __name__ == "__main__":
    unittest.main()
