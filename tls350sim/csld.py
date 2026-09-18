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
"""CSLD: the test that never shuts the tank down.

"Continuous Statistical Leak Detection, CSLD, is a tank leak detection method
that allows the tank to be tested without shutting down the tank ... CSLD
continuously monitors the tank level to determine when the tank is idle (no
dispensing or deliveries in progress). A single leak test is then performed
during the identified idle period. The result of this test is added to a
database of leak test results. The database is statistically analyzed to
produce a final test result."

So that is what this does. It needs three things before it will run on a tank,
all of which the console knows about: the CSLD software module key, a leak test
method of CSLD, and a tank quiet enough to test.

"Test results are provided automatically every 24 hours except when the CSLD
Report Only feature is enabled in setup."
"""
import math
import time

from . import readings
from .clock import clock_date

# A tank is idle when nothing is moving it faster than this.
#
# A real console decides idle from ACTIVITY -- "no dispensing or deliveries in
# progress" -- and dispensing is a different order of magnitude from any loss:
# one hose at 10 gallons a minute is 600 gph, and the slowest dispenser a site
# runs is still several hundred. A leak the console tests for is 3.0 gph at
# the very top. So the threshold belongs between the two, not underneath both,
# and the meters and the delivery watcher are asked first because they are
# what the console actually knows.
#
# This number is the simulator's own and the manual gives none, so what
# stands behind it is the reasoning above and not a page. The comment that
# used to stand here argued the case from a behaviour the manual describes as
# CORRECT: it said a 1.0 threshold "made CSLD blind to exactly the leaks it
# exists to find ... the console reported NO CSLD IDLE TIME instead of a
# failed test", where p.11-10 lists that outcome among the things a console
# does properly -- "Very large leaks may look like a product dispense. If
# this occurs the system will post a NO CSLD IDLE TIME alarm since it appears
# that product is being continually dispensed from the tank. Stop all
# activities and run a Static Leak Test." The VALUE may stand; the argument
# had the manual backwards. See FIDELITY K5.
IDLE_GPH = 100.0

# How often the database is analysed into a result.
REPORT_HOURS = 24.0

# ---------------------------------------------------------------------------
# Figure 11-2, "CSLD Leak Test Timing Sequence", is every number below.
# ---------------------------------------------------------------------------
# "Tank goes idle and must remain so for 8 minutes." This console waited an
# HOUR, seven and a half times that, and then took an instantaneous sample
# whose INTVL was the whole idle period. A CSLD test has a start, a length
# and an end. See FIDELITY K5 and K6.
IDLE_MINUTES = 8.0
# "one sample delay (30 seconds)", which is one row of the 30-second table.
SAMPLE_DELAY_MINUTES = 0.5
# "PRE-START: 5 minutes + Accept time (dynamic variable of 0 - 45 minutes
# duration)".
PRE_START_MINUTES = 5.0
# "Minimum test time 15 minutes + Feedback time (dynamic variable of 0 - 45
# minutes duration)". The 15 was in this file as `MINIMUM_MINUTES`, marked as
# the simulator's own; it is the manual's, and so is the term added to it.
TEST_MINUTES = 15.0
# "A maximum test time of 3 hours is imposed", measured from the end of the
# pre-delay -- which is what the rate table's own longest interval says.
# p.11-30: "CSLD will complete a test after 3 hours and start a new test if
# the tank remains idle", and "Test intervals are less than 3 hours because
# CSLD eliminates the first part of a test. The amount of time eliminated
# varies with the feedback variables." Figure 11-3 prints 174.5 minutes three
# times, which is 180 less the 5.5 of sample delay and pre-start.
MAX_SEQUENCE_MINUTES = 180.0

# What the test is RATED at, which is what the screens and the paper call
# it -- "0.2 GAL/HR TEST". It is not the threshold it is judged against.
RATE = 0.2

# The threshold IS four numbers, and 576013-818 prints the same four twice:
# p.11-11 under ALARM: PERIODIC TEST FAIL, "This message is posted when CSLD
# data indicates a high probability that a tank is leaking. The threshold for
# this determination is shown below", and p.11-9 under ALARM: CSLD RATE INCR
# WARN, "a higher than acceptable positive increase in product calculated
# from the CSLD Rate Table. The threshold amounts are listed below."
#
#     Single Tanks:      PD - 95% = +0.17 gph    PD - 99% = +0.16 gph
#     Manifolded Tanks:  PD - 95% = +0.16 gph    PD - 99% = +0.15 gph
#
# A flat 0.2 passes a 0.17 gph loss that a real console fails at either
# setting, and it does so identically for both -- so `S613` reached nothing.
# See FIDELITY K4 and K2a.
THRESHOLDS = {(False, "1"): 0.17, (False, "2"): 0.16,
              (True, "1"): 0.16, (True, "2"): 0.15}
THRESHOLD_PD = ("1", "2")
# "f - Probability of Detection: 1=95%, 2=99%, 3=CUSTOM (Inquiry Command
# Only)", 576013-635 p.253. A console nobody has told reads at 95%, which is
# the looser of the two and the one a bare console has to be.
DEFAULT_PD = "1"

SAMPLES_WANTED = 3

# "The CSLD option appears only when the tank is equipped with a 0.1 gph
# (0.38 lph) Mag probe", 576013-623 p.8-2, said twice on two pages.
CSLD_PROBE_RATING = "0.10"

# S61C's four values, 576013-623 p.8-9: "Disabled/End of Month/Day 15 and End
# of Month/Day 25 and End of Month", all the enabled ones at 8:00 a.m.
REPORT_ONLY_OFF = "0"
REPORT_ONLY_DAYS = {"1": (), "2": (15,), "3": (25,)}
REPORT_ONLY_HOUR = 8

PASS, FAIL, NONE, INCR, WARN = ("PASS", "FAIL", "NO RESULTS", "INCR", "WARN")

# 576013-818 p.11-11 heads a section with it, and it is what a TLS-350 says
# where a TLS-450 says COLLECTING. See FIDELITY K7.
NONE_LINE = "NO RESULTS AVAILABLE"
RESULT_CODE = {PASS: "01", FAIL: "02", NONE: "03", INCR: "08", WARN: "09"}

# "No CSLD Idle Time Warning" and "CSLD Rate Increase Warning", from i10100
NO_IDLE_ALARM = ("02", "21")
RATE_INCREASE_ALARM = ("02", "23")

# CSLD tests at 0.2 gph, so a CSLD failure IS the tank's periodic leak test
# failure and it posts the alarm the manual gives that: "02 ... 14=Tank
# Periodic Leak Test Fail Alarm". Not shutting the tank down is the whole
# point of CSLD; not SAYING anything would make it useless.
PERIODIC_FAIL_ALARM = ("02", "14")


# ---------------------------------------------------------------------------
# The four diagnostic tables 576013-818 chapter 11 is read from, and their
# own cadences. See FIDELITY K1.
# ---------------------------------------------------------------------------

# IA54: "averaged probe data collected every 30 seconds. CSLD uses this data
# to determine if the tank is idle or active", and its sample's rows are
# 30 seconds apart -- 132554, 132624, 132654, 132724.
PROBE_SECONDS = 30.0
# An hour of them. The probe reads about once a second and the record says
# how many went into it: the sample's SMPLS column alternates 30 and 31.
PROBE_RECORDS = 120
# Chapter 11's own samples run 21 to 29 and the serial
# manual's 30 to 31, so the probe is read about once a second
# and the count varies with what else the board was doing.
PROBE_SAMPLES = (21, 31)

# IA53: "This report contains volume samples collected once every hour. CSLD
# uses this data to determine the amount of dispensing that has occurred
# during the last 24 hours", and the display is a grid of 24 cells.
VOLUME_HOURS = 24

# "All tests rejected with error code 2 started within 2 hours of a
# delivery", p.11-30's own reading of its own rate table. This was an hour,
# and was one of the numbers FIDELITY K1a listed as the simulator's own.
DELIVERY_HOURS = 2.0

# IA51's "ss - Test acceptability". The console had three of the six.
ACCEPTABLE = "00"
REJECT_DURATION = "01"       # less than minimum duration requirement
REJECT_DELIVERY = "02"       # within delivery threshold
REJECT_DISPENSING = "03"     # excessive dispensing
REJECT_TEMPERATURE = "04"    # excessive temperature change
REJECT_DEVIATION = "06"      # outside weighted STD

# Above this many degrees an hour a test is thrown out: "04=Rejected -
# excessive temperature change". The code exists and the manual gives no
# number for it, so this one is the simulator's own -- see FIDELITY K1a.
TEMPERATURE_RATE_LIMIT = 0.25

# "01=Rejected - less than minimum duration requirement", and the requirement
# is Figure 11-2's own: `TEST_MINUTES` plus the feedback control variable.
# Figure 11-3's two rejected rows ran 19.5 minutes, which is over the 15 and
# under 15 + any feedback worth the name; p.11-30 reads the same table the
# same way -- "All the tests with status code 1 were rejected due to short
# intervals."

# IA52's status, and 576013-818 chapter 11 gives every threshold behind it
# where the serial manual gives only the names:
#
#     0 NO TEST - no evaluation.
#     1 PASS
#     2 FAIL
#     3 NOT USED.
#     4 INVALID - obsolete.
#     5 NO DATA:COUNT - not enough tests available to evaluate. There must
#       be at least 2 acceptable tests.
#     6 NO DATA:INTERVAL - not enough total test time to evaluate (<6 hours).
#     7 NO DATA:RANGE - tests did not range over a sufficient time period.
#       test time < 10 hours AND tests date range < 5 DAYS.
#     8 WARNING INCREASE - excessive positive leak rate.
#     9 WARNING NEGATIVE_HOLD - 2 day waiting period before reporting a
#       failure.
A52_NO_TEST = "0"
A52_PASS = "1"
A52_FAIL = "2"
A52_NO_COUNT = "5"
A52_NO_INTERVAL = "6"
A52_NO_RANGE = "7"
A52_INCREASE = "8"
A52_HOLD = "9"

MIN_ACCEPTABLE = 2          # "at least 2 acceptable tests"
MIN_INTERVAL_HOURS = 6.0    # "not enough total test time to evaluate"
RANGE_HOURS = 10.0          # "test time < 10 hours AND"
RANGE_DAYS = 5.0            # "tests date range < 5 DAYS"
NEGATIVE_HOLD_DAYS = 2.0    # "2 day waiting period before reporting"

# "RJT: Of the last 20 tests completed, this is the number of tests rejected
# due to excessive positive leak rate (>0.4 gph)". Positive is a GAIN in the
# manual's sign, which is a negative rate in this engine's -- see K2.
POSITIVE_LEAK = 0.4
RJT_WINDOW = 20

# "FDBK: Feedback control variable, range 0 to 45 minutes" and "ACPT: Accept
# control variable, range 0 to 45 minutes".
CONTROL_MAX_MINUTES = 45.0


def _tenth(value):
    """Round to a tenth the way the manual's own FDBK column does.

    Its steps are eighths of a minute, so half of them land on a tie, and
    the samples break every tie upwards: C1=74 is 38.25 and prints 38.3,
    C1=58 is 20.25 and prints 20.3, C1=70 is 33.75 and prints 33.8. Python
    rounds a tie to even and would print the first two of those a tenth
    short.
    """
    return math.floor(value * 10.0 + 0.5) / 10.0


class CSLD:
    """One console's worth of continuous testing."""

    def __init__(self, console):
        self.c = console
        self.samples = {}      # tank -> [(when, rate)] newest last
        self.detail = {}       # tank -> [the whole sample], for diagnostics
        # tank -> the rate of each of the last few tests that COMPLETED,
        # which is a wider set than the rate table: a test whose gain is
        # over the gate finishes and is thrown away rather than recorded,
        # and RJT is the count of exactly those. See `_sample`.
        self.completed = {}
        self.results = {}      # tank -> (result, when)
        self.idle_from = {}    # tank -> when the tank went quiet
        # tank -> when an IDLE PERIOD was last seen: eight minutes of quiet,
        # which is what p.11-10's alarm asks for and is a lower bar than a
        # test getting far enough to be recorded
        self.idle_seen = {}
        self.testing = {}      # tank -> when the TEST period began
        self.test_at = {}      # tank -> when the next test period may begin
        self.reported = {}     # tank -> when the database was last analysed
        self._seen = {}        # tank -> (volume, when)
        # the end of the last interval this walked, so the next one knows
        # where it begins
        self._watched = None
        self.watching = {}     # tank -> when CSLD started looking at it
        # IA54's 30-second probe records and IA53's hourly volumes. Both are
        # rolling buffers with a cadence of their own, and both are what the
        # other tables are DERIVED from -- the probe table decides idle from
        # active, and the volume table is where the dispense factor comes
        # from. See FIDELITY K1.
        # Every verdict this tank has been given, newest last, which is
        # what IA56's month of state changes is a window onto. `results`
        # holds only the latest, so the monthly report had nothing to read.
        # See FIDELITY K3.
        self.history = {}      # tank -> [(when, IA56 state code)]
        self._passes = {}      # tank -> [(when, volume)] for passed analyses
        self.probe = {}        # tank -> [30-second record], newest last
        self.probe_at = {}     # tank -> the last 30-second boundary sampled
        self.hourly = {}       # tank -> [(when, volume)], newest last
        self.hourly_at = {}    # tank -> the last hour boundary sampled

    # ---- the set, and which tank of it is tested ---------------------------
    def set_of(self, tank):
        """The tanks a siphon manifold makes one tank of, in order.

        The SIPHON partners alone, `S612`, because a siphon carries product
        between the tanks and a line manifold does not: what CSLD measures
        is the level, and only the siphon moves it. `console.manifolded`
        answers for both kinds because the reports it serves cover both.
        """
        out = {int(tank)}
        out.update(self.c.partners("612", int(tank)))
        return sorted(out)

    def primary(self, tank):
        """The tank of the set that is actually tested.

        "The primary tank is defined as the lowest numbered tank in the
        manifolded set", 576013-818 p.12-8, which is the rule BIR takes too.
        """
        return min(self.set_of(tank))

    def manifolded(self, tank):
        """Is this tank siphoned to another? The threshold depends on it."""
        return len(self.set_of(tank)) > 1

    def probability(self, tank):
        """`S613`'s f, which nothing read: "1=95%, 2=99%"."""
        raw = (self.c.values.get(f"S613{int(tank):02d}") or "").strip()
        digit = raw[-1:] if raw else ""
        return digit if digit in THRESHOLD_PD else DEFAULT_PD

    def threshold(self, tank):
        """The gph this tank's result is judged against, both ways round.

        The same number fails a loss and warns about a gain, because the
        manual gives one table and hangs both alarms off it.
        """
        return THRESHOLDS[(self.manifolded(tank), self.probability(tank))]

    # ---- is this tank on CSLD at all ---------------------------------------
    def enabled(self, tank):
        """The key, the method, and a probe rated to do it.

        576013-623 p.8-2, twice on two pages: "The CSLD option appears only
        when the tank is equipped with a 0.1 gph (0.38 lph) Mag probe,
        and the system has the CSLD software module key installed." This
        checked the key and the probe CARD, so a tank carrying a MAG2 -- a
        0.2 gph probe -- ran CSLD, which is a 0.2 gph test the probe cannot
        resolve. See FIDELITY K6.
        """
        if not (self.c.licensed("csld") and self.c.has("probe")):
            return False
        if self.c.probe_type(tank) != "MAG PROBE":
            return False
        if self.c.probe_leak_rating(tank) != CSLD_PROBE_RATING:
            return False
        raw = self.c.values.get(f"S611{tank:02d}") or ""
        body = raw[2:] if len(raw) > 8 else raw
        return body[3:4] == "7"          # 7 = CSLD, from S611's method field

    def report_only(self, tank):
        """S61C, and it has four values rather than two.

        576013-623 p.8-9: "When enabled this feature inhibits the 'No CSLD
        Idle Time' and 'CSLD Incr Rate' alarms, and only prints CSLD status
        reports at one of the times selected below: End of Month (at 8:00
        a.m.), Day 15 and End of Month (both at 8:00 a.m.), or Day 25 and
        End of Month (both at 8:00 a.m.)."

        This tested `raw.endswith("1")`, so `2` and `3` -- both of them
        ENABLED settings -- read as disabled, and the method was called from
        nowhere at all. See FIDELITY K6.
        """
        raw = (self.c.values.get(f"S61C{tank:02d}") or "").strip()
        digit = raw[-1:] if raw else ""
        return digit if digit in REPORT_ONLY_DAYS else REPORT_ONLY_OFF

    def reporting_inhibited(self, tank):
        """"this feature inhibits the 'No CSLD Idle Time' and 'CSLD Incr
        Rate' alarms" -- any of the three enabled settings, not just the
        first."""
        return self.report_only(tank) != REPORT_ONLY_OFF

    # ---- watching ----------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.tank_level):
            if not self.enabled(tank):
                self.idle_from.pop(tank, None)
                continue
            if tank != self.primary(tank):
                # "The secondary tank in manifolded sets will have empty
                # rate tables!", p.11-19, annotating an IA51 that prints
                # RATE TABLE EMPTY -- and "Tanks programmed as manifolded
                # would have a common result", p.11-26. The set is one tank
                # as far as a level measurement goes, so it is tested once.
                self.idle_from.pop(tank, None)
                continue
            # Both samplers run whether the tank is idle or busy: the probe
            # table is what DECIDES which it is, and the volume table is the
            # record of dispensing. Only the leak-rate sampling below waits
            # for an idle hour.
            self._probe_samples(tank, now)
            self._hourly_volumes(tank, now)
            self._watch_window(tank, self._watched, now)
        self._watched = now

    # How finely a quiet stretch is walked. Eight minutes is the grain the
    # sequence is measured in -- "Tank goes idle and must remain so for 8
    # minutes" -- so nothing is missed by stepping at it, and a seven-hour
    # tick costs fifty steps rather than one.
    WALK_MINUTES = IDLE_MINUTES

    def _watch_window(self, tank, was, now):
        """Drive Figure 11-2's sequence across a whole interval.

        This used to be one call with one `now`, which asked the tank a
        single question about however much time had passed: at 36,000x, was
        anything selling in the last seven hours. One car answered yes and
        seven hours of quiet went unseen, so a site doing eight thousand
        gallons a day could never bank a test -- and a site doing none could
        bank one three hours long that never happened.

        The interval is walked instead, as `_periods` already walks the two
        sampling buffers for the same reason. The level check stays whole:
        a volume moving faster than `IDLE_GPH` is a measurement over the
        interval and does not decompose.
        """
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, when = self._seen.get(tank, (volume, now))
        hours = (now - when) / 3600.0
        self._seen[tank] = (volume, now)
        moving = hours > 0 and abs(volume - seen) / hours > IDLE_GPH
        if moving or self.c.deliveries.in_progress(tank) is not None:
            self._watch(tank, now, busy=True)
            return
        if was is None or now <= was:
            self._watch(tank, now, busy=self.busy(tank))
            return
        for start, end, busy in self.c.activity_segments(tank, was, now):
            if busy:
                self._watch(tank, start, busy=True)
                self._watch(tank, end, busy=True)
                continue
            self._watch(tank, start, busy=False)
            at, steps = start, 0
            while at < end and steps < 500:
                at = min(end, at + self.WALK_MINUTES * 60.0)
                self._watch(tank, at, busy=False)
                steps += 1

    # ---- the two rolling buffers -------------------------------------------
    def _periods(self, tank, now, cursor, seconds, keep):
        """The boundaries to fill in, newest last, at most `keep` of them.

        The bench's clock can cross an hour in one tick, so a sampler that
        assumed one period per tick would produce an empty table on a fast
        console and a ruinous one on a slow-running long session. This walks
        whole periods and never returns more than the buffer holds, because
        a rolling buffer that has fallen a week behind wants the most recent
        `keep` and not the week.
        """
        latest = now - (now % seconds)
        last = cursor.get(tank)
        if last is None:
            cursor[tank] = latest
            return [latest]
        if latest <= last:
            return []
        count = int((latest - last) / seconds)
        cursor[tank] = latest
        first = latest - (min(count, keep) - 1) * seconds
        return [first + n * seconds for n in range(min(count, keep))]

    def _probe_samples(self, tank, now):
        """IA54: "averaged probe data collected every 30 seconds"."""
        rows = self.probe.setdefault(tank, [])
        for at in self._periods(tank, now, self.probe_at,
                                PROBE_SECONDS, PROBE_RECORDS):
            volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
            rows.append({
                "at": at,
                # "ss - Number of samples averaged into this record", and the
                # manual's own column alternates 30 and 31
                "samples": readings.integer(PROBE_SAMPLES[0],
                                            PROBE_SAMPLES[1],
                                            "csldsmpl", tank, int(at)),
                "tcvol": self.c.tc_volume(tank),
                "height": self.c.height_at(tank, volume),
                "temp": self.c.product_temperature(tank, at=at),
                "toptemp": self.c.probe_top_temperature(tank, at=at),
                "bdtemp": self.c.probe_board_temperature(tank, at=at),
            })
        del rows[:-PROBE_RECORDS]

    def _hourly_volumes(self, tank, now):
        """IA53: "volume samples collected once every hour"."""
        rows = self.hourly.setdefault(tank, [])
        for at in self._periods(tank, now, self.hourly_at,
                                3600.0, VOLUME_HOURS):
            rows.append((at, self.c.tank_level.get(tank, {})
                         .get("volume", 0.0)))
        del rows[:-VOLUME_HOURS]

    def dispensed_today(self, tank):
        """What IA51's DSPNS column carries.

        "CSLD uses this data to determine the amount of dispensing that has
        occurred during the last 24 hours" -- said of the hourly volume
        table, so the dispense factor is read off it: every hour the level
        went DOWN, added up. An hour it went up is a delivery, not a sale.
        """
        rows = self.hourly.get(tank) or []
        total = 0.0
        for (_a, before), (_b, after) in zip(rows, rows[1:]):
            if after < before:
                total += before - after
        return total

    def moving_state(self, tank):
        """IA54's `DISPENSE STATE:` footer, off the probe table and the pump.

        "CSLD uses this data to determine if the tank is idle or active",
        which is the one place the manual says what the table is FOR -- and
        it is not the only input. Figure 11-2: "Idle is determined by: 1)
        analysis of a group of probe samples from the 30 second average
        table, and 2) checking the pump sense module (if available)."

        The pump half was missing HERE while the test sequence had it, and
        the field's own description is the sharpest statement of it:
        "DISPENSE STATE: ACTIVE -- tank activity. ACTIVE/IDLE. If followed
        by an asterisk (*), CSLD is using a Pump Sense signal to determine"
        it, 576013-818 Rev AA p.6-12. So the console printed a mark meaning
        the pump signal decides this line beside a value the pump signal had
        no part in. See FIDELITY K5.
        """
        if self.c.pump_running(tank):
            return "ACTIVE"
        rows = self.probe.get(tank) or []
        if len(rows) < 2:
            return "IDLE"
        moved = abs(rows[-1]["tcvol"] - rows[-2]["tcvol"])
        seconds = max(1.0, rows[-1]["at"] - rows[-2]["at"])
        return "ACTIVE" if moved / seconds * 3600.0 > IDLE_GPH else "IDLE"

    def moving_volume(self, tank):
        """IA54's `MOVING AVERAGE:`, which is a temperature compensated
        VOLUME and not a mean of leak rates -- the console averaged the
        latter, which is a different quantity in different units."""
        rows = self.probe.get(tank) or []
        if not rows:
            return 0.0
        recent = [r["tcvol"] for r in rows[-PROBE_RECORDS:]]
        return sum(recent) / len(recent)

    def _lead_minutes(self, tank):
        """The sample delay and the pre-start, which every test waits out.

        "one sample delay (30 seconds)" then "5 minutes + Accept time", and
        it is also what p.11-30 means by "CSLD eliminates the first part of a
        test. The amount of time eliminated varies with the feedback
        variables" -- so the test PERIOD is the sequence less this.
        """
        return SAMPLE_DELAY_MINUTES + PRE_START_MINUTES + self.accept(tank)

    def _max_test_minutes(self, tank):
        """How long a test may run before the console completes it anyway."""
        return max(TEST_MINUTES,
                   MAX_SEQUENCE_MINUTES - self._lead_minutes(tank))

    def _watch(self, tank, now, busy):
        """Figure 11-2's timing sequence, start to finish.

        PRE-DELAY, one sample delay, PRE-START, TEST IN PROGRESS, TEST
        COMPLETE, END DELAY -- and then round again while the tank stays
        quiet. This console had none of it: an hour of quiet produced an
        instantaneous sample whose INTVL was the hour, so a test had no
        length of its own, could not be halted by dispensing, and could not
        be rejected for being short. See FIDELITY K5 and K6.

        `busy` is the caller's, because the caller is what knows WHEN in
        the interval the tank was busy -- see `_watch_window`.
        """
        self.watching.setdefault(tank, now)
        if busy:
            # "Tank going active will abort test if insufficient data has
            # been collected" -- and a test that HAS enough data ends and is
            # kept, which is why chapter 11 can read a table full of short
            # intervals as "tests are halted by dispensing, not the 3-hour
            # CSLD limit".
            self._end_test(tank, now)
            self.idle_from[tank] = None
            self.test_at.pop(tank, None)
            return
        start = self.idle_from.get(tank)
        if start is None:
            self.idle_from[tank] = now
            # "Tank goes idle and must remain so for 8 minutes", and then
            # the sample delay and the pre-start before the test itself.
            self.test_at[tank] = now + (IDLE_MINUTES
                                        + self._lead_minutes(tank)) * 60.0
            return
        if (now - start) / 60.0 >= IDLE_MINUTES:
            # "Tank goes idle and must remain so for 8 minutes" -- and that,
            # on its own, is the "idle period" p.11-10's alarm is about,
            # whether or not a test ever comes of it.
            self.idle_seen[tank] = now
        if self.testing.get(tank) is None:
            if now >= self.test_at.get(tank, now):
                self.testing[tank] = now
        elif ((now - self.testing[tank]) / 60.0
                >= self._max_test_minutes(tank)):
            # "CSLD will complete a test after 3 hours and start a new test
            # if the tank remains idle."
            self._end_test(tank, now)
        self._analyse(tank, now)

    def _end_test(self, tank, now):
        """TEST COMPLETE, and the END DELAY before the next one.

        The bench's clock can cross three hours in a single tick, so what the
        test RAN is capped at what the console would have stopped it at
        rather than taken from the wall.
        """
        began = self.testing.pop(tank, None)
        if began is None:
            return
        ran = min((now - began) / 60.0, self._max_test_minutes(tank))
        self._sample(tank, now, ran)
        # "Several sample delay to clear current test data from test sequence
        # and prepare for next test", and then the pre-start again. The
        # 8-minute pre-delay is not waited out twice: the tank has been idle
        # throughout, which is what the flow chart asks.
        self.test_at[tank] = now + self._lead_minutes(tank) * 60.0

    def busy(self, tank):
        """Is anything the console can see selling out of this tank?

        The meter map is the console's own answer to "is the forecourt
        running", and it is a better one than watching the level, because the
        level moves for a leak as well.
        """
        for meter, where in (self.c.meters or {}).items():
            if int(where) == int(tank) and self.c.meter_flow.get(meter):
                return True
        # and a pump sense contact is the forecourt saying so directly
        return self.c.inputs.pump_on(tank)

    def volume_table_complete(self, tank):
        """"CSLD Volume Table must be complete", the third of Figure 11-2's
        three conditions for recording a test.

        IA53 is "volume samples collected once every hour ... to determine
        the amount of dispensing that has occurred during the last 24
        hours", and a console that has not been up for a day has not got
        them. So a freshly started console records no CSLD test until it
        has a day of volume behind it, which is a real console's behaviour
        and was not this one's. See FIDELITY K6.
        """
        return len(self.hourly.get(tank) or []) >= VOLUME_HOURS

    def _sample(self, tank, now, minutes):
        """One leak test, taken while nobody was looking.

        The rate is what the report needs; everything alongside it is what
        IA51 prints, so the sample keeps the lot rather than making them up
        again later -- and it has to, because several of them are readings
        of the moment the test ran rather than of now.
        """
        if not self.volume_table_complete(tank):
            return
        rate = self.c.leaks.measured_rate("tank", tank)
        st = self.c.tank_level.get(tank, {})
        volume = st.get("volume", 0.0)
        full = self.c.full_volume(tank) or 0.0
        samples = self.samples.setdefault(tank, [])
        samples.append((now, rate))
        # Every test that gets this far has COMPLETED, which is the set RJT
        # counts over; the rate table below is the narrower set of tests
        # that were also RECORDED.
        done = self.completed.setdefault(tank, [])
        done.append(rate)
        del done[:-RJT_WINDOW]
        # Figure 11-2's second condition for recording a test: "Record test
        # results in database if: ... 2) leak rate < +0.4 gph". A gain over
        # the gate is thrown away here rather than recorded and excluded
        # later, and the evidence that this is what a real console does is
        # that no rate table on the shelf has a row over it -- 100 sample
        # rows across five manuals, the largest +0.395 -- while the same
        # figures print RJT counts of 5, 9 and 12. If the over-gate tests
        # were recorded and filtered afterwards they would show up in the
        # column. So RJT reads `completed` and everything else reads the
        # table. See UNKNOWNS A63.
        #
        # Positive is a GAIN in the manual's sign and a NEGATIVE rate in
        # this engine's -- see K2, and `POSITIVE_LEAK`.
        if rate < -POSITIVE_LEAK:
            self._age_out(tank, now)
            return
        # Every column IA51 prints, because chapter 11's method is a walk
        # down them and Problem 1's analysis turns on the ones this used to
        # leave out. `ullage` stays as the ullage VOLUME the other reports
        # mean by the word; `area` is IA51's own "12. Ullage area".
        self.detail.setdefault(tank, []).append(
            {"at": now, "rate": rate, "volume": volume,
             "ullage": max(full - volume, 0.0),
             "area": self.c.uncovered_area(tank),
             "temp": self.c.product_temperature(tank),
             "toptemp": self.c.probe_top_temperature(tank),
             "bdtemp": self.c.probe_board_temperature(tank),
             "tmrt": self._temperature_rate(tank),
             "dispns": self.dispense_factor(tank),
             "interval": minutes,
             "delivered": self._since_delivery(tank, now),
             "throughput": self.dispensed_today(tank) / 1000.0,
             "evap": self._evaporation(tank),
             "state": self._state(tank, minutes)})
        self._age_out(tank, now)

    # "This table contains the last 28 days of leak tests, or a maximum of 80
    # of the most recent tests", 576013-818 p.11-4. This kept 100 with no
    # age-out at all, so a console left running for a month held tests from
    # outside the window the report's own title claims. See FIDELITY K6.
    TABLE_DAYS = 28.0
    TABLE_TESTS = 80

    def _age_out(self, tank, now):
        """Both halves of the window, whichever bites first."""
        oldest = now - self.TABLE_DAYS * 86400.0
        rows = [r for r in self.detail.get(tank, []) if r["at"] >= oldest]
        self.detail[tank] = rows[-self.TABLE_TESTS:]
        kept = {r["at"] for r in self.detail[tank]}
        self.samples[tank] = [s for s in self.samples.get(tank, [])
                              if s[0] in kept]

    def feedback(self, tank):
        """IA52's `FDBK`, "Feedback control variable, range 0 to 45 minutes".

        The manual gives the RANGE and not the formula, and the formula is
        in its own samples: FDBK is a ramp on how full the rate table is,
        flat at zero until the table is half full and reaching the ceiling
        exactly as it fills.

            FDBK = 45 x (C1 - 40) / 40, clamped to 0 and 45

        where C1 is "Total number of tests in the rate table" and 80 is the
        table. That fits every IA52 row on the shelf -- 100 of them, across
        576013-818 Rev AA and Rev AB, 577013-918 Rev D and both TLS-450
        serial manuals -- with no exceptions and no slack: C1=44 gives
        exactly 4.5 and C1=56 exactly 18.0, and C1=80 lands on 45.0 rather
        than being clipped there. This replaces a mean of the intervals,
        which was the simulator's own. See UNKNOWNS A51.

        It is a control variable and not only a printed one: the minimum a
        test has to run is "15 minutes + Feedback time".
        """
        rows = self.detail.get(tank) or []
        if not rows:
            return 0.0
        half = self.TABLE_TESTS / 2.0
        ramp = CONTROL_MAX_MINUTES * (len(rows) - half) / half
        return _tenth(min(CONTROL_MAX_MINUTES, max(0.0, ramp)))

    def accept(self, tank):
        """IA52's `ACPT`, the same for the accepted tests, and the term the
        PRE-START phase adds to its five minutes."""
        rows = [r for r in (self.detail.get(tank) or [])
                if r["state"] == ACCEPTABLE]
        if not rows:
            return 0.0
        return min(CONTROL_MAX_MINUTES,
                   sum(r["interval"] for r in rows) / len(rows))

    def dispense_factor(self, tank):
        """IA51's `DSPNS`, which is not the gallons and says so.

        p.11-30: "The dispense factor is an indication of the amount of
        dispensing that occurred during the last 24 hours. It is not as
        simple as the amount of gallons dispensed during the last 24 hours
        because the hourly volumes are weighted in such a way that the most
        recent dispensing value contributes more to the dispense factor than
        dispensing volume that has occurred 23 hours ago. But it can be
        used as a relative indication of tank activity."

        So the shape is stated and the weights are not. These rise linearly
        with recency and are normalised so that a day of even dispensing
        gives the plain total back -- which keeps the column in the range its
        own samples print, 162 to 600 gallons-ish, while a tank that has just
        gone quiet after a busy evening reads higher than the same gallons
        spread over the day. The WEIGHTS are the simulator's own.
        """
        rows = self.hourly.get(tank) or []
        pairs = list(zip(rows, rows[1:]))
        if not pairs:
            return 0.0
        total = weights = 0.0
        for age, ((_a, before), (_b, after)) in enumerate(pairs):
            # the weight is the hour's own place in the day, so an hour that
            # sold nothing still carries its weight into the normalisation
            weight = age + 1.0
            weights += weight
            if after < before:
                total += weight * (before - after)
        return total * len(pairs) / weights

    def _temperature_rate(self, tank):
        """IA51's TMRT, degrees an hour, off the 30-second probe table.

        Its own sample runs 0.02 and 0.06, which is a tank settling rather
        than one that has just been filled.
        """
        rows = self.probe.get(tank) or []
        if len(rows) < 2:
            return 0.0
        hours = (rows[-1]["at"] - rows[0]["at"]) / 3600.0
        if hours <= 0:
            return 0.0
        return abs(rows[-1]["temp"] - rows[0]["temp"]) / hours

    def delivery_hours(self, tank, now):
        """Hours since the last delivery, or None if there has not been one.

        Separate from `_since_delivery` because the two callers want
        opposite things from a tank that has never had one: the DEL column
        wants a number to print and the rejection test wants to know there
        is nothing to be too close to.
        """
        record = self.c.deliveries.last(tank)
        if record is None:
            return None
        when = (record.end or {}).get("at") or record.start.get("at")
        return None if when is None else max(0.0, (now - when) / 3600.0)

    def _since_delivery(self, tank, now):
        """IA51's DEL, hours since the last one. A tank that has never had a
        delivery on this console reports the time it has been watched, which
        is the only honest answer to "how long since".
        """
        since = self.delivery_hours(tank, now)
        if since is not None:
            return since
        return max(0.0, (now - self.watching.get(tank, now)) / 3600.0)

    def _evaporation(self, tank):
        """IA51's EVAP, and it is a rule rather than a number.

        576013-818 chapter 11, on both A51 and A52: "EVAP -- If the Reid
        Vapor Pressure table has been entered, the evaporation rate will be
        here." The table CAN be entered now -- 54C holds its twelve monthly
        pressures -- and the field is still 0.000, because what no page on
        this shelf gives is the step from a Reid Vapor Pressure to a rate in
        gallons an hour. 576013-635 defines the chart and its range;
        576013-818 says the rate appears; neither says how one becomes the
        other, and chapter 11's own four sample tanks all print 0.000.
        Inventing the arithmetic would put a number on the wire that no
        manual could be checked against. See FIDELITY Y8.
        """
        return 0.0

    def _state(self, tank, minutes):
        """IA51's "ss - Test acceptability", all six of the manual's codes.

        The console had three. The order is the manual's own listing read
        from the most specific reason out: a test inside a delivery window
        is rejected for that before anything is said about its length.
        """
        now = time.mktime(self.c.now())
        # "All tests rejected with error code 2 started within 2 hours of a
        # delivery", p.11-30 reading its own rate table. The window was one
        # hour, and it was measured with `deliveries.during`, which adds the
        # STATIC test's own eight-hour quiet period to whatever window it is
        # handed -- so CSLD's two hours would have been ten. The branch above
        # it returned code 3, "excessive dispensing prior to test", for a
        # delivery in progress: the wrong code for the wrong thing, and
        # unreachable besides, because a delivery aborts the test rather
        # than being sampled through. See FIDELITY K6.
        since = self.delivery_hours(tank, now)
        if since is not None and since <= DELIVERY_HOURS:
            return REJECT_DELIVERY
        if minutes < TEST_MINUTES + self.feedback(tank):
            return REJECT_DURATION
        if self._temperature_rate(tank) > TEMPERATURE_RATE_LIMIT:
            return REJECT_TEMPERATURE
        if self._outside_deviation(tank):
            return REJECT_DEVIATION
        return ACCEPTABLE

    def _outside_deviation(self, tank):
        """"06=Rejected - outside weighted STD": a rate that disagrees with
        the ones around it by more than the spread of those.

        The manual names the test and gives no multiple, so the two standard
        deviations here are the simulator's own. See FIDELITY K1a.
        """
        rates = [r for _w, r in (self.samples.get(tank) or [])][-20:]
        if len(rates) < 5:
            return False
        mean = sum(rates) / len(rates)
        var = sum((r - mean) ** 2 for r in rates) / len(rates)
        spread = var ** 0.5
        return spread > 0 and abs(rates[-1] - mean) > 2.0 * spread

    def table(self, tank):
        """[{at, rate, volume, ullage, temp, state}] behind IA51 to IA54."""
        return self.detail.get(tank) or []

    def moving_average(self, tank, window=5):
        """[(when, average)]: what IA54 prints."""
        rows, out = self.table(tank), []
        for i in range(len(rows)):
            recent = [r["rate"] for r in rows[max(0, i - window + 1):i + 1]]
            out.append((rows[i]["at"], sum(recent) / len(recent)))
        return out

    def _analyse(self, tank, now):
        """"The database is statistically analyzed to produce a final test
        result": every twenty-four hours, unless it is report only."""
        last = self.reported.get(tank)
        if last is not None and (now - last) / 3600.0 < REPORT_HOURS:
            return
        if last is None:
            self.reported[tank] = now
            return
        self.reported[tank] = now
        # The ACCEPTED tests in the rate table, which is the set IA52
        # averages and prints. This took the last twenty samples whatever
        # their acceptability, so the result the console announced came off
        # a different average from the one its own report showed -- and the
        # twenty-test window is `RJT`'s, not this one's. "There must be at
        # least 2 acceptable tests" is the count. See FIDELITY K6.
        recent = [r["rate"] for r in (self.detail.get(tank) or [])
                  if r["state"] == ACCEPTABLE]
        if len(recent) < MIN_ACCEPTABLE:
            self.results[tank] = (NONE, now)
            return
        average = sum(recent) / len(recent)
        limit = self.threshold(tank)
        result = FAIL if average >= limit else PASS
        if result == PASS and average <= -limit:
            # "CSLD Rate Increase Warning ... indicates fluid is ENTERING
            # the tank during the leak test ... a higher than acceptable
            # positive increase in product", 576013-818 p.11-9. A gain, not
            # a loss that has grown: this used to fire when a small drift
            # appeared in a LOSS, and a gain could not be represented at all
            # because `measured_rate` clamped at zero. Chapter 11's Problem
            # 3 -- a siphon leaking product back into a tank -- is what it
            # is for. See FIDELITY K2.
            result = INCR
        self.results[tank] = (result, now)
        if result == PASS:
            # what the tank held while the tests behind this verdict ran,
            # which is IA52's own "VOL: Average volume of all acceptable
            # tests" and the figure `fullest_pass` ranks on
            volumes = [r["volume"] for r in (self.detail.get(tank) or [])
                       if r["state"] == ACCEPTABLE]
            self._passes.setdefault(tank, []).append(
                (now, sum(volumes) / len(volumes) if volumes else 0.0))
            del self._passes[tank][:-64]         # keep the list bounded
        self._post_result(tank, result)

    def evaluation(self, tank):
        """IA52's own fourteen fields, which are not IA51's.

        The console fell through to IA51's branch and printed its columns,
        so not one of A52's own was produced -- including the `C1`/`C3`
        counts and the compensated average that chapter 11's Problems 8 and
        9 are read from. See FIDELITY K1.

        576013-818 chapter 11's glossary is what this is built from, because
        it defines every column where the serial manual only names it. Two
        of them are the opposite way round from the obvious reading: LRATE
        is the COMPENSATED rate and AVLRTE the UNCOMPENSATED one, despite
        AVLRTE looking like an average.
        """
        rows = self.detail.get(tank) or []
        result, when = self.results.get(tank, (None, None))
        accepted = [r for r in rows if r["state"] == ACCEPTABLE]
        rates = [r["rate"] for r in accepted]
        # "INTVL: Total test duration, sum of all acceptable tests, in hours"
        hours = sum(r["interval"] for r in accepted) / 60.0
        span_days = ((rows[-1]["at"] - rows[0]["at"]) / 86400.0
                     if len(rows) > 1 else 0.0)
        compensated = sum(rates) / len(rates) if rates else 0.0
        uncompensated = rows[-1]["rate"] if rows else 0.0
        # "VOL: Average volume of all acceptable tests"
        volumes = [r["volume"] for r in accepted]
        return {
            "at": when or (rows[-1]["at"] if rows else
                           time.mktime(self.c.now())),
            "status": self._a52_status(tank, rows, accepted, hours,
                                       span_days, result),
            "records": len(rows),
            "accepted": len(accepted),
            "compensated": compensated,
            "hours": hours,
            "uncompensated": uncompensated,
            "volume": (sum(volumes) / len(volumes)) if volumes else 0.0,
            # The two control variables, which drive the timing as well
            # as printing here: see `feedback` and `accept`.
            "feedback": self.feedback(tank),
            "acceptance": self.accept(tank),
            # "THPUT: Estimated monthly throughput in thousands of gallons"
            "throughput": self.dispensed_today(tank) * 30.0 / 1000.0,
            "multiplier": readings.fixed(0.8, 1.2, "csldmult", tank),
            # "RJT: Of the last 20 tests completed, this is the number of
            # tests rejected due to excessive positive leak rate (>0.4 gph)"
            # -- COMPLETED, which is not the rate table: an over-gate test
            # never reaches it. See `_sample`.
            "rejects": sum(1 for r in self.completed.get(tank, [])
                           if r < -POSITIVE_LEAK),
            # "EVAP: If the Reid Vapor Pressure table has been entered, the
            # evaporation rate will be here" -- and this console has no RVP
            # table, so it is 0.000 exactly as chapter 11's own four sample
            # tanks print it.
            "evap": 0.0,
        }

    def _a52_status(self, tank, rows, accepted, hours, span_days, result):
        """Chapter 11's status ladder, which it states with every number."""
        if not rows or result is None:
            return A52_NO_TEST
        if len(accepted) < MIN_ACCEPTABLE:
            return A52_NO_COUNT
        if hours < MIN_INTERVAL_HOURS:
            return A52_NO_INTERVAL
        if hours < RANGE_HOURS and span_days < RANGE_DAYS:
            return A52_NO_RANGE
        if result == INCR:
            return A52_INCREASE
        if result == FAIL and span_days < NEGATIVE_HOLD_DAYS:
            # "9 WARNING NEGATIVE_HOLD - 2 day waiting period before
            # reporting a failure"
            return A52_HOLD
        return {PASS: A52_PASS, FAIL: A52_FAIL}.get(result, A52_NO_TEST)

    def _post_result(self, tank, result):
        """A finished analysis is a test result, and a result latches.

        "The result of this test is added to a database of leak test results.
        The database is statistically analyzed to produce a final test
        result", and a final test result of FAIL is a failed 0.2 gph test.
        A pass takes the alarm off again, the same way a passing manual test
        does.
        """
        # hrmreports.CSLD_STATE's own codes: 01 PASS, 02 FAIL, 03 NO
        # RESULTS AVAIL, 08 INCR.
        code = {PASS: "01", FAIL: "02", NONE: "03", INCR: "08",
                WARN: "01"}.get(result)
        if code:
            log = self.history.setdefault(tank, [])
            log.append((time.mktime(self.c.now()), code))
            del log[:-64]
        aa, nn = PERIODIC_FAIL_ALARM
        if result == FAIL:
            if self._fail_alarm_enabled(tank):
                self.c.post(aa, nn, tank)
        else:
            self.c.clear_posted(aa, nn, tank)

    def _fail_alarm_enabled(self, tank):
        """S62D, the middle character: "Periodic Test Fail Alarm"."""
        raw = self.c.values.get(f"S62D{tank:02d}")
        if not raw:
            return True                     # not programmed, so not disabled
        body = raw[2:] if len(raw) > 3 else raw
        return body[1:2] != "0"

    # ---- what the console says ---------------------------------------------
    def result_of(self, tank):
        """"Tanks programmed as manifolded would have a common result", so a
        secondary answers with its primary's."""
        return (self.results.get(self.primary(tank)) or (NONE, None))[0]

    def result_code(self, tank):
        """I251's rr: 01 pass, 02 fail, 03 no results, 08 incr, 09 warn."""
        return RESULT_CODE.get(self.result_of(tank), "03")

    def status_line(self, tank):
        """One line for the CSLD TEST RESULTS screen.

        576013-610 p.10-2 draws it `PER: (Results)` and 576013-635 p.62
        prints one: `PER: JAN 22, 1996 PASS`. With the year, which every
        printed example on this shelf carries -- `PER: JUL 26, 1996 FAIL`,
        `PER: MAY 18, 2000 FAIL` -- and which this dropped. The full form is
        22 characters and the panel line is 24, so it fits on both.

        And a console with nothing yet to say says so in the TLS-350's own
        words. `COLLECTING n TEST(S)` is not a string on this console:
        grepped across all twenty-five documents, "COLLECTING" as a status
        appears only in 577013-950, the TLS-450 serial manual. The
        TLS-350's word for the state is `NO RESULTS AVAILABLE`, which has a
        section heading of its own at p.11-11 and a result code of its own.
        See FIDELITY K7.
        """
        if not self.enabled(tank):
            # not a CSLD tank at all, so there is no periodic test of this
            # kind to put a prefix in front of
            return "NO RESULTS AVAILABLE"
        result, when = self.results.get(self.primary(tank)) or (NONE, None)
        if when is None:
            # `PER: (Results)`, and NO RESULTS AVAILABLE is a Results value:
            # it has a result code of its own. The same shape the scheduled
            # tests take, `GRS: NO TEST DATA AVAILABLE`. FIDELITY O5 and K8.
            return f"PER: {NONE_LINE}"
        return f"PER: {clock_date(when)} {result}"

    # "the one passed test out of all passed tests in the LAST MONTH in which
    # the tank was most full", 576013-610 p.10-2. The page does not say
    # whether the month is the calendar one or the last month's worth, and
    # the console's other monthly figure -- I207's "Fullest Periodic Monthly
    # Test Passed" -- groups by calendar month. This takes the trailing
    # window, because the screen is read today and answers "recently".
    FULLEST_DAYS = 31.0

    def passes(self, tank):
        """[(when, volume)] for every analysis that PASSED, newest last.

        Kept alongside the verdict because "the tank was most full" is a
        fact about the moment the test passed and there is nowhere else to
        read it from afterwards.
        """
        return self._passes.get(tank) or []

    def fullest_pass(self, tank):
        """The passed analysis with the most fuel in the tank behind it."""
        now = time.mktime(self.c.now())
        recent = [(when, volume) for when, volume in self.passes(
                      self.primary(tank))
                  if (now - when) / 86400.0 <= self.FULLEST_DAYS]
        return max(recent, key=lambda row: row[1]) if recent else None

    def last_pass(self, tank):
        """CSLD FULLEST LAST PASS, which is not the last pass.

        "This display refers to the one passed test out of all passed tests
        in the last month in which the tank was most full." This
        returned the most recent result if it happened to be a pass,
        ignoring both the volume and the month -- so the screen that exists
        to find the fullest test showed whatever had just happened. And it
        showed a bare date where p.10-2 draws `PER: (Results)`, the same
        prefix the current-result screen beside it takes. See FIDELITY K7
        and K8.
        """
        if not self.enabled(tank):
            return "NO RESULTS AVAILABLE"
        fullest = self.fullest_pass(tank)
        if fullest is None:
            return f"PER: {NONE_LINE}"
        return f"PER: {clock_date(fullest[0])} {PASS}"

    def conditions(self):
        """The two warnings CSLD raises by itself."""
        now = time.mktime(self.c.now())
        out = []
        for tank in sorted(self.c.tank_level):
            if not self.enabled(tank):
                continue
            if tank != self.primary(tank):
                # one set, one result, one alarm
                continue
            if self.reporting_inhibited(tank):
                # "this feature inhibits the 'No CSLD Idle Time' and 'CSLD
                # Incr Rate' alarms", which is both of the two this method
                # raises, so the tank has nothing to say.
                continue
            # An IDLE PERIOD, not a stored test, and 576013-818 p.11-10
            # says so in a parenthesis written to settle exactly this: "The
            # system has not detected an idle period in the last 24 hours.
            # All tanks must have at the very least some short idle periods
            # each day. CSLD needs to find an idle time to clear this alarm.
            # This alarm will automatically clear when the system detects
            # that at least one idle period has occurred (this does not
            # require that a CSLD record get stored in the rate table)."
            #
            # This asked when a SAMPLE was last banked, which is a much
            # higher bar: a test needs the eight minutes and then the sample
            # delay, the pre-start, its accept time and fifteen minutes of
            # test on top, so a site with a car every seven minutes finds
            # idle periods all day and banks nothing -- and was told it had
            # no idle time. The parenthesis is the manual forbidding that
            # reading.
            newest = self.idle_seen.get(tank) or self.watching.get(tank)
            if newest is not None and (now - newest) / 3600.0 > REPORT_HOURS:
                # "CSLD needs to find an idle time to clear this alarm"
                out.append(NO_IDLE_ALARM[0] + NO_IDLE_ALARM[1] + f"{tank:02d}")
            if self.result_of(tank) == INCR:
                out.append(RATE_INCREASE_ALARM[0] + RATE_INCREASE_ALARM[1]
                           + f"{tank:02d}")
        return out

    def report(self, tanks):
        """CSLD TEST RESULTS, as the console prints it."""
        # No title block here. `printer.csld` already puts the station
        # header, the stamp and the title above this, so spelling them again
        # printed the title, the rule and the date TWICE on one report. This
        # method is the body; the paper's head belongs to whoever prints it.
        out = []
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            out.append(f"T {tank}: {label}")
            if not self.enabled(tank):
                out.append("  CSLD NOT ENABLED")
                out.append("")
                continue
            out.append(f"PROBE SERIAL NUM {self.c.probe_serial(tank)}")
            out.append("0.2 GAL/HR TEST")
            out.append("  " + self.status_line(tank))
            samples = self.samples.get(tank) or []
            if samples:
                rates = [rate for _w, rate in samples[-20:]]
                out.append(f"  {len(samples)} TESTS, AVERAGE "
                           f"{sum(rates) / len(rates):.3f} GAL/HR")
            out.append("")
        return chr(10).join(out)

    def delete_table(self, tank):
        """S054, Delete CSLD Rate Table."""
        n = len(self.samples.get(tank) or [])
        self.samples.pop(tank, None)
        self.detail.pop(tank, None)
        self.results.pop(tank, None)
        self.reported.pop(tank, None)
        return n
