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
"""The console's printer.

A TLS-350 has a paper roll behind the left door, and PRINT is not a screenshot
key: what comes out depends on where you are standing in the menu. The
operator's manual prints each report in full, so these are its layouts, filled
from the console's own state: the same numbers the display and the serial
port are giving.

The manual is also specific about when the console prints on its own:
"If your system has a printer, it will print an alarm or warning report when
it detects a warning or alarm condition", and again when ALARM/TEST is pressed.
"""
import time

from . import screens
from .clock import clock_words
from .console import describe_alarms

# The roll behind the left door is 40 characters wide. Everything the console
# prints for itself is written to fit it; the reports it shares with the
# serial port are the serial port's width. The BIR reconciliation table is
# eight columns and seventy characters, and those come off the roll folded,
# because that is what a narrow printer does with a wide report.
WIDTH = 40
FOLD = "  "          # what a folded line is indented by

# The reports that ARE the console's own screen -- the setup report, and the
# status block every printout ends with -- are the display's twenty-four
# characters rather than the roll's forty, because that is what the console
# has to draw before it can print it. They are ruled the way 576013-635
# Rev AA rules a display-format response, which is not a run of hyphens.
SETUP_COLS = 24
SETUP_RULE = "- - - - - -  - - - - - -"
FEED = 12            # what the console runs out between one report and the
                     # next, so there is something to tear off by
REPEATS = 4          # station header lines, and shift times


def fit(lines, width=WIDTH):
    """Fold a report onto the paper, the way the roll takes it.

    Reports arrive as lines, and some of them arrive as one block with
    newlines in it, so both are flattened first. Nothing is thrown away: a
    line too wide for the paper is broken at the last space that fits and
    carried on, indented, until it is all on the roll.
    """
    out = []
    for block in lines:
        for line in str(block).split("\n"):
            line = line.rstrip()
            while len(line) > width:
                cut = line.rfind(" ", 0, width + 1)
                if cut <= len(FOLD):
                    cut = width
                out.append(line[:cut].rstrip())
                line = FOLD + line[cut:].lstrip()
            out.append(line)
    return out


def _rule():
    return "-" * 22


def header(console, title):
    """Every report starts with the station header and the date and time."""
    out = []
    for line in range(1, 5):
        text = console.text("503", line)
        if text:
            out.append(text)
    out.append("")
    out.append(console.clock_text())
    out.append("")
    out.append(title)
    out.append(_rule())
    return out


def inventory(console, only=None):
    """INVENTORY REPORT, volume, ullage, height, water and temperature."""
    out = header(console, "INVENTORY REPORT")
    tanks = console.programmed_tanks()
    if only:
        tanks = {n: v for n, v in tanks.items() if n in only}
    if not tanks:
        out.append("NO TANKS PROGRAMMED")
        return out
    pct = "95%" if (console.values.get("S56400") or "").strip().endswith("1") \
        else "90%"
    for n, (label, full) in sorted(tanks.items()):
        st = console.tank_level.get(n, {})
        vol, water = st.get("volume", 0.0), st.get("water", 0.0)
        ullage = max(full - vol, 0.0)
        diam = console.limit("607", n) or 96.0
        height = (vol / full if full else 0.0) * diam
        out.append("")
        out.append(f"T {n}:{label}")
        out.append(f"  VOLUME    = {vol:9.0f} GALS")
        out.append(f"  ULLAGE    = {ullage:9.0f} GALS")
        out.append(f"  {pct} ULLAGE = "
                   f"{max(full * (0.95 if pct == '95%' else 0.90) - vol, 0):6.0f} GALS")
        out.append(f"  TC VOLUME = {vol * 0.998:9.0f} GALS")
        out.append(f"  HEIGHT    = {height:9.2f} INCHES")
        out.append(f"  WATER VOL = {water * 12:9.0f} GALS")
        out.append(f"  WATER     = {water:9.2f} INCHES")
        out.append(f"  TEMP      = {55.0:9.1f} DEG F")
    return out


def alarms(console):
    """The alarm or warning report, which is what ALARM/TEST prints."""
    out = header(console, "ALARM/WARNING REPORT")
    shown = describe_alarms(console.compute_alarms())
    if not shown:
        out.append("ALL FUNCTIONS NORMAL")
        return out
    live = {a["aa"] + a["nn"] + a["tt"]
            for a in describe_alarms(console.conditions())}
    for a in shown:
        out.append("")
        out.append(a["screen"])
        out.append("  " + ("ACTIVE" if a["aa"] + a["nn"] + a["tt"] in live
                           else "CLEARED - NOT ACKNOWLEDGED"))
    return out


def status(console):
    """SYSTEM STATUS, the report the console sits on."""
    out = header(console, "SYSTEM STATUS REPORT")
    shown = describe_alarms(console.compute_alarms())
    if not shown:
        out.append("ALL FUNCTIONS NORMAL")
    else:
        for a in shown:
            out.append(a["screen"])
    return out


def revision(console):
    return header(console, "SYSTEM REVISION LEVEL") + console.revision_report()


def setup(console, function=None, device=None):
    """The setup a function holds, as the console draws it.

    Not a table of settings and values: the console prints the SCREEN. Each
    function is headed by its own name over the dashed rule the display
    format uses, and under it every step the console is showing draws the
    same two lines it draws on the display -- which is why this report is
    twenty-four characters wide on a forty character roll, and why a
    setting nobody has programmed is still on it, reading the default the
    console reads.

    Every printout ends the same way: the station header, the time it came
    off, and the system status underneath.
    """
    functions = [f for f in console.available_functions()
                 if function is None or f["function"] == function]
    out = []
    for fn in functions:
        if out:
            out.extend([""] * FEED)
        out.extend(setup_section(console, fn, device))
    out.extend([""] * FEED)
    out.extend(setup_footer(console))
    return out


def setup_section(console, fn, device=None):
    """One function: its name, the rule, and the screens under it."""
    out = [fn["function"][:SETUP_COLS], SETUP_RULE]
    for one in _setup_devices(console, fn, device):
        if not _programmed(console, fn, one):
            continue        # a device nobody has programmed is not on it
        for st in print_order(_printable_steps(console, fn, one)):
            for n in _repeats(st, one):
                out.extend(setup_block(console, fn, st, n))
    return out


def _printable_steps(console, fn, device):
    """The steps on the report, which is not quite the steps on the panel.

    A screen the panel gates on whether you can EDIT it still has a value,
    and the report prints it. 576013-635 Rev AA's display format for 551
    lists all six inventory alarm lines, and the tape prints all six
    under CONFIG: STANDARD -- where the panel only lets you walk the five
    custom ones when the config is CUSTOM.
    """
    return [st for st in fn["steps"]
            if console.visible(st, device) or st.get("print_always")]


def print_order(steps):
    """Menu order, with the steps that print somewhere else moved.

    A report is not always in the order the panel walks. 576013-623 Rev AN
    is explicit that SYSTEM LANGUAGE is the first screen of System Setup and
    that STEP moves from it to SYSTEM UNITS -- and the tape prints
    SYSTEM UNITS first, because the printed block for 517 carries both and
    carries them the other way round. Same story at the end of the function,
    where INVENTORY ALARMS UNITS is documented before Mass/Density and
    printed after Fiscal Height Security. So the menu keeps the order the
    manual gives it, and a step that PRINTS somewhere else says where.
    """
    out = list(steps)
    for st in list(out):
        anchor = st.get("print_before") or st.get("print_after")
        if not anchor:
            continue
        out.remove(st)
        hits = [i for i, x in enumerate(out) if x["text"].startswith(anchor)]
        if not hits:
            out.append(st)          # its anchor is not on this console
        elif st.get("print_before"):
            out.insert(hits[0], st)
        else:
            # "after" means after ALL of it: a setting the panel reaches
            # through a branch is two steps with the same words on them
            out.insert(hits[-1] + 1, st)
    return out


def _repeats(step, device):
    """How many times this step draws itself on a printed report.

    A console-wide screen that REPEATS is one screen on the display -- the
    panel walks it with the arrow keys, station header line 1 to 4, shift
    time 1 to 4 -- but a report cannot be walked, so all four are on it.
    Four is what the console has of each: 576013-635 Rev AA gives 503 the
    lines 01 to 04 and 502 the shifts 01 to 04, and the tape prints
    four header lines and four shift times.
    """
    if step.get("repeat") and screens.console_step(step):
        return range(1, REPEATS + 1)
    return [device]


def setup_block(console, fn, step, device=1):
    """One step, as the console draws it: one line, or two.

    A screen whose second line is blank prints as one line, because that is
    what the console has drawn -- there is no value under it to print.
    """
    return screens.print_lines(console, fn, step, device)


def setup_footer(console):
    """What every printout ends with: who, when, and how the system is."""
    out = []
    for line in range(1, 5):
        text = console.text("503", line)
        if text:
            out.append(text)
    out.append("")
    out.append(clock_words(console.now()))
    out.extend(["", "", ""])
    out.append(_centre("SYSTEM STATUS REPORT"))
    out.append(SETUP_RULE)
    shown = describe_alarms(console.compute_alarms())
    out.extend([a["screen"] for a in shown] or ["ALL FUNCTIONS NORMAL"])
    return out


def _centre(text):
    return text.rjust((SETUP_COLS + len(text)) // 2)


def _programmed(console, fn, device):
    """Has anybody put anything in this device's copy of this function?"""
    for st in fn["steps"]:
        code = screens.code_for(console, st, device)
        if code and console.values.get(code.upper()) is not None:
            return True
    return False


def _setup_devices(console, fn, device=None):
    """Which devices this function's report covers."""
    from .console import FUNCTION_REQUIRES, MODULE_WIRES
    if device is not None:
        return [device]
    need = FUNCTION_REQUIRES.get(fn["function"])
    if not need:
        return [1]
    most = max(console.capacity(m) for m in need) or MODULE_WIRES.get(need[0], 1)
    return list(range(1, most + 1))


def _device_tag(console, fn, device):
    from .console import FUNCTION_REQUIRES
    letter = {"probe": "T", "liquid": "L", "vapor": "V", "gw": "G",
              "2wire": "C", "3wire": "H", "smart": "s", "plld": "Q",
              "wplld": "W", "vlld": "P", "pump": "S", "pumpmon": "r",
              "io": "I", "relay": "R"}
    need = (FUNCTION_REQUIRES.get(fn["function"]) or ("probe",))[0]
    tag = letter.get(need, "T")
    label = console.text({"T": "602", "L": "702", "V": "707", "G": "712",
                          "C": "742", "H": "747", "s": "722", "Q": "782",
                          "W": "7A2", "P": "760", "I": "802", "R": "807",
                          "r": "7C5"}.get(tag, "602"), device)
    return f"{tag} {device}:{label}" if label else f"{tag} {device}"


def _numbered(text, n):
    """"Station Header Line 1" for line 2 is "Station Header Line 2"."""
    import re
    pattern = r"\b1\b"
    if re.search(pattern, text):
        return re.sub(pattern, str(n), text, count=1)
    return f"{text} {n}"


def _setup_rows(console, fn, device, numbered=False):
    """One device's programmed values, skipping what it is not showing."""
    from . import fieldio
    from .console import FIELDS
    rows = []
    for st in console.visible_steps(fn, device or 1):
        code = st.get("code")
        if not code:
            continue
        text = st["text"].split("(")[0].strip()
        wide = code[4:6] == "00"
        # a console-wide screen that carries its own number, AUTO SHIFT #2
        # CLOSING is S79402, is one row, not four
        own = st.get("scope") == "console" and not st.get("repeat")
        devices = [None] if (wide or own or not numbered) else [1, 2, 3, 4]
        for one in devices:
            n = one if one is not None else (device or 1)
            full = code if (wide or own) else f"{code[:4]}{n:02d}"
            raw = console.values.get(full.upper())
            if raw is None:
                continue               # nothing programmed is not a value
            f = FIELDS.get(st.get("field") or code)
            if f and f.get("kind") == "slots":
                wires = f.get("slots") or 4
                if (n - 1) % wires:
                    continue           # the module's slots, once per module
                value = console.slot_text(code[1:4], wires,
                                          ((n - 1) // wires) * wires)
            elif f and f.get("kind") == "profile":
                from .console import Console as _C
                value = _C.PROFILE_NAME[console.tank_profile(n)]
            else:
                value = fieldio.decode(f, full, raw) if f else raw.strip()
            if value == "":
                continue
            label = text if one is None else _numbered(text, one)
            rows.extend(_setup_row(label, value))
    return rows


def _setup_row(label, value):
    """One line of the Setup Data Report, on paper 40 characters wide.

    The setting on the left and what it is set to against the right margin,
    which is how a report reads on a narrow roll; a value with no room left
    for it goes on its own line under the setting rather than off the paper.
    """
    label, value = label[:30], str(value)
    gap = WIDTH - 2 - len(label) - len(value)
    if gap >= 1:
        return [f"  {label}{' ' * gap}{value}"]
    return [f"  {label}", f"{value[:WIDTH]:>{WIDTH}}"]


def leak_tests(console, kind="tank", only=None):
    """The leak test report, which is what PRINT gives you at a results step.

    The columns are function 208's: test type, when it started, the result,
    the rate it measured, how long it ran and the volume the tank held.
    """
    titles = {"tank": "IN-TANK LEAK TEST RESULTS",
              "plld": "PRESSURE LINE LEAK TEST RESULTS",
              "wplld": "WPLLD LINE LEAK TEST RESULTS",
              "vlld": "LINE LEAK DETECT RESULTS"}
    out = header(console, titles.get(kind, "LEAK TEST RESULTS"))
    devices = sorted(d for k, d in console.leaks.results if k == kind)
    running = sorted(d for k, d in console.leaks.running if k == kind)
    if only:
        devices = [d for d in devices if d == only]
        running = [d for d in running if d == only]
    if not devices and not running:
        out.append("NO TEST DATA AVAILABLE")
        return out
    letter = {"tank": "T", "plld": "Q", "wplld": "W", "vlld": "P"}[kind]
    for device in sorted(set(devices) | set(running)):
        label = console.text("602" if kind == "tank" else "782", device)
        out.append("")
        out.append(f"{letter} {device}:{label or ''}".rstrip())
        run = console.leaks.active(kind, device)
        if run is not None and kind in console.leaks.LINES:
            # a line under test has no hours left to quote: it runs until the
            # line is thermally stable, so it says what it is doing instead
            out.append(f"  TEST ACTIVE  {run.rate_key.upper()}"
                       f"  {run.state}")
        elif run:
            out.append(f"  TEST ACTIVE  {run.rate_key.upper()}"
                       f"  {run.hours:g} HOURS")
        out.append("  TEST TYPE  RESULT     RATE  HOURS   VOLUME")
        for _key, res in sorted((console.leaks.results.get((kind, device))
                                 or {}).items()):
            out.append(f"  {res.rate_key.upper():10s}{res.result:9s}"
                       f"{res.rate:6.2f}{res.hours:7.1f}{res.volume:9.0f}")
        if (kind, device) in console.leaks.disabled:
            out.append("  LINE SHUT DOWN BY FAILED TEST")
    return out


def delivery(console, tank, record):
    """INVENTORY INCREASE, the report a delivery prints by itself."""
    label = console.text("602", tank) or f"TANK {tank}"
    out = header(console, f"T{tank}: {label[:16]}")
    out.append("INVENTORY INCREASE")
    for name, snap in (("INCREASE START", record.start),
                       ("INCREASE END", record.end)):
        if not snap:
            continue
        out.append("")
        out.append(name)
        out.append(clock_words(snap["at"]))
        out.append(f"  VOLUME  = {snap['volume']:9.0f} GALS")
        out.append(f"  HEIGHT  = {snap['height']:9.2f} INCHES")
        out.append(f"  WATER   = {snap['water']:9.2f} INCHES")
        out.append(f"  TEMP    = {snap['temp']:9.1f} DEG F")
    out.append("")
    out.append(f"GROSS INCREASE  = {record.amount:9.0f}")
    out.append(f"TC NET INCREASE = {record.tc_amount:9.0f}")
    if record.ticket is not None:
        out.append(f"TICKETED VOLUME = {record.ticket:9.0f}")
        out.append(f"VARIANCE        = {record.variance():9.0f}")
    return out


def deliveries(console, tanks=None):
    # "PRINT - Deliveries to all tanks", or to the one selected
    """Every delivery the console is holding, as PRINT gives them."""
    tanks = tanks or sorted(console.tank_level)
    out = header(console, "DELIVERY REPORT")
    for tank in tanks:
        records = console.deliveries.records.get(tank) or []
        label = console.text("602", tank) or f"TANK {tank}"
        out.append("")
        out.append(f"T {tank}:{label}")
        if not records:
            out.append("  NO DELIVERY DATA AVAILABLE")
        for record in records:
            when = clock_words(record.end["at"])
            out.append(f"  {when}")
            out.append(f"    GROSS {record.amount:9.0f}"
                       f"   TC {record.tc_amount:9.0f}")
            if record.ticket is not None:
                out.append(f"    TICKET {record.ticket:8.0f}"
                           f"   VAR {record.variance():8.0f}")
    return out


def ticketed(console, tanks=None):
    """TICKETED DELIVERY REPORT, ticket against gauge."""
    tanks = tanks or sorted(console.tank_level)
    out = header(console, "TICKETED DELIVERY REPORT")
    out.append(console.deliveries.ticketed_report(tanks))
    return out


def csld(console, tanks=None):
    """CSLD TEST RESULTS, which the console prints every 24 hours anyway."""
    tanks = tanks or sorted(console.tank_level)
    out = header(console, "CSLD TEST RESULTS")
    out.append(console.csld.report(tanks))
    return out


def accuchart(console, tanks=None, what="status"):
    """The three AccuChart reports, as IB91, IB93 and IB94 print them."""
    tanks = tanks or sorted(console.programmed_tanks())
    chart = console.accuchart
    rows = {"diagnostics": chart.diagnostics_rows,
            "status": chart.status_rows,
            "history": chart.history_rows,
            "data": chart.calibration_data_rows}[what](tanks)
    out = header(console, rows[0])
    out += [row for row in rows[1:]]
    return out


def accuchart_update(console, tank, when):
    """"Each time an AccuChart calibration is updated, a user notification
    message is sent to the local printer."

    The manuals never print that message, so this is the simulator's own and
    it is recorded as such in UNKNOWNS.md: the tank, the time, and what the
    calibration moved, which is what a notification is for.
    """
    entry = console.accuchart.state(tank)
    label = console.text("602", tank) or f"TANK {tank}"
    out = header(console, "ACCUCHART CALIBRATION UPDATE")
    out.append(f"T {tank}:{label}")
    out.append(clock_words(when))
    out.append("")
    out.append(f"UPDATE NUMBER {entry.updates}")
    out.append(f"DIAMETER      {entry.chart.diameter:10.2f}")
    out.append(f"FULL VOLUME   {entry.chart.capacity:10.0f}")
    out.append(f"PROBE OFFSET  {entry.chart.offset:10.2f}")
    out.append(f"FITNESS       {entry.chart.fitness:10.2f}")
    return out


def shift(console, tanks=None, previous=None):
    """SHIFT RECONCILIATION, the eight numbers and a line to sign.

    The closed shift if there is one, "a Shift Reconciliation Report for the
    previous shift": and the one running otherwise, so the report is not
    empty on a console nobody has closed yet.
    """
    tanks = tanks or sorted(console.tank_level)
    if previous is None:
        previous = any(console.bir.last(t) for t in tanks)
    out = header(console, "SHIFT RECONCILIATION")
    out.append(console.bir.report(tanks, previous=previous))
    return out


def meters(console):
    """What each meter has put through itself."""
    out = header(console, "METER TOTALS")
    out.append(console.bir.meter_report())
    return out


def sensors(console, kind=None, only=None):
    """SENSOR STATUS, "status for all sensors", or for the selected one."""
    titles = {"liquid": "LIQUID SENSOR STATUS", "vapor": "VAPOR SENSOR STATUS",
              "gw": "GROUNDWATER SENSOR STATUS",
              "2wire": "2-WIRE CL SENSOR STATUS",
              "3wire": "3-WIRE CL SENSOR STATUS",
              "smart": "SMART SENSOR STATUS"}
    out = header(console, titles.get(kind, "SENSOR STATUS REPORT"))
    found = [(mod, n, label) for mod, n, label in console.programmed_sensors()
             if (kind is None or mod == kind) and (only is None or n == only)]
    if not found:
        out.append("NO SENSORS PROGRAMMED")
        return out
    letter = {"liquid": "L", "vapor": "V", "gw": "G", "2wire": "C",
              "3wire": "H", "smart": "s"}
    for mod, n, label in found:
        out.append(f"{letter.get(mod, 'L')} {n}:{label[:18]:18s} "
                   f"{console.sensor_reading(mod, n)}")
    return out


def fuel(console, only=None):
    """FUEL MANAGEMENT REPORT: what is left and how long it lasts."""
    out = header(console, "FUEL MANAGEMENT REPORT")
    tanks = console.programmed_tanks()
    for n, (label, full) in sorted(tanks.items()):
        if only and n not in only:
            continue
        volume = console.tank_level.get(n, {}).get("volume", 0.0)
        sales = console.limit("683", n) or 0.0
        out.append("")
        out.append(f"T {n}:{label}")
        out.append(f"  INVENTORY   = {volume:9.0f} GALS")
        out.append(f"  95% ULLAGE  = {max(full * 0.95 - volume, 0):9.0f} GALS")
        out.append(f"  AVG SALES   = {sales:9.0f} GALS")
        out.append("  DAYS REMAIN = " + (f"{volume / sales:9.1f}" if sales
                                         else "  NO DATA"))
    return out


def relays(console, kind="relay"):
    """Output relay setup, or what the pump relay monitors are reading."""
    if kind == "pumpmon":
        out = header(console, "PUMP RELAY MONITOR STATUS")
        for n in range(1, console.capacity("pumpmon") + 1):
            label = console.text("7C5", n) or f"MONITOR {n}"
            out.append(f"r {n}:{label[:18]:18s} "
                       + ("ON" if console.relays.get(n) else "OFF"))
        if console.capacity("pumpmon") == 0:
            out.append("NO PUMP RELAY MONITOR FITTED")
        return out
    out = header(console, "OUTPUT RELAY SETUP")
    for n in range(1, max(console.capacity("relay"),
                          console.capacity("io")) + 1):
        label = console.text("807", n) or f"RELAY {n}"
        state = "ON" if console.relays.get(n) else "OFF"
        out.append(f"R {n}:{label[:18]:18s} {state}")
    return out


def service_codes(console):
    """The service code list the Service Report offers to print."""
    out = header(console, "SERVICE CODE LIST")
    out.append("PREDEFINED CODES ARE IN")
    out.append("QUICK HELP 577013-874")
    out.append("")
    out.append("USER DEFINED: 9901-9999")
    return out


def alarm_history(console, letter=None, system=False):
    """The alarm history for one device type, or the system's own.

    "Date and time alarm occurred": the console keeps a record of when each
    one posted, which is what this report is, newest first.
    """
    from .console import STATUS_DEVICE_CODE
    from .wire import _when
    out = header(console, "ALARM HISTORY REPORT")
    if system:
        # the console's own alarms, which have no device against them
        wanted = {"01"}
    else:
        wanted = {aa for aa, code in STATUS_DEVICE_CODE.items()
                  if letter and code == letter}
    shown = [r for r in console.alarm_log
             if not wanted or r["aa"] in wanted]
    if not shown:
        out.append("NO ALARM HISTORY")
        return out
    for record in shown:
        described = describe_alarms(
            [record["aa"] + record["nn"] + record["tt"]])
        if not described:
            continue
        out.append(described[0]["screen"][:24])
        out.append("  " + _when(record["at"]))
    return out


# ---------------------------------------------------------------------------
# Reconciliation Mode. The operator's manual prints each of these in full, and
# they are a column of labelled figures rather than a table: "OPENING VOLUME:"
# on one line and "5511 GALS" right-aligned under it.
# ---------------------------------------------------------------------------
PERIOD_WORD = {"shift": "SHIFT", "daily": "DAILY", "weekly": "WEEK",
               "periodic": "PERIODIC"}


def _figure(out, label, value, unit="GALS"):
    """"OPENING VOLUME:" over its number, which is how the console lays it
    out on a 40 column roll. Gallons are whole; inches are not."""
    out.append(f"{label}:")
    if unit == "INCH":
        out.append(f"{value:22.2f} {unit}")
    else:
        out.append(f"{value:22.0f} {unit}")
    out.append("")


def _when_block(out, row):
    out.append("OPENING DATE & TIME:")
    out.append(clock_words(row["opened"]))
    out.append("")
    out.append("CLOSING DATE & TIME:")
    out.append(clock_words(row["closed"]))
    out.append("")


def _volumes_are(console):
    """"VOLUMES ARE STANDARD": or TC, if BIR was set to TC VOLUME."""
    tc = (console.values.get("S79F00") or "").strip().endswith("1")
    return "VOLUMES ARE " + ("TC" if tc else "STANDARD")


def _label(console, tank):
    return console.text("602", tank) or ""


def reconcile(console, tanks=None, kind=None, previous=None):
    """The Reconciliation Report, per product, in the manual's own layout."""
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    title = ("SHIFT RECONCILIATION" if kind == "shift"
             else PERIOD_WORD[kind] + " RECONCILIATION")
    out = header(console, title)
    for tank in tanks:
        row = console.bir.row(tank, kind, previous)
        out.append("")
        out.append(f"T {tank}:{_label(console, tank)}".rstrip())
        out.append("")
        if row is None:
            out.append("NO SHIFT DATA AVAILABLE")
            continue
        _when_block(out, row)
        _figure(out, "OPENING VOLUME", row["opening"])
        _figure(out, "DELIVERIES", row["deliveries"])
        _figure(out, "METERED SALES", row["sales"])
        _figure(out, "MANUAL ADJUSTMENTS", row["adjust"])
        _figure(out, "CALCULATED INVNTRY", row["calculated"])
        _figure(out, "GAUGED INVNTRY", row["physical"])
        _figure(out, "WATER HEIGHT", row["water"], "INCH")
        _figure(out, "VARIANCE", row["variance"])
        if kind == "periodic":
            _figure(out, "THRESHOLD", console.bir.threshold(row))
    return out


def _variance_head(console, tank, title, kind, previous):
    """"PROD 1:UNLEADED GASOLIN" over the report's name and its period."""
    which = "PREVIOUS " if previous else "CURRENT "
    return [f"PROD {tank}:{_label(console, tank)}".rstrip(), "", title,
            which + PERIOD_WORD[kind], "", console.clock_text(),
            _volumes_are(console), ""]


def delivery_variance(console, tanks=None, kind=None, previous=None):
    """DELIVERY VARIANCE: what the tickets said against what the gauge saw."""
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    out = []
    for tank in tanks:
        row = console.bir.row(tank, kind, previous)
        out += _variance_head(console, tank, "DELIVERY VARIANCE", kind,
                              previous)
        if row is None:
            out.append("NO DATA AVAILABLE")
            out.append("")
            continue
        _when_block(out, row)
        var = console.bir.analysis(row)
        sales = row["sales"]
        out.append(f"TICKET VOL : {row['ticketed']:9.0f} GAL")
        out.append(f"GAUGED VOL : {row['deliveries']:9.0f} GAL")
        out.append(f"DLVY VAR   : {var['delivery_var']:9.0f} GAL")
        pct = (var["delivery_var"] / sales * 100.0) if sales else 0.0
        out.append(f"% VAR SALES: {pct:9.2f}%")
        out.append("")
    return out


def book_variance(console, tanks=None, kind=None, previous=None):
    """BOOK VARIANCE: the gauge against the book the tickets and meters keep."""
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    out = []
    for tank in tanks:
        row = console.bir.row(tank, kind, previous)
        out += _variance_head(console, tank, "BOOK VARIANCE", kind, previous)
        if row is None:
            out.append("NO DATA AVAILABLE")
            out.append("")
            continue
        _when_block(out, row)
        var = console.bir.analysis(row)
        out.append(f"OPN GAUG VOL : {row['opening']:9.0f} GAL")
        out.append(f"METER SALES  : {row['sales']:9.0f} GAL")
        out.append(f"TICKET DLVY  : {row['ticketed']:9.0f} GAL")
        out.append(f"MANUAL ADJ   : {row['adjust']:9.0f} GAL")
        out.append(f"BOOK INV     : {console.bir.book(row):9.0f} GAL")
        out.append(f"GAUGED INV   : {row['physical']:9.0f} GAL")
        out.append(f"WATER HT     : {row['water']:9.2f} IN")
        out.append(f"VAR          : {var['book_var']:.0f} GAL "
                   f"{var['book_pct']:.1f}%")
        out.append("")
    return out


def variance_analysis(console, tanks=None, kind=None, previous=None):
    """VARIANCE ANALYSIS: the variance split into where it went.

    "Book variance, book variance %, delivery variance, sales variance,
    temperature variance, water change, unexplained variance": and under
    them the corrective actions and the leak test results the manual prints.
    """
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    out = []
    for tank in tanks:
        row = console.bir.row(tank, kind, previous)
        out += _variance_head(console, tank, "VARIANCE ANALYSIS", kind,
                              previous)
        if row is None:
            out.append("NO DATA AVAILABLE")
            out.append("")
            continue
        _when_block(out, row)
        var = console.bir.analysis(row)
        out.append(f"BOOK VAR      : {var['book_var']:9.0f} GAL")
        out.append(f"BOOK VAR %    : {var['book_pct']:9.2f} %")
        out.append(f"DLVY VAR      : {var['delivery_var']:9.0f} GAL")
        out.append(f"SALE VAR      : {var['sales_var']:9.0f} GAL")
        out.append(f"TEMP VAR      : {var['temp_var']:9.0f} GAL")
        out.append(f"WATER CHG     : {var['water_change']:9.2f} IN")
        out.append(f"UNEX VAR      : {var['unexplained']:9.0f} GAL")
        out.append("")
        actions = console.corrective_actions(tank, var)
        if actions:
            out.append("CORRECTIVE ACTIONS")
            out.append(_rule())
            out += actions
            out.append("")
        out.append("LEAK TEST RESULTS")
        out.append(_rule())
        out.append(f"T {tank}: {_label(console, tank)}".rstrip())
        out.append(f"PROBE SERIAL NUM {console.probe_serial(tank)}")
        out.append("")
        out += console.last_test_lines(tank)
        out.append("")
    return out


def adjusted_delivery(console, tank, record):
    """"When the system recognizes that a delivery occurred, an adjusted
    delivery report is automatically printed for single or manifolded tanks."""
    out = []
    for one in console.manifolded(tank):
        out.append(f"T {one}: {_label(console, one)}".rstrip())
    out.append("ADJUSTED DELIVERY REPORT")
    out.append(_rule())
    out.append("")
    out.append(console.clock_text())
    out.append("")
    out.append(f"DELIVERY VOLUME = {record.amount:.0f}")
    out.append(f"TC DLVY VOLUME = {record.tc_amount:.0f}")
    return out


def loads(console, tanks=None, index=None):
    """TANKER LOAD REPORT, in the layout the manual samples.

    "Press PRINT to print all Tanker Load Reports for all tanks in the
    system", one tank's worth from the tank screen, and one load from the
    load screen.
    """
    out = header(console, "TANKER LOAD REPORT")
    for tank in (tanks or sorted(console.tank_level)):
        records = console.loads.all(tank)
        if index is not None:
            one = console.loads.load(tank, index)
            records = [one] if one else []
        out.append("")
        out.append(f"T {tank}: {console.text('602', tank) or ''}".rstrip())
        if not records:
            out.append("NO LOAD DATA AVAILABLE")
            continue
        for record in records:
            out.append("")
            out.append(f"NUMBER: {record.number}")
            for name, snap in (("LOAD START", record.start),
                               ("LOAD END", record.end)):
                out.append("")
                out.append(f"{name}:")
                out.append(clock_words(snap["at"]))
                out.append("")
                out.append(f"VOLUME    = {snap['volume']:9.0f} GALS")
                out.append(f"TC VOLUME = {snap['tc']:9.0f} GALS")
                out.append(f"TEMP      = {snap['temp']:9.1f} DEG F")
            out.append("")
            out.append(f"TOTAL     = {record.total:9.0f} GALS")
            out.append(f"TC TOTAL  = {record.tc_total:9.0f} GALS")
    return out


def leak_history(console, kind="plld", only=None):
    """"the last 3.0 gph, the first 0.2 gph, and the first 0.1 gph test
    results for each month"."""
    titles = {"plld": "PRESSURE LINE LEAK TEST HISTORY",
              "wplld": "WPLLD LINE LEAK TEST HISTORY",
              "vlld": "LINE LEAK TEST HISTORY",
              "tank": "IN-TANK LEAK TEST HISTORY"}
    letter = {"plld": "Q", "wplld": "W", "vlld": "P", "tank": "T"}[kind]
    label_code = {"tank": "602", "plld": "782", "wplld": "7A2",
                  "vlld": "760"}[kind]
    out = header(console, titles.get(kind, "LEAK TEST HISTORY"))
    devices = sorted({d for k, d in console.leaks.history if k == kind})
    if only:
        devices = [d for d in devices if d == only]
    if not devices:
        out.append("NO TEST HISTORY")
        return out
    for device in devices:
        out.append("")
        out.append(f"{letter} {device}: "
                   f"{console.text(label_code, device) or ''}".rstrip())
        out.append("")
        last = console.leaks.last_pass(kind, device, "gross")
        out.append("LAST 3.0 GAL/HR PASS:")
        out.append(clock_words(last) if last else "NO PASS RECORDED")
        for rate_key, name in (("periodic", "0.20"), ("annual", "0.10")):
            out.append("")
            out.append(f"FIRST {name} GAL/HR PASS EACH MONTH:")
            out.append("")
            months = console.leaks.first_pass_each_month(kind, device,
                                                        rate_key)
            if not months:
                out.append("NO PASS RECORDED")
            for when in months:
                out.append(clock_words(when))
    return out


def vmc(console, only=None):
    """VMC REPORT: every controller the interface module carries, both sides.

    "You can generate a report for up to 18 VMC controllers."
    """
    out = header(console, "VMC REPORT")
    numbers = [only] if only else console.vmc_numbers()
    for number in numbers:
        for side in console.VMC_SIDES:
            out.append("")
            out.append(console.vmc_head(number, side))
            for what in ("status", "rate", "fuel", "error", "remain"):
                out.append(console.vmc_reading(number, side, what))
    return out


def line_diag(console, kind, device, which):
    """The four printouts behind the line leak diagnostic's PRESS <PRINT> screens.

    577013-344 Figures 9 to 12. The two pump-Off tests print their raw
    pressures, "This report contains the last 5 test passes, fails, and high
    pressure events"; the two precision tests print a leak rate as a ratio
    against the rate they were looking for, "the last 10 test results", over a
    block of running totals. Ratio is the whole verdict: "Ratio <1 Pass,
    >1 Fail".
    """
    ln = console.lines.line(kind, device)
    title = ("PRESSURE LINE LEAK DIAG" if kind == "plld"
             else "WPLLD LINE LEAK DIAG")
    out = header(console, title)
    out.append(f"{console.lines.code(kind)}{device}:")
    if which in ("gross", "mid"):
        return out + _pressure_diag(ln, which)
    return out + _precision_diag(console, ln, which)


def _pressure_diag(ln, which):
    """PON P1 P2, in passes, fails and high pressure events."""
    name = "3.0" if which == "gross" else "MID"
    records = ln.readings[which][-30:]
    out = []
    blocks = [(f"{name} TEST PASSES", [r for r in records
                                       if r.passed and not r.high]),
              (f"{name} TEST FAILS", [r for r in records
                                      if r.passed is False and not r.high])]
    if which == "gross":
        # "High Pressure Event Thresholds: Pon > 50 psi"
        blocks.append((f"{name} HI PRESSURE EVENTS",
                       [r for r in records if r.high]))
    for label, rows in blocks:
        out.append("")
        out.append(label)
        out.append("- " * 10)
        out.append("PON  P1         P2")
        for r in rows[-5:]:
            out.append(clock_words(r.when))
            out.append(r.line())
        if not rows:
            out.append("NO TEST DATA AVAILABLE")
    return out


def _precision_diag(console, ln, which):
    """0.20 or 0.10 TEST DIAG: the totals block, then the last ten results."""
    rate = "0.20" if which == "periodic" else "0.10"
    tally = ln.tally[which]
    rows = ln.cycles[which]
    out = ["", f"{rate} TEST DIAG", ""]
    out.append("CURRENT TEST:")
    started = ln.last_start.get(which)
    out.append("START TIME: " + (clock_words(started) if started else "NONE"))
    out.append(f"DURATION: {_days(console, started):>10d} DAYS")
    out.append(f"SEQUENTIAL PASSES: {tally['run']:>5d}")
    out.append(f"SEQUENTIAL FAILS: {tally['runfail']:>6d}")
    out.append(f"TOTAL PASSES: {tally['pass']:>10d}")
    out.append(f"TOTAL FAILS: {tally['fail']:>11d}")
    out.append("RESULT REASON CODE:")
    out.append("    " + ln.reason(which))
    verdict = ln.result.get(which)
    out.append("RESULT: " + ("NONE" if verdict is None
                             else "PASS" if verdict else "FAIL"))
    out.append("")
    out.append("LAST TEST:")
    out.append("PON RATIO DUR RESULT")
    if rows:
        out.append(clock_words(rows[-1].when))
        out.append("    " + rows[-1].line())
    else:
        out.append("NO TEST DATA AVAILABLE")
    out.append("")
    out.append(f"{rate} TEST RESULTS")
    out.append("- " * 11)
    out.append("    PON RATIO DUR RESULT")
    for row in rows[-10:]:
        out.append(clock_words(row.when))
        out.append("    " + row.line())
    if not rows:
        out.append("NO TEST DATA AVAILABLE")
    out.append("")
    # "Test aborts if Pon = P1 (P1 should be lower since pump is shut off
    # before P1 is measured)"
    aborts = sum(1 for r in rows if abs(r.pon - r.p1) < 0.001)
    out.append("NO-VENT TEST ABORTS:")
    out.append(f"{aborts} OUT OF {len(rows)} TEST")
    return out


def _days(console, started):
    """"DURATION: n DAYS", how long the current test has been going."""
    if not started:
        return 0
    import time as _time
    return int(max(0.0, _time.mktime(console.now()) - started) // 86400)
