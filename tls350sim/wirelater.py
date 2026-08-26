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
"""The function codes later revisions added, which Revision U lacks.

Fifteen of them: eleven from **Revision Y** (software 020/132/332/432/520)
and four more from **Revision AA** (020/133/333/433/520).

For a long time this simulator answered 9999 to eleven codes and the note
against them said "not obtainable" -- the census knew they existed because it
was built from Revision Y's index, and the manual in `reference/` was Revision
U, which stops before them. Revision Y turned up (software 020/132/332/432/520,
674 pages) and every one of the eleven is in it, with a full Command Format
and notes. So they are implemented from the manual like everything else.

**Every name the index gave them was wrong.** The index is a one-line
Function Type list and it was read out of a later revision's appendix; the
bodies say something different in almost every case:

    code   what the index suggested      what Revision Y actually calls it
    404    tank pressure sensor          Input Generator Report
    54E    line pressure setup           Set Vapor Monitoring Type
    8C3    maintenance tracker           VMC Edit/Add Fueling Position Number
    8C4    maintenance tracker           VMC Communications Timeout Value
    BA1    vapour processor              DIM Communication Status and History
    V12    Vapor Collection Test Results (this one was right)
    V82    Vapor Processor Status Report (right)
    V88    PMC Daily Vapor Polisher Diagnostic (right)
    VA1-3  the three VMC A/L reports     (right: Daily Records, Exception,
                                          Transaction)

That is worth keeping because it is the argument against implementing from an
index: five of eleven would have been built as the wrong feature entirely.

**Revision Y has copy-paste errors in these pages**, and they are the kind
that look like data:

  * `8C4`'s "Typical Response Message, Display Format" is 8C3's, verbatim --
    it prints VMC FUELING POSITION SETUP with Side A and Side B columns for a
    code whose entire data is `hh`, a timeout. Its Notes are 8C3's too.
  * `V88`'s Command Format reads `<SOH>IV8500yyyymmddnnnn` -- V85's code in
    V88's page. The response beneath it is `IV8800`.
  * `54E`'s computer response reads `<SOH>i54D00...` for function 54E.
  * `BA1`'s notes name `pp` and `NN` both "Communciation Port number", and
    `PPPPPPPP` and `CCCCCCCC` both "Totalizer value". The display sample says
    what they really are: a POST TIME and a CLEAR TIME, with the note "0
    indicates the condition is currently active" attaching to the clear time.

Where a sample and a Command Format disagree, the Command Format wins -- it is
the part a tool has to match.
"""

import time

SEP = "\r\n"

# ---------------------------------------------------------------------------
# 54E, Set Vapor Monitoring Type
# ---------------------------------------------------------------------------
VAPOR_MONITORING = {"0": "CARB ISD", "1": "APM"}

# ---------------------------------------------------------------------------
# 404, Input Generator Report
# ---------------------------------------------------------------------------
# "nn - Number of 8 character data fields to follow (Hex)" then that many
# floats, and the manual numbers them 1..10 rather than naming them in the
# packed record. The order is the order the note lists.
GENERATOR_FIELDS = ["Start Height", "Start Volume", "Start TC Volume",
                    "Start Water", "Start Temperature", "End Height",
                    "End Volume", "End TC Volume", "End Water",
                    "End Temperature"]

# ---------------------------------------------------------------------------
# V82, Vapor Processor Status Report
# ---------------------------------------------------------------------------
# "nn - Number of 2-byte ASCII hex values to follow", five statuses, then
# "NN - Number of 8-byte ASCII hex values to follow", six floats.
VP_STATUS_FIELDS = ["VP overpress test", "Emission test", "Maximum runtime",
                    "Autonomous vapor processor", "Vapor processor"]
VP_FLOAT_FIELDS = ["Ullage pressure 95th percentile", "Emission LB/1KG",
                   "Duty Cycle %", "Runtime hours", "Daily Throughput",
                   "Average HC %"]

# ---------------------------------------------------------------------------
# V88 and V12 share this four-state verdict, and it is the SAME table on both,
# which is worth saying out loud in a manual where neighbouring codes usually
# disagree about exactly this.
# ---------------------------------------------------------------------------
TEST_STATE = {"00": "NO TEST", "01": "WARN", "02": "FAIL", "03": "PASS"}

# V12's Chi-square status is one character and counts from 0, not two counting
# from 0 -- a different encoding of the same four words.
CHI_STATE = {"0": "N/A", "1": "WARN", "2": "FAIL", "3": "PASS"}

# ---------------------------------------------------------------------------
# VA1, VA2, VA3 -- the three VMC A/L reports
# ---------------------------------------------------------------------------
# "ff - Fuel Position Number (Decimal, 01-99, 00=Not Allowed)". 00 means NOT
# ALLOWED on these three, where almost every other code in the manual reads 00
# as "all". Getting that backwards answers a report for every position when
# the console would have refused the command.
AL_REPORTS = {
    "VA1": ("A/L Daily Report", "VMC A/L DAILY RECORDS REPORT"),
    "VA2": ("A/L Exception Report", "VMC A/L EXCEPTION REPORT"),
    "VA3": ("A/L Transaction Report", "VMC A/L TRANSACTION REPORT"),
}

AL_STATE = {0: "IDLE", 1: "WARN", 2: "FAIL", 3: "PASS", 4: "NOTPASS"}

# ---------------------------------------------------------------------------
# 237, 238, 239, 23A -- Revision AA's four
# ---------------------------------------------------------------------------
# 237 and 238 are one report grouped two ways: by PRODUCT and by SIPHON
# MANIFOLD. Same columns, same packed layout; the grouping moves the TOTAL
# lines and nothing else changes, so the grouping IS the difference.
#
# 239 and 23A carry the SAME Function Type, word for word -- "In-Tank
# Manifolded Delivery Report / With Sales Adjustment if BIR available" -- and
# they are not the same report. 239 carries ONE timestamp per delivery and
# prints "DATE / TIME"; 23A carries TWO and prints "START DATE / TIME  END
# DATE / TIME". That is ten characters in the middle of every record, so a
# reader using 239's layout on 23A's data takes the end time for the field
# count and every float after it is rubbish. The shift is silent.
#
# Revision AA prints 23A's computer format as "<SOH>i239TT..." -- 239's code
# on 23A's own page -- and gives 239's tank count radix as literally "(???)".
# The manual does not know, so this does not pretend to: the count is written
# the way every other count in the section is written, two hex digits.
MANIFOLD_DELIVERY = {"239": 1, "23A": 2}    # timestamps per record

# ---------------------------------------------------------------------------
# 908 and the APM block -- Revision AA's reports
# ---------------------------------------------------------------------------
# VA7 takes its verification code at the FRONT: "SVA700149TT". 8A4 was
# described in this repo as "the only code in the manual whose 149 leads", and
# that was true of Revision U. It is not true of Revision AA. A claim about
# what is unique in a manual is only as good as the revision it was read from,
# which is the same lesson the eleven "not obtainable" codes taught.
APM_TESTS = {"01": "APM TESTS", "02": "APM SENSOR SELF TEST",
             "03": "APM SETUP SELF TEST"}

# "S - Status of APM Setup Test, 0=Pass, 1=Fail". Note the direction: ZERO is
# the good one here, where every test verdict elsewhere in this manual counts
# up from NO TEST through to PASS. Reading it the familiar way reports a
# failure as a pass.
APM_SETUP_PASS = "0"

MINE = {"404", "54E", "8C3", "8C4", "BA1",
        "V12", "V82", "V88", "VA1", "VA2", "VA3",
        "237", "238", "239", "23A",
        "908", "VA4", "VA5", "VA6", "VA7", "VA8"}

# what each one needs in the cage or on the software key before it means
# anything. "An ISD/APM SEM is required for this command" is 54E's own note.
NEEDS_SOFTWARE = {"54E": ("isd", "pmc"), "V12": ("isd",), "V82": ("pmc",),
                  "V88": ("pmc",), "VA1": ("isd",), "VA2": ("isd",),
                  "VA3": ("isd",),
                  # "APM feature required" is the manual's own note on these
                  "VA4": ("isd",), "VA6": ("isd",), "VA7": ("isd",),
                  "VA8": ("isd",)}
NEEDS_MODULE = {"8C3": "vmc", "8C4": "vmc", "VA1": "vmc", "VA2": "vmc",
                "VA3": "vmc", "VA5": "vmc"}

# Two of the eleven are reports and nothing else: no Set format on the page.
INQUIRE_ONLY = {"404", "BA1", "V12", "V82", "V88", "VA1", "VA2", "VA3",
                "237", "238", "239", "23A",
                "908", "VA4", "VA5", "VA6", "VA8"}


def _hexfloat(value):
    from tls350sim import packed
    return packed.hexfloat(value)


def _stamp_seconds(when):
    """"seconds since 1/1/1970" as eight ASCII hex characters."""
    return f"{int(when):08X}"


def handle(handler, tok, dev, code, data):
    """Answer one of the eleven, or None if it is not ours."""
    if tok not in MINE:
        return None
    c = handler.c
    keys = NEEDS_SOFTWARE.get(tok)
    if keys and not any(c.licensed(k) for k in keys):
        return (handler._nine(code),
                "needs the " + " or ".join(k.upper() for k in keys)
                + " software module")
    card = NEEDS_MODULE.get(tok)
    if card and not c.has(card):
        return handler._nine(code), f"no {card} module fitted"
    setting = code[0] in "Ss"
    if setting and tok in INQUIRE_ONLY:
        return handler._nine(code), f"{tok} is a report, not a setting"
    if setting:
        return _set(handler, tok, dev, code, (data or "").strip())
    return _inquire(handler, tok, dev, code, (data or "").strip())


# ---------------------------------------------------------------------------
def _set(handler, tok, dev, code, body):
    c = handler.c
    if tok == "54E":
        if body[:1] not in VAPOR_MONITORING:
            return handler._nine(code), "REJECTED: 0=CARB ISD, 1=APM"
        c.values["S54E00"] = body[:1]
        c.save()
        return (handler._frame(code),
                f"vapor monitoring {VAPOR_MONITORING[body[:1]]}")

    if tok == "8C3":
        # "AA - Side A Fueling Position Number (Decimal 00-99)", same for B
        if len(body) != 4 or not body.isdigit():
            return handler._nine(code), "REJECTED: wants AABB"
        number = int(dev) if dev.isdigit() and int(dev) else 1
        c.vmc_fuel_pos[number] = {"A": int(body[:2]), "B": int(body[2:])}
        c.save()
        return (handler._frame(code),
                f"VMC {number} sides {body[:2]}/{body[2:]}")

    if tok == "VA7":
        # "SVA700149TT", the verification code at the FRONT
        if not body.startswith("149"):
            return handler._nine(code), "REJECTED: wants a leading 149"
        which = body[3:5]
        if which not in APM_TESTS:
            return handler._nine(code), "REJECTED: test type 01, 02 or 03"
        c.apm_cleared[which] = time.mktime(c.now())
        c.save()
        return handler._frame(code), f"{APM_TESTS[which].lower()} cleared"

    # 8C4, "S8C400hh" -- a hex timeout and nothing else. The manual's own
    # sample response for this code is 8C3's, so there is no authentic display
    # line to copy; this prints the setting plainly.
    if len(body) != 2 or not all(ch in "0123456789ABCDEFabcdef"
                                 for ch in body):
        return handler._nine(code), "REJECTED: wants two hex digits"
    c.values["S8C400"] = body.upper()
    c.save()
    return handler._frame(code), f"VMC comm timeout {int(body, 16)}s"


# ---------------------------------------------------------------------------
def _inquire(handler, tok, dev, code, body):
    c = handler.c
    display = code[0].isupper()

    if tok == "54E":
        held = (c.values.get("S54E00") or "0")[:1]
        if display:
            return (handler._frame(
                code, "VAPOR MONITORING TYPE: "
                + VAPOR_MONITORING.get(held, "CARB ISD")), "vapor type")
        return handler._frame(code, held), "vapor type"

    if tok == "8C3":
        numbers = _vmcs(c, dev)
        if display:
            rows = ["VMC FUELING POSITION SETUP", "VMC S/N    SIDE A  SIDE B"]
            for n in numbers:
                side = c.vmc_fuel_pos.get(n, {})
                rows.append(f"{n:<4d}{c.vmc_serial(n):<8s}"
                            f"{side.get('A', 0):<8d}{side.get('B', 0)}")
            return handler._frame(code, SEP.join(rows)), "VMC fueling positions"
        out = ""
        for n in numbers:
            side = c.vmc_fuel_pos.get(n, {})
            out += f"{n:02d}{side.get('A', 0):02d}{side.get('B', 0):02d}"
        return handler._frame(code, out), "VMC fueling positions"

    if tok == "8C4":
        held = (c.values.get("S8C400") or "1E")[:2]
        if display:
            return (handler._frame(
                code, f"VMC COMM TIMEOUT: {int(held, 16)} SEC"), "comm timeout")
        return handler._frame(code, held), "comm timeout"

    if tok == "908":
        return _power_up(handler, code, display)
    if tok in ("VA4", "VA5", "VA6", "VA7", "VA8"):
        return _apm_report(handler, tok, code, display, body)
    if tok in ("237", "238"):
        return _grouped_inventory(handler, tok, dev, code, display)
    if tok in MANIFOLD_DELIVERY:
        return _manifold_delivery(handler, tok, dev, code, display)
    if tok == "404":
        return _generator_report(handler, dev, code, display)
    if tok == "BA1":
        return _dim_report(handler, code, display)
    if tok == "V82":
        return _vp_status(handler, code, display)
    if tok == "V88":
        return _polisher(handler, code, display, body)
    if tok == "V12":
        return _collection(handler, code, display, body)
    return _al_report(handler, tok, dev, code, display, body)


def _vmcs(console, dev):
    """"xx - VMC Number (Decimal, 01-18, 00=all)"."""
    if dev.isdigit() and int(dev):
        return [int(dev)]
    return sorted(console.vmc_fuel_pos) or list(range(1, console.count("vmc") + 1)) or [1]


# ---------------------------------------------------------------------------
def _generator_report(handler, dev, code, display):
    """404: what a tank fed a generator, per run.

    "Setup parameters determine whether an input is from a generator", so a
    console with no input programmed as one has no records, which is what an
    untouched console answers.
    """
    c = handler.c
    tanks = ([int(dev)] if dev.isdigit() and int(dev)
             else sorted(c.tank_level) or [1])
    if display:
        rows = ["INPUT GENERATOR REPORT",
                "     START               END          DURATION  CONSUMPTION"]
        any_run = False
        for tank in tanks:
            for run in c.generator_runs(tank):
                any_run = True
                rows.append(
                    f"{_when(run['start'])}  {_when(run['end'])}  "
                    f"{run['hours']:7.2f}  {run['used']:9.1f}")
        if not any_run:
            rows.append("NO GENERATOR RECORDS")
        return handler._frame(code, SEP.join(rows)), "generator report"
    out = ""
    for tank in tanks:
        runs = c.generator_runs(tank)
        out += f"{tank:02d}{len(runs):02X}"
        for run in runs:
            out += _packed_stamp(run["start"]) + _packed_stamp(run["end"])
            out += f"{len(GENERATOR_FIELDS):02X}"
            out += "".join(_hexfloat(v) for v in run["figures"])
    return handler._frame(code, out), "generator report"


def _when(seconds):
    return time.strftime("%m-%d-%y %H:%M", time.localtime(seconds))


def _packed_stamp(seconds):
    return time.strftime("%y%m%d%H%M", time.localtime(seconds))


# ---------------------------------------------------------------------------
def _dim_report(handler, code, display):
    """BA1: whether the DIM is talking, and when it last stopped."""
    c = handler.c
    ports = c.dim_ports()
    if display:
        rows = []
        for port in ports:
            rows.append(f"DIM COMMUNICATION STATUS AND FAULT HISTORY "
                        f"PORT {port['port']}")
            rows.append(f"STATUS: {port['status']}")
            rows.append("POST TIME       CLEAR TIME      DURATION (HOURS)")
            for fault in port["faults"]:
                clear = (_when(fault["clear"]) if fault["clear"]
                         else "ACTIVE         ")
                rows.append(f"{_when(fault['post'])}  {clear}  "
                            f"{fault['hours']:8.2f}")
            if not port["faults"]:
                rows.append("NO FAULT HISTORY")
        return handler._frame(code, SEP.join(rows)), "DIM comm status"
    out = ""
    for port in ports:
        out += f"{port['port']:02d}{len(port['faults']):02X}"
        for fault in port["faults"]:
            # the notes name both of these "Totalizer value", which they are
            # not; the display sample says POST TIME and CLEAR TIME, and "0
            # indicates the condition is currently active" is the clear time
            out += _hexfloat(fault["post"])
            out += _hexfloat(fault["clear"] or 0.0)
    return handler._frame(code, out), "DIM comm status"


# ---------------------------------------------------------------------------
def _vp_status(handler, code, display):
    """V82: the vapour processor's five verdicts and six figures."""
    c = handler.c
    st = c.vapor_processor_status()
    if display:
        rows = ["VAPOR PROCESSOR STATUS REPORT",
                f"PMC VERSION: {st['version']}",
                f"VAPOR PROCESSSOR TYPE: {st['type']}"]
        for name in VP_STATUS_FIELDS:
            rows.append(f"{name.upper():<38s}{st['status'][name]}")
        for name in VP_FLOAT_FIELDS:
            rows.append(f"{name.upper():<38s}{st['figures'][name]:.2f}")
        return handler._frame(code, SEP.join(rows)), "vapor processor status"
    out = _stamp_seconds(st["tested"])
    out += f"{len(VP_STATUS_FIELDS):02X}"
    out += "".join(f"{st['codes'][n]:02d}" for n in VP_STATUS_FIELDS)
    out += f"{len(VP_FLOAT_FIELDS):02X}"
    out += "".join(_hexfloat(st["figures"][n]) for n in VP_FLOAT_FIELDS)
    return handler._frame(code, out), "vapor processor status"


# ---------------------------------------------------------------------------
def _polisher(handler, code, display, body):
    """V88: the daily vapour polisher diagnostic.

    Its Command Format on the page reads `IV8500yyyymmddnnnn` -- V85's code in
    V88's page -- and the response beneath it is `IV8800`. The date and count
    are taken as written; the code answered is this one.
    """
    c = handler.c
    want = int(body[8:12]) if len(body) >= 12 and body[8:12].isdigit() else 0
    rows_data = c.polisher_days(want or None)
    if display:
        rows = ["PMC DAILY VAPOR POLISHER DIAGNOSTIC",
                "DATE/TIME          LOAD  PRGE  MIN%  MAX%  SELF    PRESS"]
        for r in rows_data:
            rows.append(
                f"{_when(r['at'])} {r['load']:6.1f}{r['purge']:6.1f}"
                f"{r['min']:6.0f}{r['max']:6.0f}  {r['self']:<8s}{r['press']}")
        if not rows_data:
            rows.append("NO DATA AVAILABLE")
        return handler._frame(code, SEP.join(rows)), "vapor polisher"
    out = f"{len(rows_data):04d}"
    for i, r in enumerate(rows_data, 1):
        out += f"{i:04d}{int(r['at']):08d}"
        out += _hexfloat(r["load"]) + _hexfloat(r["purge"])
        out += _hexfloat(r["min"]) + _hexfloat(r["max"])
        out += "1" if r["valid"] else "0"
        out += r["self_code"] + r["press_code"]
    return handler._frame(code, out), "vapor polisher"


# ---------------------------------------------------------------------------
def _collection(handler, code, display, body):
    """V12: the balance flow monitoring test results."""
    c = handler.c
    want = int(body[:3]) if len(body) >= 3 and body[:3].isdigit() else 0
    records = c.collection_tests(want or None)
    if display:
        rows = ["BALANCE FLOW MONITORING TEST RESULTS",
                "REC# TEST TIMESTAMP      ESTPRORVR"]
        for i, r in enumerate(records, 1):
            rows.append(f"{i:04d} {_when(r['at'])} {r['orvr']:8.1f}%")
        if not records:
            rows.append("NO TEST DATA AVAILABLE")
        return handler._frame(code, SEP.join(rows)), "vapor collection tests"
    out = f"{len(records):04X}"
    for r in records:
        out += _stamp_seconds(r["at"])
        # "Items OOOOOOOO to S are only included when oo = 1"
        out += "01"
        out += _hexfloat(r["orvr"]) + _hexfloat(r["limit"])
        out += _hexfloat(r["chi"]) + _hexfloat(r["chi_limit"])
        out += r["chi_state"]
        out += "00"          # nn, the per-position records this bench has none of
    return handler._frame(code, out), "vapor collection tests"


# ---------------------------------------------------------------------------
def _al_report(handler, tok, dev, code, display, body):
    """VA1, VA2, VA3: the three VMC air/liquid reports.

    "ff - Fuel Position Number (Decimal, 01-99, 00=Not Allowed)". Zero is NOT
    "all" on these three, where it is "all" almost everywhere else in the
    manual, so it is refused rather than widened.
    """
    c = handler.c
    if not (dev.isdigit() and int(dev)):
        return handler._nine(code), "fueling position 00 is not allowed"
    position = int(dev)
    title, heading = AL_REPORTS[tok]
    rows_data = c.al_records(tok, position, body)
    if display:
        vmc = c.vmc_for_position(position)
        rows = [f"{title} - VMC:{c.vmc_serial(vmc):>6s} "
                f"Side:{c.side_for_position(position)} FP:{position:02d}",
                "        Date  Time   Avg A/L  Trans.  Status"]
        for r in rows_data:
            rows.append(f"{_when(r['at'])} {r['al']:8.1f} {r['count']:6d}"
                        f"  {r['status']}")
        if not rows_data:
            rows.append("NO RECORDS")
        return handler._frame(code, SEP.join(rows)), heading.lower()
    out = f"{position:02d}{len(rows_data):02X}"
    for r in rows_data:
        out += _stamp_seconds(r["at"]) + _hexfloat(r["al"])
        out += f"{r['count']:04d}{r['code']:02d}"
    return handler._frame(code, out), heading.lower()


# ---------------------------------------------------------------------------
def _groups(console, tok, tanks):
    """237 groups by product label, 238 by siphon manifold.

    Both print a TOTAL under each group, so the grouping is the whole of the
    difference between them.
    """
    if tok == "237":
        out = {}
        for tank in tanks:
            out.setdefault(console.text("602", tank) or "TANK %d" % tank,
                           []).append(tank)
        return list(out.values())
    seen, out = set(), []
    for tank in tanks:
        if tank in seen:
            continue
        group = [tank] + [t for t in console.partners("612", tank)
                          if t in tanks and t != tank]
        seen.update(group)
        out.append(group)
    return out


def _product_code(console, tank):
    """"p - Product Code (one ASCII character [20h-7Eh])"."""
    label = (console.text("602", tank) or "").strip()
    if label and 0x20 <= ord(label[0]) <= 0x7E:
        return label[:1]
    return " "


def _grouped_inventory(handler, tok, dev, code, display):
    """237 and 238: volume and TC volume, subtotalled per group."""
    c = handler.c
    if not c.has("probe"):
        return handler._nine(code), "no probe module fitted"
    tanks = ([int(dev)] if dev.isdigit() and int(dev)
             else sorted(c.tank_level) or [1])
    groups = _groups(c, tok, tanks)
    title = ("PRODUCT INVENTORY REPORT" if tok == "237"
             else "SIPHON MANIFOLDED INVENTORY REPORT")
    if display:
        rows = [title, "TANK PRODUCT LABEL           VOLUME   TC VOLUME"]
        for group in groups:
            sub_v = sub_tc = 0.0
            for tank in group:
                volume = c.tank_level.get(tank, {}).get("volume", 0.0)
                tc = c.tc_volume(tank)
                sub_v += volume
                sub_tc += tc
                label = c.text("602", tank) or ("TANK %d" % tank)
                rows.append("%-5d%-22s%9.0f%12.0f" % (tank, label, volume, tc))
            rows.append("%27s%9.0f%12.0f" % ("TOTAL:", sub_v, sub_tc))
        return handler._frame(code, SEP.join(rows)), title.lower()
    body = "%02X" % len(tanks)
    for group in groups:
        sub_v = sub_tc = 0.0
        for tank in group:
            volume = c.tank_level.get(tank, {}).get("volume", 0.0)
            tc = c.tc_volume(tank)
            sub_v += volume
            sub_tc += tc
            body += "%02d%s02" % (tank, _product_code(c, tank))
            body += _hexfloat(volume) + _hexfloat(tc)
        # "bb - Number of eight byte ASCII Hex floats to follow" is the group
        # subtotal, which is the whole reason bb exists
        body += "02" + _hexfloat(sub_v) + _hexfloat(sub_tc)
    return handler._frame(code, body), title.lower()


def _manifold_delivery(handler, tok, dev, code, display):
    """239 and 23A: one name, and only one of them carries an end time."""
    c = handler.c
    if not c.has("probe"):
        return handler._nine(code), "no probe module fitted"
    tanks = ([int(dev)] if dev.isdigit() and int(dev)
             else sorted(c.tank_level) or [1])
    stamps = MANIFOLD_DELIVERY[tok]
    groups = _groups(c, "238", tanks)
    if display:
        head = ("START DATE / TIME    END DATE / TIME      GALLONS TC GALLONS"
                if stamps == 2 else
                "DATE / TIME                               GALLONS TC GALLONS")
        rows = ["MANIFOLDED DELIVERY REPORT", "TANK PRODUCT LABEL"]
        for group in groups:
            for tank in group:
                rows.append("%-5d%s" % (tank, c.text("602", tank) or ""))
            rows.append(head)
            for drop in c.manifold_deliveries(group):
                when = _when(drop["start"])
                if stamps == 2:
                    when += "  " + _when(drop["end"])
                rows.append("%-42s%8.0f%10.0f"
                            % (when, drop["gallons"], drop["tc"]))
        return handler._frame(code, SEP.join(rows)), "manifolded delivery"
    body = ""
    for group in groups:
        body += "%02X" % len(group)
        for tank in group:
            body += "%02d%s" % (tank, _product_code(c, tank))
        drops = c.manifold_deliveries(group)
        body += "%02d" % len(drops)
        for drop in drops:
            body += _packed_stamp(drop["start"])
            if stamps == 2:
                body += _packed_stamp(drop["end"])
            body += "02" + _hexfloat(drop["gallons"]) + _hexfloat(drop["tc"])
    return handler._frame(code, body), "manifolded delivery"


# ---------------------------------------------------------------------------
def _power_up(handler, code, display):
    """908: how long since the console last came up.

    "llllllll - Power Up Time (minutes) ASCII-Hex long", so the packed form is
    MINUTES in hex and the printed form is days, hours and minutes. Neither is
    derivable from the other without knowing which unit it started in, which
    is exactly the sort of thing worth writing down.
    """
    minutes = int(handler.c.uptime_minutes())
    if display:
        days, rest = divmod(minutes, 60 * 24)
        hours, mins = divmod(rest, 60)
        return (handler._frame(code, "SYSTEM POWER UP TIME = %d DAYS, "
                               "%d HOURS, %d MINUTES" % (days, hours, mins)),
                "power up time")
    return handler._frame(code, "%08X" % minutes), "power up time"


def _apm_report(handler, tok, code, display, body):
    """VA4 to VA8, the APM block."""
    c = handler.c
    if tok == "VA4":
        passed = c.apm_setup_ok()
        if display:
            return (handler._frame(code, "APM SETUP TEST STATUS: "
                                   + ("PASS" if passed else "FAIL")),
                    "APM setup test")
        # 0=Pass, which is the opposite way round from every other verdict in
        # this manual
        return (handler._frame(code, APM_SETUP_PASS if passed else "1"),
                "APM setup test")

    if tok == "VA5":
        rows = c.vmci_sub_alarms()
        if display:
            out = ["VMCI SUB ALARM HISTORY",
                   "ID  ALARM ID           SUB ALARM      STATE  DATE TIME"]
            for r in rows:
                out.append("X%2d: %-18s x%2d: %-13s %-6s %s"
                           % (r["sensor"], r["alarm"], r["sub"], r["alarm"],
                              r["state"], _when(r["at"])))
            if not rows:
                out.append("NO SUB ALARM HISTORY")
            return handler._frame(code, SEP.join(out)), "VMCI sub alarms"
        packed_body = "%02X" % len(rows)
        for r in rows:
            packed_body += "%02X%02X%02X%02X%02X%s" % (
                r["sensor"], r["code"], r["sub"], r["state_code"],
                r["category"], _packed_stamp(r["at"]))
        return handler._frame(code, packed_body), "VMCI sub alarms"

    if tok == "VA6":
        pressure = c.apm_vapor_pressure()
        if display:
            return (handler._frame(code, SEP.join(
                ["APM DIAGNOSTIC", "-" * 21, "VAPOR PRESSURE",
                 "%.1f IWC" % pressure])), "APM diagnostic")
        return (handler._frame(code, "01" + _hexfloat(pressure)
                               + _packed_stamp(time.mktime(c.now()))[:6]),
                "APM diagnostic")

    if tok == "VA7":
        when = c.apm_clear_dates()
        if display:
            rows = ["%-24s: %s" % (APM_TESTS[k], when[k]) for k in
                    sorted(APM_TESTS)]
            return handler._frame(code, SEP.join(rows)), "APM service report"
        return (handler._frame(code, "".join(
            time.strftime("%y%m%d", time.localtime(c.apm_cleared[k]))
            if c.apm_cleared.get(k) else "000000"
            for k in sorted(APM_TESTS))), "APM service report")

    # VA8, the miscellaneous events report
    events = c.apm_events()
    if display:
        rows = ["APM MISCELLANEOUS EVENTS", "DATE  TIME        EVENT"]
        for e in events:
            rows.append("%s  %s" % (_when(e["at"]), e["what"]))
        if not events:
            rows.append("NO EVENTS")
        return handler._frame(code, SEP.join(rows)), "APM events"
    packed_body = "%04d" % len(events)
    for e in events:
        packed_body += _stamp_seconds(e["at"]) + "%02d" % e["code"]
    return handler._frame(code, packed_body), "APM events"
