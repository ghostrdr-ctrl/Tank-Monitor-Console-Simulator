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
"""Fixed-width value masks, which is how a console draws a number.

A real TLS-350 does not print `OVERFILL LIMIT: 0`. It prints
`OVERFILL LIMIT: 000%`, and after programming, `OVERFILL LIMIT: 090%`. Every
numeric setup field on the console is a fixed-width field: the manual draws
each one's mask in its body chapter, either as zeros or as X's, and the
console keeps the width whatever the value is.

    576013-623 Rev AN p.107   OVERFILL LIMIT: 000%   ->  OVERFILL LIMIT: XX%
    576013-623 Rev AN p.97    FULL VOL: 000000       ->  FULL VOL: XXXXXX
    576013-623 Rev AN p.94    THERMAL COEFF: 0.00000 ->  THERMAL COEFF: 0.00070
    576013-623 Rev AN p.110   TANK TILT: +000.00

So each field carries the mask the manual draws for it and the value is
rendered into it. A field with no mask is left alone: text, enums, times and
dates are not this kind of field, and a numeric field whose mask no manual
draws is better left bare than given an invented width.

The mask is written the way the manual writes it:

    000000    six digits, zero padded
    000.00    three digits, a point, two decimals
    0.00000   one digit and five decimals
    000%      three digits and a literal per-cent sign
    +000.00   signed: the sign is always drawn, "+" or "-"
    000S      three digits and a literal S, for a delay in seconds

Anything that is not `0`, `.` or a leading `+`/`-` is a literal suffix and is
copied through.
"""

import re

_MASK = re.compile(r"^(?P<sign>[+\u00b1])?(?P<int>0+)(?:\.(?P<dec>0+))?"
                   r"(?P<suffix>.*)$")


def parse(mask):
    """-> (signed, integer digits, decimal digits, suffix), or None."""
    if not mask:
        return None
    m = _MASK.match(mask)
    if not m:
        return None
    return (bool(m.group("sign")), len(m.group("int")),
            len(m.group("dec") or ""), m.group("suffix"))


def apply(mask, value):
    """Draw `value` in `mask`. A value that will not parse is passed through.

    The console is showing a stored number, so a string that is not a number
    is not something to force into a numeric mask -- it is a label, a word, or
    a value some other part of the console owns, and it is returned unchanged.
    """
    shape = parse(mask)
    if shape is None:
        return value
    signed, width, places, suffix = shape
    text = "" if value is None else str(value).strip()
    # a value already carrying this mask's suffix is measured without it
    if suffix and text.endswith(suffix):
        text = text[:-len(suffix)].strip()
    if text == "":
        text = "0"
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return value
    sign = ""
    if signed:
        sign = "-" if number < 0 else "+"
        number = abs(number)
    elif number < 0:
        sign = "-"
        number = abs(number)
    if places:
        body = f"{number:.{places}f}"
        whole, _dot, frac = body.partition(".")
        body = f"{whole.rjust(width, '0')}.{frac}"
    else:
        body = f"{int(round(number))}".rjust(width, "0")
    return f"{sign}{body}{suffix}"


# ---------------------------------------------------------------------------
# And a whole number is TRUNCATED, not rounded.
#
# 576013-610 Rev AC says so twice, on two pages, about two different
# quantities:
#
#   p.4-2  MASS = 15290 LBS for 2549 GALS at 5.9987 LBS/GAL, and
#          2549 * 5.9987 is 15290.686
#   p.1-3  WATER VOL = 28 GALS for 1.37 inches of water in a 10,000 gallon,
#          96 inch tank, and the chart reads 28.82 there
#
# Both would be 15291 and 29 rounded. `"%.0f"` rounds, and rounds to EVEN at
# a half besides, so it was wrong in two directions at once. There is no
# sample on this shelf that rounding fits and truncation does not.
# See FIDELITY Y11.
def whole(value, width=0):
    """A whole-number quantity as a console prints it: the fraction dropped.

    Toward zero rather than downward, so a negative change of -0.4 prints 0
    and not -1: the console is dropping the digits it has no column for, not
    taking a floor.
    """
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        n = 0
    return f"{n:{width}d}" if width else str(n)
