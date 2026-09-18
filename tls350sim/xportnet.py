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
"""Putting the simulated card on the address it has been programmed with.

On a real site you program a card with an address and a port, and from then
on that is where everything happens: you ping it, you point DeviceInstaller
at it, you open its web pages, and you pull inventory from its tunnel port.
Nothing is at 127.0.0.1. A trainee who learns "connect to localhost" has
learned the wrong lesson, so this module makes the emulated card answer at
the address programmed into it.

Three things follow from that, and all three are the real card's behaviour:

  * Change the IP and save, and the card comes back on the new address. The
    old one stops answering. So do the sessions that were open on it.
  * Change the tunnel port and save, and inventory now comes from the new
    port. 10001 stops answering.
  * Everything moves together -- the tunnel, the setup menu on 9999, the web
    manager and the discovery responder are all faces of one card at one
    address.

What needs privilege, and what does not
---------------------------------------
A host answers on an address only if that address is on one of its network
adapters. Adding one is an administrative action on every operating system.
So this module works at two levels:

  * Unprivileged, it binds to the programmed address when the machine
    already has it, and otherwise falls back to whatever `--host` gave it --
    which is `127.0.0.1` unless somebody said otherwise, NOT every address.
    Connecting, programming and reading inventory all work on the fallback;
    whether anything OFF this machine can reach it depends on which fallback
    it was, which is why `bind_host`'s note names the address it bound
    rather than describing the fallback in general. See FIDELITY Z5.
  * Elevated, with `claim=True`, it adds the programmed address to an
    adapter for as long as the simulator is running, and removes it on the
    way out. Then the address answers ping, and other machines on the same
    subnet -- including a laptop running DeviceInstaller -- reach it exactly
    as they would reach a real card.

`status()` reports which of the two is in force and says so plainly, so a
trainee is never left guessing why an address does not answer.
"""
import socket
import subprocess
import sys
import threading
import time
import weakref

from . import exposed
from . import xport as _x

# The port the arp-and-telnet procedure knocks on to hand an unaddressed
# card its address. Lantronix reserves it for exactly that.
ASSIGN_PORT = 1


def local_addresses():
    """Every IPv4 address this machine currently answers on."""
    found = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    # getaddrinfo misses addresses that are not in the host name's records,
    # which is exactly what a freshly added one is.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # TEST-NET-1: goes nowhere
        found.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    if sys.platform == "win32":
        found |= _windows_addresses()
    return found


def _windows_addresses():
    out = set()
    try:
        res = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "ipaddresses",
             "level=normal"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return out
    for line in res.stdout.splitlines():
        parts = line.split()
        for p in parts:
            if p.count(".") == 3 and p.replace(".", "").isdigit():
                out.add(p)
    return out


def is_local(ip):
    return bool(ip) and ip not in ("0.0.0.0", "") and ip in local_addresses()


# ---------------------------------------------------------------------------
# claiming an address


def _windows_adapter():
    """The adapter a claimed address should be added to.

    The one carrying this machine's default route, so a claimed address in
    the site's subnet is reachable from the rest of the network.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    try:
        res = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "ipaddresses",
             "level=normal"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    name = None
    for line in res.stdout.splitlines():
        low = line.strip()
        if low.lower().startswith("interface "):
            name = low.split('"')[1] if '"' in low else None
        if primary in line and name:
            return name
    return None


def claim_ip(ip, netmask="255.255.255.0", adapter=None, log=None):
    """Add `ip` to an adapter so this machine answers on it.

    Returns the adapter it was added to, or None. Needs administrator
    rights; without them netsh refuses and this returns None rather than
    raising, because running unprivileged is a supported way to use the
    simulator, just a more limited one.
    """
    if sys.platform != "win32":
        if log:
            log("-- claiming an address is implemented for Windows only; "
                "add it by hand with `ip addr add %s/24 dev <iface>`" % ip)
        return None
    adapter = adapter or _windows_adapter()
    if not adapter:
        return None
    try:
        res = subprocess.run(
            ["netsh", "interface", "ipv4", "add", "address",
             'name=%s' % adapter, "address=%s" % ip, "mask=%s" % netmask],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        if log:
            log("-- could not add %s: %s" % (ip, e))
        return None
    if res.returncode != 0:
        text = (res.stdout + res.stderr).strip().splitlines()
        why = text[0] if text else "netsh refused"
        if log:
            log("-- could not add %s to %s: %s "
                "(run the simulator as administrator to claim an address)"
                % (ip, adapter, why))
        return None
    if log:
        log("-- %s added to %s; the card answers there until it is stopped"
            % (ip, adapter))
    return adapter


def release_ip(ip, adapter, log=None):
    if sys.platform != "win32" or not adapter:
        return
    try:
        subprocess.run(
            ["netsh", "interface", "ipv4", "delete", "address",
             'name=%s' % adapter, "address=%s" % ip],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if log:
            log("-- %s removed from %s" % (ip, adapter))
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------------------
# the supervisor


class CardNetwork:
    """Every face of the card, bound to the address it is programmed with.

    Watches the configuration and, when the address or the tunnel port
    changes, takes the listeners down and brings them back up on the new
    one. That gap is the card rebooting; a real one does the same and drops
    whatever was connected.
    """

    POLL = 0.5

    def __init__(self, console, config, log=None, powered=None,
                 claim=False, web=True, fallback_host="0.0.0.0",
                 trace=False, direct=False, policy=None, capture=None):
        self.console = console
        self.config = config
        self.log = log
        self.powered = powered or (lambda: True)
        self.claim = claim
        self.web = web
        self.fallback_host = fallback_host
        self.trace = trace
        self.direct = direct
        # One policy, one gate and one capture for every face of the card,
        # because they are one card: a per-source connection limit that each
        # listener counted separately would let a single address hold six
        # times what it is allowed. Handed no policy, the card serves as it
        # always has and nothing below does anything. See `exposed.py`.
        self.policy = policy or exposed.Policy(exposed.BENCH)
        self.capture = capture
        # Always built, never conditionally: `Gate.admit` waves everything
        # through while the policy has no limits, so a gate on a bench card
        # costs one dictionary. Built only for a capped level, a card that
        # started on the bench would have none -- and then changing the
        # exposure from the bench's own dropdown would cap nothing, which
        # is the one case the dropdown exists for.
        self.gate = exposed.Gate(self.policy, capture=capture)
        self.claimed = None          # (ip, adapter) while an address is held
        # The address the OPERATOR started this card on, before anything
        # from the network could have moved it. `--claim-ip` runs `netsh`
        # as an administrator to add an address to this machine's adapter,
        # and the address it adds is `config.ip` -- which the setup menu
        # and the web manager can both write. So on a network card, a
        # stranger chose which IPv4 address an elevated process added to,
        # and removed from, the host's default-route adapter. Not command
        # injection -- the arguments are a list and an address is four
        # bytes rendered as a dotted quad -- but a privileged state change
        # driven by remote input, which is enough.
        self._claimable = config.ip
        self._closers = []
        # the tunnel's open sessions, weakly: one that ended on its own is
        # not a thing to close, and should not be kept alive to be closed
        self._sessions = weakref.WeakSet()
        self._stop = False
        self._bound = None           # (host, port, web_port) now serving
        self._lock = threading.Lock()

    # -- what address to use ---------------------------------------------

    def wanted(self):
        """The address and ports the card is programmed with."""
        return (self.config.ip, self.config.port, self.config.http_port)

    def _fallback_note(self):
        """What falling back to `fallback_host` actually did, in words.

        The note used to read "the card is answering on every address
        instead" whatever it had bound -- and the fallback is `--host`,
        which DEFAULTS to 127.0.0.1, so the commonest case said the
        opposite of what happened: one address, and the one no other
        machine can reach. A person who read it and found the LAN address
        refused had been told the wrong thing by the one line written to
        stop them guessing. See FIDELITY Z5.
        """
        host = self.fallback_host
        if host == "0.0.0.0":
            return "so the card is answering on every address this machine has"
        if host.startswith("127.") or host == "localhost":
            return ("so the card is answering on %s alone -- nothing off this "
                    "machine can reach it. Use --host 0.0.0.0 for the LAN, or "
                    "--claim-ip (as administrator) to hold the card's own "
                    "address" % host)
        return "so the card is answering on %s alone" % host

    def bind_host(self):
        """The address to bind to, and why.

        Returns (host, note). A programmed address this machine has is used
        as-is. One it does not have cannot be bound, so the card falls back
        to whatever `--host` gave it -- and the note says WHICH, because
        only `0.0.0.0` is "every address" and it is not the default.
        """
        ip = self.config.ip
        if not ip or ip == "0.0.0.0":
            return (self.fallback_host,
                    "no address programmed yet, " + self._fallback_note())
        if is_local(ip):
            return ip, ""
        return (self.fallback_host,
                "%s is not an address on this machine, %s"
                % (ip, self._fallback_note()))

    def status(self):
        """A line a person can act on, for the window and the log."""
        ip, port, web = self.wanted()
        host, note = self.bind_host()
        shown = ip if ip and ip != "0.0.0.0" else self.config.effective_ip()
        text = ("card at %s -- inventory on %d, setup menu on %d, "
                "web manager on %d" % (shown, port, _x.SETUP_PORT, web))
        if self.claimed:
            text += " (address held on %s)" % self.claimed[1]
        elif note:
            text += " [" + note + "]"
        return text

    # -- running ----------------------------------------------------------

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self._stop:
            self._bring_up()
            while not self._stop and self.wanted() == self._bound:
                time.sleep(self.POLL)
            if self._stop:
                break
            # Something was saved. Take the card down and bring it back on
            # the new address, the way it reboots after "Save and exit".
            if self.log:
                self.log("-- card rebooting onto its new settings")
            self._tear_down()
            time.sleep(0.4)
        self._tear_down()

    def _remember(self, sock):
        with self._lock:
            self._closers.append(sock.close)

    def _remember_session(self, conn):
        with self._lock:
            self._sessions.add(conn)

    def _remember_server(self, srv):
        with self._lock:
            self._closers.append(srv.shutdown)
            self._closers.append(srv.server_close)

    def _bring_up(self):
        from . import wire, xportmenu
        cfg = self.config
        self._bound = self.wanted()

        if self.claim:
            self._claim_now()

        host, note = self.bind_host()

        # A card with no address answers nothing that is addressed by IP,
        # because it has no IP to be addressed at. Only the two ways of
        # reaching an unaddressed card work: the discovery broadcast, and
        # the port-1 knock the arp-and-telnet procedure uses. This is a new
        # module out of the box, and giving it an address is the first job.
        if not cfg.assigned():
            if self.policy.exposed:
                # An exposed card with no address would take one from the
                # first stranger who knocks on port 1, and then it is wherever
                # they put it. There is no deflection to offer here -- the
                # whole procedure IS the write -- so the port simply does not
                # open, and an exposed card must be given its address on the
                # command line before it is ever reachable.
                if self.log:
                    self.log("-- EXPOSED: the card has no address and will "
                             "not take one from the network. Give it one "
                             "with --card-ip before exposing it.")
                self._start_discovery()
                return
            if self.log:
                self.log("-- the card has no IP address, as a new module "
                         "does. Assign one before anything else will answer:")
                self.log("--   DeviceInstaller: Assign IP, or")
                self.log("--   a command prompt: arp -s <ip> %s"
                         % _x.mac_hex(cfg.mac, "-"))
                self.log("--                    telnet <ip> 1")
                self.log("--   then telnet <ip> 9999 to program the rest")
            self._start_discovery()
            self._start_assign(self.fallback_host)
            return

        if self.log:
            # `status()` already ends with the note in brackets; logging it
            # again put the same sentence on two consecutive lines, and the
            # sentence is now long enough that the repeat reads as two
            # different problems. See FIDELITY Z5.
            self.log("-- " + self.status())

        # the serial tunnel: the port the card is programmed with
        threading.Thread(
            target=self._guard,
            args=("inventory tunnel", wire.serve, (self.console, host,
                                                   cfg.port, False, self.log),
                  {"on_socket": self._remember,
                   "on_conn": self._remember_session,
                   "policy": self.policy, "gate": self.gate,
                   "capture": self.capture}),
            daemon=True).start()

        # the setup menu on 9999
        threading.Thread(
            target=self._guard,
            args=("setup menu", xportmenu.serve_setup,
                  (cfg, host, self.log, self.powered),
                  {"on_socket": self._remember, "policy": self.policy,
                   "gate": self.gate, "capture": self.capture}),
            daemon=True).start()

        self._start_discovery()

        if self.web:
            from . import xportweb
            threading.Thread(
                target=self._guard,
                args=("web manager", xportweb.serve_web,
                      (cfg, host, self.log, self.powered, cfg.http_port),
                      {"on_server": self._remember_server,
                       "capture": self.capture, "policy": self.policy,
                       "gate": self.gate}),
                daemon=True).start()

    def _start_discovery(self):
        """Discovery on 30718, bound to every address on purpose.

        A tool looking for a card broadcasts, and a socket bound to one
        address does not see a broadcast on this platform. This is also the
        only thing that answers a card with no address, which is exactly why
        DeviceInstaller can find a module you cannot yet ping.
        """
        disc = _x.Discovery(self.config, log=self.log,
                            powered=self.powered, trace=self.trace,
                            policy=self.policy, gate=self.gate,
                            capture=self.capture)
        threading.Thread(target=disc.serve, args=(self.fallback_host,),
                         daemon=True).start()
        # The same protocol answers on TCP 30718, and that is where a tool
        # reads a device's configuration once it has found it.
        # A second, address-specific UDP binding. Off by default: it was
        # meant to win the configuration reads back from a tool sharing this
        # machine, and it does the opposite -- holding the specific address
        # stops DeviceInstaller binding the port it sends those reads from,
        # so it stops sending them at all. See the note in xport.serve_directed.
        if self.direct:
            threading.Thread(target=disc.serve_directed,
                             args=(self.config.ip,), daemon=True).start()
        threading.Thread(target=disc.serve_tcp,
                         args=(self.fallback_host,),
                         kwargs={"on_socket": self._remember},
                         daemon=True).start()
        self._disc = disc

    def _start_assign(self, host):
        """The port-1 knock that gives an unaddressed card its address.

        Veeder-Root's procedure, and Lantronix's before it, is to put the
        wanted address in the ARP table against the card's MAC and then open
        a connection to it -- the card sees a packet on its own MAC carrying
        an address it does not have, takes that address, and drops the
        connection. From then on it answers there.

        Emulated by reading the local end of the accepted socket: whatever
        address the client aimed at is the address the card takes. It needs
        this machine to be able to reach that address, which is the same
        condition the real procedure has.
        """
        threading.Thread(target=self._assign_loop, args=(host,),
                         daemon=True).start()

    def _assign_loop(self, host):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, ASSIGN_PORT))
            srv.listen(1)
        except OSError as e:
            if self.log:
                self.log("-- the card's assignment port is not listening: %s"
                         % e)
            return
        self._remember(srv)
        while True:
            try:
                conn, _addr = srv.accept()
            except OSError:
                return
            try:
                wanted = conn.getsockname()[0]
            except OSError:
                wanted = None
            try:
                conn.close()
            except OSError:
                pass
            if not wanted or wanted in ("0.0.0.0", "127.0.0.1"):
                # A knock at the loopback address tells the card nothing
                # useful; a real one never arrives that way.
                if self.log:
                    self.log("-- assignment knock at %s ignored: that is not "
                             "an address the card could be given" % wanted)
                continue
            if self.config.assigned():
                continue
            if self.policy.exposed:
                if self.log:
                    self.log("-- assignment knock at %s refused: an exposed "
                             "card does not take its address from the "
                             "network" % wanted)
                continue
            self.config.ip = wanted
            if self.config.netmask_bits == 0:
                self.config.netmask_bits = 8
            self.config.save()
            if self.log:
                self.log("-- the card took the address %s. Now telnet to it "
                         "on 9999 to program the rest." % wanted)
            return

    def _guard(self, what, fn, args, kwargs):
        try:
            fn(*args, **kwargs)
        except OSError as e:
            if self.log:
                self.log("-- the card's %s could not start: %s" % (what, e))

    def _claim_now(self):
        ip = self.config.ip
        if not ip or ip == "0.0.0.0":
            self._release_now()
            return
        if self.policy.level != exposed.BENCH and ip != self._claimable:
            # The address moved after startup, so something programmed it,
            # and off the bench that something may have been a stranger.
            # An elevated `netsh add address` is not a thing to do on a
            # remote party's say-so: they could name the subnet's gateway,
            # or an address belonging to another host.
            #
            # The card still MOVES -- the listeners rebind and `bind_host`
            # falls back exactly as it does for any address this machine
            # does not have. It simply is not claimed. Start with
            # `--card-ip` to claim a chosen address off the bench.
            if self.log and self.claimed:
                self.log("-- not claiming %s: the card's address was "
                         "changed after it started, and this is not a "
                         "bench. Use --card-ip." % ip)
            self._release_now()
            return
        if self.claimed and self.claimed[0] == ip:
            return
        self._release_now()
        if is_local(ip):
            return                   # already ours, nothing to add
        adapter = claim_ip(ip, self.config.netmask, log=self.log)
        if adapter:
            self.claimed = (ip, adapter)

    def _release_now(self):
        if self.claimed:
            release_ip(self.claimed[0], self.claimed[1], log=self.log)
            self.claimed = None

    def _tear_down(self):
        with self._lock:
            closers, self._closers = self._closers, []
            sessions, self._sessions = list(self._sessions), weakref.WeakSet()
        # The sessions go down with the listeners. This closed the listening
        # sockets only, so a client connected before a reboot onto a new
        # address went on being answered on the old one while new
        # connections were refused. FIDELITY Z3.
        for conn in sessions:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        for close in closers:
            try:
                close()
            except (OSError, RuntimeError, ValueError):
                pass
        disc = getattr(self, "_disc", None)
        if disc:
            disc.close()
        if self._stop:
            self._release_now()

    def stop(self):
        self._stop = True
        self._tear_down()
