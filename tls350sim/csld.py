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
import time

# A tank is idle when nothing is moving it faster than this.
#
# This number used to be 1.0, and it was wrong in the way that matters most:
# it made CSLD blind to exactly the leaks it exists to find. Put 60 gph on a
# tank and the level moves 60 gallons an hour, the watcher called that
# dispensing, the tank never went "idle", no sample was ever taken and the
# console reported NO CSLD IDLE TIME instead of a failed test.
#
# A real console decides idle from ACTIVITY, "no dispensing or deliveries in
# progress", and dispensing is a different order of magnitude from any loss:
# one hose at 10 gallons a minute is 600 gph, and the slowest dispenser a site
# runs is still several hundred. A leak the console tests for is 3.0 gph at
# the very top. So the threshold belongs between the two, not underneath both,
# and the meters and the delivery watcher are asked first because they are
# what the console actually knows.
IDLE_GPH = 100.0

# How long the tank has to stay quiet before CSLD takes its sample, and how
# often the database is analysed into a result.
IDLE_HOURS = 1.0
REPORT_HOURS = 24.0

# What a periodic (0.2 gph) test is looking for, and how many samples the
# database wants before it will call it.
RATE = 0.2
SAMPLES_WANTED = 3

PASS, FAIL, NONE, INCR, WARN = ("PASS", "FAIL", "NO RESULTS", "INCR", "WARN")
RESULT_CODE = {PASS: "01", FAIL: "02", NONE: "03", INCR: "08", WARN: "09"}

# "No CSLD Idle Time Warning" and "CSLD Rate Increase Warning", from i10100
NO_IDLE_ALARM = ("02", "21")
RATE_INCREASE_ALARM = ("02", "23")

# CSLD tests at 0.2 gph, so a CSLD failure IS the tank's periodic leak test
# failure and it posts the alarm the manual gives that: "02 ... 14=Tank
# Periodic Leak Test Fail Alarm". Not shutting the tank down is the whole
# point of CSLD; not SAYING anything would make it useless.
PERIODIC_FAIL_ALARM = ("02", "14")


class CSLD:
    """One console's worth of continuous testing."""

    def __init__(self, console):
        self.c = console
        self.samples = {}      # tank -> [(when, rate)] newest last
        self.detail = {}       # tank -> [the whole sample], for diagnostics
        self.results = {}      # tank -> (result, when)
        self.idle_from = {}    # tank -> when the tank went quiet
        self.reported = {}     # tank -> when the database was last analysed
        self._seen = {}        # tank -> (volume, when)
        self.watching = {}     # tank -> when CSLD started looking at it

    # ---- is this tank on CSLD at all ---------------------------------------
    def enabled(self, tank):
        """The key, the method, and a probe to do it with."""
        if not (self.c.licensed("csld") and self.c.has("probe")):
            return False
        raw = self.c.values.get(f"S611{tank:02d}") or ""
        body = raw[2:] if len(raw) > 8 else raw
        return body[3:4] == "7"          # 7 = CSLD, from S611's method field

    def report_only(self, tank):
        """"except when the CSLD Report Only feature is enabled in setup"."""
        raw = (self.c.values.get(f"S61C{tank:02d}") or "").strip()
        return raw.endswith("1")

    # ---- watching ----------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.tank_level):
            if not self.enabled(tank):
                self.idle_from.pop(tank, None)
                continue
            self._watch(tank, now)

    def _watch(self, tank, now):
        self.watching.setdefault(tank, now)
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, when = self._seen.get(tank, (volume, now))
        hours = (now - when) / 3600.0
        self._seen[tank] = (volume, now)
        moving = hours > 0 and abs(volume - seen) / hours > IDLE_GPH
        if moving or self.busy(tank) or self.c.deliveries.in_progress(tank):
            self.idle_from[tank] = None
            return
        start = self.idle_from.get(tank)
        if start is None:
            self.idle_from[tank] = now
            return
        if (now - start) / 3600.0 >= IDLE_HOURS:
            self._sample(tank, now)
            self.idle_from[tank] = now      # the next one needs its own hour
        self._analyse(tank, now)

    def busy(self, tank):
        """Is anything the console can see selling out of this tank?

        The meter map is the console's own answer to "is the forecourt
        running", and it is a better one than watching the level, because the
        level moves for a leak as well.
        """
        for meter, where in (self.c.meters or {}).items():
            if int(where) == int(tank) and self.c.meter_flow.get(meter):
                return True
        return False

    def _sample(self, tank, now):
        """One leak test, taken while nobody was looking.

        The rate is what the report needs; the volume, ullage and temperature
        alongside it are what the CSLD diagnostics tables print, so the sample
        keeps the lot rather than making them up again later.
        """
        rate = self.c.leaks.measured_rate("tank", tank)
        st = self.c.tank_level.get(tank, {})
        volume = st.get("volume", 0.0)
        full = self.c.full_volume(tank) or 0.0
        samples = self.samples.setdefault(tank, [])
        samples.append((now, rate))
        self.detail.setdefault(tank, []).append(
            {"at": now, "rate": rate, "volume": volume,
             "ullage": max(full - volume, 0.0), "temp": 55.0,
             "state": self._state(tank)})
        del samples[:-100]
        del self.detail[tank][:-100]

    def _state(self, tank):
        """"ss - Test acceptability: 00=Acceptable, 02=Rejected - within
        delivery threshold, 03=Rejected - excessive dispensing"."""
        now = time.mktime(self.c.now())
        if self.c.deliveries.during(tank, now - 3600.0, now):
            return "02"
        if self.c.deliveries.in_progress(tank):
            return "03"
        return "00"

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
        samples = self.samples.get(tank) or []
        if last is None:
            self.reported[tank] = now
            return
        self.reported[tank] = now
        if len(samples) < SAMPLES_WANTED:
            self.results[tank] = (NONE, now)
            return
        recent = [rate for _when, rate in samples[-20:]]
        average = sum(recent) / len(recent)
        was = self.results.get(tank)
        result = FAIL if average >= RATE else PASS
        if result == PASS and was and was[0] == PASS:
            earlier = [rate for _w, rate in samples[:-20]] or recent
            if average > (sum(earlier) / len(earlier)) + 0.05:
                # "CSLD Rate Increase Warning"
                result = INCR
        self.results[tank] = (result, now)
        self._post_result(tank, result)

    def _post_result(self, tank, result):
        """A finished analysis is a test result, and a result latches.

        "The result of this test is added to a database of leak test results.
        The database is statistically analyzed to produce a final test
        result", and a final test result of FAIL is a failed 0.2 gph test.
        A pass takes the alarm off again, the same way a passing manual test
        does.
        """
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
        return (self.results.get(tank) or (NONE, None))[0]

    def result_code(self, tank):
        """I251's rr: 01 pass, 02 fail, 03 no results, 08 incr, 09 warn."""
        return RESULT_CODE.get(self.result_of(tank), "03")

    def status_line(self, tank):
        """One line for the CSLD TEST RESULTS screen."""
        if not self.enabled(tank):
            return "NO RESULTS AVAILABLE"
        result, when = self.results.get(tank) or (NONE, None)
        if when is None:
            samples = len(self.samples.get(tank) or [])
            return f"COLLECTING {samples} TEST(S)"
        return f"PER: {time.strftime('%b %d', time.localtime(when)).upper()}" \
               f" {result}"

    def last_pass(self, tank):
        result, when = self.results.get(tank) or (NONE, None)
        if when is None or result not in (PASS, INCR):
            return "NO RESULTS AVAILABLE"
        return time.strftime("%b %d, %Y", time.localtime(when)).upper()

    def conditions(self):
        """The two warnings CSLD raises by itself."""
        now = time.mktime(self.c.now())
        out = []
        for tank in sorted(self.c.tank_level):
            if not self.enabled(tank):
                continue
            samples = self.samples.get(tank) or []
            newest = (samples[-1][0] if samples else
                      self.reported.get(tank) or self.watching.get(tank))
            if newest is not None and (now - newest) / 3600.0 > REPORT_HOURS:
                # "CSLD needs to find an idle time to clear this alarm"
                out.append(NO_IDLE_ALARM[0] + NO_IDLE_ALARM[1] + f"{tank:02d}")
            if self.result_of(tank) == INCR:
                out.append(RATE_INCREASE_ALARM[0] + RATE_INCREASE_ALARM[1]
                           + f"{tank:02d}")
        return out

    def report(self, tanks):
        """CSLD TEST RESULTS, as the console prints it."""
        out = ["CSLD TEST RESULTS", "-" * 22, self.c.clock_text(), ""]
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
