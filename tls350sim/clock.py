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
"""How the console writes a date and a time.

Its own module because every part of the console needs it and the engines
load before console.py has finished importing them."""
import time


def clock_words(when, seconds=False):
    """"JAN 22, 1996  3:06 PM": how the manuals print a date and time.

    The hour is space padded rather than zero padded: every sample in the
    Operator's and Serial manuals reads " 3:06 PM", never "03:06 PM".
    """
    t = when if isinstance(when, time.struct_time) else time.localtime(when)
    hour = t.tm_hour % 12 or 12
    tail = time.strftime(":%M:%S %p" if seconds else ":%M %p", t).upper()
    return time.strftime("%b %d, %Y", t).upper() + f" {hour:2d}" + tail


def clock_hhmm(when):
    """Just the time of day, the same way: " 3:06 PM"."""
    t = when if isinstance(when, time.struct_time) else time.localtime(when)
    hour = t.tm_hour % 12 or 12
    return f"{hour:2d}" + time.strftime(":%M %p", t).upper()
