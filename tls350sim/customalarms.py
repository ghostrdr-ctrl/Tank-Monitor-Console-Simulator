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
"""The custom alarm labels, 5BE and 5BF, as the bench TLS-350 keeps them.

Measured on 2026-10-08 (`bench-2026-10-08-cold/transcripts/customset.jsonl`),
custom alarms switched on at 5BD and PAPER OUT given the label `KPM` at the
keypad:

- `S5BE00` AA NN f + a 19 character label, f=1, ADDS that alarm with that
  label and all four outputs on; f=0 REMOVES it from both lists.
- `S5BF00` AA NN TT f l p b d + the label sets an entry's outputs:
  `S5BF0001020010100PRINTER TEST` left PRINTER ERROR printing and nothing
  else.
- `i5BE00` is a hex count, then AA NN and the 19 character label per entry;
  `i5BF00` the same with TT and the four flags l p b d between them:
  `020101001111KPM                0102000100PRINTER TEST       `.
- The display reports group under the category, ` SYSTEM ALARMS     : `,
  name the alarm on its own line two in, and its label three in; 5BF adds
  `ALL:` before the label and the four outputs one to a line.

Every Set answers the report of its own code. The store is i5BF's own
entries, `S5BF00`, so each of the four reports is drawn from one place. A
device other than 00 is not measured: it is drawn by number.
"""
import re

from . import alarmgroups

KEY = "S5BF00"
LABEL = 19
ENTRY = 4 + 2 + 4 + LABEL          # AANN TT lpbd label
OUTPUTS = ("LCD  ", "PRINT", "BEEP ", "LED  ")


def entries(console):
    """[(aann, tt, flags, label)] in the store's order."""
    raw = console.values.get(KEY) or ""
    out = []
    for at in range(0, len(raw) - ENTRY + 1, ENTRY):
        one = raw[at:at + ENTRY]
        out.append((one[:4], one[4:6], one[6:10], one[10:]))
    return out


def _store(console, rows):
    rows = sorted(rows, key=lambda r: (r[0], r[1]))
    console.values[KEY] = "".join(
        aann + tt + flags + label[:LABEL].ljust(LABEL)
        for aann, tt, flags, label in rows)
    console.save()


def set_label(console, data):
    """S5BE00 AANN f label. False if the data is not that shape."""
    if len(data) < 5 or not data[:4].isdigit() or data[4] not in "01":
        return False
    aann, on, label = data[:4], data[4], data[5:5 + LABEL]
    rows = [r for r in entries(console) if r[0] != aann]
    if on == "1":
        rows.append((aann, "00", "1111", label))
    _store(console, rows)
    return True


def set_outputs(console, data):
    """S5BF00 AANN TT f lpbd label. False if the data is not that shape.

    An output flag that is not `1` is off: `S5BF000304031111SUMP 3 OUT`,
    a character short, put the `S` in the LED flag and the bench stored
    LED DISABLED and the label `UMP 3 OUT` (2026-10-08). A device number
    other than 00 makes an entry of its own, for that device alone."""
    if len(data) < 11 or not data[:6].isdigit() or data[6] not in "01":
        return False
    aann, tt, on = data[:4], data[4:6], data[6]
    flags = "".join("1" if ch == "1" else "0" for ch in data[7:11])
    label = data[11:11 + LABEL]
    rows = [r for r in entries(console) if (r[0], r[1]) != (aann, tt)]
    if on == "1":
        rows.append((aann, tt, flags, label))
    _store(console, rows)
    return True


def label_for(console, aa, nn, tt):
    """The custom label an alarm is named by instead of its own name, or
    None. The bench, PAPER OUT labelled `KPM` with custom alarms on: its
    non-priority history filed `SYSTEM  KPM  ALARM 1-16-06 10:04AM` where
    PRINTER ERROR beside it kept its name, and the glass showed `KPM`
    when the paper ran out (2026-10-08)."""
    if not (console.values.get("S5BD00") or "").strip().endswith("1"):
        return None
    found = _entry(console, aa, nn, tt)
    return (found[3].strip() or None) if found else None


def _entry(console, aa, nn, tt):
    """The entry an alarm on that device answers to: its own device's
    first, else the all-devices one."""
    if not (console.values.get("S5BD00") or "").strip().endswith("1"):
        return None
    rows = [r for r in entries(console) if r[0] == aa + nn]
    for row in rows:
        if row[1] == tt and tt != "00":
            return row
    for row in rows:
        if row[1] == "00":
            return row
    return None


def shown_on_lcd(console, aa, nn, tt):
    """Is the alarm in the display's rotation? An entry with its LCD
    output off is not: the bench's `L 3:SUMP 3 OUT` never came round in
    six reads of the glass with LCD off, and came straight back with it
    on, while I10100 listed it throughout (2026-10-08)."""
    found = _entry(console, aa, nn, tt)
    return not found or found[2][:1] == "1"


# The keypad's CUSTOM ALARMS branch (`ca_*` console settings), which edits
# the same list the wire's 5BE and 5BF do. Walked on the bench on 2026-10-08
# (`bench-2026-10-08-cold/transcripts/menuwalk3.jsonl`) and 2026-10-09
# (`bench-2026-10-09/glass.jsonl`), read back through 5BE and 5BF after:
#
# - A category's switch, `SYSTEM ALARMS     : NO`, opens the category for
#   this walk and stores nothing. It reads NO every time the walk comes to
#   it, with alarms set under it or not, and leaving it NO leaves them.
# - A system alarm's switch, `PAPER OUT  :NO`, set YES opens `ALL: PAPER
#   OUT` over `LBL:`. A label entered makes the entry, all four outputs on,
#   and the four outputs follow under `ALL:` and the label. STEP past an
#   empty `LBL:` and the alarm is NO again, the outputs never shown.
# - A liquid alarm's switch has three words, NO SENSORS, ALL SENSORS and
#   SINGLE SRS. ALL SENSORS is the system alarm's walk, an entry for device
#   00. SINGLE SRS opens one screen, `L 1 OPEN      :  NO`, which
#   TANK/SENSOR walks through all eight inputs, wired or not. A sensor set
#   YES walks `L 2: SENSOR OUT ALARM` over `LBL:` and its outputs under
#   `L 2:` and the label, an entry for that device. Off the screen with no
#   sensor set, the alarm is NO SENSORS again.
# - The keypad takes ten characters of a label, `U R AWESOM`; the wire
#   takes nineteen.
OUTPUT_KEYS = ("lcd", "prt", "bep", "led")

#: the categories the branch offers a switch for, and the alarms each
#: walks, in the bench's order: SYSTEM's eleven, the liquid card's eight
#: and the PLLD board's fourteen
KEYPAD_GROUPS = {"01": ("01", "02", "04", "05", "06", "07", "10", "11", "15",
                        "16", "17"),
                 "03": ("02", "03", "04", "05", "06", "07", "08", "09"),
                 "21": ("01", "02", "03", "04", "05", "06", "08", "11", "12",
                        "13", "14", "16", "17", "18")}

#: the categories whose alarms are per device, and their three words: none,
#: every device (an entry for device 00) and one (an entry per device)
PER_DEVICE = {"03": ("NO SENSORS", "ALL SENSORS", "SINGLE SRS"),
              "21": ("NO LINES", "ALL LINES", "SINGLE LINE")}

#: what the keypad takes of a label
KEYPAD_LABEL = 10


def panel_keys():
    """Every `ca_*` setting the branch reads and writes, by name."""
    out = set()
    for aa, alarms in KEYPAD_GROUPS.items():
        out.add(f"ca_g{aa}")
        for nn in alarms:
            base = f"ca_{aa}{nn}"
            out.update({base, base + "_set", base + "_lbl"})
            out.update(f"{base}_{part}" for part in OUTPUT_KEYS)
            if aa in PER_DEVICE:
                out.update({base + "_s", base + "_dset", base + "_dlbl"})
                out.update(f"{base}_d{part}" for part in OUTPUT_KEYS)
    return out


def _state(console):
    """The walk's own state, which no report stores: which categories are
    open and which alarms are set YES with no label yet."""
    if not hasattr(console, "ca_walk"):
        console.ca_walk = {"open": set(), "pending": set()}
    return console.ca_walk


def walked(console, key, device=0, came=""):
    """The panel has arrived on a screen. A category switch reads NO again
    and closes its category; an alarm set YES and left without a label is
    dropped once the walk is off that alarm's screens.

    Answers the switch to go back to, or None: the bench, off an empty
    `LBL:`, came back to the alarm's own switch reading NO."""
    st = _state(console)
    m = re.match(r"^ca_g(\d\d)$", key or "")
    if m:
        st["open"].discard(m.group(1))
        st["pending"].clear()
        return None
    m = re.match(r"^ca_(\d{4})", key or "")
    here = m.group(1) if m else None
    dropped = {p for p in st["pending"] if p[0] != here}
    st["pending"] -= dropped
    m = re.match(r"^ca_(\d{4})_lbl$", came or "")
    if m and (m.group(1), "00") in dropped:
        return f"ca_{m.group(1)}"
    return None


def _row(console, aann, tt):
    return next((r for r in entries(console)
                 if r[0] == aann and r[1] == tt), None)


def panel_setting(console, key, device=0):
    """A `ca_*` setting's value, off the list and the walk."""
    st = _state(console)
    m = re.match(r"^ca_g(\d\d)$", key)
    if m:
        return "YES" if m.group(1) in st["open"] else "NO"
    m = re.match(r"^ca_(\d{4})(?:_(set|lbl|s|dset|dlbl|d?(?:lcd|prt|bep|led)))?$",
                 key)
    if not m:
        return ""
    aann, part = m.group(1), m.group(2)
    tt = f"{int(device):02d}" if part and (part.startswith("d")
                                         or part == "s") else "00"
    row = _row(console, aann, tt)
    if part is None:
        if aann[:2] in PER_DEVICE:
            none, every, one = PER_DEVICE[aann[:2]]
            if row or (aann, "00") in st["pending"]:
                return every
            if (any(r[0] == aann and r[1] != "00" for r in entries(console))
                    or (aann, "single") in st["pending"]):
                return one
            return none
        return "YES" if row or (aann, "00") in st["pending"] else "NO"
    if part == "s":
        return "YES" if row or (aann, tt) in st["pending"] else "NO"
    if part in ("set", "dset"):
        return "YES" if row else "NO"
    if part in ("lbl", "dlbl"):
        return row[3].strip() if row else ""
    if row is None:
        return "YES"
    return "YES" if row[2][OUTPUT_KEYS.index(part.lstrip("d"))] == "1" \
        else "NO"


def set_panel_setting(console, key, value, device=0):
    """Write a `ca_*` setting into the list or the walk; answer what it
    reads now."""
    value = str(value or "")
    st = _state(console)
    m = re.match(r"^ca_g(\d\d)$", key)
    if m:
        if value == "YES":
            st["open"].add(m.group(1))
        else:
            st["open"].discard(m.group(1))
        return panel_setting(console, key)
    m = re.match(r"^ca_(\d{4})(?:_(lbl|s|dlbl|d?(?:lcd|prt|bep|led)))?$", key)
    if not m:
        return value
    aann, part = m.group(1), m.group(2)
    tt = f"{int(device):02d}" if part and (part.startswith("d")
                                         or part == "s") else "00"
    rows = entries(console)
    if part is None:
        # the alarm's own switch: YES waits for a label, NO takes it off
        st["pending"] = {p for p in st["pending"] if p[0] != aann}
        words = PER_DEVICE.get(aann[:2], ("NO", "YES", None))
        if value == words[1]:
            st["pending"].add((aann, "00"))
        elif value and value == words[2]:
            st["pending"].add((aann, "single"))
        else:
            _store(console, [r for r in rows if r[0] != aann])
        return panel_setting(console, key)
    if part == "s":
        st["pending"].discard((aann, tt))
        if value == "YES":
            st["pending"].add((aann, tt))
        else:
            _store(console, [r for r in rows if (r[0], r[1]) != (aann, tt)])
        return panel_setting(console, key, device)
    row = _row(console, aann, tt)
    rest = [r for r in rows if (r[0], r[1]) != (aann, tt)]
    if part in ("lbl", "dlbl"):
        label = value[:KEYPAD_LABEL]
        st["pending"].discard((aann, tt))
        if label.strip():
            rest.append((aann, tt, row[2] if row else "1111", label))
        _store(console, rest)
        return panel_setting(console, key, device)
    if row is None:
        return panel_setting(console, key, device)
    at = OUTPUT_KEYS.index(part.lstrip("d"))
    flags = row[2][:at] + ("1" if value == "YES" else "0") + row[2][at + 1:]
    _store(console, rest + [(aann, tt, flags, row[3])])
    return panel_setting(console, key, device)


def packed(console, tok):
    """i5BE00 / i5BF00's data field."""
    rows = entries(console)
    if tok == "5BE":
        return f"{len(rows):02X}" + "".join(
            aann + label for aann, _tt, _f, label in rows)
    return f"{len(rows):02X}" + "".join(
        aann + tt + flags + label for aann, tt, flags, label in rows)


def _category(aa):
    if aa == "01":
        return "SYSTEM ALARMS"
    for _key, name, cat in alarmgroups.GROUPS:
        if cat == aa:
            return name
    return f"CATEGORY {aa}"


def _device(aa, tt):
    """`L 3:` for one device, as the alarm display writes it."""
    from .console import STATUS_DEVICE_CODE
    letter = STATUS_DEVICE_CODE.get(aa, "")
    return f"{letter} {int(tt)}:" if letter else f"{int(tt)}:"


def display(console, tok):
    """I5BE00 / I5BF00's lines under the stamp. A second category opens
    after a blank line; an entry for one device is headed `L 3:` with the
    label hard against it, in both reports (2026-10-08)."""
    from .console import STATUS_TYPES
    rows = ["", "CUSTOM ALARMS".ljust(24), ""]
    last = None
    for aann, tt, flags, label in entries(console):
        aa, nn = aann[:2], aann[2:]
        if aa != last:
            if last is not None:
                rows.append("")
            rows.append(" " + _category(aa).ljust(18) + ": ")
            last = aa
        rows.append("  " + (STATUS_TYPES.get(aa, {}).get(nn)
                            or f"ALARM {aann}").upper())
        if tt != "00":
            # the row is 27 wide either way: `   L 3:` and the label held to
            # twenty, against `   ALL: ` and nineteen
            rows.append("   " + _device(aa, tt) + label.ljust(LABEL + 1))
            if tok == "5BE":
                continue
        elif tok == "5BE":
            rows.append("   " + label.ljust(LABEL))
            continue
        else:
            rows.append("   ALL: " + label.ljust(LABEL))
        rows += [f"   {word}: " + ("ENABLED" if bit == "1" else "DISABLED")
                 for word, bit in zip(OUTPUTS, flags)]
    return rows + [""]
