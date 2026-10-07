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
"""An entered density: which of its three forms it is, and what it is FOR.

576013-623 Rev AN says a tank's density may be entered as mass per unit
volume, specific gravity or an API number, "units are not entered", and that
the console converts it -- and says neither how it tells them apart nor what
it converts to. UNKNOWNS A23. Both were read off the bench TLS-350 (software
326.01) on 2026-09-18:

* **The form is decided by magnitude, in three bands.** `S61E` accepts
  0.6002 to 1.2003, 5.0036 to 10.0071 and 11.0000 to 99.9999, and refuses
  everything between them and outside them, each edge bisected to 0.0001.
  The first two are the SAME window -- the second is the first times 8.337,
  a gallon of water in pounds -- so they are specific gravity and pounds per
  gallon; the third is API. The number is stored exactly as entered.

* **What it is for is the tank's thermal coefficient.** Entering a density
  rewrites `609`, and 27 entries across all three bands put it on ASTM
  D1250-80 Table 6B's own curve -- the product-group constants for
  gasolines, the transition zone, jet fuels and fuel oils, per degree F --
  with the jump between groups exactly where the table puts it. The console
  converts to specific gravity first (pounds per gallon over 8.33679, API by
  141.5 / (API + 131.5)) and then to kg/m3 at 999.65 kg/m3 for water.
  8.337 is the ratio the band edges themselves give; 999.65 is FITTED, not
  documented. With the two, all 26 points outside the transition zone agree
  with the console to within 0.016%, and the one inside it (API 50) to 0.04%.
  The measurements are `transcripts/dentc.log` in the bench capture.

  **Measured properly on 2026-09-19, and the constants below are the
  CONSOLE'S now rather than the table's.** 46 densities were set on the bench
  console's tank 2 and each packed coefficient read back: twelve in the
  gasoline band, seven in the transition zone, nine in jet, ten in fuel oil,
  and eight in pounds per gallon (`transcripts/denscal.log`, `denscal2.log`;
  the fit is `scripts/densfit2.py`). What a measurement can see is two
  numbers per band, because `K0/rho**2 + K1/rho` is
  `(K0/W**2)/sg**2 + (K1/W)/sg` and the water density cancels; fitted at
  W = 999.65 they reproduce every point to 2.3e-10, which is single
  precision, where the table's own constants were out by 1.1e-7 (0.012%).
  The band edges are where they were: the curve changes shape between 0.7705
  and 0.7710, between 0.7875 and 0.7880 and between 0.8385 and 0.8390
  relative, which is 770.352, 787.5 and 838.5 kg/m3 at this water density.

  And the pounds-per-gallon scale is 8.33679, not 8.337: inverting each
  band's fit for the eight lb/gal points gives that ratio on every one of
  them, to six figures. FIDELITY S36.
"""

#: (low, high, form) -- the bench console's own edges, inclusive
BANDS = ((0.6002, 1.2003, "relative"), (5.0036, 10.0071, "actual"),
         (11.0, 99.9999, "api"))

#: fitted to the bench console; see the module docstring
WATER_KG_M3 = 999.65
LB_GAL_PER_SG = 8.33679

# The shape is ASTM D1250-80 Table 6B's, per degree F, density in kg/m3 --
# alpha = K0 / rho^2 + K1 / rho, and A + B / rho^2 in the transition zone --
# and the constants are the bench console's own, fitted to 46 of its
# coefficients at the water density above. The table's read GASOLINE
# (192.4571, 0.2438), TRANSITION (-0.00186840, 1489.0670), JET (330.3010,
# 0.0) and FUEL_OIL (103.8720, 0.2701): the same numbers a digit or two out.
GASOLINE = (192.438780, 0.24376630)
TRANSITION = (-0.00186840294, 1488.927371)
JET = (330.269616, 0.0)
FUEL_OIL = (103.862145, 0.27009817)
#: the density edges between the four groups, kg/m3 at 60 F
EDGES = (770.352, 787.5, 838.5)


def form(entered):
    """"relative", "actual" or "api" for a density the console would take,
    or None for one it refuses."""
    for low, high, name in BANDS:
        # a density is stored as a single-precision float, which puts
        # 99.9999 back at 99.99990082: the edges are held to that precision,
        # and the next value the entry can carry is still a whole 0.0001 out
        tol = 1e-6 * high
        if low - tol <= entered <= high + tol:
            return name
    return None


def relative(entered):
    """Specific gravity, whichever form was entered; None if refused."""
    kind = form(entered)
    if kind == "relative":
        return entered
    if kind == "actual":
        return entered / LB_GAL_PER_SG
    if kind == "api":
        return 141.5 / (entered + 131.5)
    return None


def pounds_per_gallon(entered):
    """The actual density in lb/gal, which is what mass is reckoned in."""
    sg = relative(entered)
    return None if sg is None else sg * LB_GAL_PER_SG


def thermal_coefficient(entered):
    """Table 6B's coefficient per degree F for an entered density, or None."""
    sg = relative(entered)
    if sg is None:
        return None
    rho = sg * WATER_KG_M3
    if rho < EDGES[0]:
        k0, k1 = GASOLINE
    elif rho < EDGES[1]:
        a, b = TRANSITION
        return a + b / rho ** 2
    elif rho < EDGES[2]:
        k0, k1 = JET
    else:
        k0, k1 = FUEL_OIL
    return k0 / rho ** 2 + k1 / rho
