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
"""The two halves of the build agreeing on what they are making.

`installer.iss` decides what Inno Setup writes; `build.py` then looks for
that file by name and stops if it is not there. Nothing connects the two but
a string, so changing one and not the other produces a build that fails only
on a release runner, minutes in, after the tests have already passed. That
happened once. This is here so it happens once.
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ISS = os.path.join(ROOT, "packaging", "installer.iss")
BUILD = os.path.join(ROOT, "packaging", "build.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TheInstallerNameBothHalvesUse(unittest.TestCase):
    def test_the_iss_and_build_py_agree(self):
        """The name Inno Setup writes is the name build.py goes looking for."""
        iss = re.search(r"^OutputBaseFilename=(.+)$", read(ISS), re.M)
        self.assertIsNotNone(iss, "installer.iss has no OutputBaseFilename")
        # the .iss writes it with its own version macro, build.py with an
        # f-string; compare the parts either side of the version
        iss_name = iss.group(1).strip().replace("{#MyAppVersion}", "{V}")

        py = re.search(r'f"([A-Za-z]\S*?)\{__version__\}(\S*?)\.exe"',
                       read(BUILD))
        self.assertIsNotNone(
            py, "build.py no longer names the installer with an f-string; "
                "this test needs updating alongside it")
        py_name = py.group(1) + "{V}" + py.group(2)

        self.assertEqual(
            iss_name, py_name,
            "installer.iss writes %r but build.py looks for %r, so the build "
            "will fail on the release runner" % (iss_name, py_name))

    def test_the_name_has_no_spaces(self):
        """A spaced name is served by GitHub with dots, and then the asset on
        the release page and the name inside SHA256SUMS.txt disagree."""
        iss = re.search(r"^OutputBaseFilename=(.+)$", read(ISS), re.M)
        self.assertNotIn(
            " ", iss.group(1).strip(),
            "the installer file name must not contain spaces: GitHub rewrites "
            "them to dots in a release asset, leaving the served name at odds "
            "with the one the checksum file records")


if __name__ == "__main__":
    unittest.main()
