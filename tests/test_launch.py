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
"""What `run.py` arms on the way in, and gives back on the way out.

Two defects, both of them the launcher's rather than any console's, and
both invisible from inside the simulator:

`--capture FILE` recorded nothing unless the exposure had been raised
(FIDELITY R29). The recorder was armed from the LEVEL alone, so a path
typed out in full on a bench run built a disarmed object and did not even
create the file -- and nothing said so.

Nothing ever called `CardNetwork.stop()` (FIDELITY R27). `--claim-ip` runs
an elevated `netsh` to add the card's address to this machine's adapter and
`stop()` is the only thing that takes it off again; the window's path and
the headless interrupt both ran off the end of `main` instead.

Nothing here binds to anything, starts a window, or touches the operator's
own state or card configuration: the socket server and the card's network
are both stood in for.
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run                                                      # noqa: E402
from tls350sim import exposed                                   # noqa: E402
from tls350sim.console import Console                           # noqa: E402

# The window half of R29 and R34 needs a real `SimApp`, which needs a real
# Tk. It skips itself where there is none, the way `test_panel.py` does.
#
# `bench` is imported HERE and not at the top, because it does `import
# tkinter` at module level: imported up there it would turn this whole
# file into an import error on a machine without Tk, taking the fifteen
# tests below that have nothing to do with the window down with it. Which
# is the same shape as the bug this file exists for -- something that only
# shows itself on the machine that has not got the thing.
try:
    import tkinter
    tkinter.Tk().destroy()
    from tls350sim import bench
    HAVE_TK = True
except Exception:                                   # pragma: no cover
    bench = None
    HAVE_TK = False


class _Brick:
    """A file handle that takes the first N writes and then cannot."""

    def __init__(self, ok=0):
        self.left = ok
        self.written = []

    def write(self, text):
        if self.left <= 0:
            raise OSError(28, "No space left on device")
        self.left -= 1
        self.written.append(text)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeCard:
    """A `CardNetwork` that records its own lifecycle and binds nothing."""

    made = []

    def __init__(self, *a, **kw):
        self.started = False
        self.stops = 0
        _FakeCard.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stops += 1

    def bind_host(self):
        return ("127.0.0.1", "")


class _Launch(unittest.TestCase):
    """Drive `run.main()` with the network and the window stood in for."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state.json")
        self.capture_path = os.path.join(self.dir, "cap.jsonl")
        self.card_cfg = os.path.join(self.dir, "xport.json")
        _FakeCard.made = []
        self.served = {}

    def _serve(self, console, host, port, verbose=True, log=None, **kw):
        """Stand in for `wire.serve`, which would otherwise never return."""
        self.served.update(kw)
        self.served["host"] = host
        cap = kw.get("capture")
        if cap is not None:
            cap.record("in", b"\x01I20100", peer="198.51.100.9", port=4444)

    def launch(self, *argv, serve=None, interrupt=False, headless=True):
        argv = (["run.py"] + (["--headless"] if headless else [])
                + ["--quiet", "--state", self.state] + list(argv))
        sleep = mock.Mock(side_effect=KeyboardInterrupt) if interrupt \
            else mock.DEFAULT
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(run, "serve", serve or self._serve), \
                mock.patch.object(run.xportnet, "CardNetwork", _FakeCard), \
                mock.patch.object(run.paths, "xport_config_file",
                                  lambda: self.card_cfg), \
                mock.patch.object(run.time, "sleep", sleep):
            run.main()

    def rows(self):
        if not os.path.exists(self.capture_path):
            return None
        with open(self.capture_path, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]


class ACapturePathIsARequestForACapture(_Launch):
    """FIDELITY R29."""

    def test_a_named_path_records_on_a_bench(self):
        self.launch("--capture", self.capture_path)
        rows = self.rows()
        self.assertIsNotNone(rows, "--capture did not even create the file")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cmd"], "I20100")
        self.assertEqual(rows[0]["src"], "198.51.100.9")

    def test_the_recorder_the_listeners_hold_is_the_armed_one(self):
        """One object, handed to the listeners once and kept. Arming a
        different one is the bug `_apply_exposure` already carries a
        comment about."""
        self.launch("--capture", self.capture_path)
        cap = self.served["capture"]
        self.assertIsInstance(cap, exposed.Capture)
        self.assertTrue(cap.enabled)
        self.assertEqual(cap.path, self.capture_path)

    def test_a_bench_run_that_asked_for_nothing_still_records_nothing(self):
        """The default has not moved: no path, no file, no recording."""
        self.launch()
        self.assertFalse(self.served["capture"].enabled)
        self.assertIsNone(self.rows())

    def test_the_bench_capture_is_healthy_not_merely_armed(self):
        self.launch("--capture", self.capture_path)
        self.assertTrue(self.served["capture"].healthy())

    def test_an_exposed_run_still_captures_without_being_asked(self):
        """The behaviour that was already right, held in place.

        The freeze is a process-wide latch, so the `finally` has to cover
        the launch and not only the assertions: leaking it would silently
        freeze every test that runs after this one in the same process.
        """
        try:
            with mock.patch.object(run.paths, "default_capture_file",
                                   lambda: self.capture_path):
                self.launch("--exposure", "exposed")
            self.assertTrue(self.served["capture"].enabled)
            self.assertIsNotNone(self.rows())
        finally:
            exposed.freeze_writes(False)


class LeavingGivesBackWhatWasClaimed(_Launch):
    """FIDELITY R27."""

    def test_the_headless_interrupt_stops_the_card(self):
        self.launch("--xport", "--claim-ip", interrupt=True)
        self.assertEqual(len(_FakeCard.made), 1)
        card = _FakeCard.made[0]
        self.assertTrue(card.started)
        self.assertEqual(card.stops, 1,
                         "the card was left holding its address")

    def test_the_tunnel_returning_closes_the_capture(self):
        """`serve` returns when its listening socket is closed, which is
        every headless run that is not interrupted. The handle has to be
        open while it serves and shut behind it -- asserting only that it
        is shut afterwards would pass on a capture that never opened."""
        held = {}

        def watch(console, host, port, verbose=True, log=None, **kw):
            self._serve(console, host, port, verbose, log, **kw)
            held["fh"] = kw["capture"]._fh
        self.launch("--capture", self.capture_path, serve=watch)
        self.assertIsNotNone(held["fh"], "it was never writing at all")
        self.assertTrue(held["fh"].closed)
        self.assertIsNone(self.served["capture"]._fh)

    def test_an_exception_on_the_way_out_still_hands_things_back(self):
        """A crash is exactly when something gets left behind, because
        nothing after it runs. The `finally` is the whole of the fix.

        Run at LAN, where the capture is armed by the level rather than by
        the path, so what this measures is the `finally` and not R29.
        """
        def angry(console, host, port, verbose=True, log=None, **kw):
            self._serve(console, host, port, verbose, log, **kw)
            raise RuntimeError("the socket fell over")
        with self.assertRaises(RuntimeError):
            self.launch("--exposure", "lan", "--capture", self.capture_path,
                        serve=angry)
        cap = self.served["capture"]
        self.assertTrue(cap.enabled)
        self.assertIsNone(cap._fh, "the capture file was left open")
        self.assertEqual(len(self.rows()), 1)


class TheWindowGivesItBackToo(_Launch):
    """FIDELITY R27, on the path a person actually closes the program by.

    `main` imports `SimApp` where it uses it, so the whole window can be
    stood in for by putting a module in front of it. That is deliberate
    rather than lazy: patching the real `tls350sim.ui` would need tkinter
    installed, these tests would SKIP on a build without it, and a silent
    skip is coverage that goes away without saying so. Nothing here wants
    a window -- only the paths either side of `mainloop`.
    """

    def _windowed(self, app_class, *argv):
        stand_in = types.ModuleType("tls350sim.ui")
        stand_in.SimApp = app_class
        with mock.patch.dict(sys.modules, {"tls350sim.ui": stand_in}):
            self.launch(*argv, headless=False)

    def test_closing_the_window_stops_the_card(self):
        class FakeApp:
            def __init__(self, *a, **kw):
                self._card_net = None

            def attach_card(self, cfg, net):
                self._card_net = net

            def mainloop(self):
                pass

            def log(self, *a):
                pass
        self._windowed(FakeApp, "--xport")
        self.assertEqual(len(_FakeCard.made), 1)
        self.assertEqual(_FakeCard.made[0].stops, 1)

    def test_a_card_the_bench_menu_started_is_stopped_as_well(self):
        """Started WITHOUT `--xport`, so the launcher has no card at all
        and `cards` is empty. `ui._start_xport` builds one from the bench's
        own menu, and the app is the only thing holding it."""
        from_the_menu = _FakeCard()

        class FakeApp:
            def __init__(self, *a, **kw):
                self._card_net = from_the_menu

            def mainloop(self):
                pass

            def log(self, *a):
                pass
        self._windowed(FakeApp)
        self.assertEqual(from_the_menu.stops, 1)

    def test_a_card_is_not_stopped_twice_for_being_in_both_places(self):
        class FakeApp:
            def __init__(self, *a, **kw):
                self._card_net = None

            def attach_card(self, cfg, net):
                self._card_net = net        # the same object as `cards[0]`

            def mainloop(self):
                pass

            def log(self, *a):
                pass
        self._windowed(FakeApp, "--xport")
        self.assertEqual(_FakeCard.made[0].stops, 1)


class TheCardStopReleasesTheAddress(unittest.TestCase):
    """The other half of R27: that `stop()` is worth calling."""

    def a_card(self):
        from tls350sim import xport, xportnet
        from tls350sim.console import Console
        cfg = xport.XPortConfig(None)
        return xportnet, xportnet.CardNetwork(Console(None), cfg)

    def test_stopping_hands_the_address_back(self):
        xportnet, net = self.a_card()
        net.claimed = ("192.0.2.9", "Ethernet")
        with mock.patch.object(xportnet, "release_ip") as release:
            net.stop()
        release.assert_called_once_with("192.0.2.9", "Ethernet", log=None)
        self.assertIsNone(net.claimed)

    def test_stopping_twice_does_not_claim_or_release_twice(self):
        xportnet, net = self.a_card()
        net.claimed = ("192.0.2.9", "Ethernet")
        with mock.patch.object(xportnet, "release_ip") as release:
            net.stop()
            net.stop()
        self.assertEqual(release.call_count, 1)

    def test_a_stopped_card_will_not_claim_an_address_again(self):
        """The supervisor runs in its own thread and may be partway through
        bringing the card up when `stop()` lands. Claiming after that puts
        back an address nothing will release."""
        xportnet, net = self.a_card()
        net.claim = True
        net._stop = True
        with mock.patch.object(xportnet, "claim_ip") as claim:
            net._claim_now()
        claim.assert_not_called()
        self.assertIsNone(net.claimed)


@unittest.skipUnless(HAVE_TK, "no display")
class TheWindowDoesNotDisarmACaptureItWasHanded(unittest.TestCase):
    """FIDELITY R29, in the window.

    `SimApp.__init__` called `capture.enable(self.policy.capturing)`, which
    turned OFF the capture `run.py` had just armed from `--capture`. Fixing
    the launcher alone would have left this, and the windowed run would
    have looked like the same bug coming back.

    One `SimApp` per test here rather than one for the class, because the
    decision under test is made in the constructor. Three windows, which is
    well inside what Tk will give -- see `test_panel.py` on why thirty is
    not.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cap.jsonl")

    def build(self, capture, level=exposed.BENCH):
        from tls350sim.ui import SimApp
        policy = exposed.Policy(level, capture=capture)
        try:
            app = SimApp(Console(None), 10001, policy=policy, capture=capture)
        except tkinter.TclError as exc:          # pragma: no cover
            # ONLY a display problem skips. The module-level probe has
            # already built and destroyed a `Tk()`, so anything else
            # raising here is a defect in the code under test, and
            # swallowing it as a skip is how coverage goes away without
            # saying so: with `ui.py` reverted, a broad `except Exception`
            # turned eight of these from failures into a green run.
            raise unittest.SkipTest("no usable Tk: %s" % exc)
        self.addCleanup(app.destroy)
        self.addCleanup(app.quit)
        return app

    def test_a_bench_window_leaves_an_armed_capture_armed(self):
        cap = exposed.Capture(self.path, enabled=True)
        app = self.build(cap)
        self.assertTrue(cap.enabled, "the window switched the capture off")
        self.assertTrue(app._switch.visible("Capture"),
                        "the one view that says whether the file is being "
                        "written was hidden")

    def test_a_bench_window_with_no_capture_asked_for_shows_no_view(self):
        """The default has not moved."""
        cap = exposed.Capture(enabled=False)
        app = self.build(cap)
        self.assertFalse(cap.enabled)
        self.assertFalse(app._switch.visible("Capture"))

    def test_the_window_says_where_the_capture_is_going_before_anything_moves(
            self):
        """`_build_exposure` paints inside `_build_network`, which runs
        BEFORE `_build_capture` makes `_cap_where`, so that paint's
        `hasattr` guard skipped the label without a word and it sat empty
        all session unless the exposure dropdown was touched. This is the
        one line that tells an operator which file they are filling."""
        cap = exposed.Capture(self.path, enabled=True)
        app = self.build(cap)
        self.assertIn(self.path, app._cap_where.cget("text"))

    def test_the_rows_reach_the_file_through_the_window(self):
        cap = exposed.Capture(self.path, enabled=True)
        self.build(cap)
        cap.record("in", b"\x01I20100", peer="198.51.100.9")
        cap.close()
        with open(self.path, encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cmd"], "I20100")


@unittest.skipUnless(HAVE_TK, "no display")
class TheCaptureViewSaysWhenItStopsWriting(unittest.TestCase):
    """FIDELITY R34, in the window.

    One Tk interpreter for the whole class. The CAPTURE is reset per test
    rather than the window: it is the same starting point a fresh window
    would have given, and it costs one interpreter instead of six.
    """

    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        cls.dir = tempfile.mkdtemp()
        cls.cap = exposed.Capture(os.path.join(cls.dir, "cap.jsonl"),
                                  enabled=True)
        policy = exposed.Policy(exposed.LAN, capture=cls.cap)
        try:
            cls.app = SimApp(Console(None), 10001,
                             policy=policy, capture=cls.cap)
        except tkinter.TclError as exc:          # pragma: no cover
            # ONLY a display problem skips. The module-level probe has
            # already built and destroyed a `Tk()`, so anything else
            # raising here is a defect in the code under test, and
            # swallowing it as a skip is how coverage goes away without
            # saying so: with `ui.py` reverted, a broad `except Exception`
            # turned eight of these from failures into a green run.
            cls.app = None
            raise unittest.SkipTest("no usable Tk: %s" % exc)

    @classmethod
    def tearDownClass(cls):
        if cls.app is None:                     # pragma: no cover
            return
        try:
            cls.app.quit()
        except Exception:
            pass
        cls.app.destroy()
        cls.app = None

    def setUp(self):
        self.app = type(self).app
        self.cap = type(self).cap
        self.cap._fh = _Brick(ok=99)            # a handle that works
        self.cap.fault = None
        self.cap.unwritten = 0
        self.cap._closed = False
        self.app._cap_fault_said = None
        self.app.logbox.delete("1.0", "end")
        self.poll()

    def poll(self):
        """One turn of the view's own loop, leaving no timer armed."""
        self.app._poll_capture()
        if self.app._cap_after:
            self.app.after_cancel(self.app._cap_after)
            self.app._cap_after = None

    def stats(self):
        return self.app._cap_stats.cget("text")

    def log(self):
        return self.app.logbox.get("1.0", "end")

    def test_a_healthy_capture_shows_a_plain_count(self):
        self.cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.poll()
        self.assertNotIn("NOT WRITING", self.stats())
        self.assertEqual(str(self.app._cap_stats.cget("fg")), bench.MUTED)
        self.assertIn("Appending to", self.app._cap_where.cget("text"))

    def test_a_stopped_capture_is_named_counted_and_red(self):
        self.cap._fh = _Brick(ok=0)
        self.cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.poll()
        text = self.stats()
        self.assertIn("NOT WRITING", text)
        self.assertIn("No space left", text)
        self.assertIn("1 exchange(s) lost", text)
        self.assertEqual(str(self.app._cap_stats.cget("fg")), bench.BAD)

    def test_it_stops_claiming_to_be_appending_to_the_file(self):
        """"Appending to" is a claim about the disk, and it was still being
        made while the disk was refusing every row."""
        self.cap._fh = _Brick(ok=0)
        self.cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.poll()
        where = self.app._cap_where.cget("text")
        self.assertIn("NOT writing to", where)
        self.assertNotIn("Appending to", where)

    def test_the_log_says_it_once_and_not_once_per_poll(self):
        self.cap._fh = _Brick(ok=0)
        for _ in range(5):
            self.cap.record("in", b"\x01I20100", peer="1.1.1.1")
            self.poll()
        self.assertEqual(self.log().count("capture stopped"), 1,
                         "one outage, one line")

    def test_recovery_is_said_and_the_view_goes_quiet_again(self):
        self.cap._fh = _Brick(ok=0)
        self.cap.record("in", b"\x01I20100", peer="1.1.1.1")
        self.poll()
        self.assertIn("NOT WRITING", self.stats())
        self.cap._fh = _Brick(ok=99)
        self.cap.record("in", b"\x01I20200", peer="1.1.1.1")
        self.poll()
        self.assertNotIn("NOT WRITING", self.stats())
        self.assertIn("capture is writing again", self.log())
        self.assertIn("Appending to", self.app._cap_where.cget("text"))
        # The rows the outage ate do not come back, so the count stays --
        # as history, in the warning colour, not as a standing fault.
        self.assertIn("1 exchange(s) never reached the file", self.stats())
        self.assertEqual(str(self.app._cap_stats.cget("fg")), bench.WARN)

    def test_the_rows_that_could_not_be_written_are_still_on_screen(self):
        """The ring is memory and the outage is the disk's. What arrived
        during one is still worth showing."""
        self.cap._fh = _Brick(ok=0)
        self.cap.record("in", b"\x01I20100", peer="198.51.100.4")
        self.poll()
        self.assertIn("198.51.100.4", self.app.capbox.get("1.0", "end"))


class _FakeNet:
    """A card whose binding can be asked about and moved."""

    def __init__(self, host="0.0.0.0"):
        self.host = host
        self.confined = None
        self.calls = []

    def bound_host(self):
        return self.confined or self.host

    def bound_wide(self):
        from tls350sim import xportnet
        return not xportnet.is_loopback(self.bound_host())

    def confine(self, host):
        self.calls.append(host)
        if host == self.confined:
            return False
        self.confined = host
        return True


@unittest.skipUnless(HAVE_TK, "no display")
class LoweringTheExposureMovesTheListenersFirst(unittest.TestCase):
    """FIDELITY R26.

    `_apply_exposure` dropped the limits, unfroze the writes and disarmed
    the capture and did not touch a socket, so a card brought up on
    `0.0.0.0` was still on `0.0.0.0` afterwards with nothing in front of
    it. The dangerous direction is exposed to Bench, and the note this
    project left at that line -- that the card "is still bound where it
    started" -- read it as a limitation of the control rather than as the
    hole it is.
    """

    @classmethod
    def setUpClass(cls):
        from tls350sim.ui import SimApp
        cls.SimApp = SimApp

    def build(self, level=exposed.EXPOSED, host="0.0.0.0", card=None):
        policy = exposed.Policy(level)
        try:
            app = self.SimApp(Console(None), 10001, policy=policy,
                              capture=exposed.Capture(enabled=False),
                              host=host)
        except tkinter.TclError as exc:          # pragma: no cover
            # ONLY a display problem skips. The module-level probe has
            # already built and destroyed a `Tk()`, so anything else
            # raising here is a defect in the code under test, and
            # swallowing it as a skip is how coverage goes away without
            # saying so: with `ui.py` reverted, a broad `except Exception`
            # turned eight of these from failures into a green run.
            raise unittest.SkipTest("no usable Tk: %s" % exc)
        self.addCleanup(exposed.freeze_writes, False)
        self.addCleanup(app.destroy)
        self.addCleanup(app.quit)
        if card is not None:
            app._card_net = card
        return app

    def test_the_card_is_moved_to_loopback_before_the_guards_come_off(self):
        card = _FakeNet("0.0.0.0")
        app = self.build(card=card)
        order = []
        real = exposed.freeze_writes

        def watch(on=True):
            order.append(("freeze", on))
            real(on)
        with mock.patch.object(exposed, "freeze_writes", watch):
            card_confine = card.confine

            def note(host):
                order.append(("confine", host))
                return card_confine(host)
            card.confine = note
            app._apply_exposure(exposed.BENCH)
        self.assertEqual(card.confined, "127.0.0.1",
                         "the card was left where it was")
        self.assertEqual(order[0], ("confine", "127.0.0.1"),
                         "the protections came off before the retreat: %r"
                         % (order,))
        self.assertEqual(app.policy.level, exposed.BENCH)

    def test_a_card_already_on_loopback_is_left_alone(self):
        card = _FakeNet("127.0.0.1")
        app = self.build(card=card)
        app._apply_exposure(exposed.BENCH)
        self.assertEqual(card.calls, [], "it moved a card that was not wide")
        self.assertEqual(app.policy.level, exposed.BENCH)

    def test_going_back_up_releases_the_card_to_its_programming(self):
        card = _FakeNet("0.0.0.0")
        app = self.build(card=card)
        app._apply_exposure(exposed.BENCH)
        self.assertEqual(card.confined, "127.0.0.1")
        app._apply_exposure(exposed.EXPOSED)
        self.assertIsNone(card.confined,
                          "the card stayed on loopback for ever")

    def test_raising_does_not_retreat(self):
        card = _FakeNet("0.0.0.0")
        app = self.build(level=exposed.BENCH, card=card)
        app._apply_exposure(exposed.EXPOSED)
        self.assertNotIn("127.0.0.1", card.calls)
        self.assertEqual(app.policy.level, exposed.EXPOSED)

    def test_with_no_card_and_a_wide_tunnel_the_change_is_refused(self):
        """The bare tunnel is a thread `run.py` started round a socket this
        window was never given. Refusing beats lowering the guard and
        hoping."""
        app = self.build(host="0.0.0.0")
        with mock.patch("tkinter.messagebox.showwarning"):
            app._apply_exposure(exposed.BENCH)
        self.assertEqual(app.policy.level, exposed.EXPOSED,
                         "the exposure was lowered with the tunnel still wide")
        self.assertIn("REFUSED", app.logbox.get("1.0", "end"))

    def test_with_no_card_and_a_loopback_tunnel_it_is_allowed(self):
        app = self.build(host="127.0.0.1")
        app._apply_exposure(exposed.BENCH)
        self.assertEqual(app.policy.level, exposed.BENCH)

    def test_the_freeze_is_still_lifted_when_the_change_goes_through(self):
        card = _FakeNet("127.0.0.1")
        app = self.build(card=card)
        exposed.freeze_writes(True)
        app._apply_exposure(exposed.BENCH)
        self.assertFalse(exposed.writes_frozen())

    def test_a_refused_change_leaves_the_freeze_on(self):
        """The whole point: nothing is given up when the answer is no."""
        app = self.build(host="0.0.0.0")
        exposed.freeze_writes(True)
        with mock.patch("tkinter.messagebox.showwarning"):
            app._apply_exposure(exposed.BENCH)
        self.assertTrue(exposed.writes_frozen(),
                        "the writes were unfrozen by a refused change")


class WhatCountsAsLoopback(unittest.TestCase):
    """`0.0.0.0` is emphatically not one, and it is the one that matters."""

    def test_the_addresses_nothing_off_this_machine_can_reach(self):
        from tls350sim import xportnet
        for host in ("127.0.0.1", "127.0.0.53", "localhost"):
            self.assertTrue(xportnet.is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.10", "10.0.0.1", "", None):
            self.assertFalse(xportnet.is_loopback(host), repr(host))


if __name__ == "__main__":
    unittest.main()
