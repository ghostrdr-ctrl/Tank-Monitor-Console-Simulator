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
"""The seven setup codes whose data is a LIST rather than a single value.

These were the last things in the setup range left untyped, and they were left
untyped for one reason: every other setup code holds one value of one width,
and each of these holds a run of them. Three different shapes of run, in fact,
which is why this is a module and not a `kind` in `fieldio`:

    52A   a count, then that many (report, on/off) pairs
    52B   a method digit, then a date field WHOSE WIDTH THE DIGIT DECIDES
    52C   one alarm assignment per Set, accumulating into a list
    612   a run of tank numbers, siphon
    61D   the same run, line
    75A   a type digit, then either one daily window or one of seven day rules
    7B1   five fixed fields, and no computer format at all

**52B and 75A are the interesting ones.** The field's own first character says
how long the rest of it is. `S52B01` followed by `5` wants four more
characters and followed by `1` wants ten, so a reader that fixes the width
before looking at the method reads the next command's bytes as this one's
minutes. That is the whole reason these two never got a `kind`: `fieldio`
decides a width from the field definition, and here the width is in the data.

**52A has a hole at 04.** Reports run 01, 02, 03, 05, 06 ... 19 and there is no
04 in the manual's list. It is not a typo to tidy up -- a console that accepts
04 is accepting a report it cannot send, so 04 is refused like any other
number outside the list.

**7B1 says so itself:** "Computer format is not supported for this command."
It is the only setup code in the manual that has no computer format, so a
lowercase `s7B100` is refused rather than answered. 680 is the only REPORT
that does the same.
"""

import time

from . import relays
from . import screens
from . import wiretables
from .clock import clock_date
from .meterid import MeterId

SEP = "\r\n"

# The five rows 550 and 551 both print, in the manual's own order and with
# its own labels -- the colon is at column 12 on every one of them, which is
# what makes OVERFILL and LOW PRODUCT carry their trailing spaces.
UNIT_ROWS = (("MAX OR LABEL", "max"), ("HIGH PRODUCT", "high"),
             ("OVERFILL", "overfill"), ("DELIV NEEDED", "delivery"),
             ("LOW PRODUCT", "low"))

# "C - Inventory Alarms Units Configuration", and "U - Units Type", both p.221
UNITS_CONFIG_CODE = {"STANDARD": "1", "ALL %FULL": "2", "ALL VOLUME": "3",
                     "ALL HEIGHT": "4", "CUSTOM": "5"}
UNITS_CODE = {"% MAX": "1", "%MAX": "1", "%FULL": "2", "% FULL": "2",
              "VOLUME": "3", "HEIGHT": "4"}
UNITS_WORD = {"1": "% MAX", "2": "%FULL", "3": "VOLUME", "4": "HEIGHT"}


def _units_config(console):
    return console.setting("inventory_units", 0, "STANDARD")


# 576013-635 Rev AA p.218's own twelve, in calendar order and printed with
# the month at column 0 and the pressure at 16, zero padded to two figures
# before the point: `JAN 14.0`, `JUL 08.0`.
RVP_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def rvp_table(console):
    """The twelve Reid Vapor Pressures, or [] where none has been entered.

    An all-zero table is not a table: it is how a site says there is none,
    which is what "if the Reid Vapor Pressure table has been entered" is
    asking about.
    """
    raw = (console.values.get("S54C00") or "").strip()
    out = []
    for n in range(12):
        piece = raw[n * 4:n * 4 + 4]
        try:
            out.append(float(piece))
        except ValueError:
            return []
    return out if any(out) else []

# ---------------------------------------------------------------------------
# 52A, the report list
# ---------------------------------------------------------------------------
# The manual's own numbering, hole and all. 04 does not exist.
REPORTS = {
    "01": "SYSTEM STATUS", "02": "PRIORITY ALARM HISTORY",
    "03": "NON-PRIORITY ALARM HISTORY", "05": "IN-TANK STATUS",
    "06": "IN-TANK INVENTORY", "07": "IN-TANK DELIVERY",
    "08": "IN-TANK LEAK TEST", "09": "SHIFT REPORT",
    "10": "PLLD RESULTS", "11": "WPLLD RESULTS",
    "12": "VOLUMETRIC LINE LEAK STATUS", "13": "PERIODIC ROW REPORT",
    "14": "FUEL MANAGEMENT REPORT", "15": "CSLD RESULTS",
    "16": "MOST RECENT DELIVERY REPORT",
    "17": "CURRENT PERIODIC DELIVERY VARIANCE REPORT",
    "18": "CURRENT PERIODIC BOOK VARIANCE REPORT",
    "19": "DAILY VARIANCE ANALYSIS REPORT",
}

# ---------------------------------------------------------------------------
# 52B, the auto dial method -- and the widths it decides
# ---------------------------------------------------------------------------
DIAL_METHOD = {"1": "ON DATE", "2": "ANNUALLY", "3": "MONTHLY",
               "4": "WEEKLY", "5": "DAILY"}

# method -> how many characters follow it. This table IS the trap: read it
# before slicing, never after.
DIAL_WIDTH = {"1": 10, "2": 8, "3": 6, "4": 5, "5": 4}

WEEKDAY = {"1": "MONDAY", "2": "TUESDAY", "3": "WEDNESDAY", "4": "THURSDAY",
           "5": "FRIDAY", "6": "SATURDAY", "7": "SUNDAY"}

# "HHmm=Hour, Minute (EE00=Disabled)" -- the disabled marker is not a time and
# must not be formatted as one.
DISABLED = "EE00"


def _clock(text):
    """"1045" as the console prints it, or DISABLED."""
    if text.upper() == DISABLED:
        return "DISABLED"
    hh, mm = int(text[:2]), int(text[2:])
    ampm = "AM" if hh < 12 else "PM"
    h = hh % 12 or 12
    return f"{h}:{mm:02d} {ampm}"


def _valid_clock(text):
    if text.upper() == DISABLED:
        return True
    return (len(text) == 4 and text.isdigit()
            and int(text[:2]) < 24 and int(text[2:]) < 60)


def _dial_text(raw):
    """The DIAL TYPE and START TIME columns for one receiver.

    START TIME is TWO values on the ON DATE method and the console keeps them
    in two columns: `JAN 16, 2006    DISABLED`, the date at 40 and the time
    at 56, off `tests/console_capture/raw/I52000.bin`. This wrote the pair as
    `01/16/06 DISABLED` -- the console's date in neither form nor place. The
    other four methods name a recurrence rather than a date and stay as they
    are; the capture has no example of one. See FIDELITY S18.
    """
    if not raw:
        return "", ""
    m, rest = raw[0], raw[1:]
    name = DIAL_METHOD.get(m, "")
    if m == "1":
        when = time.strptime(rest[0:6], "%y%m%d")
        return name, f"{clock_date(when):<16s}{_clock(rest[6:])}"
    if m == "2":
        return name, (f"MONTH {int(rest[0:2])} WEEK {rest[2]} "
                      f"{WEEKDAY.get(rest[3], '')} {_clock(rest[4:])}")
    if m == "3":
        return name, (f"WEEK {rest[0]} {WEEKDAY.get(rest[1], '')} "
                      f"{_clock(rest[2:])}")
    if m == "4":
        return name, f"{WEEKDAY.get(rest[0], '')} {_clock(rest[1:])}"
    return name, _clock(rest)


# What a LIST field shows on the PANEL. A list is a packed run whose shape
# the data itself decides, so there is no width in the field definition to
# decode it with -- which is why these have no `kind` that `fieldio`
# understands, and why the panel drew the payload. 52B is the one with a word
# for it: 576013-623 Rev AN p.6-11 draws the frequency screen as `ALL RCVRS`
# over `ON DATE`, and `_dial_text` has produced that word for the wire's own
# report all along. See FIDELITY N6a.
LIST_TEXT = {"52B": lambda body: _dial_text(body)[0]}


def list_text(code, body):
    """The panel's form of a packed list value, or the payload itself."""
    fn = LIST_TEXT.get((code or "")[1:4].upper())
    text = (body or "").strip()
    return fn(text) if fn and text else text


def _dial_ok(body):
    """Validate a 52B payload, method digit and all."""
    if not body:
        return False
    m, rest = body[0], body[1:]
    if m not in DIAL_WIDTH or len(rest) != DIAL_WIDTH[m]:
        return False
    if not _valid_clock(rest[-4:]):
        return False
    if m == "1":
        return (rest[:6].isdigit() and 1 <= int(rest[2:4]) <= 12
                and 1 <= int(rest[4:6]) <= 31)
    if m == "2":
        return (rest[:2].isdigit() and 1 <= int(rest[:2]) <= 12
                and rest[2] in "1234" and rest[3] in WEEKDAY)
    if m == "3":
        return rest[0] in "1234" and rest[1] in WEEKDAY
    if m == "4":
        return rest[0] in WEEKDAY
    return True


# ---------------------------------------------------------------------------
# 75A, the lockout schedule -- the other width-in-the-data field
# ---------------------------------------------------------------------------
LOCKOUT_TYPE = {"0": "DAILY", "1": "INDIVIDUAL"}


def _lockout_ok(body):
    if not body:
        return False
    kind, rest = body[0], body[1:]
    if kind == "0":
        return (len(rest) == 8 and _valid_clock(rest[:4])
                and _valid_clock(rest[4:]))
    if kind == "1":
        # "N = Lockout Number (0=All Lockouts, 1..7)", then start day/time and
        # end day/time
        return (len(rest) == 11 and rest[0] in "01234567"
                and rest[1] in WEEKDAY and _valid_clock(rest[2:6])
                and rest[6] in WEEKDAY and _valid_clock(rest[7:]))
    return False


def _lockout_text(raw):
    if not raw:
        # The title and its rule, and nothing under them. `NO LOCKOUT
        # SCHEDULE` was this project's phrase; an empty report on this
        # console is its heading alone. See FIDELITY S18 and U5.
        return []
    kind, rest = raw[0], raw[1:]
    if kind == "0":
        return ["LOCKOUT SCHEDULE", "DAILY",
                f"START TIME: {_clock(rest[:4])}",
                f"STOP TIME : {_clock(rest[4:])}"]
    number = "ALL LOCKOUTS" if rest[0] == "0" else f"LOCKOUT {rest[0]}"
    return ["LOCKOUT SCHEDULE", "INDIVIDUAL", number,
            f"START: {WEEKDAY.get(rest[1], '')} {_clock(rest[2:6])}",
            f"STOP : {WEEKDAY.get(rest[6], '')} {_clock(rest[7:])}"]


# ---------------------------------------------------------------------------
# 7B1, the BIR meter map
# ---------------------------------------------------------------------------
BUS = {"2": "POWER BUS (MDIM)", "3": "COMM BUS"}
# "Bus 2: 09-16, Bus 3: 01-06" -- a slot outside its own bus's range is not a
# map entry, it is a typo, and the console has nowhere to put it.
BUS_SLOTS = {"2": range(9, 17), "3": range(1, 7)}
# "FP - Fueling Position (00-99)" and "MM - Meter (00-99)", 576013-635 p.439,
# and 576013-818 p.12-11 gives the same two ranges. *(p.12-12's explanation
# writes "M = meter (0-9)" instead, which its own I7B100 sample on the
# facing page contradicts: that sample maps meters 10, 11 and 12. Both
# manuals' command notes say 00-99, and two sources beat one.)*
POSITIONS = range(0, 100)
METERS = range(0, 100)

# p.439's own columns: BUS at 1, SLOT at 6, FUEL_P at 12, METER at 20 and
# TANK at 27, over a rule of 31.
MAP_HEAD = ["FUELING POSITION - METER - TANK MAP",
            " BUS  SLOT  FUEL_P  METER  TANK", "-" * 31]
# "the command parameters will be repeated with the parameter in error
# replaced with ??", 576013-818 p.12-12.
MAP_BAD = "??"


def map_row(bus, slot, fp, meter, tank):
    """One row of the map report, wherever it is printed."""
    return f"{bus:<10s}{slot:<3s}{fp:<8s}{meter:<10s}{tank}"


def map_errors(c, fields):
    """Which of 7B1's five parameters are out of range, by index.

    "All parameters are checked before the command is performed" -- all of
    them, so a command with two mistakes in it is echoed with two `??`.
    A slot is judged against its own bus, so a bus nobody recognises leaves
    the slot unjudged rather than condemning it twice over.
    """
    bus, slot, fp, meter, tank = fields
    bad = set()
    if bus not in BUS_SLOTS:
        bad.add(0)
    if not slot.isdigit():
        bad.add(1)
    elif bus in BUS_SLOTS and int(slot) not in BUS_SLOTS[bus]:
        bad.add(1)
    if not fp.isdigit() or int(fp) not in POSITIONS:
        bad.add(2)
    if not meter.isdigit() or int(meter) not in METERS:
        bad.add(3)
    if tank != "-1" and (not tank.isdigit() or int(tank) > c.tank_count()):
        bad.add(4)
    return bad


def map_echo(fields, bad=(), tank=None):
    """The Set command's own answer: the row it wrote, or the row it refused.

    576013-818 p.12-12 prints four of these and every one is the report's
    head over a single row -- a successful `S7B100 3 1 18 1 1` answers with
    `3  1  18  1  1`, and its rejected `S7B100 3 1 108 3 2` answers with the
    same row and `??` where the fueling position was. This console answered
    a bare frame either way, and a 9999 for the rejection. FIDELITY G6.

    *The rule under the header is 576013-635 p.439's. 576013-818 draws none
    under any of its map samples, INCLUDING its two `I7B100` ones, where the
    other manual's sample of the same report has it -- so the missing rule
    is that manual's rendering rather than the console's paper.*
    """
    shown = [MAP_BAD if i in bad else f for i, f in enumerate(fields)]
    if tank is not None and 4 not in bad:
        shown[4] = tank
    return MAP_HEAD + [map_row(*shown)]


def map_set_tank(number):
    """The TANK column of a Set echo, which has one form the map has not.

    p.12-12's own three: `1` for a tank, `X` for the probeless one, and `-`
    for a removal -- "Removing FP18/M4 from the map ... TANK  -". The map's
    own `?` is a reported meter nobody has mapped, which is not a state a
    command can ask for, and the `*` a locked entry carries in the LIST is
    on none of the three echoes.
    """
    if number < 0:
        return "X"
    return "-" if not number else str(number)


# ---------------------------------------------------------------------------
# 7B4, the per-meter offset
# ---------------------------------------------------------------------------
# "FF - Fueling Position, MM - Meter Number, TT - Tank Number, o.oo - Meter
# Offset, percent (Decimal +/-9.99)". The THIRD code in the manual with no
# computer format, after 680 and 7B1.
def _offset_ok(body):
    if len(body) != 11:
        return False
    fp, meter, tank, sign, pct = (body[0:2], body[2:4], body[4:6],
                                  body[6], body[7:])
    if not (fp.isdigit() and meter.isdigit() and tank.isdigit()):
        return False
    if sign not in "+-":
        return False
    try:
        value = float(pct)
    except ValueError:
        return False
    return len(pct) == 4 and pct[1] == "." and value <= 9.99


def map_fields(body):
    """Normalise 7B1's data to the contiguous `B SS FP MM TT` widths.

    576013-635 p.439 writes the command `<SOH>S7B100 B SS FP MM TT`, spaces
    and all, and 576013-818 pp.12-11 and 12-12 use that form in five worked
    commands a technician is told to send -- `S7B100 3 1 18 1 1`,
    `S7B100 3 1 18 3 -1`, `S7B100 3 1 18 4 0`. Note the fields there are
    NOT padded: the slot is `1` and the fuel position `18`.

    This parser sliced fixed columns, so **every meter-map command printed
    in the manuals was rejected** while the packed form nobody writes was
    accepted. Both are taken now. See FIDELITY G6.
    """
    if " " in body.strip():
        parts = body.split()
        return parts if len(parts) == 5 else None
    if len(body) < 8:
        return None
    return [body[0], body[1:3], body[3:5], body[5:7], body[7:]]


def _map_ok(fields):
    """B SS FP MM TT, with TT allowed to be -1.

    What the PANEL is allowed to type, which has to be the same rule the
    wire applies or "the keypad can store something the serial port would
    have refused". `map_errors` is the wire's half and knows the console, so
    it can judge the tank number too; this one cannot and does not.

    The tank's two-digit rule went with the fixed columns: the manual writes
    `S7B100 3 1 18 1 1`, one digit at a time, and its slot is `1` rather
    than `01`.
    """
    if not fields:
        return False
    bus, slot, fp, meter, tank = fields
    if bus not in BUS_SLOTS or not slot.isdigit():
        return False
    if int(slot) not in BUS_SLOTS[bus]:
        return False
    if not fp.isdigit() or int(fp) not in POSITIONS:
        return False
    if not meter.isdigit() or int(meter) not in METERS:
        return False
    return tank == "-1" or tank.isdigit()


# ---------------------------------------------------------------------------
# 52D, and the word "ignored"
# ---------------------------------------------------------------------------
# "f - Alarm clear flag, 1=clear; all others ignored".
#
# IGNORED is not the same as REFUSED and it is not the same as STORED. The
# console takes the command, does nothing, and says so. This used to fall
# through to the generic setup path, which stored the payload verbatim -- so
# `S52D01` followed by anything at all became the receiver's setting, and
# `S52D011`, the one value that means something, cleared nothing at all
# because nothing was watching for it.
#
# The Inquire half was right the whole time, which is what made this hide: the
# code has a test proving `f` means opposite things on its two halves, and
# that test set `autodial_alarm` directly rather than going through the Set.
CLEAR_FLAG = "1"


# ---------------------------------------------------------------------------
# 8C1 and 8C2, the VMC serial number
# ---------------------------------------------------------------------------
# "IIIIII - Serial Number (Decimal)", six digits, on both. They are a pair
# that does opposite things -- Edit/Add against Remove -- and share a format
# exactly.
VMC_SERIAL = 6


def _serial_ok(body):
    return len(body) == VMC_SERIAL and body.isdigit()


# ---------------------------------------------------------------------------
# what the panel is allowed to type into one of these
# ---------------------------------------------------------------------------
def _reports_ok(body):
    if len(body) < 2 or not body[:2].isdigit():
        return False
    count, rest = int(body[:2]), body[2:]
    if len(rest) != count * 4:
        return False
    return all(rest[i * 4:i * 4 + 2] in REPORTS
               and rest[i * 4 + 2:i * 4 + 4] in ("00", "01")
               for i in range(count))


def _tanks_ok(body):
    digits = body.replace(",", "")
    return not digits or (digits.isdigit() and not len(digits) % 2)


VALIDATE = {
    "52A": _reports_ok,
    "52B": _dial_ok,
    "52C": lambda b: len(b) == 8 and b.isdigit() and b[6:] in ("00", "01"),
    "612": _tanks_ok,
    "61D": _tanks_ok,
    # 80C's tail is the same run of tank numbers: "You must identify which
    # tanks supply fuel to the generator ... If only one or some of the
    # tanks connected to the system supply fuel to this generator, enter
    # the individual tank numbers", 576013-623 Rev AN p.23-3, over a screen
    # that draws `TANK #: X, X`. Only the PART of 80C that holds the run is
    # a list field, so this is asked about that field and about nothing
    # else the code carries.
    "80C": _tanks_ok,
    "75A": _lockout_ok,
    # both forms: the packed one a tool sends and the space separated one
    # every worked command in the manuals is written in
    "7B1": lambda b: _map_ok(map_fields(b)),
    "7B4": _offset_ok,
    "8C1": _serial_ok,
    "8C2": _serial_ok,
}


# One legal value per list field, which is what a blank one starts from and
# what a test has to use: "AB" is a fine placeholder for a free text field and
# a refusal for every one of these.
SAMPLE = {
    "52A": "010101",              # one report, System Status, ON
    "52B": "50630",               # daily at 6:30 am
    "52C": "01010101",            # one alarm assignment, set
    "612": "02",                  # manifolded to tank 2
    "61D": "02",
    "80C": "01,03",               # a generator fed by tanks 1 and 3
    "75A": "022450445",           # daily, 10:45 pm to 4:45 am
    "7B1": "3030010-1",           # comm bus, slot 3, position 00, meter 10
    "7B4": "010101+0.00",         # position 1, meter 1, tank 1, no offset
    "8C1": "123456",
    "8C2": "123456",
}


def map_report(c):
    """The meter map, as both the wire and the paper print it.

    `I7B100` has always answered with this and it was written inline
    there. 576013-623 Rev AN p.17-5 sends a second reader to it: "Press
    PRINT to output a report of the current meter map for reference before
    proceeding", said of the MODIFY TANK/METER MAP screen, so the panel
    prints the same rows rather than a second rendering of them.
    """
    rows = list(MAP_HEAD)
    for key in sorted(c.meter_map):
        rows.append(map_row(str(key.bus), str(key.slot), str(key.fp),
                            str(key.meter), map_tank(c.meter_map[key])))
    if len(rows) == len(MAP_HEAD):
        # "The response from the I7B100 command should be TANK MAP EMPTY",
        # 576013-818 p.12-12, which is the check a technician is told to
        # make after clearing the map.
        rows.append("TANK MAP EMPTY")
    return rows


def map_tank(entry):
    """The TANK column of the meter map, which is not always a number.

    576013-818 p.12-11 prints all four forms in one sample:

        3   1   18   1   1        mapped to tank 1
        3   1   18   2   ?        reported and not mapped
        3   1   18   3   X        a probeless tank
        3   1   18   4   R        a retired meter
        3   1   18   5   2*       mapped to tank 2, and LOCKED

    "A manually mapped meter is considered locked. Auto meter mapping will
    not change a locked meter", and a meter "not reported by a POS within 24
    hours of the last report" is declared retired. This console printed the
    raw integer, and `-1` where the manual prints `X`. See FIDELITY G6.
    """
    tank = entry.get("tank")
    if entry.get("retired"):
        return "R"
    if tank in (-1, "-1"):
        return "X"
    if not tank:
        return "?"
    return f"{int(tank)}" + ("*" if entry.get("locked") else "")


def sample(code):
    """A legal value for this list field, or "" if it is not one."""
    return SAMPLE.get((code or "")[1:4].upper(), "")


def validate(code, text):
    """True if `text` is a legal value for the list field `code` holds.

    The panel and the wire have to agree about these or the keypad can store
    something the serial port would have refused, which is how a console ends
    up with a setting no tool can read back.
    """
    check = VALIDATE.get((code or "")[1:4].upper())
    return True if check is None else bool(check((text or "").strip()))


# ---------------------------------------------------------------------------
# the shared plumbing
# ---------------------------------------------------------------------------
MINE = {"52A", "52B", "52C", "612", "61D", "75A", "7B1", "7B4",
        "52D", "8C1", "8C2", "550", "551", "54C", "520",
        # 787, 7A7 and 75B are 52C's payload and 52C's report, for a LINE
        # rather than a receiver: "Set Pressure Line Leak Disable Alarm
        # Assignments" and its WPLLD and VLLD siblings, `AANNTTSS`, one
        # assignment a Set. They had no renderer, so `I78700` answered a
        # title and an empty body on a console with the card in it, where a
        # real one prints a block per line. See FIDELITY S17.
        "787", "7A7", "75B",
        # 808 is the same payload and the same report again, for a RELAY,
        # and 8BC is 808 at a later version number -- "Set Relay Alarm
        # Assignments II", whose own notes carry the identical `nn -
        # Number of Alarms to Follow (Hex)` repeat group. This console
        # kept ONE assignment for 808 -- a `digits` field of width 8, so a
        # second Set overwrote the first -- and had no field at all for
        # 8BC. See FIDELITY I1a and CLOSED U48.
        "808", "8BC"}

# Which line family each of the three belongs to, what its report is called,
# and the code its label lives at -- all three off the manual's own page.
LINE_DISABLE = {"787": ("plld", "PRESSURE LLD SETUP REPORT", "Q", "782"),
                "7A7": ("wplld", "WPLLD LLD   SETUP REPORT", "W", "7A2"),
                "75B": ("vlld", "LINE LEAK SETUP REPORT", "P", "760")}

# "Computer format is not supported for this command", said of the CODE and
# not of one direction of it. 680 is the report that says the same.
NO_COMPUTER_FORMAT = {"7B1", "7B4"}

# what card each one needs before it means anything
NEEDS = {"52A": "modem", "52B": "modem", "52C": "modem", "52D": "modem",
         "520": "modem", "7B1": "bir",
         "787": "plld", "7A7": "wplld", "75B": "vlld"}





def _disable_lines(console, kind, dev):
    """The lines a 787/7A7/75B command covers: the one asked for, or all.

    "QQ - Pressure Line Leak sensor number (Decimal, 00=All)", and a real
    console prints a block for every line the card serves whether or not
    anybody has assigned an alarm to it -- three blocks, `Q 1:` to `Q 3:`,
    on the captured console.
    """
    if dev.isdigit() and int(dev):
        return [int(dev)]
    numbers = [n for kind_, n, _label in console.programmed_lines()
               if kind_ == kind]
    return numbers or list(range(1, max(console.capacity(kind), 0) + 1))


def _relays(console, dev):
    """The relays an 808 command covers: the one asked for, or all of them.

    The CONFIGURED ones, not every position the cards could carry: the
    captured console has no relay module and answers `I80600`, `I80700`
    and `I80800` with a stamp and an empty body -- no report title, where
    `I78700` on the same console prints one for each of its three pressure
    lines. A console with nothing to report on this code says nothing.
    """
    if dev.isdigit() and int(dev):
        return [int(dev)]
    return list(console.outputs.configured())


def _receivers(console, dev):
    """"RR - Receiver Number (Decimal, 00=all)"."""
    if dev.isdigit() and int(dev):
        return [int(dev)]
    return list(console.receivers())


def _partner_text(partners):
    """One manifold column of I612 and I61D: the tanks, or NONE.

    576013-818 Rev AB chapter 12 prints `NONE` in all three of its samples,
    and 635 Rev AA p.275 prints the partner's bare number. A set of three or
    more tanks would print two numbers in the one column and no page on this
    shelf draws that, so the space between them is the only part of this
    column not quoted from a sample.
    """
    return " ".join(str(n) for n in partners) if partners else "NONE"


def handle(handler, tok, dev, code, data):
    """Answer one of the seven, or None if it is not ours."""
    if tok not in MINE:
        return None
    c = handler.c
    need = NEEDS.get(tok)
    if need and not (c.has(need) if need != "bir" else c.licensed("bir")):
        return handler._nine(code), f"no {need} fitted"
    setting = code[0] in "Ss"
    body = (data or "").strip()

    if tok == "520" and setting:
        # 520's REPORT is 52B's, and its Set is not. The manual's "II" form
        # adds methods 6 and 7 (Reserved) and an 8 whose payload is a single
        # flag, none of which `_dial_ok` knows, so validating a 520 Set with
        # 52B's rule would refuse commands the page allows. The Set stays on
        # the general field path it was already on.
        return None

    if tok in NO_COMPUTER_FORMAT and code[0].islower():
        # the manual's own words: "Computer format is not supported for this
        # command", and it is a property of the code rather than of the Set
        return handler._nine(code), f"{tok} has no computer format"

    if setting:
        return _set(handler, tok, dev, code, body)
    return _inquire(handler, tok, dev, code)


def _set(handler, tok, dev, code, body):
    c = handler.c
    if tok == "54C":
        # "GG.G - 12 Reid Vapor Pressures (Decimal) ... The command will be
        # rejected if any value is outside the range 0.0 to 15.0, or all
        # table values are zero" -- and an all-zero table is how a site says
        # there is none, so it is the one way to clear the chart rather than
        # a rejection.
        if len(body) != 48:
            return handler._nine(code), "REJECTED: wants twelve of GG.G"
        try:
            values = [float(body[n * 4:n * 4 + 4]) for n in range(12)]
        except ValueError:
            return handler._nine(code), "REJECTED: not twelve numbers"
        if any(not 0.0 <= v <= 15.0 for v in values):
            return handler._nine(code), "REJECTED: 0.0 to 15.0"
        c.values["S54C00"] = "".join(f"{v:04.1f}" for v in values)
        return handler._frame(code, ""), "Reid vapor pressures"

    if tok == "550":
        # "C - Inventory Alarms Units Configuration". The panel and the wire
        # were two stores that did not talk: setting Custom over the wire
        # left the panel on STANDARD and the report reading the panel.
        word = next((w for w, digit in UNITS_CONFIG_CODE.items()
                     if digit == body[:1]), None)
        if word is None:
            return handler._nine(code), "REJECTED: 1 to 5"
        # ONE store, which is the whole of FIDELITY F9: the setting is it, and
        # `values["S55000"]` was a second copy that nothing ever read back.
        # A write-only duplicate cannot be seen to be wrong, so it is the kind
        # that drifts; `water_filter` on 642 keeps no such copy and is the
        # pattern this follows.
        c.set_setting("inventory_units", word, 0)
        return handler._frame(code, ""), "inventory alarm units"
    if tok == "551":
        # "A - Alarm Type", "U - Units Type", and note 3: "Alarm Type Max
        # Product cannot be set to unit type % Max"
        if len(body) < 2 or not body[:2].isdigit():
            return handler._nine(code), "REJECTED: wants an alarm and a unit"
        alarm, unit = body[0], body[1]
        if alarm not in "12345" or unit not in "1234":
            return handler._nine(code), "REJECTED: out of range"
        if alarm == "1" and unit == "1":
            return (handler._nine(code),
                    "REJECTED: Max Product cannot be % Max")
        row = UNIT_ROWS[int(alarm) - 1][1]
        c.set_setting(f"custom_{row}", UNITS_WORD[unit], 0)
        return handler._frame(code, ""), "inventory alarm custom units"
    if tok == "52A":
        if len(body) < 2 or not body[:2].isdigit():
            return handler._nine(code), "REJECTED: wants a count"
        count, rest = int(body[:2]), body[2:]
        if len(rest) != count * 4:
            return (handler._nine(code),
                    f"REJECTED: {count} reports wants {count * 4} characters")
        picks = {}
        for i in range(count):
            rr, ss = rest[i * 4:i * 4 + 2], rest[i * 4 + 2:i * 4 + 4]
            if rr not in REPORTS or ss not in ("00", "01"):
                return handler._nine(code), f"REJECTED: report {rr}"
            picks[rr] = ss
        for r in _receivers(c, dev):
            c.receiver_reports.setdefault(r, {}).update(picks)
        c.save()
        return handler._frame(code), f"{count} report(s) set"

    if tok == "52B":
        if not _dial_ok(body):
            return handler._nine(code), "REJECTED: method and width disagree"
        for r in _receivers(c, dev):
            c.receiver_dial[r] = body
        c.save()
        return (handler._frame(code),
                f"auto dial {DIAL_METHOD[body[0]].lower()}")

    if tok in LINE_DISABLE:
        # The same `AANNTTSS` 52C takes, against a LINE instead of a receiver.
        kind = LINE_DISABLE[tok][0]
        if len(body) != 8 or not body[6:].isdigit():
            return handler._nine(code), "REJECTED: wants AANNTTSS"
        aa, nn, tt, ss = body[:2], body[2:4], body[4:6], body[6:]
        if ss not in ("00", "01"):
            return handler._nine(code), "REJECTED: status is 00 or 01"
        for number in _disable_lines(c, kind, dev):
            # Through the console's own door, so the group screen chapter 25
            # asks first -- `Q1: (Name)` over `IN-TANK ALARMS: NO` -- reads
            # YES for a line the wire has just given an in-tank alarm to.
            c.assign_line_disable_alarm(kind, number, aa, nn, tt,
                                        ss == "01")
        c.save()
        return (handler._frame(code),
                "line disable assigned" if ss == "01" else "cleared")

    if tok in ("808", "8BC"):
        # 52C's payload again, for a relay: `AANNTTss`, one assignment a
        # Set, ss putting it on the list or taking it off. 8BC is the same
        # command with a later version number -- see the note on RELAY_ALARM.
        if len(body) != 8 or not body[6:].isdigit():
            return handler._nine(code), "REJECTED: wants AANNTTSS"
        aa, nn, tt, ss = body[:2], body[2:4], body[4:6], body[6:]
        if ss not in ("00", "01"):
            return handler._nine(code), "REJECTED: status is 00 or 01"
        numbers = _relays(c, dev)
        if not numbers:
            # "RR - Relay number (Decimal, RR>00)" on 808, and a relay the
            # console has not got is not a relay
            return handler._nine(code), "no such relay"
        for r in numbers:
            c.assign_relay_alarm(r, aa, nn, tt, ss == "01")
        return (handler._frame(code),
                "relay alarm assigned" if ss == "01" else "relay alarm cleared")

    if tok == "52C":
        # "AANNTTSS" -- one assignment per Set, and SS says whether it goes on
        # the list or comes off it
        if len(body) != 8 or not body[6:].isdigit():
            return handler._nine(code), "REJECTED: wants AANNTTSS"
        aa, nn, tt, ss = body[:2], body[2:4], body[4:6], body[6:]
        if ss not in ("00", "01"):
            return handler._nine(code), "REJECTED: status is 00 or 01"
        for r in _receivers(c, dev):
            rows = c.receiver_alarms.setdefault(r, [])
            key = (aa, nn, tt)
            rows[:] = [x for x in rows if x != key]
            if ss == "01":
                rows.append(key)
        c.save()
        return (handler._frame(code),
                "alarm assigned" if ss == "01" else "alarm cleared")

    if tok in ("612", "61D"):
        digits = body.replace(",", "")
        if digits and (not digits.isdigit() or len(digits) % 2):
            return handler._nine(code), "REJECTED: wants tank numbers"
        tank = int(dev) if dev.isdigit() and int(dev) else 1
        partners = []
        for i in range(0, len(digits), 2):
            n = int(digits[i:i + 2])
            if n and n != tank and n not in partners:
                if n > c.tank_count():
                    return handler._nine(code), f"REJECTED: no tank {n}"
                partners.append(n)
        if tok == "612":
            # "You only need to enter this information for one of the tanks
            # in the set. The system automatically enters the information for
            # the other tank(s) in the set" -- 576013-623 Rev AN p.7-20, and
            # this wrote one end of it. See FIDELITY Y8.
            c.manifold_together(tank, partners)
        else:
            c.values[f"S{tok}{tank:02d}"] = "".join(f"{n:02d}"
                                                    for n in partners)
        c.save()
        return (handler._frame(code),
                f"tank {tank} manifolded to {partners or 'nothing'}")

    if tok == "75A":
        if not _lockout_ok(body):
            return handler._nine(code), "REJECTED: type and width disagree"
        c.values["S75A00"] = body
        c.save()
        return handler._frame(code), f"lockout {LOCKOUT_TYPE[body[0]].lower()}"

    if tok == "52D":
        # the whole command is one character, and every character except "1"
        # is a no-op rather than a value
        if body.strip() == CLEAR_FLAG:
            for r in _receivers(c, dev):
                c.autodial_alarm[r] = False
            c.save()
            return handler._frame(code), "autodial alarm cleared"
        return handler._frame(code), "ignored: only 1 clears"

    if tok in ("8C1", "8C2"):
        if not _serial_ok(body):
            return (handler._nine(code),
                    f"REJECTED: wants {VMC_SERIAL} decimal digits")
        number = int(dev) if dev.isdigit() and int(dev) else 1
        if tok == "8C1":
            c.vmc_serials[number] = body
            c.values[f"S8C1{number:02d}"] = body
            c.save()
            return handler._frame(code), f"VMC {number} serial {body}"
        # 8C2 REMOVES, and only if that is the serial actually held --
        # otherwise a typo silently unregisters a controller that was fine
        held = c.vmc_serial(number)
        if held != body:
            return (handler._nine(code),
                    f"REJECTED: VMC {number} holds {held}")
        c.vmc_serials.pop(number, None)
        c.values.pop(f"S8C1{number:02d}", None)
        c.save()
        return handler._frame(code), f"VMC {number} serial removed"

    if tok == "7B4":
        if not _offset_ok(body):
            return handler._nine(code), "REJECTED: wants FF MM TT +o.oo"
        fp, meter, tank = int(body[0:2]), int(body[2:4]), int(body[4:6])
        pct = float(body[7:]) * (-1.0 if body[6] == "-" else 1.0)
        # The panel's INDIVIDUAL METER OFFSET row writes the same three
        # fields and a percent. See `Console.set_meter_offset`.
        c.set_meter_offset(fp, meter, tank, pct)
        return handler._frame(code), f"meter {meter} offset {pct:+.2f}%"

    # 7B1
    fields = map_fields(body)
    if fields is None:
        # Five fields is what the command IS; anything else is not a 7B1
        # with a bad parameter in it, it is not a 7B1, and the console has
        # no parameter to point at. 9999 is the wire's own "I did not
        # understand that", and it is what a real console answers a
        # malformed command with.
        return handler._nine(code), "REJECTED: wants B SS FP MM TT"
    bad = map_errors(c, fields)
    if bad:
        # "All parameters are checked before the command is performed. If an
        # error is detected, the command parameters will be repeated with
        # the parameter in error replaced with ??" -- a REPORT, not a 9999,
        # and the page's own example of one is a fueling position of 108.
        # FIDELITY G6.
        return (handler._frame(code, SEP.join(map_echo(fields, bad))),
                "REJECTED: " + " ".join(sorted(
                    ("bus", "slot", "position", "meter", "tank")[i]
                    for i in bad)))
    bus, slot, fp, meter, tank = (int(fields[0]), int(fields[1]),
                                  int(fields[2]), int(fields[3]), fields[4])
    # "The meter must be identified by bus, slot, real FP, and real M" --
    # all four of them, and the key is all four. The manual's own I7B100
    # sample maps meter 10 on seven fueling positions to two different
    # tanks, which a store keyed on the meter number cannot hold at all.
    # FIDELITY G7.
    where = f"FP {fp} M {meter}"
    number = -1 if tank == "-1" else int(tank)
    # The panel's MODIFY TANK/METER MAP screen writes the same five fields
    # and goes through the same method. See `Console.map_meter`.
    c.map_meter(bus, slot, fp, meter, number)
    note = (f"{where} unmapped" if not number
            else f"{where} on tank {number}")
    # Every one of p.12-12's four worked responses is the report's head over
    # the single row the command wrote. This answered a bare frame.
    return (handler._frame(code, SEP.join(
        map_echo(fields, tank=map_set_tank(number)))), note)


def _inquire(handler, tok, dev, code):
    c = handler.c
    display = code[0].isupper()

    if tok == "54C":
        table = rvp_table(c) or [0.0] * 12
        if display:
            rows = ["CSLD EVAP CONSTANTS", "REID VAPOR PRESSURE:"]
            rows += [f"{month:<16s}{value:04.1f}"
                     for month, value in zip(RVP_MONTHS, table)]
            return handler._frame(code, SEP.join(rows)), "Reid vapor pressures"
        from . import packed
        return (handler._frame(code,
                               "".join(packed.hexfloat(v) for v in table)),
                "Reid vapor pressures")

    if tok in ("550", "551"):
        # 576013-635 Rev AA p.221 and p.222 draw the SAME seven line block
        # for both codes and give them the SAME computer format, `CMHODL`.
        # This console answered `INVENTORY ALARMS UNITS: Custom` and stopped,
        # and sent one character where the manual sends six -- so a tool
        # reading fields 2 through 6 was reading the checksum. See FIDELITY
        # S8.
        # Both pages leave a line under the title, and `wiretables.lead`
        # cannot put it there: it matches a line by its WORDS and this one
        # carries the site's own configuration, which is not the sample's.
        rows = ["INVENTORY ALARMS UNITS", "", f"CONFIG: {_units_config(c)}"]
        for label, row in UNIT_ROWS:
            rows.append(f"{label:<12s}: {screens.threshold_units(c, row)}")
        if display:
            return handler._frame(code, SEP.join(rows)), "inventory alarm units"
        body = UNITS_CONFIG_CODE.get(_units_config(c), "1")
        body += "".join(UNITS_CODE.get(screens.threshold_units(c, row), "3")
                        for _label, row in UNIT_ROWS)
        return handler._frame(code, body), "inventory alarm units"
    if tok == "52A":
        rows, body = ["RECEIVER REPORT LIST"], ""
        for r in _receivers(c, dev):
            picks = c.receiver_reports.get(r, {})
            on = sorted(k for k, v in picks.items() if v == "01")
            rows.append(f"RCVR {r}: {c.text('522', r)}".rstrip())
            # A receiver with nothing selected gets no lines under it.
            #
            # *This one is worth a second look and is flagged in U5.* Its
            # immediate sibling 52C DOES mark an empty block, and the words
            # are in the capture verbatim -- `- NO ALARM ASSIGNMENTS -` on
            # `I78700` and `I61500` -- so the dashed style is real for that
            # report. What a REPORT LIST block reads when nothing is
            # selected is on no page and in no capture, and `- NONE -` was
            # a guess at it. See FIDELITY U5.
            rows += [f"     {REPORTS[k]}" for k in on]
            body += f"{r:02d}{len(on):02d}" + "".join(k + "01" for k in on)
        if display:
            return handler._frame(code, SEP.join(rows)), "report list"
        return handler._frame(code, body), "report list"

    # An unlabelled receiver's label column is BLANK, the way an unlabelled
    # tank's is. `receiver_label` still answers `RECEIVER n` and the PANEL
    # still wants that; a real console's `I52000` prints eight rows with
    # nothing in the label column at all. See FIDELITY S17.
    if tok in ("52B", "520"):
        # p.198: RCVR right against column 3, LOCATION LABEL at 7, DIAL TYPE
        # at 28 and START TIME at 40, off the word boxes.
        #
        # 40 and not 41. The page disagrees with ITSELF about that column --
        # its heading puts START TIME at 40 and its sample row puts
        # `4:15 AM` at 41 -- and a real console agrees with the heading:
        # `tests/console_capture/raw/I52000.bin` starts the date at 40 on
        # all eight rows. See FIDELITY S18.
        #
        # 520 is the SAME report. The manual names it "Set Receiver Auto
        # Dial Type and Start Time II" against 52B's "Set Receiver Auto
        # Dial Type and Start Time" -- a Version 20 extension of a
        # Version 3 code, the way 5BC extends 52C -- and p.185 draws it
        # under the identical heading. It had no renderer, and the
        # generic table builder cannot fill a four column row from one
        # packed dial spec, so it printed nothing at all.
        rows = ["RECEIVER AUTO DIAL TYPE & START TIME",
                "RCVR   LOCATION LABEL       DIAL TYPE   START TIME"]
        body = ""
        for r in _receivers(c, dev):
            raw = c.receiver_dial_spec(r)
            kind, when = _dial_text(raw)
            rows.append((f"{r:4d}   {c.text('522', r):<21.21s}"
                         f"{kind:<12s}{when}").rstrip())
            body += f"{r:02d}" + raw
        if display:
            return handler._frame(code, SEP.join(rows)), "auto dial setup"
        return handler._frame(code, body), "auto dial setup"

    if tok in LINE_DISABLE:
        kind, title, letter, label_code = LINE_DISABLE[tok]
        rows, body = [title], ""
        for number in _disable_lines(c, kind, dev):
            # A blank line stands above every block, the first one included:
            # the captured console prints the title, a blank, `Q 1:`, its
            # assignments, a blank, `Q 2:` and so on.
            #
            # `Q 1:REGULAR UNLEADED` on the page; on the console, `Q 1:`
            # followed by twenty spaces -- the label column, padded, on a
            # line nobody has named.
            rows.append("")
            rows.append(f"{letter} {number}:"
                        + f"{c.text(label_code, number) or '':<20.20s}")
            mine = c.line_disable_alarms.get((kind, number), [])
            for aa, nn, tt in mine:
                rows.append(f"     {c.alarm_name(aa, nn)}"
                            + (f" TANK {int(tt)}" if int(tt) else ""))
            if not mine:
                rows.append("- NO ALARM ASSIGNMENTS -")
            body += (f"{number:02d}{len(mine):02X}"
                     + "".join(aa + nn + tt + "01" for aa, nn, tt in mine))
        if display:
            return handler._frame(code, SEP.join(rows)), "line disable alarms"
        return handler._frame(code, body), "line disable alarms"

    if tok in ("808", "8BC"):
        # `RELAY SETUP REPORT`, and a block per relay: the relay and its
        # label, its type and orientation, and then its assignments. The
        # title and the `TYPE:` line are 576013-635 Rev AA's own -- p.454's
        # sample for 808 and p.478's for 8BC both carry them -- and the
        # block's SHAPE is 787's, which is the only one of this family a
        # real console has been captured answering. See UNKNOWNS.
        #
        # And NOTHING at all where the console has no relay to report on,
        # which is what the captured console does with this code and with
        # the two beside it. See `_relays`.
        rows, body = [], ""
        numbers = _relays(c, dev)
        if numbers:
            rows.append("RELAY SETUP REPORT")
        for r in numbers:
            rows.append("")
            rows.append(f"R {r}:" + f"{c.text('807', r) or '':<20.20s}")
            rows.append("TYPE:   "
                        + relays.TYPE_WORDS.get(c.outputs.kind(r), "STANDARD"))
            rows.append("NORMALLY CLOSED"
                        if c.outputs.orientation(r) == relays.NORMALLY_CLOSED
                        else "NORMALLY OPEN")
            mine = c.relay_alarms.get(r, [])
            for aa, nn, tt in mine:
                rows.append(f"     {c.alarm_name(aa, nn)}"
                            + (f" TANK {int(tt)}" if int(tt) else ""))
            if not mine:
                rows.append("- NO ALARM ASSIGNMENTS -")
            body += (f"{r:02d}{len(mine):02X}"
                     + "".join(aa + nn + tt + "01" for aa, nn, tt in mine))
        if display:
            return handler._frame(code, SEP.join(rows)), "relay alarms"
        return handler._frame(code, body), "relay alarms"

    if tok == "52C":
        rows, body = ["RECEIVER SETUP REPORT"], ""
        for r in _receivers(c, dev):
            rows.append(f"D {r}: {c.text('522', r)}".rstrip())
            mine = c.receiver_alarms.get(r, [])
            for aa, nn, tt in mine:
                rows.append(f"     {c.alarm_name(aa, nn)}"
                            + (f" TANK {int(tt)}" if int(tt) else ""))
            if not mine:
                rows.append("- NO ALARM ASSIGNMENTS -")
            body += (f"{r:02d}{len(mine):02X}"
                     + "".join(aa + nn + tt + "01" for aa, nn, tt in mine))
        if display:
            return handler._frame(code, SEP.join(rows)), "alarm assignments"
        return handler._frame(code, body), "alarm assignments"

    if tok in ("612", "61D"):
        # The same rule every other tank table on this shelf follows: a
        # FITTED probe module puts its positions on the setup report whether
        # or not anybody has switched them on. `wiretables._connected` writes
        # that down and names I61200 in the list, and this renderer -- which
        # lives in another module -- counted PROGRAMMED tanks instead, so a
        # console with an empty cage answered with one row where
        # `tests/console_capture/raw/I61200.bin` has four. See FIDELITY S18.
        # Both codes, because it is ONE table asked for two ways: a tank
        # that 61D has a partner for is a row of 612's report too, and
        # walking only the asked code's own store made the two answers
        # different lengths.
        tanks = sorted(set(wiretables._devices(handler, "612", dev))
                       | set(wiretables._devices(handler, "61D", dev)))
        # NONE, not a dash. All three of chapter 12's samples print the word
        # for a tank that is in no set -- " 1     UNLEADED SOUTH
        # NONE" -- and a dash appears on none of them. FIDELITY S12.
        # p.265 and p.275: the tank right against 1, the label at 7, and
        # both partner lists right against 34 and 61.
        #
        # TWO columns is right, and the reason is worth writing down because
        # FIDELITY S12 read it the other way. 576013-818 Rev AB chapter 12
        # prints three I612 samples with ONE column headed MANIFOLDED TANKS
        # -- and they are dated 1997, 1998 and 1999, while 61D, Set Tank LINE
        # Manifolded Partners, is a Version 23 function. There was only one
        # kind of manifold to report when those pages were printed. 635 Rev
        # AA's own p.275 sample, dated 2002, is this table:
        #
        #     TANK   PRODUCT LABEL          SIPHON MANIFOLDED TANKS    LINE ...
        #      2     REGULAR UNLEADED           1                          3
        #
        # Both are real; they are W17's two firmware eras, and this console
        # runs the later one.
        rows = [wiretables.TITLES[tok]["title"], "",
                wiretables.heading(tok)]
        body = ""
        for t in tanks:
            sip = c.partners("612", t)
            lin = c.partners("61D", t)
            # A tank nobody has labelled prints a blank label column, not the
            # panel's "TANK 1" -- the rule `wiretables._row` states for every
            # other setup table, and this one was the exception.
            label = c.text("602", t) or ""
            rows.append(wiretables.row(tok, [
                t, label, _partner_text(sip), _partner_text(lin)]))
            mine = sip if tok == "612" else lin
            body += (f"{t:02d}{len(mine):02d}"
                     + "".join(f"{n:02d}" for n in mine))
        if display:
            return handler._frame(code, SEP.join(rows)), "manifolded partners"
        return handler._frame(code, body), "manifolded partners"

    if tok == "75A":
        raw = (c.values.get("S75A00") or "").strip()
        if display:
            rows = ["LINE LEAK LOCKOUT SETUP", "------ ------"]
            return (handler._frame(code, SEP.join(rows + _lockout_text(raw))),
                    "lockout schedule")
        return handler._frame(code, raw), "lockout schedule"

    if tok == "52D":
        receivers = _receivers(c, dev)
        if display:
            # p.201: RCVR right against column 1, STATUS at 8
            rows = ["RECEIVER AUTODIAL ALARM STATUS", "RCVR    STATUS"]
            for r in receivers:
                rows.append(f"{r:2d}      "
                            + ("ALARM" if c.autodial_alarm.get(r) else "CLEAR"))
            return handler._frame(code, SEP.join(rows)), "autodial alarms"
        body = f"{len(receivers):02d}" + "".join(
            "1" if c.autodial_alarm.get(r) else "0" for r in receivers)
        return handler._frame(code, body), "autodial alarms"

    if tok in ("8C1", "8C2"):
        # 8C1 and 8C2 share this report; the serial a console holds is the
        # answer to both "what is set" and "what would be removed"
        numbers = ([int(dev)] if dev.isdigit() and int(dev)
                   else sorted(c.vmc_serials) or [1])
        if display:
            rows = ["VMC SERIAL NUMBERS", "VMC  SERIAL"]
            for n in numbers:
                rows.append(f"{n:<5d}{c.vmc_serial(n)}")
            return handler._frame(code, SEP.join(rows)), "VMC serial numbers"
        return (handler._frame(code, "".join(f"{n:02d}{c.vmc_serial(n)}"
                                             for n in numbers)),
                "VMC serial numbers")

    if tok == "7B4":
        rows = ["INDIVIDUAL METER OFFSET", "FUEL_P METER TANK OFFSET"]
        for fp, meter in sorted(c.meter_offsets):
            e = c.meter_offsets[(fp, meter)]
            rows.append(f"{fp:<7d}{meter:<6d}{e['tank']:<5d}"
                        f"{e['pct']:+.2f}%")
        # no offsets, no rows: see FIDELITY U5
        return handler._frame(code, SEP.join(rows)), "meter offsets"

    # 7B1, display only
    return handler._frame(code, SEP.join(map_report(c))), "meter map"
