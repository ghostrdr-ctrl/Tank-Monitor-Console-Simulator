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
  3. Zip that folder -> dist/installer/...-Portable.zip.
  4. Run Inno Setup (ISCC) on the .iss -> dist/installer/...-Setup.exe.
  5. Write dist/installer/SHA256SUMS.txt over both of them.

The SHA256SUMS.txt is not decoration: the app's own updater refuses to run a
downloaded installer that is not listed there (tls350sim/update.py), so a
release without it cannot self-update. The CI workflow publishes both the
installer and this file to the GitHub release together.

The portable zip is that same one-folder build handed over as an archive
instead. It is there for sites whose endpoint policy blocks an .exe download
outright, with no override for the person trying to install it; a .zip
usually passes where the installer does not. It is a second way to get the
same program, not a second program -- same files, same per-user state
directory, so a machine can run either and see the same console.

Signing is out of scope here: these builds are unsigned for now. When a
certificate is available, --signtool signs dist/TankMonitorConsoleSimulator/
*.exe before either package is made and the installer before the checksums
are taken, so the sums cover the signed bytes and both the zip and the
installer carry signed binaries.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tls350sim import APP_NAME, PUBLISHER, __version__      # noqa: E402

SPEC = os.path.join("packaging", "tank-monitor-console-simulator.spec")
ISS = os.path.join("packaging", "installer.iss")
DIST = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST, "TankMonitorConsoleSimulator")
INSTALLER_DIR = os.path.join(DIST, "installer")
EXE_NAME = "TankMonitorConsoleSimulator.exe"

# The folder the zip unpacks to, which must be what PyInstaller called the
# one-folder output (COLLECT name= in the spec). The zip carries a top-level
# folder on purpose: a flat archive sprays several hundred files into
# whatever directory the person happened to extract from.
PORTABLE_ROOT = os.path.basename(APP_DIR)

# No spaces, for the same reason the installer has none: GitHub rewrites a
# space to a dot in a release asset, which would leave the served name at odds
# with the name SHA256SUMS.txt records, and a technician comparing the two by
# eye sees a mismatch that is not real.
PORTABLE_ZIP = f"{PORTABLE_ROOT}-{__version__}-Portable.zip"

# What goes in HOW-TO-RUN.txt inside the zip. A portable download arrives with
# no installer to explain itself, and the two things people get wrong --
# running the .exe from inside Explorer's zip viewer, and the SmartScreen
# warning -- both look like the program is broken rather than like Windows
# being careful. Answering them inside the archive is cheaper than answering
# them by email, and the person who needs the answer is offline by then.
HOW_TO_RUN = r"""{app} {version} -- portable build

WHAT THIS IS

The same program the installer installs, as a folder you can put anywhere.
Nothing goes into Program Files and nothing is written to the registry, so no
administrator rights are needed and there is nothing to uninstall: delete the
folder when you are done with it.

RUNNING IT

  1. Extract this whole folder somewhere you can write to, such as your
     Desktop or your Documents.
  2. Run {exe}.

Extract it first. Windows will happily open the .exe from inside the zip
viewer without extracting, but the program cannot find the runtime files it
needs from in there and will fail on startup.

IF WINDOWS WARNS ABOUT AN UNRECOGNISED APP

This build is not code-signed yet, so SmartScreen shows "Windows protected
your PC" the first time. Choose "More info", then "Run anyway".

You can avoid the warning altogether by clearing the mark Windows puts on a
downloaded file BEFORE extracting: right-click the downloaded .zip, choose
Properties, tick "Unblock" at the bottom of the General tab, then Apply.
Files extracted after that are not treated as downloaded.

CHECKING WHAT YOU DOWNLOADED

The release publishes SHA256SUMS.txt beside this zip. In PowerShell:

    Get-FileHash .\{zip} -Algorithm SHA256

and compare the result against that file's line for this zip.

WHERE IT KEEPS ITS PROGRAMMING

Console state, settings and the XPort configuration are written to

    %LOCALAPPDATA%\{publisher}\{app}

and not into this folder, so replacing this folder with a newer one keeps a
site's programming. An installed copy uses that same directory, which means a
portable copy and an installed copy on one machine share one set of
programming rather than each keeping its own.

LICENCE

Free software under the GNU General Public License v3. The full terms are in
LICENSE.txt beside this file.
"""


def how_to_run():
    return HOW_TO_RUN.format(app=APP_NAME, version=__version__,
                             publisher=PUBLISHER, exe=EXE_NAME,
                             zip=PORTABLE_ZIP)


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
    exe = os.path.join(APP_DIR, EXE_NAME)
    if not os.path.exists(exe):
        sys.exit("[build] PyInstaller did not produce the .exe")


def sign(signtool, path):
    print(f"[build] signing {os.path.basename(path)}")
    run([signtool, "sign", "/fd", "SHA256", "/tr",
         "http://timestamp.digicert.com", "/td", "SHA256", "/a", path])


def sign_app_dir(signtool):
    """Sign every .exe in the app folder, before anything packages it.

    Both the portable zip and the installer are made from this folder, so
    signing here means neither can ship an unsigned binary while the other
    ships a signed one.
    """
    for root, _dirs, files in os.walk(APP_DIR):
        for name in sorted(files):
            if name.lower().endswith(".exe"):
                sign(signtool, os.path.join(root, name))


def build_portable_zip():
    """The one-folder build as a .zip, for sites that cannot fetch an .exe.

    Deflate, not one of the better-compressing formats: Explorer opens a
    deflate zip on its own, and the machines this exists for are exactly the
    machines where the user cannot install 7-Zip to open something else.

    The contents mirror what the installer lays down, including LICENSE.txt
    at the top of the folder rather than only inside _internal -- GPL section
    6 wants the terms delivered with the program, and it is where a person
    looks for them.
    """
    os.makedirs(INSTALLER_DIR, exist_ok=True)
    out = os.path.join(INSTALLER_DIR, PORTABLE_ZIP)
    print("[build] portable zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(APP_DIR):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, APP_DIR).replace(os.sep, "/")
                z.write(full, f"{PORTABLE_ROOT}/{rel}")
        z.write(os.path.join(ROOT, "LICENSE"),
                f"{PORTABLE_ROOT}/LICENSE.txt")
        z.writestr(f"{PORTABLE_ROOT}/HOW-TO-RUN.txt", how_to_run())
    print(f"[build] wrote {out}")
    return out


def build_installer(iscc):
    print("[build] Inno Setup")
    run([iscc, f"/DMyAppVersion={__version__}", ISS])
    # Must match OutputBaseFilename in packaging/installer.iss. Spaces are
    # deliberately absent: GitHub rewrites them to dots in a release asset,
    # which would put the served name at odds with the one SHA256SUMS.txt
    # records.
    setup = os.path.join(
        INSTALLER_DIR,
        f"TankMonitorConsoleSimulator-{__version__}-Setup.exe")
    if not os.path.exists(setup):
        built = [n for n in sorted(os.listdir(INSTALLER_DIR))
                 if n.lower().endswith(".exe")] \
            if os.path.isdir(INSTALLER_DIR) else []
        sys.exit(f"[build] Inno Setup did not produce {setup}\n"
                 f"[build] the installer directory holds: "
                 f"{', '.join(built) or 'nothing'}\n"
                 f"[build] if that name looks right but differs, "
                 f"OutputBaseFilename in packaging/installer.iss and this "
                 f"file have drifted apart")
    return setup


def write_checksums():
    """SHA256SUMS.txt over every package in the installer directory.

    The .exe line is required, not decoration: the in-app updater looks its
    installer up by name here and refuses to run a download that is not
    listed (tls350sim/update.py). The .zip line is for a person checking a
    portable download by hand, which is the only check a portable download
    gets -- there is no installer in that path to do it for them.
    """
    lines = []
    for name in sorted(os.listdir(INSTALLER_DIR)):
        if not name.lower().endswith((".exe", ".zip")):
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
                    help="stop after the app folder and the portable zip, "
                         "and do not build the .exe installer (for when Inno "
                         "Setup is not present)")
    a = ap.parse_args()

    print(f"[build] {APP_NAME} {__version__}")
    make_icon_if_missing()
    build_exe()
    # Before either package is made, so both carry the same signed binaries
    # and the checksums at the end are taken over the signed bytes.
    if a.signtool:
        sign_app_dir(a.signtool)
    build_portable_zip()

    if a.skip_installer:
        # The zip is a shippable artifact on its own, so it gets a checksum
        # line whether or not Inno Setup was here to build the installer.
        write_checksums()
        print("[build] done (installer skipped). Portable zip and "
              "SHA256SUMS.txt in", INSTALLER_DIR)
        return

    iscc = find_iscc()
    if not iscc:
        sys.exit("[build] Inno Setup 6 not found. Install it from "
                 "https://jrsoftware.org/isdl.php, or re-run with "
                 "--skip-installer to stop after the portable zip.")
    setup = build_installer(iscc)
    if a.signtool:
        sign(a.signtool, setup)
    write_checksums()
    print("[build] done. Installer, portable zip and SHA256SUMS.txt in",
          INSTALLER_DIR)


if __name__ == "__main__":
    main()
