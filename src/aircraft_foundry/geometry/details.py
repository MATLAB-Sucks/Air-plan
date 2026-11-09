"""Surface details that make designs look like aircraft: cabin windows, cockpit glazing (or a canopy on
light aircraft) and dark engine intakes.  Generated from each design's own fuselage and engines, as
thin lofts standing just proud of the skin.  Appearance only: not analysed, not exported to CAD."""
from __future__ import annotations

import math

import numpy as np

from ..core.components import Component
from ..core.components import ComponentType as CT
from .arrangement import Arrangement
from .loft import Loft

PROUD = 1.012  # just outside the skin


def _surface_point(fus, x: float, theta: float, side: int, scale: float = PROUD) -> np.ndarray:
    """A point on the fuselage at station x and angle theta (0 = side, +90 deg = top)."""
    hw = float(fus.half_width(np.array([x]))[0])
    hh = float(fus.half_height(np.array([x]))[0])
    cz = float(fus.centre_z(np.array([x]))[0])
    return np.array([x, side * hw * math.cos(theta) * scale, cz + hh * math.sin(theta) * scale])


def _patch(fus, x0, x1, t0, t1, side, nx=2, nt=6) -> Loft:
    """A thin curved panel on the skin between stations x0..x1 and angles t0..t1."""
    sections = []
    for x in np.linspace(x0, x1, nx):
        outer = [_surface_point(fus, x, t, side, PROUD + 0.006) for t in np.linspace(t0, t1, nt)]
        inner = [_surface_point(fus, x, t, side, PROUD - 0.004) for t in np.linspace(t1, t0, nt)]
        sections.append(np.array(outer + inner))
    return Loft(sections, smooth=False)


def build_details(arr: Arrangement) -> list[Component]:
    comps = []
    fus = arr.fuselage
    L, D = fus.length, fus.diameter
    if arr.layout not in ("flying_wing",) and L > 3.0:
        nose = arr.params.fuselage.nose_fraction * L
        tail = arr.params.fuselage.tail_fraction * L
        if D >= 2.0:  # airliner-style: cockpit windscreen and a row of cabin windows each side
            glass = [_patch(fus, 0.55 * nose, 0.86 * nose, math.radians(8), math.radians(50), s, nx=4, nt=6)
                     for s in (1, -1)]
            comps.append(Component("detail.cockpit", CT.DETAIL, "Cockpit windscreen", glass,
                                   source_groups=("fuselage",)))
            pitch = 0.53 * max(D / 4.0, 0.7)
            w, h = 0.24 * max(D / 4.0, 0.7), 0.34 * max(D / 4.0, 0.7)
            x = nose + 1.2
            wins = []
            while x + w < L - tail - 0.4:
                t_mid = math.radians(16)
                hh = float(fus.half_height(np.array([x]))[0])
                dt = h / max(hh, 0.3) / 2
                for s in (1, -1):
                    wins.append(_patch(fus, x, x + w, t_mid - dt, t_mid + dt, s, nx=2, nt=3))
                x += pitch
            if wins:
                comps.append(Component("detail.windows", CT.DETAIL, f"Cabin windows ({len(wins)})", wins,
                                       source_groups=("fuselage",)))
        else:  # light aircraft: a bubble canopy over the cockpit
            canopy = [_patch(fus, nose + 0.05 * L, nose + 0.22 * L, math.radians(35), math.radians(145), 1,
                             nx=5, nt=10)]
            comps.append(Component("detail.canopy", CT.DETAIL, "Canopy", canopy, source_groups=("fuselage",)))
    intakes = []
    for e in arr.engines:
        if e.prop_diameter > 0:
            continue  # propellers: no visible fan face
        r = 0.42 * e.diameter
        circle = lambda x: np.array([[x, e.inlet[1] + r * math.cos(a), e.inlet[2] + r * math.sin(a)]
                                     for a in np.linspace(0, 2 * math.pi, 24, endpoint=False)])
        intakes.append(Loft([circle(e.inlet[0] - 0.02), circle(e.inlet[0] + 0.01)]))  # just in front of the lip
    if intakes:
        comps.append(Component("detail.intakes", CT.DETAIL, "Engine intakes", intakes,
                               source_groups=("engines",)))
    return comps
