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
"""What identifies one dispenser meter to this console.

576013-818 p.12-11 states it in one sentence: "The meter must be identified
by bus, slot, real FP, and real M." Not by its number. The serial
manual's own I7B100 sample is the proof of why -- meter 10 appears on seven
fueling positions there and goes to two different tanks:

    3  3  0  10  1        3  3  2  10  2
    3  3  1  10  1        3  3  3  10  2
    3  3  4  10  1        3  3  6  10  2
    3  3  5  10  1

A store keyed on the meter number alone holds ONE of those seven rows. This
console's did, so the manual's twenty-row sample, 576013-818's Example 3
(twelve rows over four positions) and its Example 5 (twenty-five over
twelve) were all unstorable. See FIDELITY G7.

The page also says what makes the four fields necessary and not merely
sufficient. A POS reports fueling positions 0-99 and meter numbers 0-99;
the console cross references them down to 36 logical positions and 6 logical
meters per position. "In addition, more than one DIM board is allowed, so it
is possible to have two POS terminals reporting the same FP and M numbers. A
number identifying each DIM board is added to the Real FP to ensure a unique
number." Bus and slot are which DIM board, so they are what keeps two
terminals' FP 1 apart.

Its own module because the console, BIR, the wire and the bench all need to
say the same thing about a meter, and console.py imports two of those.
"""
import collections

# "Bus 3: 01-06 / 3=Comm Bus, Bus 2: 09-16 / 2=Power Bus (MDIM)", 7B1's own
# note. A bench meter nobody has mapped over the wire has to come from
# SOMEWHERE, and the commonest DIM on a TLS-350 is an EDIM in the first comm
# bay slot -- which is also the board every worked command in 576013-818
# addresses, `S7B100 3 1 ...`.
DEFAULT_BUS = 3
DEFAULT_SLOT = 1

# "The console is limited to 6 meters (M) per FP" -- so a bare meter number,
# which is the only thing the bench and the presets know, lands two to a
# fueling position. That is the shape a two-hose dispenser has and the shape
# `bir.log_event` invented for its FP column before there was a key to read
# one off.
METERS_PER_POSITION = 2


class MeterId(collections.namedtuple("MeterId", "bus slot fp meter")):
    """One meter, identified the way 7B1 identifies it.

    A tuple, so it sorts the way the I7B100 report wants to print -- board,
    then position, then meter -- and hashes as a dict key without ceremony.
    """

    __slots__ = ()

    def __str__(self):
        """The form the saved state file holds, and nothing else reads."""
        return f"{self.bus}.{self.slot}.{self.fp}.{self.meter}"

    @classmethod
    def parse(cls, text):
        """Back off a state file, or `None` if it is not one of these."""
        parts = str(text).split(".")
        if len(parts) != 4:
            return None
        try:
            return cls(*(int(p) for p in parts))
        except ValueError:
            return None


def meter_key(what):
    """Normalise anything that names a meter to a `MeterId`.

    A bare number is under-specified -- it says which meter and not which
    board or which position -- so it gets the default DIM and the position
    two-to-a-dispenser puts it at. That is what the bench, the presets and
    every saved state file written before 7B1 could hold a position have,
    and it is what keeps `c.meters[1]` meaning what it always meant.
    """
    if isinstance(what, MeterId):
        return what
    if isinstance(what, tuple) and len(what) == 4:
        return MeterId(*(int(x) for x in what))
    if isinstance(what, str):
        parsed = MeterId.parse(what)
        if parsed is not None:
            return parsed
    number = int(what)
    return MeterId(DEFAULT_BUS, DEFAULT_SLOT,
                   (number + METERS_PER_POSITION - 1) // METERS_PER_POSITION,
                   number)


class MeterDict(dict):
    """A dict whose keys are meter identities, however they are written.

    Every store in this console that hangs off a meter -- the map, the
    bench's flow rates, BIR's running totals, its retirement stamps -- is
    one of these, so that the qualified key is what is actually STORED while
    a bare number still reaches the entry it has always reached.
    """

    def __init__(self, source=None):
        super().__init__()
        if source:
            self.update(source)

    def __setitem__(self, key, value):
        super().__setitem__(meter_key(key), value)

    def __getitem__(self, key):
        return super().__getitem__(meter_key(key))

    def __delitem__(self, key):
        super().__delitem__(meter_key(key))

    def __contains__(self, key):
        return super().__contains__(meter_key(key))

    def get(self, key, default=None):
        return super().get(meter_key(key), default)

    def pop(self, key, *default):
        return super().pop(meter_key(key), *default)

    def setdefault(self, key, default=None):
        return super().setdefault(meter_key(key), default)

    def update(self, source=None, **kw):
        items = (source.items() if hasattr(source, "items")
                 else (source or ()))
        for key, value in items:
            self[key] = value
        for key, value in kw.items():
            self[key] = value

    def as_json(self):
        """Keys a JSON object can hold: `"3.1.9.18"`, not a tuple."""
        return {str(key): value for key, value in self.items()}


def offset_key(what):
    """Normalise anything that names a meter to 7B4's shorter identity.

    "FF - Fueling Position, MM - Meter Number, TT - Tank Number": the
    individual calibration offset carries no bus and no slot, so it is keyed
    on the pair the command actually gives and applies to that position's
    meter on whichever DIM reports it.
    """
    if isinstance(what, tuple) and len(what) == 2:
        return (int(what[0]), int(what[1]))
    if isinstance(what, str) and what.count(".") == 1:
        fp, meter = what.split(".")
        return (int(fp), int(meter))
    key = meter_key(what)
    return (key.fp, key.meter)


def legacy_map_key(key, entry):
    """The key for a stored map entry, whichever era wrote it.

    Before the position was part of the key it was a FIELD of the entry --
    `{"bus": "3", "slot": 1, "fp": 18, ...}` under the bare meter number --
    so a state file from then knows exactly where its meters are and would
    otherwise have every one of them collapsed onto the default DIM. The
    fields are the key now, so they are read back into one.
    """
    if isinstance(entry, dict) and "fp" in entry:
        return MeterId(int(entry.get("bus", DEFAULT_BUS)),
                       int(entry.get("slot", DEFAULT_SLOT)),
                       int(entry["fp"]), int(key))
    return meter_key(key)


def legacy_offset_key(key, entry):
    """The same for 7B4's store, which held its fueling position too."""
    if isinstance(entry, dict) and "fp" in entry and "." not in str(key):
        return (int(entry["fp"]), int(key))
    return offset_key(key)
