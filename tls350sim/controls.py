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
"""Section 7.1's control functions: the nineteen codes that DO something.

Every one is Set-only -- none has an Inquire form -- and they fall into five
shapes rather than into subsystems:

    A  001, 002, 003, 010    a bare acknowledgement, no argument
    B  031                   the same, but carrying a literal confirm token
    C  089, 090              one sensor argument, 149, and a bare ack back
    D  092-09B               one sensor argument, 149, and a status echoed
    E  087, 088              a sensor, 149, AND a test type

The tables below are the state enumerations D and E echo back.
"""

# ---------------------------------------------------------------------------
# 087 and 088 look like a matched pair and their status tables are NOT the
# same, which is the trap in this section. The same digit means different
# things on the two of them:
#
#     03  on 087 is "testing at 0.10 gal/hr"
#     03  on 088 is "testing at 0.20 gal/hr"
#
# and 087 has twelve values where 088 has ten. So they are two tables, and
# sharing them would silently mislabel every 0.10 and 0.20 test on one of the
# two systems.
# ---------------------------------------------------------------------------
PLLD_TEST_STATUS = {
    "00": "TEST COMPLETE", "01": "DISPENSING", "02": "TESTING AT 3.00 GAL/HR",
    "03": "TESTING AT 0.10 GAL/HR", "04": "TEST ABORTED",
    "05": "RUNNING PUMP", "06": "LINE LOCKOUT", "07": "DISABLE ALARM",
    "08": "TEST PENDING", "09": "TESTING DELAY", "0A": "PRESSURE CHECK",
    "0B": "TESTING AT 0.20 GAL/HR",
}
WPLLD_TEST_STATUS = {
    "00": "TEST COMPLETE", "01": "DISPENSING", "02": "TESTING AT 3.00 GAL/HR",
    "03": "TESTING AT 0.20 GAL/HR", "04": "TEST ABORTED",
    "05": "LINE LOCKOUT", "06": "DISABLE ALARM", "07": "TEST PENDING",
    "08": "TEST DELAY", "09": "TESTING AT 0.10 GAL/HR",
}

# "rr - Test Type", and this one IS the same on both.
TEST_TYPE = {"01": "annual", "02": "periodic", "03": "gross"}
TEST_TYPE_NAME = {"01": "0.10 GPH", "02": "0.20 GPH", "03": "3.00 GPH"}

# 092, 093 and 094 share one enumeration between them.
PROFILE_STATUS = {
    "00": "TEST COMPLETE", "01": "RUNNING PUMP", "02": "RUNNING PUMP",
    "03": "PUMP OFF", "04": "MEASURING", "05": "MEASURING",
    "06": "MEASURING", "07": "MEASURING", "08": "ABORTED",
}

# 095 and 096 are a start/stop pair over one table...
VACUUM_TEST_STATUS = {"00": "ABORTED", "01": "STARTED", "02": "PENDING"}

# ...and 097 and 098 are another pair over a different one.
EVACUATION_STATE = {
    "00": "VACUUM OK", "01": "EVACUATION PENDING", "02": "EVACUATION ACTIVE",
    "03": "EVACUATION PENDING MANUAL", "04": "EVACUATION ACTIVE MANUAL",
    "05": "NO VACUUM", "06": "EVAC HOLD",
}

# 099, 09A and 09B share one.
SUMP_TEST_STATUS = {
    "00": "NO TEST DATA AVAILABLE", "01": "LEAK TEST ABORTED",
    "02": "FILL SUMP", "03": "MEASURING HEIGHT", "04": "LEAK TEST PASSED",
}

# Family D, keyed by code: (what it does, which table, the banner its printout
# heads itself with).
DEVICE_ACTIONS = {
    "092": ("profile_start", PROFILE_STATUS,
            "START PRESSURE LINE LEAK PROFILE LINE TEST"),
    "093": ("profile_stop", PROFILE_STATUS,
            "STOP PRESSURE LINE LEAK PROFILE LINE TEST"),
    "094": ("profile_bulk", PROFILE_STATUS,
            "RECALCULATE PRESSURE LINE LEAK PROFILE LINE TEST BULK MODULUS"),
    "095": ("vac_start", VACUUM_TEST_STATUS, "START VACUUM SENSOR MANUAL TEST"),
    "096": ("vac_stop", VACUUM_TEST_STATUS,
            "STOP VACUUM SENSOR MANUAL EVACUATION TEST"),
    "097": ("evac_hold", EVACUATION_STATE,
            "START VACUUM SENSOR EVACUATION HOLD"),
    "098": ("evac_release", EVACUATION_STATE,
            "STOP VACUUM SENSOR EVACUATION HOLD"),
    "099": ("sump_start", SUMP_TEST_STATUS, "START MAG SUMP LEAK TEST"),
    "09A": ("sump_height", SUMP_TEST_STATUS,
            "START MAG SUMP LEAK TEST MEASURING HEIGHT PHASE"),
    "09B": ("sump_stop", SUMP_TEST_STATUS, "STOP MAG SUMP LEAK TEST"),
}

# Family A: what each bare acknowledgement actually does, and what the display
# form says about it. 010 has nothing behind it here and says so.
SYSTEM_ACTIONS = {
    "001": ("system_reset", ""),
    "002": ("clear_power_flag", ""),
    "003": ("remote_alarm_reset", ""),
    "010": ("cancel_autodial", ""),
    "031": ("confirm_clear", "CONFIRM CLEAR COMPLETE"),
}

# 031 does not use 149. It carries this instead, and no manual here says what
# it is or why it differs -- see UNKNOWNS.
CONFIRM_CLEAR = "832382"
