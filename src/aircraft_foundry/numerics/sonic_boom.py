"""Sonic-boom prediction (Whitham far-field theory) and perceived loudness.

The equivalent area A_e(x) = volume area + lift equivalent area
(beta/(2q) times the lift accumulated along the length) drives the Whitham
F-function:  F(y) = 1/(2 pi) int_0^y A_e''(xi) / sqrt(y - xi) dxi.
The far-field N-wave bow-shock overpressure is

    dp = K_r sqrt(p_alt p_ground) 2^(1/4) gamma beta^(1/4) / sqrt(gamma + 1)
         * sqrt(max int_0^y F) * h^(-3/4)

with reflection factor K_r = 1.9.  This is the physics low-boom design
manipulates: a long, slender aircraft whose area and lift build up gradually
(the NASA X-59's long nose, top-mounted engine and carefully shaped area
distribution) has a weaker F-function peak and spreads the pressure rise
into a "thump" instead of a sharp double-crack.

Loudness: an N-wave's perceived level is calibrated to Concorde
(~2 psf -> ~105 PLdB).  Signature shaping (gradual rise instead of a shock)
is credited from the aircraft's geometry, capped at ~15 dB, so that an
X-59-like design approaches NASA's ~75 PLdB design target.  This credit is an
engineering estimate, not a waveform-propagation calculation.

Mach cutoff: in a standard atmosphere the boom does not reach the ground if
the aircraft flies slower than the speed of sound *at the ground*
(about Mach 1.15 at cruise altitude): the shock rays bend back upward.
That is genuinely boomless supersonic flight.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d

from .atmosphere import isa

GAMMA = 1.4
PSF = 47.880259  # Pa per lb/ft^2
REF_PSF, REF_PLDB = 2.0, 105.0  # Concorde N-wave calibration
MAX_SHAPING_CREDIT_DB = 15.0


def mach_cutoff(altitude_m: float, ground_altitude_m: float = 0.0) -> float:
    """Highest flight Mach whose boom does not reach the ground (no wind)."""
    return isa(ground_altitude_m).speed_of_sound / isa(altitude_m).speed_of_sound


def lift_equivalent_area(x: np.ndarray, lift_per_length: np.ndarray, mach: float, altitude_m: float) -> np.ndarray:
    p = isa(altitude_m).pressure
    beta = math.sqrt(max(mach**2 - 1, 1e-6))
    q = 0.5 * GAMMA * p * mach**2
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (lift_per_length[1:] + lift_per_length[:-1]) * np.diff(x))])
    return beta / (2 * q) * cum


def f_matrix(x: np.ndarray) -> np.ndarray:
    """Abel-kernel weights W[j, i] = int over cell i of 1/sqrt(y_j - xi) (depends only on the grid)."""
    y = x[:, None]
    lo = x[None, :-1]
    hi = np.minimum(x[None, 1:], y)
    w = 2 * (np.sqrt(np.maximum(y - lo, 0)) - np.sqrt(np.maximum(y - hi, 0)))
    w[y[:, 0][:, None] <= lo] = 0.0
    return np.hstack([w, np.zeros((len(x), 1))])


def f_function(x: np.ndarray, area: np.ndarray, kernel: np.ndarray | None = None) -> np.ndarray:
    a = gaussian_filter1d(np.asarray(area, float), sigma=max(1.5, len(x) / 150))
    dx = x[1] - x[0]
    a2 = np.gradient(np.gradient(a, dx), dx)
    kernel = f_matrix(x) if kernel is None else kernel
    return kernel @ a2 / (2 * math.pi)


@dataclass
class BoomResult:
    overpressure_pa: float
    overpressure_psf: float
    f: np.ndarray
    x: np.ndarray
    front_loading: float  # share of the F-function "area" generated in the first 10 % of the length
    reaches_ground: bool
    mach_cutoff: float


def n_wave(x: np.ndarray, equivalent_area: np.ndarray, mach: float, altitude_m: float,
           ground_altitude_m: float = 0.0, reflection: float = 1.9, kernel: np.ndarray | None = None) -> BoomResult:
    mc = mach_cutoff(altitude_m, ground_altitude_m)
    f = f_function(x, equivalent_area, kernel)
    integral = np.concatenate([[0.0], np.cumsum(0.5 * (f[1:] + f[:-1]) * np.diff(x))])
    i_max = max(float(integral.max()), 0.0)
    k = max(1, len(x) // 10)
    front = float(max(integral[:k].max(), 0.0) / i_max) if i_max > 0 else 0.0
    if mach <= 1.0 or mach <= mc:
        return BoomResult(0.0, 0.0, f, x, front, False, mc)
    beta = math.sqrt(mach**2 - 1)
    h = max(altitude_m - ground_altitude_m, 100.0)
    p_eff = math.sqrt(isa(altitude_m).pressure * isa(ground_altitude_m).pressure)
    dp = reflection * p_eff * 2 ** 0.25 * GAMMA * beta ** 0.25 / math.sqrt(GAMMA + 1) * math.sqrt(i_max) * h ** -0.75
    return BoomResult(dp, dp / PSF, f, x, front, True, mc)


def shaping_credit_db(nose_fineness: float, engine_on_top: bool, front_loading: float) -> float:
    """Loudness reduction from signature shaping (engineering estimate).

    nose_fineness: nose length / max fuselage diameter (X-59 ~ 8-10, Concorde ~ 3.5)
    engine_on_top: inlet/nozzle shielded from the ground by the wing/fuselage (X-59)
    front_loading: share of the F-function built up in the first 10 % of the length
    """
    nose = float(np.clip((nose_fineness - 3.0) / 6.0, 0.0, 1.0))
    smooth = float(np.clip(1.0 - 2.5 * front_loading, 0.0, 1.0))
    shield = 1.0 if engine_on_top else 0.6
    return MAX_SHAPING_CREDIT_DB * nose * (0.4 + 0.6 * smooth) * shield


def perceived_level_db(overpressure_psf: float, shaping_db: float = 0.0) -> float:
    """Perceived level (PLdB) of the boom on the ground; 0 when no boom reaches the ground."""
    if overpressure_psf <= 0:
        return 0.0
    return REF_PLDB + 20 * math.log10(overpressure_psf / REF_PSF) - shaping_db
