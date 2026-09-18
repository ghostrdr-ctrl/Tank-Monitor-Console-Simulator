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

def maintenance_words(entry):
    """119's six character data field, read the way its type says to.

    One field, six meanings: a filler, a login ID, a device/type/alarm
    triple, a service code or a device number. The same hazard as 087 and
    088, in a single field this time. See MAINTENANCE_DATA above.

    It lives here rather than on the wire's Handler because the PAPER needs
    it too: the white key's Maintenance Report prints the same records the
    serial port serves, and a console with two renderings of one report is
    a console whose two halves drift apart -- which is the defect three
    separate entries in `CLOSED.md` are about. `describe_alarms` is
    imported inside the call because `console` imports this module.
    """
    from .console import describe_alarms
    how = MAINTENANCE_DATA.get(entry["type"], "filler")
    data = entry.get("data", "000000")
    if how == "filler":
        return ""
    if how == "login":
        return data.strip("0") or data
    if how == "service":
        return data[-4:]
    if how == "device":
        return f"DEVICE {int(data[-2:] or 0)}"
    described = describe_alarms([data[2:4] + data[4:6] + data[0:2]])
    if described:
        return described[0]["description"].upper()
    return data


# The count of records to follow is not written the same way twice in this
# family: 116 and 11A count in decimal, 11B counts in hex, and 119 counts in
# five decimal digits. Four neighbouring codes, three conventions.
COUNT = {"116": ("d", 2), "11A": ("d", 2), "11B": ("x", 2), "119": ("d", 5)}


def count_field(tok, n):
    """The record count, written the way this particular code writes it."""
    how, width = COUNT.get(tok, ("d", 2))
    return ("%0*X" if how == "x" else "%0*d") % (width, n)


def service_rows(tok, entries):
    """116's and 11A's display rows, one per service log entry.

    576013-635 Rev AA p.56 and p.59's own columns. 11A carries two LABEL
    columns this console has nothing for -- the technician's name and the
    service description -- so they stand empty rather than being filled with
    something invented. The serial report and the panel's SERVICE REPORT on
    paper both draw these. See FIDELITY D24.
    """
    import time
    from .clock import clock_words
    wide, wide_code, _numeric = SERVICE_WIDTHS[tok]
    rows = []
    for e in entries:
        stamp = time.strptime(e["at"], "%y%m%d%H%M")
        at = clock_words(time.mktime(stamp))
        if tok == "11A":
            rows.append(f"{at:43s}"
                        f"{e['id']:<7.{wide}s}"
                        f"{'':20s}"
                        f"{e['code']:<{wide_code}.{wide_code}s}")
        else:
            rows.append(f"{at:23s}"
                        f"{e['id']:<12.{wide}s}"
                        f"{e['code']:<{wide_code}.{wide_code}s}")
    return rows
