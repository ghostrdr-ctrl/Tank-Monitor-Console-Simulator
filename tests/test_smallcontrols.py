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
"""Five small controls against methods that already existed: the siphon
valve by hand, a line's thermal slope, a transducer fault, the vapour
processor's run switch, and LOW TEMP WARNING. BENCH.md T11, L7, L10, V6, T8."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import pressure                                # noqa: E402
from tls350sim.console import Console                        # noqa: E402


def a_pair():
    """Tanks 1 and 2 on a siphon bar with the valve fitted, and a line."""
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "plld"):
        c.modules[key] = 1
    for n in (1, 2):
        c.values[f"S60A{n:02d}"] = f"{n:02d}" + struct.pack(">f", 10000.0).hex().upper()
        c.tank_level[n] = {"volume": 5000.0 if n == 1 else 3000.0, "water": 0.0}
    c.values["S61201"] = "0102"
    c.values["S61202"] = "0201"
    c.values["S63201"] = "011"                       # the valve is fitted
    c.values["S78101"] = "011"
    c.values["S78501"] = "0101"
    c.tick()
    return c


class TheSiphonValve(unittest.TestCase):

    def test_shut_by_hand_the_set_stops_settling_and_the_warning_posts(self):
        c = a_pair()
        self.assertNotIn("022201", c.conditions())
        self.assertEqual(c.shut_siphon(1), [1, 2])
        self.assertIn("022201", c.conditions())
        self.assertIn("022202", c.conditions())
        before = dict((n, c.tank_level[n]["volume"]) for n in (1, 2))
        c.clock_offset += 3600.0
        c.tick()
        for n in (1, 2):
            self.assertAlmostEqual(c.tank_level[n]["volume"], before[n])
        c.shut_siphon(1, shut=False)
        self.assertNotIn("022201", c.conditions())
        c.clock_offset += 3600.0
        c.tick()
        self.assertLess(c.tank_level[1]["volume"], before[1])

    def test_no_valve_nothing_to_shut(self):
        c = a_pair()
        c.values["S63201"] = "010"
        c.shut_siphon(1)
        self.assertNotIn("022201", c.conditions())

    def test_the_valve_stays_shut_through_a_reboot(self):
        c = a_pair()
        c.shut_siphon(1)
        c.cold_boot()
        self.assertIn(1, c.siphon_shut)
        c.reset()
        self.assertEqual(c.siphon_shut, set())


class TheThermalSlope(unittest.TestCase):

    def test_a_slope_moves_the_pressure_and_decays(self):
        c = a_pair()
        ln = c.lines.line("plld", 1)
        c.lines.thermals("plld", 1, -2.0)
        self.assertEqual(ln.thermal, -2.0)
        was = ln.pressure
        c.clock_offset += 360.0
        c.tick()
        self.assertLess(ln.pressure, was)
        self.assertGreater(ln.thermal, -2.0)          # decaying
        c.clock_offset += 6 * 3600.0
        c.tick()
        self.assertEqual(ln.thermal, 0.0)


class TheTransducer(unittest.TestCase):

    def test_open_reads_negative_and_posts_the_open_alarm(self):
        c = a_pair()
        ln = c.lines.line("plld", 1)
        aa, nn = pressure.LINE_ALARMS["plld"]["open"]
        self.assertNotIn(aa + nn + "01", c.conditions())
        ln.transducer = "open"
        self.assertLess(ln.reading, 0.0)
        self.assertIn("-1.000 PSI", ln.screen()[0])
        self.assertIn(aa + nn + "01", c.conditions())
        ln.transducer = None
        self.assertNotIn(aa + nn + "01", c.conditions())

    def test_short_posts_the_short_alarm(self):
        c = a_pair()
        aa, nn = pressure.LINE_ALARMS["plld"]["short"]
        c.lines.line("plld", 1).transducer = "short"
        self.assertIn(aa + nn + "01", c.conditions())


class TheProcessorSwitch(unittest.TestCase):

    def test_a_run_is_a_cycle(self):
        c = a_pair()
        c.values["SV4000"] = "0001"                  # a VST ECS processor
        self.assertEqual(c.vapor_processor_type(), "VST ECS PROCESSOR")
        c.vapor_processor_on(True)
        self.assertIsNotNone(c.vp_started)
        c.clock_offset += 900.0
        c.tick()
        c.vapor_processor_on(False)
        self.assertIsNone(c.vp_started)
        self.assertEqual(len(c.vp_cycles), 1)


class LowTemperature(unittest.TestCase):
    """"Probe temperature drops below -4 F (-15.6 C)" ... "returns to normal
    operation after probe temperature rises above 0 F": two figures, which
    is a hysteresis."""

    def test_below_minus_four_and_back_over_zero(self):
        c = a_pair()
        self.assertNotIn("022701", c.conditions())
        c.hold_temperature(1, -10.0)
        self.assertIn("022701", c.conditions())
        self.assertNotIn("022702", c.conditions())
        c.hold_temperature(1, -2.0)                    # warmer, not warm
        self.assertIn("022701", c.conditions())
        c.hold_temperature(1, 1.0)
        self.assertNotIn("022701", c.conditions())

    def test_minus_three_never_trips_it(self):
        c = a_pair()
        c.hold_temperature(1, -3.0)
        self.assertNotIn("022701", c.conditions())


class ThePrinterLever(unittest.TestCase):
    """"PRINTER ERROR -- Printer feed roller release is open." FIDELITY N2b
    said the lever did not exist on the bench; it does now. BENCH.md P6."""

    def test_the_lever_down_is_printer_error(self):
        c = a_pair()
        self.assertNotIn("010200", c.conditions())
        c.printer_lever_open = True
        self.assertIn("010200", c.conditions())
        self.assertNotIn("010100", c.conditions())    # not out of paper
        c.printer_lever_open = False
        self.assertNotIn("010200", c.conditions())


class WplldNoise(unittest.TestCase):
    """"A WPLLD Comm Alarm is posted when the transmission is not received
    or when noise interferes with the reception." BENCH.md L11."""

    def a_wplld(self):
        c = a_pair()
        c.modules["wplld"] = 1
        c.values["S7A101"] = "011"
        c.values["S7A501"] = "0101"
        c.tick()
        return c

    def test_noise_posts_the_comm_alarm_on_a_wplld(self):
        c = self.a_wplld()
        self.assertNotIn("260701", c.conditions())
        c.lines.line("wplld", 1).noise = True
        self.assertIn("260701", c.conditions())
        c.lines.line("wplld", 1).noise = False
        self.assertNotIn("260701", c.conditions())

    def test_a_plld_has_no_such_alarm(self):
        c = self.a_wplld()
        c.lines.line("plld", 1).noise = True
        self.assertFalse([r for r in c.conditions() if r.startswith("2107")])
        self.assertNotIn("comm", pressure.LINE_ALARMS["plld"])


class TheCommErrorRecord(unittest.TestCase):
    """888 lists a port's last error and when; nothing here could suffer one
    by itself. BENCH.md P9."""

    def test_an_error_reaches_the_report_with_its_stamp(self):
        from tls350sim.wire import Handler
        c = a_pair()
        c.modules["rs232"] = 1
        port = sorted(c.comm_positions())[0]
        c.fault_comm(port, 4)                          # LOST CARRIER
        h = Handler(c, verbose=False)
        report = h.handle(("{}I888{:02d}{}".format(chr(1), port, chr(13))).encode())
        self.assertIn(b"LOST CARRIER", report)
        self.assertIn(b"TIME OF LAST COMM ERROR", report)
        c.clear_comm_errors(port)
        report = h.handle(("{}I888{:02d}{}".format(chr(1), port, chr(13))).encode())
        self.assertNotIn(b"LOST CARRIER", report)


class TheServiceVisit(unittest.TestCase):
    """116 and 11A are the record of what a contractor entered, and nothing
    wrote one. BENCH.md P10."""

    def test_a_logged_visit_is_on_both_histories(self):
        from tls350sim.wire import Handler
        c = a_pair()
        c.modules["rs232"] = 1
        self.assertEqual(c.service_log(), [])
        entry = c.log_service("0710", "A12345")
        self.assertEqual(entry["code"], "0710")
        self.assertEqual(entry["id"], "A12345")
        h = Handler(c, verbose=False)
        for code in ("I11600", "I11A00"):
            report = h.handle(("{}{}{}".format(chr(1), code, chr(13))).encode())
            self.assertIn(b"A12345", report)
            self.assertIn(b"0710", report)


if __name__ == "__main__":
    unittest.main()
