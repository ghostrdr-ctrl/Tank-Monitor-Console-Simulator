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
"""A site's own day: cars arriving, nozzles lifting, tanks going down.

Nothing here is new physics. A car is a call to `Sales.start()` -- the same
door the bench's LIFT NOZZLE pill goes through -- so everything downstream
of `meter_flow` sees exactly what it sees for a nozzle lifted by hand: BIR
draws the tank and books the shift, the meter events table gets a start and
an end, the DIM is stamped as having reported, CSLD's `busy` reads the rate,
and the line's HANDLE goes up while the fuel is moving. The generator's
whole job is deciding WHEN, and out of which tank.

Two knobs, because the four stores a trainer asks for vary along two axes
and not one. VOLUME is how many cars a day; SHAPE is the hour they come in.
A truck stop is not a busy retail forecourt with the hours moved -- it is a
NIGHT shape at whatever volume it does -- and a site that never goes quiet
is a FLAT shape, which is the only one that can deny CSLD its idle time.

**Arrivals are Poisson, and that is the point.** A car every four minutes
never leaves a thirty minute hole, so a site paced on a fixed interval looks
identical to a quiet one from CSLD's end and the alarm this generator exists
to raise could never post. Real idle time is the exponential tail between
Poisson arrivals: a SLOW store finds plenty, a BUSY FLAT store finds none,
and where the boundary falls is the thing a technician is being trained to
recognise. 576013-818 p.11-10 is written about that boundary.

Determinism: this is the only module in the package that draws a random
number, and it draws every one of them from its own `random.Random`, seeded
and saved. Nothing else in the console has ever been random and the test
suite depends on that, so a run with a given seed puts the same gallons
through the same meters every time.

BENCH.md T12 (the pattern generator), and the deliveries T1 built the truck
for.
"""
import random
import time

from .meterid import MeterDict, meter_key


# ---------------------------------------------------------------------------
# how many, and when


# Cars a day. The manuals state no nozzle flow rate and no transaction
# size anywhere in eighty megabytes of them, so the only figure here with
# a citation behind it is the CEILING: 576013-818 Table 11-1 caps a
# 12,000 gallon gasoline tank at 200,000 gallons a month, which is about
# 6,600 a day, and says "Installations exceeding these limitations may not
# pass monthly tests". BUSY is a site pushing a three-tank forecourt at
# roughly that, which is precisely where NO CSLD IDLE TIME lives -- p.11-10
# cause 2, "Very high activity. Tank capacity or throughput specifications
# are exceeding CSLD specifications". The other three are fractions of it.
LEVELS = {"CLOSED": 0, "SLOW": 150, "MID": 550, "BUSY": 1400}
LEVEL_ORDER = ["CLOSED", "SLOW", "MID", "BUSY"]

# Relative weight per hour of the day, midnight first. Only the shape
# matters: the numbers are normalised against their own sum, so the volume
# above is what decides how many cars there are.
SHAPES = {
    # the commuter curve: a peak going to work and a bigger one coming home
    "DAY": [4, 2, 2, 2, 3, 8, 22, 55, 70, 48, 40, 42,
            52, 46, 44, 52, 70, 84, 66, 44, 30, 20, 12, 7],
    # a truck stop or a highway site: dead at noon, working all night
    "NIGHT": [72, 68, 60, 52, 48, 44, 36, 30, 24, 20, 18, 16,
              15, 15, 16, 18, 22, 28, 36, 46, 58, 66, 74, 76],
    # the site that never rests, and the only shape that denies CSLD its
    # idle time at every hour rather than only at the peak
    "FLAT": [38, 36, 35, 34, 34, 36, 40, 44, 46, 44, 42, 42,
             44, 44, 42, 44, 46, 48, 46, 44, 42, 40, 40, 39],
    # one broad hump in the middle of the day
    "WEEKEND": [8, 5, 4, 3, 3, 4, 8, 14, 24, 40, 58, 70,
                78, 80, 76, 70, 62, 52, 42, 34, 26, 20, 14, 10],
}
SHAPE_ORDER = ["DAY", "NIGHT", "FLAT", "WEEKEND"]
SHAPE_WORDS = {"DAY": "DAY-HEAVY", "NIGHT": "NIGHT-HEAVY",
               "FLAT": "FLAT (24 HOUR)", "WEEKEND": "WEEKEND"}


# ---------------------------------------------------------------------------
# what they buy


def family(label):
    """Which product a tank's label names, from the label alone.

    The console has no idea what a grade is: it knows a tank's PRODUCT
    LABEL (S602) and nothing else about what comes out of it. So the grade
    a site sells is read off the label the site programmed, which is how a
    technician reads it too. The order matters -- "PREMIUM UNLEADED"
    carries the word UNLEADED and is not regular.
    """
    s = (label or "").upper()
    if "DEF" in s or "UREA" in s:
        return "def"
    if "E-85" in s or "E85" in s or "FLEX" in s:
        return "e85"
    if "KERO" in s:
        return "kero"
    if "DIESEL" in s or "DSL" in s or "#2" in s:
        return "diesel"
    if "PREM" in s or "SUPER" in s or "ULTRA" in s:
        return "premium"
    if "PLUS" in s or "MID" in s or "SILVER" in s or "E15" in s:
        return "plus"
    return "regular"


FAMILY_WORDS = {"regular": "REGULAR", "plus": "MID-GRADE",
                "premium": "PREMIUM", "diesel": "DIESEL", "e85": "E-85",
                "def": "DEF", "kero": "KEROSENE"}

# What share of the cars want each grade, before the site's own tanks
# narrow it. Normalised over the families the site actually has, so a
# truck stop with no premium does not lose those customers -- they buy
# what is there.
MIX = {"regular": 60.0, "plus": 10.0, "premium": 12.0, "diesel": 15.0,
       "e85": 2.0, "def": 1.0, "kero": 0.5}

# Median gallons a sale, and the spread around it. A car takes ten or
# twelve; a tractor unit takes a hundred and takes it through a high flow
# nozzle, which is why the rate is a property of the grade too.
#
#   (median gallons, log spread, cap, gallons an hour at the nozzle)
#
# Ten gallons a minute is a modern retail nozzle's rated flow and is what
# `Sales.RATE` already assumes; a truck lane runs at twenty to forty.
SIZES = {
    "regular": (11.0, 0.45, 30.0, 600.0),
    "plus": (12.0, 0.45, 30.0, 600.0),
    "premium": (12.0, 0.45, 30.0, 600.0),
    "e85": (13.0, 0.45, 30.0, 600.0),
    "diesel": (40.0, 0.70, 180.0, 1500.0),
    "kero": (6.0, 0.60, 40.0, 400.0),
    "def": (3.0, 0.50, 15.0, 300.0),
}


# ---------------------------------------------------------------------------
# the tanker that comes when the tank gets low


class Day:
    """What one console day did, so far.

    A day of its own rather than a set of counters on the generator,
    because a car has to be counted against the day it ARRIVED in. At a
    real console's pace that is always the day the tick ended in; at
    36,000x one tick is seven hours, so a tick landing at 06:00 carries
    cars that arrived before midnight. The counters were zeroed at the
    top of that tick and everything it then generated was booked as
    today's -- so the previous day's final figures were destroyed unread,
    and up to ten hours of its cars were credited to a day they did not
    happen in. Measured: 23:30 read 838 cars and 16,261 gallons, and one
    seven hour tick to 06:30 replaced it with 327 cars, 27 of which had
    arrived the day before.
    """

    __slots__ = ("cars", "gallons", "balked", "blocked", "dry",
                 "deliveries")

    def __init__(self):
        self.cars = 0
        self.gallons = 0.0
        self.balked = 0          # cars that found every nozzle busy
        self.blocked = 0         # cars turned away by a shut-down line
        self.dry = 0             # cars that found the tank empty
        self.deliveries = 0


class Order:
    """A load ordered and not yet on the ground."""

    def __init__(self, tank, gallons, due):
        self.tank = int(tank)
        self.gallons = float(gallons)
        self.due = float(due)


class Traffic:
    """The forecourt's own timetable, and the truck it calls when it runs low.

    OFF is not the same as CLOSED. Off means the generator is not there at
    all and the bench behaves exactly as it did before it existed -- a
    meter sells whatever gal/h somebody typed into its card and nothing
    else. CLOSED means the site is modelled and shut: no cars, which is its
    own interesting state, because a shut site is a site where CSLD finds
    all the idle time it wants, every close happens the moment it is due,
    and the BDIM Transaction Alarm cannot tell a closed store from a dead
    card until the programmed delay runs out.
    """

    # a tanker compartment, and the gap before the driver couples the next
    # one -- the same shape the truck dialog builds by hand. 576013-623
    # p.7-26's DELIVERY DELAY is what the gap is measured against.
    COMPARTMENT = 3000.0
    COMPARTMENT_GAP = 4.0            # minutes
    DROP_RATE = 300.0                # gallons a minute down the fill riser

    def __init__(self, console, seed=20260910):
        self.c = console
        self.on = False
        self.level = "SLOW"
        self.shape = "DAY"
        # a site can be busier or quieter than any of the four words; the
        # words set this and the bench can then type over it
        self.cars_per_day = float(LEVELS["SLOW"])
        self.mix = dict(MIX)
        # the truck that comes on its own
        self.auto_deliver = True
        self.low_pct = 25.0          # order when the tank drops below this
        self.fill_pct = 90.0         # and fill it back to this
        self.lead_hours = 4.0        # how long the tanker takes to arrive
        self.orders = {}             # tank -> Order not yet delivered
        self.ticket = 480000         # the driver's paperwork, counting up
        self.seed = int(seed)
        self.rng = random.Random(self.seed)
        # what each console day has done, for the bench to show. Keyed
        # YYYYMMDD, and `cars`, `gallons` and the rest read today's.
        self.day = None
        self._days = {}

    # ---- the settings, as one blob the state file can hold -------------------
    def state(self):
        # a COPY of the mix: the blob handed out here is written to the
        # state file, and it used to alias the live dict, so anything that
        # held the blob held console state that went on changing under it
        return {"on": self.on, "level": self.level, "shape": self.shape,
                "cars_per_day": self.cars_per_day, "mix": dict(self.mix),
                "auto_deliver": self.auto_deliver, "low_pct": self.low_pct,
                "fill_pct": self.fill_pct, "lead_hours": self.lead_hours,
                "ticket": self.ticket, "seed": self.seed,
                "orders": [[o.tank, o.gallons, o.due]
                           for o in self.orders.values()]}

    def restore(self, blob):
        if not isinstance(blob, dict):
            return
        self.on = bool(blob.get("on", False))
        self.level = blob.get("level", self.level)
        self.shape = blob.get("shape", self.shape)
        self.cars_per_day = float(blob.get("cars_per_day",
                                           self.cars_per_day))
        mix = blob.get("mix")
        if isinstance(mix, dict):
            self.mix = {k: float(v) for k, v in mix.items()}
        self.auto_deliver = bool(blob.get("auto_deliver", True))
        self.low_pct = float(blob.get("low_pct", self.low_pct))
        self.fill_pct = float(blob.get("fill_pct", self.fill_pct))
        self.lead_hours = float(blob.get("lead_hours", self.lead_hours))
        self.ticket = int(blob.get("ticket", self.ticket))
        self.seed = int(blob.get("seed", self.seed))
        self.rng = random.Random(self.seed)
        self.orders = {}
        for row in blob.get("orders") or ():
            try:
                order = Order(row[0], row[1], row[2])
            except (IndexError, TypeError, ValueError):
                continue
            self.orders[order.tank] = order

    def set_level(self, level):
        """Pick one of the four words, and take its cars a day with it."""
        if level not in LEVELS:
            return
        self.level = level
        self.cars_per_day = float(LEVELS[level])

    def level_word(self):
        """Which of the four words this site IS running at, or None.

        The word and the number are two stores of one fact, and they
        drifted: the pills set both, the cars/day box sets only the
        number, and nothing put the word right afterwards. Click CLOSED,
        type 500 over it, and the site ran at 33 cars an hour with the
        CLOSED pill still lit and the header still reporting a shut shop.

        So the NUMBER is the fact, and the word is read back off it. A
        site between two of the words -- which the cars/day box exists to
        let you build -- has no word, and nothing is lit.
        """
        for name, cars in LEVELS.items():
            if abs(self.cars_per_day - float(cars)) < 0.5:
                return name
        return None

    # ---- why nothing is happening -------------------------------------------
    def can_sell(self):
        """Can a gallon actually LEAVE a tank on this console?

        `Bir._dispense` is the only thing on this bench that draws a tank
        down from a sale, and it runs only with the BIR key in the console
        and a DIM in the cage -- the bench models the fuel the console can
        account for, and "the fuel still flows at the site, but this
        console cannot see it, so the bench meters go quiet too".

        Which means a site with meters mapped and no BIR key is a site
        where `selling()` is full, a car can be sent, a handle goes up, and
        not one gallon moves. Measured: 313 cars and 5,963 gallons against
        a tank that never left 8,000. Counting a sale that did not happen
        is worse than not sending the car -- the header would report a
        day's trade that no screen, no report and no probe agrees with --
        so the car is not sent, and `blockers()` says why instead.
        """
        return (self.c.licensed("bir")
                and (self.c.has("edim") or self.c.has("mdim")))

    def blockers(self):
        """Why no car can buy anything, in words, with where to go fix it.

        The generator has one failure that looks exactly like success: it
        says RUNNING, the curve draws, the hour lights up, and no car ever
        arrives -- because `selling()` is empty and there was nothing for
        the arrivals to be spent on. A site with no DIM in the cage cannot
        have a meter, a console without BIR cannot have meter data at all,
        and either way a technician staring at the line screen waiting for
        HANDLE ON is waiting for something that was never going to come.

        So the reasons are named rather than left to be deduced. Each is
        (what is wrong, which bench view fixes it), ordered the way they
        have to be fixed: the card goes in before the meter is mapped, and
        the meter is mapped before it can be a blend's component.
        """
        c = self.c
        out = []
        if not c.licensed("bir"):
            out.append(("BIR is not installed, so no metered transaction "
                        "reaches this console at all.", "Modules"))
        if not (c.has("edim") or c.has("mdim")):
            out.append(("No DIM is fitted, so there is nothing to report a "
                        "meter. Fit an EDIM or MDIM.", "Modules"))
        elif not c.meters:
            out.append(("No meter is mapped to a tank. Map one under "
                        "Dispensers.", "Site"))
        empty = [m for m in sorted(c.blends) if not c.blend_meters(m)]
        if empty:
            out.append((f"{len(empty)} blended grade"
                        f"{'' if len(empty) == 1 else 's'} with no "
                        "components: a grade button the dispenser cannot "
                        "make, so nobody buys it.", "Traffic"))
        dead = [m for m in sorted(c.meters) if c.dispensing_blocked(m)]
        if dead and len(dead) == len(c.meters):
            out.append((f"Every one of the {len(dead)} mapped meters is "
                        "shut down, so a handle gets no pump.", "Traffic"))
        return out

    def idle_reason(self):
        """One line for the header: why the counters are all zero.

        None when there is something to sell and a car that could buy it,
        which is when the header has better things to say than this. Not
        every entry in `blockers()` stops the whole forecourt -- one empty
        blend on a site with eight working meters is a note, not a halt --
        so what is asked here is the question a technician is actually
        asking: is ANYTHING for sale?
        """
        if not self.on:
            return "the generator is off: every meter sells whatever " \
                   "gal/h the bench typed into it, and nothing else"
        # the RATE, not the word over it: a site whose cars/day box
        # has been typed over is running at whatever is in the box
        if self.cars_per_day <= 0:
            return "the site is CLOSED -- modelled, and shut. No cars, " \
                   "which is its own thing to train on: CSLD finds all " \
                   "the idle time it wants"
        if self.can_sell() and self.selling():
            return None
        for why, _where in self.blockers():
            return why
        return "no nozzle is selling anything"

    # ---- how many cars, right now -------------------------------------------
    def weights(self):
        return SHAPES.get(self.shape) or SHAPES["DAY"]

    def arrival_rate(self, when=None):
        """Cars an hour at this hour of the CONSOLE's day."""
        if not self.on or self.cars_per_day <= 0:
            return 0.0
        stamp = when or self.c.now()
        weights = self.weights()
        total = float(sum(weights)) or 1.0
        # The day's cars shared out by weight. Hour h gets `day * w[h]/sum`
        # cars IN that hour, so that figure is already a rate per hour.
        return self.cars_per_day * weights[stamp.tm_hour] / total

    def _poisson(self, mean):
        """How many arrived in this interval.

        Knuth for a small mean, which is every interval a bench tick
        produces; a normal approximation above it, for the case where the
        clock has jumped a long way forward.
        """
        if mean <= 0:
            return 0
        if mean < 30:
            limit = 2.718281828459045 ** -mean
            n, product = 0, self.rng.random()
            while product > limit:
                n += 1
                product *= self.rng.random()
                if n > 400:
                    break
            return n
        return max(0, int(round(self.rng.gauss(mean, mean ** 0.5))))

    # ---- what the site sells -------------------------------------------------
    def tank_family(self, tank):
        return family(self.c.text("602", int(tank)) or "")

    def meter_family(self, meter):
        """What comes out of this nozzle.

        A blended meter's grade is the blend's own -- an E15 nozzle fed
        from a regular tank and an E-85 tank sells neither of them -- so
        the blend's label is asked first and the mapped tank second.
        """
        key = meter_key(meter)
        blend = self.c.blends.get(key)
        if blend and blend.get("parts"):
            return family(blend.get("label") or "")
        tank = self.c.meters.get(key)
        return self.tank_family(tank) if tank else None

    def selling(self):
        """{family: [meters that sell it]} for every mapped meter."""
        out = {}
        for meter in sorted(self.c.meters):
            fam = self.meter_family(meter)
            if fam:
                out.setdefault(fam, []).append(meter)
        for meter in sorted(self.c.blends):
            # a blend whose components are gone, or unmapped, is a grade
            # button on a dispenser that cannot make it
            if not self.c.blend_meters(meter):
                continue
            fam = self.meter_family(meter)
            if fam and meter not in out.get(fam, ()):
                out.setdefault(fam, []).append(meter)
        return out

    def _pick_family(self, available):
        """Which grade this car wants, out of the ones the site has.

        None when nobody wants anything. Zeroing every share used to fall
        back to picking uniformly, which is the opposite of what was
        asked: the grade rows read `0%  2 nozzles  0 gal/day`, the
        Deliveries card stopped warning about stock because
        `daily_gallons` was zero, and the forecourt served 96 cars in the
        next console hour. A share of nothing is a grade nobody buys, and
        a site of those sells nothing.
        """
        weights = [(fam, max(0.0, float(self.mix.get(fam, 0.0))))
                   for fam in available]
        total = sum(w for _f, w in weights)
        if total <= 0:
            return None
        draw = self.rng.random() * total
        for fam, weight in weights:
            draw -= weight
            if draw <= 0:
                return fam
        return weights[-1][0]

    def _dry(self, meter):
        """Has this nozzle got anything behind it?

        A blended nozzle needs product in EVERY component tank -- a
        blender with one dry tank cannot make the grade at all -- so the
        test is on all of them. The floor is a gallon rather than zero
        because the last few gallons in a tank are below the pickup and
        do not come out on a real site either.
        """
        for tank, _frac in (self.c.blend_parts(meter) or ()):
            st = self.c.tank_level.get(int(tank))
            if st is None or st.get("volume", 0.0) < 1.0:
                return True
        return False

    def _gallons(self, fam):
        median, spread, cap, _rate = SIZES.get(fam, SIZES["regular"])
        gallons = median * (2.718281828459045 ** self.rng.gauss(0.0, spread))
        return max(1.0, min(cap, gallons))

    def rate_for(self, fam):
        return SIZES.get(fam, SIZES["regular"])[3]

    # ---- the day -------------------------------------------------------------
    def tick(self, hours):
        """Send the cars this interval earned, and mind the tank levels.

        The tanker board is minded on EVERY tick and not only on one that
        moved the clock: ordering a load is a look at the gauge rather than
        a rate, and the first tick of a console's life passes no time at
        all. A site that comes up with a tank already low has already
        needed a truck for a while.
        """
        self._roll_day()
        # No car is sent to a forecourt where no fuel can move. See
        # `can_sell`. The tanker is minded in `gauge()` instead, which the
        # console calls further down the same pass.
        if not self.on or hours <= 0 or not self.can_sell():
            return
        # Each car arrives at a MOMENT inside the interval, not all of them
        # at its edge. At a real console's pace the difference is nothing;
        # at 36,000x an interval is seven hours, and a forecourt whose cars
        # all turn up at once is a forecourt where seven of every eight are
        # turned away by a queue that would not have existed -- a third of
        # the day's traffic vanished that way before this.
        now = time.mktime(self.c.now())
        opened = max(now - hours * 3600.0, now - self.MOST_HOURS * 3600.0)
        when = []
        for began, ended, stamp in self._slices(opened, now):
            due = self.arrival_rate(stamp) * (ended - began) / 3600.0
            when.extend(began + self.rng.random() * (ended - began)
                        for _ in range(self._poisson(due)))
        when.sort()
        for at in when:
            self._arrive(at, now)

    # The most traffic one tick will ever simulate, however far the clock
    # jumped. Nothing capped this: `Console.tick` runs inside the Tk main
    # loop, and setting the console's date a year forward -- which S501
    # invites you to do -- built 303,532 arrivals in one call and froze the
    # window for 29.5 seconds. A day is already far more than the 700ms
    # poll can be asked to cover, and a clock jumped further than that is
    # somebody moving the date, not a forecourt trading.
    MOST_HOURS = 24.0

    def _slices(self, opened, closes):
        """Walk an interval in pieces that never cross an hour boundary.

        `arrival_rate()` is the rate at ONE hour of the day. Multiplying it
        by the whole interval says the interval ran at whatever hour the
        tick happened to land on, which is exact at x1 and nonsense at
        36,000x, where one tick is seven hours: measured over ten console
        days on the DAY curve, 03:00 drew 41 cars per mille against its
        own 2, and the 17:00 peak kept 38 of its 102. The day's TOTAL
        stayed right, so no counter looked wrong while the shape -- one of
        the two knobs this whole module exists to offer -- had quietly
        flattened out.

        Yields (from, to, the console's own time at `from`).
        """
        at = float(opened)
        closes = float(closes)
        while at < closes:
            stamp = time.localtime(at)
            left = 3600.0 - (stamp.tm_min * 60.0 + stamp.tm_sec)
            ended = min(at + max(left, 1.0), closes)
            yield at, ended, stamp
            at = ended

    # How many console days of tallies to keep. Yesterday is worth
    # having -- it is the figure a seven hour tick used to destroy -- and
    # a bench left running does not need last week's.
    KEEP_DAYS = 3

    def _stamp(self, when=None):
        """The console date `when` falls in, as the tallies key them."""
        return time.strftime("%Y%m%d", self.c.now() if when is None
                             else time.localtime(when))

    def _tally(self, when=None):
        """The counters for the console day `when` falls in."""
        stamp = self._stamp(when)
        day = self._days.get(stamp)
        if day is None:
            day = self._days[stamp] = Day()
            for old in sorted(self._days)[:-self.KEEP_DAYS]:
                self._days.pop(old, None)
        return day

    def yesterday(self):
        """The day before the console's own, or None if it never ran."""
        return self._days.get(
            self._stamp(time.mktime(self.c.now()) - 86400.0))

    # The bench reads these as plain numbers and always means TODAY's, so
    # they stay plain numbers; what changed is that a car is added to the
    # day it arrived in rather than to whichever day the counters happened
    # to be zeroed for. See `Day`.
    cars = property(lambda self: self._tally().cars)
    gallons = property(lambda self: self._tally().gallons)
    balked = property(lambda self: self._tally().balked)
    blocked = property(lambda self: self._tally().blocked)
    dry = property(lambda self: self._tally().dry)
    deliveries = property(lambda self: self._tally().deliveries)

    def _roll_day(self):
        """Note the console's date. Nothing is zeroed any more: a day's
        tally is its own, and today's starts empty because it is new."""
        self.day = self._stamp()
        self._tally()

    def _arrive(self, at, closes):
        """One car at console time `at`: a grade, a free nozzle, a fill.

        `closes` is the end of the interval being filled in. A fill that
        finishes before then is served whole, between two ticks; one that
        would still be running goes into somebody's hand and stays there,
        which is what keeps a nozzle up across a tick boundary and a handle
        with it.
        """
        available = self.selling()
        if not available:
            return
        fam = self._pick_family(sorted(available))
        if fam is None:
            return
        free, shut, dry = [], 0, 0
        for meter in available[fam]:
            if not self.c.sales.free_at(meter, at):
                continue
            # a meter the bench is holding open by hand belongs to whoever
            # typed the rate in, not to this generator
            if self.c.meter_flow.get(meter, 0.0) > 0 \
                    and meter not in self.c.sales._serving \
                    and meter not in self.c.sales.running:
                continue
            if any(self.c.meter_flow.get(comp, 0.0) > 0
                   and comp not in self.c.sales._serving
                   and comp not in self.c.sales.running
                   for comp, _frac in self.c.blend_meters(meter)):
                continue
            if self.c.dispensing_blocked(meter):
                shut += 1
                continue
            if self._dry(meter):
                dry += 1
                continue
            free.append(meter)
        if not free:
            # the difference matters on the bench: every nozzle busy is a
            # queue, every nozzle DEAD is a shutdown somebody has to go and
            # clear, and a nozzle with nothing behind it is a tank that
            # needed a truck. The generator used to serve out of an empty
            # tank quite happily: four tanks at 0.0 gallons and the header
            # still counted 8,225 gallons sold that day, because BIR
            # clamps what actually LEAVES a tank and nothing clamped what
            # the generator claimed had been bought.
            day = self._tally(at)
            if dry and not shut:
                day.dry += 1
            elif shut:
                day.blocked += 1
            else:
                day.balked += 1
            return
        meter = self.rng.choice(free)
        gallons = self._gallons(fam)
        rate = self.rate_for(fam)
        if at + gallons / rate * 3600.0 <= closes:
            parts = self.c.blend_meters(meter)
            for component, frac in (parts or [(meter, 1.0)]):
                self.c.sales.serve(component, gallons * frac, rate * frac, at)
        elif self.c.sales.start(meter, gallons, rate=rate, at=at,
                                by="traffic") is None:
            return
        day = self._tally(at)
        day.cars += 1
        day.gallons += gallons

    # ---- the truck that comes on its own ------------------------------------
    def gauge(self):
        """Look at the tank gauges and call a truck if one is wanted.

        Called by `Console.tick` AFTER the interval's fuel has left the
        tanks, which is the whole point of it being its own method. It
        used to run at the top of `tick`, before `Bir._dispense` had drawn
        anything, so it always read the level as of the PREVIOUS console
        tick. At a real console's pace that is a stale reading by a
        fraction of a second. At 36,000x a tick is seven hours, so the
        board was making its decision on a seven hour old gauge: measured
        on the retail preset at BUSY, the console went through a whole
        console day noticing nothing, ordered on the last tick of it, and
        ended with both tanks at ZERO -- against 3,160 and 5,509 gallons
        at 60x on the same cars.

        The tanker board is minded on EVERY pass and not only on one that
        moved the clock: ordering a load is a look at the gauge rather
        than a rate, and the first pass of a console's life passes no time
        at all. A site that comes up with a tank already low has already
        needed a truck for a while.
        """
        if self.auto_deliver:
            self._replenish()
        elif self.orders:
            # switching it off calls the tanker back rather than leaving a
            # load pending that nothing will ever bring
            self.orders.clear()

    def _replenish(self):
        """Order a load when a tank gets low, and drop it when it arrives.

        A site does not wait until the tank is dry and the tanker does not
        arrive the moment it is called, so this is two steps: an order with
        a lead time, and a drop when the lead time is up. What lands is a
        multi-compartment load down the same `Drops` the truck dialog
        sends, which means the console is never told a delivery happened --
        it watches the level rise and works it out, exactly as it does on a
        site. BENCH.md T1.
        """
        now = time.mktime(self.c.now())
        # The bench clamps these as they are typed, but a state file written
        # before it did -- or edited by hand -- can still carry a low% of 0,
        # which means "order when the tank is empty", or a fill% under the
        # low% one, which orders a load every tick and pins every tank at
        # its overfill alarm. A setting that cannot be acted on sensibly is
        # brought back to one that can.
        low = min(95.0, max(1.0, self.low_pct))
        fill = min(100.0, max(low + 1.0, self.fill_pct))
        lead = min(168.0, max(0.0, self.lead_hours))
        for tank in sorted(self.c.tank_level):
            full = self.c.full_volume(tank)
            if full <= 0:
                continue
            volume = self.c.tank_level[tank].get("volume", 0.0)
            if tank in self.orders or tank in self.c.drops.running:
                continue
            # A tank that cannot last until the tanker could get here is
            # already late, whatever the percentage says. A site orders on
            # that too, and on this bench it is what keeps the answer the
            # same at any clock speed: the level only moves once a console
            # tick, so at 36,000x -- where a tick is seven hours -- the
            # threshold is noticed up to seven hours after it was crossed
            # and the four hour lead runs from there. Measured on the
            # retail preset at BUSY, tank 1 ended a console day at 3,226
            # gallons at 60x and at ZERO at 36,000x, on the same cars.
            due_soon = volume <= self.daily_gallons(tank) * lead / 24.0
            if volume / full * 100.0 <= low or due_soon:
                want = max(0.0, full * fill / 100.0 - volume)
                if want < 500.0:
                    continue
                self.orders[tank] = Order(
                    tank, round(want / 100.0) * 100.0,
                    now + lead * 3600.0)
        for tank, order in sorted(self.orders.items()):
            if order.due > now + lead * 3600.0 + 60.0:
                # The clock has been set BACKWARDS past this order -- S501
                # SET TIME/DATE, which the console's own `now()` invites
                # ("set the date to 2003") -- and `due` is an absolute
                # stamp. Nothing expired one, and `_replenish` will not
                # re-order a tank that already has one, so four tanks ran
                # dry with four loads still showing as "due 11:05" and a
                # truck that would not arrive until 2026. The load is real
                # and still wanted; it is the clock that moved, so the
                # order is re-timed from where the clock is now.
                order.due = now + lead * 3600.0
                continue
            if now < order.due:
                continue
            if tank in self.c.drops.running:
                continue
            self.orders.pop(tank, None)
            # do not overfill a tank somebody has filled in the meantime
            full = self.c.full_volume(tank)
            room = max(0.0, full * fill / 100.0
                       - self.c.tank_level.get(tank, {}).get("volume", 0.0))
            gallons = min(order.gallons, room)
            if gallons < 100.0:
                continue
            self.ticket += 1
            self.c.drops.start(tank, self._compartments(gallons),
                               rate=self.DROP_RATE, ticket=str(self.ticket),
                               bol=f"BOL{self.ticket}",
                               gap=self.COMPARTMENT_GAP)
            self._tally(now).deliveries += 1

    def _compartments(self, gallons):
        """A load split the way a tanker carries it."""
        out = []
        left = float(gallons)
        while left > 0:
            take = min(self.COMPARTMENT, left)
            out.append(take)
            left -= take
        return out

    # ---- what the bench shows ------------------------------------------------
    def nozzles_up(self):
        return len(self.c.sales.running)

    def days_left(self, tank):
        """How long this tank lasts at the rate the day is going.

        None when nothing is coming out of it, which is most tanks on a
        closed site and every tank with the generator off.
        """
        full = self.c.full_volume(tank)
        volume = self.c.tank_level.get(int(tank), {}).get("volume", 0.0)
        rate = self.daily_gallons(tank)
        if rate <= 0 or full <= 0:
            return None
        return volume / rate

    def grade_gallons(self, fam):
        """Gallons a day this GRADE is set to sell.

        Not the site's total split by how many CARS want it. A diesel
        customer takes 51 gallons where a regular takes 12, so the car
        share and the gallon share are different numbers -- and the grade
        rows printed the car share under the words `gal/day`. On the
        default mix that understated diesel by 2.7x (1,755 against the
        4,737 the engine actually puts through it) while still summing to
        the right site total, so it looked self-consistent and disagreed
        with the per-tank figure printed a few inches below it.
        """
        available = self.selling()
        if not self.on or fam not in available:
            return 0.0
        weights = {f: max(0.0, float(self.mix.get(f, 0.0)))
                   for f in available}
        total = sum(weights.values())
        if total <= 0:
            return 0.0
        share = self.cars_per_day * weights[fam] / total
        median, spread, _cap, _rate = SIZES.get(fam, SIZES["regular"])
        # the mean of a lognormal, not its median
        return share * median * (2.718281828459045 ** (spread * spread / 2.0))

    def daily_gallons(self, tank=None):
        """Gallons a day this site, or this tank, is set to sell.

        Computed from the settings rather than measured, so the bench can
        show what a level MEANS before a day of it has run.
        """
        available = self.selling()
        if not available or not self.on:
            return 0.0
        weights = {fam: max(0.0, float(self.mix.get(fam, 0.0)))
                   for fam in available}
        total = sum(weights.values())
        if total <= 0:
            return 0.0
        out = 0.0
        for fam, meters in available.items():
            share = self.cars_per_day * weights[fam] / total
            median, spread, _cap, _rate = SIZES.get(fam, SIZES["regular"])
            # the mean of a lognormal, not its median
            mean = median * (2.718281828459045 ** (spread * spread / 2.0))
            if tank is None:
                out += share * mean
                continue
            # what share of that grade's gallons comes out of THIS tank
            for meter in meters:
                parts = self.c.blend_parts(meter)
                for ptank, frac in parts:
                    if int(ptank) == int(tank):
                        out += share / len(meters) * mean * frac
        return out
