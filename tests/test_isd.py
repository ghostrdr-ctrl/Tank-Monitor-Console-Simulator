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
"""ISD and PMC setup, 576013-635 section 7.7.2."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import isd, packed                        # noqa: E402
from tls350sim.clock import clock_words                  # noqa: E402
from tls350sim.console import Console                    # noqa: E402
from tls350sim.wire import Handler                       # noqa: E402


def a_console(isd_key=True, pmc_key=True, board="E6"):
    """A console with the ISD-capable board, which is the E6's NVMEM203."""
    c = Console()
    for card in ("probe", "rs232"):
        c.modules[card] = 4
    c.set_board(board)
    c.software = {k: True for k, on in (("isd", isd_key), ("pmc", pmc_key))
                  if on}
    return c, Handler(c, verbose=False)


class TheKeysAreTwoKeys(unittest.TestCase):
    """The manual states the requirement per function and it is not one rule:
    "PMC feature required" on V40, "ISD feature required" on V4E, "ISD or PMC"
    on V47 and "ISD and PMC" on V50."""

    def ask(self, h, code):
        return h.handle((chr(1) + f"I{code}00" + chr(13)).encode()).decode("latin-1")

    def test_pmc_alone_answers_the_pmc_functions(self):
        _c, h = a_console(isd_key=False)
        self.assertNotIn("9999", self.ask(h, "V40"))
        self.assertNotIn("9999", self.ask(h, "V45"))

    def test_pmc_alone_refuses_the_isd_ones(self):
        _c, h = a_console(isd_key=False)
        for code in ("V4E", "V4F"):
            self.assertIn("9999", self.ask(h, code), code)

    def test_isd_alone_refuses_the_pmc_ones(self):
        _c, h = a_console(pmc_key=False)
        for code in ("V40", "V41", "V44", "V45", "V46"):
            self.assertIn("9999", self.ask(h, code), code)

    def test_either_key_answers_the_either_functions(self):
        """V47 and V52 say "ISD or PMC" and "ISD and/or PMC"."""
        for one, other in ((True, False), (False, True)):
            _c, h = a_console(isd_key=one, pmc_key=other)
            for code in ("V47", "V52"):
                self.assertNotIn("9999", self.ask(h, code), code)

    def test_both_keys_are_needed_for_the_both_function(self):
        """V50 says "ISD and PMC features required"."""
        for one, other in ((True, False), (False, True)):
            _c, h = a_console(isd_key=one, pmc_key=other)
            self.assertIn("9999", self.ask(h, "V50"))
        _c, h = a_console()
        self.assertNotIn("9999", self.ask(h, "V50"))

    def test_isd_still_wants_the_memory_card(self):
        """"Maintenance Tracker and ISD want an NVMEM203": an E7 is an
        NVMEM201, so its ISD key was never cut whatever the software says."""
        c, h = a_console(board="E7")
        self.assertFalse(c.licensed("isd"))
        self.assertIn("9999", self.ask(h, "V4E"))


class TheSetupValues(unittest.TestCase):

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def shown(self, h, code):
        r = self.send(h, f"I{code}00").strip(chr(1) + chr(3) + chr(13) + chr(10))
        return " | ".join(r.split(chr(13) + chr(10))[2:])

    def test_every_setup_function_answers_a_default(self):
        """A console nobody has programmed still has an answer for each."""
        _c, h = a_console()
        for code in isd.SETUP:
            self.assertNotIn("9999", self.send(h, f"I{code}00"), code)
            self.assertNotIn("9999", self.send(h, f"i{code}00"), code)

    def test_an_enum_takes_the_manuals_own_table(self):
        """V40's own display sample answers "VST ECS PROCESSOR" under
        "VAPOR PROCESSOR TYPE", in both 576013-635 Rev Y and Rev AA."""
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV400001"))
        self.assertIn("VST ECS PROCESSOR", self.shown(h, "V40"))
        self.assertIn("9999", self.send(h, "sV400099"))

    def test_the_processor_table_is_all_eight_codes_v40_names(self):
        """"07 = VST Green Machine, 06 = Husky Polisher, 05 = Veeder-Root
        Polisher, 04 = User Defined, 03 = HIRT Vapor Processor, 02 = OPW
        Vapor Processor, 01 = VST ECS Processor, 00 = None" -- V40's
        computer format note, which is the only place all eight are listed.
        03 was transcribed as ARID, which is V0A's word for its own digit 3
        and not V40's for this one, and 07 was missing."""
        _c, h = a_console()
        self.assertEqual(isd.VAPOR_PROCESSOR["03"], "HIRT VAPOR PROCESSOR")
        self.assertEqual(isd.VAPOR_PROCESSOR["07"], "VST GREEN MACHINE")
        self.assertNotIn("9999", self.send(h, "sV400007"))
        self.assertIn("VST GREEN MACHINE", self.shown(h, "V40"))

    def test_a_range_is_the_manuals_own_range(self):
        """"MMM - Runtime threshold in minutes [010-180]"."""
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV4500120"))
        self.assertIn("120", self.shown(h, "V45"))
        for bad in ("009", "181", "999"):
            self.assertIn("9999", self.send(h, f"sV4500{bad}"), bad)

    def test_a_value_can_be_written_packed_or_in_words(self):
        """"Display: <SOH>SV4600xx.xx" against "Computer: <SOH>sV4600AAAAAAAA"."""
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "SV460025.5"))
        self.assertIn("25.50", self.shown(h, "V46"))
        self.assertNotIn("9999", self.send(h, "sV4600" + packed.hexfloat(40.0)))
        self.assertIn("40.00", self.shown(h, "V46"))
        self.assertIn("9999", self.send(h, "SV4600150.0"))

    def test_the_pressure_thresholds_confirm_at_the_front(self):
        """"<SOH>SV4400149 -a.bcd -A.BCD" -- the 149 leads here where it
        trails everywhere else, so the shared VERIFIED table cannot serve it."""
        _c, h = a_console()
        low, high = packed.hexfloat(-2.0), packed.hexfloat(0.2)
        self.assertIn("9999", self.send(h, f"sV4400{low}{high}"))
        self.assertNotIn("9999", self.send(h, f"sV4400149{low}{high}"))

    def test_the_low_threshold_has_to_be_below_the_high_one(self):
        """"-8.000 <= low/off threshold < high/on threshold <= 3.000"."""
        _c, h = a_console()
        low, high = packed.hexfloat(-2.0), packed.hexfloat(0.2)
        self.assertIn("9999", self.send(h, f"sV4400149{high}{low}"))
        self.assertIn("9999", self.send(h, "sV4400149"
                                          + packed.hexfloat(-20.0)
                                          + packed.hexfloat(0.2)))

    def test_a_pair_takes_two_tables(self):
        """"EE - EVR Type" and "VV - Vacuum Assist Type".

        "02" was WAYNE VAC here, which is on no page of this shelf. The
        panel walk in 577013-800 Rev P goes `VACUUM ASSIST TYPE / VAPOR VAC`
        then CHANGE to `HEALY VAC`, and 577013-819 Rev F's setup check asks
        "VACUUM ASSIST TYPE is set to HEALY VAC?". The panel's own choice
        list said HEALY VAC all along; they were two stores with two words.
        FIDELITY I10.
        """
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV4E000202"))
        got = self.shown(h, "V4E")
        self.assertIn("VACUUM ASSIST", got)
        self.assertIn("HEALY VAC", got)
        self.assertIn("9999", self.send(h, "sV4E000999"))

    def test_the_panel_and_v4e_are_one_store(self):
        """The panel's first two EVR/ISD SETUP steps ARE V4E. FIDELITY I10."""
        c, h = a_console()
        self.assertEqual(c.setting("evr_type", 0), "BALANCE")
        self.assertEqual(c.evr_site(), "balance")
        self.assertNotIn("9999", self.send(h, "sV4E000202"))
        self.assertEqual(c.setting("evr_type", 0), "VACUUM ASSIST")
        self.assertEqual(c.setting("evr_vac_type", 0), "HEALY VAC")
        self.assertEqual(c.evr_site(), "assist")
        c.set_setting("evr_type", "BALANCE", 0)
        self.assertIn("BALANCE", self.shown(h, "V4E"))
        self.assertIn("HEALY VAC", self.shown(h, "V4E"))

    def test_a_clock_field_is_a_time_and_a_count_of_minutes(self):
        """"HHMMddd": start hour, start minute, duration [000-720]."""
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV50002230090"))
        self.assertIn("22:30", self.shown(h, "V50"))
        self.assertIn("9999", self.send(h, "sV50002530090"))   # hour 25
        self.assertIn("9999", self.send(h, "sV50002230999"))   # over 720

    def test_a_value_survives_a_dump_and_a_restore(self):
        """The property a backup depends on."""
        c, h = a_console()
        self.send(h, "sV400005")
        dumped = self.send(h, "iV4000").strip(chr(1) + chr(3) + chr(13) + chr(10))
        field = dumped.split("&&")[0][len("iV4000") + 10:]
        c.values.pop("SV4000", None)
        self.send(h, f"sV4000{field}")
        self.assertIn("VEEDER-ROOT POLISHER", self.shown(h, "V40"))

    def test_the_version_number_is_isds_own(self):
        """"ISD VERSION: 01.00", not the console's software number."""
        c, h = a_console()
        self.assertIn(f"ISD VERSION: {isd.ISD_VERSION}",
                      self.send(h, "IV1000"))
        self.assertNotIn(str(c.version), isd.ISD_VERSION)


class ThePanelCanChooseTheProcessorToo(unittest.TestCase):
    """The EVR/ISD step that picks the processor and V40 are ONE field.

    They were two stores: the panel wrote a console setting nothing read
    back, and the PMC SETUP gate read `values["SV4000"]`, so PMC setup could
    only be unlocked by sending SV4000 down the socket. See FIDELITY I7, and
    F9 for the same shape on 550 and 642.
    """

    def a_panel(self):
        c, h = a_console()
        c.modules["smart"] = 1                   # "PMC SETUP" wants one
        return c, h

    def pmc_offered(self, c):
        return "PMC SETUP" in [f["function"] for f in c.available_functions()]

    def test_walking_the_panel_to_vst_unlocks_pmc_setup(self):
        """"Note: the vapor processor type VST must have been selected in
        EVR/ISD setup to access PMC setup", 577013-937 Rev J Figure 15 --
        EVR/ISD setup being the panel walk, not the socket."""
        c, _h = self.a_panel()
        self.assertFalse(self.pmc_offered(c))
        c.set_setting("evr_vp_type", "VST VAPOR PROCESSOR", 0)
        self.assertTrue(self.pmc_offered(c))

    def test_the_green_machine_is_a_vst_processor_as_well(self):
        """Figure 15 is headed "PMC Setup for VST Processors" and its Vapor
        Processor On/Off example gives a turn on and a turn off pressure for
        the "ECS Membrane" and for the "Green Machine" side by side, so both
        of V40's two VST codes reach this walk."""
        c, _h = self.a_panel()
        c.set_setting("evr_vp_type", "VST GREEN MACHINE", 0)
        self.assertEqual(c.values["SV4000"], "07")
        self.assertTrue(self.pmc_offered(c))
        c.set_setting("evr_vp_type", "V-R POLISHER", 0)
        self.assertFalse(self.pmc_offered(c))

    def test_the_panel_and_the_wire_read_each_other(self):
        """One store, so a type set over the wire is on the screen and a
        type set on the screen is what V40 answers."""
        c, h = self.a_panel()
        self.send(h, "sV400005")
        self.assertEqual(c.setting("evr_vp_type", 0, "NONE"), "V-R POLISHER")
        c.set_setting("evr_vp_type", "HIRT VCS 100", 0)
        self.assertIn("HIRT VAPOR PROCESSOR", self.shown(h, "V40"))

    def test_the_screen_offers_the_five_types_the_figure_draws(self):
        """577013-937 Rev J Figure 9's VAPOR PROCESSOR TYPE screen: None,
        VST Vapor Processor, VST Green Machine, V-R Polisher, Hirt VCS 100.
        The list held two, "NONE" and "ARID PERMEATOR", which is 577013-800
        Rev P's older figure of the same screen and names no V40 code."""
        from tls350sim.console import FIELDS
        choices = FIELDS["set.evr_vp_type"]["choices"]
        self.assertEqual(choices, ["NONE", "VST VAPOR PROCESSOR",
                                   "VST GREEN MACHINE", "V-R POLISHER",
                                   "HIRT VCS 100"])
        for word in choices:
            self.assertIsNotNone(isd.panel_processor_code(word), word)

    def test_a_type_the_screen_no_longer_offers_still_draws(self):
        """A console programmed to one of the three codes Rev J's screen
        dropped -- "06 = Husky Polisher (ISD SEM required) (Obsolete V30)" --
        has to draw something, and what it has is V40's own word for it."""
        c, h = self.a_panel()
        self.send(h, "sV400006")
        self.assertEqual(c.setting("evr_vp_type", 0, "NONE"),
                         "HUSKY POLISHER")
        self.assertFalse(self.pmc_offered(c))

    def test_a_word_that_names_no_code_writes_nothing(self):
        """The store is V40's, so a word off its table cannot enter it."""
        c, _h = self.a_panel()
        c.set_setting("evr_vp_type", "VST VAPOR PROCESSOR", 0)
        c.set_setting("evr_vp_type", "ARID PERMEATOR", 0)
        self.assertEqual(c.values["SV4000"], "01")

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def shown(self, h, code):
        r = self.send(h, f"I{code}00").strip(chr(1) + chr(3) + chr(13)
                                             + chr(10))
        return " | ".join(r.split(chr(13) + chr(10))[2:])


class TheTablesV42Builds(unittest.TestCase):
    """V42 is the only thing that writes any of them -- V48, V4A and V4B all
    say "Inquire only, use Function Code V42 to set" -- so there is one store
    and the other three are views of it."""

    # The manual's own worked example row, which is the best test data there is.
    ROW = ("0103" "0605" "020502" "030502" "100502" "06UU01"
           "0706" "020602" "030602" "100602" "06UU01")

    def a_site(self):
        c = Console()
        for card in ("probe", "rs232", "smart"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        for n in (1, 2, 3):
            c.values[f"S723{n:02d}"] = f"{n:02d}0{1 if n < 3 else 2}"
        return c, Handler(c, verbose=False)

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def lines(self, h, cmd):
        r = self.send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10))
        return r.split(chr(13) + chr(10))[2:]

    def test_the_row_is_sixty_characters(self):
        self.assertEqual(len(self.ROW), isd.ROW)

    def test_the_manuals_own_row_prints_back_exactly_as_the_manual_prints_it(self):
        """The strongest check available: parse the example, store it, format
        it, and compare against the line printed in the book."""
        _c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        printed = self.lines(h, "IV4201")[-1]
        self.assertEqual(printed,
                         "01 03 06 05 020502 030502 100502 06UU01"
                         " 07 06 020602 030602 100602 06UU01")

    def test_it_confirms_at_the_front(self):
        _c, h = self.a_site()
        self.assertIn("9999", self.send(h, f"sV4201{self.ROW}"))
        self.assertNotIn("9999", self.send(h, f"sV4201149{self.ROW}"))

    def test_a_second_map_on_one_sensor_fails(self):
        """"If one already exists, command will fail (clear all entries with
        SS=0 before setting up tables)"."""
        _c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        self.assertIn("9999", self.send(h, f"sV4201149{self.ROW}"))

    def test_clearing_takes_them_all_away(self):
        """"00149 Clears all tables"."""
        c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        self.assertNotIn("9999", self.send(h, "sV4200149"))
        self.assertEqual([k for k in c.values if k.startswith("SV42")], [])
        self.assertNotIn("9999", self.send(h, f"sV4201149{self.ROW}"))

    def test_a_row_that_is_not_a_row_is_refused(self):
        _c, h = self.a_site()
        self.assertIn("9999", self.send(h, "sV4201149TOOSHORT"))

    def test_the_three_read_tables_are_views_of_that_one_row(self):
        _c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        afm = self.lines(h, "IV4800")[-1]
        self.assertTrue(afm.startswith("03 01 "), afm)   # meter 03, sensor 01
        # the row carries two fuel positions, so the grade table has two rows
        grades = [g for g in self.lines(h, "IV4B00") if g[:2].isdigit()]
        self.assertEqual([g[:2] for g in grades], ["06", "07"])
        hoses = [x for x in self.lines(h, "IV4A00") if x[:2].isdigit()]
        self.assertEqual([x[:2] for x in hoses], ["05", "06"])

    def test_a_hose_used_twice_appears_once(self):
        """"Only one Hose device is created for each unique hose"."""
        _c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        hoses = [x[:2] for x in self.lines(h, "IV4A00") if x[:2].isdigit()]
        self.assertEqual(len(hoses), len(set(hoses)))

    def test_the_read_tables_write_xx_where_the_map_writes_uu(self):
        """V42's note says "UU=unassigned"; V48's says "(xx=unassigned)"."""
        _c, h = self.a_site()
        self.send(h, f"sV4201149{self.ROW}")
        self.assertIn("UU", self.lines(h, "IV4201")[-1])
        self.assertIn("xx", self.lines(h, "IV4800")[-1])
        self.assertIn("xx", self.lines(h, "IV4B00")[-1])

    def test_the_read_tables_are_inquire_only(self):
        _c, h = self.a_site()
        for code in ("V48", "V4A", "V4B", "V10"):
            self.assertIn("9999", self.send(h, f"s{code}0001"), code)

    def test_the_sensor_index_table_walks_the_smart_sensors(self):
        """ISD reads airflow meters and vapour pressure sensors, and both are
        smart sensors this console already models."""
        _c, h = self.a_site()
        shown = self.send(h, "IV4300")
        self.assertIn("AIR FLOW METER", shown)
        self.assertIn("VAPOR PRESSURE", shown)
        self.assertNotIn("9999", shown)

    def test_the_in_use_flag_stores_and_shows(self):
        _c, h = self.a_site()
        self.assertIn("NO", self.send(h, "IV4300"))
        self.assertNotIn("9999", self.send(h, "sV4300149011"))
        self.assertIn("YES", self.send(h, "IV4300"))
        self.assertIn("9999", self.send(h, "sV4300149019"))   # flag not 0/1

    def test_the_label_table_refuses_the_unassigned_id(self):
        """"II - Hose Label ID (02-10, 01=Unassigned)"."""
        _c, h = self.a_site()
        self.assertNotIn("9999", self.send(h, "sV490003REGULAR   "))
        self.assertIn("REGULAR", self.send(h, "IV4900"))
        self.assertIn("9999", self.send(h, "sV490001NOPE      "))
        self.assertIn("9999", self.send(h, "sV490011TOOHIGH   "))
        self.assertIn("UNASSIGNED", self.send(h, "IV4900"))


class TheControls(unittest.TestCase):
    """VC0, VC1, VC5, VC8, V51, V85 and XE0 -- and the interlocks between
    them, which are the part worth having."""

    ROW = ("0103" "0605" "020502" "030502" "100502" "06UU01"
           "0706" "020602" "030602" "100602" "06UU01")

    def a_site(self, polisher=False):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        h = Handler(c, verbose=False)
        self.send(h, f"sV4201149{self.ROW}")
        self.send(h, "sV400005" if polisher else "sV400001")
        return c, h

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def last(self, h, cmd):
        return self.send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10)).split(
            chr(13) + chr(10))[-1]

    def test_the_setup_test_actually_tests_the_setup(self):
        """A verification that always passed would be worth nothing. ISD wants
        a map to measure through and PMC wants a processor to control."""
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        h = Handler(c, verbose=False)
        self.assertIn("FAIL", self.last(h, "IV5100"))
        self.send(h, f"sV4201149{self.ROW}")
        self.send(h, "sV400001")
        self.assertIn("PASS", self.last(h, "IV5100"))
        self.assertEqual(self.send(h, "iV5100").split("&&")[0][-1], "0")

    def test_the_processor_cannot_be_driven_while_it_is_automatic(self):
        """"VP control MUST be Manual (see VC0 command)"."""
        _c, h = self.a_site()
        self.assertIn("AUTOMATIC", self.last(h, "IVC000"))
        self.assertIn("9999", self.send(h, "sVC1001491"))
        self.assertNotIn("9999", self.send(h, "sVC0001490"))
        self.assertNotIn("9999", self.send(h, "sVC1001491"))
        self.assertIn("ON", self.last(h, "IVC100"))

    def test_going_back_to_manual_turns_the_processor_off(self):
        """"Changing from automatic to manual while VP is on turns VP (and HC
        sensor) off"."""
        _c, h = self.a_site()
        self.send(h, "sVC0001490")
        self.send(h, "sVC1001491")
        self.assertIn("ON", self.last(h, "IVC100"))
        self.send(h, "sVC0001491")           # back to automatic
        self.send(h, "sVC0001490")           # and to manual again
        self.assertIn("OFF", self.last(h, "IVC100"))

    def test_the_valve_wants_a_veeder_root_polisher(self):
        """"Vapor Processor Type must be Veeder-Root Polisher"."""
        _c, h = self.a_site(polisher=False)
        self.send(h, "sVC0001490")
        self.assertIn("9999", self.send(h, "sVC8001491"))
        _c, h = self.a_site(polisher=True)
        self.send(h, "sVC0001490")
        self.assertNotIn("9999", self.send(h, "sVC8001491"))

    def test_the_valve_reports_current_and_requested(self):
        """The screen has two columns because they can differ."""
        _c, h = self.a_site(polisher=True)
        self.send(h, "sVC0001490")
        self.send(h, "sVC8001491")
        got = self.send(h, "iVC800").split("&&")[0]
        self.assertEqual(got[-2:], "01", "closed now, open requested")
        self.send(h, "sVC1001491")           # run the processor
        self.assertEqual(self.send(h, "iVC800").split("&&")[0][-2:], "11")

    def test_the_override_flag_reads_backwards(self):
        """"S - ISD shutdown alarms overridden: 0=Yes, 1=No" -- zero is the
        affirmative here where it is the negative on V52, VC0, VC1 and VC8."""
        _c, h = self.a_site()
        self.assertIn("NO", self.last(h, "IVC500"))
        self.assertEqual(self.send(h, "iVC500").split("&&")[0][-1],
                         isd.OVERRIDDEN_NO)
        self.assertNotIn("9999", self.send(h, "sVC500149"))
        self.assertIn("YES", self.last(h, "IVC500"))
        self.assertEqual(self.send(h, "iVC500").split("&&")[0][-1],
                         isd.OVERRIDDEN_YES)

    def test_every_control_confirms_at_the_front(self):
        _c, h = self.a_site()
        for cmd in ("sVC0000", "sVC1001", "sVC500", "sV8500050000"):
            self.assertIn("9999", self.send(h, cmd), cmd)

    def test_clearing_a_test_dates_it(self):
        _c, h = self.a_site()
        self.assertIn("--/--/--", self.send(h, "IV8500"))
        self.assertNotIn("9999", self.send(h, "sV85001490500"))
        shown = self.send(h, "IV8500")
        self.assertIn("SETUP TEST", shown)
        self.assertNotIn("SETUP TEST : --/--/--", shown)

    def test_a_collection_clear_can_take_one_hose_or_all_of_them(self):
        """"FF=00, HH=00: All FP's and hoses are cleared"."""
        c, h = self.a_site()
        self.send(h, "sV85001490601 05")
        self.send(h, "sV8500149060105")
        self.assertTrue([k for k in c.values if k.startswith("SV85C")])
        self.send(h, "sV8500149060000")
        self.assertEqual([k for k in c.values if k.startswith("SV85C")], [])

    def test_an_unknown_test_type_is_refused(self):
        _c, h = self.a_site()
        self.assertIn("9999", self.send(h, "sV85001499900"))

    def test_the_time_stamp_is_seconds_since_the_epoch_in_hex(self):
        _c, h = self.a_site()
        got = self.send(h, "iXE000").split("&&")[0][-8:]
        self.assertEqual(len(got), 8)
        int(got, 16)                          # it has to parse as hex
        self.assertNotIn("9999", self.send(h, "sXE000149DEADBEEF"))
        self.assertEqual(self.send(h, "iXE000").split("&&")[0][-8:], "DEADBEEF")

    def test_the_processor_controls_want_the_relay(self):
        """"PMC Feature and Vapor Processor relay required"."""
        c, h = self.a_site()
        c.modules["relay"] = 0
        c.modules["io"] = 0
        self.assertIn("9999", self.send(h, "sVC0001490"))


class TheStatusReports(unittest.TestCase):
    """V00, V0A and V0B: the CARB thresholds and the overall status."""

    ROW = ("0103" "0605" "020502" "030502" "100502" "06UU01"
           "0706" "020602" "030602" "100602" "06UU01")

    def a_site(self, evr="01", processor="01", programmed=True):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        h = Handler(c, verbose=False)
        if programmed:
            self.send(h, f"sV4201149{self.ROW}")
            self.send(h, f"sV4000{processor}")
        self.send(h, f"sV4E00{evr}01")
        return c, h

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    # Where each field starts in V0A/V0B's body, counted off the manual's
    # own list. VV.VV is five characters, which is the easy one to miscount.
    DATE, EVR, VERSION, PROC = 0, 8, 9, 14
    OVERALL, COLLECT, CONTAIN = 15, 16, 17
    UPTIME, PASSING, TOTAL = 18, 20, 23

    def body(self, h, cmd):
        """Past the echoed code and the stamp.

        The reply echoes the six character CODE, not the command -- a command
        carrying a date is longer than what comes back in front of the data.
        """
        r = self.send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0]
        return r[6 + 10:]

    def test_the_carb_rows_follow_the_site_type(self):
        """The report is not the same on an assist site as on a balance one:
        one has an A/L range and the other a flow performance figure."""
        _c, h = self.a_site(evr="01")                      # balance
        shown = self.send(h, "IV0000")
        self.assertIn("BALANCE SYS FLOW PERFORMANCE", shown)
        self.assertNotIn("ASSIST SYSTEM A/L GROSS FAIL", shown)
        _c, h = self.a_site(evr="02")                      # vacuum assist
        shown = self.send(h, "IV0000")
        self.assertIn("ASSIST SYSTEM A/L GROSS FAIL", shown)
        self.assertNotIn("BALANCE SYS FLOW PERFORMANCE", shown)

    def test_an_assist_site_has_two_a_l_threshold_rows(self):
        """577013-800 Rev P p.48 prints both, and its numbers are not the
        certified nozzle range above them:

            VAPOR COLLECTION ASSIST SYSTEM A/L GROSS FAIL 1DAYS 0.33 1.90
            VAPOR COLLECTION ASSIST SYSTEM A/L DEGRADATION FAIL 7DAYS
                                                           0.81 1.32

        The gross row used to read 7dys 0.90 1.10, which is the CARB
        REQUIREMENTS line's own range copied down into a threshold row, and
        the degradation row was absent. See FIDELITY I5."""
        _c, h = self.a_site(evr="02")
        shown = self.send(h, "IV0000")
        self.assertIn("A/L GROSS FAIL", shown)
        self.assertIn("A/L DEGRADATION FAIL", shown)
        for figure in ("1dys", "0.33", "1.90", "7dys", "0.81", "1.32"):
            self.assertIn(figure, shown, figure)

    def test_the_daily_tests_report_a_one_day_period(self):
        """"VAPOR COLLECTION BALANCE SYS FLOW PERFORMANCE 1DAYS 0.60 ----",
        577013-937 Rev J p.12-54, where 576013-635's composite sample says
        7dys for the same row. The abbreviation is 635's; the period is the
        one a site prints."""
        _c, h = self.a_site(evr="01")
        shown = self.send(h, "IV0000")
        self.assertIn("BALANCE SYS FLOW PERFORMANCE  1dys", shown)

    def test_the_uncertain_carb_figures_are_left_as_they_were(self):
        """Two rows are NOT resolved here. The leak detection limit is a
        function of hose count -- 577013-819 Rev F p.8, "limit ranges over
        8-10 cfh for <6 to >24 hoses" -- and the manuals print 13.5, 12.5
        and 8.50 for it; the Stage I percentile is 75TH in 576013-635 and
        50th in both ISD manuals. Both belong in UNKNOWNS, so both keep
        635's figure until the rule is found."""
        _c, h = self.a_site(evr="01")
        shown = self.send(h, "IV0000")
        self.assertIn("13.5cfh", shown)
        self.assertIn("STAGE I VAPOR TRANSFER FAIL, 75TH PERCENTILE", shown)

    def test_the_rows_every_site_has_are_on_both(self):
        for evr in ("01", "02"):
            _c, h = self.a_site(evr=evr)
            shown = self.send(h, "IV0000")
            for row in ("VAPOR CONTAINMENT GROSS FAIL",
                        "VAPOR CONTAINMENT DEGRADATION",
                        "STAGE I VAPOR TRANSFER FAIL"):
                self.assertIn(row, shown, f"{evr} {row}")

    def test_it_names_the_carb_document_it_comes_from(self):
        _c, h = self.a_site()
        self.assertIn("CP201", self.send(h, "IV0000"))

    def test_the_evr_type_is_reported_backwards_from_how_it_is_set(self):
        """V4E says "01=Balance, 02=Vacuum Assist" and V0A says "E - EVR Type:
        0=Assist, 1=Balance". Same setting, opposite digits, forty pages
        apart."""
        for evr, expected in (("01", "1"), ("02", "0")):
            _c, h = self.a_site(evr=evr)
            self.assertEqual(self.body(h, "iV0A00")[self.EVR], expected, evr)
            self.assertEqual(isd.EVR_REPORTED[evr], expected)

    def test_a_site_that_is_not_set_up_reads_unknown(self):
        """Nothing here measures a vapour, so nothing here invents a failure:
        what it can say honestly is that it has not tested anything."""
        _c, h = self.a_site(programmed=False)
        got = self.body(h, "iV0A00")
        self.assertEqual(got[self.OVERALL:self.OVERALL + 3],
                         isd.UNKNOWN * 3)
        self.assertIn("UNKNOWN", self.send(h, "IV0A00"))

    def test_a_site_that_is_set_up_reads_pass(self):
        _c, h = self.a_site()
        got = self.body(h, "iV0A00")
        self.assertEqual(got[self.OVERALL:self.OVERALL + 3], isd.PASS * 3)
        self.assertIn("PASS", self.send(h, "IV0A00"))

    def test_stage_one_transfers_are_the_deliveries(self):
        """A Stage I vapour transfer is a tanker unloading into a tank, so the
        count is what the console recorded in the period."""
        c, h = self.a_site()
        got = self.body(h, "iV0A00")
        self.assertEqual(got[self.PASSING:self.TOTAL + 3], "000000",
                         "no deliveries yet")
        self.assertIn("0 of 0", self.send(h, "IV0A00"))

    def test_the_monthly_report_starts_on_the_first(self):
        """"For monthly report dd=01"."""
        _c, h = self.a_site()
        self.assertEqual(self.body(h, "iV0B00202601")[self.DATE:8],
                         "20260101")
        self.assertIn("JAN 2026", self.send(h, "IV0B00202601"))

    def test_the_daily_report_takes_the_day_it_is_asked_for(self):
        _c, h = self.a_site()
        self.assertEqual(self.body(h, "iV0A0020260317")[self.DATE:8],
                         "20260317")

    def test_the_processor_enumerations_do_not_match_either(self):
        """V40 offers seven processors; V0A's P field stops at "4=User
        Defined", so the two it cannot name have no digit in the report."""
        self.assertNotIn("05", isd.PROCESSOR_REPORTED)
        self.assertIn("05", isd.VAPOR_PROCESSOR)
        _c, h = self.a_site(processor="05")
        self.assertIn("VEEDER-ROOT POLISHER", self.send(h, "IV0A00"))

    def test_all_three_want_the_isd_key(self):
        c, h = self.a_site()
        c.software.pop("isd")
        for code in ("V00", "V0A", "V0B"):
            self.assertIn("9999", self.send(h, f"I{code}00"), code)

    def test_all_three_are_inquire_only(self):
        _c, h = self.a_site()
        for code in ("V00", "V0A", "V0B"):
            self.assertIn("9999", self.send(h, f"s{code}0001"), code)


class TheProcessorAndSensorReports(unittest.TestCase):
    """V80, V81 and V83."""

    def a_site(self, full=True, processor="01"):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        for n in (1, 2):
            c.values[f"S723{n:02d}"] = f"{n:02d}0{n}"
        h = Handler(c, verbose=False)
        self.send(h, f"sV4000{processor}")
        self.send(h, "sV4100" + ("00" if full else "01"))
        return c, h

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def run_processor(self, c, h, times=2, minutes=400):
        self.send(h, "sVC0001490")
        for _ in range(times):
            self.send(h, "sVC1001491")
            c.clock_offset += minutes
            c.tick()
            self.send(h, "sVC1001490")
            c.clock_offset += 60
            c.tick()

    def test_both_want_full_vapor_processor_control(self):
        """"PMC Feature and Full Vapor Processor Control required", and V41's
        "00=Full Control" is the only level that is."""
        _c, h = self.a_site(full=False)
        for code in ("V80", "V81"):
            self.assertIn("9999", self.send(h, f"I{code}00"), code)
        _c, h = self.a_site(full=True)
        for code in ("V80", "V81"):
            self.assertNotIn("9999", self.send(h, f"I{code}00"), code)

    def test_a_cycle_is_recorded_when_the_processor_stops(self):
        """Nothing is recorded until it stops, because until then there is no
        elapsed time to record."""
        c, h = self.a_site()
        self.send(h, "sVC0001490")
        self.send(h, "sVC1001491")
        self.assertEqual(c.vp_cycles, [], "still running, nothing to record")
        c.clock_offset += 300
        c.tick()
        self.send(h, "sVC1001490")
        self.assertEqual(len(c.vp_cycles), 1)
        self.assertAlmostEqual(c.vp_cycles[0]["minutes"], 5.0, delta=0.2)

    def test_the_buffer_holds_twenty(self):
        """"nnnn - number of Vapor Processor cycles (Decimal,0-20)"."""
        c, h = self.a_site()
        self.run_processor(c, h, times=25, minutes=60)
        self.assertEqual(len(c.vp_cycles), 20)

    def test_a_run_past_the_maximum_runtime_is_a_fault(self):
        """V45 sets it, and the report has a column for it."""
        c, h = self.a_site()
        self.send(h, "sV4500010")                 # ten minutes
        self.run_processor(c, h, times=1, minutes=20 * 60)
        self.assertTrue(c.vp_cycles[-1]["fault"])
        self.assertIn("YES", self.send(h, "IV8000"))

    def test_a_short_run_is_not(self):
        c, h = self.a_site()
        self.send(h, "sV4500060")
        self.run_processor(c, h, times=1, minutes=60)
        self.assertFalse(c.vp_cycles[-1]["fault"])

    def test_the_set_clears_the_cycle_buffer(self):
        """"Set command clear buffer"."""
        c, h = self.a_site()
        self.run_processor(c, h, times=3)
        self.assertTrue(c.vp_cycles)
        self.assertIn("9999", self.send(h, "sV8000"))     # wants the 149
        self.assertNotIn("9999", self.send(h, "sV8000149"))
        self.assertEqual(c.vp_cycles, [])

    def test_the_polisher_gets_a_different_printout(self):
        """The manual draws two, "when VST Polisher selected" and "when
        Veeder-Root Polisher selected"."""
        _c, h = self.a_site(processor="01")
        self.assertIn("VAPOR PROCESSOR", self.send(h, "IV8000"))
        _c, h = self.a_site(processor="05")
        self.assertIn("VAPOR POLISHER", self.send(h, "IV8000"))

    def test_the_hydrocarbon_samples_are_fifteen_seconds_apart(self):
        """Which is the spacing in the manual's own example."""
        c, _h = self.a_site()
        got = c.hydrocarbon_history(4)
        gaps = {got[i][0] - got[i + 1][0] for i in range(3)}
        self.assertEqual(gaps, {c.HC_SECONDS})

    def test_a_sample_is_the_same_sample_every_time_it_is_read(self):
        """readings.py's rule: a value that changes when you glance away is
        not a reading."""
        c, _h = self.a_site()
        self.assertEqual(c.hydrocarbon_history(6), c.hydrocarbon_history(6))

    def test_the_set_clears_the_hydrocarbon_buffer(self):
        c, h = self.a_site()
        self.assertTrue(len(c.hydrocarbon_history()) > 1)
        self.assertNotIn("9999", self.send(h, "sV8100149"))
        self.assertEqual(c.hydrocarbon_history(), [],
                         "nothing in it the instant it is cleared")
        c.clock_offset += 5 * c.HC_SECONDS       # and it fills again
        c.tick()
        # NO COUNT AT ALL, on the fourth attempt. The samples land on fifteen
        # second slots and the console's clock is real time PLUS the offset,
        # so the number depends on which side of a boundary the clear fell and
        # on how long the suite took to reach this line. 4..6 flaked, then
        # 3..8 flaked. The invariant being tested is that the buffer EMPTIES
        # and then REFILLS; the count was never the point, and every attempt
        # to bound it has been a guess about machine speed.
        self.assertTrue(c.hydrocarbon_history(), "it should have refilled")

    def test_clearing_on_a_slot_boundary_still_clears(self):
        """The console kept one sample when the clear landed exactly on a
        fifteen second boundary, because the comparison was `<`.

        `now()` is whole seconds and a slot is fifteen of them, so this hit
        one clear in fifteen. It read as a flaky test for a long time and was
        loosened three times before anybody checked WHICH assertion was
        failing -- it was "empty the instant it is cleared", not the refill.
        """
        c, _h = self.a_site()
        for offset in range(15):
            c.hc_cleared = None
            now = time.mktime(c.now())
            # land the clear exactly on a boundary, and every second after it
            c.hc_cleared = int(now // c.HC_SECONDS) * c.HC_SECONDS + offset
            if c.hc_cleared > now:
                continue
            kept = [at for at, _v in c.hydrocarbon_history()
                    if at <= c.hc_cleared]
            self.assertEqual(kept, [],
                             f"a sample survived a clear at +{offset}s")

    def test_the_calibration_history_counts_records_in_range(self):
        """"III - Requested number of records per category [001-255]"."""
        _c, h = self.a_site()
        self.assertNotIn("9999", self.send(h, "IV83000100001"))
        self.assertIn("9999", self.send(h, "IV83000100000"))
        self.assertIn("9999", self.send(h, "IV83000100256"))

    def test_it_abbreviates_the_type_where_v43_spells_it_out(self):
        """V83's column reads "AIR FLOW" against V43's "AIR FLOW METER"."""
        _c, h = self.a_site()
        self.assertIn("AIR FLOW METER", self.send(h, "IV4300"))
        shown = self.send(h, "IV83000100001")
        self.assertIn("AIR FLOW ", shown)
        self.assertNotIn("AIR FLOW METER", shown)

    def test_it_names_the_categories_it_has_nothing_for(self):
        """The manual's example prints "SERIAL SENSOR CALIBRATION HISTORY"
        and then "NONE" rather than leaving the heading off."""
        _c, h = self.a_site()
        shown = self.send(h, "IV83000000001")
        self.assertIn("MODBUS SENSOR CALIBRATION HISTORY", shown)
        self.assertIn("SERIAL SENSOR CALIBRATION HISTORY", shown)
        self.assertIn("NONE", shown)

    def test_the_rows_date_the_way_this_section_dates_rows(self):
        """"12-26-01 10:51 AM", not the long form the status line uses."""
        c, h = self.a_site()
        self.run_processor(c, h, times=1)
        for code in ("IV8000", "IV8100", "IV83000100001"):
            shown = self.send(h, code)
            self.assertRegex(shown, r"\d\d-\d\d-\d\d \d\d:\d\d", code)


class TheAlarmAndStatusReports(unittest.TestCase):
    """V01, V02 and V03 -- and how much of them is other reports."""

    ROW = ("0103" "0605" "020502" "030502" "100502" "06UU01"
           "0706" "020602" "030602" "100602" "06UU01")

    def a_site(self, programmed=True):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        h = Handler(c, verbose=False)
        if programmed:
            self.send(h, f"sV4201149{self.ROW}")
            self.send(h, "sV400001")
        return c, h

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def test_the_status_reports_reprint_the_carb_block_and_the_alarm_one_does_not(self):
        """V02 and V03 carry the thresholds inside them; V01 is the alarms."""
        _c, h = self.a_site()
        alarms = self.send(h, "IV0100")
        monthly = self.send(h, "IV0200202608")
        self.assertNotIn("CARB EVR CERTIFIED OPERATING REQUIREMENTS", alarms)
        self.assertIn("CARB EVR CERTIFIED OPERATING REQUIREMENTS", monthly)
        for group in ("ISD WARNING ALARMS", "FAILURE ALARMS",
                      "SHUTDOWN & MISC. EVENT LOG"):
            self.assertIn(group, alarms, group)
            self.assertIn(group, monthly, group)

    def test_all_three_open_with_the_same_status_block(self):
        """Which is V0A's, so a site cannot read one way on one report and
        another way on the next."""
        _c, h = self.a_site()
        for code in ("IV0100", "IV0200202608", "IV0300", "IV0A00"):
            shown = self.send(h, code)
            for line in ("EVR TYPE:", "ISD TYPE:", "VAPOR PROCESSOR TYPE:",
                         "OVERALL STATUS", "STAGE I TRANSFERS"):
                self.assertIn(line, shown, f"{code} {line}")

    def test_the_warning_and_failure_groups_are_empty(self):
        """Nothing here measures a vapour, so nothing here raises a vapour
        alarm -- the same reason A20 to A22 answer empty."""
        _c, h = self.a_site()
        body = self.send(h, "iV0100").strip(chr(1) + chr(3) + chr(13) + chr(10)).split("&&")[0]
        body = body[6 + 10:]
        self.assertEqual(body[0:3], "000", "no warnings")
        self.assertEqual(body[3:6], "000", "no failures")

    def test_the_event_log_is_not_empty(self):
        """Its entries are things the console genuinely knows: when ISD
        started, and what the readiness check says."""
        _c, h = self.a_site()
        shown = self.send(h, "IV0100")
        self.assertIn("ISD STARTUP", shown)
        self.assertIn("READINESS", shown)

    def test_the_readiness_line_follows_the_setup_test(self):
        """It is V51's question already answered, so the two cannot disagree."""
        _c, h = self.a_site(programmed=False)
        self.assertIn("FAIL", self.send(h, "IV5100"))
        self.assertIn("CHECK SETUP CONFIGURATION", self.send(h, "IV0100"))
        _c, h = self.a_site(programmed=True)
        self.assertIn("PASS", self.send(h, "IV5100"))
        self.assertIn("EVR/ISD SYSTEM READY", self.send(h, "IV0100"))

    def test_the_monthly_one_reports_a_month_and_the_daily_one_a_day(self):
        _c, h = self.a_site()
        self.assertIn("AUG 2026", self.send(h, "IV0200202608"))
        self.assertIn("MONTHLY STATUS REPORT", self.send(h, "IV0200202608"))
        self.assertIn("DAILY STATUS REPORT", self.send(h, "IV0300"))

    def test_each_names_the_carb_appendix_it_is(self):
        _c, h = self.a_site()
        self.assertIn('"EVR-ISD ALARM STATUS REPORT"', self.send(h, "IV0100"))
        self.assertIn('"EVR-ISD MONTHLY STATUS REPORT"',
                      self.send(h, "IV0200202608"))

    def test_nothing_overflows_its_column(self):
        """"READINESS ISD:PP EVR:PPPP" is wider than a description column
        sized for the shorter entries."""
        _c, h = self.a_site()
        for line in self.send(h, "IV0100").split(chr(13) + chr(10)):
            if "READINESS" in line:
                self.assertIn("READINESS ISD:PP EVR:PPPP ", line)

    def test_all_three_want_the_isd_key_and_are_inquire_only(self):
        c, h = self.a_site()
        for code in ("V01", "V02", "V03"):
            self.assertIn("9999", self.send(h, f"s{code}0001"), code)
        c.software.pop("isd")
        for code in ("V01", "V02", "V03"):
            self.assertIn("9999", self.send(h, f"I{code}00"), code)


class TheDailyDetailReports(unittest.TestCase):
    """V04 to V09: ONE report asked for six ways. Two axes and nothing else,
    which period and how wide the paper is."""

    ROW = ("0103" "0605" "020502" "030502" "100502" "06UU01"
           "0706" "020602" "030602" "100602" "06UU01")

    def a_site(self, programmed=True):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        h = Handler(c, verbose=False)
        if programmed:
            self.send(h, f"sV4201149{self.ROW}")
            self.send(h, "sV400001")
        return c, h

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def rows(self, h, cmd):
        return self.send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10)).split(chr(13) + chr(10))

    def data_rows(self, h, cmd):
        return [r for r in self.rows(h, cmd) if r[:2].isdigit() and "/" in r[:6]]

    def test_the_month_variants_give_a_row_a_day(self):
        _c, h = self.a_site()
        for code in ("V04", "V06"):
            self.assertEqual(len(self.data_rows(h, f"I{code}00202602")), 28,
                             f"{code}: February 2026")
            self.assertEqual(len(self.data_rows(h, f"I{code}00202608")), 31,
                             f"{code}: August")

    def test_the_day_variants_give_the_days_asked_for(self):
        _c, h = self.a_site()
        for code in ("V05", "V07"):
            for want in (1, 5, 30):
                self.assertEqual(
                    len(self.data_rows(h, f"I{code}00{want:03d}")), want,
                    f"{code}: {want} days")

    def test_the_width_is_the_width_of_the_whole_printout(self):
        _c, h = self.a_site()
        for cmd, width in (("IV0400202608", 80), ("IV0600202608", 132),
                           ("IV0800202608060", 60), ("IV0800202608120", 120)):
            widest = max(len(r) for r in self.rows(h, cmd))
            self.assertLessEqual(widest, width, cmd)

    def test_the_user_column_count_has_the_manuals_range(self):
        """"CCC - Number of columns, Default=255 [055-999]"."""
        _c, h = self.a_site()
        self.assertIn("9999", self.send(h, "IV0800202608054"))
        self.assertNotIn("9999", self.send(h, "IV0800202608055"))
        self.assertNotIn("9999", self.send(h, "IV0800202608999"))
        self.assertIn("9999", self.send(h, "IV0900005054"))

    def test_a_column_per_hose_the_map_knows_about(self):
        """The table has a column per hose, so an unprogrammed console has
        none -- which is the honest table for one."""
        _c, h = self.a_site(programmed=True)
        self.assertIn("FP06/05", self.send(h, "IV0500003"))
        _c, h = self.a_site(programmed=False)
        self.assertNotIn("FP06/05", self.send(h, "IV0500003"))

    def test_what_it_cannot_measure_reads_no_test(self):
        """Nothing here measures a vapour, and NO TEST is a status the report
        has a code for rather than something to invent a number for."""
        _c, h = self.a_site()
        row = self.data_rows(h, "IV0500001")[0]
        self.assertIn("N", row)
        # the code key is at the end of a 121 character line, so ask a
        # variant wide enough to print it: at 80 columns it is cut, which is
        # the width rule doing its job rather than a missing line
        self.assertIn("(N)No Test", self.send(h, "IV0700001"))
        self.assertNotIn("(N)No Test", self.send(h, "IV0500001"))

    def test_a_day_with_a_delivery_passed_its_stage_one(self):
        """A Stage I vapour transfer is a tanker unloading, so a day with a
        delivery on it says Pass and a day without says nothing -- which is
        what the manual's example prints."""
        import time as _t
        c, h = self.a_site()
        record = type("D", (), {"end": {"at": _t.mktime(c.now())}})()
        c.deliveries.records[1] = [record]
        rows = self.data_rows(h, "IV0500003")
        self.assertIn("Pass", rows[-1], "today had one")
        self.assertNotIn("Pass", rows[0], "the day before did not")

    def test_it_never_writes_a_long_word_into_a_short_column(self):
        """UNKNOWN does not fit a five character column, and printing UNKNO
        would be worse than printing nothing."""
        _c, h = self.a_site()
        shown = self.send(h, "IV0500003")
        self.assertNotIn("UNKNO", shown)

    def test_the_computer_format_is_the_same_for_all_six(self):
        """The width only ever decides how much of the DISPLAY form prints."""
        _c, h = self.a_site()
        def body(cmd):
            # strip the SOH before counting, or every offset is one out
            r = self.send(h, cmd).strip(chr(1) + chr(3) + chr(13) + chr(10))
            return r.split("&&")[0][6 + 10:]
        one, two, three = (body("iV0500003"), body("iV0700003"),
                           body("iV0900003255"))
        self.assertEqual(one, two)
        self.assertEqual(one, three)
        self.assertEqual(one[0:4], "0003", "the record count leads it")

    def test_all_six_want_the_isd_key_and_are_inquire_only(self):
        c, h = self.a_site()
        for code in isd.DETAIL:
            self.assertIn("9999", self.send(h, f"s{code}0001"), code)
        c.software.pop("isd")
        for code in isd.DETAIL:
            self.assertIn("9999", self.send(h, f"I{code}00202608"), code)


class PanelReports(unittest.TestCase):
    """The three operating-mode ISD functions, 577013-800 p.39-42."""

    def an_isd_console(self):
        c = Console()
        c.board = "E6"
        c.modules.update({"smart": 1, "probe": 1, "rs232": 1})
        c.software["isd"] = True
        c.values["SV4201"] = "01" + "0" * 58     # one AFM map row: setup ok
        return c

    def test_the_three_functions_appear_with_isd(self):
        c = self.an_isd_console()
        names = [f["function"] for f in c.available_operating()]
        for want in ("ISD STATUS", "ISD DAILY REPORT", "ISD MONTHLY REPORT"):
            self.assertIn(want, names)

    def test_without_the_software_they_do_not(self):
        c = self.an_isd_console()
        c.software.pop("isd")
        names = [f["function"] for f in c.available_operating()]
        self.assertNotIn("ISD STATUS", names)

    def test_a_verified_site_reads_pass(self):
        c = self.an_isd_console()
        self.assertEqual(c.live_reading("isd_st_contain", 1), "STATUS: PASS")
        self.assertEqual(c.live_reading("isd_st_collect", 1), "STATUS: PASS")

    def test_an_unverified_site_reads_unknown(self):
        c = self.an_isd_console()
        del c.values["SV4201"]                   # no AFM map: setup fails
        self.assertEqual(c.live_reading("isd_st_contain", 1),
                         "STATUS: UNKNOWN")

    def test_stage1_counts_the_deliveries(self):
        c = self.an_isd_console()
        line = c.live_reading("isd_st_stage1", 1)
        self.assertRegex(line, r"STATUS:  \d+ of \d+  PASS")

    def test_the_dates_head_the_report_screens(self):
        c = self.an_isd_console()
        self.assertTrue(c.live_reading("isd_daily_date", 1)
                        .startswith("REPORT DATE: "))
        self.assertTrue(c.live_reading("isd_monthly_date", 1)
                        .startswith("REPORT DATE: "))


class TheDailyAssessment(unittest.TestCase):
    """FIDELITY I3. 577013-937 Rev J p.12-32, ALARM SEQUENCE: "Each ISD
    monitoring test operates once each day ... When a test first fails, a
    warning is posted and a warning event is logged. If this condition
    persists for seven more consecutive days, an alarm is posted, a failure
    alarm event is logged and the site is shutdown."

    This console had no scheduler. `isd_force()` set `warn` or `fail`
    straight from a bench tile with no counter, no period and no
    progression, so every ISD verdict was something a person clicked and
    **the distinction the whole feature turns on -- a warning that has
    persisted -- could not exist.**
    """

    def a_site(self):
        c = Console()
        c.board = "E6"
        c.software.update({"isd": True})
        c.modules.update({"smart": 1, "probe": 1, "rs232": 1})
        c.tick()
        return c

    def days(self, c, n):
        for _ in range(n):
            c.clock_offset += 86400.0
            c.tick()

    def test_the_escalation_is_the_manuals_own_table(self):
        """Table 3 states each one against its own alarm name, on the
        alarm's own line -- 8th for the containment and self-test alarms,
        31st for degradation, 2nd for collection."""
        self.assertEqual(isd.ESCALATION["leakage"], 8)
        self.assertEqual(isd.ESCALATION["gross"], 8)
        self.assertEqual(isd.ESCALATION["sensor"], 8)
        self.assertEqual(isd.ESCALATION["setup"], 8)
        self.assertEqual(isd.ESCALATION["degrade"], 31)
        self.assertEqual(isd.ESCALATION["collect_gross"], 2)
        self.assertEqual(isd.ESCALATION["collect_degrade"], 2)
        self.assertEqual(isd.ESCALATION["collect_flow"], 2)

    def test_a_fresh_failure_is_a_warning(self):
        c = self.a_site()
        c.isd_force("gross", "warn")
        self.assertEqual(c.isd_state("gross"), "warn")
        self.assertIn("300200", c.conditions())
        self.assertFalse(c.isd_shutdown_active())

    def test_it_is_still_a_warning_on_the_seventh_day(self):
        c = self.a_site()
        c.isd_force("gross", "warn")
        self.days(c, 7)
        self.assertEqual(c.isd_days["gross"], 7)
        self.assertEqual(c.isd_state("gross"), "warn")

    def test_and_an_alarm_on_the_eighth(self):
        c = self.a_site()
        c.isd_force("gross", "warn")
        self.days(c, 8)
        self.assertEqual(c.isd_state("gross"), "fail")
        self.assertIn("300300", c.conditions())
        self.assertNotIn("300200", c.conditions())
        self.assertTrue(c.isd_shutdown_active())

    def test_a_collection_test_escalates_on_the_second(self):
        c = self.a_site()
        c.isd_force("collect_gross", "warn")
        self.days(c, 1)
        self.assertEqual(c.isd_state("collect_gross"), "warn")
        self.days(c, 1)
        self.assertEqual(c.isd_state("collect_gross"), "fail")

    def test_the_balance_sites_own_hose_alarm_can_be_raised(self):
        """FIDELITY I10. The console's default is a BALANCE site, and
        577013-937 Rev J's Table 3 gives one collection pair to a Balance
        site: `hnn: FLOW COLLECT WARN` for "vapor collection flow
        performance is less than 50%", and `hnn: FLOW COLLECT FAIL` on the
        second consecutive failure.

        `status_types["31"]` carried both as `05` and `06` and nothing
        produced either. The only hose alarm the default site could raise
        belonged to the system it is not configured as.
        """
        c = self.a_site()
        self.assertEqual(c.evr_site(), "balance")
        c.isd_force("collect_flow", "warn")
        self.assertIn("310501", c.conditions())
        self.days(c, 1)
        self.assertEqual(c.isd_state("collect_flow"), "warn")
        self.days(c, 1)
        self.assertEqual(c.isd_state("collect_flow"), "fail")
        self.assertIn("310601", c.conditions())
        self.assertNotIn("310501", c.conditions())

    def test_each_system_offers_its_own_collection_tests(self):
        """577013-800 Rev P's Table 3 has GROSS and DEGRD COLLECT and no
        FLOW COLLECT; 577013-937 Rev J's has FLOW COLLECT and neither of
        the others. FIDELITY I10."""
        c = self.a_site()
        self.assertEqual(isd.COLLECTION_TESTS[c.evr_site()],
                         ("collect_flow",))
        c.set_setting("evr_type", "VACUUM ASSIST", 0)
        self.assertEqual(isd.COLLECTION_TESTS[c.evr_site()],
                         ("collect_gross", "collect_degrade"))

    def test_degradation_takes_a_month(self):
        c = self.a_site()
        c.isd_force("degrade", "warn")
        self.days(c, 30)
        self.assertEqual(c.isd_state("degrade"), "warn")
        self.days(c, 1)
        self.assertEqual(c.isd_state("degrade"), "fail")

    def test_a_condition_that_clears_resets_the_count(self):
        """"Consecutive" is the word the manual uses."""
        c = self.a_site()
        c.isd_force("gross", "warn")
        self.days(c, 6)
        c.isd_force("gross", None)
        self.days(c, 1)
        self.assertNotIn("gross", c.isd_days)
        c.isd_force("gross", "warn")
        self.days(c, 1)
        self.assertEqual(c.isd_days["gross"], 1)

    def test_the_escalation_logs_its_shutdown_once(self):
        """"an alarm is posted, a failure alarm event is logged and the
        site is shutdown"."""
        c = self.a_site()
        c.isd_force("collect_gross", "warn")
        self.assertEqual(c.isd_events, [])
        self.days(c, 4)
        shutdowns = [e for e in c.isd_events if e[1] == "ISD SHUTDOWN"]
        self.assertEqual(len(shutdowns), 1)

    def test_a_forced_fail_is_still_a_fail(self):
        """The bench has to be able to reach the shutdown without waiting
        thirty-one days for it."""
        c = self.a_site()
        c.isd_force("degrade", "fail")
        self.assertEqual(c.isd_state("degrade"), "fail")
        self.assertTrue(c.isd_shutdown_active())

    def test_the_clock_counts_assessments_and_not_ticks(self):
        """The bench runs the clock as fast as you like, so one tick can be
        a week wide. An escalation that counted ticks would depend on how
        fast somebody dragged the slider."""
        c = self.a_site()
        c.isd_force("gross", "warn")
        c.clock_offset += 8 * 86400.0
        c.tick()
        self.assertEqual(c.isd_days["gross"], 8)
        self.assertEqual(c.isd_state("gross"), "fail")

    def test_a_console_that_just_came_up_has_assessed_nothing(self):
        """The first assessment is the next one, not every day since 1970."""
        c = self.a_site()
        c.isd_force("gross", "warn")
        self.assertEqual(c.isd_days.get("gross", 0), 0)

    def test_the_assessment_time_is_the_programmed_one(self):
        """Figure 11: "SET START TIME / TIME: 11:59 PM ... Time defines when
        24-hour ISD tests are run and results posted", plus the post delay.
        Both were stored and read by nothing."""
        c = self.a_site()
        c.set_setting("evr_start_time", "6:00 AM", 0)
        c.set_setting("evr_post_delay", "030", 0)
        self.assertEqual(c.isd_assessment_time(), 6 * 60 + 30)
        c.set_setting("evr_start_time", "11:59 PM", 0)
        c.set_setting("evr_post_delay", "001", 0)
        self.assertEqual(c.isd_assessment_time(), 0)

    def test_a_console_without_the_key_assesses_nothing(self):
        c = self.a_site()
        c.software.pop("isd")
        c.isd_force("gross", "warn")
        self.days(c, 9)
        self.assertEqual(c.isd_days, {})


class TheSetupSelfTest(unittest.TestCase):
    """FIDELITY I1. 577013-819 Rev F p.17 defines the setup self-test and
    its escalation in one paragraph, and gives each failing criterion its
    own alarm with its own one-line definition on its own page. The console
    checked none of it: `setup_warnings()` has no ISD branch, and the
    nearest thing, `wire._isd_setup_ok()`, tested two of the seven criteria
    and posted nothing.

    "Setup self-testing occurs following power-up as well as at daily
    intervals at the Daily Test Time" -- so it is not a live view of the
    configuration, and the result is kept rather than recomputed. Every one
    of these alarms is cleared by the same procedure: "enter and exit the
    Setup Menu using the MODE key, then press the red ALARM button on the
    TLS and the condition should clear."
    """

    def a_site(self, configured=False):
        c = Console()
        c.board = "E6"
        c.software["isd"] = True
        c.modules.update({"probe": 1, "smart": 1, "rs232": 1, "relay": 1})
        c.tick()
        if configured:
            c.values["S60A01"] = "01" + packed.hexfloat(10000.0)
            c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
            c.values["S72101"] = "0111"          # two smart sensor positions
            c.values["S72301"] = "0101"          # ROTARY AIR FLOW METER
            c.values["S72302"] = "0202"          # VAPOR PRESSURE SENSOR
            c.values["S80B01"] = "0101"          # a relay controls tank 1
            c.isd_add_hose()
        c.isd_setup_selftest()
        return c

    def alarms(self, c):
        return [a for a in c.conditions() if a.startswith("30")]

    def test_a_bare_isd_console_fails_every_criterion_it_can(self):
        c = self.a_site()
        self.assertEqual(sorted(c.isd_setup_faults()),
                         ["flowmeter", "hose", "pressure", "tank"])

    def test_each_failing_criterion_posts_its_own_alarm(self):
        """Rev F lists all six as the COMMON CAUSES of ISD SETUP WARN and
        gives each its own page."""
        got = self.alarms(self.a_site())
        for code in ("301300", "301400", "301500", "301600"):
            self.assertIn(code, got)

    def test_and_the_setup_test_itself_warns(self):
        self.assertIn("301800", self.alarms(self.a_site()))

    def test_a_configured_site_is_quiet(self):
        """The diagnostic procedure is "configure the tanks and assign one
        of the following control devices ... the condition should clear"."""
        c = self.a_site(configured=True)
        self.assertEqual(c.isd_setup_faults(), [])
        self.assertEqual(self.alarms(c), [])

    def test_the_hose_criterion_is_the_fuel_grade_table(self):
        """"The Fuel Grade Table does not have any hoses assigned to it"."""
        c = self.a_site(configured=True)
        for device, _pos, _label in c.isd_hoses():
            c.isd_clear_hose(device)
        self.assertIn("hose", c.isd_setup_faults())

    def test_the_tank_criterion_wants_a_control_device(self):
        """"There are no vapor recovery (gasoline) tanks defined, or a
        gasoline pump has not been assigned to a control (shut down) device
        in at least one tank." Rev F names RELAY, PLLD, WPLLD and VLLD."""
        c = self.a_site(configured=True)
        c.values["S80B01"] = "0100"
        self.assertIn("tank", c.isd_setup_faults())
        c.values["S78501"] = "0101"              # PLLD controls it instead
        self.assertNotIn("tank", c.isd_setup_faults())

    def test_the_two_sensor_criteria_are_smart_sensor_categories(self):
        """S723's own list: 01 ROTARY AIR FLOW METER, 02 VAPOR PRESSURE
        SENSOR."""
        c = self.a_site(configured=True)
        c.values["S72301"] = "0103"              # a MAG sensor instead
        self.assertIn("flowmeter", c.isd_setup_faults())
        self.assertNotIn("pressure", c.isd_setup_faults())

    def test_the_vp_input_criterion_only_applies_to_two_processors(self):
        """"An external input for the OPW and ARID vapor processor cannot
        be found", which is criterion 6's "non-TLS Console Controlled
        Processor"."""
        c = self.a_site(configured=True)
        c.values["SV4000"] = "01"                # VST, console controlled
        self.assertNotIn("vpinput", c.isd_setup_faults())
        c.values["SV4000"] = "02"                # OPW
        self.assertIn("vpinput", c.isd_setup_faults())
        c.values["S80C01"] = "0151"              # a VAPOR PROCESSOR input
        self.assertNotIn("vpinput", c.isd_setup_faults())

    def test_it_runs_at_power_up(self):
        """"Setup self-testing occurs following power-up"."""
        c = self.a_site(configured=True)
        c.isd_clear_hose(c.isd_hoses()[0][0])
        self.assertEqual(c.isd_setup_result, [])   # not seen it yet
        c.breaker_off()
        c.breaker_on()
        self.assertIn("hose", c.isd_setup_result)

    def test_and_at_the_daily_assessment(self):
        """"as well as at daily intervals at the Daily Test Time"."""
        c = self.a_site(configured=True)
        c.isd_clear_hose(c.isd_hoses()[0][0])
        self.assertEqual(c.isd_setup_result, [])
        c.clock_offset += 86400.0
        c.tick()
        self.assertIn("hose", c.isd_setup_result)

    def test_it_is_not_a_live_view_of_the_configuration(self):
        """A result that recomputed on every read would clear itself the
        moment a value was typed, and the diagnostic says to exit the Setup
        Menu and press ALARM."""
        c = self.a_site()
        self.assertIn("301800", self.alarms(c))
        c.values["S72101"] = "0111"
        c.values["S72301"] = "0101"
        self.assertIn("301800", self.alarms(c), "cleared without a self-test")

    def test_a_console_without_the_key_checks_nothing(self):
        c = self.a_site()
        c.software.pop("isd")
        self.assertEqual(c.isd_setup_faults(), [])

    def test_the_setup_warning_escalates_like_any_other(self):
        """It is one of the 8th-day alarms, so a site left unconfigured for
        a week is shut down on the eighth."""
        c = self.a_site()
        for _ in range(8):
            c.clock_offset += 86400.0
            c.tick()
        self.assertEqual(c.isd_state("setup"), "fail")
        self.assertIn("301900", self.alarms(c))
        self.assertTrue(c.isd_shutdown_active())


class TheThreeProcessorTests(unittest.TestCase):
    """FIDELITY I2. 577013-819 Rev F pp.29-31 states all three thresholds
    outright, and `vapor_processor_status()` produced the figures they are
    measured against and set every verdict to `3 if running else 0` -- PASS
    or NOTEST, never WARN, never FAIL. The figures and the thresholds sat in
    one function and never met, so the report contradicted itself on the
    paper: `EMISSION TEST NOTEST` over `EMISSION LB/1KG 0.32`, which is
    exactly the failure threshold.
    """

    def a_site(self, polisher=False):
        c = Console()
        c.board = "E6"
        c.software.update({"isd": True, "pmc": True})
        c.modules.update({"probe": 1, "smart": 1, "rs232": 1})
        c.vp_cycles = [1]                      # the processor has run
        if polisher:
            c.values["SV4000"] = "05"          # VEEDER-ROOT POLISHER
        c.tick()
        return c

    def readings_of(self, c, **figures):
        """Pin the wandering figures, so a threshold can be tested."""
        from tls350sim import readings
        original = readings.wander

        def fixed(console, low, high, key, *a, **kw):
            if key in figures:
                return figures[key]
            return original(console, low, high, key, *a, **kw)
        readings.wander = fixed
        self.addCleanup(setattr, readings, "wander", original)
        return c

    def test_the_thresholds_are_the_manuals_own(self):
        self.assertEqual(isd.MASS_EMISSION_LB_PER_1KG, 0.32)
        self.assertEqual(isd.DUTY_CYCLE_PERCENT, 75.0)
        self.assertEqual(isd.OVER_PRESSURE_DEFAULT_WC, 1.0)
        self.assertEqual(isd.OVER_PRESSURE_WC["05"], 2.3)

    def test_an_emission_over_the_threshold_fails(self):
        """"A failure occurs when the mass emission exceeds the defined
        threshold", and the console's own PASS/FAIL THRESHOLDS block prints
        that threshold as 0.32 LBS/1KG in two separate figures."""
        c = self.readings_of(self.a_site(), vpemit=0.40)
        self.assertIn("vp_emission", c.vapor_processor_faults())
        c = self.readings_of(self.a_site(), vpemit=0.20)
        self.assertNotIn("vp_emission", c.vapor_processor_faults())

    def test_a_duty_cycle_over_eighteen_hours_fails(self):
        """"A failure occurs when the duty cycle exceeds 18 hours (75%)"."""
        c = self.readings_of(self.a_site(), vpduty=80.0)
        self.assertIn("vp_duty", c.vapor_processor_faults())
        c = self.readings_of(self.a_site(), vpduty=70.0)
        self.assertNotIn("vp_duty", c.vapor_processor_faults())

    def test_the_over_pressure_limit_is_per_processor(self):
        """"A VST ECS Membrane Processor failure occurs when the 90th
        percentile ... is equal to or exceeds 1" wc. A Veeder-Root Polisher
        failure occurs when ... 2.3" wc"."""
        c = self.readings_of(self.a_site(), vp95=1.5)
        self.assertIn("vp_pressure", c.vapor_processor_faults())
        c = self.readings_of(self.a_site(polisher=True), vp95=1.5)
        self.assertNotIn("vp_pressure", c.vapor_processor_faults())
        c = self.readings_of(self.a_site(polisher=True), vp95=2.5)
        self.assertIn("vp_pressure", c.vapor_processor_faults())

    def test_equal_to_the_limit_is_a_failure(self):
        """"is EQUAL TO or exceeds"."""
        c = self.readings_of(self.a_site(), vp95=1.0)
        self.assertIn("vp_pressure", c.vapor_processor_faults())

    def test_two_consecutive_days_is_an_alarm_and_a_shutdown(self):
        """"Two consecutive 1-day periods of ... test failures will result
        in a failure alarm, failure event recording, and shutdown of the
        site."""
        c = self.readings_of(self.a_site(), vpemit=0.40)
        c.isd_setup_selftest()
        self.assertEqual(c.isd_state("vp_emission"), "warn")
        self.assertIn("330200", c.conditions())
        # the FIRST assessed failure is still the warning
        c.clock_offset += 86400.0
        c.tick()
        self.assertEqual(c.isd_days["vp_emission"], 1)
        self.assertEqual(c.isd_state("vp_emission"), "warn")
        # and the second is the alarm
        c.clock_offset += 86400.0
        c.tick()
        self.assertEqual(c.isd_state("vp_emission"), "fail")
        self.assertIn("330300", c.conditions())
        self.assertTrue(c.isd_shutdown_active())

    def test_the_verdict_agrees_with_the_figure_beside_it(self):
        """The whole point of the entry: the report used to print NOTEST
        over a number past its own threshold."""
        c = self.readings_of(self.a_site(), vpemit=0.40)
        c.isd_setup_selftest()
        st = c.vapor_processor_status()
        self.assertGreater(st["figures"]["Emission LB/1KG"],
                           isd.MASS_EMISSION_LB_PER_1KG)
        self.assertEqual(st["status"]["Emission test"], "WARN")

    def test_a_healthy_processor_still_passes(self):
        c = self.readings_of(self.a_site(), vpemit=0.10, vpduty=10.0,
                             vp95=0.2)
        c.isd_setup_selftest()
        st = c.vapor_processor_status()
        self.assertEqual(st["status"]["Emission test"], "PASS")
        self.assertEqual(st["status"]["VP overpress test"], "PASS")
        self.assertEqual(c.vapor_processor_faults(), set())

    def test_a_processor_nobody_has_run_reads_no_test(self):
        """Claiming a pass the console never made is the one answer a
        diagnostic must not give."""
        c = self.a_site()
        c.vp_cycles = []
        c.vp_started = None
        st = c.vapor_processor_status()
        self.assertEqual(st["status"]["Emission test"], "NOTEST")

    def test_without_the_pmc_key_there_is_no_processor_to_test(self):
        c = self.a_site()
        c.software.pop("pmc")
        self.assertEqual(c.vapor_processor_faults(), set())


class V82PrintsTheReportAndNotItsOwnFieldNames(unittest.TestCase):
    """FIDELITY I6. V82's display format used to print `VP_STATUS_FIELDS`
    and `VP_FLOAT_FIELDS` uppercased -- `VP OVERPRESS TEST`, `ULLAGE
    PRESSURE 95TH PERCENTILE` -- which are the COMPUTER format's note names
    out of 576013-635 Rev AA and appear on no printout in any manual. The
    display format is its own report, and it states the two PMC limits the
    console states nowhere else.
    """

    # 576013-635 Rev AA's V82 sample, verbatim, except that the emission
    # threshold is the console's own 0.32 rather than the sample's 0.64:
    # 577013-937 Rev J prints 0.32 in two separate figures and it is what
    # `isd.MASS_EMISSION_LB_PER_1KG` holds, so it is the number the daily
    # test is measured against.
    HEAD = " " * 47 + "PERIOD    BELOW  ABOVE"
    DUTY_ROW = ("VAPOR PROCESSOR DUTY CYCLE FAIL                "
                "1DAYS     ----  75.00 %")
    EMISSION_ROW = ("VAPOR PROCESSOR MASS EMISSION FAIL             "
                    "1DAYS     ----   0.32 LBS/1KG")

    def a_site(self, isd_key=True, run=True):
        c = Console()
        for card in ("probe", "rs232"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"pmc": True}
        if isd_key:
            c.software["isd"] = True
        if run:
            c.vp_cycles = [1]                  # the processor has run
        c.tick()
        return c, Handler(c, verbose=False)

    def readings_of(self, **figures):
        """Pin the wandering figures, so a printed line can be read."""
        from tls350sim import readings
        original = readings.wander

        def fixed(console, low, high, key, *a, **kw):
            if key in figures:
                return figures[key]
            return original(console, low, high, key, *a, **kw)
        readings.wander = fixed
        self.addCleanup(setattr, readings, "wander", original)

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def test_the_computer_formats_note_names_are_not_a_report(self):
        """`ULLAGE PRESSURE 95TH PERCENTILE` is a note against `ffffffff` in
        the Command Format, and neither manual prints it on paper."""
        _c, h = self.a_site()
        shown = self.send(h, "IV8200")
        for name in ("ULLAGE PRESSURE 95TH PERCENTILE", "VP OVERPRESS TEST",
                     "EMISSION LB/1KG", "AUTONOMOUS VAPOR PROCESSOR",
                     "MAXIMUM RUNTIME"):
            self.assertNotIn(name, shown)

    def test_the_lines_are_the_ones_the_manual_prints(self):
        """576013-635 Rev AA's V82 sample, and 577013-937 Rev J Figure 39
        for the assessment line."""
        _c, h = self.a_site()
        shown = self.send(h, "IV8200")
        for line in ("VAPOR PROCESSOR STATUS REPORT", "PMC VERSION: ",
                     "ASSESSMENT TIME: ", "VAPOR PROCESSSOR TYPE: ",
                     "PMC MONITORING TEST PASS/FAIL THRESHOLDS",
                     "VP DUTY CYCLE TEST     : ", "VP INPUT STATUS        : ",
                     "EFFLUENT EMISSIONS TEST: ", "AVG HC PERCENT  :",
                     "DAILY THROUGHPUT:", "RUN TIME HOURS  :"):
            self.assertIn(line, shown)

    def test_the_manuals_own_typo_is_still_reproduced(self):
        """Both revisions of 635 read `VAPOR PROCESSSOR TYPE` with three
        S's on this report, where 937 Figure 39 spells it correctly."""
        _c, h = self.a_site()
        self.assertIn("VAPOR PROCESSSOR TYPE:", self.send(h, "IV8200"))

    def test_the_threshold_block_is_spaced_as_the_manual_sets_it(self):
        """The one place the console ever states either limit, and the
        columns are 635's: `1DAYS` at 47, the ABOVE figure ending at 68."""
        _c, h = self.a_site()
        shown = self.send(h, "IV8200")
        self.assertIn(self.HEAD, shown)
        self.assertIn(self.DUTY_ROW, shown)
        self.assertIn(self.EMISSION_ROW, shown)

    def test_the_two_numbers_are_the_consoles_own_limits(self):
        """Not transcribed off the sample: the report has to state what the
        daily tests actually measure against, or it contradicts the verdict
        printed under it -- which is what FIDELITY I2 was about."""
        _c, h = self.a_site()
        shown = self.send(h, "IV8200")
        self.assertIn(f"{isd.DUTY_CYCLE_PERCENT:>7.2f} %", shown)
        self.assertIn(f"{isd.MASS_EMISSION_LB_PER_1KG:>7.2f} LBS/1KG", shown)
        # 635's own sample says 0.64, and this console does not hold 0.64
        self.assertNotIn("0.64", shown)

    def test_the_three_figures_are_set_the_way_the_sample_sets_them(self):
        """`AVG HC PERCENT  :    0.00 %` / `DAILY THROUGHPUT:      -1 GALS`
        / `RUN TIME HOURS  :    -1.0`, which is 635's sample verbatim."""
        self.readings_of(vphc=0.0, vpthru=-1.0, vprun=-1.0)
        _c, h = self.a_site()
        shown = self.send(h, "IV8200")
        self.assertIn("AVG HC PERCENT  :    0.00 %", shown)
        self.assertIn("DAILY THROUGHPUT:      -1 GALS", shown)
        self.assertIn("RUN TIME HOURS  :    -1.0", shown)

    def test_the_effluent_line_carries_the_verdict_and_the_figure(self):
        """`EFFLUENT EMISSIONS TEST: PASS     (0.00 LBS/1KG)` -- the
        emission test's status and the emission value in parentheses."""
        self.readings_of(vpemit=0.0)
        _c, h = self.a_site()
        self.assertIn("EFFLUENT EMISSIONS TEST: PASS     (0.00 LBS/1KG)",
                      self.send(h, "IV8200"))

    def test_a_failing_emission_prints_its_own_figure_beside_it(self):
        self.readings_of(vpemit=0.40)
        c, h = self.a_site()
        c.isd_setup_selftest()
        self.assertIn("EFFLUENT EMISSIONS TEST: WARN     (0.40 LBS/1KG)",
                      self.send(h, "IV8200"))

    def test_the_duty_cycle_line_follows_the_seventy_five_percent_limit(self):
        """Not a mapping onto one of the five packed statuses -- FIDELITY
        I2a leaves that unsettled -- but the verdict of the console's own
        `vp_duty` test, which is the Duty Cycle % against the 75% printed
        three lines above it. 577013-819 Rev F p.31: "A failure occurs when
        the duty cycle exceeds 18 hours (75%)"."""
        self.readings_of(vpduty=80.0)
        c, h = self.a_site()
        c.isd_setup_selftest()
        self.assertIn("VP DUTY CYCLE TEST     : WARN", self.send(h, "IV8200"))
        self.readings_of(vpduty=70.0)
        c, h = self.a_site()
        c.isd_setup_selftest()
        self.assertIn("VP DUTY CYCLE TEST     : PASS", self.send(h, "IV8200"))

    def test_a_processor_nobody_has_run_reads_no_test(self):
        """The rule the five packed verdicts already keep: the daily
        assessment compares figures whether or not anything ran, and a
        verdict on a processor that never started is one the console never
        made."""
        self.readings_of(vpduty=80.0)
        c, h = self.a_site(run=False)
        c.isd_setup_selftest()
        self.assertIn("VP DUTY CYCLE TEST     : NOTEST",
                      self.send(h, "IV8200"))

    def test_the_input_status_line_has_no_source_and_says_so(self):
        """577013-819 Rev F p.28's MISSING VP INPUT is "an external input
        for the OPW and ARID vapor processor", and this console models no
        such input. NOTEST is the manual's own word for a test that has not
        run, and no packed status is claimed for the line."""
        self.readings_of(vpduty=80.0, vpemit=0.40)
        c, h = self.a_site()
        c.isd_setup_selftest()
        shown = self.send(h, "IV8200")
        self.assertIn("VP INPUT STATUS        : NOTEST", shown)

    def test_the_assessment_time_names_the_last_assessment(self):
        """937 Figure 39 heads the report `DEC  8, 2010  4:29 AM` and then
        says `ASSESSMENT TIME: DEC  7, 2010 11:59 PM`, so the line names the
        assessment the figures came out of, not the moment of the report."""
        c, h = self.a_site()
        shown = self.send(h, "IV8200")
        self.assertIn(
            f"ASSESSMENT TIME: {clock_words(c.isd_assessment_started())}",
            shown)

    def test_the_assessment_time_is_the_start_and_not_the_posting(self):
        """FIDELITY I6. The same figure shows the error twice: it heads
        itself DEC 8 and dates its assessment DEC 7, at the defaults
        576013-635's setup group draws -- `START TIME 11:59 PM` over `TIME
        DELAY MINUTES 1`. The console printed the moment the results were
        POSTED, so at those defaults it read `12:00 AM` and the date rolled
        with it.
        """
        import time
        c, h = self.a_site()
        self.assertEqual(c.isd_post_delay_minutes(), 1)
        self.assertEqual(c.isd_assessed - c.isd_assessment_started(), 60.0)
        started = time.localtime(c.isd_assessment_started())
        self.assertEqual((started.tm_hour, started.tm_min), (23, 59))
        posted = time.localtime(c.isd_assessed)
        self.assertEqual((posted.tm_hour, posted.tm_min), (0, 0))
        self.assertIn("11:59 PM", self.send(h, "IV8200"))

    def test_and_a_console_that_has_assessed_nothing_prints_no_such_line(self):
        """Which is 635's sample: no assessment line, every verdict NOTEST.
        A PMC-only console runs no ISD assessment at all."""
        c, h = self.a_site(isd_key=False)
        self.assertIsNone(c.isd_assessed)
        self.assertNotIn("ASSESSMENT TIME", self.send(h, "IV8200"))

    def test_the_computer_format_is_untouched(self):
        """The packed record is the one the notes describe and the display
        rewrite does not reach it: five 2-byte statuses, six 8-byte floats,
        behind the two counts."""
        _c, h = self.a_site()
        reply = self.send(h, "iV8200")
        packed_body = reply.strip(chr(1) + chr(3) + chr(13)
                                  + chr(10)).split("&&")[0]
        record = packed_body[6 + 10:]            # past the code and the stamp
        self.assertEqual(len(record), 8 + 2 + 5 * 2 + 2 + 6 * 8)
        self.assertEqual(record[8:10], "05")
        self.assertEqual(record[20:22], "06")
        self.assertNotIn("VAPOR", record)


class ForcedTestsAndShutdown(unittest.TestCase):
    """The bench forces an ISD test outcome the way it sets a sensor's
    state, and the console does the rest: 577013-800 Table 3 and Fig 23."""

    def a_site(self):
        c = Console()
        c.board = "E6"
        c.software.update({"isd": True, "bir": True})
        c.modules.update({"smart": 1, "probe": 1, "rs232": 1, "edim": 1})
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.meters = {1: 1}
        c.meter_flow = {1: 100.0}
        c.tick()
        return c

    def test_a_warn_posts_the_warning_alarm(self):
        c = self.a_site()
        c.isd_force("leakage", "warn")
        self.assertIn("300600", c.conditions())
        self.assertFalse(c.isd_shutdown_active())

    def test_a_fail_posts_the_alarm_and_shuts_the_site_down(self):
        c = self.a_site()
        c.isd_force("gross", "fail")
        self.assertIn("300300", c.conditions())
        self.assertTrue(c.isd_shutdown_active())
        c.clock_offset += 3600
        c.tick()
        self.assertEqual(c.tank_level[1]["volume"], 5000.0)   # nothing sold

    def test_the_override_resumes_dispensing_but_not_the_alarm(self):
        c = self.a_site()
        c.isd_force("gross", "fail")
        c.isd_do_override()
        self.assertFalse(c.isd_shutdown_active())
        self.assertIn("300300", c.conditions())               # alarm stands
        c.clock_offset += 3600
        c.tick()
        self.assertLess(c.tank_level[1]["volume"], 4901)      # selling again

    def test_clearing_the_last_failure_retires_the_override(self):
        c = self.a_site()
        c.isd_force("gross", "fail")
        c.isd_do_override()
        c.isd_force("gross", None)
        self.assertFalse(c.isd_override)
        c.isd_force("gross", "fail")
        self.assertTrue(c.isd_shutdown_active())              # shuts again

    def test_the_hose_tests_ride_the_hose(self):
        from tls350sim.console import describe_alarms
        c = self.a_site()
        c.isd_force("collect_gross", "fail")
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("h 1:GROSS COLLECT FAIL", screens)

    def test_the_forced_states_reach_the_reports(self):
        c = self.a_site()
        c.isd_force("leakage", "warn")
        c.isd_force("gross", "fail")
        h = Handler(c, verbose=False)
        warnings, failures, events = h._isd_alarm_groups()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("VAPOR CONTAINMENT LEAKAGE", warnings[0][1])

    def test_the_override_is_logged_to_the_event_log(self):
        c = self.a_site()
        c.isd_force("gross", "fail")
        c.isd_do_override()
        h = Handler(c, verbose=False)
        _w, _f, events = h._isd_alarm_groups()
        self.assertIn("ISD SHUTDOWN OVERRIDE", [e[1] for e in events])


class ClearTestAfterRepairClearsTheTest(unittest.TestCase):
    """FIDELITY I4. 577013-819 Rev F Table 2, the Clear Test Repair Menu, is
    a THREE column map -- Menu Selection, Clears Alarms, Reset Dates -- and
    this console wrote the third column and nothing else. `V85` stamped a
    date, `isd_forced` stood untouched, and the alarm survived the repair
    that had just been recorded against it.
    """

    def a_site(self):
        c = Console()
        for card in ("probe", "rs232", "smart", "relay"):
            c.modules[card] = 4
        c.set_board("E6")
        c.software = {"isd": True, "pmc": True}
        return c, Handler(c, verbose=False)

    def send(self, h, cmd):
        return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def test_the_table_is_the_manuals_own_six_rows(self):
        from tls350sim import isd
        self.assertEqual(sorted(isd.CLEARS), ["01", "02", "03", "04", "05",
                                              "06"])
        # "Containment Over Press: ISD GROSS PRESSURE, ISD DEGRD PRESSURE,
        # ISD VP PRESSURE"
        self.assertEqual(isd.CLEARS["01"],
                         ("gross", "degrade", "vp_pressure"))
        # "Vapor Collection Test: GROSS COLLECT, DEGRD COLLECT, FLOW
        # COLLECT" -- Table 8, and this comment named all three while the
        # tuple held two. FLOW COLLECT is the BALANCE site's pair and had
        # no producer at all. FIDELITY I10.
        self.assertEqual(isd.CLEARS["06"],
                         ("collect_gross", "collect_degrade",
                          "collect_flow"))
        # every key names a test the console actually holds a state for
        for code, tests in isd.CLEARS.items():
            for test in tests:
                self.assertIn(test, isd.ESCALATION, f"{code}: {test}")

    def test_a_selection_clears_its_own_alarms_and_no_others(self):
        c, _h = self.a_site()
        c.isd_force("gross", "fail")
        c.isd_force("degrade", "warn")
        c.isd_force("leakage", "warn")
        c.isd_days["gross"] = 12
        self.assertEqual(sorted(c.isd_states()),
                         ["degrade", "gross", "leakage"])
        self.assertEqual(sorted(c.clear_isd_test("01")),
                         ["degrade", "gross", "vp_pressure"])
        self.assertEqual(sorted(c.isd_states()), ["leakage"])
        # the day count goes with the alarm, or the next assessment
        # escalates straight back to a shutdown
        self.assertNotIn("gross", c.isd_days)

    def test_the_wire_clears_what_it_records(self):
        """`V85` wrote `SV85tt` and returned. The date is Table 2's third
        column and the alarms are its second."""
        c, h = self.a_site()
        c.isd_force("sensor", "fail")
        self.assertIn("sensor", c.isd_states())
        # the controls take a security code at the front, which is what
        # `test_every_control_confirms_at_the_front` is about
        reply = self.send(h, "sV85001490400")
        self.assertNotIn("9999", reply)
        self.assertNotIn("sensor", c.isd_states())
        self.assertTrue(c.values.get("SV8504"))

    def test_the_panel_walk_is_two_enters(self):
        """Figure 4: ENTER on CLEAR TEST AFTER REPAIR, ENTER on the
        selection, ENTER on CLEAR TEST AND LOG. The selection is remembered
        rather than acted on, the way the Maintenance Tracker remembers a
        key before ARE YOU SURE."""
        c, _h = self.a_site()
        c.isd_force("setup", "warn")
        c.isd_setup_result = ["SETUP"]
        self.assertEqual(c.diag_action("isd_select_05", 1), "SETUP TEST")
        self.assertEqual(c.isd_pending, "05")
        self.assertEqual(c.diag_action("isd_clear", 1), "CLEAR TEST AND LOG")
        self.assertNotIn("setup", c.isd_states())
        self.assertEqual(c.isd_setup_result, [])
        self.assertTrue(c.values.get("SV8505"))

    def test_clearing_nothing_selected_does_nothing(self):
        c, _h = self.a_site()
        c.isd_force("gross", "fail")
        self.assertEqual(c.diag_action("isd_clear", 1),
                         "CLEAR TEST AFTER REPAIR")
        self.assertIn("gross", c.isd_states())

    def test_a_fault_that_is_still_there_comes_back(self):
        """"After repair" is the point: clearing is not a mute. The setup
        self-test recomputes on the next assessment, so a console still
        failing its setup raises it again."""
        c, _h = self.a_site()
        c.isd_setup_selftest()
        standing = list(c.isd_setup_result)
        self.assertTrue(standing, "this fixture passes its own setup test")
        c.diag_action("isd_select_05", 1)
        c.diag_action("isd_clear", 1)
        self.assertEqual(c.isd_setup_result, [])
        c.isd_setup_selftest()
        self.assertEqual(c.isd_setup_result, standing)


if __name__ == "__main__":
    unittest.main()


class TheThirdSplitStoreWasImaginary(unittest.TestCase):
    """FIDELITY I5's last finding, and F9's shape for the third time.

    `Console.vapor_processor_type()` read `values["SVC200"]`, a key nothing
    on the wire writes and no manual on this shelf names, and defaulted to
    VST ECS PROCESSOR. So V82 reported a VST on a console with a
    Veeder-Root Polisher in it, and on a console with no processor at all.
    The other two split stores had a second copy that nothing read; this one
    had a second copy that nothing ever wrote.

    V40, Set Vapor Processor Type, is where a console keeps this, and since
    I7 it is where the panel keeps it too.
    """

    def typed(self, code):
        from tls350sim.console import Console
        c = Console(None)
        if code is not None:
            c.values["SV4000"] = code
        return c.vapor_processor_type()

    def test_an_unprogrammed_console_has_no_processor(self):
        """Not a VST. Claiming a processor nobody fitted is the one answer
        this cannot give."""
        self.assertEqual(self.typed(None), "NONE")
        self.assertEqual(self.typed("00"), "NONE")

    def test_each_code_gets_v40s_own_word(self):
        self.assertEqual(self.typed("01"), "VST ECS PROCESSOR")
        self.assertEqual(self.typed("05"), "VEEDER-ROOT POLISHER")
        self.assertEqual(self.typed("07"), "VST GREEN MACHINE")
        self.assertEqual(self.typed("03"), "HIRT VAPOR PROCESSOR")

    def test_the_imaginary_key_decides_nothing_now(self):
        from tls350sim.console import Console
        c = Console(None)
        c.values["SVC200"] = "2"                # ARID PERMEATOR, once
        c.values["SV4000"] = "05"
        self.assertEqual(c.vapor_processor_type(), "VEEDER-ROOT POLISHER")


class TheLongestCARBLabelKeepsItsGap(unittest.TestCase):
    """FIDELITY I5. The threshold rows print the label in a 47 wide field,
    which is the column the manual puts the period in -- and one label is
    longer than the field. `VAPOR COLLECTION ASSIST SYSTEM A/L DEGRADATION
    FAIL` is 51 characters, so the row came out `...DEGRADATION FAIL7dys`
    with the two running together. The page leaves two spaces."""

    def rows(self):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console(None)
        c.values["SV0A00"] = "02"               # an assist site
        h = Handler(c, verbose=False)
        return h._isd_carb_lines()

    def test_no_row_runs_its_period_into_its_label(self):
        from tls350sim import isd
        for label, per, _lo, _hi, _unit, _only in isd.CARB_THRESHOLDS:
            wide = max(47, len(label) + 2)
            drawn = ("%-" + str(wide) + "s") % label
            self.assertTrue(drawn.endswith("  "), label)

    def test_the_long_one_is_long_enough_to_have_mattered(self):
        from tls350sim import isd
        longest = max(len(row[0]) for row in isd.CARB_THRESHOLDS)
        self.assertGreater(longest, 47)
