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
from . import wiretables

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
    # in console.MODULES and the bench can fit one, so the family stays here
    # whole and its three codes are in CODES below -- a code this console
    # claims has to be a code it can serve, and with the card fittable it can.
    # No preset carries one, because nobody could buy one. See UNKNOWNS C3.
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
# p.153 sets the closing asterisk 12.01 points -- exactly two characters at
# this manual's 6.0 pitch -- after the number, where I401's `* EXTERNAL INPUT
# 1 *` on p.149 sets its own one character away. The number is left justified
# in a field two wide, so relay 10 fills it and relay 1 leaves the space.
RELAY_LABEL = "* RELAY {n:<2} *"

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

    `Console.sample_counter` is the counter; this is the float the wire
    sends. The panel used to hold its own hardcoded one, so the two
    disagreed. See FIDELITY L9.
    """
    return float(console.sample_counter())


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


# The column layout every sensor status and history report prints in, read
# off 576013-635 Rev AA's own samples rather than counted by hand: p.105,
# p.107, p.109, p.121, p.123 and p.125 all draw
#
#     SENSOR  LOCATION               STATUS
#          1  LIQUID # 1             SENSOR NORMAL
#
# with the number right aligned to column 5, the location at 8 and the status
# at 31. This console had them at 8, 11 and 33 -- three columns out on two of
# the three, which is what FIDELITY S5 counted and what a page settles. The
# alarm history is the same left edge, with its incident lines indented to
# the location column and the alarm words at 38 (p.106, p.111, p.122).
SENSOR_COLUMN = 6           # the device number, right aligned in six
LOCATION_COLUMN = 8
STATUS_COLUMN = 31
HISTORY_WORDS = 38

STATUS_HEADER = ("SENSOR".ljust(LOCATION_COLUMN)
                 + "LOCATION".ljust(STATUS_COLUMN - LOCATION_COLUMN) + "STATUS")
HISTORY_HEADER = "SENSOR".ljust(LOCATION_COLUMN) + "LOCATION"


def _sensor_rows(status_column=STATUS_COLUMN):
    """The row builders for one family of sensors.

    `status_column` is 21 rather than 31 on the smart sensors, which p.111
    draws narrower than the rest of the family and which is the one place
    this layout is not shared.
    """
    width = status_column - LOCATION_COLUMN

    def status_row(number, label, words):
        return (f"{number:{SENSOR_COLUMN}d}  "
                f"{label:<{width}.{width}s}{words}")

    def history_row(number, label):
        return f"{number:{SENSOR_COLUMN}d}  {label}"

    return status_row, history_row


# ---------------------------------------------------------------------------
# The smart sensors, which are a different card and a different alarm list
# ---------------------------------------------------------------------------
# "TTTT - Smart Sensor Type: 0001=Air Flow Meter, 0002=Vapor Pressure,
# 0008=Mag Sensor, 0009=Vac Sensor, 0010=Atmospheric Sensor". The console
# numbers the same list differently at S723, SMART SENSOR CATEGORY, so the
# two numberings have to be mapped rather than assumed equal.
#
# Category 08, the vapour valve, was missing. 723's own note ends
# "08=vapor valve  (Version 29)" and `consoledata.json` carries it, so the
# panel could hold a category the wire had no word for: a sensor programmed
# as a vapour valve came back `UNKNOWN` / `0000` from `I333`, `IB34`, `IB35`
# and `IB36`, on a console that answers `IB61` VAPOR VALVE DIAGNOSTIC and
# `IB62` for the same sensor. See FIDELITY L4.
#
# The NAME is certain and the CODE is inferred. B34's own TTTT list stops
# at 0010 and has no vapour valve in it -- it predates the category, which
# arrived in Version 29 -- but B62's does: "TT - Smart Sensor Type (Hex) ...
# 0E = Vapor Valve", the same field under a shorter name. `000E` is that
# code in B34's width. Nothing on this shelf prints a B34 row for a vapour
# valve, so what a console really sends there is worth checking against one.
SMART_TYPE = {
    "01": ("0001", "AIR FLOW METER"),
    "02": ("0002", "VAPOR PRESSURE"),
    "03": ("0008", "MAG SENSOR"),
    "04": ("0009", "VAC SENSOR"),
    "05": ("0010", "ATMOSPHERIC SENSOR"),
    "08": ("000E", "VAPOR VALVE"),
}
SMART_UNKNOWN = ("0000", "UNKNOWN")

# The two smart sensor categories ISD reads, and the letters its own index
# table puts in the middle of their serial numbers.
AIR_FLOW, VAPOR_PRESSURE = "01", "02"
ISD_TAG = {AIR_FLOW: "AF", VAPOR_PRESSURE: "PS"}


def isd_serial(console, number):
    """The ten-character serial V43's index table answers with.

    Five digits, the two letters that say which kind of ISD device it is,
    and the position. 577013-800 Rev P Figure 7 draws exactly ten of them
    on the panel -- `SN#: (10 char)` over the AIRFLOW METER SELECT and
    PRESSURE SENSOR SELECT screens -- and this was the wire's alone, built
    inline where nothing else could reach it, so the panel drew `SN#:` with
    nothing after it on a console that could answer the same question over
    the port. Panel and port do not disagree about a device's serial.
    """
    kind = console.sensor_type("smart", number) or ""
    if not kind:
        # A position nobody has told the console about is not a device, and
        # a device that is not there has no serial. The panel walks these
        # -- Setup Mode is where a position gets switched on -- so it has to
        # be able to draw one that is empty.
        return ""
    return (f"{readings.digits(5, 'isdsn', number)}"
            f"{ISD_TAG.get(kind, 'HC')}{number:03d}")


def isd_in_use(console, number):
    """Is that smart sensor switched on for ISD? V43's own flag."""
    return (console.values.get(f"SV43{int(number):02d}") or "0") == "1"


def set_isd_in_use(console, number, on):
    """V43's door, which the panel's own ENABLE/DISABLE screen goes through.

    "SS - Smart Sensor Index number", "f - In use flag 1=Yes 0=No". The
    panel kept its own `evr_afm` and `evr_ps` settings beside this and
    nothing joined them, so a technician who enabled an air flow meter on
    the glass left V43 answering that it was not in use -- and V43 is what
    ISD's own MISSING VAPOR FLOW MTR alarm is raised against. Two stores
    for one fact, which is FIDELITY F9's shape.
    """
    console.values[f"SV43{int(number):02d}"] = "1" if on else "0"

# The four categories with a diagnostic report of their own. 723's own
# category list is the source for all of them: "03=mag sensor, 04=vac Sensor,
# 05=atmospheric sensor, 08=vapor valve", 576013-635 Rev AA p.327.
MAG, VAC, ATMP, VALVE = "03", "04", "05", "08"


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


def install_time(console, number):
    """When the console learned this sensor was there: 333's install event.

    The console keeps no install log of its own, so an install is the cold
    start it counts its test needed warnings from, less a stable spread per
    sensor. The panel's SMART SENSOR INSTALL LOG prints the same moment.
    FIDELITY D30.
    """
    began = console._commissioned or time.mktime(console.now())
    return began - readings.fixed(0.0, 7200.0, "install", number)


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


def _vac_stamp_at(when):
    """The date line B38 stands in front of a reading, in its own form.

    `4-12-04 11:28AM` on p.534: two digit year, and no space before the
    meridiem. It is a line of its own -- see `_vac_diagnostic`. It takes the
    moment the reading was TAKEN, because that is what it stamps; it used to
    take an offset from now, which stamped every report with the hour it was
    asked for. FIDELITY L18.
    """
    return time.strftime("%m-%d-%y %I:%M%p", time.localtime(when))


def _atm_psi(console, number):
    """The ATM P sensor's reading, which a vacuum sensor is compensated by.

    "The atmospheric pressure [ATMP] sensor is resident in the Smart Sensor /
    Press Module. One ATMP sensor is required with Vac Sensor systems per
    site", 576013-623 Rev AN p.26-3 -- so on a site that has one there is
    exactly one to find, and its reading is the site's. A site that has not
    programmed one still has to answer, and answers off the vacuum sensor's
    own position, which is where this reading came from before.
    """
    for one in range(1, max(console.capacity("smart"), 0) + 1):
        if _smart_kind(console, one) == ATMP:
            return _number(console.diag_reading("ss_atm", one))
    return _number(console.diag_reading("ss_atm", number))


def _vac_pressures(console, number):
    """(compensated, uncompensated) PSI on a vacuum sensor.

    The compensated reading is the interstitial pressure this console
    MODELS -- `vac_psi`, the one the bench drives, the one the panel draws
    and the one the No Vacuum Alarm is posted against. Both readings were a
    `readings.wander` band near -9 psi instead, so the sump the bench was
    filling and the sump the port reported were two different sumps: a space
    driven to -0.4 by a leak still went out over B38 at -9.1. FIDELITY L18.

    The uncompensated reading is the same sensor BEFORE the atmospheric
    correction: "COMPENSATED PRESSURE ... Pressure sensor value minus ATMP
    sensor value", 576013-818 Rev AB Figure 6-29, whose -0.155 stands against
    its own uncompensated -0.094 and Figure 6-32's ATM PRESSURE of 0.062.
    Subtracting the atmosphere is what makes the compensated one, so adding
    it back is what makes the uncompensated one.
    """
    compensated = console.vac_psi(number)
    return compensated, compensated + _atm_psi(console, number)


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


# How often an ISD sensor is read, for SS COMM DIAG's SAMPLES READ. No page
# says; the probe's own count (`Console.probe_samples_read`) is a sample a
# second, and an ISD sensor's is taken as one a minute. UNKNOWNS A56.
ISD_SAMPLE_SECONDS = 60.0


def ss_comm_counts(console, number):
    """SS COMM DIAG's six counters: samples read, samples used, parity
    errors, partial reads, comm errors, restarts.

    The ISD manuals print them (577013-800 Rev P p.20-44) and define none.
    Read is counted from commissioning, the way A15 counts a probe's; a
    sensor the bench has put into a communication or fault alarm is losing
    samples to it, so its comm or partial count is one and USED is that
    much short. UNKNOWNS A56.
    """
    now = time.mktime(console.now())
    installed = console._commissioned or now
    read = int(max(0.0, now - installed) / ISD_SAMPLE_SECONDS)
    state = console.sensor_state.get(("smart", str(int(number))), "normal")
    comm = 1 if state == "comm" else 0
    partial = 1 if state in ("fault", "faultwarn") else 0
    return (read, max(0, read - comm - partial), 0, partial, comm, 0)


def ss_channel_words(console, number):
    """SS CHANNEL DIAG's twenty-four sixteen-bit words, `C00` to `C20`.

    The first six are the sensor's channel values -- the same `_channels`
    B34 reports -- as the IEEE floats they are, split into words; the rest
    are stable per sensor. What the words ARE is on no page. UNKNOWNS A56.
    """
    words = []
    for value in _channels(console, number)[:3]:
        bits = int(_float(value), 16)
        words += [bits >> 16, bits & 0xFFFF]
    while len(words) < 24:
        words.append(readings.integer(0, 0xFFFF, "sschanword", int(number),
                                      len(words)))
    return words[:24]


def smart_protocol(number):
    """B35's protocol version, which SS CONSTANTS DIAG prints too."""
    return readings.integer(1, 9, "ssproto", number)


def _channel_grid(values):
    """The ten-across table the last sample report draws them in."""
    # p.529 numbers the ten columns at 8, 13, 18 and so on, five apart,
    # over four character values -- not the eight this was spacing for
    rows = ["    " + "".join(f"{i:5d}" for i in range(10))]
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
    # 577013-812 Rev G sells two, a 1-foot and a 2-foot measurement range,
    # and 576013-610 Rev AC p.24-2 calls them the 12-inch and the 24-inch
    # sensor. This drew anything from 18 to 36; the bench says which one
    # is fitted, and the leak test's WATER TOO HIGH is read off the same
    # number. FIDELITY U1b.
    length = float(console.sumps.range_of(number))
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
MAX_RUN_HOURS = 8.0


def _monitored(console, number):
    """(kind, device) the monitor is watching, from S7C6."""
    raw = (console.values.get(f"S7C6{number:02d}") or "").strip()
    body = raw[2:] if len(raw) > 4 else raw
    if len(body) < 4 or not body.isdigit() or body[:2] == "00":
        return "", 0
    return body[:2], int(body[2:4])


def _pump_out(console, kind, device):
    """The PUMP (OUT) column: is the pump running?

    What the console is calling for, from the device it watches -- and a
    welded contactor is a pump that runs whatever the console calls for,
    which is the case the monitor exists to catch. BENCH.md P3.
    """
    if kind and console.outputs.is_welded(kind, device):
        return True
    if kind == "11":
        return console.outputs.energised(device)
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
    # The console counts from the moment the relay was told to open with the
    # pump still running, which `monitor_watch` keeps. This used to work it
    # out backwards from the alarm history -- no alarm meant not stuck for
    # longer than the delay -- which was the best available while nothing
    # raised the alarm at all.
    since = console.pumpmon_since.get((number, "stuck"))
    if since is None:
        return 0.0
    return max(time.mktime(console.now()) - since, 0.0)


def monitor_watch(console, now):
    """Keep each pump relay monitor's two clocks, one tick at a time.

    576013-623 Rev AN gives the monitor both of its jobs and both of its
    delays: "if the pump continues to run after it is instructed to turn
    off, for longer than a 5 - 600 second selectable delay (Stuck Delay), an
    alarm is posted", and "monitor the pump each time it switches on, and if
    it is still running after a 1 - 24 hour delay (Max Run Time delay), to
    post an alarm". Both want a moment to count from, which is what this
    keeps: when the relay was told to open with the pump still running, and
    when the pump last switched on. See FIDELITY D1 and N1.
    """
    since = console.pumpmon_since
    for number in range(1, max(console.capacity("pumpmon"), 0) + 1):
        kind, device = _monitored(console, number)
        running = _pump_out(console, kind, device)
        stuck = running and kind and not _relay_in(console, number)
        for what, on in (("stuck", stuck), ("run", running)):
            key = (number, what)
            if not on:
                since.pop(key, None)
            else:
                since.setdefault(key, now)


def monitor_conditions(console):
    """[AANNTT] for the Pump Relay Monitor's own alarm.

    576013-610 Rev AC Table 29-21 gives one alarm for both conditions --
    "PUMP RELAY ALARM ... If pump relay assigned - pump continues to run
    after it was instructed to" stop -- and the setup manual gives the two
    delays it waits. Nothing raised it: `relay_stuck` was read by the status
    line alone, and the diagnostic that exists to show it had no reader.
    """
    out, now = [], time.mktime(console.now())
    for number in range(1, max(console.capacity("pumpmon"), 0) + 1):
        kind, _device = _monitored(console, number)
        if not kind:
            continue
        stuck = console.pumpmon_since.get((number, "stuck"))
        delay = console.limit("7C7", number) or STUCK_DELAY
        if stuck is not None and now - stuck >= delay:
            out.append(PUMPMON_AA + "02" + f"{number:02d}")
            continue
        ran = console.pumpmon_since.get((number, "run"))
        most = console.limit("7C8", number) or MAX_RUN_HOURS
        if ran is not None and now - ran >= most * 3600.0:
            out.append(PUMPMON_AA + "02" + f"{number:02d}")
    return out


def monitored_kind(console, number):
    """Is a pump relay ASSIGNED to this monitor, and what kind?

    Figure 6-16's two columns are headed "If pump relay assigned" and "If
    pump relay = NONE", and S7C6 is what says which: "00 none".
    """
    return _monitored(console, number)[0]


def _run_time(console, number, wide=False):
    """"PUMP RUN TIME: HH:MM", and HHH:MM on the other branch.

    "If PUMP = ON, total time pump has been running" -- the same clock 7C9
    reports, drawn as hours and minutes instead of the letters H and M.
    """
    hours = _run_hours(console, number)
    whole = int(hours)
    minutes = int(round((hours - whole) * 60.0)) % 60
    width = 3 if wide else 2
    return f"PUMP RUN TIME: {whole:0{width}d}:{minutes:02d}"


def monitor_diag(console, token, number):
    """The PUMP RELAY MONITOR DIAG screens, read rather than drawn.

    576013-818 Figure 6-16 draws this function twice over and annotates
    every value on it: PUMP (OUT) is "OFF or ON (See Diagram A below)",
    RELAY (IN) is "OFF or ON", STUCK RELAY is "If RELAY =OFF and PUMP = ON,
    time pump has been on since relay was supposed to open", and PUMP RUN
    TIME is "If PUMP = ON, total time pump has been running". Every one of
    those was already computed for 7C4's report, and this function had no
    reader at all -- three screens of the flow chart drawn literally, with
    `999 SEC`, `HH:MM` and `HHH:MM` on them for ever. See FIDELITY D1.
    """
    kind, device = _monitored(console, number)
    out = "ON" if _pump_out(console, kind, device) else "OFF"
    if token == "pumpmon_out":
        letter = MONITORED.get(kind, ("R", None))[0]
        state = "ON" if _relay_in(console, number) else "OFF"
        return (f"r {number}: PUMP (OUT): {out}" + chr(10)
                + f"{letter} {device}: RELAY (IN): {state}")
    if token == "pumpmon_stuck":
        seconds = min(int(_stuck_seconds(console, number)), 999)
        return (f"r {number}: STUCK RELAY: {seconds:3d} SEC" + chr(10)
                + _run_time(console, number))
    if token == "pumpmon_label":
        # the branch with no relay assigned: the label takes the top line and
        # the pump state moves under it
        return f"PUMP (OUT): {out}"
    if token == "pumpmon_run":
        return _run_time(console, number, wide=True)
    return ""


def _run_hours(console, number):
    """How long the pump this monitor watches has been running.

    "monitor the pump each time it switches on, and if it is still running
    after a 1 - 24 hour delay (Max Run Time delay), to post an alarm" -- so
    it is counted from the switch-on, which `monitor_watch` notes. This used
    to answer the console's UPTIME, because there was no pump run clock to
    read: a pump that started an hour ago on a console that came up
    yesterday reported a day.
    """
    since = console.pumpmon_since.get((number, "run"))
    if since is None:
        return 0.0
    return max(time.mktime(console.now()) - since, 0.0) / 3600.0


# ---------------------------------------------------------------------------
# The external inputs and the output relays
# ---------------------------------------------------------------------------
# S80C, "External input type and orientation": type 2 is a generator, in
# either orientation, which is what I403's own heading means by "Setup
# parameters determine whether an input is from a generator." The reading
# is `inputs.Inputs.setup`, which is the same one the engine acts on.

# "aaaa - Alarm type number: ... 0004=Generator Off, 0005=Generator On": the
# two extra values I403 has and I402 does not, which an input programmed as a
# generator reports in place of Input Normal and Input Alarm.
GENERATOR_VALUE = {"02": "0004", "03": "0005"}
GENERATOR_WORDS = {"02": "GENERATOR OFF", "03": "GENERATOR ON"}


def _is_generator(console, number):
    # the engine's reading of S80C, which knows a tank list can follow the
    # type: `raw[-2:]` read the last TANK of a generator on tanks 1 and 2
    # as its type and reported the run as an ordinary input alarm
    from . import inputs
    return console.inputs.kind(number) == inputs.GENERATOR


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
    return console.outputs.closed(number)


# ---------------------------------------------------------------------------
# The diagnostic reports
# ---------------------------------------------------------------------------
# What each module's own typical response prints for its reference channels,
# which is the only figure the manual gives for them.
#
# HIGH REF is the manual's LEFT column and not the larger number. On four
# of the seven codes it happens to be both, and on the three chlorine ones it
# is not: 576013-635 Rev AA prints `B41` as `1 5 1815 7823 4193` under
# `SENSOR COUNTER HIGH REF LOW REF VALUE`, and `B46` and `B4B` as
# `1 5 8900 32000 5200 100000`. This table held all three the other way
# round -- tidied, at some point, into larger-first by somebody meeting a row
# where HIGH was the smaller figure -- so the console printed the two columns
# swapped on exactly those three. See FIDELITY L3. The order here is the
# manual's column order; do not sort it.
#           code    high     low
REFERENCE = {"B01": (1072.0, 193.0),
             "B06": (1080.0, 208.0),
             "B11": (5440.0, 930.0),
             "B21": (1086.0, 215.0),
             "B41": (1815.0, 7823.0),
             "B46": (8900.0, 32000.0),
             "B4B": (8900.0, 32000.0)}

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
        rows.append(_diag_row(number, counter, hi1, lo1, shown))
        body += f"{number:02d}" + _floats(values)
    if code[0].isupper():
        return handler._frame(code, SEP.join(rows))
    return handler._frame(code, body)


# 576013-635 Rev AA p.523 and p.524, counted off the rendered page. The two
# word headings stack, and the second line's columns are the row's:
#
#             SAMPLE     HIGH       LOW
#     SENSOR COUNTER      REF       REF        VALUE
#          1        5     1072       193       145727
#
# SENSOR at 0, COUNTER at 7, the two REFs at 20 and 30, and the value held
# right against 45 -- with a second channel's held right against 58. This
# console had every one of those a column or more out.
def _diag_row(number, counter, high, low, values):
    """One data row under `_diag_header`, in the heading's own columns.

    6, 8, 9, 10 and 13 wide, so the five fields end at 6, 14, 23, 33 and 46
    -- which is where p.523's `1 5 1072 193 145727` and p.527's
    `1 50 1086 215 28393` both put them, the widths being the same on every
    page that stacks this heading. A row builder living beside the heading
    builder, because the one report that hand-rolled its own drifted a
    column to six wider than the heading above it. FIDELITY L11.
    """
    return (f"{number:6d}{counter:8.0f}{high:9.0f}{low:10.0f}"
            + "".join(f"{v:13.0f}" for v in values))


def _diag_header(channels):
    """The two heading lines these six reports stack."""
    first = " " * 8 + "SAMPLE" + " " * 5 + "HIGH" + " " * 7 + "LOW"
    second = "SENSOR COUNTER" + " " * 6 + "REF" + " " * 7 + "REF"
    if channels == 1:
        return [first, second + " " * 8 + "VALUE"]
    return [first, second + " " * 7 + "VALUE1" + " " * 7 + "VALUE2"]


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
                f"{title} STATUS REPORT", STATUS_HEADER,
                status_row), "sensor status")
        return (_history_report(
            handler, code, aa, devices, label_of,
            f"{title} ALARM HISTORY REPORT",
            HISTORY_HEADER, history_row, LOCATION_COLUMN, HISTORY_WORDS),
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
        # p.525: SENSOR at 0, PPM at 13, and the reading right aligned
        # to column 15
        rows = ["VAPOR DIAGNOSTIC REPORT - VAPOR CONCENTRATION", "",
                f"{'SENSOR':<13s}PPM"]
        body = ""
        for number in devices:
            # "1. Vapor concentration (ppm)", which is the second channel of
            # the vapor module read as a concentration rather than a
            # resistance, and the same number the panel's screen shows
            ppm = _number(c.diag_reading("sensor_ppm", number))
            rows.append(f"{number:{SENSOR_COLUMN}d}{ppm:10.0f}")
            body += f"{number:02d}" + _floats([ppm])
        if display:
            return handler._frame(code, SEP.join(rows)), "vapor concentration"
        return handler._frame(code, body), "vapor concentration"

    # ---- the ground temperature thermistor, which belongs to VLLD ---------
    if tok == "B21":
        # It was gated on the GROUNDWATER card, and enumerated from
        # groundwater config 711, on the strength of the word "ground" in
        # two unrelated names. 576013-879 Rev W p.60: "When using volumetric
        # line leak detection (VLLD), only one ground temperature thermistor
        # is needed per site and the thermistor must be wired to thermistor
        # position number 1 (positions 2 - 4 are not used)."
        #
        # `diagdata.json` already had it right -- GROUND TEMP DIAGNOSTIC is
        # `requires: vlld` -- so the panel and the wire disagreed about the
        # same screen: on a console with VLLD and a probe module, exactly the
        # console p.60 is describing, the panel offered the diagnostic and
        # IB2100 answered 9999. FIDELITY M4 and L10.
        if not c.has("vlld"):
            return handler._nine(code), "no VLLD module fitted"
        # One thermistor per site, on position 1. The card that carries it is
        # 635's function 102 `0A=Four Probe w/ Ground Temp Module`, which this
        # console does not model as its own type -- see FIDELITY M4, still
        # open on that point.
        devices = [1] if dev in ("00", "01") else []
        high, low = REFERENCE["B21"]
        rows = ["GROUNDTEMP DIAGNOSTIC REPORT", ""] + _diag_header(1)
        body = ""
        for number in devices:
            counter = _sample_counter(c)
            hi = _reference(c, tok, number, high, "high")
            lo = _reference(c, tok, number, low, "low")
            # "Value = resistance measured by thermistor", which is what the
            # panel's own GROUND TEMP screen reads out
            # See console.ground_ohms: 480 to 620 is a value Figure 6-22
            # calls a shorted thermistor, and B21 agreed with the panel so
            # there was no second opinion.
            from .console import ground_ohms
            average = ground_ohms(c.product_temperature(number))
            # "Last Reading" and "Current Average Value" are two of B21's
            # own five fields, and both were this one expression -- so the
            # two floats of its computer response came back bit-identical,
            # where every other diagnostic on this card puts the sample to
            # sample noise of a single A/D conversion on one of them. Same
            # noise, same key shape, as `_sensor_pair`. See FIDELITY L10.
            last = average * (1.0 + readings.wander(
                c, -0.01, 0.01, "sample", "gw", number, 1,
                swing=1.0, period=30.0))
            # the SAME widths as every other report that stacks this header,
            # which this one did not have: 6/9/11/11/15 against the shared
            # 6/8/9/10/13, so B21's data row ran a column to six wider than
            # the heading printed directly above it. p.527 sets it exactly as
            # p.523 does. FIDELITY L11.
            rows.append(_diag_row(number, counter, hi, lo, [last]))
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
        # p.153: RELAY at 0, LOCATION at 8 and STATUS at 31, with the number
        # right against column 4. Measured off the word boxes, not counted --
        # this used to put LOCATION at 8 and STATUS at 33.
        rows = [f"{'RELAY':<8s}{'LOCATION':<23s}STATUS"]
        body = ""
        for number in devices:
            closed = _relay_closed(c, number)
            label = _label(c, "807", number, RELAY_LABEL)
            rows.append(f"{number:4d}    {label:<23.23s}"
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
        # p.149: INPUT at 0, LOCATION at 8 and STATUS at 31, the number right
        # against column 5. 402 and 403 put the same number against column 4
        # on their own pages, one to the left of this one -- each page is
        # followed as it is drawn rather than averaged with its neighbours.
        rows = [f"{'INPUT':<8s}{'LOCATION':<23s}STATUS"]
        body = ""
        for number in devices:
            nn = _worst(INPUT_AA, standing.get(number, []))
            # "STATUS ... OFF": an external input is a contact, and the
            # console words the two states it can be in the way a contact
            # reads rather than the way its alarm number does
            words = {None: "OFF", "02": "OFF", "03": "ON"}.get(
                nn, _words(INPUT_AA, nn, "OFF"))
            rows.append(f"{number:5d}   {label_of(number):<23.23s}{words}")
            body += f"{number:02d}" + _status_value(INPUT_AA, nn or "02")
        if code[0].isupper():
            return handler._frame(code, SEP.join(rows)), "input status"
        return handler._frame(code, body), "input status"

    generator = tok == "403"
    title = "INPUT / GENERATOR ALARM HISTORY REPORT" if generator else ""

    def row(number, label):
        # pp.150 and 151 both: the number right against column 4 and the
        # label at 8, under a head whose LOCATION is at 8 as well
        return f"{number:4d}    {label:<23.23s}"

    header = f"{'INPUT':<8s}LOCATION"
    if code[0].isupper():
        rows = [title, "", header] if title else [header]
        for number in devices:
            rows.append(row(number, label_of(number)))
            rows[-1] = rows[-1].rstrip()
            for one in _incidents(c, INPUT_AA, number):
                words = _words(INPUT_AA, one["nn"])
                if generator and _is_generator(c, number):
                    words = GENERATOR_WORDS.get(one["nn"], words)
                rows.append(f"{'':8s}{_when(one['at']):<30s}{words}")
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


def pumpmon_status_rows(c, dev="00"):
    """I322's report as lines, for whichever end of the console asks.

    The panel's PUMP RELAY MONITOR print built its own row -- a label and
    the pump's state and nothing else -- where this one carries the pump,
    the relay LINE it is watching and the STATUS that is the reason the
    report exists. One renderer, folded onto the roll the way `csld_monthly`
    and the Service Report already are. See FIDELITY T12.
    """
    devices = _devices(c, "pumpmon", "7C4", dev)
    standing = _standing(c, PUMPMON_AA)
    rows = ["PUMP RELAY MONITOR STATUS REPORT", "",
            _PUMPMON_UPPER, _PUMPMON_LOWER]
    for number in devices:
        kind, device = _monitored(c, number)
        pump = _pump_out(c, kind, device)
        nn = _worst(PUMPMON_AA, standing.get(number, []))
        rows.append(f"{number:6d}  "
                    f"{_label(c, '7C5', number, 'PUMP RELAY {n}'):<23.23s}"
                    f"{'ON' if pump else 'OFF':<7s}"
                    f"{_monitor_text(c, number):<13s}"
                    + _words(PUMPMON_AA, nn, "NORMAL"))
    return rows


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
            "DEVICE  LABEL",
            lambda number, label: label,
            14, 45, "%02X"), "pump relay alarm history")

    standing = _standing(c, PUMPMON_AA)
    # 576013-635 Rev AA p.118 and p.549, read off the word boxes rather than
    # off the text extraction, which interleaves these two columns:
    #
    #                                   PUMP   PUMP RELAY    STUCK     RUN
    #     DEVICE  LABEL                 (OUT)     (IN)       RELAY     TIME
    #          1  PUMP RELAY UNLEADED    OFF    Q 1: OFF     0 SEC    00:00
    #
    # The upper line is the one with PUMP on it, and this console had the
    # two the other way round -- DEVICE and LABEL on top with the
    # parenthesised halves under them. `_diag_header` gets the same shape
    # right for the six resistance diagnostics, so it was a slip and not a
    # misunderstanding. The columns were out as well, by three and by six.
    # See FIDELITY L6.
    upper = (_PUMPMON_UPPER if tok == "322"
             else " " * 30 + f"{'PUMP':<7s}"
             + f"{'PUMP RELAY':<14s}{'STUCK':<10s}RUN")
    lower = (_PUMPMON_LOWER if tok == "322"
             else f"{'DEVICE':<8s}{'LABEL':<22s}{'(OUT)':<10s}{'(IN)':<11s}"
             + f"{'RELAY':<10s}TIME")
    rows = ["PUMP RELAY MONITOR STATUS REPORT" if tok == "322"
            else "PUMP RELAY MONITOR DIAGNOSTIC", "", upper, lower]
    body = ""
    for number in devices:
        kind, device = _monitored(c, number)
        pump = _pump_out(c, kind, device)
        relay = _relay_in(c, number)
        a = "1" if pump else "0"
        b = "1" if relay else "0"
        if tok == "322":
            nn = _worst(PUMPMON_AA, standing.get(number, []))
            rows.append(f"{number:6d}  {label_of(number):<23.23s}"
                        f"{'ON' if pump else 'OFF':<7s}"
                        f"{_monitor_text(c, number):<13s}"
                        + _words(PUMPMON_AA, nn, "NORMAL"))
            body += (f"{number:02d}{a}{b}"
                     + _status_value(PUMPMON_AA, nn))
            continue
        stuck = _stuck_seconds(c, number)
        hours = _run_hours(c, number)
        # the run time is held RIGHT against 64, where the stuck delay
        # runs left from 51: `0 SEC    00:00` on the sample's own row
        rows.append(f"{number:6d}  {label_of(number):<23.23s}"
                    f"{'ON' if pump else 'OFF':<7s}"
                    f"{_monitor_text(c, number):<13s}"
                    f"{f'{stuck:.0f} SEC':<9s}"
                    f"{f'{int(hours):02d}:{int(hours % 1.0 * 60):02d}':>5s}")
        body += f"{number:02d}{a}{b}" + _floats([stuck, hours])
    note = "pump relay status" if tok == "322" else "pump relay diagnostic"
    if display:
        return handler._frame(code, SEP.join(rows)), note
    return handler._frame(code, body), note


# 576013-635 Rev AA p.118's own two-line stacked head for function 322,
# kept where both the wire and the roll can reach it. See FIDELITY T12.
_PUMPMON_UPPER = " " * 30 + f"{'PUMP':<7s}PUMP RELAY"
_PUMPMON_LOWER = (f"{'DEVICE':<8s}{'LABEL':<22s}{'(OUT)':<10s}"
                  f"{'(IN)':<11s}STATUS")


SMART_STATUS_COLUMN = 21
SMART_STATUS_HEADER = ("SENSOR".ljust(LOCATION_COLUMN)
                       + "LOCATION".ljust(SMART_STATUS_COLUMN
                                          - LOCATION_COLUMN) + "STATUS")


def _smart(handler, tok, dev, code, display):
    """The smart sensor reports, which are a card that talks back."""
    c = handler.c

    def label_of(number):
        return _label(c, "722", number, "SMART SENSOR {n}")

    def head(number):
        """"s 1: SUMP 1", the way a smart sensor screen heads itself."""
        return f"s {number}:{label_of(number)}"

    if tok in ("315", "316"):
        # p.111 draws the smart sensors' STATUS column at 21 where the rest
        # of the family draws it at 31, and the alarm history at p.112 is the
        # family's own again. Both are the manual's, not a tidier rule.
        smart_status, smart_history = _sensor_rows(SMART_STATUS_COLUMN)
        if tok == "315":
            return (_status_report(
                handler, code, SMART_AA, _smart_devices(c, dev), label_of,
                "SMART SENSOR STATUS REPORT", SMART_STATUS_HEADER,
                smart_status), "smart sensor status")
        return (_history_report(
            handler, code, SMART_AA, _smart_devices(c, dev), label_of,
            "SMART SENSOR ALARM HISTORY REPORT",
            HISTORY_HEADER, smart_history, LOCATION_COLUMN, HISTORY_WORDS),
            "smart sensor alarm history")

    if tok == "333":
        # "nnn - Number of Events to Follow (Decimal)". The console keeps no
        # install log of its own, so an install event is the console learning
        # the sensor was there, which on this bench is the cold start it
        # counts its test needed warnings from.
        devices = _smart_devices(c, dev)
        # p.120: SENSOR right against 23, SERIAL NUMBER at 27 and TYPE at
        # 43, with the hour space padded the way it is everywhere else
        rows = ["SMART SENSOR INSTALL LOG", "", wiretables.heading("333")]
        body = f"{len(devices):03d}"
        for number in devices:
            when = install_time(c, number)
            packed = time.strftime("%y%m%d%H%M", time.localtime(when))
            serial = _smart_serial(c, number)
            type_code, type_name = SMART_TYPE.get(_smart_kind(c, number),
                                                  SMART_UNKNOWN)
            at = time.localtime(when)
            shown = (time.strftime("%m-%d-%y", at)
                     + f"{at.tm_hour:3d}" + time.strftime(":%M:%S", at))
            rows.append(f"{shown:<17s}{number:7d}   "
                        f"{serial:<16d}{type_name}")
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
            # p.528: a leading space, the label from column 1, the figure
            # held right against 20 and its unit at 21 -- ` TOTAL HT
            # 15.0 IN.`. This had the figure a column left of that, so the
            # whole value column sat one out. FIDELITY L11.
            rows += [f" {name:<12s}{value:7.1f} {unit}"
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
        # p.530: LABEL at 8, TYPE at 28, SERIAL NUMBER at 40 and DATE
        # CODE at 55, with the two numbers held right against 52 and 63
        rows = ["SMART SENSOR SERIAL NUMBER", "",
                "SENSOR  LABEL               TYPE"
                "        SERIAL NUMBER  DATE CODE"]
        body = ""
        for number in _smart_devices(c, dev):
            type_code, type_name = SMART_TYPE.get(_smart_kind(c, number),
                                                  SMART_UNKNOWN)
            model = _smart_model(c, number)
            serial = _smart_serial(c, number)
            date_code = readings.integer(10000, 60000, "ssdate", number)
            protocol = smart_protocol(number)
            # the two numbers are held RIGHT against 52 and 63, which is
            # where p.530 puts them: `123456` under SERIAL NUMBER at 40-52
            # and `26214` under DATE CODE at 55-63. The serial field was
            # twelve wide, two past its column -- and this console's smart
            # serials are eight digits where the sample's is six, so the
            # field overflowed and carried DATE CODE along with it whenever
            # a serial changed width. See FIDELITY L10.
            rows.append(f"{number:2d} {label_of(number):<21.21s}"
                        f"{type_code[1:]}-{type_name:<15.15s}"
                        f"{serial:>10d}{date_code:>11d}")
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
                     # p.533, and it is NOT p.531's column: this report
                     # holds its serial right against 24 where the constants
                     # report holds its against 23, and the pressure against
                     # 20 with the unit at 21. Each report is measured off
                     # its own page. FIDELITY L11.
                     f"{'SERIAL NUMBER':<13s}{serial:>11d}",
                     f"{'ATM PRESSURE':<12s}{psi:>8.3f} PSI", ""]
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
            # p.537, the same inversion as B72 and the same source:
            #
            #                            DURATION
            #     START DATE/TIME        HH:MM:SS
            #     04-05-04 09:06:58       0:02:24
            #
            # DURATION alone on the upper line, START DATE/TIME with the
            # HH:MM:SS under it, and the times held right against 30.
            rows += [head(number), "",
                     f"{'':23s}DURATION",
                     f"{'START DATE/TIME':<23s}HH:MM:SS"]
            for when, seconds in events:
                clock = (f"{int(seconds) // 3600:d}"
                         f":{int(seconds) % 3600 // 60:02d}"
                         f":{int(seconds) % 60:02d}")
                rows.append(time.strftime("%m-%d-%y %H:%M:%S",
                                          time.localtime(when))
                            + f"{clock:>14s}")
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
        # p.531 holds every constant right against 23 -- `SERIAL NUMBER
        # 123456`, `MODEL 101`, `GRADIENT 360.000` all ending in the same
        # column -- where this held them against 26. FIDELITY L11.
        rows += [f"{name:<13s}{text:>10s}"
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
        # `Console.evacuation_state` is the one place this is decided, and
        # B38's field is the one hex digit its own note lists. The state was
        # read off `sensor_state` here, which knows about No Vacuum and
        # nothing about the valve -- so an EVAC HOLD the panel was showing
        # never reached this report at all. FIDELITY L18.
        evac = c.evacuation_state(number)[-1]
        fluid = "1" if state in ("fault", "faultwarn") else (
            "2" if state == "high" else "0")
        # ...and the valve is the valve. This asked the evacuation state
        # whether the valve was open, and answered CLOSED for every state
        # this console can reach, so the one screen and the one report that
        # both name VCV disagreed whenever a technician held it open.
        vcv = "1" if c.vac_valve_open(number) else "0"
        # the manual's own typical response has a relief valve fault on it,
        # which is the fault a sensor in a fault state reports here
        faults = 4 if fluid == "1" else 0
        # The last manual test that finished, which is what the three
        # stamped readings ARE -- B38 carries a date and a validity flag for
        # each of them, and the panel's three result screens read the same
        # record. These were a `readings.wander` band, so a sensor that had
        # never run a test answered with all three and a sensor that had
        # answered with numbers unrelated to its own result. FIDELITY L18.
        result = c.vac_result(number) or {}
        rate, hours = result.get("rate"), result.get("hours")
        ratio, at_psi = result.get("ratio"), result.get("psi")
        minutes = None if hours is None else int(round(hours * 60.0))
        # V, v and f: "1=Leak Rate valid ... 1=Time to No Vacuum valid ...
        # 1=Evac Ratio valid", p.531 and p.532. All three were hard-coded 1,
        # which is the console asserting three measurements it had not made.
        valid = ["1" if v is not None else "0"
                 for v in (rate, minutes, ratio)]
        when = _stamp(c) if result.get("at") is None else time.strftime(
            "%y%m%d%H%M", time.localtime(result["at"]))
        rows += [head(number), "",
                 "VAC SENSOR",
                 # `SERIAL NUMBER        24` -- the number is held right
                 # against 22, where this had it against 25
                 f"{'SERIAL NUMBER':<13s}{serial:>10d}",
                 # `              -9.000 PSI`: the figure runs to 19 and
                 # the unit to 23, which is one column left of where this
                 # console had both
                 "COMPENSATED PRESSURE:",
                 f"{compensated:>20.3f} PSI",
                 "UNCOMPENSATED PRESSURE:",
                 f"{uncompensated:>20.3f} PSI",
                 "EVACUATION STATE:",
                 # ` VACUUM OK` is indented one, the way ` NONE` and the
                 # fault names are: a value under its own heading
                 " " + EVAC_WORDS[evac],
                 f"FLUID STATUS: {FLUID_WORDS[fluid]}",
                 f"VCV: {VCV_WORDS[vcv]}"]
        # p.534, and 576013-818 Rev AB Figure 6-29 draws the same block:
        #
        #     4-12-04 11:28AM
        #     LEAK RATE:     0.123 GPH
        #     TIME TO NO VAC:
        #               150:20 HHHH:MM
        #     4-12-04 10:15AM
        #     EVAC RATIO:5.2 @ -4.3PSI
        #     SENSOR FAULTS:
        #       RELIEF VALVE FAULT
        #
        # The date is a line of its own, standing IN FRONT of the reading
        # it stamps, and the label shares its line with the value. This
        # console had it the other way round -- the label alone and the date
        # concatenated with the value -- which put a timestamp inside the
        # measurement column:
        #
        #     LEAK RATE:
        #     09-02-26 11:01AM   0.213 GPH
        #
        # TIME TO NO VAC is the reading with NO date on it; the second date
        # belongs to EVAC RATIO, which is a different measurement made at a
        # different moment. See FIDELITY L7.
        # A blank line before the FIRST date and not before the second.
        # Measured off the page rather than argued: p.534's lines are 8.6
        # apart and the gap between `VCV: CLOSED` and `4-12-04 11:28AM` is
        # 17.2, while the gap in front of the second date is 8.6.
        #
        # `wiretables.lead` places the manual's blank lines from the same
        # measurement and cannot place this one, because it keys on a line's
        # WORDS and this line is a date -- a console fills in its own. It
        # gets the one in front of `SENSOR FAULTS:` and misses this one, so
        # the blank is here. Any report whose sample breaks in front of a
        # line made of site data has the same hole in it.
        #
        # A reading whose validity flag is 0 has no moment either, so the
        # date line in front of it goes with it: printing the console's own
        # clock over a measurement nobody made would be the report claiming
        # one. What it draws INSTEAD of the number is this console's, not a
        # page's -- 576013-818 Rev AB draws Figure 6-29 fully populated and
        # says nothing anywhere about an unrun test, and neither do the two
        # vacuum manuals beside it. The dashes are the panel's own form,
        # already on the glass for the same three readings, so that the
        # screen and the paper answer alike. See UNKNOWNS A73.
        stamp = (None if result.get("at") is None
                 else _vac_stamp_at(result["at"]))
        rows.append("")
        if valid[0] == "1":
            rows += [stamp,
                     f"{'LEAK RATE:':<10s}{f'{rate:.3f} GPH':>14s}"]
        else:
            rows.append(f"{'LEAK RATE:':<10s}{'--- GPH':>14s}")
        rows.append("TIME TO NO VAC:")
        shown = ("---:--" if minutes is None
                 else f"{minutes // 60:d}:{minutes % 60:02d}")
        rows.append(f"{shown + ' HHHH:MM':>24s}")
        if valid[2] == "1":
            rows += [stamp, "EVAC RATIO:" f"{ratio:.1f} @ {at_psi:.1f}PSI"]
        else:
            rows.append("EVAC RATIO:--- @ ---PSI")
        # A clean sensor still prints the heading. Figure 6-29 draws
        # `SENSOR FAULTS:` over ` NONE`, and this printed neither -- so the
        # one screen that answers "is anything wrong with this sensor"
        # answered by saying nothing, which reads as a report that stopped
        # early rather than as a sensor with nothing to report.
        named = [name for bit, name in FAULT_BITS if faults & bit]
        rows.append("SENSOR FAULTS:")
        rows += [f"  {name}" for name in named] or [" NONE"]
        rows.append("")
        # The three stamps are one stamp: the console takes all three
        # readings off one manual test, so they are one moment. The manual
        # draws two, 11:28AM and 10:15AM, because a real evacuation and a
        # real leak measurement happen at different times -- what it settles
        # is that the fields are SEPARATE, not that they must differ.
        body += (f"{number:02d}{serial:08X}{evac}{fluid}{vcv}{valid[0]}"
                 + when + _float(rate or 0.0) + valid[1]
                 + when + f"{minutes or 0:08X}" + valid[2]
                 + when + _float(ratio or 0.0) + _float(at_psi or 0.0)
                 + f"{faults:04X}"
                 + _floats([compensated, uncompensated]))
    if display:
        return handler._frame(code, SEP.join(rows)), "vac sensor diagnostic"
    return handler._frame(code, body), "vac sensor diagnostic"
