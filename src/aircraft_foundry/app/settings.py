"""Application settings stored as an INI file in the data folder.

Using an explicit INI file (instead of the Windows registry / macOS plist)
keeps settings on the USB stick in portable mode.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

_PATH: str | None = None


def configure(path: str) -> None:
    global _PATH
    _PATH = path


def settings() -> QSettings:
    if _PATH is None:
        return QSettings()
    return QSettings(_PATH, QSettings.IniFormat)
