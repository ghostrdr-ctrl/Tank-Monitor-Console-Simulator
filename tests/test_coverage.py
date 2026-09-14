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


def refused(reply):
    """Is this reply the manual's 9999, rather than a report containing it?

    "It will respond with a <SOH>9999FF1B<ETX>" -- a whole message, eight
    characters between the two control codes. Testing `b"9999" in reply` reads
    a report's own numbers as a refusal: I102's SYSTEM CONFIGURATION prints
    twenty-odd drifting ID resistances, and about one sweep in three hundred
    drew a 29999 and reported the code as unanswered. That is a false
    negative in the ratchet that guards every other code, and it made this
    file flake.

    Worth knowing before writing `assertNotIn("9999", ...)` against any
    report wide enough to carry a number.
    """
    return reply.strip(chr(1).encode() + chr(3).encode()) == b"9999FF1B"


# The communication bay is FOUR slots and there are more comm cards than
# that with function codes of their own, so "one of everything" is two cages
# rather than one. Slot by slot, because which slot a card is in decides
# which POSITION it answers on. See FIDELITY M7.
#
# Between them they carry every comm identity a function code is gated on:
# RS-232 (the multiport's DB-9 half in bay 1, its own card in bay 2), the
# modem, the VMCI, the Maintenance Tracker, the EDIM, the satellite and the
# remote printer.
COMM_BAYS = ({1: "rs232", 2: "modem", 3: "vmc", 4: "mt4"},
             {1: "ssat", 2: "edim", 3: "rprinter", 4: "rs485"})


def a_full_console(board=None, comm=None):
    """A cage with one of everything in it, so nothing is gated out."""
    from tls350sim import presets
    from tls350sim.console import MODULES
    c = Console()
    presets.load(c, "Truck stop, four tanks and BIR")
    for card in ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart",
                 "vlld", "plld", "wplld", "io", "relay", "pump", "pumpmon",
                 "universal"):
        c.modules[card] = 1
    comm = dict(comm or COMM_BAYS[0])
    for key, _n, _p, bay, _w, _m in MODULES:
        if bay == "comm":
            c.modules[key] = 0
    for _slot, key in comm.items():
        c.modules[key] = 1
    c.comm_slots = comm
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


def part_only_codes():
    """The function codes that exist ONLY as part fields.

    `consoledata.json` holds `S50100.date` and `S50100.time` and no bare
    `S50100`, because two panel prompts share one function's data. Derived
    rather than listed, so a new one is swept the day it is added.
    """
    from tls350sim.console import FIELDS
    out = {}
    for key, field in FIELDS.items():
        head, _, part = key.partition(".")
        if not part or not field or head in FIELDS and FIELDS[head]:
            continue
        if not (len(head) == 6 and head[0] == "S"):
            continue
        out.setdefault(head[1:4], []).append(field)
    return out


def programmed(c, tok, parts):
    """Give one part-field code a stored value, the width its parts imply.

    This is the state a restored backup leaves the console in, and it is the
    state the census never sweeps: `a_full_console` is a cage that is FULL and
    a console that is mostly UNPROGRAMMED, so every one of these codes turned
    back at "not programmed" before it reached the decode.
    """
    width = 0
    for field in parts:
        span = field.get("part")
        if span:
            width = max(width, span[0] + span[1])
        for span in (field.get("part_when") or {}).get("map", {}).values():
            width = max(width, span[0] + span[1])
    c.values[f"S{tok}01"] = "1" * width
    return f"S{tok}01"


class ProgrammedNotJustFitted(unittest.TestCase):
    """FIDELITY S7: fourteen Display inquiries raised instead of answering.

    `I50100` -- ask the console what time it thinks it is -- came back as an
    AttributeError out of `Handler.handle`, and `_session` catches only
    OSError, so the connection dropped mid-conversation. The trigger was not
    an odd value; it was having programmed the code at all.

    The census could not see it. It is the right sweep asked on the wrong
    console: every one of these returned "not programmed" and turned back
    before reaching the decode. **Proving a code answers on an empty console
    is not proving it answers.**
    """

    def setUp(self):
        self.parts = part_only_codes()

    def test_the_part_only_codes_are_still_the_shape_that_caused_this(self):
        """If the bare fields ever appear, this whole class is moot -- and
        the sweep below would be passing for the wrong reason."""
        self.assertGreaterEqual(len(self.parts), 14, self.parts)

    def test_a_display_inquire_answers_once_the_code_is_programmed(self):
        c = a_full_console()
        h = Handler(c, verbose=False)
        for tok, fields in sorted(self.parts.items()):
            programmed(c, tok, fields)
            for letter in ("I", "i"):
                try:
                    reply = h.handle(
                        (chr(1) + letter + tok + "01" + chr(13)).encode())
                except Exception as exc:                # noqa: BLE001
                    self.fail(f"{letter}{tok}01 raised {exc!r}")
                self.assertTrue(reply, f"{letter}{tok}01 answered nothing")

    def test_the_whole_census_survives_a_programmed_console(self):
        """And the general form, which is the test S7 says should have
        existed: sweep every code the console claims, on a console somebody
        has actually set up."""
        c = a_full_console()
        for tok, fields in self.parts.items():
            programmed(c, tok, fields)
        h = Handler(c, verbose=False)
        for token in sorted(wire.KNOWN):
            if not (wire.DOCUMENTED.get(token) or {}).get("inquire"):
                continue
            device = "01" if token in ("VA1", "VA2", "VA3") else "00"
            for letter in ("I", "i"):
                try:
                    h.handle((chr(1) + letter + token + device
                              + chr(13)).encode())
                except Exception as exc:                # noqa: BLE001
                    self.fail(f"{letter}{token}{device} raised {exc!r}")


class Coverage(unittest.TestCase):
    def test_the_census_parsed_and_is_the_size_the_manual_is(self):
        self.assertGreater(len(wire.DOCUMENTED), 500)
        for code, entry in wire.DOCUMENTED.items():
            self.assertEqual(len(code), 3, code)
            self.assertIn("name", entry)
            self.assertTrue(entry["set"] or entry["inquire"], code)

    # Codes no manual on this shelf documents and a REAL CONSOLE answers.
    # The bar is a capture, not an argument: `tests/console_capture/raw/`
    # holds the reply, and the codes either side of it are refused by the
    # same console in the same sweep, so it is the hardware's own answer and
    # not a range that happens to respond.
    #
    # 121 answers `ACTIVE ALARMS REPORT` over 113's heading, character for
    # character, while 120 and 122 come back `9999FF1B`. See FIDELITY S17.
    OFF_THE_HARDWARE = {"121"}

    def test_the_console_invents_no_function_codes(self):
        """Anything this console answers has to be in the manual, or in the
        capture -- and the capture is the narrower door of the two."""
        invented = sorted(c for c in wire.KNOWN if c not in wire.DOCUMENTED)
        self.assertEqual(invented, sorted(self.OFF_THE_HARDWARE),
                         f"not in the manual: {invented}")

    def test_a_code_it_does_not_have_says_9999_rather_than_nothing(self):
        """FIDELITY V1. This looped over `DOCUMENTED - KNOWN` and asserted
        on each -- and it has asserted nothing since the census finished,
        because that set became empty and the `continue` fired on every
        iteration. It did not fail. Nothing announced it.

        **Any test shaped "for each thing not yet done, assert something
        about it" disarms itself at the moment the work completes**, which
        is the moment nobody is looking. So the first line here is the set
        itself: if a later revision adds a documented code this console does
        not serve, the census is incomplete and this says so, and the loop
        below has something to do again.

        The property is not left unguarded meanwhile.
        `test_conformance.test_an_unknown_function_code_is_9999_and_never_
        silence` sends five tokens that are deliberately outside the census
        -- `5C0`, `63E`, `7CA`, `8A1`, `ZZZ` -- and asserts on every one.
        """
        missing = sorted(set(wire.DOCUMENTED) - set(wire.KNOWN))
        self.assertEqual(missing, [],
                         "documented codes this console does not serve; the "
                         "census is no longer complete")
        c = a_full_console()
        h = Handler(c, verbose=False)
        for token in missing:
            reply = h.handle((chr(1) + "I" + token + "00" + chr(13)).encode())
            self.assertTrue(reply, token)
            self.assertIn(b"9999", reply, token)

    def test_every_code_it_claims_answers_something_on_a_full_cage(self):
        """A code in KNOWN has to be served, not just recognised."""
        answered = set()
        wanted = set()
        for board in BOARDS:
          for comm in COMM_BAYS:
            c = a_full_console(board, comm)
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
                if reply and not refused(reply):
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
