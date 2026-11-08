"""JSON export/import of editable aircraft design parameters.

The file holds the parameters (the editable source of truth), the mission,
the evaluation summary and a read-only component manifest (ids, types,
placements, relationships).  Importing uses only the parameters and
regenerates everything else.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from ..core.design import AircraftDesign
from ..core.evaluator import Evaluation
from ..core.mission import MissionRequirements
from ..core.parameters import AircraftParameters

FORMAT = "aircraft-foundry-design"


def design_document(design: AircraftDesign, mission: MissionRequirements | None = None,
                    evaluation: Evaluation | None = None, include_manifest: bool = True) -> dict:
    doc = {
        "format": FORMAT,
        "format_version": 1,
        "app_version": __version__,
        "units": {"length": "m", "angle": "deg", "mass": "kg", "thrust": "kN"},
        "axes": "x aft from nose, y starboard, z up",
        "design": design.to_dict(),
    }
    if mission is not None:
        doc["mission"] = mission.to_saved_dict()
    if evaluation is not None:
        doc["evaluation"] = evaluation.to_dict()
    if include_manifest:
        doc["components"] = design.manifest()
    return doc


def export_design_json(path: str | Path, design: AircraftDesign, mission=None, evaluation=None) -> Path:
    path = Path(path)
    path.write_text(json.dumps(design_document(design, mission, evaluation), indent=2, default=float))
    return path


def import_design_json(path: str | Path) -> tuple[AircraftDesign, MissionRequirements | None]:
    d = json.loads(Path(path).read_text())
    if d.get("format") != FORMAT:
        # also accept a bare parameter dictionary
        if "wing" in d and "fuselage" in d:
            return AircraftDesign(AircraftParameters.from_dict(d)), None
        raise ValueError(f"{path} is not an Aircraft Foundry design file")
    design = AircraftDesign.from_dict(d["design"])
    mission = MissionRequirements.from_dict(d["mission"]) if "mission" in d else None
    return design, mission
