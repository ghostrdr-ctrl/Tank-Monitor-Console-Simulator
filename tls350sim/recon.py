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
"""Sections 7.5 and 7.6: the reconciliation and variance analysis reports.

Fourteen function codes, every one Inquire-only -- there is no Set among them,
and nothing in the C range closes or clears a period. Closing a shift is 79D
and clearing the tank map is 79E, both out in the 7xx configuration range.

They are four families, and the family is decided by what the RECORD carries
rather than by what the report is called:

    row / column   the eight reconciliation figures      C01 C02 C05 C06 C07 C08
    book           nine, the book inventory and a percent   C10 C11 C12
    analysis       nine, plus two bit-encoded tank masks    C20 C21 C22 C25
    history        C09, which shares nothing with any of them

"Row" and "Column" are two LAYOUTS over one set of numbers: the row form is
the wide table with a line per period, the column form writes the same figures
down the page as labels and values and names the closing date at the bottom.
They are not two names for one report, which is worth saying because this
console answered C03 and C04 with identical text until somebody looked.
"""

# ---------------------------------------------------------------------------
# The trap in this block: `tt` is the report type on seven of these codes and
# it is NOT the same enumeration on all of them.
#
#     C07, C08          00 = current, 01 = previous
#     C10 C11 C20 C21 C25   01 = current, 02 = previous
#
# Same field letter, same meaning, two different bases, forty pages apart. A
# single shared table would read every C07 "current" as a C10 "previous".
# ---------------------------------------------------------------------------
TYPE_FROM_ZERO = {"00": False, "01": True}
TYPE_FROM_ONE = {"01": False, "02": True}

# The periodic reports whose length is set by function code 796; "monthly"
# never appears in this manual, the monthly-ish period is called Periodic.
RECON = {
    # -- section 7.5, the reconciliation reports ----------------------------
    "C01": {"kind": "daily", "shape": "row", "multi": False,
            "select": "date", "note": "daily reconciliation, row"},
    "C02": {"kind": "daily", "shape": "column", "multi": False,
            "select": "date", "note": "daily reconciliation, column"},
    "C05": {"kind": "periodic", "shape": "row", "multi": True,
            "select": None, "note": "periodic reconciliation, row"},
    "C06": {"kind": "periodic", "shape": "column", "multi": False,
            "select": None, "note": "periodic reconciliation, column"},
    "C07": {"kind": "periodic", "shape": "row", "multi": True,
            "select": "zero", "note": "periodic reconciliation, row"},
    "C08": {"kind": "periodic", "shape": "column", "multi": False,
            "select": "zero", "note": "periodic reconciliation, column"},
    "C09": {"kind": "daily", "shape": "history", "multi": False,
            "select": None, "note": "reconciliation daily history"},
    # -- section 7.6, the variance analysis reports -------------------------
    "C10": {"kind": "periodic", "shape": "book", "multi": True,
            "select": "one", "note": "periodic book variance"},
    "C11": {"kind": "weekly", "shape": "book", "multi": True,
            "select": "one", "note": "weekly book variance"},
    "C12": {"kind": "daily", "shape": "book", "multi": False,
            "select": "date", "note": "daily book variance"},
    "C20": {"kind": "periodic", "shape": "analysis", "multi": False,
            "select": "one", "note": "periodic variance analysis"},
    "C21": {"kind": "weekly", "shape": "analysis", "multi": False,
            "select": "one", "note": "weekly variance analysis"},
    "C22": {"kind": "daily", "shape": "analysis", "multi": False,
            "select": "date", "note": "daily variance analysis"},
    "C25": {"kind": "periodic", "shape": "analysis", "multi": True,
            "select": "one", "note": "periodic variance analysis, by day"},
}


def previous_wanted(tok, data):
    """Whether this command asked for the previous period.

    Returns None if the selector is not one the code accepts, which is how a
    bad report type gets refused rather than quietly answered as current.
    """
    how = RECON[tok]["select"]
    text = (data or "").strip()
    if how is None or how == "date":
        # C05, C06 and C09 take no selector at all; the date-selected ones
        # name a day rather than a period, and a day is a day.
        return False
    table = TYPE_FROM_ZERO if how == "zero" else TYPE_FROM_ONE
    if not text:
        # "if not entered will default to current" on the 01/02 family. C07
        # and C08 state no default, and current is the harmless reading.
        return False
    return table.get(text[:2])
