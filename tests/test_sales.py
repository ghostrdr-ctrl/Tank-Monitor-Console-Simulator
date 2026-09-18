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
"""A sale is a nozzle lifted and hung up, not a rate; and a DIM that stops
reporting them earns the Transaction Alarm. BENCH.md D2 and D3."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console, describe_alarms       # noqa: E402


def a_site(delay="005"):
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "edim"):
        c.modules[key] = 1
    c.software["bir"] = True
    c.software["csld"] = True
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    c.meters = {1: 1}
    c.meter_flow = {}
    c.set_setting("bdim_delay", delay)
    c.tick()
    c.tick()
    return c


def seconds(c, n):
    c.clock_offset += float(n)
    c.tick()


class ANozzle(unittest.TestCase):

    def test_a_sale_is_the_rate_for_exactly_its_gallons(self):
        c = a_site()
        before = c.tank_level[1]["volume"]
        sale = c.sales.start(1, 10.0, rate=600.0)       # ten a minute
        self.assertEqual(c.meter_flow[1], 600.0)
        seconds(c, 30)
        self.assertAlmostEqual(sale.sold, 5.0, delta=0.2)
        self.assertEqual(c.sales.describe(1)[:6], "5.0 of")
        self.assertTrue(c.csld.busy(1))
        seconds(c, 40)
        self.assertNotIn(1, c.sales.running)
        self.assertEqual(c.meter_flow[1], 0.0)
        self.assertFalse(c.csld.busy(1))
        self.assertAlmostEqual(before - c.tank_level[1]["volume"], 10.0,
                               delta=0.3)
        self.assertAlmostEqual(c.bir.totals[1], 10.0, delta=0.3)
        ends = [e for e in c.bir.events if e["kind"] == "end"]
        self.assertEqual(len(ends), 1)
        self.assertAlmostEqual(ends[0]["gallons"], 10.0, delta=0.3)

    def test_hanging_up_early_ends_the_sale_where_it_is(self):
        c = a_site()
        c.sales.start(1, 100.0, rate=600.0)
        seconds(c, 30)
        sale = c.sales.stop(1)
        self.assertAlmostEqual(sale.sold, 5.0, delta=0.2)
        self.assertEqual(c.meter_flow[1], 0.0)
        seconds(c, 30)
        self.assertAlmostEqual(c.bir.totals[1], 5.0, delta=0.3)

    def test_the_bench_flow_comes_back_after_the_sale(self):
        c = a_site()
        c.meter_flow[1] = 40.0                          # a trickle all day
        c.sales.start(1, 5.0, rate=600.0)
        self.assertEqual(c.meter_flow[1], 600.0)
        seconds(c, 60)
        self.assertEqual(c.meter_flow[1], 40.0)


class TheTransactionAlarm(unittest.TestCase):
    """"a delay (of from 5 to 999 hours) before posting a Block DIM
    Transaction Alarm ... Enter 000 to disable this feature"."""

    def test_a_dim_that_reported_and_went_quiet(self):
        c = a_site(delay="005")
        c.sales.start(1, 5.0)
        seconds(c, 60)
        self.assertNotIn("190401", c.conditions())
        seconds(c, 4 * 3600)
        self.assertNotIn("190401", c.conditions())
        seconds(c, 3600 + 60)
        self.assertIn("190401", c.conditions())
        self.assertEqual(describe_alarms(["190401"])[0]["screen"],
                         "E 1:TRANSACTION ALARM")
        # a sale is a transaction, and it clears the condition
        c.sales.start(1, 5.0)
        seconds(c, 10)
        self.assertNotIn("190401", c.conditions())

    def test_a_dim_that_never_reported_is_a_quiet_site(self):
        c = a_site(delay="005")
        seconds(c, 10 * 3600)
        self.assertNotIn("190401", c.conditions())

    def test_zero_disables_it(self):
        c = a_site(delay="000")
        c.sales.start(1, 5.0)
        seconds(c, 60)
        seconds(c, 10 * 3600)
        self.assertNotIn("190401", c.conditions())

    def test_no_edim_no_alarm(self):
        c = a_site(delay="005")
        c.sales.start(1, 5.0)
        seconds(c, 60)
        c.modules["edim"] = 0
        seconds(c, 10 * 3600)
        self.assertNotIn("190401", c.conditions())


if __name__ == "__main__":
    unittest.main()
