"""Where Aircraft Foundry keeps its data: portable (USB stick) or per-user.

Portable mode
-------------
If a folder called ``AircraftFoundryData`` containing ``portable.json`` sits
next to the application (or up to three folders above it), *all* data goes
there: the design library, projects, exports and settings.  Paths are always
derived from where the program runs, never stored, so a stick that is drive
``E:`` on one Windows PC and ``F:`` on another, or ``/Volumes/AIRCRAFT`` on a
Mac, works the same way.

macOS app translocation: when an unsigned app carrying the download
quarantine flag is opened, Gatekeeper may run it from a random read-only
copy (``/private/var/folders/.../AppTranslocation/...``), which hides the
USB folder.  We detect that and look for the marker on mounted volumes.
The USB "Mac first-run fix" script removes the quarantine flag permanently.

Environment override: ``AIRCRAFT_FOUNDRY_DATA=/some/folder``.
"""
from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_NAME = "AircraftFoundryData"
MARKER = "portable.json"
ENV_VAR = "AIRCRAFT_FOUNDRY_DATA"


@dataclass(frozen=True)
class DataLocation:
    root: Path
    portable: bool
    source: str  # how it was found (for the status bar / diagnostics)

    @property
    def library_path(self) -> Path:
        return self.root / "library.sqlite"

    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def settings_path(self) -> Path:
        return self.root / "settings.ini"

    def ensure(self) -> "DataLocation":
        for d in (self.root, self.projects_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def application_dir() -> Path:
    """Folder that contains the program (the .app bundle's parent on macOS)."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:  # .../Aircraft Foundry.app/Contents/MacOS/AircraftFoundry
            if parent.suffix == ".app":
                return parent.parent
        return exe.parent
    return Path(__file__).resolve().parents[2]  # source checkout: repo root (src/aircraft_foundry/..)


def is_translocated(path: Path) -> bool:
    return "AppTranslocation" in str(path)


def _marker_in(folder: Path) -> Path | None:
    cand = folder / DATA_DIR_NAME
    return cand if (cand / MARKER).is_file() else None


def find_portable_root(start: Path, levels: int = 3) -> Path | None:
    for folder in [start, *list(start.parents)[:levels]]:
        found = _marker_in(folder)
        if found:
            return found
    return None


def _mounted_volumes() -> list[Path]:
    if sys.platform == "darwin":
        base = Path("/Volumes")
    elif sys.platform.startswith("linux"):
        user = os.environ.get("USER", "")
        return [p for b in (Path("/media") / user, Path("/run/media") / user, Path("/mnt")) if b.is_dir()
                for p in b.iterdir()]
    else:
        return []
    return [p for p in base.iterdir()] if base.is_dir() else []


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Aircraft Foundry"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Aircraft Foundry"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "aircraft-foundry"


def locate(app_dir: Path | None = None) -> DataLocation:
    env = os.environ.get(ENV_VAR)
    if env:
        return DataLocation(Path(env), True, f"${ENV_VAR}")
    app_dir = app_dir or application_dir()
    found = find_portable_root(app_dir)
    if found:
        return DataLocation(found, True, f"next to the application ({app_dir})")
    if is_translocated(app_dir):
        hits = [v / DATA_DIR_NAME for v in _mounted_volumes() if _marker_in(v)]
        if len(hits) == 1:
            return DataLocation(hits[0], True, "mounted volume (app was translocated by Gatekeeper)")
    return DataLocation(user_data_dir(), False, "per-user data folder")


def create_portable_marker(folder: Path, note: str = "") -> Path:
    """Turn ``folder/AircraftFoundryData`` into a portable data folder."""
    data = folder / DATA_DIR_NAME
    data.mkdir(parents=True, exist_ok=True)
    marker = data / MARKER
    if not marker.exists():
        marker.write_text(json.dumps({"format": "aircraft-foundry-portable", "version": 1, "note": note}, indent=2))
    return data


def host_description() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()}) on {platform.node() or 'unknown host'}"
