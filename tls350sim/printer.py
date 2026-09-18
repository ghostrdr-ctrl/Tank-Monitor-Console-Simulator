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
import re
import time

from . import alarmreports
from . import wiretables
from . import leaktest
from . import masks
from . import screens
from .clock import clock_hhmm
from .clock import clock_words
from .console import DAY_NAMES
from .console import describe_alarms

# The roll behind the left door is 40 characters wide. Everything the console
# prints for itself is written to fit it; the reports it shares with the
# serial port are the serial port's width. The BIR reconciliation table is
# eight columns and seventy characters, and those come off the roll folded,
# because that is what a narrow printer does with a wide report.
# The roll is twenty four characters wide, and two independent pieces of real
# paper say so. The console draws its own ruler: under SYSTEM STATUS REPORT a
# real 2024 tape prints a spaced rule, and measured off the glyphs it is
# `- - - - - -  - - - - - -`, twenty four characters with a two space gap in
# the middle -- SETUP_RULE exactly, and no ink beyond column 24 on a scan that
# ran to 26. The real site tape in tests/tape/ agrees from the other side: its
# longest line is 24 and a hundred of its lines are exactly 24.
#
# This was 40, which is why the setup report looked like the odd one out at
# 24 and got a constant of its own. It is not the odd one out; the roll is
# just narrow, and SETUP_COLS below is the same number for the same reason.
#
# And a THIRD witness, found 2026-09-17 and a document rather than a
# measurement: 577013-369 Rev B is Veeder-Root's own A/E specification for a
# TLS-350, and its section 3.1.E requires "an integral, **24-character**,
# thermal report printer". Its 3.1.B does the same for the display -- "a
# two-line 24-character liquid crystal display" -- which is what closes the
# first half of UNKNOWNS A52. The paper was measured right before the
# specification was read; this is the citation catching up with the tape.
WIDTH = 24
# A folded line is NOT indented. The one wrap visible on real paper --
# "PRESSURE LINE LEAK TEST" over "RESULTS" -- has its continuation flush at
# column 1, measured to within half a point of the line above it.
FOLD = ""

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

# What a real console puts at the foot of a report. Every report on both of
# the compliance rolls ends with it, and it is how a technician tells where
# one report stops on a continuous roll. 23 characters, so it fits the 24
# the paper gives.
END_MARK = "* * * * * END * * * * *"


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
    """The line a real console draws under a report title.

    576013-610 prints a solid run of hyphens under INVENTORY REPORT, and
    this used to follow it. Two photographs of real paper -- a 2025 LIQUID
    STATUS slip and a SYSTEM STATUS REPORT off the 1996 roll, thirty years
    and two firmware revisions apart -- both draw the SPACED form instead,
    which is the one the display already uses. The manual's artwork is
    tidied; the paper is not. See FIDELITY W9.
    """
    return SETUP_RULE


def header(console, title=None, rule=False, restamp=False):
    """Every report starts with the station header and the date and time.

    Only a STATUS report is ruled, and this console used to rule all of
    them. On real paper of either era the line under the title appears on
    SYSTEM STATUS REPORT, LIQUID STATUS and CSLD TEST RESULTS and nowhere
    else -- not on INVENTORY REPORT, which 576013-610 draws with a rule, and
    not on LEAK TEST REPORT or either delivery slip. Two of the three follow
    the rule with a repeat of the stamp, which `restamp` adds. See
    FIDELITY W9.
    """
    out = []
    for line in range(1, REPEATS + 1):
        # All four, programmed or not. A real console feeds the blank
        # ones: on a compliance roll from a site that has set only
        # header line 1, three empty lines run between it and the
        # timestamp. Dropping them gave a one-line site a two-line
        # header where a real one gets five.
        out.append(console.text("503", line) or "")
    out.append("")
    out.append(console.clock_stamp())
    if title:
        out.append("")
        out.append(title)
    # Two reports have no title of their own -- 352 and 402 go straight from
    # the station header to their device line -- so a caller can ask for the
    # header alone. See FIDELITY W25.
    if rule:
        out.append(_rule())
        if restamp:
            out.append(console.clock_stamp())
    return out


# The inventory grid, measured off a photograph of real 2024 paper against
# its own character cells:
#
#     VOLUME    =   5318 GALS
#     ULLAGE    =    680 GALS
#     90% ULLAGE=     80 GALS
#     HEIGHT    =  73.29 INCHES
#     WATER VOL =      0 GALS
#     WATER     =   0.00 INCHES
#     TEMP      =   69.5 DEG F
#
# The label runs left in ten and the "=" is at column ten, which is why
# "90% ULLAGE" -- exactly ten characters -- sits hard against its own. The
# value is right justified in six and the unit follows, so the HEIGHT row is
# 24 characters, the width of the roll, and nothing here can fold. This
# console used to indent two and pad the value to nine, which folded every
# INCHES row. See FIDELITY W3.
def inv_row(label, value, unit):
    """One "LABEL = value UNIT" line of an inventory block."""
    return f"{label:<10}={value:>6} {unit}"


def density_row(density):
    """The DENSITY row, and the one row of the block that is not the grid.

    It cannot be. The inventory grid was measured off a photograph of
    real paper (FIDELITY W3): the label runs left in ten, the `=` sits at
    column ten, the value is right justified in six and the unit follows.
    That puts the widest rows on exactly the 24 columns the roll gives --
    `HEIGHT    = 29.02 INCHES` is 24 to the character, and so is WATER. The
    arithmetic for this row is 10 + 1 + 6 + 1 + 7, because `LBS/GAL` is one
    character wider than `INCHES`, and 25 does not go onto 24. Drawn in the
    grid it folds, and it folds between the value and its unit, which is
    certainly not what a console does.

    So this row is drawn the one way it is drawn anywhere: 576013-610 Rev AC
    p.4-2's own artwork, `DENSITY = 5.9987 LBS/GAL`, which comes to exactly
    24. Every other row keeps the measured grid, because for every other row
    the measurement is the better evidence and the artwork's padding is
    plainly typeset rather than printed -- the same page draws `HEIGHT  =
    29.02 INCHES` tight and `WATER   =          0.00 INCHES` loose, with
    identical value widths.

    This is the weakest row on the report and it is the only one with no
    paper behind it. See FIDELITY X8.
    """
    return f"DENSITY = {density:.4f} LBS/GAL"


def mass_density(console):
    """Is the Mass/Density feature enabled?

    Function 560, Set Mass/Density Enable/Disable. The panel's IN-TANK
    INVENTORY steps already read it -- DENSITY, MASS, NEXT DELIVER DENSITY
    and LAST DELIVERY DENSITY each carry `"when": {"code": "S56000", "is":
    ["1"]}` -- so this is that same gate, said once, for the paper.
    """
    return (console.values.get("S56000") or "").strip().endswith("1")


def inv_rows(console, n, full, tc=True):
    """A tank's inventory block, the seven rows a real slip carries.

    `tc` adds the TC VOLUME row, which 576013-610's own INVENTORY REPORT
    sample draws and the auto-printed leak test slip does not.

    MASS and DENSITY are the sample's other two, sitting between TC VOLUME
    and HEIGHT and marked "Only appears if Mass/Density feature enabled" --
    which is `S56000`, the same flag the panel's own DENSITY and MASS steps
    are gated on. See FIDELITY X8.
    """
    st = console.tank_level.get(n, {})
    # The water the console has FOUND, not where the float is resting.
    # `water_height` applies the Water Minimum Threshold -- 576013-623 Rev
    # AN p.7-16, "the water float is resting on a layer of debris on the
    # bottom of the tank" -- and this row took the raw float while the WATER
    # VOL row two lines below it went through `water_volume`, which does
    # apply it. So one report answered the same question both ways: `WATER
    # VOL = 0 GALS` over `WATER = 0.50 INCHES`. CLOSED Y7 claims "the water
    # volume, the water screen and both water alarms read it"; the printed
    # inventory did not. FIDELITY O27.
    vol, water = st.get("volume", 0.0), console.water_height(n)
    ninety = not (console.values.get("S56400") or "").strip().endswith("1")
    pct = "90" if ninety else "95"
    share = full * (0.90 if ninety else 0.95)
    # Whole-number quantities are TRUNCATED, not rounded, and the paper says
    # the same as the display because it is the same rule. See FIDELITY Y11.
    out = [inv_row("VOLUME", masks.whole(vol), "GALS"),
           inv_row("ULLAGE", masks.whole(max(full - vol, 0.0)), "GALS"),
           inv_row(f"{pct}% ULLAGE", masks.whole(max(share - vol, 0.0)),
                   "GALS")]
    if tc:
        out.append(inv_row("TC VOLUME", masks.whole(console.tc_volume(n)),
                           "GALS"))
    if mass_density(console):
        out.append(inv_row("MASS", masks.whole(console.product_mass(n)),
                           "LBS"))
        out.append(density_row(console.product_density(n)))
    out.append(inv_row("HEIGHT", f"{console.height_at(n, vol):.2f}", "INCHES"))
    out.append(inv_row("WATER VOL", masks.whole(console.water_volume(n)),
                       "GALS"))
    out.append(inv_row("WATER", f"{water:.2f}", "INCHES"))
    out.append(inv_row("TEMP", f"{console.product_temperature(n):.1f}",
                       "DEG F"))
    return out


# A leak test prints twice on its own, once when it starts and once when it
# stops, and on a real compliance roll those slips outnumber the reports two
# to one. Neither carries a station header or a rule -- they are not
# reports in the sense the inventory report is, they are the console saying
# what it just did.
#
# They DO carry the END mark, and this comment used to say they did not.
# `ui.py` has appended one to everything it prints since before either slip
# existed, so the comment and the code disagreed and had disagreed for as
# long as there were slips. The mark stays and the comment moves, on the only
# manual language anyone has found on the question: 576013-610 Rev AC p.20-2
# and p.20-3 call the START printout a REPORT in the console's own voice --
# "prints a report confirming that a test has started on all tanks". Not
# decisive, because the same manual calls the delivery slip a report too, and
# the OCR of the real roll is illegible in the gap between a START slip and
# whatever followed it. See FIDELITY H2 and W19, and UNKNOWNS A32.
def leak_start_slip(console, run):
    """START IN-TANK LEAK TEST, printed the moment a test begins.

        START IN-TANK LEAK TEST
        TEST BY PROGRAMMED TIME
        JUL 13, 2024 11:59 PM

        TEST LENGTH 2 HOURS

        T 2:DIESEL
        VOLUME    =   6665 GALS
        ...

    TEST LENGTH here carries no "=" and is whole hours and the word HOURS,
    where the report's row is "TEST LENGTH =    2.0 HRS". See FIDELITY W10.
    """
    label = console.text("602", run.device) or ""
    out = ["START IN-TANK LEAK TEST"]
    # Why the test started, from the test's own origin. This read
    # `manual_stop`, which is the panel's STOP MODE and not where the START
    # came from -- so a technician's own fixed-length test printed
    # TEST BY PROGRAMMED TIME and a serial one did too. See FIDELITY H1.
    line = leaktest.ORIGIN_LINE.get(getattr(run, "origin", "panel"))
    if line:
        out.append(line)
    out.append(clock_words(run.started))
    out.append("")
    out.append(f"TEST LENGTH {run.hours:.0f} HOURS")
    out.append("")
    out.append(f"T {run.device}:{label}".rstrip())
    full = console.capacity(run.device) or 0.0
    out.extend(inv_rows(console, run.device, full, tc=False))
    return out


def leak_stop_slip(console, device, when):
    """STOP IN-TANK LEAK TEST, the three lines that precede the report.

        STOP IN-TANK LEAK TEST
        T 1:UNLEADED
        JUL 14, 2024 1:59 AM

    On the real roll the LEAK TEST REPORT follows it immediately, with its
    own station header.
    """
    label = console.text("602", device) or ""
    return ["STOP IN-TANK LEAK TEST",
            f"T {device}:{label}".rstrip(),
            clock_words(when)]


# The four station header lines, the blank after them, and the stamp. What
# `body` drops so a second report can go under the first one's header.
STAMP_LINES = REPEATS + 2


def body(report):
    """A report with its station header block taken off, title kept."""
    return report[STAMP_LINES:]


def under_one_header(*reports):
    """Several reports on one tape, the way PRINT gives them.

    576013-610 on the PRINT key: "a copy of the system status [active alarms
    (if any), and in-tank inventory]" -- one tape, one header, both bodies.
    This console gave the status report alone and left the inventory to be
    fetched from its own function with a header of its own. See FIDELITY W4.
    """
    out = list(reports[0])
    for more in reports[1:]:
        out.extend(body(more))
    return out


def inventory(console, only=None):
    """INVENTORY REPORT, volume, ullage, height, water and temperature."""
    out = header(console, "INVENTORY REPORT")
    tanks = console.programmed_tanks()
    if only:
        tanks = {n: v for n, v in tanks.items() if n in only}
    # A report with nothing to list prints its heading and stops.
    # UNKNOWNS A17 retired five inventions of exactly this shape -- `NO
    # SALES DATA`, `NO DELIVERY DATA` and three more -- on the grounds that
    # "a per-feature phrase invented for each screen teaches a technician a
    # message no console ever shows", and named the console's whole
    # vocabulary for nothing-yet: NO TEST DATA AVAILABLE, NO RESULTS
    # AVAILABLE, NO IDLE DATA. None of those is about an empty LIST.
    # FIDELITY S18 then settled what a real console does with an empty
    # report, from a capture: `I11200` prints its heading and nothing at
    # all. See FIDELITY U5.
    if not tanks:
        return out
    for n, (label, full) in sorted(tanks.items()):
        out.append("")
        out.append(f"T {n}:{label}")
        out.extend(inv_rows(console, n, full))
    return out


def alarms(console):
    """The alarm or warning report, which is what ALARM/TEST prints.

    Three things, and both manuals count them the same way.
    576013-610 Rev AC p.29-1: "This report shows the type and location of
    the warning or alarm and the date and time it occurred." 576013-939
    Quick Help p.12 says it again independently: "the warning or alarm
    type, its location and the date and time the warning or alarm
    condition occurred."

    The slip carried two of the three. The only stamp on it was the
    header's, which is when the SLIP was printed -- and the occurrence time
    is the half a technician writes on the work order. The console has held
    it all along: `_log_alarms` stamps every `02` row and the Alarm History
    reports read exactly this. See the alarm-lifecycle audit, A7.

    The stamp's place on the slip is the console's own shape rather than a
    drawing, because no page draws this report at all -- its head is
    UNKNOWNS A35 for the same reason. `alarm_history` indents a stamp under
    the name it belongs to, so this one does too.
    """
    out = header(console, "ALARM/WARNING REPORT")
    shown = describe_alarms(console.compute_alarms())
    if not shown:
        out.append("ALL FUNCTIONS NORMAL")
        return out
    live = {a["aa"] + a["nn"] + a["tt"]
            for a in describe_alarms(console.conditions())}
    for a in shown:
        key = a["aa"] + a["nn"] + a["tt"]
        out.append("")
        out.append(a["screen"])
        when = _occurred(console, key)
        if when:
            out.append("  " + when)
        out.append("  " + ("ACTIVE" if key in live
                           else "CLEARED - NOT ACKNOWLEDGED"))
    return out


def _occurred(console, key):
    """When this alarm last came on, as the console writes a date.

    The newest `02` row for it -- "SS - Alarm State: 01=Alarm cleared,
    02=Alarm occurred" -- which is the same record I111, I112 and I206
    read. A condition with no row is one the log has aged out at 200
    entries, or one raised before the log existed; it prints no stamp
    rather than the slip's own printing time, which is the thing that was
    wrong with it.
    """
    from .wire import _when
    at = _occurred_at(console, key)
    return _when(at) if at is not None else ""


def _occurred_at(console, key):
    """The newest `02` row's stamp for this alarm, YYMMDDHHmm, or None."""
    for record in console.alarm_log:
        if record.get("state") != "02":
            continue
        if record["aa"] + record["nn"] + record["tt"] == key:
            return record["at"]
    return None


def isd_alarm_slip(console, key):
    """What an ISD alarm prints when it posts, 577013-800 Rev P p.20-27:

        ---- ISD SITE ALARM ----      ---- ISD HOSE ALARM ----
        ISD VAPOR LEAKAGE WARN        h 1: FP1 SUPER
        MMM DD, YYYY HH:MM XM         FLOW COLLECT FAIL
                                      MMM DD, YYYY HH:MM XM

    Figures 21 and 22 draw these as the "Printed message" of a warning
    posting and an alarm posting, beside the display's two lines. Every
    posting printed the ALARM/WARNING REPORT instead. Category 30 is the
    site and 31 the hose, whose line is the hose, its fuel position label
    and its hose label. FIDELITY I11.
    """
    alarm = describe_alarms([key])[0]
    try:
        # the alarm log keeps its stamp packed, YYMMDDHHmm
        at = time.mktime(time.strptime(_occurred_at(console, key) or "",
                                       "%y%m%d%H%M"))
    except ValueError:
        at = time.mktime(console.now())
    stamp = clock_words(at)
    name = alarm["description"].upper()
    if alarm["aa"] != "31":
        return ["---- ISD SITE ALARM ----", name, stamp]
    hose = int(alarm["tt"]) if alarm["tt"].isdigit() else 0
    fp = str(console.setting("evr_fuel_pos", hose, "")).strip()
    label = str(console.setting("evr_hose_label", hose, "")).strip()
    where = f"h {hose}: " + (f"FP{int(fp)} " if fp.isdigit() else "") + label
    return ["---- ISD HOSE ALARM ----", where.rstrip(), name, stamp]


# Figure 4's walk of CLEAR TEST AFTER REPAIR, as V85's test types.
ISD_CLEAR_ORDER = ("01", "02", "06", "04", "05", "03")


def isd_clear_dates(console):
    """TEST FAIL CLEAR DATES, 577013-819 Rev F p.35's example printout:

        TEST FAIL CLEAR DATES
        CONTAINMENT OVER PRESS
        02-06-11  12:43
        VAPOR COLLECTION TEST
        FP: 1 h:   1 MIDGRADE
        02-06-10 11:13

    Figure 4 puts PRINT on the CLEAR TEST AFTER REPAIR menu -- "See example
    printout at right" -- and nothing printed it. One block for the last
    clear of each selection, in the menu's order, and one per hose for a
    collection clear. The stamp is the ISD event log's YY-MM-DD HH:MM with
    one space; the page's two samples disagree about that gap (UNKNOWNS
    A58). Nothing cleared prints the title and nothing. FIDELITY I11.
    """
    from . import isd
    out = header(console)[:-1] + ["TEST FAIL CLEAR DATES"]
    for code in ISD_CLEAR_ORDER:
        clears = [r for r in console.isd_clears if r["test"] == code]
        if not clears:
            continue
        if code != isd.COLLECTION:
            out += [isd.CLEAR_MENU[code], _isd_clear_stamp(clears[0]["at"])]
            continue
        done = set()
        for record in clears:                          # newest first
            for hose, fp, label in _cleared_hoses(console, record):
                if hose in done:
                    continue
                done.add(hose)
                out += [isd.CLEAR_MENU[code],
                        f"FP: {fp} h:{hose:>4} {label}".rstrip(),
                        _isd_clear_stamp(record["at"])]
    return out


def pmc_diagnostics(console):
    """PMC DIAGNOSTICS, 577013-937 Rev J p.12-59 and p.12-60, in two forms.

    The membrane's, Figure 48:     The polisher's, Figure 49:

        PMC DIAGNOSTICS                PMC DIAGNOSTICS
        ----------------------         ----------------------
        PMC VERSION: XX.XX             PMC VERSION: XX.XX

        VAPOR PROCESSOR MODE           VAPOR PRESSURE INCHES H20: -X.XXX
        AUTOMATIC                      VEEDER-ROOT POLISHER LOAD:  24.9%
                                       VAPOR PROCESSOR MODE
        VAPOR PROCESSOR STATE          EFFLUENT EMISSION: X.XX LB/KGAL
        VP STATE ON                    AUTOMATIC
                                       VAPOR VALVE POSITION
                                       CURRENT   : CLOSED
                                       REQUESTED : CLOSED

                                       TEMP:  75.05 DEG F

    Both figures say PRINT "Prints out a copy of the PMC Diagnostic report",
    and PRINT printed the function's screens. The polisher's order, its
    `H20` and its width are the figure's own, read off the word positions
    rather than the text: EFFLUENT EMISSION does stand between the mode's
    label and its value, and the lines are wider than the roll, which folds
    them (UNKNOWNS A61). FIDELITY I11.
    """
    from . import isd
    head = header(console)[:-1] + ["PMC DIAGNOSTICS", "-" * 22,
                                   f"PMC VERSION: {isd.PMC_VERSION}", ""]
    mode = isd.VP_CONTROL[console.vp_control()]
    if (console.values.get("SV4000") or "00").strip()[-2:] not in isd.POLISHERS:
        return head + ["VAPOR PROCESSOR MODE", mode, "",
                       "VAPOR PROCESSOR STATE",
                       "VP STATE " + isd.VP_RUNNING[console.vp_running()]]
    fig = console.pmc_figures()
    return head + [
        f"VAPOR PRESSURE INCHES H20: {fig['vapor']:.3f}",
        f"VEEDER-ROOT POLISHER LOAD:  {fig['load']:.1f}%",
        "VAPOR PROCESSOR MODE",
        f"EFFLUENT EMISSION: {fig['effluent']:.2f} LB/KGAL",
        mode,
        "VAPOR VALVE POSITION",
        f"CURRENT   : {fig['valve_cur']}",
        f"REQUESTED : {fig['valve_req']}",
        "",
        f"TEMP:  {fig['temp']:.2f} DEG F"]


def _isd_clear_stamp(at):
    return time.strftime("%y-%m-%d %H:%M", time.localtime(at))


def _cleared_hoses(console, record):
    """[(hose, fuel position, label)] one collection clear reached.

    V85's own three cases: "FF=00, HH=00: All FP's and hoses are cleared",
    "FF=FP Label, HH=00: All hoses for the FP are cleared", and "FF=FP
    Label, HH=Hose Id: The selected hose is cleared."
    """
    fp, hose = record["fp"], record["hose"]
    out = []
    for device, position, label in console.isd_hoses():
        at = int(position) if position.isdigit() else 0
        if fp not in ("", "00") and at != int(fp):
            continue
        if hose not in ("", "00") and device != int(hose):
            continue
        out.append((device, at, label))
    return out


def status(console):
    """SYSTEM STATUS, the report the console sits on."""
    out = header(console, "SYSTEM STATUS REPORT", rule=True)
    shown = describe_alarms(console.compute_alarms())
    if not shown:
        out.append("ALL FUNCTIONS NORMAL")
    else:
        for a in shown:
            out.append(a["screen"])
    return out


def maintenance(console, start=None, end=None, most=20):
    """The white key's Maintenance Report, on paper.

    576013-610 Rev AC chapter 32. The SCREEN says `MAINTENANCE REPORT` and
    the paper says `MAINTENANCE HISTORY` -- Figure 32-2 prints function
    119's own title, which is the distinction W13 is about -- so the two
    names are both right and belong to different surfaces.

    The rows are the ones the serial port has served all along, through the
    renderer they now share: one report, read one way, whichever end of the
    console asks for it. See R3.
    """
    rows = [wiretables.heading("119")]
    for entry in console.maintenance_log(start, end, most):
        stamp = time.strptime(entry["at"], "%y%m%d%H%M")
        what = alarmreports.MAINTENANCE_TYPE.get(entry["type"], "")
        rows.append(f"{what:<20.20s}"
                    f"{clock_words(time.mktime(stamp)):25s}"
                    f"{alarmreports.maintenance_words(entry)}".rstrip())
    return header(console, "MAINTENANCE HISTORY") + rows


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


# A device head on the panel is "T1: PREMIUM" and on paper it is
# "T 1:PREMIUM" -- a space after the letter and none after the colon, the
# same inversion the delivery slip and the sensor rows had. The panel screen
# is right as it is; this is the report's form of it.
PANEL_HEAD = re.compile(r"^([A-Za-z])\s?(\d+):\s*(.*)$")


def report_head(line):
    """A panel device head, in the form the paper prints it."""
    hit = PANEL_HEAD.match(str(line))
    return f"{hit.group(1)} {hit.group(2)}:{hit.group(3)}".rstrip()         if hit else line


def setup_section(console, fn, device=None):
    """One function: its name, the rule, and the screens under it.

    The device head prints ONCE, at the top of that device's rows, and not
    again before every value. The tape's IN-TANK SETUP heads a tank with
    "T 1:PREMIUM" and then runs PRODUCT CODE, THERMAL COEFF, TANK DIAMETER
    and twenty more underneath it; this console drew the panel's two-line
    screen for each of them and repeated the head twenty-one times. That one
    difference is most of the gap between the 626 lines this section emitted
    and the 110 the paper prints. See FIDELITY T1.

    A section is not always one run of devices, either. COMMUNICATIONS SETUP
    is four: the port settings once per comm board, auto-transmit once for
    the console, the phone directory and auto-dial once per RECEIVER, and
    the end-of-message once again for the console. 576013-623 Rev AN walks
    it in exactly those pieces -- "When you have specified port settings for
    all the communication modules, press STEP in response to the PORT
    SETTINGS message to advance to the next communications setup function"
    -- and the tape prints it in them. See FIDELITY T4.
    """
    # The report's title is the function's name in every section but one.
    # The tape heads this block `LEAK TEST METHOD` where the console's own
    # function screen reads IN-TANK LEAK TEST SETUP, so the section carries
    # what the paper calls it. See FIDELITY T4.
    out = [(fn.get("print_title") or fn["function"])[:SETUP_COLS], SETUP_RULE]
    # A sub-heading belongs to the REPORT, not to a device: the tape prints
    # PORT SETTINGS: once and then every board under it, not once a board.
    # See FIDELITY T1.
    headed = set()
    opened = 0
    for family, run in _families(fn):
        # A run with no device in it has no device to be absent. The
        # comment on the gate below has said so since it was written and
        # only `devices: "console"` was reaching it: a section whose steps
        # are console-scoped in the menu data -- SYSTEM SETUP is all of
        # them -- fell through to the section's own devices and was gated
        # on whether anybody had stored a value.
        #
        # So on a console with nothing programmed the Setup Data Report
        # printed SYSTEM SETUP's title, its rule, and stopped, while the
        # glass one keypress away read `SYSTEM LANGUAGE / ENGLISH`,
        # `SYSTEM UNITS / U.S.` and `SYSTEM DATE/TIME FORMAT`. This
        # function's own docstring is the rule it was breaking -- "a
        # setting nobody has programmed is still on it, reading the default
        # the console reads" -- and the tape agrees: its COMMUNICATIONS
        # SETUP prints three boards, all at their defaults.
        #
        # The one sentence that could be read the other way is quoted
        # here so a reader can disagree: p.5-1 calls the report "a record
        # of all setup values ENTERED into this system". Against it: the
        # console is displaying those defaults, the tape prints defaulted
        # values, and both of this module's own comments say defaults
        # print. See CLOSED U35.
        whole = _console_run(run)
        for one in (_family_devices(console, fn, family, device)
                    if not whole else [1]):
            if family is None and not whole and not _programmed(console, run,
                                                                one):
                # A device nobody has programmed is not on the report. Only
                # the section's own devices are gated this way: a
                # console-wide run has no device to be absent and prints the
                # defaults the console reads, which is what the tape's
                # twelve auto-transmit limits are, and a receiver has
                # already been gated by being configured at all -- the
                # alarm-assignment run is one step with nothing stored
                # under it and belongs to the destination anyway.
                continue
            last_head, first = None, True
            for st in print_order(_printable_steps(console, run, one)):
                for again, n in enumerate(_repeats(st, one)):
                    brk = (st.get("print") or {}).get("break")
                    if brk:
                        # a new titled block opens with its device head,
                        # whoever was heading the last one
                        last_head = None
                    block = setup_block(console, fn, st, n)
                    if (len(block) == 2 and screens.is_label_step(st)
                            and PANEL_HEAD.match(str(block[1]))):
                        # The product label step. Its prompt is ENTER PRODUCT
                        # LABEL and its value is the device head itself, and
                        # the paper prints neither the prompt nor a second
                        # copy: the tank's block simply opens "T 1:PREMIUM".
                        # So the value IS the head.
                        #
                        # Asked of the STEP, not of the value's shape.
                        # `is_label_step` is the console's own test -- the
                        # code is in `LABEL_CODES` -- and this matched any
                        # two-line block whose second line looked like a
                        # device head. VMC SETUP's three serial-number
                        # screens draw `ADD VMC SERIAL NUMBER` over `x 1:`,
                        # which looks exactly like one, so the report threw
                        # the title away and printed `x 1:` three times with
                        # nothing to say which screen each was.
                        block = [report_head(block[1])]
                        last_head = block[0]
                    elif len(block) > 1:
                        head = report_head(block[0])
                        if head == last_head:
                            block = block[1:]
                        else:
                            block = [head] + list(block[1:])
                            last_head = head
                    gap = int((st.get("print") or {}).get("gap") or 0)
                    if again:
                        # A step's gap opens its RUN, not every line of it.
                        # The tape prints the four station header lines
                        # against each other -- account, name, street, city
                        # and registration with nothing between them -- and
                        # the four shift times the same way, one blank in
                        # front of SHIFT TIME 1 and none after it. Emitting
                        # the gap once per repeat put six blanks into
                        # SYSTEM SETUP that are not on the paper. The panel
                        # walks these with the arrow keys and the report
                        # cannot, which is why they are a run at all: see
                        # `_repeats`. FIDELITY T2.
                        gap = 0
                    section = (st.get("print") or {}).get("section")
                    fresh = bool(section and section not in headed)
                    if block and first:
                        # How much blank opens a section is the section's,
                        # and measured: the tape gives SYSTEM SETUP and LEAK
                        # TEST METHOD none, IN-TANK, LIQUID SENSOR and AUTO
                        # DIAL one, COMMUNICATIONS and RECONCILIATION two.
                        # One was emitted for all of them. A second device
                        # opens with its own count -- three between the
                        # tape's liquid sensors -- which is `print_gap`
                        # where it differs. See FIDELITY T2.
                        out.extend([""] * int(
                            fn.get("print_lead", 1) if opened == 0
                            else fn.get("print_gap",
                                        fn.get("print_lead", 1))))
                        first = False
                        opened += 1
                    elif block and not fresh:
                        # A blank line on the tape groups the rows: the water
                        # limits sit together, each inventory alarm gets its
                        # own block, and two blanks open a bigger group. They
                        # are not spacing, they are the paper's punctuation,
                        # and this console emitted none. See FIDELITY T2.
                        out.extend([""] * gap)
                    if block and brk:
                        # A section that becomes two titled blocks on the
                        # paper without becoming two functions on the panel.
                        # The tape closes COMMUNICATIONS SETUP, leaves one
                        # blank line rather than the twelve-line feed
                        # between sections, and heads AUTO DIAL ALARM SETUP
                        # over its own rule. The manual's function index
                        # files it as a step of COMMUNICATIONS SETUP, which
                        # is what it is here. See FIDELITY T4.
                        while out and not out[-1]:
                            out.pop()
                        out.extend(["", brk[:SETUP_COLS], SETUP_RULE, ""])
                        last_head = None
                    if fresh:
                        # Under a sub-heading the blanks fall AFTER it, and
                        # the device is headed again: the tape puts two
                        # blanks and `D 8:` between AUTO DIAL TIME SETUP:
                        # and the rows it covers.
                        #
                        # They fall BEFORE it as well, and by their own
                        # count. The tape's four sub-headings run 1, 2, 4 and
                        # 4 blanks in front against 1, 1, 2 and 2 behind, so
                        # the two are not one number and the front one is not
                        # derivable from the back: `INVENTORY ALARMS UNITS`
                        # takes one and one where `AUTO TRANSMIT SETTINGS:`
                        # takes two and one. Measured per section, in
                        # `lead`, and 0 where nothing has measured it -- a
                        # guessed blank is an invention on the paper the same
                        # way a guessed column is one on the wire. FIDELITY
                        # T2.
                        headed.add(section)
                        lead = int((st.get("print") or {}).get("lead") or 0)
                        out.extend([""] * lead)
                        out.extend([section] + [""] * (gap or 1))
                        last_head = None
                        block = _headed(console, fn, st, n, block)
                    out.extend(block)
    return out


def _headed(console, fn, step, device, block):
    """A block that has just lost its head to the dedup, given it back.

    A sub-heading starts a fresh block on the paper and the device is named
    again under it -- the tape prints `D 8:` beneath AUTO DIAL TIME SETUP:
    though it printed the same head a few rows earlier. A step that draws
    nothing has no head to give back.
    """
    drawn = setup_block(console, fn, step, device)
    if not drawn or not block or PANEL_HEAD.match(str(block[0])):
        # nothing to head, or the block already opens with the device head
        # -- the product-label case, where the value IS the head
        return block
    head = report_head(drawn[0])
    return block if block[0] == head else [head] + list(block)


def _families(fn):
    """A section's steps in consecutive runs, each with what it repeats over.

    A step says `devices` when its run is not the section's own: "console"
    for one pass whatever is fitted, "receiver" for the eight autodial
    destinations. Everything without one belongs to the section's devices,
    which is what every section but COMMUNICATIONS is made of.
    """
    runs, current, family = [], [], None
    for st in fn["steps"]:
        want = st.get("devices")
        if current and want != family:
            runs.append((family, current))
            current = []
        family = want
        current.append(st)
    if current:
        runs.append((family, current))
    return runs


def _console_run(steps):
    """Is this whole run the console's own, with no device anywhere in it?

    `screens.console_step` answers it per step, and a run that is entirely
    console-scoped has one pass and no device to gate on. The test has to
    be on EVERY step: a run that mixes the two -- FUEL MANAGEMENT SETUP,
    EVR/ISD SETUP and five more do -- is still the section's devices, and
    repeating its console screens once per device or printing them for a
    device nobody programmed are both wrong. Those are left exactly as they
    were.
    """
    return bool(steps) and all(screens.console_step(st) for st in steps)


def _family_devices(console, fn, family, device=None):
    """Which devices one run of steps covers."""
    if family == "console":
        return [1]
    if family == "receiver":
        # 576013-623 Rev AN: one receiver "and ... up to seven more". The
        # tape's console has configured the eighth and only the eighth and
        # prints `D 8:` alone, in all three of the places a destination
        # appears -- the phone directory, the auto-dial times and the alarm
        # assignments, which is a separate run of one step and could not be
        # gated by what is stored under it.
        return console.configured_receivers()
    return _setup_devices(console, fn, device)


def _printable_steps(console, steps, device):
    """The steps on the report, which is not quite the steps on the panel.

    A screen the panel gates on whether you can EDIT it still has a value,
    and the report prints it. 576013-635 Rev AA's display format for 551
    lists all six inventory alarm lines, and the tape prints all six
    under CONFIG: STANDARD -- where the panel only lets you walk the five
    custom ones when the config is CUSTOM.
    """
    return [st for st in steps
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
    for line in range(1, REPEATS + 1):
        # all four, programmed or not, the way the report header feeds them
        out.append(console.text("503", line) or "")
    out.append("")
    out.append(clock_words(console.now()))
    out.extend(["", "", ""])
    out.append(_centre("SYSTEM STATUS REPORT"))
    out.append(SETUP_RULE)
    shown = describe_alarms(console.compute_alarms())
    out.extend([a["screen"] for a in shown] or ["ALL FUNCTIONS NORMAL"])
    return out


# The Archive Utility's own paper. 576013-637 Rev M draws a record as the
# operation starts and another when it finishes, twice each: pp.4-5 for a
# save and pp.15-16 for a restore. They are short records with no station
# header, and the two flows are drawn differently, which this follows rather
# than tidies. A save's second line carries a colon and its stamp carries
# seconds -- "SAVE SETUP DATA: / START TIME: / MMM DD, YYYY HH:MM:SS XM" --
# where a restore's does neither, "RESTORE SETUP DATA / START TIME: /
# MMM DD, YYYY HH:MM XM". Each form is printed twice in that manual, so
# neither of them is a slip of the typesetting.
ARCHIVE_TITLE = "ARCHIVE UTILITY"


def archive_record(console, what, when, written=None):
    """One of the records an archive save or restore puts on the roll.

    `what` is "save" or "restore" and `when` is the manual's "START TIME" or
    "END TIME". A save's END TIME record carries the size of what went into
    the chip under it, "BYTES: XXXX"; no other record does. A clear gets no
    record at all, because 576013-637 draws none for one.
    """
    label = "SAVE SETUP DATA:" if what == "save" else "RESTORE SETUP DATA"
    stamp = console.clock_text() if what == "save" else console.clock_stamp()
    out = [ARCHIVE_TITLE, label, f"{when}:", stamp]
    if written is not None:
        out.append(f"BYTES: {written}")
    return out


def _centre(text):
    return text.rjust((SETUP_COLS + len(text)) // 2)


def _programmed(console, steps, device):
    """Has anybody put anything in this device's copy of these steps?

    A comm port is the exception: its settings have defaults and a real
    console prints them for every board in the bay whether or not anyone
    has touched them -- the tape prints three boards, all at their
    defaults. What gates a port is the card being fitted, and only the
    port run: the receivers beside it in the same section are gated the
    ordinary way, which is why the tape prints `D 8:` and no other.
    """
    if any((st.get("code") or "").startswith("S881") for st in steps):
        return console.comm_board_name(device) != "UNUSED"
    for st in steps:
        code = screens.code_for(console, st, device)
        if code and console.values.get(code.upper()) is not None:
            return True
    return False


# A leak test section is one set of conditions for the whole console or one
# per device, and the section's own first screen says which. 576013-623 Rev
# AN says it of the lines and means it of the tanks: "Whether you choose ALL
# LINES or SINGLE LINE, the procedure for specifying a set of test conditions
# is identical. The only difference is that the SINGLE LINE method requires
# you to specify multiple test conditions, one for each line", and for tanks,
# "If you choose SINGLE TANK, the tank number (for example TANK 1) replaces
# the phrase ALL TANK on each screen". The tape prints the block ONCE, over
# `TEST CSLD    : ALL TANK`. See FIDELITY T5.
ONE_SET_WHEN = {
    "IN-TANK LEAK TEST SETUP": ("tank_test_method", "ALL TANK"),
    "LINE LEAK TEST SETUP": ("line_test_method", "ALL LINES"),
}


def _setup_devices(console, fn, device=None):
    """Which devices this function's report covers."""
    from .console import FIELDS, FUNCTION_REQUIRES, MODULE_WIRES, MODULE_BAY
    if device is not None:
        return [device]
    one = ONE_SET_WHEN.get(fn["function"])
    if one:
        which, all_of_them = one
        default = FIELDS.get(f"set.{which}", {}).get("default", "")
        if console.setting(which, 0, default) == all_of_them:
            return [1]
    need = FUNCTION_REQUIRES.get(fn["function"])
    if not need:
        return [1]
    # capacity() is a card's WIRE count, and a communication board has none:
    # it is a port, not a run of terminals, so every comm card in MODULES
    # carries zero. That made this fall through to MODULE_WIRES, which is
    # zero for the same reason, and range(1, 1) is empty -- so a console with
    # an RS-232 card and a modem in it printed COMMUNICATIONS SETUP as a
    # title and a rule and no body at all, whatever was fitted. Where a card
    # has no wires, one card is one device.
    if all(MODULE_BAY.get(m) == "comm" for m in need):
        # A comm bay is not a run of terminals and it is not a run of CARDS
        # either: it is six POSITIONS across four slots, and which of them a
        # card answers on is where it sits. The tape's bay is `1`, `5`, `6`
        # -- a satellite in slot 1 and one dual-port module in slot 4 -- and
        # counting cards printed it as 1, 2, 3. Positions nothing is fitted
        # on are dropped by `_programmed`, which is what leaves the gap.
        # See FIDELITY M6 and M7.
        return sorted(console.comm_positions()) or [1]
    if all(not console.capacity(m) for m in need):
        most = sum(console.count(m) for m in need)
        return list(range(1, (most or 1) + 1))
    most = max((console.capacity(m) or console.count(m) for m in need),
               default=0) or MODULE_WIRES.get(need[0], 1) or 1
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


# What a real tank leak test slip prints, measured off two compliance rolls.
# The label runs left in twelve, the "=" is at thirteen, the value is right
# justified in seven and the unit follows -- 24 characters, the width of the
# roll:
#
#     TEST LENGTH =    2.0 HRS
#     STRT VOLUME = 2290.4 GAL
#
# Note GAL singular on the start volume where the inventory block says GALS,
# and the rate to two decimals. The 1996 firmware printed one decimal and
# GALS; this follows the 2024 paper, which is the software this console
# reports itself as. See FIDELITY W17 for the two eras.
def leak_row(label, value, unit):
    """One "LABEL = value UNIT" line of a leak test slip."""
    return f"{label:<12}={value:>7} {unit}"


# The words a real slip uses, against the ones the engine keeps. INVL is off
# a real 2024 slip; the manuals only ever show a pass.
SLIP_RESULT = {"PASSED": "PASS", "FAILED": "FAIL", "INVALID": "INVL"}


def leak_slip_rate(rate_key):
    """"0.20 GAL/HR", the way a slip names the rate it tested at."""
    from .leaktest import RATES
    rate = RATES.get(rate_key, 0.0)
    return f"{rate:.2f} GAL/HR" if rate < 1.0 else f"{rate:.1f} GAL/HR"


def tank_leak_report(console, only=None):
    """LEAK TEST REPORT, the slip a compliance inspector reads.

    Not a table. This used to print function 208's SERIAL columns onto paper
    -- TEST TYPE, RESULT, RATE, HOURS, VOLUME -- and a real console prints a
    block per tank, headed by the probe's serial number and giving the start
    time, the length, the starting volume and the result in words. Two
    compliance rolls thirty years apart agree on the shape.
    """
    out = header(console, "LEAK TEST REPORT")
    devices = sorted(d for k, d in console.leaks.results if k == "tank")
    if only:
        devices = [d for d in devices if d == only]
    if not devices:
        out.append("NO TEST DATA AVAILABLE")
        return out
    for device in devices:
        label = console.text("602", device) or ""
        out.append("")
        out.append(f"T {device}:{label}".rstrip())
        out.append(f"PROBE SERIAL NUM {console.probe_serial(device)}")
        results = (console.leaks.results.get(("tank", device)) or {})
        for _key, res in sorted(results.items()):
            out.append("")
            out.append("TEST STARTING TIME:")
            out.append(clock_words(res.started))
            out.append("")
            out.append(leak_row("TEST LENGTH", f"{res.hours:.1f}", "HRS"))
            out.append(leak_row("STRT VOLUME", f"{res.volume:.1f}", "GAL"))
            out.append("")
            out.append("LEAK TEST RESULTS")
            rate = leak_slip_rate(res.rate_key)
            word = SLIP_RESULT.get(res.result, res.result)
            out.append(f" {rate} TEST {word}")
            # Real 2024 paper, on a report and on a slip:
            #
            #     LEAK TEST RESULTS
            #      0.20 GAL/HR TEST INVL
            #      0.20 GAL/HR FLAGS:
            #      LOW LEVEL TEST ERROR
            #
            # The heading repeats the rate and is followed by the flags,
            # each indented one, in the words of Table 29-4. Every printed
            # example in the manuals shows the heading with nothing under
            # it, which is why this project had never seen the vocabulary.
            # See FIDELITY W15.
            if res.flags:
                out.append(f" {rate} FLAGS:")
                for flag in res.flags:
                    out.append(f" {flag}")
    return out


# What a line's report calls each rate. The console runs three and the
# manuals name them the same way in both places: " 3.0 GAL/HR RESULTS:",
# "0.20 GAL/HR RESULTS:", "0.10 GAL/HR RESULTS:".
LINE_RATE_NAMES = (("gross", "3.0"), ("periodic", "0.20"),
                   ("annual", "0.10"))

# "It also prints results of the last ten 0.2 gph tests and the last ten
# 0.1 gph tests" -- 576013-610 Rev AC p.11-1.
LINE_RESULTS_DEEP = 10

# The report's own word for a verdict. Function 373's sample prints `PASS`
# where the console's screens say PASSED, and the same three-letter forms
# are what chapter 11's monthly CSLD report uses: PASS, FAIL, INVL.
REPORT_VERDICT = {"PASSED": "PASS", "FAILED": "FAIL", "INVALID": "INVL"}


def line_leak_report(console, kind, only=None):
    """Function 373's own shape: a block per RATE, not a table.

    576013-635 Rev AA and 576013-610 Rev AC p.11-1 draw the same report:

        PRESSURE LINE LEAK TEST RESULTS
        Q 1:REGULAR UNLEADED
         3.0 GAL/HR RESULTS:
        LAST TEST:
        JAN 24, 1996  2:49 PM PASS
        NUMBER OF TESTS PASSED
        PREV 24 HOURS :   149
        SINCE MIDNIGHT :    76
        0.20 GAL/HR RESULTS:
        JAN 22, 1996  1:32 AM PASS
        0.10 GAL/HR RESULTS:
        JAN 23, 1996 11:59 PM PASS

    This console printed function 208's table instead -- TEST TYPE, RESULT,
    RATE, HOURS, VOLUME -- which is the tank report's mistake in a second
    place, and it had neither the LAST TEST block nor the two counters.
    See FIDELITY W14.

    The counters are drawn with `=` in 576013-610 and with `:` in the serial
    manual. W13 settled which to follow for this family of reports: paper
    follows the serial manual's display format, because 576013-610's own
    Figure 32-2 prints function 119's serial title where the screen says
    something else.
    """
    titles = {"plld": "PRESSURE LINE LEAK TEST RESULTS",
              "wplld": "WPLLD LINE LEAK TEST RESULTS",
              "vlld": "LINE LEAK DETECT RESULTS"}
    letter = {"plld": "Q", "wplld": "W", "vlld": "P"}[kind]
    label_code = {"plld": "782", "wplld": "7A2", "vlld": "760"}[kind]
    out = header(console, titles[kind])
    devices = sorted({d for k, d in console.leaks.results if k == kind}
                     | {d for k, d in console.leaks.running if k == kind}
                     | {d for k, d in console.leaks.history if k == kind})
    if only:
        devices = [d for d in devices if d == only]
    if not devices:
        out.append("NO TEST DATA AVAILABLE")
        return out
    now = time.mktime(console.now())
    midnight = time.mktime(time.struct_time(
        tuple(time.localtime(now))[:3] + (0, 0, 0) + tuple(
            time.localtime(now))[6:]))
    for device in devices:
        out.append("")
        out.append(f"{letter} {device}:"
                   f"{console.text(label_code, device) or ''}".rstrip())
        run = console.leaks.active(kind, device)
        if run is not None:
            out.append(f"TEST ACTIVE  {run.state}")
        history = console.leaks.history.get((kind, device)) or []
        for rate_key, name in LINE_RATE_NAMES:
            out.append(f"{name:>4} GAL/HR RESULTS:")
            done = [r for r in reversed(history) if r.rate_key == rate_key]
            if rate_key == "gross":
                # "LAST TEST:" and then the stamp, which is the one rate the
                # manual heads: a line runs a 3.0 after every dispense, so
                # its list would be the whole day.
                out.append("LAST TEST:")
                out.append(_verdict_line(done[0]) if done
                           else "NO TEST DATA AVAILABLE")
                out.append("NUMBER OF TESTS PASSED")
                passed = [r for r in done if r.result == "PASSED"]
                out.append("PREV 24 HOURS :"
                           f"{sum(1 for r in passed if r.started >= now - 86400):6d}")
                out.append("SINCE MIDNIGHT :"
                           f"{sum(1 for r in passed if r.started >= midnight):5d}")
                continue
            if not done:
                out.append("NO TEST DATA AVAILABLE")
            for result in done[:LINE_RESULTS_DEEP]:
                out.append(_verdict_line(result))
        if (kind, device) in console.leaks.disabled:
            out.append("LINE SHUT DOWN BY FAILED TEST")
    return out


def _verdict_line(result):
    """"JAN 24, 1996  2:49 PM PASS", the stamp and the verdict on one line.

    Twenty-nine characters on a twenty-four column roll, so `fit` folds it at
    the last space -- which puts the verdict on its own line under the stamp,
    and is the same rule every other over-wide line here follows.
    """
    return (clock_words(result.started) + " "
            + REPORT_VERDICT.get(result.result, result.result))


def tank_leak_history(console, tank):
    """TANK LEAK TEST HISTORY, which is what `I207` calls the same report.

    576013-818 Rev AB Figure 6-9 annotates the PRINT screen this comes from:
    "This printout gives the tank result for every month", against the two
    rate screens beside it whose "Printout contains static tank test
    results". `console.leak_history_lines` is the same body `I207` and
    `I212` serve over the wire -- the last pass of each rate, the fullest
    pass, and the fullest periodic pass of each month -- so this is a report
    the console could already build and could not reach. See FIDELITY H13.

    The title is 576013-635 p.65's, because it is the same report: one
    printout, one name, whichever port asks for it. 576013-610 draws no
    in-tank leak test HISTORY anywhere, which is W7's question and its
    answer -- the panel's own manual has none, the serial manual has this
    one, and the only screen that reaches it is a diagnostic.
    """
    out = header(console, "TANK LEAK TEST HISTORY")
    label = console.text("602", tank) or ""
    out.append(f"T {tank}:{label}".rstrip())
    out += console.leak_history_lines(tank)
    return out


def leak_tests(console, kind="tank", only=None):
    """The leak test report, which is what PRINT gives you at a results step.

    A tank prints the slip an inspector reads, which is a block and not a
    table; see `tank_leak_report`. A line prints function 373's blocks; see
    `line_leak_report`.
    """
    if kind == "tank":
        return tank_leak_report(console, only)
    if kind in ("plld", "wplld", "vlld"):
        return line_leak_report(console, kind, only)
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
    """INVENTORY INCREASE, the slip a delivery prints by itself.

    A slip, not a report: real paper carries no station header, no stamp of
    its own and no rule, and heads it with the tank and then the title. This
    console built "T2: DIESEL" -- no space after the T and a space after the
    colon, backwards from the paper and from every other report here -- put
    a rule under it, and truncated a 20 character label at 16.

    The rows are the inventory grid of `inv_row`, and the total is a
    fourteen character label with the "=" hard against it. See FIDELITY W11
    and W16.
    """
    label = console.text("602", tank) or f"TANK {tank}"
    out = [f"T {tank}:{label}".rstrip(), "INVENTORY INCREASE"]
    for name, snap in (("INCREASE START", record.start),
                       ("INCREASE END", record.end)):
        if not snap:
            continue
        out.append("")
        out.append(name)
        out.append(clock_words(snap["at"]))
        out.append(inv_row("VOLUME", masks.whole(snap['volume']), "GALS"))
        out.append(inv_row("HEIGHT", f"{snap['height']:.2f}", "INCHES"))
        out.append(inv_row("WATER", f"{snap['water']:.2f}", "INCHES"))
        out.append(inv_row("TEMP", f"{snap['temp']:.1f}", "DEG F"))
    out.append("")
    out.append(f" GROSS INCREASE={record.amount:>6.0f}")
    out.append(f" TC NET INCREASE={record.tc_amount:>6.0f}")
    if record.ticket is not None:
        out.append(f" TICKETED VOLUME={record.ticket:>6.0f}")
        out.append(f" VARIANCE={record.variance():>6.0f}")
    return out


def colon_row(label, value, colon=16):
    """One row of the TICKETED DELIVERY REPORT, on the tape's own grid.

    576013-610 Rev AC p.5-4 draws `TICKET VOL      :  2500 GALS`. Measured
    off its word boxes, at the page's own pitch of 5.40 points, the label
    run puts its colon at column 16 and the value run right-aligns every
    value and its unit to one edge -- at 27.7, on a roll that is 24. That
    overrun is the artwork's and not the paper's: p.4-2's INVENTORY REPORT,
    whose real paper is measured (W3), runs to 27.8 in the same manual.

    The real site tape prints the grid itself on its setup rows,
    `WATER WARNING   :    0.8` and `DELIVERY DELAY  :  3 MIN`: the label
    in sixteen, the colon at sixteen, the value and its unit right-aligned
    to the last column. A gallon row is nine characters where that leaves
    seven, so the block's labels give up their padding for it, together,
    and keep p.5-4's colons in one column -- `TICKET VOL    :2500 GALS`, a
    value hard against its colon, which is the tape's own
    `THERMAL COEFF   :.000690`. `colon` is that column. What a real console
    gives up is on no page and no paper: UNKNOWNS A66.
    """
    head = label.ljust(max(len(label), colon))
    return (head + ":" + value.rjust(WIDTH - 1 - len(head))).rstrip()


# 576013-610 Rev AC p.28-10's DELIVERY VARIANCE column, measured off the
# page's word boxes: the label run ends with a colon in column 11 and the
# value and its unit are one field right-aligned to the last column. It is
# the one sample in this family that is the roll's own width. FIDELITY T13.
VARIANCE_COLON = 11


def colon_block(rows):
    """`colon_row` for a block: (label, value) pairs, None for a blank.

    The colon column is the block's -- sixteen where every value fits the
    tape's seven, and as far left as the widest value needs otherwise --
    and a label longer than that keeps its own. See `colon_row`.
    """
    widest = max(len(row[1]) for row in rows if row)
    colon = min(16, WIDTH - 1 - widest)
    return ["" if row is None else colon_row(row[0], row[1], colon)
            for row in rows]


def _same_day(one, other):
    return time.localtime(one)[:3] == time.localtime(other)[:3]


def deliveries(console, tanks=None, day=None):
    """TICKETED DELIVERY REPORT, which PRINT gives on Delivery Maintenance.

    576013-610 Rev AC p.5-3 says what it carries -- "the delivery time,
    ticketed volume, gauged volume, fuel temperatures, delivery variance
    (ticketed volume-gauged volume), and Bill of Lading number" -- and p.5-4
    draws it: the tank, the title, the stamp and the basis, then a block per
    delivery. p.5-3 gives PRINT three scopes, one per screen: every tank on
    the function's own screen, "all deliveries for the tank shown" on
    `SELECT: EDIT/VIEW`, and "all deliveries for the day and tank shown" on
    a delivery's own screen, which is `day`.

    This was `DELIVERY REPORT`, gross and TC on one row and a ticket and a
    variance on the next, with no temperatures, no BOL and no basis line --
    a layout no page draws -- while `ticketed()` beside it had the right
    title over I221's seventy columns of serial layout, and nothing called
    it. FIDELITY O7.
    """
    tanks = tanks or sorted(console.tank_level)
    # "Volumes can be either standard or temperature-compensated. This
    # feature is selected in the Setup Mode": TC TICKETED DELIVERY,
    # 576013-623 Rev AN p.5-6, the setting I221 heads itself by as well
    tc = (console.values.get("S51D00") or "").strip().endswith("1")
    out = []
    for tank in tanks:
        records = console.deliveries.records.get(tank) or []
        if day is not None:
            records = [r for r in records
                       if r.end and _same_day(r.end["at"], day)]
        label = console.text("602", tank) or f"TANK {tank}"
        out += [f"T {tank}:{label}".rstrip(), "TICKETED DELIVERY REPORT",
                console.clock_stamp(),
                "VOLUMES ARE " + ("TC" if tc else "STANDARD")]
        for record in records:
            out.append("")
            out += _delivery_block(console, record, tc)
        out.append("")
    return out


def _delivery_block(console, record, tc):
    """One delivery of p.5-4: when it finished, then its seven rows.

    "When you insert ticketed deliveries, gauged volume and temperature
    information appear as 'UNAVAIL' (unavailable) on the report", p.5-3.
    The variance is ticket less gauge, so an inserted delivery has none to
    take either, and a gauged delivery nobody has ticketed has no ticket.
    """
    def gallons(value):
        return "UNAVAIL" if value is None else f"{masks.whole(value)} GALS"

    def degrees(value):
        return "UNAVAIL" if value is None else f"{value:.1f} F"

    gauged = pre = post = est = None
    if not record.inserted:
        gauged = record.tc_amount if tc else record.amount
        pre, post = record.start["temp"], record.end["temp"]
        est = console.deliveries.delivered_temperature(record)
    variance = (None if record.ticket is None or gauged is None
                else record.ticket - gauged)
    rows = [("TICKET VOL", gallons(record.ticket)),
            ("GAUGED VOL", gallons(gauged)),
            ("DLVY VAR", gallons(variance)),
            ("EST DLVY TEMP", degrees(est)),
            ("PRE DLVY TEMP", degrees(pre)),
            ("POST DLVY TEMP", degrees(post)),
            ("BOL", record.bol or "")]
    return [clock_words(record.end["at"])] + colon_block(rows)


def csld(console, tanks=None):
    """CSLD TEST RESULTS, which the console prints every 24 hours anyway."""
    tanks = tanks or sorted(console.tank_level)
    out = header(console, "CSLD TEST RESULTS", rule=True, restamp=True)
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

    The manuals never print that message, so this is the simulator's own:
    the tank, the time, and what the calibration moved, which is what a
    notification is for.

    This sentence used to say the invention was recorded in UNKNOWNS.md
    and it was not -- not there, not in FIDELITY.md, not anywhere. It is
    UNKNOWNS A34 now. A citation to a register entry is worth exactly as
    much as the entry, and a sweep for invented console text is what found
    the gap. See FIDELITY U5.
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
    """SHIFT RECONCILIATION, in the layout the PAPER draws it in.

    The closed shift if there is one, "a Shift Reconciliation Report for the
    previous shift": and the one running otherwise, so the report is not
    empty on a console nobody has closed yet.

    **This used to hand the roll `bir.report`, which is IC03 -- the report
    the WIRE asks for**, a seventy-character table of eight columns whose
    own docstring says so. Folded onto a twenty-four column roll it came out
    as a column header spread over four lines (`DATE TIME  OPENING` /
    `DLVRIES   SALES  ADJUST` / `CALC'D PHYSICL WATER` / `VAR`), the title
    printed twice, and the wire form's thirty-five character
    `SIGNATURE ____...` broken into a full line and one stray underscore. No
    console prints that.

    576013-610 Rev AC p.28-2 draws this report on paper and it is the
    label-over-value form `reconcile` already builds, so this defers to it
    rather than keeping a second layout under the same title. The SIGNATURE
    line goes with the wire form it belongs to: it occurs in 576013-635 and
    in no page of the Operator's Manual at all.

    See UNKNOWNS A19, whose "prior question" this was -- the paper was being
    handed a terminal's table.
    """
    tanks = tanks or sorted(console.tank_level)
    if previous is None:
        previous = any(console.bir.last(t) for t in tanks)
    return reconcile(console, tanks, kind="shift", previous=previous)


def last_shift(console, tanks=None, ending=False):
    """The Last-Shift Inventory Report, a block per shift per tank.

    576013-610 Rev AC p.8-1: "Press PRINT to print a Last-Shift Inventory
    Report. The system will print a report for all shifts for up to eight
    tanks", and it draws one: `SHIFT STARTING INV #1`, the stamp, the tank,
    seven inventory rows, DLVY ADJUSTMENT, GROSS CHANGE, TC NET CHANGE and
    HEIGHT, with the blank lines where the page leaves them. p.8-2 gives
    END INVENTORY an "Ending Inventory Report", which is `ending`; its
    title is the "Shift Ending Inv" p.8-2 names beside "Shift Starting Inv".

    PRINT here gave `SHIFT RECONCILIATION` -- Reconciliation Mode's report,
    off BIR's shift. The operating-mode audit's OP7; FIDELITY O22.

    No rule under the title, where the page draws one: W9 found real paper
    drops the rule this manual draws under INVENTORY REPORT, and this is
    that report, stored. The grid is `colon_block`'s. Both are UNKNOWNS A67.
    """
    shifts = console.shifts
    labels = console.programmed_tanks()
    tanks = tanks or sorted(labels)
    out = []
    for n in shifts.programmed():
        snap = shifts.end_of(n) if ending else shifts.start_of(n)
        if snap is None:
            continue
        out += [f"SHIFT {'ENDING' if ending else 'STARTING'} INV #{n}",
                clock_words(snap["at"])]
        for tank in tanks:
            row = snap["tanks"].get(tank)
            if row is None:
                continue
            fig = shifts.figures(n, tank)
            label = labels.get(tank, ("", 0.0))[0]
            out += ["", f"T {tank}: {label}".rstrip()]
            out += colon_block([
                ("VOLUME", f"{masks.whole(row['volume'])} GALS"),
                ("ULLAGE", f"{masks.whole(row['ullage'])} GALS"),
                (f"{row['pct']}% ULLAGE", f"{masks.whole(row['share'])} GALS"),
                ("TC VOLUME", f"{masks.whole(row['tc'])} GALS"),
                None,
                ("WATER VOL", f"{masks.whole(row['water_vol'])} GALS"),
                ("WATER", f"{row['water']:.2f} INCHES"),
                ("TEMP", f"{row['temp']:.1f} DEG F"),
                None,
                ("DLVY ADJUSTMENT", masks.whole(fig["deliveries"])),
                ("GROSS CHANGE", masks.whole(fig["gross"])),
                ("TC NET CHANGE", masks.whole(fig["tc_net"])),
                None,
                ("HEIGHT", f"{row['height']:.2f} INCHES")])
        out.append("")
    return out


def meters(console):
    """What each meter has put through itself."""
    out = header(console, "METER TOTALS")
    out.append(console.bir.meter_report())
    return out


def sensors(console, kind=None, only=None):
    """SENSOR STATUS, "status for all sensors", or for the selected one."""
    # The function names 576013-610 gives at p.2091 onwards, which are also
    # what a real slip prints. Only the smart sensor keeps the word SENSOR
    # in its title; the others were carrying an invented one. FIDELITY W6.
    titles = {"liquid": "LIQUID STATUS", "vapor": "VAPOR STATUS",
              "gw": "GROUNDWATER STATUS",
              "2wire": "2-WIRE C.L. (TYPE A) STATUS",
              "3wire": "3-WIRE C.L. (TYPE B) STATUS",
              "smart": "SMART SENSOR STATUS"}
    out = header(console, titles.get(kind, "SENSOR STATUS REPORT"),
                  rule=True, restamp=True)
    found = [(mod, n, label) for mod, n, label in console.programmed_sensors()
             if (kind is None or mod == kind) and (only is None or n == only)]
    if not found:
        return out                      # see the inventory report, and U5
    letter = {"liquid": "L", "vapor": "V", "gw": "G", "2wire": "C",
              "3wire": "H", "smart": "s"}
    for mod, n, label in found:
        # Two lines, the way the manual's own sample draws it and the way a
        # real slip prints it -- the status has a line of its own, not a
        # column 21 the 24 character roll does not have. The manual writes
        # "L1: LOCATION" and the paper writes "L 1:LOCATION"; the paper wins,
        # as it does on the delivery head.
        out.append(f"{letter.get(mod, 'L')} {n}:{label}".rstrip())
        out.append(console.sensor_reading(mod, n))
    return out


def fuel(console, only=None, index=None, long=True):
    """FUEL MANAGEMENT REPORT, in the shape 576013-610 Rev AC draws.

    Two reports, not one. p.7-1's SHORT report is a block per tank --
    `T 1: (product label)`, then DAYS FUEL REMAINING, INVENTORY and
    95% ULLAGE, in that order and colon-separated -- and p.7-3's is the same
    plus the seven `AVG SALES-SUN : 983 GAL` rows for the product. This
    printed a different report: `INVENTORY  =` first, `=` for `:`,
    `DAYS REMAIN` for `DAYS FUEL REMAINING`, one average-sales row where
    seven belong, and no short-against-long distinction at all.

    The manual's own sample is set on wider paper than this console's
    24-column roll, so the labels, the colons and the order are the
    manual's and the padding is what fits. See FIDELITY O4.
    """
    out = header(console, "FUEL MANAGEMENT REPORT")
    return out + _fuel_body(console, only, long)


def fuel_all_products(console):
    """PRINT on FUEL MANAGEMENT's own screen: "a Fuel Management report for
    all products", 576013-610 Rev AC p.7-1.

    p.7-3's report once per product under one header, so each product's
    seven average-sales rows sit under its own tanks rather than being
    added up across products. The operating-mode audit's OP8; FIDELITY O23.
    """
    out = header(console, "FUEL MANAGEMENT REPORT")
    for _code, tanks in console.fuel_products():
        out += _fuel_body(console, tanks, True)
    return out


def _fuel_body(console, only, long):
    """The tank blocks, and with `long` the product's seven sales rows."""
    out = []
    tanks = console.programmed_tanks()
    for n, (label, full) in sorted(tanks.items()):
        if only and n not in only:
            continue
        volume = console.tank_level.get(n, {}).get("volume", 0.0)
        spare = volume - (console.limit("621", n) or 0.0)
        sales = sum(console._fuel_day_sales(day, n)
                    for day in DAY_NAMES) / len(DAY_NAMES)
        days = max(spare, 0.0) / sales if sales > 0 else 0.0
        out.append("")
        out.append(f"T {n}:{label}")
        out.append(f"DAYS FUEL REMAINING: {days:.1f}")
        out.append(f"INVENTORY   : {volume:5.0f} GALS")
        out.append(f"95% ULLAGE  : {max(full * 0.95 - volume, 0):5.0f} GALS")
    if long:
        # "Press PRINT to print a report of the average daily sales", and the
        # sample that follows it carries the tank blocks above these seven.
        # They are the PRODUCT's, so they are printed once rather than per
        # tank.
        out.append("")
        for day in DAY_NAMES:
            total = sum(console._fuel_day_sales(day, n)
                        for n in sorted(tanks) if not only or n in only)
            out.append(f"AVG SALES-{day}: {total:5.0f} GAL")
    return out


def relays(console, kind="relay"):
    """Output relay setup, or what the pump relay monitors are reading."""
    if kind == "pumpmon":
        # 576013-635 Rev AA function 322's own report, which `wiresensors`
        # has rendered all along: the pump's state, the relay LINE it is
        # watching, and the STATUS that is the reason the report exists.
        # The paper built a row of its own carrying the first of those and
        # calling it the pump's -- one report with two renderings and the
        # wire the right one, which is the shape D23 and D24 both had.
        # `fit` folds it onto the roll. See FIDELITY T12.
        from . import wiresensors
        rows = wiresensors.pumpmon_status_rows(console)
        # "FITTED" is the BENCH's word for a card in a bay, and it was on
        # a printed page. A console with no monitor prints the heading and
        # no rows. See FIDELITY U5.
        return header(console, rows[0]) + rows[1:]
    out = header(console, "OUTPUT RELAY SETUP")
    for n in range(1, max(console.capacity("relay"),
                          console.capacity("io")) + 1):
        # Two lines, the way `sensors` draws the same shape three hundred
        # lines up: the label on the device's own line and the state under
        # it, because there is no column 21 the 24 character roll does not
        # have. This was one 26 character line padded to a column the paper
        # cannot reach, and it folded at whatever space came last -- the
        # right answer by accident, and it truncated an 18 character label
        # the roll has room for 20 of. See FIDELITY T12.
        label = console.text("807", n) or f"RELAY {n}"
        out.append(f"R {n}:{label}".rstrip())
        out.append("ON" if console.outputs.energised(n) else "OFF")
    return out


def service_codes(console):
    """The service code list the Service Report offers to print.

    576013-610 Rev AC p.33-2: "Press Print to print out a list of all
    predefined and previously entered User Defined (99xx) service codes.
    (you can also refer to the Maintenance Service Codes Quick Help
    guide ...)". The pamphlet is an "also", not a substitute, and this
    printed a pointer to it and nothing else while the console held all
    129 codes and answered 8A2 with them.

    The shape is 8A2's own, from 576013-635 Rev AA: a `STANDARD LABEL
    CODE` heading, the codes with the label left in twenty and the number
    at column 21, then a blank and `USER DEFINED LABEL  CODE` over
    whatever the site has added. See FIDELITY P1.
    """
    out = header(console, "SERVICE CODE LIST")
    out.append("STANDARD LABEL      CODE")
    out.extend(f"{name:<20.20s}{code}" for code, name in console.SERVICE_CODES)
    out.append("")
    out.append("USER DEFINED LABEL  CODE")
    out.extend(f"{name:<20.20s}{code}"
               for code, name in console.user_service_codes)
    return out


def service_history(console, most=25):
    """The Service Report the function's own screen prints, or None.

    576013-818 Rev AB Figure 6-3: "Press Print to printout a list of the 25
    most recent services codes entered. If none exist, there will be no
    printout." No page draws that paper, so its rows are the ones the wire
    serves the same log with -- 116's before Version 27, when 116 went
    obsolete, and 11A's from then -- through one renderer, folded to the
    roll the way every report wider than it is. See FIDELITY D24.
    """
    entries = console.service_log(most)
    if not entries:
        return None
    tok = "11A" if int(console.version) >= 27 else "116"
    rows = [wiretables.heading(tok)] + alarmreports.service_rows(tok, entries)
    return header(console, "SERVICE REPORT") + rows


def csld_monthly(console, tank, previous=False):
    """CSLD MONTHLY REPORT for the tank and the month the panel shows.

    576013-610 Rev AC p.27-3: "Press PRINT to print out the report for the
    tank shown", on the CUR and PRV CSLD MONTHLY screens. The rows are A56's
    display body, from the renderer the wire answers with. See FIDELITY D23.
    """
    from .wire import Handler
    rows = Handler(console, verbose=False).csld_monthly_rows([tank], previous)
    return header(console, rows[0]) + rows[1:]


def meter_events(console):
    """"Prints Last 4 Meter Events", 576013-818 Figure 6-25.

    The figure marks the P on the time-of-last-event screen and gives no
    sample of the paper, so the report is the SCREENS -- which is the rule
    this manual states for the diagnostic prints it does describe: "press
    Print. (If the console does not have a printer, manually record the
    diagnostic data from each diag screen)". Newest first. See FIDELITY
    D14 and U1.
    """
    out = header(console, "BIR METER EVENTS")
    for event in list(reversed(console.bir.events))[:4]:
        out.append(clock_words(event["at"], seconds=True))
        if event["kind"] == "start":
            out.append(f"FP: {event['fp']:02d}")
            out.append("START EVENT")
        else:
            out.append(f"FP: {event['fp']:02d} M: {event['meter']:02d}"
                       f" =T {event['tank']}")
            out.append(f"END EVENT: {event['gallons']:.0f} GALS")
        out.append("")
    return out


def blocked_keys(console):
    """The list Figure 6-4 hangs off SELECT KEY TO BLOCK.

    "Press PRINT to printout a list of blocked keys", and the shape is
    8A4's own Display format: `MAINTENANCE TRACKER BLOCK HARDWARE KEY` over
    `LABEL  ID`, the label left in twenty and the six-character ID at
    column 21 -- measured off the rendered page, where they sit at x=72 and
    x=191.9 on a six-point character. See FIDELITY D13.
    """
    out = header(console, "MAINTENANCE TRACKER BLOCK HARDWARE KEY")
    out.append("LABEL" + " " * 15 + "ID")
    out.extend(f"{name:<20.20s}{ident}"
               for ident, name in console.blocked_tracker_keys())
    return out


# The report's own title, per device. 576013-635 Rev AA heads each one
# separately -- TANK ALARM HISTORY, LIQUID ALARM HISTORY REPORT, VAPOR
# ALARM HISTORY REPORT and eleven more -- where `ALARM HISTORY REPORT` on
# its own is the display PROMPT, the line over PRESS <STEP> TO CONTINUE.
# See FIDELITY W13.
# Each family's report title, as the serial manual's own display format
# prints it -- which is what a console prints on paper: 576013-610 Rev AC
# Figure 32-2 prints function 119's `MAINTENANCE HISTORY` where the SCREEN
# says `MAINTENANCE REPORT`. See W13.
#
# Two of them have no title at all, and that is the manual's doing rather
# than an omission here: function 352 (VLLD) goes straight from the station
# header to `P 1:REGULAR UNLEADED`, and function 402 (external input) to
# `INPUT   LOCATION`. A letter absent from this table and present in
# HISTORY_UNTITLED prints its device line and no title. See W25.
#
# X is VMCI and x is VMC, which this table had the other way round. The
# serial manual's category list reads `35=VMCI Dispenser Interface Alarm`
# and `36=VMC Alarm`, `consoledata.json` maps 35 to X and 36 to x, and
# functions 411 and 412 print `VMCI ALARM HISTORY REPORT` and
# `VMC ALARM HISTORY REPORT` in that order. See W22.
HISTORY_TITLE = {
    "T": "TANK ALARM HISTORY",
    "L": "LIQUID ALARM HISTORY REPORT",
    "V": "VAPOR ALARM HISTORY REPORT",
    "G": "GROUNDWATER ALARM HISTORY REPORT",
    "C": "2 WIRE CL ALARM HISTORY REPORT",
    "H": "3 WIRE CL ALARM HISTORY REPORT",
    "U": "UNIVERSAL ALARM HISTORY REPORT",
    "Q": "PRESSURE LINE LEAK ALARM HISTORY REPORT",
    "W": "WPLLD LINE LEAK ALARM HISTORY REPORT",
    "s": "SMART SENSOR ALARM HISTORY REPORT",
    "r": "PUMP RELAY MONITOR ALARM HISTORY REPORT",
    "X": "VMCI ALARM HISTORY REPORT",
    "x": "VMC ALARM HISTORY REPORT",
}

# The two the manual prints with no title of their own.
HISTORY_UNTITLED = ("P", "I")

# The identity line each family's report carries above its alarms, from the
# same display formats. Six shapes, not two. See W26.
#
#   L V G C H U s   `SENSOR  LOCATION` over "1  LIQUID # 1"      (302, 342, 316)
#   T               `TANK 1  REGULAR UNLEADED`                   (206)
#   I               `INPUT   LOCATION` over "1  ..."             (402)
#   r               `DEVICE  LABEL` over the label alone         (323)
#   X               `DEVICE  ALARMS`                             (411)
#   x               `VMC   S/N    ALARMS`                        (412)
#
# W26's own table has those last two the other way round, from the same
# swapped source W22 found: 411 is the VMCI report and the VMCI is category
# 35, which `consoledata.json` maps to X.
#   Q W P           no head at all: straight to "Q 1:LABEL"      (382, 387, 352)
HISTORY_HEAD = {"I": "INPUT   LOCATION", "r": "DEVICE  LABEL",
                "X": "DEVICE  ALARMS", "x": "VMC   S/N    ALARMS"}
HISTORY_NO_HEAD = ("Q", "W", "P")

# How deep the console's memory goes. 576013-610 Rev AC: "record of the last
# three occurrences of each type of alarm or warning condition."
HISTORY_DEEP = 3


def alarm_history(console, letter=None, system=False, device=None):
    """The alarm history for one device type, or the system's own.

    I206's display format is the shape: a title, the device under it, then
    the alarms GROUPED BY TYPE -- the description once, its stamps under it
    newest first, and the description column left blank on the repeats.

        TANK ALARM HISTORY
        TANK 1  REGULAR UNLEADED
        LOW PRODUCT ALARM
          DEC 22, 1995  3:31 PM
          DEC 19, 1995 10:05 AM

    On a 24 column roll the description and its stamp cannot share a line
    the way the serial format's 40 columns let them, so the stamp indents
    under its description instead -- which is what this printed already, and
    the only part of the old shape worth keeping. What it did not do was
    group, or head, or stop at three. See FIDELITY W13.
    """
    from .console import STATUS_DEVICE_CODE
    from .wire import _when
    if system:
        title = "ALARM HISTORY REPORT"
    elif letter in HISTORY_UNTITLED:
        title = None
    else:
        title = HISTORY_TITLE.get(letter or "", "ALARM HISTORY REPORT")
    # Twelve of the fourteen titles are longer than the roll -- only
    # `TANK ALARM HISTORY` at 18 and one 24 fit -- because 576013-635 Rev
    # AA's are the DISPLAY format's, forty columns wide, and this paper is
    # twenty-four. This report clipped its own title before `fit()` could
    # fold it, alone among the reports here, and all twelve fold cleanly at
    # a space: `PRESSURE LINE LEAK ALARM` / `HISTORY REPORT`. See W13.
    out = header(console, title) if title else header(console)
    if system:
        # the console's own alarms, which have no device against them
        wanted = {"01"}
    else:
        wanted = {aa for aa, code in STATUS_DEVICE_CODE.items()
                  if letter and code == letter}
        if letter and not wanted:
            # A letter no alarm category maps to -- `g` groundtemp and `F`
            # BIR product are two of the seventeen screens -- filtered to
            # NOTHING and this printed EVERYTHING: every alarm the console
            # held, of every category, under a groundtemp heading. An empty
            # filter shows nothing. See W21.
            wanted = {None}
    shown = [r for r in console.alarm_log
             if (not wanted and not letter) or r["aa"] in wanted]
    if device:
        # "Press PRINT to print the report for the tank displayed. Press
        # TANK/SENSOR to access other tanks in the system" -- and this
        # printed every device in the family whatever the screen said.
        # See W24.
        shown = [r for r in shown if int(r["tt"] or 0) == int(device)]
    if not shown:
        # "NO ALARM HISTORY" is this project's phrase and no manual's. The
        # shelf's whole vocabulary for "nothing yet" -- NO TEST DATA
        # AVALIABLE, NO RESULTS AVAILABLE, NO IDLE DATA, NO TEST PASSED --
        # has nothing about an alarm history in it, so this stands rather
        # than being swapped for a second invention. FIDELITY W13.
        out.append("NO ALARM HISTORY")
        return out
    for device in sorted({r["tt"] for r in shown}):
        rows = [r for r in shown if r["tt"] == device]
        if not system:
            out.extend(_history_device(console, letter, device))
        # I206's own sample groups by TYPE and orders the groups by their
        # newest stamp: LOW PRODUCT's two stamps together, then INVALID FUEL
        # LEVEL's three. This walked the log in one date order and printed a
        # name only on first sight, so a later occurrence of an earlier group
        # landed under whichever name had been printed last. See W23.
        groups = {}
        for record in sorted(rows, key=lambda r: r["at"], reverse=True):
            described = describe_alarms(
                [record["aa"] + record["nn"] + record["tt"]])
            if not described:
                continue
            # the identity line above already says which device this is,
            # and I206's sample carries no tag on the description:
            # `LOW PRODUCT ALARM`, not `T 1:LOW PRODUCT ALARM`
            screen = str(described[0]["screen"])
            hit = PANEL_HEAD.match(screen)
            name = (hit.group(3) if hit and hit.group(3) else screen)
            groups.setdefault(name[:SETUP_COLS], []).append(record)
        for name, kept in sorted(groups.items(),
                                 key=lambda kv: kv[1][0]["at"], reverse=True):
            out.append(name)
            for record in kept[:HISTORY_DEEP]:
                # "the last three occurrences of each type"
                out.append("  " + _when(record["at"]))
    return out


def _history_device(console, letter, tt):
    """The identity line under the title, in this family's own shape.

    Six shapes, not two: I206 prints `TANK 1  REGULAR UNLEADED`, I302 a
    `SENSOR  LOCATION` column head over `1  LIQUID # 1`, I402 the same with
    `INPUT   LOCATION`, I323 a `DEVICE  LABEL` head over the label ALONE
    with no number, I411 `DEVICE  ALARMS`, I412 `VMC   S/N    ALARMS` -- and
    the three line-leak reports no head at all, straight to `Q 1:REGULAR
    UNLEADED`. See FIDELITY W26.
    """
    try:
        number = int(tt)
    except (TypeError, ValueError):
        return []
    if not number:
        return []
    label = _device_label(console, letter, number)
    if letter == "T":
        return [f"TANK {number}  {console.text('602', number) or ''}"
                .rstrip()[:SETUP_COLS]]
    if letter in HISTORY_NO_HEAD:
        # "P 1:REGULAR UNLEADED", the device and its label with no column
        # head over them and no space after the colon
        return [f"{letter} {number}:{label}".rstrip()[:SETUP_COLS]]
    if letter == "r":
        # "DEVICE  LABEL" over "PUMP RELAY UNLEADED": the label alone
        return [HISTORY_HEAD["r"], (label or f"MONITOR {number}")[:SETUP_COLS]]
    head = HISTORY_HEAD.get(letter or "", "SENSOR  LOCATION")
    return [head, f"{number}  {label}".rstrip()[:SETUP_COLS]]


def _device_label(console, letter, number):
    """Whatever the site called that device, or the console's own name."""
    code = {"L": "702", "V": "707", "G": "712", "C": "742", "H": "747",
            "s": "722", "Q": "782", "W": "7A2", "I": "802",
            "r": "7C5"}.get(letter or "")
    if code:
        return console.text(code, number) or ""
    return ""


# ---------------------------------------------------------------------------
# Reconciliation Mode. The operator's manual prints each of these in full, and
# they are a column of labelled figures rather than a table: "OPENING VOLUME:"
# on one line and "5511 GALS" right-aligned under it.
# ---------------------------------------------------------------------------
PERIOD_WORD = {"shift": "SHIFT", "daily": "DAILY", "weekly": "WEEK",
               "periodic": "PERIODIC"}


FIGURE_COLS = 23


def _figure(out, label, value, unit="GALS"):
    """"OPENING VOLUME:" over its number, the way p.28-2 draws it.

    Gallons are whole; inches are not. The value and its unit are ONE
    right-aligned field, and 23 is measured off the page rather than assumed:
    576013-610 Rev AC p.28-2 draws the whole SHIFT RECONCILIATION report, and
    its word boxes put every label at column 0 and the right edge of all
    eight values at column 23.1, in a 5.40pt monospace.

        OPENING VOLUME:
                      5511 GALS

    This was right-aligned in 22 and then given its unit, which is 27
    characters, and the docstring said "how the console lays it out on a 40
    column roll". The roll is 24 -- see `WIDTH`, which was corrected against
    real paper and is now cited to 577013-369 Rev B s.3.1.E as well -- so
    every one of these nine lines was three characters over the paper and got
    folded, putting `GALS` on a line of its own. UNKNOWNS A19.

    *One measured detail is deliberately not reproduced.* On the page the
    VARIANCE value is one column wider than the others -- it starts at 16.1
    where DELIVERIES' identical `0 GALS` starts at 17.1 -- which is a sign
    position, the variance being the one signed figure here. Python writes
    the sign only when the number is negative, so a zero or positive variance
    lands one column right of where the page draws it, and a negative one
    lands exactly. A space held for the sign would match the sample and
    misalign every other row against itself; the page has one sample and it
    is a zero, so this is left as the smaller of two guesses.
    """
    out.append(f"{label}:")
    shown = f"{value:.2f}" if unit == "INCH" else f"{value:.0f}"
    out.append(f"{shown} {unit}".rjust(FIGURE_COLS))
    out.append("")


def _when_block(out, row):
    out.append("OPENING DATE & TIME:")
    out.append(clock_words(row["opened"]))
    out.append("")
    out.append("CLOSING DATE & TIME:")
    out.append(clock_words(row["closed"]))
    out.append("")


def _volumes_are(console):
    """"VOLUMES ARE STANDARD": or TC, if BIR was set to TC VOLUME.

    One source with the numbers under it, rather than a second reading of
    S79F: this line and `bir.gauged` were the two halves of the same
    setting, and for a long time only this one moved. See FIDELITY G10.
    """
    tc = console.bir.temperature_compensated()
    return "VOLUMES ARE " + ("TC" if tc else "STANDARD")


def _label(console, tank):
    return console.text("602", tank) or ""


def reconcile(console, tanks=None, kind=None, previous=None, day=None):
    """The Reconciliation Report, per product, in the manual's own layout.

    `day` is the day SELECT DAY was pointed at, on the daily report that
    lets one be typed. See FIDELITY Q1.
    """
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    title = ("SHIFT RECONCILIATION" if kind == "shift"
             else PERIOD_WORD[kind] + " RECONCILIATION")
    out = header(console, title)
    for tank in console.bir.report_tanks(tanks):
        row = console.bir.row(tank, kind, previous, day)
        out.append("")
        out += [line.rstrip() for line in console.bir.tank_lines(tank)]
        out.append("")
        if row is None:
            continue                    # see the inventory report, and U5
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
            which + PERIOD_WORD[kind], "", console.clock_stamp(),
            _volumes_are(console), ""]


def delivery_variance(console, tanks=None, kind=None, previous=None):
    """DELIVERY VARIANCE: what the tickets said against what the gauge saw."""
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    out = []
    for tank in console.bir.report_tanks(tanks):
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
        # 576013-610 Rev AC p.28-10's own grid, read off its word boxes:
        # the label run to a colon in column 11 and the value and its unit
        # right-aligned as ONE field to column 23.
        #
        #     TICKET VOL :     800 GAL
        #     % VAR SALES:      11.23%
        #
        # These were built `f"TICKET VOL : {…:9.0f} GAL"`, 26 characters, so
        # all three GAL rows folded their unit onto a line of its own and
        # `% VAR SALES` came to 23 and lined up with none of them. That is
        # 12 over-wide lines on a four-tank site, from the one report in
        # this family whose sample IS the roll's width. `_figure` three
        # functions above had already fixed the identical defect for the
        # reconciliation report and did not look sideways. FIDELITY T13.
        #
        # THIS report's own direction, p.28-7: "difference between gauged and
        # ticketed delivery volumes", and its sample prints 99 for a ticket
        # of 800 against a gauge of 899. The Variance Analysis report on
        # p.28-14 defines the same words the other way round and prints -99
        # for the same pair. Both are right on their own page. FIDELITY G9.
        pct = (var["gauged_delivery_var"] / sales * 100.0) if sales else 0.0
        # `+ 0.0` on the two signed figures, the way `variance_analysis`
        # already does it: a variance that rounds to nothing from below
        # prints `-0 GAL` otherwise, and a report that distinguishes minus
        # zero from zero is telling a technician something untrue. It did.
        out += [colon_row(label, value, VARIANCE_COLON) for label, value in (
            ("TICKET VOL", f"{row['ticketed']:.0f} GAL"),
            ("GAUGED VOL", f"{row['deliveries']:.0f} GAL"),
            ("DLVY VAR", f"{var['gauged_delivery_var'] + 0.0:.0f} GAL"),
            ("% VAR SALES", f"{pct + 0.0:.2f}%"))]
        out.append("")
    return out


def book_variance(console, tanks=None, kind=None, previous=None):
    """BOOK VARIANCE: the gauge against the book the tickets and meters keep."""
    kind = kind or console.recon_kind
    previous = console.recon_previous if previous is None else previous
    tanks = tanks or sorted(console.tank_level)
    out = []
    for tank in console.bir.report_tanks(tanks):
        row = console.bir.row(tank, kind, previous)
        out += _variance_head(console, tank, "BOOK VARIANCE", kind, previous)
        if row is None:
            out.append("NO DATA AVAILABLE")
            out.append("")
            continue
        _when_block(out, row)
        var = console.bir.analysis(row)
        # Its own page does not fit any roll. 576013-610 Rev AC p.28-14
        # measures **30** columns -- `OPN GAUG VOL :         800 GAL`,
        # label run to a colon in 13 and a sixteen-wide value field -- where
        # p.28-10 four pages earlier measures exactly 24 for the report
        # beside it. A manual sample is typeset, not photographed, so this
        # block gives up the value field's padding the way the TICKETED
        # DELIVERY REPORT's does, keeping the page's labels and its one
        # colon column: `colon_block`, which is what that pair exists for.
        # The rows were 27 and 28 and every one of them folded. FIDELITY T13.
        out += colon_block([
            ("OPN GAUG VOL", f"{row['opening']:.0f} GAL"),
            ("METER SALES", f"{row['sales']:.0f} GAL"),
            ("TICKET DLVY", f"{row['ticketed']:.0f} GAL"),
            ("MANUAL ADJ", f"{row['adjust']:.0f} GAL"),
            ("BOOK INV", f"{console.bir.book(row):.0f} GAL"),
            ("GAUGED INV", f"{row['physical']:.0f} GAL"),
            ("WATER HT", f"{row['water']:.2f} IN"),
            ("VAR", f"{var['book_var'] + 0.0:.0f} GAL "
                    f"{var['book_pct'] + 0.0:.1f}%")])
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
    for tank in console.bir.report_tanks(tanks):
        row = console.bir.row(tank, kind, previous)
        out += _variance_head(console, tank, "VARIANCE ANALYSIS", kind,
                              previous)
        if row is None:
            out.append("NO DATA AVAILABLE")
            out.append("")
            continue
        _when_block(out, row)
        var = console.bir.analysis(row)
        # The label is padded so every colon lands in the same column and
        # the VALUE follows it directly. These were right-aligned into nine
        # characters, which puts six spaces in front of `800 GAL` where
        # p.28-18 has none -- and the page settles it: read by its word
        # boxes, `800 GAL` and `T 1` begin at the same column, and a right
        # aligned field could not put a three character value and a seven
        # character one in the same place. See FIDELITY Q3.
        # `+ 0.0` on each: a variance that rounds to nothing from below
        # prints `-0 GAL` otherwise, and a report that distinguishes minus
        # zero from zero is telling a technician something untrue.
        out.append(f"BOOK VAR      : {var['book_var'] + 0.0:.0f} GAL")
        out.append(f"BOOK VAR %    : {var['book_pct'] + 0.0:.2f} %")
        out.append(f"DLVY VAR      : {var['delivery_var'] + 0.0:.0f} GAL")
        out.append(f"SALE VAR      : {var['sales_var'] + 0.0:.0f} GAL")
        out.append(f"TEMP VAR      : {var['temp_var'] + 0.0:.0f} GAL")
        out.append(f"WATER CHG     : {var['water_change'] + 0.0:.2f} IN")
        out.append(f"UNEX VAR      : {var['unexplained'] + 0.0:.0f} GAL")
        # p.28-18 puts two more rows in the same column, and the console
        # already keeps both flags apart under the wire's own names:
        # "LLLLLLLL - failure to calibrate in 56 days" and "llllllll - tank
        # chart alarm". They are conditional rows -- the bullet list of the
        # report's contents on p.28-17 does not name them and the sample
        # draws them -- so each prints when its own flag stands.
        if console.accuchart.chart_alarm(tank):
            out.append(f"CHART ALM     : T {tank}")
        if console.accuchart.calibration_failed(tank):
            out.append(f"CALIB FAIL    : T {tank}")
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
        out.append("MONTHLY TANK TEST REPORT")
        out.append(_rule())
        out.append(f"T {tank}: {_label(console, tank)}".rstrip())
        out.append(f"PROBE SERIAL NUM {console.probe_serial(tank)}")
        out += console.monthly_test_lines(tank)
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
    out.append(console.clock_stamp())
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
            continue                    # see the inventory report, and U5
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


SS_RULE = "-" * 23


def _ss_block(console, title, number):
    """The heading all three SS DIAG printouts share: the title, a run of
    twenty-three hyphens, and the sensor with its label."""
    label = console.text("722", number) or f"SMART SENSOR {number}"
    return header(console)[:-1] + [title, SS_RULE, f"s {number}: {label}"]


# The words each smart sensor's own TYPE screen draws, 576013-818 Rev AB
# Figures 6-28, 6-29 and 6-32, which the install log repeats.
SMART_TYPE_WORDS = {"03": "MAG SENSOR", "04": "VAC SENSOR",
                    "05": "ATMP SENSOR"}


def smart_diagnostic(console, number):
    """SMART SENSOR DIAGNOSTIC, the P beside a Mag or an ATMP sensor's walk.

    576013-818 Rev AB Figure 6-28, beside MAG SENSOR DIAGS:

        SMART SENSOR DIAGNOSTIC
        MMM DD, YYYY  HH:MM XM
        s1: SUMP 1
        MAG SENSOR
        TOTAL HT      XX.X IN.
        ...
        BOARD TEMP    XX.X F

    and Figure 6-32 beside ATM P SENSOR DIAGS, with TYPE, SERIAL NUMBER and
    ATM PRESSURE under the sensor. The readings are the screens' own, so the
    paper is what the panel showed. None for a sensor of any other kind.
    """
    from . import wiresensors
    kind = console.sensor_type("smart", number)
    label = console.text("722", number) or f"SMART SENSOR {number}"
    out = header(console)[:-1] + ["SMART SENSOR DIAGNOSTIC",
                                  console.clock_stamp(),
                                  f"s {number}: {label}"]
    if kind in ("03", "00", ""):
        names = ("TOTAL HT", "FUEL HT", "WATER HT", "INSTALL POS",
                 "FLUID TEMP", "BOARD TEMP")
        units = ("IN.", "IN.", "IN.", "IN.", "F", "F")
        values = wiresensors._mag_values(console, number)
        return out + ["MAG SENSOR"] + [
            f"{name:<14s}{value:4.1f} {unit}"
            for name, value, unit in zip(names, values, units)]
    if kind == "04":
        return out + _vac_paper(console, number)
    if kind == "05":
        return out + ["TYPE: ATM P SENSOR",
                      console.diag_reading("ss_serial9", number),
                      console.diag_reading("ss_atm", number)]
    return None


def _vac_paper(console, number):
    """The Vac sensor's half of Figure 6-29's printout, under the head.

    576013-818 Rev AB p.6-24 draws it beside `TYPE: VAC SENSOR`, in its own
    columns, and every line of it is a reading this console already models:

        VAC SENSOR
        SERIAL NUMBER XXXXXXXX
        COMPENSATED PRESSURE:
         -0.155 PSI
        UNCOMPENSATED PRESSURE:
         -0.094 PSI
        EVACUATION STATE:
          NO VACUUM
        FLUID STATUS:  NORMAL
        VCV: CLOSED
        MM-DD-YYYY  HH:MM XM
        LEAK RATE:    0.123 GPH
        TIME TO NO VAC:
        150:20   HHH:MM
        MM-DD-YYYY  HH:MM XM
        EVAC RATIO:5.2 @-4.1PSI
        SENSOR FAULTS:
         NONE

    It is B38's block in the printer's own geometry, which is not B38's: the
    two pressure values are indented ONE and the evacuation state TWO, where
    the port holds all three right against 24; `FLUID STATUS:` carries two
    spaces here and one on the glass; the stamp is `MM-DD-YYYY` with a
    four-digit year where the port writes `4-12-04`; and the format hint
    under TIME TO NO VAC is `HHH:MM` against the port's `HHHH:MM`. Measured
    off the page rather than carried across. FIDELITY D30, which waited on
    L18 because until L18 the block this shares with the port was invented.
    """
    from . import wiresensors
    compensated, uncompensated = wiresensors._vac_pressures(console, number)
    state = console.sensor_state.get(("smart", str(number)), "normal")
    fluid = ("FAULT" if state in ("fault", "faultwarn")
             else "FLUID" if state == "high" else "NORMAL")
    evac = wiresensors.EVAC_WORDS[console.evacuation_state(number)[-1]]
    out = ["VAC SENSOR",
           console.diag_reading("ss_serial9", number),
           "COMPENSATED PRESSURE:", f"{compensated:>7.3f} PSI",
           "UNCOMPENSATED PRESSURE:", f"{uncompensated:>7.3f} PSI",
           "EVACUATION STATE:", "  " + evac,
           f"{'FLUID STATUS:':<15s}{fluid}",
           "VCV: " + ("OPEN" if console.vac_valve_open(number) else "CLOSED")]
    # ...and then the last manual test, which is the same record the three
    # result screens read and the same three validity flags B38 sends. A
    # sensor that has not run one has no moment to stamp, so the date line
    # goes with the reading it stamps. The dashes are the panel's own form.
    result = console.vac_result(number) or {}
    stamp = (None if result.get("at") is None
             else time.strftime("%m-%d-%Y", time.localtime(result["at"]))
             + "  " + clock_hhmm(time.localtime(result["at"])))
    rate, hours = result.get("rate"), result.get("hours")
    if rate is None:
        out.append(f"{'LEAK RATE:':<10s}{'--- GPH':>13s}")
    else:
        out += [stamp, f"{'LEAK RATE:':<10s}{f'{rate:.3f} GPH':>13s}"]
    shown = ("---:--" if hours is None
             else f"{int(hours):d}:{int(round((hours % 1) * 60)):02d}")
    out += ["TIME TO NO VAC:", f"{shown:<6s}   HHH:MM"]
    ratio = result.get("ratio")
    if ratio is None:
        at_psi = result.get("psi")
        out.append("EVAC RATIO:--- @ ---PSI" if at_psi is None
                   else f"EVAC RATIO:--- @{at_psi:.1f}PSI")
    else:
        out += [stamp, f"EVAC RATIO:{ratio:.1f} @{result['psi']:.1f}PSI"]
    # The same fault the port names, off the same sensor state, under the
    # heading Figure 6-29 draws whether or not there is one to name.
    named = [name for bit, name in wiresensors.FAULT_BITS
             if (4 if fluid == "FAULT" else 0) & bit]
    return out + ["SENSOR FAULTS:"] + ([f"  {n}" for n in named] or [" NONE"])


def smart_install_log(console, number):
    """SMART SENSOR INSTALL LOG, the P on the install log screen.

    576013-818 Rev AB Figures 6-28, 6-31 and 6-32 draw the same paper under
    all three kinds of sensor:

        SMART SENSOR INSTALL LOG
        - - - - - - - - - - - -
        MMM DD, YYYY HH:MM XM
        s1 MAG SENSOR
        SERIAL NUMBER:  111111

    The moment is 333's install event and the serial is the one 333 prints,
    so the paper and the port name one install. It printed the generic
    sensor status report. FIDELITY D30.
    """
    from . import wiresensors
    word = SMART_TYPE_WORDS.get(console.sensor_type("smart", number))
    if word is None:
        return None
    when = wiresensors.install_time(console, number)
    serial = wiresensors._smart_serial(console, number)
    return header(console)[:-1] + [
        "SMART SENSOR INSTALL LOG", "- " * 11 + "-", clock_words(when),
        f"s{number} {word}", f"SERIAL NUMBER:  {serial}"]


def ss_comm_diag(console, number):
    """SS COMM DIAG, 577013-800 Rev P p.20-44, 577013-937 Rev J Figure 45
    and 577013-819 Rev F p.34, all three the same:

        SS COMM DIAG
        -----------------------
        s 1: AFM1   FP1-2
        SAMPLES READ    58
        SAMPLES USED    54
        PARITY ERR       0
        PARTIAL READ     0
        COMM ERR         0
        RESTARTS         0

    Every PRINT on SMART SENSOR DIAGNOSTIC gave the generic sensor status
    report. FIDELITY I11; the counters are UNKNOWNS A56.
    """
    from . import wiresensors
    names = ("SAMPLES READ", "SAMPLES USED", "PARITY ERR", "PARTIAL READ",
             "COMM ERR", "RESTARTS")
    counts = wiresensors.ss_comm_counts(console, number)
    return _ss_block(console, "SS COMM DIAG", number) + [
        name + str(count).rjust(18 - len(name))
        for name, count in zip(names, counts)]


def ss_constants_diag(console, number):
    """SS CONSTANTS DIAG, the same three pages:

        SS CONSTANTS DIAG
        -----------------------
        s 1: AFM1   FP1-2

        VAPOR PRESSURE
        SERIAL NUMBER     1007
        PROTOCOL VERSION     0
    """
    from . import wiresensors
    _code, name = wiresensors.SMART_TYPE.get(
        console.sensor_type("smart", number), wiresensors.SMART_UNKNOWN)
    serial = wiresensors._smart_serial(console, number)
    protocol = wiresensors.smart_protocol(number)
    return _ss_block(console, "SS CONSTANTS DIAG", number) + [
        "", name,
        "SERIAL NUMBER" + str(serial).rjust(22 - len("SERIAL NUMBER")),
        "PROTOCOL VERSION" + str(protocol).rjust(22 - len("PROTOCOL VERSION"))
    ] + [label + text.rjust(22 - len(label))
         for label, text in _ss_type_constants(console, number)]


def _ss_type_constants(console, number):
    """The rows SS CONSTANTS DIAG adds for a sensor of each kind.

    576013-818 Rev AB Figure 6-28 prints a Mag sensor's MODEL, LENGTH,
    GRADIENT, MIN and MAX THRESHOLD, NUM FLOATS, TEMPERATURE and INSTALL POS;
    Figures 6-31 and 6-32 print a Vac and an ATMP sensor's MODEL, SLOPE and
    OFFSET. The values are the ones B36 serves for the same sensor, so the
    paper and the port agree. An ISD sensor's Figure 45 prints none of them.
    FIDELITY D26.
    """
    from . import wiresensors
    kind = console.sensor_type("smart", number)
    if kind == wiresensors.MAG:
        v = wiresensors._mag_constants(console, number)
        return [("MODEL", f"{v[0]:.0f}"), ("LENGTH", f"{v[1]:.1f}"),
                ("GRADIENT", f"{v[2]:.3f}"), ("MIN THRESHOLD", f"{v[3]:.1f}"),
                ("MAX THRESHOLD", f"{v[4]:.1f}"),
                ("NUM FLOATS", f"{v[5]:.0f}"),
                ("TEMPERATURE", "YES" if v[6] else "NO"),
                ("INSTALL POS", "YES" if v[7] else "NO")]
    if kind == wiresensors.VAC:
        v = wiresensors._vac_constants(console, number)
        return [("MODEL", f"{v[0]:.0f}"), ("SLOPE", f"{v[1]:.3f}"),
                ("OFFSET", f"{v[2]:.3f}")]
    if kind == wiresensors.ATMP:
        v = wiresensors._atmp_constants(console, number)
        return [("MODEL", f"{v[0]:.0f}"), ("SLOPE", f"{v[2]:.3f}"),
                ("OFFSET", f"{v[3]:.3f}")]
    return []


def ss_channel_diag(console, number):
    """SS CHANNEL DIAG, the same three pages:

        SS CHANNEL DIAG
        -----------------------
        s 1: AFM1   FP1-2
        YY-MM-DD  HH:MM:SS
        C00 B50B 3D68 00E0 0000
        ...
        C20 0709 0032 04C9 880F
    """
    from . import wiresensors
    words = wiresensors.ss_channel_words(console, number)
    stamp = time.strftime("%y-%m-%d  %H:%M:%S", console.now())
    return _ss_block(console, "SS CHANNEL DIAG", number) + [stamp] + [
        f"C{i:02d} " + " ".join(f"{w:04X}" for w in words[i:i + 4])
        for i in range(0, 24, 4)]


def ps_calibration(console, number):
    """VAPOR PRESSURE SENSOR / CALIBRATION HISTORY, 577013-800 Rev P p.20-45
    and 577013-937 Rev J Figure 46.

        VAPOR PRESSURE SENSOR
        CALIBRATION HISTORY

        s 1: VAPOR PRESSURE
        DATE: MM-DD-YY HH:MM
        SERIAL #: XXXXXXXX
        SLOPE:  XXXX.XXX
        OFFSET: XXXX.XXX
        CALB STATUS: PASS

    One entry per calibration, newest first, ending with the factory one.
    FIDELITY I11.
    """
    from . import readings
    out = header(console)[:-1] + ["VAPOR PRESSURE SENSOR",
                                  "CALIBRATION HISTORY"]
    serial = readings.digits(8, "ss", number)
    for at, slope, offset, passed in console.calibration_history(
            "smart", number, most=10):
        out += ["", f"s {number}: VAPOR PRESSURE",
                "DATE: " + time.strftime("%m-%d-%y %H:%M",
                                         time.localtime(at)),
                f"SERIAL #: {serial}", f"SLOPE:  {slope:8.3f}",
                f"OFFSET: {offset:8.3f}",
                f"CALB STATUS: {'PASS' if passed else 'FAIL'}"]
    return out


def _sump_label(console, n):
    return console.text("722", n) or f"SUMP {n}"


def _sump_head(console, titles, rule=False, at=None, gap=1, station=True):
    """The station header, then the Mag sump reports' own order.

    All four of 576013-610 Rev AC's sump figures put the title OVER the
    stamp, where every other report here stamps first: `MAG SUMP LEAK TEST`
    / `IN PROGRESS` / the spaced rule / the stamp on Figure 24-1, and the
    same without the rule on Figure 24-2, p.23-1 and p.23-2. `at` stamps a
    report with the moment it describes rather than the moment it prints.
    """
    out = header(console)[:-1] if station else []
    out += titles
    if rule:
        out.append(SETUP_RULE)
    out += [""] * gap
    out.append(clock_words(at) if at is not None else console.clock_stamp())
    return out


def sump_slip(console, test, at, station=True):
    """MAG SUMP LEAK TEST, IN PROGRESS or RESULT: 576013-610 Rev AC Figures
    24-1 and 24-2.

        MAG SUMP LEAK TEST              MAG SUMP LEAK TEST
        IN PROGRESS                     RESULT
        - - - - - -  - - - - - -
                                        MMM DD, YYYY  HH:MM XM
        FEB 21, 2005  10:00 AM          S  1:  SUMP 1
        S 1: SUMP 1
                                        RESULT: TEST ABORTED
        STATUS:MEASURING HEIGHT         REASON:WATER TOO LOW
        START TIME:                     START TIME:
         FEB 19, 2005  9:43 AM            FEB 19, 2005  9:43 AM
        START HT:     20.971 IN.        START HT:      5.710 IN
        ...

    One report, whether PRINT asked for it or the console printed it by
    itself. FIDELITY U1b.
    """
    from . import sumpreports
    head = f"S {test.sensor}: {_sump_label(console, test.sensor)}"
    if test.running:
        out = _sump_head(console, ["MAG SUMP LEAK TEST", "IN PROGRESS"],
                         rule=True, at=at, station=station)
        return out + [head, ""] + sumpreports.in_progress_rows(console, test,
                                                               at)
    out = _sump_head(console, ["MAG SUMP LEAK TEST", "RESULT"], at=at,
                     station=station)
    return out + [head, ""] + sumpreports.result_rows(console, test)


def sump_report(console, only=None):
    """PRINT on MAG SUMP LEAK TEST: "the status of the current test, if in
    progress, or the last completed test", 576013-610 Rev AC p.24-5."""
    numbers = [only] if only else console.mag_sensors()
    if not numbers:
        return header(console, "MAG SUMP LEAK TEST") + ["NO SENSORS PROGRAMMED"]
    now = time.mktime(console.now())
    out = []
    for n in numbers:
        station = not out
        if out:
            out.append("")
        test = console.sumps.tests.get(n)
        if test is not None:
            out += sump_slip(console, test, now, station=station)
            continue
        # p.24-3 annotates this screen "Press PRINT to printout Mag Sump Leak
        # Test (no test data available)" and draws no such printout: this is
        # 317's status word under Figure 24-1's heading. UNKNOWNS A53.
        out += _sump_head(console, ["MAG SUMP LEAK TEST"], station=station)
        out += [f"S {n}: {_sump_label(console, n)}", "",
                "NO TEST DATA AVAILABLE"]
    return out


def sump_last_passed(console, only=None):
    """576013-610 Rev AC p.23-1: "Press PRINT to printout the last passed Mag
    Sump Sensor leak test results".

        MAG SUMP LEAK TEST
        LAST PASSED TEST


        MMM DD, YYYY HH:MM XM

        s 1: SUMP 1

        RESULT:      TEST PASSED
        START TIME:
        MMM DD, YYYY HH:MM XM
        START HT:      22.971 IN
        ...
    """
    from . import sumpreports
    numbers = [only] if only else console.mag_sensors()
    if not numbers:
        return header(console, "MAG SUMP LEAK TEST") + ["NO SENSORS PROGRAMMED"]
    out = []
    for n in numbers:
        station = not out
        if out:
            out.append("")
        out += _sump_head(console, ["MAG SUMP LEAK TEST", "LAST PASSED TEST"],
                          gap=2, station=station)
        out += ["", f"s {n}: {_sump_label(console, n)}", ""]
        test = console.sumps.last_passed(n)
        out += (sumpreports.last_passed_rows(console, test) if test
                else ["NO TEST DATA AVAILABLE"])
    return out


def _sump_pairs(tests):
    """p.23-2's entries: the start of the Measuring Height Phase and the
    height it started at, a blank line between each."""
    from . import sumpreports
    if not tests:
        return ["NO TEST PASSED"]
    out = []
    for i, test in enumerate(tests):
        if i:
            out.append("")
        # p.23-2's grid sets `22.971` two columns right of p.23-1's, which
        # would be twenty-six on a roll that is twenty-four (`WIDTH`) and
        # fold; read as the grid's own drift, and set as p.23-1 sets it.
        out += ["START TIME:", clock_words(test.start_at),
                sumpreports.row("START HT:", f"{test.start_ht:.3f}", " IN")]
    return out


def sump_history(console, only=None):
    """MAG SUMP LEAK TEST HISTORY, 576013-610 Rev AC p.23-2.

        MAG SUMP LEAK TEST HISTORY

        MMM DD, YYYY  HH:MM XM
        s 1: SUMP 1

        LAST 10 TESTS PASSED:
        START TIME:
        FEB 19, 2005  9:43 AM
        START HT:        22.971 IN
        :
        LAST PASSED EACH YEAR:
        ...

    "the last test results and the last passed test for each year, up to the
    last 10 years". This printed one heading over `NO TEST PASSED` whatever
    had been run, because there were no records to print. The second section
    was missing altogether. `NO TEST PASSED` is still this project's own
    words for an empty section. See FIDELITY U1b.
    """
    numbers = [only] if only else console.mag_sensors()
    if not numbers:
        return header(console, "MAG SUMP LEAK TEST HISTORY") + [
            "NO SENSORS PROGRAMMED"]
    out = []
    for n in numbers:
        station = not out
        if out:
            out.append("")
        out += _sump_head(console, ["MAG SUMP LEAK TEST HISTORY"],
                          station=station)
        out += [f"s {n}: {_sump_label(console, n)}", "",
                "LAST 10 TESTS PASSED:"]
        out += _sump_pairs(console.sumps.last_ten(n))
        out += ["", "LAST PASSED EACH YEAR:"]
        out += _sump_pairs(console.sumps.each_year(n, 10))
    return out


def leak_history(console, kind="plld", only=None):
    """"the last 3.0 gph, the first 0.2 gph, and the first 0.1 gph test
    results for each month".

    A LINE report, and only a line report. 576013-610 Rev AC draws
    PRESSURE LINE LEAK TEST HISTORY, WPLLD LINE LEAK TEST HISTORY and MAG
    SUMP LEAK TEST HISTORY, and draws no in-tank leak test history at all --
    so this used to carry a fourth entry, `IN-TANK LEAK TEST HISTORY`, an
    invented title over the line report's body with its gph rate headings.
    Nothing reached it: the panel's `history` screens name `plld`, `wplld`
    and `sump`, and the tank's own history is `tank_leak_history` above,
    under the name the serial manual gives it. See FIDELITY W7.
    """
    titles = {"plld": "PRESSURE LINE LEAK TEST HISTORY",
              "wplld": "WPLLD LINE LEAK TEST HISTORY",
              "vlld": "LINE LEAK TEST HISTORY"}
    letter = {"plld": "Q", "wplld": "W", "vlld": "P"}[kind]
    label_code = {"plld": "782", "wplld": "7A2", "vlld": "760"}[kind]
    out = header(console, titles.get(kind, "LEAK TEST HISTORY"))
    devices = sorted({d for k, d in console.leaks.history if k == kind})
    if only:
        devices = [d for d in devices if d == only]
    if not devices:
        # The one of these that the console HAS a phrase for: UNKNOWNS A17
        # names `NO TEST DATA AVAILABLE` as one of the three the manuals
        # draw, and this report is test data. See FIDELITY U5.
        out.append("NO TEST DATA AVAILABLE")
        return out
    for device in devices:
        out.append("")
        out.append(f"{letter} {device}: "
                   f"{console.text(label_code, device) or ''}".rstrip())
        out.append("")
        last = console.leaks.last_pass(kind, device, "gross")
        # "NO TEST PASSED" is what a real console prints here, twice on one
        # slip in a 1996 roll. This used to say NO PASS RECORDED, which is in
        # no manual and on no paper: the per-feature phrase invented for one
        # screen that UNKNOWNS A17 warns about, one that survived that sweep.
        out.append("LAST 3.0 GAL/HR PASS:")
        out.append(clock_words(last) if last else "NO TEST PASSED")
        for rate_key, name in (("periodic", "0.20"), ("annual", "0.10")):
            out.append("")
            out.append(f"FIRST {name} GAL/HR PASS EACH MONTH:")
            out.append("")
            months = console.leaks.first_pass_each_month(kind, device,
                                                        rate_key)
            if not months:
                out.append("NO TEST PASSED")
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
    if which == "offset":
        return out + _offset_diag(ln)
    return out + _precision_diag(console, ln, which)


def _offset_diag(ln):
    """P OFFSET DIAG, 577013-344 Rev H Figure 13.

        PO: Pass
        Pd: Fail
           Pd = 40.0 psi
           Pd Ref = 32.0 psi
        Pv: Pass
           Pv = 30.1 psi
           Pon = 44.1 psi
           Pd = 40.0 psi

    "The PO state will always be pass. The Pd state will be either pass or
    fail. The Pv state will be either pass or fail." Pv is the P2 measured
    in the last gross test and Pon its pump-on pressure, so both come off
    that test rather than being measured again.

    This screen carried `diagprint: offset` in the data and there was no
    renderer for it: `line_diag` handled the four printouts of Figures 9 to
    12 and this fifth one raised KeyError, so PRINT on the screen crashed.
    See FIDELITY U1.
    """
    from .pressure import PD_REF_UNKNOWN
    def state(ok):
        return "Pass" if ok else "Fail"
    pd = ln.pd
    ref = ln.pd_ref if ln.pd_ref is not None else PD_REF_UNKNOWN
    out = ["", "PO: Pass"]
    # Pd fails when it has grown away from the reference it was first
    # recorded at, which is what the whole monitor is for
    grown = pd is not None and ln.pd_ref is not None and pd - ln.pd_ref > 5.0
    out.append(f"Pd: {state(not grown)}")
    out.append(f"   Pd = {pd:.1f} psi" if pd is not None
               else "   Pd = UNKNOWN")
    out.append(f"   Pd Ref = {ref:.1f} psi")
    # "Pv = P2 measured in last gross test", "Pon = pump on pressure
    # measured in last gross test": both come off that record rather than
    # being measured again
    last = (ln.readings.get("gross") or [None])[-1]
    pv = getattr(last, "p2", None)
    pon = getattr(last, "pon", None)
    if pon is None:
        pon = ln.pon
    high = pv is not None and pv > (pon or 0.0)
    out.append(f"Pv: {state(not high)}")
    out.append(f"   Pv = {(pv or 0.0):.1f} psi")
    out.append(f"   Pon = {(pon or 0.0):.1f} psi")
    if pd is not None:
        out.append(f"   Pd = {pd:.1f} psi")
    return out


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


# ---------------------------------------------------------------------------
# What the console prints without being asked.
#
# The console does print by itself, and well -- a finished delivery, a leak
# test's slips and report, an auto-dial confirmation, an AccuChart update and
# a newly posted alarm all reach the paper on their own. What that mechanism
# was missing is a home: it sat inside the UI's refresh loop, so
# `run.py --headless` printed none of it and the queues it drains grew
# without bound on a console nobody was watching. Deciding WHAT is on the
# paper is this module's, so it is here, and a caller asks the console what
# it owes and puts what comes back on the roll. Four printouts the manuals
# state and nothing in the package produced are below with the rest of them.
# See FIDELITY P2.
# ---------------------------------------------------------------------------
AUTO_MARKS = "_auto_printed"


def _marks(console):
    """The watermarks the dispatcher keeps, made on first use.

    A queue can be drained and a clock cannot, so the timed printouts have
    to remember how far they have got. The console does not carry that, so
    the dispatcher carries it beside the console it belongs to, primed from
    where that console already is: the first call prints what has happened
    since it was asked, not the day's history over again.
    """
    marks = getattr(console, AUTO_MARKS, None)
    if marks is not None:
        return marks
    marks = {"clock": time.mktime(console.now()), "bir": {}, "loads": {},
             "posted": {a["aa"] + a["nn"] + a["tt"]
                        for a in describe_alarms(console.compute_alarms())}}
    for (_tank, kind), rows in console.bir.closed.items():
        if rows:
            marks["bir"][kind] = max(marks["bir"].get(kind, 0.0),
                                     rows[0]["closed"])
    for tank, rows in console.loads.records.items():
        if rows and rows[0].end:
            marks["loads"][tank] = rows[0].end["at"]
    setattr(console, AUTO_MARKS, marks)
    return marks


def _occurrences(before, now, hhmm):
    """The moments a programmed HHmm fell in the window just gone.

    Yesterday's occurrence as well as today's, because a window that runs
    over midnight would otherwise lose the time inside it. This is the walk
    `bir._scheduled` makes over its own closing times, for the same reason.
    -> struct_times, so a caller can ask what DAY each one fell on.
    """
    stamp = time.localtime(now)
    out = []
    for day in (-1, 0):
        when = time.mktime((stamp.tm_year, stamp.tm_mon, stamp.tm_mday + day,
                            int(hhmm[:2]), int(hhmm[2:]), 0, 0, 1, -1))
        if before < when <= now:
            out.append(time.localtime(when))
    return out


def _due(before, now, hhmm):
    """Whether a programmed HHmm fell in the window just gone."""
    return bool(_occurrences(before, now, hhmm))


def _time_setting(console, code):
    """A programmed HHmm, or None where the screen reads DISABLED.

    The tape's console holds shift 1 as `0400` and the three shifts it does
    not use as `EE00`, which is the disabled mark and not four o'clock. A
    device numbered store carries the device in the first two characters,
    which is what the extra pair is when there is one.
    """
    raw = (console.values.get(code) or "").strip()
    body = raw[2:] if len(raw) > 4 else raw
    if len(body) != 4 or not body.isdigit():
        return None
    return body if int(body[:2]) < 24 and int(body[2:]) < 60 else None


def _auto_deliveries(console):
    """"When the system recognizes that a delivery occurred, an adjusted
    delivery report is automatically printed"."""
    out = []
    for tank, record in console.printed_deliveries:
        out.append((f"-- PRINT: delivery on tank {tank}, "
                    f"{record.amount:.0f} gallons",
                    delivery(console, tank, record)))
        if console.licensed("bir"):
            # a reconciling console prints the adjusted report too, which
            # "takes into consideration all dispensing that occurred during
            # the delivery"
            out.append((f"-- PRINT: adjusted delivery on tank {tank}",
                        adjusted_delivery(console, tank, record)))
    console.printed_deliveries.clear()
    return out


def _auto_leak_slips(console):
    """A STOP slip is followed by that tank's LEAK TEST REPORT, the way the
    real compliance roll runs."""
    out = []
    for slip in console.leaks.printed_slips:
        if slip[0] == "start":
            out.append((f"-- PRINT: leak test started on tank "
                        f"{slip[1].device}",
                        leak_start_slip(console, slip[1])))
            continue
        _kind, device, when = slip
        out.append((f"-- PRINT: leak test stopped on tank {device}",
                    leak_stop_slip(console, device, when)))
        out.append((f"-- PRINT: leak test report, tank {device}",
                    tank_leak_report(console, device)))
    console.leaks.printed_slips.clear()
    return out


def _auto_sumps(console):
    """Three printouts on one page, 576013-610 Rev AC p.24-1: "The TLS will
    automatically print that the Test Phase was started", "... that the
    Measuring Height Phase was started", and "the test result when the test
    has been completed". The page draws none of the first two, so each is
    the IN PROGRESS report as it stood at that moment. UNKNOWNS A53."""
    words = {"started": "test phase started",
             "measuring": "measuring height started", "result": "result"}
    out = [(f"-- PRINT: mag sump leak test {words[what]}, sensor {n}",
            sump_slip(console, test, at))
           for what, n, test, at in console.sumps.printed]
    console.sumps.printed.clear()
    return out


def generator_slip(console, number, on, when):
    """GENERATOR ON, GENERATOR OFF: "messages are printed whenever the
    generator turns on and off", 576013-623 p.23-2. Three lines, the way
    the leak test slips are cut: the message, the input, the stamp."""
    label = console.inputs.label(number)
    return [f"GENERATOR {on}",
            f"I {number}:{label}".rstrip(),
            clock_words(when)]


def _auto_generator(console):
    out = []
    for on, number, when in console.inputs.printed:
        out.append((f"-- PRINT: generator {on.lower()} on input {number}",
                    generator_slip(console, number, on, when)))
    console.inputs.printed.clear()
    return out


def _auto_confirmations(console):
    """"Confirmation Report": it prints after a successful auto-dial call."""
    out = [(f"-- PRINT: confirmation report, receiver {receiver}",
            ["CONFIRMATION REPORT", f"RECEIVER {receiver}: CONNECTED",
             console.clock_text()])
           for receiver in console.autodial.confirm_pending]
    console.autodial.confirm_pending.clear()
    return out


def _auto_accuchart(console):
    """"Each time an AccuChart calibration is updated, a user notification
    message is sent to the local printer"."""
    out = [(f"-- PRINT: accuchart update on tank {tank}",
            accuchart_update(console, tank, when))
           for tank, when in console.accuchart_log]
    console.accuchart_log.clear()
    return out


def _auto_alarms(console, marks):
    """"If your system has a printer, it will print an alarm or warning
    report when it detects a warning or alarm condition"."""
    keys = {a["aa"] + a["nn"] + a["tt"]
            for a in describe_alarms(console.compute_alarms())}
    fresh = keys - marks["posted"]
    marks["posted"] = keys
    # An ISD site or hose alarm prints its own slip, 577013-800 Rev P Figures
    # 21 and 22; anything else posted prints the report ALARM/TEST prints.
    isd_keys = sorted(k for k in fresh if k[:2] in ("30", "31"))
    out = [("-- PRINT: ISD %s alarm" % ("hose" if k[:2] == "31" else "site"),
            isd_alarm_slip(console, k)) for k in isd_keys]
    if fresh - set(isd_keys):
        out.append(("-- PRINT: alarm posted", alarms(console)))
    return out


def _auto_shift_inventory(console, before, now):
    """The inventory report a programmed shift start time prints.

    576013-623 Rev AN, Shift Start Times: "At each programmed time, the
    system automatically prints a complete inventory report and stores it in
    memory". The day-end report is the same one and needs nothing of its
    own: a site with fewer than three shifts is told to "use the next shift
    start time as the day-end time. The system automatically prints a final
    inventory". The shift half of this was built -- `bir` closes the shift
    on time and writes its row -- and nothing printed. See FIDELITY P2.
    """
    out = []
    for shift_no in range(1, REPEATS + 1):
        hhmm = _time_setting(console, "S502%02d" % shift_no)
        if hhmm and _due(before, now, hhmm):
            out.append((f"-- PRINT: shift {shift_no} inventory",
                        inventory(console)))
    return out


def _auto_fuel(console, before, now):
    """The fuel management report a site can ask for once a day.

    576013-623 Rev AN p.128: "This display lets you set a daily time when
    the system will automatically print a fuel management report. The report
    printed is a 'Short Report'" -- which is why this asks for the short one
    and not the seven average-sales rows under it. `S682` held the time and
    was read by nothing. See FIDELITY P2.
    """
    hhmm = _time_setting(console, "S68200")
    if hhmm and _due(before, now, hhmm):
        return [("-- PRINT: fuel management report",
                 fuel(console, long=False))]
    return []


def _auto_csld(console, before, now):
    """CSLD TEST RESULTS, which the console prints by itself.

    576013-818 Rev AB ch.11: "Test results are provided automatically every
    24 hours at 8:00 a.m." 576013-610 Rev AC: "except when the CSLD Report
    Only feature is enabled in setup" -- and 576013-623 Rev AN p.8-9 says
    what that feature prints instead: "only prints CSLD status reports at one
    of the times selected below: End of Month (at 8:00 a.m.), Day 15 and End
    of Month (both at 8:00 a.m.), or Day 25 and End of Month (both at 8:00
    a.m.)". Nothing printed CSLD results at any of those times. A set is
    reported under its primary tank, and the tanks due at one moment share
    one report. FIDELITY K6.
    """
    import calendar
    from . import csld as csldmod
    hhmm = "%02d00" % csldmod.REPORT_ONLY_HOUR
    out = []
    for when in _occurrences(before, now, hhmm):
        month_end = calendar.monthrange(when.tm_year, when.tm_mon)[1]
        due = []
        for tank in sorted(console.tank_level):
            if (not console.csld.enabled(tank)
                    or console.csld.primary(tank) != tank):
                continue
            only = console.csld.report_only(tank)
            if (only == csldmod.REPORT_ONLY_OFF
                    or when.tm_mday == month_end
                    or when.tm_mday in csldmod.REPORT_ONLY_DAYS[only]):
                due.append(tank)
        if due:
            out.append(("-- PRINT: CSLD test results", csld(console, due)))
    return out


def _auto_bir(console, marks):
    """The BIR report a closed shift or a closed day prints.

    576013-623 Rev AN: "Shift BIR Printouts enabled causes a BIR report to
    print at the end of every shift", and "Daily BIR Printouts enabled
    causes a BIR report to print at the end of the day's last shift". Both
    flags were stored, printed back on the setup report, and read by
    nothing. The report is the CLOSED period's, not the running one, which
    is what `previous` asks for. See FIDELITY P2.
    """
    if not console.licensed("bir"):
        return []
    out = []
    for kind, code in (("shift", "S51100"), ("daily", "S51200")):
        if not (console.values.get(code) or "").strip().endswith("1"):
            continue
        closed = [rows[0]["closed"]
                  for (_tank, one), rows in console.bir.closed.items()
                  if one == kind and rows]
        if not closed or max(closed) == marks["bir"].get(kind):
            continue
        marks["bir"][kind] = max(closed)
        out.append((f"-- PRINT: {kind} BIR report",
                    reconcile(console, kind=kind, previous=True)))
    return out


def _auto_loads(console, marks):
    """The report a finished tanker load prints.

    576013-623 Rev AN, Tanker Load Report: "The Tanker Load Report is an
    optional feature. In the ENABLE position, a report is printed after
    every tanker load is dispensed." `Loads.enabled` is that position, the
    loads were recorded, and nothing put one on the paper. One load, not
    the tank's forty: it is the one that was just dispensed.
    See FIDELITY P2.
    """
    if not console.loads.enabled():
        return []
    out = []
    for tank in sorted(console.loads.records):
        rows = console.loads.all(tank)
        if not rows or not rows[0].end:
            continue
        at = rows[0].end["at"]
        if at == marks["loads"].get(tank):
            continue
        marks["loads"][tank] = at
        out.append((f"-- PRINT: tanker load report, tank {tank}",
                    loads(console, [tank], index=0)))
    return out


def automatic(console):
    """Everything the console owes the paper now, in the order it owes it.

    Each entry is a pair: the line the bench log says it printed, and the
    report itself. Asking DRAINS the console's print queues and moves the
    dispatcher's watermarks, so whatever asks must put the answer on the
    roll -- ask twice and the second answer is empty, which is the point of
    the queues being drained at all.
    """
    marks = _marks(console)
    now, before = time.mktime(console.now()), marks["clock"]
    out = []
    out.extend(_auto_deliveries(console))
    out.extend(_auto_leak_slips(console))
    out.extend(_auto_sumps(console))
    out.extend(_auto_generator(console))
    out.extend(_auto_confirmations(console))
    out.extend(_auto_accuchart(console))
    out.extend(_auto_shift_inventory(console, before, now))
    out.extend(_auto_fuel(console, before, now))
    out.extend(_auto_csld(console, before, now))
    out.extend(_auto_bir(console, marks))
    out.extend(_auto_loads(console, marks))
    out.extend(_auto_alarms(console, marks))
    marks["clock"] = now
    return out
