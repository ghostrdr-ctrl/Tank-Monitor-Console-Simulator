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
"""The alarm GROUPS an output relay and a line disable are assigned by.

576013-623 Rev AN ch.24 and ch.25 walk the same seventeen screens, in the
same order, one per family of alarm the console can hand to a relay or to a
line's shutdown list: `R1: (Name)` over `IN-TANK ALARMS: NO`, then
`LIQUID SENSOR ALMS: NO`, and so on to `VMC ALARM: NO`. Chapter 6 walks the
same seventeen for an auto-dial destination with `RECEIVER ALARMS` inserted
among them, which is the group only a receiver has.

The screens themselves live in `consoledata.json`, one step per group per
function, because that is where every other screen lives. **What is here is
the pairing those screens cannot carry**: which alarm CATEGORY -- the `AA`
of the wire's `AANNTTss` -- each group is the front of, so that a relay
assigned an in-tank leak over the serial port reads `IN-TANK ALARMS: YES`
on the glass, and a group switched to NO on the glass takes its category's
assignments off the list.

The categories are 576013-623 Rev AN Table 5-1's own, whose Alarm Category
column names two of them outright where nothing else here could:

    "Power Side DIM (MDIM) (18) or Communication Side DIM (EDIM/BDIM) (19)"

and marks TRANSACTION ALARM "BDIM only", which is why 19 carries three
alarms and 18 carries two. That is the question the setup-mode audit said
the data could not answer.

Two categories are not on any of these screens and are deliberately absent:
13, the Universal Sensor, which no chapter gives a group screen to, and 14,
the Auto-Dial Fax receiver, whose group belongs to chapter 6's walk and not
to these two. An assignment in either is stored and reported and shows on no
setup screen.
"""

# key -- which is the tail of the setting each function stores it under --
# the screen's own second line, and the alarm category it is the front of.
GROUPS = (
    ("intank",  "IN-TANK ALARMS",     "02"),
    ("liquid",  "LIQUID SENSOR ALMS", "03"),
    ("vapor",   "VAPOR SENSOR ALMS",  "04"),
    ("input",   "EXTERNAL INPUTS",    "05"),
    ("vlld",    "LINE LEAK ALARMS",   "06"),
    ("gw",      "GROUNDWATER ALMS",   "07"),
    ("2wire",   "2 WIRE CL ALARMS",   "08"),
    ("3wire",   "3 WIRE CL ALARMS",   "12"),
    ("mdim",    "POWER SIDE DIM ALM", "18"),
    ("recon",   "RECONCILIATION ALM", "20"),
    ("plld",    "PRESSURE LINE LEAK", "21"),
    ("wplld",   "WPLLD LINE LEAK",    "26"),
    ("edim",    "COMM SIDE DIM ALM",  "19"),
    ("smart",   "SMART SENSOR ALM",   "28"),
    ("pumpmon", "PUMP ALARM",         "34"),
    ("vmci",    "VMCI ALARM",         "35"),
    ("vmc",     "VMC ALARM",          "36"),
)

# The setting each function keeps its seventeen answers under. One prefix
# per function rather than one shared set, because the DEVICE is a relay in
# the first and a line in the other three: relay 1 and PLLD line 1 are both
# device 1 and must not read each other's answers.
PREFIX = {
    "OUTPUT RELAY SETUP": "relay_alm",
    "PLLD LINE DISABLE SETUP": "plld_dis",
    "WPLLD LINE DISABLE SETUP": "wplld_dis",
    "VLLD LINE DISABLE SETUP": "vlld_dis",
}

# which of those three functions each line family is set up on
LINE_DISABLE_FUNCTION = {"plld": "PLLD LINE DISABLE SETUP",
                         "wplld": "WPLLD LINE DISABLE SETUP",
                         "vlld": "VLLD LINE DISABLE SETUP"}

BY_CATEGORY = {aa: key for key, _line, aa in GROUPS}


def setting_key(prefix, aa):
    """-> the setting that function keeps that category's YES/NO under.

    None for a category no screen on these two chapters' walks offers.
    """
    key = BY_CATEGORY.get(aa)
    return f"{prefix}_{key}" if key else None
