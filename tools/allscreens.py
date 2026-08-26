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

from tls350sim.console import (Console, SOFTWARE_MODULES,     # noqa: E402
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

CARDS = ("probe", "liquid", "vapor", "gw", "2wire", "3wire", "smart", "plld",
         "wplld", "vlld", "io", "relay", "pump", "pumpmon", "vmc", "mt",
         "rs232", "modem")
DEVICE = 1


def a_console(path):
    """A console with one of everything, which is the widest menu."""
    c = Console(path)
    c.board = "E6"
    for key in CARDS:
        c.modules[key] = 1
    c.software = {k: True for k, _n, _p in SOFTWARE_MODULES}
    c.values["S60201"] = "01REGULAR UNLEADED   "
    c.values["S60A01"] = "01" + struct.pack(">f", 10000.0).hex().upper()
    c.tank_level[1] = {"volume": 2500.0, "water": 0.0}
    # Pin the clock to noon, so the date-and-time screens read the same on
    # every run. The templates mask the digits but keep AM/PM literal, so a
    # walk run in the morning would draw "... AM" where the citations were
    # taken "... PM" and read as stale. Noon is unambiguously PM.
    noon = list(time.localtime())
    noon[3:6] = [12, 0, 0]
    c.clock_offset = time.mktime(time.struct_time(noon)) - time.time()
    # An ISD site whose setup verifies, so the status screens read PASS the
    # way the manual's own healthy-site examples do: one airflow-meter map
    # row is what the setup self-test wants to see.
    c.values["SV4201"] = "01" + "01" * 2 + ("0102" + "01" * 2) * 4 + " " * 34
    c.values["SV4201"] = c.values["SV4201"][:60]
    # a hose in the fuel hose table, so the grade-hose mapping steps that
    # gate on it are drawn and audited too
    c.set_setting("evr_fuel_pos", "01", 1)
    c.set_setting("evr_hose_label", "REGULAR", 1)
    # a VST vapor processor, so PMC SETUP and its two threshold screens
    # are drawn and audited
    c.values["SV4000"] = "01"
    c.values["SV4400"] = "000000003DCCCCCD"
    return c


def open_gate(c, cond, device=DEVICE):
    """Make `cond` true, so the screen it guards can be drawn."""
    if not cond:
        return
    if cond.get("chart_secured"):
        c.set_chart_code("123456")
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
    from tls350sim.ui import SimApp, MODES, HEADER

    def fresh():
        if os.path.exists(state_path):
            os.remove(state_path)
        return a_console(state_path)

    out = []
    base = fresh()
    # One window for the whole walk. Tk does not enjoy being started once per
    # screen, and `reset_panel` puts the panel back to power-on without one.
    app = SimApp(base, 10099)
    try:
        for mi, mode in enumerate(MODES):
            app.mode = mi
            fns = app.functions()
            for fi, fn in enumerate(fns):
                app.reset_panel()
                app.mode, app.func, app.step = mi, fi, HEADER
                steps = app.steps()
                for si in range(HEADER, len(steps)):
                    app.mode, app.func, app.step = mi, fi, si
                    app.editing, app.buf, app.confirm, app.msg =                         False, "", None, ""
                    l1, l2 = _lines(app)
                    out.append({"mode": mode, "function": fn["function"],
                                "index": si, "l1": l1, "l2": l2, "when": None})

        # now the screens no configuration above could show
        hidden = []
        for mode, menu, key in MENUS:
            for fn in menu:
                for i, st in enumerate(fn.get(key, [])):
                    if not any(base.visible(st, dev) for dev in range(1, 17)):
                        hidden.append((mode, fn["function"], i, st))
        for mode, fnm, i, st in hidden:
            c = fresh()
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
            steps = app.steps()
            si = [k for k, x in enumerate(steps)
                  if (x.get("text") or x.get("l1")) == want]
            if not si:
                continue
            app.step = si[0]
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
