"""Statistical group weights (Raymer, cargo/transport equations 15.25-15.50).

Raymer's equations are in imperial units; every function here takes and
returns SI (kg, m, m^2, m/s) and converts internally.
"""
from __future__ import annotations

import numpy as np

KG2LB, LB2KG = 2.20462, 0.453592
M2FT, M22FT2 = 3.28084, 10.7639
M2IN = 39.3701
MS2KT = 1.94384


def wing_weight(wdg_kg: float, nz: float, area: float, ar: float, tc: float, taper: float,
                sweep_qc: float, control_area: float | None = None) -> float:
    """``control_area``: wing-mounted control surfaces incl. high-lift devices (m^2)."""
    scs = (0.25 * area if control_area is None else max(control_area, 0.02 * area)) * M22FT2
    lb = (0.0051 * (wdg_kg * KG2LB * nz) ** 0.557 * (area * M22FT2) ** 0.649 * ar**0.5 * tc**-0.4
          * (1 + taper) ** 0.1 / np.cos(sweep_qc) * scs**0.1)
    return lb * LB2KG


def htail_weight(wdg_kg: float, nz: float, area: float, arm: float, ar: float, sweep_qc: float,
                 fuselage_width_ratio: float = 0.1) -> float:
    lt = max(arm, 0.5) * M2FT
    lb = (0.0379 * (1 + fuselage_width_ratio) ** -0.25 * (wdg_kg * KG2LB) ** 0.639 * nz**0.1
          * (area * M22FT2) ** 0.75 / lt * (0.3 * lt) ** 0.704 / np.cos(sweep_qc) * ar**0.166 * 1.15**0.1)
    return lb * LB2KG


def vtail_weight(wdg_kg: float, nz: float, area: float, arm: float, ar: float, sweep_qc: float, tc: float,
                 t_tail: bool) -> float:
    tc = max(tc, 0.08)  # Raymer's transport fit is not valid for very thin (supersonic) sections
    lt = max(arm, 0.5) * M2FT
    lb = (0.0026 * (1 + (1.0 if t_tail else 0.0)) ** 0.225 * (wdg_kg * KG2LB) ** 0.556 * nz**0.536
          * lt**-0.5 * (area * M22FT2) ** 0.5 * lt**0.875 / np.cos(sweep_qc) * ar**0.35 * tc**-0.5)
    return lb * LB2KG


def fuselage_weight(wdg_kg: float, nz: float, length: float, diameter: float, wetted: float, span: float,
                    taper: float, sweep_qc: float, fuselage_gear: bool) -> float:
    kws = 0.75 * ((1 + 2 * taper) / (1 + taper)) * (span * np.tan(sweep_qc) / length)
    kws = max(kws, -0.9)  # forward-swept quarter-chord lines would make (1 + Kws) negative
    lb = (0.3280 * 1.06 * (1.12 if fuselage_gear else 1.0) * (wdg_kg * KG2LB * nz) ** 0.5
          * (length * M2FT) ** 0.25 * (wetted * M22FT2) ** 0.302 * (1 + kws) ** 0.04
          * (length / diameter) ** 0.10)
    return lb * LB2KG


def main_gear_weight(wl_kg: float, strut_m: float, wheels: int, stall_ms: float) -> float:
    lb = (0.0106 * 1.0 * (wl_kg * KG2LB) ** 0.888 * 4.5**0.25 * (max(strut_m, 0.3) * M2IN) ** 0.4
          * max(2 * wheels, 1) ** 0.321 * 2**-0.5 * (stall_ms * MS2KT) ** 0.1)
    return lb * LB2KG


def nose_gear_weight(wl_kg: float, strut_m: float, wheels: int) -> float:
    lb = 0.032 * (wl_kg * KG2LB) ** 0.646 * 4.5**0.2 * (max(strut_m, 0.3) * M2IN) ** 0.5 * wheels**0.45
    return lb * LB2KG


def engine_dry_weight(thrust_kn: float, propulsion: str) -> float:
    if propulsion == "turboprop":
        return 18.0 * thrust_kn
    # Power-law fit through high-bypass turbofans from ~120 kN (2.3 t) to
    # ~510 kN (8.3 t).  Raymer's eq. 10.4 (exponent 1.1) overestimates large engines.
    return 2300.0 * (thrust_kn / 120.0) ** 0.88


def furnishings_and_operator_items(passengers: int) -> float:
    """Seats, galleys, lavatories, emergency & oxygen equipment, operator items.

    Not covered by Raymer's 'all-else empty' fraction; ~42 kg per seat (plus the
    separately modelled ~1.5 kg/m^2 cabin acoustic insulation) reproduces published
    operating-empty weights of narrow-body transports.
    """
    return 42.0 * passengers


def systems_and_equipment(w0_kg: float) -> float:
    """Systems, avionics, hydraulics, electrics, APU, air-conditioning.

    Raymer's flat 0.17 W0 'all-else' fraction fits narrow-bodies but
    overestimates wide-bodies by ~2x; this power law gives ~0.17 W0 at 78 t
    and ~0.12 W0 at 350 t, consistent with published group weights.
    """
    return 13300.0 * (w0_kg / 78000.0) ** 0.75


# ---------------------------------------------------------------------------------------------
# General-aviation equations (Raymer 15.46-15.58) for light aircraft (stunt planes, light props).
# Imperial inside, SI outside.  q is cruise dynamic pressure (Pa).

def _psf(q_pa: float) -> float:
    return q_pa * 0.0208854


def ga_wing_weight(wdg_kg, nz, area, ar, tc, taper, sweep_qc, q_pa, wing_fuel_kg) -> float:
    c = np.cos(sweep_qc)
    lb = (0.036 * (area * M22FT2) ** 0.758 * max(wing_fuel_kg * KG2LB, 1.0) ** 0.0035 * (ar / c ** 2) ** 0.6
          * _psf(q_pa) ** 0.006 * max(taper, 0.05) ** 0.04 * (100 * tc / c) ** -0.3 * (nz * wdg_kg * KG2LB) ** 0.49)
    return lb * LB2KG


def ga_htail_weight(wdg_kg, nz, area, ar, sweep_qc, tc, taper, q_pa) -> float:
    c = np.cos(sweep_qc)
    lb = (0.016 * (nz * wdg_kg * KG2LB) ** 0.414 * _psf(q_pa) ** 0.168 * (area * M22FT2) ** 0.896
          * (100 * tc / c) ** -0.12 * (ar / c ** 2) ** 0.043 * max(taper, 0.05) ** -0.02)
    return lb * LB2KG


def ga_vtail_weight(wdg_kg, nz, area, ar, sweep_qc, tc, taper, q_pa, t_tail=False) -> float:
    c = np.cos(sweep_qc)
    lb = (0.073 * (1 + 0.2 * (1.0 if t_tail else 0.0)) * (nz * wdg_kg * KG2LB) ** 0.376 * _psf(q_pa) ** 0.122
          * (area * M22FT2) ** 0.873 * (100 * tc / c) ** -0.49 * (ar / c ** 2) ** 0.357 * max(taper, 0.05) ** 0.039)
    return lb * LB2KG


def ga_fuselage_weight(wdg_kg, nz, wetted, tail_arm, length, depth, q_pa) -> float:
    lb = (0.052 * (wetted * M22FT2) ** 1.086 * (nz * wdg_kg * KG2LB) ** 0.177 * (tail_arm * M2FT) ** -0.051
          * (length / max(depth, 0.3)) ** -0.072 * _psf(q_pa) ** 0.241)
    return lb * LB2KG


def ga_gear_weight(wl_kg, main_strut_m, nose_strut_m, n_land=4.5) -> tuple[float, float]:
    main = 0.095 * (n_land * wl_kg * KG2LB) ** 0.768 * (main_strut_m * M2IN / 12) ** 0.409
    nose = 0.125 * (n_land * wl_kg * KG2LB) ** 0.566 * (nose_strut_m * M2IN / 12) ** 0.845
    return main * LB2KG, nose * LB2KG


def ga_systems_weight(wdg_kg, nz, fuel_kg, length, span, n_engines, avionics_kg=18.0) -> float:
    """Fuel system, flight controls, hydraulics, avionics and electrical (light aircraft)."""
    gal = fuel_kg / 0.8 / 3.785
    fuel_sys = 2.49 * max(gal, 1.0) ** 0.726 * 0.5 ** 0.363 * 2 ** 0.242 * n_engines ** 0.157
    controls = 0.053 * (length * M2FT) ** 1.536 * (span * M2FT) ** 0.371 * (nz * wdg_kg * KG2LB * 1e-4) ** 0.80
    hyd = 0.001 * wdg_kg * KG2LB
    av = 2.117 * (avionics_kg * KG2LB) ** 0.933
    elec = 12.57 * (fuel_sys + av) ** 0.51
    return (fuel_sys + controls + hyd + av + elec) * LB2KG


def ga_furnishings_weight(wdg_kg) -> float:
    return max(0.0, 0.0582 * wdg_kg * KG2LB - 65) * LB2KG
