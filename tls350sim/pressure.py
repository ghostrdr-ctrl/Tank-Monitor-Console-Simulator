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
"""The pressure in a pressurised line, and the tests that measure it.

A PLLD console does not decide a line is leaking; it watches a transducer.
So does this. Every line carries a pressure in psi, the pump raises it, the
relief valve lets it back down, and a leak bleeds it away at a rate the pipe's
own stiffness decides. The tests then do exactly what the manual says they do
with that number.

The source is the PLLD & WPLLD Troubleshooting Guide, 577013-344 Rev H, which
is the manual the console's own PRESSURE LINE LEAK DIAG screen refers you to
("Refer to the PLLD/WPLLD Troubleshooting Manual (P/N 577013-344) for an
analysis of this function"). Its Theory of Operation chapter is short and
completely specific:

    Pressure relief valve closes @ 22 psi
    Line check valve opens @ 1 psi

GROSS (3 gph), a pump-Off test that follows the end of dispensing:

    1. After the pump is turned Off, the test waits time T1 for the line
       pressure to vent down to the relief valve pressure of approximately
       22 psi. At this time the reference pressure, P1, is measured. If P1
       is below 12 psi it is assumed there is a large leak and a retest is
       run to confirm the leak.
    2. If P1 is above 12 psi the test then waits time T2 to allow the line to
       lose pressure should there be a leak. At time T2 a second pressure
       measurement is made, P2.
    3. This monitoring process continues until P2 drops below 12 psi (a
       failure) or the P1-P2 comparison does not indicate a leak (a pass).

and Pon is "pressure measurement made just before pump is shut Off".

PRECISION (0.2 and 0.1 gph), pump-On tests:

    When the pump is turned On a pressure spike is trapped in the line. After
    a waiting time T1, the reference pressure P1 is measured. After waiting
    time T2, pressure P2 is measured. A leak rate (LR) is calculated using
    the values T1, T2, P1, and P2.

run as 15-minute cycles until the line is thermally stable:

    If the pressure is changing due to thermals, the leak rates will be
    different when measured 15 minutes apart. If the rates are different, the
    system continues to take measurements until two sequential leak rate
    calculations are equal (or within a certain tolerance). At this point the
    line is declared thermally stable and the state of the test determined by
    the last measured leak rate.

WHAT IS DERIVED AND WHAT IS NOT

The pressure-to-volume step is a bulk modulus, which is the whole reason the
console asks for the pipe type: dV/V = dP/K, so a line of volume V losing q
gallons an hour loses K*q/V psi an hour. Both numbers are published per pipe
type in the Line Leak Detection Systems Application Guide, 577013-465, and
`PIPE` below is that table.

What is NOT published anywhere in these manuals is the actual length of T1
and T2. 577013-344 says only what they depend on and in which direction:

    The wait times (T1 and T2) are based upon the line type and line length.
    ... In the case where a stiff line (steel or fiberglass) is programmed as
    a flex line the wait time will be excessively long ... When a soft flex
    line is programmed as a steel or fiberglass line the wait times will be
    too short and will not give the line enough time to lose the required
    amount of pressure that would permit identification of a leak near the
    fail threshold.

So `wait_times` is the one curve here that is the simulator's own: it has the
shape the manual describes (falls with stiffness, rises with volume) and is
scaled so that a 2-inch steel line and a PP1500 flex line both come out with
the sample printouts' numbers. It is marked as such wherever it is used, and
it is the only invented quantity in this file.
"""
import math
import time

from . import readings

# The console's display, and the reason these two lines are built by padding
# from the middle rather than by joining with a space. Every example of this
# screen in 577013-344 is exactly twenty-four characters with the switch state
# hard against the right-hand edge, and the giveaway is the WPLLD one:
#
#     W 1: PENDING    PUMP OFF
#
# "PENDING" is seven characters and there are FOUR spaces after it, which is
# the number that puts PUMP OFF at column 24 and no other number. The state is
# anchored to the right edge and the gap takes up whatever is left, so a
# shorter status word opens the gap rather than dragging the state left.
SCREEN = 24

# 577013-344, Figure 1: the two valves that decide where a line sits at rest.
RELIEF_CLOSES = 22.0        # "Pressure relief valve closes @ 22 psi"
CHECK_OPENS = 1.0           # "Line check valve opens @1 psi"

# "If P1 is below 12 psi it is assumed there is a large leak", and Figure 19
# gives the same number as the test criteria: P2 > 12 psi passes, P2 < 12 fails.
FLOOR = 12.0

TAU = 2.0 * math.pi

# Figure 19, "High Pressure Event Thresholds: Pon > 50 psi".
HIGH_PRESSURE = 50.0

# "Fifteen minutes after a Gross test has completed the Periodic test starts
# with the measurement of leak rate LR1. After another 15 minute waiting
# period LR2 is measured."
CYCLE = 15 * 60.0

# "When dispensing ends the pump remains On for 10 more seconds."
PUMP_TRAIL = 10.0

# The rate each test is looking for, in gallons per hour.
THRESHOLD = {"gross": 3.0, "periodic": 0.2, "annual": 0.1}

# "two sequential leak rate calculations are equal (or within a certain
# tolerance)". The tolerance is not published; the ratio is printed to two
# decimals, so two rates that print the same are the same.
STABLE = {"periodic": 0.005, "annual": 0.0025}

# Where a line settles once the relief valve has shut on it. It is under the
# valve's 22 psi by however much the last of the fuel took with it, and it is
# NOT the same number on two lines: a different pump, a different length of
# pipe and a different grade in it. Every P1 in 577013-344's sample printouts
# is 20-point-something, so the band sits just under the valve.
REST_BAND = (RELIEF_CLOSES - 3.4, RELIEF_CLOSES - 0.6)

# Every line loses a little pressure standing still. No manual gives a figure
# for a line that is NOT leaking, because that line is the reference the whole
# test is measured against -- what 577013-344 says is that a line passes when
# its rate is under the threshold. So the seep is written as a FRACTION OF THE
# ANNUAL THRESHOLD rather than as gallons an hour, which is the only way to
# be certain it can never fail the tightest test the console runs: a tenth of
# the 0.1 gph limit is a tenth of the limit on any line, whatever its
# stiffness and volume do to the pressures.
SEEP_FRACTION = (0.05, 0.15)

# THE SIMULATOR'S OWN. A seep that ran to zero would leave an untouched line
# unreadable and untestable after a night, which no forecourt has ever done:
# a standing line holds a head. So the loss eases off as the line approaches
# a residual and stops there, and the residual is above FLOOR so that a line
# nobody has touched is still a line the console can test.
SEEP_RESIDUAL = 0.78

# Nominal submersible pump pressures, System Setup Manual Table 12-1, and the
# Pon column of every sample printout in 577013-344 sits in the low thirties.
PUMP_PSI = 30.0

# Bulk modulus in psi and line volume in gallons per foot, per pipe type, from
# the Line Leak Detection Systems Application Guide 577013-465, "Supported Pipe
# Types and Line Lengths - For DPLLD and PLLD". Keyed by the console's own
# S788 piping-material enumeration.
#
# Two rows need a word. 01 is the console's single "2.0/3.0 FIBERGLASS"
# choice where the guide publishes the two diameters separately (25,000 psi
# and 0.204 gal/ft for 2 inch, 35,000 and 0.461 for 3 inch); the 2 inch row is
# used, because that is the size the setup screen asks for first and the one
# the length step defaults to. 04 "GEOFLEX II 1.5" has no row of its own in
# the guide at all: Geoflex II is the pre-2001 product, and the guide's
# footnote 3 says pre-2001 Geoflex piping "has a lower bulk modulus than the
# current product ... use the values in ( )", which for 1.5 inch is 5700.
PIPE = {
    "01": (25000.0, 0.204),     # 2.0/3.0 FIBERGLASS, 2 inch
    "02": (50000.0, 0.190),     # 2.0 STEEL
    "03": (3500.0, 0.092),      # ENVIROFLEX PP1501
    "04": (5700.0, 0.092),      # GEOFLEX II 1.5, footnote 3's pre-2001 value
    "05": (13000.0, 0.092),     # OMNIFLEX CP1501
    "06": (2400.0, 0.092),      # ENVIROFLEX PP1500
    "07": (7300.0, 0.092),      # ENVIROFLEX PP1502/2502
    "08": (9000.0, 0.092),      # OPW PISCES SP-15
    "09": (11650.0, 0.092),     # OPW PISCES CP-15
    "10": (11000.0, 0.163),     # WFG COFLEX 2000, 2 inch
    "11": (2500.0, 0.092),      # ENVIROFLEX PP1503/2503
    "12": (4500.0, 0.092),      # OMNIFLEX CP1503
    "13": (14500.0, 0.092),     # GEOFLEX D 1.5/2.0, 1.5 inch
    "14": (7400.0, 0.125),      # APT P175SC
    "15": (5400.0, 0.092),      # OPW PISCES CP15DW
    "16": (7600.0, 0.163),      # OPW PISCES CP20
    "17": (7000.0, 0.163),      # OPW PISCES SP20
    "19": (11500.0, 0.163),     # PETROTECHNIK UPP 63MM
}
DEFAULT_PIPE = "03"             # "The default is Enviroflex PP1501."

# "IMPORTANT! The default line length must be changed to reflect the actual
# line length or a Setup Data Warning will occur." The setup screens draw
# that default as 501 feet, which is what an unprogrammed line is worth here.
DEFAULT_LENGTH = 501.0

# The WPLLD setup offers six pipe types where PLLD offers nineteen, and names
# four of them by letter. They are the same pipes.
WPLLD_PIPE = {"01": "01", "02": "02", "03": "03", "04": "13", "05": "05",
              "06": "06"}

# 2 inch steel is the stiffest line the console offers and the one whose waits
# are shortest; every other pipe's waits are scaled off it.
REFERENCE_K = 50000.0
REFERENCE_GALLONS = 95.0        # 500 feet of it, the guide's maximum length


class Reading:
    """One pump-Off measurement: Pon, P1, P2, and what it decided.

    The record behind the 3.0 DIAG and MID DIAG printouts, which print
    "PON P1 P2" over a date and three pressures.
    """

    def __init__(self, when, pon, p1, p2, passed):
        self.when = when
        self.pon = pon
        self.p1 = p1
        self.p2 = p2
        self.passed = passed

    @property
    def high(self):
        """A high pressure event rather than a test result."""
        return self.pon > HIGH_PRESSURE

    def line(self):
        return f"{self.pon:.1f} {self.p1:.1f} {self.p2:.1f}"


class Cycle:
    """One precision leak rate: PON, RATIO, DUR, RESULT, as 0.20 DIAG prints."""

    def __init__(self, when, pon, p1, p2, rate, ratio, minutes, passed):
        self.when = when
        self.pon = pon
        self.p1 = p1
        self.p2 = p2
        self.rate = rate
        self.ratio = ratio
        self.minutes = minutes
        self.passed = passed

    def line(self):
        dur = (f"{self.minutes // 60}:{self.minutes % 60:02d}"
               if self.minutes >= 60 else f"{self.minutes}")
        return (f"{self.pon:.1f} {self.ratio:.2f} {dur} "
                f"{'PASS' if self.passed else 'FAIL'}")


class Line:
    """One pressurised line: its pressure, its pump, and the test on it.

    The state names are the console's own, from Figure 19's list of what the
    first PLLD diagnostic screen can say: TEST COMPLETE, DISPENSING, TEST 3.0,
    TEST 0.20, TEST 0.10, TEST ABORTED, RUNNING PUMP, PRESSURE CHECK,
    TEST PENDING, LINE LOCKOUT.
    """

    def __init__(self, engine, kind, number):
        self.engine = engine
        self.kind = kind
        self.number = number
        self.pressure = engine.rest_psi(kind, number)
        self.pump = False
        self.isolated = True        # the check valve has shut on the line
        self.handle = False
        self.state = "TEST COMPLETE"
        self.rate_key = None        # what this run is working towards
        self.leg = None             # which of the three tests is running now
        self.stage = None           # the measurement it is part way through
        self.waited = 0.0           # seconds spent in the current stage
        self.cycle = 0.0            # seconds spent in the current 15 min cycle
        self.started_at = 0.0       # console time the whole run began
        self.pon = 0.0
        self.p1 = None
        self.rates = []             # the LR values this run has measured
        self.retried = False        # a low P1 gets one confirming retest
        self.pending = None         # a measured rate waiting for its cycle
        self.readings = {"gross": [], "mid": []}
        self.cycles = {"periodic": [], "annual": []}
        self.thermal = 0.0          # psi an hour the ground is adding
        self.result = {}            # rate_key -> True/False, the last verdict
        # The transducer's own zero error, which the offset test measures and
        # function codes 089 and 090 reset. It has to be STORED rather than
        # derived, because a reset has to be able to change it -- a value that
        # is recomputed on every read cannot be reset, and the panel has had a
        # "P OFFSET RESET <ENTER>" screen doing nothing for want of one.
        self.offset = None
        # what the 0.20 and 0.10 DIAG printouts count up: SEQUENTIAL PASSES,
        # SEQUENTIAL FAILS, TOTAL PASSES, TOTAL FAILS
        self.tally = {k: {"pass": 0, "fail": 0, "run": 0, "runfail": 0}
                      for k in THRESHOLD}
        self.last_start = {}        # rate_key -> when that test last began

    # ---- what the pipe is --------------------------------------------------
    def pipe(self):
        """(bulk modulus, gallons per foot) for the programmed pipe type."""
        c = self.engine.c
        code = "788" if self.kind == "plld" else "7A8"
        raw = (c.values.get(f"S{code}{self.number:02d}") or "").strip()
        key = raw[-2:] if len(raw) >= 2 else DEFAULT_PIPE
        if self.kind == "wplld":
            key = WPLLD_PIPE.get(key, DEFAULT_PIPE)
        if key == "18":
            # USER DEFINED: the console asks for the modulus itself, and the
            # setup screen's own default is 0, which is no line at all
            modulus = c.limit("78B", self.number) or PIPE[DEFAULT_PIPE][0]
            return float(modulus), PIPE[DEFAULT_PIPE][1]
        return PIPE.get(key, PIPE[DEFAULT_PIPE])

    def length(self):
        """Programmed pipe length in feet."""
        code = "789" if self.kind == "plld" else "7A9"
        return self.engine.c.limit(code, self.number) or DEFAULT_LENGTH

    def volume(self):
        """Gallons of product the line under test holds."""
        return max(0.1, self.length() * self.pipe()[1])

    def psi_per_gallon(self):
        """K/V: how far one gallon out of this line moves the transducer.

        dV/V = dP/K is the whole of it. A stiff, short line answers a leak
        with a steep pressure drop; a long soft one barely moves, which is why
        the console will not certify 0.1 gph testing past 1100 feet.
        """
        return self.pipe()[0] / self.volume()

    def wait_times(self, rate_key):
        """(T1, T2) in seconds. THE SIMULATOR'S OWN CURVE - see the module docstring.

        577013-344 gives the dependence but never the numbers: the waits are
        "based upon the line type and line length", a stiff line wants short
        ones and a soft one long ones. So: inversely with stiffness, and with
        the line's volume, off 2 inch steel as the reference.
        """
        modulus, _ = self.pipe()
        stiffness = REFERENCE_K / max(modulus, 1.0)
        size = self.volume() / REFERENCE_GALLONS
        scale = max(0.5, min(12.0, stiffness * max(0.3, size) ** 0.5))
        if rate_key == "gross":
            # "3.0 gph - several minutes" is the manual's figure for a console
            # on a forecourt, and it is unwatchable on a panel somebody is
            # sitting in front of: ten seconds of pump and then a minute of a
            # number that barely moves reads as a hang, not as a test. So the
            # Gross window is about twenty seconds of measuring on a typical
            # line, which is long enough for T1 and T2 to be different
            # readings and short enough to watch. The shape is unchanged: it
            # still runs with stiffness and volume, and the Mid test still
            # borrows it.
            return min(10.0 * scale, 10.0), min(20.0 * scale, 20.0)
        # A precision leg and, on the second one, the Mid test after it both
        # have to fit inside the 15 minutes the cycle is allowed: the manual
        # counts "15 minutes to measure LR1 and another 15 minutes to measure
        # LR2" as the whole of a 30 minute Periodic test.
        return min(60.0 * scale, 180.0), min(150.0 * scale, 480.0)

    # ---- the physics --------------------------------------------------------
    def bleed(self, hours):
        """Let the leak, and the ground, move the pressure."""
        if hours <= 0:
            return
        leak = self.engine.leak_rate(self.kind, self.number)
        self.pressure -= self.psi_per_gallon() * leak * hours
        self.pressure -= self.seep_drop(hours)
        self.pressure += self.thermal * hours
        if self.thermal:
            # "the pressure of the fluid in the line may increase or decrease
            # rapidly after dispensing before tapering off as its temperature
            # approaches that of the line": a decaying slope, which is what
            # makes successive leak rates converge
            self.thermal *= 0.5 ** (hours / 0.35)
            if abs(self.thermal) < 0.01:
                self.thermal = 0.0
        self.pressure = max(0.0, self.pressure)

    def run_pump(self):
        """Pump On: "the line check valve opens and fuel is pumped into the line"."""
        self.pump = True
        self.isolated = False
        self.pressure = self.engine.pump_psi(self.kind, self.number)

    def stop_pump(self):
        """Pump Off.

        "the line pressure drops as the fuel in the line returns to the pump
        head chamber through the pressure relief valve. When the line pressure
        drops to the pressure relief valve's setpoint, the valve closes."
        That is where a pump-Off measurement starts from, and it is why every
        P1 in the manual's sample printouts is 20-point-something.
        """
        self.pump = False
        self.isolated = True
        self.pressure = min(self.pressure,
                            self.engine.rest_psi(self.kind, self.number))

    def trap(self):
        """"The line check valve closes trapping a pressure spike in the line."

        The pump does NOT come off: "The precision leak tests (Periodic, 0.2
        gph and Annual, 0.1 gph) are pump-On tests", and Figure 3 keeps the
        pump running right through the measurements. What isolates the line is
        the check valve, so the diagnostic screen reads PUMP ON while the
        trapped spike decays underneath it.
        """
        self.isolated = True

    # ---- the arithmetic a test does on two pressures -------------------------
    def rate_between(self, p1, p2, seconds):
        """Gallons an hour behind a pressure drop, which is dV/V = dP/K read backwards."""
        hours = seconds / 3600.0
        if hours <= 0:
            return 0.0
        return max(0.0, (p1 - p2) / self.psi_per_gallon() / hours)

    def measure_offset(self):
        """Run the pressure offset test and keep what it read.

        "Enter the Offset value exactly as displayed in the Offset test result
        message": so the console holds one figure until somebody resets it,
        rather than a fresh guess each time the screen is drawn.
        """
        if self.offset is None:
            self.offset = readings.fixed(-1.8, 1.8, "offset",
                                         self.kind, self.number)
        return self.offset

    def reset_offset(self):
        """Function codes 089 and 090, and the panel's own reset screen."""
        had = self.offset
        self.offset = 0.0
        return had

    def seep_drop(self, hours):
        """How far this line falls in that time just standing there.

        Proportional to how far it still is above its residual, which is what
        a seep through a small opening does and what stops the line from
        emptying: it creeps down and settles rather than running to nothing.
        At rest the loss is `seep_gph` exactly, and `seep_gph` is a fraction
        of the annual threshold, so a line that is otherwise sound cannot be
        failed by it.
        """
        rest = self.engine.rest_psi(self.kind, self.number)
        floor = rest * SEEP_RESIDUAL
        if self.pressure <= floor or hours <= 0:
            return 0.0
        full = self.psi_per_gallon() * self.engine.seep_gph(self.kind,
                                                            self.number)
        share = (self.pressure - floor) / max(rest - floor, 0.001)
        return full * hours * min(1.0, share)

    def leak_drop(self, rate_key, seconds):
        """How far a leak at the threshold rate would move this line in that time.

        The pump-Off tests are a comparison, "P1 - P2 < Leak threshold", and
        the threshold is a pressure because the console is holding a pressure
        against a rate: the same K/V that turns a drop into gallons an hour
        turns gallons an hour back into a drop.
        """
        return self.psi_per_gallon() * THRESHOLD[rate_key] * (seconds / 3600.0)

    # ---- bookkeeping ---------------------------------------------------------
    def begin(self, rate_key, now, stage):
        self.rate_key = rate_key
        self.leg = "gross"
        self.stage, self.waited, self.started_at = stage, 0.0, now
        self.rates = []
        self.retried = False
        self.pending = None
        self.p1 = None

    def measure(self, stage, now, restart):
        """Take the reference pressure P1 and move to the wait for P2.

        Where the second wait is timed from is not the same for the two kinds
        of test, and the manual is careful about it. Figure 6 draws T1 and T2
        for a precision test both from the moment the pump comes on, so P2
        comes T2-T1 after P1. The gross test's step 2 reads the other way,
        "If P1 is above 12 psi the test THEN waits time T2", so there T2 is a
        fresh wait that starts when P1 is taken.
        """
        self.p1 = self.pressure
        self.stage = stage
        if restart:
            self.waited = 0.0

    # ---- what the console shows ---------------------------------------------
    # "tt - Test status" of function codes 081 to 084. The console's own
    # screen words and the wire's own numbers for the same nine states.
    STATUS_CODE = {"TEST COMPLETE": "00", "DISPENSING": "01", "TEST 3.0": "02",
                   "TEST 0.10": "03", "TEST ABORTED": "04",
                   "RUNNING PUMP": "05", "LINE LOCKOUT": "06",
                   "DISABLE ALARM": "07", "TEST PENDING": "08",
                   "TESTING DELAY": "09", "PRESSURE CHECK": "0A",
                   "TEST 0.20": "0B"}

    def status_code(self):
        return self.STATUS_CODE.get(self.status(), "00")

    def status(self):
        """Line two of the first PLLD diag screen, in the manual's words."""
        if self.engine.disabled(self.kind, self.number):
            return "DISABLE ALARM"
        return self.state

    def programmed(self):
        """Whether the console has been told this line is there at all.

        "Four unprogrammed PLLD positions are four pieces of pipe nobody has
        told the console about", which is programmed_lines()'s own rule. The
        diagnostic still walks every position the card carries, the same way
        TANK/SENSOR does, so the screen is there -- but a console cannot read
        a pressure off a line it does not know it has.
        """
        return any(k == self.kind and n == self.number
                   for k, n, _label in self.engine.c.programmed_lines())

    def screen(self):
        """"Q 1: XX.XXX PSI PUMP OFF" over "TEST COMPLETE HANDLE OFF".

        An unprogrammed position keeps the columns and leaves the reading
        blank, because a blank is what the console has to say about it.
        """
        shown = f"{self.pressure:6.3f}" if self.programmed() else " " * 6
        pump = "PUMP ON" if self.pump else "PUMP OFF"
        handle = "HANDLE ON" if self.handle else "HANDLE OFF"
        left = f"{self.engine.code(self.kind)} {self.number}: {shown} PSI"
        return self._pad(left, pump), self._pad(self.status(), handle)

    @staticmethod
    def _pad(left, right):
        """`left`, then `right` hard against column 24.

        The state is the anchored field, so when the two will not both fit it
        is the status that gives way, not the state: a truncated PUMP OFF is
        unreadable where a truncated PRESSURE CHECK is still obviously the
        pressure check. Figure 19's longest status, PRESSURE CHECK, is
        fourteen characters and HANDLE OFF is ten, which is one over, so this
        is not hypothetical -- and what a real console does with that one is
        not something any manual here shows.
        """
        left = left[:max(0, SCREEN - len(right) - 1)]
        gap = SCREEN - len(left) - len(right)
        return f"{left}{' ' * gap}{right}"

    def running(self):
        return self.rate_key is not None

    def count(self, rate_key, passed, started):
        """Tally a finished test the way its diagnostic printout reports it."""
        t = self.tally[rate_key]
        t["pass" if passed else "fail"] += 1
        if passed:
            t["run"], t["runfail"] = t["run"] + 1, 0
        else:
            t["runfail"], t["run"] = t["runfail"] + 1, 0
        self.last_start[rate_key] = started

    def reason(self, rate_key):
        """"RESULT REASON CODE", which reads WORKING until there is a verdict."""
        if self.running() and self.leg == rate_key:
            return "WORKING"
        if rate_key not in self.result:
            return "WORKING"
        if self.result[rate_key]:
            return "PASS"
        return ("FAIL - SEQUENTIAL" if self.tally[rate_key]["runfail"] > 1
                else "FAIL")

    def sensor_counts(self):
        """(LO, SNS CNTS, HI) as the A/D screen draws them.

        "SNS CNTS should always be in between the LO and HI reference counts.
        Also the HI counts should always be less than the LO counts." So the
        scale runs downwards: LO is the count at no pressure, HI the count at
        the top of the transducer's range, and the reading sits between them.
        """
        lo, hi = 32768.0, 8192.0
        span = self.engine.pump_psi(self.kind, self.number) * 2.0
        frac = max(0.0, min(1.0, self.pressure / span)) if span else 0.0
        return lo, hi, lo - (lo - hi) * frac

    def pressures(self, which=None):
        """"P1: X.XXX  P2: X.XXX PSI", the pair the current leg has measured."""
        p1 = self.p1
        p2 = self.pressure if p1 is not None else None
        if which in ("periodic", "annual", "gross"):
            last = (self.cycles.get(which) or [None])[-1] if which != "gross" \
                else (self.readings["gross"] or [None])[-1]
            if last is not None and not self.running():
                p1, p2 = last.p1, last.p2
        elif which == "mid":
            last = (self.readings["mid"] or [None])[-1]
            if last is not None and not self.running():
                p1, p2 = last.p1, last.p2
        if p1 is None:
            return "P1: -.---  P2: -.--- PSI"
        return f"P1: {p1:.3f}  P2: {p2:.3f} PSI"

    def leg_name(self, which):
        return {"gross": "3.0 GPH", "periodic": "0.20 GPH",
                "mid": "MID TEST"}[which]

    def leg_clock(self, which):
        """"(MM:SS)": how long the leg on this screen has been going."""
        running = {"gross": ("t1", "t2"), "mid": ("mid1", "mid2"),
                   "periodic": ("spike1", "spike2")}[which]
        if self.stage not in running:
            return "(--:--)"
        seconds = int(self.waited)
        return f"({seconds // 60:02d}:{seconds % 60:02d})"


class Lines:
    """Every pressurised line on this console, and the tests running on them.

    One object drives both PLLD and WPLLD, because they are the same test on
    the same graph: 577013-344 documents them together, and its two diagnostic
    figures differ only in what the transducer is wired to.
    """

    def __init__(self, console):
        self.c = console
        self.lines = {}            # (kind, number) -> Line
        self._last = None          # console time this engine last looked

    # ---- the bench ----------------------------------------------------------
    def line(self, kind, number):
        key = (kind, number)
        if key not in self.lines:
            self.lines[key] = Line(self, kind, number)
        return self.lines[key]

    def leak_rate(self, kind, number):
        return max(0.0, self.c.line_leak.get((kind, number), 0.0))

    def disabled(self, kind, number):
        return (kind, number) in self.c.leaks.disabled

    def pump_psi(self, kind, number):
        """What the pump pushes the line to.

        Table 12-1 of the Setup Manual lists submersibles from 25 to 45 psi,
        and every Pon in 577013-344's sample printouts is in the low thirties.

        Two submersibles are not the same pump, and one submersible is not
        the same twice: the head varies with the pump, with how worn it is,
        with the temperature and with what else is running on the manifold,
        which is why a technician reads Pon rather than assuming it. So each
        line has its OWN nominal pressure, and it moves a little run to run.
        A programmed value is taken exactly, because somebody measured it.
        """
        psi = self.c.limit("7B7", number) if kind == "plld" else None
        if psi:
            return float(psi)
        nominal = readings.fixed(PUMP_PSI - 4.0, PUMP_PSI + 6.5,
                                 "pump", kind, number)
        # A submersible pushing fuel into a line does not sit on one figure:
        # the head moves with what the impeller is doing and with what else is
        # on the manifold, which is why a technician watches Pon for a moment
        # rather than reading it once.
        #
        # The slow part is wander()'s, which is what it is for. The fast part
        # is added here rather than asked of wander() because wander's swing
        # is a fraction of its BAND and it clamps to that band: turned up
        # far enough to move second to second it spends whole seconds pinned
        # to the band edge, which on the panel is the very thing this is
        # meant to fix -- a reading that sits on one number and looks hung.
        # A plain ripple has no edge to stick to.
        drift = readings.wander(self.c, nominal - 0.7, nominal + 0.7,
                                "pumprun", kind, number, swing=0.25,
                                period=240.0)
        phase = readings.fixed(0.0, TAU, "pumpphase", kind, number)
        when = time.mktime(self.c.now())
        ripple = (0.26 * math.sin(when / 1.9 * TAU + phase)
                  + 0.14 * math.sin(when / 4.7 * TAU + phase * 2.3))
        return drift + ripple

    def rest_psi(self, kind, number):
        """Where this line sits once the relief valve has shut on it.

        Its own number, not the same 21.0 on every line: the screens exist to
        be compared with each other and three identical readings tell a
        technician nothing.
        """
        return readings.fixed(*REST_BAND, "rest", kind, number)

    def seep_gph(self, kind, number):
        """This line's standing loss, as gallons an hour.

        A fraction of the annual threshold, per line, so it is always small
        enough to pass and never the same on two lines.
        """
        lo, hi = SEEP_FRACTION
        return THRESHOLD["annual"] * readings.fixed(lo, hi, "seep",
                                                    kind, number)

    @staticmethod
    def code(kind):
        """"Q" for a PLLD line, "W" for a WPLLD one, as the screens head them."""
        return "Q" if kind == "plld" else "W"

    def thermals(self, kind, number, psi_per_hour):
        """Put a thermal slope on a line, which is what lengthens a test.

        "Thermally-induced pressure change occurs when the ground temperature
        at the depth of the tank is different from the ground temperature at
        the line." A falling slope looks exactly like a leak until it decays.
        """
        self.line(kind, number).thermal = float(psi_per_hour)

    # ---- dispensing ---------------------------------------------------------
    def handle(self, kind, number, up):
        """The dispenser handle, which is what starts and ends everything.

        "A gross test always follows the completion of a dispense." And the
        other way: "If a dispense request occurs during any test, the test is
        aborted and the pump is turned On to commence dispensing. The testing
        will restart from the beginning once dispensing stops."
        """
        ln = self.line(kind, number)
        was, ln.handle = ln.handle, bool(up)
        now = self._last = time.mktime(self.c.now())
        if up and not was:
            if ln.running():
                self._abort(ln)
            ln.state = "DISPENSING"
            ln.run_pump()
        elif was and not up:
            ln.begin("gross", now, stage="trail")
            ln.state = "DISPENSING"

    def _abort(self, ln):
        ln.rate_key = ln.leg = ln.stage = None
        ln.rates = []
        ln.p1 = None
        ln.state = "TEST ABORTED"

    # ---- starting and stopping ----------------------------------------------
    def start(self, kind, number, rate_key):
        """Begin a manual test.

        "Tests always run in the order: 3.0 gph, 0.2 gph, and 0.1 gph", and
        the console says so while it gets going: "Q #: RUNNING PUMP".
        """
        ln = self.line(kind, number)
        if ln.handle:
            return "DISPENSING"
        if ln.running():
            return "TEST ALREADY RUNNING"
        self._last = time.mktime(self.c.now())
        ln.begin(rate_key, self._last, stage="pump")
        ln.state = "RUNNING PUMP"
        ln.run_pump()
        return "TEST STARTED"

    def stop(self, kind, number):
        ln = self.line(kind, number)
        if not ln.running():
            return "NO TEST RUNNING"
        self._abort(ln)
        ln.stop_pump()
        return "TEST ABORTED"

    def stop_all(self, kind):
        for k, number in list(self.lines):
            if k == kind:
                self.stop(k, number)

    # ---- time ---------------------------------------------------------------
    def tick(self):
        """Move every line's pressure, and every test along with it.

        The bench runs the console's clock as fast as you like, so one tick
        can be an hour wide. A test measures at a MOMENT, though, and a
        pressure read late is a leak rate read wrong, so this walks the
        interval deadline by deadline: bleed the line exactly as far as the
        next measurement, take it, and carry on with what is left.

        It keeps its own mark of when it last looked rather than being handed
        an interval, because the first interval after a test starts is the one
        that matters most and a shared counter has not been set by then.
        """
        now = time.mktime(self.c.now())
        last, self._last = self._last, now
        seconds = max(0.0, now - last) if last is not None else 0.0
        for ln in list(self.lines.values()):
            self._run(ln, seconds, now)

    def _run(self, ln, seconds, now):
        left = seconds
        for _ in range(500):            # every stage either consumes time or ends
            if ln.handle:
                # "the pump is turned On to commence dispensing"
                ln.pressure = self.pump_psi(ln.kind, ln.number)
                return
            total = self._stage_seconds(ln)
            due = None if total is None else max(0.0, total - ln.waited)
            if due is None or due > left:
                if ln.isolated:
                    ln.bleed(left / 3600.0)
                else:
                    # the check valve is open and the pump is filling the
                    # line, so what the transducer reads is the pump, now
                    ln.pressure = self.pump_psi(ln.kind, ln.number)
                ln.waited += left
                ln.cycle += left
                return
            if ln.isolated:
                ln.bleed(due / 3600.0)
            left -= due
            ln.waited += due
            ln.cycle += due
            self._fire(ln, now - left)
        return

    def _stage_seconds(self, ln):
        """How long the current stage lasts, or None if it is not on a clock."""
        if not ln.rate_key or not ln.stage:
            return None
        stage = ln.stage
        if stage in ("trail", "pump"):
            return PUMP_TRAIL
        if stage == "pad":
            # what is left of this cycle's fifteen minutes once it has
            # measured: LR1 lands 15 minutes in, LR2 at 30, LR3 at 45.
            # ln.cycle counts the pad itself too, so take that back off.
            return max(0.0, CYCLE - (ln.cycle - ln.waited))
        pumpoff = stage in ("t1", "t2", "mid1", "mid2")
        t1, t2 = ln.wait_times("gross" if pumpoff else ln.leg)
        if stage in ("t1", "mid1", "spike1"):
            return t1
        if stage in ("t2", "mid2"):
            # the gross and mid tests wait T2 from P1, so the whole of it is left
            return t2
        if stage == "spike2":
            # a precision leg keeps the clock it started at the pump, because
            # Figure 6 measures both T1 and T2 from there
            return t2
        return None

    def _fire(self, ln, now):
        """The stage the line has just finished waiting out."""
        stage = ln.stage
        if stage in ("trail", "pump"):
            self._begin_gross(ln, now)
        elif stage == "t1":
            if ln.pressure < FLOOR:
                # "If P1 is below 12 psi it is assumed there is a large leak
                # and a retest is run to confirm the leak. If it fails yet
                # again, a "Gross Test Fail" alarm is posted."
                if ln.retried:
                    ln.p1 = ln.pressure
                    self._finish_gross(ln, now, ln.p1, ln.pressure, False,
                                       ln.wait_times("gross")[1])
                    return
                ln.retried = True
                ln.run_pump()
                ln.stage, ln.waited = "pump", 0.0
                ln.state = "RUNNING PUMP"
                return
            ln.measure("t2", now, restart=True)
        elif stage == "t2":
            self._judge_gross(ln, now, ln.wait_times("gross")[1])
        elif stage == "pad":
            if self._commit(ln, now):
                self._after_rate(ln, now)
            elif ln.rate_key:
                self._begin_cycle(ln, now)
        elif stage == "spike1":
            ln.measure("spike2", now, restart=False)
        elif stage == "spike2":
            t1, t2 = ln.wait_times(ln.leg)
            self._close_cycle(ln, now, t1, t2)
        elif stage == "mid1":
            ln.measure("mid2", now, restart=True)
        elif stage == "mid2":
            self._judge_mid(ln, now, ln.wait_times("gross")[1])

    # ---- the gross test ------------------------------------------------------
    def _begin_gross(self, ln, now):
        """Pon is "made just before pump is shut Off"."""
        ln.leg = "gross"
        ln.pon = ln.pressure
        ln.stop_pump()
        ln.stage, ln.waited = "t1", 0.0
        ln.state = "TEST 3.0"

    def _judge_gross(self, ln, now, t2):
        p1, p2 = ln.p1, ln.pressure
        drop = ln.leak_drop("gross", t2)
        if p2 < FLOOR:
            self._finish_gross(ln, now, p1, p2, False, t2)
            return
        if (p1 - p2) < drop:
            self._finish_gross(ln, now, p1, p2, True, t2)
            return
        # ""Riding" the Pressure Drop": the loss looks like a leak but the line
        # is still above the floor, so run another pair and see whether it
        # levels off. A thermal slope does. A hole does not.
        ln.readings["gross"].append(Reading(now, ln.pon, p1, p2, None))
        # A FRESH wait, so the next P2 is taken T2 after this one. This read
        # `ln.waited = now`, a timestamp where a count of seconds belongs,
        # which made `total - waited` negative, fired the next stage on the
        # very next tick with no time elapsed, and compared P2 against itself:
        # a drop of exactly nothing, which passes any threshold. It never
        # showed while the Gross window was long, because a leak big enough
        # to get here drove P2 under the floor and failed on the line above.
        ln.p1, ln.stage, ln.waited = p2, "t2", 0.0
        ln.state = "PRESSURE CHECK"

    def _finish_gross(self, ln, now, p1, p2, passed, t2):
        ln.readings["gross"].append(Reading(now, ln.pon, p1, p2, passed))
        del ln.readings["gross"][:-20]
        ln.result["gross"] = passed
        ln.count("gross", passed, ln.started_at)
        self.c.leaks.record_line(ln.kind, ln.number, "gross", passed,
                                 ln.rate_between(p1, p2, t2), ln.started_at)
        if not passed or ln.rate_key == "gross":
            self._done(ln)
            return
        # "At the conclusion of the gross test a periodic test will be
        # performed if either a periodic or annual test is scheduled."
        # "Fifteen minutes after a Gross test has completed the Periodic
        # test starts with the measurement of leak rate LR1": the fifteen
        # minutes and the measurement are the same fifteen minutes.
        ln.leg = "periodic"
        self._begin_cycle(ln, now)

    # ---- the precision tests -------------------------------------------------
    def _begin_cycle(self, ln, now):
        """"When the pump is turned On a pressure spike is trapped in the line"."""
        ln.run_pump()
        ln.pon = ln.pressure
        ln.trap()                     # the check valve closes on the spike
        ln.stage, ln.waited, ln.cycle = "spike1", 0.0, 0.0
        ln.state = "TEST 0.20" if ln.leg == "periodic" else "TEST 0.10"

    def _close_cycle(self, ln, now, t1, t2):
        """P2 is in. The leak rate it makes is not declared until the cycle is.

        "15 minutes to measure LR1 and another 15 minutes to measure LR2":
        the rate belongs to its fifteen minutes, not to the moment the second
        pressure was read, which is why a Periodic test comes out at 30
        minutes and an Annual one at 45 rather than at the sum of the waits.
        """
        p1, p2 = ln.p1, ln.pressure
        rate = ln.rate_between(p1, p2, t2 - t1)
        ln.pending = (ln.pon, p1, p2, rate)
        if ln.leg == "periodic" and len(ln.rates) == 1:
            # "At the end of the second leak rate measurement the pump is
            # turned Off and a pump-Off test is performed." Pon is read before
            # it goes off, which is why the manual's MID DIAG printout shows a
            # Pon in the thirties over a P1 in the twenties.
            ln.pon = ln.pressure
            ln.stop_pump()
            ln.stage, ln.waited = "mid1", 0.0
            return
        ln.stop_pump()
        ln.stage, ln.waited = "pad", 0.0
        ln.state = "TEST PENDING"

    def _commit(self, ln, now):
        """The cycle is up: the rate it measured becomes LR(n)."""
        if not ln.pending:
            return False
        pon, p1, p2, rate = ln.pending
        ln.pending = None
        ratio = rate / THRESHOLD[ln.leg]
        minutes = int(round((now - ln.started_at) / 60.0))
        ln.cycles[ln.leg].append(Cycle(now, pon, p1, p2, rate, ratio, minutes,
                                       ratio < 1.0))
        del ln.cycles[ln.leg][:-10]
        ln.rates.append(rate)
        return True

    def _after_rate(self, ln, now):
        """Stable yet? "until two sequential leak rate calculations are equal"."""
        tolerance = STABLE[ln.leg]
        if len(ln.rates) >= 2 and abs(ln.rates[-1] - ln.rates[-2]) <= tolerance:
            self._finish_precision(ln, now)
            return
        self._begin_cycle(ln, now)

    def _judge_mid(self, ln, now, t2):
        """"The Mid test is a pump-Off test that is a part of the Periodic test"."""
        p1, p2 = ln.p1, ln.pressure
        passed = p2 >= FLOOR and (p1 - p2) < ln.leak_drop("periodic", t2)
        ln.readings["mid"].append(Reading(now, ln.pon, p1, p2, passed))
        del ln.readings["mid"][:-10]
        if not passed:
            # "If the Mid test fails, the Periodic Test Fail alarm is posted
            # and precision testing is complete."
            self._commit(ln, now)
            self._finish_precision(ln, now, failed_mid=True)
            return
        ln.stage, ln.waited = "pad", 0.0
        ln.state = "TEST PENDING"

    def _finish_precision(self, ln, now, failed_mid=False):
        which = ln.leg
        rate = ln.rates[-1] if ln.rates else 0.0
        passed = (not failed_mid) and rate < THRESHOLD[which]
        ln.result[which] = passed
        ln.count(which, passed, ln.started_at)
        self.c.leaks.record_line(ln.kind, ln.number, which, passed, rate,
                                 ln.started_at)
        if not passed or ln.rate_key == which or which == "annual":
            self._done(ln)
            return
        # "If the Periodic test result is a pass, the Annual test will follow.
        # The Annual test uses the last Periodic test rate, LR2, as it's
        # starting test rate LR1."
        # "The Annual test uses the last Periodic test rate, LR2, as it's
        # starting test rate LR1. After a fifteen-minute wait LR2 is measured."
        ln.leg = "annual"
        ln.rates = [rate]
        self._begin_cycle(ln, now)

    def _done(self, ln):
        ln.rate_key = ln.leg = ln.stage = None
        ln.p1 = None
        ln.state = "TEST COMPLETE"
        ln.stop_pump()
