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
"""The places two function codes look like a pair and are not.

Every test in this file exists because the obvious implementation -- one
shared table for two codes that differ by a letter -- would be wrong, and
wrong silently. They are gathered here rather than spread through the other
files because the pattern is the point: this manual does this repeatedly, and
the next person to add a code should read these first.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import (alarmreports, controls, hrmreports,      # noqa: E402
                       presets, recon, sumpreports)
from tls350sim.console import Console                            # noqa: E402
from tls350sim.wire import Handler                               # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    c.set_board("E6")
    for card in ("smart", "vmc", "mt", "wplld", "plld", "modem", "universal"):
        c.modules[card] = 4
    c.software.update({"fuelman": True, "csld": True, "bir": True,
                       "isd": True, "pmc": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def refused(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3)).startswith("9999")


class SameDigitDifferentMeaning(unittest.TestCase):

    def test_087_and_088_status_tables_disagree_on_six_digits(self):
        shared = set(controls.PLLD_TEST_STATUS) & set(controls.WPLLD_TEST_STATUS)
        differ = [d for d in shared
                  if controls.PLLD_TEST_STATUS[d]
                  != controls.WPLLD_TEST_STATUS[d]]
        self.assertEqual(len(differ), 6, sorted(differ))
        self.assertEqual(controls.PLLD_TEST_STATUS["03"],
                         "TESTING AT 0.10 GAL/HR")
        self.assertEqual(controls.WPLLD_TEST_STATUS["03"],
                         "TESTING AT 0.20 GAL/HR")

    def test_411_and_412_alarm_tables_disagree(self):
        """0002 is a disabled board on one and a disconnected meter on the
        other, in identical byte layouts."""
        self.assertEqual(sumpreports.VMCI_ALARMS["0002"], "DISABLED ALARM")
        self.assertEqual(sumpreports.VMC_ALARMS["0002"],
                         "METER NOT CONNECTED")
        self.assertNotEqual(sumpreports.VMCI_MAX, sumpreports.VMC_MAX)

    def test_the_report_type_field_has_two_bases(self):
        """01 is PREVIOUS on C07 and CURRENT on C10."""
        self.assertIs(recon.previous_wanted("C07", "01"), True)
        self.assertIs(recon.previous_wanted("C10", "01"), False)


class SameLetterDifferentField(unittest.TestCase):

    def test_tt_is_a_status_on_317_and_a_count_on_319(self):
        """Same family, same version, same position after the sensor number."""
        self.assertEqual(sumpreports.SUMP_REPORTS["317"]["tt"], "status")
        self.assertEqual(sumpreports.SUMP_REPORTS["318"]["tt"], "status")
        self.assertEqual(sumpreports.SUMP_REPORTS["319"]["tt"], "count")
        self.assertEqual(sumpreports.SUMP_REPORTS["31A"]["tt"], "count")

    def test_the_count_variants_carry_a_count(self):
        c, h = a_site()
        send(h, "S09901149")          # put sump 1 into a test
        body = send(h, "i31901").strip(chr(1) + chr(3)).split("&&")[0][6 + 10:]
        # sensor number then a COUNT, not a status
        self.assertEqual(body[0:2], "01")
        self.assertLessEqual(int(body[2:4]),
                             sumpreports.SUMP_REPORTS["319"]["rows"])

    def test_225_takes_a_period_and_227_takes_a_date(self):
        """The second argument is a different field, not a different value."""
        _c, h = a_site()
        self.assertFalse(refused(h, "I22501" + "02"))
        self.assertFalse(refused(h, "I22701" + "0317"))


class RecordWidthsThatDiffer(unittest.TestCase):

    def test_only_114_carries_the_state_byte(self):
        self.assertIn("114", alarmreports.HAS_STATE)
        self.assertNotIn("113", alarmreports.HAS_STATE)
        self.assertNotIn("115", alarmreports.HAS_STATE)

    def test_113_records_are_eighteen_characters(self):
        c, h = a_site()
        c.tank_level[1]["volume"] = 100.0
        for _ in range(4):
            c.clock_offset += 600
            c.tick()
        body = send(h, "i11300").strip(chr(1) + chr(3)).split("&&")[0]
        body = body[6 + 10 + 80:]            # past the code, stamp, headers
        self.assertTrue(body, "the tank should be in alarm")
        self.assertEqual(len(body) % 18, 0, len(body))

    def test_116_and_11a_share_a_name_and_not_a_layout(self):
        """Both are "Service Report History" and neither can read the other."""
        self.assertEqual(alarmreports.SERVICE_WIDTHS["116"], (10, 5, False))
        self.assertEqual(alarmreports.SERVICE_WIDTHS["11A"], (6, 4, True))
        self.assertIn("116", alarmreports.HEADERS)
        self.assertNotIn("11A", alarmreports.HEADERS)

    def test_four_neighbours_count_records_three_ways(self):
        self.assertEqual(alarmreports.count_field("116", 12), "12")
        self.assertEqual(alarmreports.count_field("11A", 12), "12")
        self.assertEqual(alarmreports.count_field("11B", 12), "0C")
        self.assertEqual(alarmreports.count_field("119", 12), "00012")


class TheWorstOne(unittest.TestCase):
    """B61 and B62 number the same eight faults in permuted AND rebased
    order, so no offset converts one to the other."""

    def test_no_offset_maps_one_scheme_to_the_other(self):
        offsets = set()
        for name in hrmreports.FAULTS:
            offsets.add(hrmreports.B61_BIT[name]
                        - int(hrmreports.B62_CODE[name]))
        self.assertGreater(len(offsets), 1,
                           "if this were one offset the tables could merge")

    def test_they_lead_with_different_faults(self):
        first_61 = min(hrmreports.FAULTS, key=lambda n: hrmreports.B61_BIT[n])
        first_62 = min(hrmreports.FAULTS,
                       key=lambda n: int(hrmreports.B62_CODE[n]))
        self.assertEqual(first_61, "VALVE COMMAND FAULT")
        self.assertEqual(first_62, "CAP NOT CHARGING FAULT")

    def test_b61_has_a_hole_where_b62_does_not(self):
        bits = sorted(hrmreports.B61_BIT.values())
        self.assertNotIn(2, bits, "bit 2 is unused on B61")
        codes = sorted(int(v) for v in hrmreports.B62_CODE.values())
        self.assertEqual(codes, list(range(8)), "B62 is contiguous from 0")

    def test_every_fault_is_in_both_tables(self):
        for name in hrmreports.FAULTS:
            self.assertIn(name, hrmreports.B61_BIT, name)
            self.assertIn(name, hrmreports.B62_CODE, name)


class OneFieldTwoMeaningsInOneCode(unittest.TestCase):

    def test_52d_inverts_its_own_flag(self):
        """Writing f=1 CLEARS the alarm; reading f=1 means it is ON."""
        c, h = a_site()
        c.autodial_alarm[1] = True
        self.assertIn("ALARM", send(h, "I52D01"))
        c.autodial_alarm[1] = False
        self.assertIn("CLEAR", send(h, "I52D01"))

    def test_119s_data_field_is_read_by_its_type(self):
        for kind in ("filler", "login", "alarm", "service", "device"):
            self.assertIn(kind, alarmreports.MAINTENANCE_DATA.values())
        self.assertEqual(alarmreports.MAINTENANCE_DATA["03"], "login")
        self.assertEqual(alarmreports.MAINTENANCE_DATA["07"], "alarm")
        self.assertEqual(alarmreports.MAINTENANCE_DATA["0B"], "service")


class VerificationCodesSitInDifferentPlaces(unittest.TestCase):

    def test_8a4_wants_it_leading_and_refuses_it_trailing(self):
        """The only code in REVISION U that does -- Revision AA adds VA7, see
        tests/test_later.py. A generic strip-the-trailing-149 helper breaks on
        both."""
        _c, h = a_site()
        self.assertTrue(refused(h, "S8A400ABC123149"))
        self.assertFalse(refused(h, "S8A400149ABC123"))

    def test_79e_wants_it_trailing(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "S79E00"))
        self.assertFalse(refused(h, "S79E00149"))

    def test_79d_wants_none_at_all(self):
        """Its neighbour is gated and it is not -- do not model them as a
        pair."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S79D0001"))

    def test_the_isd_section_confirms_at_the_front(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "sV4400C00000003E4CCCCD"))
        self.assertFalse(refused(h, "sV4400149C00000003E4CCCCD"))


class TheDisplayAndTheWireDisagree(unittest.TestCase):

    def test_a61_prints_a_column_its_packed_form_cannot_fill(self):
        """A61 and A63 share a printed header including ENDTEMP, and only
        A63's computer format carries a temperature."""
        _c, h = a_site()
        self.assertIn("ENDTEMP", send(h, "IA6101"))
        self.assertIn("ENDTEMP", send(h, "IA6301"))
        one = send(h, "iA6101").strip(chr(1) + chr(3)).split("&&")[0]
        two = send(h, "iA6301").strip(chr(1) + chr(3)).split("&&")[0]
        self.assertGreater(len(two), len(one),
                           "A63 carries a float A61 does not")

    def test_680_has_no_computer_format_at_all(self):
        """The only report in the manual that says so."""
        _c, h = a_site()
        self.assertFalse(refused(h, "I68001"))
        self.assertTrue(refused(h, "i68001"))


class SomeCodesAreThingsYouDoNotThingsItHolds(unittest.TestCase):
    """29 codes have a Set format in the manual and no Inquire format at all.

    An Inquire to one of them used to come back as a header and a timestamp
    with nothing after it, which is the worst possible answer: it reads as
    "answered" to anything counting replies, and as an empty setting to
    anything reading one. 9999 is the honest answer -- the console has nothing
    to hold, because these are things you DO.
    """

    def test_a_system_reset_cannot_be_read_back(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "I00100"))
        self.assertTrue(refused(h, "i00100"))

    def test_neither_can_a_line_leak_test_you_started(self):
        """087 starts a test. The RESULT is a different code."""
        _c, h = a_site()
        self.assertTrue(refused(h, "I08701"))
        self.assertFalse(refused(h, "S08701149" + "01"))

    def test_nor_a_report_you_cleared(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "I05101"))

    def test_the_set_half_of_each_still_works(self):
        """Refusing the Inquire must not refuse the Set beside it."""
        _c, h = a_site()
        for code in ("S05101", "S05201", "S05301"):
            self.assertFalse(refused(h, code), code)

    def test_none_of_them_answers_with_an_empty_body(self):
        """The symptom that found this: a reply that is a header and
        nothing else."""
        from tls350sim import wire
        _c, h = a_site()
        for tok in sorted(wire.SET_ONLY):
            reply = send(h, "I" + tok + "01").strip(chr(1) + chr(3))
            rest = "".join(reply.splitlines()[2:]).strip()
            self.assertTrue(reply.startswith("9999") or rest,
                            f"{tok} answers with a header and nothing else")


class ARuleIsOnlyAsNewAsTheManualItCameFrom(unittest.TestCase):
    """INQUIRE_ONLY was first derived from Revision U and missed 132.

    132 is the Fiscal Height Security Report, and Revision U does not carry it
    at all -- so a rule built from U silently granted it a Set it should never
    have had. A rule about what the manual documents inherits the gaps of
    whichever manual it was built from.
    """

    def test_132_is_a_report_and_refuses_a_set(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "I13200"))
        self.assertTrue(refused(h, "S13200" + "01"))

    def test_the_table_came_from_revision_y(self):
        from tls350sim import wire
        self.assertIn("132", wire.INQUIRE_ONLY)
        # and the eight Rev Y reports that came with it
        for code in ("404", "BA1", "V12", "V82", "V88", "VA1", "VA2", "VA3"):
            self.assertIn(code, wire.INQUIRE_ONLY, code)

    def test_the_six_a_real_dump_writes_are_still_allowed(self):
        """Refusing what a real backup restores breaks the restore."""
        from tls350sim import wire
        for code in ("680", "773", "780", "790", "7A0", "887"):
            self.assertNotIn(code, wire.INQUIRE_ONLY, code)


class EveryCodeTheManualCarriesAnswers(unittest.TestCase):
    """This used to allow eleven exceptions -- the codes Revision U does not
    document. Revision Y turned up and documents all eleven, so there is no
    exception list any more."""

    THE_ELEVEN = {"404", "54E", "8C3", "8C4", "BA1",
                  "V12", "V82", "V88", "VA1", "VA2", "VA3"}

    def test_every_documented_code_is_answered(self):
        from tls350sim import wire
        missing = {c for c in wire.DOCUMENTED if c not in wire.KNOWN}
        self.assertEqual(missing, set(), sorted(missing))

    def test_the_eleven_revision_u_lacks_are_among_them(self):
        from tls350sim import wire
        for code in sorted(self.THE_ELEVEN):
            self.assertIn(code, wire.KNOWN, code)


if __name__ == "__main__":
    unittest.main()
