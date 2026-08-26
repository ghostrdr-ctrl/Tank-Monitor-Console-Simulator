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
"""The HRM, CSLD monthly, fuel management, power outage and vapour valve
reports -- A56, A61, A62, A63, A81, A91, B61 and B62.

Two traps in here and the second is the worst one in the manual.

**A61 against A63.** They print the SAME column headings, including an
ENDTEMP column, and their computer formats are not the same: A63 carries an
Ending Temperature float and A61 does not. A61's packed record is status,
volume, sales, variance -- there is no temperature in it at all. So the
printed report has a column its own packed form cannot fill, and anybody
generating one from the other has to know that. A63 also carries an `NN`
field count where A61's records are fixed width.

**B61 against B62.** The same eight vapour valve faults, numbered two
incompatible ways -- and it is a REORDERING as well as a rebasing, so no
offset converts one to the other:

    fault                    B61 bit    B62 code
    Valve Command                  1          02
    (unused)                       2           -
    Cap Not Charging               3          00
    Cap Not Holding                4          01
    Temperature Range              5          03
    Ref Resistor Range             6          04
    Vapor Resistance               7          05
    Data Noise                     8          06
    Valve Noise                    9          07

B61 counts from 1 with a hole at 2 and leads with Valve Command; B62 counts
from 0 with no hole and leads with Cap Not Charging. B61's own PRINTOUT is a
third order again. One shared table indexed either way reports the wrong
fault, silently, and a technician would chase the wrong part.
"""

# The faults, named once. The two numbering schemes are separate tables
# BECAUSE they disagree; do not derive one from the other.
FAULTS = ["VALVE COMMAND FAULT", "CAP NOT CHARGING FAULT",
          "CAP NOT HOLDING FAULT", "TEMPERATURE RANGE FAULT",
          "REF RESISTOR FAULT", "VAPOR RESISTANCE FAULT",
          "DATA NOISE FAULT", "VALVE NOISE FAULT"]

# B61: which bit of the fault field each fault is. Bit 2 is unused.
B61_BIT = {"VALVE COMMAND FAULT": 1, "CAP NOT CHARGING FAULT": 3,
           "CAP NOT HOLDING FAULT": 4, "TEMPERATURE RANGE FAULT": 5,
           "REF RESISTOR FAULT": 6, "VAPOR RESISTANCE FAULT": 7,
           "DATA NOISE FAULT": 8, "VALVE NOISE FAULT": 9}

# B62: which sub-alarm code each fault is. Starts at 00 and is in a different
# order.
B62_CODE = {"CAP NOT CHARGING FAULT": "00", "CAP NOT HOLDING FAULT": "01",
            "VALVE COMMAND FAULT": "02", "TEMPERATURE RANGE FAULT": "03",
            "REF RESISTOR FAULT": "04", "VAPOR RESISTANCE FAULT": "05",
            "DATA NOISE FAULT": "06", "VALVE NOISE FAULT": "07"}

VALVE_POSITION = {"0": "CLOSED", "1": "OPEN"}
BATTERY = {"0": "UNKNOWN", "1": "FULL", "2": "MEDIUM", "3": "LOW",
           "4": "REPLACE"}
CAPACITOR = {"0": "DISCHARGED", "1": "CHARGED"}

# A61 and A63's status flag, shared between them verbatim.
HRM_FLAG = {
    "00": "Data Used", "01": "Not mapped", "02": "Time Set Back",
    "03": "Gap Too Long", "04": "Delivery", "05": "Temp Low",
    "06": "Temp High", "07": "Temp Increase", "08": "Volume High",
    "09": "Volume Low", "0A": "Volume Change", "0B": "Not Calibrated",
    "0C": "Cal Time Filter", "0D": "No Sales Data", "0E": "Temp Decrease",
    "0F": "Reset Filter", "10": "Therm Flag", "11": "DIM Reset",
    "12": "BDIM Transaction",
}

# A62's daily verdict, which A61 and A63 do not have.
HRM_DAILY = {"00": "NO DATA", "01": "PASS", "02": "WARNING", "03": "FAIL"}

# A56's CSLD state changes. The codes are not contiguous -- 05, 06 and 07 are
# undefined -- and the manual's own example prints "RESULT: WARN" for which
# it gives no code at all. See UNKNOWNS.
CSLD_STATE = {
    "01": "RESULT: PASS", "02": "RESULT: FAIL",
    "03": "RESULT: NO RESULTS AVAIL", "04": "RESULT: INVL",
    "08": "RESULT: INCR", "98": "STATUS: NO IDLE DATA",
    "99": "STATUS: ACTIVE",
}
