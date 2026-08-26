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
"""The console's communications gear beyond the RS-232 card: auto-dial and
its one documented failure, the remote display, and the DIMs that metered
transactions arrive through."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console, describe_alarms


def dialing_console():
    c = Console()
    c.modules["modem"] = 1
    c.values["S52101"] = "011"            # receiver 1 configured
    c.values["S52301"] = "015551234567"   # with a number to dial
    c.values["S52601"] = "0102"           # two tries
    c.values["S52701"] = "0101"           # a minute apart
    c.tick()
    return c


class Autodial(unittest.TestCase):
    """576013-818: "System failed to connect to a remote receiver after
    'n' tries." The call, the schedule and the alarm are documented; the
    frame the console would send once connected is not, and is not
    invented."""

    def test_a_new_alarm_makes_the_console_dial(self):
        # with the receiver answering, the call completes within the tick:
        # the evidence is the answered call in the dial log
        c = dialing_console()
        c.cover_open = True
        c.tick()
        self.assertIn("answered", [e[2] for e in c.autodial.log])

    def test_no_answer_retries_then_posts_autodial_failure(self):
        c = dialing_console()
        c.autodial.answers = False
        c.cover_open = True
        c.tick()
        c.clock_offset += 61; c.tick()
        c.clock_offset += 61; c.tick()
        self.assertTrue(c.autodial.failed)
        self.assertIn("010900", c.conditions())
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("AUTODIAL FAILURE", screens)

    def test_an_answered_call_clears_the_failure(self):
        c = dialing_console()
        c.autodial.answers = False
        c.cover_open = True
        for _ in range(3):
            c.clock_offset += 61; c.tick()
        self.assertTrue(c.autodial.failed)
        c.autodial.answers = True
        c.selftest_error = True               # any fresh alarm dials
        c.tick()
        self.assertFalse(c.autodial.failed)

    def test_the_confirmation_report_queues_when_asked_for(self):
        c = dialing_console()
        c.values["S52801"] = "011"            # confirmation report on
        c.cover_open = True
        c.tick()
        self.assertIn(1, c.autodial.confirm_pending)

    def test_no_modem_no_dial(self):
        c = dialing_console()
        del c.modules["modem"]
        c.cover_open = True
        c.tick()
        self.assertIsNone(c.autodial.pending)


class RemoteDisplay(unittest.TestCase):
    def test_a_faulted_link_posts_system_alarm_08(self):
        c = Console()
        c.modules["rdu"] = 1
        c.rdu_fault = True
        self.assertIn("010800", c.conditions())
        c.rdu_fault = False
        self.assertNotIn("010800", c.conditions())

    def test_no_module_no_alarm(self):
        c = Console()
        c.rdu_fault = True
        self.assertNotIn("010800", c.conditions())


class Dims(unittest.TestCase):
    """576013-623 ch.17: metered transactions reach the console through a
    DIM; no DIM, no meter data, whatever the site is really selling."""

    def a_selling_site(self):
        c = Console()
        c.board = "E6"
        c.software["bir"] = True
        c.modules.update({"probe": 1, "edim": 1})
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
        c.meters = {1: 1}
        c.meter_flow = {1: 100.0}
        c.tick()
        return c

    def test_with_a_dim_the_meters_sell(self):
        c = self.a_selling_site()
        c.clock_offset += 3600; c.tick()
        self.assertLess(c.tank_level[1]["volume"], 4901)

    def test_without_one_the_console_sees_nothing(self):
        c = self.a_selling_site()
        del c.modules["edim"]
        c.clock_offset += 3600; c.tick()
        self.assertEqual(c.tank_level[1]["volume"], 5000.0)

    def test_a_faulted_link_is_the_same_blindness_plus_the_alarm(self):
        c = self.a_selling_site()
        c.dim_fault = True
        c.clock_offset += 3600; c.tick()
        self.assertEqual(c.tank_level[1]["volume"], 5000.0)
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("E 1:DIM COMMUNICATION ALARM", screens)

    def test_the_mdim_is_a_dim_too(self):
        c = self.a_selling_site()
        del c.modules["edim"]
        c.modules["mdim"] = 1
        c.clock_offset += 3600; c.tick()
        self.assertLess(c.tank_level[1]["volume"], 4901)


if __name__ == "__main__":
    unittest.main()
