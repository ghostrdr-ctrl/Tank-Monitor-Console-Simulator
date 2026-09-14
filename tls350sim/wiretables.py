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
"""What a Display inquire on a SETUP code actually answers with.

576013-635 does not answer `I621TT` with a number. It answers with a titled
table:

    TANK LOW PRODUCT LIMIT

    TANK   PRODUCT LABEL             GALLONS
     1     REGULAR UNLEADED            1000

and for the console-wide codes with a titled line, whose label and column
are equally the manual's:

    TEMP COMPENSATION
    VALUE (DEG F ):   60.0

This console used to answer the first with `TANK LOW PRODUCT LIMIT` over a
bare `1000`, and a device-00 inquire short-circuited to the computer
aggregate before the display side was reached at all -- so `I62100` came back
`0144FA00000244FA0000...` on the display side, which is the computer format
wearing the display's envelope. See FIDELITY S1.

**The columns are read off the page and not guessed.** `tools/
build_wire_titles.py` takes the x coordinate of every word in the manual's
own sample and divides by the page's character pitch, so `GALLONS` is at
column 33 because that is where it is printed, and `1000` right-aligns to 38
because that is where IT is printed -- which is one column short of the
heading's own right edge, and no argument from tidiness would have produced
it. A guessed column is an invention on the wire.

What this module does NOT do is decide anything the manual does not draw.
Three shapes are rendered, and they are the three the samples show:

    number, 3 columns   device, its label, the value       63 codes
    tag,    2 columns   `T 1:REGULAR UNLEADED`, the value  21 codes
    number, 2 columns   device, the value                  11 codes

A code whose sample carries columns this cannot fill -- 60E's four float
offsets, 606's twenty profile points -- returns None here and is answered
the way it was before, because half a table is worse than none.
"""

import json
import os
import re

SEP = chr(13) + chr(10)


def _titles():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "wiretitles.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


# Every Display response the manual prints, by function code: its title, the
# blank lines under it, its heading line, and the start, end, alignment and
# printed precision of each of its columns.
TITLES = _titles()

# 576013-635 p.10's code-space bands, for the handful of settable codes that
# no Setup Mode function walks -- 648 and 651-655 among them -- where the
# device family cannot be read off the panel because the panel never goes
# there. Same bands `wire.Handler._module_present` gates on.
BANDS = ((0x520, 0x52F, "D"),
         (0x601, 0x6FF, "T"), (0x701, 0x704, "L"), (0x706, 0x709, "V"),
         (0x711, 0x713, "G"), (0x721, 0x72C, "s"), (0x741, 0x744, "C"),
         (0x746, 0x749, "H"), (0x74B, 0x74E, "U"),
         (0x751, 0x761, "P"), (0x771, 0x773, "S"),
         (0x774, 0x78F, "Q"), (0x7A0, 0x7AF, "W"), (0x7C4, 0x7C9, "r"),
         (0x7D0, 0x7DF, "S"), (0x801, 0x805, "I"), (0x806, 0x80B, "R"),
         # 80C is an INPUT and this band used to end on it, so Set External
         # Input Type was reported against a RELAY. 811 to 813 are inputs too
         # and had no band at all, so they fell through to the "T" default and
         # headed their rows with a TANK's product label. Each code's own echo
         # says which it is -- `I80CII` and `I811II` against `I80BRR` -- and
         # 80B is the last "Set Relay ..." function in the manual. 80D to 810
         # are in no revision on this shelf and are left unclaimed.
         (0x80C, 0x80C, "I"), (0x811, 0x813, "I"))

# what module a device letter's card is, for the letters the panel's own
# FUNCTION_REQUIRES names differently from Table 29-1
MODULE_LETTER = {"probe": "T", "liquid": "L", "vapor": "V", "gw": "G",
                 "2wire": "C", "3wire": "H", "smart": "s", "plld": "Q",
                 "wplld": "W", "vlld": "P", "pump": "S", "pumpmon": "r",
                 "io": "I", "relay": "R"}

# What a SETUP table calls a device nobody has labelled, which is not what a
# STATUS report calls the same device. The manual is unanimous within each
# family and it differs between the two kinds of report:
#
#     I703  `     1  LIQUID SENSOR #1`     I301  `  1  LIQUID # 1`
#     I801  `     1  EXTERNAL INPUT #1`    I401  `  1  * EXTERNAL INPUT 1 *`
#     I806  `     1  OUTPUT RELAY #1`      I406  `  1  * RELAY 1  *`
#
# `wiresensors.FAMILY` already holds the status side. This is the setup side,
# and the setup tables were printing the PANEL's word instead -- the panel
# abbreviates to fit 24 columns, so `GROUNDWATER #1` came out `GRND WATER 1`
# and `2 WIRE CL SENSOR #1` came out `2-WIRE CL 1`, hyphen, no hash and no
# noun. Every code of every family agrees on these, which is why they are
# listed by letter rather than by code.
SETUP_NAME = {
    "L": "LIQUID SENSOR #{n}",          # 701, 702, 703, 704
    "V": "VAPOR SENSOR #{n}",           # 706, 707, 708, 709
    "G": "GROUNDWATER #{n}",            # 711, 712, 713
    "C": "2 WIRE CL SENSOR #{n}",       # 741, 742, 743
    "H": "3 WIRE CL SENSOR #{n}",       # 746, 747, 748, 749
    "U": "UNIVERSAL SENSOR #{n}",       # 74B, 74C, 74D, 74E
    "I": "EXTERNAL INPUT #{n}",         # 801, 803, 80C
    "R": "OUTPUT RELAY #{n}",           # 806
}

# where each of those families keeps the label a site DID programme
_LABEL_CODE = {"L": "702", "V": "707", "G": "712", "C": "742", "H": "747",
               "U": "74C", "I": "802", "R": "807"}

_LETTERS = {}


def device_letter(tok):
    """Table 29-1's letter for the devices a setup code numbers.

    The panel is asked first, because its function names are curated and
    `screens.device_code` already reads them; the card the function needs
    settles the ones the name does not -- PUMP SENSOR SETUP has neither
    "pump sensor" nor a tank in its name and its devices are `S 1:`.
    """
    if tok in _LETTERS:
        return _LETTERS[tok]
    from .console import SETUP_MENU, FUNCTION_REQUIRES
    from .screens import device_code
    letter = None
    for fn in SETUP_MENU:
        if letter is not None:
            break
        for step in fn.get("steps", []):
            if (step.get("code") or "")[1:4] == tok:
                letter = device_code(fn["function"])
                need = FUNCTION_REQUIRES.get(fn["function"]) or ()
                if letter == "T" and need and need[0] != "probe":
                    letter = MODULE_LETTER.get(need[0], letter)
                break
    if letter is None:
        try:
            number = int(tok, 16)
        except ValueError:
            number = 0
        letter = next((l for low, high, l in BANDS if low <= number <= high),
                      "T")
    _LETTERS[tok] = letter
    return letter


def to_places(shown, column):
    """The value at the precision the manual's own sample prints it to.

    A float decodes with "%g", which drops a trailing zero -- so a tank
    diameter of 120 inches came back `120` where p.254 prints `96.00`, and a
    compensation temperature came back `60` where p.169 prints `60.0`. The
    sample is the only statement of the precision the display side uses: the
    Set mask is `III.hh` on both of those codes and the two responses do not
    agree with it, or with each other.

    Left alone unless the value is a bare number. `4.0 IN` and `12000 PSI`
    carry a unit from the field tables, and reformatting the pair of them
    would drop it.
    """
    places = column.get("places")
    if places is None:
        return shown
    try:
        return "%.*f" % (places, float(str(shown).strip()))
    except (TypeError, ValueError):
        return shown


def _place(line, text, column):
    """Put a cell where the manual puts it, without running into its neighbour."""
    start = column["start"]
    if column.get("align") == "right":
        start = column["end"] - len(text) + 1
    if len(line) >= start:
        return (line + " " if line else line) + text
    return line.ljust(start) + text


def lead(tok, body):
    """A report's own blank lines, where the manual's sample leaves them.

    FIDELITY S6's second half. The header block's blank was settled by
    measuring 321 samples; this is the same measurement one level down, and
    it says the commonest place a report breaks is above its heading row.
    The lead of every line of every sample is in `wiretitles.json`, keyed on
    the line's WORDS -- a console fills in a site's own labels where the
    sample prints REGULAR UNLEADED, so the spacing cannot be the key.

    Blanks are only ever inserted, never taken away: a report that already
    leads a line is left alone, which is what keeps this from fighting with
    the renderers that were counted by hand and got it right.
    """
    wants = (TITLES.get(SHOWN_AS.get(tok, tok)) or {}).get("leads")
    if not wants or not body:
        return body
    feed = chr(10)
    lines = body.replace(SEP, feed).replace(chr(13), feed).split(feed)
    out = []
    for n, line in enumerate(lines):
        gap = wants.get(" ".join(line.split()))
        if gap and n and out and out[-1].strip():
            out += [""] * gap
        out.append(line)
    return SEP.join(out)


def columns(tok):
    """The columns the manual draws this response in, or []."""
    return (TITLES.get(tok) or {}).get("columns") or []


def station_header(tok):
    """Does this code's own sample draw the four STATION HEADER lines?

    True, False, or None for a code with no sample in 576013-635 -- the
    ISD C-series, the Troubleshooting Guide's `@` reports and the handful
    of others whose pages are in another manual.

    **It is not derivable from anything else**, which is why it is read off
    the page. The obvious rule -- that a section 7.4 DIAGNOSTIC has no
    header and a 7.2 or 7.3 report does -- is wrong twenty-seven times:
    A15, A81, A91, B62 and BB1 are diagnostics that draw one, and 207, 212,
    216, 217, 218, 317 through 323, 411, 412, 56A, 780, 7A0, 888 and 8A3
    are reports that do not. See FIDELITY L5.
    """
    return (TITLES.get(tok) or {}).get("station")


def heading(tok):
    """The heading line the manual prints, exactly as it prints it."""
    return (TITLES.get(tok) or {}).get("heading") or ""


def row(tok, cells):
    """One line of a REPORT, in the columns the manual draws it in.

    The hand-built reports each counted their own columns, and about forty of
    them were out -- FIDELITY S5. Naming the cells and letting the page place
    them means a manual revision moves the report by rebuilding the JSON,
    rather than by somebody counting spaces again.

    Cells are placed as given, except that a column whose sample states a
    precision formats a number to it. Anything the manual prints as a whole
    number is the caller's to format, because the page cannot tell a count
    from a rounded measurement.
    """
    line = ""
    for value, column in zip(cells, columns(tok)):
        line = _place(line, str(to_places(value, column)), column)
    return line.rstrip()


def parts_of(tok, dev):
    """A code's part fields, in the order the record packs them.

    605 holds a four point chart as `S60501` and three parts of it, 606 holds
    a twenty point chart as twenty, and 631 holds two flags. The bare key is
    one of the parts rather than the whole record, because it carries a
    `part` offset of its own -- so ordering by that offset is the only
    ordering that is the console's rather than somebody's guess.
    """
    from .console import FIELDS
    out = []
    for key, field in FIELDS.items():
        if not isinstance(field, dict) or field.get("part") is None:
            continue
        if key.startswith(f"S{tok}{dev}") or key.startswith(f"S{tok}01"):
            # `column` where the report does NOT print the parts in the order
            # the record packs them. 536 packs `s` then `aaaaaa` -- the
            # command format is `s536PPsaaaaaa` -- and prints SECURITY CODE
            # before STATUS. One code needs it; the offset is the ordering
            # everywhere else, because it is the console's rather than
            # somebody's guess.
            at = field.get("column")
            out.append((field["part"][0] if at is None else at, key, field))
    return [field for _at, _key, field in sorted(out)]


def _part_rows(handler, tok, device, layout, label):
    """A device's rows where the value columns are the code's part fields.

    576013-635 Rev AA p.253 draws 606's twenty chart points as five rows of
    four under one tank, with the tank and its label on the first row only,
    and that is the shape every part-field table takes. The run has to divide
    by the columns.

    **Not every one of these tables carries a label column.** 605, 606 and
    631 head their second column PRODUCT LABEL; 60E's p.260 sample heads
    its four straight across from the tank -- `TANK    WATER OFFSET
    FUEL OFFSET      INVALID FUEL     WATER MINIMUM` over
    `  1        -3.160            0.270            8.000            0.750`
    -- so counting the label column in unconditionally left four values to
    fit three columns, and the run divided by nothing. See FIDELITY R13.
    """
    labelled = (len(layout) > 1
                and "LABEL" in (layout[1].get("head") or "").upper())
    values = layout[2:] if labelled else layout[1:]
    wide = len(values)
    parts = parts_of(tok, f"{device:02d}")
    if wide < 1 or len(parts) < 2 or len(parts) % wide:
        return None
    key = f"S{tok}{device:02d}"
    val = handler.c.values.get(key)
    shown = [handler._part_shown(field, key, val) for field in parts]
    shown = ["" if one is None else str(one) for one in shown]
    if not any(shown):
        return None
    out = []
    for at in range(0, len(shown), wide):
        line = ""
        if at == 0:
            line = _place(line, str(device), layout[0])
            if labelled:
                line = _place(line, label, layout[1])
        for text, column in zip(shown[at:at + wide], values):
            line = _place(line, str(to_places(text, column)), column)
        out.append(line.rstrip())
    return out


def _row(handler, tok, device, layout, kind):
    """One device's line -- or lines -- of the table, or None if it has
    nothing programmed."""
    from . import screens
    letter = device_letter(tok)
    label = (handler.c.text(_LABEL_CODE.get(letter) or "", device)
             if letter in SETUP_NAME else "")
    if not label:
        # A device nobody has LABELLED has a blank label column on the wire.
        # A real TLS-350 with four unprogrammed tanks prints
        # " 1                                    0" for I60400 -- the tank
        # number, spaces, the value. This console filled the gap with
        # "TANK 1", which is what a PANEL screen draws (screens.device_label
        # still does, and should) but not what a report carries. A device
        # that HAS been labelled still prints its label, which is why this
        # reads the store rather than dropping the column.
        #
        # **A receiver was the exception and is not.** L14 gave the eight
        # autodial addresses `RECEIVER 1` to `RECEIVER 8` where every other
        # unlabelled device goes blank, on the reading that a receiver is one
        # of the console's own addresses rather than something somebody
        # wired up. The reading was right and the LABEL was not: a real
        # console answers `I52000` with `   1                        ON DATE`
        # -- eight rows, every label column empty. See FIDELITY S17.
        if letter in SETUP_NAME:
            label = SETUP_NAME[letter].format(n=device)
        else:
            code = screens.DEVICE_LABEL_CODE.get(letter, "602")
            label = handler.c.text(code, device) or ""
    if _is_config(tok):
        # A CONFIG code stores one flag character per position, so its value
        # is a property of the DEVICE and not of the code: `display_value`
        # decoded the whole slot string and the report printed a bare `1`
        # where every one of these samples prints `ON`. p.317's I701, and
        # 706, 711, 721, 741, 746, 74B, 801 and 806 alike.
        shown = "ON" if handler.c.position_on(tok, device) else "OFF"
        return [row(tok, [device, label, shown])]
    shown = handler.display_value(tok, f"{device:02d}")
    if shown in (None, "") and _is_receiver(tok) and len(layout) >= 2:
        # A receiver ADDRESS exists whether or not anybody has filled in its
        # telephone number, so the row is drawn with the value column empty --
        # which is what 52B already does for a receiver with no dial time.
        # Otherwise 523 and 52E printed nothing at all. See FIDELITY L14.
        #
        # `>= 2` rather than `== 3`, because `522`'s own value IS the label
        # and its table has two columns. A real console prints eight rows of
        # `     1` for it on a console where nobody has named a receiver, and
        # this printed nothing. See FIDELITY S17.
        shown = ""
    elif shown in (None, ""):
        if kind == "number" and len(layout) >= 3:
            return _part_rows(handler, tok, device, layout, label)
        return None
    if kind == "tag":
        others = TAG_COLUMN_CODES.get(tok)
        if others:
            # Each value column its own code. `display_value` is asked for
            # every one of them rather than the row being built from the one
            # that was inquired for. See `TAG_COLUMN_CODES`.
            values = [handler.display_value(one, f"{device:02d}")
                      for one in others]
            if all(one in (None, "") for one in values):
                return None
            cells = [f"{letter} {device}:{label}"] + [
                "" if one is None else one for one in values]
        else:
            cells = [f"{letter} {device}:{label}", shown]
    elif tok == "60A" and len(layout) == 4:
        # 60A's third column is not a part field, it is the tank's PROFILE --
        # the one thing this report has that 604 does not, and the reason
        # 576013-818 Rev AB p.12-31 says "The only way to determine that the
        # profile is set to linear is to run the 60A command." Without this
        # the row could not be built at all and the whole block fell back to
        # answering the packed body.
        cells = [device, label,
                 handler.c.PROFILE_REPORT_NAME[handler.c.tank_profile(device)],
                 shown]
    elif len(layout) == 3:
        cells = [device, label, shown]
    elif len(layout) == 2:
        cells = [device, shown]
    else:
        return _part_rows(handler, tok, device, layout, label)
    return [row(tok, cells)]


def _devices(handler, tok, dev):
    """Which devices the answer covers: the one asked for, or all programmed.

    `aggregate` walks positions 1 to 16 and keeps the ones that hold a value,
    and the display side covers the same ground as the computer side by
    walking it the same way.
    """
    if dev != "00" and dev.isdigit() and int(dev):
        return [int(dev)]
    stored = [n for n in range(1, 17)
              if any(handler.c.values.get(f"S{key}{n:02d}") is not None
                     for key in _value_keys(tok))]
    if _is_receiver(tok):
        # An autodial receiver is not a device somebody wired up, it is one of
        # the console's own eight addresses -- "Press CHANGE twice to
        # configure one receiver... for up to seven more", 576013-623 Rev AN.
        # 52B and 52D have always walked all eight through `_receivers`, and
        # their nine siblings walked "positions holding a value" instead and
        # so printed NOTHING at all on a console nobody had autodialled from
        # -- not the title the page draws, not the heading under it. See
        # FIDELITY L14.
        return list(handler.c.receivers())
    # A device that is CONNECTED is on the report whether or not anybody has
    # changed this setting on it, because the console is not blank there: it
    # reads the documented default. FIDELITY S9 taught `display_value` that
    # and this was not told -- it walked stored values only, so 72 codes
    # carrying a default had a value to print and no row to print it in.
    # `_row` still drops a device with nothing to say, so a code with no
    # default stays silent exactly as before.
    return sorted(set(stored) | set(_connected(handler, tok)))


# Table 29-1's letter for a family, and the config screen that switches its
# positions on. `Console.CONFIG_LABEL` holds the same config codes keyed the
# other way.
FAMILY_OF = {"T": ("probe", "601"), "L": ("liquid", "701"),
             "V": ("vapor", "706"), "G": ("gw", "711"),
             "C": ("2wire", "741"), "H": ("3wire", "746"),
             "s": ("smart", "721"), "P": ("vlld", "751"),
             "Q": ("plld", "781"), "W": ("wplld", "7A1"),
             "R": ("relay", "806"), "I": ("io", "801"),
             "r": ("pumpmon", "7C4"), "U": ("universal", "74B")}


#: the profile tables, which are not a setting every tank HAS. A tank on one
#: point has no four point chart, so it is not a row of zeros on `I605` -- it
#: is absent, the way the manual's own sample shows only the tank it profiles.
#: `Console.PROFILE_CODE`'s values plus 63B's pairs.
PROFILE_TABLES = frozenset({"605", "606", "63B", "63C"})


def _connected(handler, tok):
    """The devices of this code's family that are wired up, or []."""
    if tok in PROFILE_TABLES:
        return []
    family = FAMILY_OF.get(device_letter(tok))
    if not family:
        return []
    module, config = family
    console = handler.c
    if not console.has(module):
        return []
    # A FITTED module gives a setup report its positions whether or not each
    # one is switched on. A real TLS-350 with all four tank positions OFF
    # still prints four rows for I60100, I60400, I60A00, I60C00, I61200,
    # I61500 and I61A00 -- I60100's own rows read "OFF". Walking only the
    # configured devices left every one of those reports empty on a console
    # nobody had programmed. `_row` still drops a device with nothing to
    # say, so a code carrying no default stays silent as before.
    configured = console.configured_devices(config, console.capacity(module))
    return configured or list(range(1, console.capacity(module) + 1))


def _is_config(tok):
    """Whether this code is a family's CONFIG screen.

    `Console.CONFIG_LABEL` is the list, keyed the same way: each sensor and
    I/O family's config function against the label function beside it.
    """
    from .console import Console
    return tok in Console.CONFIG_LABEL


def _is_receiver(tok):
    """Whether this code numbers autodial receivers rather than devices.

    576013-635 p.10's own code-space band: 520 to 52F are the receiver
    configuration functions, and every one of their samples heads its table
    `RCVR` or numbers its rows `DEVICE`.
    """
    try:
        return 0x520 <= int(tok, 16) <= 0x52F
    except ValueError:
        return False


# A code whose Display response IS another code's report, over that code's
# store. One entry, and a real console is the only reason it is here.
#
# 576013-635 files 504 as "Set System RS-232 Security Code", Version 1, one
# code for the console, and draws its response as `SYSTEM SECURITY CODE` over
# a single `CODE : 000000`. `tests/console_capture/raw/I50400.bin` -- a
# running TLS-350 -- answers `232 SECURITY CODE` over
# `PORT  SECURITY CODE   STATUS` and six port rows, which is the report the
# same manual draws for 536, "Set RS-232 Security Code per Port", Version 20.
# A console at this software has both codes and answers the newer report for
# the older code. See FIDELITY S14.
# 121 is the other one, and it is in no manual at all: a real console answers
# it with 113's ACTIVE ALARMS REPORT, title and heading character for
# character, while refusing 120 and 122 either side of it. It has no
# `wiretitles.json` entry of its own -- there is no page to read one off --
# so everything keyed on the code, the blank line under the title included,
# has to resolve through here.
SHOWN_AS = {"504": "536", "121": "113"}

# A per-device table whose value columns are not all the asked code's own.
# 77F is the one: "Set Pressure Line Leak Secondary Pipe Length", and its
# report heads two columns `1.5 IN DIAM LEN` and `2.5 IN DIAM LEN` -- the
# line's PRIMARY length, which is 789's value, beside its own. A code's own
# payload is one length, so nothing could fill the row from it and the whole
# report came back as a bare stamp. The same relation `Handler.GROUP_ROWS`
# describes for 505, one dimension along.
#
# The WPLLD side does NOT do this: 7AD gives the larger diameter a report of
# its own, `WPLLD LINE LEAK LINE LENGTH   LARGE`, rather than a second column
# on 7A9's. Two families, one setting, two shapes. See FIDELITY S15 and S18.
TAG_COLUMN_CODES = {"77F": ("789", "77F")}

# How many RS-232 ports the security report lists. "PP - Port number
# (Decimal, 01..03 [..06]; 99=this port)" is the range 536 takes, and the
# captured console prints all six with nothing programmed on any of them.
# `COMM_PORTS` is the same six positions from the other end -- four slots,
# two of them dual.
SECURITY_PORTS = 6


def _value_keys(tok):
    """The function codes that hold this code's value.

    One, for every code but two. 604 is Set Tank 1 Point Full Height Volume
    and 60A is Set Tank Linear Calculated Full Volume, their payloads are
    byte for byte the same, and a console holds ONE full volume per tank
    which both of them report. 576013-818 Rev AB p.12-32 is a real console
    doing it: `I60A00` calls tanks 1, 2 and 3 LINEAR, and `I60400` on the
    next page answers the same 10000, 6000 and 8000 for the same three
    tanks, under the TSG's own line -- "The 1 Point Full Volume command 604
    gives no indication that the profile is linear!"

    This console had them as separate stores, so a tank programmed through
    one was missing from the other's report entirely.
    """
    from .console import Console
    other = Console.SHARED_STORE.get(tok)
    return (tok, other) if other else (tok,)


def display_answer(handler, tok, dev, code):
    """The manual's Display response for this setup code, or None.

    None means the manual draws no shape this can fill, and the caller
    answers the way it did before.
    """
    # 504 has no device of its own and its report covers every port. 536
    # DOES have one, and port zero is not a port: "PP - Port number (Decimal,
    # 01..03 [..06]; 99=this port)". A real console answers `I53600` with a
    # bare stamp and `I50400` with all six rows, which is exactly that rule
    # seen from both ends.
    alias = SHOWN_AS.get(tok)
    devices = (list(range(1, SECURITY_PORTS + 1)) if alias
               else _devices(handler, tok, dev))
    tok = alias or tok
    spec = TITLES.get(tok)
    if not spec:
        return None
    layout = spec.get("columns")
    if layout:
        kind = spec.get("kind")
        if not ((kind == "number" and len(layout) >= 2)
                or (kind == "tag"
                    and (len(layout) == 2
                         or len(TAG_COLUMN_CODES.get(tok) or ())
                         == len(layout) - 1))):
            return None
        rows = []
        for n in devices:
            got = _row(handler, tok, n, layout, kind)
            if got:
                rows += got
        if not rows:
            return None
        # 201, 205, 21A, 2E2, 353, 615, 616 and 761 print no title at all:
        # their block opens with the heading. Drawing the recorded title
        # would print the heading twice.
        body = [] if spec.get("untitled") else (
            [spec["title"]] + [""] * spec.get("gap", 1))
        body += [spec["heading"]] + [""] * spec.get("rowgap", 0) + rows
        return handler._frame(code, SEP.join(body)), "the manual's own columns"
    line = spec.get("line")
    if not line or ":" in spec["title"]:
        # A block whose first line already carries a colon is a device
        # header and not a title -- 889's is `COMM BOARD  : 1 (S-SAT )` --
        # and those codes have their own handling.
        return None
    shown = handler.display_value(tok, dev)
    if shown in (None, ""):
        return None
    body = [spec["title"]] + [""] * spec.get("gap", 0)
    body.append(_place(_line_label(handler, tok, dev, line["label"]),
                       str(to_places(shown, line)), line).rstrip())
    return handler._frame(code, SEP.join(body)), "the manual's own columns"


# A titled line whose label opens with a DEVICE -- `T 1:` and the tank's
# product name -- rather than with a constant.
_DEVICE_IN_LABEL = re.compile(r"^([A-Za-z]) ?(\d+):(.*?)(:?)$")


def _line_label(handler, tok, dev, label):
    """The label of a titled line, with this console's device in it.

    Two of the fifty-seven titled lines carry a device: 613 and 614, the two
    CSLD per-tank settings, whose recorded label is `T 1:REGULAR UNLEADED
          :` -- the manual's own sample site, baked in. Every console
    answered `I61300` with that tank and that product name whatever it had
    in the ground, which is the "a sample value is not a field" trap one
    level down from the one `_row` documents. 618, the third CSLD setting,
    is a TABLE and had the rule already.

    The width is the sample's own: the label column runs from the end of
    `T 1:` to the second colon, which is 22 characters on both pages. An
    unlabelled tank leaves it blank, as it does on every other report.
    See FIDELITY S18.
    """
    found = _DEVICE_IN_LABEL.match(label or "")
    if not found:
        return label
    letter, _sample, middle, tail = found.groups()
    device = int(dev) if dev.isdigit() and int(dev) else 1
    from . import screens
    code = (_LABEL_CODE.get(letter)
            or screens.DEVICE_LABEL_CODE.get(letter) or "602")
    text = handler.c.text(code, device) or ""
    return f"{letter} {device}:{text:<{len(middle)}.{len(middle)}s}{tail}"
