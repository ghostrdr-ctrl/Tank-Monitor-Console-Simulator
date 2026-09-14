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
"""Auto-Transmit: which events arm a transmit, when it goes, and how often.

576013-623 Rev AN section 6, Auto-Transmit Setup: "The Auto-Transmit Setup
feature allows you to set an Automatic Transmit or Transmit/Repeat of any of
the following signals -- in-tank alarm, sensor alarm, delivery start/stop,
and input on/off -- to an external device via the RS-232 or RS-485 port."

The setup half of this has been complete and correct for a long time: twelve
signals, three choices each, a delay of 0-254 seconds and a repeat of 0-240.
Nothing read any of it, so the whole family sat in FIDELITY F12's list of
settings a console stores and does not have. This is the half that can be
built: the trigger set, the delay before the first message, the repeat
interval, and the connect type 888 reports while it happens.

**What is NOT here is the frame.** 576013-635 documents no Auto Transmit
message -- no function code, no format, nothing but `04=AUTO TRANSMIT` as a
connect type in 888's own communication diagnostic. So this engine transmits
no bytes, for the same reason `autodial.py` dials and sends nothing: a
simulator that invented the payload would teach a receiver author a format no
console uses. Everything observable AROUND the message is modelled, and the
message itself waits for a document. See FIDELITY P3.
"""
import time


# 576013-623 Rev AN p.6-5 and p.6-6, in the order the walk offers them: the
# first is the one the page draws (`AUTO LEAK ALARM LIMIT`) and the other
# eleven are its two-column list.
#
# The third column is how the console knows the signal happened, and it is
# the alarm type's own name out of `status_categories`/`status_types` rather
# than a hard-coded AANN pair -- so `AUTO SENSOR FUEL ALARM` picks up every
# category that has a `Fuel Alarm`, which is the six sensor cards plus the
# smart sensor, and it cannot drift from the table the rest of the console
# reads. The names are matched WHOLE: `Water Alarm` must not also catch
# `Water Out Alarm`, and `HIGH WATER ALARM` must not catch `HIGH WATER
# WARNING`.
#
# **AUTO THEFT LIMIT is the console's Sudden Loss Limit.** Nothing on the
# TLS-350 shelf is called a theft limit; 577013-940 names them in one breath
# -- "Sudden Loss Limit (theft alarm limit loss) immediately warns of a
# sudden loss of fuel during a leak test" -- and 577013-950's tank setup
# error flags call the same field THEFT_ALARM_LIMIT_OUT_OF_RANGE. Function
# 625 is `Set Tank Sudden Loss Limit` and 02/06 is the alarm it raises.
SIGNALS = (
    (1,  "AUTO LEAK ALARM LIMIT",   "raised",   "LEAK ALARM"),
    (2,  "AUTO HIGH WATER LIMIT",   "raised",   "HIGH WATER ALARM"),
    (3,  "AUTO OVERFILL LIMIT",     "raised",   "OVERFILL ALARM"),
    (4,  "AUTO LOW PRODUCT",        "raised",   "LOW PRODUCT ALARM"),
    (5,  "AUTO THEFT LIMIT",        "raised",   "SUDDEN LOSS ALARM"),
    (6,  "AUTO DELIVERY START",     "delivery", "start"),
    (7,  "AUTO DELIVERY END",       "delivery", "end"),
    (8,  "AUTO EXTERNAL INPUT ON",  "raised",   "EXTERN INPUT ALARM"),
    (9,  "AUTO EXTERNAL INPUT OFF", "cleared",  "EXTERN INPUT ALARM"),
    (10, "AUTO SENSOR FUEL ALARM",  "raised",   "FUEL ALARM"),
    (11, "AUTO SENSOR WATER ALARM", "raised",   "WATER ALARM"),
    (12, "AUTO SENSOR OUT ALARM",   "raised",   "SENSOR OUT ALARM"),
)

SIGNAL_NAME = {n: name for n, name, _k, _w in SIGNALS}

# An external input has one alarm number and two states. `status_types` calls
# 05/02 `EXTERN INPUT NORMAL` and 05/03 `EXTERN INPUT ALARM`, and the console
# only ever POSTS the second: I401 reads the absence of 05/03 as the contact
# being off, `{None: "OFF", "02": "OFF", "03": "ON"}`. So AUTO EXTERNAL INPUT
# OFF is not an alarm arriving, it is 05/03 going away -- which is why it and
# AUTO DELIVERY END share a shape with nothing standing behind them.
DISABLED = "DISABLED"
TRANSMIT = "TRANSMIT"
REPEAT = "TRANSMIT/REPEAT"

# "to an external device via the RS-232 or RS-485 port", and the section's own
# title, "Auto-Transmit Setup (RS-232 or RS-232/RS-485 Modules Only)". Read as
# the positions that ANSWER as one of those, not the cards: the multiport's
# DB-9 half is an RS-232 port on a card called RS-485, and the dual
# Maintenance Tracker's other half is an RS-232 port as well.
TRANSMIT_PORTS = ("rs232", "mt", "rs485")

# 576013-635 Rev AA p.474, 888's connect types: `04=AUTO TRANSMIT`. The one
# trace of this feature in the whole serial manual, and it is what the port
# is doing while a message goes out.
CONNECT_AUTO_TRANSMIT = "04"


_WATCHES = {}


def watches(signal):
    """{AANN} for one of the ten signals that watch an alarm.

    Worked out once, on first use rather than at import: this module is
    constructed by `console` and reads `console`'s own tables, which is the
    same way round `wiresensors` does it.
    """
    if not _WATCHES:
        from .console import STATUS_TYPES
        for n, _name, kind, words in SIGNALS:
            if kind not in ("raised", "cleared"):
                continue
            want = words.upper()
            _WATCHES[n] = {aa + nn for aa, types in STATUS_TYPES.items()
                           for nn, name in types.items()
                           if name.upper() == want}
    return _WATCHES[signal]


class AutoTransmit:
    """The Auto-Transmit engine: it arms, it waits, it repeats, it sends
    nothing."""

    def __init__(self, console):
        self.c = console
        self.armed = {}     # (signal, key) -> [when it is due, messages sent]
        self.log = []       # (at, signal, key, "transmit"/"repeat") newest 1st
        self.notices = []   # lines the bench has not shown yet
        self._seen = set()          # the conditions standing last tick
        self._delivering = set()    # the tanks with a drop running last tick

    # ---- what is programmed -------------------------------------------------
    def method(self, signal):
        """DISABLED, TRANSMIT or TRANSMIT/REPEAT for one of the twelve."""
        from .console import FIELDS
        field = FIELDS.get(f"set.autotx_{signal}") or {}
        return (self.c.setting(f"autotx_{signal}")
                or field.get("default") or DISABLED).strip().upper()

    def _seconds(self, which):
        from .console import FIELDS
        field = FIELDS.get(f"set.{which}") or {}
        raw = (self.c.setting(which) or field.get("default") or "0").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        return int(digits) if digits else 0

    def delay(self):
        """"the time interval between any alarm, delivery, or input
        indication in the system and the time the system sends an
        Auto-Transmit message", 0 to 254 seconds, defaulting to 005."""
        return self._seconds("auto_delay")

    def repeat(self):
        """"the length of time the system waits before retransmitting a
        message", 0 to 240 seconds, defaulting to 060."""
        return self._seconds("auto_repeat")

    def ports(self):
        """The positions a message could go out on."""
        out = set()
        for module in TRANSMIT_PORTS:
            out.update(self.c.comm_ports_for(module))
        return sorted(out)

    def port(self):
        """The one it goes out on, which is the console's own RS-232 port
        where there is one -- the same position this simulator's socket
        answers on, so 888's two lines about a port describe one event."""
        ports = self.ports()
        if not ports:
            return None
        preferred = self.c.rs232_port()
        return preferred if preferred in ports else ports[0]

    def enabled(self):
        """A transmit needs a port and at least one signal turned on."""
        return bool(self.port()) and any(
            self.method(n) != DISABLED for n, _name, _k, _w in SIGNALS)

    # ---- the engine ---------------------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        # The FILTERED list, not the raw conditions. Auto-Transmit is armed
        # by "any alarm, delivery, or input indication in the system", and a
        # condition is not an alarm until Appendix A's detection delay has
        # run: a sensor short that comes and goes inside two minutes is not
        # something the console has decided anything about, so there is
        # nothing to transmit. `autodial.tick()` reads `conditions()` raw and
        # would dial about one; that is its entry to answer, not this one's.
        live = set(self.c.reduce_alarms(self.c.conditions()))
        fresh, gone = live - self._seen, self._seen - live
        self._seen = live
        # A tank entering the delivery engine's `running` set and a tank
        # leaving it, which is the console SEEING a drop start and stop
        # rather than the history recording one. The two are not always the
        # same, deliberately: a rise that all goes back out again, and a drop
        # during a Service Notice session with Delivery Override on, both
        # leave `running` without writing a record, and the console still
        # watched fuel go in and stop going in.
        running = set(self.c.deliveries.running)
        started, ended = running - self._delivering, self._delivering - running
        self._delivering = running
        if not self.enabled():
            # the events are still tracked, so that turning the feature on
            # does not transmit a backlog of things that happened while it
            # was off
            self.armed.clear()
            return
        for signal, _name, kind, words in SIGNALS:
            if self.method(signal) == DISABLED:
                continue
            if kind in ("raised", "cleared"):
                seen = fresh if kind == "raised" else gone
                for record in sorted(seen):
                    if record[:4] in watches(signal):
                        self._arm(signal, record, now)
            else:
                for tank in sorted(started if words == "start" else ended):
                    self._arm(signal, f"T{tank:02d}", now)
        self._drop_finished(live, running)
        self._send(now, live, running)

    def _arm(self, signal, key, now):
        """Start one message's delay. A signal already waiting on the same
        event is not armed twice: an alarm that flickers is one indication."""
        if (signal, key) not in self.armed:
            self.armed[(signal, key)] = [now + self.delay(), 0]

    def _drop_finished(self, live, running):
        """A repeat stops when the thing it is repeating about stops.

        **This is the one inference in the file, and it is what makes the
        repeat terminate at all.** The manual gives the repeat interval and
        never says what ends the sequence -- there is no try count the way
        527 gives autodial one. Repeating while the condition STANDS is the
        reading that needs nothing invented: the console has an alarm to
        report for exactly as long as the alarm is on the display, and a
        delivery is in progress for exactly as long as the level is rising.

        It also draws the line under the two signals that have no standing
        state behind them. AUTO DELIVERY END and AUTO EXTERNAL INPUT OFF are
        both a state DEPARTING, and a departure has no duration to repeat
        over, so each sends once however it is programmed. A console that
        repeated them would have to repeat forever.

        Only a message that has already GONE is dropped this way. The delay
        is a delay and not a filter: an indication that arrives and clears
        inside it still happened, and the console has its own machinery --
        Appendix A's detection delays, in `reduce_alarms` -- for deciding
        that a condition was too brief to be an alarm at all.
        """
        for key, (_due, sent) in list(self.armed.items()):
            if sent and not self._standing(key, live, running):
                del self.armed[key]

    def _send(self, now, live, running):
        for key, entry in sorted(self.armed.items()):
            due, sent = entry
            if now < due:
                continue
            signal = key[0]
            self.log.insert(0, (now, signal, key[1],
                                "repeat" if sent else "transmit"))
            del self.log[200:]
            # The bench draws this on the serial log, because a message
            # going out on the port IS the observable and there is no frame
            # to show. A trainee who has programmed Auto-Transmit and sees
            # nothing at all has no way to tell it from a console that
            # stores the setting and ignores it, which is what this was.
            self.notices.append(
                f"-- AUTO TRANSMIT on comm {self.port()}: "
                f"{SIGNAL_NAME[signal]} {key[1]}"
                + (" (repeat)" if sent else ""))
            self.notices[:] = self.notices[-50:]
            # 888 has two lines about this and they are the same event: the
            # port carried data, and what it was doing while it did.
            self.c.note_comm(self.port(), when=now,
                             connect=CONNECT_AUTO_TRANSMIT)
            if (self.method(signal) == REPEAT
                    and self._standing(key, live, running)):
                self.armed[key] = [now + self.repeat(), sent + 1]
            else:
                del self.armed[key]

    @staticmethod
    def _standing(key, live, running):
        """Is the thing this message is about still true?"""
        signal, what = key
        kind, words = SIGNALS[signal - 1][2], SIGNALS[signal - 1][3]
        if kind == "raised":
            return what in live
        if kind == "delivery" and words == "start":
            return int(what[1:]) in running
        return False

    def waiting(self):
        """What is armed and not yet sent, for the bench and for tests."""
        return sorted((signal, key, due)
                      for (signal, key), (due, sent) in self.armed.items()
                      if not sent)
