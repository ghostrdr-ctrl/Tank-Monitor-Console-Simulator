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
"""What the listeners may do when the people reaching them are strangers.

Every port this simulator opens was written for a bench: a trainee on the
same machine, or a technician on the same bench network, driving a console
that is meant to be driven. The whole point of the emulation is that it
answers everything a real card answers, and that it keeps what it is told --
an address programmed into the card survives a restart because a real card's
does.

Both of those are wrong on a public address, and they are wrong in the same
way: they were written assuming the other end means well.

  * `xport.XPortConfig.save()` is reached from an unauthenticated HTTP POST
    (`xportweb._setup_cgi`) and from the port-1 assignment knock
    (`xportnet.CardNetwork._assign_loop`). A stranger can move the card,
    reboot it, and write a file on the host.
  * `xport.default_mac()` seeds the card's identity from `socket.gethostname()`,
    so the emulated MAC carries a hash of the operator's machine name and two
    deployments by one person correlate to each other.
  * `wire._session` had no timeout at all, and every listener is
    thread-per-connection with no ceiling. A few thousand idle connections
    is the host.

None of that is a defect on a bench. All of it is a defect on the internet.
So the decision is made once, explicitly, and the listeners ask.

Three levels, because the real question has three answers
---------------------------------------------------------
`BENCH` is what this program has always done and remains the default: bound
to loopback, everything writable, nothing captured. `LAN` is the same
console on a network you trust -- it binds wide and it captures, but a card
programmed on it still keeps its address, because that is the lesson the
training is teaching. `EXPOSED` is a public address, and it is the only one
that changes what the console will do:

  * writes are frozen, process wide (`writes_frozen()`). Nothing the network
    says reaches the disk, so the card cannot be moved, the state file cannot
    be rewritten, and a restart is always a clean restart.
  * the card's identity stops coming from the host name.
  * connections are capped in total and per source, rated per source, and
    given a short idle timeout.
  * every byte in and every byte out is captured with its source.

Why the freeze is a process-wide latch and not a parameter
-----------------------------------------------------------
Because it is a property of the process, not of a call. There is no coherent
state in which one listener is exposed and another is not -- they are all
faces of one card at one address, and `CardNetwork` brings them up together.
Threading a policy object through `XPortConfig`, `Console`, `WebState` and
every page store function would put the guarantee in thirty places and make
it impossible to audit. Here it is two functions and one flag, and
`test_exposed.py` can assert the whole of it.

What this is NOT
----------------
It is not a sandbox. A policy object does not make a 60,000 line Python
program safe to run as root on a box that matters. It closes the holes this
program has that a honeypot must not have; it does not turn the CPython
process into a jail. The README says the same thing in the same words, and
says to run it in a container as an unprivileged user regardless.

GasPot, and what it got right
------------------------------
GasPot (Kyle Wilhoit and Stephen Hilt, Trend Micro) is the well known ATG
honeypot and the reason port 10001 is scanned at all. Nothing here is taken
from its code -- it is CC BY-NC-SA and this is GPL -- but three of its
decisions are the right ones and are matched deliberately:

  * a 30 second idle timeout and a bounded read, which is what this program
    lacked outright;
  * JSON lines carrying the source address and the command code, so the
    capture drops into an existing SIEM without a parser being written. The
    field names below follow that convention for the same reason;
  * a randomised response delay, which is the one that matters most and the
    one this simulator would never have thought of. A real console answers
    over RS-232 at 9600 baud through a serial-to-Ethernet bridge; a reply
    that comes back in 40 microseconds did not come from one. Answering
    instantly is a fingerprint, and it is the fingerprint that identifies a
    software emulation as a software emulation no matter how perfect its
    bytes are. `Policy.delay()` is that jitter.

The fourth GasPot lesson is one it learned the hard way and is recorded in
the README rather than here: its own default station names shipped
unchanged, and "H0BBIT LANE" and the rest became a published signature that
identified a GasPot on sight. A default site is a fingerprint. This
simulator ships a demo site, and on a public address it says so.
"""
import json
import os
import random
import threading
import time


BENCH = "bench"
LAN = "lan"
EXPOSED = "exposed"
LEVELS = (BENCH, LAN, EXPOSED)

LABELS = {
    BENCH: "Bench -- loopback only",
    LAN: "Trusted network",
    EXPOSED: "Exposed (honeypot)",
}

# What each level means in one line, for the dropdown's own tooltip and for
# the log line written when it changes.
BLURB = {
    BENCH: ("Bound to this machine only. Everything is writable and nothing "
            "is captured. This is the default and it is what training uses."),
    LAN: ("Bound to every address, so the bench network can reach it. The "
          "card still keeps what it is programmed with. Traffic is "
          "captured. Do not use this on an address the internet can reach."),
    EXPOSED: ("A public address. Nothing the network says is written to "
              "disk, the card's identity stops coming from this machine's "
              "name, connections are capped and rated, and every byte is "
              "captured. Read the warning before choosing it."),
}


# ---------------------------------------------------------------------------
# the process-wide write freeze


_frozen = False
_freeze_lock = threading.Lock()
_refusals = []          # what tried to write while frozen, for the capture


def freeze_writes(on=True):
    """Stop or allow every persistent write this program makes."""
    global _frozen
    with _freeze_lock:
        _frozen = bool(on)


def writes_frozen():
    """True when no save anywhere in this process may touch the disk.

    Consulted by `xport.XPortConfig.save`, `console.Console.save` and
    `console.Console.archive`. Anything added later that persists must
    consult it too, and `test_exposed.py` greps for `atomicfile.replacing`
    to make sure a new one is not forgotten.
    """
    return _frozen


def refused(what):
    """Record a write that the freeze turned away."""
    if not _frozen:
        return False
    with _freeze_lock:
        _refusals.append((time.time(), what))
        del _refusals[:-200]
    return True


def refusals():
    with _freeze_lock:
        return list(_refusals)


# ---------------------------------------------------------------------------
# the policy


class Policy:
    """The limits one exposure level puts on every listener.

    The numbers are deliberately modest. A real ATG serves one polling host
    and an occasional technician; a card fielding forty simultaneous
    connections is not being used, it is being scanned, and there is nothing
    a honeypot learns from the four hundredth connection of a flood that it
    did not learn from the fourth.
    """

    # total, per source, connections a minute per source, idle seconds
    LIMITS = {
        BENCH:   (None, None, None, None),
        LAN:     (64, 16, 240, 300.0),
        EXPOSED: (48, 6, 30, 30.0),
    }

    # The serial round trip a real card cannot beat. A TLS-350 answers over
    # RS-232 at 9600 baud -- a hundred byte reply is 104 ms of line time
    # before the console has thought about it -- through a bridge that adds
    # its own. GasPot uses 0.15 to 0.75 s and that bracket is a reasonable
    # read of the real thing, so it is the bracket used here.
    DELAY = {BENCH: None, LAN: None, EXPOSED: (0.15, 0.75)}

    def __init__(self, level=BENCH, capture=None, seed=None):
        if level not in LEVELS:
            raise ValueError("unknown exposure level: %r" % (level,))
        self.level = level
        self.capture = capture
        total, per_ip, rate, idle = self.LIMITS[level]
        self.max_total = total
        self.max_per_ip = per_ip
        self.rate_per_min = rate
        self.idle_timeout = idle
        self._delay = self.DELAY[level]
        # Its own generator, seeded, because `traffic.py` is documented as
        # the only module in the package that draws a random number and the
        # test suite depends on the console being deterministic. A response
        # delay drawn from `random` directly would pull the global stream
        # out from under it.
        self._rng = random.Random(seed if seed is not None
                                  else os.urandom(8))
        self._rng_lock = threading.Lock()

    # -- the properties the listeners ask about ---------------------------

    @property
    def exposed(self):
        return self.level == EXPOSED

    @property
    def readonly(self):
        return self.level == EXPOSED

    @property
    def capturing(self):
        return self.level in (LAN, EXPOSED)

    @property
    def stable_identity(self):
        """False when the card's MAC must not come from the host name."""
        return self.level != EXPOSED

    def delay(self):
        """How long to wait before answering, in seconds. 0 on a bench."""
        if not self._delay:
            return 0.0
        lo, hi = self._delay
        with self._rng_lock:
            return self._rng.uniform(lo, hi)

    def sleep(self):
        d = self.delay()
        if d:
            time.sleep(d)

    def describe(self):
        return "%s -- %s" % (LABELS[self.level], BLURB[self.level])


# ---------------------------------------------------------------------------
# admission control


class Gate:
    """Who may hold a connection, and how many at once.

    Three limits, because they fail differently. The TOTAL is the host's
    thread count and it is the one that actually protects the machine. The
    PER SOURCE stops one address taking all of the total. The RATE stops an
    address that connects, is counted, disconnects and connects again --
    which the per-source cap alone never sees, because at no instant is it
    holding more than one.

    A refused connection is recorded, because a refusal is a finding: an
    address hitting the rate limit is doing something a polling host never
    does, and that is the first thing worth knowing about it.
    """

    def __init__(self, policy, capture=None, clock=time.monotonic):
        self.policy = policy
        self.capture = capture
        self._clock = clock
        self._lock = threading.Lock()
        self._held = {}          # ip -> how many open now
        self._recent = {}        # ip -> [connect times inside the window]
        # Datagrams are counted SEPARATELY from connections, and the
        # separation is the point rather than tidiness.
        #
        # A TCP connection proves the source address: the handshake came
        # back. A UDP datagram proves nothing -- the source is whatever the
        # sender wrote. Sharing one window between them let an attacker
        # spend a victim's CONNECTION budget by sending datagrams that
        # merely claimed to be from the victim, with no handshake and
        # nothing to stop them. Measured before the split: 12 of an
        # address's 30 connections a minute, gone, for any address the
        # attacker cared to name.
        #
        # So a spoofable channel never consumes a non-spoofable one's
        # allowance. The datagram limiter protects third parties from
        # reflection; the connection limiter protects this host; neither
        # can be used to attack the other.
        self._datagrams = {}     # ip -> [datagram times inside the window]
        self._total = 0
        self.refused_total = 0

    WINDOW = 60.0

    def admit(self, ip, port=None):
        """True if this connection may proceed. Call `release` when done."""
        p = self.policy
        if p.max_total is None:
            return True
        now = self._clock()
        with self._lock:
            if p.rate_per_min is not None:
                seen = [t for t in self._recent.get(ip, ())
                        if now - t < self.WINDOW]
                if len(seen) >= p.rate_per_min:
                    self._recent[ip] = seen
                    return self._refuse(ip, port, "rate", len(seen))
                seen.append(now)
                self._recent[ip] = seen
                # Addresses that have gone quiet stop costing memory. Without
                # this the dictionary is an unbounded record of every source
                # that ever connected, which on a public address is the leak
                # that eventually ends the process.
                if len(self._recent) > 4096:
                    self._sweep(now)
            if self._total >= p.max_total:
                return self._refuse(ip, port, "total", self._total)
            if p.max_per_ip is not None:
                if self._held.get(ip, 0) >= p.max_per_ip:
                    return self._refuse(ip, port, "per-source",
                                        self._held.get(ip, 0))
            self._held[ip] = self._held.get(ip, 0) + 1
            self._total += 1
        return True

    def _refuse(self, ip, port, why, count):
        self.refused_total += 1
        if self.capture:
            self.capture.event("refused", peer=ip, port=port,
                               note="%s limit (%s)" % (why, count))
        return False

    def _sweep(self, now):
        for ip in list(self._recent):
            self._recent[ip] = [t for t in self._recent[ip]
                                if now - t < self.WINDOW]
            if not self._recent[ip] and not self._held.get(ip):
                del self._recent[ip]

    def release(self, ip):
        """Give a slot back. Deliberately NOT conditional on the policy.

        This used to return early when `max_total` was None, which is true
        of a bench policy -- and the bench's own dropdown mutates the policy
        object IN PLACE while this gate holds live counts. Going EXPOSED ->
        Bench with connections open meant none of them were ever released:
        `_total` and `_held` kept their dead entries, and raising the level
        again started from a stale count that never drained. Enough of that
        residue and the gate refuses everyone for the life of the process,
        the operator included.

        Releasing unconditionally is safe in the other direction too: a
        connection admitted under a bench policy was never counted, and the
        floor below keeps that from going negative.
        """
        with self._lock:
            n = self._held.get(ip, 0) - 1
            if n > 0:
                self._held[ip] = n
            else:
                self._held.pop(ip, None)
            self._total = max(0, self._total - 1)

    def held(self):
        with self._lock:
            return self._total, dict(self._held)

    def session(self, ip):
        """`with gate.session(ip) as ok:` -- see `_Admitted`."""
        return _Admitted(self, ip)

    # A datagram responder answers far more often than a person discovers a
    # card, and one legitimate DeviceInstaller sweep is a handful of probes.
    # This is per source per minute.
    DATAGRAM_RATE = 12

    def datagram_ok(self, ip):
        """May we answer one more datagram from `ip`?

        The rest of this class counts CONNECTIONS -- admit one, hold it,
        release it. UDP has none of that, so the discovery responder on
        30718 sat outside every limit the gate applies, and it is the one
        listener where that matters most to somebody else:

        a four-byte probe draws a 124-byte reply (opcodes F8 and E0, the
        setup record), which is thirty-one times amplification, over a
        protocol with no handshake, so the source address can be forged.
        An exposed console was therefore a usable DDoS reflector -- and the
        victim of that is a third party who never touched this program.

        Answering at a bounded rate keeps the card discoverable the way a
        real one is while removing the reason to point a reflector at it.
        A refusal is recorded, because a source asking thousands of times a
        minute is a finding in itself.
        """
        if self.policy.max_total is None:
            return True                  # a bench card, on loopback
        now = self._clock()
        with self._lock:
            seen = [t for t in self._datagrams.get(ip, ())
                    if now - t < self.WINDOW]
            if len(seen) >= self.DATAGRAM_RATE:
                self._datagrams[ip] = seen
                self.refused_total += 1
                if self.capture:
                    self.capture.event("refused", peer=ip, proto="77fe/udp",
                                       note="datagram rate limit (%d/min)"
                                            % self.DATAGRAM_RATE)
                return False
            seen.append(now)
            self._datagrams[ip] = seen
            if len(self._datagrams) > 4096:
                # Its own sweep, over its own dictionary. A source that has
                # gone quiet stops costing memory; without this, a reflector
                # attack walking forged addresses is an unbounded record of
                # every address it ever claimed.
                for other in list(self._datagrams):
                    self._datagrams[other] = [
                        t for t in self._datagrams[other]
                        if now - t < self.WINDOW]
                    if not self._datagrams[other]:
                        del self._datagrams[other]
        return True


class _Admitted:
    """`with gate.session(ip):` -- release on every path out, including a
    raised one. The listeners each had their own try/finally and one of them
    would eventually not."""

    def __init__(self, gate, ip):
        self.gate = gate
        self.ip = ip
        self.ok = False

    def __enter__(self):
        self.ok = self.gate is None or self.gate.admit(self.ip)
        return self.ok

    def __exit__(self, *exc):
        if self.ok and self.gate is not None:
            self.gate.release(self.ip)
        return False


def session(gate, ip):
    """Admission for a listener that may have been handed no gate at all."""
    return _Admitted(gate, ip)


# ---------------------------------------------------------------------------
# the capture


class Capture:
    """Everything said over the wire and who said it.

    Two sinks, and they exist for two different readers. The RING is for the
    bench's Capture view -- the last few hundred exchanges, in memory, so a
    person watching a honeypot sees what is arriving as it arrives. The FILE
    is JSON lines, one object per exchange, appended and flushed, for the
    reader who is not watching: a SIEM, or the person going through a week
    of it afterwards.

    The field names follow GasPot's JSON log rather than inventing a
    vocabulary, so an existing Splunk or ELK pipeline built for one reads the
    other: `ts`, `src`, `sport`, `proto`, `dir`, `cmd`, `raw`.

    Bytes are stored hex-encoded, not decoded. The whole value of a honeypot
    capture is what was ACTUALLY sent, and a stranger's traffic is not UTF-8
    and is not meant to be: decoding it loses the bytes that made it
    interesting, and `errors="replace"` loses them silently. `text` carries a
    printable rendering alongside for the person reading the view, and it is
    derived from the hex, never the other way round.
    """

    RING = 500

    def __init__(self, path=None, ring=None, on_record=None, enabled=True):
        # Whether anything is actually recorded. The listeners are handed
        # this object ONCE, when the card comes up, and they keep it for
        # the life of the card -- so an exposure changed from the bench's
        # dropdown cannot reach them by swapping the object out. It reaches
        # them by flipping this. See `Capture.enabled` and
        # `ui.SimApp._apply_exposure`.
        self.enabled = enabled
        self.path = path
        self.ring = ring or self.RING
        self.on_record = on_record
        self._lock = threading.Lock()
        self._rows = []
        self._fh = None
        self._opened = False
        self.dropped = 0
        self.total = 0
        self.by_peer = {}
        self.peers_untallied = 0
        if path and enabled:
            self._open()

    def enable(self, on, path=None):
        """Turn recording on or off, and open the file the first time.

        The file is not opened until something is going to be written to
        it: a bench session that never captures should not leave an empty
        capture file behind in the operator's data directory.
        """
        if path and path != self.path:
            self.close()
            self.path = path
            self._opened = False
        self.enabled = bool(on)
        if self.enabled and self.path and not self._opened:
            self._open()

    def _open(self):
        try:
            d = os.path.dirname(os.path.abspath(self.path))
            if d:
                os.makedirs(d, exist_ok=True)
            # Append, never truncate: a capture file is evidence and a
            # restart must not be able to destroy the last one.
            #
            # This is the one write the freeze deliberately does NOT stop.
            # It is the honeypot's whole product, it goes to a path the
            # operator named rather than one the network can steer, and it
            # is append-only.
            self._fh = open(self.path, "a", encoding="utf-8")
            self._opened = True
        except OSError:
            self._fh = None

    # -- writing ----------------------------------------------------------

    def record(self, direction, data, peer=None, port=None, proto="tunnel",
               cmd=None, note=None):
        """One direction of one exchange."""
        raw = bytes(data or b"")
        row = {
            "ts": time.time(),
            "src": peer,
            "sport": port,
            "proto": proto,
            "dir": direction,
            "cmd": cmd or command_of(raw),
            "bytes": len(raw),
            "raw": raw.hex(),
        }
        if note:
            row["note"] = note
        self._put(row)
        return row

    def event(self, kind, peer=None, port=None, proto="tunnel", note=None):
        """Something that happened to a connection rather than on it."""
        self._put({"ts": time.time(), "src": peer, "sport": port,
                   "proto": proto, "dir": kind, "note": note or "",
                   "bytes": 0, "raw": ""})

    # How many distinct sources the tally will name before it stops
    # growing. It is a tally for the view's "N sources", not a record --
    # the record is the capture file, which has every one of them. Left
    # unbounded this is a dictionary with an entry per address that ever
    # connected, which on a public address is a slow leak that ends the
    # process after a few weeks of being scanned.
    PEERS = 4096

    def _put(self, row):
        if not self.enabled:
            return
        with self._lock:
            self.total += 1
            src = row.get("src")
            if src:
                if src in self.by_peer:
                    self.by_peer[src] += 1
                elif len(self.by_peer) < self.PEERS:
                    self.by_peer[src] = 1
                else:
                    self.peers_untallied += 1
            self._rows.append(row)
            if len(self._rows) > self.ring:
                del self._rows[:-self.ring]
                self.dropped += 1
            fh = self._fh
        if fh:
            try:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                fh.flush()
            except (OSError, ValueError):
                pass
        if self.on_record:
            try:
                self.on_record(row)
            except Exception:       # noqa: BLE001  -- a view must not
                pass                # be able to kill a listener thread

    # -- reading ----------------------------------------------------------

    def rows(self, since=0):
        with self._lock:
            return self._rows[since:]

    def stats(self):
        with self._lock:
            top = sorted(self.by_peer.items(), key=lambda kv: -kv[1])[:8]
            return {"total": self.total, "dropped": self.dropped,
                    "peers": len(self.by_peer),
                    "peers_untallied": self.peers_untallied,
                    "top": top}

    def clear(self):
        with self._lock:
            self._rows = []

    def close(self):
        with self._lock:
            fh, self._fh = self._fh, None
        if fh:
            try:
                fh.close()
            except OSError:
                pass


SOH = 0x01


def command_of(raw):
    """The TLS-350 command code in a frame, for the log's `cmd` field.

    A command is SOH then a letter then five digits -- optionally behind a
    six digit security code, which is stripped here because the code is a
    credential and belongs in the capture's `raw` only. Returns None for
    anything that is not shaped like one, which is most of what arrives on a
    public address.
    """
    if not raw:
        return None
    body = raw[1:] if raw[0] == SOH else raw
    if len(body) >= 6 and body[:6].isdigit():
        body = body[6:]
    if len(body) >= 6 and body[0:1].isalpha() and body[1:6].isdigit():
        return body[:6].decode("ascii", "replace").upper()
    return None


def printable(hexed):
    """A hex capture rendered for a person, without pretending it is text."""
    try:
        raw = bytes.fromhex(hexed or "")
    except ValueError:
        return ""
    out = []
    for b in raw:
        if b == SOH:
            out.append("<SOH>")
        elif b == 0x03:
            out.append("<ETX>")
        elif b == 0x0D:
            out.append("<CR>")
        elif b == 0x0A:
            out.append("<LF>")
        elif 0x20 <= b <= 0x7E:
            out.append(chr(b))
        else:
            out.append("<%02X>" % b)
    return "".join(out)


__all__ = ["BENCH", "LAN", "EXPOSED", "LEVELS", "LABELS", "BLURB",
           "Policy", "Gate", "Capture", "freeze_writes", "writes_frozen",
           "command_of",
           "refused", "refusals", "printable", "session"]
