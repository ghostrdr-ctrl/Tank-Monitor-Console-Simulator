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
"""The Lantronix XPort inside the TLS-350's TCP/IP Interface Module.

A real TLS-350 does not speak Ethernet. Veeder-Root's TCP/IP Interface
Module (VR P/N 331870-001) is a Lantronix XPort -- a serial-to-Ethernet
device server -- wired to the console's RS-232 port. Everything a network
client does to a networked TLS-350 is really done to this XPort:

  * TCP 10001 is the SERIAL TUNNEL. Bytes sent here go down the RS-232 line
    to the console and its answers come back. That tunnel is `wire.serve`;
    this module does not touch it, it only describes it.
  * TCP 9999 is the SETUP MENU, in `xportmenu`. Telnet to it, press Enter
    within the window, and the XPort prints its configuration and a
    "Change Setup:" menu. This is where a technician programs the card.
  * TCP 80 is the WEB MANAGER, in `xportweb`: the same settings as a set of
    forms, reading and writing the same records.
  * UDP 30718 (0x77FE) is DISCOVERY. Lantronix DeviceInstaller broadcasts a
    four-byte probe and every XPort on the subnet answers with its type and
    MAC, which is how the tool finds a card with no known address. The same
    port serves the configuration record DeviceInstaller displays.

Where the fidelity comes from
-----------------------------
This module used to be written from the Lantronix XPort User Guide, with the
banner string and the discovery bytes marked as representative rather than
real. They are no longer guesses: a live Veeder-Root TCP/IP Interface Module
was captured byte for byte -- its discovery responses, its whole setup-menu
walk, and its web manager's configuration records -- and this emulator
reproduces what the card actually sent. `reference/lantronix_xport_capture.md`
holds the capture, the cross-checks and the few details still inferred.

The card captured reports firmware V6.5.0.7 (070919) and the product tag
XPTEXE, the encrypted-firmware variant. Those are the strings used here, so a
tool that fingerprints the card sees a real one.
"""
import base64
import json
import os
import socket
import threading

from . import atomicfile
from . import exposed
from .exposed import BENCH as BENCH_LEVEL
from . import xportrec as R

# The port a real XPort reserves for its setup menu, and the one
# DeviceInstaller broadcasts discovery to. Neither is configurable on the
# hardware; both are fixed here too.
SETUP_PORT = 9999
DISCOVERY_PORT = 30718            # 0x77FE
WEB_PORT = 80

# Lantronix's own OUI. A real card's MAC begins with these three bytes, so
# a discovered emulator that DeviceInstaller is to believe must too.
LANTRONIX_OUI = (0x00, 0x20, 0x4A)

# Read off the captured card's banner. XPTEXE is the encrypted-firmware
# product tag; the plain variant prints XPTEX.
FIRMWARE_VERSION = "V6.5.0.7"
FIRMWARE_DATE = "070919"
PRODUCT_TAG = "XPTEXE"

# The captured card's own MAC, kept as the emulator's default so a tool that
# was pointed at the real card sees the same unit. `default_mac` derives a
# distinct one per host when several simulators share a bench.
CAPTURED_MAC = bytes((0x00, 0x20, 0x4A, 0xAD, 0x64, 0x70))

CRLF = b"\r\n"


def default_mac(captured=False, anonymous=False):
    """A MAC in Lantronix's range, unique to this machine.

    A real card's is burned in, and no two cards share one. The emulator
    derives its own from the host name so that a simulator and a real card
    can sit on the same bench without colliding -- which they otherwise do,
    loudly: two devices answering discovery with one MAC are shown by
    DeviceInstaller as a single unit, and the real card wins. That is also an
    address conflict on a real network.

    `captured=True` returns the MAC of the card this emulation was written
    from, for reproducing a capture byte for byte. Do not use it on a network
    where that card is present.

    `anonymous=True` draws the last three bytes instead of deriving them,
    because on a public address the host name is not the emulator's to give
    away. The derivation is a plain SHA-256 of `socket.gethostname()`: three
    of its bytes are published in every discovery response, in the setup
    menu's banner and in the web manager, and three bytes of a known hash is
    enough to confirm a guessed host name and more than enough to tie two
    deployments to one operator. On a bench that is a feature -- the MAC is
    stable, so DeviceInstaller sees the same unit every run. On the internet
    it is a leak, and `--card-mac` is how an exposed card gets a stable
    identity that is nobody's host name. See `exposed.Policy`.
    """
    if captured:
        return CAPTURED_MAC
    if anonymous:
        return bytes(LANTRONIX_OUI) + os.urandom(3)
    import hashlib
    seed = hashlib.sha256(socket.gethostname().encode()).digest()
    return bytes(LANTRONIX_OUI) + seed[:3]


def mac_hex(mac, sep=""):
    return sep.join("%02X" % b for b in mac)


class XPortConfig:
    """The XPort's non-volatile configuration.

    Backed by the card's own setup records (`xportrec`), so the setup menu,
    the web manager and DeviceInstaller are three views of one set of bytes
    and cannot drift apart. The named properties below are the fields those
    interfaces show; anything the emulator does not name still survives a
    read-modify-write, as it does on the hardware.

    Persisted beside the console's own state so a card keeps its address
    between runs, the way a real one keeps it in Flash.
    """

    # The TLS-350's required settings. A card shipped for a TLS-350 is
    # programmed to these; VR's install guide gives them as the values to
    # enter. They are the emulator's power-on defaults so the simulated card
    # behaves as a site's would.
    DEFAULTS = {
        "ip": "0.0.0.0",                # 0.0.0.0 => "not set"
        "gateway": "0.0.0.0",
        "netmask_bits": 8,              # host bits; 8 => 255.255.255.0
        "telnet_password": "",
        "baudrate": 9600,
        "if_mode": 0x4C,                # RS-232C, 8 bit, no parity, 1 stop
        "flow": 0x02,                   # RTS/CTS (console SW V21+)
        "port": 10001,                  # the serial tunnel
        "connect_mode": 0xC4,           # always accept + manual connection
        "remote_ip": "0.0.0.0",
        "remote_port": 0,
        "disconn_mode": 0x80,           # disconnect on DTR drop
        "flush_mode": 0x00,
        "disconn_time": "01:30",        # 90s serial-idle timeout
        "send_char_1": 0x00,
        "send_char_2": 0x00,
    }

    # What a real card holds after option 7, "Defaults". Taken from the
    # captured card, which was sitting at them: 9600 8N1 no flow, tunnel on
    # 10001, connect mode C0, keepalive 45 s, ARP 600 s, hostlist retry 3
    # every 250 ms, start char CR, monitor mode on, everything in Security
    # enabled except ECHO and the enhanced password.
    FACTORY = {
        "baudrate": 9600,
        "if_mode": 0x4C,
        "flow": 0x00,
        "port": 10001,
        "connect_mode": 0xC0,
        "remote_ip": "0.0.0.0",
        "remote_port": 0,
        "disconn_mode": 0x00,
        "flush_mode": 0x00,
        "disconn_time": "00:00",
        "send_char_1": 0x00,
        "send_char_2": 0x00,
    }

    def __init__(self, path=None, mac=None, records=None):
        self.path = path
        self.mac = mac or default_mac()
        self._lock = threading.Lock()
        self.recs = R.SetupRecords(records)
        # Security and the handful of expert settings the records do not
        # carry a documented bit for are held alongside them; each is a
        # yes/no the menu prints and takes.
        self.security = {
            "snmp": True, "telnet_setup": True, "tftp": True,
            "port_77fe": True, "web_server": True, "web_setup": True,
            "echo": False, "enhanced_password": False, "port_77f0": True,
        }
        self.snmp_community = "public"
        self.alternate_mac = False
        self.rs485_high = False
        if records is None:
            self.apply(self.DEFAULTS)
            self._seed_factory_records()
        self.load()

    def _seed_factory_records(self):
        """Put the card's own resting values into the records.

        These are the bytes a card holds that no menu field of ours writes:
        the host-list retry pair, the start character, the ARP timeout and
        the TCP keepalive. Without them a fresh emulator would show zeroes
        where a real card shows 3 / 250 / CR / 600 / 45.
        """
        self.recs.u8(0, R.TCPKEEP_OFF, 45)
        self.recs.u8(3, R.HLRCNT_OFF, 3)
        self.recs.u8(3, R.HLRTO_OFF, 250)
        self.recs.u8(3, R.STCHAR_OFF, 0x0D)
        self.recs.le16(3, R.ARPTO_OFF, 600)
        # Channel 2 exists in the record even though the XPort has one
        # serial port; the captured card keeps it at 4C / 9600 / 10002.
        self.recs.ch(1, R.LINEIF_OFF, value=0x4C)
        self.recs.ch(1, R.SPEED_OFF, value=R.baud_code(9600))
        self.recs.ch(1, R.LPORT_OFF, "le16", 10002)
        self.recs.ch(1, R.CMODE_OFF, value=0xC0)
        # A card at defaults has both modem-mode courtesies on: it sends
        # '+++' and shows the IP after 'RING'. Confirmed on a card that had
        # just been factory reset, which printed both as enabled.
        self.recs.bit(3, R.SENDIP_OFF, R.SENDPLUS_BIT, True)
        self.recs.bit(3, R.SENDIP_OFF, R.SHOWIP_BIT, True)
        # The three e-mail triggers rest disabled (TSER low nibble 3), at
        # priority L (TPRIO 3), notifying at most once a second and never
        # re-notifying -- the values the captured card holds.
        for base in R.TRIG_OFF:
            self.recs.u8(6, base + R.TSER_OFF, 0x03)
            self.recs.u8(6, base + R.TPRIO_OFF, 0x03)
            self.recs.le16(6, base + R.TNOT_OFF, 1)
            self.recs.le16(6, base + R.TRENOT_OFF, 0)

    # -- the named fields -------------------------------------------------
    # Each is a view onto a byte in the records. The property names are the
    # ones the rest of the simulator and its tests already use.

    @property
    def ip(self):
        return self.recs.ip(0, R.IPADDR_OFF)

    @ip.setter
    def ip(self, v):
        self.recs.ip(0, R.IPADDR_OFF, v)

    @property
    def gateway(self):
        return self.recs.ip(0, R.GWADDR_OFF)

    @gateway.setter
    def gateway(self, v):
        self.recs.ip(0, R.GWADDR_OFF, v)

    @property
    def netmask_bits(self):
        return self.recs.u8(0, R.NETMASK_OFF)

    @netmask_bits.setter
    def netmask_bits(self, v):
        self.recs.u8(0, R.NETMASK_OFF, int(v))

    @property
    def telnet_password(self):
        return self.recs.text(0, R.PASSWD_OFF, 4)

    @telnet_password.setter
    def telnet_password(self, v):
        self.recs.text(0, R.PASSWD_OFF, 4, v)

    @property
    def keepalive(self):
        return self.recs.u8(0, R.TCPKEEP_OFF)

    @keepalive.setter
    def keepalive(self, v):
        self.recs.u8(0, R.TCPKEEP_OFF, int(v))

    @property
    def baudrate(self):
        return R.baud_of(self.recs.ch(0, R.SPEED_OFF))

    @baudrate.setter
    def baudrate(self, v):
        self.recs.ch(0, R.SPEED_OFF, value=R.baud_code(v))

    @property
    def if_mode(self):
        return self.recs.ch(0, R.LINEIF_OFF)

    @if_mode.setter
    def if_mode(self, v):
        self.recs.ch(0, R.LINEIF_OFF, value=int(v))

    @property
    def flow(self):
        return self.recs.ch(0, R.FC_OFF)

    @flow.setter
    def flow(self, v):
        self.recs.ch(0, R.FC_OFF, value=int(v))

    @property
    def port(self):
        return self.recs.ch(0, R.LPORT_OFF, "le16")

    @port.setter
    def port(self, v):
        self.recs.ch(0, R.LPORT_OFF, "le16", int(v))

    @property
    def connect_mode(self):
        return self.recs.ch(0, R.CMODE_OFF)

    @connect_mode.setter
    def connect_mode(self, v):
        self.recs.ch(0, R.CMODE_OFF, value=int(v))

    @property
    def remote_ip(self):
        return self.recs.ch(0, R.RHOST_OFF, "ip")

    @remote_ip.setter
    def remote_ip(self, v):
        self.recs.ch(0, R.RHOST_OFF, "ip", v)

    @property
    def remote_port(self):
        return self.recs.ch(0, R.RPORT_OFF, "le16")

    @remote_port.setter
    def remote_port(self, v):
        self.recs.ch(0, R.RPORT_OFF, "le16", int(v))

    @property
    def disconn_mode(self):
        return self.recs.ch(0, R.DMODE_OFF)

    @disconn_mode.setter
    def disconn_mode(self, v):
        self.recs.ch(0, R.DMODE_OFF, value=int(v))

    @property
    def flush_mode(self):
        return self.recs.ch(0, R.FLMODE_OFF)

    @flush_mode.setter
    def flush_mode(self, v):
        self.recs.ch(0, R.FLMODE_OFF, value=int(v))

    @property
    def disconn_time(self):
        return "%02d:%02d" % (self.recs.ch(0, R.IN_TO_MIN_OFF),
                              self.recs.ch(0, R.IN_TO_MIN_OFF + 1))

    @disconn_time.setter
    def disconn_time(self, v):
        mm, ss = _mmss(v)
        self.recs.ch(0, R.IN_TO_MIN_OFF, value=mm)
        self.recs.ch(0, R.IN_TO_MIN_OFF + 1, value=ss)

    @property
    def send_char_1(self):
        return self.recs.ch(0, R.SNDCHAR_OFF)

    @send_char_1.setter
    def send_char_1(self, v):
        self.recs.ch(0, R.SNDCHAR_OFF, value=int(v))

    @property
    def send_char_2(self):
        return self.recs.ch(0, R.SNDCHAR_OFF + 1)

    @send_char_2.setter
    def send_char_2(self, v):
        self.recs.ch(0, R.SNDCHAR_OFF + 1, value=int(v))

    @property
    def terminal_name(self):
        return self.recs.ch(0, R.PASS_TERM_OFF, "text", None, 16)

    @terminal_name.setter
    def terminal_name(self, v):
        self.recs.ch(0, R.PASS_TERM_OFF, "text", v, 16)

    # expert settings, all in record 3

    @property
    def arp_timeout(self):
        return self.recs.le16(3, R.ARPTO_OFF)

    @arp_timeout.setter
    def arp_timeout(self, v):
        self.recs.le16(3, R.ARPTO_OFF, int(v))

    @property
    def http_port(self):
        """0 in the record means the built-in default of 80."""
        return self.recs.le16(3, R.HTTPPORT_OFF) or 80

    @http_port.setter
    def http_port(self, v):
        self.recs.le16(3, R.HTTPPORT_OFF, 0 if int(v) == 80 else int(v))

    @property
    def smtp_port(self):
        return self.recs.le16(3, R.SMTPPORT_OFF) or 25

    @smtp_port.setter
    def smtp_port(self, v):
        self.recs.le16(3, R.SMTPPORT_OFF, 0 if int(v) == 25 else int(v))

    @property
    def mtu(self):
        return self.recs.le16(3, R.MTUSIZE_OFF) or 1400

    @mtu.setter
    def mtu(self, v):
        self.recs.le16(3, R.MTUSIZE_OFF, 0 if int(v) == 1400 else int(v))

    @property
    def cpu_performance(self):
        """0 regular, 1 low, 2 high -- the menu's own numbering."""
        return 2 if self.recs.bit(3, R.MISC_OFF, R.CPUPERF_BIT) else 0

    @cpu_performance.setter
    def cpu_performance(self, v):
        self.recs.bit(3, R.MISC_OFF, R.CPUPERF_BIT, int(v) == 2)

    @property
    def monitor_mode(self):
        return not self.recs.bit(3, R.MISC_OFF, R.DISMON_BIT)

    @monitor_mode.setter
    def monitor_mode(self, v):
        self.recs.bit(3, R.MISC_OFF, R.DISMON_BIT, not v)

    @property
    def auto_increment(self):
        return self.recs.bit(3, R.MISC_OFF, R.AINC_BIT)

    @auto_increment.setter
    def auto_increment(self, v):
        self.recs.bit(3, R.MISC_OFF, R.AINC_BIT, bool(v))

    @property
    def send_plus(self):
        return self.recs.bit(3, R.SENDIP_OFF, R.SENDPLUS_BIT)

    @send_plus.setter
    def send_plus(self, v):
        self.recs.bit(3, R.SENDIP_OFF, R.SENDPLUS_BIT, bool(v))

    @property
    def show_ip(self):
        return self.recs.bit(3, R.SENDIP_OFF, R.SHOWIP_BIT)

    @show_ip.setter
    def show_ip(self, v):
        self.recs.bit(3, R.SENDIP_OFF, R.SHOWIP_BIT, bool(v))

    @property
    def eth_mode(self):
        return self.recs.u8(3, R.ETHMODE_OFF) & 0x07

    @eth_mode.setter
    def eth_mode(self, v):
        cur = self.recs.u8(3, R.ETHMODE_OFF) & 0xF8
        self.recs.u8(3, R.ETHMODE_OFF, cur | (int(v) & 0x07))

    @property
    def hostlist_retry_count(self):
        return self.recs.u8(3, R.HLRCNT_OFF)

    @hostlist_retry_count.setter
    def hostlist_retry_count(self, v):
        self.recs.u8(3, R.HLRCNT_OFF, int(v))

    @property
    def hostlist_retry_timeout(self):
        return self.recs.u8(3, R.HLRTO_OFF)

    @hostlist_retry_timeout.setter
    def hostlist_retry_timeout(self, v):
        self.recs.u8(3, R.HLRTO_OFF, int(v))

    # e-mail, records 5 and 6

    @property
    def mail_server(self):
        return self.recs.ip(5, R.SVRIP_OFF)

    @mail_server.setter
    def mail_server(self, v):
        self.recs.ip(5, R.SVRIP_OFF, v)

    @property
    def unit_name(self):
        return self.recs.text(5, R.UNAME_OFF, 24)

    @unit_name.setter
    def unit_name(self, v):
        self.recs.text(5, R.UNAME_OFF, 24, v)

    @property
    def domain_name(self):
        return self.recs.text(6, R.DNAME_OFF, 24)

    @domain_name.setter
    def domain_name(self, v):
        self.recs.text(6, R.DNAME_OFF, 24, v)

    @property
    def recipient_1(self):
        return self.recs.text(5, R.R1EM_OFF, 49)

    @recipient_1.setter
    def recipient_1(self, v):
        self.recs.text(5, R.R1EM_OFF, 49, v)

    @property
    def recipient_2(self):
        return self.recs.text(5, R.R2EM_OFF, 49)

    @recipient_2.setter
    def recipient_2(self, v):
        self.recs.text(5, R.R2EM_OFF, 49, v)

    def trigger(self, n):
        """One e-mail trigger's fields, as a dict the menu and web share.

        The serial-trigger flag reads backwards: the low nibble of TSER holds
        3 when the trigger is DISABLED and 0 when it is enabled, which is why
        a card at defaults stores 3 there and still prints "disabled".
        """
        base = R.TRIG_OFF[n]
        return {
            "serial": (self.recs.u8(6, base + R.TSER_OFF) & 0x0F) != 0x03,
            "match": (self.recs.u8(6, base + R.TMASK_OFF),
                      self.recs.u8(6, base + R.TCMP_OFF)),
            "message": self.recs.text(6, base + R.TMESG_OFF, 24),
            "priority": self.recs.u8(6, base + R.TPRIO_OFF),
            "notify": self.recs.le16(6, base + R.TNOT_OFF),
            "renotify": self.recs.le16(6, base + R.TRENOT_OFF),
        }

    def set_trigger(self, n, **kw):
        base = R.TRIG_OFF[n]
        if "serial" in kw:
            cur = self.recs.u8(6, base + R.TSER_OFF) & 0xF0
            self.recs.u8(6, base + R.TSER_OFF,
                         cur | (0x00 if kw["serial"] else 0x03))
        if "message" in kw:
            self.recs.text(6, base + R.TMESG_OFF, 24, kw["message"])
        if "notify" in kw:
            self.recs.le16(6, base + R.TNOT_OFF, int(kw["notify"]))
        if "renotify" in kw:
            self.recs.le16(6, base + R.TRENOT_OFF, int(kw["renotify"]))

    # -- bulk field access ------------------------------------------------

    def apply(self, values):
        for k, v in values.items():
            setattr(self, k, v)

    def snapshot(self):
        """Every named field, for a working copy the menu can discard."""
        return {k: getattr(self, k) for k in self.DEFAULTS}

    # -- persistence ------------------------------------------------------

    def load(self):
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        macs = data.get("mac")
        if isinstance(macs, str) and len(macs) == 12:
            try:
                self.mac = bytes.fromhex(macs)
            except ValueError:
                pass
        # Records are the real state. A file written by an older build has
        # named fields instead, so both are read and the records win.
        recs = data.get("records")
        if isinstance(recs, dict):
            for num, b64 in recs.items():
                try:
                    self.recs.put(int(num), base64.b64decode(b64))
                except (ValueError, TypeError):
                    pass
        else:
            for k in self.DEFAULTS:
                if k in data:
                    try:
                        setattr(self, k, data[k])
                    except (ValueError, TypeError):
                        pass
        sec = data.get("security")
        if isinstance(sec, dict):
            self.security.update({k: bool(v) for k, v in sec.items()
                                  if k in self.security})
        if isinstance(data.get("snmp_community"), str):
            self.snmp_community = data["snmp_community"]

    def save(self):
        if not self.path:
            return
        # On a public address this is reachable from an unauthenticated HTTP
        # POST (`xportweb._setup_cgi`) and from the port-1 assignment knock,
        # so a stranger could move the card and write this file. Frozen, the
        # card still accepts the programming in memory and still answers from
        # it -- what a real card does -- but it forgets it on restart.
        # See `exposed.writes_frozen`.
        if exposed.refused("xport card configuration"):
            return
        data = {
            "mac": mac_hex(self.mac),
            "records": {str(n): base64.b64encode(v).decode("ascii")
                        for n, v in self.recs.as_dict().items()},
            "security": dict(self.security),
            "snmp_community": self.snmp_community,
        }
        # The named fields go in too, so the file stays readable and an
        # older build can still make sense of it.
        for k in self.DEFAULTS:
            data[k] = getattr(self, k)
        try:
            with self._lock, atomicfile.replacing(self.path) as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # The address a Reset puts the card back on. A real card's option 7
    # deliberately keeps the address a site programmed, which is right in
    # the field and unhelpful on a bench: a trainee who has lost track of
    # what they set needs one known place to find the card again. So the
    # simulator has a second, larger reset that clears the address too, and
    # this is where it lands.
    RESET_IP = "192.168.0.10"
    RESET_GATEWAY = "192.168.0.1"
    RESET_NETMASK_BITS = 8

    def reset_card(self, ip=None, gateway=None, netmask_bits=None):
        """Put the whole card back to a known state, address included.

        This is not what the card's own "7 Defaults" does -- that keeps the
        address, and `factory_defaults` reproduces it faithfully. This is the
        bench's own bigger hammer: everything goes back, including the IP, so
        there is always one address to get back to.
        """
        self.recs = R.SetupRecords()
        self._seed_factory_records()
        self.apply(self.DEFAULTS)
        self.ip = ip or self.RESET_IP
        self.gateway = gateway or self.RESET_GATEWAY
        self.netmask_bits = (self.RESET_NETMASK_BITS
                             if netmask_bits is None else netmask_bits)
        self.security = {k: (k not in ("echo", "enhanced_password"))
                         for k in self.security}
        self.snmp_community = "public"
        self.alternate_mac = False
        self.rs485_high = False
        self.save()

    def blank_card(self):
        """A module straight out of its box: no address at all.

        Veeder-Root's installation guide is explicit that the address is
        customer-supplied and has to be assigned -- with DeviceInstaller, or
        with `arp -s` and a telnet to port 1 and then 9999. There is no
        factory address to fall back on. A card that arrives with one is a
        card somebody has already programmed, which is why the address
        survives the menu's own "7 Defaults".

        So this is the state a commissioning exercise starts from: discovery
        answers, and nothing else does, until someone gives it an address.
        """
        self.reset_card(ip="0.0.0.0", gateway="0.0.0.0", netmask_bits=0)

    def program(self, ip=None, gateway=None, netmask_bits=None, port=None):
        """Program the card the way a site's would be, in one go."""
        if ip:
            self.ip = ip
        if gateway:
            self.gateway = gateway
        if netmask_bits is not None:
            self.netmask_bits = netmask_bits
        if port:
            self.port = port
        self.save()

    def factory_defaults(self):
        """Option 7: reset channel/e-mail/expert, KEEP the network address.

        This is the behaviour that lets a card be reset in the field without
        losing the address the site put on it: IP, gateway and netmask
        survive, everything else goes back to the card's resting values.
        """
        keep = (self.ip, self.gateway, self.netmask_bits)
        self.recs = R.SetupRecords()
        self._seed_factory_records()
        self.apply(self.FACTORY)
        self.ip, self.gateway, self.netmask_bits = keep
        self.security = {k: (k not in ("echo", "enhanced_password"))
                         for k in self.security}
        self.snmp_community = "public"
        self.save()

    # -- derived ----------------------------------------------------------

    @property
    def netmask(self):
        return R.netmask_of(self.netmask_bits)

    def assigned(self):
        return self.ip not in ("0.0.0.0", "", None)

    def autoip(self):
        """The link-local address an unconfigured card self-assigns.

        An XPort ships with no static IP, tries DHCP, and falls back to
        AutoIP when there is no DHCP server -- a link-local address in
        169.254.1.0 .. 169.254.254.255 (RFC 3927 keeps the first and last
        /24 out of the pool). Real AutoIP seeds the pick from the MAC, so the
        same card lands on the same address every time.
        """
        third = 1 + (self.mac[4] % 254)
        fourth = self.mac[5]
        return "169.254.%d.%d" % (third, fourth)

    def effective_ip(self):
        return self.ip if self.assigned() else self.autoip()

    def hostlist(self):
        """The twelve host-list entries, as (ip, port) pairs."""
        out = []
        for i in range(R.NUM_HLIST):
            off = R.HLDATA_OFF + i * R.HLIST_ENTRY
            out.append((self.recs.ip(3, off), self.recs.le16(3, off + 4)))
        return out

    def set_hostlist(self, index, ip, port):
        off = R.HLDATA_OFF + index * R.HLIST_ENTRY
        self.recs.ip(3, off, ip)
        self.recs.le16(3, off + 4, port)

    def uses_datagram(self):
        """UDP, which the low nibble of the connect mode selects.

        This is tested BEFORE the host-list bit, because a UDP connect mode
        can have that bit set too: 2C is datagram and 27 is the host list,
        both confirmed on the card. Keying on the bit alone offered the
        host-list editor for a UDP mode, which the card never does.
        """
        return (self.connect_mode & 0x0F) == R.UDP_MODE

    def uses_hostlist(self):
        return (not self.uses_datagram()
                and bool(self.connect_mode & R.CMODE_HOSTLIST))

    @property
    def datagram_type(self):
        """In UDP mode the disconnect-mode byte carries the datagram type."""
        return self.recs.ch(0, R.DMODE_OFF)

    @datagram_type.setter
    def datagram_type(self, v):
        self.recs.ch(0, R.DMODE_OFF, value=int(v))

    @property
    def pack_control(self):
        return self.recs.ch(0, R.PCKCTRL_OFF)

    @pack_control.setter
    def pack_control(self, v):
        self.recs.ch(0, R.PCKCTRL_OFF, value=int(v))

    def telnet_mode(self):
        return bool(self.disconn_mode & R.DMODE_TELNET)

    # -- the 120-byte setup record (F8/F9 payload) ------------------------

    def setup_record(self):
        """Record 0, which is what DeviceInstaller reads over 77FEh."""
        return self.recs.get(0)


# ---------------------------------------------------------------------------
# helpers


def _mmss(text):
    try:
        mm, ss = str(text).split(":")
        return int(mm) & 0xFF, int(ss) & 0xFF
    except (ValueError, AttributeError):
        return 0, 0


def _strip_iac(data):
    """Drop telnet IAC sequences from a client's input.

    A telnet client opens by negotiating options; the card ignores them and
    reads the rest as typed characters. Three-byte WILL/WONT/DO/DONT runs and
    the two-byte commands both go.
    """
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 255:                      # IAC
            if i + 1 < len(data) and data[i + 1] in (251, 252, 253, 254):
                i += 3
                continue
            if i + 1 < len(data) and data[i + 1] == 255:
                out.append(255)
                i += 2
                continue
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def _split_line(buf):
    """Pull one complete line off a buffer, or return (None, buf).

    The card ends a line on CR; a telnet client sends CR LF or CR NUL, so
    whichever follows the CR is swallowed.
    """
    for i, b in enumerate(buf):
        if b in (13, 10):
            line = bytes(buf[:i])
            rest = buf[i + 1:]
            if rest[:1] in (b"\n", b"\x00") and b == 13:
                rest = rest[1:]
            return line, rest
    return None, buf


# ---------------------------------------------------------------------------
# UDP 30718 discovery -- the 77FEh protocol


class Discovery:
    """The 77FEh responder DeviceInstaller talks to.

    Every message is four bytes, `00 00 00 xx`, and every answer echoes the
    opcode plus one. The four the captured card answers are all here, with
    the reply bodies taken from that card:

      F4 -> F5   firmware query: the version string DeviceInstaller lists
      F6 -> F7   discovery: the unit's type and MAC
      F8 -> F9   the 120-byte setup record, which is the Device Details view
      FA -> FB   a bare acknowledgement

    A real card answers only four-byte requests. Anything longer is not a
    probe it understands, and this responder ignores it -- deliberately: the
    captured card, sent an over-long frame, replied with the contents of a
    network buffer and corrupted its own working configuration. Emulating
    that fault would be faithful and useless, so the emulator declines the
    frame instead. `reference/lantronix_xport_capture.md` records it.
    """

    def __init__(self, config, log=None, powered=None, trace=False,
                 policy=None, gate=None, capture=None):
        self.config = config
        self.log = log
        # The exposure controls, all optional; handed none, this responds
        # exactly as it always has. See `exposed.py`.
        self.policy = policy
        self.gate = gate
        self.capture = capture
        # With trace on, every frame is logged, answered or not. Off by
        # default because a discovery sweep is chatty; on when working out
        # what a tool is asking for, which is the only way to see a frame
        # that IS recognised but whose answer the tool then rejects.
        self.trace = trace
        self.sock = None
        self._stop = False
        # Frames that arrived and were not recognised, newest last. Kept so
        # a tool's unknown request can be read off afterwards.
        self.unknown = []
        self.directed = None
        # A broadcast reaches BOTH the wildcard socket and the
        # address-specific one, and answering twice is not what a card does.
        # Remember what was just answered so only the first reply goes out.
        self._answered = {}
        self._answered_lock = threading.Lock()
        # The card draws its power from the console. With the breaker open
        # there is no card on the network to answer anything.
        self.powered = powered or (lambda: True)

    def _already_answered(self, data, addr):
        """True when this exact request was just answered by the other socket.

        A broadcast is delivered to every socket bound to the port, so the
        wildcard and the address-specific binding both see it. A real card
        answers once.
        """
        import time as _t
        now = _t.monotonic()
        key = (addr, bytes(data))
        with self._answered_lock:
            for k, when in list(self._answered.items()):
                if now - when > 1.0:
                    del self._answered[k]
            if key in self._answered and now - self._answered[key] < 0.25:
                return True
            self._answered[key] = now
        return False

    def serve_directed(self, ip):
        """A second socket, bound to the card's own address on 30718.

        The wildcard socket is what hears the discovery broadcast, but it
        loses a race that only happens when the card and the tool share a
        machine. A tool reads a device's configuration from ITS OWN port
        30718 to the device's port 30718, and it holds that port bound; two
        sockets on one host then match the datagram, and the operating
        system gives it to the more specific binding. Bound only to the
        wildcard, the emulator loses every one of those requests -- they
        appear on the wire and are answered by nobody, which is exactly what
        a capture showed.

        Binding the card's own address as well should win those, because an
        address-specific binding beats a wildcard one.

        It does not help, and this is off by default because of what
        happened when it was on. Holding the card's address on 30718 stops
        the tool binding the port it sends configuration reads FROM, so
        instead of losing those reads to the wrong socket, the tool stops
        sending them at all. Measured both ways: without this, a capture
        shows four requests sent and answered by nobody; with it, none are
        sent.

        Which means the configuration read cannot work with the tool and the
        emulated card on one machine, whichever way the sockets are
        arranged. On a real bench the card is a separate box and the
        question does not arise. Kept, opt-in, because on a host where
        nothing else wants port 30718 it is the more accurate binding.
        """
        if not ip or ip in ("0.0.0.0", ""):
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((ip, DISCOVERY_PORT))
        except OSError as e:
            if self.log:
                self.log("-- XPort 77FE not also bound to %s: %s" % (ip, e))
            sock.close()
            return
        self.directed = sock
        if self.log:
            self.log("-- XPort 77FE also bound to %s:%d for directed reads"
                     % (ip, DISCOVERY_PORT))
        while not self._stop:
            try:
                data, addr = sock.recvfrom(1024)
            except ConnectionResetError:
                continue            # the same as `serve`'s, FIDELITY Z3
            except OSError:
                return
            if self._already_answered(data, addr):
                continue
            reply = self.answer(data) if self.powered() else None
            if self.trace and self.log:
                self.log("-- 77FE(direct) %s from %s:%d -> %s"
                         % (" ".join("%02X" % b for b in data[:8]),
                            addr[0], addr[1],
                            "%d bytes" % len(reply) if reply else "no answer"))
            if reply:
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass
            elif self.powered():
                self._unknown(data, addr)

    def serve(self, host="0.0.0.0"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            self.sock.bind((host, DISCOVERY_PORT))
        except OSError as e:
            if self.log:
                self.log("-- XPort discovery not listening: %s" % e)
            return
        if self.log:
            self.log("-- XPort discovery on udp/%d" % DISCOVERY_PORT)
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(1024)
            except ConnectionResetError:
                # Windows hands back the ICMP port-unreachable from answering
                # a tool that had already closed its socket as an error on
                # the NEXT receive. Nothing is wrong with this socket, and
                # returning here ended discovery for good: DeviceInstaller
                # lost the card until the simulator restarted. FIDELITY Z3.
                continue
            except OSError:
                return
            if self._already_answered(data, addr):
                continue
            reply = self.answer(data) if self.powered() else None
            if self.trace and self.log:
                # The source port matters when a tool and an emulated card
                # share a machine: both want udp/30718, and the one with the
                # more specific binding gets the packet.
                self.log("-- 77FE %s from %s:%d -> %s"
                         % (" ".join("%02X" % b for b in data[:8]),
                            addr[0], addr[1],
                            ("%d bytes, %s" % (len(reply),
                             " ".join("%02X" % b for b in reply[:4])))
                            if reply else "no answer"))
            if reply:
                self._reply(reply, addr)
            elif self.powered():
                self._unknown(data, addr)

    def _unknown(self, data, addr):
        """Record a 77FEh frame this responder does not know.

        A real card answers a handful of opcodes and ignores the rest. The
        emulator ignores them too, but says so, because an unrecognised
        frame is usually a tool trying to do something the emulation has not
        learned yet -- assigning an address, or rebooting the unit -- and
        the bytes are the whole specification of how it does it.
        """
        self.unknown.append((addr[0], bytes(data)))
        del self.unknown[:-64]
        if self.log:
            self.log("-- 77FE frame from %s not understood, ignored: %s"
                     % (addr[0], " ".join("%02X" % b for b in data[:32])))

    def _reply(self, reply, addr):
        """Answer through the listening socket, so the reply comes FROM 30718.

        This once used a second socket bound to the card's own address, so
        that a tool would list the card at the address programmed into it
        rather than at whichever interface routed the reply. That was a
        mistake, and an instructive one: a socket bound to `(ip, 0)` gets an
        ephemeral source port, and the card's replies then came from
        something like 62463 instead of 30718.

        DeviceInstaller tolerates that for the discovery broadcast -- it
        found and listed the emulated card quite happily -- but not for the
        configuration read that follows, which failed with "the
        configuration was not received from the device" while every frame it
        sent was being answered. Answering from the wrong port is worse than
        answering from the wrong address.

        The listening socket is bound to 30718 on every address, so the
        source port is right and the operating system picks the source
        address by the route to the asker. For a card whose address this
        machine actually has, that is the card's address anyway.
        """
        # Every UDP reply leaves here, so this is where the datagram rate
        # limit goes. See `exposed.Gate.datagram_ok`: a four-byte probe
        # draws up to 124 bytes with no handshake, so an unlimited
        # responder on a public address is a 31x DDoS reflector aimed at
        # whoever the attacker forges as the source address.
        if self.gate is not None and not self.gate.datagram_ok(addr[0]):
            return
        if self.capture is not None:
            self.capture.record("out", reply, peer=addr[0],
                                proto="77fe/udp")
        try:
            self.sock.sendto(reply, addr)
        except OSError:
            pass

    def answer(self, data):
        """The response to one probe, or None if it is not one we know.

        Byte 1 is a flag DeviceInstaller sets on some queries -- it sends
        `00 01 00 F6` as well as `00 00 00 F6` -- and the card answers both
        the same way, so it is accepted and ignored here too.
        """
        # "Disable Port 77FEh", from the card's own Security menu. Every
        # listener used to ignore it; see `_answering`.
        if not self._answering():
            return None
        if len(data) != 4 or data[0] != 0x00 or data[2] != 0x00:
            return None
        op = data[3]
        if op == 0xF4:
            return self._version()
        if op == 0xF6:
            return self._info()
        if op == 0xF8:
            return self._setup()
        if op == 0xFA:
            return b"\x00\x00\x00\xFB"
        if 0xE0 <= op <= 0xE7:
            return self._record(op)
        return None

    def _record(self, op):
        """E<n> -> D<n>: setup record n, 126 bytes.

        This is how a tool reads a card's configuration, and it is a
        separate conversation from discovery: `F8` fetches record 0 and then
        one `E<n>` per record fetches the rest. The reply opcode is the
        request less 0x10, which is why it looks unlike the F6/F7 and F8/F9
        pairs where the reply is the request plus one.

        A record the card does not have answers with 126 bytes of FF --
        erased flash. There is no record 1 or 2, so `E2` answers FF on
        healthy hardware, which is what made it look for a while as though
        the opcode meant something else.

        Read off the wire between DeviceInstaller and a real card; the
        payloads match this emulator's own records byte for byte.
        """
        num = op & 0x0F
        reply = bytes([0x00, 0x00, 0x00, (op - 0x10) & 0xFF])
        if num == 0:
            # E0 fetches record 0, and record 0 is the short one: 120 bytes,
            # the same payload F8 returns.
            return reply + self.config.recs.get(0)
        if num == 1:
            # Record 1 answers as 126 zero bytes on the captured card.
            return reply + b"\x00" * 126
        if num not in R.REC_SIZES:
            # No record 2 exists, and an absent record reads as erased flash.
            return reply + b"\xFF" * 126
        return reply + self.config.recs.get(num)[:126].ljust(126, b"\x00")

    def _info(self):
        """F6 -> F7, 30 bytes, MAC in the last six.

        The fixed bytes are the captured card's. They carry the unit type
        DeviceInstaller matches against its product database, which is what
        makes the emulator list as an XPort rather than an unknown device.
        """
        r = bytearray(bytes.fromhex(
            "000000F7" "00205006" "58354B0F" "FF130000" "62A7E896" "FF000000"
        ))
        r[24:30] = self.config.mac
        return bytes(r)

    def _version(self):
        """F4 -> F5, 33 bytes.

        The version string sits at 16 and the card's tunnel port, little
        endian, at 24 -- a card serving 10001 puts `11 27` there.
        """
        r = bytearray(33)
        r[0:4] = b"\x00\x00\x00\xF5"
        r[4:9] = bytes.fromhex("0904" "1EEF" "FF")
        # Bytes 12-15 and 24-31 read FF on a configured card and 00 on one
        # just returned to defaults -- the two states the bench card was
        # captured in. The FF form is the one a working card presents, and
        # the earlier reading of 24-25 as the tunnel port was a coincidence
        # of the defaulted card happening to hold 10001 there.
        r[12:16] = b"\xFF" * 4
        ver = FIRMWARE_VERSION.lstrip("V").encode("ascii")
        r[16:16 + len(ver)] = ver
        r[24:32] = b"\xFF" * 8
        r[32] = 0x06
        return bytes(r)

    def _setup(self):
        """F8 -> F9, 124 bytes: 4-byte header + the 120-byte record."""
        return b"\x00\x00\x00\xF9" + self._record0()

    def _record0(self):
        """Record 0, with the setup password masked off a public card.

        Bytes 8-11 of record 0 are the four-character telnet and web setup
        password (`xportrec.PASSWD_OFF`), and this responder answers any
        stranger's four-byte probe with no authentication at all. So the
        credential that locks the setup menu and the web manager was handed
        to anyone who asked for it, on UDP and TCP alike -- and it is the
        same secret the web manager takes as its HTTP Basic password.

        The emulation stays byte-exact on a bench, which is where the
        capture is diffed against the real card. Off the bench the four
        credential bytes are zeroed and nothing else is touched; a card
        with no password set answers identically either way, because those
        bytes are already zero.
        """
        rec = bytearray(self.config.setup_record())
        if self.policy is not None and self.policy.level != BENCH_LEVEL:
            rec[R.PASSWD_OFF:R.PASSWD_OFF + 4] = b"\x00" * 4
        return bytes(rec)

    def _answering(self):
        """Whether 77FEh answers at all.

        "Disable Port 77FEh" is in the card's own Security menu, and this
        emulator printed it, stored it and let the menu toggle it without a
        single listener ever reading it -- so the card's own documented
        mitigation for the disclosure above did nothing whatsoever. It is
        honoured here.
        """
        return bool(self.config.security.get("port_77fe", True))

    def close(self):
        self._stop = True
        for sock in (self.sock, self.directed):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    # -- the same protocol over TCP ---------------------------------------

    def serve_tcp(self, host="0.0.0.0", on_socket=None):
        """The 77FEh protocol again, on TCP 30718.

        The card answers the same opcodes on a TCP connection to the same
        port number, several frames to a connection, and that -- not the UDP
        broadcast -- is where DeviceInstaller reads a device's configuration
        when you select it. A card that answers only the UDP side is found
        and listed and then fails with "the configuration was not received
        from the device", which is exactly what happened here.
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, DISCOVERY_PORT))
            srv.listen(4)
        except OSError as e:
            if self.log:
                self.log("-- XPort 77FE/tcp not listening: %s" % e)
            return
        if on_socket:
            on_socket(srv)
        if self.log:
            self.log("-- XPort 77FE also on tcp/%d" % DISCOVERY_PORT)
        while not self._stop:
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            if not self.powered():
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            threading.Thread(target=self._tcp_session, args=(conn, addr),
                             daemon=True).start()

    def _tcp_session(self, conn, addr):
        ip = addr[0] if addr else None
        with exposed.session(self.gate, ip) as admitted:
            if not admitted:
                try:
                    conn.close()
                except OSError:
                    pass
                return
            self._tcp_frames(conn, addr, ip)

    def _tcp_frames(self, conn, addr, ip):
        cap = self.capture
        try:
            conn.settimeout(self.policy.idle_timeout
                            if self.policy is not None
                            and self.policy.idle_timeout else 20.0)
            buf = bytearray()
            while True:
                try:
                    data = conn.recv(256)
                except (OSError, socket.timeout):
                    return
                if not data:
                    return
                buf.extend(data)
                if cap is not None:
                    cap.record("in", data, peer=ip, proto="77fe/tcp")
                # Requests are four bytes each; a client may pipeline them.
                while len(buf) >= 4:
                    frame = bytes(buf[:4])
                    del buf[:4]
                    reply = self.answer(frame)
                    if self.trace and self.log:
                        self.log("-- 77FE/tcp %s from %s -> %s"
                                 % (" ".join("%02X" % b for b in frame),
                                    addr[0],
                                    "%d bytes" % len(reply) if reply
                                    else "no answer"))
                    if reply is None:
                        self._unknown(frame, addr)
                        if cap is not None:
                            cap.record("drop", frame, peer=ip,
                                       proto="77fe/tcp",
                                       note="opcode not recognised")
                        continue
                    if self.policy is not None:
                        self.policy.sleep()
                    try:
                        conn.sendall(reply)
                    except OSError:
                        return
                    if cap is not None:
                        cap.record("out", reply, peer=ip, proto="77fe/tcp")
        finally:
            try:
                conn.close()
            except OSError:
                pass


# The setup menu lives in its own module; re-exported so callers and tests
# that have always imported it from here keep working. The import stays at the
# foot of the file because xportmenu imports this one. `serve_setup` is used
# below; `SetupSession` only ever comes back out as `xport.SetupSession`, and
# is named again so that reading it as dead is harder than reading the comment.
from .xportmenu import SetupSession, serve_setup      # noqa: E402

_RE_EXPORTED = (SetupSession,)


def serve(config, host="0.0.0.0", log=None, powered=None, web=True):
    """Start every network face of the card: discovery, setup menu, web."""
    disc = Discovery(config, log=log, powered=powered)
    threading.Thread(target=disc.serve, args=(host,), daemon=True).start()
    threading.Thread(target=serve_setup, args=(config, host, log, powered),
                     daemon=True).start()
    if web:
        from .xportweb import serve_web
        threading.Thread(target=serve_web,
                         args=(config, host, log, powered),
                         daemon=True).start()
    return disc
