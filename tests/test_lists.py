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
"""The seven setup codes whose data is a list.

The thing worth testing here is not that they store a value. It is that two of
them decide their own width from their own first character, which is the
reason they resisted a `kind` for so long, and that a reader which fixes the
width first eats the next command.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import fieldio, presets, wirelists          # noqa: E402
from tls350sim.console import Console, FIELDS              # noqa: E402
from tls350sim.wire import Handler                         # noqa: E402


def a_site():
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    c.set_board("E6")
    for card in ("smart", "vmc", "mt", "wplld", "plld", "vlld", "modem",
                 "universal"):
        c.modules[card] = 4
    c.software.update({"fuelman": True, "csld": True, "bir": True})
    return c, Handler(c, verbose=False)


def send(h, cmd):
    return h.handle((chr(1) + cmd + chr(13)).encode()).decode("latin-1")


def body(h, cmd):
    return send(h, cmd).strip(chr(1) + chr(3))


def refused(h, cmd):
    return body(h, cmd).startswith("9999")


class TheWidthIsInTheData(unittest.TestCase):
    """52B and 75A: the first character says how long the rest is."""

    def test_every_dial_method_has_its_own_width(self):
        self.assertEqual(wirelists.DIAL_WIDTH,
                         {"1": 10, "2": 8, "3": 6, "4": 5, "5": 4})

    def test_a_daily_dial_is_four_characters_and_a_dated_one_is_ten(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S52B01" + "5" + "0415"))
        self.assertFalse(refused(h, "S52B01" + "1" + "2601220415"))

    def test_the_right_payload_under_the_wrong_method_is_refused(self):
        """Four characters is a whole daily setting and half a dated one. A
        reader that slices before it reads the method takes the next
        command's bytes to fill the gap."""
        _c, h = a_site()
        self.assertTrue(refused(h, "S52B01" + "1" + "0415"))
        self.assertTrue(refused(h, "S52B01" + "5" + "2601220415"))

    def test_the_lockout_type_decides_its_width_too(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S75A00" + "0" + "2245" + "0445"))
        self.assertFalse(refused(h, "S75A00" + "1" + "2" + "5" + "2200"
                                 + "1" + "0600"))
        # the daily payload under the individual type, and the reverse
        self.assertTrue(refused(h, "S75A00" + "1" + "2245" + "0445"))
        self.assertTrue(refused(h, "S75A00" + "0" + "2520" + "0010" + "600"))

    def test_a_disabled_time_is_not_a_time(self):
        """"HHmm=Hour, Minute (EE00=Disabled)" -- EE00 is accepted where a
        time is wanted and must not be printed as one."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S52B01" + "5" + "EE00"))
        self.assertIn("DISABLED", send(h, "I52B01"))
        self.assertTrue(refused(h, "S52B01" + "5" + "2465"))


class TheHoleAtFour(unittest.TestCase):

    def test_there_is_no_report_04(self):
        self.assertNotIn("04", wirelists.REPORTS)
        self.assertIn("03", wirelists.REPORTS)
        self.assertIn("05", wirelists.REPORTS)

    def test_report_04_is_refused_like_any_other_number_off_the_list(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S52A01" + "01" + "0301"))
        self.assertTrue(refused(h, "S52A01" + "01" + "0401"))
        self.assertTrue(refused(h, "S52A01" + "01" + "2001"))

    def test_the_count_has_to_match_what_follows(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "S52A01" + "02" + "0101"))
        self.assertFalse(refused(h, "S52A01" + "02" + "0101" + "0601"))


class SevenBOne(unittest.TestCase):

    def test_it_has_no_computer_format_in_either_direction(self):
        """"Computer format is not supported for this command" -- the only
        setup code that says so, and it is not a Set-only rule."""
        _c, h = a_site()
        self.assertFalse(refused(h, "I7B100"))
        self.assertTrue(refused(h, "i7B100"))
        self.assertTrue(refused(h, "s7B100" + "3030010" + "01"))

    def test_a_slot_outside_its_own_bus_is_refused(self):
        """"Bus 2: 09-16, Bus 3: 01-06"."""
        _c, h = a_site()
        self.assertFalse(refused(h, "S7B100" + "3" + "03" + "00" + "10" + "01"))
        self.assertFalse(refused(h, "S7B100" + "2" + "09" + "00" + "11" + "01"))
        self.assertTrue(refused(h, "S7B100" + "3" + "09" + "00" + "12" + "01"))
        self.assertTrue(refused(h, "S7B100" + "2" + "03" + "00" + "13" + "01"))

    def test_it_carries_the_fueling_position_nothing_else_knows(self):
        c, h = a_site()
        send(h, "S7B100" + "3" + "04" + "07" + "10" + "02")
        self.assertEqual(c.fueling_position(10), 7)
        self.assertEqual(c.meters[10], 2)
        self.assertIn("FUEL_P", send(h, "I7B100"))

    def test_tank_00_unmaps_and_minus_one_is_probeless(self):
        c, h = a_site()
        send(h, "S7B100" + "3" + "03" + "00" + "10" + "01")
        self.assertIn(10, c.meters)
        send(h, "S7B100" + "3" + "03" + "00" + "10" + "-1")
        self.assertEqual(c.meters[10], -1)          # probeless, still mapped
        send(h, "S7B100" + "3" + "03" + "00" + "10" + "00")
        self.assertNotIn(10, c.meters)              # unmapped entirely


class TheyRoundTrip(unittest.TestCase):

    def test_what_the_panel_stores_the_wire_reads_back(self):
        c, h = a_site()
        send(h, "S52A01" + "02" + "0101" + "0601")
        text = send(h, "I52A01")
        self.assertIn("SYSTEM STATUS", text)
        self.assertIn("IN-TANK INVENTORY", text)
        packed = body(h, "i52A01")
        self.assertIn("0101", packed)
        self.assertIn("0601", packed)

    def test_the_computer_format_is_not_stamped_twice(self):
        """_frame stamps the reply itself; a handler that adds one as well
        puts twenty digits where the tool expects ten."""
        _c, h = a_site()
        send(h, "S75A00" + "0" + "2245" + "0445")
        packed = body(h, "i75A00")
        self.assertTrue(packed.startswith("i75A00"))
        rest = packed[6:].split("&&")[0]
        self.assertEqual(len(rest), 10 + 9, rest)   # stamp, then S + 8

    def test_manifolded_partners_come_back_as_tanks(self):
        c, h = a_site()
        send(h, "S61201" + "02")
        self.assertEqual(c.partners("612", 1), [2])
        self.assertIn(2, c.manifolded(1))

    def test_a_tank_is_never_its_own_partner(self):
        c, h = a_site()
        send(h, "S61201" + "01" + "02")
        self.assertEqual(c.partners("612", 1), [2])

    def test_a_partner_that_is_not_a_tank_is_refused(self):
        _c, h = a_site()
        self.assertTrue(refused(h, "S61201" + "37"))


class IgnoredIsNotRefusedAndIsNotStored(unittest.TestCase):
    """52D: "f - Alarm clear flag, 1=clear; all others ignored".

    Three different behaviours hide behind that one word. The console TAKES
    the command, does NOTHING, and stores NOTHING. This used to fall through
    to the generic setup path, so any payload at all became the receiver's
    setting and the one value that means something cleared nothing, because
    nothing was watching for it.

    What made it hide: the Inquire half was right the whole time, and the test
    proving `f` means opposite things on the two halves set the alarm directly
    rather than going through the Set.
    """

    def test_one_clears_the_alarm(self):
        c, h = a_site()
        c.autodial_alarm[1] = True
        self.assertFalse(refused(h, "S52D01" + "1"))
        self.assertIs(c.autodial_alarm[1], False)

    def test_anything_else_is_taken_and_does_nothing(self):
        c, h = a_site()
        c.autodial_alarm[1] = True
        self.assertFalse(refused(h, "S52D01" + "0"), "ignored, not refused")
        self.assertIs(c.autodial_alarm[1], True, "and not acted on")

    def test_and_is_not_stored_either(self):
        c, h = a_site()
        send(h, "S52D01" + "@@@GARBAGE@@@")
        self.assertIsNone(c.values.get("S52D01"))

    def test_the_inquire_half_still_reads_the_other_way(self):
        c, h = a_site()
        c.autodial_alarm[1] = True
        self.assertIn("ALARM", send(h, "I52D01"))
        send(h, "S52D01" + "1")
        self.assertIn("CLEAR", send(h, "I52D01"))


class TheVmcSerialPair(unittest.TestCase):
    """8C1 Edit/Add and 8C2 Remove: one format, opposite jobs."""

    def test_both_want_six_decimal_digits(self):
        _c, h = a_site()
        self.assertFalse(refused(h, "S8C101" + "123456"))
        self.assertTrue(refused(h, "S8C102" + "1234"))
        self.assertTrue(refused(h, "S8C102" + "12345A"))

    def test_add_then_read_back(self):
        c, h = a_site()
        send(h, "S8C101" + "123456")
        self.assertEqual(c.vmc_serial(1), "123456")
        self.assertIn("123456", send(h, "I8C101"))

    def test_remove_refuses_a_serial_the_controller_does_not_hold(self):
        """A typo here silently unregisters a controller that was fine."""
        c, h = a_site()
        send(h, "S8C101" + "123456")
        self.assertTrue(refused(h, "S8C201" + "999999"))
        self.assertEqual(c.vmc_serial(1), "123456")

    def test_remove_takes_the_one_it_holds(self):
        c, h = a_site()
        send(h, "S8C101" + "123456")
        self.assertFalse(refused(h, "S8C201" + "123456"))
        self.assertNotEqual(c.vmc_serial(1), "123456")


class ThePanelAndTheWireAgree(unittest.TestCase):

    def test_no_setup_field_is_untyped_any_more(self):
        raw = [k for k, v in FIELDS.items() if v.get("kind") == "raw"]
        self.assertEqual(raw, [], "raw means nothing validates it")

    def test_every_list_field_has_a_sample_that_its_own_check_accepts(self):
        for key, field in FIELDS.items():
            if field.get("kind") != "list":
                continue
            value = wirelists.sample(key)
            self.assertTrue(wirelists.validate(key, value),
                            f"{key} sample {value!r} fails its own check")

    def test_the_keypad_refuses_what_the_wire_refuses(self):
        """A value the panel accepts and the serial port does not is how a
        console ends up holding a setting no tool can read back."""
        field = FIELDS["S52B01"]
        with self.assertRaises(ValueError):
            fieldio.encode_value(field, "1" + "0415")   # dated, daily payload
        self.assertTrue(fieldio.encode_value(field, "5" + "0415"))


if __name__ == "__main__":
    unittest.main()
