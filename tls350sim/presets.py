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
"""Example sites, so there is something to look at without programming one.

Each preset is a whole console: the software in it, the cards in the cage, the
software keys, the programming those cards need, and a site with fuel in it.
They are written the way the panel writes them, so a tool reading them back
over the wire sees what it would see on the real thing.

A preset that does not name a software version and a CPU board gets the
console's own defaults, because a site programmed for cards its console cannot
drive is not an example of anything.
"""
from . import console as _console
from . import packed


def _float(device, value):
    return f"{device:02d}" + packed.hexfloat(value)


def _text(device, value, width=20):
    return f"{device:02d}" + value.ljust(width)[:width]


def _tank(values, tank, label, code, full, diameter, limits):
    """One programmed tank: what it is, how big, and where its alarms sit."""
    values[f"S601{tank:02d}"] = f"{tank:02d}1"
    values[f"S602{tank:02d}"] = _text(tank, label)
    values[f"S603{tank:02d}"] = f"{tank:02d}{code}"
    values[f"S604{tank:02d}"] = _float(tank, full)
    values[f"S607{tank:02d}"] = _float(tank, diameter)
    values[f"S609{tank:02d}"] = _float(tank, 0.00070)      # thermal coeff
    values[f"S610{tank:02d}"] = f"{tank:02d}05"            # 5 minute delivery delay
    for code_, value in limits.items():
        values[f"S{code_}{tank:02d}"] = _float(tank, value)


def _limits(full):
    """The alarm limits a site would actually programme, from the tank size."""
    return {"628": full,               # max or label volume
            "623": full * 0.95,        # overfill
            "622": full * 0.90,        # high product
            "629": full * 0.25,        # delivery needed
            "621": full * 0.10,        # low product
            # "Typically, you should set this limit at 25 gallons or 100
            # litres, or higher", and the Leak Alarm Limit takes 1 to 99
            # gallons: "A limit value of 8 gallons will warn of a 1 gph leak
            # in 8 hours". Neither of them scales with the tank.
            "625": 25.0,               # sudden loss
            "626": 8.0,                # leak alarm
            "624": 2.0,                # high water
            "627": 1.0,                # water warning
            "636": full * 0.20,        # periodic test minimum
            "62A": full * 0.50}        # annual test minimum


def _header(values, *lines):
    for i, line in enumerate(lines, start=1):
        values[f"S503{i:02d}"] = line.ljust(20)[:20]


def two_tank_retail():
    """A forecourt: two grades, pressurised lines, sumps watched."""
    values = {}
    _header(values, "GREENFIELD SERVICE", "1200 STATION ROAD",
            "GREENFIELD", "")
    _tank(values, 1, "REGULAR UNLEADED", "1", 10000, 96, _limits(10000))
    _tank(values, 2, "PREMIUM UNLEADED", "2", 8000, 96, _limits(8000))
    for line in (1, 2):
        values[f"S781{line:02d}"] = f"{line:02d}1"
        values[f"S782{line:02d}"] = _text(line, f"LINE {line}")
        values[f"S788{line:02d}"] = f"{line:02d}02"        # 2.0 steel
        values[f"S789{line:02d}"] = _float(line, 120)
        values[f"S784{line:02d}"] = f"{line:02d}02"        # shut down at 0.2
        values[f"S785{line:02d}"] = f"{line:02d}{line:02d}"
    for sensor in (1, 2, 3):
        values[f"S701{sensor:02d}"] = f"{sensor:02d}1"
        values[f"S702{sensor:02d}"] = _text(sensor, f"STP SUMP {sensor}")
        values[f"S703{sensor:02d}"] = f"{sensor:02d}1"     # tri-state
        values[f"S704{sensor:02d}"] = f"{sensor:02d}5"     # STP sump
    return {
        "modules": {"probe": 1, "liquid": 1, "plld": 1, "rs232": 1},
        "software": {"plld020": True, "plld010": True},
        "values": values,
        "tanks": {1: {"volume": 6200.0, "water": 0.5},
                  2: {"volume": 3100.0, "water": 0.0}},
    }


def truck_stop():
    """Four tanks, diesel on pressurised lines, pump sense, BIR running."""
    values = {}
    _header(values, "JUNCTION 14 TRUCKSTOP", "OLD MILL ROAD", "", "")
    _tank(values, 1, "DIESEL", "3", 20000, 120, _limits(20000))
    _tank(values, 2, "DIESEL", "3", 20000, 120, _limits(20000))
    _tank(values, 3, "REGULAR UNLEADED", "1", 12000, 96, _limits(12000))
    _tank(values, 4, "DEF", "9", 4000, 64, _limits(4000))
    for line in (1, 2, 3, 4):
        values[f"S781{line:02d}"] = f"{line:02d}1"
        values[f"S782{line:02d}"] = _text(line, f"LANE {line}")
        values[f"S788{line:02d}"] = f"{line:02d}02"        # 2.0 steel
        values[f"S789{line:02d}"] = _float(line, 200)
        values[f"S784{line:02d}"] = f"{line:02d}02"        # shut down at 0.2
        values[f"S785{line:02d}"] = f"{line:02d}{line:02d}"
    for pump in (1, 2, 3, 4):
        values[f"S771{pump:02d}"] = f"{pump:02d}1"
        values[f"S772{pump:02d}"] = f"{pump:02d}{pump:02d}"
    for tank in (1, 2, 3, 4):
        # AccuChart wants meter data, a Mag probe and a chart it can
        # improve: "Meter Data Present = NO" is the first reason it does not
        # run, and it "does not enable when the tank profile is set to
        # linear" or when the probe is not a Mag.
        values[f"S615{tank:02d}"] = f"{tank:02d}1"
        values[f"S616{tank:02d}"] = f"{tank:02d}1"        # CAL UPDATE IMMEDIATE
        values[f"S62F{tank:02d}"] = f"{tank:02d}1"        # Mag probe float
        values[f"S639{tank:02d}"] = f"{tank:02d}1"        # END FACTOR FLAT
    values["S51C00"] = "1"            # ticketed delivery
    values["S51100"] = "1"            # shift BIR printouts
    values["S51200"] = "1"            # daily BIR printouts
    values["S79300"] = "0600"         # daily closing time
    values["S79401"] = "010600"
    return {
        "modules": {"probe": 1, "liquid": 1, "plld": 1, "pump": 1, "io": 1,
                    "edim": 1,
                    "rs232": 1, "modem": 1},
        "software": {"bir": True, "fuelman": True, "plld020": True,
                     "plld010": True},
        "values": values,
        # tank 4 is down to its delivery limit, which is the point of it
        "tanks": {1: {"volume": 14000.0, "water": 0.5},
                  2: {"volume": 9000.0, "water": 0.0},
                  3: {"volume": 7000.0, "water": 0.0},
                  4: {"volume": 900.0, "water": 0.0}},
        "meters": {1: 1, 2: 1, 3: 2, 4: 3},
    }


def compliance_site():
    """A site set up for testing: CSLD on every tank, sensors everywhere."""
    values = {}
    _header(values, "NORTHGATE FUEL", "COMPLIANCE DEMO", "", "")
    for tank, (label, code, full) in enumerate(
            (("REGULAR UNLEADED", "1", 12000), ("PREMIUM", "2", 8000),
             ("DIESEL", "3", 12000)), start=1):
        _tank(values, tank, label, code, full, 96, _limits(full))
        # twelve hours, 0.2 gph, method 7 = CSLD
        values[f"S611{tank:02d}"] = f"{tank:02d}" + "12" + "0" + "7" + "0000"
        values[f"S62D{tank:02d}"] = f"{tank:02d}111"       # all fail alarms on
    for sensor in range(1, 5):
        values[f"S701{sensor:02d}"] = f"{sensor:02d}1"
        values[f"S702{sensor:02d}"] = _text(sensor, f"ANNULAR TANK {sensor}")
        values[f"S703{sensor:02d}"] = f"{sensor:02d}4"     # dual float discrim
        values[f"S704{sensor:02d}"] = f"{sensor:02d}2"     # annular
    for sensor in (1, 2):
        values[f"S711{sensor:02d}"] = f"{sensor:02d}1"
        values[f"S712{sensor:02d}"] = _text(sensor, f"MONITOR WELL {sensor}")
        values[f"S713{sensor:02d}"] = f"{sensor:02d}4"     # monitoring well
    values["S54600"] = "1"            # tank periodic test needed warning
    values["S54700"] = "07"           # warn after seven days without a pass
    values["S54800"] = "14"           # and alarm after fourteen
    return {
        # an NVMEM203 board, because this site has a Maintenance Tracker in
        # it and that is the memory card Maintenance Tracker wants
        "board": "E6",
        "modules": {"probe": 1, "liquid": 1, "gw": 1, "rs232": 1, "mt": 1},
        "software": {"csld": True},
        "values": values,
        # tank 3 has water over its limit and is losing product, so the
        # console has something to find
        "tanks": {1: {"volume": 8000.0, "water": 0.0},
                  2: {"volume": 5000.0, "water": 0.0},
                  3: {"volume": 9000.0, "water": 2.5}},
        "leaks": {3: 0.35},
    }


PRESETS = {
    "Two-tank retail site": two_tank_retail,
    "Truck stop, four tanks and BIR": truck_stop,
    "Compliance site, CSLD and sensors": compliance_site,
}


def load(console, name):
    """Put a whole example console in place of whatever is there."""
    build = PRESETS.get(name)
    if build is None:
        return False
    site = build()
    console.reset(keep_clock=True)
    console.version = site.get("version", _console.DEFAULT_VERSION)
    console.board = site.get("board", _console.DEFAULT_BOARD)
    console.modules = dict(site["modules"])
    console.software = dict(site.get("software") or {})
    # a preset is a whole different console, chip and cards included: no
    # ROM Revision or MT-removed warnings for becoming one
    console.rom_at_boot = console.version
    console._mt_seen = console.has("mt")
    console.values.update(site["values"])
    console.tank_level = {int(k): dict(v)
                          for k, v in (site.get("tanks") or {}).items()}
    console.tank_leak = {int(k): float(v)
                         for k, v in (site.get("leaks") or {}).items()}
    console.meters = {int(m): int(t)
                      for m, t in (site.get("meters") or {}).items()}
    console.save()
    return True
