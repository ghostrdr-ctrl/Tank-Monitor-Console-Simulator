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
"""Read a manual page as it is LAID OUT, not as it extracts.

`../UNKNOWNS.md` section D records the trap this exists for: a two-column
table or a flow-chart figure comes out of a plain text extraction with its
columns interleaved, and this project has made five wrong readings that
way -- one of which reached the console as a regression rather than as a
wrong note (FIDELITY D4).

PyMuPDF's word boxes carry their own coordinates, so grouping words by their
y position rebuilds the visual lines exactly, and keeping the x positions
lets a column be cut out of a page by where it sits rather than by counting
spaces. That is what "settle it against a rendered page" means for a table:
the geometry, not the reading order.

Run it against a page to see what is actually there:

    python tools/pagelines.py 576013-818_RevAA_TLS3XX_TSG 118
    python tools/pagelines.py 576013-818_RevAA_TLS3XX_TSG 118 --x 300 520

`--x` keeps only words whose left edge falls in that band, which is how one
column of a two-column figure is read without the other bleeding into it.

`--columns` redraws the page in its own CHARACTER grid instead of joining
words with single spaces:

    python tools/pagelines.py 576013-635_RevAA_SerialInterfaceManual 549 --columns

                                  PUMP   PUMP RELAY    STUCK     RUN
    DEVICE  LABEL                 (OUT)     (IN)       RELAY     TIME
         1  PUMP RELAY UNLEADED    OFF    Q 1: OFF     0 SEC    00:00

That is the thing a heading argument turns on, and it cannot be counted off
the joined form: `pdftotext -layout` renders this same block with the data
row merged into the second heading line, so read from the text it looks like
a three line header with `0 SEC` and `00:00` in it. The pitch is any word's
width over its own length -- these samples are Courier at almost exactly six
points a character -- and the origin is the leftmost word on the page, or the
`<SOH>` when the block has one, because a page footer set three characters
further left will otherwise indent every line of the block by three.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REFERENCE = os.path.join(ROOT, "reference")

# Words within this many points of each other vertically are on one line.
# The manuals set their tables at about 8pt, and the figures' screen boxes
# sit a little over a line apart.
TOLERANCE = 3.0


def manual_path(name):
    """The PDF for a manual, named the way `reference/` names it."""
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    path = os.path.join(REFERENCE, name + ".pdf")
    if not os.path.exists(path):
        raise SystemExit("no such manual: %s" % path)
    return path


def page_lines(path, number, x_from=None, x_to=None, tolerance=TOLERANCE):
    """[(y, [(x, word), ...])] for one page, top to bottom, left to right.

    `number` is the PDF's own page number counting from 1, which is what the
    `<<<PAGE n>>>` markers in the text extractions count as well, so a line
    found in the text can be looked up here directly.
    """
    import fitz
    with fitz.open(path) as doc:
        if not 1 <= number <= doc.page_count:
            raise SystemExit("page %d is outside 1..%d"
                             % (number, doc.page_count))
        words = doc[number - 1].get_text("words")
    rows = []
    for x0, y0, _x1, _y1, word, *_rest in words:
        if x_from is not None and x0 < x_from:
            continue
        if x_to is not None and x0 > x_to:
            continue
        for row in rows:
            if abs(row[0] - y0) <= tolerance:
                row[1].append((x0, word))
                break
        else:
            rows.append([y0, [(x0, word)]])
    rows.sort(key=lambda r: r[0])
    return [(y, sorted(cells)) for y, cells in rows]


def character_grid(path, number, x_from=None, x_to=None):
    """[(y, line)] with every word at the character column it is printed in.

    The pitch is the commonest width-over-length across the page's words,
    which is robust against one word the extraction has mismeasured.
    """
    import fitz
    with fitz.open(path) as doc:
        words = doc[number - 1].get_text("words")
    widths = {}
    for x0, _y0, x1, _y1, word, *_rest in words:
        if word:
            key = round((x1 - x0) / len(word), 3)
            widths[key] = widths.get(key, 0) + 1
    pitch = max(widths.items(), key=lambda kv: kv[1])[0] if widths else 6.0
    rows = page_lines(path, number, x_from, x_to)
    origin = next((cells[0][0] for _y, cells in rows
                   if len(cells) == 1 and cells[0][1] == "<SOH>"),
                  min((cells[0][0] for _y, cells in rows if cells),
                      default=0.0))
    out = []
    for y, cells in rows:
        line = ""
        for x, word in cells:
            line = line.ljust(max(0, round((x - origin) / pitch))) + word
        out.append((y, line))
    return out


def as_text(path, number, x_from=None, x_to=None, gap=None):
    """The page's visual lines as strings.

    `gap` inserts a marker between words further apart than that many
    points, so a table's column boundaries stay visible instead of
    collapsing into single spaces.
    """
    out = []
    for _y, cells in page_lines(path, number, x_from, x_to):
        parts, last = [], None
        for x, word in cells:
            if last is not None and gap and x - last > gap:
                parts.append("|")
            parts.append(word)
            last = x + len(word) * 4.0
        out.append(" ".join(parts))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("manual", help="a name from reference/, without .pdf")
    ap.add_argument("page", type=int, help="PDF page number, counting from 1")
    ap.add_argument("--x", nargs=2, type=float, metavar=("FROM", "TO"),
                    help="keep only words starting inside this x band")
    ap.add_argument("--gap", type=float, default=None,
                    help="mark word gaps wider than this with |")
    ap.add_argument("--y", nargs=2, type=float, metavar=("FROM", "TO"),
                    help="keep only lines inside this y band")
    ap.add_argument("--columns", action="store_true",
                    help="redraw the page in its own character grid")
    args = ap.parse_args()

    path = manual_path(args.manual)
    band = args.x or (None, None)
    if args.columns:
        for y, line in character_grid(path, args.page, band[0], band[1]):
            if args.y and not (args.y[0] <= y <= args.y[1]):
                continue
            print(f"{y:7.1f}  |{line}|")
        return 0
    for y, cells in page_lines(path, args.page, band[0], band[1]):
        if args.y and not (args.y[0] <= y <= args.y[1]):
            continue
        parts, last = [], None
        for x, word in cells:
            if last is not None and args.gap and x - last > args.gap:
                parts.append("|")
            parts.append(word)
            last = x + len(word) * 4.0
        print(f"{y:7.1f}  " + " ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
