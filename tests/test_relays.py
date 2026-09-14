"""The output relays follow what they are assigned to, and the pump relay
monitor can catch a welded contactor. BENCH.md P2 and P3.

`console.relays` held only what TEST OUTPUT RELAYS left behind, so a relay
assigned to an alarm never moved when the alarm posted.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import printer, relays                        # noqa: E402
from tls350sim.console import Console, describe_alarms       # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


def a_site(kind="1", orient="1", assign="05030101", tank=""):
    """One relay, and an external input whose alarm it can be assigned to."""
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "io", "relay", "plld", "pumpmon"):
        c.modules[key] = 1
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    c.values["S80101"] = "011"
    c.values["S80201"] = "01OVERFILL HORN"
    c.values["S80C01"] = "0111"
    c.values["S80601"] = "011"
    c.values["S80701"] = "01HORN"
    c.values["S80A01"] = "01" + kind
    c.values["S80901"] = "01" + orient
    # 808 holds a LIST now -- "You may assign more than one in-tank alarm,
    # sensor alarm, and external input to a relay" -- so the fixture writes
    # through the console's own setter rather than into `values`, which is
    # where nothing reads it any more. See CLOSED U48.
    c.assign_relay_alarm(1, assign[0:2], assign[2:4], assign[4:6],
                         assign[6:8] == "01")
    if tank:
        c.values["S80B01"] = "01" + tank
    c.tick()
    c.compute_alarms()
    return c


def i406(c):
    return Handler(c, verbose=False).handle(
        ("{}I40600{}".format(chr(1), chr(13))).encode())


class AStandardRelay(unittest.TestCase):

    def test_idle_until_its_alarm_posts(self):
        c = a_site()
        self.assertEqual(c.outputs.configured(), [1])
        self.assertFalse(c.outputs.energised(1))
        self.assertIn(b"OPEN", i406(c))
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))
        self.assertIn(b"CLOSED", i406(c))
        self.assertIn("HORN", "".join(printer.relays(c)))
        self.assertIn(" ON", [r for r in printer.relays(c) if "HORN" in r][0])

    def test_stays_in_until_the_alarm_leaves_the_display(self):
        c = a_site()
        c.inputs.set(1, True)
        c.compute_alarms()
        c.inputs.set(1, False)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))      # latched, not yet acked
        c.acknowledge(True)
        c.compute_alarms()
        self.assertFalse(c.outputs.energised(1))

    def test_all_devices_or_one(self):
        c = a_site(assign="05030002")                 # input 2's alarm only
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertFalse(c.outputs.energised(1))
        c = a_site(assign="05030001")                 # TT=00 is "all"
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))

    def test_a_relay_carries_more_than_one_alarm(self):
        """"You may assign more than one in-tank alarm, sensor alarm, and
        external input to a relay", 576013-623 Rev AN p.24-3, and 808's own
        reply carries `nn - number of alarms to follow`. The store held one
        -- a `digits` field eight characters wide -- so a second Set
        overwrote the first and ISD's MISSING RELAY SETUP could not be
        decided against it. FIDELITY I1a, CLOSED U48."""
        # a tank alarm FIRST and the input alarm second: under the old
        # store the second Set overwrote the first, so this relay would
        # have been assigned to the tank alarm alone
        c = a_site(assign="02050001")                 # low product, any tank
        c.assign_relay_alarm(1, "05", "03", "01")     # and input 1's alarm
        self.assertEqual(c.outputs.assignments(1),
                         [("02", "05", "00"), ("05", "03", "01")])
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))
        # and taking one off leaves the other
        c.assign_relay_alarm(1, "02", "05", "00", False)
        self.assertEqual(c.outputs.assignments(1), [("05", "03", "01")])
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))

    def test_the_report_carries_every_assignment_both_ways(self):
        """808's computer format is `RRnnAANNTTss...` with `nn - number of
        alarms to follow (Hex)`, and its display format puts them under one
        relay. `8BC` is the same command at a later version and had no
        field at all. CLOSED U48."""
        from tls350sim.wire import Handler
        c = a_site(assign="02020001")
        c.assign_relay_alarm(1, "02", "03", "01")
        h = Handler(c, verbose=False)
        shown = h.handle(chr(1).encode() + b"I80801").decode("latin-1")
        self.assertIn("RELAY SETUP REPORT", shown)
        self.assertIn("R 1:HORN", shown)
        self.assertIn("TYPE:   STANDARD", shown)
        self.assertIn("LEAK ALARM", shown)
        self.assertIn("HIGH WATER ALARM TANK 1", shown)
        # the count, then that many quadruples
        packed = h.handle(chr(1).encode() + b"i80801").decode("latin-1")
        self.assertIn("0102" + "02020001" + "02030101", packed)
        # and 8BC answers the same store
        self.assertIn("R 1:HORN",
                      h.handle(chr(1).encode() + b"I8BC01").decode("latin-1"))

    def test_a_console_with_no_relay_configured_reports_nothing_at_all(self):
        """Not an empty REPORT -- an empty BODY, the title included.
        `tests/console_capture/raw/I80800.bin` is a stamp and nothing else,
        and so are `I80600` and `I80700` beside it, where `I78700` on the
        same console prints a block for each of its three pressure lines.
        A console with nothing to report on this code says nothing."""
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console()
        c.set_module("relay", 1)                      # the card, unconfigured
        out = Handler(c, verbose=False).handle(chr(1).encode() + b"I80800")
        self.assertNotIn(b"9999", out)
        self.assertNotIn(b"RELAY SETUP REPORT", out)

    def test_a_cleared_assignment_drives_nothing(self):
        c = a_site(assign="05030100")                 # ss=00 is "clear"
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertFalse(c.outputs.energised(1))

    def test_normally_closed_reads_the_other_way_round(self):
        c = a_site(orient="2")
        self.assertFalse(c.outputs.energised(1))
        self.assertTrue(c.outputs.closed(1))
        self.assertIn(b"CLOSED", i406(c))
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))
        self.assertFalse(c.outputs.closed(1))

    def test_the_relay_test_holds_it_on(self):
        c = a_site()
        c.relays[1] = True
        self.assertTrue(c.outputs.held(1))
        self.assertTrue(c.outputs.energised(1))
        c.relays[1] = False
        self.assertFalse(c.outputs.energised(1))


class AMomentaryRelay(unittest.TestCase):

    def test_drops_when_the_alarm_is_acknowledged(self):
        c = a_site(kind=relays.MOMENTARY)
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))
        c.acknowledge(True)                            # cause still there
        c.compute_alarms()
        self.assertIn("050301", c.compute_alarms())
        self.assertFalse(c.outputs.energised(1))

    def test_acknowledging_it_lets_the_pump_go_too(self):
        """576013-623 Rev AN p.24-2: "relay returns to the inactive state
        after the ALARM/TEST key is pressed to acknowledge the alarm" -- and
        a relay that has returned to the inactive state is not holding a
        pump off.

        The coil got this right and the CONTACTOR did not: `pump_cut` asked
        the unfiltered question, so acknowledging dropped the coil and left
        the line dead, reading DISABLE ALARM, until the underlying condition
        cleared. One predicate now answers both.
        """
        c = a_site(kind=relays.MOMENTARY, tank="01")
        c.values["S78101"] = "011"
        c.values["S78201"] = "01UNLEADED"
        c.values["S78501"] = "0101"                   # line 1 feeds tank 1
        c.tick()
        c.inputs.set(1, True)
        c.compute_alarms()
        self.assertTrue(c.outputs.energised(1))
        self.assertTrue(c.outputs.pump_cut(1))
        self.assertTrue(c.lines.disabled("plld", 1))
        c.acknowledge(True)                            # cause still there
        c.compute_alarms()
        self.assertFalse(c.outputs.energised(1))
        self.assertFalse(c.outputs.pump_cut(1))
        self.assertEqual(c.outputs.cutting(), [])
        self.assertFalse(c.lines.disabled("plld", 1))


class APumpControlRelay(unittest.TestCase):

    def a_line_site(self):
        c = a_site(kind=relays.PUMP_CONTROL, assign="00000000", tank="01")
        c.values["S78101"] = "011"
        c.values["S78201"] = "01UNLEADED"
        c.values["S78501"] = "0101"                   # line 1 feeds tank 1
        c.tick()
        return c

    def test_follows_the_pump_request(self):
        c = self.a_line_site()
        self.assertFalse(c.outputs.energised(1))
        c.lines.handle("plld", 1, True)
        self.assertTrue(c.outputs.energised(1))
        c.lines.handle("plld", 1, False)
        c.lines.stop_all("plld")
        c.tick()
        self.assertFalse(c.outputs.energised(1) and not c.lines.line("plld", 1).pump)

    def test_the_relay_drops_when_the_line_is_shut_down(self):
        """A PUMP CONTROL OUTPUT relay follows the line's own pump, so a
        line the console has shut down de-energizes it -- and a handle
        lifted afterwards does not pull it back in.

        577013-344 Rev H p.21 reads this from the failure side: "Verify that
        the pump relay is not sticking, causing the STP to stay On when the
        console is trying to shut it down." The console's act is to drop the
        relay; this console left the pump running through its own shutdown,
        so the relay stayed in. See FIDELITY U4.
        """
        from tests.test_leaktest import run_out, shutdown_code
        c = self.a_line_site()
        c.values["S78401"] = "01" + shutdown_code("784", 0.2)
        c.line_leak[("plld", 1)] = 4.0            # fails even a 3.0 gph test
        c.lines.handle("plld", 1, True)
        self.assertTrue(c.outputs.energised(1))
        # Nobody starts this test: "A gross test always follows the
        # completion of a dispense", so hanging the handle up runs it, and
        # at 4 gph it fails.
        c.lines.handle("plld", 1, False)
        run_out(c, 2)
        self.assertIn(("plld", 1), c.leaks.disabled)
        self.assertFalse(c.outputs.energised(1),
                         "the pump relay stayed in through a shutdown")

        c.lines.handle("plld", 1, True)
        self.assertFalse(c.outputs.energised(1),
                         "a handle pulled the pump relay back in")

    def test_a_pump_sense_contact_is_a_request_too(self):
        c = a_site(kind=relays.PUMP_CONTROL, assign="00000000", tank="01")
        c.values["S80C01"] = "013101"                 # input 1: pump sense, tank 1
        c.inputs.set(1, True)
        self.assertTrue(c.outputs.energised(1))


class TheWeldedContactor(unittest.TestCase):
    """"if the pump continues to run after it is instructed to turn off, for
    longer than a 5 - 600 second selectable delay (Stuck Delay), an alarm is
    posted." A welded contactor is exactly that pump."""

    def a_monitored_pump(self):
        c = a_site(kind=relays.PUMP_CONTROL, assign="00000000", tank="01")
        c.values["S7C401"] = "011"
        c.values["S7C501"] = "01STP 1 MONITOR"
        c.values["S7C601"] = "011101"                 # watching relay 1
        c.values["S7C701"] = "0130"                   # stuck delay 30 s
        c.tick()
        return c

    def test_a_welded_pump_runs_with_the_coil_dropped_and_the_monitor_sees_it(self):
        c = self.a_monitored_pump()
        self.assertNotIn("340201", c.conditions())
        c.outputs.weld("11", 1)
        self.assertIn("PUMP (OUT): ON", c.diag_value("pumpmon_out", 1))
        c.clock_offset += 10.0
        c.tick()
        self.assertNotIn("340201", c.conditions())    # not for thirty yet
        c.clock_offset += 30.0
        c.tick()
        self.assertIn("340201", c.conditions())
        self.assertEqual(describe_alarms(["340201"])[0]["screen"],
                         "r 1:PUMP RELAY MON ALM")
        c.outputs.weld("11", 1, stuck=False)          # the contactor freed
        c.tick()
        self.assertNotIn("340201", c.conditions())

    def test_it_survives_a_reboot_and_not_a_reset(self):
        c = self.a_monitored_pump()
        c.outputs.weld("11", 1)
        c.cold_boot()
        self.assertTrue(c.outputs.is_welded("11", 1))
        c.reset()
        self.assertFalse(c.outputs.is_welded("11", 1))


if __name__ == "__main__":
    unittest.main()
