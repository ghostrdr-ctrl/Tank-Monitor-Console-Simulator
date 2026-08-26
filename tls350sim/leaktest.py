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
"""Leak tests that actually run.

A console does not decide a tank is leaking; it measures. So does this: a test
records the volume it started with, waits out its programmed duration on the
console's own clock, and divides the volume it lost by the hours it took. If
the site is losing product, which the bench decides, in gallons per hour,
the test finds it, and finds it at the rate the test is looking for. A 0.2 gph
test passes a 0.05 gph leak and fails a 0.4 gph one, which is the whole point
of there being three rates.

The result records match the manual's own: a rate, a duration, the volume the
tank held, and PASSED / FAILED / INVALID, 01, 02 and 00 as function 208
reports them.

Failing does more than print. The manual gives every test its own alarm
(Tank 02/13, 02/14, 02/15; PLLD 21/02, 21/11, 21/03; WPLLD 26/02, 26/03,
26/10; VLLD 06/07, 06/15, 06/19), those alarms are results rather than
conditions so they latch, and a line that fails at or beyond its programmed
shutdown rate is shut down, and stays down until whatever the Line Re-Enable
Method says brings it back.
"""
import time

from .clock import clock_words

# The three rates a TLS-350 tests at, in gallons per hour.
RATES = {"gross": 3.0, "periodic": 0.2, "annual": 0.1}

# How long each one takes. The tank tests take what they are programmed to
# take; these are the line tests, which the console times itself.
LINE_HOURS = {"gross": 0.05, "periodic": 0.75, "annual": 8.0}

PASSED, FAILED, INVALID = "PASSED", "FAILED", "INVALID"
RESULT_CODE = {INVALID: "00", PASSED: "01", FAILED: "02"}
TYPE_CODE = {"periodic": "00", "annual": "01", "gross": "02"}

# category and type for "this test failed", from the i101 tables
FAIL_ALARM = {
    "tank":  {"gross": ("02", "13"), "periodic": ("02", "14"),
              "annual": ("02", "15")},
    "plld":  {"gross": ("21", "02"), "periodic": ("21", "11"),
              "annual": ("21", "03")},
    "wplld": {"gross": ("26", "02"), "periodic": ("26", "03"),
              "annual": ("26", "10")},
    "vlld":  {"gross": ("06", "07"), "periodic": ("06", "15"),
              "annual": ("06", "19")},
}
SHUTDOWN_ALARM = {"plld": ("21", "08"), "wplld": ("26", "08"),
                  "vlld": ("06", "03")}
ACTIVE_ALARM = {"tank": ("02", "20")}

# The two limits a tank carries for what it loses WHILE a test is running.
# "During a leak test, Leak Alarm Limit warns when the cumulative temperature
# compensated product loss from a tank reaches the limit value", S626; and
# "Sudden Loss Limit immediately warns of a sudden loss of fuel during a leak
# test. It is not based on temperature-compensated volume and is intended to
# identify losses larger than the Leak Alarm Limit", S625.
LEAK_ALARM = ("02", "02")
SUDDEN_LOSS = ("02", "06")
LEAK_LIMIT_CODE = "626"
SUDDEN_LIMIT_CODE = "625"

# the shutdown rate each line type is programmed with, and what its
# enumeration means in gallons per hour
SHUTDOWN_CODE = {"plld": "784", "wplld": "7A4", "vlld": "757"}

# A pumpside test is a test of its own and fails on its own alarms. The
# console has carried these three since the status tables were written and
# nothing ever raised them, because nothing ran the test.
PUMP_FAIL_ALARM = {"gross": ("06", "09"),      # Gross Pump Test Fail
                   "periodic": ("06", "17"),   # Periodic Pump Test Fail
                   "annual": ("06", "21")}     # Annual Pump Test Fail

# The results live under a kind of their own so that a pumpside pass is never
# counted as a line pass. 351 prints them in separate columns and they are
# separate measurements.
PUMP_KIND = "vlldpump"
SHUTDOWN_RATE = {"01": 3.0, "02": 0.2, "03": 0.1, "04": None}

# which tank minimum volume a test has to clear to be valid
MINIMUM_CODE = {"periodic": "636", "annual": "62A"}


class Result:
    """One finished test, as function 208 reports it."""

    def __init__(self, kind, device, rate_key, result, rate, hours, volume,
                 started):
        self.kind = kind
        self.device = device
        self.rate_key = rate_key
        self.result = result
        self.rate = rate
        self.hours = hours
        self.volume = volume
        self.started = started

    def line(self):
        return (f"{self.rate_key.upper():9s}"
                f"{clock_words(self.started):24s}"
                f"{self.result:9s}{self.rate:6.2f}{self.hours:7.1f}"
                f"{self.volume:9.0f}")


class Running:
    """A test in progress."""

    def __init__(self, kind, device, rate_key, hours, volume, started,
                 manual_stop=False):
        self.kind = kind
        self.device = device
        self.rate_key = rate_key
        self.hours = hours
        self.volume = volume
        self.started = started
        self.manual_stop = manual_stop

    def elapsed(self, now):
        return max(0.0, (now - self.started) / 3600.0)

    def remaining(self, now):
        return max(0.0, self.hours - self.elapsed(now))


class Engine:
    """Every test this console is running, and every result it remembers."""

    def __init__(self, console):
        self.c = console
        self.running = {}     # (kind, device) -> Running
        self.results = {}     # (kind, device) -> {rate_key: Result}
        self.history = {}     # (kind, device) -> [Result], oldest first
        self.disabled = set()  # (kind, device) lines the console has shut down
        self._checked = None   # console time as of the last look, for schedules

    # ---- starting and stopping ---------------------------------------------
    LINES = ("plld", "wplld")

    def start(self, kind, device, rate_key, hours=None, manual_stop=False):
        """Begin a test. Returns a message for the display."""
        if kind in self.LINES:
            # A line test is not a stopwatch over a volume, it is a sequence of
            # pressure measurements, so `pressure.Lines` runs it and hands the
            # result back through `record_line`.
            return self.c.lines.start(kind, device, rate_key)
        if kind == "tank" and device not in self.c.tank_level:
            return "NO TANK"
        if (kind, device) in self.running:
            return "TEST ALREADY RUNNING"
        if hours is None:
            hours = LINE_HOURS[rate_key] if kind != "tank" else 2.0
        volume = self.c.tank_level.get(device, {}).get("volume", 0.0) \
            if kind == "tank" else self._line_volume(device)
        self.running[(kind, device)] = Running(
            kind, device, rate_key, float(hours), volume,
            time.mktime(self.c.now()), manual_stop)
        return "TEST STARTED"

    def stop(self, kind, device):
        """STOP LEAK TEST. A test stopped early is invalid, not passed."""
        if kind in self.LINES:
            return self.c.lines.stop(kind, device)
        run = self.running.pop((kind, device), None)
        if run is None:
            return "NO TEST RUNNING"
        now = time.mktime(self.c.now())
        if run.manual_stop and run.elapsed(now) > 0:
            self._finish(run, now)
            return "TEST COMPLETE"
        self._record(Result(run.kind, run.device, run.rate_key, INVALID, 0.0,
                            run.elapsed(now), run.volume, run.started))
        return "TEST STOPPED"

    def stop_all(self, kind):
        if kind in self.LINES:
            self.c.lines.stop_all(kind)
            return
        for k, dev in list(self.running):
            if k == kind:
                self.stop(k, dev)

    # ---- time --------------------------------------------------------------
    def tick(self):
        """Finish anything whose duration has run out, and start what is due."""
        stamp = self.c.now()
        now = time.mktime(stamp)
        for key, run in list(self.running.items()):
            if run.manual_stop:
                continue
            if run.elapsed(now) >= run.hours:
                self.running.pop(key, None)
                self._finish(run, now)
        # the lines move on pressure rather than on a stopwatch, but they move
        # on the same console time as everything else
        self.c.lines.tick()
        self._scheduled(stamp, now)

    def record_line(self, kind, device, rate_key, passed, rate, started):
        """A finished line test, handed back by the pressure engine.

        The pressure model decides pass or fail; everything a result MEANS
        afterwards, the report line, the alarm, the shutdown, is the same
        machinery a tank test goes through, so it happens here.
        """
        now = time.mktime(self.c.now())
        result = Result(kind, device, rate_key, PASSED if passed else FAILED,
                        rate, max(0.0, (now - started) / 3600.0), 0.0, started)
        self._record(result)
        if kind == "vlld":
            # "the line leak detector ALSO runs a pump side test" -- after
            # every line test, pass or fail, and only where S758 enabled it
            self.pumpside_test(device, rate_key, started)
        if passed:
            self.c.clear_posted(*FAIL_ALARM[kind][rate_key], device)
            self.disabled.discard((kind, device))
            return
        self.c.post(*FAIL_ALARM[kind][rate_key], device)
        shutdown = self._shutdown_rate(kind, device)
        if shutdown is not None and RATES[rate_key] >= shutdown:
            # a line is shut down by the RATE THE TEST WAS LOOKING FOR, not by
            # the rate it measured: "If you select a shutdown rate of 3.0 gph,
            # then only a failed 3.0 gph leak test will disable dispensing,
            # while a failed 0.2 gph or 0.1 gph leak test will just trigger an
            # alarm."
            self.disabled.add((kind, device))

    # ---- tests the console starts by itself ---------------------------------
    def _scheduled(self, stamp, now):
        """Nobody presses START for a scheduled test; the console does.

        S611 holds a tank's test as duration, rate, method and start time. A
        DAILY test is due once a day at that time, so it fires when the clock
        crosses it, which the bench's fast clock reaches in seconds.

        A line's schedule is an enumeration rather than a time: REPETITIVE
        means the console tests the line again as soon as it is free.
        """
        last, self._checked = self._checked, now
        if last is None:
            return
        for tank in sorted(self.c.tank_level):
            plan = self._tank_schedule(tank)
            if plan is None:
                continue
            rate_key, hours, hhmm = plan
            for day in (-1, 0):
                due = self._at(stamp, hhmm, day)
                if last < due <= now and ("tank", tank) not in self.running:
                    self.start("tank", tank, rate_key, hours=hours)
        for kind, code in (("plld", "78C"), ("wplld", "7A3")):
            if not self.c.has(kind):
                continue
            for line in range(1, 5):
                raw = self.c.values.get(f"S{code}{line:02d}")
                if not raw or not raw.strip().endswith("1"):
                    continue          # 1 = REPETITIVE, and only repetitive
                if (kind, line) not in self.running:
                    self.start(kind, line, "periodic")

    def _tank_schedule(self, tank):
        """(rate, hours, HHmm) from S611, or None if it is not on a timer."""
        raw = self.c.values.get(f"S611{tank:02d}")
        if not raw:
            return None
        body = raw[2:] if len(raw) > 8 else raw
        if len(body) < 8 or not body[:2].isdigit():
            return None
        hours = int(body[:2]) or 2
        rate_key = "annual" if body[2:3] == "1" else "periodic"
        if body[3:4] != "5":              # 5 = DAILY, the one with a time
            return None
        hhmm = body[4:8]
        return (rate_key, hours, hhmm) if hhmm.isdigit() else None

    @staticmethod
    def _at(stamp, hhmm, offset_days=0):
        """Console epoch for HHmm on the day `offset_days` from this one."""
        return time.mktime((stamp.tm_year, stamp.tm_mon,
                            stamp.tm_mday + offset_days, int(hhmm[:2]),
                            int(hhmm[2:]), 0, 0, 1, -1))

    def _finish(self, run, now):
        hours = min(run.elapsed(now), run.hours) or run.hours
        rate = self.measured_rate(run.kind, run.device)
        result = self._judge(run, rate, hours)
        self._record(Result(run.kind, run.device, run.rate_key, result, rate,
                            hours, run.volume, run.started))
        if result == FAILED:
            self._fail(run, rate)
        elif result == PASSED:
            # a passing test is the other way a fail alarm goes away, and the
            # way a shut-down line comes back when the method is Pass Line Test
            self.c.clear_posted(*FAIL_ALARM[run.kind][run.rate_key],
                                run.device)
            self.disabled.discard((run.kind, run.device))

    def _judge(self, run, rate, hours):
        threshold = RATES[run.rate_key]
        if run.kind == "tank":
            # "A delivery occurred during the leak detect test" is one of the
            # reasons a result comes back invalid, and so is starting one
            # within eight hours of a drop.
            now = time.mktime(self.c.now())
            if self.c.deliveries.during(run.device, run.started, now):
                return INVALID
            code = MINIMUM_CODE.get(run.rate_key)
            minimum = self.c.limit(code, run.device) if code else None
            if minimum and run.volume < minimum:
                # "Set Tank Periodic/Annual Leak Test Minimum Volume"
                return INVALID
        if hours < 0.01:
            return INVALID
        return FAILED if rate >= threshold else PASSED

    def _record(self, result):
        self.results.setdefault((result.kind, result.device), {})[
            result.rate_key] = result
        # a history report wants every test, not the latest one: "the last
        # 3.0 gph, the first 0.2 gph, and the first 0.1 gph test results for
        # each month"
        log = self.history.setdefault((result.kind, result.device), [])
        log.append(result)
        del log[:-200]

    def last_pass(self, kind, device, rate_key):
        """When that rate last passed on that device, or None."""
        for result in reversed(self.history.get((kind, device)) or []):
            if result.rate_key == rate_key and result.result == PASSED:
                return result.started
        return None

    def first_pass_each_month(self, kind, device, rate_key):
        """The first pass in each month it passed in, oldest first."""
        seen, out = set(), []
        for result in self.history.get((kind, device)) or []:
            if result.rate_key != rate_key or result.result != PASSED:
                continue
            month = time.strftime("%Y%m", time.localtime(result.started))
            if month in seen:
                continue
            seen.add(month)
            out.append(result.started)
        return out[-12:]

    def air_purge(self, device):
        """"Air Purge purges air from the VLLD Controller by performing six
        consecutive VLLD Controller 3.0 gph selftests."

        A service routine rather than a test: it runs, it records the six
        selftests, and it leaves the line as it found it.
        """
        now = time.mktime(self.c.now())
        for _ in range(6):
            rate = self.measured_rate("vlld", device)
            self._record(Result("vlld", device, "gross",
                                PASSED if rate < 3.0 else FAILED, rate,
                                0.1, self.c.tank_level.get(device, {}).get(
                                    "volume", 0.0), now))
        return "AIR PURGE DONE"

    def _fail(self, run, rate):
        if run.kind == "tank" and not self._fail_alarm_enabled(run):
            return
        self.c.post(*FAIL_ALARM[run.kind][run.rate_key], run.device)
        shutdown = self._shutdown_rate(run.kind, run.device)
        if shutdown is not None and rate >= shutdown:
            # the shutdown alarm stands as long as the line is down, so the
            # disabled set is the condition; nothing to post separately
            self.disabled.add((run.kind, run.device))

    def _fail_alarm_enabled(self, run):
        """S62D: gross, periodic and annual fail alarms, one character each."""
        raw = self.c.values.get(f"S62D{run.device:02d}")
        if not raw:
            return True                     # not programmed, so not disabled
        body = raw[2:] if len(raw) > 3 else raw
        index = {"gross": 0, "periodic": 1, "annual": 2}[run.rate_key]
        return body[index:index + 1] != "0"

    def _shutdown_rate(self, kind, device):
        code = SHUTDOWN_CODE.get(kind)
        if not code:
            return None
        raw = self.c.values.get(f"S{code}{device:02d}")
        if not raw:
            return None
        body = (raw[2:] if len(raw) > 2 else raw).strip()
        return SHUTDOWN_RATE.get(body[-2:])

    # ---- what the site is actually doing ------------------------------------
    def measured_rate(self, kind, device):
        """Gallons per hour going missing, which is what a test measures."""
        if kind == "tank":
            return max(0.0, self.c.tank_leak.get(device, 0.0))
        return max(0.0, self.c.line_leak.get((kind, device), 0.0))

    def _line_volume(self, device):
        return 0.0

    # ---- the pipe between the check valve and the pump ----------------------
    def pumpside_enabled(self, device):
        """S758: "ss - Line Leak Pump Side Test, 00=Disable, 01=Enable"."""
        raw = (self.c.values.get(f"S758{device:02d}") or "").strip()
        return raw.endswith("1")

    def pumpside_test(self, device, rate_key, started=None):
        """The test the console runs after a VLLD line test.

        "After the system conducts a line leak test, the line leak detector
        also runs a pump side test for a pressure loss in the piping and
        connections BETWEEN THE IN-LINE CHECK VALVE AND THE SUBMERSIBLE PUMP."

        That is a different piece of pipe from the one the line test just
        measured, which is the whole point of it: a leak on the pump side of
        the check valve does not show up in a line test at all. This used to
        be derived from the line result, and deriving it made the console
        incapable of reporting the one failure the test exists to catch.

        Returns the Result, or None if the site never enabled the test.
        """
        if not self.pumpside_enabled(device):
            return None
        now = time.mktime(self.c.now())
        rate = max(0.0, self.c.pump_leak.get(("vlld", device), 0.0))
        threshold = RATES[rate_key]
        passed = rate < threshold
        result = Result(PUMP_KIND, device, rate_key,
                        PASSED if passed else FAILED, rate,
                        max(0.0, (now - (started or now)) / 3600.0), 0.0,
                        started or now)
        self._record(result)
        if passed:
            self.c.clear_posted(*PUMP_FAIL_ALARM[rate_key], device)
        else:
            self.c.post(*PUMP_FAIL_ALARM[rate_key], device)
        return result

    def pumpside_passes(self, device, rate_key, since):
        """How many pumpside tests passed at that rate since `since`."""
        log = self.history.get((PUMP_KIND, device)) or []
        return sum(1 for r in log
                   if r.rate_key == rate_key and r.result == PASSED
                   and r.started >= since)

    # ---- what the console shows ---------------------------------------------
    def active(self, kind, device):
        if kind in self.LINES:
            ln = self.c.lines.lines.get((kind, device))
            return ln if ln is not None and ln.running() else None
        return self.running.get((kind, device))

    def result(self, kind, device, rate_key):
        return self.results.get((kind, device), {}).get(rate_key)

    def status_line(self, kind, device, rate_key):
        """One line for a results screen, in the console's own words."""
        if kind in self.LINES:
            ln = self.c.lines.lines.get((kind, device))
            if ln is not None and ln.running() and ln.leg == rate_key:
                # a line under test says what it is doing, not how long is
                # left: there is no "left" until the line is thermally stable
                return ln.state
        run = self.running.get((kind, device))
        if run and run.rate_key == rate_key:
            now = time.mktime(self.c.now())
            return f"TEST ACTIVE {run.remaining(now):4.1f} HRS"
        res = self.result(kind, device, rate_key)
        if res is None:
            return "NO TEST DATA AVAILABLE"
        if res.result == INVALID:
            return "INVALID"
        if kind in self.LINES or kind == "vlld":
            # 576013-610 Rev AC p.51 and p.61: a LINE result screen reads
            # "DATE       3.0 PASSED" -- the date the test ran, then the rate
            # and the verdict. The rate-by-rate block with LAST TEST and the
            # counts is the PRINTOUT, not the screen.
            rate = {"gross": "3.0", "periodic": "0.20",
                    "annual": "0.10"}.get(rate_key, f"{res.rate:.2f}")
            when = time.strftime("%b %d",
                                 time.localtime(res.started)).upper()
            said = f"{rate} {res.result}"
            return f"{when}{said.rjust(24 - len(when))}"
        return f"{res.result} {res.rate:5.2f} GAL/HR"

    def loss_conditions(self):
        """What the tank has lost since the test started, against its limits.

        These are the two alarms that do not wait for the test to finish: a
        console watching a tank go down 25 gallons in the middle of a leak
        test says so at once rather than eight hours later.
        """
        out = []
        for (kind, device), run in sorted(self.running.items()):
            if kind != "tank":
                continue
            now = self.c.tank_level.get(device, {}).get("volume", 0.0)
            lost = run.volume - now
            if lost <= 0:
                continue
            sudden = self.c.limit(SUDDEN_LIMIT_CODE, device)
            if sudden and lost >= sudden:
                out.append(SUDDEN_LOSS[0] + SUDDEN_LOSS[1] + f"{device:02d}")
            leak = self.c.limit(LEAK_LIMIT_CODE, device)
            # the leak alarm limit is read on temperature compensated volume,
            # which is the same 0.998 the inventory screens use
            if leak and lost * 0.998 >= leak:
                out.append(LEAK_ALARM[0] + LEAK_ALARM[1] + f"{device:02d}")
        return out

    def conditions(self):
        """The alarms a test in progress puts up by itself."""
        out = []
        for (kind, device), run in sorted(self.running.items()):
            aa_nn = ACTIVE_ALARM.get(kind)
            if aa_nn:
                out.append(aa_nn[0] + aa_nn[1] + f"{device:02d}")
        out += self.loss_conditions()
        for kind, device in sorted(self.disabled):
            aa, nn = SHUTDOWN_ALARM[kind]
            out.append(aa + nn + f"{device:02d}")
        return out

    def re_enable(self):
        """Acknowledging clears a shutdown only if that is how it was set up.

        "LINE RE-ENABLE METHOD: PASS LINE TEST / ACKNOWLEDGE ALARM."
        """
        method = (self.c.values.get("S55300") or "").strip()
        if method.endswith("1"):
            self.disabled.clear()
            return True
        return False
