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
"""The console's state: what is installed, what is programmed, what is wrong.

Self-contained on purpose. This simulator is something you point a real tool
at, so it does not import that tool: if both read the same tables, a bug in
the tables is invisible to the test. Everything it needs was vendored into
consoledata.json.
"""
import json
import math
import os
import re
import threading
import zlib
import time

from .clock import clock_date, clock_hhmm, clock_words
from .meterid import (DEFAULT_BUS, DEFAULT_SLOT, MeterDict, MeterId,
                      legacy_map_key, legacy_offset_key,
                      meter_key, offset_key)

from . import accuchart as _accuchart
from . import alarmgroups
from . import atomicfile
from . import bir as _bir
from . import exposed
from . import csld as _csld
from . import inputs as _inputs
from . import relays as _relays
from . import sales as _sales
from . import traffic as _traffic
from . import autodial as _autodial
from . import autotx as _autotx
from . import delivery as _delivery
from . import isd
from . import leaktest
from . import sumptest
from . import shifts as _shifts
from . import pscal
from . import masks
from . import pressure as _pressure
from . import packed
from . import readings
from . import versions


# What the ATM P sensor reads. It is a DIFFERENCE -- "current atmospheric
# pressure relative to sea level" -- so it is tenths of a psi rather than the
# fourteen and a half a barometer reads, and 0.062 is the only figure any
# document here puts on it. The band is the simulator's; see UNKNOWNS A28.
ATM_BAND = (0.008, 0.180)


def leaktest_rate(key):
    """How the console writes the rate a test is looking for."""
    return {"gross": "3.0 GAL/HR", "periodic": "0.2 GAL/HR",
            "annual": "0.1 GAL/HR"}[key]


_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "consoledata.json"), encoding="utf-8") as _fh:
    DATA = json.load(_fh)

STATUS_CATEGORIES = DATA["status_categories"]
STATUS_TYPES = DATA["status_types"]

# The decimal width of every Set's data field, off the Command Format block
# of 576013-635 Rev AA and built by tools/build_field_widths.py. It is what
# makes a leading device prefix detectable in a DISPLAY format Set: the
# manual's forms are fixed width, so `II.t` is four characters and anything
# six characters long is a prefix and a value. Only the unambiguous 211 are
# in it; a code that is not there cannot be decided this way.
with open(os.path.join(_HERE, "fieldwidths.json"), encoding="utf-8") as _fh:
    DECIMAL_WIDTHS = {code: len(row[0])
                      for code, row in json.load(_fh)["width"].items()}

# The alarms a manual explicitly says clear themselves, named one at a time
# rather than assumed -- see `Console.latches`, which is where the sentences
# are. AANN, because this used to match on words in the alarm's name and the
# names are the manual's now: adopting 576013-623 Table 5-1's labels turned
# "No CSLD Idle Time Warning" into "NO CSLD IDLE TIME" and a substring test
# for "Idle Time" quietly stopped firing. A rule about which alarm it is
# should be written in the alarm's NUMBER, which no revision rewords.
SELF_CLEARING = {
    "0221",                          # No CSLD Idle Time
    "0628", "2117", "2617",          # Fuel Out, on each of the three lines
    # 576013-610 Rev AC Table 29-3 p.29-5, and it is the Action column that
    # says so. Every other row in that table tells the reader to do
    # something -- "Call for delivery", "Stop delivery", "Rerun in-tank
    # leak test" -- and these two tell the reader what the console will do:
    #
    #   TANK SIPHON BREAK | Warning | Siphon break valve has shut down
    #     manifold for tank test. | Clears when tank test completes.
    #   TANK TEST ACTIVE | Warning | In-tank leak test underway. | Do not
    #     dispense fuel from this tank until message disappears.
    #
    # The first is as explicit as the two above it. The second is an
    # implication and worth marking as one: an instruction to wait for a
    # message to go is unfollowable on a console where it does not, and
    # the operator it is addressed to has not been told to press anything.
    #
    # `siphon_break_conditions`' own docstring quotes the first sentence,
    # so this row was missed when the set was built rather than decided
    # against. See the alarm-lifecycle audit, A4 and A5.
    "0222",                          # Tank Siphon Break
    "0220",                          # Tank Test Active
}
STATUS_DEVICE_WORD = DATA["status_device_word"]
STATUS_DEVICE_CODE = DATA["status_device_code"]
DEVICE_PREFIXED = {int(x) for x in DATA["device_prefixed"]}
MULTI_DEVICE = {int(x) for x in DATA["multi_device"]}
FIELDS = DATA["fields"]
SETUP_MENU = DATA["setup_menu"]

# Which LAMP a condition lights, off 576013-610 Rev AC chapter 29's own
# `Front Panel Indicator` column and built by tools/build_alarm_indicators.py.
# It cannot be worked out from the message -- `HIGH PRODUCT ALARM` is a
# Warning, `PAPER OUT` is a Warning, and `LIQUID WARNING` is an Alarm on a
# Type B sensor and a Warning on a liquid one -- which is why five of these
# are keyed by the alarm's category as well as its name. See
# `front_panel_indicator` and CLOSED U23.
with open(os.path.join(_HERE, "alarmindicators.json"), encoding="utf-8") as _fh:
    _INDICATORS = json.load(_fh)
    ALARM_INDICATOR = _INDICATORS["indicator"]
    ALARM_INDICATOR_BY_CATEGORY = _INDICATORS["by_category"]


def front_panel_indicator(description, category=None):
    """"Warning", "alarm", or None where the manual's tables do not say.

    `category` is the two-digit alarm category, which decides the five
    messages whose lamp differs between sensor families.
    """
    name = str(description or "").strip().upper()
    if category is not None:
        cat = f"{int(category):02d}" if str(category).isdigit() else category
        found = ALARM_INDICATOR_BY_CATEGORY.get(str(cat), {}).get(name)
        if found:
            return found
    return ALARM_INDICATOR.get(name)


with open(os.path.join(_HERE, "diagdata.json"), encoding="utf-8") as _fh:
    DIAG_MENU = json.load(_fh)

with open(os.path.join(_HERE, "normaldata.json"), encoding="utf-8") as _fh:
    NORMAL_MENU = json.load(_fh)

with open(os.path.join(_HERE, "recondata.json"), encoding="utf-8") as _fh:
    RECON_MENU = json.load(_fh)


# ---------------------------------------------------------------------------
# Modules. A TLS-350 is a card cage: the console only offers the setup
# functions its installed modules can serve. The manual is explicit,
# "only the functions relevant to your console and its installed options and
# connected detection systems will be accessible in setup": so an empty cage
# should show almost nothing, and that is the behaviour worth simulating. A
# simulator that lets you program sensors with no sensor module teaches a
# habit the real console will refuse.
# ---------------------------------------------------------------------------
# Positions on one module, as each chapter's own SLOT # screen draws them:
# "SLOT #: X X X X" is four probe positions, "SLOT # - X X" is two inputs.
# Fuel Manager's week, in the console's own spelling. THR, not THU: the
# operator's manual, the setup manual and Figure 6-7 all write Thursday with
# an R, and a screen keyed THU against a reader keyed THR is a lookup that
# misses silently and reads Wednesday's number. See FIDELITY O4.
DAY_NAMES = ("SUN", "MON", "TUE", "WED", "THR", "FRI", "SAT")

SLOT_POSITIONS = {"601": 4, "701": 8, "706": 5, "711": 5, "741": 8, "746": 6,
                  "721": 8, "781": 6, "7A1": 3, "751": 4, "771": 4, "7C4": 4,
                  "801": 2, "806": 4}
# A real VLLD module carries one line and a site fits several, which this
# cage's one card per type cannot express; its four lines stand for four
# modules, so the config screen offers the same four TANK/SENSOR walks.

# Every card the cage takes, with the part number the TLS-350PLUS datasheet
# gives it and the compartment it goes in. A console has three: the
# Communication Bay behind the left door (four card cage slots), and behind
# the right door the Power Bay for the mains-side cards and the
# Intrinsically Safe Bay for anything that runs down a tank riser.
#
#   key         name                                    part number   bay      wires  max
MODULES = [
    # Every sensor family's per-console maximum is EIGHT, and three
    # independent sources say so. 577013-750 Rev AK's Console Compatibility
    # table has a "# of Modules per Console" column and every TLS-350 row
    # reads "Up to 8"; 576013-813 Rev D's I.S. bay figure says "Permissible
    # Modules (Limit 8 per console)" from the other end; and 576013-610 Rev
    # AC states each family's report limit -- 64 liquid, 64 2-wire, 40
    # groundwater, 40 vapour, 48 3-wire -- which is the card's inputs times
    # eight, five times over, in a manual written for the operator that never
    # mentions a module. The 2 and 3 that used to be here said in their own
    # comment that they were "a simulator convention rather than a sourced
    # figure". See FIDELITY M1.
    #
    # The Type B card is SIX inputs, which is what this table has always
    # called it: "You can print a report for up to 48 sensors" is six times
    # eight, and position 6 could be neither configured nor wired. M15.
    ("probe",   "Four-Input Probe Module",              "329356-002", "is",    4,  4),
    ("liquid",  "Eight-Input Liquid Sensor Module",     "329358-001", "is",    8,  8),
    ("vapor",   "Five-Input Vapor Sensor Module",       "329357-001", "is",    5,  8),
    ("gw",      "Five-Input Groundwater Sensor Module", "329399-001", "is",    5,  8),
    ("2wire",   "Eight-Input Type A Sensor Module",     "329956-001", "is",    8,  8),
    ("3wire",   "Six-Input Type B Sensor Module",       "329950-001", "is",    6,  8),
    ("smart",   "Eight-Input Smart Sensor Module",      "329356-004", "is",    8,  8),
    ("vlld",    "Line Leak Interface Module",           "",           "power", 4,  2),
    # PLLD and WPLLD are each TWO cards on a real site, and this table used to
    # carry one apiece. 576013-818 Table 6-1 gives all four identities and
    # 577013-344 draws the WPLLD chain: "E/CPU Board -> AC Controller Module
    # -> WPLLD Comm Board -> WPLLD Transducer". The keyed card in each pair is
    # the one whose INPUT count this console models -- the six transducer
    # inputs and the three wireless ones -- so the key keeps the identity that
    # owns the count, and its partner is fitted beside it.
    ("plld",    "Six-Input PLLD Interface",             "330843-001", "is",    6,  1),
    ("plldctl", "Three-Output PLLD Controller",         "330374-001", "power", 3,  1),
    ("wplld",   "WPLLD AC Interface",                   "330874-001", "power", 3,  3),
    ("wplldcom", "WPLLD Comm Board",                    "330883-001", "comm",  0,  1),
    ("io",      "Two-Input/Two-Relay Output Interface", "329360-001", "power", 2,  4),
    ("relay",   "Four-Relay Output Module",             "329359-001", "power", 4,  4),
    ("pump",    "Four-Input Pump Sense Module",         "329999-001", "power", 4,  4),
    ("pumpmon", "Pump Relay Monitor Module",            "847490-504", "power", 4,  4),
    ("rs232",   "RS-232 Interface Module",              "329362-001", "comm",  0,  3),
    # 577013-528 p.4's last slot-1-3 row: "330148-001 ... 15 K ... DB-25 (2)
    # ... Serial Port with Auxiliary Port (DB-25) for daisy chaining one or
    # more consoles". One card, one comm position, two connectors on the end
    # plate -- which is not the same thing as a dual-port module, and is why
    # it is here rather than below. See FIDELITY M8.
    ("aux",     "Serial Port with Auxiliary Port",      "330148-001", "comm",  0,  3),
    ("modem",   "SiteFax Fax/Modem Interface Module",   "330149-002", "comm",  0,  3),
    ("mt",      "RS-232 Single Port, MT ID Resistor",   "329362-005", "comm",  0,  1),
    # Two, because the console has a WARNING for a second one: "More than
    # one VMCI module is installed. The VMCI module in the higher comm port
    # must be removed", 576013-610 Rev AC Table 29-22. A console that cannot
    # hold two cannot raise it. How many more than two a bay would take is
    # not stated anywhere and does not matter: the fault is one card too
    # many, and two is one too many.
    # 331001-004 is the module's own installation guide's number, 577013-951
    # Rev A Figure 1, "VMCI Module, P/N 331001-004" -- the -004 of the LDIM
    # board family, which is what the guide says it is: "used as a RS-485,
    # 2-wire serial interface on the dispenser's VMC network". The kit is
    # 847490-473 (576047-187 Rev A). See UNKNOWNS A22.
    ("vmc",     "VMCI Interface Module",                "331001-004", "comm", 18,  2),
    # 577013-528 p.5: the dual-port slot-4 module whose RJ-45 half is the
    # Remote Display (27.4K ID); and the two DIM families of 576013-623
    # ch.17: EDIMs in the comm bay, MDIMs in the power bay, which is how
    # metered transactions reach BIR at all.
    # 577013-528's own table of comm-bay end plates, which gives a part
    # number, an ID resistance and what each card is for. The satellite
    # board is what the tape's console dials through -- its bay reads
    # `S-SAT `, `RS-485` and `MTCOMM` -- and the remote printer is the card
    # three screens across two sections were waiting on.
    # 577013-528 p.4 lists the two satellites with DIFFERENT ID resistors --
    # "329362-003 ... 332 K ... Satellite, Amoco Applications" and
    # "329362-004 ... 475 K ... Satellite, Shell Applications" -- and the ID
    # resistance is exactly how the console tells cards apart, which is Table
    # 6-1's whole stated purpose. Two resistances is two boards, and this
    # table carried a comment saying they were one. Table 6-1 names them both
    # "Serial Satellite Comm", so they are one card on every SCREEN and two
    # in the cage. See FIDELITY M10.
    ("ssat",    "Serial Satellite Comm",                "329362-004", "comm",  0,  3),
    ("asat",    "Serial Satellite Comm",                "329362-003", "comm",  0,  3),
    # 330546-001, end plate B, spare 847490-307, 267 K, DB-25: "SiteLink,
    # (Not intended for general purpose applications)", and Table 6-1's
    # "SiteLink Comm 270K" beside it. This console knew SiteLink everywhere
    # except the cage -- three diagnostic screens, the wire's own
    # `COMM BOARD : n S-LINK`, and function 885's Set SiteLink Modem Type --
    # and no card could drive any of it. See FIDELITY M8.
    ("slink",   "SiteLink Comm",                        "330546-001", "comm",  0,  3),
    # 331944-001, end plate D, spare 847490-315, the slot-4 multiport whose
    # RJ-45 half is 82.5 K: "This multiport module provides serial
    # communications at service stations running In-Station Diagnostics that
    # require a RS-485 port." It is Table 6-1's `ISD Comm 82.5K`, under a
    # name no search for RS-485 could find, and it is what the tape's console
    # has in comm 5. See FIDELITY M5.
    ("rs485",   "RS-485 Multiport Interface",           "331944-001", "comm",  0,  1),
    ("rprinter", "Remote Printer Interface",            "330000-001", "comm",  0,  1),
    # The dual-port modules of 577013-528 p.5, each of which takes ONE slot
    # and answers on TWO comm positions. `rdu` and `rs485` were already here
    # as one position apiece, which is what M7's slot model is for; these
    # four are the rest of that table. Their halves, ID resistances and
    # what each is for are in COMM_DUAL below.
    ("rdu",     "Remote Display Interface",             "330586-011", "comm",  0,  1),
    ("dual",    "Dual-Port Serial Interface",           "330586-001", "comm",  0,  2),
    ("asat4",   "Serial Satellite Comm, Dual Port",     "330586-015", "comm",  0,  2),
    ("ssat4",   "Serial Satellite Comm, Dual Port",     "330586-016", "comm",  0,  2),
    ("mt4",     "Maintenance Tracker, Dual Port",       "330586-017", "comm",  0,  1),
    ("edim",    "Electronic Dispenser Interface",       "330280-001", "comm",  0,  3),
    ("mdim",    "Mechanical Dispenser Interface",       "331001-003", "power", 0,  3),
    # The Universal Sensor Module is REAL in the firmware and was apparently
    # never stocked: it has an ID resistance (30.1K in Table 6-1), a module
    # type code (11 in function 102), an alarm category (13), a device code
    # (U) and a full serial API at 34B, 34C, 74B-74E and B4B, all tagged
    # Version 4 -- and no part number, no setup chapter and no entry in any
    # Veeder-Root catalogue, with the TLS-450 manual marking the whole feature
    # OBSOLETE. So: fittable, because the console's program plainly knows how
    # to talk to one, and absent from every preset, because nobody could buy
    # it. See UNKNOWNS C3.
    #
    # The five inputs are INFERRED, not stated, and the inference is this:
    # Table 29-1 of the Operator's Manual gives the device code and calls it
    # "Universal 3-wire", which is the only statement anywhere of the card's
    # electrical form. Every 3-wire card here carries five (vapor, ground
    # water, and the Type B, which Veeder-Root sells as "Six-Input" while its
    # setup screen draws five positions); eight is what the 2-wire cards
    # carry. This entry said eight until that device code turned up, which
    # was the one number the evidence argued against. The bay is inferred the
    # same way: every sensor interface module Veeder-Root lists sits in the
    # Low Power compartment. The max of three is the convention this table
    # already follows -- every five-input card here takes three and every
    # eight-input card takes two -- and it is a simulator convention rather
    # than a sourced figure, for this module and for all the others.
    #
    # Do NOT paste in the TLS-450's Universal Sensor Module (332812-001, 16
    # inputs). Despite the name it is a different card on a different console.
    ("universal", "Universal Sensor Module",             "",           "is",    5,  3),
]

# The S-Module features, which are software keys rather than cards: the
# datasheet's own Software Enhancement Modules, plus the line leak test keys
# the setup manual mentions ("this message will not appear unless the 0.20
# Repetitive PLLD software module key is installed in your system").
# The part numbers are from 330160-000 Rev V, "GROUP - SEM / TLS-350", a
# four-sheet D-size drawing on the shelf whose every page is vector outlines
# with NO text layer -- so it extracts to nothing, no grep has ever reached
# it, and nobody here had read it until 2026-09-17. Sheet 2 carries the
# legend, "EXPLANATION OF VARIATIONS AND SEM GROUP PART NUMBER":
#
#     0XX without BIR, without VLD      XX2  CS     CSLD for manifold tanks
#     1XX with BIR, without VLD         XX3  FL     Fuel Management Reorder
#     2XX without BIR, with VLD         XX4  ISD    In-Station Diagnostics
#     3XX with BIR, with VLD            X1X  P1C2C  PLLD .1 and .2 continuous
#                                       X2X  TLC    Tanker Loading Control
#                                       X5X  WP1D   WPLLD and PLLD .1 on-demand
#                                       X6X  WP1D2C WPLLD and PLLD .1 on-demand
#                                                   and .2 continuous
#
# **The number is POSITIONAL, not per-feature**: one SEM part number encodes
# a whole feature SET, and the units digit carries a SUBSET (5 is CS FL, 9 is
# CS FL ISD). So a column of one part number per feature is a category error
# wherever the drawing has no standalone bundle for that feature, and three
# of the numbers here were wrong in exactly that way:
#
# * `330160-010` is P1C2C, PLLD .1 and .2 CONTINUOUS -- it was labelled
#   "0.10 On Demand", which is the opposite mode. On-demand is X5X.
# * `330160-050` is WP1D, WPLLD and PLLD .1 ON-DEMAND -- it was labelled
#   "0.20 Repetitive", and .2 continuous is the X1X/X6X bundles.
# * `330160-005` is the units-digit subset CS FL, CSLD plus Fuel Management.
#   **PMC does not appear anywhere in the drawing**, so it had another
#   feature's number.
#
# The deeper mismatch is left alone deliberately. These KEYS are function
# 102's decomposition of the feature space -- "PRECISION PLLD", "0.2 GPH
# PLLD", "PRECISION PLLD ON DEMAND", which is what `licensed()` gates and
# what the console reports -- and the drawing's is a different cut of the
# same space, by SEM bundle. No SEM sells 0.1 gph PLLD by itself, so the two
# PLLD rows and PMC get no part number rather than a plausible one. BIR does
# have one, and now has it: sheet 2's first row is `330160-100 WITH BIR, NO
# VLD`. See UNKNOWNS A77.
SOFTWARE_MODULES = [
    ("csld",    "Continuous Statistical Leak Detection", "330160-002"),
    ("fuelman", "Fuel Manager",                          "330160-003"),
    ("bir",     "Business Inventory Reconciliation",     "330160-100"),
    ("plld020", "0.20 Repetitive PLLD/WPLLD",            ""),
    ("plld010", "0.10 On Demand PLLD/WPLLD",             ""),
    ("isd",     "In-Station Diagnostics",                "330160-004"),
    # Pressure Management Control is its own feature and its own key, which
    # 576013-635 is careful about all through section 7.7: some ISD functions
    # say "PMC feature required", some say "ISD feature required", V47 says
    # "ISD or PMC" and V50 says "ISD and PMC". A console with one and not the
    # other answers a different set of codes, so they cannot be one flag.
    ("pmc",     "Pressure Management Control",           ""),
]
SOFTWARE_NAME = {k: name for k, name, _p in SOFTWARE_MODULES}
SOFTWARE_PART = {k: part for k, _n, part in SOFTWARE_MODULES}

# The card cage slots each compartment has. The datasheet's interface module
# tables are each headed "Limit 8 per console".
#
# The communication bay is SIX, and it is six POSITIONS across four physical
# slots. 576013-818 Figure 2-2's caption reads "Card Cage Slots in
# Communication Bay: 1 2 3 4*", with an asterisk on slot 4 alone, and
# 577013-528 says what the asterisk is for: slots 1 to 3 take single-port
# modules and slot 4 takes DUAL-port ones, "and two dual-port modules can be
# installed in slots 3 and 4 by using a double dual-port wiring harness (P/N
# 332609-001)". Four slots, up to six ports. Function 102's own sample
# printout ends at `COMM 6 UNUSED` and Figure 6-2's SYSTEM DIAGNOSTIC walk
# ends at the same screen, so the console addresses six either way -- and a
# real console's bay numbered `1, 5, 6`, which is what the tape shows, is
# only reachable with six. See FIDELITY M6, and M7 for the slot model this
# still does not have.
BAY_SLOTS = {"comm": 6, "power": 8, "is": 8, "sw": 1}
BAY_NAME = {"comm": "Communication Bay", "power": "Power Bay",
            "is": "Intrinsically Safe Bay", "sw": "Software Module"}

# ---- the communication bay's four slots ------------------------------------
#
# Six positions (BAY_SLOTS above) is what the console ANSWERS on; four is what
# a card goes into. 577013-528 Rev G heads its two tables "Comm Modules That
# Can Be Installed In Slots 1, 2, or 3" and "... In Slot 4", and 576013-818
# Figure 2-2's caption puts the asterisk on slot 4 alone. Slots 1 to 3 take
# single-port modules; slot 4 takes a DUAL-port one; and "two dual-port
# modules can be installed in slots 3 and 4 by using a double dual-port wiring
# harness (P/N 332609-001)". Two singles and two duals is four cards and six
# ports, which is exactly the six rows function 102 prints. See FIDELITY M7.
COMM_SLOTS = 4

# Which position each slot answers on, (first, second). 577013-819 Rev F p.37
# is the one document on this shelf that states a number, and it states the
# hard one: of the COMM BOARD line on a port settings printout, "This number
# is the assigned by the console and indicates the slot in which the RS-232
# module is installed. It could be 1, 2, or 3. However, for the RS-232 port of
# a Multiport module, which is installed in slot 4, this number would be 6."
#
# So a single-port card answers on its own slot number, and the DB-9 serial
# half of a slot-4 module answers on 6 -- which leaves 5 for the RJ-45 half
# beside it, and 3 and 4 for a dual module in slot 3. That is what makes a bay
# numbered `1, 5, 6` -- the tape's own bay, and the reason this file went
# looking -- a single-port card in slot 1 and one dual-port module in slot 4.
# The slot-3 pair and RJ-45-before-DB-9 are symmetry rather than a page: see
# UNKNOWNS A25.
COMM_PORTS = {1: (1, None), 2: (2, None), 3: (3, 4), 4: (5, 6)}

# The name the COMM BOARD screens print for a single-port card. A card not
# named here is not a port a technician sets up -- the EDIM, the WPLLD comm
# board -- and its position prints UNUSED on those screens while still
# reading its own ID resistance on SYSTEM CONFIGURATION. See FIDELITY T8.
#
# Not six wide, every one of them. This comment said so and three of the
# ten are not, and the satellite's is the one that matters: it is `S-SAT `
# WITH a trailing space, which is a character of the name and not a field
# being padded. 576013-635 Rev AA prints `COMM BOARD  : 1 (S-SAT )` twice
# and `tests/console_capture/raw/I88800.bin` is a real console printing
# `COMM BOARD  : 2 (S-SAT )`, both with the space inside the brackets --
# while the same manual prints `(FXMOD)`, five characters, six times over,
# once of them on the very page whose block this console reproduces line for
# line. So padding every name to six is wrong on the page that draws the
# most of them, and the space belongs here instead. The tape's `(RS-485)`
# and `(MTCOMM)` are six already and say nothing either way.
# See FIDELITY S18.
COMM_NAME = {"rs232": "RS-232", "modem": "FXMOD", "mt": "MTCOMM",
             "vmc": "VMCI", "ssat": "S-SAT ", "asat": "S-SAT ",
             "slink": "S-LINK", "rs485": "RS-485", "rprinter": "PRINTR",
             "aux": "RS-232"}

# The dual-port modules of 577013-528 Rev G p.5, per half: the six-character
# name the COMM BOARD screens print (None for a position that is not a port
# anybody sets baud rate on), the name a slot line prints, the ID resistance,
# and the card the console reads that position AS. A dual module's DB-9 serial
# half IS an RS-232 port -- 577013-819's own procedure plugs a laptop into it
# -- which is what "Serial port for consoles requiring more than 3 Comm
# modules" means.
#
# Every RJ-45 half prints RS-485, and that is the tape's reading rather than a
# guess: its slot-4 module answers `RS-485` on 5 and `MTCOMM` on 6, the only
# 402K DB-9 Maintenance Tracker port in the catalogue is 330586-017's, and
# that card's RJ-45 half is a 15K general-purpose one. So the console names
# that side by its connector -- RJ-45 on a dual module is the RS-485 side --
# and not by the resistor it reads there.
COMM_DUAL = {
    # 27.4K RJ-45 Remote Display + 15K DB-9 serial. The remote display is not
    # a port with settings, so its position prints UNUSED on the COMM BOARD
    # screens, which is what this console has always done with the card.
    "rdu":   ((None,     "REMOTE DISP",  27400, "rdu"),
              ("RS-232", "RS-232",       15000, "rs232")),
    # 82.5K RJ-45 "requires a RS-485 port" for ISD + 15K DB-9 serial
    "rs485": (("RS-485", "RS-485",       82500, "rs485"),
              ("RS-232", "RS-232",       15000, "rs232")),
    # 68.1K RJ-45 "Not intended for general purpose applications" + 15K DB-9.
    # Table 6-1 has three rows at 68K and none of them is this, so the slot
    # line carries the connector's name rather than a borrowed one.
    "dual":  (("RS-485", "RS-485",       68100, None),
              ("RS-232", "RS-232",       15000, "rs232")),
    "asat4": (("RS-485", "RS-485",       68100, None),
              ("S-SAT",  "S-SAT COMM",  332000, "asat")),
    "ssat4": (("RS-485", "RS-485",       68100, None),
              ("S-SAT",  "S-SAT COMM",  475000, "ssat")),
    # 15K RJ-45 "Serial port for general TLS use" + 402K DB-9 "Serial port for
    # Maintenance Tracker". Table 6-1's own row is "Maintenance Tracker
    # (Single and Dual Port) 402K", which names both cards at once.
    "mt4":   (("RS-485", "RS-485",       15000, "rs232"),
              ("MTCOMM", "MT COMM",     402000, "mt")),
}

# Every card a comm position can read AS, which is what `count` answers for.
COMM_IDENTITIES = set(COMM_NAME) | {"rdu", "edim", "wplldcom"} | {
    half[3] for card in COMM_DUAL.values() for half in card if half[3]}

# Table 6-1 of the Troubleshooting Guide, "Console Modules - ID Resistances",
# in ohms. "Table 6-1 contains nominal resistance values used to identify
# TLS-350 Modules. The actual or measured resistance will differ slightly from
# the nominal value", which is what the two columns on the SYSTEM
# CONFIGURATION screen are for: "POR = ID resistor value of module in this
# slot read at last system reset. C = current ID resistor value."
#
# The manual's own sample of function 102 shows what "differ slightly" means:
# a nominal 160K reading 164040 at reset and 166912 now, a 15K reading 14764
# and 14753, a 47K reading 47008 and 47006. Tenths of a percent, both ways.
#
# One row of Table 6-1 is doubtful and is marked below: Vapor Sensor prints as
# 15K, which breaks the table's ascending sort and collides with the RS-232
# module. It is printed that way in every revision.
# The smart sensor family is TWO cards, and this cage had one of them.
# 577013-750 Rev AK p.49's secondary containment compatibility table lists
# both against the TLS-350: "0329356-004 | 8 Input Smart Sensor Interface
# Module" and "0332250-001 | 7 Input Smart Sensor/Pressure Module | 1 for up
# to 7 Vac Sensors". 576013-623 Rev AN p.26-1 says the console takes either
# -- "When Smart Sensor Interface Modules (8 inputs) or Smart Sensor / Press
# Modules (7 inputs) are installed, the system recognizes the presence and
# module slot locations of each one" -- and p.26-3 says what the eighth
# channel is doing on the smaller one: "the atmospheric pressure [ATMP]
# sensor is resident in the Smart Sensor / Press Module. One ATMP sensor is
# required with Vac Sensor systems per site." 576013-818 Table 6-1 gives it
# its own ID resistance, "Smart Sensor / Press Module 499K", beside the
# eight-input card's 39.2K.
#
# It is a VARIANT rather than a second cage key: one console fits one kind,
# the sensor numbering is the card's positions, and a cage holding both at
# once would number its sensors in two different arithmetics. See FIDELITY M2.
SMART_PRESS = {"label": "Seven-Input Smart Sensor/Pressure Module",
               "part": "332250-001", "wires": 7, "ohms": 499000,
               # "2B=SmartSensor(7) Module" against the eight-input card's
               # "28=SmartSensor(8) Module", in function 102's own "TT - Type
               # of Module (Hex)" list. The variant reported 28, which is the
               # other card.
               "type": "2B",
               # ...and no page draws either smart sensor card's slot line,
               # so this one has no name of its own and falls back to the
               # family's. Inventing `SMART/PRESS` would be drawing a screen
               # the hardware may not have.
               "short": None, "paper": None}

# ...and the probe family is two cards for the same reason, and the two are
# neighbours in that same list: "0A=Four Probe w/ Ground Temp Module"
# against "01=Four Probe Module".
#
# 576013-879 Rev W p.60 is the card itself, Figure 58, headed `PROBE /
# THERMISTOR INTERFACE MODULE - I.S. BAY` and drawn `PROBE 1 2 3 4` over
# `THERMISTOR 1 2 3 4`. Its connection table gives the second row: "Ground
# temperature thermistor - When using volumetric line leak detection (VLLD),
# only one ground temperature thermistor is needed per site and the
# thermistor must be wired to thermistor position number 1 (positions 2 - 4
# are not used)."
#
# Table 6-1 gives it its own resistor, `4 Probe w/Temp Interface 160K`,
# beside the plain card's `4 Probe 2K` -- and 160K is the nominal the
# comment above uses as its example of "differ slightly", because the sample
# it read that from is THIS card's slot: `1  4 PROBE / G.T.  164040
# 166912`. The console quoted the card's numbers for years without having
# the card. A real console says the same: the capture in `tests/
# console_capture` prints `1  4 PROBE / G.T.  164313  164181`.
#
# A VARIANT rather than a second cage key, for M2's reason and one more of
# its own: some forty serial codes and three setup functions gate on
# `has("probe")`, and a console whose only probe card was a second key would
# refuse every one of them. Both cards carry four probes, so the numbering
# objection M2 had does not arise here -- what differs is the four
# thermistor positions beside them, which are not probes.
#
# **The shelf names this card four ways and numbers it none**:
# `Probe/Thermistor Interface Module` (576013-879), `4 Probe w/Temp
# Interface` (Table 6-1), `Four Probe w/ Ground Temp Module` (635's type
# list) and `Four-Input Probe/Thermistor Module` (818's version note). The
# number is in none of them, and came from a tenth document fetched for it:
# 576013-632 Rev C, the module's own ten-page installation guide, whose
# Introduction opens "This manual contains instructions for installing the
# Veeder-Root Probe/Thermistor Interface Module (P/N 847490-104) in
# TLS-350/ProMax/EMC Consoles." The prefix is one this cage already uses --
# the Pump Relay Monitor is 847490-504.
#
# *Veeder-Root's own catalogue disagrees with its own manual*, and UNKNOWNS
# A74 records it rather than choosing: the end-of-sale list sells one
# four-probe board, "0329356-002 - Module, Four-Input Probe Interface ...
# Incl. terminal connect. for 1 ground temp thermistor for volumetric line
# leak detector". Read that way the two type codes are two ID resistors on
# one board rather than two boards, which is a thing the ID column can say
# and the part number cannot.
PROBE_GT = {"label": "Four-Input Probe/Thermistor Interface Module",
            "part": "847490-104", "wires": 4, "ohms": 160000, "type": "0A",
            # the glass and the paper spell it differently; see `slot_name`
            "short": "4 PROBE/ G. T.", "paper": "4 PROBE / G.T."}

MODULE_OHMS = {
    "probe": 2000,        # "4 Probe 2K"
    "io": 10000,          # "I/O Combo 10K"
    "relay": 15000,       # "4 Relay Output Interface 15K"
    "rs232": 15000,       # "RS232 Serial Interface 15K"
    "vapor": 15000,       # "Vapor Sensor 15K" - see the note above
    "3wire": 20000,       # "Type B Sensor Interface 20K"
    "pump": 33000,        # "Pump Sense 33K"
    "smart": 39200,       # "8-Input Smart Sensor 39.2K"
    "modem": 47000,       # "SiteFax Modem (new) 47K"
    "vlld": 47000,        # "VLLD Interface 47K"
    "universal": 30100,   # "Universal Sensor 30.1K", Table 6-1
    # 577013-528's table, which is a clean one and agrees with Table 6-1 on
    # both: "330000-001 ... 33 K ... Remote Printer" and "329362-004 ...
    # 475 K ... Satellite, Shell Applications". The Amoco satellite is
    # 329362-003 at 332K and is the same board to the console.
    "rprinter": 33000,
    "ssat": 475000,
    "2wire": 68000,       # "Type A Sensor Interface 68K"
    # Table 6-1, read off a rendered page rather than an extraction -- its
    # value column offsets by a row in plain text, which is the trap section D
    # of UNKNOWNS records. "PLLD Sensor 3.9K", "PLLD Controller 100K",
    # "WPLLD AC Interface 162K", "WPLLD Comm 200K". The 200K this file used to
    # give the power-bay WPLLD card is the COMM board's, which sits in the
    # communication bay: the card beside the STPs is the 162K AC Interface.
    "plld": 3900,         # "PLLD Sensor 3.9K", the six-input interface
    "plldctl": 100000,    # "PLLD Controller 100K"
    "wplld": 162000,      # "WPLLD AC Interface 162K"
    "wplldcom": 200000,   # "WPLLD Comm 200K"
    "liquid": 200000,     # "Interstitial/Liquid Sensor Interface 200K"
    "gw": 270000,         # "Groundwater Sensor 270K"
    "mt": 402000,         # "Maintenance Tracker (Single and Dual Port) 402K"
    "rs485": 82500,       # "ISD Comm 82.5K", which is 331944-001's RJ-45 half
    "slink": 267000,      # 577013-528's own figure; Table 6-1 rounds it to 270K
    "asat": 332000,       # "329362-003 ... 332 K ... Satellite, Amoco"
    # 577013-528 p.5 gives the slot-4 dual-port module's two halves, and the
    # Remote Display is the RJ-45 one: "27.4 K ... RJ-45 ... Remote Display
    # (Remote Display must be connected)". Table 6-1 rounds it to 27K. Both
    # of the next two are Table 6-1 rows as well, and all three were reaching
    # the undocumented fallback -- so the SYSTEM CONFIGURATION screen, which
    # exists to show a card's ID drifting from its power-on value, was
    # drifting around an invented centre for them. See FIDELITY M9.
    "rdu": 27400,         # "Remote Display Interface 27K"
    # The rest of 577013-528 p.4 and p.5. A dual-port module has an ID
    # resistance PER HALF and COMM_DUAL carries both; the value here is the
    # one its RJ-45 side reads, for anything that asks about the card rather
    # than the position. `aux` is a single-port card and has just the one.
    "aux": 15000,         # "330148-001 ... 15 K ... DB-25 (2)"
    "dual": 68100,        # "330586-001 ... 68.1 K ... RJ-45"
    "asat4": 68100,       # "330586-015 ... 68.1 K ... RJ-45"
    "ssat4": 68100,       # "330586-016 ... 68.1 K ... RJ-45"
    "mt4": 15000,         # "330586-017 ... 15 K ... RJ-45"
    "mdim": 68000,        # "Mechanical Dim 68K"
    "edim": 100000,       # "Dispenser Interface Module 100K", and function
                          # 102's own sample row `ELEC DISP INT. 100725`
    # The VMCI Interface Module is real -- 576013-610 chapters 26 and 27,
    # device code X, alarm category 35, its own history report, and now its
    # own installation guide, 577013-951 Rev A, which gives it a part number
    # (331001-004) and no ID resistance: the only ohms in it are the data
    # cable's. It is in no ID resistance table on this shelf either -- Table
    # 6-1 has no VMCI row and no LDIM row for its family to borrow from.
    # 82.5K is the row this console borrowed for it, and that row now has an
    # owner: the RS-485 multiport above. Table 6-1 gives one resistance to
    # more than one card in a dozen places, so a shared value is not itself
    # a fault; this one is still a guess and is marked as one. UNKNOWNS A22.
    "vmc": 82500,
    "pumpmon": 33000,     # not in Table 6-1; the pump sense row is its family
}

# What an empty slot reads: the same rail in every bay. `Console.
# EMPTY_READING` carries the evidence and the history; this stays a table
# because the three bays are three questions and the day one of them answers
# differently is the day this needs to be one again.
EMPTY_OHMS = {"is": 15000000, "power": 15000000, "comm": 15000000}

# what a slot screen calls each card: "SLOT 1 4 PROBE/ G. T." is the
# manual's own shorthand, and 24 characters is all there is
MODULE_SHORT = {
    "probe": "4 PROBE", "liquid": "8 LIQUID", "vapor": "5 VAPOR",
    "gw": "5 GRND WATER", "2wire": "8 TYPE A", "3wire": "5 TYPE B",
    "smart": "8 SMART", "vlld": "LINE LEAK",
    # Table 6-1's own four names for the two pairs, shortened to the width a
    # slot line has. The keyed card of each pair is the interface, so "PLLD
    # CNTRL" now belongs to the controller beside it rather than to this one.
    "plld": "6 PLLD SENSOR", "plldctl": "PLLD CNTRL",
    "wplld": "WPLLD AC INT", "wplldcom": "WPLLD COMM",
    "io": "2 IN/2 RELAY", "relay": "4 RELAY",
    "pump": "4 PUMP SENSE", "pumpmon": "PUMP RELAY", "rs232": "RS-232",
    "rdu": "REMOTE DISP", "edim": "EDIM", "mdim": "MDIM",
    "modem": "SITEFAX", "mt": "MT COMM", "vmc": "VMCI",
    # Table 6-1 names both satellites "Serial Satellite Comm", so the two
    # boards read the same on a slot line and differ by their ID resistance,
    # which is what a slot line is for.
    "ssat": "S-SAT COMM", "asat": "S-SAT COMM",
    "slink": "S-LINK COMM", "rs485": "RS-485",
    "rprinter": "REMOTE PRINT",
    # A dual-port module's slot lines are its halves', in COMM_DUAL; these
    # are what names the CARD, on the bench and anywhere a whole card is
    # asked about.
    "aux": "RS-232 AUX", "dual": "DUAL PORT",
    "asat4": "S-SAT COMM", "ssat4": "S-SAT COMM", "mt4": "MT COMM",
}

# ...and what function 102's PAPER calls a card, which is a different
# vocabulary and not a shortening of the same one.
#
# `MODULE_SHORT` above is Table 6-1's names cut to the glass's width, and
# Table 6-1 is the ID RESISTANCE table -- it names cards for a technician
# holding a meter. The report has its own list, and two sources agree on it
# against this console: function 102's own sample printout in 576013-635
# Rev AA p.49, and the captured console in `tests/console_capture`, which
# are eleven years and one continent apart and print the same strings.
#
#     the sample        1  4 PROBE / G.T.     9  4 INPUT BOARD
#                       COMM 1 FAXMODEM  BOARD     COMM 3 ELEC DISP INT.
#                       COMM 2 RS232 SERIAL BD
#     the capture       1  4 PROBE / G.T.     2  PLLD SENSOR BD
#                       9  PLLD POWER BD      COMM 1 RS232 SERIAL BD
#                       COMM 2 SERIAL SAT BD
#
# Note `BD` for a board and the two PLLD cards named by what they DO --
# sensor and power -- where Table 6-1 names them by what they ARE.
#
# **Only attested names go in here.** Most of the cage appears in no sample
# at all, and a card with no entry keeps the name the screen uses, because
# the screen's is at least a name a manual draws. Guessing `8 LIQUID BD`
# from the pattern would be inventing a line on the one report a technician
# reads to find out what is in the console. FIDELITY M20, and the ones
# still missing are named there.
# Each of these is attested twice over: the STRING is printed in one of the
# two samples, and the two ID resistances beside it reproduce, to within the
# tolerance `module_id_resistance` already models, the Table 6-1 nominal of
# the card this console maps that name to. A name and a resistance agreeing
# is what identifies the card, because the resistance is the only field on
# that report whose meaning does not depend on reading the name.
#
#     PLLD SENSOR BD    3878 against  "PLLD Sensor 3.9K"        1.0057
#     PLLD POWER BD   100848 against  "PLLD Controller 100K"    0.9916
#     RS232 SERIAL BD  15051 against  "RS232 Serial Interface"  0.9966
#     SERIAL SAT BD   482940 against  "Serial Satellite 475K"   0.9836
#     FAXMODEM  BOARD  47008 against  "SiteFax Modem (new) 47K" 0.9998
#     ELEC DISP INT.  100725 against  "Dispenser Interface 100K" 0.9928
#
# The satellite is the SERIAL one and not the Amoco one, which the name
# cannot tell you and the resistor can: 482940 is 1.017 of the Serial
# Satellite's 475K and 1.45 of the Amoco board's 332K.
#
# Note `FAXMODEM  BOARD` carries TWO spaces, and `ELEC DISP INT.` a full
# stop. They are transcribed rather than tidied: a report is what it prints.
MODULE_PAPER = {
    "plld": "PLLD SENSOR BD",        # the capture, I.S. slot 2
    "plldctl": "PLLD POWER BD",      # the capture, power slot 9
    "rs232": "RS232 SERIAL BD",      # the capture AND p.49's own sample
    "ssat": "SERIAL SAT BD",         # the capture, comm 2
    "modem": "FAXMODEM  BOARD",      # p.49's sample, comm 1
    "edim": "ELEC DISP INT.",        # p.49's sample, comm 3
}

# A tank lying on its side is not a wedge, and the manuals' own examples say
# so. 576013-610 Rev AC works two of them on a 10,000 gallon, 96 inch tank:
# 9038 gallons stands 81.37 inches deep and 8518 stands 76.26. Straight-line
# arithmetic gives 86.76 and 81.77, out by more than five inches at the levels
# a site actually runs at, and height is the number a technician checks
# against a dip stick. The circle reproduces both to a hundredth of an inch.
#
# This is what an uncharted tank falls back on. A tank with its own chart
# points still interpolates between them, because a real strapping chart
# beats any formula.
# What a ground temperature thermistor reads, in ohms. 576013-818 Rev AB
# Figure 6-22 gives three points on the same page as the screen -- 26100 at
# 40 F, 11880 at 70 F, 5820 at 100 F -- and warns that under 1000 means the
# thermistor may be shorted and over 200000 that it may be open.
#
# Three points want a three-point model. This was a two-parameter beta
# fit pinned to the two ENDS, which reproduced the middle to within 0.6% --
# 11812 against 11880, sixty-eight ohms out. The defence written here was
# that 0.6% is "closer than the screen prints", and that is true and is not
# the same thing as reproducing the figure. A manual gives three points
# because two do not determine the curve. See FIDELITY X7.
#
# The model is `ln R = A + B/T + C/T^2` in kelvin, which is the standard
# three-point thermistor curve -- Steinhart-Hart written the way round that
# gives resistance FROM temperature, so nothing has to be inverted. Solved
# once, by hand, through the figure's own three pairs; it passes through all
# three exactly, it decreases across the whole -40 to 140 F range the clamp
# allows, and it crosses the figure's two fault limits at -26 F and 188 F,
# so a healthy thermistor never reads as shorted or open.
GROUND_A = -5.615958193304632
GROUND_B = 4937.939536502947
GROUND_C = -154323.3935668349


def _diag_row(label, value):
    """A diagnostic screen's label left and its value hard right.

    The same rule D5 settled for AccuChart, and for the same reason: the
    display is 24 characters and 576013-818's figures draw the value against
    the right of it. Figure 6-11's own artwork is set in a proportional font
    so a column cannot be counted off it exactly -- what it does show, by
    the width of the gap, is that these two values are pushed a long way
    right of their labels rather than following them. See FIDELITY D6.
    """
    value = str(value)
    return f"{label}{value:>{max(1, 24 - len(label))}}"[:24]


def ground_ohms(fahrenheit):
    """The resistance a healthy ground thermistor shows at that temperature."""
    def kelvin(f):
        return (f - 32.0) * 5.0 / 9.0 + 273.15
    warm = max(-40.0, min(140.0, fahrenheit))
    x = 1.0 / kelvin(warm)
    return math.exp(GROUND_A + GROUND_B * x + GROUND_C * x * x)


def cylinder_part(depth):
    """How full a horizontal cylinder is, as a fraction, at depth/diameter."""
    depth = max(0.0, min(1.0, depth))
    return ((math.acos(1.0 - 2.0 * depth)
             - (1.0 - 2.0 * depth) * math.sqrt(max(0.0, 4.0 * depth
                                                   * (1.0 - depth))))
            / math.pi)


def cylinder_depth(part):
    """The inverse: depth/diameter at that fraction full.

    There is no closed form, so it is bisected. Forty passes puts it well
    inside the hundredth of an inch a console prints.
    """
    part = max(0.0, min(1.0, part))
    low, high = 0.0, 1.0
    for _pass in range(40):
        mid = (low + high) / 2.0
        if cylinder_part(mid) < part:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


SECONDS_IN_STAMP = re.compile(r"(\d{1,2}:\d{2}):\d{2}")

MODULE_PART = {k: part for k, _n, part, _b, _w, _m in MODULES}
MODULE_BAY = {k: bay for k, _n, _p, bay, _w, _m in MODULES}
MODULE_WIRES = {k: wires for k, _n, _p, _b, wires, _m in MODULES}
MODULE_MAX = {k: most for k, _n, _p, _b, _w, most in MODULES}
MODULE_LABEL = {k: name for k, name, _p, _b, _w, _m in MODULES}
# how many devices ONE of these modules carries: the wires on it
MODULE_CAPACITY = MODULE_WIRES

# Which setup FUNCTIONS each module unlocks. Names are the console's own, from
# the manual's Setup Mode Table.
# Functions a software module licenses rather than a card
FUNCTION_LICENSED = {
    "FUEL MANAGEMENT SETUP": "fuelman",
    "RECONCILIATION SETUP": "bir",
    # "In-Station Diagnostics (ISD) Install, Setup & Operation", 577013-800:
    # the console gains a whole setup function on an ISD site
    "EVR/ISD SETUP": "isd",
    "PMC SETUP": "pmc",
}

FUNCTION_REQUIRES = {
    "IN-TANK SETUP": ("probe",),
    "IN-TANK LEAK TEST SETUP": ("probe",),
    "FUEL MANAGEMENT SETUP": ("probe",),
    "LIQUID SENSOR SETUP": ("liquid",),
    "VAPOR SENSOR SETUP": ("vapor",),
    "GROUNDWATER SENSOR SETUP": ("gw",),
    "2-WIRE CL SENSOR SETUP": ("2wire",),
    "3-WIRE CL SENSOR SETUP": ("3wire",),
    "SMART SENSOR SETUP": ("smart",),
    "EVR/ISD SETUP": ("smart",),
    "PMC SETUP": ("smart",),
    "PRESSURE LINE LEAK SETUP": ("plld",),
    "PLLD LINE DISABLE SETUP": ("plld",),
    "WPLLD LINE LEAK SETUP": ("wplld",),
    "WPLLD LINE DISABLE SETUP": ("wplld",),
    "LINE LEAK DETECTOR SETUP": ("vlld",),
    "LINE LEAK TEST SETUP": ("vlld",),
    "LINE LEAK LOCKOUT SETUP": ("vlld",),
    "VLLD LINE DISABLE SETUP": ("vlld",),
    "EXTERNAL INPUT SETUP": ("io",),
    "OUTPUT RELAY SETUP": ("io", "relay"),
    "PUMP SENSOR SETUP": ("pump",),
    "PUMP RELAY MONITOR SETUP": ("pumpmon",),

    "VMC SETUP": ("vmc",),
    # a console with no comm board has nothing to set up in it
    # Every card the bay reads as a PORT, which is what this function sets
    # up: 577013-528's own tables are eleven cards and four of them are the
    # halves of dual-port modules. A console whose only board is a satellite
    # has port settings to program, and the tape is one. See FIDELITY M7.
    "COMMUNICATIONS SETUP": ("rs232", "modem", "mt", "vmc", "ssat", "asat",
                             "slink", "rs485", "rprinter", "aux"),
    # SYSTEM SETUP and ARCHIVE UTILITY are always present.
}

# Which sensor module a sensor-alarm category belongs to, so the bench only
# offers sensors the console could actually have.
SENSOR_MODULE_CATEGORY = {"liquid": "03", "vapor": "04", "gw": "07",
                          "2wire": "08", "3wire": "12", "universal": "13",
                          "smart": "28"}

# ---------------------------------------------------------------------------
# What each KIND of sensor can actually report.
#
# The serial protocol gives every sensor category the same nine alarm numbers,
# but a sensor is a piece of wire in a sump and it can only say what its
# resistance can distinguish. The Troubleshooting Guide prints the bands, one
# set per sensor family (Figures 6-17, 6-18, 6-20, 6-21), and the Operator's
# Manual prints the same answer as a status table per family (Tables 29-5 to
# 29-17). They agree, and they are narrower than the protocol:
#
#   "Single Float Sensor: Normal = 55000 - 135000; Fuel = 0 - 50000;
#    Open = >150000"
#
# is three states, which is why the console calls the type TRI-STATE, and why
# a 794380-208 sump sensor has a FUEL ALARM and a SENSOR OUT ALARM and
# nothing else. Simulating a WATER ALARM on one is simulating a sensor that
# does not exist.
# ---------------------------------------------------------------------------
# state -> the alarm number the shared sensor list gives it
SENSOR_STATE_NN = {"fuel": "03", "out": "04", "short": "05", "water": "06",
                   "waterout": "07", "high": "08", "low": "09", "warn": "10"}

# ---------------------------------------------------------------------------
# ALARM REDUCTION -- 577013-814 Rev N Appendix A, "Filtered Alarms".
#
# "In TLS-350 Software Version 32, Veeder-Root added filters to reduce
# nuisance alarms ... In the TLS-350 consoles this feature is called Alarm
# Reduction ... (Default is Enabled.) ... Refer to Appendix A for a list of
# the alarms that will be filtered and their resulting delay times."
#
# Every row carries TWO delays: a Detection Response Time, which is how long
# the condition has to hold before the console posts the alarm, and a Clear
# Response Time, which is how long it has to be gone before the console drops
# it. Read off pp.A-1 and A-2 by word position rather than by reading order --
# the two time columns sit at x=381 and x=469 against the alarm names at
# x=73, and a text extraction interleaves the bulleted names with them.
#
# The rows that name an alarm outright:
ALARM_FILTER_SECONDS = {
    "0209": (120.0, 180.0),     # Probe Out Alarm: 2 minutes, 3 minutes
    "1903": (360.0, 600.0),     # DIM Communication Alarm: 6 and 10 minutes
    "1803": (360.0, 600.0),     # ... and the MDIM's own number for it
    "2802": (120.0, 900.0),     # Mag Sump Sensor, Communication: 2 and 15
    "2803": (120.0, 900.0),     # Mag Sump Sensor, Fault: 2 and 15
}
# and then one block per wired sensor family -- Liquid, Vapor, Ground Water,
# 2-wire C.L. and 3-wire C.L. -- each with the SAME four rows:
#
#   the alarm states (Fuel, Water, Water Out, High/Low Liquid, Warning)
#                                             Immediate      3-minute delay
#   Open                                      2-minute       3-minute delay
#   Open, "when caused by open circuit and no open alarms within the last
#   24-hours"                                 Immediate      3-minute delay
#   Short                                     2-minute      15-minute delay
SENSOR_FILTER_CATEGORIES = ("03", "04", "07", "08", "12")
SENSOR_FILTER_SECONDS = {"04": (120.0, 180.0), "05": (120.0, 900.0)}
SENSOR_FILTER_DEFAULT = (0.0, 180.0)
# "no open alarms within the last 24-hours" is asked of the sensor that is
# alarming, which is the reading its own row supports: the row sits inside
# one family's block and every other row in that block is about one sensor.
# 3-wire's block splits Open by CAUSE as well -- noise takes the delay, an
# open circuit takes this rule -- and this console does not model the cause,
# so the rule is applied to the Open alarm itself. See UNKNOWNS A21.
SENSOR_FIRST_OPEN_WINDOW = 24 * 3600.0

# and how the console words each one on a status screen
SENSOR_STATE_WORDS = {
    # "SENSOR NORMAL", not "NORMAL": the word appears 61 times in
    # 576013-610 and on a photograph of a real 2025 slip, and it is the
    # whole status indicator rather than a prefix -- an alarming sensor
    # says "FUEL ALARM" with no SENSOR in front of it. See FIDELITY W6.
    "normal": "SENSOR NORMAL", "fuel": "FUEL ALARM",
    "out": "SENSOR OUT ALARM",
    "short": "SHORT ALARM", "water": "WATER ALARM",
    "waterout": "WATER OUT ALARM", "high": "HIGH LIQUID ALARM",
    "low": "LOW LIQUID ALARM", "warn": "LIQUID WARNING",
}

# S703, Liquid Sensor Type. Tables 29-5 to 29-8 and Figure 6-17's bands.
LIQUID_TYPE_STATES = {
    "1": ("fuel", "out"),                             # TRI-STATE
    "2": ("fuel",),                                   # NORMALLY CLOSED
    "3": ("out", "high", "low"),                      # DUAL FLOAT HYDROSTATIC
    "4": ("fuel", "out", "short", "high", "warn"),    # DUAL FLOAT DISCRIM
    "5": ("fuel", "out", "short", "high", "warn"),    # DUAL FLOAT HIGH VAPOR
    "6": ("fuel", "out"),                             # INTERCEPTOR
    "7": ("fuel", "out", "short", "high", "warn"),    # DW SUMP 2-1
}

# S743, Type A. "DISCRIM INTERSTITIAL sensors have three sensing states:
# normal condition, water detection, and fuel detection. ULTRA-2 sensors may
# have two sensing states: normal condition and liquid condition", and Figure
# 6-20 gives the Water band only to the discriminating one.
TYPE_A_STATES = {"1": ("fuel", "out", "short"),
                 "2": ("fuel", "out", "short", "water")}

# S748, Type B. Both modes read the same bands; High Vapor only gates when a
# Fuel alarm is allowed to post (see `sensor_alarm_allowed`).
TYPE_B_STATES = {"1": ("fuel", "out", "short", "high", "warn"),
                 "2": ("fuel", "out", "short", "high", "warn")}

# S74D, the Universal Sensor. Its own status report is the widest list any
# sensor category on this console has: 576013-635 Rev AA's 34B notes run
# "0002=Sensor Fuel Alarm 0003=Sensor Out Alarm 0004=Sensor Short Alarm
# 0005=Sensor Water Alarm 0006=Sensor Water Out Alarm 0007=Sensor High Liquid
# Alarm 0008=Sensor Low Liquid Alarm 0009=Sensor Liquid Warning", which is
# `SENSOR_STATE_NN` entire and in its order -- the point of the module being
# that one card reads every kind of sensor the others read between them.
#
# Which of the eight a GIVEN type senses is not on any page here. 74D names
# seven types -- TRI-STATE, NORMALLY CLOSED, DUAL DIFFERENTIATING, ULTRA 2,
# ULTRA 3, ULTRA/Z-1, ULTRA/Z-1 HV -- and no manual on this shelf prints a
# band table for one of them, where the liquid and Type A families each have
# a figure. So every type is offered the whole list, which is what Type B
# already does for its two, and the narrowing is an open question rather
# than a decision. See FIDELITY L2.
UNIVERSAL_STATES = ("fuel", "out", "short", "water", "waterout",
                    "high", "low", "warn")

# Vapor and groundwater have no type screen at all: one kind of sensor each.
VAPOR_STATES = ("fuel", "out", "short", "water")
GW_STATES = ("fuel", "out", "short", "waterout")

# The smart sensor is a different alarm list, and which of it a sensor has
# depends on what the console found on the end of the wire.
SMART_STATE_NN = {"setup": "01", "comm": "02", "fault": "03",
                  "fuelwarn": "04", "fuel": "05", "waterwarn": "06",
                  "water": "07", "highwarn": "08", "high": "09",
                  "lowwarn": "10", "low": "11", "temp": "12",
                  "relay": "13", "install": "14", "faultwarn": "15",
                  "vacuum": "16", "novacuum": "17"}
SMART_STATE_WORDS = {
    "normal": "NORMAL", "comm": "COMMUNICATION ALARM",
    "fault": "SENSOR FAULT ALARM", "faultwarn": "SENSOR FAULT WARNING",
    "fuelwarn": "FUEL WARNING", "fuel": "FUEL ALARM",
    "waterwarn": "WATER WARNING", "water": "WATER ALARM",
    "highwarn": "HIGH LIQUID WARNING", "high": "HIGH LIQUID ALARM",
    "lowwarn": "LOW LIQUID WARNING", "low": "LOW LIQUID ALARM",
    "temp": "TEMPERATURE WARNING", "relay": "RELAY ACTIVE",
    "install": "INSTALL ALARM", "vacuum": "VACUUM WARNING",
    "novacuum": "NO VACUUM ALARM",
}
_SMART_COMMON = ("comm", "fault", "faultwarn")
SMART_CATEGORY_STATES = {
    # "any designated Fuel, Water, Hi Liquid, and Lo Liquid warnings will
    # change to alarms", plus the Mag sensor's own Install and Temperature
    "03": _SMART_COMMON + ("fuelwarn", "fuel", "waterwarn", "water",
                           "highwarn", "high", "lowwarn", "low",
                           "temp", "install", "relay"),
    # "High Liquid Alarm from the float, Vacuum Warning from leak-rate,
    # No Vacuum Alarm above -1 psi"
    "04": _SMART_COMMON + ("high", "vacuum", "novacuum"),
    "05": _SMART_COMMON,                       # ATMP: it reads a pressure
}


# ---------------------------------------------------------------------------
# What this console says it is. A real one prints its revision level and then
# the features its S-Module licenses; the features it lists are the ones its
# cards can actually serve, so they are derived here rather than hard-coded.
#
# Which revision block it prints depends on the software chip in it, which is
# a thing you can change: the ladder of versions, and what each one knows how
# to drive, is in versions.py.
# ---------------------------------------------------------------------------
# The console this simulator starts as: an ECPU2 with an NVMEM201 running
# 3XX software at version 33, which is the most capable configuration the
# manual's table gives a non-MSP board, sixteen tanks, BIR, fiscal height
# security, alarm reduction. ISD and Maintenance Tracker are not on it,
# because those want the NVMEM203 instead and no console carries both.
DEFAULT_VERSION = 33
DEFAULT_BOARD = "E7"

SOFTWARE = versions.info(DEFAULT_VERSION, DEFAULT_BOARD)

# A line in SYSTEM FEATURES the card alone does not earn: a probe module
# lists CSLD only on software that has CSLD in it, and the two repetitive
# line tests are the software module keys' own.
FEATURE_LINE = {"CSLD": "csld", "0.10 REPETITIV": "plld",
                "0.20 REPETITIV": "plld", "PLLD": "plld", "WPLLD": "wplld",
                "VLLD": "vlld", "MAINTENANCE TRACKER": "mt"}

MODULE_FEATURES = {
    "probe": ["PERIODIC IN-TANK TESTS", "ANNUAL IN-TANK TESTS", "CSLD"],
    # The console's own words, off `tests/console_capture/raw/I90200.bin`:
    # `REPETITIV` without its last letter and `AUTO` for the on-demand key,
    # not the Setup Manual's "Repetitive" and "On Demand". Nine characters is
    # what fits beside `0.10 ` in the column the list is printed in.
    "plld": ["PLLD", "0.10 REPETITIV", "0.20 REPETITIV"],
    "wplld": ["WPLLD"],
    "vlld": ["VLLD"],
    "vmc": ["VMC"],
    "mt": ["MAINTENANCE TRACKER"],
}

# A line in SYSTEM FEATURES that NAMES the family under it rather than being
# a feature of its own, and which the console prints hard against the margin
# where a feature is indented two. `I90200.bin` draws `PLLD` and `WPLLD` at
# column 0 with their rates indented under each. See FIDELITY S18.
FEATURE_HEADERS = {"PLLD", "WPLLD", "VLLD", "VMC"}


def alarm_report_stamp(stamp):
    """`12-20-95` and `12:00PM`, the way 113, 114 and 115 print a moment.

    Neither the month nor the hour is zero padded -- p.54 reads
    `1-02-96  4:10AM` -- which is the same rule `clock_words` follows and a
    plain `%m-%d-%y %I:%M%p` breaks on both counts.
    """
    hour = stamp.tm_hour % 12 or 12
    return (f"{stamp.tm_mon}-{stamp.tm_mday:02d}-{stamp.tm_year % 100:02d}",
            f"{hour}:{stamp.tm_min:02d}"
            + ("AM" if stamp.tm_hour < 12 else "PM"))


# Table 29-1's device code -> the function that holds that device's label
DEVICE_LABEL_CODE = {"T": "602", "L": "702", "V": "707", "G": "712",
                     "C": "742", "H": "747", "s": "722", "P": "760",
                     "Q": "782", "W": "7A2", "R": "807", "I": "802",
                     "r": "7C5", "D": "522", "U": "74C"}

# and what to call one nobody has labelled
DEVICE_WORD = {"T": "TANK", "L": "LIQUID SENSOR", "V": "VAPOR SENSOR",
               "G": "GRND WATER", "C": "2-WIRE CL", "H": "3-WIRE CL",
               "s": "SMART SENSOR", "P": "VLLD LINE", "Q": "PLLD LINE",
               "W": "WPLLD LINE", "R": "RELAY", "I": "INPUT",
               "r": "PUMP RELAY", "g": "GRND TEMP",
               # the autodial receiver, whose letter the tape settles:
               # its console's only receiver prints as `D 8:`, under
               # AUTO DIAL TIME SETUP and again under AUTO DIAL ALARM
               # SETUP. See FIDELITY T4. Without the letter the 520-52F
               # reports fell through to "T" and headed every receiver
               # row with a TANK's product label.
               "D": "RECEIVER",
               # 576013-635 p.343's own unlabelled sample is
               # `UNIVERSAL SENSOR #1`, and 13 is the alarm category
               # consoledata maps to "U"
               "U": "UNIVERSAL SENSOR"}


def _map_entry(stored):
    """One map entry, with the fields that became the key taken out of it.

    An entry written before 7B1's four fields were the key carried `bus`,
    `slot` and `fp` inside it. `legacy_map_key` has just read them into the
    key, and leaving the copies behind would make two places to look for the
    same fact and one of them stale. See FIDELITY G7.
    """
    if not isinstance(stored, dict):
        return stored
    return {k: v for k, v in stored.items() if k not in ("bus", "slot", "fp")}


def _meter_store(name):
    """A meter-keyed attribute that stays `MeterDict` however it is set.

    The presets, a state file and half the tests assign a whole dict at
    once. Without this each of those would put a plain one back and the
    qualified key -- bus, slot, position, meter -- would quietly stop being
    what is stored. See `meterid.py` and FIDELITY G7.
    """
    return property(lambda self: getattr(self, name),
                    lambda self, value: setattr(self, name, MeterDict(value)))


class Console:
    """One simulated TLS-350."""

    # Everything that hangs off a meter is keyed on its full identity --
    # bus, slot, real fueling position, real meter -- because 7B1 says a
    # meter is identified by those four and by nothing shorter.
    meters = _meter_store("_meters")
    meter_flow = _meter_store("_meter_flow")
    meter_map = _meter_store("_meter_map")
    # Which tanks a blended nozzle actually draws from. The console's own
    # map holds ONE tank per meter, because that is all 7B1 can say; a
    # blender is a dispenser that mixes two of them behind the nozzle.
    blends = _meter_store("_blends")

    def __init__(self, state_path=None):
        self.state_path = state_path
        self.values = {}          # "S60201" -> stored ASCII data
        # how many of each card is in the cage, not just whether one is
        # a bare console: a probe card and something to talk through. Every
        # other card, and every software option, is fitted on the bench.
        self.modules = {"probe": 1, "rs232": 1}
        # Which of the smart sensor family's two cards is fitted: the
        # eight-input Smart Sensor Interface Module, or the seven-input
        # Smart Sensor/Press Module whose eighth channel is its own
        # atmospheric pressure sensor. See FIDELITY M2.
        self.smart_press = False
        # ...and which of the probe family's two: the plain Four-Input Probe
        # Module, or the Probe/Thermistor Interface Module that carries four
        # thermistor positions beside its four probes. FIDELITY M4.
        self.probe_gt = False
        # Which of the comm bay's four slots each card is in, {slot: key}.
        # Empty means nobody has arranged the bay by hand and `comm_layout`
        # will lay it out legally; an entry is somebody having put a card
        # somewhere, including somewhere Table 7-2 says it will not work.
        self.comm_slots = {}
        # The wiring harness a dual-port module needs before it does
        # anything: "connect the 4-pin connector (of the included wiring
        # harness) to J4 on the Module ... the 8-pin connector of the harness
        # to J6 on the ECPU/ECPU2 board". It ships with the card, so it is on
        # by default and is here to be disconnected. The double one is
        # ordered separately -- P/N 332609-001 -- and is what a second
        # dual-port module in slot 3 needs.
        self.dual_harness = True
        self.double_harness = False
        # V80's buffer, "number of Vapor Processor cycles (0-20)": one entry
        # per run of the processor. In memory the way the leak test history
        # is, because it is a log of what happened rather than programming.
        self.vp_cycles = []
        self.vp_started = None        # when the current run began, if it is on
        self.hc_cleared = None        # V81's buffer clear, if anybody has
        # (family, device) -> the phase 092 to 09B last put it in
        self.control_phase = {}
        self.software = {"csld": True}
        # The CPU board in the console and the software on it. A TLS-350, a
        # PLUS and an R are the same box; these two are the difference, and
        # between them they decide what is on the menus at all.
        self.version = DEFAULT_VERSION
        self.board = DEFAULT_BOARD
        self.tank_level = {}      # tank -> {"volume", "water"}
        self.sensor_state = {}    # (module, number) -> state
        self.siphon_shut = set()  # tanks whose siphon valve the bench shut
        self.low_temp = set()     # tanks below -4 F and not yet back over 0
        self.acked = set()        # acknowledged, waiting for the cause to go
        # tank -> circuit code of the probe fitted on the bench, e.g. "D004".
        # Empty means nobody has chosen one and the console falls back to
        # deriving a code from the tank's setup. See probe_circuit.
        self.probe_fitted = {}
        self.latched = set()      # alarms held on the display after the cause
        self._seen = set()        # conditions as of the last look
        # AANNTT -> {"since", "gone", "on"}: what Alarm Reduction is holding
        # back, and what it is holding on. See ALARM_FILTER_SECONDS.
        self._filtered = {}
        self.alarm_log = []       # {aa, nn, tt, at} newest first, for I206
        self.service_entries = []  # what a contractor entered, for 116/11A
        self.autodial_alarm = {}   # receiver -> whether 52D says it is in alarm
        self._submerged = {}       # (tank, volume) -> which thermistors are wet
        # 888 reports, per comm position, when it last carried data and when
        # it last failed, and lists the errors behind the second. A console
        # keeps all three; this one can honestly know the first, because the
        # serial port is the one thing here that really does carry traffic.
        # `comm_errors` stays empty until something in this simulator can
        # produce a UART error -- see FIDELITY S13.
        self.comm_data_at = {}     # port -> when it last answered anything
        self.comm_connect = {}     # port -> 888's connect type, "01".."06"
        self.comm_error_at = {}    # port -> when it last failed
        self.comm_errors = {}      # port -> [{"state", "error", "uart"}]
        self.user_service_codes = []   # 8A2's "USER DEFINED LABEL" rows
        # 8A3's Contractor ID keys and 8A4's blocked ones, both as
        # (six-character ID, seventeen-character label), which is what both
        # reports carry: "nnnnnnnnnnnnnnnnn - ID label (17 characters,
        # ASCII), cccccc - Six digit ID code (ASCII)". A key gets onto the
        # active list by being presented, which is `present_tracker_key`.
        self.mt_keys = []
        self.blocked_keys = []
        # (ID, when) for the contractor logged in at the moment, if anybody
        # is: "Contractor connects a valid ID Key to the TLS and presses the
        # blue key to log in for a work session."
        self.mt_session = None
        # The key MAINT HARDWARE KEY BLOCK is standing on, or the one typed
        # into ENTER ID TO BLOCK: what ARE YOU SURE?: YES will block.
        self.mt_pending = ""
        # (monitor, "stuck"|"run") -> when that condition began, which
        # is what the Pump Relay Monitor's two delays count from.
        self.pumpmon_since = {}
        self.service_sessions = []  # 11B's start/end pairs
        # The vacuum sensor's interstitial space: what the bench is letting
        # into it, what the pressure has got to, and what the last manual
        # test measured. See the VAC block below.
        # A tank the bench is holding at a temperature, against the seasonal
        # wander. The one site condition Table 29-4 names that this console
        # had no way to be got into.
        self.tank_temp = {}       # tank -> degrees F, or absent
        # What the hold WAS, so that moving it during a test reads as drift
        # rather than as a tank that was always at the new figure. Table
        # 29-4's two thermistor criteria are rates, and a rate needs a past.
        self.tank_temp_log = {}   # tank -> [(when, degrees F or None)]
        self.vac_leak = {}        # sensor -> gallons an hour getting in
        self.vac_pressure = {}    # sensor -> psi gauge, negative is vacuum
        self.vac_tests = {}       # sensor -> the last test's record
        self.vac_running = {}     # sensor -> when a manual test started
        self.vac_hold = set()     # sensors held open, EVAC HOLD
        self.vac_high_since = {}  # sensor -> when its leak rate passed 22.4
        self.vac_no_vac = set()   # sensors whose NO VACUUM ALARM is posted
        self.vmc_serials = {}     # VMC controller -> its serial number
        # 61F, Set Delivery Density: "(tank, delivery type)" -> the value
        # somebody entered, where the type is the manual's own "0=next,
        # 1=last". Kept as ENTERED, because that is what the console reports
        # back -- "FFFFFFFF - Entered Density, relative, actual or API".
        self.delivery_density = {}
        # Settings the panel programmes that no serial function reads: the
        # Setup Manual draws the screen, the Serial Interface Manual has no
        # code for it, so the console holds it rather than the wire.
        self.settings = {}        # (key, device) -> what was entered
        self.vmc_state = {}       # (controller, side) -> what it reads
        self.recon_kind = "shift"   # the period Reconciliation Mode is on
        self.recon_previous = False  # CURRENT or PREVIOUS, on that mode
        self.silenced = False     # the audible alarm, after ALARM/TEST
        self.printer_lever_open = False  # the feed roller release, down
        self.out_of_paper = False  # the roll run out: nothing prints, and
                                   # the console says so, "Printer out of
                                   # Paper" is a system alarm of its own
        self.clock_offset = 0.0   # the console keeps its own date and time
        self.relays = {}          # output relays, as TEST OUTPUT RELAYS left them
        self.tank_leak = {}       # tank -> gallons an hour going missing
        self.probe_out = set()    # tanks whose probe is unplugged at the riser
        # A15's SAMPLES READ against SAMPLES USED: how many of the samples a
        # probe reported the console could use. It is a HISTORY, which is
        # what makes 577014-348's triage step work -- "check samples_read /
        # sample_used to see probe performance PREVIOUS to being out" -- so
        # it survives the probe being plugged back in. See FIDELITY R15.
        self.probe_dropped = {}   # tank -> samples the console threw away
        self.probe_last_error = {}  # tank -> (sample number, unix time)
        # The RS-232 card's security DIP switch. 576013-635 p.267: "The system
        # will not respond to a command without the proper security code, if
        # the DIP switch is set to enable RS-232 security." The code itself is
        # programmed (504); this is the physical switch that turns enforcement
        # on. Off out of the box, as the card ships.
        self.rs232_security = False
        # The other positions of the same 4-position DIP (SW2, next to the
        # battery switch -- 576013-635 p.7): 1 = front-panel security,
        # 3 = display power (closed blanks the display), 4 = unused.
        # Position 1 ships enabled here so a programmed security code locks
        # the panel, which is the behaviour the setup manual walks.
        self.panel_security = True
        # DIP position 4: the hardware enable for Fiscal Height Security.
        # 576013-635 Rev AA 4.1 p.6 assigns all four positions -- 1 front
        # panel setup security, 2 RS-232 security, 3 unused, 4 fiscal height
        # security -- and position 4 was not modelled at all, so I13200
        # printed the software flag twice under two labels and the two could
        # never disagree. The flag is what somebody programmed; the switch is
        # what the hardware allows. FIDELITY M13.
        #
        # Position 3 is left as it is. 635 calls it unused and 576013-623
        # calls it display power, the two manuals genuinely disagree, and
        # `display_blanked` rests on the 623 reading rather than on nothing.
        self.fiscal_height_switch = False
        self.display_blanked = False
        # ---- power -----------------------------------------------------
        # The breaker on the wall, the Battery Backup switch (S1) on the
        # ECPU, and the battery itself. With AC off, the battery -- switch
        # on AND battery fitted -- is all that holds RAM; break that chain
        # at any moment while the power is out and the programming is gone,
        # and putting the battery back does not bring it back.
        self.powered = True
        self.battery_switch = True
        self.battery_present = True
        self._ram_held = True     # whether RAM survived the outage so far
        # Whether anyone has set the clock since the last cold boot. The
        # battery backs the clock, so a cold boot loses it, and a console
        # that has not been told the time since then says so.
        self.clock_set = True
        # The safety cover over the power area. Take it off and the console
        # posts Protective Cover Alarm until it goes back on.
        self.cover_open = False
        # Turning the battery switch ON before startup completes is a
        # System Self Test Error (576013-818); it stands until the next
        # power cycle done properly.
        self.selftest_error = False
        self.booting = False      # inside the power-up sequence
        # When the console was last switched off and back on, or None on one
        # that has simply been running. The archive's post-reboot hold is
        # counted from here; see `restore_hold`.
        self.reboot_at = None
        # The software chip the console booted with. Swap the chip with the
        # battery switch on -- change the version without a cold boot --
        # and ROM Revision Warning posts until a cold boot owns the change.
        self.rom_at_boot = None
        # The ISD monitoring tests. Nothing in a simulator measures a real
        # vapour, so the bench SETS a test's outcome the way it sets a
        # sensor's state, and everything downstream -- the alarms, the site
        # shutdown, the reports -- follows from it. Keys are the tests of
        # 577013-800 Table 3; values "warn" or "fail".
        self.isd_forced = {}
        self.isd_forced_at = {}
        self.isd_pending = ""     # which CLEAR TEST selection ENTER is on
        self.isd_events = []      # (at, line1, line2) for the misc event log
        self.isd_clears = []      # what TEST FAIL CLEAR DATES prints, newest first
        # The daily assessment. "Each ISD monitoring test operates once each
        # day ... When a test first fails, a warning is posted ... If this
        # condition persists for seven more consecutive days, an alarm is
        # posted". So a test's REPORTED state is not what the bench set; it
        # is how long what the bench set has stood. See FIDELITY I3.
        self.isd_days = {}        # test -> consecutive failing assessments
        self.isd_assessed = None  # console time of the last assessment
        # What the setup self-test found when it last ran, which is at
        # power-up and at each daily assessment rather than continuously.
        self.isd_setup_result = []
        # And the same for the three processor tests: "The processor
        # over-pressure test occurs at daily intervals at the daily
        # assessment time after at least 1-day's UST ullage vapor pressure
        # data has been collected."
        self.isd_vp_result = set()
        # A failed shutdown-class test disables dispensing until the alarm
        # clears or a technician overrides it from the panel: ALARM/TEST
        # three times, then the OVERRIDE SHUTDOWN & LOG confirmation.
        self.isd_override = False
        # bench faults on the comm gear: the remote display link, and the
        # DIM link the metered transactions arrive over
        self.rdu_fault = False
        self.dim_fault = False
        self.dim_disabled = set() # DIM ports whose card the ECPU cannot reach
        # the grade-to-hose map the AUTO/MANUAL MAP flows build:
        # hose (device index) -> the meter that proved to dispense it
        self.isd_hose_map = {}
        self.line_leak = {}       # (kind, line) -> gallons an hour
        # The pump side of the check valve is a DIFFERENT piece of pipe, and
        # a leak in it is invisible to a line test: "leak detection for
        # components prior to the check valve must be provided" is the setup
        # manual warning about exactly this gap. So it leaks separately.
        self.pump_leak = {}       # (kind, line) -> gallons an hour
        self.clock_speed = 1.0    # a bench control: a 12 hour test in a minute
        self.posted = set()       # alarms a test has raised, AANNTT
        self.in_setup = False     # the warning waits until you leave Setup
        self.chart_code = ""      # Tank Chart Security passcode, "" = off
        self.chart_code_set = ""  # when it was last changed, for I56A
        self.serial_number = ""   # the console's own, off the label
        self.wm_office = ""       # the Weights and Measures office
        self.tank_capacity = {}   # tank -> capacity the W&M officer entered
        self.chart_audit = {}     # tank -> [YYMMDDHHmm], newest first
        self.tank_profiles = {}   # tank -> profile code, panel-set only
        self.printed_deliveries = []   # drops waiting for the printer
        self.meters = {}          # MeterId -> the tank it draws from
        self.meter_map = {}       # MeterId -> {tank, locked}, 7B1's own store
        self.meter_offsets = {}   # 7B4: (fp, meter) -> {fp, tank, pct}
        self.vmc_fuel_pos = {}    # 8C3: VMC -> {"A": position, "B": position}
        self.generator_log = {}   # 404: tank -> [runs feeding a generator]
        self.dim_faults = {}      # BA1: DIM port -> [comm faults]
        self.dim_down = set()     # the ports the bench has taken down
        self.apm_cleared = {}     # VA7: test type -> when it was last cleared
        self.apm_event_log = []   # VA8: what the APM has done
        self.vmci_sub_log = []    # VA5: sub-alarms under a VMCI alarm
        self.started = time.mktime(time.localtime())   # 908 counts from here
        self.receiver_reports = {}   # 52A: receiver -> {report: "01"/"00"}
        self.receiver_dial = {}      # 52B: receiver -> method digit + when
        self.receiver_alarms = {}    # 52C: receiver -> [(aa, nn, tt)]
        # 787, 7A7 and 75B: the alarms that DISABLE a line, per line.
        # "Set Pressure Line Leak Disable Alarm Assignments" and its two
        # siblings carry 52C's own payload -- `AANNTTSS`, one assignment a
        # Set, SS putting it on the list or taking it off -- and report it
        # the way 52C does, a line and its assignments under it. Keyed
        # (kind, number). See FIDELITY S17.
        self.line_disable_alarms = {}
        # 808: the alarms assigned to each output relay, relay -> [(aa, nn,
        # tt)]. A LIST, because 576013-623 Rev AN p.24-3 says so outright:
        # "You may assign more than one in-tank alarm, sensor alarm, and
        # external input to a relay, and you may assign any in-tank alarm,
        # sensor alarm, and external input to more than one relay." The
        # code's own name is "Set Relay Alarm AssignmentS" and this console
        # kept one -- a `digits` field of width 8, so a second Set
        # overwrote the first. See FIDELITY I1a.
        self.relay_alarms = {}
        self.meter_flow = {}      # MeterId -> gallons an hour it sells
        # MeterId -> {"label": "E15", "parts": [[tank, pct], ...]}. A
        # blender mixes at the nozzle, so the fuel leaves two tanks in the
        # ratio the dispenser is set to and the POS reports each component
        # against the tank it came out of.
        self.blends = {}
        self._last_tick = time.time()
        self._last_console = None
        # When this console was last started. "Days Before Tank Periodic Test
        # Needed Warning" counts from the last test that passed; a console
        # that has never passed one has to count from somewhere, and a cold
        # start is the date a real one would be counting from.
        self._commissioned = None
        # "POWER REMOVED" and "POWER RESTORED": a console keeps the last
        # outage, and starting this simulator IS the restore.
        self.power_off = None
        self.power_off_state = {}
        self.leaks = leaktest.Engine(self)
        self.sumps = sumptest.Sumps(self)
        self.calibrations = pscal.Calibrations(self)
        self.lines = _pressure.Lines(self)
        self.deliveries = _delivery.Deliveries(self)
        self.loads = _delivery.Loads(self)
        self.drops = _delivery.Drops(self)
        self.inputs = _inputs.Inputs(self)
        self.outputs = _relays.Outputs(self)
        self.sales = _sales.Sales(self)
        self.traffic = _traffic.Traffic(self)
        self.csld = _csld.CSLD(self)
        self.bir = _bir.BIR(self)
        self.shifts = _shifts.Shifts(self)
        self.accuchart = _accuchart.AccuChart(self)
        self.autodial = _autodial.Autodial(self)
        self.autotx = _autotx.AutoTransmit(self)
        self.accuchart_log = []   # (tank, when) each time a chart was applied
        # The E2 chip's other tenant. "AccuChart users should note that you
        # archive system setup data only in the E2 chip. If AccuChart is
        # complete, the final calibration values are automatically stored in
        # the E2 chip and there will be no need to recalibrate the tank."
        # Nobody saves these and nobody puts them back by hand: a finished
        # calibration is written to the chip on its own, and the chip is not
        # RAM, so a cold boot hands it straight back rather than asking a
        # site for another 56 days. Tank number -> what AccuChart had when it
        # finished, which `accuchart.py` writes and reads.
        self.e2_accuchart = {}
        self.lock = threading.Lock()
        if state_path and os.path.exists(state_path):
            self.load()
        # the chip and the cards this console woke up with
        self.rom_at_boot = self.version
        self._mt_seen = self.has("mt")

    def reset(self, keep_clock=False):
        """Back to a console out of its box: no programming, nothing fitted.

        A cold start with the battery switch off is the nearest real
        equivalent, "you will lose system programming if AC power to the
        console is interrupted": and it is what you want when an experiment
        has left the console in a state nobody can read.

        The software version survives it, because the program is a chip and
        not something the battery holds. Loading a preset changes it; this
        does not.
        """
        offset, speed = self.clock_offset, self.clock_speed
        self.values.clear()
        self.tank_level.clear()
        self.sensor_state.clear()
        self.siphon_shut.clear()
        self.low_temp.clear()
        self.tank_leak.clear()
        self.probe_out.clear()
        self.rs232_security = False
        self.line_leak.clear()
        self.pump_leak.clear()
        self.relays.clear()
        self.acked.clear()
        self.latched.clear()
        self.posted.clear()
        self._seen.clear()
        self.alarm_log.clear()
        self.vmc_serials.clear()
        self.settings.clear()
        self.vmc_state.clear()
        self.printed_deliveries.clear()
        self.tank_capacity.clear()
        self.chart_audit.clear()
        self.tank_profiles.clear()
        self.meters.clear()
        # And the programming kept outside `values` and `settings`. Left
        # behind, a preset loaded over a programmed console kept the old
        # site's relay and receiver alarm lists, its line disables, its
        # meter map and offsets -- and, `settings` being cleared, a relay
        # whose screens said NO over a list still driving its coil.
        self._clear_stores()
        # Out of the box nobody's Maintenance Tracker key has been presented.
        # `cold_boot` puts the keys back: they are FPROM, not RAM.
        self.mt_keys, self.blocked_keys = [], []
        self.chart_code = self.chart_code_set = ""
        self.serial_number = self.wm_office = ""
        self.silenced = False
        self.out_of_paper = False
        self.printer_lever_open = False
        self.clock_set = True
        self.cover_open = False
        self.selftest_error = False
        self.isd_forced = {}
        self.isd_forced_at = {}
        self.isd_pending = ""     # which CLEAR TEST selection ENTER is on
        self.isd_events = []
        self.isd_clears = []
        self.isd_days = {}
        self.isd_assessed = None
        self.isd_setup_result = []
        self.isd_vp_result = set()
        self.isd_override = False
        self.rdu_fault = False
        self.dim_fault = False
        self.dim_disabled = set() # DIM ports whose card the ECPU cannot reach
        self.dim_faults = {}
        self.dim_down = set()
        self.isd_hose_map = {}
        self.modules = {"probe": 1, "rs232": 1}
        # Which of the smart sensor family's two cards is fitted: the
        # eight-input Smart Sensor Interface Module, or the seven-input
        # Smart Sensor/Press Module whose eighth channel is its own
        # atmospheric pressure sensor. See FIDELITY M2.
        self.smart_press = False
        # ...and which of the probe family's two: the plain Four-Input Probe
        # Module, or the Probe/Thermistor Interface Module that carries four
        # thermistor positions beside its four probes. FIDELITY M4.
        self.probe_gt = False
        # Which of the comm bay's four slots each card is in, {slot: key}.
        # Empty means nobody has arranged the bay by hand and `comm_layout`
        # will lay it out legally; an entry is somebody having put a card
        # somewhere, including somewhere Table 7-2 says it will not work.
        self.comm_slots = {}
        # The wiring harness a dual-port module needs before it does
        # anything: "connect the 4-pin connector (of the included wiring
        # harness) to J4 on the Module ... the 8-pin connector of the harness
        # to J6 on the ECPU/ECPU2 board". It ships with the card, so it is on
        # by default and is here to be disconnected. The double one is
        # ordered separately -- P/N 332609-001 -- and is what a second
        # dual-port module in slot 3 needs.
        self.dual_harness = True
        self.double_harness = False
        # V80's buffer, "number of Vapor Processor cycles (0-20)": one entry
        # per run of the processor. In memory the way the leak test history
        # is, because it is a log of what happened rather than programming.
        self.vp_cycles = []
        self.vp_started = None        # when the current run began, if it is on
        self.hc_cleared = None        # V81's buffer clear, if anybody has
        # (family, device) -> the phase 092 to 09B last put it in
        self.control_phase = {}
        self.software = {"csld": True}
        self.leaks = leaktest.Engine(self)
        self.sumps = sumptest.Sumps(self)
        self.calibrations = pscal.Calibrations(self)
        self.lines = _pressure.Lines(self)
        self.deliveries = _delivery.Deliveries(self)
        self.loads = _delivery.Loads(self)
        self.drops = _delivery.Drops(self)
        self.inputs = _inputs.Inputs(self)
        self.outputs = _relays.Outputs(self)
        self.sales = _sales.Sales(self)
        self.traffic = _traffic.Traffic(self)
        self.csld = _csld.CSLD(self)
        self.bir = _bir.BIR(self)
        self.shifts = _shifts.Shifts(self)
        self.accuchart = _accuchart.AccuChart(self)
        self.autodial = _autodial.Autodial(self)
        self.autotx = _autotx.AutoTransmit(self)
        self.accuchart_log.clear()
        if keep_clock:
            self.clock_offset, self.clock_speed = offset, speed
        else:
            self.clock_offset, self.clock_speed = 0.0, 1.0
        self.rom_at_boot = self.version
        self._mt_seen = self.has("mt")
        self.save()

    # ---- power -------------------------------------------------------------
    def battery_backup(self):
        """Is anything holding RAM with the AC off? Switch AND battery."""
        return self.battery_switch and self.battery_present

    def breaker_off(self):
        """The breaker opens. From here, RAM lives on the battery alone.

        The moment the lights go out is also the moment the POWER REMOVED
        record and the power-off tank readings are taken -- that is what
        those screens show when power comes back.
        """
        if not self.powered:
            return
        import time as _t
        self.power_off = _t.mktime(self.now())
        self.power_off_state = {
            n: {"volume": st.get("volume", 0.0),
                "water_vol": self.water_volume(n),
                "temp": self.product_temperature(n)}
            for n, st in self.tank_level.items()}
        # "APM Shutdown at:", VA8's second system event. FIDELITY I8.
        self.apm_log("01", "02", when=self.power_off)
        self.powered = False
        self.selftest_error = False    # the prescribed fix is a power cycle
        self._ram_held = self.battery_backup()

    def battery_changed(self):
        """The switch was flipped or the battery pulled or fitted.

        With AC on, nothing happens: the supply holds RAM. With AC off, the
        battery chain breaking for even a moment loses RAM, and restoring
        the chain afterwards does not bring it back. And flipping the
        switch ON while the console is still starting up is a System Self
        Test Error, which the troubleshooting guide tells you to fix with a
        proper power cycle.
        """
        if self.booting and self.battery_switch:
            self.selftest_error = True
        if not self.powered and not self.battery_backup():
            self._ram_held = False

    def breaker_on(self):
        """The breaker closes. Warm boot if RAM held; cold boot if it did
        not. Returns "warm" or "cold" so the caller can say which happened.
        """
        if self.powered:
            return "warm"
        self.powered = True
        # "If you are restoring after a reboot (switching the console Off and
        # then back On), the system will wait 5 minutes before processing
        # your request to restore archived setup data. This delay is to allow
        # all hardware to initialize." The reboot is the switching back on,
        # and it is the same reboot whether RAM held or not: what is being
        # waited for is hardware, which does not know which kind of boot this
        # turned out to be.
        self.reboot_at = time.time()
        # "Setup self-testing occurs following power-up as well as at daily
        # intervals at the Daily Test Time", 577013-819 Rev F p.17.
        if self._ram_held:
            self.isd_setup_selftest()
            self.apm_log("01", "01")          # "APM Startup at:"
            return "warm"
        self.cold_boot()
        self.isd_setup_selftest()
        self.apm_log("01", "01")
        return "cold"

    def cold_boot(self):
        """Power returned to a console whose RAM did not survive.

        What is lost is what the battery held: the programming, the alarm
        history, every log. What survives is what is not RAM: the cards in
        the cage (the cage is re-scanned at power-up), the software chips
        and their keys, the RS-232 card's DIP switch, and the archive in
        the E2 chip, which exists precisely so a cold-booted console can be
        restored. The site outside the console -- fuel, water, sensors,
        leaks -- is the world, not memory, and the world does not reboot.
        """
        modules = dict(self.modules)
        # Where the cards are and what they are wired to is hardware, and the
        # cage is re-scanned at power-up: a cold boot loses the programming,
        # not the bay's arrangement.
        slots = dict(self.comm_slots)
        harness = (self.dual_harness, self.double_harness)
        variants = (self.smart_press, self.probe_gt)
        # The Maintenance Tracker's key lists are not RAM either: a log-in
        # record goes "to FPROM", 576013-610 ch.33, and the feature wants an
        # NVMEM203 to exist at all. The SESSION does not survive -- nobody is
        # logged in to a console that has just come back up.
        keys = (list(self.mt_keys), list(self.blocked_keys))
        board, software = self.board, dict(self.software)
        tanks = dict(self.tank_level)
        sensors = dict(self.sensor_state)
        t_leak, l_leak = dict(self.tank_leak), dict(self.line_leak)
        p_leak = dict(self.pump_leak)
        p_out = set(self.probe_out)
        s_shut = set(self.siphon_shut)
        dip = self.rs232_security
        meterf = dict(self.meter_flow)
        trucks = self.drops
        contacts = dict(self.inputs.state)
        welded = set(self.outputs.welded)
        nozzles = self.sales
        # The forecourt is the world, not RAM: rebooting the console does
        # not close the store, empty the queue or cancel the tanker that is
        # already on its way. Nor does it un-blend a dispenser.
        forecourt, mixes = self.traffic, dict(self.blends)
        self.reset(keep_clock=False)
        self.modules = modules
        self.comm_slots = slots
        self.dual_harness, self.double_harness = harness
        self.smart_press, self.probe_gt = variants
        self.mt_keys, self.blocked_keys = keys
        self.board, self.software = board, software
        self.tank_level = tanks
        self.sensor_state = sensors
        self.tank_leak, self.line_leak = t_leak, l_leak
        self.pump_leak = p_leak
        self.probe_out = p_out
        self.siphon_shut = s_shut
        self.rs232_security = dip
        self.meter_flow = meterf
        # a tanker on the ground does not notice the console rebooting
        self.drops = trucks
        self.drops.c = self
        # and a contact does not open because the console rebooted, nor
        # does a welded contactor come unstuck
        self.inputs.state = contacts
        self.outputs.welded = welded
        # a nozzle in somebody's hand is still in it
        self.sales = nozzles
        self.sales.c = self
        # `Lines` was rebuilt by the reset, so the handles came down with
        # it; the nozzles that are still in somebody's hand put them back
        self.sales.resync()
        self.traffic = forecourt
        self.traffic.c = self
        self.blends = mixes
        self._ram_held = True
        self.clock_set = False    # the battery backed the clock too
        self.rom_at_boot = self.version
        self._mt_seen = self.has("mt")
        self.save()

    # ---- persistence -------------------------------------------------------
    def load(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                blob = json.load(fh)
            self.values = blob.get("values", {})
            self.modules = {k: int(v) for k, v
                            in blob.get("modules", self.modules).items()}
            # JSON has no integer keys, so the slot numbers come back as text
            self.comm_slots = {int(k): v for k, v
                               in blob.get("comm_slots", {}).items()}
            # tuples do not survive JSON either
            self.mt_keys = [tuple(x) for x in blob.get("mt_keys", [])]
            self.blocked_keys = [tuple(x)
                                 for x in blob.get("blocked_keys", [])]
            self.dual_harness = bool(blob.get("dual_harness", True))
            self.smart_press = bool(blob.get("smart_press", False))
            self.probe_gt = bool(blob.get("probe_gt", False))
            self.double_harness = bool(blob.get("double_harness", False))
            self.tank_level = {int(k): v for k, v in blob.get("tanks", {}).items()}
            self.sensor_state = {tuple(k.split("|")): v
                                 for k, v in blob.get("sensors", {}).items()}
            self.inputs.state = {int(k): bool(v)
                                 for k, v in blob.get("inputs", {}).items()}
            self.clock_offset = blob.get("clock_offset", 0.0)
            self.chart_code = blob.get("chart_code", "")
            self.chart_code_set = blob.get("chart_code_set", "")
            self.serial_number = blob.get("serial_number", "")
            self.wm_office = blob.get("wm_office", "")
            self.tank_capacity = {int(k): v for k, v
                                  in blob.get("tank_capacity", {}).items()}
            self.chart_audit = {int(k): v for k, v
                                in blob.get("chart_audit", {}).items()}
            self.tank_profiles = {int(k): v for k, v
                                  in blob.get("tank_profiles",
                                              {}).items()}
            self.software = blob.get("software", self.software)
            # the chip's completed calibrations, whose tank numbers came
            # back from JSON as text
            self.e2_accuchart = {int(k): v for k, v
                                 in blob.get("e2_accuchart", {}).items()}
            self.version = int(blob.get("version", self.version))
            self.board = blob.get("board", self.board)
            # `meter_key` takes both forms, so a state file written
            # before the map could hold a fueling position still loads: a
            # bare number picks up the default DIM and the position
            # two-to-a-dispenser puts it at, which is where it used to sit.
            self.meters = {meter_key(k): int(v)
                           for k, v in blob.get("meters", {}).items()}
            self.blends = {meter_key(k): v
                           for k, v in blob.get("blends", {}).items()}
            self.traffic.restore(blob.get("traffic"))
            self.settings = self._settings_from(blob.get("settings"))
            self._stores_load(blob)
        except Exception as e:
            print(f"[sim] could not load state: {e}")

    def save(self):
        if not self.state_path:
            return
        # An exposed console does not write its programming down. Everything
        # a network client sets still takes effect and still reads back --
        # the emulation is unchanged from the wire's point of view -- but it
        # lives only as long as the process, so a restart is always a clean
        # restart and nothing a stranger sent can outlive one.
        # See `exposed.writes_frozen`.
        if exposed.refused("console state"):
            return
        try:
            with atomicfile.replacing(self.state_path) as fh:
                json.dump({"values": self.values, "modules": self.modules,
                           "comm_slots": self.comm_slots,
                           "mt_keys": self.mt_keys,
                           "blocked_keys": self.blocked_keys,
                           "dual_harness": self.dual_harness,
                           "smart_press": self.smart_press,
                           "probe_gt": self.probe_gt,
                           "double_harness": self.double_harness,
                           "tanks": self.tank_level,
                           "sensors": {"|".join(k): v
                                       for k, v in self.sensor_state.items()},
                           "inputs": {str(k): v
                                      for k, v in self.inputs.state.items()},
                           "clock_offset": self.clock_offset,
                           "chart_code": self.chart_code,
                           "chart_code_set": self.chart_code_set,
                           "serial_number": self.serial_number,
                           "wm_office": self.wm_office,
                           "tank_capacity": self.tank_capacity,
                           "chart_audit": self.chart_audit,
                           "tank_profiles": self.tank_profiles,
                           "software": self.software,
                           "e2_accuchart": self.e2_accuchart,
                           "version": self.version,
                           "board": self.board,
                           "meters": self.meters.as_json(),
                           "blends": self.blends.as_json(),
                           "traffic": self.traffic.state(),
                           "settings": self._settings_json(),
                           **self._stores_json()},
                          fh, indent=1)
        except Exception as e:
            print(f"[sim] could not save state: {e}")

    # ---- the programming kept outside `values` -------------------------------
    def _settings_json(self):
        """`settings` with keys a JSON object can hold: `"tank_test_method|0"`.

        The store is keyed (name, device), and a tuple is not a JSON key,
        which is presumably why it was never written: 118 setup screens --
        every relay and line-disable group's YES/NO, the test methods, the
        inventory units -- forgot their value on every restart, and a
        relay's group screen came back NO over an alarm list that still
        drove its coil.
        """
        return {f"{key}|{device}": value
                for (key, device), value in sorted(self.settings.items())}

    @staticmethod
    def _settings_from(stored):
        """The other way: the device is after the LAST bar, and a number."""
        out = {}
        for text, value in (stored or {}).items():
            key, _, device = str(text).rpartition("|")
            if key and device.lstrip("-").isdigit():
                out[(key, int(device))] = value
        return out

    # Programming that lives in a store of its own rather than in `values`,
    # mostly because its key is one JSON cannot hold. It is RAM like the
    # rest -- a reset clears it, a cold boot loses it, an archive carries it
    # -- so one place says how each is written and read back, and the state
    # file, the archive and `reset` cannot come to disagree about the list.
    STORES = ("meter_map", "meter_offsets", "vmc_fuel_pos",
              "receiver_reports", "receiver_dial", "receiver_alarms",
              "relay_alarms", "line_disable_alarms")

    def _stores_json(self):
        return {"meter_map": self.meter_map.as_json(),
                "meter_offsets": {f"{fp}.{m}": v for (fp, m), v
                                  in self.meter_offsets.items()},
                "vmc_fuel_pos": self.vmc_fuel_pos,
                "rcvr_reports": self.receiver_reports,
                "rcvr_dial": self.receiver_dial,
                "rcvr_alarms": {k: [list(x) for x in v] for k, v
                                in self.receiver_alarms.items()},
                "relay_alarms": {k: [list(x) for x in v] for k, v
                                 in self.relay_alarms.items()},
                "line_disable": {
                    f"{kind}.{n}": [list(x) for x in v]
                    for (kind, n), v in self.line_disable_alarms.items()}}

    def _stores_load(self, blob):
        self.meter_map = {legacy_map_key(k, v): _map_entry(v) for k, v
                          in blob.get("meter_map", {}).items()}
        self.meter_offsets = {legacy_offset_key(k, v): v for k, v
                              in blob.get("meter_offsets", {}).items()}
        self.vmc_fuel_pos = {int(k): v for k, v
                             in blob.get("vmc_fuel_pos", {}).items()}
        self.receiver_reports = {int(k): v for k, v
                                 in blob.get("rcvr_reports", {}).items()}
        self.receiver_dial = {int(k): v for k, v
                              in blob.get("rcvr_dial", {}).items()}
        # tuples do not survive JSON, so the alarm keys come back as lists
        self.receiver_alarms = {
            int(k): [tuple(x) for x in v]
            for k, v in blob.get("rcvr_alarms", {}).items()}
        self.relay_alarms = {
            int(k): [tuple(x) for x in v]
            for k, v in blob.get("relay_alarms", {}).items()}
        # and neither does a (kind, number) key, so it is stored as
        # "plld.1" and split back
        self.line_disable_alarms = {
            (k.split(".")[0], int(k.split(".")[1])): [tuple(x) for x in v]
            for k, v in blob.get("line_disable", {}).items()
            if "." in k}

    def _clear_stores(self):
        for name in self.STORES:
            setattr(self, name, {})

    def seed(self, path):
        n = 0
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                code, _, data = line.rstrip("\n").partition("\t")
                if not code.startswith("S") or not data.strip():
                    continue
                try:
                    self.values[code.upper()] = bytes.fromhex(data).decode(
                        "ascii", "replace")
                except ValueError:
                    continue
                n += 1
        return n

    # ---- the board and the software on it ----------------------------------
    def software_info(self):
        """The revision block this console prints: version, part, date."""
        return versions.info(self.version, self.board)

    def supports(self, feature):
        """Is that feature in this console's cell of the manual's table?

        The gate under the card cage, and it asks about the board as well as
        the software: Table 3-1 to 3-5 of the Troubleshooting Guide give, for
        every feature at every version, the CPU boards that carry it. A
        console whose program was written before pressurised line leak
        detection existed has no code for a PLLD controller, so fitting one
        changes nothing: no setup function, no line to programme, and 9999
        to a tool that asks. A console with the wrong memory board is the
        same story one level down: Maintenance Tracker wants an NVMEM203 and
        sixteen tanks want an NVMEM201, and no console has both.
        """
        return versions.supports(self.version, self.board, feature)

    def family(self):
        """"1XX" or "3XX": which software family this board is running.

        The one that decides whether the console is the R: Reconciliation
        Mode is 3XX, as is the sixteenth tank.
        """
        return versions.family(self.board)

    def knows_step_code(self, step):
        """Is the code this step writes in this console's software yet?

        `alt` is the exception and it is not one: a part-field group like
        `611schedule` names a code the step reaches THROUGH the function it
        is already on, so the code to ask about is the step's own.
        """
        code = (step.get("code") or "").upper()
        if not code.startswith("S") or len(code) < 4:
            return True
        return versions.knows_token(code[1:4], self.version, self.board)

    def knows_module(self, module):
        """Is that card one this software can drive?

        Two gates, because two documents state them. Tables 3-1 to 3-5 give
        a feature per version per board, which is `supports`; and a card's
        own installation manual sometimes states its own, which those tables
        have no row for at all.
        """
        return (self.supports(versions.MODULE_FEATURE.get(module))
                and versions.module_allowed(self.version, self.board,
                                            module))

    def knows_option(self, option):
        """Is there a software key for that in this version?"""
        return self.supports(versions.SOFTWARE_FEATURE.get(option))

    def set_version(self, version):
        """Change the software. Everything it never heard of goes away."""
        if not versions.known(version):
            return False
        self.version = version
        self.save()
        return True

    def set_board(self, board):
        """Change the CPU board, which is the other half of the same question."""
        if board not in versions.BOARD:
            return False
        self.board = board
        self.save()
        return True

    # ---- modules -----------------------------------------------------------
    def has(self, module):
        return self.count(module) > 0

    def licensed(self, option):
        """Is that software module in the console, and can it be?

        A feature the S-Module does not carry is not on the menu: "this
        message will not appear unless the 0.20 Repetitive PLLD software
        module key is installed in your system." A key for a feature the
        software version predates is not a key that was ever cut.
        """
        return bool(self.software.get(option)) and self.knows_option(option)

    def count(self, module):
        """How many of that card the console can actually drive.

        The cage holds what somebody put in it; this is what the program in
        the console can do anything with, which on older software is none of
        an unrecognised card. `fitted` is the physical count.

        A communication card is counted by the POSITIONS the bay reads it on
        rather than by the cards in the slots, because a dual-port module is
        two of them and they are two different cards to the console: the
        multiport in slot 4 is an RS-485 port on 5 and an RS-232 port on 6.
        `fitted` is still the number of boards. See FIDELITY M7.
        """
        if not self.knows_module(module):
            return 0
        if module in COMM_IDENTITIES:
            return self.comm_count(module)
        return self.fitted(module)

    def fitted(self, module):
        """How many of that card are physically in the cage."""
        return int(self.modules.get(module) or 0)

    def wires(self, module):
        """How many devices one of that card carries.

        Fixed for every card but the smart sensor's, which is seven or eight
        depending on which of the family's two the site fitted. See
        FIDELITY M2.
        """
        if module == "smart" and self.smart_press:
            return SMART_PRESS["wires"]
        return MODULE_WIRES.get(module, 0)

    def variant(self, module):
        """The record for the card actually fitted, or None for the plain one.

        Two of this cage's keys are families of two: `smart` is the eight-
        input Smart Sensor module or the seven-input Smart Sensor/Press
        module, and `probe` is the Four-Input Probe Module or the
        Probe/Thermistor Interface Module. Everything that differs between
        the two of a pair goes through here, so that a third variant is one
        dict and one line rather than a branch in six methods. FIDELITY M2
        and M4.
        """
        if module == "smart" and self.smart_press:
            return SMART_PRESS
        if module == "probe" and self.probe_gt:
            return PROBE_GT
        return None

    def card(self, module):
        """(label, part number, ohms) for the card actually in the cage."""
        fitted = self.variant(module)
        if fitted is not None:
            return (fitted["label"], fitted["part"], fitted["ohms"])
        return (MODULE_LABEL.get(module, module), MODULE_PART.get(module, ""),
                MODULE_OHMS.get(module, 100000))

    def module_type(self, module):
        """"TT - Type of Module (Hex)", for the card actually in the cage.

        Function 102's own list names both halves of both families --
        `28=SmartSensor(8) Module` beside `2B=SmartSensor(7) Module`, and
        `01=Four Probe Module` beside `0A=Four Probe w/ Ground Temp Module`
        -- and this read the key's code, so a console with either variant in
        it reported the OTHER card down the port. The one report whose job
        is saying what is in the cage said the wrong thing about it, on the
        one field a reader cannot check against anything else.
        """
        fitted = self.variant(module)
        if fitted is not None and fitted.get("type"):
            return fitted["type"]
        return self.MODULE_TYPE.get(module, "00")

    def slot_name(self, module, paper=False):
        """What a slot line calls this card -- and they are not one name.

        576013-818 Rev AB Figure 6-2 draws the SCREEN: `SLOT 1 4 PROBE/ G. T.`
        over `POR=  XXXXXX   C=  XXXXXX`. 576013-635 Rev AA's function 102
        sample draws the PAPER: `1   4 PROBE / G.T.` against its own POWER ON
        RESET and CURRENT columns. No space before the slash and a space
        after it on the glass; a space either side and none inside `G.T.` on
        the printout. Two manuals, one card, two spellings, and the captured
        console prints the paper's.

        It is not one card's oddity. Function 102's sample and the captured
        console agree on a whole vocabulary of PRINTED board names that this
        console did not have, because `MODULE_SHORT` was read off Table 6-1
        -- the ID resistance table, which names cards for a technician with a
        meter and is not what the report prints. `MODULE_PAPER` holds the
        printed names that are attested; a card with no entry falls back,
        because most of the cage's cards appear in no sample and inventing a
        printed name for them would be worse than using the one the screen
        has. FIDELITY M4 and M20.
        """
        fitted = self.variant(module) or {}
        if paper:
            printed = fitted.get("paper") or MODULE_PAPER.get(module)
            if printed:
                return printed
        if fitted.get("short"):
            return fitted["short"]
        return MODULE_SHORT.get(module, MODULE_LABEL.get(module, module))

    def positions(self, code):
        """How many positions that CONFIG screen draws.

        "SLOT #: X X X X" is four probe positions and "SLOT # - X X" is two
        inputs; each one is its card's inputs. Only the smart sensor's
        depends on which card is in the cage. See FIDELITY M2.
        """
        if code == "721":
            return self.wires("smart")
        return SLOT_POSITIONS.get(code, 4)

    def capacity(self, module):
        """How many devices the cage carries of that kind.

        Two eight-input liquid sensor modules is sixteen sensors, and each
        module's own config screen switches on the eight positions it has.
        """
        return self.wires(module) * self.count(module)

    def most(self, module):
        """The most of that card this console takes.

        The card's own limit, except for probes: tanks 9 to 16 are their own
        line in the manual's table and they want 3XX software on a board with
        an NVMEM201, so a console without that stops at eight.
        """
        limit = MODULE_MAX.get(module, 1)
        if module == "probe" and not self.supports("tanks16"):
            return min(limit, 8 // MODULE_WIRES["probe"])
        return limit

    def bay_used(self, bay):
        """Slots taken. A card the software cannot drive still fills one."""
        return sum(self.fitted(k) for k, b in MODULE_BAY.items() if b == bay)

    def bay_slots(self, bay):
        """How many CARDS that compartment holds.

        The comm bay is the one where that is not the number of positions it
        answers on: four slots, six positions. See FIDELITY M6 and M7.
        """
        return COMM_SLOTS if bay == "comm" else BAY_SLOTS.get(bay, 0)

    def bay_free(self, bay):
        return self.bay_slots(bay) - self.bay_used(bay)

    def fits(self, module, count):
        """Would that many fit, in its own limit, and in its bay?"""
        if count < 0 or count > self.most(module):
            return False
        bay = MODULE_BAY.get(module, "power")
        if bay == "comm":
            return self._comm_fits(module, count)
        return (self.bay_used(bay) - self.fitted(module) + count
                <= BAY_SLOTS.get(bay, 0))

    def _comm_fits(self, module, count):
        """Is there a legal arrangement of the comm bay with that many?

        577013-528 Rev G: single-port modules go in slots 1, 2 or 3 and
        dual-port ones in slot 4, with a second dual-port module in slot 3
        only "by using a double dual-port wiring harness (P/N 332609-001)".
        So the bay takes four cards and at most one of them is dual-port
        unless the double harness is fitted.

        A fourth single-port card still FITS, because a technician can slide
        one into slot 4 and 576013-818 Table 7-2 exists to say what happens
        when they do. What it will not do there is communicate; see
        `comm_slot_works`.
        """
        cards = duals = 0
        for key, _n, _p, bay, _w, _m in MODULES:
            if bay != "comm":
                continue
            n = count if key == module else self.fitted(key)
            cards += n
            if self.comm_dual(key):
                duals += n
        return (cards <= COMM_SLOTS
                and duals <= (2 if self.double_harness else 1))

    def available_functions(self):
        """The setup functions this console's card cage can serve.

        And that its software knows about: a function carries the same
        `when` block a step does, so a chapter that arrived with a later
        version is not on FUNCTION at all before it.
        """
        out = []
        for fn in SETUP_MENU:
            need = FUNCTION_REQUIRES.get(fn["function"])
            if need is not None and not any(self.has(m) for m in need):
                continue
            option = FUNCTION_LICENSED.get(fn["function"])
            if option and not self.licensed(option):
                continue
            if not self.visible(fn, 1):
                continue
            out.append(fn)
        return out

    def available_diagnostics(self):
        """Diagnostic functions this card cage can serve.

        The troubleshooting manual states it outright: "Your system will
        display only the diagnostic functions of installed and configured
        modules and options." Same rule as Setup Mode, and SERVICE NOTICE
        SESSION carries a second one, "only appears if Service Notice".
        """
        def fitted(fn):
            need = fn.get("requires")
            if not need:
                return True
            cards = [need] if isinstance(need, str) else need
            return any(self.has(card) for card in cards)

        return [f for f in DIAG_MENU if fitted(f) and self.visible(f, 1)]

    def available_operating(self):
        """Operating-mode functions this card cage can serve.

        The operator's manual carries the same caveat as the other two modes:
        "only the Functions/Steps relevant to your console and its installed
        options and connected detection systems will be accessible".
        """
        def fitted(fn):
            need = fn.get("requires")
            if not need:
                return True
            cards = [need] if isinstance(need, str) else need
            return any(self.has(card) for card in cards)

        return [f for f in NORMAL_MENU if fitted(f) and self.visible(f, 1)]

    def available_reconciliation(self):
        """Reconciliation-mode functions, which need the BIR key.

        "Business Inventory Reconciliation is an option. You must have the
        BIR software module key installed to access this mode." The variance
        reports are gated a second time, on the Setup Mode switches that turn
        each family of them on.
        """
        # "Reconciliation Mode (TLS-350R Only)" and "you must have the BIR
        # software module key installed to access this mode": and the R is
        # 3XX software, which is a fact about the board and not the badge.
        if self.family() != "3XX":
            return []
        if not (self.licensed("bir") and self.has("probe")):
            return []
        return [f for f in RECON_MENU if self.visible(f, 1)]

    # ---- screens that only appear when they should -------------------------
    def visible(self, step, device):
        """Is this screen on the console right now?

        The manuals annotate the conditional ones by hand, "this message
        appears only if you select METER DATA PRESENT: YES", "if Mass/Density
        is disabled this window will not appear", "visible only if Relay
        assigned": so each one carries what it depends on, and a screen whose
        condition is false is not there to step onto at all. Every condition
        on a screen has to hold, not just the first.
        """
        # A step writes a function code, and a console cannot write a code
        # its software has never heard of. `available_functions` already
        # hides a whole CHAPTER that arrived later -- "a chapter that
        # arrived with a later version is not on FUNCTION at all before it"
        # -- and this is the same rule one level down, on the steps inside a
        # chapter that has always existed. 576013-635 heads every code with
        # the version it arrived in and `versions.knows_token` is the gate
        # the WIRE has used all along; asked through the panel instead, a
        # Version 15 console with a full cage walked twenty-five steps whose
        # code postdates it -- a Vapor Loss Factor from v29, a Probe Offset
        # from v22, the whole of PUMP RELAY MONITOR SETUP from v27. See
        # FIDELITY F13, and S10 for the wire half.
        if not self.knows_step_code(step):
            return False
        cond = step.get("when")
        if not cond:
            return True
        for also in cond.get("and") or []:
            # One `code` per condition is not always enough. CSLD's
            # evaporation compensation is on the CSLD walk AND on a climate
            # factor of EXTREME, which is two stored values, so a condition
            # can carry more conditions. Every one of them has to hold --
            # what the docstring above has always said.
            if not self.visible({"when": also}, device):
                return False
        if cond.get("tanks") and not self.programmed_tanks():
            return False
        if cond.get("mag_sensor") and not self.mag_sensors():
            # "This menu displays only if the console detects a Mag Sump
            # Sensor capable of leak detection", 576013-610 Rev AC p.23-1
            # and p.24-3, over both Mag sump functions. FIDELITY U1b.
            return False
        if cond.get("water_probe") and not self.probe_detects_water(device):
            # "This message does not appear for tanks in which high alcohol
            # probes are installed", 576013-623 p.7-14 and p.7-15, against
            # the water warning, the high water limit and the water alarm
            # filter. A high alcohol probe is a one-float probe: no water
            # float, so no water reading, so no water screens. Table 9-2
            # says which circuit codes those are and PROBE_MODELS carries it.
            return False
        if cond.get("software") and not self.licensed(cond["software"]):
            return False
        if cond.get("shift_times") and not self.shifts.programmed():
            # "At least one Shift Start Time must be entered to activate the
            # 'Last Shift Inventory' feature", 576013-623 Rev AN p.5-4 --
            # which is a condition on the SETTING, where this function was
            # gated on the BIR key chapter 8 never mentions. FIDELITY O22.
            return False
        if cond.get("isd_hoses") and not self.isd_hoses():
            # "appears only after completing Fuel Hose Table Setup"
            return False
        if cond.get("vst_processor") and (
                (self.values.get("SV4000") or "00").strip()[-2:]
                not in isd.VST_PROCESSORS):
            # PMC SETUP: "the vapor processor type VST must have been
            # selected in EVR/ISD setup to access PMC setup" (937-J p.27).
            # VST is two of V40's codes: the same figure is headed "PMC
            # Setup for VST Processors" and its own On/Off example gives a
            # turn on and a turn off pressure for the "ECS Membrane" and for
            # the "Green Machine". See FIDELITY I7.
            return False
        if cond.get("feature") and not self.supports(cond["feature"]):
            # a screen for something this software version predates
            return False
        if cond.get("ecpu") and not self.has_ecpu():
            # "Appears with ECPU board only. The Peripheral Controller (PC)
            # is the second processor (H8) on the ECPU board" -- 576013-818
            # Rev AB Figure 6-2, against the PC DIAGNOSTIC DATA block. A
            # plain CPU has one processor and nothing to report about a
            # second. See FIDELITY D10.
            return False
        slot = cond.get("board")
        if slot:
            # Which card is in THIS comm slot, not whether the console has
            # one anywhere. The modem screens -- DIAL TYPE, ANSWER ON,
            # SELECT MODEM, the setup string, the dial tone interval -- were
            # gated on the console owning a modem, so a site with a modem in
            # slot 2 got all five of them on its RS-232 in slot 1 as well.
            # By the name the position READS, which is the card's identity
            # rather than the board it is half of: the Maintenance Tracker
            # port on a dual-port module answers MTCOMM the same as the
            # single-port card does.
            cards = [slot] if isinstance(slot, str) else slot
            if self.comm_board_name(device) not in [COMM_NAME.get(c)
                                                    for c in cards]:
                return False
        want = cond.get("no_module")
        if want:
            # A screen that belongs to the console WITHOUT that card. Fig
            # 6-3 splits SERVICE REPORT in two: a console with a
            # Maintenance Tracker takes the technician's identity off the
            # key, so it is the console without one that has to ask for an
            # ID. See FIDELITY D3.
            cards = [want] if isinstance(want, str) else want
            if any(self.has(card) for card in cards):
                return False
        want = cond.get("module")
        if want:
            # "appears only for systems equipped with a modem module": and
            # some screens want any one of a family of cards
            cards = [want] if isinstance(want, str) else want
            if not any(self.has(card) for card in cards):
                return False
        several = cond.get("any_setting")
        if several:
            where = device if cond.get("device") else 0
            wanted = cond.get("is", [])
            if not any(self.setting(key, where,
                                    FIELDS.get(f"set.{key}", {}).get(
                                        "default", "")) in wanted
                       for key in several):
                return False
        want = cond.get("setting")
        if want:
            where = device if cond.get("device") else 0
            field = FIELDS.get(f"set.{want}", {})
            now = self.setting(want, where, field.get("default", ""))
            if now not in cond.get("is", []):
                return False
        if cond.get("not_siphon_secondary") and self.siphon_secondary(device):
            # "If this is a siphon manifolded secondary tank, this window
            # will not appear!" -- the condition is the screen's ABSENCE, so
            # it reads as what has to be untrue. See FIDELITY F6.
            return False
        if "relay_assigned" in cond:
            # "If pump relay assigned" against "If pump relay = NONE",
            # 576013-818 Figure 6-16's two columns. This console drew both
            # at once. See FIDELITY D1.
            from . import wiresensors
            if bool(wiresensors.monitored_kind(self, device)) != cond[
                    "relay_assigned"]:
                return False
        if "meter_events" in cond and bool(self.bir.events) != cond["meter_events"]:
            # "If there is no data in Meter Events Table, you see" METER
            # EVENTS TABLE EMPTY; "if there is data ... " PRESS <ENTER>.
            # 576013-818 Figure 6-25. See FIDELITY D14.
            return False
        if "last_event" in cond and (
                (self.bir.last_event() or {}).get("kind")
                != cond["last_event"]):
            # and then the figure's second branch, on whether the newest row
            # is a Start or an End
            return False
        if cond.get("has_delivery") and not self.deliveries.last(device):
            # "NOTE: this display is not shown if a delivery has not
            # occurred" -- 576013-610 Rev AC p.4-5, of the LAST DELIVERY
            # density screen. See FIDELITY O13.
            return False
        if cond.get("chart_secured") and not self.chart_secured():
            # the Weights and Measures block belongs to a locked chart
            return False
        if "profile" in cond and self.tank_profile(device) not in cond["profile"]:
            return False
        if "code" not in cond:
            return True
        code = cond["code"]
        full = code if code[4:6] == "00" else f"{code[:4]}{device:02d}"
        raw = self.values.get(full.upper())
        if raw is None:
            value = cond.get("default", "")
        else:
            value = (raw[2:] if self.is_prefixed(code[1:4]) and len(raw) > 2
                     else raw)
            part = cond.get("part")
            if part:
                value = value[part[0]:part[0] + part[1]]
            value = value.strip()
        if "is" in cond:
            return value in cond["is"]
        if "not" in cond:
            return value not in cond["not"]
        return bool(value)

    def visible_steps(self, function, device):
        """A function's steps, minus the ones this console is not showing."""
        return [st for st in function["steps"] if self.visible(st, device)]

    def live_reading(self, token, device):
        """What a gauge would be showing right now.

        Operating mode is where a technician LOOKS at the site, so these come
        from the physical state the bench is driving, not from stored setup.
        Drag the slider and the inventory screen moves with it, which is the
        difference between a menu that walks and a console that gauges.
        """
        if not token:
            return ""
        if token.startswith("isd_"):
            # the ISD status screens read from the same machinery the wire
            # answers V01 with, so panel and port never disagree
            from . import wire as _wire
            from . import isd as _isd
            h = _wire.Handler(self, verbose=False)
            if token == "isd_daily_date":
                return ("REPORT DATE: "
                        + clock_date(self.now(), sep=" "))
            if token == "isd_monthly_date":
                return "REPORT DATE: " + time.strftime(
                    "%b %Y", self.now()).upper()
            overall, collect, contain, processor = h._isd_status()
            if token == "isd_st_stage1":
                since = self._commissioned or 0.0
                p, t = h._isd_stage1(since, time.mktime(self.now()) + 1)
                return f"STATUS:  {p} of {t}  PASS"
            state = {"isd_st_contain": contain,
                     "isd_st_collect": collect}.get(token, overall)
            word = {_isd.PASS: "PASS", _isd.WARNING: "WARN",
                    _isd.FAILURE: "FAIL"}.get(state, "UNKNOWN")
            return f"STATUS: {word}"
        if token.startswith("shift_"):
            # No unit, and a six-wide field, which is what 576013-610
            # Rev AC draws for all four of these screens:
            #
            #   p.8-1  `BEGIN INVENTORY: XXXXXX`   `END INVENTORY: XXXXXX`
            #   p.8-2  `DLVY ADJUSTMENT: XXXXXX`   `GROSS CHANGE:  XXXXX`
            #
            # and the printed Shift Starting Inv report agrees with its own
            # screens: its VOLUME and ULLAGE rows carry units and its
            # DLVY ADJUSTMENT, GROSS CHANGE and TC NET CHANGE rows are bare
            # numbers. One rule across the screen and the paper.
            #
            # This appended ` GALS` to an eight-wide field, so on a tank
            # with five figures in it the line came to 25 and 27 characters
            # and the glass cut the unit in half: `BEGIN INVENTORY: 14000 G`
            # reads as a stray letter, and a trainee cannot tell whether it
            # is a unit, a flag, or a digit that did not fit. See the
            # operating-mode audit, OP3.
            #
            # And no padding, because the padding never survived: the panel
            # renders a `reading` template around `live.strip()`, so the
            # eight-wide field was doing nothing but making the line longer
            # before the unit was added to it. The manual's `XXXXXX` is a
            # placeholder for the digits, not a column to pad to.
            #
            # And off Last-Shift Inventory's own shifts. This read BIR's
            # shift row, so the screens showed BIR's period under a head
            # that should name one of four programmed shifts, and GROSS
            # CHANGE was end less beginning where p.8-2 defines it as
            # beginning less end plus the ticketed deliveries. The panel
            # asks `shifts` with the shift it is standing on; this is the
            # running one. FIDELITY O22.
            return self.shifts.shown(token[6:], None, device)
        if token.startswith("recon_"):
            return self.recon_reading(token[6:], device)
        if token == "csld_current":
            return self.csld.status_line(device)
        if token == "csld_last":
            return self.csld.last_pass(device)
        if token.startswith("result_"):
            _, kind, rate_key = token.split("_", 2)
            return self.leaks.status_line(kind, device, rate_key)
        if token.startswith("sensor_"):
            return self.sensor_reading(token[7:], device)
        if token.startswith("sump_"):
            return self.sump_screen(token[5:], device)
        if token == "relay_status":
            # 576013-610 Rev AC p.67 draws "r#: (Location)" over a status
            # indicator, and Table 29-21 names the two: PUMP RELAY NORMAL
            # when nothing is wrong, PUMP RELAY ALARM when the pump is still
            # running after it was told to stop.
            return ("PUMP RELAY ALARM" if self.relay_stuck(device)
                    else "PUMP RELAY NORMAL")
        if token == "relay_test":
            # 576013-610 Rev AC p.22-1 draws the third screen of the relay
            # test as "R 1: (Device Name)" over "ON - PRESS ANY KEY", and
            # its flow chart annotates the same step "Display reads: Relay X
            # On/Off. Press any key to printout Relay Setup". So the value
            # is the state and the step spells the rest. See FIDELITY O6.
            return "ON" if self.relays.get(device) else "OFF"

        st = self.tank_level.get(device)
        if st is None:
            return ""
        full = self.full_volume(device)
        vol = st.get("volume", 0.0)
        # The programmed density if there is one, and this tank's own reading
        # if there is not. It used to fall back to a flat 6.0 while i215 was
        # answering product_density for the same tank, so the panel and the
        # wire disagreed about one tank.
        dens = self.product_density(device)
        if token == "volume":
            # and a whole-number quantity is TRUNCATED, not rounded:
            # 576013-610 says so twice, on two pages, about two
            # different quantities. See `masks.whole` and FIDELITY Y11.
            return f"{masks.whole(vol, 8)} GALS"
        if token == "tc_volume":
            return f"{masks.whole(self.tc_volume(device), 8)} GALS"
        if token == "ullage":
            return f"{masks.whole(max(full - vol, 0), 8)} GALS"
        if token == "ullage95":
            return f"{masks.whole(max(full * 0.95 - vol, 0), 8)} GALS"
        if token == "height":
            return f"{self.height_at(device, vol):8.2f} INCHES"
        if token == "water":
            return f"{self.water_height(device):8.2f} INCHES"
        if token == "water_vol":
            return f"{masks.whole(self.water_volume(device), 8)} GALS"
        if token == "temperature":
            # product_temperature has been here all along, per tank and
            # moving, and this answered a literal 55.0 while the probe
            # diagnostics and IFSF answered the real one for the same tank.
            return f"{self.product_temperature(device):8.1f} DEG F"
        if token == "density":
            return f"{dens:8.4f} LBS/GAL"
        if token == "mass":
            return f"{masks.whole(vol * dens, 8)} LBS"
        if token == "delivery":
            # "To view the inventory increase for a tank (the last delivery
            # amount) ... DELIVERY = XXXXX (UNITS)"
            if self.deliveries.in_progress(device):
                return "DELIVERY IN PROGRESS"
            # A tank that has taken no delivery has taken nought gallons.
            # This is a READING, and a console draws a reading as a number:
            # 576013-610 Rev AC p.31 gives it no other form.
            last = self.deliveries.last(device)
            return f"{masks.whole(last.amount if last else 0, 8)} GALS"
        if token in ("next_density", "last_density"):
            # 576013-610 Rev AC p.4-5 draws these two as `T 1: NEXT DELIVERY`
            # over a bare `DENSITY = X.XXXX` -- no units, and not the tank's
            # own density. What they hold is what somebody entered for the
            # delivery: 0 until they do, and 0 again for NEXT once the
            # delivery report has printed. See FIDELITY O13.
            which = "0" if token == "next_density" else "1"
            return f"{self.delivery_density_value(device, which):.4f}"
        if token.startswith("fuel_"):
            # FUEL MANAGEMENT is per PRODUCT, so `device` here is the
            # product's number and not a tank's. See FIDELITY O4.
            return self.fuel_reading(token, device)
        if token == "days_fuel":
            # Days of fuel remaining is inventory divided by average daily
            # sales, and with no sales figure there is nothing to divide by.
            # "NO RESULTS AVAILABLE" is this console's own status message for
            # a figure it cannot report yet -- 576013-818 Rev AA gives it a
            # section of its own, "Status Message: NO RESULTS AVAILABLE".
            sales = self.limit("683", device) or 0.0
            return (f"{vol / sales:8.1f} DAYS" if sales
                    else "NO RESULTS AVAILABLE")
        if token == "avg_sales":
            return f"{self.limit('683', device) or 0:8.0f} GALS"
        return ""

    def recon_reading(self, what, tank, kind=None, previous=None, day=None):
        """One line of a Reconciliation Mode report, as the panel shows it.

        The mode displays the same numbers the report prints, "one item at a
        time", so the screens and the printout read off one row.
        """
        kind = kind or self.recon_kind
        previous = self.recon_previous if previous is None else previous
        row = self.bir.row(tank, kind, previous, day)
        if row is None:
            return "NO DATA AVAILABLE"
        if what in ("open_date", "open_time", "close_date", "close_time"):
            when = row["opened"] if what.startswith("open") else row["closed"]
            if what.endswith("date"):
                return clock_date(when)
            return clock_hhmm(when)
        if what == "book":
            return f"{self.bir.book(row):8.0f} GALS"
        if what == "threshold":
            return f"{self.bir.threshold(row):8.0f} GALS"
        if what == "water":
            return f"{row['water']:8.2f} INCH"
        if what in row:
            return f"{row[what]:8.0f} GALS"
        analysis = self.bir.analysis(row)
        if what == "book_var":
            # 576013-610 Rev AC p.28-11 lists what STEP walks "one item at a
            # time" and ends with the PAIR: "book variance ... AND % VARIANCE
            # SALES (book variance divided by sales)". The report on p.28-14
            # puts them on one row, `VAR            : 800 GAL 280.7%`, and
            # the printer already did; the screen dropped the percentage. See
            # FIDELITY Q2.
            #
            # That page's parenthesis reads "difference between gauged
            # volume and book inventory", which is the SIGN the samples
            # overrule -- both of them print 800 for a book of 9704 against a
            # gauge of 8904. The pairing is what this citation is for, not
            # the direction. See G9.
            return (f"{analysis['book_var']:.0f} GAL "
                    f"{analysis['book_pct']:.1f}%")
        if what == "book_pct":
            return f"{analysis['book_pct']:8.2f} %"
        if what == "water_change":
            return f"{analysis['water_change']:8.2f} INCH"
        if what in analysis:
            return f"{analysis[what]:8.0f} GALS"
        return ""

    # ---- vapour monitor controllers ----------------------------------------
    VMC_SIDES = ("A", "B")

    def vmc_side(self, number, side):
        """One side of one controller, as the bench is driving it.

        A controller with nothing happening reads IDLE with its counters at
        zero, which is what a quiet forecourt looks like; the bench moves
        them the way it moves a tank level.
        """
        return self.vmc_state.setdefault(
            (int(number), side),
            {"status": "IDLE", "rate": 0.0, "fuel": 0, "error": 0,
             "remain": 0})

    # 576013-610 Rev AC Table 29-23, the VMC's own message table, is four
    # rows, and every one of them is a STATUS the controller reports:
    #
    #   VMC COM TIMEOUT  Alarm    "posts if a VMC is powered off, not
    #                              connected or the wrong serial number has
    #                              been entered"
    #   METR NC ALM      Alarm    "Dispenser's meter not connected"
    #   FP SHUTDWN WRN   Warning  "Fuel position shutdown warning"
    #   FP SHUTDWN ALM   Alarm    "Fuel position shutdown alarm"
    #
    # They are Table 5-1's category 36 in the same order, and they are four of
    # `VMC_STATUS_CODE`'s eight words -- so the alarm IS the status, and the
    # console does not decide it, it is told it. The other four are the states
    # a healthy controller reports. See FIDELITY M12 and N1.
    VMC_ALARM_NN = {"VMC COMM TIMEOUT": "01",
                    "METER NOT CONNECTED": "02",
                    "FP SHUTDOWN WARNING": "03",
                    "FP SHUTDOWN ALARM": "04"}

    def vmc_conditions(self):
        """Category 36, from what each controller is reporting.

        A controller has two sides and the alarm record has one device field,
        so a controller alarms when EITHER side does. That is forced by the
        `AANNTT` shape rather than chosen: 412's own report is a block per
        VMC with no side column in it either.
        """
        out = []
        for number in self.vmc_numbers():
            found = set()
            for side in self.VMC_SIDES:
                held = self.vmc_state.get((int(number), side)) or {}
                nn = self.VMC_ALARM_NN.get(str(held.get("status", "")).upper())
                if nn:
                    found.add(nn)
            out.extend(f"36{nn}{int(number):02d}" for nn in sorted(found))
        return out

    def set_vmc_status(self, number, side, status):
        """What the bench does to a controller: tell it what it is saying."""
        self.vmc_side(number, side)["status"] = status.upper()

    def vmc_serial(self, number):
        """The serial number 8C1 sets and BB1 and 412 report.

        A controller that has never been given one still has one -- the
        screen 576013-610 Rev AC p.91 draws is `x 1: 005830`, never `x 1:` --
        so an unprogrammed controller answers a stable made-up number rather
        than a blank. Six digits, and the manual's own example leads with a
        zero, so the range starts below 100000.
        """
        held = self.vmc_serials.get(int(number)) or self.values.get(
            f"S8C1{int(number):02d}")
        if held:
            return str(held)[-6:]
        return f"{readings.integer(1000, 999999, 'vmcsn', number):06d}"

    # V88's two verdicts share one table, which is worth noting in a manual
    # where neighbouring codes usually disagree about exactly this.
    TEST_WORDS_TABLE = {"00": "NO TEST", "01": "WARN", "02": "FAIL",
                        "03": "PASS"}

    VMC_STATUS_CODE = {"METER NOT CONNECTED": "00", "IDLE": "01",
                       "RUNNING": "02", "LAST TRANSACTION FAILED": "03",
                       "FP SHUTDOWN WARNING": "04", "FP SHUTDOWN ALARM": "05",
                       "STATUS UNKNOWN": "FE", "VMC COMM TIMEOUT": "FF"}

    def vmc_status_code(self, number, side):
        """BB1's packed status, from the words the bench is showing."""
        said = self.vmc_side(number, side)["status"].upper()
        return self.VMC_STATUS_CODE.get(said, "FE")

    def vmc_reading(self, number, side, what):
        """One line of the VMC report, in the console's own words."""
        values = self.vmc_side(number, side)
        if what == "status":
            return f"STATUS: {values['status']}"
        if what == "rate":
            return f"RECOVER RATE: {values['rate']:.1f}"
        if what == "fuel":
            return f"FUEL COUNTER: {values['fuel']:.0f}"
        if what == "error":
            return f"ERROR COUNTER: {values['error']:.0f}"
        if what == "remain":
            return f"REMAIN TIME: {values['remain']:.0f}"
        return ""

    def sump_screen(self, what, sensor):
        """The Mag Sump Leak Test screens, 576013-610 Rev AC p.23-1 and p.24-3.

        `ht_temp` is the sump as it stands, `rates` the pair the manual gives
        four states for, `status` the test's outcome or its phase, and
        `last_passed` the results function's own screen. They are the test's
        now (sumptest.py): these drew a height and a temperature fixed per
        sensor, a leak rate nobody measured and the date of the moment you
        looked. FIDELITY U1b.
        """
        return self.sumps.screen(what, sensor)

    def vmc_head(self, number, side=None):
        """"x 1: 005830 SIDE A": the device letter, its serial and the side."""
        head = f"x {number}: {self.vmc_serial(number)}".rstrip()
        return f"{head} SIDE {side}" if side else head

    def vmc_alarm_head(self, category, number):
        """The device columns of 411's and 412's first row for one device.

        411 heads a block with the board number alone, right aligned under
        `DEVICE`; 412 heads it with the controller number and the serial the
        console holds for it, which is why 412's header has an S/N column and
        411's does not.
        """
        if category == "35":
            return f"{int(number):6d}"
        return f"{int(number):2d}   {self.vmc_serial(number)}"

    def vmc_numbers(self):
        """Every controller the interface module can carry."""
        return list(range(1, max(self.capacity("vmc"), 1) + 1))

    def stick_height(self, tank, volume=None):
        """"fuel height (without tilt) + stick offset", clamped to the tank.

        "If the stick height is less than zero, it will be set to zero. If the
        stick height is greater than tank diameter, it will be set to tank
        diameter."

        `volume` asks what the stick read at a level the tank HELD rather
        than the one it holds. Reports that print two heights want it: C09's
        STRT HT and END HT are the two ends of a reconciliation day, and
        with no way to ask, both of them were this reading twice. See
        FIDELITY G12.
        """
        diameter = self.limit("607", tank) or 96.0
        if volume is None:
            volume = self.tank_level.get(tank, {}).get("volume", 0.0)
        height = self.height_at(tank, volume)
        height += self.limit("60C", tank) or 0.0
        return max(0.0, min(height, diameter))

    def height_at(self, tank, volume):
        """How deep that many gallons stand, which is volume_at backwards.

        A charted tank walks its own points; an uncharted one is the circle.
        576013-610 works both of its examples on a 10,000 gallon, 96 inch
        tank: 9038 gallons stands 81.37 inches and 8518 stands 76.26.
        """
        diameter = self.limit("607", tank) or 96.0
        full = self.full_volume(tank) or 0.0
        points = sorted(self.chart_points(tank))
        if not points:
            if not full:
                return 0.0
            if self.tank_profile(tank) == "03":
                return diameter * max(0.0, min(volume / full, 1.0))
            return diameter * cylinder_depth(volume / full)
        if volume <= points[0][1]:
            return points[0][0]
        for (h1, v1), (h2, v2) in zip(points, points[1:]):
            if volume <= v2:
                span = (v2 - v1) or 1.0
                return h1 + (h2 - h1) * (volume - v1) / span
        return points[-1][0]

    def water_minimum(self, tank):
        """The Programmable Minimum Water Threshold, in inches.

        Two codes hold this, for two different floats, and the panel step
        was writing the wrong one. 576013-635 Rev AA p.17372 is `Set Probe
        Water Minimum 648`, `<SOH>S648TTI.hhh`, and 576013-623 Rev AN p.7-16
        is its screen: "enter the required threshold within the range of 0.0
        to 1.0 inches ... WATER MINIMUM: 0.800", with the note "This message
        does not appear when Float Type has been set to Custom". `60E`, Set
        Tank Programmable Float Parameters, carries a fourth column of the
        same name for the CUSTOM float, and its own note is the mirror
        image: "CUSTOM float size must be chosen (Function Code 62F) for
        these parameters to be set and used."

        So the two never both apply, and which one a console reads is
        decided by the float size. IN-TANK SETUP's WATER MINIMUM step was
        writing 60E on a console that is not custom -- and the tape's own
        IN-TANK block prints that row, on a console with a 2 inch float.
        See FIDELITY R13 and Y7.
        """
        if self.float_size(tank) == "9":
            return max(0.0, min(1.0, self._float_parameter(tank, 3) or 0.0))
        return max(0.0, min(1.0, self.limit("648", tank) or 0.0))

    def _float_parameter(self, tank, which):
        """One of 60E's four floats, or None.

        "1. Water Offset 2. Fuel Offset 3. Invalid Fuel Level 4. Minimum
        Water Level", 576013-635 Rev AA's own numbering of the computer
        record, eight ASCII hex characters each.
        """
        raw = self.values.get(f"S60E{int(tank):02d}")
        if not raw:
            return None
        body = raw[2:] if len(raw) > 2 else raw
        chunk = body[which * 8:which * 8 + 8]
        if not chunk.strip():
            return None
        try:
            return packed.unhexfloat(chunk)
        except ValueError:
            return None

    def water_height(self, tank):
        """The water in the tank, which is not the same as the float's depth.

        "When there is not water in the tank, but the water height
        measurement is not 0.0, the water float is resting on a layer of
        debris on the bottom of the tank. The Water Minimum Threshold sets
        the level." 576013-623 Rev AN p.7-16, and the range it offers is 0.0
        to 1.0 inches.

        So this is a CORRECTION to the measurement rather than a gate on the
        alarm: below the threshold the console has not found water, it has
        found the bottom of the tank, and everything downstream -- the water
        volume, the water height screen, both water alarms -- follows from
        that one place. The default is zero, which is a console that has not
        been told about its debris and reports the float where it is.
        """
        floated = self.tank_level.get(tank, {}).get("water", 0.0) or 0.0
        return 0.0 if floated <= self.water_minimum(tank) else floated

    def water_volume(self, tank):
        """The gallons standing under the product.

        The chart read at the water height, which is all it ever was. This
        used to be the water height times twelve, and 576013-610's own
        example says otherwise: 1.37 inches of water in a 10,000 gallon, 96
        inch tank is 28 gallons, and twelve times 1.37 is sixteen.
        """
        return self.volume_at(tank, max(0.0, self.water_height(tank)))

    def volume_at(self, tank, height):
        """What the tank chart says is in the tank at that height.

        A charted tank interpolates between its own points; an uncharted one
        is the straight line the console draws through full volume.
        """
        diameter = self.limit("607", tank) or 96.0
        full = self.full_volume(tank) or 0.0
        points = sorted(self.chart_points(tank))
        if not points:
            if not diameter:
                return 0.0
            standing = max(0.0, min(height, diameter)) / diameter
            # "This method requires only the 100% (full) volume to profile the
            # tank. When using the linear tank profile you must enter the
            # inside HEIGHT of the tank in place of the inside diameter...
            # This profile can be used for flat-ended cylindrical tanks
            # standing on end and for rectangular tanks." 576013-623 Rev AN
            # p.7-6. Both of those have a constant cross-section, so the
            # volume is the straight line through the full one -- which is
            # the whole difference from 1PT, whose one volume is interpolated
            # through a HORIZONTAL cylinder's chord instead.
            if self.tank_profile(tank) == "03":
                return full * standing
            return full * cylinder_part(standing)
        if height <= points[0][0]:
            return points[0][1]
        for (h1, v1), (h2, v2) in zip(points, points[1:]):
            if height <= h2:
                span = (h2 - h1) or 1.0
                return v1 + (v2 - v1) * (height - h1) / span
        return points[-1][1]

    def chart_pairs(self, tank, step=1.0):
        """[(height, volume)] from the bottom of the tank to the top."""
        diameter = self.limit("607", tank) or 96.0
        step = max(step, 0.010)
        out, height = [], 0.0
        while height <= diameter + 1e-6 and len(out) < 4000:
            out.append((height, self.volume_at(tank, height)))
            height += step
        return out

    def chart_table(self, tank, step=1.0):
        """I211's four column calibration chart."""
        label = self.text("602", tank) or f"TANK {tank}"
        diameter = self.limit("607", tank) or 96.0
        out = ["TANK CALIBRATION CHART", f"TANK {tank}", label,
               f"{self.full_volume(tank):.0f} GALLONS",
               f"{diameter:.2f} INCHES", "",
               "DEPTH   CAPACITY    DEPTH   CAPACITY"
               "    DEPTH   CAPACITY    DEPTH   CAPACITY",
               "INCHES   GALLONS    INCHES   GALLONS"
               "    INCHES   GALLONS    INCHES   GALLONS",
               "-" * 77]
        pairs = self.chart_pairs(tank, step)
        rows = (len(pairs) + 3) // 4
        for row in range(rows):
            line = ""
            for column in range(4):
                i = row + column * rows
                if i >= len(pairs):
                    continue
                height, volume = pairs[i]
                line += f"{height:6.3f} {volume:10.0f}    "
            out.append(line.rstrip())
        return out

    def leak_history_records(self, tank):
        """[(report type, month number, Result)] behind I207.

        "00=Last Test Passed, 01=Fullest Test Passed, 02=Fullest Periodic
        Monthly Test Passed": so the last pass of each rate, the fullest
        pass, and the fullest periodic pass of each month.
        """
        log = [r for r in (self.leaks.history.get(("tank", tank)) or [])
               if r.result == leaktest.PASSED]
        out = []
        for rate_key in ("gross", "annual", "periodic"):
            mine = [r for r in log if r.rate_key == rate_key]
            if not mine:
                continue
            out.append(("00", 0, mine[-1]))
            fullest = max(mine, key=lambda r: r.volume)
            out.append(("01", 0, fullest))
        months = {}
        for record in log:
            if record.rate_key != "periodic":
                continue
            key = time.strftime("%Y%m", time.localtime(record.started))
            if key not in months or record.volume > months[key].volume:
                months[key] = record
        for number, key in enumerate(sorted(months)[-12:], start=1):
            out.append(("02", number, months[key]))
        return out

    def leak_test_method(self, tank, rate_key):
        """"STANDARD" or "CSLD" for one test, which is per TEST and not per
        tank.

        576013-635 Rev AA p.65's I207 sample prints both on one tank:
        `LAST GROSS TEST PASSED: ... STANDARD` over `LAST PERIODIC TEST
        PASS: ... CSLD`. CSLD is a 0.2 gph continuous method, so it IS the
        tank's periodic test; the gross test is a discrete one the console
        runs after a delivery and is Standard whatever the tank is
        programmed to. Reading `csld.enabled(tank)` alone put CSLD in the
        gross row. See FIDELITY H14.
        """
        return ("CSLD" if rate_key == "periodic" and self.csld.enabled(tank)
                else "STANDARD")

    # 576013-635 Rev AA p.65's `I207` sample, block by block, and every one
    # of its titles is cut to the twenty-four column roll:
    #
    #     LAST GROSS TEST PASSED:          23
    #     LAST ANNUAL TEST PASSED:         24
    #     FULLEST ANNUAL TEST PASS         24
    #     LAST PERIODIC TEST PASS:         24   -- `PASSED:` would be 26
    #     FULLEST PERIODIC TEST            21   } one title, two lines
    #     PASSED EACH MONTH:               18   }
    #
    # This console built all of them from one f-string, so the periodic one
    # came out `LAST PERIODIC TEST PASSED:` at twenty-six characters; it
    # printed a `FULLEST PERIODIC TEST PASS` block the sample does not have,
    # because the periodic fullest IS the monthly list; and it ran the
    # monthly title onto one forty-character line. See FIDELITY H3.
    HISTORY_BLOCKS = (
        ("gross", "00", ("LAST GROSS TEST PASSED:",)),
        ("annual", "00", ("LAST ANNUAL TEST PASSED:",)),
        ("annual", "01", ("FULLEST ANNUAL TEST PASS",)),
        ("periodic", "00", ("LAST PERIODIC TEST PASS:",)),
        (None, "02", ("FULLEST PERIODIC TEST", "PASSED EACH MONTH:")),
    )

    def leak_history_lines(self, tank):
        """The same history, in the columns I207 prints it in."""
        full = self.full_volume(tank) or 0.0
        head = ("TEST START TIME            HOURS    VOLUME"
                "   % VOLUME   TEST TYPE")

        def block(title, records):
            out = list(title)
            if not records:
                out.append("NO TEST PASSED")
                return out
            out.append(head)
            for record in records:
                pct = (record.volume / full * 100.0) if full else 0.0
                kind = self.leak_test_method(tank, record.rate_key)
                # A GROSS test's HOURS column is blank on the manual's own
                # paper, in both the 207 and the 208 sample, where this
                # printed `0`. A three-gallon-an-hour test is over in
                # minutes and the console does not report its length.
                hours = ("%8.0f" % record.hours
                         if record.rate_key != "gross" else " " * 8)
                out.append(f"{clock_words(record.started):22s}"
                           f"{hours}{record.volume:10.0f}"
                           f"{pct:11.1f}{kind:>12s}")
            return out

        found = self.leak_history_records(tank)
        out = []
        for rate_key, what, title in self.HISTORY_BLOCKS:
            rows = [r for kind, _n, r in found
                    if kind == what and (rate_key is None
                                         or r.rate_key == rate_key)]
            out += block(title, rows)
        return out

    # The panel's VAPOR PROCESSOR TYPE screen and V40 are ONE field. It was
    # two: the screen wrote a setting nothing ever read, while the PMC SETUP
    # gate and every ISD report read `values["SV4000"]`, so the only way to
    # reach PMC setup was to send SV4000 down the socket. A store nothing
    # reads back cannot be seen to be wrong, which is the kind that drifts --
    # `water_filter` on 642 and `inventory_units` on 550 keep no such copy,
    # and this follows them. See FIDELITY I7, and F9 for the shape.
    VALUE_SETTINGS = {"evr_vp_type": "SV4000"}

    # And the same shape again on V4E, whose ONE value is the panel's first
    # two EVR/ISD SETUP steps. Each entry is (code, offset, table): the
    # table is the wire's, so the words the panel offers are the words the
    # function code reports, which is how `evr_vac_type` and V4E came to
    # disagree about whether "02" is HEALY VAC or WAYNE VAC. It matters more
    # than a word: the EVR type decides which vapour COLLECTION test the
    # site has at all. See FIDELITY I10.
    PART_SETTINGS = {"evr_type": ("SV4E00", 0, isd.EVR_TYPE),
                     "evr_vac_type": ("SV4E00", 2, isd.VACUUM_TYPE)}
    PART_DEFAULT = "0101"

    # And once more on V43, whose flag is per DEVICE rather than per
    # console: "SS - Smart Sensor Index number", "f - In use flag 1=Yes
    # 0=No". The panel's AIRFLOW METER SELECT and PRESSURE SENSOR SELECT
    # screens are the glass side of that one flag, and they kept settings
    # of their own beside it -- so a technician who enabled an air flow
    # meter on the panel left V43 answering that it was not in use, which
    # is what ISD raises MISSING VAPOR FLOW MTR against. F9's shape, and
    # I10's fix.
    DEVICE_SETTINGS = {"evr_afm", "evr_ps"}

    # And V4F, whose two halves are the panel's NOZZLE A/L RANGE MAX and MIN
    # screens. The screens wrote settings nothing read, while the wire kept
    # a pair of its own with the manual's RANGE standing in for a default --
    # so a site that set its range on the glass reported another one on the
    # port and on the CARB requirements line. F9's shape once more. Each
    # entry is (function, which half). See FIDELITY I5.
    PAIR_SETTINGS = {"evr_al_min": ("V4F", 0), "evr_al_max": ("V4F", 1)}

    def _isd_pair(self, tok):
        """(low, high) off a two-float ISD setting, or the manual's default."""
        from . import packed
        held = self.values.get(f"S{tok}00") or ""
        if len(held) >= 16:
            return packed.unhexfloat(held[:8]), packed.unhexfloat(held[8:16])
        return tuple(isd.SETUP[tok]["default"])

    def setting(self, key, device=0, default=""):
        """One of the console's own settings, or what it reads out of the box."""
        if key in self.VALUE_SETTINGS:
            return isd.panel_processor(
                self.values.get(self.VALUE_SETTINGS[key]))
        if key in self.PART_SETTINGS:
            code, at, table = self.PART_SETTINGS[key]
            raw = self.values.get(code) or self.PART_DEFAULT
            return table.get(raw[at:at + 2], default)
        if key in self.DEVICE_SETTINGS:
            from . import wiresensors
            return ("ENABLED" if wiresensors.isd_in_use(self, device)
                    else "DISABLED")
        if key in self.PAIR_SETTINGS:
            tok, half = self.PAIR_SETTINGS[key]
            return "%+.2f" % self._isd_pair(tok)[half]
        return self.settings.get((key, int(device)), default)

    def set_setting(self, key, value, device=0):
        if key in self.VALUE_SETTINGS:
            # only the words that screen offers name a code; anything else
            # would have to invent one, so it leaves the field as it stands
            code = isd.panel_processor_code(value)
            if code is not None:
                self.values[self.VALUE_SETTINGS[key]] = code
            return self.setting(key, device)
        if key in self.PART_SETTINGS:
            code, at, table = self.PART_SETTINGS[key]
            digits = next((k for k, word in table.items() if word == value),
                          None)
            if digits is not None:
                raw = self.values.get(code) or self.PART_DEFAULT
                self.values[code] = raw[:at] + digits + raw[at + 2:]
            return self.setting(key, device)
        if key in self.DEVICE_SETTINGS:
            from . import wiresensors
            wiresensors.set_isd_in_use(self, device, value == "ENABLED")
            return value
        if key in self.PAIR_SETTINGS:
            from . import packed
            tok, half = self.PAIR_SETTINGS[key]
            pair = list(self._isd_pair(tok))
            try:
                pair[half] = float(str(value).strip())
            except ValueError:
                return self.setting(key, device)
            # the page's range, and the Set's own "low < high"; anything
            # else leaves the field as it stands, as V40's screen does
            lo, hi = isd.SETUP[tok]["range"]
            if lo <= pair[0] < pair[1] <= hi:
                self.values[f"S{tok}00"] = (packed.hexfloat(pair[0])
                                            + packed.hexfloat(pair[1]))
            return self.setting(key, device)
        self.settings[(key, int(device))] = value
        if value == "NO":
            # NO means the list is empty, not that the screen says so.
            # 576013-623 Rev AN p.24-3: "you will first specify whether you
            # want to assign an available alarm type ... by choosing Yes or
            # No for that type of alarm or input". A relay whose IN-TANK
            # ALARMS screen has been turned back to NO is a relay with no
            # in-tank alarm on it, and without this the screen would say NO
            # over a list that still drove the coil.
            self._clear_alarm_group(key, int(device))
        return value

    def _clear_alarm_group(self, key, device):
        """Take that group's category off the device it was cleared on."""
        for function, prefix in alarmgroups.PREFIX.items():
            if not key.startswith(prefix + "_"):
                continue
            aa = next((a for a, k in alarmgroups.BY_CATEGORY.items()
                       if key == f"{prefix}_{k}"), None)
            if aa is None:
                return
            if function == "OUTPUT RELAY SETUP":
                rows = self.relay_alarms.get(device)
            else:
                kind = next(k for k, f in
                            alarmgroups.LINE_DISABLE_FUNCTION.items()
                            if f == function)
                rows = self.line_disable_alarms.get((kind, device))
            if rows:
                rows[:] = [r for r in rows if r[0] != aa]
            return

    def evr_site(self):
        """"balance" or "assist": which vapour recovery system this site is.

        The console's default is BALANCE, and the two systems do not have
        the same vapour collection test. See FIDELITY I10.
        """
        return ("assist"
                if self.setting("evr_type", 0, "BALANCE") == "VACUUM ASSIST"
                else "balance")

    def tank_count(self):
        """How many tanks this console actually has."""
        return len(self.tank_level or self.programmed_tanks() or [1])

    def receivers(self):
        """A TLS-350 addresses EIGHT autodial receivers.

        576013-623 Rev AN, Receiver Configuration: "Press CHANGE twice to
        configure one receiver. To configure additional receivers, press the
        Right-Arrow key, then press CHANGE for up to seven more receivers."
        One and seven more. The tape settles it from the other end: its
        console's only receiver is `D 8:`, which a console holding six could
        not have. This said six, sourced to 52D, and 52D's own notes give
        the receiver number no range at all. See FIDELITY T4.
        """
        return [1, 2, 3, 4, 5, 6, 7, 8]

    def metric(self):
        """Is this console programmed in metric units?

        S517's first character, "1=U.S., 2=METRIC, 3=IMPERIAL GALLONS", and
        it decides more than what a screen says: 576013-623 Rev AN gives the
        water limits as "in inches (5.0 maximum) or millimeters (199
        maximum), depending on the units established in System Setup", so
        the same field has two ceilings. See FIDELITY F3.
        """
        return (self.values.get("S51700") or " ")[:1] == "2"

    def configured_receivers(self):
        """The destinations somebody has actually set up.

        576013-623 Rev AN's Receiver Configuration screen is "how many
        phone numbers are to be entered", and S521 is the flag it sets. The
        tape's console has configured one of the eight, the eighth, and
        prints that one everywhere a destination appears -- the phone
        directory, the auto-dial times and the alarm assignments.
        """
        return [n for n in self.receivers()
                if (self.values.get(f"S521{n:02d}") or "").endswith("1")]

    def receiver_label(self, number):
        """S522, the Receiver Location Label -- "HOME OFFICE" in the manual."""
        return self.text("522", number) or f"RECEIVER {number}"

    def alarm_name(self, aa, nn):
        """One row of i10100's category/type pair, which 52C borrows whole."""
        return (STATUS_TYPES.get(aa, {}).get(nn) or f"ALARM {aa}{nn}").upper()

    def partners(self, code, tank):
        """The tanks S612 (siphon) or S61D (line) manifolds to this one.

        Stored as a run of two-digit tank numbers. Older state files wrote the
        same run comma-separated, so the separator is stripped rather than
        parsed -- a file written before 7B1 landed still reads correctly.
        """
        raw = (self.values.get(f"S{code}{tank:02d}") or "").replace(",", "")
        out = []
        for i in range(0, len(raw) - 1, 2):
            pair = raw[i:i + 2]
            if pair.isdigit() and int(pair) and int(pair) != tank:
                if int(pair) not in out:
                    out.append(int(pair))
        return out

    # Thermal expansion is NOT applied, and the reason is measured rather
    # than assumed. Letting the fuel breathe -- `volume *= 1 + coeff * dT`
    # each tick, driven from `base_temperature` -- does exactly what Y8 says
    # it should: the TC volume then holds still on an untouched tank, which
    # is the whole point of a temperature-compensated figure. It cannot ship
    # as it stands because the tanker load watcher wakes on a fall of 0.05
    # gallons and an hour of breathing is 0.126 on this preset's first tank,
    # so a cooling tank reads as a slow tanker load. See FIDELITY Y8.
    def siphon_set(self, tank):
        """Every tank the siphon joins this one to, itself included.

        576013-623 Rev AN p.7-20: "You only need to enter this information
        for one of the tanks in the set. The system automatically enters the
        information for the other tank(s) in the set." So the set is read
        from BOTH ends -- a tank that names nobody is still in the set if
        somebody names it -- and `manifold_together` writes the other ends
        when one is entered.
        """
        seen, queue = {int(tank)}, [int(tank)]
        while queue:
            one = queue.pop()
            joined = set(self.partners("612", one))
            joined |= {n for n in self.tank_level
                       if one in self.partners("612", n)}
            for n in joined - seen:
                seen.add(n)
                queue.append(n)
        return sorted(seen)

    def manifold_together(self, tank, partners):
        """Enter a siphon set, and enter it for the other tanks too.

        And take this tank OUT of any set it has just left, because the
        other end of a manifold is written by the console rather than by the
        operator -- so clearing it from one tank has to clear it from the
        tank that was named, or the set survives a command meant to dissolve
        it.
        """
        tank = int(tank)
        whole = sorted({tank} | {int(n) for n in partners})
        for one in list(self.tank_level) + whole:
            if one in whole:
                others = [n for n in whole if n != one]
            elif tank in self.partners("612", one):
                others = [n for n in self.partners("612", one) if n != tank]
            else:
                continue
            self.values[f"S612{one:02d}"] = "".join(f"{n:02d}"
                                                    for n in others)

    # A siphon carries product between the tanks it joins, which is the whole
    # reason a leak test on a manifolded tank needs the break valve -- "Tank
    # Test Siphon Break allows the operator to perform in-tank leak tests on
    # siphon manifolded tanks", 576013-623 Rev AN p.7-20. How FAST it carries
    # it is a property of the site's plumbing and no page on this shelf gives
    # a figure, so this is the one invented number in the site model: a gallon
    # a second, which settles a 3,600 gallon difference in an hour and lets a
    # delivery into one tank of a pair show up in the other over the following
    # hour rather than instantly. See FIDELITY Y8.
    SIPHON_GPM = 60.0

    def siphon_tick(self, hours):
        """Let a siphon set find its own level.

        Levels, not volumes: two tanks of different sizes joined by a siphon
        settle at the same HEIGHT, which is the only thing the pipe between
        them can equalise.
        """
        if hours <= 0:
            return
        done = set()
        for tank in sorted(self.tank_level):
            if tank in done:
                continue
            joined = [n for n in self.siphon_set(tank) if n in self.tank_level]
            done.update(joined)
            if len(joined) < 2:
                continue
            if self.siphon_broken(joined):
                continue
            self._settle_siphon(joined, self.SIPHON_GPM * hours * 60.0)

    def siphon_break_fitted(self, tank):
        """S632: has this site got the valve at all?

        "NOTE: This option requires that the siphon break valve be
        installed. When on, Tank Test Siphon Break allows the operator to
        perform in-tank leak tests on siphon manifolded tanks. To leave the
        feature off, press STEP" -- 576013-623 Rev AN p.7-24, which makes
        OFF the default, and the real tape agrees: `TNK TST SIPHON BREAK:OFF`.

        The console broke the siphon for every test on every site, so it
        modelled a valve nobody had bought. See FIDELITY H9.
        """
        return self._configured("632", int(tank))

    def siphon_broken(self, joined):
        """Is the valve shut across this set right now?

        A test on any tank of the set shuts it, and only a set with the
        valve has one to shut.
        """
        return (any(self.siphon_break_fitted(n) for n in joined)
                and (any(self.leaks.active("tank", n) for n in joined)
                     or any(n in self.siphon_shut for n in joined)))

    def shut_siphon(self, tank, shut=True):
        """Shut the set's siphon-break valve by hand, or open it.

        The valve is the set's, so every tank on the bar goes with it; and
        a set without the valve (S632 off) has nothing to shut, which is
        why the bench only offers this where it is fitted. BENCH.md T11.
        """
        joined = [n for n in self.siphon_set(tank) if n in self.tank_level]
        for n in joined:
            if shut:
                self.siphon_shut.add(n)
            else:
                self.siphon_shut.discard(n)
        return joined

    def _settle_siphon(self, joined, budget):
        """Move a siphon set toward one level, moving no fuel it has not got.

        The settled level is NOT the average of the levels: two tanks of
        different diameters, or with different charts, hold different amounts
        at the same height. It is the level whose volumes add up to what the
        set already holds, which is what a pipe between them can reach, and
        it is found by halving the interval because a tank chart is a table
        rather than a formula.
        """
        held = {n: self.tank_level[n].get("volume", 0.0) for n in joined}
        total = sum(held.values())
        low, high = 0.0, max(self.limit("607", n) or 96.0 for n in joined)
        for _step in range(40):
            middle = (low + high) / 2.0
            if sum(self.volume_at(n, middle) for n in joined) < total:
                low = middle
            else:
                high = middle
        level = (low + high) / 2.0
        moves = {n: self.volume_at(n, level) - held[n] for n in joined}
        rising = sum(m for m in moves.values() if m > 0)
        if rising <= 0:
            return
        # the budget is what the pipe can carry in this interval, across the
        # whole set rather than per tank
        share = min(1.0, budget / rising)
        for n in joined:
            self.tank_level[n]["volume"] = max(0.0, held[n] + moves[n] * share)

    # ---- the eleven codes Revision Y added ---------------------------------
    # What a tank's coefficient is when nobody has programmed one.
    #
    # It used to be applied to EVERY tank as a module constant, with a
    # comment calling it "fitted to the four worked examples the manuals
    # print, which agree to within 7%":
    #
    #   5329 -> 5413 at 37.39 F   0.000697   576013-635 Rev AA p.58, i201
    #   8518 -> 8492 at 64.6  F   0.000664   576013-610 Rev AC p.8-1
    #   4208 -> 4194 at 65.0  F   0.000665   576013-610 Rev AC p.1-3
    #   9038 -> 8950 at 74.9  F   0.000653   576013-610 Rev AC p.1-3
    #   2549 -> 2525 at 74.4  F   0.000654   576013-610 Rev AC p.4-2
    #
    # Five examples, five implied coefficients, and no single value fits them
    # -- which is not a fitting problem. "You must enter the Coefficient of
    # Thermal Expansion for the fuel in each tank": they are five sites with
    # five programmed coefficients, and the number to use is the TANK'S. This
    # is only the fallback for a tank nobody has programmed. See FIDELITY Y6.
    THERMAL_EXPANSION = 0.00067

    # 576013-623 Rev AN Table 7-1, "Typical Thermal Coefficients", U.S. units.
    # Keyed by the product label a site programmes, because that is the only
    # thing the console knows about the fuel -- the tank's own S609 wins
    # wherever it is set, and this is what `presets.py` reaches for so that a
    # DIESEL tank is not built with the unleaded figure.
    THERMAL_COEFFICIENTS = {
        "ADBLUE": 0.00025, "DEF": 0.00025,
        "ALCOHOL": 0.00063,
        "AVIATION GAS": 0.00075,
        "DIESEL": 0.00045, "BIODIESEL": 0.00045, "BIODIESEL B20": 0.00045,
        "BIODIESEL B100": 0.00044,
        "ETHYLENE GLYCOL": 0.00037,
        "FUEL OIL 4": 0.00047,
        "GASOHOL": 0.00069,
        "GEAR OIL": 0.00047,
        "HYDRAULIC OIL": 0.00047,
        "JET FUEL": 0.00047,
        "KEROSENE": 0.00050, "PARAFFIN": 0.00050,
        "LPG": 0.00160,
        "LEADED": 0.00070,
        "MOTOR OIL": 0.00047,
        "PREMIUM": 0.00070,
        "REGULAR UNLEADED": 0.00070,
        "SUPER UNLEADED": 0.00070,
        "TRANSMISSION FLUID": 0.00047,
        "TURBINE OIL": 0.00047,
    }

    @classmethod
    def thermal_coefficient(cls, label):
        """Table 7-1 for a product label, or None if it is not on the table.

        Longest match wins, so PREMIUM UNLEADED takes PREMIUM's row and not
        REGULAR UNLEADED's, and a site that writes DIESEL #2 still gets
        diesel.
        """
        want = (label or "").upper()
        best = None
        for name, value in cls.THERMAL_COEFFICIENTS.items():
            if name in want and (best is None or len(name) > len(best[0])):
                best = (name, value)
        return best[1] if best else None

    def tc_reference(self):
        """S50E, the reference temperature every volume is corrected TO.

        "The system allows you to enter the temperature compensation (TC)
        reference temperature for all volume calculations. This temperature
        is determined by your location. In the U.S., the reference
        temperature used to calculate TC volume is normally 60 F. In other
        countries, this value may differ. Canada, for example, uses 15 C."
        576013-623 Rev AN p.5-13. It was a literal 60.0 in `tc_volume`, so a
        Canadian site could be programmed and could not be simulated.
        """
        held = self.limit("50E", 0)
        return 60.0 if held is None else held

    def tank_coefficient(self, tank):
        """This tank's Coefficient of Thermal Expansion, S609."""
        return self.limit("609", int(tank)) or self.THERMAL_EXPANSION

    def tc_volume(self, tank):
        """Volume corrected to the reference temperature, which several
        reports want by name.

        This used to be `volume * 0.998`, so corrected volume was always
        SMALLER than gross. That is not what a temperature correction does,
        and the manual's own example says so: p.58 prints 5329 gallons as
        5413 TC at 37.39 F. Product colder than the reference EXPANDS on the
        way up to it, so below it the TC figure is the larger one, and this
        console's own temperature band is 48 to 62 F -- so across nearly all
        of it the correction was running backwards. Reconciliation and BIR
        exist to compare gross against TC.

        Both of its inputs are the site's now rather than the module's: the
        tank's own coefficient and the console's own reference temperature.
        """
        st = self.tank_level.get(int(tank))
        if st is None:
            return 0.0
        return self.tc_volume_at(tank, st.get("volume", 0.0))

    def tc_volume_at(self, tank, volume, temperature=None):
        """The same correction, on a volume and a temperature handed to it.

        A report about a period that has CLOSED is not a report about the
        tank as it is now. I204 printed the live TC volume and the live
        temperature against BOTH ends of every shift, so a shift that sold
        anything printed its opening volume beside its closing volume's
        correction. The manual's own sample cannot catch it -- nothing moved
        during that shift, and both ends of it read 8518, 8492 and 64.57.
        See FIDELITY X4.
        """
        if temperature is None:
            temperature = self.product_temperature(int(tank))
        away = temperature - self.tc_reference()
        return volume * (1.0 - self.tank_coefficient(tank) * away)

    def uptime_minutes(self):
        """908: minutes since the console came up.

        The bench has been running since the process started, so that is the
        honest answer -- a console that claims a power-up time it never had is
        a console lying about the one thing this report exists to say.
        """
        return max(0.0, (time.mktime(self.now()) - self.started) / 60.0)

    def apm_setup_ok(self):
        """VA4: whether the APM setup verification passes.

        It fails when the vapour monitoring type says APM and no APM sensor is
        configured, which is the condition the test is for.
        """
        wants_apm = (self.values.get("S54E00") or "0")[:1] == "1"
        return not wants_apm or self.has("vapor")

    def apm_vapor_pressure(self):
        """VA6: inches of water column, which is not PSI.

        The APM diagnostic reads in IWC and the pressure line diagnostics read
        in PSI; 1 psi is about 27.7 IWC, and the sign convention is that
        ullage sits BELOW atmospheric, so this reads negative.
        """
        from tls350sim import readings
        return -readings.wander(self, 5.0, 15.0, "apmiwc")

    def apm_clear_dates(self):
        """VA7's printed dates, or a dash where nothing has been cleared."""
        out = {}
        for key in ("01", "02", "03"):
            when = self.apm_cleared.get(key)
            out[key] = (time.strftime("%m/%d/%y", time.localtime(when))
                        if when else "--/--/--")
        return out

    def apm_events(self):
        """VA8: what the APM has done, newest first."""
        return list(self.apm_event_log)

    # VA8's events, 576013-635 Rev AA p.669: "aa - Primary Misc. Event
    # Category" 01=System Event, 02=Pumps Re-enabled, 03=Test Manually
    # Cleared, 04=Disabled Dispensers; "bb - Primary Misc. Event Type", which
    # for a system event is 01 APM Startup, 02 APM Shutdown and 03 Time
    # Change Detected, and for a manual clear 01 APM Setup Self Tests, 03 APM
    # Tests and 06 APM Sensor Self Tests. p.668 prints each as a DESCRIPTION
    # and an ACTION/NAME; None is an action that is a time rather than words.
    # Categories 02 and 04 follow an APM test, which nothing here runs, so
    # they have words on the page and no producer (FIDELITY I8).
    APM_EVENT_WORDS = {
        ("01", "01"): ("APM STARTUP", ""),
        ("01", "02"): ("APM SHUTDOWN", ""),
        ("01", "03"): ("TIME CHANGE DETECTED AT", None),
        ("03", "01"): ("APM SETUP SELF TEST", "TEST MANUALLY CLEARED"),
        ("03", "03"): ("APM TEST", "TEST MANUALLY CLEARED"),
        ("03", "06"): ("APM SENSOR SELF TEST", "TEST MANUALLY CLEARED"),
    }

    def apm_monitoring(self):
        """Is this console's vapour monitoring the APM?

        54E, "Set Vapor Monitoring Type (0=CARB ISD, 1=APM)", on the key
        its own note asks for: "An ISD/APM SEM is required".
        """
        return (self.licensed("isd")
                and (self.values.get("S54E00") or "0")[:1] == "1")

    def apm_log(self, aa, bb, when=None, data=None):
        """Put one event on VA8's log, if the APM is what is monitoring.

        `apm_event_log` was initialised, returned by VA8 and appended to by
        nothing, while the moments VA8's own sample logs -- a startup, a
        shutdown, a clock change, a manual clear -- all happen on this
        console. `data` is the time an event's action column prints.
        """
        if not self.apm_monitoring():
            return
        at = time.mktime(self.now()) if when is None else when
        self.apm_event_log.insert(0, {"at": at, "aa": aa, "bb": bb,
                                      "data": data})

    def vmci_sub_alarms(self):
        """VA5: the sub-alarms behind each VMCI alarm.

        A console with no VMCI alarm standing has no sub-alarms under it,
        which is what an untouched bench reports.
        """
        return list(self.vmci_sub_log)

    def manifold_deliveries(self, tanks):
        """239 and 23A: the drops into a set of manifolded tanks.

        "With Sales Adjustment if BIR available" is on both codes, so what was
        dispensed during the drop comes off the figure exactly as it does for
        I20A -- the adjustment is the same adjustment, not a second one.
        """
        out = []
        for tank in tanks:
            for record in self.deliveries.records.get(int(tank)) or []:
                if not record.end:
                    continue
                # the SAME adjustment I20A prints, not a second one: what was
                # dispensed while the drop ran is added back, because the tank
                # rose by that much less than was actually delivered
                extra = record.sold if self.licensed("bir") else 0.0
                out.append({"start": record.start["at"],
                            "end": record.end["at"],
                            "gallons": max(0.0, record.amount + extra),
                            "tc": max(0.0, record.tc_amount + extra)})
        return sorted(out, key=lambda r: r["start"], reverse=True)[:6]

    def generator_runs(self, tank):
        """404: the periods this tank fed a generator.

        "Setup parameters determine whether an input is from a generator", so
        a console with no input programmed as one has nothing to report --
        which is what an untouched console answers, and what this returns
        until the bench records a run.
        """
        return list(self.generator_log.get(int(tank), []))

    def record_generator_run(self, tank, start, end, used):
        """The bench putting a generator run on the record."""
        st = self.tank_level.get(int(tank), {})
        volume = st.get("volume", 0.0)
        figures = [st.get("height", 0.0), volume + used,
                   volume + used, st.get("water", 0.0),
                   st.get("temperature", 60.0), st.get("height", 0.0),
                   volume, volume, st.get("water", 0.0),
                   st.get("temperature", 60.0)]
        run = {"start": start, "end": end,
               "hours": max(0.0, (end - start) / 3600.0),
               "used": used, "figures": figures}
        self.generator_log.setdefault(int(tank), []).append(run)
        return run

    DIM_PORTS = 2

    def has_dim(self):
        """Is there a DIM of any kind in the cage? "dim" is the old bare
        key some saved sites still carry; the cards are edim and mdim."""
        return self.has("edim") or self.has("mdim") or self.has("dim")

    def dim_port_of(self, meter):
        """Which DIM port a meter's transactions arrive over.

        Every bench meter sits on the default DIM (see `meterid.py`), which
        is port 1; a second port is a second card.
        """
        key = self.meter_key(meter)
        return 1 if (key.bus, key.slot) == (DEFAULT_BUS, DEFAULT_SLOT) else 2

    def dim_link_ok(self, meter=None):
        """Is the DIM link the console needs for that meter up?

        `dim_fault` is the whole link, which the tests and older sites set
        directly; `dim_down` is by port, which is what the bench sets and
        what BA1 reports on.
        """
        if self.dim_fault:
            return False
        down = self.dim_down | self.dim_disabled
        if meter is None:
            return not down
        return self.dim_port_of(meter) not in down

    def set_dim_port(self, port, down):
        """Take one DIM port's link down, or bring it back up.

        BA1 is "DIM Communication Status and History": a post time, a clear
        time and a duration for every fault, and "0 indicates the condition
        is currently active" for one still standing. The history is written
        here, at the moment the link moves. BENCH.md D1.
        """
        port = int(port)
        now = time.mktime(self.now())
        faults = self.dim_faults.setdefault(port, [])
        if down and port not in self.dim_down:
            self.dim_down.add(port)
            faults.append({"post": now, "clear": None, "hours": 0.0})
        elif not down and port in self.dim_down:
            self.dim_down.discard(port)
            for fault in faults:
                if fault["clear"] is None:
                    fault["clear"] = now
                    fault["hours"] = max(0.0, (now - fault["post"]) / 3600.0)
            del faults[:-20]

    def dim_ports(self):
        """BA1: whether each DIM port is talking, and its fault history.

        A console with no DIM card is not in fault -- it has no port to be in
        fault ON -- so an empty cage reports nothing rather than reporting a
        failure that is really an absence.
        """
        if not self.has_dim():
            return []
        out = []
        for port in range(1, self.DIM_PORTS + 1):
            faults = list(self.dim_faults.get(port, []))
            active = any(f["clear"] is None for f in faults)
            out.append({"port": port,
                        "status": "FAULT" if active else "OK",
                        "faults": faults})
        return out

    def vapor_processor_status(self):
        """V82: the processor's five verdicts and six figures."""
        from tls350sim import readings
        from tls350sim.wirelater import VP_FLOAT_FIELDS, VP_STATUS_FIELDS
        # A processor nobody has run has been tested by nobody. `vp_cycles`
        # is V80's buffer of completed runs, so a site with one has run it.
        running = bool(self.vp_cycles) or self.vp_started is not None
        # Which V82 verdict each daily test decides. The other three status
        # fields keep the old rule, because which of the three daily tests
        # "Maximum runtime" reports is not settled -- V45's MAXIMUM RUNTIME
        # is a per-cycle limit in minutes and the duty cycle test is a
        # fraction of a day, so they are not obviously the same thing. See
        # FIDELITY I2a.
        DECIDED = {"VP overpress test": "vp_pressure",
                   "Emission test": "vp_emission"}
        codes, words = {}, {}
        for name in VP_STATUS_FIELDS:
            # a processor nobody has tested reads NO TEST rather than PASS;
            # claiming a pass the console never made is the one answer a
            # diagnostic must not give
            value = 3 if running else 0
            test = DECIDED.get(name)
            if test and running:
                # The figures and the thresholds used to sit in this
                # function and never meet, so the report contradicted itself
                # on the paper: EMISSION TEST NOTEST over EMISSION LB/1KG
                # 0.32, which is exactly the failure threshold.
                value = {"warn": 1, "fail": 2}.get(self.isd_state(test), 3)
            codes[name] = value
            words[name] = {0: "NOTEST", 1: "WARN", 2: "FAIL",
                           3: "PASS"}[value]
        figures = {
            VP_FLOAT_FIELDS[0]: readings.wander(self, 0.1, 0.4, "vp95"),
            VP_FLOAT_FIELDS[1]: readings.wander(self, 0.0, 0.5, "vpemit"),
            VP_FLOAT_FIELDS[2]: readings.wander(self, 5.0, 40.0, "vpduty"),
            VP_FLOAT_FIELDS[3]: readings.wander(self, 0.5, 6.0, "vprun"),
            VP_FLOAT_FIELDS[4]: readings.wander(self, 500.0, 5000.0, "vpthru"),
            VP_FLOAT_FIELDS[5]: readings.wander(self, 0.5, 3.0, "vphc"),
        }
        from . import isd as isdmod
        return {"version": isdmod.PMC_VERSION,
                "type": self.vapor_processor_type(),
                "tested": time.mktime(self.now()),
                "status": words, "codes": codes, "figures": figures}

    def vapor_processor_type(self):
        """The words V82 prints for the processor fitted.

        This read `values["SVC200"]`, a key nothing on the wire writes and no
        manual on this shelf names, and defaulted to VST ECS PROCESSOR -- so
        V82 reported a VST on a console with a Veeder-Root Polisher in it,
        and on a console with no processor at all. It was the third of F9's
        split stores in a row, and the one where the second store was not
        merely unread but imaginary.

        V40, Set Vapor Processor Type, is where a console keeps this, and
        since FIDELITY I7 it is where the panel keeps it too.
        """
        from tls350sim import isd
        held = (self.values.get("SV4000") or "").strip()
        return isd.VAPOR_PROCESSOR.get(held[-2:], isd.VAPOR_PROCESSOR["00"])

    def polisher_days(self, most=None):
        """V88: one record a day, newest last."""
        from tls350sim import readings
        if not self.licensed("pmc"):
            return []
        now = time.mktime(self.now())
        days = min(int(most or 7), 30)
        out = []
        for back in range(days, 0, -1):
            at = now - back * 86400.0
            valid = True
            self_code = "03"
            press_code = "03"
            out.append({
                "at": at,
                "load": readings.fixed(0.5, 6.0, "vpload", back),
                "purge": readings.fixed(5.0, 20.0, "vppurge", back),
                "min": float(readings.integer(0, 5, "vpmin", back)),
                "max": float(readings.integer(10, 35, "vpmax", back)),
                "valid": valid,
                "self": self.TEST_WORDS_TABLE[self_code], "self_code": self_code,
                "press": self.TEST_WORDS_TABLE[press_code], "press_code": press_code})
        return out

    def collection_tests(self, most=None):
        """V12: the balance flow monitoring records."""
        from tls350sim import readings
        if not self.licensed("isd"):
            return []
        now = time.mktime(self.now())
        want = min(int(most or 5), 100)
        out = []
        for back in range(want, 0, -1):
            out.append({
                "at": now - back * 86400.0,
                "orvr": readings.fixed(15.0, 35.0, "orvr", back),
                "limit": 40.0,
                "chi": readings.fixed(0.2, 2.0, "chi", back),
                "chi_limit": 3.84,
                "chi_state": "3"})
        return out

    def vmc_for_position(self, position):
        """8C3 maps a VMC's two sides to fueling positions; this reads it
        backwards, which is what the A/L reports need."""
        for number, sides in sorted(self.vmc_fuel_pos.items()):
            if position in (sides.get("A"), sides.get("B")):
                return number
        return 1

    def side_for_position(self, position):
        for _number, sides in sorted(self.vmc_fuel_pos.items()):
            if sides.get("A") == position:
                return 1
            if sides.get("B") == position:
                return 2
        return 1

    def al_records(self, code, position, window=""):
        """VA1, VA2 and VA3's rows for one fueling position.

        The three reports read the same air/liquid history through different
        columns, so they share one source and differ in what they print.
        """
        from tls350sim import readings
        now = time.mktime(self.now())
        out = []
        for back in range(5, 0, -1):
            state = 0 if code == "VA1" else (1 if code == "VA2" else 3)
            out.append({
                "at": now - back * 86400.0,
                "al": readings.fixed(0.5, 1.5, "al", position, back),
                "count": readings.integer(10, 90, "altx", position, back),
                "code": state,
                "status": {0: "IDLE", 1: "WARN", 2: "FAIL",
                           3: "PASS"}[state]})
        return out

    def assign_relay_alarm(self, relay, aa, nn, tt, on=True):
        """Put one alarm on an output relay's list, or take it off again.

        `808`'s own payload is `AANNTTss`: the alarm category, the alarm
        type, the device it is on -- "00=all" -- and whether this Set puts
        the assignment on the list or takes it off. Both the wire and the
        panel arrive here, which is the same arrangement `map_meter` and
        `set_meter_offset` have and for the same reason. -> the relay's
        list as it now stands.
        """
        rows = self.relay_alarms.setdefault(int(relay), [])
        key = (aa, nn, tt)
        rows[:] = [x for x in rows if x != key]
        if on:
            rows.append(key)
        self._note_alarm_group("OUTPUT RELAY SETUP", relay, aa, rows)
        self.save()
        return rows

    def assign_line_disable_alarm(self, kind, number, aa, nn, tt, on=True):
        """The same, for the alarms that shut a LINE down.

        `787`, `7A7` and `75B` take the same `AANNTTss` against a line
        instead of a relay, and the wire wrote this store inline while the
        relay's had a door of its own. One door, because the panel arrives
        here too now and the group screens have to be kept level with the
        list either of them writes.
        """
        rows = self.line_disable_alarms.setdefault((kind, int(number)), [])
        key = (aa, nn, tt)
        rows[:] = [x for x in rows if x != key]
        if on:
            rows.append(key)
        self._note_alarm_group(
            alarmgroups.LINE_DISABLE_FUNCTION[kind], number, aa, rows)
        return rows

    def _note_alarm_group(self, function, device, aa, rows):
        """Keep the group screen level with the list it is the front of.

        576013-623 Rev AN p.24-3 asks the group question first -- "you will
        first specify whether you want to assign an available alarm type
        ... by choosing Yes or No for that type of alarm" -- so the answer
        is not a second store: it is whether that category has anything on
        the list. A relay given an in-tank leak over the serial port reads
        `IN-TANK ALARMS: YES` on the glass because of this line.
        """
        key = alarmgroups.setting_key(alarmgroups.PREFIX[function], aa)
        if key:
            self.set_setting(key, "YES" if any(r[0] == aa for r in rows)
                             else "NO", int(device))

    def map_meter(self, bus, slot, fp, meter, tank):
        """Write one entry of the tank/meter map, which is 7B1's own store.

        Two callers, one store. The wire arrives here from `S7B100` and the
        panel from 576013-623 Rev AN p.17-5's MODIFY TANK/METER MAP screen,
        which sets the same five fields -- and a panel that had written
        them under their code in `values` would have stored five numbers
        nothing reads. `tank` is the command's own value: -1 for a tank
        with no probe, 0 to unmap.

        -> the key it wrote, or the key it removed.
        """
        key = MeterId(int(bus), int(slot), int(fp), int(meter))
        if not tank:
            # "00=Unmap present tank"
            self.meter_map.pop(key, None)
            self.meters.pop(key, None)
        else:
            # "A manually mapped meter is considered locked. Auto meter
            # mapping will not change a locked meter."
            self.meter_map[key] = {"tank": int(tank), "locked": True}
            self.meters[key] = int(tank)
        self.save()
        return key

    def set_meter_offset(self, fp, meter, tank, pct):
        """Write one meter's own calibration offset, 7B4's store.

        The same two callers `map_meter` has, for the same reason: the
        panel's INDIVIDUAL METER OFFSET row and `S7B400` write the same
        three-fields-and-a-percent, and `meter_offset` reads them from here
        and from nowhere else.
        """
        self.meter_offsets[offset_key((fp, meter))] = {
            "fp": int(fp), "tank": int(tank), "pct": float(pct)}
        self.save()

    def meter_offset(self, meter):
        """The calibration offset that applies to one meter, as a percent.

        Two codes set this and the specific one wins: S7B400 is "Set
        INDIVIDUAL Meter Offset" and carries a fueling position, a meter and
        a tank, where S7B200 is one figure for the site. A meter with its own
        offset uses it; every other meter uses the site's.
        """
        own = self.meter_offsets.get(offset_key(meter))
        if own is not None:
            return float(own.get("pct", 0.0))
        raw = (self.values.get("S7B200") or "").strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            try:
                from tls350sim import packed
                return packed.unhexfloat(raw[-8:])
            except ValueError:
                return 0.0

    # ---- blended grades, which are a DISPENSER and not a console ------------
    def blend_meters(self, meter):
        """[(component meter, fraction)] a blended nozzle actually runs.

        The TLS-350 has no proportional blend anywhere in it, and this
        is deliberately built so that it never needs one. 576013-818 p.12-7:
        "A tank can be mapped to only one meter for a given Fuel Position
        (FP)", and the only blender support in the whole manual is a DIM
        parameter that says how to READ the POS -- p.10-2's "T Blender Only
        Site" and "P Plus one dispensers at site". The mixing happens in the
        dispenser, behind the nozzle, and what reaches the console is what
        p.10-1 note 6 implies: each component reported against its own
        meter, mapped to its own tank, exactly like any other sale.

        So a blend here is a BENCH object -- a grade button on a dispenser
        -- and lifting it runs two ordinary meters at a share of the rate
        each. BIR, CSLD, the meter events table and the map need no
        knowledge of it whatever, and a technician reading I7B100 sees the
        two-meters-per-position shape p.12-24's own worked example has.
        """
        blend = self.blends.get(meter)
        parts = (blend or {}).get("parts") or []
        out = []
        for row in parts:
            try:
                comp, pct = meter_key(row[0]), float(row[1])
            except (IndexError, TypeError, ValueError):
                continue
            if pct > 0 and self.meters.get(comp):
                out.append((comp, pct))
        total = sum(pct for _m, pct in out)
        if not out or total <= 0:
            return []
        return [(comp, pct / total) for comp, pct in out]

    def blend_parts(self, meter):
        """[(tank, fraction)] the fuel for this nozzle comes out of.

        One entry for an ordinary meter, one per component for a blended
        one. This is what the bench and the traffic generator ask when they
        need the TANKS behind a nozzle -- to lift the right lines' handles,
        to tell whether a shutdown has killed it, and to work out how long
        each tank lasts.
        """
        parts = self.blend_meters(meter)
        if parts:
            merged = {}
            for comp, frac in parts:
                tank = self.meters.get(comp)
                if tank:
                    merged[int(tank)] = merged.get(int(tank), 0.0) + frac
            return sorted(merged.items())
        tank = self.meters.get(meter)
        return [(int(tank), 1.0)] if tank else []

    def blend_label(self, meter):
        blend = self.blends.get(meter) or {}
        return (blend.get("label") or "").strip()

    def lines_for_meter(self, meter):
        """[(kind, number)] every line the product for this nozzle comes up.

        A blended nozzle is on two of them, and both pumps run for one
        sale, which is why the handle is a refcount rather than a flag.
        """
        tanks = {tank for tank, _frac in self.blend_parts(meter)}
        if not tanks:
            return []
        out = []
        for kind, number, _label in self.programmed_lines():
            code = _relays.LINE_TANK_CODE.get(kind)
            if not code:
                continue
            raw = (self.text(code, number) or "").strip()
            if raw.isdigit() and int(raw) in tanks:
                out.append((kind, number))
        return out

    def activity_spans(self, tank, start, end):
        """[(from, to)] when anything sold out of this tank, in that window.

        `meter_flow` is a RATE averaged over whatever interval the console
        last ticked, and everything that asked "is this tank busy" asked
        whether that rate was zero. At a real console's pace the two are the
        same question. On this bench the clock runs at up to 36,000x, where
        one tick is seven hours of console time -- and then a single car,
        two minutes at the nozzle, leaves a rate standing for seven hours
        and the tank reads as busy for all of it.

        CSLD is what that broke, because CSLD's whole input is the SHAPE of
        the day: 576013-818 Figure 11-2, "Tank goes idle and must remain so
        for 8 minutes." Eight minutes cannot be found inside a number. So
        the sales keep the spans they actually flowed for, and this is the
        tank's own view of them.
        """
        tank = int(tank)
        start, end = float(start), float(end)
        out = []
        for meter, where in (self.meters or {}).items():
            if int(where) != tank:
                continue
            for from_, to in self.sales.spans.get(meter, ()):
                lo, hi = max(from_, start), min(to, end)
                if hi > lo:
                    out.append([lo, hi])
        # A bench rate somebody typed into a meter card is a forecourt that
        # never stops, and so is a pump sense contact -- 576013-623 p.8-8's
        # "assigned to the tank". Neither has spans: they are levels, and
        # they cover the window.
        if self.inputs.pump_on(tank):
            out.append([start, end])
        else:
            for meter, where in (self.meters or {}).items():
                if int(where) == tank and meter not in self.sales.running \
                        and meter not in self.sales._serving \
                        and self.meter_flow.get(meter, 0.0) > 0:
                    out.append([start, end])
                    break
        out.sort()
        merged = []
        for span in out:
            if merged and span[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], span[1])
            else:
                merged.append(span)
        return [(a, b) for a, b in merged]

    def activity_segments(self, tank, start, end):
        """The same window as [(from, to, busy)], gaps included.

        Every moment of it accounted for once, in order, so a state machine
        can be walked across an interval instead of being told one thing
        about the whole of it.
        """
        start, end = float(start), float(end)
        if end <= start:
            return []
        out, cursor = [], start
        for from_, to in self.activity_spans(tank, start, end):
            if from_ > cursor:
                out.append((cursor, from_, False))
            out.append((max(from_, cursor), to, True))
            cursor = to
        if cursor < end:
            out.append((cursor, end, False))
        return out

    def dispensing_blocked(self, meter):
        """Is anything stopping this nozzle from selling?

        A line the console has SHUT DOWN has had its STP de-energized --
        that is what a shutdown IS -- and a relay wired to the tank that has
        dropped out has done the same thing through the contactor. Either
        way the handle gets no pump, so no fuel moves, and on a blended
        nozzle it takes only ONE of the two components to be dead: a
        dispenser cannot make the mix without both.
        """
        for tank, _frac in self.blend_parts(meter):
            if self.outputs.pump_cut(tank):
                return True
        for kind, number in self.lines_for_meter(meter):
            if self.lines.disabled(kind, number):
                return True
        return False

    @staticmethod
    def meter_key(meter):
        """One meter's full identity -- bus, slot, position, meter.

        On the class because the bench and the panel both hold bare meter
        numbers and both need the console's own name for one. See
        `meterid.py` and FIDELITY G7.
        """
        return meter_key(meter)

    def fueling_position(self, meter):
        """7B1's FP column, which is now part of the meter's own identity.

        It used to be a field of the map entry, which meant a meter nobody
        had mapped over the wire had no position at all and every caller
        carried its own guess at one. See FIDELITY G7.
        """
        return meter_key(meter).fp

    def siphon_break_conditions(self):
        """[AANNTT] for `02/22`, Tank Siphon Break Active Warning.

        576013-610 Rev AC Table 29-3 gives it in a line: "TANK SIPHON BREAK
        | Warning | Siphon break valve has shut | Clears when tank test
        completes." So it is not a fault -- it is the console saying the
        siphon is not carrying product at the moment, which matters to
        everybody reading the levels of a manifolded set.

        One choice, and it is the set rather than the tank. The valve
        belongs to the SET and shuts across all of it, so every tank whose
        siphon is broken carries the warning, not only the one being
        tested. The manual names neither.

        This is one of N1's alarm types with no producer, and it needed no
        new physics: the condition is the valve being shut, which the site
        model already knows because it is what stops the fuel moving.
        See FIDELITY H9 and N1.
        """
        out, done = [], set()
        for tank in sorted(self.tank_level):
            if tank in done:
                continue
            joined = [n for n in self.siphon_set(tank) if n in self.tank_level]
            done.update(joined)
            if len(joined) < 2 or not self.siphon_broken(joined):
                continue
            out += [f"0222{n:02d}" for n in joined]
        return out

    def siphon_secondary(self, tank):
        """Is this tank a siphon manifolded SECONDARY?

        576013-623 Rev AN draws the thermal coefficient screen with "If this
        is a siphon manifolded secondary tank, this window will not appear!"
        beside it. The manual does not say which member of a siphon set is
        the primary, and the console stores the set symmetrically in S612,
        so the reading here is the ordinary one: the lowest numbered tank in
        the set is the primary and the rest are secondaries. See FIDELITY F6.
        """
        partners = self.partners("612", tank)
        return bool(partners) and tank > min(partners + [tank])

    def manifolded(self, tank):
        """The tanks a report has to name together.

        "an adjusted delivery report is automatically printed for single or
        manifolded tanks", and the set is what S612 (siphon) and S61D (line)
        hold: pairs of tank numbers, "00" for an empty slot.
        """
        out = [tank]
        for code in ("612", "61D"):
            for n in self.partners(code, tank):
                if n not in out:
                    out.append(n)
        return out

    def last_test_lines(self, tank):
        """"0.20 GAL/HR TEST PASS" over its date, for a variance analysis."""
        out = []
        for rate_key, name in (("periodic", "0.20 GAL/HR TEST"),
                               ("annual", "0.10 GAL/HR TEST")):
            result = self.leaks.result("tank", tank, rate_key)
            if result is None:
                out.append(f"{name} NO DATA")
                continue
            out.append(f"{name} {result.result}")
            out.append(clock_words(result.started))
        return out

    def monthly_test_lines(self, tank):
        """The MONTHLY TANK TEST REPORT block of a Variance Analysis.

        576013-610 Rev AC p.28-18 draws it under the leak test results:

            MONTHLY TANK TEST REPORT
            --------------------------
            T 1: UNLEADED GASOLINE
            PROBE SERIAL NUM 627020
            TEST TYPE: STANDARD
            PERCENT VOLUME = 24.8
            MMM DD, YYYY HH:MM XM

        The monthly test is the periodic one -- 0.2 gal/hr -- so its type is
        `S62C`, Periodic Test Type, whose own two choices are STANDARD and
        QUICK, and the percent volume is what the tank held when it ran
        against what it holds full. Both were on the console and neither was
        printed. See FIDELITY Q3.
        """
        which = (self.values.get(f"S62C{tank:02d}") or "").strip()[-1:]
        kind = "QUICK" if which == "1" else "STANDARD"
        out = [f"TEST TYPE: {kind}"]
        result = self.leaks.result("tank", tank, "periodic")
        full = self.full_volume(tank)
        if result is None or not full:
            out.append("PERCENT VOLUME = NO DATA")
            return out
        out.append(f"PERCENT VOLUME = {100.0 * result.volume / full:.1f}")
        out.append(clock_words(result.started))
        return out

    def corrective_actions(self, tank, analysis):
        """What the Variance Analysis Report tells the site to go and do.

        "Corrective action for tank chart alarm, calibration failure, or
        failed tank or line tests": so each one that is standing puts its
        own line on the report, against the tank it belongs to.
        """
        out = []
        alarms = {r[2:4] for r in self.compute_alarms()
                  if r[:2] == "02" and int(r[4:6]) == tank}
        # The alarm NUMBERS, decimal, the way `conditions()` writes them.
        # This read "18" for the AccuChart calibration warning, which is
        # `02/18 PER TST NEEDED ALM`, and hex for the three test failures --
        # `0D`, `0E`, `0F` against records that say `13`, `14`, `15` -- so
        # RECALIBRATE TANK CHART came up on the wrong alarm and INVESTIGATE
        # FAILED TANK TEST could not come up at all. See FIDELITY Q3.
        if "24" in alarms:                      # ACCUCHART CAL WARN
            out.append("RECALIBRATE TANK CHART")
            out.append(f"T{tank}")
        if alarms & {"13", "14", "15"}:         # gross, periodic, annual fail
            out.append("INVESTIGATE FAILED TANK TEST")
            out.append(f"T{tank}")
        if abs(analysis["delivery_var"]) > 0.5:
            out.append("CHECK DELIVERY TICKETS")
            out.append(f"T{tank}")
        if abs(analysis["sales_var"]) > 0.5:
            out.append("INSPECT METERS")
            out.append(f"T{tank}")
        return out

    # which S-function holds each sensor module's TYPE, where it has one
    SENSOR_TYPE_CODE = {"liquid": "703", "2wire": "743", "3wire": "748",
                        "smart": "723", "universal": "74D"}

    def sensor_type(self, module, number):
        """The type this sensor is programmed as, as the wire holds it."""
        code = self.SENSOR_TYPE_CODE.get(module)
        if not code:
            return ""
        raw = (self.values.get(f"S{code}{int(number):02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return body.strip()

    def sensor_states(self, module, number):
        """The states this sensor can be in, its own type decided.

        A console does not offer, report or alarm a condition the sensor
        cannot sense: a single-float sump sensor has FUEL and OUT, and no
        amount of water in the sump will make it say WATER.
        """
        kind = self.sensor_type(module, number)
        if module == "liquid":
            return LIQUID_TYPE_STATES.get(kind or "1",
                                          LIQUID_TYPE_STATES["1"])
        if module == "2wire":
            return TYPE_A_STATES.get(kind or "1", TYPE_A_STATES["1"])
        if module == "3wire":
            return TYPE_B_STATES.get(kind or "1", TYPE_B_STATES["1"])
        if module == "vapor":
            return VAPOR_STATES
        if module == "gw":
            return GW_STATES
        if module == "universal":
            return UNIVERSAL_STATES
        if module == "smart":
            return SMART_CATEGORY_STATES.get(kind or "00", _SMART_COMMON)
        return ()

    def sensor_alarm_allowed(self, module, number, state):
        """Can this sensor post that alarm right now?

        Mostly a question about the type, but the 3-wire sensor's High Vapor
        mode adds a condition on top: "In High Vapor Mode, a Fuel alarm is
        posted only if a High liquid or a Liquid Warning condition also
        exists." One sensor cannot be in two states at once, so on this
        console High Vapor mode means the fuel alarm does not post at all
        until the liquid channel has something on it.
        """
        if state not in self.sensor_states(module, number):
            return False
        if (module == "3wire" and state == "fuel"
                and self.sensor_type(module, number) == "2"):
            return False
        return True

    def sensor_reading(self, module, number):
        """SENSOR STATUS for one sensor, in the console's own words.

        What the console is SAYING, not what the wire in the sump is doing.
        576013-610 Rev AC p.15-1: SENSOR NORMAL shows "if the sensor is
        functioning properly **and no alarm conditions exist**" -- so an
        alarm that has been corrected and not acknowledged is still on the
        screen and still in the report, which is the rule `wiresensors`
        states for the same sensor on the wire.

        This read `sensor_state`, the physical state alone, and fed both the
        roll and the glass with it: a dried-out sensor with a latched FUEL
        ALARM printed SENSOR NORMAL on paper and answered FUEL ALARM on the
        wire, one console with two stories about one sensor. *The guard
        below shows the sync was attempted in one direction only* -- it
        suppressed a state the wire cannot say, and never added the ones the
        wire does. FIDELITY O27.
        """
        if not self.has(module):
            return ""
        words = (SMART_STATE_WORDS if module == "smart"
                 else SENSOR_STATE_WORDS)
        state = self.sensor_state.get((module, str(number)), "normal")
        if state != "normal" and not self.sensor_alarm_allowed(module, number,
                                                               state):
            # the wire cannot say that, so the console does not either
            state = "normal"
        if state == "normal":
            state = self.standing_sensor_state(module, number) or "normal"
        return words.get(state, state.upper())

    def standing_sensor_state(self, module, number):
        """The sensor state the DISPLAY is holding, or None.

        `compute_alarms` is every condition that is true plus anything
        latched and unacknowledged, and its records are `AANNTT`; this reads
        one back into the state word it was posted from.
        """
        aa = SENSOR_MODULE_CATEGORY.get(module)
        if not aa:
            return None
        table = SMART_STATE_NN if module == "smart" else SENSOR_STATE_NN
        by_nn = {nn: name for name, nn in table.items()}
        tt = f"{int(number):02d}"
        for record in self.compute_alarms():
            if record[:2] == aa and record[4:6] == tt:
                name = by_nn.get(record[2:4])
                if name:
                    return name
        return None

    # ---- the console's own clock -------------------------------------------
    # A TLS-350 is not told the time by anything; you set it at the SET TIME
    # step and it keeps it. So set the date to 2003 and every screen and every
    # serial reply is stamped 2003, which is exactly what a tool sees on a
    # console nobody has corrected in years.
    def now(self):
        return time.localtime(time.time() + self.clock_offset)

    def tick(self):
        """Move the console's clock, and the site along with it.

        The bench can run the clock fast, because a 12 hour leak test is not
        worth sitting through. Everything downstream of the clock, the
        status line, the serial timestamps, a test's remaining hours, and the
        product a leaking tank loses, moves at the same speed, so the site
        stays consistent with itself however fast you run it.
        """
        real = time.time()
        elapsed = real - self._last_tick
        self._last_tick = real
        if elapsed > 0:
            self.clock_offset += elapsed * (self.clock_speed - 1.0)
        # Everything physical runs off the CONSOLE's clock, not this machine's,
        # so however the clock moves, fast, or jumped forward, the tank
        # loses what it should and the reconciliation still adds up.
        now = time.mktime(self.now())
        if self._commissioned is None:
            self._commissioned = now
            if self.power_off is None:
                # a cold start with nothing behind it: the console came back
                # a few minutes after it went, which is what a power cut
                # looks like when nobody was there. The POWER REMOVED
                # readings go with the moment: twelve minutes of a dark
                # console moved nothing, so what the tank read when the
                # lights went out is what it reads now. Without this the
                # POWER DIAGNOSTIC reports a tank that went from nothing to
                # its whole contents -- see FIDELITY D8.
                self.power_off = now - 12 * 60.0
                self.power_off_state = {
                    n: {"volume": st.get("volume", 0.0),
                        "water_vol": self.water_volume(n),
                        "temp": self.product_temperature(n)}
                    for n, st in self.tank_level.items()}
        was, self._last_console = self._last_console, now
        hours = (now - was) / 3600.0 if was is not None else 0.0
        # the cars arrive before the nozzles are counted, so a car that
        # turns up in this interval sells in it rather than in the next one
        self.traffic.tick(hours)
        # the nozzles first, so the flow BIR sees for this interval is the
        # flow the sales had; then BIR, so a shift opens on what the probe
        # read before anything in this interval moved the level, and every
        # engine gets its look even on the first tick, when no time passed
        self.sales.tick(hours)
        # BIR opens its periods on the level BEFORE this interval's fuel
        # leaves, then the fuel leaves -- key or no key, DIM or no DIM --
        # and then BIR books what it can see of it. See `Sales.draw`.
        self.bir.open_periods()
        self.sales.draw(hours)
        self.bir.tick()
        # Last-Shift Inventory's own shifts, on System Setup's start times
        # rather than BIR's closing times. FIDELITY O22.
        self.shifts.tick()
        for tank, st in self.tank_level.items():
            leak = self.tank_leak.get(tank)
            if leak and hours > 0:
                st["volume"] = max(0.0, st.get("volume", 0.0) - leak * hours)
        from . import wiresensors
        wiresensors.monitor_watch(self, now)
        self.siphon_tick(hours)
        # The forecourt looks at its gauges here, with this interval's fuel
        # already out of the tanks, and calls a truck if one is wanted --
        # before `drops.tick` below, so a load whose lead time is up lands
        # in this pass rather than the next. It used to be minded at the
        # top of `traffic.tick`, which reads the level as of the PREVIOUS
        # tick; at 36,000x that is a seven hour old gauge. See
        # `Traffic.gauge`.
        self.traffic.gauge()
        # the trucks pour BEFORE the watcher looks, so a drop is seen the
        # tick it happens rather than the tick after
        self.drops.tick(hours)
        self.deliveries.tick()
        self.loads.tick()
        # the generator's tanks go under test before the engine looks
        self.inputs.tick()
        self.leaks.tick()
        # the Mag sumps' tests take their readings on the same clock
        self.sumps.tick()
        self.csld.tick()
        self.accuchart.tick()
        self.autodial.tick()
        self.autotx.tick()
        self.vac_tick(hours)
        self.finish_vac_tests()
        self.service_tick(now)
        self.isd_tick(now)
        self.sample_tick(now)
        # The nozzles hang up at the END of the interval, after every engine
        # has had its look at the flow -- not in the middle of it.
        #
        # This ran immediately after `bir.tick()`, on the reasoning that BIR
        # is what reads the rate. BIR is not the only thing that does:
        # AccuChart's metered volume is `meter_flow` times the interval, and
        # it runs below this line, so it was reading a rate the settle had
        # already zeroed. A sale that began and ended inside one interval
        # was invisible to it entirely -- which, once the forecourt runs
        # itself, is every sale on a fast clock.
        self.sales.settle()
        # The console's own look at itself, at the end of its own clock
        # rather than whenever something happens to ask. The panel asked
        # every 700 ms and nothing else ever did, so on a headless console
        # the alarm history stayed empty and -- once a shutdown could
        # actually stop fuel -- a relay assigned to an alarm would not drop
        # the pump until somebody opened a report. `displayed()` is what
        # everything downstream reads, and this is what fills it.
        self.compute_alarms()

    # ---- the ISD daily assessment -------------------------------------------
    def isd_assessment_time(self):
        """When the 24-hour tests run, as minutes past midnight.

        Figure 11: "SET START TIME / TIME: 11:59 PM ... Time defines when
        24-hour ISD tests are run and results posted", plus the post delay
        whose own screen says "DO NOT CHANGE DEFAULT DELAY!". Both were
        stored and read by nothing.
        """
        raw = (self.setting("evr_start_time", 0)
               or isd.DEFAULT_ASSESSMENT).strip().upper()
        digits = "".join(ch if ch.isdigit() else " " for ch in raw).split()
        if len(digits) < 2 or not digits[0].isdigit():
            return 23 * 60 + 59
        hour, minute = int(digits[0]), int(digits[1])
        if "PM" in raw and hour < 12:
            hour += 12
        elif "AM" in raw and hour == 12:
            hour = 0
        minute += self.isd_post_delay_minutes()
        return (hour * 60 + minute) % (24 * 60)

    def isd_post_delay_minutes(self):
        """"TIME DELAY MINUTES", the wait between the assessment's start
        time and its results being posted. Its own screen says "DO NOT
        CHANGE DEFAULT DELAY!", and the default is one."""
        delay = (self.setting("evr_post_delay", 0) or "001").strip()
        return int(delay) if delay.isdigit() else 1

    def isd_assessment_started(self):
        """The moment the last assessment STARTED, or None for none.

        Which is what the report names. 577013-937 Rev J Figure 39 heads
        itself `DEC  8, 2010  4:29 AM` and prints `ASSESSMENT TIME: DEC  7,
        2010 11:59 PM` -- the START time, at the same defaults 576013-635's
        setup group draws: `START TIME 11:59 PM` over `TIME DELAY MINUTES
        1`. The console printed the moment the results were POSTED, so at
        those defaults it read `12:00 AM` and the date rolled with it,
        which is the half of the error the figure shows twice over: its own
        header is the following day and its assessment line is not.
        See FIDELITY I6.
        """
        if not self.isd_assessed:
            return None
        return self.isd_assessed - self.isd_post_delay_minutes() * 60.0

    def isd_tick(self, now):
        """Run the assessment for every whole day the clock has passed.

        The bench runs the clock as fast as you like, so one tick can be a
        week wide, and an escalation that only counted ticks would depend on
        how fast somebody dragged the slider. This counts ASSESSMENTS: how
        many times the programmed time has gone by since the last one.
        """
        if not self.licensed("isd"):
            return
        due = self.isd_assessment_time() * 60.0
        stamp = time.localtime(now)
        midnight = now - (stamp.tm_hour * 3600 + stamp.tm_min * 60
                          + stamp.tm_sec)
        last = midnight + due
        if last > now:
            last -= 86400.0            # today's has not come round yet
        if self.isd_assessed is None:
            # A console that has just come up has not assessed anything; the
            # first assessment is the next one, not every day since 1970.
            self.isd_assessed = last
            return
        while self.isd_assessed < last - 1.0:
            self.isd_assessed += 86400.0
            self.isd_assess()

    def isd_assess(self):
        """One day's worth: count the tests that failed, reset the rest.

        The setup self-test runs first, because its result is one of the
        conditions being counted.

        "If this condition persists for seven more consecutive days, an
        alarm is posted, a failure alarm event is logged and the site is
        shutdown" -- so the day a warning becomes an alarm is a shutdown,
        and it is logged the same way a forced one is.
        """
        self.isd_setup_selftest()
        for test in isd.ESCALATION:
            if self.isd_condition(test) not in ("warn", "fail"):
                self.isd_days.pop(test, None)
                continue
            before = self.isd_state(test)
            self.isd_days[test] = self.isd_days.get(test, 0) + 1
            if before != "fail" and self.isd_state(test) == "fail":
                self.isd_forced_at[test] = self.isd_assessed
                if not self.isd_override:
                    self.isd_events.insert(
                        0, (self.isd_assessed, "ISD SHUTDOWN", ""))

    def isd_setup_faults(self):
        """The setup self-test's criteria that this console is failing.

        577013-819 Rev F p.17 lists seven; each has its own alarm with its
        own one-line definition on its own page, and the same page lists
        them as the COMMON CAUSES of ISD SETUP WARN. So a failing criterion
        posts its own alarm and fails the setup test with it.

        All six are here. MISSING RELAY SETUP was the last, because it
        waited on a store that held one assignment per relay where Rev F
        p.18 wants three to eight; `relay_alarms` and `line_disable_alarms`
        hold the list now, and the page's list is `isd.relay_required`. See
        FIDELITY I1a.
        """
        if not self.licensed("isd"):
            return []
        bad = []
        # "The Fuel Grade Table does not have any hoses assigned to it."
        if not self.isd_hoses():
            bad.append("hose")
        # "There are no vapor recovery (gasoline) tanks defined, or a
        # gasoline pump has not been assigned to a control (shut down)
        # device in at least one tank." The control devices Rev F names are
        # RELAY, PLLD, WPLLD and VLLD, and each has its own tank-number code.
        controlled, devices = set(), []
        for code, kind in (("80B", None), ("785", "plld"), ("7A5", "wplld"),
                           ("752", "vlld")):
            for device in range(1, 17):
                raw = (self.text(code, device) or "").strip()
                if raw.isdigit() and int(raw):
                    controlled.add(int(raw))
                    devices.append((kind, device))
        if not (self.programmed_tanks() and controlled):
            bad.append("tank")
        # "The control device does not have all the correct alarms
        # assigned", Rev F p.18 -- and its field note is the same rule from
        # the other side: a relay given a Tank ID that ISD does not shut a
        # pump down with "will cause a MISSING RELAY SETUP warning". So
        # every device with a tank is asked, whatever else it is for.
        fitted = (self.values.get("SV4000") or "00").strip()[-2:] != "00"
        want = isd.relay_required(self.evr_site(), fitted)
        for kind, device in devices:
            rows = (self.relay_alarms.get(device) if kind is None
                    else self.line_disable_alarms.get((kind, device)))
            have = {(row[0], row[1]) for row in rows or []}
            if not all(pair in have for pair in want):
                bad.insert(0, "relay")
                break
        # "There is no Vapor Flow Meter setup or detected", and the same for
        # the pressure sensor. Both are smart sensor categories at S723.
        kinds = {(self.text("723", n) or "").strip()
                 for n in range(1, 17)}
        if isd.AIR_FLOW_METER not in kinds:
            bad.append("flowmeter")
        if isd.VAPOR_PRESSURE_SENSOR not in kinds:
            bad.append("pressure")
        # "An external input for the OPW and ARID vapor processor cannot be
        # found" -- criterion 6's "non-TLS Console Controlled Processor".
        processor = (self.values.get("SV4000") or "00").strip()[-2:]
        if processor in isd.EXTERNAL_INPUT_PROCESSORS:
            inputs = {(self.text("80C", n) or "").strip()
                      for n in range(1, 17)}
            if isd.VAPOR_PROCESSOR_INPUT not in inputs:
                bad.append("vpinput")
        return bad

    def isd_setup_selftest(self):
        """Run the setup self-test and keep its result.

        "Setup self-testing occurs following power-up as well as at daily
        intervals at the Daily Test Time" -- so it is not a live view of the
        configuration, and it should not be. Every one of these alarms is
        cleared by the same procedure: "enter and exit the Setup Menu using
        the MODE key, then press the red ALARM button on the TLS and the
        condition should clear." A result that recomputed on every read
        would clear itself the moment a value was typed, which is not what
        the console does and not what the diagnostic tells a technician to
        expect.
        """
        self.isd_setup_result = self.isd_setup_faults()
        self.isd_vp_result = self.vapor_processor_faults()
        return self.isd_setup_result

    def vapor_processor_faults(self):
        """Which of the three daily processor tests are failing today.

        577013-819 Rev F pp.29-31 states all three thresholds outright, and
        `vapor_processor_status()` has always produced the figures they are
        measured against -- it just set every verdict to a constant, so the
        report contradicted itself on the paper: `EMISSION TEST NOTEST` over
        `EMISSION LB/1KG 0.32`, which is exactly the failure threshold. See
        FIDELITY I2.
        """
        if not self.licensed("pmc"):
            return set()
        st = self.vapor_processor_status()
        figures = st["figures"]
        bad = set()
        # "is equal to or exceeds", so the comparison is inclusive
        limit = isd.OVER_PRESSURE_WC.get(
            (self.values.get("SV4000") or "00").strip()[-2:],
            isd.OVER_PRESSURE_DEFAULT_WC)
        if figures["Ullage pressure 95th percentile"] >= limit:
            bad.add("vp_pressure")
        if figures["Emission LB/1KG"] > isd.MASS_EMISSION_LB_PER_1KG:
            bad.add("vp_emission")
        if figures["Duty Cycle %"] > isd.DUTY_CYCLE_PERCENT:
            bad.add("vp_duty")
        return bad

    def isd_condition(self, test):
        """Is this test failing its daily assessment?

        Measured where the console can measure it, and taken from the bench
        tile otherwise. Only the SETUP test is measurable from the console's
        own configuration; the rest need a vapour.
        """
        if test == "setup" and self.isd_setup_result:
            return "warn"
        if test in isd.VP_ALARMS:
            return "warn" if test in self.isd_vp_result else None
        return self.isd_forced.get(test)

    def isd_state(self, test):
        """What this test reports, which is not what the bench set.

        The bench says whether the test is FAILING; how long it has been
        failing is what decides between a warning and an alarm. A test set
        straight to "fail" is a technician forcing the outcome and stays
        one, because the bench has to be able to reach the shutdown without
        waiting thirty-one days for it.
        """
        forced = self.isd_condition(test)
        if forced != "warn":
            return forced
        days = self.isd_days.get(test, 0)
        return "fail" if days >= isd.ESCALATION.get(test, 8) else "warn"

    def clear_isd_test(self, code, fp="00", hose="00"):
        """CLEAR TEST AFTER REPAIR, for one menu selection.

        577013-819 Rev F Table 2 maps each selection to the alarms it clears
        AND the date it resets, and this console only ever wrote the date --
        so `isd_forced` stood untouched and the alarm survived the repair
        that was just recorded against it. See FIDELITY I4.

        Everything the state is held in has to go, not just one flag: the
        forced condition, the day count that escalates a warning into a
        shutdown, and the measured results the daily assessment leaves
        behind. A test cleared and still faulty comes back at the next
        assessment, which is what "after repair" means.
        """
        cleared = []
        for test in isd.CLEARS.get(code, ()):
            self.isd_forced.pop(test, None)
            self.isd_forced_at.pop(test, None)
            self.isd_days.pop(test, None)
            self.isd_vp_result.discard(test)
            if test == "setup":
                self.isd_setup_result = []
            cleared.append(test)
        # "All repair dates are saved in the Miscellaneous Event Log", Rev F
        # p.35 -- as 635's "Test Manually Cleared" -- and the clear keeps a
        # record of its own as well, because TEST FAIL CLEAR DATES prints a
        # time and a hose where V85 stores a day. `fp` and `hose` are V85's
        # "FF" and "HH", 00 for all of them. FIDELITY I11.
        if code in isd.CLEAR_EVENT:
            now = time.mktime(self.now())
            self.isd_events.insert(0, (now, isd.CLEAR_EVENT[code],
                                       isd.CLEARED))
            self.isd_clears.insert(0, {"at": now, "test": code,
                                       "fp": fp, "hose": hose})
        return cleared

    def isd_states(self):
        """{test: state} for everything standing, warnings and failures."""
        tests = set(self.isd_forced) | set(isd.ESCALATION)
        return {test: self.isd_state(test) for test in sorted(tests)
                if self.isd_state(test)}

    # ---- the leak test reports a tool asks for ------------------------------
    def leaks_results_report(self, tanks):
        """I208, PREVIOUS IN TANK LEAK TEST RESULTS, display format."""
        out = ["PREVIOUS IN TANK LEAK TEST RESULTS", ""]
        for tank in tanks:
            label = self.text("602", tank) or f"TANK {tank}"
            # 576013-635 Rev AA p.64 and p.71 both head a tank
            # `TANK 1    REGULAR UNLEADED`: the number at column 5 and the
            # label at 10, where 391 and 392 put the number at 6 and the
            # label at 8. Two report styles, both the manual's.
            out.append(f"TANK {tank:<5d}{label}")
            out.append("TEST TYPE  START TIME              "
                       "RESULT     RATE  HOURS  VOLUME")
            results = self.leaks.results.get(("tank", tank)) or {}
            if not results:
                out.append("  NO TEST DATA AVAILABLE")
            for _key, res in leaktest.in_report_order(results):
                out.append(res.line())
            out.append("")
        return chr(10).join(out)

    def leaks_results_record(self, tanks):
        """I208 computer format: TT NN tt mm YYMMDDHHmm RR rate hours volume."""
        out = []
        for tank in tanks:
            results = self.leaks.results.get(("tank", tank)) or {}
            out.append(f"{tank:02d}{len(results):02X}")
            for key, res in leaktest.in_report_order(results):
                # "mm - In-Tank Leak Manifold Status: 00=Tank Not Manifolded
                # During Leak Test, 01=Tank Manifolded", which was hardcoded
                # `00` on a console that knows its siphon and line manifold
                # sets. During the test, so it is what the tank was when
                # the test ran and not what it is now -- the Result carries
                # it. See FIDELITY H4.
                out.append(leaktest.TYPE_CODE[key]
                           + ("01" if res.manifolded else "00")
                           + time.strftime("%y%m%d%H%M",
                                           time.localtime(res.started))
                           + leaktest.RESULT_CODE[res.result]
                           + packed.hexfloat(res.rate)
                           + packed.hexfloat(res.hours)
                           + packed.hexfloat(res.volume))
        return "".join(out)

    def leaks_detect_report(self, tanks):
        """I203, IN-TANK LEAK DETECT, which is the test in progress."""
        out = []
        for tank in tanks:
            label = self.text("602", tank) or f"TANK {tank}"
            run = self.leaks.active("tank", tank)
            out.append(f"TANK {tank:<5d}{label}")
            if run is None:
                out.append("           TEST STATUS: OFF")
                out.append("")
                continue
            now = time.mktime(self.now())
            started = clock_words(run.started)
            out.append(f"           TEST STATUS: ON "
                       f"{leaktest_rate(run.rate_key)} TEST")
            out.append(f"TEST START TIME: {started}"
                       f"     DURATION: {run.hours:g} HOURS")
            volume = self.tank_level.get(tank, {}).get("volume", 0.0)
            out.append(f"START VOLUME: {run.volume:.0f} GALLONS"
                       f"   NOW: {volume:.0f} GALLONS")
            out.append(f"ELAPSED: {run.elapsed(now):.2f} HOURS"
                       f"   REMAINING: {run.remaining(now):.2f} HOURS")
            out.append("")
        return chr(10).join(out)

    def delivered(self, tank, record):
        """A delivery has just finished, which is a thing the console says.

        "When the system recognizes that a delivery occurred, an adjusted
        delivery report is automatically printed."
        """
        self.printed_deliveries.append((tank, record))
        # The amount DELIVERED, which is the gauge rise with what was
        # dispensed during the drop added back -- "takes into consideration
        # all dispensing that occurred during the delivery". BIR was given
        # the bare gauge rise while I20A and the manifold report both added
        # the sales back, so a site that sold anything while the tanker was
        # on the ground had a BIR delivery column short by exactly that, and
        # the shortfall came out again as unexplained variance. FIDELITY Y3.
        #
        # And on BIR's own basis: "the calculation of all BIR volumes will
        # be based on the TC value". The rise comes from the delivery's own
        # two snapshots, which each carry their TC figure, so a drop into a
        # cold tank is corrected against the temperature it was AT rather
        # than the one it has settled to; the sales added back are corrected
        # where they stand. See FIDELITY G10.
        rise = (record.tc_amount if self.bir.temperature_compensated()
                else record.amount)
        self.bir.delivered(tank, rise + self.bir.basis(tank, record.sold),
                           ticket=record.ticket)
        # "This value will default to 0 after the delivery report is
        # printed", of the NEXT delivery density, and the LAST screen is
        # "the density entered in the Next delivery display prior to the
        # printing of the delivery report for that delivery". Printing the
        # report is what moves one to the other. See FIDELITY O13.
        self.delivery_report_printed(tank)

    # ---- the vacuum sensor's interstitial space ----------------------------
    # 576013-818 Figures 6-29 and 6-30 annotate every reading on this sensor,
    # and 577013-873 Rev E, the Vacuum Sensor System Troubleshooting Guide,
    # redraws the same figure on p.4-11 with the same captions and then
    # gives the alarm a page of its own:
    #
    #   LEAK RATE       "Rate in gph at which air is entering the interstitial
    #                    space. A Vac Warning may be posted if this rate is
    #                    >22.4 gph."
    #   TIME TO NO VAC  "Predicted time (in hours : minutes) it would take for
    #                    the interstitial pressure to equal -1 psi. A Vac
    #                    Warning Alarm will be posted if this rate is <8
    #                    hours."
    #   EVAC RATIO      "A Evac Ratio >1.0 is required or evacuation will
    #                    abort. Pressure value (-4.1) is the pressure recorded
    #                    at the time this ratio was calculated."
    #
    # and p.4-4: "A Vacuum Warning will be posted under the following
    # conditions: Leak Rate > 22.4 GPH for 40 minutes; Evacuation ratio of
    # less than 1.0 during a manual evacuation when vacuum level has not
    # reached -4.0." The figure's "may" is those forty minutes. The alarm
    # page does not list the eight hours and the figure on p.4-11 of the
    # same guide still does, so all three conditions stand: UNKNOWNS A27.
    #
    # NO VACUUM ALARM, p.4-6: "posted when compensated pressure is greater
    # than -1.0 psi ... The alarm will clear when vacuum pressure is less
    # than -1.1 psi" -- a tenth of a psi of hysteresis, and p.4-7 names the
    # lower figure "the 'Vacuum OK' threshold, -1.1 psi".
    NO_VACUUM_PSI = -1.0
    VACUUM_OK_PSI = -1.1
    VAC_RATE_WARN_GPH = 22.4
    VAC_RATE_WARN_SECONDS = 40 * 60.0
    VAC_TIME_WARN_HOURS = 8.0
    VAC_EVAC_MIN_RATIO = 1.0
    VAC_EVAC_WARN_PSI = -4.0
    # A held vacuum, which is Figure 6-30's own sample screen: `s 1: VACUUM
    # OK` over `-7.14 PSI VCV: CLOSED`. 577013-873 p.2-2 gives the rule it
    # sits under: the console pumps until "the vacuum reaches either 1 psi
    # above the entered relief valve pressure (relief valve installed), or
    # -8 psi (no relief valve installed)".
    VAC_HELD_PSI = -7.14

    # The interstitial space is not a constant of the sensor. It is the ZONE
    # volume the installer works out from the Secondary Containment Volumes
    # index and types in at VOLUME, S72A: "enter the volume in gallons of the
    # interstitial space being monitored by this Vac Sensor. The permitted
    # range is 0.1 to 500 gallons ... Default is 501" (577013-836 Rev N
    # p.4-4), and the console's leak rate is worked out against it -- "if
    # the volume is programmed significantly larger than the actual volume,
    # a small leak will be calculated as being much larger by the TLS"
    # (577013-873 Rev E p.4-5). Air at atmosphere entering a fixed volume V
    # at R gallons an hour raises the pressure in it by 14.7 * R * t / V, so
    # the manual's definition of TIME TO NO VAC is t = (-1 - P) * V / (14.7
    # * R). This used to be a class constant of 44.27 gallons, derived from
    # a figure's `150:20` that the sensor's own guide prints as `100:00`:
    # see UNKNOWNS A27. An unprogrammed sensor holds the 501 the manual
    # names, which is also the state its Setup Data Warning is posted in.
    VAC_VOLUME_DEFAULT = 501.0

    # What the evacuation pump can pull against the leak, which is the one
    # number here with no derivation behind it at all. 577013-873 p.4-4 says
    # what the ratio MEASURES -- an evacuation that is "not 'making headway'
    # (the vacuum level is not increasing or it is increasing very slowly as
    # indicated by an 'Evac Ratio' less than 1.0)" -- and no page says what
    # its numerator is. Chosen so that Figure 6-29's own sample reads back,
    # 5.2 at 0.123 gph; the same guide's p.2-2 puts the floor below which
    # the console gives up evacuating at 0.1 gpm, ten times this, so the
    # figure is illustrative and the number stays INVENTED. UNKNOWNS A27.
    VAC_EVAC_GPH = 0.6396

    def vac_sensors(self):
        """Every smart sensor programmed as a vacuum sensor.

        Smart sensor category 04, which is the same table `sensor_states`
        reads to decide that this sensor and no other can raise a Vacuum
        Warning or a No Vacuum Alarm.
        """
        return [n for n in range(1, max(self.capacity("smart"), 0) + 1)
                if self.sensor_type("smart", n) == "04"]

    def mag_sensors(self):
        """Every smart sensor programmed as a Mag sensor, category 03.

        What the two Mag sump functions are gated on -- "This menu displays
        only if the console detects a Mag Sump Sensor capable of leak
        detection" -- and what a sump report for all sensors reports.
        """
        if not self.has("smart"):
            return []
        return [n for n in range(1, max(self.capacity("smart"), 0) + 1)
                if self.sensor_type("smart", n) == "03"]

    def vac_leak_rate(self, number):
        """Gallons an hour of air getting into the interstitial space."""
        return float(self.vac_leak.get(int(number), 0.0) or 0.0)

    def vac_psi(self, number):
        """Where the interstitial pressure has got to, in psi gauge."""
        return float(self.vac_pressure.get(int(number), self.VAC_HELD_PSI))

    def vac_time_to_no_vac(self, number):
        """Hours until this sensor's space reaches -1 psi, or None.

        None is "not going anywhere", which is a sensor with no leak on it:
        the manual gives the reading a value and a threshold and says nothing
        about a space that is not filling, and predicting a time that never
        arrives is the one answer that would be wrong in both directions.
        """
        rate = self.vac_leak_rate(number)
        if rate <= 0.0:
            return None
        gap = self.NO_VACUUM_PSI - self.vac_psi(number)
        if gap <= 0.0:
            return 0.0
        return gap * self.vac_volume(number) / (14.7 * rate)

    def vac_volume(self, number):
        """The zone volume programmed at S72A, in gallons, or the default.

        "Use the Containment Volume index to calculate a zone's interstice
        volume in gallons ... you would enter 21.9 (round to nearest tenth of
        a gallon) as the calculated zone volume", 577013-836 Rev N p.4-2.
        """
        volume = self.limit("72A", int(number))
        if volume is None or volume <= 0.0:
            return self.VAC_VOLUME_DEFAULT
        return float(volume)

    def vac_evac_ratio(self, number):
        """How much faster the pump pulls than the leak fills.

        ">1.0 is required or evacuation will abort" is the whole of what the
        manual says about it, so this is a reading rather than a citation.
        """
        rate = self.vac_leak_rate(number)
        if rate <= 0.0:
            return None
        return self.VAC_EVAC_GPH / rate

    def vac_tick(self, hours):
        """The pressure climbs at whatever the bench is letting in.

        Air at atmosphere entering a fixed volume: 14.7 psi for every
        interstitial volume of it. Stops at atmosphere, which is where a
        space with no vacuum left in it is.
        """
        if hours <= 0.0:
            return
        for number in self.vac_sensors():
            rate = self.vac_leak_rate(number)
            if rate <= 0.0:
                continue
            psi = self.vac_psi(number)
            psi += 14.7 * rate * hours / self.vac_volume(number)
            self.vac_pressure[int(number)] = min(psi, 0.0)

    def vac_conditions(self):
        """The two alarms, on the figures' captions and 577013-873's page.

        NO VACUUM ALARM posts above -1.0 psi and clears below -1.1, so a
        sensor sitting between the two keeps whichever it had. VACUUM
        WARNING has three ways in: a leak rate over 22.4 gph that has held
        for forty minutes; under eight hours of vacuum left; and a manual
        evacuation that recorded an Evac Ratio under 1.0 before the space
        had reached -4.0 psi, which stands until a later test reads better.
        """
        now = time.mktime(self.now())
        out = []
        for number in self.vac_sensors():
            n = int(number)
            tt = f"{n:02d}"
            psi = self.vac_psi(number)
            if psi > self.NO_VACUUM_PSI:
                self.vac_no_vac.add(n)
            elif psi < self.VACUUM_OK_PSI:
                self.vac_no_vac.discard(n)
            if n in self.vac_no_vac:
                out.append("2817" + tt)
                continue
            if self.vac_leak_rate(number) > self.VAC_RATE_WARN_GPH:
                since = self.vac_high_since.setdefault(n, now)
                high = now - since >= self.VAC_RATE_WARN_SECONDS
            else:
                self.vac_high_since.pop(n, None)
                high = False
            hours = self.vac_time_to_no_vac(number)
            test = self.vac_result(number) or {}
            ratio = test.get("ratio")
            if (high
                    or (hours is not None
                        and hours < self.VAC_TIME_WARN_HOURS)
                    or (ratio is not None
                        and ratio < self.VAC_EVAC_MIN_RATIO
                        and test.get("psi", psi) > self.VAC_EVAC_WARN_PSI)):
                out.append("2816" + tt)
        return out

    def start_vac_test(self, number):
        """"START MANUAL TEST: sX" over "PRESS <STEP> TO CONTINUE"."""
        self.vac_running[int(number)] = time.mktime(self.now())
        return f"s {int(number)}: MANUAL TEST STARTED"

    def stop_vac_test(self, number):
        """"STOP MANUAL TEST: sX", and the figure's only stop is an ABORT.

        There is no "test complete" screen anywhere in Figure 6-29: the walk
        offers START and STOP and STOP's acknowledgement reads ABORTED. So a
        test that is stopped by hand writes nothing, and the three result
        screens are the last test that was allowed to finish.
        """
        self.vac_running.pop(int(number), None)
        return f"s {int(number)}: MANUAL TEST ABORTED"

    def finish_vac_tests(self):
        """A running test takes its reading on the next look.

        How LONG a vac manual test runs for is on no page here, so nothing
        invents one: the console takes the reading the first time it looks
        after the test was started, which is the shortest thing that is still
        a test rather than a screen. That leaves the abort reachable and the
        stamp honest -- it is when the reading was taken.
        """
        for number in sorted(self.vac_running):
            self.vac_tests[number] = {
                "at": time.mktime(self.now()),
                "rate": self.vac_leak_rate(number),
                "hours": self.vac_time_to_no_vac(number),
                "ratio": self.vac_evac_ratio(number),
                "psi": self.vac_psi(number),
            }
        self.vac_running.clear()

    def vac_result(self, number):
        """The last test's record, or None if this sensor has not run one."""
        return self.vac_tests.get(int(number))

    def vac_valve_open(self, number):
        """VCV, the vacuum control valve. EVAC HOLD is what holds it open."""
        return int(number) in self.vac_hold

    def evacuation_state(self, number):
        """B38's `e`, and the one place this console decides it.

        "e - Evacuation State (Hex) 0=Vacuum Ok, 1=Evacuation Pending,
        2=Evacuation Active, 3=Evacuation Pending Manual, 4=Evacuation Active
        Manual, 5=No Vacuum, 6=Evacuation Hold", 576013-635 Rev AA p.531, and
        `controls.EVACUATION_STATE` is the same list under 097 and 098.

        Three of the seven are reachable here, because three are the states
        this bench can put a sensor into: a held-open valve, a space that has
        lost its vacuum, and neither. Nothing here runs a scheduled
        evacuation, so 1 to 4 are not invented -- UNKNOWNS A73 is what a
        console shows while it is in one of them.

        The hold wins over No Vacuum, because the hold is the phase a
        technician has PUT the sensor into and No Vacuum is a reading about
        it -- and the reading goes out anyway, as the 2817 alarm and as the
        pressure on the same screen.
        """
        if self.vac_valve_open(number):
            return "06"
        if self.vac_psi(number) > self.NO_VACUUM_PSI:
            return "05"
        return "00"

    def start_evac_hold(self, number):
        self.vac_hold.add(int(number))
        return f"s {int(number)}: EVAC HOLD STARTED"

    def stop_evac_hold(self, number):
        self.vac_hold.discard(int(number))
        return f"s {int(number)}: EVAC HOLD ABORTED"

    # ---- the Service Notice session ----------------------------------------
    # "When service is performed at a site, 'false' alarms and 'false'
    # deliveries can be generated. 'False' alarms can trigger unneeded
    # service dispatches to the site and 'false' deliveries can cause
    # reconciliation problems." 576013-623 Rev AN p.5-27 is the whole
    # feature, and Figure 6-5 of the Troubleshooting Guide is the panel that
    # opens and closes one. A session is a window a technician opens from
    # Diagnostic Mode: it runs for a selectable 1 to 8 hours, it is recorded
    # start and end, and while it is open a site with Delivery Override
    # enabled keeps its deliveries out of both delivery histories.
    SESSION_HOURS = (1, 8)          # "1 to 8 hours ... Default is 2 hours"

    def service_notice(self):
        """S56600, the FEATURE. The session is a thing you do with it."""
        return (self.values.get("S56600") or "0")[:1] == "1"

    def delivery_override(self):
        """S567, and the switch that decides what a session does to a drop."""
        return self.setting("delivery_override", 0, "DISABLED") == "ENABLED"

    def service_session(self):
        """The session that is open, or None.

        11B reads `service_sessions[0]` as the running one, so the list is
        newest first and an open session is the head with no end on it.
        """
        first = self.service_sessions[0] if self.service_sessions else None
        return first if first and first.get("end") is None else None

    def service_session_hours(self):
        """The duration timer, 1 to 8 hours, default 2."""
        raw = str(self.setting("service_duration", 0, "2")).strip()
        lo, hi = self.SESSION_HOURS
        return int(raw) if raw.isdigit() and lo <= int(raw) <= hi else 2

    def delivery_running(self):
        """Is a tanker on the ground anywhere on the site?"""
        return any(self.deliveries.in_progress(tank) is not None
                   for tank in self.tank_level)

    def start_service_session(self):
        """Open one, or say why not.

        Figure 6-5 annotates the DISABLED screen "If there is a delivery in
        progress, then cannot change to Enable, and it will display
        'DISABLED DEL IN PROGRESS'", and the ENABLED screen "Can only change
        to Enabled if there are no deliveries in progress when Delivery
        Override is Enabled". The second is the fuller statement of the
        first, and the reason is in the setup manual: it is Delivery Override
        that takes a drop out of the histories, so it is only with the
        override on that a session opened mid-drop would cut one delivery in
        half. See FIDELITY D9 for the ambiguity, which is the manual's.
        """
        if self.service_session() is not None:
            return "ENABLED"
        if self.delivery_override() and self.delivery_running():
            return "DISABLED DEL IN PROGRESS"
        self.service_sessions.insert(0, {"start": time.mktime(self.now()),
                                         "end": None})
        # "contains the last 10 records"
        del self.service_sessions[10:]
        return "ENABLED"

    def end_service_session(self):
        """Close the open one. Ending it and timing out are the same end."""
        one = self.service_session()
        if one is None:
            return "DISABLED"
        one["end"] = time.mktime(self.now())
        return "DISABLED"

    def service_tick(self, now):
        """"or automatically after timeout (max 8 hours)".

        The bench runs the clock as fast as you like, so this is measured
        against the console's own clock rather than counted in ticks.
        """
        one = self.service_session()
        if one is None:
            return
        if now - one["start"] >= self.service_session_hours() * 3600.0:
            one["end"] = one["start"] + self.service_session_hours() * 3600.0

    def post(self, aa, nn, device):
        """Raise an alarm that is a RESULT rather than a condition."""
        self.posted.add(f"{aa}{nn}{int(device):02d}")

    def clear_posted(self, aa=None, nn=None, device=None):
        if aa is None:
            self.posted.clear()
            return
        self.posted.discard(f"{aa}{nn}{int(device):02d}")

    def set_clock(self):
        """Take the clock from whatever S50100 now holds."""
        raw = (self.values.get("S50100") or "").strip()
        if len(raw) < 10 or not raw[:10].isdigit():
            return False
        yy, mm, dd = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
        hh, mi = int(raw[6:8]), int(raw[8:10])
        year = 2000 + yy if yy < 70 else 1900 + yy
        try:
            want = time.mktime((year, mm, dd, hh, mi, 0, 0, 1, -1))
        except (ValueError, OverflowError):
            return False
        before = time.mktime(self.now())
        self.clock_offset = want - time.time()
        self.clock_set = True
        # "Time Change Detected at:", stamped on the clock it came to with
        # the one it left in the action column: p.668's row is dated
        # 10-04-02 among April's events and prints 10-06-01 beside it, and
        # every event after it is April's. FIDELITY I8, UNKNOWNS A57.
        self.apm_log("01", "03", data=before)
        return True

    def clock_text(self):
        """The status line, in the format programmed at DATE/TIME FORMAT."""
        t = self.now()
        fmt = (self.values.get("S50F00") or "01").strip()[-2:]
        if fmt == "02":
            return (clock_date(t, sep=" ")
                    + time.strftime(" %H:%M:%S", t))
        if fmt == "03":
            return time.strftime("%m-%d-%y %I:%M:%S %p", t).upper()
        if fmt == "04":
            return time.strftime("%m-%d-%y %H:%M:%S", t)
        if fmt == "05":
            return time.strftime("%d-%m-%y %H:%M:%S", t)
        if fmt == "06":
            return time.strftime("%y-%m-%d %H:%M:%S", t)
        return clock_words(t, seconds=True)

    def clock_stamp(self):
        """The date and time a REPORT is stamped with, which has no seconds.

        The DATE/TIME FORMAT setting is written with them -- the setup screen
        offers "MON DD, YYYY HH:MM:SS xM" -- and that is the live status line
        on the panel, where a clock ticks. What goes on paper and down the
        wire does not tick, and it is stamped to the minute.

        The manuals are one-sided about it: 576013-635 draws
        "MMM DD, YYYY HH:MM XM" over its responses 332 times against twelve
        of the seconds form, and every one of those twelve is a description
        of the FORMAT SETTING rather than a stamp on a report -- they are in
        the setup, board replacement, troubleshooting, comm module and ISD
        manuals, and in none of the report samples. A real console agrees:
        every stamp on the tape in tests/tape/ is to the minute.
        """
        text = self.clock_text()
        # the programmed formats differ, so take the seconds out of whichever
        # one is set rather than re-spelling six of them
        return re.sub(SECONDS_IN_STAMP, lambda m: m.group(1), text)

    # ---- what the console says it is ---------------------------------------
    def features(self):
        """The SYSTEM FEATURES list: the cards fitted and the keys licensed.

        A console prints the features it can actually serve, which is all
        three gates: the card has to be in the cage, the S-Module has to
        license it, and the software in the console has to know what it is.
        """
        out = []
        for key, feats in MODULE_FEATURES.items():
            if not self.has(key):
                continue
            out.extend(f for f in feats
                       if self.supports(FEATURE_LINE.get(f.strip())))
        for key, name, _part in SOFTWARE_MODULES:
            if not self.licensed(key):
                continue
            # A feature the CARD list has already named is not named again
            # under its licence's longer title. CSLD is both: the probe
            # module earns the line and the S-Module licenses it, so this
            # printed `CSLD` and `CONTINUOUS STATISTICAL LEAK DETECTION` one
            # under the other. A real console prints the short one alone --
            # `tests/console_capture/raw/I90200.bin`. See FIDELITY S18.
            if key.upper() in out or name.upper() in out:
                continue
            out.append(name.upper())
        return out

    def revision_flags(self):
        """905's feature flags, in the manual's order, AA to LL.

        The SYSTEM FEATURES list a console prints is prose: 905's computer
        format is the same answer enumerated, twelve named flags each 00 or
        01, and the names are the manual's own (notes 10 to 21). They are the
        same three gates the printed list uses, so a card pulled out of the
        cage or a key never cut turns its flag off:

        - the two in-tank tests are the probe module, which is what runs them
        - TANKER LOAD is "a key-enabled option", S513, the flag the Tanker
          Load Report itself waits for
        - the three line leak flags split a distinction this bench does not
          make. The Setup Manual names three keys, 0.20 Repetitive, 0.10
          Repetitive and 0.10 On Demand; the S-Module list here carries the
          first and the last, so PRECISION PLLD and PRECISION PLLD ON DEMAND
          both come from the 0.10 key until the middle one is modelled
        - SPECIAL 3-TANK/LINE CONSOLE is a different console from this one
        - UNUSED WAS PMC is what it says, and reads 00 on every console
        """
        probe = self.has("probe")
        line = self.has("plld") or self.has("wplld")
        state = {
            "PERIODIC IN-TANK TESTS":      probe,
            "ANNUAL IN-TANK TESTS":        probe,
            "CSLD":                        probe and self.licensed("csld"),
            "BIR":                         self.licensed("bir"),
            "FUEL MANAGER":                self.licensed("fuelman"),
            "PRECISION PLLD":              line and self.licensed("plld010"),
            "TANKER LOAD":                 self.loads.enabled(),
            "0.2 GPH PLLD":                line and self.licensed("plld020"),
            "PRECISION PLLD ON DEMAND":    line and self.licensed("plld010"),
            "SPECIAL 3-TANK/LINE CONSOLE": False,
            "ISD":                         self.licensed("isd"),
            "UNUSED WAS PMC":              False,
        }
        return [(name, bool(state[name]))
                for name in versions.revision_flags(self.version)]

    def revision_report(self):
        """SOFTWARE REVISION LEVEL, as the console prints it."""
        s = self.software_info()
        return (["SOFTWARE REVISION LEVEL",
                 f"VERSION {s['version']}",
                 f"SOFTWARE# {s['number']}",
                 f"CREATED - {s['created']}",
                 "",
                 f"S-MODULE# {s['smodule']}",
                 "SYSTEM FEATURES:"]
                # p.488 indents a feature two, not three -- and the console
                # runs the first one straight under the heading where p.488
                # leaves a blank line. A family header keeps the margin.
                # `I90200.bin` and `I90500.bin` both. See FIDELITY S18.
                + [f if f in FEATURE_HEADERS else "  " + f
                   for f in self.features()])

    def diag_line(self, text, device):
        """A diagnostic screen with this console's own numbers in it.

        The manual prints these screens for device 1 of a site that is not
        yours. Point them at the device the panel is on, and give them the
        label that device was programmed with.
        """
        if len(text) > 3 and text[1] == " " and text[3] == ":"                 and (text[2].isdigit() or text[2] in "X#"):
            # "T 1:" and "T X:" are both the manual drawing a device number,
            # and so is "T #:" -- Figure 6-11's two CSLD MONTHLY screens, which
            # reached the glass as `T #: REGULAR UNLEADED`. FIDELITY D23.
            text = f"{text[0]} {device}:{text[4:]}"
        code = DEVICE_LABEL_CODE.get(text[0]) if text[1:2] == " " else None
        if code is None and text.startswith("("):
            # 576013-818 Rev AA Figure 6-7 heads its screens with the product
            # label on its own, no device letter in front: these are per
            # PRODUCT, and the figure says TANK/SENSOR cycles the tanks
            code = "602"
        label = self.text(code, device) if code else ""
        if not label and (code or DEVICE_WORD.get(text[0])):
            # An unlabelled device is not "(PRODUCT LABEL)" on a console; it
            # is the device, named by what it is.
            #
            # And a device with no label FIELD is the same thing: `g`, the
            # ground temperature sensor, has a Table 29-1 device code and a
            # word here and no setting anywhere that names one, so
            # `g 1: LOCATION` drew the manual's own word LOCATION where V,
            # G, C and H all draw a label. Which setting should supply one
            # is still unknown -- see FIDELITY D1 -- and until there is one
            # the console says what the device is.
            label = f"{DEVICE_WORD.get(text[0], 'DEVICE')} {device}"
        if "PROBE TYPE" in text:
            text = text.replace("(PROBE TYPE)", self.probe_type(device))
            text = text.replace("PROBE TYPE", self.probe_type(device))
        for placeholder in ("(PRODUCT LABEL)", "(Product Label)", "(Label)",
                            "LOCATION", "(Vac Sensor Label)",
                            "<PUMP MONITOR LABEL>",
                            "(ATMP Sensor Label)"):
            if placeholder in text:
                text = text.replace(placeholder, label or placeholder)
                break
        return text

    # ---- the archive utility ------------------------------------------------
    def archive_path(self):
        base = self.state_path or os.path.join(_HERE, "console_state.json")
        return os.path.splitext(base)[0] + ".vrset"

    # The settings the panel programmes that live outside the wire format.
    # A real archive is "all setup data", so these travel with it; they are
    # written as comment lines, which `seed` already skips, so an archive is
    # still a plain .vrset a tool can read.
    ARCHIVE_EXTRA = ("chart_code", "chart_code_set", "serial_number",
                     "wm_office")

    # 576013-623 Rev AN, the NOTE beside the restore procedure: "If you are
    # restoring after a reboot (switching the console Off and then back On),
    # the system will wait 5 minutes before processing your request to
    # restore archived setup data. This delay is to allow all hardware to
    # initialize."
    RESTORE_HOLD_SECONDS = 300.0

    def restore_hold(self):
        """How much of the post-reboot hold a restore still has to sit out.

        Seconds, and zero once the hardware has had its five minutes. Zero
        as well on a console nobody has switched off and back on, because
        the note is about restoring "after a reboot" and a console somebody
        has been standing in front of all morning has long since initialised.
        Real seconds rather than console ones: what is being waited for is
        hardware coming up, which the clock setting cannot hurry.
        """
        if self.reboot_at is None:
            return 0.0
        left = self.RESTORE_HOLD_SECONDS - (time.time() - self.reboot_at)
        return left if left > 0.0 else 0.0

    def archive_save(self, path=None):
        """SAVE SETUP DATA, everything programmed, to the E2 chip.

        Written in the same format --seed reads, so an archive taken here can
        be poured into another console.

        `path` writes the same bytes somewhere a person chose instead of to
        the chip. The console's own SAVE SETUP DATA never passes it -- the
        chip is a fixed place and that is the point of it -- but the bench's
        "Save programming to file" does, so a site worked up here can be
        carried to another machine and seeded into it.

        SAVE SETUP DATA is reachable from the front panel, and the front
        panel is reachable over the tunnel, so this is a network-reachable
        write like the others and the freeze covers it too.
        """
        if exposed.refused("setup data archive"):
            return
        try:
            with atomicfile.replacing(path or self.archive_path()) as fh:
                fh.write("# TLS-350 archived setup data\n")
                fh.write(f"#WHEN\t{time.strftime('%y%m%d%H%M', self.now())}\n")
                for key in self.ARCHIVE_EXTRA:
                    fh.write(f"#SET\t{key}\t{getattr(self, key) or ''}\n")
                for (key, device), value in sorted(
                        self.settings.items(), key=lambda kv: str(kv[0])):
                    fh.write(f"#CFG\t{key}\t{device}\t{value}\n")
                for tank, capacity in sorted(self.tank_capacity.items()):
                    fh.write(f"#CAP\t{tank}\t{capacity}\n")
                # the meter's full identity, `MeterId.__str__`
                for meter, tank in sorted(self.meters.items()):
                    fh.write(f"#MTR\t{meter}\t{tank}\n")
                # the alarm lists, line disables, 7B1 and 7B4: without them a
                # restore put a relay group's YES back and not the list the
                # YES stands for. One comment line, so `seed` skips it.
                fh.write(f"#PRG\t{json.dumps(self._stores_json())}\n")
                for code, data in sorted(self.values.items()):
                    blob = data.encode("ascii", "replace").hex().upper()
                    fh.write(f"{code}\t{blob}\n")
            return len(self.values)
        except OSError:
            return -1

    def archive_exists(self):
        """Is there anything in the E2 chip to put back?"""
        return os.path.exists(self.archive_path())

    def archive_bytes(self):
        """How much went into the chip, which is what the save record prints.

        "BYTES: XXXX", the last line of the record 576013-637 p.5 prints when
        a save finishes. No manual states the chip's capacity or the format
        it holds, so the honest number is the size of what was actually
        written.
        """
        try:
            return os.path.getsize(self.archive_path())
        except OSError:
            return 0

    def archive_when(self):
        """When the archive in the chip was taken, or "" if there is none."""
        try:
            with open(self.archive_path(), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#WHEN"):
                        return line.split("\t")[1].strip()
                    if not line.startswith("#"):
                        break
        except OSError:
            pass
        return ""

    def archive_restore(self):
        """RESTORE SETUP DATA, put the archive back.

        A restore REPLACES the programming rather than merging into it, which
        is what "clear current system setup data and replace it with system
        setup data you stored previously" says, and what makes it useful
        after somebody has programmed the console into a corner.
        """
        path = self.archive_path()
        if not os.path.exists(path):
            return -1
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return -1
        # Everything the archive carries is cleared first, or a restore merges:
        # the capacities and the meter map were not, so a tank capacity
        # entered after the save survived the restore of a chip without it.
        self.values.clear()
        self.settings.clear()
        self.tank_capacity.clear()
        self.meters.clear()
        self._clear_stores()
        for line in lines:
            if line.startswith("#SET\t"):
                # `#SET key value` is three fields. This unpacked four, so
                # the value went to a throwaway and the chart security code,
                # the serial number and the W&M office all came back blank.
                _tag, key, value = (line.split("\t", 2) + ["", ""])[:3]
                if key in self.ARCHIVE_EXTRA:
                    setattr(self, key, value)
            elif line.startswith("#PRG\t"):
                try:
                    self._stores_load(json.loads(line[len("#PRG\t"):]))
                except (ValueError, AttributeError):
                    pass
            elif line.startswith("#CFG\t"):
                parts = line.split("\t")
                if len(parts) >= 4:
                    self.settings[(parts[1], int(parts[2]))] = parts[3]
            elif line.startswith("#CAP\t"):
                parts = line.split("\t")
                if len(parts) >= 3:
                    self.tank_capacity[int(parts[1])] = float(parts[2])
            elif line.startswith("#MTR\t"):
                parts = line.split("\t")
                if len(parts) >= 3:
                    self.meters[meter_key(parts[1])] = int(parts[2])
        n = self.seed(path)
        self.save()
        return n

    def archive_clear(self):
        """CLEAR SETUP DATA.

        "To clear all current setup data in the EEPROM, press CHANGE, then
        press ENTER ... the system starts clearing all current setup
        information in the EEPROM." The EEPROM, not the console: this throws
        the ARCHIVE away and leaves the site running on what it is programmed
        with. Clearing the console itself is a cold start with the battery
        switch off, which is what the bench's Reset button is.

        Frozen, this does nothing and says so. `archive_save` was guarded
        and this, its sibling, was not -- and it is the more dangerous of
        the two, because it DELETES. `S853` reaches it straight off the
        tunnel with no authentication on a default card, so an exposed
        console would answer one four-byte frame by removing the operator's
        archive from disk. The freeze's promise is that nothing the network
        says reaches the disk; deleting is reaching the disk.
        """
        if exposed.refused("clear setup data archive"):
            return -1
        path = self.archive_path()
        if not os.path.exists(path):
            return 0
        n = 0
        try:
            with open(path, encoding="utf-8") as fh:
                n = sum(1 for line in fh if not line.startswith("#"))
            os.remove(path)
        except OSError:
            return -1
        return n

    def security_code(self):
        """The six digits Setup and Diagnostic ask for, or "" if disabled.

        "If you enable the System Security Code, you will be required to enter
        this code before you can access any setup or diagnostic function."
        """
        code = (self.values.get("S50400") or "").strip()
        return "" if code in ("", "000000") else code

    def rs232_enforces_security(self):
        """Whether a serial command needs the security code to be answered.

        Both have to be true: the code programmed (504), and the card's
        security DIP switch on. Either alone does nothing, which is what the
        manual describes.
        """
        return bool(self.rs232_security and self.security_code())

    def rs232_eom_chars(self, port=1):
        """The extra characters appended after <ETX> on computer-format
        replies, or b"" for the bare <ETX>.

        576013-635: End of Message (531) enables the feature; ETX-per-port
        (537) sets up to two characters, each 0-255, held as four hex digits
        AABB per port. A first character of NUL (00) reverts to the default
        (nothing extra); if only the second is NUL, just the first is sent.
        With 531 disabled, nothing extra is sent whatever 537 holds.
        """
        if (self.values.get("S53100") or "0").strip() not in ("1",):
            return b""
        raw = (self.values.get(f"S537{port:02d}")
               or self.values.get("S53799") or "").strip()
        if len(raw) < 4:
            return b""
        try:
            a, b = int(raw[0:2], 16), int(raw[2:4], 16)
        except ValueError:
            return b""
        if a == 0:                       # NUL first char: default, nothing
            return b""
        return bytes([a]) if b == 0 else bytes([a, b])

    def slot_report(self):
        """SYSTEM CONFIGURATION, slot by slot, as the console shows it.

        "POR = ID resistor value of module in this slot read at last system
        reset. C = current ID resistor value of module in this slot." Each
        compartment is walked in turn and an empty slot reads UNUSED.
        """
        out = []
        for slot, bay, key, name in self.cage_slots():
            por, now = (self.empty_slot_reading(bay, slot) if key is None
                        else self.module_id_resistance(key, slot))
            out.append((f"SLOT {slot} {name}",
                        self.resistance_line(por, now)))
        # The comm bay is walked by POSITION rather than by card: a dual-port
        # module in slot 4 has an ID resistor on each half and reads as two
        # boards here, which is what six rows across four slots are for.
        for port in range(1, BAY_SLOTS["comm"] + 1):
            name, por, now = self.port_reading(port)
            out.append((f"COMM {port} {name}", self.resistance_line(por, now)))
        return out

    @staticmethod
    def resistance_line(por, now):
        """The second line of a card cage screen, in the figure's own two forms.

        576013-818 Figure 6-2 draws it twice, and the difference is the
        spaces:

            SLOT 1 4 PROBE/ G. T.        COMM 6 UNUSED
            POR= XXXXXX C= XXXXXX        POR=XXXXXXXX C=XXXXXXXX

        Six digits get a space after each `=` and eight do not, and both come
        to twenty-three characters -- which is what a 24-column display is
        for. This drew the spaced form for everything, so every UNUSED slot
        on every console came to twenty-five, the panel clipped it, and the
        current resistance read one tenth of its real value:
        `POR= 15000000 C= 1500000`. See FIDELITY D12.
        """
        if max(por, now) < 1000000:
            return f"POR= {por:6d} C= {now:6d}"
        return f"POR={por:8d} C={now:8d}"

    def port_reading(self, port, paper=False):
        """(slot-line name, at the last reset, now) for a comm position.

        An empty position reads the open circuit function 102's own sample
        prints, `COMM 4 UNUSED 15000000 15000000`; a fitted one reads the ID
        resistor on that HALF of the card, which for a dual-port module is
        not the same resistor at both of its positions.

        `paper` picks the printout's name over the screen's -- see
        `slot_name`. `UNUSED` is `UNUSED` on both.
        """
        half = self.comm_half(port, paper)
        if half is None:
            empty = EMPTY_OHMS["comm"]
            return "UNUSED", empty, empty
        por, now = self.module_id_resistance(
            self.comm_positions()[port][1], 100 + port, ohms=half[2])
        return half[1], por, now

    def module_id_resistance(self, module, slot=1, ohms=None):
        """(at the last reset, now) for the ID resistor in that slot.

        A real one is a resistor being measured, so it is never exactly its
        nominal value and never exactly the same twice: the manual's own
        sample has one module at 164040 when the console last started and
        166912 now. The two agreeing to within a percent is the healthy
        reading; a module pulled since the reset is what makes them differ by
        orders of magnitude.
        """
        nominal = ohms if ohms is not None else self.card(module)[2]
        if nominal is None:
            nominal = 100000
        por = int(readings.fixed(nominal * 0.985, nominal * 1.03,
                                 "idpor", module, slot))
        now = int(readings.wander(self, por * 0.997, por * 1.006,
                                  "idnow", module, slot, swing=0.5,
                                  period=900.0))
        return por, now

    # "TT - Type of Module (Hex)" in i10200, for the cards this cage takes
    MODULE_TYPE = {"probe": "01", "vapor": "02", "liquid": "03",
                   "relay": "04", "io": "05", "rs232": "07", "modem": "08",
                   "mdim": "18", "edim": "19", "rdu": "0D",
                   "vlld": "09", "gw": "0B", "2wire": "0C", "3wire": "10",
                   "pump": "14", "plld": "1B", "wplld": "22", "smart": "28",
                   "mt": "2D", "pumpmon": "2E", "vmc": "2F"}

    # What an empty slot reads, on every console anyone here can check.
    #
    # 15,000,000 is a firmware constant and not a measurement, and the
    # PACKED form is what proves it: a 2015 capture sends `4B64E1C0` for
    # both columns of all nineteen of its empty slots, which is exactly
    # 15,000,000.0 and is bit-identical row to row. A measured open circuit
    # does not land on a round decimal twice, let alone nineteen times, and
    # nothing in any capture or sample ever reads ABOVE it. The type code
    # beside it is `00`, "Not used", so the console is asserting emptiness
    # rather than reporting a card it cannot identify.
    #
    # **This used to give the intrinsically safe bay a band of its own**,
    # near 10.2 million and drifting, off the seven empty I.S. rows of
    # function 102's sample -- on the argument that the I.S. bay reads its
    # open circuit through its barrier, so an empty slot there is a reading
    # like any other and only the other two bays are a sentinel. Every real
    # console contradicts it: the captured tape of 2006 and the 2015 capture
    # both print the rail in all sixteen slots and all six comm positions.
    # See FIDELITY X5 for the original reasoning, M22 for what broke it, and
    # UNKNOWNS A75 for the sample's own rows, which are real measurements of
    # something and are not explained by a barrier.
    EMPTY_READING = 15000000.0

    def empty_slot_reading(self, bay, slot):
        """(power-on reset, current) for a slot with nothing in it.

        The same rail in every bay, in both columns, because that is what
        the hardware sends. `bay` and `slot` are kept because the caller has
        them and because the question "does this bay differ" is one this
        console has now answered twice.
        """
        nominal = int(EMPTY_OHMS.get(bay, self.EMPTY_READING))
        return nominal, nominal

    def cage_slots(self, paper=False):
        """[(slot, bay, key, name)] for all sixteen slots, in slot order.

        `paper` picks the printout's spelling of a card over the screen's,
        where a card has two -- see `slot_name`. The two readers below are
        the two surfaces: `slot_report` is the glass and `slot_readings`
        feeds function 102's paper.

        The two bays are fixed RANGES, not a running count. The I.S. bay
        is slots 1 to 8 and the power bay 9 to 16 whatever is in them, which
        is what function 102's sample shows: one 4-probe at slot 1, UNUSED
        at 2 through 8, and the power bay's `4 INPUT BOARD` at slot 9.

        Both readers of this cage numbered it by walking cards instead, and
        they disagreed with the manual in two different directions.
        `slot_readings`, which I102 prints, ran ONE counter through both
        bays, so that 4-input board came out at slot 2 -- a power-bay card
        wearing an I.S. slot number. `slot_report`, which the panel steps
        through, restarted at 1 for each bay, so a technician stepping the
        card cage met two SLOT 1s and two SLOT 2s. One cage, one numbering,
        and it is this one. See FIDELITY X5.
        """
        out, base = [], 0
        for bay in ("is", "power"):
            slot, last = base, base + BAY_SLOTS[bay]
            for key, name, _part, mbay, _wires, _most in MODULES:
                if mbay != bay:
                    continue
                for _ in range(self.count(key)):
                    if slot >= last:
                        break            # a bay cannot hold more than it has
                    slot += 1
                    out.append((slot, bay, key, self.slot_name(key, paper)))
            out += [(n, bay, None, "UNUSED") for n in range(slot + 1, last + 1)]
            base = last
        return out

    def slot_readings(self):
        """[(slot, key, name, power-on reset, current)] down the whole cage.

        "POR = ID resistor value of module in this slot read at last system
        reset. C = current ID resistor value." A board that has not been
        swapped since the reset reads the same for both, which is the point of
        printing them side by side.
        """
        # A card's two numbers are its ID RESISTANCE, which is the whole
        # point of the screen: Table 6-1 says what each card should read, and
        # a technician compares the two columns to see whether the one in the
        # slot still reads like itself. This used to print crc32(key) %%
        # 1000000, a hash of the module's name -- a stable number per card,
        # which looks plausible and means nothing, and which made POR and
        # CURRENT identical so no card could ever be seen to drift.
        # module_id_resistance already did it properly and nothing called it.
        #
        # ...and the PAPER's spelling of each card, because this is what
        # function 102 prints and `slot_report` above is the glass.
        out = []
        for slot, bay, key, name in self.cage_slots(paper=True):
            por, now = (self.empty_slot_reading(bay, slot) if key is None
                        else self.module_id_resistance(key, slot))
            out.append((slot, key, name, float(por), float(now)))
        # Same again for the communication bay: its cards have ID resistances
        # too -- RS-232 15K, SiteFax 47K, MT 402K, WPLLD Comm 200K -- and the
        # same two columns to compare. It is walked by POSITION, because a
        # dual-port module carries one of those resistors on each half and
        # reads as two boards here. See FIDELITY M7.
        for port in range(1, BAY_SLOTS["comm"] + 1):
            seat = self.comm_positions().get(port)
            name, por, now = self.port_reading(port, paper=True)
            out.append((-port, seat[1] if seat else None, name,
                        float(por), float(now)))
        return out

    def configuration_lines(self):
        """I102's SYSTEM CONFIGURATION, slot by slot."""
        rows = ["SYSTEM CONFIGURATION",
                "SLOT  BOARD TYPE                    POWER ON RESET"
                "     CURRENT"]
        for slot, _key, name, por, current in self.slot_readings():
            # p.49: the slot number right against 2 and a comm position's
            # `COMM n` at 6, then the board name, and the two readings held
            # right against 44 and 61 whichever kind of row it is -- which is
            # what makes the two columns comparable down the page
            where = f"{slot:3d}   " if slot > 0 else f"      COMM {-slot} "
            rows.append(f"{(where + name)[:36]:<36s}"
                        f"{por:9.0f}{current:17.0f}")
        return rows

    def configuration_records(self):
        """The same, packed: NN then SS TT FFFFFFFF CCCCCCCC per module."""
        rows = self.slot_readings()
        out = f"{len(rows):02X}"
        for slot, key, _name, por, current in rows:
            number = slot if slot > 0 else 16 - slot
            out += f"{number:02X}{self.module_type(key)}"
            out += packed.hexfloat(por)
            out += packed.hexfloat(current)
        return out

    # ---- what the technician collects --------------------------------------
    CSLD_TABLES = {"A51": "RATE TABLE", "A52": "RATE TEST",
                   "A53": "VOLUME TABLE",
                   "A54": "MOVING AVERAGE TABLE"}

    # IA54's "SS - Current Test State".
    CSLD_TEST_STATE = {"0": "NO TEST", "1": "TEST PRE-START",
                       "2": "TEST IN-PROGRESS", "3": "TEST COMPLETE",
                       "4": "ABORT TEST", "5": "PRE-DELAY", "6": "END DELAY"}

    def csld_table_lines(self, token, tank):
        """One of the four CSLD diagnostics tables, as the guide prints them.

        Four reports, four shapes, and this console used to print two: A52
        fell through to A51's branch, and A53 and A54 printed rate samples
        where the manual prints hourly volumes and 30-second probe data.
        See FIDELITY K1.
        """
        label = self.text("602", tank) or f"TANK {tank}"
        head = [f"CSLD DIAGNOSTICS: {self.CSLD_TABLES.get(token, token)}",
                f"T {tank}:{label}"]
        builder = {"A51": self._csld_rate_table,
                   "A52": self._csld_rate_test,
                   "A53": self._csld_volume_table,
                   "A54": self._csld_moving_average}.get(token)
        if builder is None:
            return head + ["NO CSLD DATA"]
        return builder(tank, head)

    def _csld_rate_table(self, tank, head):
        """IA51, thirteen columns, and chapter 11's method is a walk down
        them: TIME ST LRT AVTMP TPTMP BDTMP TMRT DSPNS VOL INTVL DEL ULLG
        EVAP. The rate is negated on the way out -- see FIDELITY K2.
        """
        rows = self.csld.table(tank)
        if not rows:
            return head + ["NO CSLD DATA"]
        out = head + ["      TIME ST    LRT AVTMP TPTMP BDTMP  TMRT"
                      " DSPNS   VOL INTVL    DEL ULLG EVAP"]
        for one in rows:
            out.append(
                time.strftime("%y%m%d%H%M", time.localtime(one["at"]))
                + f"{int(one['state']):3d}{-one['rate']:7.3f}"
                + f"{one['temp']:6.1f}{one['toptemp']:6.1f}"
                + f"{one['bdtemp']:6.1f}{one['tmrt']:6.2f}"
                + f"{one['dispns']:6.0f}{one['volume']:6.0f}"
                + f"{one['interval']:6.1f}{one['delivered']:7.1f}"
                + f"{one['area']:5.0f}{one['evap']:7.3f}")
        return out

    def _csld_rate_test(self, tank, head):
        """IA52, fourteen fields of its own: TK DATE LRATE INTVL ST AVLRTE
        VOL C1 C3 FDBK ACPT THPUT EVAP RJT.
        """
        rows = self.csld.table(tank)
        if not rows:
            return head + ["NO CSLD DATA"]
        e = self.csld.evaluation(tank)
        out = head + ["TK        DATE  LRATE INTVL ST  AVLRTE    VOL"
                      "  C1  C3 FDBK ACPT  THPUT EVAP  RJT"]
        # LRATE is the COMPENSATED rate and AVLRTE the UNCOMPENSATED one.
        # 576013-818 chapter 11 says so in as many words -- "LRATE
        # Compensated leak rate in gph", "AVLRTE Uncompensated Leak Rate" --
        # which is the opposite way round from what the names suggest.
        out.append(
            f"{tank:2d}  "
            + time.strftime("%y%m%d%H%M", time.localtime(e["at"]))
            + f"{-e['compensated']:7.3f}{e['hours']:6.1f}"
            + f"{int(e['status']):3d}{-e['uncompensated']:8.3f}"
            + f"{e['volume']:7.0f}{e['records']:4d}{e['accepted']:4d}"
            + f"{e['feedback']:5.1f}{e['acceptance']:5.1f}"
            + f"{e['throughput']:7.2f}{e['evap']:6.3f}{e['rejects']:4d}")
        return out

    def _csld_volume_table(self, tank, head):
        """IA53: "volume samples collected once every hour", printed eight
        to a line, with the hour of the newest under them.

        LAST HOUR=229957 in the manual's own sample is HOURS since the
        epoch, not the seconds its note names -- 229957 hours is March 1996
        and the report is dated MAR 26, 1996, where 229957 seconds is still
        1970. The packed field keeps the note's seconds.
        """
        rows = self.csld.hourly.get(tank) or []
        if not rows:
            return head + ["NO CSLD DATA"]
        out = list(head)
        # "LAST HOUR = 229664" sits between the tank and the grid in every
        # one of chapter 11's four sample tanks, and it is HOURS since the
        # epoch rather than the seconds the serial manual's note names:
        # 229664 hours is March 1996 and that figure heads a report dated
        # MAR 12, 1996, where 229664 seconds is still 1970.
        out.append(f"LAST HOUR = {int(rows[-1][0] // 3600.0)}")
        # "This list starts with the most recent volume and moves to the
        # oldest volume from left to right and top to bottom."
        volumes = [f"{v:8.1f}" for _at, v in reversed(rows)]
        for i in range(0, len(volumes), 8):
            out.append("".join(volumes[i:i + 8]))
        return out

    def _csld_moving_average(self, tank, head):
        """IA54: "averaged probe data collected every 30 seconds", with the
        moving average over it and the dispense state under it.
        """
        rows = self.csld.probe.get(tank) or []
        if not rows:
            return head + ["NO CSLD DATA"]
        out = list(head)
        out.append("       TIME  SMPLS     TCVOL    HEIGHT"
                   "   AVGTEMP   TOPTEMP    BDTEMP")
        for one in rows:
            out.append(
                time.strftime("%y%m%d%H%M%S", time.localtime(one["at"]))
                + f"{one['samples']:7d}{one['tcvol']:10.2f}"
                + f"{one['height']:10.3f}{one['temp']:10.2f}"
                + f"{one['toptemp']:10.2f}{one['bdtemp']:10.2f}")
        # Both footers go UNDER the rows, which is what chapter 11's figure
        # shows; the serial manual's extraction scrambles their order.
        out.append(f"MOVING AVERAGE: {self.csld.moving_volume(tank):9.2f}")
        # "* following ACTIVE = Pump sense available", chapter 11's own key,
        # and the key means what it says: the mark follows ACTIVE and only
        # ACTIVE. Every sample of this footer on the shelf is one of three
        # shapes -- `ACTIVE *`, `ACTIVE` and `IDLE` -- across 576013-818 Rev
        # AA and AB, 577013-918 Rev D and both revisions of the serial
        # manual, and `IDLE *` is in none of them. This appended the mark to
        # whichever word came out, so a site with a pump sense on a quiet
        # tank drew a line no manual draws. See FIDELITY K5.
        state = self.csld.moving_state(tank)
        sensed = state == "ACTIVE" and self.pump_tank_has_sense(tank)
        out.append(f"DISPENSE STATE: {state}" + (" *" if sensed else ""))
        return out

    def csld_table_records(self, token, tank):
        """The same four tables packed, and they are four record shapes.

        This used to pack one -- TT RR ss NN and four floats -- for all
        four, where the manual gives A51 fourteen floats, A52 a different
        record with ten, A53 the hourly volumes and A54 an extra leading
        state byte with eight. See FIDELITY K1.
        """
        if token == "A52":
            return self._csld_test_record(tank)
        if token == "A53":
            return self._csld_volume_record(tank)
        if token == "A54":
            return self._csld_average_record(tank)
        return self._csld_rate_record(tank)

    def _csld_rate_record(self, tank):
        """A51: TT RR then per record ss NN tttttttt and fourteen floats in
        the manual's own order.
        """
        rows = self.csld.table(tank)
        out = f"{tank:02d}{len(rows):02d}"
        for one in rows:
            values = [-one["rate"],                 # 1 leak rate
                      1.0 if one["state"] == _csld.ACCEPTABLE else 0.0,
                      0.0,                          # 3 obsolete
                      one["tmrt"], one["dispns"], one["volume"],
                      one["interval"], one["delivered"], one["temp"],
                      one["toptemp"], one["bdtemp"], one["area"],
                      one["throughput"], one["evap"]]
            out += (one["state"] + f"{len(values):02d}"
                    + f"{int(one['at']):08X}")
            out += "".join(packed.hexfloat(v) for v in values)
        return out

    def _csld_test_record(self, tank):
        """A52: TT YYMMDDHHmm SS CC cc NN then ten floats."""
        e = self.csld.evaluation(tank)
        values = [-e["compensated"], e["hours"], -e["uncompensated"],
                  e["volume"], e["feedback"], e["acceptance"],
                  e["throughput"], e["multiplier"], float(e["rejects"]),
                  e["evap"]]
        return (f"{tank:02d}"
                + time.strftime("%y%m%d%H%M", time.localtime(e["at"]))
                + e["status"].rjust(2, "0")
                + f"{e['records']:02d}{e['accepted']:02d}"
                + f"{len(values):02X}"
                + "".join(packed.hexfloat(v) for v in values))

    def _csld_volume_record(self, tank):
        """A53: TT NN hhhhhhhh then the hourly volumes, newest first --
        "1. Latest recorded hourly volume ... 3. Oldest".
        """
        rows = self.csld.hourly.get(tank) or []
        newest = rows[-1][0] if rows else time.mktime(self.now())
        values = [v for _at, v in reversed(rows)]
        return (f"{tank:02d}{len(values):02X}{int(newest):08X}"
                + "".join(packed.hexfloat(v) for v in values))

    def _csld_average_record(self, tank):
        """A54: TT SS RR then per record ss NN aaaaaaaa and eight floats.
        The leading state byte is the one the other three do not have.
        """
        rows = self.csld.probe.get(tank) or []
        state = "2" if self.csld.moving_state(tank) == "ACTIVE" else "0"
        out = f"{tank:02d}0{state}{len(rows):02X}"
        average = self.csld.moving_volume(tank)
        for one in rows:
            values = [float(int(one["at"])), one["tcvol"], one["height"],
                      one["temp"], 0.0, average, one["toptemp"],
                      one["bdtemp"]]
            out += (f"{one['samples']:02X}{len(values):02X}"
                    + f"{int(one['at']):08X}")
            out += "".join(packed.hexfloat(v) for v in values)
        return out

    # The modem type a SiteLink port is programmed to, and the one value of
    # it that has a signal to report. 576013-635 Rev AA p.473 enumerates all
    # four for both of 88D's bytes, `MM - Modem Type` and `DD - Modem Auto
    # Detected`; `S885` is the setter for the first.
    GSM_MODEM = "03"
    DEFAULT_MODEM = "00"

    def modem_type(self, port):
        """`S885`'s two digits for this comm port.

        576013-623 Rev AN p.6-4 is the keypad side of it: "Press ENTER to
        accept the modem option or press CHANGE and then ENTER to choose US
        ROBOTICS (UK), VR TLS ANALOG MOD, or VR TLS GSM MODEM" -- four
        options with NETCOMM SMART M7F as the one displayed, and Table 6-1
        on p.6-1 lists the same four with their port settings. See
        FIDELITY D10.
        """
        raw = (self.values.get(f"S885{port:02d}") or "").strip()
        return raw[-2:] if len(raw) >= 2 else self.DEFAULT_MODEM

    def comm_signal(self, port):
        """(RSSI, BER) for a SiteLink port, in 88D's own units.

        The manual encodes both, so neither is this simulator's: "rr - RSSI
        received signal strength indication (Decimal) ... 31: -51 dBm or
        greater, 02...30: -109 to -53 dBm, 01: -111 dBm, 00: -113 dBm or
        less, 99: not known or not detectable" and "ee - BER channel bit
        error (Decimal) ... 00...7: as RXQUAL values in the table GSM
        05.08, 99: not known or not detectable".

        **99 is the manual's own word for no reading**, which is what a port
        with anything but a GSM modem on it has: both of 88D's notes say the
        field is "only valid if Modem Type is" GSM. The wire answered a flat
        `9999` for every port, which was accidentally right for the three
        types that have no signal and wrong for the one that does -- so the
        one modem the field exists for was the one that could not report.
        """
        if self.modem_type(port) != self.GSM_MODEM:
            return 99, 99
        return (round(readings.wander(self, 12, 31, "rssi", port, swing=0.4)),
                round(readings.wander(self, 0, 4, "ber", port, swing=0.9)))

    def pump_tank(self, pump):
        """Which tank a pump sense input is assigned to, from S772."""
        raw = (self.values.get(f"S772{pump:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return int(body) if body.isdigit() else 0

    def pump_running(self, tank):
        """Is a pump sense input assigned to this tank saying so?

        The two mechanisms answer as one. `S772` maps a Pump Sense MODULE
        input to a tank and `pump_state` says whether that pump runs; the
        External Input Type of the same name is `inputs.pump_on`. Figure
        11-2's second clause -- "Idle is determined by ... 2) checking the
        pump sense module (if available)" -- does not care which of the two
        the site wired. See FIDELITY K5.
        """
        if self.inputs.pump_on(tank):
            return True
        return any(self.pump_tank(pump) == int(tank)
                   and self.pump_state(pump) == "ON"
                   for pump in range(1, 17))

    def pump_tank_has_sense(self, tank):
        """Is a pump sense input assigned to this tank?

        Chapter 11's key to IA54's footer: "* following ACTIVE = Pump sense
        available". S772 maps a pump sense input to a tank, and a console
        with one knows the forecourt is running from the pump rather than
        from the level.
        """
        # "or if External Input Type is configured as Pump Sense and
        # assigned to the tank" -- 576013-623 p.8-8, the same option
        return (any(self.pump_tank(pump) == int(tank)
                    for pump in range(1, 17))
                or self.inputs.pump_sensed(tank))

    def pmc_thresholds(self):
        """(off, on) IWC: the vapor processor's own two settings.

        One source for both faces: the wire's V44 and the panel's PMC
        SETUP read and write the same stored pair, so a threshold set on
        either is the threshold everywhere.
        """
        from . import packed as _packed
        held = (self.values.get("SV4400") or "").strip()
        if len(held) >= 16:
            try:
                return (_packed.unhexfloat(held[0:8]),
                        _packed.unhexfloat(held[8:16]))
            except (ValueError, TypeError):
                pass
        return (-2.0, 0.2)

    def set_pmc_threshold(self, which, value):
        """One end of the pair, range-checked the way V44 checks it:
        -8 < off < on < +3."""
        from . import packed as _packed
        off, on = self.pmc_thresholds()
        if which == "off":
            off = value
        else:
            on = value
        if not (-8.0 < off < on < 3.0):
            return False
        self.values["SV4400"] = (_packed.hexfloat(off)
                                 + _packed.hexfloat(on))
        self.save()
        return True

    def isd_hoses(self):
        """[(device, fuel position, hose label)] for every hose the fuel
        hose table holds -- a hose exists once its fuel position is set."""
        out = []
        for (key, device), value in sorted(self.settings.items()):
            if key == "evr_fuel_pos" and str(value).strip():
                label = self.setting("evr_hose_label", device, "UNASSIGNED")
                out.append((device, str(value).strip(), label))
        return out

    def isd_add_hose(self):
        """ADD NEW FUEL HOSE: the next free position, with a default label
        to edit. Returns the new hose's device index."""
        used = {d for d, _p, _l in self.isd_hoses()}
        n = 1
        while n in used:
            n += 1
        self.set_setting("evr_fuel_pos", f"{n:02d}", n)
        return n

    def isd_clear_hose(self, device):
        """CLEAR FUEL HOSE n: the position, its labels, and its mapping."""
        for key in ("evr_fuel_pos", "evr_hose_label", "evr_afm_id"):
            self.settings.pop((key, int(device)), None)
        self.isd_hose_map.pop(int(device), None)

    def isd_afm_full(self, device):
        """The AFM that mapping this hose would overfill, or None.

        577013-937 Rev J Figure 11's third auto-map error, `AFMx No Space
        for FP`: "You cannot map more than 2 fueling points (and related
        hoses) to one AFM (only one AFM is installed per dispenser)". A
        hose's AFM is the serial its ASSIGN AF METER ID screen holds, so the
        fueling points already on that AFM are the ones the MAPPED hoses
        with the same serial sit on; a hose on one of those points is not a
        third. -> the smart sensor number whose serial it is, which is the
        screen's x, or "" when no programmed sensor carries that serial.
        """
        def text(key, hose):
            return str(self.setting(key, hose, "")).strip()

        serial = text("evr_afm_id", device)
        if not serial:
            return None
        taken = {text("evr_fuel_pos", hose) for hose in self.isd_hose_map
                 if hose != int(device)
                 and text("evr_afm_id", hose) == serial}
        if text("evr_fuel_pos", device) in taken or len(taken) < 2:
            return None
        from . import wiresensors
        return next((n for n in range(1, 17)
                     if wiresensors.isd_serial(self, n) == serial), "")

    def isd_force(self, test, state):
        """The bench sets an ISD test's outcome: "warn", "fail", or None.

        Nothing in a simulator measures a vapour, so the outcome is set the
        way a sensor's state is, and the console does everything it would
        do if it had measured it: the alarm, the shutdown, the reports.
        Clearing the last failure also retires any standing override, so
        the next failure shuts the site down again.
        """
        now = time.mktime(self.now())
        if state in ("warn", "fail"):
            self.isd_forced[test] = state
            self.isd_forced_at[test] = now
            if state == "fail" and not self.isd_override:
                self.isd_events.insert(0, (now, "ISD SHUTDOWN", ""))
        else:
            self.isd_forced.pop(test, None)
            self.isd_forced_at.pop(test, None)
        if not any(v == "fail" for v in self.isd_forced.values()):
            self.isd_override = False

    def isd_do_override(self):
        """OVERRIDE SHUTDOWN & LOG, confirmed. Dispensing resumes; the
        alarm stands until its cause clears; the override is logged."""
        self.isd_override = True
        self.isd_events.insert(
            0, (time.mktime(self.now()), "ISD SHUTDOWN OVERRIDE", ""))

    def isd_shutdown_active(self):
        """Is an ISD failure holding the site down right now?

        Every FAIL in Table 3 marked as a shutdown alarm stops dispensing.
        The override lets fuel flow again while the alarm stands; it does
        not clear the alarm, and it is logged.
        """
        if not self.licensed("isd") or self.isd_override:
            return False
        return any(state == "fail" for state in self.isd_states().values())

    def pump_state(self, pump):
        """Is the pump running? A tank being dispensed from says so."""
        tank = self.pump_tank(pump)
        if not tank:
            return "OFF"
        selling = any(self.meters.get(m) == tank and rate
                      for m, rate in self.meter_flow.items())
        return "ON" if selling else "OFF"

    def line_setup_lines(self, kind, lines):
        """I780 and I7A0: the line leak setup, in the console's own words."""
        titles = {"plld": "PRESSURE LINE LEAK SETUP",
                  "wplld": "WPLLD LINE LEAK SETUP"}
        label_code = {"plld": "782", "wplld": "7A2"}[kind]
        letter = {"plld": "Q", "wplld": "W"}[kind]
        pipe_code = {"plld": "788", "wplld": "7A8"}[kind]
        shut_code = {"plld": "784", "wplld": "7A4"}[kind]
        tank_code = {"plld": "785", "wplld": "7A5"}[kind]
        pipes = ["STEEL", "FIBERGLASS", "FLEXIBLE", "OTHER"]
        rows = [titles[kind]]
        for line in lines:
            label = self.text(label_code, line) or f"LINE {line}"
            rows.append(f"{letter} {line}:{label}")
            pipe = (self.values.get(f"S{pipe_code}{line:02d}") or "").strip()
            index = int(pipe[-2:]) if pipe[-2:].isdigit() else 0
            rows.append(f"PIPE TYPE:   {pipes[index % len(pipes)]}")
            if kind == "wplld":
                # I7A0 is not I780 with a W. 576013-635 Rev AA p.419,
                # read off its own grid, puts `LINE LENGTH: 200 FEET` after
                # PIPE TYPE and prints the `0.20 GPH TEST:` option where
                # p.384's I780 prints `0.10 GPH TEST:`. This built both from
                # I780's list. A fiberglass line keeps a 2.0 inch and a 3.0
                # inch length and "the unused size's length must be set to
                # zero", 576013-623 Rev AN p.11-3, so its one length is the
                # two added. FIDELITY S26, UNKNOWNS A71.
                feet = self.limit("7A9", line) or 0.0
                if index == 1:
                    feet += self.limit("7AD", line) or 0.0
                rows.append(f"LINE LENGTH: {feet:.0f} FEET")
                on = self.licensed("plld020")
                rows.append("0.20 GPH TEST: " + ("ENABLED" if on else "DISABLED"))
            else:
                on = self.licensed("plld010")
                rows.append("0.10 GPH TEST: " + ("ENABLED" if on else "DISABLED"))
            shut = (self.values.get(f"S{shut_code}{line:02d}") or "").strip()
            rate = {"01": "0.1 GPH", "02": "0.2 GPH"}.get(shut[-2:], "3.0 GPH")
            rows.append(f"SHUTDOWN RATE:  {rate}")
            raw = (self.values.get(f"S{tank_code}{line:02d}") or "").strip()
            body = raw[2:] if len(raw) > 2 else raw
            tank = int(body) if body.isdigit() else 0
            if tank:
                rows.append(f"T {tank}:{self.text('602', tank) or ''}".rstrip())
            rows.append("DISPENSE MODE:")
            rows.append("  STANDARD")
        return rows

    def cage(self):
        """[(bay, slot, key, name, part)]: what is actually in the console.

        The physical cage, which is not quite what the SLOT diagnostic shows:
        a card this software cannot drive is in the slot and reads UNUSED.
        """
        out = []
        for bay in ("is", "power", "comm", "sw"):
            if bay == "comm":
                # The comm bay knows where its cards actually are: four
                # slots, and which one a card is in is a fault of its own.
                # See FIDELITY M7.
                for slot, key in sorted(self.comm_layout().items()):
                    label, part, _ohms = self.card(key)
                    out.append((bay, slot, key, label, part))
                continue
            slot = 0
            for key, _name, _part, mbay, _wires, _most in MODULES:
                if mbay != bay:
                    continue
                # ...off `card()` rather than off the table, so a bay listing
                # names the card that is IN it. The comm branch above already
                # did; these two read the MODULES row, so a console fitted
                # with either variant listed the card it had not got. M2, M4.
                label, part, _ohms = self.card(key)
                for _ in range(self.fitted(key)):
                    slot += 1
                    out.append((bay, slot, key, label, part))
        return out

    # The line leak diagnostics are the one place a technician watches a
    # number move, so they answer for the line the panel is on rather than
    # printing the manual's placeholder.
    LINE_DIAG = {"line_pressure", "line_counts", "line_switches",
                 "line_leg_gross", "line_leg_periodic", "line_leg_mid"}

    # The part numbers the manuals print for the two satellite processors, so
    # the screens that report them report something a technician recognises:
    # "PC SWARE# 330269-002-B", "EDIM:1 VR:330273-002-C".
    PC_SOFTWARE = "330269-002-B"
    DIM_SOFTWARE = "330273-002-C"
    WPLLD_SOFTWARE = "332738-001-B"

    def relay_stuck(self, number):
        """Is a monitored pump still running after it was told to stop?

        The one condition Table 29-21 gives for the Pump Relay Monitor alarm,
        and the only thing that takes its status screen off NORMAL.
        """
        return bool(self.control_phase_of("pumpmon", number, "00") == "01")

    def diag_value(self, token, device=1, kind=None):
        """The live half of a diagnostic screen.

        A token may answer with TWO lines separated by a newline, for the
        screens whose top line is a reading as well: the panel splits them.
        """
        if token.startswith("ps_"):
            # CALIBRATE SMARTSENSOR's live screens, 577013-937 Rev J
            # Figure 46. FIDELITY I11.
            return self.calibrations.reading(token, device)
        if token == "pump_sense":
            # Figure 6-15: `S 1: TANK # NONE` over `PUMP OFF`, annotated
            # "NONE = No tank assigned, or (TANK LABEL) = Tank assigned". The
            # screen was the figure's caption drawn verbatim, the same for
            # every input on every site, while B71 on the wire read the
            # input's tank and its pump all along. The figure draws only the
            # unassigned case and replaces only NONE, so the `#` stays as
            # drawn. See FIDELITY D25.
            tank = self.pump_tank(device)
            label = ((self.text("602", tank) or f"TANK {tank}") if tank
                     else "NONE")
            return (f"S {device}: TANK # {label}" + chr(10)
                    + f"PUMP {self.pump_state(device)}")
        if token in self.LINE_DIAG:
            return self.line_diag(token, device, kind or "plld")
        s = self.software_info()
        if token == "version":
            return f"VERSION {s['version']}"
        if token == "software":
            # the manual draws this screen as "SOFTWARE # XXXXXX-XXX-X" over
            # "CREATED - YY.MM.DD.HH.MM", and BOTH of those are the console's
            # own numbers, not labels
            return (f"SOFTWARE # {s['number']}" + chr(10)
                    + f"CREATED - {s['created']}")
        if token == "created":
            return f"CREATED - {s['created']}"
        if token == "smodule":
            # Figure 6-2 draws a colon here, where the PRINTOUT uses a hash
            return f"S-MODULE: {s['smodule']}"
        if token == "modules":
            n = sum(self.count(m) for m in self.modules)
            return f"{n} MODULES FITTED"
        if token == "features":
            return f"{len(self.features())} FEATURES ENABLED"
        if token == "alarms":
            n = len(self.compute_alarms())
            return f"{n} ACTIVE" if n else "ALL FUNCTIONS NORMAL"
        return self.diag_reading(token, device, kind)

    # ---- the readings behind the manual's X's --------------------------------
    def _uptime_hours(self):
        """How long this console has been up, on its own clock."""
        if self._commissioned is None:
            return 0.0
        return max(0.0, (time.mktime(self.now()) - self._commissioned) / 3600.0)

    def receiver_dial_spec(self, receiver):
        """52B's packed dial spec for one receiver, stored or default.

        A receiver ADDRESS exists whether or not anybody has filled it in --
        L14's reading, and the one the whole `52x` family now follows -- so
        the console is not blank here either. It reads method 1, ON DATE,
        with today's date and the time disabled:
        `tests/console_capture/raw/I52000.bin` prints
        `ON DATE     JAN 16, 2006    DISABLED` on all eight rows of a
        console nobody had autodialled from, and the date it prints is the
        date in that reply's own stamp.

        The PAPER is a different surface and is left alone: the tape's
        AUTO DIAL TIME SETUP block lists the one receiver that site had
        configured and no others at all, where the wire lists all eight
        addresses. See FIDELITY S18 and T4.
        """
        stored = (self.receiver_dial.get(receiver) or "").strip()
        if stored:
            return stored
        return "1" + time.strftime("%y%m%d", self.now()) + "EE00"

    def pc_counters(self):
        """The five numbers on the PERIPHERAL CONTROLLER diagnostic.

        One source, because there are two surfaces reading them. The panel's
        `pc_resets` and `mc_comms` tokens had the reset count and the two
        message counters and the serial report had literal zeros and no
        message counters at all, so the same console answered its own
        diagnostic two different ways depending on which end you asked.
        `tests/console_capture/raw/I90300.bin` is the report end of it on a
        real console -- five counters, all five moving. See FIDELITY S18.
        """
        talked = int(self._uptime_hours() * 3600 / 2.0) + 11
        return {"resets": readings.integer(0, 3, "pcreset"),
                "comm_errors": 0,
                "cksum_errors": 0,
                "to_pc": talked % 100000,
                "from_pc": max(talked - 1, 0) % 100000}

    def diag_reading(self, token, device=1, kind=None):
        """Every diagnostic screen the manual draws with X's in it.

        The manual cannot print your probe's serial number, so it prints
        XXXXXX; a simulator that prints XXXXXX has simulated the manual and
        not the console. See `readings.py` for what is derived and what is
        generated.
        """
        c, dev = self, int(device)
        if token.startswith("probe_ref_"):
            # "ORIG REF DISTANCE - Original reference distance reading (in
            # inches or mm) recorded at date/time or serial number change";
            # 576013-818 Fig 6-6 draws the screen as `MM/DD/YY  XX.XX`, a
            # date AND a distance, and the distance is the point of it --
            # comparing ORIG against CURR is how a probe swap or a shifted
            # riser gets caught. This drew the date alone while the same
            # pair went out over A07. See FIDELITY D7.
            pair = c.probe_reference_distance(dev)
            if pair is None:
                return ""
            born, inches = pair[0] if token.endswith("orig") else pair[1]
            when = f"{born[2:4]}/{born[4:6]}/{born[0:2]}"
            return f"{when}{inches:>16.2f}"
        if token.startswith("probe_"):
            return self._probe_reading(token, dev)
        if token.startswith("sensor_"):
            return self._sensor_diag(token, dev)
        if token.startswith("ss_"):
            return self._smart_diag(token, dev)
        if token.startswith("vac_"):
            return self._vac_diag(token, dev)
        if token.startswith("fm_"):
            return self._fuel_diag(token, dev)
        if token.startswith("accu_"):
            return self.accuchart.screen(dev, token[5:])

        if token == "pc_software":
            return (f"PC SWARE# {self.PC_SOFTWARE}" + chr(10)
                    + f"CREATED - {self.software_info()['created']}")
        if token == "pc_resets":
            return ("PC ROM CHECKSUM=PASSED" + chr(10)
                    + f"PC RESET COUNTS = {self.pc_counters()['resets']}")
        if token == "pc_errors":
            return ("PC ROM ERRORS = 0" + chr(10) + "MC CKSUM ERRS = 0")
        if token == "mc_comms":
            # two counters that only ever go up, at the rate the boards talk
            out = self.pc_counters()["to_pc"]
            # `MC \u2013>PC COMMS =  XXXXX` in Figure 6-11, and the dash is one
            # character: an en dash in the manual's typesetting is a hyphen
            # on a console, not two. This drew `-->` and was two characters
            # wide where the figure is one. See FIDELITY D16.
            return (f"MC ->PC COMMS =  {out:5d}" + chr(10)
                    + f"MC <-PC COMMS =  {self.pc_counters()['from_pc']:5d}")
        if token == "dim_software":
            created = self.software_info()["created"].replace(".", "-")
            return (f"{self.dim_letter()}1: SWARE#{self.DIM_SOFTWARE}"
                    + chr(10) + f"CREATED - {created}")
        if token == "dim_errors":
            return (f"{self.dim_letter()}1: DIM ROM CKSUM = PASS" + chr(10)
                    + "DIM COMM ERRORS = 0")
        if token == "wplld_software":
            return f"#: {self.WPLLD_SOFTWARE}"
        if token == "wplld_created":
            return self.software_info()["created"]
        if token == "wplld_errors":
            return "PC COMM ERRORS = 0"
        if token == "comm_board":
            name = self.comm_board_name(dev)
            return (f"COMM {dev} ({name})" + chr(10) + "REINIT COMM BD: NO")
        if token == "mt_block":
            # The key the panel has selected, which is the one ARE YOU SURE
            # will block. The manual's own literal `BLOCK: XXXXXX` is what
            # the screen reads before anything is selected, which is what a
            # console with nothing chosen has to draw. See FIDELITY D13.
            return f"BLOCK: {self.mt_pending or 'XXXXXX'}"
        if token == "mt_id":
            # `ENTER ID TO BLOCK / ID:` and then `ID: XXXXXX` once one is
            # typed, which is Figure 6-4's own two screens.
            return f"ID: {self.mt_pending}".rstrip()
        if token == "comm_rssi":
            rssi, ber = self.comm_signal(dev)
            return f"RSSI: {rssi:.0f} BER: {ber:.0f}"

        if token == "tank_leak_rate":
            rate = self.leaks.measured_rate("tank", dev)
            return f"LEAK RATE = -{rate:.2f} GAL/HR"
        if token == "csld_rate":
            rows = self.csld.table(dev)
            rate = rows[-1]["rate"] if rows else self.leaks.measured_rate("tank", dev)
            return f"TEST RATE: {rate:.2f} GAL/HR"
        if token == "csld_hours":
            # The tests' own durations added up, which is IA52's INTVL --
            # "Total test duration, sum of all acceptable tests, in hours".
            # It used to be the row COUNT times a fixed hour, because a test
            # had no length of its own. See FIDELITY K5.
            hours = self.csld.evaluation(dev)["hours"]
            return f"TOTAL TIME: {hours:.1f} HRS"
        if token == "csld_volume":
            rows = self.csld.table(dev)
            volume = (sum(r["volume"] for r in rows) / len(rows) if rows
                      else self.tank_level.get(dev, {}).get("volume", 0.0))
            return f"AVE VOLUME: {volume:.0f} GALS"

        if token == "line_offset":
            # "enter the Offset value exactly as displayed in the Offset test
            # result message (including + or - sign)": one figure, held until
            # somebody resets it with 089, 090 or the panel's own screen.
            psi = self.lines.line(kind or "plld", dev).measure_offset()
            return f"DONE - OFFSET: {psi:+.1f} PSI"
        if token == "line_passive":
            # 577013-344 Rev H's WPLLD diagram annotates this screen "IDLE
            # or ACTIVE (Active means 0.1 gph test is scheduled)". S7AC is
            # the line's own 0.1 gph scheduling -- DISABLED, AUTO or MANUAL,
            # and only AUTO schedules one; MANUAL "run only when manually
            # started" is exactly what IDLE means here.
            raw = (self.values.get(f"S7AC{dev:02d}") or "").strip()
            body = raw[2:] if len(raw) > 2 else raw
            state = "ACTIVE" if body[-1:] == "2" else "IDLE"
            return f"0.10 GPH{state:>16}"
        if token in ("line_messages", "line_crc"):
            line = self.lines.line(kind or "wplld", dev)
            head = f"W {dev}: LAST READ={line.pressure:.3f} PSI"
            if token == "line_messages":
                total = int(self._uptime_hours() * 60) + 1
                return head + chr(10) + f"TOTAL MESSAGE: {total}"
            return head + chr(10) + "CRC: 0       PARITY: 0"

        if token.startswith("pumpmon_"):
            # imported here rather than at the top: `wiresensors` reads
            # STATUS_TYPES out of this module, so the import only goes one
            # way at load time.
            from . import wiresensors
            return wiresensors.monitor_diag(self, token, dev)
        if token == "meter_map":
            return self._meter_line(dev, "map")
        if token == "meter_events":
            row = self._event_line()
            return "BIR METER EVENTS" + chr(10) + row if row else ""
        if token == "meter_event_time":
            # "Time of Last Meter Event", which is the screen PRINT hangs
            # off: "Prints Last 4 Meter Events".
            event = self.bir.last_event()
            if event is None:
                return ""
            # "MMM DD, YYYY HH:MM:SS xM", which is the console's own clock
            # line with the seconds on it -- one helper, because otherwise
            # the space-padded hour drifts back.
            return clock_words(event["at"], seconds=True)
        if token == "meter_map_status":
            # "COMPLETE or INCOMPLETE". A map is complete when every meter
            # the console has heard of is mapped to a tank, which is exactly
            # what BIR needs before it can reconcile anything; the figure
            # names the two states and does not define them.
            known = set(self.meters) | set(self.meter_flow)
            done = bool(known) and all(self.meters.get(m) for m in known)
            return "STATUS: " + ("COMPLETE" if done else "INCOMPLETE")

        if token == "tank_leak_when":
            result = (self.leaks.result("tank", dev, "periodic")
                      or self.leaks.result("tank", dev, "annual")
                      or self.leaks.result("tank", dev, "gross"))
            if result is None:
                # The full phrase. 576013-818 Figure 6-9 draws this screen's
                # second line as a stamp and no empty state at all, so the
                # package's own vocabulary is the evidence: 576013-610 p85
                # lists `NO TEST DATA AVAILABLE`, and every other site here
                # spells it whole. 22 characters into 24. FIDELITY D31.
                return "NO TEST DATA AVAILABLE"
            return (clock_date(result.started, sep=",")
                    + time.strftime(" %I:%M:%S %p",
                                    time.localtime(result.started)).upper())
        if token == "csld_when":
            rows = self.csld.table(dev)
            when = rows[-1]["at"] if rows else time.mktime(self.now())
            return clock_words(when)
        if token == "csld_tests":
            # Figure 6-11 draws this one screen as a PAIR, and says what the
            # two numbers are: "YY = Total number of tests stored in CSLD
            # Rate Table. XX = Total number of tests used to compute leak
            # rate from CSLD Rate Table." Both are already counted, by the
            # summary IA52 is built from -- a test is used if its
            # acceptability code is 00 -- and this drew the stored count
            # alone. FIDELITY D6.
            got = self.csld.evaluation(dev)
            return _diag_row("TOTAL TESTS",
                             f"{got['accepted']}/{got['records']}")
        if token == "csld_rejects":
            # "Out of the last 20 tests, this number indicates how many
            # exceeded a positve leak rate of at least 0.4 gph" -- and this
            # counted every row in the whole table whose acceptability code
            # was not 00, which is every REJECTED test for any reason, with
            # no window and no rate test at all. The right count is IA52's
            # RJT, which has been computed correctly all along.
            return _diag_row("POS REJECTS:",
                             self.csld.evaluation(dev)["rejects"])
        if token == "csld_thruput":
            # what CSLD reckons has gone through the tank
            through = sum(self.bir.totals.get(m, 0.0)
                          for m, where in (self.meters or {}).items()
                          if int(where) == dev)
            return f"THRUPUT EST: {through:.0f} GALS"
        if token == "csld_dispense":
            busy = self.csld.busy(dev) or self.deliveries.in_progress(dev)
            return "DISPENSE STATE: " + ("ACTIVE" if busy else "IDLE")
        if token == "meter_end":
            # The gallons that went through the meter on THAT transaction,
            # which is what an End Event carries; a Start Event has none and
            # the figure gives it its own screen. See FIDELITY D14.
            event = self.bir.last_event()
            if event is None:
                return ""
            if event["kind"] == "start":
                return "START EVENT"
            return f"END EVENT: {event['gallons']:.0f} GALS"
        if token in ("power_removed", "power_restored"):
            when = (self.power_off if token == "power_removed"
                    else self._commissioned) or time.mktime(self.now())
            # `clock_words`, like every other clock in this package, because
            # `%I` zero-pads and this console does not: "JAN 16, 1996
            # 7:46:23 AM" is 576013-635 Rev AA's own sample for A91, and
            # `clock.py` states the rule -- "every sample in the Operator's
            # and Serial manuals reads ' 3:06 PM', never '03:06 PM'". The
            # A91 REPORT already went through `clock_words`, so the panel
            # and the printout of one outage disagreed. FIDELITY U12.
            return clock_words(when)
        if token.startswith("power_off"):
            # what the tank read the moment the lights went out
            kept = self.power_off_state.get(dev) or {}
            if token == "power_off_volume":
                return f"VOLUME = {kept.get('volume', 0.0):.0f} GALS"
            if token == "power_off_water":
                return f"WATER VOL = {kept.get('water_vol', 0.0):.0f} GALS"
            return f"TEMP = {kept.get('temp', 0.0):.1f} DEG F"
        if token == "power_water":
            return f"WATER VOL = {self.water_volume(device):.0f} GALS"
        if token == "power_volume":
            volume = self.tank_level.get(dev, {}).get("volume", 0.0)
            return f"VOLUME = {volume:.0f} GALS"
        if token == "power_temp":
            return f"TEMP = {self.product_temperature(dev):.1f} DEG F"
        if token == "service_session":
            # Figure 6-5's first screen, and it is a READING rather than a
            # store: the session is either open or it is not, and asking the
            # console is the only way that cannot disagree with 11B.
            return "ENABLED" if self.service_session() else "DISABLED"
        if token == "service_duration":
            return f"DURATION : {self.service_session_hours()}"
        if token == "power_change":
            # 576013-818 Fig 6-26 draws GROSS VOLUME CHANGE over
            # "               XXXX  GALS", and the function exists to show
            # what a tank did across a power cut: the screen that states
            # the change was the one drawing nothing. It is what the tank
            # holds now against what it held when the lights went out.
            kept = self.power_off_state.get(dev) or {}
            now = self.tank_level.get(dev, {}).get("volume", 0.0)
            change = now - kept.get("volume", now)
            return f"{change:>10.0f}  GALS"

        if token == "pmc_vapor":
            return f"INCHES H2O:      {self.vapor_pressure(dev):.3f}"
        if token == "pmc_hc":
            return f"HC SENSOR      {self.hydrocarbon(dev):.3f}%"
        if token == "pmc_mode":
            # VC0's own store. `VAPOR PROCESSOR MODE / AUTOMATIC` and
            # `VP STATE:  OFF` were captions, whatever the port had set.
            # FIDELITY I11.
            from . import isd as isdmod
            return isdmod.VP_CONTROL[self.vp_control()]
        if token == "pmc_state":
            from . import isd as isdmod
            return "VP STATE:  " + isdmod.VP_RUNNING[self.vp_running()]
        if token == "pmc_load":
            # the polisher's canister load, from the vapor valve dump the
            # wire already exposes (IB6100); with no reading it is 0
            return f"LOAD:       {self.pmc_figures(dev)['load']:.1f}%"
        if token == "pmc_effluent":
            return f"{self.pmc_figures(dev)['effluent']:.2f} LB/KGAL"
        if token == "pmc_temp":
            return f"{self.pmc_figures(dev)['temp']:.2f} DEG F"
        if token == "pmc_valve_req":
            return f"REQUESTED: {self._pmc_valve('req')}"
        if token == "pmc_valve_cur":
            return f"CURRENT: {self._pmc_valve('cur')}"
        return ""

    def _pmc_reading(self, which, default):
        """A polisher diagnostic reading. Nothing measures a real canister,
        so these are the manual's own example values unless the bench has
        set one, the same honesty the ISD tests use."""
        return getattr(self, "pmc_readings", {}).get(which, default)

    def _pmc_valve(self, which):
        return getattr(self, "pmc_valve", {}).get(which, "CLOSED")

    def control_action(self, what):
        """Section 7.1's console-wide commands, 001, 002, 003, 010 and 031.

        A System Reset is a RESTART and not a wipe: "the console came back a
        few minutes after it went" is what a power cut looks like, and that is
        what this does. It does not clear programming -- clearing setup data
        is its own function, and a tool that resets a console expecting to
        keep the site's programming would be very surprised otherwise.
        """
        now = time.mktime(self.now())
        if what == "system_reset":
            self.power_off = now
            self._last_console = None
            self.silenced = False
            return "console restarted"
        if what == "clear_power_flag":
            had = self.power_off is not None
            self.power_off = None
            return "power reset flag cleared" if had else "no flag to clear"
        if what == "remote_alarm_reset":
            # The same thing ALARM/TEST does from the panel: it silences, and
            # it clears what has already gone away rather than a live alarm.
            self.silenced = True
            return "alarms silenced"
        if what == "cancel_autodial":
            # Nothing here dials anybody, so there is no session to cancel.
            # The console acknowledges either way, which is what it does when
            # asked to cancel a session it does not have.
            return "no autodial session"
        if what == "confirm_clear":
            return "confirm clear complete"
        return "not simulated"

    # The states 092 to 09B report back. Nothing here evacuates a sump or
    # profiles a line, so what these hold is which phase a technician has put
    # the device INTO -- which is real, because he is the one putting it
    # there -- and not a measurement nobody made.
    CONTROL_STATES = {
        "profile_start": ("profile", "01"), "profile_stop": ("profile", "08"),
        "profile_bulk": ("profile", "00"),
        "vac_start": ("vactest", "01"), "vac_stop": ("vactest", "00"),
        "evac_hold": ("evac", "06"), "evac_release": ("evac", "00"),
        "sump_start": ("sump", "02"), "sump_height": ("sump", "03"),
        "sump_stop": ("sump", "01"),
    }

    def control_device(self, what, number):
        """Put one device into the phase the command names, and say so.

        The Mag sump is the exception, and the reason is that it is a test:
        099 starts one, which can abort the moment it starts; 09A cannot
        start a Measuring Height Phase on a test that is not in its Test
        Phase; and what 09B leaves behind depends on how long it ran. So the
        sump commands go to `sumps` and answer with what it says.
        """
        family, state = self.CONTROL_STATES[what]
        if family == "sump":
            return {"sump_start": self.sumps.start,
                    "sump_height": self.sumps.measure,
                    "sump_stop": self.sumps.stop}[what](number)
        self.control_phase[(family, number)] = state
        # ...and the two vacuum families have a model behind them, which the
        # panel's own EVAC HOLD and MANUAL TEST already drove. 097 wrote a
        # phase here and nothing else, so an evacuation hold started over the
        # port left the valve shut on B38 and on the glass, and a manual test
        # started over the port recorded no result for either to read. Two
        # stores for one fact, which is FIDELITY F9's shape and what L18
        # found on the vacuum sensor. The phase above stays as the status
        # these commands echo back; what it MEANS is the model.
        if family == "evac":
            (self.start_evac_hold if what == "evac_hold"
             else self.stop_evac_hold)(number)
        elif family == "vactest":
            (self.start_vac_test if what == "vac_start"
             else self.stop_vac_test)(number)
        return state

    def control_phase_of(self, family, number, default="00"):
        if family == "sump":
            return self.sumps.status(number)
        return self.control_phase.get((family, number), default)

    def vp_control(self):
        """VC0's mode: "1" automatic, which it is out of the box, "0" manual."""
        from . import isd as isdmod
        return self.values.get("SVC000") or isdmod.VP_AUTOMATIC

    def vp_running(self):
        """VC1's state: "1" on, "0" off."""
        return self.values.get("SVC100") or "0"

    def pmc_figures(self, dev=1):
        """The PMC diagnostic's readings, which the screens and the PMC
        DIAGNOSTICS printout both show. Nothing measures a real canister, so
        the polisher's are the manual's own example values unless the bench
        has set one."""
        return {"vapor": self.vapor_pressure(dev),
                "hc": self.hydrocarbon(dev),
                "load": self._pmc_reading("load", 24.9),
                "effluent": self._pmc_reading("effluent", 0.05),
                "temp": self._pmc_reading("temp", 75.05),
                "valve_req": self._pmc_valve("req"),
                "valve_cur": self._pmc_valve("cur")}

    def vapor_processor_on(self, running):
        """Note the processor starting or stopping, for V80's buffer.

        A cycle is a run: a start, an elapsed time, the pressure at each end,
        and whether it faulted. Nothing is recorded until it STOPS, because
        until then there is no elapsed time to record.
        """
        now = time.mktime(self.now())
        if running:
            if self.vp_started is None:
                self.vp_started = (now, self.vapor_pressure())
            return
        if self.vp_started is None:
            return
        began, on_psi = self.vp_started
        self.vp_started = None
        minutes = max(0.0, (now - began) / 60.0)
        # V45 is "Set Vapor Processor Maximum Runtime ... [010-180]", so a run
        # that outlasts it is the runtime fault the report has a column for.
        limit = float(self.values.get("SV4500") or 60)
        self.vp_cycles.append({"at": began, "minutes": minutes,
                               "on_psi": on_psi,
                               "off_psi": self.vapor_pressure(),
                               "fault": minutes > limit,
                               "on": True, "event": "OPEN PURGE"})
        del self.vp_cycles[:-20]

    def vapor_pressure(self, dev=1):
        """"VAPOR PRESSURE / INCHES H2O: -X.XXX", a small negative on a
        healthy ullage. The diagnostic screen and V80 read the same one."""
        return readings.wander(self, -1.2, -0.05, "pmc", dev)

    def hydrocarbon(self, dev=1):
        """The HC sensor's percent, which the screen and V81 both report."""
        return readings.wander(self, 0.4, 3.5, "hc", dev)

    # V81's example samples fifteen seconds apart, which is the rate the
    # buffer fills at.
    HC_SECONDS = 15.0
    HC_SAMPLES = 20                # "nnnn - number of HC samples [00-20]"

    def hydrocarbon_history(self, most=None):
        """[(when, percent)] for V81, newest first.

        Derived rather than accumulated, and stable because of it: a sample
        belongs to its fifteen second slot, so the reading for 10:51:15 is the
        same reading every time anybody asks, which is readings.py's own rule
        about a value that changes when you glance away not being a reading.
        """
        most = most or self.HC_SAMPLES
        now = time.mktime(self.now())
        slot = int(now // self.HC_SECONDS)
        out = []
        for back in range(most):
            at = (slot - back) * self.HC_SECONDS
            if self.hc_cleared is not None and at <= self.hc_cleared:
                # "Set command clears buffer", and a sample stamped AT the
                # clear instant is cleared too. This was `<` and the console
                # kept that sample, so clearing the buffer left one reading in
                # it whenever the clear landed exactly on a slot boundary.
                # `now()` is whole seconds and a slot is fifteen of them, so
                # that is one clear in fifteen -- rare enough to look like a
                # flaky test and common enough to fail the suite twice a day.
                break
            out.append((at, readings.fixed(0.4, 3.5, "hcsample",
                                           slot - back)))
        return out

    # V83: a sensor is calibrated when it goes in and whenever somebody
    # re-does it. Nothing here re-calibrates one, so each has the one record
    # it was commissioned with -- and it is the same record every time.
    def calibration_history(self, module, number, most=1):
        """[(when, slope, offset, passed)] for one sensor, newest first.

        The calibrations CALIBRATE SMARTSENSOR has actually run, and the
        factory one under them. This generated a slope of 0.9 to 5.2 and
        an offset of 0.0 to 5.1 per call, always passed, a month apart.
        FIDELITY I11.
        """
        del module
        return self.calibrations.history(number, most)

    def record_accuchart_update(self, tank, when):
        """When a calibration was applied, so the printer can say so.

        "Each time an AccuChart calibration is updated, a user notification
        message is sent to the local printer."
        """
        self.accuchart_log.append((int(tank), when))
        del self.accuchart_log[:-40]

    # ---- the communication bay's four slots --------------------------------
    def comm_dual(self, key):
        """Is that card a dual-port module: one slot, two comm positions?"""
        return key in COMM_DUAL

    def comm_layout(self):
        """{slot: key} -- which card is in which of the bay's four slots.

        Placement is remembered in `comm_slots`, because a card has to be
        able to be PUT somewhere, including somewhere it does not work: that
        is the whole of 576013-818 Table 7-2's two comm entries. Anything
        fitted and not placed is dropped into the first slot that suits it,
        dual-port modules from slot 4 downwards and single-port ones from
        slot 1 up, so a bay nobody has arranged by hand is still a legal one.
        """
        spare = []
        for key, _n, _p, bay, _w, _m in MODULES:
            if bay == "comm":
                spare += [key] * self.fitted(key)
        out = {}
        for slot, key in sorted((self.comm_slots or {}).items()):
            if slot in COMM_PORTS and slot not in out and key in spare:
                out[slot] = key
                spare.remove(key)
        for key in [k for k in spare if self.comm_dual(k)]:
            for slot in (4, 3, 2, 1):
                if slot not in out:
                    out[slot] = key
                    spare.remove(key)
                    break
        for key in list(spare):
            for slot in (1, 2, 3, 4):
                if slot not in out:
                    out[slot] = key
                    spare.remove(key)
                    break
        return out

    def comm_positions(self):
        """{position: (slot, key, half)} across the bay's six positions.

        `half` is "rj45" or "db9" for a dual-port module and None for a
        single-port card. A single-port card in slot 4 -- the fault Table 7-2
        is written about -- sits on that slot's first position and does not
        communicate; see `comm_slot_works`.
        """
        out = {}
        for slot, key in self.comm_layout().items():
            first, second = COMM_PORTS[slot]
            if not self.comm_dual(key):
                out[first] = (slot, key, None)
                continue
            out[first] = (slot, key, "rj45")
            if second:
                out[second] = (slot, key, "db9")
        return out

    def rs232_port(self):
        """The bay position the serial port a tool talks to sits on.

        This simulator's socket IS the console's RS-232 port, and 888 reports
        per position, so the traffic has to land on one of them.
        """
        for position, (_slot, key, _half) in sorted(
                self.comm_positions().items()):
            if key in ("rs232", "aux"):
                return position
        return 1

    def fault_comm(self, port, error, state=0, when=None):
        """Put a UART or modem error on a comm port, for 888's report.

        `error` is 888's own number -- 1 UART SETTINGS ERROR through 12 FAX
        BUILD MESSAGE ERROR -- and `state` the function the port was in.
        The stamp is TIME OF LAST COMM ERROR. Nothing in this simulator can
        suffer one of these by itself (FIDELITY S13); the bench can now say
        that one happened. BENCH.md P9.
        """
        port = int(port)
        now = when if when is not None else time.mktime(self.now())
        rows = self.comm_errors.setdefault(port, [])
        rows.append({"state": int(state), "error": int(error)})
        del rows[:-10]
        self.comm_error_at[port] = now

    def clear_comm_errors(self, port):
        self.comm_errors.pop(int(port), None)

    def log_service(self, code, ident="", when=None):
        """A service contractor's entry, for 116 and 11A.

        576013-610 ch.33 has the technician log in with the ID key and
        enter a service code from 577013-874's list -- 0101 REPROGRAMMED
        TLS through 99xx USER DEFINED -- and the Service Report History is
        the record of those entries. Nothing wrote one. BENCH.md P10.
        """
        stamp = (when if isinstance(when, str)
                 else time.strftime("%y%m%d%H%M", self.now()))
        entry = {"at": stamp, "id": str(ident or "")[:10],
                 "code": str(code or "")[:5]}
        self.service_entries.insert(0, entry)
        del self.service_entries[100:]
        return entry

    def note_comm(self, port=None, when=None, connect=None):
        """Remember that a port has just carried data, for 888's own report.

        `connect` is 888's own connect type -- `01=AUTO DIAL TELETYPE`
        through `06=RS232 REQUEST` -- and it is the SAME event as the stamp:
        a port carried data because something established a connection on it,
        and 888 prints both facts about it.

        The manual's own sample is what says the connect type persists rather
        than describing only this instant. Its port 1 reads `CONNECTION :
        NONE` and has no `TIME OF LAST COMM DATA` line at all, while its port
        2 reads `MODEM DIAL IN` over a last-comm stamp of 9:12 AM and a
        last-error stamp of 8:00 AM. A port that has never carried data has
        no connection and no stamp; a port that has carried data has both,
        whether or not the call is still up. See FIDELITY S13.
        """
        import time as _time
        port = port or self.rs232_port()
        self.comm_data_at[port] = (
            when if when is not None else _time.mktime(self.now()))
        if connect is not None:
            self.comm_connect[port] = connect

    def comm_half(self, port, paper=False):
        """(screen name, slot line, ohms, reads as) for that position.

        The first field is 888's vocabulary and the second is the slot
        line's, which are already two different names for one card -- `COMM
        BOARD  : 1 (RS-232)` against `COMM 1 RS-232`. `paper` asks for the
        third: what function 102's printout calls it. A dual-port module's
        halves have no attested printed name, so they keep the one they had.
        """
        seat = self.comm_positions().get(port)
        if not seat:
            return None
        _slot, key, half = seat
        if half:
            rj45, db9 = COMM_DUAL[key]
            return rj45 if half == "rj45" else db9
        return (COMM_NAME.get(key), self.slot_name(key, paper),
                MODULE_OHMS.get(key, 100000), key)

    def comm_count(self, module):
        """How many positions the bay answers on that read as that card.

        A dual-port module is two of them and they are not the same card: a
        multiport is an RS-485 port AND an RS-232 port, which is what makes
        577013-819's "connect your laptop to the TLS console's RS-232 or
        Multiport card" one instruction rather than two.

        A card whose software this console predates is in the bay and is read
        there -- the ID resistor is measured by hardware -- and is counted
        here as none of it, which is what `count` means everywhere else. The
        tape is a console in exactly that state: a Maintenance Tracker port
        on comm 6, and an NVMEM201 board that cannot drive one, which is why
        it prints the port and none of the Maintenance Tracker screens.
        """
        seats = self.comm_positions()
        return sum(1 for port in seats
                   if (self.comm_half(port) or (None,) * 4)[3] == module
                   and self.knows_module(seats[port][1]))

    def comm_slot_works(self, slot):
        """Can the card in that slot communicate?

        576013-818 Rev AB Table 7-2 makes the two ways of getting it wrong a
        troubleshooting entry: "System will not communicate via internal
        SiteFax Module -- Modem Module in slot 4 of Comm Bay card cage --
        Move module to slots 1, 2, or 3", and the same symptom and the same
        corrective procedure for the RS-232 module. A dual-port module fails
        the other way about: it wants the harness 577013-528 ships with it,
        "the 4-pin connector ... to J4 on the Module ... the 8-pin connector
        of the harness to J6 on the ECPU/ECPU2 board", and a second one in
        slot 3 wants the double dual-port harness P/N 332609-001.
        """
        key = self.comm_layout().get(slot)
        if key is None:
            return False
        if not self.comm_dual(key):
            return slot != 4
        if not self.dual_harness:
            return False
        return slot == 4 or (slot == 3 and self.double_harness)

    def comm_port_works(self, port):
        """Does that position carry traffic, or is its card in the wrong slot?"""
        seat = self.comm_positions().get(port)
        return bool(seat) and self.comm_slot_works(seat[0])

    def comm_ports_for(self, module, working=True):
        """The positions that read as that card, in order."""
        return [port for port in sorted(self.comm_positions())
                if (self.comm_half(port) or (None,) * 4)[3] == module
                and (self.comm_port_works(port) or not working)]

    def serial_port_works(self):
        """Is there a port a tool could actually talk to the console on?

        The RS-232 module, the modem and the Maintenance Tracker port are all
        ports; so is the DB-9 half of any dual-port module, which is what
        577013-819 plugs a laptop into. A card in slot 4 that does not belong
        there is fitted, is read on SYSTEM CONFIGURATION, and answers nothing.
        """
        return any(self.comm_ports_for(k)
                   for k in ("rs232", "modem", "mt", "aux"))

    def place_comm(self, key, slot):
        """Put a fitted comm card in that slot, swapping with what is there.

        The bench's way of reproducing Table 7-2: move the RS-232 module into
        slot 4 and the console stops answering, exactly as the chart says,
        and moving it back to 1, 2 or 3 is the chart's own corrective
        procedure.
        """
        if slot not in COMM_PORTS:
            return False
        layout = self.comm_layout()
        if key is not None and key not in layout.values():
            return False
        here = next((s for s, k in layout.items() if k == key), None)
        if here == slot:
            return True
        sitting = layout.get(slot)
        layout[slot] = key
        if here is not None:
            if sitting is None:
                layout.pop(here, None)
            else:
                layout[here] = sitting
        if key is None:
            layout.pop(slot, None)
        self.comm_slots = {s: k for s, k in layout.items() if k}
        self.save()
        return True

    def comm_board_name(self, port):
        """Which card is on comm position `port`, as the screens name it.

        Six characters, every one of them, which is what makes
        `COMM BOARD  : 1 (RS-232)` come to 24. The modem board is FXMOD on
        the console and SiteFax on the box: 576013-635 Rev AA prints
        `COMM BOARD  : 3 (FXMOD)` seventeen times and 576013-623 Rev AN draws
        the same, and MTCOMM is the tape's own spelling. `SITEFAX` and
        `MT COMM` are seven and ran the head off the paper. See FIDELITY T8.
        """
        half = self.comm_half(port)
        return half[0] if half and half[0] else "UNUSED"

    # How fast buried product is allowed to move, and the console says it
    # itself. 576013-610 Rev AC Table 29-4 invalidates a leak test whose
    # "average temperature of all submerged thermistors changed by more than
    # 0.1 F (0.06 C) per hour" -- TEMP CHANGE TOO LARGE -- and 576013-818
    # Figure 11-4 logs a real probe at about 0.027 F/hr.
    #
    # A `wander` peaks at `span * (2*pi/period) * (0.7 + 0.3*4.7)`, which over
    # this fourteen degree band at a swing of 0.12 is 22 F/hr on an hour-long
    # period: five to eight times the console's own limit and sixty times the
    # per-thermistor one, so every in-tank leak test on every shipped preset
    # would have aborted the moment those criteria were implemented. Solving
    # that expression for HALF the stated limit -- 0.05 F/hr, so a bench is
    # not sitting on the edge of its own invalidation criterion -- gives a
    # period of 1.6 million seconds, about nineteen days. Which is the other
    # argument for it: buried product moves with the season, not with the
    # afternoon. See FIDELITY Y4.
    PRODUCT_TEMP_PERIOD = 1_600_000.0

    # Six thermistors up the probe, evenly spaced over the tank's diameter
    # and read bottom first, which is the order A15 prints them in. The
    # spacing is the one thing here the manuals do not give -- Table 29-4
    # says WHICH of them count and nothing on this shelf says where they sit
    # -- so they stand at the centres of six equal bands, which is the only
    # spacing that needs no further assumption. See FIDELITY Y5.
    THERMISTORS = 6

    def thermistor_heights(self, tank):
        """How far up the tank each of the six sits, bottom first."""
        diameter = self.limit("607", tank) or 96.0
        return [diameter * (n + 0.5) / self.THERMISTORS
                for n in range(self.THERMISTORS)]

    def base_temperature(self, tank, at=None):
        """The tank's own temperature, before the probe reads any of it.

        This is the fuel, and it does not move because the LEVEL moved: a
        delivery changes which thermistors are submerged and therefore what
        the console reports, and it does not warm the product up. Thermal
        expansion is driven from here for that reason, and the reported
        temperature is this plus whatever the submerged run offsets it by.
        """
        return readings.wander(self, 48.0, 62.0, "temp", tank, swing=0.12,
                               period=self.PRODUCT_TEMP_PERIOD, at=at)

    def thermistor_ladder(self, tank, at=None):
        """The six RTDs before anything asks which of them are wet.

        Warmest at the bottom, where the product has been longest out of the
        weather, and the spread is A15's own five degrees -- "72.6 at T6 down
        to 67.6 at T1". The ladder averages to the wander exactly, because
        the offsets sum to zero: a full tank reports what it always did.
        """
        base = self.base_temperature(tank, at=at)
        spread = readings.fixed(3.0, 6.0, "tspread", tank)
        return [base + spread * (0.5 - n / (self.THERMISTORS - 1.0))
                for n in range(self.THERMISTORS)]

    def submerged_thermistors(self, tank):
        """Which of the six are under the fuel, as a list of indices.

        Table 29-4's qualifier, and the reason it matters: a half-empty tank
        is measuring only its own bottom half, which is the warmer half. The
        level is the fuel's own height rather than the stick's, because a
        stick offset moves the reading and not the fuel.
        """
        volume = self.tank_level.get(int(tank), {}).get("volume", 0.0)
        # `height_at` walks a fifty point chart, and `product_temperature`
        # is asked hundreds of times a report -- so the answer is kept
        # against the volume it was worked out for, which is the only thing
        # that moves it.
        return list(self._submerged_at(int(tank), volume)[0])

    def _submerged_at(self, tank, volume):
        """(which thermistors are wet, how far their average sits above the
        wander) for one tank at one volume.

        `height_at` walks a fifty point chart and `product_temperature` is
        asked well over a million times in a CSLD run, so both answers are
        kept against the volume they were worked out for -- which is the only
        thing that moves either of them.
        """
        key = (tank, round(volume, 4))
        held = self._submerged.get(key)
        if held is None:
            level = self.height_at(tank, volume)
            under = tuple(n for n, at
                          in enumerate(self.thermistor_heights(tank))
                          if at <= level)
            spread = readings.fixed(3.0, 6.0, "tspread", tank)
            steps = under or (0,)
            offset = spread * sum(0.5 - n / (self.THERMISTORS - 1.0)
                                  for n in steps) / len(steps)
            held = (under, offset)
            if len(self._submerged) > 4096:
                self._submerged.clear()
            self._submerged[key] = held
        return held

    def product_temperature(self, tank, at=None):
        """What the probe's RTDs make of the product.

        Table 29-4: "average temperature of all submerged thermistors".
        The channels are the source and the set of them changes as the level
        falls -- which is why this is the average of a ladder rather than a
        number the ladder is drawn from. A full tank reads exactly what it
        read before, because the ladder averages to the wander; a tank down
        to its bottom band reads that band alone, which is the warmest.

        Not a constant: fuel underground sits near the ground temperature and
        moves with a delivery and with the season, so this wanders the way a
        real reading does rather than reading 55.0 for ever -- and at the
        speed a real one does, which is the season and not the hour.

        `at` asks what it read at a past console time, which CSLD's
        30-second probe table needs to fill itself in after a fast clock has
        crossed an hour in one tick. The LEVEL it uses is today's: this
        console keeps no history of where the fuel stood.
        """
        held = self.held_temperature(int(tank), at=at)
        if held is not None:
            # The bench holding the product at a temperature, which is the
            # one site condition Table 29-4 names that this console could
            # not be got into: "temperature reading is below 0 F (-17.8 C)
            # or above 100 F (37.8 C)" invalidates a leak test, and the
            # wander lives between 48 and 62. `invalidations` had the
            # criterion written and unreachable, and said so. FIDELITY Y10.
            return float(held)
        base = self.base_temperature(tank, at=at)
        # The average of a run of the ladder is the wander plus the average
        # of that run's own offsets, and the offsets do not move with the
        # clock -- so the ladder is not built here. A probe out of the fuel
        # altogether still reads its lowest thermistor, which is the one
        # nearest what is left, and that is what an empty run offsets to.
        volume = self.tank_level.get(int(tank), {}).get("volume", 0.0)
        return base + self._submerged_at(int(tank), volume)[1]

    def adjust_volume(self, tank, gallons):
        """Move the product by a route no watcher sees.

        Positive is fuel arriving, negative is fuel leaving, and neither
        is a delivery or a load: the two watchers are told to start again
        from the new level. What the RECONCILIATION sees is a different
        matter -- a book that says the tank should hold what it held and a
        probe that says otherwise is exactly the variance the BIR chapter
        is written to expose. See BENCH.md T5.
        """
        tank = int(tank)
        st = self.tank_level.setdefault(tank, {"volume": 0.0, "water": 0.0})
        was = st.get("volume", 0.0)
        st["volume"] = max(0.0, min(self.full_volume(tank), was + gallons))
        self.deliveries.book(tank)
        self.loads.book(tank)
        return st["volume"] - was

    def hold_temperature(self, tank, degrees):
        """Hold a tank at a temperature, or let it follow the season again.

        The change is stamped, because a hold that MOVES is the only way a
        technician can stage Table 29-4's thermistor drift on this bench:
        "average temperature of all submerged thermistors changed by more
        than 0.1 F per hour". See FIDELITY Y10.
        """
        import time as _time
        tank = int(tank)
        was = self.tank_temp.get(tank)
        if degrees is None:
            self.tank_temp.pop(tank, None)
        else:
            self.tank_temp[tank] = float(degrees)
        if was != self.tank_temp.get(tank):
            self.tank_temp_log.setdefault(tank, []).append(
                (_time.mktime(self.now()), was))
            del self.tank_temp_log[tank][:-32]

    def held_temperature(self, tank, at=None):
        """What the bench was holding this tank at, then or now."""
        tank = int(tank)
        if at is None:
            return self.tank_temp.get(tank)
        for when, before in reversed(self.tank_temp_log.get(tank) or ()):
            if at < when:
                return before
        return self.tank_temp.get(tank)

    def _meter_line(self, device, what):
        """"FP: XX M: XX =T X": one row of the BIR meter map."""
        meters = sorted(self.meters)
        if not meters:
            return "NO METERS MAPPED"
        key = meters[(device - 1) % len(meters)]
        tank = self.meters[key]
        return ("METER MAP" + chr(10)
                + f"FP: {key.fp:02d} M: {key.meter:02d} =T {tank}")

    def _event_line(self):
        """The Meter Events Table's newest row, as Figure 6-25 draws it.

        Two shapes, and which one it is is the figure's own branch: "If the
        last event is an End Event, you see" `FP: XX  M: XX  =T X` over
        `END EVENT: X GALS`, and "if the last event is a Start Event"
        `FP: XX` over `START EVENT` -- a meter that has begun to run has no
        gallons yet and no completed transaction to hang them on. This read
        the meter's LIFETIME total and printed it, unlabelled, on the line
        the figure leaves to the map. See FIDELITY D6 and D14.
        """
        event = self.bir.last_event()
        if event is None:
            return None
        if event["kind"] == "start":
            return f"FP: {event['fp']:02d}"
        return (f"FP: {event['fp']:02d} M: {event['meter']:02d}"
                f" =T {event['tank']}")

    # ---- the probe ----------------------------------------------------------
    def probe_codes_available(self):
        """The circuit codes this console's software will admit.

        A probe is not programmed, it is fitted: the console reads its circuit
        code and works out what it has. So the bench chooses the code, and the
        only thing the console gets a say in is whether its software knows
        that family at all. `readings.PROBE_FEATURE` names the row of the
        version matrix each family sits in, and the CAP probes are the ones
        that bite -- Cap 1 stops at version 8 and Cap 0 at version 17, so a
        console running anything modern cannot have one fitted.
        """
        out = []
        for code in readings.PROBE_MODELS:
            need = readings.PROBE_FEATURE.get(code)
            if need is None or self.supports(need):
                out.append(code)
        return out

    def probe_detects_water(self, tank):
        """Has this tank's probe a water float on it?

        The one-float Mag probes -- MAG4, 5, 6, 10, 11 and 12 -- carry no
        water float, and Table 9-2 marks them "Water Detect: No". Those are
        the probes the manuals call high alcohol probes, and a console hides
        its water screens for them: "If you are using high alcohol probes,
        Water Volume will not appear on the display or the printed reports."

        A tank with nothing fitted answers True, because every preset here
        carries a two-float probe.
        """
        fitted = self.probe_fitted.get(tank)
        if not fitted:
            return True
        model = readings.PROBE_MODELS.get(fitted)
        return True if model is None else bool(model[3])

    def probe_leak_rating(self, tank):
        """What leak rate this tank's probe is rated to detect.

        "0.10", "0.20" or "none", off `PROBE_MODELS` -- the fifth field of
        the row, which nothing consulted. 576013-623 p.8-2 hangs a whole
        feature off it: "The CSLD option appears only when the tank is
        equipped with a 0.1 gph (0.38 lph) Mag probe, and the system has
        the CSLD software module key installed." See FIDELITY K6.

        A tank with nothing fitted answers the capable rating, the same way
        `probe_detects_water` answers True: every preset here carries a
        probe that can do the job, and a bench that has not been told
        otherwise should not disable a feature behind the user's back.
        """
        fitted = self.probe_fitted.get(tank)
        if not fitted:
            return "0.10"
        model = readings.PROBE_MODELS.get(fitted)
        return "0.10" if model is None else model[4]

    def float_size(self, tank):
        """S62F as the enum digit, without the device prefix on the front.

        The stored value is `01` and then the choice -- `011` is tank 1 with
        the 2.0 inch kit -- so a comparison against the bare digit has to
        take the prefix off first. `bench.py` compared the whole string to
        `"4"` to decide whether to draw the larger phase separation float,
        which is never true on a console programmed by anything. See
        FIDELITY R12.
        """
        return (self.values.get(f"S62F{tank:02d}") or "").strip()[-1:]

    def probe_minimums(self, tank):
        """(minimum fuel, minimum water) this probe can resolve, in inches.

        576013-818 Rev AB Table 9-2, per circuit code and float kit. Below
        the fuel figure the product and water floats are too close together
        to be told apart, and the console says so rather than reporting a
        depth it cannot measure: 576013-939 Quick Help p.14, "T1: INVALID
        FUEL LEVEL ... CAUSE: Fuel and water level floats on the probe are
        too close together due to a lack of fuel in the tank."

        `(None, None)` for a probe or a float size the table does not cover
        -- the CAP probes, and the 1 inch float, which has no column on the
        page. A minimum nobody published is not a minimum of zero, so those
        raise nothing. See FIDELITY R12 and H11.
        """
        size = self.float_size(tank)
        if size == "9":
            # The custom float brings its own, and 576013-879 Rev W p.34
            # gives the numbers to enter: the Media-Isolated probe's
            # stainless steel float wants "an Invalid Fuel value of
            # +0003.300", which the figure beside it calls "3.3" Approximate
            # lowest product level measured". That is this quantity, named
            # twice on one page. See FIDELITY R13.
            return (self._float_parameter(tank, 2),
                    self._float_parameter(tank, 3))
        row = readings.MINIMUM_LEVELS.get(self.probe_circuit_code(tank))
        if not row:
            return (None, None)
        return row.get(size, (None, None))

    def fuel_below_minimum(self, tank):
        """Is there too little product for this probe to measure it?

        The product HEIGHT against the probe's own minimum, which is what
        the table is about; the programmed percent-volume minimum is a
        different rule with a different consequence (a leak test that does
        not count, rather than a level the console cannot read).

        **And the minimum rises with the water.** 577013-940 Rev F p.43, on
        the Invalid Fuel field: "The invalid fuel level assumes no water is
        present. **If water is present, the invalid fuel level is increased
        by the water level reading.**" So the test is a SEPARATION between
        the two floats rather than a depth, which is what every cause line
        this alarm has ever had describes -- 576013-939 p.14's "Fuel and
        water level floats on the probe are too close together due to a lack
        of fuel in the tank". A tank with four inches of water needs four
        more inches of product before its floats are far enough apart.

        The figure is stated on the TLS-450's page for a field set the
        TLS-350 shares, and it is the only statement of the rule anywhere.
        See UNKNOWNS A36.
        """
        minimum = self.probe_minimums(tank)[0]
        if not minimum or tank in self.probe_out:
            return False
        volume = self.tank_level.get(tank, {}).get("volume")
        if volume is None:
            return False
        return (self.height_at(tank, volume)
                < minimum + self.water_height(tank))

    def has_ecpu(self):
        """Is the fitted board an enhanced CPU rather than a plain one?

        `versions.BOARDS` names them: `C0` and `C5` are `CPU` and everything
        else is an ECPU, an ECPU1, an ECPU2 or an MSP ECPU2. The screens
        gated on this are the Peripheral Controller's, which is the second
        processor an ECPU has and a CPU does not.
        """
        return "ECPU" in versions.board_name(self.board)

    def dim_letter(self):
        """"M for MDIMs and E for all other DIMs" -- Figure 6-2's own note.

        The DIM diagnostic screens hard-coded `M1:`, so a console with an
        EDIM in the comm bay headed them with the mechanical letter. See
        FIDELITY D10.
        """
        return "M" if self.has("mdim") else "E"

    def probe_type(self, tank):
        """"MAG PROBE", "CAP0 PROBE" or "CAP1 PROBE", which the screens head with.

        Read off the probe's circuit code, which is how a real console knows:
        "The system automatically recognizes which Mag probe type you have
        installed". There is no Set Probe Type function anywhere in the serial
        manual and no probe-type step in In-Tank Setup, so nothing here lets
        the KEYPAD choose one -- the bench fits the probe, the console reads
        it. See probe_codes_available.
        """
        fitted = self.probe_fitted.get(tank)
        if fitted:
            model = readings.PROBE_MODELS.get(fitted)
            if model:
                return f"{model[1]} PROBE"
        # Nothing fitted: the old rule, which is a proxy rather than a model.
        # A tank programmed with a float size has a float, and only a Mag has
        # one. Gating it on `supports("cap0")` as well was tried and reverted:
        # every CAP report in the census is reached by clearing a tank's float
        # size on a modern console, so a version gate here makes eight
        # documented codes unreachable to any test. The gate belongs where the
        # bench FITS a probe -- `probe_codes_available` already has it -- and
        # the defect R11 named was in the presets. See FIDELITY R11.
        return "MAG PROBE" if self.values.get(f"S62F{tank:02d}") else "CAP0 PROBE"

    def probe_length(self, tank):
        """The probe fitted to this tank, in inches.

        Probes come in the lengths Table 9-3 lists rather than cut to fit, so
        this is the shortest standard one that clears the tank.
        """
        return readings.probe_length(self.limit("607", tank) or 96.0)

    def probe_gradient(self, tank):
        """"Probe calibration factor used to calculate water height and
        product height. Normal operating range 175 - 185 or 347 - 357."

        The two bands are two generations of probe, not two products: the
        manual's own site reads one gradient across regular, plus and
        premium. A Mag Plus is the later band.
        """
        low, high = (readings.GRADIENT_BAND
                     if self.probe_type(tank) == "MAG PROBE"
                     else readings.GRADIENT_BAND_OLD)
        return readings.wander(self, low, high, "grad", tank, swing=0.04)

    def probe_channel(self, tank, n):
        """One of the nineteen channels behind the IN-TANK DIAGNOSTIC screens.

        The manual's template for the A12 command names them, and they are
        not what a reader would guess from the screen: nineteen labels for
        nineteen channels, in this order.

            WATER HEIGHT0 HEIGHT1 HEIGHT2 HEIGHT3 HEIGHT4 HEIGHT5 HEIGHT6
            HEIGHT7 HEIGHT8 HEIGHT9 TMP REF TMP5 TMP4 TMP3 TMP2
            TMP1 TMP0 TMP REF

        So C00 is the water float, C01 to C10 are ten reads of the product
        float, C11 and C18 are the two temperature references, and C12 to
        C17 are the six thermistors. They are RAW COUNTS: a height in inches
        times the gradient. The segment sensitivity ratios that sound like
        they belong here are a different report, IA06, and only a CAP probe
        has them.

        "Channels 00 - 05 will update every sample. Channels 06 - 18 update
        only following a system-read": which is why the manual's own data
        has C01-C05 reading 23473 while C06-C10 read 23485 on the same probe.
        """
        gradient = self.probe_gradient(tank)
        if n == 0:
            # "All Probes - C00 (No Water) - 0 - 1500", plus whatever water
            # is standing on the bottom
            water = self.tank_level.get(tank, {}).get("water", 0.0)
            floor = readings.fixed(*readings.WATER_FLOOR, "c00", tank)
            return floor + water * gradient
        if n in (11, 18):
            # the two references, which a healthy probe reads within a
            # count or three of each other
            base = readings.fixed(41800.0, 45800.0, "tmpref", tank)
            return base + (0.0 if n == 11 else readings.fixed(-3.0, 3.0,
                                                              "tmpref2", tank))
        if 12 <= n <= 17:
            # TMP5 down to TMP0, six thermistors up the probe, and they are
            # the SAME six A15 prints as temperatures. They used to be a
            # second ladder built beside the first: `(n - 12) * 780.0` counts,
            # which is 1.8 F a step and 9.1 F across the six against A15's own
            # sample of five, and it only ever ADDED -- so the mean of the six
            # channels sat four and a half degrees above the average the
            # console reported for the same tank at the same moment, on a
            # reading Table 29-4 defines AS the average of them. One ladder
            # now, and the centring comes free. See FIDELITY Y5.
            #
            # TMP0 is the bottom of the probe and the warm end -- the fuel has
            # been longest out of the weather down there -- so C17 is the
            # warmest count and C12 the coolest, which is the order this
            # channel list was already in.
            return self.thermistor_counts(tank)[17 - n]
        # C01 to C10, the product float, ten times
        height = self.stick_height(tank)
        stale = 0.0 if n <= 5 else readings.fixed(8.0, 16.0, "stale", tank)
        jitter = readings.fixed(-0.4, 0.4, "chan", tank, n)
        return max(700.0, height * gradient + stale + jitter)

    def probe_circuit(self, tank):
        """"ID CHAN", which Table 9-2 calls the probe's manufacturing code.

        A probe FITTED on the bench answers with its own code. Otherwise the
        code is derived from what the tank is set up to test at, which is how
        this console has always guessed at one.
        """
        fitted = self.probe_fitted.get(tank)
        if fitted:
            return f"0x{fitted}"
        raw = (self.values.get(f"S611{tank:02d}") or "")
        body = raw[2:] if len(raw) > 8 else raw
        rate = "0.10" if body[2:3] == "1" else "0.20"
        return readings.PROBE_CIRCUIT.get(rate, "0xD004")

    # Function code A01's three columns for what a probe IS, as against what
    # it is reading. "01=CAP0, 02=CAP1, 03=MAG1" is the manual's enumeration;
    # this console tells a Mag from a CAP by whether a float size was ever
    # programmed, which is the same test probe_type() makes, so it answers
    # with two of the three and never claims to be the CAP1 it cannot tell.
    PROBE_TYPE_CODES = {"MAG PROBE": ("03", "MAG"), "CAP0 PROBE": ("01", "CAP0")}

    def probe_type_code(self, tank):
        """"PP - Probe Type", the two digits A01 puts on the wire."""
        return self.PROBE_TYPE_CODES.get(self.probe_type(tank), ("01", "CAP0"))[0]

    def probe_type_word(self, tank):
        """TYPE, the way the A01 printout spells it: MAG, CAP1 or CAP0."""
        return self.PROBE_TYPE_CODES.get(self.probe_type(tank), ("01", "CAP0"))[1]

    # A15 spells the type out where A01 abbreviates it: "PROBE TYPE MAG 1"
    # against A01's "MAG" column, and A07 heads the same probe "MAG7". Three
    # reports, three spellings, all in one section. Each is followed where it
    # is printed rather than picking one and calling the others wrong.
    PROBE_TYPE_LONG = {"MAG": "MAG 1", "CAP0": "CAP 0", "CAP1": "CAP 1"}

    def probe_name_type(self, tank):
        """The name the console calls this probe, from its circuit code.

        Three spellings of "MAG n" exist and only this one is derived. A01's
        TYPE column prints the bare family word, because the wire's own
        enumeration has only 03=MAG1 to offer it; A15 spells that enumeration
        out as "MAG 1"; and this one, the Name Type, is looked up from the
        circuit code in 577013-940 Rev D p.360. A07 heads its tank with it,
        which is why the manual's A07 example says MAG7 where its A01 four
        pages earlier says MAG. See readings.MAG_NAME_TYPE and UNKNOWNS A13c.
        """
        word = self.probe_type_word(tank)
        if word != "MAG":
            return word
        return readings.MAG_NAME_TYPE.get(self.probe_circuit_code(tank), word)

    def probe_type_long(self, tank):
        """The way A15's printout spells the probe type."""
        word = self.probe_type_word(tank)
        return self.PROBE_TYPE_LONG.get(word, word)

    def probe_circuit_code(self, tank):
        """"KKKK - Circuit Code (Hex)", the same code ID CHAN shows.

        probe_circuit() writes it the way the diagnostic screen does, with
        the 0x on the front, because that is how Table 9-2 prints it. A01
        wants the four digits on their own.
        """
        return self.probe_circuit(tank)[2:].upper()

    def probe_date_code(self, tank):
        """"cccc - Probe Date Code (Hex)".

        What a Veeder-Root probe date code MEANS is sourced -- 577013-950
        Rev G p.590, on the TLS-450's N05: "yywwrr - Date Code (decimal),
        yy = year, ww = week, rr = revision". What is NOT sourced is how a
        TLS-350 packs a year, a week and a revision into four hex digits,
        and the published examples rule out inferring it: A01's own 1401 and
        2410 are anachronistic in a report dated 1996, A15's 2774 gives week
        116 read as decimal, and the TLS-450 manual's rewritten A01 prints
        091A and 082B in a report dated 2009. At least one example set is
        invented. So nothing is derived here. See UNKNOWNS A13b. It is stable
        per probe, four hex digits, and that is all it claims to be.
        """
        if tank not in self.programmed_tanks():
            return "0000"
        return f"{zlib.crc32(f'dcode{tank}'.encode()) % 0x10000:04X}"

    def _probe_reading(self, token, tank):
        if token == "probe_serial":
            return f"SERIAL NUMBER {self.probe_serial(tank)}"
        if token == "probe_length":
            return f"LENGTH: {self.probe_length(tank):.2f}"
        if token == "probe_samples":
            # "Under normal operating conditions, this number should read 20."
            n = (readings.MAG_SAMPLES if self.probe_type(tank) == "MAG PROBE"
                 else readings.CAP_SAMPLES)
            return f"NUM SAMPLES: {n}"
        if token.startswith("probe_c") and token[7:].isdigit():
            # 576013-818 Rev AA Figure 6-6: "consists of a measurement from
            # each of the probe's channels (00 - 18) ... Press STEP to cycle
            # through the remaining channels for this probe." So the console
            # shows them two at a time, C00 with C01 and so on, and C18 --
            # there being nineteen of them -- on its own at the end.
            first = int(token[7:])
            if first >= 18:
                return f"C18 {self.probe_channel(tank, 18):.1f}"
            return (f"C{first:02d} {self.probe_channel(tank, first):.1f}"
                    f" C-{first + 1:02d} "
                    f"{self.probe_channel(tank, first + 1):.1f}")
        if token == "probe_gradient":
            return f"GRADIENT = {self.probe_gradient(tank):.3f}"
        if token == "probe_idchan":
            return f"ID CHAN = {self.probe_circuit(tank)}"
        return ""

    # ---- the sensors --------------------------------------------------------
    SENSOR_DIAG_MODULE = {"sensor_liquid": "liquid", "sensor_vapor": "vapor",
                          "sensor_gw": "gw", "sensor_2wire": "2wire",
                          "sensor_3wire": "3wire",
                          "sensor_groundtemp": "gw"}

    def sample_counter(self):
        """"Cntr = Number of times system has looked at Value."

        A counter, so it counts: the module looks at each sensor about once
        a second and the screen has room for two digits of it. Running the
        bench clock fast runs the counter fast, the same as everything else
        here.

        It lives here because two readers need the same one. The wire
        counted and the panel did not: `IB01` moved with the clock while
        the LIQUID DIAGNOSTIC screen printed a hardcoded `CNTR = 1` and the
        2-WIRE one `CNTR = 5`, taken from the manual's figures -- where the
        first is a placeholder X and the second a captured reading. So a
        technician reading the panel and then asking the same console for
        the same figure over the wire got two different numbers, which is
        the one rule `wiresensors` states in its own docstring. See
        FIDELITY L9.
        """
        return int(time.mktime(self.now())) % 100

    def _sensor_diag(self, token, number):
        """"CNTR = X VALUE = XXXXXX": the resistance the module is reading.

        Which band it falls in IS the sensor's state, which is the whole
        point of the screen, so this is derived from the state the bench has
        the sensor in and the bands the Troubleshooting Guide prints.
        """
        if token == "sensor_ppm":
            # "Sensor detected hydrocarbon vapor (see value 2 above)
            # converted to parts per million" -- so it is the same reading
            # as the second channel and not the same NUMBER. See FIDELITY R7.
            state = self._sensor_state("vapor", number)
            ohms = readings.sensor_value(self, "vapor", number, state,
                                         channel=2)
            return f"{readings.vapor_ppm(self, number, ohms):.0f} PPM"
        module = self.SENSOR_DIAG_MODULE.get(token)
        if not module:
            return ""
        state = self._sensor_state(module, number)
        one = readings.sensor_value(self, module, number, state, channel=1)
        counter = self.sample_counter()
        if token == "sensor_liquid":
            return f"CNTR = {counter} VALUE = {one:.0f}"
        if token == "sensor_2wire":
            return f"CNTR = {counter} VALUE = {one:.0f}"
        if token == "sensor_groundtemp":
            # the ground temperature channel, in counts
            # Driven off the ground temperature rather than picked. This
            # used to wander 480 to 620 ohms, and the same page that draws
            # the screen says under 1000 means the thermistor may be
            # shorted -- so every reading this console gave was a fault, on
            # the one screen whose whole job is telling a short from a live
            # probe. Fuel underground sits near the ground temperature, so
            # the tank's own reading stands in for it.
            warm = self.product_temperature(number)
            return f"CNTR = {counter} VALUE = {ground_ohms(warm):.0f}"
        two = readings.sensor_value(self, module, number, state, channel=2)
        return f"1 = {one:.0f} 2 = {two:.0f}"

    def _sensor_state(self, module, number):
        state = self.sensor_state.get((module, str(number)), "normal")
        if state != "normal" and not self.sensor_alarm_allowed(module, number,
                                                               state):
            return "normal"
        return state

    def _vac_diag(self, token, number):
        """Figures 6-29 and 6-30's vacuum sensor readings.

        The three result screens are the last manual test that finished, and
        a sensor that has not run one answers nothing -- which drops the
        renderer back on the figure's own placeholder line rather than a
        blank, the rule D2 settled.

        Every one of these fills the display to its own column. The three
        result screens are read off Figure 6-30 by word position, and the
        leak rate off Figure 6-29 for the reason in FIDELITY D1: 6-30 draws
        it in PSI and 6-29 draws it in GPH with two sentences of prose saying
        gph, so 6-29 wins and its line is `LEAK RATE:    0.123 GPH`.
        """
        if token == "vac_state":
            # "s 1: VACUUM OK" over "-7.14 PSI VCV: CLOSED"
            psi = self.vac_psi(number)
            word = "NO VACUUM" if psi > self.NO_VACUUM_PSI else "VACUUM OK"
            valve = "OPEN" if self.vac_valve_open(number) else "CLOSED"
            return (f"s {int(number)}: {word}" + chr(10)
                    + f"{psi:.2f} PSI VCV: {valve}")
        result = self.vac_result(number)
        if result is None:
            return ""
        head = (f"s {int(number)}: "
                + time.strftime("%m-%d-%y", time.localtime(result["at"]))
                + "  " + clock_hhmm(time.localtime(result["at"])))
        if token == "vac_rate":
            return head + chr(10) + f"LEAK RATE:{result['rate']:>9.3f} GPH"
        if token == "vac_no_vac":
            hours = result["hours"]
            # "Predicted time (in hours : minutes)", HHH:MM. A space that is
            # not filling has no predicted time, and the manual's field is
            # three digits wide, so the console says so rather than printing
            # a number it would have to invent.
            shown = ("---:--" if hours is None
                     else f"{int(hours):3d}:{int(round((hours % 1) * 60)):02d}")
            return head + chr(10) + f"NO VAC TIME:{shown:>12s}"
        if token == "vac_ratio":
            ratio = result["ratio"]
            if ratio is None:
                # The dashes go where the FIGURE's number goes and nowhere
                # else. This spaced them out -- `EVAC RATIO: --- @ -7.1PSI`
                # -- which is a line shape no page draws, and the citation
                # audit would have filed it as a stray the day a walk
                # reached it. The walk never runs a manual test, so it
                # never did. FIDELITY L18.
                return head + chr(10) + f"EVAC RATIO:--- @{result['psi']:.1f}PSI"
            # Figure 6-29's own compact form, which is the one that fits:
            # `EVAC RATIO:5.2 @-4.1PSI` is twenty-three characters where
            # 6-30 draws the same reading spaced out to twenty-nine.
            return (head + chr(10)
                    + f"EVAC RATIO:{ratio:.1f} @{result['psi']:.1f}PSI")
        return ""

    def _smart_diag(self, token, number):
        """The Mag, Vac and ATMP sensor screens behind SMART SENSOR DIAGS."""
        if token == "ss_serial":
            return f"SERIAL NUMBER: {readings.digits(8, 'ss', number)}"
        if token == "ss_serial9":
            return f"SERIAL NUMBER {readings.digits(9, 'ss9', number)}"
        if token == "ss_atm":
            # 576013-818 Rev AB Figure 6-32, screen and printout both:
            # "Current atmospheric pressure RELATIVE TO SEA LEVEL as measured
            # by the ATM P Sensor", over `ATM PRESSURE:      0.062 PSI`, and
            # both revisions of 576013-635 print the same figure in B37.
            #
            # This drew `14.433`, one atmosphere ABSOLUTE -- a different
            # quantity two hundred times the size, and the one number on this
            # screen a technician could not use. See FIDELITY D11.
            #
            # The BAND is invented and the sign is not a choice: the only
            # sample on this shelf is positive, and the citation audit is
            # against the screen the manual draws, so a console that could
            # print `ATM PRESSURE: -0.140 PSI` would be drawing a line no
            # page carries. See UNKNOWNS A28 for what that leaves open.
            return ("ATM PRESSURE: "
                    + f"{readings.wander(self, *ATM_BAND, 'atm', number, period=7200.0):.3f}"
                    + " PSI")
        # The total is the sum, which is what the manual's own sample
        # says: `TOTAL HT 15.0` over `FUEL HT 5.0` and `WATER HT 10.0`. All
        # three used to be generated independently, so a sensor reported a
        # total of 38.8 inches standing over a fuel of 0.6 and a water of
        # 2.1 -- and 38.8 is taller than the 18 to 36 inch LENGTH the same
        # sensor's own `B36` constants give it, so the two reports between
        # them described a liquid column longer than the sensor holding it.
        # See FIDELITY L8.
        # ROUNDED before it is summed, because the sum has to hold on the
        # paper: every one of these prints to a tenth, and a technician
        # adding the column up is adding the printed figures.
        fuel = round(readings.wander(self, 0.0, 1.2, "ssfuel", number,
                                     swing=0.5), 1)
        # The water is the sump's, the same water the leak test measures:
        # it wandered on its own and a sump with twelve inches poured into
        # it read two. FIDELITY U1b.
        water = round(self.sumps.height(number), 1)
        total = fuel + water
        if token == "ss_total_ht":
            return f"TOTAL HT      {total:.1f} IN."
        if token == "ss_fuel_ht":
            return f"FUEL HT       {fuel:.1f} IN."
        if token == "ss_water_ht":
            return f"WATER HT      {water:.1f} IN."
        if token == "ss_install":
            # "An Install Alarm is posted if the Mag Sensor is not firmly
            # resting on the bottom of the monitored pan/sump"
            bad = self.sensor_state.get(("smart", str(number))) == "install"
            pos = readings.fixed(3.0, 9.0, "ssinst", number) if bad else 0.0
            return f"INSTALL POS   {pos:.1f} IN."
        if token == "ss_fluid_temp":
            # the sump's water, which was the temperature of the TANK with
            # the sensor's number
            return f"FLUID TEMP  {self.sumps.temperature(number):.1f} DEG F"
        if token == "ss_board_temp":
            # a board runs warmer than what it is standing in
            return f"BOARD TEMP  {self.sumps.temperature(number) + readings.fixed(8.0, 16.0, 'ssboard', number):.1f} DEG F"
        return ""

    def fuel_products(self):
        """[(code, [tank, ...])] for FUEL MANAGEMENT, lowest tank first.

        576013-610 Rev AC p.7-1, and 576013-623 Rev AN p.9-1 word for word:
        "The system assumes tanks with the same product code contain the
        same product. All information displayed is for products, not
        tanks. The product name is the product label of the lowest tank
        number containing the product."

        A tank with no product code programmed is its own product, because
        the console cannot say it is the same as anything else.
        """
        out = {}
        for tank in sorted(self.programmed_tanks()):
            code = (self.text("603", tank) or "").strip()
            out.setdefault(code or f"#{tank}", []).append(tank)
        return sorted(out.items(), key=lambda kv: kv[1][0])

    def fuel_tanks(self, index):
        """The tanks of the index'th product, counting from 1."""
        products = self.fuel_products()
        if not products:
            return []
        return products[(int(index) - 1) % len(products)][1]

    def fuel_label(self, index):
        """"REGULAR UNLEADED": the product's name, which is the label of the
        lowest tank carrying it."""
        tanks = self.fuel_tanks(index)
        if not tanks:
            return "NO TANKS"
        return (self.text("602", tanks[0]) or "").strip() or f"T {tanks[0]}"

    def fuel_reading(self, token, index):
        """One FUEL MANAGEMENT screen, for a PRODUCT rather than a tank.

        The widths are the manual's own, read off pp.7-2 and 7-3 by word
        position: `DAYS FUEL REMAINING: 2.4` is twenty-four characters
        exactly with no unit word after it, `INVENTORY` right-justifies its
        volume to column 20, `95% ULLAGE = ` runs on, and the seven sales
        screens right-justify to column 18. All four say GAL, singular,
        where the diagnostic's own Figure 6-7 says GALS. See FIDELITY O4.
        """
        tanks = self.fuel_tanks(index)
        if token.startswith("fuel_avg_"):
            day = token.rsplit("_", 1)[1]
            total = sum(self._fuel_day_sales(day, tank) for tank in tanks)
            return f"AVG SALES-{day}: {total:>4.0f} GAL"
        volume = sum(self.tank_level.get(t, {}).get("volume", 0.0)
                     for t in tanks)
        if token == "fuel_inventory":
            return f"INVENTORY {volume:>10.0f} GAL"
        if token == "fuel_ullage95":
            room = sum(max((self.full_volume(t) or 0.0) * 0.95
                           - self.tank_level.get(t, {}).get("volume", 0.0),
                           0.0) for t in tanks)
            return f"95% ULLAGE = {room:.0f} GAL"
        if token == "fuel_days":
            # "how many days of fuel you have remaining BEFORE YOU RECEIVE A
            # LOW PRODUCT ALARM", and "the Low Product alarm activates when
            # the amount of fuel falls below the Low Product Limit set
            # during In-Tank Setup". So it is the fuel ABOVE that limit, not
            # the fuel in the tank. This read volume over sales, which is
            # days to empty and a different number.
            # "The average daily sales is the average of the total daily
            # sales accumulated for a 24 hour period", and the console holds
            # one for each day of the week, so the divisor is the week's own
            # mean rather than a separate figure -- which also makes the
            # days screen agree with the seven the function walks past.
            sales = sum(self._fuel_day_sales(day, tank)
                        for tank in tanks
                        for day in DAY_NAMES) / len(DAY_NAMES)
            spare = volume - sum(self.limit("621", t) or 0.0 for t in tanks)
            if sales <= 0:
                return "DAYS FUEL REMAINING: 0.0"
            return f"DAYS FUEL REMAINING: {max(spare, 0.0) / sales:.1f}"
        return ""

    def _fuel_day_sales(self, day, tank):
        """One tank's average daily sales, for a day of the week or flat."""
        if day:
            line = self._fuel_diag(f"fm_avg_{day}", tank)
            return float(line.rsplit(" ", 2)[-2]) if line else 0.0
        return self.limit("683", tank) or 0.0

    def _fuel_diag(self, token, tank):
        """"AVG SALES-SUN: XXXX GALS": Fuel Manager's week, day by day."""
        _fm, what, day = token.split("_", 2)
        # Fuel Manager holds an average for each day of the week and the
        # setup menu programs all seven (576013-623 Rev AN p.128). Where one
        # has been programmed it is the answer; where none has, the week is
        # shaped off the single figure function code 683 was last given,
        # because a forecourt is not flat across the week.
        if day in DAY_NAMES:
            set_to = self.setting(f"avg_sales_{DAY_NAMES.index(day) + 1}",
                                  tank, "")
            try:
                if str(set_to).strip():
                    programmed = float(set_to)
                else:
                    programmed = None
            except ValueError:
                programmed = None
        else:
            programmed = None
        average = self.limit("683", tank) or readings.fixed(400, 1400, "sales",
                                                            tank)
        shape = {"SUN": 0.78, "MON": 0.94, "TUE": 0.97, "WED": 1.00,
                 "THR": 1.06, "FRI": 1.22, "SAT": 1.03}.get(day, 1.0)
        value = programmed if programmed is not None else average * shape
        if what == "last":
            value *= 1.0 + readings.fixed(-0.12, 0.12, "lastsales", tank, day)
        if what == "pred":
            value *= 1.0 + readings.fixed(-0.05, 0.05, "predsales", tank, day)
        label = {"avg": "AVG SALES", "last": "LAST SALES",
                 "pred": "PRED SALES"}[what]
        # Figure 6-7 draws GALS on all three, not GAL on two of them
        unit = "GALS"
        return f"{label}-{day}: {value:.0f} {unit}"

    # ---- the yes/no screens in Diag Mode ------------------------------------
    def diag_action(self, name, device=1, ident=None):
        """Do what a NO/YES diagnostic screen says it does.

        These are the screens the figures draw with an answer on them:
        REINIT COMM BD, DELETE CSLD RECORDS, RESET ACCUCHART, and the
        Maintenance Tracker key block. Every one of them was navigable and
        inert before.
        """
        dev = int(device)
        if name == "modem_config":
            # Figure 6-27: CHANGE to `AUTO CONFIG MODEM: YES`, "Select Yes to
            # run manually run Auto Detect again", and ENTER confirms it over
            # PRESS <STEP> TO CONTINUE. The question after it is its own
            # screen. FIDELITY D27.
            return "AUTO CONFIG MODEM: YES"
        if name == "modem_config_sure":
            # `AUTO CONFIG MODEM: YES / ARE YOU SURE? : YES`, ENTER, and the
            # figure's `ARE YOU SURE? : YES / PRESS <STEP> TO CONTINUE`. What
            # an auto-detect finds is the modem this console already reports.
            return "ARE YOU SURE? : YES"
        if name == "mt_select":
            # Figure 6-4 blocks J DOE by pressing CHANGE on J DOE's own row
            # and then ENTER, so the row IS the selection: this is what puts
            # a key in front of the ARE YOU SURE screen below.
            self.mt_pending = (ident or "").strip().upper()
            return "BLOCK: YES"
        if name.startswith("isd_select_"):
            # Figure 4's own walk: ENTER on a selection, then ENTER on
            # CLEAR TEST AND LOG. The selection is remembered rather than
            # acted on, the way `mt_select` remembers a key, and the
            # acknowledgement is the selection's own line.
            self.isd_pending = name[-2:]
            return isd.CLEAR_MENU.get(self.isd_pending, "")
        if name == "isd_clear":
            code = self.isd_pending
            if code not in dict(isd.SERVICE_TESTS):
                return "CLEAR TEST AFTER REPAIR"
            self.clear_isd_test(code)
            if code != isd.COLLECTION:
                self.values[f"SV85{code}"] = time.strftime("%y%m%d",
                                                           self.now())
            self.save()
            return "CLEAR TEST AND LOG"
        if name.startswith("vac_"):
            # Figure 6-29 and Figure 6-30's two ALL branches. The figure's
            # own acknowledgement is "ALL: MANUAL TEST STARTED" over "PRESS
            # <STEP> TO CONTINUE", and STOP's reads ABORTED -- there is no
            # "test complete" screen anywhere in either figure, so a test
            # stopped by hand writes nothing and a test left alone takes its
            # reading on the next look. See FIDELITY D1.
            if ident == "single":
                # "TO SELECT INDIVIDUAL VAC SENSORS", the second column of
                # both figures: CHANGE on SELECT VAC SENSOR names one sensor,
                # ENTER acts on it alone, and the acknowledgement is
                # `sX: MANUAL TEST STARTED`. Only ALL was reachable. FIDELITY
                # D28.
                if dev not in self.vac_sensors():
                    return "NO VAC SENSORS"
                return {"vac_test_start": self.start_vac_test,
                        "vac_test_stop": self.stop_vac_test,
                        "vac_hold_start": self.start_evac_hold,
                        "vac_hold_stop": self.stop_evac_hold}[name](dev)
            sensors = self.vac_sensors()
            if not sensors:
                return "NO VAC SENSORS"
            for one in sensors:
                if name == "vac_test_start":
                    self.start_vac_test(one)
                elif name == "vac_test_stop":
                    self.stop_vac_test(one)
                elif name == "vac_hold_start":
                    self.start_evac_hold(one)
                else:
                    self.stop_evac_hold(one)
            return {"vac_test_start": "ALL: MANUAL TEST STARTED",
                    "vac_test_stop": "ALL: MANUAL TEST ABORTED",
                    "vac_hold_start": "ALL: EVAC HOLD STARTED",
                    "vac_hold_stop": "ALL: EVAC HOLD ABORTED"}[name]
        if name == "recon_clear_map":
            # Figure 6-24: "Selecting YES restarts BIR and clears the
            # Adjusted Delivery Reports, BIR Reconciliation Records, and
            # Meter Map." The screen was navigable and inert -- it carried
            # no `act`, so CHANGE could not even draw YES, and the console
            # both hid a real function and understated a destructive one.
            # The figure draws two screens and no acknowledgement, so the
            # answer is the line the screen was already showing, which is
            # what `mt_block` does with the same shape.
            #
            # No licence check: the function itself is gated on the BIR
            # key -- `"when": {"software": "bir"}` in `diagdata.json` -- so
            # a console without it has no screen to press ENTER on, and
            # `BIR NOT INSTALLED` is a string this project invented once
            # already and took back out. See FIDELITY U5.
            self.bir.clear_map()
            return "CLEAR TANK MAPS: YES"
        if name == "pmc_clear":
            # 577013-937 Rev J Figure 49's own branch on a polisher's PMC
            # DIAGNOSTIC: PROCESSOR STATUS TEST, then CLEAR TEST AFTER
            # REPAIR / ARE YOU SURE? with CHANGE walking NO to YES. It is ISD
            # DIAGNOSTIC's Processor Status selection by another road, so it
            # clears, logs and dates the same test. FIDELITY I11.
            self.clear_isd_test("03")
            self.values["SV8503"] = time.strftime("%y%m%d", self.now())
            self.save()
            return "CLEAR TEST AFTER REPAIR"
        if name == "accu_reset":
            return self.accuchart.restart(dev)
        if name == "csld_delete":
            # Figure 6-11 draws DELETE CSLD RECORDS: NO, CHANGE to YES, and
            # no acknowledgement. `N RECORD(S) DELETED` was this console's
            # own sentence, and it disagreed with what the same console says
            # for the same action over the port -- S054's `T 1:REGULAR
            # UNLEADED CSLD RECORDS DELETED`. The answer is the line the
            # screen was already showing, which is what `recon_clear_map`
            # does with the same shape. See FIDELITY D22.
            self.csld.delete_table(dev)
            return "DELETE CSLD RECORDS: YES"
        if name == "reinit_comm":
            # a comm board re-initialise drops the port back to its defaults
            for code in ("881", "886"):
                self.values.pop(f"S{code}{dev:02d}", None)
            self.save()
            # Figure 6-2 draws REINIT COMM BD: NO and its YES and nothing
            # after them; `COMM n RE-INITIALIZED` was on no page. D22.
            return "REINIT COMM BD: YES"
        if name == "mt_block":
            # The panel and the port cannot disagree: this used to invent a
            # six-digit number and block nothing, so a key blocked on the
            # screen was still on 8A3's list and missing from 8A4's. The key
            # is the one the screen is standing on -- Figure 6-4 blocks J DOE
            # by pressing CHANGE on J DOE's own row -- or the one typed into
            # ENTER ID TO BLOCK. See FIDELITY D13.
            ident = (self.mt_pending or "").strip()
            if not ident:
                return "NO KEY SELECTED"
            self.block_tracker_key(ident)
            self.mt_pending = ""
            # Figure 6-4's last screen in both branches, which is what the
            # console draws when the block is done.
            return "ARE YOU SURE?: YES"
        return "NOT SIMULATED"

    # A capacitance probe is read in segments, and A02 to A06 all come back
    # the same shape: two reference values and then one per segment. The
    # manual's own examples are a CAP0 answering eight numbers and a CAP1
    # answering eleven, twice, which is 2 + 6 and 2 + 9 per channel.
    CAP_SEGMENTS = {"CAP0": 6, "CAP1": 9}

    # The bands are read off A02's and A03's worked examples rather than
    # invented: a CAP0's factory drys are 97 and 180 and then six segments in
    # the 640s and 650s, and its wets 130 and 335 and then six in the 1200s.
    CAP_DRY_REFS = ((90.0, 106.0), (168.0, 192.0))
    CAP_DRY_SEGMENT = (640.0, 666.0)
    CAP_WET_REFS = ((124.0, 142.0), (322.0, 348.0))
    CAP_WET_SEGMENT = (1194.0, 1228.0)

    def probe_calibration(self, tank, wet=False, updated=False):
        """The numbers behind A02 to A05.

        A02 and A03 are what the factory measured, A04 and A05 what the probe
        has settled on since. The manual's example has the updated drys
        identical to the factory ones and one updated wet different by a few
        counts, which is what a probe recalibrated once looks like: the same
        numbers with a little drift on them.

        A Mag probe has no segments to calibrate. A02 and A03 answer it with
        its gradient, which is the one number that IS its calibration -- "MAG
        GRADIENT= 178.1400" is the whole of that tank's line. A04 and A05
        answer it with nothing: the example prints "TANK 1 REGULAR UNLEADED
        MAG" and stops there.
        """
        if self.probe_type(tank) == "MAG PROBE":
            return [] if updated else [self.probe_gradient(tank)]
        tag = "wet" if wet else "dry"
        refs = self.CAP_WET_REFS if wet else self.CAP_DRY_REFS
        band = self.CAP_WET_SEGMENT if wet else self.CAP_DRY_SEGMENT
        out = [readings.fixed(lo, hi, "cal", tag, tank, i)
               for i, (lo, hi) in enumerate(refs)]
        for n in range(self.CAP_SEGMENTS.get(self.probe_type_word(tank), 6)):
            out.append(readings.fixed(*band, "cal", tag, tank, "seg", n))
        if updated:
            out = [v + readings.fixed(-4.0, 4.0, "upd", tag, tank, i)
                   for i, v in enumerate(out)]
        return out

    def probe_ratios(self, tank):
        """A06, Probe Segment Sensitivity Ratios.

        Each position's wet constant as it stands now, against the wet
        constant the factory measured for that same position. A position
        answering being wetted the way it always did reads 1.000; one whose
        constant has moved reads how far it has moved, which is the whole use
        of the screen -- CAP0's example is a probe with ONE bad position,
        `0.000 1.023 0.279 0.971 1.010 1.003 1.010 0.988`, and 0.279 is a
        segment answering at a quarter of what it was built to.

        US 4,349,882, Veeder Industries' own predecessor system, is what says
        the quantity is a ratio of WET CONSTANTS rather than of wet-minus-dry
        spans: the microcomputer "calculates and stores 'wet' constant
        ratios", and uses them so that "a 'wet' constant of a lower capacitor
        segment updated with the new dielectric constant is employed to
        update the 'wet' constant of the active or interface segment using
        the appropriate previously calculated 'wet' constant ratio". A stored
        ratio that tracks a wet constant as it moves.

        **This used to divide each position's wet-minus-dry span by the mean
        of the segments' spans**, which produced the right SHAPE -- one value
        far from 1.000, the rest near it -- for the wrong reason. The two
        reference positions are physically smaller than a segment: their
        spans are 35 and 155 counts against a segment's 558, so the second
        one came out near 0.26 on EVERY probe, for ever. The manual's
        outlier is a broken segment and this console's was arithmetic, and
        the one screen whose job is to make an anomaly stand out carried a
        permanent false one. See CLOSED X12 and UNKNOWNS A13c.

        The leading 0.000 is followed rather than derived: every example in
        the manual starts with it, on probes whose other positions are fine,
        so the first position's ratio is not a reading. What it means is not
        stated anywhere.
        """
        if self.probe_type(tank) == "MAG PROBE":
            return []
        factory = self.probe_calibration(tank, wet=True, updated=False)
        now = self.probe_calibration(tank, wet=True, updated=True)
        return [0.0] + [(n / f if f else 0.0)
                        for n, f in zip(now[1:], factory[1:])]

    # A10 to A13 are the same channels read through four windows. A Mag has
    # the nineteen `probe_channel` already models; a CAP has its own count,
    # and the manual's examples are the only statement of it: a CAP0 answers
    # ten and a CAP1 thirty-three.
    CAP_CHANNELS = {"CAP0": 10, "CAP1": 33}

    # "SSSS - Sample Number (Hex)", four digits, so it rolls at 65536. No
    # manual states the rate in words, but A54's Moving Average Table is a
    # real timestamped capture and measures it: its records are stamped
    # 960326132554, 960326132624, 960326132654 ... exactly thirty seconds
    # apart, and its SMPLS column -- "ss - Number of samples averaged into
    # this record" -- reads 31, 30, 31, 30, 31, 31. Thirty-odd samples in
    # thirty seconds is one a second. 576013-818 says the same twice: "This
    # report contains averaged probe data collected every 30 seconds" and
    # "there should be at least 7 and as many as 31".
    #
    # This was 2.5, inferred from A10 and A13 being read a minute apart with
    # sample numbers 24 apart. That inference does not survive: A10's note
    # calls SSSS a "Sample Number" where A13's calls it a "Number of
    # Samples", the two examples' CAP rows are copied verbatim from each
    # other, and read literally the same page pair gives three different
    # rates for its three probes. A54 is a capture; A10 to A13 are drawings.
    SAMPLE_SECONDS = 1.0

    def probe_sample_number(self, tank):
        """The running count A10 and A13 report, as the console has it now.

        Rolled at 65536, because "SSSS - Sample Number (Hex)" is four digits
        there. A15's own pair is eight -- `rrrrrrrr`, `uuuuuuuu` -- and does
        not roll with it; see `probe_samples_read`.
        """
        return self.probe_samples_read(tank) % 0x10000

    def probe_samples_read(self, tank):
        """The same count at A15's width, which is eight hex characters.

        Kept apart from the four digit form deliberately: SAMPLES USED is
        this minus what the console threw away, and a counter that rolls
        under a subtrahend that does not gives a probe which has used more
        samples than it read.
        """
        if tank not in self.programmed_tanks():
            return 0
        since = time.mktime(self.now()) - (self._commissioned
                                           or time.mktime(self.now()))
        return int(max(0.0, since) / self.SAMPLE_SECONDS)

    def probe_window(self, tank, which):
        """"NUMBER OF SAMPLES" for each of the four buffers.

        A10 and A13 report the running count; A11 and A12 report the width of
        the average they are. A12's is the one the Troubleshooting Guide
        already gave a number to -- "under normal operating conditions, this
        number should read 20" for a Mag -- and the manual's example agrees,
        20 against a Mag and 40 against a CAP, which is exactly the pair
        readings.MAG_SAMPLES and readings.CAP_SAMPLES already hold.
        """
        mag = self.probe_type(tank) == "MAG PROBE"
        if which == "fast":
            return 5
        if which == "standard":
            return readings.MAG_SAMPLES if mag else readings.CAP_SAMPLES
        return self.probe_sample_number(tank)

    def probe_buffer(self, tank, samples, longterm=False):
        """One buffer's worth of channel readings.

        The four reports read the SAME channels; what differs is how many
        samples have been averaged into them, and averaging shows in the
        numbers. A10 is one sample and prints whole counts; A11 and A12 are
        averages of five and twenty and print tenths and hundredths, which is
        what the manual's examples do -- 8587.000 against 8587.200 against
        8587.450 on the same channel.

        So the noise is divided by the root of the sample count, which is what
        averaging does to it, and a long term buffer additionally LAGS: A13's
        example reads 9687 where the live channel reads 8587, because it is
        still carrying a level the tank has since left.
        """
        # ONE lag for the whole buffer, and only on the channels that follow
        # the level. A13's example is the proof: its water channel reads
        # 695.555 where the live one reads 694, and its temperature channels
        # read 38259 and 31891 where live they read 38250 and 31771 -- all but
        # identical. Only C01 to C10 are far off, 9687 and 9960 against a live
        # 8587, because those are the product float and the level has moved
        # since. A long term average lags the thing that CHANGES and matches
        # the things that do not.
        lag = readings.fixed(1.06, 1.20, "lag", tank) if longterm else 1.0
        damp = 1.0 - 1.0 / max(1.0, float(samples) ** 0.5)
        out = []
        for n in range(self.probe_channels(tank)):
            live = self.probe_channel(tank, n)
            # pull the per-sample jitter back as the average widens, leaving
            # the reading itself where it was
            live -= readings.fixed(-0.4, 0.4, "chan", tank, n) * damp
            if longterm and 1 <= n <= 10:
                live *= lag
            out.append(live)
        return out

    def probe_channels(self, tank):
        """How many channels this probe answers with.

        Nineteen for a Mag, which is Table 9-3's own list and what
        probe_channel models. A CAP's count is the manual's examples and
        nothing else says it -- see UNKNOWNS.
        """
        if self.probe_type(tank) == "MAG PROBE":
            return 19
        return self.CAP_CHANNELS.get(self.probe_type_word(tank), 10)

    def probe_low_temp(self, tank):
        """A14's one flag, and A15's "oo - Probe Options".

        "00=Not Low Temperature Probe, 01=Low Temperature Probe". A low
        temperature Mag is a special order for cold climates and nothing in
        the setup data, the module tables or the part number lists here says
        which probe is one -- A14's own example answers NO on all four tanks.
        So this console has ordinary probes and says so.
        """
        return False

    def probe_initialised(self, tank):
        """"YYMMDDHHmm - Probe Initialized", A15.

        When this probe was first read, which is when the console was
        commissioned with it: a probe that has been swapped would carry a
        later date, and nothing here swaps probes.
        """
        when = self._commissioned or time.mktime(self.now())
        return time.strftime("%y%m%d%H%M", time.localtime(when))

    # How many of a failing probe's samples the console throws away. The one
    # number any document puts on this pair is the TRIAGE threshold --
    # 577014-348 step 2, "if there is a difference (>1%) between the 2
    # numbers, continue to Step 3 ... if there is little or no difference
    # (<1%) refer to the ATG troubleshooting guide" -- so the band is chosen
    # to land unambiguously on the >1% side of it rather than to model a
    # failure mode nobody wrote down. See UNKNOWNS A29.
    DROPPED_BAND = (0.04, 0.18)

    def sample_tick(self, now):
        """Count the samples a bad probe lost, one tick at a time.

        A probe reads one sample a second (`SAMPLE_SECONDS`, measured off
        A54's own timestamps), and a probe the bench has unplugged is
        reporting nothing the console can use. The count is kept rather than
        derived because the guide reads it as a HISTORY: a probe that has
        been out and is back reads normally NOW and its two counters still
        differ, which is the whole of what step 2 asks a technician to look
        at.
        """
        was, self._last_sample = getattr(self, "_last_sample", None), now
        if was is None or now <= was:
            return
        seconds = (now - was) / self.SAMPLE_SECONDS
        for tank in sorted(self.probe_out):
            share = readings.fixed(*self.DROPPED_BAND, "dropped", tank)
            lost = seconds * share
            if lost <= 0:
                continue
            self.probe_dropped[tank] = self.probe_dropped.get(tank, 0.0) + lost
            self.probe_last_error[tank] = (self.probe_samples_read(tank),
                                           now)

    def probe_sample_health(self, tank):
        """A15's (samples read, samples used, last error number, error time).

        They are running counts, not the size of the average. "rrrrrrrr -
        Samples Read (Hex)" and "uuuuuuuu - Samples Used (Hex)" are eight hex
        characters each, and the manual's own example is a probe that read 2
        and used 2 -- a console two seconds old, not a twenty sample window.
        This returned `probe_window(tank, "standard")` for both, which is
        A12's NUMBER OF SAMPLES, a different quantity that happens to be a
        number.

        Returning the same number twice also disarmed the one troubleshooting
        step that reads the pair. 577014-348: "check samples_read /
        sample_used ... if there is a difference (>1%) between the 2 numbers,
        continue to Step 3. If there is little or no difference (<1%) ...
        refer to the ATG troubleshooting guide." The difference was
        structurally zero, including for a probe the bench had unplugged, so
        the document's primary triage always took branch b and taught the
        opposite of what it is for. See FIDELITY R15.

        "eeeeeeee - Last Error Sample Number (Hex)" is WHICH sample failed
        last, not a code: zero on a probe that has never dropped one, which
        is what the manual's example prints.
        """
        read = self.probe_samples_read(tank)
        used = max(0, read - int(self.probe_dropped.get(tank, 0.0)))
        number, when = self.probe_last_error.get(tank, (0, None))
        return (read, used, number,
                time.strftime("%y%m%d%H%M", time.localtime(when))
                if when else self.probe_initialised(tank))

    def probe_temperatures(self, tank):
        """A15's "TEMP SENSOR DATA", T6 down to T1, in Fahrenheit.

        Six thermistors up the probe, which are channels C12 to C17, read as
        temperatures rather than as counts. The product is warmest at the
        bottom where it has been longest out of the weather and coolest at the
        top, which is the order the manual prints them in and the spread it
        shows: 72.6 at T6 down to 67.6 at T1, five degrees over the six.
        """
        return self.thermistor_ladder(tank)

    def thermistor_counts(self, tank):
        """C12 to C17 as the raw counts they are, bottom of the probe first.

        The measurement noise sums to zero across the six on purpose. Table
        29-4 does not describe the average as a reading beside the channels,
        it describes it as the average OF them -- "average temperature of all
        submerged thermistors" -- so a console whose six counts do not average
        to the temperature it reports is contradicting itself in a place a
        technician can see. Per-channel noise with the mean taken out is a
        probe whose channels disagree with each other and not with itself.
        """
        temps = self.probe_temperatures(tank)
        noise = [readings.fixed(-1.8, 1.8, "therm", tank, n)
                 for n in range(12, 18)]
        # The mean is taken out over the SUBMERGED thermistors and not over
        # all six, because those are the ones Table 29-4 averages: a console
        # whose submerged counts do not average to the temperature it reports
        # is contradicting itself in a place a technician can see, and taking
        # the mean out over all six leaves a residue in the subset. FIDELITY
        # Y5.
        under = self.submerged_thermistors(tank) or list(range(len(noise)))
        middle = sum(noise[n] for n in under) / len(under)
        return [17000.0 + (warm - 48.0) / 14.0 * 6000.0
                + (jitter - middle) * 90.0
                for warm, jitter in zip(temps, noise)]

    def probe_top_temperature(self, tank, at=None):
        """The topmost thermistor, which is what CSLD's tables call TOPTEMP.

        `probe_temperatures` runs T6 to T1, bottom to top, so the top of the
        product is the last of them -- and it is the cooler end, which is
        what IA54's own sample shows: `AVGTEMP 45.86  TOPTEMP 45.49`.
        """
        warm = self.product_temperature(tank, at=at)
        spread = readings.fixed(3.0, 6.0, "tspread", tank)
        return warm + spread * (0.5 - 5 / 5.0)

    def probe_board_temperature(self, tank, at=None):
        """The probe's electronics board, CSLD's BDTMP.

        Not an offset from the product: the board sits up the riser where it
        sees the ground rather than the fuel, so it can be either side of
        it, and the manual's two samples are one of each -- IA51's January
        rows read `AVTMP 36.9 ... BDTMP 33.3` and IA54's March afternoon
        reads `AVGTEMP 45.86 ... BDTEMP 48.19`. Modelled as its own slow
        reading rather than as product plus a constant, so it can cross.
        """
        return readings.wander(self, 30.0, 75.0, "csldboard", tank,
                               swing=0.35, period=6.0 * 3600.0, at=at)

    def uncovered_area(self, tank):
        """Square feet of tank wall standing above the fuel, which is CSLD's
        ULLG column.

        576013-818 chapter 11 defines it and it is not what the word means
        anywhere else on this console: "ULLG -- Amount of surface area of the
        tank that is not covered by fluid." Every other report's ullage is a
        VOLUME.

        A horizontal cylinder's wetted arc subtends `theta` at the axis, so
        what is left dry is `(2*pi - theta) * r * length`, and it grows as
        the tank empties -- which is the direction both manuals' samples run:
        chapter 11's `VOL 4281 ... ULLG 168` against `VOL 3557 ... ULLG 204`,
        and the serial manual's `VOL 9324 ... ULLG 188` against
        `VOL 6829 ... ULLG 320`.

        The MAGNITUDE is unverified: neither sample says what tank it is,
        so there is nothing to check the scale against -- only the shape.
        See FIDELITY K1a.
        """
        diameter = (self.limit("607", tank) or 96.0) / 12.0     # feet
        full = self.full_volume(tank) or 0.0
        if diameter <= 0 or full <= 0:
            return 0.0
        radius = diameter / 2.0
        # gallons to cubic feet, then the length that capacity implies
        length = (full / 7.48052) / (math.pi * radius ** 2)
        volume = self.tank_level.get(tank, {}).get("volume", 0.0)
        depth = max(0.0, min(diameter, self.height_at(tank, volume) / 12.0))
        wetted = 2.0 * math.acos(max(-1.0, min(1.0,
                                               (radius - depth) / radius)))
        return (2.0 * math.pi - wetted) * radius * length

    def probe_leak_flags(self, tank, which):
        """A20, A21 and A22's "Flag sequence characters".

        The manuals print the headings and never the vocabulary: "FFFF - Flag
        sequence characters indicating which Flag bits are set", and every
        example in all three reports shows the heading with NOTHING after it.
        A probe with nothing wrong with it sets no flags, so that is what a
        console with nothing wrong with it answers. See UNKNOWNS.

        `which` is "present", "stored" or "gross", which decides the headings
        rather than the flags: A20 and A21 report per rate and A22 reports one
        set for the gross test.
        """
        return {rate: [] for rate in self.probe_leak_rates(tank, which)}

    def probe_leak_rates(self, tank, which):
        """Which rates a probe is tested at, for A20 and A21's headings.

        A20's example heads a Mag and a CAP1 with both "0.1 GAL/HR FLAGS:" and
        "0.2 GAL/HR FLAGS:" and heads the CAP0 with 0.2 alone.
        """
        if which == "gross":
            return ["gross"]
        return ["0.2"] if self.probe_type_word(tank) == "CAP0"             else ["0.1", "0.2"]

    # A23's two buffers, and what the manual heads them.
    LEAK_BUFFERS = (("periodic", "0.20"), ("annual", "0.10"))

    def probe_leak_buffer(self, tank, rate_key, most=5):
        """A23: the finished tests in one rate's averaging buffer, newest first.

        The console already keeps every finished test rather than the latest
        one, for the history reports, so this is that log filtered to a rate
        and cut to what the buffer holds. The manual's example shows five rows
        under 0.20 and four under 0.10, so they do not have to be the same
        depth: a rate that has run fewer times has fewer.
        """
        log = self.leaks.history.get(("tank", tank)) or []
        got = [r for r in reversed(log) if r.rate_key == rate_key]
        return got[:most]

    def probe_reference_distance(self, tank):
        """A07: ((YYMMDD, inches) commissioned, (YYMMDD, inches) now).

        "Probe types 01=CAP0 and 02=CAP1 are not supported by this command",
        so this is a Mag probe's diagnostic and nobody else's, and it arrived
        at software 23.

        A Mag probe reads position against a reference target a fixed way up
        the tube. The distance to it should not change, so the point of the
        screen is the pair: what it read when it went in against what it
        reads today, and a probe that has moved or worn shows the difference.
        """
        if self.probe_type(tank) != "MAG PROBE":
            return None
        length = self.probe_length(tank)
        original = length - readings.fixed(2.0, 6.0, "refdist", tank)
        drift = readings.fixed(-0.09, 0.09, "refdrift", tank)
        born = time.localtime(time.mktime(self.now())
                              - readings.fixed(400, 2200, "refage", tank) * 86400)
        return ((time.strftime("%y%m%d", born), original),
                (time.strftime("%y%m%d", self.now()), original + drift))

    def start_line_test(self, kind, number):
        """Start the 3.0 gph test on the line the panel is pointed at.

        "Tests always run in the order: 3.0 gph, 0.2 gph, and 0.1 gph", so
        the test a technician starts by hand off the first diagnostic screen
        is the Gross one, and the screen he started it from is the screen
        that shows it running: the pressure on it is the test's own P1 and P2
        going by.

        A position the console has not been programmed with has nothing to
        test, the same rule programmed_lines() applies everywhere else.
        """
        number = int(number)
        if not any(k == kind and n == number
                   for k, n, _label in self.programmed_lines()):
            return "LINE NOT PROGRAMMED"
        # Pressing it again starts it again. There is no "already running" to
        # argue with: the key means run the test, and a technician who presses
        # it twice wants the second one, not a refusal.
        if self.lines.line(kind, number).running():
            self.lines.stop(kind, number)
        return self.lines.start(kind, number, "gross")

    def line_diag(self, token, device, kind):
        """Two lines of a PLLD or WPLLD diagnostic, for the line the panel is on.

        Both lines come from here, separated by a newline, because on these
        screens the top line is a reading too: "Q 1: XX.XXX PSI PUMP OFF" over
        "TEST COMPLETE HANDLE OFF" is a pressure and two switch states, not a
        label and a value.
        """
        ln = self.lines.line(kind, device)
        letter = self.lines.code(kind)
        if token == "line_pressure":
            return chr(10).join(ln.screen())
        if token == "line_counts":
            # "This display shows the A/D converter readings for pressure
            # sensor counts (SNS CNTS), low reference counts (LO), and high
            # reference counts (HI). SNS CNTS should always be in between the
            # LO and HI reference counts. Also the HI counts should always be
            # less than the LO counts."
            lo, hi, counts = ln.sensor_counts()
            return (f"{letter} {device}: SNS CNTS {counts:8.1f}" + chr(10)
                    + f"LO {lo:8.2f} HI {hi:8.2f}")
        if token == "line_switches":
            # "W 1: P0 H0  S: PENDING" over the two pressures, and p.29
            # says what each is: "P0 = pump off, P1 = pump on, H0 = handle
            # off, H1 = handle on, S = WPLLD Comm Module status message".
            # P was whether the line stood above 12 psi and S was the TEST
            # status. FIDELITY U14.
            return (f"{letter} {device}: P{int(ln.pump)} "
                    f"H{int(ln.handle)}  S: {ln.comm_status()[:9]}" + chr(10)
                    + ln.pressures())
        which = {"line_leg_gross": "gross", "line_leg_periodic": "periodic",
                 "line_leg_mid": "mid"}[token]
        # 577013-344 Rev H Figure 20 sets the timer against the right of the
        # display and keeps its digits in one column whichever label is in
        # front, which is why "3.0 GPH" carries a space inside its bracket
        # and the longer two do not.
        head = f"{letter} {device}: {ln.leg_name(which)}"
        clock = ln.leg_clock(which)
        return (f"{head}{clock.rjust(24 - len(head))}"
                + chr(10) + ln.pressures(which))

    # ---- the tank profile ---------------------------------------------------
    # Which function holds a tank's volumes IS its profile: one point in 604,
    # four in 605, twenty in 606, a linear calculated volume in 60A, fifty in
    # 63B/63C. Nothing sets a profile over the wire, I217 reports which one
    # a tank is on, and this is where that answer comes from.
    PROFILE_CODE = {"00": "604", "01": "605", "02": "606", "03": "60A",
                    "04": "63C"}
    # 604 and 60A are two NAMES for one number, not two numbers. "Set Tank 1
    # Point Full Height Volume" and "Set Tank Linear Calculated Full Volume"
    # carry byte-identical payloads, the panel's FULL VOL step writes
    # whichever the profile chose, and 576013-818 Rev AB p.12-32 is a real
    # console answering `I60400` with the same 10000, 6000 and 8000 that
    # `I60A00` has just reported for the same three LINEAR tanks. So a tank
    # is not "programmed" on one of them and unprogrammed on the other: both
    # report the one full volume, whatever the profile.
    FULL_VOLUME_CODES = ("604", "60A")
    # And the same relation, six more times over, between a console-wide
    # setting and the per-tank one that replaced it. 506 to 50B are "Set
    # Periodic Test Needed Warning" and its five companions at Version 2 and
    # 4; 546 to 54B are the same six as "Set TANK Periodic Test Needed
    # Warning" at Version 15. A console that has both answers both with ONE
    # value, and `tests/console_capture/raw/` is a real one doing it:
    # `I50700` and `I54700` both print 25, `I50800` and `I54800` both print
    # 30, on the same console in the same minute. Only the titles differ --
    # `PERIODIC TEST WARNING:` against `TANK PER TST NEEDED WRN:`.
    #
    # The panel writes the Version 15 half, so that is where the value
    # lives; the older six had no field at all and answered a bare stamp.
    # See FIDELITY S17.
    SHARED_CODES = (FULL_VOLUME_CODES,
                    ("506", "546"), ("507", "547"), ("508", "548"),
                    ("509", "549"), ("50A", "54A"), ("50B", "54B"))
    #: {code: the other code that holds the same value}
    SHARED_STORE = {a: b for pair in SHARED_CODES for a, b in
                    (pair, pair[::-1])}
    # what the screen reads: 576013-623 Rev AN draws "TANK PROFILE 1PT" on
    # p.96 with no space, and "TANK PROFILE    : 50 PTS" on p.100 -- the
    # multi-point ones are plural and the one-point one is not spaced.
    PROFILE_NAME = {"00": "1PT", "01": "4 PTS", "02": "20 PTS",
                    "03": "LINEAR", "04": "50 PTS"}
    # what a REPORT reads, which is not what the screen reads. The one point
    # profile is `1PT` on the panel and `1 PT` on paper: 576013-635 Rev AA
    # p.81's `I217` sample and p.253's `I60A` sample both print `1 PT`, and
    # so does 576013-818 Rev AB p.12-31's dump off a real console. The panel
    # has 24 columns to fit a label and a value into and the report does not.
    PROFILE_REPORT_NAME = dict(PROFILE_NAME, **{"00": "1 PT"})

    def profiles(self):
        """The profiles CHANGE walks on this console.

        The fifty point chart came with tank chart security; software older
        than that offers the four it has.
        """
        return [p for p in sorted(self.PROFILE_NAME)
                if self.supports(versions.PROFILE_FEATURE.get(p))]

    def tank_profile(self, tank):
        """"00" 1 point, "01" 4, "02" 20, "03" linear, "04" 50.

        The panel's own selection first, because 1PT and LINEAR store the
        same thing -- one full volume -- and nothing about the STORAGE can
        tell them apart. That is not this simulator's difficulty, it is the
        console's: 576013-818 Rev AB p.12-31 says so in as many words, over a
        real console's dump, "The only way to determine that the profile is
        set to linear is to run the 60A command."

        `S60A` used to be read as LINEAR and it is not. 60A is Set Tank
        Linear Calculated Full Volume, and its payload is byte for byte 604's;
        the manual's own `I60A` sample on p.253 answers for a 1 PT tank,
        and the TSG's dump has `I604` and `I60A` returning the same three
        volumes for the same three LINEAR tanks. Neither code's presence says
        anything about the profile, and half the fixtures in this repository
        set `S60A` only to give a tank a capacity.

        The multi-point tables are still read as the profiles they are: a
        tank with four volumes in 605 is on the four point profile, and there
        is nowhere else those volumes could have come from. That is the
        fallback for a console programmed over the wire, or restored from a
        backup written before the selection was kept.
        """
        chosen = self.tank_profiles.get(tank)
        if chosen in self.PROFILE_NAME:
            return chosen
        if self.values.get(f"S63C{tank:02d}") or self.values.get(
                f"S63B{tank:02d}"):
            return "04"
        for code, profile in (("606", "02"), ("605", "01")):
            if self.values.get(f"S{code}{tank:02d}"):
                return profile
        return "00"

    def set_tank_profile(self, tank, profile):
        """Move the tank onto another profile.

        "Changing profile selection will erase the previously entered 50 point
        profile!": and the same is true of the other four, since the console
        keeps one set of volumes per tank, not five.

        The SELECTION is remembered in its own right now. It used to be
        remembered by which function held the volumes, and that cannot tell
        1PT from LINEAR: both keep one full volume and nothing else. See
        `tank_profile`.
        """
        keep = self.PROFILE_CODE.get(profile, "604")
        full = self.full_volume(tank, default=None)
        erased = 0
        for code in list(self.PROFILE_CODE.values()) + ["63B"]:
            if code == keep:
                continue
            if self.values.pop(f"S{code}{tank:02d}", None) is not None:
                erased += 1
        # the chosen function is always written, as 0 until it is entered,
        # which is what the console shows: "FULL VOL: 000000"
        body = packed.hexfloat(full if full is not None else 0.0)
        self.values[f"S{keep}{tank:02d}"] = f"{tank:02d}{body}"
        self.tank_profiles[tank] = profile
        self.save()
        return erased

    # ---- tank chart security ------------------------------------------------
    def chart_secured(self):
        """"All zeros disables Tank Chart Security": so does no code."""
        return bool(self.chart_code) and self.chart_code != "000000"

    def set_chart_code(self, code):
        """The passcode, and the date the audit trail reports it changed."""
        self.chart_code = (code or "").strip()
        self.chart_code_set = time.strftime("%y%m%d%H%M", self.now())
        self.save()
        return self.chart_secured()

    def record_chart_change(self, tank):
        """"the times of the last 10 tank chart modifications, most recent
        first": which is what the audit trail is for."""
        stamps = self.chart_audit.setdefault(tank, [])
        stamps.insert(0, time.strftime("%y%m%d%H%M", self.now()))
        del stamps[10:]

    def chart_report(self, tank):
        """I63B: the chart, with the W&M block when it is secured."""
        label = self.text("602", tank) or f"TANK {tank}"
        out = ["TANK 50 POINT HEIGHTS AND VOLUMES", "", f"T {tank}: {label}",
               ""]
        capacity = self.tank_capacity.get(tank) or self.full_volume(tank)
        if self.chart_secured():
            out.append(f"TANK CAPACITY : {capacity:.0f}")
            out.append("CONSOLE SERIAL NUMBER:")
            out.append(self.serial_number or "")
            out.append(f"PROBE S/N             : {self.probe_serial(tank)}")
            out.append("WEIGHTS AND MEASURES:")
            out.append(self.wm_office or "")
            out.append("")
        diam = self.limit("607", tank) or 0.0
        # p.302: DIAMETER at 6 and FULL VOLUME at 17 over figures held
        # right against 12 and 27, then PAIR at 0, HEIGHT at 7 and VOLUME at
        # 22 over figures right against 3, 12 and 27
        out.append("      DIAMETER   FULL VOLUME")
        out.append(f"{diam:13.2f}{self.full_volume(tank):15.0f}")
        out.append("")
        out.append("PAIR   HEIGHT         VOLUME")
        for i, (height, volume) in enumerate(self.chart_points(tank), start=1):
            out.append(f"{i:4d}{height:9.2f}{volume:15.0f}")
        return chr(10).join(out)

    def audit_report(self, tank):
        """I218: who the chart belongs to, and when it was last touched."""
        label = self.text("602", tank) or f"TANK {tank}"
        capacity = self.tank_capacity.get(tank) or self.full_volume(tank)
        # 576013-635 Rev AA p.86's own columns: both colons at 16, the
        # capacity held right against 22 and the probe serial left at 18, and
        # the two long values on the line BELOW their label, indented one.
        out = ["TANK CHART AUDIT TRAIL", f"T {tank}: {label}",
               f"{'TANK CAPACITY':<16s}: {capacity:5.0f}",
               "CONSOLE SERIAL NUMBER:",
               " " + (self.serial_number or ""),
               f"{'PROBE S/N':<16s}: {self.probe_serial(tank)}",
               "WEIGHTS AND MEASURES:", " " + (self.wm_office or ""),
               "DATE/TIME"]
        for stamp in self.chart_audit.get(tank, []):
            when = time.strptime(stamp, "%y%m%d%H%M")
            out.append(clock_words(when))
        return chr(10).join(out)

    def probe_serial(self, tank):
        """A probe's serial number, which does not change between reboots."""
        if tank not in self.programmed_tanks():
            return "000000"
        return f"{zlib.crc32(f'probe{tank}'.encode()) % 1000000:06d}"

    def chart_points(self, tank):
        """[(height, volume)] of the 50 point chart, as S63B holds them."""
        raw = self.values.get(f"S63B{tank:02d}") or ""
        # "nn" then eighteen characters a pair, with or without the device
        # prefix depending on whether the panel or a tool wrote it
        body = raw[2:] if len(raw) % 18 == 4 else raw
        if len(body) < 2:
            return []
        out = []
        rest = body[2:]
        while len(rest) >= 18:
            flag, height, volume = rest[:2], rest[2:10], rest[10:18]
            rest = rest[18:]
            if flag != "01":
                continue
            try:
                out.append((packed.unhexfloat(height),
                            packed.unhexfloat(volume)))
            except ValueError:
                continue
        return out

    # "Tank 50 Point Heights, Volumes and Slope Report", and it prints 49
    # of them: 576013-635 Rev AA p.80's sample runs PAIR 1 at 9800 gallons
    # down to PAIR 49 at 200, in steps of 200 on a 10,000 gallon tank. The
    # fiftieth pair would be the empty tank, and the full one is the header.
    CHART_POINTS = 50

    def chart_report_rows(self, tank):
        """[(height, volume, slope)] -- the 49 rows I216 prints.

        That sample is fully derivable and every figure in it closes. The
        header slope is FULL VOLUME over DIAMETER, 10000/96 = 104.17. The
        volumes step by full/50. Each height is what the tank's own chart
        says that volume stands at, and each row's slope is the LOCAL
        gallons per inch -- the gallons between this pair and the next over
        the inches between them, which on the sample's linear tank is
        104.17 on every row and on a cylinder is not. That is what a slope
        COLUMN is for; a constant would not need one.

        A tank with a strapped 50 point chart prints that chart. A tank
        profiled any other way still has a chart -- `height_at` computes one
        -- and this printed the heading and stopped, because it read
        `chart_points`, which holds the strapped chart alone and is empty on
        every tank nobody has strapped. See FIDELITY X6.
        """
        full = self.full_volume(tank) or 0.0
        if not full:
            return []
        pairs = sorted(self.chart_points(tank), reverse=True)
        if pairs:
            pairs = pairs[:self.CHART_POINTS - 1]
        else:
            step = full / self.CHART_POINTS
            pairs = [(self.height_at(tank, full - step * n), full - step * n)
                     for n in range(1, self.CHART_POINTS)]
        out = []
        for n, (height, volume) in enumerate(pairs):
            # the pair below this one, and below the last is the empty tank
            below = pairs[n + 1] if n + 1 < len(pairs) else (0.0, 0.0)
            rise = height - below[0]
            out.append((height, volume,
                        (volume - below[1]) / rise if rise else 0.0))
        return out

    def add_chart_point(self, tank, height, volume):
        """One strapped height/volume pair, appended as 63B stores them."""
        points = self.chart_points(tank)
        points.append((float(height), float(volume)))
        points = sorted(points, reverse=True)[:50]
        body = f"{len(points):02d}" + "".join(
            "01" + packed.hexfloat(h)
            + packed.hexfloat(v) for h, v in points)
        self.values[f"S63B{tank:02d}"] = f"{tank:02d}{body}"
        self.record_chart_change(tank)
        self.save()
        return len(points)

    def profile_erasable(self, tank, profile):
        """How many stored volumes changing to this profile would throw away."""
        keep = self.PROFILE_CODE.get(profile, "604")
        return sum(1 for code in list(self.PROFILE_CODE.values()) + ["63B"]
                   if code != keep and self.values.get(f"S{code}{tank:02d}"))

    def full_volume(self, tank, default=10000.0):
        """The tank's full volume, whichever profile is holding it.

        Every profile's first value is the volume at 100% height, so this
        reads the one the tank is actually on.
        """
        code = self.PROFILE_CODE[self.tank_profile(tank)]
        return (self.limit(code, tank) or self.limit("60A", tank)
                or self.limit("604", tank) or default)

    def set_module(self, key, on):
        """Fit or pull cards. `on` is how many, or True/False for one/none."""
        count = int(on) if not isinstance(on, bool) else (1 if on else 0)
        if not self.fits(key, count):
            return False
        self.modules[key] = count
        if not count:
            # Pulling a card takes its devices with it, exactly as removing the
            # hardware would. Programming for a module that is not there cannot
            # be reached, so leaving it live would be a lie.
            if key == "probe":
                self.tank_level.clear()
            for k in [k for k in self.sensor_state if k[0] == key]:
                self.sensor_state.pop(k, None)
        self.save()
        return True

    # ---- stored values -----------------------------------------------------
    def limit(self, tok, dev):
        """A per-device setting as a number, whichever way it is stored.

        The device prefix comes off because the code is prefixed, not
        because the string is long. It used to come off at `len(raw) > 8`,
        which is true of every packed float -- `01` and eight hex digits --
        and false of a decimal one: `S77401` holding a 30 hour timeout is
        `0130`, and this read it as 130. A `ppp.pp` value is exactly eight
        with its prefix on, so 776 programmed to 20.00 PSI came back as
        1020. `aggregate()` a few lines down already asked `is_prefixed`;
        this asked the length. See FIDELITY R17.
        """
        raw = self.values.get(f"S{tok}{dev:02d}")
        if not raw:
            return None
        body = raw[2:] if self.is_prefixed(tok) and len(raw) > 2 else raw
        try:
            value = packed.unhexfloat(body[-8:])
        except ValueError:
            try:
                value = float(body)
            except ValueError:
                return None
        # A stored value that is not a finite number is not a limit. The
        # fallback above is what turned the text "nan" into `float('nan')`,
        # and from there into an `int()` that raises inside a report -- so
        # the guard belongs here, at the one door every caller comes
        # through, as well as at the Set that should never have stored it.
        # Unset is the honest answer: the field holds nothing usable.
        return value if math.isfinite(value) else None

    def limit_or_default(self, tok, dev):
        """`limit`, or the field's own default where nothing is programmed.

        A default reaches the glass, the paper and the wire through the
        field, and `limit` reads only what was stored -- so a tank nobody
        had given a Leak Alarm Limit reported `99` on I626 and enforced
        nothing during a test. A console out of the box has the limit it
        reports. See FIDELITY S18.
        """
        value = self.limit(tok, dev)
        if value is not None:
            return value
        default = (FIELDS.get(f"S{tok}01") or {}).get("default")
        try:
            return float(default) if default else None
        except ValueError:
            return None

    # The five in-tank limits 576013-623 Rev AN says to enter as a
    # PERCENT: "Press CHANGE. Enter the percent limit." Their panel screens
    # mask them "000%", three digits and a sign, and this console was
    # storing gallons in them -- so a 20,000 gallon tank drew
    # "HIGH PRODUCT: 18000%" and compared 18000 against a volume as though
    # the number meant gallons. It reads right only because both sides were
    # wrong together. The tape prints the percent AND the gallons it works
    # out to, which is what a percent field looks like on paper.
    PERCENT_LIMITS = {"622", "623", "629", "62A", "636"}

    def limit_volume(self, tok, dev):
        """A limit in gallons, whatever unit the field holds it in.

        The percent ones are a percent of MAX OR LABEL VOLUME -- the tape
        heads the row "% MAX" and prints 9495 gallons against 95.0% of a
        9995 gallon label -- falling back to the tank's full volume where
        no label volume is programmed.
        """
        value = self.limit(tok, dev)
        if value is None or tok not in self.PERCENT_LIMITS:
            return value
        base = self.limit("628", dev) or self.limit("604", dev) or 0.0
        return base * value / 100.0

    def delivery_delay(self, tank):
        """S610's "delay time between the completion of a bulk delivery and
        the Delivery Increase Report", in minutes.

        The SETTING, which is what the panel walks and the wire echoes. What
        the console actually waits is `settling_delay`, because the setting
        is not the only thing in the way.
        """
        raw = (self.values.get(f"S610{tank:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return int(body) if body.isdigit() else 1

    # "There will be a delay of at least four minutes between the end of the
    # delivery and the printing of the report while the console waits for the
    # fuel level in the tank to stabilize." 576013-939 Rev F p.3.
    SETTLING_MINUTES = 4

    def settling_delay(self, tank):
        """How long the console really waits before it calls a drop finished.

        Two things stand between the last gallon and the report and this
        console had only one of them. S610 is the programmable part, and its
        own screen says what it is for: "this feature prevents generation of
        false reports during the intervals between multi-compartment drops to
        one tank". The four minutes underneath it are physics -- the fuel is
        still moving -- and no setting turns them off. With DELIVERY DELAY:
        01 the report was written at exactly one minute, three minutes early.
        See FIDELITY Y3.
        """
        return max(self.delivery_delay(tank), self.SETTLING_MINUTES)

    def stored(self, code):
        """What a setup code holds, from wherever this console keeps it.

        Nearly every code lives in `values`. 52B does not: a receiver's auto
        dial type and start time is `receiver_dial`, which the wire's Set
        writes and the wire's reports and the setup printout read -- while
        the panel's AUTO-DIAL FREQUENCY screen wrote `values["S52B01"]`,
        which nothing but that screen read. A frequency set on the glass
        never reached the port or the paper, and one set over the port left
        the screen blank. F9's split store, on the one list field with a
        store of its own. See FIDELITY S25.
        """
        code = (code or "").upper()
        if code.startswith("S52B") and code[4:6].isdigit():
            body = self.receiver_dial.get(int(code[4:6]))
            return None if body is None else code[4:6] + body
        return self.values.get(code)

    def store(self, code, data):
        """Put what the panel encoded where `stored` will read it back.

        The panel encodes a device-prefixed code with its device in front,
        `01` then 52B's own `50630`; `receiver_dial` holds the body alone,
        which is what the wire's Set puts there.
        """
        code = (code or "").upper()
        if code.startswith("S52B") and code[4:6].isdigit():
            rr = code[4:6]
            self.receiver_dial[int(rr)] = data[2:] if data[:2] == rr else data
            return
        self.values[code] = data

    def text(self, tok, dev):
        """A stored label, with the device prefix off it if it is there.

        The panel stores the prefix the inquire response echoes; a tool's Set
        carries the device in the command header instead, so the value it
        writes has none. Both turn up in a console that has been programmed
        from the panel AND over the wire.
        """
        raw = self.values.get(f"S{tok}{dev:02d}")
        if not raw:
            return ""
        if self.is_prefixed(tok) and raw.startswith(f"{dev:02d}")                 and len(raw) > 2:
            return raw[2:].strip()
        return raw.strip()

    def is_prefixed(self, tok):
        try:
            return int(tok, 16) in DEVICE_PREFIXED
        except ValueError:
            return False

    def is_multi(self, tok):
        try:
            return int(tok, 16) in MULTI_DEVICE
        except ValueError:
            return False

    def aggregate(self, tok):
        out = []
        for dev in range(1, 17):
            val = self.values.get(f"S{tok}{dev:02d}")
            if val is None:
                continue
            body = val[2:] if self.is_prefixed(tok) and len(val) > 2 else val
            out.append(f"{dev:02d}{body}" if self.is_prefixed(tok) else body)
        return "".join(out)

    # ---- alarms ------------------------------------------------------------
    def configured(self, code, count=None):
        """The device numbers switched on at that function's config screen.

        "As you specify which positions on a module are connected to probes,
        the system establishes a number for each probe that corresponds to the
        probe's position on the module." A position left as X is not a device.
        """
        count = count or SLOT_POSITIONS.get(code, 8)
        return [n for n in range(1, count + 1) if self._configured(code, n)]

    def programmed_tanks(self):
        if not self.has("probe"):
            return {}
        out = {}
        for n in range(1, max(self.capacity("probe"), 1) + 1):
            label = self.text("602", n)
            full = self.limit("60A", n) or self.limit("604", n)
            if self._configured("601", n) or label or full:
                out[n] = (label or f"TANK {n}", full or 10000.0)
        return out

    # each sensor module's config screen and its label function
    SENSOR_CODES = {"liquid": ("701", "702"), "vapor": ("706", "707"),
                    "gw": ("711", "712"), "2wire": ("741", "742"),
                    "3wire": ("746", "747"), "smart": ("721", "722"),
                    "universal": ("74B", "74C")}

    def programmed_sensors(self):
        """[(module, number, label)] for every sensor the console has.

        A sensor exists once its position is switched on at SENSOR CONFIG,
        or once it has been given a location label, so a site seeded from a
        backup still shows up.
        """
        out = []
        for key, (config, label_code) in self.SENSOR_CODES.items():
            if not self.has(key):
                continue
            for n in range(1, self.capacity(key) + 1):
                label = self.text(label_code, n)
                if not (self._configured(config, n) or label):
                    continue
                out.append((key, n, label
                            or f"{MODULE_LABEL[key].split()[0].upper()} {n}"))
        return out

    # Every config screen in the console, and the function that holds the
    # label of the device it switches on. A device EXISTS once its position
    # is on at its config screen, or once somebody has labelled it.
    CONFIG_LABEL = {"601": "602", "701": "702", "706": "707", "711": "712",
                    "721": "722", "741": "742", "746": "747", "751": "760",
                    "74B": "74C",
                    "781": "782", "7A1": "7A2", "771": None, "7C4": "7C5",
                    "801": "802", "806": "807"}

    def configured_devices(self, code, limit):
        """[n] for the positions of that config screen that are switched on.

        The Operating and Diagnostic modes show "only the Functions/Steps
        relevant to your console and its installed options and CONNECTED
        detection systems": a sensor position nobody wired up is not a
        connected detection system, and reporting NORMAL for it is reporting
        on something that is not there. Setup Mode is the other way round,
        you have to be able to walk onto a position to switch it on, so this
        is not used there.
        """
        label_code = self.CONFIG_LABEL.get(code)
        out = [n for n in range(1, max(int(limit), 1) + 1)
               if self._configured(code, n)
               or (label_code and self.text(label_code, n))]
        return out

    # each line leak module's config screen and its label function
    LINE_CODES = {"plld": ("781", "782"), "wplld": ("7A1", "7A2"),
                  "vlld": ("751", "760")}

    # "press CHANGE until the correct pump's control device displays [QX
    # (PLLD), WX (WPLLD), or RX (Output Relay)]. NOTE: an Output Relay must
    # be set to Pump Control Output to be assigned as a pump" --
    # 576013-623 Rev AN p.26-4, the Vac Sensor's source of vacuum. 729
    # stores the manual's own pair: "AA - Device Type (Decimal), 26=WPLLD,
    # 21=PLLD, 11=Output Relay, 00=None" over "TT - Device Number", and
    # its sample draws the choice as `Q 1:UNLEADED REGULAR`.
    PUMP_DEVICES = (("plld", "Q", "21"), ("wplld", "W", "26"))
    PUMP_CONTROL_RELAY = "2"        # S80A's "PUMP CONTROL OUTPUT"

    def field_choices(self, which):
        """A choice list this SITE makes, rather than one a manual fixes.

        Every other enum in the field table is the manual's own list. This
        one is the console's own devices, which is why it cannot live in
        `consoledata.json`. See FIDELITY M3.
        """
        if which != "pumps":
            return []
        out = [("0000", "NONE")]
        for kind, letter, aa in self.PUMP_DEVICES:
            for one, number, label in self.programmed_lines():
                if one == kind:
                    out.append((f"{aa}{number:02d}",
                                f"{letter}{number:2d}:{label}"))
        for number in self.configured_devices("806", self.capacity("relay")):
            if (self.values.get(f"S80A{number:02d}") or "").strip()[-1:] \
                    != self.PUMP_CONTROL_RELAY:
                continue
            label = self.text("807", number) or f"RELAY {number}"
            out.append((f"11{number:02d}", f"R{number:2d}:{label}"))
        return out

    def programmed_lines(self):
        """[(kind, number, label)] for every line the console has.

        Same rule as the sensors: a line exists once its position is switched
        on at LINE CONFIG, or once it has been given a label. A card in the
        cage is wires, not lines; four unprogrammed PLLD positions are four
        pieces of pipe nobody has told the console about, and the console
        neither tests them nor reports them.
        """
        out = []
        for kind, (config, label_code) in self.LINE_CODES.items():
            if not self.has(kind):
                continue
            for n in range(1, max(self.capacity(kind), 1) + 1):
                label = self.text(label_code, n)
                if not (self._configured(config, n) or label):
                    continue
                out.append((kind, n, label or f"LINE {n}"))
        return out

    # the setup code that schedules each precision rate, and the key it is
    # offered under
    LINE_RATE_GATE = {("plld", "periodic"): ("78C", "plld020"),
                      ("plld", "annual"): ("783", "plld010"),
                      ("wplld", "periodic"): ("7A3", "plld020"),
                      ("wplld", "annual"): ("7AC", "plld010")}

    def line_rate_allowed(self, kind, number, rate_key):
        """May a technician start this rate on this line by hand?

        576013-610 Rev AC p.11-3 and p.12-3, the Manual Test Notes: "If your
        system does not have 0.2 or 0.1 gph test options, you will not see
        these selections", and "If the 0.2 or 0.1 gph line test option is
        available, but it was Disabled in PLLD 0.2 or 0.1 gph Test Schedule
        setups, then you can not start those test types manually."

        576013-623 Rev AN says the same from the setup side, for each rate
        and each card: the schedule screen "will not appear unless the 0.20
        Repetitive PLLD software module key is installed" (p.10-4), "Disabled
        - No manual or automatic 0.2 gph testing is allowed", and "Disabled is
        the default setting". REPETITIVE, MONTHLY, AUTO and MANUAL all say
        they enable manual testing. So a schedule nobody has programmed is a
        DISABLED one, and allows no hand-started test. The 0.1 gph pages are
        p.10-5, and WPLLD's are p.11-3 and p.11-4.

        The 3.0 gph test and every VLLD rate are on no such gate.
        """
        gate = self.LINE_RATE_GATE.get((kind, rate_key))
        if gate is None:
            return True
        code, option = gate
        if not self.licensed(option):
            return False
        raw = (self.values.get(f"S{code}{number:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return body[-1:] not in ("", "0")

    def conditions(self):
        """[AANNTT] for every condition that is TRUE right now.

        This is the physical state measured against the programmed limits,
        what the warning and alarm LEDs follow. The operator's manual: "You
        cannot turn off warning and alarm lights until you correct the cause
        of the warning or alarm. When you correct the condition, the lights
        will shut off."
        """
        out = []
        for tank, st in sorted(self.tank_level.items()):
            vol = st.get("volume", 0.0)
            # not the float's depth: below the Programmable Minimum Water
            # Threshold the float is resting on debris rather than water, and
            # a console that has been told where the debris is does not raise
            # a water alarm on it. See FIDELITY Y7.
            water = self.water_height(tank)
            tt = f"{tank:02d}"
            if tank in self.probe_out:
                # 576013-635: In-Tank alarm type 09, "Tank Probe Out
                # Alarm". With no probe there are no readings, so none of
                # the level conditions below can assert either: a console
                # that cannot see the water cannot raise High Water.
                out.append("0209" + tt)
                continue
            # 02/27 LOW TEMP WARNING: "Probe temperature drops below -4 F
            # (-15.6 C)" and "returns to normal operation after probe
            # temperature rises above 0 F" -- 576013-610 Table 29-3, whose
            # two figures are a hysteresis, not one line. BENCH.md T8.
            degrees = self.product_temperature(tank)
            if degrees < -4.0:
                self.low_temp.add(tank)
            elif degrees > 0.0:
                self.low_temp.discard(tank)
            if tank in self.low_temp:
                out.append("0227" + tt)
            if self.fuel_below_minimum(tank):
                # 02/08, Tank Invalid Fuel Level Alarm. It goes with the
                # level alarms rather than instead of them: the tank really
                # is nearly empty, so Low Product is true as well, and
                # 576013-939 pairs the two in its own cause and action.
                out.append("0208" + tt)
            hi_w, warn_w = self.limit("624", tank), self.limit("627", tank)
            over, low = self.limit_volume("623", tank), self.limit("621", tank)
            high = self.limit_volume("622", tank)
            maxv = self.limit("628", tank)
            deliver = self.limit_volume("629", tank)
            if hi_w and water >= hi_w:
                out.append("0203" + tt)
            elif warn_w and water >= warn_w:
                out.append("0210" + tt)
            # The highest threshold the volume has crossed, not the first
            # one in a fixed order. This used to test overfill before high
            # product, which is only right if overfill is the higher of the
            # two, and it is not: 576013-623 Rev AN puts High Product
            # between the Overfill percentage and 95%. With them the right
            # way round the old chain reported the LOWER alarm and never
            # reached the higher one.
            crossed = [(maxv, "0212"), (high, "0207")]
            # "Overfill Limit warns of a potential overfill ONLY DURING A
            # BULK DELIVERY. When the volume reaches this limit, the system
            # can activate an on-site overfill alarm" -- 576013-623 Rev AN.
            # Without the condition a tank that is simply full raised an
            # overfill alarm and went on raising it, where a real console
            # raises it while fuel is going in and then stops. The two
            # neighbouring limits carry no such clause and are correctly
            # unconditional: Max or Label Volume "warns when the level of
            # fluid in the tank exceeds the volume you enter here", and High
            # Product has no delivery sentence at all. See FIDELITY N7.
            if self.deliveries.in_progress(tank) is not None:
                crossed.append((over, "0204"))
            for limit, aa in sorted(crossed, key=lambda x: -(x[0] or 0.0)):
                if limit and vol >= limit:
                    out.append(aa + tt)
                    break
            if low and vol <= low:
                out.append("0205" + tt)
            elif deliver and vol <= deliver:
                out.append("0211" + tt)
        if self.out_of_paper:
            out.append("010100")      # system 01, "Printer out of Paper"
        if self.printer_lever_open:
            # 576013-610 Table 29-2: "PRINTER ERROR -- Printer feed roller
            # release is open. Push the release lever (under the lower
            # right corner of the printer cover) to the up position." The
            # lever the paper-change procedure has the operator push down.
            # FIDELITY N2b, BENCH.md P6.
            out.append("010200")
        if not self.battery_backup():
            # 576013-635: system alarm type 04, "Battery Off". The alarm
            # history report prints it as BATTERY IS OFF. A console running
            # with the switch off or the cell missing says so, because the
            # next outage would cost it everything.
            out.append("010400")
        if not self.clock_set:
            # nobody has set the clock since the cold boot took it
            out.append("011700")
        if self.cover_open:
            # the power-area safety cover is off
            out.append("011200")
        if self.selftest_error:
            out.append("011600")
        if self.rom_at_boot is not None and self.version != self.rom_at_boot:
            # the software changed hands without a cold boot: ROM Revision
            # Warning, until a cold boot owns the new chip
            out.append("010700")
        if getattr(self, "_mt_seen", False) and not self.has("mt"):
            # the Maintenance Tracker comm module was here and is gone
            out.append("012000")
        if self.apm_monitoring() and not self.apm_setup_ok():
            # 37/08 APM SETUP WARN, and its cause is stated: "A sensor used
            # by APM is missing or not configured", posted Immediate --
            # 577014-009 Rev B Table 2 p.16, "TLS-350 (APM) Alarm
            # Troubleshooting Summary", which is the only page anywhere
            # that gives category 37 a condition. It is the same rule VA4
            # already answers FAIL on, so the alarm and the verification
            # test agree by construction. See UNKNOWNS A60.
            out.append("370800")
        out.extend(self.bir.conditions())
        out.extend(self.autodial.conditions())
        if self.rdu_fault and self.has("rdu"):
            # system alarm 08: the remote display "not communicating
            # properly" (576013-818)
            out.append("010800")
        # Two DIM alarms, and 576013-610 Rev AC Table 29-19 is the whole
        # difference: COMMUNICATION ALARM is "No communication between DIM
        # board and an external device", DISABLED DIM ALARM is "No
        # communication between ECPU board and DIM board". The category is
        # which SIDE the DIM sits on -- Table 5-1's block is headed "Power
        # Side DIM (MDIM) (18) or Communication Side DIM (EDIM/BDIM) (19)"
        # -- and the type is which alarm, 02 disabled and 03 communication.
        # See UNKNOWNS A45.
        #
        # type 03: the EDIM lost its link to the POS or dispenser controller
        # for about a minute (576013-818 ch.10). The whole link down is port
        # 1; the bench can also take ports down one at a time, and each is
        # its own alarm on its own device
        ports = sorted(self.dim_down) or ([1] if self.dim_fault else [])
        for port in ports:
            if self.has("edim"):
                out.append(f"1903{port:02d}")
            elif self.has("mdim"):
                out.append(f"1803{port:02d}")
        # type 02: the card itself has stopped answering the console --
        # "the DIM module has stopped communicating with central processing
        # unit of the console", 576013-818 Table 10-7 -- which is upstream
        # of type 03 and can stand beside it: the communication charts'
        # step 2 asks "Is there a DISABLED DIM ALARM also posted for this
        # DIM?"
        for port in sorted(self.dim_disabled):
            if self.has("edim"):
                out.append(f"1902{port:02d}")
            elif self.has("mdim"):
                out.append(f"1802{port:02d}")
        # category 19 type 04: a Block DIM that reported and then did not,
        # for the BDIM TRANS ALARM DELAY (576013-623 p.5-17)
        out.extend(self.sales.conditions())
        # the ISD monitoring tests the bench has forced. Site tests are
        # category 30 on device 00; the collection tests ride hose 1.
        ISD_CODES = {"leakage": ("300600", "300700"),
                     "gross": ("300200", "300300"),
                     "degrade": ("300400", "300500"),
                     "collect_gross": ("310101", "310201"),
                     "collect_degrade": ("310301", "310401"),
                     "collect_flow": ("310501", "310601"),
                     "sensor": ("302000", "302100"),
                     "setup": ("301800", "301900")}
        if self.licensed("isd"):
            # Each failing setup criterion posts its own alarm as well as
            # failing the setup test: 577013-819 Rev F lists all six as the
            # COMMON CAUSES of ISD SETUP WARN, and gives each its own page
            # and its own one-line definition. See FIDELITY I1.
            for fault in self.isd_setup_result:
                aa, nn = isd.SETUP_ALARMS[fault]
                out.append(aa + nn + "00")
            # The ASSESSED state. A test that has just started failing posts
            # the WARNING; the same test on its own escalation day posts the
            # FAILURE alarm instead, which is what 577013-937 Rev J's alarm
            # sequence is for. See FIDELITY I3.
            for test, state in sorted(self.isd_states().items()):
                pair = ISD_CODES.get(test)
                if pair and state in ("warn", "fail"):
                    out.append(pair[0] if state == "warn" else pair[1])
        if self.licensed("pmc"):
            # The three processor tests, warning on the day they first fail
            # and alarming on the second consecutive one. Category 33 is
            # PMC's own and it is licensed separately from ISD.
            for test, (warn, fail) in sorted(isd.VP_ALARMS.items()):
                state = self.isd_state(test)
                if state == "warn":
                    out.append(warn[0] + warn[1] + "00")
                elif state == "fail":
                    out.append(fail[0] + fail[1] + "00")
        out.extend(self.siphon_break_conditions())
        out.extend(self.posted)
        out.extend(self.leaks.conditions())
        out.extend(self.lines.conditions())
        out.extend(self.csld.conditions())
        out.extend(self.setup_warnings())
        if self.has("smart"):
            # The vacuum sensor's two, both of which the figures give a
            # threshold for. See FIDELITY D1.
            out.extend(self.vac_conditions())
            # the water standing in a Mag sump, against the two heights
            # Mag Sensor Setup programs. FIDELITY U1b.
            out.extend(self.sumps.conditions())
        if self.has("vmc"):
            # Category 36, the four rows of Table 29-23. `M12` put the
            # category in the status table and nothing produced it.
            out.extend(self.vmc_conditions())
        if self.has("pumpmon"):
            # The Pump Relay Monitor's own alarm, which nothing raised:
            # `relay_stuck` was read by the status line alone and the
            # diagnostic that exists to show it had no reader at all. See
            # FIDELITY D1 and N1.
            from . import wiresensors
            out.extend(wiresensors.monitor_conditions(self))
        out.extend(self.test_needed_warnings())
        out.extend(self.accuchart.conditions())
        # "Missing Delivery Ticket": a gauged delivery with no ticket
        # against it, on a site running ticketed delivery
        for tank, _record in self.deliveries.unticketed():
            out.append("0228" + f"{tank:02d}")
        # category 05: a contact that is ON is an EXTERN INPUT ALARM
        out.extend(self.inputs.conditions())
        for (mod, num), state in sorted(self.sensor_state.items()):
            aa = SENSOR_MODULE_CATEGORY.get(mod)
            table = SMART_STATE_NN if mod == "smart" else SENSOR_STATE_NN
            nn = table.get(state)
            if not (nn and aa and self.has(mod)):
                continue
            if not self.sensor_alarm_allowed(mod, num, state):
                continue
            if (mod == "smart" and state in ("water", "waterwarn")
                    and self.sumps.suppressed(num)):
                # "During the Test Phase water alarms/warnings are
                # suppressed so that the user can fill the sump with water.
                # Fuel alarms/warnings are not suppressed." 576013-610 Rev
                # AC p.24-1, and for 24 hours after. FIDELITY U1b.
                continue
            out.append(aa + nn + f"{int(num):02d}")
        return out

    # "Set Tank Periodic Test Needed Warning" and its two day counts, then
    # the annual pair; then the same four for the lines. Each row is
    # (enable code, warning days code, alarm days code, warning nn, alarm nn).
    TANK_TEST_NEEDED = {
        "periodic": ("546", "547", "548", "16", "18"),
        "annual":   ("549", "54A", "54B", "17", "19"),
    }
    LINE_TEST_NEEDED = {
        "periodic": ("556", "557", "558"),
        "annual":   ("559", "55A", "55B"),
    }
    # each line type's own numbers for those four alarms, from i10100
    LINE_NEEDED_NN = {
        "plld":  {"periodic": ("04", "05"), "annual": ("12", "13")},
        "wplld": {"periodic": ("04", "05"), "annual": ("11", "12")},
        "vlld":  {"periodic": ("11", "13"), "annual": ("12", "14")},
    }
    LINE_CATEGORY = {"plld": "21", "wplld": "26", "vlld": "06"}

    def _days_since_pass(self, kind, device, rate_key, now):
        """Days since that rate last passed on that device.

        A CSLD pass counts. CSLD IS the tank's 0.2 gph periodic test on a tank
        set up for it, "a tank leak detection method that allows the tank to
        be tested without shutting the tank down", so a console would not
        then warn that no periodic test had been passed.
        """
        when = self.leaks.last_pass(kind, device, rate_key)
        if kind == "tank" and rate_key == "periodic":
            result, csld_when = self.csld.results.get(device) or (None, None)
            if csld_when and result in ("PASS", "INCR"):
                when = max(when or 0.0, csld_when)
        if when is None:
            when = self._commissioned
        if when is None:
            return None
        return max(0.0, (now - when) / 86400.0)

    def _enabled_flag(self, code):
        return (self.values.get(f"S{code}00") or "").strip().endswith("1")

    def _days_setting(self, code):
        raw = (self.values.get(f"S{code}00") or "").strip()
        return int(raw) if raw.isdigit() else None

    def test_needed_warnings(self):
        """[AANNTT] for tests that are overdue.

        "Press CHANGE, and enter the number of days (0 to 30 days) after which
        you want the system to warn that a tank test has not been passed."
        The console counts from the last PASS, and the alarm day count is the
        same clock a few days further on.
        """
        out = []
        now = time.mktime(self.now())
        if self._commissioned is None:
            return out
        if self.has("probe"):
            for rate_key, row in self.TANK_TEST_NEEDED.items():
                enable, warn_code, alarm_code, warn_nn, alarm_nn = row
                if not self._enabled_flag(enable):
                    continue
                warn_days = self._days_setting(warn_code)
                alarm_days = self._days_setting(alarm_code)
                for tank in sorted(self.programmed_tanks()):
                    days = self._days_since_pass("tank", tank, rate_key, now)
                    if days is None:
                        continue
                    if alarm_days and days >= alarm_days:
                        out.append("02" + alarm_nn + f"{tank:02d}")
                    elif warn_days and days >= warn_days:
                        out.append("02" + warn_nn + f"{tank:02d}")
        for rate_key, (enable, warn_code,
                       alarm_code) in self.LINE_TEST_NEEDED.items():
            if not self._enabled_flag(enable):
                continue
            warn_days = self._days_setting(warn_code)
            alarm_days = self._days_setting(alarm_code)
            for kind, number, _label in self.programmed_lines():
                days = self._days_since_pass(kind, number, rate_key, now)
                if days is None:
                    continue
                aa = self.LINE_CATEGORY[kind]
                warn_nn, alarm_nn = self.LINE_NEEDED_NN[kind][rate_key]
                if alarm_days and days >= alarm_days:
                    out.append(aa + alarm_nn + f"{number:02d}")
                elif warn_days and days >= warn_days:
                    out.append(aa + warn_nn + f"{number:02d}")
        return out

    # which sensor module raises which category, and what makes it complete
    SETUP_CHECK = [("liquid", "03", "701", "704"), ("vapor", "04", "706", "709"),
                   ("gw", "07", "711", "713"), ("2wire", "08", "741", "744"),
                   ("3wire", "12", "746", "749"),
                   ("universal", "13", "74B", "74E"),
                   ("smart", "28", "721", "723")]

    def setup_warnings(self):
        """[AANNTT] for programming that is insufficient or invalid.

        "When you exit the Setup Mode, a Setup Data Warning will appear in the
        Status Display and the yellow warning light will flash if insufficient
        or invalid setup data has been entered ... The display and report will
        identify the source of the warning (i.e. Tank 1, Sensor 4, etc.), and
        the warning indicators will remain active until the cause has been
        corrected."

        It is a condition here and it still latches, and this sentence
        used to say the opposite of what the code does -- "so it is a
        condition, not a latch: fill the gap in and it goes" -- which is
        the drift `FIDELITY.md`'s own header warns about. What this method
        returns IS a condition: fill the gap in and it leaves the list. The
        MESSAGE then waits for ALARM/TEST like every other one, because
        `latches` is the thing that decides that and no page exempts this
        row.

        The two sentences are about different objects and the general one
        wins. 576013-610 Rev AC p.29-1: "After you correct the cause, you
        must press the ALARM/TEST button to acknowledge the alarm and clear
        the display." UNKNOWNS A7 closed on that reading with an owner's
        report of a real console's `PRINTER ERROR` behind it, and 623's
        "warning indicators" most likely means the lamp -- which does go
        out on its own here, because the lamp follows the condition.

        Recorded rather than settled: see UNKNOWNS B11, and the
        alarm-lifecycle audit's A9.
        """
        if self.in_setup:
            return []
        out = []
        if self.has("probe"):
            for n in range(1, max(self.capacity("probe"), 1) + 1):
                if not self._configured("601", n):
                    continue
                if not (self.text("602", n) and self.limit("607", n)
                        and (self.limit("60A", n) or self.limit("604", n))):
                    out.append("0201" + f"{n:02d}")
                    continue
                # Mass/Density has a warning of its own, and the NOTE
                # that gives it is on the page that documents the field:
                # "A Setup Data Warning is posted from the time the
                # Mass/Density feature is enabled until a density or
                # thermal coefficient value is entered for that tank,
                # clearing the alarm" (576013-623 Rev AN p.7-5).
                #
                # Two NOTEs say it and they do not quite agree. The one
                # beside the Mass/Density switch itself reads "until a
                # product density value is entered for that tank" --
                # density alone, where p.7-5 takes either. p.7-5 is the
                # page the fields are on and is followed; the difference
                # only shows on a tank given a thermal coefficient and no
                # density, which the other reading would still warn about.
                #
                # Entered, not non-zero. The page says "entered", and a
                # tank that has never been programmed has no stored value
                # at all where one programmed to zero has one -- which is
                # the distinction `limit` erases by answering 0.0 to both.
                # The store is asked directly so the two are told apart.
                if self.values.get("S56000") == "1":
                    if not any(self.values.get(f"S{tok}{n:02d}")
                               for tok in ("61E", "609")):
                        out.append("0201" + f"{n:02d}")
        for module, aa, config, category in self.SETUP_CHECK:
            if not self.has(module):
                continue
            for n in range(1, self.capacity(module) + 1):
                if self._configured(config, n) and not self.text(category, n):
                    out.append(aa + ("01" if aa == "28" else "02")
                               + f"{n:02d}")
        out += self.vac_setup_warnings()
        if self.count("vmc") > 1:
            # "SETUP WARN -- More than one VMCI module is installed. The VMCI
            # module in the higher comm port must be removed", 576013-610 Rev
            # AC Table 29-22. The warning names the HIGHER port, so it is
            # raised against the second module and not the first, and it
            # goes when that card comes out. See FIDELITY M12.
            for n in range(2, self.count("vmc") + 1):
                out.append("3501" + f"{n:02d}")
        # The line length is the third thing each of the two pressure
        # families needs, and 576013-623 Rev AN p.10-2 says so in its own
        # voice: "IMPORTANT! The default line length must be changed to
        # reflect the actual line length or a Setup Data Warning will
        # occur." This checked the pipe type and the tank and not the
        # length, so the one field the page raises a warning about was the
        # one the warning could not see.
        #
        # EITHER length counts, because five of the nineteen pipe types are
        # two diameters and the unused one of the pair is programmed to zero
        # -- p.10-3, and see FIDELITY R22. A line with 0 feet of 2 inch and
        # 200 of 3 inch has been programmed; a line with neither stored has
        # not. Stored rather than non-zero, the same reading as the probe
        # block above: the page says CHANGED, and a value nobody entered is
        # the case it is about.
        lengths = {"plld": ("789", "77F"), "wplld": ("7A9", "7AD")}
        for module, aa, config, needs in (("plld", "21", "781", ("788", "785")),
                                          ("wplld", "26", "7A1", ("7A8", "7A5")),
                                          ("vlld", "06", "751", ("756", "752"))):
            if not self.has(module):
                continue
            for n in range(1, self.capacity(module) + 1):
                if not self._configured(config, n):
                    continue
                feet = lengths.get(module) or ()
                if not all(self.text(code, n) for code in needs):
                    out.append(aa + "01" + f"{n:02d}")
                elif feet and all(self.limit(c, n) is None for c in feet):
                    # `feet and`, not `all(...)` alone: a volumetric line has
                    # no length code here, and `all` over nothing is True --
                    # which would have warned about every VLLD line on every
                    # site, for a field that family does not have.
                    out.append(aa + "01" + f"{n:02d}")
        return out

    # S723's own category codes: "04" a Vac Sensor, "05" an ATMP one.
    VAC_CATEGORY, ATMP_CATEGORY = "04", "05"

    def vac_setup_warnings(self):
        """The Vac Sensor's own Setup Data Warnings, which are not a category.

        576013-623 Rev AN chapter 26 states three of them in as many words:

            "You must select the pump that will provide the source of vacuum
             for this Vac Sensor or a Setup Data Warning will be posted for
             this Vac Sensor."
            "The permitted range is 1 to 500 gallons. Default is 501. A
             Setup Data Warning alarm will activate if a volume between 1
             and 500 is not entered."

        The sensor's own two manuals put the bottom of that range a decade
        lower -- "The permitted range is 0.1 to 500 gallons (0.378 to
        1892.7 litres)", 577013-836 Rev N p.4-4, and 577013-873 Rev E
        p.4-2 again -- and 836's worked example is a 2.88 gallon zone, so
        the tenth is the bound enforced here. See UNKNOWNS B25.
            "A Setup Data Warning will be posted for all Vac Sensors if an
             ATMP sensor is not present and configured."

        `SETUP_CHECK` asks a smart sensor for a category and nothing else, so
        a Vac sensor with no pump, no volume and no atmospheric sensor
        anywhere on the site counted as fully programmed. See FIDELITY M3.

        One clause of the first is not modelled and is worth naming: "If the
        selected pump output relay is not assigned to a pump sense device, a
        Setup Data Warning for this Vac Sensor will be posted." That is a
        relay's assignment to a pump SENSE device, which this console does
        not hold.
        """
        if not self.has("smart"):
            return []
        positions = range(1, self.capacity("smart") + 1)
        atmp = any(self.text("723", n) == self.ATMP_CATEGORY
                   for n in positions)
        out = []
        for n in positions:
            if self.text("723", n) != self.VAC_CATEGORY:
                continue
            pump = self.text("729", n)
            volume = self.limit("72A", n)
            if (not pump or pump[:2] == "00"
                    or volume is None or not 0.1 <= volume <= 500.0
                    or not atmp):
                out.append("2801" + f"{n:02d}")
        return out

    def slot_text(self, code, count=None, base=0):
        """Which positions on this module are connected: "1 X 3 X".

        The console configures a module, not a device: four probe positions
        or eight sensor positions on one screen, a number where something is
        connected and an X where nothing is.
        """
        count = count or self.positions(code)
        return " ".join(str(i) if self._configured(code, base + i) else "X"
                        for i in range(1, count + 1))

    def set_slots(self, code, text, base=0):
        """Store one flag per position, which is what the wire format holds."""
        prefix = self.is_prefixed(code)
        for i, ch in enumerate(text.split(), start=1):
            on = "0" if ch.upper() == "X" else "1"
            device = base + i
            self.values[f"S{code}{device:02d}"] = (f"{device:02d}" + on
                                                   if prefix else on)
        self.save()

    def position_on(self, code, device):
        """Is that config screen's position switched on? The public form.

        Every family's CONFIG report prints this as a word -- 576013-635
        p.317's `I701` sample heads its third column CONFIGURED over `ON` --
        and the setting is stored as one flag character per position, so
        there is nothing for a value formatter to decode without knowing the
        position. See FIDELITY L16.
        """
        return self._configured(code, int(device))

    def _configured(self, code, device):
        raw = (self.values.get(f"S{code}{device:02d}") or "").strip()
        return raw.endswith("1")

    def test_notify(self, tank):
        """S630, Tank Test Notify, per tank.

        576013-623 Rev AN p.7-24: "When on, the Tank Test Notify feature
        triggers a warning, allowing the operator to set a relay to shut
        down the submersible", and the same manual's own default table gives
        it as OFF. So TANK TEST ACTIVE is a warning a site asks for: a
        console that has not been told to notify runs its tests quietly.
        See FIDELITY H10.
        """
        return self._configured("630", tank)

    def latches(self, rec):
        """Does this alarm stay on the display after its cause has gone?

        Nearly all of them do. This used to say the opposite, and why it was
        wrong is worth keeping. The Operator's Manual states the rule under
        its own MESSAGES heading, p.29-1:

            Warning and Alarm Messages display until you correct the cause of
            the problem. After you correct the cause, you must press the
            ALARM/TEST button to acknowledge the alarm and clear the display.
            The system will then display the ALL FUNCTIONS NORMAL message.

        and the Setup Manual's key table says it from the key's side:
        ALARM/TEST "shuts off audible alarm and clears alarms that have
        returned to normal condition" -- which is only something a key can do
        if such alarms are still on the screen waiting for it.

        The sentence this used to rest on, p.3-2's "ALARM/TEST silences the
        alarm. It does not clear the alarm message from the display or disable
        the alarm", is about an alarm whose cause is STILL THERE. The two sit
        in one manual and do not disagree: press it early and it silences
        without clearing, press it after the fix and it clears. What DOES
        follow the condition is the lights, which the same page treats
        separately -- "When you correct the condition, the lights will shut
        off." Messages latch; lights do not.

        The exceptions are the alarms a manual explicitly says clear
        themselves, named one at a time rather than assumed:

        * NO CSLD IDLE TIME -- "This alarm will automatically clear when the
          system detects that at least one idle period has occurred".
        * FUEL OUT -- "The alarm will clear when the fuel level exceeds 10
          inches".

        A test RESULT latches for the same reason as the rest, and its
        acknowledgement is what the Line Re-Enable Method (Pass Line Test /
        Alarm Acknowledge) chooses between. With a Maintenance Tracker board
        fitted every alarm becomes a PROTECTED maintenance alarm: held for the
        contractor and cleared only by an acknowledgement made with a valid ID
        key, which the Maintenance History log records.
        """
        if self.has("mt"):
            return True
        return rec[:4] not in SELF_CLEARING

    def alarm_reduction_on(self):
        """Is Alarm Reduction switched on, on a console that has it?

        Two gates, both already here: the software has to know the feature --
        "In TLS-350 Software Version 32, Veeder-Root added filters" -- and the
        site has to have left it enabled, which is the default.
        """
        if not self.supports("alarmreduce"):
            return False
        field = FIELDS.get("set.alarm_reduction", {})
        now = self.setting("alarm_reduction", 0, field.get("default", ""))
        return not str(now).upper().startswith("DIS")

    def _open_alarm_lately(self, record, now):
        """Has this sensor posted an Open alarm in the last 24 hours?

        The console already keeps the answer: `alarm_log` is what I206 and the
        alarm history reports read, its `02` rows are alarms that OCCURRED,
        and an occurrence is what Appendix A's "no open alarms" counts. A
        condition that never made it past the filter never posted, so it does
        not count against the next one, which is the right way round.
        """
        for row in self.alarm_log:
            if row.get("state") != "02":
                continue
            if row["aa"] + row["nn"] + row["tt"] != record:
                continue
            try:
                when = time.mktime(time.strptime(row["at"], "%y%m%d%H%M"))
            except ValueError:                          # pragma: no cover
                continue
            if now - when <= SENSOR_FIRST_OPEN_WINDOW:
                return True
        return False

    def water_alarm_window(self, tank):
        """(detect, clear) for a High Water alarm -- 576013-623 Rev AN p.98.

        A different feature from Alarm Reduction and an older one, with its
        own setting per tank: "To help prevent false water alarms during
        deliveries, the Water Alarm Filter allows the user to select from
        several filters that will delay the posting of a water alarm ... The
        Low, Medium and High selections all use a 3 minute delay before
        posting a water alarm. If a Water Alarm delay of less than 3 minutes
        is desired, select Off for the Water Alarm Filter ... the delay time
        is programmable from 30 to 180 seconds (default)."

        So OFF is not "no filter": it is the setting that makes the delay
        programmable, and its own default is the same three minutes. No
        clear delay is stated for any of the four.
        """
        level = str(self.setting(
            "water_filter", tank,
            FIELDS.get("set.water_filter", {}).get("default", ""))).upper()
        if level != "OFF":
            return (180.0, 0.0)
        raw = str(self.setting(
            "water_delay", tank,
            FIELDS.get("set.water_delay", {}).get("default", "180"))).strip()
        seconds = float(raw) if raw.isdigit() else 180.0
        return (min(max(seconds, 30.0), 180.0), 0.0)

    def water_alarm_inhibited(self, tank):
        """"The medium and high filter selections will inhibit the water
        alarm during a delivery", and Low explicitly does not."""
        level = str(self.setting(
            "water_filter", tank,
            FIELDS.get("set.water_filter", {}).get("default", ""))).upper()
        return (level in ("MEDIUM", "HIGH")
                and bool(self.deliveries.in_progress(tank)))

    def alarm_filter(self, record, now=None):
        """(detect, clear) seconds for one AANNTT, or None if it is not
        filtered at all. Appendix A of 577013-814, and the water filter."""
        if record[:4] == "0203":
            return self.water_alarm_window(int(record[4:6]))
        if not self.alarm_reduction_on():
            return None
        fixed = ALARM_FILTER_SECONDS.get(record[:4])
        if fixed:
            return fixed
        if record[:2] not in SENSOR_FILTER_CATEGORIES:
            return None
        if record[2:4] == "04" and not self._open_alarm_lately(
                record, now if now is not None else time.mktime(self.now())):
            # the first open circuit in a rolling 24 hours posts at once and
            # still takes the three minutes to clear
            return (0.0, SENSOR_FILTER_SECONDS["04"][1])
        return SENSOR_FILTER_SECONDS.get(record[2:4], SENSOR_FILTER_DEFAULT)

    def reduce_alarms(self, raw):
        """The filtered view of the conditions that are true right now.

        A filtered alarm is not posted until its condition has held for the
        Detection Response Time, and is not dropped until it has been gone
        for the Clear Response Time. Everything else passes straight through,
        which is most of the list: Appendix A filters the sensor families,
        the probe and the two comm alarms, and nothing about a tank's level.

        Both delays are measured from the moment the console LOOKED and saw
        the change, not from the change itself, because a console only knows
        what its own scan found: `tick` calls this every second, so on a
        running console the two are the same thing.
        """
        now = time.mktime(self.now())
        out = set()
        for record in raw:
            if record[:4] == "0203" and self.water_alarm_inhibited(
                    int(record[4:6])):
                # inhibited rather than delayed, and the delay starts again
                # when the delivery ends rather than carrying on through it
                self._filtered.pop(record, None)
                continue
            window = self.alarm_filter(record, now)
            if window is None:
                out.add(record)
                continue
            held = self._filtered.setdefault(record, {"since": now})
            held.pop("gone", None)
            held.setdefault("since", now)
            if held.get("on") or now - held["since"] >= window[0]:
                held["on"] = True
                out.add(record)
        for record, held in list(self._filtered.items()):
            if record in raw:
                continue
            window = self.alarm_filter(record, now)
            if window is None:
                # the filter was switched off, or the software downgraded,
                # while this one was being held: it stops being held
                del self._filtered[record]
                continue
            held.pop("since", None)
            gone = held.setdefault("gone", now)
            if held.get("on") and now - gone < window[1]:
                out.add(record)
            elif now - gone >= window[1]:
                del self._filtered[record]
        return out

    def compute_alarms(self):
        """[AANNTT] the console is DISPLAYING, which is not the same list.

        Every condition that is true right now, plus anything latched that has
        not been acknowledged since its cause was corrected.

        Alarm Reduction sits between the two: a filtered condition is not an
        alarm until it has held for Appendix A's detection delay, and stays
        one until it has been gone for the clear delay. See `reduce_alarms`.
        """
        live = self.reduce_alarms(self.conditions())
        fresh = live - self._seen
        if fresh:
            # a new condition sounds the audible alarm again, and an alarm
            # that comes back is a new alarm, not an acknowledged one
            self.silenced = False
            self.acked -= fresh
            # "Date and time alarm occurred": I206 and the alarm history
            # report are a record of when, so the console keeps one
            self._log_alarms(sorted(fresh), "02")
        gone = self._seen - live
        if gone:
            # "SS - Alarm State: 01=Alarm cleared, 02=Alarm occurred"
            self._log_alarms(sorted(gone), "01")
        self._seen = live
        self.latched |= {r for r in live if self.latches(r)}
        shown = live | self.latched
        self.acked &= shown
        return sorted(shown)

    def displayed(self):
        """The alarms on the display as of the console's LAST look.

        `compute_alarms` is the look. A relay follows the display, and the
        pump relay monitor watching a relay is itself one of the conditions
        the look computes -- so anything that needs the display from inside
        the look reads this, one scan stale at most, rather than asking for
        a second look from inside the first.
        """
        return sorted(self._seen | self.latched)

    def active_alarms(self):
        """The alarms whose CAUSE is still there, as of the last look.

        `displayed()` is what is on the glass, which includes an alarm that
        has been corrected and not yet acknowledged -- messages latch, and
        `latches()` is where the manual says so. This is the other half of
        the same page: "When you correct the condition, the lights will
        shut off." Anything that follows the CONDITION rather than the
        message reads this, and a pump the site has told the console to
        shut down is one of them -- see `pressure.Lines.shutdown_alarms`.
        """
        return sorted(self._seen)

    def _log_alarms(self, records, state):
        """What I111, I112 and I206 read: when each alarm came and went."""
        when = time.strftime("%y%m%d%H%M", self.now())
        for record in records:
            self.alarm_log.insert(0, {"aa": record[:2], "nn": record[2:4],
                                      "tt": record[4:6], "at": when,
                                      "state": state})
        del self.alarm_log[200:]

    # The alarms i10100's own list calls warnings rather than alarms. The
    # panel abbreviates some of them, "DELIVERY NEEDED" for what the serial
    # manual calls "Tank Delivery Needed Warning": so the split cannot be
    # read off the displayed message alone.
    WARNING_NUMBERS = {
        "01": {"01", "06", "07", "10", "11", "13", "14", "15", "17"},
        "02": {"01", "10", "11", "16", "17", "20", "21", "22", "23", "24",
               "25", "27", "28", "30"},
    }

    def priority(self, record):
        """Is this one for the Priority Alarm History, or the other one?

        A console keeps two histories: the alarms a site has to act on, and
        the warnings it should look at. i10100's list names each one, and
        that naming is the split.
        """
        numbers = self.WARNING_NUMBERS.get(record["aa"])
        if numbers is not None:
            return record["nn"] not in numbers
        described = describe_alarms([record["aa"] + record["nn"]
                                     + record["tt"]])
        if not described:
            return False
        return "WARNING" not in described[0]["description"].upper()

    # Mass and density, which 214 and 215 report and nothing else here has
    # needed. The manual's own example reads 5.9987 against 5329 gallons for
    # 20357 lb, so its density is pounds per gallon and its mass is the two
    # multiplied. Petrol is about 6.1 lb/gal and diesel about 7.1, which is
    # what makes this a PRODUCT property rather than one number for the site.
    DENSITY_BAND = (5.90, 7.20)

    # 282's table is 51 hourly volumes, newest first: "latest recorded hourly
    # volume, intermediate hourly recorded volumes, oldest recorded hourly
    # volume". Nothing here keeps an hourly log, so it is derived from what
    # the tank holds now and what it has been selling -- which is the same
    # arithmetic the tank itself runs, backwards.
    VOLUME_HISTORY = 51

    # 8A2's list. This used to hold six entries, which were the six the
    # serial manual happens to print in its EXAMPLE; the real table is
    # 577013-874 Rev A, the Maintenance Service Codes pamphlet, and it holds
    # 129 codes in sixteen categories. Read off rendered page images rather
    # than extracted text: the pamphlet is laid out in two columns of narrow
    # tables and every plain-text extraction of it offsets the label column
    # against the code column by a row.
    #
    # Spellings are the pamphlet's own, including its typos -- PREFORMED for
    # "performed" at 0109, 0302 and 0715 -- because a site's technician reads
    # these off the same card. The wire truncates them to nineteen or
    # twenty-one characters, which is the console's business and not this
    # table's. User defined codes are 99XX and are added at runtime.
    SERVICE_CODES = [
        # GENERAL
        ("0101", "REPROGRAMMED TLS"), ("0102", "COLD BOOT SYSTEM"),
        ("0103", "REPLACED PC BOARD / SOFTWARE"), ("0104", "NO PROBLEM FOUND"),
        ("0105", "NO SOLUTION FOUND"), ("0106", "OTHER SOLUTION PERFORMED"),
        ("0107", "REPLACED KEYBOARD"), ("0108", "REPLACED DISPLAY"),
        ("0109", "PREFORMED SYSTEM UPGRADE"), ("0110", "CONDUCTED TRAINING"),
        ("0111", "OPERATOR ERROR"),
        # SYSTEM
        ("0201", "INSTALLED PAPER"), ("0202", "REPLACED PRINTER"),
        ("0203", "REPLACED LINE SHARING DEVICE"), ("0204", "CLEARED PAPER JAM"),
        # TANKS
        ("0301", "DISPATCHED/ REMOVED WATER"),
        ("0302", "PREFORMED PRECISION TANK TEST"),
        ("0303", "REWIRED/RECONNECTED PROBE"), ("0304", "REPLACED PROBE"),
        ("0305", "STUCK FLOAT CORRECTED"), ("0306", "CLEANED PROBE"),
        ("0307", "CORRECTED TANK CHART"), ("0308", "RESET ACCUCHART"),
        # SENSORS
        ("0401", "REPLACED SENSOR"), ("0402", "REWIRED SENSOR"),
        ("0403", "DISPATCHED / REMOVED FUEL"),
        ("0404", "DISPATCHED / REMOVED WATER"),
        ("0405", "ADJUSTED BRINE LEVEL"), ("0406", "REPAIRED PRIMARY LEAK"),
        ("0407", "REPAIRED SECONDARY LEAK"), ("0408", "RECHARGED VACUUM"),
        ("0409", "REPLACED VACUUM TUBING"),
        # COMMUNICATIONS
        ("0501", "ADDED COM BOARD"), ("0502", "RESET MODEM"),
        ("0503", "RECONNECTED PHONE LINE"),
        ("0504", "REPROGRAMMED PHONE NUMBER"),
        ("0505", "REPROGRAMMED IP ADDRESS"), ("0506", "REPLACED COM CABLE"),
        # DIMs
        ("0601", "REPLACED DIM MODULE"), ("0602", "REPLACED DIM CABLE"),
        ("0603", "DIM MISAPPLICATION"),
        ("0604", 'SWITCHED CAB BOX TO "RUN MODE"'),
        # LINE LEAK DETECTION
        ("0701", "REPAIR LEAK"), ("0702", "REPAIRED STP MOTOR / CAPACITOR"),
        ("0703", "RESTORE STP POWER"), ("0704", "CLEANED FUNCTIONAL ELEMENT"),
        ("0705", "DISABLED FUNCTIONAL ELEMENT"), ("0706", "OUT OF FUEL"),
        ("0707", "REPLACED LLD TRANSDUCER"), ("0708", "REWIRED LLD"),
        ("0709", "REPLACED LLD MODULE"), ("0710", "REPLACED CHECK VALVE"),
        ("0711", "REPLACED PRODUCT RELAY"),
        ("0712", "REPLACED DISPENSER VALVE(S)"),
        ("0713", "ELIMINATED WPLLD NOISE SOURCE"),
        ("0714", "INSTALLED MECHANICAL LLD"), ("0715", "PREFORMED LINE TEST"),
        ("0716", "REPLACED MLLD"),
        # SUBMERSIBLE PUMP
        ("0801", "INSTALLED NEW STP"), ("0802", "REPLACED UMP"),
        ("0803", "REPLACED FUNCTIONAL ELEMENT"),
        ("0804", "REPLACED CHECK VALVE"), ("0805", "REPROGRAMMED IP ADDRESS"),
        ("0806", "REPLACED CONTROL BOX/ RELAY"),
        ("0807", "REPLACED CAPACITOR"), ("0808", "REPLACED SEALS"),
        ("0809", "REPLACED VARIABLE SPEED CONTROLLER"),
        ("0810", "REWIRED PUMP"),
        # VLLD ALARMS
        ("0901", "REPLACED VLLD CONTROLLER"),
        ("0902", "REPLACED VLLD CHECK VALVE"),
        ("0903", "RAN VLLD SELF CHECK"), ("0904", "RAN VLLD PUMP SELF TEST"),
        ("0905", "VLLD OTHER CORRECTIVE ACTION"),
        # STAGE II VAPOR RECOVERY
        ("1001", "CALIBRATED VAC ASSIST PUMP"), ("1002", "CONDUCTED A/L TEST"),
        ("1003", "BACK PRESSURE TEST"), ("1004", '2" DECAY TEST'),
        ("1005", '10" DECAY TEST'), ("1006", "REPAIRED CONTAINMENT LEAK"),
        ("1007", "REPAIRED VAC ASSIST PUMP"),
        ("1008", "REPLACED VAC ASSIST PUMP"), ("1009", "ADJUSTED A/L"),
        ("1010", "SERVICED VAPOR PROCESSOR"),
        # STAGE I VAPOR RECOVERY
        ("1101", "REPAIRED / REPLACED SPILL BUCKET"),
        ("1102", "REPAIRED / REPLACED DELIVERY CAP"),
        ("1103", "REPAIRED / REPLACED VAPOR EXTRACTOR CAP"),
        ("1104", "REPAIRED / REPLACED PROBE RISER CAP"),
        # DISPENSER
        ("1201", "REPLACED FILTER"), ("1202", "REPLACED SOFTWARE"),
        ("1203", "COLD BOOT SYSTEM"), ("1204", "REPAIRED / REPLACED DISPENSER"),
        ("1205", "RESET SHEAR VALVE"), ("1206", "REPLACED SHEAR VALVE"),
        ("1207", "REPAIRED PIPING"), ("1208", "REPAIRED / REPLACED BOOT"),
        ("1209", "CALIBRATED LIQUID METER(S)"), ("1210", "REPLACED PRINTER"),
        ("1211", "INSTALLED PAPER"), ("1212", "CLEARED PAPER JAM"),
        ("1213", "REPLACED/REPAIRED DOOR LOCK"),
        ("1214", "REPLACED/REPAIRED DOOR SWITCH"),
        # HANGING HARDWARE
        ("1301", "REPLACED HANGING HARDWARE"), ("1302", "REPLACED NOZZLE"),
        ("1303", "REPLACED BOOT"), ("1304", "REPLACED SPOUT"),
        ("1305", "REPLACED PRIMARY HOSE"), ("1306", "REPLACED BREAKAWAY"),
        ("1307", "REPAIRED BREAKAWAY"), ("1308", "REPLACED WHIP HOSE"),
        ("1309", "REPLACED HOSE EXTRACTOR"), ("1310", "REPLACED FLOW LIMITER"),
        ("1311", "REPLACED NOZZLE COVER"), ("1312", "BAGGED OFF NOZZLE"),
        ("1313", "RETURNED NOZZLE TO SERVICE"),
        # POS
        ("1401", "REPROGRAM"), ("1402", "REPLACE SOFTWARE"),
        ("1403", "COLD BOOT SYSTEM"), ("1404", "REPLACE POS"),
        ("1405", "REWIRE POS"),
        # FACILITY MAINTENANCE
        ("1501", "REPAIRED / REPLACED OUTSIDE LIGHTS"),
        ("1502", "REPAIRED / REPLACED SIGNAGE"),
        ("1503", "CLEANED STORM DRAIN"),
        ("1504", "SERVICED OIL WATER SEPARATOR"),
        # INSPECTION / TESTING
        ("1601", "ANNUAL OPERABILITY TESTING"),
        ("1602", "WEIGHT AND MEASURES TESTING"),
        ("1603", "SUMP INTEGRITY TESTING"),
        ("1604", "DESIGNATED OPERATOR VISIT"), ("1605", "ELD TESTING"),
        ("1606", "SPILL BUCKET TESTING"),
    ]

    def service_codes(self):
        """8A2, the standard list plus whatever the site has added."""
        return list(self.SERVICE_CODES) + list(self.user_service_codes)

    # "cccccc - Six digit ID code (ASCII)" and "nnnnnnnnnnnnnnnnn - ID label
    # (17 characters, ASCII)", 576013-635's own two fields for 8A3 and 8A4.
    KEY_ID_WIDTH = 6
    KEY_LABEL_WIDTH = 17

    # "You have one minute to plug your ID key into the MT Comm card and
    # press Enter, or the system will timeout", 576013-610 ch.33.
    KEY_PROMPT_SECONDS = 60.0

    # How many tracker keys are remembered. A real console's list is
    # finite because somebody has to cut the keys; this one is reachable
    # from the serial port, so it needs a number of its own.
    KEYS_KEPT = 200

    def present_tracker_key(self, ident, label="", expired=False):
        """Plug a Contractor's ID key into the MT Comm card.

        576013-610 chapter 33 is the whole sequence, and its four refusals:
        "A valid key inserted within one minute following the key insertion
        prompt will display" `MAINTENANCE TRACKER / LOGGED IN XXXXXX`, and
        "If your key is not accepted the display will read: KEY EXPIRED (your
        TLS certification has expired), KEY BLOCKED (your key has been
        blocked), KEY INVALID (for some reason your key cannot be
        acknowledged by the system), or LOG-IN RECORD ERROR - key was read
        but system was unable to write a log-in record to FPROM - (this
        usually a problem with the console system date/time)."

        The fourth one is the one with a stated CAUSE, and this console has
        that cause: a log-in record is stamped, and a console whose clock
        nobody has set cannot write one.

        A key that is accepted is on the active list afterwards, which is
        where 8A3's list comes from -- nothing else on this console presents
        one, so an untouched console's list is empty, which is what an
        untouched console's list looks like.
        """
        ident = (ident or "").strip().upper()
        if len(ident) != self.KEY_ID_WIDTH or not ident.isalnum():
            return "KEY INVALID"
        if any(k == ident for k, _n in self.blocked_keys):
            return "KEY BLOCKED"
        if expired:
            return "KEY EXPIRED"
        if not self.clock_set:
            return "LOG-IN RECORD ERROR"
        label = (label or "").strip()[:self.KEY_LABEL_WIDTH]
        if not any(k == ident for k, _n in self.mt_keys):
            self.mt_keys.append((ident, label or f"KEY {ident}"))
            del self.mt_keys[:-self.KEYS_KEPT]
        self.mt_session = (ident, time.mktime(self.now()))
        self.save()
        return f"LOGGED IN {ident}"

    def remove_tracker_key(self):
        """"When all codes are entered for this work session, remove your
        Contractor's ID key." The session ends with the key."""
        was = self.mt_session
        self.mt_session = None
        self.save()
        return was[0] if was else None

    def mt_logged_in(self):
        """Is a contractor logged in? Which is what unlatches a held alarm."""
        return self.mt_session is not None

    def block_tracker_key(self, ident, label=""):
        """8A4: take a Contractor ID key off the accepted list.

        The report the console answers with afterwards is the BLOCKED list,
        not the active one -- 8A3 and 8A4 share a template and mean opposite
        things by it. Both carry a LABEL, so the label comes off the active
        list with the key rather than being thrown away.
        """
        ident = (ident or "").strip().upper()
        named = next((n for k, n in self.mt_keys if k == ident), "")
        if any(k == ident for k, _n in self.blocked_keys):
            return ident
        self.blocked_keys.append((ident, label or named
                                  or f"KEY {ident}"))
        # Capped, like every other list the console keeps: `alarm_log` at
        # 200, `bir.events` at 40, `accuchart_log` at 40, `vp_cycles` at 20.
        # This one had no ceiling, and `S8A4` reaches it from the serial
        # port with no authentication and -- unlike the inquire side --
        # with no `has("mt")` guard either, so a console with no
        # Maintenance Tracker card fitted still grew the list. The key
        # space is 36^6, and every append saves to disk.
        del self.blocked_keys[:-self.KEYS_KEPT]
        self.mt_keys = [(k, name) for k, name in self.mt_keys if k != ident]
        if self.mt_session and self.mt_session[0] == ident:
            # a blocked key is not a session any more
            self.mt_session = None
        self.save()
        return ident

    def tracker_keys(self):
        """8A3: the Contractor ID keys the Maintenance Tracker will accept."""
        return [(ident, name) for ident, name in self.mt_keys]

    def blocked_tracker_keys(self):
        """8A4's own report: the keys this console will not accept."""
        return [(ident, name) for ident, name in self.blocked_keys]

    def volume_history(self, tank, hours=None):
        """[volume] an hour apart, newest first, for the FLS diagnostic."""
        hours = hours or self.VOLUME_HISTORY
        now = self.tank_level.get(tank, {}).get("volume", 0.0)
        rate = 0.0
        row = self.bir.row(tank, "daily") if self.licensed("bir") else None
        if row and row.get("sales"):
            span = max(1.0, (row["closed"] - row["opened"]) / 3600.0)
            rate = row["sales"] / span
        out = []
        for back in range(hours):
            out.append(max(0.0, now + rate * back))
        return out

    # 281 and A81's figures. "Days Supply of Fuel Remaining" is what the site
    # has divided by what it sells on an average day, which is the one number
    # Fuel Management exists to give.
    def fuel_management(self, tank):
        """[days, inventory, 95% ullage, then the seven daily averages]."""
        level = self.tank_level.get(tank, {})
        volume = level.get("volume", 0.0)
        full = self.limit("60A", tank) or self.limit("604", tank) or 10000.0
        ullage = max(0.0, full * 0.95 - volume)
        week = [readings.fixed(full * 0.10, full * 0.28, "fmsales", tank, day)
                for day in range(7)]
        average = sum(week) / 7.0
        days = (volume / average) if average else 0.0
        return [days, volume, ullage] + week

    def fuel_management_last(self, tank):
        """A81's "LAST SALES" row: what each day of the week actually sold."""
        full = self.limit("60A", tank) or 10000.0
        return [readings.fixed(full * 0.08, full * 0.30, "fmlast", tank, day)
                for day in range(7)]

    def fuel_management_predicted(self, tank):
        """And its "PREDICTED SALES" row, which is what the average and the
        last week together say the next one will do.

        **The blend is measured off the manual's own page rather than
        chosen, and no fixed blend reproduces it.** 576013-610 Rev AC p.27-7
        prints AVG, LAST and PRED for all seven days, and each row implies a
        weight on LAST of `(pred - avg) / (last - avg)`:

            SUN 0.403   MON 0.655   TUE 0.438   WED 0.452
            THR 0.526   FRI 0.583   SAT 0.553

        They do not agree, so the console is doing something these three
        columns do not fully determine -- but they bracket it. This was
        `(2a + b) / 3`, a weight of 0.333, which is **below every one of the
        seven**: on the manual's own figures the real console leans harder on
        last week than that, on every day of it. The least-squares best
        fixed weight over the seven is 0.5062, which is a half to within a
        rounding of the printed integers, and a half's summed error against
        the page is 23.0 gallons where the optimum's is 23.02 and the old
        formula's was 49.0.

        So this is the best fixed blend the page admits and not a formula
        the page states. See FIDELITY X9.
        """
        average = self.fuel_management(tank)[3:10]
        last = self.fuel_management_last(tank)
        return [(a + b) / 2.0 for a, b in zip(average, last)]

    def product_density(self, tank):
        """Pounds per gallon: the PROGRAMMED density, or this tank's own.

        `61E` first, and the tank's own steady reading only where nothing is
        programmed -- "A value of 0 indicates that the density for the
        product in this tank has not been entered". `live_reading` has read
        it that way since Y10 and this did not, so a tank programmed to
        7.2500 showed 7.2500 LBS/GAL on the glass and 6.8045 on the roll and
        the wire, and its MASS was 6.5% out at the same instant. Two closed
        entries already assert this fix -- Y10 and O13a's residue in
        UNKNOWNS -- and both were true of `live_reading` alone. FIDELITY O27.
        """
        return (self.limit("61E", tank)
                or readings.fixed(*self.DENSITY_BAND, "density", tank))

    def product_mass(self, tank):
        """"MASS 20357" against "VOLUME 5329" and "DENSITY 5.9987"."""
        volume = self.tank_level.get(tank, {}).get("volume", 0.0)
        return volume * self.product_density(tank)

    def delivery_density_value(self, tank, which):
        """61F's stored value for that tank and delivery type, as entered.

        "t - Delivery Type (0=next, 1=last)", and a tank that has never been
        given one reads 0 -- which is the same thing the setup manual says of
        the tank's own density: "A value of 0 indicates that the density for
        the product in this tank has not been entered".
        """
        return float(self.delivery_density.get((int(tank), str(which)), 0.0))

    def set_delivery_density(self, tank, which, value):
        """What CHANGE on the NEXT or LAST DELIVERY screen stores, and what
        61F sets over the wire.

        The value is kept as it was ENTERED. 576013-610: "enter any one of
        the three values below from the product's delivery ticket (units are
        not entered): mass per unit volume at reference temperature (actual),
        specific gravity, or API number", and the console "converts the
        entered value to the actual density (but the user entered value is
        displayed)". The wire agrees -- 61F reports "Entered Density,
        relative, actual or API". Which of the three a number is, and the
        conversion, is on no page here: see UNKNOWNS A23.
        """
        self.delivery_density[(int(tank), str(which))] = float(value)
        self.save()
        return float(value)

    def delivery_report_printed(self, tank):
        """The delivery report has printed, so NEXT becomes LAST.

        "This is the density entered in the Next delivery display prior to
        the printing of the delivery report for that delivery", and of NEXT:
        "this value will default to 0 after the delivery report is printed".
        """
        tank = int(tank)
        held = self.delivery_density.pop((tank, "0"), None)
        if held is not None:
            self.delivery_density[(tank, "1")] = held
        return held

    def density_defaulted(self, tank):
        """215's trailing flag: "0=new value, 1=default".

        Nothing here measures a density, so every one of them is the default
        the console was given rather than something a probe read back.
        """
        return True

    def active_alarm_records(self):
        """The alarms and warnings standing right now, for 113.

        "This command will report ALL active alarms and warnings regardless
        of their acknowledgement state" -- so it is the live set and not the
        log, and an alarm the console has never logged a transition for is
        still on it.
        """
        out = []
        for record in self.compute_alarms():
            when = None
            for entry in self.alarm_log:
                if (entry["aa"] + entry["nn"] + entry["tt"]) == record:
                    when = entry["at"]
                    break
            out.append({"aa": record[:2], "nn": record[2:4], "tt": record[4:6],
                        "at": when or time.strftime("%y%m%d%H%M", self.now())})
        return out

    def cleared_alarm_records(self):
        """114: what has gone away, with the state byte that only it carries."""
        return [dict(e) for e in self.alarm_log if e.get("state") == "01"]

    def unacknowledged_alarm_records(self):
        """115: active and not acknowledged. Maintenance Tracker's list."""
        return [r for r in self.active_alarm_records()
                if (r["aa"] + r["nn"] + r["tt"]) not in self.acked]

    @staticmethod
    def _alarm_report_stamp(stamp):
        return alarm_report_stamp(stamp)

    # The alarm family's one row, which five reports draw and two builders
    # used to draw differently. 576013-635 Rev AA p.54 gives 114 a
    # DESCRIPTION column of 17 and puts ALARM TYPE at 31 to make room for
    # STATE, where p.53 gives 113 a column of 21 and puts ALARM TYPE at 35 --
    # so this console had 114 four columns narrower than its siblings. It is
    # not: `tests/console_capture/raw/I11400.bin` is a real console drawing
    # that report with SYSTEM at 4, BATTERY IS OFF at 35 and CLEAR at 56,
    # character for character 111's and 112's row. STATE is a column added
    # to a fixed layout, not a layout of its own. See FIDELITY S18.
    ALARM_ROW_HEAD = ("ID  CATEGORY  DESCRIPTION          ALARM TYPE"
                      "           STATE    DATE    TIME")

    def alarm_row(self, record, described, cell=None):
        """One line of a 111, 112, 113, 114 or 115 report."""
        letter = STATUS_DEVICE_CODE.get(record["aa"], "")
        number = int(record["tt"]) if record["tt"].isdigit() else 0
        ident = f"{letter} {number}" if letter and number else "    "
        when, clock = alarm_report_stamp(time.strptime(record["at"],
                                                       "%y%m%d%H%M"))
        return (f"{ident:<4s}{self.device_category(record):<10.10s}"
                f"{self.device_label(record):<21.21s}"
                f"{described['description'].upper():<21.21s}"
                + (f"{cell:<7s}" if cell is not None else "")
                + f"{when:>8s} {clock:>7s}")

    def alarm_report_lines(self, records, title, state=False):
        """The printed form of 113, 114 and 115.

        `state` is 114's extra column and 114's alone -- which is the whole
        reason these are three reports and not one.
        """
        head = (self.ALARM_ROW_HEAD if state else
                "ID  CATEGORY  DESCRIPTION          ALARM TYPE          "
                "   DATE    TIME")
        rows = [title, head]
        for record in records:
            described = describe_alarms([record["aa"] + record["nn"]
                                         + record["tt"]])
            if not described:
                continue
            cell = None
            if state:
                cell = "CLEAR" if record.get("state") == "01" else "ALARM"
            rows.append(self.alarm_row(record, described[0], cell))
        return rows

    def alarm_report_records(self, records, state=False, headers=True):
        """The packed form: the four station headers, then AA cc NN TT [SS]
        YYMMDDHHmm per alarm.

        113, 114 and 115 carry the headers where 111 and 112 do not, and only
        114 carries SS -- so the record is eighteen characters on two of them
        and twenty on the third.
        """
        out = self.station_header_field() if headers else ""
        for record in records:
            out += record["aa"] + "00" + record["nn"] + record["tt"]
            if state:
                out += record.get("state", "02")
            out += record["at"]
        return out

    def station_header_field(self):
        """The four twenty character blocks the packed reports lead with."""
        out = ""
        for n in range(1, 5):
            # 503, the station header the display form, the printer and
            # every preset use. This read 501, which nothing writes, and
            # sent eighty spaces. FIDELITY S23.
            out += f"{self.text('503', n) or '':<20.20s}"
        return out

    def service_log(self, most=20):
        """116 and 11A: what a service contractor entered and when.

        Nothing here logs a service visit, so this is empty on a console
        nobody has serviced -- which is the honest answer and the same one
        the leak-test flag reports give.
        """
        return list(self.service_entries)[:most]

    def maintenance_report_ready(self):
        """Can the white key's Maintenance Report be reached at all?

        576013-610 Rev AC p.32-1 gives three conditions and gives them in
        one sentence: "The Maintenance Report feature is available in the
        TLS-350 with version 27 software and a NVMEM 203 card installed.
        Maintenance History must be enabled in System Setup for this feature
        to function."

        The first two are the Maintenance Tracker's gate word for word --
        `versions.py` records that beside ECPU2_NVMEM203, "ECPU2, NVMEM203
        and 'Version 27 or later software' ... `mt` reaches E6 at version 27
        and nothing earlier" -- so this asks `supports("mt")` rather than
        spelling a board list out a second time and letting the two drift.

        The third is a setup flag and is this function's own.
        """
        return (self.supports("mt")
                and bool((self.values.get("S56500") or "").strip().strip("0")))

    def maintenance_log(self, start=None, end=None, most=20):
        """119: the maintenance history.

        Its entries are things the console genuinely knows: when the history
        was enabled, and every alarm that has come and gone.
        """
        out = []
        for entry in self.alarm_log:
            kind = "08" if entry.get("state") == "01" else "07"
            out.append({"at": entry["at"], "type": kind,
                        "data": f"{int(entry['tt'] or 0):02d}"
                                f"{entry['aa']}{entry['nn']}"})
        commissioned = self._commissioned or time.mktime(self.now())
        out.append({"at": time.strftime("%y%m%d%H%M",
                                        time.localtime(commissioned)),
                    "type": "01", "data": "000000"})
        if start:
            out = [e for e in out if start <= e["at"][:6] <= (end or "999999")]
        return out[:most]

    def alarm_state_lines(self, priority=True):
        """The rows of a Priority or Non-Priority Alarm History Report."""
        rows = [self.ALARM_ROW_HEAD]
        for record in self.alarm_log:
            if bool(self.priority(record)) != bool(priority):
                continue
            described = describe_alarms([record["aa"] + record["nn"]
                                         + record["tt"]])
            if not described:
                continue
            # "W 3 OTHER SPECIAL WPLLD SHUTDOWN ALM CLEAR": the category is
            # the sensor category the device was set up as, the description is
            # what the site called it, and the alarm type is the message.
            #
            # The stamp is `alarm_report_stamp`'s, which pads neither the
            # month nor the hour -- `1-16-06  8:00AM` on the capture and
            # `1-02-96  4:10AM` on p.54. This drew `01-16-06  8:00 AM`, two
            # characters longer, on the one of the two builders that had the
            # columns right. See FIDELITY S18.
            state = "CLEAR" if record.get("state") == "01" else "ALARM"
            rows.append(self.alarm_row(record, described[0], state))
        # An empty history is the heading and NOTHING under it. This used to
        # append "NO ALARM HISTORY", which is W13's invented phrase and no
        # manual's -- and `tests/console_capture/raw/I11200.bin` is a real
        # console with an empty non-priority history answering with the
        # title, the heading and its trailing blank, no marker at all. The
        # same capture settles the other direction as well: `I11600.bin`,
        # the service report, DOES print a marker for empty, and the word is
        # `NONE`. This console had the two exactly the wrong way round --
        # a marker where the console prints none, and none where it prints
        # one. See FIDELITY W13 and S18.
        return rows

    # "cc - Sensor Category" in i11100
    SENSOR_CATEGORY = {"0": "OTHER", "1": "ANNULAR", "2": "DISPENSER PAN",
                       "3": "MONITOR WELL", "4": "STP SUMP",
                       "5": "PIPING SUMP"}

    # which S-function holds the label and the category, per alarm category
    ALARM_DEVICE = {"02": ("602", None), "03": ("702", "704"),
                    "04": ("707", None), "05": ("802", None),
                    "06": ("760", None), "07": ("712", "713"),
                    "08": ("742", "744"), "12": ("747", "749"),
                    "13": ("74B", None), "14": ("522", None),
                    "21": ("782", None), "26": ("7A2", None),
                    "28": ("722", "724"), "34": ("7C5", None)}

    def device_category(self, record):
        """The CATEGORY column: what kind of place the device is watching."""
        if record["aa"] == "01":
            return "SYSTEM"
        _label, category = self.ALARM_DEVICE.get(record["aa"], (None, None))
        if not category:
            return "OTHER"
        number = int(record["tt"]) if record["tt"].isdigit() else 0
        raw = (self.values.get(f"S{category}{number:02d}") or "").strip()
        return self.SENSOR_CATEGORY.get(raw[-1:], "OTHER")

    def device_label(self, record):
        """The DESCRIPTION column: what the site called the device."""
        code, _category = self.ALARM_DEVICE.get(record["aa"], (None, None))
        if not code:
            return ""
        number = int(record["tt"]) if record["tt"].isdigit() else 0
        return (self.text(code, number) or "").upper()

    def alarm_state_records(self, priority=True):
        """The same history packed: AA cc NN TT SS YYMMDDHHmm."""
        out = ""
        for record in self.alarm_log:
            if bool(self.priority(record)) != bool(priority):
                continue
            out += (record["aa"] + "00" + record["nn"] + record["tt"]
                    + record.get("state", "02") + record["at"])
        return out

    # Which alarms a Contractor's key is needed to acknowledge, and this
    # is empty on purpose. 576013-610 Rev AC ch.33 p.33-2 introduces the
    # requirement and scopes it to a subset in one word -- step 3 of the
    # work session is "Acknowledge any protected alarms" -- and p.32-1
    # speaks of "protected maintenance alarms" as one kind among several.
    # No page on this shelf lists which they are; `grep -i "protected
    # alarm"` across all twenty-six manuals returns those two sentences and
    # nothing else.
    #
    # It was the presence of the BOARD, so a printer `PAPER OUT` on a
    # console with Maintenance Tracker fitted -- the shipped Compliance
    # site preset fits one -- needed a contractor's certification key to
    # clear, and p.29-1 is unconditional about that: "After you correct the
    # cause, you must press the ALARM/TEST button to acknowledge the alarm
    # and clear the display. The system will then display the ALL FUNCTIONS
    # NORMAL message." Whatever the protected set is, that clear cannot be
    # unreachable for every alarm on the console.
    #
    # So the mechanism stays and the list is empty until a page names one.
    # See the refusals audit R6, the alarm-lifecycle audit's "could not
    # check", and UNKNOWNS B13.
    PROTECTED_ALARMS = frozenset()

    def protected(self, record):
        """Does acknowledging this one want a Contractor's key?"""
        return str(record)[:4] in self.PROTECTED_ALARMS

    def acknowledge(self, keyed=False):
        """ALARM/TEST. (silenced, still_live, cleared_from_display)

        `keyed` is a valid Contractor's ID key in the MT Comm card, which a
        console with Maintenance Tracker fitted wants before a protected alarm
        can be acknowledged. Silencing is never protected.
        """
        shown = set(self.compute_alarms())
        live = self.reduce_alarms(self.conditions())
        self.silenced = bool(shown)
        self.acked |= shown
        if self.has("mt") and not keyed:
            # the protected ones stay up; everything else acknowledges the
            # way p.29-1 says it does
            held = {one for one in shown if self.protected(one)}
            if held:
                shown -= held
                self.acked -= held
                if not shown:
                    return len(shown), len(live), 0
        # A failed test is a stored result, so acknowledging it IS correcting
        # the cause, and a shut-down line comes back only if the Line
        # Re-Enable Method says an acknowledgement is what re-enables it.
        self.posted -= shown
        self.leaks.re_enable()
        # the FILTERED view again: an alarm Appendix A is still holding is
        # one the console is still showing, and acknowledging it must not
        # unlatch what has not gone off the display yet
        live = self.reduce_alarms(self.conditions())
        gone = shown - live
        self.latched -= gone
        self.acked -= gone
        return len(shown), len(live), len(gone)


def describe_alarms(records):
    """AANNTT -> readable text, using the console's own tables."""
    out = []
    s = "".join(records)
    for i in range(0, len(s) - len(s) % 6, 6):
        aa, nn, tt = s[i:i + 2], s[i + 2:i + 4], s[i + 4:i + 6]
        if aa == "00":
            continue
        cat = STATUS_CATEGORIES.get(aa, f"Category {aa}")
        desc = (STATUS_TYPES.get(aa) or {}).get(nn, f"Alarm type {nn}")
        word = STATUS_DEVICE_WORD.get(aa)
        try:
            n = int(tt)
        except ValueError:
            n = 0
        where = f"{word} {n}" if word and n else cat
        # What the console itself puts on the second line: the device code
        # from Table 29-1, its number, and the message in capitals,
        # "T 3:LOW PRODUCT ALARM".
        #
        # No space after the colon, and a real console settled it against
        # both manuals. 576013-610 Rev AC p.29-2 prints `T3: LOW PRODUCT
        # ALARM` and 576013-939's Quick Help draws fifty-five more device
        # lines that all carry the space, so a panel audit called this a
        # defect and it was changed. The tape from a real site prints, in
        # this very report:
        #
        #     SYSTEM STATUS REPORT
        #     - - - - - -  - - - - - -
        #     L 6:FUEL ALARM
        #
        # which is the machine, not a typesetter. Changed back, and the
        # near miss is written down in CLOSED U13 -- fifty-six drawings
        # agreeing with each other is still only a document.
        letter = STATUS_DEVICE_CODE.get(aa)
        screen = (f"{letter} {n}:{desc.upper()}" if letter and n
                  else desc.upper())
        out.append({"aa": aa, "nn": nn, "tt": tt, "where": where,
                    "description": desc, "text": f"{where}: {desc}",
                    "screen": screen})
    return out
