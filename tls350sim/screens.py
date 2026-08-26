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
"""The two lines a setup screen draws, without a window around them.

The panel grew this logic first, because the panel is where you watch a
console draw. But the display is not the only thing that draws it: the
printer's Setup Data Report is the same block on paper, and the serial
port's display format is the same block again down the wire --
576013-635 Rev AA calls it "all the necessary formatting characters such as
carriage returns, line feeds, nulls, spaces, LABELS", and the labels are
these. Three consumers, one screen, so the screen lives here and the panel
asks for it like everybody else.

What is here is the console AT REST: the screen a technician sees standing
on a step without typing into it. Typing is the panel's business and stays
in the panel, because a cursor blinking in a field is not something a
printout has.
"""
import re
import time

from . import fieldio
from . import masks
from .clock import clock_hhmm, clock_words
from .console import DEVICE_LABEL_CODE, DEVICE_WORD, FIELDS

# The console's display is twenty-four characters. Every line here is cut to
# it, which is also why the printed report is twenty-four wide: it is this
# screen on paper.
COLS = 24

# A second line that asks for a key rather than showing a value.
KEYPRESS = re.compile(r"^PRESS <[A-Z/]+>")

# The steps that ask for a device's name rather than a setting of it. They
# are the label codes, and they draw the prompt on top with the device
# underneath, because the device has no label to head the screen with yet.
LABEL_CODES = ("602", "702", "707", "712", "722", "742", "747", "760",
               "782", "7A2", "802", "807", "7C5", "522")


def enter(text):
    """"ENTER PRODUCT LABEL", but not "ENTER ENTER RELAY DESIGNATION"."""
    return text if text.startswith("ENTER") else f"ENTER {text}"


def is_label_step(step):
    return (step.get("code") or "")[1:4] in LABEL_CODES


def console_step(step):
    """Is this screen the console's own, or some device's?

    Most of it follows the wire: a function programmed per device carries
    the device in its code, and one programmed for the whole console ends
    "00". The exceptions are the console-wide screens that REPEAT, header
    lines, shift times, shift closing times, which number themselves 01 to
    04 without a device to belong to, so the menu data says so.
    """
    scope = step.get("scope")
    if scope:
        return scope == "console"
    return (step.get("code") or "")[4:6] == "00"


def second(label, value, gap=" "):
    """Line two: the value, behind whatever label the screen carries.

    `gap` is what sits between the label and the value, and it is not
    always one space. The console runs a few of them together --
    576013-623 Rev AN p.116 draws `RECON WARN LIMIT:000003` with nothing
    between -- and pads a few out, `STICK OFFSET:  XXXX.XX` on p.116 and
    `DENSITY         :0.0000` on p.95. So the screen says what it is.
    """
    value = "" if value is None else str(value)
    if not label:
        return value
    if gap == ">":
        # 576013-623 Rev AN p.128 sets the value hard against the right
        # of the display rather than one space after its label
        return f"{label}{value.rjust(COLS - len(label))}"[:COLS]
    return f"{label}{gap}{value}".rstrip()


def device_code(function_name):
    """Table 29-1's letter for whatever this function is looking at."""
    name = function_name or ""
    if "MAG SUMP" in name:
        return "s"
    if "IN-TANK" in name or "TANK" in name.split()[0:1]:
        return "T"
    for word, letter in (("COMMUNICATION", "D"), ("LIQUID", "L"),
                         ("EXTERNAL INPUT", "I"),
                         ("VAPOR", "V"),
                         ("GROUNDWATER", "G"), ("2-WIRE", "C"),
                         ("3-WIRE", "H"), ("SMART", "s"),
                         # the mag sump functions are a smart sensor's,
                         # and 576013-610 Rev AC p.82 heads them "s 1:"
                         ("MAG SUMP", "s"),
                         ("PUMP RELAY", "r"), ("OUTPUT RELAY", "R"),
                         ("WPLLD", "W"), ("PRESSURE LINE", "Q"),
                         ("LINE LEAK DETECT", "P")):
        if word in name:
            return letter
    return "T"


def device_label(console, letter, device):
    """What that device was programmed with, or what it is if nothing."""
    code = DEVICE_LABEL_CODE.get(letter, "602")
    return (console.text(code, device)
            or f"{DEVICE_WORD.get(letter, 'DEVICE')} {device}")


def named_head(console, head, device, letter):
    """A head the screen names for itself, with this console in it.

    `%d` is the device the panel is on, and `(PRODUCT LABEL)` is what that
    device was programmed with -- the manuals draw both, and a console draws
    neither.
    """
    head = head.replace("%d", str(device))
    if "(" in head:
        label = device_label(console, letter, device)
        for placeholder in ("(PRODUCT LABEL)", "(Product Label)",
                            "(LABEL)", "(Label)"):
            head = head.replace(placeholder, label)
        # 577013-800 Rev P p.1059 draws the air flow meter's screen as
        # "LABEL: (AFM label)": the parenthetical is the label the site
        # programmes, and a console shows it rather than the word.
        for placeholder, which in (("(AFM label)", "evr_afm_label"),
                                   ("(PS label)", "evr_ps_label")):
            if placeholder in head:
                head = head.replace(
                    placeholder, console.setting(which, 0, "")).rstrip()
    return head


def setup_scope_head(console, step, device):
    """The In-Tank Leak Test Setup screens, which name their own scope.

    576013-623 Rev AN p.119: "If you choose SINGLE TANK, the tank number
    (for example 'TANK 1') replaces the phrase 'ALL TANK' on each screen."
    The all-tanks wording is not consistent between screens -- p.124 draws
    `TEST RATE: ALL TANK` and p.125 draws `TST EARLY STOP: ALL TANKS` -- so
    each screen carries its own.
    """
    prefix = step.get("setup_scope")
    if not prefix:
        return None
    which = step.get("scope_setting", "tank_test_method")
    if which == "line_test_method":
        # 576013-623 Rev AN ch.13 is the same function for lines, and says
        # LINE where ch.8 says TANK
        single = console.setting(which, 0, "ALL LINES") == "SINGLE LINE"
        word = f"LINE {device}" if single else "ALL LINES"
        if prefix == "TEST":
            # p.154 draws "ALL LINES:" bare for all, p.158
            # "TEST SINGLE LINE: LINE 1" for one
            return (f"TEST SINGLE LINE: LINE {device}" if single
                    else "ALL LINES:")
        return f"{prefix}: {word}"
    single = console.setting(which, 0, "ALL TANK") == "SINGLE TANK"
    if single:
        word = f"TANK {device}"
    else:
        word = "ALL TANKS" if step.get("scope_plural") else "ALL TANK"
    if prefix == "TEST":
        # the frequency screen, p.118: "TEST ALL TANK:" all-tanks, and
        # "TEST SINGLE TANK: TANK 1" for one
        return (f"TEST SINGLE TANK: TANK {device}" if single
                else "TEST ALL TANK:")
    return f"{prefix}: {word}"


def profile_code(console, step, device):
    """FULL VOL is a different function on a differently shaped tank."""
    by = step.get("code_by_profile")
    return by.get(console.tank_profile(device)) if by else None


def code_for(console, step, device):
    """The function this step writes, with this device's number in it."""
    if not step or not step.get("code"):
        return None
    c = profile_code(console, step, device) or step["code"]
    if c[4:6] == "00":
        return c
    if console_step(step) and not step.get("repeat"):
        # a console-wide screen that carries its own number, AUTO SHIFT #2
        # CLOSING is S79402 wherever the panel is pointed
        return c
    return f"{c[:4]}{device:02d}"


def field_of(console, step, device):
    """The field this step edits, chart profile codes aside.

    Usually one function, one field, so the field is filed under the
    function's own code. Where the console asks several questions about one
    function, end shape then end factor, baud rate then parity, the step
    names the field it wants and that field claims its part.
    """
    if not step or not step.get("code"):
        return None
    return FIELDS.get(profile_code(console, step, device)
                      or step.get("field") or step["code"])


def stored(console, step, device, field=None):
    """What the console holds for this step, decoded, or "" for nothing."""
    code = code_for(console, step, device)
    if not code:
        return ""
    raw = console.values.get(code.upper())
    if raw is None:
        return ""
    f = field if field is not None else field_of(console, step, device)
    return fieldio.decode(f, code, raw) if f else raw.strip()


def shown(console, field, held):
    """What line two reads, defaulted the way a console out of the box is.

    Nothing is stored for a setting nobody has changed, but the console is
    not blank there, it reads ENGLISH, U.S., DISABLED. The clock is the
    same: SET TIME shows the time the console is keeping, whether or not
    anyone has ever set it.
    """
    v = str(held or "")
    if v:
        return v
    f = field or {}
    if f.get("code") == "S50100":
        t = console.now()
        if f.get("kind") == "date":
            return time.strftime("%m/%d/%Y", t)
        return clock_hhmm(t).strip()
    if f.get("default") is not None:
        return str(f["default"])
    kind = f.get("kind")
    if kind == "enum" and f.get("choices"):
        first = f["choices"][0]
        return str(first[1] if isinstance(first, (list, tuple)) else first)
    if kind == "flag":
        return (f.get("words") or ("DISABLED",))[0]
    if kind == "time":
        # "SHIFT #1 START TIME / TIME: DISABLED": a time nobody has set
        return "DISABLED"
    if kind in ("int", "float"):
        # an unprogrammed limit reads zero on a console, not blank
        return "0"
    return v


def masked(field, value):
    """`value` drawn in the field's own fixed-width mask.

    A console's numeric setup fields are fixed width: the manual draws
    `OVERFILL LIMIT: 000%`, not `OVERFILL LIMIT: 0`, and a programmed
    console reads `090%`. Fields carry the mask the manual draws for them;
    a field without one is drawn as it comes.
    """
    return masks.apply((field or {}).get("mask"), value)


def console_value(console, step, device):
    """Tank chart security's own fields, which no S-function holds."""
    f = FIELDS.get(step["console"], {})
    kind, which = f.get("kind"), f.get("which")
    if kind == "chartcode":
        return "******" if console.chart_secured() else "000000"
    if kind == "view":
        return console.probe_serial(device)
    if kind == "consolefloat":
        value = getattr(console, which, {}).get(device)
        return f"{value:g}" if value else "0"
    if kind == "consoletextdev":
        return getattr(console, which, {}).get(device, "")
    if kind == "setting":
        where = device if f.get("scope") == "device" else 0
        value = console.setting(which, where, f.get("default", ""))
        return f"{value}{f.get('unit', '')}" if value else value
    return getattr(console, which, "") or ""


def setup_context(console, function, step, device):
    """The two halves of a setup screen: whose it is, and what it asks.

    The manual's own screens: a tank step reads "T1: (Product Label)" over
    "PRODUCT CODE: 1", so the device and its label take the top line and the
    prompt carries the value. A console-wide step has no device to name, so
    the PROMPT takes the top line and the value goes underneath on its own,
    "SYSTEM UNITS" over "U.S.", behind a short label only where the manual
    shows one: "SET TIME" over "TIME: 1:32 PM".
    """
    text = step["text"].split("(")[0].strip().upper()
    code = step.get("code") or ""
    name = (function or {}).get("function", "")
    letter = device_code(name)
    # a screen can name itself: "T1: SIPHON MANIFOLDED" rather than the
    # product label, "AUTO SHIFT #2 CLOSING" rather than the step's words
    head = step.get("head")
    if head == "product":
        # 576013-623 Rev AN p.128 heads the average-sales screens with the
        # product label on its own -- these are per PRODUCT, and the manual
        # says so: "press TANK/SENSOR to select a different product"
        return (console.text("602", device) or f"TANK {device}",
                step.get("l2") or "")
    if head:
        # a head can name the device more than once: the manual's PLLD
        # screen is "Q 1: PLLD NUMBER 1"
        head = named_head(console, head, device, letter)
    label = (step.get("l2") or "").replace("%d", str(device))
    if console_step(step):
        return head or text, label
    if head:
        # a screen that names itself carries the value bare underneath:
        # "T1: ANNUAL TEST FAIL / ALARM DISABLED"
        return head, label
    if name.startswith("COMMUNICATION"):
        # the manual heads a port screen "COMM BOARD: 1" and a receiver
        # screen "D1:", and this function walks both
        if code[1:3] in ("52", "5B"):
            named = console.text("522", device)
            return f"D{device}: {named}".rstrip(), label or text + ":"
        return f"COMM BOARD: {device}", label or text + ":"
    named = console.text(DEVICE_LABEL_CODE.get(letter, "602"), device)
    return f"{letter}{device}: {named}".rstrip(), label or text + ":"


def setup_lines(console, function, step, device=1, chart_open=True):
    """The two lines this setup step draws on a console nobody is typing at.

    `chart_open` is whether the passcode for a secured 50-point chart has
    already been given this session. A printout is not a session and has no
    passcode to give, so the report asks for the screen with the chart shut.
    """
    text = step["text"].split("(")[0].strip().upper()
    f = field_of(console, step, device) or {}
    letter = device_code((function or {}).get("function", ""))

    if f.get("kind") == "slots":
        # a config screen is per MODULE: which positions are connected
        wires = f.get("slots") or 4
        base = ((device - 1) // wires) * wires
        cells = console.slot_text(f["code"][1:4], wires, base)
        return [module_head(text, base // wires + 1)[:COLS],
                ("SLOT #: " + cells)[:COLS]]

    if not chart_open and _chart_locked(console, step, device):
        # "TANK PROFILE : 50 PTS / ENTER PASSCODE->______<"
        return ["TANK PROFILE : 50 PTS", "ENTER PASSCODE->______<"]

    if step.get("console"):
        f2 = FIELDS.get(step["console"], {})
        prompt = f2.get("prompt", text + ":").replace("%d", str(device))
        value = console_value(console, step, device)
        head, _p = setup_context(console, function, step, device)
        if f2.get("scope") == "system":
            # "TANK CHART SECURITY / CODE : 000000"
            head = named_head(console, step.get("head") or text, device,
                              letter)
        return [head[:COLS],
                second(prompt, value,
                       ">" if step.get("align") == "right"
                       else step.get("gap", " "))[:COLS]]

    if step.get("point"):
        head, _p = setup_context(console, function, step, device)
        branch = {"view": "VIEW HEIGHT/VOL PTS",
                  "count": "ADD HEIGHT/VOL PTS",
                  "remove": "REMOVE HEIGHT/VOL PT"}.get(step["point"])
        if branch:
            return [f"T{device}: {branch}"[:COLS], "PRESS <ENTER>"]
        if step["point"] == "height":
            return [head[:COLS], "HEIGHT : " + masks.apply("000000", "0")]
        return [head[:COLS], "0.00 INCH VOL: " + masks.apply("000000", "0")]

    if step.get("profile"):
        from .console import Console as _C
        head, _p = setup_context(console, function, step, device)
        name = _C.PROFILE_NAME[console.tank_profile(device)]
        return [head[:COLS], f"TANK PROFILE {name}"[:COLS]]

    if step.get("archive"):
        return ["ARCHIVE UTILITY", f"{text}: NO"[:COLS]]

    if is_label_step(step):
        # "ENTER PRODUCT LABEL / T1:": the label step puts the prompt on top
        # and the device underneath, because the device has no label to head
        # the screen with yet. Not every one of them is worded "ENTER ...":
        # 576013-623 Rev AN p.129 heads the PLLD one "PRESSURE LINE LABEL",
        # so a step that names its own head keeps it.
        v = str(stored(console, step, device, f) or "")
        head = step.get("head") or enter(text)
        return [head[:COLS], f"{letter}{device}: {v}"[:COLS]]

    scoped = setup_scope_head(console, step, device)
    if scoped is not None:
        return [scoped[:COLS],
                second(step.get("l2", ""),
                       masked(f, shown(console, f, stored(console, step, device, f))),
                       step.get("gap", " "))[:COLS]]

    head, label = setup_context(console, function, step, device)
    if step.get("body"):
        # "MODIFY TANK/METER MAP / PRESS <ENTER>": the screen asks for a key
        # rather than showing a value
        return [head[:COLS], step["body"][:COLS]]
    if not step.get("code"):
        return [head[:COLS],
                second(label, "--", step.get("gap", " "))[:COLS]]
    return [head[:COLS],
            second(label, masked(f, shown(console, f,
                                          stored(console, step, device, f))),
                   ">" if step.get("align") == "right"
                   else step.get("gap", " "))[:COLS]]


# 576013-623 Rev AN Table 5-2, "Configurations For Setting Inventory Alarm
# Thresholds": the five alarms down the side, the five configurations across
# the top. Only CUSTOM lets a site pick each one; the other four say what all
# five are, whatever the custom settings underneath happen to hold. The
# console writes % MAX with a space in it, which is how both the tape
# and 576013-635 Rev AA's display format for 551 print it.
THRESHOLD_ROWS = ("max", "high", "overfill", "delivery", "low")
THRESHOLD_UNITS = {
    "STANDARD":   ("VOLUME", "% MAX", "% MAX", "% MAX", "VOLUME"),
    "ALL %FULL":  ("%FULL", "%FULL", "%FULL", "%FULL", "%FULL"),
    "ALL VOLUME": ("VOLUME", "VOLUME", "VOLUME", "VOLUME", "VOLUME"),
    "ALL HEIGHT": ("HEIGHT", "HEIGHT", "HEIGHT", "HEIGHT", "HEIGHT"),
}


def threshold_units(console, row):
    """What one inventory alarm's threshold is measured in.

    The five are only separately programmable under CUSTOM. Under any of the
    other four the configuration decides, so that is what the report reads:
    the tape prints VOLUME against MAX OR LABEL under CONFIG: STANDARD, and
    its custom setting for that row is not what it prints.
    """
    config = console.setting("inventory_units", 0, "STANDARD")
    fixed = THRESHOLD_UNITS.get(config)
    if fixed:
        return fixed[THRESHOLD_ROWS.index(row)]
    return console.setting(f"custom_{row}", 0, "%FULL")


def printed_value(field, value):
    """A number on paper carries the decimals its screen carries.

    The display masks a temperature compensation value to `+060.0` and the
    printout reads `60.0`, not `60`: the sign and the leading zeros are the
    field's fixed width and go, the decimal place is the console's precision
    and stays. So the mask says how many, which is also why a tank diameter
    prints `96.00` and a full volume prints `9995`.
    """
    f = field or {}
    mask = f.get("mask") or ""
    if f.get("kind") != "float" or "." not in mask:
        return value
    try:
        return f"{float(value):.{len(mask.split('.')[-1])}f}"
    except (TypeError, ValueError):
        return value


def print_lines(console, function, step, device=1):
    """The lines this step contributes to a PRINTED setup report.

    Mostly the screen, because mostly the console prints what it draws. But
    not always: 576013-635 Rev AA's display-format response for 564 is the
    single line `ULLAGE: 90%` where the display heads it `ULLAGE` and puts
    `90%` underneath, and for 50E it is `VALUE (DEG F ):   60.0` where the
    display masks the value `+060.0`. A step that prints differently from
    the way it draws says so, and says it the way the response reads.

    The value is what the console holds, NOT what the display masks it to:
    a printed report has no fixed-width field to fill.
    """
    spec = step.get("print")
    if spec is None:
        drawn = setup_lines(console, function, step, device, chart_open=False)
        if len(drawn) > 1 and KEYPRESS.match(str(drawn[1]).strip()):
            # a screen that asks for a key is a way in, not a value, and a
            # report has no way in. 576013-623 Rev AN draws PRESS <ENTER>
            # thirty-nine times; 576013-635 Rev AA, which prints the display
            # format of every code the console has, draws it never, and it
            # is not on the tape either.
            return []
        return [x for x in (str(l).rstrip() for l in drawn) if x]
    f = field_of(console, step, device)
    want = spec.get("value") or ""
    if want == "clock":
        # SET DATE and SET TIME are two screens and one stamp on paper
        value = clock_words(console.now())
    elif want.startswith("threshold:"):
        value = threshold_units(console, want.split(":", 1)[1])
    else:
        value = printed_value(f, shown(console, f,
                                       stored(console, step, device, f)))
    out = []
    head = spec.get("head")
    if head:
        out.append(named_head(console, head, device,
                              device_code((function or {}).get("function",
                                                               ""))))
    line = spec.get("line")
    if line is not None:
        out.append(line.replace("%d", str(device)).format(value)[:COLS])
    return [x.rstrip() for x in out if x.rstrip()]


def module_head(text, module=1):
    """"TANK CONFIG - MODULE 1": a config screen is per module.

    With two probe modules fitted, tanks 5 to 8 are module 2's four
    positions, and this is the screen that says so.
    """
    head = text.split("(")[0].strip()
    for word in ("TANK CONFIG", "SENSOR CONFIG", "SS CONFIG", "LINE CONFIG",
                 "INPUT CONFIG", "RELAY CONFIG"):
        if head.startswith(word):
            return f"{word} - MODULE {module}"
    # the two pump ones are punctuated differently, and it is not a
    # typesetting accident: 576013-623 Rev AN draws
    # "PUMPSENS CONFIG: MODULE1" on p.162 and "PUMP MON CONFIG: MODULE1" on
    # p.165, against "TANK CONFIG - MODULE 1" on p.92.
    for word, name in (("PUMP SENSE CONFIG", "PUMPSENS CONFIG"),
                       ("PUMP RELAY CONFIG", "PUMP MON CONFIG")):
        if head.startswith(word):
            return f"{name}: MODULE{module}"
    return head


def _chart_locked(console, step, device):
    """A secured chart screen nobody has given the passcode for yet.

    "If you selected 50 points for Tank Profile AND Tank Chart Security has
    been enabled, press STEP and the system displays: TANK PROFILE : 50 PTS
    / ENTER PASSCODE->______<"
    """
    if not step:
        return False
    protected = bool(step.get("point")) or bool(
        (step.get("when") or {}).get("chart_secured"))
    return (protected and console.chart_secured()
            and console.tank_profile(device) == "04")
