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
"""A real site's backup, loaded whole: its programming AND what it was saying.

The Tank Monitor Console Multitool's SITE SNAPSHOT (`dump --computer
--with-reports`) is a .vrset with the console's own reports appended to it.
Every report line starts with `#`, so `Console.seed` already read one as a
plain backup and saw nothing else. This reads the rest.

The report block format is the multitool's (`vr_tls/reports.py`), and it is
restated here rather than imported because the two are separate programs:

    #R BEGIN <code> <status> <group>|<name>
    #R X <hex>        the console's raw reply to I<code>00, split over lines
    #R ! <note>       why there are no bytes. Optional
    #R END <code>

Two things are done with the reports.

KEPT. Every block is stored on the console as it came, so the Site Reports
window can show a technician what the real console printed, next to what
this one prints now.

READ INTO THE MODEL. The reports that describe the site's STATE are parsed
back into the stores this console runs from: the cards in the cage (102), the
fuel, water and temperature in each tank (201), every sensor that was
alarming (301 and its family), the alarm history (111, 112), the alarms that
were standing (113), the deliveries (202), the leak test results and history
(208, 207), CSLD (251) and the line leak results (373, 383, 388). From then
on the simulator runs from that state: the site's reports are where it
starts, not a recording it plays back.

A report is read by the same columns this console prints it in, because
those columns were measured off real consoles and the manuals' samples; a
parser that disagreed with the renderer beside it would be a second opinion
about the one layout. Whatever a parser cannot place is listed in the
summary rather than guessed at.
"""
import collections
import re
import time

from . import console as _console
from . import delivery as _delivery
from . import leaktest
from . import versions
from . import wiresensors

# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------
REPORT_BEGIN = "#R BEGIN "
REPORT_RAW = "#R X "
REPORT_NOTE = "#R ! "
REPORT_END = "#R END "

Block = collections.namedtuple("Block", "code name group status raw note")

# A snapshot header, `# VRSET v1  host=... console=350 time=... swver=26`
_HEADER_FIELD = re.compile(r"\b([a-z_]+)=(\S+)")


def header_fields(text):
    """The `key=value` pairs off the file's `# VRSET` line, {} if none."""
    for line in (text or "").splitlines():
        if line.startswith("# VRSET"):
            return dict(_HEADER_FIELD.findall(line))
        if line and not line.startswith("#"):
            break
    return {}


def extract_blocks(text):
    """The report blocks in a .vrset's text, in file order.

    As forgiving as the multitool's own reader: an unterminated block keeps
    what it had, and hex that will not decode leaves the block with no bytes
    rather than stopping the load.
    """
    blocks, cur = [], None

    def close():
        if cur is None:
            return
        raw = None
        if cur["hex"]:
            try:
                raw = bytes.fromhex("".join(cur["hex"]))
            except ValueError:
                raw = None
        blocks.append(Block(cur["code"], cur["name"], cur["group"],
                            cur["status"], raw,
                            "\n".join(cur["notes"]) or None))

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.startswith(REPORT_BEGIN):
            close()
            parts = line[len(REPORT_BEGIN):].strip().split(" ", 2)
            group, _, name = (parts[2] if len(parts) > 2 else "").partition("|")
            cur = {"code": (parts[0] if parts else "").upper(),
                   "status": parts[1] if len(parts) > 1 else "noreply",
                   "group": group.strip(), "name": name.strip(),
                   "hex": [], "notes": []}
        elif line.startswith(REPORT_RAW) and cur is not None:
            cur["hex"].append(line[len(REPORT_RAW):].strip())
        elif line.startswith(REPORT_NOTE) and cur is not None:
            cur["notes"].append(line[len(REPORT_NOTE):])
        elif line.startswith(REPORT_END):
            close()
            cur = None
    close()
    return blocks


def block_json(block):
    """A Block as the state file keeps it: the bytes as hex."""
    return {"code": block.code, "name": block.name, "group": block.group,
            "status": block.status,
            "raw": block.raw.hex() if block.raw else None,
            "note": block.note}


def block_from_json(blob):
    raw = blob.get("raw")
    try:
        raw = bytes.fromhex(raw) if raw else None
    except ValueError:
        raw = None
    return Block(str(blob.get("code", "")), blob.get("name", ""),
                 blob.get("group", ""), blob.get("status", ""), raw,
                 blob.get("note"))


_CHECKSUM = re.compile(r"&&[0-9A-Fa-f]{4}\s*$")


def _unset_blank_headers(console, blocks):
    """Take off the station header lines the site never set.

    A header line cleared with blanks and one never set dump alike, as
    twenty spaces; a report tells them apart, printing the first as twenty
    spaces and the second as nothing (`Console.header_line`). The bench
    TLS-350's snapshot (2026-09-22) had all four at twenty spaces and every
    report printed lines 1 and 2 blank-padded and 3 and 4 empty, so seeded
    as they dumped, lines 3 and 4 came back as twenty spaces on every report.
    The header block is the four lines after the echo, the stamp and a blank.
    """
    for block in blocks:
        if block.status != "data" or not block.raw.startswith(b"\x01\r\nI"):
            continue
        rows = block.raw.decode("latin-1").split("\r\n")
        if len(rows) < 8 or rows[3] != "":
            continue
        header = rows[4:8]
        for n, row in enumerate(header, 1):
            held = console.values.get(f"S503{n:02d}")
            if row == "" and held is not None and not held.strip():
                console.values.pop(f"S503{n:02d}")
        return


def report_lines(block):
    """The report's own lines: the frame, the echoed code and the checksum
    taken off, and nothing else touched. [] for a block with no bytes, or
    for a refusal, which is a reply and not a report."""
    if not block.raw or block.status in ("unsupported", "noreply",
                                         "linkerror"):
        return []
    text = block.raw.decode("latin-1")
    for ch in ("\x01", "\x02", "\x03", "\x04"):
        text = text.replace(ch, "")
    lines = [_CHECKSUM.sub("", ln).rstrip()
             for ln in text.replace("\r\n", "\n").replace("\r", "\n")
             .split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().upper().startswith("I" + block.code):
        lines.pop(0)
    if lines and lines[0].strip().startswith("9999"):
        return []
    return lines


def report_text(block):
    """What a person reads for one block, for the Site Reports window."""
    lines = report_lines(block)
    if lines:
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)
    if block.note:
        return block.note
    return {"empty": "(the console answered, and had nothing in this report)",
            "unsupported": "(the console refused this report: 9999)",
            "noreply": "(the console did not answer)",
            "linkerror": "(not retrieved: the link failed)",
            }.get(block.status, "(no reply kept)")


# ---------------------------------------------------------------------------
# Dates, in every form the reports print one
# ---------------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
     "NOV", "DEC"), start=1)}

# "JAN 22, 1996  3:06 PM", "JAN  1, 2007   8:02 AM", "JAN 16 2006  8:10"
_WORDS = re.compile(r"([A-Z]{3})\s+(\d{1,2}),?\s+(\d{4})"
                    r"(?:\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?)?")
# " 1-16-06  8:00AM", "01-16-06  8:10 AM", "16-01-06  8:10"
_DASHED = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2})\s+(\d{1,2}):(\d{2})"
                     r"\s*([AP]M)?")


def _epoch(year, month, day, hour=0, minute=0, meridiem=None):
    if meridiem:
        hour = hour % 12 + (12 if meridiem == "PM" else 0)
    try:
        return time.mktime((year, month, day, hour, minute, 0, 0, 1, -1))
    except (OverflowError, ValueError):
        return None


def parse_when(text, date_format="01"):
    """A stamp off a report -> console epoch seconds, or None.

    `date_format` is S50F00's, which decides the order of a dashed date:
    05 is day first and 06 year first; everything else is month first.
    """
    text = (text or "").upper()
    m = _WORDS.search(text)
    if m and m.group(1) in _MONTHS:
        return _epoch(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)),
                      int(m.group(4) or 0), int(m.group(5) or 0), m.group(6))
    m = _DASHED.search(text)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if date_format == "05":
            day, month, year = a, b, c
        elif date_format == "06":
            year, month, day = a, b, c
        else:
            month, day, year = a, b, c
        return _epoch(2000 + year if year < 80 else 1900 + year, month, day,
                      int(m.group(4)), int(m.group(5)), m.group(6))
    return None


def _packed(epoch):
    return time.strftime("%y%m%d%H%M", time.localtime(epoch))


_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def _trailing_numbers(line, count):
    """The last `count` numbers on a line, or None if it has fewer."""
    words = line.split()
    if len(words) < count or not all(_NUMBER.match(w)
                                     for w in words[-count:]):
        return None
    return [float(w) for w in words[-count:]]


_TANK_HEAD = re.compile(r"^\s*(?:T|TANK)\s*(\d{1,2})(?:\s*:|\s|$)")


# ---------------------------------------------------------------------------
# 102: the cards in the cage
# ---------------------------------------------------------------------------
# The report's own rows, `  1   4 PROBE / G.T.   163954   163954`, and the
# same rows as the multitool's `# INSTALLED MODULES` block keeps them: its
# lines are stripped, and its corpus writes `SLOT 1  INTERSTITIAL BD` with
# no readings at all. The two readings are optional for that reason.
_SLOT_ROW = re.compile(r"^\s*(?:SLOT\s+)?(\d{1,2})\s{2,}(\S.*?)"
                       r"(?:\s+(\d+)\s+(\d+))?\s*$")
_COMM_ROW = re.compile(r"^\s*COMM\s+(\d)\s+(\S.*?)(?:\s+(\d+)\s+(\d+))?\s*$")

# Printed names a real console has been seen to use that this console does
# not print itself, so `MODULE_PAPER` cannot supply them. Read, never
# written: a name goes into MODULE_PAPER only with its resistance beside it.
#
#   INTERSTITIAL BD   the multitool's second bench TLS-350, slot 1 of its
#                     I10200 (VR-Tool docs/NOTES.md, 2026-07-18). Table
#                     6-1's "Interstitial/Liquid Sensor Interface" is the
#                     eight-input liquid card.
PRINTED_ALIASES = {"INTERSTITIAL BD": ("liquid", None)}


def _squash(name):
    return re.sub(r"\s+", " ", (name or "").upper()).strip()


def _card_names():
    """name on a slot line -> (key, variant) for every card this console
    knows a printed or screen name for."""
    out = {}
    for key, *_rest in _console.MODULES:
        for name in (_console.MODULE_PAPER.get(key),
                     _console.MODULE_SHORT.get(key)):
            if name:
                out.setdefault(_squash(name), (key, None))
    for key, variant, flag in (("probe", _console.PROBE_GT, "probe_gt"),
                               ("smart", _console.SMART_PRESS, "smart_press")):
        for name in (variant.get("paper"), variant.get("short")):
            if name:
                out[_squash(name)] = (key, flag)
    for name, hit in PRINTED_ALIASES.items():
        out.setdefault(_squash(name), hit)
    return out


def _by_ohms(ohms, bay):
    """The card of that bay whose ID resistor that reading is, within 10%.

    The resistance is what identifies a card -- it is the whole purpose of
    Table 6-1 -- and it is the fallback for a name this console has no
    spelling of yet."""
    best, err = None, 0.10
    for key, _label, _part, mbay, _wires, _most in _console.MODULES:
        if mbay != bay:
            continue
        nominal = _console.MODULE_OHMS.get(key)
        if nominal and abs(ohms - nominal) / nominal < err:
            best, err = (key, None), abs(ohms - nominal) / nominal
    for key, variant, flag in (("probe", _console.PROBE_GT, "probe_gt"),
                               ("smart", _console.SMART_PRESS, "smart_press")):
        if bay == "is" and abs(ohms - variant["ohms"]) / variant["ohms"] < err:
            best, err = (key, flag), abs(ohms - variant["ohms"]) / variant["ohms"]
    return best


def _card(name, ohms, bay, names):
    hit = names.get(_squash(name))
    if hit is not None:
        mbay = next((b for k, _l, _p, b, _w, _m in _console.MODULES
                     if k == hit[0]), None)
        if mbay == bay:
            return hit
    return _by_ohms(ohms, bay) if ohms is not None else None


def parse_cage(lines):
    """(modules, comm_slots, flags, unplaced) off a SYSTEM CONFIGURATION
    report, or None if these lines are not one."""
    if not any("SYSTEM CONFIGURATION" in ln for ln in lines):
        return None
    names = _card_names()
    modules, flags, unplaced, comm = {}, {}, [], {}
    # rows only below the title: a station header line can start with a
    # number too
    below = False
    for line in lines:
        if "SYSTEM CONFIGURATION" in line:
            below = True
            continue
        if not below:
            continue
        m = _COMM_ROW.match(line)
        if m:
            port, name = int(m.group(1)), m.group(2)
            por = float(m.group(3)) if m.group(3) else None
            if _squash(name) != "UNUSED":
                comm[port] = (name, por)
            continue
        m = _SLOT_ROW.match(line)
        if not m:
            continue
        slot, name = int(m.group(1)), m.group(2)
        por = float(m.group(3)) if m.group(3) else None
        if _squash(name) == "UNUSED" or not 1 <= slot <= 16:
            continue
        bay = "is" if slot <= _console.BAY_SLOTS["is"] else "power"
        hit = _card(name, por, bay, names)
        if hit is None:
            unplaced.append(f"slot {slot}: {name.strip()}")
            continue
        key, flag = hit
        modules[key] = modules.get(key, 0) + 1
        if flag:
            flags[flag] = True
    comm_slots = {}
    for slot, (first, second) in _console.COMM_PORTS.items():
        if first not in comm:
            if second in comm:
                unplaced.append(f"COMM {second}: {comm[second][0].strip()}")
            continue
        key = None
        if second in comm:
            # both halves of one slot answering is a dual-port module, and
            # the pair of resistors says which one
            key = (_dual_by_ohms(comm[first][1], comm[second][1])
                   if comm[first][1] and comm[second][1] else None)
            if key is None:
                unplaced.append(f"COMM {second}: {comm[second][0].strip()}")
        if key is None:
            hit = _card(comm[first][0], comm[first][1], "comm", names)
            key = hit[0] if hit else None
        if key is None:
            unplaced.append(f"COMM {first}: {comm[first][0].strip()}")
            continue
        comm_slots[slot] = key
        modules[key] = modules.get(key, 0) + 1
    return modules, comm_slots, flags, unplaced


def parse_cage_positions(lines):
    """{"S<slot>" or "C<port>": (card key or None, POR, CURRENT)} off a
    102, every position the report prints, UNUSED ones included."""
    names, out, below = _card_names(), {}, False
    comm_names = {}
    for line in lines:
        if "SYSTEM CONFIGURATION" in line:
            below = True
            continue
        if not below:
            continue
        m = _COMM_ROW.match(line)
        if m:
            where, bay = f"C{int(m.group(1))}", "comm"
        else:
            m = _SLOT_ROW.match(line)
            if not m or not 1 <= int(m.group(1)) <= 16:
                continue
            slot = int(m.group(1))
            where = f"S{slot}"
            bay = "is" if slot <= _console.BAY_SLOTS["is"] else "power"
        por = float(m.group(3)) if m.group(3) else None
        now = float(m.group(4)) if m.group(4) else None
        key = None
        if _squash(m.group(2)) != "UNUSED":
            hit = _card(m.group(2), por, bay, names)
            key = hit[0] if hit else "?"
            if bay == "comm":
                comm_names[where] = key
        out[where] = (key, por, now)
    return out


def _reset_cage(console, lines):
    """What was in the cage at the site's last cold start, off 102's POWER ON
    RESET column, or None where the report carries no readings.

    A card fitted since then reads the empty rail in that column: the bench
    TLS-350's WPLLD Comm Board, fitted with the power off two days after its
    cold start, printed `COMM 3 WPLLD COMM BD 15000000 202958` (2026-09-24).
    Such a position was empty at the reset. A card pulled since then would
    print the old card's figure over UNUSED; which card that was is not
    printed, so it is not guessed."""
    cage, read = console.current_cage(), False
    empty = min(_console.EMPTY_OHMS.values()) * 0.9
    below = False
    for line in lines:
        if "SYSTEM CONFIGURATION" in line:
            below = True
            continue
        if not below:
            continue
        m = _COMM_ROW.match(line)
        where = f"C{m.group(1)}" if m else None
        if m is None:
            m = _SLOT_ROW.match(line)
            where = f"S{int(m.group(1))}" if m else None
        if m is None or not m.group(3):
            continue
        read = True
        if _squash(m.group(2)) != "UNUSED" and float(m.group(3)) >= empty:
            cage[where] = None
    return cage if read else None


def reports_comm_bay(lines, cut=False):
    """Do these lines say what is in the communication bay? The multitool
    caps its copy of the report, and a cage read without its comm rows is
    a cage whose comm bay is unknown, not empty. The report prints all six
    positions, UNUSED ones too, so a copy that was cut knows the bay only
    if all six made it."""
    rows = sum(1 for ln in lines if _COMM_ROW.match(ln))
    return rows >= _console.BAY_SLOTS["comm"] if cut else rows > 0


def modules_block(text):
    """The multitool's `# INSTALLED MODULES` block (v0.62.0 on), as report
    lines, and whether it was cut short. ([], False) when there is none."""
    lines, inside, cut = [], False, False
    for line in (text or "").splitlines():
        if line.startswith("# INSTALLED MODULES"):
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith("#   "):
            break
        body = line[4:]
        if re.match(r"^\.\.\. \d+ more line", body):
            cut = True
            continue
        lines.append(body)
    return lines, cut


def _dual_by_ohms(first, second):
    best, err = None, 0.10
    for key, (a, b) in _console.COMM_DUAL.items():
        miss = max(abs(first - a[2]) / a[2], abs(second - b[2]) / b[2])
        if miss < err:
            best, err = key, miss
    return best


# ---------------------------------------------------------------------------
# 201: what is in each tank
# ---------------------------------------------------------------------------
_INVENTORY_LABELS = re.compile(r"TC VOLUME|VOLUME|ULLAGE|HEIGHT|WATER|TEMP")


def _temperature_for(console, tank, row):
    """The temperature to hold a tank at, off its I201 row.

    The row prints the temperature to two places, and the TC volume beside
    it was worked out from the whole of it, so holding the printed figure
    could land a gallon away: a truck stop's tank came back `6992` where it
    left `6993`, at 61.43 both ways, and which preset did depended on the
    hour. Anywhere in the printed figure's rounding the row's own TC volume
    is reproduced, that is the temperature; nearest the printed one first."""
    printed = row["temp"]
    want, volume = row.get("tc_volume"), row.get("volume")
    if want is None or volume is None:
        return printed
    for step in sorted(range(-9, 10), key=abs):
        degrees = printed + step * 0.0005
        # I201 prints the TC volume cut to a whole gallon, not rounded:
        # 6992.993 is `6992`
        if int(console.tc_volume_at(tank, volume, degrees)) == int(want):
            return degrees
    return printed


def parse_inventory(lines):
    """{tank: {"volume", "water", "temp", ...}} off an I201."""
    out, labels = {}, None
    for line in lines:
        if labels is None:
            if line.lstrip().startswith("TANK") and "VOLUME" in line:
                labels = _INVENTORY_LABELS.findall(line.split("PRODUCT")[-1])
            continue
        m = re.match(r"^\s*(\d{1,2})\s", line)
        if not m:
            continue
        values = _trailing_numbers(line, len(labels))
        if values is None:
            continue
        out[int(m.group(1))] = {k.lower().replace(" ", "_"): v
                                for k, v in zip(labels, values)}
    return out


# ---------------------------------------------------------------------------
# Sensor status: 301, 306, 311, 341, 346, 34B and the smart sensor's 315
# ---------------------------------------------------------------------------
SENSOR_STATUS = dict(wiresensors.STATUS_CODE, **{"315": "smart"})


def _sensor_words(module):
    """status words -> state, longest first so FUEL ALARM is not read out of
    WATER OUT ALARM's tail."""
    if module == "smart":
        words = {w: s for s, w in _console.SMART_STATE_WORDS.items()}
    else:
        words = {w: s for s, w in _console.SENSOR_STATE_WORDS.items()}
        aa = wiresensors.FAMILY.get(module, ("",))[0]
        nn_state = {nn: s for s, nn in _console.SENSOR_STATE_NN.items()}
        for nn, word in (_console.STATUS_TYPES.get(aa) or {}).items():
            if nn in nn_state:
                words.setdefault(word.upper(), nn_state[nn])
    return sorted(words.items(), key=lambda kv: -len(kv[0]))


def parse_sensor_status(lines, module):
    """{sensor number: state} off a sensor STATUS report."""
    words = _sensor_words(module)
    out = {}
    for line in lines:
        m = re.match(r"^\s*(\d{1,2})\s", line)
        if not m:
            continue
        tail = _squash(line)
        for word, state in words:
            if tail.endswith(" " + _squash(word)):
                out[int(m.group(1))] = state
                break
    return out


# ---------------------------------------------------------------------------
# Alarms: 111 and 112 are the history, 113 is what is standing
# ---------------------------------------------------------------------------
def _letters():
    out = collections.defaultdict(list)
    for aa, letter in _console.STATUS_DEVICE_CODE.items():
        out[letter].append(aa)
    return out


def _alarm_number(aas, described):
    """(aa, nn) whose alarm type prints as `described`, or None."""
    want = _squash(described)
    for aa in aas:
        for nn, desc in (_console.STATUS_TYPES.get(aa) or {}).items():
            if _squash(desc.upper()[:21]) == want:
                return aa, nn
    return None


_HISTORY_TAIL = re.compile(r"\s(ALARM|CLEAR)\s+(\S.*\S)\s*$")


def parse_alarm_rows(lines, active=False, date_format="01"):
    """[{"aa","nn","tt","at","state"}] off 111/112 (or 113 with `active`),
    newest first as printed, and [row text] for the rows not placed.

    The columns are `Console.alarm_row`'s, which were measured off the bench
    TLS-350: the alarm type is always columns 35 to 56, the ID and the
    category start the line, and the stamp ends it.
    """
    letters, rows, unplaced = _letters(), [], []
    head = False
    for line in lines:
        if line.startswith("ID  CATEGORY"):
            head = True
            continue
        if not head or not line.strip():
            continue
        described = line[35:56].strip()
        if active:
            state, stamp = "02", line[56:]
        else:
            m = _HISTORY_TAIL.search(line[50:])
            if not m:
                unplaced.append(line.strip())
                continue
            state = "01" if m.group(1) == "CLEAR" else "02"
            stamp = m.group(2)
        ident = re.match(r"^([A-Za-z])\s?(\d{1,2})\b", line)
        category = line[4:14].strip()
        if ident:
            aas, tt = letters.get(ident.group(1), []), int(ident.group(2))
        elif category.startswith("SYSTEM"):
            aas, tt = ["01"], 0
        else:
            aas = [aa for aa in _console.STATUS_TYPES
                   if aa not in _console.STATUS_DEVICE_CODE]
            tt = 0
        found = _alarm_number(aas, described)
        at = parse_when(stamp, date_format)
        # A standing alarm may carry no stamp at all: the bench TLS-350's
        # 113 printed `Q 1  OTHER    REG TURBINE          PLLD OPEN ALARM`
        # and nothing after it, with its alarm history not answering
        # (2026-09-22). It is still standing; when it began is unknown, and
        # `at` says so rather than guessing.
        if found is None or (at is None and not (active and not
                                                  stamp.strip())):
            unplaced.append(line.strip())
            continue
        rows.append({"aa": found[0], "nn": found[1], "tt": f"{tt:02d}",
                     "at": _packed(at) if at is not None else None,
                     "state": state})
    return rows, unplaced


_TANK_HISTORY_HEAD = re.compile(r"^TANK\s+(\d{1,2})\b")


def parse_tank_alarm_history(lines, date_format="01"):
    """[alarm row] off a 206: each type named once at column five, its
    dates from column thirty, three to a type."""
    out, tank, nn = [], None, None
    for line in lines:
        m = _TANK_HISTORY_HEAD.match(line)
        if m:
            tank, nn = int(m.group(1)), None
            continue
        if tank is None or len(line) < 31:
            continue
        name, stamp = line[:30].strip(), line[30:].strip()
        if name:
            found = _alarm_number(["02"], name)
            nn = found[1] if found else None
        at = parse_when(stamp, date_format)
        if nn and at is not None:
            out.append({"aa": "02", "nn": nn, "tt": f"{tank:02d}",
                        "at": _packed(at), "state": "02"})
    return out


_LINE_HISTORY_ROW = re.compile(r"^\s+(\S.*?[AP]M)\s{2,}(\S.*\S)\s*$")


def parse_line_alarm_history(lines, date_format="01"):
    """[alarm row] off a 382 or 387: under each line's head, a stamp and
    the alarm's name, newest first."""
    out, aa, tt = [], None, None
    for line in lines:
        m = _LINE_HEAD.match(line)
        if m:
            aa = "21" if m.group(1) == "Q" else "26"
            tt = int(m.group(2))
            continue
        m = _LINE_HISTORY_ROW.match(line)
        if not m or aa is None:
            continue
        at = parse_when(m.group(1), date_format)
        found = _alarm_number([aa], m.group(2))
        if at is not None and found:
            out.append({"aa": aa, "nn": found[1], "tt": f"{tt:02d}",
                        "at": _packed(at), "state": "02"})
    return out


_SHIFT_TIME = re.compile(r"^SHIFT\s+(\d)\s+TIME:\s*(\d{1,2}):(\d{2})\s*([AP]M)?")
_SHIFT_TANK = re.compile(r"^\s*(\d{1,2})\s+\S.*\bVOLUME TC VOLUME")
_SHIFT_VALUES = re.compile(r"^(?:SHIFT\s+\d+\s+)?\s*(STARTING|ENDING) VALUES"
                           r"\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?[\d.]+)"
                           r"\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$")
_SHIFT_DELIVERY = re.compile(r"^\s*DELIVERY VALUE\s+(-?\d+)")


def parse_shift_report(lines):
    """{shift: {"time": (h, m), "tanks": {tank: {"start", "end", "delivery"}}}}
    off a 204, each "start"/"end" the six figures as `shifts` keeps them."""
    out, shift, tank = {}, None, None
    for line in lines:
        m = _SHIFT_TIME.match(line)
        if m:
            hour = int(m.group(2)) % 12 + (12 if m.group(4) == "PM" else 0)
            shift = int(m.group(1))
            out[shift] = {"time": (hour, int(m.group(3))), "tanks": {}}
            continue
        m = _SHIFT_TANK.match(line)
        if m and shift is not None:
            tank = int(m.group(1))
            out[shift]["tanks"][tank] = {"start": None, "end": None,
                                         "delivery": 0.0}
            continue
        if shift is None or tank is None:
            continue
        m = _SHIFT_VALUES.match(line)
        if m:
            vol, tc, ullage, height, water, temp = (float(g) for g
                                                    in m.groups()[1:])
            out[shift]["tanks"][tank]["start" if m.group(1) == "STARTING"
                                      else "end"] = {
                "volume": vol, "tc": tc, "ullage": ullage, "height": height,
                "water": water, "temp": temp}
            continue
        m = _SHIFT_DELIVERY.match(line)
        if m:
            out[shift]["tanks"][tank]["delivery"] = float(m.group(1))
    return out


def load_shifts(console, report, taken):
    """Put a 204's last shift into `shifts`: closed at its own start time's
    last occurrence before the report was printed, opened a day before
    that, and the shift running now starting from where it closed."""
    sh = console.shifts
    for shift, entry in sorted(report.items()):
        hour, minute = entry["time"]
        t = time.localtime(taken)
        end = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0,
                           0, 0, -1))
        if end > taken:
            end -= 86400.0
        start_snap = {"at": end - 86400.0, "tanks": {}}
        end_snap = {"at": end, "tanks": {}}
        adjust = {}
        for tank, figs in entry["tanks"].items():
            if figs["start"]:
                start_snap["tanks"][tank] = dict(figs["start"])
            if figs["end"]:
                end_snap["tanks"][tank] = dict(figs["end"])
            adjust[tank] = figs["delivery"]
        sh.closed[shift] = {"start": start_snap, "end": end_snap,
                            "adjust": adjust}
        sh.open = {"shift": shift, "start": end_snap, "adjust": {}}
    return len(report)


# ---------------------------------------------------------------------------
# B87 to B8E: the pressure line diagnostics behind the 3.0 DIAG printout
# ---------------------------------------------------------------------------
# Which store each code's rows go back into: (kind, readings or cycles, leg)
LINE_DIAGNOSTICS = {"B87": ("plld", "readings", "gross"),
                    "B88": ("plld", "readings", "mid"),
                    "B8B": ("wplld", "readings", "gross"),
                    "B8C": ("wplld", "readings", "mid"),
                    "B89": ("plld", "cycles", "periodic"),
                    "B8A": ("plld", "cycles", "annual"),
                    "B8D": ("wplld", "cycles", "periodic"),
                    "B8E": ("wplld", "cycles", "annual")}

_PUMPOFF_ROW = re.compile(r"^(.*[AP]M)\s+(-?[\d.]+) PSI\s+(-?[\d.]+) PSI"
                          r"\s+(-?[\d.]+) PSI\s*$")
_PRECISION_ROW = re.compile(r"^(.*[AP]M)\s+(-?[\d.]+) PSI\s+(-?[\d.]+)"
                            r"\s+(\d+)\s+(PASSED|FAILED)\s*$")


def parse_line_diagnostic(code, lines):
    """{line: [Reading or Cycle], oldest first} off one of B87 to B8E, in
    the display form `wirelines` prints and the Multitool backs up since
    its 830c86d. A pump-Off row is Pon, P1 and P2 under TEST PASSES, TEST
    FAILS or HI PRESSURE EVENTS; a precision row is Pon, the ratio, the
    minutes and the verdict. The ratio is the rate over the rate's own
    threshold ("Ratio <1 Pass, >1 Fail"), so the rate is worked back from
    it; P1 and P2 are not on a precision row and are left at Pon."""
    from . import pressure
    _kind, store, leg = LINE_DIAGNOSTICS[code]
    out, line, passed = {}, None, None
    for text in lines:
        m = _LINE_HEAD.match(text)
        if m:
            line, passed = int(m.group(2)), None
            out.setdefault(line, [])
            continue
        upper = text.upper()
        if "TEST PASSES" in upper:
            passed = True
            continue
        if "TEST FAILS" in upper:
            passed = False
            continue
        if "HI PRESSURE" in upper:
            passed = True        # an event, told apart by its Pon (`high`)
            continue
        if line is None:
            continue
        if store == "readings":
            m = _PUMPOFF_ROW.match(text.strip())
            at = parse_when(m.group(1)) if m else None
            if m and at is not None and passed is not None:
                out[line].append(pressure.Reading(
                    at, float(m.group(2)), float(m.group(3)),
                    float(m.group(4)), passed))
            continue
        m = _PRECISION_ROW.match(text.strip())
        at = parse_when(m.group(1)) if m else None
        if m and at is not None:
            pon, ratio = float(m.group(2)), float(m.group(3))
            out[line].append(pressure.Cycle(
                at, pon, pon, pon, ratio * pressure.THRESHOLD[leg], ratio,
                int(m.group(4)), m.group(5) == "PASSED"))
    for rows in out.values():
        rows.sort(key=lambda r: r.when)
    return out


# ---------------------------------------------------------------------------
# 202: deliveries
# ---------------------------------------------------------------------------
_DELIVERY_ROW = re.compile(r"^\s*(END|START):\s+(.*\S)\s*$")


def parse_deliveries(lines):
    """{tank: [(start snapshot, end snapshot)]}, newest first as printed."""
    out, tank, end = {}, None, None
    for line in lines:
        m = _TANK_HEAD.match(line)
        if m and not _DELIVERY_ROW.match(line):
            tank, end = int(m.group(1)), None
            continue
        m = _DELIVERY_ROW.match(line)
        if not m or tank is None:
            continue
        values = _trailing_numbers(m.group(2), 5)
        at = parse_when(m.group(2))
        if values is None or at is None:
            continue
        shot = {"at": at, "volume": values[0], "tc": values[1],
                "water": values[2], "temp": values[3], "height": values[4]}
        if m.group(1) == "END":
            end = shot
        elif end is not None:
            out.setdefault(tank, []).append((shot, end))
            end = None
    return out


# ---------------------------------------------------------------------------
# Leak tests: 208 results, 207 history, 251 CSLD
# ---------------------------------------------------------------------------
_RESULT_ROW = re.compile(r"^\s*(ANNUAL|PERIODIC|GROSS)\s+(.*?)\s+"
                         r"(PASSED|FAILED|INVALID)\s+(-?\d+\.\d+)"
                         r"(?:\s+(\d+))?\s+(\d+)\s*$")


def parse_leak_results(lines):
    """[Result] off an I208."""
    out, tank = [], None
    for line in lines:
        m = _TANK_HEAD.match(line)
        if m:
            tank = int(m.group(1))
            continue
        m = _RESULT_ROW.match(line)
        if not m or tank is None:
            continue
        started = parse_when(m.group(2))
        if started is None:
            continue
        out.append(leaktest.Result(
            "tank", tank, m.group(1).lower(), m.group(3),
            float(m.group(4)), float(m.group(5) or 0), float(m.group(6)),
            started))
    return out


_HISTORY_ROW = re.compile(r"^(.*?\d{1,2}:\d{2}\s*[AP]M)\s+(?:(\d+)\s+)?"
                          r"(\d+)\s+(\d+\.\d)\s+(STANDARD|CSLD)\s*$")


def parse_leak_history(lines):
    """[Result] off an I207: every pass it lists, at the rate its block
    names. A pass is all a history holds, so each comes back PASSED."""
    out, tank, rate_key = [], None, None
    for line in lines:
        m = _TANK_HEAD.match(line)
        if m and "TEST" not in line:
            tank, rate_key = int(m.group(1)), None
            continue
        upper = line.upper()
        for key in ("GROSS", "ANNUAL", "PERIODIC"):
            if upper.startswith(("LAST " + key, "FULLEST " + key)):
                rate_key = key.lower()
        m = _HISTORY_ROW.match(line)
        if not m or tank is None or rate_key is None:
            continue
        started = parse_when(m.group(1))
        if started is None:
            continue
        out.append(leaktest.Result(
            "tank", tank, rate_key, leaktest.PASSED,
            leaktest.RATES[rate_key], float(m.group(2) or 0),
            float(m.group(3)), started, method=m.group(5),
            percent=float(m.group(4))))
    return out


_CSLD_ROW = re.compile(r"^\s*PER:\s+(.*\d{4})\s+(PASS|FAIL|INCR|WARN)\s*$")
# I251's own form, a row a tank: `  1  REGULAR UNLEADED   PER: OCT  7, 2026 PASS`
_CSLD_TABLE_ROW = re.compile(r"^\s*(\d{1,2})\s+.*?\bPER:\s+(.*\d{4})\s+"
                             r"(PASS|FAIL|INCR|WARN)\s*$")


def parse_csld(lines):
    """{tank: (result, epoch)} off an I251."""
    out, tank = {}, None
    for line in lines:
        m = _CSLD_TABLE_ROW.match(line)
        if m:
            when = parse_when(m.group(2))
            if when is not None:
                out[int(m.group(1))] = (m.group(3), when)
            continue
        m = _TANK_HEAD.match(line)
        if m:
            tank = int(m.group(1))
            continue
        m = _CSLD_ROW.match(line)
        if m and tank is not None:
            when = parse_when(m.group(1))
            if when is not None:
                out[tank] = (m.group(2), when)
    return out


_LINE_HEAD = re.compile(r"^\s*([QW])\s*(\d{1,2}):")
_LINE_RATE = {"3.0": "gross", "0.20": "periodic", "0.10": "annual"}
_LINE_RATE_WORD = {v: k for k, v in _LINE_RATE.items()}
_LINE_VERDICT = re.compile(r"^(.*[AP]M)\s+(PASS|FAIL|INVALID)\s*$")
_LINE_WORD = {"PASS": leaktest.PASSED, "FAIL": leaktest.FAILED,
              "INVALID": leaktest.INVALID}


def parse_line_results(lines):
    """[Result] off a 373, 383 or 388: every verdict listed at each rate.

    The 3.0 block has one, LAST TEST. Each precision rate is a list, newest
    first -- a version 23 site printed ten 0.20 results and eight 0.10 under
    each line, and 576013-635 Rev AA counts them ("NN - Number of 0.10
    gal/hr test results ... to follow"). This took the first and stopped.
    """
    out, kind, number, rate_key = [], None, None, None
    for line in lines:
        m = _LINE_HEAD.match(line)
        if m:
            kind = "plld" if m.group(1) == "Q" else "wplld"
            number, rate_key = int(m.group(2)), None
            continue
        m = re.match(r"^\s*(3\.0|0\.20|0\.10) GAL/HR RESULTS:", line)
        if m:
            rate_key = _LINE_RATE[m.group(1)]
            continue
        if line.strip().startswith(("NUMBER OF TESTS", "NO-VENT")):
            rate_key = None
            continue
        m = _LINE_VERDICT.match(line.strip())
        if m and kind and rate_key:
            started = parse_when(m.group(1))
            if started is not None:
                out.append(leaktest.Result(
                    kind, number, rate_key, _LINE_WORD[m.group(2)],
                    leaktest.RATES[rate_key], 0.0, 0.0, started))
    return out


_LINE_COUNT = re.compile(r"^\s*(PREV 24 HOURS|SINCE MIDNIGHT)\s*:\s*(\d+)")


def parse_line_counts(lines):
    """{(kind, line): (passed in the previous 24 hours, since midnight)} off
    a 373 or 383's NUMBER OF TESTS PASSED, which is the 3.0 test's."""
    out, key, day = {}, None, None
    for line in lines:
        m = _LINE_HEAD.match(line)
        if m:
            key = ("plld" if m.group(1) == "Q" else "wplld", int(m.group(2)))
            continue
        m = _LINE_COUNT.match(line)
        if m and key:
            if m.group(1) == "PREV 24 HOURS":
                day = int(m.group(2))
            elif day is not None:
                out[key] = (day, int(m.group(2)))
                day = None
    return out


_NO_VENT = re.compile(r"^\s*(\d+) OUT OF (\d+) TESTS?\s*$")


def parse_no_vent(lines):
    """{(kind, line): (aborts, tests)} off a 373 or 388's NO-VENT TEST
    ABORTS, the line under its heading."""
    out, key, armed = {}, None, False
    for line in lines:
        m = _LINE_HEAD.match(line)
        if m:
            key = ("plld" if m.group(1) == "Q" else "wplld", int(m.group(2)))
            armed = False
            continue
        if line.strip().startswith("NO-VENT TEST ABORTS"):
            armed = True
            continue
        m = _NO_VENT.match(line)
        if m and armed and key:
            out[key] = (int(m.group(1)), int(m.group(2)))
            armed = False
    return out


_LINE_TESTING = re.compile(r"\bTESTING (?:AT )?(3\.00?|0\.20|0\.10) GAL/HR")


def parse_line_status(lines):
    """{(kind, line): rate_key} for the lines a 381 or 386 shows testing."""
    out = {}
    for line in lines:
        m = _LINE_HEAD.match(line)
        t = _LINE_TESTING.search(line)
        if m and t:
            rate = "3.0" if t.group(1).startswith("3") else t.group(1)
            out[("plld" if m.group(1) == "Q" else "wplld",
                 int(m.group(2)))] = _LINE_RATE[rate]
    return out


_LINE_FIRST = re.compile(r"^\s*(?:LAST\s+(3\.0)|FIRST\s+(0\.10|0\.20))"
                         r"\s+PASS(?: EACH MONTH)?:\s*(.*)$")


def parse_line_history(lines):
    """[Result] off a 374, 384 or 389: the last 3.0 pass, and the first
    0.10 and 0.20 pass of each month, every one of them a PASS. A month
    after the first is a date alone on its line, under the one before."""
    out, kind, number, rate_key = [], None, None, None

    def add(text):
        started = parse_when(text)
        if started is not None and kind and rate_key:
            out.append(leaktest.Result(
                kind, number, rate_key, leaktest.PASSED,
                leaktest.RATES[rate_key], 0.0, 0.0, started))

    for line in lines:
        m = _LINE_HEAD.match(line)
        if m:
            kind = "plld" if m.group(1) == "Q" else "wplld"
            number, rate_key = int(m.group(2)), None
            continue
        m = _LINE_FIRST.match(line)
        if m:
            rate_key = _LINE_RATE[m.group(1) or m.group(2)]
            if m.group(3).strip():
                add(m.group(3))
            continue
        if not line.strip():
            continue
        if rate_key and line.startswith(" ") and _WORDS.search(line):
            add(line.strip())
    return out


def gross_passes(kind, number, counts, last, taken):
    """The 3.0 passes a 373 counts and does not list, put back as results.

    The report gives the last test and two counts, and this console works
    both counts out of its history; a site loaded with one gross pass read
    `PREV 24 HOURS : 1` where the site's read 60. So the passes it counted
    are spread evenly through the windows they were counted in -- before
    midnight for the day's count less the night's, after it for the rest --
    and the last one is the LAST TEST the report names. When each of the
    others happened is not on the report; how many there were is.
    """
    day, since = counts
    midnight = time.mktime(time.localtime(taken)[:3] + (0, 0, 0, 0, 1, -1))
    out = []

    def spread(n, start, end):
        if n <= 0 or end <= start:
            return
        step = (end - start) / (n + 1)
        out.extend(leaktest.Result(kind, number, "gross", leaktest.PASSED,
                                   leaktest.RATES["gross"], 0.0, 0.0,
                                   start + step * (i + 1)) for i in range(n))

    tonight = since - (1 if last is not None and last >= midnight else 0)
    spread(tonight, midnight, last if last is not None else taken)
    before = day - since - (1 if last is not None and last < midnight else 0)
    spread(before, taken - 86400.0, midnight)
    return out


# ---------------------------------------------------------------------------
# Loading it
# ---------------------------------------------------------------------------
def _decode_smodule(text):
    """`330160-HTU-A` -> the software keys it says are installed, or None.

    The inverse of `Console.s_module_number`: hundreds BIR, tens the line
    leak bundle, units CSLD / Fuel Manager / ISD as a subset.
    """
    m = re.match(r"^330160-(\d)(\d)(\d)", text or "")
    if not m:
        return None
    hundreds, tens, units = m.groups()
    keys = {"bir": hundreds == "1",
            "plld010": tens in ("1", "5"), "plld020": tens == "1"}
    subset = next((s for s, u in _console.Console.SEM_UNITS.items()
                   if u == units), None)
    if subset is None:
        return None
    for k in ("csld", "fuelman", "isd"):
        keys[k] = k in subset
    return keys


def read(path):
    """(text, header fields, blocks) for a backup file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return text, header_fields(text), extract_blocks(text)


def load(console, path, now=None):
    """Put a site backup into this console: programming, cards and state.

    Returns a summary dict: `values` seeded, `reports` kept, and `applied`
    and `skipped` as lists of short lines for a person to read. Raises what
    `Console.seed` raises for a file that cannot be read at all.
    """
    import os
    text, head, blocks = read(path)
    by_code = {}
    for block in blocks:
        by_code.setdefault(block.code, block)
    applied, skipped = [], []

    def lines_of(code):
        block = by_code.get(code)
        return report_lines(block) if block else []

    # The cards before the programming, because a console reads its
    # programming through the cards it has.
    version, board = console.version, console.board
    modules, comm_slots = dict(console.modules), dict(console.comm_slots)
    probe_gt, smart_press = console.probe_gt, console.smart_press
    software = dict(console.software)
    console.reset(keep_clock=True)
    console.version, console.board = version, board
    swver = head.get("swver")
    console.software_part = None
    if swver and swver.isdigit() and versions.known(int(swver)):
        console.version = int(swver)
        if console.board not in versions.boards_for(console.version):
            boards = versions.boards_for(console.version)
            if boards:
                console.board = boards[0]
        # the site's own SOFTWARE#, which the header carries as `sw=`
        sw = (head.get("sw") or "").strip().upper()
        if re.match(rf"^346\d{int(swver):02d}-\d{{3}}-[A-Z]$", sw):
            console.software_part = sw
        applied.append(f"software version {console.version}"
                       + (f", SOFTWARE# {sw}" if console.software_part
                          else ""))
    keys = _decode_smodule(head.get("smodule", ""))
    if keys is not None:
        console.software = {k: v for k, v in keys.items() if v}
        applied.append("software keys from " + head["smodule"])
    else:
        console.software = software
    console.rom_at_boot = console.version

    # The cage: the snapshot's own 102 if it has one, and otherwise the
    # `# INSTALLED MODULES` block the multitool has written on every backup
    # since v0.62.0, which is the same report copied into comments.
    cage_lines, cut, where = lines_of("102"), False, "the 102 report"
    if parse_cage(cage_lines) is None:
        cage_lines, cut = modules_block(text)
        where = "the backup's INSTALLED MODULES block"
    cage = parse_cage(cage_lines)
    if cage is not None:
        found, slots, flags, unplaced = cage
        comm_keys = ({k for k, bay in _console.MODULE_BAY.items()
                      if bay == "comm"} | set(_console.COMM_DUAL))
        if not reports_comm_bay(cage_lines, cut):
            # The copy stopped before the comm bay. Unknown is not empty:
            # a cage with no comm card is a console nothing can talk to,
            # and this simulator's socket IS that serial port.
            found = {k: n for k, n in found.items() if k not in comm_keys}
            found.update({k: n for k, n in modules.items() if k in comm_keys})
            slots = comm_slots
            skipped.append(f"{where} stops before the communication bay"
                           + (" (the backup cut it short)" if cut else "")
                           + ": the comm cards were left as they were")
        console.modules = found
        console.comm_slots = slots
        console.probe_gt = bool(flags.get("probe_gt"))
        console.smart_press = bool(flags.get("smart_press"))
        console.reset_cage = _reset_cage(console, cage_lines)
        # where each card sits and what each position read, so the cage
        # prints as the site's did while those cards stay fitted
        positions = parse_cage_positions(cage_lines)
        if positions and "?" not in [k for k, _p, _n in positions.values()]:
            # the comm bay's halves are the console's to name: take its own
            # key for each fitted position rather than the name's guess
            seats = console.comm_positions()
            for port in range(1, _console.BAY_SLOTS["comm"] + 1):
                where = f"C{port}"
                if where in positions and positions[where][0] is not None:
                    seat = seats.get(port)
                    _k, por, now = positions[where]
                    positions[where] = (seat[1] if seat else None, por, now)
            console.site_cage = positions
        applied.append("cards from " + where + ": " + (", ".join(
            f"{n} x {k}" if n > 1 else k
            for k, n in sorted(found.items())) or "none"))
        skipped += [f"card not recognised, {u}" for u in unplaced]
    else:
        console.modules, console.comm_slots = modules, comm_slots
        console.probe_gt, console.smart_press = probe_gt, smart_press
        skipped.append("no SYSTEM CONFIGURATION in the backup: "
                       "the cards were left as they were")
    console._mt_seen = console.has("mt")
    if not console.serial_port_works():
        # Faithful and baffling: this socket is the console's serial port,
        # and a cage without a card it answers through is a console that
        # says nothing. Said here so a silent simulator has a reason.
        skipped.append("this site's comm bay has no RS-232, modem, MT or "
                       "aux port, so the simulator will not answer on its "
                       "socket until one is fitted on the bench")

    values = console.seed(path)
    _unset_blank_headers(console, blocks)
    date_format = (console.values.get("S50F00") or "01").strip()[-2:]

    inventory = parse_inventory(lines_of("201"))
    for tank, row in sorted(inventory.items()):
        level = console.tank_level.setdefault(tank, {})
        if "volume" in row:
            level["volume"] = row["volume"]
        level["water"] = row.get("water", 0.0)
        if "temp" in row:
            console.hold_temperature(tank, _temperature_for(console, tank, row))
    if inventory:
        applied.append(f"inventory for {len(inventory)} tank(s), "
                       "temperatures held at the reported value")

    sensors = 0
    for code, module in sorted(SENSOR_STATUS.items()):
        for number, state in parse_sensor_status(lines_of(code),
                                                 module).items():
            if state == "normal":
                console.sensor_state.pop((module, str(number)), None)
                continue
            console.sensor_state[(module, str(number))] = state
            sensors += 1
    if sensors:
        applied.append(f"{sensors} sensor(s) in alarm")

    history, unplaced = [], []
    for code in ("111", "112"):
        rows, missed = parse_alarm_rows(lines_of(code),
                                        date_format=date_format)
        history += rows
        unplaced += missed
    history.sort(key=lambda r: r["at"], reverse=True)
    console.alarm_log[:] = history
    if history:
        applied.append(f"{len(history)} alarm history row(s)")
    # The tanks' and the lines' own histories, which go back further than
    # 111 and 112 do: theirs, and the rows 111 and 112 hold
    device_rows = (parse_tank_alarm_history(lines_of("206"), date_format)
                   + parse_line_alarm_history(lines_of("382"), date_format)
                   + parse_line_alarm_history(lines_of("387"), date_format))
    for row in history + device_rows:
        console.keep_device_history(row)
    if device_rows:
        applied.append(f"{len(device_rows)} tank and line alarm history "
                       "row(s)")
    skipped += [f"alarm history row not recognised: {u}" for u in unplaced]

    taken_204 = parse_when((lines_of("204") or [""])[0])
    if taken_204 is not None:
        shifts = parse_shift_report(lines_of("204"))
        if shifts and load_shifts(console, shifts, taken_204):
            applied.append(f"the last shift's inventory for "
                           f"{len(next(iter(shifts.values()))['tanks'])} "
                           "tank(s)")

    deliveries = parse_deliveries(lines_of("202") or lines_of("20C"))
    for tank, drops in deliveries.items():
        records = []
        for start, end in drops[:10]:
            record = _delivery.Delivery(tank, start)
            record.end = end
            records.append(record)
        console.deliveries.records[tank] = records
    if deliveries:
        applied.append(f"{sum(len(v) for v in deliveries.values())} "
                       "delivery record(s)")

    results = (parse_leak_results(lines_of("208"))
               + parse_line_results(lines_of("373"))
               + parse_line_results(lines_of("383"))
               + parse_line_results(lines_of("388")))
    history_results = (parse_leak_history(lines_of("207"))
                       + parse_line_history(lines_of("374"))
                       + parse_line_history(lines_of("384"))
                       + parse_line_history(lines_of("389")))
    # The 3.0 passes the results reports count and do not list, as of the
    # moment the report was printed -- its own date line
    for codes in (("373", "383"), ("388",)):   # 373 and 383 count alike
        code = next((c for c in codes if lines_of(c)), None)
        taken = parse_when((lines_of(code) or [""])[0]) if code else None
        if taken is None:
            continue
        for (kind, number), counts in parse_line_counts(
                lines_of(code)).items():
            last = max((r.started for r in results
                        if (r.kind, r.device, r.rate_key)
                        == (kind, number, "gross")), default=None)
            history_results += gross_passes(kind, number, counts, last,
                                            taken)
    for code in ("373", "388"):
        for (kind, number), counts in parse_no_vent(lines_of(code)).items():
            console.lines.line(kind, number).no_vent_seed = counts
    # the measurements behind the 3.0, MID, 0.20 and 0.10 DIAG printouts
    diagnosed = 0
    for code, (kind, store, leg) in LINE_DIAGNOSTICS.items():
        for number, rows in parse_line_diagnostic(code,
                                                  lines_of(code)).items():
            if rows:
                getattr(console.lines.line(kind, number), store)[leg] = rows
                diagnosed += len(rows)
    if diagnosed:
        applied.append(f"{diagnosed} line diagnostic measurement(s)")
    seen = set()
    for result in sorted(history_results + results, key=lambda r: r.started):
        key = (result.kind, result.device, result.rate_key, result.started)
        if key in seen:
            continue
        seen.add(key)
        console.leaks.history.setdefault(
            (result.kind, result.device), []).append(result)
    # the newest at each rate is the one a results screen shows; a report
    # lists newest first, so the last one read is the OLDEST
    for result in sorted(results, key=lambda r: r.started):
        console.leaks.results.setdefault(
            (result.kind, result.device), {})[result.rate_key] = result
    if results or history_results:
        applied.append(f"{len(seen)} leak test result(s)")

    csld = parse_csld(lines_of("251"))
    for tank, (result, when) in csld.items():
        console.csld.results[tank] = (result, when)
    if csld:
        applied.append(f"CSLD results for {len(csld)} tank(s)")

    # What was standing. Most of it follows from the state above -- a fuel
    # alarm from the sensor, a delivery needed from the level -- and what
    # does not is posted, the way a test result is, so the console shows
    # what the site showed until somebody clears it.
    standing, missed = parse_alarm_rows(lines_of("113"), active=True,
                                        date_format=date_format)
    # A standing PLLD OPEN ALARM (21/06) is its line's transducer reading
    # open, which is hardware: the bench TLS-350's Q1 had one standing in its
    # snapshot, taken with the line off, and switched on two days later its
    # tests read TEST ABORTED as an open transducer's do. Seeded without it,
    # the line read a pressure and passed (2026-09-24).
    for row in standing:
        if row["aa"] == "21" and row["nn"] == "06" and row["tt"].isdigit():
            console.lines.line("plld", int(row["tt"])).transducer = "open"
    live = set(console.conditions())
    posted = []
    for row in standing:
        record = row["aa"] + row["nn"] + row["tt"]
        if record not in live:
            console.post(row["aa"], row["nn"], int(row["tt"]))
            posted.append(record)
        if row["at"] and not any(r["aa"] + r["nn"] + r["tt"] == record
                                 and r["state"] == "02"
                                 for r in console.alarm_log):
            console.alarm_log.insert(0, dict(row))
            console.keep_device_history(row)
    if standing:
        applied.append(f"{len(standing)} standing alarm(s)"
                       + (f", {len(posted)} posted" if posted else ""))
    skipped += [f"standing alarm not recognised: {u}" for u in missed]

    # The site's alarms are ALREADY in its history, so the first look must
    # not log them again at this console's own time as though they had just
    # happened. Only what the site was SHOWING is primed: a condition the
    # site was still holding in an Appendix A delay -- a high water the
    # filter had not let through yet -- starts its delay here instead.
    # Without a 113 there is no telling, and everything is taken as shown.
    shown = ({r["aa"] + r["nn"] + r["tt"] for r in standing}
             if lines_of("113") else None)
    stamp = time.mktime(console.now()) if now is None else now
    seen = set()
    for record in console.conditions():
        if shown is not None and record not in shown:
            continue
        if console.alarm_filter(record, stamp) is not None:
            console._filtered[record] = {"since": stamp, "on": True}
        seen.add(record)
    console._seen = seen

    # A line the status report shows testing carries on testing, so it
    # holds the tester and the other lines read TEST COMPLETE, as the
    # site's did. Before this every repetitive line started at once.
    for code in ("381", "386"):
        for (kind, number), rate_key in parse_line_status(
                lines_of(code)).items():
            if console.has(kind):
                console.lines.resume(kind, number, rate_key)
                applied.append(f"{'Q' if kind == 'plld' else 'W'}{number} "
                               f"testing at {_LINE_RATE_WORD[rate_key]} "
                               "gal/hr, as the site was")

    # a site that was working when its backup was taken has its tank checks
    # armed, as the bench TLS-350 has (FIDELITY S35)
    console.tank_checks = True
    console.site_backup = {
        "source": os.path.basename(path), "loaded": time.time(),
        "header": head, "posted": posted,
        "blocks": [block_json(b) for b in blocks]}
    console.save()
    return {"values": values, "reports": len(blocks), "applied": applied,
            "skipped": skipped, "header": head}


def reports(console):
    """The blocks the last site backup brought in, as Blocks."""
    kept = getattr(console, "site_backup", None) or {}
    return [block_from_json(b) for b in kept.get("blocks") or []]


def clear_posted(console):
    """Take down every alarm the backup posted, and return how many.

    A posted alarm latches like any other, so clearing the post alone would
    leave it on the display until somebody acknowledged it. These were
    never the simulator's alarms to begin with: they go all the way.
    """
    kept = getattr(console, "site_backup", None) or {}
    posted = list(kept.get("posted") or [])
    for record in posted:
        console.clear_posted(record[:2], record[2:4], int(record[4:6]))
        console.latched.discard(record)
        console.acked.discard(record)
    if kept:
        kept["posted"] = []
    console.save()
    return len(posted)
