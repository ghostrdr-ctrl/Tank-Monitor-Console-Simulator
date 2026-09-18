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
"""A save that fails leaves the last good file where it was.

Every save used to open its destination with "w", which empties the file
before the first byte of the new one is written. A value json could not
encode, a full disk or a killed process then left the console's programming,
the card's setup, the preferences or the E2 archive truncated, and the next
start read nothing back. They now write beside the file and replace it only
once the new content is complete, through `tls350sim.atomicfile`.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tls350sim import atomicfile, paths, update, xport     # noqa: E402
from tls350sim.console import Console                      # noqa: E402


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def attempt(save):
    """Run a save that is expected to fail, however it fails.

    Some of these saves swallow the error and some let it through, as they
    did before; what is under test is the file left behind, not that.
    """
    try:
        save()
    except Exception:                                   # noqa: BLE001
        pass


class Scratch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def only(self, *names):
        """Nothing else in the directory: no temporary file left behind."""
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(names))


class Replacing(Scratch):
    OLD = 'first line\n{"kept": true}\n'

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "state.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.OLD)
        self.before = read(self.path)

    def untouched(self):
        self.assertEqual(read(self.path), self.before)
        self.only("state.json")

    def test_a_completed_write_replaces_the_file(self):
        with atomicfile.replacing(self.path) as fh:
            fh.write("second\n")
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "second\n")
        self.only("state.json")

    def test_a_file_that_did_not_exist_is_created(self):
        fresh = os.path.join(self.dir, "fresh.json")
        with atomicfile.replacing(fresh) as fh:
            fh.write("{}")
        self.assertEqual(read(fresh), b"{}")
        self.only("state.json", "fresh.json")

    def test_the_bytes_are_the_ones_a_plain_open_writes(self):
        # the same encoding and the same newline translation, so no file
        # changes shape by being saved this way
        text = ("line one\nline two, É\n"
                + json.dumps({"a": [1, 2], "b": "é"}, indent=1) + "\n")
        plain = os.path.join(self.dir, "plain.json")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write(text)
        with atomicfile.replacing(self.path) as fh:
            fh.write(text)
        self.assertEqual(read(self.path), read(plain))

    def test_json_that_cannot_be_encoded_leaves_the_old_file(self):
        # json.dump writes as it goes, so the destination would have held
        # the first half by the time the object it cannot encode came up
        with self.assertRaises(TypeError):
            with atomicfile.replacing(self.path) as fh:
                json.dump({"a": "x" * 10000, "b": object()}, fh, indent=1)
        self.untouched()

    def test_an_error_partway_through_leaves_the_old_file(self):
        with self.assertRaises(RuntimeError):
            with atomicfile.replacing(self.path) as fh:
                fh.write("half of the new")
                raise RuntimeError("interrupted")
        self.untouched()

    def test_a_write_that_does_not_reach_the_disk_leaves_the_old_file(self):
        with mock.patch("os.fsync", side_effect=OSError(5, "I/O error")):
            with self.assertRaises(OSError):
                with atomicfile.replacing(self.path) as fh:
                    fh.write("new")
        self.untouched()

    def test_a_replace_that_fails_leaves_the_old_file(self):
        with mock.patch("os.replace",
                        side_effect=PermissionError(13, "in use")):
            with self.assertRaises(PermissionError):
                with atomicfile.replacing(self.path) as fh:
                    fh.write("new")
        self.untouched()

    def test_a_missing_directory_fails_the_way_open_does(self):
        # an OSError, so the callers' own `except OSError` still applies
        with self.assertRaises(FileNotFoundError):
            with atomicfile.replacing(
                    os.path.join(self.dir, "gone", "state.json")) as fh:
                fh.write("new")
        self.untouched()


class TheConsoleState(Scratch):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "state.json")
        c = Console(self.path)
        c.values["S60201"] = "01REGULAR UNLEADED   "
        c.save()
        self.console = c

    def test_a_save_replaces_the_programming(self):
        self.console.values["S60201"] = "01PREMIUM UNLEADED   "
        self.console.save()
        self.assertEqual(Console(self.path).values["S60201"],
                         "01PREMIUM UNLEADED   ")
        self.only("state.json")

    def test_a_save_that_cannot_encode_keeps_the_last_good_programming(self):
        before = read(self.path)
        self.console.values["S60202"] = object()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.console.save()
        self.assertIn("could not save state", out.getvalue())
        self.assertEqual(read(self.path), before)
        self.assertEqual(Console(self.path).values["S60201"],
                         "01REGULAR UNLEADED   ")
        self.only("state.json")


class TheArchive(Scratch):
    def setUp(self):
        super().setUp()
        self.console = Console(os.path.join(self.dir, "state.json"))
        self.console.values["S60201"] = "01REGULAR UNLEADED   "
        self.assertEqual(self.console.archive_save(), 1)
        self.path = self.console.archive_path()

    def test_a_save_replaces_the_archive(self):
        self.console.values["S60202"] = "02PREMIUM UNLEADED   "
        self.assertEqual(self.console.archive_save(), 2)
        restored = Console(os.path.join(self.dir, "state.json"))
        restored.archive_restore()              # which saves the state
        self.assertIn("S60202", restored.values)
        self.only("state.json", "state.vrset")

    def test_a_save_that_fails_partway_keeps_the_last_archive(self):
        before = read(self.path)
        self.console.values["S60202"] = None    # `.encode` fails mid-file
        attempt(self.console.archive_save)
        self.assertEqual(read(self.path), before)
        self.only("state.vrset")

    def test_a_save_that_does_not_reach_the_disk_keeps_the_last_archive(self):
        before = read(self.path)
        self.console.values["S60202"] = "02PREMIUM UNLEADED   "
        with mock.patch("os.fsync", side_effect=OSError(5, "I/O error")):
            self.assertEqual(self.console.archive_save(), -1)
        self.assertEqual(read(self.path), before)
        self.only("state.vrset")


class TheCardConfig(Scratch):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "xport.json")
        self.card = xport.XPortConfig(self.path)
        self.card.program(port=10002)
        self.card.save()

    def test_a_save_replaces_the_setup(self):
        self.card.program(port=10003)
        self.card.save()
        self.assertEqual(xport.XPortConfig(self.path).port, 10003)
        self.only("xport.json")

    # `program` saves as well, which is why the setting changes before the
    # snapshot in one of these and inside the failing disk in the other
    def test_a_save_that_cannot_encode_keeps_the_last_setup(self):
        self.card.program(port=10003)
        before = read(self.path)
        self.card.snmp_community = object()
        attempt(self.card.save)
        self.assertEqual(read(self.path), before)
        self.assertEqual(xport.XPortConfig(self.path).port, 10003)
        self.only("xport.json")

    def test_a_save_that_does_not_reach_the_disk_keeps_the_last_setup(self):
        before = read(self.path)
        with mock.patch("os.fsync", side_effect=OSError(5, "I/O error")):
            self.card.program(port=10003)       # swallowed, as it was
        self.assertEqual(read(self.path), before)
        self.assertEqual(xport.XPortConfig(self.path).port, 10002)
        self.only("xport.json")


class ThePreferences(Scratch):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.dir, "settings.json")
        patcher = mock.patch.object(paths, "settings_file",
                                    return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        update._save_settings({"check_on_startup": True})

    def test_a_save_replaces_the_preferences(self):
        update._save_settings({"check_on_startup": False})
        self.assertFalse(update.check_on_startup())
        self.only("settings.json")

    def test_a_save_that_cannot_encode_keeps_the_last_preferences(self):
        attempt(lambda: update._save_settings(
            {"check_on_startup": False, "window": object()}))
        self.assertTrue(update.check_on_startup())
        self.only("settings.json")

    def test_a_save_that_does_not_reach_the_disk_keeps_the_last_preferences(
            self):
        with mock.patch("os.fsync", side_effect=OSError(5, "I/O error")):
            update._save_settings({"check_on_startup": False})
        self.assertTrue(update.check_on_startup())
        self.only("settings.json")


if __name__ == "__main__":
    unittest.main()
