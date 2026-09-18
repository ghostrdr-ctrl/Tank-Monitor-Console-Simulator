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
"""The XPort's setup records -- the bytes every interface reads and writes.

A Lantronix XPort keeps its whole configuration in a handful of fixed-length
binary records. Record 0 is the network address and the two serial channels;
record 3 is the host list plus the server and expert settings; records 5 and
6 carry e-mail; record 7 is the configurable pins. Every way of programming
the card is a different skin over these same bytes:

  * the telnet setup menu on 9999 prints a field, takes a new value, and
    stores it back into a record;
  * the web manager fetches the records as base64 in `setuprec.xml`, edits
    them in the browser, and posts them back to `setup.cgi`;
  * DeviceInstaller reads record 0 straight off UDP 30718 with opcode F8.

So the records are the model and the three interfaces are views. Getting the
offsets right is what makes a tool that talks to the emulator see the same
card the menu is showing.

The layout here was read off a live Veeder-Root TCP/IP Interface Module
(Lantronix XPort, firmware V6.5.0.7, MAC 00:20:4A:AD:64:70) rather than
inferred: the offsets come from the card's own web manager, which carries
them as named constants, and each one was checked against a byte in the
card's record whose value the telnet menu also prints. See
`reference/lantronix_xport_capture.md` for the capture and the cross-checks.
"""
import base64
import struct

# Record numbers the card serves, and how long each one is. Record 0 is
# shorter than the rest; that is the hardware's own shape, not a quirk here.
REC_SIZES = {0: 120, 3: 126, 4: 126, 5: 126, 6: 126, 7: 126, 8: 126, 9: 126}
REC_NUMBERS = (0, 3, 4, 5, 6, 7, 8, 9)

# The records the web manager posts back. Record 4 is fetched and displayed
# but never returned -- the card's own page has no field for it -- so an
# emulator that expects it back would reject a genuine save.
POSTED_RECORDS = (0, 3, 5, 6, 7, 8, 9)

# The Setup Capability Record: a bitmap of what this hardware can do, which
# the web manager consults before offering a baud rate or an RS-485 mode.
# Read verbatim off the card; `is_bit_set` below is its indexing rule.
SCR = bytes([0x1E, 0xEF, 0xFF, 0x00, 0x00, 0x00])

SCR_RS485 = 0x0008
SCR_BAUD_920K = 0x0010
SCR_BAUD_230K = 0x0120


def is_bit_set(scr, val):
    """The capability test the card's own pages use.

    The high byte of `val` picks the byte, the low byte is the mask, so
    0x0120 means "bit 0x20 of scr[1]".
    """
    return bool(scr[val >> 8] & (val & 0xFF))


# --------------------------------------------------------------------------
# record 0 -- network address and the serial channels

IPADDR_OFF = 0            # 4 bytes
NETMASK_OFF = 6           # host bits, not a dotted mask
TCPKEEP_OFF = 7           # seconds
PASSWD_OFF = 8            # 4 bytes, telnet/web setup password
GWADDR_OFF = 12           # 4 bytes
DHCP_NAME_0_OFF = 112     # first half of the DHCP name; rest is in record 3

CHAN_OFF = (16, 64)       # channel 1, channel 2
CH_PARAM_SIZE = 48

# offsets within a channel block
LINEIF_OFF = 0            # I/F mode: word length, parity, stop bits, RS-232/485
SPEED_OFF = 1             # baud rate code, see BAUD_CODES
FC_OFF = 2                # flow control
LPORT_OFF = 4             # local (tunnel) port, little-endian
RPORT_OFF = 6             # remote port, little-endian
RHOST_OFF = 8             # remote IP, 4 bytes
CMODE_OFF = 12            # connect mode
DMODE_OFF = 13            # disconnect mode (UDP datagram type in UDP mode)
IN_TO_MIN_OFF = 14        # disconnect timeout: minutes at +14, seconds at +15
SNDCHAR_OFF = 16          # send char 1 at +16, send char 2 at +17
FLMODE_OFF = 18           # flush mode
PCKCTRL_OFF = 19          # pack control
DEVADDR_OFF = 22          # 16 bytes of UDP device addresses
PASS_TERM_OFF = 32

UDP_MODE = 0x0C           # connect-mode value that means "UDP datagram"

# --------------------------------------------------------------------------
# record 3 -- host list, server and expert settings

HLRCNT_OFF = 0            # host list retry counter
HLRTO_OFF = 1             # host list retry timeout, ms
HLDATA_OFF = 3            # 12 entries of 4-byte IP + little-endian port
NUM_HLIST = 12
HLIST_ENTRY = 6
HLIST_SIZE = 72

STCHAR_OFF = 75           # serial channel start character
ETHMODE_OFF = 77          # bits 0-2: ethernet speed/duplex selection
MTUSIZE_OFF = 78          # little-endian; 0 means the 1400 default
CFGPRT_OFF = 80           # little-endian config (setup) port
ARPTO_OFF = 100           # little-endian ARP cache timeout, seconds
DHCP_NAME_3_OFF = 102     # second half of the DHCP name
HTTPPORT_OFF = 110        # little-endian; 0 means the 80 default
SMTPPORT_OFF = 112        # little-endian; 0 means the 25 default
MISC_OFF = 116            # a bitfield, see below
SENDIP_OFF = 117          # two modem-mode flags, see below

AINC_BIT = 0x01           # auto-increment source port
ESCPASS_BIT = 0x04        # enhanced (escape) password
DISMON_BIT = 0x40         # monitor mode at bootup DISABLED when set
CPUPERF_BIT = 0x80        # high CPU performance mode

SENDPLUS_BIT = 0x01       # send '+++' in modem mode
SHOWIP_BIT = 0x02         # show IP address after 'RING'

# Connect-mode and disconnect-mode bits that change which prompts the setup
# menu walks. Both were confirmed against the card: a connect mode with 0x20
# set replaces the Remote IP prompts with the host-list editor, and a
# disconnect mode with 0x20 set adds the Terminal name prompt.
CMODE_HOSTLIST = 0x20
DMODE_TELNET = 0x20       # telnet mode: adds the terminal-name prompt
DMODE_PORTPASS = 0x10     # port password: the same field holds a password
DMODE_DTRDROP = 0x80      # disconnect when DTR drops

# --------------------------------------------------------------------------
# record 5 / 6 -- e-mail, and record 6's triggers

SVRIP_OFF = 0             # record 5: mail server IP, 4 bytes
R1EM_OFF = 4              # record 5: recipient 1, 49 bytes
R2EM_OFF = 53             # record 5: recipient 2, 49 bytes
UNAME_OFF = 102           # record 5: unit name, 24 bytes
DNAME_OFF = 0             # record 6: domain name, 24 bytes

TRIG_OFF = (24, 58, 92)   # record 6: the three e-mail triggers
TRIG_PARAM_SIZE = 34

TMASK_OFF = 0             # within a trigger block
TCMP_OFF = 1
TSER_OFF = 2
TMBYTE_OFF = 3
TMESG_OFF = 5             # 24 bytes
TPRIO_OFF = 29
TNOT_OFF = 30             # little-endian seconds
TRENOT_OFF = 32           # little-endian seconds

# --------------------------------------------------------------------------
# record 7 -- configurable pins, record 8 -- interface flags

NUM_PINS = 3
GENW_OFF = 0              # record 8, bit 0: network interface selection
WIRELESS_EN = 0x01

# --------------------------------------------------------------------------
# encodings

# The card's baud-rate codes. Not an ordering anyone would guess -- 38400 is
# code 0 and the table climbs downwards -- so it is taken from the card's own
# page, where each rate is listed against the byte it stores.
BAUD_CODES = {
    38400: 0x00, 19200: 0x01, 9600: 0x02, 4800: 0x03, 2400: 0x04,
    1200: 0x05, 600: 0x06, 300: 0x07, 115200: 0x08, 57600: 0x09,
    230400: 0x0A, 460800: 0x0B, 921600: 0x0C,
}
BAUD_BY_CODE = {v: k for k, v in BAUD_CODES.items()}

# What the ethernet-mode nibble in record 3 stands for, as the menu prints it.
ETH_MODES = {
    0x00: "auto-negotiate",
    0x01: "10 Mbit, half duplex",
    0x02: "100 Mbit, half duplex",
    0x03: "10 Mbit, full duplex",
    0x04: "100 Mbit, full duplex",
}

# Flow-control values the card accepts, and the words its pages use.
FLOW_MODES = {
    0x00: "None",
    0x01: "XON/XOFF",
    0x02: "Hardware (RTS/CTS)",
    0x05: "XON/XOFF pass chars to host",
}


def baud_code(baud):
    """The byte that stands for a baud rate; 9600's code if it is not one."""
    return BAUD_CODES.get(int(baud), BAUD_CODES[9600])


def baud_of(code):
    return BAUD_BY_CODE.get(code & 0xFF, 9600)


def netmask_of(host_bits):
    """The dotted mask a host-bit count stands for.

    The card asks for the number of HOST bits, not the prefix length, and
    stores that count. Anything outside the table it treats as the default.
    """
    return {0: "0.0.0.0", 8: "255.255.255.0", 16: "255.255.0.0",
            24: "255.0.0.0", 32: "0.0.0.0"}.get(int(host_bits),
                                                "255.255.255.0")


def host_bits_of(mask):
    """The host-bit count a dotted mask stands for."""
    for bits, dotted in ((8, "255.255.255.0"), (16, "255.255.0.0"),
                         (24, "255.0.0.0"), (0, "0.0.0.0")):
        if mask == dotted:
            return bits
    return 8


def ip_bytes(text):
    """Four bytes from a dotted address; zeroes if it will not parse."""
    try:
        parts = [int(x) & 0xFF for x in str(text).split(".")]
    except (ValueError, AttributeError):
        return b"\x00\x00\x00\x00"
    if len(parts) != 4:
        return b"\x00\x00\x00\x00"
    return bytes(parts)


def ip_text(raw, off=0):
    return ".".join(str(b) for b in raw[off:off + 4])


class SetupRecords:
    """The card's records, as bytes, with the field map applied on top.

    Everything is stored in the records themselves rather than in parallel
    attributes, so a byte the emulator does not understand still survives a
    read-modify-write from the web manager -- which is exactly what a real
    card does with the reserved bytes in its own records.
    """

    def __init__(self, records=None):
        self.rec = {n: bytearray(REC_SIZES[n]) for n in REC_NUMBERS}
        self.scr = bytearray(SCR)
        if records:
            for n, data in records.items():
                self.put(n, data)

    # -- whole-record access ---------------------------------------------

    def put(self, num, data):
        """Store a record, padded or trimmed to the length the card uses."""
        num = int(num)
        if num not in self.rec:
            return False
        size = REC_SIZES[num]
        buf = bytearray(data[:size])
        if len(buf) < size:
            buf.extend(b"\x00" * (size - len(buf)))
        self.rec[num] = buf
        return True

    def get(self, num):
        return bytes(self.rec[int(num)])

    def copy(self):
        return SetupRecords({n: bytes(v) for n, v in self.rec.items()})

    def as_dict(self):
        return {n: bytes(v) for n, v in self.rec.items()}

    # -- primitive field access ------------------------------------------

    def u8(self, num, off, value=None):
        if value is None:
            return self.rec[num][off]
        self.rec[num][off] = value & 0xFF
        return value

    def le16(self, num, off, value=None):
        if value is None:
            return struct.unpack_from("<H", self.rec[num], off)[0]
        struct.pack_into("<H", self.rec[num], off, value & 0xFFFF)
        return value

    def ip(self, num, off, value=None):
        if value is None:
            return ip_text(self.rec[num], off)
        self.rec[num][off:off + 4] = ip_bytes(value)
        return value

    def text(self, num, off, length, value=None):
        """A NUL-padded fixed-length string field."""
        if value is None:
            raw = bytes(self.rec[num][off:off + length])
            return raw.split(b"\x00")[0].decode("latin-1")
        enc = str(value).encode("latin-1", "replace")[:length]
        self.rec[num][off:off + length] = enc + b"\x00" * (length - len(enc))
        return value

    def bit(self, num, off, mask, value=None):
        if value is None:
            return bool(self.rec[num][off] & mask)
        if value:
            self.rec[num][off] |= mask
        else:
            self.rec[num][off] &= 0xFF & ~mask
        return value

    # -- channel helpers --------------------------------------------------

    def ch(self, chan, off, kind="u8", value=None, length=0):
        base = CHAN_OFF[chan] + off
        if kind == "u8":
            return self.u8(0, base, value)
        if kind == "le16":
            return self.le16(0, base, value)
        if kind == "ip":
            return self.ip(0, base, value)
        if kind == "text":
            return self.text(0, base, length, value)
        raise ValueError(kind)

    # -- the XML the web manager fetches ---------------------------------

    def to_xml(self):
        """`setuprec.xml`, in the shape the card serves it.

        The card emits every record it holds, base64 with no line breaks,
        then the capability record. The DOCTYPE and element names are what
        the page's parser looks for.
        """
        parts = ['<?xml version="1.0"?>',
                 '<!DOCTYPE SETUPREC SYSTEM "setuprec.dtd">',
                 '<SETUPREC>']
        for num in REC_NUMBERS:
            data = base64.b64encode(bytes(self.rec[num])).decode("ascii")
            parts.append('<RECORD>')
            parts.append('<NUM>%02d</NUM>' % num)
            parts.append('<DATA>%s</DATA>' % data)
            parts.append('</RECORD>')
        parts.append('<SCR>%s</SCR>'
                     % base64.b64encode(bytes(self.scr)).decode("ascii"))
        parts.append('</SETUPREC>')
        return "\n".join(parts) + "\n"

    def load_posted(self, fields):
        """Take records back from a `setup.cgi` post.

        `fields` maps "rec0", "rec3" ... to base64 text. Anything missing is
        left as it was, which is how the card treats a record its page did
        not send -- record 4 never comes back at all.
        """
        changed = []
        for num in POSTED_RECORDS:
            key = "rec%d" % num
            if key not in fields:
                continue
            try:
                raw = base64.b64decode(fields[key], validate=False)
            except (ValueError, TypeError):
                continue
            if not raw:
                continue
            self.put(num, raw)
            changed.append(num)
        return changed
