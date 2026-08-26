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
"""Deliveries the console notices for itself.

Nobody tells a TLS-350 that a tanker has arrived. It watches the level, and
when the product starts going up it records where it started from; when the
level stops moving for the tank's programmed DELIVERY DELAY it decides the
drop is over and works out the increase.

That delay is what the setup manual says it is for: "a delay time between the
completion of a bulk delivery and the Delivery Increase Report. This feature
prevents generation of false reports during the intervals between
multi-compartment drops to one tank."

The record it keeps is function 202's: a start and an end, each with volume,
temperature-compensated volume, water, temperature and height.
"""
import time

from . import packed
from .clock import clock_words

# How much the level has to climb before the console calls it a delivery
# rather than noise. A tanker drop is thousands of gallons; a probe's own
# jitter is fractions of one.
START_GALLONS = 25.0

# What the operator's manual tells you to wait after one before leak testing:
# "not waiting 8 hours after a delivery to begin an In-Tank Leak Detect Test
# results in an invalid test".
QUIET_HOURS = 8.0


def snapshot(console, tank, when):
    """Everything the delivery report records at one moment."""
    st = console.tank_level.get(tank, {})
    volume, water = st.get("volume", 0.0), st.get("water", 0.0)
    full = console.full_volume(tank)
    diam = console.limit("607", tank) or 96.0
    return {"at": when, "volume": volume, "tc": volume * 0.998,
            "water": water, "temp": 55.0,
            "height": (volume / full if full else 0.0) * diam}


class Delivery:
    """One drop: where the tank started, where it finished."""

    def __init__(self, tank, start):
        self.tank = tank
        self.start = start
        self.end = None
        self.ticket = None        # what the driver's ticket said
        self.sold = 0.0           # dispensed while the drop was running
        self.bol = ""             # bill of lading
        self.inserted = False     # entered by hand rather than gauged

    @property
    def amount(self):
        if not self.end:
            return 0.0
        return max(self.end["volume"] - self.start["volume"], 0.0)

    @property
    def tc_amount(self):
        if not self.end:
            return 0.0
        return max(self.end["tc"] - self.start["tc"], 0.0)

    def variance(self):
        """Ticket against gauge, which is what a ticketed delivery is for."""
        if self.ticket is None or not self.end:
            return None
        return self.ticket - self.amount


class Deliveries:
    """What the console has seen go into each tank."""

    def __init__(self, console):
        self.c = console
        self.records = {}      # tank -> [Delivery], newest first
        self.running = {}      # tank -> Delivery in progress
        self._last = {}        # tank -> (volume, when it stopped rising)
        self._rose = {}        # tank -> when the level last went UP

    # ---- watching ----------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.tank_level):
            self._watch(tank, now)

    def _watch(self, tank, now):
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, since = self._last.get(tank, (volume, now))
        running = self.running.get(tank)

        if volume > seen + 0.05:                    # the level is going up
            if running is None:
                # the delivery started from where the tank was before it moved
                start = snapshot(self.c, tank, since)
                start["volume"], start["tc"] = seen, seen * 0.998
                running = Delivery(tank, start)
            self._last[tank] = (volume, now)
            self._rose[tank] = now
            if volume - running.start["volume"] >= START_GALLONS:
                self.running[tank] = running        # big enough to be a drop
            return

        if volume < seen - 0.05:                    # dispensing, or a drain
            self._last[tank] = (volume, now)
            if running is not None and volume <= running.start["volume"]:
                # it all went back out again, so there was no delivery
                self.running.pop(tank, None)
                self._rose.pop(tank, None)
                return
        else:
            self._last[tank] = (seen, since)
        if running is None:
            return
        # "a delay time between the completion of a bulk delivery and the
        # Delivery Increase Report": timed from the last RISE, because a
        # forecourt goes on selling while the tanker is still on the ground,
        # and that dispensing is what the adjusted report accounts for
        delay = self.c.delivery_delay(tank) * 60.0
        if now - self._rose.get(tank, since) >= delay:
            self._finish(tank, now)

    def _finish(self, tank, now):
        run = self.running.pop(tank, None)
        self._rose.pop(tank, None)
        if run is None:
            return
        run.end = snapshot(self.c, tank, now)
        if run.amount < START_GALLONS:
            return
        self.records.setdefault(tank, []).insert(0, run)
        del self.records[tank][10:]
        self.c.delivered(tank, run)

    def in_progress(self, tank):
        return self.running.get(tank)

    def last(self, tank):
        records = self.records.get(tank) or []
        return records[0] if records else None

    def since_last(self, tank, when):
        """Hours from the last delivery on that tank to `when`."""
        last = self.last(tank)
        if last is None or not last.end:
            return None
        return (when - last.end["at"]) / 3600.0

    def during(self, tank, start, end):
        """Did a delivery land in that window, or too soon before it?

        "A delivery occurred during the leak detect test" is one of the
        reasons the console gives for an invalid result, and so is starting
        one within eight hours of a drop.
        """
        for record in self.records.get(tank) or []:
            if record.end and start - QUIET_HOURS * 3600.0 <= record.end["at"] <= end:
                return True
        return bool(self.running.get(tank))

    def find(self, tank, stamp):
        """The delivery that ended at that YYMMDDHHmm, which is how S7B5
        addresses one."""
        for record in self.records.get(tank) or []:
            if record.end and time.strftime(
                    "%y%m%d%H%M", time.localtime(record.end["at"])) == stamp:
                return record
        return None

    def insert(self, tank, when, ticket, bol=""):
        """A delivery entered by hand, "if your console is down for
        maintenance when a delivery occurs"."""
        for record in self.records.get(tank) or []:
            if record.end and abs(record.end["at"] - when) < 60:
                return None                      # "INVALID INSERT"
        blank = {"at": when, "volume": 0.0, "tc": 0.0, "water": 0.0,
                 "temp": 0.0, "height": 0.0}
        record = Delivery(tank, dict(blank))
        record.end = dict(blank)
        record.ticket = float(ticket)
        record.bol = bol
        record.inserted = True
        self.records.setdefault(tank, []).insert(0, record)
        self.records[tank].sort(key=lambda r: r.end["at"], reverse=True)
        del self.records[tank][10:]
        return record

    def unticketed(self):
        """[(tank, record)] for deliveries nobody has put a ticket against.

        Only worth asking about when the site is running ticketed delivery,
        which is a System Setup flag.
        """
        if not (self.c.values.get("S51C00") or "").strip().endswith("1"):
            return []
        return [(tank, r) for tank, records in self.records.items()
                for r in records if r.ticket is None]

    def clear(self, tank=None):
        """S051, Clear In-Tank Delivery Reports."""
        if tank is None:
            n = sum(len(v) for v in self.records.values())
            self.records.clear()
            return n
        n = len(self.records.get(tank) or [])
        self.records.pop(tank, None)
        return n

    # ---- what the console shows and prints ----------------------------------
    def report(self, tanks, title="DELIVERY REPORT", most_recent=False):
        """I202 and I20C, in the columns the manual prints them."""
        out = [title, ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            out.append(f"T {tank}:{label}"
                       "        GALLONS TC GALLONS WATER TEMP DEG F HEIGHT")
            records = self.records.get(tank) or []
            if not records:
                out.append("  NO DELIVERY DATA AVAILABLE")
            for record in (records[:1] if most_recent else records):
                for name, snap in (("END", record.end), ("START", record.start)):
                    if not snap:
                        continue
                    when = clock_words(snap["at"])
                    out.append(f"  {name:>6}: {when:22s}"
                               f"{snap['volume']:8.0f}{snap['tc']:9.0f}"
                               f"{snap['water']:7.2f}{snap['temp']:8.2f}"
                               f"{snap['height']:7.2f}")
                out.append(f"  {'AMOUNT':>6}: {'':22s}"
                           f"{record.amount:8.0f}{record.tc_amount:9.0f}")
                out.append("")
        return chr(10).join(out)

    def ticketed_report(self, tanks, previous=False):
        """I221, the ticketed delivery report, in its own columns."""
        tc = (self.c.values.get("S51D00") or "").strip().endswith("1")
        out = [("PREVIOUS" if previous else "CURRENT")
               + " PERIOD TICKETED DELIVERY REPORT",
               "VOLUMES ARE " + ("TC" if tc else "STANDARD"), ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            out.append(f"T {tank}:{label}")
            out.append("DELIVERY END DATE        TICKET    GAUGE      VAR"
                       "   BOL")
            records = self.records.get(tank) or []
            if not records:
                out.append("  NO TICKETED DELIVERY DATA")
            for record in records:
                when = clock_words(record.end["at"])
                ticket = ("UNAVAIL" if record.ticket is None
                          else f"{record.ticket:.0f}")
                gauge = "UNAVAIL" if record.inserted else f"{record.amount:.0f}"
                var = ("" if record.variance() is None
                       else f"{record.variance():.0f}")
                out.append(f"{when:24s}{ticket:>8s}{gauge:>9s}{var:>8s}"
                           f"   {record.bol}")
            out.append("")
        return chr(10).join(out)

    def record_data(self, tanks):
        """I202 computer format: TT p dd start end NN then ten floats."""
        out = []
        for tank in tanks:
            records = [r for r in (self.records.get(tank) or []) if r.end]
            code = (self.c.text("603", tank) or " ")[:1] or " "
            out.append(f"{tank:02d}{code}{len(records):02d}")
            for record in records:
                out.append(time.strftime("%y%m%d%H%M",
                                         time.localtime(record.start["at"])))
                out.append(time.strftime("%y%m%d%H%M",
                                         time.localtime(record.end["at"])))
                out.append("0A")
                for value in (record.start["volume"], record.start["tc"],
                              record.start["water"], record.start["temp"],
                              record.end["volume"], record.end["tc"],
                              record.end["water"], record.end["temp"],
                              record.start["height"], record.end["height"]):
                    out.append(packed.hexfloat(value))
        return "".join(out)


class Load:
    """One tanker load: what came OUT of the tank into a road tanker."""

    def __init__(self, tank, number, start):
        self.tank = tank
        self.number = number
        self.start = start
        self.end = None

    @property
    def total(self):
        if not self.end:
            return 0.0
        return max(self.start["volume"] - self.end["volume"], 0.0)

    @property
    def tc_total(self):
        if not self.end:
            return 0.0
        return max(self.start["tc"] - self.end["tc"], 0.0)


class Loads:
    """Tanker Load Reports, which are deliveries the other way up.

    "Tanker Load Reports show the volume of fluid pumped from a tank to a road
    tanker. The volume of fuel pumped each time is referred to as a load. The
    system automatically assigns a sequence number in ascending order to each
    load. The sequence number is reset to one at the beginning of each day
    (12:00 am). Up to 40 loads per tank will be recorded in a day."
    """

    # A load is a bulk draw, not a car filling up: the manual's own sample is
    # 9422 gallons. This is well above dispensing and well above probe noise.
    LOAD_GALLONS = 500.0

    def __init__(self, console):
        self.c = console
        self.records = {}      # tank -> [Load], newest first
        self.running = {}      # tank -> Load in progress
        self._last = {}        # tank -> (volume, when it stopped falling)
        self._day = {}         # tank -> the day its numbering belongs to

    def enabled(self):
        """"Tanker Load Report is a key-enabled option."""
        return (self.c.values.get("S51300") or "").strip().endswith("1")

    # ---- watching ----------------------------------------------------------
    def tick(self):
        if not self.enabled():
            return
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.tank_level):
            self._watch(tank, now)

    def _watch(self, tank, now):
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, since = self._last.get(tank, (volume, now))
        running = self.running.get(tank)

        if volume < seen - 0.05:                    # the level is going down
            if running is None:
                start = snapshot(self.c, tank, since)
                start["volume"], start["tc"] = seen, seen * 0.998
                running = Load(tank, self._next_number(tank, now), start)
            self._last[tank] = (volume, now)
            if running.start["volume"] - volume >= self.LOAD_GALLONS:
                self.running[tank] = running        # big enough to be a load
            return

        if volume > seen + 0.05:                    # a delivery, not a load
            self._last[tank] = (volume, now)
            self.running.pop(tank, None)
            return

        self._last[tank] = (seen, since)
        if running is None:
            return
        delay = self.c.delivery_delay(tank) * 60.0
        if now - since >= delay:
            self._finish(tank, now)

    def _next_number(self, tank, now):
        """"The sequence number is reset to one at the beginning of each day"."""
        day = time.strftime("%Y%m%d", time.localtime(now))
        if self._day.get(tank) != day:
            self._day[tank] = day
            self.records[tank] = []
        return len(self.records.get(tank) or []) + 1

    def _finish(self, tank, now):
        run = self.running.pop(tank, None)
        if run is None:
            return
        run.end = snapshot(self.c, tank, now)
        if run.total < self.LOAD_GALLONS:
            return
        self.records.setdefault(tank, []).insert(0, run)
        # "Up to 40 loads per tank will be recorded in a day"
        del self.records[tank][40:]

    # ---- what the console shows --------------------------------------------
    def all(self, tank):
        return self.records.get(tank) or []

    def load(self, tank, index=0):
        records = self.all(tank)
        return records[index % len(records)] if records else None

    def screen(self, tank, index=0):
        """"T #: DATE #(LOAD NO.) / TOTAL = XXXX GALS"."""
        record = self.load(tank, index)
        if record is None:
            return f"T {tank}: NO LOAD DATA", "TOTAL =        0 GALS"
        when = time.strftime("%b %d", time.localtime(record.end["at"])).upper()
        return (f"T {tank}: {when} #{record.number}",
                f"TOTAL = {record.total:8.0f} GALS")
