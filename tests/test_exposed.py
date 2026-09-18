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
"""What an exposed console will and will not do.

The claim `--exposed` makes is narrow and it is worth stating exactly, so
that this file can hold it to it: on a public address, nothing the network
says reaches the disk, no source can hold more than its share of the
listeners, the card's identity does not carry this machine's name, and every
byte is captured.

Each of those is a test below. The one that matters most is
`NoWriteSurvivesTheFreeze`, because it is the one a future change is most
likely to break without noticing: it walks the source for `atomicfile`
writes and fails on any that does not consult the freeze, so a save added
later cannot quietly become a hole. See `exposed.py` for why the freeze is a
process-wide latch and not a parameter.

Nothing here binds to anything but loopback.
"""
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import exposed, presets, wire, xport, xportweb   # noqa: E402
from tls350sim import xportrec as R                             # noqa: E402
from tls350sim import xportmenu                                 # noqa: E402
from tls350sim import ifsf                                      # noqa: E402
from tls350sim import xportnet                                  # noqa: E402
from tls350sim.console import Console                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "tls350sim")


def a_console():
    c = Console(None)
    presets.load(c, "Two-tank retail site")
    return c


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Served:
    """A tunnel on loopback for the life of a `with`."""

    def __init__(self, console, policy=None, capture=None, gate=None):
        self.console = console
        self.policy = policy
        self.capture = capture
        self.gate = gate
        self.port = free_port()
        self._srv = None

    def __enter__(self):
        ready = threading.Event()

        def keep(sock):
            self._srv = sock
            ready.set()

        threading.Thread(
            target=wire.serve,
            args=(self.console, "127.0.0.1", self.port, False, None),
            kwargs={"on_socket": keep, "policy": self.policy,
                    "capture": self.capture, "gate": self.gate},
            daemon=True).start()
        ready.wait(5)
        return self

    def __exit__(self, *exc):
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass
        return False

    def connect(self, timeout=5):
        s = socket.create_connection(("127.0.0.1", self.port), timeout)
        s.recv(len(wire.PROBE))         # the card's probe, sent on connect
        return s


class TheFreezeStopsEveryPersistentWrite(unittest.TestCase):
    """The state file, the card's flash and the setup archive."""

    def setUp(self):
        exposed.freeze_writes(False)
        self.addCleanup(exposed.freeze_writes, False)
        self.dir = tempfile.mkdtemp()

    def test_console_state_is_not_written_while_frozen(self):
        path = os.path.join(self.dir, "state.json")
        c = Console(path)
        c.save()
        self.assertTrue(os.path.exists(path), "a bench console saves")
        before = open(path, encoding="utf-8").read()

        exposed.freeze_writes(True)
        c.set_setting("s_shift_1", 0, "0600")
        c.save()
        self.assertEqual(open(path, encoding="utf-8").read(), before,
                         "the frozen save rewrote the state file")

    def test_the_card_configuration_is_not_written_while_frozen(self):
        path = os.path.join(self.dir, "card.json")
        cfg = xport.XPortConfig(path)
        cfg.program(ip="10.0.0.5")
        cfg.save()
        self.assertIn("10.0.0.5", open(path, encoding="utf-8").read())

        exposed.freeze_writes(True)
        cfg.program(ip="192.0.2.99")
        cfg.save()
        self.assertNotIn("192.0.2.99", open(path, encoding="utf-8").read(),
                         "a frozen card wrote its new address to flash")

    def test_the_refusal_is_recorded_so_it_can_be_seen(self):
        exposed.freeze_writes(True)
        cfg = xport.XPortConfig(os.path.join(self.dir, "c.json"))
        cfg.save()
        self.assertTrue(any("card configuration" in what
                            for _ts, what in exposed.refusals()))


class NoWriteSurvivesTheFreeze(unittest.TestCase):
    """Every `atomicfile.replacing` in the package consults the freeze.

    The freeze is only as good as its coverage, and coverage is exactly the
    thing that rots: someone adds a save, it is correct in every other way,
    and the honeypot silently gets a hole. So this does not test behaviour,
    it tests the source -- every function that opens an atomic write must
    mention the freeze somewhere inside itself.

    `Capture._open` is the deliberate exception and is named here: the
    capture is the honeypot's whole product, it appends to a path the
    operator gave rather than one the network can steer, and freezing it
    would mean an exposed console that records nothing.
    """

    # `atomicfile` itself is the write primitive: it is what the others
    # call, and it has no idea what it is being asked to persist.
    ALLOWED_FILES = {"atomicfile.py"}

    # Writes that are deliberately outside the freeze. Each one is named
    # with the reason it is allowed to happen on a public address, because
    # a blanket exemption is how the next `archive_clear` gets in. Anything
    # NOT on this list and not consulting the freeze fails the test.
    ALLOWED = {
        ("exposed.py", "_open"):
            "the capture file itself -- the honeypot's whole product, "
            "append-only, to a path the operator named and the network "
            "cannot steer",
        ("paths.py", "user_data_dir"):
            "creates the data directory; makes no file and writes no "
            "content, and the path has no network input",
        ("update.py", "_discard"):
            "deletes the updater's OWN download, in a temp directory it "
            "made; not reachable from any listener",
        ("update.py", "download"):
            "the updater's temp file; the updater is off while frozen "
            "(check_on_startup returns False) and is never network-driven",
    }

    # Every way this package changes the disk. Widened from
    # `atomicfile.replacing` alone after that narrow version passed while
    # `Console.archive_clear` called `os.remove` on a path an
    # unauthenticated `S853` off the tunnel reached.

    # Every way this package changes the disk, not just the one it changes
    # it through most often. The first version of this walk matched
    # `atomicfile.replacing` alone, and that is exactly why it passed while
    # `Console.archive_clear` sat there calling `os.remove` on a path an
    # unauthenticated `S853` off the tunnel could reach: DELETING is
    # reaching the disk, and the walk could not see it.
    #
    # Code only, never prose -- matching the bare names would also match
    # every mention of them in a docstring, and the first run of this test
    # failed on its own.
    OPENS = re.compile(
        r"^\s*(?:with\s+)?(?:atomicfile\.replacing\(|"
        r"os\.(?:remove|unlink|rmdir|replace|rename|truncate|makedirs|"
        r"mkdir)\(|shutil\.(?:rmtree|move|copy\w*)\(|"
        r"open\([^)]*[\"'][rbt]*[wax])"
    )

    def test_every_atomic_write_consults_the_freeze(self):
        missing = []
        for name in sorted(os.listdir(PKG)):
            if not name.endswith(".py") or name in self.ALLOWED_FILES:
                continue
            if "(#" in name:
                # A sync client's edit-conflict or name-clash copy. Not a
                # module, and not this test's business.
                continue
            src = open(os.path.join(PKG, name), encoding="utf-8").read()
            if not any(self.OPENS.match(ln) for ln in src.splitlines()):
                continue
            for func, body in self._functions(src):
                if not any(self.OPENS.match(ln) for ln in body.splitlines()):
                    continue
                if ("exposed.refused" in body
                        or "exposed.writes_frozen" in body):
                    continue
                if (name, func) in self.ALLOWED:
                    continue
                missing.append("%s:%s" % (name, func))
        self.assertEqual(missing, [],
                         "these write to disk without consulting "
                         "exposed.writes_frozen(): " + ", ".join(missing))

    def test_the_walk_can_still_see_a_hole(self):
        """A guard that cannot fail is not a guard.

        The first version of this walk matched one primitive and silently
        passed over `os.remove`. This feeds it a function with each shape
        of unguarded write and fails if any of them slips through, so a
        regex edited into uselessness is caught here rather than by the
        next audit.
        """
        for line in ('    with atomicfile.replacing(p) as f:',
                     '    os.remove(p)',
                     '    os.unlink(p)',
                     '    shutil.rmtree(p)',
                     '    with open(p, "w") as f:',
                     '    open(p, "wb").write(b"x")',
                     '    os.replace(a, b)'):
            src = "def leaky(self):\n%s\n        pass\n" % line
            found = [f for f, body in self._functions(src)
                     if any(self.OPENS.match(ln)
                            for ln in body.splitlines())]
            self.assertEqual(found, ["leaky"],
                             "the walk cannot see %r" % line.strip())

    @staticmethod
    def _functions(src):
        """(name, body) for every `def` in a module, by indentation."""
        lines = src.splitlines()
        out = []
        starts = [i for i, ln in enumerate(lines)
                  if re.match(r"\s*def \w+", ln)]
        for i in starts:
            indent = len(lines[i]) - len(lines[i].lstrip())
            body = []
            for ln in lines[i + 1:]:
                if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
                    break
                body.append(ln)
            out.append((re.match(r"\s*def (\w+)", lines[i]).group(1),
                        "\n".join(body)))
        return out


class TheCardDoesNotPublishTheHostName(unittest.TestCase):

    def test_an_anonymous_mac_is_lantronix_but_not_derived(self):
        derived = xport.default_mac()
        first = xport.default_mac(anonymous=True)
        second = xport.default_mac(anonymous=True)
        for mac in (first, second):
            self.assertEqual(tuple(mac[:3]), xport.LANTRONIX_OUI,
                             "an anonymous MAC must still be a Lantronix one")
            self.assertEqual(len(mac), 6)
        self.assertNotEqual(first, derived,
                            "the anonymous MAC still carries the host name")
        self.assertNotEqual(first, second,
                            "an anonymous MAC that repeats is a serial number")

    def test_the_bench_mac_is_still_stable(self):
        self.assertEqual(xport.default_mac(), xport.default_mac(),
                         "a bench card must be the same unit every run")


class TheGateHoldsTheLine(unittest.TestCase):
    """Admission: the total, the per-source cap and the rate."""

    def setUp(self):
        self.cap = exposed.Capture()
        self.policy = exposed.Policy(exposed.EXPOSED, capture=self.cap)

    def test_one_source_cannot_take_more_than_its_share(self):
        gate = exposed.Gate(self.policy, capture=self.cap)
        allowed = self.policy.max_per_ip
        for _ in range(allowed):
            self.assertTrue(gate.admit("198.51.100.7"))
        self.assertFalse(gate.admit("198.51.100.7"),
                         "the per-source cap let one address past it")
        # and it is not a global lockout: another source still gets in
        self.assertTrue(gate.admit("198.51.100.8"))

    def test_releasing_gives_the_slot_back(self):
        gate = exposed.Gate(self.policy, capture=self.cap)
        for _ in range(self.policy.max_per_ip):
            gate.admit("203.0.113.1")
        self.assertFalse(gate.admit("203.0.113.1"))
        gate.release("203.0.113.1")
        self.assertTrue(gate.admit("203.0.113.1"))

    def test_connect_and_drop_is_caught_by_the_rate_and_not_the_cap(self):
        """The per-source cap never sees this: nothing is held at any
        instant. The rate is the only thing that does."""
        gate = exposed.Gate(self.policy, capture=self.cap)
        ip = "192.0.2.50"
        for _ in range(self.policy.rate_per_min):
            self.assertTrue(gate.admit(ip))
            gate.release(ip)
        self.assertFalse(gate.admit(ip),
                         "an address reconnecting in a loop was never rated")

    def test_the_rate_window_moves(self):
        clock = [1000.0]
        gate = exposed.Gate(self.policy, capture=self.cap,
                            clock=lambda: clock[0])
        ip = "192.0.2.51"
        for _ in range(self.policy.rate_per_min):
            gate.admit(ip)
            gate.release(ip)
        self.assertFalse(gate.admit(ip))
        clock[0] += gate.WINDOW + 1
        self.assertTrue(gate.admit(ip), "the rate window never expired")

    def test_a_bench_gate_is_not_built_at_all(self):
        bench = exposed.Policy(exposed.BENCH)
        self.assertIsNone(bench.max_total)
        self.assertIsNone(bench.idle_timeout)

    def test_a_refusal_is_captured_because_a_flood_is_the_finding(self):
        gate = exposed.Gate(self.policy, capture=self.cap)
        ip = "198.51.100.99"
        for _ in range(self.policy.max_per_ip + 1):
            gate.admit(ip)
        rows = [r for r in self.cap.rows() if r["dir"] == "refused"]
        self.assertTrue(rows, "a refused connection was not recorded")
        self.assertEqual(rows[0]["src"], ip)


class TheTunnelUnderExposure(unittest.TestCase):
    """The serial tunnel itself, over a real loopback socket."""

    def setUp(self):
        self.console = a_console()

    def test_an_idle_connection_is_dropped_instead_of_held_for_ever(self):
        """There was no timeout at all: this is the cheapest denial of
        service this program had, and it cost one connection."""
        policy = exposed.Policy(exposed.EXPOSED)
        policy.idle_timeout = 0.4           # the same mechanism, faster
        with _Served(self.console, policy=policy) as served:
            s = served.connect()
            s.settimeout(5)
            self.assertEqual(s.recv(64), b"",
                             "an idle connection was not dropped")
            s.close()

    def test_a_bench_connection_is_not_dropped(self):
        with _Served(self.console) as served:
            s = served.connect()
            s.settimeout(1.5)
            with self.assertRaises(socket.timeout):
                s.recv(64)                  # still open: nothing arrives
            s.close()

    def test_the_command_and_its_answer_are_both_captured(self):
        cap = exposed.Capture()
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        policy._delay = None                # no need to wait in a test
        with _Served(self.console, policy=policy, capture=cap) as served:
            s = served.connect()
            s.sendall(b"\x01" + b"I20100")
            s.settimeout(5)
            s.recv(4096)
            s.close()
        time.sleep(0.2)
        rows = cap.rows()
        ins = [r for r in rows if r["dir"] == "in"]
        outs = [r for r in rows if r["dir"] == "out"]
        self.assertTrue(ins and outs, "nothing was captured")
        self.assertEqual(ins[0]["src"], "127.0.0.1")
        self.assertEqual(ins[0]["proto"], "tunnel")
        self.assertIn("I20100", bytes.fromhex(ins[0]["raw"]).decode("latin-1"))
        self.assertTrue(any(r.get("cmd") == "I20100" for r in outs),
                        "the answer was not tagged with its command code")

    def test_the_capture_keeps_bytes_not_text(self):
        """A stranger's traffic is not UTF-8 and decoding it loses the part
        that made it worth keeping."""
        cap = exposed.Capture()
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        policy._delay = None
        junk = bytes(range(256))
        with _Served(self.console, policy=policy, capture=cap) as served:
            s = served.connect()
            s.sendall(junk)
            time.sleep(0.3)
            s.close()
        time.sleep(0.2)
        seen = b"".join(bytes.fromhex(r["raw"]) for r in cap.rows()
                        if r["dir"] == "in")
        self.assertIn(junk, seen, "the raw bytes did not survive the capture")

    def test_a_refused_connection_is_closed_not_left_hanging(self):
        cap = exposed.Capture()
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        gate = exposed.Gate(policy, capture=cap)
        # fill the per-source allowance from the gate's own side
        for _ in range(policy.max_per_ip):
            gate.admit("127.0.0.1")
        with _Served(self.console, policy=policy, capture=cap,
                     gate=gate) as served:
            s = socket.create_connection(("127.0.0.1", served.port), 5)
            s.settimeout(5)
            self.assertEqual(s.recv(64), b"",
                             "a refused connection was left open")
            s.close()


class TheResponseDelayIsNotAFingerprint(unittest.TestCase):
    """GasPot's lesson: an instant reply did not come from a serial line."""

    def test_an_exposed_policy_waits_and_a_bench_one_does_not(self):
        bench = exposed.Policy(exposed.BENCH)
        self.assertEqual(bench.delay(), 0.0)
        hot = exposed.Policy(exposed.EXPOSED)
        seen = {round(hot.delay(), 6) for _ in range(200)}
        self.assertGreater(len(seen), 100,
                           "a delay that repeats is itself a fingerprint")
        for d in seen:
            self.assertGreaterEqual(d, 0.15)
            self.assertLessEqual(d, 0.75)

    def test_the_delay_does_not_disturb_the_console_random_stream(self):
        """`traffic.py` is documented as the only module drawing from the
        global stream, and the suite's determinism rests on it."""
        import random as _r
        _r.seed(1234)
        before = [_r.random() for _ in range(3)]
        _r.seed(1234)
        p = exposed.Policy(exposed.EXPOSED)
        for _ in range(50):
            p.delay()
        after = [_r.random() for _ in range(3)]
        self.assertEqual(before, after,
                         "the response delay drew from the global stream")


class TheWebManagerLiesInsteadOfObeying(unittest.TestCase):
    """Apply Settings must not move an exposed card, and must not say so.

    Refusing would be safe and would also be the loudest fingerprint on the
    card: no real XPort declines its own Apply Settings. So the page reports
    success, the unit "reboots", and nothing underneath moved.
    """

    def setUp(self):
        exposed.freeze_writes(False)
        self.addCleanup(exposed.freeze_writes, False)
        self.cfg = xport.XPortConfig(None)
        self.cfg.program(ip="10.1.1.10", port=10001)

    def test_a_bench_card_applies_the_change(self):
        state = xportweb.WebState(self.cfg)
        state.edit().ip = "10.9.9.9"
        state.commit()
        self.assertEqual(self.cfg.ip, "10.9.9.9")

    def test_an_exposed_card_does_not_move(self):
        exposed.freeze_writes(True)
        state = xportweb.WebState(self.cfg)
        state.edit().ip = "203.0.113.77"
        state.edit().port = 9
        state.commit()
        self.assertEqual(self.cfg.ip, "10.1.1.10",
                         "an exposed card took a new address in memory -- "
                         "CardNetwork would follow it and go dark")
        self.assertEqual(self.cfg.port, 10001)

    def test_it_still_looks_like_it_worked(self):
        exposed.freeze_writes(True)
        state = xportweb.WebState(self.cfg)
        state.edit().ip = "203.0.113.77"
        state.commit()
        self.assertTrue(state.rebooting(),
                        "a deflected save did not fake the reboot, which is "
                        "the tell a scanner looks for")

    def test_what_was_attempted_is_kept(self):
        exposed.freeze_writes(True)
        state = xportweb.WebState(self.cfg)
        state.edit().ip = "203.0.113.77"
        state.commit()
        self.assertTrue(state.attempts, "the attempt was not recorded")
        self.assertEqual(state.attempts[-1][2]["ip"], "203.0.113.77")

    def test_factory_defaults_is_deflected_too(self):
        exposed.freeze_writes(True)
        state = xportweb.WebState(self.cfg)
        state.defaults()
        self.assertEqual(self.cfg.ip, "10.1.1.10",
                         "an exposed card was reset to defaults by a POST")


class TheCaptureFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cap.jsonl")

    def test_it_is_json_lines_one_object_to_a_line(self):
        cap = exposed.Capture(self.path)
        cap.record("in", b"\x01I20100", peer="198.51.100.1", port=4444)
        cap.record("out", b"\x01ok\x03", peer="198.51.100.1", port=4444)
        cap.close()
        rows = [json.loads(ln) for ln in open(self.path, encoding="utf-8")
                if ln.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["src"], "198.51.100.1")
        self.assertEqual(rows[0]["cmd"], "I20100")
        self.assertEqual(bytes.fromhex(rows[0]["raw"]), b"\x01I20100")

    def test_a_restart_appends_and_never_truncates(self):
        """A capture is evidence; a restart must not be able to destroy it."""
        a = exposed.Capture(self.path)
        a.record("in", b"first", peer="1.2.3.4")
        a.close()
        b = exposed.Capture(self.path)
        b.record("in", b"second", peer="1.2.3.4")
        b.close()
        text = open(self.path, encoding="utf-8").read()
        self.assertIn(b"first".hex(), text)
        self.assertIn(b"second".hex(), text)

    def test_the_ring_is_bounded_so_a_flood_is_not_a_leak(self):
        cap = exposed.Capture(ring=10)
        for i in range(100):
            cap.record("in", bytes([i]), peer="1.1.1.1")
        self.assertEqual(len(cap.rows()), 10)
        self.assertEqual(cap.total, 100)
        self.assertTrue(cap.dropped)

    def test_the_command_is_found_behind_a_security_code(self):
        self.assertEqual(exposed.command_of(b"\x01" + b"123456" + b"I20100"),
                         "I20100")
        self.assertEqual(exposed.command_of(b"\x01I20100"), "I20100")
        self.assertIsNone(exposed.command_of(b"GET / HTTP/1.1"))
        self.assertIsNone(exposed.command_of(b""))

    def test_printable_shows_the_control_bytes_by_name(self):
        self.assertEqual(exposed.printable(b"\x01AB\x03".hex()),
                         "<SOH>AB<ETX>")


class TheLevelsMeanWhatTheySay(unittest.TestCase):

    def test_bench_changes_nothing(self):
        p = exposed.Policy(exposed.BENCH)
        self.assertFalse(p.exposed)
        self.assertFalse(p.readonly)
        self.assertFalse(p.capturing)
        self.assertTrue(p.stable_identity)

    def test_lan_captures_but_still_keeps_its_programming(self):
        p = exposed.Policy(exposed.LAN)
        self.assertFalse(p.readonly)
        self.assertTrue(p.capturing)
        self.assertTrue(p.stable_identity,
                        "a bench-network card must stay the same unit")

    def test_exposed_is_all_four(self):
        p = exposed.Policy(exposed.EXPOSED)
        self.assertTrue(p.exposed)
        self.assertTrue(p.readonly)
        self.assertTrue(p.capturing)
        self.assertFalse(p.stable_identity)

    def test_an_unknown_level_is_refused_rather_than_defaulted(self):
        """Defaulting an unrecognised level to BENCH would mean a typo in a
        deployment script silently serving an unprotected console."""
        with self.assertRaises(ValueError):
            exposed.Policy("public")


class DeletingIsReachingTheDisk(unittest.TestCase):
    """CLEAR SETUP DATA is a write, and it was not frozen.

    `Console.archive_save` consulted the freeze and its sibling
    `archive_clear` did not -- and it is the more dangerous of the two,
    because it removes a file rather than rewriting one. `S853` reaches it
    straight off the serial tunnel, and the tunnel is unauthenticated on a
    default card, so one four-byte frame deleted the operator's archive
    from an exposed console.
    """

    def setUp(self):
        exposed.freeze_writes(False)
        self.addCleanup(exposed.freeze_writes, False)
        self.dir = tempfile.mkdtemp()
        self.c = Console(os.path.join(self.dir, "state.json"))
        presets.load(self.c, "Two-tank retail site")
        self.c.archive_save()
        self.path = self.c.archive_path()
        self.assertTrue(os.path.exists(self.path), "no archive to clear")

    def test_a_bench_console_still_clears_its_archive(self):
        self.assertGreaterEqual(self.c.archive_clear(), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_a_frozen_console_will_not_delete_it(self):
        exposed.freeze_writes(True)
        self.c.archive_clear()
        self.assertTrue(os.path.exists(self.path),
                        "S853 deleted the archive on an exposed console")

    def test_the_refusal_is_recorded(self):
        exposed.freeze_writes(True)
        self.c.archive_clear()
        self.assertTrue(any("clear setup data" in what
                            for _ts, what in exposed.refusals()))


class TheCardStopsHandingOutItsPassword(unittest.TestCase):
    """77FEh answers anyone, and record 0 carries the setup password.

    Bytes 8-11 of record 0 are the four-character telnet and web setup
    password, and `Discovery` answers a four-byte probe from any source
    with no authentication. That is the same secret the web manager takes
    as its HTTP Basic password, so the credential locking both interfaces
    was readable by anybody who asked for it.
    """

    def _disc(self, level):
        cfg = xport.XPortConfig(None)
        cfg.telnet_password = "S3cr"
        pol = exposed.Policy(level)
        return xport.Discovery(cfg, policy=pol), cfg

    def test_a_bench_card_stays_byte_exact(self):
        """The capture is diffed against the real card on a bench, so the
        emulation must not move there."""
        disc, cfg = self._disc(exposed.BENCH)
        self.assertEqual(disc._record0(), cfg.setup_record())

    def test_an_exposed_card_masks_the_password(self):
        disc, cfg = self._disc(exposed.EXPOSED)
        rec = disc._record0()
        self.assertEqual(rec[R.PASSWD_OFF:R.PASSWD_OFF + 4], b"\x00" * 4,
                         "the setup password went out over 77FEh")
        # and nothing else moved
        plain = bytearray(cfg.setup_record())
        plain[R.PASSWD_OFF:R.PASSWD_OFF + 4] = b"\x00" * 4
        self.assertEqual(rec, bytes(plain),
                         "masking the password changed other bytes too")

    def test_the_password_is_not_in_the_f9_reply(self):
        disc, _cfg = self._disc(exposed.EXPOSED)
        self.assertNotIn(b"S3cr", disc.answer(b"\x00\x00\x00\xF8"))

    def test_disable_port_77fe_is_actually_honoured(self):
        """The card's own Security menu offers this switch, and every
        listener ignored it -- so the documented mitigation for the leak
        above did nothing at all."""
        disc, cfg = self._disc(exposed.BENCH)
        self.assertIsNotNone(disc.answer(b"\x00\x00\x00\xF6"))
        cfg.security["port_77fe"] = False
        self.assertIsNone(disc.answer(b"\x00\x00\x00\xF6"),
                          "77FEh answered with the port disabled")
        self.assertIsNone(disc.answer(b"\x00\x00\x00\xF8"))


class TheGateGivesSlotsBackWhenTheLevelDrops(unittest.TestCase):
    """The bench's dropdown mutates the policy in place under a live gate.

    `release` returned early when `max_total` was None, which is what a
    bench policy has -- so EXPOSED -> Bench with connections still open
    never decremented them. Raise the level again and the gate starts from
    a stale total that can never drain, and once the residue reaches the
    cap it refuses everyone for the life of the process.
    """

    def test_connections_open_across_a_level_change_are_released(self):
        policy = exposed.Policy(exposed.EXPOSED)
        gate = exposed.Gate(policy)
        for _ in range(policy.max_per_ip):
            self.assertTrue(gate.admit("198.51.100.4"))
        self.assertEqual(gate.held()[0], policy.max_per_ip)

        # the dropdown, mid-flight
        policy.level = exposed.BENCH
        total, per_ip, rate, idle = exposed.Policy.LIMITS[exposed.BENCH]
        policy.max_total, policy.max_per_ip = total, per_ip
        policy.rate_per_min, policy.idle_timeout = rate, idle
        for _ in range(4):
            gate.release("198.51.100.4")

        # and back up again
        policy.level = exposed.EXPOSED
        total, per_ip, rate, idle = exposed.Policy.LIMITS[exposed.EXPOSED]
        policy.max_total, policy.max_per_ip = total, per_ip
        policy.rate_per_min, policy.idle_timeout = rate, idle
        held, _per = gate.held()
        self.assertEqual(held, policy.max_per_ip - 4,
                         "slots released while on the bench never came back")

    def test_releasing_what_was_never_counted_cannot_go_negative(self):
        gate = exposed.Gate(exposed.Policy(exposed.BENCH))
        for _ in range(5):
            gate.release("10.0.0.1")
        self.assertEqual(gate.held()[0], 0)


class TheWebManagerRefusesGetForAnythingThatChangesTheCard(unittest.TestCase):
    """`setup.cgi` and `apply.htm` answered a GET, and both write.

    `_route` computed `post` and then never consulted it on either branch,
    so `<img src="http://card/secure/apply.htm">` in any page the owner
    opened committed a pending configuration, and
    `setup.cgi?rec0=<base64>` rewrote record 0 -- the address, the tunnel
    port and the four-byte setup password -- from a URL with no form, no
    body and no Content-Type.

    POST-only is also the faithful answer:
    `reference/lantronix_xport_capture.md` line 442 records the real
    endpoint as `POST /secure/setup.cgi`.
    """

    def _route_of(self, path, post):
        """Call `_route` on a handler built without a socket."""
        seen = {}
        h = xportweb._Handler.__new__(xportweb._Handler)
        h.path = path
        h.command = "POST" if post else "GET"
        h.server = self.srv
        h._send = lambda body, status=200, ctype="text/html": seen.setdefault(
            "sent", (status, body))
        h._not_found = lambda: seen.setdefault("sent", (404, b"ERROR 404"))
        h._unauthorized = lambda: seen.setdefault("sent", (401, b""))
        h._route(xportweb.parse_qs(""), post=post)
        return seen.get("sent", (None, None))

    def setUp(self):
        exposed.freeze_writes(False)
        self.addCleanup(exposed.freeze_writes, False)
        self.cfg = xport.XPortConfig(None)
        self.cfg.program(ip="10.1.1.10", port=10001)
        self.srv = xportweb._Server.__new__(xportweb._Server)
        self.srv.config = self.cfg
        self.srv.state = xportweb.WebState(self.cfg)
        self.srv.capture = None
        self.srv.policy = None
        self.srv.gate = None
        self.srv.logfn = None

    def test_get_on_setup_cgi_is_refused(self):
        status, _body = self._route_of("/secure/setup.cgi?def=1", post=False)
        self.assertEqual(status, 404,
                         "a GET reset the card to factory defaults")

    def test_get_on_apply_htm_is_refused(self):
        self.srv.state.edit().ip = "203.0.113.5"
        status, _body = self._route_of("/secure/apply.htm", post=False)
        self.assertEqual(status, 404)
        self.assertEqual(self.cfg.ip, "10.1.1.10",
                         "a GET committed a pending configuration")

    def test_post_still_reaches_them(self):
        """The fix must not break the card's own forms, which POST."""
        status, _body = self._route_of("/secure/apply.htm", post=True)
        self.assertNotEqual(status, 404,
                            "POST must still be served -- the real card's "
                            "forms are method=post")


class TheWebManagerActuallyAsksTheGate(unittest.TestCase):
    """The gate was stored on the server and never consulted.

    Every other listener on the card runs its connections past
    `exposed.Gate`. `serve_web` took one, assigned it to an attribute and
    then served every connection that arrived -- so on tcp/80, the one
    listener that spawns a thread per REQUEST, the per-source cap, the
    total and the rate did not apply at all.
    """

    def _server(self, policy, gate):
        srv = xportweb._Server.__new__(xportweb._Server)
        srv.gate = gate
        srv.policy = policy
        return srv

    class _Req:
        closed = False

        def close(self):
            self.closed = True

        def settimeout(self, _t):
            pass

    def _drive(self, srv, ip, times):
        """process_request without letting a thread actually run."""
        started = []
        real = threading.Thread

        class NoRun(real):
            def start(self):
                started.append(1)

        threading.Thread = NoRun
        try:
            admitted = 0
            for _ in range(times):
                before = len(started)
                xportweb._Server.process_request(srv, self._Req(), (ip, 1))
                if len(started) > before:
                    admitted += 1
        finally:
            threading.Thread = real
        return admitted

    def test_one_source_is_capped_on_port_80_too(self):
        cap = exposed.Capture()
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        gate = exposed.Gate(policy, capture=cap)
        srv = self._server(policy, gate)
        admitted = self._drive(srv, "198.51.100.5", policy.max_per_ip + 4)
        self.assertEqual(admitted, policy.max_per_ip,
                         "the web manager served past its per-source cap")
        self.assertTrue(any(r["dir"] == "refused" for r in cap.rows()),
                        "a refused web connection was not captured")

    def test_a_bench_server_still_serves_everything(self):
        policy = exposed.Policy(exposed.BENCH)
        gate = exposed.Gate(policy)
        srv = self._server(policy, gate)
        self.assertEqual(self._drive(srv, "127.0.0.1", 40), 40,
                         "a bench web manager refused a connection")


class TheEscaperCoversEveryQuote(unittest.TestCase):
    """Values a stranger POSTs are stored verbatim and made safe here.

    The terminal name, domain name, e-mail recipients and trigger message
    all reach the records unescaped and are only rendered safe by `esc` at
    output time. It left `'` alone, which held only because every attribute
    in the file happens to be double-quoted.
    """

    def test_every_dangerous_character(self):
        got = xportweb.esc("""a'b"c<d>e&f""")
        for raw in ("<", ">", '"', "'"):
            self.assertNotIn(raw, got, "%r survived escaping: %r"
                             % (raw, got))
        self.assertEqual(got, "a&#39;b&quot;c&lt;d&gt;e&amp;f")

    def test_a_stored_script_tag_renders_escaped(self):
        cfg = xport.XPortConfig(None)
        # An e-mail recipient: 49 characters of free text a stranger can
        # POST unauthenticated, stored verbatim in the records, and made
        # safe only by `esc` when the page is next rendered.
        cfg.recipient_1 = "<script>alert(1)</script>"
        page = xportweb.email_page(cfg)
        self.assertNotIn("<script>alert(1)", page,
                         "a stored script tag reached the page unescaped")
        self.assertIn("&lt;script&gt;", page)


class ARepliesFrameCannotBeForgedFromInside(unittest.TestCase):
    """A stored value could carry SOH, and every reply drawing it re-emitted it.

    The wire frames a reply `SOH ... ETX`, and SOH is the only way a client
    can find where a frame begins. Stored text went out through
    `encode("ascii", "replace")`, which substitutes bytes ABOVE 0x7E and
    lets every control byte below 0x20 through -- so a Set that put a raw
    0x01 into a stored field made the console emit a second,
    attacker-authored frame inside its own reply, closed by the real
    reply's ETX. The display format carries no checksum to contradict it.

    The station header at 503 is the strongest version: four separately
    settable lines, drawn at the top of nearly every display report.
    """

    def setUp(self):
        self.c = a_console()
        self.h = wire.Handler(self.c, False, None)

    def test_a_set_cannot_open_a_second_frame(self):
        self.h.handle(b"\x01S50301\x01I20100")
        reply = self.h.handle(b"\x01I10100")
        self.assertEqual(reply.count(wire.SOH), 1,
                         "a stored SOH forged a second frame: %r" % reply)
        self.assertEqual(reply.count(wire.ETX), 1)

    def test_a_set_cannot_close_the_frame_early(self):
        self.h.handle(b"\x01S50301AB\x03CD")
        reply = self.h.handle(b"\x01I10100")
        self.assertEqual(reply.count(wire.ETX), 1,
                         "a stored ETX ended the frame early: %r" % reply)
        self.assertTrue(reply.endswith(wire.ETX))

    def test_every_control_byte_is_taken_out(self):
        for b in list(range(0x20)) + [0x7F]:
            if b in (0x0D, 0x0A):
                continue          # the frame's own separators
            self.c.values["S50301"] = "A%sB" % chr(b)
            reply = self.h.handle(b"\x01I10100")
            self.assertEqual(reply.count(wire.SOH), 1,
                             "byte %02X survived into the frame" % b)
            self.assertNotIn(bytes([b]), reply[1:-1],
                             "byte %02X reached the wire" % b)

    def test_ordinary_text_is_untouched(self):
        """The sanitiser must not damage what a console really displays."""
        self.c.values["S50301"] = "1200 STATION ROAD"
        reply = self.h.handle(b"\x01I10100")
        self.assertIn(b"1200 STATION ROAD", reply)

    def test_the_line_structure_survives(self):
        self.c.values["S50301"] = "LINE ONE"
        self.c.values["S50302"] = "LINE TWO"
        reply = self.h.handle(b"\x01I10100")
        self.assertIn(b"LINE ONE\r\nLINE TWO", reply)


class ARefusalCostsWhatAnAnswerCosts(unittest.TestCase):
    """The response delay sat on the answering path only.

    A command refused for a bad RS-232 security code returns `b""`, and the
    delay was inside `if out:` -- so a wrong guess was free while a right
    one cost 0.15-0.75 s and produced a reply. Over a six-digit code, with
    `Framer.feed` accepting an inquiry with no terminator (about 315
    guesses to a segment), that is a free oracle.
    """

    def test_the_policy_sleeps_for_a_refusal_too(self):
        slept = []
        policy = exposed.Policy(exposed.EXPOSED)
        policy.sleep = lambda: slept.append(1)

        console = a_console()
        # turn the security code on, so the next command is refused
        console.rs232_security = True          # the card's DIP switch
        console.values["S50400"] = "123456"     # function 504, the code
        self.assertTrue(console.rs232_enforces_security(),
                        "the refusal path is not even armed")

        with _Served(console, policy=policy) as served:
            s = served.connect()
            s.settimeout(3)
            s.sendall(b"\x01" + b"000000" + b"I20100")
            time.sleep(0.6)
            got = b""
            try:
                got = s.recv(4096)
            except socket.timeout:
                pass
            s.close()
        time.sleep(0.2)
        self.assertEqual(got, b"",
                         "the wrong code was answered, so this test is not "
                         "exercising the refusal path at all")
        self.assertTrue(slept,
                        "a refused command was answered instantly, which "
                        "prices a wrong security code at nothing")


class TheSetupMenuEnforcesThePasswordItStores(unittest.TestCase):
    """9999 read the setup password, printed it, and never checked it.

    `xportweb._authorised` enforces `config.telnet_password`, so an
    operator who set a setup password reasonably believed both doors were
    locked. Only one was: the telnet menu's sole gate was "did a return
    arrive in the window", and one Enter produced the whole parameter dump
    -- address, gateway, netmask, every Security flag, the SNMP community,
    the hostlist, the e-mail recipients -- and the Change Setup menu.

    The prompt's wording is inferred; see the note in `xportmenu.feed`.
    Everything asserted here is about what is and is not disclosed.
    """

    # Strings that appear only in the parameter dump, never in the banner
    # the real card prints to anyone who connects.
    DUMP_ONLY = (b"Change Setup", b"10.9.8.7", b"Security")

    def _run(self, cfg, *lines):
        out = []
        sess = xportmenu.SetupSession(cfg, out.append)
        for line in lines:
            sess.feed(line)
        return b"".join(out), sess

    def _carded(self, password=""):
        cfg = xport.XPortConfig(None)
        cfg.program(ip="10.9.8.7")
        cfg.telnet_password = password
        return cfg

    def _leaks(self, blob):
        return [m for m in self.DUMP_ONLY if m in blob]

    def test_an_unpassworded_card_is_unchanged(self):
        """The captured card had no password, and must still behave as
        captured: Enter opens setup."""
        blob, _s = self._run(self._carded(), "")
        self.assertEqual(self._leaks(blob), list(self.DUMP_ONLY))

    def test_enter_alone_no_longer_opens_a_passworded_card(self):
        blob, _s = self._run(self._carded("S3cr"), "")
        self.assertEqual(self._leaks(blob), [],
                         "one Enter dumped a passworded card's setup")
        self.assertIn(b"Password", blob)

    def test_a_wrong_password_discloses_nothing(self):
        blob, _s = self._run(self._carded("S3cr"), "", "wrong")
        self.assertEqual(self._leaks(blob), [])

    def test_the_right_password_opens_it(self):
        blob, _s = self._run(self._carded("S3cr"), "", "S3cr")
        self.assertEqual(self._leaks(blob), list(self.DUMP_ONLY))

    def test_guessing_is_not_free(self):
        """Three tries and the session goes, so 9999 is not an oracle over
        a four-character code."""
        cfg = self._carded("S3cr")
        _blob, sess = self._run(cfg, "", "aaaa", "bbbb")
        self.assertFalse(sess.closed, "dropped earlier than three tries")
        _blob, sess = self._run(cfg, "", "aaaa", "bbbb", "cccc")
        self.assertTrue(sess.closed, "a wrong password could be retried "
                                     "for ever on one connection")

    def test_the_banner_is_still_shown_before_the_prompt(self):
        """The real card greets anyone who connects; that is captured
        behaviour and is not what this fix is about."""
        blob, _s = self._run(self._carded("S3cr"), "")
        self.assertIn(b"Software version", blob)


class TheCardIsNotAReflector(unittest.TestCase):
    """UDP discovery answered anybody, any number of times.

    Every other limit in `exposed.Gate` counts CONNECTIONS -- admit, hold,
    release -- and UDP has none of that, so the 77FEh responder on 30718 sat
    outside all of them. A four-byte probe draws a 124-byte reply (opcodes
    F8 and E0, the setup record): thirty-one times amplification over a
    protocol with no handshake, so the source address can be forged.

    That makes an exposed console a usable DDoS reflector, and the victim
    is a third party who never touched this program. It is the one finding
    in this file whose harm lands on somebody else.
    """

    def _responder(self, level=exposed.EXPOSED):
        cfg = xport.XPortConfig(None)
        cfg.program(ip="10.0.0.5")
        cap = exposed.Capture()
        pol = exposed.Policy(level, capture=cap)
        gate = exposed.Gate(pol, capture=cap)
        disc = xport.Discovery(cfg, policy=pol, gate=gate, capture=cap)
        sent = []

        class Sock:
            def sendto(self, data, addr):
                sent.append((data, addr))

        disc.sock = Sock()
        return disc, gate, cap, sent

    def test_the_amplification_is_real_and_worth_bounding(self):
        """Documents the ratio, so a change that made it worse would show."""
        disc, _g, _c, _s = self._responder()
        reply = disc.answer(b"\x00\x00\x00\xF8")
        self.assertGreater(len(reply) / 4.0, 20,
                           "if this ever drops, revisit the rate limit")

    def test_one_forged_source_cannot_pump_it(self):
        disc, gate, cap, sent = self._responder()
        for _ in range(40):
            disc._reply(b"X" * 124, ("198.51.100.9", 30718))
        self.assertEqual(len(sent), gate.DATAGRAM_RATE,
                         "the responder answered past its datagram rate")
        self.assertTrue(any(r["dir"] == "refused" and r["proto"] == "77fe/udp"
                            for r in cap.rows()),
                        "a rate-limited datagram was not recorded")

    def test_other_sources_are_unaffected(self):
        """A limit that punishes everyone for one source's traffic would
        stop the card being discoverable at all."""
        disc, gate, _cap, sent = self._responder()
        for _ in range(40):
            disc._reply(b"X" * 124, ("198.51.100.9", 30718))
        sent.clear()
        disc._reply(b"X" * 124, ("203.0.113.4", 30718))
        self.assertEqual(len(sent), 1)

    def test_forged_datagrams_cannot_spend_a_real_client_budget(self):
        """A TCP connection proves its source; a datagram proves nothing.

        Sharing one rate window between them let an attacker spend a
        victim's CONNECTION allowance by sending datagrams that merely
        claimed to come from the victim. Measured before the split: 12 of
        an address's 30 connections a minute, for any address named.
        """
        policy = exposed.Policy(exposed.EXPOSED)
        victim = "198.51.100.20"

        clean = exposed.Gate(policy)
        allowed = 0
        while clean.admit(victim) and allowed < 200:
            clean.release(victim)
            allowed += 1

        flooded = exposed.Gate(policy)
        for _ in range(500):
            flooded.datagram_ok(victim)
        after = 0
        while flooded.admit(victim) and after < 200:
            flooded.release(victim)
            after += 1

        self.assertEqual(after, allowed,
                         "forged datagrams spent a real client's "
                         "connection budget")

    def test_a_bench_card_is_not_rate_limited(self):
        disc, _gate, _cap, sent = self._responder(exposed.BENCH)
        for _ in range(40):
            disc._reply(b"X" * 124, ("127.0.0.1", 30718))
        self.assertEqual(len(sent), 40,
                         "a bench card rate-limited its own discovery")


class TheUpdaterDoesNotTrustAServerFilename(unittest.TestCase):
    """`os.path.join` obeys a traversing or absolute name.

    `release.installer_name` is whatever the releases API called the asset.
    A name of `..\\..\\evil.exe` walks out of the temp directory, and an
    absolute one discards the temp directory altogether. That needs a
    hostile release, which is a bigger problem than this -- but it is one
    line to stop the compromise from also being able to write anywhere the
    user can.
    """

    def test_a_traversing_name_is_reduced_to_its_basename(self):
        for name, want in ((r"..\..\..\evil.exe", "evil.exe"),
                           ("C:\\Windows\\System32\\evil.exe", "evil.exe"),
                           ("../../etc/cron.d/x", "x"),
                           ("", "update"),
                           ("..", "update")):
            safe = os.path.basename(name or "").strip() or "update"
            if safe in (".", ".."):
                safe = "update"
            self.assertEqual(safe, want, "%r -> %r" % (name, safe))

    def test_the_source_still_does_it(self):
        """The logic above mirrors `update.download`; this checks the file
        has not drifted away from it."""
        src = open(os.path.join(os.path.dirname(PKG), "tls350sim",
                                "update.py"), encoding="utf-8").read()
        self.assertIn("os.path.basename(release.installer_name", src,
                      "update.download stopped sanitising the asset name")


class TheIfsfDatabaseFailsClosed(unittest.TestCase):
    """A trap for whoever builds the IFSF transport, defused early.

    `ifsf.py` has no dispatcher -- the LON framing is in external IFSF
    documents that are not on the shelf -- so none of this is reachable and
    none of it was a live defect. It is on record here because the natural
    place to put an IFSF dispatcher is BEFORE the standard protocol's
    security check (IFSF being a different protocol with its own
    addressing), and at that moment two things become true at once:

      * database 01H element 7, Maint_Password, returned the LIVE console
        security code -- the value `wire._handle` compares against to
        decide whether to answer a serial command at all; and
      * a malformed DB_Ad raised out of `address()` rather than being
        rejected, which in a decoder fed a truncated frame is a crash.

    Both now fail closed, so wiring the transport is a decision rather than
    an inheritance.
    """

    def _console(self):
        c = a_console()
        c.values["S50400"] = "123456"
        c.set_setting("ifsf_platform", 1, 0)   # (key, value, device)
        self.assertTrue(ifsf.is_ifsf(c), "the IFSF gate is not even open")
        return c

    def test_the_password_element_is_blank_unless_the_caller_vouches(self):
        c = self._console()
        self.assertEqual(ifsf.read(c, ifsf.DB_TLG, 7), "",
                         "an unauthenticated read returned the security code")

    def test_an_authenticated_caller_still_gets_it(self):
        c = self._console()
        self.assertEqual(
            ifsf.read(c, ifsf.DB_TLG, 7, authenticated=True), "123456")

    def test_the_default_is_the_safe_one(self):
        """A transport that forgets the argument must get the blank."""
        import inspect
        sig = inspect.signature(ifsf.read)
        self.assertIs(sig.parameters["authenticated"].default, False)

    def test_a_malformed_address_is_rejected_not_raised(self):
        c = self._console()
        for bad in ((), b"", "ab", None, 3.5, [], (0x01,)):
            try:
                ifsf.read(c, bad, 1)
                ifsf.unsolicited(c, bad)
            except Exception as e:                      # noqa: BLE001
                self.fail("DB_Ad %r raised %s: %s"
                          % (bad, type(e).__name__, e))

    def test_ordinary_reads_are_unaffected(self):
        c = self._console()
        self.assertEqual(ifsf.read(c, ifsf.DB_TLG, 50), "VEEDER-ROOT")
        self.assertIsNotNone(ifsf.read(c, 0x21, 1))

    def test_it_is_still_off_on_a_console_nobody_ordered_it_for(self):
        self.assertIsNone(ifsf.read(a_console(), ifsf.DB_TLG, 50))


class AnElevatedNetshIsNotDrivenFromTheNetwork(unittest.TestCase):
    """`--claim-ip` adds `config.ip` to this machine's adapter, as admin.

    Both the setup menu and the web manager can write `config.ip`, so off
    the bench a stranger chose which IPv4 address an elevated process
    added to -- and removed from -- the host's default-route adapter: the
    subnet's gateway, say, or an address belonging to another host. It is
    not command injection (the arguments are a list, and an address is
    four bytes rendered as a dotted quad) but it is a privileged state
    change driven by remote input.

    The card still MOVES when it is reprogrammed; it simply is not
    claimed. `--card-ip` is how an exposed card gets a claimed address.
    """

    def _net(self, level, started_on="10.0.0.5"):
        cfg = xport.XPortConfig(None)
        cfg.program(ip=started_on)
        net = xportnet.CardNetwork(Console(None), cfg, claim=True,
                                   policy=exposed.Policy(level))
        return cfg, net

    def _claims(self, level, moved_to="203.0.113.99"):
        cfg, net = self._net(level)
        seen = []
        real = xportnet.claim_ip
        xportnet.claim_ip = lambda ip, mask, log=None: (seen.append(ip)
                                                        or "Eth")
        try:
            cfg.program(ip=moved_to)      # what a stranger's write does
            net._claim_now()
        finally:
            xportnet.claim_ip = real
        return seen

    def test_a_bench_still_claims_whatever_it_is_programmed_with(self):
        """On a bench the only person programming it is the operator."""
        self.assertEqual(self._claims(exposed.BENCH), ["203.0.113.99"])

    def test_a_network_card_will_not_claim_an_address_it_was_given(self):
        self.assertEqual(self._claims(exposed.LAN), [],
                         "a stranger steered an elevated netsh")

    def test_nor_will_an_exposed_one(self):
        self.assertEqual(self._claims(exposed.EXPOSED), [])

    def test_the_startup_address_is_still_claimed(self):
        """The guard must not stop `--card-ip` working."""
        cfg, net = self._net(exposed.EXPOSED, started_on="10.0.0.5")
        seen = []
        real = xportnet.claim_ip
        xportnet.claim_ip = lambda ip, mask, log=None: (seen.append(ip)
                                                        or "Eth")
        try:
            net._claim_now()
        finally:
            xportnet.claim_ip = real
        self.assertEqual(seen, ["10.0.0.5"],
                         "--card-ip with --claim-ip stopped working")


class ElevenBytesCannotKillTheConsole(unittest.TestCase):
    """`\\x01s60401nan` stored a non-finite number as a tank's capacity.

    The computer-format Set path returned its data verbatim for every
    non-date field, so "nan" was stored; `Console.limit` failed to unpack
    it as a packed IEEE float and fell back to `float(body)`, which
    happily produced `nan`. From there `int(nan)` raised inside the
    inventory builder and `round(nan)` inside `traffic.gauge()` -- and
    gauge runs from `console.tick()`, at the top of EVERY command.

    So one unauthenticated eleven-byte frame made the console answer
    `9999FF` to everything, for the life of the process. On a bench or LAN
    console the value was written to disk and outlived the restart. With
    the GUI attached, the Tk poll loop stopped rescheduling.

    Refusing is also the FAITHFUL answer: "nan" is not a packed IEEE
    float, and the field has a range.
    """

    POISON = (b"nan", b"inf", b"-inf", b"1e400", b"NaN", b"INF")
    # the numeric fields the poison reached: full volume, diameter, and
    # the float limits that share the same Set path
    CODES = (b"604", b"607", b"60A")

    def _fresh(self):
        c = a_console()
        return c, wire.Handler(c, False, None)

    def test_the_console_still_answers_after_every_poison(self):
        for code in self.CODES:
            for bad in self.POISON:
                c, h = self._fresh()
                h.handle(b"\x01s" + code + b"01" + bad + b"\r")
                reply = h.handle(b"\x01I20100")
                self.assertNotIn(b"9999FF", reply,
                                 "s%s01%s killed the console"
                                 % (code.decode(), bad.decode()))

    def test_the_poison_is_refused_rather_than_stored(self):
        for bad in self.POISON:
            c, h = self._fresh()
            h.handle(b"\x01s60401" + bad + b"\r")
            self.assertEqual(c.full_volume(1), 10000.0,
                             "%r was stored as a capacity" % bad)

    def test_limit_never_hands_out_a_non_finite_number(self):
        """The second layer, in case a value gets stored another way."""
        c, _h = self._fresh()
        for bad in ("nan", "inf", "-inf"):
            c.values["S60401"] = bad
            self.assertIsNone(c.limit("604", 1),
                              "limit returned %r" % bad)

    def test_an_ordinary_value_still_sets(self):
        """The guard must not refuse numbers the console can hold."""
        c, h = self._fresh()
        reply = h.handle(b"\x01S6040112000\r")
        self.assertNotIn(b"9999FF", reply)
        self.assertEqual(c.full_volume(1), 12000.0)

    def test_a_packed_float_still_sets(self):
        """Computer format packs a float as eight hex digits."""
        from tls350sim import packed
        c, h = self._fresh()
        body = packed.hexfloat(9500.0)
        reply = h.handle(b"\x01s60401" + body.encode("ascii") + b"\r")
        self.assertNotIn(b"9999FF", reply)
        self.assertAlmostEqual(c.full_volume(1), 9500.0, places=1)

    def test_the_formatter_survives_an_infinity(self):
        """`int(float('inf'))` raises OverflowError, which was not caught."""
        from tls350sim import masks
        self.assertEqual(masks.whole(float("inf"), 6).strip(), "0")
        self.assertEqual(masks.whole(float("nan"), 6).strip(), "0")


class TheTrackerKeyListsHaveACeiling(unittest.TestCase):
    """`S8A4` grew `blocked_keys` for ever, from the serial port.

    Every other list the console keeps is capped -- `alarm_log` at 200,
    `bir.events` at 40, `accuchart_log` at 40, `vp_cycles` at 20. This one
    was not, and the Set path reaches it with no authentication and, unlike
    the inquire side, with no `has("mt")` guard either: a console with no
    Maintenance Tracker card fitted still grew the list. The key space is
    36^6, and on a bench or LAN console every append also saves to disk.
    """

    IDS = ["%04d" % n for n in range(1200)]

    def test_blocked_keys_stops_at_the_cap(self):
        c = a_console()
        self.assertFalse(c.has("mt"), "this site should have no MT card")
        h = wire.Handler(c, False, None)
        for ident in self.IDS:
            h.handle(b"\x01S8A400149" + ident.encode("ascii"))
        self.assertEqual(len(c.blocked_keys), c.KEYS_KEPT,
                         "blocked_keys grew past its ceiling")

    def test_the_newest_keys_are_the_ones_kept(self):
        c = a_console()
        h = wire.Handler(c, False, None)
        for ident in self.IDS:
            h.handle(b"\x01S8A400149" + ident.encode("ascii"))
        kept = [k for k, _n in c.blocked_keys]
        self.assertIn(self.IDS[-1], kept, "the newest key was dropped")
        self.assertNotIn(self.IDS[0], kept, "the oldest key was kept")

    def test_an_ordinary_number_of_keys_is_untouched(self):
        c = a_console()
        h = wire.Handler(c, False, None)
        for ident in self.IDS[:5]:
            h.handle(b"\x01S8A400149" + ident.encode("ascii"))
        self.assertEqual(len(c.blocked_keys), 5)


class TheConsoleHeartbeatCannotBeStopped(unittest.TestCase):
    """`_poll` used to reschedule itself on its LAST line.

    Anything that raised on the way there took the poll loop with it: Tk
    prints the traceback and drops the callback, and nothing schedules
    another. The console's clock stops advancing, `compute_alarms()` never
    runs again, and the lamps, the alarm history and the alarm-driven
    shutdown relays freeze while the process sits there looking healthy.
    Silent, permanent, and indistinguishable from a quiet site.

    That is too much to rest on every line of `tick()` being correct, now
    that the same `tick()` is reachable from a card a stranger can talk to.

    These build ONE withdrawn Tk app -- a window per test exhausts Tk.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter
            tkinter.Tk().destroy()
        except Exception as e:                          # noqa: BLE001
            raise unittest.SkipTest("no display: %s" % e)
        from tls350sim.ui import SimApp
        cls.console = Console(None)
        presets.load(cls.console, "Two-tank retail site")
        cls.app = SimApp(cls.console, 10001)
        cls.app.withdraw()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        # Cancel the reschedule these tests left pending. Each `_poll()`
        # call arms another `after(700, ...)`, and without cancelling it
        # the callback fires once the window is gone -- Tk then prints
        # "invalid command name <id>_poll" from outside any test, with no
        # traceback to trace it by.
        # Cancel everything still queued, by asking Tk rather than by
        # asking the app. An `after` id lives in an attribute only for as
        # long as someone keeps it there, and a loop that re-arms itself
        # overwrites its own: anything orphaned in between is invisible to
        # a by-name sweep and fires later, into a destroyed interpreter,
        # printing `invalid command name` from inside whichever unrelated
        # test file happens to be driving Tk at the time. `after info` is
        # the only list that is actually complete.
        try:
            for ident in cls.app.tk.call("after", "info"):
                try:
                    cls.app.after_cancel(str(ident))
                except Exception:                       # noqa: BLE001
                    pass
            cls.app._poll_id = None
        except Exception:                               # noqa: BLE001
            pass
        try:
            cls.app.destroy()
        except Exception:                               # noqa: BLE001
            pass

    def tearDown(self):
        # put the real tick back for whatever runs next, and forget which
        # fault was last reported -- the "said once per kind" memory is per
        # app, and the app is shared by every test in this class
        try:
            del self.console.tick
        except AttributeError:
            pass
        self.app._poll_said = None
        self._disarm()

    def _disarm(self):
        """Cancel the pending tick and forget it -- in that order.

        Clearing `_poll_id` without cancelling first orphans the callback:
        nothing holds the id any more, so nothing can ever cancel it, and
        it fires later into a destroyed window.
        """
        pending = getattr(self.app, "_poll_id", None)
        if pending:
            try:
                self.app.after_cancel(pending)
            except Exception:                           # noqa: BLE001
                pass
        self.app._poll_id = None

    def _tick(self):
        """One poll, then take its reschedule straight back out of Tk.

        Each `_poll()` arms `after(700, ...)` and overwrites `_poll_id`
        with the new one, so calling it twice orphans the first: nothing
        holds its id any more and nothing can cancel it. Orphans do not
        show up here -- this file alone is silent, because the interpreter
        exits before they come due -- they show up as `invalid command
        name` on stderr once some LATER test file drives Tk long enough
        for them to fire, by which time this app is destroyed and the
        message has nothing to do with the test printing it.

        Returns the id that was armed, so a test can assert the loop
        rescheduled itself before it is cancelled.
        """
        self.app._poll()
        armed = getattr(self.app, "_poll_id", None)
        self._disarm()
        return armed

    def test_a_raising_tick_does_not_stop_the_loop(self):
        calls = []

        def exploding():
            calls.append(1)
            raise RuntimeError("dictionary changed size during iteration")

        self.console.tick = exploding
        self._disarm()   # cancel, never just forget: forgetting an id orphans it
        self.assertIsNotNone(self._tick(),
                             "the poll loop did not reschedule after a raise")
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(self._tick(), "the loop gave up on the second")
        self.assertEqual(len(calls), 2, "the loop stopped calling tick")

    def test_it_is_said_once_per_kind_not_once_per_tick(self):
        """700ms forever means a repeated fault must not flood the log."""
        def exploding():
            raise RuntimeError("same every time")

        self.console.tick = exploding
        said = []
        real = self.app.log
        self.app.log = lambda line: said.append(line)
        try:
            for _ in range(5):
                self._tick()
        finally:
            self.app.log = real
        self.assertEqual(len(said), 1,
                         "a repeating fault logged every tick: %r" % said)

    def test_a_healthy_tick_still_reschedules(self):
        self._disarm()   # cancel, never just forget: forgetting an id orphans it
        self.assertIsNotNone(self._tick())


if __name__ == "__main__":
    unittest.main(verbosity=2)
