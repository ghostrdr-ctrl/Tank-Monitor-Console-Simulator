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
"""The serial/TCP side: speak the console's wire format.

Command   <SOH>[6-digit security]<letter><3-char function><2-digit device>[data]<CR>
Reply     <SOH><echoed code><YYMMDDHHmm><value><ETX>

The LETTER picks the action, I/i Inquire (read), S/s Set (write),and the
CASE picks the response format, upper = display, lower = computer. Unknown
function codes answer 9999, which is how a tool discovers what a console does
not implement.

Deliberately hand-written rather than imported from any tool: a simulator that
shares its framing code with the thing under test cannot catch a framing bug.
"""
import json
import math
import os
import re
import socket
import threading
import time
import traceback

from .clock import clock_hhmm, clock_words
from .console import alarm_report_stamp, describe_alarms
from . import exposed
from . import fieldio
from . import listen
from . import units
from . import blankrows
from . import replytails
from . import alarmreports
from . import controls
from . import delivery
from . import recon
from . import hrmreports
from . import sumpreports
from . import isd
from . import packed
from . import readings
from . import versions
from . import wirelines
from . import wiretables
from tls350sim import formats
from tls350sim import wirelists
from tls350sim import wirelater
from . import wiresensors

# The report families that live in their own modules, because one if-chain
# for five hundred function codes is not a design. Each one answers
# (reply, note) for a code it owns, or None to say "not mine".
EXTRA_REPORTS = (wiresensors.handle, wirelines.handle,
                 wirelists.handle, wirelater.handle)
EXTRA_SETS = (wirelists.handle, wirelater.handle)

# display format "includes all the necessary formatting characters such as
# carriage returns, line feeds, nulls, spaces, labels"
SEP = chr(13) + chr(10)

SOH = b"\x01"
ETX = b"\x03"
CR = b"\r"

# "If the system receives a command message string containing a function code
# that it does not recognize, it will respond with a <SOH>9999FF1B<ETX>."
NOT_UNDERSTOOD = SOH + b"9999FF1B" + ETX

# 888's connect type for a command that arrived on the serial port,
# "06=RS232 REQUEST". See `note_comm`.
RS232_REQUEST = "06"


# What the manual titles each Display response and the columns it draws it
# in, read off its own pages by tools/build_wire_titles.py. It lives in
# `wiretables` so that a renderer in any of the wire modules can read it
# without importing this one back. Absent for a code the manual prints no
# display response for, which is why every use is a .get().
WIRE_TITLES = wiretables.TITLES


def checksum(message):
    """The four ASCII-hex characters a computer-format reply ends with.

    "The four characters represent a 16-bit binary count which is the 2's
    complemented sum of the 8-bit binary representation of the message
    characters ... The binary result should be zero." Which is what makes
    <SOH>9999 come out as FF1B.
    """
    return f"{(-sum(message)) & 0xFFFF:04X}"


def stamp(console=None):
    """The console's own clock, which is not necessarily this machine's."""
    t = console.now() if console is not None else time.localtime()
    return time.strftime("%y%m%d%H%M", t)


# The short command a technician is taught for a comms check. It is not in
# the Serial Interface Manual, which says a function code is six characters,
# but it is in Veeder-Root's own TCP/IP Interface Module installation manual
# (577013-895), twice, as the way to prove a console is talking:
#
#     3. Type: <ctrl+A>200
#     4. Press Enter. The console's inventory will appear - this confirms good
#        communication between the laptop and console.
#
#     Example for TLS Inventory: c:\>TELNET 10.2.11.17 10001 <CTRL A>200
#
# Three digits and a Return, which cannot be confused with the six-digit
# security code that may also lead a command, so the console can tell them
# apart. It answers the In-Tank Inventory Report for every tank.
SHORT_COMMANDS = {"200": "I20100"}


# The five function codes whose own notes print a CEILING on the device
# number, rather than the usual `00=All` with nothing above it. 576013-635
# Rev AA gives 412, 8C1, 8C2, 8C3 and BB1 all the same one --
# `xx - VMC Controller Number (Decimal, 01-18, 00=all)` -- and 576013-610
# p.26-1 says it again in prose: "You can generate a report for up to 18 VMC
# controllers."
#
# Asked for controller 19 or 99 this console did not refuse, it INVENTED:
# each row came back with a serial number, a status and a rate, because
# `readings.fixed` hashes the device number, so a tool walking device
# numbers to discover a site found every one of them occupied and healthy.
#
# Only these five are bounded here. Every other code's notes read
# `TT - Tank number, 00=All tanks` with no upper bound, and what a real
# console does there is a question rather than a defect. FIDELITY S28.
DEVICE_MAX = {"412": 18, "8C1": 18, "8C2": 18, "8C3": 18, "BB1": 18}


def _sent_token(raw, folded):
    """The three token characters as the caller typed them.

    `parse_command` folds the token to upper case so one dispatch table can
    serve either format, but the reply echoes what was sent.
    """
    try:
        body = raw.lstrip(SOH).rstrip(CR).decode("ascii", "replace")
    except AttributeError:
        return folded
    if body.endswith("\r\n"):
        body = body[:-1]
    body = body.rstrip("\r\x00\x03")
    if body in SHORT_COMMANDS:
        body = SHORT_COMMANDS[body]
    if len(body) > 6 and body[0].isdigit():
        body = body[6:]
    sent = body[1:4]
    return sent if sent.upper() == folded else folded


def parse_command(raw):
    """(security, letter, token, device, data)."""
    # Latin-1, so a byte past 7F reaches the field as itself rather than as
    # U+FFFD: what a real console does with one depends on the field, and a
    # label maps it to `A` (see `fieldio`). UNKNOWNS A76.
    body = raw.lstrip(SOH).rstrip(CR).decode("latin-1")
    # ETX ends a command as CR does (`TERMINATORS`), and is no more data
    # than CR is: the bench TLS-350 answered `I61F00<ETX>` as it answers
    # `I61F00<CR>`, where this took the ETX as a delivery type (2026-09-23).
    # A line feed is data (`TERMINATORS`) but for the one a telnet client
    # sends after its return, which a session drops as noise before the next
    # SOH and a caller handing over the whole line has not yet dropped.
    if body.endswith("\r\n"):
        body = body[:-1]
    body = body.rstrip("\r\x00\x03")
    if body in SHORT_COMMANDS:
        body = SHORT_COMMANDS[body]
    security = ""
    if len(body) > 6 and body[0].isdigit():
        security, body = body[:6], body[6:]
    if len(body) < 6:
        return security, "", "", "", ""
    return security, body[0], body[1:4].upper(), body[4:6], body[6:]


# Every function this console has. A settable one comes from the serial
# manual's own list of Set functions; the rest are the reports it answers.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "set_function_names.json"), encoding="utf-8") as _fh:
    SETTABLE = set(json.load(_fh))

# The Troubleshooting Guide's own list of what a technician collects,
# "<Ctrl-A> I20100 INVENTORY REPORT": is the set that matters most here, so
# every code on it is in this table.
REPORTS = {"101", "102", "111", "112", "201", "202", "203", "204", "205",
           "206", "207", "208", "20A", "20B", "20C", "20D", "211", "217",
           "218", "219", "21A", "221", "251", "56A", "63B", "780", "7A0",
           "902", "905", "A01", "A02", "A03", "A04", "A05", "A06", "A07",
           "A10", "A11", "A12", "A13", "A14", "A15", "A20", "A21", "A22",
           "A23", "A51", "A52", "A53", "A54", "A55", "B71",
           "B91", "B93", "B94", "C03", "C04", "132"}
REPORTS |= set(recon.RECON)
REPORTS |= {"113", "114", "115", "116", "119", "11A", "11B"}
# 121 is in no manual on this shelf -- not in the body, not in the index --
# and a real console answers it. `tests/console_capture/raw/I12100.bin` comes
# back with `ACTIVE ALARMS REPORT` over
# `ID  CATEGORY  DESCRIPTION          ALARM TYPE             DATE    TIME`,
# which is 113's title and 113's heading, character for character; 120 and
# 122 either side of it are refused. This console refused all three.
#
# The captured console had no alarms standing, so what the capture CANNOT say
# is whether 121 differs from 113 in which alarms it lists -- both are empty
# on a quiet console. Answering it as 113 is the reading that fits everything
# known; the alternative, going on refusing a code the hardware answers, fits
# nothing. See FIDELITY S17.
REPORTS |= {"121"}
# 7C3 is in no revision of 576013-635 on this shelf either, and the bench
# TLS-350 answers it: TANK MAXIMUM VOLUME LIMIT, 132 gallons on every tank
# out of a cold start (2026-09-18). `blankrows.TABLES` draws it.
REPORTS |= {"7C3"}
# 7B0 and 7B3 likewise: the meter map's column header, and a reply that is
# three question marks. `Handler.NO_BIR` draws both.
REPORTS |= {"7B0", "7B3"}
# ...and none of the three draws the station header on the bench, which a
# code with no page to read it off would otherwise be given for being here
NO_PAGE_NO_HEADER = {"7C3", "7B0", "7B3"}
# Reports whose frame was measured on a real console where the manual's
# typeset sample says otherwise: whether the station header is drawn, how
# many blank lines follow it, and -- by being here -- that the report draws
# its own blank lines and the sample's are not added (`wiretables.lead`).
# 207 off a version 23 site (2026-10-07): the header, two blank lines, and
# the title over every tank, where the sample has none of the three.
# 212 is the same report on the page (p.74 against p.65) and follows it.
# 20A and 391, the same site: the header and two blank lines.
MEASURED_FRAME = {"207": {"station": True, "head_gap": 2},
                  "212": {"station": True, "head_gap": 2},
                  "20A": {"station": True, "head_gap": 2},
                  "391": {"station": True, "head_gap": 2}}
# and three more with no page, answered with what the bench holds:
# `Handler.FIXED`
REPORTS |= {"51F", "55D", "535"}
NO_PAGE_NO_HEADER |= {"51F", "55D", "535"}
# 617, CSLD's custom probability of detection, is in no revision either; the
# bench answers its title. `Handler.NO_LIVE_TANK`.
REPORTS |= {"617"}
NO_PAGE_NO_HEADER |= {"617"}
# 904, WPLLD DIAGNOSTIC DATA, answers on the bench with no WPLLD card in it
# (`Handler._bench_state_reply`); it is in no revision of the code manual
REPORTS |= {"904"}
NO_PAGE_NO_HEADER |= {"904"}
# 5FA is the display itself: `Handler._glass_reply`. The other four of its
# family on the bench (5F0 annunciator status, 5F2 local print, 5F4, 5F8)
# are service functions that change things, and 5F4 took the bench console
# down; none of them is answered here.
REPORTS |= {"5FA"}
NO_PAGE_NO_HEADER |= {"5FA"}
REPORTS |= {"212", "213", "214", "215", "216", "21B", "222",
            "225", "226", "227", "281", "282", "2E2"}
REPORTS |= {"A56", "A61", "A62", "A63", "A81", "A91", "B61", "B62"}


# B61's value block is 24 characters wide and its value is set against the
# right of it, every line of it: 576013-635 Rev AA p.540 runs each of them
# from x=72 to x=221.9, which at that manual's 6.0 points per character is
# column 0 to column 24 exactly. 577013-937 Rev J Figure 42, a real console's
# printout of the same report, measures the same 24 at its own pitch. It is
# the display's own width, and it is why `SERIAL NUMBER` carries no colon --
# there is no room for one.
VALVE_COLUMNS = 24


def _valve_line(label, value):
    """One line of B61's block, the value against column 24."""
    return f"{label}{str(value).rjust(VALVE_COLUMNS - len(label))}"
REPORTS |= set(sumpreports.SUMP_REPORTS) | {"391", "392", "411", "412",
                                            "680", "790", "888", "88D",
                                            "8A2", "8A3", "901", "903",
                                            "BA0", "BB1"}
# 7.7.2 ISD SETUP, plus V10 which is inquire-only. Held apart from SETTABLE
# because these gate on the ISD and PMC keys rather than on a card.
ISD_SETUP = set(isd.SETUP)
ISD_READ = {"V10", "V48", "V4A", "V4B"}
ISD_TABLES = {"V42", "V43", "V49"}
ISD_CONTROL = {"VC0", "VC1", "VC5", "VC8", "V85", "XE0"}
ISD_READ_ONLY = ({"V51", "V00", "V01", "V02", "V03", "V0A", "V0B", "V83"}
                 | set(isd.DETAIL))
ISD_BUFFERS = {"V80", "V81"}

ACTIONS = ({"051", "052", "053", "054", "081", "082", "083",
            "084", "091", "851", "852", "853", "087", "088", "089", "090"}
           | set(controls.SYSTEM_ACTIONS) | set(controls.DEVICE_ACTIONS))
# The settable ones from 7.2 and 7.3 that this console had not carried, and
# the one action beside them: 79E clears the tank map behind a trailing 149.
SETTABLE |= {"52D", "7AE", "882", "889", "8A4", "8C1", "8C2", "79D"}
ACTIONS |= {"79E"}

# The commands the Troubleshooting Guide uses and the Serial Interface Manual
# has never carried, in any revision. Chapter 12 asks a technician for four of
# them by name -- "17. <Control-A> I@B600 AccuChart Diagnostics - Calibration
# Status" -- and prints the fifth's output, so they are as real as anything
# else here; they are simply unpublished. The shape is the console's usual
# one, letter then three characters then two digits: I@B600 is tank 00 and
# I@B601 is tank 1, which is what the guide's own two samples show.
AT_COMMANDS = {"@A0": "meter map diagnostics",
               "@A4": "basic reconciliation history",
               "@A9": "ASR error event history",
               "@B6": "accuchart calibration status",
               "@B9": "tank calibration data"}

# Functions the manual will not let you set without confirming: "149 - This
# verification code must be sent to confirm the command", <SOH>S53000x149.
# 081 to 084 want it too: "149 - This verification code must be sent to
# confirm the command", <SOH>S081QQ149.
# Two settings this console used to hold outside the wire format because
# Revision U of the Serial Interface Manual had no code for them. Revision Y
# does: 55E for fiscal height security and 642 for the water alarm filter.
SETTABLE |= {"55E", "642"}

# Revision AA's settable additions. `set_function_names.json` was built from
# Revision U's Set list, so it stops eleven software versions short of these.
SETTABLE |= {"550", "551", "581", "648", "64B",
             "651", "652", "653", "654", "655",
             "7D7", "7D8", "7D9", "7DA", "7DB", "7DC",
             "811", "812", "813"}

# The codes the manual gives a Set format for and NO Inquire format at all.
# Asked to read one, a console has nothing to answer with -- these are things
# you DO, not things it holds: System Reset, Clear In-Tank Delivery Reports,
# Start Pressure Line Leak Test by Type, Set BOL number.
#
# This is the same shape of mistake as a Set against an Inquire-only code
# being acknowledged, which was fixed three times in the C block and the ISD
# block. It is worth being blunt about the symptom, because it hides well: an
# Inquire to one of these used to come back as a header and a timestamp with
# NOTHING after it, which reads as "answered" to anything counting replies and
# as an empty setting to anything reading one.
# The mirror of SET_ONLY: codes the manual gives an Inquire format for and no
# Set format at all. A report is not a setting -- there is nothing to write --
# and a console asked to write one has the same answer as for a code it has
# never heard of.
#
# This was fixed three times in single blocks (the ISD read-only codes, then
# the C reconciliation range, then again) before anyone asked the general
# question, and the general answer was that 170 of them still took a Set and
# stored it. A tool sweeping the range would have been told its write
# succeeded.
#
# Built from Revision Y, not Revision U. Deriving it from U missed `132`,
# Fiscal Height Security Report, which Rev U does not carry at all -- a report
# that took a Set and stored it. A rule about what the manual documents has to
# be built from the latest manual on the shelf, or it inherits that manual's
# gaps as permissions.
#
# Six codes the manual calls Inquire-only are EXCLUDED, because a real site's
# .vrset holds them as settings: 680, 773, 780, 790, 7A0 and 887. That file
# came off a live console, and a rule that refuses what a real backup restores
# is a rule that breaks the restore. Whether the console truly accepts those
# six or the tool merely saved them is not settled here -- see UNKNOWNS C6.
INQUIRE_ONLY = {
            "101", "102", "111", "112", "113", "114", "115", "116", "119", "11A",
            "11B", "121", "132", "201", "202", "203", "204", "205", "206", "207", "208",
            "20A", "20B", "20C", "20D", "211", "212", "213", "214", "215", "216",
            "217", "218", "219", "21A", "21B", "221", "225", "226", "227", "251",
            "281", "282", "2E2", "301", "302", "306", "307", "311", "312", "315",
            "316", "317", "318", "319", "31A", "322", "323", "333", "341", "342",
            "346", "347", "34B", "34C", "351", "352", "353", "373", "374", "381",
            "382", "383", "384", "386", "387", "388", "389", "391", "392", "401",
            "402", "403", "404", "406", "411", "412", "56A", "888", "88D", "8A2",
            "8A3", "901", "902", "903", "905", "A01", "A02", "A03", "A04", "A05",
            "A06", "A07", "A10", "A11", "A12", "A13", "A14", "A15", "A20", "A21",
            "A22", "A23", "A51", "A52", "A53", "A54", "A55", "A56", "A61", "A62",
            "A63", "A81", "A91", "B01", "B06", "B07", "B11", "B21", "B33", "B34",
            "B35", "B36", "B37", "B38", "B39", "B41", "B46", "B4B", "B50", "B51",
            "B52", "B61", "B62", "B71", "B72", "B7B", "B7C", "B7D", "B7E", "B7F",
            "B81", "B82", "B83", "B87", "B88", "B89", "B8A", "B8B", "B8C", "B8D",
            "B8E", "B91", "B93", "B94", "BA0", "BA1", "BB1", "C01", "C02", "C03",
            "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C20",
            "C21", "C22", "C25", "V00", "V01", "V02", "V03", "V04", "V05", "V06",
            "V07", "V08", "V09", "V0A", "V0B", "V10", "V12", "V48", "V4A", "V4B",
            "V51", "V52", "V82", "V83", "V88", "VA1", "VA2", "VA3",
}

SET_ONLY = {"001", "002", "003", "010", "031", "051", "052", "053", "054",
            "081", "082", "083", "084", "087", "088", "089", "090", "091",
            "092", "093", "094", "095", "096", "097", "098", "099", "09A",
            "09B"}
# 7B6 was on it, and the bench TLS-350 answers `I7B600` and `i7B600` with a
# bare frame once a CR follows them (2026-09-22, `WAITING_INQUIRIES`): its
# Inquire is not in the manual, and it is there.

# 7.3.3's Service Notice block, less 566 -- which is the FEATURE switch, has a
# panel step and a field, and so reads back down the generic path like any
# other stored value. These three do not: 567 is a setting the console keeps
# for itself, 568 is not a stored value at all but whether a session is open,
# and 569 is the duration that session runs for. See FIDELITY D9.
SERVICE_NOTICE = {"567", "568", "569"}
# The two codes that answer with a RESULT CODE of their own rather than 9999.
TICKET_CODES = {"7B5", "7B6"}
_ON_OFF = {"1": "enabled", "0": "disabled"}

VERIFIED = {"530": "149", "081": "149", "082": "149",
            "083": "149", "084": "149",
            # "7.3.13 EEPROM SETUP": restore, save and clear all want it,
            # <SOH>S85100149
            "851": "149", "852": "149", "853": "149",
            # "Set AccuChart Calibration Restart ... 149 - This verification
            # code must be sent to confirm the command"
            "891": "149"}
# The sensor family are reports, so they carry the station header the way the
# manual's own samples do; the line family builds its own header, because
# several of its reports put a line of their own above it.
REPORTS |= wiresensors.CODES | set(AT_COMMANDS)

# Forty-four codes in no revision of 576013-635 on this shelf that the bench
# TLS-350 (326.01) knows: asked in both formats on 2026-09-18 (`cap_swept`),
# every one answered a bare frame rather than 9999FF. The cage had none of
# the cards most of them plainly belong to -- 724 to 734 sit in the smart
# sensor band, 7D0 to 7D4 past the I/O band, V4C to VC4 among the ISD codes
# -- so a bare frame is what an absent card gets (`Handler._absent`), and
# what any of them says with its card in is unknown. Inquiries only: none
# was sent a Set. FIDELITY S33. Two of the forty-four were only bare for a
# console with no line on -- 375 and 385 are the pressure line results
# report once one is (`cap_site`, `wirelines`) -- and are not here.
#: The newest software known NOT to take 786's MANIFOLDED: ALTERNATE-HT:
#: the bench TLS-350's 326.01 refused `S786015` (2026-09-18). When it
#: arrived is on no page on the shelf. See `Handler._bench_set_gate`.
ALTERNATE_HT_AFTER = 26

#: Numbered console-wide slots -- the print header's four lines and the four
#: shift start times -- whose report is one line about one of them. Asked for
#: all of them at once, the bench TLS-350 answers a bare frame in both
#: formats, whatever is programmed: `I50300` with two header lines set and
#: `I50200` with three shift times (2026-09-19, `transcripts/header503b`,
#: `shifts2`). A device family's codes list every position instead -- 52B
#: prints all eight receivers -- so this is the slots' own rule.
NUMBERED_SLOTS = {"502", "503"}

UNDOCUMENTED_BARE = {
    "209", "20E", "20F", "331", "332", "38A", "510", "539",
    "53A", "53B", "53C", "53D", "53E", "53F", "540", "541", "542", "543",
    "724", "725", "726", "731", "732", "733", "734", "762", "78D", "7C1",
    "7C2", "7D0", "7D2", "7D3", "7D4", "892", "B31", "B53", "B54", "B92",
    "V4C", "V4D", "VC3", "VC4",
    # three more that wait for a data field (`WAITING_INQUIRIES`) and were
    # on no capture until a CR was sent after them (2026-09-22): C24 and C30
    # then answer bare, and E20 answers nothing even so
    "C24", "C30", "E20",
    # and the service family's Inquiries, which are bare too. Their SETS
    # change things -- `S5F400` with data put the bench in a boot loop --
    # and stay refused here.
    "5F0", "5F2", "5F4", "5F8"}

KNOWN = (SETTABLE | REPORTS | ACTIONS | ISD_SETUP | ISD_READ | ISD_TABLES
         | ISD_CONTROL | ISD_READ_ONLY | ISD_BUFFERS | set(VERIFIED)
         | wiresensors.CODES | wirelines.CODES | wirelater.MINE
         | UNDOCUMENTED_BARE)

# The eleven Revision Y documents and Revision U does not are settable where
# the manual gives them a Set format, which is three of them.
SETTABLE |= wirelater.MINE - wirelater.INQUIRE_ONLY

# Every function code the Serial Interface Manual documents, parsed out of
# section 7 of 576013-635 Rev U rather than typed in. Answering a code this
# console does not implement with 9999 is right, and it is what the manual
# says: "a function code that it does not recognize". Silence is NOT right,
# and a tool sweeping the ranges reads silence as a console that has fallen
# over. `python -m tests.coverage` prints what is still missing, and
# UNKNOWNS.md keeps the list.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "functiondata.json"), encoding="utf-8") as _fh:
    DOCUMENTED = json.load(_fh)


# "tt - In-Tank Leak Test Type" in I207
TEST_TYPE_CODE = {"periodic": "00", "annual": "01", "gross": "02"}

def alarm_history_code(nn):
    """"aaaa - Type of alarm" in I206.

    It is i10100's alarm list over again, but numbered in hex where i10100
    numbers it in decimal: NN 11, Tank Delivery Needed Warning, is 000B.
    """
    return f"{int(nn):04X}" if nn.isdigit() else "0000"


def _when(packed):
    """YYMMDDHHmm as the display format writes it: "DEC 22, 1995  3:31 PM"."""
    try:
        t = time.strptime(packed, "%y%m%d%H%M")
    except ValueError:
        return packed
    return clock_words(t)


def _stamp_words(stamp):
    """A stored YYMMDDHHmm printed the way a console prints a date."""
    try:
        return clock_words(time.mktime(time.strptime(stamp, "%y%m%d%H%M")))
    except (ValueError, OverflowError):
        return stamp


class Handler:
    def __init__(self, console, verbose=True, log=None):
        self.c = console
        self.verbose = verbose
        self.log = log
        self._token = None

    def _frame(self, code, value=""):
        """A reply, in whichever format the letter's case asked for.

        Computer format is "SOH Function Code Data Field && Checksum ETX",
        and the checksum covers everything before it, the SOH included.

        Display format "is intended for display on a CRT or printer. It
        includes all the necessary formatting characters", and the manual's
        own examples show what that means, the echoed code, the date and
        time as the console writes it, and the station header, before the
        report itself:

            <SOH>
            I10100
            JUL 29, 1997  9:02 AM
            STATION HEADER 1....
            SYSTEM STATUS REPORT
              ALL FUNCTIONS NORMAL
            <ETX>
        """
        if code[:1].islower():
            # `errors="replace"` on the VALUE, which is the same choice the
            # display form below already makes and for the same reason: a
            # site's own text reaches here, and a byte the console cannot
            # send is not a reason to refuse a function code it knows.
            #
            # It used to encode the value strictly, so one character out of
            # range raised, `handle` turned the raise into `9999FF`, and an
            # inquiry answered the refusal the manual keeps for "a function
            # code that it does not recognize". A label set over the port
            # with a Latin-1 byte in it -- `S60201CAF\xc9` -- put `i60201`
            # into that state permanently, while `I60201` went on answering,
            # because only one of these two branches had been made safe.
            # The checksum is taken after the substitution, so it covers
            # what is actually sent. See FIDELITY S27.
            body = (SOH + code.encode("ascii") + stamp(self.c).encode("ascii")
                    + printable_body(value).encode("ascii", "replace"))
            # the checksum covers "all the characters preceding it", which
            # includes the && tag itself
            body += b"&&"
            return body + checksum(body).encode("ascii") + ETX
        lines = [code, self.c.clock_stamp()]
        body = value.lstrip(SEP)
        if body and self._draws_station(code[1:4]):
            # All four, programmed or not. 576013-635 draws its samples
            # with STATION HEADER 1 through 4 standing where a site's own
            # lines would be, so a site that has set only the first still
            # sends four. Same rule as the paper's header. FIDELITY W5.
            #
            # Confirmed on a real TLS-350 with NO header programmed: I10100
            # still carries four blank lines before SYSTEM STATUS REPORT. An
            # earlier reading of this bench -- that a console with no header
            # sends none -- was wrong, and came from I20100, which sends
            # nothing at all for a different reason.
            #
            # That reason is the `body and` above. A report with no content
            # carries no header block either: the same console answers
            # I20100 with the code, the stamp and nothing else, because it
            # has no tanks. Header lines belong to a report, not to a reply.
            # ONE BLANK LINE between the stamp and the header block, which
            # is what the manual draws in every one of the 128 samples that
            # print a header at all, and what a real console's own I10100
            # carries -- six blank lines before SYSTEM STATUS REPORT on a
            # console with nothing programmed, where this sent five. The
            # blank BELOW the block was already here; the one above it was
            # not. See FIDELITY S6.
            lines.insert(2, "")
            lines += [self.c.header_line(n) for n in range(1, 5)]
        if body and self._head_gap(code[1:4]):
            # ONE BLANK LINE between the header block and the body, which is
            # what 576013-635 draws and this console did not. Measured across
            # the manual rather than argued: of 321 display samples whose
            # header block is followed by anything, 304 leave a full line's
            # gap after it and nine do not -- and the rule holds whether the
            # station header block is there or not, so it belongs here and
            # not in the reports.
            #
            # It looked as though the blank was already there, and that is
            # the trap: a site with three programmed station header lines
            # sends an EMPTY fourth, and an empty fourth reads exactly like
            # the missing blank. Programme all four and the body ran straight
            # onto the header. See FIDELITY S6.
            lines += [""] * self._head_gap(code[1:4])
        # A report's own blank lines, where the manual's sample leaves them,
        # and one line ending rather than two: the header block was joined
        # with CR/LF and several report bodies with a bare line feed, so one
        # reply carried both. See FIDELITY S6.
        if code[1:4] not in MEASURED_FRAME:
            body = wiretables.lead(code[1:4], body)
        text = SEP.join(lines) + SEP + body.replace(SEP, chr(10)).replace(
            chr(13), chr(10)).replace(chr(10), SEP)
        # and the trailing spaces the bench keeps, which every comparison
        # that strips a line never saw (`wiretables.pad_line`)
        rows = text.split(SEP)
        text = SEP.join(rows[:2] + [wiretables.pad_line(code[1:4], row)
                                    for row in rows[2:]])
        # A display reply opens SOH CR LF and closes CR LF ETX. The manual
        # draws the text of a report and not its framing, so this was written
        # without the two line endings for a long time. Measured on a real
        # TLS-350 (software 326.01) over its TCP/IP card: all 49 replies
        # captured begin SOH CR LF and end CR LF ETX, without exception.
        return SOH + SEP.encode("ascii") \
            + printable_body(text).encode("ascii", "replace") \
            + SEP.encode("ascii") + ETX

    def _devices_of(self, module, dev):
        """One device, or every one the module carries."""
        if dev != "00" and dev.isdigit() and int(dev):
            return [int(dev)]
        return list(range(1, self.c.capacity(module) + 1))

    def _chart_step(self, data, code):
        """I211's height step: six decimal digits, or a packed float."""
        text = (data or "").strip()
        if not text:
            return 1.0
        if code[0].isupper():
            try:
                return max(float(text) / 1000.0, 0.010)
            except ValueError:
                return None
        try:
            return max(packed.unhexfloat(text[:8]), 0.010)
        except ValueError:
            return None

    @staticmethod
    def _tanker_stamp(when):
        """`97/07/24 07:57`, which is how the tanker load reports write a time.

        576013-635 draws 391 and 392 with placeholders rather than a made-up
        date -- `YY/MM/DD HH:mm` -- and the COLUMNS are what say that is the
        format rather than a lazy sample. p.146 gives the start group columns
        4 to 24, twenty-one characters, holding the stamp, a space and a
        six-digit volume; `clock_words` is twenty-one characters on its own
        and would run through the volume and out the other side. The slashes
        and the colon are not in the computer format either, so somebody
        chose them.
        """
        return time.strftime("%y/%m/%d %H:%M", time.localtime(when))

    # 576013-635 Rev AA p.474 and p.475. The block per port is a block per
    # ERROR: "nn - Number of Errors to follow for each port", and the four
    # UART lines are the settings "During Error", so a port that has not
    # failed prints its connection and nothing else -- which is exactly what
    # the sample's port 1 does. The two TIME lines are inside the port's own
    # record in the computer format and the sample prints them under the port
    # that has them, which is the reading that makes both halves agree.
    #
    # CONNECTION is the connect type of the last connection a port
    # established, and the sample is what says "last" rather than "right
    # now": port 1 there reads NONE and has no TIME OF LAST COMM DATA line
    # at all, while port 2 reads MODEM DIAL IN over a last-comm stamp of
    # 9:12 AM. A port that has never carried data has neither; a port that
    # has carried data has both. `note_comm` keeps the pair together, and
    # `console.comm_connect` is where the type lands. See FIDELITY S13.
    COMM_STATE = {0: "NONE", 1: "OPEN PHONE PORT",
                  2: "MODEM CHECK CONNECTION", 3: "TRANSMITTING DATA",
                  4: "CHECKING FOR CARRIER", 5: "WAITING FOR DATA",
                  6: "HANGING UP", 7: "FAXMODEM INITIALIZING",
                  8: "FAX CHECK CONNECTION", 9: "FAX CHECK PAGE",
                  10: "FAX END PAGE", 11: "FAX BUILD MESSAGE"}
    COMM_ERROR = {1: "UART SETTINGS ERROR", 2: "MODEM INITIALIZATION FAILED",
                  3: "MODEM TIMED OUT", 4: "LOST CARRIER",
                  5: "DATA TIMED OUT", 6: "HANG UP FAILED",
                  7: "FAX INITIALIZATION FAILED", 8: "FAX CONNECTION FAILED",
                  9: "FAX TIMED OUT", 10: "FAX INTERPAGE ERROR",
                  11: "FAX END PAGE ERROR", 12: "FAX BUILD MESSAGE ERROR"}
    CONNECT_TYPE = {0: "NONE", 1: "AUTO DIAL TELETYPE", 2: "AUTO DIAL FAX",
                    3: "AUTO DIAL COMPUTER", 4: "AUTO TRANSMIT",
                    5: "MODEM DIAL IN", 6: "RS232 REQUEST"}

    UART_PARTS = ("baud", "parity", "stop", "data")

    @staticmethod
    def _uart_field(port, part):
        from .console import FIELDS
        return (FIELDS.get(f"S881{port:02d}.{part}")
                or FIELDS.get(f"S88101.{part}"))

    def _port_uart(self, port):
        """One port's UART settings as the manual ENCODES them, `01200 0 1 7`.

        These are 881's part fields, which the COMMUNICATIONS SETUP report
        already prints off the same console; 888 was answering 9600, odd, one
        stop and eight data for every port whatever was programmed. The codes
        rather than the words, because the computer format sends BBBBB, P, S
        and D and the display side can always look them back up.
        """
        from .screens import shown
        key = f"S881{port:02d}"
        stored = self.c.values.get(key)
        out = {}
        for part in self.UART_PARTS:
            field = self._uart_field(port, part)
            word = shown(self.c, field,
                         fieldio.decode(field, key, stored)
                         if field and stored is not None else "")
            out[part] = next(
                (str(choice[0]) for choice in (field or {}).get("choices") or ()
                 if str(choice[1]) == str(word)), str(word))
        return out

    def _uart_words(self, port, codes):
        """The same four settings as the display prints them."""
        out = {}
        for part in self.UART_PARTS:
            field = self._uart_field(port, part)
            value = str(codes.get(part, ""))
            out[part] = next(
                (str(choice[1]) for choice in (field or {}).get("choices") or ()
                 if str(choice[0]) == value), value)
        return out

    def _comm_status(self, ports):
        """888's display format: a block per port, and its errors under it."""
        rows = []
        for n in ports:
            rows.append(f"COMM BOARD  : {n} ({self.c.comm_board_name(n)})")
            rows.append(" CONNECTION : " + self.CONNECT_TYPE[
                int(self.c.comm_connect.get(n) or 0)])
            for one in self.c.comm_errors.get(n) or ():
                uart = self._uart_words(
                    n, one.get("uart") or self._port_uart(n))
                rows += [
                    " FUNCTION   : "
                    + self.COMM_STATE.get(one.get("state", 0), "NONE"),
                    " ERROR      : "
                    + self.COMM_ERROR.get(one.get("error", 0), "NONE"),
                    f" BAUD RATE  : {uart['baud']}",
                    f" PARITY     : {uart['parity']}",
                    f" STOP BIT   : {uart['stop']}",
                    f" DATA LENGTH: {uart['data']}"]
            for label, which in (("TIME OF LAST COMM DATA:", "comm_data_at"),
                                 ("TIME OF LAST COMM ERROR:", "comm_error_at")):
                when = getattr(self.c, which).get(n)
                # and only under a record: the bench's board carried two days
                # of sessions with no stamp of either kind until one of them
                # was cut off mid-command (`Console.session_cut`)
                if when and self.c.comm_errors.get(n):
                    # p.474 puts both stamps at column 25, and it is
                    # `clock_words` here rather than the wide form
                    rows.append(f"{label:<25s}{clock_words(when)}")
            # a blank line closes every port's block: the bench TLS-350's
            # I88800, both boards, 2026-09-18
            rows.append("")
        return rows

    def _shift_rows(self, tank):
        """The shifts this tank has closed, oldest first, then the one open."""
        closed = list(reversed(self.c.bir.closed.get((tank, "shift")) or []))
        return (closed + [self.c.bir.current(tank, "shift")])[-3:]

    def _gauges(self, tank, row, which):
        """Volume, ullage, TC volume, height, water and temperature at one
        end of a shift.

        Every one of them off the ROW. The volume, the ullage and the height
        always were; the TC volume and the temperature were the live tank,
        for both ends, so a shift that sold anything printed its opening
        volume beside the CLOSING volume's correction. `bir` has stored
        `temp_open` and `temp_close` on the row all along and neither was
        read. See FIDELITY X4.
        """
        opening = which == "opening"
        volume = row[which]
        full = self.c.full_volume(tank) or 0.0
        water = row["water_open"] if opening else row["water"]
        temp = row.get("temp_open" if opening else "temp_close")
        if temp is None:
            # a row written before either stamp existed, or restored from a
            # saved state that predates them
            temp = self.c.product_temperature(tank)
        return [volume, max(full - volume, 0.0),
                self.c.tc_volume_at(tank, volume, temp),
                self.c.height_at(tank, volume), water, temp]

    def _adjusted_delivery_report(self, tok, tanks, records_of):
        """20A and 20B's display body, headed and spaced as a version 23
        site printed them (2026-10-07). They are two reports: 20A heads a
        tank `TANK  1  REGULAR UNLEADED      MAG` over INCREASE columns and
        leaves three blank lines after it; 20B is BIR ADJUSTED DELIVERY
        REPORT, `T 1:` and its own START/END columns. This printed 20A's
        for both. The site had no BIR and no rows, so a row is this
        console's own layout under the site's headings."""
        if tok == "20A":
            rows = ["ADJUSTED DELIVERY REPORT", ""]
            for tank in tanks:
                label = self.c.text("602", tank) or f"TANK {tank}"
                kind = (self.c.probe_type(tank) or "").split()[:1]
                rows.append(f"TANK {tank:2d}  {label:<22.22s}"
                            + (kind[0] if kind else "").rstrip())
                rows.append("                       INCREASE   INCREASE"
                            "            DELIVERY  DELIVERY")
                rows.append("INCREASE DATE/TIME       VOLUME  TC VOLUME"
                            "  ADJUSTMENT  VOLUME TC VOLUME")
                for record in records_of(tank):
                    rows.append(f"{clock_words(record.end['at']):22s}"
                                f"{record.amount:9.0f}"
                                f"{record.tc_amount:11.0f}"
                                f"{record.sold:12.0f}"
                                f"{record.amount + record.sold:8.0f}"
                                f"{record.tc_amount + record.sold:10.0f}")
                rows += ["", "", ""]
            return SEP.join(rows)
        rows = ["BIR ADJUSTED DELIVERY REPORT", "", ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            rows.append(f"T {tank}:{label}")
            rows.append(" " * 48 + "START    END     ADJ   ADJ TC")
            rows.append("DELIVERY START   DATE   DELIVERY  END    DATE  "
                        "VOLUME  VOLUME   DELIV   DELIV")
            for record in records_of(tank):
                start = time.strftime("%m/%d/%y %H:%M",
                                      time.localtime(record.start["at"]))
                end = time.strftime("%m/%d/%y %H:%M",
                                    time.localtime(record.end["at"]))
                rows.append(f"{start:<23s}{end:<24s}"
                            f"{record.start['volume']:7.0f}"
                            f"{record.end['volume']:8.0f}"
                            f"{record.amount + record.sold:8.0f}"
                            f"{record.tc_amount + record.sold:8.0f}")
        return SEP.join(rows)

    def _tanker_row(self, n, one):
        """One 391 row under the site's headings: the number to column 2,
        each stamp at its DATE/TIME (4 and 36), and the figures held right
        against the end of GALLON, TEMP and TOTAL -- 26, 32, 57, 63, 71."""
        row = f"{n:2d}  {self._tanker_stamp(one['start'])}"
        row = f"{row:<19s}{one['start_vol']:7.0f}{one['start_temp']:6.1f}"
        row = f"{row:<36s}{self._tanker_stamp(one['end'])}"
        row = f"{row:<50s}{one['end_vol']:7.0f}{one['end_temp']:6.1f}"
        return f"{row}{one['total']:8.0f}"

    def _shift_records(self):
        """[(shift, record)]: each programmed shift's last closed occurrence,
        and the one running where none has closed yet."""
        sh = self.c.shifts
        out = [(n, sh.closed[n]) for n in sh.programmed() if n in sh.closed]
        if not out and sh.open is not None:
            out = [(sh.open["shift"], {"start": sh.open["start"],
                                       "end": None,
                                       "adjust": sh.open["adjust"]})]
        return out

    @staticmethod
    def _snap_values(snap, tank):
        """Volume, ullage, TC volume, height, water and temperature, in the
        order 204's computer format numbers them."""
        t = ((snap or {}).get("tanks") or {}).get(tank) or {}
        return [t.get("volume", 0.0), t.get("ullage", 0.0), t.get("tc", 0.0),
                t.get("height", 0.0), t.get("water", 0.0), t.get("temp", 0.0)]

    def _shift_gauges(self, record, tank):
        """(start, end, total, delivery) for one tank of one shift. TOTALS is
        the shift's sales -- opening, plus delivered, less closing: 6844 +
        2063 - 8175 = 732 on a version 23 site (2026-10-07)."""
        start = self._snap_values(record.get("start"), tank)
        end = self._snap_values(record.get("end"), tank)
        delivered = (record.get("adjust") or {}).get(tank, 0.0)
        total = (start[0] + delivered - end[0]) if record.get("end") else 0.0
        return start, end, total, delivered

    def _shift_report(self, tanks, shifts):
        """204's display body as a version 23 site printed it (2026-10-07):
        ` SHIFT REPORT`, each shift's start time, TANK PRODUCT, and a block a
        tank with VOLUME at column 28 and its figures ending at 34, 44, 52,
        60, 67 and 74. The manual's sample (p.65) has no title and a shift a
        line under each tank; the site had one shift programmed, so how a
        console with several heads them is not seen."""
        rows = [" SHIFT REPORT", ""]
        for shift, record in shifts:
            hhmm = (self.c.shifts.hhmm(shift) or "").strip()
            when = (f"{int(hhmm[:2]) % 12 or 12}:{hhmm[2:4]} "
                    f"{'AM' if int(hhmm[:2]) < 12 else 'PM'}"
                    if hhmm[:4].isdigit() else "")
            rows += [f"SHIFT {shift} TIME: {when:>8s}", "",
                     "TANK PRODUCT", ""]
            for tank in tanks:
                label = self.c.text("602", tank) or f"TANK {tank}"
                start, end, total, delivered = self._shift_gauges(record,
                                                                  tank)
                rows.append(f"{tank:3d}  {label:<23.23s}VOLUME TC VOLUME"
                            "  ULLAGE  HEIGHT  WATER   TEMP")

                def line(name, v):
                    vol, ullage, tc, height, water, temp = v
                    return (f"{name:<26s}{vol:8.0f}{tc:10.0f}{ullage:8.0f}"
                            f"{height:8.2f}{water:7.2f}{temp:7.2f}")
                rows += [line(f"SHIFT {shift:2d} STARTING VALUES", start),
                         line("         ENDING VALUES", end),
                         f"{'         DELIVERY VALUE':<26s}{delivered:8.0f}",
                         f"{'         TOTALS':<26s}{total:8.0f}", ""]
        return SEP.join(rows)

    def _shift_lines(self, tank, number, row):
        """One shift's block of the I204 display report."""
        def line(name, values):
            volume, ullage, tc, height, water, temp = values
            return (f"{name:<28s}{volume:8.0f}{tc:8.0f}{ullage:8.0f}"
                    f"{height:8.2f}{water:7.2f}{temp:7.2f}")
        start = self._gauges(tank, row, "opening")
        end = self._gauges(tank, row, "physical")
        return [line(f"SHIFT {number:2d} STARTING VALUES", start),
                line("         ENDING VALUES", end),
                # p.65 holds both summary figures right against 33,
                # which is five columns left of the VOLUME column above them
                f"{'         DELIVERY VALUE':<28s}{row['deliveries']:6.0f}",
                f"{'         TOTALS':<28s}"
                f"{row['physical'] - row['opening']:6.0f}"]

    # A setup report lists the tank POSITIONS the console has; an inventory
    # report lists the tanks that are programmed. Measured on a real TLS-350
    # with all four positions OFF: I60400, I60A00, I60C00, I61200, I61500 and
    # I61A00 each print four rows, and I20100 prints an empty body. The
    # families divide on the leading digit of the function code.
    POSITION_REPORTS = "6"

    def _tanks(self, dev):
        """"TT - Tank Number (Decimal, 00=all)"."""
        if dev != "00":
            if ((self._token or "")[:1] not in self.POSITION_REPORTS
                    and int(dev) not in self.c.tank_level):
                # one tank's measurements need its probe reporting too
                return []
            return [int(dev)]
        live = sorted(self.c.tank_level)
        if live:
            return live
        if (self._token or "")[:1] not in self.POSITION_REPORTS:
            # A tank with no probe reporting has nothing to measure, and a
            # report of measurements says nothing about it: the bench
            # TLS-350, its tank 1 labelled and given a full volume but with
            # no probe wired, answers I201, I205, I20C, I212 and every A-code
            # in-tank diagnostic with a bare frame, in both formats
            # (`cap_swept`, 2026-09-18). This fell back to the programmed
            # tanks and reported tank 1 at zero. FIDELITY S33.
            return []
        live = sorted(self.c.programmed_tanks())
        if live:
            return live
        # Nothing programmed. A setup report still has a row per position;
        # an inventory report has nothing to say and says nothing. Inventing
        # a tank 1 here, as this console used to, reported fuel in a console
        # that had none.
        if (self._token or "")[:1] in self.POSITION_REPORTS:
            return list(range(1, max(self.c.capacity("probe"), 0) + 1))
        return []

    def _inventory(self, tank, tok):
        """The seven numbers I201 reports, in the manual's own order."""
        st = self.c.tank_level.get(tank, {})
        # `water_height`, not the float's own depth: the Water Minimum
        # Threshold is a correction to the measurement rather than a gate on
        # an alarm, and the WATER VOLUME column beside this one has gone
        # through it all along. FIDELITY O27.
        volume, water = st.get("volume", 0.0), self.c.water_height(tank)
        full = self.c.full_volume(tank) or 0.0
        # The calibration chart, not a straight line: height_at() is what
        # I204, I214, the printed inventory and a delivery snapshot all use,
        # and 576013-635 Rev AA p.84's I21A row fixes it at 80.00 where the
        # fraction-of-diameter form gave 85.48 (X1).
        height = self.c.height_at(tank, volume)
        if tok == "21A":
            # the 90 or 95 percent the site programmed at S564
            share = 0.95 if (self.c.values.get("S56400")
                             or "").strip().endswith("1") else 0.90
            ullage = max(full * share - volume, 0.0)
        else:
            ullage = max(full - volume, 0.0)
        return [volume, self.c.tc_volume(tank), ullage, height, water,
                self.c.product_temperature(tank), self.c.water_volume(tank)]

    def _inventory_text(self, tanks, tok):
        """The report as the manual prints it, columns and all.

        With no tanks the body is empty -- not even the column header. A
        real TLS-350 with its four tank positions all OFF answers I20100
        with the frame, the code, the stamp and nothing else, 37 bytes in
        all. The manual only ever draws a site that has tanks, so the empty
        case had to come from hardware.
        """
        if not tanks:
            return ""
        if tok == "21A":
            share = "95%" if (self.c.values.get("S56400")
                              or "").strip().endswith("1") else "90%"
            rows = ["TANK PRODUCT             VOLUME TC VOLUME  "
                    f"{share} ULLAGE  HEIGHT    WATER     TEMP"]
            widths = "{:10.0f}{:10.0f}{:12.0f}{:8.2f}{:9.2f}{:9.2f}"
        else:
            # titled, with a blank line under it: a version 23 site's 201
            # (2026-10-07), where the manual's sample has no title
            rows = (["IN-TANK INVENTORY", ""] if tok == "201" else []) + [
                "TANK PRODUCT             VOLUME TC VOLUME   ULLAGE"
                "   HEIGHT    WATER     TEMP"]
            widths = "{:10.0f}{:10.0f}{:9.0f}{:9.2f}{:9.2f}{:9.2f}"
        for tank in tanks:
            label = self.c.text("602", tank) or ""
            v = self._inventory(tank, tok)
            # The display form's three whole-number columns are TRUNCATED,
            # the same rule the panel and the paper follow -- the COMPUTER
            # form is untouched, because it carries the float. FIDELITY Y11.
            rows.append(f"{tank:3d}  {label:<16.16s}"
                        + widths.format(int(v[0]), int(v[1]), int(v[2]),
                                        v[3], v[4], v[5]))
        return SEP.join(rows)

    def _inventory_data(self, tank, tok):
        """TTpssssNN then the seven floats, packed."""
        code = (self.c.text("603", tank) or " ")[:1] or " "
        bits = 0
        if self.c.deliveries.in_progress(tank):
            bits |= 1
        if self.c.leaks.active("tank", tank):
            bits |= 2
        values = self._inventory(tank, tok)
        return (f"{tank:02d}{code}{bits:04X}"
                + packed.hexfloats(values))

    # "f - Tank Water Alarm Filter Level: 1 = Low, 2 = Medium, 3 = High".
    # The panel offers an OFF as well, which the wire has no number for, so a
    # filter that is off reads back as its lowest setting.
    WATER_FILTER = {"1": "LOW", "2": "MEDIUM", "3": "HIGH"}

    def _at_command(self, tok, dev, code):
        """The five undocumented @ diagnostics, as chapter 12 prints them."""
        c = self.c
        if not c.has("probe"):
            return self._absent(code), "no probe module fitted"
        tanks = self._tanks(dev)
        if tok == "@B6":
            rows = c.accuchart.calibration_status_rows(tanks)
        elif tok == "@B9":
            rows = c.accuchart.calibration_data_rows(tanks)
        elif tok == "@A4":
            if not c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            # The daily history, which is what the title says: "I@A400
            # DAILY RECONCILIATION LIST FOR LAST 31 DAYS". This printed the
            # SHIFT report under it -- the wrong period, one row of it, in
            # the wrong layout. FIDELITY G12.
            rows = c.bir.history_report(tanks).split(chr(10))
        elif tok == "@A0":
            # No BIR gate here. A real console with no BIR in its feature
            # list -- I90200 shows only the in-tank tests, CSLD, PLLD and
            # WPLLD -- still answers I@A002 with the ballot grid. I@A400 is
            # the one that goes silent without BIR, and it keeps its gate.
            # The ballot, not the result. FIDELITY S11 had this printing a
            # four-column FP/METER/TANK/THROUGHPUT table, which is what the
            # mapping arrived at; the report exists to show it working, and
            # the guide's first instruction under it is "look for unmapped
            # or retired meters", neither of which a result table can show.
            #
            # Head and rule are a real console's, byte for byte: the state
            # line, a blank, the two-row column header and the rule. The
            # grid under them is `bir.ballot_rows`, whose rows are fueling
            # positions -- which is why it needed G7's key first.
            rows = ["MAP IS COMPLETE" if c.bir.map_complete()
                    else "MAP IS INCOMPLETE", "",
                    " FP|     METER         **TANK_MAP_BALLOT**",
                    "   |     0          1          2          3"
                    "          4          5     ",
                    c.bir.BALLOT_RULE]
            rows += c.bir.ballot_rows()
        else:
            # "ASR Error Event History Buffer": the console keeps what went
            # wrong, and the alarm log is where this console keeps it
            # Titled and bodied as a real console does it. FIDELITY S11
            # had the title a word short and the empty body reading
            # "NO ALARM HISTORY" -- W13's invented phrase, in a report that
            # is not an alarm history. The console prints a column header
            # and the single word EMPTY.
            rows = ["ASR ERROR EVENT HISTORY BUFFER", "",
                    "TIME         CODE MESSAGE"]
            events = [line for line in c.alarm_state_lines(priority=True)[1:12]
                      if line.strip() and "NO ALARM HISTORY" not in line]
            rows += events or ["EMPTY"]
        if code[0].isupper():
            return self._frame(code, SEP.join(rows)), AT_COMMANDS[tok]
        # No manual shows a computer format for these; the console answers the
        # display text rather than inventing a packing for it.
        return self._frame(code, SEP.join(rows)), AT_COMMANDS[tok]

    def _fiscal_and_filter(self, tok, dev, code):
        """55E, 132 and 642: three settings Revision Y added.

        Fiscal height security and the water alarm filter are both programmed
        on the panel and were both held only there, because the revision of
        the manual this simulator was built from has no function code for
        either. It has since turned out that a later revision does.
        """
        c = self.c
        if tok == "642":
            if not c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            words = {v: k for k, v in self.WATER_FILTER.items()}
            if code[0].isupper():
                rows = ["WATER ALARM FILTER LEVEL",
                        "TANK       PRODUCT LABEL         LEVEL"]
                for tank in tanks:
                    label = c.text("602", tank) or ""
                    level = c.setting("water_filter", tank, "LOW")
                    rows.append(f"{tank:5d}       {label:<22.22s}{level}")
                return self._frame(code, SEP.join(rows)), "water alarm filter"
            body = "".join(
                f"{t:02d}" + words.get(c.setting("water_filter", t, "LOW"), "1")
                for t in tanks)
            return self._frame(code, body), "water alarm filter"

        enabled = c.setting("fiscal_height", 0, "DISABLED") == "ENABLED"
        if tok == "55E":
            if code[0].isupper():
                return (self._frame(code, "FISCAL HEIGHT SECURITY: "
                                    + ("ENABLED" if enabled else "DISABLED")),
                        "fiscal height security")
            return self._frame(code, "1" if enabled else "0"), \
                "fiscal height security"
        # 132, the report: "FISCALLY SEALED", the flag, and the switch
        sealed = c.chart_secured()
        if code[0].isupper():
            # p.61 sets all three colons at column 30 and the values at
            # 32, which is what makes the block a column rather than three
            # sentences; the longest label, FISCAL HEIGHT SECURITY SWITCH,
            # is 29 characters and that is where the 30 comes from.
            rows = [f"{label:<30s}: {value}" for label, value in (
                ("FISCALLY SEALED", "YES" if sealed else "NO"),
                ("FISCAL HEIGHT SECURITY",
                 "ENABLED" if enabled else "DISABLED"),
                # The SWITCH is DIP SW2 position 4, not the flag again. The
                # two were the same value under two labels, so they could
                # never disagree -- and disagreeing is the whole point of
                # printing both. FIDELITY M13.
                ("FISCAL HEIGHT SECURITY SWITCH",
                 "ON" if c.fiscal_height_switch else "OFF"))]
            return self._frame(code, SEP.join(rows)), "fiscal height report"
        return (self._frame(code,
                            f"{int(sealed)}{int(enabled)}"
                            f"{int(c.fiscal_height_switch)}"),
                "fiscal height report")

    def _accu_record(self, tok, tanks):
        """B91/B93/B94 in computer format, as the manual packs them."""
        chart = self.c.accuchart
        out = ""
        for tank in tanks:
            on = chart.enabled(tank)
            entry = chart.state(tank) if on else None
            if tok == "B91":
                # "TT SS NN FFFFFFFF...": status, count, then six floats
                values = (entry.chart.values() if entry
                          else chart._user_profile(tank).values())
                out += (f"{tank:02d}{'01' if on else '00'}"
                        + packed.hexfloats(values))
            elif tok == "B93":
                # "TT SS MM UU AA NN FFFFFFFF": mode, user enable, alarm,
                # then duration, fitness and data quantity
                now = time.mktime(self.c.now())
                mode = "01" if entry and entry.mode == "MONITOR" else "00"
                user = "01" if entry and entry.user_status else "00"
                alarm = chart.alarm_state(tank)
                days = ((now - (entry.mode_since or now)) / 86400.0
                        if entry else 0.0)
                values = [days, entry.chart.fitness if entry else 0.0,
                          entry.data if entry else 0.0]
                out += (f"{tank:02d}{'01' if on else '00'}{mode}{user}{alarm}"
                        + packed.hexfloats(values))
            else:
                # "TT rr YYMMDDHHmm NN FFFFFFFF...", one block per record
                log = entry.history if entry else []
                out += f"{tank:02d}{len(log):02d}"
                for when, profile in log:
                    values = profile.values() + [profile.fitness]
                    out += (time.strftime("%y%m%d%H%M", time.localtime(when))
                            + packed.hexfloats(values))
        return out

    # ---- ISD and PMC setup, section 7.7.2 -----------------------------------
    def _isd_licensed(self, tok):
        """Whether this console has the key (or keys) the function wants.

        The manual states it per function and it is not one rule: "PMC feature
        required" on V40, "ISD feature required" on V4E, "ISD or PMC features
        required" on V47, "ISD and PMC features required" on V50.
        """
        spec = isd.SETUP.get(tok)
        if spec is None:
            return self.c.licensed("isd")
        needs = spec["needs"]
        if spec.get("any"):
            return any(self.c.licensed(k) for k in needs)
        return all(self.c.licensed(k) for k in needs)

    def _isd_value(self, tok):
        """What is stored for this function, or the manual's default."""
        spec = isd.SETUP[tok]
        held = self.c.values.get(f"S{tok}00")
        if held:
            return held
        kind, default = spec["kind"], spec["default"]
        if kind == "floats2":
            return packed.hexfloat(default[0]) + packed.hexfloat(default[1])
        if kind == "float":
            return packed.hexfloat(default)
        if kind == "int":
            return f"{default:0{spec['width']}d}"
        if kind == "clock":
            return f"{default[0]}{default[1]:0{spec['width']}d}"
        return default

    def _isd_store(self, tok, data):
        """Validate a Set against the manual's own range. None refuses it."""
        spec = isd.SETUP[tok]
        kind = spec["kind"]
        text = (data or "").strip()
        verify = spec.get("verify")
        if verify:
            # These carry their confirmation at the FRONT, where the rest of
            # the manual puts it at the back: "<SOH>SV4400149 -a.bcd -A.BCD"
            # against the 149 that trails a Set everywhere else. The shared
            # VERIFIED table strips a trailing one, so it cannot serve this.
            if not text.startswith(verify):
                return None
            text = text[len(verify):].strip()
        try:
            if kind in ("enum", "flag"):
                width = spec.get("width", 1)
                key = text[:width]
                return key if key in spec["table"] else None
            if kind == "pair":
                w = spec["width"]
                one, two = text[:w], text[w:w * 2]
                if one in spec["table"] and two in spec["table2"]:
                    return one + two
                return None
            if kind == "int":
                lo, hi = spec["range"]
                return (f"{int(text):0{spec['width']}d}"
                        if lo <= int(text) <= hi else None)
            if kind == "clock":
                # HHMM and then a count of minutes
                w = spec["width"]
                hh, mm, rest = int(text[0:2]), int(text[2:4]), int(text[4:4 + w])
                lo, hi = spec["range"]
                if not (0 <= hh <= 23 and 0 <= mm <= 59 and lo <= rest <= hi):
                    return None
                return f"{hh:02d}{mm:02d}{rest:0{w}d}"
            lo, hi = spec["range"]
            if kind == "float":
                v = self._isd_number(text)
                return packed.hexfloat(v) if lo <= v <= hi else None
            one = self._isd_number(text[:8] if len(text) >= 16 else
                                   text.split()[0])
            two = self._isd_number(text[8:16] if len(text) >= 16 else
                                   text.split()[1])
            # "low/off threshold < high/on threshold"
            if not (lo <= one <= hi and lo <= two <= hi and one < two):
                return None
            return packed.hexfloat(one) + packed.hexfloat(two)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _isd_number(text):
        """A value written either as a packed float or as plain decimal.

        The Set takes both: "Display: <SOH>SV4600xx.xx" against "Computer:
        <SOH>sV4600AAAAAAAA".
        """
        text = text.strip()
        if len(text) == 8 and all(c in "0123456789ABCDEFabcdef" for c in text):
            return packed.unhexfloat(text)
        return float(text)

    def _isd_words(self, tok):
        """The setting as the console prints it."""
        spec, held = isd.SETUP[tok], self._isd_value(tok)
        kind = spec["kind"]
        if kind in ("enum", "flag"):
            return spec["table"].get(held, held)
        if kind == "pair":
            w = spec["width"]
            return (f"{spec['table'].get(held[:w], held[:w])}"
                    f" / {spec['table2'].get(held[w:w * 2], held[w:w * 2])}")
        if kind == "int":
            return f"{int(held)} {spec.get('units', '')}".strip()
        if kind == "clock":
            w = spec["width"]
            return f"{held[0:2]}:{held[2:4]} + {int(held[4:4 + w])} MIN"
        units = spec.get("units", "")
        if kind == "float":
            return f"{packed.unhexfloat(held):.2f} {units}".strip()
        one, two = packed.unhexfloat(held[:8]), packed.unhexfloat(held[8:16])
        if spec.get("pair"):
            return spec["pair"].format(one, two)
        return f"{one:.3f} TO {two:.3f} {units}".strip()

    @staticmethod
    def _isd_map_line(row):
        """A V42 row spaced the way the manual prints one.

        The triples stay whole -- "020502", not "02 05 02" -- because that is
        a meter, its hose and the hose's label, and the example groups them.
        """
        out, at = [row[0:2], row[2:4]], 4
        for _ in range(isd.POSITIONS):
            out += [row[at:at + 2], row[at + 2:at + 4]]
            at += 4
            for _ in range(isd.TRIPLES):
                out.append(row[at:at + 6])
                at += 6
        return " ".join(out)

    @staticmethod
    def _isd_xx(value):
        """V48 and V4B write an unassigned field "xx" where V42 writes "UU"."""
        return "xx" if value in (isd.UNASSIGNED, "00") else value

    @staticmethod
    def _isd_date(stamp):
        """A stored YYMMDD as the service report prints it, MM/DD/YY."""
        if not stamp or len(stamp) < 6:
            return "--/--/--"
        return f"{stamp[2:4]}/{stamp[4:6]}/{stamp[0:2]}"

    @staticmethod
    def _isd_passed(status):
        """The Stage I and processor columns, which say "Pass" or nothing.

        The manual's example prints "Pass Pass" on the days it tested and
        leaves both blank on the days it did not -- it does not write the word
        UNKNOWN into a table column five characters wide.
        """
        return {isd.PASS: "Pass", isd.WARNING: "Warn",
                isd.FAILURE: "Fail"}.get(status, "")

    @staticmethod
    def _isd_cell(pair):
        """One measurement cell: the value, or what stands in for it.

        "-0.01=Blkd" on every value field, and a status of NO TEST prints the
        report's own N rather than a number nobody measured.
        """
        status, value = pair
        if status == isd.UNKNOWN:
            return "N"
        if abs(value - isd.BLOCKED) < 1e-9:
            return "Blkd"
        return f"{value:.1f}"

    @staticmethod
    def _control_state(line, table):
        """Which of 087/088's status codes this line is in right now.

        The line already knows its own state in words; this maps it back onto
        whichever of the two tables the code being answered uses.
        """
        said = line.status()
        for key, words in table.items():
            if words == said.upper():
                return key
        running = {"gross": "3.00", "periodic": "0.20", "annual": "0.10"}
        rate = running.get(line.rate_key or "")
        if rate:
            for key, words in table.items():
                if rate in words:
                    return key
        return "00"

    # 576013-635 Rev AA p.588's own columns, measured off the word boxes:
    # the two stamps at 0 and 11, then 7, 8, 7, 8, 5, 8, 5 and 8. The header
    # is the manual's, character for character -- this had one space where
    # the page has two and three where it has one.
    C09_HEAD = ("STRT TIME  END TIME   STRT HT END HT STRT VL END_VL SALES"
                "  DELIV OFFSET   VAR")

    def _recon_history(self, dev, data):
        """C09, which is the odd one: keyed by TANK and not by product.

        A history is a table and this drew one row of it, the current
        day, twice over: `STRT HT` and `END HT` were both `stick_height`,
        the reading NOW, so the two columns could never differ on a report
        whose whole subject is a day's movement. p.588's sample chains --
        the second row's start height and start volume are the first row's
        end height and end volume -- and it replays exactly against this
        console's own chart: 4700 gallons in a 10,000 gallon 96 inch tank
        is 45.737 inches, 5000 is 48.000 and 4986.1 is 47.895, which are
        the sample's three heights to the digit. See FIDELITY G12.

        The blank after the title is p.588's leading: its lines are 8.6
        points apart and the gap in front of the tank line is 17.3.
        """
        tanks = ([int(dev)] if dev not in ("00", "") and dev.isdigit()
                 and int(dev) else sorted(self.c.tank_level))
        ticketed = (data or "").strip().startswith("1")
        out = ["INDIVIDUAL BASIC RECONCILIATION HISTORY DIAGNOSTIC", ""]
        for tank in self.c.bir.report_tanks(tanks):
            out += self.c.bir.tank_lines(tank)
            out.append(self.C09_HEAD)
            rows = self.c.bir.daily_history(tank)
            if not rows:
                out.append("  NO DATA AVAILABLE")
                continue
            for row in rows:
                deliv = row["ticketed"] if ticketed else row["deliveries"]
                out.append(
                    "%s %s" % (time.strftime("%y%m%d%H%M",
                                             time.localtime(row["opened"])),
                               time.strftime("%y%m%d%H%M",
                                             time.localtime(row["closed"])))
                    + "%7.3f%8.3f" % (
                        self.c.stick_height(tank, row["opening"]),
                        self.c.stick_height(tank, row["physical"]))
                    + "%7.1f%8.1f" % (row["opening"], row["physical"])
                    + "%5.1f%8.1f" % (row["sales"], deliv)
                    + "%5.1f%8.1f" % (row["adjust"], row["variance"]))
        return chr(10).join(out)

    def _recon_history_body(self, dev):
        """C09's computer format, which is not the shape the other C-codes
        share: "TT - Tank Number, rr - Number of records to follow, ...
        NN - Number of eight character Data Fields to follow", and three
        stamps per record rather than two -- the requested start, the actual
        start and the end. It went through `_recon_body` and sent one
        record, two stamps and the wrong floats."""
        tanks = ([int(dev)] if dev not in ("00", "") and dev.isdigit()
                 and int(dev) else sorted(self.c.tank_level))
        body = ""
        for tank in self.c.bir.report_tanks(tanks):
            rows = self.c.bir.daily_history(tank)
            body += "%02d%02X" % (tank, len(rows))
            for row in rows:
                values = self.c.bir.history_figures(tank, row)
                body += (time.strftime("%y%m%d%H%M",
                                       time.localtime(row.get("requested",
                                                              row["opened"])))
                         + time.strftime("%y%m%d%H%M",
                                         time.localtime(row["opened"]))
                         + time.strftime("%y%m%d%H%M",
                                         time.localtime(row["closed"]))
                         + "%02X" % len(values)
                         + packed.hexfloats(values))
        return body

    def _recon_body(self, tok, spec, tanks, previous):
        """The computer format: product, its tanks, the period, then floats."""
        bir = self.c.bir
        body = ""
        for tank in tanks:
            product = (self.c.text("603", tank) or "0")[:2].strip() or "0"
            rows = (bir.period_days(tank, previous) if spec["multi"]
                    else [bir.row(tank, spec["kind"], previous)])
            got = [r for r in rows if r]
            if not got:
                continue
            body += "%02d01%02d" % (int(product) if product.isdigit() else 0,
                                    tank)
            if spec["multi"]:
                body += "%02X" % len(got)
            for row in got:
                body += (time.strftime("%y%m%d%H%M",
                                       time.localtime(row["opened"]))
                         + time.strftime("%y%m%d%H%M",
                                         time.localtime(row["closed"])))
                if spec["shape"] == "analysis":
                    # "bit encoded long integer with tank 1=lsb", twice
                    body += "%08X%08X" % (0, 0)
                    values = bir.analysis_figures(row)
                elif spec["shape"] == "book":
                    values = bir.book_figures(row)
                else:
                    values = bir.figures(row)
                body += packed.hexfloats(values)
        return body

    @staticmethod
    def _maintenance_words(entry):
        """119's data field. Moved to `alarmreports.maintenance_words`, so
        that the white key's printed Maintenance Report reads its records
        the same way this does rather than growing a second copy."""
        return alarmreports.maintenance_words(entry)

    def _hrm_hours(self, tank):
        """A61 and A63's hourly reconciliation records.

        HRM is the hourly half of reconciliation: what the tank held at the
        end of each hour against what the meters sold in it, and the variance
        between them. The console already keeps the daily row those hours add
        up to, so the hours are that row spread back over its own span.
        """
        row = self.c.bir.row(tank, "daily")
        if not row:
            return []
        out = []
        now = time.mktime(self.c.now())
        volume = row["physical"]
        for back in range(8):
            at = now - back * 3600.0
            sold = row["sales"] / 8.0 if row["sales"] else 0.0
            out.append({
                "stamp": time.strftime("%y%m%d%H%M", time.localtime(at)),
                "temp": self.c.product_temperature(tank),
                "volume": volume + sold * back,
                "sales": sold,
                "flag": "00",
                "variance": readings.fixed(-0.2, 0.2, "hrm", tank, back)})
        return out

    def _hrm_days(self, tank):
        """A62's daily aggregate: how many hours went in, and their spread."""
        hours = self._hrm_hours(tank)
        if not hours:
            return []
        variances = [h["variance"] for h in hours]
        return [{"stamp": hours[0]["stamp"], "records": len(hours),
                 "min": min(variances), "max": max(variances),
                 "ave": sum(variances) / len(variances), "status": "01"}]

    def csld_monthly_rows(self, tanks, previous=False):
        """A56's display body: a month of CSLD state changes per tank.

        The panel's CUR and PRV CSLD MONTHLY screens print this too, so it
        is one report whichever end of the console asks. FIDELITY D23.
        """
        rows = ["CSLD MONTHLY REPORT",
                "PREVIOUS MONTH" if previous else "CURRENT MONTH",
                "0.2 GAL/HR TEST"]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            rows.append(f"T {tank}:{label}")
            rows.append("PROBE SERIAL NUM " + self.c.probe_serial(tank))
            for at, state in self._csld_states(tank, previous):
                rows.append(f"{clock_words(at):22s}"
                            + hrmreports.CSLD_STATE[state])
        return rows

    def _csld_states(self, tank, previous=False):
        """A56's month of CSLD state changes, newest first.

        This read `probe_leak_buffer(tank, "periodic")` -- the SCHEDULED
        leak-test buffer -- and a CSLD tank runs no scheduled tests, so the
        list was always empty and the fallback fired. No PASS or FAIL
        could ever appear in the monthly report, against the manual's own
        sample showing a month of RESULT: PASS, RESULT: FAIL, RESULT: INCR
        and STATUS: changes. `csld.history` is the database it wanted. See
        FIDELITY K3.
        """
        now = self.c.now()
        month = (now.tm_year, now.tm_mon)
        if previous:
            month = ((month[0] - 1, 12) if month[1] == 1
                     else (month[0], month[1] - 1))
        out = []
        for when, code in reversed(self.c.csld.history.get(tank) or []):
            stamp = time.localtime(when)
            if (stamp.tm_year, stamp.tm_mon) == month:
                out.append((when, code))
        if out:
            return out[:31]
        if previous:
            return []
        # Nothing decided this month. A tank CSLD is still gathering on is
        # active; one that cannot find an idle hour says so, which is the
        # other status word the report has.
        idle = self.c.csld.idle_from.get(tank)
        return [(time.mktime(now), "99" if idle is not None else "98")]

    def _outages(self, tank):
        """A91: what the tank held either side of a power cut."""
        if self.c.power_off is None:
            return []
        level = self.c.tank_level.get(tank, {})
        volume = level.get("volume", 0.0)
        water = level.get("water", 0.0)
        temp = self.c.product_temperature(tank)
        back = self.c.power_off
        return [{"off": back, "on": back + 12 * 60.0,
                 "off_volume": volume, "off_water": water, "off_temp": temp,
                 "on_volume": volume, "on_water": water, "on_temp": temp,
                 "change": 0.0}]

    def _probe_head(self, tank, tail="", word=None):
        """`TANK  1  REGULAR UNLEADED      MAG`, and whatever follows it.

        Section 7.4.2's reports all open each tank's block with this line and
        they all set it the same way, measured off pp.489, 490 and 495: the
        word TANK at column 0, the tank number right against 7, the product
        label at 9, the probe's type at 31 and anything after it at 38.

        Four reports built it with single spaces between the four pieces --
        `TANK 1 REGULAR UNLEADED MAG` -- so none of the columns landed and
        the line moved whenever a product label changed width.
        """
        label = self.c.text("602", tank) or f"TANK {tank}"
        word = word or self.c.probe_type_word(tank)
        line = f"TANK {tank:2d}  {label:<22.22s}"
        line += f"{word:<7s}" if tail else word
        return (line + tail).rstrip()

    def _valve(self, number):
        """B61's live state for one vapour valve.

        The battery is `0`, Unknown, because nothing here makes a valve a
        WIRELESS one and the manual's own annotation on that line is "(only
        if wireless)". 577013-937 Rev J Figure 42 is a real console's IB6100
        for a wired valve and prints no BATTERY line; the packed form still
        carries the byte, because B61's computer format always has it.
        """
        want = self.c.values.get("SVC800") or "0"
        return {
            "serial": f"{readings.integer(10000000, 99999999, 'valve', number)}",
            "position": want,
            "battery": "0", "open_cap": "1", "close_cap": "1",
            "ambient": readings.wander(self.c, 65.0, 78.0, "amb", number),
            "outlet": readings.wander(self.c, 68.0, 82.0, "outlet", number),
            "faults": []}

    def _valve_history(self):
        """B62's sub alarm log. Nothing here faults a valve, so it is empty --
        the same answer A20 to A22 give and for the same reason."""
        return []

    def _mag_devices(self, dev):
        """One sensor, or every sensor programmed as a Mag sensor.

        `00` is all of them, and all a Mag sump test can run on is the smart
        sensors set to category 03 -- not every position the card has, which
        is what `_devices_of` answers and which put a vacuum sensor's
        position into the sump reports. See FIDELITY U1b.
        """
        if dev != "00" and dev.isdigit() and int(dev):
            return [int(dev)]
        return self.c.mag_sensors()

    def _loads_of(self, tank):
        """[(sequence, load)] for the tanker load reports, newest first."""
        out = []
        records = getattr(self.c.loads, "records", {}).get(tank) or []
        for n, record in enumerate(records[:40], 1):
            start = getattr(record, "start", {}) or {}
            end = getattr(record, "end", {}) or {}
            out.append((getattr(record, "number", n), {
                "start": start.get("at", 0.0), "end": end.get("at", 0.0),
                "start_vol": start.get("volume", 0.0),
                "end_vol": end.get("volume", 0.0),
                "start_temp": start.get("temp", 0.0),
                "end_temp": end.get("temp", 0.0),
                "start_tc": start.get("tc", 0.0),
                "end_tc": end.get("tc", 0.0),
                # The manual defines the total as "start volume - end volume"
                # on both 391 and 392, which for a load OUT of a tank is the
                # right way round: a tanker load takes fuel away.
                "total": start.get("volume", 0.0) - end.get("volume", 0.0),
                "total_tc": start.get("tc", 0.0) - end.get("tc", 0.0)}))
        return out

    def _tank_status_bits(self, tank):
        """"ssss - Tank Status Bits": delivery, leak test, invalid height."""
        bits = 0
        if self.c.deliveries.in_progress(tank):
            bits |= 1
        if self.c.leaks.active("tank", tank):
            bits |= 2
        return bits

    def _mass_floats(self, tank):
        """214's six: volume, MASS, DENSITY, height, water, temperature."""
        level = self.c.tank_level.get(tank, {})
        return [level.get("volume", 0.0), self.c.product_mass(tank),
                self.c.product_density(tank), self.c.stick_height(tank),
                self.c.water_height(tank), self.c.product_temperature(tank)]

    def _deliveries_of(self, tank, most):
        """The delivery records these reports walk, newest first."""
        out = []
        for record in (self.c.deliveries.records.get(tank) or [])[:most]:
            if not record.end:
                continue
            out.append({
                "start": record.start.get("at", 0.0),
                "end": record.end.get("at", 0.0),
                "amount": record.amount, "tc": record.tc_amount,
                "ticketed": getattr(record, "ticket", None) or record.amount,
                "bol": getattr(record, "bol", "") or "",
                "record": record})
        return out

    @staticmethod
    def _delivery_head(tok):
        # 213 is 202's report with a count on the front, so it prints 202's
        # columns: `Deliveries.DELIVERY_HEAD` is p.63's header counted off
        # the page, and both reports use the same row builder. 215 keeps its
        # own three columns, which S12 sourced from a different page.
        if tok == "215":
            return delivery.Deliveries.MASS_HEAD
        if tok == "21B":
            return ("DELIVERY START DATE   DELIVERY END DATE  VOLUME"
                    " VOLUME DELIV  DELIV")
        return delivery.Deliveries.DELIVERY_HEAD

    def _delivery_rows(self, tok, tank, rec):
        """One delivery, printed the way this particular report prints one."""
        r = rec["record"]
        if tok == "21B":
            return [f"{clock_words(rec['start']):22s}"
                    f"{clock_words(rec['end']):19s}"
                    f"{r.start.get('volume', 0.0):6.0f}"
                    f"{r.end.get('volume', 0.0):7.0f}"
                    f"{rec['amount']:6.0f}{rec['tc']:7.0f}"]
        out = []
        line = delivery.Deliveries.delivery_line
        for name, side in (("END", r.end), ("START", r.start)):
            stamp = clock_words(side.get("at", 0.0))
            if tok == "215":
                out.append(line(name, stamp, (
                    f"{side.get('volume', 0.0):.0f}",
                    f"{side.get('volume', 0.0) * self.c.product_density(tank):.0f}",
                    f"{self.c.product_density(tank):.4f}",
                    f"{side.get('water', 0.0):.2f}",
                    f"{side.get('temp', 0.0):.2f}",
                    f"{side.get('height', 0.0):.2f}"),
                    delivery.Deliveries.MASS_WIDTHS))
            else:
                out.append(line(name, stamp, (
                    f"{side.get('volume', 0.0):.0f}",
                    f"{side.get('tc', 0.0):.0f}",
                    f"{side.get('water', 0.0):.2f}",
                    f"{side.get('temp', 0.0):.2f}",
                    f"{side.get('height', 0.0):.2f}")))
        if tok == "215":
            # 576013-635 Rev AA p.79: the AMOUNT row's second column is the
            # MASS delta, under the MASS heading, not the TC volume the 213
            # row carries -- 3303 gallons is 19,814 pounds. The trailing star
            # is the density-defaulted flag the computer format already emits
            # (X3).
            mass = rec["amount"] * self.c.product_density(tank)
            star = "*" if self.c.density_defaulted(tank) else ""
            out.append(line("AMOUNT", "", (f"{rec['amount']:.0f}",
                                           f"{mass:.0f}{star}"),
                            delivery.Deliveries.MASS_WIDTHS))
        else:
            out.append(line("AMOUNT", "", (f"{rec['amount']:.0f}",
                                           f"{rec['tc']:.0f}")))
        return out

    def _delivery_floats(self, tok, tank, rec):
        """And the floats, which are three different lists.

        213 is all-Starting then all-Ending then the two heights; 215 swaps
        TC volume for mass and density; 21B is a different shape again --
        start volume, END volume, the two adjusted volumes, then a height and
        six temperatures for each end. Do not reuse one decoder for another.
        """
        r = rec["record"]
        start, end = r.start, r.end
        if tok == "213":
            return [start.get("volume", 0.0), start.get("tc", 0.0),
                    start.get("water", 0.0), start.get("temp", 0.0),
                    end.get("volume", 0.0), end.get("tc", 0.0),
                    end.get("water", 0.0), end.get("temp", 0.0),
                    start.get("height", 0.0), end.get("height", 0.0)]
        density = self.c.product_density(tank)
        if tok == "215":
            return [start.get("volume", 0.0),
                    start.get("volume", 0.0) * density, density,
                    start.get("water", 0.0), start.get("temp", 0.0),
                    end.get("volume", 0.0),
                    end.get("volume", 0.0) * density, density,
                    end.get("water", 0.0), end.get("temp", 0.0),
                    start.get("height", 0.0), end.get("height", 0.0)]
        temps = [self.c.probe_temperatures(tank)[n] for n in range(6)]
        return ([start.get("volume", 0.0), end.get("volume", 0.0),
                 rec["amount"], rec["tc"], start.get("height", 0.0)]
                + temps + [end.get("height", 0.0)] + temps
                + [getattr(r, "sold", 0.0) or 0.0,
                   start.get("temp", 0.0), end.get("temp", 0.0)])

    def _isd_hoses(self):
        """[(fuel position, hose)] the V42 map knows about, in order.

        The detail report has a column per hose, so what it has columns for is
        whatever the site has been programmed with -- and if nothing has, it
        has none, which is the honest table for an unprogrammed console.
        """
        out = []
        for row in isd.hose_view(self._isd_rows()):
            out.append((row[2:4], row[0:2]))
        return out

    def _isd_day_record(self, day):
        """One day of the detail report.

        What can be said honestly about a day: whether ISD was up, how many
        Stage I transfers there were -- which is the deliveries -- and whether
        the processor ran. What CANNOT be is any of the containment or
        collection measurements, because nothing here measures a vapour, so
        those read NO TEST, which is a status the report has a code for.
        """
        passing, total = self._isd_stage1(day, day + 86400)
        ran = any(day <= cy["at"] < day + 86400 for cy in self.c.vp_cycles)
        fitted = (self.c.values.get("SV4000") or "00") != "00"
        overall = self._isd_status()[0]
        return {
            "at": day,
            "evr": overall,
            "up": 100 if self._isd_setup_ok() else 0,
            # containment: (status, value) for gross, degradation, leak, and
            # the bare min/max
            "gross": (isd.UNKNOWN, 0.0), "degrade": (isd.UNKNOWN, 0.0),
            "min": 0.0, "max": 0.0, "leak": (isd.UNKNOWN, 0.0),
            "stage1": isd.PASS if total else isd.UNKNOWN,
            "processor": (isd.PASS if ran else isd.UNKNOWN) if fitted
            else isd.UNKNOWN,
            "hoses": [(fp, hose, isd.UNKNOWN, 0.0)
                      for fp, hose in self._isd_hoses()],
        }

    def _isd_days(self, tok, asked):
        """Which days this variant is being asked for."""
        period, _width = isd.DETAIL[tok]
        now = self.c.now()
        if period == "days":
            count = int(asked[0:3]) if len(asked) >= 3 else 10
            count = max(1, min(count, 366))
            today = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                 0, 0, 0, 0, 1, -1))
            return [today - n * 86400 for n in range(count - 1, -1, -1)]
        year = int(asked[0:4]) if len(asked) >= 6 else now.tm_year
        month = int(asked[4:6]) if len(asked) >= 6 else now.tm_mon
        out, day = [], 1
        while day <= 31:
            try:
                at = time.mktime((year, month, day, 0, 0, 0, 0, 1, -1))
            except (ValueError, OverflowError):
                break
            if time.localtime(at).tm_mon != month:
                break
            out.append(at)
            day += 1
        return out

    def _isd_columns(self, tok, asked):
        """How wide the printed table may be."""
        _period, width = isd.DETAIL[tok]
        if width is None:
            return isd.DETAIL_DEFAULT_COLUMNS
        if width != "ccc":
            return width
        tail = asked[6:9] if isd.DETAIL[tok][0] == "month" else asked[3:6]
        try:
            want = int(tail) if tail else isd.DETAIL_CCC_DEFAULT
        except ValueError:
            return None
        lo, hi = isd.DETAIL_CCC_RANGE
        return want if lo <= want <= hi else None

    def _carb_requirements(self, site):
        """[(label, min, max)]: the CARB operating requirement rows.

        Each row reports the setting it names, so the A/L RANGE line is the
        site's own V4F -- which is what 577013-800 Rev P p.48 prints. It was
        a constant that no programming reached. See FIDELITY I5.
        """
        out = []
        for label, tok, only in isd.CARB_REQUIREMENTS:
            if only == site:
                held = self._isd_value(tok)
                out.append((label, packed.unhexfloat(held[:8]),
                            packed.unhexfloat(held[8:16])))
        return out

    def _carb_thresholds(self, site):
        """The CARB threshold rows, with the leak detection figure resolved.

        That one is the site's own, off its hose count and CP-201's bands --
        see `isd.leak_detection_cfh`. Every other row is a constant.
        """
        return isd.carb_thresholds(site, len(isd.hose_view(self._isd_rows())))

    def _isd_carb_lines(self):
        """The CARB block V00 prints and V02 and V03 reprint inside theirs."""
        site = "assist" if self._isd_evr() == "02" else "balance"
        rows = ["CARB EVR CERTIFIED OPERATING REQUIREMENTS",
                f"{'':47s}Min  Max"]
        for label, lo, hi in self._carb_requirements(site):
            rows.append(f"{label:47s}{lo:.2f} {hi:.2f}")
        rows.append("ISD MONITORING TEST PASS/FAIL THRESHOLDS")
        rows.append(f"{'':47s}Period Below Above")
        for label, per, lo, hi, unit, _only in self._carb_thresholds(site):
            # 47 is the column the manual puts the period in, and one
            # label is longer than that: VAPOR COLLECTION ASSIST SYSTEM
            # A/L DEGRADATION FAIL is 51 characters, so a fixed field ran
            # the two together as `...DEGRADATION FAIL7dys`. The page
            # leaves two spaces, so the long row pushes its period right
            # rather than losing the gap.
            wide = max(47, len(label) + 2)
            rows.append(f"{label:{wide}s}{per:6s} {lo:5s} {hi}{unit}")
        return rows

    def _isd_status_lines(self, since, monthly, heading):
        """The status block V0A, V0B, V01, V02 and V03 all open with."""
        if since is None:
            # V01 asks for no period: it is a report on the console as it
            # stands, so the counts are everything it has seen.
            first = self.c._commissioned or 0.0
            passing, total = self._isd_stage1(first,
                                              time.mktime(self.c.now()) + 1)
        else:
            passing, total = self._isd_stage1(
                since, since + (31 * 86400 if monthly else 86400))
        overall, collect, contain, processor = self._isd_status()
        evr = isd.EVR_REPORTED.get(self._isd_evr(), "1")
        kind = (self.c.values.get("SV4000") or "00")
        up = 100 if self._isd_setup_ok() else 0
        rows = [heading]
        if since is not None:
            when = (time.strftime("%b %Y", time.localtime(since)).upper()
                    if monthly else clock_words(since)[:12])
            rows.append(f"REPORT DATE: {when}")
        rows += ["EVR TYPE: " + ("VACUUM ASSIST" if evr == "0" else "BALANCE"),
                 f"ISD TYPE: {isd.ISD_VERSION}",
                 "VAPOR PROCESSOR TYPE: "
                 + isd.VAPOR_PROCESSOR.get(kind, "NONE"),
                 f"OVERALL STATUS :{isd.STATUS[overall]}"
                 f" EVR VAPOR COLLECTION :{isd.STATUS[collect]}",
                 f"EVR VAPOR CONTAINMENT :{isd.STATUS[contain]}",
                 f"ISD MONITOR UP-TIME :{up}%"
                 f" STAGE I TRANSFERS: {passing} of {total} PASS",
                 f"EVR/ISD PASS TIME :{up}%"
                 f" VAPOR PROCESSOR : {isd.STATUS[processor]}"]
        return rows

    def _isd_alarm_groups(self):
        """(warnings, failures, events) for V01, V02 and V03.

        Nothing here measures a vapour, so nothing here raises a vapour alarm:
        the warning and failure groups are empty on this console for the same
        reason A20 to A22 are, and the manual's own examples of those are
        empty too. The event log is NOT empty, because its entries are things
        the console genuinely knows -- when ISD started, and what the
        readiness check says, which is V51's question already answered.
        """
        started = self.c._commissioned or time.mktime(self.c.now())
        ready = self._isd_setup_ok()
        events = [(started, "ISD STARTUP", "")]
        if ready:
            events.insert(0, (started, "READINESS ISD:PP EVR:PPPP",
                              "EVR/ISD SYSTEM READY"))
        else:
            events.insert(0, (started, "READINESS ISD:FN EVR:NNN",
                              "CHECK SETUP CONFIGURATION"))
        # what the bench has forced IS what the console measured, so the
        # warning and failure groups carry it, under the monthly report's
        # own long descriptions (577013-800 pp.48-49)
        LONG = {"leakage": "VAPOR CONTAINMENT LEAKAGE",
                "gross": "A/L RATIO GROSS BLOCKAGE",
                "degrade": "A/L RATIO DEGRADATION",
                "collect_gross": "A/L RATIO GROSS BLOCKAGE",
                "collect_degrade": "A/L RATIO DEGRADATION",
                # 577013-937 Rev J p.12-32's own monthly sample, which is a
                # BALANCE site's: `FLOW PERFORMANCE HOSE BLOCKAGE` in both
                # the warning and the failure group
                "collect_flow": "FLOW PERFORMANCE HOSE BLOCKAGE",
                "sensor": "CHECK ISD SENSORS",
                "setup": "CHECK SETUP CONFIGURATION"}
        warnings, failures = [], []
        # The ASSESSED state, not the bench's: a test that has been failing
        # since Tuesday is a warning, and the same test on its eighth
        # consecutive day is a failure alarm. See FIDELITY I3.
        for test, state in sorted(self.c.isd_states().items()):
            at = self.c.isd_forced_at.get(test, started)
            row = (at, LONG.get(test, test.upper()), "")
            (warnings if state == "warn" else failures).append(row)
        events = list(self.c.isd_events) + events
        return warnings, failures, events

    def _isd_alarm_lines(self):
        """Those three groups as the reports print them."""
        warnings, failures, events = self._isd_alarm_groups()
        rows = ["ISD WARNING ALARMS",
                "DATE TIME           DESCRIPTION                READING VALUE"]
        for at, what, value in warnings:
            rows.append(f"{self._isd_stamp(at):18s}{what:27s}{value}")
        rows += ["FAILURE ALARMS",
                 "DATE TIME           DESCRIPTION                READING VALUE"]
        for at, what, value in failures:
            rows.append(f"{self._isd_stamp(at):18s}{what:27s}{value}")
        rows += ["SHUTDOWN & MISC. EVENT LOG",
                 "DATE TIME           DESCRIPTION                ACTION OR NAME"]
        for at, what, value in events:
            rows.append(f"{self._isd_stamp(at):18s}{what:27s}{value}")
        return rows

    def _isd_alarm_body(self):
        """And as the computer format packs them: a count then the records."""
        body = ""
        warnings, failures, events = self._isd_alarm_groups()
        for group in (warnings, failures):
            body += f"{len(group):03d}"
            # p.604-605 name a warning's and a failure's category and type
            # and enumerate neither, so these keep the code they had.
            # UNKNOWNS A62.
            for at, _what, _value in group:
                body += f"{int(at):08X}" + "01" + "01" + "00" + "00" + "00" + "00"
        body += f"{len(events):03d}"
        for at, what, value in events:
            # p.606 does enumerate the Shutdown & Misc. events, and every
            # one of them was packed as 01 01, ISD Startup. FIDELITY I11.
            aa, bb = isd.misc_event_code(what, value)
            body += f"{int(at):08X}" + aa + bb + "00" + "00" + "00" + "00"
        return body

    def _isd_evr(self):
        """V4E's EVR type: "01" balance, "02" vacuum assist."""
        return (self.c.values.get("SV4E00") or "0101")[:2]

    def _isd_status(self):
        """(overall, collection, containment, processor) as V0A reports them.

        Nothing here measures a vapour, so nothing here invents a failure.
        What the console CAN say honestly is whether its ISD is set up and
        whether anything has alarmed: a site whose setup does not verify has
        not tested anything and reads UNKNOWN, and one that verifies with
        nothing wrong reads PASS. That is what the manual's own examples show
        a healthy site reading.
        """
        if not self._isd_setup_ok():
            return (isd.UNKNOWN,) * 4
        processor = (self.c.values.get("SV4000") or "00") != "00"
        return (isd.PASS, isd.PASS, isd.PASS,
                isd.PASS if processor else isd.UNKNOWN)

    def _isd_stage1(self, since, until):
        """"STAGE I TRANSFERS: 12 of 12 PASS".

        A Stage I vapour transfer is what happens while a tanker is unloading
        into a tank, so the count is the deliveries the console recorded in
        the period. It has no failure model, so all of them passed.
        """
        total = 0
        for records in self.c.deliveries.records.values():
            for record in records:
                at = (record.end or {}).get("at") if record.end else None
                if at is not None and since <= at < until:
                    total += 1
        return total, total

    @staticmethod
    def _isd_stamp(at, seconds=False):
        """"12-26-01 10:51 AM", which is how section 7.7's reports date a row.

        Not clock_words: these are table rows with a column to fit in, and the
        manual writes them MM-DD-YY rather than the long form the status line
        uses.
        """
        shape = "%m-%d-%y %I:%M:%S %p" if seconds else "%m-%d-%y %I:%M %p"
        return time.strftime(shape, time.localtime(at))

    def _vp_full_control(self):
        """"PMC Feature and Full Vapor Processor Control required".

        V41's level: "00=Full Control", which is the only one these two
        reports are offered on.
        """
        return (self.c.licensed("pmc")
                and (self.c.values.get("SV4100") or "00") == "00")

    def _vp_control(self):
        """VC0: whether the vapour processor is on automatic or manual."""
        return self.c.vp_control()

    def _vp_running(self):
        return self.c.vp_running()

    def _isd_setup_ok(self):
        """V51: "Status of ISD/PMC Setup Test", 0=Pass.

        A verification test that always passed would be worth nothing, so it
        checks the two things the setup cannot work without: ISD wants at
        least one airflow meter map, because that is what every collection
        test is measured through, and PMC wants a vapour processor type,
        because there is nothing to control without one.
        """
        if self.c.licensed("isd") and not self._isd_rows():
            return False
        if self.c.licensed("pmc"):
            kind = (self.c.values.get("SV4000") or "00")
            if kind == "00":
                return False
        return True

    def _isd_rows(self):
        """Every V42 map row the console holds, by sensor index."""
        return [self.c.values[k] for k in sorted(self.c.values)
                if k.startswith("SV42") and self.c.values[k]]

    def _isd_labels(self):
        """V49's label table: ten IDs, 01 unassigned unless somebody says so."""
        out = []
        for ident in isd.LABEL_IDS:
            held = self.c.values.get(f"SV49{ident}")
            out.append((ident, held or isd.LABEL_DEFAULT.get(ident, "")))
        return out

    def _isd_sensors(self):
        """The smart sensors, which is what V43's index table walks.

        ISD reads airflow meters and vapour pressure sensors, and both are
        smart sensors this console already models -- SMART_TYPE's 01 and 02
        are AIR FLOW METER and VAPOR PRESSURE by name.
        """
        from . import wiresensors
        from .wiresensors import SMART_TYPE, SMART_UNKNOWN
        out = []
        for number in range(1, max(self.c.capacity("smart"), 0) + 1):
            kind = self.c.sensor_type("smart", number)
            if not kind:
                continue
            _code, name = SMART_TYPE.get(kind, SMART_UNKNOWN)
            out.append((f"{number:02d}", name,
                        wiresensors.isd_serial(self.c, number),
                        wiresensors.isd_in_use(self.c, number)))
        return out

    @staticmethod
    def _draws_station(tok):
        """Whether this reply carries the four station header lines.

        The manual's own sample for the code, and a fallback for the
        codes it has no sample for. This used to be "is it in `REPORTS`",
        a hand-kept set -- and a hand-kept set is exactly what the samples
        are there to replace. 67 codes in it draw no header on the page and
        got one anyway, most of the sensor diagnostics along with `217`
        TANK PROFILE, `218` TANK CHART AUDIT TRAIL, `219` TANK CHART
        SECURITY and `888`; and 50 codes outside it draw one on the page and
        got none, the whole ISD `V` family included. Both directions are one
        defect: the header was decided by the wrong thing.

        The obvious rule is wrong too. A section 7.4 DIAGNOSTIC mostly draws
        no header and a 7.2 or 7.3 report mostly draws one, but that misses
        twenty-seven codes: A15, A81, A91, B62 and BB1 are diagnostics with
        a header, and the twenty-two above are reports without.

        Read by `tools/build_wire_titles.py` off the word boxes, and checked
        against a plain-text scan of both manual revisions: 367 of the 371
        codes both readings could see agree, and all four disagreements are
        the text scan running past a page boundary -- `208`, `219`, `503`
        and `7B4` plainly draw no header on the page.

        See FIDELITY L5.
        """
        if tok in MEASURED_FRAME:
            return MEASURED_FRAME[tok]["station"]
        drawn = wiretables.station_header(tok)
        if drawn is None and tok in NO_PAGE_NO_HEADER:
            return False
        if tok in ("375", "385"):
            # no page, and the bench draws the header as it does on 373
            # (`cap_site`, 2026-09-19)
            return True
        return tok in REPORTS if drawn is None else drawn

    def _nine(self, _code=None):
        """What the console says when it has not understood."""
        return NOT_UNDERSTOOD

    # Reports about DEVICES, which a console with none of that device
    # configured answers with the frame, the code, the stamp and nothing
    # else -- not a title, not a heading, and not a device 1 it has not got.
    # Every one of these was read on the bench TLS-350 on 2026-09-18 straight
    # after a cold start, with the probe and PLLD cards fitted and nothing
    # switched on. I20100 had been known to do this since the 2006 capture;
    # these are the rest of the family.
    TANK_REPORTS = {"202", "207", "208", "20A", "20B", "20C", "20D", "212",
                    "214", "215", "216", "217", "218", "219", "21B", "282",
                    "A01", "A14", "A15", "A55", "B91", "B93", "B94",
                    # bare at 00 and at 01 with no tank reporting (the bench
                    # TLS-350, 2026-09-22), where tank 1 drew its block
                    "203", "251", "683",
                    # and, CR-terminated (`WAITING_INQUIRIES`), the CSLD
                    # monthly report and 2E2's packed form, which drew four
                    # tanks' rows and a tank-1 record
                    "A56", "2E2"}
    LINE_REPORTS = {"373", "374", "375", "381", "382", "383", "384", "385",
                    "B7B", "B7C",
                    "B7E", "B81", "B87", "B88", "B89", "B8A"}

    def _answered(self, tok, dev, code):
        """An accepted Set's reply: what the Inquire of that device answers.

        The bench TLS-350 (2026-09-18) answers `S78901200` with the I78901
        report under an `S78901` echo -- the code and device, never the data
        -- and `s7890143480000` with i78901's own packed body under `s78901`.
        Every family tried did the same: 602, 503, 560, 61E, 52B, 781, 782,
        785, 788. A code with no report to give answers the bare echo.
        """
        # Asked as the Inquire it is -- the list families route an S code
        # back to their Set path -- and then echoed as the Set it was.
        asked = ("I" if code[:1].isupper() else "i") + code[1:]
        try:
            out, _note = self.inquire(tok, dev, asked)
        except Exception:
            out = None
        if not out or out.startswith(NOT_UNDERSTOOD[:5]):
            return self._frame(code)
        out = out.replace(asked.encode("latin-1"), code.encode("latin-1"), 1)
        if code[:1].islower() and b"&&" in out:
            body = out[:out.rindex(b"&&") + 2]
            out = body + checksum(body).encode("ascii") + ETX
        return out

    def _absent(self, code):
        """A code the console knows, for a card, key or feature it has not
        got: a bare frame, not 9999FF.

        The bench TLS-350 (326.01, 2026-09-18) answers every one of them so,
        in both formats -- `I70100` and `i70100` with no liquid sensor card,
        `IV0000` with no ISD, `I68000`'s neighbours with no Fuel Manager --
        and keeps 9999FF for a code it does not know at all. 485 codes
        captured in both formats, `cap_swept`; FIDELITY S33.
        """
        return self._frame(code)

    def _refused(self, code, data, tok=None, dev=None):
        """A refused Set's reply, which is not 9999FF.

        Display: the echo, the stamp, and one `?` for every character of
        data that was SENT -- `S789015000` answers `????`, `S78901ABC`
        answers `???`, a one-character flag answers `?`. Computer: the echo,
        the stamp, one `?` for every character of the field's own width --
        eight for a float -- then `&&` and the checksum. Read off the bench
        TLS-350 on 2026-09-18.
        """
        if code[:1].islower():
            width = self._field_width(tok, dev) or len(data or "")
            body = (SOH + code.encode("ascii") + stamp(self.c).encode("ascii")
                    + b"?" * width + b"&&")
            return body + checksum(body).encode("ascii") + ETX
        # ...no more than the field reads, where it reads a width of its own:
        # `S50E00+999.9` answers five, `S52E01ABC` two
        from .console import FIELDS
        field = tok and (FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
                         or FIELDS.get(f"S{tok}00"))
        marks = len(data or "")
        if field and wire_cut(field):
            marks = min(marks, wire_cut(field))
        return (SOH + SEP.encode() + code.encode("ascii") + SEP.encode()
                + self.c.clock_stamp().encode("ascii") + SEP.encode()
                + b"?" * marks + SEP.encode() + ETX)

    def _clock_reply(self, code, digits):
        """501's own Set reply: the echo, the stamp, and the digits taken.

        Called before the clock moves, so the stamp is the one the console
        is answering from -- which is what the bench sent, a FEB 28 stamp
        over the JAN 19 digits that replaced it.
        """
        if code[:1].islower():
            body = (SOH + code.encode("ascii") + stamp(self.c).encode("ascii")
                    + digits.encode("ascii") + b"&&")
            return body + checksum(body).encode("ascii") + ETX
        return (SOH + SEP.encode() + code.encode("ascii") + SEP.encode()
                + self.c.clock_stamp().encode("ascii") + SEP.encode()
                + digits.encode("ascii") + SEP.encode() + ETX)

    def _field_width(self, tok, dev):
        """The computer-format width of a code's data field, or None."""
        from .console import FIELDS
        if not tok:
            return None
        field = (FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
                 or FIELDS.get(f"S{tok}00"))
        if not field or field.get("part"):
            return None
        return self.WIDTHS.get(field.get("kind"), lambda f: None)(field)

    #: codes whose devices are a fixed count, not a family's positions
    SET_DEVICES = {"503": 4}
    #: codes whose value is a position of another family, held to its count
    SET_MAX_FROM = {"785": "probe"}
    #: documented codes a software version refuses outright: {code: version}
    WITHHELD = {"78B": 26, "7AA": 26}
    #: `_canonical`'s answer for a computer-format value short of its width
    BARE = object()
    #: of `wiretables.ROW_FILTER`, the codes a COMPUTER-format Set is not
    #: gated for: `s77B0100000000` and `s77E011` were taken on a line that
    #: is not USER DEFINED, where the display Sets are refused
    UNGATED_PACKED = {"77B", "77E"}
    # The Version 4 test-warning codes against their Version 15 tank and
    # line twins, on the bench TLS-350 (2026-09-18): `S50700015` set both
    # 547 and 557 to 1 day, `S50A00300` both 54A and 55A to 300, `S506000`
    # disabled both 546 and 556. Reading one back is not symmetric: I507
    # answered 547's 20 with 557 at 25, and I50A answered 55A's 300 with 54A
    # at 350. 508 and 50B were not tried, and keep `Console.SHARED_CODES`.
    LEGACY_TWINS = {"506": ("546", "556"), "507": ("547", "557"),
                    "50A": ("54A", "55A")}
    #: which twin a Version 4 code reads, where it is not the tank's
    LEGACY_READ = {"50A": "55A"}

    # What the reconciliation and meter-map codes answer on a console with no
    # BIR key, which is every one of them answering -- not 9999 -- and most
    # with a bare frame. The bench TLS-350 (S-Module 330160-012: no BIR),
    # 2026-09-18, byte for byte. 795, 796 and 79F are not here because their
    # own reports print the same thing keyed or not. None is a bare frame;
    # a list is the lines after the stamp, blank lines and all.
    NO_BIR = {
        "790": None, "791": None, "792": None, "793": None, "794": None,
        "797": None, "798": None, "799": None, "79A": None, "79B": None,
        "79C": None, "79D": None, "7B2": None, "7B4": None, "7B5": None,
        "7B6": None,
        "79E": ["", "RECONCILIATION CLEAR MAP", "", ""],
        "7B0": ["LOGICAL       REAL    |     METER",
                "  FP     FP  BUS  SLOT| 0  1  2  3  4  5 ",
                "----------------------+------------------", "", ""],
        "7B1": ["FUELING POSITION - METER - TANK MAP", "",
                " BUS  SLOT  FUEL_P  METER  TANK",
                "-------------------------------", "TANK MAP EMPTY", "", "",
                ""],
        # the whole of the reply: three question marks
        "7B3": ["", "???"],
        # The BIR printout and ticketed-delivery switches, and the variance
        # printouts, answer a bare frame too -- 51C says why in so many words.
        # 513 (Tanker Load) and 515 (QPLD) have keys of their own and 54C is
        # CSLD's evaporation chart, on a console WITH CSLD; the bench has
        # none of BIR, Tanker Load or Fuel Manager, so all three are filed
        # here on this console's evidence and not on a rule anybody wrote.
        "511": None, "512": None, "513": None, "515": None, "51D": None,
        "51E": None, "532": None, "533": None, "534": None, "54C": None,
        "51C": ["", "", "", "MUST HAVE BIR", ""],
    }

    # Tank settings the bench TLS-350 lists for NO tank -- not a row, not a
    # position -- even with tank 1 switched on (2026-09-18, with no probe
    # wired to its card), where every other tank table lists all four
    # positions whether they are on or not. What they have in common is the
    # probe: CSLD's settings, the water limits and the settings that belong
    # to a probe TYPE (the CAP0 boot, the Mag floats). So they are read as
    # rows for a tank whose probe is reporting, and on a console with none
    # this is what each answers: its title alone, a blank and its title, its
    # title and heading, or a bare frame.
    NO_LIVE_TANK = {
        "613": ["CSLD PROBABLITY OF DETECTION", "", ""],
        "614": ["CSLD CLIMATE FACTOR", "", ""],
        "617": ["CSLD CUSTOM PROBABLITY OF DETECTION", "", ""],
        "618": ["", "CSLD EVAPORATION COMPENSATION", "", ""],
        "619": None,
        "61B": ["", "IN-TANK LEAK GROSS TEST AUTO-CONFIRM", "", ""],
        "61C": ["", "CSLD REPORT ONLY", "", ""],
        "624": None, "627": None, "60E": None,
        "62E": ["", "CAP0 PROBE CONDUCTIVE BOOT FLAG", "",
                "TANK   PRODUCT LABEL          CAP0 CONDUCTIVE BOOT:", ""],
        "62F": ["", "MAG PROBE FLOAT SIZE", "",
                "TANK   PRODUCT LABEL                FLOAT SIZE:", ""],
    }

    # What the bench TLS-350 does with a Set it has no use for, which is
    # decided before the value is looked at: every value tried, good or bad,
    # got the same answer. Measured by the Set sweep of 2026-09-18 (981 Sets,
    # `tests/test_bench_sweep.py`). A code answers one of three ways --
    #   bare      the echo alone, nothing stored;
    #   refused   one `?` per character sent, as for a bad value;
    #   answered  its Inquire report, unchanged, as for a good one.
    # Without BIR: its own table says which codes, and of those the three
    # below are refused rather than bare, and 51C answers MUST HAVE BIR.
    NO_BIR_SET_REFUSED = {"511", "512", "515"}
    # Only the codes the sweep reached; the ticket codes (7B5, 7B6) have a
    # result-code reply of their own and were not tried.
    NO_BIR_SET = {"511", "512", "513", "515", "51C", "51D", "51E", "797",
                  "798", "799", "79A", "7B2"}
    # With no tank whose probe reports: the CSLD settings answer their title,
    # 619 is bare, and the water limits, the float size and the periodic test
    # type are refused -- 62C though its Inquire lists a row for every tank.
    NO_LIVE_TANK_SET_REFUSED = {"624", "627", "62C", "62F"}
    NO_LIVE_TANK_SET = {"613", "614", "618", "619", "61B", "61C"} \
        | NO_LIVE_TANK_SET_REFUSED
    #: of `NO_LIVE_TANK`, the ones that answer one tank with a bare frame
    #: (62E: `I62E01` on the bench, 2026-09-22)
    NO_LIVE_TANK_BARE_ONE = {"62F", "62E"}

    def _bench_set_gate(self, tok, dev, code, data):
        """A Set the bench answers the same whatever it carries, or None."""
        if tok in SETTABLE and not self._module_present(tok):
            # `S72C01-5.0` on a console with no smart sensor card is bare,
            # and so is `S72C01ABC`: the card is asked about before the value
            return self._frame(code), "no module fitted: acked, nothing stored"
        if not self.c.licensed("bir") and tok in self.NO_BIR_SET:
            if tok in self.NO_BIR_SET_REFUSED:
                return self._refused(code, data, tok, dev), "no BIR key: refused"
            text = self.NO_BIR[tok]
            if text is None or code[:1].islower():
                return self._frame(code), "no BIR key: a bare frame"
            return self._raw_display(code, text), "no BIR key"
        if tok in self.NO_LIVE_TANK_SET and not self.c.tank_level:
            if tok in self.NO_LIVE_TANK_SET_REFUSED:
                return (self._refused(code, data, tok, dev),
                        "no tank with a probe: refused")
            text = self.NO_LIVE_TANK[tok]
            if text is None or code[:1].islower():
                return self._frame(code), "no tank with a probe: bare"
            return self._raw_display(code, text), "no tank with a probe"
        if tok == "681" and not self.c.licensed("fuelman"):
            return self._refused(code, data, tok, dev), "no Fuel Manager: refused"
        if tok == "61E" and not self._mass_density_on():
            # No density without Mass/Density: the bench TLS-350 refused
            # `S61E030.8500` with six `?` while it was disabled, and kept
            # the tank's thermal coefficient as it was (2026-09-19,
            # `transcripts/sdw10`). This answered a bare frame and quietly
            # rewrote the coefficient from the density it had refused.
            return (self._refused(code, data, tok, dev),
                    "mass/density disabled: refused")
        if tok == "525" and data.strip().isdigit():
            # "Enter the slot number in which the a modem module ... is
            # installed", 576013-623 Rev AN p.6-8 -- so a port with no card
            # in it, or the RS-232 port the tool itself is on, is not a
            # port to dial from. The bench TLS-350 (RS-232 on comm 1, the
            # serial satellite on comm 2, 3 to 6 empty) took 2 and refused
            # 0, 1 and 3 to 7 (2026-09-18/19, `transcripts/port525.log`).
            card = self.c.comm_positions().get(int(data.strip()))
            if card is None or card[1] == "rs232":
                return (self._refused(code, data, tok, dev),
                        "no card to dial from on that port")
        if (tok == "786" and data.strip() in ("5", "05")
                and self.c.version <= ALTERNATE_HT_AFTER):
            # MANIFOLDED: ALTERNATE-HT is 576013-623 Rev AN's and not
            # 326.01's: the bench refused `S786015` (2026-09-18) and took
            # modes 1 to 4. No page or version table dates it, so this
            # says only what was measured -- version 26 and older refuse it
            # -- and a later console takes it, as the manual documents.
            return (self._refused(code, data, tok, dev),
                    "ALTERNATE-HT: not in this software")
        keep = wiretables.ROW_FILTER.get(tok)
        if code[:1].islower() and tok in self.UNGATED_PACKED:
            keep = None
        if keep and dev.isdigit() and int(dev) and not keep(self.c, int(dev)):
            # the user-defined pipe's settings, and the second length a
            # fiberglass pipe has, on a line whose pipe is neither: refused.
            # `S77701` is taken the moment S788 makes the line USER DEFINED.
            return (self._refused(code, data, tok, dev),
                    "not this line's pipe type: refused")
        return None

    def _glass_reply(self, code):
        """5FA: what is on the display, as the bench TLS-350 answers it.

        Undocumented, and found by the hex census on 2026-09-18: the two
        24-column lines run together as one 48-character line, the top the
        clock to the second and the second the standing messages in turn,
        one a clock second (2026-09-19, two alarms standing: `:54` PLLD,
        `:55` PRINTER ERROR, `:56` PLLD), or ALL FUNCTIONS NORMAL. Sampled five times a
        second on the bench, the message line never blanked, acknowledged or
        not. Computer format carries the same 48 characters.

        Off the resting display it is the menu: at the bench keypad
        (2026-09-19) i5FA00 read `SYSTEM SETUP / PRESS <STEP> TO CONTINUE`,
        `SET MONTH DAY YEAR / DATE: 1-/--/----` mid-entry, and so on --
        the characters under the cursor, not the flashing block. With a
        panel attached (`Console.glass_source`, which `ui.SimApp` sets) this
        answers what the panel shows; without one, the resting display.
        """
        source = getattr(self.c, "glass_source", None)
        away = source() if source else None
        if away:
            glass = "".join(row[:24].ljust(24) for row in (list(away) + ["", ""])[:2])
            if code[:1].islower():
                return self._frame(code, glass)
            return self._raw_display(code, [glass])
        # centred: the numeric formats are shorter than the line (`   01-16-06
        # 20:30:09    ` on the bench), and 01 fills it
        top = self.c.clock_text()[:24].center(24)
        shown = [a["screen"] for a in describe_alarms(self.c.compute_alarms())]
        if shown:
            beat = int(time.mktime(self.c.now()))
            second = shown[beat % len(shown)][:24].ljust(24)
        else:
            second = "ALL FUNCTIONS NORMAL".center(24)
        glass = top + second
        if code[:1].islower():
            return self._frame(code, glass)
        return self._raw_display(code, [glass])

    def _bench_state_reply(self, tok, dev, code):
        """The bench TLS-350's own answer where this console had none, in the
        state the bench was in -- a feature not keyed, a card not fitted, a
        setting never made -- or None. Every body below is byte for byte
        `tests/console_capture/bench-2026-09-18/cap_coldstart`, taken on
        2026-09-18 out of a cold start; anything the state does not cover
        goes on to the renderer that was here."""
        c = self.c
        if tok == "611" and c.values.get("S61100") is None:
            # LEAK TEST METHOD, never set: TEST ON DATE for all tanks, on the
            # day the console came up, with no start time. That day is
            # STORED: the bench printed JAN 16, 2006 with its clock at JAN
            # 22, off tank 1's `0201060116EE00` (`TTDDRMYYMMDDHHmm`, the
            # method 1 and the date 060116), so a record held is read.
            date = time.strftime("%b %d, %Y", c.now()).upper()
            held = (c.values.get("S61101") or "").strip()
            if len(held) == 16 and held[5] == "1" and held[6:12].isdigit():
                try:
                    date = time.strftime("%b %d, %Y", time.strptime(
                        held[6:12], "%y%m%d")).upper()
                except ValueError:
                    pass
            # and the early stop is TANK 1's 61A: with tank 2 alone enabled
            # the bench printed DISABLED here, with tank 1 ENABLED
            # (2026-09-19, `transcripts/earlystop.log`)
            early = ("ENABLED" if (c.values.get("S61A01") or "").strip()
                     .endswith("1") else "DISABLED")
            return self._raw_display(code, [
                "", "LEAK TEST METHOD", "- - - - - -  - - - - - -",
                "TEST ON DATE : ALL TANK", date, "START TIME : DISABLED",
                "TEST RATE  :0.20 GAL/HR", "DURATION   : 2  HOURS", "",
                "TST EARLY STOP:" + early, ""])
        if not c.licensed("fuelman"):
            if tok in ("681", "682"):
                return self._frame(code)
            if tok == "680":
                # the setup report answers with its defaults under the
                # station header whether or not Fuel Manager is keyed
                # -- and a block of zero average sales for every tank that
                # is configured, keyed or not (`cap_tank1on`, 2026-09-19)
                blocks = []
                # and the one asked for, configured or not: `I68001` drew
                # tank 1's block with every tank off (2026-09-22)
                for t in ([int(dev)] if dev.isdigit() and int(dev)
                          else c.configured("601", c.capacity("probe"))):
                    # the tank's label first: `REGULAR UNLEADED     ( TANK 1 )`
                    blocks += [f"{c.text('602', t) or '':<21.21s}( TANK {t} ) ",
                               "   SUN   MON   TUE   WED   THR   FRI   SAT",
                               "     0" * 7, ""]
                return self._frame(code, SEP.join([
                    "FUEL MANAGEMENT SETUP", "", "DELIVERY WARN DAYS:  0.0",
                    "AUTO PRINT: DISABLED", "", "",
                    "FUEL MANAGEMENT AVERAGE SALES (GALLONS)", ""]
                    + blocks + [""]))
        if tok == "5E2" and dev.isdigit() and int(dev):
            # a record with its time disabled (`I5E201`, 2026-09-22), where
            # this printed the stored `EE00`; and an enabled one's time in
            # twelve hours, the hour space padded -- `RECORD 1 :  2:30 PM`,
            # `12:05 AM` for 0005 -- in every DATE/TIME FORMAT, 24-hour ones
            # too (2026-09-25, `bench-2026-09-25/fmt5e2.jsonl`), where this
            # printed the stored `1430`
            held = (c.values.get(f"S5E2{dev}") or "EE00").strip()[-4:]
            when = "DISABLED"
            if held.isdigit() and int(held[:2]) < 24 and int(held[2:]) < 60:
                when = clock_hhmm(time.strptime(held, "%H%M"))
            return self._raw_display(code, ["", f"RECORD {int(dev)} : {when}"])
        if tok == "904" and not c.has("wplld"):
            # the software is the COMM module's (576013-818 Rev AB ch.6: "the
            # software version number of the WPLLD Comm Module"), and it shows
            # with the comm module alone
            part, made = (self.WPLLD_COMM_SOFTWARE if c.has("wplldcom")
                          else ("", ""))
            return self._raw_display(code, [
                " WPLLD DIAGNOSTIC DATA  ", "- - - - - -  - - - - - -",
                f"#: {part:<14s}" if part else "#: ", made,
                "PC COMM ERRORS  =      0", "", "", "", ""])
        if tok == "B21" and c.has("probe") and c.probe_gt:
            # the G.T. board's four ground temperature inputs, each its
            # sample counter, the two reference counts, and the resistance
            # read -- a thousand million on an input with nothing on it
            # (`groundtemp`). `IB2101` answered input 1 alone.
            inputs = range(1, 5) if dev == "00" else [int(dev)]
            rows = []
            for n in inputs:
                count, high, low, _last, average = c.thermistor_row(n)
                rows.append(f"{n:6d}{count:8d}{high:9d}{low:10d}"
                            f"{int(round(average)):13d}")
            return self._raw_display(code, [
                "", "GROUNDTEMP DIAGNOSTIC REPORT", "",
                "        SAMPLE     HIGH       LOW",
                "SENSOR COUNTER      REF       REF        VALUE"]
                + rows + [""])
        if tok == "BA0" and not (c.has("dim") or c.has("mdim")):
            return self._raw_display(code,
                                     ["MDIM TOTALIZER", "", "", "", ""])
        if tok == "51B" and self._dst_on():
            # both windows whatever the device: `I51B01` printed the start
            # and the end, as `I51B00` does (2026-09-22)
            return self._raw_display(code, self._dst_lines())
        if tok in ("5BE", "5BF") and self._custom_alarms_on():
            # in no revision on this shelf; with custom alarms on (5BD) the
            # bench answers its title and nothing set (`cap_tank1on`)
            return self._raw_display(code, ["", "CUSTOM ALARMS".ljust(24), ""])
        if tok == "V10" and not c.licensed("isd"):
            # the ISD software's version answers on a console with no ISD key
            return self._raw_display(code, ["", "ISD VERSION: 01.00", ""])
        if tok == "A91" and not self._tanks(dev):
            # the outage report is per tank, and a console with none has
            # nothing to report -- not even the title
            return self._frame(code)
        return None

    def _bench_computer_reply(self, tok, dev="00"):
        """The computer-format data the bench TLS-350 answers where this
        console had none, or None. Read off `cap_swept` (2026-09-18), both
        formats of every answering code in the state the Set sweep left.

        The clock is 501's own value; 505 is 517's units and language with
        the language one digit wide; the codes `FIXED` and `NO_BIR` draw on
        the display side have packed forms of their own; 7C3's 132 gallons
        is on every tank; the ground temperature block is `_bench_state_
        reply`'s B21 as floats; and the ISD version answers without a key.
        """
        c = self.c
        if tok == "501":
            return stamp(c)
        if tok == "505":
            units_language = (c.values.get("S51700")
                              or self._default_stored("517", "00")
                              or self._blank_stored("517", "00") or "")
            if len(units_language) == 3 and units_language.isdigit():
                return units_language[0] + str(int(units_language[1:]))
            return None
        if tok == "51F":
            return "0"
        if tok == "55D":
            return self._precision_print()
        if tok == "51B" and self._dst_on():
            # the start alone, as `i51B00` answered it
            return self._dst_window()[0]
        if tok in ("5BE", "5BF") and self._custom_alarms_on():
            return "00"
        if tok in ("681", "682") and not c.licensed("fuelman"):
            return ""
        if tok == "535":
            # and one receiver asked for is its own record: `i53501` packs
            # `0100` on the bench (2026-09-22), where this answered nothing
            receivers = ([int(dev)] if dev.isdigit() and int(dev)
                         else range(1, 9))
            return "".join(f"{n:02d}{int(self._hangup(n)):02d}"
                           for n in receivers)
        if not c.licensed("bir") and tok in ("79E", "7B0"):
            return "00"
        if not c.licensed("bir") and tok == "7B3":
            return "00000"
        if tok == "7C3" and c.has("probe"):
            return "".join(
                f"{n:02d}" + packed.hexfloat(units.out(
                    "volume", float(blankrows.max_volume(c, n)),
                    units.system(c)))
                for n in range(1, c.capacity("probe") + 1))
        if tok == "904" and not c.has("wplld"):
            if c.has("wplldcom"):
                part, made = self.WPLLD_COMM_SOFTWARE
                return f"{part:<14s}{made}00000000"
            return "00000000"
        if tok == "B21" and c.has("probe") and c.probe_gt:
            # count, then the samples, the two references, the last sample
            # and the average -- the average is the display's VALUE. One
            # input asked for is that input's block alone: `iB2102` packed
            # input 2 (2026-09-22), where this answered nothing at all
            inputs = [int(dev)] if dev.isdigit() and int(dev) else range(1, 5)
            return "".join(f"{n:02d}05" + "".join(
                packed.hexfloat(float(v)) for v in c.thermistor_row(n))
                for n in inputs)
        if tok == "V10" and not c.licensed("isd"):
            return "01.00"
        return None

    # A code in no revision of 576013-635 on this shelf, which the bench
    # TLS-350 answers (2026-09-18) with what it holds out of a cold start.
    # It is a setting, and no page says how to set it -- and setting the
    # protocol's own prefix could leave a tool unable to talk to it, so it
    # was not tried -- so this console answers what the bench did and takes
    # no Set for it.
    FIXED = {
        "51F": ["", "EURO PROTOCOL PREFIX", "S", ""],
    }

    # 55D, PRINT PRECISION LINE TEST RESULTS, is in no revision either, and
    # it takes a Set: the bench TLS-350 answered `S55D001` ENABLED and
    # `S55D000` DISABLED, packed 1 and 0, and refused `S55D002` with a `?`
    # (2026-09-19, `transcripts/p55d.log`). What it changes on the paper
    # was not seen: the printer was not watched.
    PRECISION_PRINT = {"0": "DISABLED", "1": "ENABLED"}

    def _precision_print(self):
        return (self.c.values.get("S55D00") or "0")[-1:]

    def _precision_print_lines(self):
        return ["", "PRINT PRECISION LINE TEST RESULTS: "
                + self.PRECISION_PRINT[self._precision_print()], ""]

    def _set_max_volume(self, dev, code, data):
        """7C3, TANK MAXIMUM VOLUME LIMIT: a whole number of gallons, kept in
        sixteen bits. The bench TLS-350 (2026-09-19, `transcripts/p7c3*`):
        `S7C301200` took 200; `132.5` took 132; `9999999` was read as six
        digits and held 999999 as 16959; `65535` held, `65536` held 0 and
        `65537` 1; `0`, `-1` and `ABC` were refused. Packed, `s7C301` took
        a float and dropped its fraction, and refused 0.0. Every value read
        back as a float."""
        text = (data or "").strip()
        if code[:1].islower():
            try:
                value = packed.unhexfloat(text[:8])
            except ValueError:
                value = None
        else:
            whole = text[:6].split(".")[0]
            value = float(whole) if whole.isdigit() else None
            if value is not None:
                value = units.into("volume", value, units.system(self.c))
        if value is None or value != value or value < 1:
            return self._refused(code, data, "7C3", dev), "REJECTED: not a volume"
        held = int(value) % 65536
        tanks = ([int(dev)] if dev.isdigit() and int(dev)
                 else range(1, self.c.capacity("probe") + 1))
        for n in tanks:
            self.c.values[f"S7C3{n:02d}"] = str(held)
        self.c.save()
        return self._answered("7C3", dev, code), f"max volume {held}"

    def _set_precision_print(self, code, data):
        value = (data or "").strip()
        if value not in self.PRECISION_PRINT:
            return self._refused(code, data), "REJECTED: 0 or 1"
        self.c.values["S55D00"] = value
        self.c.save()
        if code[:1].islower():
            return self._frame(code, value), "stored"
        return self._raw_display(code, self._precision_print_lines()), "stored"

    # 535, RECEIVER AUTO COMPUTER MODE HANGUP, is in no revision either --
    # and it does take a Set: the bench TLS-350 answered `S535011` with
    # HANGUP and `S535010` with CHARACTER, and refused 2, 3 and 9 with a
    # `?` (2026-09-19, `transcripts/probes.jsonl`). Held per receiver.
    HANGUP_WORDS = {"0": "CHARACTER", "1": "HANGUP"}

    def _hangup(self, receiver):
        return (self.c.values.get(f"S535{receiver:02d}") or "0")[-1:]

    def _hangup_lines(self, dev):
        receivers = ([int(dev)] if dev.isdigit() and int(dev)
                     else list(range(1, 9)))
        return (["", "RECEIVER AUTO COMPUTER MODE HANGUP", "",
                 "RCVR   LOCATION LABEL       METHOD"]
                + [f"{r:2d}                          "
                   f"{self.HANGUP_WORDS[self._hangup(r)]}"
                   for r in receivers])

    def _set_hangup(self, dev, code, data):
        value = (data or "").strip()
        if value not in self.HANGUP_WORDS:
            return self._refused(code, data), "REJECTED: 0 or 1"
        receivers = ([int(dev)] if dev.isdigit() and int(dev)
                     else list(range(1, 9)))
        for r in receivers:
            self.c.values[f"S535{r:02d}"] = value
        self.c.save()
        if code[:1].islower():
            return self._frame(code, self._bench_computer_reply("535")), "stored"
        return self._raw_display(code, self._hangup_lines(dev)), "stored"

    def _etx_table(self, tok, port, code):
        """537's or 538's reply at one port, which a Set answers with too.

        The bench TLS-350 (2026-09-25, `bench-2026-09-25/etx*.jsonl`): the
        title, the heading, and the port's row -- its number in two, the
        first character at 9 and the second four after it, as stored, a
        control byte and all -- and packed, the characters alone."""
        chars = self.c.values.get(f"S{tok}{port:02d}") or ""
        if code[0].islower():
            # raw, not through `printable_body`: the bench packed `\x04A`
            # with the control byte in it. Nothing stored here can forge a
            # frame, since SOH, CR and ETX each end a Set before its data.
            body = (SOH + code.encode("ascii") + stamp(self.c).encode("ascii")
                    + chars.encode("latin-1") + b"&&")
            return body + checksum(body).encode("ascii") + ETX
        a, b = (chars + "  ")[:1], chars[1:2]
        row = f"{port:2d}       " + (a if chars else "") + "    " + b
        title = ("DISPLAY" if tok == "537" else "COMPUTER") + \
            " MODE RS-232 ETX CHARACTERS"
        return self._raw_display(code, ["", title, "", "PORT    ETX    ETX",
                                        "", row, ""])

    def _raw_display(self, code, rows):
        """A display reply exactly as `rows` lays it out under the stamp."""
        return (SOH + SEP.encode() + code.encode("latin-1") + SEP.encode()
                + self.c.clock_stamp().encode("latin-1") + SEP.encode()
                + SEP.join(rows).encode("latin-1") + SEP.encode() + ETX)

    def _set_family(self, tok):
        """The card family whose positions a code's devices are, or None."""
        family = wiretables.FAMILY_OF.get(wiretables.device_letter(tok))
        return family[0] if family else None

    def _computer_aggregate(self, tok):
        """Device 00 of a per-device setting in computer format: every
        position the cage has, programmed or not.

        The bench TLS-350 (2026-09-18, `cap_swept`) answers `i60200` with
        four tanks of twenty blanks on a console that labelled none,
        `i60400` with tank 1's 200000 and three zeros, `i60300` with each
        tank's own number as its product code, and `i78200` with the three
        lines one controller carries. This answered only what was stored --
        nothing at all on a blank console. A position nobody set carries
        what it holds: its default, else what the display shows for it,
        else the field's blank.
        """
        family = self._set_family(tok)
        count = self.c.capacity(family) if family else 0
        head = (wiretables.columns(tok) or [{}])[0].get("head")
        if not family and (head == "RCVR" or (head == "DEVICE"
                                              and tok.startswith("52"))):
            # the receivers: `i52100` lists all eight, as I535 prints them
            count = 8
        elif not family and head == "TANK":
            # a tank table whose rows carry no letter: 62B, 62D, 631, 639
            count = self.c.capacity("probe")
        if tok in self.SET_DEVICES:
            # the print header answers only the lines that were set
            count = 0
        if tok in ("681", "682", "683") and not self.c.licensed("fuelman"):
            return ""
        if count <= 0:
            return self.c.aggregate(tok)
        keep = self._packed_filter(tok)
        prefixed = self.c.is_prefixed(tok)
        out = []
        for n in range(1, count + 1):
            if keep and not keep(self.c, n):
                # a user-defined pipe's setting, on a line that is not one
                continue
            dev = f"{n:02d}"
            # the store and the default both hold a prefixed code's value
            # with its device in front (`_with_prefix`); a blank does not
            val = self.c.values.get(f"S{tok}{dev}")
            if val is None:
                val = self._default_stored(tok, dev)
            if tok == "77F" and not wiretables.ROW_FILTER["77F"](self.c, n):
                # no second length on this pipe: `i77F00` packed three zeros
                # with Q1 on type 19 and Q2 and Q3 on 03, where their
                # default would have read 351 (`cap_swept`, 2026-09-19)
                val = None
                body = packed.hexfloat(0.0)
            elif val is not None:
                body = val[2:] if prefixed and len(val) >= 2 else val
            else:
                body = self._blank_stored(tok, dev)
            if body is None:
                continue
            body = self._packed(tok, "00", body)
            out.append(dev + body if prefixed else body)
        return "".join(out)

    def _packed_filter(self, tok):
        """`wiretables.ROW_FILTER` as the computer format's device 00
        applies it: 777 to 77A answer nothing for a line whose pipe is not
        USER DEFINED, 77B and 77E nothing for one that is neither that nor
        Petrotechnik -- `i77B00` packed Q1 alone with Q1 on type 19 and the
        others on 03 (`cap_swept`, and again 2026-09-19) -- and the second
        length 77F every line, 0 where the pipe has none (`_computer_
        aggregate`). One line asked for by number is not filtered here."""
        if tok == "77F":
            return None
        return wiretables.ROW_FILTER.get(tok)

    def _blank_stored(self, tok, dev):
        """What an unset position holds with no documented default: the
        display's own reading of it encoded back, or the field's blank --
        blanks for a label, zeros for a number, the first choice's zero."""
        from .console import FIELDS
        if tok == "603":
            # a tank's product code starts as its own number: `i60300`
            # answers 1, 2, 3 and 4 on a console that set none
            return str(int(dev))
        if tok == "62B":
            # a last annual test never run is a date of zeros, which the
            # display prints `???  0,    0`: `i62B00` packs `01000000` and
            # so on for each tank (`cap_swept`, and every capture since)
            return "000000"
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
        if not field or field.get("part"):
            # a record of parts is the parts' blanks, in the record's order:
            # 605's four zero volumes, 631's two flags, 639's shape and factor
            parts = wiretables.parts_of(tok, dev)
            # a part with a documented default holds it, as the display
            # already reads it: 881's baud is `01200` and its rings `01`,
            # where this packed the first choice and zero -- 300 baud and no
            # rings under a display that said 1200 (2026-09-22)
            blanks = [str(p["default"]) if p.get("default") is not None
                      and len(str(p["default"])) == (p.get("part")
                                                     or [0, 0])[1]
                      else self._kind_blank(p, (p.get("part") or [0, None])[1])
                      for p in parts]
            if parts and tok in ("605", "606"):
                # a chart's first figure is the tank's full volume, the one
                # value 604 and 60A hold: 200000 in `i60500` and `i60600`
                # on the bench, with no chart point set
                full = (self.c.limit("604", int(dev))
                        or self.c.limit("60A", int(dev)) or 0.0)
                blanks[0] = packed.hexfloat(full)
            return None if not parts or None in blanks else "".join(blanks)
        if not field:
            return None
        shown = self._display_value_us(tok, dev)
        if shown not in (None, ""):
            try:
                return fieldio.encode_value(field, str(shown).strip(),
                                            self.c.metric(), self.c,
                                            code=f"S{tok}{dev}")
            except (ValueError, TypeError):
                pass
        return self._kind_blank(field)

    def _kind_blank(self, field, width=None):
        """A field's blank by its kind, at `width` or its own."""
        kind = field.get("kind")
        width = width or self.WIDTHS.get(kind, lambda f: None)(field)
        if kind == "text":
            return " " * (width or 20)
        if kind == "float":
            return packed.hexfloat(0.0)
        if kind in ("int", "digits", "flag", "slots"):
            return "0" * (width or 1)
        if kind == "enum" and field.get("choices"):
            return str(field["choices"][0][0])
        return None

    def _default_stored(self, tok, dev):
        """A field's documented default, in the form the store holds it, or
        None where the field has none."""
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
        if tok == "628" and dev.isdigit() and int(dev):
            # a maximum volume nobody set is the tank's full volume, and
            # follows it: tanks 2 to 4 read 12000, 8000 and 6000 on the
            # bench as their 604s did (`cap_site`, 2026-09-19)
            full = self.c.limit("604", int(dev)) or self.c.limit("60A", int(dev))
            return self._with_prefix(tok, dev, packed.hexfloat(full or 0.0))
        if field and field.get("default_litres") is not None:
            # a default the firmware holds in litres: 634's is 3 and 635's 4,
            # stored as 0.7926 and 1.0568 gallons -- which a U.S. display
            # cuts to the 0 and 1 the cold start printed (`cap_metric`)
            gallons = units.into("volume", float(field["default_litres"]),
                                 units.METRIC)
            return self._with_prefix(tok, dev, packed.hexfloat(gallons))
        if not field or field.get("part") or field.get("default") is None:
            return None
        try:
            value = fieldio.encode_value(field, str(field["default"]),
                                         self.c.metric(), self.c,
                                         code=f"S{tok}{dev}")
        except (ValueError, TypeError):
            return None
        return self._with_prefix(tok, dev, value)

    def _nothing_to_report(self, tok, dev="00"):
        """Whether `tok` is a device report and there is no such device.

        For one device asked by number, whether THAT one is missing: the
        bench TLS-350, every tank and line off, answered `I20201`, `I37301`
        and the rest with the frame alone as it did their `00`s, where this
        drew device 1's report (2026-09-22)."""
        one = int(dev) if dev.isdigit() and int(dev) else None
        if tok in self.TANK_REPORTS:
            # a probe reporting, not a tank programmed: see `_tanks`
            return (not self.c.tank_level if one is None
                    else one not in self.c.tank_level)
        if tok in self.LINE_REPORTS:
            # a line switched ON, not one merely labelled: see
            # `Console.programmed_lines`
            on = {n for kind, n, _label in self.c.programmed_lines()
                  if kind == "plld"}
            return not on if one is None else one not in on
        return False

    def handle(self, raw):
        """Answer one command, and never let a defect take the session down.

        Anything that raised below here -- a parse a malformed command
        reached, a stored value its own inquiry could not read -- went out
        through `_session`, which catches `OSError` only, so the thread died,
        the connection dropped and the tool's next send got
        `ConnectionAbortedError`. A console refuses what it cannot answer,
        with the same frame as a code it does not know, and the session
        carries on.
        """
        try:
            return self._handle(raw)
        except Exception as e:
            note = f"{raw!r}   [refused: {type(e).__name__}: {e}]"
            if self.verbose:
                print("  " + note)
                traceback.print_exc()
            if self.log:
                self.log(note)
            try:
                letter = parse_command(raw)[1] or ""
            except Exception:
                letter = ""
            return self._eom(NOT_UNDERSTOOD, letter)

    def _handle(self, raw):
        # A console with the breaker open is dark everywhere at once: no
        # display, no printer, and nothing on the serial port either.
        if not self.c.powered:
            if self.log:
                self.log(f"{raw!r}   [console has no power]")
            return b""
        self.c.tick()
        # No comm card in the cage, no serial port. A real console with its
        # RS-232 module pulled has nothing to answer on, so neither does this
        # one: the socket stays open but the console is deaf. 329362-001 is
        # the RS-232 card; the modem and MT cards are ports too, and so is
        # the DB-9 half of any dual-port module -- 577013-819's own procedure
        # plugs a laptop into "the TLS console's RS-232 or Multiport card".
        #
        # A card in the WRONG SLOT is the same silence with a card in the
        # cage, which is 576013-818 Table 7-2: "System will not communicate
        # via RS-232 Module -- RS-232 Module in slot 4 of Comm Bay card cage
        # -- Move Module to Comm Cage slots 1, 2, or 3." See FIDELITY M7.
        if not self.c.serial_port_works():
            if self.log:
                self.log(f"{raw!r}   [no comm card fitted]")
            return b""
        security, letter, tok, dev, data = parse_command(raw)
        # 576013-635 p.267: with the security DIP on and a code programmed,
        # "the system will not respond to a command without the proper
        # security code." No response at all, not an error frame: a caller
        # without the code cannot tell the console is even there.
        if self.c.rs232_enforces_security() and security != self.c.security_code():
            if self.log:
                self.log(f"{raw!r}   [refused: RS-232 security]")
            return b""
        if security and not self.c.rs232_enforces_security():
            # And with security OFF a code in front is not a code at all:
            # the bench TLS-350, security disabled on every port, answered
            # `000000I20100` -- its own code, in front of a good command --
            # with 9999FF (2026-09-19, `transcripts/edge2`). The six digits
            # are read as the function code, which no console knows.
            # (`123456I20100` and `12345I20100` answered a frame of their
            # own, `123309`, which no page explains; not built.)
            if self.log:
                self.log(f"{raw!r}   [a security code with security off]")
            return NOT_UNDERSTOOD
        if not letter:
            # A command too short to be one gets nothing at all. This
            # answered 9999FF from 2026-08-19 (2ba6a71) so that somebody
            # typing half a command at a telnet prompt got something back;
            # the bench TLS-350 answers `I999<CR>` and a lone `<SOH><CR>`
            # with silence (2026-09-23), and the console is what this
            # matches. The manual's 9999FF is for "a function code that it
            # does not recognize", which a command this short does not reach.
            if self.verbose:
                print(f"  {raw!r}   [too short: no reply]")
            if self.log:
                self.log(f"{raw!r}   [too short: no reply]")
            return b""
        # The console echoes the function code exactly as it was sent. Only
        # the dispatch is case-folded, which is why parse_command upper-cases
        # the token. Sending i@a900 to a real TLS-350 brings back i@a900, not
        # i@A900 -- the only codes where it shows are the undocumented @
        # family, because everything else is typed in one case anyway.
        code = f"{letter}{_sent_token(raw, tok)}{dev}"
        if (letter in ("I", "i") and len(dev) == 2 and dev[:1].isdigit()
                and (dev[1:].isalpha() or dev[1:] < " ") and tok in KNOWN):
            # (and a digit then a control character, on one observation:
            # `I2019<BS>` was the bare frame too, where `I2019<DEL>` was
            # 9999FF, 2026-09-23)
            # A digit then a letter is a device the console reads and has not
            # got: the bench TLS-350 answered `I2010A` with the report's bare
            # frame, as it does a tank past the cage, and refused `I201A0`
            # (2026-09-19, `transcripts/edge2`). Sets were not tried.
            return self._frame(code)
        if not dev.isdigit():
            # the device is two decimal digits; anything else is not a command
            if self.log:
                self.log(f"{code}   [not understood]")
            return self._eom(NOT_UNDERSTOOD, letter)
        if tok not in KNOWN:
            # "a function code that it does not recognize"
            if self.log:
                self.log(f"{letter}{tok}{dev}   [not understood]")
            return self._eom(NOT_UNDERSTOOD, letter)
        bound = DEVICE_MAX.get(tok.upper())
        if bound is not None and int(dev) > bound:
            # a device number past the ceiling its own notes print
            if self.log:
                self.log(f"{letter}{tok}{dev}   [device past {bound}]")
            return self._eom(NOT_UNDERSTOOD, letter)
        if (not self.c.supports(versions.TOKEN_FEATURE.get(tok.upper()))
                or not versions.knows_token(tok.upper(), self.c.version,
                                            self.c.board)):
            # a function that arrived with a later software version is one
            # this console has never heard of, which is the same answer.
            # Either half can say so: a code that came in with a feature,
            # or one the manual heads with a version of its own,
            # "Function Code: 905 ... Version 15"
            if self.log:
                self.log(f"{letter}{tok}{dev}   "
                         f"[not in software {self.c.version}]")
            return self._eom(NOT_UNDERSTOOD, letter)
        with self.c.lock:
            # _tanks needs to know which family it is answering for. Set
            # under the lock: one Handler serves every session on the port,
            # and set before it, two clients at once could swap it between
            # this line and the read.
            self._token = tok
            # A command arriving on the serial port is NOT a 888 connection.
            # This used to note one -- `06=RS232 REQUEST` and a TIME OF LAST
            # COMM DATA -- on every command. The bench TLS-350 answers I88800
            # with `CONNECTION : NONE` on both its boards, with no time line,
            # in four captures taken over hours of commands arriving on one
            # of them (2026-09-18), and p.474's own sample has the same NONE
            # on the port being asked. What 888 records is what the console
            # itself dialled or was dialled for: autodial and auto transmit
            # still note theirs.
            if letter in "Ii":
                out, note = self.inquire(tok, dev, code, data)
            elif letter in "Ss":
                out, note = self.set_(tok, dev, data, code)
            else:
                out, note = self._nine(code), "bad command letter"
        line = f"{letter}{tok}{dev}  {data[:34]!r}" + (f"   [{note}]" if note else "")
        if self.verbose:
            print("  " + line)
        if self.log:
            self.log(line)
        return self._eom(out, letter)

    def _eom(self, out, letter):
        """End a reply with the programmed end-of-message characters in
        place of its ETX (531, 537 and 538; `Console.reply_end`). An empty
        reply is returned untouched."""
        if not out or not out.endswith(ETX):
            return out
        return out[:-1] + self.c.reply_end(computer=letter.islower())

    # ---- read --------------------------------------------------------------
    def inquire(self, tok, dev, code, data=""):
        """`_inquire`, in the console's system units (`units`): the tables
        are told which system they draw in, and a display reply that names
        a unit or a test rate has its words put into it -- GALLONS to
        LITERS, `0.10 TEST SCHEDULE` to `0.38 TEST SCHEDULE`."""
        system = units.system(self.c)
        wiretables.UNITS[0] = system
        try:
            out, note = self._inquire(tok, dev, code, data)
        finally:
            wiretables.UNITS[0] = units.US
        if (system != units.US and code[:1].isupper() and out
                and (tok in units.QUANTITY or tok in units.RATE_TEXT)
                and not out.startswith(NOT_UNDERSTOOD[:5])):
            text = out.decode("latin-1")
            text = (units.rate_words(text, system) if tok in units.RATE_TEXT
                    else units.words(text, system))
            out = text.encode("latin-1")
        tail = replytails.TAIL.get(tok)
        if (tail is not None and code[:1].isupper() and out
                and out.endswith(ETX) and not out.startswith(NOT_UNDERSTOOD[:5])):
            # the blank lines before ETX, which are the code's own and not
            # the report's: FIDELITY S6, `replytails`
            rows = out[:-1].decode("latin-1").split(SEP)
            while rows and not rows[-1].strip():
                rows.pop()
            if (tok in replytails.BODY_TAIL
                    and sum(1 for r in rows[3:] if r.strip()) > 1):
                # past the leading blank, the echo and the stamp there is
                # more than a title, and a body closes by its own count
                # (`BODY_TAIL`): 780 with every line off is its title alone
                # and closes by TAIL's
                tail = replytails.BODY_TAIL[tok]
            out = (SEP.join(rows + [""] * (tail + 1))).encode("latin-1") + ETX
        return out, note

    #: codes with no device of their own in the tables that still read one:
    #: the bench TLS-350 answered these differently for 01 and 00
    #: (2026-09-22) -- 888 per comm board, B21 per thermistor, 680
    DEVICE_READ = {"680", "888", "B21"}
    #: the WPLLD Comm Module's software and creation stamp, as 904 printed
    #: them off a 330812-001 in the bench's comm 3 (2026-09-24)
    WPLLD_COMM_SOFTWARE = ("349751-001-A", "96.02.14.11.38")
    #: console-wide codes with no setting of their own that the bench
    #: answered at 01 as at 00, body and all (2026-09-22)
    DEVICE_BLIND = {"501", "505", "517", "51F", "55D", "5BE", "5BF", "79E",
                    "7B0", "7B3", "901", "902", "904", "905", "V10"}
    #: codes the bench answers bare at device 00 whatever is stored
    NO_DEVICE_ZERO = {"536", "5E2", "881", "882", "889"}
    #: the comm port settings a port answers by its board (`_port_reply`)
    PORT_CODES = {"881", "882", "885", "886", "887", "889"}

    def _port_reply(self, tok, port, code):
        """One comm port's settings, as the bench TLS-350 answered them
        (2026-09-22): port 1 its RS-232 board, port 2 its serial satellite.

        881 and 882 print the same block, PORT SETTINGS: right under the
        stamp, the board, its UART indented, the port's RS-232 security,
        and the DTR state on the satellite board. This drew 881's parts as
        `BAUD RATE: 1200` lines under no title, and 882 its packed record.
        With no modem board 885, 886 and 887 are bare; the RS-232 board has
        no DTR and 889 is bare for it, where the satellite answered 889 with
        its DTR. Only those two boards have been seen, so a modem board keeps
        the renderer that was here. None where this has nothing to say."""
        name = self.c.comm_board_name(port)
        rs232 = name == "RS-232"
        if tok in ("885", "886", "887") and not self.c.has("modem"):
            # the bench has no modem board, so whether an RS-232 port on a
            # console that HAS one answers these is not known
            return self._frame(code), "no modem board"
        if tok == "889" and rs232:
            return self._frame(code), "not a setting of an RS-232 board"
        dtr = (self.c.values.get(f"S889{port:02d}") or "1").strip()[-1:]
        dtr = "LOW" if dtr == "0" else "HIGH"
        if tok == "889" and code[:1].isupper() and name == "S-SAT ":
            return self._raw_display(code, [
                "", f"COMM BOARD  : {port} ({name})",
                f" DTR NORMAL STATE: {dtr}"]), "DTR"
        if (tok in ("881", "882") and code[:1].isupper()
                and name in ("RS-232", "S-SAT ")):
            uart = self._uart_words(port, self._port_uart(port))
            # 536's `S` then the code: 0 is DISABLED, which is all the
            # bench has shown; an enabled port is read as printing its code
            secure = (self.c.values.get(f"S536{port:02d}") or "0").strip()
            rows = ["PORT SETTINGS:", "",
                    f"COMM BOARD  : {port} ({name})",
                    f" BAUD RATE  : {uart['baud']}",
                    f" PARITY     : {uart['parity']}",
                    f" STOP BIT   : {uart['stop']}",
                    f" DATA LENGTH: {uart['data']}",
                    "RS-232 SECURITY",
                    "CODE : " + ("DISABLED" if secure[:1] != "1"
                                 else secure[1:7])]
            if not rs232:
                rows.append(f" DTR NORMAL STATE: {dtr}")
            return self._raw_display(code, rows), "port settings"
        return None

    def _past_the_end(self, tok, n):
        """Is device `n` beyond the positions this code has?

        The card families have their own check against the cage; these are
        the numbered slots, receivers and ports, whose count is not a card's.
        `_set_family` reads 5BC and 7BD as tank codes by their letter, and
        they are a receiver's and a pressure line's."""
        if tok in self.SET_DEVICES:
            return n > self.SET_DEVICES[tok]
        if wiretables._is_receiver(tok) or tok in ("5BC", "535"):
            return n not in self.c.receivers()
        if tok == "536":
            return n > wiretables.SECURITY_PORTS
        if tok == "7BD":
            return n > self.c.capacity("plld")
        if tok in self.PORT_CODES:
            return n not in self.c.serial_positions()
        return False

    def _console_wide(self, tok):
        """A code that has one value for the console, whatever device."""
        from .console import FIELDS
        if tok in self.DEVICE_BLIND:
            return True
        return (tok[:1] in "56789V" and tok not in self.DEVICE_READ
                and not self.c.is_multi(tok) and not self.c.is_prefixed(tok)
                and (f"S{tok}00" in FIELDS or f"S{tok}01" in FIELDS))

    def _inquire(self, tok, dev, code, data=""):
        if tok == "E20":
            # one of `WAITING_INQUIRIES` that answered nothing even once a CR
            # followed it, at 00 and at 01 (2026-09-22). What data field it
            # waits for is on no page.
            return b"", "E20: no answer"
        if (dev != "00" and dev.isdigit() and not data
                and self._console_wide(tok)):
            # A console-wide setting ignores the device it is asked for: the
            # bench TLS-350 answered them at 01 exactly as at 00, in both
            # formats (2026-09-22) -- `I50601` is PERIODIC TEST WARNINGS:
            # ENABLED, `i51F01` packs its 0. This answered the frame alone,
            # or a raw stored value where a dump had left one. The echo
            # keeps the device that was asked. Not the tank, line and sensor
            # reports: the bench had every device off, so it cannot say what
            # their numbers do, and those are read as the device's own.
            dev = "00"
        if dev == "00" and not data and (
                tok in self.NO_DEVICE_ZERO
                or (tok == "611" and code[:1].islower())):
            # A port, a record or a receiver has no device 00: the bench
            # TLS-350 answered these bare in every capture, and on
            # 2026-09-22 with every port holding a value that `i53601` to
            # `i53606` read back. This drew them all once one was set. 611's
            # display form prints the all-tank method; its packed form is
            # bare as well.
            return self._frame(code), "device 00 is not one of them"
        if dev.isdigit() and int(dev) and not data and self._past_the_end(
                tok, int(dev)):
            # A position the console has not got: the bench TLS-350 answered
            # header line 5, receiver 9, port 7, comm port 3 with no board
            # and a fourth pressure line with the frame alone, in both
            # formats (2026-09-22), where this drew a blank row for each
            return self._frame(code), "no such position"
        if (tok == "77F" and code[:1].islower() and dev.isdigit()
                and 0 < int(dev) <= self.c.capacity("plld") and not data
                and not wiretables.ROW_FILTER["77F"](self.c, int(dev))):
            # no second length on this pipe: `i77F01` packs 0 on the bench
            # (2026-09-22), as `i77F00` does for every such line
            # (`_computer_aggregate`), where this packed the 351 default
            return (self._frame(code, dev + packed.hexfloat(0.0)),
                    "no second length on this pipe")
        if tok == "882" and code[:1].islower() and dev.isdigit() and int(dev):
            # a port's 882 packs its 881 record: `i88201` and `i88101` are
            # `01200117001` alike on the bench (2026-09-22), where this packed
            # nothing unless a dump had left a value
            out, note = self._inquire("881", dev, "i881" + dev, data)
            if out and b"&&" in out and not out.startswith(NOT_UNDERSTOOD[:5]):
                return self._frame(code, out[17:out.index(b"&&")].decode(
                    "latin-1")), note
        if (tok in self.PORT_CODES and dev.isdigit() and int(dev)
                and not data and int(dev) in self.c.serial_positions()):
            said = self._port_reply(tok, int(dev), code)
            if said is not None:
                return said
        if tok in SET_ONLY:
            return (self._nine(code),
                    f"{tok} is a Set with no Inquire format")
        if tok in UNDOCUMENTED_BARE:
            return self._frame(code), "known to 326.01, answered bare"
        if (dev.isdigit() and int(dev) and not data
                and tok[:1] in "234679AB" and not "500" <= tok < "520"):
            # (the device families' own codes: a console-wide 501 answers
            # `I50199` with its title, whatever the number)
            family = self._set_family(tok)
            count = self.c.capacity(family) if family else 0
            if count > 0 and int(dev) > count:
                # a device past the positions the cage gives its family: the
                # bench TLS-350 answers `I20199`, `I60205` and `I78104` -- a
                # fourth line on a three-line controller -- with a bare frame,
                # in both formats (2026-09-19, UNKNOWNS B29)
                return self._frame(code), "no such position"
        if tok in self.WITHHELD and self.c.version == self.WITHHELD[tok]:
            # The two 0.10 GPH schedule DATE codes, PLLD's 78B and WPLLD's
            # 7AA, are refused by the bench TLS-350 (326.01) where every
            # other code for a card it lacks answers bare. Refused on the
            # one version seen doing it; what withholds them is not known.
            # FIDELITY S29.
            return self._nine(code), f"{tok} withheld by software {self.c.version}"
        if tok in SETTABLE and not self._module_present(tok):
            # Reading back a setting belonging to a card that is not in the
            # cage is the same question as writing one: the console has
            # nothing to answer with. That was 9999FF here, on the reading
            # that a tool sweeping the ranges wants to skip the function;
            # the bench TLS-350 answers a bare frame (`_absent`).
            return self._absent(code), "no module fitted"
        if tok in ("537", "538"):
            # the RS-232 ETX characters per port, which this drew bare at
            # every port (FIDELITY S38)
            port = self.c.etx_port(dev)
            if port is None:
                return self._frame(code), "no such port"
            return self._etx_table(tok, port, code), "ETX characters"
        if not data and self._nothing_to_report(tok, dev):
            return self._frame(code), "no device configured"
        # any device: `I61301` answered the title as `I61300` did -- but
        # `I62F01` was bare where `I62F00` answers its title and heading
        if (tok in self.NO_LIVE_TANK
                and not self.c.tank_level):
            text = self.NO_LIVE_TANK[tok]
            if (text is None or code[:1].islower()
                    or (tok in self.NO_LIVE_TANK_BARE_ONE and dev != "00")):
                return self._frame(code), "no tank with a probe: bare"
            return self._raw_display(code, text), "no tank with a probe"
        if code[:1].isupper():
            said = self._bench_state_reply(tok, dev, code)
            if said is not None:
                return said, "the bench console's answer in this state"
        elif dev == "00" or tok in ("B21", "535"):
            said = self._bench_computer_reply(tok, dev)
            if said is not None:
                return self._frame(code, said), "the bench console's packed answer"
        if tok in self.FIXED and code[:1].isupper():
            return self._raw_display(code, self.FIXED[tok]), "the bench's own"
        if tok == "535" and code[:1].isupper():
            return self._raw_display(code, self._hangup_lines(dev)), "hangup"
        if tok == "55D" and code[:1].isupper():
            return (self._raw_display(code, self._precision_print_lines()),
                    "precision print")
        if tok == "7C3" and self.c.has("probe"):
            # its own table and packing whatever is stored, since the
            # stored figure is whole gallons and not the field's own form
            tanks = ([int(dev)] if dev.isdigit() and int(dev)
                     else list(range(1, self.c.capacity("probe") + 1)))
            if code[:1].islower():
                return self._frame(code, "".join(
                    f"{n:02d}" + packed.hexfloat(units.out(
                        "volume", float(blankrows.max_volume(self.c, n)),
                        units.system(self.c)))
                    for n in tanks)), "max volume"
            return (self._frame(code, SEP.join(
                blankrows.table(self.c, "7C3", tanks))), "max volume")
        if not self.c.licensed("bir") and tok in self.NO_BIR:
            text = self.NO_BIR[tok]
            if tok == "7B1" and code[:1].islower():
                # the meter map, packed, with no BIR key: `+99`, once a CR
                # followed it -- it was filed as answering nothing at all,
                # and it was waiting for one (`WAITING_INQUIRIES`,
                # 2026-09-22; FIDELITY S36)
                return self._frame(code, "+99"), "the meter map, packed"
            if text is None or code[:1].islower():
                return self._frame(code), "no BIR key: a bare frame"
            return self._raw_display(code, text), "no BIR key"
        if tok == "5FA":
            return self._glass_reply(code), "the display"
        if tok in NUMBERED_SLOTS and (dev == "00" or not dev.isdigit()):
            # All four at once is a bare frame in BOTH formats, whatever is
            # programmed: with ACME FUEL on line 1 and SECOND LINE on line 2
            # the bench TLS-350 answered `I50300` and `i50300` with the echo
            # and the stamp and nothing else, while `i50301` carried the
            # twenty characters (2026-09-19, `transcripts/header503b`). The
            # packed form had been sending every line's text run together,
            # which was only ever right because the lines were blank.
            return self._frame(code), f"{tok}: device 00 is bare"
        if tok == "503" and code[:1].isupper():
            # The print header lines. The bench TLS-350 (2026-09-18) answers
            # `I50301` with the one line `# 1:HEADER ONE` and the header's
            # width in blanks after it. p.158's sample line had been read
            # as a title and printed over every answer.
            n = int(dev)
            return (self._frame(code, f"# {n}:{self.c.text('503', n):<20.20s}"),
                    "print header line")
        if tok == "61E" and not self._mass_density_on():
            # The bench TLS-350 answers I61E01 with a bare frame while
            # Mass/Density is disabled, and with the table once it is on.
            return self._frame(code), "mass/density disabled"
        for family in EXTRA_REPORTS:
            answered = family(self, tok, dev, code, data)
            if answered is not None:
                return answered
        if tok in SERVICE_NOTICE:
            return self._service_notice_read(tok, code)
        if tok == "101":
            recs = self.c.compute_alarms()
            note = (f"{len(recs)} alarm(s)" if recs
                    else "all functions normal")
            if code[0].isupper():
                # The bench TLS-350, 2026-09-18: a quiet console answers the
                # blank line and `  ALL FUNCTIONS NORMAL  ` and stops; one
                # with something standing answers the blank line, each
                # message hard against the margin -- a device's padded to
                # 23, `Q 1:SETUP DATA WARNING ` and `T 1:PROBE OUT          `,
                # a system one bare, `PAPER OUT` -- and a blank line after.
                shown = [a["screen"] for a in describe_alarms(recs)]
                if not shown:
                    rows = ["SYSTEM STATUS REPORT", "",
                            "  ALL FUNCTIONS NORMAL  "]
                else:
                    rows = ["SYSTEM STATUS REPORT", ""]
                    rows += [s.ljust(23) if re.match(r"^[A-Za-z] ?\d+:", s)
                             else s for s in shown]
                    rows.append("")
                return self._frame(code, SEP.join(rows)), note
            return (self._frame(code, "".join(recs) if recs else "000000"),
                    note)
        if tok in ("201", "21A"):
            # In-Tank Inventory Report, and the same report with the 90/95%
            # ullage the site programmed at S564
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                return (self._frame(code, self._inventory_text(tanks, tok)),
                        "inventory report")
            body = "".join(self._inventory_data(t, tok) for t in tanks)
            return self._frame(code, body), "inventory report"
        if tok == "205":
            # In-Tank Status Report: the alarms standing against each tank
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            active = {}
            for record in self.c.compute_alarms():
                if record[:2] == "02":
                    active.setdefault(int(record[4:6]), []).append(record[2:4])
            # the tanks with a probe reporting, and a tank switched on with
            # no probe once it has an alarm standing: the bench TLS-350's
            # tank 1, on with no probe, listed its SETUP DATA WARNING and
            # PROBE OUT here (2026-09-24), and out of its cold start, with
            # tanks on and nothing posted, listed none (`cap_tank1on`)
            on = set(self.c.configured("601", self.c.capacity("probe")))
            tanks = sorted(set(self._tanks("00"))
                           | {t for t in on if active.get(t)})
            if dev != "00":
                tanks = [t for t in tanks if dev.isdigit() and t == int(dev)]
            if code[0].isupper():
                # a blank line under the heading before the first tank, on
                # the bench TLS-350 (2026-09-24)
                rows = ["TANK   PRODUCT                 STATUS"] + (
                    [""] if tanks else [])
                for i, tank in enumerate(tanks):
                    if i:
                        # and one between tanks: a version 23 site with four
                        # (2026-10-07); the bench had one
                        rows.append("")
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    names = [a["description"].upper()
                             for a in describe_alarms(
                        [f"02{nn}{tank:02d}" for nn in active.get(tank, [])])]
                    # each status in its nineteen, `SETUP DATA WARNING ` and
                    # `PROBE OUT          ` on the bench (2026-09-24)
                    rows.append(f"{tank:3d}    {label:<24s}"
                                + f"{names[0] if names else 'NORMAL':<19s}")
                    for extra in names[1:]:
                        rows.append(" " * 31 + f"{extra:<19s}")
                return self._frame(code, SEP.join(rows)), "tank status"
            body = "".join(f"{t:02d}{len(active.get(t, [])):02X}"
                           + "".join(active.get(t, [])) for t in tanks)
            return self._frame(code, body), "tank status"
        if tok == "206":
            # In-Tank Alarm History Report. The bench TLS-350 with real rows
            # behind it (2026-09-19, `transcripts/p206b.log`, `hist206.log`):
            #
            #   TANK 2  PREMIUM UNLEADED
            #        SETUP DATA WARNING       JAN 17, 2006  9:25 AM
            #                                 JAN 17, 2006  9:24 AM
            #                                 JAN 17, 2006  9:20 AM
            #        PROBE OUT                JAN 17, 2006  9:25 AM
            #
            # grouped by alarm TYPE, the type named once, the THREE newest
            # of each, types in their numbers' order -- and the CLEARs are
            # not entries: a cycle whose alarm and clear fell in one minute
            # left that minute in the list once, with an older alarm's
            # minute still under it. A tank is listed while 601 has it
            # switched on, probe or no probe, and not at all when it is off.
            # This listed every record in log order, each with its own
            # description, for the tanks with a probe reporting.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            on = list(self.c.configured("601", self.c.capacity("probe")))
            if dev == "00":
                tanks = on
            else:
                tanks = [int(dev)] if int(dev) in on else []
            log = [r for r in self.c.alarm_history("02")
                   if r.get("state", "02") != "01"]

            def grouped(tank):
                """[(nn, [record, ...])] -- three newest of each type."""
                out = {}
                for r in log:
                    if int(r["tt"]) != tank:
                        continue
                    kept = out.setdefault(r["nn"], [])
                    if len(kept) < 3:
                        kept.append(r)
                return sorted(out.items())
            if code[0].isupper():
                rows = ["TANK ALARM HISTORY"]
                for tank in tanks:
                    blocks = grouped(tank)
                    if not blocks:
                        continue
                    label = self.c.text("602", tank) or ""
                    # the label in its twenty, and a blank line before each
                    # type's group: the bench TLS-350 on 2026-09-24,
                    # `TANK 1  REGULAR UNLEADED    ` then SETUP DATA WARNING
                    # and PROBE OUT each under a blank line
                    # and a blank line before the tank: a version 23 site
                    # with four tanks in alarm history (2026-10-07)
                    rows.append("")
                    rows.append(f"TANK {tank}  {label:<20.20s}")
                    for nn, records in blocks:
                        rows.append("")
                        desc = describe_alarms(
                            [f"02{nn}{tank:02d}"])[0]["description"]
                        for i, r in enumerate(records):
                            head = f"{desc.upper():<25s}" if not i else " " * 25
                            rows.append("     " + head + _when(r["at"]))
                # The title and nothing under it, which is what
                # `tests/console_capture/raw/I20600.bin` is: a real console
                # with no tank alarms answering `I20600` with the header
                # block, TANK ALARM HISTORY and its trailing blank. The
                # marker that used to stand here was W13's invented phrase.
                # See FIDELITY S18.
                return self._frame(code, SEP.join(rows)), "tank alarm history"
            body = ""
            for tank in tanks:
                mine = [r for _nn, records in grouped(tank) for r in records]
                body += f"{tank:02d}{len(mine):02d}"
                body += "".join(r["at"] + alarm_history_code(r["nn"])
                                for r in mine)
            return self._frame(code, body), "tank alarm history"
        if tok in ("C03", "C04"):
            # BIR shift reconciliation. C03 is the "Row" report and C04 the
            # "Column" one, and they are two LAYOUTS and not two names for one
            # -- they answered with identical text until this was noticed.
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            tanks = sorted(self.c.tank_level)
            previous = dev[-2:] == "02"
            if tok == "C04":
                text = self.c.bir.column_report(tanks, kind="shift",
                                                previous=previous)
            else:
                text = self.c.bir.report(tanks, previous=previous)
            if code[0].isupper():
                return (self._frame(code, "\n" + text + "\n"),
                        "shift reconciliation")
            return self._frame(code, text), "shift reconciliation"
        if tok in recon.RECON:
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            spec = recon.RECON[tok]
            tanks = sorted(self.c.tank_level)
            previous = recon.previous_wanted(tok, data)
            if previous is None:
                return self._nine(code), "REJECTED: no such report type"
            bir = self.c.bir
            kind, shape, multi = spec["kind"], spec["shape"], spec["multi"]
            if shape == "row":
                text = bir.row_report(tanks, kind, previous, multi)
            elif shape == "column":
                text = bir.column_report(tanks, kind, previous,
                                         threshold=(kind != "daily"))
            elif shape == "book":
                text = bir.book_report(tanks, kind, previous, multi)
            elif shape == "analysis":
                text = bir.analysis_report(tanks, kind, previous, multi)
            else:
                if not code[0].isupper():
                    return (self._frame(code, self._recon_history_body(dev)),
                            spec["note"])
                text = self._recon_history(dev, data)
            if code[0].isupper():
                return self._frame(code, text), spec["note"]
            return (self._frame(code, self._recon_body(tok, spec, tanks,
                                                       previous)),
                    spec["note"])
        if tok in ("251", "A55"):
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.tank_level))
            if tok == "251":
                if code[0].isupper():
                    text = "\n" + self.c.csld.results_table(tanks) + "\n"
                    return self._frame(code, text), "CSLD results"
                body = "".join(f"{t:02d}{self.c.csld.result_code(t)}"
                               for t in tanks)
                return self._frame(code, body), "CSLD results"
            # A55: CSLD Diagnostics, Leak Test Status
            rows = ["CSLD DIAGNOSTICS: LEAK TEST STATUS", "",
                    wiretables.heading("A55")]
            body = []
            for tank in tanks:
                run = self.c.leaks.active("tank", tank)
                idle = self.c.csld.idle_from.get(tank)
                now = time.mktime(self.c.now())
                if run:
                    state, minutes = "02", run.elapsed(now) * 60.0
                elif idle:
                    state, minutes = "05", (now - idle) / 60.0
                else:
                    state, minutes = "00", 0.0
                names = {"00": "NO TEST", "02": "TEST IN PROGRESS",
                         "05": "TEST PRE-DELAY"}
                # p.515 puts the status at 18 and the duration right
                # against 34, to one decimal
                rows.append(wiretables.row("A55",
                                           [tank, names[state], minutes]))
                body.append(f"{tank:02d}{state}"
                            + packed.hexfloat(minutes))
            if code[0].isupper():
                return (self._frame(code, "\n" + "\n".join(rows) + "\n"),
                        "CSLD leak test status")
            return self._frame(code, "".join(body)), "CSLD leak test status"
        if tok in AT_COMMANDS:
            return self._at_command(tok, dev, code)
        if tok in ("55E", "132", "642"):
            return self._fiscal_and_filter(tok, dev, code)
        if tok == "61F":
            # "Set Delivery Density ... t - Delivery Type (0=next, 1=last)".
            # The delivery type is part of the ADDRESS on an inquiry and part
            # of the DATA on a set, which is why this code needs its own
            # branch either way. See FIDELITY O13.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            if not data:
                # No delivery type, no report: the bench TLS-350 waits for
                # one (`WAITING_INQUIRIES`) and answered a lone CR with the
                # frame alone (2026-09-22), where `I61F010` draws the table.
                # This read a missing type as NEXT.
                return self._frame(code), "no delivery type given"
            which = data[:1]
            tanks = self._tanks(dev)
            if which not in ("0", "1"):
                # a `?` a tank, in either format: `I61F00` and a line feed,
                # a space, a tab, `X` or `2` all answered `????` on the
                # bench's four tanks, and `I61F01X` / `i61F01X` one
                # (2026-09-25, `lf.jsonl`). This answered 9999FF.
                return (self._refused(code, "?" * len(tanks)),
                        "REJECTED: delivery type is 0 or 1")
            word = "NEXT" if which == "0" else "LAST"
            if code[0].isupper():
                # The bench TLS-350 (2026-09-18) answers `I61F010` under the
                # echo `I61F01` -- the delivery type is NOT echoed, whatever
                # p.273's "<SOH> I61FTT0" draws -- and sets the table out as
                # its 61E: DENSITY at 31, the tank number ending at column 1,
                # the label at 7 and the value ending under the Y at 37.
                rows = [f"{word} DELIVERY DENSITY", "",
                        "TANK   PRODUCT LABEL           DENSITY"]
                for tank in tanks:
                    label = self.c.text("602", tank) or ""
                    got = self.c.delivery_density_value(tank, which)
                    rows.append(f"{tank:2d}     {label:<20.20s}{got:11.4f}")
                # and one blank line to close it, as 61E's does: the table's
                # first read with a real type, 2026-09-25 (`lf.jsonl`). It
                # is not in `replytails`, whose count would also close the
                # frame alone and the `?` refusal, which end otherwise.
                return (self._frame(code, SEP.join(rows + [""])),
                        f"{word.lower()} delivery density")
            body = "".join(
                f"{tank:02d}{which}"
                + packed.hexfloat(self.c.delivery_density_value(tank, which))
                for tank in tanks)
            return self._frame(code, body), f"{word.lower()} delivery density"
        if tok in ("B91", "B93", "B94"):
            # AccuChart Diagnostics, Status and Calibration History
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            chart = self.c.accuchart
            if tok == "B91":
                rows, note = chart.diagnostics_rows(tanks), "accuchart diagnostics"
            elif tok == "B93":
                rows, note = chart.status_rows(tanks), "accuchart status"
            else:
                rows, note = chart.history_rows(tanks), "accuchart history"
            if code[0].isupper():
                return self._frame(code, SEP.join(rows)), note
            return self._frame(code, self._accu_record(tok, tanks)), note
        if tok == "221":
            # Ticketed Delivery Report, current period or previous
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.tank_level))
            if not self.c.licensed("bir"):
                # Tickets are BIR's. A version 23 site with no BIR key
                # answered I22100 with the frame and nothing in it
                # (2026-10-07), not a refusal.
                return self._frame(code, ""), "ticketed delivery report"
            text = self.c.deliveries.ticketed_report(tanks)
            if code[0].isupper():
                if not tanks:
                    # No tank reporting, no report: the bench TLS-350 with
                    # every tank switched off answered I22100 with the frame
                    # and the stamp alone (site snapshot, 2026-09-22), the
                    # same as I201. This printed the period and VOLUMES ARE
                    # heading over nothing. CLOSED S37.
                    return self._frame(code, ""), "ticketed delivery report"
                return (self._frame(code, "\n" + text + "\n"),
                        "ticketed delivery report")
            return self._frame(code, text), "ticketed delivery report"
        if tok == "102":
            # System Configuration Report: what is in the cage, slot by slot
            if code[0].isupper():
                return (self._frame(code, SEP.join(
                    self.c.configuration_lines())), "configuration")
            return (self._frame(code, self.c.configuration_records()),
                    "configuration")
        if tok in ("113", "114", "115", "121"):
            # Three reports that look like one. 113 is what is standing now,
            # 114 is what has gone away, 115 is what Maintenance Tracker has
            # not had acknowledged -- and only 114 carries the state byte, so
            # its record is twenty characters where the other two are
            # eighteen. See alarmreports.py.
            #
            # 121 is a fourth, undocumented, and it prints as 113 does --
            # see the note beside `REPORTS` above -- but packs a record of
            # its own (`Console.alarm_121_records`, FIDELITY S38)
            if code[0] == "i" and tok == "121":
                return (self._frame(code, self.c.alarm_121_records()),
                        "alarm report")
            tok = "113" if tok == "121" else tok
            state = tok in alarmreports.HAS_STATE
            records = {"113": self.c.active_alarm_records,
                       "114": self.c.cleared_alarm_records,
                       "115": self.c.unacknowledged_alarm_records}[tok]()
            title = {"113": "ACTIVE ALARMS REPORT",
                     "114": "CLEARED ALARMS REPORT",
                     "115": "MAINTENANCE TRACKER UNACKNOWLEDGED ALARM REPORT"
                     }[tok]
            if tok == "115" and not self.c.has("mt"):
                return self._absent(code), "no Maintenance Tracker fitted"
            if code[0].isupper():
                rows = self.c.alarm_report_lines(records, title, state)
                return self._frame(code, SEP.join(rows)), "alarm report"
            return (self._frame(code, self.c.alarm_report_records(
                records, state, tok in alarmreports.HEADERS)), "alarm report")
        if tok in ("116", "11A"):
            # SAME Function Type, "Service Report History", and incompatible:
            # 116 has station headers, a ten character ID and a five character
            # code; 11A has no headers, a six character ID and a four
            # character NUMERIC one. 116 went obsolete at V27 and 11A replaced
            # it, and they are not drop-in for each other.
            wide, wide_code, numeric = alarmreports.SERVICE_WIDTHS[tok]
            entries = self.c.service_log()
            if code[0].isupper():
                # p.56 and p.59's own columns. 11A carries two LABEL
                # columns this console has nothing for -- the technician's
                # name and the service description -- so they stand empty
                # rather than being filled with something invented, and the
                # ID and CODE sit where the page puts them.
                rows = ["SERVICE REPORT", wiretables.heading(tok)]
                if not entries:
                    # And this report DOES mark an empty one. `I11600.bin` is
                    # a real console printing the single word under the
                    # heading, where its alarm-history neighbours print
                    # nothing at all. See FIDELITY S18.
                    rows.append("NONE")
                rows += alarmreports.service_rows(tok, entries)
                return self._frame(code, SEP.join(rows)), "service history"
            body = (self.c.station_header_field()
                    if tok in alarmreports.HEADERS else "")
            body += alarmreports.count_field(tok, len(entries))
            for e in entries:
                body += (e["at"] + f"{e['id']:<{wide}.{wide}s}"
                         + f"{e['code']:<{wide_code}.{wide_code}s}")
            return self._frame(code, body), "service history"
        if tok == "119":
            asked = (data or "").strip()
            start = asked[0:6] if len(asked) >= 12 else None
            end = asked[6:12] if len(asked) >= 12 else None
            entries = self.c.maintenance_log(start, end)
            if code[0].isupper():
                # p.57: TYPE at 0, the stamp at 20 and DESCRIPTION at 45
                rows = ["MAINTENANCE HISTORY", wiretables.heading("119")]
                for e in entries:
                    stamp = time.strptime(e["at"], "%y%m%d%H%M")
                    what = alarmreports.MAINTENANCE_TYPE.get(e["type"], "")
                    rows.append(f"{what:<20.20s}"
                                f"{clock_words(time.mktime(stamp)):25s}"
                                f"{self._maintenance_words(e)}")
                return self._frame(code, SEP.join(rows)), "maintenance history"
            body = alarmreports.count_field("119", len(entries))
            for e in entries:
                body += e["at"] + e["type"] + f"{e['data']:0>6.6s}"
            return self._frame(code, body), "maintenance history"
        # 11B
        if tok == "11B":
            sessions = list(self.c.service_sessions)
            running = bool(sessions) and sessions[0].get("end") is None
            if code[0].isupper():
                # p.60 puts END TIME at 26, not 24
                rows = ["SERVICE NOTICE SESSION REPORT",
                        wiretables.heading("11B")]
                for one in sessions:
                    began = clock_words(one["start"])
                    ended = ("IN PROGRESS" if one.get("end") is None
                             else clock_words(one["end"]))
                    rows.append(f"{began:26s}{ended}")
                return self._frame(code, SEP.join(rows)), "service sessions"
            body = "1" if running else "0"
            body += (time.strftime("%y%m%d%H%M",
                                   time.localtime(sessions[0]["start"]))
                     if running else "0" * 10)
            body += alarmreports.count_field("11B", len(sessions))
            for one in sessions:
                body += time.strftime("%y%m%d%H%M",
                                      time.localtime(one["start"]))
                body += (time.strftime("%y%m%d%H%M",
                                       time.localtime(one["end"]))
                         if one.get("end") else "0" * 10)
            return self._frame(code, body), "service sessions"
        if tok in ("111", "112"):
            # Priority and Non-Priority Alarm History
            priority = tok == "111"
            title = ("PRIORITY ALARM HISTORY" if priority
                     else "NON-PRIORITY ALARM HISTORY")
            if code[0].isupper():
                rows = [title] + self.c.alarm_state_lines(priority)
                return self._frame(code, SEP.join(rows)), "alarm history"
            return (self._frame(code, self.c.alarm_state_records(priority)),
                    "alarm history")
        if tok in ("A51", "A52", "A53", "A54"):
            # The CSLD diagnostics tables the guide asks a tech to collect
            if not self.c.licensed("csld"):
                return self._absent(code), "CSLD not installed"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    rows += self.c.csld_table_lines(tok, tank)
                return self._frame(code, SEP.join(rows)), "CSLD diagnostics"
            body = "".join(self.c.csld_table_records(tok, t) for t in tanks)
            return self._frame(code, body), "CSLD diagnostics"
        if tok == "B71":
            # Pump Sensor Diagnostic
            if not self.c.has("pump"):
                return self._absent(code), "no pump sense module fitted"
            devices = self._devices_of("pump", dev)
            if code[0].isupper():
                rows = ["PUMP SENSOR DIAGNOSTIC",
                        "PUMP  TANK  STATE"]
                for pump in devices:
                    rows.append(f"{pump:4d}{self.c.pump_tank(pump):6d}"
                                f"  {self.c.pump_state(pump)}")
                return self._frame(code, SEP.join(rows)), "pump diagnostic"
            body = "".join(f"{p:02d}{self.c.pump_tank(p):02d}"
                           + ("01" if self.c.pump_state(p) == "ON" else "00")
                           for p in devices)
            return self._frame(code, body), "pump diagnostic"
        if tok in ("780", "7A0"):
            # "Computer format is not supported for this command"
            kind = "plld" if tok == "780" else "wplld"
            if not self.c.has(kind):
                return self._absent(code), f"no {kind} module fitted"
            if not code[0].isupper():
                # "Computer format is not supported for this command", and
                # the bench TLS-350 answers `i78000` with a bare frame, not
                # 9999FF, in every capture (2026-09-18 and 19)
                return self._frame(code), "display format only"
            lines = self.c.line_setup_lines(kind, self._devices_of(kind, dev))
            return self._frame(code, SEP.join(lines)), "line leak setup"
        if tok == "204":
            # In-Tank Shift Inventory Report: the Last-Shift Inventory of
            # System Setup's shift start times (S502, `shifts.Shifts`), not
            # BIR's shifts. A version 23 site with no BIR key answered it
            # (2026-10-07); this answered nothing without the key.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            shifts = self._shift_records()
            if not tanks or not shifts:
                # nothing to report, so the frame and nothing in it: the
                # bench TLS-350 with no tank reporting, in every capture
                return self._frame(code, ""), "shift inventory"
            if code[0].isupper():
                return (self._frame(code, self._shift_report(tanks, shifts)),
                        "shift inventory")
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                for shift, record in shifts:
                    start, end, total, _dlv = self._shift_gauges(record, tank)
                    body += f"{tank:02d}{pcode}{shift:02d}0D"
                    body += packed.hexfloats(start + end + [total])
            return self._frame(code, body), "shift inventory"
        if tok == "207":
            # In-Tank Leak Test History Report
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                return (self._frame(code, self._leak_history_display(tanks)),
                        "leak test history")
            body = ""
            for tank in tanks:
                records = self.c.leak_history_records(tank)
                body += f"{tank:02d}{len(records):02X}"
                for kind_code, number, result in records:
                    full = self.c.full_volume(tank) or 0.0
                    pct = (result.percent if result.percent is not None
                           else (result.volume / full * 100.0) if full
                           else 0.0)
                    body += (kind_code + f"{number:02d}"
                             + TEST_TYPE_CODE.get(result.rate_key, "00")
                             + time.strftime("%y%m%d%H%M",
                                             time.localtime(result.started)))
                    for value in (result.hours, result.volume, pct):
                        body += packed.hexfloat(value)
            return self._frame(code, body), "leak test history"
        if tok in ("20A", "20B"):
            # HRM and BIR Adjusted Delivery Reports, which are the delivery
            # the gauge saw plus whatever was dispensed during it
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            # An adjusted delivery is BIR's: a version 23 site with no BIR
            # key printed both reports' headings over nothing at all
            # (2026-10-07), where this adjusted every delivery the gauge saw.
            adjusted = self.c.licensed("bir")

            def adjusted_records(tank):
                return [r for r in (self.c.deliveries.records.get(tank)
                                    or []) if r.end] if adjusted else []
            if code[0].isupper():
                return (self._frame(code, self._adjusted_delivery_report(
                    tok, tanks, adjusted_records)), "adjusted delivery")
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                records = adjusted_records(tank)
                body += f"{tank:02d}{pcode}00{len(records):02d}"
                for record in records:
                    values = [record.amount, record.tc_amount, record.sold,
                              record.amount + record.sold,
                              record.tc_amount + record.sold]
                    body += time.strftime("%y%m%d%H%M",
                                          time.localtime(record.start["at"]))
                    body += packed.hexfloats(values)
            return self._frame(code, body), "adjusted delivery"
        if tok == "A01":
            # "7.4.2 IN-TANK DIAGNOSTIC REPORTS", the first of them: what the
            # probe IS, as against what it is reading. The display format's
            # columns are the manual's own, and its example puts the tank and
            # its product label on the same line as the probe's five figures.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                # p.489, off the word boxes. The header names only the five
                # probe columns and is INDENTED past the tank and its label:
                # TYPE at 31, CODE at 38, LENGTH at 45, SERIAL NO. at 54 and
                # D/CODE at 66. There is no `TANK PRODUCT LABEL` over the
                # first two columns -- this console invented one -- and no
                # title line above it either, the block opening on the header
                # the way 201's and 21A's do.
                #
                # The row is `TANK  1  REGULAR UNLEADED`, not a bare number:
                # the word TANK at 0, the number right against 7, the label
                # at 9. Every one of the seven columns was in the wrong place.
                rows = [f"{'':31}{'TYPE':<7}{'CODE':<7}{'LENGTH':<9}"
                        f"{'SERIAL NO.':<12}D/CODE"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    row = (f"TANK {tank:2d}  {label:<22.22s}"
                           f"{self.c.probe_type_word(tank):<7s}"
                           f"{self.c.probe_circuit_code(tank):<7s}"
                           f"{self.c.probe_length(tank):>6.2f}")
                    # the last two run to their own right edges, 62 and 71
                    row += f"{self.c.probe_serial(tank):>11s}"
                    row += f"{self.c.probe_date_code(tank):>9s}"
                    rows.append(row)
                return self._frame(code, SEP.join(rows)), "probe type and serial"
            # "TTpPPKKKKFFFFFFFFSSSSSScccc", once per tank
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         f"{self.c.probe_circuit_code(tank)}"
                         + packed.hexfloat(self.c.probe_length(tank))
                         + f"{self.c.probe_serial(tank)}"
                         f"{self.c.probe_date_code(tank)}")
            return self._frame(code, body), "probe type and serial"
        if tok in ("A02", "A03", "A04", "A05", "A06"):
            # The four calibration reports and the ratios drawn off them.
            # One shape between them: a line naming the tank, its probe and
            # what is being shown, and then the numbers eight to a row. The
            # only differences are the title and where the numbers come from.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            title = {"A02": "FACTORY DRYS", "A03": "FACTORY WETS",
                     "A04": "UPDATED DRYS", "A05": "UPDATED WETS",
                     "A06": "SENSITIVITY RATIOS"}[tok]
            wet = tok in ("A03", "A05")
            updated = tok in ("A04", "A05")

            def values_of(tank):
                if tok == "A06":
                    return self.c.probe_ratios(tank)
                return self.c.probe_calibration(tank, wet=wet, updated=updated)

            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    word = self.c.probe_type_word(tank)
                    values = values_of(tank)
                    if word == "MAG" and tok in ("A02", "A03"):
                        # "MAG    GRADIENT= 178.1400" is the whole of
                        # that line, and p.490 sets GRADIENT= at 38 with
                        # its figure right against 56
                        rows.append(self._probe_head(
                            tank, f"GRADIENT={values[0]:9.4f}"))
                        continue
                    head = self._probe_head(tank)
                    if not values:
                        # a Mag probe has no updated calibration and no
                        # ratios: the example prints the tank and stops
                        rows.append(head)
                        continue
                    rows.append(f"{head} {title}")
                    for i in range(0, len(values), 8):
                        rows.append(" ".join(f"{v:8.3f}"
                                             for v in values[i:i + 8]))
                return self._frame(code, SEP.join(rows)), title.lower()
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         + packed.hexfloats(values_of(tank)))
            return self._frame(code, body), title.lower()
        if tok in ("A10", "A11", "A12", "A13"):
            # The same channels through four windows: one sample, an average
            # of five, an average of twenty (forty on a CAP), and the long
            # term one. "TTpPPSSSSNNFFFFFFFF", where SSSS is the running
            # sample number on A10 and A13 and the width of the average on
            # A11 and A12.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            which = {"A10": "last", "A11": "fast",
                     "A12": "standard", "A13": "long"}[tok]
            tanks = self._tanks(dev)

            def buffer_of(tank):
                window = self.c.probe_window(tank, which)
                samples = window if which in ("fast", "standard") else 1
                return window, self.c.probe_buffer(
                    tank, samples, longterm=(which == "long"))

            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    window, values = buffer_of(tank)
                    rows.append(self._probe_head(
                        tank, f"NUMBER OF SAMPLES={window:5d}"))
                    for i in range(0, len(values), 8):
                        rows.append(" ".join(f"{v:8.3f}"
                                             for v in values[i:i + 8]))
                return self._frame(code, SEP.join(rows)), "probe buffers"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                window, values = buffer_of(tank)
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         f"{window & 0xFFFF:04X}"
                         + packed.hexfloats(values))
            return self._frame(code, body), "probe buffers"
        if tok in ISD_CONTROL or tok in ISD_READ_ONLY or tok in ISD_BUFFERS:
            display = code[0].isupper()
            if tok in ISD_BUFFERS:
                if not self._vp_full_control():
                    return (self._absent(code),
                            "needs PMC and full vapor processor control")
                if tok == "V81":
                    samples = self.c.hydrocarbon_history()
                    if display:
                        rows = ["HYDROCARBON SENSOR DIAGNOSTIC",
                                "DATE/TIME              READING %"]
                        for at, percent in samples:
                            rows.append(f"{self._isd_stamp(at, True):22s}"
                                        f"{percent:.3f}")
                        return self._frame(code, SEP.join(rows)), "hc report"
                    body = f"{len(samples):04d}"
                    for at, percent in samples:
                        body += f"{int(at):08X}" + packed.hexfloat(percent)
                    return self._frame(code, body), "hc report"
                # V80, the vapour processor's own cycles
                polisher = (self.c.values.get("SV4000") or "00") == isd.POLISHER
                cycles = self.c.vp_cycles[-20:]
                if display:
                    if polisher:
                        rows = ["VAPOR POLISHER",
                                "            VALVE EVENT PRESSURE",
                                'DATE-TIME    "WC EVENT   CODE']
                        for cy in reversed(cycles):
                            rows.append(f"{self._isd_stamp(cy['at']):18s}"
                                        f"{cy['on_psi']:7.3f} "
                                        f"{'OPEN' if cy['on'] else 'CLOSE':5s}"
                                        f" {cy['event']}")
                    else:
                        rows = ["VAPOR PROCESSOR",
                                "          ELAPSED PRESSURE INCHES H2O RUNTIME",
                                "DATE-TIME  ON MINUTES  ON      OFF     FAULT"]
                        for cy in reversed(cycles):
                            rows.append(f"{self._isd_stamp(cy['at']):18s}"
                                        f"{cy['minutes']:7.2f} "
                                        f"{cy['on_psi']:7.3f} "
                                        f"{cy['off_psi']:7.3f} "
                                        f"{'YES' if cy['fault'] else 'NO'}")
                    return self._frame(code, SEP.join(rows)), "vapor processor"
                body = f"{len(cycles):04d}"
                for cy in cycles:
                    body += (f"{int(cy['at']):08X}03"
                             + packed.hexfloat(cy["minutes"])
                             + packed.hexfloat(cy["on_psi"])
                             + packed.hexfloat(cy["off_psi"])
                             + ("1" if cy["fault"] else "0"))
                return self._frame(code, body), "vapor processor"
            if tok == "V83":
                if not self.c.licensed("isd"):
                    return self._absent(code), "needs the ISD software module"
                # "IV8300CCNNIII": category, sensor number, and how many
                # records of each, "[001-255]".
                asked = (data or "").strip()
                want = asked[0:2] or "00"
                number = asked[2:4] or "00"
                try:
                    most = int(asked[4:7]) if len(asked) >= 7 else 1
                except ValueError:
                    return self._nine(code), "REJECTED: bad record count"
                if not 1 <= most <= 255:
                    return self._nine(code), "REJECTED: record count out of range"
                rows = []
                for ident, name, serial, _used in self._isd_sensors():
                    if number != "00" and ident != number:
                        continue
                    for at, slope, offset, ok in self.c.calibration_history(
                            "smart", int(ident), most):
                        rows.append((ident, name, serial, at, slope,
                                     offset, ok))
                if display:
                    out = ["SMART SENSOR CALIBRATION HISTORY",
                           f"{'DATE':18s}{'NUMBER':7s}{'TYPE':12s}"
                           f"{'S/N':11s}SLOPE OFFSET P/F"]
                    for ident, name, serial, at, slope, offset, ok in rows:
                        short = isd.CALIBRATION_TYPE.get(name, name)
                        out.append(f"{self._isd_stamp(at):18s}{ident:<7s}"
                                   f"{short:<12.12s}{serial:<11s}"
                                   f"{slope:5.3f} {offset:6.3f} "
                                   f"{'P' if ok else 'F'}")
                    if want in ("00", "02"):
                        out += ["MODBUS SENSOR CALIBRATION HISTORY", "NONE"]
                    if want in ("00", "03"):
                        out += ["SERIAL SENSOR CALIBRATION HISTORY", "NONE"]
                    return self._frame(code, SEP.join(out)), "calibration history"
                body = ""
                for ident, _n, _s, at, slope, offset, ok in rows:
                    body += (f"01{ident}{most:03d}"
                             + time.strftime("%y%m%d%H%M", time.localtime(at))
                             + packed.hexfloat(slope) + packed.hexfloat(offset)
                             + ("1" if ok else "0"))
                return self._frame(code, body), "calibration history"
            if tok in isd.DETAIL:
                if not self.c.licensed("isd"):
                    return self._absent(code), "needs the ISD software module"
                asked = (data or "").strip()
                width = self._isd_columns(tok, asked)
                if width is None:
                    return (self._nine(code),
                            "REJECTED: column count out of range")
                days = [self._isd_day_record(d)
                        for d in self._isd_days(tok, asked)]
                if display:
                    rows = self._isd_status_lines(
                        None, False, "ISD DAILY REPORT DETAILS")
                    rows.append(isd.DETAIL_CODES)
                    head = (f"{'':6s}{'ISD':7s}{'ISD':5s}"
                            f"{'Gross':6s}{'Dgrd':6s}{'Max':6s}{'Min':6s}"
                            f"{'Leak':5s}{'StgI':5s}{'Prcsr':6s}")
                    for fp, hose in self._isd_hoses():
                        head += f"{'FP' + fp + '/' + hose:9s}"
                    rows.append(f"{'Date':6s}{'Status':7s}{'%Up':5s}"
                                + head[18:])
                    for rec in days:
                        line = (f"{time.strftime('%m/%d', time.localtime(rec['at'])):6s}"
                                f"{isd.STATUS[rec['evr']][:1]:7s}"
                                f"{str(rec['up']) + '%':5s}"
                                f"{self._isd_cell(rec['gross']):6s}"
                                f"{self._isd_cell(rec['degrade']):6s}"
                                f"{rec['max']:<6.1f}{rec['min']:<6.1f}"
                                f"{self._isd_cell(rec['leak']):5s}"
                                f"{self._isd_passed(rec['stage1']):5s}"
                                f"{self._isd_passed(rec['processor']):6s}")
                        for _fp, _hose, status, value in rec["hoses"]:
                            line += f"{self._isd_cell((status, value)):9s}"
                        rows.append(line[:width])
                    rows.append("-" * min(width, 79))
                    rows.append(isd.DETAIL_FOOTER)
                    # "CCC - Number of columns": it is the width of the whole
                    # printout, not of the data rows alone
                    return (self._frame(code, SEP.join(r[:width]
                                                       for r in rows)),
                            "isd detail")
                body = f"{len(days):04X}"
                for rec in days:
                    body += time.strftime("%m%d", time.localtime(rec["at"]))
                    body += isd.STATUS[rec["evr"]][:1]
                    body += f"{rec['up']:02X}"
                    for key in ("gross", "degrade"):
                        status, value = rec[key]
                        body += status + packed.hexfloat(value)
                    body += packed.hexfloat(rec["min"])
                    body += packed.hexfloat(rec["max"])
                    status, value = rec["leak"]
                    body += status + packed.hexfloat(value)
                    body += rec["stage1"] + rec["processor"]
                    body += f"{len(rec['hoses']):02X}"
                    for fp, hose, status, value in rec["hoses"]:
                        body += fp + hose + status + packed.hexfloat(value)
                return self._frame(code, body), "isd detail"
            if tok in ("V01", "V02", "V03"):
                if not self.c.licensed("isd"):
                    return self._absent(code), "needs the ISD software module"
                asked = (data or "").strip()
                now = self.c.now()
                monthly = tok == "V02"
                if tok == "V01":
                    since = None
                else:
                    year = int(asked[0:4]) if len(asked) >= 6 else now.tm_year
                    month = int(asked[4:6]) if len(asked) >= 6 else now.tm_mon
                    day = (1 if monthly else
                           (int(asked[6:8]) if len(asked) >= 8
                            else now.tm_mday))
                    since = time.mktime((year, month, day, 0, 0, 0, 0, 1, -1))
                heading = {"V01": "ISD ALARM STATUS REPORT",
                           "V02": "ISD MONTHLY STATUS REPORT",
                           "V03": "ISD DAILY STATUS REPORT"}[tok]
                if display:
                    rows = self._isd_status_lines(since, monthly, heading)
                    if tok != "V01":
                        # the status reports reprint the CARB block; the alarm
                        # report does not
                        rows += self._isd_carb_lines()
                    rows += self._isd_alarm_lines()
                    rows.append("-" * 79)
                    rows.append(
                        'CARB STANDARD REPORT FORMAT - CP201 APPENDIX '
                        '"EVR-ISD ' + ("ALARM" if tok == "V01" else "MONTHLY")
                        + ' STATUS REPORT"')
                    return self._frame(code, SEP.join(rows)), "isd alarm status"
                body = ""
                if tok != "V01":
                    site = "assist" if self._isd_evr() == "02" else "balance"
                    reqs = [(lo, hi) for _l, lo, hi
                            in self._carb_requirements(site)]
                    body += f"{len(reqs):02d}"
                    for lo, hi in reqs:
                        body += "01" + packed.hexfloats([lo, hi])
                    thresholds = self._carb_thresholds(site)
                    body += f"{len(thresholds):02d}"
                    for i, (_l, per, lo, hi, _u, _o) in enumerate(thresholds, 1):
                        values = [float(x) for x
                                  in (per.rstrip("dysmin"), lo, hi)
                                  if x not in ("----", "")]
                        body += f"{i:02d}" + packed.hexfloats(values)
                body += self._isd_alarm_body()
                return self._frame(code, body), "isd alarm status"
            if tok == "V00":
                if not self.c.licensed("isd"):
                    return self._absent(code), "needs the ISD software module"
                assist = self._isd_evr() == "02"
                site = "assist" if assist else "balance"
                if display:
                    rows = self._isd_carb_lines() + [isd.CARB_FOOTER]
                    return self._frame(code, SEP.join(rows)), "carb thresholds"
                reqs = [(lo, hi) for _l, lo, hi
                        in self._carb_requirements(site)]
                body = f"{len(reqs):02d}"
                for lo, hi in reqs:
                    body += "01" + packed.hexfloats([lo, hi])
                rows = self._carb_thresholds(site)
                body += f"{len(rows):02d}"
                for i, (_l, per, lo, hi, _u, _o) in enumerate(rows, 1):
                    values = [float(x) for x in (per.rstrip("dysmin"), lo, hi)
                              if x not in ("----", "")]
                    body += f"{i:02d}" + packed.hexfloats(values)
                return self._frame(code, body), "carb thresholds"
            if tok in ("V0A", "V0B"):
                if not self.c.licensed("isd"):
                    return self._absent(code), "needs the ISD software module"
                # "yyyymmdd" on the daily one, "yyyymm" on the monthly
                asked = (data or "").strip()
                now = self.c.now()
                year = int(asked[0:4]) if len(asked) >= 6 else now.tm_year
                month = int(asked[4:6]) if len(asked) >= 6 else now.tm_mon
                if tok == "V0B":
                    day = 1              # "for monthly report dd=01"
                elif len(asked) >= 8:
                    day = int(asked[6:8])
                else:
                    day = now.tm_mday    # no date asked for: today
                since = time.mktime((year, month, day, 0, 0, 0, 0, 1, -1))
                span = 86400 if tok == "V0A" else 31 * 86400
                passing, total = self._isd_stage1(since, since + span)
                overall, collect, contain, processor = self._isd_status()
                evr = isd.EVR_REPORTED.get(self._isd_evr(), "1")
                kind = (self.c.values.get("SV4000") or "00")
                fitted = "0" if kind == "00" else "1"
                up = 100 if self._isd_setup_ok() else 0
                if display:
                    rows = self._isd_status_lines(
                        since, tok == "V0B",
                        "ISD DAILY REPORT" if tok == "V0A"
                        else "ISD MONTHLY REPORT")
                    return self._frame(code, SEP.join(rows)), "isd status"
                body = (f"{year:04d}{month:02d}{day:02d}{evr}"
                        f"{isd.ISD_VERSION}"
                        f"{isd.PROCESSOR_REPORTED.get(kind, '0')}"
                        f"{overall}{collect}{contain}"
                        f"{up:02X}{passing:03X}{total:03X}{up:02X}"
                        f"{fitted}{processor}")
                return self._frame(code, body), "isd status"
            if tok == "V51":
                if not (self.c.licensed("isd") or self.c.licensed("pmc")):
                    return self._absent(code), "needs ISD or PMC"
                passed = self._isd_setup_ok()
                if display:
                    return (self._frame(code, "ISD/PMC TEST STATUS: "
                                        + ("PASS" if passed else "FAIL")),
                            "isd setup verification")
                return (self._frame(code, "0" if passed else "1"),
                        "isd setup verification")
            if tok in ("VC0", "VC1", "VC8"):
                if not self.c.licensed("pmc"):
                    return self._absent(code), "needs the PMC software module"
            elif not self.c.licensed("isd"):
                return self._absent(code), "needs the ISD software module"
            if tok == "VC0":
                held = self._vp_control()
                if display:
                    return (self._frame(code, "VAPOR PROCESSOR "
                                        f"{isd.VP_CONTROL[held]} CONTROL"),
                            "vapor processor control")
                return self._frame(code, held), "vapor processor control"
            if tok == "VC1":
                held = self._vp_running()
                if display:
                    return (self._frame(code, "VAPOR PROCESSOR "
                                        f"{isd.VP_RUNNING[held]}"),
                            "vapor processor state")
                return self._frame(code, held), "vapor processor state"
            if tok == "VC5":
                held = self.c.values.get("SVC500") or isd.OVERRIDDEN_NO
                if display:
                    word = "YES" if held == isd.OVERRIDDEN_YES else "NO"
                    return (self._frame(code, "ISD SHUTDOWN ALARMS "
                                        f"OVERRIDDEN: {word}"),
                            "isd alarm override")
                return self._frame(code, held), "isd alarm override"
            if tok == "VC8":
                want = self.c.values.get("SVC800") or "0"
                now = want if self._vp_running() == "1" else "0"
                if display:
                    return (self._frame(code, SEP.join(
                        ["CURRENT REQUESTED",
                         "VAPOR VALVE POSITION "
                         f"{isd.VALVE[now]} {isd.VALVE[want]}"])),
                        "vapor valve")
                return self._frame(code, now + want), "vapor valve"
            if tok == "XE0":
                held = self.c.values.get("SXE000")
                if not held:
                    held = f"{int(time.mktime(self.c.now())):08X}"
                return self._frame(code, held), "isd setup time stamp"
            # V85, the service report and what has been cleared on it
            cleared = [(tt, self.c.values.get(f"SV85{tt}") or "")
                       for tt, _name in isd.SERVICE_TESTS
                       if tt != isd.COLLECTION]
            if display:
                rows = []
                for (tt, name) in isd.SERVICE_TESTS:
                    if tt == isd.COLLECTION:
                        continue
                    when = dict(cleared).get(tt) or ""
                    rows.append(f"{name} : {self._isd_date(when)}")
                rows.append("COLLECTION TESTS")
                rows.append("FP HOSE-DATE")
                for key in sorted(k for k in self.c.values
                                  if k.startswith("SV85C")):
                    rows.append(f"{key[5:7]} {key[7:9]}-"
                                f"{self._isd_date(self.c.values[key])}")
                return self._frame(code, SEP.join(rows)), "isd service report"
            body = "".join((when or "000000") for _tt, when in cleared)
            for key in sorted(k for k in self.c.values
                              if k.startswith("SV85C")):
                body += key[5:7] + key[7:9] + self.c.values[key]
            return self._frame(code, body), "isd service report"
        if tok in ("V42", "V43", "V48", "V49", "V4A", "V4B"):
            if not self.c.licensed("isd"):
                return self._absent(code), "needs the ISD software module"
            rows = self._isd_rows()
            display = code[0].isupper()
            if tok == "V42":
                got = [r for r in rows if dev == "00" or r[:2] == dev]
                if display:
                    head = ("SS AA F1 FL M1H1L1 M2H2L2 M3H3L3 M4H4L4"
                            " F2 FL M1H1L1 M2H2L2 M3H3L3 M4H4L4")
                    out = ["Sensor / Airflow Meter / Hose Table /"
                           " Grade Table Relationship", head]
                    out += [self._isd_map_line(r) for r in got]
                    return self._frame(code, SEP.join(out)), "isd maps"
                return self._frame(code, "".join(got)), "isd maps"
            if tok == "V43":
                sensors = [s for s in self._isd_sensors()
                           if dev == "00" or s[0] == dev]
                if display:
                    out = ["SENSOR INDEX TABLE",
                           "SENSOR TYPE           S/N        IN USE FLAG"]
                    for ident, name, serial, used in sensors:
                        out.append(f"{ident} {name:<20.20s}{serial:<11.11s}"
                                   f"{'YES' if used else 'NO'}")
                    return self._frame(code, SEP.join(out)), "sensor index"
                return (self._frame(code, "".join(
                    f"{i}{'1' if u else '0'}" for i, _n, _s, u in sensors)),
                    "sensor index")
            if tok == "V49":
                labels = self._isd_labels()
                if display:
                    out = ["LABEL TABLE", "ID LABEL"]
                    out += [f"{i} {t}" for i, t in labels]
                    return self._frame(code, SEP.join(out)), "hose labels"
                return (self._frame(code, "".join(f"{i}{t:<10.10s}"
                                                  for i, t in labels)),
                        "hose labels")
            if tok == "V48":
                got = [(aa, ss, line) for aa, ss, line in isd.afm_view(rows)
                       if dev == "00" or aa == dev]
                if display:
                    out = ["AIRFLOW METER TABLE",
                           "MTR-ID INDEX F1 H1 H2 H3 H4 F2 H1 H2 H3 H4"]
                    for _a, _s, line in got:
                        out.append(" ".join(
                            self._isd_xx(line[i:i + 2])
                            for i in range(0, len(line), 2)))
                    return self._frame(code, SEP.join(out)), "airflow meters"
                return (self._frame(code, "".join(l for _a, _s, l in got)),
                        "airflow meters")
            if tok == "V4A":
                got = [r for r in isd.hose_view(rows)
                       if dev == "00" or r[:2] == dev]
                if display:
                    out = ["ISD HOSE TABLE",
                           "HOSE FP FP  AFM HOSE", "ID   ID LABEL ID  LABEL"]
                    for r in got:
                        label = dict(self._isd_labels()).get(r[8:10], "")
                        out.append(f"{r[0:2]}   {r[2:4]} {r[4:6]}    "
                                   f"{r[6:8]}  {label}")
                    return self._frame(code, SEP.join(out)), "isd hoses"
                return self._frame(code, "".join(got)), "isd hoses"
            got = [r for r in isd.grade_view(rows)
                   if dev == "00" or r[:2] == dev]
            if display:
                out = ["PRODUCT/HOSE MAP TABLE",
                       "FP AFID M1/H1 M2/H2 M3/H3 M4/H4"]
                for r in got:
                    pairs = " ".join(
                        f"{self._isd_xx(r[4 + i * 4:6 + i * 4])}/"
                        f"{self._isd_xx(r[6 + i * 4:8 + i * 4])}"
                        for i in range(4))
                    out.append(f"{r[0:2]} {r[2:4]}   {pairs}")
                return self._frame(code, SEP.join(out)), "isd grades"
            return self._frame(code, "".join(got)), "isd grades"
        if tok == "V10":
            # "ISD VERSION: 01.00", which is the ISD software's own number and
            # not the console's.
            if not self.c.licensed("isd"):
                return self._absent(code), "no ISD software module"
            if code[0].isupper():
                return (self._frame(code, f"ISD VERSION: {isd.ISD_VERSION}"),
                        "isd version")
            return self._frame(code, isd.ISD_VERSION), "isd version"
        if tok in isd.SETUP:
            if not self._isd_licensed(tok):
                spec = isd.SETUP[tok]
                want = " and ".join(k.upper() for k in spec["needs"])
                return self._absent(code), f"needs the {want} software module"
            if code[0].isupper():
                spec = isd.SETUP[tok]
                rows = [r for r in (spec.get("title"), ) if r]
                line = spec.get("line")
                words = self._isd_words(tok)
                rows.append(f"{line} {words}" if line else words)
                return self._frame(code, SEP.join(rows)), "isd setup"
            return self._frame(code, self._isd_value(tok)), "isd setup"
        if tok == "A15":
            # IN-TANK DIAGNOSTIC, which is every other report in 7.4.2 on one
            # sheet: what the probe is, what it is reading, how its sampling
            # is going, its six thermistors as temperatures, and A07's pair of
            # reference distances. Nothing new is modelled here; it is the
            # printout the others feed.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["IN-TANK DIAGNOSTIC", "-" * 18, "PROBE DIAGNOSTICS"]
                for tank in tanks:
                    read, used, err, errtime = self.c.probe_sample_health(tank)
                    rows += [
                        f"T{tank}:PROBE TYPE {self.c.probe_type_long(tank)}",
                        f"SERIAL NUMBER {self.c.probe_serial(tank)}",
                        f"LENGTH: {self.c.probe_length(tank):.1f}",
                        f"DATE CODE {self.c.probe_date_code(tank)}",
                        f"ID CHAN={self.c.probe_circuit_code(tank)}",
                        f"GRADIENT= {self.c.probe_gradient(tank):.4f}",
                        "PROBE INIT:",
                        _stamp_words(self.c.probe_initialised(tank)),
                        # p.505 holds these four counters right against
                        # column 15 and 18
                        f"{'NUM SAMPLES=':<12s}"
                        f"{self.c.probe_window(tank, 'standard'):4d}",
                    ]
                    channels = self.c.probe_buffer(tank, 1)
                    for n in range(0, len(channels), 2):
                        pair = "".join(f"C{n + i:02d} {channels[n + i]:.1f} "
                                       for i in range(2)
                                       if n + i < len(channels))
                        rows.append(pair.rstrip())
                    rows += [f"{'SAMPLES READ=':<13s}{read:6d}",
                             f"{'SAMPLES USED=':<13s}{used:6d}",
                             f"{'LAST ERROR  =':<13s}{err:6d}",
                             "LAST SAMPLE ERROR TIME:",
                             _stamp_words(errtime), "TEMP SENSOR DATA"]
                    for i, t in enumerate(self.c.probe_temperatures(tank)):
                        rows.append(f"T{6 - i}: {t:.1f} F")
                    ref = self.c.probe_reference_distance(tank)
                    if ref:
                        (d1, v1), (d2, v2) = ref
                        rows.append("REF DISTANCE")
                        for when, value in ((d1, v1), (d2, v2)):
                            rows.append(f"{when[2:4]}/{when[4:6]}/{when[0:2]}"
                                        f" {value:9.2f}")
                return self._frame(code, SEP.join(rows)), "in-tank diagnostic"
            body = ""
            for tank in tanks:
                read, used, err, errtime = self.c.probe_sample_health(tank)
                channels = self.c.probe_buffer(tank, 1)
                temps = self.c.probe_temperatures(tank)
                body += (f"{tank:02d}"
                         f"{int(self.c.probe_type_code(tank)):04X}"
                         f"{self.c.probe_serial(tank)}"
                         + packed.hexfloat(self.c.probe_length(tank))
                         + self.c.probe_date_code(tank)
                         + self.c.probe_initialised(tank)
                         + packed.hexfloat(self.c.probe_gradient(tank))
                         + self.c.probe_circuit_code(tank)
                         + ("01" if self.c.probe_low_temp(tank) else "00")
                         + f"{self.c.probe_window(tank, 'standard'):04X}"
                         + packed.hexfloats(channels)
                         + f"{read:08X}{used:08X}{err:08X}" + errtime
                         + packed.hexfloats(temps))
                ref = self.c.probe_reference_distance(tank)
                if ref:
                    (d1, v1), (d2, v2) = ref
                    body += (d1 + packed.hexfloat(v1)
                             + d2 + packed.hexfloat(v2))
            return self._frame(code, body), "in-tank diagnostic"
        if tok == "A14":
            # "MAG PROBE OPTIONS TABLE", one flag wide: "TTNNL", where NN is
            # the number of option flags and L is the low temperature one.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                # p.504 stacks a two word heading over columns at 0 and
                # 5, with the tank held right against 3 and the flag at 7
                rows = ["MAG PROBE OPTIONS TABLE", "TNK   LOW", "NUM  TEMP"]
                for tank in tanks:
                    rows.append(
                        f"{tank:4d}"
                        f"{'YES' if self.c.probe_low_temp(tank) else 'NO':>5s}")
                return self._frame(code, SEP.join(rows)), "mag probe options"
            body = "".join(f"{tank:02d}01"
                           f"{1 if self.c.probe_low_temp(tank) else 0}"
                           for tank in tanks)
            return self._frame(code, body), "mag probe options"
        if tok in ("A20", "A21", "A22"):
            # The three leak test flag reports. Every example in all three
            # prints the headings with nothing after them, which is what a
            # probe with nothing wrong with it has to say.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            which = {"A20": "present", "A21": "stored", "A22": "gross"}[tok]
            title = {"present": "PRESENT LEAK TEST ANALYSIS REPORT",
                     "stored": "STORED LEAK TEST ANALYSIS REPORT",
                     "gross": "GROSS LEAK TEST ANALYSIS REPORT"}[which]
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(self._probe_head(tank, title))
                    flags = self.c.probe_leak_flags(tank, which)
                    for rate in self.c.probe_leak_rates(tank, which):
                        head = ("GROSS LEAK TEST FLAGS:" if rate == "gross"
                                else f"{rate} GAL/HR FLAGS:")
                        rows.append(head)
                        rows.extend(flags.get(rate) or [])
                return self._frame(code, SEP.join(rows)), "leak test flags"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                setflags = [f for rate in self.c.probe_leak_rates(tank, which)
                            for f in (self.c.probe_leak_flags(tank, which)
                                      .get(rate) or [])]
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         f"{len(setflags):02X}" + "".join(setflags))
            return self._frame(code, body), "leak test flags"
        if tok == "A23":
            # "TANK LEAK TEST AVERAGING BUFFERS": the finished tests each rate
            # is averaging over, newest first, with the average under them.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(self._probe_head(
                        tank, "LEAK TEST AVERAGING BUFFERS"))
                    for rate_key, shown in self.c.LEAK_BUFFERS:
                        rows.append(f"{shown} GAL/HR LEAK TEST BUFFER")
                        # p.510: HOURS at 21, VOLUME at 28 and RATE at
                        # 38, over figures held right against 25, 33 and 41
                        rows.append("START TIME           HOURS  VOLUME"
                                    "    RATE")
                        got = self.c.probe_leak_buffer(tank, rate_key)
                        for r in got:
                            rows.append(f"{clock_words(r.started):21s}"
                                        f"{r.hours:5.1f}{r.volume:8.0f}"
                                        f"{r.rate:8.3f}")
                        if got:
                            n = len(got)
                            rows.append(f"{'AVERAGE':21s}"
                                        f"{sum(r.hours for r in got)/n:5.1f}"
                                        f"{sum(r.volume for r in got)/n:8.0f}"
                                        f"{sum(r.rate for r in got)/n:7.3f}")
                return self._frame(code, SEP.join(rows)), "leak averaging buffers"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                recs = [r for rate_key, _s in self.c.LEAK_BUFFERS
                        for r in self.c.probe_leak_buffer(tank, rate_key)]
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         f"{len(recs):02X}")
                for r in recs:
                    body += (time.strftime("%y%m%d%H%M",
                                           time.localtime(r.started))
                             + packed.hexfloat(r.hours)
                             + packed.hexfloat(r.volume)
                             + packed.hexfloat(r.rate))
            return self._frame(code, body), "leak averaging buffers"
        if tok == "A07":
            # "Probe types 01=CAP0 and 02=CAP1 are not supported by this
            # command": a Mag probe's diagnostic and nobody else's. Asked
            # about one tank that has not got one, that is a 9999; asked
            # about all of them, it is simply not one of the rows.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = [t for t in self._tanks(dev)
                     if self.c.probe_reference_distance(t)]
            if not tanks:
                return self._absent(code), "A07 is a Mag probe command"
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    (d1, v1), (d2, v2) = self.c.probe_reference_distance(tank)
                    # A07 heads its tank with the Name Type, not the family
                    # word: the manual's own example reads MAG7 where A01's
                    # reads MAG, and a real console's chapter 9 printout
                    # agrees. See console.probe_name_type.
                    rows.append(self._probe_head(
                        tank, word=self.c.probe_name_type(tank)))
                    # p.495: the label at 0, the date at 20 and the
                    # figure right against 37, which is where the
                    # page's own `XXXXX.XX` placeholder ends
                    for what, when, value in (("ORIG", d1, v1),
                                              ("CURR", d2, v2)):
                        shown = f"{when[2:4]}/{when[4:6]}/{when[0:2]}"
                        rows.append(f"{what} REF DISTANCE".ljust(20)
                                    + f"{shown} {value:8.2f}")
                return self._frame(code, SEP.join(rows)), "reference distance"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                (d1, v1), (d2, v2) = self.c.probe_reference_distance(tank)
                body += (f"{tank:02d}{pcode}{self.c.probe_type_code(tank)}"
                         + d1 + packed.hexfloat(v1)
                         + d2 + packed.hexfloat(v2))
            return self._frame(code, body), "reference distance"
        if tok == "20D":
            # "This command will respond only if stick height is enabled"
            if not (self.c.values.get("S60B00") or "").strip().endswith("1"):
                return self._absent(code), "stick height not enabled"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["TANK STICK HEIGHT",
                        "TANK  PRODUCT LABEL     INCHES"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"{tank:3d}   {label:<18.18s}"
                                f"{self.c.stick_height(tank):6.1f}")
                return self._frame(code, SEP.join(rows)), "stick height"
            body = "".join(f"{t:02d}" + packed.hexfloat(self.c.stick_height(t))
                           for t in tanks)
            return self._frame(code, body), "stick height"
        if tok == "211":
            # Tank Chart Report, at the height step the command carries
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if not (data or "").strip():
                # No step, no chart: a version 23 site answered a bare
                # I21100 with the frame and nothing in it (2026-10-07).
                return self._frame(code, ""), "tank chart"
            step = self._chart_step(data, code)
            if step is None:
                return self._nine(code), "REJECTED: bad step size"
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    rows += self.c.chart_table(tank, step)
                return self._frame(code, SEP.join(rows)), "tank chart"
            body = ""
            for tank in tanks:
                pairs = self.c.chart_pairs(tank, step)
                body += f"{tank:02d}{len(pairs) * 2:04X}"
                for height, volume in pairs:
                    body += packed.hexfloat(height)
                    body += packed.hexfloat(volume)
            return self._frame(code, body), "tank chart"
        if tok in ("202", "20C"):
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.tank_level))
            recent = tok == "20C"
            title = "LAST DELIVERY REPORT" if recent else "DELIVERY REPORT"
            if code[0].isupper():
                text = "\n" + self.c.deliveries.report(
                    tanks, title, recent) + "\n"
                return self._frame(code, text), "delivery report"
            return (self._frame(code, self.c.deliveries.record_data(tanks)),
                    "delivery report")
        if tok in ("218", "219", "56A", "63B"):
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.programmed_tanks()))
            if tok == "219":
                # Tank Chart Security Status
                flag = "1" if self.c.chart_secured() else "0"
                if code[0].isupper():
                    state = "ENABLED" if flag == "1" else "DISABLED"
                    text = "\nTANK CHART SECURITY\n" + state + "\n"
                    return self._frame(code, text), "chart security status"
                return self._frame(code, flag), "chart security status"
            if tok == "56A":
                # when the passcode was last changed
                when = self.c.chart_code_set or "0000000000"
                if code[0].isupper():
                    text = "\nTANK CHART SECURITY\nDATE/TIME\n" + when + "\n"
                    return self._frame(code, text), "chart code audit"
                return self._frame(code, when), "chart code audit"
            reports = {"218": self.c.audit_report, "63B": self.c.chart_report}
            if tok == "63B":
                # only a tank on the fifty point profile has fifty points:
                # the bench's tank 1, labelled, 500 inches across and 200000
                # gallons on 1 PT, is not in I63B00 (2026-09-18, `cap_swept`)
                # -- and asked by number, a 1 PT tank 1 is the title alone,
                # and bare packed (`I63B01`, `i63B01`, 2026-09-22)
                tanks = [t for t in tanks if self.c.tank_profile(t) == "04"]
            if tok == "63B" and code[0].isupper() and not tanks:
                # the bench TLS-350, with no tank programmed, answers the
                # title and nothing under it (2026-09-18)
                return (self._frame(code, "TANK 50 POINT HEIGHTS AND VOLUMES"),
                        "tank chart, no tank")
            if code[0].isupper():
                text = "\n" + ("\n\n".join(
                    reports[tok](t) for t in tanks)) + "\n"
                return self._frame(code, text), "tank chart"
            # `tanks` is empty on a console with a probe card and no tank
            # programmed, and the chart of no tank is an empty one
            val = (self.c.values.get(f"S63B{tanks[0]:02d}")
                   if tok == "63B" and tanks else None)
            if tok == "63B" and tanks and val is None:
                # the bench packs a fifty point tank as its number, a `0`
                # whose meaning no page gives, the diameter, the full volume
                # and the count of pairs: `0104479FF5C497423F000` for 999.99
                # inches and 999999 gallons with none (`cap_tank1on`)
                val = "".join(
                    f"{t:02d}0" + packed.hexfloat(self.c.limit("607", t) or 0.0)
                    + packed.hexfloat(self.c.full_volume(t) or 0.0)
                    + f"{len(self.c.chart_points(t)):02d}"
                    + "".join(packed.hexfloat(h) + packed.hexfloat(v)
                              for h, v in self.c.chart_points(t))
                    for t in tanks)
            return self._frame(code, val or ""), "tank chart"
        if tok == "217":
            # Tank Profile: which of the five a tank is on, per I217's table
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.programmed_tanks()))
            if code[0].isupper():
                # 576013-635 Rev AA p.81, measured off the word boxes: TANK
                # at column 0, PRODUCT LABEL at 7 and PROFILE at 31, with the
                # tank right against 2 and the profile right against 37.
                # `wiretables.heading` has nothing for 217 -- the builder
                # cannot shape a block whose title is followed by a DEVICE
                # header before its heading -- so the line is written here,
                # and it used to be missing entirely.
                rows = ["TANK PROFILE", "",
                        f"{'TANK':<7s}{'PRODUCT LABEL':<24s}PROFILE"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    name = self.c.PROFILE_REPORT_NAME[
                        self.c.tank_profile(tank)]
                    rows.append(f"{tank:2d}     {label:<24.24s}{name:>6s}")
                text = "\n" + "\n".join(rows) + "\n"
                return self._frame(code, text), "tank profile"
            body = "".join(f"{t:02d}{self.c.tank_profile(t)}" for t in tanks)
            return self._frame(code, body), "tank profile"
        if tok in ("214", "2E2"):
            # Two reports with the SAME "TTpssssNN" header and different
            # float blocks: 2E2 carries 201's seven (volume, TC volume,
            # ullage, height, water, temperature, water volume) and 214
            # carries six (volume, MASS, DENSITY, height, water, temperature).
            # Height lands in slot 4 on both by coincidence; slots 2, 3 and 7
            # are different quantities. 2E2 also puts a record number and a
            # timestamp in FRONT of the tank, so its record does not even
            # start at the same offset.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            record = ((data or "").strip()[:2] or "01") if tok == "2E2" else ""
            if tok == "2E2":
                # A STORED inventory, and this console stores none: it
                # answered with the live one dressed as record 01. A
                # version 23 site with four tanks reporting answered the
                # frame and nothing in it (2026-10-07); what makes a console
                # store one is not on the shelf.
                tanks = []
            if code[0].isupper() and not tanks and tok == "2E2":
                # The bench TLS-350, every tank off: I2E200 is the frame and
                # the stamp alone (site snapshot, 2026-09-22), as I201 is.
                # This printed the column heading over nothing. CLOSED S37.
                return self._frame(code, ""), "inventory"
            if code[0].isupper():
                # Both blocks' columns are 576013-635's own -- p.82 for 214
                # and p.104 for 2E2 -- and 2E2's block was one this project
                # could not see until the tool learned that a stored report
                # answers with a SECOND stamp where a title would be.
                rows = (["IN-TANK MASS/DENSITY INVENTORY", ""]
                        if tok == "214" else [])
                rows.append(wiretables.heading(tok))
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    v = (self._mass_floats(tank) if tok == "214"
                         else self._inventory(tank, "201"))
                    whole = (0, 1) if tok == "214" else (0, 1, 2)
                    rows.append(wiretables.row(tok, [tank, label] + [
                        f"{n:.0f}" if p in whole else n
                        for p, n in enumerate(v)]))
                return self._frame(code, SEP.join(rows)), "inventory"
            body = ""
            if tok == "2E2":
                body += record + time.strftime("%y%m%d%H%M", self.c.now())
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                values = (self._mass_floats(tank) if tok == "214"
                          else self._inventory(tank, "201"))
                body += (f"{tank:02d}{pcode}{self._tank_status_bits(tank):04X}"
                         + packed.hexfloats(values))
            return self._frame(code, body), "inventory"
        if tok in ("213", "215", "21B"):
            # The "extended delivery" trio, which is NOT uniform:
            #   213  TTp dd ...  takes nn, no trailing flag
            #   215  TTp dd ...  takes NO nn, trailing density flag per record
            #   21B  TT  dd ...  no product code at all, and a different and
            #        much longer float list
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            asked = (data or "").strip()
            if tok in ("213", "21B") and asked and not asked[0:2].isdigit():
                # nn, how many deliveries, is a decimal count
                return self._nine(code), "REJECTED: nn is a number"
            most = int(asked[0:2] or 5) if tok in ("213", "21B") and asked \
                else 5
            tanks = self._tanks(dev)
            title = {"213": "DELIVERY REPORT",
                     "215": "MASS/DENSITY DELIVERY REPORT",
                     "21B": "BIR ADJUSTED DELIVERY REPORT"}[tok]
            if code[0].isupper() and not tanks and tok == "213":
                # I21300 on the bench TLS-350 with every tank off is the
                # frame and the stamp alone (site snapshot, 2026-09-22), as
                # I202 is. This printed DELIVERY REPORT over nothing.
                # CLOSED S37.
                return self._frame(code, ""), "delivery report"
            if code[0].isupper():
                rows = [title]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows.append(self._delivery_head(tok))
                    for rec in self._deliveries_of(tank, most):
                        rows += self._delivery_rows(tok, tank, rec)
                return self._frame(code, SEP.join(rows)), "delivery report"
            body = ""
            for tank in tanks:
                got = self._deliveries_of(tank, most)
                body += f"{tank:02d}"
                if tok != "21B":
                    body += (self.c.text("603", tank) or " ")[:1] or " "
                body += f"{len(got):02d}"
                for rec in got:
                    body += (time.strftime("%y%m%d%H%M",
                                           time.localtime(rec["start"]))
                             + time.strftime("%y%m%d%H%M",
                                             time.localtime(rec["end"])))
                    body += packed.hexfloats(self._delivery_floats(tok, tank,
                                                                   rec))
                    if tok == "215":
                        body += "1" if self.c.density_defaulted(tank) else "0"
            return self._frame(code, body), "delivery report"
        if tok == "216":
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["TANK 50 POINT HEIGHTS, VOLUMES AND SLOPES"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    pairs = self.c.chart_report_rows(tank)
                    rows.append(f"T {tank}: {label}")
                    # p.84: DIAMETER at 7, FULL VOLUME at 19 and SLOPE
                    # at 35 over figures held right against 14, 29 and 39,
                    # then PAIR at 1, HEIGHT at 9, VOLUME at 24, SLOPE at 35
                    rows.append("       DIAMETER    FULL VOLUME     SLOPE")
                    diameter = self.c.limit("607", tank) or 96.0
                    full = self.c.full_volume(tank)
                    slope = full / diameter if diameter else 0.0
                    rows.append(f"{diameter:15.2f}{full:15.0f}{slope:10.2f}")
                    rows.append(" PAIR    HEIGHT         VOLUME     SLOPE")
                    for n, (height, volume, rate) in enumerate(pairs, 1):
                        rows.append(f"{n:4d}{height:11.2f}{volume:15.0f}"
                                    f"{rate:10.2f}")
                return self._frame(code, SEP.join(rows)), "tank chart"
            body = ""
            for tank in tanks:
                pairs = self.c.chart_report_rows(tank)
                diameter = self.c.limit("607", tank) or 96.0
                full = self.c.full_volume(tank)
                slope = full / diameter if diameter else 0.0
                body += (f"{tank:02d}" + packed.hexfloat(diameter)
                         + packed.hexfloat(full) + packed.hexfloat(slope)
                         + f"{len(pairs):02X}")
                for height, volume, rate in pairs:
                    body += (packed.hexfloat(height) + packed.hexfloat(volume)
                             + packed.hexfloat(rate))
            return self._frame(code, body), "tank chart"
        if tok in ("222", "225", "226", "227"):
            # The ticket/variance family. 222 carries a Bill of Lading number
            # between the timestamp and the float count; the other three do
            # not. And the period selector is NOT the same field: 222, 225 and
            # 226 take tt (01=current, 02=previous) where 227 takes MMDD.
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            asked = (data or "").strip()
            previous = asked[:2] == "02" if tok != "227" else False
            kind = {"222": "periodic", "225": "periodic",
                    "226": "weekly", "227": "daily"}[tok]
            tanks = self._tanks(dev)
            title = {"222": "TICKETED AND BOL DELIVERY REPORT",
                     "225": "CURRENT PERIOD DELIVERY VARIANCE REPORT",
                     "226": "CURRENT WEEK DELIVERY VARIANCE REPORT",
                     "227": "DAILY DELIVERY VARIANCE REPORT"}[tok]
            if previous:
                title = title.replace("CURRENT", "PREVIOUS")
            if code[0].isupper():
                rows = [title, "VOLUMES ARE STANDARD"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    if tok == "222":
                        rows.append("                      BOL     TICKET"
                                    "  GAUGE   TC GAUGE")
                        rows.append("DELIVERY END DATE     NUMBER  VOLUME"
                                    "  VOLUME  VOLUME")
                    else:
                        # p.93: TICKET at 25, GAUGE at 41 and VARIANCE
                        # at 55, over figures held right against 31, 46
                        # and 61
                        rows.append("                         TICKET      "
                                    "    GAUGE         VARIANCE")
                        rows.append("                         VOLUME      "
                                    "    VOLUME")
                    total = [0.0, 0.0, 0.0]
                    for rec in self._deliveries_of(tank, 10):
                        stamp = clock_words(rec["end"])
                        tick, gauge = rec["ticketed"], rec["amount"]
                        if tok == "222":
                            rows.append(f"{stamp:22s}{rec['bol'] or '':<8s}"
                                        f"{tick:7.1f}{gauge:8.1f}"
                                        f"{rec['tc']:8.1f}")
                        else:
                            rows.append(f"{stamp:22s}{tick:10.1f}"
                                        f"{gauge:15.1f}{tick - gauge:15.1f}")
                        total[0] += tick
                        total[1] += gauge
                        total[2] += tick - gauge
                    if tok != "222":
                        rows.append(f"{'TOTALS':22s}{total[0]:10.1f}"
                                    f"{total[1]:8.1f}{total[2]:9.1f}")
                        sales = self.c.bir.row(tank, kind)
                        sold = (sales or {}).get("sales", 0.0)
                        pct = (total[2] / sold * 100.0) if sold else 0.0
                        rows.append("PERCENT VARIANCE OF SALES "
                                    f"{total[2]:.1f}={pct:.1f}%")
                return self._frame(code, SEP.join(rows)), "delivery variance"
            body = ""
            for tank in tanks:
                got = self._deliveries_of(tank, 10)
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                body += (f"{tank:02d}{pcode}"
                         f"{self.c.probe_type_code(tank)}{len(got):03d}")
                for rec in got:
                    body += time.strftime("%y%m%d%H%M",
                                          time.localtime(rec["end"]))
                    if tok == "222":
                        bol = rec["bol"] or ""
                        body += f"{len(bol):02X}" + bol
                        body += packed.hexfloats([rec["ticketed"],
                                                  rec["amount"], rec["tc"]])
                    else:
                        body += packed.hexfloats(
                            [rec["ticketed"], rec["amount"],
                             rec["ticketed"] - rec["amount"]])
            return self._frame(code, body), "delivery variance"
        if tok in ("281", "282"):
            if tok == "281" and not self.c.licensed("fuelman"):
                return self._absent(code), "Fuel Manager not installed"
            tanks = self._tanks(dev)
            if tok == "282" and not self.c.licensed("fuelman"):
                # Fuel Manager's, like 281, but answered bare rather than
                # refused: a version 23 site with no Fuel Manager key
                # (2026-10-07)
                return self._frame(code, ""), "FLS volumes"
            if tok == "282":
                if code[0].isupper():
                    rows = ["FLS DIAGNOSTICS: VOLUME TABLE"]
                    for tank in tanks:
                        label = self.c.text("602", tank) or f"TANK {tank}"
                        volume = self.c.tank_level.get(tank, {}).get(
                            "volume", 0.0)
                        rows.append(f"T {tank}:{label}")
                        rows.append(f"CURRENT INVENTORY VOLUME: {volume:.0f}")
                        history = self.c.volume_history(tank)
                        for at in range(0, len(history), 13):
                            rows.append(" ".join(f"{v:.0f}"
                                                 for v in history[at:at + 13]))
                    return self._frame(code, SEP.join(rows)), "FLS volumes"
                body = ""
                for tank in tanks:
                    volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
                    history = self.c.volume_history(tank)
                    body += (f"{tank:02d}" + packed.hexfloat(volume)
                             + time.strftime("%y%m%d%H%M", self.c.now())
                             + packed.hexfloats(history))
                return self._frame(code, body), "FLS volumes"
            # 281, the Fuel Management Report
            if code[0].isupper():
                rows = ["FUEL MANAGEMENT REPORT"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    f = self.c.fuel_management(tank)
                    rows.append(f"{label} ( TANK {tank} )")
                    rows.append(f"DAYS FUEL REMAINING: {f[0]:.1f}"
                                "    AVERAGE SALES (GALLONS)")
                    rows.append(f"INVENTORY : {f[1]:.0f} GAL"
                                "        SUN  MON  TUE  WED  THR  FRI  SAT")
                    rows.append(f"95% ULLAGE: {f[2]:.0f} GAL        "
                                + " ".join(f"{v:4.0f}" for v in f[3:10]))
                return self._frame(code, SEP.join(rows)), "fuel management"
            body = f"{len(tanks):02X}"
            for tank in tanks:
                body += f"{tank:02d}" + ((self.c.text("603", tank)
                                          or " ")[:1] or " ")
            for tank in tanks:
                body += packed.hexfloats(self.c.fuel_management(tank))
            return self._frame(code, body), "fuel management"
        if tok in ("A61", "A63"):
            # The same printed columns and NOT the same packed record: A63
            # carries an Ending Temperature float and an NN field count, A61
            # carries neither. So A61's own printout has an ENDTEMP column
            # that its computer format cannot fill, which is the manual's
            # doing rather than ours.
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows.append(wiretables.heading(tok))
                    for one in self._hrm_hours(tank):
                        # p.517 and p.519 draw the same six columns, and the
                        # precision of four of them is on the page too
                        rows.append(wiretables.row(tok, [
                            one["stamp"], one["temp"], one["volume"],
                            one["sales"], one["flag"], one["variance"]]))
                return self._frame(code, SEP.join(rows)), "HRM diagnostic"
            body = ""
            for tank in tanks:
                hours = self._hrm_hours(tank)
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                body += f"{tank:02d}{pcode}{len(hours):02d}"
                for one in hours:
                    body += one["stamp"] + one["flag"]
                    values = [one["volume"], one["sales"], one["variance"]]
                    if tok == "A63":
                        # only A63 counts its fields and only A63 has the
                        # temperature the display column wants
                        values.append(one["temp"])
                        body += f"{len(values):02X}"
                    body += packed.hexfloats(values)
            return self._frame(code, body), "HRM diagnostic"
        if tok == "A62":
            # A different record from its two neighbours: a daily aggregate
            # with a min, a max, an average and a verdict, and no status flag.
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows.append("DAILY HRM HISTORY")
                    # p.518: RECORDS at 17, MIN at 29, MAX at 40, AVE at
                    # 51 and STATUS at 60, over figures held right against
                    # 18, 32, 43 and 54
                    rows.append(wiretables.heading("A62"))
                    for one in self._hrm_days(tank):
                        rows.append(f"{one['stamp']:10s}{one['records']:9d}"
                                    f"{one['min']:14.3f}{one['max']:11.3f}"
                                    f"{one['ave']:11.3f}"
                                    f"{hrmreports.HRM_DAILY[one['status']]:>11s}")
                return self._frame(code, SEP.join(rows)), "HRM daily history"
            body = ""
            for tank in tanks:
                days = self._hrm_days(tank)
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                body += f"{tank:02d}{pcode}{len(days):02d}"
                for one in days:
                    body += (one["stamp"] + f"{one['records']:02d}"
                             + packed.hexfloats([one["min"], one["max"],
                                                 one["ave"]])
                             + one["status"])
            return self._frame(code, body), "HRM daily history"
        if tok == "A56":
            if not self.c.licensed("csld"):
                return self._absent(code), "CSLD not installed"
            previous = (data or "").strip().startswith("1")
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = self.csld_monthly_rows(tanks, previous)
                return self._frame(code, SEP.join(rows)), "CSLD monthly"
            body = "1" if previous else "0"
            for tank in tanks:
                states = self._csld_states(tank, previous)
                body += f"{tank:02d}{len(states):02X}"
                for at, state in states:
                    body += time.strftime("%y%m%d%H%M", time.localtime(at))
                    body += state
            return self._frame(code, body), "CSLD monthly"
        if tok == "A81":
            if not self.c.licensed("fuelman"):
                return self._absent(code), "Fuel Manager not installed"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["FUEL MANAGEMENT DIAGNOSTIC REPORT"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    f = self.c.fuel_management(tank)
                    last = self.c.fuel_management_last(tank)
                    predicted = self.c.fuel_management_predicted(tank)
                    rows.append(f"{label} ( TANK {tank} )")
                    rows.append(f"DAYS FUEL REMAINING: {f[0]:.1f}"
                                "    AVERAGE SALES (GALLONS)")
                    rows.append(f"INVENTORY : {f[1]:.0f} GAL   "
                                "SUN  MON  TUE  WED  THR  FRI  SAT")
                    rows.append(f"95% ULLAGE: {f[2]:.0f} GAL   "
                                + " ".join(f"{v:4.0f}" for v in f[3:10]))
                    rows.append("LAST SALES:            "
                                + " ".join(f"{v:4.0f}" for v in last))
                    rows.append("PREDICTED SALES:       "
                                + " ".join(f"{v:4.0f}" for v in predicted))
                return self._frame(code, SEP.join(rows)), "fuel diagnostic"
            body = f"{len(tanks):02d}"
            for tank in tanks:
                body += f"{tank:02d}" + ((self.c.text("603", tank)
                                          or " ")[:1] or " ")
            for tank in tanks:
                f = self.c.fuel_management(tank)
                body += packed.hexfloats(
                    list(f) + list(self.c.fuel_management_last(tank))
                    + list(self.c.fuel_management_predicted(tank)))
            return self._frame(code, body), "fuel diagnostic"
        if tok == "A91":
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["POWER OUTAGE REPORT"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    # p.522: FUEL VOLUME at 39, WATER VOLUME at 54 and
                    # TEMP DEG F at 69, over figures right against 49, 61
                    # and 77
                    rows.append(wiretables.heading("A91"))
                    for one in self._outages(tank):
                        rows.append(f"{'POWER REMOVED:':<16s}"
                                    f"{clock_words(one['off']):24s}"
                                    f"{one['off_volume']:10.0f}"
                                    f"{one['off_water']:12.0f}"
                                    f"{one['off_temp']:16.1f}")
                        rows.append(f"{'POWER RESTORED:':<16s}"
                                    f"{clock_words(one['on']):24s}"
                                    f"{one['on_volume']:10.0f}"
                                    f"{one['on_water']:12.0f}"
                                    f"{one['on_temp']:16.1f}")
                        rows.append(f"{'GROSS VOLUME CHANGE:':<21s}"
                                    f"{one['change']:28.0f}")
                return self._frame(code, SEP.join(rows)), "power outage"
            body = ""
            for tank in tanks:
                got = self._outages(tank)
                body += f"{tank:02d}{len(got):02d}"
                for one in got:
                    # the manual's notes name these the other way round, and
                    # its own display and float order both put REMOVED first
                    body += time.strftime("%y%m%d%H%M",
                                          time.localtime(one["off"]))
                    body += time.strftime("%y%m%d%H%M",
                                          time.localtime(one["on"]))
                    body += packed.hexfloats(
                        [one["off_volume"], one["off_water"], one["off_temp"],
                         one["on_volume"], one["on_water"], one["on_temp"],
                         one["change"]])
            return self._frame(code, body), "power outage"
        if tok in ("B61", "B62"):
            if not self.c.has("smart"):
                return self._absent(code), "no smart sensor module fitted"
            if tok == "B61":
                # A VAPOR VALVE DIAGNOSTIC is a per-category report, and this
                # walked every smart sensor on the card whatever it was --
                # every position the cage could hold, configured or not -- so
                # a mag sensor and a vacuum sensor each answered with a valve
                # position and a battery invented for them. `_smart_devices`
                # is the filter B33 has always used; category 08 is 723's own
                # number for the valve. See FIDELITY L4.
                sensors = wiresensors._smart_devices(
                    self.c, dev, wiresensors.VALVE)
                if code[0].isupper():
                    rows = ["VAPOR VALVE DIAGNOSTIC REPORT"]
                    for n in sensors:
                        label = self.c.text("722", n) or f"VAPOR VALVE {n}"
                        v = self._valve(n)
                        rows += ["", f"s {n}:{label}", "",
                                 "VAPOR VALVE",
                                 _valve_line("SERIAL NUMBER", v["serial"]),
                                 _valve_line("VALVE POSITION:",
                                             hrmreports.VALVE_POSITION[
                                                 v["position"]])]
                        # "BATTERY: ... (only if wireless)", and a wired valve
                        # reports 0=Unknown. 577013-937 Figure 42 is a real
                        # console's IB6100 for a wired valve and prints no
                        # BATTERY line at all; the packed form below still
                        # carries the byte, because its format always does.
                        if v["battery"] != "0":
                            rows.append(_valve_line(
                                "BATTERY:", hrmreports.BATTERY[v["battery"]]))
                        rows += [
                            _valve_line("OPEN CAP:",
                                        hrmreports.CAPACITOR[v["open_cap"]]),
                            _valve_line("CLOSE CAP:",
                                        hrmreports.CAPACITOR[v["close_cap"]]),
                            _valve_line("AMBNT TEMP:",
                                        f"{v['ambient']:.2f} F"),
                            _valve_line("OUTLET TMP:", f"{v['outlet']:.2f} F"),
                            "SENSOR FAULTS:"]
                        # every fault name on p.540 carries one leading space,
                        # and so does NONE
                        rows += [" " + f for f in (v["faults"] or ["NONE"])]
                    return self._frame(code, SEP.join(rows)), "vapor valve"
                body = ""
                for n in sensors:
                    v = self._valve(n)
                    bits = 0
                    for name in v["faults"]:
                        bits |= 1 << (hrmreports.B61_BIT[name] - 1)
                    body += (f"{n:02d}{v['serial']:0>8.8s}{v['position']}"
                             f"{v['battery']}{v['open_cap']}{v['close_cap']}"
                             f"{bits:04X}" + "02"
                             + packed.hexfloat(v["ambient"])
                             + packed.hexfloat(v["outlet"]))
                return self._frame(code, body), "vapor valve"
            # B62, whose sub alarm numbering is NOT B61's bit numbering
            history = self._valve_history()
            if code[0].isupper():
                # p.546: ID right against 1, TYPE at 5, ALARM TYPE at 10,
                # SUB ALARM at 31, STATE at 56 and the unpadded date and
                # hour at 64 and 72
                rows = ["SMART SENSOR SUB ALARM HISTORY",
                        wiretables.heading("B62")]
                for one in history:
                    stamp = time.strptime(one["at"], "%y%m%d%H%M")
                    when, clock = alarm_report_stamp(stamp)
                    rows.append(f"{one['sensor']:2d}"
                                f"{14:5d}   "
                                f"{'SENSOR FAULT ALARM':<21s}"
                                f"{one['fault']:<25s}"
                                f"{'CLEAR' if one['state'] == '00' else 'ALARM':<8s}"
                                f"{when:>8s} {clock:>7s}")
                return self._frame(code, SEP.join(rows)), "sub alarm history"
            body = f"{len(history):02X}"
            for one in history:
                body += (f"{one['sensor']:02X}0E03"
                         + hrmreports.B62_CODE[one["fault"]]
                         + one["state"] + one["at"])
            return self._frame(code, body), "sub alarm history"
        if tok in sumpreports.SUMP_REPORTS:
            # Four reports, one family -- and `tt` is a STATUS on 317 and 318
            # and a COUNT OF ROWS on 319 and 31A. Same letter, same position.
            # The rows are sumpreports' and the tests behind them sumptest's;
            # these used to be one sensor's readings made up per call, the
            # same whatever had or had not been run. FIDELITY U1b.
            if not self.c.has("smart"):
                return self._absent(code), "no smart sensor module fitted"
            spec = sumpreports.SUMP_REPORTS[tok]
            sensors = self._mag_devices(dev)
            if code[0].isupper():
                rows = ["MAG SUMP LEAK TEST", spec["title"]]
                for number in sensors:
                    rows += sumpreports.display_rows(self.c, tok, number)
                return self._frame(code, SEP.join(rows)), "mag sump test"
            body = "".join(sumpreports.computer_record(self.c, tok, number)
                           for number in sensors)
            return self._frame(code, body), "mag sump test"
        if tok in ("391", "392"):
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                if not tanks:
                    # the frame and nothing in it, on the bench with no tank
                    # reporting (`cap_coldstart`)
                    return self._frame(code, ""), "tanker load"
                rows = ["TANKER LOAD REPORT", ""] if tok == "391" else []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    loads = self._loads_of(tank)
                    if tok == "391":
                        # A version 23 site (2026-10-07): the title, `T 1:`,
                        # a blank line, GALLON columns, NO DATA HISTORY on a
                        # tank with none, and a blank line after. p.146's
                        # sample heads it `TANK  1 ...` over VOLUME, and has
                        # no title. A row is placed under the site's
                        # headings; the site had no loads to show one.
                        rows += [f"T {tank}:{label}", "",
                                 "NO START DATE/TIME  GALLON  TEMP    "
                                 "END DATE/TIME  GALLON  TEMP   TOTAL"]
                        for n, one in loads:
                            rows.append(self._tanker_row(n, one))
                        if not loads:
                            rows.append("NO DATA HISTORY")
                        rows.append("")
                        continue
                    # p.146 and p.147 head a tank with the number held right
                    # against column 6 and the label at 8, which is NOT how
                    # p.64 and p.71 head one
                    rows.append(f"TANK {tank:2d} {label}")
                    if tok == "392":
                        rows.append("NO         DATE/TIME      VOLUME  TEMP"
                                    "  TC VOLUME")
                        for n, one in loads:
                            rows.append(
                                f"{n:2d}  START: "
                                f"{self._tanker_stamp(one['start'])}"
                                f"{one['start_vol']:7.0f}"
                                f"{one['start_temp']:6.1f}"
                                f"{one['start_tc']:11.0f}")
                            rows.append(
                                f"{'END:':>10s} {self._tanker_stamp(one['end'])}"
                                f"{one['end_vol']:7.0f}"
                                f"{one['end_temp']:6.1f}"
                                f"{one['end_tc']:11.0f}")
                            rows.append(f"{'TOTAL:':>10s} {'':14s}"
                                        f"{one['total']:7.0f}{'':6s}"
                                        f"{one['total_tc']:11.0f}")
                return self._frame(code, SEP.join(rows)), "tanker load"
            body = ""
            for tank in tanks:
                loads = self._loads_of(tank)
                body += f"{tank:02d}{len(loads):02d}"
                for n, one in loads:
                    body += f"{n:02d}"
                    if tok == "391":
                        body += "06"
                        body += time.strftime("%y%m%d%H%M",
                                              time.localtime(one["start"]))
                        body += packed.hexfloat(one["start_vol"])
                        body += packed.hexfloat(one["start_temp"])
                        body += time.strftime("%y%m%d%H%M",
                                              time.localtime(one["end"]))
                        body += packed.hexfloat(one["end_vol"])
                        body += packed.hexfloat(one["end_temp"])
                        body += packed.hexfloat(one["total"])
                    else:
                        body += "02"
                        body += time.strftime("%y%m%d%H%M",
                                              time.localtime(one["start"]))
                        body += time.strftime("%y%m%d%H%M",
                                              time.localtime(one["end"]))
                        body += packed.hexfloats(
                            [one["start_vol"], one["start_temp"],
                             one["end_vol"], one["end_temp"], one["total"],
                             one["start_tc"], one["end_tc"], one["total_tc"]])
            return self._frame(code, body), "tanker load"
        if tok in ("411", "412"):
            # Identical layouts, incompatible alarm tables: 0002 is "Disabled
            # VMCI Board" on 411 and "Roots meter not connected" on 412.
            if not self.c.has("vmc"):
                return self._absent(code), "no VMC interface fitted"
            table = (sumpreports.VMCI_ALARMS if tok == "411"
                     else sumpreports.VMC_ALARMS)
            most = sumpreports.VMCI_MAX if tok == "411" else sumpreports.VMC_MAX
            devices = ([int(dev)] if dev != "00" and dev.isdigit() and int(dev)
                       else list(range(1, most + 1)))
            title = ("VMCI ALARM HISTORY REPORT" if tok == "411"
                     else "VMC ALARM HISTORY REPORT")
            # 411 is the VMCI BOARD, which is alarm category 35; 412 is the
            # VMC CONTROLLER, category 36. Both read the console's own alarm
            # log rather than a store of their own, so the two reports, the
            # two alarm histories and the status report cannot disagree about
            # what happened. Both printed a header and no rows before -- the
            # computer form said "00 incidents" for every device and the
            # display form said nothing at all. See FIDELITY M12 and N1.
            category = "35" if tok == "411" else "36"
            if code[0].isupper():
                rows = [title, "DEVICE  ALARMS" if tok == "411"
                        else "VMC   S/N    ALARMS"]
                rows += sumpreports.alarm_rows(
                    self.c, category, devices, table,
                    8 if tok == "411" else 13)
                return self._frame(code, SEP.join(rows)), "vmc alarm history"
            body = sumpreports.alarm_records(self.c, category, devices)
            return self._frame(code, body), "vmc alarm history"
        if tok == "680":
            # "Computer format is not supported for this command" -- the only
            # report in the manual that says so.
            if not self.c.licensed("fuelman"):
                return self._absent(code), "Fuel Manager not installed"
            if not code[0].isupper():
                return self._nine(code), "display format only"
            rows = ["FUEL MANAGEMENT SETUP",
                    # p.314 holds the days right against 23 and the
                    # auto print time at 16
                    f"DELIVERY WARN DAYS:"
                    f"{self.c.limit('681', 0) or 3.5:5.1f}",
                    # p.314 holds the time right against 23, which is
                    # where DELIVERY WARN DAYS's figure ends too
                    f"{'AUTO PRINT:':<16s}"
                    + (self.c.text("682", 0) or "10:00 AM"),
                    "FUEL MANAGEMENT AVERAGE SALES (GALLONS)"]
            for tank in self._tanks(dev):
                label = self.c.text("602", tank) or f"TANK {tank}"
                f = self.c.fuel_management(tank)
                rows.append(f"{label} ( TANK {tank} )")
                rows.append("   SUN   MON   TUE   WED   THR   FRI   SAT")
                rows.append("".join(f"{v:6.0f}" for v in f[3:10]))
            return self._frame(code, SEP.join(rows)), "fuel management setup"
        if tok == "790":
            # "Response is the same as display format" -- no packed template.
            ports = ([int(dev)] if dev != "00" and dev.isdigit() and int(dev)
                     else [1])
            rows = [f"EDIM:{n} VR:{self.c.DIM_SOFTWARE} "
                    f"TD:{self.c.software_info()['created']}" for n in ports]
            return self._frame(code, SEP.join(rows)), "DIM software revision"
        if tok in ("888", "88D"):
            # "PP - Communication Port Number (00=all)", and device 00
            # answered for port 1 alone on a console with six positions.
            ports = ([int(dev)] if dev != "00" and dev.isdigit() and int(dev)
                     else self.c.serial_positions() or [1])
            if tok == "88D" and not self.c.has("modem"):
                # SiteLink's diagnostic, on a console with no modem board:
                # the bench TLS-350 (RS-232 and serial satellite only)
                # answers a bare frame, where this drew both its boards as
                # S-LINK with a NetComm modem (2026-09-18).
                return self._frame(code), "no modem board"
            if tok == "88D":
                # `MM - Modem Type` and `DD - Modem Auto Detected` are two
                # bytes, and this console has no modem to detect: what it
                # found is what it was told it would find. Both read `S885`
                # -- which had four options on the keypad and two here, so
                # 88D reported a GSM modem on every port whatever the
                # setting said. See FIDELITY D10.
                if code[0].isupper():
                    rows = ["COMMUNICATION DIAGNOSTIC"]
                    for n in ports:
                        kind = self.c.modem_type(n)
                        name = sumpreports.MODEM_TYPE.get(
                            kind, sumpreports.MODEM_TYPE["00"])
                        # p.477 sets the colon at 12, the same column
                        # 888 and 889 set theirs
                        rows += [f"COMM BOARD  : {n} S-LINK",
                                 "MODEM TYPE : " + name,
                                 "MODEM AUTO DETECTED: " + name]
                        # "Only displayed if modem type is VR TLS GSM
                        # MODEM" -- 576013-818 Rev AA Figure 6-27, p.6-22.
                        if kind == self.c.GSM_MODEM:
                            rssi, ber = self.c.comm_signal(n)
                            rows.append(f"RSSI: {rssi:02d}  BER: {ber:02d}")
                    return self._frame(code, SEP.join(rows)), "SiteLink"
                body = ""
                for n in ports:
                    kind = self.c.modem_type(n)
                    rssi, ber = self.c.comm_signal(n)
                    body += f"{n:02d}{kind}{kind}{rssi:02d}{ber:02d}"
                return self._frame(code, body), "SiteLink"
            if code[0].isupper():
                return (self._frame(code, SEP.join(self._comm_status(ports))),
                        "comm status")
            total = sum(len(self.c.comm_errors.get(n) or ()) for n in ports)
            body = f"{total:02d}"
            if not total:
                # "NN - Total Number of Error Reports To Follow", and none
                # follow: the bench TLS-350 answers `i88800` with `00` and
                # nothing after it, both ports connected to nothing
                # (`cap_swept` and every capture since)
                return self._frame(code, body), "comm status"
            for n in ports:
                errors = self.c.comm_errors.get(n) or ()
                body += (f"{n:02d}{len(errors):02d}"
                         + f"{int(self.c.comm_connect.get(n) or 0):02d}")
                for one in errors:
                    uart = one.get("uart") or self._port_uart(n)
                    body += (f"{one.get('state', 0):02d}"
                             f"{one.get('error', 0):02d}"
                             f"{int(uart['baud']):05d}"
                             f"{uart['parity']}{uart['stop']}{uart['data']}")
                for which in ("comm_data_at", "comm_error_at"):
                    when = getattr(self.c, which).get(n)
                    body += (time.strftime("%y%m%d%H%M", time.localtime(when))
                             if when else "0" * 10)
            return self._frame(code, body), "comm status"
        if tok == "8A4":
            # The Inquire form, which the manual gives in both formats and
            # this console answered with an empty frame: "MAINTENANCE TRACKER
            # BLOCK HARDWARE KEY / LABEL  ID". Same template as 8A3 and the
            # opposite list, which is what makes blocking a key checkable
            # from the wire. See FIDELITY D13.
            if not self.c.has("mt"):
                return self._absent(code), "no Maintenance Tracker fitted"
            keys = self.c.blocked_tracker_keys()
            if code[0].isupper():
                rows = ["MAINTENANCE TRACKER BLOCK HARDWARE KEY",
                        "LABEL" + " " * 15 + "ID"]
                rows += [f"{name:<20.20s}{ident}" for ident, name in keys]
                return self._frame(code, SEP.join(rows)), "blocked keys"
            body = f"{len(keys):03d}"
            for ident, name in keys:
                body += f"{name:<17.17s}{ident:<6.6s}"
            return self._frame(code, body), "blocked keys"
        if tok in ("8A2", "8A3"):
            if not self.c.has("mt"):
                return self._absent(code), "no Maintenance Tracker fitted"
            if tok == "8A2":
                entries = self.c.service_codes()
                if code[0].isupper():
                    # 576013-635 Rev AA's own sample: the label left in
                    # twenty and the code at column 21, under a STANDARD
                    # heading, then a blank and a USER DEFINED one. This
                    # ran the code column one place right and printed no
                    # second heading at all. See FIDELITY P1.
                    rows = ["SERVICE CODE LIST", "STANDARD LABEL      CODE"]
                    rows += [f"{name:<20.20s}{cc}"
                             for cc, name in self.c.SERVICE_CODES]
                    rows += ["", "USER DEFINED LABEL  CODE"]
                    rows += [f"{name:<20.20s}{cc}"
                             for cc, name in self.c.user_service_codes]
                    return self._frame(code, SEP.join(rows)), "service codes"
                body = f"{len(entries):03d}"
                for cc, name in entries:
                    body += f"{name:<19.19s}{cc}"
                return self._frame(code, body), "service codes"
            keys = self.c.tracker_keys()
            if code[0].isupper():
                # The label runs left in TWENTY and the ID starts at column
                # 21, measured off the rendered page rather than counted in
                # an extraction: the sample's `LABEL` and `ID` sit at x=72
                # and x=191.9 on a 6-point character, which is column 20.
                # This was eighteen, and 8A2's list two screens away had the
                # same fault -- see FIDELITY P1 and D13.
                rows = ["MAINTENANCE TRACKER ACTIVE HARDWARE KEY LIST",
                        "LABEL" + " " * 15 + "ID"]
                rows += [f"{name:<20.20s}{ident}" for ident, name in keys]
                return self._frame(code, SEP.join(rows)), "tracker keys"
            body = f"{len(keys):03d}"
            for ident, name in keys:
                body += f"{name:<17.17s}{ident:<6.6s}"
            return self._frame(code, body), "tracker keys"
        if tok in ("901", "903"):
            if tok == "901":
                if code[0].isupper():
                    # p.487: the three headings at 23, 32 and 40, and
                    # the results held right against 25, 34 and 43
                    return (self._frame(code, SEP.join(
                        [f"{'':23s}I/O      RAM     PROM",
                         f"{'SYSTEM BOARD':<22s}PASS     PASS     PASS"])),
                        "self test")
                return self._frame(code, "000000"), "self test"
            if code[0].isupper():
                # p.489: the title at 3, the sub-title at 2, the rule
                # in two halves of six with two spaces between them, and the
                # three counters held right against 21
                # Twenty-four columns, every value held right against the
                # last of them, and the label padded to fifteen with " ="
                # after it -- which puts `PASSED` and a five-digit count in
                # the same place. The two message counters break that rule
                # and the console keeps them: `MC->PC COMMS =` is fourteen
                # characters where the other five are seventeen.
                #
                # `tests/console_capture/raw/I90300.bin` is where all of that
                # is measured, and it settles three things this report had
                # wrong besides the spacing: the blank line under the
                # checksum, the two MC/PC message counters, which were not
                # drawn at all, and the counters themselves, which were
                # literal zeros here while the PANEL's own screen for the
                # same diagnostic had a reset count and both message counts.
                # See FIDELITY S18.
                n = self.c.pc_counters()
                pad = (lambda label, value:
                       f"{label:<15s} ={str(value):>7s}")
                rows = ["   PC DIAGNOSTIC DATA", "  PERIPHERAL CONTROLLER",
                        # Twenty-four columns exactly: six dashes, two
                        # spaces, six dashes. This ran to twenty-five,
                        # because "- " * 6 carries its own trailing space.
                        ("- " * 6).strip() + "  " + ("- " * 6).strip(),
                        f"PC SWARE# {self.c.PC_SOFTWARE}",
                        f"CREATED - {self.c.PC_CREATED}",
                        pad("PC ROM CHECKSUM", "PASSED"),
                        "",
                        pad("PC RESET COUNTS", n["resets"]),
                        pad("PC COMM ERRORS", n["comm_errors"]),
                        pad("MC CKSUM ERRS", n["cksum_errors"]),
                        f"MC->PC COMMS ={n['to_pc']:>10d}",
                        f"MC<-PC COMMS ={n['from_pc']:>10d}"]
                return self._frame(code, SEP.join(rows)), "PC diagnostic"
            # the PC board's own date, then `01` -- in no note of the
            # manual's, and on the bench before the count; read as the ROM
            # checksum the display calls PASSED -- then the five counters in
            # hex (`330269-002-B  94.12.16.13.2601050000000100000000...`,
            # 2026-09-18). This sent five zeros.
            n = self.c.pc_counters()
            body = (f"{self.c.PC_SOFTWARE:<14.14s}"
                    f"{self.c.PC_CREATED:<14.14s}" + "01" + "05"
                    + "".join(f"{int(n[k]):08X}" for k in
                              ("resets", "comm_errors", "cksum_errors",
                               "to_pc", "from_pc")))
            return self._frame(code, body), "PC diagnostic"
        if tok in ("BA0", "BB1"):
            if not self.c.has("vmc"):
                return self._absent(code), "no VMC interface fitted"
            if tok == "BA0":
                if code[0].isupper():
                    rows = ["MDIM TOTALIZER"]
                    rows += [f"{n}  0.000" for n in range(1, 5)]
                    return self._frame(code, SEP.join(rows)), "MDIM totalizer"
                # "No record count field": the reader runs to the terminator
                return (self._frame(code, "".join(f"{n:04d}"
                                                  + packed.hexfloat(0.0)
                                                  for n in range(1, 5))),
                        "MDIM totalizer")
            # "all" is what the cage can carry, which is what the PAPER has
            # always used: `console.vmc_numbers()` is
            # `range(1, capacity("vmc") + 1)` and this was a hardcoded three.
            # 576013-610 p.26-1: "You can generate a report for up to 18 VMC
            # controllers." One code, too narrow for `00` and unbounded for
            # `xx`. FIDELITY O27 and S28.
            controllers = ([int(dev)] if dev != "00" and dev.isdigit()
                           and int(dev) else self.c.vmc_numbers())
            if code[0].isupper():
                rows = ["VMC REPORT",
                        "VMC S/N     SIDE STATUS  RECOVER RATE FUEL CNT"
                        " ERR CNT REM TIME"]
                for n in controllers:
                    for side in ("A", "B"):
                        v = self.c.vmc_side(n, side)
                        rows.append(f"{n:<4d}{self.c.vmc_serial(n):<8s}"
                                    f"{side:<5s}{v['status']:<8s}"
                                    f"{v['rate']:<13.1f}{v['fuel']:<9d}"
                                    f"{v['error']:<8d}{v['remain']}")
                return self._frame(code, SEP.join(rows)), "VMC status"
            body = ""
            for n in controllers:
                for side in ("A", "B"):
                    v = self.c.vmc_side(n, side)
                    # the radix mixes inside one record: serial decimal, side
                    # and status hex, rate DECIMAL times ten, counters hex
                    body += (f"{n:02d}{self.c.vmc_serial(n):>06.6s}"
                             f"{1 if side == 'A' else 2}"
                             f"{self.c.vmc_status_code(n, side)}"
                             # the radix mixes inside one record: the serial
                             # is decimal, the side and status hex, the
                             # recover rate DECIMAL times ten, and the three
                             # counters hex again
                             f"{int(v['rate'] * 10):04d}"
                             f"{v['fuel']:04X}{v['error']:04X}"
                             f"{v['remain']:04X}")
            return self._frame(code, body), "VMC status"
        if tok in ("212",):
            # 576013-635 Rev AA p.74 prints the SAME sample under 212 as p.65
            # prints under 207, line for line: TANK LEAK TEST HISTORY, the
            # LAST and FULLEST blocks and the monthly section. The two differ
            # only in the COMPUTER format, where each of 212's records
            # carries two more fields after the percentage:
            #
            #   zz        - Number of 8 Byte Fields to Follow (Hex)
            #   mmmmmmmm  - In-Tank Leak Test Method (Hex),
            #               00000000=Standard, 00000001=CSLD
            #
            # This was built as a separate report -- three LAST blocks and no
            # FULLEST or monthly sections, a compressed header, a different
            # row format, the report type and history number hardcoded, and
            # the method byte hardcoded to Standard even on a CSLD tank,
            # which is the one field 212 exists for. See FIDELITY H14.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                return (self._frame(code, self._leak_history_display(tanks)),
                        "leak test history")
            body = ""
            for tank in tanks:
                records = self.c.leak_history_records(tank)
                body += f"{tank:02d}{len(records):02X}"
                full = self.c.full_volume(tank) or 0.0
                for kind_code, number, result in records:
                    pct = (result.volume / full * 100.0) if full else 0.0
                    body += (kind_code + f"{number:02d}"
                             + TEST_TYPE_CODE.get(result.rate_key, "00")
                             + time.strftime("%y%m%d%H%M",
                                             time.localtime(result.started)))
                    for value in (result.hours, result.volume, pct):
                        body += packed.hexfloat(value)
                    # one eight byte field follows, and it is the method --
                    # per TEST, not per tank, the same rule the display
                    # column follows
                    body += "01" + (
                        "00000001"
                        if self.c.leak_test_method(tank, result.rate_key)
                        == "CSLD" else "00000000")
            return self._frame(code, body), "leak test history"
        if tok in ("203", "208"):
            devices = ([int(dev)] if dev != "00"
                       else sorted(self.c.tank_level))
            text = (self.c.leaks_detect_report(devices) if tok == "203"
                    else self.c.leaks_results_report(devices))
            if code[0].isupper():
                return self._frame(code, "\n" + text + "\n"), "leak test report"
            return (self._frame(code, self.c.leaks_results_record(devices)),
                    "leak test report")
        if tok in ("902", "905"):
            # System Revision Level Report, and the Report II that arrived at
            # version 15 to replace it. Both draw the same block a console
            # prints, so the display format is one report under two codes.
            #
            # The computer formats are where they part. 902 answers the two
            # identity lines and stops:
            #
            #     SOFTWARE# nnnnnn-vvv-rrrCREATED - YY.MM.DD.HH.mm
            #
            # 905 carries the same pair, then nn feature flags of two bytes
            # each, then the S-Module, which is the whole reason a technician
            # on V15 or later is told to ask for this one instead:
            #
            #     SOFTWARE# 346abb-Tvv-rrrCREATED - YY.MM.DD.HH.mm
            #     nnAABBCCDDEEFFGGHHIIJJKKLLS-MODULE# nnnnnn-vvv-r
            rep = self.c.revision_report()
            if code[0].isupper():
                text = "\n" + "\n".join(rep) + "\n"
                return self._frame(code, text), "revision level report"
            s = self.c.software_info()
            # `nnnnnn-vvv-rrr` is fourteen wide and a one-letter revision
            # leaves two spaces: `SOFTWARE# 346326-100-B  CREATED - ...` on
            # the bench TLS-350's i90200 and i90500 (2026-09-18)
            body = f"SOFTWARE# {s['number']:<14s}CREATED - {s['created']}"
            if tok == "905":
                flags = self.c.revision_flags()
                body += f"{len(flags):02X}"
                body += "".join("01" if on else "00" for _name, on in flags)
                body += f"S-MODULE# {s['smodule']}"
            return self._frame(code, body), "revision level report"
        if not self._module_present(tok):
            return self._absent(code), "no module fitted for this function"
        if code[0].isupper():
            # The manual answers a Display inquire with a titled TABLE, and
            # its columns are read off the page rather than guessed. This
            # has to come before the device-00 short-circuit below: a Display
            # inquire for every tank is a table with a row per tank, not the
            # computer aggregate in a display envelope. See FIDELITY S1.
            answered = wiretables.display_answer(self, tok, dev, code)
            if answered is not None:
                return answered
            # Before the "not programmed" turn below, because a group's
            # values are other codes' and this one's own store is empty on a
            # console nobody has been near. `I50500` answered a bare stamp
            # for exactly that reason. See FIDELITY S17.
            answered = self._group_answer(tok, code)
            if answered is not None:
                return answered
        if dev == "00" and self.c.is_multi(tok):
            return (self._frame(code, self._computer_aggregate(tok)),
                    "device-00 aggregate")
        keep = self._packed_filter(tok) if code[:1].islower() else None
        if keep and dev.isdigit() and int(dev) and not keep(self.c, int(dev)):
            return self._frame(code, ""), "not this line's pipe type"
        val = self.c.values.get(f"S{tok}{dev}")
        if val is None and tok in self.LEGACY_TWINS:
            # the Version 4 code reads its twin in both formats: `i50700`
            # answers 547's 01, `i50A00` 55A's 300
            twin = self.LEGACY_READ.get(tok, self.LEGACY_TWINS[tok][0])
            val = self.c.values.get(f"S{twin}{dev}")
        if val is None and tok in self.c.SHARED_STORE:
            # one value under two names: a 60A Set lands on 604's key when
            # 604 holds it, and `i60A01` answers it (`s60A01` in the sweep)
            val = self.c.values.get(f"S{self.c.SHARED_STORE[tok]}{dev}")
        if val is None and code[:1].islower():
            # A setting nobody has changed still has a value, in computer
            # format as on the glass: the bench TLS-350 answers `i56000` with
            # `0` out of a cold start, where this answered nothing at all.
            val = self._default_stored(tok, dev)
            if val is None:
                # and one with no documented default holds what the display
                # reads for it: `i50600` answers 1 for a warning 546 turned
                # on, and `i62C01` answers `010`, tank 1 STANDARD
                blank = self._blank_stored(tok, dev)
                if blank is not None:
                    val = (dev + blank if self.c.is_prefixed(tok)
                           and dev != "00" else blank)
        if val is None and not (code[0].isupper()
                                and (self.display_value(tok, dev)
                                     or self._has_parts(tok))):
            return self._frame(code, ""), "not programmed"
        if code[0].isupper():
            # a few of these have their display line printed in the manual,
            # "BEEPER: ENABLED": and where it does, that is what comes back
            from .console import FIELDS
            field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
            if field is None:
                answered = self._part_field_answer(tok, dev, val, code)
                if answered is not None:
                    return answered
            line = (field or {}).get("wire_line")
            shown = self.display_value(tok, dev)
            if line:
                # 502's line names the shift the inquire asked for --
                # `SHIFT TIME 1 : DISABLED` is the sample's own shift, and
                # `I50203` answers about the third. It is the only one of
                # the twenty-four that carries a device. See FIDELITY N6a.
                if "%d" in line:
                    line = line.replace("%d", str(int(dev or "1") or 1))
                if (field or {}).get("kind") == "time":
                    # right-aligned in eight, which is what DISABLED is:
                    # `SHIFT TIME 1 :  6:00 AM` and `SHIFT TIME 3 : 10:00 PM`
                    # against this console's unpadded ` 6:00 AM`. The bench
                    # TLS-350 with three shifts programmed, 2026-09-19,
                    # `transcripts/shifts2`.
                    shown = f"{shown:>8}"
                head = self._comm_board_head(tok, dev)
                body = f"{line} {shown}"
                return self._frame(code, head + SEP + body if head
                                   else body), ""
            # No printed display line for this one, and no table either --
            # `wiretables` was asked before the device-00 short-circuit and
            # had nothing to draw. The manual still does not answer a Display
            # inquire with the COMPUTER payload, which is what this used to
            # do: I621TT came back `0144FA0000`, the ASCII hex IEEE float of
            # the low product limit, where the manual's own response prints
            # `1000`. The command formats say it plainly -- Display is
            # `S621TTGGGGGG`, six decimal digits, and Computer is
            # `S621TTFFFFFFFF` -- so the display side gets the decoded value
            # under whatever title the manual gives the response.
            if shown not in (None, ""):
                entry = WIRE_TITLES.get(tok) or {}
                title = self._comm_board_head(tok, dev) or entry.get("title")
                if title and self._title_is_the_body(title, field):
                    title = None
                # The blank lines under a title are the block's own, and this
                # path was the one place that ignored them. 530 is why: a
                # real console answers I53000 with `SYSTEM BEEPER`, a blank
                # line, and `ENABLED` under it, where the manual prints the
                # single line `BEEPER: ENABLED`. See `tools/
                # console_corrections.json`.
                gap = SEP * (1 + max(int(entry.get("gap") or 0), 0))
                # A label with its value on the line BELOW it, rather than
                # after it. 518 draws `CODE PAGE SELECTED:` and ` WINDOWS`
                # under it -- the manual's own sample and a real console
                # both -- where `wire_line` puts the two on one line. The
                # blank between them is the block's own `gap`, already
                # measured off the page.
                block = (field or {}).get("wire_block")
                if block:
                    shown = SEP.join(part.replace("{}", str(shown))
                                     for part in block)
                body = title + gap + str(shown) if title else str(shown)
                return self._frame(code, body), "decoded for the display"
        return self._frame(code, self._packed(tok, dev, val)), ""

    def _packed(self, tok, dev, val):
        """A stored value as computer format carries it, where the bench
        TLS-350 packs it otherwise than the store holds it (2026-09-18,
        `cap_swept`): `packed_width` pads a code to that width -- 525's port
        is `02`, 78A's sensor type `03`, 851's flag `00` -- and
        `packed_float` sends a whole number as a float, 78F's 25 PSI as
        41C80000. A device prefix stays in front."""
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01") \
            or FIELDS.get(f"S{tok}00")
        if not field or val is None:
            return val
        prefixed = self.c.is_prefixed(tok) and dev != "00" and len(val) > 2
        pfx, body = (val[:2], val[2:]) if prefixed else ("", val)
        if field.get("packed_float"):
            try:
                body = packed.hexfloat(float(body))
            except ValueError:
                pass
        elif field.get("packed_width"):
            body = body.strip().rjust(field["packed_width"], "0")
        elif field.get("packed_digits") and body.strip().isdigit():
            # printed to a minimum width rather than the field's: 54A's 0
            # days packs as `00` and 365 as `365` (the computer Set sweep)
            body = f"{int(body):0{field['packed_digits']}d}"
        # the exact floats in the system's units: `i60401` answers 757000.0
        # in metric for 200000 gallons (`cap_metric`)
        body = units.packed(tok, body, units.system(self.c),
                            packed.unhexfloat, packed.hexfloat)
        return pfx + body

    # `COMM BOARD  : 3 (FXMOD)` -- the two codes whose response opens with
    # the comm position it is about rather than with a title.
    _COMM_BOARD_TITLE = re.compile(r"^COMM BOARD\s*:\s*\d+\s*\(.*\)$")

    def _comm_board_head(self, tok, dev):
        """The `COMM BOARD  : n (TYPE)` line this code is answered under.

        None for every code that has no such line, so the caller carries on.

        887 and 889 both draw one and neither drew it. 887 printed the
        manual's own sample board -- `COMM BOARD  : 3 (FXMOD)`, filed as the
        report's TITLE by the generator, which is what it looks like -- for
        whatever position was asked and whatever card was in it; 889 took the
        `wire_line` path, which returns before a title is ever considered,
        and printed `DTR NORMAL STATE: HIGH` with no header at all. The
        manual draws the pair on both pages, p.473 and p.475, and the
        console's own 888 report has drawn this header from
        `comm_board_name` all along. See FIDELITY S15 and S18.
        """
        title = (WIRE_TITLES.get(tok) or {}).get("title") or ""
        if not self._COMM_BOARD_TITLE.match(title):
            return None
        port = int(dev) if dev.isdigit() and int(dev) else 1
        return f"COMM BOARD  : {port} ({self.c.comm_board_name(port)})"

    @staticmethod
    def _title_is_the_body(title, field):
        """Is that "title" really the sample's one-line ANSWER?

        `wiretitles.json` is read off the manual's own Display samples, and a
        response whose whole body is one line gives the extractor nothing to
        tell a title from a value. 529's sample is two lines -- the stamp and
        `ALL PHONES` -- so ALL PHONES was filed as the report's title, and
        the console answered it over the top of the value: `ALL PHONES` and
        then `SINGLE PHONE`.

        A body whose whole text is one of the field's own CHOICES is a body.
        Narrow on purpose: seventy-four entries have a title and nothing
        else, and almost all of them are `LABEL: VALUE` lines that a field's
        `wire_line` already answers instead. See FIDELITY N6a.
        """
        want = str(title).strip().upper()
        for choice in (field or {}).get("choices") or []:
            seq = choice if isinstance(choice, (list, tuple)) else (choice,
                                                                    choice)
            if want in (str(seq[0]).upper(), str(seq[-1]).upper()):
                return True
        return False

    def _dst_window(self):
        """51B's two moments as MMWDHHmm, the stored ones or the documented
        APR WEEK 1 SUN and OCT WEEK 6 SUN at 2:00 AM."""
        out = []
        for n, default in ((1, "04170200"), (2, "10670200")):
            raw = (self.c.values.get(f"S51B{n:02d}") or "").strip()
            out.append(raw[-8:] if len(raw) >= 8 and raw[-8:].isdigit()
                       else default)
        return out

    def _dst_lines(self):
        """I51B00 as the bench TLS-350 draws it once 51A is on (2026-09-18):
        `START DATE    APR   WEEK 1   SUN   2:00 AM`, and the end the same."""
        rows = ["", "DAYLIGHT SAVING TIME"]
        for head, raw in zip(("START DATE", "END DATE"), self._dst_window()):
            month = fieldio.MONTHS[int(raw[:2]) - 1]
            day = fieldio.DAYS[int(raw[3]) - 1]
            hour, minute = int(raw[4:6]), raw[6:8]
            clock = f"{hour % 12 or 12}:{minute} {'PM' if hour >= 12 else 'AM'}"
            rows += ["", f"{head:<14s}{month}   WEEK {raw[2]}   {day}   {clock}"]
        return rows + [""]

    def _mass_density_on(self):
        """560, Mass/Density, switched on."""
        return (self.c.values.get("S56000") or "0").strip()[-1:] == "1"

    def _custom_alarms_on(self):
        """5BD, custom alarms, switched on."""
        return (self.c.values.get("S5BD00") or "").strip().endswith("1")

    def _dst_on(self):
        """51A, automatic daylight saving, switched on."""
        return (self.c.values.get("S51A00") or "").strip().endswith("1")

    def _dst_in_force(self):
        """Is the console's clock inside the daylight saving window?

        The window is 51B's documented one, APR WEEK 1 SUN to OCT WEEK 6 SUN
        at 2:00 AM, week 6 being the month's last; a window programmed
        through 51B is not modelled, and neither is the hour the clock
        moves.
        """
        import calendar
        from .console import FIELDS
        now = self.c.now()

        def moment(key):
            month, _week, week, _day = FIELDS[key + ".date"]["default"].split()
            m = fieldio.MONTHS.index(month) + 1
            sundays = [d for d in range(1, calendar.monthrange(now.tm_year, m)[1] + 1)
                       if calendar.weekday(now.tm_year, m, d) == 6]
            day = sundays[-1] if int(week) > len(sundays) else sundays[int(week) - 1]
            return (m, day, 2)
        here = (now.tm_mon, now.tm_mday, now.tm_hour)
        return moment("S51B01") <= here < moment("S51B02")

    def display_value(self, tok, dev):
        """`_display_value_us`, in the console's system units: the console
        stores U.S. units and converts on the way out (`units`)."""
        shown = self._display_value_us(tok, dev)
        quantity = units.QUANTITY.get(tok)
        if quantity:
            system = units.system(self.c)
            exact = None
            if system != units.US and dev.isdigit():
                # the stored float, where there is one, not its digits
                exact = self.c.limit(tok, int(dev))
                if exact is None:
                    held = self._default_stored(tok, dev) or ""
                    try:
                        exact = packed.unhexfloat(held[-8:])
                    except (ValueError, TypeError):
                        exact = None
            shown = units.shown(quantity, shown, system, exact)
        return shown

    def _display_value_us(self, tok, dev):
        """One device's setting, as the DISPLAY side writes it.

        The two sides of the wire disagree about what a setting is and the
        command formats say so: Display is `S621TTGGGGGG`, six decimal
        digits, and Computer is `S621TTFFFFFFFF`, an ASCII hex IEEE float.
        This is the decimal one. None where nothing is programmed, or where
        the code holds only part fields and has no bare field of its own.
        """
        from .console import FIELDS
        from .screens import shown as panel_shows
        key = f"S{tok}{dev}"
        if tok in self.LEGACY_READ:
            key = f"S{self.LEGACY_READ[tok]}{dev}"
        val = self.c.values.get(key)
        if val is None and tok in self.c.SHARED_STORE:
            # Two names for one value: 604 and 60A for a full volume, and the
            # six Version 4 test-warning codes for their Version 15 twins. A
            # console programmed through either answers both -- see
            # `Console.SHARED_CODES` and `wiretables._value_keys`.
            other = self.c.SHARED_STORE[tok]
            val = self.c.values.get(f"S{other}{dev}")
            if val is not None:
                key = f"S{other}{dev}"
        field = FIELDS.get(key) or FIELDS.get(f"S{tok}01")
        if field is None:
            return None
        if val is None and (field.get("default_litres") is not None
                            or tok == "628"):
            # held in litres (see `_default_stored`), read in gallons
            val = self._default_stored(tok, dev)
        if val is None:
            # Nothing is stored for a setting nobody has changed, and the
            # console is not blank there: the panel reads ENGLISH, U.S.,
            # DISABLED, 1200 baud, +0.000 offset. `screens.shown` is the
            # rule, and the wire did not follow it -- 27 codes carrying a
            # documented default answered a bare stamp. See FIDELITY S9.
            shown = panel_shows(self.c, field, "")
            if not shown:
                return None
            # And a NUMBER the panel shows is still a number. `screens.shown`
            # answers in the panel's own MASK -- 634's reconciliation warning
            # limit reads `000003` on a 24-column display, which is what the
            # mask `000000` is for -- and that string reached the GALLONS
            # column of `I63400` with its leading zeros still on, where the
            # captured console prints a bare right-aligned decimal like every
            # other number on the shelf. Only the four fields whose default
            # is written in mask form were affected; a stored value has gone
            # through `fieldio.decode` all along. See FIDELITY S18.
            shown = self._unmasked_number(field, shown)
        else:
            shown = fieldio.decode(field, key, val, self.c)
        if tok == "51A" and str(shown).strip() == "ENABLED":
            # whether the shift is in force now: the bench TLS-350 answers
            # `ENABLED OFF` on a January clock (2026-09-18)
            return "ENABLED " + ("ON" if self._dst_in_force() else "OFF")
        # A number some consoles print as a word. The bench TLS-350 answers
        # I78500 with `NONE` for a line that has no tank, where this printed
        # `0` -- `screen_words` had the panel's NONE and nothing gave the
        # wire its own. Keyed by the number, so `00` and `0` are one value.
        words = field.get("wire_words")
        if words:
            try:
                word = words.get(str(int(float(str(shown).strip()))))
            except (TypeError, ValueError):
                word = None
            if word is not None:
                return word
        # A float decodes with "%g" so that one rule serves a thermal
        # coefficient and a full volume alike. Where the manual PRINTS the
        # display line it also prints the precision and the unit --
        # "OFFSET: 0.000%" -- and `wire_format` is that, for the display line
        # only. The computer format is untouched.
        fmt = field.get("wire_format")
        if fmt:
            try:
                shown = fmt % float(shown)
            except (TypeError, ValueError):
                pass
        return shown

    @staticmethod
    def _unmasked_number(field, shown):
        """A numeric default without the panel mask's leading zeros."""
        if field.get("kind") not in ("float", "int", "number"):
            return shown
        text = str(shown).strip()
        if not text.isdigit() or not text.startswith("0") or len(text) == 1:
            return shown
        return str(int(text))

    # Fifteen function codes have no bare field at all, because two or more
    # panel prompts share one function's data and `consoledata.json` holds
    # only the PARTS: `S50100.date` and `S50100.time`, never `S50100`. That is
    # the part-field mechanism working as designed, and the Display inquire
    # path walked straight into it -- `fieldio.decode(None, ...)` raised out of
    # `Handler.handle`, and `_session` catches only OSError, so the connection
    # dropped. Fourteen of the fifteen have an Inquire and four are in the
    # tape's own backup, so restoring a real site and asking for the time was
    # enough to do it. See FIDELITY S7.
    #
    # 501's answer IS the frame's own stamp. 576013-635 Rev AA p.152 prints
    # the whole response and there is no line under the title:
    #
    #     <SOH> I50100 JAN 22, 1996  3:11 PM
    #           SYSTEM DATE AND TIME <ETX>
    #
    # so it gets the title alone and the other thirteen get their parts.
    STAMP_IS_THE_ANSWER = {"501"}

    # ---- a report whose rows are label OVER value, and whose values are not
    # all the code's own ------------------------------------------------------
    #
    # `I50500` and `I51700` come back from a real console with the same six
    # lines to the character, one minute apart -- see
    # `tests/console_capture/raw/`. 576013-635 Rev AA p.156 draws the same
    # six, and its own note 1 says why there are two codes for one setting:
    # "For all languages beyond Finnish (L=9), use command S51700." 505
    # carries a one-digit language and 517 a two-digit one; the setting is
    # the same setting, so the store is 517's and 505 reads and writes it.
    #
    # Two things about the shape, both off the capture. The value sits on the
    # line BELOW its label, which is 518's shape and what `wire_block`
    # describes for a single field; and the THIRD row is not this code's
    # value at all -- the date/time format is 50F's field. So the body is a
    # list of (label, field, indent) rather than anything either code holds
    # on its own. The third row's value carries no leading space where the
    # first two do, which is the console's, not a transcription slip: the
    # capture puts ` U.S.` and ` ENGLISH` one column in and
    # `MON DD YYYY HH:MM:SS xM` hard against the margin.
    #
    # See FIDELITY S17, whose reading of 505 as "a group inquiry rather than
    # 517's older name" was half right: it is both.
    GROUP_ROWS = {
        "517": (("SYSTEM UNITS", "S51700.units", " "),
                ("SYSTEM LANGUAGE", "S51700.lang", " "),
                ("SYSTEM DATE/TIME FORMAT", "S50F00", "")),
    }
    #: {the older name: the code whose report and store it shares}
    GROUP_ALIAS = {"505": "517"}

    def _group_answer(self, tok, code):
        """The Display answer for a code whose body is label-over-value rows.

        None if this is not one of them, so the caller carries on.
        """
        from .console import FIELDS
        rows = self.GROUP_ROWS.get(self.GROUP_ALIAS.get(tok, tok))
        if not rows:
            return None
        entry = WIRE_TITLES.get(self.GROUP_ALIAS.get(tok, tok)) or {}
        out = [entry.get("title") or ""]
        out.append("")                       # the blank the capture draws
        for label, key, indent in rows:
            field = FIELDS.get(key)
            if field is None:
                continue
            owner = field.get("code") or key.split(".")[0]
            shown = self._part_shown(field, owner, self.c.values.get(owner))
            if shown in (None, ""):
                continue
            out.append(label)
            out.append(f"{indent}{shown}")
        return self._frame(code, SEP.join(out)), "a label-over-value group"

    def _leak_history_display(self, tanks):
        """207's and 212's display body, which the manual prints alike.

        The title over every tank, a blank line either side of the tank's
        head, and three blank lines between tanks: a version 23 site
        (2026-10-07). The sample prints the title once.
        """
        rows = []
        for i, tank in enumerate(tanks):
            label = self.c.text("602", tank) or f"TANK {tank}"
            if i:
                rows += ["", "", ""]
            rows += ["TANK LEAK TEST HISTORY", "", f"T {tank}:{label}", ""]
            rows += self.c.leak_history_lines(tank)
        return SEP.join(rows)

    def _head_gap(self, tok):
        """Blank lines between the frame and the first line of the body.

        Not the constant this console took it for. 512 of the manual's
        541 Display samples leave one and 29 leave none -- 613, 614, 902,
        903, 905 and 881 among them -- and a real console agrees with the
        page on every one of those that was captured. `build_wire_titles.py`
        measures it off the word boxes the same way it measures a column
        position, and it is `head_gap` in `wiretitles.json`. A code the
        manual does not draw keeps the commoner answer. See FIDELITY S6.
        """
        if tok in MEASURED_FRAME:
            return MEASURED_FRAME[tok]["head_gap"]
        entry = WIRE_TITLES.get(wiretables.SHOWN_AS.get(tok, tok))
        if entry is None or entry.get("head_gap") is None:
            return 1
        return max(int(entry["head_gap"]), 0)

    @staticmethod
    def _has_parts(tok):
        """Whether this code exists only as part fields."""
        from .console import FIELDS
        return any(key.startswith(f"S{tok}") and "." in key for key in FIELDS)

    def _part_shown(self, part, key, val):
        """One part field's value, or the default the panel shows for it.

        881 holds a port's baud rate, parity, stop bits and data length as
        parts of one record, and a port nobody has been near still runs at
        1200, none, one and eight -- which is what the panel reads and what
        the wire owed. See FIDELITY S9.

        `wire_default` is for the one place the two surfaces disagree about
        what "nothing programmed" looks like. A port with no RS-232 security
        code prints `CODE : DISABLED` on the setup report -- real paper, and
        the panel screen behind it -- and `000000` in the SECURITY CODE
        column of `I50400`, beside a STATUS column that says DISABLED. Same
        console, same state, two surfaces. See FIDELITY S14.
        """
        from .screens import shown as panel_shows
        if val is None and key[1:4] in ("605", "606") \
                and (part.get("part") or [None])[0] == 0 and key[4:6].isdigit():
            # a chart's first figure is the tank's full volume (see
            # `_blank_stored`): the bench's I60500 prints 200000 there
            full = (self.c.limit("604", int(key[4:6]))
                    or self.c.limit("60A", int(key[4:6])))
            if full:
                return units.shown("volume", f"{full:.0f}",
                                   units.system(self.c))
        if val is None:
            if part.get("wire_default") is not None:
                return part["wire_default"]
            return panel_shows(self.c, part, "")
        return fieldio.decode(part, key, val)

    def _part_field_answer(self, tok, dev, val, code):
        """The Display answer for a code that exists only as part fields.

        None if this is not one of them, so the caller carries on.
        """
        from .console import FIELDS
        if not any(key.startswith(f"S{tok}") and "." in key for key in FIELDS):
            return None
        title = (WIRE_TITLES.get(tok) or {}).get("title") or ""
        rows = [title] if title else []
        if tok not in self.STAMP_IS_THE_ANSWER:
            device = int(dev) if dev.isdigit() and dev != "00" else 1
            for step in self._steps_for(tok):
                # The panel's own rules decide which parts apply. 611 holds
                # four mutually exclusive schedules at the same offset -- the
                # `611schedule` alt group -- and only the one the test method
                # selects is real; listing all four would report a monthly
                # schedule on a console testing annually. `visible()` is what
                # the panel walks, so the wire and the panel cannot drift.
                if not self.c.visible(step, device):
                    continue
                part = FIELDS.get(step.get("field") or "")
                if not part:
                    continue
                shown = self._part_shown(part, f"S{tok}{dev}", val)
                if shown in (None, ""):
                    continue
                # The console's own name for the screen. What the manual
                # prints instead is a column heading over a table, and its
                # spacing cannot be recovered from the text extraction -- the
                # same reason the `heading` in WIRE_TITLES is unused. See
                # FIDELITY S1, which is where that geometry is tracked.
                name = step.get("setup_scope") or part.get("label") or ""
                rows.append(f"{name.upper()}: {shown}")
            if len(rows) == bool(title):
                # No panel step claimed a part -- 551's two parts have no
                # setup screen at all, and 51B's and 532's are gated on a
                # feature this console has switched off. The value is
                # programmed either way and the wire is entitled to it, so
                # fall back to the parts themselves rather than answer a
                # bare title.
                for key, part in sorted(FIELDS.items()):
                    if not (key.startswith(f"S{tok}") and "." in key and part):
                        continue
                    shown = self._part_shown(part, f"S{tok}{dev}", val)
                    if shown not in (None, ""):
                        rows.append(f"{part.get('label', key).upper()}: "
                                    f"{shown}")
        return (self._frame(code, chr(10).join(rows)),
                "decoded from the part fields")

    @staticmethod
    def _steps_for(tok):
        """The setup steps that write parts of one function's data, in the
        order the panel asks for them."""
        from .console import SETUP_MENU
        out = []
        for fn in SETUP_MENU:
            for step in fn.get("steps", []):
                field = step.get("field") or ""
                if field.startswith(f"S{tok}") and "." in field:
                    out.append(step)
        return out

    # ---- Set Ticketed Delivery and Set BOL, 7B5 and 7B6 ---------------------
    #
    # These two are the only codes on this shelf that document a RESULT code,
    # and every rejection used to come back as the generic `9999`, which the
    # manual defines as "a function code that it does not recognize". The
    # console recognised it perfectly well; it would not say what was wrong
    # with the data. See FIDELITY S2.
    #
    # "RR - Result code - if an error occurs, just error code will be
    # returned", 576013-635 Rev AA p.439 and p.441. 7B5 has eleven of them
    # and 7B6 has nine: the last two are about a VOLUME, which 7B6 does not
    # carry, so its list stops at 08. Two tables, not one.
    TICKET_RESULTS = {
        "ok": "00", "no_bir": "01", "bad_tank": "02", "no_stamp": "03",
        "not_numeric": "04", "bad_date": "05", "bad_time": "06",
        "out_of_period": "07", "no_match": "08", "bad_volume": "09",
        "already_gauged": "10",
    }
    VOLUME_RESULTS = ("bad_volume", "already_gauged")

    # p.438's own report, counted off the rendered page at six points a
    # character: TICKET at 25, GAUGE at 41, VARIANCE at 55, with the values
    # right-aligning to 31, 47 and 61.
    TICKET_HEAD = (" " * 25 + "TICKET" + " " * 10 + "GAUGE" + " " * 9
                   + "VARIANCE",
                   " " * 25 + "VOLUME" + " " * 10 + "VOLUME")
    # and p.440's, which is a different report with different columns: BOL at
    # 24, TICKET at 37, GAUGE at 49, TC GAUGE at 57, values to 28, 42, 53, 63.
    BOL_HEAD = (" " * 24 + "BOL" + " " * 10 + "TICKET" + " " * 6 + "GAUGE"
                + " " * 3 + "TC GAUGE",
                "DELIVERY END DATE" + " " * 7 + "NUMBER" + " " * 7 + "VOLUME"
                + " " * 5 + "VOLUME" + " " * 4 + "VOLUME")

    def _ticket_fault(self, tok, dev, edit, stamp, rest, computer):
        """Which of the manual's result codes this command earns, or None.

        In the manual's own order, which is also the order a console would
        have to check them in: what it is set up for, then who it is
        addressed to, then whether the data is there, then whether it parses,
        then whether it means anything.
        """
        if not self.c.licensed("bir"):
            return "no_bir"
        tank = int(dev) if dev.isdigit() else 0
        if not tank or tank not in self.c.tank_level:
            return "bad_tank"
        if len(stamp) < 10:
            return "no_stamp"
        if not stamp.isdigit():
            return "not_numeric"
        try:
            when = time.mktime(time.strptime(stamp, "%y%m%d%H%M"))
        except ValueError:
            # 05 and 06 are separate codes, so which half of the stamp is
            # wrong decides which one comes back -- and the date half is
            # wrong when it is not a DATE, not only when its month or its day
            # is out of range on its own. The 30th of February has a legal
            # month and a legal day, and answered BAD TIME. FIDELITY S24.
            try:
                time.strptime(stamp[:6], "%y%m%d")
            except ValueError:
                return "bad_date"
            return "bad_time"
        if when > time.mktime(self.c.now()):
            # "Date out of range of period (curr & prev via BIR)": a delivery
            # that has not happened yet is in no period at all
            return "out_of_period"
        record = self.c.deliveries.find(tank, stamp)
        if edit == "01" and record is None:
            return "no_match"
        if tok == "7B6":
            return None
        if edit == "02" and record is not None:
            return "already_gauged"
        try:
            (packed.unhexfloat(rest) if computer and len(rest) == 8
             else float(rest or 0))
        except ValueError:
            return "bad_volume"
        return None

    def _set_ticket(self, tok, dev, data, code):
        """7B5 and 7B6: edit or insert a ticket against a gauged delivery."""
        if not self.c.has("probe"):
            return self._absent(code), "no probe module fitted"
        computer = code[0].islower()
        edit, stamp, rest = data[:2], data[2:12], data[12:]
        fault = self._ticket_fault(tok, dev, edit, stamp, rest, computer)
        if fault:
            return (self._ticket_reply(tok, dev, code, fault, None),
                    f"REJECTED: {self.TICKET_RESULTS[fault]} {fault}")
        tank = int(dev)
        volume = None
        if tok == "7B5":
            # The two forms carry the volume differently and the command
            # format says so: Display is "GGGGGG", six decimal digits, and
            # Computer is "FFFFFFFF", an ASCII hex IEEE float. This read both
            # with float(), which throws on every well-formed computer
            # message, so a host could not set a ticketed delivery at all.
            volume = (packed.unhexfloat(rest)
                      if computer and len(rest) == 8 else float(rest or 0))
        record = self.c.deliveries.find(tank, stamp)
        if record is None:
            when = time.mktime(time.strptime(stamp, "%y%m%d%H%M"))
            record = self.c.deliveries.insert(
                tank, when, 0 if volume is None else volume)
        if tok == "7B6":
            record.bol = rest.strip()[:20]
        else:
            record.ticket = volume
        self.c.save()
        return (self._ticket_reply(tok, dev, code, "ok", record),
                "ticketed delivery stored")

    def _ticket_reply(self, tok, dev, code, result, record):
        """The response, which is a REPORT on the display side and a record
        on the computer side -- and on the computer side an error is the
        result code and nothing after it."""
        tank = int(dev) if dev.isdigit() and int(dev) else 1
        rr = self.TICKET_RESULTS[result]
        if code[0].islower():
            body = (f"{tank:02d}{(self.c.text('603', tank) or ' ')[:1] or ' '}"
                    f"{self.c.probe_type_code(tank)}{rr}")
            if record is None:
                return self._frame(code, body)
            body += time.strftime("%y%m%d%H%M",
                                  time.localtime(record.end["at"]))
            if tok == "7B6":
                bol = record.bol or ""
                body += f"{len(bol):02X}" + bol
                body += packed.hexfloats([record.ticket or 0.0,
                                          record.amount, record.tc_amount])
            else:
                body += packed.hexfloats([record.ticket or 0.0, record.amount,
                                          (record.variance() or 0.0)])
            return self._frame(code, body)
        if record is None:
            # No error form is drawn for the display side anywhere on this
            # shelf -- the result code is a field of the COMPUTER response --
            # so the display side says what went wrong in the manual's own
            # words rather than inventing a report shape for it.
            return self._frame(code, SEP.join(
                ["SET TICKETED DELIVERY" + (" BOL NUMBER" if tok == "7B6"
                                            else ""),
                 f"RESULT CODE {rr}: {result.replace('_', ' ').upper()}"]))
        label = self.c.text("602", tank) or f"TANK {tank}"
        stamp = clock_words(record.end["at"])
        if tok == "7B6":
            rows = ["SET TICKETED DELIVERY BOL NUMBER"]
            rows += list(self.BOL_HEAD)
            rows.append(f"{stamp:21.21s}  {(record.bol or ''):<12.12s}"
                        f"{record.ticket or 0.0:>7.1f}"
                        f"{record.amount:>11.1f}"
                        f"{record.tc_amount:>10.1f}")
        else:
            rows = ["SET TICKETED DELIVERY",
                    "VOLUMES ARE " + ("TC" if (self.c.values.get("S51D00")
                                               or "").strip().endswith("1")
                                      else "STANDARD"),
                    f"T {tank}:{label}"]
            rows += list(self.TICKET_HEAD)
            rows.append(f"{stamp:21.21s}{record.ticket or 0.0:>11.1f}"
                        f"{record.amount:>16.1f}"
                        f"{(record.variance() or 0.0):>14.1f}")
        return self._frame(code, SEP.join(rows))

    # ---- the Service Notice block, 566 to 569 -------------------------------
    def _service_notice_read(self, tok, code):
        """567, 568 and 569, whose values are not in `values`.

        The manual prints all three display lines, so they are quotations
        rather than readings: "SERVICE NOTICE DELIVERY OVERRIDE: DISABLED",
        "SERVICE NOTICE SESSION: DISABLED", "SERVICE NOTICE SESSION DURATION:
        2 HOURS". 576013-635 Rev AA pp.234-236.
        """
        c = self.c
        display = code[0].isupper()
        if tok == "567":
            on = c.delivery_override()
            if display:
                return (self._frame(code, "SERVICE NOTICE DELIVERY OVERRIDE: "
                                    + ("ENABLED" if on else "DISABLED")),
                        "service notice delivery override")
            return self._frame(code, "1" if on else "0"), "delivery override"
        if tok == "568":
            # Not a stored value: the session either is open or it is not,
            # and asking the console is the only answer that cannot disagree
            # with 11B's own report of the same thing.
            on = c.service_session() is not None
            if display:
                return (self._frame(code, "SERVICE NOTICE SESSION: "
                                    + ("ENABLED" if on else "DISABLED")),
                        "service notice session")
            return self._frame(code, "1" if on else "0"), "service session"
        hours = c.service_session_hours()
        if display:
            return (self._frame(code,
                                f"SERVICE NOTICE SESSION DURATION: "
                                f"{hours} HOURS"), "session duration")
        return self._frame(code, f"{hours:02d}"), "session duration"

    def _service_notice_set(self, tok, code, data):
        """The three Sets, and 566's, which all carry a LEADING 149.

        `VERIFIED` strips a TRAILING verification code -- `S53000x149` -- and
        this block's is at the front, `S56600149f`, so it cannot serve them.
        566 had no template at all and no leading-149 handling either, so the
        one Service Notice code that DID read back could not be written.
        """
        c = self.c
        if tok != "569":
            if not data.startswith("149"):
                return (self._nine(code),
                        f"REJECTED: {tok} wants a leading 149")
            flag = data[3:4]
            if flag not in ("0", "1"):
                return self._nine(code), "REJECTED: 1=ENABLED, 0=DISABLED"
            if tok == "566":
                c.values["S56600"] = flag
                c.save()
                return self._frame(code), f"service notice {_ON_OFF[flag]}"
            if tok == "567":
                c.set_setting("delivery_override",
                              "ENABLED" if flag == "1" else "DISABLED", 0)
                c.save()
                return (self._frame(code),
                        f"delivery override {_ON_OFF[flag]}")
            if not c.service_notice():
                # "Only appears if Service Notice feature has been enabled":
                # there is no session to open on a console whose feature
                # switch is off.
                return self._nine(code), "REJECTED: service notice is off"
            said = (c.start_service_session() if flag == "1"
                    else c.end_service_session())
            if said.startswith("DISABLED DEL"):
                return self._nine(code), "REJECTED: delivery in progress"
            return self._frame(code), f"service session {said.lower()}"
        # 569, "hh - Service Notice Session Duration in Hours (Decimal)"
        lo, hi = self.c.SESSION_HOURS
        if not data.isdigit() or not lo <= int(data) <= hi:
            return self._nine(code), f"REJECTED: {lo} to {hi} hours"
        c.set_setting("service_duration", str(int(data)), 0)
        c.save()
        return self._frame(code), f"session duration {int(data)} hours"

    # ---- write -------------------------------------------------------------
    def set_(self, tok, dev, data, code):
        if tok == "535":
            return self._set_hangup(dev, code, data)
        if tok in ("537", "538"):
            # two raw characters, stored as `Console.etx_chars` keeps them,
            # and answered with the port's table (2026-09-25). A port that
            # is not one is unmeasured for a Set; it answers as its inquiry.
            port = self.c.etx_port(dev)
            if port is None:
                return self._frame(code), "no such port"
            self.c.values[f"S{tok}{port:02d}"] = self.c.etx_chars(data)
            self.c.save()
            return (self._etx_table(tok, port, code),
                    "ETX characters " + ("cleared" if not self.c.values[
                        f"S{tok}{port:02d}"] else "set"))
        if tok == "55D":
            return self._set_precision_print(code, data)
        if tok == "7C3" and self.c.has("probe"):
            return self._set_max_volume(dev, code, data)
        if tok in INQUIRE_ONLY or tok in UNDOCUMENTED_BARE:
            # an undocumented code was only ever asked, never set
            return (self._nine(code),
                    f"{tok} is an Inquire with no Set format")
        if tok in SETTABLE and self._short_packed(tok, dev, data, code):
            return self._frame(code), "short of its width: acked, nothing stored"
        gated = self._bench_set_gate(tok, dev, code, data)
        if gated is not None:
            return gated
        # The list-shaped setup codes parse their own data, because its width
        # is decided by the data itself rather than by the field definition.
        # They are asked before the generic path, which would otherwise store
        # the whole payload as one opaque string -- which is exactly what
        # `raw` used to mean.
        for family in EXTRA_SETS:
            answered = family(self, tok, dev, code, data)
            if answered is not None:
                return answered
        # 75 setup codes are reachable only over the wire -- no panel step, so
        # no field, so nothing ever checked what they were given and they took
        # anything. The manual writes each one's data as a template and
        # `formats` checks the SHAPE against it: the length and the character
        # class, never the range. Checked here, before the 149 is stripped,
        # because several of these templates include the 149.
        if tok in TICKET_CODES:
            # 7B5 and 7B6 answer the manual's own RESULT CODE for exactly the
            # failures this check would refuse with 9999 -- 03 missing
            # time/date, 04 not numeric, 09 invalid volume -- so the generic
            # shape check must not get there first. See FIDELITY S2.
            return self._set_ticket(tok, dev, data, code)
        shaped = self._as_read(tok, dev, data, code)
        if (tok in SETTABLE and data and shaped is not None
                and not self._label_code(tok) and not formats.valid(
                tok, shaped,
                aggregate=(dev == "00" and self.c.is_multi(tok)),
                computer=code[0].islower())):
            # Refused the way the bench refuses a value it cannot take --
            # `S78901ABC` answers `???` -- not with 9999FF, which the manual
            # keeps for a function code the console does not know.
            return (self._refused(code, data, tok, dev),
                    f"REJECTED: does not fit {tok}'s data format")
        if tok in SERVICE_NOTICE or tok == "566":
            return self._service_notice_set(tok, code, data)
        verify = VERIFIED.get(tok)
        if verify:
            if not data.endswith(verify):
                return (self._nine(code),
                        f"REJECTED: {tok} wants the {verify} verification code")
            data = data[:-len(verify)]
        if tok in ISD_BUFFERS:
            # "Set command clears buffer", and it confirms at the front too
            if not self._vp_full_control():
                return (self._absent(code),
                        "needs PMC and full vapor processor control")
            if not data.startswith("149"):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            if tok == "V80":
                self.c.vp_cycles = []
            else:
                self.c.hc_cleared = time.mktime(self.c.now())
            return self._frame(code), "buffer cleared"
        if tok in ISD_CONTROL:
            if tok in ("VC0", "VC1", "VC8"):
                if not self.c.licensed("pmc"):
                    return self._absent(code), "needs the PMC software module"
                if not (self.c.has("relay") or self.c.has("io")):
                    # "PMC Feature and Vapor Processor relay required"
                    return self._nine(code), "no vapor processor relay"
            elif not self.c.licensed("isd"):
                return self._absent(code), "needs the ISD software module"
            if not data.startswith("149"):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            body = data[3:]
            if tok == "VC0":
                if body[:1] not in isd.VP_CONTROL:
                    return self._nine(code), "REJECTED: value out of range"
                # "changing from automatic to manual while VP is on turns VP
                # (and HC sensor) off"
                if (body[:1] == isd.VP_MANUAL
                        and self._vp_control() == isd.VP_AUTOMATIC):
                    self.c.values["SVC100"] = "0"
                    self.c.vapor_processor_on(False)
                self.c.values["SVC000"] = body[:1]
                self.c.save()
                return self._frame(code), "stored"
            if tok == "VC1":
                if body[:1] not in isd.VP_RUNNING:
                    return self._nine(code), "REJECTED: value out of range"
                if self._vp_control() != isd.VP_MANUAL:
                    # "VP control MUST be Manual (see VC0 command)"
                    return self._nine(code), "REJECTED: VP control is automatic"
                self.c.values["SVC100"] = body[:1]
                self.c.vapor_processor_on(body[:1] == "1")
                self.c.save()
                return self._frame(code), "stored"
            if tok == "VC5":
                # "Set command acknowledges alarm", and there is no data on it
                self.c.values["SVC500"] = isd.OVERRIDDEN_YES
                self.c.save()
                return self._frame(code), "isd shutdown alarms overridden"
            if tok == "VC8":
                if body[:1] not in isd.VALVE:
                    return self._nine(code), "REJECTED: value out of range"
                if self._vp_control() != isd.VP_MANUAL:
                    return self._nine(code), "REJECTED: VP control is automatic"
                if (self.c.values.get("SV4000") or "00") != isd.POLISHER:
                    # "Vapor Processor Type must be Veeder-Root Polisher"
                    return self._nine(code), "REJECTED: not a Veeder-Root polisher"
                self.c.values["SVC800"] = body[:1]
                self.c.save()
                return self._frame(code), "stored"
            if tok == "XE0":
                self.c.values["SXE000"] = body[:8]
                self.c.save()
                return self._frame(code), "stored"
            # V85: clear a test's failure, and note when it was cleared
            test, fp, hose = body[0:2], body[2:4], body[4:6]
            if test not in dict(isd.SERVICE_TESTS):
                return self._nine(code), "REJECTED: no such test type"
            stamp = time.strftime("%y%m%d", self.c.now())
            # 577013-819 Rev F Table 2 is a THREE column map and this wrote
            # the third: the date. The alarms the selection clears are the
            # second column. See FIDELITY I4.
            self.c.clear_isd_test(test, fp, hose)
            if test != isd.COLLECTION:
                self.c.values[f"SV85{test}"] = stamp
            elif fp == "00":
                for key in [k for k in list(self.c.values)
                            if k.startswith("SV85C")]:
                    self.c.values.pop(key, None)
            elif hose == "00":
                for key in [k for k in list(self.c.values)
                            if k.startswith(f"SV85C{fp}")]:
                    self.c.values.pop(key, None)
            else:
                self.c.values[f"SV85C{fp}{hose}"] = stamp
            self.c.save()
            return self._frame(code), "test fail cleared"
        if tok in recon.RECON or tok in ("C03", "C04"):
            # Sections 7.5 and 7.6 are reports and nothing else: not one of
            # them has a Set form. Closing a shift is 79D and clearing the
            # tank map is 79E, both out in the configuration range.
            return self._nine(code), "REJECTED: inquire only"
        if tok in ("79D", "79E", "882", "8A4"):
            # Four actions whose verification is in four different places,
            # which is the whole reason they are handled here rather than
            # falling through the generic setup path:
            #
            #   79E   S79E00149          trailing, and set-only
            #   882   S882PP149          trailing
            #   8A4   S8A400149cccccc    LEADING, the only one in the manual
            #   79D   S79D00ff           none at all
            #
            # 79D and 79E sit next to each other and are not a pair: 79E is
            # gated and echoes S, 79D is ungated and echoes I.
            body = data or ""
            if tok == "8A4":
                if not body.startswith("149"):
                    return (self._nine(code),
                            "REJECTED: wants a leading 149")
                ident = body[3:9].strip()
                if not ident:
                    return self._nine(code), "REJECTED: no key to block"
                self.c.block_tracker_key(ident)
                return self._frame(code), f"key {ident} blocked"
            if tok in ("79E", "882"):
                if not body.strip().endswith("149"):
                    return (self._nine(code),
                            "REJECTED: wants a trailing 149")
                if tok == "79E":
                    self.c.meters.clear()
                    self.c.save()
                    if code[0].isupper():
                        # this one echoes the SET, where its neighbour 79D
                        # echoes the inquire
                        return (self._frame(code, SEP.join(
                            ["RECONCILIATION CLEAR MAPS",
                             "MAPS TABLE CLEARED"])), "tank map cleared")
                    return self._frame(code, "01"), "tank map cleared"
                port = int(dev) if dev.isdigit() and int(dev) else 1
                for key in ("881", "885", "886", "887"):
                    self.c.values.pop(f"S{key}{port:02d}", None)
                self.c.save()
                return self._frame(code), f"port {port} initialised"
            # 79D, which has no verification code of any kind and whose data
            # field the manual never defines on the Set side: the only value
            # it attaches meaning to is 01, "Close shift pending".
            if body.strip()[:2] != "01":
                return self._nine(code), "REJECTED: wants 01"
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            rows = self.c.bir.close("shift")
            if code[0].isupper():
                return (self._frame(code, SEP.join(
                    ["MANUAL SHIFT CLOSE", "*** CLOSE SHIFT PENDING ***"])),
                    f"{len(rows)} tank(s) closed")
            return self._frame(code, "01"), f"{len(rows)} tank(s) closed"
        if tok in ISD_READ or tok in ISD_READ_ONLY:
            # "Inquire only, use Function Code V42 to set", and V10 is the ISD
            # software's own version number, which nobody sets over a wire.
            return self._nine(code), "REJECTED: inquire only"
        if tok in ("V42", "V43", "V49"):
            if not self.c.licensed("isd"):
                return self._absent(code), "needs the ISD software module"
            if tok in ("V42", "V43") and not data.startswith("149"):
                # both confirm at the FRONT, the way V44 does
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            body = data[3:] if tok in ("V42", "V43") else data
            if tok == "V42":
                if dev == "00":
                    # "00149 Clears all tables"
                    for key in [k for k in list(self.c.values)
                                if k.startswith("SV42")]:
                        self.c.values.pop(key, None)
                    self.c.save()
                    return self._frame(code), "all isd tables cleared"
                if isd.parse_row(body) is None:
                    return self._nine(code), "REJECTED: not a map row"
                if self.c.values.get(f"SV42{dev}"):
                    # "if one already exists, command will fail (clear all
                    # entries with SS=0 before setting up tables)"
                    return self._nine(code), "REJECTED: that map already exists"
                self.c.values[f"SV42{dev}"] = body
                self.c.save()
                return self._frame(code), "stored"
            if tok == "V43":
                index, flag = body[:2], body[2:3]
                if not index.isdigit() or index == "00" or flag not in "01":
                    return self._nine(code), "REJECTED: value out of range"
                self.c.values[f"SV43{index}"] = flag
                self.c.save()
                return self._frame(code), "stored"
            ident, text = body[:2], body[2:12]
            if ident not in isd.LABEL_IDS or ident == isd.LABEL_UNASSIGNED:
                # "II - Hose Label ID (02-10, 01=Unassigned)"
                return self._nine(code), "REJECTED: label id out of range"
            self.c.values[f"SV49{ident}"] = text.strip()
            self.c.save()
            return self._frame(code), "stored"
        if tok in isd.SETUP:
            if not self._isd_licensed(tok):
                spec = isd.SETUP[tok]
                want = " and ".join(k.upper() for k in spec["needs"])
                return self._absent(code), f"needs the {want} software module"
            stored = self._isd_store(tok, data)
            if stored is None:
                return self._nine(code), "REJECTED: value out of range"
            self.c.values[f"S{tok}00"] = stored
            self.c.save()
            return self._frame(code), "stored"
        if tok == "63B" and self.c.chart_secured():
            # "Set command is only valid if Tank Chart Security is disabled"
            return self._nine(code), "REJECTED: tank chart security enabled"
        if tok in ("55E", "642"):
            value = (data or "").strip()[-1:]
            if tok == "55E":
                if value not in ("0", "1"):
                    return self._nine(code), "REJECTED: 0 or 1"
                self.c.set_setting("fiscal_height",
                                   "ENABLED" if value == "1" else "DISABLED", 0)
            else:
                level = self.WATER_FILTER.get(value)
                if level is None:
                    return self._nine(code), "REJECTED: 1, 2 or 3"
                for tank in self._tanks(dev):
                    self.c.set_setting("water_filter", level, tank)
            self.c.save()
            return self._frame(code), "stored"
        if tok in ("851", "852", "853"):
            # "7.3.13 EEPROM SETUP": Restore, Save and Clear All Setup Data
            if tok == "852":
                n = self.c.archive_save()
                note = f"{n} value(s) saved to EEPROM"
            elif tok == "851":
                n = self.c.archive_restore()
                if n < 0:
                    return self._nine(code), "REJECTED: no archive in EEPROM"
                note = f"{n} value(s) restored from EEPROM"
            else:
                n = self.c.archive_clear()
                note = f"{n} value(s) cleared from EEPROM"
            if n < 0:
                return self._nine(code), "REJECTED: EEPROM not writable"
            return self._frame(code), note
        if tok == "891":
            # Set AccuChart Calibration Restart, one tank only
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            if dev == "00":
                return (self._nine(code),
                        "REJECTED: command valid for single tank only")
            said = self.c.accuchart.restart(int(dev))
            return self._frame(code), said.lower()
        if tok in controls.SYSTEM_ACTIONS:
            what, said = controls.SYSTEM_ACTIONS[tok]
            if tok == "031":
                # The one in the section that does not use 149. It carries
                # 832382 and no manual here says why.
                if controls.CONFIRM_CLEAR not in (data or ""):
                    return (self._nine(code),
                            f"REJECTED: wants {controls.CONFIRM_CLEAR}")
            note = self.c.control_action(what)
            if code[0].isupper() and said:
                return self._frame(code, said), note
            return self._frame(code), note
        if tok in ("089", "090"):
            kind = "plld" if tok == "089" else "wplld"
            if not self.c.has(kind):
                return self._absent(code), f"no {kind} module fitted"
            if "149" not in (data or ""):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            for number in self._devices_of(kind, dev):
                self.c.lines.line(kind, number).reset_offset()
            if code[0].isupper():
                first = self._devices_of(kind, dev)[0]
                label = self.c.text("782" if kind == "plld" else "7A2",
                                    first) or ""
                return (self._frame(code, SEP.join(
                    [f"{self.c.lines.code(kind)} {first}:{label}",
                     "PRESSURE OFFSET RESET"])), "pressure offset reset")
            # "no data echoed back": the acknowledgement is the whole reply
            return self._frame(code), "pressure offset reset"
        if tok in ("087", "088"):
            kind = "plld" if tok == "087" else "wplld"
            if not self.c.has(kind):
                return self._absent(code), f"no {kind} module fitted"
            body = (data or "")
            if not body.startswith("149"):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            want = body[3:5]
            if want not in controls.TEST_TYPE:
                return self._nine(code), "REJECTED: no such test type"
            lines = self._devices_of(kind, dev)
            for number in lines:
                self.c.leaks.start(kind, number, controls.TEST_TYPE[want],
                                   origin="wire")
            first = self.c.lines.line(kind, lines[0] if lines else 1)
            # The two status tables are NOT the same table, see controls.py
            table = (controls.PLLD_TEST_STATUS if tok == "087"
                     else controls.WPLLD_TEST_STATUS)
            state = self._control_state(first, table)
            if code[0].isupper():
                label = self.c.text("782" if kind == "plld" else "7A2",
                                    first.number) or ""
                return (self._frame(code, SEP.join(
                    [f"{self.c.lines.code(kind)} {first.number}:{label}",
                     f"{controls.TEST_TYPE_NAME[want]} SCHEDULED",
                     f"STATUS: {table[state]}"])), "line leak test started")
            return (self._frame(code,
                                f"{first.number:02d}{want}{state}"),
                    "line leak test started")
        if tok in controls.DEVICE_ACTIONS:
            what, table, banner = controls.DEVICE_ACTIONS[tok]
            module = "plld" if what.startswith("profile") else "smart"
            if not self.c.has(module):
                return self._absent(code), f"no {module} module fitted"
            if "149" not in (data or ""):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            # a sump test runs on the Mag sensors, not on every position
            devices = (self._mag_devices(dev) if what.startswith("sump")
                       else self._devices_of(module, dev))
            rows = []
            for number in devices:
                state = self.c.control_device(what, number)
                rows.append((number, state))
            if not rows:
                # a console with no Mag sensor programmed has none to test
                return (self._frame(code, banner if code[0].isupper()
                                    else ""), what)
            if code[0].isupper():
                letter = "Q" if module == "plld" else "s"
                first, state = rows[0]
                label = (self.c.text("782", first) if module == "plld"
                         else self.c.text("722", first)) or ""
                return (self._frame(code, SEP.join(
                    [banner, f"{letter} {first}:{label}",
                     f"STATUS: {table.get(state, state)}"])), what)
            return (self._frame(code,
                                "".join(f"{n:02d}{s}" for n, s in rows)), what)
        if tok == "091":
            # Close Current Shift
            if not self.c.licensed("bir"):
                return self._absent(code), "BIR not installed"
            rows = self.c.bir.close("shift")
            return self._frame(code), f"{len(rows)} tank(s) closed"
        if tok == "054":
            # Delete CSLD Rate Table, which wants its verification code
            if not data.strip().endswith("149"):
                return self._nine(code), "REJECTED: verification code 149"
            n = self.c.csld.delete_table(int(dev) if dev != "00" else 1)
            return self._frame(code), f"{n} CSLD sample(s) deleted"
        if tok == "051":
            # Clear In-Tank Delivery Reports
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            n = self.c.deliveries.clear(None if dev == "00" else int(dev))
            return self._frame(code), f"{n} delivery report(s) erased"
        if tok in ("052", "053"):
            # Start / Stop In-Tank Leak Detect Test, which a tool can do too
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            devices = ([int(dev)] if dev != "00"
                       else sorted(self.c.tank_level))
            for tank in devices:
                if tok == "052":
                    self.c.leaks.start("tank", tank, "periodic", hours=2.0,
                                       origin="wire")
                else:
                    self.c.leaks.stop("tank", tank)
            running = "1" if self.c.leaks.active("tank", devices[0] if devices
                                                 else 0) else "0"
            return (self._frame(code, f"{int(dev):02d}{running}"),
                    "leak test started" if tok == "052" else "leak test stopped")
        if tok in ("081", "082", "083", "084"):
            # Start / Stop Pressure Line Leak Test, and the WPLLD pair.
            # "QQ - Pressure Line Leak sensor number (Decimal, 00=All)", and
            # the response is that line's test status either way.
            kind = "plld" if tok in ("081", "082") else "wplld"
            if not self.c.has(kind):
                return self._absent(code), f"no {kind} module fitted"
            lines = self._devices_of(kind, dev)
            for number in lines:
                if tok in ("081", "083"):
                    # "Start Pressure Line Leak Test (3.00 GPH only in V18)":
                    # 3.0 is the one every version has, and the sequence runs
                    # on from it to whatever else is scheduled
                    self.c.leaks.start(kind, number, "gross", origin="wire")
                else:
                    self.c.leaks.stop(kind, number)
            first = self.c.lines.line(kind, lines[0] if lines else 1)
            if code[0].isupper():
                label = self.c.text("782" if kind == "plld" else "7A2",
                                    first.number) or ""
                return (self._frame(code, SEP.join(
                    [f"{self.c.lines.code(kind)} {first.number}:{label}",
                     f"STATUS: {first.status()}"])),
                        "line leak test " + ("started" if tok in ("081", "083")
                                             else "stopped"))
            return (self._frame(code, f"{first.number:02d}{first.status_code()}"),
                    "line leak test " + ("started" if tok in ("081", "083")
                                         else "stopped"))
        if tok == "61F":
            # "S61FTTtdd.ddddd", where t is the delivery type and the rest is
            # the density AS ENTERED -- "relative, actual or API". The value
            # is kept the way it arrived, which is what the console reports
            # back on an inquiry. See FIDELITY O13.
            if not self.c.has("probe"):
                return self._absent(code), "no probe module fitted"
            which, rest = (data or "")[:1], (data or "")[1:]
            if which not in ("0", "1"):
                return self._nine(code), "REJECTED: delivery type is 0 or 1"
            try:
                value = (packed.unhexfloat(rest) if code[0].islower()
                         else float(rest))
            except (ValueError, TypeError):
                return self._nine(code), "REJECTED: not a density"
            for tank in self._tanks(dev):
                self.c.set_delivery_density(tank, which, value)
            return self._frame(code), "delivery density stored"
        if not data:
            # Hardware-confirmed on a real console: a Set with no data field
            # just acks with the date/time and leaves the value alone.
            return self._frame(code), "empty data: acked, nothing stored"
        if not self._module_present(tok):
            # The bench TLS-350 answers `S7A1011` -- a WPLLD line switched on
            # in a console with no WPLLD card -- with the bare echo, and
            # stores nothing.
            return self._frame(code), "no module fitted: acked, nothing stored"
        fixed = self.SET_DEVICES.get(tok)
        if fixed and dev.isdigit() and int(dev) > fixed:
            # A device the code has no such number for: the bench refuses
            # `S50399X` -- header line 99 of four -- with one `?`.
            return (self._refused(code, data, tok, dev),
                    f"REJECTED: {tok} has devices 1 to {fixed}")
        family = self._set_family(tok)
        if family and dev.isdigit() and int(dev) > self.c.capacity(family):
            # A device past the positions the cage gives the family: the
            # bench answers `S60217X` -- tank 17 on a four-probe card -- with
            # the bare echo, and stores nothing.
            return self._frame(code), "no such position: acked, nothing stored"
        if (family and dev == "00" and code[:1].isupper()
                and self.c.is_prefixed(tok)):
            # Device 00 on a one-value-per-device Set is EVERY position: the
            # bench answers `S60200ALL` by labelling all four tanks ALL and
            # listing the four of them.
            stored = 0
            for n in range(1, self.c.capacity(family) + 1):
                one = self._canonical(tok, f"{n:02d}", data, code)
                if one is None:
                    return (self._refused(code, data, tok, dev),
                            "REJECTED: value out of range")
                self.c.values[f"S{tok}{n:02d}"] = self._with_prefix(
                    tok, f"{n:02d}", one)
                stored += 1
            self.c.save()
            return self._answered(tok, dev, code), f"stored on {stored}"
        value = self._canonical(tok, dev, data, code)
        if value is self.BARE:
            return self._frame(code), "short of its width: acked, nothing stored"
        if value is None:
            return (self._refused(code, data, tok, dev),
                    "REJECTED: value out of range")
        # An older name writes the store its newer one owns, or the console
        # answers the report it has just been programmed through with the
        # value it had before. 505's language is one digit and 517's is two
        # -- 576013-635 Rev AA p.156 note 1 -- so the narrow one widens on
        # the way in rather than sitting in a key nothing reads.
        if tok in self.GROUP_ALIAS:
            tok = self.GROUP_ALIAS[tok]
            value = value[:1] + value[1:].rjust(2, "0")
        if tok in self.LEGACY_TWINS:
            for twin in self.LEGACY_TWINS[tok]:
                self.c.values[f"S{twin}{dev}"] = value
            self.c.values.pop(f"S{tok}{dev}", None)
            self.c.save()
            return self._answered(tok, dev, code), "stored on its twins"
        # One value under two names, written through either: on the bench
        # TLS-350 `S60A01123456` made I604 answer 123456 where 604 had been
        # set to 999999 (2026-09-18). So a Set lands where the value already
        # is -- the twin's key, if only it is held -- or the twin set earlier
        # would go on shadowing it; and a dump restored through both names
        # comes back to the one key it started as.
        stamp_before = self.c.clock_stamp() if tok == "50F" else None
        keys = [f"S{tok}{dev}"]
        twin = self.c.SHARED_STORE.get(tok)
        if twin and f"S{twin}{dev}" in self.c.values:
            keys = ([f"S{twin}{dev}"] if keys[0] not in self.c.values
                    else keys + [f"S{twin}{dev}"])
        for key in keys:
            self.c.values[key] = self._with_prefix(tok, dev, value)
        if tok == "609" and dev.isdigit() and int(dev):
            # A coefficient set by hand is no longer the density's: on the
            # bench TLS-350 `S609010.00090` turned tank 1's entered density
            # from 99.9999 to 0.0000 (2026-09-18). A tank with none held
            # already reads 0.0000, so nothing is written for it. And only
            # while Mass/Density is on: with it off, `S609030.00000` left
            # tank 3's 0.8500 where it was, and with it on `S609030.00046`
            # zeroed it (2026-09-19, `transcripts/sdw9`).
            if (self.c.values.get(f"S61E{dev}") is not None
                    and self._mass_density_on()):
                self.c.values[f"S61E{dev}"] = dev + packed.hexfloat(0.0)
        if tok == "788" and dev.isdigit() and int(dev):
            # A pipe type clears the settings it has no use for: after Q1
            # went from USER DEFINED to fiberglass and on to Petrotechnik,
            # i777 to i77A answered nothing for it and its second length
            # 0 (the computer Set sweep, 2026-09-18). 77B and 77E kept theirs.
            n = int(dev)
            for other, keep in wiretables.ROW_FILTER.items():
                if other in self.UNGATED_PACKED or keep(self.c, n):
                    continue
                if other == "77F":
                    if self.c.values.get(f"S77F{dev}") is not None:
                        self.c.values[f"S77F{dev}"] = self._with_prefix(
                            "77F", dev, packed.hexfloat(0.0))
                else:
                    self.c.values.pop(f"S{other}{dev}", None)
        if tok in ("604", "60A", "63C") and dev.isdigit() and int(dev):
            # Setting a full volume sets the profile it belongs to. On the
            # bench TLS-350 `S60A010` turned tank 1's I60A row from 1 PT to
            # LINEAR, and `S60401200000` turned it back (2026-09-18); after
            # `s63C01` tank 1 read 50 PTS (2026-09-19, `cap_tank1on`).
            self.c.tank_profiles[int(dev)] = {"60A": "03", "604": "00",
                                              "63C": "04"}[tok]
            # And the three are ONE full volume. On the bench TLS-350
            # (2026-09-19, `transcripts/reset63c.log`) `S63C015000` made
            # I604 and I60A read 5000; `S60A01047000` made I604 read 47000
            # and I63C read 0; `S6040110000` made I63C read 0 again. So
            # 604 and 60A always read it, and 63C only while the tank is
            # on the fifty point profile -- which is why the Set sweep
            # found 63C at 0 after its last `S63C01999999`: a later
            # `S60401999999` had moved the tank off it.
            #
            # 604 and 60A are already one store (`SHARED_STORE`), so a 63C
            # Set writes that store as well -- 604's key, or 60A's where
            # only it is held -- and a 604 or 60A Set zeroes a 63C that is
            # held. Keys that are not held are not made: a dump restored
            # through all three names comes back to the keys it started as.
            if tok == "63C":
                shared = (f"S60A{dev}" if f"S60A{dev}" in self.c.values
                          and f"S604{dev}" not in self.c.values
                          else f"S604{dev}")
                self.c.values[shared] = self._with_prefix(
                    shared[1:4], dev, value)
            elif f"S63C{dev}" in self.c.values:
                self.c.values[f"S63C{dev}"] = self._with_prefix(
                    "63C", dev, packed.hexfloat(0.0))
        if tok == "61E":
            # the tank's thermal coefficient follows its entered density
            for tank in ([int(dev)] if dev != "00"
                         else range(1, self.c.capacity("probe") + 1)):
                if dev == "00":
                    self.c.values[f"S61E{tank:02d}"] = self._with_prefix(
                        tok, f"{tank:02d}", value)
                self.c.density_entered(tank)
        if tok == "501":
            # Set Time of Day sets the CLOCK, not just the stored string.
            # The panel path already did (ui calls set_clock after ENTER);
            # this path stored the digits and left the clock alone, so a
            # tool that set the time was answered politely and ignored.
            #
            # The reply is the bench TLS-350's (2026-09-19, `transcripts/
            # clockset` to `clockset4`): the echo, the clock as it WAS, and
            # the ten digits the console took, `S50100 / FEB 28, 2006  1:01
            # AM / 0601192359`. Packed, the same three in one frame. It had
            # been a bare echo stamped with the new time. A value that is
            # not a date and time is a `?` per character, where this sent
            # 9999FF, and leaves the stored digits alone.
            # and the device number is ignored: the bench took
            # `S501010601190035` and set the clock from it, answering with
            # its own `S50101` echo. The value belongs under the console's
            # one key, or the clock would be read from a position nothing
            # else looks at.
            if dev != "00":
                moved = self.c.values.pop(f"S501{dev}", None)
                if moved is not None:
                    self.c.values["S50100"] = moved
            digits = (self.c.values.get("S50100") or "")[:10]
            reply = self._clock_reply(code, digits)
            if not self.c.set_clock():
                # the refused digits are already in the store, and the clock
                # they would be read from on the next cold boot is the one
                # still running: put that back
                self.c.values["S50100"] = time.strftime("%y%m%d%H%M",
                                                        self.c.now())
                return (self._refused(code, data, tok, dev),
                        "REJECTED: not a date and time")
            self.c.save()
            return reply, "clock set"
        if tok == "683":
            # 683 is `D` then the volume, and D picks one day of the week:
            # 0 all days, 1 Sunday .. 7 Saturday (576013-635 Rev AA p.330).
            # The console holds seven values per product and the panel draws
            # seven screens for them, so the day has to be unpacked rather
            # than left inside one string.
            body = value[2:] if value[:2] == dev else value
            day, rest = body[:1], body[1:].strip()
            if not day or day not in "01234567":
                return self._nine(code), "REJECTED: D is 0 to 7"
            for one in (range(1, 8) if day == "0" else [int(day)]):
                if 1 <= one <= 7:
                    self.c.set_setting(f"avg_sales_{one}", rest,
                                       int(dev) if dev != "00" else 1)
        self.c.save()
        out = self._answered(tok, dev, code)
        if tok == "50F" and stamp_before and code[:1].isupper():
            # a new DATE/TIME FORMAT is answered under the stamp of the old:
            # `S50F0002` came back `JAN 16, 2006  8:29 PM` over the 02 it
            # had just set, and the next reply `JAN 16, 2006 20:29`
            out = out.replace(self.c.clock_stamp().encode("latin-1"),
                              stamp_before.encode("latin-1"), 1)
        return out, "stored"

    def _as_read(self, tok, dev, data, code):
        """A display Set's data as far as the console reads it, for the
        shape check: cut to a `wire_cut`, or None -- no check at all -- for
        a word on a field that `ignores_text`, which `_canonical` answers
        with the value it had rather than refusing."""
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
        if not code[:1].isupper():
            # a word on a packed whole number that keeps its value -- 785's
            # `s78501ABC` was taken -- is not shape-checked either
            if (field and field.get("ignores_text")
                    and field.get("kind") == "int"
                    and not data.lstrip("-").isdigit()):
                return None
            return data
        if not field or field.get("part"):
            return data
        if field.get("ignores_text"):
            try:
                float(data.strip())
            except ValueError:
                return None
        if wire_cut(field):
            return data[:wire_cut(field)]
        return data

    def _packed_input_width(self, field):
        """How much data a computer-format Set of this field carries: the
        store's width, or a float's eight -- 78F's whole psi arrives packed.
        Not `packed_width`, which is the REPLY's: 78A takes `s78A011` and
        answers `0103`."""
        if field.get("packed_float"):
            return 8
        return self.WIDTHS.get(field.get("kind"), lambda f: None)(field)

    def _short_packed(self, tok, dev, data, code):
        """A computer-format Set whose data is short of its field's width,
        which the bench TLS-350 answers with the bare echo rather than a
        refusal: `s60401ABC`, `s54A00-1` and `s525011` alike (the computer
        Set sweep, 2026-09-18). Asked before the shape check, which would
        refuse them."""
        from .console import FIELDS
        if not code[:1].islower() or not data:
            return False
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01") \
            or FIELDS.get(f"S{tok}00")
        if not field or field.get("part"):
            return False
        width = self._packed_input_width(field)
        return bool(width) and len(self._without_prefix(tok, dev, data)) < width

    def _label_code(self, tok):
        """A code whose data is a free label, which `_canonical` cuts to width
        rather than the shape check refusing a long one."""
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}01") or FIELDS.get(f"S{tok}00")
        return bool(field and not field.get("part")
                    and field.get("kind") == "text")

    def _with_prefix(self, tok, dev, data):
        """Store a per-device value the way the console reads one back.

        The Inquire response for a device-prefixed function leads with the
        two digit device number; the Set command does not, because the device
        is already in the command's own address: the manual's format is
        `<SOH>S616TTf`, and `f` is the whole data field. A tool that dumps a
        console and writes it back therefore sends the value WITHOUT the
        prefix, and storing that verbatim means the next Inquire answers two
        characters short, every value looks changed, and a backup does not
        round trip.

        So the prefix goes on unconditionally. It cannot be conditional on
        "unless the data already starts with these two digits", because
        plenty of values legitimately do: S785 on line 1 is tank 01, and
        `0101` stripped is `01`, which is indistinguishable from an unstripped
        `01`. The panel writes its own values through `fieldio`, which adds
        the prefix there, so this path only ever sees a serial Set.
        A tool that dumps and restores VERBATIM sends the prefix back, though,
        because that is what the Inquire gave it -- and a real console backup
        does exactly this. Adding a second prefix to those leaves a tank
        labelled "01REGULAR" where the console says "REGULAR", so one has to
        come off first, and `_without_prefix` is the half that decides.
        """
        if dev == "00" or not self.c.is_prefixed(tok):
            return data
        return dev + self._without_prefix(tok, dev, data)

    # How wide the data field is for each kind, which is what makes a leading
    # device prefix detectable: a value that is exactly two characters longer
    # than the field can hold has two characters on the front that are not the
    # value. None means "cannot tell", and then nothing is stripped.
    WIDTHS = {"float": lambda f: 8, "text": lambda f: f.get("maxlen"),
              "int": lambda f: f.get("width"), "digits": lambda f: f.get("width"),
              "flag": lambda f: 1,
              "enum": lambda f: (len(f["choices"][0][0])
                                 if f.get("choices") else None)}

    def _without_prefix(self, tok, dev, data):
        """`data` with a device prefix taken off it, if it has one.

        The docstring above used to say this could not be conditional on the
        data starting with the device digits, "because plenty of values
        legitimately do: S785 on line 1 is tank 01, and `0101` stripped is
        `01`, which is indistinguishable from an unstripped `01`". That is
        true of the CONTENT and not of the LENGTH, which is what settles it.
        S785 holds two digits: `0101` is four characters, two too many, so the
        first two are a prefix; a bare `01` is two, exactly the width, so it
        is the value. The same test reads a 22 character label as a prefix and
        twenty characters of text, and an 8 character float as no prefix.

        Anything whose width is not known is left alone, so this only ever
        strips where it can be sure.
        """
        from .console import FIELDS
        if not data.startswith(dev):
            return data
        field = FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
        if not field or field.get("part"):
            return data
        width = self.WIDTHS.get(field.get("kind"), lambda f: None)(field)
        if not width or len(data) != width + 2:
            return data
        return data[2:]

    def _canonical(self, tok, dev, data, code):
        """A display-format Set carries the value in WORDS, not packed.

        "Display: <SOH>S60901c.cccccc" against "Computer: <SOH>s60901FFFFFFFF":
        the same setting, one as decimal text and one as an ASCII-hex IEEE
        float, and the console stores one thing either way. Without this a
        thermal coefficient written as 0.000700 reads back as the string
        `0.000700` and every tool that checks its own writes reports a
        failure.

        Returns the data to store, or None if the console would refuse it.
        """
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}" + (dev if dev != "00" else "00"))
        if field is None and dev != "01":
            field = FIELDS.get(f"S{tok}01")
        if (field and field.get("kind") == "slots" and dev != "00"
                and data.strip() not in ("0", "1")):
            # One device's configuration flag is `f`, 1=On 0=Off, and the
            # bench TLS-350 refuses `S781012` and `S781019` with a `?`.
            return None
        if field and not field.get("part") and field.get("kind") == "text":
            # A label, in either format: cut to its width, 80 to FF stored
            # as `A`, 7F refused -- the bench TLS-350's rule, `fieldio`'s
            # text branch. The device prefix a dump-and-restore carries comes
            # off first and goes back on after, the way `_with_prefix` would.
            pfx = ""
            if (dev != "00" and self.c.is_prefixed(tok)
                    and self._without_prefix(tok, dev, data) != data):
                pfx, data = data[:2], data[2:]
            try:
                cut = fieldio.encode_value(field, data, self.c.metric(),
                                           self.c, code=f"S{tok}{dev}")
            except ValueError:
                return None
            # Padded to the field's width, which is how the console holds it:
            # `i78201` on the bench answers `01RUAL` and sixteen blanks. A
            # label never set is still no value at all, so a header set to
            # twenty blanks and one never set stay two different things.
            return pfx + cut
        if not code[:1].isupper():
            # Computer format is already packed -- but a date is six digits
            # in both forms, and this returned before looking: `s75C01AB0101`
            # was acked where the display form is refused, and I75C01 then
            # raised on int('AB') from the state file for good.
            text = data.strip()
            if (field and not field.get("part")
                    and field.get("kind") == "date"
                    and text and not text.isdigit()):
                return None
            # A numeric field in computer format carries a packed IEEE
            # float, and "nan", "inf" and "1e400" are none of those -- a
            # real console has a range for the field and would refuse them.
            # Accepting them stored a non-finite number that `int()` and
            # `round()` cannot represent, and the console answered 9999FF
            # to everything from then on, because `traffic.gauge()` runs
            # from `tick()` at the top of every command. Eleven bytes, once,
            # unauthenticated, and on a bench or LAN console it was written
            # to disk and outlived the restart.
            if (field and not field.get("part")
                    and field.get("kind") in ("float", "int")
                    and not (field.get("kind") == "int"
                             and field.get("ignores_text"))
                    and text and not _finite_packed(text)):
                return None
            if field and not field.get("part"):
                # The bench TLS-350 reads a computer-format field to its own
                # width and no further -- `s7890143480000FF` stored 200 feet
                # and answered `0143480000` -- and holds it to the same
                # range as the display form: `s7890100000000` (0 feet) and
                # `s7890144160000` (600) were refused with eight `?`, and
                # `s78801ZZ` with two. 2026-09-18.
                width = self._packed_input_width(field)
                # a dump-and-restore carries the device prefix the inquiry
                # gave it; that comes off before the field is cut to width
                data = self._without_prefix(tok, dev, data)
                if width and len(data) > width:
                    data = data[:width]
                if width and len(data) < width:
                    return self.BARE
                kind = field.get("kind")
                if field.get("packed_float"):
                    kind = "float"
                try:
                    if kind == "int":
                        # held to its range, which the display form is too:
                        # `s5070031` and `s51900745` refused
                        if field.get("ignores_text") and not data.lstrip(
                                "-").isdigit():
                            held = self.c.values.get(f"S{tok}{dev}") \
                                or self._default_stored(tok, dev)
                            if held is None:
                                # nothing set: it keeps what it reads, NONE
                                return self._blank_stored(tok, dev)
                            return self._without_prefix(tok, dev, held)
                        fieldio.encode_value(field, str(int(data)),
                                             self.c.metric(), self.c,
                                             code=f"S{tok}{dev}")
                        top = self.SET_MAX_FROM.get(tok)
                        if top and int(data) > self.c.capacity(top):
                            return None
                    elif kind == "float":
                        value = packed.unhexfloat(data[:8])
                        quantity = units.QUANTITY.get(tok)
                        system = units.system(self.c)
                        if quantity and system != units.US:
                            # a packed value in the system's units is stored
                            # in U.S.: `s62101` 4000.0 in metric read 1057
                            # gallons back in U.S. (`units`)
                            value = units.into(quantity, value, system)
                            data = packed.hexfloat(value) + data[8:]
                        if tok == "628" and value <= 0 and dev.isdigit() \
                                and int(dev):
                            # 0 or less is the full volume as it stands, as
                            # the display Set's is: `s62801BF800000`
                            value = (self.c.limit("604", int(dev))
                                     or self.c.limit("60A", int(dev)) or 0.0)
                            data = packed.hexfloat(value)
                        clamp = field.get("packed_clamp")
                        if clamp is not None and value > clamp:
                            # a packed volume past six digits is taken and
                            # held at 999999 -- `s6040149742400` read back
                            # 497423F0 -- where the display's six digits
                            # cannot carry it
                            value = float(clamp)
                            data = packed.hexfloat(value)
                        # a packed floor below the display's: `s60401BF800000`
                        # (-1.0) was taken where `S60401-1` is refused
                        floor = field.get("packed_floor")
                        check = value
                        if floor is not None and floor <= value < 0:
                            check = 0.0
                        fieldio.encode_value(field, str(int(check))
                                             if field.get("kind") == "int"
                                             else repr(check),
                                             self.c.metric(), self.c,
                                             code=f"S{tok}{dev}")
                    elif kind == "enum":
                        # a choice may carry a third element, a variant it
                        # belongs to -- 62F's 4.0 IN. PS is `ethanol`
                        codes = [ch[0] for ch in field.get("choices", [])]
                        if codes and data not in codes:
                            return None
                    elif kind == "flag" and data not in ("0", "1"):
                        if field.get("any_digit") and data.isdigit():
                            return "0" if data == "0" else "1"
                        return None
                except ValueError:
                    return None
                if field.get("packed_float"):
                    # the store keeps 78F's whole psi; the wire packs it
                    try:
                        return str(int(packed.unhexfloat(data)))
                    except (ValueError, TypeError):
                        return None
            return data
        if not field or field.get("part") or field.get("kind") not in (
                "float", "int", "flag", "time", "date", "digits", "enum"):
            return data
        text = data.strip()
        if not text:
            return data
        # a device-prefixed Set carries `TT` in front of the value -- S613 01 1
        # is tank 1, Pd = 95% -- so the prefix comes off before the value is
        # checked and goes back on after. Without this, every prefixed field
        # whose kind this function knows would refuse its own correct form.
        #
        # WHICH two characters those are is decided by the manual's own field
        # width, not by whether the value happens to begin with the device
        # number. `S624TTII.t` is the high water limit on tank TT, four
        # characters wide -- so `S6240101.5` is tank 1 at 1.5 inches, and
        # reading the leading `01` as a prefix stored 0.5. `_without_prefix`
        # below settled the same question the same way for the store side
        # and this path went on guessing. See FIDELITY R17.
        from .console import DECIMAL_WIDTHS, DEVICE_PREFIXED
        pfx = ""
        try:
            prefixed = int(tok, 16) in DEVICE_PREFIXED
        except ValueError:
            prefixed = False
        width = DECIMAL_WIDTHS.get(tok)
        if prefixed and dev != "00" and text.startswith(dev) and len(text) > 2:
            if width is None or len(text) == width + 2:
                pfx, text = text[:2], text[2:]
        cut = wire_cut(field)
        if cut:
            # The bench TLS-350 reads a display-format value to a width of
            # its own and drops the rest: `S60801+099.99` stored 99.90,
            # `S50E00+120.1` 120.0 and `S52E01100` 10. 2026-09-18.
            text = text[:cut]
        try:
            number = float(text)
        except ValueError:
            number = None
        if (tok == "628" and number is not None and number <= 0
                and dev.isdigit() and int(dev)):
            # A maximum volume of 0 or less is the tank's full volume as it
            # stands: on the bench TLS-350 `S628010` stored 123456 with the
            # full volume at 123456, and kept it when 604 then moved to
            # 200000 (2026-09-18).
            full = (self.c.limit("604", int(dev))
                    or self.c.limit("60A", int(dev)) or 0)
            text = str(int(full))
        if number is None and field.get("ignores_text"):
            # Answered with the report and the value as it was, not refused:
            # `S77501ABC`, where `S77D01ABC` is. The bench TLS-350, 2026-09-18.
            held = self.c.values.get(f"S{tok}{dev}")
            if held is None:
                held = self._default_stored(tok, dev)
            return (None if held is None
                    else self._without_prefix(tok, dev, held))
        if number is not None and field.get("whole"):
            # `S6260149.5` stored 49: the whole part, not the nearest
            text = str(int(number))
        quantity = units.QUANTITY.get(tok)
        system = units.system(self.c)
        converted = False
        if (quantity and system != units.US and number is not None
                and not (tok == "628" and number <= 0)):
            # entered in the system's units, stored and ranged in U.S.:
            # `S621013785` in metric stored 1000 gallons, and 336 m was
            # refused for a line whose longest is 1101 feet (`units`)
            us = units.into(quantity, number, system)
            text = (str(int(us)) if field.get("kind") == "int"
                    else repr(us))
            converted = True
        top = self.SET_MAX_FROM.get(tok)
        if top and text.lstrip("-").isdigit() and int(text) > self.c.capacity(top):
            # a line's tank past the tanks the probe card can have: the
            # bench refuses `S7850108` on a four-probe card with `??`
            return None
        try:
            # a value converted to U.S. above is ranged in U.S. -- not by the
            # field's metric limits a second time: `S50E00+048.8` in metric
            # is 118.4 F, taken, not 48 against a metric ceiling of 49
            return pfx + fieldio.encode_value(
                field, text, self.c.metric() and not converted, self.c,
                code=f"S{tok}{dev}")
        except ValueError:
            return None

    def _module_present(self, tok):
        """A console rejects what its card cage cannot serve.

        The ranges are the serial manual's own code-space bands, p.10: 601-683
        In-tank setup, 701-74E Sensor setup, 751-761 VLL setup, 771-773 Pump
        sensor setup, 774-78F PLLD setup, 790-79F Reconciliation setup,
        7A0-7AF WPLLD setup, 7B1-7B6 Meter map, 7BC-80C I/O setup.
        """
        try:
            fn = int(tok, 16)
        except ValueError:
            return True
        c = self.c
        if fn == 0x889:
            # "DTR Normal State for Serial Satellite Boards" is 576013-635's
            # own Function Type for this code, and its display format draws
            # the setting under `COMM BOARD  : 1 (S-SAT )` -- one screen, two
            # lines, the same pairing the tape prints. It answered on a
            # console with no satellite in it. See FIDELITY S7a.
            return c.has("ssat") or c.has("asat")
        if 0x601 <= fn <= 0x6FF:
            return c.has("probe")
        if 0x721 <= fn <= 0x72C:
            return c.has("smart")
        if 0x701 <= fn <= 0x74E:
            return any(c.has(m) for m in ("liquid", "vapor", "gw", "2wire", "3wire"))
        if fn == 0x75A:
            # The one code in the VLLD band that is not a VLLD code. Its own
            # Function Type says so: "Set Line Leak Lockout Schedule (All
            # Types)", where every neighbour in 751-761 names one type. The
            # band on p.10 is a guide to the code space, not a rule about
            # cards, and gating this on the VLLD card alone would stop a
            # PLLD-only site setting a schedule the manual says covers it.
            return any(c.has(m) for m in ("vlld", "plld", "wplld"))
        if 0x751 <= fn <= 0x761:
            return c.has("vlld")
        if 0x771 <= fn <= 0x773:
            return c.has("pump")
        if 0x774 <= fn <= 0x78F:
            return c.has("plld")
        if 0x790 <= fn <= 0x79F or 0x7B0 <= fn <= 0x7B6:
            # Reconciliation setup and the meter map answer with no BIR key:
            # the bench TLS-350 (2026-09-18, S-Module 330160-012, no BIR)
            # answers 795, 796, 79E, 79F, 7B0, 7B1 and 7B3 with their
            # reports. This gated them on the key and answered 9999.
            return True
        if fn in (0x7BC, 0x7BD, 0x7BE):
            # the line families' "Disable Alarm Assignments II", each its own
            # family's card although the band calls it I/O setup
            return c.has({0x7BC: "vlld", 0x7BD: "plld", 0x7BE: "wplld"}[fn])
        if fn == 0x7C3:
            # TANK MAXIMUM VOLUME LIMIT, undocumented and a tank setting
            # though it sits in the I/O band; the bench answers it with a
            # row per tank position. `blankrows.TABLES`.
            return c.has("probe")
        if 0x7A0 <= fn <= 0x7AF:
            return c.has("wplld")
        if 0x7C4 <= fn <= 0x7C9:
            return c.has("pumpmon")
        if 0x7BC <= fn <= 0x80C:
            return c.has("io") or c.has("relay")
        return True


# ---------------------------------------------------------------------------
# Telnet. A console has an RS-232 port and no idea what telnet is, but the
# people who use one type at it through a terminal, and Microsoft's telnet
# client will not send a Ctrl-A at all until the far end negotiates character
# mode. Left to itself the client stays in line mode, where its own line
# editor swallows control characters: you press Ctrl-A, nothing crosses the
# wire, and the console looks dead when it is simply hearing nothing.
#
# So: offer character mode on connect, and take over the echo. A client that
# answers is a terminal with somebody typing at it, and gets its keystrokes
# echoed back the way a terminal program shows them. A client that says
# nothing is a TOOL, and gets exactly the bytes a console would send it.
# ---------------------------------------------------------------------------
IAC, SE, SB, WILL, WONT, DO, DONT = 255, 240, 250, 251, 252, 253, 254
OPT_ECHO, OPT_SGA, OPT_LINEMODE = 1, 3, 34

# The opening question, which must not contain a 01 byte: a TOOL reading this
# port scans for SOH, and an IAC WILL ECHO (ff fb 01) would hand it one out of
# nowhere. IAC DO SUPPRESS-GO-AHEAD is three harmless bytes that every telnet
# client answers and no tool notices.
PROBE = bytes([IAC, DO, OPT_SGA])

# Once something has answered, it is a terminal, and this is what puts it into
# character mode with its local echo off.
HELLO = bytes([IAC, WILL, OPT_ECHO,        # "I will do the echoing"
               IAC, WILL, OPT_SGA,         # character at a time, not lines
               IAC, DONT, OPT_LINEMODE])   # so keep your line editor out of it


def strip_telnet(buf):
    """(data, saw_telnet, leftover): the command bytes, without the IAC."""
    out, saw, i = bytearray(), False, 0
    while i < len(buf):
        byte = buf[i]
        if byte != IAC:
            out.append(byte)
            i += 1
            continue
        saw = True
        if i + 1 >= len(buf):
            return bytes(out), saw, buf[i:]          # half an IAC so far
        command = buf[i + 1]
        if command == IAC:                           # a literal 255
            out.append(IAC)
            i += 2
        elif command in (WILL, WONT, DO, DONT):
            if i + 2 >= len(buf):
                return bytes(out), saw, buf[i:]
            i += 3
        elif command == SB:
            end = buf.find(bytes([IAC, SE]), i)
            if end == -1:
                return bytes(out), saw, buf[i:]
            i = end + 2
        else:
            i += 2
    return bytes(out), saw, b""


def echo_for(data):
    """What a terminal shows for what was just typed.

    Ctrl-A is drawn as ^A, because a tech needs to see that the SOH landed,
    it is the one keystroke of the command that has nothing to show for
    itself, and the one they are most likely to have missed.
    """
    out = bytearray()
    for byte in data:
        if byte == 0x01:
            out += b"^A"
        elif byte in (0x0D,):
            out += b"\r\n"
        elif byte in (0x08, 0x7F):
            out += b"\b \b"
        elif 0x20 <= byte <= 0x7E:
            out.append(byte)
    return bytes(out)



# The bytes a console cannot put on its display, and must never put on the
# wire inside a frame.
#
# A reply is framed `SOH ... ETX`, and SOH is the only way a client can find
# where a frame starts. Stored text was emitted with `encode("ascii",
# "replace")`, which substitutes bytes ABOVE 0x7E and passes every control
# byte below 0x20 through untouched -- SOH included. So a Set that put a raw
# 0x01 into a stored field (the station header at 503 is four separately
# settable lines, drawn at the top of nearly every display report) made the
# console emit a second, attacker-authored frame inside its own reply, closed
# by the real reply's ETX. A client resynchronising on SOH -- which is the
# only thing it can do -- reads the forgery as a complete report, and the
# display format carries no checksum to contradict it.
#
# Sanitising on OUTPUT rather than on input covers every field at once,
# including the ones a later revision adds. CR and LF survive because
# `_frame` uses them as its own separators; everything else below a space,
# and DEL, becomes a space -- which is what a real console's display does
# with them, having no glyph for any of it.
_UNPRINTABLE = {c: " " for c in range(0x20)}
_UNPRINTABLE.pop(0x0D)
_UNPRINTABLE.pop(0x0A)
_UNPRINTABLE[0x7F] = " "
_UNPRINTABLE = str.maketrans(_UNPRINTABLE)


def printable_body(text):
    """Stored text, with anything that could forge a frame taken out."""
    return text.translate(_UNPRINTABLE)


def wire_cut(field):
    """How many characters of a display-format value the console reads, or
    None for all of them.

    The bench TLS-350 (2026-09-18) reads a whole number to its width and
    drops the rest -- `S52E01100` stored 10 and `S50700015` 1 -- and a few
    signed decimals to a width of their own, `wire_cut`: `S60801+099.99`
    stored 99.90, and `S50E00+120.1` 120.0.
    """
    if field.get("wire_cut"):
        return field["wire_cut"]
    if field.get("kind") == "int" and field.get("width"):
        return field["width"]
    return None


def _finite_packed(text):
    """Is this a number the console could actually hold?

    Computer format packs a float as eight hex digits, and `Console.limit`
    falls back to `float(body)` when that fails -- which is what let
    "nan" and "inf" through. Both forms are checked here, and both must
    come out finite.
    """
    try:
        return math.isfinite(packed.unhexfloat(text[-8:]))
    except (ValueError, TypeError):
        pass
    try:
        return math.isfinite(float(text))
    except (ValueError, TypeError):
        return False


def serve(console, host, port, verbose=True, log=None, on_socket=None,
          on_conn=None, policy=None, gate=None, capture=None, running=None):
    """Answer the console's serial protocol on a TCP port.

    `on_socket` is handed the listening socket once it is up. The card's
    network supervisor keeps it so it can close it and move the tunnel when
    the address or the port programmed into the card changes -- which is
    what a real card does when you save a new port and it reboots.

    `on_conn` is handed each connection as it is accepted, for the same
    reason: when the card reboots, the sessions open on it go too.

    `policy`, `gate` and `capture` are the exposure controls and all three
    are optional: handed none, this serves exactly as it always has. See
    `exposed.py` for what each of them does and why the defaults are what
    they are.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    # A deeper backlog than the old 5 when there is a gate to do the
    # refusing. The point is not to serve more of a flood: it is that a
    # connection refused by the KERNEL is invisible -- it never reaches the
    # gate, so it is never counted and never captured -- and on a honeypot
    # the fact that an address is flooding is the finding. Accepting it and
    # then turning it away in `_session` is what puts it in the log.
    srv.listen(16 if gate is not None else 5)
    if on_socket:
        on_socket(srv)
    if verbose:
        print(f"[sim] TLS-350 listening on {host}:{port}")
    handler = Handler(console, verbose, log)
    while True:
        # Not `srv.accept()` directly: a thread already blocked in one is
        # not woken by another thread closing the socket, so a stopped
        # tunnel went on answering the next caller. `listen.accept` waits
        # with a timeout, re-asks `running` each time round, and refuses a
        # connection that arrived in the gap. FIDELITY R30.
        got = listen.accept(srv, running)
        if got is None:
            try:
                srv.close()
            except OSError:
                pass
            return
        conn, addr = got
        if log:
            log(f"-- connection from {addr[0]}")
        if on_conn:
            on_conn(conn)
        threading.Thread(target=_session,
                         args=(conn, handler),
                         kwargs={"peer": addr, "policy": policy,
                                 "gate": gate, "capture": capture},
                         daemon=True).start()


# carriage return and ETX. A line feed is a character like any other: the
# bench TLS-350 took `I61F00<LF>` as a delivery type and refused it,
# `I61F<LF>00` as an unknown code, and `S60204SUPER<LF>` waited for its CR
# and refused six
# characters (2026-09-25, `bench-2026-09-25/lf.jsonl`, `lfset.jsonl`). This
# ended a command at a line feed. FIDELITY S38.
TERMINATORS = (13, 3)

# Inquiries the console does not answer at their sixth character: it waits
# for a data field and its terminator, and a lone CR gets the answer. The
# bench TLS-350 (2026-09-22, `bench-2026-09-22/waitcr*.jsonl`) was silent for
# every one of these sent bare, at device 00 and 01 alike, and answered when
# a CR followed -- with the frame alone, but for the two named in
# `Handler.WAITED_REPLY`. The census never sent a CR, so none of them was on
# any capture, and `i7B100` was filed as the one code that answers nothing
# (FIDELITY S36): it was waiting. 7B1's display form and C09's packed one
# answer at once.
WAITING_INQUIRIES = frozenset({
    "2E2", "61F", "79B", "79C", "7B5", "7B6", "A56", "C01", "C02", "C03",
    "C04", "C07", "C08", "C09", "C10", "C11", "C12", "C20", "C21", "C22",
    "C24", "C25", "C30", "E20", "V02", "V03", "V04", "V05", "V06", "V07",
    "V08", "V09", "V0A", "V0B", "V43", "V83"})
WAITING_DISPLAY = WAITING_INQUIRIES
WAITING_PACKED = (WAITING_INQUIRIES - {"C09"}) | {"7B1"}


def _waits(body):
    """Does this inquiry wait for a terminator before it is answered?

    Not with a control byte in its code: `I61F<LF>00` was answered 9999FF at
    its sixth character, an unknown code (2026-09-25, `lf.jsonl`)."""
    if not body[1:6].isalnum():
        return False
    tok = body[1:4].upper().decode("latin-1")
    return tok in (WAITING_DISPLAY if body[:1] == b"I" else WAITING_PACKED)

# The most a session holds of a command it has not finished. The longest a
# tool sends is a fourteen-pair tank chart -- SOH, the six-digit security
# code, `s63BTTnn`, fourteen `ffHHHHHHHHVVVVVVVV` and its terminator -- at 268
# bytes, and the widest template in `formats` run across sixteen devices is
# 526. 576013-635 Rev AA section 3.0 gives the console's own buffer as 128
# characters, which the tank chart overruns by itself, so the number is not
# taken from there. See CLOSED Z4.
LONGEST_COMMAND = 1024

# The most a session holds of a telnet subnegotiation still waiting for its
# IAC SE. A terminal's own are a few bytes; its window size is nine.
LONGEST_SUBNEGOTIATION = 256


#: a data field whose width the console knows before it reads it. `enum`,
#: `int` and `digits` carry their own; these are the rest.
FIXED_KINDS = {"flag": 1, "date": 6, "time": 4, "slots": 1}
#: and the kinds a field made of PARTS can be made of, where the whole is as
#: wide as its last part ends: 501's date and time, 517's units and language
PARTED_KINDS = ("date", "time", "enum", "int", "digits", "flag")
#: Codes with no `FIELDS` row of their own, as (display, packed) characters.
#: All of them were answered bare by the bench TLS-350, so all of them have a
#: width: `S08701 14901` (the 149 confirmation and a test type), `S55D001`,
#: `S7C301200` and `s7C30143040000` (2026-09-18/19).
EXTRA_SET_WIDTHS = {"087": (5, 5), "088": (5, 5), "55D": (1, 1),
                    "7C3": (6, 8),
                    # two raw characters, answered at the second in either
                    # format (2026-09-25, `bench-2026-09-25/etxset.jsonl`)
                    "537": (2, 2), "538": (2, 2)}
#: and the auto dial payloads, whose first digit says how long the rest is
DIAL_SIZED = {"52B": ("wirelists", "DIAL_WIDTH"),
              "75A": ("wirelists", "DIAL_WIDTH"),
              # 52B's successor, whose sixth method carries one character
              # where 52B has none: the manual's own widths, not measured --
              # the bench answered `S52B01` bare and 520 was never sent one
              "520": ("formats", "DIAL2_WIDTH")}
_SET_WIDTHS = {}


def _dial_width(tok, data):
    """`S52B01 1000000EE00`: a method digit and a field it sizes."""
    from importlib import import_module
    where, name = DIAL_SIZED[tok]
    table = getattr(import_module("." + where, __package__), name, {})
    size = table.get((data or "")[:1])
    return 1 + size if size else 0


def _set_width(tok, dev, packed=False):
    """How many characters of a Set's data field the console reads before it
    acts, or 0 when it waits for a terminator.

    The bench TLS-350 answers a Set the moment its field is full and sends
    nothing at all while it is short: `S50100060119005`, nine of the clock's
    ten digits, sat unanswered until a CR arrived, and then was refused
    (2026-09-19, `transcripts/framewidth.log`). Every whole-field Set in
    every experiment was answered with no terminator at all -- `S781021`,
    `S601021`, `S783012`, `S55D001`, `S50100` with its ten. A field whose
    width is not fixed waits instead: the station header took `1234` and a
    CR, and stored it.

    Only the kinds whose width is known are early: a label, a decimal and a
    list wait, which is what the header showed and what keeps a value the
    console would have read whole from being cut in half here.

    The computer format reads ITS own widths, which are not the display's:
    `s6070142C00000` -- a tank diameter as eight hex digits -- was answered
    bare where seven got nothing, and `s50301` took twenty characters of
    label the same way (2026-09-19, `transcripts/packedwidth`,
    `packedtext`). Display 607 reads six characters, so one table for both
    would have cut a packed float in half and refused it.
    """
    key = (tok, dev, packed)
    if key in _SET_WIDTHS:
        return _SET_WIDTHS[key]
    from .console import FIELDS
    width = 0
    if tok in EXTRA_SET_WIDTHS:
        width = EXTRA_SET_WIDTHS[tok][1 if packed else 0]
        _SET_WIDTHS[key] = width
        return width
    parts = []
    for where in (dev, "01", "00"):
        parts = [f for f in FIELDS.values()
                 if f.get("code") == f"S{tok}{where}" and f.get("part")]
        if parts:
            break
    if parts and all(p.get("kind") in PARTED_KINDS for p in parts):
        # both formats send 501's ten digits and 517's three
        width = max(p["part"][0] + p["part"][1] for p in parts)
    else:
        field = (FIELDS.get(f"S{tok}{dev}") or FIELDS.get(f"S{tok}01")
                 or FIELDS.get(f"S{tok}00"))
        kind = (field or {}).get("kind")
        if field and not field.get("part") and packed and field.get(
                "packed_float"):
            # a whole number held but packed as a float: `s78F0125`, the
            # display's two digits, sat unanswered on the bench where
            # `i78F01` packs `41C80000` (the computer Set sweep, 2026-09-18)
            width = 8
        elif field and not field.get("part") and packed:
            # the packed widths are the ones `_field_width` answers a refusal
            # with, and a label's is its length
            reads = Handler.WIDTHS.get(kind)
            width = (reads(field) if reads else None) or FIXED_KINDS.get(kind, 0)
        elif field and not field.get("part"):
            if kind in FIXED_KINDS:
                width = FIXED_KINDS[kind]
            elif kind == "enum" and field.get("choices"):
                lengths = set(len(str(c[0])) for c in field["choices"])
                width = lengths.pop() if len(lengths) == 1 else 0
            elif kind in ("int", "digits"):
                width = field.get("width") or field.get("wire_cut") or 0
            elif kind == "float" and field.get("wire_cut"):
                # a decimal the console reads to a width of its own:
                # `S62101000000` was answered bare, and `S60801+099.99`
                # stored 99.90 -- six characters read either way
                width = field["wire_cut"]
    _SET_WIDTHS[key] = width
    return width


def _set_end(body):
    """Where a Set's data field ends inside `body`, or 0 while it is short."""
    tok, dev = (body[1:4].upper().decode("latin-1"),
                body[4:6].decode("latin-1"))
    data = body[6:].decode("latin-1")
    if tok in DIAL_SIZED:
        width = _dial_width(tok, data)
    else:
        width = _set_width(tok, dev, packed=body[:1] == b"s")
    return 6 + width if width and len(data) >= width else 0


def _complete(buf):
    """How many bytes of `buf` are one command, or 0 if it is not one yet.

    The manual's command format has no terminator: "SOH, Security Code,
    Function Code, Data Field", and the function code "is a six character
    command code". So an inquiry IS complete at six characters, which is why
    a console answers a hand-typed session the moment you finish typing
    I20100, no Return needed. A Set of a field whose width the console knows
    is finished at the last character of it (`_set_width`); one of a label or
    a decimal runs to a carriage return, line feed or ETX.
    """
    if not buf.startswith(SOH):
        return 0
    ends = [buf.find(bytes([t])) for t in TERMINATORS]
    end = min([e for e in ends if e != -1], default=-1)
    if end != -1:
        return end + 1
    body = buf[1:]
    if len(body) > 6 and body[:1].isdigit():
        body = body[6:]              # the optional six-digit security code
    if len(body) >= 6 and body[:1] in (b"I", b"i"):
        if body[1:4].upper() == b"61F" and len(body) >= 7 and _waits(body):
            # its one-character delivery type is its whole data field, and
            # the bench answered at it with no terminator: `I61F000` drew
            # the table and `I61F002` a `?` a tank (2026-09-25, `lf.jsonl`)
            return len(buf) - len(body) + 7
        if _waits(body):
            return 0
        return len(buf) - len(body) + 6
    if len(body) > 6 and body[:1] in (b"S", b"s"):
        end = _set_end(body)
        if end:
            return len(buf) - len(body) + end
    return 0


class Framer:
    """One session's bytes, as the commands in them.

    Everything `_session` knows about a connection that is not the socket:
    the part of a telnet sequence still to come, the part of a command still
    being typed, and whether the other end has turned out to be a terminal.
    Its own class so what a stream can do to those buffers can be tested
    without a connection. Neither buffer had a limit, so a Set that never
    sent its terminator, or a subnegotiation that never sent IAC SE, grew
    the session for as long as the bytes kept coming. See CLOSED Z4.
    """

    def __init__(self):
        self.buf, self.pending, self.telnet = bytearray(), bytearray(), False

    def feed(self, chunk):
        """(sends, commands): what goes straight back, in order, and each
        whole command this chunk completed."""
        sends, commands = [], []
        self.pending += chunk
        data, saw, leftover = strip_telnet(bytes(self.pending))
        if len(leftover) > LONGEST_SUBNEGOTIATION:
            # Only a subnegotiation waits this long, and one that has not
            # ended by now is not going to. What it swallowed goes with it.
            leftover = b""
        self.pending = bytearray(leftover)
        if saw and not self.telnet:
            # it answered, so it is a terminal: take over the echo
            self.telnet = True
            sends.append(HELLO)
        if self.telnet and data:
            # somebody is typing: show them what they typed
            sends.append(echo_for(data))
        buf = self.buf
        # in order, because a rub-out only takes back what came before it
        for byte in data:
            if byte == 0x1B:
                # ESC (576013-635 p.267): "a means to halt a response
                # message at any time before its completion." Over a
                # socket the reply goes out in one send, so what ESC can
                # still do is abandon a command that is only part typed,
                # which is the same intent: nothing part-formed survives.
                buf.clear()
            elif byte in (0x08, 0x7F) and self.telnet:
                # DEL rubs out only for somebody at a terminal. On a raw
                # tunnel it is a data byte: the bench TLS-350 refused
                # `S78201Q<DEL>Q` as three characters of label -- `???` --
                # where a rub-out would have left an acceptable `Q`. And so
                # is BACKSPACE, which rubbed out everywhere here: the bench
                # answered `I2019<BS>00` under the echo `I2019<BS>`, the BS
                # the device's second character (2026-09-23).
                del buf[-1:]
            else:
                buf.append(byte)
        while True:
            start = buf.find(SOH)
            if start == -1:
                buf.clear()      # noise with no SOH in it is not a command
                break
            del buf[:start]
            n = _complete(bytes(buf))
            after = buf.find(SOH, 1)
            if after != -1 and (not n or after < n):
                # A new SOH before this command was whole: the bench TLS-350
                # threw the fragment away and answered only what followed.
                # `I201`, `I2010`, `I101`, `I999`, a lone `I` -- each sent
                # and then followed by the next command -- got no reply of
                # their own (2026-09-19, `transcripts/edge1`). This ran the
                # fragment and the next SOH together into one command.
                del buf[:after]
                continue
            if n > LONGEST_COMMAND or (not n and len(buf) > LONGEST_COMMAND):
                # Longer than any command there is, so not one: dropped, and
                # taken up again at the next SOH, the way noise is.
                after = buf.find(SOH, 1)
                if after == -1:
                    buf.clear()
                    break
                del buf[:after]
                continue
            if not n:
                break
            commands.append(bytes(buf[:n]))
            del buf[:n]
        return sends, commands


def _session(conn, handler, peer=None, policy=None, gate=None, capture=None):
    """One tunnel session: the bytes in, the console's answers out.

    The exposure controls are all no-ops when nothing was handed in, which
    is how every existing caller and every test still gets the old
    behaviour. What they add on a public address:

      * an IDLE TIMEOUT. There was none at all -- a connection that opened
        and said nothing held its thread until the process ended, so the
        cheapest possible denial of service against this program was to open
        connections and do nothing. `conn.settimeout` is the whole fix.
      * ADMISSION, so one source cannot hold every thread.
      * a RESPONSE DELAY before each answer. An instant reply is the tell
        that this is software; see `exposed.Policy`.
      * the CAPTURE, which is the point of running it at all.
    """
    ip = peer[0] if peer else None
    port = peer[1] if peer and len(peer) > 1 else None
    # One recorder for this connection, carrying its own share of the
    # capture: past its budget the rows keep their time, source, direction
    # and LENGTH and drop the hex, so one flooder cannot push every other
    # source's evidence out of the file. FIDELITY R32.
    rec = capture.session(peer=ip, port=port, proto="tunnel") \
        if capture is not None else None

    def seen(direction, data, **kw):
        if rec is not None:
            rec.record(direction, data, **kw)

    with exposed.session(gate, ip) as admitted:
        if not admitted:
            # Refused, and told so the way a card with no free connection
            # tells: by closing. A real XPort's tunnel accepts one session
            # and drops what it cannot serve, so a closed connection is the
            # honest answer here and not a honeypot tell.
            try:
                conn.close()
            except OSError:
                pass
            return
        if policy is not None and policy.idle_timeout:
            conn.settimeout(policy.idle_timeout)
        seen("open", b"")
        framer = Framer()
        try:
            conn.sendall(PROBE)
        except OSError:
            return
        budget = getattr(policy, "max_bytes_per_min", None)
        taken = 0
        since = time.monotonic()
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                if budget:
                    # A rate on the BYTES, not only on the connections. A
                    # flood arriving down one accepted connection was
                    # limited by nothing at all, and the capture wrote 2.12
                    # bytes to disk for each one of them. FIDELITY R32.
                    now = time.monotonic()
                    if now - since >= 60.0:
                        since, taken = now, 0
                    taken += len(chunk)
                    if taken > budget:
                        seen("refused", b"",
                             note="byte rate limit (%d/min); connection "
                                  "closed after %d bytes" % (budget, taken))
                        break
                seen("in", chunk)
                sends, commands = framer.feed(chunk)
                for data in sends:
                    conn.sendall(data)
                    seen("out", data)
                for raw in commands:
                    # the port has carried data, whatever 888 decides to
                    # print about it
                    handler.c.note_comm()
                    out = handler.handle(raw)
                    # BEFORE the `if`, deliberately. The delay used to sit
                    # on the answering path only, and a command refused for
                    # a bad RS-232 security code returns b"" -- so a wrong
                    # guess cost nothing while a right one cost 0.15-0.75 s
                    # and produced a reply. That is a free oracle over a
                    # six digit code, and `Framer.feed` accepts an inquiry
                    # with no terminator, so guesses pack about 315 to a
                    # segment. Delaying every command, answered or not,
                    # prices the silent path the same as the loud one -- and
                    # is the more faithful behaviour anyway, because a card
                    # on a 9600 baud line cannot be polled thousands of
                    # times a second whatever it decides to say back.
                    if policy is not None:
                        policy.sleep()
                    if out:
                        # a terminal wants the reply on a line of its own,
                        # and the next thing it types on the line after
                        # that; a tool gets the frame and nothing else
                        if framer.telnet:
                            lead = (b"" if raw[-1:] in (b"\r", b"\n")
                                    else b"\r\n")
                            out = lead + out + b"\r\n"
                        # captured before it goes: a paced reply takes
                        # seconds, and a caller that hangs up part way
                        # would otherwise leave no record it was answered
                        seen("out", out, cmd=exposed.command_of(raw))
                        pace = getattr(policy, "pace", None)
                        if pace:
                            # at the line's own pace, the way the bench's
                            # bridge hands a reply on (`Policy.PACE`)
                            for i in range(0, len(out), 16):
                                conn.sendall(out[i:i + 16])
                                time.sleep(pace * len(out[i:i + 16]))
                        else:
                            conn.sendall(out)
        except OSError:
            pass
        finally:
            seen("close", b"")
            if framer.buf:
                # gone in the middle of a command, which is what the bench
                # console recorded as WAITING FOR DATA / DATA TIMED OUT
                handler.c.session_cut()
            try:
                conn.close()
            except OSError:
                pass
