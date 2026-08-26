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

# The processors V40 names. Three of them went obsolete at V28 and are kept
# because a console programmed before then still answers with one.
VAPOR_PROCESSOR = {
    "00": "NONE",
    "01": "VST VAPOR PROCESSOR",
    "02": "OPW VAPOR PROCESSOR",       # obsolete V28
    "03": "ARID VAPOR PROCESSOR",      # obsolete V28
    "04": "USER DEFINED",              # obsolete V28
    "05": "VEEDER-ROOT POLISHER",
    "06": "HUSKY POLISHER",
}

CONTROL_LEVEL = {"00": "FULL", "01": "PARTIAL", "02": "NO"}
EVR_TYPE = {"01": "BALANCE", "02": "VACUUM ASSIST"}
VACUUM_TYPE = {"01": "VAPOR VAC", "02": "WAYNE VAC"}
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

# And they enumerate the processor differently too. V40 offers seven, "05 =
# Veeder-Root Polisher" and "06 = Husky Polisher" among them; V0A's P field
# stops at "4=User Defined". The two the report cannot say are reported as
# the nearest thing it can, which is None -- there is no digit for them.
PROCESSOR_REPORTED = {"00": "0", "01": "1", "02": "2", "03": "3", "04": "4"}

# The CARB CP-201 numbers V00 prints. Each row is (label, period, below,
# above, units) and `only` says which sites it applies to, because the report
# is not the same on an assist site as on a balance one.
CARB_REQUIREMENTS = [
    ("VAPOR COLLECTION ASSIST SYSTEM A/L RANGE", 0.90, 1.10, "assist"),
]
CARB_THRESHOLDS = [
    ("VAPOR COLLECTION ASSIST SYS A/L GROSS FAIL",
     "7dys", "0.90", "1.10", "", "assist"),
    ("VAPOR COLLECTION BALANCE SYS FLOW PERFORMANCE",
     "7dys", "0.60", "----", "", "balance"),
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
