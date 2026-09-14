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
import os
import re
import socket
import threading
import time

from .clock import clock_words
from .console import describe_alarms
from . import fieldio
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


def _sent_token(raw, folded):
    """The three token characters as the caller typed them.

    `parse_command` folds the token to upper case so one dispatch table can
    serve either format, but the reply echoes what was sent.
    """
    try:
        body = raw.lstrip(SOH).rstrip(CR).decode("ascii", "replace")
    except AttributeError:
        return folded
    body = body.rstrip("\r\n\x00")
    if body in SHORT_COMMANDS:
        body = SHORT_COMMANDS[body]
    if len(body) > 6 and body[0].isdigit():
        body = body[6:]
    sent = body[1:4]
    return sent if sent.upper() == folded else folded


def parse_command(raw):
    """(security, letter, token, device, data)."""
    body = raw.lstrip(SOH).rstrip(CR).decode("ascii", "replace")
    body = body.rstrip("\r\n\x00")
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
# Built from **Revision Y**, not Revision U. Deriving it from U missed `132`,
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
            "09B", "7B6"}

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

KNOWN = (SETTABLE | REPORTS | ACTIONS | ISD_SETUP | ISD_READ | ISD_TABLES
         | ISD_CONTROL | ISD_READ_ONLY | ISD_BUFFERS | set(VERIFIED)
         | wiresensors.CODES | wirelines.CODES | wirelater.MINE)

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
            body = (SOH + code.encode("ascii") + stamp(self.c).encode("ascii")
                    + value.encode("ascii"))
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
            lines += [self.c.text("503", n) or "" for n in range(1, 5)]
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
        body = wiretables.lead(code[1:4], body)
        text = SEP.join(lines) + SEP + body.replace(SEP, chr(10)).replace(
            chr(13), chr(10)).replace(chr(10), SEP)
        # A display reply opens SOH CR LF and closes CR LF ETX. The manual
        # draws the text of a report and not its framing, so this was written
        # without the two line endings for a long time. Measured on a real
        # TLS-350 (software 326.01) over its TCP/IP card: all 49 replies
        # captured begin SOH CR LF and end CR LF ETX, without exception.
        return SOH + SEP.encode("ascii") + text.encode("ascii", "replace") \
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
                if when:
                    # p.474 puts both stamps at column 25, and it is
                    # `clock_words` here rather than the wide form
                    rows.append(f"{label:<25s}{clock_words(when)}")
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
            return [int(dev)]
        live = sorted(self.c.tank_level) or sorted(self.c.programmed_tanks())
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
        volume, water = st.get("volume", 0.0), st.get("water", 0.0)
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
            rows = ["TANK PRODUCT             VOLUME TC VOLUME   ULLAGE"
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
            return self._nine(code), "no probe module fitted"
        tanks = self._tanks(dev)
        if tok == "@B6":
            rows = c.accuchart.calibration_status_rows(tanks)
        elif tok == "@B9":
            rows = c.accuchart.calibration_data_rows(tanks)
        elif tok == "@A4":
            if not c.licensed("bir"):
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "no probe module fitted"
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

        **A history is a table and this drew one row of it**, the current
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

    def _csld_states(self, tank, previous=False):
        """A56's month of CSLD state changes, newest first.

        This read `probe_leak_buffer(tank, "periodic")` -- the SCHEDULED
        leak-test buffer -- and a CSLD tank runs no scheduled tests, so the
        list was always empty and the fallback fired. **No PASS or FAIL
        could ever appear in the monthly report**, against the manual's own
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

    def _sump_rows(self, number, most):
        """The mag sump tests this sensor has to report.

        Nothing here runs a sump test to completion -- 099, 09A and 09B put
        one into a phase and that is what 317 reports. A sensor that has been
        through one has a reading to show; one that has not has none, which is
        status 00, NO TEST DATA AVAILABLE.
        """
        phase = self.c.control_phase_of("sump", number)
        if phase == "00":
            return []
        base = readings.fixed(18.0, 24.0, "sumpht", number)
        warm = readings.fixed(70.0, 80.0, "sumptemp", number)
        now = time.mktime(self.c.now())
        out = []
        for n in range(most):
            when = now - n * 30 * 86400
            out.append((base, warm, base - n * 0.002, warm - n * 0.1, 120.0,
                        6.0, 0.0, when, 12.0))
        return out

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
                level.get("water", 0.0), self.c.product_temperature(tank)]

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

    def _isd_carb_lines(self):
        """The CARB block V00 prints and V02 and V03 reprint inside theirs."""
        site = "assist" if self._isd_evr() == "02" else "balance"
        rows = ["CARB EVR CERTIFIED OPERATING REQUIREMENTS",
                f"{'':47s}Min  Max"]
        for label, lo, hi, only in isd.CARB_REQUIREMENTS:
            if only == site:
                rows.append(f"{label:47s}{lo:.2f} {hi:.2f}")
        rows.append("ISD MONITORING TEST PASS/FAIL THRESHOLDS")
        rows.append(f"{'':47s}Period Below Above")
        for label, per, lo, hi, unit, only in isd.CARB_THRESHOLDS:
            if only in (site, "any"):
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
        for group in self._isd_alarm_groups():
            body += f"{len(group):03d}"
            for at, _what, _value in group:
                body += f"{int(at):08X}" + "01" + "01" + "00" + "00" + "00" + "00"
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
        return self.c.values.get("SVC000") or isd.VP_AUTOMATIC

    def _vp_running(self):
        return self.c.values.get("SVC100") or "0"

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

        **The manual's own sample for the code, and a fallback for the
        codes it has no sample for.** This used to be "is it in `REPORTS`",
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
        drawn = wiretables.station_header(tok)
        return tok in REPORTS if drawn is None else drawn

    def _nine(self, _code=None):
        """What the console says when it has not understood."""
        return NOT_UNDERSTOOD

    def handle(self, raw):
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
        # _tanks needs to know which family it is answering for
        self._token = tok
        # 576013-635 p.267: with the security DIP on and a code programmed,
        # "the system will not respond to a command without the proper
        # security code." No response at all, not an error frame: a caller
        # without the code cannot tell the console is even there.
        if self.c.rs232_enforces_security() and security != self.c.security_code():
            if self.log:
                self.log(f"{raw!r}   [refused: RS-232 security]")
            return b""
        if not letter:
            # a command too short to be one, which a hand-typed telnet session
            # makes plenty of. The console answers rather than sitting silent.
            if self.verbose:
                print(f"  {raw!r}   [not understood]")
            if self.log:
                self.log(f"{raw!r}   [not understood]")
            return NOT_UNDERSTOOD
        # The console echoes the function code exactly as it was sent. Only
        # the dispatch is case-folded, which is why parse_command upper-cases
        # the token. Sending i@a900 to a real TLS-350 brings back i@a900, not
        # i@A900 -- the only codes where it shows are the undocumented @
        # family, because everything else is typed in one case anyway.
        code = f"{letter}{_sent_token(raw, tok)}{dev}"
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
            # the port has carried data, which is half of what 888 reports;
            # the other half is what it was doing while it did, and a command
            # arriving on the serial port is 888's own `06=RS232 REQUEST`.
            self.c.note_comm(connect=RS232_REQUEST)
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
        """Append the programmed end-of-message characters to a computer
        format reply, per 576013-635 (531/537). Display format and empty
        replies are returned untouched.
        """
        if not out or not letter.islower() or not out.endswith(ETX):
            return out
        return out + self.c.rs232_eom_chars()

    # ---- read --------------------------------------------------------------
    def inquire(self, tok, dev, code, data=""):
        if tok in SET_ONLY:
            return (self._nine(code),
                    f"{tok} is a Set with no Inquire format")
        if tok in SETTABLE and not self._module_present(tok):
            # Reading back a setting belonging to a card that is not in the
            # cage is the same question as writing one: the console has
            # nothing to answer with, and a tool sweeping the ranges wants
            # 9999 so it can skip the whole function rather than walk
            # sixteen devices that are not there.
            return self._nine(code), "no module fitted"
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
                rows = ["SYSTEM STATUS REPORT"]
                rows += ["  " + a["screen"]
                         for a in describe_alarms(recs)] or ["  ALL FUNCTIONS NORMAL"]
                return self._frame(code, SEP.join(rows)), note
            return (self._frame(code, "".join(recs) if recs else "000000"),
                    note)
        if tok in ("201", "21A"):
            # In-Tank Inventory Report, and the same report with the 90/95%
            # ullage the site programmed at S564
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                return (self._frame(code, self._inventory_text(tanks, tok)),
                        "inventory report")
            body = "".join(self._inventory_data(t, tok) for t in tanks)
            return self._frame(code, body), "inventory report"
        if tok == "205":
            # In-Tank Status Report: the alarms standing against each tank
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            active = {}
            for record in self.c.compute_alarms():
                if record[:2] == "02":
                    active.setdefault(int(record[4:6]), []).append(record[2:4])
            if code[0].isupper():
                rows = ["TANK   PRODUCT                 STATUS"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    names = [a["description"].upper()
                             for a in describe_alarms(
                        [f"02{nn}{tank:02d}" for nn in active.get(tank, [])])]
                    rows.append(f"{tank:3d}    {label:<24s}"
                                + (names[0] if names else "NORMAL"))
                    for extra in names[1:]:
                        rows.append(" " * 31 + extra)
                return self._frame(code, SEP.join(rows)), "tank status"
            body = "".join(f"{t:02d}{len(active.get(t, [])):02X}"
                           + "".join(active.get(t, [])) for t in tanks)
            return self._frame(code, body), "tank status"
        if tok == "206":
            # In-Tank Alarm History Report
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            log = [r for r in self.c.alarm_log if r["aa"] == "02"]
            if code[0].isupper():
                rows = ["TANK ALARM HISTORY"]
                for tank in tanks:
                    mine = [r for r in log if int(r["tt"]) == tank]
                    if not mine:
                        continue
                    label = self.c.text("602", tank) or ""
                    rows.append(f"TANK {tank}  {label}".rstrip())
                    for r in mine:
                        desc = describe_alarms(
                            [r["aa"] + r["nn"] + r["tt"]])[0]["description"]
                        rows.append(f"     {desc.upper():<25s}"
                                    + _when(r["at"]))
                # The title and nothing under it, which is what
                # `tests/console_capture/raw/I20600.bin` is: a real console
                # with no tank alarms answering `I20600` with the header
                # block, TANK ALARM HISTORY and its trailing blank. The
                # marker that used to stand here was W13's invented phrase.
                # See FIDELITY S18.
                return self._frame(code, SEP.join(rows)), "tank alarm history"
            body = ""
            for tank in tanks:
                mine = [r for r in log if int(r["tt"]) == tank]
                body += f"{tank:02d}{len(mine):02d}"
                body += "".join(r["at"] + alarm_history_code(r["nn"])
                                for r in mine)
            return self._frame(code, body), "tank alarm history"
        if tok in ("C03", "C04"):
            # BIR shift reconciliation. C03 is the "Row" report and C04 the
            # "Column" one, and they are two LAYOUTS and not two names for one
            # -- they answered with identical text until this was noticed.
            if not self.c.licensed("bir"):
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.tank_level))
            if tok == "251":
                if code[0].isupper():
                    text = "\n" + self.c.csld.report(tanks) + "\n"
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
                return self._nine(code), "no probe module fitted"
            which = (data or "0")[:1]
            if which not in ("0", "1"):
                return self._nine(code), "REJECTED: delivery type is 0 or 1"
            word = "NEXT" if which == "0" else "LAST"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = [f"{word} DELIVERY DENSITY", "",
                        "TANK   PRODUCT LABEL               DENSITY"]
                for tank in tanks:
                    label = self.c.text("602", tank) or ""
                    got = self.c.delivery_density_value(tank, which)
                    rows.append(f"{tank:3d}     {label:<26.26s}{got:7.4f}")
                # the manual echoes the delivery type on the display form
                # and carries it in the DATA on the computer form:
                # "<SOH> I61FTT0" against "i61FTTYYMMDDHHmmTTtFFFFFFFF"
                return (self._frame(code + which, SEP.join(rows)),
                        f"{word.lower()} delivery density")
            body = "".join(
                f"{tank:02d}{which}"
                + packed.hexfloat(self.c.delivery_density_value(tank, which))
                for tank in tanks)
            return self._frame(code, body), f"{word.lower()} delivery density"
        if tok in ("B91", "B93", "B94"):
            # AccuChart Diagnostics, Status and Calibration History
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
            tanks = ([int(dev)] if dev != "00"
                     else sorted(self.c.tank_level))
            text = self.c.deliveries.ticketed_report(tanks)
            if code[0].isupper():
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
            # 121 is a fourth, undocumented, and it answers as 113 does --
            # see the note beside `REPORTS` above.
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
                return self._nine(code), "no Maintenance Tracker fitted"
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
                return self._nine(code), "CSLD not installed"
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
                return self._nine(code), "no pump sense module fitted"
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
                return self._nine(code), f"no {kind} module fitted"
            if not code[0].isupper():
                return self._nine(code), "display format only"
            lines = self.c.line_setup_lines(kind, self._devices_of(kind, dev))
            return self._frame(code, SEP.join(lines)), "line leak setup"
        if tok == "204":
            # In-Tank Shift Inventory Report, one block per shift held
            if not self.c.licensed("bir"):
                return self._nine(code), "BIR not installed"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append("TANK PRODUCT")
                    rows.append(f"{tank:3d}  {label:<18.18s}"
                                "  VOLUME TC VOLUME  ULLAGE  HEIGHT"
                                "  WATER   TEMP")
                    for n, row in enumerate(self._shift_rows(tank), start=1):
                        rows += self._shift_lines(tank, n, row)
                return self._frame(code, SEP.join(rows)), "shift inventory"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                for n, row in enumerate(self._shift_rows(tank), start=1):
                    values = (self._gauges(tank, row, "opening")
                              + self._gauges(tank, row, "physical")
                              + [row["physical"] - row["opening"]])
                    body += f"{tank:02d}{pcode}{n:02d}"
                    body += packed.hexfloats(values)
            return self._frame(code, body), "shift inventory"
        if tok == "207":
            # In-Tank Leak Test History Report
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["TANK LEAK TEST HISTORY"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows += self.c.leak_history_lines(tank)
                return self._frame(code, SEP.join(rows)), "leak test history"
            body = ""
            for tank in tanks:
                records = self.c.leak_history_records(tank)
                body += f"{tank:02d}{len(records):02X}"
                for kind_code, number, result in records:
                    full = self.c.full_volume(tank) or 0.0
                    pct = (result.volume / full * 100.0) if full else 0.0
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
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["ADJUSTED DELIVERY REPORT"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows.append("                       INCREASE   INCREASE"
                                "            DELIVERY  DELIVERY")
                    rows.append("INCREASE DATE/TIME       VOLUME  TC VOLUME"
                                "  ADJUSTMENT  VOLUME TC VOLUME")
                    for record in self.c.deliveries.records.get(tank) or []:
                        if not record.end:
                            continue
                        rows.append(f"{clock_words(record.end['at']):22s}"
                                    f"{record.amount:9.0f}{record.tc_amount:11.0f}"
                                    f"{record.sold:12.0f}"
                                    f"{record.amount + record.sold:8.0f}"
                                    f"{record.tc_amount + record.sold:10.0f}")
                return self._frame(code, SEP.join(rows)), "adjusted delivery"
            body = ""
            for tank in tanks:
                pcode = (self.c.text("603", tank) or " ")[:1] or " "
                records = [r for r in (self.c.deliveries.records.get(tank)
                                       or []) if r.end]
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
                    return (self._nine(code),
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
                    return self._nine(code), "needs the ISD software module"
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
                    return self._nine(code), "needs the ISD software module"
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
                    return self._nine(code), "needs the ISD software module"
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
                    reqs = [(lo, hi) for _l, lo, hi, only
                            in isd.CARB_REQUIREMENTS if only == site]
                    body += f"{len(reqs):02d}"
                    for lo, hi in reqs:
                        body += "01" + packed.hexfloats([lo, hi])
                    thresholds = [r for r in isd.CARB_THRESHOLDS
                                  if r[5] in (site, "any")]
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
                    return self._nine(code), "needs the ISD software module"
                assist = self._isd_evr() == "02"
                site = "assist" if assist else "balance"
                if display:
                    rows = self._isd_carb_lines() + [isd.CARB_FOOTER]
                    return self._frame(code, SEP.join(rows)), "carb thresholds"
                reqs = [(lo, hi) for _l, lo, hi, only in isd.CARB_REQUIREMENTS
                        if only == site]
                body = f"{len(reqs):02d}"
                for lo, hi in reqs:
                    body += "01" + packed.hexfloats([lo, hi])
                rows = [r for r in isd.CARB_THRESHOLDS
                        if r[5] in (site, "any")]
                body += f"{len(rows):02d}"
                for i, (_l, per, lo, hi, _u, _o) in enumerate(rows, 1):
                    values = [float(x) for x in (per.rstrip("dysmin"), lo, hi)
                              if x not in ("----", "")]
                    body += f"{i:02d}" + packed.hexfloats(values)
                return self._frame(code, body), "carb thresholds"
            if tok in ("V0A", "V0B"):
                if not self.c.licensed("isd"):
                    return self._nine(code), "needs the ISD software module"
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
                    return self._nine(code), "needs ISD or PMC"
                passed = self._isd_setup_ok()
                if display:
                    return (self._frame(code, "ISD/PMC TEST STATUS: "
                                        + ("PASS" if passed else "FAIL")),
                            "isd setup verification")
                return (self._frame(code, "0" if passed else "1"),
                        "isd setup verification")
            if tok in ("VC0", "VC1", "VC8"):
                if not self.c.licensed("pmc"):
                    return self._nine(code), "needs the PMC software module"
            elif not self.c.licensed("isd"):
                return self._nine(code), "needs the ISD software module"
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
                return self._nine(code), "needs the ISD software module"
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
                return self._nine(code), "no ISD software module"
            if code[0].isupper():
                return (self._frame(code, f"ISD VERSION: {isd.ISD_VERSION}"),
                        "isd version")
            return self._frame(code, isd.ISD_VERSION), "isd version"
        if tok in isd.SETUP:
            if not self._isd_licensed(tok):
                spec = isd.SETUP[tok]
                want = " and ".join(k.upper() for k in spec["needs"])
                return self._nine(code), f"needs the {want} software module"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
            tanks = [t for t in self._tanks(dev)
                     if self.c.probe_reference_distance(t)]
            if not tanks:
                return self._nine(code), "A07 is a Mag probe command"
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
                return self._nine(code), "stick height not enabled"
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
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
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
            if code[0].isupper():
                text = "\n" + ("\n\n".join(
                    reports[tok](t) for t in tanks)) + "\n"
                return self._frame(code, text), "tank chart"
            val = self.c.values.get(f"S63B{tanks[0]:02d}") if tok == "63B"                 else None
            return self._frame(code, val or ""), "tank chart"
        if tok == "217":
            # Tank Profile: which of the five a tank is on, per I217's table
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            record = ((data or "").strip()[:2] or "01") if tok == "2E2" else ""
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
                return self._nine(code), "no probe module fitted"
            asked = (data or "").strip()
            most = int(asked[0:2] or 5) if tok in ("213", "21B") and asked \
                else 5
            tanks = self._tanks(dev)
            title = {"213": "DELIVERY REPORT",
                     "215": "MASS/DENSITY DELIVERY REPORT",
                     "21B": "BIR ADJUSTED DELIVERY REPORT"}[tok]
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "Fuel Manager not installed"
            tanks = self._tanks(dev)
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "CSLD not installed"
            previous = (data or "").strip().startswith("1")
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["CSLD MONTHLY REPORT",
                        "PREVIOUS MONTH" if previous else "CURRENT MONTH",
                        "0.2 GAL/HR TEST"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows.append("PROBE SERIAL NUM "
                                + self.c.probe_serial(tank))
                    for at, state in self._csld_states(tank, previous):
                        rows.append(f"{clock_words(at):22s}"
                                    + hrmreports.CSLD_STATE[state])
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
                return self._nine(code), "Fuel Manager not installed"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), "no smart sensor module fitted"
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
                    when, clock = console.alarm_report_stamp(stamp)
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
            if not self.c.has("smart"):
                return self._nine(code), "no smart sensor module fitted"
            spec = sumpreports.SUMP_REPORTS[tok]
            sensors = self._devices_of("smart", dev)
            if code[0].isupper():
                rows = ["MAG SUMP LEAK TEST", spec["title"]]
                for number in sensors:
                    label = self.c.text("722", number) or f"SUMP {number}"
                    rows.append(f"s {number}:{label}")
                    got = self._sump_rows(number, spec["rows"])
                    if spec["tt"] == "status":
                        state = self.c.control_phase_of("sump", number)
                        rows.append("STATUS:"
                                    + sumpreports.SUMP_STATUS.get(state, ""))
                        if not got:
                            continue
                        one = got[0]
                        rows += [f"START HT: {one[0]:.3f} IN.",
                                 f"START TEMP: {one[1]:.1f} F",
                                 f"END HT: {one[2]:.3f} IN.",
                                 f"END TEMP: {one[3]:.1f} F",
                                 f"DURATION: {one[4]:.0f} MINS"]
                        if spec.get("full"):
                            rows += [f"TEMP RATE: {one[5]:.1f} F/HR",
                                     f"LEAK RATE: {one[6]:.4f} IN./HR"]
                    else:
                        rows.append("                       START  START"
                                    "   END    END  DURATION")
                        rows.append("START DATE/TIME       HEIGHT  TEMP"
                                    "  HEIGHT  TEMP  MINUTES")
                        for one in got:
                            rows.append(f"{clock_words(one[7]):22s}"
                                        f"{one[0]:7.3f}{one[1]:6.1f}"
                                        f"{one[2]:8.3f}{one[3]:6.1f}"
                                        f"{one[4]:6.0f}")
                return self._frame(code, SEP.join(rows)), "mag sump test"
            body = ""
            for number in sensors:
                got = self._sump_rows(number, spec["rows"])
                body += f"{number:02d}"
                if spec["tt"] == "status":
                    body += self.c.control_phase_of("sump", number)
                    if spec.get("full"):
                        body += "00"                      # abort reason
                else:
                    body += f"{len(got):02d}"             # a COUNT, not a status
                for one in got:
                    body += time.strftime("%y%m%d%H%M", time.localtime(one[7]))
                    values = list(one[0:5])
                    body += f"{len(values):02d}" + packed.hexfloats(values)
                    if spec.get("full"):
                        body += "01" + packed.hexfloat(one[5])
                        body += packed.hexfloat(one[8])
                        body += "01" + packed.hexfloat(one[6])
            return self._frame(code, body), "mag sump test"
        if tok in ("391", "392"):
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = []
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    # p.146 and p.147 head a tank with the number held right
                    # against column 6 and the label at 8, which is NOT how
                    # p.64 and p.71 head one
                    rows.append(f"TANK {tank:2d} {label}")
                    loads = self._loads_of(tank)
                    if tok == "391":
                        rows.append("NO START DATE/TIME VOLUME  TEMP   "
                                    "END DATE/TIME VOLUME  TEMP  TOTAL")
                        for n, one in loads:
                            rows.append(
                                f"{n:2d}  {self._tanker_stamp(one['start'])}"
                                f"{one['start_vol']:7.0f}"
                                f"{one['start_temp']:6.1f}  "
                                f"{self._tanker_stamp(one['end'])}"
                                f"{one['end_vol']:7.0f}"
                                f"{one['end_temp']:6.1f}"
                                f"{one['total']:7.0f}")
                    else:
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
                return self._nine(code), "no VMC interface fitted"
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
                return self._nine(code), "Fuel Manager not installed"
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
                     else sorted(self.c.comm_positions()) or [1])
            if tok == "88D":
                if code[0].isupper():
                    rows = ["COMMUNICATION DIAGNOSTIC"]
                    for n in ports:
                        # p.477 sets the colon at 12, the same column
                        # 888 and 889 set theirs
                        rows += [f"COMM BOARD  : {n} S-LINK",
                                 "MODEM TYPE : "
                                 + sumpreports.MODEM_TYPE["03"],
                                 "MODEM AUTO DETECTED: "
                                 + sumpreports.MODEM_TYPE["03"],
                                 "RSSI: 99  BER: 99"]
                    return self._frame(code, SEP.join(rows)), "SiteLink"
                return (self._frame(code, "".join(f"{n:02d}03039999"
                                                  for n in ports)),
                        "SiteLink")
            if code[0].isupper():
                return (self._frame(code, SEP.join(self._comm_status(ports))),
                        "comm status")
            body = f"{sum(len(self.c.comm_errors.get(n) or ()) for n in ports):02d}"
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
                return self._nine(code), "no Maintenance Tracker fitted"
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
                return self._nine(code), "no Maintenance Tracker fitted"
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
            info = self.c.software_info()
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
                        f"CREATED - {info['created']}",
                        pad("PC ROM CHECKSUM", "PASSED"),
                        "",
                        pad("PC RESET COUNTS", n["resets"]),
                        pad("PC COMM ERRORS", n["comm_errors"]),
                        pad("MC CKSUM ERRS", n["cksum_errors"]),
                        f"MC->PC COMMS ={n['to_pc']:>10d}",
                        f"MC<-PC COMMS ={n['from_pc']:>10d}"]
                return self._frame(code, SEP.join(rows)), "PC diagnostic"
            body = (f"{self.c.PC_SOFTWARE:<14.14s}"
                    f"{info['created']:<14.14s}" + "05"
                    + "00000000" * 5)
            return self._frame(code, body), "PC diagnostic"
        if tok in ("BA0", "BB1"):
            if not self.c.has("vmc"):
                return self._nine(code), "no VMC interface fitted"
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
            controllers = ([int(dev)] if dev != "00" and dev.isdigit()
                           and int(dev) else list(range(1, 4)))
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
                return self._nine(code), "no probe module fitted"
            tanks = self._tanks(dev)
            if code[0].isupper():
                rows = ["TANK LEAK TEST HISTORY"]
                for tank in tanks:
                    label = self.c.text("602", tank) or f"TANK {tank}"
                    rows.append(f"T {tank}:{label}")
                    rows += self.c.leak_history_lines(tank)
                return self._frame(code, SEP.join(rows)), "leak test history"
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
            body = f"SOFTWARE# {s['number']}CREATED - {s['created']}"
            if tok == "905":
                flags = self.c.revision_flags()
                body += f"{len(flags):02X}"
                body += "".join("01" if on else "00" for _name, on in flags)
                body += f"S-MODULE# {s['smodule']}"
            return self._frame(code, body), "revision level report"
        if not self._module_present(tok):
            return self._nine(code), "no module fitted for this function"
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
            return self._frame(code, self.c.aggregate(tok)), "device-00 aggregate"
        val = self.c.values.get(f"S{tok}{dev}")
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
        return self._frame(code, val), ""

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

    def display_value(self, tok, dev):
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

    def _head_gap(self, tok):
        """Blank lines between the frame and the first line of the body.

        **Not the constant this console took it for.** 512 of the manual's
        541 Display samples leave one and 29 leave none -- 613, 614, 902,
        903, 905 and 881 among them -- and a real console agrees with the
        page on every one of those that was captured. `build_wire_titles.py`
        measures it off the word boxes the same way it measures a column
        position, and it is `head_gap` in `wiretitles.json`. A code the
        manual does not draw keeps the commoner answer. See FIDELITY S6.
        """
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
        if val is None:
            if part.get("wire_default") is not None:
                return part["wire_default"]
            return panel_shows(self.c, part, "")
        return fieldio.decode(part, key, val)

    def _part_field_answer(self, tok, dev, val, code):
        """The Display answer for a code that exists only as part fields.

        None if this is not one of them, so the caller carries on.
        """
        from .console import FIELDS, SETUP_MENU
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
            # wrong decides which one comes back
            month, day = stamp[2:4], stamp[4:6]
            if not ("01" <= month <= "12" and "01" <= day <= "31"):
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
            return self._nine(code), "no probe module fitted"
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
        if tok in INQUIRE_ONLY:
            return (self._nine(code),
                    f"{tok} is an Inquire with no Set format")
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
        if tok in SETTABLE and not formats.valid(
                tok, data,
                aggregate=(dev == "00" and self.c.is_multi(tok)),
                computer=code[0].islower()):
            return (self._nine(code),
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
                return (self._nine(code),
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
                    return self._nine(code), "needs the PMC software module"
                if not (self.c.has("relay") or self.c.has("io")):
                    # "PMC Feature and Vapor Processor relay required"
                    return self._nine(code), "no vapor processor relay"
            elif not self.c.licensed("isd"):
                return self._nine(code), "needs the ISD software module"
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
            self.c.clear_isd_test(test)
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "needs the ISD software module"
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
                return self._nine(code), f"needs the {want} software module"
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
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), f"no {kind} module fitted"
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
                return self._nine(code), f"no {kind} module fitted"
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
                return self._nine(code), f"no {module} module fitted"
            if "149" not in (data or ""):
                return self._nine(code), "REJECTED: wants the 149 confirmation"
            devices = self._devices_of(module, dev)
            rows = []
            for number in devices:
                state = self.c.control_device(what, number)
                rows.append((number, state))
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
                return self._nine(code), "BIR not installed"
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
                return self._nine(code), "no probe module fitted"
            n = self.c.deliveries.clear(None if dev == "00" else int(dev))
            return self._frame(code), f"{n} delivery report(s) erased"
        if tok in ("052", "053"):
            # Start / Stop In-Tank Leak Detect Test, which a tool can do too
            if not self.c.has("probe"):
                return self._nine(code), "no probe module fitted"
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
                return self._nine(code), f"no {kind} module fitted"
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
                return self._nine(code), "no probe module fitted"
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
            return self._nine(code), "REJECTED: no module fitted"
        value = self._canonical(tok, dev, data, code)
        if value is None:
            return self._nine(code), "REJECTED: value out of range"
        # An older name writes the store its newer one owns, or the console
        # answers the report it has just been programmed through with the
        # value it had before. 505's language is one digit and 517's is two
        # -- 576013-635 Rev AA p.156 note 1 -- so the narrow one widens on
        # the way in rather than sitting in a key nothing reads.
        if tok in self.GROUP_ALIAS:
            tok = self.GROUP_ALIAS[tok]
            value = value[:1] + value[1:].rjust(2, "0")
        self.c.values[f"S{tok}{dev}"] = self._with_prefix(tok, dev, value)
        if tok == "501":
            # Set Time of Day sets the CLOCK, not just the stored string.
            # The panel path already did (ui calls set_clock after ENTER);
            # this path stored the digits and left the clock alone, so a
            # tool that set the time was answered politely and ignored.
            if not self.c.set_clock():
                return self._nine(code), "REJECTED: not a date and time"
            self.c.save()
            return self._frame(code), "clock set"
        if tok == "683":
            # 683 is `D` then the volume, and D picks one day of the week:
            # 0 all days, 1 Sunday .. 7 Saturday (576013-635 Rev AA p.330).
            # The console holds seven values per product and the panel draws
            # seven screens for them, so the day has to be unpacked rather
            # than left inside one string.
            body = value[2:] if value[:2] == dev else value
            day, rest = body[:1], body[1:].strip()
            for one in (range(1, 8) if day == "0" else [int(day or 0)]):
                if 1 <= one <= 7:
                    self.c.set_setting(f"avg_sales_{one}", rest,
                                       int(dev) if dev != "00" else 1)
        self.c.save()
        return self._frame(code), "stored"

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
              "flag": lambda f: 1}

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
        if not code[:1].isupper():
            return data                      # computer format: already packed
        from .console import FIELDS
        field = FIELDS.get(f"S{tok}" + (dev if dev != "00" else "00"))
        if field is None and dev != "01":
            field = FIELDS.get(f"S{tok}01")
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
        try:
            return pfx + fieldio.encode_value(field, text,
                                              self.c.metric(), self.c)
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
        if 0x790 <= fn <= 0x79F or 0x7B1 <= fn <= 0x7B6:
            return c.licensed("bir")
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


def serve(console, host, port, verbose=True, log=None, on_socket=None):
    """Answer the console's serial protocol on a TCP port.

    `on_socket` is handed the listening socket once it is up. The card's
    network supervisor keeps it so it can close it and move the tunnel when
    the address or the port programmed into the card changes -- which is
    what a real card does when you save a new port and it reboots.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    if on_socket:
        on_socket(srv)
    if verbose:
        print(f"[sim] TLS-350 listening on {host}:{port}")
    handler = Handler(console, verbose, log)
    while True:
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        if log:
            log(f"-- connection from {addr[0]}")
        threading.Thread(target=_session, args=(conn, handler),
                         daemon=True).start()


TERMINATORS = (13, 10, 3)      # carriage return, line feed, ETX


def _complete(buf):
    """How many bytes of `buf` are one command, or 0 if it is not one yet.

    The manual's command format has no terminator: "SOH, Security Code,
    Function Code, Data Field", and the function code "is a six character
    command code". So an inquiry IS complete at six characters, which is why
    a console answers a hand-typed session the moment you finish typing
    I20100, no Return needed. A Set carries a data field of its own length,
    so that one runs to a carriage return, line feed or ETX.
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
        return len(buf) - len(body) + 6
    return 0


def _session(conn, handler):
    buf, pending, telnet = bytearray(), bytearray(), False
    try:
        conn.sendall(PROBE)
    except OSError:
        return
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            pending += chunk
            data, saw, leftover = strip_telnet(bytes(pending))
            pending = bytearray(leftover)
            if saw and not telnet:
                # it answered, so it is a terminal: take over the echo
                telnet = True
                try:
                    conn.sendall(HELLO)
                except OSError:
                    break
            if telnet and data:
                # somebody is typing: show them what they typed
                try:
                    conn.sendall(echo_for(data))
                except OSError:
                    break
            # in order, because a rub-out only takes back what came before it
            for byte in data:
                if byte == 0x1B:
                    # ESC (576013-635 p.267): "a means to halt a response
                    # message at any time before its completion." Over a
                    # socket the reply goes out in one send, so what ESC can
                    # still do is abandon a command that is only part typed,
                    # which is the same intent: nothing part-formed survives.
                    buf.clear()
                elif byte in (0x08, 0x7F):
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
                if not n:
                    break
                raw, _ = bytes(buf[:n]), None
                del buf[:n]
                out = handler.handle(raw)
                if out:
                    # a terminal wants the reply on a line of its own, and the
                    # next thing it types on the line after that; a tool gets
                    # the frame and nothing else
                    if telnet:
                        lead = b"" if raw[-1:] in (b"\r", b"\n") else b"\r\n"
                        out = lead + out + b"\r\n"
                    conn.sendall(out)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
