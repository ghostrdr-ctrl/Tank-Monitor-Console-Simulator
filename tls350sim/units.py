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
"""The system units, as the bench TLS-350 keeps and converts them.

576013-623 Rev AN p.5-2 defines the three: "U.S. units (gallons, gal/hour,
inches, F), metric units (litres, litres/hour, millimeters, C), or imperial
gallons (imperial gallons, imp. gal/hour, inches, F)". What it does not say
is where the conversion happens, and the bench TLS-350 (326.01) settled it on
2026-09-18 (`cap_swept`, `cap_metric`, `cap_imperial`: every code in both
formats in each system, the same settings throughout):

* **The console stores U.S. units, always, and converts on the way in and
  out.** Tank 1's 200000 gallons read 757000 LITERS in metric and 166540 IMP
  GAL in imperial -- in both formats: `i60401` answered 757000.0 packed --
  and back in U.S. every value read its old bits. A Set in metric is in
  litres: `S621013785` stored 1000 gallons, `s62101` 4000.0 read 1057
  gallons. A range is held in U.S. after converting: -45 C was taken for
  50E and -46 C refused, 335 m for 789 and 336 m refused.
* **The factors are the firmware's round ones**: 3.785 litres and 0.8327
  imperial gallons to the gallon, 25.4 mm to the inch, 0.3048 m to the foot,
  a psi DIVIDED by 0.1451 for kPa, and 1.8 for a coefficient per degree. The
  packed form works them in double precision and rounds once to single --
  every packed value in the two captures, bit for bit -- and the display
  with single-precision constants (`out`).
* **Imperial converts only volumes and rates.** Heights stay in inches,
  temperatures in F, pressures in psi, lengths in feet; a rate keeps the
  word GPH, a volume says IMP GAL.
* **A display volume or length is cut, not rounded** -- 123456 gallons is
  467280 litres, 501 feet 152 metres -- and a height in millimetres has one
  place where inches have two. The packed form carries the exact float.
"""
import re
import struct

US, METRIC, IMPERIAL = "1", "2", "3"

#: {quantity: {system: factor}} -- value in the system = U.S. value x factor,
#: worked in double precision and rounded once to single: every packed value
#: in `cap_metric` and `cap_imperial` is that, bit for bit. A pressure is
#: DIVIDED, by 0.1451 -- 70 psi packs as 482.4259 kPa, where x 6.8918 is two
#: bits off (`PER_PSI`).
FACTOR = {
    "volume": {METRIC: 3.785, IMPERIAL: 0.8327},
    "rate": {METRIC: 3.785, IMPERIAL: 0.8327},
    "height": {METRIC: 25.4},
    "length": {METRIC: 0.3048},
    "per_degree": {METRIC: 1.8},
}
PER_PSI = 0.1451
#: the constants the packed form holds in single precision too: litres per
#: gallon. 635's default of 4 litres, stored as 1.0568031 gallons, packs
#: back as 4.0000005 -- which only a single 3.785 gives -- where 0.8327 and
#: 0.1451 fit only as doubles.
SINGLE = {("volume", METRIC), ("rate", METRIC)}

#: a code whose PACKED form is converted as another quantity: in metric the
#: bench packs 775's test leak rate with the pressure factor -- 0.09 GPH as
#: 0.6203, which is 0.09 / 0.1451 -- while its display reads 0.34 LPH
PACKED_AS = {"775": "pressure"}

#: what each setup code measures, where the bench converted it
QUANTITY = {}
for _tok in ("604", "605", "606", "60A", "621", "622", "623", "625", "626",
             "628", "629", "62A", "634", "635", "636", "63C", "7C3"):
    QUANTITY[_tok] = "volume"
for _tok in ("607", "608", "60C", "60F", "624", "627"):
    QUANTITY[_tok] = "height"
for _tok in ("775", "784"):
    QUANTITY[_tok] = "rate"
for _tok in ("776", "77D", "78F"):
    QUANTITY[_tok] = "pressure"
for _tok in ("789", "77F"):
    QUANTITY[_tok] = "length"
for _tok in ("609", "77B"):
    QUANTITY[_tok] = "per_degree"
QUANTITY["50E"] = "temperature"

#: the places a display value is drawn to where the system changes them
PLACES = {("height", METRIC): 1}
#: quantities a display cuts to a whole number
WHOLE = {"volume", "length"}

#: the words, as the bench prints them
WORDS = {
    METRIC: [("GALLONS", "LITERS"), ("INCHES", "MM"), ("GAL/HR", "LIT/HR"),
             ("GPH", "LPH"), ("PSI", "KPA"), ("FEET", "METERS"),
             ("DEG F", "DEG C")],
    IMPERIAL: [("GALLONS", "IMP GAL")],
}


def f32(x):
    """A number as the console holds it: single precision."""
    return struct.unpack(">f", struct.pack(">f", x))[0]


def system(console):
    """The console's system units: "1" U.S., "2" metric, "3" imperial."""
    held = (console.values.get("S51700") or "").strip()
    return held[:1] if held[:1] in (US, METRIC, IMPERIAL) else US


def out(quantity, value, units, display=False):
    """A U.S. value in `units`.

    `display` is the glass's and a display reply's arithmetic, which is not
    the packed form's: the factor is a single-precision constant there, and
    `3.00 GPH` prints `11.36 LPH` because 3 x 3.7850000858 is past 11.355,
    where the packed form's double 3.785 gives 11.3549995 and would print
    11.35."""
    if units == US or value is None:
        return value
    k32 = f32 if display or (quantity, units) in SINGLE else float
    if units == METRIC and quantity == "temperature":
        return f32((value - 32.0) / k32(1.8))
    if units == METRIC and quantity == "pressure":
        return f32(value / k32(PER_PSI))
    k = FACTOR.get(quantity, {}).get(units)
    return value if k is None else f32(value * k32(k))


def into(quantity, value, units):
    """A value entered in `units`, as the U.S. one the console stores."""
    if units == US or value is None:
        return value
    if units == METRIC and quantity == "temperature":
        return f32(value * 1.8 + 32.0)
    if units == METRIC and quantity == "pressure":
        return f32(value * PER_PSI)
    k = FACTOR.get(quantity, {}).get(units)
    return value if k is None else f32(value / k)


def packed(tok, hexdata, units, unhex, hexf):
    """A packed record's floats in `units`, eight hex digits at a time: one
    for most codes, four for a four point chart. `unhex` and `hexf` are
    `packed.unhexfloat` and `packed.hexfloat`."""
    quantity = PACKED_AS.get(tok) or QUANTITY.get(tok)
    if units == US or not quantity or not hexdata or len(hexdata) % 8:
        return hexdata
    try:
        return "".join(hexf(out(quantity, unhex(hexdata[i:i + 8]), units))
                       for i in range(0, len(hexdata), 8))
    except (ValueError, TypeError):
        return hexdata


def words(text, units):
    """U.S. unit words in `text` put into `units`."""
    for us, other in WORDS.get(units, ()):
        text = re.sub(r"(?<![A-Z/])" + re.escape(us) + r"(?![A-Z])", other, text)
    return text


_NUMBER = re.compile(r"^(\s*)(-?\d+(?:\.\d+)?)(.*)$", re.S)


def shown(quantity, text, units, exact=None):
    """A display value -- `501 FEET`, `3.00 GPH`, `200000` -- in `units`,
    to the places it had, or the system's own, cut where the bench cuts.

    `exact` is the stored float where the caller has it: converting the
    display's own digits loses what they rounded off, and 634's default of
    0.79260248 gallons prints 3 litres where 0.792602 prints 2."""
    if text in (None, ""):
        return text
    if units == US:
        # a volume or a length is whole on the display in any system: 625's
        # 99.9 gallons prints 99
        m = _NUMBER.match(str(text))
        if quantity in WHOLE and m and "." in m.group(2):
            return m.group(1) + str(int(float(m.group(2)))) + m.group(3)
        return text
    m = _NUMBER.match(str(text))
    if not m:
        return words(str(text), units)
    lead, number, rest = m.groups()
    converted = out(quantity, float(number) if exact is None else exact, units,
                    display=True)
    if converted is None:
        return text
    places = len(number.split(".")[1]) if "." in number else 0
    places = PLACES.get((quantity, units), places)
    if quantity in WHOLE or places == 0 and quantity != "temperature":
        body = str(int(converted))
    else:
        body = f"{converted:.{places}f}"
    word = words(rest, units)
    if len(word) > len(rest) and rest != rest.rstrip():
        # a unit padded to its width keeps the width: `501 FEET  ` is
        # `152 METERS`, not two columns wider
        word = word.rstrip().ljust(len(rest))
    return lead + body + word


def rate_words(text, units):
    """A title or heading that names a test rate -- `0.10 TEST SCHEDULE`,
    `0.20 GPH TEST`, `0.20 GPH LINE TEST AUTO-CONFIRM` -- with the rate and
    its word in `units`: `0.38 TEST SCHEDULE`, `0.76 LPH TEST`."""
    if units == US:
        return text

    def one(m):
        return f"{out('rate', float(m.group(1)), units, display=True):.2f}"
    text = re.sub(r"(?<![\d.])(0\.[12]0)(?= (?:GPH|GAL/HR|TEST|LINE))", one, text)
    return words(text, units)


#: codes whose display reply names a test rate in its words
RATE_TEXT = {"554", "555", "611", "680", "77E", "783", "78C"}
