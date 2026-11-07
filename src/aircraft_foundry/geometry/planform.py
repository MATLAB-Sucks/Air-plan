"""Trapezoidal lifting-surface planforms.

One :class:`LiftingSurface` instance is the single source of truth for a wing
or tail: its outer skin sections, the rib and spar positions, engine pylon
stations and the aerodynamic reference quantities are all computed from it.
Changing the planform therefore moves ribs, spars and attachments with it.

Local frame: origin at the root leading edge.  For a horizontal surface the
span runs along +y (starboard, ``side=+1``) or -y (port, ``side=-1``).  For a
vertical surface the span runs along +z and the airfoil thickness along y.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from .airfoil import NacaAirfoil


@dataclass(frozen=True)
class LiftingSurface:
    span: float  # full span (tip to tip) for symmetric surfaces, height for a fin
    root_chord: float
    taper_ratio: float
    sweep_le_deg: float
    dihedral_deg: float = 0.0
    twist_tip_deg: float = 0.0
    tc_root: float = 0.12
    tc_tip: float = 0.12
    camber: float = 0.0
    camber_pos: float = 0.4
    symmetric: bool = True
    vertical: bool = False
    # Optional crank (planform kink), e.g. a flying wing's deep centre section or a
    # blended-wing-body's cabin.  crank_eta = 0 means a simple trapezoid.
    crank_eta: float = 0.0
    crank_chord_ratio: float = 1.0  # chord at the crank / root chord
    inner_sweep_le_deg: float | None = None  # LE sweep inboard of the crank

    # ------------------------------------------------------------ planform
    @property
    def semi_span(self) -> float:
        return self.span / 2 if self.symmetric else self.span

    @property
    def tip_chord(self) -> float:
        return self.root_chord * self.taper_ratio

    @property
    def cranked(self) -> bool:
        return 0.0 < self.crank_eta < 1.0

    @property
    def crank_chord(self) -> float:
        return self.root_chord * self.crank_chord_ratio

    def _panels(self) -> list[tuple[float, float, float, float]]:
        """(eta0, eta1, chord0, chord1) for each straight-tapered panel."""
        if not self.cranked:
            return [(0.0, 1.0, self.root_chord, self.tip_chord)]
        return [(0.0, self.crank_eta, self.root_chord, self.crank_chord),
                (self.crank_eta, 1.0, self.crank_chord, self.tip_chord)]

    @property
    def area(self) -> float:
        """Reference planform area, both halves for a symmetric surface."""
        a = sum(self.semi_span * (e1 - e0) * (c0 + c1) / 2 for e0, e1, c0, c1 in self._panels())
        return 2 * a if self.symmetric else a

    @property
    def aspect_ratio(self) -> float:
        b = self.span if self.symmetric else self.semi_span
        return b**2 / self.area if self.symmetric else self.semi_span**2 / self.area

    @cached_property
    def _grid(self) -> np.ndarray:
        g = np.linspace(0.0, 1.0, 801)
        return np.union1d(g, [self.crank_eta]) if self.cranked else g

    @cached_property
    def mac(self) -> float:
        if not self.cranked:
            lam = self.taper_ratio
            return 2 / 3 * self.root_chord * (1 + lam + lam**2) / (1 + lam)
        e = self._grid
        c = self.chord(e)
        return float(np.trapezoid(c**2, e) / np.trapezoid(c, e))

    @cached_property
    def mac_eta(self) -> float:
        if not self.cranked:
            lam = self.taper_ratio
            return (1 + 2 * lam) / (3 * (1 + lam))
        e = self._grid
        c = self.chord(e)
        return float(np.trapezoid(c * e, e) / np.trapezoid(c, e))

    def chord(self, eta) -> np.ndarray:
        eta = np.asarray(eta, dtype=float)
        if not self.cranked:
            return self.root_chord * (1 - (1 - self.taper_ratio) * eta)
        ek = self.crank_eta
        inner = self.root_chord + (self.crank_chord - self.root_chord) * eta / ek
        outer = self.crank_chord + (self.tip_chord - self.crank_chord) * (eta - ek) / (1 - ek)
        return np.where(eta <= ek, inner, outer)

    def x_le(self, eta) -> np.ndarray:
        eta = np.asarray(eta, dtype=float)
        t_out = np.tan(np.radians(self.sweep_le_deg))
        if not self.cranked:
            return eta * self.semi_span * t_out
        t_in = np.tan(np.radians(self.inner_sweep_le_deg if self.inner_sweep_le_deg is not None
                                 else self.sweep_le_deg))
        ek, s = self.crank_eta, self.semi_span
        return np.where(eta <= ek, eta * s * t_in, ek * s * t_in + (eta - ek) * s * t_out)

    def lift_offset(self, eta) -> np.ndarray:
        """Out-of-plane offset due to dihedral."""
        return np.asarray(eta, dtype=float) * self.semi_span * np.tan(np.radians(self.dihedral_deg))

    def twist_deg(self, eta) -> np.ndarray:
        return np.asarray(eta, dtype=float) * self.twist_tip_deg

    def tc(self, eta) -> np.ndarray:
        return self.tc_root + (self.tc_tip - self.tc_root) * np.asarray(eta, dtype=float)

    def airfoil(self, eta: float) -> NacaAirfoil:
        return NacaAirfoil(self.camber, self.camber_pos, round(float(self.tc(eta)), 5))

    def sweep_at(self, chord_fraction: float) -> float:
        """Sweep (rad) of the line at a chord fraction (area-weighted over panels if cranked)."""
        tans, weights = [], []
        for (e0, e1, c0, c1) in self._panels():
            span = (e1 - e0) * self.semi_span
            t_le = float(self.x_le(e1) - self.x_le(e0)) / span
            tans.append(t_le - chord_fraction * (c0 - c1) / span)
            weights.append(span * (c0 + c1))
        return float(np.arctan(np.average(tans, weights=weights)))

    @property
    def x_mac_le(self) -> float:
        if not self.cranked:
            return float(self.x_le(self.mac_eta))
        e = self._grid
        c = self.chord(e)
        return float(np.trapezoid(c * self.x_le(e), e) / np.trapezoid(c, e))

    @property
    def x_quarter_mac(self) -> float:
        return self.x_mac_le + 0.25 * self.mac

    # ------------------------------------------------------------ mapping
    def map_section(self, eta: float, pts: np.ndarray, side: int = 1) -> np.ndarray:
        """Map chord-normalised (x, z) airfoil coordinates to 3D local space."""
        c = float(self.chord(eta))
        tw = np.radians(float(self.twist_deg(eta)))
        x = pts[:, 0] * c
        z = pts[:, 1] * c
        xc = 0.25 * c  # twist about the quarter chord, nose-up positive
        xr = xc + (x - xc) * np.cos(tw) + z * np.sin(tw)
        zr = -(x - xc) * np.sin(tw) + z * np.cos(tw)
        s = eta * self.semi_span
        xg = xr + float(self.x_le(eta))
        off = float(self.lift_offset(eta))
        if self.vertical:
            return np.column_stack([xg, -zr + off, np.full_like(xg, s)])
        return np.column_stack([xg, np.full_like(xg, side * s), zr + off])

    def point(self, eta: float, chord_fraction: float, height: float = 0.0, side: int = 1) -> np.ndarray:
        """3D point on the camber-ish mid line (height in chord fractions)."""
        af = self.airfoil(eta)
        zu, zl = af.z_upper_lower(chord_fraction)
        z = 0.5 * (zu + zl) + height
        return self.map_section(eta, np.array([[chord_fraction, z]]), side)[0]

    def surface_point(self, eta: float, chord_fraction: float, upper: bool, side: int = 1) -> np.ndarray:
        zu, zl = self.airfoil(eta).z_upper_lower(chord_fraction)
        return self.map_section(eta, np.array([[chord_fraction, zu if upper else zl]]), side)[0]

    def eta_of(self, spanwise: float) -> float:
        return float(np.clip(spanwise / self.semi_span, 0.0, 1.0))

    def skin_etas(self, n: int = 7) -> np.ndarray:
        # cosine clustering toward the tip where twist/thickness change fastest
        e = np.sin(np.linspace(0, np.pi / 2, n))
        return np.union1d(e, [self.crank_eta]) if self.cranked else e

    def breaks(self) -> list[float]:
        """Spanwise stations where the planform has a kink (skin lofts must split there)."""
        return [self.crank_eta] if self.cranked else []

    def strip_area(self, eta0: float, eta1: float, chord_fraction: float = 1.0) -> float:
        """Planform area of one side between two stations (times a chord fraction)."""
        e = np.linspace(eta0, eta1, 41)
        if self.cranked and eta0 < self.crank_eta < eta1:
            e = np.union1d(e, [self.crank_eta])
        return chord_fraction * self.semi_span * float(np.trapezoid(self.chord(e), e))

    def box_volume(self, front: float, rear: float, eta0: float = 0.0, eta1: float = 1.0,
                   n: int = 40) -> float:
        """Volume of the wing box between two spars (one side), m^3."""
        etas = np.linspace(eta0, eta1, n)
        area = []
        for e in etas:
            af = self.airfoil(e)
            xs = np.linspace(front, rear, 15)
            _, zu, _, zl = af.surfaces(xs)
            area.append(np.trapezoid(zu - zl, xs) * float(self.chord(e)) ** 2)
        return float(np.trapezoid(area, etas * self.semi_span))
