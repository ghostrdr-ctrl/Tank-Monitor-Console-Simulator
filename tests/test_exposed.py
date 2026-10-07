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
import ntpath
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
from tls350sim import listen                                   # noqa: E402
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
        ("exposed.py", "_rotate"):
            "the capture file's own rotation, and the only thing that "
            "holds its quota -- freezing it would leave an EXPOSED "
            "console, the one that most needs the bound, unable to keep "
            "it. The paths are the operator's own with an integer suffix, "
            "so the network cannot steer them, and the only file it "
            "removes is the oldest capture this program wrote. "
            "FIDELITY R32",
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
        # the bench TLS-350's first byte, timed from its own subnet
        # (2026-09-19): 35 to 120 ms
        for d in seen:
            self.assertGreaterEqual(d, 0.035)
            self.assertLessEqual(d, 0.12)
        # and the rest at its line's pace, not in one burst
        self.assertAlmostEqual(hot.pace, 0.00125)
        self.assertIsNone(bench.pace)

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


class _Brick:
    """A file handle that takes the first N writes and then cannot.

    A full disk, in other words, or a volume pulled out from under the
    process: the handle is still open and every write from here on raises.
    """

    def __init__(self, ok=0, exc=None):
        self.left = ok
        self.exc = exc or OSError(28, "No space left on device")
        self.written = []
        self.closed = False

    def write(self, text):
        if self.left <= 0:
            raise self.exc
        self.left -= 1
        self.written.append(text)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class TheCaptureSaysWhenItStopsWriting(unittest.TestCase):
    """FIDELITY R34. `_put` counted the row, wrote it, and swallowed the
    `OSError`.

    So a full disk, a revoked permission or a removed volume stopped the
    recording while the counter climbed and the view went on scrolling --
    and a honeypot whose whole product is the capture then looks exactly
    the same as one having a quiet night. The state below is what tells
    the two apart.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cap.jsonl")
        self.said = []

    def a_capture(self, ok=0):
        cap = exposed.Capture(self.path, on_fault=self.said.append)
        # The real handle is closed directly rather than through `close()`,
        # which would latch `_closed` and make every fault below a
        # deliberate shutdown.
        cap._fh.close()
        cap._fh = _Brick(ok=ok)
        return cap

    def test_a_healthy_capture_says_nothing_and_reports_nothing(self):
        cap = self.a_capture(ok=10)
        cap.record("in", b"\x01I20100", peer="198.51.100.1")
        self.assertTrue(cap.healthy())
        self.assertIsNone(cap.stats()["fault"])
        self.assertEqual(cap.stats()["unwritten"], 0)
        self.assertEqual(self.said, [])

    def test_a_write_that_fails_is_counted_as_lost_not_as_recorded(self):
        cap = self.a_capture()
        cap.record("in", b"\x01I20100", peer="198.51.100.1")
        st = cap.stats()
        self.assertEqual(st["total"], 1, "the exchange still happened")
        self.assertEqual(st["unwritten"], 1, "and it is not on the disk")
        self.assertIn("No space left", st["fault"])
        self.assertFalse(cap.healthy())

    def test_dropped_and_unwritten_are_not_the_same_number(self):
        """`dropped` fell off the view's ring and is safe on the disk.
        `unwritten` never reached the disk at all. Showing one as the other
        is how a silent outage stays silent."""
        cap = exposed.Capture(ring=2, on_fault=self.said.append)
        for i in range(6):
            cap.record("in", bytes([i]), peer="1.1.1.1")
        st = cap.stats()
        self.assertTrue(st["dropped"])
        self.assertEqual(st["unwritten"], 0)
        self.assertIsNone(st["fault"])

    def test_it_is_said_once_per_outage_and_not_once_per_row(self):
        cap = self.a_capture()
        for _ in range(50):
            cap.record("in", b"junk", peer="198.51.100.1")
        self.assertEqual(cap.stats()["unwritten"], 50)
        self.assertEqual(len(self.said), 1,
                         "a full disk fails on every write; one line, not 50")

    def test_recovery_is_said_too(self):
        """An operator who freed the disk needs to know the capture came
        back, or the fix reads as having done nothing."""
        cap = self.a_capture()
        cap.record("in", b"a", peer="198.51.100.1")
        self.assertEqual(len(self.said), 1)
        cap._fh = _Brick(ok=5)
        cap.record("in", b"b", peer="198.51.100.1")
        self.assertEqual(self.said[1], None)
        self.assertIsNone(cap.stats()["fault"])
        self.assertEqual(cap.stats()["unwritten"], 1,
                         "the row lost in the outage is still lost")

    def test_a_second_outage_is_said_again(self):
        cap = self.a_capture()
        cap.record("in", b"a", peer="1.1.1.1")
        cap._fh = _Brick(ok=1)
        cap.record("in", b"b", peer="1.1.1.1")     # recovers
        cap.record("in", b"c", peer="1.1.1.1")     # and fails again
        self.assertEqual(len(self.said), 3)
        self.assertIsNotNone(self.said[0])
        self.assertIsNone(self.said[1])
        self.assertIsNotNone(self.said[2])

    def test_a_path_that_cannot_be_opened_faults_at_the_start(self):
        """The one outage the operator is standing there for."""
        bad = os.path.join(self.path, "under-a-file", "cap.jsonl")
        open(self.path, "w").close()
        cap = exposed.Capture(bad, on_fault=self.said.append)
        self.assertIsNotNone(cap.fault)
        self.assertIn("cannot open", cap.fault)
        self.assertEqual(len(self.said), 1)

    def test_rows_are_counted_as_lost_while_the_file_will_not_open(self):
        bad = os.path.join(self.path, "under-a-file", "cap.jsonl")
        open(self.path, "w").close()
        cap = exposed.Capture(bad, on_fault=self.said.append)
        cap.record("in", b"\x01I20100", peer="1.1.1.1")
        cap.record("in", b"\x01I20200", peer="1.1.1.1")
        self.assertEqual(cap.stats()["unwritten"], 2,
                         "an outage reporting nothing lost is the same lie")

    def test_a_ring_only_capture_is_not_a_fault(self):
        """No path was named, so nothing was promised the disk."""
        cap = exposed.Capture(on_fault=self.said.append)
        cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.assertTrue(cap.healthy())
        self.assertEqual(self.said, [])

    def test_a_notifier_that_raises_cannot_kill_the_listener(self):
        def angry(_why):
            raise RuntimeError("the view fell over")
        cap = exposed.Capture(self.path, on_fault=angry)
        cap._fh = _Brick()
        cap.record("in", b"\x01I20100", peer="1.1.1.1")     # must not raise
        self.assertIsNotNone(cap.fault)

    def test_closing_on_purpose_is_not_an_outage(self):
        """`close()` runs while the listener threads are still alive, so a
        row already past the lock lands on a closed handle. Announcing that
        would end every clean run with a fault it invented itself."""
        cap = exposed.Capture(self.path, on_fault=self.said.append)
        fh = cap._fh
        cap.close()
        cap._fh = fh                    # the row that was already in flight
        cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.assertIsNone(cap.fault)
        self.assertEqual(self.said, [])

    def test_a_capture_reopened_after_a_close_can_fault_again(self):
        """The counterpart: the silence is for the close, not for ever."""
        cap = exposed.Capture(self.path, on_fault=self.said.append)
        cap.close()
        second = os.path.join(self.dir, "next.jsonl")
        cap.enable(True, second)
        cap.close()
        cap._fh = _Brick()
        cap._closed = False             # as `_open` leaves it
        cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.assertIsNotNone(cap.fault)
        self.assertEqual(len(self.said), 1)

    def test_a_path_that_will_not_open_on_enable_still_says_so(self):
        """`enable` closes the old file before opening the new one, and
        the fault from the failed open must survive that close."""
        cap = exposed.Capture(self.path, on_fault=self.said.append)
        blocker = os.path.join(self.dir, "wall")
        open(blocker, "w").close()
        cap.enable(True, os.path.join(blocker, "under-a-file", "cap.jsonl"))
        self.assertIsNotNone(cap.fault)
        self.assertIn("cannot open", cap.fault)
        self.assertEqual(len(self.said), 1)

    def test_the_view_still_sees_the_rows_it_could_not_write(self):
        """The ring is memory and the outage is the disk's. What arrived
        during one is still worth showing."""
        cap = self.a_capture()
        cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.assertEqual(len(cap.rows()), 1)


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
        # each header line padded to its twenty, as the bench sends it
        self.assertIn(b"LINE ONE            \r\nLINE TWO", reply)


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


class TheCaptureSeesTheTrafficWorthSeeing(unittest.TestCase):
    """FIDELITY R28.

    `xportweb` recorded a request only after its `Content-Length` had been
    validated, so the malformed ones -- the ones a scanner sends -- were
    refused with a 400 and recorded nowhere: `Content-Length: -1` produced
    an answer and zero capture rows. HTTP responses were not captured at
    all, and neither was anything the setup menu printed.
    """

    def setUp(self):
        self.cap = exposed.Capture()
        self.cfg = xport.XPortConfig()
        self.srv = xportweb._Server(("127.0.0.1", 0), self.cfg,
                                    capture=self.cap)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def raw(self, request):
        s = socket.create_connection(("127.0.0.1", self.port), 5)
        s.sendall(request)
        out = b""
        try:
            while True:
                b = s.recv(4096)
                if not b:
                    break
                out += b
        except OSError:
            pass
        s.close()
        time.sleep(0.15)
        return out

    def rows(self, direction):
        return [r for r in self.cap.rows() if r["dir"] == direction]

    def test_a_malformed_length_is_recorded_and_not_only_refused(self):
        out = self.raw(b"POST /secure/setup.cgi HTTP/1.0\r\n"
                       b"Content-Length: -1\r\n\r\n")
        self.assertIn(b"400", out, "it was not refused at all")
        ins = self.rows("in")
        self.assertTrue(ins, "the refused request was recorded nowhere")
        self.assertIn("not a count", ins[0].get("note") or "")
        self.assertIn(b"Content-Length: -1", bytes.fromhex(ins[0]["raw"]))

    def test_an_over_long_form_is_recorded_too(self):
        self.raw(b"POST /secure/setup.cgi HTTP/1.0\r\n"
                 b"Content-Length: 999999999\r\n\r\n")
        ins = self.rows("in")
        self.assertTrue(ins)
        self.assertIn("over the", ins[0].get("note") or "")

    def test_the_response_is_captured_as_well_as_the_request(self):
        """The file held one side of every exchange, so a reader could not
        tell a 404 from a served page."""
        self.raw(b"GET /nope HTTP/1.0\r\n\r\n")
        outs = self.rows("out")
        self.assertTrue(outs, "no HTTP response was ever captured")
        body = bytes.fromhex(outs[-1]["raw"])
        self.assertIn(b"404", body)
        self.assertIn(b"ERROR 404", body)
        self.assertEqual(outs[-1]["cmd"], "404")

    def test_the_401_is_captured(self):
        """The most interesting response this server sends: what a scanner
        meets before it starts guessing."""
        self.cfg.telnet_password = "ABCD"
        self.raw(b"GET /secure/ltx_conf.htm HTTP/1.0\r\n\r\n")
        outs = self.rows("out")
        self.assertTrue(outs)
        self.assertEqual(outs[-1]["cmd"], "401")
        self.assertIn(b"WWW-Authenticate", bytes.fromhex(outs[-1]["raw"]))

    def test_an_ordinary_page_still_records_both_directions(self):
        self.raw(b"GET / HTTP/1.0\r\n\r\n")
        self.assertTrue(self.rows("in"))
        self.assertTrue(self.rows("out"))
        self.assertEqual(self.rows("in")[0]["cmd"], "GET /")


class TheSetupMenuRecordsWhatItPrints(unittest.TestCase):
    """FIDELITY R28's third part. The capture held everything a stranger
    typed at the setup menu and nothing the menu said back."""

    def test_the_banner_and_the_prompt_are_captured(self):
        cap = exposed.Capture()
        cfg = xport.XPortConfig()
        port = free_port()
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            threading.Thread(
                target=lambda: xportmenu._session(
                    cfg, srv.accept()[0], None, peer=("198.51.100.5", 4444),
                    capture=cap),
                daemon=True).start()
            s = socket.create_connection(("127.0.0.1", port), 5)
            s.settimeout(3)
            s.sendall(b"\r\n")
            time.sleep(0.5)
            try:
                s.recv(4096)
            except OSError:
                pass
            s.close()
        finally:
            srv.close()
        time.sleep(0.3)
        outs = [r for r in cap.rows() if r["dir"] == "out"]
        self.assertTrue(outs, "the menu printed and nothing was recorded")
        printed = b"".join(bytes.fromhex(r["raw"]) for r in outs)
        self.assertTrue(printed.strip(), "the recorded output was empty")


class AStoppedListenerStopsAnswering(unittest.TestCase):
    """FIDELITY R30.

    Every listener here was stopped by closing its socket from another
    thread, and on Linux a thread already blocked in `accept()` is not
    woken by that: the call stays blocked until a connection arrives, and
    then it is served -- by a listener that was shut down. Reproduced by
    connecting after `stop()` had returned and pulling inventory, and
    getting it.
    """

    def setUp(self):
        self.console = a_console()

    def test_the_tunnel_refuses_after_it_is_stopped(self):
        stop = threading.Event()
        port = free_port()
        holder = {}
        threading.Thread(
            target=wire.serve,
            args=(self.console, "127.0.0.1", port, False, None),
            kwargs={"on_socket": lambda s: holder.__setitem__("srv", s),
                    "running": lambda: not stop.is_set()},
            daemon=True).start()
        for _ in range(100):
            if "srv" in holder:
                break
            time.sleep(0.02)
        self.assertIn("srv", holder, "the tunnel never came up")

        # It answers while it is running.
        s = socket.create_connection(("127.0.0.1", port), 5)
        s.recv(len(wire.PROBE))
        s.sendall(b"\x01I20100")
        self.assertTrue(s.recv(4096), "it was not answering to begin with")
        s.close()

        # Now stop it, the way `CardNetwork.stop` does, and wait out one
        # tick of the listener's own loop.
        stop.set()
        time.sleep(listen.TICK * 4)

        served = False
        try:
            s = socket.create_connection(("127.0.0.1", port), 2)
            s.settimeout(2)
            s.sendall(b"\x01I20100")
            served = bool(s.recv(4096))
            s.close()
        except OSError:
            served = False          # refused or reset, which is the point
        self.assertFalse(served,
                         "a stopped tunnel answered a command")

    def test_a_connection_in_the_gap_is_closed_unanswered(self):
        """"Stop the listener" has to mean "refuse what arrives after it"
        as well as "stop waiting", and there is always a gap between."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        stopped = {"yes": False}
        out = {}

        def loop():
            out["got"] = listen.accept(srv, lambda: not stopped["yes"])

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        time.sleep(0.05)
        stopped["yes"] = True                   # stopped, then knocked on
        c = socket.create_connection(srv.getsockname(), 2)
        t.join(5)
        self.assertIsNone(out.get("got"),
                          "a stopped listener took the connection")
        c.close()
        srv.close()

    def test_a_closed_socket_ends_the_wait_instead_of_raising(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.close()
        self.assertFalse(listen.wait_readable(srv, timeout=0.05))
        self.assertIsNone(listen.accept(srv, timeout=0.05))

    def test_the_predicate_is_asked_and_not_only_the_socket(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        asked = {"n": 0}

        def running():
            asked["n"] += 1
            return asked["n"] < 3
        self.assertIsNone(listen.accept(srv, running, timeout=0.01))
        self.assertGreaterEqual(asked["n"], 3, "it never re-asked")
        srv.close()

    def test_with_no_predicate_it_still_waits_like_an_accept(self):
        """Every existing caller hands none, and must be unaffected."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        out = {}
        t = threading.Thread(target=lambda: out.__setitem__(
            "got", listen.accept(srv)), daemon=True)
        t.start()
        c = socket.create_connection(srv.getsockname(), 2)
        t.join(5)
        self.assertIsNotNone(out.get("got"), "an ordinary accept was refused")
        out["got"][0].close()
        c.close()
        srv.close()


class TheCaptureIsBounded(unittest.TestCase):
    """FIDELITY R32.

    Every byte that arrives is stored hex-encoded, two characters a byte
    before the JSON around it: 2.12 bytes on disk for every byte received,
    so a connection sending junk filled the disk FASTER than it sent. There
    was no size limit on the file, no rotation, no cap on one connection's
    share and no rate on the bytes coming in -- only on the connections,
    which a flood down one accepted connection never touches.

    Hex is the right storage and the fix is not to decode it.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cap.jsonl")

    def files(self):
        return sorted(n for n in os.listdir(self.dir) if n.startswith("cap."))

    def test_the_file_rotates_at_its_quota(self):
        cap = exposed.Capture(self.path, max_bytes=4096, keep=2)
        for i in range(400):
            cap.record("in", bytes([i % 256]) * 64, peer="198.51.100.1")
        cap.close()
        self.assertTrue(cap.rotations, "it never rotated")
        self.assertIn("cap.jsonl", self.files())
        self.assertIn("cap.jsonl.1", self.files())

    def test_nothing_grows_without_end(self):
        """The whole point: bytes on disk stay bounded however many arrive."""
        cap = exposed.Capture(self.path, max_bytes=4096, keep=2)
        for i in range(3000):
            cap.record("in", b"\xde\xad\xbe\xef" * 64, peer="198.51.100.1")
        cap.close()
        total = sum(os.path.getsize(os.path.join(self.dir, n))
                    for n in self.files())
        self.assertLess(total, 4096 * 5,
                        "the quota did not bound the disk: %d bytes in %s"
                        % (total, self.files()))

    def test_only_keep_old_files_survive(self):
        cap = exposed.Capture(self.path, max_bytes=2048, keep=2)
        for i in range(2000):
            cap.record("in", b"x" * 64, peer="198.51.100.1")
        cap.close()
        self.assertEqual(self.files(),
                         ["cap.jsonl", "cap.jsonl.1", "cap.jsonl.2"])

    def test_the_old_file_is_renamed_and_not_truncated(self):
        """A capture is evidence. The quota bounds the disk; it does not
        decide that the last minute was the interesting one."""
        cap = exposed.Capture(self.path, max_bytes=2048, keep=2)
        for _ in range(60):
            cap.record("in", b"y" * 64, peer="198.51.100.1")
        cap.close()
        older = os.path.join(self.dir, "cap.jsonl.1")
        self.assertTrue(os.path.exists(older))
        self.assertTrue(os.path.getsize(older) > 0,
                        "the rotated file was emptied instead of kept")
        with open(older, encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
        self.assertTrue(rows)
        self.assertEqual(bytes.fromhex(rows[0]["raw"]), b"y" * 64)

    def test_a_restart_counts_what_is_already_in_the_file(self):
        """Seeded from zero, a process restarted every few minutes would
        append past the quota for ever."""
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("x" * 5000)
        cap = exposed.Capture(self.path, max_bytes=4096, keep=1)
        self.assertGreaterEqual(cap._written, 5000)
        cap.record("in", b"z", peer="1.1.1.1")
        self.assertTrue(cap.rotations, "it appended past the quota")
        cap.close()

    def test_no_quota_still_means_no_quota(self):
        cap = exposed.Capture(self.path, max_bytes=None)
        for _ in range(200):
            cap.record("in", b"q" * 64, peer="1.1.1.1")
        cap.close()
        self.assertEqual(cap.rotations, 0)
        self.assertEqual(self.files(), ["cap.jsonl"])

    def test_the_default_is_a_quota_and_not_none(self):
        """A bound nobody switches on is not a bound."""
        cap = exposed.Capture(self.path)
        self.assertIsNotNone(cap.max_bytes)
        self.assertGreater(cap.keep, 0)
        cap.close()


class OneConnectionCannotCrowdOutTheRest(unittest.TestCase):
    """FIDELITY R32's second half: a cap on one connection's share."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cap.jsonl")
        self.cap = exposed.Capture(self.path)
        self.cap.MAX_SESSION_BYTES = 1024

    def test_the_bytes_stop_and_the_rows_do_not(self):
        s = self.cap.session(peer="198.51.100.1", port=4444)
        for _ in range(40):
            s.record("in", b"j" * 64)
        rows = self.cap.rows()
        self.assertEqual(len(rows), 40, "the rows stopped, not just the hex")
        self.assertTrue(s.truncated)
        self.assertEqual(sum(len(bytes.fromhex(r["raw"])) for r in rows),
                         1024, "more than the budget was stored")

    def test_the_length_is_still_the_truth(self):
        """The volume of a flood is the part worth keeping."""
        s = self.cap.session(peer="198.51.100.1", port=4444)
        for _ in range(40):
            s.record("in", b"j" * 64)
        rows = self.cap.rows()
        self.assertEqual(sum(r["bytes"] for r in rows), 40 * 64)
        self.assertEqual(s.bytes, 40 * 64)

    def test_it_says_once_where_the_line_was_drawn(self):
        s = self.cap.session(peer="198.51.100.1", port=4444)
        for _ in range(40):
            s.record("in", b"j" * 64)
        notes = [r for r in self.cap.rows() if r.get("note")]
        self.assertEqual(len(notes), 1)
        self.assertIn("storing lengths only", notes[0]["note"])

    def test_another_connection_has_its_own_budget(self):
        """The point of a PER-CONNECTION cap: a flooder must not spend
        anybody else's share."""
        flood = self.cap.session(peer="198.51.100.1", port=4444)
        for _ in range(40):
            flood.record("in", b"j" * 64)
        polite = self.cap.session(peer="203.0.113.9", port=5555)
        polite.record("in", b"\x01I20100")
        self.assertFalse(polite.truncated)
        last = self.cap.rows()[-1]
        self.assertEqual(bytes.fromhex(last["raw"]), b"\x01I20100")
        self.assertEqual(last["cmd"], "I20100")

    def test_an_ordinary_session_is_stored_whole(self):
        s = self.cap.session(peer="203.0.113.9", port=5555)
        s.record("in", b"\x01I20100")
        s.record("out", b"\x01ok\x03")
        self.assertFalse(s.truncated)
        self.assertEqual([bytes.fromhex(r["raw"]) for r in self.cap.rows()],
                         [b"\x01I20100", b"\x01ok\x03"])


class TheBytesAreRatedAndNotOnlyTheConnections(unittest.TestCase):
    """FIDELITY R32's third half, on a real socket.

    Counting connections is no limit on a flood that arrives down ONE of
    them, and the response delay does not slow it either, because that
    delay is per command and junk never becomes one.
    """

    def setUp(self):
        self.console = a_console()

    def test_a_flood_down_one_connection_is_cut_off(self):
        cap = exposed.Capture(ring=4000)
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        policy._delay = None            # the delay is not what is measured
        policy.pace = None
        policy.max_bytes_per_min = 8192
        gate = exposed.Gate(policy, capture=cap)
        with _Served(self.console, policy=policy, capture=cap,
                     gate=gate) as served:
            s = served.connect()
            sent = 0
            try:
                for _ in range(200):
                    s.sendall(b"\x00" * 1024)
                    sent += 1024
            except OSError:
                pass                    # the far end hung up, which is the point
            time.sleep(0.6)
            s.close()
        notes = [r for r in cap.rows() if "byte rate" in (r.get("note") or "")]
        self.assertTrue(notes, "the flood was never cut off")
        stored = sum(r["bytes"] for r in cap.rows() if r["dir"] == "in")
        self.assertLessEqual(stored, 8192 + 4096,
                             "more than the budget was taken in")

    def test_an_ordinary_poller_is_untouched(self):
        cap = exposed.Capture()
        policy = exposed.Policy(exposed.EXPOSED, capture=cap)
        policy._delay = None
        policy.pace = None
        gate = exposed.Gate(policy, capture=cap)
        with _Served(self.console, policy=policy, capture=cap,
                     gate=gate) as served:
            s = served.connect()
            for _ in range(20):
                s.sendall(b"\x01I20100")
                self.assertTrue(s.recv(4096), "the console stopped answering")
            s.close()
        self.assertFalse([r for r in cap.rows()
                          if "byte rate" in (r.get("note") or "")])

    def test_a_bench_tunnel_has_no_byte_limit(self):
        self.assertIsNone(exposed.Policy(exposed.BENCH).max_bytes_per_min)
        self.assertIsNotNone(exposed.Policy(exposed.EXPOSED).max_bytes_per_min)

    def test_the_exposed_budget_is_under_what_the_hardware_could_take(self):
        """A real card reads a 9600 baud line: 72,000 bytes a minute is its
        physical ceiling, so a limit above that is not a limit."""
        self.assertLess(exposed.Policy(exposed.EXPOSED).max_bytes_per_min,
                        72000)


class TheSourceTablesHaveACeilingAndNotAThreshold(unittest.TestCase):
    """FIDELITY R33.

    `datagram_ok` swept its table when it passed 4,096, and a sweep only
    removed addresses whose timestamps had aged out. Under source churn
    nothing had aged, so nothing was removed, the table went on growing --
    6,000 retained past a threshold of 4,096 -- and the O(n) scan ran again
    on the next packet. Both the memory and the CPU were the attacker's to
    spend, and a UDP source address is whatever the sender wrote, so the
    churn costs them nothing.
    """

    def a_gate(self, level=exposed.EXPOSED):
        self.t = 1000.0
        pol = exposed.Policy(level)
        return exposed.Gate(pol, clock=lambda: self.t)

    def test_the_datagram_table_stops_at_its_ceiling(self):
        g = self.a_gate()
        g.DATAGRAM_RATE_ALL = 10 ** 9       # the ceiling, not the rate
        for i in range(exposed.Gate.SOURCES + 2000):
            g.datagram_ok("10.%d.%d.%d" % (i >> 16 & 255, i >> 8 & 255,
                                           i & 255))
        self.assertLessEqual(len(g._datagrams), exposed.Gate.SOURCES)
        self.assertTrue(g.sources_evicted)

    def test_the_oldest_source_is_the_one_that_goes(self):
        g = self.a_gate()
        g.DATAGRAM_RATE_ALL = 10 ** 9
        g.SOURCES = 4
        for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"):
            g.datagram_ok(ip)
        g.datagram_ok("1.1.1.1")            # touched, so no longer oldest
        g.datagram_ok("5.5.5.5")            # over the ceiling
        self.assertNotIn("2.2.2.2", g._datagrams, "the oldest should go")
        self.assertIn("1.1.1.1", g._datagrams, "a touched source should stay")
        self.assertIn("5.5.5.5", g._datagrams)
        self.assertEqual(len(g._datagrams), 4)

    def test_a_forged_flood_is_bounded_by_the_global_rate(self):
        """The per-source rate is no limit at all when the source is
        forged: a new address on every packet is under its own allowance
        for ever. This is what actually stops a reflector."""
        g = self.a_gate()
        answered = sum(1 for i in range(5000)
                       if g.datagram_ok("10.%d.%d.%d"
                                        % (i >> 16 & 255, i >> 8 & 255,
                                           i & 255)))
        self.assertEqual(answered, exposed.Gate.DATAGRAM_RATE_ALL)
        self.assertEqual(len(g._datagrams), answered,
                         "a refused datagram must not buy a table entry")

    def test_the_global_window_moves(self):
        g = self.a_gate()
        for i in range(exposed.Gate.DATAGRAM_RATE_ALL):
            self.assertTrue(g.datagram_ok("10.0.0.%d" % (i % 250)))
        self.assertFalse(g.datagram_ok("10.9.9.9"))
        self.t += 61.0
        self.assertTrue(g.datagram_ok("10.9.9.9"),
                        "the window never reopened")

    def test_one_polite_source_is_still_rate_limited_on_its_own(self):
        """The global rate must not have replaced the per-source one."""
        g = self.a_gate()
        ok = sum(1 for _ in range(100) if g.datagram_ok("198.51.100.1"))
        self.assertEqual(ok, exposed.Gate.DATAGRAM_RATE)

    def test_a_bench_card_is_not_bounded_at_all(self):
        g = self.a_gate(exposed.BENCH)
        self.assertTrue(all(g.datagram_ok("198.51.100.1")
                            for _ in range(1000)))
        self.assertFalse(g._datagrams, "a bench gate should not even tally")

    def test_the_connection_table_has_the_same_ceiling(self):
        """A TCP source cannot be forged, so this table grows far slower --
        but the defect and the fix are the same shape."""
        g = self.a_gate()
        g.policy.rate_per_min = 10 ** 9
        g.policy.max_total = 10 ** 9
        g.policy.max_per_ip = 10 ** 9
        g.SOURCES = 8
        for i in range(200):
            ip = "10.0.%d.%d" % (i >> 8 & 255, i & 255)
            g.admit(ip)
            g.release(ip)
        self.assertLessEqual(len(g._recent), 8)

    def test_a_source_holding_a_connection_keeps_its_rate_history(self):
        """Evicting it would let one held connection buy an unlimited rate
        on all the others."""
        g = self.a_gate()
        g.policy.rate_per_min = 10 ** 9
        g.policy.max_total = 10 ** 9
        g.policy.max_per_ip = 10 ** 9
        g.SOURCES = 4
        g.admit("198.51.100.7")             # held, and never released
        for i in range(100):
            ip = "10.0.%d.%d" % (i >> 8 & 255, i & 255)
            g.admit(ip)
            g.release(ip)
        self.assertIn("198.51.100.7", g._recent)


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
            safe = ntpath.basename(name or "").strip() or "update"
            if safe in (".", ".."):
                safe = "update"
            self.assertEqual(safe, want, "%r -> %r" % (name, safe))

    def test_the_source_still_does_it(self):
        """The logic above mirrors `update.download`; this checks the file
        has not drifted away from it."""
        src = open(os.path.join(os.path.dirname(PKG), "tls350sim",
                                "update.py"), encoding="utf-8").read()
        self.assertIn("ntpath.basename(release.installer_name", src,
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
