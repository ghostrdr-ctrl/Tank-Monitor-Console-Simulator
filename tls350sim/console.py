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
"""The console's state: what is installed, what is programmed, what is wrong.

Self-contained on purpose. This simulator is something you point a real tool
at, so it does not import that tool: if both read the same tables, a bug in
the tables is invisible to the test. Everything it needs was vendored into
consoledata.json.
"""
import json
import os
import threading
import zlib
import time

from .clock import clock_hhmm, clock_words

from . import accuchart as _accuchart
from . import bir as _bir
from . import csld as _csld
from . import autodial as _autodial
from . import delivery as _delivery
from . import leaktest
from . import pressure as _pressure
from .pressure import FLOOR as FLOOR_PSI
from . import packed
from . import readings
from . import versions


def leaktest_rate(key):
    """How the console writes the rate a test is looking for."""
    return {"gross": "3.0 GAL/HR", "periodic": "0.2 GAL/HR",
            "annual": "0.1 GAL/HR"}[key]


_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "consoledata.json"), encoding="utf-8") as _fh:
    DATA = json.load(_fh)

STATUS_CATEGORIES = DATA["status_categories"]
STATUS_TYPES = DATA["status_types"]
STATUS_DEVICE_WORD = DATA["status_device_word"]
STATUS_DEVICE_CODE = DATA["status_device_code"]
DEVICE_PREFIXED = {int(x) for x in DATA["device_prefixed"]}
MULTI_DEVICE = {int(x) for x in DATA["multi_device"]}
FIELDS = DATA["fields"]
SETUP_MENU = DATA["setup_menu"]

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
SLOT_POSITIONS = {"601": 4, "701": 8, "706": 5, "711": 5, "741": 8, "746": 5,
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
    ("probe",   "Four-Input Probe Module",              "329356-002", "is",    4,  4),
    ("liquid",  "Eight-Input Liquid Sensor Module",     "329358-001", "is",    8,  2),
    ("vapor",   "Five-Input Vapor Sensor Module",       "329357-001", "is",    5,  3),
    ("gw",      "Five-Input Groundwater Sensor Module", "329399-001", "is",    5,  3),
    ("2wire",   "Eight-Input Type A Sensor Module",     "329956-001", "is",    8,  2),
    ("3wire",   "Six-Input Type B Sensor Module",       "329950-001", "is",    5,  3),
    ("smart",   "Eight-Input Smart Sensor Module",      "329356-004", "is",    8,  2),
    ("vlld",    "Line Leak Interface Module",           "",           "power", 4,  2),
    ("plld",    "Three-Output PLLD Controller",         "330374-001", "power", 6,  1),
    ("wplld",   "WPLLD Controller Module",              "",           "power", 3,  3),
    ("io",      "Two-Input/Two-Relay Output Interface", "329360-001", "power", 2,  4),
    ("relay",   "Four-Relay Output Module",             "329359-001", "power", 4,  4),
    ("pump",    "Four-Input Pump Sense Module",         "329999-001", "power", 4,  4),
    ("pumpmon", "Pump Relay Monitor Module",            "847490-504", "power", 4,  4),
    ("rs232",   "RS-232 Interface Module",              "329362-001", "comm",  0,  3),
    ("modem",   "SiteFax Fax/Modem Interface Module",   "330149-002", "comm",  0,  3),
    ("mt",      "RS-232 Single Port, MT ID Resistor",   "329362-005", "comm",  0,  1),
    ("vmc",     "VMCI Interface Module",                "",           "comm", 18,  1),
    # 577013-528 p.5: the dual-port slot-4 module whose RJ-45 half is the
    # Remote Display (27.4K ID); and the two DIM families of 576013-623
    # ch.17: EDIMs in the comm bay, MDIMs in the power bay, which is how
    # metered transactions reach BIR at all.
    ("rdu",     "Remote Display Interface",             "330586-011", "comm",  0,  1),
    ("edim",    "Electronic Dispenser Interface",       "330280-001", "comm",  0,  3),
    ("mdim",    "Mechanical Dispenser Interface",       "331001-003", "power", 0,  3),
    # The Universal Sensor Module is REAL in the firmware and was apparently
    # never stocked: it has an ID resistance (30.1K in Table 6-1), a module
    # type code (11 in function 102), an alarm category (13) and a full serial
    # API at 34B, 34C, 74B-74E and B4B, all tagged Version 4 -- and no part
    # number, no input count, no bay, no setup chapter and no entry in any
    # Veeder-Root catalogue, with the TLS-450 manual marking the whole feature
    # OBSOLETE. So: fittable, because the console's program plainly knows how
    # to talk to one, and absent from every preset, because nobody could buy
    # it. See UNKNOWNS C3.
    ("universal", "Universal Sensor Module",             "",           "is",    8,  2),
]

# The S-Module features, which are software keys rather than cards: the
# datasheet's own Software Enhancement Modules, plus the line leak test keys
# the setup manual mentions ("this message will not appear unless the 0.20
# Repetitive PLLD software module key is installed in your system").
SOFTWARE_MODULES = [
    ("csld",    "Continuous Statistical Leak Detection", "330160-002"),
    ("fuelman", "Fuel Manager",                          "330160-003"),
    ("bir",     "Business Inventory Reconciliation",     ""),
    ("plld020", "0.20 Repetitive PLLD/WPLLD",            "330160-050"),
    ("plld010", "0.10 On Demand PLLD/WPLLD",             "330160-010"),
    ("isd",     "In-Station Diagnostics",                "330160-004"),
    # Pressure Management Control is its own feature and its own key, which
    # 576013-635 is careful about all through section 7.7: some ISD functions
    # say "PMC feature required", some say "ISD feature required", V47 says
    # "ISD or PMC" and V50 says "ISD and PMC". A console with one and not the
    # other answers a different set of codes, so they cannot be one flag.
    ("pmc",     "Pressure Management Control",           "330160-005"),
]
SOFTWARE_NAME = {k: name for k, name, _p in SOFTWARE_MODULES}
SOFTWARE_PART = {k: part for k, _n, part in SOFTWARE_MODULES}

# The card cage slots each compartment has. "Card Cage Slots in Communication
# Bay: 1 2 3 4"; the datasheet's interface module tables are each headed
# "Limit 8 per console".
BAY_SLOTS = {"comm": 4, "power": 8, "is": 8, "sw": 1}
BAY_NAME = {"comm": "Communication Bay", "power": "Power Bay",
            "is": "Intrinsically Safe Bay", "sw": "Software Module"}

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
    "2wire": 68000,       # "Type A Sensor Interface 68K"
    "plld": 100000,       # "PLLD Controller 100K"
    "wplld": 200000,      # "WPLLD Controller 200K"
    "liquid": 200000,     # "Interstitial/Liquid Sensor Interface 200K"
    "gw": 270000,         # "Groundwater Sensor 270K"
    "mt": 402000,         # "Maintenance Tracker (Single and Dual Port) 402K"
    "vmc": 82500,         # nearest documented row: "ISD Comm 82.5K"
    "pumpmon": 33000,     # not in Table 6-1; the pump sense row is its family
}

# What an empty slot reads, from the same function 102 sample: "UNUSED
# 10191362 10329900" in the intrinsically safe bay and "COMM 4-6 UNUSED
# 15000000 15000000" in the communication bay. An open circuit, in other
# words, and the I.S. bay measures it through its own barrier.
EMPTY_OHMS = {"is": 10200000, "power": 15000000, "comm": 15000000}

# what a slot screen calls each card: "SLOT 1 4 PROBE/ G. T." is the
# manual's own shorthand, and 24 characters is all there is
MODULE_SHORT = {
    "probe": "4 PROBE", "liquid": "8 LIQUID", "vapor": "5 VAPOR",
    "gw": "5 GRND WATER", "2wire": "8 TYPE A", "3wire": "5 TYPE B",
    "smart": "8 SMART", "vlld": "LINE LEAK", "plld": "PLLD CNTRL",
    "wplld": "WPLLD CNTRL", "io": "2 IN/2 RELAY", "relay": "4 RELAY",
    "pump": "4 PUMP SENSE", "pumpmon": "PUMP RELAY", "rs232": "RS-232",
    "rdu": "REMOTE DISP", "edim": "EDIM", "mdim": "MDIM",
    "modem": "SITEFAX", "mt": "MT COMM", "vmc": "VMCI",
}

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
    "COMMUNICATION SETUP": ("rs232", "modem", "mt", "vmc"),
    # SYSTEM SETUP and ARCHIVE UTILITY are always present.
}

# Which sensor module a sensor-alarm category belongs to, so the bench only
# offers sensors the console could actually have.
SENSOR_MODULE_CATEGORY = {"liquid": "03", "vapor": "04", "gw": "07",
                          "2wire": "08", "3wire": "12", "smart": "28"}

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

# and how the console words each one on a status screen
SENSOR_STATE_WORDS = {
    "normal": "NORMAL", "fuel": "FUEL ALARM", "out": "SENSOR OUT ALARM",
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
FEATURE_LINE = {"CSLD": "csld", "0.10 REPETITIVE": "plld",
                "0.20 REPETITIVE": "plld", "PLLD": "plld", "WPLLD": "wplld",
                "VLLD": "vlld", "MAINTENANCE TRACKER": "mt"}

MODULE_FEATURES = {
    "probe": ["PERIODIC IN-TANK TESTS", "ANNUAL IN-TANK TESTS", "CSLD"],
    "plld": ["PLLD", "   0.10 REPETITIVE", "   0.20 REPETITIVE"],
    "wplld": ["WPLLD"],
    "vlld": ["VLLD"],
    "vmc": ["VMC"],
    "mt": ["MAINTENANCE TRACKER"],
}

# Table 29-1's device code -> the function that holds that device's label
DEVICE_LABEL_CODE = {"T": "602", "L": "702", "V": "707", "G": "712",
                     "C": "742", "H": "747", "s": "722", "P": "760",
                     "Q": "782", "W": "7A2", "R": "807", "I": "802",
                     "r": "7C5"}

# and what to call one nobody has labelled
DEVICE_WORD = {"T": "TANK", "L": "LIQUID SENSOR", "V": "VAPOR SENSOR",
               "G": "GRND WATER", "C": "2-WIRE CL", "H": "3-WIRE CL",
               "s": "SMART SENSOR", "P": "VLLD LINE", "Q": "PLLD LINE",
               "W": "WPLLD LINE", "R": "RELAY", "I": "INPUT",
               "r": "PUMP RELAY", "g": "GRND TEMP"}


class Console:
    """One simulated TLS-350."""

    def __init__(self, state_path=None):
        self.state_path = state_path
        self.values = {}          # "S60201" -> stored ASCII data
        # how many of each card is in the cage, not just whether one is
        # a bare console: a probe card and something to talk through. Every
        # other card, and every software option, is fitted on the bench.
        self.modules = {"probe": 1, "rs232": 1}
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
        self.acked = set()        # acknowledged, waiting for the cause to go
        self.latched = set()      # alarms held on the display after the cause
        self._seen = set()        # conditions as of the last look
        self.alarm_log = []       # {aa, nn, tt, at} newest first, for I206
        self.service_entries = []  # what a contractor entered, for 116/11A
        self.autodial_alarm = {}   # receiver -> whether 52D says it is in alarm
        self.user_service_codes = []   # 8A2's "USER DEFINED LABEL" rows
        self.mt_keys = []          # 8A3's Contractor ID keys
        self.blocked_keys = []     # and the ones 8A4 has blocked
        self.service_sessions = []  # 11B's start/end pairs
        self.vmc_serials = {}     # VMC controller -> its serial number
        # Settings the panel programmes that no serial function reads: the
        # Setup Manual draws the screen, the Serial Interface Manual has no
        # code for it, so the console holds it rather than the wire.
        self.settings = {}        # (key, device) -> what was entered
        self.vmc_state = {}       # (controller, side) -> what it reads
        self.recon_kind = "shift"   # the period Reconciliation Mode is on
        self.recon_previous = False  # CURRENT or PREVIOUS, on that mode
        self.silenced = False     # the audible alarm, after ALARM/TEST
        self.out_of_paper = False  # the roll run out: nothing prints, and
                                   # the console says so, "Printer out of
                                   # Paper" is a system alarm of its own
        self.clock_offset = 0.0   # the console keeps its own date and time
        self.relays = {}          # output relays, as TEST OUTPUT RELAYS left them
        self.tank_leak = {}       # tank -> gallons an hour going missing
        self.probe_out = set()    # tanks whose probe is unplugged at the riser
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
        self.isd_events = []      # (at, line1, line2) for the misc event log
        # A failed shutdown-class test disables dispensing until the alarm
        # clears or a technician overrides it from the panel: ALARM/TEST
        # three times, then the OVERRIDE SHUTDOWN & LOG confirmation.
        self.isd_override = False
        # bench faults on the comm gear: the remote display link, and the
        # DIM link the metered transactions arrive over
        self.rdu_fault = False
        self.dim_fault = False
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
        self.printed_deliveries = []   # drops waiting for the printer
        self.meters = {}          # BIR meter number -> the tank it draws from
        self.meter_map = {}       # and 7B1's bus, slot and fueling position
        self.meter_offsets = {}   # 7B4: meter -> {fp, tank, pct}
        self.vmc_fuel_pos = {}    # 8C3: VMC -> {"A": position, "B": position}
        self.generator_log = {}   # 404: tank -> [runs feeding a generator]
        self.dim_faults = {}      # BA1: DIM port -> [comm faults]
        self.apm_cleared = {}     # VA7: test type -> when it was last cleared
        self.apm_event_log = []   # VA8: what the APM has done
        self.vmci_sub_log = []    # VA5: sub-alarms under a VMCI alarm
        self.started = time.mktime(time.localtime())   # 908 counts from here
        self.receiver_reports = {}   # 52A: receiver -> {report: "01"/"00"}
        self.receiver_dial = {}      # 52B: receiver -> method digit + when
        self.receiver_alarms = {}    # 52C: receiver -> [(aa, nn, tt)]
        self.meter_flow = {}      # meter -> gallons an hour it is selling
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
        self.lines = _pressure.Lines(self)
        self.deliveries = _delivery.Deliveries(self)
        self.loads = _delivery.Loads(self)
        self.csld = _csld.CSLD(self)
        self.bir = _bir.BIR(self)
        self.accuchart = _accuchart.AccuChart(self)
        self.autodial = _autodial.Autodial(self)
        self.accuchart_log = []   # (tank, when) each time a chart was applied
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
        self.meters.clear()
        self.chart_code = self.chart_code_set = ""
        self.serial_number = self.wm_office = ""
        self.silenced = False
        self.out_of_paper = False
        self.clock_set = True
        self.cover_open = False
        self.selftest_error = False
        self.isd_forced = {}
        self.isd_forced_at = {}
        self.isd_events = []
        self.isd_override = False
        self.rdu_fault = False
        self.dim_fault = False
        self.isd_hose_map = {}
        self.modules = {"probe": 1, "rs232": 1}
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
        self.lines = _pressure.Lines(self)
        self.deliveries = _delivery.Deliveries(self)
        self.loads = _delivery.Loads(self)
        self.csld = _csld.CSLD(self)
        self.bir = _bir.BIR(self)
        self.accuchart = _accuchart.AccuChart(self)
        self.autodial = _autodial.Autodial(self)
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
                "water_vol": st.get("water", 0.0) * 12,
                "temp": 55.0}
            for n, st in self.tank_level.items()}
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
        if self._ram_held:
            return "warm"
        self.cold_boot()
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
        board, software = self.board, dict(self.software)
        tanks = dict(self.tank_level)
        sensors = dict(self.sensor_state)
        t_leak, l_leak = dict(self.tank_leak), dict(self.line_leak)
        p_leak = dict(self.pump_leak)
        p_out = set(self.probe_out)
        dip = self.rs232_security
        meterf = dict(self.meter_flow)
        self.reset(keep_clock=False)
        self.modules = modules
        self.board, self.software = board, software
        self.tank_level = tanks
        self.sensor_state = sensors
        self.tank_leak, self.line_leak = t_leak, l_leak
        self.pump_leak = p_leak
        self.probe_out = p_out
        self.rs232_security = dip
        self.meter_flow = meterf
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
            self.tank_level = {int(k): v for k, v in blob.get("tanks", {}).items()}
            self.sensor_state = {tuple(k.split("|")): v
                                 for k, v in blob.get("sensors", {}).items()}
            self.clock_offset = blob.get("clock_offset", 0.0)
            self.chart_code = blob.get("chart_code", "")
            self.chart_code_set = blob.get("chart_code_set", "")
            self.serial_number = blob.get("serial_number", "")
            self.wm_office = blob.get("wm_office", "")
            self.tank_capacity = {int(k): v for k, v
                                  in blob.get("tank_capacity", {}).items()}
            self.chart_audit = {int(k): v for k, v
                                in blob.get("chart_audit", {}).items()}
            self.software = blob.get("software", self.software)
            self.version = int(blob.get("version", self.version))
            self.board = blob.get("board", self.board)
            self.meters = {int(k): int(v)
                           for k, v in blob.get("meters", {}).items()}
            self.meter_map = {int(k): v for k, v
                              in blob.get("meter_map", {}).items()}
            self.meter_offsets = {int(k): v for k, v
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
        except Exception as e:
            print(f"[sim] could not load state: {e}")

    def save(self):
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump({"values": self.values, "modules": self.modules,
                           "tanks": self.tank_level,
                           "sensors": {"|".join(k): v
                                       for k, v in self.sensor_state.items()},
                           "clock_offset": self.clock_offset,
                           "chart_code": self.chart_code,
                           "chart_code_set": self.chart_code_set,
                           "serial_number": self.serial_number,
                           "wm_office": self.wm_office,
                           "tank_capacity": self.tank_capacity,
                           "chart_audit": self.chart_audit,
                           "software": self.software,
                           "version": self.version,
                           "board": self.board,
                           "meters": self.meters,
                           "meter_map": self.meter_map,
                           "meter_offsets": self.meter_offsets,
                           "vmc_fuel_pos": self.vmc_fuel_pos,
                           "rcvr_reports": self.receiver_reports,
                           "rcvr_dial": self.receiver_dial,
                           "rcvr_alarms": {k: [list(x) for x in v] for k, v
                                           in self.receiver_alarms.items()}},
                          fh, indent=1)
        except Exception as e:
            print(f"[sim] could not save state: {e}")

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

    def knows_module(self, module):
        """Is that card one this software can drive?"""
        return self.supports(versions.MODULE_FEATURE.get(module))

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
        """
        return self.fitted(module) if self.knows_module(module) else 0

    def fitted(self, module):
        """How many of that card are physically in the cage."""
        return int(self.modules.get(module) or 0)

    def capacity(self, module):
        """How many devices the cage carries of that kind.

        Two eight-input liquid sensor modules is sixteen sensors, and each
        module's own config screen switches on the eight positions it has.
        """
        return MODULE_WIRES.get(module, 0) * self.count(module)

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

    def bay_free(self, bay):
        return BAY_SLOTS.get(bay, 0) - self.bay_used(bay)

    def fits(self, module, count):
        """Would that many fit, in its own limit, and in its bay?"""
        if count < 0 or count > self.most(module):
            return False
        bay = MODULE_BAY.get(module, "power")
        return (self.bay_used(bay) - self.fitted(module) + count
                <= BAY_SLOTS.get(bay, 0))

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
        cond = step.get("when")
        if not cond:
            return True
        if cond.get("tanks") and not self.programmed_tanks():
            return False
        if cond.get("software") and not self.licensed(cond["software"]):
            return False
        if cond.get("isd_hoses") and not self.isd_hoses():
            # "appears only after completing Fuel Hose Table Setup"
            return False
        if cond.get("vst_processor") and (self.values.get("SV4000")
                                          or "00") != "01":
            # PMC SETUP: "the vapor processor type VST must have been
            # selected in EVR/ISD setup to access PMC setup" (937-J p.27)
            return False
        if cond.get("feature") and not self.supports(cond["feature"]):
            # a screen for something this software version predates
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
            value = raw[2:] if self.is_prefixed(code[1:4]) and len(raw) > 2                 else raw
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
                return "REPORT DATE: " + time.strftime(
                    "%b %d %Y", self.now()).upper()
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
            row = self.bir.last(device) or self.bir.current(device)
            what = token[6:]
            if what == "gross":
                change = row["physical"] - row["opening"]
                return f"{change:8.0f} GALS"
            return f"{row.get(what, 0.0):8.0f} GALS"
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
            return f"RELAY {device} " + ("ON" if self.relays.get(device)
                                         else "OFF")

        st = self.tank_level.get(device)
        if st is None:
            return ""
        full = self.full_volume(device)
        diam = self.limit("607", device) or 96.0
        vol, water = st.get("volume", 0.0), st.get("water", 0.0)
        frac = max(0.0, min(1.0, vol / full)) if full else 0.0
        dens = self.limit("61E", device) or 6.0
        if token == "volume":
            return f"{vol:8.0f} GALS"
        if token == "tc_volume":
            return f"{self.tc_volume(device):8.0f} GALS"
        if token == "ullage":
            return f"{max(full - vol, 0):8.0f} GALS"
        if token == "ullage95":
            return f"{max(full * 0.95 - vol, 0):8.0f} GALS"
        if token == "height":
            return f"{frac * diam:8.2f} INCHES"
        if token == "water":
            return f"{water:8.2f} INCHES"
        if token == "water_vol":
            return f"{water * 12:8.0f} GALS"
        if token == "temperature":
            return f"{55.0:8.1f} DEG F"
        if token == "density":
            return f"{dens:8.4f} LBS/GAL"
        if token == "mass":
            return f"{vol * dens:8.0f} LBS"
        if token == "delivery":
            # "To view the inventory increase for a tank (the last delivery
            # amount) ... DELIVERY = XXXXX (UNITS)"
            if self.deliveries.in_progress(device):
                return "DELIVERY IN PROGRESS"
            # A tank that has taken no delivery has taken nought gallons.
            # This is a READING, and a console draws a reading as a number:
            # 576013-610 Rev AC p.31 gives it no other form.
            last = self.deliveries.last(device)
            return f"{last.amount if last else 0:8.0f} GALS"
        if token in ("next_density", "last_density"):
            return f"{dens:8.4f} LBS/GAL" if dens else ""
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

    def recon_reading(self, what, tank, kind=None, previous=None):
        """One line of a Reconciliation Mode report, as the panel shows it.

        The mode displays the same numbers the report prints, "one item at a
        time", so the screens and the printout read off one row.
        """
        kind = kind or self.recon_kind
        previous = self.recon_previous if previous is None else previous
        row = self.bir.row(tank, kind, previous)
        if row is None:
            return "NO DATA AVAILABLE"
        if what in ("open_date", "open_time", "close_date", "close_time"):
            when = row["opened"] if what.startswith("open") else row["closed"]
            if what.endswith("date"):
                return time.strftime("%b %d, %Y",
                                     time.localtime(when)).upper()
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
        """The Mag Sump Leak Test screens, 576013-610 Rev AC p.82 and p.86-89.

        `ht_temp` is the sump as it stands, height on the left and
        temperature on the right. `rates` is the pair the manual draws
        together and gives four states for: UNKNOWN before a test, COMPUTING
        for the first ten minutes of the measuring phase, a rate after that,
        and TMP STABLE with a count of minutes once the temperature settles.
        `status` is the outcome, or the phase while one is running.
        `last_passed` is the results function's own screen.
        """
        phase = self.control_phase_of("sump", sensor, "00")
        label = self.text("722", sensor) or f"SUMP {sensor}"
        height = readings.fixed(0.0, 24.0, "sumpht", sensor)
        temp = readings.fixed(60.0, 80.0, "sumptemp", sensor)
        if what == "ht_temp":
            left = f"{height:7.3f} IN"
            return f"{left}{f'{temp:.1f} F'.rjust(24 - len(left))}"
        if what == "rates":
            head = f"s {sensor}: "
            if phase == "00":
                return (head + "TEMP RATE: UNKNOWN" + chr(10)
                        + "LEAK RATE: UNKNOWN")
            if phase == "02":
                return (head + "TEMP RATE: COMPUTING" + chr(10)
                        + "LEAK RATE: COMPUTING")
            rate = readings.fixed(0.0, 12.0, "sumptrate", sensor)
            leak = readings.fixed(0.0, 0.05, "sumplrate", sensor)
            return (head + f"TEMP RATE: {rate:.1f} F/HR" + chr(10)
                    + f"LEAK RATE: {leak:.4f} IN./HR")
        if what == "status":
            word = {"00": "NO TEST DATA AVALIABLE", "01": "TEST ABORTED",
                    "02": "STATUS: FILL SUMP",
                    "03": "STATUS: MEASURING HEIGHT",
                    "04": "TEST PASSED"}.get(phase, "NO TEST DATA AVALIABLE")
            if phase == "00":
                return f"s {sensor}: {label}" + chr(10) + word
            return (f"s {sensor}: {self.sump_stamp(sensor)}" + chr(10) + word)
        if what == "last_passed":
            return (f"s {sensor}: {self.sump_stamp(sensor)}" + chr(10)
                    + "LAST PASSED TEST")
        return ""

    def sump_stamp(self, sensor):
        """"2-19-05      9:43AM": the date and time a sump test carries."""
        t = self.now()
        date = time.strftime("%m-%d-%y", t).lstrip("0")
        clock = time.strftime("%I:%M%p", t).lstrip("0")
        # "s 1: " takes five of the twenty four columns
        return f"{date}{clock.rjust(19 - len(date))}"

    def vmc_head(self, number, side=None):
        """"x 1: 005830 SIDE A": the device letter, its serial and the side."""
        serial = self.vmc_serials.get(int(number), "")
        head = f"x {number}: {self.vmc_serial(number)}".rstrip()
        return f"{head} SIDE {side}" if side else head

    def vmc_numbers(self):
        """Every controller the interface module can carry."""
        return list(range(1, max(self.capacity("vmc"), 1) + 1))

    def stick_height(self, tank):
        """"fuel height (without tilt) + stick offset", clamped to the tank.

        "If the stick height is less than zero, it will be set to zero. If the
        stick height is greater than tank diameter, it will be set to tank
        diameter."
        """
        full = self.full_volume(tank) or 0.0
        diameter = self.limit("607", tank) or 96.0
        volume = self.tank_level.get(tank, {}).get("volume", 0.0)
        height = (volume / full if full else 0.0) * diameter
        height += self.limit("60C", tank) or 0.0
        return max(0.0, min(height, diameter))

    def volume_at(self, tank, height):
        """What the tank chart says is in the tank at that height.

        A charted tank interpolates between its own points; an uncharted one
        is the straight line the console draws through full volume.
        """
        diameter = self.limit("607", tank) or 96.0
        full = self.full_volume(tank) or 0.0
        points = sorted(self.chart_points(tank))
        if not points:
            return full * max(0.0, min(height, diameter)) / diameter             if diameter else 0.0
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

    def leak_history_lines(self, tank):
        """The same history, in the columns I207 prints it in."""
        full = self.full_volume(tank) or 0.0
        head = ("TEST START TIME            HOURS    VOLUME"
                "   % VOLUME   TEST TYPE")

        def block(title, records):
            out = [title]
            if not records:
                out.append("NO TEST PASSED")
                return out
            out.append(head)
            for record in records:
                pct = (record.volume / full * 100.0) if full else 0.0
                kind = "CSLD" if self.csld.enabled(tank) else "STANDARD"
                out.append(f"{clock_words(record.started):22s}"
                           f"{record.hours:8.0f}{record.volume:10.0f}"
                           f"{pct:11.1f}{kind:>12s}")
            return out

        found = self.leak_history_records(tank)
        out = []
        for rate_key, name in (("gross", "GROSS"), ("annual", "ANNUAL"),
                               ("periodic", "PERIODIC")):
            last = [r for what, _n, r in found
                    if what == "00" and r.rate_key == rate_key]
            out += block(f"LAST {name} TEST PASSED:", last)
            if rate_key != "gross":
                fullest = [r for what, _n, r in found
                           if what == "01" and r.rate_key == rate_key]
                out += block(f"FULLEST {name} TEST PASS", fullest)
        monthly = [r for what, _n, r in found if what == "02"]
        out += block("FULLEST PERIODIC TEST PASSED EACH MONTH:", monthly)
        return out

    def setting(self, key, device=0, default=""):
        """One of the console's own settings, or what it reads out of the box."""
        return self.settings.get((key, int(device)), default)

    def set_setting(self, key, value, device=0):
        self.settings[(key, int(device))] = value
        return value

    def tank_count(self):
        """How many tanks this console actually has."""
        return len(self.tank_level or self.programmed_tanks() or [1])

    def receivers(self):
        """A TLS-350 addresses six autodial receivers, 52D's own range."""
        return [1, 2, 3, 4, 5, 6]

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

    # ---- the eleven codes Revision Y added ---------------------------------
    def tc_volume(self, tank):
        """Volume corrected to 60F, which several reports want by name.

        This used to be spelled `vol * 0.998` inline in the reading path and
        nowhere else. 237, 238, 239 and 23A all print a TC VOLUME column, so
        it is a method now rather than four more copies of the same constant.
        """
        st = self.tank_level.get(int(tank))
        if st is None:
            return 0.0
        return st.get("volume", 0.0) * 0.998

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
        """VA8: what the APM has done lately. Nothing, on a quiet bench."""
        return list(self.apm_event_log)

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

    def dim_ports(self):
        """BA1: whether each DIM port is talking, and its fault history.

        A console with no DIM card is not in fault -- it has no port to be in
        fault ON -- so an empty cage reports nothing rather than reporting a
        failure that is really an absence.
        """
        if not self.has("dim"):
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
        codes, words = {}, {}
        for i, name in enumerate(VP_STATUS_FIELDS):
            # a processor nobody has tested reads NO TEST rather than PASS;
            # claiming a pass the console never made is the one answer a
            # diagnostic must not give
            value = 3 if running else 0
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
        return {"version": "01.03",
                "type": self.vapor_processor_type(),
                "tested": time.mktime(self.now()),
                "status": words, "codes": codes, "figures": figures}

    def vapor_processor_type(self):
        """The words V82 prints for the processor fitted."""
        held = (self.values.get("SVC200") or "").strip()
        return {"1": "VST ECS PROCESSOR",
                "2": "ARID PERMEATOR"}.get(held[:1], "VST ECS PROCESSOR")

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

    def meter_offset(self, meter):
        """The calibration offset that applies to one meter, as a percent.

        Two codes set this and the specific one wins: S7B400 is "Set
        INDIVIDUAL Meter Offset" and carries a fueling position, a meter and
        a tank, where S7B200 is one figure for the site. A meter with its own
        offset uses it; every other meter uses the site's.
        """
        own = self.meter_offsets.get(meter)
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
            except Exception:
                return 0.0

    def fueling_position(self, meter):
        """7B1's FP column, which nothing but the map knows."""
        return (self.meter_map.get(meter) or {}).get("fp")

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

    def corrective_actions(self, tank, analysis):
        """What the Variance Analysis Report tells the site to go and do.

        "Corrective action for tank chart alarm, calibration failure, or
        failed tank or line tests": so each one that is standing puts its
        own line on the report, against the tank it belongs to.
        """
        out = []
        alarms = {r[2:4] for r in self.compute_alarms()
                  if r[:2] == "02" and int(r[4:6]) == tank}
        if "18" in alarms:                      # AccuChart calibration warning
            out.append("RECALIBRATE TANK CHART")
            out.append(f"T{tank}")
        if alarms & {"0D", "0E", "0F"}:         # gross, periodic, annual fail
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
                        "smart": "723"}

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
        """SENSOR STATUS for one sensor, in the console's own words."""
        if not self.has(module):
            return ""
        state = self.sensor_state.get((module, str(number)), "normal")
        if state != "normal" and not self.sensor_alarm_allowed(module, number,
                                                               state):
            # the wire cannot say that, so the console does not either
            state = "normal"
        words = (SMART_STATE_WORDS if module == "smart"
                 else SENSOR_STATE_WORDS)
        return words.get(state, state.upper())

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
                # looks like when nobody was there
                self.power_off = now - 12 * 60.0
        was, self._last_console = self._last_console, now
        hours = (now - was) / 3600.0 if was is not None else 0.0
        # BIR first, so a shift opens on what the probe read before anything
        # in this interval moved the level, and every engine gets its look
        # even on the first tick, when no time has passed yet.
        self.bir.tick()
        for tank, st in self.tank_level.items():
            leak = self.tank_leak.get(tank)
            if leak and hours > 0:
                st["volume"] = max(0.0, st.get("volume", 0.0) - leak * hours)
        self.deliveries.tick()
        self.loads.tick()
        self.leaks.tick()
        self.csld.tick()
        self.accuchart.tick()
        self.autodial.tick()

    # ---- the leak test reports a tool asks for ------------------------------
    def leaks_results_report(self, tanks):
        """I208, PREVIOUS IN TANK LEAK TEST RESULTS, display format."""
        out = ["PREVIOUS IN TANK LEAK TEST RESULTS", ""]
        for tank in tanks:
            label = self.text("602", tank) or f"TANK {tank}"
            out.append(f"TANK {tank} {label}")
            out.append("TEST TYPE START TIME            "
                       "RESULT   RATE  HOURS   VOLUME")
            results = self.leaks.results.get(("tank", tank)) or {}
            if not results:
                out.append("  NO TEST DATA AVAILABLE")
            for _key, res in sorted(results.items()):
                out.append(res.line())
            out.append("")
        return chr(10).join(out)

    def leaks_results_record(self, tanks):
        """I208 computer format: TT NN tt mm YYMMDDHHmm RR rate hours volume."""
        out = []
        for tank in tanks:
            results = self.leaks.results.get(("tank", tank)) or {}
            out.append(f"{tank:02d}{len(results):02X}")
            for key, res in sorted(results.items()):
                out.append(leaktest.TYPE_CODE[key] + "00"
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
            out.append(f"TANK {tank} {label}")
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
        self.bir.delivered(tank, record.amount)

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
        self.clock_offset = want - time.time()
        self.clock_set = True
        return True

    def clock_text(self):
        """The status line, in the format programmed at DATE/TIME FORMAT."""
        t = self.now()
        fmt = (self.values.get("S50F00") or "01").strip()[-2:]
        if fmt == "02":
            return time.strftime("%b %d %Y %H:%M:%S", t).upper()
        if fmt == "03":
            return time.strftime("%m-%d-%y %I:%M:%S %p", t).upper()
        if fmt == "04":
            return time.strftime("%m-%d-%y %H:%M:%S", t)
        if fmt == "05":
            return time.strftime("%d-%m-%y %H:%M:%S", t)
        if fmt == "06":
            return time.strftime("%y-%m-%d %H:%M:%S", t)
        return clock_words(t, seconds=True)

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
            if self.licensed(key):
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
                 "SYSTEM FEATURES:",
                 ""]
                + ["   " + f for f in self.features()])

    def diag_line(self, text, device):
        """A diagnostic screen with this console's own numbers in it.

        The manual prints these screens for device 1 of a site that is not
        yours. Point them at the device the panel is on, and give them the
        label that device was programmed with.
        """
        if len(text) > 3 and text[1] == " " and text[3] == ":"                 and (text[2].isdigit() or text[2] == "X"):
            # "T 1:" and "T X:" are both the manual drawing a device number
            text = f"{text[0]} {device}:{text[4:]}"
        code = DEVICE_LABEL_CODE.get(text[0]) if text[1:2] == " " else None
        if code is None and text.startswith("("):
            # 576013-818 Rev AA Figure 6-7 heads its screens with the product
            # label on its own, no device letter in front: these are per
            # PRODUCT, and the figure says TANK/SENSOR cycles the tanks
            code = "602"
        label = self.text(code, device) if code else ""
        if not label and code:
            # An unlabelled device is not "(PRODUCT LABEL)" on a console; it
            # is the device, named by what it is.
            label = f"{DEVICE_WORD.get(text[0], 'DEVICE')} {device}"
        if "PROBE TYPE" in text:
            text = text.replace("(PROBE TYPE)", self.probe_type(device))
            text = text.replace("PROBE TYPE", self.probe_type(device))
        for placeholder in ("(PRODUCT LABEL)", "(Product Label)", "(Label)",
                            "LOCATION", "(Vac Sensor Label)",
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

    def archive_save(self):
        """SAVE SETUP DATA, everything programmed, to the E2 chip.

        Written in the same format --seed reads, so an archive taken here can
        be poured into another console.
        """
        try:
            with open(self.archive_path(), "w", encoding="utf-8") as fh:
                fh.write("# TLS-350 archived setup data\n")
                fh.write(f"#WHEN\t{time.strftime('%y%m%d%H%M', self.now())}\n")
                for key in self.ARCHIVE_EXTRA:
                    fh.write(f"#SET\t{key}\t{getattr(self, key) or ''}\n")
                for (key, device), value in sorted(
                        self.settings.items(), key=lambda kv: str(kv[0])):
                    fh.write(f"#CFG\t{key}\t{device}\t{value}\n")
                for tank, capacity in sorted(self.tank_capacity.items()):
                    fh.write(f"#CAP\t{tank}\t{capacity}\n")
                for meter, tank in sorted(self.meters.items()):
                    fh.write(f"#MTR\t{meter}\t{tank}\n")
                for code, data in sorted(self.values.items()):
                    blob = data.encode("ascii", "replace").hex().upper()
                    fh.write(f"{code}\t{blob}\n")
            return len(self.values)
        except OSError:
            return -1

    def archive_exists(self):
        """Is there anything in the E2 chip to put back?"""
        return os.path.exists(self.archive_path())

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
        self.values.clear()
        self.settings.clear()
        for line in lines:
            if line.startswith("#SET\t"):
                _tag, key, _, value = (line.split("\t") + ["", "", ""])[:4]
                if key in self.ARCHIVE_EXTRA:
                    setattr(self, key, value)
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
                    self.meters[int(parts[1])] = int(parts[2])
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
        """
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
        for bay in ("is", "power", "comm"):
            slot = 0
            for key, name, part, mbay, _wires, _most in MODULES:
                if mbay != bay:
                    continue
                for _ in range(self.count(key)):
                    slot += 1
                    tag = "SLOT" if bay != "comm" else "COMM"
                    por, now = self.module_id_resistance(key, slot)
                    out.append((f"{tag} {slot} {MODULE_SHORT.get(key, name)}",
                                f"POR= {por:6d} C= {now:6d}"))
            for n in range(slot + 1, BAY_SLOTS[bay] + 1):
                tag = "SLOT" if bay != "comm" else "COMM"
                empty = EMPTY_OHMS.get(bay, 15000000)
                out.append((f"{tag} {n} UNUSED",
                            f"POR= {empty} C= {empty}"))
        return out

    def module_id_resistance(self, module, slot=1):
        """(at the last reset, now) for the ID resistor in that slot.

        A real one is a resistor being measured, so it is never exactly its
        nominal value and never exactly the same twice: the manual's own
        sample has one module at 164040 when the console last started and
        166912 now. The two agreeing to within a percent is the healthy
        reading; a module pulled since the reset is what makes them differ by
        orders of magnitude.
        """
        nominal = MODULE_OHMS.get(module)
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

    # What an empty slot reads, in the manual's own sample
    EMPTY_READING = 15000000.0

    def slot_readings(self):
        """[(slot, key, name, power-on reset, current)] down the whole cage.

        "POR = ID resistor value of module in this slot read at last system
        reset. C = current ID resistor value." A board that has not been
        swapped since the reset reads the same for both, which is the point of
        printing them side by side.
        """
        out, slot = [], 0
        for bay in ("is", "power"):
            for key, name, _part, mbay, _wires, _most in MODULES:
                if mbay != bay:
                    continue
                for _ in range(self.count(key)):
                    slot += 1
                    ident = float(zlib.crc32(key.encode()) % 1000000)
                    out.append((slot, key, MODULE_SHORT.get(key, name),
                                ident, ident))
        for n in range(slot + 1, BAY_SLOTS["is"] + BAY_SLOTS["power"] + 1):
            out.append((n, None, "UNUSED", self.EMPTY_READING,
                        self.EMPTY_READING))
        comm = 0
        for key, name, _part, mbay, _wires, _most in MODULES:
            if mbay != "comm":
                continue
            for _ in range(self.count(key)):
                comm += 1
                ident = float(zlib.crc32(key.encode()) % 1000000)
                out.append((-comm, key, MODULE_SHORT.get(key, name), ident,
                            ident))
        for n in range(comm + 1, BAY_SLOTS["comm"] + 1):
            out.append((-n, None, "UNUSED", self.EMPTY_READING,
                        self.EMPTY_READING))
        return out

    def configuration_lines(self):
        """I102's SYSTEM CONFIGURATION, slot by slot."""
        rows = ["SYSTEM CONFIGURATION",
                "SLOT  BOARD TYPE                    POWER ON RESET"
                "     CURRENT"]
        for slot, _key, name, por, current in self.slot_readings():
            where = f"{slot:3d}  " if slot > 0 else f"      COMM {-slot} "
            rows.append(f"{where}{name:<24.24s}{por:15.0f}{current:17.0f}")
        return rows

    def configuration_records(self):
        """The same, packed: NN then SS TT FFFFFFFF CCCCCCCC per module."""
        rows = self.slot_readings()
        out = f"{len(rows):02X}"
        for slot, key, _name, por, current in rows:
            number = slot if slot > 0 else 16 - slot
            out += f"{number:02X}{self.MODULE_TYPE.get(key, '00')}"
            out += packed.hexfloat(por)
            out += packed.hexfloat(current)
        return out

    # ---- what the technician collects --------------------------------------
    CSLD_TABLES = {"A51": "RATE TABLE", "A52": "RATE TEST",
                   "A53": "VOLUME HISTORY TABLE",
                   "A54": "MOVING AVERAGE TABLE"}

    def csld_table_lines(self, token, tank):
        """One of the four CSLD diagnostics tables, as the guide prints them."""
        label = self.text("602", tank) or f"TANK {tank}"
        rows = [f"CSLD DIAGNOSTICS: {self.CSLD_TABLES.get(token, token)}",
                f"T {tank}:{label}"]
        table = self.csld.table(tank)
        if not table:
            rows.append("NO CSLD DATA")
            return rows
        if token == "A54":
            rows.append("      TIME   AVERAGE")
            for when, average in self.csld.moving_average(tank):
                rows.append(time.strftime("%y%m%d%H%M",
                                          time.localtime(when))
                            + f"{average:10.3f}")
            return rows
        if token == "A53":
            rows.append("      TIME      VOL   ULLG  AVTMP")
            for one in table:
                rows.append(time.strftime("%y%m%d%H%M",
                                          time.localtime(one["at"]))
                            + f"{one['volume']:9.0f}{one['ullage']:7.0f}"
                            + f"{one['temp']:7.1f}")
            return rows
        # A51 the rate table, A52 the same rows for the test in hand
        rows.append("      TIME ST    LRT AVTMP   VOL  ULLG")
        for one in (table if token == "A51" else table[-1:]):
            rows.append(time.strftime("%y%m%d%H%M", time.localtime(one["at"]))
                        + f"{int(one['state']):3d}{one['rate']:7.3f}"
                        + f"{one['temp']:6.1f}{one['volume']:6.0f}"
                        + f"{one['ullage']:6.0f}")
        return rows

    def csld_table_records(self, token, tank):
        """The same tables packed: TT RR then a record each."""
        table = self.csld.table(tank)
        if token == "A52":
            table = table[-1:]
        out = f"{tank:02d}{len(table):02d}"
        for one in table:
            values = [one["rate"], one["temp"], one["volume"], one["ullage"]]
            out += (one["state"] + f"{len(values):02d}"
                    + f"{int(one['at']):08X}")
            out += "".join(packed.hexfloat(v) for v in values)
        return out

    def pump_tank(self, pump):
        """Which tank a pump sense input is assigned to, from S772."""
        raw = (self.values.get(f"S772{pump:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return int(body) if body.isdigit() else 0

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
        return any(state == "fail" for state in self.isd_forced.values())

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
            slot = 0
            for key, name, part, mbay, _wires, _most in MODULES:
                if mbay != bay:
                    continue
                for _ in range(self.fitted(key)):
                    slot += 1
                    out.append((bay, slot, key, name, part))
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

    def diag_reading(self, token, device=1, kind=None):
        """Every diagnostic screen the manual draws with X's in it.

        The manual cannot print your probe's serial number, so it prints
        XXXXXX; a simulator that prints XXXXXX has simulated the manual and
        not the console. See `readings.py` for what is derived and what is
        generated.
        """
        c, dev = self, int(device)
        if token.startswith("probe_ref_"):
            # "ORIG REF DISTANCE - Original reference distance reading
            # recorded at date/time or serial number change"; the screen shows
            # when, and the two dates are the point of it. Checked before the
            # probe_ prefix below, which would otherwise swallow them.
            when = (self._commissioned or time.mktime(self.now())) - (
                86400.0 * 365 if token.endswith("orig") else 0.0)
            return time.strftime("%m/%d/%y", time.localtime(when))
        if token.startswith("probe_"):
            return self._probe_reading(token, dev)
        if token.startswith("sensor_"):
            return self._sensor_diag(token, dev)
        if token.startswith("ss_"):
            return self._smart_diag(token, dev)
        if token.startswith("fm_"):
            return self._fuel_diag(token, dev)
        if token.startswith("accu_"):
            return self.accuchart.screen(dev, token[5:])

        if token == "pc_software":
            return (f"PC SWARE# {self.PC_SOFTWARE}" + chr(10)
                    + f"CREATED - {self.software_info()['created']}")
        if token == "pc_resets":
            return ("PC ROM CHECKSUM=PASSED" + chr(10)
                    + f"PC RESET COUNTS = {readings.integer(0, 3, 'pcreset')}")
        if token == "pc_errors":
            return ("PC ROM ERRORS = 0" + chr(10) + "MC CKSUM ERRS = 0")
        if token == "mc_comms":
            # two counters that only ever go up, at the rate the boards talk
            out = int(self._uptime_hours() * 3600 / 2.0) + 11
            return (f"MC -->PC COMMS = {out % 100000:5d}" + chr(10)
                    + f"MC <--PC COMMS = {max(out - 1, 0) % 100000:5d}")
        if token == "dim_software":
            created = self.software_info()["created"].replace(".", "-")
            return (f"M1: SWARE#{self.DIM_SOFTWARE}" + chr(10)
                    + f"CREATED - {created}")
        if token == "dim_errors":
            return ("M1: DIM ROM CKSUM = PASS" + chr(10)
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
            return (f"BLOCK: {readings.digits(6, 'mtkey')}" + chr(10)
                    + "ARE YOU SURE?: NO")
        if token == "comm_rssi":
            # a modem's signal strength and bit error rate, both of which
            # move about on a real line
            rssi = readings.wander(self, 12, 31, "rssi", dev, swing=0.4)
            ber = readings.wander(self, 0, 4, "ber", dev, swing=0.9)
            return f"RSSI: {rssi:.0f} BER: {ber:.0f}"

        if token == "tank_leak_rate":
            rate = self.leaks.measured_rate("tank", dev)
            return f"LEAK RATE = -{rate:.2f} GAL/HR"
        if token == "csld_rate":
            rows = self.csld.table(dev)
            rate = rows[-1]["rate"] if rows else self.leaks.measured_rate("tank", dev)
            return f"TEST RATE: {rate:.2f} GAL/HR"
        if token == "csld_hours":
            rows = self.csld.table(dev)
            return f"TOTAL TIME: {len(rows) * _csld.IDLE_HOURS:.1f} HRS"
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
        if token in ("line_messages", "line_crc"):
            line = self.lines.line(kind or "wplld", dev)
            head = f"W {dev}: LAST READ={line.pressure:.3f} PSI"
            if token == "line_messages":
                total = int(self._uptime_hours() * 60) + 1
                return head + chr(10) + f"TOTAL MESSAGE: {total}"
            return head + chr(10) + "CRC: 0       PARITY: 0"

        if token == "meter_map":
            return self._meter_line(dev, "map")
        if token == "meter_events":
            return self._meter_line(dev, "events")

        if token == "tank_leak_when":
            result = (self.leaks.result("tank", dev, "periodic")
                      or self.leaks.result("tank", dev, "annual")
                      or self.leaks.result("tank", dev, "gross"))
            if result is None:
                return "NO TEST DATA"
            return time.strftime("%b %d,%Y %I:%M:%S %p",
                                 time.localtime(result.started)).upper()
        if token == "csld_when":
            rows = self.csld.table(dev)
            when = rows[-1]["at"] if rows else time.mktime(self.now())
            return time.strftime("%b %d, %Y %I:%M %p",
                                 time.localtime(when)).upper()
        if token == "csld_tests":
            return f"TOTAL TESTS {len(self.csld.table(dev))}"
        if token == "csld_rejects":
            rows = self.csld.table(dev)
            return f"POS REJECTS: {sum(1 for r in rows if r['state'] != '00')}"
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
            meters = sorted(self.meters)
            if not meters:
                return "END EVENT: 0 GALS"
            meter = meters[(dev - 1) % len(meters)]
            return f"END EVENT: {self.bir.totals.get(meter, 0.0):.0f} GALS"
        if token in ("power_removed", "power_restored"):
            when = (self.power_off if token == "power_removed"
                    else self._commissioned) or time.mktime(self.now())
            return time.strftime("%b %d, %Y %I:%M %p",
                                 time.localtime(when)).upper()
        if token.startswith("power_off"):
            # what the tank read the moment the lights went out
            kept = self.power_off_state.get(dev) or {}
            if token == "power_off_volume":
                return f"VOLUME = {kept.get('volume', 0.0):.0f} GALS"
            if token == "power_off_water":
                return f"WATER VOL = {kept.get('water_vol', 0.0):.0f} GALS"
            return f"TEMP = {kept.get('temp', 0.0):.1f} DEG F"
        if token == "power_water":
            water = self.tank_level.get(dev, {}).get("water", 0.0)
            return f"WATER VOL = {water * 12:.0f} GALS"
        if token == "power_volume":
            volume = self.tank_level.get(dev, {}).get("volume", 0.0)
            return f"VOLUME = {volume:.0f} GALS"
        if token == "power_temp":
            return f"TEMP = {self.product_temperature(dev):.1f} DEG F"

        if token == "pmc_vapor":
            return f"INCHES H2O:      {self.vapor_pressure(dev):.3f}"
        if token == "pmc_hc":
            return f"HC SENSOR      {self.hydrocarbon(dev):.3f}%"
        if token == "pmc_load":
            # the polisher's canister load, from the vapor valve dump the
            # wire already exposes (IB6100); with no reading it is 0
            return f"LOAD:       {self._pmc_reading('load', 24.9):.1f}%"
        if token == "pmc_effluent":
            return f"{self._pmc_reading('effluent', 0.05):.2f} LB/KGAL"
        if token == "pmc_temp":
            return f"{self._pmc_reading('temp', 75.05):.2f} DEG F"
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
        """Put one device into the phase the command names, and say so."""
        family, state = self.CONTROL_STATES[what]
        self.control_phase[(family, number)] = state
        return state

    def control_phase_of(self, family, number, default="00"):
        return self.control_phase.get((family, number), default)

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
        """[(when, slope, offset, passed)] for one sensor, newest first."""
        when = (self._commissioned or time.mktime(self.now()))
        out = []
        for n in range(most):
            at = when - n * 30 * 86400
            out.append((at,
                        readings.fixed(0.9, 5.2, "calslope", module, number, n),
                        readings.fixed(0.0, 5.1, "caloffset", module, number, n),
                        True))
        return out

    def record_accuchart_update(self, tank, when):
        """When a calibration was applied, so the printer can say so.

        "Each time an AccuChart calibration is updated, a user notification
        message is sent to the local printer."
        """
        self.accuchart_log.append((int(tank), when))
        del self.accuchart_log[:-40]

    def comm_board_name(self, port):
        """Which card is in comm slot `port`, as the screens name it."""
        fitted = []
        for key in ("rs232", "modem", "mt", "vmc"):
            fitted += [key] * self.count(key)
        if 1 <= port <= len(fitted):
            return {"rs232": "RS-232", "modem": "SITEFAX", "mt": "MT COMM",
                    "vmc": "VMCI"}[fitted[port - 1]]
        return "UNUSED"

    def product_temperature(self, tank):
        """What the probe's RTDs make of the product.

        Not a constant: fuel underground sits near the ground temperature and
        moves with a delivery and with the season, so this wanders the way a
        real reading does rather than reading 55.0 for ever.
        """
        return readings.wander(self, 48.0, 62.0, "temp", tank, swing=0.12,
                               period=3600.0)

    def _meter_line(self, device, what):
        """"FP: XX M: XX =T X": one row of the BIR meter map."""
        meters = sorted(self.meters)
        if not meters:
            return "NO METERS MAPPED"
        meter = meters[(device - 1) % len(meters)]
        tank = self.meters[meter]
        head = "METER MAP" if what == "map" else "BIR METER EVENTS"
        if what == "events":
            through = self.bir.totals.get(meter, 0.0)
            return (head + chr(10)
                    + f"M: {meter:02d} =T {tank}  {through:7.1f}")
        return head + chr(10) + f"FP: {(meter + 1) // 2:02d} M: {meter:02d} =T {tank}"

    # ---- the probe ----------------------------------------------------------
    def probe_type(self, tank):
        """"MAG PROBE" or "CAP0 PROBE", which is what the screens head with.

        A tank programmed with a float size is a Mag probe, because that is
        the only kind that has one.
        """
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
            # TMP5 down to TMP0, six thermistors up the probe
            warm = self.product_temperature(tank)
            span = readings.fixed(-1.8, 1.8, "therm", tank, n)
            return (17000.0 + (warm - 48.0) / 14.0 * 6000.0
                    + (n - 12) * 780.0 + span * 90.0)
        # C01 to C10, the product float, ten times
        height = self.stick_height(tank)
        stale = 0.0 if n <= 5 else readings.fixed(8.0, 16.0, "stale", tank)
        jitter = readings.fixed(-0.4, 0.4, "chan", tank, n)
        return max(700.0, height * gradient + stale + jitter)

    def probe_circuit(self, tank):
        """"ID CHAN", which Table 9-2 calls the probe's manufacturing code."""
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

        No manual here says how a date code is built. The three in A01's own
        example are 1401, 2410 and 0000, which look like they could be a year
        and a week and could equally be a batch; guessing at an encoding and
        printing it as though it were derived would be worse than saying so.
        See UNKNOWNS. It is stable per probe, four hex digits, and that is
        all it claims to be.
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

    def _sensor_diag(self, token, number):
        """"CNTR = X VALUE = XXXXXX": the resistance the module is reading.

        Which band it falls in IS the sensor's state, which is the whole
        point of the screen, so this is derived from the state the bench has
        the sensor in and the bands the Troubleshooting Guide prints.
        """
        if token == "sensor_ppm":
            module, state = "vapor", self._sensor_state("vapor", number)
            ppm = readings.sensor_value(self, module, number, state, channel=2)
            return f"{ppm:.0f} PPM"
        module = self.SENSOR_DIAG_MODULE.get(token)
        if not module:
            return ""
        state = self._sensor_state(module, number)
        one = readings.sensor_value(self, module, number, state, channel=1)
        if token == "sensor_liquid":
            return f"CNTR = 1 VALUE = {one:.0f}"
        if token == "sensor_2wire":
            return f"CNTR = 5 VALUE = {one:.0f}"
        if token == "sensor_groundtemp":
            # the ground temperature channel, in counts
            return f"CNTR = 1 VALUE = {readings.wander(self, 480, 620, 'gt', number):.0f}"
        two = readings.sensor_value(self, module, number, state, channel=2)
        return f"1 = {one:.0f} 2 = {two:.0f}"

    def _sensor_state(self, module, number):
        state = self.sensor_state.get((module, str(number)), "normal")
        if state != "normal" and not self.sensor_alarm_allowed(module, number,
                                                               state):
            return "normal"
        return state

    def _smart_diag(self, token, number):
        """The Mag, Vac and ATMP sensor screens behind SMART SENSOR DIAGS."""
        if token == "ss_serial":
            return f"SERIAL NUMBER: {readings.digits(8, 'ss', number)}"
        if token == "ss_serial9":
            return f"SERIAL NUMBER {readings.digits(9, 'ss9', number)}"
        if token == "ss_atm":
            # "ATM PRESSURE: XX.XXX PSI": one atmosphere, and the weather
            return f"ATM PRESSURE: {readings.wander(self, 14.40, 14.90, 'atm', number, period=7200.0):.3f} PSI"
        total = readings.fixed(22.0, 40.0, "sshead", number)
        fuel = readings.wander(self, 0.0, 1.2, "ssfuel", number, swing=0.5)
        water = readings.wander(self, 0.0, 2.4, "sswater", number, swing=0.4)
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
            return f"FLUID TEMP  {self.product_temperature(number):.1f} DEG F"
        if token == "ss_board_temp":
            # a board runs warmer than what it is standing in
            return f"BOARD TEMP  {self.product_temperature(number) + readings.fixed(8.0, 16.0, 'ssboard', number):.1f} DEG F"
        return ""

    def _fuel_diag(self, token, tank):
        """"AVG SALES-SUN: XXXX GALS": Fuel Manager's week, day by day."""
        _fm, what, day = token.split("_", 2)
        # Fuel Manager holds an average for each day of the week and the
        # setup menu programs all seven (576013-623 Rev AN p.128). Where one
        # has been programmed it is the answer; where none has, the week is
        # shaped off the single figure function code 683 was last given,
        # because a forecourt is not flat across the week.
        DAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
        if day in DAYS:
            set_to = self.setting(f"avg_sales_{DAYS.index(day) + 1}", tank, "")
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
                 "THU": 1.06, "FRI": 1.22, "SAT": 1.03}.get(day, 1.0)
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
    def diag_action(self, name, device=1):
        """Do what a NO/YES diagnostic screen says it does.

        These are the screens the figures draw with an answer on them:
        REINIT COMM BD, DELETE CSLD RECORDS, RESET ACCUCHART, and the
        Maintenance Tracker key block. Every one of them was navigable and
        inert before.
        """
        dev = int(device)
        if name == "accu_reset":
            return self.accuchart.restart(dev)
        if name == "csld_delete":
            return f"{self.csld.delete_table(dev)} RECORD(S) DELETED"
        if name == "reinit_comm":
            # a comm board re-initialise drops the port back to its defaults
            for code in ("881", "886"):
                self.values.pop(f"S{code}{dev:02d}", None)
            self.save()
            return f"COMM {dev} RE-INITIALIZED"
        if name == "mt_block":
            return f"KEY {readings.digits(6, 'mtkey')} BLOCKED"
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

        How much each segment answers being wetted, against how much the
        segments answer on average: that is what makes it a SENSITIVITY, and
        it is why the numbers sit around 1.000 for a probe whose segments all
        behave the same. The two reference positions are not segments, so
        they fall where they fall.

        The manual prints the ratios but never says what they are normalised
        against, and every one of its examples starts at 0.000. The 0.000 is
        followed; the normalisation is this simulator's own. See UNKNOWNS.
        """
        if self.probe_type(tank) == "MAG PROBE":
            return []
        dry = self.probe_calibration(tank, wet=False, updated=True)
        wet = self.probe_calibration(tank, wet=True, updated=True)
        spans = [w - d for w, d in zip(wet, dry)]
        segments = spans[2:]
        mean = (sum(segments) / len(segments)) if segments else 0.0
        if not mean:
            return [0.0] * len(spans)
        return [0.0] + [span / mean for span in spans[1:]]

    # A10 to A13 are the same channels read through four windows. A Mag has
    # the nineteen `probe_channel` already models; a CAP has its own count,
    # and the manual's examples are the only statement of it: a CAP0 answers
    # ten and a CAP1 thirty-three.
    CAP_CHANNELS = {"CAP0": 10, "CAP1": 33}

    # "SSSS - Sample Number (Hex)", four digits, so it rolls at 65536. The
    # rate is not published; A10 and A13 in the manual's own example are read
    # a minute apart and differ by 24 samples, which is about two and a half
    # seconds each, so that is what this counts at.
    SAMPLE_SECONDS = 2.5

    def probe_sample_number(self, tank):
        """The running count A10 and A13 report, as the console has it now."""
        if tank not in self.programmed_tanks():
            return 0
        since = time.mktime(self.now()) - (self._commissioned
                                           or time.mktime(self.now()))
        return int(max(0.0, since) / self.SAMPLE_SECONDS) % 0x10000

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

    def probe_sample_health(self, tank):
        """A15's (samples read, samples used, last error number, error time).

        A healthy probe uses every sample it reads. The manual's example is a
        probe that read 2 and used 2 with a last error of 0, which is what a
        console says when nothing has gone wrong -- and it still prints a
        LAST SAMPLE ERROR TIME, because the field is always there.
        """
        window = self.probe_window(tank, "standard")
        return (window, window, 0, self.probe_initialised(tank))

    def probe_temperatures(self, tank):
        """A15's "TEMP SENSOR DATA", T6 down to T1, in Fahrenheit.

        Six thermistors up the probe, which are channels C12 to C17, read as
        temperatures rather than as counts. The product is warmest at the
        bottom where it has been longest out of the weather and coolest at the
        top, which is the order the manual prints them in and the spread it
        shows: 72.6 at T6 down to 67.6 at T1, five degrees over the six.
        """
        warm = self.product_temperature(tank)
        spread = readings.fixed(3.0, 6.0, "tspread", tank)
        return [warm + spread * (0.5 - n / 5.0) for n in range(6)]

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
            # "W 1: P0 H0  S: PENDING" over the two pressures
            return (f"{letter} {device}: P{int(ln.pressure > FLOOR_PSI)} "
                    f"H{int(ln.handle)}  S: {ln.status()[:9]}" + chr(10)
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
    # what the screen reads: 576013-623 Rev AN draws "TANK PROFILE 1PT" on
    # p.96 with no space, and "TANK PROFILE    : 50 PTS" on p.100 -- the
    # multi-point ones are plural and the one-point one is not spaced.
    PROFILE_NAME = {"00": "1PT", "01": "4 PTS", "02": "20 PTS",
                    "03": "LINEAR", "04": "50 PTS"}

    def profiles(self):
        """The profiles CHANGE walks on this console.

        The fifty point chart came with tank chart security; software older
        than that offers the four it has.
        """
        return [p for p in sorted(self.PROFILE_NAME)
                if self.supports(versions.PROFILE_FEATURE.get(p))]

    def tank_profile(self, tank):
        """"00" 1 point, "01" 4, "02" 20, "03" linear, "04" 50."""
        if self.values.get(f"S63C{tank:02d}") or self.values.get(
                f"S63B{tank:02d}"):
            return "04"
        for code, profile in (("606", "02"), ("605", "01"), ("60A", "03")):
            if self.values.get(f"S{code}{tank:02d}"):
                return profile
        return "00"

    def set_tank_profile(self, tank, profile):
        """Move the tank onto another profile.

        "Changing profile selection will erase the previously entered 50 point
        profile!": and the same is true of the other four, since the console
        keeps one set of volumes per tank, not five.
        """
        keep = self.PROFILE_CODE.get(profile, "604")
        full = self.full_volume(tank, default=None)
        erased = 0
        for code in list(self.PROFILE_CODE.values()) + ["63B"]:
            if code == keep:
                continue
            if self.values.pop(f"S{code}{tank:02d}", None) is not None:
                erased += 1
        # the profile is remembered by which function holds the volumes, so
        # the chosen one is always written, as 0 until it is entered, which
        # is what the console shows: "FULL VOL: 000000"
        body = packed.hexfloat(full if full is not None else 0.0)
        self.values[f"S{keep}{tank:02d}"] = f"{tank:02d}{body}"
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
        out.append("           DIAMETER FULL VOLUME")
        out.append(f"           {diam:6.2f} {self.full_volume(tank):12.0f}")
        out.append("")
        out.append("PAIR       HEIGHT      VOLUME")
        for i, (height, volume) in enumerate(self.chart_points(tank), start=1):
            out.append(f"{i:5d}  {height:10.2f} {volume:11.0f}")
        return chr(10).join(out)

    def audit_report(self, tank):
        """I218: who the chart belongs to, and when it was last touched."""
        label = self.text("602", tank) or f"TANK {tank}"
        capacity = self.tank_capacity.get(tank) or self.full_volume(tank)
        out = ["TANK CHART AUDIT TRAIL", f"T {tank}: {label}",
               f"TANK CAPACITY : {capacity:.0f}", "CONSOLE SERIAL NUMBER:",
               self.serial_number or "",
               f"PROBE S/N  : {self.probe_serial(tank)}",
               "WEIGHTS AND MEASURES:", self.wm_office or "", "", "DATE/TIME"]
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
        raw = self.values.get(f"S{tok}{dev:02d}")
        if not raw:
            return None
        body = raw[2:] if len(raw) > 8 else raw
        try:
            return packed.unhexfloat(body[-8:])
        except Exception:
            try:
                return float(body)
            except ValueError:
                return None

    def delivery_delay(self, tank):
        """S610's "delay time between the completion of a bulk delivery and
        the Delivery Increase Report", in minutes."""
        raw = (self.values.get(f"S610{tank:02d}") or "").strip()
        body = raw[2:] if len(raw) > 2 else raw
        return int(body) if body.isdigit() else 1

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
                    "3wire": ("746", "747"), "smart": ("721", "722")}

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
            vol, water = st.get("volume", 0.0), st.get("water", 0.0)
            tt = f"{tank:02d}"
            if tank in self.probe_out:
                # 576013-635: In-Tank alarm type 09, "Tank Probe Out
                # Alarm". With no probe there are no readings, so none of
                # the level conditions below can assert either: a console
                # that cannot see the water cannot raise High Water.
                out.append("0209" + tt)
                continue
            hi_w, warn_w = self.limit("624", tank), self.limit("627", tank)
            over, low = self.limit("623", tank), self.limit("621", tank)
            high, maxv = self.limit("622", tank), self.limit("628", tank)
            deliver = self.limit("629", tank)
            if hi_w and water >= hi_w:
                out.append("0203" + tt)
            elif warn_w and water >= warn_w:
                out.append("0210" + tt)
            if maxv and vol >= maxv:
                out.append("0212" + tt)
            elif over and vol >= over:
                out.append("0204" + tt)
            elif high and vol >= high:
                out.append("0207" + tt)
            if low and vol <= low:
                out.append("0205" + tt)
            elif deliver and vol <= deliver:
                out.append("0211" + tt)
        if self.out_of_paper:
            out.append("010100")      # system 01, "Printer out of Paper"
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
        out.extend(self.bir.conditions())
        out.extend(self.autodial.conditions())
        if self.rdu_fault and self.has("rdu"):
            # system alarm 08: the remote display "not communicating
            # properly" (576013-818)
            out.append("010800")
        if self.dim_fault and self.has("edim"):
            # category 19 type 03: the EDIM lost its link to the POS or
            # dispenser controller for about a minute (576013-818 ch.10)
            out.append("190301")
        if self.dim_fault and self.has("mdim") and not self.has("edim"):
            out.append("180301")
        # the ISD monitoring tests the bench has forced. Site tests are
        # category 30 on device 00; the collection tests ride hose 1.
        ISD_CODES = {"leakage": ("300600", "300700"),
                     "gross": ("300200", "300300"),
                     "degrade": ("300400", "300500"),
                     "collect_gross": ("310101", "310201"),
                     "collect_degrade": ("310301", "310401"),
                     "sensor": ("302000", "302100"),
                     "setup": ("301800", "301900")}
        if self.licensed("isd"):
            for test, state in sorted(self.isd_forced.items()):
                pair = ISD_CODES.get(test)
                if pair and state in ("warn", "fail"):
                    out.append(pair[0] if state == "warn" else pair[1])
        out.extend(self.posted)
        out.extend(self.leaks.conditions())
        out.extend(self.csld.conditions())
        out.extend(self.setup_warnings())
        out.extend(self.test_needed_warnings())
        out.extend(self.accuchart.conditions())
        # "Missing Delivery Ticket": a gauged delivery with no ticket
        # against it, on a site running ticketed delivery
        for tank, _record in self.deliveries.unticketed():
            out.append("0228" + f"{tank:02d}")
        for (mod, num), state in sorted(self.sensor_state.items()):
            aa = SENSOR_MODULE_CATEGORY.get(mod)
            table = SMART_STATE_NN if mod == "smart" else SENSOR_STATE_NN
            nn = table.get(state)
            if not (nn and aa and self.has(mod)):
                continue
            if not self.sensor_alarm_allowed(mod, num, state):
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
        for rate_key, (enable, warn_code, alarm_code) in                 self.LINE_TEST_NEEDED.items():
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
                   ("smart", "28", "721", "723")]

    def setup_warnings(self):
        """[AANNTT] for programming that is insufficient or invalid.

        "When you exit the Setup Mode, a Setup Data Warning will appear in the
        Status Display and the yellow warning light will flash if insufficient
        or invalid setup data has been entered ... The display and report will
        identify the source of the warning (i.e. Tank 1, Sensor 4, etc.), and
        the warning indicators will remain active until the cause has been
        corrected."

        So it is a condition, not a latch: fill the gap in and it goes.
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
        for module, aa, config, category in self.SETUP_CHECK:
            if not self.has(module):
                continue
            for n in range(1, self.capacity(module) + 1):
                if self._configured(config, n) and not self.text(category, n):
                    out.append(aa + ("01" if aa == "28" else "02")
                               + f"{n:02d}")
        for module, aa, config, needs in (("plld", "21", "781", ("788", "785")),
                                          ("wplld", "26", "7A1", ("7A8", "7A5")),
                                          ("vlld", "06", "751", ("756", "752"))):
            if not self.has(module):
                continue
            for n in range(1, self.capacity(module) + 1):
                if self._configured(config, n) and not all(
                        self.text(code, n) for code in needs):
                    out.append(aa + "01" + f"{n:02d}")
        return out

    def slot_text(self, code, count=None, base=0):
        """Which positions on this module are connected: "1 X 3 X".

        The console configures a module, not a device: four probe positions
        or eight sensor positions on one screen, a number where something is
        connected and an X where nothing is.
        """
        count = count or SLOT_POSITIONS.get(code, 4)
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

    def _configured(self, code, device):
        raw = (self.values.get(f"S{code}{device:02d}") or "").strip()
        return raw.endswith("1")

    def latches(self, rec):
        """Does this alarm stay on the display after its cause has gone?

        Most do not. A TLS-350 without a Maintenance Tracker board follows the
        condition: refill the tank and LOW PRODUCT takes itself off the screen,
        no key press needed. ALARM/TEST never clears a live alarm, "it does
        not clear the alarm message from the display or disable the alarm",
        it silences the audible and clears what has already gone away.

        Two things do latch:

        * A test RESULT is not a condition. A failed leak test, or a line the
          console has shut down, is a stored fact; it stays until something
          re-passes the test or the alarm is acknowledged, which is what the
          Line Re-Enable Method (Pass Line Test / Alarm Acknowledge) chooses
          between.
        * With a Maintenance Tracker board fitted, alarms become PROTECTED
          maintenance alarms: they are held for the contractor and cleared
          only by an acknowledgement made with a valid ID key, which the
          Maintenance History log records.
        """
        if self.has("mt"):
            return True
        desc = (STATUS_TYPES.get(rec[:2]) or {}).get(rec[2:4], "")
        return "Test Fail" in desc or "Shutdown" in desc

    def compute_alarms(self):
        """[AANNTT] the console is DISPLAYING, which is not the same list.

        Every condition that is true right now, plus anything latched that has
        not been acknowledged since its cause was corrected.
        """
        live = set(self.conditions())
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

    # 8A2's list. The first six are the manual's own standard labels and
    # codes; the rest are "USER DEFINED LABEL" rows a site adds, and this
    # console has none until somebody adds one.
    SERVICE_CODES = [
        ("0101", "REPROGRAMMED TLS"), ("0102", "COLD BOOT SYSTEM"),
        ("0103", "REPLACED PC BOARD"), ("0104", "NO PROBLEM FOUND"),
        ("0105", "NO SOLUTION FOUND"), ("0106", "OTHER SOLUTION"),
    ]

    def service_codes(self):
        """8A2, the standard list plus whatever the site has added."""
        return list(self.SERVICE_CODES) + list(self.user_service_codes)

    def block_tracker_key(self, ident):
        """8A4: take a Contractor ID key off the accepted list.

        The report the console answers with afterwards is the BLOCKED list,
        not the active one -- 8A3 and 8A4 share a template and mean opposite
        things by it.
        """
        self.blocked_keys.append(ident)
        self.mt_keys = [(k, name) for k, name in self.mt_keys if k != ident]
        return ident

    def tracker_keys(self):
        """8A3: the Contractor ID keys the Maintenance Tracker will accept.

        A key is a piece of hardware somebody carries, so a console has the
        ones that have been presented to it and no others. Nothing here
        presents one, so the list is empty until the bench adds one -- which
        is what an untouched console's list looks like.
        """
        return [(ident, name) for ident, name in self.mt_keys]

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
        last week together say the next one will do."""
        average = self.fuel_management(tank)[3:10]
        last = self.fuel_management_last(tank)
        return [(a * 2.0 + b) / 3.0 for a, b in zip(average, last)]

    def product_density(self, tank):
        """Pounds per gallon, this tank's own and steady between looks."""
        return readings.fixed(*self.DENSITY_BAND, "density", tank)

    def product_mass(self, tank):
        """"MASS 20357" against "VOLUME 5329" and "DENSITY 5.9987"."""
        volume = self.tank_level.get(tank, {}).get("volume", 0.0)
        return volume * self.product_density(tank)

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

    def alarm_report_lines(self, records, title, state=False):
        """The printed form of 113, 114 and 115.

        `state` is 114's extra column and 114's alone -- which is the whole
        reason these are three reports and not one.
        """
        head = "ID  CATEGORY  DESCRIPTION          ALARM TYPE          "
        head += "STATE  DATE     TIME" if state else "DATE     TIME"
        rows = [title, head]
        for record in records:
            described = describe_alarms([record["aa"] + record["nn"]
                                         + record["tt"]])
            if not described:
                continue
            one = described[0]
            letter = STATUS_DEVICE_CODE.get(record["aa"], "")
            number = int(record["tt"]) if record["tt"].isdigit() else 0
            ident = f"{letter} {number}" if letter and number else "    "
            stamp = time.strptime(record["at"], "%y%m%d%H%M")
            cell = ""
            if state:
                cell = ("CLEAR " if record.get("state") == "01" else "ALARM ")
            rows.append(f"{ident:<4s}{self.device_category(record):<10.10s}"
                        f"{self.device_label(record):<21.21s}"
                        f"{one['description'].upper():<20.20s}"
                        f"{cell}"
                        f"{time.strftime('%m-%d-%y %I:%M%p', stamp)}")
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
            out += f"{self.text('501', n) or '':<20.20s}"
        return out

    def service_log(self, most=20):
        """116 and 11A: what a service contractor entered and when.

        Nothing here logs a service visit, so this is empty on a console
        nobody has serviced -- which is the honest answer and the same one
        the leak-test flag reports give.
        """
        return list(self.service_entries)[:most]

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
        rows = ["ID  CATEGORY  DESCRIPTION          ALARM TYPE"
                "           STATE    DATE    TIME"]
        for record in self.alarm_log:
            if bool(self.priority(record)) != bool(priority):
                continue
            described = describe_alarms([record["aa"] + record["nn"]
                                         + record["tt"]])
            if not described:
                continue
            one = described[0]
            letter = STATUS_DEVICE_CODE.get(record["aa"], "")
            number = int(record["tt"]) if record["tt"].isdigit() else 0
            ident = f"{letter} {number}" if letter and number else "    "
            state = "CLEAR" if record.get("state") == "01" else "ALARM"
            stamp = time.strptime(record["at"], "%y%m%d%H%M")
            # "W 3 OTHER SPECIAL WPLLD SHUTDOWN ALM CLEAR": the category is
            # the sensor category the device was set up as, the description is
            # what the site called it, and the alarm type is the message
            rows.append(f"{ident:<4s}{self.device_category(record):<10.10s}"
                        f"{self.device_label(record):<21.21s}"
                        f"{one['description'].upper():<21.21s}"
                        f"{state:<8s}"
                        + time.strftime("%m-%d-%y ", stamp)
                        + clock_hhmm(stamp))
        if len(rows) == 1:
            rows.append("NO ALARM HISTORY")
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

    def acknowledge(self, keyed=False):
        """ALARM/TEST. (silenced, still_live, cleared_from_display)

        `keyed` is a valid Contractor's ID key in the MT Comm card, which a
        console with Maintenance Tracker fitted wants before a protected alarm
        can be acknowledged. Silencing is never protected.
        """
        shown = set(self.compute_alarms())
        live = set(self.conditions())
        self.silenced = bool(shown)
        self.acked |= shown
        if self.has("mt") and not keyed:
            return len(shown), len(live), 0
        # A failed test is a stored result, so acknowledging it IS correcting
        # the cause, and a shut-down line comes back only if the Line
        # Re-Enable Method says an acknowledgement is what re-enables it.
        self.posted -= shown
        self.leaks.re_enable()
        live = set(self.conditions())
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
        # what the console itself puts on the second line: the device code
        # from Table 29-1, its number, and the message in capitals,
        # "T 3:LOW PRODUCT ALARM"
        letter = STATUS_DEVICE_CODE.get(aa)
        screen = (f"{letter} {n}:{desc.upper()}" if letter and n
                  else desc.upper())
        out.append({"aa": aa, "nn": nn, "tt": tt, "where": where,
                    "description": desc, "text": f"{where}: {desc}",
                    "screen": screen})
    return out
