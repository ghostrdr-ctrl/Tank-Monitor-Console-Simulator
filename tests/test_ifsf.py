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


def a_tank_console():
    c = Console()
    c.values["S60101"] = "011"
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.tank_level[1] = {"volume": 6000.0, "water": 2.0}
    return c


class TheDatabases(unittest.TestCase):
    def test_every_listed_element_has_a_home(self):
        # the eight databases of section 8 are all defined
        for table in (ifsf.TLG_ELEMENTS, ifsf.PROBE_ELEMENTS,
                      ifsf.TLG_ERROR_ELEMENTS, ifsf.CONTENTS_ELEMENTS,
                      ifsf.TEMPERATURE_ELEMENTS, ifsf.PROBE_ERROR_ELEMENTS,
                      ifsf.COMMS_ELEMENTS):
            self.assertTrue(table)
            for did, (name, mandatory) in table.items():
                self.assertIsInstance(name, str)
                self.assertIn(mandatory, (True, False))

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


if __name__ == "__main__":
    unittest.main()
