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
"""The bench console's own density-to-thermal-coefficient curve.

46 densities were set on the bench TLS-350's tank 2 on 2026-09-19 and each
packed coefficient read back off `i60902`: twelve in the gasoline band, seven
in the transition zone, nine in jet, ten in fuel oil, and eight entered in
pounds per gallon. That is the console's own arithmetic to single precision,
and `density.py`'s constants are fitted to it (FIDELITY S36; the sweeps are
`transcripts/denscal.log` and `denscal2.log` in the bench capture, the fit
`scripts/densfit2.py`).

The tolerance is 1e-9 on a value of about 1e-3 -- a millionth, and about four
times the worst residual any two-constant fit can reach on these points. It
is not float equality, because the console's constants are not knowable
beyond what 46 single-precision numbers carry.
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import density                                    # noqa: E402
from tls350sim.console import Console                            # noqa: E402
from tls350sim.wire import Handler                               # noqa: E402

#: (as entered, the packed coefficient the bench answered)
MEASURED = (
    ("0.6002", "3A76A3A7"),
    ("0.6500", "3A59D43B"),
    ("0.7000", "3A425848"),
    ("0.7400", "3A329264"),
    ("0.7650", "3A29D26C"),
    ("0.7700", "3A2829A8"),
    ("0.7705", "3A27FF95"),
    ("0.7710", "3A274662"),
    ("0.7750", "3A20828B"),
    ("0.7800", "3A18330D"),
    ("0.7850", "3A100C19"),
    ("0.7870", "3A0CD46C"),
    ("0.7875", "3A0C077D"),
    ("0.7880", "3A0B8714"),
    ("0.7900", "3A0AD271"),
    ("0.8000", "3A075F89"),
    ("0.8100", "3A040D20"),
    ("0.8200", "3A00D9A4"),
    ("0.8300", "39FB8730"),
    ("0.8380", "39F6BFA2"),
    ("0.8385", "39F67451"),
    ("0.8390", "39F64119"),
    ("0.8400", "39F5DE7B"),
    ("0.8500", "39F2141F"),
    ("0.9000", "39E0AC2A"),
    ("1.0000", "39C4268F"),
    ("1.2003", "399BD795"),
    ("5.0036", "3A76A671"),
    ("6.0000", "3A3A481E"),
    ("6.5000", "3A18BB6B"),
    ("7.0000", "39F600CB"),
    ("7.5000", "39E0CB2B"),
    ("8.0000", "39CECC87"),
    ("9.0000", "39B1FA08"),
    ("10.0071", "399BD559"),
    ("11.0000", "39C5ECB7"),
    ("12.0000", "39C7B445"),
    ("20.0000", "39D622D3"),
    ("30.0000", "39E8AA6E"),
    ("45.0000", "3A06CCC0"),
    ("50.0000", "3A18D5B6"),
    ("60.0000", "3A32F951"),
    ("70.0000", "3A416673"),
    ("80.0000", "3A5054AD"),
    ("90.0000", "3A5FC3FC"),
    ("99.9999", "3A6FB45A"),
)
CLOSE = 1e-9


class TheConsolesOwnCurve(unittest.TestCase):

    def test_every_measured_coefficient(self):
        worst, where = 0.0, None
        for entered, packed in MEASURED:
            want = struct.unpack(">f", bytes.fromhex(packed))[0]
            got = density.thermal_coefficient(float(entered))
            self.assertIsNotNone(got, entered)
            if abs(got - want) > worst:
                worst, where = abs(got - want), entered
            self.assertLess(abs(got - want), CLOSE,
                            "%s: %r against the bench's %r"
                            % (entered, got, want))
        self.assertLess(worst, CLOSE, where)

    def test_the_bands_are_all_covered(self):
        """A fit is only worth its points, so the count is asserted too."""
        seen = {}
        for entered, _packed in MEASURED:
            sg = density.relative(float(entered))
            rho = sg * density.WATER_KG_M3
            name = ("gasoline" if rho < density.EDGES[0] else
                    "transition" if rho < density.EDGES[1] else
                    "jet" if rho < density.EDGES[2] else "fuel oil")
            seen[name] = seen.get(name, 0) + 1
        for name in ("gasoline", "transition", "jet", "fuel oil"):
            self.assertGreaterEqual(seen.get(name, 0), 3, name)

    def test_pounds_per_gallon_is_the_same_curve(self):
        """An entry in pounds per gallon lands on the relative curve: 7.5
        lb/gal is 0.899627 relative, and the console gave both the same
        coefficient to single precision.

        Only the interior values: the two windows are the same window to
        about a ten-thousandth, and `5.0036 / 8.33679` is 0.6001834, which
        is under the relative window's own 0.6002. The console's edges were
        bisected to 0.0001 and cannot tell 8.337 from 8.33679; this curve
        can, which is how the scale was measured at all.
        """
        for lb in ("6.0000", "7.0000", "7.5000", "8.0000", "9.0000"):
            sg = float(lb) / density.LB_GAL_PER_SG
            self.assertAlmostEqual(
                density.thermal_coefficient(float(lb)),
                density.thermal_coefficient(sg), delta=CLOSE)

    def test_a_density_set_over_the_wire_writes_it(self):
        """End to end: the Set, then the packed 609 the bench answered."""
        c = Console(None)
        c.board = "E6"
        c.modules["probe"] = 1
        c.values["S60102"] = "021"
        c.values["S56000"] = "0001"            # mass/density on
        h = Handler(c, verbose=False)
        for entered, packed in MEASURED[:6]:
            h.handle(("{}S61E02{}{}".format(chr(1), entered,
                                            chr(13))).encode())
            out = h.handle(("{}i60902{}".format(chr(1), chr(13))).encode())
            body = out.decode("latin-1").split("&&")[0]
            got = struct.unpack(">f", bytes.fromhex(body[-8:]))[0]
            want = struct.unpack(">f", bytes.fromhex(packed))[0]
            self.assertLess(abs(got - want), CLOSE, entered)


if __name__ == "__main__":
    unittest.main()
