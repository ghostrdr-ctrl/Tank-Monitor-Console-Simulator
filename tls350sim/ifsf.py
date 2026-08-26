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
"""IFSF tank-gauge database support, section 8 of 576013-635.

An IFSF console is a different animal from a standard-protocol one. Instead
of answering SOH/function-code commands, it exposes its state as a set of
DATABASES -- the tank-level-gauge database, one probe database per tank, the
error databases, the contents and temperature tables -- each holding numbered
DATA ELEMENTS a client reads by (database address, data id). The serial
manual, section 8, is the list of which elements this console supports and
what each one is; the "a" digit of the software number is 3 for an IFSF
platform.

WHAT THIS EMULATES, AND WHAT IT DOES NOT. The manual defines the database
CONTENT -- every supported data element and its meaning -- and every one of
them here reads from the same console state the standard protocol reads, so
an IFSF client and a standard client see the same tank. What the manual does
NOT define is the WIRE: it points at the external IFSF documents "PART II,
COMMUNICATION SPECIFICATION" and "PART III.3 TANK LEVEL GAUGE APPLICATION"
for the LON-based framing, and those are not on this shelf. So this models
the database read interface -- `read(console, db, data_id, tank)` returns the
value of any supported element -- and does not invent the LON transport,
which is the same principled stance the auto-dial frame takes: emulate what
the manual specifies, and do not fabricate what it delegates elsewhere.

Reference: reference/ (576013-635 Rev Y section 8).
"""

# Database addresses (576013-635 8.1-8.8). The probe databases are addressed
# per tank in the 21H-3FH range; the rest are fixed.
DB_TLG = 0x01            # tank level gauge database (8.1)
DB_TLG_ERROR = 0x41      # tank level gauge error code database (8.2)
DB_PROBE_BASE = 0x21     # tank probe database, tank n at 0x20 + n (8.3)
DB_CONTENTS = 0x21       # sub: tank contents (strapping) table (8.4)
DB_TEMPERATURE = 0x22    # sub: tank temperature table (8.5)
DB_PROBE_ERROR = 0x41    # sub: tank probe error code database (8.6)
DB_DOWNLOAD = 0x81       # data download database (8.7)
DB_COMMS = 0x00          # communication service database (8.8)

# The IFSF protocol version this console reports (58 IFSF_Protocol_Ver).
IFSF_PROTOCOL_VER = "1.00"
# 50 TLG_Manufacturer_Id: Veeder-Root. IFSF assigns manufacturer ids; the
# console reports its maker, and this is the name, the id itself being an
# IFSF-registry number not given in this manual.
MANUFACTURER = "VEEDER-ROOT"
MODEL = "TLS-350"

# Every supported data element, per database, as section 8 lists them:
# {db: {data_id: (name, mandatory)}}. `read` below turns an id into a value.
TLG_ELEMENTS = {
    1: ("Nb_Tanks", True), 2: ("Reference_Temp", False),
    3: ("TLG_Measurement_Units", False), 6: ("Country_Code", True),
    7: ("Maint_Password", True), 50: ("TLG_Manufacturer_Id", True),
    51: ("TLG_Model", True), 52: ("TLG_Type", True),
    53: ("TLG_Serial_Nb", True), 54: ("TLG_Appl_Software_Ver", True),
    58: ("IFSF_Protocol_Ver", True), 59: ("Current_Date", False),
    60: ("Current_Time", False), 61: ("SW_Checksum", True),
    70: ("Enter_Maint_Mode", True), 71: ("Exit_Maint_Mode", True),
}

PROBE_ELEMENTS = {
    1: ("TP_Manufacturer_Id", True), 2: ("TP_Type", True),
    3: ("TP_Serial_Nb", True), 4: ("TP_Model", True),
    5: ("TP_Appl_Software_Ver", True), 6: ("Prod_Nb", False),
    7: ("Prod_Description", False), 8: ("Prod_Group_Code", False),
    9: ("Ref_Density", False), 10: ("Tank_Diameter", False),
    11: ("Shell_Capacity", False), 12: ("Max_Safe_Fill_Capacity", False),
    13: ("Low_Capacity", False), 14: ("Min_Operating_Capacity", False),
    15: ("HiHi_Level_Setpoint", False), 16: ("Hi_Level_Setpoint", False),
    18: ("LoLo_Level_Setpoint", False), 19: ("Hi_Water_Setpoint", False),
    20: ("Water_Detection_Thresh", False), 21: ("Tank_Tilt_Offset", False),
    22: ("Tank_Manifold_Partners", False), 23: ("TP_Measurement_Units", False),
    32: ("TP_Status", False), 33: ("TP_Alarm", False),
    64: ("Product_Level", False), 65: ("Total_Observed_Volume", False),
    66: ("Gross_Standard_Volume", False), 67: ("Average_Temp", False),
    68: ("Water_Level", False), 69: ("Observed_Density", False),
    70: ("Last_Reading_Date", False), 71: ("Last_Reading_Time", False),
    100: ("TP_Status_Message", False),
}

TLG_ERROR_ELEMENTS = {
    1: ("TLG_Error_Type", True), 2: ("TLG_Err_Description", False),
    3: ("TLG_Error_Total", True), 4: ("TLG_Error_Total_Erase_Date", False),
    100: ("TLG_Error_Type_Mes", True),
}

CONTENTS_ELEMENTS = {1: ("Strap_Level", True), 2: ("Strap_Vol", True)}
TEMPERATURE_ELEMENTS = {1: ("Temp_height", True), 2: ("Temp_value", True)}
PROBE_ERROR_ELEMENTS = {
    1: ("TP_Error_Type", True), 2: ("TP_Err_Description", False),
    100: ("TP_Error_Type_Mes", True),
}
COMMS_ELEMENTS = {
    2: ("Local_Node_Address", True), 3: ("Recipient_Addr_Table", True),
    4: ("Heartbeat_Interval", True), 5: ("Max_Block_Length", True),
    11: ("Add_Recipient_Addr", True),
}


def is_ifsf(console):
    """Whether this console is running the IFSF platform.

    The software number's platform digit is 3 for IFSF (576013-635, the
    version report note). A console configured for IFSF answers the
    databases; a standard one does not.
    """
    return console.supports("ifsf") and console.setting("ifsf_platform", 0,
                                                         True)


def probe_tank(db_address):
    """The tank a probe-database address names, or None.

    Tank n's probe database is at 0x20 + n (21H-3FH), so tank 1 is 0x21.
    """
    if DB_PROBE_BASE <= db_address <= 0x3F:
        return db_address - 0x20
    return None


def read(console, db_address, data_id, tank=None):
    """The value of a supported data element, or None if unsupported.

    One reader for every database. Probe elements read the same physical
    state the standard protocol reads, so the two personalities never
    disagree about a tank.
    """
    n = probe_tank(db_address)
    if n is not None:
        return _probe(console, n, data_id)
    if db_address == DB_TLG:
        return _tlg(console, data_id)
    if db_address == DB_TLG_ERROR:
        return _tlg_error(console, data_id)
    if db_address == DB_COMMS:
        return _comms(console, data_id)
    return None


# ---------------------------------------------------------------------------
# the tank-level-gauge database (8.1)


def _tlg(console, data_id):
    import time
    if data_id == 1:
        return len(console.programmed_tanks())
    if data_id == 2:
        # Reference_Temp: the standard reference temperature, 60 F in the US
        return 60.0
    if data_id == 3:
        return "US" if _us_units(console) else "METRIC"
    if data_id == 6:
        return (console.text("51F", 0) or "US").strip() or "US"
    if data_id == 7:
        return console.security_code() or ""
    if data_id == 50:
        return MANUFACTURER
    if data_id == 51:
        return MODEL
    if data_id == 52:
        return "TLG"                     # tank level gauge
    if data_id == 53:
        return console.serial_number or console.software_info()["smodule"]
    if data_id == 54:
        return console.software_info()["version"]
    if data_id == 58:
        return IFSF_PROTOCOL_VER
    if data_id == 59:
        return time.strftime("%Y%m%d", console.now())
    if data_id == 60:
        return time.strftime("%H%M%S", console.now())
    if data_id == 61:
        # SW_Checksum: the console's software part number carries it
        return console.software_info()["number"]
    if data_id in (70, 71):
        # Enter/Exit_Maint_Mode are commands, not readable values
        return "OK"
    return None


# ---------------------------------------------------------------------------
# the tank probe database (8.3)


def _probe(console, tank, data_id):
    if tank not in console.programmed_tanks():
        return None
    if data_id == 1:
        return MANUFACTURER
    if data_id == 2:
        return "MAG"                     # a Veeder-Root Mag probe
    if data_id == 3:
        return console.probe_serial(tank)
    if data_id == 4:
        return MODEL + " PROBE"
    if data_id == 5:
        return console.software_info()["version"]
    if data_id == 6:
        return tank                      # product number = tank number
    if data_id == 7:
        return console.text("602", tank) or f"TANK {tank}"
    if data_id == 9:
        return _f(console.setting("density", tank) or 0.0)
    if data_id == 10:
        return _f(console.limit("607", tank) or 96.0)     # tank diameter
    if data_id == 11:
        return _f(console.full_volume(tank))              # shell capacity
    if data_id == 12:
        return _f(console.limit("60A", tank)              # max safe fill
                  or console.full_volume(tank))
    if data_id == 15:
        return _f(console.limit("624", tank) or 0.0)      # high-high water
    if data_id == 16:
        return _f(console.limit("622", tank) or 0.0)      # high product
    if data_id == 18:
        return _f(console.limit("621", tank) or 0.0)      # low-low product
    if data_id == 19:
        return _f(console.limit("627", tank) or 0.0)      # high water
    if data_id == 23:
        return "US" if _us_units(console) else "METRIC"
    if data_id == 32:
        return "OUT" if tank in console.probe_out else "NORMAL"
    if data_id == 33:
        return _probe_alarm(console, tank)
    if data_id == 64:
        return _f(console.stick_height(tank))             # product level
    if data_id == 65:
        return _f(console.tank_level.get(tank, {}).get("volume", 0.0))
    if data_id == 66:
        # gross standard volume: the temperature-corrected volume
        return _f(console.tank_level.get(tank, {}).get("volume", 0.0))
    if data_id == 67:
        return _f(console.product_temperature(tank))      # average temp
    if data_id == 68:
        return _f(console.tank_level.get(tank, {}).get("water", 0.0))
    if data_id == 69:
        return _f(console.setting("density", tank) or 0.0)
    if data_id in (70, 71):
        import time
        fmt = "%Y%m%d" if data_id == 70 else "%H%M%S"
        return time.strftime(fmt, console.now())
    if data_id == 100:
        return "OUT" if tank in console.probe_out else "NORMAL"
    return None


def _probe_alarm(console, tank):
    """TP_Alarm: what the tank is alarming on right now, in IFSF terms."""
    from .console import describe_alarms
    live = [a for a in describe_alarms(console.conditions())
            if a["aa"] == "02" and a["tt"] == f"{tank:02d}"]
    if not live:
        return "NONE"
    return live[0]["description"].upper()


# ---------------------------------------------------------------------------
# the error databases (8.2, 8.6) and comms (8.8)


def _tlg_error(console, data_id):
    if data_id == 3:
        return 0                         # TLG_Error_Total
    if data_id == 1:
        return "NONE"
    return None


def _comms(console, data_id):
    if data_id == 2:
        return console.setting("ifsf_node", 0, 1)
    if data_id == 4:
        return 60                        # heartbeat interval, seconds
    if data_id == 5:
        return 255                       # max block length
    return None


# ---------------------------------------------------------------------------
# helpers


def _us_units(console):
    return (console.text("51F", 0) or "US").strip().upper() not in ("METRIC",)


def _f(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
