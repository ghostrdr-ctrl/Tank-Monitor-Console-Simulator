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
"""The output relays: what each one is wired to, and whether it is pulled in.

576013-623 Rev AN ch.24 gives a relay four types, and each is a different
answer to "when is the coil energised":

* STANDARD -- "The On/Off state is determined by assigned alarms/warnings."
* MOMENTARY -- the same, "however, relay returns to the inactive state after
  the ALARM/TEST key is pressed to acknowledge the alarm."
* PUMP CONTROL OUTPUT -- "Responds to a pump request received from an
  assigned Pump Sense module or Line Leak module."
* PUMP COMM CONTROL -- "when one IQ controlled pump of a manifolded set is
  turned On for line leak testing, the relay will activate ... until the
  precision test is complete."

and the setup's fifth choice, VAPOR PROCESSOR, is the console driving the
processor it controls. S809 is the orientation: "normally open (relay
de-energized when alarm is inactive)" or "normally closed (relay energized
when alarm is inactive)" -- so the CONTACT reads the other way round on a
normally closed relay, which is what I406 and the panel report.

Before this `console.relays` held only what TEST OUTPUT RELAYS left behind,
so a relay assigned to an overfill alarm never moved when the tank overfilled;
the alarm side of every relay, every report of one, and the pump relay
monitor watching one were reading a dictionary nobody wrote. BENCH.md P2.
`console.relays` is still the test's override -- "This key also activates
and deactivates output relays when using the Output Relay Test function" --
and it wins while it is set.
"""

STANDARD, PUMP_CONTROL, MOMENTARY, PUMP_COMM, VAPOR_PROCESSOR = (
    "1", "2", "3", "4", "5")
TYPE_WORDS = {STANDARD: "STANDARD", PUMP_CONTROL: "PUMP CONTROL OUTPUT",
              MOMENTARY: "MOMENTARY", PUMP_COMM: "PUMP COMM CONTROL",
              VAPOR_PROCESSOR: "VAPOR PROCESSOR"}
NORMALLY_OPEN, NORMALLY_CLOSED = "1", "2"

# which setup code names the tank a line of each kind feeds
LINE_TANK_CODE = {"plld": "785", "wplld": "7A5", "vlld": "752"}


class Outputs:
    """Every output relay the console has, and what it is doing."""

    def __init__(self, console):
        self.c = console
        # (kind, device) whose pump keeps running with the coil dropped: a
        # welded contactor, which is the thing a Pump Relay Monitor exists
        # to catch. BENCH.md P3.
        self.welded = set()

    # ---- what is programmed -------------------------------------------------
    def count(self):
        """"OUTPUT RELAY SETUP": ("io", "relay") -- either card serves."""
        return max(self.c.capacity("relay"), self.c.capacity("io"))

    def configured(self):
        if not (self.c.has("relay") or self.c.has("io")):
            return []
        return self.c.configured_devices("806", self.count())

    def label(self, number):
        return self.c.text("807", number) or f"RELAY {number}"

    def kind(self, number):
        raw = (self.c.text("80A", number) or "").strip()
        return raw[-1:] if raw[-1:] in TYPE_WORDS else STANDARD

    def orientation(self, number):
        raw = (self.c.text("809", number) or "").strip()
        return NORMALLY_CLOSED if raw[-1:] == "2" else NORMALLY_OPEN

    def tank(self, number):
        raw = (self.c.text("80B", number) or "").strip()
        return int(raw) if raw.isdigit() and int(raw) else 0

    def assignments(self, number):
        """[(AA, NN, TT)] off 808 -- all of them.

        "You may assign more than one in-tank alarm, sensor alarm, and
        external input to a relay, and you may assign any in-tank alarm,
        sensor alarm, and external input to more than one relay",
        576013-623 Rev AN p.24-3. This read ONE assignment out of a
        `digits` field of width 8, so a second Set overwrote the first and
        a relay could never carry the three-to-six alarm list ISD's
        `MISSING RELAY SETUP` wants to see. The store is a list now, the
        shape `receiver_alarms` and `line_disable_alarms` already have.
        See FIDELITY I1a.
        """
        return list(self.c.relay_alarms.get(int(number)) or [])

    def assignment(self, number):
        """The first assignment, for a caller that wants one.

        "TT - Tank/Sensor Number (Decimal, 00=all)" and "ss - status
        01=set 00=clear". Kept because a relay with exactly one assignment
        is the common case and two reports read it that way.
        """
        rows = self.assignments(number)
        if not rows:
            return None
        aa, nn, tt = rows[0]
        return aa + nn, tt

    # ---- what is happening --------------------------------------------------
    def assigned_alarms(self, number, shown=None):
        """The displayed alarms this relay is assigned to, right now."""
        rows = self.assignments(number)
        if not rows:
            return []
        shown = self.c.displayed() if shown is None else shown
        return [r for r in shown
                if any(r[:4] == aa + nn and (tt == "00" or r[4:6] == tt)
                       for aa, nn, tt in rows)]

    def pump_request(self, tank):
        """Is anything asking for that tank's pump?

        A line leak module asking is its pump flag; a pump sense module
        asking is the tank being dispensed from; an external input of the
        pump sense type asking is the contact.
        """
        tank = int(tank)
        if not tank:
            return False
        for kind, n, _label in self.c.programmed_lines():
            code = LINE_TANK_CODE.get(kind)
            raw = (self.c.text(code, n) or "").strip() if code else ""
            if raw.isdigit() and int(raw) == tank \
                    and self.c.lines.line(kind, n).pump:
                return True
        if any(self.c.pump_tank(p) == tank
               and self.c.pump_state(p) == "ON" for p in range(1, 17)):
            return True
        return self.c.inputs.pump_on(tank)

    def precision_test_on(self, tank):
        """Is a periodic or annual line test running on that tank's line?"""
        tank = int(tank)
        for kind, n, _label in self.c.programmed_lines():
            code = LINE_TANK_CODE.get(kind)
            raw = (self.c.text(code, n) or "").strip() if code else ""
            if not (raw.isdigit() and int(raw) == tank):
                continue
            ln = self.c.lines.line(kind, n)
            if ln.running() and ln.rate_key in ("periodic", "annual"):
                return True
        return False

    def held(self, number):
        """Is TEST OUTPUT RELAYS holding this one on?"""
        return bool(self.c.relays.get(int(number)))

    def calling(self, number, shown=None):
        """Is this relay's assignment asking for action right now?

        One predicate, because the coil and whatever the contacts are wired
        to must not disagree about it. MOMENTARY is why that matters:
        576013-623 Rev AN p.24-2, "relay returns to the inactive state
        after the ALARM/TEST key is pressed to acknowledge the alarm" -- and
        a relay that has returned to the inactive state is not holding a
        pump off. `pump_cut` asked the unfiltered question and so kept a
        line dead, reading DISABLE ALARM, after the acknowledgement had
        already dropped the coil.
        """
        number = int(number)
        if self.held(number):
            return True
        live = self.assigned_alarms(number, shown)
        if self.kind(number) == MOMENTARY:
            return any(r not in self.c.acked for r in live)
        return bool(live)

    def energised(self, number, shown=None):
        """Is the coil pulled in?"""
        number = int(number)
        if self.held(number):
            return True
        kind = self.kind(number)
        if kind in (STANDARD, MOMENTARY):
            # STANDARD: "The On/Off state is determined by assigned
            # alarms/warnings." MOMENTARY: the same, until it is
            # acknowledged. Both are `calling`.
            return self.calling(number, shown)
        if kind == PUMP_CONTROL:
            return self.pump_request(self.tank(number))
        if kind == PUMP_COMM:
            return self.precision_test_on(self.tank(number))
        if kind == VAPOR_PROCESSOR:
            return self.c.vp_started is not None
        return False

    def closed(self, number, shown=None):
        """Are the contacts made? Orientation turns the coil's answer over."""
        on = self.energised(number, shown)
        return (not on) if self.orientation(number) == NORMALLY_CLOSED else on

    # ---- the relay that stops the pump --------------------------------------
    def pump_cut(self, tank):
        """Has a relay wired to this tank's pumps taken them out?

        This is what an assigned relay is FOR, and until now nothing acted
        on one: a relay assigned to an overfill alarm moved its coil, said
        so on every report of itself, and the fuel kept coming. 576013-623
        p.7-24 names the arrangement in one line -- "the Tank Test Notify
        feature triggers a warning, allowing the operator to set a relay
        to shut down the submersible" -- and that is the wiring a site
        uses for every other shutdown assignment too: the contacts sit in
        series with the STP contactor, so the alarm drops the pump.

        A STANDARD or MOMENTARY relay with a TANK programmed against it
        (S80B) is one wired that way; its alarm going active is the pump
        going off. The other three types are not shutdowns -- PUMP CONTROL
        OUTPUT and PUMP COMM CONTROL exist to turn a pump ON, and VAPOR
        PROCESSOR drives the processor -- so they are left alone.

        `held` counts, and deliberately: "This key also activates and
        deactivates output relays when using the Output Relay Test
        function", and a technician who forces a shutdown relay on with the
        pumps live has stopped the pumps. That is what the test does.
        """
        tank = int(tank)
        if not tank:
            return False
        shown = self.c.displayed()
        for number in self.configured():
            if self.kind(number) not in (STANDARD, MOMENTARY):
                continue
            if self.tank(number) != tank:
                continue
            if self.calling(number, shown):
                return True
        return False

    def cutting(self):
        """[(relay, tank)] for every relay holding a pump off right now."""
        out = []
        shown = self.c.displayed()
        for number in self.configured():
            tank = self.tank(number)
            if not tank or self.kind(number) not in (STANDARD, MOMENTARY):
                continue
            if self.calling(number, shown):
                out.append((number, tank))
        return out

    # ---- the welded contactor -----------------------------------------------
    def weld(self, kind, device, stuck=True):
        key = (kind, int(device))
        if stuck:
            self.welded.add(key)
        else:
            self.welded.discard(key)

    def is_welded(self, kind, device):
        return (kind, int(device)) in self.welded
