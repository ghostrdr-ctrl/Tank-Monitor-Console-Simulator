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
"""The Lantronix XPort emulation, against the documented protocol.

Every assertion here traces to reference/lantronix_setup_menu.md or
reference/lantronix_deviceinstaller_protocol.md: the byte offsets of the
discovery responses, the golden TLS-350 settings, and the setup-menu walk.
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import xport


class Config(unittest.TestCase):
    def test_defaults_are_the_tls350_golden_values(self):
        c = xport.XPortConfig()
        # reference/lantronix_setup_menu.md 10, TLS-350 golden values
        self.assertEqual(c.if_mode, 0x4C)       # RS-232C 8N1
        self.assertEqual(c.flow, 0x02)          # RTS/CTS, console V21+
        self.assertEqual(c.port, 10001)         # the serial tunnel
        self.assertEqual(c.connect_mode, 0xC4)  # accept + manual connection
        self.assertEqual(c.disconn_mode, 0x80)  # disconnect on DTR drop
        self.assertEqual(c.disconn_time, "01:30")
        self.assertEqual(c.baudrate, 9600)

    def test_mac_is_in_the_lantronix_range(self):
        c = xport.XPortConfig()
        self.assertEqual(tuple(c.mac[:3]), xport.LANTRONIX_OUI)
        self.assertEqual(len(c.mac), 6)

    def test_netmask_bits_map_to_a_dotted_mask(self):
        c = xport.XPortConfig()
        c.netmask_bits = 8
        self.assertEqual(c.netmask, "255.255.255.0")

    def test_config_persists_across_a_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "xport.json")
            a = xport.XPortConfig(p)
            a.ip = "192.168.1.55"
            a.port = 10002
            a.save()
            b = xport.XPortConfig(p)
            self.assertEqual(b.ip, "192.168.1.55")
            self.assertEqual(b.port, 10002)
            self.assertEqual(b.mac, a.mac)      # MAC is stable too

    def test_factory_defaults_keep_the_address(self):
        # reference/lantronix_setup_menu.md 8.2
        c = xport.XPortConfig()
        c.ip = "10.0.0.9"
        c.gateway = "10.0.0.1"
        c.port = 12345
        c.factory_defaults()
        self.assertEqual(c.ip, "10.0.0.9")      # kept
        self.assertEqual(c.gateway, "10.0.0.1")  # kept
        self.assertEqual(c.port, 10001)         # reset


class SetupRecord(unittest.TestCase):
    """The 120-byte record, per reference/.../protocol.md 3."""

    def rec(self, **kw):
        c = xport.XPortConfig()
        for k, v in kw.items():
            setattr(c, k, v)
        return c.setup_record()

    def test_it_is_120_bytes(self):
        self.assertEqual(len(self.rec()), 120)

    def test_ip_is_at_offset_0(self):
        r = self.rec(ip="192.168.0.1")
        self.assertEqual(tuple(r[0:4]), (192, 168, 0, 1))

    def test_host_bits_at_offset_6(self):
        r = self.rec(netmask_bits=8)
        self.assertEqual(r[6], 8)

    def test_password_at_offset_8(self):
        r = self.rec(telnet_password="ABCD")
        self.assertEqual(r[8:12], b"ABCD")

    def test_disabled_password_is_zeros(self):
        r = self.rec(telnet_password="")
        self.assertEqual(r[8:12], b"\x00\x00\x00\x00")

    def test_gateway_at_offset_12(self):
        r = self.rec(gateway="10.1.2.3")
        self.assertEqual(tuple(r[12:16]), (10, 1, 2, 3))

    def test_channel_1_block_at_0x10(self):
        r = self.rec()
        self.assertEqual(r[0x10], 0x4C)         # I/F mode
        self.assertEqual(r[0x12], 0x02)         # flow

    def test_port_is_little_endian_at_0x14(self):
        r = self.rec(port=10001)
        self.assertEqual(struct.unpack_from("<H", r, 0x14)[0], 10001)

    def test_connect_and_disconnect_modes(self):
        r = self.rec()
        self.assertEqual(r[0x1C], 0xC4)         # connect mode
        self.assertEqual(r[0x1D], 0x80)         # disconnect mode

    def test_disconn_time_split_to_minutes_and_seconds(self):
        r = self.rec(disconn_time="01:30")
        self.assertEqual(r[0x1E], 1)            # minutes
        self.assertEqual(r[0x1F], 30)           # seconds


class Discovery(unittest.TestCase):
    """The 77FEh responder, per reference/.../protocol.md 2."""

    def setUp(self):
        self.d = xport.Discovery(xport.XPortConfig())
        self.mac = self.d.config.mac

    def test_ignores_a_foreign_probe(self):
        self.assertIsNone(self.d.answer(b"\x01\x02\x03\x04"))
        self.assertIsNone(self.d.answer(b"\x00\x00\x00\x00"))

    def test_f6_info_is_30_bytes_with_mac_at_24(self):
        r = self.d.answer(b"\x00\x00\x00\xF6")
        self.assertEqual(len(r), 30)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF7")
        self.assertEqual(r[24:30], self.mac)

    def test_f6_carries_a_device_type_at_8(self):
        r = self.d.answer(b"\x00\x00\x00\xF6")
        self.assertEqual(r[8:10], xport.DEVICE_TYPE)

    def test_f4_version_is_32_bytes_with_string_at_16(self):
        r = self.d.answer(b"\x00\x00\x00\xF4")
        self.assertEqual(len(r), 32)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF5")
        self.assertIn(b"V6", r[16:32])

    def test_f8_setup_is_124_bytes(self):
        r = self.d.answer(b"\x00\x00\x00\xF8")
        self.assertEqual(len(r), 124)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF9")
        # the 120-byte record follows, IP at its offset 0 => response 4
        self.d.config.ip = "1.2.3.4"
        r = self.d.answer(b"\x00\x00\x00\xF8")
        self.assertEqual(tuple(r[4:8]), (1, 2, 3, 4))


class SetupMenu(unittest.TestCase):
    """The port-9999 setup walk, per reference/lantronix_setup_menu.md 3."""

    def session(self, path=None):
        self.sent = bytearray()
        cfg = xport.XPortConfig(path)
        s = xport.SetupSession(cfg, self.sent.extend)
        return cfg, s

    def text(self):
        return bytes(self.sent).decode("ascii", "replace")

    def test_the_banner_names_the_mac_and_the_gate(self):
        cfg, s = self.session()
        self.assertIn("MAC address " + xport.mac_hex(cfg.mac), self.text())
        self.assertIn("Press Enter for Setup Mode", self.text())

    def test_enter_opens_the_menu(self):
        _cfg, s = self.session()
        s.feed("")                              # the Enter at the gate
        self.assertIn("Change Setup:", self.text())
        self.assertIn("0 Server configuration", self.text())
        self.assertIn("1 Channel 1 configuration", self.text())
        self.assertIn("9 Save and exit", self.text())

    def test_the_gate_closes_after_five_seconds(self):
        _cfg, s = self.session()
        self.assertFalse(s.gate_expired())
        s.opened -= 6
        self.assertTrue(s.gate_expired())

    def test_server_config_sets_the_ip(self):
        cfg, s = self.session()
        s.feed("")                              # open
        s.feed("0")                             # server config
        self.assertIn("IP Address :", self.text())
        s.feed("192.168.1.50")                  # ip
        s.feed("")                              # gateway (N)
        s.feed("")                              # netmask keep
        s.feed("")                              # password (N)
        self.assertEqual(s.edit["ip"], "192.168.1.50")
        # not committed until save
        self.assertEqual(cfg.ip, "0.0.0.0")

    def test_channel_config_sets_the_port(self):
        cfg, s = self.session()
        s.feed("")
        s.feed("1")                             # channel config
        self.assertIn("Baudrate", self.text())
        s.feed("")                              # baud keep
        s.feed("")                              # if mode keep
        s.feed("")                              # flow keep
        s.feed("10002")                         # port
        for _ in range(8):                      # walk the rest, keeping
            s.feed("")
        self.assertEqual(s.edit["port"], 10002)

    def test_save_commits_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "xport.json")
            cfg, s = self.session(path)
            s.feed("")
            s.feed("0")
            s.feed("172.16.0.9")
            s.feed("")
            s.feed("")
            s.feed("")
            s.feed("9")                         # save and exit
            self.assertIn("Parameters stored", self.text())
            self.assertEqual(cfg.ip, "172.16.0.9")
            # and it reached the file
            reloaded = xport.XPortConfig(path)
            self.assertEqual(reloaded.ip, "172.16.0.9")

    def test_exit_without_save_discards(self):
        cfg, s = self.session()
        s.feed("")
        s.feed("0")
        s.feed("192.168.9.9")
        s.feed("")
        s.feed("")
        s.feed("")
        s.feed("8")                             # exit without save
        self.assertEqual(cfg.ip, "0.0.0.0")     # unchanged

    def test_factory_defaults_from_the_menu_keep_the_address(self):
        cfg, s = self.session()
        cfg.ip = "10.9.9.9"
        s.edit["ip"] = "10.9.9.9"
        s.edit["port"] = 40000
        s.feed("")
        s.feed("7")                             # factory defaults
        self.assertEqual(s.edit["port"], 10001)
        self.assertEqual(s.edit["ip"], "10.9.9.9")

    def test_hex_fields_take_hex(self):
        _cfg, s = self.session()
        s.feed("")
        s.feed("1")
        s.feed("")                              # baud
        s.feed("78")                            # I/F mode 78 = 7E1
        self.assertEqual(s.edit["if_mode"], 0x78)


class TelnetStripping(unittest.TestCase):
    def test_iac_negotiation_is_removed(self):
        # IAC WILL ECHO (255 251 1), then "0", then IAC DO x
        cleaned = xport._strip_iac(bytes([255, 251, 1]) + b"0"
                                   + bytes([255, 253, 3]))
        self.assertEqual(cleaned, b"0")

    def test_a_line_splits_on_cr_or_lf(self):
        line, rest = xport._split_line(bytearray(b"0\r\nnext"))
        self.assertEqual(line, b"0")
        self.assertEqual(bytes(rest), b"next")


if __name__ == "__main__":
    unittest.main()


class AutoIp(unittest.TestCase):
    """A real XPort ships with no static IP and self-assigns a link-local
    169.254.x.x address until one is set (reference/lantronix_deviceinstaller
    _protocol.md). There is no fixed factory default IP."""

    def test_an_unconfigured_card_has_no_static_ip(self):
        c = xport.XPortConfig()
        self.assertEqual(c.ip, "0.0.0.0")
        self.assertFalse(c.assigned())

    def test_it_is_reachable_at_a_link_local_address(self):
        c = xport.XPortConfig()
        auto = c.autoip()
        self.assertTrue(auto.startswith("169.254."))
        third = int(auto.split(".")[2])
        self.assertTrue(1 <= third <= 254)        # RFC 3927 pool
        self.assertEqual(c.effective_ip(), auto)  # no static: AutoIP wins

    def test_the_autoip_is_stable_for_a_given_mac(self):
        mac = bytes((0x00, 0x20, 0x4A, 0x11, 0x22, 0x33))
        a = xport.XPortConfig(mac=mac)
        b = xport.XPortConfig(mac=mac)
        self.assertEqual(a.autoip(), b.autoip())  # same card, same address

    def test_a_static_ip_takes_over(self):
        c = xport.XPortConfig()
        c.ip = "192.168.1.50"
        self.assertTrue(c.assigned())
        self.assertEqual(c.effective_ip(), "192.168.1.50")
