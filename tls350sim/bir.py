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
import time

from .clock import clock_words

# The periods a console reconciles.
KINDS = ("shift", "daily", "weekly", "periodic")


class BIR:
    """One console's reconciliation, per tank and per period."""

    def __init__(self, console):
        self.c = console
        self.totals = {}       # meter -> lifetime gallons through it
        self.period = {}       # (tank, kind) -> the period being accumulated
        self.closed = {}       # (tank, kind) -> [closed records], newest first
        self._last = None      # console time as of the last look
        self._before = None    # and the one before that, for the closing times
        self.pending = None    # a close waiting for the site to go idle:
                               # (kind, due-time), posting Close Pending

    def enabled(self):
        return self.c.licensed("bir")

    # ---- what the dispensers do --------------------------------------------
    def tick(self):
        now = time.mktime(self.c.now())
        last, self._last = self._last, now
        self._before = last
        if last is None or not self.enabled():
            return
        for tank in self.c.tank_level:
            for kind in KINDS:
                self._open(tank, kind)    # every tank has all four running
        hours = (now - last) / 3600.0
        if hours > 0:
            self._dispense(hours)
        self._scheduled(now)

    def _dispense(self, hours):
        """Meters sell fuel, and the tank they are mapped to goes down."""
        shutdown = self.c.isd_shutdown_active()
        # Metered transactions reach the console through a DIM; with no DIM
        # in the cage, or the DIM link faulted, there is no meter data and
        # nothing to reconcile -- the fuel still flows at the site, but this
        # console cannot see it, so the bench meters go quiet too.
        dim = ((self.c.has("edim") or self.c.has("mdim"))
               and not self.c.dim_fault)
        for meter, rate in sorted(self.c.meter_flow.items()):
            if not rate or shutdown or not dim:
                # an ISD shutdown has the dispensers off: nothing sells
                # until the alarm clears or a technician overrides it
                continue
            tank = self.c.meters.get(meter)
            st = self.c.tank_level.get(tank) if tank else None
            if st is None:
                continue
            # open the periods on what the probe reads BEFORE this sale, or
            # the first hour's fuel goes missing from the opening figure
            periods = [self._open(tank, kind) for kind in KINDS]
            gallons = min(rate * hours, st.get("volume", 0.0))
            if gallons <= 0:
                continue
            st["volume"] = max(0.0, st["volume"] - gallons)
            # What LEFT the tank is `gallons`. What the METER says left it is
            # `gallons` adjusted by its calibration offset, and the gap
            # between those two is precisely what a reconciliation measures:
            # "sum total of all metered sales during period" is the meter's
            # figure, not the probe's. A site with a mis-calibrated meter and
            # a sound tank shows a variance, which is the whole reason the
            # offset is a setting.
            metered = gallons * (1.0 + self.c.meter_offset(meter) / 100.0)
            self.totals[meter] = self.totals.get(meter, 0.0) + metered
            for period in periods:
                period["sales"] += metered
            # "The adjusted delivery report takes into consideration all
            # dispensing that occurred during the delivery."
            running = self.c.deliveries.in_progress(tank)
            if running is not None:
                running.sold += gallons

    def _open(self, tank, kind="shift"):
        """The period being accumulated for that tank, started if need be."""
        period = self.period.get((tank, kind))
        if period is None:
            st = self.c.tank_level.get(tank, {})
            period = {"opened": time.mktime(self.c.now()),
                      "opening": st.get("volume", 0.0),
                      "water_open": st.get("water", 0.0),
                      "temp_open": 55.0, "sales": 0.0, "deliveries": 0.0,
                      "ticketed": 0.0, "adjust": 0.0}
            self.period[(tank, kind)] = period
        return period

    def delivered(self, tank, gallons, ticket=None):
        """A delivery counts towards every period it landed in."""
        if not self.enabled():
            return
        for kind in KINDS:
            period = self._open(tank, kind)
            period["deliveries"] += gallons
            if ticket:
                period["ticketed"] += ticket

    def ticket(self, tank, gallons):
        """A ticket entered after the drop, at DELIVERY MAINTENANCE."""
        if not self.enabled():
            return
        for kind in KINDS:
            self._open(tank, kind)["ticketed"] += gallons

    def adjust(self, tank, gallons, kind=None):
        """"Manually entered adjustments for period": S79B and S79C.

        An adjustment against a shift is also an adjustment to the day and the
        period containing it, so it lands in all of them; naming a kind is how
        the panel says which period the operator was looking at.
        """
        for one in KINDS:
            self._open(tank, one)["adjust"] += gallons
        return self._open(tank, kind or "shift")

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
        # a close that came due while the site was dispensing waits for an
        # idle period, and the console says so: "BIR Shift/Daily Close
        # Pending" posts until the close happens, then clears itself
        if self.pending is not None:
            kind, due = self.pending
            if not self._dispensing():
                self.pending = None
                self.close(kind)
                if kind == "daily":
                    self._week_and_period(due, stamp)
            return
        for kind, hhmm in times:
            for day in (-1, 0):
                due = time.mktime((stamp.tm_year, stamp.tm_mon,
                                   stamp.tm_mday + day, int(hhmm[:2]),
                                   int(hhmm[2:]), 0, 0, 1, -1))
                if self._before < due <= now:
                    if self._dispensing():
                        self.pending = (kind, due)
                        return
                    self.close(kind)
                    if kind == "daily":
                        self._week_and_period(due, stamp)
                    return

    def _dispensing(self):
        """Is any mapped meter selling right now? A close waits for idle."""
        return any(self.c.meter_flow.get(m, 0.0) > 0
                   for m in self.c.meters)

    def conditions(self):
        """[AANNTT] for the close that is waiting: system alarm 13 for a
        shift, 14 for a day."""
        if self.pending is None:
            return []
        return ["011300" if self.pending[0] == "shift" else "011400"]

    def _week_and_period(self, due, stamp):
        """The week and the period close on the day, not on a clock of their
        own: the week on Close Day Of Week, the period monthly or rolling."""
        close_day = (self.c.values.get("S51E00") or "").strip()[-1:]
        # S51E counts from Sunday, tm_wday from Monday
        if close_day.isdigit() and stamp.tm_wday == (int(close_day) + 6) % 7:
            self.close("weekly")
        mode = (self.c.values.get("S79500") or "").strip()[-1:]
        if mode == "1":
            length = (self.c.values.get("S79600") or "").strip()[-2:]
            days = int(length) if length.isdigit() and int(length) else 31
            first = min((p["opened"] for (t, k), p in self.period.items()
                         if k == "periodic"), default=due)
            if (due - first) / 86400.0 >= days:
                self.close("periodic")
        elif stamp.tm_mday == 1:
            self.close("periodic")

    def close(self, kind="shift", tank=None):
        """Close the period and write the row the report prints."""
        tanks = [tank] if tank else sorted(self.c.tank_level)
        rows = []
        for one in tanks:
            period = self._open(one, kind)
            st = self.c.tank_level.get(one, {})
            physical, water = st.get("volume", 0.0), st.get("water", 0.0)
            calculated = (period["opening"] + period["deliveries"]
                          - period["sales"] + period["adjust"])
            row = {"kind": kind, "opened": period["opened"],
                   "closed": time.mktime(self.c.now()),
                   "opening": period["opening"],
                   "water_open": period["water_open"],
                   "temp_open": period["temp_open"], "temp_close": 55.0,
                   "deliveries": period["deliveries"],
                   "ticketed": period["ticketed"],
                   "sales": period["sales"], "adjust": period["adjust"],
                   "calculated": calculated, "physical": physical,
                   "water": water, "variance": physical - calculated}
            self.closed.setdefault((one, kind), []).insert(0, row)
            del self.closed[(one, kind)][10:]
            self.period[(one, kind)] = {
                "opened": row["closed"], "opening": physical,
                "water_open": water, "temp_open": 55.0, "sales": 0.0,
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
        physical, water = st.get("volume", 0.0), st.get("water", 0.0)
        calculated = (period["opening"] + period["deliveries"]
                      - period["sales"] + period["adjust"])
        return {"kind": kind, "opened": period["opened"],
                "closed": time.mktime(self.c.now()),
                "opening": period["opening"],
                "water_open": period["water_open"],
                "temp_open": period["temp_open"], "temp_close": 55.0,
                "deliveries": period["deliveries"],
                "ticketed": period["ticketed"], "sales": period["sales"],
                "adjust": period["adjust"], "calculated": calculated,
                "physical": physical, "water": water,
                "variance": physical - calculated}

    def row(self, tank, kind="shift", previous=False):
        """The row a report is asking for: the closed one, or the running one.

        "a Shift Reconciliation Report for the previous shift" if there is
        one, so the report is not empty on a console nobody has closed yet.
        """
        if previous:
            return self.last(tank, kind)
        return self.current(tank, kind)

    def book(self, row):
        """Book inventory: "opening gauged volume - metered sales + total
        ticketed delivery volume + manual adjustments"."""
        return row["opening"] - row["sales"] + row["ticketed"] + row["adjust"]

    def analysis(self, row):
        """The seven numbers a Variance Analysis Report is made of."""
        book_var = row["physical"] - self.book(row)
        sales = row["sales"]
        # "delivery variance, difference between ticketed and gauged volumes"
        delivery_var = row["ticketed"] - row["deliveries"]
        # "temperature variance, change in volume related to change in
        # temperature", which is the tank's own coefficient over the period
        coeff = self.c.limit("609", 1) or 0.0
        temp_var = (row["physical"] * coeff
                    * (row["temp_close"] - row["temp_open"]))
        return {"book_var": book_var,
                "book_pct": (book_var / sales * 100.0) if sales else 0.0,
                "delivery_var": delivery_var,
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
        for tank in tanks:
            label = self.c.text("602", tank) or f"TANK {tank}"
            row = self.row(tank, kind, previous)
            out.append(f"T {tank}:{label}")
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

    def period_days(self, tank, previous=False):
        """The day rows inside a period, for the reports that print one line
        per reconciliation day.

        Per TANK, which is the whole point: a periodic report lists every
        tank, and each of them has its own days. Sharing one tank's rows
        across all of them would print tank 1's figures four times under four
        different labels, which is what the first cut of this did.
        """
        row = self.row(tank, "daily", previous)
        return [row] if row else []

    def row_report(self, tanks, kind="daily", previous=False, multi=False):
        """C01, C05, C07: one line per period under the manual's two-line head."""
        title = {"daily": "DAILY", "weekly": "WEEKLY",
                 "periodic": "PERIODIC", "shift": "SHIFT"}[kind]
        head = (title + " RECONCILIATION REPORT" if kind == "daily"
                else ("PREVIOUS" if previous else "CURRENT") + " " + title
                + " RECONCILIATION REPORT")
        out = [head]
        for tank in tanks:
            label = self.c.text("602", tank) or "TANK %d" % tank
            out.append("T %d:%s" % (tank, label))
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
            out.append("THRESHOLD: %.0f" % self.threshold(got[-1]))
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
            out.append("PRODUCT %s" % label)
            if row is None:
                out.append("NO DATA AVAILABLE")
                continue
            pairs = [("OPENING DATE", clock_words(row["opened"])[:12]),
                     ("OPENING TIME", clock_words(row["opened"])[13:]),
                     ("OPENING VOLUME", "%.0f" % row["opening"]),
                     ("DELIVERIES", "%.0f" % row["deliveries"]),
                     ("METERED SALES", "%.0f" % row["sales"]),
                     ("MANUAL ADJUST", "%.0f" % row["adjust"]),
                     ("CALCD INVNTRY", "%.0f" % row["calculated"]),
                     ("PHYSICAL INVNTRY", "%.0f" % row["physical"]),
                     ("WATER HEIGHT", "%.2f" % row["water"]),
                     ("VARIANCE", "%.0f" % row["variance"])]
            if threshold:
                # only the periodic column report carries it
                pairs.append(("THRESHOLD", "%.0f" % self.threshold(row)))
            pairs += [("CLOSING DATE", clock_words(row["closed"])[:12]),
                      ("CLOSING TIME", clock_words(row["closed"])[13:])]
            for name, value in pairs:
                out.append("%-18s%s" % (name, value))
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    def figures(self, row):
        """The eight floats every reconciliation record carries."""
        return [row["opening"], row["deliveries"], row["sales"],
                row["adjust"], row["calculated"], row["physical"],
                row["water"], row["variance"]]

    BOOK_HEAD = ("DATE TIME     OPENING METERED TICKET MAN CLS BOOK"
                 " GAUGED DAILY")
    BOOK_HEAD2 = ("              VOLUME SALES   DLVY   ADJ INVNTRY"
                  " INVNTRY VARIANCE")

    def book_figures(self, row):
        """C10, C11, C12's nine.

        The book inventory rather than the gauged deliveries, so a ticket that
        never arrived shows up as variance instead of vanishing.
        """
        book = self.book(row)
        var = row["physical"] - book
        pct = abs(var / row["sales"] * 100.0) if row["sales"] else 0.0
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
        for tank in tanks:
            label = self.c.text("602", tank) or "TANK %d" % tank
            out.append("T %d:%s" % (tank, label))
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
            out.append("THRESHOLD: %.0f" % self.threshold(got[-1]))
        out.append("SIGNATURE _________________________")
        return chr(10).join(out)

    def _book_line(self, row, stamp=True):
        f = self.book_figures(row)
        when = ("%-14.14s" % clock_words(row["closed"])[:12]) if stamp else ""
        # "-4= 0.13%": the variance, an equals sign, then the percent
        return (when + "%7.0f" % f[0] + "%8.0f" % f[1] + "%7.0f" % f[2]
                + "%5.0f" % f[3] + "%8.0f" % f[4] + "%8.0f" % f[5]
                + "%6.0f=" % f[7] + "%6.2f%%" % f[8])

    ANALYSIS_HEAD = ("DATE TIME     BOOK DLVY SALES BK_VAR MTR TEMP VAP"
                     " WATER UNEX")
    ANALYSIS_HEAD2 = ("              VAR  VAR  VAR   %      VAR VAR  VAR"
                      "  CHG   VAR")

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
        for tank in tanks:
            label = self.c.text("602", tank) or "TANK %d" % tank
            out.append("T %d:%s" % (tank, label))
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

    def meter_report(self):
        """What each meter has put through itself, and where it goes."""
        out = ["METER TOTALS", "", "METER  TANK      GALLONS"]
        for meter in sorted(set(self.c.meters) | set(self.totals)):
            tank = self.c.meters.get(meter, 0)
            out.append(f"{meter:5d}{tank:6d}{self.totals.get(meter, 0.0):13.1f}")
        return chr(10).join(out)
