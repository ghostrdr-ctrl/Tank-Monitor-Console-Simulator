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
"""Turn what you type on the keypad into what the console stores, and back.

The console does not store what you see. A limit shown as 1,200 gallons is an
ASCII-hex IEEE float on the wire; a label is padded to its full width. Getting
this right is what makes a value programmed on the panel read back correctly
from a tool over the socket, which is the whole point of the simulator.

Validation is deliberate too: entering an out-of-range value is REFUSED here,
because the real console refuses it. A simulator that accepts anything teaches
you that a value is fine when the console would have rejected it.

One setup function is often several prompts. The console asks for the end
shape and then the end factor, but both live in one S639 value, a character
of shape followed by a float. So a field can claim a PART of its function's
data, `"part": [offset, length]`, and writing it leaves the rest of the value
alone. That is the only way to walk the panel the way the manual walks it and
still have a tool read back what the wire format says should be there.
"""
from . import packed
from . import wirelists
from .console import DEVICE_PREFIXED


def _prefixed(code):
    try:
        return int(code[1:4], 16) in DEVICE_PREFIXED
    except ValueError:
        return False


def _dev(code):
    return code[4:6]


def body_of(code, raw):
    """The data without the device prefix the console echoes back."""
    if raw is None:
        return ""
    return raw[2:] if _prefixed(code) and len(raw) > 2 else raw


def _fit(field, value, width):
    """A part must be exactly its width, padded the way its kind is read."""
    if width is None or len(value) == width:
        return value
    if len(value) > width:
        raise ValueError(f"MAX {width} CHARS")
    if field.get("kind") in ("text", "raw", None):
        return value.ljust(width, field.get("fill", " "))
    return value.rjust(width, field.get("fill", "0"))


def _encode_value(field, text, width=None):
    """What the field itself stores, before it is placed in the data."""
    kind = field.get("kind")

    if kind == "list":
        # A run of values rather than one, and on 52B and 75A the run's own
        # first character decides how long the rest of it is -- which is why
        # the check lives with the codes that parse them rather than here.
        if not wirelists.validate(field.get("code", ""), text):
            raise ValueError("VALUE OUT OF RANGE")
        return text

    if kind == "text":
        n = int(field.get("maxlen") or width or 20)
        if len(text) > n:
            raise ValueError(f"MAX {n} CHARS")
        return text.ljust(n)

    if kind == "schedule":
        return schedule_data(text, field.get("shape", "MWD"),
                             field.get("weeks", 4))

    if kind == "flag":
        t = text.upper()
        if t in ("1", "Y", "YES", "ON", "ENABLE", "ENABLED"):
            return "1"
        if t in ("0", "N", "NO", "OFF", "DISABLE", "DISABLED"):
            return "0"
        raise ValueError("ENTER 0 OR 1")

    if kind == "int":
        if not text.lstrip("-").isdigit():
            raise ValueError("NUMBERS ONLY")
        v = int(text)
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and v < lo:
            raise ValueError(f"MIN {lo}")
        if hi is not None and v > hi:
            raise ValueError(f"MAX {hi}")
        n = field.get("width") or width
        return f"{v:0{n}d}" if n else f"{v:d}"

    if kind == "float":
        try:
            v = float(text)
        except ValueError:
            raise ValueError("NUMBERS ONLY")
        lo, hi = field.get("min"), field.get("max")
        if lo is not None and v < lo:
            raise ValueError(f"MIN {lo}")
        if hi is not None and v > hi:
            raise ValueError(f"MAX {hi}")
        return packed.hexfloat(v)

    if kind == "time":
        # "To change the time press CHANGE and enter the correct time. Select
        # either AM or PM by using the arrow keys": so what arrives is a
        # twelve hour clock with a meridiem on the end of it, and what the
        # wire holds is twenty-four hour HHmm.
        half = ""
        upper = text.upper()
        for word in ("AM", "PM"):
            if word in upper:
                half = word
                break
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) != 4:
            raise ValueError("ENTER HHMM")
        hour, minute = int(digits[:2]), int(digits[2:])
        if half:
            if not 1 <= hour <= 12:
                raise ValueError("BAD TIME")
            hour = (hour % 12) + (12 if half == "PM" else 0)
        elif hour > 23:
            raise ValueError("BAD TIME")
        if minute > 59:
            raise ValueError("BAD TIME")
        return f"{hour:02d}{minute:02d}"

    if kind == "date":
        # The screen reads "DATE: XX/XX/XXXX" and the manual says to "enter
        # the correct date by first entering the month then the day then the
        # year following the format shown on the display", so MMDDYYYY is what
        # gets typed; the wire holds YYMMDD. Both are accepted.
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) == 8:
            digits = digits[6:8] + digits[0:2] + digits[2:4]
        elif len(digits) == 6 and text.count("/") == 2:
            # MM/DD/YY typed short
            digits = digits[4:6] + digits[0:2] + digits[2:4]
        if len(digits) != 6:
            raise ValueError("ENTER MMDDYYYY")
        if not 1 <= int(digits[2:4]) <= 12 or not 1 <= int(digits[4:]) <= 31:
            raise ValueError("BAD DATE")
        return digits

    if kind == "digits":
        digits = "".join(ch for ch in text if ch.isdigit())
        n = field.get("width") or width
        if n and len(digits) != n:
            raise ValueError(f"ENTER {n} DIGITS")
        allowed = field.get("allow")
        if allowed and digits[:2] not in allowed:
            raise ValueError("NOT A CHOICE")
        return digits

    if kind == "profile":
        from .console import Console as _C
        for code, name in _C.PROFILE_NAME.items():
            if text.strip().upper() == name:
                return code
        raise ValueError("NOT A CHOICE")
    if kind == "enum":
        for c in field.get("choices") or []:
            val, label = (c if isinstance(c, (list, tuple)) else (c, c))[:2]
            if text.upper() in (str(val).upper(), str(label).upper()):
                return str(val)
        raise ValueError("NOT A CHOICE")

    return text


def encode_value(field, text):
    """Just the value, with no device prefix and no part placed.

    What a serial Set carries: "Display: <SOH>S60901c.cccccc" is the field on
    its own, and the console stores the same thing it would have stored from
    the keypad. Raises ValueError the same way `encode` does.
    """
    return _encode_value(field, (text or "").strip())


# The schedule a repeating leak test runs on. 576013-635 Rev AA p.260 gives
# the encoding -- MM month 01-12, W week 1-4, D day 1=Monday..7=Sunday -- and
# 576013-623 Rev AN p.120 draws the screen it makes: "JAN WEEK 1 MON".
#
# The month names are the console's own three-letter ones, which is what it
# prints in every date stamp in every manual here (JAN, MAR, JUN, JUL ...).
# The one place a manual writes a month out in this field is a single reused
# figure on p.120, "JUNE WEEK 1 FRI"; see UNKNOWNS.md.
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def schedule_text(body, shape):
    """"JAN WEEK 1 MON" from `MMWD`, "WEEK 1 MON" from `WD`, "MON" from `D`.

    `shape` says which of month, week and day the field carries, because the
    console packs only the ones that schedule needs.
    """
    out, i = [], 0
    if "M" in shape:
        mm = body[i:i + 2]
        i += 2
        n = int(mm) if mm.isdigit() and 1 <= int(mm) <= 12 else 1
        out.append(MONTHS[n - 1])
    if "W" in shape:
        w = body[i:i + 1]
        i += 1
        out.append(f"WEEK {w if w.isdigit() and w != '0' else '1'}")
    if "D" in shape:
        dd = body[i:i + 1]
        n = int(dd) if dd.isdigit() and 1 <= int(dd) <= 7 else 1
        out.append(DAYS[n - 1])
    # three spaces between the parts, not one: 576013-635 Rev AA's display
    # format for 51B reads `START DATE    APR   WEEK 1   SUN   2:00 AM`, and
    # the tape prints `MAR   WEEK 2   SUN` on its own line.
    return "   ".join(out)


def schedule_data(text, shape, top=4):
    """The reverse of `schedule_text`: "JAN WEEK 1 MON" -> "0111".

    Digits are taken as already encoded, so a value that came off the wire
    goes back on unchanged.
    """
    want = len(shape) + ("M" in shape)          # M is two digits, W and D one
    raw = text.strip().upper()
    if raw.isdigit() and len(raw) == want:
        return raw
    words = raw.replace(",", " ").split()
    out = ""
    if "M" in shape:
        month = next((i for i, m in enumerate(MONTHS, 1)
                      if words and words[0].startswith(m)), None)
        if month is None:
            raise ValueError("NOT A MONTH")
        out += f"{month:02d}"
        words = words[1:]
    if "W" in shape:
        if words and words[0] == "WEEK":
            words = words[1:]
        # a leak test schedule runs to week 4 (576013-635 Rev AA p.260);
        # daylight saving runs to week 6, where 5 and 6 are the last ones
        # (p.177). The field says which.
        if not words or not words[0].isdigit() or not 1 <= int(words[0]) <= top:
            raise ValueError(f"WEEK 1 TO {top}")
        out += words[0]
        words = words[1:]
    if "D" in shape:
        day = next((i for i, day in enumerate(DAYS, 1)
                    if words and words[0].startswith(day)), None)
        if day is None:
            raise ValueError("NOT A DAY")
        out += str(day)
    return out


def resolve_part(field, code, raw):
    """Where this field's bytes are, when that depends on another field.

    Function code 611 packs a leak test as `DDRM` and then a schedule whose
    LENGTH is chosen by M -- `YYMMDDHHmm` on a date, `MMWDHHmm` annually,
    `WDHHmm` monthly, `DHHmm` weekly, `HHmm` daily, and nothing at all for
    automatic or CSLD (576013-635 Rev AA p.258). So the start time does not
    sit at one offset: it sits after whatever the schedule took. A field can
    say so with `part_when`, naming the selector's own part and the offset
    each of its values implies.

        "part_when": {"at": [3, 1], "map": {"1": [10, 4], "5": [4, 4]}}

    A selector value the map does not carry means the console is not holding
    this field at all, and `None` says so.
    """
    by = field.get("part_when")
    if not by:
        return field.get("part")
    body = body_of(code, raw) if raw is not None else ""
    off, ln = by["at"]
    key = body[off:off + ln].strip() or by.get("default", "")
    got = by["map"].get(key)
    return got if got else None


def encode(field, code, text, current=None):
    """The stored data string, or None if nothing was entered.

    `current` is what the function holds already, which a part-field needs so
    that programming the end factor does not wipe the end shape beside it.

    Raises ValueError with a plain message the display can show, exactly as the
    console shows one.
    """
    text = (text or "").strip()
    if not text:
        return None
    pfx = _dev(code) if _prefixed(code) else ""
    part = (resolve_part(field, code, current) if field.get("part_when")
            else field.get("part"))
    if field.get("part_when") and part is None:
        # the console is not holding this field on this schedule
        return None

    if not part:
        return pfx + _encode_value(field, text)

    off, ln = part
    value = _fit(field, _encode_value(field, text, ln), ln)
    body = body_of(code, current)
    fill = field.get("blank", "0")
    if len(body) < off + ln:
        body = body.ljust(off + ln, fill)
    return pfx + body[:off] + value + body[off + ln:]


def decode(field, code, raw):
    """What the display should show for a stored value."""
    if raw is None:
        return ""
    body = body_of(code, raw)
    part = (resolve_part(field, code, raw) if field.get("part_when")
            else field.get("part"))
    if field.get("part_when") and part is None:
        return ""
    if part:
        off, ln = part
        body = body[off:off + ln]
        if not body.strip():
            return ""
    kind = field.get("kind")
    if kind == "schedule":
        return schedule_text(body, field.get("shape", "MWD"))
    if kind == "float":
        try:
            v = packed.unhexfloat(body[-8:])
            # a thermal coefficient is 0.00070 and a full volume is 10000, so
            # two decimal places will not do for both
            return f"{v:g}"
        except Exception:
            return body.strip()
    if kind == "flag":
        # most flags read ENABLED/DISABLED; the tank test ones read ON/OFF
        off, on = field.get("words") or ("DISABLED", "ENABLED")
        return on if body.strip().endswith("1") else off
    if kind == "time":
        d = body.strip()
        if d.upper().startswith("EE"):
            return "DISABLED"
        if len(d) < 4 or not d[:4].isdigit():
            return d
        # the manual's screens read "TIME: 2:00 AM", not "TIME: 02:00"
        hh, mm = int(d[:2]), d[2:4]
        half = "AM" if hh < 12 else "PM"
        return f"{hh % 12 or 12}:{mm} {half}"
    if kind == "date":
        d = body.strip()
        if len(d) < 6:
            return d
        # "DATE: XX/XX/XXXX": stored YYMMDD, shown MM/DD/YYYY
        yy = int(d[:2])
        return f"{d[2:4]}/{d[4:6]}/{2000 + yy if yy < 70 else 1900 + yy}"
    if kind == "int":
        d = body.strip()
        # stored zero-padded to the width the wire wants, shown as a number
        return str(int(d)) if d.lstrip("-").isdigit() else d
    if kind == "profile":
        from .console import Console as _C
        return _C.PROFILE_NAME.get(body.strip()[:2], body.strip())
    if kind == "enum":
        for c in field.get("choices") or []:
            val, label = (c if isinstance(c, (list, tuple)) else (c, c))[:2]
            if body.strip() == str(val):
                return str(label)
        return body.strip()
    return body.strip()


def choices_of(field, console=None):
    """What CHANGE walks on this screen.

    A choice can name the feature that brought it, CSLD is a leak test
    method on software that has CSLD in it and nothing at all on software
    that does not, so a console walks past it rather than offering a method
    it cannot run.
    """
    out = []
    for c in field.get("choices") or []:
        seq = c if isinstance(c, (list, tuple)) else (c, c)
        val, label = seq[:2]
        feature = seq[2] if len(seq) > 2 else None
        if feature and console is not None and not console.supports(feature):
            continue
        out.append((str(val), str(label)))
    return out
