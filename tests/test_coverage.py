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
"""How much of the Serial Interface Manual this console actually answers.

`functiondata.json` is every function code section 7 of 576013-635 Rev U
documents, parsed out of the manual rather than typed in. This measures the
simulator against it, and prints the shortfall so UNKNOWNS.md can be kept
honest:

    python -m unittest tests.test_coverage -v

The rule the coverage has to respect is the manual's own: "If the system
receives a command message string containing a function code that it does not
recognize, it will respond with a <SOH>9999FF1B<ETX>." A code this console
does not implement must say 9999 and must never say nothing at all, because a
tool sweeping the code space reads silence as a console that has stopped
answering and abandons the backup.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import wire                                  # noqa: E402
from tls350sim.console import Console                       # noqa: E402
from tls350sim.wire import Handler                          # noqa: E402


# There is no such thing as one console with everything on it, and the reason
# is a real one rather than a limitation here: "Maintenance Tracker and ISD
# want an NVMEM203, the ninth tank and BIR on manifolded tanks want an
# NVMEM201, and no console has both". So the sweep runs on both boards and a
# code has to be answered by ONE of them, which is what a code being
# implemented actually means.
BOARDS = ("E7", "E6")


def a_full_console(board=None):
    """A cage with one of everything in it, so nothing is gated out."""
    from tls350sim import presets
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart",
                 "vlld", "plld", "wplld", "io", "relay", "pump", "pumpmon",
                 "rs232", "modem", "mt", "vmc", "universal"):
        c.modules[card] = 1
    c.software = {"csld": True, "fuelman": True, "bir": True,
                  "plld020": True, "plld010": True, "isd": True}
    # "This command will respond only if stick height is enabled": I20D is
    # gated on a setting rather than on a card, so the setting goes on too.
    c.values["S60B00"] = "1"
    if board:
        c.set_board(board)
    c.software["isd"] = True
    c.software["pmc"] = True
    c.tick()
    return c


class Coverage(unittest.TestCase):
    def test_the_census_parsed_and_is_the_size_the_manual_is(self):
        self.assertGreater(len(wire.DOCUMENTED), 500)
        for code, entry in wire.DOCUMENTED.items():
            self.assertEqual(len(code), 3, code)
            self.assertIn("name", entry)
            self.assertTrue(entry["set"] or entry["inquire"], code)

    def test_the_console_invents_no_function_codes(self):
        """Anything this console answers has to be in the manual."""
        invented = sorted(c for c in wire.KNOWN if c not in wire.DOCUMENTED)
        self.assertEqual(invented, [], f"not in the manual: {invented}")

    def test_a_code_it_does_not_have_says_9999_rather_than_nothing(self):
        c = a_full_console()
        h = Handler(c, verbose=False)
        for token in sorted(wire.DOCUMENTED):
            if token in wire.KNOWN:
                continue
            reply = h.handle((chr(1) + "I" + token + "00" + chr(13)).encode())
            self.assertTrue(reply, token)
            self.assertIn(b"9999", reply, token)

    def test_every_code_it_claims_answers_something_on_a_full_cage(self):
        """A code in KNOWN has to be served, not just recognised."""
        answered = set()
        wanted = set()
        for board in BOARDS:
            c = a_full_console(board)
            h = Handler(c, verbose=False)
            for token in sorted(wire.KNOWN):
                entry = wire.DOCUMENTED.get(token) or {}
                if not entry.get("inquire"):
                    continue
                wanted.add(token)
                # "ff - Fuel Position Number (Decimal, 01-99, 00=Not
                # Allowed)". Three codes read 00 as a refusal where the rest
                # of the manual reads it as "all", so sweeping them with 00
                # asks for the one thing they are documented to reject.
                device = "01" if token in ("VA1", "VA2", "VA3") else "00"
                reply = h.handle(
                    (chr(1) + "I" + token + device + chr(13)).encode())
                if reply and b"9999" not in reply:
                    answered.add(token)
        silent = sorted(wanted - answered)
        self.assertEqual(silent, [],
                         f"claimed but unanswered on any board: {silent}")

    def test_the_shortfall_is_printed_so_it_can_be_written_down(self):
        missing = sorted(c for c in wire.DOCUMENTED if c not in wire.KNOWN)
        served = len(wire.DOCUMENTED) - len(missing)
        print(f"\nserial coverage: {served}/{len(wire.DOCUMENTED)} documented "
              f"function codes answered, {len(missing)} still 9999")
        if missing:
            print("  " + " ".join(missing))
        # a floor, so this can only go up
        self.assertGreaterEqual(served, 320)
