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
    for card in ("smart", "universal", "probe",
                 "plld", "wplld", "vlld", "dim"):
        c.modules[card] = 4
    # The comm bay is FOUR slots, three of them single-port, so a console
    # cannot carry a modem, a VMCI, an EDIM, an RS-232 port and a
    # Maintenance Tracker port as five separate cards. The dual-port MT
    # module 330586-017 is what a real site does about that: it takes slot 4
    # and answers on two positions, a general serial port on 5 and the
    # Maintenance Tracker on 6. See FIDELITY M7.
    c.modules.update({"rs232": 0, "modem": 1, "vmc": 1, "edim": 1, "mt4": 1})
    c.software.update({"bir": True, "fuelman": True, "csld": True,
                       "isd": True, "pmc": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def body(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10))


def refused(h, cmd):
    return body(h, cmd).startswith("9999")


class TheyAllAnswer(unittest.TestCase):

    def test_every_one_of_the_fifteen_is_known(self):
        for code in sorted(wirelater.MINE):
            self.assertIn(code, KNOWN, code)

    # Two codes are documented and are NOT in `functiondata.json`, which is
    # a parse of section 7 of 576013-635 and nothing else. `VAB` and `VAC`
    # are in no revision of that manual at all -- N, U, Y or AA -- and are
    # documented only in 577014-009 Rev B Table 3 p.18, with a worked sample
    # of each in its Figures 15 and 16. Hand-adding them to the parsed census
    # would cost that file its provenance, so they are named here instead,
    # with the document that carries them. See UNKNOWNS C4.
    ELSEWHERE = {"VAB": "577014-009 Rev B Table 3, Figure 15",
                 "VAC": "577014-009 Rev B Table 3, Figure 16"}

    def test_and_every_one_is_in_the_census(self):
        """A code the console answers that no manual documents would be an
        invention. Each is in one -- and not all in the SAME one."""
        for code in sorted(wirelater.MINE):
            if code in self.ELSEWHERE:
                continue
            self.assertIn(code, DOCUMENTED, code)

    def test_the_two_codes_from_another_manual_are_named(self):
        """A ratchet on the exception, so the list cannot grow quietly: a
        code that is neither in the parsed census nor named above fails the
        test before this one."""
        for code, where in self.ELSEWHERE.items():
            self.assertIn(code, wirelater.MINE, code)
            self.assertNotIn(code, DOCUMENTED, code)
            self.assertTrue(where.startswith("577014-009"), where)

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

    def test_8c4_is_a_timeout_in_decimal_hours(self):
        """Rev AA p.486: "hh - Timeout value in hours (Decimal, 00-99,
        99=Alarm Disabled)", under `VMC COMMUNICATIONS TIMEOUT` and
        `TIMEOUT VALUE: 0 HOURS`.

        That page was invisible to `build_wire_titles.py` because it echoes
        `S8C4xx` where every other Display block echoes an `I`, and Rev Y
        prints 8C3's response on this code's page -- so the console read the
        two characters as HEX and printed them as SECONDS under a title of
        its own devising. See FIDELITY S17."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S8C400" + "30"))
        self.assertIn("VMC COMMUNICATIONS TIMEOUT", send(h, "I8C400"))
        self.assertIn("TIMEOUT VALUE: 30 HOURS", send(h, "I8C400"))
        self.assertTrue(refused(h, "S8C400" + "1E"))
        self.assertTrue(refused(h, "S8C400" + "ZZ"))
        self.assertTrue(refused(h, "S8C400" + "100"))

    def test_ba1_is_dim_comms_not_a_vapour_processor(self):
        _c, h = a_site()
        self.assertIn("DIM COMMUNICATION", send(h, "IBA100"))

    def test_a_console_with_no_dim_reports_no_ports(self):
        """An absent card is not a card in fault."""
        c, h = a_site()
        # the preset carries an EDIM as well as the bare key, and a DIM of
        # either kind is a DIM with ports
        for card in ("dim", "edim", "mdim"):
            c.modules[card] = 0
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

    def test_the_units_configuration_has_five_choices_and_no_hole(self):
        """This used to assert a hole at 2 -- "1, 3, 4, 5 and no 2, the same
        shape as 52A's missing report 04" -- and there is no hole. 550's own
        notes read "C - Inventory Alarms Units Configuration ... 5=Custom
        4=All Height 3=All Volume 2=% Full 1=Standard", and 576013-623 Rev
        AN's Table 5-2 heads five columns: Standard, All %Full, All Volume,
        All Height, Custom. FIDELITY F5.
        """
        _c, h = a_site()
        for choice in "12345":
            self.assertFalse(refused(h, "S55000" + choice), choice)
        self.assertTrue(refused(h, "S55000" + "6"))

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

    def test_the_same_fault_posts_the_alarm_the_manual_names(self):
        """37/08 APM SETUP WARN: "A sensor used by APM is missing or not
        configured", 577014-009 Rev B Table 2 p.16 -- the only page that
        gives any of category 37's ten alarms a condition. It is the rule
        VA4 already answered FAIL on, so the two agree by construction.
        See UNKNOWNS A60."""
        from tls350sim.console import describe_alarms
        c, _h = a_site()
        c.software = {"isd": True, "pmc": True}
        c.values["S54E00"] = "1"                 # 54E: 1=APM
        self.assertTrue(c.apm_monitoring())
        self.assertFalse(c.apm_setup_ok())
        raised = [x for x in c.conditions() if x.startswith("37")]
        self.assertEqual(raised, ["370800"])
        self.assertEqual(describe_alarms(raised)[0]["screen"],
                         "APM SETUP FAILURE WARNING")

    def test_the_two_apm_reports_no_function_code_manual_carries(self):
        """577014-009 Rev B Table 3 p.18 gives two serial commands that are
        in no revision of 576013-635, so both answered the refusal marker.
        Figures 15 and 16 give a worked sample of each."""
        _c, h = a_site()
        for cmd in ("IVAB00", "IVAC00"):
            self.assertFalse(refused(h, cmd), cmd)
        self.assertIn("Automatic Pressure Monitoring Daily Summary Report",
                      send(h, "IVAB00"))
        self.assertIn("AUTOMATIC PRESSURE MONITORING FAULT HISTORY REPORT",
                      send(h, "IVAC00"))

    def test_the_fault_history_columns_are_the_figures_own(self):
        """Measured off Figure 16's word boxes at a 6.291pt pitch: the date
        opens the line, the time sits at column 9, the fault at 19 in a
        field as wide as its own rule, and the state at 41."""
        _c, h = a_site()
        rows = [l for l in send(h, "IVAC00").split(chr(13) + chr(10))
                if l.startswith("   DATE") or l.startswith("-" * 10)]
        head, rule = rows[0], rows[1]
        self.assertEqual(head.index("DATE"), 3)
        self.assertEqual(head.index("TIME"), 12)
        self.assertEqual(head.index("FAULT"), 25)
        self.assertEqual(head.index("STATE"), 41)
        self.assertEqual(rule.index("-" * 19), 19)
        self.assertEqual(len(rule), 46)

    def test_the_fault_history_rows_are_the_alarms_actually_posted(self):
        """Not an invented sample: `alarm_log` already records every alarm
        this console posts and clears, so the report is category 37 of it,
        named off Table 2. A console with 37/08 standing prints that row."""
        from tls350sim import wirelater
        c, h = a_site()
        c.software = {"isd": True, "pmc": True}
        c.values["S54E00"] = "1"
        c.tick()
        shown = send(h, "IVAC00")
        self.assertIn("APM SETUP WARN", shown)
        self.assertIn("ALARM", shown)
        # and it is Table 2's short name, not consoledata's long one
        self.assertEqual(wirelater.APM_FAULTS["08"], "APM SETUP WARN")
        self.assertNotIn("APM Setup Failure warning", shown)

    def test_the_daily_summary_prints_a_header_and_no_invented_rows(self):
        """A row is a day's APM assessment in kPa, and this console models
        no such reading -- the same gap that leaves nine of the ten
        category-37 alarms without a producer. So the header stands alone,
        the way every other empty report here does, with no NO DATA line."""
        _c, h = a_site()
        shown = send(h, "IVAB00")
        self.assertIn("ASSESSMENT TIME OF DAY = 11:59 PM", shown)
        self.assertIn("Status Codes: (W)Warn", shown)
        self.assertNotIn("NO DATA", shown)
        self.assertNotIn("PASS", shown)

    def test_neither_has_a_computer_format_on_any_page(self):
        """Table 3 gives the PC-to-console command and the figures call
        themselves the "Serial to PC Format"; no page carries a packed
        layout for either. 7B1 is the other code in that position."""
        _c, h = a_site()
        for cmd in ("iVAB00", "iVAC00"):
            self.assertTrue(refused(h, cmd), cmd)

    def test_a_console_not_monitoring_by_apm_never_posts_it(self):
        """54E's other setting is CARB ISD, and an ISD site has no APM to
        be misconfigured. The alarm follows the monitoring type, not the
        absence of a vapour sensor."""
        c, _h = a_site()
        c.values["S54E00"] = "0"
        self.assertFalse(c.apm_monitoring())
        self.assertEqual([x for x in c.conditions() if x.startswith("37")],
                         [])


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


class TheApmLogsWhatItDoes(unittest.TestCase):
    """VA8's events. The log was returned and written by nothing, while every
    moment its own sample records happens on this console. FIDELITY I8."""

    HEADER = "DATE     TIME     DESCRIPTION                     ACTION/NAME"

    def an_apm_site(self):
        c, h = a_site()
        c.values["S54E00"] = "1"                     # "1=APM"
        return c, h

    def rows(self, h, asked="IVA800"):
        lines = send(h, asked).splitlines()
        # the closing <ETX> is a line of its own and strip() keeps it
        return [line for line in lines[lines.index(self.HEADER) + 1:]
                if line.strip(chr(3) + chr(32))]

    def test_the_report_is_the_pages_title_and_columns(self):
        """p.668, and an empty log is the header and nothing under it."""
        _c, h = self.an_apm_site()
        text = send(h, "IVA800")
        self.assertIn("AUTOMATIC PRESSURE MONITORING MISCELLANEOUS EVENTS "
                      "REPORT", text)
        self.assertIn(self.HEADER, text)
        self.assertNotIn("NO EVENTS", text)
        self.assertEqual([r for r in self.rows(h) if r.strip()], [])

    def test_a_manual_clear_names_the_test_it_cleared(self):
        """`10-04-27 11:37:21 APM SETUP SELF TEST             TEST MANUALLY
        CLEARED`: VA7's 03 is VA8's 01, and the action starts at 50."""
        _c, h = self.an_apm_site()
        send(h, "SVA700" + "149" + "03")
        row = self.rows(h)[0]
        self.assertEqual(row[18:50].rstrip(), "APM SETUP SELF TEST")
        self.assertEqual(row[50:], "TEST MANUALLY CLEARED")

    def test_a_power_cycle_is_a_shutdown_then_a_startup(self):
        c, h = self.an_apm_site()
        c.breaker_off()
        c.breaker_on()
        words = [r[18:50].rstrip() for r in self.rows(h)]
        if "APM STARTUP" in words:
            self.assertLess(words.index("APM STARTUP"),
                            words.index("APM SHUTDOWN"), "newest first")
        self.assertIn("APM SHUTDOWN", words)

    def test_a_clock_change_prints_the_time_it_left(self):
        c, h = self.an_apm_site()
        send(h, "S50100" + "2701021530")
        row = self.rows(h)[0]
        self.assertEqual(row[:17], "27-01-02 15:30:00")
        self.assertEqual(row[18:50].rstrip(), "TIME CHANGE DETECTED AT")
        self.assertRegex(row[50:], r"^[0-9]{2}-[0-9]{2}-[0-9]{2} "
                                   r"[0-9]{2}:[0-9]{2}:[0-9]{2}$")

    def test_a_carb_isd_console_logs_no_apm_events(self):
        c, h = a_site()
        c.values["S54E00"] = "0"
        send(h, "SVA700" + "149" + "01")
        c.breaker_off()
        self.assertEqual(c.apm_events(), [])

    def test_the_computer_form_is_the_pages_record(self):
        """p.669: ssss, then SSSSSSSS aa bb cc dd ee tt per event."""
        _c, h = self.an_apm_site()
        send(h, "SVA700" + "149" + "02")
        packed = body(h, "iVA800").split("&&")[0][6 + 10:]
        self.assertEqual(packed[:4], "0001")
        record = packed[4:]
        self.assertRegex(record[:8], r"^[0-9A-F]{8}$")
        self.assertEqual(record[8:], "03" + "06" + "000000" + "00")

    def test_the_limit_and_the_window(self):
        """"nnnn - Limit number of records", and a start date after every
        event leaves nothing."""
        _c, h = self.an_apm_site()
        for which in ("01", "02", "03"):
            send(h, "SVA700" + "149" + which)
        self.assertEqual(len(self.rows(h)), 3)
        self.assertEqual(len(self.rows(h, "IVA800" + "0" * 16 + "0002")), 2)
        self.assertEqual(self.rows(h, "IVA800" + "99991231" + "99991231"),
                         [])


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
        # decimal hours, not hex -- see `test_8c4_is_a_timeout_in_decimal_hours`
        self.assertFalse(refused(h, "S8C400" + "12"))


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
