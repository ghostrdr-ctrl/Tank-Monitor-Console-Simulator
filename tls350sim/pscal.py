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
"""Calibrating an ISD vapor pressure sensor from the front panel.

577013-800 Rev P p.20-45 and 577013-937 Rev J Figure 46 draw the same walk,
"This menu only appears if this Smartsensor type is a pressure sensor":

    s 1: VAPOR PRESSURE     "the current uncalibrated value read by the
    PRESSURE: -XX.XXX        pressure sensor"
    ENTER ZERO REFERENCE    "Enter reference pressure value from calibrated
    PRESSURE: +XX.XXX        test device at pressure sensor via TLS Console
                             front panel, e.g., ambient pressure (0.0). This
                             is the first point of the calibration slope."
    READ ZERO VALUE         "Wait until the read zero pressure value
    PRESSURE: -XX.XXX        stabilizes and no longer changes, then press STEP."
    ENTER SPAN REFERENCE    "... e.g., 2psi. This is the second point"
    READ SPAN VALUE
    CALB STATUS: PASS       "only ... after all 4 values have been
                             successfully obtained and the calibrated slope
                             and offset are within acceptable limits"

and a CALIBRATION HISTORY printout of SLOPE and OFFSET. The arithmetic is
the two points the pages name. What they do not give -- the limits, what a
sensor reads before it is calibrated, what the screen says when a
calibration does not pass -- is UNKNOWNS A55. FIDELITY I11.

The console reported a slope between 0.9 and 5.2 and an offset between 0.0
and 5.1 for every sensor over V83, generated per call, always passed, and
no procedure anywhere could have produced them.
"""
import time

from . import readings

# "within acceptable limits": no page gives them. UNKNOWNS A55.
SLOPE_LIMITS = (0.9, 1.1)
OFFSET_LIMIT = 0.5
KEEP = 255                      # V83: "records per category [001-255]"


class Calibrations:
    """Each vapor pressure sensor's calibration walk, and what it recorded."""

    def __init__(self, console):
        self.c = console
        self.walk = {}          # sensor -> the four values, as they come in
        self.records = {}       # sensor -> [(at, slope, offset, passed)]
        self.last = {}          # sensor -> whether its last walk passed

    def factory(self, n):
        """The sensor's own slope and offset: what a calibration measures.

        Stable per sensor and near one and zero. A sensor is shipped
        calibrated, so this is also the record a sensor nobody has
        calibrated reports. UNKNOWNS A55.
        """
        return (readings.fixed(0.97, 1.03, "psslope", int(n)),
                readings.fixed(-0.05, 0.05, "psoffset", int(n)))

    def applied(self, n):
        """The pressure at the sensor: the reference the technician has put
        there with the calibrated test device, or the ullage's own."""
        walk = self.walk.get(int(n)) or {}
        if "span_ref" in walk:
            return walk["span_ref"]
        if "zero_ref" in walk:
            return walk["zero_ref"]
        return self.c.vapor_pressure(int(n))

    def raw(self, n):
        """"the current uncalibrated value read by the pressure sensor"."""
        slope, offset = self.factory(n)
        return (self.applied(n) - offset) / slope

    def enter(self, which, n, value):
        """ENTER ZERO REFERENCE or ENTER SPAN REFERENCE. A new zero starts a
        new calibration; a new span throws away the span read with it."""
        n = int(n)
        if which == "zero":
            self.walk[n] = {"zero_ref": float(value)}
            return
        walk = self.walk.setdefault(n, {})
        walk["span_ref"] = float(value)
        walk.pop("span_read", None)

    def capture(self, which, n):
        """STEP off READ ZERO VALUE or READ SPAN VALUE takes the reading;
        the span is the fourth value, so taking it ends the calibration."""
        n = int(n)
        self.walk.setdefault(n, {})[f"{which}_read"] = self.raw(n)
        if which == "span":
            self._finish(n)

    def _finish(self, n):
        walk = self.walk.pop(n, {})
        at = time.mktime(self.c.now())
        have = all(k in walk for k in ("zero_ref", "zero_read", "span_ref",
                                       "span_read"))
        if have and abs(walk["span_read"] - walk["zero_read"]) > 1e-9:
            slope = ((walk["span_ref"] - walk["zero_ref"])
                     / (walk["span_read"] - walk["zero_read"]))
            offset = walk["zero_ref"] - slope * walk["zero_read"]
            passed = (SLOPE_LIMITS[0] <= slope <= SLOPE_LIMITS[1]
                      and abs(offset) <= OFFSET_LIMIT)
        else:
            slope, offset, passed = 0.0, 0.0, False
        rows = self.records.setdefault(n, [])
        rows.insert(0, (at, slope, offset, passed))
        del rows[KEEP:]
        self.last[n] = passed

    def status(self, n):
        return "PASS" if self.last.get(int(n)) else "FAIL"

    def history(self, n, most=1):
        """[(when, slope, offset, passed)], newest first, ending with the
        factory calibration the sensor was installed with."""
        n = int(n)
        installed = self.c._commissioned or time.mktime(self.c.now())
        slope, offset = self.factory(n)
        rows = list(self.records.get(n, [])) + [(installed, slope, offset,
                                                 True)]
        return rows[:most]

    def reading(self, token, n):
        """The walk's four live screens."""
        n = int(n)
        if token == "ps_raw":
            return f"PRESSURE: {self.raw(n):+07.3f}"
        if token in ("ps_zero_ref", "ps_span_ref"):
            value = (self.walk.get(n) or {}).get(token[3:7] + "_ref", 0.0)
            return f"PRESSURE: {value:+07.3f}"
        if token == "ps_calb":
            return (f"CALB STATUS: {self.status(n)}" + chr(10)
                    + "PRESS <STEP> TO CONTINUE")
        return ""
