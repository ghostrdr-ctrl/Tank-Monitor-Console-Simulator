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
"""Section 7.1's control functions: the nineteen codes that DO something."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import controls, presets                    # noqa: E402
from tls350sim.console import Console                      # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("smart", "wplld", "plld", "relay"):
        c.modules[card] = 4
    c.values["S72201"] = "01SUMP 1             "
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


class TheTwoTestStatusTablesAreNotOneTable(unittest.TestCase):
    """The trap in this section. 087 and 088 look like a matched pair and
    their status enumerations differ -- same digit, different meaning, on six
    of the ten they share. Sharing one table would report a 0.10 gph test as
    a 0.20 gph one, and a running pump as a line lockout.
    """

    def test_the_same_digit_means_different_things(self):
        self.assertEqual(controls.PLLD_TEST_STATUS["03"],
                         "TESTING AT 0.10 GAL/HR")
        self.assertEqual(controls.WPLLD_TEST_STATUS["03"],
                         "TESTING AT 0.20 GAL/HR")
        self.assertEqual(controls.PLLD_TEST_STATUS["05"], "RUNNING PUMP")
        self.assertEqual(controls.WPLLD_TEST_STATUS["05"], "LINE LOCKOUT")

    def test_they_are_not_even_the_same_length(self):
        self.assertEqual(len(controls.PLLD_TEST_STATUS), 12)
        self.assertEqual(len(controls.WPLLD_TEST_STATUS), 10)

    def test_six_of_the_shared_digits_disagree(self):
        shared = set(controls.PLLD_TEST_STATUS) & set(controls.WPLLD_TEST_STATUS)
        differ = [d for d in shared
                  if controls.PLLD_TEST_STATUS[d]
                  != controls.WPLLD_TEST_STATUS[d]]
        self.assertEqual(len(differ), 6, sorted(differ))

    def test_the_test_type_table_IS_shared(self):
        """That one is the same on both, which is what makes the other easy
        to get wrong."""
        self.assertEqual(controls.TEST_TYPE,
                         {"01": "annual", "02": "periodic", "03": "gross"})


class TheBareAcknowledgements(unittest.TestCase):
    """001, 002, 003, 010: no argument, no verification, date and time back."""

    def test_all_four_answer_in_both_formats(self):
        _c, h = a_site()
        for code in ("001", "002", "003", "010"):
            for letter in ("S", "s"):
                self.assertNotIn("9999", send(h, letter + code + "00"),
                                 letter + code)

    def test_a_system_reset_is_a_restart_and_not_a_wipe(self):
        """Clearing setup data is its own function. A tool that reset a
        console and lost the site's programming would be a bad afternoon."""
        c, h = a_site()
        label = c.text("602", 1)
        self.assertTrue(label)
        send(h, "S00100")
        self.assertEqual(c.text("602", 1), label, "programming survives")
        self.assertIsNotNone(c.power_off, "but it did go off and come back")

    def test_clearing_the_power_flag_clears_it(self):
        c, h = a_site()
        send(h, "S00100")
        self.assertIsNotNone(c.power_off)
        send(h, "S00200")
        self.assertIsNone(c.power_off)

    def test_the_remote_alarm_reset_silences(self):
        """The same thing ALARM/TEST does from the panel."""
        c, h = a_site()
        c.silenced = False
        send(h, "S00300")
        self.assertTrue(c.silenced)


class TheConfirmClear(unittest.TestCase):
    """031, the only one of the nineteen that does not use 149."""

    def test_it_wants_its_own_token(self):
        _c, h = a_site()
        self.assertIn("9999", send(h, "S03100"))
        self.assertIn("9999", send(h, "S03100149"))
        self.assertNotIn("9999", send(h, "S03100832382"))

    def test_it_says_so(self):
        _c, h = a_site()
        self.assertIn("CONFIRM CLEAR COMPLETE", send(h, "S03100832382"))


class ThePressureOffsetReset(unittest.TestCase):
    """089 and 090, and the panel screen that has been waiting for them."""

    def test_the_offset_is_held_and_not_recomputed(self):
        """A value recomputed on every read cannot be reset, which is why the
        panel's own P OFFSET RESET screen did nothing before this."""
        c, _h = a_site()
        first = c.diag_value("line_offset", 1, "plld")
        self.assertEqual(first, c.diag_value("line_offset", 1, "plld"))

    def test_the_reset_resets_it(self):
        c, h = a_site()
        c.diag_value("line_offset", 1, "plld")
        self.assertNotEqual(c.lines.line("plld", 1).offset, 0.0)
        self.assertNotIn("9999", send(h, "S08901149"))
        self.assertEqual(c.lines.line("plld", 1).offset, 0.0)

    def test_both_want_the_verification_code(self):
        _c, h = a_site()
        for code in ("089", "090"):
            self.assertIn("9999", send(h, "S" + code + "01"), code)
            self.assertNotIn("9999", send(h, "S" + code + "01149"), code)

    def test_device_00_resets_every_line(self):
        c, h = a_site()
        for n in (1, 2, 3):
            c.diag_value("line_offset", n, "plld")
        send(h, "S08900149")
        for n in (1, 2, 3):
            self.assertEqual(c.lines.line("plld", n).offset, 0.0, n)


class StartingATestByType(unittest.TestCase):
    """087 and 088."""

    def test_the_type_picks_the_test(self):
        for want, rate in (("01", "annual"), ("02", "periodic"),
                           ("03", "gross")):
            c, h = a_site()
            send(h, "S08701149" + want)
            self.assertEqual(c.lines.line("plld", 1).rate_key, rate, want)

    def test_an_unknown_type_is_refused(self):
        _c, h = a_site()
        for bad in ("00", "04", "99"):
            self.assertIn("9999", send(h, "S08701149" + bad), bad)

    def test_it_wants_the_verification_code(self):
        _c, h = a_site()
        self.assertIn("9999", send(h, "S0870102"))

    def test_the_computer_reply_echoes_the_line_the_type_and_the_status(self):
        _c, h = a_site()
        got = send(h, "s08701149" + "02").strip(chr(1) + chr(3))
        body = got.split("&&")[0][len("s08701") + 10:]
        self.assertEqual(body[0:2], "01", "line number")
        self.assertEqual(body[2:4], "02", "the type asked for")
        self.assertIn(body[4:6], controls.PLLD_TEST_STATUS, "a real status")


class TheDeviceActions(unittest.TestCase):
    """092 to 09B: one argument, 149, and the phase echoed back."""

    def test_every_one_answers_and_wants_its_149(self):
        _c, h = a_site()
        for code in controls.DEVICE_ACTIONS:
            self.assertIn("9999", send(h, "S" + code + "01"), code)
            self.assertNotIn("9999", send(h, "S" + code + "01149"), code)

    def test_each_puts_the_device_in_the_phase_it_names(self):
        c, h = a_site()
        send(h, "S09901149")                      # start mag sump test
        self.assertEqual(c.control_phase_of("sump", 1), "02")    # FILL SUMP
        send(h, "S09A01149")                      # measuring height phase
        self.assertEqual(c.control_phase_of("sump", 1), "03")
        send(h, "S09B01149")                      # stop
        self.assertEqual(c.control_phase_of("sump", 1), "01")

    def test_the_two_vacuum_pairs_use_two_different_tables(self):
        """095/096 report a test status; 097/098 an evacuation state."""
        c, h = a_site()
        send(h, "S09501149")
        self.assertEqual(c.control_phase_of("vactest", 1), "01")   # STARTED
        send(h, "S09701149")
        self.assertEqual(c.control_phase_of("evac", 1), "06")      # EVAC HOLD
        self.assertIn("EVAC HOLD", send(h, "S09701149"))
        self.assertIn("STARTED", send(h, "S09501149"))

    def test_the_profile_trio_share_one_table(self):
        for code in ("092", "093", "094"):
            self.assertIs(controls.DEVICE_ACTIONS[code][1],
                          controls.PROFILE_STATUS, code)

    def test_the_printout_heads_itself_with_the_command(self):
        _c, h = a_site()
        self.assertIn("START MAG SUMP LEAK TEST", send(h, "S09901149"))
        self.assertIn("STOP VACUUM SENSOR EVACUATION HOLD",
                      send(h, "S09801149"))

    def test_they_want_the_module_that_carries_the_device(self):
        c, h = a_site()
        c.modules["smart"] = 0
        self.assertIn("9999", send(h, "S09901149"))
        c.modules["plld"] = 0
        self.assertIn("9999", send(h, "S09201149"))


if __name__ == "__main__":
    unittest.main()
