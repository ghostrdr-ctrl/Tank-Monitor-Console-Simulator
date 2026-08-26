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


def wander(console, low, high, *key, swing=0.06, period=1800.0):
    """A reading inside its tolerance, moving the way a real one does.

    `swing` is the fraction of the band it wanders over and `period` how long
    a full excursion takes on the CONSOLE's clock, so running the bench fast
    makes the numbers move fast too.
    """
    base = fixed(low, high, *key)
    span = abs(high - low) * swing
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
}

# The vapor, groundwater and 3-wire modules read TWO channels, and the second
# one is where their own alarm lives.
SECOND_BANDS = {
    # "Value 2 - Vapor Sensor: Normal = 200 - Threshold Value; Fuel = >1.05
    # times the Threshold Value ... or > 4 times the Threshold Value"
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
PROBE_LENGTHS = (48.0, 60.0, 72.0, 84.0, 90.0, 96.0, 108.0, 120.0)
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


def probe_length(diameter):
    """The probe somebody would have ordered for a tank that deep.

    "Measure the distance from the bottom of the tank to the top of the probe
    manway ... this is the minimum probe length": so the shortest standard
    probe that clears the tank, and the longest one if none does.
    """
    for length in PROBE_LENGTHS:
        if length >= diameter:
            return length
    return PROBE_LENGTHS[-1]


def sensor_value(console, module, number, state, channel=1):
    """The resistance the diagnostic screen prints for that sensor.

    Which band it falls in IS the sensor's state, which is what the screen is
    for; where in the band is the sensor's own, and it moves a little.
    """
    kind = console.sensor_type(module, number) if console else ""
    if channel == 2:
        table = SECOND_BANDS.get(module) or {}
    else:
        by_type = BANDS.get(module) or {}
        table = by_type.get(kind) or by_type.get("") or by_type.get("1") or {}
    band = table.get(state) or table.get("normal") or (0, 1)
    return wander(console, band[0], band[1], module, number, channel,
                  swing=0.10, period=1200.0)
