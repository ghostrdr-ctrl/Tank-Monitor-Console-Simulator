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
"""What a siphon manifold does, which used to be nothing.

FIDELITY Y8. Manifolding was labelling only: a delivery into one tank did not
raise its partner, and the set was written at one end.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_controls import a_site, send                 # noqa: E402


class TheSetIsEnteredForAllOfIt(unittest.TestCase):
    """576013-623 Rev AN p.7-20: "You only need to enter this information for
    one of the tanks in the set. The system automatically enters the
    information for the other tank(s) in the set"."""

    def test_naming_one_end_writes_the_other(self):
        c, h = a_site()
        send(h, "S612010102")
        self.assertEqual(c.partners("612", 1), [2])
        self.assertEqual(c.partners("612", 2), [1])

    def test_three_tanks_all_name_each_other(self):
        c, h = a_site()
        send(h, "S61201010203")
        self.assertEqual(c.partners("612", 1), [2, 3])
        self.assertEqual(c.partners("612", 2), [1, 3])
        self.assertEqual(c.partners("612", 3), [1, 2])

    def test_clearing_one_end_dissolves_the_set(self):
        """The other end was written by the console rather than by the
        operator, so it has to be unwritten the same way -- otherwise the
        set survives the command meant to dissolve it."""
        c, h = a_site()
        send(h, "S612010102")
        send(h, "S6120100")
        self.assertEqual(c.partners("612", 1), [])
        self.assertEqual(c.partners("612", 2), [])
        self.assertEqual(c.siphon_set(1), [1])


class TheSiphonMovesProduct(unittest.TestCase):
    """"Tank Test Siphon Break allows the operator to perform in-tank leak
    tests on siphon manifolded tanks" -- which is only a problem worth a
    valve because the siphon carries fuel between them."""

    def a_pair(self):
        c, h = a_site()
        c.manifold_together(1, [2])
        return c, h

    def test_the_levels_settle_to_one(self):
        c, _h = self.a_pair()
        for _ in range(8):
            c.siphon_tick(0.5)
        self.assertAlmostEqual(c.height_at(1, c.tank_level[1]["volume"]),
                               c.height_at(2, c.tank_level[2]["volume"]),
                               places=2)

    def test_it_moves_fuel_and_does_not_make_it(self):
        """The settled level is NOT the average of the levels: two tanks of
        different diameters hold different amounts at the same height, so it
        is the level whose volumes add up to what the set already holds."""
        c, _h = self.a_pair()
        c.manifold_together(3, [4])
        for group in ((1, 2), (3, 4)):
            before = sum(c.tank_level[n]["volume"] for n in group)
            for _ in range(20):
                c.siphon_tick(1.0)
            after = sum(c.tank_level[n]["volume"] for n in group)
            self.assertAlmostEqual(before, after, places=3, msg=str(group))

    def test_tanks_of_different_diameters_settle_at_one_height(self):
        c, _h = a_site()
        c.manifold_together(3, [4])
        self.assertNotEqual(c.limit("607", 3), c.limit("607", 4))
        for _ in range(20):
            c.siphon_tick(1.0)
        self.assertAlmostEqual(c.height_at(3, c.tank_level[3]["volume"]),
                               c.height_at(4, c.tank_level[4]["volume"]),
                               places=2)

    def test_it_takes_time(self):
        """A pipe carries what a pipe carries: the set does not jump to its
        level in one tick."""
        c, _h = self.a_pair()
        gap = abs(c.tank_level[1]["volume"] - c.tank_level[2]["volume"])
        c.siphon_tick(1.0 / 60.0)               # one minute
        self.assertGreater(
            abs(c.tank_level[1]["volume"] - c.tank_level[2]["volume"]),
            gap * 0.5)

    def test_a_running_leak_test_shuts_the_valve(self):
        """Which is what the break valve is for, and what makes a test on a
        manifolded tank possible at all."""
        c, _h = self.a_pair()
        for n in (1, 2):
            c.values[f"S632{n:02d}"] = f"{n:02d}1"     # the valve is fitted
        c.leaks.start("tank", 1, "periodic", 2.0)
        held = dict((n, c.tank_level[n]["volume"]) for n in (1, 2))
        c.siphon_tick(1.0)
        for n in (1, 2):
            self.assertEqual(c.tank_level[n]["volume"], held[n])

    def test_a_site_without_the_valve_keeps_siphoning_through_a_test(self):
        """FIDELITY H9. "NOTE: This option requires that the siphon break
        valve be installed. When on, Tank Test Siphon Break allows the
        operator to perform in-tank leak tests on siphon manifolded tanks.
        To leave the feature off, press STEP" -- 576013-623 Rev AN p.7-24,
        which makes OFF the default, and the real tape agrees:
        `TNK TST SIPHON BREAK:OFF`.

        `S632` was read by nothing, so the console broke the siphon for
        every test on every site: it modelled a valve nobody had bought,
        and the test it made possible is exactly the test the manual says
        you cannot do without one.
        """
        c, _h = self.a_pair()
        c.leaks.start("tank", 1, "periodic", 2.0)
        held = dict((n, c.tank_level[n]["volume"]) for n in (1, 2))
        c.siphon_tick(1.0)
        self.assertNotEqual(c.tank_level[1]["volume"], held[1])
        self.assertNotEqual(c.tank_level[2]["volume"], held[2])

    def test_a_shut_valve_posts_the_warning_the_table_gives_it(self):
        """576013-610 Rev AC Table 29-3: "TANK SIPHON BREAK | Warning |
        Siphon break valve has shut | Clears when tank test completes."

        `02/22` is one of N1's alarm types with no producer, and it needed
        no new physics -- the condition is the valve being shut, which the
        site model already knows because it is what stops the fuel moving.
        """
        c, _h = self.a_pair()
        for n in (1, 2):
            c.values[f"S632{n:02d}"] = f"{n:02d}1"
        self.assertNotIn("022201", c.conditions())
        c.leaks.start("tank", 1, "periodic", 2.0)
        # the valve is shut across the SET, not only under the tank on test
        self.assertIn("022201", c.conditions())
        self.assertIn("022202", c.conditions())
        c.leaks.stop("tank", 1)
        self.assertNotIn("022201", c.conditions())

    def test_a_site_without_the_valve_never_posts_it(self):
        c, _h = self.a_pair()
        c.leaks.start("tank", 1, "periodic", 2.0)
        self.assertNotIn("022201", c.conditions())

    def test_an_unmanifolded_tank_is_left_alone(self):
        c, _h = a_site()
        held = dict((n, c.tank_level[n]["volume"]) for n in c.tank_level)
        c.siphon_tick(1.0)
        for n, was in held.items():
            self.assertEqual(c.tank_level[n]["volume"], was)


class TableTwentyNineFoursDriftCriteria(unittest.TestCase):
    """Two of the three, which needed a probe that knows which thermistors
    are submerged -- and Y5 built one. FIDELITY Y10.

    Both are RATES: "changed by more than 0.1 F per hour" for the submerged
    average and "more than 0.3 F per hour" for any one of them.
    """

    def a_test_under_way(self, hold=None, hours=2.0):
        import time
        c, _h = a_site()
        now = time.mktime(c.now())
        if hold is not None:
            c.hold_temperature(1, hold)
            # as though the bench had been holding it since before the test
            c.tank_temp_log[1] = [(now - (hours + 1) * 3600.0, None)]
        c.leaks.start("tank", 1, "periodic", hours)
        run = c.leaks.active("tank", 1)
        run.started = now - hours * 3600.0
        return c, run

    def test_a_console_left_alone_trips_neither(self):
        """Which is Y4's whole point: this bench would have failed all three
        by two orders of magnitude before the product temperature was slowed
        to the speed of the season."""
        c, run = self.a_test_under_way()
        self.assertEqual(c.leaks.invalidations(run, 2.0), ())

    def test_a_steady_hold_trips_neither(self):
        c, run = self.a_test_under_way(hold=55.0)
        self.assertEqual(c.leaks.invalidations(run, 2.0), ())

    def test_a_slow_move_trips_the_average_alone(self):
        """0.4 F in two hours is 0.2 F an hour: over the average's 0.1 and
        under a single thermistor's 0.3."""
        c, run = self.a_test_under_way(hold=55.0)
        c.hold_temperature(1, 55.4)
        self.assertEqual(c.leaks.invalidations(run, 2.0),
                         ("TEMP CHANGE TOO LARGE",))

    def test_a_fast_move_trips_both(self):
        c, run = self.a_test_under_way(hold=55.0)
        c.hold_temperature(1, 56.0)             # 0.5 F an hour
        self.assertEqual(c.leaks.invalidations(run, 2.0),
                         ("TEMP CHANGE TOO LARGE", "CHANGE IN TANK TEMP ZONE"))

    def test_a_move_below_both_trips_neither(self):
        c, run = self.a_test_under_way(hold=55.0)
        c.hold_temperature(1, 55.1)             # 0.05 F an hour
        self.assertEqual(c.leaks.invalidations(run, 2.0), ())

    def test_the_third_criterion_is_not_claimed(self):
        """CHANGE IN HEAD TEMP names the thermistor in the probe HEAD, which
        is above the fuel and never submerged, and nothing on this shelf
        settles whether it is the sixth of A15's six or a seventh."""
        from tls350sim import leaktest
        c, run = self.a_test_under_way(hold=55.0)
        c.hold_temperature(1, 70.0)
        self.assertIn("CHANGE IN HEAD TEMP", leaktest.FLAGS)
        self.assertNotIn("CHANGE IN HEAD TEMP",
                         c.leaks.invalidations(run, 2.0))


if __name__ == "__main__":
    unittest.main()
