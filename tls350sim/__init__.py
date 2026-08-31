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
"""A training simulator for TLS-350 compatible tank monitor consoles.

Simulates the console face, the operating and setup screens, and the serial
protocol, so that a technician can learn to walk a real console without
touching live equipment.

This program is not affiliated with, authorized by, sponsored by, or endorsed
by Veeder-Root, Gilbarco Veeder-Root, or Vontier Corporation. "Veeder-Root",
"TLS-350" and "AccuChart" are trademarks of their respective owners, used here
only to state, factually, what hardware this simulator is compatible with.
"""

APP_NAME = "Tank Monitor Console Simulator"
PUBLISHER = "Verbose Software"
DISCLAIMER = (
    "Not affiliated with, authorized by, or endorsed by Veeder-Root, "
    "Gilbarco Veeder-Root, or Vontier Corporation. Trademarks are the "
    "property of their respective owners."
)

__version__ = "0.1.2"
