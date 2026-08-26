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
"""Shape-checking for the setup codes that have no field definition.

75 codes store a setting, read it back correctly, and are reachable only over
the wire -- there is no panel step for them, so there is no field, so nothing
checked what they were given. They accepted anything. `fieldio`'s own
docstring says why that is wrong:

    "entering an out-of-range value is REFUSED here, because the real console
    refuses it. A simulator that accepts anything teaches you that a value is
    fine when the console would have rejected it."

The manual writes each one's data as a template -- `QQrr.rr`, `TTYYMMDD`,
`SSAAxxx.xx` -- and the device prefix is consumed before the data reaches
here, so what is left is the shape to check.

**This checks SHAPE, not range.** The length and the character class, nothing
more. That is deliberate and it is the conservative direction: a wrong range
check refuses a value the console would take, which is worse than accepting a
value it would refuse, because it breaks restoring a real site's backup. Where
the manual's template is ambiguous the code is left unchecked rather than
guessed at -- see SKIP.

Letter conventions, taken from how the manual uses them:

    a   ASCII text, any character
    h   hex, so A-F count as digits
    .   a literal decimal point
    149 literal digits, the verification code

Everything else is a digit. A trailing run of `a` is a label and may be sent
short, because a console pads labels rather than demanding the full width.
"""

import re

from .console import DEVICE_PREFIXED

# Formats this cannot read, left unchecked on purpose rather than guessed:
#
#   520, 51B   the width of the tail depends on the method digit in the data,
#              the same trap as 52B -- these belong in wirelists.py if anyone
#              ever wires them to a panel step
#   54C        "00GG.G..." -- the manual's own ellipsis, meaning a run of
#              unstated length
#   525        "S525RRn", one character for the port number to dial. A dump
#              from a live site holds two ("0103" for S52501, the receiver
#              prefix and then 03). Revision Y says the same as Revision U,
#              so this is not a transcription slip a later revision fixed.
#   52F        "S52FRRAAf", three characters. The same dump holds eight
#              ("04010000"), and the leading pair is not the receiver number
#              either, so it is not a prefix -- the real layout is something
#              neither revision describes.
#
# Five codes used to be in here for AMBIGUITY. Revision U describes 51B, 520,
# 54C, 5BE and 5BF loosely enough that guessing was the only option, and
# **Revision Y describes all five exactly** -- an ambiguity in the manual you
# have is not an ambiguity in the manual. Three of the five are checked now.
#
# The other two joined 525 and 52F, because a live console disagrees with the
# exact description as flatly as it disagrees with the vague one:
#
#   520        Rev Y gives eight methods, each with a width its own digit
#              decides (1..5 as 52B, 6 a single character, 7 and 8 reserved).
#              A live dump holds ten characters per receiver -- "0734219190",
#              repeated for receivers 01 through 08 -- which is no documented
#              method's width and does not begin with a documented method.
#   54C        Rev Y says "12 Reid Vapor Pressures" and states the rejection
#              outright: "if any value is outside the range 0.0 to 15.0, or
#              all table values are zero". A live dump holds a leading "12"
#              and then twenty-eight zeros -- both a shape the manual does not
#              describe AND the exact contents it says are rejected.
#
# Four codes now, and every one found the same way: by replaying a real site's
# backup. That test is the only thing in this repo that can catch a manual
# being wrong, which is why it is worth more than its size.
SKIP = {"525", "52F", "520", "54C"}

# The manual's Set data template for each code, with the device prefix
# stripped. Transcribed from the Command Format line of each function's page
# in 576013-635 Revision U.
TEMPLATES = {
    "505": "UL", "506": "f", "507": "dd", "508": "dd", "509": "f",
    "50A": "ddd", "50B": "ddd", "518": "PP", "525": "n", "529": "f",
    "52E": "hh", "52F": "AAf", "537": "AB", "538": "AB", "54D": "aaa",
    "567": "149f", "568": "149f", "569": "hh", "5BC": "AANNTTSS",
    "5E2": "HHmm", "613": "f", "614": "f", "618": "f", "619": "f",
    "61F": "tdd.ddddd", "62B": "YYMMDD", "62E": "c", "727": "HHHH",
    "728": "AAxxx.xx", "729": "AATT", "72A": "GGGG.t", "72B": "f",
    "72C": "PPPP", "74B": "f", "74C": "aaaaaaaaaaaaaaaaaaaa", "74D": "t",
    "74E": "a", "774": "tt", "775": "rr.rr", "776": "ppp.pp",
    "777": "I.hh", "778": "I.hh", "78B": "MMDD", "78E": "f", "78F": "PP",
    "791": "NNaaaaaaaaaaaa", "796": "dd", "79B": "ssGGGGGG",
    "79C": "MMDDGGGGGG", "7A5": "tt", "7AA": "MMDD", "7AD": "LLL",
    "7AE": "tt", "7B5": "eeYYMMDDHHmmGGGGGG", "7BC": "AANNTTSS",
    "7BD": "AANNTTSS", "7BE": "AANNTTSS", "803": "tnTT", "804": "m",
    "851": "149", "852": "149", "853": "149", "891": "149",
    "8BC": "AANNTTSS",
    # Revision Y's wording, which Revision U left vague:
    "51B": "MMWDHHmm",                      # tt is the device, not data
    "5BE": "AANNfaaaaaaaaaaaaaaaaaaa",      # a nineteen character label
    "5BF": "AANNTTflpbdaaaaaaaaaaaaaaaaaaa",
}


# Thirteen of these write the same value two different ways. The Display
# format spells a number out -- "QQrr.rr" -- and the Computer format sends it
# as an ASCII Hex IEEE float, "QQFFFFFFFF". Same setting, same code, different
# width and different character class, chosen by the case of the command
# letter. Checking a lowercase command against the uppercase template refuses
# every float a tool ever sends, which is how this was found: replaying a real
# site's backup, which is all lowercase.
COMPUTER_TEMPLATES = {
    "61F": "tFFFFFFFF", "72A": "FFFFFFFF", "72C": "FFFFFFFF",
    "775": "FFFFFFFF", "776": "FFFFFFFF", "777": "FFFFFFFF",
    "778": "FFFFFFFF", "78F": "FFFFFFFF", "79B": "ssFFFFFFFF",
    "79C": "MMDDFFFFFFFF", "7AD": "FFFFFFFF",
    "7B5": "eeYYMMDDHHmmFFFFFFFF",
}


def _pattern(template):
    """Turn one manual template into a regex.

    **The last field may arrive short; the ones before it may not.** A tool
    that sends `S7B50101<stamp>5050` has truncated the trailing six-digit
    gallons field to four, and a real console takes it -- you can truncate the
    tail of one of these, you cannot truncate the middle, because a short
    interior field would silently shift every field after it. That is the
    distinction worth enforcing, and it is also the only one there is evidence
    for. Nothing in the manual says a short trailing field is refused, and
    refusing one would break restoring a real site's backup, which is a worse
    failure than accepting a value the console would have argued with.
    """
    runs, i = [], 0
    while i < len(template):
        ch = template[i]
        if ch.isdigit():
            # a literal, which in practice is always the 149 verification code
            run = ""
            while i < len(template) and template[i].isdigit():
                run += template[i]
                i += 1
            runs.append(re.escape(run))
            continue
        n = 0
        while i < len(template) and template[i] == ch:
            n += 1
            i += 1
        if ch == ".":
            runs.append(re.escape("." * n))
        elif ch == "a":
            runs.append(("." + "{0,%d}" % n) if n > 1 else ".")
        elif ch in "hF":
            runs.append("[0-9A-Fa-f]{%d}" % n)
        else:
            runs.append("[0-9]{%d}" % n)
    # Relax only the final run, and only its lower bound -- but NOT when the
    # template ends in a time. A four digit gallons field where six were asked
    # for is a tool being terse; a three digit HHmm is a malformed clock, and
    # there is no reading of "143" that is a time.
    if runs and not template.endswith("HHmm"):
        runs[-1] = re.sub(r"\{(\d+)\}$", lambda m: "{1,%s}" % m.group(1),
                          runs[-1])
    trailing_label = template.endswith("a")
    return re.compile("^" + "".join(runs) + "$"), trailing_label


_CACHE = {code: _pattern(t) for code, t in TEMPLATES.items()}
_CACHE_COMPUTER = {code: _pattern(t) for code, t in COMPUTER_TEMPLATES.items()}


def _prefixed(code):
    """Whether this function's data repeats its device number.

    `DEVICE_PREFIXED` holds INTEGERS -- 0x613, not "613" -- so a membership
    test with the token string silently never matches and every code looks
    unprefixed. That cost an hour; the conversion belongs here so no caller
    has to remember it.
    """
    try:
        return int(code, 16) in DEVICE_PREFIXED
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 63B, the fifty point tank chart
# ---------------------------------------------------------------------------
# "S63BTTnnffIII.hhGGGGGG...ffIII.hhGGGGGG", a count and then that many
# height/volume pairs. It does not fit the one-template machinery above
# because the length depends on a count IN the data, the same shape of problem
# wirelists.py exists for.
#
# The manual gives the display form TWO ways -- "III.hh" and "II.h, GGGG," --
# so only the COMPUTER form is checked, where a pair is unambiguous: a two
# character add/remove flag then two ASCII Hex IEEE floats. The display form
# is left alone rather than guessed between two spellings.
CHART_FLAGS = {"01", "02"}      # 01=Added, 02=Remove
CHART_PAIR = 2 + 8 + 8
CHART_MAX_PAIRS = 14            # "A maximum of 14 pairs can be set per
                                # command to avoid overflowing the buffer"


# ---------------------------------------------------------------------------
# 520, Set Receiver Auto Dial Type and Start Time II
# ---------------------------------------------------------------------------
# The successor to 52B and it decides its own width the same way, from the
# method digit. Sharing 52B's table would be wrong by one: 520 has a sixth
# method whose payload is a single character, and two reserved methods with no
# payload defined at all.
DIAL2_WIDTH = {"1": 10, "2": 8, "3": 6, "4": 5, "5": 4, "6": 1}
DIAL2_RESERVED = {"7", "8"}


def dial2_ok(data):
    """520: a method digit, then a field whose width the digit decides."""
    body = data or ""
    if not body:
        return False
    method, rest = body[0], body[1:]
    if method in DIAL2_RESERVED:
        # "Reserved" is not "anything goes"; the manual defines no payload,
        # so the honest answer is that nothing fits one
        return False
    if method not in DIAL2_WIDTH:
        return False
    if len(rest) != DIAL2_WIDTH[method]:
        return False
    if method == "6":
        return rest.isdigit()
    return rest.isdigit() or rest.upper().endswith(DISABLED)


# ---------------------------------------------------------------------------
# 54C, Set CSLD Evaporation Reid Vapor Pressure Chart
# ---------------------------------------------------------------------------
# Revision U writes the data as "GG.G..." and leaves the run's length to the
# ellipsis. Revision Y says what it is: "12 Reid Vapor Pressures", and gives
# the rule outright -- "The command will be rejected if any value is outside
# the range 0.0 to 15.0, or all table values are zero."
#
# That is the only place in this module that checks a RANGE rather than a
# shape, and it is here because the manual states the rejection itself. It is
# not inferred from what looks sensible.
REID_COUNT = 12
REID_MIN, REID_MAX = 0.0, 15.0


def reid_ok(data, computer):
    """54C: twelve vapour pressures, and the manual's own rejection rule."""
    body = (data or "").strip()
    values = []
    if computer:
        if len(body) != REID_COUNT * 8:
            return False
        from tls350sim import packed
        for i in range(REID_COUNT):
            try:
                values.append(packed.unhexfloat(body[i * 8:(i + 1) * 8]))
            except Exception:
                return False
    else:
        parts = [p for p in re.split(r"[,\s]+", body) if p]
        if len(parts) != REID_COUNT:
            return False
        for p in parts:
            try:
                values.append(float(p))
            except ValueError:
                return False
    if any(v < REID_MIN or v > REID_MAX for v in values):
        return False
    return any(v for v in values)


def chart_ok(data, computer):
    """63B: a pair count, then that many flag/height/volume groups."""
    if not computer:
        return True
    body = data or ""
    if len(body) < 2 or not body[:2].isdigit():
        return False
    count, rest = int(body[:2]), body[2:]
    if not 1 <= count <= CHART_MAX_PAIRS:
        return False
    if len(rest) != count * CHART_PAIR:
        return False
    for i in range(count):
        pair = rest[i * CHART_PAIR:(i + 1) * CHART_PAIR]
        if pair[:2] not in CHART_FLAGS:
            return False
        if not all(ch in "0123456789ABCDEFabcdef" for ch in pair[2:]):
            return False
    return True


def known(code):
    """True if this function code has a shape to check."""
    return code in _CACHE and code not in SKIP


# "HHmm=Hour, Minute (EE00=Disabled)". The disabled marker is not a time and
# does not fit a four digit template, and it is a legal value everywhere a
# time is wanted. wirelists.py knows the same thing about 52B and 75A.
DISABLED = "EE00"


def valid(code, data, aggregate=False, computer=False):
    """True if `data` fits the manual's template for `code`.

    An unknown code is True: not knowing the shape is not the same as knowing
    the value is wrong, and refusing on ignorance is the failure mode this
    module is written to avoid.

    `aggregate` matters. A Set addressed to device 00 on a MULTI-DEVICE
    function carries the all-devices form -- every device's value run together,
    so S61300 holds `011021031` for three tanks where S61301 holds `1`. That is
    not the template repeated, it is the template with a device number in front
    of each copy, and checking it against one copy refuses a real site's backup.

    Device 00 on its own is not enough to tell: a console-wide setting like
    S50600 is ALWAYS device 00 and is a single value, so treating every 00 as
    an aggregate stops checking about a third of these. The caller knows which
    functions are multi-device and says so.
    """
    # The aggregate test comes FIRST, before the per-code specials as well as
    # before the templates. A device-00 Set on a multi-device function carries
    # every device's value run together, and that is not the single-value
    # shape any of these describe -- S52000 in a real backup holds a receiver
    # number and a dial setting, repeated eight times. Checking the specials
    # ahead of it refuses a real site's backup, which the replay test caught.
    if aggregate:
        return True
    # SKIP has to be honoured here as well as in `known`, because the
    # per-code specials below do not go through `known` at all. 520 and 54C
    # are in SKIP and have specials, so without this they would still be
    # checked by the very function that was meant to stop checking them.
    if code in SKIP:
        return True
    if code == "63B":
        return chart_ok(data, computer)
    if code == "54C":
        return reid_ok(data, computer)
    if code == "520":
        return dial2_ok(data)
    if not known(code):
        return True
    if computer and code in _CACHE_COMPUTER:
        pattern, trailing_label = _CACHE_COMPUTER[code]
        template = COMPUTER_TEMPLATES[code]
    else:
        pattern, trailing_label = _CACHE[code]
        template = TEMPLATES[code]
    text = (data or "")

    def fits(candidate):
        if trailing_label:
            # a label may arrive short; a console pads rather than demands
            candidate = candidate.rstrip()
        if template.endswith("HHmm") and candidate.upper().endswith(DISABLED):
            candidate = candidate[:-4] + "0000"
        return bool(pattern.match(candidate))

    # Some Sets REPEAT the device in their data -- S61301 carries "011", the
    # tank number and then the flag -- and some do not: S89101 carries just
    # "149". Both shapes appear on codes the console calls device-prefixed, so
    # the prefix is tried and not assumed. Stripping it unconditionally eats
    # the "14" out of "149", which is how this was found.
    #
    # Trying both directions can only make the check more permissive, and
    # permissive is the direction to fail in: refusing a value a real console
    # takes breaks restoring a real site's backup.
    if fits(text):
        return True
    # a device number is two decimal digits, so anything else in those two
    # positions is not a prefix and must not be discarded as one
    return (_prefixed(code) and len(text) > 2 and text[:2].isdigit()
            and fits(text[2:]))
