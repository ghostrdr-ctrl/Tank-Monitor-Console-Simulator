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
"""The card answering at the address it is programmed with.

On a site, the address programmed into the card is the address everything
happens at: ping, DeviceInstaller, the web pages, inventory. A trainee who
learns "connect to localhost" has learned the wrong thing, so these check
that the emulated card follows its own programming.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import presets, xport, xportnet


class Reset(unittest.TestCase):
    def test_reset_clears_the_address_too(self):
        """The bench reset is deliberately bigger than the card's own.

        A real card's "7 Defaults" keeps the address, which is right in the
        field. On a bench a trainee can program an address they cannot then
        reach, so this one puts the address back as well.
        """
        c = xport.XPortConfig()
        c.ip = "10.9.9.9"
        c.port = 12345
        c.reset_card()
        self.assertEqual(c.ip, xport.XPortConfig.RESET_IP)
        self.assertEqual(c.gateway, xport.XPortConfig.RESET_GATEWAY)
        self.assertEqual(c.port, 10001)

    def test_the_cards_own_defaults_still_keep_the_address(self):
        c = xport.XPortConfig()
        c.ip = "10.9.9.9"
        c.factory_defaults()
        self.assertEqual(c.ip, "10.9.9.9")

    def test_reset_can_be_given_an_address(self):
        c = xport.XPortConfig()
        c.reset_card(ip="192.168.5.5")
        self.assertEqual(c.ip, "192.168.5.5")

    def test_reset_restores_the_serial_settings(self):
        c = xport.XPortConfig()
        c.baudrate = 38400
        c.if_mode = 0x3C
        c.reset_card()
        self.assertEqual(c.baudrate, 9600)
        self.assertEqual(c.if_mode, 0x4C)


class NewCard(unittest.TestCase):
    """A module out of its box has no address, and has to be given one.

    Veeder-Root's installation guide is explicit that the address is
    customer-supplied: there is no factory default to fall back on, and the
    two documented ways of assigning one are DeviceInstaller and `arp -s`
    followed by a telnet to port 1. A card that arrives with an address is
    one somebody has already programmed, which is why an address survives
    the menu's own "7 Defaults".
    """

    def test_a_new_card_has_no_address(self):
        c = xport.XPortConfig()
        c.blank_card()
        self.assertFalse(c.assigned())
        self.assertEqual(c.ip, "0.0.0.0")

    def test_a_new_card_still_has_its_serial_settings(self):
        """Only the address is missing; the rest is the card's defaults."""
        c = xport.XPortConfig()
        c.blank_card()
        self.assertEqual(c.baudrate, 9600)
        self.assertEqual(c.if_mode, 0x4C)
        self.assertEqual(c.port, 10001)

    def test_an_unassigned_card_is_still_discoverable(self):
        """The whole point of DeviceInstaller: find what you cannot ping."""
        c = xport.XPortConfig()
        c.blank_card()
        r = xport.Discovery(c).answer(b"\x00\x00\x00\xF6")
        self.assertIsNotNone(r)
        self.assertEqual(r[24:30], c.mac)

    def test_reset_is_not_the_same_as_new(self):
        """Reset rescues a card to a known address; new leaves it blank."""
        c = xport.XPortConfig()
        c.reset_card()
        self.assertTrue(c.assigned())
        c.blank_card()
        self.assertFalse(c.assigned())

    def test_the_assignment_port_is_the_one_the_procedure_uses(self):
        self.assertEqual(xportnet.ASSIGN_PORT, 1)


class UnknownFrames(unittest.TestCase):
    def test_an_unrecognised_frame_is_ignored_but_recorded(self):
        """A frame we do not answer is a tool doing something unlearned."""
        d = xport.Discovery(xport.XPortConfig())
        self.assertIsNone(d.answer(b"\x00\x00\x00\xF0"))
        d._unknown(b"\x00\x00\x00\xF0", ("10.0.0.1", 30718))
        self.assertEqual(d.unknown[-1], ("10.0.0.1", b"\x00\x00\x00\xF0"))

    def test_the_record_does_not_grow_without_bound(self):
        d = xport.Discovery(xport.XPortConfig())
        for i in range(200):
            d._unknown(bytes([0, 0, 0, i & 0xFF]), ("10.0.0.1", 30718))
        self.assertLessEqual(len(d.unknown), 64)


class Binding(unittest.TestCase):
    def net(self, ip):
        cfg = xport.XPortConfig()
        cfg.ip = ip
        return xportnet.CardNetwork(None, cfg)

    def test_an_address_this_machine_has_is_bound(self):
        n = self.net("127.0.0.1")
        host, note = n.bind_host()
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(note, "")

    def test_an_address_it_does_not_have_falls_back_and_says_so(self):
        n = self.net("203.0.113.7")             # TEST-NET-3, never local
        host, note = n.bind_host()
        self.assertEqual(host, "0.0.0.0")
        self.assertIn("not an address on this machine", note)

    def test_an_unprogrammed_card_listens_everywhere(self):
        n = self.net("0.0.0.0")
        host, note = n.bind_host()
        self.assertEqual(host, "0.0.0.0")
        self.assertIn("no address", note)

    def test_the_status_line_names_the_address_and_the_ports(self):
        n = self.net("127.0.0.1")
        n.config.port = 10005
        text = n.status()
        self.assertIn("127.0.0.1", text)
        self.assertIn("10005", text)
        self.assertIn("9999", text)

    def test_reprogramming_is_noticed(self):
        """`wanted` is what the supervisor watches to know it must move."""
        n = self.net("127.0.0.1")
        before = n.wanted()
        n.config.port = 10007
        self.assertNotEqual(n.wanted(), before)

    def test_loopback_is_always_a_local_address(self):
        self.assertTrue(xportnet.is_local("127.0.0.1"))
        self.assertFalse(xportnet.is_local("203.0.113.7"))
        self.assertFalse(xportnet.is_local("0.0.0.0"))


class Discovery(unittest.TestCase):
    def test_the_reply_carries_the_programmed_address_not_a_local_one(self):
        """DeviceInstaller lists a card at the address its reply came from.

        The emulator answers from a socket bound to the card's own address
        when it can, so the tool shows the address a trainee is about to be
        told to connect to rather than whichever interface routed the reply.
        """
        cfg = xport.XPortConfig()
        cfg.ip = "127.0.0.1"
        d = xport.Discovery(cfg)
        self.assertTrue(hasattr(d, "_reply"))
        r = d.answer(b"\x00\x00\x00\xF6")
        self.assertEqual(r[24:30], cfg.mac)


class PresetCards(unittest.TestCase):
    def test_every_example_site_has_its_own_card_address(self):
        seen = {}
        for name in presets.PRESETS:
            card = presets.card_of(name)
            self.assertTrue(card, "%s has no card" % name)
            self.assertIn("ip", card)
            self.assertNotIn(card["ip"], seen,
                             "%s reuses %s" % (name, card["ip"]))
            seen[card["ip"]] = name

    def test_loading_a_site_programs_its_card(self):
        from tls350sim.console import Console
        console = Console(None)
        card = xport.XPortConfig()
        name = list(presets.PRESETS)[0]
        presets.load(console, name, card=card)
        self.assertEqual(card.ip, presets.card_of(name)["ip"])
        self.assertEqual(card.port, presets.card_of(name)["port"])

    def test_a_site_with_a_different_tunnel_port_takes_it(self):
        from tls350sim.console import Console
        for name in presets.PRESETS:
            spec = presets.card_of(name)
            if spec.get("port") != 10001:
                card = xport.XPortConfig()
                presets.load(Console(None), name, card=card)
                self.assertEqual(card.port, spec["port"])
                return
        self.skipTest("every example site uses the default tunnel port")


if __name__ == "__main__":
    unittest.main()
