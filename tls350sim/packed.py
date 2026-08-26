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
"""The number format the computer-format replies are written in.

Every packed reply in both serial manuals writes a number the same way: an
"ASCII Hex IEEE float", which is the four bytes of a big-endian single
precision float printed as eight uppercase hex digits. A list of them is
introduced by its own count, "NN - Number of eight character Data Fields to
follow (Hex)".

It is two lines of code and it was written out thirteen times before it was
written down once. The reason it lives in a module of its own rather than
beside the things that use it is the import graph: console, delivery and
presets need it as much as wire and fieldio do, and fieldio imports console,
so there is no home for it among them that does not make a cycle. This module
imports nothing from the package, so anything may have it.
"""
import struct


def hexfloat(value):
    """One number, as eight uppercase hex digits."""
    return struct.pack(">f", float(value)).hex().upper()


def hexfloats(values):
    """"NN - Number of eight character Data Fields to follow (Hex)"."""
    values = list(values)
    return f"{len(values):02X}" + "".join(hexfloat(v) for v in values)


def unhexfloat(text):
    """The number back out of eight of those hex digits.

    Everything malformed comes back as ValueError, struct's own error
    included, so a caller has one exception to catch rather than two. The
    slicing stays with the caller: the manuals put the eight characters at
    the front of some fields and the back of others, and which end it is is
    the caller's business, not this function's.
    """
    try:
        return struct.unpack(">f", bytes.fromhex(text))[0]
    except struct.error as exc:
        raise ValueError(str(exc)) from exc
