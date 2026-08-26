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
"""The Lantronix XPort inside the TLS-350's TCP/IP Interface Module.

A real TLS-350 does not speak Ethernet. Veeder-Root's TCP/IP Interface
Module (VR P/N 331870-001) is a Lantronix XPort -- a serial-to-Ethernet
device server -- wired to the console's RS-232 port. Everything a network
client does to a networked TLS-350 is really done to this XPort:

  * TCP 10001 is the SERIAL TUNNEL. Bytes sent here go down the RS-232 line
    to the console and its answers come back. That tunnel is `wire.serve`;
    this module does not touch it, it only describes it.
  * TCP 9999 is the SETUP MENU. Telnet to it, press Enter within the window,
    and the XPort prints its configuration and a "Change Setup:" menu. This
    is where the IP address, the baud rate and the tunnel port are set.
  * UDP 30718 (0x77FE) is DISCOVERY. Lantronix DeviceInstaller broadcasts a
    four-byte probe and every XPort on the subnet answers with its type and
    MAC, which is how the tool finds a card with no known address.

This module emulates the XPort itself: its configuration, the setup menu on
9999, and the discovery responder on 30718. Fidelity is drawn from the
Lantronix XPort User Guide (900-270 Rev E) and the DeviceInstaller/77FEh
protocol, both written up under reference/. Where a real card's exact bytes
could only be had from a live capture -- the banner version string, the
discovery device-type code -- the format is faithful and the value is a
documented, clearly-marked representative one; those spots are called out
in reference/lantronix_setup_menu.md and cannot be pinned down further
without a card on the bench.

Golden values are the TLS-350's own required settings: I/F 4C (RS-232C
8N1), Flow 02, tunnel port 10001, Connect Mode C4, DisConnMode 80,
DisConnTime 01:30.
"""
import json
import socket
import struct
import threading
import time

# The port a real XPort reserves for its setup menu, and the one
# DeviceInstaller broadcasts discovery to. Neither is configurable on the
# hardware; both are fixed here too.
SETUP_PORT = 9999
DISCOVERY_PORT = 30718            # 0x77FE

# Lantronix's own OUI. A real card's MAC begins with these three bytes, so
# a discovered emulator that DeviceInstaller is to believe must too.
LANTRONIX_OUI = (0x00, 0x20, 0x4A)

# The product-type tag the XPort prints in its banner, and a representative
# firmware string. reference/lantronix_setup_menu.md 3.1: the format is
# firm, the exact digits are per-card. Kept constant here.
PRODUCT_TAG = "XPTEX"
FIRMWARE_VERSION = "V6.5.0.7"
FIRMWARE_DATE = "040719"

# The discovery device-type code (F7 response bytes 8-9). The classic
# XPort's exact code is not published; the field is a printable two-char
# product code on every card whose code IS known, so a printable placeholder
# plus a correct MAC is what lets DeviceInstaller list the unit.
# reference/lantronix_deviceinstaller_protocol.md 2.3.
DEVICE_TYPE = b"XP"

CRLF = b"\r\n"


def default_mac():
    """A stable MAC in Lantronix's range.

    A real card's is burned in; this one is derived from the host name so
    that two simulators on a bench do not collide, and stays put across
    runs on one machine.
    """
    import hashlib
    import socket as _s
    seed = hashlib.sha256(_s.gethostname().encode()).digest()
    return bytes(LANTRONIX_OUI) + seed[:3]


def mac_hex(mac, sep=""):
    return sep.join(f"{b:02X}" for b in mac)


class XPortConfig:
    """The XPort's non-volatile configuration.

    Persisted beside the console's own state so a card keeps its address
    between runs, the way a real one keeps it in Flash. The field names and
    their two-hex-digit encodings are the XPort's, so the setup menu can
    show and change them verbatim.
    """

    # the TLS-350's required settings, which are also this emulator's
    # power-on defaults
    DEFAULTS = {
        "ip": "0.0.0.0",                # 0.0.0.0 => "not set", DeviceInstaller
        "gateway": "0.0.0.0",           #            shows it red until assigned
        "netmask_bits": 8,              # host bits; 8 => 255.255.255.0
        "telnet_password": "",          # empty => disabled
        "baudrate": 9600,
        "if_mode": 0x4C,                # RS-232C, 8 bit, no parity, 1 stop
        "flow": 0x02,                   # RTS/CTS (console SW V21+)
        "port": 10001,                  # the serial tunnel
        "connect_mode": 0xC4,           # always accept + manual connection
        "remote_ip": "0.0.0.0",         # alarm dial-out target
        "remote_port": 0,
        "disconn_mode": 0x80,           # disconnect on DTR drop
        "flush_mode": 0x00,
        "disconn_time": "01:30",        # 90s serial-idle timeout
        "send_char_1": 0x00,
        "send_char_2": 0x00,
    }

    def __init__(self, path=None, mac=None):
        self.path = path
        self.mac = mac or default_mac()
        self._lock = threading.Lock()
        for k, v in self.DEFAULTS.items():
            setattr(self, k, v)
        self.load()

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
        for k in self.DEFAULTS:
            if k in data:
                setattr(self, k, data[k])

    def save(self):
        if not self.path:
            return
        data = {"mac": mac_hex(self.mac)}
        for k in self.DEFAULTS:
            data[k] = getattr(self, k)
        try:
            with self._lock, open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def factory_defaults(self):
        """Option 7: reset channel/expert, KEEP the network address.

        reference/lantronix_setup_menu.md 8.2: IP, gateway and netmask
        survive a factory reset; everything else returns to default. It is
        what lets a card be reset without losing the address a site put on
        it.
        """
        keep = {k: getattr(self, k)
                for k in ("ip", "gateway", "netmask_bits")}
        for k, v in self.DEFAULTS.items():
            setattr(self, k, v)
        for k, v in keep.items():
            setattr(self, k, v)
        self.save()

    # -- derived ----------------------------------------------------------

    @property
    def netmask(self):
        """The dotted mask the host-bit count stands for."""
        return {0: "0.0.0.0", 8: "255.255.255.0",
                16: "255.255.0.0", 24: "255.0.0.0"}.get(self.netmask_bits,
                                                         "255.255.255.0")

    def assigned(self):
        return self.ip not in ("0.0.0.0", "", None)

    def autoip(self):
        """The link-local 169.254.x.x address an unconfigured card self-
        assigns, derived from the MAC.

        reference/lantronix_deviceinstaller_protocol.md: an XPort ships with
        no static IP, tries DHCP, and falls back to AutoIP when there is no
        DHCP server -- a link-local address in 169.254.1.0 .. 169.254.254.255
        (RFC 3927 keeps the first and last /24 out of the pool). Real AutoIP
        seeds the pick from the MAC, so the same card lands on the same
        address every time; this derives the last two octets from the MAC's
        last two bytes, held inside the allowed range.
        """
        third = 1 + (self.mac[4] % 254)          # 1 .. 254
        fourth = self.mac[5]                     # 0 .. 255
        return f"169.254.{third}.{fourth}"

    def effective_ip(self):
        """The address the card is actually reachable at: the static IP once
        assigned, otherwise the AutoIP link-local address.

        A real unconfigured XPort is not unreachable -- DeviceInstaller finds
        it by MAC and it answers at its link-local address until someone sets
        a static IP. This is that address.
        """
        return self.ip if self.assigned() else self.autoip()

    # -- the 120-byte setup record (F8/F9 payload) ------------------------

    def setup_record(self):
        """The 120-byte configuration record a real card returns.

        Layout from reference/lantronix_deviceinstaller_protocol.md 3: the
        top-level Table-21 fields, then Channel 1's Table-22 block at 0x10.
        Ports are little-endian, as the manual specifies. Unused bytes are
        zero, which is what the hardware leaves them.
        """
        rec = bytearray(120)
        rec[0:4] = _ip_bytes(self.ip)
        rec[6] = self.netmask_bits & 0xFF
        pw = (self.telnet_password or "").encode("ascii", "ignore")[:4]
        rec[8:8 + len(pw)] = pw
        rec[12:16] = _ip_bytes(self.gateway)
        # Channel 1, base 0x10
        rec[0x10] = self.if_mode & 0xFF
        rec[0x11] = _baud_code(self.baudrate)
        rec[0x12] = self.flow & 0xFF
        struct.pack_into("<H", rec, 0x14, self.port & 0xFFFF)
        struct.pack_into("<H", rec, 0x16, self.remote_port & 0xFFFF)
        rec[0x18:0x1C] = _ip_bytes(self.remote_ip)
        rec[0x1C] = self.connect_mode & 0xFF
        rec[0x1D] = self.disconn_mode & 0xFF
        mm, ss = _mmss(self.disconn_time)
        rec[0x1E] = mm
        rec[0x1F] = ss
        rec[0x20] = self.send_char_1 & 0xFF
        rec[0x21] = self.send_char_2 & 0xFF
        rec[0x22] = self.flush_mode & 0xFF
        return bytes(rec)


# ---------------------------------------------------------------------------
# helpers


def _ip_bytes(text):
    try:
        parts = [int(x) & 0xFF for x in str(text).split(".")]
    except ValueError:
        parts = [0, 0, 0, 0]
    parts = (parts + [0, 0, 0, 0])[:4]
    return bytes(parts)


def _mmss(text):
    try:
        mm, ss = str(text).split(":")
        return int(mm) & 0xFF, int(ss) & 0xFF
    except (ValueError, AttributeError):
        return 0, 0


# The XPort's baud-rate codes are an internal table; only the common speeds
# matter here, and an unknown speed falls back to 9600's code.
_BAUD_CODES = {300: 0x02, 600: 0x03, 1200: 0x04, 2400: 0x05, 4800: 0x06,
               9600: 0x07, 19200: 0x08, 38400: 0x09, 57600: 0x0A,
               115200: 0x0B}


def _baud_code(baud):
    return _BAUD_CODES.get(int(baud), 0x07)


# ---------------------------------------------------------------------------
# UDP 30718 discovery


class Discovery:
    """The 77FEh discovery responder DeviceInstaller talks to.

    Answers the three read probes a real XPort answers -- F6 (info), F4
    (version), F8 (setup record) -- so the tool lists and identifies the
    emulated card. Write and reset opcodes are deliberately not implemented:
    the research marks them uncertain, and configuration is done through the
    setup menu instead, which is how the field does it anyway.
    """

    def __init__(self, config, log=None, powered=None):
        self.config = config
        self.log = log
        self.sock = None
        self._stop = False
        # The card draws its power from the console. With the breaker open
        # there is no card on the network to answer anything.
        self.powered = powered or (lambda: True)

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
                self.log(f"-- XPort discovery not listening: {e}")
            return
        if self.log:
            self.log(f"-- XPort discovery on udp/{DISCOVERY_PORT}")
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                return
            reply = self.answer(data) if self.powered() else None
            if reply:
                try:
                    self.sock.sendto(reply, addr)
                except OSError:
                    pass

    def answer(self, data):
        """The response to one probe, or None if it is not one we know.

        Every 77FEh message is four bytes, `00 00 00 xx`. We recognise the
        three read opcodes and ignore anything else, which is what a card
        does with a probe meant for a different product.
        """
        if len(data) < 4 or data[0:3] != b"\x00\x00\x00":
            return None
        op = data[3]
        if op == 0xF6:
            return self._info()
        if op == 0xF4:
            return self._version()
        if op == 0xF8:
            return self._setup()
        return None

    def _info(self):
        """F6 -> F7, 30 bytes: type at 8-9, MAC at 24-29."""
        r = bytearray(30)
        r[0:4] = b"\x00\x00\x00\xF7"
        r[8:10] = DEVICE_TYPE
        r[24:30] = self.config.mac
        return bytes(r)

    def _version(self):
        """F4 -> F5, 32 bytes: NUL-terminated version at offset 16."""
        r = bytearray(32)
        r[0:4] = b"\x00\x00\x00\xF5"
        ver = f"{FIRMWARE_VERSION} {PRODUCT_TAG}".encode("ascii")[:15]
        r[16:16 + len(ver)] = ver
        return bytes(r)

    def _setup(self):
        """F8 -> F9, 124 bytes: 4-byte header + the 120-byte record."""
        return b"\x00\x00\x00\xF9" + self.config.setup_record()

    def close(self):
        self._stop = True
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# TCP 9999 setup menu


class SetupSession:
    """One telnet session on port 9999: the banner, the gate, the menu.

    A line-oriented state machine. The XPort's menu is a walk of prompts,
    each showing its current value in parentheses; Enter alone keeps the
    value and moves on. The prompt strings and their order are quoted from
    the XPort User Guide via reference/lantronix_setup_menu.md 3, so a
    person who has programmed a real card sees the screens they know.

    The `send` callback writes bytes to the client; `feed` is given each
    line the client types (already stripped of its terminator). The session
    keeps the setup on a COPY of the config and only writes it back on
    "Save and exit" (9), so "Exit without save" (8) truly discards, as on
    the hardware.
    """

    GATE_SECONDS = 5

    def __init__(self, config, send, log=None):
        self.config = config
        self.send = send
        self.log = log
        self.entered = False           # got past the Enter gate
        self.opened = time.monotonic()
        # the working copy; committed only on Save
        self.edit = {k: getattr(config, k) for k in config.DEFAULTS}
        self._queue = []               # the prompts still to walk
        self._closed = False
        self.greet()

    # -- output helpers ---------------------------------------------------

    def out(self, text):
        self.send(text.replace("\n", "\r\n").encode("ascii", "replace"))

    def raw(self, text):
        self.send(text.encode("ascii", "replace"))

    # -- the greeting and the gate ----------------------------------------

    def greet(self):
        """The banner, then the 5-second Enter gate."""
        mac = mac_hex(self.config.mac)
        self.out(f"MAC address {mac}\n")
        self.out(f"Software version {FIRMWARE_VERSION} "
                 f"({FIRMWARE_DATE}) {PRODUCT_TAG}\n\n")
        self.out("Press Enter for Setup Mode\n")

    def gate_expired(self):
        return (not self.entered
                and time.monotonic() - self.opened > self.GATE_SECONDS)

    # -- the main menu ----------------------------------------------------

    def show_settings(self):
        """The saved-parameter dump printed on entry."""
        c = self.edit
        self.out("\n*** Tank Monitor Console Simulator -- emulated "
                 "Lantronix XPort ***\n")
        self.out(f"MAC address {mac_hex(self.config.mac)}\n")
        self.out(f"Software version {FIRMWARE_VERSION} "
                 f"({FIRMWARE_DATE}) {PRODUCT_TAG}\n\n")
        self.out("Network/IP Settings:\n")
        if c["ip"] != "0.0.0.0":
            ip = c["ip"]
        else:
            ip = f"0.0.0.0 (Not set; AutoIP {self.config.autoip()})"
        self.out(f"   IP Address ...... {ip}\n")
        gw = c["gateway"] if c["gateway"] != "0.0.0.0" else "0.0.0.0 (Not set)"
        self.out(f"   Default Gateway . {gw}\n")
        self.out(f"   Netmask ......... {_netmask_of(c['netmask_bits'])}\n\n")
        self.out("Serial & Connection Settings:\n")
        self.out(f"   Baudrate {c['baudrate']}, I/F Mode {c['if_mode']:02X}, "
                 f"Flow {c['flow']:02X}\n")
        self.out(f"   Port {c['port']:05d}\n")
        self.out(f"   Connect Mode {c['connect_mode']:02X}, "
                 f"Disconn Mode {c['disconn_mode']:02X}\n")
        self.out(f"   Flush Mode {c['flush_mode']:02X}, "
                 f"Disconn Time {c['disconn_time']}\n\n")

    def main_menu(self):
        self.out("Change Setup:\n")
        self.out("  0 Server configuration\n")
        self.out("  1 Channel 1 configuration\n")
        self.out("  7 Factory defaults\n")
        self.out("  8 Exit without save\n")
        self.out("  9 Save and exit\n")
        self.raw("Your choice ? ")
        self._queue = [("menu", None)]

    # -- feeding lines in -------------------------------------------------

    def feed(self, line):
        """One line from the client. Returns False when the session ends."""
        if self._closed:
            return False
        if not self.entered:
            # any Enter at the gate opens setup; the line's content does
            # not matter, only that a return arrived
            self.entered = True
            self.show_settings()
            self.main_menu()
            return True
        if not self._queue:
            self.main_menu()
            return True
        kind, ctx = self._queue.pop(0)
        handler = getattr(self, f"_step_{kind}")
        handler(line.strip(), ctx)
        if not self._queue and not self._closed:
            self.main_menu()
        return not self._closed

    # -- the menu itself --------------------------------------------------

    def _step_menu(self, choice, _ctx):
        if choice == "0":
            self._queue = [("server", "ip")]
            self._prompt_server("ip")
        elif choice == "1":
            self._queue = [("channel", "baudrate")]
            self._prompt_channel("baudrate")
        elif choice == "7":
            self.edit = dict(XPortConfig.DEFAULTS)
            # factory defaults keep the address
            for k in ("ip", "gateway", "netmask_bits"):
                self.edit[k] = getattr(self.config, k)
            self.out("\nChannel 1, E-mail and Expert settings returned to "
                     "defaults. Network address kept.\n\n")
        elif choice == "8":
            self.out("\n")
            self.close()
        elif choice == "9":
            for k, v in self.edit.items():
                setattr(self.config, k, v)
            self.config.save()
            self.out("\nParameters stored ...\n")
            if self.log:
                self.log("-- XPort setup saved "
                         f"(IP {self.config.ip}, port {self.config.port})")
            self.close()
        else:
            # an unknown choice just redraws the menu, as the card does
            pass

    # -- server configuration (option 0) ---------------------------------

    SERVER_STEPS = ["ip", "gateway", "netmask", "password"]

    def _prompt_server(self, step):
        c = self.edit
        if step == "ip":
            self.raw(f"IP Address : {_octet_prompt(c['ip'])} ")
        elif step == "gateway":
            self.raw("Set Gateway IP Address (N) ? ")
        elif step == "netmask":
            self.raw("Netmask: Number of Bits for Host Part "
                     f"(0=default) ({c['netmask_bits']}) ")
        elif step == "password":
            self.raw("Change telnet config password (N) ? ")

    def _step_server(self, line, step):
        c = self.edit
        if step == "ip":
            if line:
                c["ip"] = _parse_ip(line, c["ip"])
        elif step == "gateway":
            if line[:1].upper() == "Y":
                self._queue.insert(0, ("gateway_ip", None))
                self.raw(f"Gateway IP addr {_octet_prompt(c['gateway'])} ")
                return
        elif step == "netmask":
            if line.isdigit():
                c["netmask_bits"] = int(line)
        elif step == "password":
            if line[:1].upper() == "Y":
                self._queue.insert(0, ("password_set", None))
                self.raw("Enter new Password : ")
                return
        self._advance_server(step)

    def _step_gateway_ip(self, line, _ctx):
        if line:
            self.edit["gateway"] = _parse_ip(line, self.edit["gateway"])
        self._advance_server("gateway")

    def _step_password_set(self, line, _ctx):
        self.edit["telnet_password"] = line[:4]
        self._advance_server("password")

    def _advance_server(self, step):
        nxt = self.SERVER_STEPS[self.SERVER_STEPS.index(step) + 1:]
        if nxt:
            self._queue = [("server", nxt[0])]
            self._prompt_server(nxt[0])
        else:
            self._queue = []

    # -- channel 1 configuration (option 1) ------------------------------

    CHANNEL_STEPS = ["baudrate", "if_mode", "flow", "port", "connect_mode",
                     "remote_ip", "remote_port", "disconn_mode",
                     "flush_mode", "disconn_time", "send_char_1",
                     "send_char_2"]

    def _prompt_channel(self, step):
        c = self.edit
        prompts = {
            "baudrate": f"Baudrate ({c['baudrate']}) ? ",
            "if_mode": f"I/F Mode ({c['if_mode']:02X}) ? ",
            "flow": f"Flow ({c['flow']:02X}) ? ",
            "port": f"Port No ({c['port']}) ? ",
            "connect_mode": f"ConnectMode ({c['connect_mode']:02X}) ? ",
            "remote_ip": f"Remote IP Address : {_octet_prompt(c['remote_ip'])} ",
            "remote_port": f"Remote Port  ({c['remote_port']}) ? ",
            "disconn_mode": f"DisConnMode ({c['disconn_mode']:02X}) ? ",
            "flush_mode": f"FlushMode ({c['flush_mode']:02X}) ? ",
            "disconn_time": f"DisConnTime ({c['disconn_time']}) ?: ",
            "send_char_1": f"SendChar 1 ({c['send_char_1']:02X}) ? ",
            "send_char_2": f"SendChar 2 ({c['send_char_2']:02X}) ? ",
        }
        self.raw(prompts[step])

    def _step_channel(self, line, step):
        c = self.edit
        if line:
            if step == "baudrate" and line.isdigit():
                c["baudrate"] = int(line)
            elif step in ("if_mode", "flow", "connect_mode", "disconn_mode",
                          "flush_mode", "send_char_1", "send_char_2"):
                v = _parse_hex(line)
                if v is not None:
                    c[step] = v
            elif step == "port" and line.isdigit():
                c["port"] = int(line)
            elif step == "remote_ip":
                c["remote_ip"] = _parse_ip(line, c["remote_ip"])
            elif step == "remote_port" and line.isdigit():
                c["remote_port"] = int(line)
            elif step == "disconn_time":
                c["disconn_time"] = _parse_mmss(line, c["disconn_time"])
        nxt = self.CHANNEL_STEPS[self.CHANNEL_STEPS.index(step) + 1:]
        if nxt:
            self._queue = [("channel", nxt[0])]
            self._prompt_channel(nxt[0])
        else:
            self._queue = []

    def close(self):
        self._closed = True


def _netmask_of(bits):
    return {0: "0.0.0.0", 8: "255.255.255.0", 16: "255.255.0.0",
            24: "255.0.0.0"}.get(bits, "255.255.255.0")


def _octet_prompt(ip):
    parts = (str(ip).split(".") + ["0", "0", "0", "0"])[:4]
    return " .".join(f"({int(p):03d})" for p in _ints(parts))


def _ints(parts):
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return out


def _parse_ip(line, current):
    """Accept a dotted quad, or a single octet the way the card does.

    The XPort walks the four octets one at a time. This emulator's line
    reader is simpler: it takes a whole dotted address on one line, and
    also tolerates the card's octet-at-a-time entry by accepting a lone
    number as the first octet. A partial or bad entry keeps the current
    value rather than throwing.
    """
    line = line.strip()
    if "." in line:
        parts = line.split(".")
        if len(parts) == 4 and all(p.strip().isdigit() for p in parts):
            nums = [int(p) & 0xFF for p in parts]
            return ".".join(str(n) for n in nums)
        return current
    if line.isdigit():
        rest = str(current).split(".")[1:]
        return ".".join([str(int(line) & 0xFF)] + (rest + ["0", "0", "0"])[:3])
    return current


def _parse_hex(line):
    try:
        return int(line, 16) & 0xFF
    except ValueError:
        return None


def _parse_mmss(line, current):
    line = line.strip()
    if ":" in line:
        mm, _, ss = line.partition(":")
        if mm.isdigit() and ss.isdigit():
            return f"{int(mm):02d}:{int(ss):02d}"
    return current


# ---------------------------------------------------------------------------
# the two listeners, run together


def _setup_server(config, host, log, ready=None, powered=None):
    powered = powered or (lambda: True)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((host, SETUP_PORT))
    except OSError as e:
        if log:
            log(f"-- XPort setup menu not listening: {e}")
        if ready:
            ready.set()
        return
    srv.listen(1)
    if log:
        log(f"-- XPort setup menu on tcp/{SETUP_PORT}")
    if ready:
        ready.set()
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        if not powered():
            # a dark console powers no XPort: the connection drops at once,
            # which is what a dead card looks like from the network
            try:
                conn.close()
            except OSError:
                pass
            continue
        threading.Thread(target=_setup_session, args=(conn, config, log),
                         daemon=True).start()


def _setup_session(conn, config, log):
    if log:
        log("-- XPort setup: telnet connected")
    session = SetupSession(config, conn.sendall, log)
    conn.settimeout(1.0)
    buf = bytearray()
    try:
        while True:
            if session.gate_expired():
                # the real card drops the connection when the Enter window
                # closes with no key
                break
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            # telnet negotiation bytes (IAC ...) are stripped; the setup
            # menu is line-oriented plain text underneath
            buf += _strip_iac(chunk)
            while b"\n" in buf or b"\r" in buf:
                line, buf = _split_line(buf)
                if not session.feed(line.decode("ascii", "replace")):
                    return
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _split_line(buf):
    for i, b in enumerate(buf):
        if b in (10, 13):
            line = bytes(buf[:i])
            rest = buf[i + 1:]
            # swallow a paired CR LF / LF CR
            if rest[:1] in (b"\n", b"\r") and rest[:1] != bytes([b]):
                rest = rest[1:]
            return line, bytearray(rest)
    return b"", buf


def _strip_iac(chunk):
    """Drop telnet IAC command sequences, keep the text.

    A telnet client opens with option negotiation: IAC (255) then a two- or
    three-byte command. The setup menu is plain text, so those are removed
    rather than answered, which is enough to walk the menu from any client.
    """
    out = bytearray()
    i = 0
    while i < len(chunk):
        b = chunk[i]
        if b == 255:                    # IAC
            if i + 1 < len(chunk) and chunk[i + 1] in (251, 252, 253, 254):
                i += 3                  # IAC WILL/WONT/DO/DONT x
                continue
            i += 2                      # IAC <cmd>
            continue
        out.append(b)
        i += 1
    return bytes(out)


def serve(config, host="0.0.0.0", log=None, discovery=True, powered=None):
    """Run the XPort's setup menu and discovery responder.

    Blocks, so a caller runs it on a thread. The serial tunnel on port
    10001 is `wire.serve`, started separately; this is the rest of the
    card. `discovery` can be turned off for a host that already has a real
    Lantronix tool answering on 30718, or where binding a low broadcast
    port is not wanted.
    """
    disc = None
    if discovery:
        disc = Discovery(config, log, powered)
        threading.Thread(target=disc.serve, args=(host,), daemon=True).start()
    _setup_server(config, host, log, powered=powered)
    if disc:
        disc.close()
