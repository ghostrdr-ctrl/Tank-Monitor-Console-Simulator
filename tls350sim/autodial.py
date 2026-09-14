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
"""The console dialing out: auto-dial, its retries, and its one failure.

A real console with a SiteFax/modem card dials a programmed receiver when an
alarm it was told to report posts, retries on the programmed schedule when
nobody answers, and posts AUTODIAL FAILURE when the retries run out:
576013-818, "System failed to connect to a remote receiver after 'n' tries."
A Confirmation Report can print after a successful call (576013-623 p.6-9).

What the manuals do NOT document is the unsolicited frame the console sends
once connected -- reference/autodial_remote_dim.md hunted for it and it is
simply not written down. So this engine emulates everything observable
around the call -- the trigger, the schedule, the retries, the failure
alarm, the confirmation report -- and transmits nothing, which is honest:
a simulator that invented the frame would teach a receiver author a format
no console uses. Whether the far end ANSWERS is a bench switch, because no
modem here carries real tones.
"""
import time


class Autodial:
    def __init__(self, console):
        self.c = console
        self.pending = None      # (receiver, tries_left, next_at)
        self.failed = False      # AUTODIAL FAILURE standing
        self.answers = True      # the bench: does the receiver pick up?
        self.confirm_pending = []  # confirmation reports for the printer
        self._seen = set()       # alarms already dialed for
        self.queue = []          # receivers waiting to be called, in order
        self.holding = []        # [(due_at, receiver)] waiting out 52E
        self.log = []            # (at, receiver, "answered"/"no answer"/...)

    # ---- what is programmed -------------------------------------------------
    def receivers(self):
        """Configured receivers with a number to dial: [(n, phone)]."""
        out = []
        for n in range(1, 9):
            on = (self.c.values.get(f"S521{n:02d}") or "").strip()
            phone = (self.c.values.get(f"S523{n:02d}") or "").strip()
            if on.endswith("1") and phone:
                out.append((n, phone))
        return out

    def _retry_plan(self, receiver):
        tries = (self.c.values.get(f"S526{receiver:02d}") or "").strip()
        delay = (self.c.values.get(f"S527{receiver:02d}") or "").strip()
        tries = int(tries[-2:]) if tries[-2:].isdigit() else 3
        delay = int(delay[-2:]) if delay[-2:].isdigit() else 1
        return max(tries, 1), max(delay, 1)

    def wanted_by(self, alarm):
        """Which receivers the site told the console to call about `alarm`.

        576013-623 Rev AN ch.6 and function 52C give every receiver its own
        assignment list -- eighteen groups, then per alarm NO TANKS, ALL
        TANKS or a single tank -- and `receiver_alarms` stores it faithfully.
        `tick()` ignored it: any fresh condition dialled, and it always
        dialled `receivers()[0]`, so the table was decorative and receivers
        2 to 8 were never called. See FIDELITY N6.

        `TT` of `00` on an assignment is ALL devices, which is the same
        convention the wire uses everywhere else.
        """
        aa, nn, tt = alarm[:2], alarm[2:4], alarm[4:6]
        out = []
        for number, _phone in self.receivers():
            for a, n, t in self.c.receiver_alarms.get(number, []):
                if a == aa and n == nn and t in (tt, "00"):
                    out.append(number)
                    break
        return out

    def assigned(self):
        """Has this console been given any routing at all?

        An empty table is an UNPROGRAMMED console rather than one that has
        said "call nobody", and the two want different behaviour. With no
        assignment anywhere the first configured receiver is called, which
        is what this engine did for every alarm before the table was read;
        with any assignment present the table decides. **The fallback is the
        one invention here** -- the manuals describe programming the list and
        not what a console does before anybody has.
        """
        return any(self.c.receiver_alarms.get(n)
                   for n, _phone in self.receivers())

    def _wants_confirmation(self, receiver):
        flag = (self.c.values.get(f"S528{receiver:02d}") or "").strip()
        return flag.endswith("1")

    # 888's first three connect types are 524's three destinations, in 524's
    # own order: `01=AUTO DIAL TELETYPE`, `02=AUTO DIAL FAX`, `03=AUTO DIAL
    # COMPUTER` against `01 TELETYPE`, `02 FACSIMILE`, `03 COMPUTER`. Two
    # tables in two manuals about the same three destinations, and the codes
    # line up without being bent to. So a console that has dialled says which
    # kind of receiver it reached, and 524 -- one of FIDELITY F12's stored
    # and unread codes -- has a reader.
    DEFAULT_DESTINATION = "01"

    def _destination(self, receiver):
        raw = (self.c.values.get(f"S524{receiver:02d}") or "").strip()
        return raw[-2:] if raw[-2:] in ("01", "02", "03") else (
            self.DEFAULT_DESTINATION)

    def _note_connected(self, receiver, now):
        """The call is up: the modem port has carried data, and 888 can say
        what it was doing."""
        ports = self.c.comm_ports_for("modem")
        if ports:
            self.c.note_comm(ports[0], when=now,
                             connect=self._destination(receiver))

    # An alarm CLEARING is a thing a receiver can be called about, and it is
    # expressed the same way as any other: as an assignment in the receiver's
    # own list. 576013-623 Rev AN Table 6-2 groups `ALARM CLEAR WARNING`
    # under "Receiver Alarms" beside SERVICE REPORT WARN and DELIVERY REPORT
    # WRN, which is 14/04 in the alarm list on p.6-9. So it is not an alarm
    # this console raises -- nothing would ever raise it -- it is the site
    # saying "call me when one goes away".
    ALARM_CLEARED = "140400"

    def _clear_delay(self, receiver):
        """This receiver's 52E period, `Set Delay for Autodial on Alarm
        Clear`, whose report heads itself `RECEIVER CLEARED ALARMS REPORT
        DELAY PERIOD`.

        The page gives the field and its sample values -- 1, 3, 3 and 8 --
        and never states the unit. Minutes, because 527's retry delay is the
        only other dialling period on these pages and this engine already
        reads that one as minutes; a page that states it would settle both.
        """
        raw = (self.c.values.get(f"S52E{receiver:02d}") or "").strip()
        return max(int(raw[-2:]), 0) if raw[-2:].isdigit() else 0

    def enabled(self):
        """A dial needs the modem card and a receiver to call."""
        # And the modem has to be in a slot it works in: "System will not
        # communicate via internal SiteFax Module -- Modem Module in slot 4
        # of Comm Bay card cage", 576013-818 Table 7-2. See FIDELITY M7.
        return bool(self.c.comm_ports_for("modem")) and bool(self.receivers())

    # ---- the engine ---------------------------------------------------------
    def tick(self):
        """Watch for new alarms, run the schedule, keep the clock honest."""
        if not self.enabled():
            return
        now = time.mktime(self.c.now())
        # a new priority alarm the site has told the console to report
        current = set(self.c.conditions())
        fresh = {a for a in current if a not in self._seen
                 and not a.startswith("0109")}    # our own failure never dials
        # An alarm going AWAY is its own event, and nothing here distinguished
        # it from one arriving -- so there was no moment for 52E's delay to
        # apply to and its whole function was unread. FIDELITY N6a.
        gone = {a for a in self._seen if a not in current}
        self._seen = {a for a in self._seen if a in current} | fresh
        if fresh:
            for receiver in self._to_call(fresh):
                if receiver not in self.queue and (
                        self.pending is None or self.pending[0] != receiver):
                    self.queue.append(receiver)
        if gone:
            for receiver in self.wanted_by(self.ALARM_CLEARED):
                due = now + self._clear_delay(receiver) * 60.0
                if not any(r == receiver for _at, r in self.holding):
                    self.holding.append((due, receiver))
        for entry in [e for e in self.holding if e[0] <= now]:
            self.holding.remove(entry)
            receiver = entry[1]
            if receiver not in self.queue and (
                    self.pending is None or self.pending[0] != receiver):
                self.queue.append(receiver)
        if self.pending is None:
            if not self.queue:
                return
            receiver = self.queue.pop(0)
            tries, _delay = self._retry_plan(receiver)
            self.pending = (receiver, tries, now)
        receiver, tries_left, next_at = self.pending
        if now < next_at:
            return
        if self.answers:
            # connected. The frame itself is undocumented and not invented;
            # the observable outcomes are the log, the cleared failure, and
            # the confirmation report if one is asked for.
            self.pending = None
            self.failed = False
            self.log.insert(0, (now, receiver, "answered"))
            self._note_connected(receiver, now)
            if self._wants_confirmation(receiver):
                self.confirm_pending.append(receiver)
            return
        tries_left -= 1
        _tries, delay = self._retry_plan(receiver)
        self.log.insert(0, (now, receiver, "no answer"))
        if tries_left <= 0:
            # "failed to connect to a remote receiver after 'n' tries"
            self.pending = None
            self.failed = True
            self.log.insert(0, (now, receiver, "AUTODIAL FAILURE"))
        else:
            self.pending = (receiver, tries_left, now + delay * 60.0)

    def _to_call(self, fresh):
        """The receivers this batch of new alarms is addressed to."""
        if not self.assigned():
            first = self.receivers()[0][0]
            return [first]
        out = []
        for alarm in sorted(fresh):
            for receiver in self.wanted_by(alarm):
                if receiver not in out:
                    out.append(receiver)
        return out

    def conditions(self):
        return ["010900"] if self.failed else []
