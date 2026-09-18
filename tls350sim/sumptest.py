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
"""The Mag Sump Leak Test, and the sump it is run on.

576013-610 Rev AC chapters 23 and 24 specify the test completely, and this
console used to have none of it: 099, 09A and 09B put a sensor into a phase
and nothing ever took it out again, the screens drew fixed numbers, and the
history printed its heading over `NO TEST PASSED` for ever. FIDELITY U1b.

What the pages give, and this does:

* Two phases. "Test Phase ... During the Test Phase water alarms/warnings
  are suppressed so that the user can fill the sump with water ... If after
  2 hours the user has not started the Measuring Height Phase then the test
  will be aborted." Then "Measuring Height Phase ... the TLS will wait for
  the water temperature to stabilize. Once the temperature is stable a leak
  rate will be calculated."
* Temperature stability. "the average water temperature of two 5 minute
  periods are compared ... If the difference between the two periods is
  less than 5 degrees F/hour, then the temperature is considered stable ...
  every 5 minutes ... The test will be aborted if the temperature has not
  stabilized in 4 hours."
* The pass. "After 2 hours the leak rate is calculated from the last 2
  hours of data and compared to the leak rate threshold of 0.0104 inches
  (0.264 mm) per hour ... At every half hour until the test passes, or until
  24 hours, it recalculates ... If after 24 hours the water height has not
  dropped 0.25 inches or more, then the test will pass". "A leak test will
  either pass or be aborted, it will not fail."
* Eleven aborts, with the serial manual's numbers (sumpreports.py).
* Water alarms held off during the test, unless the water stands above
  the sensor's length less two inches, and for 24 hours after it "to allow
  time to empty water out of the sump" -- or until "no water detected for 5
  consecutive minutes".
* Three automatic printouts: "The TLS will automatically print that the
  Test Phase was started", the same for the Measuring Height Phase, and "the
  test result when the test has been completed".

What the pages do not give is the sump. A leak test measures water leaving
a sump, and this console has no water in any sump to measure, so the bench
sets it: how deep, how fast it leaves, how warm, and how fast that is
changing. Each is a straight line from the moment it was last set, so a
reading at any past moment is exact however far one tick jumps the clock --
the same property `readings.wander` gives CSLD's probe table. The choices
the pages leave open are UNKNOWNS A53.
"""
import time

from . import readings

NO_DATA, ABORTED, FILL_SUMP, MEASURING, PASSED = "00", "01", "02", "03", "04"

# 576013-610 Rev AC p.24-1 and p.24-2, and every one of them a number the
# page prints.
MIN_WATER = 6.0                 # "at least 6 inches of water in sump"
HIGH_MARGIN = 2.0               # "the sensor's length, minus 2 inches"
TEMP_LOW, TEMP_HIGH = 36.0, 115.0
DROP = 0.25                     # "0.25 inches less than the water height"
RISE = 0.10                     # "0.10 inches (2.54 mm) more"
THRESHOLD = 0.0104              # inches an hour
TEST_PHASE_LIMIT = 2 * 3600.0   # "If after 2 hours the user has not started"
STABLE_LIMIT = 4 * 3600.0       # "not stabilized in 4 hours"
FIRST_LOOK = 2 * 3600.0         # "After 2 hours ... the last 2 hours of data"
RELOOK = 1800.0                 # "At every half hour"
LONGEST = 24 * 3600.0           # "or until 24 hours"
PERIOD = 300.0                  # "two 5 minute periods"
STABLE_RATE = 5.0               # "less than 5 degrees F/hour"
COMPUTING = 600.0               # "the first 10 minutes of measuring height"
SUPPRESS_AFTER = 24 * 3600.0    # "still be suppressed for 24 hours"
EMPTY_FOR = 300.0               # "no water detected for 5 consecutive minutes"

# How often the console takes a reading. No page says; a minute is fine
# enough that every five-minute average holds five of them and the half-hour
# looks land on a sample.
SAMPLE = 60.0

# 576013-610 Rev AC p.24-2's list, which is the panel's and the paper's words.
# The serial manual's own table spells the last one TEMP STABLE TIMEOUT, and
# that one stays on the wire (sumpreports.ABORT_REASON).
ABORT_WORDS = {
    "01": "MAG SENS ALM/WARN", "02": "WATER TOO LOW", "03": "WATER TOO HIGH",
    "04": "TEMP TOO LOW", "05": "TEMP TOO HIGH", "06": "WATER INCREASED",
    "07": "WATER DECREASED", "08": "INSUFFICIENT DATA",
    "09": "LEAK RATE TOO HIGH", "10": "TEST PHASE TIMEOUT",
    "11": "TMP STABLE TIMEOUT",
}

# A Mag sensor state that aborts a test: "There must be no active Mag Sump
# alarms/warnings except for water alarms/warnings". RELAY ACTIVE is not an
# alarm or a warning.
NOT_AN_ABORT = ("normal", "water", "waterwarn", "relay")

# Enough passes to fill ten years of "LAST PASSED EACH YEAR" at a test a
# week, which is far more than anybody runs.
KEEP_PASSED = 520


def stamp(when):
    """"2-19-05      9:43AM": the date and time a sump screen carries.

    Right against the twenty-fourth column after `s 1: `, the way p.23-1
    draws it.
    """
    t = time.localtime(when)
    date = time.strftime("%m-%d-%y", t).lstrip("0")
    clock = time.strftime("%I:%M%p", t).lstrip("0")
    return f"{date}{clock.rjust(19 - len(date))}"


def _mean(values):
    return sum(values) / len(values)


class Test:
    """One test on one sensor, from START to its result."""

    def __init__(self, sensor, at):
        self.sensor = sensor
        self.status = FILL_SUMP
        self.reason = "00"
        self.test_at = at            # the Test Phase started
        self.start_at = None         # the Measuring Height Phase started
        self.start_ht = self.start_temp = None
        self.end_at = self.end_ht = self.end_temp = None
        self.samples = []            # (at, height, temp), the last 2h10m
        self.last = at               # how far the samples have got
        self.stable_at = None
        self.rate = None             # the leak rate the last look computed
        self.suppress_until = None
        self.dry_since = None

    @property
    def running(self):
        return self.status in (FILL_SUMP, MEASURING)

    def snapshot(self):
        """A copy that later readings will not move, for the printer."""
        copy = Test(self.sensor, self.test_at)
        copy.__dict__.update(self.__dict__)
        copy.samples = list(self.samples)
        return copy


class Sumps:
    """The water in every Mag sump, and the tests run on it."""

    def __init__(self, console):
        self.c = console
        self.water = {}       # sensor -> (height, when it was that)
        self.leak = {}        # sensor -> inches an hour leaving
        self.temp = {}        # sensor -> (degrees F, when it was that)
        self.drift = {}       # sensor -> degrees F an hour
        self.span = {}        # sensor -> 12 or 24, the measurement range
        self.tests = {}       # sensor -> the running test, or the last one
        self.passed = {}      # sensor -> [Test], newest first
        self.printed = []     # (what, sensor, Test snapshot, when)

    def now(self):
        return time.mktime(self.c.now())

    # ---- the sump ----------------------------------------------------------
    def range_of(self, n):
        """"1-foot measurement range ... 2-foot measurement range".

        577013-812 Rev G's two sensors, and p.24-2 calls them the 12-inch and
        the 24-inch. Nothing on the console programs which one is fitted --
        the sensor reports it -- so the bench says.
        """
        return 12 if self.span.get(int(n)) == 12 else 24

    def height_at(self, n, at):
        n = int(n)
        height, since = self.water.get(n, (0.0, at))
        height -= self.leak.get(n, 0.0) * (at - since) / 3600.0
        return max(0.0, min(height, float(self.range_of(n))))

    def temp_at(self, n, at):
        n = int(n)
        if n in self.temp:
            degrees, since = self.temp[n]
        else:
            # the figure a sump screen drew before there was a sump
            degrees, since = readings.fixed(60.0, 80.0, "sumptemp", n), at
        return degrees + self.drift.get(n, 0.0) * (at - since) / 3600.0

    def height(self, n):
        return self.height_at(n, self.now())

    def temperature(self, n):
        return self.temp_at(n, self.now())

    def _rebase(self, n):
        """Take every reading up to now on the old line before drawing a new
        one, so a change on the bench is a change from this moment on."""
        now = self.now()
        self.catch_up(now)
        self.water[n] = (self.height_at(n, now), now)
        self.temp[n] = (self.temp_at(n, now), now)
        return now

    def pour(self, n, height):
        n = int(n)
        now = self._rebase(n)
        self.water[n] = (max(0.0, min(float(height), float(self.range_of(n)))),
                         now)

    def set_leak(self, n, rate):
        n = int(n)
        self._rebase(n)
        self.leak[n] = float(rate)

    def set_temperature(self, n, degrees):
        n = int(n)
        now = self._rebase(n)
        self.temp[n] = (float(degrees), now)

    def set_drift(self, n, rate):
        n = int(n)
        self._rebase(n)
        self.drift[n] = float(rate)

    def set_range(self, n, inches):
        n = int(n)
        self._rebase(n)
        self.span[n] = 12 if int(inches) == 12 else 24

    # ---- what the console holds against it ---------------------------------
    def warning_height(self, n):
        return self._threshold(n, "warning", 2.0)

    def alarm_height(self, n):
        return self._threshold(n, "alarm", 5.0)

    def _threshold(self, n, which, default):
        """S728's WATER WARNING and WATER ALARM heights, 576013-623 p.26-3."""
        from . import fieldio
        from .console import FIELDS
        code = f"S728{int(n):02d}"
        raw = self.c.values.get(code)
        if raw:
            try:
                return float(fieldio.decode(FIELDS[f"S72801.{which}"], code,
                                            raw, self.c))
            except (KeyError, TypeError, ValueError):
                pass
        return default

    def _other_alarm(self, n):
        state = self.c.sensor_state.get(("smart", str(int(n))), "normal")
        if state in NOT_AN_ABORT:
            return False
        return self.c.sensor_alarm_allowed("smart", n, state)

    # ---- the three commands ------------------------------------------------
    def status(self, n):
        test = self.tests.get(int(n))
        return test.status if test else NO_DATA

    def start(self, n):
        """START MAG SUMP LEAK TEST, 099. A test already running is left
        running, and says what it is doing (UNKNOWNS A53)."""
        n = int(n)
        now = self.now()
        self.catch_up(now)
        test = self.tests.get(n)
        if test and test.running:
            return test.status
        test = Test(n, now)
        self.tests[n] = test
        # "Any water alarms/warnings that are active when the Test Phase is
        # started will be cleared" -- cleared, not merely held off, so one
        # the display was still holding goes as well.
        for record in (f"2806{n:02d}", f"2807{n:02d}"):
            self.c.latched.discard(record)
            self.c.acked.discard(record)
        self._print("started", test, now)
        if self._other_alarm(n):
            self._finish(test, now, ABORTED, "01")
        return test.status

    def measure(self, n):
        """START MEASURING HEIGHT, 09A. Only a test in its Test Phase has a
        Measuring Height Phase to start."""
        n = int(n)
        now = self.now()
        self.catch_up(now)
        test = self.tests.get(n)
        if not test or test.status != FILL_SUMP:
            return self.status(n)
        test.status, test.start_at, test.last = MEASURING, now, now
        test.start_ht = self.height_at(n, now)
        test.start_temp = self.temp_at(n, now)
        test.samples = [(now, test.start_ht, test.start_temp)]
        self._print("measuring", test, now)
        # "The following conditions must exist when the Mag Sump Leak Test
        # Measuring Height Phase is started": Figure 24-2 is exactly this,
        # WATER TOO LOW over a DURATION of 0 MINS.
        reason = ("01" if self._other_alarm(n)
                  else self._out_of_bounds(test, test.start_ht,
                                           test.start_temp))
        if reason:
            self._finish(test, now, ABORTED, reason)
        return test.status

    def stop(self, n):
        """STOP MAG SUMP LEAK TEST, 09B: "The user manually aborts the test".

        INSUFFICIENT DATA is "test manually aborted before 2 hrs" and LEAK
        RATE TOO HIGH is "leak rate was greater than or equal to ... and test
        was manually aborted after 2 hrs". A stop after two hours with no
        leak rate yet -- the temperature never settled -- has had no data to
        judge, so it is the first.
        """
        n = int(n)
        now = self.now()
        self.catch_up(now)
        test = self.tests.get(n)
        if not test or not test.running:
            return self.status(n)
        reason = "09" if test.rate is not None else "08"
        self._finish(test, now, ABORTED, reason)
        return test.status

    # ---- the clock ---------------------------------------------------------
    def tick(self):
        self.catch_up(self.now())

    def catch_up(self, now):
        for n, test in sorted(self.tests.items()):
            if test.status == FILL_SUMP:
                if now - test.test_at >= TEST_PHASE_LIMIT:
                    self._finish(test, test.test_at + TEST_PHASE_LIMIT,
                                 ABORTED, "10")
                elif self._other_alarm(n):
                    self._finish(test, now, ABORTED, "01")
            elif test.status == MEASURING:
                self._measure_until(n, test, now)
            if not test.running:
                self._watch_empty(n, test, now)

    def _measure_until(self, n, test, now):
        at = test.last + SAMPLE
        while at <= now:
            height, degrees = self.height_at(n, at), self.temp_at(n, at)
            test.samples.append((at, height, degrees))
            test.last = at
            reason = self._out_of_bounds(test, height, degrees)
            if reason:
                self._finish(test, at, ABORTED, reason)
                return
            since = at - test.start_at
            if (test.stable_at is None and since >= 2 * PERIOD
                    and since % PERIOD == 0):
                rate = self.temp_rate(test, at)
                if rate is not None and abs(rate) < STABLE_RATE:
                    test.stable_at = at
            if test.stable_at is None and since >= STABLE_LIMIT:
                self._finish(test, at, ABORTED, "11")
                return
            if since >= FIRST_LOOK and (since - FIRST_LOOK) % RELOOK == 0:
                if since >= LONGEST:
                    # the drop would have aborted it already
                    self._finish(test, at, PASSED)
                    return
                if test.stable_at is not None:
                    test.rate = self.leak_rate(test, at)
                    if test.rate < THRESHOLD:
                        self._finish(test, at, PASSED)
                        return
            keep = at - FIRST_LOOK - 2 * PERIOD
            while test.samples and test.samples[0][0] < keep:
                test.samples.pop(0)
            at += SAMPLE
        if self._other_alarm(n):
            self._finish(test, now, ABORTED, "01")

    def _out_of_bounds(self, test, height, degrees):
        """The first of p.24-1's conditions that no longer holds, or None."""
        if height < MIN_WATER:
            return "02"
        if height > self.range_of(test.sensor) - HIGH_MARGIN:
            return "03"
        if degrees < TEMP_LOW:
            return "04"
        if degrees > TEMP_HIGH:
            return "05"
        # a hair of tolerance, so a drop of exactly a quarter inch is one
        if height - test.start_ht >= RISE - 1e-9:
            return "06"
        if test.start_ht - height >= DROP - 1e-9:
            return "07"
        return None

    def _finish(self, test, at, status, reason="00"):
        n = test.sensor
        test.status, test.reason, test.end_at = status, reason, at
        test.end_ht, test.end_temp = self.height_at(n, at), self.temp_at(n, at)
        test.suppress_until, test.dry_since = at + SUPPRESS_AFTER, None
        if status == PASSED:
            rows = self.passed.setdefault(n, [])
            rows.insert(0, test)
            del rows[KEEP_PASSED:]
        self._print("result", test, at)

    def _watch_empty(self, n, test, now):
        """"The sump is considered to be empty if there is no water detected
        for 5 consecutive minutes." Looked at once a tick."""
        if test.suppress_until is None or now >= test.suppress_until:
            return
        if self.height_at(n, now) > 0.0:
            test.dry_since = None
            return
        if test.dry_since is None:
            test.dry_since = now
        if now - test.dry_since >= EMPTY_FOR:
            test.suppress_until = now

    def _print(self, what, test, at):
        self.printed.append((what, test.sensor, test.snapshot(), at))

    # ---- what a running test has worked out --------------------------------
    @staticmethod
    def temp_rate(test, at):
        """Degrees F an hour between the two five-minute averages ending at
        `at`, or None without a reading in both."""
        recent = [t for when, _h, t in test.samples if at - PERIOD < when <= at]
        before = [t for when, _h, t in test.samples
                  if at - 2 * PERIOD < when <= at - PERIOD]
        if not recent or not before:
            return None
        return (_mean(recent) - _mean(before)) * 3600.0 / PERIOD

    @staticmethod
    def leak_rate(test, at):
        """Inches an hour the water went DOWN over the last two hours, or
        over the whole phase before it has two. A rise is a negative rate."""
        window = [(when, h) for when, h, _t in test.samples
                  if at - FIRST_LOOK <= when <= at]
        if len(window) < 2 or window[-1][0] <= window[0][0]:
            return 0.0
        (first, h0), (last, h1) = window[0], window[-1]
        return (h0 - h1) * 3600.0 / (last - first)

    def rates(self, test):
        """(RR, temp rate, minutes stable, LL, leak rate), 317's four fields.

        UNKNOWN outside the Measuring Height Phase, COMPUTING for its first
        ten minutes, then VALID -- and the temperature's flag is STABLE once
        it is, which is when the screen swaps TEMP RATE for TMP STABLE.
        """
        if test is None or test.start_at is None:
            return "00", None, None, "00", None
        at = test.last
        since = at - test.start_at
        if since < COMPUTING:
            return "02", None, None, "02", None
        boundary = test.start_at + (since // PERIOD) * PERIOD
        trate = self.temp_rate(test, boundary)
        leak = self.leak_rate(test, at)
        if test.stable_at is not None and test.stable_at <= at:
            return "03", trate, (at - test.stable_at) / 60.0, "01", leak
        return "01", trate, None, "01", leak

    def values(self, test, now=None):
        """[start ht, start temp, end ht, end temp, minutes], 317 to 31A's
        five floats. A running test's END is where the water is now."""
        if test is None or test.start_at is None:
            return [0.0, 0.0, 0.0, 0.0, 0.0]
        if test.running:
            now = self.now() if now is None else now
            end_ht = self.height_at(test.sensor, now)
            end_temp = self.temp_at(test.sensor, now)
            end_at = now
        else:
            end_ht, end_temp, end_at = test.end_ht, test.end_temp, test.end_at
        return [test.start_ht, test.start_temp, end_ht, end_temp,
                max(0.0, (end_at - test.start_at) / 60.0)]

    # ---- the records -------------------------------------------------------
    def last_passed(self, n):
        rows = self.passed.get(int(n)) or []
        return rows[0] if rows else None

    def last_ten(self, n):
        return (self.passed.get(int(n)) or [])[:10]

    def each_year(self, n, most=10):
        """The newest pass in each calendar year, newest year first."""
        out, years = [], set()
        for test in self.passed.get(int(n)) or []:
            year = time.localtime(test.start_at).tm_year
            if year in years:
                continue
            years.add(year)
            out.append(test)
            if len(out) == most:
                break
        return out

    # ---- the alarms it holds off, and the ones the water raises -------------
    def suppressed(self, n):
        """Are this sensor's water alarms and warnings held off right now?"""
        test = self.tests.get(int(n))
        if test is None:
            return False
        now = self.now()
        if test.running:
            # "At any time during the test the water height is greater than
            # the sensor length minus 2 inches, then the water/fluid
            # alarms/warnings are no longer suppressed."
            return (self.height_at(n, now)
                    <= self.range_of(n) - HIGH_MARGIN)
        return test.suppress_until is not None and now < test.suppress_until

    def conditions(self):
        """WATER WARNING and WATER ALARM from the water that is in the sump.

        Judged against the heights S728 holds, which Mag Sensor Setup
        programs and nothing used to read. A sensor the bench has put into a
        state of its own is left to that state.
        """
        out = []
        for n in self.c.mag_sensors():
            if self.c.sensor_state.get(("smart", str(n)), "normal") != "normal":
                continue
            if self.suppressed(n):
                continue
            height = self.height(n)
            if height > self.alarm_height(n):
                out.append(f"2807{n:02d}")
            elif height > self.warning_height(n):
                out.append(f"2806{n:02d}")
        return out

    # ---- the panel ---------------------------------------------------------
    def screen(self, what, n):
        """The Mag Sump screens, 576013-610 Rev AC p.23-1 and p.24-3."""
        n = int(n)
        label = self.c.text("722", n) or f"SUMP {n}"
        if what == "ht_temp":
            left = f"{self.height(n):7.3f} IN"
            return f"{left}{f'{self.temperature(n):.1f} F'.rjust(24 - len(left))}"
        test = self.tests.get(n)
        if what == "rates":
            head = f"s {n}: "
            rr, trate, stable, _ll, leak = self.rates(
                test if test and test.status == MEASURING else None)
            if rr == "00":
                return head + "TEMP RATE: UNKNOWN" + chr(10) + "LEAK RATE: UNKNOWN"
            if rr == "02":
                return (head + "TEMP RATE: COMPUTING" + chr(10)
                        + "LEAK RATE: COMPUTING")
            bottom = f"LEAK RATE: {leak:.4f} IN./HR"
            if rr == "03":
                return head + f"TMP STABLE: {stable:.0f} MINS" + chr(10) + bottom
            return head + f"TEMP RATE: {trate or 0.0:.1f} F/HR" + chr(10) + bottom
        if what == "status":
            if test is None:
                return f"s {n}: {label}" + chr(10) + "NO TEST DATA AVALIABLE"
            if test.status == PASSED:
                when, word = test.start_at, "TEST PASSED"
            elif test.status == ABORTED:
                when = test.start_at or test.test_at
                word = abort_line(test.reason)
            elif test.status == FILL_SUMP:
                when, word = test.test_at, "STATUS: FILL SUMP"
            else:
                when = test.start_at
                word = ("STATUS: MEASURING HEIGHT"
                        if test.stable_at is not None
                        else "STATUS: CHK TEMP STABLE")
            return f"s {n}: {stamp(when)}" + chr(10) + word
        if what == "last_passed":
            test = self.last_passed(n)
            if test is None:
                return f"s {n}: {label}" + chr(10) + "NO TEST DATA AVALIABLE"
            return f"s {n}: {stamp(test.start_at)}" + chr(10) + "LAST PASSED TEST"
        return ""


def abort_line(reason):
    """"ABORT: WATER TOO LOW", in twenty-four columns.

    Two of the eleven are eighteen letters and the screen has seventeen
    after `ABORT: `; the space goes rather than the last letter
    (UNKNOWNS A53).
    """
    line = "ABORT: " + ABORT_WORDS.get(reason, "")
    return line if len(line) <= 24 else line.replace("ABORT: ", "ABORT:", 1)
