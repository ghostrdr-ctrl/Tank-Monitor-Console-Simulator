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
"""Diagnostic Mode against 576013-818 Rev AB chapter 6's own figures.

The diagnostic-mode audit (`audits/2026-09-10-diagnostic-mode.md`) filed
fifteen findings and three were closed; the rest sat in `FIDELITY.md`'s U16
index as names. These are the ones that need no window. The panel halves are
methods of `tests/test_panel.py`'s `Panel`. FIDELITY D21 onwards.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import alarmreports, presets, printer        # noqa: E402
from tls350sim.console import Console                       # noqa: E402


def a_site(name):
    c = Console()
    presets.load(c, name)
    return c


class EveryDiagnosticWalksTheSite(unittest.TestCase):
    """576013-818 Rev AB p.6-1: "Your system will display only the
    diagnostic functions of installed and configured modules and options."
    Four diagnostics walked what the site had programmed and eight walked
    every input the card has, so PRESSURE LINE RESULTS stopped at a fourth
    line and PRESSURE LINE LEAK DIAG went on to a sixth. FIDELITY D21."""

    def test_the_eight_are_walked_by_their_config_screen(self):
        from tls350sim.ui import SimApp
        want = {"2 WIRE CL DIAGNOSTIC": "741", "3 WIRE CL DIAGNOSTIC": "746",
                "SMART SENSOR DIAGNOSTIC": "721",
                "PUMP SENSOR DIAGNOSTIC": "771",
                "PUMP RELAY MONITOR DIAG": "7C4",
                "PRESSURE LINE LEAK DIAG": "781",
                "WPLLD LINE LEAK DIAG": "7A1", "LINE LEAK DIAG DATA": "751"}
        for name, code in want.items():
            self.assertEqual(SimApp.CONFIG_OF.get(name), code, name)


class TheTwoConfirmationsAreTheFiguresOwnLines(unittest.TestCase):
    """Figure 6-11 draws DELETE CSLD RECORDS: NO and its YES, Figure 6-2
    draws REINIT COMM BD: NO and its YES, and neither draws anything after.
    `0 RECORD(S) DELETED` and `COMM 1 RE-INITIALIZED` were on no page, and
    the first contradicted S054's own wording for the same action.
    FIDELITY D22."""

    def test_delete_csld_records(self):
        c = a_site("Compliance site, CSLD and sensors")
        self.assertEqual(c.diag_action("csld_delete", 1),
                         "DELETE CSLD RECORDS: YES")

    def test_reinitialise_a_comm_board(self):
        c = a_site("Truck stop, four tanks and BIR")
        self.assertEqual(c.diag_action("reinit_comm", 1),
                         "REINIT COMM BD: YES")


class TheCsldMonthlyScreens(unittest.TestCase):
    """576013-610 Rev AC p.27-3 and Figure 6-11. FIDELITY D23."""

    def test_the_hash_is_the_tank_the_panel_is_on(self):
        """`T #: REGULAR UNLEADED` reached the glass: `diag_line` read `T 1:`
        and `T X:` as a device number and not the figure's `T #:`."""
        c = a_site("Compliance site, CSLD and sensors")
        self.assertEqual(c.diag_line("T #: (Product Label)", 2),
                         f"T 2: {c.text('602', 2)}")

    def test_print_is_the_month_and_the_tank_the_screen_names(self):
        """"Press PRINT to print out the report for the tank shown." Both
        screens printed CSLD TEST RESULTS for every tank."""
        c = a_site("Compliance site, CSLD and sensors")
        out = printer.csld_monthly(c, 1, previous=True)
        self.assertIn("CSLD MONTHLY REPORT", out)
        self.assertIn("PREVIOUS MONTH", out)
        self.assertIn(f"T 1:{c.text('602', 1)}", out)
        self.assertFalse(any(line.startswith("T 2:") for line in out))
        self.assertNotIn("CSLD TEST RESULTS", out)

    def test_the_paper_and_the_wire_are_one_report(self):
        from tls350sim.wire import Handler
        c = a_site("Compliance site, CSLD and sensors")
        rows = Handler(c, verbose=False).csld_monthly_rows([1], False)
        self.assertEqual(printer.csld_monthly(c, 1)[-len(rows) + 1:],
                         rows[1:])


class TheServiceReport(unittest.TestCase):
    """576013-818 Rev AB Figure 6-3: "Press Print to printout a list of the
    25 most recent services codes entered. If none exist, there will be no
    printout." FIDELITY D24."""

    def an_entry(self, n=0):
        return {"at": "2609151130", "id": f"A{n:05d}", "code": "0101"}

    def test_nothing_entered_prints_nothing(self):
        self.assertIsNone(printer.service_history(Console()))

    def test_it_stops_at_twenty_five(self):
        c = Console()
        c.service_entries.extend(self.an_entry(n) for n in range(30))
        out = printer.service_history(c)
        self.assertEqual(sum(1 for line in out if line.endswith("0101")), 25)

    def test_the_rows_are_the_wire_s_rows(self):
        """One renderer for the log, whichever end asks for it."""
        c = Console()
        c.service_entries.append(self.an_entry())
        out = printer.service_history(c)
        self.assertEqual(out[-1], alarmreports.service_rows("11A",
                                                            c.service_log())[0])
        c.version = 26
        self.assertEqual(printer.service_history(c)[-1],
                         alarmreports.service_rows("116",
                                                   c.service_log())[0])


class ThePumpSensorScreenReadsItsInput(unittest.TestCase):
    """Figure 6-15: `S 1: TANK # NONE` over `PUMP OFF`, "NONE = No tank
    assigned, or (TANK LABEL) = Tank assigned". It was the caption, drawn
    verbatim for every input. FIDELITY D25."""

    def test_an_assigned_input_names_its_tank(self):
        c = a_site("Truck stop, four tanks and BIR")
        tank = c.pump_tank(2)
        self.assertTrue(tank)
        self.assertEqual(c.diag_value("pump_sense", 2),
                         f"S 2: TANK # {c.text('602', tank)}" + chr(10)
                         + "PUMP OFF")

    def test_the_pump_runs_when_its_tank_is_dispensed_from(self):
        c = a_site("Truck stop, four tanks and BIR")
        tank = c.pump_tank(1)
        meter = next(m for m, t in c.meters.items() if t == tank)
        c.meter_flow[meter] = 8.0
        self.assertTrue(c.diag_value("pump_sense", 1).endswith("PUMP ON"))

    def test_an_unassigned_input_is_the_figure(self):
        c = a_site("Truck stop, four tanks and BIR")
        c.values["S77203"] = "0300"
        self.assertEqual(c.diag_value("pump_sense", 3),
                         "S 3: TANK # NONE" + chr(10) + "PUMP OFF")


class ASmartSensorIsTheTypeItIsProgrammedAs(unittest.TestCase):
    """576013-818 Rev AB Figure 6-28: TANK/SENSOR goes from `s 1: (Label)`
    over `TYPE: MAG SENSOR` to the next sensor's `TYPE: VAC SENSOR`, "Go to
    Figure 6-29", or `TYPE: ATM P SENSOR`, "Go to Figure 6-31". The figures
    are alternatives picked by the sensor, and this console walked all three
    for every position under STEP -- one sensor a Mag, a Vac and an ATMP
    sensor, with two serial numbers and two install logs. The diagnostic
    audit's DG8 and DG9. FIDELITY D26."""

    def offered(self, kind):
        from tls350sim.console import DIAG_MENU
        c = Console()
        c.modules["smart"] = 1
        if kind is not None:
            c.values["S72301"] = "01" + kind
        fn = [f for f in DIAG_MENU
              if f["function"] == "SMART SENSOR DIAGNOSTIC"][0]
        return [sc for sc in fn["screens"] if c.visible(sc, 1)]

    def test_each_type_draws_its_own_figure_and_no_other(self):
        types = {"03": "TYPE: MAG SENSOR", "04": "TYPE: VAC SENSOR",
                 "05": "TYPE: ATMP SENSOR"}
        for kind, line in types.items():
            got = [sc["l2"] for sc in self.offered(kind)]
            self.assertEqual([l2 for l2 in got if l2.startswith("TYPE:")],
                             [line], kind)
            self.assertEqual(sum(1 for l2 in got if "SERIAL NUMBER" in l2),
                             1, kind)
            self.assertEqual(got.count("PRESS <PRINT>"), 1, kind)

    def test_a_sensor_nobody_typed_is_drawn_as_the_first_figure(self):
        got = [sc["l2"] for sc in self.offered(None)]
        self.assertIn("TYPE: MAG SENSOR", got)
        self.assertNotIn("TYPE: VAC SENSOR", got)

    def test_an_isd_sensor_draws_figure_45_alone(self):
        """577013-937 Rev J Figure 45 has no TYPE and no SERIAL screen."""
        got = [sc["l2"] for sc in self.offered("02")]
        self.assertFalse([l2 for l2 in got if l2.startswith("TYPE:")])
        self.assertIn("CONSTANTS PRESS <PRINT>", got)

    def test_the_top_level_is_the_figure_s_s_column(self):
        def top(kind):
            return [sc["l2"] for sc in self.offered(kind)
                    if not sc.get("depth")]
        self.assertEqual(top("03"), [
            "TYPE: MAG SENSOR", "SERIAL NUMBER: XXXXXXXX", "PRESS <ENTER>",
            "COMM DATA PRESS <PRINT>", "CONSTANTS PRESS <PRINT>",
            "CHNNL PRESS <PRINT>", "PRESS <PRINT>"])
        self.assertEqual(top("05"), [
            "TYPE: ATMP SENSOR", "SERIAL NUMBER XXXXXXXXX", "PRESS <ENTER>",
            "COMM DATA PRESS <PRINT>", "CONSTANTS PRESS <PRINT>",
            "CHANNELS PRESS <PRINT>", "PRESS <PRINT>"])
        self.assertEqual(top("04")[-4:], [
            "COMM DATA PRESS <PRINT>", "CONSTANTS PRESS <PRINT>",
            "CHANNELS PRESS <PRINT>", "PRESS <PRINT>"])

    def test_atm_pressure_is_behind_its_own_enter(self):
        screens = self.offered("05")
        at = [sc["l2"] for sc in screens].index("ATM PRESSURE: XX.XXX PSI")
        self.assertEqual(screens[at]["depth"], 1)
        self.assertEqual(screens[at - 1]["l1"], "ATM P SENSOR DIAGS")

    def test_constants_print_the_type_s_own_rows(self):
        c = Console()
        c.modules["smart"] = 1
        c.values["S72301"] = "0103"
        mag = "".join(printer.ss_constants_diag(c, 1))
        for word in ("MODEL", "LENGTH", "GRADIENT", "NUM FLOATS"):
            self.assertIn(word, mag)
        c.values["S72301"] = "0104"
        vac = printer.ss_constants_diag(c, 1)
        self.assertIn("VAC SENSOR", vac)
        self.assertTrue(any(line.startswith("SLOPE") for line in vac))
        c.values["S72301"] = "0102"
        isd = "".join(printer.ss_constants_diag(c, 1))
        self.assertNotIn("MODEL", isd)


class TheVacSensorsFourActionsAreNested(unittest.TestCase):
    """576013-818 Rev AB Figures 6-29 and 6-30: VAC SENSOR MANUAL TEST, E to
    START MANUAL TEST, E to SELECT VAC SENSOR, E to START MANUAL TEST: ALL,
    E to the acknowledgement -- and a C on SELECT VAC SENSOR "TO SELECT
    INDIVIDUAL VAC SENSORS". All eleven screens were one flat level, STEP
    walked past both START screens, and only ALL could be reached. The
    diagnostic audit's DG10. FIDELITY D28."""

    def screens(self):
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "SMART SENSOR DIAGNOSTIC"][0]
        return fn["screens"]

    def test_the_depths_are_the_figures(self):
        got = [(s["l1"], s.get("depth", 0)) for s in self.screens()
               if (s.get("when") or {}).get("is") == ["04"]][2:17]
        self.assertEqual(got, [
            ("VAC SENSOR DIAGS", 0), ("VAC SENSOR MANUAL TEST", 1),
            ("START MANUAL TEST", 2), ("SELECT VAC SENSOR", 3),
            ("START MANUAL TEST: ALL", 4), ("STOP MANUAL TEST", 2),
            ("SELECT VAC SENSOR", 3), ("STOP MANUAL TEST: ALL", 4),
            ("VAC SENSOR EVAC HOLD", 1), ("START EVAC HOLD", 2),
            ("SELECT VAC SENSOR", 3), ("START EVAC HOLD: ALL", 4),
            ("STOP EVAC HOLD", 2), ("SELECT VAC SENSOR", 3),
            ("STOP EVAC HOLD: ALL", 4)])

    def test_one_sensor_is_acted_on_alone(self):
        from tests.test_vacsensor import a_vac_site
        c = a_vac_site()
        c.values["S72302"] = "0204"
        self.assertEqual(c.diag_action("vac_test_start", 2, "single"),
                         "s 2: MANUAL TEST STARTED")
        self.assertIn(2, c.vac_running)
        self.assertNotIn(1, c.vac_running)
        self.assertEqual(c.diag_action("vac_hold_start", 3, "single"),
                         "NO VAC SENSORS")


class ArchiveDiagnosticHasNoIndexRowForAScreen(unittest.TestCase):
    """CLOSED U22 photographed `ARCHIVE DIAGNOSTIC` on the FUNCTION walk of
    a bare console, so the function is real. Its one screen was `ARCHIVE`
    over `PRESS <STEP> TO CONTINUE` -- Figure 6-1's index row, under the
    function screen the panel already draws, so the function showed its own
    name twice. No page and no photograph draws what is behind it; see
    UNKNOWNS A68. The diagnostic audit's DG7. FIDELITY D29."""

    def test_it_is_offered_and_draws_no_index_row(self):
        from tls350sim.console import DIAG_MENU
        c = Console()
        self.assertIn("ARCHIVE DIAGNOSTIC",
                      [f["function"] for f in c.available_diagnostics()])
        fn = [f for f in DIAG_MENU if f["function"] == "ARCHIVE DIAGNOSTIC"][0]
        self.assertEqual(fn["screens"], [])


class TheSmartSensorFiguresOwnTwoPrintouts(unittest.TestCase):
    """576013-818 Rev AB Figures 6-28, 6-29 and 6-32 put P beside MAG SENSOR
    DIAGS, TYPE: VAC SENSOR and ATM P SENSOR DIAGS, and all three sensor
    figures put one on SMART SENSOR INSTALL LOG, each with its paper drawn.
    Both printed the generic sensor status report. FIDELITY D30."""

    def site(self, kind):
        c = Console()
        c.modules["smart"] = 1
        c.values["S72301"] = "01" + kind
        return c

    def test_a_mag_sensor_prints_its_six_readings(self):
        c = self.site("03")
        out = printer.smart_diagnostic(c, 1)
        at = out.index("SMART SENSOR DIAGNOSTIC")
        self.assertEqual(out[at + 1], c.clock_stamp())
        self.assertEqual(out[at + 3], "MAG SENSOR")
        self.assertEqual([line[:14].rstrip() for line in out[at + 4:]],
                         ["TOTAL HT", "FUEL HT", "WATER HT", "INSTALL POS",
                          "FLUID TEMP", "BOARD TEMP"])
        self.assertTrue(out[at + 4].endswith(" IN."))
        self.assertTrue(out[-1].endswith(" F"))

    def test_the_paper_reads_what_the_screen_reads(self):
        c = self.site("03")
        c.sumps.pour(1, 6.5)
        water = c.diag_reading("ss_water_ht", 1).split()[2]
        row = next(line for line in printer.smart_diagnostic(c, 1)
                   if line.startswith("WATER HT"))
        self.assertEqual(row.split()[2], water)

    def test_an_atmp_sensor_prints_its_type_serial_and_pressure(self):
        c = self.site("05")
        out = printer.smart_diagnostic(c, 1)
        self.assertEqual(out[-3], "TYPE: ATM P SENSOR")
        self.assertEqual(out[-2], c.diag_reading("ss_serial9", 1))
        self.assertTrue(out[-1].startswith("ATM PRESSURE:"))

    def test_a_vac_sensor_prints_figure_6_29s_own_block(self):
        """The paper L18 was blocking: every line of it is a reading the
        console models, in the printer's own columns."""
        c = self.site("04")
        c.vac_leak[1] = 1.5
        c.start_vac_test(1)
        c.finish_vac_tests()
        out = printer.smart_diagnostic(c, 1)
        at = out.index("SMART SENSOR DIAGNOSTIC")
        self.assertEqual(out[at + 3], "VAC SENSOR")
        self.assertEqual(out[at + 4], c.diag_reading("ss_serial9", 1))
        self.assertEqual([out[at + 5], out[at + 7], out[at + 9]],
                         ["COMPENSATED PRESSURE:", "UNCOMPENSATED PRESSURE:",
                          "EVACUATION STATE:"])
        # the page indents the two pressures one and the state two
        self.assertTrue(out[at + 6].startswith(" -"), out[at + 6])
        self.assertTrue(out[at + 8].startswith(" -"), out[at + 8])
        self.assertEqual(out[at + 10], "  VACUUM OK")
        self.assertEqual(out[at + 11], "FLUID STATUS:  NORMAL")
        self.assertEqual(out[at + 12], "VCV: CLOSED")
        self.assertEqual(out[at + 14], "LEAK RATE:    1.500 GPH")
        self.assertEqual(out[at + 15], "TIME TO NO VAC:")
        self.assertRegex(out[at + 16], r"^\d+:\d\d   HHH:MM$")
        self.assertTrue(out[at + 18].startswith("EVAC RATIO:"), out[at + 18])
        self.assertEqual(out[at + 19:], ["SENSOR FAULTS:", " NONE"])

    def test_the_vac_paper_reads_the_sump_the_bench_is_driving(self):
        """The whole of L18: the paper and the glass are one sensor. This
        block came off `readings.wander` and reported a sump under vacuum
        while the bench was filling the one beside it."""
        c = self.site("04")
        c.vac_pressure[1] = -0.4
        c.start_evac_hold(1)
        out = printer.smart_diagnostic(c, 1)
        self.assertIn(" -0.400 PSI", out)
        self.assertIn("  EVACUATION HOLD", out)
        self.assertIn("VCV: OPEN", out)
        self.assertIn("-0.40 PSI VCV: OPEN", c.diag_value("vac_state", 1))

    def test_a_sensor_with_no_test_on_record_claims_no_measurement(self):
        """B38 carries a validity flag for each of the three, so a console
        that has taken none of them says so rather than printing numbers.
        The date line goes with the reading it stamps. UNKNOWNS A73."""
        out = printer.smart_diagnostic(self.site("04"), 1)
        self.assertIn("LEAK RATE:      --- GPH", out)
        self.assertIn("---:--   HHH:MM", out)
        self.assertIn("EVAC RATIO:--- @ ---PSI", out)
        self.assertEqual([line for line in out if "-20" in line], [])

    def test_every_line_of_it_fits_the_paper(self):
        c = self.site("04")
        c.vac_leak[1] = 1.5
        c.start_vac_test(1)
        c.finish_vac_tests()
        for line in printer.smart_diagnostic(c, 1):
            self.assertLessEqual(len(line), printer.WIDTH, repr(line))

    def test_the_install_log_names_the_sensor_and_its_serial(self):
        from tls350sim import wiresensors
        from tls350sim.clock import clock_words
        for kind, word in (("03", "MAG SENSOR"), ("04", "VAC SENSOR"),
                           ("05", "ATMP SENSOR")):
            c = self.site(kind)
            out = printer.smart_install_log(c, 1)
            at = out.index("SMART SENSOR INSTALL LOG")
            self.assertEqual(out[at + 1], "- - - - - - - - - - - -")
            self.assertEqual(out[at + 2],
                             clock_words(wiresensors.install_time(c, 1)))
            self.assertEqual(out[at + 3], f"s1 {word}")
            self.assertEqual(out[at + 4], "SERIAL NUMBER:  "
                             + str(wiresensors._smart_serial(c, 1)))


if __name__ == "__main__":
    unittest.main()
