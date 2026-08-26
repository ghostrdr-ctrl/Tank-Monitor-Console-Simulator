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
"""Build the Windows installer, end to end, from a clean checkout.

    python packaging/build.py

Does, in order:
  1. Write the Windows version resource from the package's __version__.
  2. Run PyInstaller on the spec -> dist/TankMonitorConsoleSimulator/.
  3. Run Inno Setup (ISCC) on the .iss -> dist/installer/...-Setup.exe.
  4. Write dist/installer/SHA256SUMS.txt over the installer.

The SHA256SUMS.txt is not decoration: the app's own updater refuses to run a
downloaded installer that is not listed there (tls350sim/update.py), so a
release without it cannot self-update. The CI workflow publishes both the
installer and this file to the GitHub release together.

Signing is out of scope here: an Authenticode certificate lives on a hardware
token that is not on a CI runner. If a signed build is wanted, sign
dist/TankMonitorConsoleSimulator/*.exe BEFORE step 3 and the installer BEFORE
step 4, then the checksums cover the signed bytes. See --signtool.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tls350sim import __version__       # noqa: E402

SPEC = os.path.join("packaging", "tank-monitor-console-simulator.spec")
ISS = os.path.join("packaging", "installer.iss")
DIST = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST, "TankMonitorConsoleSimulator")
INSTALLER_DIR = os.path.join(DIST, "installer")


def run(cmd, **kw):
    print("  $", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def find_iscc():
    """Locate the Inno Setup compiler, or explain how to get it."""
    for name in ("ISCC", "iscc"):
        found = shutil.which(name)
        if found:
            return found
    bases = [
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        # winget installs Inno Setup per-user here
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    for base in bases:
        cand = os.path.join(base, "Inno Setup 6", "ISCC.exe")
        if os.path.exists(cand):
            return cand
    return None


def make_icon_if_missing():
    ico = os.path.join(ROOT, "assets", "icon.ico")
    if not os.path.exists(ico):
        print("[build] assets/icon.ico missing -- generating it")
        run([sys.executable, os.path.join("tools", "make_icon.py")])


def build_exe():
    print("[build] PyInstaller")
    run([sys.executable, os.path.join("packaging", "make_version_info.py")])
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC])
    exe = os.path.join(APP_DIR, "TankMonitorConsoleSimulator.exe")
    if not os.path.exists(exe):
        sys.exit("[build] PyInstaller did not produce the .exe")


def sign(signtool, path):
    print(f"[build] signing {os.path.basename(path)}")
    run([signtool, "sign", "/fd", "SHA256", "/tr",
         "http://timestamp.digicert.com", "/td", "SHA256", "/a", path])


def build_installer(iscc, signtool=None):
    if signtool:
        # sign every .exe in the app folder before it is packaged
        for root, _dirs, files in os.walk(APP_DIR):
            for name in files:
                if name.lower().endswith(".exe"):
                    sign(signtool, os.path.join(root, name))
    print("[build] Inno Setup")
    run([iscc, f"/DMyAppVersion={__version__}", ISS])
    setup = os.path.join(
        INSTALLER_DIR,
        f"Tank Monitor Console Simulator-{__version__}-Setup.exe")
    if not os.path.exists(setup):
        sys.exit(f"[build] Inno Setup did not produce {setup}")
    if signtool:
        sign(signtool, setup)
    return setup


def write_checksums():
    """SHA256SUMS.txt over every .exe in the installer directory."""
    lines = []
    for name in sorted(os.listdir(INSTALLER_DIR)):
        if not name.lower().endswith(".exe"):
            continue
        path = os.path.join(INSTALLER_DIR, name)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        lines.append(f"{h.hexdigest()}  {name}")
    out = os.path.join(INSTALLER_DIR, "SHA256SUMS.txt")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[build] wrote {out}")
    for line in lines:
        print("       ", line)


def main():
    ap = argparse.ArgumentParser(description="Build the Windows installer.")
    ap.add_argument("--signtool",
                    help="path to signtool.exe; if given, the app .exe files "
                         "and the installer are Authenticode-signed before "
                         "the checksums are taken")
    ap.add_argument("--skip-installer", action="store_true",
                    help="build only the PyInstaller folder, not the .exe "
                         "installer (for when Inno Setup is not present)")
    a = ap.parse_args()

    print(f"[build] Tank Monitor Console Simulator {__version__}")
    make_icon_if_missing()
    build_exe()

    if a.skip_installer:
        print("[build] done (installer skipped). App folder:", APP_DIR)
        return

    iscc = find_iscc()
    if not iscc:
        sys.exit("[build] Inno Setup 6 not found. Install it from "
                 "https://jrsoftware.org/isdl.php, or re-run with "
                 "--skip-installer to stop after PyInstaller.")
    build_installer(iscc, a.signtool)
    write_checksums()
    print("[build] done. Installer and SHA256SUMS.txt in", INSTALLER_DIR)


if __name__ == "__main__":
    main()
