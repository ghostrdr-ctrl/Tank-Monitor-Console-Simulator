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
"""Business Inventory Reconciliation: meters against the probe.

A shift reconciliation report is eight numbers per tank, and the manual lists
them in the order the console prints them:

    "Probe measured inventory at previous period close, sum total of adjusted
    deliveries during period, sum total of all metered sales during period,
    manually entered adjustments for period, calculated inventory volume at
    period close, probe measured inventory at period close, water height at
    period close, variance over period."

So the console needs to know what the dispensers sold, which is what the
tank/meter map is for: "assign a meter to a tank ... so that BIR can still
function". Sales run down the tank they are mapped to; deliveries run it up;
what the probe reads at the end should equal what the arithmetic says, and the
difference is the variance a site is looking for.

It reconciles FOUR periods at once, because the Reconciliation Mode reports
are per period and the periods do not close together, "the totals at the end
of each shift, day, and period", plus the week that Close Day Of Week ends.
Each accumulates the same sales and deliveries and is closed on its own
schedule, so closing a shift does not disturb the day that contains it.
"""
import datetime
import time

from .clock import clock_words
from .meterid import MeterDict, meter_key

# The periods a console reconciles.
KINDS = ("shift", "daily", "weekly", "periodic")

# Category 20 is "BIR / Product" and its type 02 is the Threshold Alarm --
# the Periodic Reconciliation Alarm 576013-818 p.12-3 gives the formula for.
THRESHOLD_ALARM = ("20", "02")

# How many closed periods the console keeps, per kind. Every period kept ten
# of them, which is a number nothing states; the DAILY history is the one the
# manuals put a figure on, and they put it in the report's own title:
# "I@A400 DAILY RECONCILIATION LIST FOR LAST 31 DAYS (62 ON NEWER VERSIONS)",
# 576013-818 p.12-5. The sample under that title runs to twenty-four rows for
# one tank, against a console that could hold ten. This simulator's software
# is 3xx, which is the newer one. See FIDELITY G12.
KEPT = {"daily": 62}
KEPT_DEFAULT = 10

# "If an unmapped meter has not been reported by a POS within 24 hours of the
# last report, the meter is declared 'retired' ... Until the 'retired' meter
# is mapped, every time the meter is activated, and for 24 hours thereafter,
# BIR is suspended." 576013-818 p.12-8. One window, used both ways round.
RETIRE_HOURS = 24.0


class BIR:
    """One console's reconciliation, per tank and per period."""

    def __init__(self, console):
        self.c = console
        self.totals = MeterDict()   # meter -> lifetime gallons
        # The Meter Events Table, which 576013-818 Figure 6-25 branches on
        # twice: whether it has anything in it at all, and whether the last
        # event in it is a Start or an End. An event is one meter running:
        # it STARTS when fuel begins to move through it and ENDS with the
        # gallons that went through, which is what `END EVENT: X GALS`
        # reads. Newest last. See FIDELITY D14.
        self.events = []
        self._running = MeterDict()   # gallons since its start event
        self.period = {}       # (tank, kind) -> the period being accumulated
        self.closed = {}       # (tank, kind) -> [closed records], newest first
        self.reported_at = MeterDict()   # when a POS last mentioned it
        self.suspended = MeterDict()   # when its retirement window ends
        self._last = None      # console time as of the last look
        self._before = None    # and the one before that, for the closing times
        self.pending = []      # closes waiting for the site to go idle:
                               # (kind, due-time), posting Close Pending

    def enabled(self):
        return self.c.licensed("bir")

    # ---- which volume a BIR volume is --------------------------------------
    def temperature_compensated(self):
        """S79F: is this site's reconciliation on the TC basis?

        576013-623 Rev AN p.17-4: "Select STANDARD (the default) if the
        meters are not temperature compensated. Select TC VOLUME if the
        meters are temperature compensated (the calculation of all BIR
        volumes will be based on the TC value)." 576013-818 p.12-2 says
        what a wrong answer costs: "If the meters are reporting temperature
        compensated volumes, this entry must be set to YES. Incorrect
        setting of this entry will result in variance errors."

        It was read in exactly one place -- to choose the words `VOLUMES ARE
        TC` or `VOLUMES ARE STANDARD` on a report header -- and every volume
        under that header was the gross one. A header that says which basis
        the numbers are on, over numbers that are always the same basis, is
        the SYSTEM CONFIGURATION defect from the head of FIDELITY: two
        things rendered from one source on a screen that exists to show them
        differ. See FIDELITY G10.
        """
        return (self.c.values.get("S79F00") or "").strip().endswith("1")

    def basis(self, tank, gallons):
        """A quantity that moved through that tank, on BIR's own basis.

        The correction is the one `tc_volume` applies to a standing volume,
        with the tank's own coefficient and the console's own reference
        temperature: product drawn at the tank's temperature occupies a
        different volume at the reference temperature, and a TC meter is a
        meter that has already done this arithmetic.
        """
        if not self.temperature_compensated():
            return gallons
        away = self.c.product_temperature(tank) - self.c.tc_reference()
        return gallons * (1.0 - self.c.tank_coefficient(tank) * away)

    def gauged(self, tank):
        """What the probe says the tank holds, on BIR's own basis -- and for
        a manifolded set, what the SET holds.

        576013-610 p.28-2: "Reconciliation Reports are generated as a single
        product report for a manifolded set." One report over one inventory,
        and a siphon set's inventory is its tanks added up. See FIDELITY
        G11.
        """
        total = 0.0
        for one in self.set_of(tank):
            total += (self.c.tc_volume(one)
                      if self.temperature_compensated()
                      else (self.c.tank_level.get(one) or {}
                            ).get("volume", 0.0))
        return total

    # ---- a manifolded set is one report ------------------------------------
    def primary(self, tank):
        """The tank a manifolded set reconciles under.

        576013-818 p.12-8: "In the case of manifolded tanks, the meter is
        mapped to the primary tank. The primary tank is defined as the
        lowest numbered tank in the manifolded set." 576013-623 p.17-6
        says it from the setup side and adds the consequence: "only the
        primary tank needs to be entered for a manifolded set of tanks ...
        If a tank number is entered that is part of a manifolded set, but it
        is not the primary tank, the selection will be rejected."
        """
        return min(self.set_of(tank))

    def set_of(self, tank):
        """Every tank one report covers, in order."""
        return sorted(self.c.manifolded(int(tank)))

    def tank_lines(self, tank):
        """The label lines that go over one table.

        576013-818 p.12-27 draws it: `T1: BLUE WEST Primary` over `T2: BLUE
        EAST Secondary`, one header, one table. Every reconciliation and
        variance report here looped over bare tanks, so the secondary of a
        siphon set kept a period of its own -- taking the deliveries the
        siphon carried it and none of the sales, which are mapped to the
        primary -- and showed a large permanent phantom variance.

        The tank line has two spellings in one chapter, as its title does:
        p.12-27 writes `T1: BLUE WEST Primary` and p.12-30 writes
        `T 1:UNLEADED`. The console follows p.12-30 for both, so its head
        and its columns come from one page.
        """
        return ["T %d:%s" % (n, self.c.text("602", n) or "TANK %d" % n)
                for n in self.set_of(tank)]

    # ---- the meter map, and what an incomplete one stops ---------------
    def available(self, tank):
        """Can a meter be mapped to this tank at all?

        576013-818 p.12-8: "A tank will be unavailable for mapping if any of
        the following conditions are true: In-tank programming parameter
        Meter Data Present set to NO, It is manifolded and the console has
        1XX software, It is not configured, Probe data is not being
        collected, or Probe not magnetostrictive type."

        The set's PRIMARY is asked, because that is where the same page puts
        the meter. See FIDELITY G8.
        """
        if not tank:
            return False
        one = self.primary(tank)
        c = self.c
        if one not in c.tank_level:
            return False
        if not (c.values.get(f"S615{one:02d}") or "").strip().endswith("1"):
            return False
        if c.family() == "1XX" and len(c.manifolded(one)) > 1:
            return False
        if not c.programmed_tanks().get(one):
            return False
        if one in c.probe_out:
            return False
        if c.probe_type(one) != "MAG PROBE":
            return False
        return True

    # "TT - Tank Number ... -1=Probeless tank", 7B1's own note, and the
    # keyboard's own spelling of the same thing is 99.
    PROBELESS = -1

    def mapped(self, meter):
        """Is this meter placed somewhere the map can account for?

        Two ways of being placed, and only one of them is a tank this
        console gauges. 576013-818 p.12-9: "In some applications the
        dispensing data sent from the POS terminal to the TLS Console will
        contain meter transactions from a tank(s) in which there is no
        probe. Unable to match the transaction with a corresponding height
        change, the tank-meter mapping algorithm will declare the map
        incomplete and BIR will be inhibited. You must manually map a
        'probeless' meter into the tank/meter map before it will be declared
        complete and BIR can begin."

        So mapping a meter to the probeless tank is what COMPLETES the map,
        and this asked `available` about tank -1, which is not a tank and
        never will be: the one action the page prescribes made no
        difference, and the third of p.12-8's three completeness conditions
        was unreachable. See FIDELITY G8.
        """
        where = self.c.meters.get(meter)
        if where == self.PROBELESS:
            return True
        return self.available(where)

    def reported_meters(self):
        """The meters a POS is telling this console about.

        Selling now, or heard from inside the retirement window. A meter
        that has gone quiet for longer than that is RETIRED and stops
        counting against the map, which is the whole point of the state:
        "A retired meter may be a phantom meter incorrectly reported by the
        POS, or it may be a seldom heard from meter, such as one connected
        to a kerosene tank." Without it a phantom the POS mentioned once
        would stop a site reconciling for ever.
        """
        now = time.mktime(self.c.now())
        out = {m for m, rate in self.c.meter_flow.items() if rate}
        out |= {m for m, at in self.reported_at.items()
                if (now - at) / 3600.0 <= RETIRE_HOURS}
        return sorted(out)

    def retired(self, meter):
        """Has this meter been silent long enough to be written off?

        "If an UNMAPPED meter has not been reported by a POS within 24 hours
        of the last report", so both halves: silent, and with nowhere to
        reconcile against.
        """
        at = self.reported_at.get(meter)
        if at is None or self.c.meter_flow.get(meter):
            return False
        if self.mapped(meter):
            return False
        now = time.mktime(self.c.now())
        return (now - at) / 3600.0 > RETIRE_HOURS

    def note_reported(self, meter, now):
        """A POS has just mentioned this meter.

        If it was retired and is still unmapped, its coming back suspends
        BIR for a day: "every time the meter is activated, and for 24 hours
        thereafter". Mapping it is what ends that, so the window is checked
        against the map rather than only against the clock.
        """
        at = self.reported_at.get(meter)
        # measured off the STAMP rather than through `retired()`, because a
        # meter is selling again at the moment it is reactivated and
        # `retired()` asks whether it is silent NOW
        gone = at is not None and (now - at) / 3600.0 > RETIRE_HOURS
        if gone and not self.mapped(meter):
            self.suspended[meter] = now + RETIRE_HOURS * 3600.0
        self.reported_at[meter] = now

    def suspended_meters(self):
        """Retired meters whose 24 hours have not run out, still unmapped."""
        now = time.mktime(self.c.now())
        return sorted(m for m, until in self.suspended.items()
                      if now < until and not self.mapped(m))

    def unmapped_meters(self):
        """Reported meters this console cannot reconcile against a tank.

        A meter with no entry in the map is unmapped; so is one pointing at
        a tank the map is not allowed to use, because that is the state
        p.12-8's availability list produces -- 576013-818's Example 5 is a
        console whose meter shows as `U` in the ballot precisely because its
        tanks are manifolded on 1XX software. One mapped to the PROBELESS
        tank is mapped, which is the whole of p.12-9; see `mapped`.
        """
        return [m for m in self.reported_meters() if not self.mapped(m)]

    def map_complete(self):
        """"BIR will not produce reports while the meter map is incomplete."

        All three of the conditions p.12-8 gives are here: an unmapped
        reported meter, a retired meter coming back, and the probeless one
        -- a meter whose transactions match no height change anywhere,
        which stays unmapped until somebody maps it to tank -1 by hand. See
        `mapped` and FIDELITY G8.

        A console that has mapped nothing has an incomplete map, and
        that is the captured hardware rather than a reading of the page: a
        TLS-350 running 326.01 with no meters at all answers I@A002 with
        `MAP IS INCOMPLETE`. Vacuous truth would make it complete, which is
        what this returned for one run of the suite.
        """
        return (bool(self.c.meters) and not self.unmapped_meters()
                and not self.suspended_meters())

    def clear_map(self):
        """RECONCILIATION CLEAR MAP, answered YES.

        576013-818 Rev AB Figure 6-24 annotates the screen with what the
        answer does: "Selecting YES restarts BIR and clears the Adjusted
        Delivery Reports, BIR Reconciliation Records, and Meter Map.
        Selecting YES does not restart the AccuChart 56-day calibration."

        So three stores go and one deliberately stays. The map goes first
        and the restart follows from it: a console that has mapped nothing
        has an incomplete map -- `map_complete` says so, off captured
        hardware -- and BIR does not report while the map is incomplete, so
        clearing the map IS the restart. The meters themselves are not
        forgotten: they are reported by the POS and will be mapped again as
        their transactions match height changes, which is what restarting
        the mapping means.

        The Adjusted Delivery Reports are the one this console cannot
        separate, and it is worth saying which way. It derives both that
        report and the In-Tank Delivery Report from one store of delivery
        records, and Figure 6-24 asks for only the first. Clearing the
        store would take a report the page does not name -- the one S051
        exists to clear on its own -- so what is cleared here is the queue
        of adjusted reports this console has recognised and not yet
        printed. See CLOSED D19.

        Returns what went, for the bench log; the console says nothing
        about counts, which is FIDELITY U5's rule.
        """
        meters = len(self.c.meters) + len(self.c.meter_map)
        self.c.meters.clear()
        self.c.meter_map.clear()
        records = sum(len(v) for v in self.closed.values())
        self.closed.clear()
        self.period.clear()
        self.events.clear()
        self._running.clear()
        self.pending = []
        self.reported_at.clear()
        self.suspended.clear()
        queued = len(self.c.printed_deliveries)
        self.c.printed_deliveries.clear()
        self.c.save()
        return meters, records, queued

    def report_tanks(self, tanks):
        """The tanks a report actually prints, one entry per SET.

        A request for the secondary is a request for the set, and the set
        prints once.
        """
        out = []
        for tank in tanks or ():
            one = self.primary(tank)
            if one not in out:
                out.append(one)
        return out

    def covers(self, tank):
        """Does this tank reconcile at all?

        S615, Meter Data Present. 576013-818 p.12-2 gives the flag its two
        consequences and both of them are about whether the tank is IN the
        reconciliation: "If there is meter data present and this entry is
        incorrectly set to NO, the map will never complete because the
        auto-meter mapping program will not assign this tank to a meter. If
        there is no meter data present and this entry is incorrectly set to
        YES, a BIR report will be generated for this tank. There will be
        large reconciliation errors because there is no sales information."

        A report exists for a tank BECAUSE the flag says yes, so the flag is
        what decides. This opened all four periods for every tank in
        `tank_level` whatever the flag said, and the only reader S615 had
        was AccuChart. See FIDELITY G10.
        """
        raw = (self.c.values.get(f"S615{int(tank):02d}") or "").strip()
        # and the SET reconciles under its primary, so the secondary of a
        # manifolded pair is covered by the primary's report rather than
        # keeping one of its own
        return raw.endswith("1") and self.primary(tank) == int(tank)

    def tanks(self):
        """The tanks this console reconciles, in order."""
        return [t for t in sorted(self.c.tank_level) if self.covers(t)]

    # ---- what the dispensers do --------------------------------------------
    def open_periods(self):
        """Every reconciled tank has all four periods running.

        Called BEFORE `Sales.draw` takes the interval's fuel, so a period
        opens on what the probe read before anything in this interval moved
        the level -- or the first interval's fuel goes missing from the
        opening figure. `tick` opens them too, for a console whose key was
        fitted between the two calls, but by then the fuel has moved.
        """
        if self._last is None or not self.enabled():
            return
        for tank in self.tanks():
            for kind in KINDS:
                self._open(tank, kind)

    def tick(self):
        now = time.mktime(self.c.now())
        last, self._last = self._last, now
        self._before = last
        if last is None or not self.enabled():
            return
        for tank in self.tanks():
            for kind in KINDS:
                self._open(tank, kind)    # every tank has all four running
        hours = (now - last) / 3600.0
        if hours > 0:
            self._dispense(hours)
        self._scheduled(now)

    def _dispense(self, hours):
        """Meters sell fuel, and the sale is booked against its tank.

        The fuel itself has already left: `Sales.draw` moved it, before
        this looked, and `drawn` says how much per meter. What is decided
        here is whether the console gets to KNOW -- which needs a DIM in
        the cage with its link up -- and what the meter says about it.
        """
        shutdown = self.c.isd_shutdown_active()
        # Metered transactions reach the console through a DIM; with no DIM
        # in the cage, or the DIM link faulted, there is no meter data and
        # nothing to reconcile -- the fuel still flows at the site, but this
        # console cannot see it, so the bench meters go quiet too.
        dim = self.c.has("edim") or self.c.has("mdim")
        for meter, rate in sorted(self.c.meter_flow.items()):
            if (not rate or shutdown or not dim
                    or not self.c.dim_link_ok(meter)):
                # an ISD shutdown has the dispensers off: nothing sells
                # until the alarm clears or a technician overrides it
                self.end_event(meter)
                continue
            if self.c.dispensing_blocked(meter):
                # And a line the console has shut down, or a relay wired to
                # the tank that has dropped out, is the same thing one step
                # closer to the fuel: no pump, so no product moves however
                # long the handle stays up. 577013-344 Rev H p.22's diag
                # screen says DISABLE ALARM while this is true.
                self.end_event(meter)
                continue
            # The POS has just reported this meter, whether or not the map
            # can place it -- which is exactly the case the retirement rule
            # is about.
            self.note_reported(meter, time.mktime(self.c.now()))
            tank = self.c.meters.get(meter)
            st = self.c.tank_level.get(tank) if tank else None
            if st is None:
                self.end_event(meter)
                continue
            # "In the case of manifolded tanks, the meter is mapped to the
            # primary tank": the fuel leaves the tank the meter is wired to
            # and the SALE is reconciled against the set.
            owner = self.primary(tank)
            # open the periods on what the probe reads BEFORE this sale, or
            # the first hour's fuel goes missing from the opening figure.
            # A tank the site says has no meter data reconciles nothing --
            # the fuel still leaves it and the meter still counts, which is
            # what makes the flag being wrong visible.
            periods = ([self._open(owner, kind) for kind in KINDS]
                       if self.covers(owner) else [])
            gallons = float(self.c.sales.drawn.get(meter, 0.0))
            if gallons <= 0:
                continue
            # What LEFT the tank is `gallons`. What the METER says left it is
            # `gallons` adjusted by its calibration offset, and the gap
            # between those two is precisely what a reconciliation measures:
            # "sum total of all metered sales during period" is the meter's
            # figure, not the probe's. A site with a mis-calibrated meter and
            # a sound tank shows a variance, which is the whole reason the
            # offset is a setting.
            # And on the basis the site's meters report in: a TC meter has
            # already corrected what it sold, and BIR compares it against a
            # gauged inventory corrected the same way. See `basis`.
            metered = self.basis(
                owner, gallons * (1.0 + self.c.meter_offset(meter) / 100.0))
            self.totals[meter] = self.totals.get(meter, 0.0) + metered
            if meter not in self._running:
                self.log_event(meter, "start", 0.0)
                self._running[meter] = 0.0
            self._running[meter] += metered
            for period in periods:
                period["sales"] += metered
            # "The adjusted delivery report takes into consideration all
            # dispensing that occurred during the delivery."
            running = self.c.deliveries.in_progress(tank)
            if running is not None:
                running.sold += gallons
        # A meter taken off the bench altogether stops the same way one whose
        # rate went to zero does: its event ends with what went through it.
        for meter in [m for m in self._running if m not in self.c.meter_flow]:
            self.end_event(meter)

    # "Prints Last 4 Meter Events", Figure 6-25 -- so the table is longer
    # than that and this is the depth a bench needs.
    EVENTS_KEPT = 40

    def log_event(self, meter, kind, gallons):
        """One row of the Meter Events Table."""
        meter = meter_key(meter)
        self.events.append({
            # both numbers come off the meter's own identity now: the
            # position is part of what a meter IS rather than a field of a
            # map entry with a guess behind it. FIDELITY G7.
            "at": time.mktime(self.c.now()), "meter": meter.meter,
            "fp": meter.fp,
            "tank": self.c.meters.get(meter, 0), "kind": kind,
            "gallons": float(gallons)})
        del self.events[:-self.EVENTS_KEPT]

    def end_event(self, meter):
        """A meter that has stopped: the End Event carries what went out."""
        meter = meter_key(meter)
        if meter not in self._running:
            return None
        gallons = self._running.pop(meter)
        self.log_event(meter, "end", gallons)
        return gallons

    def last_event(self):
        """The newest row of the table, which is what the screens read."""
        return self.events[-1] if self.events else None

    def _open(self, tank, kind="shift"):
        """The period being accumulated for that tank, started if need be."""
        period = self.period.get((tank, kind))
        if period is None:
            st = self.c.tank_level.get(tank, {})
            period = {"opened": time.mktime(self.c.now()),
                      # "REQUEST ST" against "STRT TIME": the history table
                      # prints both because they are not the same instant.
                      # A scheduled close reopens the next period at the
                      # programmed time; a period opened any other way was
                      # asked for when it opened. See FIDELITY G8.
                      "requested": time.mktime(self.c.now()),
                      "opening": self.gauged(tank),
                      "water_open": st.get("water", 0.0),
                      "temp_open": self.c.product_temperature(tank), "sales": 0.0, "deliveries": 0.0,
                      "ticketed": 0.0, "adjust": 0.0}
            self.period[(tank, kind)] = period
        return period

    def delivered(self, tank, gallons, ticket=None):
        """A delivery counts towards every period it landed in."""
        if not self.enabled():
            return
        tank = self.primary(tank)
        for kind in KINDS:
            period = self._open(tank, kind)
            period["deliveries"] += gallons
            if ticket:
                period["ticketed"] += ticket

    def ticket(self, tank, gallons):
        """A ticket entered after the drop, at DELIVERY MAINTENANCE."""
        if not self.enabled():
            return
        tank = self.primary(tank)
        for kind in KINDS:
            self._open(tank, kind)["ticketed"] += gallons

    def adjust(self, tank, gallons, kind=None, previous=False, day=None):
        """"Manually entered adjustments for period": S79B and S79C.

        An adjustment against a shift is also an adjustment to the day and the
        period containing it, so it lands in all of them; naming a kind is how
        the panel says which period the operator was looking at.

        And the period it names can have closed. 576013-610 Rev AC
        p.28-19: "You can adjust the volume for the previous or current shift
        or for any day in the period", and p.28-20 asks for "the desired
        closing date for the adjustment". Every adjustment used to land in
        the OPEN periods whatever the panel was pointed at, so forty gallons
        entered on SELECT SHIFT: PREVIOUS went into the shift that was
        running. A closed row's calculated inventory and its variance were
        worked out when it closed, so both move with the adjustment, and so
        does every coarser period holding that moment, closed or still open.
        None when the panel names a period this console no longer holds.
        See FIDELITY Q1.
        """
        primary = self.primary(tank)
        kind = kind or "shift"
        target = None
        if day is not None and kind == "daily":
            today = time.strftime("%Y%m%d", self.c.now())
            if time.strftime("%Y%m%d", time.localtime(day)) != today:
                target = self._closed_on(primary, day)
                if target is None:
                    return None
        elif previous:
            target = self.last(primary, kind)
            if target is None:
                return None
        if target is None:
            for one in KINDS:
                self._open(primary, one)["adjust"] += gallons
            return self._open(tank, kind)
        moment = target["closed"] - 1.0
        for one in KINDS[KINDS.index(kind):]:
            row = target if one == kind else self._holding(primary, one,
                                                           moment)
            if row is None:
                continue
            row["adjust"] += gallons
            if "calculated" in row:
                # a closed row carries the figures it closed with
                row["calculated"] += gallons
                row["variance"] = row["physical"] - row["calculated"]
        return target

    def _holding(self, tank, kind, moment):
        """The period of that kind the moment falls in.

        A closed row that spans it, or else the running period -- when
        nothing of that kind has closed since the moment. A period is opened
        the first time something touches it, so a day nobody has sold into
        yet still holds the shift that closed inside it.
        """
        rows = self.closed.get((tank, kind)) or []
        for row in rows:
            if row["opened"] <= moment <= row["closed"]:
                return row
        if not rows or rows[0]["closed"] < moment:
            return self._open(tank, kind)
        return None

    # ---- closing ------------------------------------------------------------
    def _scheduled(self, now):
        """The closing times a site programmes.

        S794 holds up to four auto shift closing times and S793 the automatic
        daily closing time, both HHmm; the week ends on the Close Day Of Week
        at S51E, and the period is the Periodic Reconciliation Mode at S795,
        "a report will automatically print on the first day of each month" or
        at the end of a rolling interval.
        """
        if self._before is None:
            return
        stamp = self.c.now()
        times = []
        for shift in range(1, 5):
            raw = (self.c.values.get(f"S794{shift:02d}") or "").strip()
            body = raw[2:] if len(raw) > 4 else raw
            if len(body) == 4 and body.isdigit():
                times.append(("shift", body))
        daily = (self.c.values.get("S79300") or "").strip()
        if len(daily) == 4 and daily.isdigit():
            times.append(("daily", daily))
        # Every close that came due, not the first one. This loop used
        # to `return` on the first, and `_before` then advanced past the
        # rest, so a second close due in the same window was lost for good.
        # 576013-623 p.17-2 tells a site to arrange exactly that -- "Shift
        # Closing Time #4 should match Shift Start Time #1" -- and the daily
        # default is 2:00 AM, so a site with shift 1 at 02:00 lost its daily
        # close every day, and with it the weekly and the periodic, which
        # only fire from inside the daily branch. See FIDELITY G3.
        due_now = [(due, kind)
                   for kind, hhmm in times
                   for day in (-1, 0)
                   for due in [time.mktime((stamp.tm_year, stamp.tm_mon,
                                            stamp.tm_mday + day,
                                            int(hhmm[:2]), int(hhmm[2:]),
                                            0, 0, 1, -1))]
                   if self._before < due <= now]
        # oldest first, and a SHIFT before a DAILY due at the same minute:
        # the day contains the shift, so closing the day first would carry
        # the shift's own book into the next one.
        due_now.sort(key=lambda item: (item[0], item[1] != "shift"))
        # a close that came due while the site was dispensing waits for an
        # idle period, and the console says so: "BIR Shift/Daily Close
        # Pending" posts until the close happens, then clears itself.
        # 576013-818 p.12-2 names `Close Daily Pending` and `Close Shift
        # Pending` as two conditions a console can hold AT ONCE, which is
        # why this is a list and not one slot.
        waiting, self.pending = self.pending, []
        for due, kind in waiting + due_now:
            if self._dispensing():
                self.pending.append((due, kind))
                continue
            self.close(kind, at=due)
            if kind == "daily":
                self._week_and_period(due)

    def _dispensing(self):
        """Is any mapped meter selling right now? A close waits for idle.

        The instant, off the sales' own spans, and not `meter_flow` -- which
        is a rate averaged over the interval that has just ticked, so on a
        fast clock one car anywhere in the last seven hours would hold a
        close open for ever and earn a CLOSE SHIFT WARNING nobody had
        caused. Same reasoning as `Console.activity_spans`.
        """
        now = time.mktime(self.c.now())
        for meter in self.c.meters:
            if not self.c.sales.free_at(meter, now):
                return True
            if (self.c.meter_flow.get(meter, 0.0) > 0
                    and meter not in self.c.sales.running
                    and meter not in self.c.sales._serving):
                # a rate somebody typed into a meter card is a forecourt
                # that never stops, and it has no spans to say when
                return True
        return False

    def conditions(self):
        """[AANNTT] for the close that is waiting: system alarm 13 for a
        shift, 14 for a day -- and category 20's Threshold Alarm."""
        out = []
        for _due, kind in self.pending:
            code = "011300" if kind == "shift" else "011400"
            if code not in out:
                out.append(code)
        out.extend(self.threshold_alarms())
        return out

    def threshold_alarms(self):
        """The Periodic Reconciliation Alarm, 576013-818 p.12-3.

        "If the variance for the reconciliation period exceeds the maximum
        limit determined by the Alarm Threshold and Alarm Offset values, the
        Periodic Reconciliation Alarm will be posted."

        `threshold()` has implemented the formula and the manual's own
        defaults all along, and all five of its call sites were PRINT sites:
        three report builders, a panel reading and the printer. Nothing
        compared a variance against it, so no 20xx alarm was ever posted by
        anything. See FIDELITY G4.

        Magnitude, not sign: the same page says "the polarity of the variance
        is either positive or negative", a negative one meaning more fluid
        left the tank than the POS reported. A loss of two thousand gallons
        exceeds an eleven hundred gallon limit whichever way it is written.
        """
        if not self.enabled():
            return []
        # "Periodic Reconciliation Alarm (Disabled/Enabled)", S797, and the
        # alarm exists only if the site turned it on
        if (self.c.values.get("S79700") or "").strip()[-2:] != "02":
            return []
        out = []
        for (tank, kind), rows in sorted(self.closed.items()):
            if kind != "periodic" or not rows:
                continue
            row = rows[0]
            if abs(row["variance"]) > self.threshold(row):
                out.append(THRESHOLD_ALARM[0] + THRESHOLD_ALARM[1]
                           + f"{tank:02d}")
        return out

    def _week_and_period(self, due):
        """The week and the period close on the day, not on a clock of their
        own: the week on Close Day Of Week, the period monthly or rolling.

        On the day the daily close came DUE, which is the day it closes.
        This asked the moment the tick ran instead, so a close held pending
        past midnight, or reached by a tick that crossed one, missed its
        week and its month or took them twice. FIDELITY G14.
        """
        stamp = time.localtime(due)
        close_day = (self.c.values.get("S51E00") or "").strip()[-1:]
        # S51E counts from Sunday, tm_wday from Monday
        if close_day.isdigit() and stamp.tm_wday == (int(close_day) + 6) % 7:
            self.close("weekly")
        # "ss - Periodic Reconciliation Mode  2=Rolling 1=Monthly", 576013-635
        # Rev AA under 795. This read the last character and branched on "1"
        # for ROLLING, which is the manual exactly backwards both ways round:
        # a site programmed Monthly closed on a rolling window and a site
        # programmed Rolling closed on the first of the month. It decides what
        # every periodic report on the console covers. The two-character form
        # is the manual's; a one-character value means the same digit, so both
        # widths are read. See FIDELITY F11.
        mode = (self.c.values.get("S79500") or "").strip()[-2:]
        if mode in ("2", "02"):
            length = (self.c.values.get("S79600") or "").strip()[-2:]
            days = int(length) if length.isdigit() and int(length) else 31
            first = min((p["opened"] for (t, k), p in self.period.items()
                         if k == "periodic"), default=due)
            if (due - first) / 86400.0 >= days:
                self.close("periodic")
        elif stamp.tm_mday == 1:
            self.close("periodic")

    def close(self, kind="shift", tank=None, at=None):
        """Close the period and write the row the report prints.

        `at` is the moment the close was DUE, which is what the next period
        was asked to open at -- the history table prints it beside the
        moment it actually opened.
        """
        tanks = [tank] if tank else self.tanks()
        rows = []
        for one in tanks:
            period = self._open(one, kind)
            st = self.c.tank_level.get(one, {})
            physical, water = self.gauged(one), st.get("water", 0.0)
            calculated = (period["opening"] + period["deliveries"]
                          - period["sales"] + period["adjust"])
            row = {"tank": one, "kind": kind, "opened": period["opened"],
                   "closed": time.mktime(self.c.now()),
                   "requested": period.get("requested", period["opened"]),
                   "opening": period["opening"],
                   "water_open": period["water_open"],
                   "temp_open": period["temp_open"],
                   # `one`, not `tank`: `tank` is this function's parameter
                   # and is None on every scheduled close, so all four tanks
                   # recorded one shared temperature. It feeds TEMP VAR --
                   # "change in volume related to change in temperature",
                   # 576013-610 p.28-14 -- and through it UNEX VAR. See
                   # FIDELITY G2.
                   "temp_close": self.c.product_temperature(one),
                   "deliveries": period["deliveries"],
                   "ticketed": period["ticketed"],
                   "sales": period["sales"], "adjust": period["adjust"],
                   "calculated": calculated, "physical": physical,
                   "water": water, "variance": physical - calculated}
            self.closed.setdefault((one, kind), []).insert(0, row)
            del self.closed[(one, kind)][KEPT.get(kind, KEPT_DEFAULT):]
            self.period[(one, kind)] = {
                "opened": row["closed"],
                "requested": at if at is not None else row["closed"],
                "opening": physical,
                "water_open": water,
                "temp_open": self.c.product_temperature(one), "sales": 0.0,
                "deliveries": 0.0, "ticketed": 0.0, "adjust": 0.0}
            rows.append(row)
        return rows

    def last(self, tank, kind="shift"):
        rows = self.closed.get((tank, kind)) or []
        return rows[0] if rows else None

    # ---- what it shows ------------------------------------------------------
    def current(self, tank, kind="shift"):
        """The period so far, as a row the same shape as a closed one."""
        period = self._open(tank, kind)
        st = self.c.tank_level.get(tank, {})
        physical, water = self.gauged(tank), st.get("water", 0.0)
        calculated = (period["opening"] + period["deliveries"]
                      - period["sales"] + period["adjust"])
        return {"tank": tank, "kind": kind, "opened": period["opened"],
                "closed": time.mktime(self.c.now()),
                "requested": period.get("requested", period["opened"]),
                "opening": period["opening"],
                "water_open": period["water_open"],
                "temp_open": period["temp_open"], "temp_close": self.c.product_temperature(tank),
                "deliveries": period["deliveries"],
                "ticketed": period["ticketed"], "sales": period["sales"],
                "adjust": period["adjust"], "calculated": calculated,
                "physical": physical, "water": water,
                "variance": physical - calculated}

    def row(self, tank, kind="shift", previous=False, day=None):
        """The row a report is asking for: the closed one, or the running one.

        "a Shift Reconciliation Report for the previous shift" if there is
        one, so the report is not empty on a console nobody has closed yet.

        `day` is the third way of asking, and it belongs to the daily
        reconciliation report alone -- see `row_on`.
        """
        if not self.covers(tank):
            # "a BIR report will be generated for this tank" is what the flag
            # being YES does, so NO is a tank with no report at all. Every
            # report already draws its own no-data line for this.
            return None
        if not self.map_complete():
            # "BIR will not produce reports while the meter map is
            # incomplete", 576013-818 p.12-8, and it is the whole of that
            # chapter's Example 5: four tanks, all with METER DATA PRESENT
            # YES, and every one of them prints EMPTY because one meter
            # belongs to a manifolded pair on 1XX software. FIDELITY G8.
            return None
        if day is not None and kind == "daily":
            return self.row_on(tank, day)
        if previous:
            return self.last(tank, kind)
        return self.current(tank, kind)

    def row_on(self, tank, when):
        """The daily row for one calendar day, which is what SELECT DAY picks.

        576013-610 Rev AC p.28-3: "To select a different date, press CHANGE,
        enter the date, and then press ENTER." The date meant is the CLOSING
        date -- p.28-20 asks for "the desired closing date" in as many words
        -- so a row is matched on when it closed, and today matches the day
        still in progress. A day this console no longer holds has no row and
        the report prints its own no-data line. See FIDELITY Q1.
        """
        want = time.strftime("%Y%m%d", time.localtime(when))
        if want == time.strftime("%Y%m%d", self.c.now()):
            return self.current(tank, "daily")
        return self._closed_on(tank, when)

    def _closed_on(self, tank, when):
        """The closed daily row whose closing date is that day, or None."""
        want = time.strftime("%Y%m%d", time.localtime(when))
        for row in self.closed.get((tank, "daily")) or []:
            if time.strftime("%Y%m%d", time.localtime(row["closed"])) == want:
                return row
        return None

    def book(self, row):
        """Book inventory: "opening gauged volume - metered sales + total
        ticketed delivery volume + manual adjustments"."""
        return row["opening"] - row["sales"] + row["ticketed"] + row["adjust"]

    def analysis(self, row):
        """The seven numbers a Variance Analysis Report is made of.

        Both of the first two are SIGNED against the manual's own printed
        samples, and the prose is not unanimous about either. See FIDELITY
        G9; the rule is the file's own -- an example is a test somebody else
        already wrote, and a sample beats a sentence.

        BOOK VARIANCE is book minus gauged. Two pages say so -- 576013-610
        p.28-14, "(opening gauged volume-metered sales+total ticketed
        delivery volume+manual adjustments)-(closing gauged volume)", and
        576013-623 p.5-6, "the difference between book inventory and closing
        gauged volume" -- and two say the reverse, p.28-10's own bullet under
        BOOK VARIANCE REPORTS and p.28-11's STEP walk. Both samples settle
        it: `BOOK INV 9704` over `GAUGED INV 8904` prints `VAR : 800 GAL`,
        and the Variance Analysis sample prints `BOOK VAR : 800 GAL` for the
        same site. A tank 800 gallons short of its book reads +800, which is
        also the sign a loss report wants.

        DELIVERY VARIANCE is defined in opposite directions for two
        different reports, and each sample agrees with its own page. p.28-14
        gives the Variance Analysis "difference between ticketed and gauged
        volumes" and its sample prints `DLVY VAR : -99` against a ticket of
        800 and a gauge of 899; p.28-7 gives the Delivery Variance report
        "difference between gauged and ticketed" and its sample prints `DLVY
        VAR : 99` for the same pair. So there are two numbers here, not one
        with a doubtful sign, and each report takes its own.

        The third check is that they COMPOSE: "sales variance, difference
        between book variance and delivery variance" is 800 - (-99) = 899,
        which is the sample's `SALE VAR : 899 GAL`. It reproduces only with
        both signs right, and reproduced neither before.
        """
        book_var = self.book(row) - row["physical"]
        sales = row["sales"]
        delivery_var = row["ticketed"] - row["deliveries"]
        # "temperature variance, change in volume related to change in
        # temperature", which is the tank's own coefficient over the period
        # -- THIS row's tank. It read `limit("609", 1)`, tank 1's, and applied
        # it to every tank on the site: a four-tank truck stop worked out its
        # diesel variance on the coefficient of whatever was in tank 1. And
        # the docstring beside it said "the tank's own" all along. FIDELITY Y6.
        coeff = self.c.tank_coefficient(row.get("tank") or 1)
        temp_var = (row["physical"] * coeff
                    * (row["temp_close"] - row["temp_open"]))
        return {"book_var": book_var,
                "book_pct": (book_var / sales * 100.0) if sales else 0.0,
                "delivery_var": delivery_var,
                # p.28-7's, for the report that asks it that way round
                "gauged_delivery_var": -delivery_var,
                "sales_var": book_var - delivery_var,
                "temp_var": temp_var,
                "water_change": row["water"] - row["water_open"],
                "unexplained": book_var - delivery_var - temp_var}

    def threshold(self, row):
        """"the alarm threshold plus offset": the default the manual gives is
        "1.00% of throughput plus 130 gallons (492 litres) offset"."""
        pct = self.c.limit("798", 0)
        offset = self.c.limit("799", 0)
        pct = 1.0 if pct is None else pct
        offset = 130.0 if offset is None else offset
        return row["sales"] * pct / 100.0 + offset

    def report(self, tanks, previous=False, kind="shift"):
        """IC03, the row report the wire asks for, in its own columns."""
        which = "PREVIOUS" if previous else "CURRENT"
        out = [f"{which} {kind.upper()} RECONCILIATION REPORT", ""]
        for tank in self.report_tanks(tanks):
            row = self.row(tank, kind, previous)
            out += self.tank_lines(tank)
            out.append("DATE TIME  OPENING DLVRIES   SALES  ADJUST"
                       "  CALC'D PHYSICL WATER   VAR")
            if row is None:
                out.append("  NO SHIFT DATA AVAILABLE")
                out.append("")
                continue
            for when in (row["opened"], row["closed"]):
                out.append(clock_words(when))
            out.append(f"{row['opening']:9.0f}{row['deliveries']:8.0f}"
                       f"{row['sales']:8.0f}{row['adjust']:8.0f}"
                       f"{row['calculated']:8.0f}{row['physical']:8.0f}"
                       f"{row['water']:6.2f}{row['variance']:6.0f}")
            out.append("")
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    # ---- the reports sections 7.5 and 7.6 ask for --------------------------
    #
    # A "Row" report is the wide table with one line per period; a "Column"
    # report is the same numbers written down the page as labels and values.
    # Two layouts over one set of figures, which is why the manual gives every
    # pair the same eight floats and a different picture.

    ROW_HEAD = ("DATE TIME     OPENING DLVRIES  SALES ADJUST INVNTRY"
                " INVNTRY  HEIGHT VARIANCE")
    ROW_HEAD2 = ("              VOLUME METERED MANUAL CALCD  PHYSICAL"
                 " WATER")

    def daily_history(self, tank):
        """Every daily row this console still holds, oldest first, with the
        day in progress at the end.

        I@A400 and C09 both print this table, and both printed ONE row: the
        current day, through `row()`. 576013-818 p.12-5 prints twenty-four
        rows for one tank under a title that names thirty-one or sixty-two,
        and p.12-3 says what they are for -- "An examination of the BIR daily
        history table will indicate whether a large periodic variance is a
        summation of smaller daily variances with the same sign or whether
        there are isolated instances of large daily variances." One row
        cannot answer that question. See FIDELITY G12.
        """
        if not self.covers(tank) or not self.map_complete():
            return []
        rows = list(reversed(self.closed.get((tank, "daily")) or []))
        return rows + [self.current(tank, "daily")]

    # I@A400's own columns, measured off 576013-818 p.12-5's word boxes
    # rather than counted: the three stamps sit at 0, 11 and 22, the two
    # volumes are eight wide and the last four are seven.
    HISTORY_HEAD = ("REQUEST ST  STRT TIME   END TIME STRT_VL  END_VL"
                    "  SALES  DELIV OFFSET VARIEN")

    @staticmethod
    def _stamp(when):
        return time.strftime("%y%m%d%H%M", time.localtime(when))

    def history_line(self, row, ticketed=False):
        """One row of I@A400's table.

        OFFSET is the row's manual adjustment, and the sample is what says
        so: the table carries five quantities and the console's variance is
        made of five terms, and `END_VL - (STRT_VL - SALES + DELIV +
        OFFSET)` reproduces every printed VARIEN on the page. p.12-5's
        9711110200 is 4194.3 - 0.0 + 6618.2 against 9586.9, and -1225.5 is
        what it prints.
        """
        deliv = row["ticketed"] if ticketed else row["deliveries"]
        return ("%s %s %s" % (self._stamp(row.get("requested",
                                                  row["opened"])),
                              self._stamp(row["opened"]),
                              self._stamp(row["closed"]))
                + "%8.1f%8.1f" % (row["opening"], row["physical"])
                + "%7.1f%7.1f%7.1f%7.1f" % (row["sales"], deliv,
                                            row["adjust"], row["variance"]))

    # 576013-818 p.12-23's Example 4 prints this between two rows whose
    # requested days are not consecutive. ONE marker per gap however wide it
    # is: its 9608040000 and 9608060000 skip one day and get one, and its
    # 9608070000 and 9608100000 skip two and get one.
    MISSING_DATA = "---- MISSING DATA ----"

    @staticmethod
    def _history_day(row):
        """The calendar day a row was DUE, which is the day that can go
        missing: this console reconciles on a schedule, so a day with no row
        is a day the schedule came round and nothing closed.
        """
        when = time.localtime(row.get("requested", row["opened"]))
        return datetime.date(when.tm_year, when.tm_mon,
                             when.tm_mday).toordinal()

    def history_lines(self, tank, ticketed=False):
        """One tank's daily rows, with the days between them marked.

        "Customer complaint: Missing days in reconciliation" is the whole of
        p.12-22's Example 4, and this report is what shows it: a console that
        was switched off, or suspended, or whose clock jumped, closes nothing
        on the days it missed and the table simply skips them. This console
        can produce them -- `_scheduled` looks a day back and no further, so
        a clock that moves a week forward in one tick leaves five days with
        no close -- and printed the rows end to end, leaving a reader to
        compare consecutive REQUEST ST stamps by eye down thirty-one of
        them. See FIDELITY G8.
        """
        out, last = [], None
        for row in self.daily_history(tank):
            day = self._history_day(row)
            if last is not None and day - last > 1:
                out.append(self.MISSING_DATA)
            last = day
            out.append(self.history_line(row, ticketed))
        return out

    def history_report(self, tanks, ticketed=False):
        """I@A400, the daily reconciliation list.

        "I@A400 DAILY RECONCILIATION LIST FOR LAST 31 DAYS (62 ON NEWER
        VERSIONS)", and the sample under that title is twenty-four rows of
        one tank's days. This printed the SHIFT report -- one period, the
        wrong period, in the wrong layout -- under the right heading. See
        FIDELITY G12 and S11.

        The blank lines are the page's leading rather than a guess: p.12-5
        sets its lines 9.0 points apart and leaves 18.0 twice, before the
        tank line and before the column header.
        """
        out = ["BASIC_RECONCILIATION HISTORY", ""]
        for tank in self.report_tanks(tanks):
            out += self.tank_lines(tank) + ["", self.HISTORY_HEAD]
            if not self.daily_history(tank):
                # p.12-27's own word for a history with nothing in it, the
                # same one I@A900 prints over its column header
                out.append("EMPTY")
            out += self.history_lines(tank, ticketed)
            out.append("")
        return chr(10).join(out)

    def history_figures(self, tank, row):
        """C09's ten floats, in the manual's own order.

        "1. Start height 2. End height 3. Start Volume 4. End Volume
        5. Metered sales (dispensed volume) 6. Ticket Delivery 7. Gauged
        Delivery 8. Offset volume 9. Variance (calculated with ticketed
        volume) 10. Variance (calculated with gauged volume)" -- so the
        computer format carries BOTH variances and the display format picks
        one with its `D` flag.
        """
        return [self.c.stick_height(tank, row["opening"]),
                self.c.stick_height(tank, row["physical"]),
                row["opening"], row["physical"], row["sales"],
                row["ticketed"], row["deliveries"], row["adjust"],
                row["physical"] - self.book(row), row["variance"]]

    def period_days(self, tank, previous=False):
        """The day rows inside a period, for the reports that print one line
        per reconciliation day.

        Per TANK, which is the whole point: a periodic report lists every
        tank, and each of them has its own days. Sharing one tank's rows
        across all of them would print tank 1's figures four times under four
        different labels, which is what the first cut of this did.

        And it is the DAYS in the period, which this returned one of -- the
        periodic report's own daily row -- so `multi` printed a single line
        on five C-codes and their TOTALS branch was unreachable.
        """
        span = self.row(tank, "periodic", previous)
        if span is None:
            return []
        rows = [r for r in reversed(self.closed.get((tank, "daily")) or ())
                if span["opened"] <= r["closed"] <= span["closed"]]
        if not previous:
            rows.append(self.current(tank, "daily"))
        return rows

    def row_report(self, tanks, kind="daily", previous=False, multi=False):
        """C01, C05, C07: one line per period under the manual's two-line head."""
        title = {"daily": "DAILY", "weekly": "WEEKLY",
                 "periodic": "PERIODIC", "shift": "SHIFT"}[kind]
        head = (title + " RECONCILIATION REPORT" if kind == "daily"
                else ("PREVIOUS" if previous else "CURRENT") + " " + title
                + " RECONCILIATION REPORT")
        out = [head]
        for tank in self.report_tanks(tanks):
            out += self.tank_lines(tank)
            out.append(self.ROW_HEAD)
            out.append(self.ROW_HEAD2)
            rows = (self.period_days(tank, previous) if multi
                    else [self.row(tank, kind, previous)])
            got = [r for r in rows if r]
            if not got:
                out.append("  NO DATA AVAILABLE")
                continue
            for row in got:
                out.append(self._row_line(row))
            if len(got) > 1:
                out.append("TOTALS        "
                           + self._row_line(self._totals(got), stamp=False))
            out.append("")
            out.append("THRESHOLD: %.0f" % self.threshold(got[-1]))
        out.append("")
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    def _row_line(self, row, stamp=True):
        when = ("%-14.14s" % clock_words(row["closed"])[:12]) if stamp else ""
        return (when
                + "%7.0f" % row["opening"] + "%8.0f" % row["deliveries"]
                + "%7.0f" % row["sales"] + "%7.0f" % row["adjust"]
                + "%8.0f" % row["calculated"] + "%8.0f" % row["physical"]
                + "%7.2f" % row["water"] + "%9.0f" % row["variance"])

    @staticmethod
    def _totals(rows):
        """The TOTALS line: opened where the first did, closed where the last
        did, and everything between summed."""
        first, last = rows[0], rows[-1]
        out = dict(last)
        out["opening"] = first["opening"]
        for key in ("deliveries", "sales", "adjust"):
            out[key] = sum(r[key] for r in rows)
        return out

    def column_report(self, tanks, kind="daily", previous=False,
                      threshold=False):
        """C02, C06, C08: the same figures written down the page.

        Not the row report with different spacing -- a different shape. The
        manual gives it its own labels and it names the closing date and time
        at the bottom, which the row form never prints.
        """
        title = {"daily": "DAILY", "weekly": "WEEKLY",
                 "periodic": "PERIODIC", "shift": "SHIFT"}[kind]
        head = (title + " RECONCILIATION REPORT" if kind == "daily"
                else ("PREVIOUS" if previous else "CURRENT") + " " + title
                + " RECONCILIATION REPORT")
        out = [head]
        for tank in tanks:
            row = self.row(tank, kind, previous)
            label = self.c.text("602", tank) or "TANK %d" % tank
            # 576013-635 Rev AA p.575 holds every value in this block
            # right against column 27, where this ran them on at 18
            out.append("%-18s%10s" % ("PRODUCT", label))
            if row is None:
                out.append("NO DATA AVAILABLE")
                continue
            pairs = [("OPENING DATE", clock_words(row["opened"])[:12]),
                     ("OPENING TIME", clock_words(row["opened"])[13:]),
                     ("OPENING VOLUME", "%.0f" % row["opening"]),
                     ("DELIVERIES", "%.0f" % row["deliveries"]),
                     ("METERED SALES", "%.0f" % row["sales"]),
                     ("MANUAL ADJUST", "%.0f" % row["adjust"]),
                     ("CALC'D INVNTRY", "%.0f" % row["calculated"]),
                     ("PHYSICAL INVNTRY", "%.0f" % row["physical"]),
                     ("WATER  HEIGHT", "%.2f" % row["water"]),
                     ("VARIANCE", "%.0f" % row["variance"])]
            if threshold:
                # only the periodic column report carries it
                pairs.append(("THRESHOLD", "%.0f" % self.threshold(row)))
            pairs += [("CLOSING DATE", clock_words(row["closed"])[:12]),
                      ("CLOSING TIME", clock_words(row["closed"])[13:])]
            for name, value in pairs:
                out.append("%-18s%10s" % (name, value))
        out.append("")
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    def figures(self, row):
        """The eight floats every reconciliation record carries."""
        return [row["opening"], row["deliveries"], row["sales"],
                row["adjust"], row["calculated"], row["physical"],
                row["water"], row["variance"]]

    # 576013-635 Rev AA p.589: TIME at 8, OPENING at 17, METERED at 25,
    # TICKET at 34, MAN at 43, CLS at 47, BOOK at 51, GAUGED at 57 and DAILY
    # at 70, with the second word of each stacked under the first
    BOOK_HEAD = ("DATE    TIME     OPENING METERED  TICKET   MAN CLS BOOK"
                 "  GAUGED       DAILY")
    BOOK_HEAD2 = ("                  VOLUME   SALES    DLVY   ADJ  INVNTRY"
                  " INVNTRY      VARIANCE")

    def book_figures(self, row):
        """C10, C11, C12's nine.

        The book inventory rather than the gauged deliveries, so a ticket that
        never arrived shows up as variance instead of vanishing.
        """
        book = self.book(row)
        # book minus gauged, and SIGNED: this had the subtraction the other
        # way round and then took the magnitude of the percentage, so the
        # wire and the paper disagreed about a number they read off one row.
        # See FIDELITY G9.
        var = book - row["physical"]
        pct = (var / row["sales"] * 100.0) if row["sales"] else 0.0
        return [row["opening"], row["sales"], row["ticketed"], row["adjust"],
                book, row["physical"], row["water"], var, pct]

    def book_report(self, tanks, kind="periodic", previous=False,
                    multi=False):
        """C10, C11, C12: book variance over a period."""
        title = {"periodic": "CURRENT PERIOD", "weekly": "CURRENT WEEK",
                 "daily": "DAILY"}[kind]
        if previous:
            title = title.replace("CURRENT", "PREVIOUS")
        out = [title + " BOOK VARIANCE"]
        for tank in self.report_tanks(tanks):
            out += self.tank_lines(tank)
            out.append(self.BOOK_HEAD)
            out.append(self.BOOK_HEAD2)
            rows = (self.period_days(tank, previous) if multi
                    else [self.row(tank, kind, previous)])
            got = [r for r in rows if r]
            if not got:
                out.append("  NO DATA AVAILABLE")
                continue
            for row in got:
                out.append(self._book_line(row))
            if len(got) > 1:
                out.append("TOTALS        "
                           + self._book_line(self._totals(got), stamp=False))
            out.append("")
            out.append("THRESHOLD: %.0f" % self.threshold(got[-1]))
        out.append("")
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    def _book_line(self, row, stamp=True):
        f = self.book_figures(row)
        when = ("%-14.14s" % clock_words(row["closed"])[:12]) if stamp else ""
        # "-4= 0.13%": the variance, an equals sign, then the percent
        return (when + "%7.0f" % f[0] + "%8.0f" % f[1] + "%7.0f" % f[2]
                + "%5.0f" % f[3] + "%8.0f" % f[4] + "%8.0f" % f[5]
                + "%6.0f=" % f[7] + "%6.2f%%" % f[8])

    # p.594: BOOK at 20, DLVY at 28, SALES at 35, BK_VAR at 41, MTR at
    # 50, TEMP at 55, VAP at 62, WATER at 68 and UNEX at 75
    ANALYSIS_HEAD = ("DATE    TIME        BOOK    DLVY   SALES BK_VAR   MTR"
                     "  TEMP   VAP   WATER  UNEX")
    ANALYSIS_HEAD2 = ("                     VAR     VAR     VAR    %     VAR"
                      "   VAR   VAR    CHG    VAR")

    def analysis_figures(self, row):
        """C20 to C25's nine, in the order the MANUAL'S NOTES list them.

        Which is NOT the order its own columns print. The notes give book,
        delivery, sales, percent, temperature, water, unexplained, and then
        meter and vapour appended at 8 and 9 "(Version 29)"; the printed
        header reads BOOK DLVY SALES BK_VAR% MTR TEMP VAP WATER UNEX. So the
        wire follows the notes and the printout follows the header, and they
        are not the same sequence. Nothing here measures a meter variance or
        a vapour variance, so those two are zero.
        """
        a = self.analysis(row)
        return [a["book_var"], a["delivery_var"], a["sales_var"],
                a["book_pct"], a["temp_var"], a["water_change"],
                a["unexplained"], 0.0, 0.0]

    def analysis_report(self, tanks, kind="periodic", previous=False,
                        multi=False):
        """C20, C21, C22 and C25."""
        title = {"periodic": "CURRENT PERIOD", "weekly": "CURRENT WEEK",
                 "daily": "DAILY"}[kind]
        if previous:
            title = title.replace("CURRENT", "PREVIOUS")
        out = [title + " VARIANCE ANALYSIS"]
        for tank in self.report_tanks(tanks):
            out += self.tank_lines(tank)
            out.append(self.ANALYSIS_HEAD)
            out.append(self.ANALYSIS_HEAD2)
            rows = (self.period_days(tank, previous) if multi
                    else [self.row(tank, kind, previous)])
            got = [r for r in rows if r]
            if not got:
                out.append("  NO DATA AVAILABLE")
                continue
            for row in got:
                f = self.analysis_figures(row)
                out.append(("%-14.14s" % clock_words(row["closed"])[:12])
                           + "%5.0f" % f[0] + "%5.0f" % f[1] + "%6.0f" % f[2]
                           + "%7.2f" % f[3] + "%4.0f" % f[7] + "%5.0f" % f[4]
                           + "%4.0f" % f[8] + "%6.0f" % f[5]
                           + "%6.0f" % f[6])
        return chr(10).join(out)

    # ----------------------------------------------------------------
    # I@A002's grid, 576013-818 p.12-14
    # ----------------------------------------------------------------
    # "FP|     METER": the ROW is a fueling position and the COLUMN is one
    # of the six logical meters a position can carry -- "The console is
    # limited to 6 meters (M) per FP". Each cell is 10 wide with one space
    # between, which is what the two-row column header the captured console
    # prints is spaced for.
    BALLOT_COLUMNS = 6
    BALLOT_RULE = "---+" + "-" * 66
    BALLOT_EMPTY = "   -:-/-/-"
    BALLOT_NO_STAMP = "     *    "
    # 8 is not a tank on a sixteen-tank console and the summary block's
    # cells are full of it, so it reads as an empty vote slot. See UNKNOWNS.
    BALLOT_NO_TANK = 8
    # Three vote slots, and the page reads them as votes for a tank:
    # "3/3/3 = three votes for tank 4". This console has no auto-mapping
    # algorithm to disagree with itself, so a mapped meter votes for its own
    # tank three times, which is what every unanimous cell in the sample
    # looks like. The digit after `>` is the one field on the page nobody
    # explains -- see UNKNOWNS.
    BALLOT_VOTES = 3

    def _ballot_stamp(self, key):
        """"date of last reported event for this meter", or the `*`."""
        at = self.reported_at.get(key)
        return self._stamp(at) if at else self.BALLOT_NO_STAMP

    def ballot_rows(self):
        """The auto-mapping ballot, one two-line block per fueling position.

        The report the guide's own instruction hangs off: "Look for unmapped
        or retired meters". This printed one row per METER with the meter
        number where the page puts the tank, which is a shape the page does
        not have and a cell the page's legend contradicts. FIDELITY S11, and
        it needed G7's key first: a row is a fueling position, so a store
        that cannot tell one position's meter 10 from another's cannot draw
        one.
        """
        out = []
        positions = {}
        for key, tank in self.c.meters.items():
            if int(tank) > 0:
                positions.setdefault(key.fp, []).append((key, int(tank)))
        for fp in sorted(positions):
            cells, stamps = [], []
            for key, tank in sorted(positions[fp])[:self.BALLOT_COLUMNS]:
                # "Tank numbers are zero based (e.g., tank 1 is 0 ... tank 4
                # is 3)", the page's own footnote, and the reason a cell
                # reading `M3` is a TANK and not a meter: the legend reads
                # one for you, "M3 = mapped to tank 4 (3+1)".
                zero = tank - 1
                votes = "/".join([str(zero)] * self.BALLOT_VOTES)
                cells.append("%10s" % f"M{zero}>{self.BALLOT_VOTES}:{votes}")
                stamps.append(self._ballot_stamp(key))
            while len(cells) < self.BALLOT_COLUMNS:
                cells.append(self.BALLOT_EMPTY)
                stamps.append(self.BALLOT_NO_STAMP)
            out.append(f"{fp:3d}|" + " ".join(cells))
            out.append("   |" + " ".join(stamps))
            out.append(self.BALLOT_RULE)
        out += self.ballot_summary()
        return out

    # The summary block sits under the grid at a row of its own, and the
    # sample's is `15`. Nothing on the page says what chooses that number.
    BALLOT_SUMMARY_FP = 15
    BALLOT_SUMMARY_HEAD = "    Unmapped   Retired    Probeless"

    def ballot_summary(self):
        """`U`, `R` and `X`: "U = unmapped, R = retired, X = probe".

        The three states a grid cell cannot show, because a meter in any of
        them has no tank to put in one. Their vote slots print as no votes:
        this console maps by hand and over the wire, so there is no ballot
        behind an unmapped meter to print. See UNKNOWNS.
        """
        states = [
            ("U", self.unmapped_meters()),
            ("R", [m for m in self.reported_at if self.retired(m)]),
            ("X", [m for m, tank in self.c.meters.items() if int(tank) < 0]),
        ]
        if not any(found for _letter, found in states):
            return []
        none = "/".join([str(self.BALLOT_NO_TANK)] * self.BALLOT_VOTES)
        cells, stamps = [], []
        for letter, found in states:
            if not found:
                cells.append(self.BALLOT_EMPTY)
                stamps.append(self.BALLOT_NO_STAMP)
                continue
            cells.append("%10s" % f"{letter} >0:{none}")
            seen = [self.reported_at[m] for m in found
                    if m in self.reported_at]
            stamps.append(self._stamp(max(seen)) if seen
                          else self.BALLOT_NO_STAMP)
        while len(cells) < self.BALLOT_COLUMNS:
            cells.append(self.BALLOT_EMPTY)
            stamps.append(self.BALLOT_NO_STAMP)
        return [self.BALLOT_SUMMARY_HEAD,
                self.BALLOT_RULE,
                f"{self.BALLOT_SUMMARY_FP:3d}|" + " ".join(cells),
                "   |" + " ".join(stamps),
                self.BALLOT_RULE]

    def meter_report(self):
        """What each meter has put through itself, and where it goes."""
        out = ["METER TOTALS", "", "METER  TANK      GALLONS"]
        # One row per METER, which is per identity: the same meter number on
        # two fueling positions is two meters and sells out of two tanks.
        for key in sorted(set(self.c.meters) | set(self.totals)):
            tank = self.c.meters.get(key, 0)
            out.append(f"{key.meter:5d}{tank:6d}"
                       f"{self.totals.get(key, 0.0):13.1f}")
        return chr(10).join(out)
