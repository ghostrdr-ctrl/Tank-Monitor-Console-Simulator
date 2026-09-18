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


class ClosingTheAppDuringACheck(unittest.TestCase):
    """The updater waits on the network on a worker thread and comes back
    through `after(0, ...)`, which is the only way to touch a widget from
    anywhere but the main thread. If the window has gone in the meantime --
    somebody closed the app, or the check was a startup one and the app never
    got as far as a mainloop -- `after` raises in the worker, which on a
    packaged build is a silent crash in a daemon thread."""

    def test_the_worker_does_not_raise_when_the_window_has_gone(self):
        import threading
        from tls350sim import update, updateui

        class Gone:
            def after(self, _ms, _fn):
                raise RuntimeError("main thread is not in main loop")

        blew_up = []
        real = threading.Thread

        def watched(target=None, **kw):
            def wrapped():
                try:
                    target()
                except BaseException as exc:        # pragma: no cover
                    blew_up.append(exc)
            return real(target=wrapped, **kw)

        with mock.patch.object(update, "check", lambda: None),                 mock.patch.object(threading, "Thread", watched):
            updateui.check_for_updates(Gone(), silent=True)
        # the thread is started inside check_for_updates; give it its run
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon:
                t.join(timeout=2)
        self.assertEqual(blew_up, [])


if __name__ == "__main__":
    unittest.main()


class TheInternalDocsStayOutOfTheSnapshot(unittest.TestCase):
    """The public repository carries one document, and the rule that keeps it
    that way is "a new .md is private unless it is named".

    That rule is the right way round -- a document added tomorrow is private
    by default rather than shipping because nobody updated a list -- but
    nothing asserted it, so the guarantee rested on reading the code. It is
    the kind of thing that is discovered by a leak.

    `CLOSED.md` is the case that prompted this: FIDELITY.md was split in two
    on 2026-09-08 and the closed half is as internal as the open one. It
    needed no change to the snapshot builder, which is exactly the claim
    worth pinning.
    """

    # The snapshot builder is itself held back from the snapshot, so a
    # public clone has nothing here to check and these three errored on it
    # -- which the release build gates on. Skipped by the file's ABSENCE
    # rather than by catching the failure, so in this tree, where it is
    # always present, they always run. See `tests/test_citations.py` for
    # the same rule about a skip that swallows a crash.
    BUILDER = os.path.join(ROOT, "packaging", "make_public_snapshot.py")

    def snapshot(self):
        import importlib.util
        if not os.path.exists(self.BUILDER):
            raise unittest.SkipTest(
                "packaging/make_public_snapshot.py is not in this tree, so "
                "this is the public snapshot and there is no builder to "
                "check")
        spec = importlib.util.spec_from_file_location("_snapshot",
                                                      self.BUILDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_every_internal_document_is_held_back(self):
        snap = self.snapshot()
        for name in ("NOTES.md", "UNKNOWNS.md", "AUDIT.md", "FIDELITY.md",
                     "CLOSED.md", "CHANGELOG.md", "RELEASING.md", "BENCH.md"):
            self.assertFalse(snap.ships(name), f"{name} would be published")

    def test_the_register_s_own_test_is_held_back_with_it(self):
        """`test_fidelity.py` is written against FIDELITY.md, which never
        ships, so a public clone would carry a test for a document it does
        not have."""
        snap = self.snapshot()
        self.assertFalse(snap.ships("tests/test_fidelity.py"))
        self.assertTrue(snap.ships("tests/test_console.py"))

    def test_the_readme_is_the_one_that_ships(self):
        """The check on the check: a rule that held everything back would
        pass the test above and ship nothing."""
        snap = self.snapshot()
        self.assertTrue(snap.ships("README.md"))
        self.assertEqual(snap.SHIPPING_DOCS, {"README.md"})

    def test_no_tracked_markdown_but_the_readme_would_ship(self):
        """The general form, over the files git actually tracks, so a
        document added later is covered without naming it here."""
        snap = self.snapshot()
        shipped = [rel for rel in snap.tracked_files()
                   if rel.lower().endswith(".md") and snap.ships(rel)]
        self.assertEqual(shipped, ["README.md"])


class EveryRegisterIdTheSourceCitesResolves(unittest.TestCase):
    """A citation in a comment outlives the entry it names.

    The code cites `FIDELITY S9`, `CLOSED U13`, `BENCH.md P3` and about two
    hundred and sixty more, and those citations are how a reader gets from
    a line of code to the reason it is that way. Two of them -- `D16` and
    `D18` -- named entries that had never been written: both were added by
    commits that changed no register file at all, and neither had a heading
    or a mention in FIDELITY.md, CLOSED.md, BENCH.md or UNKNOWNS.md.

    **An id that resolves to nothing is worse than no id**, because it
    reads as a promise that the explanation is somewhere. Nothing could
    catch it by reading the registers, because the registers are where the
    gap is; only the citations can, and only by grepping the source.

    The check is deliberately loose about WHERE an id is recorded. An entry
    can be a heading, a row of the `Fixed` list, a bullet in a closed
    entry's tail, or a row of BENCH.md's table -- W9 is a bullet, T11 is a
    table row -- and all of those are a reader finding what they came for.
    What it refuses is an id that appears in no register at all.
    """

    REGISTERS = ("FIDELITY.md", "CLOSED.md", "BENCH.md", "UNKNOWNS.md")

    def setUp(self):
        # The registers are internal and the public snapshot ships none of
        # them, so there is nothing for a citation to resolve AGAINST on a
        # public clone. By absence, so this always runs in the tree that
        # has them.
        if not any(os.path.exists(os.path.join(ROOT, r))
                   for r in self.REGISTERS):
            raise unittest.SkipTest(
                "no register in this tree, so this is the public snapshot")

    # `FIDELITY S9`, `CLOSED U13, U14`, `BENCH.md P3`, `UNKNOWNS A17`
    CITE = re.compile(r"(?:FIDELITY|CLOSED|BENCH\.md|UNKNOWNS)\s+"
                      r"((?:[A-Z]{1,2}\d+[a-z]?)"
                      r"(?:\s*(?:,|and|/|to)\s*[A-Z]{1,2}\d+[a-z]?)*)")
    ONE = re.compile(r"[A-Z]{1,2}\d+[a-z]?")

    def registers(self):
        text = ""
        for name in self.REGISTERS:
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text += fh.read()
        return text

    def cited(self):
        """Every id the source cites, with the first place it does."""
        out = {}
        for folder in ("tls350sim", "tests", "tools"):
            here = os.path.join(ROOT, folder)
            for name in sorted(os.listdir(here)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(here, name)
                with open(path, encoding="utf-8") as fh:
                    for n, line in enumerate(fh, 1):
                        for hit in self.CITE.finditer(line):
                            for one in self.ONE.findall(hit.group(1)):
                                out.setdefault(one, f"{folder}/{name}:{n}")
        return out

    def test_no_citation_names_an_entry_that_does_not_exist(self):
        text = self.registers()
        cited = self.cited()
        self.assertGreater(len(cited), 200, "the citation scan found nothing")
        missing = {k: v for k, v in cited.items()
                   if not re.search(r"(?<![A-Za-z0-9])" + k
                                    + r"(?![A-Za-z0-9])", text)}
        self.assertEqual(missing, {},
                         "cited in the source, in no register: "
                         + ", ".join(f"{k} ({v})"
                                     for k, v in sorted(missing.items())))

    def test_the_scan_can_see_an_id_that_is_only_a_bullet(self):
        """The check on the check. W9 has no heading in either register --
        it is a bullet in a closed entry's `Fixed` list -- and T11 is a row
        of BENCH.md's table. If the scan only looked at headings it would
        fail both, and a stricter check would be a worse one."""
        text = self.registers()
        for one in ("W9", "T11"):
            self.assertIn(one, self.cited(), f"{one} is not cited any more")
            self.assertRegex(text, r"(?<![A-Za-z0-9])" + one
                             + r"(?![A-Za-z0-9])")


class EveryToolStillParses(unittest.TestCase):
    """`tools/` is not imported by anything the app runs, so nothing was
    looking at it.

    Two of the generators -- `build_alarm_labels.py` and
    `build_field_widths.py` -- raised SyntaxError on import and had for as
    long as the damage was there: a `\\n` that lost its backslash, so the
    string terminated at the end of the line and the file stopped being
    Python. They are the scripts that rebuild `alarmlabels.json` and
    `fieldwidths.json` from the manuals, and the comment they both carry
    says what that costs: "a generated file nobody can re-generate cleanly
    is a generated file people stop re-generating."

    Compiling is a low bar and it is the bar these fell under. It does not
    run them -- `build_wire_titles.py` reads 570 pages and belongs in a
    pass of its own -- it only asserts every one of them is still a Python
    file.
    """

    def tools(self):
        here = os.path.join(ROOT, "tools")
        return sorted(name for name in os.listdir(here)
                      if name.endswith(".py"))

    def test_every_tool_compiles(self):
        names = self.tools()
        self.assertGreater(len(names), 5, "the tools scan found nothing")
        broken = []
        for name in names:
            path = os.path.join(ROOT, "tools", name)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            try:
                compile(source, path, "exec")
            except SyntaxError as exc:
                broken.append(f"{name}:{exc.lineno}: {exc.msg}")
        self.assertEqual(broken, [])
