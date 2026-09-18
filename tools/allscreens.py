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
"""Every screen this console can ever draw, gated ones included.

The panel only shows a screen whose condition holds -- "this message appears
only if you select METER DATA PRESENT: YES", "visible only if Relay
assigned" -- so walking the menu on one configuration reaches most of the
console and silently misses the rest. Roughly a fifth of the setup menu is
conditional.

So this walks it twice. Once on a console with every module fitted and every
software key present, which reaches the unconditional screens; then once per
hidden screen, on a console built to satisfy that screen's own condition,
which reaches the rest. What comes out is the whole surface: every screen,
with the two lines it draws and the condition that reveals it.

Used by `tools/build_citations.py` to look every line up in the manuals, and
by `tests/test_citations.py` to check that every line still has one.
"""
import os
import re
import struct
import time
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tls350sim import packed                                  # noqa: E402
from tls350sim.facepanel import CURSOR                        # noqa: E402
from tls350sim.console import (Console, SOFTWARE_MODULES, MODULES,  # noqa: E402
                               SETUP_MENU, DIAG_MENU, NORMAL_MENU, RECON_MENU)

# ---- what a screen IS, as against what it happens to be reading -----------
_NUM = re.compile(r"[0-9]+(?:[.,:][0-9]+)*")
# a part number, not a date: the first run is four or more digits
# or X's, which "MM-DD-YY" and "2-19-05" are not
_BLANK = re.compile(r"(?<![A-Z0-9])[-.:]{3,}(?![A-Z0-9])")
_PART = re.compile(r"[0-9X]{4,}(?:-[0-9A-Z]+)+")
_MASK = re.compile(r"(?<![A-Z])X+(?:[.,:/]X+)*(?![A-Z])")
# what the manuals write where a console writes a clock
_MONTH = re.compile(r"\b(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
                    r"[A-Z]*\b")
_WHEN = re.compile(r"\b(?:DD|YYYY|YY|HH:MM:SS|HH:MM|MM:SS|HH|MM|XM|AM|PM)\b")
# a meridiem stuck to the digits before it: `9:43AM`, `12:32PM`
_MERIDIEM = re.compile(r"#(?:AM|PM|XM)")
# A weekday the console SUBSTITUTES, which is one screen: `(Day) OPEN:
# (Date)`, 576013-610 Rev AC p.28-3. See UNKNOWNS A46. Left in, the citation
# for that screen would be a different one every day of the week -- the same
# argument `_MERIDIEM` makes about the hour the audit ran.
#
# **Deliberately narrow.** A weekday is a LABEL on other screens and must
# survive there: Fuel Manager draws seven of `LAST SALES-SUN: XXXX GALS`,
# one per day, and collapsing those would lose six screens and their
# citations. So this matches only a weekday that OPENS the line and is
# followed by the one word that screen uses.
_WEEKDAY = re.compile(r"^(?:SUN|MON|TUE|WED|THR|FRI|SAT)(?= OPEN\b)")


def template(line):
    """The screen behind a reading.

    `CNTR = 1 VALUE = 122814` and `CNTR = 5 VALUE = 4575` are the same screen
    showing two different moments, and the manual draws it a third way again,
    `CNTR = X VALUE = XXXXXX`. A citation belongs to the screen, not to one
    moment of it, so the key is the screen: a run of digits, a run of the
    manual's X's, and the placeholders it writes a date and time with all
    become a single `#`. What is left is the part the console owns and the
    site does not.
    """
    s = " ".join(str(line).split()).upper()
    # The cursor is a blink, not a screen. It is a solid block standing in
    # one cell of a field the console is waiting on, so the screen behind it
    # is the field with that cell still empty -- which is how the manuals
    # draw every one of these, `ENTER PASSCODE->______<` and not a shot
    # caught mid-blink. Without this the same screen cites twice, once per
    # half of the blink, and the lit half cites against nothing.
    s = s.replace(CURSOR, "_")
    # a value the console has not got yet, which it draws as dashes in the
    # shape of the value: `--:--` for a clock, `-.---` for a pressure. The
    # manual draws the same field as MM:SS and X.XXX.
    s = _BLANK.sub("#", s)
    # a part number is a run of at least three characters and then more runs
    # after hyphens: 346333-102-B on a console, XXXXXX-XXX-X in the manual
    s = _PART.sub("#", s)
    s = _MASK.sub("#", s)
    # a space sitting just inside a bracket is the artwork keeping a
    # proportional font in column, not part of the screen: the manual draws
    # "( MM:SS)" after a short label and "(MM:SS)" after a long one so the
    # digits land in the same place either way
    s = s.replace("( ", "(").replace(" )", ")")
    s = _MONTH.sub("MMM", s)
    s = _WEEKDAY.sub("DDD", s)
    s = _WHEN.sub("#", s)
    s = _NUM.sub("#", s)
    # a meridiem run onto the end of the digits, `9:43AM`, has no word
    # boundary in front of it and so survives `_WHEN`. It is part of the
    # clock, not part of the screen: leaving it in makes the citation for
    # `s 1: 3-29-05 9:43AM` a different one from the same screen read after
    # noon, which would make this whole audit depend on the hour it ran.
    return _MERIDIEM.sub("#", s)


MENUS = [("SETUP", SETUP_MENU, "steps"), ("NORMAL", NORMAL_MENU, "steps"),
         ("DIAGNOSTIC", DIAG_MENU, "screens"),
         ("RECONCILIATION", RECON_MENU, "steps")]

# `mdim` is here and not in COMM_BAYS because `MODULES` gives it the bay
# `power`: an EDIM lives in the comm bay, an MDIM in the power bay, and the
# comm-bay walk below could never reach one. Without it no console this walk
# built had ever had an MDIM in it, so the POWER-side half of every LINE
# DISABLE and OUTPUT RELAY screen went uncited while the COMMUNICATION-side
# half was cited off the EDIM -- an asymmetry that looked like a citation
# file with nothing missing. See FIDELITY V7.
CARDS = ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart", "plld",
         "wplld", "vlld", "io", "relay", "pump", "pumpmon", "mdim")

# The communication bay is FOUR slots and there are more comm cards than that
# with screens of their own, so "one of everything" is not a console that can
# be built: 577013-528 puts single-port modules in slots 1, 2 or 3 and a
# dual-port one in slot 4. The walk runs the bay twice instead, which is the
# same thing the conditional pass below does for a screen's own gate.
#
# The bays are written slot by slot, because a screen gated on the CARD IN
# THIS SLOT -- the modem's five, the satellite's DTR -- is drawn for the card
# on position 1 and nothing else. See FIDELITY M7.
COMM_BAYS = ({1: "rs232", 2: "modem", 3: "mt", 4: "vmc"},
             {1: "ssat", 2: "edim", 3: "rprinter", 4: "mt4"},
             # The third bay exists for one card. `WPLLD DIAGNOSTIC DATA`
             # and its three children are gated on the WPLLD Comm Board --
             # 576013-818 Rev AB Figure 6-2 says so twice, "the software
             # version number of the WPLLD Comm Module" and "error count
             # between console and WPLLD Comm module" -- and no cage above
             # has a slot free for it. A screen nothing in this walk can fit
             # the card for is a screen the citation audit cannot see. See
             # FIDELITY D10.
             {1: "wplldcom", 2: "modem", 3: "mt", 4: "vmc"})
DEVICE = 1


def a_console(path, comm=COMM_BAYS[0]):
    """A console with one of everything its cage can hold at once."""
    c = Console(path)
    c.board = "E6"
    for key in CARDS + tuple(comm.values()):
        c.modules[key] = 1
    # and nothing else in the comm bay: a fresh console comes with an RS-232
    # card already in it, and a fifth card in a bay of four slots pushes one
    # of these out of its seat and off the menu.
    for key, _n, _p, bay, _w, _m in MODULES:
        if bay == "comm" and key not in comm.values():
            c.modules[key] = 0
    c.comm_slots = dict(comm)
    c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
    # Pin the clock to noon on a FIXED day, so the walk draws the same
    # screens on every run. The templates mask the digits but keep AM/PM
    # literal, so a walk run in the morning would draw "... AM" where the
    # citations were taken "... PM" and read as stale -- which is why the
    # hour was pinned. The DAY had to go too: every live reading on this
    # console is a function of the clock, so `citations.json`'s `seen`
    # field -- the line the walker actually saw -- moved every time anybody
    # regenerated it. Twenty-eight of them churned in the run that added one
    # citation, and a real change is worth nothing if it arrives in a diff
    # nobody can read. Noon is unambiguously PM; the date is arbitrary and
    # only has to hold still.
    c.clock_offset = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1)) - time.time()
    # An ISD site whose setup verifies, so the status screens read PASS the
    # way the manual's own healthy-site examples do: one airflow-meter map
    # row is what the setup self-test wants to see.
    c.values["SV4201"] = "01" + "01" * 2 + ("0102" + "01" * 2) * 4 + " " * 34
    c.values["SV4201"] = c.values["SV4201"][:60]
    # a hose in the fuel hose table, so the grade-hose mapping steps that
    # gate on it are drawn and audited too
    c.set_setting("evr_fuel_pos", "01", 1)
    c.set_setting("evr_hose_label", "REGULAR", 1)
    # The relay test's third screen is reached by pressing ALARM/TEST,
    # which energises the relay: 576013-610 Rev AC p.22-1 draws it "ON -
    # PRESS ANY KEY", and a walk that never presses the key would audit the
    # OFF state no manual has a page for. See FIDELITY O6.
    c.relays[1] = True
    # Figure 6-4's own three Contractor ID keys, presented to the console
    # the way a contractor presents one: MAINT HARDWARE KEY BLOCK draws a
    # row per ACTIVE key now rather than three names copied off the figure,
    # so a console nobody has ever logged into has no rows to walk. They are
    # a level down, where this walk does not go -- see FIDELITY D15.
    for ident, label in (("A12345", "J SMYTHE"), ("A54321", "J DOE"),
                         ("A98765", "J CLARKE")):
        c.present_tracker_key(ident, label)
    # a meter mapped to tank 1, because the cage has a DIM in it and a DIM
    # with no meter draws `NO METERS MAPPED` -- a line of the console's own
    # invention where the figure draws `FP: XX  M: XX  =T X`
    c.meters = {1: 1}
    # and METER DATA PRESENT: YES, which is what puts that tank into the
    # reconciliation at all -- "If dispenser data for this tank is being
    # reported to the DIM ... this parameter MUST be set to YES". Without
    # it every Reconciliation Mode value screen draws NO DATA AVAILABLE,
    # which is fifteen cited screens the walk can no longer reach. It also
    # opens END FACTOR, which is the manual's own "This message appears
    # only if you select METER DATA PRESENT: YES". See FIDELITY G10.
    c.values["S61501"] = "011"
    # a Shift Start Time, which is what puts LAST-SHIFT INVENTORY on the
    # panel at all: "At least one Shift Start Time must be entered to
    # activate the Last Shift Inventory feature". See FIDELITY O22.
    c.values["S50201"] = "0600"
    # and ticketed delivery ON, which is what puts DELIVERY MAINTENANCE on
    # the panel: "Before you use this function, Ticketed Delivery must be
    # enabled in the Setup Mode". The DLVY ADJUSTMENT screen that hides is
    # drawn by the hidden-screen pass below. See FIDELITY O23.
    c.values["S51C00"] = "1"
    # a VST vapor processor, so PMC SETUP and its two threshold screens
    # are drawn and audited
    # a Mag sensor on the smart card, because both Mag sump functions are
    # gated on one: "This menu displays only if the console detects a Mag
    # Sump Sensor capable of leak detection". See FIDELITY U1b.
    c.values["S72301"] = "0103"
    # ... and a test on it that has passed, so p.23-1's LAST PASSED TEST and
    # p.24-3's TEST PASSED are drawn and audited: twelve inches of water
    # that held, run from three hours before the pinned noon.
    noon = c.clock_offset
    c.sumps.pour(1, 12.0)
    c.clock_offset = noon - 3 * 3600
    c.sumps.start(1)
    c.clock_offset += 600
    c.sumps.measure(1)
    c.clock_offset = noon
    c.sumps.tick()
    c.sumps.printed.clear()
    c.values["SV4000"] = "01"
    c.values["SV4400"] = "000000003DCCCCCD"
    return c


def selections(fn):
    """-> one `app.sel` update per branch a SELECTION screen opens.

    A function can carry a choice the panel makes rather than a value the
    site programmes -- DELIVERY MAINTENANCE's EDIT/VIEW against INSERT, the
    reconciliation reports' SHIFT against PERIODIC -- and the steps after it
    can be gated on the answer with `{"when": {"sel": ...}}`.
    `_selection_allows` filters on that, so a walk which never touches
    `app.sel` sees the DEFAULT branch and nothing else.

    That was invisible while one step in the whole package carried such a
    gate and its branch happened to be the default. It stopped being
    invisible when DELIVERY MAINTENANCE's two chains were separated: four
    screens the console draws became screens no walk could reach, and the
    audit reported them as cited lines gone stale. Yields `{}` first, so the
    default branch is still walked exactly as before. See FIDELITY O7.
    """
    yield {}
    for st in fn.get("steps") or ():
        key = st.get("sel")
        if not key:
            continue
        for choice in st.get("choices") or ():
            yield {key: choice}
        if st.get("choices_from") == "lines":
            # SELECT LINE's lines are the site's own, so the branch is one
            # of them: `_line_walk` programs it. FIDELITY O24.
            from tls350sim.ui import line_pick
            yield {key: line_pick(fn["scope"], DEVICE)}
    # And a STAGE, which no screen offers as a choice: Figure 6-27's ARE YOU
    # SURE and WORKING are drawn only after CHANGE and ENTER on the screen
    # before them, and the panel keeps which stage it is in as a selection.
    # FIDELITY D27.
    for st in fn.get("steps") or ():
        gate = st.get("when") or {}
        if gate.get("sel") and not any(s.get("sel") == gate["sel"]
                                       for s in fn.get("steps") or ()):
            for value in gate.get("is") or ():
                yield {gate["sel"]: value}


def _line_walk(c, fn, mode):
    """Program a line and its precision schedules while an Operating Mode
    line function is drawn, and hand back what takes them out again.

    SELECT LINE offers the site's lines and SELECT TEST TYPE offers only the
    rates a line's schedule allows, so a console with no line and no
    schedule draws neither `Q 1: PLLD #1` nor `0.2 GPH`. They cannot go on
    the walk console for good: Setup Mode would then draw `0.20 GPH TEST:
    MANUAL` where p.10-4's exactly-cited screen reads DISABLED. FIDELITY O24.

    And a console with no pressure line draws `SENSORS NOT CONFIGURED` on
    the function's own screen and nothing behind it, so PRESSURE LINE
    RESULTS wants the line too. The bare-console pass draws that screen.
    CLOSED U18.
    """
    kind = fn.get("scope") or fn.get("requires")
    if mode != "NORMAL" or kind not in ("plld", "wplld"):
        return lambda: None
    config, periodic, annual = {"plld": ("781", "78C", "783"),
                                "wplld": ("7A1", "7A3", "7AC")}[kind]
    want = {f"S{config}{DEVICE:02d}": f"{DEVICE:02d}1",
            f"S{periodic}{DEVICE:02d}": f"{DEVICE:02d}3",
            f"S{annual}{DEVICE:02d}": f"{DEVICE:02d}3"}
    old = {code: c.values.get(code) for code in want}
    c.values.update(want)

    def undo():
        for code, value in old.items():
            if value is None:
                c.values.pop(code, None)
            else:
                c.values[code] = value
    return undo


def open_gate(c, cond, device=DEVICE):
    """Make `cond` true, so the screen it guards can be drawn."""
    if not cond:
        return
    for also in cond.get("and") or []:
        # a screen can carry more than one condition, and all of them have
        # to be opened before it draws
        open_gate(c, also, device)
    if cond.get("chart_secured"):
        c.set_chart_code("123456")
    if cond.get("meter_events") or cond.get("last_event"):
        # "If there is data in Meter Events Table, you see" -- so there has
        # to be data in it before that screen exists to be walked. See
        # FIDELITY D14.
        c.bir.log_event(1, cond.get("last_event") or "end", 12.0)
    if cond.get("has_delivery"):
        # "this display is not shown if a delivery has not occurred": so the
        # console needs one before the screen exists to be walked. Pour
        # product in and let the delivery engine notice, which is what the
        # bench does. See FIDELITY O13.
        c.values[f"S607{device:02d}"] = f"{device:02d}" + packed.hexfloat(96.0)
        c.values[f"S610{device:02d}"] = f"{device:02d}01"
        c.tank_level.setdefault(device, {"volume": 2000.0, "water": 0.0})
        c.deliveries.tick()
        for volume in (5000.0, 5000.0):
            c.tank_level[device]["volume"] = volume
            c.clock_offset += 60.0
            c.deliveries.tick()
        c.clock_offset += 600.0
        c.deliveries.tick()
    if cond.get("no_module"):
        # The one gate that wants hardware TAKEN OUT. Everything else here
        # adds some, which is why SERVICE REPORT's two ENTER SERVICE ID
        # screens could not be drawn on any console this walk built: every
        # comm bay carries a Maintenance Tracker in one form or another, and
        # a dual-port MT counts as one through `comm_count`. Slots come out
        # until the console stops reporting the card. See FIDELITY V7.
        name = cond["no_module"]
        c.modules[name] = 0
        for slot, card in sorted(c.comm_slots.items()):
            if not c.has(name):
                break
            c.comm_slots.pop(slot, None)
            c.modules[card] = 0
    if cond.get("relay_assigned"):
        # "If pump relay assigned" against "If pump relay = NONE", Figure
        # 6-16's two columns, and `S7C6` is what says which -- a stored
        # SETTING where every other gate on this screen is a card, which is
        # why the builder could not satisfy it. See FIDELITY V7 and D1.
        #
        # `21` is a PLLD line, because that is what the figure's own example
        # monitors: it draws `Q 1: RELAY (IN): OFF`, and the letter follows
        # the WATCHED device (`wiresensors.MONITORED`), so a monitor set to
        # watch a relay draws `R` and matches no line on the page. The walk
        # should stand where the manual stands.
        c.values[f"S7C6{device:02d}"] = f"{device:02d}21{device:02d}"
    for key in cond.get("any_setting") or []:
        c.set_setting(key, (cond.get("is") or ["TRANSMIT"])[0],
                      device if cond.get("device") else 0)
    if cond.get("setting"):
        c.set_setting(cond["setting"], (cond.get("is") or [""])[0],
                      device if cond.get("device") else 0)
    if "profile" in cond:
        c.set_tank_profile(device, cond["profile"][0])
    if "code" in cond:
        code = cond["code"]
        full = (code if code[4:6] == "00" else f"{code[:4]}{device:02d}").upper()
        part = cond.get("part")
        if "is" in cond:
            want = cond["is"][0]
        else:
            want = "77" if "77" not in (cond.get("not") or []) else "88"
        pfx = f"{device:02d}" if c.is_prefixed(code[1:4]) else ""
        old = c.values.get(full, "")
        body = old[len(pfx):] if old.startswith(pfx) else ""
        if part:
            body = body.ljust(part[0] + part[1])
            body = (body[:part[0]] + want.ljust(part[1])[:part[1]]
                    + body[part[0] + part[1]:])
        else:
            body = want
        c.values[full] = pfx + body


def bay_for(cond):
    """The comm bay whose cards satisfy this screen's gate.

    The hidden-screen pass built every console on the FIRST bay, so a screen
    gated on a card that only ever sits in another one -- an EDIM, a remote
    printer, the WPLLD Comm Board -- could never be drawn however many other
    gates were opened for it. Ten of the fifteen screens the walk still gave
    up on were this. See FIDELITY V7.
    """
    wanted = set()

    def collect(one):
        if not one:
            return
        for also in one.get("and") or []:
            collect(also)
        for key in ("module", "board", "no_module"):
            value = one.get(key)
            if isinstance(value, str):
                wanted.add(value)
            elif value:
                wanted.update(value)

    collect(cond)
    if not wanted:
        return COMM_BAYS[0]
    for bay in COMM_BAYS:
        if wanted & set(bay.values()):
            return bay
    return COMM_BAYS[0]


def _lines(app):
    # A console sits on the Operating Mode status display until STEP is
    # pressed, so a walk that has not pressed it sees the clock on every
    # screen rather than the function it is standing in.
    app._entered = True
    app._sync_device()
    rows = list(app._lines()) + ["", ""]
    return rows[0][:24].rstrip(), rows[1][:24].rstrip()


def enumerate_screens(state_path):
    """-> [{mode, function, index, l1, l2, when}], every screen, once each."""
    real_time = time.time
    frozen = real_time()
    # STOP THE CLOCK for the whole walk.
    #
    # `a_console` pins the console's clock to noon, which pins the DATE but
    # not the moment: `clock_offset` is a shift and `Console.now()` still
    # reads `time.time()`, so the console clock ticks through the walk. Every
    # reading that WANDERS -- and `readings.wander` moves on the console's
    # clock with a half-hour period -- therefore has a different value at the
    # end of the walk than at the start, and screens are deduplicated by what
    # they DRAW. So the screen count depended on how fast the machine ran:
    # three walks in one process came back 879, 878 and 879, differing by one
    # `HC SENSOR 3.018%`. It moved by up to thirteen across a sitting.
    #
    # The distinct-LINE count never moved, because `template` masks every run
    # of digits -- which is the argument for keying the citations on the line
    # and quoting the line count rather than the screen count. This makes the
    # screen count mean something too. See FIDELITY V0.
    time.time = lambda: frozen
    try:
        return _walk(state_path)
    finally:
        time.time = real_time


def _stand_on(app, want, depth=0, second=None):
    """Put the panel on the step called `want`, wherever it is.

    Not always a step of the function any more: Setup Mode and Diagnostic
    Mode both put screens a level down behind a PRESS <ENTER> screen, and
    `steps()` offers one level at a time. So this walks the branches too.

    `second` is the screen's own second line, for the screens whose first
    line is a device and its label: a Vac sensor's figure has eleven screens
    headed `s 1: (Vac Sensor Label)`, and matching the head alone stood the
    walk on the first of them every time. FIDELITY D26.
    """
    path = list(app.subs)
    for k, x in enumerate(app.steps()):
        app.subs = list(path)
        app.step = k
        if ((x.get("text") or x.get("l1")) == want
                and (second is None or x.get("l2") == second)):
            return True
        # four levels: Figure 6-29 puts START MANUAL TEST: ALL under SELECT
        # VAC SENSOR under START MANUAL TEST under VAC SENSOR MANUAL TEST
        # under VAC SENSOR DIAGS. FIDELITY D28.
        parent = app._branch_at() if depth < 4 else None
        if parent is not None:
            app.subs = list(path) + [parent]
            if _stand_on(app, want, depth + 1, second):
                return True
    app.subs = list(path)
    return False


def _walk(state_path):
    from tls350sim.ui import SimApp, MODES, HEADER

    def fresh(comm=COMM_BAYS[0]):
        if os.path.exists(state_path):
            os.remove(state_path)
        return a_console(state_path, comm)

    out, seen = [], set()
    base = fresh()
    # One window for the whole walk. Tk does not enjoy being started once per
    # screen, and `reset_panel` puts the panel back to power-on without one.
    app = SimApp(base, 10099)
    try:
        # And the first bay once more with a V-R Polisher in place of the
        # VST, in Diagnostic Mode alone: PMC DIAGNOSTIC shows one processor's
        # screens or the other's, so a walk on a VST console never drew
        # 577013-937 Rev J Figure 49's menu, and none of it had been
        # audited. FIDELITY I11.
        variants = [(bay, None) for bay in COMM_BAYS] + [(COMM_BAYS[0], "05")]
        for bay, processor in variants:
            # The second bay is here for the four cards the first one has no
            # slot for; everything it draws that the first bay drew as well
            # is the same screen and is kept once.
            app.console = (base if bay is COMM_BAYS[0] and not processor
                           else fresh(bay))
            if processor:
                app.console.values["SV4000"] = processor
            for mi, mode in enumerate(MODES):
                if processor and mode != "DIAGNOSTIC":
                    continue
                app.mode = mi
                fns = app.functions()
                for fi, fn in enumerate(fns):
                  undo = _line_walk(app.console, fn, mode)
                  for picked in selections(fn):
                    app.reset_panel()
                    app.sel.update(picked)
                    app.mode, app.func, app.step = mi, fi, HEADER
                    steps = app.steps()
                    for si in range(HEADER, len(steps)):
                        app.mode, app.func, app.step = mi, fi, si
                        app.editing, app.buf, app.confirm, app.msg =                             False, "", None, ""
                        l1, l2 = _lines(app)
                        # by what the panel DRAWS, because `steps()` is
                        # already filtered by what this cage can show: the
                        # same index is a different screen in the two bays,
                        # and the four cards the second bay is here for are
                        # exactly the ones that shift every index after them.
                        which = (mode, fn["function"], l1, l2)
                        if which in seen:
                            continue
                        seen.add(which)
                        out.append({"mode": mode, "function": fn["function"],
                                    "index": si, "l1": l1, "l2": l2,
                                    "when": None})
                  undo()
            # And the screens a level DOWN, on THIS bay's console. The
            # figures put a screen you reach with ENTER in a column of its
            # own, and the top-level walk above never stands on one, so a
            # branch like Figure 6-4's key list was audited only where its
            # screens had been flattened into the top level by hand. Walking
            # them is the same three steps the panel takes: stand on the
            # parent, take the index `_diag_children` gives, and step through
            # what `steps()` then offers. See FIDELITY D15.
            #
            # This used to run ONCE, after the bay loop, on the first bay's
            # console. Nothing gated a child screen then. D10's four gates
            # do -- the DIM block wants a DIM and the WPLLD block wants the
            # WPLLD Comm Board, and neither is in the first bay -- so a
            # descent that only ever ran on one cage stopped reaching six
            # lines the console can still draw. See FIDELITY D10.
            #
            # Both modes that have levels, and as deep as the levels go.
            # Setup Mode gained the descent Diagnostic Mode had; p.6-5 puts
            # a branch inside a branch, AUTO TRANSMIT SETUP over TRANSMIT
            # MESSAGE SETUP over the twelve limits, so one level down is not
            # the bottom. See the setup-mode audit, SU12.
            def _descend(mode_name, fn, depth=0):
                path = list(app.subs)
                for si in range(len(app.steps())):
                    app.subs = list(path)
                    app.step = si
                    parent = app._branch_at()
                    if parent is None:
                        continue
                    app.subs = list(path) + [parent]
                    for sj in range(len(app.steps())):
                        app.step = sj
                        app.editing, app.buf, app.confirm, app.msg = \
                            False, "", None, ""
                        l1, l2 = _lines(app)
                        which = (mode_name, fn["function"], l1, l2)
                        if which in seen:
                            continue
                        seen.add(which)
                        out.append({"mode": mode_name,
                                    "function": fn["function"],
                                    "index": sj, "l1": l1, "l2": l2,
                                    "when": None})
                    if depth < 3:
                        _descend(mode_name, fn, depth + 1)
                app.subs = list(path)

            for mode_name in ("DIAGNOSTIC", "SETUP"):
                if processor and mode_name != "DIAGNOSTIC":
                    continue
                mi = MODES.index(mode_name)
                app.mode = mi
                for fi, fn in enumerate(app.functions()):
                  for picked in selections(fn):
                    app.reset_panel()
                    app.sel.update(picked)
                    app.mode, app.func, app.step = mi, fi, 0
                    _descend(mode_name, fn)
            app.reset_panel()
        app.console = base
        app.reset_panel()

        # And the function screens of a console with its cards fitted and
        # nothing programmed on them: `IN-TANK INVENTORY` over `NO ACTIVE
        # TANKS` and the pressure line functions over `SENSORS NOT
        # CONFIGURED`, which every console above has too much programmed to
        # draw. CLOSED U18.
        if os.path.exists(state_path):
            os.remove(state_path)
        bare = Console(state_path)
        bare.modules["plld"] = 1
        app.console = bare
        app.reset_panel()
        mi = MODES.index("NORMAL")
        app.mode = mi
        for fi, fn in enumerate(app.functions()):
            if not app._unconfigured(fn):
                continue
            app.reset_panel()
            app.mode, app.func, app.step = mi, fi, HEADER
            l1, l2 = _lines(app)
            which = ("NORMAL", fn["function"], l1, l2)
            if which in seen:
                continue
            seen.add(which)
            out.append({"mode": "NORMAL", "function": fn["function"],
                        "index": HEADER, "l1": l1, "l2": l2, "when": None})
        app.console = base
        app.reset_panel()

        # now the screens no configuration above could show
        hidden = []
        for mode, menu, key in MENUS:
            for fn in menu:
                for i, st in enumerate(fn.get(key, [])):
                    if not any(base.visible(st, dev) for dev in range(1, 17)):
                        hidden.append((mode, fn["function"], i, st))
        for mode, fnm, i, st in hidden:
            c = fresh(bay_for(st.get("when")))
            open_gate(c, st.get("when"))
            if not c.visible(st, DEVICE):
                out.append({"mode": mode, "function": fnm, "index": i,
                            "l1": None, "l2": None,
                            "when": st.get("when"), "unreachable": True})
                continue
            app.console = c
            app.reset_panel()
            app.mode = MODES.index(mode)
            fns = app.functions()
            fi = [k for k, f in enumerate(fns) if f["function"] == fnm]
            if not fi:
                continue
            app.func, app.device = fi[0], DEVICE
            want = st.get("l1") or st.get("text")
            if not _stand_on(app, want, second=st.get("l1") and st.get("l2")):
                continue
            app.editing, app.buf, app.confirm, app.msg = False, "", None, ""
            l1, l2 = _lines(app)
            out.append({"mode": mode, "function": fnm, "index": i,
                        "l1": l1, "l2": l2, "when": st.get("when")})
    finally:
        try:
            app.quit()
        except Exception:
            pass
        app.destroy()
    if os.path.exists(state_path):
        os.remove(state_path)
    return out


def distinct_lines(screens):
    """-> {template: [(mode, function, index, which, line), ...]}.

    Keyed by `template`, not by the literal line, so a clock or a sensor
    count does not make a screen look like a new one on every run.
    """
    seen = {}
    for s in screens:
        for which in ("l1", "l2"):
            line = (s.get(which) or "").rstrip()
            if not line:
                continue
            seen.setdefault(template(line), []).append(
                (s["mode"], s["function"], s["index"], which, line))
    return seen
