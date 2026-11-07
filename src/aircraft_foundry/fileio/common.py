"""Shared export helpers: component selection and safe file names."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.components import Component
from ..core.design import AircraftDesign


@dataclass
class ExportSelection:
    """Which components to export.

    component_ids: explicit list (None = whole aircraft).
    include_structure: include ribs, spars, frames, longerons, fittings.
    """

    component_ids: list[str] | None = None
    include_structure: bool = False
    include_children: bool = True
    exclude_types: set[str] = field(default_factory=set)

    def resolve(self, design: AircraftDesign) -> list[Component]:
        if self.component_ids is None:
            ids = list(design.components)
        else:
            ids = []
            for cid in self.component_ids:
                if cid not in design.components:
                    raise KeyError(f"unknown component {cid!r}")
                ids.append(cid)
                if self.include_children:
                    ids += [c.id for c in design.components.values()
                            if c.parent == cid or c.id.startswith(cid + ".")]
        out, seen = [], set()
        for cid in ids:
            c = design.components[cid]
            if cid in seen or c.type.value in self.exclude_types:
                continue
            if c.is_structure and not self.include_structure and (
                    self.component_ids is None or cid not in self.component_ids):
                continue
            seen.add(cid)
            out.append(c)
        return out


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "component"
