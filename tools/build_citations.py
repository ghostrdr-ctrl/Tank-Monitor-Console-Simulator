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
"""Look every screen line up in the manuals and write tests/citations.json.

This is the machine half of the audit. It needs the PDFs, which are not in
the repository (see reference/README.md), so it is a tool rather than a test:
run it when the manuals are on the shelf, commit what it writes, and
`tests/test_citations.py` then enforces the result on every run.

    python tools/build_citations.py

Matching, in the order it is tried:

  exact      the line is a line of the manual
  clip       a manual line, cut to the console's 24 columns
  label      a manual line with the same LABEL -- everything up to the last
             colon -- because the value after it is the site's, not the
             console's
  prefix     the console's line is the start of a longer manual line

Anything that matches none of those is written to the `uncited` list with
nothing invented, so the gap is visible rather than papered over.
"""
import collections
import glob
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

import allscreens                                            # noqa: E402

REF = os.path.join(HERE, "reference")
COLS = 24

# The documents that draw SCREENS. The serial interface manuals describe the
# wire format and print reports; they are not where a display line comes from.
SCREEN_DOCS = (
    "576013-610_RevAC_OperatorManual",
    "576013-623_AN_SystemSetup",
    "576013-818_RevAA_TLS3XX_TSG",
    "576013-939_QuickHelp",
    "577013-344_RevH_PLLD_WPLLD_Troubleshooting",
    "577013-465_RevAD_LineLeakApplicationGuide",
    "577013-750_AK_SensorAppGuide",
    "577013-800_RevP_ISD_ISO",
    "577013-819_RevF_ISD_TSG",
    "577013-874_MaintServiceCodes",
    "577013-937_RevJ_ISD_ISO",
)


BULLET = '•▪●·�'


def norm(s):
    """A manual line, as the console would have drawn it.

    The manuals set their choice lists as bullets, so the line that names a
    screen often arrives with a bullet and a space in front of it. That is
    the typesetting, not the screen.
    """
    for a, b in (("’", "'"), ("‘", "'"), ("“", chr(34)),
                 ("”", chr(34)), ("-", "-"), ("--", "-"),
                 (" ", " "), ("±", "+")):
        s = s.replace(a, b)
    s = re.sub(r"\s+", " ", s).strip()
    while s and (s[0] in BULLET or s[:2] in ("- ", "* ")):
        s = s[1:].lstrip()
    return s.upper()


# The serial interface manuals do not draw screens, so they are no use for
# citing one. They DO enumerate every value a setting can hold, against the
# function code that holds it -- "3=Ultra/Z-1 HV" is the console's own name
# for that choice even where no chapter draws the screen. So an OPTION is
# cited against these as well.
VALUE_DOCS = SCREEN_DOCS + (
    "576013-635_RevAA_SerialInterfaceManual",
    "576013-635_RevY_SerialInterfaceManual",
)


def load_manuals(names=SCREEN_DOCS):
    rows = []
    for name in names:
        path = os.path.join(REF, name + ".txt")
        if not os.path.exists(path):
            print(f"  (missing {name}.txt -- run the extractor first)")
            continue
        page = 0
        for raw in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"<<<PAGE (\d+)>>>", raw.strip())
            if m:
                page = int(m.group(1))
                continue
            n = norm(raw)
            if n:
                rows.append((name, page, n))
    return rows


def label_of(s):
    i = s.rfind(":")
    return s[:i + 1] if i > 0 else None


# A flag screen has two states and the manual pictures one of them. Drawing
# the other is the same screen, so it carries the same citation.
FLAG_STATES = (("ENABLED", "DISABLED"), ("ON", "OFF"), ("YES", "NO"))

# The same screen once per day of the week. 576013-623 Rev AN p.128 draws
# SUN and then MON and says "Repeat the procedures just described until you
# have entered an average daily sales for each day of the week".
WEEKDAYS = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")


def other_states(n):
    """-> the same screen said another way: the other state of a flag, or
    the same screen on another day of the week."""
    out = []
    for day in WEEKDAYS:
        if day in n:
            out += [n.replace(day, other) for other in WEEKDAYS
                    if other != day]
    for a, b in FLAG_STATES:
        for one, two in ((a, b), (b, a)):
            if n.endswith(" " + one):
                out.append(n[:-len(one)] + two)
            if n.endswith(":" + one):
                out.append(n[:-len(one)] + two)
    return out


GLOSS = re.compile(r"\s*\((?:[^()]*\b(?:MM|CM|LPH|GPH|M|FT|IN|KPA|PSI)\b[^()]*)\)")


def deglossed(n):
    """A manual line with its unit gloss removed.

    The manuals write every unit twice -- "0.2 gph (0.76 lph)",
    "2.0 IN. (50 mm) STEEL", "1.5 (38 mm) IN. ENVIRN GFLEXII" -- and a
    console has twenty-four columns and writes it once. So the parenthetical
    comes off before a value is looked for.
    """
    out = GLOSS.sub("", n)
    return re.sub(r"\s+", " ", out).strip()


XRUN = re.compile(r"(?<![A-Z0-9])X{3,}(?![A-Z0-9])")
QUOTED = re.compile(r"DISPLAY(?:S)? READS?:?\s*([^.]{6,40})")
ALT = re.compile(r"(?<![A-Z0-9/])([A-Z0-9.]+)/([A-Z0-9.]+)(?![A-Z0-9/])")


def alternatives(t):
    """-> the screens a manual writes as one with a slash between them.

    "Display reads: Relay X On/Off" is two screens, and so is
    "TC TICKETED DELIVERY (Standard/Temp Compensated)". A slash between two
    words is how these manuals write "either of these", everywhere.
    """
    m = ALT.search(t)
    if not m:
        return []
    return [t[:m.start()] + side + t[m.end():] for side in m.groups()]


def build_index(rows):
    """Index the manuals by SCREEN rather than by line.

    `allscreens.template` puts a manual's `CNTR = X VALUE = XXXXXX` and a
    console's `CNTR = 1 VALUE = 122814` into the same shape, which is the
    shape a citation is about.
    """
    exact, clip, label = {}, {}, {}
    for name, page, n in rows:
        t = allscreens.template(n)
        exact.setdefault(t, (name, page))
        exact.setdefault(allscreens.template(deglossed(n)), (name, page))
        for one in alternatives(t):
            exact.setdefault(one, (name, page))
        # A manual sometimes quotes a screen inside a sentence rather than
        # drawing it: "Display reads: Relay X On/Off. Press any key to
        # printout Relay Setup". What follows the colon, up to the full stop,
        # is the screen, and it is said so explicitly.
        quoted = QUOTED.search(t)
        if quoted:
            said = quoted.group(1).strip()
            if len(said) >= 8:
                exact.setdefault(said, (name, page))
                for one in alternatives(said):
                    exact.setdefault(one, (name, page))
        clip.setdefault(t[:COLS], (name, page))
        # and the manual's line as the CONSOLE would clip it, which is not
        # the same thing: `LAST SALES-SUN: XXXX GALS` is 25 characters, so a
        # four digit reading loses the S and a three digit one does not.
        # Clipping first and templating after catches both.
        clip.setdefault(allscreens.template(n[:COLS]), (name, page))
        for m in re.finditer(":", t):
            label.setdefault(t[:m.end()][:COLS], (name, page))
    return exact, clip, label


def main():
    rows = load_manuals()
    value_rows = load_manuals(VALUE_DOCS)
    printed_rows = [r for r in value_rows if r[0] not in SCREEN_DOCS]
    if not rows:
        print("no manual text found in reference/ -- nothing to do")
        return 1
    exact, clip, label = build_index(rows)
    # A manual line with a placeholder in it -- "(Selected) SHFT ADJ VOL",
    # "T1: (Product Label)" -- stands for whatever the console puts there.
    # It is only usable as evidence if what is NOT a placeholder is enough to
    # identify a screen: a line like "(Insert more deliveries for other
    # tanks)" is all placeholder and would otherwise match anything at all.
    holders = []
    for name, page, n in rows:
        if "(" not in n and not XRUN.search(n):
            continue
        t = XRUN.sub("(X)", allscreens.template(n))
        parts = [x for x in re.split(r"(\([^()]*\))", t) if x]
        literal = "".join(x for x in parts if not x.startswith("("))
        if len(literal.strip()) < 10:
            continue
        holders.append((re.compile("^" + "".join(
            ".+?" if x.startswith("(") else re.escape(x)
            for x in parts) + "$"), (name, page)))

    def by_placeholder(t):
        """-> (doc, page) where a manual placeholder stands for this."""
        if len(t) < 12:
            return None
        for rx, where in holders:
            if rx.match(t):
                return where
        return None

    starts = sorted({allscreens.template(n) for _d, _p, n in rows})

    state = os.path.join(tempfile.gettempdir(), "_citations_state.json")
    screens = allscreens.enumerate_screens(state)
    lines = allscreens.distinct_lines(screens)

    cited, uncited = {}, {}
    how = collections.Counter()
    for n, where in lines.items():
        hit = kind = None
        if n in exact:
            hit, kind = exact[n], "exact"
        elif n[:COLS] in clip:
            hit, kind = clip[n[:COLS]], "clip"
        else:
            lab = label_of(n)
            # the same screen said another way -- the other state of a flag,
            # or another day of the week -- looked for in both indexes,
            # because a longer value clips and a shorter one does not
            swapped = [(o, exact.get(o) or clip.get(o))
                       for o in other_states(n)]
            swapped = [(o, w) for o, w in swapped if w]
            if lab and lab[:COLS] in label:
                hit, kind = label[lab[:COLS]], "label"
            elif swapped:
                hit, kind = swapped[0][1], "state"
            else:
                for m in starts:
                    if m.startswith(n) and len(n) >= 8:
                        for d, p, mm in rows:
                            if allscreens.template(mm) == m:
                                hit, kind = (d, p), "prefix"
                                break
                        break
        if hit is None:
            # The screen's label names a row of a table and its value is one
            # of that row's cells: Table 5-2 lists "Delivery Needed  %Max
            # %Full  Volume  Height" and the console draws
            # "DELIVERY NEEDED: %FULL".
            lab3 = (label_of(n) or "").rstrip(":")
            val3 = n[len(lab3) + 1:].strip() if lab3 else ""
            if len(lab3) >= 8:
                for m, w in exact.items():
                    if m.startswith(lab3) and len(m) > len(lab3) and (
                            not val3 or val3 in m):
                        hit, kind = w, "table"
                        break
        if hit is None:
            # The manual writes a placeholder where the console writes a
            # thing: "(Selected) SHFT ADJ VOL", "(Date) ADJ VOL",
            # "T1: (Product Label)". Any parenthesised group stands for
            # whatever the console puts there.
            hit, kind = by_placeholder(n), None
            if hit:
                kind = "placeholder"
        if hit is None:
            # The manual's line is a SUFFIX of the console's, because what
            # the console puts in front of it is a placeholder the manual
            # draws in italic and the extraction drops: p.28-19's
            # "(Selected) SHFT ADJ VOL: XXXXXX" comes out as
            # "SHFT ADJ VOL: XXXXXX".
            for m, w in exact.items():
                if len(m) >= 10 and n.endswith(m) and n != m:
                    hit, kind = w, "suffix"
                    break
                # ... or the console's line is the tail of the manual's,
                # because the manual introduces it: "Display reads: Relay X
                # On/Off"
                if len(n) >= 8 and m.endswith(n) and m != n:
                    hit, kind = w, "suffix"
                    break
        if hit is None:
            # Last: the serial manuals. They draw no screens, but a function
            # code's printed report names the console's own words for the
            # thing on the screen -- C04 prints OPENING TIME and CLOSING DATE
            # as separate labelled lines, which is where the display's
            # vocabulary for those items comes from. Recorded as "printed"
            # so it is visible that the evidence is a printout and not a
            # picture of the screen.
            # a printout sets its label in a column and a screen follows it
            # with a colon, so the colon comes off before they are compared
            lab2 = (label_of(n) or "").rstrip(":")
            for name, page, m in printed_rows:
                mt = allscreens.template(m)
                if n == mt or (len(lab2) >= 6
                               and mt.startswith(lab2)
                               and mt[len(lab2):len(lab2) + 1] in ("", " ",
                                                                   ":")):
                    hit, kind = (name, page), "printed"
                    break
        if hit:
            how[kind] += 1
            cited[n] = {"manual": hit[0], "page": hit[1], "how": kind,
                        "seen": where[0][4]}
        else:
            uncited[n] = [f"{m}/{f}/{i}/{w}" for m, f, i, w, _l in where[:3]]

    # ---- and every value CHANGE can put on a screen ----------------------
    # A screen citation says the console asks the right question. This says
    # it offers the right answers: every label CHANGE can walk onto, looked
    # for in the manuals the same way.
    from tls350sim.console import FIELDS
    opt_cited, opt_uncited = {}, []
    for key, f in sorted(FIELDS.items()):
        kind = f.get("kind")
        if kind == "enum":
            labels = [c[1] if isinstance(c, (list, tuple)) else c
                      for c in f.get("choices") or []]
        elif kind == "flag":
            labels = list(f.get("words") or ("DISABLED", "ENABLED"))
        elif kind == "setting" and f.get("choices"):
            labels = [c if isinstance(c, str) else c[1] for c in f["choices"]]
        else:
            continue
        for lab in labels:
            t = allscreens.template(str(lab))
            where = None
            for name, page, n in value_rows:
                if t and (t in allscreens.template(n)
                          or t in allscreens.template(deglossed(n))):
                    where = (name, page)
                    break
            if where is None:
                # a value whose name wraps across a line of prose, like
                # "4.0 IN." and "PS" on two lines of 576013-623 p.104
                blob = {}
                for name, page, n in value_rows:
                    blob.setdefault(name, []).append(
                        (page, allscreens.template(deglossed(n))))
                for name, rowset in blob.items():
                    joined = " ".join(x for _p, x in rowset)
                    at = joined.find(t)
                    if at >= 0 and len(t) >= 6:
                        run = 0
                        for page, x in rowset:
                            run += len(x) + 1
                            if run >= at:
                                where = (name, page)
                                break
                        break
            if where:
                opt_cited[f"{key}={lab}"] = {"manual": where[0],
                                             "page": where[1]}
            else:
                opt_uncited.append(f"{key}={lab}")

    out = {
        "options": len(opt_cited) + len(opt_uncited),
        "options_cited": opt_cited,
        "options_uncited": sorted(opt_uncited),
        "_": "Generated by tools/build_citations.py -- every SCREEN the panel "
             "can draw, against the manual page that draws it. Keys are "
             "templates: a run of digits, of the manual's X's, or of its date "
             "and time placeholders is a single #, so a citation belongs to "
             "the screen rather than to one reading of it. Do not edit by "
             "hand: fix the screen or the manual set and regenerate.",
        "screens": len(screens),
        "lines": len(lines),
        "cited": cited,
        "uncited": uncited,
    }
    path = os.path.join(HERE, "tests", "citations.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=1, sort_keys=True)
    print(f"{len(screens)} screens, {len(lines)} distinct lines")
    print(f"  cited   {len(cited):>4}   " + ", ".join(
        f"{k} {v}" for k, v in how.most_common()))
    print(f"  uncited {len(uncited):>4}")
    print(f"{len(opt_cited) + len(opt_uncited)} selectable options: "
          f"{len(opt_cited)} cited, {len(opt_uncited)} not")
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
