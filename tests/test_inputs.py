"""The external inputs: a contact per programmed input, and the five things
the console makes of one. BENCH.md P1.

Category 05 had no producer: `INPUT_AA` was read by I401/I402/I403 and
written by nothing, so every input reported OFF forever and
`record_generator_run` had no caller.
"""
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import inputs, printer                        # noqa: E402
from tls350sim.console import Console                       # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


def a_site(kind="11", tanks=""):
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "io", "liquid"):
        c.modules[key] = 1
    c.software["csld"] = True
    for n in (1, 2):
        c.values[f"S60A{n:02d}"] = f"{n:02d}" + struct.pack(">f", 10000.0).hex().upper()
        c.tank_level[n] = {"volume": 5000.0, "water": 0.0}
    c.values["S80101"] = "011"                       # input 1 switched on
    c.values["S80201"] = "01GENERATOR 1"
    c.values["S80C01"] = "01" + kind + tanks
    c.tick()
    return c


class TheContact(unittest.TestCase):

    def test_a_configured_input_is_off_and_normal(self):
        c = a_site()
        self.assertEqual(c.inputs.configured(), [1])
        self.assertFalse(c.inputs.is_on(1))
        self.assertNotIn("050301", c.compute_alarms())

    def test_on_is_an_extern_input_alarm_and_off_clears_it(self):
        c = a_site()
        c.inputs.set(1, True)
        self.assertIn("050301", c.compute_alarms())
        c.inputs.set(1, False)
        # "It does not clear the alarm message from the display": the
        # cause is corrected, and the alarm waits for ALARM/TEST
        self.assertIn("050301", c.compute_alarms())
        c.acknowledge(True)
        self.assertNotIn("050301", c.compute_alarms())
        # and both edges are on the record I402 reads, newest first
        states = [r["state"] for r in c.alarm_log if r["aa"] == "05"]
        self.assertEqual(states, ["01", "02"])

    def test_i401_reads_the_contact(self):
        c = a_site()
        h = Handler(c, verbose=False)
        off = h.handle(("{}I40100{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"OFF", off)
        c.inputs.set(1, True)
        on = h.handle(("{}I40100{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"GENERATOR 1", on)
        self.assertIn(b"ON", on)

    def test_an_unconfigured_position_posts_nothing(self):
        c = a_site()
        c.inputs.set(2, True)
        self.assertNotIn("050302", c.compute_alarms())

    def test_no_io_module_no_inputs(self):
        c = a_site()
        c.modules["io"] = 0
        c.inputs.set(1, True)
        self.assertEqual(c.inputs.configured(), [])
        self.assertNotIn("050301", c.compute_alarms())

    def test_the_contact_word_follows_the_orientation(self):
        c = a_site("11")                                 # normally open
        self.assertEqual(c.inputs.contact(1), "OPEN")
        c.inputs.set(1, True)
        self.assertEqual(c.inputs.contact(1), "CLOSED")
        c.values["S80C01"] = "0112"                      # normally closed
        self.assertEqual(c.inputs.contact(1), "OPEN")
        c.inputs.set(1, False)
        self.assertEqual(c.inputs.contact(1), "CLOSED")

    def test_the_state_survives_a_reboot_and_not_a_reset(self):
        c = a_site()
        c.inputs.set(1, True)
        c.cold_boot()
        self.assertTrue(c.inputs.is_on(1))
        c.reset()
        self.assertFalse(c.inputs.is_on(1))


class TheGenerator(unittest.TestCase):
    """"The system runs a continuous leak test in the generator's tank(s)
    until the generator turns On. When the generator shuts Off, the system
    returns to its Leak Test mode. GENERATOR ON and GENERATOR OFF messages
    are printed whenever the generator turns on and off." 576013-623 p.23-2."""

    def test_the_tanks_are_under_test_while_it_is_off(self):
        c = a_site("21", "01")                           # generator, tank 1
        self.assertEqual(c.inputs.tanks_of(1), [1])
        c.tick()
        self.assertTrue(c.leaks.active("tank", 1))
        self.assertFalse(c.leaks.active("tank", 2))

    def test_all_tanks_when_none_were_entered(self):
        c = a_site("21")
        self.assertEqual(c.inputs.tanks_of(1), [1, 2])
        c.tick()
        self.assertTrue(c.leaks.active("tank", 1))
        self.assertTrue(c.leaks.active("tank", 2))

    def test_on_stops_the_test_and_off_logs_the_run(self):
        c = a_site("21", "01")
        c.tick()
        self.assertTrue(c.leaks.active("tank", 1))
        c.inputs.set(1, True)
        self.assertFalse(c.leaks.active("tank", 1))
        self.assertIn("050301", c.compute_alarms())
        c.clock_offset += 3600.0
        c.tank_level[1]["volume"] -= 40.0                # an hour's draw
        c.tick()
        self.assertFalse(c.leaks.active("tank", 1))      # not while it runs
        c.inputs.set(1, False)
        runs = c.generator_runs(1)
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0]["used"], 40.0)
        self.assertAlmostEqual(runs[0]["hours"], 1.0, places=2)
        c.tick()
        self.assertTrue(c.leaks.active("tank", 1))       # and back under test

    def test_both_edges_print(self):
        c = a_site("21", "01")
        c.inputs.set(1, True)
        c.inputs.set(1, False)
        printed = printer.automatic(c)
        lines = [line for line, _report in printed]
        self.assertIn("-- PRINT: generator on on input 1", lines)
        self.assertIn("-- PRINT: generator off on input 1", lines)
        slip = [r for line, r in printed if "generator on" in line][0]
        self.assertIn("GENERATOR ON", slip)
        self.assertIn("I 1:GENERATOR 1", slip)

    def test_i403_words_the_history_as_the_generator(self):
        c = a_site("21", "01")
        c.inputs.set(1, True)
        h = Handler(c, verbose=False)
        report = h.handle(("{}I40300{}".format(chr(1), chr(13))).encode())
        self.assertIn(b"GENERATOR ON", report)


class PumpSense(unittest.TestCase):

    def test_the_forecourt_is_busy_while_the_pump_runs(self):
        c = a_site("31", "01")
        self.assertTrue(c.pump_tank_has_sense(1))
        self.assertFalse(c.pump_tank_has_sense(2))
        self.assertFalse(c.csld.busy(1))
        c.inputs.set(1, True)
        self.assertTrue(c.csld.busy(1))
        self.assertFalse(c.csld.busy(2))
        self.assertTrue(c.inputs.pump_on(1))


class TheRemoteButton(unittest.TestCase):

    def test_a_press_is_alarm_test(self):
        c = a_site("41")
        c.values["S80102"] = "011"
        c.values["S80C02"] = "0211"
        c.inputs.set(2, True)                            # something to silence
        self.assertIn("050302", c.compute_alarms())
        self.assertFalse(c.silenced)
        said = c.inputs.press(1)
        self.assertIn("ALARM/TEST", said)
        self.assertTrue(c.silenced)
        self.assertFalse(c.inputs.is_on(1))              # momentary


class TheVapourProcessor(unittest.TestCase):

    def test_the_run_signal_is_a_processor_cycle(self):
        c = a_site("51")
        c.inputs.set(1, True)
        self.assertIsNotNone(c.vp_started)
        c.clock_offset += 600.0
        c.tick()
        c.inputs.set(1, False)
        self.assertIsNone(c.vp_started)
        self.assertTrue(c.vp_cycles)


if __name__ == "__main__":
    unittest.main()
