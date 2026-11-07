"""Assemble a portable USB layout from the Windows and macOS builds.

    python packaging/assemble_usb.py --windows dist-win/AircraftFoundry \
        --mac "dist-mac/Aircraft Foundry.app" --out USB

Result (copy the *contents* of USB/ to the root of an exFAT-formatted stick):

    Aircraft Foundry.app                       <- double-click on a Mac (Intel; Apple Silicon via Rosetta)
    Start Aircraft Foundry (Windows).bat       <- double-click on Windows
    Mac first-run fix.command                  <- run once if macOS says the app is damaged / can't be opened
    Windows/AircraftFoundry/AircraftFoundry.exe
    AircraftFoundryData/                       <- shared by both: library, projects, exports, settings
    README-USB.txt
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aircraft_foundry import __version__  # noqa: E402
from aircraft_foundry.portable import DATA_DIR_NAME, create_portable_marker  # noqa: E402

BAT = r"""@echo off
rem Starts Aircraft Foundry from this USB stick; all data stays in AircraftFoundryData.
start "" "%~dp0Windows\AircraftFoundry\AircraftFoundry.exe" %*
"""

MAC_FIX = """#!/bin/bash
# macOS marks apps copied from the internet as "quarantined" and may then run
# them from a hidden read-only location (or refuse to open an unsigned app).
# This removes the flag from the copy on this stick so it opens normally and
# finds the AircraftFoundryData folder next to it.
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine "Aircraft Foundry.app" 2>/dev/null
echo "Done. You can now open Aircraft Foundry.app (right-click > Open the first time)."
"""

README = """Aircraft Foundry {version} - portable edition
=============================================

Windows:  double-click  "Start Aircraft Foundry (Windows).bat"
macOS:    double-click  "Aircraft Foundry.app"
          (first time: right-click > Open; if macOS refuses, run "Mac first-run fix.command")

Both versions use the same AircraftFoundryData folder on this stick:
  library.sqlite   shared design library (Library menu / "Design library" tab)
  projects/        project files (.afproj)
  exports/         STEP / STL / OBJ / JSON exports
  settings.ini     window layout and preferences

Rules for the stick
  * Format it as exFAT (readable and writable by both Windows and macOS; NTFS
    is read-only on a Mac, FAT32 also works).
  * Use "Eject" / "Safely remove" before unplugging.
  * Use one computer at a time; do not open the library from two computers
    simultaneously (e.g. over a network share).
  * The Mac build is Intel (x86_64) and runs on Apple Silicon through Rosetta 2
    (macOS offers to install Rosetta the first time).  macOS 12 or later.
  * Windows 10/11, 64-bit, with OpenGL 3.2 graphics (any GPU from the last decade;
    some remote-desktop sessions lack OpenGL).
"""


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)  # .app bundles contain symlinks


def assemble(out: Path, windows: Path | None, mac: Path | None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    if windows:
        if not (windows / "AircraftFoundry.exe").exists():
            raise SystemExit(f"{windows} does not contain AircraftFoundry.exe")
        _copytree(windows, out / "Windows" / "AircraftFoundry")
        (out / "Start Aircraft Foundry (Windows).bat").write_text(BAT.replace("\n", "\r\n"))
    if mac:
        if mac.suffix != ".app":
            raise SystemExit(f"{mac} is not a .app bundle")
        _copytree(mac, out / "Aircraft Foundry.app")
        fix = out / "Mac first-run fix.command"
        fix.write_text(MAC_FIX)
        fix.chmod(fix.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    data = create_portable_marker(out, note="Shared data for the Windows and macOS builds on this stick.")
    for sub in ("projects", "exports"):
        (data / sub).mkdir(exist_ok=True)
    (out / "README-USB.txt").write_text(README.format(version=__version__).replace("\n", os.linesep))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windows", type=Path, help="PyInstaller output folder containing AircraftFoundry.exe")
    ap.add_argument("--mac", type=Path, help='"Aircraft Foundry.app" bundle')
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if not a.windows and not a.mac:
        ap.error("give --windows and/or --mac")
    out = assemble(a.out, a.windows, a.mac)
    print(f"Portable layout written to {out} ({DATA_DIR_NAME}/ shared by both builds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
