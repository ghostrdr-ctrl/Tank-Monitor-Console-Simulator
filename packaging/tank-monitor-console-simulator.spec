# -*- mode: python ; coding: utf-8 -*-
# Tank Monitor Console Simulator -- PyInstaller build specification.
# Copyright (C) 2026 Verbose Software. GNU GPL v3.
#
# Build (from the repo root):
#     pyinstaller packaging/tank-monitor-console-simulator.spec
#
# Produces dist/TankMonitorConsoleSimulator/ with the .exe and its runtime.
# The app is pure standard library plus tkinter, so there are no third-party
# packages to collect and no hidden imports to chase: what PyInstaller finds
# by following the imports from run.py is the whole program.
#
# One-folder, not one-file. A one-file build unpacks itself to a temp
# directory on every launch, which a locked-down site's antivirus treats as
# suspicious and which slows the first window; a one-folder build starts
# faster and is what the Inno Setup installer packages anyway.

import glob
import os

block_cipher = None
# SPECPATH is injected by PyInstaller and points at this file's directory;
# the repo root is its parent. Script and data paths must be absolute
# because PyInstaller resolves a spec's relative paths from its own location,
# not the working directory the build was launched from.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
ICON = os.path.join(ROOT, "assets", "icon.ico")

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    # The licence travels with the binary: GPL section 6 wants the terms
    # delivered alongside the program, and the About box points at it.
    # The package reads several *.json data files from beside its own
    # modules at import time (versions.py, console.py, wire.py). PyInstaller
    # only collects .py by default, so those must be listed or the frozen
    # app dies on the first import with FileNotFoundError. Globbing every
    # JSON in the package means a new data file is picked up automatically.
    datas=[
        (os.path.join(ROOT, "LICENSE"), "."),
        (os.path.join(ROOT, "assets", "icon.png"), "assets"),
    ] + [(f, "tls350sim")
         for f in glob.glob(os.path.join(ROOT, "tls350sim", "*.json"))
         if not f.endswith("_state.json")],   # runtime state, not shipped data
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Trim parts of the standard library the simulator never touches, to keep
    # the installer small. Kept conservative on purpose: `email`, `http` and
    # the rest of urllib's dependencies are NOT excluded, because the updater
    # imports urllib.request and that pulls them in. Only leaf packages with
    # no path from the app are listed here.
    excludes=[
        "pydoc", "doctest", "pdb", "lib2to3", "test", "idlelib",
        "distutils", "setuptools", "pip", "pygments", "PIL", "numpy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TankMonitorConsoleSimulator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # a GUI app: no console window
    disable_windowed_traceback=False,
    icon=ICON,
    version=os.path.join(SPECPATH, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TankMonitorConsoleSimulator",
)
