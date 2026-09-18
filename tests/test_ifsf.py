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
"""IFSF database support, 576013-635 section 8.

Every supported data element reads the same console state the standard
protocol reads, so the two personalities never disagree about a tank; the
LON transport the manual delegates to external IFSF specs is not invented.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console
from tls350sim import ifsf


def float_value(v):
    return struct.pack(">f", v).hex().upper()


# 8.2's DB_Ad written the way the manual concatenates it, "TLG_DAT (01H) +
# TLG_ER_DAT (41H) + TLG_ER_ID (01H-40H)", pointing at the first record.
TLG_ERROR_1 = (ifsf.DB_TLG, ifsf.SUB_TLG_ERROR, 0x01)


def a_tank_console():
    c = Console()
    c.values["S60101"] = "011"
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.tank_level[1] = {"volume": 6000.0, "water": 2.0}
    # Section 8 answers "when equipped with the appropriate software and
    # interface module", and `read` asks now, so a console this file expects
    # an answer out of is one somebody ordered IFSF for. See FIDELITY J2.
    c.set_setting("ifsf_platform", 1, 0)
    return c


class TheDatabases(unittest.TestCase):
    def test_every_listed_element_has_a_home(self):
        # the databases of section 8 that have any supported row at all
        for table in (ifsf.TLG_ELEMENTS, ifsf.PROBE_ELEMENTS,
                      ifsf.TLG_ERROR_ELEMENTS,
                      ifsf.TEMPERATURE_ELEMENTS, ifsf.PROBE_ERROR_ELEMENTS):
            self.assertTrue(table)
            for did, (name, mandatory) in table.items():
                self.assertIsInstance(name, str)
                self.assertIn(mandatory, (True, False))

    def test_the_two_unsupported_databases_have_no_element_table(self):
        """8.7's eight rows are all `Supported = No` and 8.4's two rows are
        `1 Strap_Level O No` and `2 Strap_Vol O No`. A database the manual
        supports nothing in is an address and nothing else."""
        self.assertFalse(hasattr(ifsf, "DOWNLOAD_ELEMENTS"))
        self.assertFalse(hasattr(ifsf, "CONTENTS_ELEMENTS"))
        self.assertTrue(ifsf.DB_DOWNLOAD)
        self.assertTrue(ifsf.SUB_CONTENTS)

    def test_the_communication_table_carries_no_m_o_flag(self):
        """8.8's heading is "Data_Id  Variable Name  Supported", three
        columns where every other table in section 8 has four. There is no
        M/O to record, so this table holds names and nothing else, and all
        eight of its rows are there."""
        for did, name in ifsf.COMMS_ELEMENTS.items():
            self.assertIsInstance(name, str)
        self.assertEqual(sorted(ifsf.COMMS_ELEMENTS), [1, 2, 3, 4, 5, 10,
                                                       11, 12])

    def test_the_probe_database_is_addressed_per_tank(self):
        self.assertEqual(ifsf.probe_tank(0x21), 1)
        self.assertEqual(ifsf.probe_tank(0x28), 8)
        self.assertIsNone(ifsf.probe_tank(0x01))     # the TLG db, not a probe


class TheTlgDatabase(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()

    def test_nb_tanks_counts_the_programmed_tanks(self):
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 1), 1)
        self.c.values["S60102"] = "021"
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 1), 2)

    def test_manufacturer_model_and_type(self):
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 50), "VEEDER-ROOT")
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 51), "TLS-350")
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 52), "TLG")

    def test_software_version_matches_the_console(self):
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 54),
                         self.c.software_info()["version"])

    def test_an_unsupported_id_reads_none(self):
        self.assertIsNone(ifsf.read(self.c, ifsf.DB_TLG, 999))


class TheProbeDatabase(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()

    def test_it_reads_the_same_tank_the_standard_protocol_does(self):
        # product level is the console's own stick height, to the inch
        level = ifsf.read(self.c, 0x21, 64)
        self.assertAlmostEqual(level, self.c.stick_height(1), places=1)

    def test_volume_water_and_temperature(self):
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 65), 6000.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 68), 2.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 67),
                               self.c.product_temperature(1), places=1)

    def test_the_product_label(self):
        self.assertEqual(ifsf.read(self.c, 0x21, 7), "REGULAR UNLEADED")

    def test_status_follows_the_probe(self):
        self.assertEqual(ifsf.read(self.c, 0x21, 32), "NORMAL")
        self.c.probe_out.add(1)
        self.assertEqual(ifsf.read(self.c, 0x21, 32), "OUT")

    def test_an_unprogrammed_tank_reads_none(self):
        self.assertIsNone(ifsf.read(self.c, 0x23, 64))   # tank 3, not there


class ThePlatform(unittest.TestCase):
    def test_an_ifsf_console_is_recognised(self):
        c = a_tank_console()
        c.set_setting("ifsf_platform", 1, 0)
        self.assertTrue(ifsf.is_ifsf(c))

    def test_a_standard_console_is_not(self):
        c = a_tank_console()
        c.set_setting("ifsf_platform", 0, 0)
        self.assertFalse(ifsf.is_ifsf(c))

    def test_a_console_nobody_has_ordered_it_for_is_not_on_the_platform(self):
        """8.0: "When equipped with the appropriate software and interface
        module, these systems can respond to commands using the
        International Forecourt Standards Forum (IFSF) tank gauge
        communications protocols". Equipped, so the gate defaults shut and
        an untouched console is a standard one."""
        self.assertFalse(ifsf.is_ifsf(Console()))

    def test_a_standard_console_answers_no_database_at_all(self):
        """The other half of 8.0's "when equipped": the databases are the
        equipped console's, so every one of them is silent on a console
        that is not on the platform. `read` is the whole interface, so it
        is the only place the gate can be asked."""
        c = a_tank_console()
        c.set_setting("ifsf_platform", 0, 0)
        self.assertIsNone(ifsf.read(c, ifsf.DB_TLG, 1))
        self.assertIsNone(ifsf.read(c, 0x21, 64))
        self.assertIsNone(ifsf.read(c, TLG_ERROR_1, 1))
        self.assertIsNone(ifsf.read(c, ifsf.DB_COMMS, 2))

    def test_the_gate_is_asked_before_any_element_is(self):
        """The same tank, read twice, with only the switch moved."""
        c = a_tank_console()
        self.assertEqual(ifsf.read(c, ifsf.DB_TLG, 51), "TLS-350")
        c.set_setting("ifsf_platform", 0, 0)
        self.assertIsNone(ifsf.read(c, ifsf.DB_TLG, 51))


class TheUnitsAndTheCountry(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()

    def test_the_measurement_units_are_the_system_units_flag(self):
        """517's first digit, "U - System Units: 3=Imperial Gallons
        2=Metric 1=U.S.". Both TLG_Measurement_Units (3) and
        TP_Measurement_Units (23) report it, and a console programmed
        metric says METRIC."""
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 3), "US")
        self.assertEqual(ifsf.read(self.c, 0x21, 23), "US")
        self.c.values["S51700"] = "201"
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 3), "METRIC")
        self.assertEqual(ifsf.read(self.c, 0x21, 23), "METRIC")
        self.c.values["S51700"] = "301"
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 3), "IMPERIAL")

    def test_the_units_never_disagree_with_the_console_s_own_flag(self):
        """`Console.metric()` reads the same digit of the same function, so
        the two personalities cannot label the same tank differently."""
        for flag, metric in (("101", False), ("201", True), ("301", False)):
            self.c.values["S51700"] = flag
            self.assertEqual(self.c.metric(), metric)
            self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 3) == "METRIC",
                             metric)

    def test_the_country_code_is_the_iso3166_one(self):
        """54D, "Set IS03166 3 Character Country Code", whose note gives
        "aaa - ISO3166 Country Code (3 ASCII characters [20h-7EH])" and
        whose screen prints "ISO3166 COUNTRY CODE: ESP". Unprogrammed it is
        blank, the way Maint_Password is."""
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 6), "")
        self.c.values["S54D00"] = "ESP"
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 6), "ESP")

    def test_the_country_code_does_not_move_when_the_units_do(self):
        """They were one token doing two jobs. The manual keeps them in two
        functions and never conflates them."""
        self.c.values["S54D00"] = "ESP"
        self.c.values["S51700"] = "201"
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 6), "ESP")
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 3), "METRIC")


class TheQuantities(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()
        self.c.values["S60A01"] = "01" + float_value(10000.0)

    def test_gross_standard_volume_is_the_corrected_volume(self):
        """8.3 lists Gross_Standard_Volume (66) apart from
        Total_Observed_Volume (65), and the corrected quantity is the one
        function 201 prints as TC VOLUME beside VOLUME. The reference
        temperature is pulled away from the product's here so the
        correction cannot come out as no correction at all."""
        self.c.values["S50E00"] = float_value(0.0)
        tov = ifsf.read(self.c, 0x21, 65)
        gsv = ifsf.read(self.c, 0x21, 66)
        self.assertAlmostEqual(tov, 6000.0)
        self.assertAlmostEqual(gsv, round(self.c.tc_volume(1), 2), places=1)
        self.assertNotAlmostEqual(gsv, tov, places=0)

    def test_the_high_water_setpoint_is_the_limit_and_not_the_warning(self):
        """"When water in the tank rises to this High Water Limit value,
        the system triggers an alarm" -- 576013-623 Rev AN, High Water
        Limit, 624. 627 is the Water Warning, which "acts as a pre-warning
        to the High Water Limit" and is set lower."""
        self.c.values["S62401"] = "01" + float_value(13.5)
        self.c.values["S62701"] = "01" + float_value(11.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 19), 13.5)

    def test_a_product_setpoint_is_not_served_a_water_limit(self):
        """15 is HiHi_Level_Setpoint, a product element, and 8.3 marks it
        `Supported = No`. It was answering 624, the High Water Level
        Limit, which belongs to Hi_Water_Setpoint."""
        self.c.values["S62401"] = "01" + float_value(13.5)
        self.assertIsNone(ifsf.read(self.c, 0x21, 15))

    def test_max_safe_fill_is_the_overfill_limit_and_not_the_shell(self):
        """"Set this percentage no greater than 90% of the tank's capacity"
        -- 576013-623 Rev AN, Overfill Limit, 623, which is a percent of the
        max or label volume. Shell_Capacity (11) is the full tank and
        Max_Safe_Fill_Capacity (12) is not the same number."""
        self.c.values["S62801"] = "01" + float_value(10000.0)
        self.c.values["S62301"] = "01" + float_value(90.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 11), 10000.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 12), 9000.0)

    def test_an_unprogrammed_overfill_limit_is_not_the_shell_either(self):
        """A console with no Overfill Limit programmed has no max safe fill
        to report, which is how `conditions()` reads the same limit -- it
        raises no overfill alarm on a tank that has none. Answering the
        shell capacity is what put 11 and 12 on the same number."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 12), 0.0)


class TheSupportedElementsThatWereSilent(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()
        self.c.values["S60301"] = "011"
        self.c.values["S62101"] = "01" + float_value(1000.0)
        self.c.values["S62801"] = "01" + float_value(10000.0)
        self.c.values["S62901"] = "01" + float_value(20.0)
        self.c.values["S64801"] = "01" + float_value(0.8)
        self.c.values["S60801"] = "01" + float_value(-1.25)

    def test_the_product_group_code_is_the_product_code(self):
        """603: "Enter the alphanumeric code used by a point-of-sale
        terminal or other external device to identify product for inventory
        control purposes" -- 576013-623 Rev AN, Product Code."""
        self.assertEqual(ifsf.read(self.c, 0x21, 8), "1")

    def test_low_capacity_is_the_low_product_limit(self):
        """621: "Low Product warns when volume in a tank recedes to the
        level you enter here" -- 576013-623 Rev AN, Low Product."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 13), 1000.0)

    def test_minimum_operating_capacity_is_the_delivery_limit(self):
        """629: "Delivery Limit warns when the level of fluid in the tank
        drops to a level at which the operator calls for a delivery. Set
        this percentage at a volume higher than that of the Low Product
        alarm" -- 576013-623 Rev AN, Delivery Limit. A percent of the max or
        label volume, so 20% of 10,000 gallons."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 14), 2000.0)

    def test_the_two_low_limits_are_not_the_same_number(self):
        """The manual sets one above the other -- "higher than that of the
        Low Product alarm" -- so a client reading both gets both."""
        self.assertLess(ifsf.read(self.c, 0x21, 13),
                        ifsf.read(self.c, 0x21, 14))

    def test_the_water_detection_threshold_is_the_water_minimum(self):
        """"When there is not water in the tank, but the water height
        measurement is not 0.0, the water float is resting on a layer of
        debris on the bottom of the tank. The Water Minimum Threshold sets
        the level" -- 576013-623 Rev AN p.7-16, the Programmable Minimum
        Water Threshold. Not 627, which is a warning limit."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 20), 0.8)
        self.c.values["S62701"] = "01" + float_value(3.0)
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 20), 0.8)

    def test_the_tank_tilt_offset_is_signed(self):
        """608: "If the probe is installed in the center of the tank, the
        value is 000.00 U.S.; 0000.0 Metric" -- 576013-623 Rev AN, Tank
        Tilt, whose worksheet works out a value that "may be a positive (+)
        or negative (-) value"."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 21), -1.25)
        self.assertAlmostEqual(ifsf.read(a_tank_console(), 0x21, 21), 0.0)

    def test_the_manifold_partners_are_both_kinds_of_manifold(self):
        """612 is the siphon set -- "This entry tells the system which tanks
        are siphon manifolded together" -- and 61D is the line set. The tank
        itself is not its own partner."""
        self.assertEqual(ifsf.read(self.c, 0x21, 22), "")
        self.c.values["S61201"] = "02"
        self.c.values["S61D01"] = "03"
        self.assertEqual(ifsf.read(self.c, 0x21, 22), "0203")

    def test_the_unsupported_probe_elements_answer_nothing(self):
        """8.3 marks 9 Ref_Density, 15 HiHi_Level_Setpoint, 16
        Hi_Level_Setpoint, 18 LoLo_Level_Setpoint and 69 Observed_Density
        `Supported = No`, and does not list 17 at all. Four of them were
        answering, two of them out of the density setting."""
        self.c.set_setting("density", 0.7400, 1)
        self.c.values["S62201"] = "01" + float_value(90.0)
        for did in (9, 15, 16, 17, 18, 69):
            self.assertIsNone(ifsf.read(self.c, 0x21, did), did)
            self.assertNotIn(did, ifsf.PROBE_ELEMENTS)

    def test_the_low_product_limit_serves_low_capacity_and_not_lolo(self):
        """621 was answering LoLo_Level_Setpoint (18), which the manual does
        not support, while Low_Capacity (13), which it does, answered
        nothing."""
        self.assertAlmostEqual(ifsf.read(self.c, 0x21, 13), 1000.0)
        self.assertIsNone(ifsf.read(self.c, 0x21, 18))


class TheSolicitedAndTheUnsolicited(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()

    def test_a_read_does_not_serve_an_unsolicited_element(self):
        """8.3 puts TP_Status_Message under a heading of its own,
        `UNSOLICITED`, and 8.2 and 8.6 put their id 100 rows under
        `UNSOLICITED DATA`. A read is what a client solicits."""
        self.assertIsNone(ifsf.read(self.c, 0x21, 100))
        self.assertIsNone(ifsf.read(self.c, TLG_ERROR_1, 100))

    def test_the_console_still_has_the_message_to_send(self):
        """The split is in who starts the exchange, not in whether the
        datum exists. `TP_Status_Message` is the message form of
        `TP_Status`, so the two agree -- which is why serving both to a
        read was the error, and not the agreement."""
        self.assertEqual(ifsf.unsolicited(self.c, 0x21), "NORMAL")
        self.assertEqual(ifsf.unsolicited(self.c, 0x21),
                         ifsf.read(self.c, 0x21, 32))
        self.c.probe_out.add(1)
        self.assertEqual(ifsf.unsolicited(self.c, 0x21), "OUT")
        self.assertEqual(ifsf.unsolicited(self.c, 0x21),
                         ifsf.read(self.c, 0x21, 32))

    def test_the_tlg_error_message_is_the_error_type_sent(self):
        """8.2's `TLG_Error_Type_Mes` is Mandatory and was unanswered
        anywhere. It is the message form of `TLG_Error_Type` (1)."""
        self.assertEqual(ifsf.unsolicited(self.c, TLG_ERROR_1),
                         ifsf.read(self.c, TLG_ERROR_1, 1))

    def test_only_id_100_is_unsolicited(self):
        """No other row of any table in section 8 sits under that heading."""
        self.assertEqual(ifsf.UNSOLICITED_ID, 100)
        self.assertIsNone(ifsf.unsolicited(self.c, 0x21, 32))

    def test_a_standard_console_sends_nothing_either(self):
        """8.0's "when equipped" gates what the console sends as well as
        what it answers."""
        self.c.set_setting("ifsf_platform", 0, 0)
        self.assertIsNone(ifsf.unsolicited(self.c, 0x21))


class TheConcatenatedAddresses(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()
        self.c.values["S60701"] = "01" + float_value(96.0)

    def test_a_single_byte_address_still_reads_as_one(self):
        """8.1, 8.3, 8.7 and 8.8 are addressed by one byte and say so --
        `DB_Ad=TLG_DAT (01H)`, `DB_Ad=TP_ID (21H-3FH)`, `DB_Ad=SW_DAT
        (81H)`, `DB_Ad=00H`."""
        self.assertEqual(ifsf.address(ifsf.DB_TLG), (0x01, None, None))
        self.assertEqual(ifsf.read(self.c, ifsf.DB_TLG, 51), "TLS-350")

    def test_a_concatenated_address_carries_its_three_parts(self):
        """"DB_Ad = TP_ID (21H-3FH) + TEMP_DAT (22H) + TEMP_ADDR
        (01H-08H)", which is three bytes and not one."""
        self.assertEqual(ifsf.address((0x21, ifsf.SUB_TEMPERATURE, 3)),
                         (0x21, 0x22, 3))
        self.assertEqual(ifsf.address((0x21, ifsf.SUB_CONTENTS)),
                         (0x21, 0x21, None))

    def test_the_two_forty_one_databases_are_two_databases(self):
        """41H under TLG_DAT is the console's error database (8.2) and 41H
        under a TP_ID is that probe's (8.6). They were one constant, and the
        console's was the one that answered."""
        self.assertEqual(ifsf.SUB_TLG_ERROR, ifsf.SUB_PROBE_ERROR)
        self.assertEqual(ifsf.read(self.c, TLG_ERROR_1, 3), 0)
        probe_error = (0x21, ifsf.SUB_PROBE_ERROR, 0x01)
        self.assertIsNone(ifsf.read(self.c, probe_error, 3))

    def test_the_contents_table_no_longer_answers_out_of_the_probe(self):
        """`ifsf.read(c, DB_CONTENTS, 1)` used to ask for Strap_Level and be
        handed tank 1's manufacturer name, because CAL_DAT (21H) and the
        first TP_ID are the same byte at different depths."""
        contents = (0x21, ifsf.SUB_CONTENTS, 0x01)
        self.assertEqual(ifsf.read(self.c, 0x21, 1), "VEEDER-ROOT")
        self.assertIsNone(ifsf.read(self.c, contents, 1))
        self.assertIsNone(ifsf.read(self.c, contents, 2))

    def test_the_error_record_selector_has_a_range(self):
        """"TLG_ER_ID (01H-40H)" is 64 records, and 41H is not one of
        them."""
        self.assertEqual(ifsf.read(self.c, TLG_ERROR_1, 1), "NONE")
        last = (ifsf.DB_TLG, ifsf.SUB_TLG_ERROR, 0x40)
        self.assertEqual(ifsf.read(self.c, last, 1), "NONE")
        past = (ifsf.DB_TLG, ifsf.SUB_TLG_ERROR, 0x41)
        self.assertIsNone(ifsf.read(self.c, past, 1))


class TheTankTemperatureTable(unittest.TestCase):
    def setUp(self):
        self.c = a_tank_console()
        self.c.values["S60701"] = "01" + float_value(96.0)

    def node(self, n, data_id):
        return ifsf.read(self.c, (0x21, ifsf.SUB_TEMPERATURE, n), data_id)

    def test_each_node_reads_its_own_height_and_temperature(self):
        """8.5's two elements are `Temp_height` and `Temp_value`, and the
        console's own ladder is "how far up the tank each of the six sits,
        bottom first" with a reading against each."""
        for n in range(1, self.c.THERMISTORS + 1):
            self.assertAlmostEqual(self.node(n, 1),
                                   round(self.c.thermistor_heights(1)[n - 1],
                                         2))
            self.assertIsNotNone(self.node(n, 2))

    def test_the_nodes_climb_the_tank(self):
        """Bottom first, so height rises with the node number."""
        heights = [self.node(n, 1) for n in range(1, self.c.THERMISTORS + 1)]
        self.assertEqual(heights, sorted(heights))

    def test_a_console_with_six_nodes_does_not_invent_two_more(self):
        """"TEMP_ADDR (01H-08H)" is the width of the address field. This
        console has six RTDs -- A15's "72.6 at T6 down to 67.6 at T1" -- so
        7 and 8 are addressable and empty, and 9 is not an address."""
        self.assertEqual(self.c.THERMISTORS, 6)
        self.assertIsNone(self.node(7, 1))
        self.assertIsNone(self.node(8, 1))
        self.assertIsNone(self.node(9, 1))
        self.assertIsNone(self.node(0, 1))

    def test_an_unprogrammed_tank_has_no_temperature_table(self):
        self.assertIsNone(ifsf.read(self.c, (0x23, ifsf.SUB_TEMPERATURE, 1),
                                    2))


if __name__ == "__main__":
    unittest.main()
