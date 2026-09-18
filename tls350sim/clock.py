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
"""How the console writes a date and a time.

Its own module because every part of the console needs it and the engines
load before console.py has finished importing them."""
import time


def clock_date(when, year=True, sep=", "):
    """"JAN  6, 1996": the day is space padded, exactly like the hour.

    Counted across the whole reference shelf by word position rather than by
    reading order -- the text extractions collapse runs of spaces, so a grep
    says the opposite -- single-digit days are written with a space 694
    times against 61, and the geometry settles it rather than the count:
    576013-635 Rev AA p.100 sets six consecutive rows in one monospace
    sample, and `OCT 10,` starts one character to the LEFT of `OCT 9,` with
    the comma and the year in the same columns in both. The day is right
    aligned in two columns, which is a space and not a zero.

    (The 61 are real pages rather than a bad reading -- function 119's
    service history writes `JAN 09,` nineteen times, and 7B6 writes it both
    ways on one page. See FIDELITY W27.)

    `year=False` gives the day alone, "JAN  6", and `sep` is what sits
    between the day and the year for the two screens that print no space
    after the comma or no comma at all.
    """
    t = when if isinstance(when, time.struct_time) else time.localtime(when)
    day = time.strftime("%b", t).upper() + f" {t.tm_mday:2d}"
    return day + sep + time.strftime("%Y", t) if year else day


def clock_words(when, seconds=False):
    """"JAN 22, 1996  3:06 PM": how the manuals print a date and time.

    The hour is space padded rather than zero padded: every sample in the
    Operator's and Serial manuals reads " 3:06 PM", never "03:06 PM". So is
    the day -- see `clock_date`.
    """
    t = when if isinstance(when, time.struct_time) else time.localtime(when)
    hour = t.tm_hour % 12 or 12
    tail = time.strftime(":%M:%S %p" if seconds else ":%M %p", t).upper()
    return clock_date(t) + f" {hour:2d}" + tail


def clock_wide(when):
    """The TWENTY-TWO character stamp, `JAN  1, 2007   8:02 AM`.

    Three spaces after the year where `clock_words` has two. It is the date
    and the time of day with two spaces between them, and one column is the
    whole difference between the two forms -- 411, 412 and 208 all draw this
    one, and only a rendered page tells them apart. See FIDELITY D5 and S5.
    """
    return f"{clock_date(when)}  {clock_hhmm(when)}"


def clock_hhmm(when):
    """Just the time of day, the same way: " 3:06 PM"."""
    t = when if isinstance(when, time.struct_time) else time.localtime(when)
    hour = t.tm_hour % 12 or 12
    return f"{hour:2d}" + time.strftime(":%M %p", t).upper()
