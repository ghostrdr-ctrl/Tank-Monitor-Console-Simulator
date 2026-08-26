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
"""Where this program is, and where it is allowed to write.

Run from a source tree, the console keeps its programming next to `run.py`,
which is convenient and is what the tests expect. Run from an installed copy,
that directory is `C:\\Program Files\\...`, which a normal user cannot write
to: the first attempt to save programming would fail, and it would fail at
the end of a session rather than the start of one.

So an installed copy keeps its state under the user's own profile instead.
`frozen()` is what tells the two apart -- PyInstaller sets `sys.frozen`.
"""
import os
import sys

from . import APP_NAME, PUBLISHER


def frozen():
    """True when running from a PyInstaller build rather than a source tree."""
    return getattr(sys, "frozen", False)


def program_dir():
    """The directory the program itself lives in."""
    if frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def user_data_dir():
    """Per-user, always writable, and it survives an uninstall.

    Windows puts this under LOCALAPPDATA, which is the right place for state
    a user would not think to back up. Everywhere else follows the XDG
    convention. The directory is created on first use.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = (os.environ.get("XDG_DATA_HOME")
                or os.path.expanduser("~/.local/share"))
    d = os.path.join(base, PUBLISHER, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def default_state_file():
    """Where programming is kept between runs.

    Next to the source tree when running from source; under the user's
    profile when installed.
    """
    if frozen():
        return os.path.join(user_data_dir(), "console_state.json")
    return os.path.join(program_dir(), "console_state.json")


def xport_config_file():
    """Where the emulated XPort keeps its network configuration.

    Always per-user: the card's IP address is the machine's, not the
    checkout's, and it must survive between runs the way a real card's
    Flash does.
    """
    return os.path.join(user_data_dir(), "xport.json")


def settings_file():
    """Preferences that are not console programming -- update checks, mostly.

    Always per-user, source tree or not: a preference is the person's, not
    the checkout's.
    """
    return os.path.join(user_data_dir(), "settings.json")
