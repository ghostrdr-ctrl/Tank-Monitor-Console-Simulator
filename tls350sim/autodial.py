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

    def _wants_confirmation(self, receiver):
        flag = (self.c.values.get(f"S528{receiver:02d}") or "").strip()
        return flag.endswith("1")

    def enabled(self):
        """A dial needs the modem card and a receiver to call."""
        return self.c.has("modem") and bool(self.receivers())

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
        self._seen = {a for a in self._seen if a in current} | fresh
        if fresh and self.pending is None:
            receiver, _phone = self.receivers()[0]
            tries, _delay = self._retry_plan(receiver)
            self.pending = (receiver, tries, now)
        if self.pending is None:
            return
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

    def conditions(self):
        return ["010900"] if self.failed else []
