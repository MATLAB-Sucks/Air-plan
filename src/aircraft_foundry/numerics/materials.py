"""Conceptual-design material allowables (room temperature, A/B-basis-like
values with typical knockdowns already applied for composites).  SI units."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    name: str
    density: float  # kg/m^3
    E: float  # Young's modulus, Pa
    ftu: float  # ultimate tensile strength, Pa
    fcy: float  # compressive yield strength, Pa
    fsu: float  # ultimate shear strength, Pa
    tension_knockdown: float  # damage-tolerance factor on ftu for ultimate tension sizing
    fatigue_hoop: float  # allowable hoop stress at 1.0 x cabin pressure (fatigue), Pa


MATERIALS: dict[str, Material] = {m.name: m for m in (
    Material("Al 7075-T6", 2810, 71.7e9, 572e6, 503e6, 331e6, 0.75, 95e6),
    Material("Al 2024-T3", 2780, 73.1e9, 483e6, 270e6, 283e6, 0.85, 120e6),
    Material("Al-Li 2099-T83", 2630, 78.0e9, 545e6, 480e6, 320e6, 0.85, 125e6),
    Material("Ti-6Al-4V", 4430, 113.8e9, 950e6, 970e6, 550e6, 0.85, 300e6),
    # quasi-isotropic carbon/epoxy laminate; allowables include barely-visible-impact knockdown
    Material("CFRP quasi-isotropic", 1600, 52.0e9, 450e6, 300e6, 180e6, 1.0, 200e6),
)}

# Standard sheet / plate gauges (mm) used to round required thicknesses up.
GAUGES_MM = (0.5, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.3, 2.5, 2.8, 3.2, 3.6, 4.0, 4.5, 5.0, 5.6, 6.35,
             7.1, 8.0, 9.0, 10.0, 11.0, 12.7, 14.0, 16.0, 19.0, 22.0, 25.4, 30.0, 35.0, 40.0)
MAX_GAUGE_MM = GAUGES_MM[-1]


def material(name: str) -> Material:
    return MATERIALS[name]
