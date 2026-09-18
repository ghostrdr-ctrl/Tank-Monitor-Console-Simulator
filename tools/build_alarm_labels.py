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
"""Read the alarm labels the console SHOWS off 576013-623 Rev AN Table 5-1.

The table's columns are headed "Alarm Category | Display/print Alarm Label |
Custom Alarm Label | ID Code | Type/Num | Autodial Alarm Label", so the
second column is not a description of the alarm -- it is what the console
puts on its display and on its paper, and the fifth is the same alarm
abbreviated for an autodial receiver.

`consoledata.json` carried the serial manual's i101 LEGEND instead, which is
a different thing: "08=Tank Invalid Fuel Level Alarm" explains what a code
means to somebody decoding a byte. The proof that the legend is not the label
is in the serial manual itself -- its own I206 sample prints

    INVALID FUEL LEVEL       DEC 20, 1995 11:59 AM

which is Table 5-1's spelling, not the legend's. The widths say the same
thing: every one of Table 5-1's labels fits the 24 column display and 41 of
the console's did not, up to 33 characters.

Why this table can be read from the text extraction and others cannot.
`../UNKNOWNS.md` section D records five wrong readings caused by two-column
tables whose columns drift apart in every extraction tried. This one does
not have that shape: each row carries its label and its Type/Num on ONE
line, so the pairing is inside the line rather than across the page. It is
still read TWICE, by two independent parses -- once splitting each line on
runs of spaces, once from the doubled form the extractor also emits, where
the label and the code land on lines of their own -- and the tool refuses to
write anything unless both agree on every row.

Writes `tls350sim/alarmlabels.json`. Run it when a manual revision changes:

    python tools/build_alarm_labels.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANUAL = os.path.join(ROOT, "reference", "576013-623_AN_SystemSetup.txt")
OUT = os.path.join(ROOT, "tls350sim", "alarmlabels.json")

# A label is capitals, digits and a little punctuation: "PC (H8) REVISION
# WARN", "TC VOLUME TIME-OUT". Anything with a lower case letter in it is a
# category name or a sentence from the page around the table.
LABEL = re.compile(r"[A-Z0-9][A-Z0-9 ()/.,+#'\-]{2,}")
NUM = re.compile(r"\d\d/\d\d")

# ---------------------------------------------------------------------------
# Where Table 5-1 is wrong about itself, and the second reading that says so.
#
# Table 6-2 is this table's own second printing. 576013-623 Rev AN
# p.5-20 says the five functions that assign alarms -- "Autodial, Output
# Relay, WPLLD, PLLD, and VLLD line disable setups" -- use "the Autodial
# Alarm Label column of Table 5-1", and Table 6-2 on pp.6-21 to 6-26 is that
# column re-listed group by group with the N/A rows dropped. Two rows
# disagree between the two printings, and in both cases the shorter list is
# right and can be checked against `status_types`.
#
# Nothing else here is corrected. A table that needs correcting in more than
# a couple of places is a table that was read wrong.

# A Type/Num the page prints TWICE, against two different labels. Keyed by
# the wrong code and the label that proves which row it is.
RENUMBERED = {
    # p.5-26 prints `28/01` for SETUP DATA WARNING and again for
    # COMMUNICATION ALARM, so both parses agreed on the duplicate and the
    # second row fell out of the table altogether -- the console has no
    # display label and no autodial label for a Smart Sensor communication
    # alarm. Table 6-2's Smart Sensor list runs SETUP WARN, COMM ALARM,
    # SENSR FAULT, and `status_types["28"]["02"]` is the Communication
    # Alarm, so the second row is 28/02.
    ("28/01", "COMMUNICATION ALARM"): "28/02",
}

# A Type/Num whose autodial word is a typo. Keyed by code, valued (what the
# page prints, what it should be).
DIAL_FIXES = {
    # p.5-24 gives 06/22 as `ANNLSELF`, which is 06/20's own word with the
    # space taken out. 06/22 is ANN-PUMP SELF FAIL and 06/20 is ANN-LINE
    # SELF FAIL, and Table 6-2's VLLD list has `ANN PSELF` in that position
    # between ANN PUMP and PRESS WARN.
    "06/22": ("ANNLSELF", "ANN PSELF"),
}


# The DIM block's two rows, each printed once for categories 18 and 19:
# the display label, the type, and the autodial label, as p.5-24 prints them.
SIDE_ROWS = (
    ("DISABLED DIM ALARM", "02", "DISABLED"),
    ("COMMUNICATION ALARM", "03", "COMM ERROR"),
)


def _table(lines):
    """Everything from the table's caption to the end of the document."""
    for i, line in enumerate(lines):
        if line.strip() == "Table 5-1. Alarm Labels":
            return lines[i:]
    raise SystemExit("Table 5-1 is not in %s" % MANUAL)


def by_fields(lines):
    """Split each line on runs of spaces and take the field before the code.

    `Tank Alarm    SETUP DATA WARNING    02/01    N/A` gives the category,
    the label, the Type/Num and the autodial label as four fields, and the
    label is always the one immediately before the number.
    """
    labels, dial = {}, {}
    for line in lines:
        if line.startswith("<<<PAGE"):
            continue
        parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
        for i, part in enumerate(parts):
            if not (i and NUM.fullmatch(part)):
                continue
            label = parts[i - 1]
            if LABEL.fullmatch(label):
                part = RENUMBERED.get((part, label), part)
                labels.setdefault(part, label)
                if i + 1 < len(parts):
                    dial.setdefault(part, parts[i + 1])
            break
    return labels, dial


def by_lines(lines):
    """The same rows out of the doubled form, where each cell is its own line.

    The extractor emits every row twice, once joined and once exploded. The
    exploded copy is an independent witness: it never went through the
    space-splitting above, so a column that had drifted would disagree here.
    """
    labels = {}
    for one, two in zip(lines, lines[1:]):
        head, code = one.strip(), two.strip()
        if NUM.fullmatch(code) and LABEL.fullmatch(head):
            labels.setdefault(RENUMBERED.get((code, head), code), head)
    return labels


def main():
    with open(MANUAL, encoding="utf-8", errors="replace") as fh:
        lines = _table(fh.read().splitlines())

    fields, dial = by_fields(lines)
    doubled = by_lines(lines)

    # The refusal. Two parses that agree are evidence; one parse is a guess.
    if set(fields) != set(doubled):
        only = sorted(set(fields) ^ set(doubled))
        raise SystemExit("the two parses disagree about which rows exist: %s"
                         % only)
    clash = sorted(k for k in fields if fields[k] != doubled[k])
    if clash:
        raise SystemExit("the two parses disagree about %d labels: %s"
                         % (len(clash), [(k, fields[k], doubled[k])
                                         for k in clash]))

    # Two rows the page prints ONCE for two categories. p.5-24's DIM block
    # is headed "Power Side DIM (MDIM) (18) or Communication Side DIM
    # (EDIM/BDIM) (19)", and its Type/Num cells read "(for type, see alarm
    # category)/02" and "ditto/03": the category is which side the DIM sits
    # on and the type is which alarm it is. Neither parse can read a number
    # out of prose, so the four rows are written from the two the page
    # prints -- and only while the page still prints them that way.
    text = "\n".join(lines)
    if "category)/" not in text or "ditto/03" not in text:
        raise SystemExit("Table 5-1's DIM block no longer reads '(for type, "
                         "see alarm category)/02' and 'ditto/03' -- re-read "
                         "the page before writing SIDE_ROWS")
    for label, num, word in SIDE_ROWS:
        for category in ("18", "19"):
            fields.setdefault(f"{category}/{num}", label)
            dial.setdefault(f"{category}/{num}", word)

    for code, (printed, right) in DIAL_FIXES.items():
        # A correction that no longer corrects anything is a correction
        # that has rotted: a manual revision fixing its own typo has to
        # take the entry out rather than leave it silently doing nothing.
        if dial.get(code) != printed:
            raise SystemExit("DIAL_FIXES expects %s to read %r and it reads "
                             "%r -- take the entry out or re-read the page"
                             % (code, printed, dial.get(code)))
        dial[code] = right

    over = sorted(k for k, v in fields.items() if len(v) > 24)
    if over:
        # A label that does not fit the display is a label read out of the
        # wrong column, because the whole argument for this table is that its
        # labels fit and the legend's do not.
        raise SystemExit("labels wider than the 24 column display: %s" % over)

    out = {"_source": "576013-623 Rev AN Table 5-1, Alarm Labels",
           "label": dict(sorted(fields.items())),
           "autodial": dict(sorted(dial.items()))}
    # `newline="\n"`, like the other two generators. Without it Python
    # translates every line ending to the platform's, so re-running these on
    # Windows rewrote both files end to end and left a diff nobody had
    # asked for -- which is the same hazard `console_corrections.json`
    # exists for, one level down: a generated file nobody can re-generate
    # cleanly is a generated file people stop re-generating.
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print("%d display labels and %d autodial labels -> %s"
          % (len(fields), len(dial), os.path.relpath(OUT, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
