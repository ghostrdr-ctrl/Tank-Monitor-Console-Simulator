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
COLS = 24                 # 577013-369 Rev B s.3.1.B: "a two-line
                          # 24-character liquid crystal display". See
                          # UNKNOWNS A52, and B27 for the one document that
                          # says 20.

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
        value = _unpadded(value, len(label) + len(value) - COLS)
        return f"{label}{value.rjust(COLS - len(label))}"[:COLS]
    value = _unpadded(value, len(label) + len(gap) + len(value) - COLS)
    return f"{label}{gap}{value}".rstrip()


def _unpadded(value, over):
    """`value` with up to `over` of its LEADING ZEROS given up.

    A setup line that overruns the glass is clipped from the right, and for
    most of them that loses a word or a unit and looks cut. For a masked
    NUMBER it loses a decade and looks like a number: `S62501` holding 25
    gallons drew `SUDDEN LOSS LIMIT: 00002`, which a technician reads as
    two. The console proves a 24-column spelling of the same fact exists on
    its own paper, at the same instant -- `SUDDEN LOSS LIMIT:    25` -- so
    the usual answer, that shortening the line would be inventing console
    text, does not apply here.

    A leading zero is the one thing on such a line that can go without
    anything being lost, and only as many of them go as the overrun needs,
    so the field keeps as much of the manual's own mask as the glass has
    room for. A value that is not a zero-padded whole number is left alone
    and clipped as before, which is A52's standing rule. FIDELITY F16.
    """
    if over <= 0 or not re.fullmatch(r"0+[0-9]+", value):
        return value
    zeros = len(value) - len(value.lstrip("0"))
    return value[min(over, zeros):]


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
                         ("GROUNDWATER", "G"),
                         # Setup spells these with a hyphen and the status
                         # functions with a space, so matching only the
                         # hyphen sent every 2-wire and 3-wire STATUS
                         # screen to the default "T" and labelled a
                         # sensor as a tank. 576013-610 Table 29-1:
                         # "C  2-wire C.L. sensor", "H  3-wire C.L.".
                         ("2-WIRE", "C"), ("2 WIRE", "C"),
                         ("3-WIRE", "H"), ("3 WIRE", "H"), ("SMART", "s"),
                         # the mag sump functions are a smart sensor's,
                         # and 576013-610 Rev AC p.82 heads them "s 1:"
                         ("MAG SUMP", "s"),
                         ("PUMP RELAY", "r"), ("OUTPUT RELAY", "R"),
                         # WPLLD before PLLD, because one contains the
                         # other. 576013-623 Rev AN p.25-1 names all three
                         # line families in one sentence -- "Whenever the
                         # prefix 'Q' appears in the display, it stands for
                         # the selected line in the PLLD system, 'W' stands
                         # for the selected line in the WPLLD system and 'P'
                         # stands for the selected line in the VLLD system"
                         # -- and the two functions that spell themselves
                         # PLLD and VLLD rather than PRESSURE LINE and LINE
                         # LEAK DETECT matched none of these words and fell
                         # through to the tank's letter. See FIDELITY U6.
                         ("WPLLD", "W"), ("PRESSURE LINE", "Q"),
                         ("PLLD", "Q"),
                         ("LINE LEAK DETECT", "P"), ("VLLD", "P")):
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
    if "{ca_label:" in head:
        # `ALL: KPM` over the four outputs, the label just entered
        head = re.sub(r"\{ca_label:(\d{4})\}",
                      lambda m: console.setting(f"ca_{m.group(1)}_lbl"), head)
    if "{ca_dlabel:" in head:
        # `L 2: BOOP` over the four outputs of one device's entry
        head = re.sub(r"\{ca_dlabel:(\d{4})\}",
                      lambda m: console.setting(f"ca_{m.group(1)}_dlbl",
                                                device), head)
    if "(" in head:
        label = device_label(console, letter, device)
        for placeholder in ("(PRODUCT LABEL)", "(Product Label)",
                            "(LABEL)", "(Label)"):
            head = head.replace(placeholder, label)
        # 577013-800 Rev P Figure 7 draws the air flow meter's screen as
        # "LABEL: (AFM label)": the parenthetical is the label the site
        # programmes, and a console shows it rather than the word. It is
        # the SMART SENSOR's label, S722, because that is what an air
        # flow meter and a vapour pressure sensor are -- SMART SENSOR SETUP
        # names the device and this screen reads the name. Two settings of
        # their own used to stand in for it, written by nothing and so
        # empty on every console.
        for placeholder in ("(AFM label)", "(PS label)"):
            if placeholder in head:
                head = head.replace(
                    placeholder,
                    console.text("722", device) or "").rstrip()
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
    if which == "dial_method":
        # 576013-623 Rev AN p.6-11: "If you choose Single Phone, the phrase
        # 'ALL RCVR' is replaced on each screen by the selected receiver
        # number (RCVR n)", and the figure draws `ALL RCVRS` against
        # `SINGLE RCVR: D1`. The setting is 529, which was read by nothing.
        # See FIDELITY N6a.
        single = (console.values.get("S52900") or "0").strip()[-1:] == "1"
        return f"SINGLE RCVR: D{device}" if single else "ALL RCVRS"
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
    return panel_field(console, FIELDS.get(profile_code(console, step, device)
                                           or step.get("field")
                                           or step["code"]))


def panel_field(console, field):
    """The field as the PANEL edits it, which for five limits is not the
    field the wire reads.

    High Product, Overfill, Delivery and the two leak test minimums are
    gallons in the field on every version (`Console.PERCENT_LIMITS`). From
    version 33 the panel enters and draws them as a percent of the label
    volume, and the field comes back marked `percent_of_max` for
    `fieldio` to convert both ways; before 33 the panel enters the
    gallons, in the six-digit mask every other volume limit has.
    """
    tok = ((field or {}).get("code") or "")[1:4]
    if tok not in console.PERCENT_LIMITS:
        return field
    if console.panel_percent(tok):
        return dict(field, percent_of_max=True)
    return dict(field, mask="000000", max=999999.0)


def stored(console, step, device, field=None):
    """What the console holds for this step, decoded, or "" for nothing."""
    code = code_for(console, step, device)
    if not code:
        return ""
    raw = console.stored(code)
    if raw is None:
        return ""
    f = field if field is not None else field_of(console, step, device)
    return fieldio.decode(f, code, raw, console) if f else raw.strip()


def screen_word(field, value):
    """The word the SETUP SCREEN uses, where it is not the wire's word.

    A field can be rendered two ways by one console, and ULLAGE is the
    clearest case on this shelf: 576013-623 Rev AN p.5-14 draws the setup
    screen as `ULLAGE` over `90 PERCENT`, and the site's own tape prints
    `ULLAGE: 90%`. Both are Veeder-Root's, neither is wrong, and they are
    different surfaces -- so the choice list keeps the wire's and the
    paper's word and `screen_words` carries the panel's.

    Applied in `setup_lines` and in the panel's CHANGE walk only. The
    printed report and the serial reply go through the same `shown()` and
    must not see it.
    """
    words = (field or {}).get("screen_words") or {}
    return words.get(str(value), value)


def wire_word(field, value):
    """`screen_word` the other way: the panel's word back to the field's.

    What a technician typed or walked to is a SCREEN word, and what
    `fieldio.encode` matches against is the choice list. Without this, ENTER
    on `95 PERCENT` would be refused by a field whose choices read `95%`.
    """
    words = (field or {}).get("screen_words") or {}
    for wire, screen in words.items():
        if str(value) == str(screen):
            return wire
    return value


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
    kind = f.get("kind")
    if f.get("default") is not None:
        want = str(f["default"])
        if kind == "enum":
            # a default is stored as the code, and the screen reads the
            # word: an unset baud rate is "01200" in the field and 1200 on
            # the display.
            #
            # Off `choices_of`, not off `f["choices"]`. A field whose
            # list belongs to the CONSOLE rather than to the field --
            # `choices_from` -- has no `choices` to walk, so its default
            # fell through and the raw wire value reached the glass:
            # `s 1: SELECT PUMP #` drew `0000` where p.26-4 draws `NONE`,
            # and `0000` is the code for NONE. It is the state every Vac
            # Sensor starts in, so it is the state a trainee sees first.
            # See the setup-mode audit, SU27.
            for choice in fieldio.choices_of(f, console):
                if isinstance(choice, (list, tuple)) and str(choice[0]) == want:
                    return str(choice[1])
        return want
    if kind == "enum" and f.get("choices"):
        first = f["choices"][0]
        return str(first[1] if isinstance(first, (list, tuple)) else first)
    if kind == "flag":
        return (f.get("words") or ("DISABLED",))[0]
    if kind == "time":
        # "SHIFT #1 START TIME / TIME: DISABLED": a time nobody has set
        return "DISABLED"
    if kind in ("int", "float"):
        # an unprogrammed limit reads zero on a console, not blank.
        #
        # A field whose range does not START at zero is a different
        # question and belongs on the FIELD: `DIAL RETRY NUMBER` is "a
        # number between 3 and 99" and drew `0`, `DELIVERY DELAY` has a
        # minimum of 1 and drew `00` where p.7-25 draws `01`, and both
        # carry a `default` now. Reading the minimum here instead would
        # have changed five screens nobody has a page for -- TANK TILT
        # would read `-999.00` out of the box, because its range is
        # signed and its minimum is not the value a console rests on.
        # See the setup-mode audit, SU31.
        return "0"
    return v


def masked(field, value):
    """`value` drawn in the field's own fixed-width mask.

    A console's numeric setup fields are fixed width: the manual draws
    `OVERFILL LIMIT: 000%`, not `OVERFILL LIMIT: 0`, and a programmed
    console reads `090%`. Fields carry the mask the manual draws for them;
    a field without one is drawn as it comes.

    `pad_hour` is the same idea for a clock, and it is HERE rather than in
    `fieldio.decode` because it is a screen's shape and not the value's.
    576013-623 Rev AN p.5-16 draws both DAYLIGHT SAVINGS times
    `TIME: 02:00 AM` where p.17-1 of the same manual draws AUTOMATIC DAILY
    CLOSING `TIME: 2:00 AM` -- and the site's own tape settles the PAPER
    against both, printing ` 2:00 AM` right aligned in eight. So the zero
    is one screen's, the paper keeps the tape's, and the two rows that
    print through a `line` spec never come through this function at all.
    See UNKNOWNS B15.
    """
    f = field or {}
    if f.get("pad_hour") and str(value)[1:2] == ":":
        value = "0" + str(value)
    return masks.apply(f.get("mask"), value)


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
        # "COMM BOARD: 1 (Type)" -- 576013-623 Rev AN p.6-2 draws the card
        # in the slot beside its number, and the tape prints it too. This
        # drew the number alone, so nothing on the screen or the paper said
        # which board you were setting up.
        return (f"COMM BOARD: {device} ({console.comm_board_name(device)})",
                label or text + ":")
    named = console.text(DEVICE_LABEL_CODE.get(letter, "602"), device)
    if step.get("bare"):
        # `Q1: (Pressure Line Label)` over a bare `NONE` -- 576013-623 Rev
        # AN p.10-7. The value stands on its own under the device's own
        # head, with no prompt in front of it. `"l2": ""` cannot say that:
        # an empty prompt falls back to the step's own words, which is
        # what every other headless screen wants -- `AUTO-DIAL FREQUENCY:`
        # is drawn by exactly that fallback. See the setup-mode audit,
        # SU18.
        return f"{letter}{device}: {named}".rstrip(), ""
    return f"{letter}{device}: {named}".rstrip(), label or text + ":"


# LIQUID SENSOR SETUP as the bench's glass drew it, read through 5FA with
# stepped at its keypad (2026-10-08, the interstitial card in slot 2, sensor 2
# switched on):
#
#     SENSOR CONFIG - MODULE 1      ENTER SENSOR LOCATION
#     SLOT 2 - X 2 X X X X X X      L 2:
#
#     L 2:ENTER SENSOR TYPE         L 2:
#     TRI-STATE (SINGLE FLOAT)      CATEGORY : OTHER SENSORS
#
# The device is `L 2:` -- the number held right in two, as the alarm
# display holds it, and nothing after the colon -- where the guide draws
# `L1: ENTER SENSOR TYPE`; the category has a space before its colon; and
# the slot line is the card's own SLOT number, ` - `, then the positions.
# Those last two the Setup manual draws as well (576013-623 Rev AN p.173 to
# p.175), and this console had misspelt them.
# The other sensor families draw the same way on the page and none has been
# read, so this is the liquid card's alone (FIDELITY S46).
#
# IN-TANK SETUP the same way, tank 1 of the cold-started bench stepped
# with nothing programmed on it (`transcripts/menuwalk2.jsonl`): `T 1:` with
# the label hard against the colon, `T 1:ANNUAL TEST FAIL` likewise -- but
# `T 1: SIPHON MANIFOLDED` and `T 1: LINE MANIFOLDED` keep their space --
# and `ENTER PRODUCT LABEL:` with a colon the guide does not draw.
MEASURED_FACES = {"LIQUID SENSOR SETUP": "L", "IN-TANK SETUP": "T"}
HEADS_KEEP_THE_SPACE = {"SIPHON MANIFOLDED", "LINE MANIFOLDED"}


# SYSTEM SETUP's own lines off the bench's glass (`transcripts/menuwalk`,
# `menuwalk3`), each rewritten where this drew the guide's or its own:
# the station header line `# 1:`, the shift time `SHIFT #1 START TIME :`,
# the security code `CODE : 000000` as TANK CHART SECURITY already drew it,
# and three names -- `DAYLIGHT SAVING TIME` (which the setup printout here
# already spelt so, off the tape), `CUSTOM ALARMS` and `SYSTEM BEEPER`.
LINE_FACES = {"SYSTEM SETUP": (
    (r"^#(\d): ?", r"# \1:"),
    (r"^(SHIFT #\d START TIME)$", r"\1 :"),
    (r"^CODE: ", "CODE : "),
    (r"^DAYLIGHT SAVINGS TIME$", "DAYLIGHT SAVING TIME"),
    (r"^CUSTOM ALARM LABELS$", "CUSTOM ALARMS"),
    (r"^BEEPER$", "SYSTEM BEEPER"),
),
    # a port's settings, at rest and while CHANGE walks them, in the setup
    # printout's own words, which the bench's glass draws too
    "COMMUNICATIONS SETUP": (
    (r"^COMM BOARD: ", "COMM BOARD  : "),
    (r"^BAUD RATE: ", " BAUD RATE  : "),
    (r"^PARITY: ", " PARITY     : "),
    (r"^STOP BIT: ", " STOP BIT   : "),
    (r"^DATA LENGTH: ", " DATA LENGTH: "),
    (r"^CODE: ", "CODE : "),
)}


def _measured_face(function, lines):
    """`lines` as a function in `MEASURED_FACES` draws them."""
    name = (function or {}).get("function", "")
    rules = LINE_FACES.get(name)
    if rules:
        out = []
        for line in lines:
            for pattern, repl in rules:
                line = re.sub(pattern, repl, line)
            out.append(line[:COLS])
        lines = out
    letter = MEASURED_FACES.get(name)
    if not letter:
        return lines

    def head(m):
        rest = m.group(2)
        gap = " " if rest.strip() in HEADS_KEEP_THE_SPACE else ""
        return f"{letter}{int(m.group(1)):2d}:{gap}{rest}"

    out = []
    for line in lines:
        line = re.sub(rf"^{letter}(\d+): ?(.*)$", head, line)
        line = re.sub(r"^CATEGORY: ", "CATEGORY : ", line)
        if line == "ENTER PRODUCT LABEL":
            line = "ENTER PRODUCT LABEL:"
        if letter == "T":
            # the bench's own label wherever the line still carries the
            # guide's -- CHANGE paging a word draws `TNK TST SIPHON
            # BREAK:ON `, the value hard against the label as at rest
            for label, _form in TANK_FACES.values():
                root = label.replace(":", "").strip()
                m = re.match(rf"^\s*{re.escape(root)}\s*: ?(.*)$", line)
                if m:
                    # a line already in the bench's form comes back as it
                    # was; an edit buffer keeps its own right alignment
                    line = label + m.group(1)
                    break
        out.append(line[:COLS])
    return out


def _whole(width):
    return lambda v: f"{int(v):0{width}d}"


def _percent(v):
    return f"{int(v):03d}%"


def _signed(v):
    return ("-" if v < 0 else "+") + f"{abs(v):07.2f}"


# IN-TANK SETUP's second lines off the bench's glass, tank 1 unprogrammed:
# every label padded to its own colon, every value at its own width and
# truncated, not rounded -- the 99.9 sudden loss default reads `000099`, and
# the wire's I625 reads 99 -- where this drew the guide's `LABEL: value`
# through each field's mask. TANK TILT has no colon at all. Keyed by code.
# keyed by the code's NUMBER: these draw a setting, they do not read it,
# and a quoted code is a reader to `test_fidelity`'s source scan
TANK_FACES = {
    1539: ("PRODUCT CODE    : ", None),
    1545: ("THERMAL COEFF  :", lambda v: f"{v:.6f}"),
    1543: ("TANK DIAMETER   :", lambda v: f"{v:07.2f}"),
    1540: ("       FULL VOL : ", _whole(6)),
    1576: ("MAX OR LABEL VOL: ", _whole(6)),
    1571: ("OVERFILL LIMIT  : ", _percent),
    1570: ("HIGH PRODUCT    : ", _percent),
    1577: ("DELIVERY LIMIT  : ", _percent),
    1569: ("LOW PRODUCT     :", _whole(7)),
    1574: ("LEAK ALARM LIMIT:", _whole(4)),
    1573: ("SUDDEN LOSS LIMIT:", _whole(6)),
    1544: ("TANK TILT       ", _signed),
    1551: ("PROBE OFFSET   :", _signed),
    1590: ("LEAK MIN PERIODIC:", _percent),
    1578: ("LEAK MIN ANNUAL : ", _percent),
    1586: ("TNK TST SIPHON BREAK:", None),
    1552: ("DELIVERY DELAY  : ", _whole(2)),
    1594: ("PUMP THRESHOLD  : ", lambda v: f"{v:05.2f}%"),
}
# `TANK PROFILE    : 1 PT`, `:  4 PTS`, `: 20 PTS`, `: LINEAR`, `: 50 PTS`
TANK_PROFILE_FACE = {"00": "1 PT", "01": " 4 PTS", "02": "20 PTS",
                     "03": "LINEAR", "04": "50 PTS"}


# the settings PORT SETTINGS holds, a port's own: UART, security code,
# modem, dial tone and DTR -- and not the receivers' beside them
PORT_SETTING_CODES = {"881", "536", "885", "886", "887", "889"}


def _port_face(console, step, device, lines):
    """A port setting as the bench's glass draws it -- which is the setup
    printout's own line: `COMM BOARD  : 1 (RS-232)` over ` BAUD RATE  :
    1200`, ` PARITY     : ODD`, ` STOP BIT   : 1 STOP`, ` DATA LENGTH: 7
    DATA` and `CODE : DISABLED`, inside PORT SETTINGS / PRESS <ENTER>
    (`transcripts/menuwalk2.jsonl`, 2026-10-08). The guide drew
    `COMM BOARD: 1` and `BAUD RATE: 1200`."""
    if (step.get("code") or "")[1:4] not in PORT_SETTING_CODES:
        return lines
    form = ((step.get("print") or {}).get("lines") or [None])[0]
    if not form or len(lines) < 2 or ":" not in str(lines[1]):
        return lines
    value = str(lines[1]).split(":", 1)[1].strip()
    board = console.comm_board_name(device)
    return [f"COMM BOARD  : {device} ({board})"[:COLS],
            form.format(value)[:COLS]]


def tank_faced(step):
    """Does this IN-TANK SETUP step draw a `TANK_FACES` face?"""
    try:
        return int((step.get("code") or "")[1:4], 16) in TANK_FACES
    except ValueError:
        return False


def _tank_face(console, step, device, lines):
    """IN-TANK SETUP's second line as the bench draws it, where it was
    read (`TANK_FACES`); every other screen as drawn."""
    if len(lines) < 2:
        return lines
    if step.get("profile"):
        name = TANK_PROFILE_FACE.get(console.tank_profile(device))
        if name:
            return [lines[0], "TANK PROFILE    : " + name]
        return lines
    tok = (step.get("code") or "")[1:4]
    if tok in ("612", "61D"):
        # `T#: 00` under an unprogrammed manifold, where this listed seven
        # zero pairs; a programmed one is not read
        held = str(shown(console, field_of(console, step, device) or {},
                         stored(console, step, device)) or "")
        if not held.replace("0", "").replace(",", "").strip():
            return [lines[0], "T#: 00"]
        return lines
    try:
        face = TANK_FACES.get(int(tok, 16))
    except ValueError:
        face = None
    if not face:
        return lines
    label, form = face
    f = field_of(console, step, device) or {}
    held = str(shown(console, f, stored(console, step, device, f)) or "")
    if form is None:
        if tok == "603" and not held.strip():
            # the product code defaults to the tank's own number, as I603
            # answers it (`blankrows`)
            held = str(device)
        return [lines[0], (label + held.strip())[:COLS]]
    try:
        value = float(held.rstrip("%").strip() or 0)
    except ValueError:
        return lines
    return [lines[0], (label + form(value))[:COLS]]


# the config screens read off the bench's glass: the tank's
# `SLOT 1 - 1 X X X` and the liquid card's `SLOT 2 - X 2 X X X X X X`
SLOT_CARD = {"601": "probe", "701": "liquid"}


def slot_line(console, code, module, cells):
    """The config screen's second line: which positions are connected.

    The guide draws the slot as `#`, and it is a placeholder: the bench's
    liquid config screen printed its card's slot, `SLOT 2 - X 2 X X X X X
    X`. The liquid card's is drawn so; the others keep the page's until one
    is read."""
    if code in SLOT_CARD:
        slots = [slot for slot, _bay, key, _name in console.cage_slots()
                 if key == SLOT_CARD[code]]
        where = slots[module - 1] if 0 < module <= len(slots) else "#"
        return f"SLOT {where} - {cells}"
    return "SLOT #: " + cells


def setup_lines(console, function, step, device=1, chart_open=True):
    """The two lines this setup step draws, as `_setup_lines` composes them
    and as a measured console draws its own (`MEASURED_FACES`)."""
    lines = _setup_lines(console, function, step, device, chart_open)
    if (function or {}).get("function") == "IN-TANK SETUP":
        lines = _tank_face(console, step, device, lines)
    if (function or {}).get("function") == "COMMUNICATIONS SETUP":
        lines = _port_face(console, step, device, lines)
    return _measured_face(function, lines)


def _setup_lines(console, function, step, device=1, chart_open=True):
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
        wires = console.positions(f["code"][1:4]) or f.get("slots") or 4
        base = ((device - 1) // wires) * wires
        cells = console.slot_text(f["code"][1:4], wires, base)
        return [module_head(text, base // wires + 1)[:COLS],
                slot_line(console, f["code"][1:4], base // wires + 1,
                          cells)[:COLS]]

    if not chart_open and _chart_locked(console, step, device):
        # "TANK PROFILE : 50 PTS / ENTER PASSCODE->______<"
        return ["TANK PROFILE : 50 PTS", "ENTER PASSCODE->______<"]

    if step.get("console"):
        f2 = FIELDS.get(step["console"], {})
        prompt = f2.get("prompt", text + ":").replace("%d", str(device))
        value = console_value(console, step, device)
        head, _p = setup_context(console, function, step, device)
        if f2.get("scope") == "system" or step.get("ca_devices"):
            # "TANK CHART SECURITY / CODE : 000000"; and CUSTOM ALARMS'
            # screens for one device, `L 2: SENSOR OUT ALARM` over `LBL:`
            head = named_head(console, step.get("head") or text, device,
                              letter)
        if step.get("smart_kind"):
            # `SN#: (10 char)       DISABLED`, 577013-800 Rev P Figure 7,
            # and it is exactly twenty-four columns with a ten character
            # serial in the middle of it -- the same ten characters V43's
            # index table answers with. The value is hard against the right
            # of the glass, which is what the figure's own spacing draws
            # and what makes the two words line up under each other.
            from . import wiresensors
            serial = wiresensors.isd_serial(console, device)
            line = f"{prompt} {serial}".rstrip()
            return [head[:COLS],
                    (line + value.rjust(COLS - len(line)))[:COLS]]
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
        # `88.32 INCH VOL : 000000`, 576013-623 Rev AN p.7-8, which draws the
        # ADD screen with the space before the colon five times on the page.
        # The VIEW screen on p.7-9 is flush -- `92.16 INCH VOL: 009800` -- and
        # that flush spelling is what this line used to carry, which is the
        # prefix collision M4 named. The height is the panel's, so away from
        # the panel there is none and the field heads at 0.00. FIDELITY F15.
        return [head[:COLS], "0.00 INCH VOL : " + masks.apply("000000", "0")]

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
                       screen_word(f, masked(f, shown(
                           console, f, stored(console, step, device, f)))),
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
            second(label, screen_word(f, masked(f, shown(
                console, f, stored(console, step, device, f)))),
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


def printed_value(field, value, mask=None):
    """A number on paper carries the decimals its screen carries.

    The display masks a temperature compensation value to `+060.0` and the
    printout reads `60.0`, not `60`: the sign and the leading zeros are the
    field's fixed width and go, the decimal place is the console's precision
    and stays. So the mask says how many, which is also why a tank diameter
    prints `96.00` and a full volume prints `9995`.

    A row can print to a different precision from the screen it is set on,
    and then its spec carries the mask the PAPER uses instead. Two of those
    are on the tape. 576013-623 Rev AN draws the thermal coefficient screen
    `THERMAL COEFF: 0.00000` where the report prints `.000690` -- the same
    number, six places and no leading zero, which is how a seventh digit
    fits in seven columns -- and the percent limits are masked `000%` on the
    panel where the paper prints `95.0`. A mask with no digit before its
    point drops the zero. See FIDELITY T6.
    """
    f = field or {}
    paper = mask is not None
    mask = mask or f.get("mask") or ""
    if "." not in mask or (not paper and f.get("kind") != "float"):
        # the field's own mask only decides a float; a spec's mask is the
        # paper saying what it prints, whatever kind holds the value
        return value
    try:
        out = f"{float(value):.{len(mask.split('.')[-1])}f}"
    except (TypeError, ValueError):
        return value
    if not mask.split(".")[0].strip() and out.startswith("0."):
        out = out[1:]
    return out


def panel_value(console, function, step, device):
    """What the panel puts on its second line, without the panel's label.

    The report row carries its own label in its own column, so what it
    wants from the screen is the value alone.
    """
    # the PAPER's own composition: the glass's measured faces are the
    # glass's, and the setup report keeps the tape's (`setup_lines`)
    drawn = _setup_lines(console, function, step, device, chart_open=False)
    if len(drawn) < 2:
        return ""
    line = str(drawn[1]).strip()
    for label in (step.get("l2") or "", step.get("text", "")):
        label = (label.split("(")[0].replace("%d", str(device))
                 .strip().rstrip(":"))
        if label and line.upper().startswith(label.upper()):
            line = line[len(label):].lstrip(": ").strip()
            break
    return line


# MODIFY TANK/METER MAP, 576013-623 Rev AN p.17-5. The heading is 24
# characters on the nose -- `BUS SLOT FUEL METER TANK` -- and the row under
# it is too, its five cells at the columns the page draws them at rather
# than evenly spaced:
#
#     BUS SLOT FUEL METER TANK
#     X    XX   XX    XX    XX
#
# (column, width) each, and `X` is the page's own word for a cell nobody has
# dialled to anything yet.
MAP_CELLS = ((0, 1), (5, 2), (10, 2), (16, 2), (22, 2))
MAP_BLANK = " ".join("X" * w for _at, w in MAP_CELLS)
# "99: Tank with no probe" -- the screen's own word for the tank the
# command writes as -1.
MAP_NO_PROBE = 99


def map_row_text(cells):
    """The five cells at the columns p.17-5 draws them at."""
    row = [" "] * COLS
    for (at, width), value in zip(MAP_CELLS, cells):
        row[at:at + width] = str(value).rjust(width)[-width:]
    return "".join(row)[:COLS]


# INDIVIDUAL METER OFFSET, 576013-623 Rev AN p.17-6, drawn the same way:
#
#     FUEL METER TANK OFFSET
#     XX     XX    XX   +X.XX
#
# and only the last cell is a value the panel sets -- "press CHANGE. The
# Offset value flashes '0'." The other three say which meter it belongs to.
OFFSET_CELLS = ((0, 2), (7, 2), (13, 2), (18, 5))
OFFSET_BLANK = ["XX", "XX", "XX", "+X.XX"]


def offset_row_text(cells):
    """The four cells at the columns p.17-6 draws them at."""
    row = [" "] * COLS
    for (at, width), value in zip(OFFSET_CELLS, cells):
        row[at:at + width] = str(value).rjust(width)[-width:]
    # the page's row is 23 columns and the display is 24: the console does
    # not pad a line out to the glass, and no other screen here does either
    return "".join(row)[:COLS].rstrip()


def offset_cells(console, key):
    """One meter's row: its position, its meter, its tank, its offset."""
    if key is None:
        return list(OFFSET_BLANK)
    tank = int((console.meter_map.get(key) or {}).get("tank") or 0)
    if tank < 0:
        tank = MAP_NO_PROBE
    pct = console.meter_offsets.get((key.fp, key.meter), {}).get("pct", 0.0)
    return [f"{key.fp:02d}", f"{key.meter:02d}", f"{tank:02d}",
            f"{float(pct):+.2f}"]


def map_lines(console):
    """The tank/meter map as the PAPER lays it out.

    The wire's report is 34 columns wide -- `FUELING POSITION - METER -
    TANK MAP` over ` BUS  SLOT  FUEL_P  METER  TANK` -- and this console's
    paper is 24, so the wire's rendering wraps into nonsense on it. The
    tape settles what the paper actually says: the RECONCILIATION SETUP
    block ends on `BUS SLOT FUEL METER TANK`, the SCREEN's own heading, on
    a site with nothing mapped. So the rows go under it in the screen's own
    columns and the screen's own vocabulary, which is where `99` comes from
    for a tank with no probe. No sample anywhere shows this block WITH rows
    in it -- see UNKNOWNS.
    """
    out = ["BUS SLOT FUEL METER TANK"]
    for key in sorted(console.meter_map):
        tank = int(console.meter_map[key].get("tank") or 0)
        if tank < 0:
            tank = MAP_NO_PROBE
        out.append(map_row_text([key.bus, f"{key.slot:02d}",
                                 f"{key.fp:02d}", f"{key.meter:02d}",
                                 f"{tank:02d}"]))
    return out


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
    if step.get("map"):
        # The tank/meter map is a screen you dial a row into, and the paper
        # carries the whole map rather than the row the panel happens to be
        # showing. The tape ends RECONCILIATION SETUP on this heading.
        return map_lines(console)
    spec = step.get("print")
    if spec and spec.get("when") is not None and not console.visible(
            {"when": spec["when"]}, device):
        # A gate the REPORT has and the panel walk does not. 576013-623 Rev
        # AN says to step past the reconciliation alarm threshold and its
        # offset when the alarm is off -- "To leave the Periodic
        # Reconciliation Alarm disabled, press STEP until you see the REMOTE
        # REPORT FORMAT message" -- and the tape's console, which has
        # `ALARM:          DISABLED`, prints neither row under it. The
        # screens stay on the panel, because the evidence for their absence
        # is a printout. See FIDELITY T8.
        return []
    if spec and spec.get("off"):
        # A prompt is a way in, not a value, and some of them carry one
        # anyway: ENTER RCVR PHONE NO. draws the number under it and RCVR
        # CONFIG draws how many destinations are configured. Neither is on
        # the tape's receiver block, which prints the head, the type, the
        # port, the two retry rows and the confirmation and stops. The
        # screen stays on the panel. See FIDELITY T4.
        return []
    if spec and not (set(spec) - {"section", "gap", "when"}):
        # a spec that only places the row -- which sub-heading it falls
        # under, how much blank goes above it -- and says nothing about the
        # row itself, which is still the screen the console draws
        spec = None
    if spec is None:
        if ((FIELDS.get(step.get("field") or step.get("code") or "") or {})
                .get("kind") == "slots"):
            # The module's slot map -- "SLOT #: 1 2 3 4" under TANK CONFIG
            # - MODULE 1 -- is how a technician tells the console which
            # wires are connected, and no report carries it. The tape's 568
            # lines hold one `CONFIG` (SYSTEM SETUP's `CONFIG: STANDARD`)
            # and one `SLOT` (RECONCILIATION's `BUS SLOT FUEL METER TANK`
            # column head) and no module configuration screen in any of its
            # seven blocks, for a console with two probes and four sensors.
            # It is a way in like the `PRESS <ENTER>` screens below, and it
            # was printing once per DEVICE besides. See FIDELITY T7.
            return []
        drawn = _setup_lines(console, function, step, device, chart_open=False)
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
    if want == "secured":
        # The tape prints TANK CHART SECURITY as an enable flag where the
        # panel draws the passcode itself. 576013-623 Rev AN says which is
        # which: the CODE screen "appears if a passcode has not been
        # entered (default). All zeros disables Tank Chart Security", and
        # the passcode prompt "appears if Tank Chart Security is enabled --
        # a passcode other than all zeros has been entered". So the state
        # is the code, and the report prints the state. See FIDELITY T9.
        value = "ENABLED" if console.chart_secured() else "DISABLED"
    elif want == "clock":
        # SET DATE and SET TIME are two screens and one stamp on paper
        value = clock_words(console.now())
    elif want.startswith("threshold:"):
        value = threshold_units(console, want.split(":", 1)[1])
    elif want == "panel":
        # TANK PROFILE holds "1" and draws "1PT": the word list is the
        # value, and the panel already has it
        value = panel_value(console, function, step, device)
    else:
        value = printed_value(f, shown(console, f,
                                       stored(console, step, device, f)),
                              spec.get("mask"))
        if not str(value).strip():
            # Not every step's value comes out of a field. TANK PROFILE is
            # rendered from a word list, CAL UPDATE and WATER ALARM FILTER
            # are console settings, SIPHON MANIFOLDED is a list of tank
            # numbers. All of them already draw correctly on the panel, so
            # the panel's second line is the value -- with its own label
            # taken off, because the printed row supplies its own.
            value = panel_value(console, function, step, device)
    out = []
    head = spec.get("head")
    if head:
        head = named_head(console, head, device,
                          device_code((function or {}).get("function", "")))
        if "{board}" in head:
            # The comm bay's head names the card in the slot, in a field six
            # wide: the tape prints `(S-SAT )`, `(RS-485)` and `(MTCOMM)`
            # and each of them comes to 24 with the label in front. A name
            # shorter than six is padded, which is where that trailing space
            # inside the brackets comes from. See FIDELITY T8.
            head = head.format(board=console.comm_board_name(device).ljust(6))
        out.append(head[:COLS])
    # A row can need more than one line and more than one value. The tape
    # prints a percent limit as its percent AND the gallons that works out
    # to, under a wrapped label:
    #
    #     HIGH PRODUCT
    #               % MAX :   95.0
    #           (GALLONS) :   9495
    #
    # so a spec carries `lines` where one is not enough, and `{gallons}`
    # where the value on the paper is not the value in the field. See
    # FIDELITY T1.
    lines = spec.get("lines")
    if lines is None:
        lines = [spec["line"]] if spec.get("line") is not None else []
    none = spec.get("none")
    if none is not None and not any(c in "123456789" for c in str(value)):
        # 576013-623 Rev AN draws the siphon screen "T#: 00,00,00,00,00,00,00"
        # and the tape prints "T#: NONE" for the same tank. The panel keeps
        # the manual's zeros; the paper says what they mean.
        value = none
    gallons = ""
    if any("{gallons" in l for l in lines):
        code = (step.get("code") or "")[1:4]
        worked = console.limit_volume(code, device) if code else None
        gallons = f"{worked:.0f}" if worked is not None else ""
        if code in console.PERCENT_LIMITS and not console.panel_percent(code):
            # Before version 33 the limit is entered in gallons and there
            # is no percent to print: the gallons row alone, under the
            # head, or carrying the label where the row is its own head.
            g = [l for l in lines if "{gallons" in l][0]
            if not head:
                label = lines[0].split(":")[0]
                g = f"{label}:{{gallons:>{COLS - len(label) - 1}}}"
            lines = [g]
    other = ""
    if spec.get("other"):
        # A row that carries two of the console's values. The panel draws
        # the leak test frequency as `TEST ALL TANK:` over `CSLD`, and the
        # tape prints the two the other way round on one line:
        # `TEST CSLD    : ALL TANK`. So a spec can name a second field and
        # interpolate it as {other}. See FIDELITY T4.
        f2 = FIELDS.get(spec["other"])
        other = str(printed_value(f2, shown(console, f2,
                                            stored(console,
                                                   {"code": spec["other"]
                                                    .split(".")[0],
                                                    "field": spec["other"]},
                                                   device, f2))))
    for line in lines:
        out.append(line.replace("%d", str(device))
                   .format(value, gallons=gallons, other=other)[:COLS])
    if spec.get("after") == "fourpoint":
        out.extend(four_point_rows(console, device))
    if spec.get("after") == "rcvralarms":
        out.extend(receiver_alarm_rows(console, device))
    if spec.get("after") == "rcvrdial":
        out.extend(receiver_dial_rows(console, device))
    return [x.rstrip() for x in out if x.rstrip()]


def receiver_dial_rows(console, receiver):
    """When one autodial destination is called, in the paper's shape.

    The tape's only receiver:

        D 8:
        DIAL WEEKLY
        THR
        DIAL TIME :  5:26 PM

    which is 52B's method digit and the field whose width that digit
    decides. The day is the console's own three letters -- THR, not THU;
    576013-635 Rev AA heads the fuel management report "SUN MON TUE WED
    THR FRI SAT" in seven printouts and the tape prints THR here.

    The tape shows the WEEKLY form and no other, so the middle line is
    rendered from the same words the wire renders for each method and only
    that one is measured. See FIDELITY T4.
    """
    from .wirelists import DIAL_METHOD, DIAL_WIDTH, _clock
    from .fieldio import DAYS, MONTHS
    raw = (console.receiver_dial.get(receiver) or "").strip()
    if not raw:
        return []
    method, rest = raw[0], raw[1:1 + DIAL_WIDTH.get(raw[0], 0)]
    out = [f"DIAL {DIAL_METHOD.get(method, '')}".rstrip()]
    if method == "1":                       # ON DATE: YYMMDD then HHmm
        out.append(f"{rest[2:4]}/{rest[4:6]}/{rest[0:2]}")
    elif method == "2":                     # ANNUALLY: MM W D then HHmm
        out.append(f"{MONTHS[int(rest[0:2]) - 1]}   WEEK {rest[2]}   "
                   f"{DAYS[int(rest[3]) - 1]}")
    elif method == "3":                     # MONTHLY: W D then HHmm
        out.append(f"WEEK {rest[0]}   {DAYS[int(rest[1]) - 1]}")
    elif method == "4":                     # WEEKLY: D then HHmm
        out.append(DAYS[int(rest[0]) - 1])
    time = _clock(rest[-4:]) if len(rest) >= 4 else ""
    if time:
        out.append(f"DIAL TIME :{time:>9}")
    return out


def receiver_alarm_rows(console, receiver):
    """What one autodial destination is set to be called about.

    The same list 52C renders over the wire, in the paper's form: the
    console's own words for each alarm, or `- NO ALARM ASSIGNMENTS -` when
    there are none, which is what the tape's only receiver prints and what
    576013-635 Rev AA draws for `D 1: HOME OFFICE`. See FIDELITY T4.
    """
    out = []
    for aa, nn, tt in console.receiver_alarms.get(receiver, []):
        row = console.alarm_name(aa, nn)
        out.append(f"     {row}" + (f" TANK {int(tt)}" if int(tt) else ""))
    return out or ["- NO ALARM ASSIGNMENTS -"]


def four_point_rows(console, tank):
    """The three chart rows a 4 PTS tank prints under its full volume.

        FULL VOL :   9995
      72.0 INCH VOL :   8070
      48.0 INCH VOL :   5031
      24.0 INCH VOL :   1983

    576013-635 Rev AA's 605 is "Set Tank 4 Point Full, 3/4, 1/2, 1/4
    Volumes" and holds all four in one field, and this console has held all
    four in `S60501` and its three parts all along without ever printing
    three of them. The heights are three quarters, a half and a quarter of
    the tank diameter, which is how the tape's 96 inch tank comes to print
    72.0, 48.0 and 24.0. See FIDELITY T1.
    """
    from .console import Console as _C
    if _C.PROFILE_NAME.get(console.tank_profile(tank)) != "4 PTS":
        return []
    diameter = console.limit("607", tank) or 0.0
    out = []
    code = f"S605{tank:02d}"
    raw = console.values.get(code)
    if not raw:
        return []
    for share, part in ((0.75, "p75"), (0.50, "p50"), (0.25, "p25")):
        f = FIELDS.get(f"S60501.{part}")
        try:
            volume = float(fieldio.decode(f, code, raw))
        except (TypeError, ValueError):
            continue
        out.append(f"{diameter * share:>6.1f} INCH VOL :{volume:>7.0f}")
    return out


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
