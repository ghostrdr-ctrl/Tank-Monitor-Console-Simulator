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

SEP = "\r\n"

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
    """The DIAL TYPE and START TIME columns for one receiver."""
    if not raw:
        return "", ""
    m, rest = raw[0], raw[1:]
    name = DIAL_METHOD.get(m, "")
    if m == "1":
        return name, f"{rest[2:4]}/{rest[4:6]}/{rest[0:2]} {_clock(rest[6:])}"
    if m == "2":
        return name, (f"MONTH {int(rest[0:2])} WEEK {rest[2]} "
                      f"{WEEKDAY.get(rest[3], '')} {_clock(rest[4:])}")
    if m == "3":
        return name, (f"WEEK {rest[0]} {WEEKDAY.get(rest[1], '')} "
                      f"{_clock(rest[2:])}")
    if m == "4":
        return name, f"{WEEKDAY.get(rest[0], '')} {_clock(rest[1:])}"
    return name, _clock(rest)


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
        return ["NO LOCKOUT SCHEDULE"]
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


def _map_ok(body):
    """B SS FP MM TT, with TT allowed to be -1."""
    if len(body) < 8:
        return False
    bus, slot, fp, meter, tank = (body[0], body[1:3], body[3:5],
                                  body[5:7], body[7:])
    if bus not in BUS_SLOTS or not slot.isdigit():
        return False
    if int(slot) not in BUS_SLOTS[bus]:
        return False
    if not (fp.isdigit() and meter.isdigit()):
        return False
    return tank == "-1" or (tank.isdigit() and len(tank) == 2)


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
    "75A": _lockout_ok,
    "7B1": _map_ok,
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
    "75A": "022450445",           # daily, 10:45 pm to 4:45 am
    "7B1": "3030010-1",           # comm bus, slot 3, position 00, meter 10
    "7B4": "010101+0.00",         # position 1, meter 1, tank 1, no offset
    "8C1": "123456",
    "8C2": "123456",
}


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
        "52D", "8C1", "8C2"}

# "Computer format is not supported for this command", said of the CODE and
# not of one direction of it. 680 is the report that says the same.
NO_COMPUTER_FORMAT = {"7B1", "7B4"}

# what card each one needs before it means anything
NEEDS = {"52A": "modem", "52B": "modem", "52C": "modem", "52D": "modem",
         "7B1": "bir"}





def _receivers(console, dev):
    """"RR - Receiver Number (Decimal, 00=all)"."""
    if dev.isdigit() and int(dev):
        return [int(dev)]
    return list(console.receivers())


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

    if tok in NO_COMPUTER_FORMAT and code[0].islower():
        # the manual's own words: "Computer format is not supported for this
        # command", and it is a property of the code rather than of the Set
        return handler._nine(code), f"{tok} has no computer format"

    if setting:
        return _set(handler, tok, dev, code, body)
    return _inquire(handler, tok, dev, code)


def _set(handler, tok, dev, code, body):
    c = handler.c
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
        c.values[f"S{tok}{tank:02d}"] = "".join(f"{n:02d}" for n in partners)
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
        c.meter_offsets[meter] = {"fp": fp, "tank": tank, "pct": pct}
        c.save()
        return handler._frame(code), f"meter {meter} offset {pct:+.2f}%"

    # 7B1
    if not _map_ok(body):
        return handler._nine(code), "REJECTED: wants B SS FP MM TT"
    bus, slot, fp, meter, tank = (body[0], int(body[1:3]), int(body[3:5]),
                                  int(body[5:7]), body[7:])
    if tank == "00":
        # "00=Unmap present tank"
        c.meter_map.pop(meter, None)
        c.meters.pop(meter, None)
        c.save()
        return handler._frame(code), f"meter {meter} unmapped"
    number = -1 if tank == "-1" else int(tank)
    if number > 0 and number > c.tank_count():
        return handler._nine(code), f"REJECTED: no tank {number}"
    c.meter_map[meter] = {"bus": bus, "slot": slot, "fp": fp, "tank": number}
    c.meters[meter] = number
    c.save()
    return handler._frame(code), f"meter {meter} on tank {number}"


def _inquire(handler, tok, dev, code):
    c = handler.c
    display = code[0].isupper()

    if tok == "52A":
        rows, body = ["RECEIVER REPORT LIST"], ""
        for r in _receivers(c, dev):
            picks = c.receiver_reports.get(r, {})
            on = sorted(k for k, v in picks.items() if v == "01")
            rows.append(f"RCVR {r}: {c.receiver_label(r)}")
            rows += [f"     {REPORTS[k]}" for k in on] or ["     - NONE -"]
            body += f"{r:02d}{len(on):02d}" + "".join(k + "01" for k in on)
        if display:
            return handler._frame(code, SEP.join(rows)), "report list"
        return handler._frame(code, body), "report list"

    if tok == "52B":
        rows = ["RECEIVER AUTO DIAL TYPE & START TIME",
                "RCVR LOCATION LABEL   DIAL TYPE START TIME"]
        body = ""
        for r in _receivers(c, dev):
            raw = c.receiver_dial.get(r, "")
            kind, when = _dial_text(raw)
            rows.append(f"{r:5d} {c.receiver_label(r):<21s}"
                        f"{kind:<10s}{when}")
            body += f"{r:02d}" + raw
        if display:
            return handler._frame(code, SEP.join(rows)), "auto dial setup"
        return handler._frame(code, body), "auto dial setup"

    if tok == "52C":
        rows, body = ["RECEIVER SETUP REPORT"], ""
        for r in _receivers(c, dev):
            rows.append(f"D {r}: {c.receiver_label(r)}")
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
        tanks = ([int(dev)] if dev.isdigit() and int(dev)
                 else list(range(1, c.tank_count() + 1)))
        rows = ["TANK MANIFOLDED PARTNERS",
                "TANK PRODUCT LABEL               SIPHON MANIFOLDED TANKS  "
                "LINE MANIFOLDED TANKS"]
        body = ""
        for t in tanks:
            sip = c.partners("612", t)
            lin = c.partners("61D", t)
            label = c.text("602", t) or f"TANK {t}"
            rows.append(f"{t:<10d} {label:<28s} "
                        f"{' '.join(str(n) for n in sip) or '-':<24s}"
                        f"{' '.join(str(n) for n in lin) or '-'}")
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
            rows = ["RECEIVER AUTODIAL ALARM STATUS", "RCVR  STATUS"]
            for r in receivers:
                rows.append(f"{r:<6d}"
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
        for meter in sorted(c.meter_offsets):
            e = c.meter_offsets[meter]
            rows.append(f"{e['fp']:<7d}{meter:<6d}{e['tank']:<5d}"
                        f"{e['pct']:+.2f}%")
        if len(rows) == 2:
            rows.append("NO INDIVIDUAL OFFSETS")
        return handler._frame(code, SEP.join(rows)), "meter offsets"

    # 7B1, display only
    rows = ["FUELING POSITION - METER - TANK MAP",
            "BUS SLOT FUEL_P METER TANK", "-" * 31]
    for meter in sorted(c.meter_map):
        e = c.meter_map[meter]
        rows.append(f"{e['bus']:<10s}{e['slot']:<3d}{e['fp']:<8d}"
                    f"{meter:<10d}{e['tank']}")
    if len(rows) == 3:
        rows.append("NO METERS MAPPED")
    return handler._frame(code, SEP.join(rows)), "meter map"
