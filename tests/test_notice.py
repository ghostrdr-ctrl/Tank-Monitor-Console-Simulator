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
"""The Service Notice session, which is what a technician opens before work.

"When service is performed at a site, 'false' alarms and 'false' deliveries
can be generated. 'False' alarms can trigger unneeded service dispatches to
the site and 'false' deliveries can cause reconciliation problems."
576013-623 Rev AN p.5-27 is the feature; Figure 6-5 of the Troubleshooting
Guide is the panel that opens and closes one; 576013-635 Rev AA pp.233-236
are the four function codes.

Before this, `service_sessions` was an empty list nothing appended to, 11B
reported on it, and the panel's three screens asserted DISABLED and ENABLED
at once. FIDELITY D9.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console
from tls350sim.wire import Handler, SOH


def a_site(override=False):
    c = Console()
    c.set_module("probe", 1)
    c.modules["rs232"] = 1
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S56600"] = "1"                    # the FEATURE is switched on
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    if override:
        c.set_setting("delivery_override", "ENABLED", 0)
    c.tick()
    return c


class TheSession(unittest.TestCase):
    def test_it_opens_and_closes_and_is_recorded_both_times(self):
        """"A 'Service Notice' session start record is entered in the Service
        Notice history" and "the 'Service Notice' end record is entered in
        the Service Notice history"."""
        c = a_site()
        self.assertIsNone(c.service_session())
        self.assertEqual(c.start_service_session(), "ENABLED")
        self.assertIsNotNone(c.service_session())
        self.assertEqual(len(c.service_sessions), 1)
        self.assertEqual(c.end_service_session(), "DISABLED")
        self.assertIsNone(c.service_session())
        self.assertEqual(len(c.service_sessions), 1)
        self.assertIsNotNone(c.service_sessions[0]["end"])

    def test_the_history_keeps_ten(self):
        """"contains the last 10 records"."""
        c = a_site()
        for _ in range(14):
            c.start_service_session()
            c.end_service_session()
        self.assertEqual(len(c.service_sessions), 10)

    def test_the_duration_is_one_to_eight_hours_and_two_by_default(self):
        """"Duration can be selected from 1 - 8 hours. Default is 2 hours"."""
        c = a_site()
        self.assertEqual(c.service_session_hours(), 2)
        c.set_setting("service_duration", "8", 0)
        self.assertEqual(c.service_session_hours(), 8)
        for junk in ("0", "9", "", "TWO"):
            c.set_setting("service_duration", junk, 0)
            self.assertEqual(c.service_session_hours(), 2, junk)

    def test_it_times_out_on_its_own(self):
        """"Cleared manually following service session or automatically after
        timeout (max 8 hours)" -- 576013-610 Rev AC's warning table. The end
        recorded is the moment it was due, not the tick that noticed."""
        c = a_site()
        c.set_setting("service_duration", "3", 0)
        c.start_service_session()
        began = c.service_sessions[0]["start"]
        c.clock_offset += 2 * 3600
        c.tick()
        self.assertIsNotNone(c.service_session())      # two hours in, still on
        c.clock_offset += 2 * 3600
        c.tick()
        self.assertIsNone(c.service_session())
        self.assertAlmostEqual(c.service_sessions[0]["end"], began + 3 * 3600,
                               delta=1.0)

    def test_a_second_start_does_not_open_a_second_session(self):
        c = a_site()
        c.start_service_session()
        c.start_service_session()
        self.assertEqual(len(c.service_sessions), 1)


class TheDeliveryInProgressRefusal(unittest.TestCase):
    """Figure 6-5 annotates the DISABLED screen "If there is a delivery in
    progress, then cannot change to Enable, and it will display 'DISABLED DEL
    IN PROGRESS'", and the ENABLED screen "Can only change to Enabled if there
    are no deliveries in progress when Delivery Override is Enabled". The
    second is the fuller statement of the first: it is the override that takes
    a drop out of the histories, so it is only with the override on that a
    session opened mid-drop would cut one delivery in half."""

    def dropping(self, c):
        c.tank_level[1]["volume"] += 600.0
        c.tick()
        self.assertTrue(c.delivery_running())
        return c

    def test_a_drop_refuses_the_session_when_the_override_is_on(self):
        c = self.dropping(a_site(override=True))
        self.assertEqual(c.start_service_session(),
                         "DISABLED DEL IN PROGRESS")
        self.assertIsNone(c.service_session())

    def test_and_the_refusal_is_exactly_the_width_of_the_display(self):
        self.assertEqual(len("DISABLED DEL IN PROGRESS"), 24)

    def test_without_the_override_a_drop_does_not_stop_it(self):
        c = self.dropping(a_site(override=False))
        self.assertEqual(c.start_service_session(), "ENABLED")


class WhatTheOverrideDoesToADrop(unittest.TestCase):
    """"If 'Delivery Override' is enabled then any deliveries that occur when
    the TLS is in a 'Service Notice' session will not go into the standard
    delivery history or BIR delivery history", and "if 'Delivery Override' is
    disabled ... [they] go into the standard delivery history or BIR delivery
    history"."""

    def one_drop(self, c):
        c.tank_level[1]["volume"] += 900.0
        c.tick()
        # past the delay, so the delivery finishes
        c.clock_offset += (c.delivery_delay(1) + 5) * 60.0
        c.tick()
        return len(c.deliveries.records.get(1) or [])

    def test_a_session_with_the_override_on_keeps_the_drop_out(self):
        c = a_site(override=True)
        c.start_service_session()
        self.assertEqual(self.one_drop(c), 0)

    def test_a_session_with_the_override_off_records_it(self):
        c = a_site(override=False)
        c.start_service_session()
        self.assertEqual(self.one_drop(c), 1)

    def test_and_with_no_session_at_all_the_override_changes_nothing(self):
        c = a_site(override=True)
        self.assertEqual(self.one_drop(c), 1)


class OverTheWire(unittest.TestCase):
    """576013-635 Rev AA pp.233-236. All four Sets carry their verification
    code at the FRONT -- `S56600149f` -- where `VERIFIED` strips a trailing
    one, so none of them could be written; 566 had no data template either."""

    def setUp(self):
        self.c = a_site()
        self.c.values.pop("S56600", None)
        self.h = Handler(self.c, verbose=False)

    def ask(self, code):
        return self.h.handle(SOH + code).decode("latin-1")

    def test_the_feature_switch_takes_its_leading_149(self):
        self.assertNotIn("9999", self.ask(b"S566001491"))
        self.assertIn("SERVICE NOTICE: ENABLED", self.ask(b"I56600"))
        self.assertIn("9999", self.ask(b"S5660001"))     # no verification

    def test_the_delivery_override_reads_and_writes(self):
        self.ask(b"S566001491")
        self.assertIn("SERVICE NOTICE DELIVERY OVERRIDE: DISABLED",
                      self.ask(b"I56700"))
        self.ask(b"S567001491")
        self.assertIn("SERVICE NOTICE DELIVERY OVERRIDE: ENABLED",
                      self.ask(b"I56700"))
        self.assertTrue(self.c.delivery_override())
        self.assertTrue(self.ask(b"i56700").split("&&")[0].endswith("1"))

    def test_the_session_switch_is_the_session_itself(self):
        self.ask(b"S566001491")
        self.assertIn("SERVICE NOTICE SESSION: DISABLED", self.ask(b"I56800"))
        self.ask(b"S568001491")
        self.assertIsNotNone(self.c.service_session())
        self.assertIn("SERVICE NOTICE SESSION: ENABLED", self.ask(b"I56800"))
        self.ask(b"S568001490")
        self.assertIsNone(self.c.service_session())

    def test_no_session_without_the_feature(self):
        """"Only appears if Service Notice feature has been enabled"."""
        self.assertIn("9999", self.ask(b"S568001491"))
        self.assertIsNone(self.c.service_session())

    def test_the_duration_is_hours_in_decimal(self):
        self.assertIn("SERVICE NOTICE SESSION DURATION: 2 HOURS",
                      self.ask(b"I56900"))
        self.assertNotIn("9999", self.ask(b"S5690005"))
        self.assertIn("SERVICE NOTICE SESSION DURATION: 5 HOURS",
                      self.ask(b"I56900"))
        self.assertTrue(self.ask(b"i56900").split("&&")[0].endswith("05"))
        for bad in (b"S5690000", b"S5690009", b"S56900XX"):
            self.assertIn("9999", self.ask(bad), bad)

    def test_the_session_report_finally_has_something_to_report(self):
        """11B has been reading `service_sessions` since it was written, and
        nothing has ever put a record in it."""
        self.assertNotIn("IN PROGRESS", self.ask(b"I11B00"))
        self.c.start_service_session()
        self.assertIn("IN PROGRESS", self.ask(b"I11B00"))
        self.c.end_service_session()
        report = self.ask(b"I11B00")
        self.assertIn("SERVICE NOTICE SESSION REPORT", report)
        self.assertNotIn("IN PROGRESS", report)


if __name__ == "__main__":
    unittest.main()
