"""Conceptual-design aerodynamics (Raymer, *Aircraft Design: A Conceptual
Approach*, ch. 12 & 16; DATCOM lift-curve slope; Korn drag divergence)."""
from __future__ import annotations

import numpy as np


def skin_friction(reynolds: float, mach: float) -> float:
    """Turbulent flat-plate skin-friction coefficient with compressibility."""
    re = max(reynolds, 1e5)
    return 0.455 / (np.log10(re) ** 2.58 * (1 + 0.144 * mach**2) ** 0.65)


def form_factor_wing(tc: float, mach: float, sweep_max_t: float, x_max_t: float = 0.3) -> float:
    return (1 + 0.6 / x_max_t * tc + 100 * tc**4) * (1.34 * mach**0.18 * np.cos(sweep_max_t) ** 0.28)


def form_factor_body(fineness: float) -> float:
    return 1 + 60 / fineness**3 + fineness / 400


def form_factor_nacelle(fineness: float) -> float:
    return 1 + 0.35 / fineness


def wetted_area_surface(exposed_area: float, tc: float) -> float:
    return exposed_area * (2.003 if tc < 0.05 else 2 * (1 + 0.2 * tc))


def lift_curve_slope(aspect_ratio: float, mach: float, sweep_half_chord: float, efficiency: float = 0.95) -> float:
    """DATCOM / Helmbold finite-wing lift curve slope, per radian."""
    beta2 = max(1 - mach**2, 0.05)
    a = aspect_ratio
    return 2 * np.pi * a / (2 + np.sqrt(4 + a**2 * beta2 / efficiency**2 * (1 + np.tan(sweep_half_chord) ** 2 / beta2)))


def oswald_efficiency(aspect_ratio: float, sweep_le: float) -> float:
    """Whole-aircraft span efficiency, Obert's transport correlation.

    Raymer's swept-wing fit is known to be very pessimistic (e ~ 0.5) for
    high aspect-ratio transports; Obert's fit matches flight data better.
    A mild sweep penalty is applied above 30 deg LE sweep.
    """
    e = 1.0 / (1.05 + 0.007 * np.pi * aspect_ratio)
    excess = max(np.degrees(sweep_le) - 30.0, 0.0)
    return float(np.clip(e * (1 - 0.004 * excess), 0.5, 0.95))


def drag_divergence_mach(tc: float, cl: float, sweep_half_chord: float, kappa: float = 0.87) -> float:
    """Korn equation with Lock's 2nd-order fit (supercritical kappa ~0.95, conventional ~0.87)."""
    c = np.cos(sweep_half_chord)
    return kappa / c - tc / c**2 - cl / (10 * c**3)


def wave_drag(mach: float, mdd: float) -> float:
    mcrit = mdd - (0.1 / 80) ** (1 / 3)
    return 20 * (mach - mcrit) ** 4 if mach > mcrit else 0.0


def upsweep_drag_area(upsweep_rad: float, max_cross_section: float) -> float:
    """Raymer eq. 12.36, D/q in m^2."""
    return 3.83 * abs(upsweep_rad) ** 2.5 * max_cross_section


def fuselage_pitching_moment_factor(wing_quarter_root_fraction: float) -> float:
    """Empirical K_fus (Raymer fig. 16.14, per degree) vs. wing root c/4 station / L."""
    xs = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    ks = np.array([0.005, 0.0075, 0.011, 0.018, 0.028, 0.045])
    return float(np.interp(wing_quarter_root_fraction, xs, ks))


REF_FLAPPED_FRACTION, REF_FLAP_CHORD = 0.61, 0.30


def clmax_landing(sweep_quarter_chord: float, slats: bool = True, flapped_fraction: float = REF_FLAPPED_FRACTION,
                  flap_chord: float = REF_FLAP_CHORD) -> float:
    """Trimmed landing CLmax with slotted flaps (Raymer table 12.2 range).

    The flap increment scales with the flapped-area fraction and with the
    square root of flap chord; at the reference flap layout this gives ~2.85
    with leading-edge slats and ~2.5 without, reduced with quarter-chord sweep.
    """
    clean = 1.85 if slats else 1.50
    flap = 1.0 * (flapped_fraction / REF_FLAPPED_FRACTION) * np.sqrt(max(flap_chord, 0.05) / REF_FLAP_CHORD)
    return float((clean + flap) * np.cos(sweep_quarter_chord) ** 0.7)
