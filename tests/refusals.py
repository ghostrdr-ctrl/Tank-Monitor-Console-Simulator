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
"""What a refused command looks like, for the tests that ask.

A console has two refusals. `9999FF` is for a function code it does not
know. A Set whose code it knows and whose VALUE it will not take is answered
the way the bench TLS-350 answered one on 2026-09-18: the echo, the stamp,
and one `?` for every character of data sent (display format), or the echo,
the stamp, one `?` per character of the field's width, `&&` and the checksum
(computer format). A test that only means "this was not accepted" takes
either.
"""


def is_bare(reply):
    """Whether a reply is a bare frame: the echo and the stamp, nothing after.

    What a console answers for a code it knows about a card, key or feature
    it has not got -- `I70100` with no liquid sensor card, `IV0000` with no
    ISD -- in both formats; the bench TLS-350, 2026-09-18. Not a refusal.
    """
    if isinstance(reply, bytes):
        reply = reply.decode("latin-1")
    if "9999FF" in reply or not reply.startswith("\x01"):
        return False
    body = reply.strip("\x01\x03")
    if "&&" in body:                                  # computer
        head = body.split("&&")[0]
        return len(head) == 16 and head[6:].isdigit()
    rows = [r for r in body.split("\r\n") if r.strip()]
    return len(rows) == 2                             # display


def is_refused(reply):
    """Whether a reply refuses its command, in either of the two forms."""
    if isinstance(reply, bytes):
        reply = reply.decode("latin-1")
    if "9999FF" in reply or reply.strip("\x01\x03\r\n").startswith("9999"):
        return True
    rows = reply.split("\r\n")
    if len(rows) > 3 and rows[3] and set(rows[3]) == {"?"}:
        return True                                   # display
    head = reply.split("&&")[0]
    return reply.startswith("\x01") and "&&" in reply and head.endswith("?") \
        and head.rstrip("?") != head                  # computer
