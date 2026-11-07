"""Supersonic and transonic aerodynamics.

* Volume wave drag from the aircraft's actual normal-area distribution using
  the von Karman slender-body integral in Fourier form:
  x = l/2 (1 - cos t),  S'(t) = sum A_n sin(n t),  D/q = (pi/4) sum n A_n^2.
  For a Sears-Haack body this reproduces D/q = 4.5 pi (S_max/l)^2 (tested).
  Above Mach 1.2 Raymer's Mach/sweep correction (eq. 12.48) is applied.
* Supersonic lift slope and drag-due-to-lift (Raymer eq. 12.6, 12.52).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import gaussian_filter1d


# The normal-cut area rule (Mach 1) misses oblique Mach-plane cuts, wing-body interference and
# intake spillage.  One factor stands in for them.  Calibration: at 1.8 a hand-built Concorde shape
# gave L/D 8.4 and generated Concorde-class shapes ~6.4 (published Concorde ~7.3); 1.5 splits the two.
WAVE_DRAG_REALISM = 1.5


def area_rule_wave_drag_area(x: np.ndarray, area: np.ndarray, n_terms: int = 40) -> float:
    """D/q (m^2) at Mach 1 for a normal-area distribution S(x) on a uniform grid."""
    x = np.asarray(x, float)
    s = gaussian_filter1d(np.asarray(area, float), sigma=max(1.0, len(x) / 200))
    length = x[-1] - x[0]
    theta = np.linspace(1e-4, math.pi - 1e-4, 721)
    xt = x[0] + 0.5 * length * (1 - np.cos(theta))
    ds = np.interp(xt, x, np.gradient(s, x))
    n = np.arange(1, n_terms + 1)
    coeffs = (2 / math.pi) * np.trapezoid(ds[None, :] * np.sin(n[:, None] * theta[None, :]), theta, axis=1)
    return float(math.pi / 4 * np.sum(n * coeffs**2))


def sears_haack_wave_drag_area(s_max: float, length: float) -> float:
    return 4.5 * math.pi * (s_max / length) ** 2


def mach_sweep_factor(mach: float, sweep_le_deg: float) -> float:
    """Raymer eq. 12.48 correction of the Mach-1 area-rule drag for M > 1.2."""
    if mach <= 1.2:
        return 1.0
    return max(0.3, 1 - 0.2 * (mach - 1.2) ** 0.57 * (1 - math.pi * sweep_le_deg ** 0.77 / 100))


def lift_slope_supersonic(ar: float, mach: float) -> float:
    beta = math.sqrt(max(mach**2 - 1, 1e-4))
    return min(4.0 / beta, math.pi * ar / 2.0)  # 2D supersonic limit vs slender-wing limit


def drag_due_to_lift_supersonic(ar: float, mach: float, sweep_le_deg: float) -> float:
    beta = math.sqrt(max(mach**2 - 1, 1e-4))
    return ar * (mach**2 - 1) * math.cos(math.radians(sweep_le_deg)) / max(4 * ar * beta - 2, 0.5)


def mach_cone_half_angle_deg(mach: float) -> float:
    return math.degrees(math.asin(1.0 / mach)) if mach > 1 else 90.0
