"""The DIM link, one port at a time, with the history BA1 reports.
BENCH.md D1."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console                        # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


def a_site():
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "edim"):
        c.modules[key] = 1
    c.software["bir"] = True
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    c.meters = {1: 1}
    c.meter_flow = {1: 600.0}
    c.tick()
    c.tick()
    return c


def ba1(c):
    return Handler(c, verbose=False).handle(
        ("{}IBA100{}".format(chr(1), chr(13))).encode()).decode("latin-1")


class APort(unittest.TestCase):

    def test_an_edim_site_has_ports_and_they_are_ok(self):
        c = a_site()
        self.assertEqual([p["status"] for p in c.dim_ports()], ["OK", "OK"])
        self.assertIn("PORT 1", ba1(c))
        # A port with no faults gets its heading row and nothing under it.
        # `NO FAULT HISTORY` was this project's phrase -- FIDELITY S18
        # settled the shape from a capture, and U5 collects the rest.
        self.assertIn("DURATION (HOURS)", ba1(c))
        self.assertNotIn("NO FAULT HISTORY", ba1(c))

    def test_taking_a_port_down_posts_the_alarm_on_that_port(self):
        c = a_site()
        c.set_dim_port(2, True)
        self.assertIn("190302", c.conditions())
        self.assertNotIn("190301", c.conditions())
        c.set_dim_port(1, True)
        self.assertIn("190301", c.conditions())
        c.set_dim_port(2, False)
        self.assertNotIn("190302", c.conditions())

    def test_the_history_has_a_post_a_clear_and_a_duration(self):
        c = a_site()
        c.set_dim_port(1, True)
        port = c.dim_ports()[0]
        self.assertEqual(port["status"], "FAULT")
        self.assertIsNone(port["faults"][0]["clear"])
        self.assertIn("ACTIVE", ba1(c))
        c.clock_offset += 1800.0
        c.tick()
        c.set_dim_port(1, False)
        port = c.dim_ports()[0]
        self.assertEqual(port["status"], "OK")
        fault = port["faults"][0]
        self.assertIsNotNone(fault["clear"])
        self.assertAlmostEqual(fault["hours"], 0.5, places=2)
        self.assertIn("0.50", ba1(c))

    def test_the_meters_on_a_down_port_stop_reporting(self):
        c = a_site()
        before = c.bir.totals.get(1, 0.0)
        c.set_dim_port(1, True)
        c.clock_offset += 60.0
        c.tick()
        self.assertEqual(c.bir.totals.get(1, 0.0), before)
        c.set_dim_port(1, False)
        c.set_dim_port(2, True)                       # not this meter's port
        c.clock_offset += 60.0
        c.tick()
        self.assertGreater(c.bir.totals.get(1, 0.0), before)

    def test_the_old_whole_link_flag_still_means_port_one(self):
        c = a_site()
        c.dim_fault = True
        self.assertIn("190301", c.conditions())
        self.assertFalse(c.dim_link_ok(1))

    def test_reset_forgets_the_history(self):
        c = a_site()
        c.set_dim_port(1, True)
        c.reset()
        self.assertEqual(c.dim_down, set())
        self.assertEqual(c.dim_faults, {})


if __name__ == "__main__":
    unittest.main()
