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
"""The Leak Verification Procedure. FIDELITY H18.

576013-610 Rev AC chapter 29 gives the VLLD 3.0 gph failure its own operator
procedure, and until 2026-09-21 the word appeared NOWHERE in this
repository: `grep -rni "\\blvp\\b"` over the package, the tests and the tools
returned zero. The alarms existed and the shutdown existed; the operator's
answer to them did not, and that is the half a technician is trained on.

The chapter is one flow and this file walks it:

    a 3.0 gph test fails and shuts the pump down
    the console prints PERFORM LVP TEST
    ALARM/TEST silences the alarm and puts up P #: START LVP TEST
    ENTER runs the procedure
    it is interrupted, or it passes and enables the pump, or it fails and
    leaves the pump down

Nothing here needed a real console: the bench TLS-350 has PLLD boards in
slots 2 and 9 and no VLLD interface at all, so it cannot run one. Asked on
2026-09-21 -- `I102` names the cage and `I751`, `I752` and `I757` all come
back as bare headers.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import leaktest, printer                    # noqa: E402
from tls350sim.console import Console                      # noqa: E402

try:
    import tkinter
    tkinter.Tk().destroy()
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    HAVE_TK = False


def a_site(shutdown="01"):
    """One VLLD line on one tank, with a 3.0 gph shutdown rate.

    `751` is LINE CONFIG and `760` is the label -- `Console.LINE_CODES`
    gives VLLD as ("751", "760") -- and getting those the wrong way round
    is what made the first draft of the slip print `P1:` with no name.
    """
    c = Console(None)
    c.board = "E6"
    for key in ("probe", "vlld"):
        c.modules[key] = 1
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 5000.0, "water": 0.0}
    c.values["S60201"] = "UNLEADED"
    c.values["S75101"] = "1"                    # LINE CONFIG: switched on
    c.values["S76001"] = "UNLEADED SUPER"       # the line's label
    c.values["S75201"] = "1"                    # on tank 1
    c.values["S75701"] = shutdown               # 01 = 3.00 gph
    c.tick()
    c.tick()
    return c


def run_test(c, kind="vlld", device=1, rate_key="gross"):
    """Start a leak test and turn the clock until it is over.

    A line test is seconds (`LINE_SECONDS`) and an in-tank one is hours, so
    the clock moves in ten-minute steps and is given enough of them for the
    longest of them.
    """
    c.leaks.start(kind, device, rate_key)
    for _ in range(200):
        c.clock_offset += 600.0
        c.tick()
        if (kind, device) not in c.leaks.running:
            return
    raise AssertionError("the test never finished")


def slips(c):
    """The console's own printouts, as {what: [lines]}."""
    out = {}
    for title, body in printer.automatic(c):
        if "LVP" in title:
            out[title.split("LVP ")[1].split(" on")[0]] = body
    return out


class AFailedThreeGphTestAsksForTheProcedure(unittest.TestCase):
    """p.29-20: "it prints a report of the results and instructs you to
    perform the Line Verification Procedure"."""

    def test_a_shutdown_arms_the_procedure(self):
        c = a_site()
        c.line_leak[("vlld", 1)] = 5.0
        run_test(c)
        self.assertIn(("vlld", 1), c.leaks.disabled)
        self.assertEqual(c.leaks.lvp_pending(), ("vlld", 1))

    def test_the_console_prints_perform_lvp_test(self):
        c = a_site()
        c.line_leak[("vlld", 1)] = 5.0
        run_test(c)
        body = slips(c)["perform"]
        self.assertEqual(body[0], "PERFORM LVP TEST")
        self.assertEqual(len(body), 2, "the page draws the line and a stamp")

    def test_an_alarm_without_a_shutdown_asks_for_nothing(self):
        """"If you select a shutdown rate of 3.0 gph, then only a failed 3.0
        gph leak test will disable dispensing, while a failed 0.2 gph or 0.1
        gph leak test will just trigger an alarm." Nothing is disabled, so
        there is nothing to verify and nothing to re-enable."""
        c = a_site()
        c.line_leak[("vlld", 1)] = 0.5
        run_test(c, rate_key="periodic")
        self.assertNotIn(("vlld", 1), c.leaks.disabled)
        self.assertIsNone(c.leaks.lvp_pending())
        self.assertNotIn("perform", slips(c))

    def test_a_passing_test_asks_for_nothing(self):
        c = a_site()
        c.line_leak[("vlld", 1)] = 0.0
        run_test(c)
        self.assertIsNone(c.leaks.lvp_pending())

    def test_a_plld_shutdown_does_not_ask_for_one(self):
        """Chapter 29 gives this procedure to the VLLD family alone; the
        PLLD and WPLLD chapters have nothing like it."""
        c = a_site()
        c.leaks.disabled.add(("plld", 1))
        c.leaks._lvp_due("plld", 1, "gross")
        self.assertIsNone(c.leaks.lvp_pending())


class RunningTheProcedure(unittest.TestCase):

    def shut_down(self, c):
        c.line_leak[("vlld", 1)] = 5.0
        run_test(c)
        printer.automatic(c)            # drain the slips of the failure
        return c

    def test_enter_starts_it_and_the_console_says_so(self):
        """"Press ENTER to start the Line Verification Procedure. The system
        prints a message to confirm that it has started the test"."""
        c = self.shut_down(a_site())
        self.assertEqual(c.leaks.lvp_start(), "LVP TEST STARTED")
        self.assertEqual(c.leaks.lvp_running, ("vlld", 1))
        self.assertIn(("vlld", 1), c.leaks.running)
        self.assertIn("start", slips(c))

    def test_it_runs_the_three_gph_test(self):
        c = self.shut_down(a_site())
        c.leaks.lvp_start()
        self.assertEqual(c.leaks.running[("vlld", 1)].rate_key, "gross")

    def test_a_successful_run_enables_the_pump(self):
        """"If the LVP test is successful, the pump is enabled and the system
        prints a message that the test was successful"."""
        c = self.shut_down(a_site())
        c.line_leak[("vlld", 1)] = 0.0
        c.leaks.lvp_start()
        for _ in range(60):
            c.clock_offset += 60.0
            c.tick()
            if not c.leaks.lvp_running:
                break
        self.assertNotIn(("vlld", 1), c.leaks.disabled, "the pump stayed down")
        self.assertIsNone(c.leaks.lvp_pending())
        body = slips(c)["passed"]
        self.assertEqual(body[0], "STOP LINE LEAK TEST")
        self.assertEqual(body[1], "P1:UNLEADED SUPER")
        self.assertIn("TEST RESULT = 3.0 GAL/HR", body)
        self.assertIn("RESULT = PASSED", body)
        self.assertIn("SUBMERSIBLE PUMP 1", body)
        self.assertIn("ENABLED", body)

    def test_a_failed_run_leaves_the_pump_down(self):
        """"the pump remains disabled until you reenable it by running a
        successful Self test"."""
        c = self.shut_down(a_site())
        c.leaks.lvp_start()
        for _ in range(60):
            c.clock_offset += 60.0
            c.tick()
            if not c.leaks.lvp_running:
                break
        self.assertIn(("vlld", 1), c.leaks.disabled)
        body = slips(c)["failed"]
        self.assertIn("RESULT = FAILED", body)
        self.assertIn("DISABLED", body)
        self.assertNotIn("ENABLED", body)

    def test_the_procedure_is_still_owed_after_a_failure(self):
        c = self.shut_down(a_site())
        c.leaks.lvp_start()
        for _ in range(60):
            c.clock_offset += 60.0
            c.tick()
            if not c.leaks.lvp_running:
                break
        self.assertEqual(c.leaks.lvp_pending(), ("vlld", 1))


class TheThreeInterruptions(unittest.TestCase):
    """"If the LVP test is interrupted by the dispenser being on, a lockout
    time, or an in-tank leak test, the pump is disabled and the system prints
    out a message that the dispenser is on"."""

    def shut_down(self, c):
        c.line_leak[("vlld", 1)] = 5.0
        run_test(c)
        printer.automatic(c)
        c.line_leak[("vlld", 1)] = 0.0      # it would pass, if it could run
        return c

    def test_a_handle_up_interrupts_it(self):
        c = self.shut_down(a_site())
        c.lines.handle("vlld", 1, True)
        self.assertEqual(c.leaks.lvp_start(), "LVP TEST INTERRUPTED")
        self.assertIsNone(c.leaks.lvp_running)
        self.assertIn(("vlld", 1), c.leaks.disabled, "the pump was enabled")

    def test_the_interrupted_slip_is_the_page_s_own_shape(self):
        """The page names the line by LABEL ALONE here, with no `P 1:`
        prefix, where the other slips carry it. Kept as drawn."""
        c = self.shut_down(a_site())
        c.lines.handle("vlld", 1, True)
        c.leaks.lvp_start()
        body = slips(c)["interrupted"]
        self.assertEqual(body[0], "LVP TEST INTERRUPTED")
        self.assertEqual(body[1], "UNLEADED SUPER")
        self.assertEqual(body[3], "DISPENSER ON")

    def test_an_in_tank_test_interrupts_it(self):
        c = self.shut_down(a_site())
        c.leaks.start("tank", 1, "periodic")
        self.assertEqual(c.leaks.lvp_start(), "LVP TEST INTERRUPTED")
        self.assertEqual(slips(c)["interrupted"][3], "IN-TANK TEST ACTIVE")

    def test_the_procedure_has_to_be_begun_again(self):
        """"If someone lifts a handle, the system alarms and you will have to
        begin the procedure again"."""
        c = self.shut_down(a_site())
        c.lines.handle("vlld", 1, True)
        c.leaks.lvp_start()
        self.assertEqual(c.leaks.lvp_pending(), ("vlld", 1))
        c.lines.handle("vlld", 1, False)
        self.assertEqual(c.leaks.lvp_start(), "LVP TEST STARTED")

    def test_a_quiet_forecourt_does_not_interrupt_it(self):
        c = self.shut_down(a_site())
        self.assertIsNone(c.leaks.lvp_blocked("vlld", 1))


class TheLineTestDoesNotPrintATankSlip(unittest.TestCase):
    """Found while building the procedure, and pre-existing.

    `Engine.start` printed a START IN-TANK LEAK TEST slip for every kind
    that is not a line-PRESSURE kind, and VLLD is one of those -- so a VLLD
    test started at the panel printed an in-tank slip naming a TANK, for a
    test of a pipe. The LVP runs a VLLD test, so it would have printed one
    every time.
    """

    def test_a_vlld_test_prints_no_in_tank_slip(self):
        c = a_site()
        run_test(c)
        printed = [t for t, _b in printer.automatic(c) if "leak test" in t]
        self.assertEqual(printed, [])

    def test_a_tank_test_still_prints_both_of_its_slips(self):
        """The control: the slips that were right are still right."""
        c = a_site()
        run_test(c, kind="tank", device=1, rate_key="periodic")
        printed = [t for t, _b in printer.automatic(c) if "leak test" in t]
        self.assertTrue(any("started" in t for t in printed), printed)
        self.assertTrue(any("stopped" in t for t in printed), printed)


@unittest.skipUnless(HAVE_TK, "no display")
class ThePanelPutsTheScreenUp(unittest.TestCase):
    """p.29-20: "Press ALARM/TEST to silence the alarm. The system displays
    the message: P #: START LVP TEST / PRESS <ENTER>"."""

    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        cls.SimApp = SimApp

    def app(self, console):
        try:
            app = self.SimApp(console, 10001)
        except tkinter.TclError as exc:          # pragma: no cover
            raise unittest.SkipTest("no usable Tk: %s" % exc)
        self.addCleanup(app.destroy)
        self.addCleanup(app.quit)
        return app

    def shut_down(self):
        c = a_site()
        c.line_leak[("vlld", 1)] = 5.0
        run_test(c)
        return c

    def glass(self, app):
        return [app.lcd.itemcget(rid, "text").rstrip()
                for rid in app._text_ids]

    def test_alarm_test_puts_the_screen_up(self):
        app = self.app(self.shut_down())
        app.k_alarm()
        self.assertEqual(app.lvp_prompt, ("vlld", 1))
        self.assertEqual(self.glass(app),
                         ["P 1: START LVP TEST", "PRESS <ENTER>"])

    def test_enter_runs_it(self):
        c = self.shut_down()
        app = self.app(c)
        app.k_alarm()
        app.k_enter()
        self.assertIsNone(app.lvp_prompt)
        self.assertEqual(c.leaks.lvp_running, ("vlld", 1))

    def test_another_key_postpones_it_rather_than_cancelling_it(self):
        """The line is still shut down, so the procedure is still owed."""
        c = self.shut_down()
        app = self.app(c)
        app.k_alarm()
        app.k_mode()
        self.assertIsNone(app.lvp_prompt)
        self.assertEqual(c.leaks.lvp_pending(), ("vlld", 1))
        app.k_alarm()
        self.assertEqual(app.lvp_prompt, ("vlld", 1))

    def test_a_console_with_nothing_shut_down_gets_no_screen(self):
        app = self.app(a_site())
        app.k_alarm()
        self.assertIsNone(app.lvp_prompt)


if __name__ == "__main__":
    unittest.main()
