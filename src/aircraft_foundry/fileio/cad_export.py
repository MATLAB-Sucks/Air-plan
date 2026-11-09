"""STEP export via CadQuery / OpenCascade.

The assembly keeps one named, coloured part per component, placed with its
own location, so CAD tools show the same structure as the Aircraft Foundry
component tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..core.components import Component
from ..geometry.cad import cadquery_available, component_shape, loft_to_solid, placement_location
from ..structural_vis.styles import style_for
from .common import safe_name


class CadUnavailable(RuntimeError):
    pass


def _require():
    if not cadquery_available():
        raise CadUnavailable("STEP export needs CadQuery (pip install cadquery==2.8.0)")


def build_assembly(components: list[Component], name: str = "aircraft",
                   progress: Callable[[float, str], None] | None = None):
    _require()
    import cadquery as cq
    assy = cq.Assembly(name=safe_name(name))
    for i, c in enumerate(components):
        solids = [loft_to_solid(lo) for lo in c.lofts]
        shape = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
        r, g, b, _ = style_for(c)
        assy.add(shape, name=safe_name(c.id), loc=placement_location(c.placement), color=cq.Color(r, g, b))
        if progress:
            progress((i + 1) / len(components), f"Solid {i + 1}/{len(components)}: {c.id}")
    return assy


def export_step_assembly(components: list[Component], path: str | Path, name: str = "aircraft",
                         progress=None) -> Path:
    components = [c for c in components if c.type.value != "detail"]  # cosmetic details aren't CAD
    path = Path(path)
    assy = build_assembly(components, name, progress)
    assy.export(str(path), exportType="STEP")
    return path


def export_step_each(components: list[Component], directory: str | Path, progress=None) -> list[Path]:
    components = [c for c in components if c.type.value != "detail"]  # cosmetic details aren't CAD
    _require()
    import cadquery as cq
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for i, c in enumerate(components):
        target = directory / f"{safe_name(c.id)}.step"
        cq.exporters.export(cq.Workplane().add(component_shape(c, world=True)), str(target), exportType="STEP")
        out.append(target)
        if progress:
            progress((i + 1) / len(components), f"STEP {i + 1}/{len(components)}: {c.id}")
    return out
