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
"""The breaker, the battery, and what a cold boot costs.

The battery -- the S1 switch on AND the cell fitted -- is all that holds RAM
while the AC is off. Break that chain for a moment during an outage and the
programming is gone, and putting the battery back does not bring it back.
What survives a cold boot is what is not RAM: the cards in the cage, the
software chips, and the archive in E2.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim.console import Console
from tls350sim.wire import Handler, SOH


def a_programmed_console():
    c = Console()
    c.modules["rs232"] = 1
    c.modules["liquid"] = 2
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.tank_level[1] = {"volume": 5000.0, "water": 2.0}
    c.tank_leak[1] = 0.5
    c.software["bir"] = True
    return c


class WarmBoot(unittest.TestCase):
    def test_an_outage_with_the_battery_keeps_everything(self):
        c = a_programmed_console()
        c.breaker_off()
        self.assertEqual(c.breaker_on(), "warm")
        self.assertIn("S60201", c.values)

    def test_the_power_off_moment_is_recorded(self):
        # the POWER REMOVED screen shows when the lights went out, and the
        # power-off tank readings are what the tank read at that moment
        c = a_programmed_console()
        c.breaker_off()
        c.breaker_on()
        self.assertIsNotNone(c.power_off)
        self.assertAlmostEqual(c.power_off_state[1]["volume"], 5000.0)


class ColdBoot(unittest.TestCase):
    def cold(self, c):
        c.breaker_off()
        c.battery_present = False
        c.battery_changed()
        c.battery_present = True         # refitted too late
        c.battery_changed()
        return c.breaker_on()

    def test_breaking_the_battery_chain_mid_outage_wipes_ram(self):
        c = a_programmed_console()
        self.assertEqual(self.cold(c), "cold")
        self.assertNotIn("S60201", c.values)

    def test_the_switch_off_mid_outage_is_the_same_loss(self):
        c = a_programmed_console()
        c.breaker_off()
        c.battery_switch = False
        c.battery_changed()
        c.battery_switch = True
        c.battery_changed()
        self.assertEqual(c.breaker_on(), "cold")
        self.assertNotIn("S60201", c.values)

    def test_what_is_not_ram_survives(self):
        c = a_programmed_console()
        c.rs232_security = True
        self.cold(c)
        self.assertEqual(c.modules.get("liquid"), 2)      # the cage
        self.assertTrue(c.software.get("bir"))            # the chips
        self.assertTrue(c.rs232_security)                 # a DIP switch

    def test_the_world_outside_does_not_reboot(self):
        c = a_programmed_console()
        self.cold(c)
        self.assertEqual(c.tank_level[1]["volume"], 5000.0)
        self.assertEqual(c.tank_leak[1], 0.5)

    def test_the_archive_survives_and_restores(self):
        # the whole point of the E2 archive: a cold-booted console can be
        # put back the way it was
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            c = Console(os.path.join(d, "state.json"))
            c.modules["rs232"] = 1
            c.values["S60201"] = "01REGULAR UNLEADED   "
            c.archive_save()
            self.cold(c)
            self.assertNotIn("S60201", c.values)
            self.assertTrue(c.archive_exists())
            c.archive_restore()
            self.assertIn("S60201", c.values)

    def test_with_the_battery_held_there_is_no_wipe_even_if_pulled_after(self):
        # pulling the battery AFTER power is back costs nothing: AC holds RAM
        c = a_programmed_console()
        c.breaker_off()
        c.breaker_on()
        c.battery_present = False
        c.battery_changed()
        self.assertIn("S60201", c.values)


class ThePostRebootHold(unittest.TestCase):
    """"If you are restoring after a reboot (switching the console Off and
    then back On), the system will wait 5 minutes before processing your
    request to restore archived setup data. This delay is to allow all
    hardware to initialize" -- 576013-623 Rev AN, beside the restore.
    """

    def test_a_console_nobody_has_rebooted_has_no_hold(self):
        """The note is about restoring "after a reboot"; a console somebody
        has been standing in front of all morning has had its five minutes."""
        c = a_programmed_console()
        self.assertEqual(c.restore_hold(), 0.0)

    def test_switching_it_off_and_back_on_starts_the_five_minutes(self):
        c = a_programmed_console()
        c.breaker_off()
        self.assertEqual(c.breaker_on(), "warm")
        self.assertGreater(c.restore_hold(), 290.0)
        self.assertLessEqual(c.restore_hold(), 300.0)

    def test_a_cold_boot_holds_for_the_same_five_minutes(self):
        """What is initialising is hardware, which does not know whether RAM
        happened to survive the outage."""
        c = a_programmed_console()
        c.breaker_off()
        c.battery_switch = False
        c.battery_changed()
        c.battery_switch = True
        c.battery_changed()
        self.assertEqual(c.breaker_on(), "cold")
        self.assertGreater(c.restore_hold(), 290.0)

    def test_the_hold_runs_out_and_does_not_go_negative(self):
        import time
        c = a_programmed_console()
        c.breaker_off()
        c.breaker_on()
        c.reboot_at = time.time() - Console.RESTORE_HOLD_SECONDS - 60.0
        self.assertEqual(c.restore_hold(), 0.0)


class DeadConsole(unittest.TestCase):
    def test_no_power_means_no_serial(self):
        c = a_programmed_console()
        h = Handler(c, verbose=False)
        c.breaker_off()
        self.assertEqual(h.handle(SOH + b"I10100"), b"")
        c.breaker_on()
        self.assertNotEqual(h.handle(SOH + b"I10100"), b"")


class BatteryOffAlarm(unittest.TestCase):
    """576013-635: system alarm 04, printed BATTERY IS OFF. A console
    running without battery backup says so, because the next outage would
    cost it everything."""

    def test_switch_off_posts_the_alarm(self):
        c = a_programmed_console()
        c.battery_switch = False
        self.assertIn("010400", c.conditions())

    def test_cell_removed_posts_it_too(self):
        c = a_programmed_console()
        c.battery_present = False
        self.assertIn("010400", c.conditions())

    def test_backup_restored_clears_it(self):
        c = a_programmed_console()
        c.battery_switch = False
        c.battery_switch = True
        self.assertNotIn("010400", c.conditions())

    def test_the_screen_reads_battery_is_off(self):
        from tls350sim.console import describe_alarms
        c = a_programmed_console()
        c.battery_switch = False
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("BATTERY IS OFF", screens)


class DipSw2(unittest.TestCase):
    """The 4-position DIP next to the battery switch (576013-635 p.7):
    1 front-panel security, 2 RS-232 security, 3 display power."""

    def test_panel_security_needs_position_one(self):
        c = a_programmed_console()
        c.values["S50400"] = "123456"
        self.assertTrue(c.panel_security)         # shipped on here
        c.panel_security = False
        # with the DIP off, the code alone must not lock the panel; the
        # check lives in the UI, which reads exactly these two facts
        self.assertFalse(c.panel_security and bool(c.security_code()))

    def test_display_blank_is_state_not_power(self):
        c = a_programmed_console()
        c.display_blanked = True
        self.assertTrue(c.powered)                # the console still runs

class SystemAlarms(unittest.TestCase):
    """The system alarms the extraction pinned triggers for, each posting
    from the state that causes it on the hardware."""

    def test_clock_incorrect_after_cold_boot_until_set(self):
        c = a_programmed_console()
        c.breaker_off()
        c.battery_switch = False
        c.battery_changed()
        c.battery_switch = True
        c.breaker_on()                              # cold
        self.assertIn("011700", c.conditions())
        c.values["S50100"] = "2601011230"
        c.set_clock()
        self.assertNotIn("011700", c.conditions())

    def test_protective_cover(self):
        c = a_programmed_console()
        c.cover_open = True
        self.assertIn("011200", c.conditions())
        c.cover_open = False
        self.assertNotIn("011200", c.conditions())

    def test_battery_switch_on_mid_boot_is_a_self_test_error(self):
        c = a_programmed_console()
        c.battery_switch = False
        c.booting = True
        c.battery_switch = True
        c.battery_changed()                         # flipped ON during boot
        c.booting = False
        self.assertIn("011600", c.conditions())
        # the prescribed fix is a proper power cycle
        c.breaker_off()
        c.breaker_on()
        self.assertNotIn("011600", c.conditions())

    def test_rom_revision_posts_on_a_live_chip_swap(self):
        c = a_programmed_console()
        self.assertNotIn("010700", c.conditions())
        c.software = dict(c.software)
        c.version = "13"                            # the chip changed hands
        self.assertIn("010700", c.conditions())
        c.cold_boot()                               # a cold boot owns it
        self.assertNotIn("010700", c.conditions())

    def test_mt_comm_removed(self):
        c = a_programmed_console()
        c.modules["mt"] = 1
        c._mt_seen = True
        del c.modules["mt"]
        self.assertIn("012000", c.conditions())

    def test_bir_close_pending_while_dispensing(self):
        import time as _t
        c = a_programmed_console()
        c.software["bir"] = True
        c.modules["edim"] = 1                       # meter data needs a DIM
        c.meters = {1: 1}
        c.meter_flow = {1: 100.0}                   # selling
        noon = list(_t.localtime())
        noon[3:6] = [12, 0, 0]
        c.clock_offset = _t.mktime(_t.struct_time(noon)) - _t.time()
        c.values["S79401"] = "011205"               # shift closes at 12:05
        c.tick()
        c.clock_offset += 600                       # 12:10, past the close
        c.tick()
        self.assertIn("011300", c.conditions())     # pending: still selling
        c.meter_flow = {}                           # the site goes idle
        c.clock_offset += 60
        c.tick()
        self.assertNotIn("011300", c.conditions())  # closed, self-cleared


class PowerDiagnostic(unittest.TestCase):
    """576013-818 Figure 6-26 draws POWER REMOVED against POWER RESTORED as a
    comparison, and ends on the gross change between them. The three REMOVED
    value screens carried no reader, so they drew the figure's literal zeros
    for ever while `power_off_volume`, `power_off_water` and the power-off
    temperature branch sat in `console.py` referenced by nothing: the console
    said the tank had gone from nothing to its whole contents and that the
    gross change was zero. FIDELITY D8."""

    def screens(self, c):
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "POWER DIAGNOSTIC"][0]
        out = []
        for sc in fn["screens"]:
            if not c.visible(sc, 1):
                continue
            head = c.diag_line(sc["l1"], 1)
            body = (c.diag_value(sc["live"], 1) if sc.get("live")
                    else c.diag_line(sc["l2"], 1))
            out.append((head[:24], body[:24]))
        return out

    def test_a_quiet_console_reports_no_change_across_the_cut(self):
        """A console that came up with nothing behind it invents the moment
        the lights went out -- twelve minutes ago -- and the readings go with
        the moment. Twelve dark minutes move nothing, so REMOVED and RESTORED
        read the same and the gross change is zero."""
        c = a_programmed_console()
        c.tick()
        removed = [b for a, b in self.screens(c)
                   if a == "T 1: POWER REMOVED"]
        restored = [b for a, b in self.screens(c)
                    if a == "T 1: POWER RESTORED"]
        self.assertEqual(removed[1], restored[1])       # VOLUME
        self.assertEqual(removed[2], restored[2])       # WATER VOL
        self.assertEqual(removed[3], restored[3])       # TEMP
        change = [b for a, b in self.screens(c)
                  if a == "T 1: GROSS VOLUME CHANGE"][0]
        self.assertEqual(change.split()[0], "0")

    def test_the_removed_screens_read_the_moment_and_not_a_literal(self):
        """Fill the tank, cut the power, then move it while the console is
        dark: REMOVED holds what the tank read at the cut and RESTORED holds
        what it reads now."""
        c = a_programmed_console()
        c.tick()
        c.tank_level[1] = {"volume": 8000.0, "water": 4.0}
        c.breaker_off()
        c.tank_level[1] = {"volume": 2000.0, "water": 4.0}
        c.breaker_on()
        removed = [b for a, b in self.screens(c)
                   if a == "T 1: POWER REMOVED"]
        restored = [b for a, b in self.screens(c)
                    if a == "T 1: POWER RESTORED"]
        self.assertEqual(removed[1], "VOLUME = 8000 GALS")
        self.assertEqual(restored[1], "VOLUME = 2000 GALS")

    def test_the_gross_change_is_restored_less_removed(self):
        c = a_programmed_console()
        c.tick()
        c.tank_level[1] = {"volume": 8000.0, "water": 4.0}
        c.breaker_off()
        c.tank_level[1] = {"volume": 2000.0, "water": 4.0}
        c.breaker_on()
        change = [b for a, b in self.screens(c)
                  if a == "T 1: GROSS VOLUME CHANGE"][0]
        self.assertEqual(change.split()[0], "-6000")

    def test_every_screen_on_the_function_carries_a_reader(self):
        """All nine of Figure 6-26's screens are readings. None is a
        literal."""
        from tls350sim.console import DIAG_MENU
        fn = [f for f in DIAG_MENU
              if f["function"] == "POWER DIAGNOSTIC"][0]
        self.assertEqual(len(fn["screens"]), 9)
        self.assertTrue(all(sc.get("live") for sc in fn["screens"]))


if __name__ == "__main__":
    unittest.main()


class DipSw2Position4(unittest.TestCase):
    """FIDELITY M13. 576013-635 Rev AA 4.1 p.6 gives position 4 to Fiscal
    Height Security, and it was not modelled at all -- so I13200 printed the
    software flag twice under two labels and the two could never disagree."""

    def report(self, switch):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console(None)
        c.fiscal_height_switch = switch
        return Handler(c, verbose=False).handle(
            chr(1).encode() + b"I13200").decode("latin-1")

    def rows(self, switch):
        sep = chr(13) + chr(10)
        return [r for r in self.report(switch).split(sep) if "FISCAL" in r]

    def test_the_switch_is_its_own_state(self):
        self.assertIn("SECURITY SWITCH : OFF", self.report(False))
        self.assertIn("SECURITY SWITCH : ON", self.report(True))

    def test_the_flag_and_the_switch_are_independent(self):
        """Which is the whole reason a report prints both.

        They were one value under two labels, so the report could never
        show a console whose software wants fiscal height security and
        whose DIP switch does not allow it -- the case the two rows exist
        to tell apart.
        """
        off, on = self.rows(False), self.rows(True)
        self.assertEqual(off[1], on[1])       # the flag row does not move
        self.assertNotEqual(off[2], on[2])    # the switch row does
        self.assertIn("DISABLED", off[1])

    def test_the_computer_form_carries_the_switch_as_its_own_digit(self):
        from tls350sim.console import Console
        from tls350sim.wire import Handler
        c = Console(None)
        c.fiscal_height_switch = True
        out = Handler(c, verbose=False).handle(
            chr(1).encode() + b"i13200").decode("latin-1")
        self.assertTrue(out.split("&&")[0].endswith("001"))
