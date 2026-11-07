"""Conceptual noise estimates for people on the ground and in the cabin.

Airport (take-off flyover) noise: jet-mixing noise power scales as
T * Vj^6 (Lighthill's V^8 law with A_jet ~ T / Vj^2), with spherical
spreading to the flyover microphone 6.5 km from brake release.  Calibrated so
an A320-class aircraft (2 x 120 kN, BPR 5) gives ~85 dB; the same law then
puts Concorde-class engines ~30 dB higher, close to its certified ~119 EPNdB.

Cabin noise: turbulent-boundary-layer pressure (p_rms ~ 0.006 q) plus engine
noise at the fuselage, minus wall transmission loss (mass law at 1 kHz plus
a double-wall/A-weighting constant).  Acoustic insulation adds wall mass,
so a quieter cabin costs weight.
"""
from __future__ import annotations

import math

from .propulsion import specific_thrust

FLYOVER_CAL_DB = -58.0
CABIN_CAL_DB = -17.0
SKIN_AND_TRIM_KG_M2 = 5.8


def flyover_level_db(n_engines: int, thrust_n_each: float, bpr: float, distance_m: float,
                     propulsion: str = "turbofan") -> float:
    if propulsion == "turboprop":
        vj = 220.0  # propeller + core noise behaves like a slow jet in this simplified law
    else:
        vj = specific_thrust(bpr)
    return 10 * math.log10(n_engines * thrust_n_each * vj**6) - 20 * math.log10(max(distance_m, 50.0)) + FLYOVER_CAL_DB


def flyover_distance_m(takeoff_field_m: float, climb_gradient: float) -> float:
    """Height over the 6.5 km flyover point after an all-engines climb."""
    return max((6500.0 - takeoff_field_m) * max(climb_gradient, 0.02), 150.0)


def _tbl_db(q: float, mach: float) -> float:
    """Boundary-layer pressure fluctuation, Lowson: p_rms / q = 0.006 / (1 + 0.14 M^2)."""
    return 20 * math.log10(0.006 / (1 + 0.14 * mach**2) * q / 2e-5)


def cabin_level_dba(q_cruise: float, engine_level_at_wall_db: float, wall_kg_m2: float, mach: float = 0.78) -> float:
    tbl = _tbl_db(q_cruise, mach) + 20 * math.log10(1 + 0.14 * 0.78**2)  # calibration point is Mach 0.78
    exterior = 10 * math.log10(10 ** (tbl / 10) + 10 ** (engine_level_at_wall_db / 10))
    tl = 20 * math.log10(max(wall_kg_m2, 0.5) * 1000.0) - 47.0
    return exterior - tl + CABIN_CAL_DB


def engine_level_at_wall_db(n_engines: int, thrust_n_each: float, bpr: float, distance_m: float,
                            shielded: bool) -> float:
    vrel = 0.5 * specific_thrust(bpr)  # cruise jet velocity relative to the free stream
    lvl = 10 * math.log10(n_engines * thrust_n_each * vrel**6) - 20 * math.log10(max(distance_m, 1.0)) - 60.0
    return lvl - (8.0 if shielded else 0.0)


def insulation_needed_kg_m2(target_dba: float, q_cruise: float, engine_level_db: float, mach: float = 0.78) -> float:
    """Areal insulation mass that brings the cabin down to ``target_dba``."""
    tbl = _tbl_db(q_cruise, mach) + 20 * math.log10(1 + 0.14 * 0.78**2)
    exterior = 10 * math.log10(10 ** (tbl / 10) + 10 ** (engine_level_db / 10))
    tl_needed = exterior + CABIN_CAL_DB - target_dba
    wall = 10 ** ((tl_needed + 47.0) / 20) / 1000.0
    return max(wall - SKIN_AND_TRIM_KG_M2, 0.3)
