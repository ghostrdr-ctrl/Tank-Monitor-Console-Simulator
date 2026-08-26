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
"""The alarm and maintenance reports, 113 to 11B.

Five of these look like one report with five titles and they are not. The
differences are small, load-bearing, and easy to lose:

    111, 112   no station headers in the computer format
    113, 115   station headers, and NO state byte -- an 18 character record
    114        station headers AND a state byte -- a 20 character record

so a parser that shares one record reader across 113, 114 and 115 desyncs on
the second record of a 114. That is why `RECORD` below is per code rather
than one constant.

And 116 and 11A carry the SAME `Function Type: Service Report History` while
being incompatible: 116 has station headers, a ten character ID and a five
character code; 11A has no headers, a six character ID and a four character
NUMERIC code. 116 went obsolete at V27 and 11A replaced it, but they are not
drop-in for each other, and a console answering both has to answer them
differently.
"""

# Which reports put the four station header blocks into the COMPUTER format.
# The display form always shows them; this is about the packed reply.
HEADERS = {"113", "114", "115", "116"}

# "SS - Alarm State", which only 114 carries.
STATE = {"01": "CLEAR", "02": "ALARM"}
HAS_STATE = {"114"}

# 116 and 11A: same name, different widths. (id width, code width, numeric?)
SERVICE_WIDTHS = {"116": (10, 5, False), "11A": (6, 4, True)}

# 119's record types. The six character data field that follows means six
# different things depending on which of these it is -- the same hazard as
# 087/088, in one field this time.
MAINTENANCE_TYPE = {
    "01": "HISTORY ENABLED", "02": "HISTORY DISABLED",
    "03": "LOGIN", "04": "LOGOUT",
    "05": "REMOTE LOGIN", "06": "REMOTE LOGOUT",
    "07": "ALARM ACTIVE", "08": "ALARM CLEAR",
    "09": "ALARM ACKNOWLEDGED", "0A": "REMOTE ACKNOWLEDGED",
    "0B": "SERVICE CODE",
    "0C": "TANK TEST", "0D": "PLLD TEST", "0E": "WPLLD TEST",
    "0F": "MTC ERR",
    "10": "VLLD TEST",
}

# What the six characters after the type ARE, per type.
#   filler   000000, unused
#   login    an ID code
#   alarm    device, type and alarm number
#   service  a four digit service code, zero padded
#   device   a device number, zero padded
MAINTENANCE_DATA = {
    "01": "filler", "02": "filler",
    "03": "login", "04": "login", "05": "login", "06": "login",
    "07": "alarm", "08": "alarm", "09": "alarm", "0A": "alarm",
    "0B": "service",
    "0C": "device", "0D": "device", "0E": "device",
    "0F": "filler",
    # Type 10 arrived later and note 5 was never extended to cover it: the
    # "0000tt = Device #" line names 0C, 0D and 0E only. It is a test result
    # like those three, so it is read the same way. See UNKNOWNS.
    "10": "device",
}

# The count of records to follow is not written the same way twice in this
# family: 116 and 11A count in decimal, 11B counts in hex, and 119 counts in
# five decimal digits. Four neighbouring codes, three conventions.
COUNT = {"116": ("d", 2), "11A": ("d", 2), "11B": ("x", 2), "119": ("d", 5)}


def count_field(tok, n):
    """The record count, written the way this particular code writes it."""
    how, width = COUNT.get(tok, ("d", 2))
    return ("%0*X" if how == "x" else "%0*d") % (width, n)
