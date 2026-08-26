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
"""The mag sump, tanker load, VMC and comm reports.

The trap in the mag sump family is `tt`. Four codes, one family, one version,
and the field immediately after the sensor number is not the same field:

    317, 318    tt = Mag Sump Leak Test STATUS (00 NO TEST DATA .. 04 PASSED)
    319, 31A    tt = a COUNT of tests to follow (max 10, max 3)

A parser that shares the `sstt` header decode across all four reads 319's
"four tests follow" as "status 04, leak test passed". Which is why the count
and the status live in different tables below and the handler asks which kind
of code it is answering before it writes the field.
"""

# 317 and 318's status, and the abort reason that only 317 carries.
SUMP_STATUS = {
    "00": "NO TEST DATA AVAILABLE", "01": "LEAK TEST ABORTED",
    "02": "FILL SUMP", "03": "MEASURING HEIGHT", "04": "LEAK TEST PASSED",
}
ABORT_REASON = {
    "00": "NOT ABORTED", "01": "MAG SENS ALM/WARN", "02": "WATER TOO LOW",
    "03": "WATER TOO HIGH", "04": "TEMP TOO LOW", "05": "TEMP TOO HIGH",
    "06": "WATER INCREASED", "07": "WATER DECREASED",
    "08": "INSUFFICIENT DATA", "09": "LEAK RATE TOO HIGH",
    "10": "TEST PHASE TIMEOUT", "11": "TEMP STABLE TIMEOUT",
}
RATE_STATUS = {"00": "UNKNOWN", "01": "VALID", "02": "COMPUTING",
               "03": "STABLE"}
LEAK_STATUS = {"00": "UNKNOWN", "01": "VALID", "02": "COMPUTING"}

# Which of the four is a status and which is a count, and how many rows the
# counted ones may carry.
SUMP_REPORTS = {
    "317": {"tt": "status", "rows": 1, "title": "IN PROGRESS", "full": True},
    "318": {"tt": "status", "rows": 1, "title": "LAST PASSED TEST"},
    "319": {"tt": "count", "rows": 10, "title": "LAST 10 TEST PASSED"},
    "31A": {"tt": "count", "rows": 3, "title": "LAST PASSED EACH YEAR"},
}

# ---------------------------------------------------------------------------
# 411 and 412 have IDENTICAL byte layouts and incompatible alarm tables. The
# same `aaaa` means different things: 0002 is "Disabled VMCI Board" on 411 and
# "Roots meter not connected" on 412. Two tables, never one.
# ---------------------------------------------------------------------------
VMCI_ALARMS = {"0001": "SETUP DATA WARNING", "0002": "DISABLED ALARM"}
VMC_ALARMS = {"0001": "VMC COMM TIMEOUT", "0002": "METER NOT CONNECTED",
              "0003": "FP SHUTDOWN WARNING", "0004": "FP SHUTDOWN ALARM"}

# 411 counts boards 01-06; 412 counts controllers 01-18. Same field, two
# ranges.
VMCI_MAX, VMC_MAX = 6, 18

# BB1's status, and note the radix mixing inside one record: the serial is
# decimal, the side and status are hex, the recover rate is decimal x10, and
# the three counters are hex again.
VMC_STATUS = {
    "00": "METER NOT CONNECTED", "01": "IDLE", "02": "RUNNING",
    "03": "LAST TRANSACTION FAILED", "04": "FP SHUTDOWN WARNING",
    "05": "FP SHUTDOWN ALARM", "FE": "STATUS UNKNOWN",
    "FF": "VMC COMM TIMEOUT",
}

# 888's three enumerations.
CONNECT_TYPE = {
    "00": "NONE", "01": "AUTO DIAL TELETYPE", "02": "AUTO DIAL FAX",
    "03": "AUTO DIAL COMPUTER", "04": "AUTO TRANSMIT", "05": "MODEM DIAL IN",
    "06": "RS232 REQUEST",
}
COMM_STATE = {
    "00": "NONE", "01": "OPEN PHONE PORT", "02": "MODEM CHECK CONNECTION",
    "03": "TRANSMITTING DATA", "04": "CHECKING FOR CARRIER",
    "05": "WAITING FOR DATA", "06": "HANGING UP",
    "07": "FAXMODEM INITIALIZING", "08": "FAX CHECK CONNECTION",
    "09": "FAX CHECK PAGE", "10": "FAX END PAGE", "11": "FAX BUILD MESSAGE",
}
COMM_ERROR = {
    "01": "UART SETTINGS ERROR", "02": "MODEM INITIALIZATION FAILED",
    "03": "MODEM TIMED OUT", "04": "LOST CARRIER", "05": "DATA TIMED OUT",
    "06": "HANG UP FAILED", "07": "FAX INITIALIZATION FAILED",
    "08": "FAX CONNECTION FAILED", "09": "FAX TIMED OUT",
    "10": "FAX INTERPAGE ERROR", "11": "FAX END PAGE ERROR",
    "12": "FAX BUILD MESSAGE ERROR",
}

# 88D. 885 (the setter) knows two of these and 88D (the reader) knows four,
# so the setter cannot name the two the reader can report.
MODEM_TYPE = {"00": "NETCOMM SMART M7F", "01": "US ROBOTICS (UK)",
              "02": "VR TLS ANALOG MOD", "03": "VR TLS GSM MODEM"}
