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
"""The ISD and PMC setup functions, section 7.7.2 of 576013-635.

In-Station Diagnostics watches a site's vapour recovery: what the nozzles
collect, what the lines contain, and what the processor does about it.
Pressure Management Control is the half that runs the processor. They are
separate features with separate keys, and the manual is careful about which
functions want which -- some say "PMC feature required", some "ISD feature
required", V47 says "ISD or PMC" and V50 says "ISD and PMC" -- so a console
with one key and not the other answers a different set of codes.

This module is the SETUP half: eleven functions that hold a value, plus the
version number. The reports that read them are their own job.

Each entry says how the value is written on the wire, what it may be, and how
the console prints it back. `kind` is the shape:

    enum     fixed width digits out of a named table
    pair     two of those, side by side (V4E's EVR type and vacuum type)
    int      decimal digits in a range
    float    one ASCII hex IEEE float in a range
    floats2  two of them
    clock    HHMM and then a count of minutes
    flag     one digit

`verify` is the confirmation code the manual will not let you set without.
"""

# The processors V40 names, in V40's own words: the inquiry's display format
# answers "VST ECS PROCESSOR" over "VAPOR PROCESSOR TYPE", and the computer
# format's note enumerates all eight codes. Three are obsolete and are kept
# because a console programmed before then still answers with one.
VAPOR_PROCESSOR = {
    "00": "NONE",
    "01": "VST ECS PROCESSOR",
    "02": "OPW VAPOR PROCESSOR",       # obsolete V28
    "03": "HIRT VAPOR PROCESSOR",      # "(ISD SEM required)"
    "04": "USER DEFINED",              # obsolete V28
    "05": "VEEDER-ROOT POLISHER",      # added V30
    "06": "HUSKY POLISHER",            # obsolete V30
    "07": "VST GREEN MACHINE",         # added V30
}

# "PMC Setup for VST Processors" is Figure 15's own title, and the note
# beside it is the gate: "the vapor processor type VST must have been
# selected in EVR/ISD setup to access PMC setup". VST is TWO of V40's codes
# rather than one, and the figure says so itself: its Vapor Processor On/Off
# example sets a turn on and a turn off pressure for "ECS Membrane" and for
# "Green Machine" side by side, both of them on this walk. See FIDELITY I7.
VST_PROCESSORS = ("01", "07")

# The panel offers a SHORTER list than the wire, and spells it differently.
# 577013-937 Rev J Figure 9's VAPOR PROCESSOR TYPE screen walks five --
# "None", "VST Vapor Processor", "VST Green Machine", "V-R Polisher",
# "Hirt VCS 100" -- and V40's own set note offers the same five codes:
# "07 = VST Green Machine, 05 = Veeder-Root Polisher, 03 = HIRT Vapor
# Processor, 01 = VST ECS Processor, 00 = None". One field on two surfaces,
# so the words here are the screen's and the codes are V40's.
#
# 577013-800 Rev P's own Figure of the same screen walks "NONE" and "ARID
# PERMEATOR" instead, which is what this list used to hold. No V40 code on
# this shelf is an ARID Permeator -- 03 is HIRT in both serial revisions --
# so that word cannot name a code, and Rev J's five replace it.
VAPOR_PROCESSOR_PANEL = [
    ("NONE", "00"),
    ("VST VAPOR PROCESSOR", "01"),
    ("VST GREEN MACHINE", "07"),
    ("V-R POLISHER", "05"),
    ("HIRT VCS 100", "03"),
]


def panel_processor(code):
    """The word the panel's VAPOR PROCESSOR TYPE screen puts on a V40 code.

    A console programmed to one of the three codes that screen no longer
    offers -- OPW, User Defined, Husky -- still has to draw something, and
    what it has is V40's own word for it.
    """
    code = (code or "00").strip()[-2:] or "00"
    for word, digits in VAPOR_PROCESSOR_PANEL:
        if digits == code:
            return word
    return VAPOR_PROCESSOR.get(code, "NONE")


def panel_processor_code(word):
    """The V40 code behind one of that screen's words, or None."""
    for name, digits in VAPOR_PROCESSOR_PANEL:
        if name == (word or "").strip().upper():
            return digits
    return None

CONTROL_LEVEL = {"00": "FULL", "01": "PARTIAL", "02": "NO"}
EVR_TYPE = {"01": "BALANCE", "02": "VACUUM ASSIST"}
# "02" was WAYNE VAC, which no page on this shelf carries. 577013-800 Rev P
# walks the panel `VACUUM ASSIST TYPE / VAPOR VAC` then CHANGE to `HEALY
# VAC`, and 577013-819 Rev F's setup check asks "VACUUM ASSIST TYPE is set
# to HEALY VAC?". The panel's own choice list said HEALY VAC all along --
# the two were separate stores with separate words. See FIDELITY I10.
VACUUM_TYPE = {"01": "VAPOR VAC", "02": "HEALY VAC"}
ENABLE_FLAG = {"0": "ENABLE", "1": "DISABLE"}

SETUP = {
    "V40": {"needs": ("pmc",), "kind": "enum", "width": 2,
            "table": VAPOR_PROCESSOR, "default": "00",
            "title": "VAPOR PROCESSOR TYPE", "line": None},
    "V41": {"needs": ("pmc",), "kind": "enum", "width": 2,
            "table": CONTROL_LEVEL, "default": "00",
            "title": None, "line": "PROCESSOR CONTROL LEVEL:"},
    "V44": {"needs": ("pmc",), "kind": "floats2", "verify": "149",
            "range": (-8.0, 3.0), "default": (-2.0, 0.2),
            "title": "VAPOR PROCESSOR", "line": "PRESSURE THRESHOLDS:",
            "units": "IN H2O"},
    "V45": {"needs": ("pmc",), "kind": "int", "width": 3, "range": (10, 180),
            "default": 60, "title": "VAPOR PROCESSOR",
            "line": "MAXIMUM RUNTIME:", "units": "MIN"},
    "V46": {"needs": ("pmc",), "kind": "float", "range": (0.0, 100.0),
            "default": 10.0, "title": None,
            "line": "HYDROCARBON ALARM THRESHOLD:", "units": "%"},
    "V47": {"needs": ("isd", "pmc"), "any": True, "kind": "clock",
            "width": 3, "range": (0, 999), "default": ("1159", 1),
            "title": None, "line": "TEST START TIME:"},
    "V4E": {"needs": ("isd",), "kind": "pair", "width": 2,
            "table": EVR_TYPE, "table2": VACUUM_TYPE, "default": "0101",
            "title": "ISD EVR TYPE", "line": None},
    "V4F": {"needs": ("isd",), "kind": "floats2", "range": (0.5, 1.5),
            "default": (0.5, 1.5), "title": "NOZZLE A/L RANGE",
            "line": None, "units": ""},
    "V50": {"needs": ("isd", "pmc"), "kind": "clock", "width": 3,
            "range": (0, 720), "default": ("0200", 120), "title": None,
            "line": "CVLD MIN PRESSURE WINDOW:"},
    "V52": {"needs": ("isd", "pmc"), "any": True, "kind": "flag",
            "table": ENABLE_FLAG, "default": "0", "title": None,
            "line": "ACCEPT HIGH ORVR:"},
}

# "ISD VERSION: 01.00". The console reports what its ISD software is, and it
# is not the console's own version number: ISD arrived at software 25 and
# carries a version of its own.
ISD_VERSION = "01.00"


# ---------------------------------------------------------------------------
# The sensor / airflow meter / hose / grade tables, function code V42.
#
# V42 is the only thing that writes any of them -- V48, V4A and V4B all say
# "Inquire only, use Function Code V42 to set" -- so there is ONE store here
# and the other three reports are views of it. A row is a smart sensor, the
# airflow meter on it, and the two fuel positions that meter serves, each with
# up to four meter/hose/label triples:
#
#     SS AA  F1 FL M1H1L1 M2H2L2 M3H3L3 M4H4L4  F2 FL M1H1L1 ... M4H4L4
#      2  2   2  2      6      6      6      6   2  2      6 ...      6
#
# which is sixty characters, and the manual's own worked example measures the
# same. "UU" is an unassigned hose and "00" an unassigned anything else.
# ---------------------------------------------------------------------------
ROW = 60
POSITIONS = 2          # fuel positions per airflow meter
TRIPLES = 4            # meter/hose/label triples per fuel position
UNASSIGNED = "UU"

# "II - Hose Label ID (02-10, 01=Unassigned)", and V49's example table.
LABEL_IDS = [f"{n:02d}" for n in range(1, 11)]
LABEL_UNASSIGNED = "01"
LABEL_DEFAULT = {"01": "UNASSIGNED"}


def parse_row(row):
    """One V42 row as (sensor, meter, [(fuel position, label, triples)]).

    Returns None if it is not the right shape, which is how a Set refuses one.
    """
    if len(row) != ROW or not row[:4].isdigit():
        return None
    out, at = [], 4
    for _ in range(POSITIONS):
        fp, label, at = row[at:at + 2], row[at + 2:at + 4], at + 4
        triples = []
        for _ in range(TRIPLES):
            triples.append((row[at:at + 2], row[at + 2:at + 4],
                            row[at + 4:at + 6]))
            at += 6
        out.append((fp, label, triples))
    return row[0:2], row[2:4], out


def afm_view(rows):
    """V48: "IISSF1H1H2H3H4F2H5H6H7H8", one line per airflow meter."""
    out = []
    for row in rows:
        got = parse_row(row)
        if not got:
            continue
        ss, aa, positions = got
        line = f"{aa}{ss}"
        for fp, _label, triples in positions:
            line += fp + "".join(h for _m, h, _l in triples)
        out.append((aa, ss, line))
    return sorted(out)


def hose_view(rows):
    """V4A: "hhffggaall", one line per hose, each hose once.

    "Hoses may be used more than once. Only one Hose device is created for
    each unique hose", and the label that sticks is the one it was created
    with: "duplicate HnLn pairs are ignored if Hn is already found".
    """
    seen, out = set(), []
    for row in rows:
        got = parse_row(row)
        if not got:
            continue
        _ss, aa, positions = got
        for fp, label, triples in positions:
            for _m, hose, hose_label in triples:
                if hose in (UNASSIGNED, "00") or hose in seen:
                    continue
                seen.add(hose)
                out.append((hose, f"{hose}{fp}{label}{aa}{hose_label}"))
    return [line for _h, line in sorted(out)]


def grade_view(rows):
    """V4B: "ffaam1h1m2h2m3h3m4h4", one line per fuel position."""
    out = []
    for row in rows:
        got = parse_row(row)
        if not got:
            continue
        _ss, aa, positions = got
        for fp, _label, triples in positions:
            if fp == "00":
                continue
            out.append((fp, fp + aa + "".join(m + h for m, h, _l in triples)))
    return [line for _f, line in sorted(out)]


# ---------------------------------------------------------------------------
# The daily assessment, and how long a warning takes to become an alarm.
# ---------------------------------------------------------------------------
#
# 577013-937 Rev J p.12-32, ALARM SEQUENCE:
#
#     Each ISD monitoring test operates once each day on sensor data
#     gathered over a fixed time interval ... When a test first fails, a
#     warning is posted and a warning event is logged. If this condition
#     persists for seven more consecutive days, an alarm is posted, a
#     failure alarm event is logged and the site is shutdown.
#
# The "seven more days" of that paragraph is the Gross Pressure case, and the
# escalation is NOT uniform. Table 3 states each one against its own alarm
# name, on the alarm's own line, so this is read off the table rather than
# from the paragraph's example:
#
#     ISD VAPOR LEAKAGE FAIL     8th Consecutive Failure
#     ISD GROSS PRESSURE FAIL    8th Consecutive Failure
#     ISD DEGRD PRESSURE FAIL   31st Consecutive Failure
#     hnn: FLOW COLLECT FAIL     2nd Consecutive Failure
#     ISD SENSOR OUT FAIL        8th Consecutive Failure
#     ISD SETUP FAIL             8th Consecutive Failure
#
# The number is the day the ALARM is posted on, counting the first failing
# day as one -- "8th Consecutive Failure", not "eight days after". So a test
# at 8 warns on days one to seven and fails on the eighth.
ESCALATION = {
    "leakage": 8,           # ISD VAPOR LEAKAGE, containment
    "gross": 8,             # ISD GROSS PRESSURE, containment
    "degrade": 31,          # ISD DEGRD PRESSURE, containment
    "collect_gross": 2,     # GROSS COLLECT, collection, assist sites
    "collect_degrade": 2,   # DEGRD COLLECT, collection, assist sites
    "collect_flow": 2,      # FLOW COLLECT, collection, balance sites
    "sensor": 8,            # ISD SENSOR OUT, self-test
    "setup": 8,             # ISD SETUP, self-test
    # The three vapour processor tests, which Table 3 puts at 2 and whose
    # own pages say so twice over: "Two consecutive 1-day periods of ...
    # test failures will result in a failure alarm, failure event
    # recording, and shutdown of the site."
    "vp_pressure": 2,       # ISD VP PRESSURE / VP OVER PRESSURE
    "vp_emission": 2,       # VP EMISSION
    "vp_duty": 2,           # VP DUTY CYCLE
}

# "Time defines when 24-hour ISD tests are run and results posted. Default
# time is 11:59 PM." Figure 11, and `set.evr_start_time` already stores it.
DEFAULT_ASSESSMENT = "11:59 PM"

# The setup self-test's own criteria, 577013-819 Rev F p.17:
#
#     Setup self-test will verify: 1. That the ISD system is properly setup
#     to shutdown affected fueling point(s) as required by CP-201
#     regulations. 2. At least one tank contains gasoline. 3. At least one
#     fuel position and gas hose is setup. 4. At least one Vapor Flow Meter
#     is setup. 5. At least one Vapor Pressure Sensor is setup. 6. An
#     external input is setup if a non-TLS Console Controlled Processor is
#     installed. 7. A control relay is setup if a TLS Console Controlled
#     Processor is installed.
#
# Each failing criterion has its own alarm, and Rev F gives each of those a
# one-line definition of its own on its own page -- which is what makes them
# implementable rather than a paraphrase of the list above:
#
#   MISSING RELAY SETUP     "One or more required shutdown alarms have not
#                            been assigned to a control device."
#   MISSING TANK SETUP      "There are no vapor recovery (gasoline) tanks
#                            defined, or a gasoline pump has not been
#                            assigned to a control (shut down) device in at
#                            least one tank."
#   MISSING HOSE SETUP      "The Fuel Grade Table does not have any hoses
#                            assigned to it."
#   MISSING VAPOR FLOW MTR  "There is no Vapor Flow Meter setup or detected."
#   MISSING VAPOR PRES SEN  "There is no Vapor Pressure Sensor setup or
#                            detected."
#   MISSING VP INPUT        "An external input for the OPW and ARID vapor
#                            processor cannot be found."
#
# and the same page lists all six as COMMON CAUSES of ISD SETUP WARN, so a
# failing criterion posts its own alarm AND fails the setup test.
SETUP_ALARMS = {
    "relay": ("30", "12"),
    "hose": ("30", "13"),
    "tank": ("30", "14"),
    "flowmeter": ("30", "15"),
    "pressure": ("30", "16"),
    "vpinput": ("30", "17"),
}

# "S723nn - Smart sensor category": the two ISD cares about.
AIR_FLOW_METER = "01"
VAPOR_PRESSURE_SENSOR = "02"

# The processors that are NOT console-controlled, so criterion 6 applies:
# "An external input for the OPW and ARID vapor processor cannot be found."
EXTERNAL_INPUT_PROCESSORS = ("02", "03")

# "S80Cnn - External input type": 51 is the VAPOR PROCESSOR input.
VAPOR_PROCESSOR_INPUT = "51"

# Which shutdown alarms must be assigned to a control device before MISSING
# RELAY SETUP clears. Rev F p.18 enumerates them, and the list varies with
# what the site has:
#
#     The following ISD alarms must be assigned to the relay:
#       ISD GROSS PRESSURE FAIL / ISD DEGRD PRESSURE FAIL /
#       ISD VAPOR LEAKAGE FAIL
#     When there is a Vapor Processor installed the following ISD alarms
#     must be assigned:
#       ISD VP PRESSURE FAIL / ISD VP STATUS FAIL
#     When ISD system is configured as an EVR Balance type the following
#     HOSE alarms must be assigned:
#       FLOW COLLECT FAIL
#     When the ISD system is configured as an EVR Vacuum Assist type the
#     following HOSE alarms must be assigned:
#       GROSS COLLECT FAIL / DEGRD COLLECT FAIL
REQUIRED_SHUTDOWN = ("3003", "3005", "3007")
REQUIRED_WITH_PROCESSOR = ("3009", "3011")
REQUIRED_BALANCE = ("3106",)
REQUIRED_VACUUM = ("3102", "3104")


# ---------------------------------------------------------------------------
# Vapor Processor Monitoring: three daily tests, and the thresholds each one
# is measured against. 577013-819 Rev F pp.29-31 states all three outright.
# ---------------------------------------------------------------------------
#
# **Over-pressure**, and it is per processor type: "A VST ECS Membrane
# Processor failure occurs when the 90th percentile of 1-day's ullage
# pressure data (i.e. 10% of the pressure data) is equal to or exceeds 1" wc.
# A Veeder-Root Polisher failure occurs when the 90th percentile of 1-day's
# ullage pressure data ... is equal to or exceeds 2.3" wc."
OVER_PRESSURE_WC = {"05": 2.3}       # VEEDER-ROOT POLISHER
OVER_PRESSURE_DEFAULT_WC = 1.0       # the VST ECS membrane, and everything else

# **Emissions.** "A failure occurs when the mass emission exceeds the defined
# threshold". The number is not in the prose; it is printed in the console's
# own PASS/FAIL THRESHOLDS block, which 577013-937 Rev J renders twice in two
# different figures and both agree:
#
#     VAPOR PROCESSOR MASS EMISSION FAIL          1DAYS   ----   0.32 LBS/1KG
#     VAPOR PROCESSOR MASS EMISSION FAIL (LB/1KG) 1DAYS   ----   0.32
#
# Each carries the label, the period, the BELOW column and the ABOVE column
# on one line, so the pairing is inside the line rather than across the page.
MASS_EMISSION_LB_PER_1KG = 0.32

# **Duty cycle.** "A failure occurs when the duty cycle exceeds 18 hours
# (75%)." The console reports the percentage, so that is the form used.
DUTY_CYCLE_PERCENT = 75.0

# The alarms the three post. Category 33 is PMC's own, and its names are the
# tests' names; category 30's 08 and 09 are the ISD-side pair for the same
# condition, which is a configuration question this does not decide.
VP_ALARMS = {
    "vp_pressure": (("33", "04"), ("33", "05")),
    "vp_emission": (("33", "02"), ("33", "03")),
    "vp_duty": (("33", "06"), ("33", "07")),
}


# ---------------------------------------------------------------------------
# The controls, and the tests V85 clears.
# ---------------------------------------------------------------------------

# V85's "TT - Test Type", and the lines its printout heads them with.
SERVICE_TESTS = [
    ("01", "CONTAINMENT TESTS (GROSS AND DEGRADATION)"),
    ("02", "CONTINUOUS VAPOR LEAK DETECTION TEST"),
    ("03", "VAPOR PROCESSOR TESTS"),
    ("04", "SENSOR OUT TEST"),
    ("05", "SETUP TEST"),
    ("06", "COLLECTION TESTS"),
]
COLLECTION = "06"

# 577013-819 Rev F Table 2, "Clear Test Repair Menu": a three column map from
# each menu selection to the alarms it clears and the date it resets. The
# date half was already here -- V85 writes a stamp per test -- and the alarm
# half was not, so a technician recorded the repair and the alarm stood.
# See FIDELITY I4.
#
# The keys are `ESCALATION`'s, which are the states the console actually
# holds. Two rows of the table name an alarm this console has no state for:
# `AIRFLOW MTR SETUP` under Vapor Collection and `ISD VP STATUS` under
# Processor Status. Both are N1's subject -- a named alarm with no producer
# -- and neither can be cleared by something that cannot be raised.
# The panel's own words for the same six, which are Table 2's Menu Selection
# column and are what Figure 4 draws. `SERVICE_TESTS` is V85's REPORT
# wording -- "CONTAINMENT TESTS (GROSS AND DEGRADATION)" -- and the two are
# not interchangeable: one is 22 characters and the other is 41 on a display
# that holds 24.
CLEAR_MENU = {
    "01": "CONTAINMENT OVER PRESS",
    "02": "VAPOR LEAKAGE TEST",
    "03": "PROCESSOR STATUS TEST",
    "04": "SENSOR OUT TEST",
    "05": "SETUP TEST",
    "06": "VAPOR COLLECTION TEST",
}

CLEARS = {
    "01": ("gross", "degrade", "vp_pressure"),      # Containment Over Press
    "02": ("leakage",),                             # Vapor Leakage Test
    "03": ("vp_emission", "vp_duty"),               # Processor Status Test
    "04": ("sensor",),                              # Sensor Out Test
    "05": ("setup",),                               # Setup Test
    "06": ("collect_gross", "collect_degrade",      # Vapor Collection Test
           "collect_flow"),
}

# Which vapour COLLECTION test a site has, which is not the same question on
# the two systems. 577013-800 Rev P's Table 3 gives a VACUUM ASSIST site
# `Hnn: GROSS COLLECT WARN/FAIL` ("1-Day Gross A/L Test") and `Hnn: DEGRD
# COLLECT WARN/FAIL` ("7-Day Degradation A/L Test"); 577013-937 Rev J's
# Table 3, for a BALANCE site, has neither and carries one pair instead,
# `hnn: FLOW COLLECT WARN/FAIL`, "Vapor collection flow performance is less
# than 50%". Table 8, the Clear Test Repair menu, is the same table on both
# and clears all six under one Vapor Collection Test.
#
# The console's own default is BALANCE, so the pair it could raise belonged
# to the system it is not, and the one it is configured for had no producer
# at all. See FIDELITY I10.
COLLECTION_TESTS = {"balance": ("collect_flow",),
                    "assist": ("collect_gross", "collect_degrade")}

COLLECTION_LABEL = {"collect_gross": "GROSS COLLECT",
                    "collect_degrade": "DEGRD COLLECT",
                    "collect_flow": "FLOW COLLECT"}

# VC0's control, and the words the screen puts on it.
VP_MANUAL, VP_AUTOMATIC = "0", "1"
VP_CONTROL = {VP_MANUAL: "MANUAL", VP_AUTOMATIC: "AUTOMATIC"}
VP_RUNNING = {"0": "OFF", "1": "ON"}
VALVE = {"0": "CLOSED", "1": "OPEN"}

# VC5 reads BACKWARDS from every other flag in the section: "S - ISD shutdown
# alarms overridden, 0=Yes, 1=No". Zero is the affirmative here, where zero is
# the negative on V52, VC0, VC1 and VC8. Worth a name so it cannot be read as
# a typo and quietly "fixed".
OVERRIDDEN_YES, OVERRIDDEN_NO = "0", "1"

# "05 = Veeder-Root Polisher", which is the one VC8 works on.
POLISHER = "05"


# ---------------------------------------------------------------------------
# The status reports, V00, V0A and V0B.
# ---------------------------------------------------------------------------

# "A - Overall Status: 0=Unknown, 1=Warning, 2=Failure, 3=Pass", and the same
# four on collection, containment and the processor.
STATUS = {"0": "UNKNOWN", "1": "WARNING", "2": "FAILURE", "3": "PASS"}
UNKNOWN, WARNING, FAILURE, PASS = "0", "1", "2", "3"

# V0A and V0B encode the EVR type BACKWARDS from V4E, which sets it:
# V4E says "01=Balance, 02=Vacuum Assist" and the reports say "E - EVR Type:
# 0=Assist, 1=Balance". Same site, same setting, opposite digits, forty pages
# apart. Kept as a table rather than arithmetic so it cannot be "simplified".
EVR_REPORTED = {"01": "1", "02": "0"}

# And they enumerate the processor differently too. V40 offers eight, "05 =
# Veeder-Root Polisher", "06 = Husky Polisher" and "07 = VST Green Machine"
# among them; V0A's P field stops at "4=User Defined". The three the report
# cannot say are reported as the nearest thing it can, which is None --
# there is no digit for them.
#
# The two lists disagree on digit 3 as well. V0A's legend reads "3=ARID"
# where V40's reads "03 = HIRT Vapor Processor", and the report has no other
# digit for a HIRT, so the codes are mapped straight across and the words
# are left to each function's own table.
PROCESSOR_REPORTED = {"00": "0", "01": "1", "02": "2", "03": "3", "04": "4"}

# The CARB CP-201 numbers V00 prints. Each row is (label, period, below,
# above, units) and `only` says which sites it applies to, because the report
# is not the same on an assist site as on a balance one.
#
# 576013-635's V00 sample is a COMPOSITE and cannot be transcribed whole: it
# prints an ASSIST requirements line over a BALANCE threshold list, and it
# carries no A/L threshold row at all. The two rows that used to stand for
# the assist site were built out of the requirements line's own 0.90 and
# 1.10, which is a site's certified nozzle range and not a test threshold.
# What a real site prints is in the ISD manuals, one of each kind:
#
#   577013-800 Rev P p.48, Figure 33, a VACUUM ASSIST site --
#     VAPOR COLLECTION ASSIST SYSTEM A/L GROSS FAIL        1DAYS 0.33 1.90
#     VAPOR COLLECTION ASSIST SYSTEM A/L DEGRADATION FAIL  7DAYS 0.81 1.32
#   577013-937 Rev J p.12-54, a BALANCE site --
#     VAPOR COLLECTION BALANCE SYS FLOW PERFORMANCE        1DAYS 0.60 ----
#
# The period ABBREVIATIONS stay 635's, because 635's is the sample of the
# console's own display format: it writes "7dys", "30dys" and "20min" where
# both ISD manuals set "7DAYS", "30DAYS" and "20MINS". "1dys" is that same
# abbreviation for a period 635 does not itself print.
#
# Two figures are deliberately NOT changed here. See FIDELITY I5.
#
#   The LEAK DETECTION number is uncertain and a constant is wrong whatever
#   it is: 635 prints 13.5cfh, Rev J 12.5cfh and Rev P 8.50cfh, and
#   577013-819 Rev F p.8 makes it a function of the site -- "for a typical
#   12-hose site, that means it exceeds 8.5cfh (limit ranges over 8-10 cfh
#   for <6 to >24 hoses)". Nothing on the shelf gives the rule per hose
#   count, so 635's own figure stands.
#
#   The STAGE I percentile is a manual-against-manual conflict: 635 says
#   "75TH PERCENTILE" and both ISD manuals say "50th PERCENTILE" for the
#   same row. That belongs in UNKNOWNS, not in a silent switch.
CARB_REQUIREMENTS = [
    ("VAPOR COLLECTION ASSIST SYSTEM A/L RANGE", 0.90, 1.10, "assist"),
]
CARB_THRESHOLDS = [
    ("VAPOR COLLECTION ASSIST SYSTEM A/L GROSS FAIL",
     "1dys", "0.33", "1.90", "", "assist"),
    ("VAPOR COLLECTION ASSIST SYSTEM A/L DEGRADATION FAIL",
     "7dys", "0.81", "1.32", "", "assist"),
    ("VAPOR COLLECTION BALANCE SYS FLOW PERFORMANCE",
     "1dys", "0.60", "----", "", "balance"),
    ("VAPOR CONTAINMENT GROSS FAIL, 95TH PERCENTILE",
     "7dys", "----", "1.30", '"wcg', "any"),
    ("VAPOR CONTAINMENT DEGRADATION, 75TH PERCENTILE",
     "30dys", "----", "0.30", '"wcg', "any"),
    ('VAPOR CONTAINMENT LEAK DETECTION FAIL @2"WCG',
     "7dys", "----", "13.5", "cfh", "any"),
    ("STAGE I VAPOR TRANSFER FAIL, 75TH PERCENTILE",
     "20min", "----", "2.50", '"wcg', "any"),
]
CARB_FOOTER = ('CARB STANDARD REPORT FORMAT - CP201 APPENDIX '
               '"EVR-ISD MONTHLY STATUS REPORT"')


# V83 abbreviates the sensor type where V43 spells it out: its column reads
# "AIR FLOW" and "PRESSURE" and "HYDROCARBON" against V43's "AIR FLOW METER"
# and "PRESSURE SENSOR" and "HYDROCARBON SENSOR". Same sensors, two widths.
CALIBRATION_TYPE = {
    "AIR FLOW METER": "AIR FLOW",
    "VAPOR PRESSURE": "PRESSURE",
    "HYDROCARBON SENSOR": "HYDROCARBON",
}


# ---------------------------------------------------------------------------
# V04 to V09: ONE report, "ISD Daily Report Details", asked for six ways.
# Two axes and nothing else -- which period, and how wide the paper is:
#
#     V04 month, default      V05 days, default
#     V06 month, 132 columns  V07 days, 132 columns
#     V08 month, CCC columns  V09 days, CCC columns
#
# The computer format is identical across all six; the width only ever
# decides how much of the table the DISPLAY form prints.
# ---------------------------------------------------------------------------
DETAIL = {
    "V04": ("month", None), "V05": ("days", None),
    "V06": ("month", 132), "V07": ("days", 132),
    "V08": ("month", "ccc"), "V09": ("days", "ccc"),
}
DETAIL_DEFAULT_COLUMNS = 80
DETAIL_CCC_DEFAULT = 255           # "Default=255 [055-999]"
DETAIL_CCC_RANGE = (55, 999)

# "Status Codes: (W)Warn (F)Fail (D)Degradation (G)Gross Fail (ISD-W) ISD
# SelfTest-Warn (ISD-F) ISD SelfTest-Fail (N)No Test"
DETAIL_CODES = ("Status Codes: (W)Warn (F)Fail (D)Degradation (G)Gross Fail"
                " (ISD-W) ISD SelfTest-Warn (ISD-F) ISD SelfTest-Fail"
                " (N)No Test")
DETAIL_FOOTER = ('CARB Standard Report Format - CP201 Appendix'
                 ' "EVR-ISD Monthly Details Report"')

# "-0.01=Blkd" on every value field in the record: the number that means the
# hose was blocked rather than measured.
BLOCKED = -0.01
