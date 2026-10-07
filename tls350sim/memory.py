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
"""The console's histories, kept across a restart.

A real console holds its alarm, leak test, delivery and shift histories in
memory that survives a power cycle; this one kept them only while it ran,
so everything a site backup brought in -- and everything the console had
recorded itself -- was gone the next time it started. The state file has
always held the programming, the cards and the tank levels; this is the
rest, as `Console.save` writes it and `Console.load` reads it back.

Each record is kept as its attributes, which are all plain values, and
rebuilt without running its constructor. JSON has no tuple and no integer
key, so the keys are spelled out and turned back on the way in.
"""
from . import delivery as _delivery
from . import leaktest
from . import pressure


def _attrs(obj):
    return dict(vars(obj))


def _make(cls, attrs):
    obj = cls.__new__(cls)
    obj.__dict__.update(attrs)
    return obj


def _result(attrs):
    attrs = dict(attrs)
    attrs["flags"] = tuple(attrs.get("flags") or ())
    attrs.setdefault("manifolded", False)
    attrs.setdefault("method", None)
    attrs.setdefault("percent", None)
    return _make(leaktest.Result, attrs)


def _snap(snap):
    """A shift snapshot, whose tanks are keyed by number."""
    if not snap:
        return snap
    return {"at": snap.get("at"),
            "tanks": {int(k): v for k, v in (snap.get("tanks") or {}).items()}}


def dump(c):
    """Everything below, as JSON-ready values."""
    return {
        "alarm_log": list(c.alarm_log),
        "device_alarm_log": list(c.device_alarm_log),
        "leak_history": {f"{k}|{d}": [_attrs(r) for r in rows]
                         for (k, d), rows in c.leaks.history.items()},
        "leak_results": {f"{k}|{d}": {rate: _attrs(r)
                                      for rate, r in by_rate.items()}
                         for (k, d), by_rate in c.leaks.results.items()},
        "deliveries": {str(t): [_attrs(r) for r in rows]
                       for t, rows in c.deliveries.records.items()},
        "csld_results": {str(t): list(v) for t, v in c.csld.results.items()},
        "shifts": {"open": c.shifts.open,
                   "closed": {str(n): v
                              for n, v in c.shifts.closed.items()}},
        "lines": {f"{k}|{n}": {
            "readings": {leg: [_attrs(r) for r in rows]
                         for leg, rows in ln.readings.items()},
            "cycles": {leg: [_attrs(r) for r in rows]
                       for leg, rows in ln.cycles.items()},
            "no_vent_seed": ln.no_vent_seed}
            for (k, n), ln in c.lines.lines.items()},
        "site_cage": c.site_cage,
        # which alarms were standing, acknowledged, latched or posted, so a
        # restart does not log every standing alarm again as a new one
        "alarms": {"seen": sorted(c._seen), "acked": sorted(c.acked),
                   "latched": sorted(c.latched), "posted": sorted(c.posted),
                   "filtered": c._filtered},
    }


def restore(c, blob):
    """Put back what `dump` wrote. Nothing is touched for a key it lacks,
    so a state file from before this kept its histories in memory alone
    loads as it always did."""
    if not blob:
        return
    if "alarm_log" in blob:
        c.alarm_log[:] = blob["alarm_log"]
    if "device_alarm_log" in blob:
        c.device_alarm_log[:] = blob["device_alarm_log"]
    for key, rows in (blob.get("leak_history") or {}).items():
        kind, device = key.split("|")
        c.leaks.history[(kind, int(device))] = [_result(r) for r in rows]
    for key, by_rate in (blob.get("leak_results") or {}).items():
        kind, device = key.split("|")
        c.leaks.results[(kind, int(device))] = {
            rate: _result(r) for rate, r in by_rate.items()}
    for tank, rows in (blob.get("deliveries") or {}).items():
        c.deliveries.records[int(tank)] = [_make(_delivery.Delivery, r)
                                           for r in rows]
    for tank, value in (blob.get("csld_results") or {}).items():
        c.csld.results[int(tank)] = tuple(value)
    shifts = blob.get("shifts") or {}
    if shifts.get("open"):
        opened = dict(shifts["open"])
        opened["start"] = _snap(opened.get("start"))
        opened["adjust"] = {int(k): v for k, v
                            in (opened.get("adjust") or {}).items()}
        c.shifts.open = opened
    for n, record in (shifts.get("closed") or {}).items():
        c.shifts.closed[int(n)] = {
            "start": _snap(record.get("start")),
            "end": _snap(record.get("end")),
            "adjust": {int(k): v for k, v
                       in (record.get("adjust") or {}).items()}}
    for key, held in (blob.get("lines") or {}).items():
        kind, number = key.split("|")
        ln = c.lines.line(kind, int(number))
        for leg, rows in (held.get("readings") or {}).items():
            ln.readings[leg] = [_make(pressure.Reading, r) for r in rows]
        for leg, rows in (held.get("cycles") or {}).items():
            ln.cycles[leg] = [_make(pressure.Cycle, r) for r in rows]
        seed = held.get("no_vent_seed")
        ln.no_vent_seed = tuple(seed) if seed else None
    if blob.get("site_cage"):
        c.site_cage = {k: tuple(v) for k, v in blob["site_cage"].items()}
    alarms = blob.get("alarms") or {}
    if alarms:
        c._seen = set(alarms.get("seen") or ())
        c.acked = set(alarms.get("acked") or ())
        c.latched = set(alarms.get("latched") or ())
        c.posted = set(alarms.get("posted") or ())
        c._filtered = dict(alarms.get("filtered") or {})
