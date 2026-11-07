"""Engine models that depend on bypass ratio (BPR).

Bypass ratio is the main trade between a quiet, efficient subsonic engine
(high BPR, big fan) and a slim, supersonic-capable engine (low BPR, fast jet).
All fits are conceptual and checked against public data:
CFM56 (BPR ~5, 120 kN, fan ~1.7 m), Olympus 593 (BPR 0, 169 kN, ~1.2 m),
Concorde cruise TSFC ~1.2 /h at Mach 2.
"""
from __future__ import annotations

import math


def specific_thrust(bpr: float) -> float:
    """Static specific thrust (N per kg/s of air) ~ mixed jet velocity at take-off, m/s."""
    return 1050.0 / (1.0 + max(bpr, 0.0)) ** 0.7


def engine_dimensions(thrust_kn: float, propulsion: str, bpr: float = 5.0) -> dict:
    t = max(thrust_kn, 1.0)
    if propulsion == "turboprop":
        d = 0.19 * math.sqrt(t)
        return {"diameter": d, "length": 3.2 * d, "prop_diameter": 0.65 * math.sqrt(t)}
    mass_flow = t * 1000.0 / specific_thrust(bpr)
    d = 0.1015 * math.sqrt(mass_flow)
    return {"diameter": d, "length": d * (1.9 + 0.8 / (1.0 + bpr)), "prop_diameter": 0.0}


def tsfc_per_hour(mach: float, propulsion: str, bpr: float = 5.0) -> float:
    """Installed thrust-specific fuel consumption, lb/(lbf h) = kg/(kgf h)."""
    if propulsion == "turboprop":
        return 0.45
    c08 = 0.45 + 0.45 / (1.0 + bpr) ** 0.75
    return c08 * (1.0 + 0.22 * (mach - 0.8) / (1.0 + 0.15 * bpr))


def thrust_lapse(sigma: float, mach: float, propulsion: str, bpr: float = 5.0) -> float:
    """Available thrust / sea-level static thrust.  Low-BPR engines gain ram thrust with Mach."""
    if propulsion == "turboprop":
        return sigma ** 0.9 * max(0.1, 1 - 0.45 * mach)
    # ram compression lifts low-bypass thrust with Mach (Olympus 593 keeps ~1/3 of its static
    # thrust at Mach 2 / 17 km); high-bypass fans lose thrust with speed (k = -0.25 at BPR 5)
    k = max(0.4 - 0.15 * bpr, -0.25)
    return sigma ** (0.7 + 0.01 * bpr) * max(0.15, 1.0 + k * mach)


def jet_velocity(bpr: float, flight_speed: float = 0.0, throttle: float = 1.0) -> float:
    return flight_speed + throttle * specific_thrust(bpr)
