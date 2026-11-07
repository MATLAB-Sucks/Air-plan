"""Single entry point used by the GUI export manager and the command line."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.design import AircraftDesign
from ..core.evaluator import Evaluation
from ..core.mission import MissionRequirements
from . import cad_export, json_io, mesh_export
from .common import ExportSelection, safe_name

FORMATS = ("step", "stl", "obj", "json")


@dataclass
class ExportRequest:
    directory: Path
    formats: tuple[str, ...] = ("step", "stl", "obj", "json")
    selection: ExportSelection = field(default_factory=ExportSelection)
    per_component: bool = False  # also write one file per component
    basename: str = "aircraft"


def run_export(req: ExportRequest, design: AircraftDesign, mission: MissionRequirements | None = None,
               evaluation: Evaluation | None = None, progress=None) -> list[Path]:
    unknown = set(req.formats) - set(FORMATS)
    if unknown:
        raise ValueError(f"unsupported formats: {sorted(unknown)}")
    out_dir = Path(req.directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    comps = req.selection.resolve(design)
    if not comps:
        raise ValueError("nothing selected for export")
    base = safe_name(req.basename)
    written: list[Path] = []
    steps = len(req.formats)

    def sub(i):
        return (lambda f, msg: progress((i + f) / steps, msg)) if progress else None

    for i, fmt in enumerate(req.formats):
        if fmt == "json":
            written.append(json_io.export_design_json(out_dir / f"{base}.json", design, mission, evaluation))
        elif fmt == "stl":
            written.append(mesh_export.export_stl(comps, out_dir / f"{base}.stl"))
            if req.per_component:
                written += mesh_export.export_each(comps, out_dir / "stl_components", "stl")
        elif fmt == "obj":
            written.append(mesh_export.export_obj(comps, out_dir / f"{base}.obj"))
            if req.per_component:
                written += mesh_export.export_each(comps, out_dir / "obj_components", "obj")
        elif fmt == "step":
            written.append(cad_export.export_step_assembly(comps, out_dir / f"{base}.step", base, sub(i)))
            if req.per_component:
                written += cad_export.export_step_each(comps, out_dir / "step_components", sub(i))
        if progress:
            progress((i + 1) / steps, f"{fmt.upper()} written")
    return written
