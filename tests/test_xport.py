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
"""The XPort emulation, against a real card.

These assertions used to trace to the Lantronix manuals. They now trace to a
live Veeder-Root TCP/IP Interface Module that was captured byte for byte, and
where the card contradicted the manual the card wins. The capture and the
places the two disagree are written up in
`reference/lantronix_xport_capture.md`; the differences that changed a test
are called out at the assertion.

`test_xport_capture.py` goes further and diffs the emulator against a stored
capture when there is one on the machine.
"""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import xport, xportmenu, xportrec, xportweb


class Config(unittest.TestCase):
    def test_defaults_are_the_tls350_golden_values(self):
        c = xport.XPortConfig()
        self.assertEqual(c.if_mode, 0x4C)       # RS-232C 8N1
        self.assertEqual(c.flow, 0x02)          # RTS/CTS, console V21+
        self.assertEqual(c.port, 10001)         # the serial tunnel
        self.assertEqual(c.connect_mode, 0xC4)  # accept + manual connection
        self.assertEqual(c.disconn_mode, 0x80)  # disconnect on DTR drop
        self.assertEqual(c.disconn_time, "01:30")
        self.assertEqual(c.baudrate, 9600)

    def test_resting_values_match_the_captured_card(self):
        """The settings no field of ours writes still have to be right."""
        c = xport.XPortConfig()
        self.assertEqual(c.keepalive, 45)
        self.assertEqual(c.arp_timeout, 600)
        self.assertEqual(c.mtu, 1400)
        self.assertEqual(c.http_port, 80)
        self.assertEqual(c.smtp_port, 25)
        self.assertEqual(c.hostlist_retry_count, 3)
        self.assertEqual(c.hostlist_retry_timeout, 250)
        self.assertEqual(c.recs.u8(3, xportrec.STCHAR_OFF), 0x0D)

    def test_mac_is_in_the_lantronix_range(self):
        c = xport.XPortConfig()
        self.assertEqual(tuple(c.mac[:3]), xport.LANTRONIX_OUI)
        self.assertEqual(len(c.mac), 6)

    def test_the_default_mac_is_not_the_captured_cards(self):
        """A simulator must not wear a real card's address.

        Two devices answering discovery with one MAC are shown by
        DeviceInstaller as a single unit -- the real card wins and the
        emulator is invisible -- and on a real network it is an address
        conflict. Found on a bench with both present.
        """
        self.assertNotEqual(xport.XPortConfig().mac, xport.CAPTURED_MAC)
        self.assertEqual(xport.default_mac(captured=True), xport.CAPTURED_MAC)

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
            self.assertEqual(b.mac, a.mac)

    def test_a_reload_keeps_bytes_no_field_of_ours_names(self):
        """A record byte the emulator does not understand must survive."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "xport.json")
            a = xport.XPortConfig(p)
            a.recs.u8(7, 40, 0x5A)              # a GPIO byte nothing reads
            a.save()
            b = xport.XPortConfig(p)
            self.assertEqual(b.recs.u8(7, 40), 0x5A)

    def test_factory_defaults_keep_the_address(self):
        c = xport.XPortConfig()
        c.ip = "10.0.0.9"
        c.gateway = "10.0.0.1"
        c.port = 12345
        c.factory_defaults()
        self.assertEqual(c.ip, "10.0.0.9")      # kept
        self.assertEqual(c.gateway, "10.0.0.1")  # kept
        self.assertEqual(c.port, 10001)         # reset
        # The card rests at connect mode C0, not the TLS-350's C4.
        self.assertEqual(c.connect_mode, 0xC0)


class Records(unittest.TestCase):
    """The record model, against offsets read off the card."""

    def test_the_records_are_the_lengths_the_card_serves(self):
        r = xportrec.SetupRecords()
        self.assertEqual(len(r.get(0)), 120)
        for n in (3, 4, 5, 6, 7, 8, 9):
            self.assertEqual(len(r.get(n)), 126)

    def test_baud_codes_are_the_cards_own_table(self):
        # Not an ordering anyone would guess: 38400 is code 0.
        self.assertEqual(xportrec.baud_code(38400), 0x00)
        self.assertEqual(xportrec.baud_code(9600), 0x02)
        self.assertEqual(xportrec.baud_code(300), 0x07)
        self.assertEqual(xportrec.baud_of(0x02), 9600)

    def test_xml_round_trips_every_byte(self):
        import base64
        import re
        a = xportrec.SetupRecords()
        a.u8(3, 100, 0x77)
        a.u8(6, 90, 0x11)
        xml = a.to_xml()
        back = {}
        for num, data in re.findall(
                r"<NUM>(\w+)</NUM>\s*<DATA>([^<]*)</DATA>", xml):
            back[int(num)] = base64.b64decode(data)
        for n in xportrec.REC_NUMBERS:
            self.assertEqual(back[n], a.get(n))

    def test_a_posted_record_replaces_that_record_only(self):
        import base64
        r = xportrec.SetupRecords()
        r.u8(3, 10, 0x99)
        blob = base64.b64encode(bytes([0x42] * 120)).decode()
        changed = r.load_posted({"rec0": blob})
        self.assertEqual(changed, [0])
        self.assertEqual(r.get(0)[0], 0x42)
        self.assertEqual(r.u8(3, 10), 0x99)     # record 3 untouched

    def test_record_four_is_never_posted_back(self):
        """The card's own page has no field for record 4 and omits it."""
        self.assertNotIn(4, xportrec.POSTED_RECORDS)


class SetupRecord(unittest.TestCase):
    """Record 0, which is what DeviceInstaller reads over 77FEh."""

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

    def test_keepalive_at_offset_7(self):
        self.assertEqual(self.rec()[7], 45)

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
        self.assertEqual(r[0x11], 0x02)         # baud code for 9600
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

    def test_channel_2_is_present_and_rests_on_10002(self):
        """The record carries a second channel the XPort has no port for."""
        r = self.rec()
        self.assertEqual(struct.unpack_from("<H", r, 64 + 4)[0], 10002)


class Discovery(unittest.TestCase):
    """The 77FEh responder, against the captured card's replies."""

    def setUp(self):
        self.d = xport.Discovery(xport.XPortConfig())
        self.mac = self.d.config.mac

    def test_ignores_a_foreign_probe(self):
        self.assertIsNone(self.d.answer(b"\x01\x02\x03\x04"))
        self.assertIsNone(self.d.answer(b"\x00\x00\x00\x00"))

    def test_only_four_byte_frames_are_answered(self):
        """An over-long frame made the real card leak a network buffer and
        corrupt its own configuration. The emulator declines it instead."""
        self.assertIsNone(self.d.answer(b"\x00\x00\x00\xF8\x00\x00\x00\x00"))
        self.assertIsNone(self.d.answer(b"\x00\x00\x00\xF6" + b"\x00" * 60))

    def test_f6_info_is_30_bytes_with_mac_at_24(self):
        r = self.d.answer(b"\x00\x00\x00\xF6")
        self.assertEqual(len(r), 30)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF7")
        self.assertEqual(r[24:30], self.mac)

    def test_f6_body_matches_the_captured_card(self):
        r = self.d.answer(b"\x00\x00\x00\xF6")
        self.assertEqual(r[4:24].hex().upper(),
                         "0020500658354B0FFF13000062A7E896FF000000")

    def test_f4_version_is_33_bytes_with_the_string_at_16(self):
        r = self.d.answer(b"\x00\x00\x00\xF4")
        self.assertEqual(len(r), 33)            # 33, not the 32 assumed once
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF5")
        self.assertEqual(r[16:23], b"6.5.0.7")
        self.assertEqual(r[32], 0x06)

    def test_the_flag_byte_variant_of_a_query_is_answered(self):
        """DeviceInstaller sends `00 01 00 F6` as well as `00 00 00 F6`.

        The card answers both with the same F7. Rejecting the variant on its
        second byte made the emulator look unreachable to the tool while
        still answering dsearch, which sends only the plain form.
        """
        plain = self.d.answer(b"\x00\x00\x00\xF6")
        variant = self.d.answer(b"\x00\x01\x00\xF6")
        self.assertEqual(variant, plain)

    def test_e2_returns_an_erased_record(self):
        """E2 -> D2, and the reply opcode is not the request plus one."""
        r = self.d.answer(b"\x00\x00\x00\xE2")
        self.assertEqual(len(r), 130)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xD2")
        self.assertEqual(r[4:], b"\xFF" * 126)

    def test_e0_to_e7_read_the_setup_records(self):
        """This is how a tool reads a card's configuration.

        One opcode per record, the reply opcode being the request less 0x10.
        Read off the wire between DeviceInstaller and a real card: it asks
        F8, then E3, E5, E6, E7 in turn, and that exchange -- not discovery
        -- is what fills the Configuration Records tab.
        """
        for op in range(0xE0, 0xE8):
            r = self.d.answer(bytes([0, 0, 0, op]))
            self.assertIsNotNone(r, "E%X must be answered" % (op & 0xF))
            self.assertEqual(r[0:4], bytes([0, 0, 0, op - 0x10]))
            # record 0 is the short one; the rest are 126 bytes
            self.assertEqual(len(r), 124 if op == 0xE0 else 130)

    def test_e0_returns_the_same_record_as_f8(self):
        self.assertEqual(self.d.answer(b"\x00\x00\x00\xE0")[4:],
                         self.d.answer(b"\x00\x00\x00\xF8")[4:])

    def test_e3_carries_the_hostlist_and_expert_record(self):
        r = self.d.answer(b"\x00\x00\x00\xE3")[4:]
        self.assertEqual(r[0], 3)          # hostlist retry counter
        self.assertEqual(r[1], 250)        # hostlist retry timeout

    def test_the_card_falls_silent_above_e7(self):
        """A real card answers E0-E7 and nothing else in that range."""
        for op in range(0xE8, 0xF0):
            self.assertIsNone(self.d.answer(bytes([0, 0, 0, op])),
                              "E%X must not be answered" % (op & 0xF))

    def test_a_frame_with_a_bad_third_byte_is_still_refused(self):
        self.assertIsNone(self.d.answer(b"\x00\x00\x01\xF6"))

    def test_fa_is_acknowledged(self):
        self.assertEqual(self.d.answer(b"\x00\x00\x00\xFA"),
                         b"\x00\x00\x00\xFB")

    def test_replies_go_out_through_the_listening_socket(self):
        """So they come FROM port 30718, as the card's do.

        A reply sent through a socket bound to `(ip, 0)` carries an
        ephemeral source port. DeviceInstaller tolerates that for the
        discovery broadcast and still lists the card, then fails the
        configuration read that follows -- every frame answered, and still
        "the configuration was not received from the device". Answering from
        the wrong port is worse than answering from the wrong address.
        """
        sent = []

        class FakeSock:
            def sendto(self, data, addr):
                sent.append((data, addr))

        self.d.sock = FakeSock()
        self.d._reply(b"\x00\x00\x00\xF7", ("10.0.0.9", 30718))
        self.assertEqual(len(sent), 1,
                         "the reply must go through the listening socket")
        self.assertEqual(sent[0][0], b"\x00\x00\x00\xF7")

    def test_the_same_protocol_is_served_over_tcp(self):
        """77FEh answers on TCP 30718 as well as UDP, and that is the one
        that matters: a tool finds a card over the UDP broadcast but reads
        its configuration over TCP. Answering only UDP gets the card listed
        and then fails with "the configuration was not received from the
        device", which is what a real DeviceInstaller did to this emulator.
        """
        self.assertTrue(hasattr(self.d, "serve_tcp"))
        self.assertTrue(hasattr(self.d, "_tcp_session"))

    def test_pipelined_tcp_frames_are_each_answered(self):
        """The card takes several requests on one connection."""
        sent = bytearray()

        class FakeConn:
            def __init__(self):
                self.data = (b"\x00\x00\x00\xF8" b"\x00\x00\x00\xF6"
                             b"\x00\x00\x00\xE2")
                self.done = False

            def settimeout(self, _t):
                pass

            def recv(self, _n):
                if self.done:
                    return b""
                self.done = True
                return self.data

            def sendall(self, b):
                sent.extend(b)

            def close(self):
                pass

        self.d._tcp_session(FakeConn(), ("10.0.0.9", 30718))
        self.assertEqual(len(sent), 124 + 30 + 130)
        self.assertEqual(bytes(sent[0:4]), b"\x00\x00\x00\xF9")
        self.assertEqual(bytes(sent[124:128]), b"\x00\x00\x00\xF7")
        self.assertEqual(bytes(sent[154:158]), b"\x00\x00\x00\xD2")

    def test_f8_setup_is_124_bytes(self):
        r = self.d.answer(b"\x00\x00\x00\xF8")
        self.assertEqual(len(r), 124)
        self.assertEqual(r[0:4], b"\x00\x00\x00\xF9")
        self.d.config.ip = "1.2.3.4"
        r = self.d.answer(b"\x00\x00\x00\xF8")
        self.assertEqual(tuple(r[4:8]), (1, 2, 3, 4))


class SetupMenu(unittest.TestCase):
    """The port-9999 walk, against the captured transcript."""

    def session(self, path=None):
        self.sent = bytearray()
        cfg = xport.XPortConfig(path)
        s = xport.SetupSession(cfg, self.sent.extend)
        return cfg, s

    def text(self):
        return bytes(self.sent).decode("latin-1")

    def test_the_banner_names_the_mac_and_the_gate(self):
        cfg, s = self.session()
        self.assertIn("MAC address " + xport.mac_hex(cfg.mac), self.text())
        self.assertIn("Press Enter for Setup Mode", self.text())

    def test_the_banner_opens_with_telnet_negotiation(self):
        _cfg, _s = self.session()
        self.assertTrue(bytes(self.sent).startswith(
            b"\xff\xfb\x01\xff\xfb\x03"))

    def test_lines_end_the_way_the_card_ends_them(self):
        """A bare CR with the NUL telnet requires, not CR LF."""
        _cfg, _s = self.session()
        self.assertIn(b"\r\x00", bytes(self.sent))

    def test_enter_opens_the_menu(self):
        _cfg, s = self.session()
        s.feed("")
        t = self.text()
        self.assertIn("Change Setup:", t)
        # The card's labels are short: "0 Server", not "0 Server
        # configuration" as the manual's example had it.
        self.assertIn("  0 Server", t)
        self.assertIn("  1 Channel 1", t)
        self.assertIn("  7 Defaults", t)
        self.assertIn("9 Save and exit            Your choice ? ", t)

    def test_the_dump_reports_the_settings(self):
        _cfg, s = self.session()
        s.feed("")
        t = self.text()
        self.assertIn("*** basic parameters", t)
        self.assertIn("Hardware: Ethernet TPI", t)
        self.assertIn("Baudrate 9600, I/F Mode 4C, Flow 02", t)
        self.assertIn("Port 10001", t)
        self.assertIn("Connect Mode : C4", t)
        self.assertIn("TCP Keepalive    : 45s", t)

    def test_the_gate_closes_after_five_seconds(self):
        _cfg, s = self.session()
        self.assertFalse(s.gate_expired())
        s.opened -= 6
        self.assertTrue(s.gate_expired())

    def test_server_config_takes_the_ip_one_octet_at_a_time(self):
        """The card prompts per octet; it does not take a dotted string."""
        _cfg, s = self.session()
        s.feed("")
        s.feed("0")
        self.assertIn("IP Address : (000) ", self.text())
        for part in ("192", "168", "1", "50"):
            s.feed(part)
        self.assertEqual(s.edit.ip, "192.168.1.50")
        self.assertIn("Set Gateway IP Address", self.text())

    def test_enter_alone_keeps_an_octet(self):
        cfg, s = self.session()
        cfg.ip = "10.20.30.40"
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed("")
        s.feed("0")
        for _ in range(4):
            s.feed("")
        self.assertEqual(s.edit.ip, "10.20.30.40")

    def test_answering_yes_asks_for_the_gateway(self):
        _cfg, s = self.session()
        s.feed("")
        s.feed("0")
        for part in ("192", "168", "1", "50"):
            s.feed(part)
        s.feed("Y")
        self.assertIn("Gateway IP addr", self.text())
        for part in ("192", "168", "1", "1"):
            s.feed(part)
        self.assertEqual(s.edit.gateway, "192.168.1.1")

    def test_channel_config_sets_the_port(self):
        _cfg, s = self.session()
        s.feed("")
        s.feed("1")
        self.assertIn("Baudrate (9600) ? ", self.text())
        s.feed("")                              # baudrate
        s.feed("")                              # I/F mode
        s.feed("")                              # flow
        s.feed("10002")                         # port
        self.assertEqual(s.edit.port, 10002)

    def test_hex_fields_take_hex(self):
        _cfg, s = self.session()
        s.feed("")
        s.feed("1")
        s.feed("")                              # baudrate
        s.feed("3C")                            # I/F mode
        self.assertEqual(s.edit.if_mode, 0x3C)

    def test_the_hostlist_branch_replaces_the_remote_address(self):
        """Connect mode bit 0x20 swaps the prompts, as on the card."""
        cfg, _s = self.session()
        cfg.connect_mode = 0x67
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed("")
        s.feed("1")
        for _ in range(8):                      # up to the branch
            s.feed("")
        self.assertIn("Change Hostlist ?", self.text())
        self.assertNotIn("Remote IP Address", self.text())

    def test_telnet_mode_adds_the_terminal_name_prompt(self):
        cfg, _s = self.session()
        cfg.disconn_mode = 0xEA                 # bit 0x20 set
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed("")
        s.feed("1")
        for _ in range(20):
            s.feed("")
        self.assertIn("Terminal name", self.text())

    def test_a_udp_connect_mode_asks_for_a_datagram_type(self):
        """Low nibble C is UDP, and it is tested before the host-list bit.

        2C has bit 0x20 set but is datagram, not host list; 27 is the host
        list. Keying on the bit alone offered the host-list editor for a UDP
        mode, which the card never does. Both confirmed on the bench.
        """
        cfg, _s = self.session()
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed_bytes(b"\r\n1\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")          # baud, I/F, flow, port
        self.sent.clear()
        s.feed_bytes(b"2C\r\n")            # connect mode
        self.assertIn(b"Datagram Type", bytes(self.sent))
        self.assertNotIn(b"Send '+++'", bytes(self.sent))

    def test_a_udp_walk_returns_to_the_menu_after_the_datagram_type(self):
        cfg, _s = self.session()
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed_bytes(b"\r\n1\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")
        s.feed_bytes(b"2C\r\n")
        self.sent.clear()
        s.feed_bytes(b"\r\n")              # datagram type
        self.assertIn(b"Change Setup:", bytes(self.sent))

    def test_the_hostlist_prints_addresses_padded_to_three_digits(self):
        """`01. IP : 010.000.000.000  Port : 10001` -- the card's own form.

        Every other address the menu shows is unpadded. The host list is the
        exception, and the label is "IP :", not "IP address :".
        """
        cfg, _s = self.session()
        cfg.connect_mode = 0x27
        cfg.set_hostlist(0, "10.0.0.0", 10001)
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed("")
        self.assertIn("01. IP : 010.000.000.000  Port : 10001", self.text())

    def test_the_hostlist_port_prompt_is_the_cards(self):
        cfg, _s = self.session()
        cfg.connect_mode = 0x27
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed_bytes(b"\r\n1\r\n")
        for _ in range(8):
            s.feed_bytes(b"\r\n")
        s.feed_bytes(b"Y")                 # change hostlist
        for part in (b"10", b"0", b"0", b"5"):
            s.feed_bytes(part + b"\r\n")
        self.assertIn("     Port :  (0) ? ", self.text())

    def test_the_password_prompt_has_no_space_before_its_colon(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n0\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")          # the four IP octets
        s.feed_bytes(b"N")                 # gateway
        s.feed_bytes(b"\r\n")              # netmask
        self.sent.clear()
        s.feed_bytes(b"Y")                 # change password
        self.assertEqual(bytes(self.sent),
                         b"Y\r\x00\nEnter new Password: ")

    def test_enter_takes_the_default_a_yes_no_prompt_shows(self):
        """With a gateway set the default is (Y), so Enter walks into it.

        The emulator used to treat a bare Enter as "no" whatever the prompt
        showed, which skipped the gateway octets on a configured card and
        put every later answer in the wrong field.
        """
        cfg, _s = self.session()
        cfg.ip = "172.30.9.14"
        cfg.gateway = "172.30.9.17"
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed_bytes(b"\r\n0\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")
        self.sent.clear()
        s.feed_bytes(b"\r\n")              # Enter at "Set Gateway (Y) ?"
        self.assertIn(b"Gateway IP addr", bytes(self.sent))

    def test_defaults_prints_nothing_but_the_menu(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n")
        self.sent.clear()
        s.feed_bytes(b"7\r\n")
        self.assertEqual(bytes(self.sent), b"7\n" + xportmenu.NL
                         + xportmenu.MENU)

    def test_save_commits_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "xport.json")
            cfg, s = self.session(path)
            s.feed("")
            s.feed("0")
            for part in ("172", "16", "0", "9"):
                s.feed(part)
            s.feed("")                          # gateway (N)
            s.feed("")                          # netmask keep
            s.feed("")                          # password (N)
            s.feed("9")                         # save and exit
            self.assertIn("Parameters stored", self.text())
            self.assertEqual(cfg.ip, "172.16.0.9")
            reloaded = xport.XPortConfig(path)
            self.assertEqual(reloaded.ip, "172.16.0.9")

    def test_exit_without_save_discards(self):
        cfg, s = self.session()
        s.feed("")
        s.feed("0")
        for part in ("172", "16", "0", "9"):
            s.feed(part)
        s.feed("")
        s.feed("")
        s.feed("")
        s.feed("8")
        self.assertIn("exiting without save !", self.text())
        self.assertEqual(cfg.ip, "0.0.0.0")     # untouched

    def test_factory_defaults_from_the_menu_keep_the_address(self):
        cfg, _s = self.session()
        cfg.ip = "10.0.0.5"
        s = xport.SetupSession(cfg, self.sent.extend)
        s.feed("")
        s.feed("7")
        self.assertEqual(s.edit.ip, "10.0.0.5")
        self.assertEqual(s.edit.port, 10001)

    def test_typed_characters_are_echoed(self):
        """The card negotiates WILL ECHO, so it echoes what is typed."""
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n")
        self.sent.clear()
        s.feed_bytes(b"0")
        self.assertEqual(bytes(self.sent), b"0")

    def test_one_enter_advances_one_prompt(self):
        """A telnet client sends CR LF; that is one Enter, not two.

        The line-oriented tests above cannot see this: they hand the session
        whole lines. Driven with real client bytes, an emulator that treats
        the LF as a second empty line walks two prompts per keypress, and a
        technician typing an address puts each octet in the wrong field.
        """
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n")                   # the gate
        s.feed_bytes(b"0\r\n")                  # Server
        self.sent.clear()
        s.feed_bytes(b"\r\n")                   # keep the first octet
        self.assertEqual(bytes(self.sent), b".(000) ")

    def test_a_cr_nul_line_ending_is_also_one_enter(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\x00")
        s.feed_bytes(b"0\r\x00")
        self.sent.clear()
        s.feed_bytes(b"\r\x00")
        self.assertEqual(bytes(self.sent), b".(000) ")

    def test_an_address_typed_over_the_wire_lands_in_order(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n0\r\n")
        for part in (b"192", b"168", b"1", b"50"):
            s.feed_bytes(part + b"\r\n")
        self.assertEqual(s.edit.ip, "192.168.1.50")

    def test_a_yes_no_prompt_answers_on_the_keypress(self):
        """"Y" decides the question; it does not wait for Enter."""
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n0\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")               # keep the four IP octets
        self.sent.clear()
        s.feed_bytes(b"Y")                      # no Enter
        self.assertIn(b"Gateway IP addr", bytes(self.sent))

    def test_the_enter_after_a_y_falls_through_to_the_next_prompt(self):
        """Typing Y then Enter shifts an address by one octet.

        On the card the Y answers the gateway question and the Enter it is
        natural to press after it is taken by the FIRST OCTET prompt, which
        keeps 000 and moves on -- so 172.30.9.17 typed after it lands as
        0.172.30.9. This was found by doing it to a real card. It is
        emulated because it is exactly the trap a trainee needs to meet
        here rather than on a live site.
        """
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n0\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")
        s.feed_bytes(b"Y\r\n")                  # the Enter is one too many
        for part in (b"172", b"30", b"9", b"17"):
            s.feed_bytes(part + b"\r\n")
        self.assertEqual(s.edit.gateway, "0.172.30.9")

    def test_y_without_the_enter_sets_the_address_correctly(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n0\r\n")
        for _ in range(4):
            s.feed_bytes(b"\r\n")
        s.feed_bytes(b"Y")                      # no Enter after it
        for part in (b"172", b"30", b"9", b"17"):
            s.feed_bytes(part + b"\r\n")
        self.assertEqual(s.edit.gateway, "172.30.9.17")

    def test_backspace_erases(self):
        _cfg, s = self.session()
        s.feed_bytes(b"\r\n")
        self.sent.clear()
        s.feed_bytes(b"01\x08")
        self.assertEqual(bytes(self.sent), b"01\b \b")

    def test_iac_negotiation_is_removed(self):
        cleaned = xport._strip_iac(bytes([255, 251, 1]) + b"0"
                                   + bytes([255, 253, 3]))
        self.assertEqual(cleaned, b"0")

    def test_a_line_splits_on_cr_or_lf(self):
        line, rest = xport._split_line(bytearray(b"0\r\nnext"))
        self.assertEqual(line, b"0")
        self.assertEqual(bytes(rest), b"next")


class WebManager(unittest.TestCase):
    """The web pages, which edit the same records the menu does."""

    def setUp(self):
        self.cfg = xport.XPortConfig()
        self.state = xportweb.WebState(self.cfg)

    def test_ok_stores_into_a_pending_copy_only(self):
        e = self.state.edit()
        xportweb.store_conn(e, {"lport": ["10005"]})
        self.assertEqual(e.port, 10005)
        self.assertEqual(self.cfg.port, 10001)  # the card is unchanged

    def test_apply_settings_commits_the_pending_copy(self):
        e = self.state.edit()
        xportweb.store_conn(e, {"lport": ["10005"]})
        self.state.commit()
        self.assertEqual(self.cfg.port, 10005)

    def test_navigating_away_without_ok_changes_nothing(self):
        self.state.edit()
        self.state.discard()
        self.assertEqual(self.cfg.port, 10001)

    def test_the_network_page_stores_an_address(self):
        e = self.state.edit()
        xportweb.store_net(e, {
            "dynip": ["0"],
            "ipaddr1": ["172"], "ipaddr2": ["30"], "ipaddr3": ["9"],
            "ipaddr4": ["14"],
            "ipmask1": ["255"], "ipmask2": ["255"], "ipmask3": ["255"],
            "ipmask4": ["0"],
            "ipgw1": ["172"], "ipgw2": ["30"], "ipgw3": ["9"],
            "ipgw4": ["17"]})
        self.assertEqual(e.ip, "172.30.9.14")
        self.assertEqual(e.gateway, "172.30.9.17")
        self.assertEqual(e.netmask_bits, 8)

    def test_the_serial_page_rebuilds_the_if_mode_byte(self):
        e = self.state.edit()
        xportweb.store_serial(e, {"serproto": ["1"], "data": ["8"],
                                  "parity": ["0"], "stop": ["1"],
                                  "baud": ["9600"], "flow": ["2"]})
        self.assertEqual(e.if_mode, 0x4C)
        self.assertEqual(e.baudrate, 9600)
        self.assertEqual(e.flow, 0x02)

    def test_a_bad_number_keeps_the_current_value(self):
        e = self.state.edit()
        xportweb.store_conn(e, {"lport": ["not a port"]})
        self.assertEqual(e.port, 10001)

    def test_the_menu_and_the_web_share_one_set_of_records(self):
        e = self.state.edit()
        xportweb.store_conn(e, {"lport": ["10009"]})
        self.state.commit()
        sent = bytearray()
        s = xport.SetupSession(self.cfg, sent.extend)
        s.feed("")
        self.assertIn("Port 10009", bytes(sent).decode("latin-1"))

    def test_setuprec_xml_carries_every_record(self):
        xml = self.cfg.recs.to_xml()
        for n in xportrec.REC_NUMBERS:
            self.assertIn("<NUM>%02d</NUM>" % n, xml)
        self.assertIn("<SCR>", xml)


class Address(unittest.TestCase):
    def test_an_unconfigured_card_has_no_static_ip(self):
        c = xport.XPortConfig()
        self.assertFalse(c.assigned())

    def test_it_is_reachable_at_a_link_local_address(self):
        c = xport.XPortConfig()
        self.assertTrue(c.effective_ip().startswith("169.254."))

    def test_the_autoip_is_stable_for_a_given_mac(self):
        mac = bytes((0x00, 0x20, 0x4A, 1, 2, 3))
        a = xport.XPortConfig(mac=mac)
        b = xport.XPortConfig(mac=mac)
        self.assertEqual(a.autoip(), b.autoip())

    def test_a_static_ip_takes_over(self):
        c = xport.XPortConfig()
        c.ip = "192.168.5.5"
        self.assertEqual(c.effective_ip(), "192.168.5.5")


if __name__ == "__main__":
    unittest.main()
