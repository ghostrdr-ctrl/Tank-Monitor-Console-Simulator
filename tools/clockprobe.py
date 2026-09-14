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
"""Run the suite as if the wall clock were somewhere else in the day.

    python tools/clockprobe.py 9          # nine hours from now
    python tools/clockprobe.py 3 9 15 21  # four times, one per hour

A console fixture that advances `clock_offset` from wherever the clock
happens to be is a fixture whose SHAPE depends on the time of day the suite
is run. The site closes its BIR day at 06:00; a fixture that ticks fifteen
hours forward crosses that close if it starts after 15:00 and does not if it
starts before, and the reports it then builds carry an extra row per tank.
`start_at` in `tests/test_console.py` names the failure mode -- "a test that
runs the clock forward from whenever it happens to be run is a test that
fails at midnight" -- and pinning a fixture is the fix. This is how you find
which fixtures need it.

**It found two, and it found them by the suite going red at three in the
afternoon on a green commit**, which is the worst way to find out. See
FIDELITY V5.

The mechanism is a shim over `time.time` and `time.localtime`, installed
before pytest imports anything. Every part of this package reads the clock
through `time.localtime(...)` or `time.mktime(console.now())`, so moving
those two moves the whole console; a module that had done `from time import
localtime` would escape it, and none does.
"""
import sys
import time

_time, _localtime = time.time, time.localtime


def install(hours):
    """Shift what this process thinks the wall clock says."""
    shift = float(hours) * 3600.0

    def shifted_time():
        return _time() + shift

    def shifted_localtime(when=None):
        return _localtime(shifted_time() if when is None else when)

    time.time = shifted_time
    time.localtime = shifted_localtime


def main(argv):
    hours = argv[1:] or ["0"]
    if len(hours) > 1:
        # one child per hour, because the shim has to be in place before
        # pytest imports the tests and it cannot be taken out again
        import subprocess
        worst = 0
        for hour in hours:
            print(f"=== {hour:>3}h ===", flush=True)
            done = subprocess.run([sys.executable, __file__, hour])
            worst = max(worst, done.returncode)
        return worst
    install(hours[0])
    import pytest
    return pytest.main(["-q", "--no-header", "-p", "no:cacheprovider"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
