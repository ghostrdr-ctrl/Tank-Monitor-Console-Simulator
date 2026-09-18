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
"""Run the TLS-350 simulator.

    python run.py                     front panel + bench, serial on 10001
    python run.py --port 10002        somewhere else
    python run.py --seed site.vrset   start out looking like a real site
    python run.py --headless          serial only, no window

Point any TLS tool at 127.0.0.1 and the port it prints.
"""
import argparse
import io
import os
import re
import sys
import textwrap
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A windowed build has no console: PyInstaller sets sys.stdout and
# sys.stderr to None, and the first print or argparse --version then dies
# with "NoneType has no attribute write", which the app shows as an
# unhandled-exception dialog. Give both streams somewhere harmless to go so
# the GUI still launches; a real console (running from source, or a console
# build) keeps its own streams untouched.
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

from tls350sim import APP_NAME, DISCLAIMER, PUBLISHER, __version__
from tls350sim import exposed, paths, update, xport, xportnet
from tls350sim.console import Console
from tls350sim.wire import serve

# GPL-3.0 section 5(d): a program that talks to a person interactively should
# be able to say what it is, who wrote it, that it comes with no warranty, and
# where the licence is. `--version` and the line under the console face are
# where this one says it.
NOTICE = f"""{APP_NAME} {__version__}
Copyright (C) 2026 {PUBLISHER}

This program comes with ABSOLUTELY NO WARRANTY. It is free software, and you
are welcome to redistribute it under the terms of the GNU General Public
License version 3 or later. See the LICENSE file, or
<https://gnu.org/licenses/gpl-3.0>.

{textwrap.fill(DISCLAIMER, 76)}"""


# Six bytes as twelve hex digits: bare, or in pairs with one kind of
# separator between every pair.
_MAC = re.compile(r"[0-9A-Fa-f]{12}"
                  r"|[0-9A-Fa-f]{2}([-:])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}")


def card_mac(text):
    """`--card-mac` as the six bytes the card will carry, or argparse's error.

    Checked here, before a card is built, rather than in `xport`: the
    discovery reply puts the MAC into a six-byte field by slice assignment,
    so a short address shortens the frame instead of failing.
    """
    if not _MAC.fullmatch(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a hardware address: give six bytes as twelve "
            "hex digits, as AA-BB-CC-DD-EE-FF, AA:BB:CC:DD:EE:FF or "
            "AABBCCDDEEFF")
    return bytes.fromhex(re.sub("[-:]", "", text))


def exposure_banner(level, a, capture_path):
    """What is about to be open, said plainly, before it opens.

    Printed rather than logged because the person reading it is standing at
    a terminal about to walk away from the process, and this is the last
    moment the decision is cheap to change.
    """
    out = ["", "[sim] exposure: %s" % exposed.LABELS[level]]
    for line in textwrap.wrap(exposed.BLURB[level], 72):
        out.append("[sim]   " + line)
    if capture_path:
        out.append("[sim]   capture: %s" % capture_path)
    if level != exposed.EXPOSED:
        out.append("")
        return out
    for line in (
            "",
            "  This console is about to answer strangers. Before you leave it:",
            "",
            "   *  Run it as an unprivileged user, in a container, on a host",
            "      that does not matter. This mode closes the holes a honeypot",
            "      must not have -- it does NOT make the process a sandbox.",
            "   *  Expect to be indexed as exposed critical infrastructure and",
            "      to field abuse reports. Do not run it on a home connection",
            "      or on any network you do not own.",
            "   *  Change the site. The demo site ships with this program, so",
            "      it is a fingerprint: a scanner that has seen one of these",
            "      recognises the tank names in the next one. Seed your own",
            "      with --seed. GasPot's own defaults became its signature.",
            "   *  Check your jurisdiction on collecting traffic from people",
            "      who did not consent to it.",
            ""):
        out.append("[sim]" + line if line else "[sim]")
    return out


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=f"""{APP_NAME}: a training simulator for TLS-350
compatible tank monitor consoles.""",
        epilog=textwrap.fill(DISCLAIMER, 76))
    ap.add_argument("--version", action="version", version=NOTICE,
                    help="version, copyright and licence, then exit")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 (default) keeps it on this machine; "
                         "0.0.0.0 exposes it to your LAN")
    ap.add_argument("--port", type=int, default=None,
                    help="the tunnel port. With --xport the card's own "
                         "programmed port is used unless this overrides it; "
                         "without it, 10001")
    ap.add_argument("--seed", metavar="FILE.vrset",
                    help="preload programming from a backup")
    ap.add_argument("--state", metavar="FILE.json",
                    default=paths.default_state_file(),
                    help="where programming is kept between runs "
                         "(installed copies keep it under your user profile)")
    ap.add_argument("--headless", action="store_true",
                    help="serial only, no window")
    ap.add_argument("--check-update", action="store_true",
                    help="ask GitHub whether a newer release is published, "
                         "then exit")
    ap.add_argument("--xport", action="store_true",
                    help="also emulate the Lantronix XPort inside the TCP/IP "
                         "Interface Module: the setup menu on tcp/9999, the "
                         "web manager on tcp/80 and DeviceInstaller "
                         "discovery on udp/30718. Needs permission to bind "
                         "those ports.")
    ap.add_argument("--xport-web-port", type=int, default=None, metavar="PORT",
                    help="serve the card's web manager here instead of 80, "
                         "for when something else already has port 80")
    ap.add_argument("--no-xport-web", action="store_true",
                    help="emulate the card but leave its web manager off")
    ap.add_argument("--card-ip", metavar="A.B.C.D",
                    help="program the card with this address before it "
                         "starts, as though someone had set it in the menu")
    ap.add_argument("--reset-card", action="store_true",
                    help="put the card back to its defaults, address "
                         "included, before starting. Use this when a card "
                         "has been programmed into a corner")
    ap.add_argument("--card-mac", type=card_mac, metavar="AA-BB-CC-DD-EE-FF",
                    help="give the card this hardware address instead of "
                         "one derived from the host name")
    ap.add_argument("--card-trace", action="store_true",
                    help="log every 77FEh frame the card receives, answered "
                         "or not. For working out what a tool is asking for")
    ap.add_argument("--blank-card", action="store_true",
                    help="start with a card that has no address at all, as "
                         "a new module does. Only discovery answers until "
                         "an address is assigned with DeviceInstaller or "
                         "arp and telnet")
    ap.add_argument("--claim-ip", action="store_true",
                    help="add the card's programmed address to this "
                         "machine's network adapter, so it answers ping and "
                         "other machines can reach it. Needs administrator "
                         "rights; without them the card still answers on "
                         "this machine's own addresses")
    ap.add_argument("--exposure", choices=exposed.LEVELS,
                    default=exposed.BENCH, metavar="LEVEL",
                    help="who is allowed to reach this: bench (default, "
                         "this machine only), lan (a network you trust), or "
                         "exposed (a public address: writes frozen, "
                         "connections capped and rated, replies delayed, "
                         "every byte captured). See --exposed")
    ap.add_argument("--exposed", action="store_true",
                    help="shorthand for --exposure exposed --host 0.0.0.0. "
                         "Run it as an unprivileged user in a container: "
                         "this closes the holes a honeypot must not have, "
                         "it does not make the process a sandbox")
    ap.add_argument("--capture", metavar="FILE.jsonl",
                    help="append every exchange here as JSON lines -- one "
                         "object per direction, carrying the source address "
                         "and the command code. Implied by --exposed; the "
                         "field names follow GasPot's so an existing SIEM "
                         "pipeline reads both")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    # --exposed is the shorthand, and it wins: somebody who typed it meant
    # it, and silently serving a bench policy because another flag came
    # later is the one failure mode this must not have.
    if a.exposed:
        a.exposure = exposed.EXPOSED
        if a.host == "127.0.0.1":
            a.host = "0.0.0.0"
    level = a.exposure
    capture_path = a.capture
    if level == exposed.EXPOSED and not capture_path:
        capture_path = paths.default_capture_file()
    # Always built, even on a bench. The listeners take this object once
    # and keep it, so the bench's own dropdown can only reach them by
    # arming the object they already hold -- never by making a new one.
    capture = exposed.Capture(capture_path,
                              enabled=level != exposed.BENCH)
    policy = exposed.Policy(level, capture=capture)
    if policy.readonly:
        exposed.freeze_writes(True)
    if level != exposed.BENCH and not a.quiet:
        for line in exposure_banner(level, a, capture_path):
            print(line)

    if a.check_update:
        sys.exit(update.cli_check())

    console = Console(a.state)
    if a.seed:
        n = console.seed(a.seed)
        print(f"[sim] seeded {n} value(s) from {os.path.basename(a.seed)}")

    def start_card(log):
        """Bring the card up on the address it is programmed with.

        With the card emulated it owns the tunnel as well as the setup menu,
        the web manager and discovery, because on a real site they are all
        one card at one address. Reprogram it and they all move together.
        """
        # An exposed card does not keep its programming between runs and
        # does not read a file somebody could have left pointing anywhere:
        # it starts from the defaults every time, so a restart is always a
        # clean restart. `XPortConfig.save` would refuse the write anyway;
        # passing no path means there is nothing to refuse.
        cfg = xport.XPortConfig(None if policy.readonly
                                else paths.xport_config_file())
        if a.card_mac:
            cfg.mac = a.card_mac        # six bytes: `card_mac` saw to it
        elif not policy.stable_identity:
            # The derived MAC carries three bytes of a SHA-256 of this
            # machine's host name, which is not the emulator's to publish.
            # See `xport.default_mac`.
            cfg.mac = xport.default_mac(anonymous=True)
        if a.blank_card:
            cfg.blank_card()
        elif a.reset_card:
            cfg.reset_card(ip=a.card_ip)
        elif a.card_ip:
            cfg.program(ip=a.card_ip)
        # A card with no address is left with none: that is what a new
        # module is, and giving it one is the first job on a site.
        if a.port:
            cfg.program(port=a.port)
        if a.xport_web_port:
            # The card keeps its HTTP port like any other setting, so this
            # has to be able to put it back to 80 as well as move it off.
            cfg.http_port = a.xport_web_port
        cfg.save()
        net = xportnet.CardNetwork(console, cfg, log=log,
                                   powered=lambda: console.powered,
                                   claim=a.claim_ip,
                                   web=not a.no_xport_web,
                                   fallback_host=a.host,
                                   trace=a.card_trace,
                                   policy=policy, capture=capture)
        net.start()
        return cfg, net

    tunnel_port = a.port or 10001

    if a.headless:
        if a.xport:
            log = print if not a.quiet else None
            cfg, _net = start_card(log)
            if not a.quiet:
                # Name the address that will actually answer. This used to
                # print the PROGRAMMED one whatever had been bound, so on a
                # machine without that address the first line on screen sent
                # you to an address that times out. See FIDELITY Z5.
                bound = _net.bind_host()[0]
                if not cfg.assigned():
                    print("[sim] the card has no address yet; assign one "
                          "before it will answer")
                elif bound == cfg.ip:
                    print("[sim] connect to %s:%d for inventory"
                          % (cfg.ip, cfg.port))
                else:
                    print("[sim] connect to %s:%d for inventory -- the card "
                          "is programmed %s, which this machine does not have"
                          % ("127.0.0.1" if bound == "0.0.0.0" else bound,
                             cfg.port, cfg.ip))
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                return
        serve(console, a.host, tunnel_port, not a.quiet,
              policy=policy, capture=capture,
              gate=exposed.Gate(policy, capture=capture))
        return

    from tls350sim.ui import SimApp
    app = SimApp(console, tunnel_port, policy=policy, capture=capture)
    if a.xport:
        cfg, net = start_card(app.log)
        app.attach_card(cfg, net)
    else:
        # Tk owns the main thread; the socket loop must not block it.
        threading.Thread(
            target=serve,
            args=(console, a.host, tunnel_port, not a.quiet, app.log),
            kwargs={"policy": policy, "capture": capture,
                    "gate": exposed.Gate(policy, capture=capture)},
            daemon=True).start()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[sim] stopped")
