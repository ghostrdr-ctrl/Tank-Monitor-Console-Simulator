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
"""The emulated card, diffed against a real one, byte for byte.

This is the test that says whether the emulation is actually faithful rather
than merely plausible: it replays the captured keystrokes through
`SetupSession` and compares every chunk of bytes the card sent.

It needs a capture, which takes a card. `tools/capture_xport.py` makes one:

    python tools/capture_xport.py 172.30.9.14 --out tests/xport_capture

The capture is not committed -- it is a device's own output, the same rule
the manuals under `reference/` follow -- so this skips when it is not there,
the way `test_tape.py` skips without its tape. A green suite on a machine
with no card therefore proves less than one on a machine with a card, which
is worth remembering before trusting it.

`XPORT_CAPTURE` overrides where the capture is looked for. Set
`XPORT_CAPTURE_STATE` to a JSON object of `XPortConfig` fields when the card
was not at the emulator's defaults, so the comparison is of behaviour and not
of configuration.
"""
import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import xport, xportmenu

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.environ.get("XPORT_CAPTURE") or os.path.join(HERE,
                                                          "xport_capture")
MENU_DIR = os.path.join(CAPTURE, "menu")


def have_capture():
    return os.path.isdir(MENU_DIR) and bool(
        glob.glob(os.path.join(MENU_DIR, "*.bin")))


def captured_chunks():
    out = {}
    for path in sorted(glob.glob(os.path.join(MENU_DIR, "*.bin"))):
        name = os.path.basename(path)[3:-4]
        with open(path, "rb") as f:
            out[name] = f.read()
    return out


def configured_card():
    """A card set up the way the captured one was.

    Without this the diff would report every difference of configuration as
    a difference of behaviour, which is the opposite of useful.
    """
    # The banner carries the MAC, so a capture only compares against a card
    # wearing the same one.
    cfg = xport.XPortConfig(mac=xport.default_mac(captured=True))
    raw = os.environ.get("XPORT_CAPTURE_STATE")
    if raw:
        try:
            for key, value in json.loads(raw).items():
                if key == "mac":
                    cfg.mac = bytes.fromhex(value)
                    continue
                setattr(cfg, key, value)
        except (ValueError, AttributeError, TypeError) as exc:
            raise AssertionError("XPORT_CAPTURE_STATE is not usable: %s"
                                 % exc)
    return cfg


def replay():
    """Drive the emulator through the same keystrokes the capture used."""
    sent = bytearray()
    session = xportmenu.SetupSession(configured_card(), sent.extend)

    def take():
        data = bytes(sent)
        sent.clear()
        return data

    steps = [("banner", take())]
    session.feed_bytes(b"\r\n")
    steps.append(("dump_and_menu", take()))
    for choice in ("0", "1", "3", "5", "6"):
        session.feed_bytes(choice.encode() + b"\r\n")
        out = take()
        steps.append(("menu%s_open" % choice, out))
        i = 0
        while b"Your choice ?" not in out and i < 45:
            session.feed_bytes(b"\r\n")
            out = take()
            i += 1
            steps.append(("menu%s_step%02d" % (choice, i), out))
            if not out:
                break
    session.feed_bytes(b"8\r\n")
    steps.append(("exit", take()))
    return steps


@unittest.skipUnless(have_capture(),
                     "no capture in %s; run tools/capture_xport.py against a "
                     "card to make one" % CAPTURE)
class AgainstTheCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card = captured_chunks()
        cls.sim = dict(replay())
        cls.order = [name for name, _ in replay()]

    def test_the_banner_is_byte_identical(self):
        self.assertEqual(self.sim["banner"], self.card["banner"])

    def test_the_main_menu_is_byte_identical(self):
        # Every screen that ends back at the menu carries it, so any one
        # will do; take the first.
        for name in self.order:
            if name.endswith("step07") and name.startswith("menu0"):
                self.assertEqual(self.sim[name], self.card.get(name))
                return
        self.skipTest("the capture does not reach the end of menu 0")

    def test_the_emulator_produces_every_screen_the_card_did(self):
        missing = [n for n in self.card if n not in self.sim]
        self.assertEqual(missing, [],
                         "the card sent screens the emulator never did: %s"
                         % missing)

    def test_every_screen_is_byte_identical(self):
        differing = []
        for name in self.order:
            want = self.card.get(name)
            if want is None:
                continue
            if self.sim[name] != want:
                differing.append(name)
        if differing:
            name = differing[0]
            self.fail(
                "%d of %d screens differ (%s).\nfirst: %s\n  card: %r\n"
                "  sim : %r"
                % (len(differing), len(self.card), ", ".join(differing),
                   name, self.card[name][:160], self.sim[name][:160]))


@unittest.skipUnless(
    os.path.isdir(os.path.join(CAPTURE, "udp")),
    "no 77FEh capture; run tools/capture_xport.py against a card")
class AgainstTheCardsDiscovery(unittest.TestCase):
    def reply(self, op):
        path = os.path.join(CAPTURE, "udp", "%02X.bin" % op)
        if not os.path.exists(path):
            self.skipTest("opcode %02X not in the capture" % op)
        with open(path, "rb") as f:
            return f.read()

    def test_f6_matches_apart_from_the_mac(self):
        want = self.reply(0xF6)
        cfg = xport.XPortConfig(mac=want[24:30])
        got = xport.Discovery(cfg).answer(b"\x00\x00\x00\xF6")
        self.assertEqual(got, want)

    def test_f4_matches(self):
        want = self.reply(0xF4)
        got = xport.Discovery(xport.XPortConfig()).answer(b"\x00\x00\x00\xF4")
        self.assertEqual(got, want)

    def test_fa_matches(self):
        want = self.reply(0xFA)
        got = xport.Discovery(xport.XPortConfig()).answer(b"\x00\x00\x00\xFA")
        self.assertEqual(got, want)

    def test_f8_is_the_right_shape(self):
        want = self.reply(0xF8)
        got = xport.Discovery(xport.XPortConfig()).answer(b"\x00\x00\x00\xF8")
        self.assertEqual(len(got), len(want))
        self.assertEqual(got[:4], want[:4])


if __name__ == "__main__":
    unittest.main()
