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
"""The XPort's telnet setup menu on port 9999.

This is the screen a technician programs the card from, so it is reproduced
byte for byte rather than approximately: the same prompts in the same order,
the same current-value-in-parentheses convention, the same odd spacing, and
the same line endings.

Those line endings are the reason this module deals in bytes rather than
text. The firmware ends a line with a bare carriage return, which telnet
requires be followed by a NUL, and it emits the pair in whichever order the
string it is printing happened to have -- sometimes `\\r\\x00\\n`, sometimes
`\\n\\r\\x00`, and the order is not consistent even within one screen. A
terminal does not care, but a diff against a real card does, and a test that
compares the emulator's output with the captured transcript would fail on
every line if this module normalised them. So each screen is held as the
literal bytes the card sent, with the values substituted in.

Everything here was read off a live Veeder-Root TCP/IP Interface Module; see
`reference/lantronix_xport_capture.md` for the transcript it came from.

The walk of each submenu is written as a generator: it yields the bytes of a
prompt and receives back the line the user typed. That keeps the branches
readable -- answering Y to the gateway question inserts four octet prompts,
a connect mode with the host-list bit set replaces the remote-address
prompts with the host-list editor -- which is exactly how the firmware
behaves and exactly what a technician has to learn.
"""
import socket
import threading
import time

from . import xportrec as R
from . import xport as _x

# The two orders the firmware writes a newline in. Named so the screens below
# read as the card printed them.
NL = b"\r\x00\n"
LN = b"\n\r\x00"

# The main menu, verbatim. Note that "7 Defaults" is the one entry followed
# by the other newline order, and that "9 Save and exit" shares its line with
# the prompt, separated by twelve spaces. Both are the card's, not typos.
MENU = (NL + LN + b"Change Setup:" + LN +
        b"  0 Server" + NL +
        b"  1 Channel 1" + NL +
        b"  3 E-mail" + NL +
        b"  5 Expert" + NL +
        b"  6 Security" + NL +
        b"  7 Defaults" + LN +
        b"  8 Exit without save" + NL +
        b"  9 Save and exit            Your choice ? ")

GATE_SECONDS = 5


def _b(text):
    return text.encode("latin-1", "replace")


def _on(flag):
    return "enabled" if flag else "disabled"


def _yn(flag):
    return "Y" if flag else "N"


def _is_yes(line):
    return line[:1].upper() == "Y"


def _hex2(line, current):
    """A two-hex-digit field: keep the current value if it will not parse."""
    try:
        return int(line, 16) & 0xFF
    except (ValueError, TypeError):
        return current


def _num(line, current):
    try:
        return int(line, 10)
    except (ValueError, TypeError):
        return current


def _key(prompt):
    """Mark a prompt the card answers on a single keypress.

    The yes/no questions do not wait for Enter: the first character decides
    them. A client that sends "Y\r" has therefore answered the question with
    the Y and given the Enter to whatever is asked next -- which is why
    typing Y and Return at the gateway question silently keeps the first
    octet and shifts the address. Emulated, because a trainee meets it.
    """
    return (prompt, True)


def _dotted(ip):
    """An address the way the host list prints one: 010.000.000.000.

    Every other address the menu shows is unpadded; the host list pads each
    octet to three digits. Captured from the card.
    """
    parts = (str(ip).split(".") + ["0"] * 4)[:4]
    out = []
    for p in parts:
        try:
            out.append("%03d" % int(p))
        except ValueError:
            out.append("000")
    return ".".join(out)


def _octet(line, current):
    try:
        v = int(line, 10)
    except (ValueError, TypeError):
        return current
    return v & 0xFF if 0 <= v <= 255 else current


class SetupSession:
    """One telnet session on port 9999.

    `send` is handed the bytes to write to the client. Feed the session the
    client's bytes with `feed_bytes`, or whole lines with `feed`.

    The session edits a COPY of the configuration and writes it back only on
    "9 Save and exit", so "8 Exit without save" truly discards, as on the
    hardware.
    """

    def __init__(self, config, send, log=None):
        self.config = config
        self.send = send
        self.log = log
        self.entered = False
        self.opened = time.monotonic()
        self._closed = False
        self._buf = bytearray()
        self._walk = None
        self._after_cr = False
        self._keypress = False
        # the working copy: a full clone of the records plus the settings
        # that live beside them
        self.edit = _x.XPortConfig(records=config.recs.as_dict(),
                                   mac=config.mac)
        self.edit.security = dict(config.security)
        self.edit.snmp_community = config.snmp_community
        self.greet()

    # -- output -----------------------------------------------------------

    def out(self, data):
        if isinstance(data, str):
            data = _b(data)
        self.send(data)

    def _emit(self, produced):
        """Send what a walk yielded, and note if it wants a single key."""
        if isinstance(produced, tuple):
            data, self._keypress = produced
        else:
            data, self._keypress = produced, False
        self.out(data)

    def greet(self):
        """The banner and the Enter gate, exactly as the card sends them.

        It opens with IAC WILL ECHO and IAC WILL SUPPRESS-GO-AHEAD: the card
        does its own echoing, which is why a character typed here comes back
        from the emulator rather than from the client's terminal.
        """
        self.out(b"\xff\xfb\x01\xff\xfb\x03")
        self.out(NL + _b("MAC address %s" % _x.mac_hex(self.config.mac)) + LN
                 + _b("Software version %s (%s) %s"
                      % (_x.FIRMWARE_VERSION, _x.FIRMWARE_DATE,
                         _x.PRODUCT_TAG))
                 + NL + LN + b"Press Enter for Setup Mode " + LN)

    def gate_expired(self):
        return (not self.entered
                and time.monotonic() - self.opened > GATE_SECONDS)

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    # -- input ------------------------------------------------------------

    def feed_bytes(self, data):
        """Client bytes: echo them as the card does, and act on each line."""
        if self._closed:
            return False
        data = _x._strip_iac(data)
        for b in data:
            if self._closed:
                return False
            ch = bytes([b])
            if b in (13, 10):
                # A client ends its line with CR, then LF or NUL. The CR is
                # the line; whatever pads it is not a second, empty one.
                if b == 10 and self._after_cr:
                    self._after_cr = False
                    continue
                self._after_cr = (b == 13)
                line = bytes(self._buf).decode("latin-1")
                self._buf.clear()
                # The card terminates an echoed line with a LF. A bare Enter
                # -- keeping the shown value -- echoed nothing to terminate.
                if line:
                    self.out(b"\n")
                self.feed(line)
            elif b == 0 and self._after_cr:
                self._after_cr = False
                continue
            elif b in (8, 127):
                self._after_cr = False
                if self._buf:
                    self._buf.pop()
                    self.out(b"\b \b")
            elif 32 <= b < 127:
                self._after_cr = False
                if self._keypress:
                    # Answered on the keypress. Anything after it -- the
                    # Enter a person naturally presses -- belongs to the
                    # next prompt, exactly as on the card.
                    self.out(ch)
                    self._buf.clear()
                    self.feed(ch.decode("latin-1"))
                    continue
                self._buf.append(b)
                self.out(ch)
        return not self._closed

    def feed(self, line):
        """One complete line. Returns False when the session has ended."""
        if self._closed:
            return False
        if not self.entered:
            # Any return within the window opens setup; the content of the
            # line does not matter, only that a return arrived.
            self.entered = True
            self.out(self.dump())
            self.out(MENU)
            return True
        if self._walk is None:
            self._choose(line.strip())
            return not self._closed
        try:
            self._emit(self._walk.send(line))
        except StopIteration as stop:
            self._walk = None
            if not self._closed:
                # A walk returns whatever the card prints between its last
                # answer and the menu. Channel 1 returns nothing; the rest
                # return a newline.
                self._keypress = False
                self.out((stop.value or b"") + MENU)
        return not self._closed

    def _start(self, walk):
        """Run a submenu generator up to its first prompt."""
        self._walk = walk
        try:
            self._emit(next(walk))
        except StopIteration as stop:
            self._walk = None
            self.out((stop.value or b"") + MENU)

    def _choose(self, choice):
        e = self.edit
        if choice == "0":
            self._start(self.server_walk())
        elif choice == "1":
            self._start(self.channel_walk())
        elif choice == "3":
            self._start(self.email_walk())
        elif choice == "5":
            self._start(self.expert_walk())
        elif choice == "6":
            self._start(self.security_walk())
        elif choice == "7":
            # Defaults prints nothing at all: a newline and the menu again.
            # It only touches the working copy, so leaving by 8 discards it,
            # which is what the card does -- confirmed on the bench.
            e.factory_defaults()
            self.out(NL + MENU)
        elif choice == "8":
            self.out(NL + b"exiting without save !" + LN)
            self.close()
        elif choice == "9":
            self.commit()
            self.out(NL + b"Parameters stored ..." + LN)
            if self.log:
                self.log("-- XPort setup saved (IP %s, port %d)"
                         % (self.config.ip, self.config.port))
            self.close()
        else:
            # An unrecognised choice just redraws the menu, as the card does.
            self.out(MENU)

    def commit(self):
        """Write the working copy back and save it, as option 9 does."""
        for num, data in self.edit.recs.as_dict().items():
            self.config.recs.put(num, data)
        self.config.security = dict(self.edit.security)
        self.config.snmp_community = self.edit.snmp_community
        self.config.save()

    # -- the parameter dump ----------------------------------------------

    def dump(self):
        """The saved-parameter block printed after the Enter gate.

        Reproduces the card's layout down to the column the values start in
        and the missing space after the comma before "netmask".
        """
        e = self.edit
        o = bytearray()
        o += NL + b"\n*** basic parameters " + LN
        o += b"Hardware: Ethernet TPI" + NL
        if e.gateway == "0.0.0.0":
            o += _b("IP addr %s, no gateway set" % e.ip) + NL
        else:
            o += _b("IP addr %s, gateway %s,netmask %s"
                    % (e.ip, e.gateway, e.netmask)) + NL
        o += NL + b"*** Security" + NL
        sec = e.security
        for label, flag in (
                ("SNMP is             ", sec["snmp"]),
                (None, None),
                ("Telnet Setup is     ", sec["telnet_setup"]),
                ("TFTP Download is    ", sec["tftp"]),
                ("Port 77FEh is       ", sec["port_77fe"]),
                ("Web Server is       ", sec["web_server"]),
                ("Web Setup is        ", sec["web_setup"]),
                ("ECHO is             ", sec["echo"]),
                ("Enhanced Password is", sec["enhanced_password"]),
                ("Port 77F0h is       ", sec["port_77f0"])):
            if label is None:
                o += _b("SNMP Community Name: %s" % e.snmp_community) + NL
                continue
            o += _b("%s %s" % (label, _on(flag))) + NL
        o += NL + b"*** Channel 1" + LN
        o += _b("Baudrate %d, I/F Mode %02X, Flow %02X"
                % (e.baudrate, e.if_mode, e.flow)) + LN
        o += _b("Port %d" % e.port) + NL
        o += _b("Connect Mode : %02X" % e.connect_mode) + NL
        if e.uses_datagram():
            # UDP says its piece in two lines and stops: no remote address,
            # no disconnect or flush mode, and none of the modem-mode
            # courtesies below.
            o += _b("Datagram Type %02X" % e.datagram_type) + NL
            o += _b("Pack Cntrl:   %02X" % e.pack_control) + NL
            o += NL + b"*** Expert" + NL
            o += _b("TCP Keepalive    : %ds" % e.keepalive) + LN
            o += _b("ARP cache timeout: %ds" % e.arp_timeout) + NL
            o += self._expert_tail(e)
            return bytes(o)
        o += _b("Send '+++' in Modem Mode %s" % _on(e.send_plus)) + NL
        o += _b("Show IP addr after 'RING' %s" % _on(e.show_ip)) + NL
        o += _b("Auto increment source port %s" % _on(e.auto_increment))
        if e.uses_hostlist():
            o += LN + b"Hostlist :" + NL
            entries = [(ip, p) for ip, p in e.hostlist() if ip != "0.0.0.0"]
            if not entries:
                o += LN + b"No Entry !" + LN
            else:
                o += LN
                for i, (ip, p) in enumerate(entries, 1):
                    o += _b("%02d. IP : %s  Port : %d"
                            % (i, _dotted(ip), p)) + NL
                o += LN
            o += _b("Hostlist Retrycounter  : %d" % e.hostlist_retry_count)
            o += LN
            o += _b("Hostlist Retrytimeout  : %d"
                    % e.hostlist_retry_timeout) + NL
        else:
            if e.remote_ip == "0.0.0.0":
                o += NL + _b("Remote IP Adr: --- none ---, Port %05d"
                             % e.remote_port) + NL
            else:
                o += NL + _b("Remote IP Adr: %s, Port %05d"
                             % (e.remote_ip, e.remote_port)) + NL
        o += _b("Disconn Mode : %02X" % e.disconn_mode)
        if e.telnet_mode():
            o += b" (Telnet Com Port Cntrl Enabled)"
        if e.disconn_time != "00:00":
            o += _b("  Disconn Time: %s" % e.disconn_time)
        o += NL
        o += _b("Flush   Mode : %02X" % e.flush_mode) + NL
        if e.send_char_1 or e.send_char_2:
            o += _b("SendChars    : %02X %02X "
                    % (e.send_char_1, e.send_char_2)) + NL
        if e.telnet_mode():
            o += _b("Terminal name: %s" % e.terminal_name) + NL
        o += NL + b"*** Expert" + NL
        o += _b("TCP Keepalive    : %ds" % e.keepalive) + LN
        o += _b("ARP cache timeout: %ds" % e.arp_timeout) + NL
        o += self._expert_tail(e)
        return bytes(o)

    def _expert_tail(self, e):
        """CPU performance through to the last trigger.

        Shared, because the datagram form of the dump reaches it by a
        shorter road through Channel 1.
        """
        o = bytearray()
        o += _b("CPU performance: %s"
                % ("High" if e.cpu_performance == 2 else "Regular")) + NL
        o += _b("Monitor Mode @ bootup : %s" % _on(e.monitor_mode)) + NL
        o += _b("RS485 tx enable  : active %s"
                % ("high" if e.rs485_high else "low")) + NL
        o += _b("HTTP Port Number : %d" % e.http_port) + NL
        o += _b("SMTP Port Number : %d" % e.smtp_port) + NL
        o += _b("MTU Size: %d" % e.mtu) + NL
        o += _b("Alternate MAC: %s" % _on(e.alternate_mac)) + NL
        o += _b("Ethernet connection type: %s"
                % R.ETH_MODES.get(e.eth_mode, "auto-negotiate")) + LN
        o += LN + b"*** E-mail" + LN
        o += _b("Mail server: %s" % e.mail_server) + LN
        o += _b("Unit       : %s" % e.unit_name) + LN
        o += _b("Domain     : %s" % e.domain_name) + LN
        o += _b("Recipient 1: %s" % e.recipient_1) + LN
        o += _b("Recipient 2: %s" % e.recipient_2) + LN
        for i in range(3):
            t = e.trigger(i)
            # The first trigger follows the Recipient 2 line, which ended
            # the other way round, so it needs only the bare newline; the
            # other two follow a line that ended with NL.
            o += (b"\n" if i == 0 else NL) + _b("- Trigger %d " % (i + 1))
            o += NL
            o += _b("Serial trigger input: %s" % _on(t["serial"])) + NL
            o += b"  Channel: 1" + NL
            o += _b("  Match: %02X,%02X" % t["match"]) + NL
            for pin in (1, 2, 3):
                o += _b("Trigger input%d: X" % pin) + NL
            o += _b("Message : %s" % t["message"]) + LN
            o += b"Priority: L" + NL
            o += _b("Min. notification interval: %d s" % t["notify"]) + NL
            o += _b("Re-notification interval  : %d s" % t["renotify"]) + NL
        return bytes(o)

    # -- option 0, Server -------------------------------------------------

    def _octets(self, label, current, first_sep):
        """The card's four-field address entry.

        It prompts one octet at a time -- `IP Address : (172) ` then
        `.(030) ` and so on -- and Enter alone keeps the octet shown. This
        yields each prompt in turn and returns the address that results.
        """
        parts = [int(x) for x in current.split(".")]
        out = []
        for i in range(4):
            if i == 0:
                prompt = first_sep + _b("%s(%03d) " % (label, parts[0]))
            else:
                prompt = _b(".(%03d) " % parts[i])
            line = yield prompt
            out.append(_octet(line.strip(), parts[i]))
        return ".".join(str(x) for x in out)

    def server_walk(self):
        e = self.edit
        e.ip = yield from self._octets("IP Address : ", e.ip, NL)
        # Enter alone takes the default the prompt is showing, and the
        # default is Y once a gateway is set -- so on a configured card
        # pressing Enter here walks INTO the gateway octets rather than past
        # them. Confirmed against the card both ways.
        has_gw = e.gateway != "0.0.0.0"
        line = yield _key(NL + _b("Set Gateway IP Address (%s) ? "
                                  % _yn(has_gw)))
        answer = line.strip()
        if _is_yes(answer) or (not answer and has_gw):
            e.gateway = yield from self._octets(
                "Gateway IP addr ", e.gateway, NL)
        line = yield NL + _b("Netmask: Number of Bits for Host Part "
                             "(0=default) (%d) " % e.netmask_bits)
        e.netmask_bits = _num(line.strip(), e.netmask_bits)
        line = yield _key(NL + b"Change telnet config password (N) ? ")
        if _is_yes(line.strip()):
            line = yield NL + b"Enter new Password: "
            e.telnet_password = line.strip()[:4]
        return NL

    # -- option 1, Channel 1 ----------------------------------------------

    def channel_walk(self):
        e = self.edit
        line = yield NL + _b("Baudrate (%d) ? " % e.baudrate)
        e.baudrate = _num(line.strip(), e.baudrate)
        line = yield NL + _b("I/F Mode (%02X) ? " % e.if_mode)
        e.if_mode = _hex2(line.strip(), e.if_mode)
        line = yield NL + _b("Flow (%02X) ? " % e.flow)
        e.flow = _hex2(line.strip(), e.flow)
        line = yield NL + _b("Port No (%d) ? " % e.port)
        e.port = _num(line.strip(), e.port)
        line = yield NL + _b("ConnectMode (%02X) ? " % e.connect_mode)
        e.connect_mode = _hex2(line.strip(), e.connect_mode)
        if e.uses_datagram():
            # A connect mode whose low nibble is C is UDP, and the walk then
            # asks one more question and goes back to the menu: no modem-mode
            # courtesies, no remote address, no disconnect or flush mode.
            line = yield NL + _b("Datagram Type (%02X) ? " % e.datagram_type)
            e.datagram_type = _hex2(line.strip(), e.datagram_type)
            return b""
        line = yield _key(NL + _b("Send '+++' in Modem Mode  (%s) ? "
                             % _yn(e.send_plus)))
        if line.strip():
            e.send_plus = _is_yes(line.strip())
        line = yield _key(NL + _b("Show IP addr after 'RING'  (%s) ? "
                             % _yn(e.show_ip)))
        if line.strip():
            e.show_ip = _is_yes(line.strip())
        line = yield _key(NL + _b("Auto increment source port  (%s) ? "
                             % _yn(e.auto_increment)))
        if line.strip():
            e.auto_increment = _is_yes(line.strip())

        if e.uses_hostlist():
            yield from self._hostlist_walk()
        else:
            e.remote_ip = yield from self._octets(
                "Remote IP Address : ", e.remote_ip, NL)
            line = yield NL + _b("Remote Port  (%d) ? " % e.remote_port)
            e.remote_port = _num(line.strip(), e.remote_port)

        line = yield NL + _b("DisConnMode (%02X) ? " % e.disconn_mode)
        e.disconn_mode = _hex2(line.strip(), e.disconn_mode)
        # The card annotates the answer when telnet com-port control is on,
        # on the same line, before the next prompt's newline.
        note = (b" (Telnet Com Port Cntrl Enabled)"
                if e.telnet_mode() else b"")
        line = yield note + NL + _b("FlushMode   (%02X) ? " % e.flush_mode)
        e.flush_mode = _hex2(line.strip(), e.flush_mode)
        # DisConnTime asks for minutes, then seconds after a bare colon.
        mm, ss = e.disconn_time.split(":")
        line = yield NL + _b("DisConnTime (%s) ?" % e.disconn_time)
        mm = "%02d" % _num(line.strip(), int(mm))
        line = yield b":"
        ss = "%02d" % _num(line.strip(), int(ss))
        e.disconn_time = "%s:%s" % (mm, ss)
        line = yield NL + _b("SendChar 1  (%02X) ? " % e.send_char_1)
        e.send_char_1 = _hex2(line.strip(), e.send_char_1)
        line = yield NL + _b("SendChar 2  (%02X) ? " % e.send_char_2)
        e.send_char_2 = _hex2(line.strip(), e.send_char_2)
        if e.telnet_mode():
            line = yield NL + _b("Terminal name (%s) ?" % e.terminal_name)
            if line.strip():
                e.terminal_name = line.strip()[:16]
            return NL
        return b""

    def _hostlist_walk(self):
        """The host-list editor, shown when Connect Mode has bit 0x20 set."""
        e = self.edit
        entries = [(ip, p) for ip, p in e.hostlist() if ip != "0.0.0.0"]
        head = NL + LN + b"Hostlist :" + NL
        if not entries:
            head += LN + b"No Entry !" + NL
        else:
            head += LN
            for i, (ip, p) in enumerate(entries, 1):
                head += _b("%02d. IP : %s  Port : %d"
                           % (i, _dotted(ip), p)) + NL
        head += LN + b"Change Hostlist ? (N) ? "
        line = yield _key(head)
        if _is_yes(line.strip()):
            for i in range(R.NUM_HLIST):
                ip, port = e.hostlist()[i]
                addr = yield from self._octets(
                    "%02d. IP address : " % (i + 1), ip, NL)
                line = yield _b("     Port :  (%d) ? " % port)
                port = _num(line.strip(), port)
                e.set_hostlist(i, addr, port)
                if addr == "0.0.0.0":
                    break
        line = yield NL + _b("Hostlist Retrycounter  (%d) ? "
                             % e.hostlist_retry_count)
        e.hostlist_retry_count = _num(line.strip(), e.hostlist_retry_count)
        line = yield NL + _b("Hostlist Retrytimeout  (%d) ? "
                             % e.hostlist_retry_timeout)
        e.hostlist_retry_timeout = _num(line.strip(),
                                        e.hostlist_retry_timeout)

    # -- option 3, E-mail --------------------------------------------------

    def email_walk(self):
        e = self.edit
        e.mail_server = yield from self._octets(
            "Mail server (%s) ? " % e.mail_server, e.mail_server,
            b"\n" + LN)
        line = yield NL + _b("Unit name (%s) ? " % e.unit_name)
        if line.strip():
            e.unit_name = line.strip()
        line = yield NL + _b("Domain name (%s) ? " % e.domain_name)
        if line.strip():
            e.domain_name = line.strip()
        line = yield NL + _b("Recipient 1 (%s) ? " % e.recipient_1)
        if line.strip():
            e.recipient_1 = line.strip()
        line = yield NL + _b("Recipient 2 (%s) ? " % e.recipient_2)
        if line.strip():
            e.recipient_2 = line.strip()
        for i in range(3):
            yield from self._trigger_walk(i)
        return NL

    def _trigger_walk(self, i):
        e = self.edit
        t = e.trigger(i)
        line = yield _key(NL + NL + _b("- Trigger %d " % (i + 1)) + NL +
                          _b("Enable serial trigger input (%s) ? "
                             % _yn(t["serial"])))
        if line.strip():
            e.set_trigger(i, serial=_is_yes(line.strip()))
        for pin in (1, 2, 3):
            sep = NL if pin == 1 else (b"\r\x00" + NL)
            yield sep + _b("Trigger input%d [A/I/X] (X) ? " % pin)
        line = yield b"\r\x00" + NL + _b("Message (%s) ? " % t["message"])
        if line.strip():
            e.set_trigger(i, message=line.strip())
        yield NL + b"\r\x00" + b"Priority (L) ? "
        line = yield (b"\r\x00" + NL +
                      _b("Min. notification interval (%d s) ? "
                         % t["notify"]))
        e.set_trigger(i, notify=_num(line.strip(), t["notify"]))
        line = yield NL + _b("Re-notification interval (%d s) ? "
                             % t["renotify"])
        e.set_trigger(i, renotify=_num(line.strip(), t["renotify"]))

    # -- option 5, Expert --------------------------------------------------

    def expert_walk(self):
        e = self.edit
        line = yield (LN +
                      _b("TCP Keepalive time in s (1s - 65s; 0s=disable):  "
                         "(%d) ? " % e.keepalive))
        e.keepalive = _num(line.strip(), e.keepalive)
        line = yield (LN + _b("ARP Cache timeout in s (1s - 600s) :  "
                              "(%d) ? " % e.arp_timeout))
        e.arp_timeout = _num(line.strip(), e.arp_timeout)
        line = yield (NL + _b("CPU performance (0=Regular, 1=Low, 2=High):  "
                              "(%d) ? " % e.cpu_performance))
        e.cpu_performance = _num(line.strip(), e.cpu_performance)
        line = yield _key(NL + _b("Disable Monitor Mode @ bootup (%s) ? "
                             % _yn(not e.monitor_mode)))
        if line.strip():
            e.monitor_mode = not _is_yes(line.strip())
        line = yield (NL + _b("RS485 tx enable active level (0=low; 1=high):"
                              "  (%d) ? " % (1 if e.rs485_high else 0)))
        if line.strip():
            e.rs485_high = _num(line.strip(), 0) == 1
        line = yield NL + _b("HTTP Port Number :  (%d) ? " % e.http_port)
        e.http_port = _num(line.strip(), e.http_port)
        line = yield NL + _b("SMTP Port Number :  (%d) ? " % e.smtp_port)
        e.smtp_port = _num(line.strip(), e.smtp_port)
        line = yield NL + _b("MTU Size (512 - 1400):  (%d) ? " % e.mtu)
        e.mtu = _num(line.strip(), e.mtu)
        line = yield _key(NL + _b("Enable alternate MAC (%s) ? "
                             % _yn(e.alternate_mac)))
        if line.strip():
            e.alternate_mac = _is_yes(line.strip())
        line = yield NL + _b("Ethernet connection type:  (%d) ? "
                             % e.eth_mode)
        e.eth_mode = _num(line.strip(), e.eth_mode)
        return NL

    # -- option 6, Security ------------------------------------------------

    def security_walk(self):
        e = self.edit
        s = e.security
        line = yield _key(NL + _b("Disable SNMP (%s) ? " % _yn(not s["snmp"])))
        if line.strip():
            s["snmp"] = not _is_yes(line.strip())
        line = yield (NL + LN +
                      _b("SNMP Community Name (%s): " % e.snmp_community))
        if line.strip():
            e.snmp_community = line.strip()[:13]
        for key, label in (("telnet_setup", "Disable Telnet Setup"),
                           ("tftp", "Disable TFTP Firmware Update"),
                           ("port_77fe", "Disable Port 77FEh"),
                           ("web_server", "Disable Web Server"),
                           ("web_setup", "Disable Web Setup")):
            line = yield _key(NL + NL + _b("%s (%s) ? " % (label, _yn(not s[key]))))
            if line.strip():
                s[key] = not _is_yes(line.strip())
        # ECHO is stored the other way round: the prompt asks to disable it
        # and the card ships with it disabled, so the default answer is Y.
        line = yield _key(NL + NL + _b("Disable ECHO ports (%s) ? "
                                  % _yn(not s["echo"])))
        if line.strip():
            s["echo"] = not _is_yes(line.strip())
        line = yield _key(NL + NL + _b("Enable Enhanced Password (%s) ? "
                                  % _yn(s["enhanced_password"])))
        if line.strip():
            s["enhanced_password"] = _is_yes(line.strip())
        line = yield _key(NL + NL + _b("Disable Port 77F0h (%s) ? "
                                  % _yn(not s["port_77f0"])))
        if line.strip():
            s["port_77f0"] = not _is_yes(line.strip())
        return NL


# ---------------------------------------------------------------------------
# the listener


def serve_setup(config, host="0.0.0.0", log=None, powered=None,
                on_socket=None):
    """Accept telnet sessions on 9999, one at a time as the card does.

    `on_socket` is handed the listening socket, so the card's network
    supervisor can close it and move the menu when the card is reprogrammed
    onto another address.
    """
    powered = powered or (lambda: True)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((host, _x.SETUP_PORT))
        srv.listen(1)
    except OSError as e:
        if log:
            log("-- XPort setup menu not listening: %s" % e)
        return
    if on_socket:
        on_socket(srv)
    if log:
        log("-- XPort setup menu on tcp/%d" % _x.SETUP_PORT)
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        if not (config.security.get("telnet_setup", True) and powered()):
            conn.close()
            continue
        threading.Thread(target=_session, args=(config, conn, log),
                         daemon=True).start()


def _session(config, conn, log=None):
    try:
        conn.settimeout(1.0)
        s = SetupSession(config, lambda d: conn.sendall(d), log=log)
        while not s.closed:
            try:
                data = conn.recv(1024)
            except socket.timeout:
                # The card drops a session that never answers the gate.
                if s.gate_expired():
                    break
                continue
            except OSError:
                break
            if not data:
                break
            if not s.feed_bytes(data):
                break
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
