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


class ASaleOnlyProgressesOnFuelThatMoved(unittest.TestCase):
    """FIDELITY R31.

    `tick` advanced `sale.sold` before anything had asked whether the fuel
    was there, and `draw` -- which is what actually takes it out of the
    tank, capped at what the tank holds and skipped altogether under a
    shutdown -- ran afterwards. So a twenty gallon sale completed against a
    tank holding one, and a sale during a shutdown completed having moved
    nothing. This is a disagreement with the physics the rest of the model
    keeps, which is why it is the odd entry in its section.
    """

    def test_a_dry_tank_sells_what_it_has_and_no_more(self):
        c = a_site()
        c.tank_level[1]["volume"] = 1.0
        sale = c.sales.start(1, 20.0, rate=600.0)
        for _ in range(6):
            seconds(c, 30)
        self.assertLessEqual(sale.sold, 1.0 + 1e-6,
                             "a 20 gallon sale completed against 1 gallon")
        self.assertAlmostEqual(c.tank_level[1]["volume"], 0.0, delta=1e-6)

    def test_the_nozzle_goes_up_rather_than_hanging_on_a_dry_tank(self):
        """A pump that will not pump is a customer who hangs up. Without
        this the sale never reaches `done` and holds its nozzle for ever."""
        c = a_site()
        c.tank_level[1]["volume"] = 1.0
        c.sales.start(1, 20.0, rate=600.0)
        for _ in range(8):
            seconds(c, 30)
        self.assertNotIn(1, c.sales.running,
                         "the nozzle was left up on an empty tank")

    def test_a_shutdown_moves_nothing_and_completes_nothing(self):
        c = a_site()
        before = c.tank_level[1]["volume"]
        sale = c.sales.start(1, 10.0, rate=600.0)
        with _blocked(c):
            for _ in range(3):
                seconds(c, 30)
            self.assertEqual(sale.sold, 0.0,
                             "a sale progressed while the pump was dead")
        self.assertAlmostEqual(c.tank_level[1]["volume"], before, delta=1e-6)

    def test_the_meter_does_not_turn_when_no_fuel_passed(self):
        """`meter_flow` is what BIR and AccuChart book from. Booking a
        transaction for fuel that never left is the same defect one step
        downstream."""
        c = a_site()
        c.sales.start(1, 10.0, rate=600.0)
        with _blocked(c):
            seconds(c, 30)
            self.assertEqual(c.meter_flow.get(1, 0.0), 0.0)

    def test_an_ordinary_sale_is_untouched(self):
        c = a_site()
        before = c.tank_level[1]["volume"]
        sale = c.sales.start(1, 10.0, rate=600.0)
        seconds(c, 30)
        self.assertAlmostEqual(sale.sold, 5.0, delta=0.2)
        self.assertAlmostEqual(c.tank_level[1]["volume"], before - sale.sold,
                               delta=0.2)
        seconds(c, 40)
        self.assertNotIn(1, c.sales.running)

    def test_a_partly_served_sale_keeps_the_part_it_got(self):
        """Not all or nothing: the tank had some, and that much was sold."""
        c = a_site()
        c.tank_level[1]["volume"] = 3.0
        sale = c.sales.start(1, 20.0, rate=600.0)
        seconds(c, 30)
        self.assertGreater(sale.sold, 0.0)
        self.assertLessEqual(sale.sold, 3.0 + 1e-6)

    def test_a_shutdown_that_lifts_lets_the_sale_go_on(self):
        """The starve counter takes two intervals, so a shutdown that
        clears inside one tick does not hang up a sale about to be served."""
        c = a_site()
        sale = c.sales.start(1, 10.0, rate=600.0)
        with _blocked(c):
            seconds(c, 10)
        seconds(c, 30)
        self.assertIn(1, c.sales.running)
        self.assertGreater(sale.sold, 0.0)


class _blocked:
    """Hold every nozzle on this console down, the way an ISD shutdown
    does, for the life of a `with`."""

    def __init__(self, console):
        self.c = console

    def __enter__(self):
        self._was = self.c.isd_shutdown_active
        self.c.isd_shutdown_active = lambda: True
        return self

    def __exit__(self, *exc):
        self.c.isd_shutdown_active = self._was
        return False


if __name__ == "__main__":
    unittest.main()
