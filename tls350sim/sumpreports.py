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
"""The mag sump, tanker load, VMC and comm reports.

The trap in the mag sump family is `tt`. Four codes, one family, one version,
and the field immediately after the sensor number is not the same field:

    317, 318    tt = Mag Sump Leak Test STATUS (00 NO TEST DATA .. 04 PASSED)
    319, 31A    tt = a COUNT of tests to follow (max 10, max 3)

A parser that shares the `sstt` header decode across all four reads 319's
"four tests follow" as "status 04, leak test passed". Which is why the count
and the status live in different tables below and the handler asks which kind
of code it is answering before it writes the field.
"""
import time

from . import packed
from .clock import clock_words
from .sumptest import ABORT_WORDS, NO_DATA, PASSED

# 317 and 318's status, and the abort reason that only 317 carries.
SUMP_STATUS = {
    "00": "NO TEST DATA AVAILABLE", "01": "LEAK TEST ABORTED",
    "02": "FILL SUMP", "03": "MEASURING HEIGHT", "04": "LEAK TEST PASSED",
}
ABORT_REASON = {
    "00": "NOT ABORTED", "01": "MAG SENS ALM/WARN", "02": "WATER TOO LOW",
    "03": "WATER TOO HIGH", "04": "TEMP TOO LOW", "05": "TEMP TOO HIGH",
    "06": "WATER INCREASED", "07": "WATER DECREASED",
    "08": "INSUFFICIENT DATA", "09": "LEAK RATE TOO HIGH",
    "10": "TEST PHASE TIMEOUT", "11": "TEMP STABLE TIMEOUT",
}
RATE_STATUS = {"00": "UNKNOWN", "01": "VALID", "02": "COMPUTING",
               "03": "STABLE"}
LEAK_STATUS = {"00": "UNKNOWN", "01": "VALID", "02": "COMPUTING"}

# Which of the four is a status and which is a count, and how many rows the
# counted ones may carry.
SUMP_REPORTS = {
    "317": {"tt": "status", "rows": 1, "title": "IN PROGRESS", "full": True},
    "318": {"tt": "status", "rows": 1, "title": "LAST PASSED TEST"},
    "319": {"tt": "count", "rows": 10, "title": "LAST 10 TEST PASSED"},
    "31A": {"tt": "count", "rows": 3, "title": "LAST PASSED EACH YEAR"},
}

# ---------------------------------------------------------------------------
# The bodies. Four codes and three printouts carry the same handful of rows,
# and the pages differ from each other in small things -- `IN.` on the wire
# and on Figure 24-1, `IN` on Figure 24-2 and p.23-1; `RESULT: TEST PASSED`
# on 318 against p.23-1's `RESULT:      TEST PASSED` -- so each caller says
# which it is drawing, rather than one being made to look like another.
# ---------------------------------------------------------------------------
# 576013-635 Rev AA p.112, counted on its own character grid: the dates are
# twenty-one wide and every column after them ends where its heading does.
LIST_HEAD = [" " * 29 + "START   START       END     END  DURATION",
             "START DATE/TIME             HEIGHT    TEMP    HEIGHT    TEMP"
             "   MINUTES"]
DASH = "---"


def row(label, value, unit="", width=24):
    """`START HT:     20.971 IN.`: a label, and its value against column 24.

    Every row of p.113 and of Figure 24-1 is twenty-four characters.
    """
    return label + value.rjust(width - len(label) - len(unit)) + unit


def _minute(when):
    return time.strftime("%y%m%d%H%M", time.localtime(when))


def _floats(values):
    """`NN - Number of 8 bytes data fields to follow (Decimal)`, and them."""
    return f"{len(values):02d}" + "".join(packed.hexfloat(v) for v in values)


def _rate(flag, value, shape, unit):
    """A rate in words while 317's flag says it is not one yet."""
    if flag == "00":
        return "UNKNOWN"
    if flag == "02":
        return "COMPUTING"
    return format(value or 0.0, shape) + unit


def in_progress_rows(console, test, at, unit=" IN."):
    """Figure 24-1's body, which is 317's display body word for word: a
    test still running, as it stood at `at`."""
    sumps = console.sumps
    rows = ["STATUS:" + SUMP_STATUS[test.status], "START TIME:",
            " " + clock_words(test.start_at or test.test_at)]
    height = sumps.height_at(test.sensor, at)
    degrees = sumps.temp_at(test.sensor, at)
    if test.start_at is None:
        # Filling the sump. Nothing has started to be measured, so the rows
        # Figure 24-2 dashes out for a test aborted this early are dashed
        # here too, and the water going in is the one live reading.
        # UNKNOWNS A53.
        return rows + [row("START HT:", DASH), row("START TEMP:", DASH),
                       row("CURRENT HT:", f"{height:.3f}", unit),
                       row("CURRENT TEMP:", f"{degrees:.1f}", " F"),
                       row("DURATION:", DASH), row("TEMP RATE:", DASH),
                       row("LEAK RATE:", DASH)]
    start_ht, start_temp, _ht, _temp, minutes = sumps.values(test, at)
    rr, trate, _stable, ll, leak = sumps.rates(test)
    return rows + [row("START HT:", f"{start_ht:.3f}", unit),
                   row("START TEMP:", f"{start_temp:.1f}", " F"),
                   row("CURRENT HT:", f"{height:.3f}", unit),
                   row("CURRENT TEMP:", f"{degrees:.1f}", " F"),
                   row("DURATION:", f"{minutes:.0f}", " MINS"),
                   row("TEMP RATE:", _rate(rr, trate, ".1f", " F/HR")),
                   row("LEAK RATE:", _rate(ll, leak, ".4f", " IN./HR"))]


def result_rows(console, test, unit=" IN", words=ABORT_WORDS):
    """Figure 24-2's body, and 318's: how a finished test ended.

    `words` is whose spelling of the reason: the Operator's Manual's list
    on paper, the serial manual's table on the wire.
    """
    passed = test.status == PASSED
    rows = ["RESULT: " + ("TEST PASSED" if passed else "TEST ABORTED")]
    if not passed:
        rows.append("REASON:" + words.get(test.reason, ""))
    rows += ["START TIME:", " " + clock_words(test.start_at or test.test_at)]
    if test.start_at is None:
        # "If test was aborted before measuring height phase, then all of
        # these values will be replaced with dashes (---)"
        return rows + [row(label, DASH) for label in
                       ("START HT:", "START TEMP:", "END HT:", "END TEMP:",
                        "DURATION:")]
    start_ht, start_temp, end_ht, end_temp, minutes = console.sumps.values(test)
    return rows + [row("START HT:", f"{start_ht:.3f}", unit),
                   row("START TEMP:", f"{start_temp:.1f}", " F"),
                   row("END HT:", f"{end_ht:.3f}", unit),
                   row("END TEMP:", f"{end_temp:.1f}", " F"),
                   row("DURATION:", f"{minutes:.0f}", " MINS")]


def last_passed_rows(console, test):
    """576013-610 Rev AC p.23-1's LAST PASSED TEST printout, which sets
    RESULT against column 24 and the date against the margin."""
    start_ht, start_temp, end_ht, end_temp, minutes = console.sumps.values(test)
    return [row("RESULT:", "TEST PASSED"), "START TIME:",
            clock_words(test.start_at),
            row("START HT:", f"{start_ht:.3f}", " IN"),
            row("START TEMP:", f"{start_temp:.1f}", " F"),
            row("END HT:", f"{end_ht:.3f}", " IN"),
            row("END TEMP:", f"{end_temp:.1f}", " F"),
            row("DURATION:", f"{minutes:.0f}", " MINS")]


def list_row(console, test):
    """One line of 319 and 31A's table."""
    start_ht, start_temp, end_ht, end_temp, minutes = console.sumps.values(test)
    return (f"{clock_words(test.start_at):21s}{start_ht:13.3f}"
            f"{start_temp:8.1f}{end_ht:10.3f}{end_temp:8.1f}{minutes:10.0f}")


def listed(console, tok, n):
    """The passes 319 and 31A each report, newest first."""
    if tok == "319":
        return console.sumps.last_ten(n)
    return console.sumps.each_year(n, SUMP_REPORTS[tok]["rows"])


def display_rows(console, tok, n):
    """One sensor's block of 317 to 31A's display form, 576013-635 Rev AA
    pp.113-117."""
    sumps = console.sumps
    label = console.text("722", n) or f"SUMP {n}"
    rows = [f"s {n}:{label}"]
    if tok in ("317", "318"):
        test = sumps.tests.get(n) if tok == "317" else sumps.last_passed(n)
        if test is None:
            return rows + ["STATUS:" + SUMP_STATUS[NO_DATA]]
        if test.running:
            return rows + in_progress_rows(console, test,
                                           time.mktime(console.now()))
        return rows + result_rows(console, test, " IN.", ABORT_REASON)
    return rows + LIST_HEAD + [list_row(console, t)
                               for t in listed(console, tok, n)]


def computer_record(console, tok, n):
    """One sensor's record of 317 to 31A's computer form.

    317: ss tt cc YYMMDDHHmm NN (five floats) RR rrrrrrrr mmmmmmmm LL llllllll
    318: ss tt YYMMDDHHmm NN (five floats)
    319, 31A: ss tt, where tt COUNTS the YYMMDDHHmm NN (five floats) after it
    """
    sumps = console.sumps
    if tok == "317":
        test = sumps.tests.get(n)
        if test is None:
            return f"{n:02d}{NO_DATA}00"
        rr, trate, stable, ll, leak = sumps.rates(test)
        return (f"{n:02d}{test.status}{test.reason}"
                + _minute(test.start_at or test.test_at)
                + _floats(sumps.values(test))
                + rr + packed.hexfloat(trate or 0.0)
                + packed.hexfloat(stable or 0.0)
                + ll + packed.hexfloat(leak or 0.0))
    if tok == "318":
        test = sumps.last_passed(n)
        if test is None:
            return f"{n:02d}{NO_DATA}"
        return (f"{n:02d}{PASSED}" + _minute(test.start_at)
                + _floats(sumps.values(test)))
    tests = listed(console, tok, n)
    return (f"{n:02d}{len(tests):02d}"
            + "".join(_minute(t.start_at) + _floats(sumps.values(t))
                      for t in tests))

# ---------------------------------------------------------------------------
# 411 and 412 have IDENTICAL byte layouts and incompatible alarm tables. The
# same `aaaa` means different things: 0002 is "Disabled VMCI Board" on 411 and
# "Roots meter not connected" on 412. Two tables, never one.
# ---------------------------------------------------------------------------
VMCI_ALARMS = {"0001": "SETUP DATA WARNING", "0002": "DISABLED ALARM"}
VMC_ALARMS = {"0001": "VMC COMM TIMEOUT", "0002": "METER NOT CONNECTED",
              "0003": "FP SHUTDOWN WARNING", "0004": "FP SHUTDOWN ALARM"}

# 411 counts boards 01-06; 412 counts controllers 01-18. Same field, two
# ranges.
VMCI_MAX, VMC_MAX = 6, 18

# BB1's status, and note the radix mixing inside one record: the serial is
# decimal, the side and status are hex, the recover rate is decimal x10, and
# the three counters are hex again.
VMC_STATUS = {
    "00": "METER NOT CONNECTED", "01": "IDLE", "02": "RUNNING",
    "03": "LAST TRANSACTION FAILED", "04": "FP SHUTDOWN WARNING",
    "05": "FP SHUTDOWN ALARM", "FE": "STATUS UNKNOWN",
    "FF": "VMC COMM TIMEOUT",
}

# 888's three enumerations.
CONNECT_TYPE = {
    "00": "NONE", "01": "AUTO DIAL TELETYPE", "02": "AUTO DIAL FAX",
    "03": "AUTO DIAL COMPUTER", "04": "AUTO TRANSMIT", "05": "MODEM DIAL IN",
    "06": "RS232 REQUEST",
}
COMM_STATE = {
    "00": "NONE", "01": "OPEN PHONE PORT", "02": "MODEM CHECK CONNECTION",
    "03": "TRANSMITTING DATA", "04": "CHECKING FOR CARRIER",
    "05": "WAITING FOR DATA", "06": "HANGING UP",
    "07": "FAXMODEM INITIALIZING", "08": "FAX CHECK CONNECTION",
    "09": "FAX CHECK PAGE", "10": "FAX END PAGE", "11": "FAX BUILD MESSAGE",
}
COMM_ERROR = {
    "01": "UART SETTINGS ERROR", "02": "MODEM INITIALIZATION FAILED",
    "03": "MODEM TIMED OUT", "04": "LOST CARRIER", "05": "DATA TIMED OUT",
    "06": "HANG UP FAILED", "07": "FAX INITIALIZATION FAILED",
    "08": "FAX CONNECTION FAILED", "09": "FAX TIMED OUT",
    "10": "FAX INTERPAGE ERROR", "11": "FAX END PAGE ERROR",
    "12": "FAX BUILD MESSAGE ERROR",
}

# 88D, and 885 now that it has the same four. The serial manual's Notes list
# under 885 gives two -- in Rev U, Rev Y and Rev AA alike, so it is not a
# revision artefact -- while 576013-623 Rev AN p.6-4 walks the keypad through
# all four, "press CHANGE and then ENTER to choose US ROBOTICS (UK), VR TLS
# ANALOG MOD, or VR TLS GSM MODEM", and its Table 6-1 on p.6-1 lists the four
# with their port settings. The setup manual is the one that describes the
# field rather than the wire format of a reply. See FIDELITY D10.
MODEM_TYPE = {"00": "NETCOMM SMART M7F", "01": "US ROBOTICS (UK)",
              "02": "VR TLS ANALOG MOD", "03": "VR TLS GSM MODEM"}


# ---------------------------------------------------------------------------
# 411 and 412's rows, which are one shape read off two rendered pages.
#
# Both samples are Courier at six points a character, so the columns can be
# counted rather than guessed. On 412 (p.151) the VMC number's digit sits at
# column 1, the serial at 5, the stamp at 13 and the alarm name at 41; on 411
# (p.150) the device digit is at 5, the stamp at 8 and the name at 36. Six
# spaces between the stamp and the name on both, and a second row for the same
# device drops the leading columns and keeps the stamp where it was.
#
# The stamp is TWENTY-TWO characters, which is not `clock_words`. Both samples
# put "8:02" one column further right than a 21-character stamp would --
# `JAN  1, 2007   8:02 AM`, three spaces after the year where `clock_words`
# has two. It is `clock_date` and `clock_hhmm` with two spaces between them,
# and the extra column is what tells them apart.
VMC_ROW_GAP = " " * 6


def alarm_stamp(when):
    """The 22-character date and time these two reports carry."""
    from .clock import clock_wide
    return clock_wide(when)


def alarm_rows(console, category, devices, table, width):
    """One block per device that has anything, oldest incident first.

    `width` is where the stamp starts: 8 on 411, 13 on 412. The device's own
    columns are written once and the rest of its incidents are indented onto
    the stamp.
    """
    import time
    rows = []
    for number in devices:
        found = [e for e in reversed(console.alarm_log)
                 if e["aa"] == category and e.get("state") == "02"
                 and e["tt"] == f"{number:02d}"]
        if not found:
            continue
        for i, entry in enumerate(found):
            head = console.vmc_alarm_head(category, number) if not i else ""
            stamp = alarm_stamp(time.mktime(time.strptime(entry["at"],
                                                          "%y%m%d%H%M")))
            name = table.get("00" + entry["nn"], "")
            rows.append(f"{head:<{width}s}{stamp}{VMC_ROW_GAP}{name}")
    return rows


def alarm_records(console, category, devices):
    """The computer form: `xxNNYYMMDDHHmmaaaa...` per device.

    Every device asked for gets a block, including the ones with nothing --
    "NN - Number of alarm Incidents to follow" is then 00, which is what a
    tool sweeping the range needs in order to skip it.
    """
    body = ""
    for number in devices:
        found = [e for e in reversed(console.alarm_log)
                 if e["aa"] == category and e.get("state") == "02"
                 and e["tt"] == f"{number:02d}"]
        body += f"{number:02d}{len(found):02X}"
        for entry in found:
            body += entry["at"] + "00" + entry["nn"]
    return body
