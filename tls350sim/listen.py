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
"""How a listener in this program is brought down.

Every listener here is a thread sitting in `accept()` or `recvfrom()`, and
every one of them was stopped by closing its socket from another thread.
**That does not work.** On Linux a thread already blocked in `accept()` is
not woken by another thread closing the descriptor: the call stays blocked
until a connection actually arrives, and then it is served -- by a listener
that was shut down, possibly on a descriptor number the kernel has since
handed to something else. Reproduced by connecting after `CardNetwork.stop()`
had returned and pulling inventory, and getting it. FIDELITY R30.

So the socket is never the thing that is waited on directly. `wait_readable`
waits with a TIMEOUT and re-asks a predicate each time round, which makes
the stop take effect within one tick whatever the platform does with a
closed descriptor, and `accept` re-checks that same predicate AFTER the
accept returns -- because "stop the listener" has to mean "refuse what
arrives after it" as well as "stop waiting", and there is always a gap
between the two.

Nothing here is about exposure. A bench listener that goes on answering
after it has been stopped is the same defect, and this is the whole of the
mechanism for both.
"""
import select

# How long a listener sleeps before asking whether it should still be
# running. Short enough that `stop()` is over before a person notices, long
# enough that an idle card is not a busy loop: at 0.25 s a listener costs
# four wake-ups a second and nothing else.
TICK = 0.25


def running_always():
    return True


def wait_readable(sock, running=None, timeout=TICK):
    """Wait until `sock` has something, or the listener should stop.

    True when there is something to take, False when the caller should
    give up. A closed or invalid descriptor is a False rather than a
    raised error, because closing the socket remains a legitimate way to
    end a listener and this is the path that notices.
    """
    running = running or running_always
    while running():
        try:
            ready = select.select([sock], [], [], timeout)[0]
        except (OSError, ValueError):
            # Closed underneath us, or already invalid. Either way there is
            # nothing left to wait for.
            return False
        except TypeError:
            # Not a selectable object at all -- a stand-in socket in a test,
            # which has `recvfrom` and no `fileno`. Hand it straight to the
            # read, which is exactly what this code did before there was a
            # select in front of it. Failing soft here rather than making
            # every fake grow a file descriptor keeps the helper usable
            # with a duck-typed socket, and the caller's own stop flag
            # still ends the loop.
            return True
        if ready:
            return True
    return False


def accept(srv, running=None, timeout=TICK):
    """`srv.accept()` that a stop can interrupt, and that refuses after one.

    Returns `(conn, addr)`, or None when the listener should end. The
    predicate is asked again after the accept: a connection that arrived
    in the gap between the stop and this line is closed unanswered, which
    is what "a listener that has been stopped still answers" was about.
    """
    running = running or running_always
    if not wait_readable(srv, running, timeout):
        return None
    try:
        conn, addr = srv.accept()
    except OSError:
        return None
    if not running():
        try:
            conn.close()
        except OSError:
            pass
        return None
    return conn, addr


def recvfrom(sock, size, running=None, timeout=TICK):
    """The same for a datagram socket. Returns `(data, addr)` or None."""
    running = running or running_always
    if not wait_readable(sock, running, timeout):
        return None
    try:
        data, addr = sock.recvfrom(size)
    except OSError:
        return None
    if not running():
        return None
    return data, addr
