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
"""What the hardware reads, so a diagnostic screen has a number on it.

A diagnostic screen in a manual is drawn with X's, because the manual cannot
know what your probe says. A simulator that draws the X's has not simulated
anything: `SERIAL NUMBER XXXXXX` teaches nothing, and a technician looking at
`LIQUID DIAGNOSTIC / CNTR = X VALUE = XXXXXX` cannot learn the one thing that
screen is for, which is that the number in it tells you what the sensor is
doing.

So every X in `diagdata.json` is filled in here, and the rule is the same one
the rest of this simulator follows: derive it where the manuals give the
derivation, and where they do not, generate something a real device would
plausibly read and say so.

WHAT IS DERIVED

The sensor resistances are the strongest case, because the Troubleshooting
Guide prints the bands. Figure 6-17 for the liquid module:

    Single Float Sensor
    Normal = 55000 - 135000; Fuel = 0 - 50000; Open = >150000

    Discriminating Dispenser Pan & Containment Sump Sensors - Dual Float
    Discriminating
    Normal = 113000 - 247000; Fuel (3 ranges) = 43000 - 49000, or
    76000 - 107000, or 337000 - 570000; Open = > 612000; Short = 0 - 28000;
    High Liquid = 29000 - 41000; Liquid Warning = 52000 - 71000

and Figures 6-18, 6-20 and 6-21 do the same for the vapor, 2-wire and 3-wire
modules. So `BANDS` below is those figures, and the number a diagnostic screen
shows is a reading inside the band for the state the sensor is actually in.
Change the sensor to FUEL on the bench and the diagnostic value drops into the
fuel band, which is exactly the relationship the screen exists to show.

The probe and tank numbers are derived from the tank the panel is pointed at:
a probe's length is the tank's diameter plus its riser, the gradient follows
the product, the fuel and water heights are the ones the inventory screen is
already showing.

WHAT IS GENERATED, AND HOW

Everything else is a device characteristic nobody can derive: a serial number,
a board temperature, a pump's shut-off pressure. Two rules:

  * It is STABLE per device. `fixed()` hashes whatever identifies the thing
    being read, so probe 1 has the same serial number every time you look, and
    a different one from probe 2. Nothing here uses `random`, because a value
    that changes when you glance away is not a reading.

  * It DRIFTS inside its tolerance. `wander()` moves the value slowly on the
    console's own clock. Two submersible pumps do not sit at the same
    pressure, and neither does one pump on two runs, which is the whole
    reason a technician reads the number twice.
"""
import hashlib
import math
import time

TAU = 2.0 * math.pi


def _unit(*key):
    """A stable 0..1 from whatever identifies the thing being read."""
    text = "|".join(str(part) for part in key)
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def fixed(low, high, *key):
    """One device's own value: stable, and its own rather than its neighbour's."""
    return low + (high - low) * _unit(*key)


def integer(low, high, *key):
    return int(round(fixed(low, high, *key)))


def digits(width, *key):
    """A serial number: `width` digits, the same ones every time."""
    return f"{int(_unit(*key) * (10 ** width)):0{width}d}"


def wander(console, low, high, *key, swing=0.06, period=1800.0, at=None):
    """A reading inside its tolerance, moving the way a real one does.

    `swing` is the fraction of the band it wanders over and `period` how long
    a full excursion takes on the CONSOLE's clock, so running the bench fast
    makes the numbers move fast too.

    `at` asks what the reading WAS at a given console time rather than what
    it is now. A buffer of past samples has to be filled with the values that
    moment would have given -- CSLD's 30-second probe table is a rolling hour
    of them and the bench's clock can cross that hour in one tick -- and
    since this is a pure function of the time and the key, the answer is the
    same whether it is asked then or later.
    """
    base = fixed(low, high, *key)
    span = abs(high - low) * swing
    if at is not None:
        when = float(at)
    else:
        when = time.mktime(console.now()) if console is not None else 0.0
    phase = _unit("phase", *key) * TAU
    slow = math.sin(when / max(period, 1.0) * TAU + phase)
    fast = math.sin(when / max(period / 4.7, 1.0) * TAU + phase * 1.7)
    value = base + span * (0.7 * slow + 0.3 * fast)
    return min(max(value, min(low, high)), max(low, high))


# ---------------------------------------------------------------------------
# Sensor resistance bands, Troubleshooting Guide Figures 6-17, 6-18, 6-20,
# 6-21. Keyed by module, then by the sensor type the console is programmed
# with, then by the state the sensor is in. An open-ended band ("Open =
# >150000") is given a top a real meter would still read.
# ---------------------------------------------------------------------------
_SINGLE_FLOAT = {"normal": (55000, 135000), "fuel": (0, 50000),
                 "out": (150000, 400000)}
_DUAL_HYDROSTATIC = {"normal": (80000, 150000), "out": (150000, 400000),
                     "high": (9000, 80000), "low": (0, 9000)}
_DUAL_DISCRIM = {"normal": (113000, 247000), "fuel": (76000, 107000),
                 "out": (612000, 900000), "short": (0, 28000),
                 "high": (29000, 41000), "warn": (52000, 71000)}

BANDS = {
    "liquid": {
        "1": _SINGLE_FLOAT,          # TRI-STATE (single float)
        "2": _SINGLE_FLOAT,          # NORMALLY CLOSED
        "3": _DUAL_HYDROSTATIC,      # DUAL FLOAT HYDROSTATIC
        "4": _DUAL_DISCRIM,          # DUAL FLOAT DISCRIM
        "5": _DUAL_DISCRIM,          # DUAL FLOAT HIGH VAPOR
        "6": _SINGLE_FLOAT,          # INTERCEPTOR
        "7": _DUAL_DISCRIM,          # DW SUMP 2-1
    },
    # "Value 1 - Liquid Sensor: Normal = 52500 - 380000; Open = > 400000;
    # Short = 0 - 200; Water = 200 - 50000"
    "vapor": {"": {"normal": (52500, 380000), "out": (400000, 700000),
                   "short": (0, 200), "water": (200, 50000),
                   "fuel": (52500, 380000)}},
    # "Value 1 - Hydrocarbon Sensor: Normal = 60000 - 280000; Fuel = >800000;
    # Short = 0 - 60000" over "Value 2 - Water Sensor: Normal = 80000 -
    # 150000; Open = >150000; Short = 2000 - 80000; Water Out = 0 - 2000"
    "gw": {"": {"normal": (60000, 280000), "fuel": (800000, 1200000),
                "short": (0, 60000), "out": (60000, 280000),
                "waterout": (60000, 280000)}},
    # "Microsensor: Normal = 2500 - 6000; Fuel = 8000 - 12000;
    # Open = 0 - 2000; Short = >14000", and the discriminating one adds
    # "Water = 13000 - 17000"
    "2wire": {"1": {"normal": (2500, 6000), "fuel": (8000, 12000),
                    "out": (0, 2000), "short": (14000, 20000)},
              "2": {"normal": (2500, 6000), "fuel": (8000, 12000),
                    "out": (0, 2000), "short": (18000, 24000),
                    "water": (13000, 17000)}},
    # "Value 1 - Liquid Sensor: Normal = 5500 - 10500; Open = 0 - 5100;
    # Short = >24000; High Liquid = 17100 - 23100;
    # Liquid Warning = 11400 - 16800"
    "3wire": {"": {"normal": (5500, 10500), "out": (0, 5100),
                   "short": (24000, 30000), "high": (17100, 23100),
                   "warn": (11400, 16800), "fuel": (5500, 10500)}},
    # The Universal Sensor Module has no band table anywhere on this shelf.
    # What it has is a typical response, and B4B's is B46's to the character:
    # the same two reference channels, `8900` and `32000`, over the same two
    # values, `5200` and `100000`. Two reports printing one row is the only
    # statement Veeder-Root makes about what this card reads, so the Type B
    # bands are what it reads here -- which is a reading of the page and not
    # a figure off it. Before this the module had no key at all and
    # `sensor_value` fell through to its `(0, 1)` default, so a universal
    # sensor reported nought and one ohm on a report whose own sample prints
    # 5200 and 100000. See FIDELITY L2.
    "universal": {"": {"normal": (5500, 10500), "out": (0, 5100),
                       "short": (24000, 30000), "high": (17100, 23100),
                       "warn": (11400, 16800), "fuel": (5500, 10500),
                       "water": (11400, 16800), "low": (0, 5100)}},
}

# The vapor, groundwater and 3-wire modules read TWO channels, and the second
# one is where their own alarm lives.
SECOND_BANDS = {
    # "Value 2 - Vapor Sensor: Normal = 200 - Threshold Value; Fuel = >1.05
    # times the Threshold Value ... or > 4 times the Threshold Value". The
    # threshold is the site's, so these are the shape and `vapor_bands`
    # below fills the number in. See FIDELITY R6.
    "vapor": {"normal": (200, 900), "fuel": (1100, 4000),
              "short": (0, 200), "water": (200, 900), "out": (200, 900)},
    "gw": {"normal": (80000, 150000), "out": (150000, 400000),
           "short": (2000, 80000), "waterout": (0, 2000),
           "fuel": (80000, 150000)},
    # "Value 2 - Hydrocarbon Sensor: Normal = 80000 - 280000; Fuel = >500000;
    # Short = 0 - 70000"
    "3wire": {"normal": (80000, 280000), "fuel": (500000, 800000),
              "short": (0, 70000), "high": (80000, 280000),
              "warn": (80000, 280000), "out": (80000, 280000)},
    # B4B's second channel, the same way and for the same reason.
    "universal": {"normal": (80000, 280000), "fuel": (500000, 800000),
                  "short": (0, 70000), "high": (80000, 280000),
                  "warn": (80000, 280000), "out": (80000, 280000),
                  "water": (500000, 800000), "waterout": (0, 70000),
                  "low": (80000, 280000)},
}


# ---------------------------------------------------------------------------
# The Mag probe, from Troubleshooting Guide chapter 9 and its worked examples.
#
# Table 9-3, "Normal Count Range", is a ladder: a 4 foot probe reads 700-17040
# on channels C01-C10, a 5 foot 700-21300, a 6 foot 700-25560, and so on to a
# 10 foot at 700-42600. Every one of those is the probe length in inches times
# 355, which is what makes the channel counts a HEIGHT: the manual gives the
# same constant a name on the next screen along,
#
#     "GRADIENT - Probe calibration factor used to calculate water height and
#      product height. Normal operating range 175 - 185 or 347 - 357."
#
# and the two bands are two probe generations rather than two products. The
# arithmetic closes on the manual's own field data: a site reading 44.69 in of
# unleaded shows C01-C10 at 15480, and 15480/44.69 is 346.4.
# ---------------------------------------------------------------------------
# Table 9-3's own eight, which is a diagnostic count table running 4 to 10
# foot, plus the one length the CATALOGUE names beyond it. Every float kit
# table in 577014-449 Rev G heads its column "Min/Max Probe Length" and gives
# `48" (1.2m) - 144" (3.66m)`, so a probe longer than ten foot exists and the
# count table simply does not cover it. 132 is not here because no page on
# this shelf prints it: the run is the documented lengths, not the pattern
# they suggest. See FIDELITY R14.
PROBE_LENGTHS = (48.0, 60.0, 72.0, 84.0, 90.0, 96.0, 108.0, 120.0, 144.0)

# "The probe canister must be within the riser pipe (minimum length of 10
# inches [254mm])" -- 576013-879 Rev W p.17, the step after the one that
# defines the minimum probe length. A probe is ordered for the tank AND the
# riser above it, which is why the minimum is measured to the top of the
# manway rather than to the top of the tank.
RISER_MINIMUM = 10.0
COUNTS_PER_INCH = 355.0
GRADIENT_BAND = (347.0, 357.0)          # the Mag Plus band
GRADIENT_BAND_OLD = (175.0, 185.0)      # the older Mag

# "All Probes - C00 (No Water) - 0 - 1500", and the manual's three real probes
# sit at 1334, 1309 and 1312 with dry tanks.
WATER_FLOOR = (1250.0, 1400.0)

# "Number of probe measurement samples made before calculating water height,
# product volume, and product temperature. Under normal operating conditions,
# this number should read 20."
MAG_SAMPLES = 20
CAP_SAMPLES = 40

# Table 9-2, Probe Circuit Codes. 0xD004 is the two float 8463 at 0.10 gph,
# which is what both of the manual's worked examples are.
PROBE_CIRCUIT = {"0.10": "0xD004", "0.20": "0xD005", "none": "0xD006"}

# 577013-940 Rev D p.360, "Table of Mag Probe Features", which pairs every Mag
# circuit code with the name the console calls that probe. Nothing on the
# TLS-350 shelf carries this table; it is the TLS-4XX screens manual that
# prints it, and three things agree that it holds here too: 576013-635's own
# A07 example heads its tank MAG7, 576013-818 ch.9's real console printout
# reads "T1: PROBE TYPE MAG7" beside "ID CHAN = 0xD004", and 577013-940 p.53
# names "Low-Level Mag Probes - MAG7, MAG8 and MAG9", which are D004 to D006.
#
# Read the page with care: the Probe Name and Leak Detect columns are offset a
# row by merged cells, the trap UNKNOWNS section D describes. The two columns
# below are the two that align.
# What each circuit code IS, which is the fact everything else about a probe
# follows from. The console reads the circuit code off the probe -- it is never
# entered, there is no "Set Probe Type" function anywhere in the serial manual,
# and 576013-623 p.7-13 says so plainly: "The system automatically recognizes
# which Mag probe type you have installed and will display only the applicable
# float size options."
#
# Columns: name type, family word, how many floats, does it detect water, what
# leak rate it can run, and whether it may take the 4 inch phase separation
# float. Sources: 577013-940 Rev D p.360 for the name types and the float
# counts, and 576013-818 Table 9-2 for water detect and leak detect. Read both
# with care -- 940's Probe Name column and 818's Table 9-2 are BOTH offset by
# merged cells in a plain text extraction.
#
# Note what the 1-float codes mean: no water float, so no water reading. Those
# are the probes the manuals call "high alcohol probes", and the console hides
# its water screens for them -- "This message does not appear for tanks in
# which high alcohol probes are installed".
PROBE_MODELS = {
    "C000": ("MAG1",  "MAG",  2, True,  "0.10", False),
    "C001": ("MAG2",  "MAG",  2, True,  "0.20", False),
    "D000": ("MAG3",  "MAG",  2, True,  "none", False),
    "D001": ("MAG4",  "MAG",  1, False, "0.10", False),
    "D002": ("MAG5",  "MAG",  1, False, "0.20", False),
    "D003": ("MAG6",  "MAG",  1, False, "none", False),
    "D004": ("MAG7",  "MAG",  2, True,  "0.10", True),
    "D005": ("MAG8",  "MAG",  2, True,  "0.20", True),
    "D006": ("MAG9",  "MAG",  2, True,  "none", True),
    "D007": ("MAG10", "MAG",  1, False, "0.10", False),
    "D008": ("MAG11", "MAG",  1, False, "0.20", False),
    "D009": ("MAG12", "MAG",  1, False, "none", False),
    "D021": ("GLB8",  "MAG",  2, True,  "none", False),
    "D022": ("GLB9",  "MAG",  2, True,  "none", False),
    "D023": ("GLB10", "MAG",  1, False, "none", False),
    "D024": ("GLB11", "MAG",  1, False, "none", False),
    "D041": ("MAG-D", "MAG",  2, True,  "0.10", False),
    "D042": ("MAG-D", "MAG",  2, True,  "0.20", False),
    "D043": ("MAG-D", "MAG",  2, True,  "none", False),
    # The capacitance probes, from A01's own example: a CAP0 answers circuit
    # code 0001 and a CAP1 A66C. Both were discontinued -- see PROBE_FEATURE.
    "0001": ("CAP0",  "CAP0", 0, True,  "none", False),
    "A66C": ("CAP1",  "CAP1", 0, True,  "none", False),
}

# 576013-818 Rev AB Table 9-2, "Mag Probe Minimum Detected Fluid Levels",
# in inches: the shallowest fuel and the shallowest water this probe can
# resolve, per circuit code and per float kit. Below the fuel figure the two
# floats are too close together to be told apart, which is the cause both
# manuals give for the alarm -- 576013-939 Quick Help p.14, "fuel and water
# level floats on the probe are too close together due to a lack of fuel in
# the tank" -- and for the leak test invalidation that goes with it.
#
# Read off the page's word boxes with tools/pagelines.py rather than out of
# the plain extraction. It is the same table whose merged cells are already
# noted above, and the eight numeric columns interleave in text order.
#
# Keyed by the float size AS S62F HOLDS IT, so the lookup is the setting
# rather than a translation of it: `4` is the 4 inch ethanol-blended
# gasoline float, `0` the 4 inch, `2` the 3 inch and `1` the 2 inch. The
# page draws an em dash for a size a probe does not take, and those are
# simply absent; `None` in the water slot is a one-float probe, which has no
# water float to have a minimum for. There is no 1 inch column on the page,
# so `3` is absent everywhere.
_TWO_FLOAT_8473 = {"0": (8.0, 0.75), "1": (9.5, 0.75)}
_ONE_FLOAT_8473 = {"0": (5.0, None), "1": (7.0, None)}
_TWO_FLOAT_8463 = {"4": (7.000, 0.38), "0": (3.04, 0.63),
                   "2": (3.04, 0.63), "1": (3.23, 0.867)}
_ONE_FLOAT_8463 = {"0": (0.985, None), "2": (0.985, None), "1": (3.0, None)}
_TWO_FLOAT_8468 = {"0": (3.04, 0.63), "2": (3.04, 0.63), "1": (3.23, 0.867)}
_DENSITY_8860 = {"1": (8.0, 0.87)}

MINIMUM_LEVELS = {
    # Form Number 8473
    "C000": _TWO_FLOAT_8473, "C001": _TWO_FLOAT_8473, "D000": _TWO_FLOAT_8473,
    "D001": _ONE_FLOAT_8473, "D002": _ONE_FLOAT_8473, "D003": _ONE_FLOAT_8473,
    # Form Numbers 8463 & 8493
    "D004": _TWO_FLOAT_8463, "D005": _TWO_FLOAT_8463, "D006": _TWO_FLOAT_8463,
    "D007": _ONE_FLOAT_8463, "D008": _ONE_FLOAT_8463, "D009": _ONE_FLOAT_8463,
    # Form Number 8468
    "D021": _TWO_FLOAT_8468, "D022": _TWO_FLOAT_8468,
    "D023": _ONE_FLOAT_8463, "D024": _ONE_FLOAT_8463,
    # Density Mag Probes, Form Number 8860: the 2 inch float and nothing else
    "D041": _DENSITY_8860, "D042": _DENSITY_8860, "D043": _DENSITY_8860,
}

# 577014-449 Rev G's Mag Plus specification is the second source, and it
# agrees with the 8463 family exactly: "Minimum Measurement -- 2" Float Kits
# 3.23" / 0.867"" and "4" Float Kits 3.04" / 0.630"". Two documents, one
# written for the technician and one for the specifier, and the same pair of
# numbers. Nothing on this shelf gives a minimum for the CAP probes.

# Which row of the version matrix each probe family is gated by, so a console
# only offers probes its software actually supports. The CAP rows are the
# interesting ones: Cap 1 ends at version 8 and Cap 0 at version 17, so a
# modern console cannot have either.
PROBE_FEATURE = {
    "0001": "cap0", "A66C": "cap1",
    "C000": "mag012", "C001": "mag012", "D000": "mag3",
    "D001": "mag456", "D002": "mag456", "D003": "mag456",
    "D004": "mag712", "D005": "mag712", "D006": "mag712",
    "D007": "mag712", "D008": "mag712", "D009": "mag712",
    "D021": "mag712", "D022": "mag712", "D023": "mag712", "D024": "mag712",
    # No table on this shelf gates the density probes at all. See UNKNOWNS.
    "D041": None, "D042": None, "D043": None,
}

MAG_NAME_TYPE = {"C000": "MAG1",  "C001": "MAG2",  "D000": "MAG3",
                 "D001": "MAG4",  "D002": "MAG5",  "D003": "MAG6",
                 "D004": "MAG7",  "D005": "MAG8",  "D006": "MAG9",
                 "D007": "MAG10", "D008": "MAG11", "D009": "MAG12",
                 "D021": "GLB8",  "D022": "GLB9",  "D023": "GLB10",
                 "D024": "GLB11",
                 "D041": "MAG-D", "D042": "MAG-D", "D043": "MAG-D"}


def probe_length(diameter):
    """The probe somebody would have ordered for a tank that deep.

    576013-879 Rev W p.17, both steps: "Measure the distance from the bottom
    of the tank to the TOP OF THE PROBE MANWAY, or the 2-, 3- or 4-inch tank
    opening -- this is the minimum probe length", and "the probe canister
    must be within the riser pipe (minimum length of 10 inches [254mm])".

    So the minimum is the tank plus the riser, not the tank. This passed the
    diameter alone, which `readings`' own module docstring had already
    contradicted -- "a probe's length is the tank's diameter plus its riser"
    -- and a 96 inch tank got a 96 inch probe where the manual wants at least
    106. See FIDELITY R14.
    """
    want = diameter + RISER_MINIMUM
    for length in PROBE_LENGTHS:
        if length >= want:
            return length
    return PROBE_LENGTHS[-1]


# "Thresholds are in ohms and must be calculated for each vapor sensor",
# 576013-623 Rev AN ch.19, and the entry range is 1k to 100k.
VAPOR_THRESHOLD_DEFAULT = 25000.0


def vapor_bands(console, number):
    """One vapor sensor's second-channel bands, from ITS OWN threshold.

    576013-818 Fig 6-18 gives the bands as arithmetic on a number the site
    enters: "Normal = 200 - Threshold Value; Fuel = >1.05 times the
    Threshold Value for longer than 24 hours, or > 4 times the Threshold
    Value; Short = < 200", and "This Sensor Threshold value is used in the
    Value 2 calculations above."

    It was a fixed 200-900 normal band, which is BELOW the 1k minimum the
    threshold itself can be set to, so the channel sat pinned near the short
    boundary whatever the site programmed and changing the threshold -- the
    one thing the setup screen exists to do -- did nothing. The 24-hour arm
    of the fuel rule is a persistence this model has nowhere to keep, so
    fuel is the immediate one, four times the threshold. See FIDELITY R6.
    """
    threshold = VAPOR_THRESHOLD_DEFAULT
    if console is not None:
        try:
            raw = console.limit("708", number)
        except Exception:
            raw = None
        if raw:
            threshold = float(raw)
    threshold = max(1000.0, min(100000.0, threshold))
    return {"normal": (200.0, threshold),
            "fuel": (4.0 * threshold, 4.4 * threshold),
            "short": (0.0, 200.0),
            "water": (200.0, threshold),
            "out": (200.0, threshold)}


# The concentration screen is four digits wide -- 576013-818 Fig 6-18 draws
# `XXXX  PPM` where the value it converts is `XXXXXX` -- so this is where it
# reads full. The SCALE is the simulator's; the two ends are the manual's.
# See UNKNOWNS A30.
VAPOR_PPM_FULL = 9999.0


def vapor_ppm(console, number, ohms):
    """A vapour sensor's resistance as parts per million.

    576013-818 Fig 6-18 draws two screens, `1 = XXXX 2 = XXXXXX` and
    `XXXX  PPM`, and says what the second one is: "sensor detected
    hydrocarbon vapor (see value 2 above) CONVERTED TO PARTS PER MILLION".
    This console printed value 2 itself with ` PPM` written after it, which
    is the resistance under a heading that says it is not one -- and the two
    screens are drawn at different widths, which is the manual saying so
    twice. See FIDELITY R7.

    **No conversion curve is on this shelf, and the two ENDS are.**
    576013-623 Rev AN p.19-2 calibrates the sensor: "measure the resistance
    across the V and G terminals for each sensor using an ohmmeter ... for
    each sensor, multiply the measured resistance by 4 to determine the
    vapor threshold value that you should enter." So a clean well sits at a
    quarter of its own threshold, and the threshold is where the console
    starts calling it fuel. Zero at one end, full scale at the other, and
    what happens between them is a straight line because nothing here says
    otherwise. UNKNOWNS A30.
    """
    bands = vapor_bands(console, number)
    threshold = bands["normal"][1]
    clean = threshold / 4.0
    if threshold <= clean:
        return 0.0
    share = (float(ohms) - clean) / (threshold - clean)
    return max(0.0, min(1.0, share)) * VAPOR_PPM_FULL


def sensor_value(console, module, number, state, channel=1):
    """The resistance the diagnostic screen prints for that sensor.

    Which band it falls in IS the sensor's state, which is what the screen is
    for; where in the band is the sensor's own, and it moves a little.
    """
    kind = console.sensor_type(module, number) if console else ""
    if channel == 2:
        table = (vapor_bands(console, number) if module == "vapor"
                 else SECOND_BANDS.get(module) or {})
    else:
        by_type = BANDS.get(module) or {}
        table = by_type.get(kind) or by_type.get("") or by_type.get("1") or {}
    band = table.get(state) or table.get("normal") or (0, 1)
    return wander(console, band[0], band[1], module, number, channel,
                  swing=0.10, period=1200.0)
