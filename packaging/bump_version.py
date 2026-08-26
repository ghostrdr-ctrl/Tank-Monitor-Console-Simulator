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
"""Bump the version, in the one place it lives and in the changelog.

    python packaging/bump_version.py patch     0.1.0 -> 0.1.1
    python packaging/bump_version.py minor     0.1.0 -> 0.2.0
    python packaging/bump_version.py major     0.1.0 -> 1.0.0

What each level means is in RELEASING.md. Briefly: major breaks how you use
it or what a tool sees, minor adds a capability without breaking anything,
patch fixes or polishes without changing how you use it.

Updates `tls350sim/__init__.py` and rolls the changelog's Unreleased section
into a dated section for the new version. Run the tests and commit, then tag
`v<version>` to release.
"""
import argparse
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "tls350sim", "__init__.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")


def current():
    text = open(INIT, encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
    if not m:
        sys.exit("[bump] could not find __version__ in tls350sim/__init__.py")
    return tuple(int(g) for g in m.groups())


def bump(part, ver):
    major, minor, patch = ver
    if part == "major":
        return (major + 1, 0, 0)
    if part == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def write_init(new):
    text = open(INIT, encoding="utf-8").read()
    text = re.sub(r'__version__\s*=\s*"\d+\.\d+\.\d+"',
                  f'__version__ = "{new}"', text)
    open(INIT, "w", encoding="utf-8", newline="\n").write(text)


def roll_changelog(new, today):
    """Turn the Unreleased heading into a dated version heading, and start a
    fresh empty Unreleased above it."""
    if not os.path.exists(CHANGELOG):
        return
    text = open(CHANGELOG, encoding="utf-8").read()
    marker = "## [Unreleased]"
    if marker not in text:
        return
    fresh = (f"## [Unreleased]\n\n"
             f"## [{new}] - {today}")
    text = text.replace(marker, fresh, 1)
    open(CHANGELOG, "w", encoding="utf-8", newline="\n").write(text)


def main():
    ap = argparse.ArgumentParser(description="Bump the project version.")
    ap.add_argument("part", choices=["major", "minor", "patch"])
    ap.add_argument("--date", help="release date YYYY-MM-DD for the changelog "
                    "(defaults to today)")
    a = ap.parse_args()

    old = current()
    new = bump(a.part, old)
    old_s = ".".join(str(n) for n in old)
    new_s = ".".join(str(n) for n in new)
    today = a.date or datetime.date.today().isoformat()

    write_init(new_s)
    roll_changelog(new_s, today)
    print(f"[bump] {old_s} -> {new_s} ({a.part})")
    print("[bump] updated tls350sim/__init__.py and CHANGELOG.md")
    print(f"[bump] next: run the tests, commit, then tag v{new_s}")


if __name__ == "__main__":
    main()
