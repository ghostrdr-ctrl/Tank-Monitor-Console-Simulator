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
"""The card's web manager: the same settings pages, served over HTTP.

The device server inside the TCP/IP Interface Module has a small web manager
as well as its telnet menu, and a technician may be sent to either. This
serves the same pages: the same frame layout, the same navigation, the same
fields with the same names and the same validation, reading and writing the
same setup records as the menu and DeviceInstaller.

It is a reimplementation, not a copy. The markup, the stylesheet and the
server are written here; the manufacturer's own HTML, scripts and images are
not redistributed, and the branding is this simulator's. What is reproduced
is behaviour -- which is the part a technician has to learn.

Two behaviours matter more than they look:

  * Each page's OK button stores that page's fields into a PENDING copy of
    the records. Nothing reaches the card until "Apply Settings". A
    technician who fills in a page and navigates away without pressing OK
    loses it, and one who presses OK on every page but never Apply Settings
    has changed nothing -- both are real, and both catch people out.
  * "Apply Settings" writes the records and reboots the unit, which drops
    the connection. That is what the real card does and why the page says so.

`setuprec.xml` and `unitinfo.xml` are served in the card's own shape, because
they are an interface rather than a design: a tool that reads the records
over HTTP expects those element names, and `setup.cgi` accepts a post of
base64 records the same way.
"""
import base64
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from . import xportrec as R

PRODUCT = "TCP/IP Interface Module"
SUBTITLE = "Serial Device Server"

STYLE = """
body { font-family: Verdana, Geneva, sans-serif; font-size: 11px;
       margin: 0; background: #ffffff; color: #000000; }
a { color: #003366; }
.bannerbar { background: #1f3a5f; color: #ffffff; padding: 10px 14px; }
.bannerbar h1 { font-size: 15px; margin: 0 0 2px 0; font-weight: bold; }
.bannerbar .sub { font-size: 10px; color: #b8cbe4; }
.bannerbar .ident { float: right; text-align: right; font-size: 10px;
                    color: #b8cbe4; }
.nav { background: #999999; height: 100%; padding: 0; margin: 0; }
.nav ul { list-style: none; margin: 0; padding: 0; }
.nav li.head { background: #667a94; color: #ffffff; padding: 4px 8px;
               font-weight: bold; }
.nav li.item { background: #c0c0c0; padding: 3px 8px 3px 16px; }
.nav li.item a, .nav li.head a { color: #000033; text-decoration: none; }
.nav li.item a:hover, .nav li.head a:hover { text-decoration: underline; }
.nav li.cur { background: #ffffff; }
.page { padding: 12px 16px; }
h2 { font-size: 13px; margin: 0 0 10px 0; }
table.form { border-collapse: collapse; }
table.form td { padding: 2px 6px 2px 0; font-size: 11px;
                vertical-align: middle; }
table.form td.label { text-align: right; white-space: nowrap; }
input[type=text], input[type=password], select {
    font-family: Verdana, sans-serif; font-size: 11px; }
input.oct { width: 34px; text-align: right; }
input.num { width: 70px; }
input.hex { width: 34px; }
input.wide { width: 240px; }
.btn { margin-top: 12px; }
.note { color: #666666; font-size: 10px; margin-top: 14px;
        border-top: 1px solid #cccccc; padding-top: 6px; }
.warn { color: #a00000; }
.ok { color: #006000; }
fieldset { border: 1px solid #999999; margin: 0 0 10px 0; padding: 8px; }
legend { font-weight: bold; }
"""

# The pages in the order the navigation lists them.
NAV = [
    ("head", "netset.htm", "Network"),
    ("head", "servset.htm", "Server"),
    ("plain", None, "Serial Tunnel"),
    ("item", "hlist.htm", "Hostlist"),
    ("plain", None, "Channel 1"),
    ("item", "serial.htm", "Serial Settings"),
    ("item", "connset.htm", "Connection"),
    ("head", "smtpset.htm", "Email"),
    ("item", "smtptrig.htm?t=0", "Trigger 1"),
    ("item", "smtptrig.htm?t=1", "Trigger 2"),
    ("item", "smtptrig.htm?t=2", "Trigger 3"),
    ("head", "gpio.htm", "Configurable Pins"),
    ("head", "apply.htm", "Apply Settings"),
    ("head", "subdef.htm", "Apply Defaults"),
]


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _opt(value, label, current):
    sel = " selected" if str(value) == str(current) else ""
    return '<option value="%s"%s>%s</option>' % (esc(value), sel, esc(label))


def _select(name, pairs, current, extra=""):
    out = ['<select name="%s"%s>' % (name, extra)]
    for value, label in pairs:
        out.append(_opt(value, label, current))
    out.append("</select>")
    return "".join(out)


def _text(name, value, cls="num", size=None, kind="text"):
    sz = ' size="%d"' % size if size else ""
    return ('<input type="%s" name="%s" class="%s" value="%s"%s>'
            % (kind, name, cls, esc(value), sz))


def _octets(name, addr):
    """Four boxes for an address, the way the card's pages take one."""
    parts = (str(addr).split(".") + ["0"] * 4)[:4]
    return " . ".join(
        '<input type="text" name="%s%d" class="oct" value="%s" maxlength="3">'
        % (name, i + 1, esc(parts[i])) for i in range(4))


def _radio(name, value, current, label):
    chk = " checked" if str(value) == str(current) else ""
    return ('<label><input type="radio" name="%s" value="%s"%s> %s</label>'
            % (name, esc(value), chk, esc(label)))


def _yesno(name, flag):
    return (_radio(name, "1", "1" if flag else "0", "Enable") + " &nbsp; "
            + _radio(name, "0", "1" if flag else "0", "Disable"))


def _addr_from(fields, name, current):
    parts = []
    for i in range(4):
        got = fields.get("%s%d" % (name, i + 1), [""])[0].strip()
        cur = (str(current).split(".") + ["0"] * 4)[i]
        try:
            v = int(got)
            parts.append(str(v & 0xFF) if 0 <= v <= 255 else cur)
        except ValueError:
            parts.append(cur)
    return ".".join(parts)


def _int_from(fields, name, current, lo=None, hi=None):
    raw = fields.get(name, [""])[0].strip()
    try:
        v = int(raw, 10)
    except ValueError:
        return current
    if lo is not None and v < lo:
        return current
    if hi is not None and v > hi:
        return current
    return v


def _hex_from(fields, name, current):
    raw = fields.get(name, [""])[0].strip()
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return current


def _str_from(fields, name, current):
    if name not in fields:
        return current
    return fields[name][0]


def _flag_from(fields, name, current):
    if name not in fields:
        return current
    return fields[name][0] not in ("0", "", "off")


class WebState:
    """The pending records the pages edit before Apply Settings writes them.

    A real card keeps this in the browser; keeping it here instead means the
    pages need no scripting to work, and the behaviour a technician sees --
    OK stores, Apply Settings commits -- is the same.
    """

    def __init__(self, config):
        self.config = config
        self.pending = None
        self.lock = threading.Lock()
        self.rebooting_until = 0.0

    def edit(self):
        """The working copy, created from the live records on first use."""
        if self.pending is None:
            from . import xport
            self.pending = xport.XPortConfig(
                records=self.config.recs.as_dict(), mac=self.config.mac)
            self.pending.security = dict(self.config.security)
            self.pending.snmp_community = self.config.snmp_community
        return self.pending

    def discard(self):
        self.pending = None

    def commit(self):
        e = self.edit()
        for num, data in e.recs.as_dict().items():
            self.config.recs.put(num, data)
        self.config.security = dict(e.security)
        self.config.snmp_community = e.snmp_community
        self.config.save()
        self.discard()
        # A real unit reboots after Apply Settings and is off the network
        # for a few seconds. Emulate the gap so the page's warning is true.
        self.rebooting_until = time.monotonic() + 3.0

    def defaults(self):
        self.config.factory_defaults()
        self.discard()
        self.rebooting_until = time.monotonic() + 3.0

    def rebooting(self):
        return time.monotonic() < self.rebooting_until


# ---------------------------------------------------------------------------
# page rendering


def frame_shell():
    return ("<html><head><title>%s</title></head>\n"
            '<frameset rows="62,*" frameborder="0" border="0">\n'
            '  <frame name="topbar" src="banner.htm" scrolling="no" '
            'noresize marginheight="0" marginwidth="0">\n'
            '  <frameset cols="150,*" frameborder="0" border="0">\n'
            '    <frame name="leftmenu" src="menu.htm" scrolling="no" '
            'noresize marginheight="0" marginwidth="0">\n'
            '    <frame name="data" src="welcome.htm" marginheight="0" '
            'marginwidth="0">\n'
            "  </frameset>\n</frameset>\n</html>\n" % esc(PRODUCT))


def page(body, title="", cls="page"):
    return ("<html><head><title>%s</title><style>%s</style></head>"
            '<body><div class="%s">%s</div></body></html>'
            % (esc(title or PRODUCT), STYLE, cls, body))


def banner_page(cfg):
    from . import xport
    return ("<html><head><style>%s</style></head><body "
            'style="margin:0">\n'
            '<div class="bannerbar">\n'
            '  <div class="ident">Firmware %s<br>MAC %s</div>\n'
            "  <h1>%s</h1>\n"
            '  <div class="sub">%s</div>\n'
            "</div></body></html>"
            % (STYLE, esc(xport.FIRMWARE_VERSION),
               esc(xport.mac_hex(cfg.mac, ":")), esc(PRODUCT), esc(SUBTITLE)))


def menu_page(current=""):
    items = ['<html><head><style>%s</style></head>'
             '<body style="margin:0"><div class="nav"><ul>' % STYLE]
    for kind, href, label in NAV:
        if kind == "plain":
            items.append('<li class="head">%s</li>' % esc(label))
            continue
        cls = "head" if kind == "head" else "item"
        if href and href.split("?")[0] == current:
            cls += " cur"
        items.append('<li class="%s"><a href="%s" target="data">%s</a></li>'
                     % (cls, esc(href), esc(label)))
    items.append("</ul></div></body></html>")
    return "".join(items)


def welcome_page(cfg):
    body = [
        "<h2>%s</h2>" % esc(PRODUCT),
        "<p>The device server that puts the console on the network. "
        "Use the menu on the left to read or change its settings.</p>",
        '<table class="form">',
        '<tr><td class="label">IP address</td><td>%s</td></tr>'
        % esc(cfg.effective_ip()),
        '<tr><td class="label">Subnet mask</td><td>%s</td></tr>'
        % esc(cfg.netmask),
        '<tr><td class="label">Gateway</td><td>%s</td></tr>'
        % esc(cfg.gateway),
        '<tr><td class="label">Serial tunnel port</td><td>%d</td></tr>'
        % cfg.port,
        '<tr><td class="label">Serial settings</td>'
        "<td>%d baud, I/F %02X, flow %02X</td></tr>"
        % (cfg.baudrate, cfg.if_mode, cfg.flow),
        "</table>",
        '<p class="note">Changes made on these pages are held until you '
        "press <b>Apply Settings</b>. Applying settings reboots the unit.</p>",
        '<p class="note">This is a simulated card in a training simulator. '
        "It is not affiliated with, authorised by or endorsed by any device "
        "manufacturer.</p>",
    ]
    return page("".join(body), "Home")


def form(action, body, extra=""):
    return ('<form method="post" action="%s">%s'
            '<div class="btn"><input type="submit" value="OK">%s</div>'
            "</form>" % (action, body, extra))


def net_page(e, saved=False):
    dyn = "0" if e.assigned() else "1"
    body = [
        "<h2>Network Settings</h2>",
        _saved(saved),
        "<fieldset><legend>IP Configuration</legend>",
        "<p>" + _radio("dynip", "1", dyn,
                       "Obtain IP address automatically") + "</p>",
        '<table class="form">',
        "<tr><td></td><td>"
        + '<label><input type="checkbox" name="nobootp" value="1"%s> '
          "Disable BOOTP</label> " % ""
        + '<label><input type="checkbox" name="nodhcp" value="1"%s> '
          "Disable DHCP</label> " % ""
        + '<label><input type="checkbox" name="noautoip" value="1"%s> '
          "Disable AutoIP</label>" % ""
        + "</td></tr>",
        '<tr><td class="label">DHCP host name</td><td>%s</td></tr>'
        % _text("dhcphname", "", "wide"),
        "</table>",
        "<p>" + _radio("dynip", "0", dyn, "Use the following IP configuration")
        + "</p>",
        '<table class="form">',
        '<tr><td class="label">IP address</td><td>%s</td></tr>'
        % _octets("ipaddr", e.ip),
        '<tr><td class="label">Subnet mask</td><td>%s</td></tr>'
        % _octets("ipmask", e.netmask),
        '<tr><td class="label">Default gateway</td><td>%s</td></tr>'
        % _octets("ipgw", e.gateway),
        "</table></fieldset>",
        "<fieldset><legend>Ethernet Configuration</legend>",
        "<p>" + '<label><input type="checkbox" name="autoneg" value="1"%s> '
        "Auto negotiate</label></p>" % (" checked" if e.eth_mode == 0 else ""),
        '<table class="form">',
        '<tr><td class="label">Speed</td><td>%s</td></tr>'
        % (_radio("speed", "100", "100" if e.eth_mode in (0, 2, 4) else "10",
                  "100 Mbps") + " "
           + _radio("speed", "10", "100" if e.eth_mode in (0, 2, 4) else "10",
                    "10 Mbps")),
        '<tr><td class="label">Duplex</td><td>%s</td></tr>'
        % (_radio("duplex", "full", "full" if e.eth_mode in (0, 3, 4)
                  else "half", "Full") + " "
           + _radio("duplex", "half", "full" if e.eth_mode in (0, 3, 4)
                    else "half", "Half")),
        "</table></fieldset>",
    ]
    return page(form("netset.htm", "".join(body)), "Network Settings")


def _saved(saved):
    if not saved:
        return ""
    return ('<p class="ok">Stored. Press <b>Apply Settings</b> when you have '
            "finished making changes.</p>")


def server_page(e, saved=False):
    body = [
        "<h2>Server Settings</h2>",
        _saved(saved),
        "<fieldset><legend>Server Configuration</legend>",
        '<table class="form">',
        '<tr><td class="label">Telnet password</td><td>%s</td></tr>'
        % _text("telpasswd", "", "num", kind="password"),
        '<tr><td class="label">Retype password</td><td>%s</td></tr>'
        % _text("telrepasswd", "", "num", kind="password"),
        "</table></fieldset>",
        "<fieldset><legend>Advanced</legend>",
        '<table class="form">',
        '<tr><td class="label">ARP cache timeout (s)</td><td>%s</td></tr>'
        % _text("arpcato", e.arp_timeout),
        '<tr><td class="label">TCP keepalive (s)</td><td>%s</td></tr>'
        % _text("tcpkeep", e.keepalive),
        '<tr><td class="label">Monitor mode at bootup</td><td>%s</td></tr>'
        % _yesno("disnmon", e.monitor_mode),
        '<tr><td class="label">CPU performance</td><td>%s</td></tr>'
        % _select("cpuperf", [(0, "Regular"), (1, "Low"), (2, "High")],
                  e.cpu_performance),
        '<tr><td class="label">HTTP port</td><td>%s</td></tr>'
        % _text("httpport", e.http_port),
        '<tr><td class="label">MTU size</td><td>%s</td></tr>'
        % _text("mtusize", e.mtu),
        '<tr><td class="label">Config server port</td><td>%s</td></tr>'
        % _text("cfgport", e.recs.le16(3, R.CFGPRT_OFF) or 9999),
        "</table></fieldset>",
    ]
    return page(form("servset.htm", "".join(body)), "Server Settings")


def hostlist_page(e, saved=False):
    rows = ['<table class="form">'
            "<tr><td></td><td><b>Host address</b></td>"
            "<td><b>Port</b></td></tr>"]
    for i, (ip, port) in enumerate(e.hostlist(), 1):
        rows.append('<tr><td class="label">%d</td><td>%s</td><td>%s</td></tr>'
                    % (i, _octets("h%dip" % i, ip),
                       _text("h%dport" % i, port, "num", 6)))
    rows.append("</table>")
    body = [
        "<h2>Hostlist Settings</h2>",
        _saved(saved),
        "<fieldset><legend>Retry Settings</legend>",
        '<table class="form">',
        '<tr><td class="label">Retry counter</td><td>%s</td></tr>'
        % _text("hlretrycnt", e.hostlist_retry_count),
        '<tr><td class="label">Retry timeout (ms)</td><td>%s</td></tr>'
        % _text("hlretryto", e.hostlist_retry_timeout),
        "</table></fieldset>",
        "<fieldset><legend>Host Information</legend>",
        "".join(rows),
        "</fieldset>",
        '<p class="note">The host list is used only when the connect mode '
        "selects it. Entries are tried in order.</p>",
    ]
    return page(form("hlist.htm", "".join(body)), "Hostlist Settings")


# The I/F mode byte packs the line interface, word length, parity and stop
# bits. These are the combinations the pages offer.
DATA_BITS = [(7, "7"), (8, "8")]
PARITY = [(0, "None"), (1, "Odd"), (3, "Even")]
STOP_BITS = [(1, "1"), (2, "2")]


def _if_parts(if_mode):
    """Split an I/F mode byte into interface, data bits, parity, stop bits.

    The card's canonical value 4C is RS-232C, 8 bit, no parity, one stop bit,
    and its own documentation states that mapping directly rather than
    deriving it from the bits, so 4C is treated as that token.
    """
    proto = (if_mode >> 6) & 0x03
    if if_mode == 0x4C:
        return proto, 8, 0, 1
    bits = 7 if (if_mode & 0x03) == 0x02 else 8
    par = (if_mode >> 2) & 0x03
    stop = 2 if ((if_mode >> 4) & 0x03) == 0x03 else 1
    return proto, bits, par, stop


def _if_byte(proto, bits, par, stop):
    if (proto, bits, par, stop) == (1, 8, 0, 1):
        return 0x4C
    v = (proto & 0x03) << 6
    v |= (0x03 if stop == 2 else 0x01) << 4
    v |= (par & 0x03) << 2
    v |= 0x03 if bits == 8 else 0x02
    return v & 0xFF


def serial_page(e, saved=False):
    proto, bits, par, stop = _if_parts(e.if_mode)
    body = [
        "<h2>Serial Settings</h2>",
        _saved(saved),
        "<fieldset><legend>Port Settings</legend>",
        '<table class="form">',
        '<tr><td class="label">Protocol</td><td>%s</td></tr>'
        % _select("serproto", [(0, "RS-232"), (1, "RS-422/485 4-wire"),
                               (3, "RS-485 2-wire")], proto),
        '<tr><td class="label">Flow control</td><td>%s</td></tr>'
        % _select("flow", [(0, "None"), (1, "XON/XOFF"),
                           (2, "Hardware (RTS/CTS)"),
                           (5, "XON/XOFF pass chars to host")], e.flow),
        '<tr><td class="label">Baud rate</td><td>%s</td></tr>'
        % _select("baud", [(b, str(b)) for b in
                           sorted(R.BAUD_CODES, key=lambda x: x)],
                  e.baudrate),
        '<tr><td class="label">Data bits</td><td>%s</td></tr>'
        % _select("data", DATA_BITS, bits),
        '<tr><td class="label">Parity</td><td>%s</td></tr>'
        % _select("parity", PARITY, par),
        '<tr><td class="label">Stop bits</td><td>%s</td></tr>'
        % _select("stop", STOP_BITS, stop),
        "</table></fieldset>",
        "<fieldset><legend>Pack Control</legend>",
        '<table class="form">',
        '<tr><td class="label">Packing</td><td>%s</td></tr>'
        % _yesno("packyn", bool(e.recs.ch(0, R.PCKCTRL_OFF))),
        '<tr><td class="label">Send character 1</td><td>%s</td></tr>'
        % _text("pctrlmdata1", "%02X" % e.send_char_1, "hex", 2),
        '<tr><td class="label">Send character 2</td><td>%s</td></tr>'
        % _text("pctrlmdata2", "%02X" % e.send_char_2, "hex", 2),
        "</table></fieldset>",
        "<fieldset><legend>Flush Mode</legend>",
        '<table class="form">',
        '<tr><td class="label">Flush mode byte</td><td>%s</td></tr>'
        % _text("flidisc", "%02X" % e.flush_mode, "hex", 2),
        "</table></fieldset>",
        '<p class="note">The console expects 9600 baud, I/F mode 4C '
        "(RS-232C, 8 data bits, no parity, one stop bit). Software V21 and "
        "later also expects hardware flow control.</p>",
    ]
    return page(form("serial.htm", "".join(body)), "Serial Settings")


def conn_page(e, saved=False):
    body = [
        "<h2>Connection Settings</h2>",
        _saved(saved),
        "<fieldset><legend>Connect Mode</legend>",
        '<table class="form">',
        '<tr><td class="label">Connect mode byte</td><td>%s</td></tr>'
        % _text("actvconn", "%02X" % e.connect_mode, "hex", 2),
        '<tr><td class="label">Accept incoming</td><td>%s</td></tr>'
        % _select("accptin", [(0xC0, "Always accept"),
                              (0x40, "Accept with DTR active"),
                              (0x00, "Never accept")],
                  e.connect_mode & 0xC0),
        '<tr><td class="label">Use hostlist</td><td>%s</td></tr>'
        % _yesno("hlistyn", e.uses_hostlist()),
        '<tr><td class="label">Auto increment source port</td>'
        "<td>%s</td></tr>" % _yesno("ainclport", e.auto_increment),
        "</table></fieldset>",
        "<fieldset><legend>Endpoint Configuration</legend>",
        '<table class="form">',
        '<tr><td class="label">Local port</td><td>%s</td></tr>'
        % _text("lport", e.port, "num", 6),
        '<tr><td class="label">Remote port</td><td>%s</td></tr>'
        % _text("rport", e.remote_port, "num", 6),
        '<tr><td class="label">Remote host</td><td>%s</td></tr>'
        % _octets("rhost", e.remote_ip),
        "</table></fieldset>",
        "<fieldset><legend>Disconnect Mode</legend>",
        '<table class="form">',
        '<tr><td class="label">Disconnect mode byte</td><td>%s</td></tr>'
        % _text("dtrdisc", "%02X" % e.disconn_mode, "hex", 2),
        '<tr><td class="label">Inactivity timeout</td>'
        "<td>%s : %s (mm:ss)</td></tr>"
        % (_text("inactmin", e.disconn_time.split(":")[0], "oct", 2),
           _text("inactsec", e.disconn_time.split(":")[1], "oct", 2)),
        '<tr><td class="label">Terminal name</td><td>%s</td></tr>'
        % _text("termname", e.terminal_name, "wide"),
        "</table></fieldset>",
        '<p class="note">The console\'s card is normally set to connect '
        "mode C4 (always accept an incoming connection, manual connection on "
        "the serial side) with disconnect mode 80 (drop on DTR).</p>",
    ]
    return page(form("connset.htm", "".join(body)), "Connection Settings")


def email_page(e, saved=False):
    body = [
        "<h2>Email Settings</h2>",
        _saved(saved),
        '<table class="form">',
        '<tr><td class="label">Mail server</td><td>%s</td></tr>'
        % _octets("emsvrip", e.mail_server),
        '<tr><td class="label">SMTP port</td><td>%s</td></tr>'
        % _text("emport", e.smtp_port, "num", 6),
        '<tr><td class="label">Unit name</td><td>%s</td></tr>'
        % _text("emuname", e.unit_name, "wide"),
        '<tr><td class="label">Domain name</td><td>%s</td></tr>'
        % _text("emdnname", e.domain_name, "wide"),
        '<tr><td class="label">Recipient 1</td><td>%s</td></tr>'
        % _text("r1emaddr", e.recipient_1, "wide"),
        '<tr><td class="label">Recipient 2</td><td>%s</td></tr>'
        % _text("r2emaddr", e.recipient_2, "wide"),
        "</table>",
    ]
    return page(form("smtpset.htm", "".join(body)), "Email Settings")


def trigger_page(e, n, saved=False):
    t = e.trigger(n)
    body = [
        "<h2>Email Trigger %d</h2>" % (n + 1),
        _saved(saved),
        '<input type="hidden" name="t" value="%d">' % n,
        '<table class="form">',
        '<tr><td class="label">Serial trigger input</td><td>%s</td></tr>'
        % _yesno("trigseryn", t["serial"]),
        '<tr><td class="label">Channel</td><td>%s</td></tr>'
        % _select("trigchan", [(1, "1")], 1),
        '<tr><td class="label">Match data</td><td>%s %s</td></tr>'
        % (_text("trigdata1", "%02X" % t["match"][0], "hex", 2),
           _text("trigdata2", "%02X" % t["match"][1], "hex", 2)),
        '<tr><td class="label">Message</td><td>%s</td></tr>'
        % _text("trigmesg", t["message"], "wide"),
        '<tr><td class="label">Priority</td><td>%s</td></tr>'
        % _select("trigprio", [(3, "Low"), (1, "Urgent")], t["priority"]),
        '<tr><td class="label">Min. notification interval (s)</td>'
        "<td>%s</td></tr>" % _text("trignotif", t["notify"]),
        '<tr><td class="label">Re-notification interval (s)</td>'
        "<td>%s</td></tr>" % _text("trigrenotif", t["renotify"]),
        "</table>",
    ]
    return page(form("smtptrig.htm", "".join(body)),
                "Email Trigger %d" % (n + 1))


PIN_FUNCS = [(0, "General purpose I/O"), (1, "Link status"),
             (2, "RS-485 transmit enable")]


def gpio_page(e, saved=False):
    rows = ['<table class="form">'
            "<tr><td></td><td><b>Function</b></td><td><b>Direction</b></td>"
            "<td><b>Active level</b></td></tr>"]
    for p in range(R.NUM_PINS):
        func = e.recs.u8(7, p * 2) & 0x0F
        direc = e.recs.u8(7, p * 2 + 1) & 0x01
        level = (e.recs.u8(7, p * 2 + 1) >> 1) & 0x01
        rows.append(
            '<tr><td class="label">CP%d</td><td>%s</td><td>%s</td>'
            "<td>%s</td></tr>"
            % (p + 1, _select("p%dfunc" % p, PIN_FUNCS, func),
               _select("p%ddir" % p, [(0, "Input"), (1, "Output")], direc),
               _select("p%dlevel" % p, [(0, "Low"), (1, "High")], level)))
    rows.append("</table>")
    body = ["<h2>Configurable Pins</h2>", _saved(saved), "".join(rows),
            '<p class="note">The console does not use the configurable pins; '
            "they are here because the card has them.</p>"]
    return page(form("gpio.htm", "".join(body)), "Configurable Pins")


def apply_page(state):
    body = [
        "<h2>Apply Settings</h2>",
        "<p>The settings have been written and the unit is restarting.</p>",
        '<p class="warn">The connection will drop while it restarts. Wait a '
        "few seconds, then reload the page.</p>",
    ]
    return page("".join(body), "Apply Settings")


def defaults_page():
    body = [
        "<h2>Apply Defaults</h2>",
        "<p>Every setting has been returned to its default and the unit is "
        "restarting.</p>",
        "<p>The IP address, gateway and subnet mask were kept, so the unit "
        "stays reachable at the address it had.</p>",
        '<p class="warn">The connection will drop while it restarts.</p>',
    ]
    return page("".join(body), "Apply Defaults")


def confirm_defaults_page():
    body = [
        "<h2>Apply Defaults</h2>",
        "<p>This returns the serial, connection, email and expert settings "
        "to their defaults. The IP address, gateway and subnet mask are "
        "kept.</p>",
        '<form method="post" action="setup.cgi">'
        '<input type="hidden" name="def" value="1">'
        '<input type="hidden" name="loc" value="applydef.htm">'
        '<div class="btn"><input type="submit" value="Apply Defaults"></div>'
        "</form>",
    ]
    return page("".join(body), "Apply Defaults")


def disabled_page():
    return page("<h2>Setup is disabled</h2>"
                "<p>Web setup has been turned off on this unit. Use the "
                "telnet setup menu on port 9999 to turn it back on.</p>",
                "Setup disabled")


# ---------------------------------------------------------------------------
# the request handler


class _Handler(BaseHTTPRequestHandler):
    server_version = "Lantronix"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        log = getattr(self.server, "logfn", None)
        if log:
            log("-- XPort web: " + (fmt % args))

    @property
    def cfg(self):
        return self.server.config

    @property
    def state(self):
        return self.server.state

    def _send(self, body, status=200, ctype="text/html"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (OSError, ValueError):
            pass

    def _not_found(self):
        # The card answers a missing page with this exact body.
        self._send(b"ERROR 404\r\n", 404, "text/plain")

    def _unauthorized(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Basic")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _authorised(self):
        """Basic auth against the telnet/web setup password.

        The card ships with no password, and then any credentials -- including
        the empty pair a browser sends first -- are accepted. Once a password
        is set it is checked, and the user name is ignored, as on the card.
        """
        want = self.cfg.telnet_password or ""
        if not want:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:]).decode("latin-1")
        except (ValueError, TypeError):
            return False
        _, _, given = raw.partition(":")
        return given == want

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        self._route(self._query())

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("latin-1") if length else ""
        fields = parse_qs(raw, keep_blank_values=True)
        fields.update(self._query())
        self._route(fields, post=True)

    def _query(self):
        return parse_qs(urlparse(self.path).query, keep_blank_values=True)

    def _route(self, fields, post=False):
        if not self.cfg.security.get("web_server", True):
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/index.htm"):
            # The card's root is a redirect into the secure tree.
            self._send('<html><head><meta http-equiv="refresh" '
                       'content="0; URL=secure/ltx_conf.htm"></head>'
                       "<body></body></html>")
            return
        if not path.startswith("/secure/"):
            self._not_found()
            return
        name = path[len("/secure/"):]

        if name in ("setuprec.dtd", "unitinfo.dtd"):
            self._send(DTDS[name], 200, "text/plain")
            return
        if not self._authorised():
            self._unauthorized()
            return
        if name == "unitinfo.xml":
            self._send(self._unitinfo(), 200, "text/xml")
            return
        if name == "setuprec.xml":
            if not self.cfg.security.get("web_setup", True):
                self._send(b"", 200, "text/xml")
                return
            self._send(self.cfg.recs.to_xml(), 200, "text/xml")
            return
        if not self.cfg.security.get("web_setup", True):
            self._send(disabled_page())
            return
        if name == "ltx_conf.htm":
            self._send(frame_shell())
            return
        if name == "banner.htm":
            self._send(banner_page(self.cfg))
            return
        if name == "menu.htm":
            self._send(menu_page())
            return
        if name in ("welcome.htm", ""):
            self._send(welcome_page(self.cfg))
            return
        if name == "setup.cgi":
            self._setup_cgi(fields)
            return
        if name == "subdef.htm":
            self._send(confirm_defaults_page())
            return
        if name == "apply.htm":
            self.state.commit()
            self._send(apply_page(self.state))
            return
        if name in PAGES:
            self._page(name, fields, post)
            return
        self._not_found()

    def _unitinfo(self):
        from . import xport
        return ('<?xml version="1.0"?>\n'
                '<!DOCTYPE UNITINFO SYSTEM "unitinfo.dtd">\n'
                "<UNITINFO>\n<FW>%s</FW>\n<MAC>%s</MAC>\n</UNITINFO>\n"
                % (xport.FIRMWARE_VERSION,
                   xport.mac_hex(self.cfg.mac, ":")))

    def _setup_cgi(self, fields):
        """The endpoint the card's own pages post their records to.

        Kept so a tool that drives the card by posting base64 records still
        works: `def=1` applies defaults, otherwise each `recN` field replaces
        that record and the unit reboots.
        """
        if fields.get("def", ["0"])[0] == "1":
            self.state.defaults()
            self._send(defaults_page())
            return
        changed = self.cfg.recs.load_posted(
            {k: v[0] for k, v in fields.items()})
        if changed:
            self.cfg.save()
            self.state.discard()
            self.state.rebooting_until = time.monotonic() + 3.0
        self._send(apply_page(self.state))

    def _page(self, name, fields, post):
        render, store = PAGES[name]
        e = self.state.edit()
        saved = False
        if post:
            store(e, fields)
            saved = True
        if name == "smtptrig.htm":
            n = _int_from(fields, "t", 0, 0, 2)
            self._send(render(e, n, saved))
            return
        self._send(render(e, saved))


# -- the per-page store functions -------------------------------------------


def store_net(e, f):
    if f.get("dynip", ["0"])[0] == "1":
        e.ip = "0.0.0.0"
    else:
        e.ip = _addr_from(f, "ipaddr", e.ip)
        e.gateway = _addr_from(f, "ipgw", e.gateway)
        e.netmask_bits = R.host_bits_of(_addr_from(f, "ipmask", e.netmask))
    if "autoneg" in f:
        e.eth_mode = 0
    else:
        fast = f.get("speed", ["100"])[0] == "100"
        full = f.get("duplex", ["full"])[0] == "full"
        e.eth_mode = (4 if full else 2) if fast else (3 if full else 1)


def store_server(e, f):
    pw = _str_from(f, "telpasswd", "")
    if pw and pw == _str_from(f, "telrepasswd", ""):
        e.telnet_password = pw[:4]
    e.arp_timeout = _int_from(f, "arpcato", e.arp_timeout, 1, 600)
    e.keepalive = _int_from(f, "tcpkeep", e.keepalive, 0, 65)
    e.monitor_mode = _flag_from(f, "disnmon", e.monitor_mode)
    e.cpu_performance = _int_from(f, "cpuperf", e.cpu_performance, 0, 2)
    e.http_port = _int_from(f, "httpport", e.http_port, 1, 65535)
    e.mtu = _int_from(f, "mtusize", e.mtu, 512, 1400)
    e.recs.le16(3, R.CFGPRT_OFF,
                _int_from(f, "cfgport", e.recs.le16(3, R.CFGPRT_OFF),
                          1, 65535))


def store_hostlist(e, f):
    e.hostlist_retry_count = _int_from(f, "hlretrycnt",
                                       e.hostlist_retry_count, 1, 15)
    e.hostlist_retry_timeout = _int_from(f, "hlretryto",
                                         e.hostlist_retry_timeout, 10, 255)
    for i in range(1, R.NUM_HLIST + 1):
        cur_ip, cur_port = e.hostlist()[i - 1]
        e.set_hostlist(i - 1, _addr_from(f, "h%dip" % i, cur_ip),
                       _int_from(f, "h%dport" % i, cur_port, 0, 65535))


def store_serial(e, f):
    proto = _int_from(f, "serproto", 0, 0, 3)
    bits = _int_from(f, "data", 8, 7, 8)
    par = _int_from(f, "parity", 0, 0, 3)
    stop = _int_from(f, "stop", 1, 1, 2)
    e.if_mode = _if_byte(proto, bits, par, stop)
    e.flow = _int_from(f, "flow", e.flow, 0, 5)
    e.baudrate = _int_from(f, "baud", e.baudrate)
    e.send_char_1 = _hex_from(f, "pctrlmdata1", e.send_char_1)
    e.send_char_2 = _hex_from(f, "pctrlmdata2", e.send_char_2)
    e.flush_mode = _hex_from(f, "flidisc", e.flush_mode)


def store_conn(e, f):
    e.connect_mode = _hex_from(f, "actvconn", e.connect_mode)
    if "accptin" in f:
        e.connect_mode = ((e.connect_mode & 0x3F)
                          | (_int_from(f, "accptin", 0xC0) & 0xC0))
    if "hlistyn" in f:
        want = _flag_from(f, "hlistyn", e.uses_hostlist())
        e.connect_mode = ((e.connect_mode | R.CMODE_HOSTLIST) if want
                          else (e.connect_mode & ~R.CMODE_HOSTLIST))
    e.auto_increment = _flag_from(f, "ainclport", e.auto_increment)
    e.port = _int_from(f, "lport", e.port, 1, 65535)
    e.remote_port = _int_from(f, "rport", e.remote_port, 0, 65535)
    e.remote_ip = _addr_from(f, "rhost", e.remote_ip)
    e.disconn_mode = _hex_from(f, "dtrdisc", e.disconn_mode)
    mm = _int_from(f, "inactmin", 0, 0, 99)
    ss = _int_from(f, "inactsec", 0, 0, 59)
    e.disconn_time = "%02d:%02d" % (mm, ss)
    e.terminal_name = _str_from(f, "termname", e.terminal_name)[:16]


def store_email(e, f):
    e.mail_server = _addr_from(f, "emsvrip", e.mail_server)
    e.smtp_port = _int_from(f, "emport", e.smtp_port, 1, 65535)
    e.unit_name = _str_from(f, "emuname", e.unit_name)[:24]
    e.domain_name = _str_from(f, "emdnname", e.domain_name)[:24]
    e.recipient_1 = _str_from(f, "r1emaddr", e.recipient_1)[:49]
    e.recipient_2 = _str_from(f, "r2emaddr", e.recipient_2)[:49]


def store_trigger(e, f):
    n = _int_from(f, "t", 0, 0, 2)
    base = R.TRIG_OFF[n]
    e.set_trigger(n, serial=_flag_from(f, "trigseryn",
                                       e.trigger(n)["serial"]))
    e.recs.u8(6, base + R.TMASK_OFF,
              _hex_from(f, "trigdata1", e.recs.u8(6, base + R.TMASK_OFF)))
    e.recs.u8(6, base + R.TCMP_OFF,
              _hex_from(f, "trigdata2", e.recs.u8(6, base + R.TCMP_OFF)))
    e.set_trigger(n, message=_str_from(f, "trigmesg", "")[:24])
    e.recs.u8(6, base + R.TPRIO_OFF,
              _int_from(f, "trigprio", e.recs.u8(6, base + R.TPRIO_OFF)))
    e.set_trigger(n, notify=_int_from(f, "trignotif",
                                      e.trigger(n)["notify"], 0, 65535))
    e.set_trigger(n, renotify=_int_from(f, "trigrenotif",
                                        e.trigger(n)["renotify"], 0, 65535))


def store_gpio(e, f):
    for p in range(R.NUM_PINS):
        e.recs.u8(7, p * 2,
                  _int_from(f, "p%dfunc" % p, e.recs.u8(7, p * 2), 0, 15))
        direc = _int_from(f, "p%ddir" % p, 0, 0, 1)
        level = _int_from(f, "p%dlevel" % p, 0, 0, 1)
        e.recs.u8(7, p * 2 + 1, (level << 1) | direc)


PAGES = {
    "netset.htm": (net_page, store_net),
    "servset.htm": (server_page, store_server),
    "hlist.htm": (hostlist_page, store_hostlist),
    "serial.htm": (serial_page, store_serial),
    "connset.htm": (conn_page, store_conn),
    "smtpset.htm": (email_page, store_email),
    "smtptrig.htm": (trigger_page, store_trigger),
    "gpio.htm": (gpio_page, store_gpio),
}

DTDS = {
    "setuprec.dtd": (b"<!ELEMENT SETUPREC (RECORD*,SCR)>\r\n"
                     b"<!ELEMENT RECORD (NUM,DATA)>\r\n"
                     b"<!ELEMENT NUM (#PCDATA)>\r\n"
                     b"<!ELEMENT DATA (#PCDATA)>\r\n"
                     b"<!ELEMENT SCR (#PCDATA)>\r\n"),
    "unitinfo.dtd": (b"<!ELEMENT UNITINFO (FW,MAC)>\r\n"
                     b"<!ELEMENT FW (#PCDATA)>\r\n"
                     b"<!ELEMENT MAC (#PCDATA)>\r\n"),
}


class _Server(HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, config, logfn=None):
        HTTPServer.__init__(self, addr, _Handler)
        self.config = config
        self.state = WebState(config)
        self.logfn = logfn

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle,
                             args=(request, client_address), daemon=True)
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except OSError:
            pass
        finally:
            try:
                self.shutdown_request(request)
            except OSError:
                pass


def serve_web(config, host="0.0.0.0", log=None, powered=None, port=None,
              on_server=None):
    """Serve the web manager, on the card's configured HTTP port.

    `on_server` is handed the server, so the card's network supervisor can
    shut it down and move it when the card is reprogrammed.
    """
    powered = powered or (lambda: True)
    port = port or config.http_port
    try:
        srv = _Server((host, port), config, log)
    except OSError as e:
        if log:
            log("-- XPort web manager not listening on %d: %s" % (port, e))
        return None
    if on_server:
        on_server(srv)
    if log:
        log("-- XPort web manager on tcp/%d" % port)
    try:
        srv.serve_forever(poll_interval=0.4)
    except OSError:
        pass
    return srv


__all__ = ["serve_web", "WebState", "PAGES"]
