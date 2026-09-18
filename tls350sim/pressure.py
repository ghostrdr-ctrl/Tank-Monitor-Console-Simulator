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
and T2 -- and function code 092 suggests no manual could publish them, because
the console derives them. Its test status enumeration runs "03 = PRESSURE 1
WAIT (PUMP OFF)", which is T1, then "05 = CALC WAIT TIME", which is the
console working a wait out; and 094 is "Recalculate Pressure Line Leak Profile
Bulk Modulus". The waits come from a measured bulk modulus, not from a table.
The third-party evaluations do bound the total: a 3.0 gph test is certified at
28.8 seconds on rigid pipe and one to six minutes on flexible. See UNKNOWNS
A13 for what that says about the caps below. 577013-344 says only what they depend on and in which direction:

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

# How long the whole Gross test may take on this bench, pump run included.
# The third-party evaluations certify a real 3.0 gph test at 28.8 seconds on
# rigid pipe and one to six minutes on flexible (see UNKNOWNS A13). Six
# minutes of a barely-moving number is not watchable on a panel somebody is
# sat in front of, so the long end is truncated here and the short end is
# not: a steel line runs at its real duration, a flexible one takes visibly
# longer, and nothing takes more than a minute.
GROSS_TEST_MAX = 60.0

# The rate each test is looking for, in gallons per hour.
THRESHOLD = {"gross": 3.0, "periodic": 0.2, "annual": 0.1}

# ---------------------------------------------------------------------------
# The four alarms 577013-727 Rev B states a trigger for in one sentence each,
# and this console carried the names for and never posted. See FIDELITY N2.

# p.9: "This alarm occurs when the fuel level is below 10 inches and a gross
# line test has failed. The alarm will clear when the fuel level exceeds 10
# inches." Both halves were already modelled -- S785 maps a PLLD line to its
# tank and the chart gives the level -- and Console.latches() special-cased
# this alarm to stop it latching, guarding a branch nothing could reach.
FUEL_OUT_INCHES = 10.0

# p.11: open is "when the pressure transducer is not connected to the PLLD
# Interface Module the pressure reading is negative".
SENSOR_OPEN_PSI = 0.0

# p.12: short is "when the pump-On and pump-Off pressures are reading the
# same value and are within the range of 5 to 15 psi".
SENSOR_SHORT_BAND = (5.0, 15.0)

# 576013-623 Rev AN p.10-6: "The Low Pressure Alarm Shutoff detects low
# pressure during a dispense. The Default value is 0 psi (0 kPa), with
# programmable values from 0 - 25 psi ... When the pressure drops below the
# entered shutoff value, the pump shuts off. The next handle up will restart
# the pump. A value of 0 will disable this alarm."
#
# The threshold is function code 78F, "PP - Low Pressure, PSI", which had no
# field and no setup step -- so the console drew the yes/no screen beside it
# and the number it is measured against could not be programmed at all.
LOW_PRESSURE_DISABLED = 0.0
SENSOR_SHORT_PSI = 0.05          # "the same value", to the printed precision

# 577013-727 Rev B p.8: "A continuous pump-in signal will activate: (Pre 19)
# A Continuous Pump On warning after 8 hours and a Continuous Pump On alarm
# after 16 hours, or (Version 19 and higher) A Continuous Handle alarm."
# The single alarm arrives at version 19 and PLLD itself at version 24, so
# on this console it is always the v19 form -- see `_line_faults`.
#
# The v19 form's delay is stated, in the other PLLD document: 577013-344 Rev H
# p.19, "A continuous Pump-in signal will activate a Continuous Handle alarm
# after 16 hours." This console counted the PRE-19 WARNING's eight hours
# instead, on the reasoning that the v19 alarm had no delay of its own -- so
# it posted an alarm at the hour a version it cannot be would have posted a
# warning.
HANDLE_ALARM_HOURS = 16.0

# And the sixteen hours is the default rather than the rule. 774, "Set
# Pressure Line Leak Continuous Handle Alarm Timeout", and 7AE, its WPLLD
# twin, are both "tt - Continuous Handle Alarm Timeout (Decimal, in hours,
# 1-16)", per line. `S77401` has been a field on this console the whole time
# and nothing read it, which is the shape FIDELITY's table collects: the
# right thing already present, and the display path spelling a literal.
HANDLE_TIMEOUT_HOURS = (1.0, 16.0)

# Which function code maps a line of each kind to the tank it feeds, and
# which alarm numbers that kind uses. VLLD is not here: 577013-727 is the
# PLLD/WPLLD quick help and says nothing about a volumetric line.
LINE_ALARMS = {
    "plld": {"tank_code": "785", "fuel_out": ("21", "17"),
             "open": ("21", "06"), "short": ("21", "15"),
             "handle": ("21", "16"), "handle_code": "774",
             "low": ("21", "14"),
             "equip": ("21", "18")},
    "wplld": {"tank_code": "7A5", "fuel_out": ("26", "17"),
              "open": ("26", "06"), "short": ("26", "15"),
              "handle": ("26", "16"), "handle_code": "7AE",
              "equip": ("26", "18"),
              # "A WPLLD Comm Alarm is posted when the transmission is not
              # received or when noise interferes with the reception" --
              # 577013-344 p.15. WPLLD's alone: a PLLD transducer is wired
              # to its module, a WPLLD's talks over the STP's power line.
              "comm": ("26", "07")},
    # WPLLD has no low pressure alarm: category 26's 14 is High Pressure,
    # and the shutoff's own codes -- S77C and S78F -- are PLLD's alone.

}

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
# "When the line pressure has been measured at pressures less than 5 psi, it
# is known that pressure transducer has little or no offset", 577013-344
# Rev H p.17, which is when Pd_ref is allowed to update itself.
LOW_OFFSET_PSI = 5.0

# ---------------------------------------------------------------------------
# The Line Equipment Fault alarm, 577013-344 Rev H pp.13-14. From version 19
# it is what REPLACES the High Pressure Warning and Alarm -- 577013-727 Rev B
# Appendix A Table I ends "19: Not Applicable" -- and it is not a threshold on
# the line's pressure at all but two monitors on the measurement system:
#
#   "The dispensing pressure monitor compares the dispensing pressure, Pd,
#    with the previously measured reference dispensing pressure, Pd_ref. If
#    the current Pd value exceeds the Pd_ref value by 5 psi for a continuous
#    period of one month, the Line Equipment Fault alarm will be posted."
#
#   "The vent pressure monitor identifies gross errors in the pressure
#    measurement system. This monitor runs after a passing Gross Line Test.
#    The Gross Test values Pon (pump on) and P2 (pump Off vent pressure - Pv)
#    are used to determine if there is a fault condition. If Pd is available,
#    the Pd value will be used in place of the Pon value.
#    If Pv > 40 psi and Pd > 50 psi -> Line Equipment Fault alarm is posted."
PD_FAULT_PSI = 5.0
PD_FAULT_SECONDS = 30 * 24 * 3600.0
VENT_FAULT_PV = 40.0
VENT_FAULT_PD = 50.0
# What the offset diagnostic prints for a Pd_ref nobody has established yet
PD_REF_UNKNOWN = 99.0

# Nominal submersible pump pressures, System Setup Manual Table 12-1, and the
# Pon column of every sample printout in 577013-344 sits in the low thirties.
PUMP_PSI = 30.0

# Bulk modulus in psi and line volume in gallons per foot, per pipe type, from
# the Line Leak Detection Systems Application Guide 577013-465, "Supported Pipe
# Types and Line Lengths - For DPLLD and PLLD". Keyed by the console's own
# S788 piping-material enumeration.
#
# One row needs a word. 04 "GEOFLEX II 1.5" has no row of its own in the
# guide at all: Geoflex II is the pre-2001 product, and the guide's footnote 3
# says pre-2001 Geoflex piping "has a lower bulk modulus than the current
# product ... use the values in ( )", which for 1.5 inch is 5700.
#
# 01 is the FIRST of two diameters, not the whole of the type -- see
# SECOND_PIPE.
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

# The SECOND diameter of the five pipe types that have one, out of the same
# 577013-465 Rev AD table as PIPE. Four of the console's nineteen choices name
# two sizes in the option text itself -- "2.0/3.0 IN. FIBERGLASS",
# "ENVROFLX PP1502/2502", "ENVROFLX PP1503/2503", "1.5/2 IN. ENVIRON GFLXD" --
# and USER DEFINED is programmed a second diameter and a second modulus of its
# own. A line of one of these is TWO connected lengths of pipe, and the setup
# asks for both: 576013-623 Rev AN p.10-3 heads its own block "ENTERING LINE
# LENGTH FOR DUAL SIZE FLEXIBLE PIPE TYPES", and p.10-4 says of User Defined
# that the second entry is for "two connected lengths of the same pipe type
# but with different diameters, or ... two connected lengths of different pipe
# types".
#
# The guide's prose says "the 2.5 inch size appears" for all three dual flex
# types, which is a slip: Geoflex D has no 2.5 inch row in the guide's own
# table and the console's option is named "1.5/2 IN." So the table is read
# for the size and the prose only for the shape.
SECOND_PIPE = {
    "01": (35000.0, 0.461),     # FIBERGLASS (3 INCH)
    "07": (8700.0, 0.255),      # PP2502 (2.5 INCH)
    "11": (3100.0, 0.255),      # PP2503 (2.5 INCH)
    "13": (11000.0, 0.163),     # GEOFLEX D (2 INCH)
}
USER_DEFINED = "18"


def gallons_per_foot(diameter):
    """Gallons a foot of pipe of that bore holds. The inverse of the bore.

    A gallon is 231 cubic inches, so a foot of pipe of diameter d holds
    pi/4 * d^2 * 12 / 231 gallons. `wirelines._diameter` has run this
    backwards since it was written, to print the bore of a pipe the guide
    gives only the volume for; this is the direction USER DEFINED needs,
    because there the console has the diameter and nothing else.

    It is a derivation rather than an invention, and the guide's own table
    is the check: every FLEXIBLE row in it agrees to the printed precision
    -- 1.5 inch is 0.092, 1.75 is 0.125, 2.0 is 0.163, 2.5 is 0.255, 3.0 is
    0.367 -- and so does 1 inch type K copper at 0.041. The two rigid
    fiberglass rows and the steel one do not, because their bore is wider
    than their nominal size: 0.204 gal/ft is a 2.24 inch bore on pipe the
    table calls 2 INCH. Those three have their own rows in PIPE and never
    reach this.
    """
    return math.pi / 4.0 * diameter * diameter * 12.0 / 231.0

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
        # When the handle went up, so a signal that never goes down can be
        # timed. "A continuous pump-in signal will activate ... a Continuous
        # Handle alarm" -- the console recorded the handle and never the
        # clock, so neither of the two names it carries could ever be posted.
        self.handle_since = None
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
        # "open" or "short": the transducer as the bench has wired it.
        # p.11 open: "the pressure reading is negative"; p.12 short:
        # "the pump-On and pump-Off pressures are reading the same
        # value". BENCH.md L10.
        self.transducer = None
        # noise on the STP's power line, for a WPLLD: "neon signs, variable
        # speed motors, and STP contactors". BENCH.md L11.
        self.noise = False
        self.result = {}            # rate_key -> True/False, the last verdict
        # The transducer's own zero error, which the offset test measures and
        # function codes 089 and 090 reset. It has to be STORED rather than
        # derived, because a reset has to be able to change it -- a value that
        # is recomputed on every read cannot be reset, and the panel has had a
        # "P OFFSET RESET <ENTER>" screen doing nothing for want of one.
        self.offset = None
        # Pd, the dispense pressure, and the reference it is judged against.
        # 577013-344 Rev H p.17: "At system startup the Pd_ref value is
        # unknown. When the dispense pressure, Pd, is calculated for the
        # first time this value is recorded as Pd_ref. Any subsequent growth
        # in pressure measurement offset can now be identified when the
        # current Pd value is compared to this reference value." A manual
        # reset puts Pd_ref back to the unknown state, "value of 99".
        self.pd = None
        self.pd_ref = None
        # When Pd first went more than 5 psi above Pd_ref and stayed there,
        # and what the vent monitor made of the last passing gross test.
        # Both belong to the Line Equipment Fault alarm.
        self.pd_high_since = None
        self.vent_fault = False
        # what the 0.20 and 0.10 DIAG printouts count up: SEQUENTIAL PASSES,
        # SEQUENTIAL FAILS, TOTAL PASSES, TOTAL FAILS
        self.tally = {k: {"pass": 0, "fail": 0, "run": 0, "runfail": 0}
                      for k in THRESHOLD}
        self.last_start = {}        # rate_key -> when that test last began

    # ---- what the pipe is --------------------------------------------------
    def raw_pipe_key(self):
        """The `788`/`7A8` piping material as the family stores it."""
        c = self.engine.c
        code = "788" if self.kind == "plld" else "7A8"
        raw = (c.values.get(f"S{code}{self.number:02d}") or "").strip()
        return raw[-2:] if len(raw) >= 2 else DEFAULT_PIPE

    def pipe_key(self):
        """The same, as a PIPE key -- WPLLD's six mapped onto PLLD's."""
        key = self.raw_pipe_key()
        if self.kind == "wplld":
            key = WPLLD_PIPE.get(key, DEFAULT_PIPE)
        return key

    def pipe(self):
        """(bulk modulus, gallons per foot) of the line's FIRST diameter.

        The first of two on the five types SECOND_PIPE names, and the whole
        of the line on the other fourteen.
        """
        c = self.engine.c
        key = self.pipe_key()
        if key == USER_DEFINED:
            # USER DEFINED: the console asks for the modulus itself, and the
            # setup screen's own default is 0, which is no line at all.
            #
            # `779`, Set Pressure Line Leak Primary Pipe Bulk Modulus, is
            # where that number goes -- it has a field, `S77901`, "PLLD
            # primary pipe bulk modulus, PSI", and a setup step, "User
            # Defined Pipe Type - 1st Bulk Modulus", that writes it. This
            # asked `78B`, which is Set Pressure Line Leak 0.10 GPH Test
            # SCHEDULE, so the panel stored the modulus where the manual says
            # it goes and the model looked for it in a schedule: a site that
            # programmed its own pipe got the default pipe's behaviour,
            # silently. WPLLD has no bulk modulus code of its own, so both
            # kinds read this one. See FIDELITY R8.
            #
            # The gallons a foot are the programmed DIAMETER's, `777`, "1ST
            # LINE DIAMETER" on the panel and "Pipe Diameter, Inches" on the
            # wire. This returned PP1501's 1.5 inch figure whatever the
            # technician had typed, so a user-defined 3 inch line was
            # modelled with a third of its volume. See FIDELITY R22.
            modulus = c.limit("779", self.number) or PIPE[DEFAULT_PIPE][0]
            bore = c.limit("777", self.number)
            volume = gallons_per_foot(bore) if bore else PIPE[DEFAULT_PIPE][1]
            return float(modulus), volume
        return PIPE.get(key, PIPE[DEFAULT_PIPE])

    def second_pipe(self):
        """(bulk modulus, gallons per foot) of the SECOND diameter, or None.

        None on the fourteen types that are one size, which is what makes
        `second_length` zero for them.
        """
        key = self.raw_pipe_key()
        if self.kind == "wplld":
            # The RAW key, because `WPLLD_PIPE` maps onto PIPE and the two
            # lists do not agree about how many diameters an option has.
            # WPLLD's `04` is "1.5 IN. ENVIRON GEOFLX D" and PLLD's `13`,
            # which it maps to, is "1.5/2 IN. ENVIRON GFLXD" -- the same
            # pipe at one size and at two. Mapping first would have given a
            # WPLLD Geoflex line a second segment its own option name says
            # it has not got. Fiberglass is the one two-diameter choice the
            # WPLLD setup offers, which is what console.py's WPLLD SETUP
            # report has always said by summing `7AD` for that index alone.
            return SECOND_PIPE["01"] if key == "01" else None
        if key != USER_DEFINED:
            return SECOND_PIPE.get(key)
        modulus = self.engine.c.limit("77A", self.number)
        bore = self.engine.c.limit("778", self.number)
        if not modulus or not bore:
            # "If the line consists of one pipe type, one diameter, ignore
            # the second line length entry" -- 576013-623 Rev AN p.10-4. An
            # unprogrammed second modulus or diameter is that case.
            return None
        return float(modulus), gallons_per_foot(bore)

    def length(self):
        """Feet of the FIRST diameter's pipe.

        Unprogrammed and zero are different here, and reading them alike is
        what this method used to do. "IMPORTANT! When using the Fiberglass
        pipe type, the unused size's length must be set to zero" --
        576013-623 Rev AN p.10-3 -- so a site with 3 inch fiberglass only
        stores 0 in `789`, and `or DEFAULT_LENGTH` gave it 501 feet of 2 inch
        pipe it has not got. A line with NEITHER length programmed is still
        the setup screen's own 501.
        """
        code = "789" if self.kind == "plld" else "7A9"
        feet = self.engine.c.limit(code, self.number)
        if feet is None:
            return 0.0 if self.second_length() else DEFAULT_LENGTH
        return feet

    def second_length(self):
        """Feet of the SECOND diameter's pipe; 0 where the type has none.

        `77F` for PLLD and `7AD` for WPLLD, whose own name in 576013-635 Rev
        AA says what it is for: "Set WPLLD Line Leak Secondary Pipe Length
        (only used for the larger diameter line in dual diameter piping
        configurations)".
        """
        if self.second_pipe() is None:
            return 0.0
        code = "77F" if self.kind == "plld" else "7AD"
        return self.engine.c.limit(code, self.number) or 0.0

    def segments(self):
        """[(bulk modulus, gallons)], one per length of pipe in the line.

        One for a single-diameter line and two for a dual one, with a
        segment of no length left out rather than carried as a zero.
        """
        out = [(self.pipe()[0], self.length() * self.pipe()[1])]
        second = self.second_pipe()
        if second is not None:
            out.append((second[0], self.second_length() * second[1]))
        return [(k, v) for k, v in out if v > 0.0]

    def volume(self):
        """Gallons of product the line under test holds.

        The sum over the segments, which is the guide's own arithmetic:
        "To determine the line volume for mixed piping types, multiply the
        line length (in feet) times the 'gallons/foot' value for each pipe
        type and add ... Total line volume = [150 x 0.204] + [50 x 0.461] =
        30.6 + 23.1 = 53.7 gallons", 577013-465 Rev AD footnote 1. This used
        to be the first segment alone, so that line came out at 30.6.
        """
        return max(0.1, sum(gallons for _k, gallons in self.segments()))

    def psi_per_gallon(self):
        """K/V: how far one gallon out of this line moves the transducer.

        dV/V = dP/K is the whole of it. A stiff, short line answers a leak
        with a steep pressure drop; a long soft one barely moves, which is why
        the console will not certify 0.1 gph testing past 1100 feet.

        Two segments share one pressure, so their give adds: a gallon out of
        the line drops it by 1/(V1/K1 + V2/K2). That is the same equation
        applied twice rather than a new one, and on a single-diameter line it
        is K/V exactly as before.
        """
        give = sum(v / max(k, 1.0) for k, v in self.segments())
        if not give:
            return self.pipe()[0] / self.volume()
        return 1.0 / give

    def modulus(self):
        """The line's effective bulk modulus, psi.

        K = V / (V1/K1 + V2/K2) -- the one number a dual line behaves as, and
        the primary's own on a single one.
        """
        return self.psi_per_gallon() * self.volume()

    def wait_times(self, rate_key):
        """(T1, T2) in seconds. THE SIMULATOR'S OWN CURVE - see the module docstring.

        577013-344 gives the dependence but never the numbers: the waits are
        "based upon the line type and line length", a stiff line wants short
        ones and a soft one long ones. So: inversely with stiffness, and with
        the line's volume, off 2 inch steel as the reference.
        """
        stiffness = REFERENCE_K / max(self.modulus(), 1.0)
        size = self.volume() / REFERENCE_GALLONS
        scale = max(0.5, min(12.0, stiffness * max(0.3, size) ** 0.5))
        if rate_key == "gross":
            # Calibrated against the certification rather than chosen. A 3.0
            # gph test on rigid pipe is certified end to end at 28.8 seconds;
            # take off the ten second pump run and 18.8 seconds of waiting is
            # left, which is what the reference line gets, split in the same
            # one-to-two ratio the two waits have always had here. A softer or
            # longer line scales up from there exactly as the manuals say it
            # should -- "the wait times are based upon the line type and line
            # length" -- instead of being flattened by a cap that bound for
            # every pipe but the stiffest.
            #
            # The one departure from the real thing is the ceiling: a real
            # flexible line runs one to six minutes and this one stops at
            # GROSS_TEST_MAX including the pump run. The scaling is truncated,
            # not switched off, so a flex line still visibly outlasts a steel
            # one. The Mid test borrows this leg.
            t1, t2 = 6.3 * scale, 12.5 * scale
            room = max(0.0, GROSS_TEST_MAX - PUMP_TRAIL)
            if t1 + t2 > room:
                shrink = room / (t1 + t2)
                t1, t2 = t1 * shrink, t2 * shrink
            return t1, t2
        # A precision leg and, on the second one, the Mid test after it both
        # have to fit inside the 15 minutes the cycle is allowed: the manual
        # counts "15 minutes to measure LR1 and another 15 minutes to measure
        # LR2" as the whole of a 30 minute Periodic test.
        #
        # Truncated the same way the gross leg is, rather than capped per
        # wait. `min(60*scale, 180), min(150*scale, 480)` flattened the table:
        # on a thousand-foot line sixteen of the eighteen pipe types came out
        # identical, and the cap bound HARDER the longer the line got, which
        # is backwards. 577013-344 makes mis-programming the pipe type a
        # troubleshooting procedure precisely because the waits should differ
        # -- "in the case where a stiff line (steel or fiberglass) is
        # programmed as a flex line the wait time will be excessively long" --
        # and a console that answers sixteen types the same cannot show the
        # symptom that procedure exists to diagnose. So the pair scales
        # freely and is shrunk in proportion only when it will not fit,
        # against the manual's own fifteen minutes rather than against two
        # invented ceilings. See FIDELITY R8.
        t1, t2 = 60.0 * scale, 150.0 * scale
        room = max(0.0, CYCLE - GROSS_TEST_MAX)
        if t1 + t2 > room:
            shrink = room / (t1 + t2)
            t1, t2 = t1 * shrink, t2 * shrink
        return t1, t2

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
        """Function codes 089 and 090, and the panel's own reset screen.

        "After a pressure offset reset the Pd_ref value is set to the
        unknown state (value of 99). It is updated when the next Pd value is
        calculated."
        """
        had = self.offset
        self.offset = 0.0
        self.pd_ref = None
        # "A manual reset should be performed after a transducer or pump has
        # been replaced": both monitors start again from what the repaired
        # equipment reads, not from what the old one did.
        self.pd_high_since = None
        self.vent_fault = False
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

    # Figure 19 is PLLD's list of status words and Figure 20 is WPLLD's,
    # and they are not the same list. 577013-344 Rev H p.22 gives PLLD
    # `RUNNING PUMP: The pump is running at the beginning of a test` and
    # `PRESSURE CHECK: Checking for high pressure after a 3.0 gph test`;
    # p.26's WPLLD list has neither, and 576013-610 Rev AC p.12-4 draws the
    # WPLLD start confirmation as `W #: TEST PENDING` where p.11-4 draws
    # PLLD's as `Q #: RUNNING PUMP`.
    #
    # The engine keeps one vocabulary and the GLASS translates, so the wire
    # is untouched -- `wirelines.py` already maps both PLLD-only words onto
    # WPLLD's `02` with a note that there is no code for them, and that
    # stays true.
    SHOWN_AS = {"wplld": {"RUNNING PUMP": "TEST PENDING",
                          # a 3.0 gph test is what is running while the
                          # pressure is checked, and TEST 3.0 is on
                          # Figure 20's list where PRESSURE CHECK is not
                          "PRESSURE CHECK": "TEST 3.0"}}

    # 577013-344 Rev H Figure 20, p.26: "Top line of display shows WPLLD Comm
    # Module status", and its five words with what each means --
    #
    #     PENDING       Waiting to start a test
    #     DECAY         Waiting for pressure measurement
    #     DISPENSING    Product is being dispensed
    #     PRESSURIZING  The line is being pressurized
    #     MEASUREMENT   Pump is on, sensor is recording/transmitting messages
    #
    # Which of the engine's stages is which word is on no page, and this
    # reading of the five meanings is the project's: the pump running up a
    # test (or trailing a dispense) is pressurizing the line, the pump-off
    # waits for P1 and P2 are waiting for a pressure measurement, and the
    # pump-on precision measurements are the sensor recording. UNKNOWNS A54.
    COMM_STATUS = {"pump": "PRESSURIZING", "trail": "PRESSURIZING",
                   "t1": "DECAY", "t2": "DECAY", "mid1": "DECAY",
                   "mid2": "DECAY", "spike1": "MEASUREMENT",
                   "spike2": "MEASUREMENT"}

    def comm_status(self):
        """The WPLLD Comm Module's own word for what this line is doing."""
        if self.handle:
            return "DISPENSING"
        if self.running():
            return self.COMM_STATUS.get(self.stage, "PENDING")
        return "PENDING"

    def shown_status(self):
        """The status word this KIND of line puts on the glass."""
        word = self.status()
        return self.SHOWN_AS.get(self.kind, {}).get(word, word)

    @property
    def reading(self):
        """What the console READS off the transducer, which is the pressure
        unless the transducer is not there: "when the pressure transducer
        is not connected to the PLLD Interface Module the pressure reading
        is negative"."""
        if self.transducer == "open":
            return -1.0
        return self.pressure

    def status(self):
        """Line two of the first PLLD diag screen, in the manual's words.

        A test that is running outranks the shutdown that is standing.

        This asked `disabled` first and returned unconditionally, so
        `RUNNING PUMP`, `TEST 3.0`, `PRESSURE CHECK` and `TEST ABORTED`
        could never appear on a shut-down line -- and that is exactly the
        line a technician needs to watch, because running a test on it is
        the documented way to get it back. 576013-623 Rev AN p.5-10 makes
        that the console's own default: "This feature lets you choose how
        to re-enable a line shut down by a failing line leak test. To
        re-enable a shutdown line only by a passed line test, press
        STEP", over the screen `LINE RE-ENABLE METHOD / PASS LINE TEST`.
        576013-610 Rev AC p.29-20 says the same for VLLD -- "the pump
        remains disabled until you reenable it by running a successful Self
        test."

        So the console would show a technician the one test he has been
        told to run, and this showed him `DISABLE ALARM` for the whole of
        it while the pump ran and the pressure climbed. 577013-344 Rev H
        p.22 lists all thirteen of these words as one set of test statuses,
        not as an overlay with one of them on top, and 576013-635 numbers
        them the same way -- `tt - Test status`, of which `07` is disable
        alarm and `05` is running pump.
        """
        if self.running():
            return self.state
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
        shown = f"{self.reading:6.3f}" if self.programmed() else " " * 6
        pump = "PUMP ON" if self.pump else "PUMP OFF"
        handle = "HANDLE ON" if self.handle else "HANDLE OFF"
        left = f"{self.engine.code(self.kind)} {self.number}: {shown} PSI"
        if self.kind == "wplld":
            # Figure 20 draws `W 1: PENDING    PUMP OFF` and says the top
            # line is the Comm Module's status. Figure 19 puts a pressure
            # there for PLLD and Figure 20 does not, because a WPLLD
            # transducer talks over the STP's power line. This drew
            # `W 1:        PSI  PUMP ON`. FIDELITY U14.
            word = self.comm_status() if self.programmed() else ""
            left = f"{self.engine.code(self.kind)} {self.number}: {word}"
        return self._pad(left, pump), self._pad(self.shown_status(),
                                                 handle)

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

    def blend_set(self, kind, number):
        """Every line in this line's blend set, itself included.

        576013-623 p.10-11, and it is the whole of what the manual says
        about the feature: "When a site has mechanical blenders, the lines
        can be assigned to a blend set. This change affects the
        scheduling of precision line testing, 0.2 and 0.1." The screens
        beneath it are `MECHANICAL BLENDER: YES` and then `Q 1: BLEND
        PARTNERS / Q#: 02, 03`.

        Both settings were stored by the panel and read by nothing -- they
        were two of the twelve on FIDELITY F12's list. What they are FOR is
        the thing a blended nozzle does: it runs the pumps on every line in
        the set at once, so a precision test on one of them is aborted by a
        dispense on any of the others. 577013-344 Rev H p.21 is the field
        symptom, cause 5 of a Periodic or Annual Test Needed warning: "If
        the site is extremely busy, especially if blenders are present,
        there may not be sufficient idle time to complete a Periodic or
        Annual test unless the station is shut down."

        PLLD only: the two steps are on PLLD LINE LEAK SETUP and neither
        the WPLLD nor the VLLD function has them.
        """
        if kind != "plld":
            return []
        if (self.c.setting("blender", number) or "NO").strip().upper() \
                != "YES":
            return []
        out = {int(number)}
        raw = str(self.c.setting("blend_partners", number) or "")
        for part in raw.replace(",", " ").split():
            if part.isdigit() and int(part):
                out.add(int(part))
        return sorted(out)

    def blend_busy(self, kind, number):
        """Is any OTHER line in this line's blend set dispensing?"""
        for partner in self.blend_set(kind, number):
            if partner != number and self.line(kind, partner).handle:
                return True
        return False

    def tank_of(self, kind, number):
        """Which tank this line comes out of, or 0."""
        code = {"plld": "785", "wplld": "7A5", "vlld": "752"}.get(kind)
        raw = (self.c.text(code, number) or "").strip() if code else ""
        return int(raw) if raw.isdigit() else 0

    def shutdown_alarms(self, kind, number):
        """The programmed disable-alarm assignments that are active NOW.

        787, 7A7 and 75B -- "Set Pressure Line Leak Disable Alarm
        Assignments" and its two siblings -- are the site saying WHICH
        alarms take this line out. The console stored them, printed them
        back and acted on none of them: the list was a list. So a site that
        had assigned an overfill or a high water alarm to shut a line down
        kept dispensing through it, and the one setting whose whole purpose
        is to stop fuel moving stopped nothing.

        This follows the CONDITION, not the message. Water in a sump
        assigned to shut a line down holds the line down until the water is
        gone -- not until somebody presses ALARM/TEST. The two halves of
        576013-610 Rev AC p.29-1 are the rule: "Warning and Alarm Messages
        display until you correct the cause ... you must press the
        ALARM/TEST button to acknowledge the alarm and clear the display",
        and, treated separately on the same page, "When you correct the
        condition, the lights will shut off." A de-energized pump is on the
        lights' side of that line, not the messages' side.

        This read `displayed()`, which is `_seen | latched`, so a sump that
        had dried out kept its line shut down until the alarm was
        acknowledged. `active_alarms()` is the live half.

        The FAILED-TEST route is a different thing and is not this: that
        one is governed by LINE RE-ENABLE METHOD (S553, `leaks.re_enable`),
        whose two choices are PASS LINE TEST and ACKNOWLEDGE ALARM, and
        576013-623 Rev AN p.5-10 scopes it to "a line shut down by a
        failing line leak test" in as many words.

        One scan stale at most, because the look is where this is asked
        from.
        """
        rows = self.c.line_disable_alarms.get((kind, number)) or []
        if not rows:
            return []
        shown = self.c.active_alarms()
        out = []
        for aa, nn, tt in rows:
            for record in shown:
                if record[:2] != aa or record[2:4] != nn:
                    continue
                # "TT - Tank/Sensor Number (Decimal, 00=all)"
                if tt in ("00", "0") or record[4:6] == tt:
                    out.append((aa, nn, tt))
                    break
        return out

    def disabled(self, kind, number):
        """Is this line's pump de-energized -- however it got that way?

        Three routes, and a technician has to tell them apart. A failed
        test is the one the console shuts down itself and the one a Self
        test clears. An assigned alarm is the site's own programming, and
        it goes when the alarm goes. A relay wired to the tank is the
        shutdown that is not in this module at all -- it is in the
        contactor -- and it is why a line with no line leak alarm on it can
        still be dead.
        """
        if (kind, number) in self.c.leaks.disabled:
            return True
        if self.shutdown_alarms(kind, number):
            return True
        tank = self.tank_of(kind, number)
        return bool(tank and self.c.outputs.pump_cut(tank))

    def programmed_psi(self, kind, number):
        """776, the Profile Line Test Reference Pressure, or None.

        576013-635 Rev AA p.21239, `Set Pressure Line Leak Profile Line Test
        Reference Pressure 776`, Version 23, entered as `ppp.pp`. It is
        PLLD's alone: the WPLLD family has no profile line test -- B7B is
        PLLD-only and there is no `7Ax` twin for this -- so a WPLLD line
        never has one programmed and always gets its own generated figure.

        This used to ask for `7B7`, which is not a function code: it is in
        no field, in no census entry and nowhere in 576013-635, so nothing
        could ever store it and the branch below was unreachable on every
        console. See FIDELITY R17.
        """
        psi = self.c.limit("776", number) if kind == "plld" else None
        return float(psi) if psi else None

    def nominal_psi(self, kind, number):
        """Where this line's reference pressure sits, before it moves.

        The one place the generated band is spelled, because the offset
        monitor report reads the same quantity and the two must not drift.
        """
        return self.programmed_psi(kind, number) or readings.fixed(
            PUMP_PSI - 4.0, PUMP_PSI + 6.5, "pump", kind, number)

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
        psi = self.programmed_psi(kind, number)
        if psi:
            return psi
        nominal = self.nominal_psi(kind, number)
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
        """The letter the screens head this family's lines with.

        "Q" for a PLLD line, "W" for a WPLLD one, "P" for a VLLD one --
        576013-610 Rev AC ch.13 heads every VLLD screen `P #:`, and
        `printer.py` has carried the same three-way table in three places
        all along. This named two families and fell through to the WPLLD
        letter for the third, so the one screen that reached it with a VLLD
        line answered `W 1:` -- the wrong card's letter, on the function a
        technician starts a volumetric test from. See CLOSED U34.
        """
        return {"plld": "Q", "vlld": "P"}.get(kind, "W")

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
        ln.handle_since = now if up and not was else (
            ln.handle_since if up else None)
        if up and not was:
            if ln.running():
                self._abort(ln)
            if self.disabled(kind, number):
                # A handle is a request to the pump, and a shut-down pump
                # does not answer it. The console has de-energized the STP
                # for this line -- that is what a shutdown IS -- so lifting a
                # handle gets no pump and no pressure, and the line stays
                # where the shutdown left it until it is re-enabled.
                #
                # This console had the shutdown driving every REPORT of
                # itself -- the 21/08 alarm, function 381's DISPENSING
                # column and its bit 1, the DISABLE ALARM diagnostic, the
                # bench card -- and nothing physical, so the next handle up
                # ran the pump straight through it and the pressure came
                # back. A technician practising the one recovery a line leak
                # shutdown needs was taught that there is nothing to recover
                # from.
                #
                # 576013-610 Rev AC makes "the next handle up will restart
                # the pump" a property of the LOW PRESSURE ALARM row of
                # Table 29-11 and of no other row on that page. For a
                # shutdown, p.29-20: "the pump remains disabled until you
                # reenable it by running a successful Self test." And a
                # handle lifted during the recovery is an ABORT rather than
                # a start -- p.29-21, "Prevent the dispenser handles from
                # being lifted. If someone lifts a handle, the system alarms
                # and you will have to begin the procedure again."
                #
                # See FIDELITY U4.
                ln.state = "DISPENSING DISABLED"
                return
            ln.state = "DISPENSING"
            ln.run_pump()
        elif was and not up:
            # the dispense pressure, measured while the line was flowing
            ln.pd = ln.pressure
            if ln.pd_ref is None or ln.pressure < LOW_OFFSET_PSI:
                # "When the line pressure has been measured at pressures
                # less than 5 psi, it is known that pressure transducer has
                # little or no offset. At this time Pd_ref will be updated
                # to the current Pd value."
                ln.pd_ref = ln.pd
            # THE DISPENSING PRESSURE MONITOR: "if the current Pd value
            # exceeds the Pd_ref value by 5 psi for a continuous period of
            # one month". Continuous is the word that makes this a clock
            # rather than a comparison, so what is kept is when it started.
            if ln.pd > ln.pd_ref + PD_FAULT_PSI:
                if ln.pd_high_since is None:
                    ln.pd_high_since = now
            else:
                ln.pd_high_since = None
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
        # A precision test on a blend set needs the SET quiet, not just this
        # line: the blended nozzle on the next island runs this pump too.
        # The gross test is not on the list -- p.10-11 names "precision line
        # testing, 0.2 and 0.1" and nothing else.
        if rate_key in ("periodic", "annual") \
                and self.blend_busy(kind, number):
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

    # ---- the alarms a line raises by itself ---------------------------------
    def conditions(self):
        """[AANNTT] for the four faults 577013-727 Rev B states a trigger for.

        Each is a sentence in the quick help, and every one of the states
        they turn on was already modelled here. See FIDELITY N2.
        """
        out = []
        now = time.mktime(self.c.now())
        # Only lines the console admits to having: an unprogrammed position
        # is a piece of pipe nobody has told the console about, and it
        # neither tests it nor reports it.
        for kind, number, _label in self.c.programmed_lines():
            names = LINE_ALARMS.get(kind)
            if not names or (kind, number) not in self.lines:
                continue
            for aa, nn in self._line_faults(self.lines[(kind, number)],
                                            names, now):
                out.append(aa + nn + f"{number:02d}")
        return out

    def _line_faults(self, ln, names, now):
        """Which of them this one line is showing."""
        out = []
        # "the pressure reading is negative"
        if ln.reading < SENSOR_OPEN_PSI:
            out.append(names["open"])
        if ln.noise and "comm" in names:
            out.append(names["comm"])
        # "the pump-On and pump-Off pressures are reading the same value and
        # are within the range of 5 to 15 psi". The last gross reading holds
        # both: Pon is measured just before the pump is shut off and P2 is
        # the last pump-Off pressure of the same test.
        last = (ln.readings["gross"] or [None])[-1]
        low, high = SENSOR_SHORT_BAND
        if ln.transducer == "short":
            out.append(names["short"])
        elif last is not None and last.p2 is not None:
            if (abs(last.pon - last.p2) <= SENSOR_SHORT_PSI
                    and low <= last.pon <= high and low <= last.p2 <= high):
                out.append(names["short"])
        # "the fuel level is below 10 inches and a gross line test has
        # failed. The alarm will clear when the fuel level exceeds 10
        # inches" -- so the level is the live condition and the failed test
        # is the qualifier, which is why this one does not latch.
        if ln.result.get("gross") is False:
            # `text()` and not `limit()`: the tank number is stored behind the
            # device prefix as `0101`, and `limit()` only strips a prefix when
            # the value is longer than eight characters, so it reads that as
            # the number one hundred and one.
            raw = (self.c.text(names["tank_code"], ln.number) or "").strip()
            tank = int(raw) if raw.isdigit() else 0
            level = self.c.tank_level.get(tank)
            if level is not None:
                height = self.c.height_at(tank, level.get("volume", 0.0))
                if height < FUEL_OUT_INCHES:
                    out.append(names["fuel_out"])
        # "The Low Pressure Alarm Shutoff detects low pressure DURING A
        # DISPENSE ... When the pressure drops below the entered shutoff
        # value, the pump shuts off." So it is measured only while the
        # handle is up, and a threshold of zero disables it.
        if "low" in names and ln.handle and self._low_pressure_limit(ln):
            if ln.pressure < self._low_pressure_limit(ln):
                out.append(names["low"])
        # "(Pre 19) A Continuous Pump On warning after 8 hours and a
        # Continuous Pump On alarm after 16 hours, or (Version 19 and higher)
        # A Continuous Handle alarm."
        #
        # Only the second half can happen here, and not by choice: PLLD and
        # WPLLD both arrive at version 24 in the version tables, so a
        # console old enough for the pre-19 pair cannot have a pressurised
        # line to raise them on. The two names are still in the alarm table
        # (21/10, 26/09) and stay unproducible for that reason -- which is a
        # better answer than the code carrying a branch it can never take.
        #
        # The v19 form's own delay is sixteen hours -- 577013-344 p.19 --
        # and it is programmable per line at 774 and 7AE.
        if ln.handle and ln.handle_since is not None:
            hours = max(0.0, now - ln.handle_since) / 3600.0
            if hours >= self._handle_timeout(ln):
                out.append(names["handle"])
        # "Two monitors are used to identify a problem with the pressure
        # measurement equipment", and either one posts the alarm. This is
        # what a version 19 and higher console has INSTEAD of the High
        # Pressure Warning and Alarm; see FIDELITY N2a for why the pre-19
        # pair cannot be reached from here at all.
        if ln.vent_fault or (ln.pd_high_since is not None
                             and now - ln.pd_high_since >= PD_FAULT_SECONDS):
            out.append(names["equip"])
        return out

    def _handle_timeout(self, ln):
        """The hours a handle may stay up, from 774 or 7AE.

        "tt - Continuous Handle Alarm Timeout (Decimal, in hours, 1-16)" on
        both codes, per line. An unprogrammed line gets the sixteen hours
        577013-344 p.19 states outright, which is also the value the serial
        manual's own sample response prints. Out-of-range values are held to
        the manual's pair rather than refused: the panel and the wire both
        police the field, so a number outside it can only arrive from a
        store somebody else wrote.
        """
        low, high = HANDLE_TIMEOUT_HOURS
        code = LINE_ALARMS[ln.kind].get("handle_code")
        hours = self.c.limit(code, ln.number) if code else None
        if not hours:
            return high
        return min(max(hours, low), high)

    def _low_pressure_limit(self, ln):
        """The psi below which a dispense is a fault, or 0 for disabled.

        Two screens: `LOW PRESSURE SHUTOFF: NO` is the flag at S77C and
        `LOW PRESSURE: 5` is the number at S78F, "0 - 25 psi". Both have to
        be set -- "A value of 0 will disable this alarm" -- and the console
        had the flag alone.
        """
        flag = (self.c.values.get(f"S77C{ln.number:02d}") or "").strip()
        if not flag.endswith("1"):
            return LOW_PRESSURE_DISABLED
        raw = (self.c.text("78F", ln.number) or "").strip()
        try:
            return float(raw)
        except ValueError:
            return LOW_PRESSURE_DISABLED

    def _run(self, ln, seconds, now):
        left = seconds
        for _ in range(500):            # every stage either consumes time or ends
            if ln.handle and not self.disabled(ln.kind, ln.number):
                # "the pump is turned On to commence dispensing"
                ln.pressure = self.pump_psi(ln.kind, ln.number)
                return
            if ln.handle:
                # A handle up on a shut-down line: no pump, so the line
                # bleeds like any other unpressurised one rather than being
                # re-pinned to pump pressure every tick. Gating only the
                # handler above would have left this to undo it. See U2.
                ln.bleed(left / 3600.0)
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
        if passed:
            # THE VENT PRESSURE MONITOR, which "runs after a passing Gross
            # Line Test": P2 is the pump-off vent pressure Pv, and "if Pd is
            # available, the Pd value will be used in place of the Pon
            # value". The vent pressure is nominally 22 psi, and the Pd term
            # is what stops a restricted relief path alone from raising it.
            against = ln.pd if ln.pd is not None else ln.pon
            ln.vent_fault = (p2 > VENT_FAULT_PV and against > VENT_FAULT_PD)
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
        if self.blend_busy(ln.kind, ln.number):
            # Except on a blend set with a partner still flowing, which is
            # the scheduling p.10-11 says the setting affects: the precision
            # leg would be aborted by that partner the moment it began, so
            # it waits for the next gross test instead. On a busy blender
            # site it waits all day, which is 577013-344 p.21 cause 5.
            self._done(ln)
            # after `_done`, which sets TEST COMPLETE: the precision leg is
            # not complete, it is waiting. 577013-344 p.22's own word for a
            # test that is scheduled and has not run.
            ln.state = "TEST PENDING"
            return
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
