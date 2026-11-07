"""NACA 4-digit airfoils with the standard (slightly blunt) trailing edge.

The finite trailing edge (~0.25 % chord) is deliberate: knife-edge trailing
edges produce self-intersecting lofted B-rep faces wherever thickness varies
along the span, which breaks STEP export.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

_A = (0.2969, -0.1260, -0.3516, 0.2843, -0.1015)  # standard open trailing edge


@dataclass(frozen=True)
class NacaAirfoil:
    camber: float = 0.02  # m: maximum camber / chord
    camber_pos: float = 0.4  # p: chordwise position of max camber
    thickness: float = 0.12  # t: maximum thickness / chord

    def thickness_dist(self, x: np.ndarray) -> np.ndarray:
        x = np.clip(x, 0.0, 1.0)
        a0, a1, a2, a3, a4 = _A
        return 5 * self.thickness * (a0 * np.sqrt(x) + a1 * x + a2 * x**2 + a3 * x**3 + a4 * x**4)

    def camber_line(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        m, p = self.camber, self.camber_pos
        x = np.asarray(x, dtype=float)
        if m <= 0 or p <= 0:
            return np.zeros_like(x), np.zeros_like(x)
        fwd = x < p
        yc = np.where(fwd, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x**2))
        dyc = np.where(fwd, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
        return yc, dyc

    def surfaces(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return (xu, zu, xl, zl) for chord stations x (fraction of chord)."""
        yt = self.thickness_dist(x)
        yc, dyc = self.camber_line(x)
        th = np.arctan(dyc)
        return x - yt * np.sin(th), yc + yt * np.cos(th), x + yt * np.sin(th), yc - yt * np.cos(th)

    def z_upper_lower(self, x: float) -> tuple[float, float]:
        """Approximate upper/lower surface heights at chord fraction x."""
        yt = float(self.thickness_dist(np.array(x)))
        yc, _ = self.camber_line(np.array(x))
        return float(yc) + yt, float(yc) - yt

    def loop(self, n_half: int = 40) -> np.ndarray:
        return _loop(self.camber, self.camber_pos, self.thickness, n_half)

    def fore_loop(self, x_cut: float, n_half: int = 40) -> np.ndarray:
        """Section ahead of a hinge line at chord fraction ``x_cut``.

        Same layout as :meth:`loop` (upper cut point first, leading edge at n//2,
        lower cut point last, straight closing edge), so it lofts like an airfoil.
        """
        return _fore(self.camber, self.camber_pos, self.thickness, round(x_cut, 6), n_half)

    def aft_loop(self, x_cut: float, n_side: int = 12) -> tuple[np.ndarray, tuple[int, ...]]:
        """Control-surface section aft of ``x_cut``: (points, corner indices).

        Points run upper-hinge -> upper-TE, lower-TE -> lower-hinge; corners are
        the four ends, joined by the TE base and the hinge face.
        """
        beta = np.linspace(0.0, np.pi / 2, n_side + 1)
        x = x_cut + (1 - x_cut) * np.sin(beta) ** 1.2
        _, zu, _, zl = self.surfaces(x)
        upper = np.column_stack([x, zu])
        lower = np.column_stack([x[::-1], zl[::-1]])
        pts = np.vstack([upper, lower])
        return pts, (0, n_side, n_side + 1, 2 * n_side + 1)

    def box_loop(self, x_front: float, x_rear: float, n_half: int = 12) -> np.ndarray:
        """Closed (x, z) contour of the section between two chord fractions.

        Used for rib webs bounded by the front and rear spars.
        """
        x = np.linspace(x_front, x_rear, n_half)
        _, zu, _, zl = self.surfaces(x)
        upper = np.column_stack([x[::-1], zu[::-1]])
        lower = np.column_stack([x, zl])
        return np.vstack([upper, lower])


@lru_cache(maxsize=512)
def _fore(m: float, p: float, t: float, x_cut: float, n_half: int) -> np.ndarray:
    af = NacaAirfoil(m, p, t)
    beta = np.linspace(0.0, np.pi, n_half + 1)
    x = x_cut * 0.5 * (1 - np.cos(beta))
    xu, zu, xl, zl = af.surfaces(x)
    upper = np.column_stack([xu[::-1], zu[::-1]])
    lower = np.column_stack([xl[1:], zl[1:]])
    pts = np.vstack([upper, lower])
    pts.setflags(write=False)
    return pts


@lru_cache(maxsize=256)
def _loop(m: float, p: float, t: float, n_half: int) -> np.ndarray:
    af = NacaAirfoil(m, p, t)
    beta = np.linspace(0.0, np.pi, n_half + 1)
    x = 0.5 * (1 - np.cos(beta))  # cosine spacing, x = 0..1
    xu, zu, xl, zl = af.surfaces(x)
    # upper TE -> upper surface -> LE (index n_half) -> lower surface -> lower TE.
    # The loop closes with the short trailing-edge base from the last point to point 0.
    upper = np.column_stack([xu[::-1], zu[::-1]])  # n_half + 1 points, ends at LE
    lower = np.column_stack([xl[1:], zl[1:]])  # n_half points, ends at lower TE
    pts = np.vstack([upper, lower])
    pts.setflags(write=False)
    return pts
