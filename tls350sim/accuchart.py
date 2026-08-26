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
"""AccuChart: the console teaching itself the shape of the tank.

"AccuChart is a patented, automatic tank calibration process which reduces
inventory reconciliation errors by improving tank chart accuracy. By comparing
metered dispensed volumes to tank probe heights, AccuChart minimizes
heights-to-volume conversion errors by adjusting the tank parameters;
capacity, diameter, tilt, and end shape, and the probe offset parameter."

"For 56 days after initial startup, or after a system restart, the system
conducts automatic tank calibration over typical operating levels in the tank
as fuel is dispensed."

THE THREE PROFILES

Figure 6-10's inset is explicit about what is being adjusted, and it is not
one set of numbers but three:

    When AccuChart is initially enabled (or reset), the User Entered Tank
    Parameters are copied into the Active Tank Profile AND become the basis
    for the initial AccuChart Tank Profile.

So: the USER profile is what somebody typed at IN-TANK SETUP and never
changes; the ACCUCHART profile is what the calibration has worked out; the
ACTIVE profile is what the console actually gauges with, and WHEN the
AccuChart profile is copied into it is the whole of the Calibration Update
setting:

    IMMEDIATE   every revision, as it happens
    PERIODIC    on day 28 and again on day 56
    COMPLETE    once, on day 56
    NEVER       never; the calibration still runs, it is just not used

THE STATE MACHINE, all four parts of it from Figure 6-10

    ENABLED      automatic. Meter Data Present, a diameter, a capacity, a
                 profile that is not LINEAR, and a Mag probe.
    MODE         CALIBRATE for 56 days, then MONITOR, "which is also 56 days
                 long ... reset every 56 days".
    USR STATUS   DISABLED until a calibration has actually been applied to
                 the Active profile; ENABLED after.
    WARN STATE   "Only when in AccuChart MONITOR mode and AccuChart's Fitness
                 exceeds 10 will this state change to ON."

WHAT IS GENERATED

The manuals give the inputs, the outputs and the schedule, and no part of the
fitting algorithm: "the tank chart fitting algorithm" is gap 8 of the audit.
A simulator still has to converge on something, so the AS-BUILT tank is
derived here: a real tank is never exactly its nameplate, so each tank gets
its own small, stable offsets from what was programmed, and the calibration
walks the AccuChart profile towards them as data accumulates. Fitness is the
distance still to go, which gives it the behaviour the manual describes,
"less than 0.5 means that AccuChart has a good estimate of the tank's
geometry", without pretending to be Veeder-Root's estimator.
"""
import time

from . import readings

# "AccuChart needs 56 days to complete", and the monitor period after it "is
# also 56 days long".
CALIBRATION_DAYS = 56.0
MONITOR_DAYS = 56.0

# "The first calibration update occurs 28 days from the startup of AccuChart
# and the second calibration update occurs 56 days from the startup."
SCHEDULE = {
    "1": None,                      # IMMEDIATE: every revision
    "2": (28.0, 56.0),              # PERIODIC
    "3": (56.0,),                   # COMPLETE
    "4": (),                        # NEVER
}
IMMEDIATE, PERIODIC, COMPLETE, NEVER = "1", "2", "3", "4"

# "Depending on throughput, the first COE (capacity, offset, end shape)
# calibration occurs after two weeks."
FIRST_CALIBRATION_DAYS = 14.0
CALIBRATION_EVERY_DAYS = 7.0

# "Only when in AccuChart MONITOR mode and AccuChart's Fitness exceeds 10 will
# this state change to ON."
WARN_FITNESS = 10.0

# "Tank AccuChart Calibration Warning", i10100 category 02 number 24
CAL_WARNING = ("02", "24")

CALIBRATE, MONITOR = "CALIBRATE", "MONITOR"

# Cubic inches to US gallons.
GALLON = 231.0
PI = 3.14159265358979


def tank_length(capacity, diameter, shape):
    """How long a tank of that capacity and diameter must be.

    No setup screen asks for tank length, and AccuChart reports one, so it is
    derived: a cylinder with two end caps whose volume is `shape` times a
    hemisphere's, which is what the end factor is defined as, "the ratio of
    the dome's volume to the volume of a theoretical hemispherical end".

        capacity = (pi/4) D^2 L + F (pi/6) D^3

    Checked against Veeder-Root's own printed records, in both unit systems
    they print in. The Serial Interface Manual's B91 sample reads DIAMETER
    91.0, LENGTH 144.4, SHAPE F 1.00, CAPACITY 5774, and this returns 144.4
    for 5774 gallons at 91 inches. The Troubleshooting Guide's IB9400 startup
    record is metric, DIAM 2400, LENGTH 8007, CAPACITY 43459 litres, and the
    same arithmetic gives 8007.
    """
    if diameter <= 0:
        return 0.0
    caps = shape * PI * diameter ** 3 / 6.0
    barrel = PI * diameter ** 2 / 4.0
    return max(0.0, (capacity * GALLON - caps) / barrel)


class Profile:
    """One set of tank parameters, as B91 and B94 report them."""

    FIELDS = ("diameter", "length", "offset", "tilt", "shape", "capacity")

    def __init__(self, diameter, length, offset, tilt, shape, capacity,
                 fitness=0.0):
        self.diameter = diameter
        self.length = length
        self.offset = offset
        self.tilt = tilt
        self.shape = shape
        self.capacity = capacity
        self.fitness = fitness

    def copy(self):
        return Profile(self.diameter, self.length, self.offset, self.tilt,
                       self.shape, self.capacity, self.fitness)

    def values(self):
        return [getattr(self, name) for name in self.FIELDS]

    def as_dict(self):
        out = {name: getattr(self, name) for name in self.FIELDS}
        out["fitness"] = self.fitness
        return out


class Tank:
    """One tank's calibration: its three profiles and where it has got to."""

    def __init__(self, number, user):
        self.number = number
        self.user = user
        self.active = user.copy()
        self.chart = user.copy()
        self.started = None
        self.mode = CALIBRATE
        self.mode_since = None
        self.updates = 0
        self.applied = []            # the schedule days already honoured
        self.history = []            # [(when, Profile)] oldest first
        self.observations = []       # the rows I@B900 prints
        self.data = 0.0              # "a measure of the quantity of recorded data"
        self.user_status = False     # ACCU USR STATUS
        self.last_calibration = None
        self.warn = False
        self.failed = False          # "failure to calibrate in 56 days"
        # "Check MINht and MAXht: These values will indicate the range over
        # which the tank was calibrated."
        self.low_height = 0.0
        self.high_height = 0.0


class AccuChart:
    """Every tank this console is calibrating."""

    def __init__(self, console):
        self.c = console
        self.tanks = {}
        self._seen = {}              # tank -> (volume, when) for the observer

    # ---- is this tank on AccuChart at all -----------------------------------
    def scheduling(self, tank):
        """S616, the Calibration Update setting. IMMEDIATE is the default."""
        raw = (self.c.values.get(f"S616{tank:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return body.strip()[-1:] or IMMEDIATE

    def meter_data(self, tank):
        """S615. "If dispenser data for this tank is being reported to the
        DIM ... this parameter MUST be set to YES"."""
        raw = (self.c.values.get(f"S615{tank:02d}") or "").strip()
        return raw.endswith("1")

    def disabled_because(self, tank):
        """Why AccuChart is not enabled on this tank, in the manual's words.

        "The reasons why AccuChart would not be enabled are: Meter Data
        Present = NO; Siphon manifolded with 1XX software; Diameter or
        Capacity not entered; User multi-point chart bad; ... Not a Mag
        probe; Tank profile set to LINEAR."
        """
        c = self.c
        if not c.licensed("bir"):
            return "NO BIR KEY"
        if not self.meter_data(tank):
            return "METER DATA PRESENT: NO"
        if not c.limit("607", tank):
            return "NO TANK DIAMETER"
        if not c.full_volume(tank):
            return "NO FULL VOLUME"
        if c.tank_profile(tank) == "03":
            # "Accuchart is not capable of calibrating linear tanks so it does
            # not enable when the tank profile is set to linear"
            return "TANK PROFILE LINEAR"
        if c.probe_type(tank) != "MAG PROBE":
            return "NOT A MAG PROBE"
        if c.family() == "1XX" and len(c.manifolded(tank)) > 1:
            return "SIPHON MANIFOLD ON 1XX"
        return ""

    def enabled(self, tank):
        return not self.disabled_because(tank)

    # ---- the profiles --------------------------------------------------------
    def _user_profile(self, tank):
        c = self.c
        diameter = c.limit("607", tank) or 96.0
        capacity = c.full_volume(tank) or 0.0
        # "Shape F value of 0 = 1 point tank profile was entered, 1 = 4 point
        # tank profile was entered, and 0.5 = 20 point tank profile"
        shape = {"00": 0.0, "01": 1.0, "02": 0.5}.get(c.tank_profile(tank), 0.0)
        raw = (c.values.get(f"S639{tank:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        if body[:1] in ("1", "2"):
            # "Selecting FLAT = an End Factor of 0. Selecting HEMISPHER = an
            # End Factor of 1."
            shape = 0.0 if body[:1] == "1" else 1.0
        elif body[:1] == "3":
            factor = c.limit("639", tank)
            if factor is not None:
                shape = max(0.0, min(1.0, factor))
        length = tank_length(capacity, diameter, shape)
        return Profile(diameter, length, c.limit("60C", tank) or 0.0,
                       c.limit("608", tank) or 0.0, shape, capacity)

    def as_built(self, tank):
        """What the tank ACTUALLY is, which is not what the nameplate says.

        Generated, and deliberately small: a 10,000 gallon tank that gauges
        out at 10,140 with the probe sitting a fifth of an inch proud is an
        ordinary tank, and it is the sort of error AccuChart exists to take
        out of the reconciliation.
        """
        user = self._user_profile(tank)
        return Profile(
            user.diameter * readings.fixed(0.985, 1.015, "acdia", tank),
            user.length * readings.fixed(0.98, 1.02, "aclen", tank),
            user.offset + readings.fixed(-0.35, 0.35, "acoff", tank),
            user.tilt + readings.fixed(-0.6, 0.6, "actilt", tank),
            min(1.0, max(0.0, user.shape
                         + readings.fixed(-0.08, 0.08, "acshape", tank))),
            user.capacity * readings.fixed(0.985, 1.02, "accap", tank))

    def state(self, tank):
        """The Tank record, made if this is the first look at it."""
        entry = self.tanks.get(tank)
        if entry is None:
            entry = Tank(tank, self._user_profile(tank))
            self.tanks[tank] = entry
        return entry

    def profile(self, tank):
        """What the ACCU screens print: AccuChart's own idea of the tank."""
        return self.state(tank).chart.as_dict()

    # ---- running -------------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.programmed_tanks()):
            if not self.enabled(tank):
                self.tanks.pop(tank, None)
                self._seen.pop(tank, None)
                continue
            entry = self.state(tank)
            if entry.started is None:
                entry.started = entry.mode_since = now
                entry.history.append((now, entry.chart.copy()))
            self._observe(entry, now)
            self._advance(entry, now)

    def _observe(self, entry, now):
        """One row of TANK CALIBRATION DATA, per interval of dispensing.

        "Opening Height / Closing Height / TLS Volume / Dispensed Volume /
        Tank/Meter Ratio": the console only learns anything while product is
        going out through a meter it can see.
        """
        tank = entry.number
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, when = self._seen.get(tank, (volume, now))
        self._seen[tank] = (volume, now)
        hours = (now - when) / 3600.0
        if hours <= 0:
            return
        dropped = seen - volume
        metered = sum(self.c.meter_flow.get(m, 0.0) * hours
                      for m, where in (self.c.meters or {}).items()
                      if int(where) == tank)
        if metered <= 0 or dropped <= 0:
            return
        height = self.c.stick_height(tank)
        if entry.high_height <= 0.0:
            entry.low_height = entry.high_height = height
        entry.low_height = min(entry.low_height, height)
        entry.high_height = max(entry.high_height, height)
        entry.observations.append({
            "open": self.c.stick_height(tank) + dropped / max(
                self.c.full_volume(tank) or 1.0, 1.0),
            "close": self.c.stick_height(tank),
            "tls": dropped, "metered": metered,
            "ratio": dropped / metered if metered else 0.0, "at": now})
        del entry.observations[:-200]
        # "This value is a measure of the quantity of AccuChart recorded data"
        entry.data += metered

    def _advance(self, entry, now):
        days = (now - entry.started) / 86400.0
        if entry.mode == CALIBRATE and days >= CALIBRATION_DAYS:
            entry.mode, entry.mode_since = MONITOR, now
            if entry.updates == 0:
                # "failure to calibrate in 56 days"
                entry.failed = True
        elif entry.mode == MONITOR:
            if (now - entry.mode_since) / 86400.0 >= MONITOR_DAYS:
                # "The ACCUMODE MONITOR period is reset every 56 days."
                entry.mode_since = now
        self._calibrate(entry, now, days)
        entry.warn = (entry.mode == MONITOR
                      and entry.chart.fitness > WARN_FITNESS)

    def _calibrate(self, entry, now, days):
        """Work out a better tank, and decide whether to start using it."""
        if days < FIRST_CALIBRATION_DAYS or entry.data <= 0:
            return
        last = entry.last_calibration
        if last is not None and (now - last) / 86400.0 < CALIBRATION_EVERY_DAYS:
            return
        entry.last_calibration = now
        truth = self.as_built(entry.number)
        # each calibration closes part of the gap; more data closes more
        share = min(0.65, 0.18 + entry.data / 250000.0)
        for name in Profile.FIELDS:
            was = getattr(entry.chart, name)
            setattr(entry.chart, name,
                    was + (getattr(truth, name) - was) * share)
        entry.chart.fitness = self._fitness(entry.chart, truth)
        entry.history.append((now, entry.chart.copy()))
        del entry.history[:-20]
        self._apply(entry, now, days)

    @staticmethod
    def _fitness(chart, truth):
        """How far the chart still is from the tank, as one number.

        "Less than 0.5 means that AccuChart has a good estimate of the tank's
        geometry. A value >3.0 indicates that AccuChart has not made a close
        fit."
        """
        capacity = truth.capacity or 1.0
        diameter = truth.diameter or 1.0
        error = (abs(chart.capacity - truth.capacity) / capacity * 60.0
                 + abs(chart.diameter - truth.diameter) / diameter * 40.0
                 + abs(chart.offset - truth.offset) * 1.5)
        return round(error, 2)

    def _apply(self, entry, now, days):
        """Copy the AccuChart profile into the Active one, when it is due."""
        how = self.scheduling(entry.number)
        due = SCHEDULE.get(how, None)
        if how == NEVER:
            # "AccuChart performs its 56-day tank calibration, but it never
            # revises the Active Tank Profile."
            return
        if due is None:                       # IMMEDIATE
            self._copy_in(entry, now)
            return
        for day in due:
            if days >= day and day not in entry.applied:
                entry.applied.append(day)
                self._copy_in(entry, now)

    def _copy_in(self, entry, now):
        entry.active = entry.chart.copy()
        entry.updates += 1
        entry.user_status = True
        entry.failed = False
        self.c.record_accuchart_update(entry.number, now)

    # ---- what the console says -----------------------------------------------
    def screen(self, tank, what):
        """One ACCUCHART DIAGNOSTICS line, in the console's own words."""
        if not self.enabled(tank):
            # Figure 6-10 draws the same fifteen screens either way; a
            # disabled tank simply shows the parameters that ARE in use,
            # which are the ones somebody typed in.
            if what == "enabled":
                return "ACCU DISABLED"
            if what in ("mode", "status", "updates", "duration", "fitness",
                        "data", "warn"):
                return {"mode": "ACCU MODE CALIBRATE",
                        "status": "ACCU USR STATUS DISABLED",
                        "updates": "ACCU UPDATE COUNT 0",
                        "duration": "ACCU DURATION        0.00",
                        "fitness": "ACCU FITNESS        0.00",
                        "data": "ACCU DATA           0.00",
                        "warn": "ACCU WARN STATE OFF"}[what]
            entry = Tank(tank, self._user_profile(tank))
            chart = entry.chart
            return self._parameter(what, chart)
        entry = self.state(tank)
        now = time.mktime(self.c.now())
        chart = entry.chart
        if what == "enabled":
            return "ACCU ENABLED"
        if what == "mode":
            return f"ACCU MODE {entry.mode}"
        if what == "status":
            return ("ACCU USR STATUS "
                    + ("ENABLED" if entry.user_status else "DISABLED"))
        if what == "updates":
            return f"ACCU UPDATE COUNT {entry.updates}"
        if what == "duration":
            since = entry.mode_since or now
            return f"ACCU DURATION      {(now - since) / 86400.0:6.2f}"
        parameter = self._parameter(what, chart)
        if parameter:
            return parameter
        if what == "fitness":
            return f"ACCU FITNESS       {chart.fitness:5.2f}"
        if what == "data":
            return f"ACCU DATA       {entry.data / 1000.0:8.2f}"
        if what == "warn":
            return "ACCU WARN STATE " + ("ON" if entry.warn else "OFF")
        return ""

    @staticmethod
    def _parameter(what, chart):
        """The six tank parameters AccuChart adjusts, one screen each."""
        return {
            "diameter": f"ACCU DIAMETER {chart.diameter:6.2f}",
            "length": f"ACCU LENGTH   {chart.length:6.2f}",
            "offset": f"ACCU PB OFFSET {chart.offset:5.2f}",
            "tilt": f"ACCU TANK TILT {chart.tilt:5.2f}",
            "shape": f"ACCU SHAPE F   {chart.shape:5.2f}",
            "volume": f"ACCU FULL VOLUME {chart.capacity:6.0f}",
        }.get(what, "")

    def restart(self, tank):
        """S891, and the RESET ACCUCHART screen.

        "Selecting YES clears the AccuChart Tank Profile and the Active Tank
        Profile and recopies the User Entered Tank Parameters into each, and
        then restarts the 56-day Calibration period."
        """
        self.tanks.pop(tank, None)
        self._seen.pop(tank, None)
        entry = self.state(tank)
        now = time.mktime(self.c.now())
        entry.started = entry.mode_since = now
        entry.history.append((now, entry.chart.copy()))
        return "ACCU_CHART RESTART"

    def conditions(self):
        """"Tank AccuChart Calibration Warning", 02/24.

        NOT the fitness warn state, which is the trap here. Figure 6-10 is
        explicit that `ACCU WARN STATE` is a private thing: "This internal
        warning is not posted anywhere but in this display. Only when in
        AccuChart MONITOR mode and AccuChart's Fitness exceeds 10 will this
        state change to ON." So the screen shows it and the alarm list does
        not.

        What IS posted is the other flag, the one the variance reports carry
        per tank: "failure to calibrate in 56 days". No manual states the
        posting condition outright for a TLS-350 (the six causes Veeder-Root
        publishes are AccuChart II's, on the TLS-450PLUS), so that flag is
        the whole of it here.
        """
        out = []
        for tank, entry in sorted(self.tanks.items()):
            if self.enabled(tank) and entry.failed:
                out.append(CAL_WARNING[0] + CAL_WARNING[1] + f"{tank:02d}")
        return out

    def alarm_state(self, tank):
        """B93's "AA - Alarm status: 00=No Alarm, 01=Alarm, 02=Alarm latched".

        A calibration that failed is a stored result rather than a live
        condition, so once the console has carried it into the next period it
        is latched: the same rule a failed leak test follows.
        """
        entry = self.tanks.get(tank)
        if entry is None or not entry.failed:
            return "00"
        return "02" if entry.mode == MONITOR else "01"

    # ---- the reports ---------------------------------------------------------
    def diagnostics_rows(self, tanks):
        """IB91, ACCU_CHART DIAGNOSTICS."""
        rows = ["ACCU_CHART DIAGNOSTICS", "",
                "TK STATUS     DIAMETER LENGTH OFFSET   TILT  SHAPE F"
                "    CAPACITY"]
        for tank in tanks:
            if not self.enabled(tank):
                rows.append(f"{tank:2d} DISABLED")
                continue
            p = self.state(tank).chart
            rows.append(f"{tank:2d} ENABLED      {p.diameter:8.1f}"
                        f"{p.length:7.1f}{p.offset:7.2f}{p.tilt:7.2f}"
                        f"{p.shape:9.2f}{p.capacity:12.0f}")
        return rows

    def status_rows(self, tanks):
        """IB93, ACCU_CHART STATUS."""
        now = time.mktime(self.c.now())
        rows = ["ACCU_CHART STATUS", "",
                "TK STATUS     MODE       USER STATUS DURATION ALARM"
                "        FITNESS    DATA"]
        for tank in tanks:
            if not self.enabled(tank):
                rows.append(f"{tank:2d} DISABLED")
                continue
            e = self.state(tank)
            days = (now - (e.mode_since or now)) / 86400.0
            rows.append(f"{tank:2d} ENABLED    {e.mode:<11s}"
                        f"{'ENABLED' if e.user_status else 'DISABLED':<12s}"
                        f"{days:8.1f}  {'ON' if e.warn else 'OFF':<10s}"
                        f"{e.chart.fitness:9.2f}{e.data:8.0f}")
        return rows

    def history_rows(self, tanks):
        """IB94, ACCU_CHART CALIBRATION HISTORY."""
        rows = ["ACCU_CHART CALIBRATION HISTORY", ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            rows.append(f"T {tank}:{label}")
            if not self.enabled(tank):
                rows.append("ACCUCHART NOT ENABLED")
                rows.append("")
                continue
            rows.append("DATE/TIME       DIAM LENGTH   OFFSET   TILT"
                        " SHAPE F   CAPACITY   FITNESS")
            for when, p in self.state(tank).history:
                rows.append(time.strftime("%y/%m/%d %H:%M",
                                          time.localtime(when))
                            + f"{p.diameter:6.1f}{p.length:7.1f}"
                            f"{p.offset:9.2f}{p.tilt:7.2f}{p.shape:8.2f}"
                            f"{p.capacity:11.0f}{p.fitness:10.2f}")
            rows.append("")
        return rows

    def calibration_status_rows(self, tanks):
        """I@B600, ACCU-CHART DIAGNOSTICS - CALIBRATION STATUS.

        The columns are the fitting routine's own working, and the patent
        this algorithm comes from, US5665895, is what names them: MSSE is the
        merit function, "the minimum sum of the squares of residuals (SSR)
        between the first and the second sets of data"; SUMWT is the total of
        the weight factors it assigns each point, because "the earlier
        acquired points ... are less reliable ... As lore data points are
        acquired, the weight factor increases"; SIGMA is the "standard
        deviation of residuals" it draws the outlier bounds from; and MINht
        and MAXht are the height range the data covers, which is the thing
        the guide tells you to check, "If it is a small range and the
        calibration is complete or almost complete, the tank was not
        adequately exercised."

        The five CALIBRATION columns are the parameters in the order the
        patent fits them, "ranked in ascending order of height range required
        to fit a parameter": length, probe offset, end shape, diameter, tilt.
        """
        out = ["ACCU-CHART DIAGNOSTICS - CALIBRATION STATUS", ""]
        for tank in tanks:
            out.append(f"TANK {tank} CAL STATUS")
            if not self.enabled(tank):
                out.append("ENABLE = OFF")
                out.append("")
                continue
            e = self.state(tank)
            now = time.mktime(self.c.now())
            out.append(f"ENABLE = ON  MODE = {e.mode}  "
                       f"ALARM = {'ON' if e.failed else 'OFF'}"
                       f"          USER ENABLE = "
                       f"{'ON' if e.user_status else 'OFF'}")
            out.append("START TIME   DURATION   MSSE   SUMWT"
                       "         SIGMA  MINht   maxHT UPDATES")
            days = (now - (e.mode_since or now)) / 86400.0
            out.append(f"{int(e.started or 0):>9d}{days:11.1f}"
                       f"{e.chart.fitness:7.2f}{e.data:8.0f}"
                       f"{self.sigma(e):14.2f}{e.low_height:7.1f}"
                       f"{e.high_height:7.1f}{e.updates:8d}")
            out.append("")
            out.append("CALIBRATION     CAP CAP_O_E      DIAM"
                       "             TILT       SLICE")
            counts = self.calibration_counts(e)
            out.append("COUNT      " + "".join(f"{n:8d}" for n in counts))
            out.append("SUMWEIGHT  "
                       + "".join(f"{n * e.data / max(sum(counts), 1):8.0f}"
                                 for n in counts))
            out.append("")
        return out

    @staticmethod
    def sigma(entry):
        """"standard deviation of residuals", which the outlier bounds use."""
        return round(entry.chart.fitness * 7.1 + 0.4, 2)

    @staticmethod
    def calibration_counts(entry):
        """How many of each kind of calibration have run.

        "The calibration parameters can be ranked in ascending order of height
        range required to fit a parameter as follows: 1) length, 2) probe
        offset, 3) end shape, 4) diameter, and 5) tilt", and the console
        groups the first three as CAP and CAP_O_E. A narrow height range
        never earns the later ones, which is the whole point of the ranking.
        """
        done = entry.updates
        span = entry.high_height - entry.low_height
        cap = min(done, 3)
        coe = max(0, done - 3)
        diam = max(0, done - 5) if span > 20.0 else 0
        tilt = max(0, done - 8) if span > 40.0 else 0
        return [cap, coe, diam, tilt, 0]

    def calibration_data_rows(self, tanks):
        """I@B900, TANK CALIBRATION DATA: what the fitting is being fed."""
        rows = ["TANK CALIBRATION DATA", "=" * 24]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            rows.append(f"T {tank}:{label}")
            rows.append("")
            rows.append("Opening  Closing     TLS       Dispensed"
                        "    Tank/Meter")
            rows.append(" Height   Height   Volume        Volume"
                        "         Ratio")
            entry = self.tanks.get(tank)
            for row in (entry.observations[-20:] if entry else []):
                rows.append(f"{row['open']:7.3f} {row['close']:8.3f}"
                            f"{row['tls']:9.2f}{row['metered']:14.2f}"
                            f"{row['ratio']:14.4f}")
            rows.append("")
        return rows

    def flags(self, tanks, which):
        """The two bit-encoded per-tank flags the variance reports carry.

        "LLLLLLLL - failure to calibrate in 56 days (bit encoded long integer
        with tank 1=lsb)" and "llllllll - tank chart alarm".
        """
        bits = 0
        for tank in tanks:
            entry = self.tanks.get(tank)
            if entry is None:
                continue
            on = entry.failed if which == "calibrate" else entry.warn
            if on:
                bits |= 1 << (tank - 1)
        return bits
