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
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_controls import send                      # noqa: E402
from tests.test_console import a_tank, float_value        # noqa: E402
from tls350sim import autotx                              # noqa: E402
from tls350sim.console import Console, FIELDS, describe_alarms
from tls350sim.wire import Handler                        # noqa: E402


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

    def test_the_console_addresses_eight_receivers(self):
        """576013-623 Rev AN: "Press CHANGE twice to configure one
        receiver. To configure additional receivers, press the Right-Arrow
        key, then press CHANGE for up to seven more receivers." One and
        seven more. The tape settles it from the other end -- its console's
        only receiver is D 8 -- and this held six. FIDELITY T4."""
        self.assertEqual(dialing_console().receivers(),
                         [1, 2, 3, 4, 5, 6, 7, 8])

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


class TheReceiverMatrixDecidesWhoIsCalled(unittest.TestCase):
    """FIDELITY N6. 576013-623 Rev AN ch.6 and function 52C give each
    receiver its own alarm assignment -- eighteen groups, then per alarm NO
    TANKS, ALL TANKS or a single tank -- and `receiver_alarms` stored it
    faithfully. `autodial.tick()` ignored it: any fresh condition dialled,
    and it always dialled `receivers()[0]`, so the table was decorative and
    receivers 2 to 8 were never called.
    """

    def a_console_with_three_receivers(self):
        c = dialing_console()
        for n in (1, 2, 3):
            c.values[f"S521{n:02d}"] = f"{n:02d}1"
            c.values[f"S523{n:02d}"] = f"{n:02d}555000000{n}"
        return c

    def called(self, c):
        return [entry[1] for entry in c.autodial.log if entry[2] == "answered"]

    def test_an_alarm_goes_to_the_receiver_it_is_assigned_to(self):
        c = self.a_console_with_three_receivers()
        c.receiver_alarms[2] = [("01", "12", "00")]   # the cover alarm
        c.cover_open = True
        c.tick()
        self.assertEqual(self.called(c), [2])

    def test_an_alarm_nobody_asked_for_dials_nobody(self):
        c = self.a_console_with_three_receivers()
        c.receiver_alarms[2] = [("02", "02", "01")]   # a tank 1 leak only
        c.cover_open = True
        c.tick()
        self.assertEqual(self.called(c), [])

    def test_all_tanks_matches_any_device(self):
        """"NO TANKS, ALL TANKS or a single tank", and TT of 00 is ALL --
        the same convention the wire uses everywhere else."""
        c = self.a_console_with_three_receivers()
        c.receiver_alarms[3] = [("02", "02", "00")]
        self.assertEqual(c.autodial.wanted_by("020207"), [3])

    def test_a_single_tank_assignment_does_not(self):
        c = self.a_console_with_three_receivers()
        c.receiver_alarms[3] = [("02", "02", "01")]
        self.assertEqual(c.autodial.wanted_by("020201"), [3])
        self.assertEqual(c.autodial.wanted_by("020202"), [])

    def test_two_receivers_wanting_it_are_both_called(self):
        """One pending call at a time, and the rest queue behind it."""
        c = self.a_console_with_three_receivers()
        c.receiver_alarms[2] = [("01", "12", "00")]
        c.receiver_alarms[3] = [("01", "12", "00")]
        c.cover_open = True
        c.tick()
        c.tick()
        self.assertEqual(sorted(self.called(c)), [2, 3])
        self.assertEqual(c.autodial.queue, [])

    def test_an_unprogrammed_table_still_calls_the_first_receiver(self):
        """The one invention, and it is marked as one: an empty table is an
        UNPROGRAMMED console rather than one that has said "call nobody",
        and the manuals describe programming the list without saying what a
        console does before anybody has."""
        c = self.a_console_with_three_receivers()
        self.assertFalse(c.autodial.assigned())
        c.cover_open = True
        c.tick()
        self.assertEqual(self.called(c), [1])


class RemoteDisplay(unittest.TestCase):
    def a_console_that_can_take_one(self):
        """V32 is the last version the tables give the Remote Display.

        The default console is V33, where the row is dashed, so this test
        cannot use one: see `test_the_card_is_discontinued_at_v33` below.
        """
        c = Console()
        c.set_version(32)
        return c

    def test_a_faulted_link_posts_system_alarm_08(self):
        c = self.a_console_that_can_take_one()
        c.modules["rdu"] = 1
        c.rdu_fault = True
        self.assertIn("010800", c.conditions())
        c.rdu_fault = False
        self.assertNotIn("010800", c.conditions())

    def test_no_module_no_alarm(self):
        c = self.a_console_that_can_take_one()
        c.rdu_fault = True
        self.assertNotIn("010800", c.conditions())

    def test_the_card_is_discontinued_at_v33(self):
        """FIDELITY M14. Table 3-4 dashes the Remote Display at version 33,
        and Table 3-5 -- "Version 34 and Higher" -- has no row for it at all.

        Nothing was reading that, so the console this simulator ships as
        would take a card its own software no longer knows about. It is the
        Cap probes' shape, not the Remote Printer's: the gate is at the top
        of the range."""
        self.assertTrue(self.a_console_that_can_take_one().knows_module("rdu"))
        for version in (33, 34):
            c = Console()
            c.set_version(version)
            self.assertFalse(c.knows_module("rdu"),
                             "V%d still takes a Remote Display" % version)

    def test_a_card_the_software_cannot_drive_cannot_fault(self):
        """Which is what the gate is for, on the default console."""
        c = Console()
        c.modules["rdu"] = 1
        c.rdu_fault = True
        self.assertEqual(c.count("rdu"), 0)
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


class TheMaintenanceTrackerKey(unittest.TestCase):
    """576013-610 chapter 33 is the whole log-in sequence and its four
    refusals, and 576013-818 Figure 6-4 is what a blocked key does to the
    key list. Nothing here could present a key at all, so 8A3 always
    answered an empty list and the diagnostic showed three names copied off
    the figure. FIDELITY D13."""

    def a_console(self):
        c = Console()
        c.board = "E6"                    # ECPU2 with an NVMEM203, for MT
        c.modules = {"probe": 1, "rs232": 1, "mt": 1}
        return c

    def test_a_valid_key_logs_in_and_joins_the_active_list(self):
        c = self.a_console()
        self.assertEqual(c.tracker_keys(), [])
        self.assertEqual(c.present_tracker_key("A12345", "J SMYTHE"),
                         "LOGGED IN A12345")
        self.assertEqual(c.tracker_keys(), [("A12345", "J SMYTHE")])
        self.assertTrue(c.mt_logged_in())
        c.remove_tracker_key()
        self.assertFalse(c.mt_logged_in())
        # and it is still an active key afterwards
        self.assertEqual(c.tracker_keys(), [("A12345", "J SMYTHE")])

    def test_the_four_refusals(self):
        """"KEY EXPIRED (your TLS certification has expired), KEY BLOCKED
        (your key has been blocked), KEY INVALID ... or LOG-IN RECORD ERROR
        - key was read but system was unable to write a log-in record to
        FPROM - (this usually a problem with the console system date/time)."
        """
        c = self.a_console()
        self.assertEqual(c.present_tracker_key("A1", "SHORT"), "KEY INVALID")
        self.assertEqual(c.present_tracker_key("A12345", "J DOE", True),
                         "KEY EXPIRED")
        c.block_tracker_key("A54321", "J DOE")
        self.assertEqual(c.present_tracker_key("A54321", "J DOE"),
                         "KEY BLOCKED")
        c.clock_set = False               # a cold boot nobody has set since
        self.assertEqual(c.present_tracker_key("A12345", "J SMYTHE"),
                         "LOG-IN RECORD ERROR")
        self.assertEqual(c.tracker_keys(), [])

    def test_blocking_a_key_moves_it_between_the_two_lists(self):
        """8A3 is the active list and 8A4 is the blocked one, and they are
        the same template meaning opposite things."""
        c = self.a_console()
        c.present_tracker_key("A12345", "J SMYTHE")
        c.present_tracker_key("A54321", "J DOE")
        c.block_tracker_key("A54321")
        self.assertEqual(c.tracker_keys(), [("A12345", "J SMYTHE")])
        self.assertEqual(c.blocked_tracker_keys(), [("A54321", "J DOE")])
        # the label comes with it rather than being thrown away
        self.assertEqual(c.present_tracker_key("A54321", "J DOE"),
                         "KEY BLOCKED")

    def test_the_panel_and_the_port_agree(self):
        """The diagnostic invented a six-digit number and blocked nothing,
        so a key blocked on the panel was still on 8A3's list and missing
        from 8A4's. D7's defect in a second function."""
        from tls350sim.wire import Handler
        c = self.a_console()
        c.present_tracker_key("A12345", "J SMYTHE")
        c.mt_pending = "A12345"
        self.assertEqual(c.diag_action("mt_block"), "ARE YOU SURE?: YES")
        self.assertEqual(c.tracker_keys(), [])
        h = Handler(c, verbose=False)
        reply = h.handle((chr(1) + "I8A400" + chr(13)).encode()).decode("latin-1")
        self.assertIn("MAINTENANCE TRACKER BLOCK HARDWARE KEY", reply)
        self.assertIn("J SMYTHE", reply)
        self.assertIn("A12345", reply)

    def test_the_active_list_is_what_the_wire_answers(self):
        from tls350sim.wire import Handler
        c = self.a_console()
        c.present_tracker_key("A12345", "J SMYTHE")
        h = Handler(c, verbose=False)
        reply = h.handle((chr(1) + "I8A300" + chr(13)).encode()).decode("latin-1")
        rows = reply.strip(chr(1) + chr(3) + chr(13) + chr(10)).split(chr(13) + chr(10))
        keyed = [r for r in rows if "A12345" in r]
        self.assertEqual(len(keyed), 1, reply)
        # "the label left in twenty and the ID at column 21", measured off
        # the rendered page: LABEL at x=72 and ID at x=191.9
        self.assertEqual(keyed[0], "J SMYTHE" + " " * 12 + "A12345")

    def test_nothing_presents_a_key_on_a_console_without_the_card(self):
        c = self.a_console()
        c.modules = {"probe": 1, "rs232": 1}
        from tls350sim.wire import Handler
        h = Handler(c, verbose=False)
        for code in ("I8A300", "I8A400"):
            reply = h.handle((chr(1) + code + chr(13)).encode())
            self.assertIn(b"9999", reply, code)


class TheAutoDialMethodIsItsOwnScreen(unittest.TestCase):
    """FIDELITY N6a. 576013-623 Rev AN p.6-11 draws two screens where
    COMMUNICATIONS SETUP had one, and the one it had wore the other one's
    name:

        AUTO DIAL METHOD          ALL RCVRS
        ALL PHONES                ON DATE

    The console's screen read `D1:` over `AUTO DIAL METHOD:` with nothing
    after the colon, and it was programming 52B -- the auto dial TYPE AND
    START TIME. The method is 529, "Set Fax Auto Dial Method", `f -
    1=SINGLE PHONE 0=ALL PHONES`, and it had no field, no step and no
    reader.
    """

    def a_console(self):
        from tls350sim.console import Console
        c = Console()
        c.board = "E6"
        c.modules["modem"] = 1
        c.modules["rs232"] = 1
        return c

    def steps(self, c):
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU
              if f["function"] == "COMMUNICATIONS SETUP"][0]
        return fn, [st["text"] for st in c.visible_steps(fn, 1)]

    def lines(self, c, code, device=1):
        from tls350sim import screens
        from tls350sim.console import SETUP_MENU
        fn = [f for f in SETUP_MENU
              if f["function"] == "COMMUNICATIONS SETUP"][0]
        st = next(s for s in fn["steps"] if s.get("code") == code)
        return screens.setup_lines(c, fn, st, device)

    def test_the_method_has_a_screen_of_its_own(self):
        c = self.a_console()
        _fn, texts = self.steps(c)
        self.assertIn("Auto Dial Method (All Phones/Single Phone)", texts)
        self.assertEqual(self.lines(c, "S52900"),
                         ["AUTO DIAL METHOD", "ALL PHONES"])

    def test_and_the_frequency_screen_is_headed_by_the_method(self):
        """"If you choose Single Phone, the phrase 'ALL RCVR' is replaced on
        each screen by the selected receiver number (RCVR n)", drawn as
        `SINGLE RCVR: D1`."""
        c = self.a_console()
        c.values["S52B01"] = "0150630"                # daily, 06:30
        self.assertEqual(self.lines(c, "S52B01"), ["ALL RCVRS", "DAILY"])
        c.values["S52900"] = "1"
        self.assertEqual(self.lines(c, "S52B01"),
                         ["SINGLE RCVR: D1", "DAILY"])
        # and every receiver's own head, though only D1 has a frequency
        self.assertEqual(self.lines(c, "S52B01", 3)[0], "SINGLE RCVR: D3")

    def test_the_frequency_screen_shows_a_word_not_a_payload(self):
        """A list field's value is a packed run whose shape the data itself
        decides, so `fieldio` had no width to decode it with and the panel
        drew the payload. `_dial_text` has produced the word for the wire's
        own report all along."""
        c = self.a_console()
        c.values["S52B01"] = "01" + "2" + "06" + "1" + "5" + "0800"
        self.assertEqual(self.lines(c, "S52B01")[1], "ANNUALLY")

    def test_the_wire_answers_the_manuals_own_one_line_sample(self):
        """576013-635 Rev AA p.192's Display sample is the stamp and
        `ALL PHONES`, one line. `wiretitles.json` had filed ALL PHONES as
        the report's TITLE -- it is read off the sample, and a response
        whose whole body is one line gives the extractor nothing to tell a
        title from a value -- so the console answered it twice over,
        `ALL PHONES` and then `SINGLE PHONE`."""
        from tls350sim.wire import Handler
        c = self.a_console()
        h = Handler(c, verbose=False)

        def ask(cmd):
            out = h.handle((chr(1) + cmd + chr(13)).encode())
            # the frame is the echoed code, the stamp, then the body
            return [l for l in out.decode("latin-1").splitlines()
                    if l.strip(chr(1) + chr(3) + " ")][2:]

        self.assertEqual([l.strip(chr(3)) for l in ask("I52900")],
                         ["ALL PHONES"])
        ask("S529001")
        self.assertEqual([l.strip(chr(3)) for l in ask("I52900")],
                         ["SINGLE PHONE"])


class TwoVMCIModulesIsAWarning(unittest.TestCase):
    """"SETUP WARN -- More than one VMCI module is installed. The VMCI
    module in the higher comm port must be removed", 576013-610 Rev AC
    Table 29-22. The cage took one, so the warning could not be raised.
    FIDELITY M12."""

    def test_two_of_them_warn_against_the_higher_one(self):
        c = Console()
        c.modules = {"probe": 1, "vmc": 2}
        c.tick()
        screens = [a["screen"] for a in describe_alarms(c.conditions())]
        self.assertIn("X 2:SETUP DATA WARNING", screens)
        self.assertNotIn("X 1:SETUP DATA WARNING", screens)

    def test_and_pulling_it_clears_the_warning(self):
        """"a Setup Data Warning ... will remain active until the cause has
        been corrected", and the correction is the manual's own."""
        c = Console()
        c.modules = {"probe": 1, "vmc": 2}
        c.tick()
        self.assertTrue(describe_alarms(c.conditions()))
        c.set_module("vmc", 1)
        c.tick()
        self.assertEqual(describe_alarms(c.conditions()), [])

    def test_the_vmci_and_vmc_alarm_types_are_the_manual_s(self):
        """Categories 35 and 36 were in the alarm LABEL table and missing
        from the status table, so every VMCI and VMC alarm described itself
        as `ALARM TYPE nn`."""
        from tls350sim.console import STATUS_TYPES
        self.assertEqual(STATUS_TYPES["35"]["02"], "COMMUNICATION ALARM")
        self.assertEqual(STATUS_TYPES["36"]["01"], "VMC COM TIMEOUT ALARM")


class TheSatelliteBoardOwnsItsOwnSetting(unittest.TestCase):
    """889 is "DTR Normal State for Serial Satellite Boards" and answered on
    a console with no satellite in it -- the one place this file's "pull the
    card and its commands answer 9999" was vacuous. The tape prints the
    setting under its S-SAT board and under neither of the other two, and
    576013-635 draws the same two lines together. FIDELITY S7a."""

    def ask(self, c, cmd):
        from tls350sim.wire import Handler
        return Handler(c, verbose=False).handle(
            (chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def a_console(self, **cards):
        c = Console()
        c.modules = dict({"probe": 1, "rs232": 1}, **cards)
        return c

    def test_no_satellite_no_dtr(self):
        self.assertIn("9999", self.ask(self.a_console(), "I88901"))
        self.assertIn("9999", self.ask(self.a_console(), "S889010"))

    def test_a_satellite_answers(self):
        for card in ("ssat", "asat"):
            c = self.a_console(**{card: 1})
            self.assertNotIn("9999", self.ask(c, "S889010"), card)
            self.assertIn("DTR NORMAL STATE", self.ask(c, "I88901"), card)

    def test_and_so_does_the_dual_port_one(self):
        """330586-015 and -016 are satellites on their DB-9 halves, and the
        console reads a port by what is on it."""
        c = self.a_console(ssat4=1)
        c.board = "E6"
        self.assertNotIn("9999", self.ask(c, "S889010"))
        self.assertIn("DTR NORMAL STATE", self.ask(c, "I88901"))

    def test_the_screen_belongs_to_the_satellite_port(self):
        """And on the panel it is one board's screen, not every board's:
        the tape prints it under `COMM BOARD  : 1 (S-SAT )` and under
        neither of the two above it."""
        from tls350sim.console import SETUP_MENU
        c = self.a_console(ssat=1, modem=1)
        step = [st for fn in SETUP_MENU
                if fn["function"] == "COMMUNICATIONS SETUP"
                for st in fn["steps"] if st.get("code") == "S88901"][0]
        ports = {c.comm_board_name(n).strip(): n for n in c.comm_positions()}
        self.assertTrue(c.visible(step, ports["S-SAT"]))
        self.assertFalse(c.visible(step, ports["FXMOD"]))
        self.assertFalse(c.visible(step, ports["RS-232"]))


class TheTwoCodesDrawnUnderACommBoardHeader(unittest.TestCase):
    """887 and 889 are answered under `COMM BOARD  : n (TYPE)`, and neither
    drew it.

    576013-635 Rev AA p.473 and p.475 both print the pair -- the position the
    setting belongs to, then the setting -- and the console's own 888 report
    has drawn that header from `comm_board_name` all along.

    They failed in opposite directions. 887 printed the manual's SAMPLE
    board, `COMM BOARD  : 3 (FXMOD)`, whatever position was asked and
    whatever card was in it, because the generator files a response's first
    line as its title and that is what a first line looks like; and its own
    value came back as a bare `0` where the page prints
    `DIAL TONE VALIDATION INTERVAL:   32 HOURS`. 889 had the labelled line
    and no header at all, because the `wire_line` path answers before a title
    is ever considered. See FIDELITY S15 and S18.
    """

    def a_console(self):
        c = Console()
        c.modules = {"probe": 1, "rs232": 1, "modem": 1, "ssat": 1}
        c.tick()
        return c

    def ask(self, c, cmd):
        return Handler(c, verbose=False).handle(
            (chr(1) + cmd + chr(13)).encode()).decode("latin-1")

    def test_the_header_names_the_port_that_was_asked_for(self):
        c = self.a_console()
        for port in c.comm_positions():
            want = f"COMM BOARD  : {port} ({c.comm_board_name(port)})"
            self.assertIn(want, self.ask(c, f"I887{port:02d}"))

    def test_the_header_is_not_the_manuals_own_sample_board(self):
        """`3 (FXMOD)` is p.473's site, not this console's."""
        c = self.a_console()
        port = c.comm_ports_for("rs232")[0]
        self.assertNotIn("COMM BOARD  : 3 (FXMOD)",
                         self.ask(c, f"I887{port:02d}"))

    def test_the_interval_is_a_labelled_line_with_its_unit(self):
        c = self.a_console()
        c.values["S88701"] = "0032"
        reply = self.ask(c, "I88701")
        self.assertIn("DIAL TONE VALIDATION INTERVAL:   32 HOURS", reply)

    def test_the_satellites_setting_carries_the_header_too(self):
        c = self.a_console()
        port = c.comm_ports_for("ssat")[0]
        reply = self.ask(c, f"I889{port:02d}")
        self.assertIn(f"COMM BOARD  : {port} (S-SAT )", reply)
        self.assertIn("DTR NORMAL STATE:", reply)


class TheCommBayHasSlots(unittest.TestCase):
    """577013-528 Rev G heads its two tables "Comm Modules That Can Be
    Installed In Slots 1, 2, or 3" and "... In Slot 4", and 576013-818 Rev
    AB's Table 7-2 makes getting it wrong a troubleshooting entry. Until
    there was a slot to put a card in, neither symptom could be reproduced
    and neither corrective procedure could be practised. FIDELITY M7."""

    def a_console(self):
        c = Console()
        c.modules = {"probe": 1, "rs232": 1}
        return c

    def test_a_card_sits_in_a_slot_and_can_be_moved(self):
        c = self.a_console()
        self.assertEqual(c.comm_layout(), {1: "rs232"})
        self.assertTrue(c.place_comm("rs232", 4))
        self.assertEqual(c.comm_layout(), {4: "rs232"})

    def test_an_rs232_module_in_slot_4_does_not_communicate(self):
        """"System will not communicate via RS-232 Module -- RS-232 Module
        in slot 4 of Comm Bay card cage -- Move Module to Comm Cage slots 1,
        2, or 3." The card is still in the cage and still reads its own ID
        resistance; what it does not do is answer."""
        from tls350sim.wire import Handler
        c = self.a_console()
        h = Handler(c, verbose=False)
        ask = lambda: h.handle((chr(1) + "I50100" + chr(13)).encode())
        self.assertTrue(ask(), "a console with an RS-232 card should answer")
        c.place_comm("rs232", 4)
        self.assertFalse(c.comm_slot_works(4))
        self.assertEqual(ask(), b"")
        # and the corrective procedure is the chart's own
        c.place_comm("rs232", 2)
        self.assertTrue(ask())

    def test_a_modem_in_slot_4_does_not_dial(self):
        """"System will not communicate via internal SiteFax Module --
        Modem Module in slot 4 of Comm Bay card cage."
        """
        c = dialing_console()
        c.modules["rs232"] = 0
        self.assertTrue(c.autodial.enabled())
        c.place_comm("modem", 4)
        self.assertFalse(c.autodial.enabled())

    def test_a_dual_port_module_wants_its_harness(self):
        """"Before installing the dual-port module in slot 4 of the Comm
        bay, connect the 4-pin connector (of the included wiring harness) to
        J4 on the Module. After the module is installed, connect the 8-pin
        connector of the harness to J6 on the ECPU/ECPU2 board."
        """
        c = self.a_console()
        c.board = "E6"
        c.modules = {"rs485": 1}
        self.assertTrue(c.comm_slot_works(4))
        c.dual_harness = False
        self.assertFalse(c.comm_slot_works(4))
        self.assertFalse(c.serial_port_works())

    def test_one_card_is_two_ports_and_two_identities(self):
        """The multiport is an RS-485 port on one half and an RS-232 port on
        the other: "Serial port for consoles requiring more than 3 Comm
        modules", which is what 577013-819 plugs a laptop into.
        """
        c = self.a_console()
        c.board = "E6"
        c.modules = {"rs485": 1}
        self.assertEqual(c.fitted("rs485"), 1)
        self.assertEqual(c.count("rs485"), 1)
        self.assertEqual(c.count("rs232"), 1)
        self.assertEqual(c.comm_ports_for("rs232"), [6])
        self.assertTrue(c.serial_port_works())

    def test_both_halves_read_their_own_id_resistance(self):
        """Each dual-port row in 577013-528 carries two resistors and two
        connectors, and SYSTEM CONFIGURATION is where a technician compares
        them: 82.5K on the RJ-45 half of the multiport and 15K on its DB-9.
        """
        c = self.a_console()
        c.board = "E6"
        c.modules = {"rs485": 1}
        rows = {-slot: (name, por) for slot, _key, name, por, _now
                in c.slot_readings() if slot < 0}
        self.assertEqual(rows[5][0], "RS-485")
        self.assertEqual(rows[6][0], "RS-232")
        self.assertTrue(81000 < rows[5][1] < 86000, rows[5])
        self.assertTrue(14700 < rows[6][1] < 15500, rows[6])
        self.assertEqual(rows[1][0], "UNUSED")

    def test_a_card_the_software_predates_is_still_read_in_its_slot(self):
        """The ID resistor is measured by hardware. A console whose board
        cannot drive a Maintenance Tracker still reads one in the bay and
        still prints its port -- which is the tape, exactly: MTCOMM on comm
        6 and not one Maintenance Tracker screen anywhere on the roll."""
        c = self.a_console()
        c.modules = {"mt4": 1}
        self.assertFalse(c.knows_module("mt4"))
        self.assertEqual(c.comm_layout(), {4: "mt4"})
        self.assertEqual(c.comm_board_name(6), "MTCOMM")
        self.assertEqual(c.count("mt"), 0)


class TheCommunicationStatusReport(unittest.TestCase):
    """888, which drew two of the eleven lines 576013-635 Rev AA p.474 draws.

    The block per port is a block per ERROR -- "nn - Number of Errors to
    follow for each port" -- and the four UART lines are the settings "During
    Error", which is why the sample's port 1 prints its connection and
    nothing else.
    """

    def a_console(self):
        from tests.test_controls import a_site
        return a_site()

    def test_device_00_answers_for_every_port_in_the_bay(self):
        """"PP - Communication Port Number (00=all)", and this answered for
        port 1 alone on a console with three cards in the bay."""
        c, h = self.a_console()
        reply = send(h, "I88800")
        for position in sorted(c.comm_positions()):
            self.assertIn(f"COMM BOARD  : {position} ", reply)

    def test_a_port_with_no_errors_prints_its_connection_and_stops(self):
        _c, h = self.a_console()
        reply = send(h, "I88801").replace(chr(1), "").replace(chr(3), "")
        self.assertIn("COMM BOARD  : 1 (RS-232)", reply)
        self.assertNotIn("BAUD RATE", reply)

    def test_a_port_that_has_never_carried_data_has_no_connection(self):
        """p.474's own pair, and it is what says the connect type is the
        LAST connection rather than this instant: port 1 reads
        `CONNECTION : NONE` and has no TIME OF LAST COMM DATA line under it,
        while port 2 reads MODEM DIAL IN over a stamp of 9:12 AM. Port 3
        here is the one nothing has ever talked to."""
        _c, h = self.a_console()
        reply = send(h, "I88800").replace(chr(1), "").replace(chr(3), "")
        block = reply.split("COMM BOARD  : 3 ")[1]
        self.assertIn(" CONNECTION : NONE", block)
        self.assertNotIn("TIME OF LAST COMM DATA", block)

    def test_the_port_the_command_arrived_on_says_rs232_request(self):
        """The stamp and the connect type are the same event seen twice:
        "06=RS232 REQUEST" is what a port serving a command is doing, and
        888 was answering NONE on every port whatever had happened.
        FIDELITY S13."""
        _c, h = self.a_console()
        reply = send(h, "I88801").replace(chr(1), "").replace(chr(3), "")
        self.assertIn(" CONNECTION : RS232 REQUEST", reply)
        self.assertIn("TIME OF LAST COMM DATA", reply)

    def test_the_computer_format_carries_the_same_connect_type(self):
        """`PPnnCC`, and CC was the literal `00` on every port."""
        _c, h = self.a_console()
        body = send(h, "i88801").replace(chr(1), "").replace(chr(3), "")
        self.assertIn("010006", body)

    def test_an_error_prints_the_manuals_eight_lines(self):
        """p.474's own block, and the UART settings are the PORT's rather
        than the 9600/odd/1/8 this answered for every port whatever was
        programmed."""
        c, h = self.a_console()
        c.values["S88102"] = "02400117"          # 2400, odd, 1 stop, 7 data
        c.comm_errors[2] = [{"state": 0, "error": 1}]
        c.comm_data_at[2] = time.mktime((1996, 1, 1, 9, 12, 0, 0, 0, -1))
        c.comm_error_at[2] = time.mktime((1996, 1, 1, 8, 0, 0, 0, 0, -1))
        rows = send(h, "I88802").replace(chr(1), "").replace(
            chr(3), "").splitlines()
        for want in ("COMM BOARD  : 2 (FXMOD)",
                     " FUNCTION   : NONE",
                     " ERROR      : UART SETTINGS ERROR",
                     " BAUD RATE  : 2400",
                     " PARITY     : ODD",
                     " STOP BIT   : 1 STOP",
                     " DATA LENGTH: 7 DATA",
                     "TIME OF LAST COMM DATA:  JAN  1, 1996  9:12 AM",
                     "TIME OF LAST COMM ERROR: JAN  1, 1996  8:00 AM"):
            self.assertIn(want, rows)

    def test_the_computer_format_carries_the_ports_own_uart(self):
        """`PP nn CC [SS EE BBBBB P S D] YYMMDDHHmm YYMMDDHHmm`, and the
        UART digits were hard coded."""
        c, h = self.a_console()
        c.values["S88102"] = "02400117"
        c.comm_errors[2] = [{"state": 0, "error": 1}]
        body = send(h, "i88802").replace(chr(1), "").replace(chr(3), "")
        self.assertIn("0201" + "00" + "00" + "01" + "02400" + "1" + "1" + "7",
                      body)
        self.assertNotIn("09600", body)

    def test_the_port_a_tool_talks_on_remembers_that_it_did(self):
        """Half of what 888 reports is when a port last carried data, and
        this simulator's socket IS the console's RS-232 port."""
        c, h = self.a_console()
        self.assertEqual(c.comm_data_at, {})
        send(h, "I10100")
        self.assertIn(c.rs232_port(), c.comm_data_at)


if __name__ == "__main__":
    unittest.main()


class TheClearSideOfTheAutodial(unittest.TestCase):
    """FIDELITY N6a. 52E had no moment to apply to.

    576013-635 gives it a page of its own -- `Set Delay for Autodial on
    Alarm Clear`, whose report heads itself `RECEIVER CLEARED ALARMS REPORT
    DELAY PERIOD` -- and nothing in the engine distinguished an alarm going
    away from one arriving, so the whole function was unread.

    An alarm CLEARING is routed the same way as any other event a receiver
    can be called about: as an assignment. 576013-623 Rev AN Table 6-2
    groups `ALARM CLEAR WARNING` under "Receiver Alarms" beside SERVICE
    REPORT WARN and DELIVERY REPORT WRN, and the alarm list on p.6-9 numbers
    it 14/04. It is not an alarm the console raises -- nothing would ever
    raise it -- it is the site saying "call me when one goes away".
    """

    def a_console_that_wants_calling_on_a_clear(self, delay="03"):
        c = dialing_console()
        c.values["S52101"] = "011"
        c.values["S52301"] = "015551234"
        c.values["S52E01"] = "01" + delay
        c.receiver_alarms[1] = [("14", "04", "00")]
        return c

    def raise_then_clear(self, c):
        """Put an alarm up, tick, take it away, tick."""
        c.cover_open = True
        c.tick()
        c.cover_open = False
        c.tick()

    def test_a_clear_holds_the_dial_for_the_receivers_delay(self):
        c = self.a_console_that_wants_calling_on_a_clear()
        self.raise_then_clear(c)
        self.assertEqual([r for _at, r in c.autodial.holding], [1])
        self.assertEqual([e[1] for e in c.autodial.log
                          if e[2] == "answered"], [])

    def test_and_dials_once_the_delay_is_up(self):
        c = self.a_console_that_wants_calling_on_a_clear()
        self.raise_then_clear(c)
        c.autodial.holding = [(0.0, 1)]         # the delay has run out
        c.tick()
        self.assertIn(1, [e[1] for e in c.autodial.log if e[2] == "answered"])

    def test_a_receiver_with_no_delay_dials_at_once(self):
        """The field takes 0, and 0 is not "never" -- it is no wait."""
        c = self.a_console_that_wants_calling_on_a_clear(delay="00")
        self.raise_then_clear(c)
        self.assertIn(1, [e[1] for e in c.autodial.log if e[2] == "answered"])

    def test_an_alarm_ARRIVING_does_not_use_the_clear_route(self):
        """Which is the distinction the entry says was missing: the same
        receiver is assigned 14/04 alone, so the cover opening is not its
        business and only the cover CLOSING is."""
        c = self.a_console_that_wants_calling_on_a_clear(delay="00")
        c.cover_open = True
        c.tick()
        self.assertEqual([e[1] for e in c.autodial.log
                          if e[2] == "answered"], [])
        c.cover_open = False
        c.tick()
        self.assertIn(1, [e[1] for e in c.autodial.log if e[2] == "answered"])

    def test_a_receiver_not_assigned_the_clear_is_not_called(self):
        c = self.a_console_that_wants_calling_on_a_clear()
        c.receiver_alarms[1] = [("01", "12", "00")]   # arrivals only
        self.raise_then_clear(c)
        self.assertEqual(c.autodial.holding, [])

    def test_one_clear_holds_one_call_not_one_per_alarm(self):
        c = self.a_console_that_wants_calling_on_a_clear()
        self.raise_then_clear(c)
        c.tick()
        c.tick()
        self.assertEqual(len(c.autodial.holding), 1)


class AutoTransmit(unittest.TestCase):
    """576013-623 Rev AN section 6: "The Auto-Transmit Setup feature allows
    you to set an Automatic Transmit or Transmit/Repeat of any of the
    following signals -- in-tank alarm, sensor alarm, delivery start/stop,
    and input on/off -- to an external device via the RS-232 or RS-485
    port."

    Twelve signals, a delay and a repeat interval, all of them programmable
    and every one of them read by nothing. The MESSAGE is still not
    invented: 576013-635 documents no Auto Transmit frame anywhere, and
    `04=AUTO TRANSMIT` in 888's connect types is the only trace of the
    feature in the whole serial manual. See FIDELITY P3.
    """

    def a_console(self, signal=None, method="TRANSMIT", **settings):
        c = Console()
        c.modules["liquid"] = 1
        c.values["S70101"] = "011"
        c.values["S70301"] = "014"
        c.sensor_state[("liquid", "1")] = "normal"
        if signal is not None:
            c.set_setting(f"autotx_{signal}", method)
        for key, value in settings.items():
            c.set_setting(key, value)
        c.tick()
        return c

    def kinds(self, c):
        return [(entry[1], entry[3]) for entry in c.autotx.log]

    def test_the_twelve_signals_are_the_manuals_twelve(self):
        """p.6-5 draws the first and p.6-6 lists the other eleven. The
        engine's table and the setup menu's labels are two spellings of one
        list, and this is the ratchet that keeps them one."""
        self.assertEqual(len(autotx.SIGNALS), 12)
        for number, name, _kind, _watch in autotx.SIGNALS:
            label = FIELDS[f"set.autotx_{number}"]["label"]
            self.assertEqual(label.split(":", 1)[1].strip().upper(), name)

    def test_nothing_programmed_transmits_nothing(self):
        """Every signal defaults to DISABLED, so a console nobody has been
        near sends nothing however loud the alarm."""
        c = self.a_console()
        self.assertFalse(c.autotx.enabled())
        c.sensor_state[("liquid", "1")] = "fuel"
        c.clock_offset += 600.0
        c.tick()
        self.assertEqual(c.autotx.log, [])

    def test_a_programmed_alarm_transmits_after_the_delay(self):
        """"the time interval between any alarm, delivery, or input
        indication in the system and the time the system sends an
        Auto-Transmit message". Five seconds is the page's own default."""
        c = self.a_console(10)
        self.assertEqual(c.autotx.delay(), 5)
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        self.assertEqual(c.autotx.log, [])          # armed, not sent
        self.assertEqual([w[:2] for w in c.autotx.waiting()],
                         [(10, "030301")])
        c.clock_offset += 6.0
        c.tick()
        self.assertEqual(self.kinds(c), [(10, "transmit")])

    def test_the_delay_is_the_programmed_one(self):
        c = self.a_console(10, auto_delay="030")
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        c.clock_offset += 25.0
        c.tick()
        self.assertEqual(c.autotx.log, [])
        c.clock_offset += 6.0
        c.tick()
        self.assertEqual(len(c.autotx.log), 1)

    def test_a_plain_transmit_sends_once(self):
        c = self.a_console(10, auto_delay="001")
        c.sensor_state[("liquid", "1")] = "fuel"
        for _ in range(6):
            c.clock_offset += 120.0
            c.tick()
        self.assertEqual(self.kinds(c), [(10, "transmit")])

    def test_transmit_repeat_repeats_at_the_repeat_time(self):
        """"the length of time the system waits before retransmitting a
        message"."""
        c = self.a_console(10, method="TRANSMIT/REPEAT",
                           auto_delay="001", auto_repeat="010")
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        c.clock_offset += 2.0
        c.tick()
        self.assertEqual(self.kinds(c), [(10, "transmit")])
        for _ in range(3):
            c.clock_offset += 11.0
            c.tick()
        self.assertEqual(self.kinds(c),
                         [(10, "repeat")] * 3 + [(10, "transmit")])

    def test_a_repeat_stops_when_the_alarm_does(self):
        """The manual gives the interval and never says what ends the
        sequence -- there is no try count the way 527 gives autodial one.
        Repeating while the condition STANDS is the reading that invents
        nothing, and it is the one inference in the engine."""
        c = self.a_console(10, method="TRANSMIT/REPEAT",
                           auto_delay="000", auto_repeat="010")
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        c.clock_offset += 11.0
        c.tick()
        self.assertEqual(len(c.autotx.log), 2)
        c.sensor_state[("liquid", "1")] = "normal"
        c.clock_offset += 600.0                  # past the clear response
        c.tick()
        sent = len(c.autotx.log)
        c.clock_offset += 600.0
        c.tick()
        self.assertEqual(len(c.autotx.log), sent)
        self.assertEqual(c.autotx.waiting(), [])

    def test_a_condition_the_filter_holds_is_not_an_indication_yet(self):
        """Auto-Transmit is armed by an ALARM, which is the FILTERED list.
        The second sensor open of a day waits out Appendix A's two-minute
        detection delay, and until it has run the console has decided
        nothing to transmit. `autodial` reads the raw conditions and would
        dial about it at once.

        `compute_alarms` is called the way the panel calls it, once a
        refresh: it is what writes the alarm log, and the log is how
        Appendix A knows whether this open is the first of the day.
        """
        c = self.a_console(12, auto_delay="000")

        def look():
            c.tick()
            c.compute_alarms()

        c.sensor_state[("liquid", "1")] = "out"
        look()
        self.assertEqual(self.kinds(c), [(12, "transmit")])
        self.assertEqual(c.alarm_filter("030401", 0), (120.0, 180.0))
        c.sensor_state[("liquid", "1")] = "normal"
        look()
        c.clock_offset += 181.0                  # past the clear response
        look()
        c.sensor_state[("liquid", "1")] = "out"  # the second of the day
        look()
        self.assertEqual(len(c.autotx.log), 1)
        c.clock_offset += 121.0
        look()
        self.assertEqual(len(c.autotx.log), 2)

    def test_theft_is_the_consoles_sudden_loss_alarm(self):
        """Nothing on this shelf is called a theft limit. 577013-940 names
        them in one breath -- "Sudden Loss Limit (theft alarm limit loss)"
        -- and function 625 is Set Tank Sudden Loss Limit, whose alarm is
        02/06."""
        self.assertEqual(autotx.watches(5), {"0206"})
        c = self.a_console(5, auto_delay="000")
        c.posted.add("020601")
        c.tick()
        self.assertEqual(self.kinds(c), [(5, "transmit")])

    def test_the_sensor_signals_reach_every_card_with_that_alarm(self):
        """AUTO SENSOR FUEL ALARM is one setting over six sensor categories
        and the smart sensor, because it is matched against the alarm type's
        own name rather than a hard-coded pair. And the names are matched
        WHOLE: Water Alarm is not Water Out Alarm, and HIGH WATER ALARM is
        not HIGH WATER WARNING."""
        self.assertIn("0303", autotx.watches(10))      # liquid Fuel Alarm
        self.assertIn("2805", autotx.watches(10))      # smart FUEL ALARM
        self.assertIn("0306", autotx.watches(11))      # liquid Water Alarm
        self.assertNotIn("0307", autotx.watches(11))   # Water OUT Alarm
        self.assertEqual(autotx.watches(2), {"0203"})

    def test_an_external_input_off_is_the_alarm_going_away(self):
        """`status_types` has 05/02 EXTERN INPUT NORMAL and 05/03 EXTERN
        INPUT ALARM, and the console only ever posts the second: I401 reads
        the absence of 05/03 as the contact being off. So INPUT ON is the
        alarm arriving and INPUT OFF is it leaving."""
        c = self.a_console(8, auto_delay="000")
        c.set_setting("autotx_9", "TRANSMIT")
        c.posted.add("050301")
        c.tick()
        self.assertEqual(self.kinds(c), [(8, "transmit")])
        c.posted.discard("050301")
        c.tick()
        self.assertEqual(self.kinds(c)[0], (9, "transmit"))

    def test_a_departure_sends_once_however_it_is_programmed(self):
        """AUTO EXTERNAL INPUT OFF and AUTO DELIVERY END are both a state
        DEPARTING, and a departure has no duration to repeat over. A console
        that repeated them would have to repeat forever."""
        c = self.a_console(9, method="TRANSMIT/REPEAT",
                           auto_delay="000", auto_repeat="001")
        c.posted.add("050301")
        c.tick()
        c.posted.discard("050301")
        c.tick()
        for _ in range(5):
            c.clock_offset += 2.0
            c.tick()
        self.assertEqual(self.kinds(c), [(9, "transmit")])

    def test_delivery_start_and_end_are_two_signals(self):
        c = self.a_console(6, auto_delay="000")
        c.set_setting("autotx_7", "TRANSMIT")
        a_tank(c, volume=2000.0)
        c.values["S60701"] = "01" + float_value(96.0)
        c.values["S61001"] = "0101"              # a minute of settling
        c.tick()
        for volume in (3000.0, 4000.0):
            c.tank_level[1]["volume"] = volume
            c.clock_offset += 60.0
            c.tick()
        self.assertEqual(self.kinds(c), [(6, "transmit")])
        c.clock_offset += 600.0                  # the level settles
        c.tick()
        self.assertEqual(self.kinds(c)[0], (7, "transmit"))

    def test_no_port_no_transmit(self):
        """"to an external device via the RS-232 or RS-485 port"."""
        c = self.a_console(10, auto_delay="000")
        del c.modules["rs232"]
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        self.assertIsNone(c.autotx.port())
        self.assertEqual(c.autotx.log, [])

    def test_the_transmit_puts_auto_transmit_on_the_port(self):
        """`04=AUTO TRANSMIT`, 576013-635 Rev AA p.474. The one trace of
        this feature in the serial manual, and 888 could not report it
        because nothing in this console had ever established a
        connection."""
        c = self.a_console(10, auto_delay="000")
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        self.assertEqual(c.comm_connect.get(c.autotx.port()), "04")

    def test_888_cannot_show_it_on_the_port_you_asked_over(self):
        """Asking is itself a connection, so the port serving the request
        reads RS232 REQUEST whatever it was doing a moment ago -- and the
        manual's own sample has the same shape, its interesting connection
        on the port that is NOT answering. A second port is where you see
        one."""
        c = self.a_console(10, auto_delay="000")
        c.modules["modem"] = 1
        c.sensor_state[("liquid", "1")] = "fuel"
        c.tick()
        c.comm_connect[2] = autotx.CONNECT_AUTO_TRANSMIT
        reply = send(Handler(c, verbose=False), "I88800")
        first, second = reply.split("COMM BOARD  : 2 ")
        self.assertIn(" CONNECTION : RS232 REQUEST", first)
        self.assertIn(" CONNECTION : AUTO TRANSMIT", second)


class TheDialledReceiverSaysWhatItIs(unittest.TestCase):
    """888's first three connect types are 524's three destinations, in
    524's own order: `01=AUTO DIAL TELETYPE`, `02=AUTO DIAL FAX`,
    `03=AUTO DIAL COMPUTER` against 524's `01 TELETYPE`, `02 FACSIMILE`,
    `03 COMPUTER`. Two tables in two manuals about the same three
    destinations. 524 was one of FIDELITY F12's stored and unread codes."""

    def test_the_destination_type_is_the_connect_type(self):
        c = dialing_console()
        c.values["S52401"] = "0102"           # a facsimile
        c.cover_open = True
        c.tick()
        port = c.comm_ports_for("modem")[0]
        self.assertEqual(c.comm_connect.get(port), "02")
        reply = send(Handler(c, verbose=False), f"I888{port:02d}")
        self.assertIn(" CONNECTION : AUTO DIAL FAX", reply)

    def test_an_unprogrammed_destination_is_a_teletype(self):
        c = dialing_console()
        c.cover_open = True
        c.tick()
        port = c.comm_ports_for("modem")[0]
        self.assertEqual(c.comm_connect.get(port), "01")

    def test_a_call_nobody_answers_establishes_nothing(self):
        c = dialing_console()
        c.autodial.answers = False
        c.cover_open = True
        c.tick()
        self.assertEqual(c.comm_connect, {})
