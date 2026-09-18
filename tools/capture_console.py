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
"""Capture a real TLS-350's answers, so the emulation can be diffed on them.

    python tools/capture_console.py 172.30.9.14 --port 10001 \
        --out tests/console_capture

The console is reached through its TCP/IP card's serial tunnel, which is what
a technician's laptop talks to.

Inquiry codes only. Every command this sends begins `I` or `i`, which
read. There is no `S` code anywhere in this file, and none should ever be
added: a Set writes the console's programming, and a bench console is
somebody's equipment.

Writes:
    <out>/raw/<CODE>.bin        every display-format answer, verbatim
    <out>/computer/<code>.bin   the computer-format answer for a chosen few
    <out>/census.tsv            code, length, and whether it was supported

`tests/test_console_capture.py` replays these against the emulator. The
capture is not committed -- it is the console's own output and it takes a
console to make -- the same rule the manuals under `reference/` follow.
"""
import argparse
import os
import socket
import sys
import time

SOH = b"\x01"
ETX = b"\x03"
ERROR_MARK = b"9999FF"

# The five undocumented "@" reports the Troubleshooting Guide names, in both
# formats, because the pair settles whether the case is honoured.
AT_CODES = ["I@A002", "I@A400", "I@A900", "I@B600", "I@B900"]

# A handful worth having in computer format, to check the checksum.
COMPUTER = ["i90200", "i90100", "i20100", "i60100", "i@a900"]


def read_reply(s, wait):
    buf = b""
    end = time.time() + wait
    while time.time() < end:
        try:
            d = s.recv(8192)
        except socket.timeout:
            break
        if not d:
            raise ConnectionError("closed")
        buf += d
        if buf.endswith(ETX):
            break
    return buf


class Link:
    """One tunnel connection, reopened if the card drops it."""

    def __init__(self, host, port, wait):
        self.host, self.port, self.wait = host, port, wait
        self.s = None
        self.open()

    def open(self):
        self.s = socket.create_connection((self.host, self.port), 8)
        self.s.settimeout(min(2.5, self.wait))
        self.drain()

    def drain(self):
        """Throw away anything still in flight.

        A reply the console was still sending arrives at the front of the
        next one, and then every length after it belongs to the code before.
        Reconnecting is not enough on its own -- the console keeps sending --
        so read the line empty before asking anything.
        """
        self.s.settimeout(0.4)
        try:
            while True:
                if not self.s.recv(8192):
                    break
        except OSError:
            pass
        self.s.settimeout(min(2.5, self.wait))

    def ask(self, code, fresh=False):
        if fresh:
            # A reply that arrived late lands at the front of the next one
            # and every length after it is wrong. For the short sets, take a
            # new connection each time rather than trust the stream.
            try:
                self.s.close()
            except OSError:
                pass
            try:
                self.open()
            except OSError:
                return b""
        for attempt in (0, 1):
            try:
                self.drain()
                self.s.sendall(SOH + code.encode())
                return read_reply(self.s, self.wait)
            except (OSError, ConnectionError):
                try:
                    self.s.close()
                except OSError:
                    pass
                if attempt:
                    return b""
                time.sleep(1.0)
                try:
                    self.open()
                except OSError:
                    return b""
        return b""


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host", help="the card's address")
    ap.add_argument("--port", type=int, default=10001,
                    help="the card's tunnel port (default 10001)")
    ap.add_argument("--out", default="tests/console_capture")
    ap.add_argument("--wait", type=float, default=3.0,
                    help="seconds to wait for a reply")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    print("Reading the console at %s:%d" % (a.host, a.port))
    print("Inquiry codes only; nothing here writes.")
    print()
    link = Link(a.host, a.port, a.wait)
    rows = []
    supported = 0

    print("every inquiry code, I10000 to I99900:")
    for n in range(100, 1000):
        code = "I%03d00" % n
        path = os.path.join(a.out, "raw", code + ".bin")
        if os.path.exists(path):
            continue
        data = link.ask(code)
        write(path, data)
        ok = bool(data) and ERROR_MARK not in data and len(data) >= 16
        if ok:
            supported += 1
            if not a.quiet:
                text = data.decode("latin-1", "replace")
                parts = [p for p in text.split("\r\n") if p.strip()]
                title = parts[2].strip() if len(parts) > 2 else ""
                print("  %-8s %5d  %s" % (code, len(data), title[:56]))
        rows.append((code, len(data), "yes" if ok else "no"))

    print()
    print("the undocumented @ reports:")
    for code in AT_CODES:
        for form, kind in ((code, "display"), (code.lower(), "computer")):
            data = link.ask(form, fresh=True)
            # Named by FORMAT, not by the code as typed: on a case-insensitive
            # filesystem I@A900 and i@a900 are one filename, and the second
            # capture quietly overwrites the first.
            name = "%s_%s.bin" % (kind, code.replace("@", "at"))
            write(os.path.join(a.out, "at", name), data)
            print("  %-8s %-9s %5d bytes" % (form, kind, len(data)))

    print()
    print("computer format, for the checksum:")
    for code in COMPUTER:
        data = link.ask(code, fresh=True)
        write(os.path.join(a.out, "computer",
                           code.replace("@", "at") + ".bin"), data)
        print("  %-8s %5d bytes" % (code, len(data)))

    with open(os.path.join(a.out, "census.tsv"), "w", encoding="utf-8") as f:
        for code, n, ok in rows:
            f.write("%s\t%d\t%s\n" % (code, n, ok))

    print()
    print("answered with a report: %d of %d" % (supported, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
