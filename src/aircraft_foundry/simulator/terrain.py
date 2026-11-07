"""Procedural flight-test world (deterministic, no external data).

A 120 km x 120 km region with four airports:

* **Foundry Field (FFD)** - home base, 3.5 km runway heading 360, sea level;
* **Lakeside (LKS)** - 2.8 km runway heading 090 beside a large lake;
* **Coastal (CST)** - 3.0 km runway heading 180 near the western sea coast;
* **Highland (HLD)** - 2.4 km runway heading 045 in a mountain valley at 650 m
  (thinner air, terrain all around: a real test of a design).

Around them: a western sea coast with beaches, rolling farmland, a high
mountain range to the north-east (peaks ~2 000 m, snow), southern hills,
lakes, a river running from the mountains to the sea, forests, and towns.
Approach corridors are kept clear for every runway direction that has one.

The same height field drives the physics (wheels roll on it, hitting it is a
crash) and the renderer.  Coordinates are north / east metres from Foundry
Field's runway start; heights are metres above sea level.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates, zoom

WATER_LEVEL = -4.0


@dataclass(frozen=True)
class Runway:
    code: str
    name: str
    north: float  # threshold (start of the landing direction)
    east: float
    heading_deg: float
    length: float
    width: float
    elevation: float
    approach_both_ends: bool = True

    @property
    def dir(self) -> np.ndarray:  # unit (north, east) along the landing direction
        h = math.radians(self.heading_deg)
        return np.array([math.cos(h), math.sin(h)])

    @property
    def perp(self) -> np.ndarray:  # unit (north, east) to the right of the landing direction
        h = math.radians(self.heading_deg)
        return np.array([-math.sin(h), math.cos(h)])

    def local(self, north: float, east: float) -> tuple[float, float]:
        """(distance along the runway from the threshold, offset to the right)."""
        d = np.array([north - self.north, east - self.east])
        return float(d @ self.dir), float(d @ self.perp)

    def point(self, along: float, right: float = 0.0) -> tuple[float, float]:
        p = np.array([self.north, self.east]) + along * self.dir + right * self.perp
        return float(p[0]), float(p[1])

    def contains(self, north: float, east: float, margin: float = 0.0) -> bool:
        a, r = self.local(north, east)
        return -margin <= a <= self.length + margin and abs(r) <= self.width / 2 + margin

    @property
    def aim_point(self) -> float:
        return 300.0  # metres past the threshold, abeam the PAPI

    @property
    def half_width(self) -> float:
        return self.width / 2

    def reciprocal(self) -> "Runway":
        """The same strip used in the opposite direction (e.g. departures from a one-way mountain strip)."""
        n, e = self.point(self.length)
        return Runway(self.code, self.name, n, e, (self.heading_deg + 180.0) % 360.0, self.length, self.width,
                      self.elevation, self.approach_both_ends)

    def departure(self) -> "Runway":
        """Direction to take off in: one-way strips (terrain beyond the far end) depart the other way."""
        return self if self.approach_both_ends else self.reciprocal()


RUNWAYS: tuple[Runway, ...] = (
    Runway("FFD", "Foundry Field", -300.0, 0.0, 0.0, 3500.0, 50.0, 0.0),
    Runway("LKS", "Lakeside", -24000.0, -12000.0, 90.0, 2800.0, 45.0, 0.0),
    Runway("CST", "Coastal", 9000.0, -36000.0, 180.0, 3000.0, 45.0, 0.0),
    Runway("HLD", "Highland", 36000.0, 20000.0, 45.0, 2400.0, 40.0, 650.0, approach_both_ends=False),
)
HOME = RUNWAYS[0]
# backwards-compatible description of the home runway (heading 360)
RUNWAY = {"north0": HOME.north, "north1": HOME.north + HOME.length, "half_width": HOME.half_width}


def runway_by_code(code: str) -> Runway:
    return next(r for r in RUNWAYS if r.code == code)


def _octave_noise(rng: np.random.Generator, n: int, octaves: int = 5, base_cells: int = 4) -> np.ndarray:
    out = np.zeros((n, n))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = base_cells * 2 ** o
        coarse = rng.standard_normal((cells + 1, cells + 1))
        out += amp * zoom(coarse, n / (cells + 1), order=3)[:n, :n]
        total += amp
        amp *= 0.5
    return out / total


@dataclass(frozen=True)
class Terrain:
    size: float = 120000.0
    resolution: int = 481  # 250 m spacing
    seed: int = 7
    north_c: float = 6000.0
    east_c: float = -6000.0

    @cached_property
    def grid(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = self.resolution
        north = self.north_c + np.linspace(-self.size / 2, self.size / 2, n)
        east = self.east_c + np.linspace(-self.size / 2, self.size / 2, n)
        N, E = np.meshgrid(north, east, indexing="ij")
        rng = np.random.default_rng(self.seed)
        rolling = 80 * _octave_noise(rng, n, 6, 6)
        ridges = np.abs(_octave_noise(rng, n, 7, 6))
        h = 60 + rolling
        # north-east mountain range and southern hills
        d1 = np.hypot((N - 42000) / 20000, (E - 26000) / 16000)
        h += 2100 * np.clip(1.1 - d1, 0, 1) ** 1.5 * (0.4 + 1.2 * ridges)
        d2 = np.hypot((N + 40000) / 14000, (E - 22000) / 18000)
        h += 650 * np.clip(1.0 - d2, 0, 1) ** 1.3 * (0.5 + ridges)
        # western sea with an irregular coast, beaches sloping into it
        coast = -40000 + 5000 * _octave_noise(rng, n, 4, 3)
        h = np.where(E < coast, np.minimum(h, -30 - 0.004 * (coast - E)), h)
        beach = np.clip((E - coast) / 6000, 0, 1)
        h = np.where(E >= coast, h * beach + (1 - beach) * 2, h)
        # lakes and a river from the mountains to the sea
        h -= 160 * np.exp(-(((N + 20000) / 5000) ** 2 + ((E + 20000) / 4500) ** 2))
        h -= 90 * np.exp(-(((N - 18000) / 2500) ** 2 + ((E + 4000) / 3000) ** 2))
        t = np.linspace(0, 1, 400)
        pts = np.column_stack([30000 - 34000 * t + 4000 * np.sin(6 * t), 15000 - 52000 * t + 3000 * np.sin(9 * t)])
        step = max(1, n // 120)
        Nc, Ec = N[::step, ::step], E[::step, ::step]
        coarse_d = np.full(Nc.shape, 1e9)
        for pn, pe in pts[::2]:
            coarse_d = np.minimum(coarse_d, np.hypot(Nc - pn, Ec - pe))
        river_d = zoom(coarse_d, (n / coarse_d.shape[0], n / coarse_d.shape[1]), order=1)[:n, :n]
        h -= 80 * np.exp(-(river_d / 600) ** 2)
        h = gaussian_filter(h, 0.7)
        # airports: level platforms with blended edges and clear approach corridors
        for rw in RUNWAYS:
            along = (N - rw.north) * rw.dir[0] + (E - rw.east) * rw.dir[1]
            right = (N - rw.north) * rw.perp[0] + (E - rw.east) * rw.perp[1]
            mid = rw.length / 2
            d = np.hypot(np.clip(np.abs(along - mid) - mid - 400, 0, None), np.clip(np.abs(right) - 500, 0, None))
            w = np.clip(1 - d / 1800, 0, 1)
            w = w * w * (3 - 2 * w)
            h = h * (1 - w) + rw.elevation * w
            ends = ((-1, 0.0), (1, rw.length)) if rw.approach_both_ends else ((-1, 0.0),)
            for sign, start in ends:
                dist = sign * (along - start)  # distance out along the approach
                # full-strength corridor ~2 km either side of the centreline out to 12 km, fading by 16 km;
                # terrain is cut to a 1.5 deg ceiling so a 3 deg approach keeps >= half its height as clearance
                weight = (dist > -200) * np.clip(1.6 * np.exp(-(right / 2000) ** 2) - 0.3, 0, 1) \
                    * np.clip((16000 - dist) / 4000, 0, 1)
                ceiling = rw.elevation + np.tan(math.radians(1.5)) * np.clip(dist, 0, None)
                h = h - weight * np.clip(h - ceiling, 0, None)
            flat = (np.abs(along - mid) < mid + 300) & (np.abs(right) < 300)
            h[flat] = rw.elevation
        return north, east, h

    def height(self, north, east) -> np.ndarray:
        nn, ee, h = self.grid
        north, east = np.asarray(north, float), np.asarray(east, float)
        i = (north - nn[0]) / (nn[1] - nn[0])
        j = (east - ee[0]) / (ee[1] - ee[0])
        return map_coordinates(h, [np.atleast_1d(i), np.atleast_1d(j)], order=1, mode="nearest").reshape(north.shape)

    def height_at(self, north: float, east: float) -> float:
        nn, ee, h = self.grid
        i = (north - nn[0]) / (nn[1] - nn[0])
        j = (east - ee[0]) / (ee[1] - ee[0])
        if not (0 <= i < len(nn) - 1 and 0 <= j < len(ee) - 1):
            return WATER_LEVEL - 50.0  # open sea beyond the map
        i0, j0 = int(i), int(j)
        fi, fj = i - i0, j - j0
        return float((h[i0, j0] * (1 - fi) + h[i0 + 1, j0] * fi) * (1 - fj)
                     + (h[i0, j0 + 1] * (1 - fi) + h[i0 + 1, j0 + 1] * fi) * fj)

    def surface_height(self, north: float, east: float) -> float:
        return max(self.height_at(north, east), WATER_LEVEL)

    def colours(self) -> np.ndarray:
        _, _, h = self.grid
        rng = np.random.default_rng(self.seed + 1)
        n = h.shape[0]
        gy, gx = np.gradient(h, 250.0)
        slope = np.hypot(gx, gy)
        field = _octave_noise(rng, n, 5, 8) > 0.0
        forest = _octave_noise(rng, n, 4, 5) > 0.35
        c = np.where(field[..., None], np.array([112, 140, 74]), np.array([150, 150, 88])).astype(float)
        c[(forest & (h > 20)) | (h > 250) | (slope > 0.12)] = [58, 92, 52]
        c[(h > 1000) | (slope > 0.4)] = [120, 112, 104]
        c[h > 1650] = [236, 239, 243]
        c[(h > WATER_LEVEL) & (h < 6)] = [212, 198, 150]  # beaches and shores
        c[h <= WATER_LEVEL + 0.5] = [70, 110, 150]
        c[h < -60] = [48, 82, 125]  # deep sea
        shade = np.clip(1.0 - 1.6 * gx, 0.72, 1.18)
        return np.clip(c * shade[..., None], 0, 255).astype(np.uint8)

    def town(self, count: int = 180) -> list[tuple[float, float, float, float, float]]:
        """Buildings (north, east, width, depth, height): a town by each airport and a city."""
        rng = np.random.default_rng(self.seed + 2)
        sites = [(1200, 2300, 1100, 900, count, False), (-24500, -9000, 800, 1200, 70, False),
                 (10500, -33000, 900, 900, 70, False), (36500, 22500, 500, 600, 35, False),
                 (14000, -14000, 2200, 2200, 260, True)]
        out = []
        for cn, ce, rn, re_, k, city in sites:
            for _ in range(k):
                n_, e_ = cn + rng.uniform(-rn, rn), ce + rng.uniform(-re_, re_)
                if self.height_at(n_, e_) < WATER_LEVEL + 1 or any(rw.contains(n_, e_, 350) for rw in RUNWAYS):
                    continue
                w, d = rng.uniform(10, 35), rng.uniform(10, 35)
                if city:
                    ht = rng.choice([6, 9, 12, 15, 25, 40, 70], p=[0.2, 0.2, 0.2, 0.15, 0.12, 0.08, 0.05])
                else:
                    ht = rng.choice([6, 9, 12, 15, 25], p=[0.3, 0.3, 0.2, 0.12, 0.08])
                out.append((n_, e_, w, d, float(ht)))
        return out
