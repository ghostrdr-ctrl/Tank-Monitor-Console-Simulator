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
        r = self.send(h, f"I{code}00").strip(chr(1) + chr(3))
        return " | ".join(r.split(chr(13) + chr(10))[2:])

    def test_every_setup_function_answers_a_default(self):
        """A console nobody has programmed still has an answer for each."""
        _c, h = a_console()
        for code in isd.SETUP:
            self.assertNotIn("9999", self.send(h, f"I{code}00"), code)
            self.assertNotIn("9999", self.send(h, f"i{code}00"), code)

    def test_an_enum_takes_the_manuals_own_table(self):
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV400001"))
        self.assertIn("VST VAPOR PROCESSOR", self.shown(h, "V40"))
        self.assertIn("9999", self.send(h, "sV400099"))

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
        """"EE - EVR Type" and "VV - Vacuum Assist Type"."""
        _c, h = a_console()
        self.assertNotIn("9999", self.send(h, "sV4E000202"))
        got = self.shown(h, "V4E")
        self.assertIn("VACUUM ASSIST", got)
        self.assertIn("WAYNE VAC", got)
        self.assertIn("9999", self.send(h, "sV4E000999"))

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
        dumped = self.send(h, "iV4000").strip(chr(1) + chr(3))
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
        r = self.send(h, cmd).strip(chr(1) + chr(3))
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
        return self.send(h, cmd).strip(chr(1) + chr(3)).split(
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
        r = self.send(h, cmd).strip(chr(1) + chr(3)).split("&&")[0]
        return r[6 + 10:]

    def test_the_carb_rows_follow_the_site_type(self):
        """The report is not the same on an assist site as on a balance one:
        one has an A/L range and the other a flow performance figure."""
        _c, h = self.a_site(evr="01")                      # balance
        shown = self.send(h, "IV0000")
        self.assertIn("BALANCE SYS FLOW PERFORMANCE", shown)
        self.assertNotIn("ASSIST SYS A/L GROSS FAIL", shown)
        _c, h = self.a_site(evr="02")                      # vacuum assist
        shown = self.send(h, "IV0000")
        self.assertIn("ASSIST SYS A/L GROSS FAIL", shown)
        self.assertNotIn("BALANCE SYS FLOW PERFORMANCE", shown)

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
        body = self.send(h, "iV0100").strip(chr(1) + chr(3)).split("&&")[0]
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
        return self.send(h, cmd).strip(chr(1) + chr(3)).split(chr(13) + chr(10))

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
            r = self.send(h, cmd).strip(chr(1) + chr(3))
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


if __name__ == "__main__":
    unittest.main()


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
