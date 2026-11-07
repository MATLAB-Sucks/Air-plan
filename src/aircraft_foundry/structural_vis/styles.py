"""Colours and per-mode appearance of every component type."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.components import Component, ComponentType as CT


class ViewMode(str, Enum):
    SOLID = "solid"
    TRANSPARENT = "transparent"  # translucent skins, structure visible
    WIREFRAME = "wireframe"
    STRUCTURE = "structure"  # structure only, skins as faint ghosts
    STRESS = "stress"  # analysed members coloured by utilisation (stress / allowable)


class Layer(str, Enum):
    SKIN = "skin"
    STRUCTURE = "structure"
    ATTACHMENTS = "attachments"
    SYSTEMS = "systems"  # engines, propellers, landing gear
    CONTROLS = "controls"  # ailerons, flaps, elevators, rudder


COLOURS: dict[CT, tuple[float, float, float]] = {
    CT.FUSELAGE: (0.86, 0.88, 0.91),
    CT.WING: (0.70, 0.76, 0.84),
    CT.HORIZONTAL_TAIL: (0.70, 0.76, 0.84),
    CT.VERTICAL_TAIL: (0.70, 0.76, 0.84),
    CT.NACELLE: (0.46, 0.50, 0.56),
    CT.PYLON: (0.55, 0.58, 0.62),
    CT.PROPELLER: (0.25, 0.27, 0.30),
    CT.GEAR_STRUT: (0.40, 0.42, 0.45),
    CT.WHEEL: (0.12, 0.12, 0.13),
    CT.RIB: (0.93, 0.62, 0.20),
    CT.SPAR: (0.85, 0.25, 0.22),
    CT.FRAME: (0.25, 0.60, 0.85),
    CT.LONGERON: (0.30, 0.75, 0.55),
    CT.ATTACHMENT: (0.85, 0.20, 0.75),
    CT.AILERON: (0.38, 0.62, 0.92),
    CT.FLAP: (0.50, 0.72, 0.62),
    CT.ELEVATOR: (0.38, 0.62, 0.92),
    CT.RUDDER: (0.38, 0.62, 0.92),
    CT.ELEVON: (0.38, 0.62, 0.92),
    CT.RUDDERVATOR: (0.38, 0.62, 0.92),
    CT.DRAG_RUDDER: (0.55, 0.50, 0.85),
    CT.CANARD: (0.70, 0.76, 0.84),
    CT.V_TAIL: (0.70, 0.76, 0.84),
    CT.BOOM: (0.80, 0.82, 0.86),
}
HIGHLIGHT = (1.0, 0.85, 0.1)


def layer_of(c: Component) -> Layer:
    if c.type == CT.ATTACHMENT:
        return Layer.ATTACHMENTS
    if c.is_structure:
        return Layer.STRUCTURE
    if c.type.is_control_surface:
        return Layer.CONTROLS
    if c.type in (CT.NACELLE, CT.PYLON, CT.PROPELLER, CT.GEAR_STRUT, CT.WHEEL):
        return Layer.SYSTEMS
    return Layer.SKIN


def style_for(c: Component) -> tuple[float, float, float, float]:
    """Base RGBA (used for STEP colours too)."""
    return (*COLOURS.get(c.type, (0.7, 0.7, 0.7)), 1.0)


@dataclass(frozen=True)
class Appearance:
    visible: bool
    colour: tuple[float, float, float]
    opacity: float
    wireframe: bool


UTIL_STOPS = ((0.0, (0.20, 0.35, 0.85)), (0.5, (0.20, 0.70, 0.45)), (0.8, (0.95, 0.85, 0.20)),
              (1.0, (0.95, 0.45, 0.15)), (1.2, (0.85, 0.10, 0.10)))


def utilisation_colour(u: float) -> tuple[float, float, float]:
    """Blue (lightly loaded) -> green -> yellow -> orange (at allowable) -> red (over)."""
    u = max(0.0, float(u))
    for (u0, c0), (u1, c1) in zip(UTIL_STOPS[:-1], UTIL_STOPS[1:]):
        if u <= u1:
            f = (u - u0) / (u1 - u0)
            return tuple(a + f * (b - a) for a, b in zip(c0, c1))
    return UTIL_STOPS[-1][1]


def appearance(c: Component, mode: ViewMode, layers: set[Layer], utilisation: float | None = None) -> Appearance:
    layer = layer_of(c)
    rgb = COLOURS.get(c.type, (0.7, 0.7, 0.7))
    if layer not in layers:
        return Appearance(False, rgb, 1.0, False)
    if mode == ViewMode.STRESS:
        if utilisation is not None and c.type not in (CT.WING, CT.FUSELAGE):
            return Appearance(True, utilisation_colour(utilisation), 1.0, False)
        if layer == Layer.SKIN or c.type in (CT.WING, CT.FUSELAGE):
            return Appearance(True, (0.75, 0.78, 0.82), 0.07, False)
        return Appearance(True, (0.55, 0.57, 0.60), 0.35, False)  # not analysed
    structural = layer in (Layer.STRUCTURE, Layer.ATTACHMENTS)
    if mode == ViewMode.SOLID:
        # structure is enclosed by skins; hide it unless skins are switched off
        return Appearance(not structural or Layer.SKIN not in layers, rgb, 1.0, False)
    if mode == ViewMode.WIREFRAME:
        return Appearance(True, rgb, 1.0, True)
    if mode == ViewMode.TRANSPARENT:
        return Appearance(True, rgb, 1.0 if structural else 0.22, False)
    # STRUCTURE
    if structural:
        return Appearance(True, rgb, 1.0, False)
    return Appearance(True, rgb, 0.07 if layer == Layer.SKIN else 0.25, False)
