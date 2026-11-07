"""Structured aircraft components.

Every component carries a unique, *stable* identifier (a dotted path such as
``wing.right.rib.07``), a type, its own geometry in its local frame, a
placement, typed relationships to other components and a snapshot of the
design parameters it was generated from.  Stable identifiers let the renderer
and the GUI keep their state (selection, actors) across regenerations.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ..geometry.loft import Loft, loft_to_mesh
from ..geometry.mesh import Mesh, Placement


class ComponentType(str, Enum):
    FUSELAGE = "fuselage"
    WING = "wing"
    HORIZONTAL_TAIL = "horizontal_tail"
    VERTICAL_TAIL = "vertical_tail"
    NACELLE = "nacelle"
    PYLON = "pylon"
    PROPELLER = "propeller"
    GEAR_STRUT = "gear_strut"
    WHEEL = "wheel"
    AILERON = "aileron"
    FLAP = "flap"
    ELEVATOR = "elevator"
    RUDDER = "rudder"
    ELEVON = "elevon"
    RUDDERVATOR = "ruddervator"
    DRAG_RUDDER = "drag_rudder"
    CANARD = "canard"
    V_TAIL = "v_tail"
    BOOM = "boom"
    RIB = "rib"
    SPAR = "spar"
    FRAME = "frame"
    LONGERON = "longeron"
    ATTACHMENT = "attachment"

    @property
    def is_structure(self) -> bool:
        return self in STRUCTURAL_TYPES

    @property
    def is_control_surface(self) -> bool:
        return self in CONTROL_SURFACE_TYPES


STRUCTURAL_TYPES = {ComponentType.RIB, ComponentType.SPAR, ComponentType.FRAME, ComponentType.LONGERON,
                    ComponentType.ATTACHMENT}
CONTROL_SURFACE_TYPES = {ComponentType.AILERON, ComponentType.FLAP, ComponentType.ELEVATOR, ComponentType.RUDDER,
                         ComponentType.ELEVON, ComponentType.RUDDERVATOR, ComponentType.DRAG_RUDDER}


class Relation(str, Enum):
    PART_OF = "part_of"  # structural member belongs to a parent assembly
    ATTACHED_TO = "attached_to"  # primary load path joint
    MOUNTED_ON = "mounted_on"
    BOUNDED_BY = "bounded_by"  # e.g. rib web between front and rear spar
    CONNECTS = "connects"  # attachment fitting joining two components
    HINGED_TO = "hinged_to"  # control surface rotating about a hinge line on its parent


@dataclass(frozen=True)
class Connection:
    target: str
    relation: Relation
    point: tuple[float, float, float] | None = None  # aircraft frame, metres
    note: str = ""

    def to_dict(self) -> dict:
        return {"target": self.target, "relation": self.relation.value,
                "point": list(self.point) if self.point is not None else None, "note": self.note}


@dataclass
class Component:
    id: str
    type: ComponentType
    name: str
    lofts: list[Loft]
    placement: Placement = field(default_factory=Placement)
    parent: str | None = None
    connections: list[Connection] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)  # associated design parameters / derived values
    source_groups: tuple[str, ...] = ()
    _mesh: Mesh | None = field(default=None, repr=False, compare=False)
    _geom_fp: str | None = field(default=None, repr=False, compare=False)

    @property
    def is_structure(self) -> bool:
        return self.type.is_structure

    def local_mesh(self) -> Mesh:
        if self._mesh is None:
            self._mesh = Mesh.merge([loft_to_mesh(lo) for lo in self.lofts])
        return self._mesh

    def world_mesh(self) -> Mesh:
        return self.local_mesh().transformed(self.placement)

    @property
    def geometry_fingerprint(self) -> str:
        if self._geom_fp is None:
            h = hashlib.blake2b(digest_size=12)
            for lo in self.lofts:
                lo.fingerprint(h)
            self._geom_fp = h.hexdigest()
        return self._geom_fp

    @property
    def fingerprint(self) -> str:
        """Changes whenever geometry, placement or relationships change."""
        h = hashlib.blake2b(self.geometry_fingerprint.encode(), digest_size=12)
        h.update(np.round(self.placement.matrix(), 9).tobytes())
        h.update(repr([c.to_dict() for c in self.connections]).encode())
        return h.hexdigest()

    def world_position(self) -> np.ndarray:
        return np.array(self.placement.position)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "parent": self.parent,
            "placement": self.placement.to_dict(),
            "connections": [c.to_dict() for c in self.connections],
            "parameters": self.parameters,
            "source_groups": list(self.source_groups),
        }
