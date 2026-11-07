"""Structural summaries for the inspector."""
from __future__ import annotations

from collections import Counter

from ..core.components import ComponentType as CT
from ..core.design import AircraftDesign

ASSEMBLIES = ("wing.right", "wing.left", "htail.right", "htail.left", "vtail", "fuselage")


def structure_summary(design: AircraftDesign) -> dict[str, dict[str, int]]:
    """Count structural members per parent assembly."""
    out: dict[str, Counter] = {a: Counter() for a in ASSEMBLIES}
    for c in design.components.values():
        if c.is_structure and c.parent in out:
            out[c.parent][c.type.value] += 1
    return {k: dict(v) for k, v in out.items() if v}


def members_of(design: AircraftDesign, assembly: str, member_type: CT | None = None) -> list[str]:
    return [c.id for c in design.components.values()
            if c.parent == assembly and c.is_structure and (member_type is None or c.type == member_type)]
