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
"""The sensor family of function codes: what is on the end of each wire.

Every report a technician uses to answer "is that sump wet, and if the console
says it is, what is the module actually reading?". Three kinds of report, once
per sensor family:

  the STATUS report      what the console is saying about the sensor now
  the ALARM HISTORY      when it last said it, and what it said
  the DIAGNOSTIC report  the resistance underneath, which is why it says it

They are a family in the manual and a family here, because the layouts differ
only in the title and in how many channels the module reads: the liquid module
reads one, the vapor, groundwater and 3-wire modules read two.

Kept out of wire.py because one if-chain for five hundred function codes is
not a design, and imported BY wire.py, which is why nothing here imports from
it: `SEP` is defined again rather than borrowed.
"""
import re
import time

from . import packed
from . import readings
from .clock import clock_words
from .console import STATUS_TYPES

# display format "includes all the necessary formatting characters such as
# carriage returns, line feeds, nulls, spaces, labels"
SEP = chr(13) + chr(10)


# ---------------------------------------------------------------------------
# The sensor families.
#
# Each one is a module in the cage, a status category in i10100's alarm list,
# the config screen that says which of its positions are wired up, the
# function that holds a position's location label, the word the report titles
# itself with, and what the manual's own sample calls a sensor nobody has
# labelled ("LIQUID # 1", "2 WIRE CL SENSOR #1").
# ---------------------------------------------------------------------------
#           module      aa    config label  title          unlabelled sensor
FAMILY = {
    "liquid": ("03", "701", "702", "LIQUID", "LIQUID # {n}"),
    "vapor":  ("04", "706", "707", "VAPOR", "VAPOR # {n}"),
    "gw":     ("07", "711", "712", "GROUNDWATER", "GROUND WATER # {n}"),
    "2wire":  ("08", "741", "742", "2 WIRE CL", "2 WIRE CL SENSOR #{n}"),
    "3wire":  ("12", "746", "747", "3 WIRE CL", "3 WIRE CL SENSOR #{n}"),
    # i10100 calls category 13 the Universal Sensor, and 34B, 34C and B4B
    # report it exactly as the five families above are reported. The card is
    # the part this cage does not stock: there is no Universal Sensor Module
    # in console.MODULES, so no console this simulator can build ever has one
    # to report on. The family stays here whole, and the three codes stay out
    # of CODES until the card is in the cage, because a code this console
    # claims has to be a code it can serve.
    "universal": ("13", "74B", "74C", "UNIVERSAL", "UNIVERSAL SENSOR #{n}"),
}

# These three wait on the Universal Sensor Module, which is now a card the
# bench can fit -- see the note beside it in console.MODULES. They were
# written and then held back behind this set; the card being fittable is what
# lets them answer, and a console without one still says 9999 because it has
# no such sensor to report on.
UNIVERSAL_CODES = {"34B", "34C", "B4B"}

# Which family each status and each alarm history report belongs to
STATUS_CODE = {"301": "liquid", "306": "vapor", "311": "gw",
               "341": "2wire", "346": "3wire", "34B": "universal"}
HISTORY_CODE = {"302": "liquid", "307": "vapor", "312": "gw",
                "342": "2wire", "347": "3wire", "34C": "universal"}

# The smart sensor is its own family: a different card, a different alarm
# list, and a sensor on the end of the wire that talks back.
SMART_AA = "28"

# "rr - Pump Relay Monitor Number", category 34 in i10100's list
PUMPMON_AA = "34"

# "II - Input Number", category 05
INPUT_AA = "05"

# What the manual's own samples call an input and a relay nobody has labelled
INPUT_LABEL = "* EXTERNAL INPUT {n} *"
RELAY_LABEL = "* RELAY {n} *"

CODES = {"301", "302", "306", "307", "311", "312", "315", "316", "322",
         "323", "333", "341", "342", "346", "347",
         "401", "402", "403", "406",
         "B01", "B06", "B07", "B11", "B21", "B33", "B34", "B35", "B36",
         "B37", "B38", "B39", "B41", "B46", "B72"} | UNIVERSAL_CODES


# ---------------------------------------------------------------------------
# Small readers, shared by every report below
# ---------------------------------------------------------------------------
def _number(text):
    """The number off a diagnostic screen.

    The diagnostic REPORT and the diagnostic SCREEN have to show the same
    reading: a technician who reads 33.2 inches on the panel and asks the
    same console for IB3301 has found a bug if the answer differs. So the
    report takes its numbers off the console's own screen rather than
    generating a second set beside them.
    """
    found = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(found.group()) if found else 0.0


def _tail(text):
    """The reading off a screen that labels it, "CNTR = 1 VALUE = 145727"."""
    return _number((text or "").rpartition("=")[2])


def _when(packed):
    """YYMMDDHHmm as the display format writes it: "JAN 6, 1995  8:02 AM"."""
    try:
        return clock_words(time.strptime(packed, "%y%m%d%H%M"))
    except ValueError:
        return packed


def _stamp(console, ago_hours=0.0):
    """The console's own clock, packed, that many hours back."""
    return time.strftime("%y%m%d%H%M", time.localtime(
        time.mktime(console.now()) - ago_hours * 3600.0))


# One ASCII hex IEEE float, and a counted run of them, as every packed data
# field carries them. Both are packed's, under this module's names for them.
_float = packed.hexfloat
_floats = packed.hexfloats


def _devices(console, module, config, dev):
    """The devices one command is asking about.

    "SS - Sensor Number (Decimal, 00=all)", where all is the positions the
    console has been told are connected rather than every wire on the card:
    "only the Functions/Steps relevant to your console and its installed
    options and connected detection systems will be accessible". A command
    naming one device gets that device whether or not anybody wired it up,
    because that is the question it asked.
    """
    if dev != "00" and dev.isdigit() and int(dev):
        return [int(dev)]
    return console.configured_devices(config, console.capacity(module))


def _label(console, code, number, pattern):
    """What the site called this device, or what the manual calls one nobody
    has labelled."""
    return console.text(code, number) or pattern.format(n=number)


def _standing(console, aa):
    """{device number: [NN]} for every alarm of that category on the display.

    The status reports say what the console is SAYING, which is
    `compute_alarms`, not the raw physical state: an alarm that has been
    corrected but is still latched is still on the screen and still in the
    report.
    """
    out = {}
    for record in console.compute_alarms():
        if record[:2] != aa:
            continue
        out.setdefault(int(record[4:6]) if record[4:6].isdigit() else 0,
                       []).append(record[2:4])
    return out


def _incidents(console, aa, number):
    """The alarm history for one device, newest first.

    "NN - Number of Alarm Incidents to follow" over a list of alarm type
    numbers with no value in it for an alarm that has gone away: so a history
    report is the occurrences, and the 01 clear records the console keeps for
    I111 and I112 are not incidents.
    """
    tt = f"{int(number):02d}"
    return [r for r in console.alarm_log
            if r["aa"] == aa and r["tt"] == tt
            and r.get("state", "02") != "01"][:99]


def _sample_counter(console):
    """"Cntr = Number of times system has looked at Value."

    A counter, so it counts: the module looks at each sensor about once a
    second and the screen has room for two digits of it. Running the bench
    clock fast runs the counter fast, the same as everything else here.
    """
    return float(int(time.mktime(console.now())) % 100)


def _reference(console, token, number, nominal, key="ref"):
    """One of the module's own A/D reference channels.

    A reference channel is a resistor on the board rather than anything down
    the riser, so it is not derivable from the site; each report's own
    typical response prints what one reads, and the value sits near that and
    moves the way a reading does.
    """
    return readings.wander(console, nominal * 0.97, nominal * 1.03,
                           key, token, number, swing=0.20, period=900.0)


# ---------------------------------------------------------------------------
# Status and alarm history, the sensor families
# ---------------------------------------------------------------------------
# "ssss - Sensor Status Value: 0000=Sensor Normal, 0001=Sensor Setup Data
# Warning, 0002=Sensor Fuel Alarm ... 0009=Sensor Liquid Warning". That is
# i10100's alarm list for a sensor category shifted down by one, because
# i10100 starts its numbering at 01 for the category itself: NN 02 Setup Data
# Warning is ssss 0001, NN 03 Fuel Alarm is ssss 0002, and NN 10 Liquid
# Warning is ssss 0009.
SETUP_NN = "02"

# The smart sensor list is NOT shifted: "0001=Smart Sensor Setup Data Warning,
# 0002=Smart Sensor Communication Alarm ... 0014=Smart Sensor Install Alarm"
# runs one for one with console.SMART_STATE_NN.
SMART_SETUP_NN = "01"


def _status_value(aa, nn):
    """The four digit status value for one alarm number."""
    if nn is None:
        return "0000"
    shift = 0 if aa in (SMART_AA, PUMPMON_AA, INPUT_AA) else 1
    return f"{int(nn) - shift:04d}"


def _worst(aa, numbers):
    """Which alarm a one-value status report speaks for.

    A status report has room for one condition and a sensor can have a setup
    data warning standing behind a real alarm. The alarm is what the sensor
    is reading, so it is the one the report carries; the warning is about the
    programming and only speaks when nothing else does.
    """
    setup = SMART_SETUP_NN if aa == SMART_AA else SETUP_NN
    ordered = sorted(numbers, key=lambda nn: (nn == setup, nn))
    return ordered[0] if ordered else None


def _words(aa, nn, normal="SENSOR NORMAL"):
    """The STATUS column, in the console's own words."""
    if nn is None:
        return normal
    return (STATUS_TYPES.get(aa) or {}).get(nn, f"ALARM {nn}").upper()


def _status_report(handler, code, aa, devices, label_of, title, header, row):
    """One STATUS report, both formats, for any family of devices."""
    standing = _standing(handler.c, aa)
    if code[0].isupper():
        rows = [title, "", header] if title else [header]
        for number in devices:
            nn = _worst(aa, standing.get(number, []))
            rows.append(row(number, label_of(number), _words(aa, nn)))
        return handler._frame(code, SEP.join(rows))
    body = ""
    for number in devices:
        nn = _worst(aa, standing.get(number, []))
        body += f"{number:02d}" + _status_value(aa, nn)
    return handler._frame(code, body)


def _history_report(handler, code, aa, devices, label_of, title,
                    header, row, indent, column, count="%02d"):
    """One ALARM HISTORY report, both formats.

    Display format is the device, then a line per incident carrying the date
    and time it happened and what happened, which is how every one of these
    reports is printed:

        SENSOR     LOCATION
                1  2 WIRE CL SENSOR #1
                   FEB 12, 1990 11:32 AM            FUEL ALARM
    """
    if code[0].isupper():
        rows = [title, "", header] if title else [header]
        for number in devices:
            rows.append(row(number, label_of(number)).rstrip())
            for one in _incidents(handler.c, aa, number):
                rows.append(f"{'':{indent}s}"
                            + f"{_when(one['at']):<{column - indent}s}"
                            + _words(aa, one["nn"]))
        return handler._frame(code, SEP.join(rows))
    body = ""
    for number in devices:
        found = _incidents(handler.c, aa, number)
        body += f"{number:02d}" + count % len(found)
        body += "".join(one["at"] + _status_value(aa, one["nn"])
                        for one in found)
    return handler._frame(code, body)


def _sensor_rows():
    """The column layout every sensor status and history report prints in.

    "SENSOR     LOCATION              STATUS" over "        1  LIQUID # 1
    SENSOR NORMAL": the number is right aligned in nine, the location starts
    at column 11 and the status at column 33.
    """
    def status_row(number, label, words):
        return f"{number:9d}  {label:<22.22s}{words}"

    def history_row(number, label):
        return f"{number:9d}  {label:<22.22s}"

    return status_row, history_row


# ---------------------------------------------------------------------------
# The smart sensors, which are a different card and a different alarm list
# ---------------------------------------------------------------------------
# "TTTT - Smart Sensor Type: 0001=Air Flow Meter, 0002=Vapor Pressure,
# 0008=Mag Sensor, 0009=Vac Sensor, 0010=Atmospheric Sensor". The console
# numbers the same list differently at S723, SMART SENSOR CATEGORY, so the
# two numberings have to be mapped rather than assumed equal.
SMART_TYPE = {
    "01": ("0001", "AIR FLOW METER"),
    "02": ("0002", "VAPOR PRESSURE"),
    "03": ("0008", "MAG SENSOR"),
    "04": ("0009", "VAC SENSOR"),
    "05": ("0010", "ATMOSPHERIC SENSOR"),
}
SMART_UNKNOWN = ("0000", "UNKNOWN")

# The three categories with a diagnostic report of their own
MAG, VAC, ATMP = "03", "04", "05"


def _smart_kind(console, number):
    """Which of the smart sensors this one is, as S723 holds it."""
    return console.sensor_type("smart", number)


def _smart_devices(console, dev, want=None):
    """The smart sensors a command is asking about, of one category or all.

    IB3300 is the MAG SENSOR diagnostic: a console with a vacuum sensor on
    position 2 has nothing to say about position 2 under that code, so the
    all-devices form of a per-category report walks only its own category.
    """
    numbers = _devices(console, "smart", "721", dev)
    if want is None:
        return numbers
    return [n for n in numbers if _smart_kind(console, n) == want]


def _smart_serial(console, number):
    """The serial number the sensor reports, which the panel shows too."""
    return int(_number(console.diag_reading("ss_serial", number)))


def _smart_model(console, number):
    """"MODEL 101": the model number a smart sensor answers with.

    Nothing derives it, so it is generated the way readings.py generates
    anything else: stable per sensor, near the manual's own sample.
    """
    return readings.integer(100, 999, "ssmodel", number)


def _mag_values(console, number):
    """The six readings the MAG sensor diagnostic prints, in the manual's
    order: total height, fuel height, water height, install position, fluid
    temperature, board temperature."""
    tokens = ("ss_total_ht", "ss_fuel_ht", "ss_water_ht", "ss_install",
              "ss_fluid_temp", "ss_board_temp")
    return [_number(console.diag_reading(t, number)) for t in tokens]


def _vac_pressures(console, number):
    """(compensated, uncompensated) PSI on a vacuum sensor.

    A sump under vacuum sits near the -9 psi of the manual's own sample; one
    that has lost it is above -1 psi, which is the console's own threshold
    for the No Vacuum Alarm. The uncompensated reading is the same sensor
    before the atmospheric correction, so it sits a little away from it.
    """
    lost = console.sensor_state.get(("smart", str(number))) == "novacuum"
    band = (-0.9, -0.2) if lost else (-9.5, -8.5)
    compensated = readings.wander(console, band[0], band[1], "vac", number,
                                  swing=0.15, period=1500.0)
    offset = readings.fixed(-0.20, 0.20, "vacoffset", number)
    return compensated, compensated + offset


def _evacuations(console, number, count=5):
    """[(when, seconds)] for the last few evacuations of a vacuum sensor.

    The console keeps this log in the sensor and this bench does not drive
    one, so the events are generated: an evacuation every six hours, each
    keyed on its own timestamp so that once an event has happened it keeps
    the duration it had.
    """
    period = 6 * 3600.0
    now = time.mktime(console.now())
    latest = now - (now % period)
    return [(latest - i * period,
             readings.fixed(40.0, 180.0, "evac", number,
                            int(latest - i * period)))
            for i in range(count)]


def _channels(console, number):
    """The values a smart sensor returned on its last sample.

    "Values are in ASCII Hex IEEE float format", and the manual draws the
    grid with X's in it because what the channels MEAN is the sensor's
    business. Where this console already knows what the sensor reads, the
    last sample is those readings, because that is what a last sample is: a
    MAG sensor's channels are the six its diagnostic prints and a vacuum
    sensor's are its two pressures. Anything else is generated stably.
    """
    kind = _smart_kind(console, number)
    if kind == MAG:
        return _mag_values(console, number)
    if kind == VAC:
        return list(_vac_pressures(console, number))
    if kind == ATMP:
        return [_number(console.diag_reading("ss_atm", number))]
    return [readings.wander(console, 0.0, 100.0, "sschan", number, i)
            for i in range(3)]


def _channel_grid(values):
    """The ten-across table the last sample report draws them in."""
    rows = ["          " + " ".join(f"{i:8d}" for i in range(10))]
    for start in range(0, max(len(values), 1), 10):
        rows.append(f"{start:02d} " + " ".join(
            _float(v) for v in values[start:start + 10]))
    return rows


# Which of the constants each kind of sensor holds are whole numbers rather
# than floats: "vvvvvvvv - Number of Floats (1 or 2) (Hex)", "VVVVVVVV -
# Temperature enabled (0 or 1) (Hex)", "vvvvvvvv - Install Position enabled
# (0 or 1) (Hex)", "vvvvvvvv - Software Version (Hex)", and the model number
# itself, which is a number the sensor was stamped with and not a measurement.
CONSTANT_INTS = {"03": {0, 5, 6, 7}, "04": {0}, "05": {0, 1}}


def _mag_constants(console, number):
    """The eight constants a MAG sensor holds, in B36's own order.

    "NN=08 for Mag Sensors": model number, sensor length, gradient, minimum
    and maximum threshold, number of floats, and whether temperature and
    install position are enabled. The thresholds are the ends of the sensor,
    which is what the manual's sample shows, 0.0 and the 24 inch length.
    """
    length = readings.fixed(18.0, 36.0, "sslength", number)
    return [float(_smart_model(console, number)), length,
            readings.wander(console, 350.0, 370.0, "ssgrad", number,
                            swing=0.05),
            0.0, length, 2.0, 1.0, 1.0]


def _vac_constants(console, number):
    """"NN=03 for Vacuum Sensors": model number, calibration slope, offset."""
    return [float(_smart_model(console, number)),
            readings.fixed(0.95, 1.05, "vacslope", number),
            readings.fixed(-0.10, 0.10, "vacoffset2", number)]


def _atmp_constants(console, number):
    """"NN=04 for Atmospheric Pressure Sensors": model, software version,
    calibration slope, offset."""
    return [float(_smart_model(console, number)),
            float(readings.integer(1, 9, "atmpsw", number)),
            readings.fixed(0.95, 1.05, "atmpslope", number),
            readings.fixed(-0.10, 0.10, "atmpoffset", number)]


# ---------------------------------------------------------------------------
# The pump relay monitor
# ---------------------------------------------------------------------------
# S7C6, the device this monitor is watching: "00 none 11 relay 15 pump sense
# 16 VLLD 21 PLLD 26 WPLLD" over a two digit device number. The setup manual
# lists the same set, "the device code, number, and label of the controlling
# relay (e.g., Pump Sense, PLLD, WPLLD, VLLD, Pump Control Output - I/O Combo
# or 4-Relay)", and each one is drawn with its own Table 29-1 device letter.
MONITORED = {"11": ("R", "807"), "15": ("S", None), "16": ("P", "760"),
             "21": ("Q", "782"), "26": ("W", "7A2")}

# "If Stuck Delay, select from 5 to 600 seconds (60 is default). If Max Run
# Time, select from 1 to 24 hours (8 is default)."
STUCK_DELAY = 60.0


def _monitored(console, number):
    """(kind, device) the monitor is watching, from S7C6."""
    raw = (console.values.get(f"S7C6{number:02d}") or "").strip()
    body = raw[2:] if len(raw) > 4 else raw
    if len(body) < 4 or not body.isdigit() or body[:2] == "00":
        return "", 0
    return body[:2], int(body[2:4])


def _pump_out(console, kind, device):
    """The PUMP (OUT) column: is the console calling for that pump?"""
    if kind == "11":
        return bool(console.relays.get(device))
    if kind == "15":
        return console.pump_state(device) == "ON"
    if kind in ("16", "21", "26"):
        line = {"16": "vlld", "21": "plld", "26": "wplld"}[kind]
        return bool(console.lines.line(line, device).pump)
    return False


def _relay_in(console, number):
    """The PUMP RELAY (IN) column: what the monitor's own input reads.

    Same state the panel's PUMP RELAY MONITOR STATUS screen shows and the
    printer prints, so the report, the screen and the paper agree.
    """
    return bool(console.relays.get(number))


def _monitor_text(console, number):
    """"Q 1: OFF", the monitored device and what it is doing."""
    kind, device = _monitored(console, number)
    if not kind:
        return "NONE"
    letter, _label_code = MONITORED[kind]
    state = "ON" if _relay_in(console, number) else "OFF"
    return f"{letter} {device}: {state}"


def _stuck_seconds(console, number):
    """How long the relay has been stuck closed with the pump told to stop.

    "if the pump continues to run after it is instructed to turn off, for
    longer than a 5 - 600 second selectable delay (Stuck Delay), an alarm is
    posted": so a monitor NOT in alarm has not been stuck for longer than its
    delay, and one that is has been stuck for the delay plus however long the
    alarm has stood, which the alarm history is the record of.
    """
    standing = [r for r in console.compute_alarms()
                if r[:2] == PUMPMON_AA and r[2:4] == "02"
                and r[4:6] == f"{number:02d}"]
    if not standing:
        return 0.0
    delay = console.limit("7C7", number) or STUCK_DELAY
    posted = _incidents(console, PUMPMON_AA, number)
    if not posted:
        return delay
    try:
        since = time.mktime(console.now()) - time.mktime(
            time.strptime(posted[0]["at"], "%y%m%d%H%M"))
    except ValueError:
        since = 0.0
    return delay + max(since, 0.0)


def _run_hours(console, number):
    """How long the pump this monitor watches has been running.

    "monitor the pump each time it switches on, and if it is still running
    after a 1 - 24 hour delay (Max Run Time delay), to post an alarm." The
    console keeps no separate pump run clock, so a pump that is running now
    has been running since the console came up, which is the clock the test
    needed warnings count from as well.
    """
    kind, device = _monitored(console, number)
    if not _pump_out(console, kind, device):
        return 0.0
    return console._uptime_hours()


# ---------------------------------------------------------------------------
# The external inputs and the output relays
# ---------------------------------------------------------------------------
# S80C, "External input type and orientation": 21 and 22 are the two
# generator orientations, which is what I403's own heading means by "Setup
# parameters determine whether an input is from a generator."
GENERATOR_TYPES = ("21", "22")

# "aaaa - Alarm type number: ... 0004=Generator Off, 0005=Generator On": the
# two extra values I403 has and I402 does not, which an input programmed as a
# generator reports in place of Input Normal and Input Alarm.
GENERATOR_VALUE = {"02": "0004", "03": "0005"}
GENERATOR_WORDS = {"02": "GENERATOR OFF", "03": "GENERATOR ON"}


def _is_generator(console, number):
    raw = (console.values.get(f"S80C{number:02d}") or "").strip()
    return raw[-2:] in GENERATOR_TYPES


def _relay_count(console):
    """How many output relays the cage carries.

    Two cards can serve them, "OUTPUT RELAY SETUP": ("io", "relay"), and the
    console offers as many as the bigger of the two provides.
    """
    return max(console.capacity("relay"), console.capacity("io"))


def _relay_closed(console, number):
    """Are the contacts closed?

    "0001=Relay Open, 0002=Relay Closed" is the CONTACT, not the coil, and
    S809 says which way round that is: a NORMALLY CLOSED relay reads closed
    when nothing has energised it.
    """
    energised = bool(console.relays.get(number))
    raw = (console.values.get(f"S809{number:02d}") or "").strip()
    if raw[-1:] == "2":
        return not energised
    return energised


# ---------------------------------------------------------------------------
# The diagnostic reports
# ---------------------------------------------------------------------------
# What each module's own typical response prints for its reference channels,
# which is the only figure the manual gives for them.
#           code    high     low
REFERENCE = {"B01": (1072.0, 193.0),
             "B06": (1080.0, 208.0),
             "B11": (5440.0, 930.0),
             "B21": (1086.0, 215.0),
             "B41": (7823.0, 1815.0),
             "B46": (32000.0, 8900.0),
             "B4B": (32000.0, 8900.0)}

# Which family each resistance diagnostic reads, how many channels it reads,
# whether the second channel has reference channels of its own, and the title
# the report prints.
#             code     module    channels  own refs  title
DIAGNOSTIC = {"B01": ("liquid", 1, False, "LIQUID"),
              "B06": ("vapor", 2, False, "VAPOR"),
              "B11": ("gw", 2, False, "GROUNDWATER"),
              "B41": ("2wire", 1, False, "2 WIRE CL"),
              "B46": ("3wire", 2, True, "3 WIRE CL"),
              "B4B": ("universal", 2, True, "UNIVERSAL")}


def _sensor_state(console, module, number):
    """The state the sensor is in, as the console reads it.

    A sensor cannot report a condition its own type cannot sense, so the
    console's own gate decides what band the resistance falls in.
    """
    state = console.sensor_state.get((module, str(number)), "normal")
    if state != "normal" and not console.sensor_alarm_allowed(module, number,
                                                              state):
        return "normal"
    return state


def _sensor_pair(console, module, number, channel):
    """(last reading, current average) on one channel of one sensor.

    The average is the value the module has settled on, which is the band
    reading `readings.sensor_value` gives; the last reading is one A/D
    conversion of it, which is that value plus the sample to sample noise a
    single conversion has.
    """
    average = readings.sensor_value(console, module, number,
                                    _sensor_state(console, module, number),
                                    channel)
    noise = readings.wander(console, -0.01, 0.01, "sample", module, number,
                            channel, swing=1.0, period=30.0)
    return average * (1.0 + noise), average


def _diagnostic_report(handler, code, tok, dev):
    """B01, B06, B11, B41, B46 and B4B, which are one report six times over.

    "1. Sample counter, 2. High Reference Channel, 3. Low Reference Channel,
    4. Liquid Channel Last Reading, 5. Liquid Channel Average Reading", and
    a module that reads two channels appends the second pair. The 3-wire and
    universal modules carry reference channels for both, so those two send
    nine values where the others send five or seven.
    """
    c = handler.c
    module, channels, own_refs, title = DIAGNOSTIC[tok]
    aa, config, label_code, _title, pattern = FAMILY[module]
    del aa, _title
    devices = _devices(c, module, config, dev)
    high, low = REFERENCE[tok]
    rows = [f"{title} DIAGNOSTIC REPORT", ""]
    rows += _diag_header(channels)
    body = ""
    for number in devices:
        counter = _sample_counter(c)
        hi1 = _reference(c, tok, number, high, "high")
        lo1 = _reference(c, tok, number, low, "low")
        last1, avg1 = _sensor_pair(c, module, number, 1)
        values = [counter, hi1, lo1, last1, avg1]
        shown = [last1]
        if channels == 2:
            last2, avg2 = _sensor_pair(c, module, number, 2)
            if own_refs:
                values += [_reference(c, tok, number, high, "high2"),
                           _reference(c, tok, number, low, "low2")]
            values += [last2, avg2]
            shown.append(last2)
        rows.append(f"{number:6d}{counter:9.0f}{hi1:11.0f}{lo1:11.0f}"
                    + "".join(f"{v:15.0f}" for v in shown))
        body += f"{number:02d}" + _floats(values)
    if code[0].isupper():
        return handler._frame(code, SEP.join(rows))
    return handler._frame(code, body)


def _diag_header(channels):
    """"SAMPLE COUNTER  HIGH REF  LOW REF  VALUE": two lines, because the
    console stacks the two word headings."""
    names = ["VALUE"] if channels == 1 else ["VALUE1", "VALUE2"]
    return [f"{'':6s}{'SAMPLE':>9s}{'HIGH':>11s}{'LOW':>11s}",
            f"{'SENSOR':<6s}{'COUNTER':>9s}{'REF':>11s}{'REF':>11s}"
            + "".join(f"{n:>15s}" for n in names)]


# ---------------------------------------------------------------------------
def handle(handler, tok, dev, code, data):
    """Answer one function code, or return None if it is not ours."""
    del data
    if tok not in CODES:
        return None
    c = handler.c
    display = code[0].isupper()

    # ---- the sensor families: status, history, resistance ------------------
    module = STATUS_CODE.get(tok) or HISTORY_CODE.get(tok)
    if module is None and tok in DIAGNOSTIC:
        module = DIAGNOSTIC[tok][0]
    if module is not None:
        if not c.has(module):
            return handler._nine(code), f"no {module} sensor module fitted"
        if tok in DIAGNOSTIC:
            return (_diagnostic_report(handler, code, tok, dev),
                    "sensor diagnostic")
        aa, config, label_code, title, pattern = FAMILY[module]
        devices = _devices(c, module, config, dev)
        status_row, history_row = _sensor_rows()

        def label_of(number):
            return _label(c, label_code, number, pattern)

        if tok in STATUS_CODE:
            return (_status_report(
                handler, code, aa, devices, label_of,
                f"{title} STATUS REPORT",
                f"{'SENSOR':<11s}{'LOCATION':<22s}STATUS",
                status_row), "sensor status")
        return (_history_report(
            handler, code, aa, devices, label_of,
            f"{title} ALARM HISTORY REPORT",
            f"{'SENSOR':<11s}LOCATION", history_row, 11, 45),
            "sensor alarm history")

    # ---- the smart sensors -------------------------------------------------
    if tok in ("315", "316", "333", "B33", "B34", "B35", "B36", "B37",
               "B38", "B39"):
        if not c.has("smart"):
            return handler._nine(code), "no smart sensor module fitted"
        return _smart(handler, tok, dev, code, display)

    # ---- the pump relay monitor --------------------------------------------
    if tok in ("322", "323", "B72"):
        if not c.has("pumpmon"):
            return handler._nine(code), "no pump relay monitor fitted"
        return _pumpmon(handler, tok, dev, code, display)

    # ---- the vapor concentration, which is the vapor module's own ----------
    if tok == "B07":
        if not c.has("vapor"):
            return handler._nine(code), "no vapor sensor module fitted"
        devices = _devices(c, "vapor", "706", dev)
        rows = ["VAPOR DIAGNOSTIC REPORT - VAPOR CONCENTRATION", "",
                f"{'SENSOR':<14s}PPM"]
        body = ""
        for number in devices:
            # "1. Vapor concentration (ppm)", which is the second channel of
            # the vapor module read as a concentration rather than a
            # resistance, and the same number the panel's screen shows
            ppm = _number(c.diag_reading("sensor_ppm", number))
            rows.append(f"{number:12d}  {ppm:.0f}")
            body += f"{number:02d}" + _floats([ppm])
        if display:
            return handler._frame(code, SEP.join(rows)), "vapor concentration"
        return handler._frame(code, body), "vapor concentration"

    # ---- the ground temperature sensor, which is on the groundwater card ---
    if tok == "B21":
        if not c.has("gw"):
            return handler._nine(code), "no groundwater sensor module fitted"
        devices = _devices(c, "gw", "711", dev)
        high, low = REFERENCE["B21"]
        rows = ["GROUNDTEMP DIAGNOSTIC REPORT", ""] + _diag_header(1)
        body = ""
        for number in devices:
            counter = _sample_counter(c)
            hi = _reference(c, tok, number, high, "high")
            lo = _reference(c, tok, number, low, "low")
            # "Value = resistance measured by thermistor", which is what the
            # panel's own GROUND TEMP screen reads out
            last = _tail(c.diag_reading("sensor_groundtemp", number))
            average = readings.fixed(480.0, 620.0, "gt", number)
            rows.append(f"{number:6d}{counter:9.0f}{hi:11.0f}{lo:11.0f}"
                        f"{last:15.0f}")
            body += f"{number:02d}" + _floats([counter, hi, lo, last, average])
        note = "groundtemp diagnostic"
        if display:
            return handler._frame(code, SEP.join(rows)), note
        return handler._frame(code, body), note

    # ---- the external inputs and the output relays -------------------------
    if tok in ("401", "402", "403"):
        if not c.has("io"):
            return handler._nine(code), "no input module fitted"
        return _inputs(handler, tok, dev, code)
    if tok == "406":
        if not (c.has("relay") or c.has("io")):
            return handler._nine(code), "no relay module fitted"
        devices = _devices(c, "relay", "806", dev)
        if not devices and dev == "00":
            devices = list(range(1, _relay_count(c) + 1))
        rows = [f"{'RELAY':<8s}{'LOCATION':<25s}STATUS"]
        body = ""
        for number in devices:
            closed = _relay_closed(c, number)
            label = _label(c, "807", number, RELAY_LABEL)
            rows.append(f"{number:6d}  {label:<25.25s}"
                        + ("CLOSED" if closed else "OPEN"))
            body += f"{number:02d}" + ("0002" if closed else "0001")
        if display:
            return handler._frame(code, SEP.join(rows)), "relay status"
        return handler._frame(code, body), "relay status"

    return None


def _inputs(handler, tok, dev, code):
    """I401, I402 and I403: what the external inputs are doing, and did."""
    c = handler.c
    devices = _devices(c, "io", "801", dev)

    def label_of(number):
        return _label(c, "802", number, INPUT_LABEL)

    if tok == "401":
        standing = _standing(c, INPUT_AA)
        rows = [f"{'INPUT':<11s}{'LOCATION':<22s}STATUS"]
        body = ""
        for number in devices:
            nn = _worst(INPUT_AA, standing.get(number, []))
            # "STATUS ... OFF": an external input is a contact, and the
            # console words the two states it can be in the way a contact
            # reads rather than the way its alarm number does
            words = {None: "OFF", "02": "OFF", "03": "ON"}.get(
                nn, _words(INPUT_AA, nn, "OFF"))
            rows.append(f"{number:8d}   {label_of(number):<22.22s}{words}")
            body += f"{number:02d}" + _status_value(INPUT_AA, nn or "02")
        if code[0].isupper():
            return handler._frame(code, SEP.join(rows)), "input status"
        return handler._frame(code, body), "input status"

    generator = tok == "403"
    title = "INPUT / GENERATOR ALARM HISTORY REPORT" if generator else ""

    def row(number, label):
        return f"{number:6d}     {label:<22.22s}"

    header = f"{'INPUT':<11s}LOCATION"
    if code[0].isupper():
        rows = [title, "", header] if title else [header]
        for number in devices:
            rows.append(row(number, label_of(number)))
            rows[-1] = rows[-1].rstrip()
            for one in _incidents(c, INPUT_AA, number):
                words = _words(INPUT_AA, one["nn"])
                if generator and _is_generator(c, number):
                    words = GENERATOR_WORDS.get(one["nn"], words)
                rows.append(f"{'':11s}{_when(one['at']):<22s}{words}")
        return handler._frame(code, SEP.join(rows)), "input alarm history"
    body = ""
    for number in devices:
        found = _incidents(c, INPUT_AA, number)
        body += f"{number:02d}{len(found):02X}"
        for one in found:
            value = _status_value(INPUT_AA, one["nn"])
            if generator and _is_generator(c, number):
                value = GENERATOR_VALUE.get(one["nn"], value)
            body += one["at"] + value
    return handler._frame(code, body), "input alarm history"


def _pumpmon(handler, tok, dev, code, display):
    """I322, I323 and IB72: the monitor that watches a pump's contactor."""
    c = handler.c
    devices = _devices(c, "pumpmon", "7C4", dev)

    def label_of(number):
        return _label(c, "7C5", number, "PUMP RELAY {n}")

    if tok == "323":
        return (_history_report(
            handler, code, PUMPMON_AA, devices, label_of,
            "PUMP RELAY MONITOR ALARM HISTORY REPORT",
            "DEVICE LABEL",
            lambda number, label: label,
            14, 45, "%02X"), "pump relay alarm history")

    standing = _standing(c, PUMPMON_AA)
    head = (f"{'DEVICE':<11s}{'LABEL':<22s}{'PUMP':<12s}"
            + (f"{'PUMP RELAY':<13s}STATUS" if tok == "322"
               else f"{'PUMP RELAY':<12s}{'STUCK':<9s}RUN"))
    second = (f"{'':33s}{'(OUT)':<12s}(IN)" if tok == "322"
              else f"{'':33s}{'(OUT)':<12s}{'(IN)':<12s}{'RELAY':<9s}TIME")
    rows = ["PUMP RELAY MONITOR STATUS REPORT" if tok == "322"
            else "PUMP RELAY MONITOR DIAGNOSTIC", "", head, second]
    body = ""
    for number in devices:
        kind, device = _monitored(c, number)
        pump = _pump_out(c, kind, device)
        relay = _relay_in(c, number)
        a = "1" if pump else "0"
        b = "1" if relay else "0"
        if tok == "322":
            nn = _worst(PUMPMON_AA, standing.get(number, []))
            rows.append(f"{number:9d}  {label_of(number):<22.22s}"
                        f"{'ON' if pump else 'OFF':<12s}"
                        f"{_monitor_text(c, number):<13s}"
                        + _words(PUMPMON_AA, nn, "NORMAL"))
            body += (f"{number:02d}{a}{b}"
                     + _status_value(PUMPMON_AA, nn))
            continue
        stuck = _stuck_seconds(c, number)
        hours = _run_hours(c, number)
        rows.append(f"{number:9d}  {label_of(number):<22.22s}"
                    f"{'ON' if pump else 'OFF':<12s}"
                    f"{_monitor_text(c, number):<12s}"
                    f"{f'{stuck:.0f} SEC':<9s}"
                    f"{int(hours):02d}:{int(hours % 1.0 * 60):02d}")
        body += f"{number:02d}{a}{b}" + _floats([stuck, hours])
    note = "pump relay status" if tok == "322" else "pump relay diagnostic"
    if display:
        return handler._frame(code, SEP.join(rows)), note
    return handler._frame(code, body), note


def _smart(handler, tok, dev, code, display):
    """The smart sensor reports, which are a card that talks back."""
    c = handler.c

    def label_of(number):
        return _label(c, "722", number, "SMART SENSOR {n}")

    def head(number):
        """"s 1: SUMP 1", the way a smart sensor screen heads itself."""
        return f"s {number}:{label_of(number)}"

    if tok == "315":
        return (_status_report(
            handler, code, SMART_AA, _smart_devices(c, dev), label_of,
            "SMART SENSOR STATUS REPORT",
            f"{'SENSOR':<11s}{'LOCATION':<22s}STATUS",
            lambda n, label, words: f"{n:9d}  {label:<22.22s}{words}"),
            "smart sensor status")
    if tok == "316":
        return (_history_report(
            handler, code, SMART_AA, _smart_devices(c, dev), label_of,
            "SMART SENSOR ALARM HISTORY REPORT",
            f"{'SENSOR':<11s}LOCATION",
            lambda n, label: f"{n:9d}  {label:<22.22s}", 11, 45),
            "smart sensor alarm history")

    if tok == "333":
        # "nnn - Number of Events to Follow (Decimal)". The console keeps no
        # install log of its own, so an install event is the console learning
        # the sensor was there, which on this bench is the cold start it
        # counts its test needed warnings from.
        devices = _smart_devices(c, dev)
        began = c._commissioned or time.mktime(c.now())
        rows = ["SMART SENSOR INSTALL LOG", "",
                f"{'DATE':<18s}{'SENSOR':>6s}    {'SERIAL NUMBER':<17s}TYPE"]
        body = f"{len(devices):03d}"
        for number in devices:
            when = began - readings.fixed(0.0, 7200.0, "install", number)
            packed = time.strftime("%y%m%d%H%M", time.localtime(when))
            serial = _smart_serial(c, number)
            type_code, type_name = SMART_TYPE.get(_smart_kind(c, number),
                                                  SMART_UNKNOWN)
            shown = time.strftime("%m-%d-%y %H:%M:%S", time.localtime(when))
            rows.append(f"{shown:<18s}{number:6d}    "
                        f"{serial:<17d}{type_name}")
            # "ffff - Smart Sensor Model Number": four characters in the
            # manual's own message template, whatever its note calls it
            body += packed + f"{number:02d}" + _float(serial) + type_code
        if display:
            return handler._frame(code, SEP.join(rows)), "smart install log"
        return handler._frame(code, body), "smart install log"

    if tok == "B33":
        # "1. Total Height, 2. Fuel Height, 3. Water Height, 4. Install
        # Position, 5. Fuel Temperature, 6. Board Temperature"
        names = ("TOTAL HT", "FUEL HT", "WATER HT", "INSTALL POS",
                 "FLUID TEMP", "BOARD TEMP")
        units = ("IN.", "IN.", "IN.", "IN.", "F", "F")
        rows = ["MAG SENSOR DIAGNOSTIC REPORT", ""]
        body = ""
        for number in _smart_devices(c, dev, MAG):
            values = _mag_values(c, number)
            rows += [head(number), ""]
            rows += [f" {name:<12s}{value:6.1f} {unit}"
                     for name, value, unit in zip(names, values, units)]
            rows.append("")
            body += f"{number:02d}" + _floats(values)
        note = "mag sensor diagnostic"
        if display:
            return handler._frame(code, SEP.join(rows)), note
        return handler._frame(code, body), note

    if tok == "B34":
        rows = ["SMART SENSOR CHANNEL DATA: LAST SAMPLE", ""]
        body = ""
        for number in _smart_devices(c, dev):
            type_code, type_name = SMART_TYPE.get(_smart_kind(c, number),
                                                  SMART_UNKNOWN)
            values = _channels(c, number)
            rows += [head(number), type_name,
                     f"SERIAL NUMBER: {_smart_serial(c, number)}", ""]
            rows += _channel_grid(values)
            rows.append("")
            body += (f"{number:02d}{type_code}{len(values):02X}"
                     + "".join(_float(v) for v in values))
        if display:
            return handler._frame(code, SEP.join(rows)), "smart last sample"
        return handler._frame(code, body), "smart last sample"

    if tok == "B35":
        # "nn - Number of 8-byte values to follow": the model, the serial
        # number, the date code and the protocol version, all four of them
        # numbers the sensor itself answers with
        rows = ["SMART SENSOR SERIAL NUMBER", "",
                "SENSOR LABEL                TYPE"
                "             SERIAL NUMBER DATE CODE"]
        body = ""
        for number in _smart_devices(c, dev):
            type_code, type_name = SMART_TYPE.get(_smart_kind(c, number),
                                                  SMART_UNKNOWN)
            model = _smart_model(c, number)
            serial = _smart_serial(c, number)
            date_code = readings.integer(10000, 60000, "ssdate", number)
            protocol = readings.integer(1, 9, "ssproto", number)
            rows.append(f"{number:2d} {label_of(number):<20.20s} "
                        f"{type_code[1:]}-{type_name:<22.22s}"
                        f"{serial:>6d}  {date_code:>6d}")
            body += (f"{number:02d}04{model:08X}{serial:08X}"
                     f"{date_code:08X}{protocol:08X}")
        if display:
            return handler._frame(code, SEP.join(rows)), "smart serial numbers"
        return handler._frame(code, body), "smart serial numbers"

    if tok == "B36":
        return _smart_constants(handler, dev, code, display, head, label_of)

    if tok == "B37":
        rows = ["ATM P SENSOR DIAGNOSTIC REPORT", ""]
        body = ""
        for number in _smart_devices(c, dev, ATMP):
            serial = _smart_serial(c, number)
            psi = _number(c.diag_reading("ss_atm", number))
            rows += [head(number), "",
                     "ATM P SENSOR",
                     f"{'SERIAL NUMBER':<16s}{serial:>10d}",
                     f"{'ATM PRESSURE':<16s}{psi:>10.3f} PSI", ""]
            body += f"{number:02d}{serial:08X}" + _floats([psi])
        note = "atm sensor diagnostic"
        if display:
            return handler._frame(code, SEP.join(rows)), note
        return handler._frame(code, body), note

    if tok == "B38":
        return _vac_diagnostic(handler, dev, code, display, head)

    if tok == "B39":
        rows = ["VAC SENSOR EVACUATION DIAGNOSTIC REPORT", ""]
        body = ""
        for number in _smart_devices(c, dev, VAC):
            events = _evacuations(c, number)
            rows += [head(number), "",
                     f"{'START DATE/TIME':<22s}DURATION",
                     f"{'':22s}HH:MM:SS"]
            for when, seconds in events:
                rows.append(time.strftime("%m-%d-%y %H:%M:%S",
                                          time.localtime(when))
                            + f"{int(seconds) // 3600:7d}"
                            + f":{int(seconds) % 3600 // 60:02d}"
                            + f":{int(seconds) % 60:02d}")
            rows.append("")
            body += f"{number:02d}{len(events):02d}"
            body += "".join(time.strftime("%y%m%d%H%M", time.localtime(when))
                            + _float(seconds) for when, seconds in events)
        if display:
            return handler._frame(code, SEP.join(rows)), "vac evacuation log"
        return handler._frame(code, body), "vac evacuation log"

    return None


def _smart_constants(handler, dev, code, display, head, label_of):
    """IB36: the constants a smart sensor was built with.

    Three shapes, one per kind of sensor, and the manual gives each its own
    field count: "NN=08 for Mag Sensors", "NN=03 for Vacuum Sensors",
    "NN=04 for Atmospheric Pressure Sensors".
    """
    del label_of
    c = handler.c
    rows = ["SMART SENSOR CONSTANTS DIAGNOSTIC", ""]
    body = ""
    for number in _smart_devices(c, dev):
        kind = _smart_kind(c, number)
        _type_code, type_name = SMART_TYPE.get(kind, SMART_UNKNOWN)
        serial = _smart_serial(c, number)
        rows += [head(number), "", type_name,
                 f"{'SERIAL NUMBER':<16s}{serial:>10d}"]
        if kind == MAG:
            values = _mag_constants(c, number)
            names = ("MODEL", "LENGTH", "GRADIENT", "MIN THRESHOLD",
                     "MAX THRESHOLD", "NUM FLOATS", "TEMPERATURE",
                     "INSTALL POS")
            shown = [f"{values[0]:.0f}", f"{values[1]:.1f}",
                     f"{values[2]:.3f}", f"{values[3]:.1f}",
                     f"{values[4]:.1f}", f"{values[5]:.0f}",
                     "YES" if values[6] else "NO",
                     "YES" if values[7] else "NO"]
        elif kind == VAC:
            values = _vac_constants(c, number)
            names = ("MODEL", "CAL SLOPE", "CAL OFFSET")
            shown = [f"{values[0]:.0f}", f"{values[1]:.3f}",
                     f"{values[2]:.3f}"]
        elif kind == ATMP:
            values = _atmp_constants(c, number)
            names = ("MODEL", "SOFTWARE VER", "CAL SLOPE", "CAL OFFSET")
            shown = [f"{values[0]:.0f}", f"{values[1]:.0f}",
                     f"{values[2]:.3f}", f"{values[3]:.3f}"]
        else:
            # a sensor the console has not identified holds no constants
            values, names, shown = [], (), []
        rows += [f"{name:<16s}{text:>10s}"
                 for name, text in zip(names, shown)]
        rows.append("")
        whole = CONSTANT_INTS.get(kind, set())
        body += f"{number:02d}{len(values):02X}"
        body += "".join(f"{int(v):08X}" if i in whole else _float(v)
                        for i, v in enumerate(values))
    if display:
        return handler._frame(code, SEP.join(rows)), "smart sensor constants"
    return handler._frame(code, body), "smart sensor constants"


# "e - Evacuation State: 0=Vacuum Ok, 1=Evacuation Pending, 2=Evacuation
# Active, 3=Evacuation Pending Manual, 4=Evacuation Active Manual,
# 5=No Vacuum, 6=Evacuation Hold"
EVAC_WORDS = {"0": "VACUUM OK", "1": "EVACUATION PENDING",
              "2": "EVACUATION ACTIVE", "3": "EVACUATION PENDING MANUAL",
              "4": "EVACUATION ACTIVE MANUAL", "5": "NO VACUUM",
              "6": "EVACUATION HOLD"}
# "F - Fluid Status: 0=Normal, 1=Fault, 2=Fluid"
FLUID_WORDS = {"0": "NORMAL", "1": "FAULT", "2": "FLUID"}
# "c - Vacuum Control Valve State: 0=Closed, 1=Open, 2=Fault"
VCV_WORDS = {"0": "CLOSED", "1": "OPEN", "2": "FAULT"}
# "ffff - Sensor Fault Bits: Bit 1=Fluid Sensor Fault, Bit 2=Pressure Sensor
# Fault, Bit 3=Relief Valve Fault, Bit 4=VCV Fault"
FAULT_BITS = ((1, "FLUID SENSOR FAULT"), (2, "PRESSURE SENSOR FAULT"),
              (4, "RELIEF VALVE FAULT"), (8, "VCV FAULT"))


def _vac_diagnostic(handler, dev, code, display, head):
    """IB38: everything a vacuum sensor has to say about its sump."""
    c = handler.c
    rows = ["VAC SENSOR DIAGNOSTIC REPORT", ""]
    body = ""
    for number in _smart_devices(c, dev, VAC):
        state = c.sensor_state.get(("smart", str(number)), "normal")
        serial = _smart_serial(c, number)
        compensated, uncompensated = _vac_pressures(c, number)
        evac = "5" if state == "novacuum" else "0"
        fluid = "1" if state in ("fault", "faultwarn") else (
            "2" if state == "high" else "0")
        # the valve only opens to pull the sump back down, so a sump that is
        # holding its vacuum is a valve sitting closed
        vcv = "1" if evac in ("2", "4") else "0"
        # the manual's own typical response has a relief valve fault on it,
        # which is the fault a sensor in a fault state reports here
        faults = 4 if fluid == "1" else 0
        rate = readings.wander(c, 0.05, 0.30, "vacrate", number, swing=0.3)
        minutes = readings.integer(600, 12000, "vacnovac", number)
        ratio = readings.fixed(3.0, 8.0, "vacratio", number)
        at_psi = readings.fixed(-5.0, -3.0, "vacratiopsi", number)
        rows += [head(number), "",
                 "VAC SENSOR",
                 f"{'SERIAL NUMBER':<16s}{serial:>10d}",
                 "COMPENSATED PRESSURE:",
                 f"{compensated:>21.3f} PSI",
                 "UNCOMPENSATED PRESSURE:",
                 f"{uncompensated:>21.3f} PSI",
                 "EVACUATION STATE:",
                 EVAC_WORDS[evac],
                 f"FLUID STATUS: {FLUID_WORDS[fluid]}",
                 f"VCV: {VCV_WORDS[vcv]}",
                 "LEAK RATE:",
                 time.strftime("%m-%d-%y %I:%M%p",
                               time.localtime(time.mktime(c.now()) - 3600))
                 + f"{rate:8.3f} GPH",
                 "TIME TO NO VAC:",
                 time.strftime("%m-%d-%y %I:%M%p",
                               time.localtime(time.mktime(c.now()) - 7200))
                 + f"{minutes // 60:5d}:{minutes % 60:02d} HHHH:MM",
                 "EVAC RATIO:" f"{ratio:.1f} @ {at_psi:.1f}PSI", ""]
        named = [name for bit, name in FAULT_BITS if faults & bit]
        if named:
            rows.append("SENSOR FAULTS:")
            rows += [f"   {name}" for name in named]
            rows.append("")
        body += (f"{number:02d}{serial:08X}{evac}{fluid}{vcv}1"
                 + _stamp(c, 1.0) + _float(rate) + "1"
                 + _stamp(c, 2.0) + f"{minutes:08X}" + "1"
                 + _stamp(c, 3.0) + _float(ratio) + _float(at_psi)
                 + f"{faults:04X}"
                 + _floats([compensated, uncompensated]))
    if display:
        return handler._frame(code, SEP.join(rows)), "vac sensor diagnostic"
    return handler._frame(code, body), "vac sensor diagnostic"
