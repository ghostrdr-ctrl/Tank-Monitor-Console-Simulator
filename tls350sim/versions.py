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
"""The board in the console and the software on it.

A TLS-350, a TLS-350 PLUS and a TLS-350R are the same box. What is different
is the CPU board fitted to it and the software that board is running, and the
console tells you which it has on one screen:

    SOFTWARE REVISION LEVEL
    VERSION 327.02
    SOFTWARE# 346327-102-B

The Serial Interface Manual's notes for function code 905 take that number
apart as `346abb-Tvv-rrr`: 346 is fixed, `a` is the PLATFORM, `bb` is the
version level, `T` the software type, `vv` the language and `rrr` the
revision. The platform digit is the whole story,

    0  standard CPU, PLLD only              4  standard CPU, no PLLD or WPLLD
    1  enhanced CPU                         5  standard CPU, WPLLD only
    3  enhanced CPU, 16 tank

and the Troubleshooting Guide says the same thing from the other end
(p1-3): a TLS-350 PLUS runs 1XX software, a TLS-350R runs 3XX. So `346327-102`
is a 16 tank enhanced CPU running version 27, and the name on the front of the
box follows from that rather than the other way round.

Which features a console has is therefore a question about two things at once,
and chapter 3 of the Troubleshooting Guide answers it as a table: every TLS-350
software version from 1 (March 1992) to 34 (July 2015), and in each cell the
board types that carry that feature at that version. `versiondata.json` is that
table, parsed out of the manual rather than typed in, so:

    console.supports("csld")

is "is CSLD in this console's cell of Table 3-1..3-5", and nothing here is
invented. Features the table does not list are not gated at all; there is no
honest way to date them from the manuals in this repository, and a made-up date
is worse than none.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "versiondata.json"), encoding="utf-8") as _fh:
    DATA = json.load(_fh)

SOURCE = DATA["source"]
VERSIONS = DATA["versions"]                 # [{"version": 27, "released": "8/06"}]
BOARDS = DATA["boards"]                     # [{"code": "E7", "board": "ECPU2", ...}]
MATRIX = DATA["matrix"]                     # {feature row: {version: [board codes]}}

NUMBERS = [v["version"] for v in VERSIONS]
RELEASED = {v["version"]: v["released"] for v in VERSIONS}
LATEST = NUMBERS[-1]
OLDEST = NUMBERS[0]

BOARD = {b["code"]: b for b in BOARDS}
BOARD_CODES = [b["code"] for b in BOARDS]

# The platform digit of the software number, from the software family the
# board is running. 1XX is the 8 tank enhanced CPU, 3XX the 16 tank one.
FAMILY_PLATFORM = {"0XX": "0", "1XX": "1", "3XX": "3", "5XX": "5"}

# What the simulator gates, and the row of the manual's table that decides it.
# A slug with no row here is in every version, because no manual in this
# repository says when it arrived.
FEATURE_ROW = {
    "vlld":         "VLLD",
    "plld":         "PLLD",
    "wplld":        "WPLLD",
    "csld":         "CSLD",
    "csldmanifold": "CSLD (manifolded tanks)",
    "sitefax":      "SiteFax",
    "fuelman":      "Fuel Manager",
    "bir":          "BIR",
    "birmanifold":  "BIR (manifolded tanks)",
    "birvariance":  "BIR Variance Analysis",
    "smart":        "Mag Sensor, Vac Sensor, ATMP Sensor",
    "isd":          "ISD",
    "mt":           "Maintenance Tracker",
    "service":      "Service Notice, VCM",
    "fiscal":       "Fiscal Height Security",
    "alarmreduce":  "Alarm Reduction",
    "invthresh":    "Programmable Inventory Alarm Threshold Units",
    "waterthresh":  "Programmable Minimum Water Threshold",
    "ethanol":      "Ethanol Phase Separation",
    "apm":          "Automatic Pressure Monitoring (APM)",
    "ifsf":         "IFSF",
    "remotedisp":   "Remote Display",
    "tanks16":      "Tank 9 - 16",
}

# What each of those is, in the words the bench puts on screen.
FEATURES = {
    "vlld":         "volumetric line leak detection",
    "plld":         "pressurised line leak detection",
    "wplld":        "wireless pressurised line leak detection",
    "csld":         "Continuous Statistical Leak Detection",
    "csldmanifold": "CSLD on manifolded tanks",
    "sitefax":      "SiteFax fax/modem",
    "fuelman":      "Fuel Manager",
    "bir":          "Business Inventory Reconciliation",
    "birmanifold":  "BIR on manifolded tanks",
    "birvariance":  "BIR variance analysis",
    "smart":        "Mag, Vac and ATMP smart sensors",
    "isd":          "In-Station Diagnostics",
    "mt":           "Maintenance Tracker",
    "service":      "service notice and VCM",
    "fiscal":       "fiscal height security",
    "alarmreduce":  "alarm reduction",
    "invthresh":    "programmable inventory alarm threshold units",
    "waterthresh":  "programmable minimum water threshold",
    "ethanol":      "ethanol phase separation",
    "apm":          "automatic pressure monitoring",
    "ifsf":         "IFSF",
    "remotedisp":   "remote display",
    "tanks16":      "tanks 9 to 16",
}

# Cards the software has to know about. A card not named here is one every
# version can drive: the probe module, the liquid/vapor/groundwater sensor
# modules, external input and relay output, and the RS-232 port.
MODULE_FEATURE = {
    "modem": "sitefax", "plld": "plld", "wplld": "wplld", "vlld": "vlld",
    "smart": "smart", "mt": "mt",
}

# S-Module keys, which cannot be cut for software that has no code to unlock.
SOFTWARE_FEATURE = {
    "csld": "csld", "bir": "bir", "fuelman": "fuelman", "isd": "isd",
    "plld020": "plld", "plld010": "plld",
}

# Serial function codes that arrived with a feature and are not already
# covered by a card or a key. Older software answers 9999, because 9999 is
# exactly "a function code that it does not recognize".
TOKEN_FEATURE = {"566": "service"}

# Serial function codes the manual heads with a software version instead of a
# feature: "Function Code: 905 ... Version 15". The Troubleshooting Guide says
# the same thing from the technician's end (p1-4), send <Ctrl A> I90200 on V14
# or earlier and <Ctrl A> I90500 on V15 or later. It does not withdraw 902,
# which is a Version 1 function and stays answerable; it says which one to ask
# a console of that age for. Older software has no code for 905 at all, and a
# function code a console does not recognize gets 9999.
#
# The three at the bottom are late arrivals that Revision U of the Serial
# Interface Manual does not carry at all: they are in Revision Y, which
# reaches software 132/332/432 where Revision U stops at 129/329/429.
TOKEN_VERSION = {"905": 15,
                 "642": 31,          # Set Tank Water Alarm Filter Level
                 "132": 32,          # Fiscal Height Security Report
                 "55E": 32}          # Set Fiscal Height Security

# 905's computer format ends in twelve two-byte feature flags, notes 10 to 21,
# AA to LL in that order. LL is annotated "(Version 29)", and the nn in front
# of them is "number of 2 byte values to follow", so the count is not a
# constant: a console older than 29 sends eleven values and says eleven.
REVISION_FLAGS = [
    ("PERIODIC IN-TANK TESTS",      15),
    ("ANNUAL IN-TANK TESTS",        15),
    ("CSLD",                        15),
    ("BIR",                         15),
    ("FUEL MANAGER",                15),
    ("PRECISION PLLD",              15),
    ("TANKER LOAD",                 15),
    ("0.2 GPH PLLD",                15),
    ("PRECISION PLLD ON DEMAND",    15),
    ("SPECIAL 3-TANK/LINE CONSOLE", 15),
    ("ISD",                         15),
    ("UNUSED WAS PMC",              29),
]

# The fifty point chart is not in the manual's table, so it is not gated.
PROFILE_FEATURE = {}


def knows_token(token, version):
    """Is that function code in this console's software yet?"""
    arrived = TOKEN_VERSION.get(token)
    return arrived is None or version >= arrived


def revision_flags(version):
    """The 905 flags this console's software has, in the manual's order."""
    return [name for name, arrived in REVISION_FLAGS if version >= arrived]


def known(version):
    """Is that one of the software versions the manual lists?"""
    return version in RELEASED


def info(version, board):
    """The revision block this console prints, derived the way 905 says.

    346 a bb - T vv - rrr, where a is the platform digit of the software
    family the board is running, bb the version, T=1 "real", vv the language
    (02 = English/German, which is what the console in the photograph has)
    and rrr the revision level.
    """
    entry = BOARD.get(board) or BOARD[LATEST_BOARD]
    a = FAMILY_PLATFORM.get(entry["family"], "1")
    bb = f"{int(version):02d}"
    number = f"346{a}{bb}-102-B"
    return {
        "version": f"{a}{bb}.02",
        "number": number,
        "created": _created(version),
        "smodule": "330160-115-A",
        "board": entry,
        "released": RELEASED.get(version, ""),
    }


def _created(version):
    """CREATED - YY.MM.DD.HH.mm, from the release date the manual gives."""
    released = RELEASED.get(version)
    if not released:
        return "00.00.00.00.00"
    month, year = released.split("/")
    return f"{year}.{int(month):02d}.11.09.42"


# Boards that are the same board running the same software family and differ
# only in the memory card plugged into them.
MEMORY_KIN = [{"E3", "E3N"}, {"E5", "E6", "E7"}, {"M6", "M7"}]


def cell(feature, version, board):
    """The board types that carry that feature at that version."""
    row = FEATURE_ROW.get(feature)
    if row is None:
        return None                      # not in the table: not gated
    return MATRIX.get(row, {}).get(str(version), [])


def _reach(codes):
    """A cell's codes, widened across memory cards where it is not about them.

    The table names a memory card where the memory card is the point, and its
    footnotes say so: Maintenance Tracker "requires an NVMEM 203 card", and
    tanks 9 to 16 and BIR on manifolded tanks are listed against the NVMEM201
    board alone. Those cells hold exactly one memory-specific code and are
    taken literally.

    Everywhere else the cell names the build that shipped, CSLD at version
    33 is "E4, E7": and reading that as "an ECPU2 with an NVMEM203 cannot
    gauge a tank" is reading it harder than it was written. A cell that offers
    a choice of boards is widened to the memory variants of each.
    """
    if len(codes) < 2:
        return set(codes)
    out = set(codes)
    for code in codes:
        for kin in MEMORY_KIN:
            if code in kin:
                out |= kin
    return out


def supports(version, board, feature):
    """Is that feature in this console's cell of the manual's table?"""
    if not feature:
        return True
    codes = cell(feature, version, board)
    if codes is None:
        return True
    return board in _reach(codes)


def features(version, board):
    """Every slug this console has, for the bench to list."""
    return {f for f in FEATURE_ROW if supports(version, board, f)}


def boards_for(version):
    """The board types the manual shows anywhere in that version's column."""
    out = []
    for row in MATRIX.values():
        for code in row.get(str(version), []):
            if code not in out:
                out.append(code)
    return [c for c in BOARD_CODES if c in out]


def arrived_in(feature):
    """The first version at which any board carries that feature."""
    row = FEATURE_ROW.get(feature)
    if row is None:
        return OLDEST
    got = [int(v) for v, codes in MATRIX.get(row, {}).items() if codes]
    return min(got) if got else OLDEST


def family(board):
    """"1XX" or "3XX": which software this board is running."""
    entry = BOARD.get(board)
    return entry["family"] if entry else "1XX"


def board_name(board):
    """"ECPU2 3XX + NVMEM201", for the bench."""
    e = BOARD.get(board)
    if not e:
        return board
    return f"{e['board']} {e['family']}" + (f" + {e['memory']}" if e["memory"] else "")


# The console in the photograph: an ECPU2 with NVMEM201 running 3XX software,
# which is what makes 346327-102-B a valid number for it.
LATEST_BOARD = "E7"
