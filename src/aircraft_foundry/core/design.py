"""The structured aircraft design: parameters + generated components."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..geometry.builder import GENERATORS
from ..geometry.arrangement import Arrangement
from .components import Component, ComponentType
from .parameters import AircraftParameters


@dataclass
class ChangeSet:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    generators_run: list[str] = field(default_factory=list)

    @property
    def touched(self) -> set[str]:
        return set(self.added) | set(self.updated) | set(self.removed)

    def __bool__(self) -> bool:
        return bool(self.added or self.updated or self.removed)


class AircraftDesign:
    """A design whose components are always consistent with its parameters."""

    def __init__(self, params: AircraftParameters | None = None, name: str = "Aircraft",
                 design_id: str | None = None):
        self.id = design_id or uuid.uuid4().hex[:12]
        self.name = name
        self.params = params.copy() if params is not None else AircraftParameters()
        self._by_generator: dict[str, list[Component]] = {}
        self.components: dict[str, Component] = {}
        self.regenerate()

    # ------------------------------------------------------------ generation
    def regenerate(self, groups: set[str] | None = None) -> ChangeSet:
        """Re-run generators that depend on ``groups`` (all when None)."""
        arr = Arrangement(self.params)
        cs = ChangeSet()
        old = dict(self.components)
        for gen in GENERATORS:
            if groups is not None and not (gen.depends_on & groups) and gen.name in self._by_generator:
                continue
            self._by_generator[gen.name] = gen.build(arr)
            cs.generators_run.append(gen.name)
        new: dict[str, Component] = {}
        for gen in GENERATORS:
            for c in self._by_generator[gen.name]:
                if c.id in new:
                    raise RuntimeError(f"duplicate component id {c.id!r}")
                new[c.id] = c
        for cid, comp in new.items():
            if cid not in old:
                cs.added.append(cid)
            elif old[cid].fingerprint != comp.fingerprint:
                cs.updated.append(cid)
            else:
                new[cid] = old[cid]  # keep the cached mesh of the identical component
                cs.unchanged += 1
        cs.removed = [cid for cid in old if cid not in new]
        self.components = new
        self.arrangement = arr
        return cs

    def set_parameter(self, path: str, value) -> ChangeSet:
        self.params.set(path, value)
        return self.regenerate({path.split(".")[0]})

    def set_parameters(self, values: dict[str, object]) -> ChangeSet:
        for path, v in values.items():
            self.params.set(path, v)
        return self.regenerate({p.split(".")[0] for p in values})

    def replace_parameters(self, params: AircraftParameters) -> ChangeSet:
        self.params = params.copy()
        return self.regenerate()

    # ------------------------------------------------------------ queries
    def component(self, cid: str) -> Component:
        return self.components[cid]

    def of_type(self, *types: ComponentType) -> list[Component]:
        return [c for c in self.components.values() if c.type in types]

    def children(self, cid: str) -> list[Component]:
        return [c for c in self.components.values() if c.parent == cid]

    def connected(self, cid: str) -> list[str]:
        """Components linked to ``cid`` in either direction."""
        out = {c.target for c in self.components[cid].connections}
        out |= {c.id for c in self.components.values() if any(k.target == cid for k in c.connections)}
        out.discard(cid)
        return sorted(out)

    def dangling_connections(self) -> list[tuple[str, str]]:
        return [(c.id, k.target) for c in self.components.values() for k in c.connections
                if k.target not in self.components]

    def manifest(self) -> list[dict]:
        return [c.summary() for c in self.components.values()]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "parameters": self.params.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "AircraftDesign":
        return cls(AircraftParameters.from_dict(d["parameters"]), d.get("name", "Aircraft"), d.get("id"))
