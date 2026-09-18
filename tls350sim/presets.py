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
    # The PRODUCT's coefficient, from 576013-623 Rev AN Table 7-1, not one
    # figure for the whole site: this wrote unleaded's 0.00070 into every
    # tank, so the truck stop's diesel was 56% high. FIDELITY Y6.
    values[f"S609{tank:02d}"] = _float(
        tank, _console.Console.thermal_coefficient(label) or 0.00070)
    values[f"S610{tank:02d}"] = f"{tank:02d}05"            # 5 minute delivery delay
    # FLOAT SIZE, which every programmed tank has and only one preset wrote.
    # Without it `probe_type` falls back to "a tank programmed with a float
    # size has a float, and only a Mag has one", so two of the three presets
    # reported CAP0 PROBE -- on consoles running version 33, where the CAP
    # probes stopped at version 17. A CAP0 tank reads a different gradient
    # band, a forty sample window and one fewer leak rate, so it is not a
    # label. 2 inch because it is the one column every Mag circuit code in
    # 576013-818 Table 9-2 has, including the Mag-D rows that have no other.
    # See FIDELITY R11.
    values[f"S62F{tank:02d}"] = f"{tank:02d}1"            # FLOAT SIZE: 2.0 IN.
    for code_, value in limits.items():
        values[f"S{code_}{tank:02d}"] = _float(tank, value)


def _limits(full):
    """The alarm limits a site would actually programme, from the tank size."""
    return {"628": full,               # max or label volume
            "623": 90.0,               # overfill, a PERCENT of the label volume
            "622": 95.0,               # high product, ABOVE overfill: see below
            "629": 25.0,               # delivery needed, a percent
            "621": full * 0.10,        # low product
            # "Typically, you should set this limit at 25 gallons or 100
            # litres, or higher", and the Leak Alarm Limit takes 1 to 99
            # gallons: "A limit value of 8 gallons will warn of a 1 gph leak
            # in 8 hours". Neither of them scales with the tank.
            "625": 25.0,               # sudden loss
            "626": 8.0,                # leak alarm
            "624": 2.0,                # high water
            "627": 1.0,                # water warning
            "636": 20.0,               # periodic test minimum, a percent
            "62A": 50.0}               # annual test minimum, a percent
    # High Product sits ABOVE Overfill, which is the way round a real tape
    # has them and the way 576013-623 Rev AN describes them: "Set this limit
    # at a percentage that is between the Overfill Limit percentage and
    # 95%". High Product is the backstop for a fill too gradual to be
    # recognised as a delivery -- "whether or not a delivery is in
    # progress" -- so it is the later alarm, not the earlier one. This
    # console had the two swapped.


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
        # and both precision rates on MANUAL, which is what lets a
        # technician start one by hand: an unprogrammed schedule is
        # DISABLED, "No manual or automatic 0.2 gph testing is allowed",
        # 576013-623 Rev AN p.10-4. FIDELITY O24.
        values[f"S78C{line:02d}"] = f"{line:02d}3"         # 0.20 GPH: MANUAL
        values[f"S783{line:02d}"] = f"{line:02d}3"         # 0.10 GPH: MANUAL
        values[f"S785{line:02d}"] = f"{line:02d}{line:02d}"
    for sensor in (1, 2, 3):
        values[f"S701{sensor:02d}"] = f"{sensor:02d}1"
        values[f"S702{sensor:02d}"] = _text(sensor, f"STP SUMP {sensor}")
        values[f"S703{sensor:02d}"] = f"{sensor:02d}1"     # tri-state
        values[f"S704{sensor:02d}"] = f"{sensor:02d}5"     # STP sump
    return {
        # An EDIM and the BIR key, because this is the site the simulator
        # opens on and a forecourt that cannot sell fuel is a poor first
        # thing to see. On this bench fuel leaves a tank only through a
        # meter the console can account for -- `Bir._dispense` is the one
        # thing that draws a tank down from a sale, and it wants a DIM in
        # the cage and the key in the console -- so without them the
        # traffic generator ran, counted its cars, lifted the handles, and
        # moved nothing. Two nozzles on each grade is the forecourt a
        # two-tank site has.
        "modules": {"probe": 1, "liquid": 1, "plld": 1, "rs232": 1,
                    "edim": 1},
        "software": {"plld020": True, "plld010": True, "bir": True},
        "values": values,
        "tanks": {1: {"volume": 6200.0, "water": 0.5},
                  2: {"volume": 3100.0, "water": 0.0}},
        "meters": {1: 1, 2: 1, 3: 2, 4: 2},
        "card": {"ip": "10.14.5.20", "gateway": "10.14.5.1",
                 "netmask_bits": 8, "port": 10001},
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
        # and both precision rates on MANUAL, which is what lets a
        # technician start one by hand: an unprogrammed schedule is
        # DISABLED, "No manual or automatic 0.2 gph testing is allowed",
        # 576013-623 Rev AN p.10-4. FIDELITY O24.
        values[f"S78C{line:02d}"] = f"{line:02d}3"         # 0.20 GPH: MANUAL
        values[f"S783{line:02d}"] = f"{line:02d}3"         # 0.10 GPH: MANUAL
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
    # and its shift starts then: Last-Shift Inventory is on the panel only
    # where a Shift Start Time is programmed. FIDELITY O22.
    values["S50201"] = "0600"
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
        "card": {"ip": "192.168.42.15", "gateway": "192.168.42.1",
                 "netmask_bits": 8, "port": 10001},
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
        # The EDIM and the BIR key are what let this site SELL, and a CSLD
        # site that cannot sell is a contradiction: CSLD's whole input is
        # the shape of the day -- 576013-818 Figure 11-2, "Tank goes idle
        # and must remain so for 8 minutes" -- and there is no idle to
        # measure on a site where the tank never moves. This preset had
        # CSLD on all three tanks, no DIM, no meters and no BIR key, so
        # the traffic generator could run on it all day and not shift a
        # gallon. On this bench fuel only leaves a tank through a meter
        # the console can account for; see `Bir._dispense`.
        "modules": {"probe": 1, "liquid": 1, "gw": 1, "rs232": 1, "mt": 1,
                    "edim": 1},
        "software": {"csld": True, "bir": True},
        "values": values,
        # tank 3 has water over its limit and is losing product, so the
        # console has something to find
        "tanks": {1: {"volume": 8000.0, "water": 0.0},
                  2: {"volume": 5000.0, "water": 0.0},
                  3: {"volume": 9000.0, "water": 2.5}},
        "leaks": {3: 0.35},
        # two nozzles on the regular tank and one on each of the others,
        # which is the forecourt a three-tank site actually has
        "meters": {1: 1, 2: 1, 3: 2, 4: 3},
        "card": {"ip": "172.20.8.30", "gateway": "172.20.8.1",
                 "netmask_bits": 8, "port": 10002},
    }


PRESETS = {
    "Two-tank retail site": two_tank_retail,
    "Truck stop, four tanks and BIR": truck_stop,
    "Compliance site, CSLD and sensors": compliance_site,
}


def card_of(name):
    """The TCP/IP card settings an example site is programmed with.

    Each site has its own address, as real ones do: switching preset should
    mean going and finding the new card, not carrying on at the old address.
    """
    build = PRESETS.get(name)
    return dict((build() or {}).get("card") or {}) if build else {}


def load(console, name, card=None):
    """Put a whole example console in place of whatever is there.

    `card` is the XPort configuration to program alongside it, so the site
    comes up on its own address the way it would in the field.
    """
    build = PRESETS.get(name)
    if build is None:
        return False
    site = build()
    if card is not None and site.get("card"):
        spec = site["card"]
        card.reset_card(ip=spec.get("ip"), gateway=spec.get("gateway"),
                        netmask_bits=spec.get("netmask_bits"))
        if spec.get("port"):
            card.program(port=spec["port"])
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
