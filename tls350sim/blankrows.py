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
"""The rows a tank or line setup report prints for a position nobody set.

The bench TLS-350, cold started on 2026-09-18, answers every one of these
with a row per position whether or not the position is switched on -- a
blank label, a zero, a default -- where this console had nothing stored and
so drew nothing. `tests/test_bench_console.py` holds them to it; every column
below is measured off `tests/console_capture/bench-2026-09-18/cap_coldstart`.

A tank row opens the same way in all of them: the tank number ending at
column 1, the label from 7 to 26, blank to column 32, so the value starts at
33. `ROWS` is used for a position with nothing stored; `TABLES` are codes
this console has no field for at all, drawn whole.
"""
from . import packed, units


def max_volume(console, n):
    """7C3 for tank n: whole gallons, 132 out of a cold start."""
    raw = (console.values.get(f"S7C3{n:02d}") or "").strip()
    return int(raw) if raw.isdigit() else 132


def _tank(n, label):
    """The first 33 columns of a tank's row."""
    return f"{n:2d}     {label:<20.20s}      "


def _ends(prefix, *pairs):
    """`prefix`, then each text placed to END at its column."""
    line = prefix
    for end, text in pairs:
        start = end - len(text) + 1
        line += " " * max(0, start - len(line)) + text
    return line


# the four and twenty point charts put a value's last digit at 40, 48, 56, 64
_CHART = (40, 48, 56, 64)


# rows for one position with nothing stored, keyed by code
ROWS = {
    "602": lambda c, n: [_tank(n, c.text("602", n) or "")],
    # the product code defaults to the tank's own number
    "603": lambda c, n: [_tank(n, c.text("602", n) or "") + str(n)],
    "605": lambda c, n: [_ends(_tank(n, c.text("602", n) or ""),
                               *[(e, "0") for e in _CHART])],
    # twenty points is five rows of four, the tank on the first
    "606": lambda c, n: [_ends(_tank(n, c.text("602", n) or "")
                               if row == 0 else " " * 33,
                               *[(e, "0") for e in _CHART])
                         for row in range(5)],
    "63C": lambda c, n: [_ends(_tank(n, c.text("602", n) or ""), (38, "0"))],
    "782": lambda c, n: [f"{n:6d}  {c.text('782', n) or '':<20.20s}   "],
    # a date nobody entered: the month has no name, the day and year are 0
    "62B": lambda c, n: [_tank(n, c.text("602", n) or "") + "???  0,    0"],
}


def _body(console, tok, n, width):
    """The last `width` characters of a stored value, device prefix or not."""
    raw = (console.values.get(f"S{tok}{n:02d}") or "").strip()
    return raw[-width:] if len(raw) >= width else ""


def fail_alarms(console, n):
    """62D for tank n: `gpa`, gross, periodic and annual, 1 enabled.

    Drawn from what is stored and not only for a tank nobody set: with a
    value held, the report fell through to a layout of this console's own
    (`ANNUAL TEST FAIL ALARM: DISABLED`), and the bench's snapshot printed
    its packed record raw. ENABLED is 576013-623 Rev AN's own word for it
    on the setup screens; the bench has printed DISABLED only."""
    flags = _body(console, "62D", n, 3) or "000"
    words = ["ALARM ENABLED" if f == "1" else "ALARM DISABLED" for f in flags]
    return [_tank(n, console.text("602", n) or "")
            + f"GROSS TEST FAIL        {words[0]}",
            " " * 33 + f"PERIODIC TEST FAIL     {words[1]}",
            " " * 33 + f"ANNUAL TEST FAIL       {words[2]}",
            ""]


# 639's S: 576013-635 Rev AA p.296. NONE is the bench's, OTHER and its END
# VALUE line the page's sample, FLAT and HEMISPHER the setup screen's words.
END_SHAPE = {"0": "NONE", "1": "FLAT", "2": "HEMISPHER", "3": "OTHER"}


def end_shape(console, n):
    """639 for tank n: the shape, and the factor when it is OTHER."""
    body = _body(console, "639", n, 9)
    shape = END_SHAPE.get(body[:1], "NONE")
    rows = [_tank(n, console.text("602", n) or ""), f"END FACTOR: {shape}"]
    if shape == "OTHER":
        try:
            rows.append(f"END VALUE: {packed.unhexfloat(body[1:]):.1f}")
        except ValueError:
            pass
    return rows + ["", ""]


# whole tables for codes with no field here: (title, heading, rows per tank)
TABLES = {
    "62D": ("TANK LEAK TEST FAIL ALARMS",
            "TANK   PRODUCT LABEL          ", fail_alarms),
    "639": ("ACCU CHART END SHAPE FACTOR",
            "TANK   PRODUCT LABEL          ", end_shape),
    # 7C3 is in no revision of 576013-635 on this shelf; the bench console
    # answers it, and 132 gallons is what it holds out of a cold start. It is
    # a setting (`Handler._set_max_volume`).
    "7C3": ("TANK MAXIMUM VOLUME LIMIT",
            "TANK   PRODUCT LABEL             GALLONS",
            # in the system's units: 499 LITERS in metric (`cap_metric`)
            lambda c, n: [_ends(_tank(n, c.text("602", n) or ""),
                                (38, units.shown("volume",
                                                 str(max_volume(c, n)),
                                                 units.system(c))))]),
}


def table(console, tok, devices):
    """The whole Display body for a TABLES code, or None."""
    spec = TABLES.get(tok)
    if not spec or not devices:
        return None
    title, heading, rows = spec
    out = [title, "", heading]
    for n in devices:
        out += rows(console, n)
    while out and not out[-1].strip():
        out.pop()
    return out
