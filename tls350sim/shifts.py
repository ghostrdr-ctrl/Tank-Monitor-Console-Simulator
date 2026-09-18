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
"""Last-Shift Inventory: what each tank held when each shift began and ended.

576013-610 Rev AC chapter 8: "Last-Shift Inventory displays what the
inventory was in each tank for up to four shifts when the shift was closed."
576013-623 Rev AN p.5-4, Shift Start Times: "At each programmed time, the
system automatically prints a complete inventory report and stores it in
memory", and "At least one Shift Start Time must be entered to activate the
'Last Shift Inventory' feature."

It is not BIR's shift. BIR closes its shifts at the Auto Shift Closing
times, `S794`, in Reconciliation Setup, behind the BIR key; this is System
Setup's `S502`, and chapter 8 carries none of the "this is an option" notes
chapters 7, 10 and 28 open with. The panel read BIR's shift rows for these
screens, PRINT gave BIR's report, and the function was hidden on every
console without the key. FIDELITY O22.

What the pages leave open -- which record a running shift shows, what TC NET
CHANGE sums, and what a refused manual close looks like -- is UNKNOWNS A67.
"""
import time

SHIFTS = (1, 2, 3, 4)
# "This command can only be invoked once an hour", p.8-2
MANUAL_GAP = 3600.0
# "up to four shifts": the day the look-back for the running shift covers
DAY = 86400.0


class Shifts:
    """The shift running now, and the last closed occurrence of each."""

    def __init__(self, console):
        self.c = console
        # the occurrence running now: its shift, its starting inventory (None
        # where it began before this console was watching) and the delivery
        # adjustments entered while it runs
        self.open = None
        # shift -> {"start", "end", "adjust"}: the last one of it that closed
        self.closed = {}
        self._before = None
        self._manual = None

    # ---- the programme ------------------------------------------------------
    def hhmm(self, shift):
        from . import printer
        return printer._time_setting(self.c, f"S502{shift:02d}")

    def programmed(self):
        """The shifts with a start time, in order: `EE00` is DISABLED."""
        return [n for n in SHIFTS if self.hhmm(n)]

    def current(self, at=None):
        """The shift running at `at`: the one whose start time passed last."""
        order = self.programmed()
        if at is None and self.open is not None and self.open["shift"] in order:
            return self.open["shift"]
        from . import printer
        at = time.mktime(self.c.now()) if at is None else at
        best = None
        for n in order:
            for when in printer._occurrences(at - DAY, at, self.hhmm(n)):
                moment = time.mktime(when)
                if best is None or moment > best[0]:
                    best = (moment, n)
        return best[1] if best else None

    # ---- the clock ----------------------------------------------------------
    def snapshot(self, when):
        """Every programmed tank's inventory, as the report prints it."""
        c = self.c
        ninety = not (c.values.get("S56400") or "").strip().endswith("1")
        tanks = {}
        for n, (_label, full) in c.programmed_tanks().items():
            st = c.tank_level.get(n, {})
            vol = st.get("volume", 0.0)
            tanks[n] = {
                "volume": vol, "ullage": max(full - vol, 0.0),
                "pct": "90" if ninety else "95",
                "share": max(full * (0.90 if ninety else 0.95) - vol, 0.0),
                "tc": c.tc_volume(n), "water_vol": c.water_volume(n),
                "water": st.get("water", 0.0),
                "temp": c.product_temperature(n),
                "height": c.height_at(n, vol)}
        return {"at": when, "tanks": tanks}

    def tick(self):
        """Begin every shift whose start time fell in the window just gone."""
        from . import printer
        now = time.mktime(self.c.now())
        before, self._before = self._before, now
        if before is None:
            # The first look. A shift is already running and its start was
            # not seen, so it has no starting inventory -- but a delivery
            # adjustment entered now still belongs to it.
            here = self.current()
            if here is not None and self.open is None:
                self.open = {"shift": here, "start": None, "adjust": {}}
            return
        due = sorted((time.mktime(when), n) for n in self.programmed()
                     for when in printer._occurrences(before, now,
                                                      self.hhmm(n)))
        for when, n in due:
            self.begin(n, when)

    def begin(self, shift, when):
        """`shift` starts at `when`: the one running ends there, and both
        take the inventory as it stands -- "the current shift's 'Shift Ending
        Inv' and the next shift's 'Shift Starting Inv' info will be updated
        with the current inventory data", p.8-2."""
        snap = self.snapshot(when)
        if self.open is not None:
            was = self.open
            self.closed[was["shift"]] = {"start": was["start"], "end": snap,
                                         "adjust": dict(was["adjust"])}
        self.open = {"shift": shift, "start": snap, "adjust": {}}

    def close_now(self):
        """CLOSE CURRENT SHIFT: False where it is refused.

        "This command can only be invoked once an hour. A shift inventory
        report is printed and the next shift automatically begins when you
        manually close a shift", p.8-2.
        """
        order = self.programmed()
        now = time.mktime(self.c.now())
        if not order or (self._manual is not None
                         and now - self._manual < MANUAL_GAP):
            return False
        here = self.current()
        following = (order[(order.index(here) + 1) % len(order)]
                     if here in order else order[0])
        self._manual = now
        self.begin(following, now)
        return True

    # ---- what the screens and the paper read --------------------------------
    def adjustment(self, shift, tank):
        """DLVY ADJUSTMENT: the running occurrence's while it runs -- "enter
        this adjustment during the shift in which the delivery(ies)
        occurred" -- and the closed one's after."""
        if self.open is not None and self.open["shift"] == shift:
            return self.open["adjust"].get(tank, 0.0)
        record = self.closed.get(shift)
        return record["adjust"].get(tank, 0.0) if record else 0.0

    def set_adjustment(self, shift, tank, gallons):
        """"enter the amount of the delivery indicated on the slip ... (if
        multiple deliveries were made into the tank, enter the total amount
        from the tickets)": a total, so it replaces rather than adds."""
        if self.open is not None and self.open["shift"] == shift:
            self.open["adjust"][tank] = float(gallons)
            return True
        record = self.closed.get(shift)
        if record is None:
            return False
        record["adjust"][tank] = float(gallons)
        return True

    def figures(self, shift, tank):
        """The four readings and TC NET CHANGE, off the last closed shift.

        p.8-2: "Gross Change is the beginning shift inventory, minus the end
        shift inventory, plus any ticketed deliveries made during that
        shift." TC NET CHANGE is the same sum on the TC volumes -- the page
        names it and defines nothing, UNKNOWNS A67.
        """
        record = self.closed.get(shift)
        shown = self.adjustment(shift, tank)
        if record is None:
            start = (self.open or {}).get("start") if (
                self.open and self.open["shift"] == shift) else None
            opening = ((start or {}).get("tanks", {}).get(tank) or {}).get(
                "volume", 0.0)
            return {"opening": opening, "physical": 0.0,
                    "deliveries": shown, "gross": 0.0, "tc_net": 0.0}
        a = ((record["start"] or {}).get("tanks", {}).get(tank)) or {}
        b = record["end"]["tanks"].get(tank) or {}
        kept = record["adjust"].get(tank, 0.0)
        return {"opening": a.get("volume", 0.0),
                "physical": b.get("volume", 0.0),
                "deliveries": shown,
                "gross": a.get("volume", 0.0) - b.get("volume", 0.0) + kept,
                "tc_net": a.get("tc", 0.0) - b.get("tc", 0.0) + kept}

    def shown(self, what, shift, tank):
        """A reading as the glass draws it: whole gallons, no unit (O17)."""
        if shift is None:
            order = self.programmed()
            shift = self.current() or (order[0] if order else 1)
        value = self.figures(shift, tank).get(what, 0.0)
        return f"{value:.0f}"

    def start_of(self, shift):
        """The latest starting inventory of `shift`, running or closed."""
        if self.open is not None and self.open["shift"] == shift \
                and self.open["start"] is not None:
            return self.open["start"]
        record = self.closed.get(shift)
        return record["start"] if record else None

    def end_of(self, shift):
        record = self.closed.get(shift)
        return record["end"] if record else None
