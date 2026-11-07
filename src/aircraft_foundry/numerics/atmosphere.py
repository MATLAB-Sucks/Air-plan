"""International Standard Atmosphere (troposphere + lower stratosphere)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

G0 = 9.80665
R_AIR = 287.053
GAMMA = 1.4
T0, P0 = 288.15, 101325.0
RHO0 = P0 / (R_AIR * T0)
LAPSE = -0.0065
H_TROPO = 11000.0


@dataclass(frozen=True)
class AtmosphereState:
    temperature: float  # K
    pressure: float  # Pa
    density: float  # kg/m^3
    speed_of_sound: float  # m/s
    viscosity: float  # Pa s

    @property
    def sigma(self) -> float:
        return self.density / RHO0


def isa(altitude_m: float) -> AtmosphereState:
    h = float(np.clip(altitude_m, -500.0, 20000.0))
    if h <= H_TROPO:
        t = T0 + LAPSE * h
        p = P0 * (t / T0) ** (-G0 / (LAPSE * R_AIR))
    else:
        t11 = T0 + LAPSE * H_TROPO
        p11 = P0 * (t11 / T0) ** (-G0 / (LAPSE * R_AIR))
        t = t11
        p = p11 * np.exp(-G0 * (h - H_TROPO) / (R_AIR * t11))
    rho = p / (R_AIR * t)
    a = np.sqrt(GAMMA * R_AIR * t)
    mu = 1.458e-6 * t**1.5 / (t + 110.4)  # Sutherland
    return AtmosphereState(t, p, rho, a, mu)
