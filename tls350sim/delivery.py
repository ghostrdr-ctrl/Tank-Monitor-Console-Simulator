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
from .clock import clock_date, clock_words

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
    return {"at": when, "volume": volume,
            "tc": console.tc_volume(tank),
            "water": water,
            "temp": console.product_temperature(tank),
            "height": console.height_at(tank, volume)}


def rewind(console, tank, shot, volume):
    """The same snapshot as it was at an EARLIER volume.

    A rise is noticed after it has happened, so the start of a delivery has
    to be reconstructed: the level was `volume` a moment ago and the reading
    taken now is of the risen tank. The temperature and the water are what
    they were; the volume, its temperature-compensated twin and the HEIGHT
    are not.

    This used to be three lines that overwrote `shot["volume"]` and then
    divided by it, so the scaling cancelled and the start TC came out as the
    TC of the volume AFTER the rise -- which is why every delivery the bench
    could make reported a TC increase of exactly zero. The height was never
    corrected at all. See FIDELITY Y1.
    """
    risen = shot.get("volume") or 0.0
    shot["tc"] = (shot["tc"] / risen * volume) if risen else volume
    shot["volume"] = volume
    shot["height"] = console.height_at(tank, volume)
    return shot


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
        self._pending = {}     # tank -> one not yet big enough to be a drop
        self._last = {}        # tank -> (volume, when it stopped rising)
        self._rose = {}        # tank -> when the level last went UP
        self._sets = {}        # tank -> what its siphon set held last look

    # ---- watching ----------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        for tank in sorted(self.c.tank_level):
            self._watch(tank, now)

    def _set_total(self, tank):
        """What the tank's siphon set holds altogether.

        A siphon carries fuel BETWEEN the tanks of a set, so one of them
        rising is not by itself product arriving at the site: a set finding
        its own level fills the low tank out of the high one. Nothing
        arrives unless the SET's total rises, and 576013-610 p.28-2 is the
        same reading from the report side -- "Reconciliation Reports are
        generated as a single product report for a manifolded set". See
        FIDELITY G11.
        """
        return sum((self.c.tank_level.get(one) or {}).get("volume", 0.0)
                   for one in self.c.siphon_set(tank))

    def _watch(self, tank, now):
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        seen, since = self._last.get(tank, (volume, now))
        running = self.running.get(tank)
        total, held_total = self._set_total(tank), self._sets.get(tank)
        self._sets[tank] = total

        if volume > seen + 0.05:                    # the level is going up
            if not (held_total is None or total > held_total + 0.05
                    or running is not None or tank in self._pending):
                # The SET is not gaining, so this is a siphon finding its
                # own level rather than product arriving: the fuel filling
                # this tank came out of its partner. The reading is still
                # BOOKED -- refusing the delivery and leaving `_last` where
                # it was makes the next real drop measure itself from a
                # level the tank left hours ago.
                self._last[tank] = (volume, now)
                return
            # A fill that has not yet reached the threshold is HELD, not
            # thrown away and rebuilt from the last tick. Rebuilding it made
            # the threshold a per-TICK one -- 25 gallons in a 700 ms poll,
            # about 2,140 gallons a minute -- so a real tanker dropping 200
            # to 400 gallons a minute was invisible. See FIDELITY Y2.
            running = running or self._pending.get(tank)
            if running is None:
                # the delivery started from where the tank was before it moved
                start = rewind(self.c, tank,
                               snapshot(self.c, tank, since), seen)
                running = Delivery(tank, start)
                self._pending[tank] = running
            self._last[tank] = (volume, now)
            self._rose[tank] = now
            if volume - running.start["volume"] >= START_GALLONS:
                self.running[tank] = running        # big enough to be a drop
            return

        if volume < seen - 0.05:                    # dispensing, or a drain
            self._last[tank] = (volume, now)
            held = running or self._pending.get(tank)
            if held is not None and volume <= held.start["volume"]:
                # it all went back out again, so there was no delivery
                self.running.pop(tank, None)
                self._pending.pop(tank, None)
                self._rose.pop(tank, None)
                return
        else:
            self._last[tank] = (seen, since)
        if running is None:
            return
        # "a delay time between the completion of a bulk delivery and the
        # Delivery Increase Report": timed from the last RISE, because a
        # forecourt goes on selling while the tanker is still on the ground,
        # and that dispensing is what the adjusted report accounts for.
        # `settling_delay` rather than the setting: there is a four-minute
        # floor under it that no setting turns off.
        delay = self.c.settling_delay(tank) * 60.0
        if now - self._rose.get(tank, since) >= delay:
            self._finish(tank, now)

    def _finish(self, tank, now):
        run = self.running.pop(tank, None)
        self._pending.pop(tank, None)
        self._rose.pop(tank, None)
        if run is None:
            return
        run.end = snapshot(self.c, tank, now)
        if run.amount < START_GALLONS:
            return
        if (self.c.service_session() is not None
                and self.c.delivery_override()):
            # "If 'Delivery Override' is enabled then any deliveries that
            # occur when the TLS is in a 'Service Notice' session will not
            # go into the standard delivery history or BIR delivery
            # history" -- 576013-623 Rev AN p.5-27. That is what the whole
            # feature is for: a tanker that arrives while a technician is
            # dropping fuel down a fill pipe to test a probe is not a
            # delivery, and recording it is what breaks the reconciliation.
            # The drop still happened and the tank still holds it; it is the
            # two HISTORIES it stays out of. See FIDELITY D9.
            return
        # A truck the bench sent hands over its ticket now, against the
        # record the console has just written for the drop it watched. A
        # float somebody dragged has no driver and no paperwork.
        ticket, bol = self.c.drops.claim_ticket(tank)
        if ticket is not None:
            run.ticket, run.bol = float(ticket), bol
        self.records.setdefault(tank, []).insert(0, run)
        del self.records[tank][10:]
        self.c.delivered(tank, run)

    def book(self, tank):
        """Take the level as it stands, without a word.

        Fuel that leaves by a route the console cannot see -- water pumped
        out, a stick reading, theft -- is what 576013-818 12-3 lists as
        the causes of lost volume, and none of them is a delivery. So the
        bench moves the level and tells the watcher to start looking from
        where it now is: whatever the reconciliation makes of the gap is
        the reconciliation's business. See BENCH.md T5.
        """
        now = time.mktime(self.c.now())
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        self._last[tank] = (volume, now)
        self._sets[tank] = self._set_total(tank)
        self._pending.pop(tank, None)
        self._rose.pop(tank, None)
        self.running.pop(tank, None)

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
    # I202's column header, read off 576013-635 Rev AA p.63 by word position.
    # The sample is Courier at six points a character, so this is counted
    # rather than guessed:
    #
    #   INCREASE 0  DATE 11  / 16  TIME 18  GALLONS 35  TC 43  GALLONS 46
    #   WATER 54  TEMP 61  DEG 66  F 70  HEIGHT 73
    #
    # and the values under it right-align to 41, 52, 58, 70 and 78, with the
    # row's own label -- END:, START:, AMOUNT: -- right-aligned to 9 and the
    # stamp starting at 11.
    #
    # This console ran the whole header onto the end of the `T n:` line and
    # dropped `INCREASE   DATE / TIME` altogether. Because the headings rode
    # on a variable-length product label, the columns moved from tank to tank
    # and sat over nothing in particular. See FIDELITY S4.
    DELIVERY_HEAD = ("INCREASE   DATE / TIME             GALLONS TC GALLONS "
                     "WATER  TEMP DEG F  HEIGHT")
    DELIVERY_WIDTHS = (10, 11, 6, 12, 8)

    # 215 is the same report with mass and density in it, and its own sample
    # is on p.83 with its own columns -- GALLONS at 33, MASS 45, DENSITY 52,
    # WATER 60, TEMP 68, HEIGHT 74 -- so it does NOT reuse 202's. The two
    # reports look alike and are not the same widths, which is the trap this
    # family sets twice.
    MASS_HEAD = ("INCREASE   DATE / TIME           GALLONS     MASS"
                 "   DENSITY WATER   TEMP  HEIGHT")
    MASS_WIDTHS = (8, 9, 10, 7, 7, 8)

    @staticmethod
    def delivery_line(label, stamp="", values=(), widths=None):
        """One row of I202 or I215, in that report's own columns.

        The label is right-aligned to column 9 -- `END:`, `START:` and
        `AMOUNT:` all end there -- and the stamp starts at 11, on both.
        """
        cells = ""
        for width, text in zip(widths or Deliveries.DELIVERY_WIDTHS, values):
            cells += f"{text:>{width}s}"
        return f"{label + ':':>10s} {stamp:21.21s}{cells}".rstrip()

    def report(self, tanks, title="DELIVERY REPORT", most_recent=False):
        """I202 and I20C, in the columns the manual prints them."""
        out = [title, ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            out.append(f"T {tank}:{label}")
            out.append(self.DELIVERY_HEAD)
            records = self.records.get(tank) or []
            if not records:
                out.append("  NO DELIVERY DATA AVAILABLE")
            for record in (records[:1] if most_recent else records):
                for name, snap in (("END", record.end),
                                   ("START", record.start)):
                    if not snap:
                        continue
                    out.append(self.delivery_line(
                        name, clock_words(snap["at"]),
                        (f"{snap['volume']:.0f}", f"{snap['tc']:.0f}",
                         f"{snap['water']:.2f}", f"{snap['temp']:.2f}",
                         f"{snap['height']:.2f}")))
                out.append(self.delivery_line(
                    "AMOUNT", "", (f"{record.amount:.0f}",
                                   f"{record.tc_amount:.0f}")))
                out.append("")
        return chr(10).join(out)

    # I221's two header lines, read off 576013-635 Rev AA p.91 by word
    # position -- the sample is Courier at six points a character, so the
    # columns can be counted rather than guessed:
    #
    #   TICKET 24  GAUGE 33  DLVY 45  BEFORE 52  AFTER 60  EST DLVY 67
    #   VOLUME 24  VOLUME 33  VAR 46  TMP 53  TMP 61  TMP 69
    #
    # and the values right-align to 29, 39, 49, 57, 65 and 73 under them.
    # This console printed four columns of its own -- all three temperatures
    # missing, and a BOL column that belongs to 222 in their place -- with
    # the two header lines collapsed into one. See FIDELITY S3.
    TICKETED_HEAD = (
        " " * 24 + "TICKET   GAUGE" + " " * 7 + "DLVY   BEFORE  AFTER  "
        "EST DLVY",
        "DELIVERY END DATE" + " " * 7 + "VOLUME   VOLUME" + " " * 7
        + "VAR    TMP" + " " * 5 + "TMP" + " " * 5 + "TMP",
    )

    def delivered_temperature(self, record):
        """EST DLVY TMP: how warm the fuel that arrived must have been.

        Mixing what was in the tank with what went into it -- the tank held
        `before` gallons at the before temperature and holds `after` at the
        after temperature, so the difference came in at whatever makes those
        two balance. The manual's own three rows reproduce under it with
        opening volumes of 3445, 3374 and 4299 gallons on a tank taking five
        and six thousand at a time, which is what makes it the formula rather
        than a guess.
        """
        if not record.end or record.amount <= 0.0:
            return None
        before = record.start["volume"]
        after = record.end["volume"]
        return ((after * record.end["temp"] - before * record.start["temp"])
                / record.amount)

    def ticketed_report(self, tanks, previous=False):
        """I221, the ticketed delivery report, in the manual's own columns."""
        tc = (self.c.values.get("S51D00") or "").strip().endswith("1")
        out = [("PREVIOUS" if previous else "CURRENT")
               + " PERIOD TICKETED DELIVERY REPORT",
               "VOLUMES ARE " + ("TC" if tc else "STANDARD"), ""]
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            out.append(f"T {tank}:{label}")
            out.extend(self.TICKETED_HEAD)
            records = self.records.get(tank) or []
            if not records:
                out.append("  NO TICKETED DELIVERY DATA")
            for record in records:
                out.append(self._ticketed_line(record))
            out.append("")
        return chr(10).join(out)

    def _ticketed_line(self, record):
        """One row of I221, right-aligned to the sample's own columns.

        A value the console has not got is UNAVAIL rather than a number,
        which is the word this report already used for a gauged volume it
        cannot supply -- an inserted delivery has no gauge reading behind it
        and a delivery with no ticket has no variance.
        """
        when = clock_words(record.end["at"]) if record.end else ""
        ticket = ("UNAVAIL" if record.ticket is None
                  else f"{record.ticket:.1f}")
        gauge = "UNAVAIL" if record.inserted else f"{record.amount:.1f}"
        var = ("UNAVAIL" if record.variance() is None
               else f"{record.variance():.1f}")
        before = f"{record.start['temp']:.1f}" if record.start else ""
        after = f"{record.end['temp']:.1f}" if record.end else ""
        est = self.delivered_temperature(record)
        est = "" if est is None else f"{est:.1f}"
        return (f"{when:21.21s}{ticket:>9.9s}{gauge:>10.10s}{var:>10.10s}"
                f"{before:>8.8s}{after:>8.8s}{est:>8.8s}")

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


class Drop:
    """A tanker on the ground, dropping into one tank.

    `compartments` is what is still to come, in gallons, newest first;
    `gap` is the minutes between one compartment closing and the next
    opening, which is exactly the interval S610's DELIVERY DELAY exists to
    bridge -- "prevents generation of false reports during the intervals
    between multi-compartment drops to one tank".
    """

    def __init__(self, tank, compartments, rate, ticket=None, bol="",
                 gap=2.0):
        self.tank = tank
        self.compartments = [float(g) for g in compartments if float(g) > 0]
        self.rate = max(1.0, float(rate))          # gallons a minute
        self.ticket = ticket
        self.bol = bol
        self.gap = float(gap)
        self.total = sum(self.compartments)
        self.dropped = 0.0
        self.pause = 0.0                            # minutes of gap left

    @property
    def done(self):
        return not self.compartments and self.pause <= 0.0


class Drops:
    """The trucks the bench has sent, and what they are doing right now.

    A console is never told a tanker has arrived: it watches the level
    (`Deliveries`, above). So a truck on the bench does the one thing a
    truck does -- it puts fuel in the tank at a rate, over console time --
    and the watcher notices exactly as it would on a site. Dragging the
    float is a JUMP, and 577013-814 p.22 is explicit that a jump is not a
    delivery: "move the float 1-2 in/sec ... If you move the float too
    quickly the system may not register the delivery flag." See BENCH.md
    T1 and T2.
    """

    # a real drop is a few hundred gallons a minute; the manual's own
    # example on p.22 moves the float an inch or two a second
    RATE = 300.0

    def __init__(self, console):
        self.c = console
        self.running = {}      # tank -> Drop
        self._tickets = {}     # tank -> (ticket, bol) waiting for the record

    def start(self, tank, compartments, rate=None, ticket=None, bol="",
              gap=None):
        """Send a truck. `compartments` is a list of gallons, one per hose
        change; `ticket` is what the driver's paperwork says, or None.

        `gap` is the minutes between one compartment closing and the next
        opening; None takes `Drop`'s own default. There was no way to pass
        it at all, so the auto-tanker's documented four minute gap was
        silently two -- and with S610's DELIVERY DELAY set to 2 or 3 that
        is the difference between a multi-compartment load booking as one
        delivery and as several.
        """
        tank = int(tank)
        drop = (Drop(tank, compartments, rate or self.RATE, ticket, bol)
                if gap is None else
                Drop(tank, compartments, rate or self.RATE, ticket, bol,
                     gap))
        if not drop.compartments:
            return None
        self.running[tank] = drop
        return drop

    def stop(self, tank):
        """Send it away mid-drop. What is in the tank stays in the tank."""
        drop = self.running.pop(int(tank), None)
        if drop is not None and drop.ticket is not None:
            # the driver still hands over the paperwork he came with
            self._tickets[int(tank)] = (drop.ticket, drop.bol)
        return drop

    def tick(self, hours):
        """Pour, at each truck's own rate, for the console time that passed."""
        if hours <= 0:
            return
        minutes = hours * 60.0
        for tank, drop in list(self.running.items()):
            st = self.c.tank_level.get(tank)
            if st is None:
                self.running.pop(tank)
                continue
            left = minutes
            full = self.c.full_volume(tank)
            while left > 0 and not drop.done:
                if drop.pause > 0:
                    used = min(drop.pause, left)
                    drop.pause -= used
                    left -= used
                    continue
                can = drop.rate * left
                want = drop.compartments[0]
                pour = min(can, want)
                room = max(0.0, full - st.get("volume", 0.0))
                pour = min(pour, room)
                st["volume"] = st.get("volume", 0.0) + pour
                drop.dropped += pour
                drop.compartments[0] -= pour
                left -= pour / drop.rate if drop.rate else left
                if drop.compartments[0] <= 1e-6 or room - pour <= 1e-6:
                    drop.compartments.pop(0)
                    if drop.compartments:
                        drop.pause = drop.gap
                    else:
                        drop.compartments = []
                if room - pour <= 1e-6:
                    # the tank is full: the driver shuts the valve
                    drop.compartments = []
            if drop.done:
                self.running.pop(tank)
                if drop.ticket is not None:
                    self._tickets[tank] = (drop.ticket, drop.bol)

    def claim_ticket(self, tank):
        """The paperwork for the drop the watcher has just written up."""
        return self._tickets.pop(int(tank), (None, ""))

    def describe(self, tank):
        """One line for the bench: what the truck is doing on this tank."""
        drop = self.running.get(int(tank))
        if drop is None:
            return ""
        if drop.pause > 0:
            return f"next compartment in {drop.pause:.0f} min"
        return f"dropping  {drop.dropped:,.0f} of {drop.total:,.0f} gal"


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
        self._pending = {}     # tank -> one not yet big enough to be a load
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
            # Held across ticks, and its start snapshot rewound properly:
            # the same two defects as the delivery watcher above, copied.
            # See FIDELITY Y1 and Y2.
            running = running or self._pending.get(tank)
            if running is None:
                start = rewind(self.c, tank,
                               snapshot(self.c, tank, since), seen)
                running = Load(tank, self._next_number(tank, now), start)
                self._pending[tank] = running
            self._last[tank] = (volume, now)
            if running.start["volume"] - volume >= self.LOAD_GALLONS:
                self.running[tank] = running        # big enough to be a load
            return

        if volume > seen + 0.05:                    # a delivery, not a load
            self._last[tank] = (volume, now)
            self.running.pop(tank, None)
            self._pending.pop(tank, None)
            return

        self._last[tank] = (seen, since)
        if running is None:
            return
        # The SETTING and not the settling floor: 576013-939's four minutes
        # are stated for the delivery report -- "between the end of the
        # delivery and the printing of the report while the console waits for
        # the fuel level in the tank to stabilize" -- and no page on this
        # shelf says anything of the kind about a tanker load. The physics
        # would be the same and the citation is not, so this waits what it is
        # told to wait. See FIDELITY Y3.
        delay = self.c.delivery_delay(tank) * 60.0
        if now - since >= delay:
            self._finish(tank, now)

    def book(self, tank):
        """Take the level as it stands: not a load either."""
        now = time.mktime(self.c.now())
        volume = self.c.tank_level.get(tank, {}).get("volume", 0.0)
        self._last[tank] = (volume, now)
        self._pending.pop(tank, None)
        self.running.pop(tank, None)

    def _next_number(self, tank, now):
        """"The sequence number is reset to one at the beginning of each day"."""
        day = time.strftime("%Y%m%d", time.localtime(now))
        if self._day.get(tank) != day:
            self._day[tank] = day
            self.records[tank] = []
        return len(self.records.get(tank) or []) + 1

    def _finish(self, tank, now):
        run = self.running.pop(tank, None)
        self._pending.pop(tank, None)
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
        when = clock_date(record.end["at"], year=False)
        return (f"T {tank}: {when} #{record.number}",
                f"TOTAL = {record.total:8.0f} GALS")
