"""Discrete sales: a nozzle lifted, N gallons through one meter, a nozzle
hung up -- and the alarm a DIM that has stopped reporting them earns.

`meter_flow` is a RATE, gallons an hour, and everything downstream of it
was written for one: BIR's `_dispense` draws the tank down and books the
sale, `note_reported` stamps the meter, CSLD's `busy` reads it. A sale is
the same rate switched on for exactly as long as N gallons take, so it goes
through the same door and leaves the same records -- a start event, an end
event with what went out, a stamp on the meter -- and the rate goes back to
whatever the bench had it at when the nozzle hangs up. BENCH.md D2.

19/04 TRANSACTION ALARM is the other half. 576013-623 p.5-17: "BDIM
Transaction Alarm Delay is available only if there is a Block DIM card
installed in the console. This feature lets you enter a delay (of from 5 to
999 hours) before posting a Block DIM Transaction Alarm ... Enter 000 to
disable this feature." The delay was stored and reached nothing (FIDELITY
N3a) because nothing was a transaction. A DIM that once reported and has
been silent for the delay is the condition. BENCH.md D3.

**A sale lifts the line's handle**, which it did not used to. A nozzle is a
request to the pump -- 576013-344 Rev H p.5: "If a dispense request occurs
during any test, the test is aborted and the pump is turned On to commence
dispensing. The testing will restart from the beginning once dispensing
stops", and "A gross test always follows the completion of a dispense".
Without this a line leak test ran straight through a busy forecourt, which
taught a technician the opposite of p.21's own answer to why a periodic
test never finishes: "If the site is extremely busy ... there may not be
sufficient idle time to complete a Periodic or Annual test unless the
station is shut down."

The handle is a REFCOUNT and not a flag, because a line has more than one
nozzle on it and a blended nozzle is on two lines at once. It is also only
dropped on lines these sales raised: a technician holding a handle up from
the bench's own pill keeps it up when a car drives away.
"""
import time

from .meterid import MeterDict, meter_key


class Sale:
    def __init__(self, meter, gallons, rate, started, by=None):
        self.meter = meter
        # Who lifted this nozzle: None for a hand on the bench, "traffic"
        # for the generator. Switching the generator off has to hang up
        # what the generator is holding and nothing else -- it used to
        # stop every sale on the site, so a technician's own fill, lifted
        # by hand before the switch was touched, was hung up at zero.
        self.by = by
        self.gallons = float(gallons)
        self.rate = float(rate)             # gallons an HOUR, like meter_flow
        self.sold = 0.0
        self.started = started
        # (kind, number) of every line this sale put a handle up on, and
        # which of those it was the one to raise
        self.lines = ()
        # the [from, to] this sale occupies in `Sales.spans`, shortened if
        # the nozzle goes back on the cradle early
        self.span = None

    @property
    def done(self):
        return self.sold >= self.gallons - 1e-9


class Blend:
    """One blended nozzle in somebody's hand: several meters, one grade.

    The console is never told this exists. What it sees is the component
    sales, each on its own meter against its own tank, which is exactly
    what a real blender's POS reports -- see `Console.blend_meters`. This
    is the bench's handle on the group, so one click hangs all of them up
    and one line of text says how the fill is going.
    """

    def __init__(self, meter, parts):
        self.meter = meter
        self.parts = list(parts)            # [Sale]
        self.by = None                      # see `Sale.by`

    @property
    def gallons(self):
        return sum(s.gallons for s in self.parts)

    @property
    def sold(self):
        return sum(s.sold for s in self.parts)

    @property
    def done(self):
        return all(s.done for s in self.parts)


class Sales:
    """The nozzles in hand right now, and the alarm for a DIM gone quiet."""

    # ten gallons a minute, which is a modern nozzle's rated flow
    RATE = 600.0

    def __init__(self, console):
        self.c = console
        # keyed by the meter's whole identity, and a bare number still
        # reaches the default board's meter, like every other meter store
        self.running = MeterDict()     # meter -> Sale
        self._was = MeterDict()        # meter -> the flow the bench had set
        self.blends = MeterDict()      # blended nozzle -> Blend
        # (kind, number) -> how many sales are holding this handle up, and
        # which of those handles these sales were the ones to raise
        self._handles = {}
        self._raised = set()
        # meter -> [[from, to]] in CONSOLE time, every span this meter has
        # flowed for. `meter_flow` is a rate averaged over an interval, and
        # at 36,000x an interval is seven hours -- so "the rate is not zero"
        # stopped being a usable answer to "was the tank busy", and anything
        # that needs the shape of the day rather than a number reads these.
        # See `Console.activity_spans`.
        self.spans = MeterDict()
        # meter -> gallons passed this interval by transactions that began
        # AND ended inside it, which is every transaction once the clock is
        # running fast enough
        self.served = MeterDict()
        self._serving = set()

    # how far back the spans are worth keeping: CSLD asks about the last
    # twenty-four hours and nothing asks about more
    SPAN_HOURS = 30.0

    # ---- when a meter was flowing -------------------------------------------
    def _note_span(self, meter, start, end):
        """Record a flow span, and return it so a hang-up can shorten it."""
        meter = meter_key(meter)
        span = [float(start), float(end)]
        rows = self.spans.setdefault(meter, [])
        rows.append(span)
        cutoff = float(start) - self.SPAN_HOURS * 3600.0
        if len(rows) > 8:
            rows[:] = [s for s in rows if s[1] >= cutoff]
        return span

    def free_at(self, meter, at):
        """Is this nozzle out of somebody's hand at that console time?

        A blended nozzle is free when every component of it is: the
        dispenser cannot make the mix out of a meter that is already
        pouring into somebody else's car.
        """
        meter = meter_key(meter)
        parts = self.c.blend_meters(meter)
        if parts:
            return all(self.free_at(component, at)
                       for component, _frac in parts)
        for start, end in self.spans.get(meter, ()):
            if start <= at < end:
                return False
        return True

    def serve(self, meter, gallons, rate, at):
        """One whole transaction, start to finish, inside one interval.

        The nozzle is lifted and hung up between two ticks of the console's
        clock, so there is nothing to hold in `running` -- what is left of
        it is the gallons, which go through `meter_flow` with everything
        else, and the span, which is when they went.

        This is the honest form of the degradation a fast clock forces. The
        GALLONS stay exact at any speed, so BIR, the tanks and the
        reconciliation stay right; what coarsens is the transaction
        granularity, because several cars inside one interval reach the
        meter events table as one start and one end.
        """
        meter = meter_key(meter)
        gallons, rate = float(gallons), float(rate)
        if gallons <= 0 or rate <= 0:
            return None
        self.served[meter] = self.served.get(meter, 0.0) + gallons
        self._serving.add(meter)
        return self._note_span(meter, at, at + gallons / rate * 3600.0)

    # ---- the nozzle -----------------------------------------------------------
    def start(self, meter, gallons, rate=None, at=None, by=None):
        """Lift the nozzle: `gallons` through `meter` at `rate` gal/h.

        A blended nozzle runs its component meters instead, each at its
        share of the gallons and its share of the rate, so the fill takes
        the same time it would through one. `at` is when the nozzle came
        off the cradle in CONSOLE time, for a car that arrived part of the
        way through an interval; it defaults to now, which is what a hand
        on the bench means.
        """
        meter = meter_key(meter)
        gallons = float(gallons)
        if gallons <= 0:
            return None
        parts = self.c.blend_meters(meter)
        if parts:
            if meter in self.blends:
                self.stop(meter)
            made = []
            for component, frac in parts:
                sale = self._start_one(component, gallons * frac,
                                       (rate or self.RATE) * frac, at,
                                       by=by)
                if sale is not None:
                    made.append(sale)
            if not made:
                return None
            blend = Blend(meter, made)
            blend.by = by
            self.blends[meter] = blend
            return blend
        if meter in self.running:
            self.stop(meter)
        return self._start_one(meter, gallons, rate or self.RATE, at,
                              by=by)

    def _start_one(self, meter, gallons, rate, at=None, by=None):
        """One meter's own sale, blended or not."""
        meter = meter_key(meter)
        if gallons <= 0:
            return None
        if meter in self.running:
            self.stop(meter)
        began = time.mktime(self.c.now()) if at is None else float(at)
        sale = Sale(meter, gallons, rate, began, by)
        self.running[meter] = sale
        self._was[meter] = self.c.meter_flow.get(meter, 0.0)
        self.c.meter_flow[meter] = sale.rate
        sale.lines = self._lift(meter)
        # the span this fill WILL take if it runs to the end; hanging up
        # early shortens it
        sale.span = self._note_span(meter, began,
                                    began + gallons / rate * 3600.0
                                    if rate > 0 else began)
        return sale

    def stop(self, meter):
        """Hang the nozzle up, however far the sale got."""
        meter = meter_key(meter)
        blend = self.blends.pop(meter, None)
        if blend is not None:
            for sale in blend.parts:
                self.stop(sale.meter)
            return blend
        sale = self.running.pop(meter, None)
        if sale is None:
            return None
        self._drop(sale)
        if sale.span is not None:
            # the fill stopped where it stopped, so the span it was going to
            # take is not the span it took
            sale.span[1] = min(sale.span[1], time.mktime(self.c.now()))
        was = self._was.pop(meter, 0.0)
        if was:
            self.c.meter_flow[meter] = was
        else:
            self.c.meter_flow[meter] = 0.0
            # the POS sends the End Event when the nozzle hangs up, not on
            # the console's next look
            self.c.bir.end_event(meter)
        return sale

    def busy(self, meter):
        """Is this nozzle -- blended or plain -- in somebody's hand?"""
        meter = meter_key(meter)
        return meter in self.blends or meter in self.running

    # ---- the handle the pump answers -----------------------------------------
    def _lift(self, meter):
        """Put the handle up on every line this meter's product comes up.

        Returns the lines this sale is holding, which is only the ones it
        RAISED: a handle already up belongs to whoever put it there.
        """
        mine = []
        for key in self.c.lines_for_meter(meter):
            count = self._handles.get(key, 0)
            self._handles[key] = count + 1
            mine.append(key)
            if count:
                continue
            if self.c.lines.line(*key).handle:
                # already up, and not by us: the bench's own HANDLE pill is
                # a technician standing there with it, and a car driving
                # away does not take it out of his hand
                self._raised.discard(key)
            else:
                self.c.lines.handle(key[0], key[1], True)
                self._raised.add(key)
        return tuple(mine)

    def _drop(self, sale):
        """Hang up: the last sale off a line puts its handle down."""
        for key in sale.lines:
            count = self._handles.get(key, 0) - 1
            if count > 0:
                self._handles[key] = count
                continue
            self._handles.pop(key, None)
            if key in self._raised:
                self._raised.discard(key)
                self.c.lines.handle(key[0], key[1], False)

    def resync(self):
        """Put the handles back after the console has been rebooted.

        `Lines` is rebuilt by the reset a reboot goes through, so every
        handle comes back down -- but a nozzle in somebody's hand is still
        in it, which is exactly why the sales themselves are carried over.
        """
        self._handles = {}
        self._raised = set()
        for meter, sale in sorted(self.running.items()):
            sale.lines = self._lift(meter)

    def tick(self, hours):
        """Count what the open nozzles pass in this interval.

        Called BEFORE BIR looks, and the flow it leaves for BIR to read is
        the interval's own average: a sale with five gallons left in an
        interval that could pass seven puts five through, not seven and not
        none. The nozzle hangs up in `settle`, AFTER BIR has booked them --
        hanging up here left the last interval's gallons unbooked, because
        BIR reads the rate as it stands at the end of the interval.
        """
        if hours <= 0:
            return
        now = time.mktime(self.c.now())
        opened = now - hours * 3600.0
        passed = MeterDict()
        # the transactions that began and ended between two ticks
        for meter, gallons in self.served.items():
            passed[meter] = passed.get(meter, 0.0) + gallons
        self.served = MeterDict()
        for meter, sale in list(self.running.items()):
            # a nozzle lifted PART of the way through this interval passes
            # what it could pass since it was lifted, not a whole interval's
            # worth: at a fast clock a car that arrives four minutes before
            # the tick would otherwise fill from the start of it
            since = min(hours, max(0.0, (now - max(sale.started, opened))
                                   / 3600.0))
            take = min(sale.gallons - sale.sold, sale.rate * since)
            sale.sold += max(0.0, take)
            passed[meter] = passed.get(meter, 0.0) + max(0.0, take)
        # a meter this engine served last interval and is not serving now
        # goes back to nothing; BIR ends its event on the zero
        for meter in list(self._serving):
            if meter not in passed:
                self.c.meter_flow[meter] = 0.0
                self._serving.discard(meter)
        for meter, gallons in passed.items():
            self.c.meter_flow[meter] = gallons / hours if gallons > 0 else 0.0

    def settle(self):
        """Hang up every nozzle whose gallons are through.

        A blended nozzle goes up as one: its components finish together,
        because each was given its own share of the gallons AND its own
        share of the rate, but a component whose tank ran dry mid-fill
        would otherwise leave the group half hung up.
        """
        for meter, blend in list(self.blends.items()):
            if blend.done:
                self.stop(meter)
        for meter, sale in list(self.running.items()):
            if sale.done:
                self.stop(meter)

    def describe(self, meter):
        meter = meter_key(meter)
        sale = self.blends.get(meter) or self.running.get(meter)
        if sale is None:
            return ""
        return f"{sale.sold:.1f} of {sale.gallons:.1f} gal"

    # ---- the DIM that stopped reporting ------------------------------------------
    def delay_hours(self):
        raw = (self.c.setting("bdim_delay") or "").strip()
        return float(raw) if raw.isdigit() else 0.0

    def last_transaction(self):
        """When a POS last mentioned any meter, or None if never."""
        stamps = list(self.c.bir.reported_at.values())
        return max(stamps) if stamps else None

    def conditions(self):
        """19/04 on the EDIM: it reported once, and then for the delay it
        did not. A DIM that never reported is a DIM nobody has sold through
        yet, which is a quiet site rather than a broken card."""
        delay = self.delay_hours()
        if not delay or not self.c.has("edim"):
            return []
        last = self.last_transaction()
        if last is None:
            return []
        if time.mktime(self.c.now()) - last >= delay * 3600.0:
            return ["190401"]
        return []
