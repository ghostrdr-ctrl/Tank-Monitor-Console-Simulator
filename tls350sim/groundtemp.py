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
"""The ground temperature inputs of the Probe/Thermistor Interface Module,
as the bench TLS-350 reads them.

Measured on 2026-09-19 with resistors wired on the card's thermistor inputs
in place of a thermistor, each one metered, and `IB2100` / `iB2100` read
after it settled (`transcripts/thermistor*.log`, bench-2026-09-18). What the
console prints under VALUE is the resistance, in ohms, as its A/D reads it
through the two reference channels -- close to what the meter said across
the middle of the range, and off by a few percent at either end:

    metered      VALUE      read / metered
        469        366          0.780
        991        992          1.001
      4,670      4,703          1.007
      9,710      9,577          0.986
      9,995      9,826          0.983
     21,730     21,872          1.007
     99,500    102,592          1.031
    473,000    492,996          1.042
  1,000,000  1,040,133          1.040

Nothing between 470 and 990 ohms was tried, so where the low end falls away
is not known; the ratio is interpolated in log ohms between the points, and
held at the end figure past either end. An input with nothing on it reads
1,000,000,000 exactly. A 473k and a 1M resistor still read as numbers, so
the ">200k = open" of the troubleshooting pages is a judgement about a
thermistor and not a limit of the input; where the input itself gives up
between 1M and an open circuit is not measured.

The rest of the row is the card's, not the input's: HIGH REF 966 (967 now
and then) and LOW REF 199 (200 now and then), whatever is wired; and SAMPLE
COUNTER, which holds at 50 and restarts when its input changes, climbing
15 -> 20 and 44 -> 50 over two seconds.
"""

import math
import time

from . import readings

OPEN = 1000000000
HIGH_REF, LOW_REF = 966, 199
SAMPLES = 50
PER_SECOND = 2.5

#          metered ohms, console VALUE
MEASURED = [(469.0, 366.0),
            (991.0, 992.0),
            (4670.0, 4703.0),
            (9710.0, 9577.0),
            (9995.0, 9826.0),
            (21730.0, 21872.0),
            (99500.0, 102592.0),
            (473000.0, 492996.0),
            (1000000.0, 1040133.0)]


def read_as(ohms):
    """What the console prints for a resistance of `ohms` on an input."""
    if ohms is None:
        return float(OPEN)
    ohms = max(1.0, float(ohms))
    points = [(math.log(m), v / m) for m, v in MEASURED]
    x = math.log(ohms)
    if x <= points[0][0]:
        return ohms * points[0][1]
    if x >= points[-1][0]:
        return ohms * points[-1][1]
    for (x0, r0), (x1, r1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return ohms * (r0 + (r1 - r0) * (x - x0) / (x1 - x0))
    return ohms


def counter(console, since):
    """SAMPLE COUNTER: the samples taken since the input last changed."""
    if since is None:
        return SAMPLES
    elapsed = time.mktime(console.now()) - since
    return max(1, min(SAMPLES, int(elapsed * PER_SECOND)))


def row(console, number, ohms, since):
    """(counter, high, low, last, average) for one input.

    The average is what the display prints; the last sample carries one A/D
    conversion's noise around it, about two parts in ten thousand on the
    bench (102,569 against 102,592). An open input reads the same on both.
    """
    average = read_as(ohms)
    if ohms is None:
        last = average
    else:
        last = average * (1.0 + readings.wander(
            console, -0.0003, 0.0003, "sample", "groundtemp", number,
            swing=1.0, period=30.0))
    return (counter(console, since), HIGH_REF, LOW_REF, last, average)
