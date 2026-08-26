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
import sys
import textwrap
import threading

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
from tls350sim import paths, update, xport
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
    ap.add_argument("--port", type=int, default=10001)
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
                         "Interface Module: the setup menu on tcp/9999 and "
                         "DeviceInstaller discovery on udp/30718. Needs "
                         "permission to bind those ports.")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.check_update:
        sys.exit(update.cli_check())

    console = Console(a.state)
    if a.seed:
        n = console.seed(a.seed)
        print(f"[sim] seeded {n} value(s) from {os.path.basename(a.seed)}")

    def start_xport(log):
        cfg = xport.XPortConfig(paths.xport_config_file())
        cfg.port = a.port          # the tunnel is this console's own port
        threading.Thread(target=xport.serve, args=(cfg, a.host, log),
                         kwargs={"powered": lambda: console.powered},
                         daemon=True).start()
        return cfg

    if a.headless:
        if a.xport:
            start_xport(print if not a.quiet else None)
        serve(console, a.host, a.port, not a.quiet)
        return

    from tls350sim.ui import SimApp
    app = SimApp(console, a.port)
    if a.xport:
        start_xport(app.log)
    # Tk owns the main thread; the socket loop must not block it.
    threading.Thread(target=serve,
                     args=(console, a.host, a.port, not a.quiet, app.log),
                     daemon=True).start()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[sim] stopped")
