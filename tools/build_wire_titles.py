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
"""Read every Display response in the manual off the page, columns and all.

576013-635 answers a Display inquire with a titled table:

    TANK LOW PRODUCT LIMIT

    TANK   PRODUCT LABEL             GALLONS
     1     REGULAR UNLEADED            1000

Neither the title nor the columns can be derived from anything this project
already holds. 621's title is TANK LOW PRODUCT LIMIT where its own Function
Type is "Set Tank Low LEVEL Limit", and the heading names a unit the field
table does not carry. They have to be read off the manual.

They cannot be read off the manual's text extraction either: the response
blocks are laid out in columns and the plain extraction interleaves them,
which is the trap `../UNKNOWNS.md` section D describes and which has cost
this project five wrong readings.

A figure or a sample is a page, and a page has coordinates. PyMuPDF's
word boxes carry theirs, so:

* grouping words by their y position rebuilds the visual lines exactly;
* `(x - origin) / pitch` is the character column, where the pitch is any
  word's width over its own length -- these samples are Courier, at almost
  exactly six points a character, and every column in them lands within a
  twentieth of a character of a whole number;
* the leading between two lines says how many blank lines separate them.

That is what makes the COLUMNS sourceable rather than guessable, and a
guessed column position is an invention on the wire.

Writes `tls350sim/wiretitles.json`, which `wire.py` renders Display responses
from. Run it when a manual revision changes:

    python tools/build_wire_titles.py
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANUAL = os.path.join(ROOT, "reference",
                      "576013-635_RevAA_SerialInterfaceManual.pdf")
OUT = os.path.join(ROOT, "tls350sim", "wiretitles.json")
# What a REAL console printed, where it disagrees with the page. See
# `apply_corrections`.
CORRECTIONS = os.path.join(HERE, "console_corrections.json")

STAMP = re.compile(r"^[A-Z]{3}\s+\d{1,2},\s*\d{4}\s+\d{1,2}:\d{2}(:\d{2})?"
                   r"\s*[AP]M$")
# The response echoes the command it is answering, "I621TT", and that is a
# steadier place to read the code from than the heading: the word boxes put
# "Function Code:" and the code itself on separate lines, because the manual
# sets them in separate columns.
# `OO` is not a device: 54C's own sample echoes `I54COO` with two letter
# O's where the zeros belong, and taking that literally threw the whole of
# its twelve month table away.
#
# The suffix is a RULE, not a list. It used to be the ten placeholders
# somebody had met -- `TT|QQ|SS|LL|PP|BB|NN|nn|00|OO` -- and Table 29-1 has a
# letter for every device family, in whichever case that family's own pages
# are set in. `II` for an input, `RR` for a relay, `WW` for a WPLLD line,
# `rr` for a pump relay monitor and `ss` for a smart sensor were all absent,
# and a suffix the regex cannot read makes the whole PAGE invisible: no
# title, no heading, no columns, no sample and no station-header flag. That
# was 86 Display blocks against the 455 the list could see, `B61` among them
# -- which is why FIDELITY L5 recorded B61 as a code with no sample in this
# manual when its sample is on p.540.
#
# Every placeholder any of these pages uses is either a DOUBLED letter or two
# digits, so that is what this matches, and the next family's letter needs no
# edit here.
# And the letter is not always an I. 40 of the manual's 582 Display
# blocks echo an `S`, and 32 of them are the action codes 001 to 09B, where
# the "Typical Response Message, Display Format" is the answer to the SET --
# `<SOH>S052TT` over `TANK   PRODUCT LABEL` and `LEAK TEST START`. Those
# pages have a title, a heading and a sample like any other, and keying on
# `I` alone threw all forty away: `521`'s own RECEIVER CONFIGURATION table is
# among them, printed under `S521RR` where its Command Format offers
# `I521RR`. See FIDELITY S17.
ECHO = re.compile(r"^[IiSs]([0-9A-Z]{3})"
                  r"(?:([A-Za-z])\2|[0-9]{2})?\s*$")
# A row of a device table opens with the device, and the manuals write that
# two ways: the bare number under a TANK or DEVICE heading, and Table 29-1's
# tagged form, `T 1:REGULAR UNLEADED`, which carries the label with it.
NUMBERED = re.compile(r"^[-+]?[0-9]+(\.[0-9]+)?$")
TAGGED = re.compile(r"^[A-Za-z#]\s?[0-9]+:")
# a value that is only digits sits under its column's right edge; anything
# else runs left from its own start. Where two sample rows disagree in width
# they settle it between them and this is not consulted.
NUMBERISH = re.compile(r"^[-+]?[0-9]*\.?[0-9]+$")
# Words within this many points of each other vertically are on one line.
TOLERANCE = 3.0


# ---------------------------------------------------------------------------
# the page as geometry


def visual_lines(page):
    """[(y, [(x0, x1, word)])] for the page, top to bottom, left to right."""
    out = []
    for x0, y0, x1, _y1, word, *_rest in page.get_text("words"):
        for row in out:
            if abs(row[0] - y0) <= TOLERANCE:
                row[1].append((x0, x1, word))
                break
        else:
            out.append([y0, [(x0, x1, word)]])
    out.sort(key=lambda r: r[0])
    return [(y, sorted(cells)) for y, cells in out]


def metrics(lines):
    """(origin, pitch, leading) for one monospaced block.

    The pitch is the commonest width-over-length across the block's words,
    which is robust against a word the extraction has mismeasured; the
    leading is the smallest gap between two of its lines, which is one line.

    The origin is the `<SOH>` the response opens with, and not the leftmost
    word on the page. A block that runs to the bottom of a page picks up
    the PAGE FOOTER, which is set three characters further left than the
    body, and taking the minimum indented every line of those blocks by three
    -- which reads exactly like a console that indents its reports and is
    nothing of the kind.
    """
    cells = [c for _y, cells in lines for c in cells]
    pitch = collections.Counter(round((b - a) / len(w), 3)
                                for a, b, w in cells if w)
    gaps = [round(lines[n + 1][0] - lines[n][0], 1)
            for n in range(len(lines) - 1)]
    origin = next((row[0][0] for _y, row in lines
                   if len(row) == 1 and row[0][2] == "<SOH>"),
                  min(c[0] for c in cells))
    return (origin, pitch.most_common(1)[0][0], min(gaps) if gaps else 0.0)


def in_columns(lines, origin, pitch):
    """Each line as [(start, end, word)] in character columns, inclusive."""
    return [[(round((a - origin) / pitch), round((b - origin) / pitch) - 1, w)
             for a, b, w in cells] for _y, cells in lines]


def cells_of(words):
    """Words a single space apart are one cell; a wider gap starts another.

    `REGULAR UNLEADED` is one product label and not two columns, and the gap
    is what says so. The manual's own tables never set two columns one space
    apart.
    """
    out = []
    for start, end, word in words:
        if out and start - out[-1][1] <= 2:
            first, last, text = out[-1]
            out[-1] = (first, end, text + " " * (start - last - 1) + word)
        else:
            out.append((start, end, word))
    return out


def render(words):
    """The line as the page draws it."""
    line = ""
    for start, _end, word in words:
        line = line.ljust(start) + word
    return line


# ---------------------------------------------------------------------------
# the block, and what shape it is


def response_block(page):
    """(code, station, leading, [(y, columned words)]) for a Display
    response, or None.

    The block is what falls between "Typical Response Message, Display
    Format" and the Computer Format that follows it, with the framing --
    <SOH>, the echoed code, the stamp, the station header placeholders and
    the closing <ETX> -- taken off, because none of that is the report.
    """
    lines = visual_lines(page)
    flat = [" ".join(w for _a, _b, w in cells) for _y, cells in lines]
    start = next((n for n, l in enumerate(flat)
                  if "Typical Response Message" in l and "Display" in l), None)
    if start is None:
        return None
    end = next((n for n in range(start + 1, len(flat))
                if "Computer Format" in flat[n]), len(flat))
    body = lines[start + 1:end]
    if not body:
        return None
    origin, pitch, leading = metrics(body)
    columned = in_columns(body, origin, pitch)
    code = after = None
    for n, words in enumerate(columned):
        line = " ".join(w for _a, _b, w in words)
        hit = ECHO.match(line)
        if hit and code is None:
            code = hit.group(1)
        if STAMP.match(line):
            # The FIRST stamp is the frame's. A Tank Alarm History prints one
            # against every alarm it lists, and running on to the last of
            # those threw the report away and kept its footer.
            after = n + 1
            break
    if code is None or after is None:
        return None
    # Where the FRAME ends, for the gap measured below: the stamp's own line.
    framing_y = body[after - 1][0]
    rest = [(body[n][0], columned[n]) for n in range(after, len(columned))
            if columned[n]
            and " ".join(w for _a, _b, w in columned[n]) not in ("<ETX>",
                                                                 "<SOH>")]
    # A REPORT carries the station header between the stamp and its title,
    # and the manual's samples print it as the four placeholders a site has
    # not programmed. Step over them: the title is what comes after.
    #
    # Whether they are there at all is worth recording, because it is not
    # a property of the code that can be derived from anything else. This
    # console gave the header to every report, and 61 of the 118 samples in
    # the manual draw no header at all. See FIDELITY L5.
    station = bool(rest) and rest[0][1][0][2].upper() == "STATION"
    while rest and rest[0][1][0][2].upper() == "STATION":
        framing_y = rest[0][0]
        rest = rest[1:]
    # 2E2 and the C0n family answer with a SECOND stamp -- the date the
    # inventory being recalled was recorded, which is the whole point of a
    # stored report -- and it sits where the title would be.
    if rest and STAMP.match(render(rest[0][1]).strip()):
        framing_y = rest[0][0]
        rest = rest[1:]
    if not rest:
        return None
    # How many blank lines the frame is followed by. Counted rather than
    # assumed, and it is not the constant this project took it for: 512 of
    # the 541 samples leave one and 29 leave none, 613, 614, 902, 903 and
    # 905 among them, and a real console agrees with the page on every one of
    # them. The console's reply used a flat one everywhere. See FIDELITY S6.
    head_gap = max(int(round((rest[0][0] - framing_y) / leading)) - 1, 0) \
        if leading else 1
    return (code, station, leading, rest, head_gap)


# Blank lines between the stamp and STATION HEADER 1, where a sample draws
# the header block at all. Unanimous -- 128 samples, every one of them a
# single blank -- so it is a constant here rather than a field per code, and
# the console left it out entirely. See FIDELITY S6.
STATION_LEAD = 1


def device_row(words):
    """Whether this line opens with a device, either way the manuals write it.

    Returns "number", "tag" or None.
    """
    cells = cells_of(words)
    if not cells:
        return None
    first = cells[0][2].strip()
    if NUMBERED.match(first):
        return "number"
    return "tag" if TAGGED.match(first) else None


def columns_of(rows):
    """Column spans, taken from the DATA rows and not from the heading.

    The heading cannot give them: 214 sets TANK and PRODUCT one space apart,
    so grouping its heading words would make one column of two. The rows are
    spaced honestly, and a heading word is then assigned to the column it
    sits over.
    """
    spans = []
    for row in rows:
        for start, end, _text in cells_of(row):
            for n, (a, b) in enumerate(spans):
                if start <= b and end >= a:
                    spans[n] = (min(a, start), max(b, end))
                    break
            else:
                spans.append((start, end))
        spans.sort()
    return spans


def headings_over(words, spans):
    """The heading words, each given to the column it sits over."""
    heads = [""] * len(spans)
    for start, end, word in words:
        best, score = 0, None
        for n, (a, b) in enumerate(spans):
            overlap = min(end, b) - max(start, a) + 1
            # a heading word over nothing goes to the nearest column, which
            # is how PRESSURE reaches RELIEF VALVE's column on 72C
            rank = overlap if overlap > 0 else -abs(start - a) / 1000.0
            if score is None or rank > score:
                best, score = n, rank
        heads[best] = (heads[best] + " " + word).strip()
    return heads


def cells_in(rows, span):
    """Every sample cell that falls in one column."""
    a, b = span
    return [(start, end, text) for row in rows
            for start, end, text in cells_of(row) if start <= b and end >= a]


# `12000 PSI` and `4.0 IN` are a number and a unit; `250 FEET` is too. The
# unit is not read here -- the field tables carry their own -- but it has to
# come off before the decimals can be counted.
DECIMALS = re.compile(r"^([-+]?[0-9]+)\.([0-9]+)(?:\s+[A-Za-z%/.]+)*$")


def decimals(rows, span):
    """How many decimal places the manual prints in this column, or None.

    The samples are the only statement of it. 607's Set format is `III.hh`
    and its response prints `96.00`; 50E's Set format is `DDD.hh` and its
    response prints `60.0`, so the SET mask is not the answer and the
    printed response is.
    """
    seen = [DECIMALS.match(t.strip()) for _s, _e, t in cells_in(rows, span)]
    if not seen or not all(seen):
        return None
    places = {len(hit.group(2)) for hit in seen}
    return places.pop() if len(places) == 1 else None


def alignment(rows, span):
    """Which edge of a column its values are held against.

    Two sample rows of different widths settle it between them -- 62F's
    `4.0 IN` and `4.0 IN PS` share a left edge and so the column is left
    aligned. Where the manual prints one row, or rows of one width, a value
    that is a number is held right and anything else left; every sample on
    this shelf that can be checked agrees with that.
    """
    a, b = span
    seen = []
    for row in rows:
        for start, end, text in cells_of(row):
            if start <= b and end >= a:
                seen.append((start, end, text))
    if len({(e - s) for s, e, _t in seen}) > 1:
        if len({s for s, _e, _t in seen}) == 1:
            return "left"
        if len({e for _s, e, _t in seen}) == 1:
            return "right"
    return ("right" if seen and all(NUMBERISH.match(t.strip()) for _s, _e, t
                                    in seen) else "left")


# The label part of a "LABEL : value" line, where the manual answers a
# console-wide code with a line instead of a table. The colon has to be
# followed by a space or the end of the line, or 50F's own answer --
# `MON DD YYYY HH:MM:SS xM`, which is a FORMAT and not a value -- splits
# into a label nobody wrote and a value that is half a time.
LABELLED = re.compile(r"^(.*?:)(\s+\S.*)$")


def one_line(words, spans):
    """The second line of a two-line block, as a label and a value."""
    line = render(words)
    hit = LABELLED.match(line)
    if not hit:
        start, end = spans[-1][0], spans[-1][1]
        return {"label": "", "start": start, "end": end,
                "align": alignment([words], spans[-1])}
    label = hit.group(1)
    value = hit.group(2)
    start = len(label) + len(value) - len(value.lstrip())
    out = {"label": label, "start": start, "end": len(line) - 1,
           "align": "right" if NUMBERISH.match(line[start:].strip())
           else "left"}
    places = DECIMALS.match(line[start:].strip())
    if places:
        out["places"] = len(places.group(2))
    return out


def _columns(heads, spans, rows):
    """Each column, with the heading over it and what the samples print in it."""
    out = []
    for n, (a, b) in enumerate(spans):
        column = {"head": heads[n], "start": a, "end": b,
                  "align": alignment(rows, (a, b))}
        places = decimals(rows, (a, b))
        if places is not None:
            column["places"] = places
        out.append(column)
    return out


def leads(leading, rest):
    """{line: blank lines above it} for every line of the block that has any.

    The leading between two lines says how many blank lines separate them,
    which is how the header block's own blank was settled -- 304 samples of
    321 -- and this is the same measurement one level down, INSIDE a report.
    Keyed on the line's words rather than its spacing, because a console
    fills in a site's own labels where the sample prints REGULAR UNLEADED.
    """
    out = {}
    for n in range(1, len(rest)):
        gap = int(round((rest[n][0] - rest[n - 1][0]) / leading)) - 1
        words = " ".join(w for _s, _e, w in rest[n][1])
        # a gap of more than a line or two is the page ending, not a blank
        if 1 <= gap <= 2 and words:
            out[words] = gap
    return out


def block_shape(leading, rest):
    """What this Display response is, as data `wire.py` can render from."""
    # rstrip and not strip: 901 and 903 INDENT their titles, and a stripped
    # title reads as a console that does not
    title = render(rest[0][1]).rstrip()
    if title.lstrip().startswith("<") or STAMP.match(title.strip()):
        return None
    y_title = rest[0][0]
    out = {"title": title}
    if len(rest) > 1 and leading:
        out["gap"] = max(int(round((rest[1][0] - y_title) / leading)) - 1, 0)
        inner = leads(leading, rest)
        if inner:
            out["leads"] = inner
    if len(rest) >= 3 and not device_row(rest[1][1]):
        kinds = {device_row(w) for _y, w in rest[2:]}
        if len(kinds) == 1 and kinds != {None} and len(cells_of(rest[1][1])) > 1:
            rows = [w for _y, w in rest[2:]]
            spans = columns_of(rows)
            heads = headings_over(rest[1][1], spans)
            out["kind"] = kinds.pop()
            out["heading"] = render(rest[1][1])
            if leading:
                # measured rather than assumed: two of the settable tables
                # leave a line between the heading and the first row and the
                # rest do not
                out["rowgap"] = max(
                    int(round((rest[2][0] - rest[1][0]) / leading)) - 1, 0)
            out["columns"] = _columns(heads, spans, rows)
            out["sample"] = [render(w) for w in rows]
            return out
    if (len(rest) >= 3 and not device_row(rest[1][1])
            and len(cells_of(rest[1][1])) > 1 and "columns" not in out):
        # A heading over rows this cannot column -- 113's alarm rows,
        # 116's dated ones -- is still a heading, and it still carries
        # the manual's own spacing. Recorded without columns, which is
        # all a hand-built renderer needs to stop counting spaces for
        # its header line.
        out["heading"] = render(rest[1][1])
    if (len(rest) == 2 and device_row(rest[1][1])
            and not device_row(rest[0][1])
            and len(cells_of(rest[0][1])) > 1):
        # 201, 21A and 2E2 print no title: the block opens with the heading
        # and a row under it. The heading is still recorded as the title, for
        # want of anything else to call it, and `untitled` says not to draw
        # one.
        rows = [rest[1][1]]
        spans = columns_of(rows)
        out["untitled"] = True
        out["kind"] = device_row(rest[1][1])
        out["heading"] = render(rest[0][1])
        out["rowgap"] = 0
        out["columns"] = _columns(headings_over(rest[0][1], spans), spans, rows)
        out["sample"] = [render(rest[1][1])]
        return out
    if len(rest) == 2:
        spans = columns_of([rest[1][1]])
        out["line"] = one_line(rest[1][1], spans)
        out["sample"] = [render(rest[1][1])]
    return out


def apply_corrections(found):
    """Put back what a real console prints where the page is wrong.

    The manual is the only source for most of these blocks and it is not the
    LAST word: `tests/console_capture/raw/` holds 904 replies off a running
    TLS-350, and two of them disagree with the page. 60C's title is
    `TANK  STICK HEIGHT OFFSET` with two spaces where p.259 sets one, and it
    runs straight into its heading where the page leaves a blank line.

    That correction was made by hand IN the generated file, and the next run
    of this tool silently undid it -- which is the failure this function
    exists to stop. A correction measured off hardware belongs in a file the
    generator READS, not in the file it writes.

    Returns how many entries were touched.
    """
    try:
        with open(CORRECTIONS, encoding="utf-8") as fh:
            fixes = json.load(fh)
    except (OSError, ValueError):
        print(f"no corrections at {CORRECTIONS}", file=sys.stderr)
        return 0
    done = 0
    for code, fix in fixes.items():
        if code.startswith("_"):
            continue
        entry = found.get(code)
        if entry is None:
            print(f"correction for {code}, which the manual does not draw",
                  file=sys.stderr)
            continue
        for key, value in fix.items():
            if key.startswith("_"):
                continue
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        done += 1
    return done


def main():
    try:
        import fitz            # the dependency check: this tool needs PyMuPDF
    except ImportError:
        print("PyMuPDF (fitz) is needed to read the word boxes", file=sys.stderr)
        return 1
    if not os.path.exists(MANUAL):
        print(f"no manual at {MANUAL}", file=sys.stderr)
        return 1
    doc = fitz.open(MANUAL)
    found, pages = {}, 0
    for page in doc:
        block = response_block(page)
        if not block:
            continue
        code, station, leading, rest, head_gap = block
        shape = block_shape(leading, rest)
        if shape is None:
            continue
        pages += 1
        shape["page"] = page.number + 1
        shape["station"] = station
        shape["head_gap"] = head_gap
        # a code can span pages; the first Display block is the one
        found.setdefault(code, shape)
    corrected = apply_corrections(found)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(found, fh, indent=1, sort_keys=True)
        fh.write("\n")
    tables = sum(1 for v in found.values() if "columns" in v)
    lines = sum(1 for v in found.values() if "line" in v)
    station = sum(1 for v in found.values() if v.get("station"))
    print(f"{pages} function-code pages, {len(found)} with a display title")
    print(f"{tables} of them a device table, {lines} a titled line")
    print(f"{station} of them draw the station header, "
          f"{len(found) - station} do not")
    flush = sum(1 for v in found.values() if not v.get("head_gap"))
    print(f"{len(found) - flush} leave a blank line under the frame, "
          f"{flush} run straight on")
    print(f"{corrected} corrected against a real console's own reply")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
