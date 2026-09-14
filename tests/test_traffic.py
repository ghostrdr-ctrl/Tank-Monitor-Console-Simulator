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
"""The site's own day: cars, blends, tankers, and what a shutdown stops.

The generator is the only thing in this package that draws a random number,
so the first thing asserted here is that a seed makes a run repeatable --
everything else in the file depends on it.

BENCH.md T12 (the pattern generator), D2 (the sale it lifts), T1 (the truck
it calls), and the two settings 576013-623 p.10-11 puts on a blend set.
"""
import os
import struct
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets                                # noqa: E402
from tls350sim import traffic as _traffic                    # noqa: E402
from tls350sim.console import Console                        # noqa: E402


LABELS = {1: "REGULAR UNLEADED", 2: "PREMIUM UNLEADED", 3: "DIESEL",
          4: "E-85"}


def a_site(tanks=LABELS, full=12000.0, volume=8000.0, lines=True):
    """Four tanks, four lines, eight meters two to a tank."""
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "edim", "plld", "relay"):
        c.modules[key] = 1
    c.software["bir"] = True
    c.software["csld"] = True
    c.software["plld020"] = True
    for tank, label in tanks.items():
        c.values[f"S602{tank:02d}"] = f"{tank:02d}" + label.ljust(20)
        c.values[f"S60A{tank:02d}"] = (f"{tank:02d}"
                                       + struct.pack(">f", full).hex().upper())
        # a 2 inch float, so `probe_type` reads a Mag probe rather than a
        # CAP0, and S611's method field 7 = CSLD -- which is what makes
        # `csld.enabled` true and the idle-time alarm reachable at all
        c.values[f"S62F{tank:02d}"] = f"{tank:02d}1"
        c.values[f"S611{tank:02d}"] = f"{tank:02d}" + "12" + "0" + "7" + "0000"
        c.tank_level[tank] = {"volume": volume, "water": 0.0}
    if lines:
        for line in tanks:
            c.values[f"S781{line:02d}"] = f"{line:02d}1"
            c.values[f"S782{line:02d}"] = f"{line:02d}" + f"LINE {line}".ljust(20)
            c.values[f"S785{line:02d}"] = f"{line:02d}{line:02d}"
    c.meters = {n: (n + 1) // 2 for n in range(1, len(tanks) * 2 + 1)}
    c.tick()
    c.tick()
    return c


def run(c, hours, step=30.0):
    """Move the console's own clock, `step` seconds at a time."""
    for _ in range(int(hours * 3600.0 / step)):
        c.clock_offset += step
        c.tick()


def through(c):
    return sum(c.bir.totals.values())


class TheGeneratorIsDeterministic(unittest.TestCase):

    def test_the_same_seed_puts_the_same_gallons_through(self):
        """Nothing else in this package is random, and the suite depends on
        it. So the one thing that is draws from its own generator.

        The wall clock is held still for this one. A console's tick length
        is real elapsed time plus whatever the clock offset moved, and the
        real half of that is different every run by a millisecond or two --
        which the generator now feels, because WHERE in an interval a car
        arrives decides whether its fill finishes inside that interval. The
        day came out the same to a hundredth of a percent with the wall
        clock running, which is not the claim being made here.
        """
        totals = []
        with mock.patch("time.time", lambda: 1789000000.0):
            for _ in range(2):
                c = a_site()
                c.traffic.seed = 4242
                c.traffic.rng.seed(4242)
                c.traffic.on = True
                c.traffic.set_level("MID")
                run(c, 6)
                totals.append(round(through(c), 6))
        self.assertEqual(totals[0], totals[1])
        self.assertGreater(totals[0], 0.0)

    def test_a_different_seed_is_a_different_day(self):
        totals = []
        for seed in (1, 2):
            c = a_site()
            c.traffic.rng.seed(seed)
            c.traffic.on = True
            c.traffic.set_level("MID")
            run(c, 6)
            totals.append(round(through(c), 2))
        self.assertNotEqual(totals[0], totals[1])


class OffIsNotClosed(unittest.TestCase):

    def test_off_sends_no_cars_and_touches_nothing(self):
        c = a_site()
        self.assertFalse(c.traffic.on)
        run(c, 8)
        self.assertEqual(through(c), 0.0)
        self.assertEqual(c.tank_level[1]["volume"], 8000.0)

    def test_closed_is_modelled_and_sends_no_cars(self):
        """A shut store is its own state: the model is running, the site is
        dark, and CSLD gets all the idle time it wants."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("CLOSED")
        run(c, 8)
        self.assertEqual(c.traffic.cars, 0)
        self.assertEqual(through(c), 0.0)
        self.assertEqual(c.csld.moving_state(1), "IDLE")

    def test_a_closed_store_still_takes_a_delivery(self):
        """The tanker is not traffic. A site that is shut still gets one."""
        c = a_site(volume=1000.0)
        c.traffic.on = True
        c.traffic.set_level("CLOSED")
        c.traffic.lead_hours = 1.0
        run(c, 4)
        self.assertGreater(c.tank_level[1]["volume"], 1000.0)


class ADayOfCars(unittest.TestCase):

    def test_the_cars_draw_the_tanks_and_book_the_sales(self):
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("MID")
        c.traffic.auto_deliver = False
        before = sum(st["volume"] for st in c.tank_level.values())
        run(c, 12)
        after = sum(st["volume"] for st in c.tank_level.values())
        gone = before - after
        self.assertGreater(gone, 500.0)
        # what BIR booked is what left the tanks, to the meter offset
        self.assertAlmostEqual(through(c), gone, delta=gone * 0.02 + 5.0)
        starts = [e for e in c.bir.events if e["kind"] == "start"]
        self.assertTrue(starts)

    def test_a_busier_store_sells_more_than_a_quiet_one(self):
        sold = {}
        for level in ("SLOW", "BUSY"):
            c = a_site()
            c.traffic.rng.seed(7)
            c.traffic.on = True
            c.traffic.set_level(level)
            c.traffic.auto_deliver = False
            run(c, 8)
            sold[level] = through(c)
        self.assertGreater(sold["BUSY"], sold["SLOW"] * 2.0)

    def test_the_day_predicted_is_about_the_day_delivered(self):
        """`daily_gallons` is computed off the settings so the bench can
        show what a level MEANS before a day of it has run. It has to agree
        with what a day actually does."""
        c = a_site(full=40000.0, volume=30000.0)
        c.traffic.rng.seed(11)
        c.traffic.on = True
        c.traffic.set_level("MID")
        c.traffic.shape = "FLAT"          # so a part-day scales cleanly
        c.traffic.auto_deliver = False
        predicted = c.traffic.daily_gallons()
        run(c, 24)
        self.assertAlmostEqual(through(c), predicted,
                               delta=predicted * 0.25)


class TheGapsBetweenTheCars(unittest.TestCase):
    """CSLD's idle time is the absence of traffic, which is why the
    arrivals are Poisson: a car every four minutes never leaves a hole,
    and a site paced on a fixed interval would look identical to a quiet
    one from CSLD's end.

    576013-818 p.11-3, Figure 11-2: "Tank goes idle and must remain so for
    8 minutes." And p.11-10: "The system has not detected an idle period in
    the last 24 hours. All tanks must have at the very least some short
    idle periods each day."
    """

    def measure(self, cars, hours=26, meters=8, tanks=1):
        """What share of the day tank 1 is quiet, and how many gaps in it
        are long enough for CSLD to do anything with.

        Measured off `activity_spans` -- the console's own record of when a
        meter actually flowed -- and not off `meter_flow`, which is a rate
        averaged over whatever interval the console last ticked and so
        reads "busy" for a whole seven-hour tick on the strength of one
        two-minute car.
        """
        c = (a_site(tanks={1: "REGULAR UNLEADED"}, full=30000.0,
                    volume=28000.0) if tanks == 1
             else a_site(full=30000.0, volume=28000.0))
        c.meters = {n: (1 if tanks == 1 else (n + 1) // 2)
                    for n in range(1, meters + 1)}
        c.traffic.rng.seed(5)
        c.traffic.on = True
        c.traffic.shape = "FLAT"
        c.traffic.cars_per_day = cars
        c.traffic.auto_deliver = False
        began = None
        for _ in range(int(hours * 120)):
            c.clock_offset += 30.0
            c.tick()
            if began is None:
                began = time.mktime(c.now())
            # hold the level, so the tank never runs dry and goes quiet for
            # a reason that is not traffic
            for tank in c.tank_level:
                c.tank_level[tank]["volume"] = 28000.0
        ended = time.mktime(c.now())
        spans = c.activity_spans(1, began, ended)
        busy = sum(b - a for a, b in spans)
        gaps, cursor = [], began
        for a, b in spans:
            if a > cursor:
                gaps.append((a - cursor) / 60.0)
            cursor = b
        if cursor < ended:
            gaps.append((ended - cursor) / 60.0)
        return (c, 1.0 - busy / (ended - began),
                len([g for g in gaps if g >= 8.0]))

    def test_a_quiet_store_leaves_idle_time(self):
        c = a_site()
        c.traffic.rng.seed(3)
        c.traffic.on = True
        c.traffic.set_level("SLOW")
        c.traffic.auto_deliver = False
        run(c, 12)
        self.assertEqual([x for x in c.conditions() if x[:4] == "0221"], [])

    def test_idle_time_falls_away_as_the_site_gets_busier(self):
        """The one number the whole feature turns on. On an ordinary
        four-tank forecourt a SLOW store is quiet nine tenths of the day
        and a BUSY one about half of it, and all three levels leave CSLD
        the eight minutes it asks for."""
        _s, slow, slow_gaps = self.measure(_traffic.LEVELS["SLOW"], tanks=4)
        _m, mid, mid_gaps = self.measure(_traffic.LEVELS["MID"], tanks=4)
        c, busy, busy_gaps = self.measure(_traffic.LEVELS["BUSY"], tanks=4)
        self.assertGreater(slow, 0.85)
        self.assertGreater(slow, mid)
        self.assertGreater(mid, busy)
        self.assertGreater(busy, 0.3)
        self.assertGreater(slow_gaps, mid_gaps)
        self.assertGreater(mid_gaps, busy_gaps)
        self.assertGreater(busy_gaps, 0)
        self.assertEqual([x for x in c.conditions() if x[:4] == "0221"], [])

    def test_a_busy_site_on_one_tank_is_denied_it(self):
        """Put every car on one tank and the eight-minute gap stops
        happening: 576013-818 p.11-10 cause 2, "Very high activity. Tank
        capacity or throughput specifications are exceeding CSLD
        specifications."

        The alarm is about the GAP and not about a stored test, which is
        what the manual's own parenthesis on that page is there to settle:
        "this does not require that a CSLD record get stored in the rate
        table"."""
        c, idle, gaps = self.measure(_traffic.LEVELS["BUSY"], tanks=1)
        self.assertLess(idle, 0.4)
        self.assertEqual(gaps, 0)
        self.assertEqual([x for x in c.conditions() if x[:4] == "0221"],
                         ["022101"])

    def test_the_answer_does_not_depend_on_how_fast_the_clock_runs(self):
        """The bench runs the clock at up to 36,000x, where one tick is
        seven hours of console time. Every quantity the generator produces
        has to be the same day at any of them -- and the idle fraction, the
        one CSLD reads, was the one that was not: it went from 76% at 43x
        to 100% at 429x on the same seed and the same cars, because a rate
        averaged over an interval says nothing about WHEN inside it."""
        seen = []
        for step in (30.0, 300.0, 1800.0):
            c = a_site(full=30000.0, volume=28000.0)
            c.traffic.rng.seed(41)
            c.traffic.on = True
            c.traffic.set_level("MID")
            c.traffic.auto_deliver = False
            began, served, last = None, 0, 0
            for _ in range(int(20 * 3600 / step)):
                c.clock_offset += step
                c.tick()
                if began is None:
                    began = time.mktime(c.now())
                # **`traffic.cars` is a counter for the console's DAY**, not
                # for the run: `_roll_day` zeroes it when the date turns
                # over and the bench labels it "cars ... today". Twenty
                # hours crosses midnight, so reading it at the end compares
                # three different windows -- whatever is left after the
                # roll, which the roll's own tick length decides. At 30
                # seconds a tick that window held no cars at all and this
                # divided by zero; at 1800 it held one.
                #
                # The quantity this test is about is the cars the run
                # served, so it is accumulated here across the reset. The
                # counter is right and the reading of it was not.
                now = c.traffic.cars
                served += now if now < last else now - last
                last = now
                for tank in c.tank_level:
                    c.tank_level[tank]["volume"] = 28000.0
            ended = time.mktime(c.now())
            spans = c.activity_spans(1, began, ended)
            busy = sum(b - a for a, b in spans)
            seen.append((1.0 - busy / (ended - began), served, through(c)))
        idle = [row[0] for row in seen]
        cars = [row[1] for row in seen]
        gallons = [row[2] for row in seen]
        self.assertLess(max(idle) - min(idle), 0.12, seen)
        # named before they are divided by, so a run that serves nothing
        # says so instead of raising ZeroDivisionError
        self.assertTrue(min(cars) > 0 and min(gallons) > 0, seen)
        self.assertLess(max(cars) / float(min(cars)), 1.4, seen)
        self.assertLess(max(gallons) / min(gallons), 1.4, seen)


class TheHandleTheCarLifts(unittest.TestCase):
    """577013-344 Rev H p.5: "If a dispense request occurs during any test,
    the test is aborted and the pump is turned On to commence dispensing."
    """

    def test_a_sale_puts_the_handle_up_and_hanging_up_puts_it_down(self):
        c = a_site()
        self.assertFalse(c.lines.line("plld", 1).handle)
        c.sales.start(1, 10.0, rate=600.0)
        self.assertTrue(c.lines.line("plld", 1).handle)
        c.sales.stop(1)
        self.assertFalse(c.lines.line("plld", 1).handle)

    def test_two_nozzles_on_one_line_are_a_refcount(self):
        """Meters 1 and 2 are both on tank 1, so both are on line 1. The
        first car up raises the handle and the LAST one down drops it."""
        c = a_site()
        c.sales.start(1, 10.0, rate=600.0)
        c.sales.start(2, 10.0, rate=600.0)
        self.assertTrue(c.lines.line("plld", 1).handle)
        c.sales.stop(1)
        self.assertTrue(c.lines.line("plld", 1).handle)
        c.sales.stop(2)
        self.assertFalse(c.lines.line("plld", 1).handle)

    def test_a_technician_holding_the_handle_keeps_it(self):
        """The bench's own HANDLE pill is somebody standing there with it,
        and a car driving away does not take it out of his hand."""
        c = a_site()
        c.lines.handle("plld", 1, True)
        c.sales.start(1, 10.0, rate=600.0)
        c.sales.stop(1)
        self.assertTrue(c.lines.line("plld", 1).handle)

    def test_a_test_will_not_start_while_a_nozzle_is_up(self):
        c = a_site()
        c.sales.start(1, 40.0, rate=600.0)
        self.assertEqual(c.lines.start("plld", 1, "periodic"), "DISPENSING")
        c.sales.stop(1)
        # "A gross test always follows the completion of a dispense", so
        # hanging the nozzle up does not leave the line free -- it leaves
        # it running the 3.0 gph test the dispense earned.
        self.assertEqual(c.lines.start("plld", 1, "periodic"),
                         "TEST ALREADY RUNNING")
        c.lines.stop("plld", 1)
        self.assertEqual(c.lines.start("plld", 1, "periodic"),
                         "TEST STARTED")

    def test_a_nozzle_lifted_mid_test_aborts_it(self):
        c = a_site()
        c.lines.start("plld", 1, "periodic")
        c.sales.start(1, 40.0, rate=600.0)
        self.assertEqual(c.lines.line("plld", 1).state, "DISPENSING")
        self.assertFalse(c.lines.line("plld", 1).running())

    def test_a_busy_site_is_a_site_that_cannot_be_tested(self):
        """The whole lesson, end to end: with traffic running a technician
        cannot get a periodic test to start, and with the store shut he
        can."""
        c = a_site(tanks={1: "REGULAR UNLEADED"})
        c.traffic.rng.seed(9)
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.traffic.shape = "FLAT"
        c.traffic.auto_deliver = False
        refused = 0
        for _ in range(60):
            run(c, 0.1)
            if c.lines.start("plld", 1, "periodic") == "DISPENSING":
                refused += 1
            else:
                c.lines.stop("plld", 1)
        self.assertGreater(refused, 10)
        # shut the store and the forecourt goes quiet
        c.traffic.on = False
        for meter in list(c.sales.running):
            c.sales.stop(meter)
        c.lines.stop("plld", 1)           # the gross test the last car left
        self.assertEqual(c.lines.start("plld", 1, "periodic"),
                         "TEST STARTED")


class ABlendedNozzle(unittest.TestCase):
    """576013-818 p.12-7: "A tank can be mapped to only one meter for a
    given Fuel Position (FP)." The console never learns a blend ratio, so a
    blended nozzle runs its component meters and the console sees two
    ordinary transactions.
    """

    def a_blend(self, c, pcts=(93.0, 7.0)):
        """E15 out of regular unleaded and E-85, which is the arithmetic:
        93% of E10 and 7% of E85 is about 15% ethanol."""
        nozzle = c.meter_key(9)
        c.blends[nozzle] = {
            "label": "E15",
            "parts": [[str(c.meter_key(1)), pcts[0]],
                      [str(c.meter_key(7)), pcts[1]]]}
        return nozzle

    def test_it_runs_its_components_in_the_ratio(self):
        c = a_site()
        nozzle = self.a_blend(c)
        c.sales.start(nozzle, 20.0, rate=600.0)
        self.assertAlmostEqual(c.meter_flow[1], 558.0, delta=1.0)
        self.assertAlmostEqual(c.meter_flow[7], 42.0, delta=1.0)
        self.assertEqual(c.blend_parts(nozzle), [(1, 0.93), (4, 0.07)])

    def test_the_fuel_leaves_both_tanks_in_the_ratio(self):
        c = a_site()
        nozzle = self.a_blend(c)
        before = {t: c.tank_level[t]["volume"] for t in (1, 4)}
        c.sales.start(nozzle, 20.0, rate=600.0)
        run(c, 0.1)
        gone = {t: before[t] - c.tank_level[t]["volume"] for t in (1, 4)}
        self.assertAlmostEqual(gone[1], 18.6, delta=0.6)
        self.assertAlmostEqual(gone[4], 1.4, delta=0.4)

    def test_the_console_sees_two_ordinary_meters(self):
        """No blend anywhere in the map, in BIR, or in the meter events
        table: two meters, each against its own tank."""
        c = a_site()
        nozzle = self.a_blend(c)
        c.sales.start(nozzle, 20.0, rate=600.0)
        run(c, 0.1)
        self.assertNotIn(nozzle, c.meters)
        self.assertNotIn(nozzle, c.bir.totals)
        self.assertIn(c.meter_key(1), c.bir.totals)
        self.assertIn(c.meter_key(7), c.bir.totals)
        tanks = {e["tank"] for e in c.bir.events}
        self.assertEqual(tanks, {1, 4})

    def test_it_lifts_the_handle_on_both_lines(self):
        c = a_site()
        nozzle = self.a_blend(c)
        c.sales.start(nozzle, 20.0, rate=600.0)
        self.assertTrue(c.lines.line("plld", 1).handle)
        self.assertTrue(c.lines.line("plld", 4).handle)
        c.sales.stop(nozzle)
        self.assertFalse(c.lines.line("plld", 1).handle)
        self.assertFalse(c.lines.line("plld", 4).handle)

    def test_the_grade_is_the_blend_s_own(self):
        """An E15 nozzle fed from a regular tank and an E-85 tank sells
        neither of them."""
        c = a_site()
        nozzle = self.a_blend(c)
        self.assertEqual(c.traffic.meter_family(nozzle), "plus")
        self.assertIn(nozzle, c.traffic.selling()["plus"])

    def test_a_blend_with_no_components_sells_nothing(self):
        c = a_site()
        c.blends[c.meter_key(9)] = {"label": "E15", "parts": []}
        self.assertNotIn("plus", c.traffic.selling())


class TheBlendSetOnTheLines(unittest.TestCase):
    """576013-623 p.10-11: "When a site has mechanical blenders, the lines
    can be assigned to a blend set. This change affects the scheduling of
    precision line testing, 0.2 and 0.1."
    """

    def a_blend_set(self, c):
        for line in (1, 2):
            c.set_setting("blender", "YES", line)
        c.set_setting("blend_partners", "02", 1)
        c.set_setting("blend_partners", "01", 2)

    def test_the_set_reads_both_settings(self):
        c = a_site()
        self.assertEqual(c.lines.blend_set("plld", 1), [])
        self.a_blend_set(c)
        self.assertEqual(c.lines.blend_set("plld", 1), [1, 2])
        self.assertEqual(c.lines.blend_set("plld", 2), [1, 2])

    def test_a_partner_dispensing_stops_a_precision_test_starting(self):
        c = a_site()
        self.a_blend_set(c)
        c.sales.start(3, 20.0, rate=600.0)      # meter 3 is on tank 2
        self.assertTrue(c.lines.line("plld", 2).handle)
        self.assertEqual(c.lines.start("plld", 1, "periodic"), "DISPENSING")

    def test_the_gross_test_is_not_on_the_list(self):
        """p.10-11 names "precision line testing, 0.2 and 0.1" and nothing
        else, so a 3.0 gph test is unaffected."""
        c = a_site()
        self.a_blend_set(c)
        c.sales.start(3, 20.0, rate=600.0)
        self.assertEqual(c.lines.start("plld", 1, "gross"), "TEST STARTED")

    def test_without_the_setting_the_partner_is_nobody(self):
        c = a_site()
        c.sales.start(3, 20.0, rate=600.0)
        self.assertEqual(c.lines.start("plld", 1, "periodic"),
                         "TEST STARTED")


class TheTruckThatComesOnItsOwn(unittest.TestCase):

    def test_a_low_tank_is_ordered_and_arrives(self):
        """The order is placed on the console's first look at the gauge --
        a site that comes up with a tank already low has needed a truck for
        a while -- and the load lands when the lead time is up."""
        c = a_site(volume=1500.0)
        self.assertIn(1, c.traffic.orders)
        self.assertEqual(c.traffic.lead_hours, 4.0)
        run(c, 3)
        self.assertIn(1, c.traffic.orders)      # still on the road
        run(c, 3)
        self.assertNotIn(1, c.traffic.orders)
        self.assertGreater(c.tank_level[1]["volume"], 1500.0)

    def test_the_console_infers_it_rather_than_being_told(self):
        """The whole point of dropping it down the riser: the watcher sees
        a level rising the way a real drop raises it."""
        c = a_site(volume=1500.0)
        c.traffic.lead_hours = 0.5
        run(c, 8)
        self.assertTrue(c.deliveries.records.get(1))

    def test_it_fills_to_the_percentage_asked_for(self):
        c = a_site(volume=1500.0)
        c.traffic.lead_hours = 0.5
        c.traffic.fill_pct = 80.0
        run(c, 10)
        self.assertAlmostEqual(c.tank_level[1]["volume"], 9600.0, delta=400.0)

    def test_turning_it_off_calls_the_tanker_back(self):
        """A load ordered and not yet on the ground is cancelled rather
        than left pending for something that will never bring it."""
        c = a_site(volume=1500.0)
        self.assertTrue(c.traffic.orders)
        c.traffic.auto_deliver = False
        run(c, 8)
        self.assertEqual(c.traffic.orders, {})
        self.assertEqual(c.tank_level[1]["volume"], 1500.0)


class WhatAShutdownStops(unittest.TestCase):
    """Three routes to a dead pump, and a technician has to tell them
    apart: a failed test, an assigned alarm, and a relay in the contactor.
    """

    def low_product_alarm(self, c, tank=1):
        """S621 above the volume, which is 02/05 LOW PRODUCT ALARM."""
        c.values[f"S621{tank:02d}"] = (f"{tank:02d}"
                                       + struct.pack(">f", 9000.0).hex().upper())
        c.tick()
        return "0205" + f"{tank:02d}"

    def test_a_failed_test_shuts_the_line_down(self):
        c = a_site()
        c.leaks.disabled.add(("plld", 1))
        self.assertTrue(c.lines.disabled("plld", 1))
        self.assertTrue(c.dispensing_blocked(1))

    def test_an_assigned_disable_alarm_shuts_the_line_down(self):
        """787's assignments were stored, printed back, and acted on by
        nothing. FIDELITY S17 put them on the wire; this makes them stop
        fuel."""
        c = a_site()
        record = self.low_product_alarm(c)
        self.assertIn(record, c.conditions())
        self.assertFalse(c.lines.disabled("plld", 1))
        c.line_disable_alarms[("plld", 1)] = [("02", "05", "01")]
        c.tick()
        self.assertTrue(c.lines.disabled("plld", 1))
        self.assertEqual(c.lines.shutdown_alarms("plld", 1),
                         [("02", "05", "01")])

    def test_the_shutdown_lifts_when_the_CAUSE_goes_not_the_message(self):
        """Reported from the bench: "it would show that message until the
        alarm cleared, aka water gone."

        Water in a sump assigned to shut a line down holds the line down
        while the water is there. 576013-610 Rev AC p.29-1 has both halves
        of the rule and treats them separately: "Warning and Alarm Messages
        display until you correct the cause of the problem. After you
        correct the cause, you must press the ALARM/TEST button to
        acknowledge the alarm and clear the display", and then "When you
        correct the condition, the lights will shut off." A de-energized
        pump is on the lights' side of that line -- the message stays on
        the glass waiting to be acknowledged, and the pump comes back.

        This read `displayed()`, which is the message, so a sump that had
        dried out kept its line shut down until somebody pressed a key.

        The FAILED-TEST route is a different thing and is not this one: it
        is governed by LINE RE-ENABLE METHOD (S553), whose two choices are
        PASS LINE TEST and ACKNOWLEDGE ALARM, and 576013-623 Rev AN p.5-10
        scopes that setting to "a line shut down by a failing line leak
        test" in as many words.
        """
        c = a_site()
        record = self.low_product_alarm(c)
        c.line_disable_alarms[("plld", 1)] = [("02", "05", "01")]
        c.tick()
        self.assertIn(record, c.conditions())
        self.assertTrue(c.lines.disabled("plld", 1))
        self.assertEqual(c.lines.line("plld", 1).screen()[1],
                         "DISABLE ALARM HANDLE OFF")
        # the cause is corrected and nobody has touched ALARM/TEST
        c.values.pop("S62101")
        run(c, 0.2)
        self.assertNotIn(record, c.conditions())
        self.assertIn(record, c.displayed())      # still on the glass
        self.assertNotIn(record, c.active_alarms())
        self.assertFalse(c.lines.disabled("plld", 1))
        self.assertEqual(c.lines.line("plld", 1).screen()[1],
                         "TEST COMPLETE HANDLE OFF")

    def test_a_failed_test_needs_its_own_re_enable_method(self):
        """The other route, and the one S553 governs. PASS LINE TEST is the
        default, so acknowledging does not bring the line back."""
        c = a_site()
        c.leaks.disabled.add(("plld", 1))
        self.assertTrue(c.lines.disabled("plld", 1))
        self.assertFalse(c.leaks.re_enable())         # PASS LINE TEST
        self.assertTrue(c.lines.disabled("plld", 1))
        c.values["S55300"] = "1"                      # ACKNOWLEDGE ALARM
        self.assertTrue(c.leaks.re_enable())
        self.assertFalse(c.lines.disabled("plld", 1))

    def test_a_disable_assignment_for_all_tanks_matches_any(self):
        """"TT - Tank/Sensor Number (Decimal, 00=all)"."""
        c = a_site()
        self.low_product_alarm(c)
        c.line_disable_alarms[("plld", 1)] = [("02", "05", "00")]
        c.tick()
        self.assertTrue(c.lines.disabled("plld", 1))

    def test_a_relay_wired_to_the_tank_takes_the_pump_out(self):
        """576013-623 p.7-24: "allowing the operator to set a relay to shut
        down the submersible"."""
        c = a_site()
        c.values["S80601"] = "011"                       # relay 1 configured
        c.values["S80701"] = "01" + "STP 1".ljust(20)
        c.values["S80A01"] = "011"                       # STANDARD
        c.values["S80B01"] = "0101"                      # on tank 1
        # low product, tank 1 -- through the setter, because 808 holds a
        # LIST now and `values` is where nothing reads it. See U48.
        c.assign_relay_alarm(1, "02", "05", "01")
        c.tick()
        self.assertFalse(c.outputs.pump_cut(1))
        self.low_product_alarm(c)
        c.tick()
        self.assertTrue(c.outputs.pump_cut(1))
        self.assertTrue(c.lines.disabled("plld", 1))
        self.assertEqual(c.outputs.cutting(), [(1, 1)])

    def test_a_shut_down_line_sells_nothing(self):
        c = a_site()
        c.leaks.disabled.add(("plld", 1))
        c.sales.start(1, 40.0, rate=600.0)
        before = c.tank_level[1]["volume"]
        run(c, 0.2)
        self.assertEqual(c.tank_level[1]["volume"], before)
        self.assertEqual(c.bir.totals.get(c.meter_key(1), 0.0), 0.0)

    def test_the_handle_still_goes_up_and_gets_no_pump(self):
        """A shut-down pump does not answer a handle -- and the console
        says so rather than pretending nothing happened."""
        c = a_site()
        c.leaks.disabled.add(("plld", 1))
        c.sales.start(1, 40.0, rate=600.0)
        self.assertTrue(c.lines.line("plld", 1).handle)
        self.assertEqual(c.lines.line("plld", 1).state,
                         "DISPENSING DISABLED")

    def test_the_generator_turns_cars_away_from_a_dead_line(self):
        # The wall clock is held still. These counters are per CONSOLE DAY
        # and `_roll_day` zeroes them at midnight, so a four-hour run
        # started late enough in the evening crossed the roll and asserted
        # against zero -- a test that passed all day and failed after eight.
        with mock.patch("time.time", lambda: 1789000000.0):
            c = a_site(tanks={1: "REGULAR UNLEADED"})
            c.leaks.disabled.add(("plld", 1))
            c.traffic.rng.seed(2)
            c.traffic.on = True
            c.traffic.set_level("MID")
            run(c, 4)
            self.assertEqual(c.traffic.cars, 0)
            self.assertGreater(c.traffic.blocked, 0)
            self.assertEqual(c.traffic.balked, 0)

    def test_one_dead_component_stops_a_blend(self):
        """A dispenser cannot make the mix without both."""
        c = a_site()
        nozzle = c.meter_key(9)
        c.blends[nozzle] = {"label": "E15",
                            "parts": [[str(c.meter_key(1)), 93.0],
                                      [str(c.meter_key(7)), 7.0]]}
        self.assertFalse(c.dispensing_blocked(nozzle))
        c.leaks.disabled.add(("plld", 4))          # the E-85 line
        self.assertTrue(c.dispensing_blocked(nozzle))


class WhatTheDayReaches(unittest.TestCase):
    """Console behaviour that existed and had nothing to drive it.

    Neither of these is new code. They are here because a forecourt that
    runs itself is the first thing on this bench that can reach them, and
    because each was claimed before it was checked.
    """

    # The wall clock is held still for this pair. Whether a close lands
    # while a nozzle happens to be up is the thing being tested, and it is
    # also a coin flip -- so the run has to be reproducible or the test
    # passes about half the time. Frozen clock plus seeded arrivals makes
    # the day the same day every time.
    FROZEN = 1789000000.0

    def a_closing_site(self):
        c = a_site()
        for shift, hhmm in ((1, "0200"), (2, "0800"),
                            (3, "1400"), (4, "2000")):
            c.values[f"S794{shift:02d}"] = f"{shift:02d}" + hhmm
        c.values["S79300"] = "00" + "0300"     # and the day at 03:00
        return c

    def test_a_forecourt_that_never_stops_defers_the_shift_close(self):
        """`BIR._scheduled` holds a close that comes due while a meter is
        selling, and 576013-610 Table 29-16 is the warning that earns:
        CLOSE SHIFT PENDING. Nothing could keep a site busy across a
        closing time before, so nothing had ever raised it."""
        with mock.patch("time.time", lambda: self.FROZEN):
            c = self.a_closing_site()
            c.traffic.rng.seed(21)
            c.traffic.on = True
            c.traffic.set_level("BUSY")
            c.traffic.shape = "FLAT"
            c.traffic.auto_deliver = False
            seen = set()
            for _ in range(30 * 120):
                c.clock_offset += 30.0
                c.tick()
                for tank in c.tank_level:
                    c.tank_level[tank]["volume"] = 8000.0
                seen |= {x for x in c.conditions()
                         if x[:4] in ("0113", "0114")}
        self.assertIn("011300", seen)

    def test_a_quiet_site_just_closes(self):
        """The other half, and the one that says the warning means
        something: the same closing times with nobody on the forecourt
        raise nothing at all."""
        with mock.patch("time.time", lambda: self.FROZEN):
            c = self.a_closing_site()
            seen = set()
            for _ in range(30 * 120):
                c.clock_offset += 30.0
                c.tick()
                seen |= {x for x in c.conditions()
                         if x[:4] in ("0113", "0114")}
        self.assertEqual(sorted(seen), [])

    def test_accuchart_calibrates_off_the_traffic(self):
        """"Opening Height / Closing Height / TLS Volume / Dispensed
        Volume / Tank/Meter Ratio": AccuChart learns from product going out
        through a meter, over a range of heights. A day of cars drawing the
        tank down and a tanker filling it back is exactly that, and it is
        the first thing here that could produce it without somebody
        dragging a float."""
        c = a_site(volume=4000.0)
        c.software["accuchart"] = True
        c.values["S61501"] = "011"          # METER DATA PRESENT: YES
        c.values["S60701"] = "01" + struct.pack(">f", 96.0).hex().upper()
        c.traffic.rng.seed(31)
        c.traffic.on = True
        c.traffic.set_level("MID")
        c.traffic.low_pct = 30.0
        self.assertTrue(c.accuchart.enabled(1),
                        c.accuchart.disabled_because(1))
        run(c, 72, step=60.0)
        entry = c.accuchart.state(1)
        self.assertEqual(entry.mode, "CALIBRATE")
        self.assertGreater(len(entry.observations), 50)
        self.assertGreater(entry.data, 5000.0)
        # and the deliveries carried the level across a real range, which is
        # what a chart is a chart OF
        self.assertGreater(entry.high_height - entry.low_height, 20.0)


class WhatSurvivesWhat(unittest.TestCase):

    def test_the_settings_come_back_off_a_state_file(self):
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.traffic.shape = "NIGHT"
        c.traffic.mix["diesel"] = 40.0
        c.traffic.low_pct = 15.0
        blob = c.traffic.state()
        fresh = a_site()
        fresh.traffic.restore(blob)
        self.assertTrue(fresh.traffic.on)
        self.assertEqual(fresh.traffic.level, "BUSY")
        self.assertEqual(fresh.traffic.shape, "NIGHT")
        self.assertEqual(fresh.traffic.mix["diesel"], 40.0)
        self.assertEqual(fresh.traffic.low_pct, 15.0)

    def test_an_order_on_the_way_survives_a_state_file(self):
        c = a_site(volume=1500.0)
        c.tick()
        self.assertIn(1, c.traffic.orders)
        fresh = a_site()
        fresh.traffic.restore(c.traffic.state())
        self.assertIn(1, fresh.traffic.orders)

    def test_rebooting_the_console_does_not_close_the_store(self):
        """The forecourt is the world, not RAM."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.blends[c.meter_key(9)] = {"label": "E15", "parts": []}
        c.sales.start(1, 40.0, rate=600.0)
        self.assertTrue(c.lines.line("plld", 1).handle)
        c.cold_boot()
        self.assertTrue(c.traffic.on)
        self.assertEqual(c.traffic.level, "BUSY")
        self.assertIn(c.meter_key(9), c.blends)
        # a nozzle in somebody's hand is still in it
        self.assertIn(c.meter_key(1), c.sales.running)
        # but a COLD boot has lost the programming, so there is no longer a
        # line for it to be holding a handle on -- `resync` puts the
        # refcount back against whatever lines the console still has, which
        # after this one is none
        self.assertEqual(c.programmed_lines(), [])
        self.assertEqual(c.sales._handles, {})


class WhyNothingIsHappening(unittest.TestCase):
    """The generator's one failure mode that looks exactly like success.

    A console with no DIM in it cannot have a meter; a forecourt with no
    meters sells nothing; and the pill still said RUNNING in green while
    the curve drew and the hour lit and not one car ever arrived. This is
    the console reported from the field: BIR unlicensed, no DIM, two tanks,
    one blended grade with no components, BUSY, and zero of everything.
    """

    def a_bare_console(self):
        c = Console(None)
        c.board = "E6"
        for key in ("probe", "liquid", "plld"):
            c.modules[key] = 1
        for tank in (1, 2):
            c.values[f"S602{tank:02d}"] = f"{tank:02d}" + "REGULAR".ljust(20)
            c.tank_level[tank] = {"volume": 6200.0, "water": 0.0}
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        return c

    def test_a_console_with_no_dim_says_why_rather_than_selling_nothing(self):
        c = self.a_bare_console()
        why = [text for text, _where in c.traffic.blockers()]
        self.assertTrue(any("BIR" in t for t in why), why)
        self.assertTrue(any("DIM" in t for t in why), why)
        self.assertEqual(c.traffic.selling(), {})

    def test_every_reason_names_a_bench_view_that_exists(self):
        c = self.a_bare_console()
        c.blends[c.meter_key(1)] = {"label": "MID", "parts": []}
        for _text, where in c.traffic.blockers():
            self.assertIn(where, ("Site", "Traffic", "Modules"))

    def test_a_blend_with_no_components_is_one_of_them(self):
        c = self.a_bare_console()
        c.blends[c.meter_key(1)] = {"label": "MID", "parts": []}
        why = [t for t, _w in c.traffic.blockers()]
        self.assertTrue(any("no components" in t for t in why), why)

    def test_a_working_site_has_nothing_to_report(self):
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        self.assertEqual(c.traffic.blockers(), [])
        self.assertIsNone(c.traffic.idle_reason())

    def test_off_and_closed_are_states_and_not_faults(self):
        """Neither is a misconfiguration, and the words have to say so --
        CLOSED is the one setting that gives CSLD all the idle time it
        wants, and OFF is the bench as it was before the generator."""
        c = a_site()
        c.traffic.on = False
        self.assertIn("off", c.traffic.idle_reason())
        c.traffic.on = True
        c.traffic.set_level("CLOSED")
        self.assertIn("CLOSED", c.traffic.idle_reason())

    def test_the_reason_is_the_first_thing_that_has_to_be_fixed(self):
        c = self.a_bare_console()
        self.assertEqual(c.traffic.idle_reason(), c.traffic.blockers()[0][0])

    def test_one_dead_blend_does_not_declare_a_working_site_idle(self):
        """`blockers` reports everything wrong; `idle_reason` answers the
        narrower question the header asks, which is whether ANYTHING is for
        sale. Eight working meters and one empty blend is not an idle site."""
        c = a_site()
        c.traffic.on = True
        c.blends[c.meter_key(9)] = {"label": "MID", "parts": []}
        self.assertTrue(c.traffic.blockers())
        self.assertIsNone(c.traffic.idle_reason())

    def test_a_site_whose_every_meter_is_shut_down_says_so(self):
        c = a_site()
        c.traffic.on = True
        for line in (1, 2, 3, 4):
            c.leaks.disabled.add(("plld", line))
        why = [t for t, _w in c.traffic.blockers()]
        self.assertTrue(any("shut down" in t for t in why), why)


class EachCarIsCountedAgainstItsOwnDay(unittest.TestCase):
    """The counters were zeroed at the top of a tick and everything that
    tick then generated was booked as today's.

    At a real console's pace the tick that crosses midnight carries a few
    seconds of yesterday. At 36,000x it carries seven hours of it: the
    previous day's closing figures were destroyed before anyone could read
    them, and up to ten hours of its cars were credited to a day they did
    not happen in. Measured: 23:30 read 838 cars and 16,261 gallons, and
    one seven hour tick to 06:30 replaced it with 327 -- 27 of which had
    arrived the day before.
    """

    def at_eleven(self):
        """A busy site parked at 23:00 on its own clock."""
        c = a_site(full=30000.0, volume=28000.0)
        c.traffic.on = True
        c.traffic.shape = "FLAT"
        c.traffic.cars_per_day = 1400.0
        c.traffic.auto_deliver = False
        c.traffic.rng.seed(9)
        stamp = c.now()
        c.clock_offset += (((23 - stamp.tm_hour) % 24) * 3600.0
                           - stamp.tm_min * 60.0 - stamp.tm_sec)
        c.tick()
        return c

    def test_a_tick_across_midnight_does_not_destroy_yesterday(self):
        c = self.at_eleven()
        run(c, 0.5)                       # trade until 23:30
        closing = c.traffic.cars
        self.assertGreater(closing, 0)
        c.clock_offset += 7 * 3600.0      # one 36,000x tick to 06:30
        c.tick()
        was = c.traffic.yesterday()
        self.assertIsNotNone(was)
        self.assertGreaterEqual(was.cars, closing)

    def test_yesterday_s_late_cars_are_credited_to_yesterday(self):
        """The seven hours that tick covers begin before midnight, and
        those cars are yesterday's however late the tick reports them."""
        c = self.at_eleven()
        run(c, 0.5)
        closing = c.traffic.cars
        c.clock_offset += 7 * 3600.0
        c.tick()
        self.assertGreater(c.traffic.yesterday().cars, closing)

    def test_today_starts_from_nothing(self):
        c = self.at_eleven()
        run(c, 0.5)
        busy = c.traffic.cars
        c.clock_offset += 7 * 3600.0
        c.tick()
        self.assertLess(c.traffic.cars, busy)
        self.assertGreater(c.traffic.cars, 0)

    def test_it_does_not_keep_every_day_forever(self):
        """A bench left running for a week does not need last week's."""
        c = self.at_eleven()
        for _ in range(6):
            c.clock_offset += 86400.0
            c.tick()
        self.assertLessEqual(len(c.traffic._days),
                             _traffic.Traffic.KEEP_DAYS)

    def test_the_counters_still_read_as_plain_numbers(self):
        c = self.at_eleven()
        for name in ("cars", "balked", "blocked", "dry", "deliveries"):
            self.assertIsInstance(getattr(c.traffic, name), int, name)
        self.assertIsInstance(c.traffic.gallons, float)


class TheShapeSurvivesAFastClock(unittest.TestCase):
    """The second of the two knobs, and it stopped working at speed.

    `arrival_rate()` is the rate at ONE hour of the day, and the interval
    was charged entirely at whatever hour the tick landed on. At x1 that is
    exact. At x36,000 one tick is seven hours, so a seven hour block of
    cars was drawn at one hour's rate and scattered over the seven before
    it: measured over ten console days on the DAY curve, 03:00 drew 41 cars
    per mille against its own 2 and the 17:00 peak kept 38 of its 102. The
    day's TOTAL stayed right, so every counter looked correct while the
    commuter curve had quietly flattened into a plateau -- and the peak
    against idle boundary this module exists to teach (576013-818 p.11-10)
    went with it.
    """

    def histogram(self, step, days=6, seed=5):
        c = a_site(full=30000.0, volume=28000.0)
        c.traffic.rng.seed(seed)
        c.traffic.on = True
        c.traffic.shape = "DAY"
        c.traffic.cars_per_day = 550.0
        c.traffic.auto_deliver = False
        hours = [0] * 24
        real = c.traffic._arrive

        def spy(at, closes):
            hours[time.localtime(at).tm_hour] += 1
            return real(at, closes)

        c.traffic._arrive = spy
        for _ in range(int(days * 86400.0 / step)):
            c.clock_offset += step
            c.tick()
            for tank in c.tank_level:
                c.tank_level[tank]["volume"] = 28000.0
        total = float(sum(hours)) or 1.0
        return [h / total for h in hours]

    def test_the_day_curve_is_the_same_shape_at_any_speed(self):
        want = [w / float(sum(_traffic.SHAPES["DAY"]))
                for w in _traffic.SHAPES["DAY"]]
        for step in (30.0, 7 * 3600.0):
            got = self.histogram(step)
            # the night hours are where a smeared curve shows first: the
            # DAY shape gives 02:00-04:00 under 1% of the day between them
            night = sum(got[2:5])
            self.assertLess(night, 0.02, f"step {step}: {night:.3f}")
            # and the evening peak has to still be a peak
            self.assertGreater(got[17], 0.07, f"step {step}: {got[17]:.3f}")
            worst = max(abs(a - b) for a, b in zip(got, want))
            self.assertLess(worst, 0.02, f"step {step}: off by {worst:.3f}")

    def test_a_slice_never_crosses_an_hour(self):
        t = a_site().traffic
        now = time.mktime(t.c.now())
        for began, ended, stamp in t._slices(now - 7 * 3600.0, now):
            self.assertLessEqual(ended - began, 3600.0 + 1e-6)
            self.assertEqual(time.localtime(began).tm_hour, stamp.tm_hour)
            if ended - began > 1.0:
                self.assertIn(time.localtime(ended - 1.0).tm_hour,
                              (stamp.tm_hour,))

    def test_one_tick_cannot_be_asked_for_a_year_of_cars(self):
        """`Console.tick` runs inside the Tk main loop. Setting the date a
        year forward -- which S501 invites -- built 303,532 arrivals in one
        call and froze the window for 29.5 seconds."""
        c = a_site(full=30000.0, volume=28000.0)
        c.traffic.on = True
        c.traffic.cars_per_day = 550.0
        c.traffic.auto_deliver = False
        c.tick()
        c.clock_offset += 365 * 86400.0
        began = time.time()
        c.tick()
        self.assertLess(time.time() - began, 5.0)
        self.assertLess(c.traffic.cars, 550 * _traffic.Traffic.MOST_HOURS)


class TheSettingsCannotBeMadeNonsense(unittest.TestCase):
    """Every delivery box took `float(text or 0)` and checked no range."""

    def test_a_low_percent_of_zero_is_not_taken_as_never(self):
        c = a_site(volume=400.0)
        c.traffic.on = True
        c.traffic.low_pct = 0.0          # what clearing the box used to write
        c.traffic.lead_hours = 0.0
        c.tick()
        self.assertTrue(c.traffic.orders or c.drops.running)

    def test_a_fill_below_the_low_mark_does_not_order_forever(self):
        c = a_site()
        c.traffic.on = True
        c.traffic.low_pct, c.traffic.fill_pct = 500.0, 400.0
        c.traffic.lead_hours = -9.0
        run(c, 12)
        for tank, st in c.tank_level.items():
            self.assertLessEqual(st["volume"], c.full_volume(tank) + 1.0)

    def test_the_share_of_a_grade_nobody_wants_is_nobody(self):
        """Zeroing every share used to fall back to picking uniformly, so
        the rows read 0% and 0 gal/day over a forecourt serving 96 cars an
        hour."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        for fam in list(c.traffic.mix):
            c.traffic.mix[fam] = 0.0
        run(c, 2)
        self.assertEqual(c.traffic.cars, 0)

    def test_the_word_follows_the_number_typed_over_it(self):
        """Click CLOSED, type 500, and the pill went on reading CLOSED over
        a forecourt selling 33 cars an hour."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("CLOSED")
        self.assertEqual(c.traffic.level_word(), "CLOSED")
        self.assertIn("CLOSED", c.traffic.idle_reason())
        c.traffic.cars_per_day = 500.0
        self.assertIsNone(c.traffic.level_word())
        self.assertIsNone(c.traffic.idle_reason())
        c.traffic.set_level("BUSY")
        self.assertEqual(c.traffic.level_word(), "BUSY")

    def test_a_grade_row_reports_its_own_gallons(self):
        """The rows split the site total by the CAR share, and a diesel
        customer takes four times what a regular one does -- diesel read
        1,755 gal/day where the engine put 4,737 through it."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("MID")
        for fam, meters in c.traffic.selling().items():
            tanks = {int(t) for m in meters
                     for t, _f in c.blend_parts(m)}
            engine = sum(c.traffic.daily_gallons(t) for t in tanks)
            self.assertAlmostEqual(c.traffic.grade_gallons(fam), engine,
                                   delta=max(1.0, engine * 0.01), msg=fam)
        self.assertAlmostEqual(
            sum(c.traffic.grade_gallons(f) for f in c.traffic.selling()),
            c.traffic.daily_gallons(), delta=1.0)


class TheTankerKeepsItsOwnPromises(unittest.TestCase):

    def test_the_gauge_is_read_after_the_fuel_has_gone(self):
        """`_replenish` ran at the top of `traffic.tick`, before
        `Bir._dispense` had drawn anything, so the tanker board always
        decided on the PREVIOUS tick's level. At 36,000x a tick is seven
        hours, and the console went a whole day noticing nothing: measured
        on the retail preset at BUSY, both tanks ended at zero against
        3,160 and 5,589 gallons at 60x on the same cars."""
        for step in (30.0, 7 * 3600.0):
            c = a_site(full=10000.0, volume=6200.0)
            c.traffic.on = True
            c.traffic.set_level("BUSY")
            c.traffic.rng.seed(4)
            for _ in range(int(23 * 3600.0 / step)):
                c.clock_offset += step
                c.tick()
            for tank, st in sorted(c.tank_level.items()):
                self.assertGreater(st["volume"], 0.0,
                                   f"step {step}: tank {tank} ran dry")

    def test_a_tank_that_cannot_last_the_lead_time_orders_now(self):
        """A site orders on "will I run out before the truck gets here",
        not only on a percentage."""
        c = a_site(full=30000.0, volume=28000.0)
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.traffic.lead_hours = 4.0
        c.tick()
        self.assertFalse(c.traffic.orders)          # 93% full, nothing due
        c.tank_level[1]["volume"] = 900.0           # 3% -- and under a
        c.traffic.gauge()                           # day's lead-time draw
        self.assertIn(1, c.traffic.orders)

    def test_the_compartment_gap_reaches_the_truck(self):
        """`COMPARTMENT_GAP` was declared, cited to 576013-623 p.7-26 and
        passed to nothing, so every auto-delivery ran at `Drop`'s default
        two minutes. With S610's DELIVERY DELAY at 2 or 3 that is the
        difference between one delivery and several."""
        c = a_site(volume=400.0)
        c.traffic.on = True
        c.traffic.lead_hours = 0.0
        c.tick()
        c.clock_offset += 60.0
        c.tick()
        drop = next(iter(c.drops.running.values()), None)
        self.assertIsNotNone(drop)
        self.assertEqual(drop.gap, _traffic.Traffic.COMPARTMENT_GAP)

    def test_a_clock_set_backwards_does_not_strand_the_load(self):
        """`Order.due` is an absolute stamp and nothing expired one, so
        S501 SET TIME/DATE could leave four tanks dry with four loads
        showing as due in a year that had not happened yet."""
        c = a_site(volume=400.0)
        c.traffic.on = True
        c.traffic.set_level("CLOSED")
        c.traffic.lead_hours = 4.0
        c.tick()
        self.assertTrue(c.traffic.orders)
        before = c.tank_level[1]["volume"]
        c.clock_offset -= 20 * 365 * 86400.0
        run(c, 40)
        self.assertGreater(c.tank_level[1]["volume"], before + 1000.0)
        self.assertFalse(c.traffic.orders)

    def test_the_blob_does_not_alias_the_live_mix(self):
        t = a_site().traffic
        blob = t.state()
        self.assertIsNot(blob["mix"], t.mix)


class ItDoesNotCountSalesThatCannotHappen(unittest.TestCase):
    """The failure that looks MOST like success.

    Map the meters but leave the BIR key out and the generator ran
    perfectly: cars arrived, handles went up, the header counted 313 cars
    and 5,963 gallons -- against a tank that never left 8,000. On this
    bench `Bir._dispense` is the only thing that draws a tank down from a
    sale and it wants the key and a DIM, so without them nothing moves and
    every figure on the view was a number no probe, screen or report would
    ever agree with.
    """

    def test_a_site_with_no_bir_key_moves_nothing_and_says_so(self):
        c = a_site()
        c.software["bir"] = False
        c.tick()
        self.assertFalse(c.traffic.can_sell())
        before = c.tank_level[1]["volume"]
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        run(c, 6)
        self.assertEqual(c.traffic.cars, 0)
        self.assertEqual(c.traffic.gallons, 0.0)
        self.assertEqual(c.tank_level[1]["volume"], before)
        self.assertIn("BIR", c.traffic.idle_reason())

    def test_a_site_with_no_dim_is_the_same(self):
        c = a_site()
        c.modules.pop("edim", None)
        c.tick()
        self.assertFalse(c.traffic.can_sell())
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        run(c, 6)
        self.assertEqual(c.traffic.cars, 0)
        self.assertIn("DIM", c.traffic.idle_reason())

    def test_a_dry_tank_sells_nothing_and_is_counted_apart(self):
        """BIR clamps what actually LEAVES a tank; nothing clamped what the
        generator claimed had been bought, so four tanks at 0.0 gallons
        still had the header reporting 8,225 gallons sold that day. A
        nozzle with nothing behind it is its own kind of turned-away car --
        not a queue, and not a shutdown, but a tank that needed a truck."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.traffic.auto_deliver = False
        for st in c.tank_level.values():
            st["volume"] = 0.0
        run(c, 6)
        self.assertEqual(c.traffic.cars, 0)
        self.assertEqual(c.traffic.gallons, 0.0)
        self.assertGreater(c.traffic.dry, 0)
        self.assertEqual(c.traffic.balked, 0)
        self.assertEqual(c.traffic.blocked, 0)

    def test_a_blend_needs_product_in_every_component_tank(self):
        """A blender with one dry tank cannot make the grade at all."""
        c = a_site()
        c.traffic.on = True
        blend = c.meter_key(9)
        c.blends[blend] = {"label": "E15",
                           "parts": [[str(c.meter_key(1)), 50.0],
                                     [str(c.meter_key(7)), 50.0]]}
        self.assertFalse(c.traffic._dry(blend))
        c.tank_level[4]["volume"] = 0.0        # the E-85 half runs out
        self.assertTrue(c.traffic._dry(blend))

    def test_a_proper_site_sells_and_the_tank_follows(self):
        """The counters and the tank have to agree: what the header says
        was sold is what left the tanks, give or take the fuel still in
        somebody's hand at the end of the run."""
        c = a_site()
        c.traffic.on = True
        c.traffic.set_level("BUSY")
        c.traffic.auto_deliver = False
        c.traffic.rng.seed(7)
        before = sum(st["volume"] for st in c.tank_level.values())
        run(c, 6)
        after = sum(st["volume"] for st in c.tank_level.values())
        self.assertTrue(c.traffic.can_sell())
        self.assertGreater(c.traffic.cars, 0)
        self.assertAlmostEqual(before - after, c.traffic.gallons,
                               delta=c.traffic.gallons * 0.05 + 60.0)

    def test_the_tanker_still_comes_to_a_site_that_cannot_sell(self):
        """A load is ordered off a gauge, not off a till. Pulling the DIM
        must not strand a tank that is already low."""
        c = a_site(volume=400.0)
        c.modules.pop("edim", None)
        c.tick()
        c.traffic.on = True
        c.traffic.auto_deliver = True
        c.traffic.lead_hours = 0.0
        c.tick()
        self.assertTrue(c.traffic.orders or c.drops.running)


class EveryExampleSiteCanRunIt(unittest.TestCase):
    """The simulator ships three example sites and two of them could not
    sell a gallon.

    This is where the bug report came from. The bench opens on the two-tank
    retail site; it had no DIM in the cage, no BIR key and no meter map, so
    the traffic generator ran on it and moved nothing at all -- and the
    compliance site, the one with CSLD on every tank, was the same. CSLD's
    whole input is the gaps between sales (576013-818 Figure 11-2, "Tank
    goes idle and must remain so for 8 minutes"), so a CSLD example site
    that cannot sell is an example of nothing.
    """

    def a_preset(self, name):
        c = Console(None)
        c.save = lambda *a, **k: None
        presets.load(c, name)
        c.tick()
        return c

    def test_every_preset_can_sell(self):
        for name in presets.PRESETS:
            c = self.a_preset(name)
            c.traffic.on = True
            c.traffic.set_level("MID")
            self.assertEqual(c.traffic.blockers(), [], name)
            self.assertIsNone(c.traffic.idle_reason(), name)
            self.assertTrue(c.traffic.selling(), name)

    def test_every_preset_actually_moves_fuel(self):
        """Not just "a meter is mapped": the tank has to go down. A site
        with meters and no BIR key counts its cars and shifts nothing,
        which is the failure that looks most like success."""
        for name in presets.PRESETS:
            c = self.a_preset(name)
            c.traffic.on = True
            c.traffic.set_level("MID")
            c.traffic.auto_deliver = False
            c.traffic.rng.seed(11)
            before = {t: st["volume"] for t, st in c.tank_level.items()}
            run(c, 6)
            self.assertGreater(c.traffic.cars, 0, name)
            moved = [t for t, v in before.items()
                     if c.tank_level[t]["volume"] < v - 1.0]
            self.assertTrue(moved, f"{name}: no tank moved")

    def test_the_compliance_site_can_be_made_idle_enough_for_csld(self):
        """The site CSLD is demonstrated on has to be able to be busy, or
        there is nothing for the idle time to be idle BETWEEN."""
        c = self.a_preset("Compliance site, CSLD and sensors")
        self.assertTrue(c.licensed("csld"))
        self.assertTrue(any(c.csld.enabled(t) for t in c.tank_level))
        c.traffic.on = True
        c.traffic.set_level("SLOW")
        self.assertTrue(c.traffic.selling())


class TheShapeOfTheDay(unittest.TestCase):

    def test_every_shape_has_a_weight_for_every_hour(self):
        for name, weights in _traffic.SHAPES.items():
            self.assertEqual(len(weights), 24, name)
            self.assertTrue(all(w > 0 for w in weights), name)

    def test_night_is_busier_at_three_than_at_noon_and_day_is_not(self):
        night = _traffic.SHAPES["NIGHT"]
        day = _traffic.SHAPES["DAY"]
        self.assertGreater(night[3], night[12])
        self.assertGreater(day[8], day[3])

    def test_flat_never_falls_far(self):
        """The shape that denies CSLD its idle time has to be flat enough
        that no hour of it is quiet."""
        flat = _traffic.SHAPES["FLAT"]
        self.assertGreater(min(flat) / max(flat), 0.6)

    def test_a_label_is_read_for_what_it_says(self):
        self.assertEqual(_traffic.family("PREMIUM UNLEADED"), "premium")
        self.assertEqual(_traffic.family("REGULAR UNLEADED"), "regular")
        self.assertEqual(_traffic.family("DIESEL"), "diesel")
        self.assertEqual(_traffic.family("E-85"), "e85")
        self.assertEqual(_traffic.family("DEF"), "def")
        self.assertEqual(_traffic.family("E15"), "plus")
        self.assertEqual(_traffic.family(""), "regular")


if __name__ == "__main__":
    unittest.main()
