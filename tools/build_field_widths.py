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
"""Read the decimal field width of every settable code off 576013-635.

The serial manual states a width for every Set, in the Command Format block
beside the ASCII-hex float form:

    S604TTGGGGGG   GGGGGG - Full Height Volume, Gallons (Decimal)
    S607TTIII.hh   III.hh - Tank Diameter, Inches and hundredths (Decimal)
    S753PPLLL      LLL    - 2" Pipe Length, Feet (Decimal)

That is a CEILING on the value, and it is independent of any range a chapter
states and of the mask the panel draws. Where a panel mask exists the two
agree -- `FULL VOL: 000000` and `TANK DIAMETER: 000.00` are 576013-623 Rev AN
p.97 and p.96 -- so a field whose `max` is above this width is a field that
accepts a value neither manual can express. Fifteen did, including six with
no panel mask at all, which the mask invariant cannot see. See FIDELITY F8.

Only the unambiguous ones are kept: a template that is a device prefix and
then ONE run of a repeated placeholder letter, with at most one decimal
point, such as `TTGGGGGG` or `PPLLL` or `TTIII.hh`. A code whose data field
packs several values (`S605TTGGGGGGggggggGGGGGGgggggg`) or whose format
varies with a mode (`S611`) has no single ceiling and is left out rather than
guessed at. 211 codes qualify.

The manuals are not in this repository, so this writes
`tls350sim/fieldwidths.json` and `test_fidelity.py` checks against that.
Run it when a manual revision changes:

    python tools/build_field_widths.py
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANUAL = os.path.join(ROOT, "reference",
                      "576013-635_RevAA_SerialInterfaceManual.txt")
OUT = os.path.join(ROOT, "tls350sim", "fieldwidths.json")

# The device tokens a Set carries before its data. They are not part of the
# value's width.
DEVICE = ("TT", "PP", "QQ", "WW", "SS", "II", "ff", "tt", "ss", "bb", "00")

# One run of one letter, optionally a point and a run of another: `GGGGGG`,
# `III.hh`, `LLL`. Anything else packs more than one number.
SINGLE = re.compile(r"^([A-Za-z])\1*(?:\.([A-Za-z])\2*)?$")

SET = re.compile(r"<SOH>S([0-9A-F]{3})([A-Za-z0-9.,+\-]*)")


def ceiling(template):
    """The largest value a template of that many digits can carry."""
    whole, _, frac = template.partition(".")
    return float("9" * len(whole) + ("." + "9" * len(frac) if frac else ""))


def main():
    with open(MANUAL, encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    seen = collections.defaultdict(set)
    for code, tail in SET.findall(text):
        for prefix in DEVICE:
            if tail.startswith(prefix):
                tail = tail[len(prefix):]
                break
        if SINGLE.match(tail):
            seen[code].add(tail)

    # A code the manual writes two different ways has no single ceiling. That
    # happens where a Set and its response share the `<SOH>S` opening, and
    # keeping both would pick one at random.
    widths = {code: templates.pop()
              for code, templates in seen.items() if len(templates) == 1}

    out = {"_source": "576013-635 Rev AA, the Command Format of each Set",
           "_note": "template, and the largest value it can carry",
           "width": {code: [widths[code], ceiling(widths[code])]
                     for code in sorted(widths)}}
    # `newline="\n"`, like the other two generators. Without it Python
    # translates every line ending to the platform's, so re-running these on
    # Windows rewrote both files end to end and left a diff nobody had
    # asked for -- which is the same hazard `console_corrections.json`
    # exists for, one level down: a generated file nobody can re-generate
    # cleanly is a generated file people stop re-generating.
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print("%d decimal field widths -> %s"
          % (len(widths), os.path.relpath(OUT, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
