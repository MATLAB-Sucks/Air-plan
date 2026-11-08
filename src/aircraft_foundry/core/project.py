"""Project persistence (JSON).

Only *editable inputs* are stored: the mission and each candidate's
parameters.  Geometry and evaluations are deterministic functions of those
inputs and are regenerated on load, so a project file can never hold
geometry that disagrees with its parameters.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .generator import Candidate
from .mission import MissionRequirements

FORMAT = "aircraft-foundry-project"
FORMAT_VERSION = 1
SUFFIX = ".afproj"


@dataclass
class Project:
    mission: MissionRequirements = field(default_factory=MissionRequirements)
    candidates: list[Candidate] = field(default_factory=list)
    active_id: str | None = None
    path: Path | None = None
    settings: dict = field(default_factory=dict)

    def candidate(self, cid: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == cid), None)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mission": self.mission.to_saved_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "active_id": self.active_id,
            "settings": self.settings,
        }

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.path)
        if target.suffix != SUFFIX:
            target = target.with_suffix(SUFFIX)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(target)  # atomic: never leave a half-written project
        self.path = target
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        d = json.loads(Path(path).read_text())
        if d.get("format") != FORMAT:
            raise ValueError(f"{path} is not an Aircraft Foundry project")
        if d.get("format_version", 0) > FORMAT_VERSION:
            raise ValueError("project was saved by a newer version of Aircraft Foundry")
        mission = MissionRequirements.from_dict(d["mission"])
        cands = [Candidate.from_dict(c, mission) for c in d.get("candidates", [])]
        return cls(mission, cands, d.get("active_id"), Path(path), d.get("settings", {}))
