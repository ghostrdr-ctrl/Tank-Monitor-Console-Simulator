"""The external inputs: dry contacts, and the five things the console makes
of one.

576013-623 Rev AN ch.23. An input is a contact on a Two-Input/Two-Relay
Output Interface, normally open or normally closed, and its TYPE says what
the console does when it changes:

* STANDARD -- "for the purpose of using the system's reporting, alarm, and
  data communications features": it posts EXTERN INPUT ALARM, which the
  relays, the auto-transmit signals and the history reports all read.
* GENERATOR -- "The system runs a continuous leak test in the generator's
  tank(s) until the generator turns On. When the generator shuts Off, the
  system returns to its Leak Test mode. GENERATOR ON and GENERATOR OFF
  messages are printed whenever the generator turns on and off." And 404,
  the generator log, records what each run drew.
* PUMP SENSE -- "used to indicate the On/Off state of the pump": the
  forecourt is running when the contact says so, which is what CSLD's idle
  time and the AUTOMATIC test option want to know (p.8-8: "...or if
  External Input Type is configured as Pump Sense and assigned to the
  tank").
* STANDARD ACK -- "a remote pushbutton ... as an ALARM/TEST key".
* VAPOR PROCESSOR -- the run signal from an OPW or Hirt processor that the
  console does not itself control (ISD SEM).

Category 05 had no producer at all before this: `INPUT_AA` was read by the
three input reports and written by nothing, so every input was OFF forever
and `record_generator_run` had no caller. See BENCH.md P1.
"""
import time

# S80C's `t`: "1=standard 2=generator 3=pump sense 4=standard acknowledge
# 5=Vapor Processor", and `O`: "1=normally open 2=normally closed"
STANDARD, GENERATOR, PUMP_SENSE, ACK, VAPOR_PROCESSOR = "1", "2", "3", "4", "5"
TYPE_WORDS = {STANDARD: "STANDARD", GENERATOR: "GENERATOR",
              PUMP_SENSE: "PUMP SENSE", ACK: "STANDARD ACK",
              VAPOR_PROCESSOR: "VAPOR PROCESSOR"}
NORMALLY_OPEN, NORMALLY_CLOSED = "1", "2"

# the alarm an input ON posts: category 05, "0003=Input Alarm"
INPUT_ALARM_NN = "03"


class Inputs:
    """What each contact is doing, and what the console did about it."""

    def __init__(self, console):
        self.c = console
        self.state = {}          # input number -> True when ON
        self.gen_runs = {}       # input number -> {tank: (started, volume)}
        self.printed = []        # ("ON"/"OFF", input number, when), to print

    # ---- what is programmed -------------------------------------------------
    def configured(self):
        """The inputs switched on at INPUT CONFIG, or labelled."""
        if not self.c.has("io"):
            return []
        return self.c.configured_devices("801", self.c.capacity("io"))

    def setup(self, number):
        """(type, orientation, [tanks]) off S80C, defaulting to STANDARD
        NORMALLY OPEN on nothing at all, the way the panel's own step does."""
        raw = (self.c.text("80C", number) or "").strip()
        kind = raw[0:1] if raw[0:1] in TYPE_WORDS else STANDARD
        orient = raw[1:2] if raw[1:2] in (NORMALLY_OPEN,
                                          NORMALLY_CLOSED) else NORMALLY_OPEN
        # The tail is a RUN of tank numbers and the panel writes it with the
        # commas 576013-623 Rev AN p.23-3 draws -- `TANK #: X, X` -- the way
        # 612 and 61D write their own manifold lists. `_tanks_ok` takes the
        # commas out to check the run and so does this.
        rest = raw[2:].replace(",", "")
        tanks = [int(rest[i:i + 2]) for i in range(0, len(rest) - 1, 2)
                 if rest[i:i + 2].isdigit() and int(rest[i:i + 2])]
        return kind, orient, tanks

    def kind(self, number):
        return self.setup(number)[0]

    def label(self, number):
        return self.c.text("802", number) or f"EXTERNAL INPUT {number}"

    def tanks_of(self, number):
        """The tanks a generator or pump sense input is assigned to: the
        ones entered, or "All Tanks" when none were."""
        kind, _o, tanks = self.setup(number)
        if kind not in (GENERATOR, PUMP_SENSE):
            return []
        return tanks or sorted(self.c.tank_level)

    def contact(self, number):
        """CLOSED or OPEN: what a meter across the terminals would read.
        A normally closed contact is closed when it is NOT signalling."""
        on = self.is_on(number)
        _k, orient, _t = self.setup(number)
        closed = on if orient == NORMALLY_OPEN else not on
        return "CLOSED" if closed else "OPEN"

    # ---- what is happening --------------------------------------------------
    def is_on(self, number):
        return bool(self.state.get(int(number)))

    def set(self, number, on):
        """The contact changes. Returns what the console did, for the log."""
        number = int(number)
        on = bool(on)
        was = self.is_on(number)
        self.state[number] = on
        if on == was:
            return ""
        # the history is written when the console notices, and it notices
        # now: I402's stamp is the moment the contact moved
        self.c.compute_alarms()
        kind = self.kind(number)
        if kind == ACK and on:
            # "as an ALARM/TEST key": the same press, from a distance
            shown, _live, _cleared = self.c.acknowledge(self.c.mt_logged_in())
            return ("ALARM/TEST from the remote button"
                    + (f" ({shown} shown)" if shown else ""))
        if kind == VAPOR_PROCESSOR:
            self.c.vapor_processor_on(on)
            return "vapor processor " + ("running" if on else "stopped")
        if kind == GENERATOR:
            return self._generator(number, on)
        if kind == PUMP_SENSE:
            return "pump " + ("running" if on else "off")
        return ""

    def press(self, number):
        """A momentary contact: on and straight off again."""
        said = self.set(number, True)
        self.set(number, False)
        return said

    def _generator(self, number, on):
        now = time.mktime(self.c.now())
        tanks = self.tanks_of(number)
        self.printed.append(("ON" if on else "OFF", number, now))
        if on:
            # the continuous test stops while the generator draws, and the
            # draw is measured from here
            runs = {}
            for tank in tanks:
                if self.c.leaks.active("tank", tank):
                    self.c.leaks.stop("tank", tank)
                runs[tank] = (now, self.c.tank_level.get(tank, {})
                              .get("volume", 0.0))
            self.gen_runs[number] = runs
            return "GENERATOR ON: the continuous test stops, the draw is timed"
        runs = self.gen_runs.pop(number, {})
        for tank, (started, volume) in runs.items():
            used = max(0.0, volume - self.c.tank_level.get(tank, {})
                       .get("volume", 0.0))
            self.c.record_generator_run(tank, started, now, used)
        return "GENERATOR OFF: the run is logged, the continuous test resumes"

    def pump_on(self, tank):
        """Is a pump sense input assigned to this tank saying the pump runs?"""
        return any(self.is_on(n) and int(tank) in self.tanks_of(n)
                   for n in self.configured() if self.kind(n) == PUMP_SENSE)

    def pump_sensed(self, tank):
        """Is a pump sense input assigned to this tank at all?"""
        return any(int(tank) in self.tanks_of(n)
                   for n in self.configured() if self.kind(n) == PUMP_SENSE)

    def generator_tanks(self):
        """{tank: input} for every tank feeding a generator that is OFF --
        the tanks the console keeps under continuous test."""
        out = {}
        for n in self.configured():
            if self.kind(n) == GENERATOR and not self.is_on(n):
                for tank in self.tanks_of(n):
                    out.setdefault(tank, n)
        return out

    # ---- the 700ms tick -------------------------------------------------------
    def tick(self):
        """Keep the generator's tanks under test while the generator is off.

        A test the engine refuses -- a delivery too recent, a tank too low
        -- is simply asked for again next look; the console "returns to its
        Leak Test mode" when it can, not when it is told.
        """
        for tank in self.generator_tanks():
            if self.c.leaks.active("tank", tank):
                continue
            self.c.leaks.start("tank", tank, "periodic", manual_stop=True,
                               origin="input")

    # ---- the alarms -----------------------------------------------------------
    def conditions(self):
        """"0003=Input Alarm" for every configured input that is ON."""
        return [f"05{INPUT_ALARM_NN}{n:02d}"
                for n in self.configured() if self.is_on(n)]
