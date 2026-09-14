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
import re
import time

from . import alarmreports
from . import wiretables
from . import leaktest
from . import masks
from . import screens
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

    **It cannot be.** The inventory grid was measured off a photograph of
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

    *This is the weakest row on the report and it is the only one with no
    paper behind it.* See FIDELITY X8.
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
    vol, water = st.get("volume", 0.0), st.get("water", 0.0)
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
# **They DO carry the END mark, and this comment used to say they did not.**
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
    # **A report with nothing to list prints its heading and stops.**
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

    **Three things, and both manuals count them the same way.**
    576013-610 Rev AC p.29-1: "This report shows the type and location of
    the warning or alarm **and the date and time it occurred**." 576013-939
    Quick Help p.12 says it again independently: "the warning or alarm
    type, its location and **the date and time the warning or alarm
    condition occurred**."

    The slip carried two of the three. The only stamp on it was the
    header's, which is when the SLIP was printed -- and the occurrence time
    is the half a technician writes on the work order. The console has held
    it all along: `_log_alarms` stamps every `02` row and the Alarm History
    reports read exactly this. See the alarm-lifecycle audit, A7.

    *The stamp's place on the slip is the console's own shape rather than a
    drawing*, because no page draws this report at all -- its head is
    UNKNOWNS A31 for the same reason. `alarm_history` indents a stamp under
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
    for record in console.alarm_log:
        if record.get("state") != "02":
            continue
        if record["aa"] + record["nn"] + record["tt"] == key:
            return _when(record["at"])
    return ""


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
        # **A run with no device in it has no device to be absent.** The
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
        # *The one sentence that could be read the other way is quoted
        # here so a reader can disagree*: p.5-1 calls the report "a record
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
                        # **Asked of the STEP, not of the value's shape.**
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
    printout, one name, whichever port asks for it. **576013-610 draws no
    in-tank leak test HISTORY anywhere**, which is W7's question and its
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
            # A17 retired `NO DELIVERY DATA` by name; this was the same
            # invention with a word on the end. See FIDELITY U5.
            pass
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

    **This sentence used to say the invention was recorded in UNKNOWNS.md
    and it was not** -- not there, not in FIDELITY.md, not anywhere. It is
    UNKNOWNS A30 now. A citation to a register entry is worth exactly as
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
        out = header(console, "PUMP RELAY MONITOR STATUS")
        for n in range(1, console.capacity("pumpmon") + 1):
            label = console.text("7C5", n) or f"MONITOR {n}"
            out.append(f"r {n}:{label[:18]:18s} "
                       + ("ON" if console.relays.get(n) else "OFF"))
        # "FITTED" is the BENCH's word for a card in a bay, and it was on
        # a printed page. A console with no monitor prints the heading and
        # no rows. See FIDELITY U5.
        return out
    out = header(console, "OUTPUT RELAY SETUP")
    for n in range(1, max(console.capacity("relay"),
                          console.capacity("io")) + 1):
        label = console.text("807", n) or f"RELAY {n}"
        state = "ON" if console.outputs.energised(n) else "OFF"
        out.append(f"R {n}:{label[:18]:18s} {state}")
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
# **Two of them have no title at all**, and that is the manual's doing rather
# than an omission here: function 352 (VLLD) goes straight from the station
# header to `P 1:REGULAR UNLEADED`, and function 402 (external input) to
# `INPUT   LOCATION`. A letter absent from this table and present in
# HISTORY_UNTITLED prints its device line and no title. See W25.
#
# **X is VMCI and x is VMC**, which this table had the other way round. The
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
    # **Twelve** of the fourteen titles are longer than the roll -- only
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
        out.append(f"TICKET VOL : {row['ticketed']:9.0f} GAL")
        out.append(f"GAUGED VOL : {row['deliveries']:9.0f} GAL")
        # THIS report's own direction, p.28-7: "difference between gauged and
        # ticketed delivery volumes", and its sample prints 99 for a ticket
        # of 800 against a gauge of 899. The Variance Analysis report on
        # p.28-14 defines the same words the other way round and prints -99
        # for the same pair. Both are right on their own page. FIDELITY G9.
        out.append(f"DLVY VAR   : {var['gauged_delivery_var']:9.0f} GAL")
        pct = (var["gauged_delivery_var"] / sales * 100.0) if sales else 0.0
        out.append(f"% VAR SALES: {pct:9.2f}%")
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


def sump_history(console, only=None):
    """MAG SUMP LEAK TEST HISTORY, 576013-610 Rev AC p.4646.

        MAG SUMP LEAK TEST HISTORY
        s 1: SUMP 1
        LAST 10 TESTS PASSED:
        START TIME:
        FEB 19, 2005  9:43 AM
        START HT:         22.971 IN

    A sump history is not the line report's shape -- no rates, one list of
    the last ten passes with the height each started at. This screen used to
    ask `leak_history` for a "sump" kind it did not have, so PRINT on it
    raised KeyError. The records themselves are not modelled yet, which is
    why a console with no sump test history prints the heading and
    NO TEST PASSED under it. See FIDELITY U1.
    """
    out = header(console, "MAG SUMP LEAK TEST HISTORY")
    numbers = [only] if only else sorted(
        n for _m, n, _l in console.programmed_sensors() if _m == "smart")
    if not numbers:
        out.append("NO SENSORS PROGRAMMED")
        return out
    for n in numbers:
        out.append("")
        out.append(f"s {n}: {console.text('722', n) or ''}".rstrip())
        out.append("LAST 10 TESTS PASSED:")
        out.append("NO TEST PASSED")
    return out


def leak_history(console, kind="plld", only=None):
    """"the last 3.0 gph, the first 0.2 gph, and the first 0.1 gph test
    results for each month".

    **A LINE report, and only a line report.** 576013-610 Rev AC draws
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


def _due(before, now, hhmm):
    """Whether a programmed HHmm fell in the window just gone.

    Yesterday's occurrence as well as today's, because a window that runs
    over midnight would otherwise lose the time inside it. This is the walk
    `bir._scheduled` makes over its own closing times, for the same reason.
    """
    stamp = time.localtime(now)
    for day in (-1, 0):
        when = time.mktime((stamp.tm_year, stamp.tm_mon, stamp.tm_mday + day,
                            int(hhmm[:2]), int(hhmm[2:]), 0, 0, 1, -1))
        if before < when <= now:
            return True
    return False


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
    return [("-- PRINT: alarm posted", alarms(console))] if fresh else []


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
    out.extend(_auto_generator(console))
    out.extend(_auto_confirmations(console))
    out.extend(_auto_accuchart(console))
    out.extend(_auto_shift_inventory(console, before, now))
    out.extend(_auto_fuel(console, before, now))
    out.extend(_auto_bir(console, marks))
    out.extend(_auto_loads(console, marks))
    out.extend(_auto_alarms(console, marks))
    marks["clock"] = now
    return out
