"""Fuselage outer mould line defined by analytic radius / camber laws."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .loft import ellipse_loop

NOSE_TIP_RATIO = 0.03  # tiny nose section keeps the CAD loft non-degenerate
TAIL_END_RATIO = 0.10


@dataclass(frozen=True)
class FuselageShape:
    length: float
    diameter: float
    height_ratio: float = 1.05
    nose_fraction: float = 0.12
    tail_fraction: float = 0.26
    tail_upsweep: float = 0.35
    waist: tuple | None = None  # ((x...), (area scale...)) from area ruling
    nose_profile: str = "elliptic"  # "ogive": area grows from zero slope -> much lower supersonic wave drag

    @property
    def radius(self) -> float:
        return self.diameter / 2

    @property
    def x_nose_end(self) -> float:
        return self.nose_fraction * self.length

    @property
    def x_tail_start(self) -> float:
        return (1 - self.tail_fraction) * self.length

    def _shape(self, x):
        x = np.clip(np.asarray(x, dtype=float), 0.0, self.length)
        ln, lt = self.x_nose_end, self.tail_fraction * self.length
        s_n = np.clip((ln - x) / ln, 0.0, 1.0)  # 1 at nose tip -> 0 at nose end
        s_t = np.clip((x - self.x_tail_start) / lt, 0.0, 1.0)  # 0 -> 1 along tail cone
        power = 0.75 if self.nose_profile == "ogive" else 0.5
        nose = np.clip(1 - s_n**2, 0.0, 1.0) ** power
        nose = NOSE_TIP_RATIO + (1 - NOSE_TIP_RATIO) * nose
        tail = 1 - (1 - TAIL_END_RATIO) * s_t**1.6
        scale = np.where(s_n > 0, nose, np.where(s_t > 0, tail, 1.0))
        if self.waist is not None:  # area ruling scales the cross-section area -> sqrt on dimensions
            scale = scale * np.sqrt(np.interp(x, self.waist[0], self.waist[1]))
        zc = -0.18 * self.radius * s_n**2 + self.tail_upsweep * self.diameter * s_t**2
        return scale, zc

    def half_width(self, x) -> np.ndarray:
        return self.radius * self._shape(x)[0]

    def half_height(self, x) -> np.ndarray:
        return self.radius * self.height_ratio * self._shape(x)[0]

    def centre_z(self, x) -> np.ndarray:
        return self._shape(x)[1]

    def top_z(self, x) -> np.ndarray:
        return self.centre_z(x) + self.half_height(x)

    def bottom_z(self, x) -> np.ndarray:
        return self.centre_z(x) - self.half_height(x)

    def stations(self, n: int = 28) -> np.ndarray:
        u = np.linspace(0.0, 1.0, n)
        # cluster stations toward both ends where curvature is highest
        return self.length * (u - np.sin(2 * np.pi * u) / (2 * np.pi) * 0.85)

    def section(self, x: float, n: int = 40, inset: float = 0.0) -> np.ndarray:
        hw = max(float(self.half_width(x)) - inset, 1e-4)
        hh = max(float(self.half_height(x)) - inset, 1e-4)
        loop = ellipse_loop(n, hw, hh)
        return np.column_stack([np.full(n, x), loop[:, 0], loop[:, 1] + float(self.centre_z(x))])

    @property
    def cabin_length(self) -> float:
        """Usable cabin: constant section plus the usable part of nose and tail cone."""
        return (self.x_tail_start - self.x_nose_end) + 0.45 * self.x_nose_end + 0.35 * self.tail_fraction * self.length

    def wetted_area(self) -> float:
        xs = np.linspace(0, self.length, 200)
        a, b = self.half_width(xs), self.half_height(xs)
        perim = np.pi * (3 * (a + b) - np.sqrt((3 * a + b) * (a + 3 * b)))  # Ramanujan
        return float(np.trapezoid(perim, xs))

    @property
    def volume(self) -> float:
        xs = np.linspace(0, self.length, 200)
        return float(np.trapezoid(np.pi * self.half_width(xs) * self.half_height(xs), xs))
