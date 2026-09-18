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

from .clock import clock_date

# The three rates a TLS-350 tests at, in gallons per hour.
RATES = {"gross": 3.0, "periodic": 0.2, "annual": 0.1}

# How long each one takes. The tank tests take what they are programmed to
# take; these are the volumetric line tests, which the console times itself.
#
# 576013-849 Rev B p.39, Table 5, "Test Type Reference Numbers and Times",
# in a column headed `Test Length (Seconds)`: the 3.0 GPH Line Test is 13.5
# seconds, the 0.2 GPH Line Test 326 and the 0.1 GPH Line Test 794 -- rows
# 3, 7 and 11, which are the three reference numbers `wirelines.py` has read
# out of the LEFT column of the same table all along. The page's own
# sentence over it is "More precise tests take longer to run".
#
# These were 0.05, 0.75 and 8.0 HOURS, uncited, so every volumetric line
# test ran 13 to 36 times too long and a 0.1 gph test was eight hours where
# the page gives thirteen minutes. 576013-635's B51 and B52 sample
# printouts reproduce twelve of Table 5's fourteen lengths exactly in their
# own `LGTH` column, in both revisions, which is what raises this above one
# reading of one table. FIDELITY H17.
LINE_SECONDS = {"gross": 13.5, "periodic": 326.0, "annual": 794.0}

# The SELF-tests beside them, rows 4, 8 and 12 of the same table, which the
# console runs six of for an Air Purge. Table 5 gives each the same length
# as its own line test and a shorter typical time -- 5-7 seconds against
# 13.5, 104-156 against 326, 250-395 against 794 -- which is the one place
# in the table the two columns disagree.
SELFTEST_SECONDS = {"gross": 13.5, "periodic": 326.0, "annual": 794.0}

PASSED, FAILED, INVALID = "PASSED", "FAILED", "INVALID"

# What a TANK results screen calls them. 576013-610 p.9-2: "the results
# (PASS, FAIL, or INVALID)", where the line screens on pp.51 and 61 draw
# `DATE  3.0 PASSED`. Two screens, two word sets, one console. FIDELITY H13.
TANK_WORDS = {PASSED: "PASS", FAILED: "FAIL", INVALID: "INVALID"}

# "If you start an In-Tank Leak Test manually, you will need to stop the test
# manually. Otherwise, the test will run for 24 hours" -- 576013-610 Rev AC
# p.20-3 and p.20-5, which is the only ceiling either page gives.
MANUAL_STOP_HOURS = 24.0

# Table 29-4, "In-Tank Leak Detection Invalidation Criteria", 576013-610
# Rev AC p.29-6. Every row is headed "Printout Message (Not displayed)":
# these go on the paper under a FLAGS heading and never on the front panel,
# which is why no amount of reading the display chapters turns them up.
# UNKNOWNS A13e recorded that no manual published this vocabulary and that
# every printed example showed the heading empty. This is the table it was
# looking for, and LOW LEVEL TEST ERROR -- the one flag real paper had
# already given this project -- is in it twice, once generally and once for
# Mag probes whose fuel and water floats have come too close together.
FLAGS = (
    "RECENT DELIVERY",
    "LOW LEVEL TEST ERROR",
    "FIRST LEAK PERIOD ERROR",
    "LAST LEAK PERIOD ERROR",
    "TEMPERATURE OUT OF RANGE",
    "TEMP CHANGE TOO LARGE",
    "CHANGE IN TANK TEMP ZONE",
    "CHANGE IN HEAD TEMP",
    "LEAK TEST TOO SHORT",
    "PERCENT VOLUME TOO LOW",
    "PRODUCT LEVEL INCREASE",
)

# "A periodic test requires at least 2 hours to complete (3 hours for an
# annual test)", from the LEAK TEST TOO SHORT row of the same table, and
# 576013-610 Rev AC Table 20-1 Minimum In-Tank Leak Test Times gives the
# same pair against the probe:
#
#     0.2 gph    0.1 or 0.2 Magnetostrictive    2 hours
#     0.1 gph    0.1 Magnetostrictive           3 hours*
#     *Add one extra hour if 2" floats are installed.
#
# The asterisk is on the 0.1 gph row alone, and it matters here more than it
# looks: `presets.py` programs every tank's float size as `2.0 IN.`, so the
# SHIPPED BENCH is a two-inch-float site where the annual minimum is four
# hours. A 3.5 hour annual test was judged valid here and is
# `LEAK TEST TOO SHORT` on real iron. See FIDELITY H7.
MIN_HOURS = {"gross": 2.0, "periodic": 2.0, "annual": 3.0}
TWO_INCH_FLOAT = "1"        # S62F's own enum: 0=4.0, 1=2.0, 2=3.0, 3=1.0
TWO_INCH_EXTRA_HOURS = 1.0

# `S62C`, Periodic Test Type. 576013-623 Rev AN p.7-22: "You can choose
# between Standard and Quick. Choose Standard to run a 2-hour periodic leak
# test. Choose Quick to perform a 0.2 gph (0.76 lph) test in one hour."
# The setting was stored and read by nothing, so a Quick site's one-hour test
# was flagged LEAK TEST TOO SHORT against the Standard table. See FIDELITY H9.
QUICK_PERIODIC = "1"
QUICK_HOURS = 1.0

# `S61A`, Leak Test Early Stop, and p.8-8 gives it two behaviours. The first
# is a refusal: "When enabled this feature will prevent an In-Tank Leak Test
# from starting under the following conditions: 1. Fuel level is less than
# Leak Min Periodic (0.2 gph test rate) or Leak Min Annual (0.1 gph test
# rate). 2. It is less than 8 hours from a delivery. 3. The product
# temperature is less than 0 F (-17.6 C) or more than +100 F (+37.4 C). 4.
# The fuel level is too low."
#
# All four are conditions the engine already computes for `invalidations()`,
# in Table 29-4's own words -- so the feature is a gate in front of a test
# rather than a new measurement. The second behaviour is the one it is named
# for: "if you have Leak Test Early Stop enabled and the console determines
# that an in-tank leak test has passed after the first two hours of the
# test, the test is completed, even though you had entered a Leak Test
# Duration of more than 2 hours."
EARLY_STOP_HOURS = 2.0

# `S61B`, Gross Test Auto-Confirm. p.8-7: "If you are experiencing tank gross
# test alarms that are proven to be false, enabling this feature may reduce
# these false alarms. When enabled, two test fails in a row will be
# required before a Fail is posted. However, when enabled this feature will
# also increase the time needed to detect a gross leak by one 30-45 minute
# idle period."
AUTO_CONFIRM_FAILS = 2

# "Temperature reading is below 0 F (-17.8 C) or above 100 F (37.8 C)."
TEMP_RANGE = (0.0, 100.0)

# Table 29-4's three drift criteria, and what each one measures. The first
# two are the submerged thermistors -- their average, and any one of them --
# and the third is a sensor this console does not model: "the temperature of
# the thermistor in the probe HEAD", which is above the fuel and never
# submerged. See FIDELITY Y5 and Y10.
DRIFT_AVERAGE = 0.1     # "average temperature of all submerged thermistors"
DRIFT_ZONE = 0.3        # "a submerged thermistor's temperature"
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
# TANK TEST ACTIVE, and the setting that asks for it. 576013-623 Rev AN
# p.7-24 makes it a feature rather than a consequence -- "when on, the Tank
# Test Notify feature triggers a warning" -- and its own default table gives
# `Tank Test Notify OFF`. There is no line equivalent: the PLLD and WPLLD
# families have no notify setting and no ACTIVE alarm to gate.
ACTIVE_ALARM = {"tank": ("02", "20")}
ACTIVE_GATE = {"tank": "630"}

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
# Not one table: three. The three families number their shutdown rates
# differently and 576013-635 Rev AA says so on three consecutive pages --
# 757 is `01=3.00 02=0.20 03=0.10`, 784 is `01=0.10 02=3.00 03=0.20 04=None`
# and 7A4 is `01=0.20 02=3.00 03=0.10 04=None`. This was one flat copy of
# the VLLD one applied to all three, so a PLLD line programmed 3.0 GPH
# stored `02` and was shut down as though it had been programmed 0.2, and a
# line programmed 0.1 was evaluated as 3.0. Both directions wrong, on the
# families where a shutdown is the point.
#
# CLOSED.md records this exact bug as fixed, and it was -- in the field
# `choices`, which is what the panel writes and the wire reports. This
# module kept a second copy and nothing pointed the two at each other. So
# the rate is READ OFF THE FIELD now, which is the only way the two cannot
# drift again: the choices carry the code and its label, and the label is
# the rate. See FIDELITY U4.
def shutdown_rate(code, stored):
    """The gallons-per-hour a stored shutdown-rate code means, or None.

    None both for `04=None` and for a code the field does not offer.
    """
    from .console import FIELDS
    field = FIELDS.get(f"S{code}01") or {}
    for value, label in field.get("choices") or ():
        if str(value) != str(stored):
            continue
        head = str(label).split()[0]
        try:
            return float(head)
        except ValueError:
            return None            # NONE
    return None

# which tank minimum volume a test has to clear to be valid
MINIMUM_CODE = {"periodic": "636", "annual": "62A"}

# The order I208 lists a tank's results in: ANNUAL, PERIODIC, GROSS, which is
# by test RATE ascending -- 0.10, then 0.20, then 3.0. 576013-635 Rev AA p.71
# prints them that way and this console used `sorted(results.items())`, which
# is alphabetical and puts GROSS second. See FIDELITY H4.
#
# Read from the PDF's word coordinates. The plain extraction of that page
# reports ANNUAL/GROSS/PERIODIC -- the console's own wrong order -- and the
# page reports ANNUAL/PERIODIC/GROSS. A table read out of step would have
# confirmed the defect as correct.
REPORT_ORDER = ("annual", "periodic", "gross")


def in_report_order(results):
    """[(rate_key, Result)] in the order p.71 prints them."""
    return [(key, results[key]) for key in REPORT_ORDER if key in results]


class Result:
    """One finished test, as function 208 reports it."""

    def __init__(self, kind, device, rate_key, result, rate, hours, volume,
                 started, flags=(), manifolded=False):
        # why the console called it invalid, in Table 29-4's own words; the
        # slip prints them under a FLAGS heading
        self.flags = tuple(flags)
        # "mm - In-Tank Leak Manifold Status ... During Leak Test", so it is
        # what the tank was when the test RAN. A tank manifolded after the
        # fact does not change a result already on the roll. See FIDELITY H4.
        self.manifolded = bool(manifolded)
        self.kind = kind
        self.device = device
        self.rate_key = rate_key
        self.result = result
        self.rate = rate
        self.hours = hours
        self.volume = volume
        self.started = started

    def line(self):
        """One row of I208, in 576013-635 Rev AA p.71's own columns.

        The type at column 1, the stamp at 11 -- and it is the TWENTY-TWO
        character stamp, not `clock_words` -- then the result at 35 and the
        rate, hours and volume held right against 49, 55 and 63.

        The GROSS row carries no HOURS. The sample's other two print 12
        under that heading and its gross row leaves the column empty, which
        is the one thing on the page that says what a gross test IS: it is
        not measured over a period, it is a level read against a level.
        """
        from .clock import clock_wide
        hours = "" if self.rate_key == "gross" else f"{self.hours:.0f}"
        return (f" {self.rate_key.upper():<10s}"
                f"{clock_wide(self.started):22s}  "
                f"{self.result:<10s}{self.rate:5.2f}{hours:>6s}"
                f"{self.volume:8.0f}")


#: Where a test came from, and what the START slip says about it.
#:
#: `TEST BY PROGRAMMED TIME` is on the real 2024 roll, against a scheduled
#: test. `TEST BY EXTERN INTERFACE` is 576013-635's own display response to
#: function 052, "Start In-Tank Leak Detect Test" -- Revisions Y and AA and
#: both TLS-450 serial manuals print it -- so a test started over the wire
#: says that. What the console writes for a test somebody started AT THE
#: PANEL is on no paper this project has seen, and an external input's is
#: not the serial interface's, so both print nothing rather than a phrase
#: somebody invented. See FIDELITY W20 and H1.
ORIGIN_LINE = {"schedule": "TEST BY PROGRAMMED TIME",
               "wire": "TEST BY EXTERN INTERFACE",
               "panel": None, "input": None}


class Running:
    """A test in progress."""

    def __init__(self, kind, device, rate_key, hours, volume, started,
                 manual_stop=False, manifolded=False, origin="panel"):
        self.kind = kind
        self.device = device
        self.rate_key = rate_key
        self.hours = hours
        self.volume = volume
        self.started = started
        # Whether the test runs until somebody stops it. NOT where it came
        # from: the panel's STOP MODE screen sets this, and a hand-started
        # test with a fixed length has it False. The slip read it as the
        # origin, so a technician's own two-hour test printed TEST BY
        # PROGRAMMED TIME -- a schedule that does not exist. See FIDELITY H1.
        self.manual_stop = manual_stop
        self.origin = origin if origin in ORIGIN_LINE else "panel"
        # read at the START, because that is the moment I208 reports
        self.manifolded = bool(manifolded)

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
        # Slips the console printed by itself, drained by whoever holds the
        # paper. A real compliance roll is mostly these: a tank test prints
        # when it starts and again when it stops, unprompted, and the file
        # an inspector reads is two slips for every report. See FIDELITY
        # W10. Each entry is ("start", Running) or ("stop", device, when).
        self.printed_slips = []
        self._checked = None   # console time as of the last look, for schedules

    # ---- starting and stopping ---------------------------------------------
    LINES = ("plld", "wplld")

    def start(self, kind, device, rate_key, hours=None, manual_stop=False,
              origin="panel"):
        """Begin a test. Returns a message for the display.

        `origin` is where the START came from -- see `ORIGIN_LINE`. It
        defaults to the panel, which is the one the console says nothing
        about, so a caller that forgets it cannot make the slip claim a
        schedule.
        """
        if kind in self.LINES:
            # A line test is not a stopwatch over a volume, it is a sequence of
            # pressure measurements, so `pressure.Lines` runs it and hands the
            # result back through `record_line`.
            return self.c.lines.start(kind, device, rate_key)
        if kind == "tank" and device not in self.c.tank_level:
            return "NO TANK"
        if (kind, device) in self.running:
            return "TEST ALREADY RUNNING"
        if kind == "tank":
            # "When enabled this feature will prevent an In-Tank Leak Test
            # from starting under the following conditions", and the console
            # already has words for all four. `S61A` appeared in no Python
            # file at all. See FIDELITY H9.
            refused = self.early_stop_refusals(device, rate_key)
            if refused:
                return refused[0]
        if hours is None:
            hours = (LINE_SECONDS[rate_key] / 3600.0 if kind != "tank"
                     else 2.0)
        volume = self.c.tank_level.get(device, {}).get("volume", 0.0) \
            if kind == "tank" else self._line_volume(device)
        run = Running(kind, device, rate_key, float(hours), volume,
                      time.mktime(self.c.now()), manual_stop,
                      manifolded=(kind == "tank"
                                  and len(self.c.manifolded(device)) > 1),
                      origin=origin)
        self.running[(kind, device)] = run
        self.printed_slips.append(("start", run))
        return "TEST STARTED"

    def stop(self, kind, device):
        """STOP LEAK TEST. A test stopped early is invalid, not passed."""
        if kind in self.LINES:
            return self.c.lines.stop(kind, device)
        run = self.running.pop((kind, device), None)
        if run is None:
            return "NO TEST RUNNING"
        now = time.mktime(self.c.now())
        self.printed_slips.append(("stop", device, now))
        if run.manual_stop and run.elapsed(now) > 0:
            self._finish(run, now)
            return "TEST COMPLETE"
        # A test stopped early is invalid, and now says so in the manual's
        # own words rather than leaving the slip's FLAGS heading empty.
        hours = run.elapsed(now)
        flags = self.invalidations(run, hours) or ("LEAK TEST TOO SHORT",)
        self._record(Result(run.kind, run.device, run.rate_key, INVALID, 0.0,
                            hours, run.volume, run.started, flags,
                            run.manifolded))
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
                # "NOTE: If you start an In-Tank Leak Test manually, you
                # will need to stop the test manually. Otherwise, the test
                # will run for 24 hours." 576013-610 Rev AC p.20-3, and
                # again on p.20-5. Nothing ended one on a timer, so a test
                # started and left alone was still running nine hours later
                # and would have run until the process stopped. See
                # FIDELITY H5.
                if run.elapsed(now) >= MANUAL_STOP_HOURS:
                    self.running.pop(key, None)
                    self.printed_slips.append(("stop", run.device, now))
                    self._finish(run, now)
                continue
            if run.elapsed(now) >= run.hours or self._passed_early(run, now):
                self.running.pop(key, None)
                self.printed_slips.append(("stop", run.device, now))
                self._finish(run, now)
        # the lines move on pressure rather than on a stopwatch, but they move
        # on the same console time as everything else
        self.c.lines.tick()
        self._scheduled(stamp, now)

    def _passed_early(self, run, now):
        """Is this the test Leak Test Early Stop lets off the hook?

        "If you have Leak Test Early Stop enabled and the console determines
        that an in-tank leak test has passed after the first two hours of
        the test, the test is completed, even though you had entered a Leak
        Test Duration of more than 2 hours."

        Two hours, and no less than the minimum a valid test of this rate
        wants -- a test that has not run long enough has not "passed", it is
        LEAK TEST TOO SHORT, and completing it early would turn the feature
        into a way of failing compliance faster. See FIDELITY H9.
        """
        if run.kind != "tank" or not self.early_stop(run.device):
            return False
        hours = run.elapsed(now)
        if hours < max(EARLY_STOP_HOURS,
                       self.minimum_hours(run.device, run.rate_key)):
            return False
        rate = self.measured_rate(run.kind, run.device)
        return self._judge(run, rate, hours,
                           self.invalidations(run, hours)) == PASSED

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
            # And the STP goes off with it, at the moment of the verdict
            # rather than at the next handle-down. 577013-344 Rev H p.21
            # looks at this from the failure side -- "Verify that the pump
            # relay is not sticking, causing the STP to stay On when the
            # console is trying to shut it down" -- so the console's act is
            # to drop the relay, and `relays.pump_request` follows the
            # line's own pump. See FIDELITY U4.
            line = self.c.lines.line(kind, device)
            if line.pump:
                line.stop_pump()

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
            rate_key, hours, hhmm, on_this_day = plan
            for day in (-1, 0):
                due = self._at(stamp, hhmm, day)
                if not on_this_day(time.localtime(due)):
                    continue
                if last < due <= now and ("tank", tank) not in self.running:
                    self.start("tank", tank, rate_key, hours=hours,
                               origin="schedule")
        for kind, code in (("plld", "78C"), ("wplld", "7A3")):
            if not self.c.has(kind):
                continue
            # every line the card carries, and not the first four: a PLLD
            # has six. FIDELITY H16.
            for line in range(1, self.c.capacity(kind) + 1):
                raw = self.c.values.get(f"S{code}{line:02d}")
                if not raw or not raw.strip().endswith("1"):
                    continue          # 1 = REPETITIVE, and only repetitive
                if (kind, line) not in self.running:
                    self.start(kind, line, "periodic", origin="schedule")

    # Where the start TIME sits in S611's data, per method, and how long the
    # schedule in front of it is. 576013-635 Rev AA writes the whole command
    # out one line per method:
    #
    #     S611TTDDRMYYMMDDHHmm   (if M=1)   ON DATE
    #             MMWDHHmm       (if M=2)   ANNUALLY
    #             WDHHmm         (if M=3)   MONTHLY
    #             DHHmm          (if M=4)   WEEKLY
    #             HHmm           (if M=5)   DAILY
    #             <CR>           (if M=6)   AUTOMATIC
    #             <CR>           (if M=7)   CSLD
    #
    # DD is the duration, R the rate and M the method, so the schedule starts
    # at offset 4 and the time follows it. The same offsets are in the part
    # fields' `part_when` map, which is where they were checked against.
    SCHEDULE_WIDTH = {"1": 6, "2": 4, "3": 2, "4": 1, "5": 0}

    def _tank_schedule(self, tank):
        """(rate, hours, HHmm, is-it-due-today) from S611, or None.

        Five of the seven frequencies used to schedule nothing. This
        returned None for anything but DAILY, so ON DATE, ANNUALLY, MONTHLY
        and WEEKLY were stored, printed on the setup report and fired no
        test -- with their date fields fully modelled and the offsets
        already worked out. See FIDELITY H8.
        """
        raw = self.c.values.get(f"S611{tank:02d}")
        if not raw:
            return None
        body = raw[2:] if len(raw) > 8 else raw
        if len(body) < 8 or not body[:2].isdigit():
            return None
        hours = int(body[:2]) or 2
        rate_key = "annual" if body[2:3] == "1" else "periodic"
        method = body[3:4]
        width = self.SCHEDULE_WIDTH.get(method)
        if width is None:
            # 6 AUTOMATIC and 7 CSLD. CSLD has its own engine; AUTOMATIC is
            # H8a -- "to run the tests automatically when Line Leak Detector
            # or Pump Sense is installed", which names the condition for the
            # option to be OFFERED and not the moment it fires.
            return None
        schedule, hhmm = body[4:4 + width], body[4 + width:8 + width]
        if len(hhmm) < 4 or not hhmm.isdigit():
            return None
        due = self._due_on(method, schedule)
        return (rate_key, hours, hhmm, due) if due else None

    @staticmethod
    def _due_on(method, schedule):
        """Does this schedule fall on the day a given stamp is in?

        "D=Day of Week (1=Monday, 2=Tuesday, .. 7=Sunday), W=Week of Month
        (1-6), MM=Month (01-12)", which is the same encoding `fieldio`'s
        `schedule_text` reads and prints -- its `DAYS` starts at MON for the
        same reason.
        """
        def week_of(stamp):
            return (stamp.tm_mday - 1) // 7 + 1

        if method == "5":                       # DAILY
            return lambda stamp: True
        if method == "4":                       # WEEKLY: D
            if not schedule.isdigit():
                return None
            day = int(schedule)
            return lambda stamp: stamp.tm_wday == (day - 1) % 7
        if method == "3":                       # MONTHLY: WD
            if not schedule.isdigit():
                return None
            week, day = int(schedule[0]), int(schedule[1])
            return lambda stamp: (stamp.tm_wday == (day - 1) % 7
                                  and week_of(stamp) == week)
        if method == "2":                       # ANNUALLY: MMWD
            if not schedule.isdigit():
                return None
            month, week, day = (int(schedule[0:2]), int(schedule[2]),
                                int(schedule[3]))
            return lambda stamp: (stamp.tm_mon == month
                                  and stamp.tm_wday == (day - 1) % 7
                                  and week_of(stamp) == week)
        if method == "1":                       # ON DATE: YYMMDD
            if not schedule.isdigit():
                return None
            year, month, day = (int(schedule[0:2]), int(schedule[2:4]),
                                int(schedule[4:6]))
            full = 2000 + year if year < 70 else 1900 + year
            return lambda stamp: (stamp.tm_year == full
                                  and stamp.tm_mon == month
                                  and stamp.tm_mday == day)
        return None

    @staticmethod
    def _at(stamp, hhmm, offset_days=0):
        """Console epoch for HHmm on the day `offset_days` from this one."""
        return time.mktime((stamp.tm_year, stamp.tm_mon,
                            stamp.tm_mday + offset_days, int(hhmm[:2]),
                            int(hhmm[2:]), 0, 0, 1, -1))

    def _finish(self, run, now):
        # A manual-stop test reports what it actually RAN. `run.hours` is
        # whatever the START walk's DURATION step held, defaulting to two,
        # so a test run for nine hours and stopped by hand recorded 2.0 and
        # printed TEST LENGTH = 2.0 HRS. 576013-610 Rev AC p.9-2's own
        # sample is TEST LENGTH = 4.3 HRS, a figure that could only be
        # measured -- and LEAK TEST TOO SHORT was being judged against the
        # clamped number. See FIDELITY H6.
        if run.manual_stop:
            # Capped at the 24 the console would have stopped it on. The
            # bench's clock can jump an hour or a week in one tick, and a
            # test the console ended at 24 hours did not run for 25.
            hours = min(run.elapsed(now), MANUAL_STOP_HOURS)
        else:
            hours = min(run.elapsed(now), run.hours) or run.hours
        rate = self.measured_rate(run.kind, run.device)
        flags = self.invalidations(run, hours)
        result = self._judge(run, rate, hours, flags)
        self._record(Result(run.kind, run.device, run.rate_key, result, rate,
                            hours, run.volume, run.started, flags,
                            run.manifolded))
        if run.kind == "vlld":
            # "the line leak detector ALSO runs a pump side test" -- after
            # every VLLD test, however it was started. Only `record_line`
            # ran it, and `record_line` is the pressure engine's door,
            # which a VLLD test never comes through: a test started at the
            # panel finished here and the pump side went unmeasured.
            self.pumpside_test(run.device, run.rate_key, run.started)
        if result == FAILED:
            self._fail(run, rate)
        elif result == PASSED:
            # a passing test is the other way a fail alarm goes away, and the
            # way a shut-down line comes back when the method is Pass Line Test
            self.c.clear_posted(*FAIL_ALARM[run.kind][run.rate_key],
                                run.device)
            self.disabled.discard((run.kind, run.device))

    def invalidations(self, run, hours):
        """Which of Table 29-4's criteria this test tripped, in its words.

        This used to be three unnamed `return INVALID`s inside `_judge`: the
        console knew exactly why a test was invalid and threw the reason
        away, so the slip could only say INVL. The same shape as every other
        defect in FIDELITY.md's table -- the information was already here.

        Eight of the eleven can fire on this console: RECENT DELIVERY,
        PERCENT VOLUME TOO LOW, LEAK TEST TOO SHORT, PRODUCT LEVEL INCREASE,
        TEMPERATURE OUT OF RANGE, TEMP CHANGE TOO LARGE, CHANGE IN TANK TEMP
        ZONE and LOW LEVEL TEST ERROR. Two of them needed a probe that knows
        which thermistors are submerged, which is what Y5 built; the last
        needed the probe's own minimum detected fuel level, which is
        576013-818 Rev AB Table 9-2 and is the same threshold that raises
        `02/08`. See FIDELITY H11.

        Three cannot. The two leak period sample counts have no sample
        counter behind them, and CHANGE IN HEAD TEMP names a sensor this
        console does not model -- the thermistor in the probe HEAD is above
        the fuel and never submerged, and nothing on this shelf settles
        whether it is the sixth of A15's six or a seventh. All eleven are in
        `FLAGS` and printable. Which is which is worth writing down rather
        than pretending the list is complete.
        """
        if run.kind != "tank":
            return ()
        out = []
        now = time.mktime(self.c.now())
        if self.c.deliveries.during(run.device, run.started, now):
            out.append("RECENT DELIVERY")
        code = MINIMUM_CODE.get(run.rate_key)
        # a percent of the label volume, not gallons; see limit_volume
        minimum = self.c.limit_volume(code, run.device) if code else None
        if minimum and run.volume < minimum:
            # "Set Tank Periodic/Annual Leak Test Minimum Volume", which is
            # the field the PERCENT VOLUME TOO LOW row names as "the
            # programmed minimum"
            out.append("PERCENT VOLUME TOO LOW")
        # "Fuel level is too low, causing the fuel and water floats to be
        # too close together" -- Table 29-4's own cause, and the threshold
        # is the probe's minimum detected fuel level rather than anything
        # programmed. A test that STARTED above it and fell below during the
        # test is caught too: the criterion is "during a tank test".
        minimum = self.c.probe_minimums(run.device)[0]
        if minimum and (self.c.height_at(run.device, run.volume) < minimum
                        or self.c.fuel_below_minimum(run.device)):
            out.append("LOW LEVEL TEST ERROR")
        if hours + 1e-9 < self.minimum_hours(run.device, run.rate_key):
            out.append("LEAK TEST TOO SHORT")
        temp = self.c.product_temperature(run.device)
        if temp is not None and not TEMP_RANGE[0] <= temp <= TEMP_RANGE[1]:
            out.append("TEMPERATURE OUT OF RANGE")
        out += self._drift(run, now)
        volume = self.c.tank_level.get(run.device, {}).get("volume")
        if volume is not None and volume - run.volume > RATES[run.rate_key]:
            # "Fuel level increased more than the leak rate threshold value
            # during the test"
            out.append("PRODUCT LEVEL INCREASE")
        return tuple(out)

    def quick_periodic(self, tank):
        """`S62C`: is this tank's periodic test the one-hour Quick one?"""
        raw = (self.c.values.get(f"S62C{int(tank):02d}") or "").strip()
        return raw[-1:] == QUICK_PERIODIC

    def minimum_hours(self, tank, rate_key):
        """How long a valid test of this rate has to run on THIS tank.

        Table 20-1's footnote is a fact about the float kit, not about the
        console, so it has to be asked per tank: "Add one extra hour if 2"
        floats are installed", on the 0.1 gph row alone. And a Quick site's
        periodic test is an hour by definition rather than a short one. See
        FIDELITY H7 and H9.
        """
        if rate_key == "periodic" and self.quick_periodic(tank):
            return QUICK_HOURS
        hours = MIN_HOURS.get(rate_key, 2.0)
        if rate_key == "annual" and self.c.float_size(tank) == TWO_INCH_FLOAT:
            hours += TWO_INCH_EXTRA_HOURS
        return hours

    def early_stop(self, tank):
        """`S61A`: is Leak Test Early Stop enabled for this tank?"""
        raw = (self.c.values.get(f"S61A{int(tank):02d}") or "").strip()
        return raw[-1:] == "1"

    def early_stop_refusals(self, tank, rate_key):
        """Which of p.8-8's four conditions would stop a test starting.

        Named in Table 29-4's words, because they are the same four
        conditions that invalidate a test that ran -- the feature moves the
        judgement to the front instead of throwing away the hours.
        """
        if not self.early_stop(tank):
            return []
        out = []
        code = MINIMUM_CODE.get(rate_key)
        minimum = self.c.limit_volume(code, tank) if code else None
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        if minimum and volume < minimum:
            out.append("PERCENT VOLUME TOO LOW")
        now = time.mktime(self.c.now())
        if self.c.deliveries.during(tank, now, now):
            out.append("RECENT DELIVERY")
        temp = self.c.product_temperature(tank)
        if temp is not None and not TEMP_RANGE[0] <= temp <= TEMP_RANGE[1]:
            out.append("TEMPERATURE OUT OF RANGE")
        if self.c.fuel_below_minimum(tank):
            out.append("LOW LEVEL TEST ERROR")
        return out

    def _drift(self, run, now):
        """Table 29-4's two thermistor criteria this console can measure.

        Per HOUR, both of them, so a test of any length is judged on the
        rate rather than on the total -- "changed by more than 0.1 F per
        hour" and "more than 0.3 F per hour". A test shorter than a minute
        is not asked, because dividing a rounding error by a small enough
        number invalidates everything.
        """
        c = self.c
        span = (now - run.started) / 3600.0
        if span < 1.0 / 60.0:
            return []
        out = []
        under = c.submerged_thermistors(run.device) or [0]
        held_then = c.held_temperature(run.device, at=run.started)
        held_now = c.held_temperature(run.device)
        if held_now is not None or held_then is not None:
            # A tank the bench is HOLDING has no ladder of its own: every
            # thermistor reads the held figure, so the drift is the move in
            # the hold and it is the same on all six. Moving it is the only
            # way to stage these two criteria.
            was = [held_then if held_then is not None
                   else c.thermistor_ladder(run.device,
                                            at=run.started)[n]
                   for n in range(c.THERMISTORS)]
            is_now = [held_now if held_now is not None
                      else c.thermistor_ladder(run.device)[n]
                      for n in range(c.THERMISTORS)]
        else:
            was = c.thermistor_ladder(run.device, at=run.started)
            is_now = c.thermistor_ladder(run.device)
        before = sum(was[n] for n in under) / len(under)
        after = sum(is_now[n] for n in under) / len(under)
        if abs(after - before) / span > DRIFT_AVERAGE:
            out.append("TEMP CHANGE TOO LARGE")
        if any(abs(is_now[n] - was[n]) / span > DRIFT_ZONE for n in under):
            out.append("CHANGE IN TANK TEMP ZONE")
        return out

    def _judge(self, run, rate, hours, flags=()):
        threshold = RATES[run.rate_key]
        # A test that ran for no time measured nothing. The floor was a flat
        # 0.01 hours -- 36 seconds -- which is a sensible guard for a tank
        # test whose shortest legitimate length is hours, and invalidates a
        # VOLUMETRIC LINE test that ran its own full programmed length:
        # 576013-849 Rev B Table 5 gives the 3.0 GPH line test 13.5 seconds.
        # So the floor is the test's own length where that is shorter, and a
        # line test stopped part way is still invalid. FIDELITY H17.
        if flags or hours < min(0.01, run.hours):
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

        Each one lasts what 576013-849 Rev B Table 5 gives row 4, the
        3.0 GPH Line Self-Test: 13.5 seconds, the same length as the line
        test beside it. This was a hardcoded 0.1 hours, against the same
        table and for the same reason as `LINE_SECONDS`. FIDELITY H17.
        """
        now = time.mktime(self.c.now())
        hours = SELFTEST_SECONDS["gross"] / 3600.0
        for _ in range(6):
            rate = self.measured_rate("vlld", device)
            self._record(Result("vlld", device, "gross",
                                PASSED if rate < 3.0 else FAILED, rate,
                                hours, self.c.tank_level.get(device, {}).get(
                                    "volume", 0.0), now))
        return "AIR PURGE DONE"

    def _fail(self, run, rate):
        if run.kind == "tank" and not self._fail_alarm_enabled(run):
            return
        if run.kind == "tank" and not self._confirmed(run):
            # "When enabled, two test fails in a row will be required before
            # a Fail is posted" -- so the first fail is recorded in the
            # history and on the paper, and only the second raises the
            # alarm. `S61B` reached nothing. See FIDELITY H9.
            return
        self.c.post(*FAIL_ALARM[run.kind][run.rate_key], run.device)
        shutdown = self._shutdown_rate(run.kind, run.device)
        if shutdown is not None and rate >= shutdown:
            # the shutdown alarm stands as long as the line is down, so the
            # disabled set is the condition; nothing to post separately
            self.disabled.add((run.kind, run.device))

    def auto_confirm(self, tank):
        """`S61B`, Gross Test Auto-Confirm: is it on for this tank?

        "If you are experiencing tank gross test alarms that are proven to
        be false, enabling this feature may reduce these false alarms."
        """
        raw = (self.c.values.get(f"S61B{int(tank):02d}") or "").strip()
        return raw[-1:] == "1"

    def _confirmed(self, run):
        """Has this GROSS test failed twice in a row?

        The setting names the gross test and only the gross test -- it is a
        setup step of its own, headed Gross Test Auto-Confirm, and the cost
        it warns about is "the time needed to detect a gross leak". A
        periodic or annual fail posts on the first one however this is set.
        """
        if run.rate_key != "gross" or not self.auto_confirm(run.device):
            return True
        past = [r for r in (self.history.get((run.kind, run.device)) or [])
                if r.rate_key == "gross"]
        recent = [r.result for r in past[-AUTO_CONFIRM_FAILS:]]
        return (len(recent) >= AUTO_CONFIRM_FAILS
                and all(result == FAILED for result in recent))

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
        return shutdown_rate(code, body[-2:])

    # ---- what the site is actually doing ------------------------------------
    def measured_rate(self, kind, device):
        """Gallons per hour going missing, which is what a test measures.

        POSITIVE is a loss here, which is the sense the whole engine works
        in and the opposite of the sense the reports print -- 576013-818
        says it twice, "Leak rate in gph (negative number = a loss, no sign
        = a gain)", and the flip happens where a report is built.

        The clamp is gone. It was `max(0.0, ...)`, so a gain could not be
        represented at all: product entering a tank read as zero, and
        `CSLD RATE INCR WARN` -- which exists for exactly that condition,
        "indicates fluid is entering the tank during the leak test" -- could
        never see the thing it is for. A negative `tank_leak` is a tank
        filling from somewhere, which is chapter 11's Problem 3, a siphon
        leaking product back. See FIDELITY K2.
        """
        if kind == "tank":
            return self.c.tank_leak.get(device, 0.0)
        return self.c.line_leak.get((kind, device), 0.0)

    # Gallons a foot of pipe holds, by nominal size and by family.
    # 577013-465 Rev AD's pipe table gives 0.163 for 2 INCH and 0.367 for
    # 3 INCH across the steel and flexible families, and its worked example
    # of mixed piping gives the fiberglass pair: "site has 150 feet of 2"
    # fiberglass and 50 feet of 3" fiberglass pipe: Total line volume =
    # [150 x 0.204] + [50 x 0.461] = 30.6 + 23.1 = 53.7 gallons".
    VLLD_GAL_PER_FOOT = {
        "01": (0.163, 0.367),       # STEEL
        "02": (0.204, 0.461),       # FIBERGLASS
        "03": (0.204, 0.461),       # 2-WALL FIBERGLASS
        "04": (0.163, 0.367),       # FLEXIBLE
    }

    def _line_volume(self, device):
        """Gallons of product a VLLD line holds.

        This returned 0.0, which the slip printed as the test volume and
        then divided by for the percent-of-capacity column. A VLLD line is
        programmed as two lengths -- S753 for the 2 inch run and S754 for
        the 3 inch -- and the rule is the application guide's own: "multiply
        the line length (in feet) times the 'gallons/foot' value for each
        pipe type and add the results." See FIDELITY R10.
        """
        raw = (self.c.values.get(f"S756{device:02d}") or "").strip()
        two, three = self.VLLD_GAL_PER_FOOT.get(
            raw[-2:], self.VLLD_GAL_PER_FOOT["01"])
        gallons = ((self.c.limit("753", device) or 0.0) * two
                   + (self.c.limit("754", device) or 0.0) * three)
        return max(0.0, gallons)

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
        if res.result == INVALID and (kind in self.LINES or kind == "vlld"):
            return "INVALID"
        if kind in self.LINES or kind == "vlld":
            # 576013-610 Rev AC p.51 and p.61: a LINE result screen reads
            # "DATE       3.0 PASSED" -- the date the test ran, then the rate
            # and the verdict. The rate-by-rate block with LAST TEST and the
            # counts is the PRINTOUT, not the screen.
            rate = {"gross": "3.0", "periodic": "0.20",
                    "annual": "0.10"}.get(rate_key, f"{res.rate:.2f}")
            when = clock_date(res.started, year=False)
            said = f"{rate} {res.result}"
            return f"{when}{said.rjust(24 - len(when))}"
        # 576013-610 Rev AC pp.9-1 and 9-2 draw `T #: (Product Name)` over
        # `GRS: (Date) (Results)`, three times, and p.9-2 says what the two
        # halves are: "The system prints the date the test ran and the
        # results (PASS, FAIL, or INVALID)". This printed a RATE and no
        # date -- `PER: PASSED  0.00 GAL/HR` -- where the LINE branch two
        # lines above had had the right shape and its own citation all
        # along. The word set is chapter 9's own, and it is shorter than the
        # line screens': PASS where they say PASSED. See FIDELITY H13.
        #
        # The date drops its year here where CSLD's `PER:` line keeps one,
        # because the two screens have different words after the date:
        # `PER: JAN 22, 1996 PASS` is 22 characters and fits, and
        # `GRS: JAN 22, 1996 INVALID` is 25 against a 24-column line. Each
        # is followed where it is printed.
        return f"{clock_date(res.started, year=False)} " \
               f"{TANK_WORDS.get(res.result, res.result)}"

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
            sudden = self.c.limit_or_default(SUDDEN_LIMIT_CODE, device)
            if sudden and lost >= sudden:
                out.append(SUDDEN_LOSS[0] + SUDDEN_LOSS[1] + f"{device:02d}")
            leak = self.c.limit_or_default(LEAK_LIMIT_CODE, device)
            # The limit is read on the loss itself. This used to scale it by
            # 0.998, the flat factor tc_volume once used, which was
            # meaningless twice over: a temperature correction applies to a
            # VOLUME and not to a difference between two of them, and the
            # factor it borrowed was wrong anyway.
            if leak and lost >= leak:
                out.append(LEAK_ALARM[0] + LEAK_ALARM[1] + f"{device:02d}")
        return out

    def conditions(self):
        """The alarms a test in progress puts up by itself."""
        out = []
        for (kind, device), run in sorted(self.running.items()):
            aa_nn = ACTIVE_ALARM.get(kind)
            if aa_nn and (kind not in ACTIVE_GATE
                          or self.c.test_notify(device)):
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
