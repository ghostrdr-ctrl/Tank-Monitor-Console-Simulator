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
"""Screens the audit against the manuals found missing or wrong.

Each of these was checked by hand against the manual named in its docstring
and then fixed; they are here so it stays fixed. The audit itself is in
AUDIT.md and needs the PDFs, which are not in this repository; these are the
part of it that can run.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import (Console, DIAG_MENU,           # noqa: E402
                               NORMAL_MENU)
from tls350sim.wire import Handler                          # noqa: E402


def screens(function):
    fn = [f for f in DIAG_MENU if f["function"] == function][0]
    return [(s.get("l1", ""), s.get("l2", "")) for s in fn["screens"]]


def steps(function):
    fn = [f for f in NORMAL_MENU if f["function"] == function][0]
    return [s["text"] for s in fn["steps"]]


class Diagnostics(unittest.TestCase):
    """Troubleshooting Guide 576013-818, chapter 6."""

    def test_alarm_history_has_every_device_the_figure_has(self):
        """Figure 6-23: seventeen, including g (BIR) and r (pump relay)."""
        got = [l1.split(" ")[0] for l1, _l2 in screens("ALARM HISTORY REPORT")]
        for letter in ("T", "L", "V", "I", "P", "G", "C", "H", "D", "M", "E",
                       "F", "Q", "W", "g", "r"):
            self.assertIn(letter, got, letter)
        self.assertIn(("SYSTEM ALARM HISTORY", "PRESS <PRINT> FOR REPORT"),
                      screens("ALARM HISTORY REPORT"))

    def test_the_line_leak_diagnostics_are_the_plld_manuals_own(self):
        """576013-818 defers these to 577013-344, whose Figures 19 and 20 draw
        them. The referral itself was never a display line."""
        plld = screens("PRESSURE LINE LEAK DIAG")
        self.assertIn(("Q 1: (PRODUCT LABEL)", "3.0 DIAG PRESS <PRINT>"), plld)
        self.assertIn(("Q 1: (PRODUCT LABEL)", "P OFFSET TEST <ENTER>"), plld)
        self.assertIn(("Q 1:PRESS OFFSET TEST", "DONE - OFFSET: +XX.X PSI"),
                      plld)
        wplld = screens("WPLLD LINE LEAK DIAG")
        self.assertIn(("W 1: PENDING    PUMP OFF", "TEST COMPLETE HANDLE OFF"),
                      wplld)
        self.assertIn(("W 1: LAST READ=X.XXX PSI", "TOTAL MESSAGE: X"), wplld)
        for function in ("PRESSURE LINE LEAK DIAG", "WPLLD LINE LEAK DIAG"):
            for l1, l2 in screens(function):
                self.assertNotIn("SEE PLLD", l1 + l2)
                self.assertNotIn("577013-", l1 + l2)

    def test_the_isd_functions_are_there_with_the_key(self):
        """577013-819 Figures 4 and 6; 577013-800 for the setup function."""
        isd = screens("ISD DIAGNOSTIC")
        self.assertIn(("CLEAR TEST AFTER REPAIR", "PRESS <ENTER>"), isd)
        self.assertIn(("VAPOR COLLECTION TEST", "PRESS <ENTER>"), isd)
        pmc = screens("PMC DIAGNOSTIC")
        # 937-J draws two variants, the ECS membrane and the V-R polisher,
        # under one function name; the panel shows the one whose processor
        # is selected. Both defaults read AUTOMATIC.
        self.assertIn(("VAPOR PROCESSOR MODE", "AUTOMATIC"), pmc)
        self.assertIn(("HYDROCARBON SENSOR", "HC SENSOR      XX.XXX%"), pmc)
        self.assertIn(("VEEDER-ROOT POLISHER", "LOAD:       XX.X%"), pmc)

    def test_csld_diagnostics_has_both_months(self):
        """Figure 6-11 has a current and a previous month branch."""
        got = screens("CSLD DIAGNOSTICS")
        self.assertIn(("CSLD MONTHLY REPORT", "SELECT: CURRENT MONTH"), got)
        self.assertIn(("CSLD MONTHLY REPORT", "SELECT: PREVIOUS MONTH"), got)
        self.assertIn(("T #: (Product Label)", "CUR CSLD MONTHLY <PRINT>"), got)
        self.assertIn(("T #: (Product Label)", "PRV CSLD MONTHLY <PRINT>"), got)

    def test_the_service_report_asks_for_an_id(self):
        """Figure 6-3: ENTER SERVICE ID, not ENTER SERVICE CODE."""
        got = [l1 for l1, _l2 in screens("SERVICE REPORT")]
        self.assertIn("ENTER SERVICE ID", got)
        self.assertIn("ENTER SERVICE ID LABEL", got)
        self.assertNotIn("ENTER SERVICE CODE", got)

    def test_accuchart_has_the_user_status_screen(self):
        """Figure 6-10."""
        self.assertIn("ACCU USR STATUS DISABLED",
                      [l2 for _l1, l2 in screens("ACCUCHART DIAGNOSTICS")])

    def test_communication_shows_an_auto_detected_modem(self):
        """Figure 6-27."""
        self.assertIn("c1: MODEM AUTO DETECTED",
                      [l1 for l1, _l2 in screens("COMMUNICATION DIAGNOSTIC")])

    def test_the_mag_sensor_branch_is_behind_its_enter(self):
        """Figure 6-28 descends from MAG SENSOR DIAGS into six readings and
        three printouts."""
        got = [l2 for _l1, l2 in screens("SMART SENSOR DIAGNOSTIC")]
        for line in ("TOTAL HT      XX.X IN.", "FUEL HT       XX.X IN.",
                     "WATER HT      XX.X IN.", "INSTALL POS   XX.X IN.",
                     "FLUID TEMP  XX.X DEG F", "BOARD TEMP  XX.X DEG F",
                     "COMM DATA PRESS <PRINT>", "CONSTANTS PRESS <PRINT>",
                     "CHNNL PRESS <PRINT>"):
            self.assertIn(line, got, line)

    def test_the_atmp_branch_is_behind_its_enter(self):
        """Figure 6-32."""
        got = [l2 for _l1, l2 in screens("SMART SENSOR DIAGNOSTIC")]
        self.assertIn("ATM PRESSURE: XX.XXX PSI", got)
        self.assertIn("CHANNELS PRESS <PRINT>", got)

    def test_no_annotation_was_scraped_in_as_a_screen(self):
        """Two lines of the manual's prose were sitting in the data as
        screens; nothing on a 24 character display is sentence case."""
        for fn in DIAG_MENU:
            for l1, _l2 in screens(fn["function"]):
                self.assertFalse(l1[:1].isupper() and l1[1:2].islower()
                                 and " " in l1 and l1.endswith(("s", "f", ".")),
                                 f"{fn['function']}: {l1!r}")


class Operating(unittest.TestCase):
    """Operator's Manual 576013-610, chapter 2 and chapter 8."""

    def test_last_shift_inventory_is_chapter_eights_five_screens(self):
        """METERED SALES and VARIANCE are Reconciliation Mode's, not this
        function's, chapter 8 has begin, end, adjustment, gross, close."""
        self.assertEqual(steps("LAST SHIFT INVENTORY"),
                         ["BEGINNING INVENTORY", "ENDING INVENTORY",
                          "ADJUSTMENT DELIVERY", "GROSS CHANGE",
                          "GROSS CURRENT SHIFT NOW (No/Yes)"])

    def test_an_inserted_delivery_can_be_given_a_bol(self):
        """p2-2: INSERT DLVY BY TANK, DATE, TIME, TICKET VOLUME, BOL."""
        got = steps("DELIVERY MAINTENANCE")
        self.assertEqual(got[-3:], ["ENTER DLVY TIME", "ENTER TICKET VOLUME",
                                    "BOL"])


if __name__ == "__main__":
    unittest.main()


class ARealToolCanBackItUpAndPutItBack(unittest.TestCase):
    """The round trip a Veeder-Root tool actually does.

    A tool dumps every setup function with Inquire, keeps the data field, and
    writes it back with Set. Both halves have to agree or a backup is not a
    backup: verified end to end against the real tool, and pinned here.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("ascii", "replace")

    def data_of(self, reply, code):
        """The data field, the way a tool takes it: past the code and stamp."""
        body = reply.strip(chr(1) + chr(3)).replace("\r\n", "\n")
        parts = [p for p in body.split("\n") if p]
        self.assertTrue(parts and parts[0] == code, reply)
        return parts[-1] if len(parts) > 1 else ""

    def test_every_stored_value_survives_a_dump_and_a_restore(self):
        c, h = self.a_site()
        before = dict(c.values)
        dumped = {}
        for code in sorted(before):
            token, device = code[1:4], code[4:6]
            reply = self.send(h, f"i{token}{device}")
            self.assertNotIn("9999", reply, code)
            field = reply.split(chr(1))[-1].split("&&")[0][6 + 10:]
            dumped[code] = field
        # now scramble it, the way a badly programmed console is scrambled
        for code in list(c.values):
            c.values[code] = "00"
        # ...and write the dump back, stripping the device prefix exactly as
        # a tool does
        for code, field in dumped.items():
            token, device = code[1:4], code[4:6]
            payload = field
            if c.is_prefixed(token) and payload[:2] == device:
                payload = payload[2:]
            reply = self.send(h, f"s{token}{device}{payload}")
            self.assertNotIn("9999", reply, code)
        self.assertEqual(c.values, before)

    def test_a_value_that_starts_with_its_own_device_number_survives(self):
        """S785 on line 1 is tank 01, so the stored value IS `0101`.

        Strip the prefix off that and you get `01`, which is what a bare
        value looks like too: the console cannot guess, so it always puts the
        prefix back and the two rules are exact inverses.
        """
        c, h = self.a_site()
        self.send(h, "s7850101")
        self.assertEqual(c.values["S78501"], "0101")

    def test_a_display_format_set_stores_what_a_computer_set_would(self):
        """"Display: <SOH>S60901c.cccccc" and "Computer: <SOH>s60901FFFFFFFF"
        are the same setting written two ways."""
        c, h = self.a_site()
        self.send(h, "S609010.000700")
        packed = c.values["S60901"]
        self.send(h, "s609023A378034")
        self.assertEqual(packed[2:], c.values["S60902"][2:])
        self.assertAlmostEqual(c.limit("609", 1), 0.0007, places=6)

    def test_a_display_format_set_refuses_a_value_out_of_range(self):
        c, h = self.a_site()
        self.assertIn("9999", self.send(h, "S609019.9"))

    def test_an_unknown_function_code_is_9999_and_never_silence(self):
        """A tool sweeps whole ranges, gaps included, and reads silence as a
        console that has fallen over: "12 consecutive timeouts" aborts it."""
        c, h = self.a_site()
        for token in ("5C0", "63E", "7CA", "8A1", "ZZZ"):
            reply = self.send(h, f"i{token}00")
            self.assertIn("9999", reply, token)


class InTankDiagnostics(unittest.TestCase):
    """Serial Interface Manual 576013-635, section 7.4.2.

    A01 is the first of the in-tank diagnostic reports and the one that says
    what the probe IS rather than what it is reading. Its computer format is
    "TTpPPKKKKFFFFFFFFSSSSSScccc" once per tank, and the point of these tests
    is that the two formats agree with each other and with the console.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def records(self, reply):
        """The repeated 27 character block, past the code and the stamp."""
        body = reply.strip(chr(1) + chr(3)).split("&&")[0]
        body = body[len("iA0100") + len("YYMMDDHHmm"):]
        self.assertEqual(len(body) % 27, 0, body)
        return [body[i:i + 27] for i in range(0, len(body), 27)]

    def test_the_computer_format_says_what_the_console_says(self):
        from tls350sim import packed
        c, h = self.a_site()
        got = self.records(self.send(h, "iA0100"))
        self.assertEqual(len(got), len(sorted(c.tank_level)))
        for rec in got:
            tank = int(rec[0:2])
            self.assertEqual(rec[2], (c.text("603", tank) or " ")[:1])
            self.assertEqual(rec[3:5], c.probe_type_code(tank))
            self.assertEqual(rec[5:9], c.probe_circuit_code(tank))
            self.assertAlmostEqual(packed.unhexfloat(rec[9:17]),
                                   c.probe_length(tank), places=3)
            self.assertEqual(rec[17:23], c.probe_serial(tank))
            self.assertEqual(rec[23:27], c.probe_date_code(tank))

    def test_the_display_format_prints_the_manuals_own_columns(self):
        """"TYPE CODE LENGTH SERIAL NO. D/CODE", and a line for each tank."""
        c, h = self.a_site()
        reply = self.send(h, "IA0100")
        for column in ("TYPE", "CODE", "LENGTH", "SERIAL NO.", "D/CODE"):
            self.assertIn(column, reply)
        for tank in sorted(c.tank_level):
            self.assertIn(c.probe_serial(tank), reply)
            self.assertIn(c.probe_date_code(tank), reply)

    def test_one_tank_answers_for_that_tank_alone(self):
        """"TT - Tank Number (Decimal, 00=all)"."""
        c, h = self.a_site()
        got = self.records(self.send(h, "iA0102"))
        self.assertEqual(len(got), 1)
        self.assertEqual(int(got[0][0:2]), 2)
        self.assertEqual(got[0][17:23], c.probe_serial(2))

    def test_a_console_with_no_probe_card_answers_9999(self):
        """An in-tank diagnostic reads a probe, so it wants the card that
        drives one."""
        c, h = self.a_site()
        c.modules["probe"] = 0
        self.assertIn("9999", self.send(h, "iA0100"))

    def test_a_probe_keeps_its_identity_between_looks(self):
        """A serial number that changes when you glance away is not a serial
        number: readings.py's rule, and A01 is where a tool would notice."""
        c, h = self.a_site()
        first = self.records(self.send(h, "iA0100"))
        c.tick()
        self.assertEqual(first, self.records(self.send(h, "iA0100")))


class TheProbeCalibrationReports(unittest.TestCase):
    """576013-635 section 7.4.2, function codes A02 to A07.

    A02 to A06 share one computer format, "TTpPPNNFFFFFFFF", and differ in
    which numbers they carry. A07 is a different shape and Mag probes only.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.values["S62F03"] = ""      # no float size: tank 3 reads as a CAP0
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def fields(self, reply, code):
        """TT p PP NN and the floats, the way a tool takes them apart."""
        from tls350sim import packed
        body = reply.strip(chr(1) + chr(3)).split("&&")[0][len(code) + 10:]
        count = int(body[5:7], 16)
        return (body[0:2], body[2], body[3:5],
                [packed.unhexfloat(body[7 + i * 8:15 + i * 8])
                 for i in range(count)])

    def test_a_mag_probe_answers_a02_with_its_gradient_and_nothing_else(self):
        """"MAG GRADIENT= 178.1400" is the whole of that tank's line."""
        c, h = self.a_site()
        _tt, _p, pp, values = self.fields(self.send(h, "iA0201"), "iA0201")
        self.assertEqual(pp, "03")
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values[0], c.probe_gradient(1), places=2)
        self.assertIn("GRADIENT=", self.send(h, "IA0201"))

    def test_a_cap_probe_answers_two_references_and_a_segment_each(self):
        """A CAP0's example is eight numbers: 97 and 180, then six."""
        c, h = self.a_site()
        _tt, _p, pp, values = self.fields(self.send(h, "iA0203"), "iA0203")
        self.assertEqual(pp, "01")
        self.assertEqual(len(values), 2 + Console.CAP_SEGMENTS["CAP0"])
        self.assertEqual([round(v, 3) for v in values],
                         [round(v, 3) for v in c.probe_calibration(3)])

    def test_the_wets_read_higher_than_the_drys(self):
        """Which is what wetting a capacitance segment does to it, and what
        makes the difference between them a sensitivity."""
        c, _h = self.a_site()
        dry = c.probe_calibration(3, wet=False)
        wet = c.probe_calibration(3, wet=True)
        for d, w in zip(dry, wet):
            self.assertGreater(w, d)

    def test_a_mag_probe_has_no_updated_calibration_and_no_ratios(self):
        """A04, A05 and A06 print "TANK 1 REGULAR UNLEADED MAG" and stop."""
        c, h = self.a_site()
        for code in ("A04", "A05", "A06"):
            _tt, _p, _pp, values = self.fields(self.send(h, f"i{code}01"),
                                               f"i{code}01")
            self.assertEqual(values, [], code)
            shown = self.send(h, f"I{code}01")
            self.assertIn("MAG", shown)
            for title in ("UPDATED", "SENSITIVITY"):
                self.assertNotIn(title, shown, code)

    def test_the_updated_values_are_the_factory_ones_with_drift_on_them(self):
        """The manual's updated drys are its factory drys and one of its
        updated wets differs by a few counts: a probe recalibrated once."""
        c, _h = self.a_site()
        factory = c.probe_calibration(3, wet=False, updated=False)
        updated = c.probe_calibration(3, wet=False, updated=True)
        self.assertEqual(len(factory), len(updated))
        self.assertNotEqual(factory, updated)
        for f, u in zip(factory, updated):
            self.assertLess(abs(u - f), 5.0)

    def test_the_ratios_start_at_zero_and_sit_about_one(self):
        """Every example in the manual starts 0.000, and a probe whose
        segments behave alike reads about 1.000 across them."""
        c, h = self.a_site()
        ratios = c.probe_ratios(3)
        self.assertEqual(ratios[0], 0.0)
        self.assertEqual(len(ratios), len(c.probe_calibration(3)))
        for r in ratios[2:]:
            self.assertTrue(0.8 < r < 1.2, r)
        self.assertIn("SENSITIVITY RATIOS", self.send(h, "IA0603"))

    def test_a07_is_a_mag_command_and_says_so_to_a_cap(self):
        """"Probe types 01=CAP0 and 02=CAP1 are not supported by this
        command"."""
        c, h = self.a_site()
        self.assertIn("9999", self.send(h, "iA0703"))
        self.assertNotIn("9999", self.send(h, "iA0701"))

    def test_a07_carries_two_dated_readings_of_the_same_distance(self):
        """The point of the screen is the pair: what it read going in against
        what it reads now."""
        from tls350sim import packed
        c, h = self.a_site()
        code = "iA0701"
        body = self.send(h, code).strip(chr(1) + chr(3)).split("&&")[0]
        body = body[len(code) + 10:]
        self.assertEqual(body[0:2], "01")
        self.assertEqual(body[3:5], "03")
        first, second = body[5:19], body[19:33]
        (d1, v1), (d2, v2) = c.probe_reference_distance(1)
        self.assertEqual(first[:6], d1)
        self.assertEqual(second[:6], d2)
        self.assertAlmostEqual(packed.unhexfloat(first[6:]), v1, places=2)
        self.assertAlmostEqual(packed.unhexfloat(second[6:]), v2, places=2)
        self.assertLess(abs(v2 - v1), 1.0, "a probe that has not moved")

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A02", "A03", "A04", "A05", "A06", "A07"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)


class ARestoreThatCarriesTheDevicePrefix(unittest.TestCase):
    """What a real console backup actually looks like coming back.

    Found against a dump off a live console. A tool that backs a console up
    stores what the INQUIRE gave it, and for a device-prefixed function that
    answer leads with the two digit device number. Writing it back verbatim
    therefore sends the prefix too -- and the Set path used to add a second
    one, so a tank came back labelled "01REGULAR" where the console says
    "REGULAR", and a line "03DIESEL  LINE" where it says "DIESEL  LINE".
    """

    def a_console(self):
        c = Console()
        for card in ("probe", "plld", "rs232", "liquid"):
            c.modules[card] = 4
        return c, Handler(c, verbose=False)

    def test_a_label_written_back_with_its_prefix_keeps_its_text(self):
        c, h = self.a_console()
        h.set_("602", "03", "03DIESEL              ", "s60203")
        self.assertEqual(c.text("602", 3), "DIESEL")

    def test_a_label_written_back_without_one_still_works(self):
        """The manual's own Set format has no prefix, "<SOH>S616TTf"."""
        c, h = self.a_console()
        h.set_("602", "03", "DIESEL              ", "s60203")
        self.assertEqual(c.text("602", 3), "DIESEL")

    def test_a_one_character_product_code_is_not_eaten(self):
        """maxlen 1, so "013" is two too many and "3" is exactly right."""
        c, h = self.a_console()
        h.set_("603", "01", "013", "s60301")
        self.assertEqual(c.text("603", 1), "3")
        h.set_("603", "02", "1", "s60302")
        self.assertEqual(c.text("603", 2), "1")

    def test_the_ambiguous_one_is_decided_by_length_not_by_content(self):
        """S785 holds a two digit tank number, so on line 1 both the prefix
        and the value can read "01". Four characters is prefix plus value;
        two is the value. That is the whole distinction and it is enough.
        """
        c, h = self.a_console()
        h.set_("785", "01", "0101", "s78501")     # prefix 01, tank 01
        h.set_("785", "02", "02", "s78502")       # no prefix, tank 02
        # both end up stored the one way the console reads them back
        self.assertEqual(c.values.get("S78501"), "0101")
        self.assertEqual(c.values.get("S78502"), "0202")

    def test_a_packed_float_is_left_alone(self):
        """Eight characters is a float and ten is a float with a prefix."""
        c, h = self.a_console()
        h.set_("789", "01", "01433E0000", "s78901")
        self.assertAlmostEqual(c.limit("789", 1), 190.0, places=3)
        h.set_("789", "02", "43160000", "s78902")
        self.assertAlmostEqual(c.limit("789", 2), 150.0, places=3)

    def test_it_still_round_trips_what_it_answers(self):
        """The property the prefix exists for: dump, restore, dump again."""
        c, h = self.a_console()
        h.set_("602", "01", "REGULAR             ", "s60201")
        first = h.handle((chr(1) + "i60201" + chr(13)).encode()).decode("latin-1")
        field = first.split(chr(1))[-1].split("&&")[0][6 + 10:]
        h.set_("602", "01", field, "s60201")
        again = h.handle((chr(1) + "i60201" + chr(13)).encode()).decode("latin-1")
        self.assertEqual(first.split("&&")[0][16:], again.split("&&")[0][16:])
        self.assertEqual(c.text("602", 1), "REGULAR")


class TheProbeSampleBuffers(unittest.TestCase):
    """576013-635 7.4.2, A10 to A13: the same channels through four windows.

    "TTpPPSSSSNNFFFFFFFF", where SSSS is the running sample number on A10 and
    A13 and the width of the average on A11 and A12.
    """

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        for _ in range(3):                # let the sample counter get going
            c.clock_offset += 3600.0
            c.tick()
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def fields(self, reply, code):
        """TT p PP SSSS NN and the floats."""
        from tls350sim import packed
        body = reply.strip(chr(1) + chr(3)).split("&&")[0][len(code) + 10:]
        window = int(body[5:9], 16)
        count = int(body[9:11], 16)
        return window, [packed.unhexfloat(body[11 + i * 8:19 + i * 8])
                        for i in range(count)]

    def test_a_mag_probe_answers_nineteen_channels(self):
        """Table 9-3's own list, and what probe_channel already models: the
        water float, ten reads of the product float, two references and six
        thermistors. A10's example prints exactly nineteen."""
        c, h = self.a_site()
        for code in ("A10", "A11", "A12", "A13"):
            _w, values = self.fields(self.send(h, f"i{code}01"), f"i{code}01")
            self.assertEqual(len(values), 19, code)

    def test_the_standard_average_is_the_number_the_guide_gave(self):
        """"Under normal operating conditions, this number should read 20",
        and the manual's example agrees: 20 on a Mag, 40 on a CAP."""
        c, h = self.a_site()
        window, _v = self.fields(self.send(h, "iA1201"), "iA1201")
        self.assertEqual(window, 20)
        c.values["S62F03"] = ""                    # tank 3 becomes a CAP0
        window, _v = self.fields(self.send(h, "iA1203"), "iA1203")
        self.assertEqual(window, 40)

    def test_the_fast_average_is_five(self):
        c, h = self.a_site()
        window, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        self.assertEqual(window, 5)

    def test_the_sample_number_runs_and_the_windows_do_not(self):
        """A10 and A13 report a counter; A11 and A12 report a width."""
        c, h = self.a_site()
        first, _v = self.fields(self.send(h, "iA1001"), "iA1001")
        fixed, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        c.clock_offset += 3600.0
        c.tick()
        later, _v = self.fields(self.send(h, "iA1001"), "iA1001")
        again, _v = self.fields(self.send(h, "iA1101"), "iA1101")
        self.assertGreater(later, first, "the sample number should run on")
        self.assertEqual(again, fixed, "the averaging width should not")

    def test_a_wider_average_is_a_quieter_reading(self):
        """Which is the whole difference between these four reports: the
        manual prints 8587.000, then 8587.200, then 8587.450 on one channel."""
        c, _h = self.a_site()
        one = c.probe_buffer(1, 1)
        five = c.probe_buffer(1, 5)
        twenty = c.probe_buffer(1, 20)
        settled = c.probe_buffer(1, 10 ** 6)
        for n in range(19):
            near = abs(twenty[n] - settled[n])
            far = abs(one[n] - settled[n])
            self.assertLessEqual(near, far + 1e-9, f"channel {n}")
            self.assertLessEqual(abs(five[n] - settled[n]) + 1e-9, far + 1e-9)

    def test_the_long_term_buffer_lags_only_what_moves(self):
        """A13's example reads 695.555 against a live 694 on the water float
        and 38259 against 38250 on a temperature reference -- all but the
        same -- while its product float reads 9687 against a live 8587. A long
        term average lags the level and matches what does not change.
        """
        c, _h = self.a_site()
        live = c.probe_buffer(1, 1)
        longterm = c.probe_buffer(1, c.probe_sample_number(1), longterm=True)
        self.assertAlmostEqual(longterm[0], live[0], delta=abs(live[0]) * 0.01)
        for n in (11, 12, 17, 18):
            self.assertAlmostEqual(longterm[n], live[n],
                                   delta=abs(live[n]) * 0.01)
        moved = [n for n in range(1, 11)
                 if abs(longterm[n] - live[n]) > abs(live[n]) * 0.02]
        self.assertEqual(len(moved), 10, "every product channel should lag")

    def test_the_display_format_heads_each_tank_with_its_count(self):
        c, h = self.a_site()
        shown = self.send(h, "IA1001")
        self.assertIn("NUMBER OF SAMPLES=", shown)
        self.assertIn("MAG", shown)

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A10", "A11", "A12", "A13"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)


class TheRestOfTheProbeBlock(unittest.TestCase):
    """576013-635 7.4.2, A14, A15 and A20 to A23 -- what closes the section."""

    def a_site(self):
        from tls350sim import presets
        c = Console()
        presets.load(c, "Truck stop, four tanks and BIR")
        c.tick()
        return c, Handler(c, verbose=False)

    def send(self, handler, command):
        return handler.handle(
            (chr(1) + command + chr(13)).encode()).decode("latin-1")

    def body(self, reply, code):
        return reply.strip(chr(1) + chr(3)).split("&&")[0][len(code) + 10:]

    # ---- A14 ----------------------------------------------------------------
    def test_a14_is_one_flag_wide(self):
        """"TTNNL": tank, the number of option flags, and the flag."""
        c, h = self.a_site()
        got = self.body(self.send(h, "iA1401"), "iA1401")
        self.assertEqual(got, "01010")          # tank 01, one flag, NO
        self.assertIn("MAG PROBE OPTIONS TABLE", self.send(h, "IA1401"))

    def test_a14_answers_no_because_nothing_says_otherwise(self):
        """A low temperature probe is a special order and nothing in the setup
        data names one. A14's own example answers NO on all four tanks."""
        c, _h = self.a_site()
        self.assertFalse(c.probe_low_temp(1))

    # ---- A20 to A22 ---------------------------------------------------------
    def test_a_healthy_probe_sets_no_leak_test_flags(self):
        """Every example in all three reports prints the heading and nothing
        after it."""
        c, h = self.a_site()
        for code in ("A20", "A21", "A22"):
            got = self.body(self.send(h, f"i{code}01"), f"i{code}01")
            self.assertEqual(got[5:7], "00", f"{code} should set no flags")

    def test_the_headings_follow_the_probe_type(self):
        """A20 heads a Mag with both rates and a CAP0 with 0.2 alone."""
        c, h = self.a_site()
        mag = self.send(h, "IA2001")
        self.assertIn("0.1 GAL/HR FLAGS:", mag)
        self.assertIn("0.2 GAL/HR FLAGS:", mag)
        c.values["S62F03"] = ""                  # tank 3 becomes a CAP0
        cap = self.send(h, "IA2003")
        self.assertIn("0.2 GAL/HR FLAGS:", cap)
        self.assertNotIn("0.1 GAL/HR FLAGS:", cap)

    def test_a22_is_one_set_of_flags_not_two(self):
        c, h = self.a_site()
        self.assertIn("GROSS LEAK TEST FLAGS:", self.send(h, "IA2201"))
        self.assertNotIn("GAL/HR FLAGS:", self.send(h, "IA2201"))

    # ---- A23 ----------------------------------------------------------------
    def test_the_averaging_buffers_are_the_history_this_console_keeps(self):
        """The console already keeps every finished test for the history
        reports, so A23 is that log split by rate and cut to the buffer."""
        c, h = self.a_site()
        for _ in range(3):
            for key in ("periodic", "annual"):
                c.leaks.start("tank", 1, key, hours=2.0)
                for _ in range(400):
                    c.clock_offset += 60.0
                    c.tick()
                    if not c.leaks.active("tank", 1):
                        break
        got = c.probe_leak_buffer(1, "periodic")
        self.assertEqual(len(got), 3)
        self.assertGreater(got[0].started, got[-1].started, "newest first")
        shown = self.send(h, "IA2301")
        self.assertIn("0.20 GAL/HR LEAK TEST BUFFER", shown)
        self.assertIn("0.10 GAL/HR LEAK TEST BUFFER", shown)
        self.assertIn("AVERAGE", shown)

    def test_the_buffer_holds_five_at_most(self):
        """The manual's example shows five rows under 0.20."""
        c, _h = self.a_site()
        for _ in range(7):
            c.leaks.start("tank", 1, "periodic", hours=2.0)
            for _ in range(400):
                c.clock_offset += 60.0
                c.tick()
                if not c.leaks.active("tank", 1):
                    break
        self.assertEqual(len(c.probe_leak_buffer(1, "periodic")), 5)

    # ---- A15 ----------------------------------------------------------------
    def test_a15_is_every_other_report_on_one_sheet(self):
        """Nothing new is modelled in it, so every figure on it has to be the
        same figure the report it came from answers."""
        c, h = self.a_site()
        shown = self.send(h, "IA1501")
        self.assertIn(c.probe_serial(1), shown)                   # A01
        self.assertIn(c.probe_date_code(1), shown)                # A01
        self.assertIn(c.probe_circuit_code(1), shown)             # A01
        # the gradient WANDERS on the console clock, so comparing the printed
        # figure to a freshly computed one races the tick: read it back out
        # and check it is the same reading rather than the same string
        printed = [l for l in shown.split(chr(13) + chr(10))
                   if l.startswith("GRADIENT=")][0]
        self.assertAlmostEqual(float(printed.split("=")[1]),
                               c.probe_gradient(1), delta=0.5)      # A02
        self.assertIn(f"NUM SAMPLES= {c.probe_window(1, 'standard')}", shown)
        for line in ("IN-TANK DIAGNOSTIC", "PROBE DIAGNOSTICS",
                     "TEMP SENSOR DATA", "REF DISTANCE",
                     "SAMPLES READ=", "LAST ERROR ="):
            self.assertIn(line, shown)

    def test_a15_prints_all_nineteen_channels_and_six_temperatures(self):
        c, h = self.a_site()
        shown = self.send(h, "IA1501")
        for n in range(19):
            self.assertIn(f"C{n:02d} ", shown, f"channel {n}")
        for n in range(1, 7):
            self.assertIn(f"T{n}: ", shown, f"temp {n}")

    def test_a15_spells_the_type_the_way_a15_spells_it(self):
        """"PROBE TYPE MAG 1" here, "MAG" in A01's column, "MAG7" in A07's
        heading. Three reports, three spellings, each followed where printed."""
        c, h = self.a_site()
        self.assertIn("PROBE TYPE MAG 1", self.send(h, "IA1501"))
        self.assertIn("MAG ", self.send(h, "IA0101"))

    def test_a15_computer_format_leads_with_the_probes_identity(self):
        from tls350sim import packed
        c, h = self.a_site()
        got = self.body(self.send(h, "iA1501"), "iA1501")
        self.assertEqual(got[0:2], "01")                    # TT
        self.assertEqual(got[2:6], "0003")                  # pppp, MAG1
        self.assertEqual(got[6:12], c.probe_serial(1))      # ssssss
        self.assertAlmostEqual(packed.unhexfloat(got[12:20]),
                               c.probe_length(1), places=3)
        self.assertEqual(got[20:24], c.probe_date_code(1))  # dddd

    def test_none_of_them_answer_without_a_probe_card(self):
        c, h = self.a_site()
        c.modules["probe"] = 0
        for code in ("A14", "A15", "A20", "A21", "A22", "A23"):
            self.assertIn("9999", self.send(h, f"i{code}01"), code)
