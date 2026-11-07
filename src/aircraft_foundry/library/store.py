"""Design library: a single SQLite file of saved aircraft.

Portability & robustness choices (the file lives on a USB stick that is used
from Windows and macOS, and may be unplugged at any time):

* SQLite's file format is identical on every OS and CPU, and the library
  stores only text/JSON and PNG bytes - never pickles or absolute paths.
* ``journal_mode=DELETE`` (not WAL: WAL needs shared memory that is
  unreliable on exFAT/FAT32 and leaves extra files), ``synchronous=FULL``.
* Connections are opened per operation and closed immediately, so nothing
  is held open when the stick is pulled.
* Write-protected media fall back to read-only mode.
* Not designed for simultaneous use over a network share.

The core library has no Qt dependency; thumbnails are passed in as PNG bytes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..core.mission import MissionRequirements
from ..core.parameters import AircraftParameters

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    created_utc     TEXT NOT NULL,
    modified_utc    TEXT NOT NULL,
    app_version     TEXT NOT NULL,
    created_on      TEXT NOT NULL,
    configuration   TEXT NOT NULL,
    mission_name    TEXT NOT NULL,
    feasible        INTEGER NOT NULL,
    score           REAL,
    mtow_kg         REAL,
    span_m          REAL,
    tags            TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL,
    mission_json    TEXT NOT NULL,
    metrics_json    TEXT NOT NULL DEFAULT '{}',
    thumbnail_png   BLOB,
    origin          TEXT NOT NULL DEFAULT '',
    parent_id       TEXT
);
CREATE INDEX IF NOT EXISTS designs_modified ON designs(modified_utc);
CREATE INDEX IF NOT EXISTS designs_name ON designs(name);
"""
LIST_COLUMNS = ("id", "name", "created_utc", "modified_utc", "app_version", "created_on", "configuration",
                "mission_name", "feasible", "score", "mtow_kg", "span_m", "tags", "notes", "origin", "thumbnail_png")


class LibraryReadOnly(RuntimeError):
    pass


@dataclass
class LibraryEntry:
    id: str
    name: str
    created_utc: str
    modified_utc: str
    app_version: str
    created_on: str
    configuration: str
    mission_name: str
    feasible: bool
    score: float | None
    mtow_kg: float | None
    span_m: float | None
    tags: str
    notes: str
    origin: str
    thumbnail_png: bytes | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DesignLibrary:
    def __init__(self, path: str | Path, created_on: str = ""):
        self.path = Path(path)
        self.created_on = created_on
        self.read_only = False
        # The rollback journal is created next to the database, so the folder
        # must be writable too (write-protected stick, read-only share).
        if self.path.exists() and not (os.access(self.path, os.W_OK) and os.access(self.path.parent, os.W_OK)):
            self.read_only = True
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as con:
                con.executescript(SCHEMA)
                self._migrate(con)
        except (sqlite3.OperationalError, PermissionError, OSError):
            if not self.path.exists():
                raise
            self.read_only = True  # e.g. write-protected stick: browsing still works

    # ------------------------------------------------------------ connection
    @contextmanager
    def _connect(self, write: bool = True):
        if self.read_only and write:
            raise LibraryReadOnly(f"the library at {self.path} is read-only")
        uri = f"file:{self.path.as_posix()}" + ("" if write and not self.read_only else "?mode=ro")
        con = sqlite3.connect(uri, uri=True, timeout=10.0, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout = 10000")
            if write:
                con.execute("PRAGMA journal_mode = DELETE")
                con.execute("PRAGMA synchronous = FULL")
            con.row_factory = sqlite3.Row
            yield con
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc) or "read-only" in str(exc):
                self.read_only = True
                raise LibraryReadOnly(f"the library at {self.path} is read-only") from exc
            raise
        finally:
            con.close()

    def _migrate(self, con) -> None:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        version = int(row[0]) if row else 0
        if version > SCHEMA_VERSION:
            self.read_only = True  # created by a newer app: never write to it
            return
        if version < SCHEMA_VERSION:
            # future migrations go here: if version < 2: ALTER TABLE ...
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            con.execute("INSERT OR IGNORE INTO meta VALUES ('created_utc', ?)", (_now(),))

    # ------------------------------------------------------------ write
    def save(self, params: AircraftParameters, mission: MissionRequirements, name: str, *,
             configuration: str = "", evaluation=None, thumbnail_png: bytes | None = None, tags: str = "",
             notes: str = "", origin: str = "", parent_id: str | None = None, design_id: str | None = None) -> str:
        """Insert a design, or replace it when ``design_id`` exists.  Returns the id."""
        ev = evaluation
        did = design_id or uuid.uuid4().hex
        now = _now()
        metrics = dict(ev.metrics) if ev is not None else {}
        row = {
            "id": did, "name": name.strip() or "Untitled design", "modified_utc": now, "app_version": __version__,
            "configuration": configuration, "mission_name": mission.name,
            "feasible": int(bool(ev and ev.feasible)), "score": ev.score if ev is not None else None,
            "mtow_kg": metrics.get("mtow_kg"), "span_m": params.wing.span, "tags": tags, "notes": notes,
            "parameters_json": json.dumps(params.to_dict()), "mission_json": json.dumps(mission.to_dict()),
            "metrics_json": json.dumps(metrics), "thumbnail_png": thumbnail_png, "origin": origin,
            "parent_id": parent_id,
        }
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute("SELECT created_utc, created_on FROM designs WHERE id=?", (did,)).fetchone()
            row["created_utc"] = existing["created_utc"] if existing else now
            row["created_on"] = existing["created_on"] if existing else self.created_on
            cols = ", ".join(row)
            con.execute(f"INSERT OR REPLACE INTO designs ({cols}) VALUES ({', '.join('?' * len(row))})",
                        tuple(row.values()))
            con.execute("COMMIT")
        return did

    def update(self, design_id: str, **fields) -> None:
        allowed = {"name", "tags", "notes"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._connect() as con:
            con.execute(f"UPDATE designs SET {sets}, modified_utc=? WHERE id=?", (*fields.values(), _now(), design_id))

    def delete(self, design_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM designs WHERE id=?", (design_id,))

    # ------------------------------------------------------------ read
    def list(self, search: str = "", feasible_only: bool = False) -> list[LibraryEntry]:
        sql = f"SELECT {', '.join(LIST_COLUMNS)} FROM designs"
        where, args = [], []
        if search:
            like = f"%{search}%"
            where.append("(name LIKE ? OR tags LIKE ? OR notes LIKE ? OR configuration LIKE ? OR mission_name LIKE ?)")
            args += [like] * 5
        if feasible_only:
            where.append("feasible = 1")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY modified_utc DESC"
        with self._connect(write=False) as con:
            rows = con.execute(sql, args).fetchall()
        return [LibraryEntry(**{k: (bool(r[k]) if k == "feasible" else r[k]) for k in LIST_COLUMNS}) for r in rows]

    def load(self, design_id: str) -> tuple[AircraftParameters, MissionRequirements, LibraryEntry]:
        with self._connect(write=False) as con:
            r = con.execute("SELECT * FROM designs WHERE id=?", (design_id,)).fetchone()
        if r is None:
            raise KeyError(design_id)
        entry = LibraryEntry(**{k: (bool(r[k]) if k == "feasible" else r[k]) for k in LIST_COLUMNS})
        return (AircraftParameters.from_dict(json.loads(r["parameters_json"])),
                MissionRequirements.from_dict(json.loads(r["mission_json"])), entry)

    def count(self) -> int:
        with self._connect(write=False) as con:
            return int(con.execute("SELECT COUNT(*) FROM designs").fetchone()[0])

    def integrity_ok(self) -> bool:
        with self._connect(write=False) as con:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
