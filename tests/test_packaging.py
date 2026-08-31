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

The portable zip added the same kind of seam in two more places: the folder
it unpacks to has to be the folder PyInstaller actually built, and the
release now carries an asset the in-app updater must not mistake for an
installer.
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ISS = os.path.join(ROOT, "packaging", "installer.iss")
BUILD = os.path.join(ROOT, "packaging", "build.py")
SPEC = os.path.join(ROOT, "packaging",
                    "tank-monitor-console-simulator.spec")

sys.path.insert(0, os.path.join(ROOT, "packaging"))
sys.path.insert(0, ROOT)
import build                                            # noqa: E402
from tls350sim import update                            # noqa: E402


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


class ThePortableZip(unittest.TestCase):
    """The zip is built by hand from a folder PyInstaller names elsewhere."""

    def test_it_unpacks_to_the_folder_pyinstaller_builds(self):
        """The spec's COLLECT name is the folder on disk; the zip's top-level
        folder is derived from it. If someone renames one, the archive would
        still build and would still look right in a listing, but the path in
        HOW-TO-RUN.txt would point at a folder that is not there."""
        coll = re.search(r'name="([^"]+)",\s*\)\s*$', read(SPEC).rstrip())
        self.assertIsNotNone(
            coll, "the spec no longer ends with a COLLECT name= ; this test "
                  "needs updating alongside it")
        self.assertEqual(
            coll.group(1), build.PORTABLE_ROOT,
            "the spec builds %r but the zip unpacks to %r"
            % (coll.group(1), build.PORTABLE_ROOT))

    def test_the_name_has_no_spaces(self):
        """Same reason as the installer: GitHub rewrites a space to a dot in
        a release asset, and then the served name and the name recorded in
        SHA256SUMS.txt disagree."""
        self.assertNotIn(" ", build.PORTABLE_ZIP)

    def test_the_checksums_cover_it(self):
        """A portable download gets no installer to verify it, so the line in
        SHA256SUMS.txt is the only check available to the person holding it.
        Narrowing the filter back to .exe would remove it silently."""
        with tempfile.TemporaryDirectory() as d:
            for name in ("App-1.0-Setup.exe", "App-1.0-Portable.zip"):
                with open(os.path.join(d, name), "wb") as f:
                    f.write(b"x")
            # it prints the sums it wrote, which is right for a build log
            # and noise in a test run
            with mock.patch.object(build, "INSTALLER_DIR", d):
                with contextlib.redirect_stdout(io.StringIO()):
                    build.write_checksums()
            with io.open(os.path.join(d, "SHA256SUMS.txt"),
                         encoding="utf-8") as f:
                listed = [ln.split()[-1] for ln in f if ln.strip()]
        self.assertIn("App-1.0-Portable.zip", listed)
        self.assertIn("App-1.0-Setup.exe", listed)


class _Answer:
    """The little of urlopen's result that update.py actually uses."""

    def __init__(self, data):
        self._data = data

    def read(self, *_a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class TheUpdaterSeeingTheZip(unittest.TestCase):
    """A release carries two downloadable files now. The updater wants the
    installer, and only the installer: it cannot run a .zip, and offering one
    would fail after the download rather than before it."""

    TAG = "v99.0.0"
    EXE = "TankMonitorConsoleSimulator-99.0.0-Setup.exe"
    ZIP = "TankMonitorConsoleSimulator-99.0.0-Portable.zip"
    EXE_SUM = "a" * 64
    ZIP_SUM = "b" * 64

    def _answer(self, url):
        if url.endswith("/sums"):
            # the zip line comes first, so a reader that takes the first line
            # rather than the matching one gets the wrong sum
            return _Answer(("%s  %s\n%s  %s\n"
                            % (self.ZIP_SUM, self.ZIP,
                               self.EXE_SUM, self.EXE)).encode())
        return _Answer(json.dumps({
            "tag_name": self.TAG,
            "body": "notes",
            # the zip is listed first on purpose: picking the first asset,
            # or the last, must not be what makes this pass
            "assets": [
                {"name": self.ZIP, "size": 1,
                 "browser_download_url": "https://example.invalid/zip"},
                {"name": self.EXE, "size": 2,
                 "browser_download_url": "https://example.invalid/exe"},
                {"name": "SHA256SUMS.txt", "size": 3,
                 "browser_download_url": "https://example.invalid/sums"},
            ],
        }).encode())

    def test_it_offers_the_installer_not_the_zip(self):
        with mock.patch.object(update, "_open", self._answer):
            release = update.check(repo="example/repo")
        self.assertIsNotNone(release, "a newer release should have been seen")
        self.assertEqual(release.installer_name, self.EXE)

    def test_it_reads_the_installers_line_out_of_the_checksum_file(self):
        """SHA256SUMS.txt has more than one line in it now."""
        with mock.patch.object(update, "_open", self._answer):
            release = update.check(repo="example/repo")
            self.assertEqual(update.published_checksum(release),
                             self.EXE_SUM)


if __name__ == "__main__":
    unittest.main()
