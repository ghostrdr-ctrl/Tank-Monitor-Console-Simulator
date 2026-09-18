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
"""Capture a real TCP/IP Interface Module, so the emulator can be diffed on it.

    python tools/capture_xport.py 172.30.9.14 --out tests/xport_capture

Writes three things under the output directory:

    menu/       every chunk of bytes the setup menu sent, in order
    web/        the web manager's files, verbatim, with their headers
    udp/        the 77FEh replies

`tests/test_xport_capture.py` replays the menu capture through the emulator
and compares it byte for byte. The capture is not committed: it is a device's
own output and it takes a card to make, the same rule the manuals under
`reference/` follow. This script remakes it from any card on a bench.

Two safety rules, both deliberate:

  * The menu walk answers every prompt with a bare Enter, which keeps the
    value shown, and leaves by "8 Exit without save". The card is read, never
    written.
  * Only four-byte frames go to udp/30718. A longer one makes this firmware
    reply with an internal network buffer and corrupt its own working
    configuration -- see reference/lantronix_xport_capture.md section 4. That
    is how it was found. Do not widen this sweep.
"""
import argparse
import base64
import os
import re
import socket
import sys

WEB_PAGES = [
    "/", "/secure/ltx_conf.htm", "/secure/banner.htm", "/secure/menu.htm",
    "/secure/welcome.htm", "/secure/netset.htm", "/secure/servset.htm",
    "/secure/hlist.htm", "/secure/serial.htm", "/secure/connset.htm",
    "/secure/smtpset.htm", "/secure/smtptrig.htm", "/secure/gpio.htm",
    "/secure/dissetup.htm", "/secure/subdef.htm",
    "/secure/setuprec.xml", "/secure/unitinfo.xml",
    "/secure/setuprec.dtd", "/secure/unitinfo.dtd",
]

# The only frames a card of this generation may safely be sent.
#
# E2 is here because it was missed the first time: the first sweep covered
# F0-FF only, E2 was found later from what DeviceInstaller sent, and by then
# the bench card's configuration had been corrupted -- so every E2 answer on
# record was the erased-flash one from a broken card. Capture it from a
# healthy card or the emulator will copy the broken answer.
UDP_OPCODES = (0xE2, 0xF4, 0xF6, 0xF8, 0xFA)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


# ---------------------------------------------------------------------------
# the setup menu


def capture_menu(host, out, quiet=False):
    """Walk the whole menu, keeping every value, and record what came back."""
    seq = [0]

    def drain(s, wait):
        s.settimeout(wait)
        buf = b""
        while True:
            try:
                d = s.recv(4096)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
        return buf

    def grab(s, label, wait=1.5):
        data = drain(s, wait)
        seq[0] += 1
        write(os.path.join(out, "menu", "%02d_%s.bin" % (seq[0], label)),
              data)
        if not quiet:
            print("  %-26s %5d bytes" % (label, len(data)))
        return data

    try:
        s = socket.create_connection((host, 9999), 10)
    except OSError as e:
        print("  setup menu: %s" % e)
        return False
    try:
        grab(s, "banner", 2.0)
        s.sendall(b"\r\n")
        grab(s, "dump_and_menu", 3.0)
        for choice in ("0", "1", "3", "5", "6"):
            s.sendall(choice.encode() + b"\r\n")
            out_bytes = grab(s, "menu%s_open" % choice, 2.0)
            for i in range(45):
                if b"Your choice ?" in out_bytes:
                    break
                s.sendall(b"\r\n")
                out_bytes = grab(s, "menu%s_step%02d" % (choice, i + 1), 1.0)
                if not out_bytes:
                    break
        # 8, never 9: leave without writing anything.
        s.sendall(b"8\r\n")
        grab(s, "exit", 2.0)
    finally:
        try:
            s.close()
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# the web manager


def capture_web(host, out, password="", quiet=False):
    auth = base64.b64encode((":" + password).encode()).decode()
    got = 0
    for path in WEB_PAGES:
        req = ("GET %s HTTP/1.0\r\nHost: %s\r\n"
               "Authorization: Basic %s\r\nConnection: close\r\n\r\n"
               % (path, host, auth))
        try:
            s = socket.create_connection((host, 80), 10)
            s.settimeout(10)
            s.sendall(req.encode("latin-1"))
            buf = b""
            while True:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
            s.close()
        except OSError as e:
            if not quiet:
                print("  %-30s %s" % (path, e))
            continue
        head, _, body = buf.partition(b"\r\n\r\n")
        code = 0
        m = re.search(r"\s(\d{3})", head.decode("latin-1", "replace"))
        if m:
            code = int(m.group(1))
        rel = path.lstrip("/") or "index.html"
        write(os.path.join(out, "web", rel.replace("/", os.sep)), body)
        write(os.path.join(out, "web",
                           rel.replace("/", os.sep) + ".headers"), head)
        if code == 200:
            got += 1
        if not quiet:
            print("  %-30s %3d  %5d bytes" % (path, code, len(body)))
    return got


# ---------------------------------------------------------------------------
# 77FEh


def capture_udp(host, out, quiet=False):
    """The 77FEh replies, over UDP and TCP.

    Both matter: a tool finds a card over the UDP broadcast and reads its
    configuration over a TCP connection to the same port number.
    """
    got = 0
    for op in UDP_OPCODES:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        try:
            s.sendto(bytes([0, 0, 0, op]), (host, 30718))
            data, _addr = s.recvfrom(4096)
        except OSError:
            data = b""
        finally:
            s.close()
        write(os.path.join(out, "udp", "%02X.bin" % op), data)
        if data:
            got += 1
        if not quiet:
            print("  udp opcode %02X                  %5d bytes"
                  % (op, len(data)))

    # The same opcodes over TCP, on the same port number.
    for op in UDP_OPCODES:
        data = b""
        try:
            s = socket.create_connection((host, 30718), 5)
            s.settimeout(2.0)
            s.sendall(bytes([0, 0, 0, op]))
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
            s.close()
        except OSError:
            pass
        write(os.path.join(out, "tcp", "%02X.bin" % op), data)
        if not quiet:
            print("  tcp opcode %02X                  %5d bytes"
                  % (op, len(data)))
    return got


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host", help="the card's address")
    ap.add_argument("--out", default="tests/xport_capture",
                    help="where to write the capture "
                         "(default tests/xport_capture)")
    ap.add_argument("--password", default="",
                    help="the card's setup password, if one is set")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    print("Capturing the card at %s into %s" % (a.host, a.out))
    print("The card is read, never written: every prompt is answered with")
    print("Enter and the walk leaves by 8, Exit without save.")
    print()
    print("setup menu, tcp/9999:")
    capture_menu(a.host, a.out, a.quiet)
    print()
    print("web manager, tcp/80:")
    capture_web(a.host, a.out, a.password, a.quiet)
    print()
    print("discovery, udp/30718:")
    capture_udp(a.host, a.out, a.quiet)
    print()
    print("Done. `python -m pytest tests/test_xport_capture.py` will now diff")
    print("the emulator against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
