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
"""Which lamp a condition lights, read off the manual's own column.

576013-610 Rev AC chapter 29 lists every message the console can display in
a run of tables -- 29-2 for the system, 29-3 for in-tank leak detection,
then one per sensor and line-leak family -- and every one of them is headed

    Display Message | Front Panel Indicator | Cause | Action

The second column is the lamp, one row at a time: `PAPER OUT -- Warning`,
`BATTERY IS OFF -- Alarm`. It cannot be worked out from the message.
`HIGH PRODUCT ALARM` is a Warning and `LIQUID WARNING` is, in one family, an
Alarm; the console's own descriptions are not even written in one case.
Reading the lamp off a substring of the description was this project's, and
it put eighteen conditions on the wrong lamp -- see CLOSED U23 and A1 in
`audits/2026-09-10-alarm-lifecycle.md`.

Two independent parses, and the tool refuses to write unless they
agree, which is the same bar `build_alarm_labels.py` is held to and for
the same reason: `../UNKNOWNS.md` section D records five wrong readings
caused by two-column tables whose columns drift apart in an extraction.

* The DOUBLED form the extractor emits, where the message and the indicator
  each land on a line of their own. This one carries the whole message, so
  it decides the message TEXT.
* The MERGED form, where the row survives intact on one line and the
  columns are still side by side. Line-wrapped messages arrive truncated
  here, so this one decides nothing by itself -- it is the corroboration,
  matched by prefix.

Every row this writes is present in both, with the same indicator, and a row
in only one of them is dropped.

Writes `tls350sim/alarmindicators.json`. Run it when a manual revision
changes:

    python tools/build_alarm_indicators.py
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANUAL = os.path.join(ROOT, "reference", "576013-610_RevAC_OperatorManual.txt")
OUT = os.path.join(ROOT, "tls350sim", "alarmindicators.json")

# The column headings, which land on lines of their own in the extraction
# and are not messages.
HEADINGS = {"Indicator", "Cause", "Action", "Front Panel", "Display Message"}

# A display message is what the 24 column glass can show: capitals, digits
# and the little punctuation the console uses. Anything with a lower case
# letter in it is a sentence out of the Cause or Action column.
MESSAGE = re.compile(r"^[A-Z0-9][A-Z0-9 ()./+#:%'-]*$")

# The same row with its columns intact: the message, a run of spaces, the
# indicator. The leading spaces are real -- some rows are indented.
INLINE = re.compile(r"^\s{0,3}([A-Z0-9][A-Z0-9 ()./+#:%'-]{2,23}?)\s{2,}"
                    r"(Warning|Alarm)(?:\s|$)")

TABLE = re.compile(r"^Table (29-\d+)\.- ")

# Only for the messages whose lamp DIFFERS between families. The console
# numbers its alarm categories and the manual titles its tables, and these
# five are the only pairings this file has to make -- each one the same
# device under two names, and the naming is the console's own:
# `console.MODULES` calls the 2-wire C.L. module the "Eight-Input Type A
# Sensor Module" and the 3-wire the "Six-Input Type B Sensor Module", which
# is what `status_categories` 08 and 12 are.
#
# A family-dependent message whose table is NOT here stops the build rather
# than being written under a guess.
TABLE_CATEGORY = {
    "29-5": "03",     # Liquid Sensor Status Indicators
    "29-9": "04",     # Vapor Sensor Status Indicators
    "29-15": "08",    # 2-Wire C.L. -> Type A Sensor
    "29-17": "12",    # 3-Wire C.L. -> Type B Sensor
    "29-20": "28",    # Smart Sensor Status Indicators
}

WORD = {"Warning": "warning", "Alarm": "alarm"}


def window(lines):
    """Chapter 29's run of tables, and nothing either side of it."""
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("Table 29-2.- System Status"))
    end = next((i for i, ln in enumerate(lines)
                if i > start and ln.startswith("Table 30")), len(lines))
    return start, end


def parse_doubled(lines):
    """{message: {table: {indicator}}} from the doubled form."""
    start, end = window(lines)
    out = collections.defaultdict(lambda: collections.defaultdict(set))
    table = None
    for i in range(start, end):
        hit = TABLE.match(lines[i])
        if hit:
            table = hit.group(1)
        word = lines[i].strip()
        if word not in WORD:
            continue
        # the message is the nearest line above that looks like one
        for j in range(i - 1, max(start, i - 5), -1):
            cand = lines[j].strip()
            if not cand or cand in HEADINGS or cand.startswith("Table "):
                continue
            if cand.startswith(("Alarm", "Warning")):
                # a Cause column that begins with the indicator word: this
                # row's message is not above it
                break
            if MESSAGE.match(cand) and 3 <= len(cand) <= 24:
                out[cand][table].add(word)
            break
    return out


def parse_merged(lines):
    """The same, from rows whose columns survived on one line."""
    start, end = window(lines)
    out = collections.defaultdict(lambda: collections.defaultdict(set))
    table = None
    for i in range(start, end):
        hit = TABLE.match(lines[i])
        if hit:
            table = hit.group(1)
        row = INLINE.match(lines[i])
        if row:
            out[row.group(1).strip()][table].add(row.group(2))
    return out


def corroborated(name, table, word, merged):
    """Is this row in the merged parse too, with the same indicator?

    Matched by prefix, because a message too long for the column arrives
    there wrapped -- `ANN TST NEEDED` for `ANN TST NEEDED ALM`, and with a
    trailing hyphen where the manual hyphenates.
    """
    for other, tables in merged.items():
        head = other.rstrip("- ")
        if not (name == other or name.startswith(head)):
            continue
        if table in tables and sorted(tables[table]) == [word]:
            return True
    return False


def main():
    with open(MANUAL, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh]
    doubled, merged = parse_doubled(lines), parse_merged(lines)

    plain, by_category, dropped, rows = {}, {}, [], 0
    for name in sorted(doubled):
        words = {}
        for table, found in doubled[name].items():
            if len(found) != 1:
                dropped.append((name, table, "two indicators on one row"))
                continue
            word = found.pop()
            if not corroborated(name, table, word, merged):
                dropped.append((name, table, "only one parse saw it"))
                continue
            words[table] = word
            rows += 1
        if not words:
            continue
        if len(set(words.values())) == 1:
            plain[name] = WORD[next(iter(words.values()))]
            continue
        # the lamp depends on which family raised it
        for table, word in words.items():
            cat = TABLE_CATEGORY.get(table)
            if cat is None:
                raise SystemExit(
                    f"{name} differs by family and Table {table} has no "
                    "category in TABLE_CATEGORY -- add it with its reason")
            by_category.setdefault(cat, {})[name] = WORD[word]

    out = {
        "_source": "576013-610 Rev AC chapter 29, the Front Panel Indicator "
                   "column of Tables 29-2 to 29-23",
        "_note": "Which lamp a condition lights. It cannot be worked out "
                 "from the message: HIGH PRODUCT ALARM is a Warning, and "
                 "LIQUID WARNING is an Alarm on a Type B sensor.",
        "indicator": dict(sorted(plain.items())),
        "by_category": {k: dict(sorted(v.items()))
                        for k, v in sorted(by_category.items())},
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, sort_keys=False)
        fh.write("\n")
    warn = sum(1 for v in plain.values() if v == "warning")
    print(f"{rows} rows corroborated by both parses -> {len(plain)} "
          f"messages ({warn} warning, {len(plain) - warn} alarm) and "
          f"{sum(len(v) for v in by_category.values())} that depend on the "
          f"family")
    for row in dropped:
        print("   dropped:", row)
    print("->", os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
