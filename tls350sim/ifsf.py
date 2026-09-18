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
"""IFSF tank-gauge database support, section 8 of 576013-635.

An IFSF console is a different animal from a standard-protocol one. Instead
of answering SOH/function-code commands, it exposes its state as a set of
DATABASES -- the tank-level-gauge database, one probe database per tank, the
error databases, the contents and temperature tables -- each holding numbered
DATA ELEMENTS a client reads by (database address, data id). The serial
manual, section 8, is the list of which elements this console supports and
what each one is. Section 8 opens by making all of it conditional -- "When
equipped with the appropriate software and interface module, these systems
can respond to commands using the International Forecourt Standards Forum
(IFSF) tank gauge communications protocols" -- so `is_ifsf` gates every
read, and a console that is not on the platform answers nothing at all.

WHAT THIS EMULATES, AND WHAT IT DOES NOT. The manual defines the database
CONTENT -- every supported data element and its meaning -- and every one of
them here reads from the same console state the standard protocol reads, so
an IFSF client and a standard client see the same tank. What the manual does
NOT define is the WIRE: it points at the external IFSF documents "PART II,
COMMUNICATION SPECIFICATION" and "PART III.3 TANK LEVEL GAUGE APPLICATION"
for the LON-based framing, and those are not on this shelf. So this models
the database read interface -- `read(console, db_address, data_id)` returns
the value of any supported element, and `unsolicited` the three the manual
files under its own UNSOLICITED headings -- and does not invent the LON
transport, which is the same principled stance the auto-dial frame takes:
emulate what the manual specifies, and do not fabricate what it delegates
elsewhere. The split between what a client asks for and what the console
sends is CONTENT, which section 8 does define, and it is modelled here; when
the console would send one is framing, and it is not.

Reference: reference/ (576013-635 Rev Y section 8).
"""

# Database addresses (576013-635 8.1-8.8). Four of the eight are addressed
# by CONCATENATION and the manual writes each of them out:
#
#   8.2  DB_Ad = TLG_DAT (01H) + TLG_ER_DAT (41H) + TLG_ER_ID (01H-40H)
#   8.4  DB_Ad = TP_ID (21H-3FH) + CAL_DAT (21H) + ENTRY (01H-FFH)
#   8.5  DB_Ad = TP_ID (21H-3FH) + TEMP_DAT (22H) + TEMP_ADDR (01H-08H)
#   8.6  DB_Ad = TP_ID (21H-3FH) + TP_ER_DAT (41H) + TP_ER_ID (01H-40H)
#
# So a DB_Ad is up to three bytes -- a database, a sub-database under it and
# a record selector -- and only four of the eight are the single byte 8.1,
# 8.3, 8.7 and 8.8 give. The sub-database bytes below are NOT addresses in
# their own right, which is why they are named apart: 41H under TLG_DAT is
# the console's error database and 41H under a TP_ID is that probe's, and
# they were both `DB_..._ERROR = 0x41` here. `read` reached the first and
# nothing reached the second. `address` splits either form. See FIDELITY J1.
DB_TLG = 0x01            # TLG_DAT, the tank level gauge database (8.1)
DB_PROBE_BASE = 0x21     # TP_ID, tank n at 0x20 + n (8.3)
DB_DOWNLOAD = 0x81       # SW_DAT, the data download database (8.7)
DB_COMMS = 0x00          # the communication service database (8.8)

SUB_TLG_ERROR = 0x41     # TLG_ER_DAT, under TLG_DAT (8.2)
SUB_CONTENTS = 0x21      # CAL_DAT, under a TP_ID (8.4)
SUB_TEMPERATURE = 0x22   # TEMP_DAT, under a TP_ID (8.5)
SUB_PROBE_ERROR = 0x41   # TP_ER_DAT, under a TP_ID (8.6)

# The IFSF protocol version this console reports (58 IFSF_Protocol_Ver).
IFSF_PROTOCOL_VER = "1.00"
# 50 TLG_Manufacturer_Id: Veeder-Root. IFSF assigns manufacturer ids; the
# console reports its maker, and this is the name, the id itself being an
# IFSF-registry number not given in this manual.
MANUFACTURER = "VEEDER-ROOT"
MODEL = "TLS-350"

# 3 TLG_Measurement_Units and 23 TP_Measurement_Units, keyed by the first
# digit of 517: "U - System Units: 3=Imperial Gallons 2=Metric 1=U.S.".
# Section 8 names both elements and gives no encoding for either, so what
# this console reports is the name of the system it is programmed in.
UNIT_NAMES = {"1": "US", "2": "METRIC", "3": "IMPERIAL"}

# Three of the element tables are split by a sub-heading of the manual's own
# -- `UNSOLICITED DATA` in 8.2 and 8.6, `UNSOLICITED` in 8.3. The rows above
# it are what a client asks the console for; the row below it is what the
# console SENDS without being asked, and in all three tables that row is id
# 100. Nothing else in section 8 carries such a heading.
#
# This is a split in the CONTENT, which section 8 does define, and not in the
# framing, which it delegates to the external IFSF specifications, so it is
# modelled: `read` serves the solicited elements and refuses id 100, and
# `unsolicited` serves id 100 and nothing else. All three used to be ordinary
# rows a solicited read would answer. See FIDELITY J6.
UNSOLICITED_ID = 100

# Every supported data element, per database, as section 8 lists them:
# {data_id: (name, mandatory)}, and a row the manual marks `Supported = No`
# is not one of them. `read` below turns an id into a value. The
# communication service table is the exception and says why at its own head.
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

# 8.3 lists six of its rows `Supported = No` -- 9 Ref_Density, 15
# HiHi_Level_Setpoint, 16 Hi_Level_Setpoint, 18 LoLo_Level_Setpoint and 69
# Observed_Density, with 17 Lo_Level_Setpoint not listed at all -- so none of
# them is below and none of them answers. That is the Data Download
# database's treatment, all eight of its rows `No` and no element table at
# all, applied to the rows of a table that is otherwise supported. Five of
# them were declared and four were answering. See FIDELITY J5.
PROBE_ELEMENTS = {
    1: ("TP_Manufacturer_Id", True), 2: ("TP_Type", True),
    3: ("TP_Serial_Nb", True), 4: ("TP_Model", True),
    5: ("TP_Appl_Software_Ver", True), 6: ("Prod_Nb", False),
    7: ("Prod_Description", False), 8: ("Prod_Group_Code", False),
    10: ("Tank_Diameter", False), 11: ("Shell_Capacity", False),
    12: ("Max_Safe_Fill_Capacity", False), 13: ("Low_Capacity", False),
    14: ("Min_Operating_Capacity", False),
    19: ("Hi_Water_Setpoint", False),
    20: ("Water_Detection_Thresh", False), 21: ("Tank_Tilt_Offset", False),
    22: ("Tank_Manifold_Partners", False), 23: ("TP_Measurement_Units", False),
    32: ("TP_Status", True), 33: ("TP_Alarm", True),
    64: ("Product_Level", True), 65: ("Total_Observed_Volume", False),
    66: ("Gross_Standard_Volume", False), 67: ("Average_Temp", False),
    68: ("Water_Level", True),
    70: ("Last_Reading_Date", False), 71: ("Last_Reading_Time", False),
    100: ("TP_Status_Message", True),
}

TLG_ERROR_ELEMENTS = {
    1: ("TLG_Error_Type", True), 2: ("TLG_Err_Description", False),
    3: ("TLG_Error_Total", True), 4: ("TLG_Error_Total_Erase_Date", False),
    100: ("TLG_Error_Type_Mes", True),
}

# 8.4, the tank contents table, has no element table here on purpose: the
# manual marks BOTH of its rows `Supported = No`, `1 Strap_Level O No` and
# `2 Strap_Vol O No`, which is the shape the data download database (8.7) is
# already handled in, eight rows of `No` and a bare address constant. It had
# both rows declared, and Mandatory at that. See FIDELITY J5.
TEMPERATURE_ELEMENTS = {1: ("Temp_height", False), 2: ("Temp_value", False)}
# 8.6 lists SIX elements and three of them were absent here, two of those
# Mandatory: `3 TP_Error_Total M Yes`, `4 TP_Error_Total_Erase_Date O Yes`
# and `5 TP_Error_Status M Yes`. They are declared now and none of the six
# answers, which is the state written down rather than the state hidden.
# The database is a store of up to 64 probe error records, `TP_ER_ID
# (01H-40H)`, and this console keeps no probe error history for a selector
# to pick out of. Section 8 names the elements and nowhere says what a
# record holds, what `TP_Error_Type` encodes or what `TP_Error_Status`
# reports, and no other manual on this shelf says either. See FIDELITY J1.
PROBE_ERROR_ELEMENTS = {
    1: ("TP_Error_Type", True), 2: ("TP_Err_Description", False),
    3: ("TP_Error_Total", True), 4: ("TP_Error_Total_Erase_Date", False),
    5: ("TP_Error_Status", True), 100: ("TP_Error_Type_Mes", True),
}
# 8.8 is the one table in section 8 with no M/O column -- its heading is
# `Data_Id | Variable Name | Supported`, three columns and not four -- so
# there is no flag to carry and this table holds names alone. All eight of
# its rows are `Supported = Yes` and all eight are listed; ids 1, 10 and 12
# were missing. What none of them has is a value: `Communication_Protocol_Ver`
# is the version of the IFSF COMMUNICATION SPECIFICATION, and the recipient
# address table, the heartbeat error and the two commands that maintain that
# table are that specification's own machinery. It is Part II, which this
# shelf does not have, and it is the one thing this module has said from the
# start it will not invent. See FIDELITY J5.
COMMS_ELEMENTS = {
    1: "Communication_Protocol_Ver", 2: "Local_Node_Address",
    3: "Recipient_Addr_Table", 4: "Heartbeat_Interval",
    5: "Max_Block_Length", 10: "Heartbeat_Error",
    11: "Add_Recipient_Addr", 12: "Remove_Recipient_Addr",
}


def is_ifsf(console):
    """Whether this console is running the IFSF platform.

    Section 8 makes its databases conditional on the console being built for
    them, "when equipped with the appropriate software and interface
    module", so this is two questions and not one: does the software this
    console runs carry IFSF at all, which is the version table's own row,
    and has this site been ordered with it, which is the bench switch.

    It is not the software number's platform digit, which this docstring
    used to claim it was. Function 905's note reads that digit two ways at
    once, `a - Platform 3="IFSF" 2="Demo" 1="Real"` and then `3=Enhanced CPU
    16 Tank`, and `versions.py` puts the CPU family there, so the default
    16-tank console prints a 3 for a reason that has nothing to do with
    IFSF. No manual on this shelf settles whether the digit really does
    double duty. An explicit switch does, and it defaults OFF: a console
    nobody has ordered IFSF for is a standard console. See FIDELITY J2.
    """
    return bool(console.supports("ifsf")
                and console.setting("ifsf_platform", 0, False))


def probe_tank(db_address):
    """The tank a probe-database address names, or None.

    Tank n's probe database is at 0x20 + n (21H-3FH), so tank 1 is 0x21.
    """
    if DB_PROBE_BASE <= db_address <= 0x3F:
        return db_address - 0x20
    return None


def address(db_address):
    """A DB_Ad as (database, sub-database, selector).

    An int is one of the four single-byte addresses; a sequence is one of
    the four the manual builds by concatenation, and its parts are in the
    order the manual concatenates them. Both forms go in, so nothing that
    reads 8.1, 8.3, 8.7 or 8.8 has to say `(db,)` about it.

    A missing part is None rather than zero, because zero is an address:
    the communication service database is at 00H.
    """
    if isinstance(db_address, int):
        return db_address, None, None
    # Three Nones, not two: a zero-length sequence padded to two elements
    # and `parts[2]` was then an IndexError. Nothing reaches this with an
    # empty address today because nothing reaches it at all -- but a
    # decoder is exactly what would, and a truncated DB_Ad should be
    # rejected by its caller, not raise here.
    try:
        parts = (list(db_address) + [None, None, None])[:3]
    except TypeError:
        return None, None, None
    return parts[0], parts[1], parts[2]


def read(console, db_address, data_id, tank=None, authenticated=False):
    """The value of a supported data element, or None if unsupported.

    One reader for every database. Probe elements read the same physical
    state the standard protocol reads, so the two personalities never
    disagree about a tank.

    A console that is not on the IFSF platform has no database to read, and
    this is the only place that can say so, because `read` is the whole
    interface. The gate was defined and consulted by nothing: every element
    answered on every console, including one whose bench switch was off.
    See FIDELITY J2.

    A read is SOLICITED, so it does not serve the rows the manual files
    under its own `UNSOLICITED` heading. `unsolicited` below does. See
    FIDELITY J6.

    `authenticated` says the caller has established who is asking. It
    gates one element -- Maint_Password, 8.1 data id 7 -- and defaults to
    False so a transport built later has to opt in rather than leak the
    console's security code by omission. See the note at that element.
    """
    if not is_ifsf(console):
        return None
    if data_id == UNSOLICITED_ID:
        return None
    db, sub, selector = address(db_address)
    if not isinstance(db, int):
        # Not a number, so not an address. Said here rather than left to
        # reach `probe_tank`'s comparison, which raises TypeError on a str
        # and IndexError on an empty sequence -- in a decoder handed a
        # truncated frame off the wire, that is a crash, not a rejection.
        return None
    n = probe_tank(db)
    if n is not None:
        if sub is None:
            return _probe(console, n, data_id)
        if sub == SUB_TEMPERATURE:
            return _temperature(console, n, selector, data_id)
        # 8.4, the tank contents table, is `CAL_DAT (21H)` under a probe and
        # both of its rows are `Supported = No`, so there is nothing under
        # it to read. 8.6, the tank probe error database, is `TP_ER_DAT
        # (41H)`, and it is reachable now and still silent: see the note on
        # `PROBE_ERROR_ELEMENTS`.
        return None
    if db == DB_TLG:
        if sub is None:
            return _tlg(console, data_id, authenticated)
        if sub == SUB_TLG_ERROR:
            return _tlg_error(console, data_id, selector)
        return None
    if db == DB_COMMS and sub is None:
        return _comms(console, data_id)
    return None


def unsolicited(console, db_address, data_id=UNSOLICITED_ID):
    """What the console sends of its own accord, rather than answers.

    The three rows section 8 puts under `UNSOLICITED DATA` and
    `UNSOLICITED`: `TLG_Error_Type_Mes` in 8.2, `TP_Status_Message` in 8.3
    and `TP_Error_Type_Mes` in 8.6, all of them id 100 and all of them
    Mandatory. Each is the message form of the row at the top of its own
    table -- the same datum sent rather than asked for -- so
    `TP_Status_Message` agreeing with `TP_Status` is right, and serving both
    of them to a solicited read was what was wrong.

    WHEN the console sends one is the framing question, and the framing is
    in the external IFSF documents this shelf does not have. So this says
    what would be sent and not when, which is the same line the module draws
    everywhere else. See FIDELITY J6.
    """
    if not is_ifsf(console) or data_id != UNSOLICITED_ID:
        return None
    db, sub, selector = address(db_address)
    if not isinstance(db, int):
        # Not a number, so not an address. Said here rather than left to
        # reach `probe_tank`'s comparison, which raises TypeError on a str
        # and IndexError on an empty sequence -- in a decoder handed a
        # truncated frame off the wire, that is a crash, not a rejection.
        return None
    n = probe_tank(db)
    if n is not None and sub is None:
        return _probe(console, n, 32)              # TP_Status_Message
    if db == DB_TLG and sub == SUB_TLG_ERROR:
        return _tlg_error(console, 1, selector)    # TLG_Error_Type_Mes
    return None


# ---------------------------------------------------------------------------
# the tank-level-gauge database (8.1)


def _tlg(console, data_id, authenticated=False):
    import time
    if data_id == 1:
        return len(console.programmed_tanks())
    if data_id == 2:
        # Reference_Temp: the standard reference temperature, 60 F in the US
        return 60.0
    if data_id == 3:
        return _units(console)
    if data_id == 6:
        # Country_Code: 54D, "Set IS03166 3 Character Country Code", whose
        # note gives "aaa - ISO3166 Country Code (3 ASCII characters
        # [20h-7EH])" and whose screen prints "ISO3166 COUNTRY CODE: ESP".
        # It reads blank until somebody programmes it, the way
        # Maint_Password below does, rather than naming a country the
        # console was never told it stands in.
        return console.text("54D", 0)
    if data_id == 7:
        # Maint_Password, and it is the LIVE console security code -- the
        # same value `wire._handle` compares against to decide whether to
        # answer a serial command at all.
        #
        # Nothing dispatches this module today: there is no IFSF transport,
        # because the LON framing is in external IFSF Part II/III.3
        # documents that are not on the shelf. So this is not a live
        # defect. It is a trap laid for whoever builds the transport, and
        # the natural place to put an IFSF dispatcher is BEFORE the
        # standard protocol's security check -- IFSF being a different
        # protocol with its own addressing -- at which point one
        # unauthenticated read of database 01H element 7 hands over the
        # credential that gates everything else.
        #
        # So it fails closed. A caller that has established who is asking
        # says so; anything else reads blank, the way Country_Code above
        # reads blank until somebody programmes it.
        return (console.security_code() or "") if authenticated else ""
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
    if data_id == 8:
        # Prod_Group_Code: 603, Set Tank Product Code -- "Enter the
        # alphanumeric code used by a point-of-sale terminal or other
        # external device to identify product for inventory control
        # purposes" (576013-623 Rev AN, Product Code). That is what groups
        # product across the forecourt, and it is what every report in
        # `wire.py` already reads out of 603.
        return console.text("603", tank)
    if data_id == 10:
        return _f(console.limit("607", tank) or 96.0)     # tank diameter
    if data_id == 11:
        return _f(console.full_volume(tank))              # shell capacity
    if data_id == 12:
        # Max_Safe_Fill_Capacity is the Overfill Limit, 623: "Set this
        # percentage no greater than 90% of the tank's capacity (in
        # international installations set this percentage no greater than
        # 99% of label volume)" -- 576013-623 Rev AN, Overfill Limit. It is
        # one of the percent limits, so it comes through `limit_volume` as
        # gallons. This read 60A, the tank's full volume, which is exactly
        # what Shell_Capacity (11) above already answers, so the two came
        # back identical. See FIDELITY J4.
        return _f(console.limit_volume("623", tank) or 0.0)
    if data_id == 13:
        # Low_Capacity: 621, Set Tank Low Level Limit -- "Low Product warns
        # when volume in a tank recedes to the level you enter here"
        # (576013-623 Rev AN, Low Product). A volume in its own right, not
        # one of the percent limits.
        return _f(console.limit("621", tank) or 0.0)
    if data_id == 14:
        # Min_Operating_Capacity: 629, Set Tank Delivery Required Limit --
        # "Delivery Limit warns when the level of fluid in the tank drops to
        # a level at which the operator calls for a delivery. Set this
        # percentage at a volume higher than that of the Low Product alarm"
        # (576013-623 Rev AN, Delivery Limit). It is the volume the site
        # works down to before it must reorder, and it is a percent limit,
        # so it comes through `limit_volume` as gallons. FIDELITY J5 offered
        # 628 as the other candidate and 628 rules itself out: Set Tank
        # MAXIMUM Volume Limit is not a minimum of anything.
        return _f(console.limit_volume("629", tank) or 0.0)
    # Nothing answers 9, 15, 16, 18 or 69, and 17 does not exist. All five
    # are `Supported = No` in 8.3 and four of them were answering: 9 and 69
    # from the density setting, 16 from 622 and 18 from 621, which is the
    # limit Low_Capacity (13) above is actually for. 15 came out under J4
    # for the separate reason that it was being handed a water limit. See
    # FIDELITY J5.
    if data_id == 19:
        # Hi_Water_Setpoint is the High Water Limit, 624: "When water in the
        # tank rises to this High Water Limit value, the system triggers an
        # alarm" -- 576013-623 Rev AN, High Water Limit. 627 is the Water
        # Warning, which "acts as a pre-warning to the High Water Limit" and
        # is set lower, so serving it here answered the warning where the
        # client asked for the alarm. See FIDELITY J4.
        return _f(console.limit("624", tank) or 0.0)
    if data_id == 20:
        # Water_Detection_Thresh: the Programmable Minimum Water Threshold.
        # "When there is not water in the tank, but the water height
        # measurement is not 0.0, the water float is resting on a layer of
        # debris on the bottom of the tank. The Water Minimum Threshold sets
        # the level" -- 576013-623 Rev AN p.7-16. That is the height below
        # which the console does not call water water, which is what a
        # detection threshold is, and it is the only thing this console
        # calls a water threshold. FIDELITY J5 named 627 instead: 627 is the
        # High Water WARNING Limit, an alarm setpoint that "acts as a
        # pre-warning to the High Water Limit", which is a limit and not a
        # threshold. `water_minimum` chooses between 648 and 60E's fourth
        # column the way the float type decides.
        return _f(console.water_minimum(tank))
    if data_id == 21:
        # Tank_Tilt_Offset: 608, Set Tank Tilt -- "If the probe is installed
        # in the center of the tank, the value is 000.00 U.S.; 0000.0
        # Metric" (576013-623 Rev AN, Tank Tilt), worked out on that page's
        # own worksheet as "E x F = Tank Tilt Value". An offset in inches,
        # signed, and zero where nobody has entered one.
        return _f(console.limit("608", tank) or 0.0)
    if data_id == 22:
        # Tank_Manifold_Partners: the tanks this one is joined to, by either
        # kind of manifold -- 612, "This entry tells the system which tanks
        # are siphon manifolded together" (576013-623 Rev AN, Siphon
        # Manifolded Tank Status), and 61D for the line manifold.
        # `manifolded` reads both, from both ends, and puts this tank at the
        # front, so the partners are the rest of it. They are reported as
        # the run of two-digit tank numbers the console itself stores them
        # as, section 8 giving no encoding for any element.
        return "".join(f"{n:02d}" for n in console.manifolded(tank)[1:])
    if data_id == 23:
        return _units(console)
    if data_id == 32:
        return "OUT" if tank in console.probe_out else "NORMAL"
    if data_id == 33:
        return _probe_alarm(console, tank)
    if data_id == 64:
        return _f(console.stick_height(tank))             # product level
    if data_id == 65:
        return _f(console.tank_level.get(tank, {}).get("volume", 0.0))
    if data_id == 66:
        # Gross_Standard_Volume is the volume corrected to the reference
        # temperature. 8.3 lists it apart from Total_Observed_Volume (65)
        # above, and the console already computes the quantity: it is what
        # function 201 prints as TC VOLUME beside VOLUME. This returned the
        # same expression as 65, so the two were identical at every
        # temperature and both disagreed with i201. See FIDELITY J4.
        return _f(console.tc_volume(tank))
    if data_id == 67:
        return _f(console.product_temperature(tank))      # average temp
    if data_id == 68:
        return _f(console.tank_level.get(tank, {}).get("water", 0.0))
    if data_id in (70, 71):
        import time
        fmt = "%Y%m%d" if data_id == 70 else "%H%M%S"
        return time.strftime(fmt, console.now())
    # 100, TP_Status_Message, is 8.3's UNSOLICITED row and is served by
    # `unsolicited` rather than from here. See FIDELITY J6.
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


def _tlg_error(console, data_id, record=None):
    """8.2, one of up to 64 console error records.

    `TLG_ER_ID (01H-40H)` is the record the address selects, and an id
    outside that range is not an address in this database. Which record it
    is makes no difference to what comes back, and that is not the selector
    being ignored: `TLG_Error_Total` is zero because this console keeps no
    error history, so every record in the range is the same empty one. A
    console that kept them would answer this from the history and the
    selector is here for it. See FIDELITY J1.
    """
    if record is not None and not 0x01 <= record <= 0x40:
        return None
    if data_id == 3:
        return 0                         # TLG_Error_Total
    if data_id == 1:
        return "NONE"
    return None


def _temperature(console, tank, node, data_id):
    """8.5, one node of the tank temperature table.

    `TEMP_ADDR (01H-08H)` is the node the address selects, counting from
    one, and the two elements are its height and its reading. This console
    has SIX temperature nodes, not eight: `thermistor_ladder` is "the six
    RTDs before anything asks which of them are wet", on A15's own spread of
    "72.6 at T6 down to 67.6 at T1". 01H-08H is the width of the address
    field and not a count of what is on the end of the probe, so nodes 7 and
    8 read as nothing on a console that has six. See FIDELITY J1.
    """
    if tank not in console.programmed_tanks():
        return None
    if node is None or not 0x01 <= node <= 0x08:
        return None
    if node > console.THERMISTORS:
        return None
    if data_id == 1:
        return _f(console.thermistor_heights(tank)[node - 1])
    if data_id == 2:
        return _f(console.thermistor_ladder(tank)[node - 1])
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


def _units(console):
    """The unit system this console is programmed in.

    517, `Set System Type & Language Flags`, is where it lives, and its
    first digit is the one `Console.metric()` already reads. Both of the
    measurement-unit elements and the country code used to come off `51F`
    instead, which is not a function code in 576013-635 at all -- the
    TLS-450 manual has one and it is `Set Euro Protocol Prefix`, neither
    units nor country -- so the read was a constant and every dimensioned
    element in every database was labelled US on a console programmed
    metric. See FIDELITY J3.
    """
    return UNIT_NAMES.get((console.text("517", 0) or " ")[:1], "US")


def _f(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
