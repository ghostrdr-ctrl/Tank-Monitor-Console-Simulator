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
"""Extract the reference PDFs to text, one line per line the PAGE shows.

The obvious extraction -- `page.get_text()` -- returns the text runs in the
order the PDF stores them, and a Veeder-Root screen diagram does not store
them in reading order. A screen whose value is set against the right of the
display, like

    DENSITY         :0.0000

is two runs in two columns, and comes out as two lines. So does half of the
WPLLD fold-out. Anything looking for that screen then cannot find it, and the
audit reports a gap in the simulator where the gap is in the extraction.

This groups words by their y coordinate instead, so a line of the page comes
out as a line of the file. Runs separated by a wide gap keep a gap, roughly
scaled to the page, because some screens are only distinguishable by it.

    python tools/extract_manuals.py

Writes `<name>.txt` beside each `<name>.pdf`, with `<<<PAGE n>>>` markers.
Both are gitignored: they are Veeder-Root's documents.
"""
import glob
import os
import sys

import fitz

REF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "reference")
# two words closer than this are one phrase
GAP = 3.0
# ... and further apart than this is a different thing on the page
COLUMN = 40.0


def _rows(spans):
    """Group spans into the lines a reader sees, by vertical overlap.

    Not by a rounded coordinate: a screen's value is often set a fraction of
    a point off its label's baseline -- "TANK PROFILE" at y=154.47 and
    ": 50 PTS" at y=155.13 on p.100 of the setup manual -- and any fixed
    rounding will sooner or later put a pair like that either side of a
    boundary.
    """
    rows = []
    for y0, y1, x0, x1, text in sorted(spans):
        mid = (y0 + y1) / 2.0
        for row in rows:
            top, bottom = row["y"]
            if top - 1.0 <= mid <= bottom + 1.0:
                row["at"].append((x0, x1, text))
                row["y"] = (min(top, y0), max(bottom, y1))
                break
        else:
            rows.append({"y": (y0, y1), "at": [(x0, x1, text)]})
    return rows


def _joined(run):
    line, last = [], None
    for x0, x1, text in run:
        if last is not None:
            gap = x0 - last
            line.append(" " * max(1, min(20, int(gap / 4.5)))
                        if gap > GAP else "")
        line.append(text)
        last = x1
    return "".join(line).rstrip()


def page_lines(page):
    """-> [str], the page's text as a reader sees it, line by line.

    Two things have to be true at once. A screen whose value is set against
    the right of the display -- `DENSITY         :0.0000` -- is two text runs
    in two columns and has to come back as ONE line. And the sentence printed
    beside that screen box is a different thing on the page and has to stay
    off it.

    The PDF's own blocks already draw that distinction: the box is one block
    and the annotation is another. So this walks blocks, and regroups the
    words inside each block by their y coordinate.
    """
    out = []
    everything = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        spans = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                x0, y0, x1, y1 = span["bbox"]
                spans.append((y0, y1, x0, x1, span["text"]))
                everything.append((y0, y1, x0, x1, span["text"]))
        # Group by what OVERLAPS vertically rather than by a rounded
        # coordinate. A screen's value is often set a fraction of a point off
        # its label's baseline -- "TANK PROFILE" at y=154.47 and ": 50 PTS"
        # at y=155.13 on p.100 -- and any fixed rounding will sooner or later
        # put a pair like that either side of a boundary.
        for row in _rows(spans):
            run = sorted(row["at"])
            line, last = [], None
            for x0, x1, text in run:
                if last is not None:
                    gap = x0 - last
                    line.append(" " * max(1, min(20, int(gap / 4.5)))
                                if gap > GAP else "")
                line.append(text)
                last = x1
            joined = "".join(line).rstrip()
            if joined.strip():
                out.append(joined)
            if len(run) > 1:
                # and each run on its own. Joining is right for a screen split
                # across columns and wrong for two screens printed side by
                # side, and which one a line is cannot be told from its
                # geometry -- so the file carries both and the search finds
                # whichever it needs.
                for _x0, _x1, text in run:
                    if text.strip() and text.strip() != joined.strip():
                        out.append(text.strip())
    # and once more across the whole page, ignoring the blocks: a screen box
    # and the value set against its right edge are sometimes two blocks, and
    # the box is still one screen.
    seen = set(out)
    for row in _rows(everything):
        joined = _joined(sorted(row["at"]))
        if joined.strip() and joined not in seen:
            out.append(joined)
            seen.add(joined)
    return out


def main():
    n = 0
    for pdf in sorted(glob.glob(os.path.join(REF, "*.pdf"))):
        out = pdf[:-4] + ".txt"
        if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(pdf) \
                and os.path.getmtime(out) > os.path.getmtime(__file__):
            print(f"cached  {os.path.basename(pdf)}")
            continue
        try:
            doc = fitz.open(pdf)
        except Exception as exc:                       # pragma: no cover
            print(f"FAIL    {os.path.basename(pdf)}: {exc}")
            continue
        parts = []
        for i, page in enumerate(doc):
            parts.append(f"\n<<<PAGE {i + 1}>>>\n")
            parts.append("\n".join(page_lines(page)))
            parts.append("\n")
        open(out, "w", encoding="utf-8").write("".join(parts))
        print(f"wrote   {os.path.basename(pdf)}: {len(doc)} pages")
        doc.close()
        n += 1
    print(f"{n} extracted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
