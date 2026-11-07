"""Section-based solid definition shared by the renderer and every exporter.

A :class:`Loft` is an ordered list of closed cross-section loops.  The same
object is triangulated for VTK / STL / OBJ (:func:`loft_to_mesh`) and lofted
into an OpenCascade B-rep for STEP (``geometry.cad.loft_to_solid``), so the
viewport and the CAD export can never drift apart.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .mesh import Mesh


@dataclass
class Loft:
    """A closed solid swept through ordered cross sections.

    sections:        list of (n, 3) arrays; every loop has the same n and a
                     consistent starting point.  Loops must not repeat their
                     first point at the end.
    inner_sections:  optional list of loops (same count) defining a hollow
                     core, e.g. a fuselage frame ring.
    smooth:          CAD hint: interpolate sections with splines (True) or
                     polygons (False, for rectangular structural members).
    sharp_start:     CAD hint: the section is an airfoil loop: point 0 is the
                     upper trailing edge, point n//2 the leading edge and point
                     n-1 the lower trailing edge (upper spline + lower spline +
                     straight trailing-edge base).
    corners:         CAD hint for loops with several corners (control-surface
                     sections): splines between consecutive corner indices,
                     straight lines where two corners are adjacent.
    """

    sections: list[np.ndarray]
    inner_sections: list[np.ndarray] | None = None
    smooth: bool = True
    sharp_start: bool = False
    corners: tuple[int, ...] | None = None  # CAD: indices where the loop has corners

    def __post_init__(self) -> None:
        self.sections = [np.asarray(s, dtype=float) for s in self.sections]
        n = self.sections[0].shape[0]
        if len(self.sections) < 2:
            raise ValueError("a loft needs at least two sections")
        if any(s.shape != (n, 3) for s in self.sections):
            raise ValueError("all loft sections must have the same number of points")
        if self.inner_sections is not None:
            self.inner_sections = [np.asarray(s, dtype=float) for s in self.inner_sections]
            if len(self.inner_sections) != len(self.sections):
                raise ValueError("inner and outer section counts differ")
            if any(s.shape != (n, 3) for s in self.inner_sections):
                raise ValueError("inner sections must match outer point count")

    def fingerprint(self, h: "hashlib._Hash") -> None:
        for s in self.sections:
            h.update(np.round(s, 7).tobytes())
        if self.inner_sections is not None:
            h.update(b"inner")
            for s in self.inner_sections:
                h.update(np.round(s, 7).tobytes())
        h.update(bytes([self.smooth, self.sharp_start]))
        if self.corners:
            h.update(repr(self.corners).encode())


def _side_faces(n_sec: int, n: int, offset: int, reverse: bool) -> np.ndarray:
    i = np.arange(n_sec - 1)[:, None]
    j = np.arange(n)[None, :]
    a = offset + i * n + j
    b = offset + i * n + (j + 1) % n
    c = offset + (i + 1) * n + (j + 1) % n
    d = offset + (i + 1) * n + j
    if reverse:
        t1, t2 = np.stack([a, c, b], -1), np.stack([a, d, c], -1)
    else:
        t1, t2 = np.stack([a, b, c], -1), np.stack([a, c, d], -1)
    return np.concatenate([t1.reshape(-1, 3), t2.reshape(-1, 3)])


def loft_to_mesh(loft: Loft) -> Mesh:
    outer = np.vstack(loft.sections)
    n_sec, n = len(loft.sections), loft.sections[0].shape[0]
    faces = [_side_faces(n_sec, n, 0, reverse=False)]
    j = np.arange(n)
    jn = (j + 1) % n
    if loft.inner_sections is None:
        c0 = len(outer)
        c1 = c0 + 1
        centres = np.vstack([loft.sections[0].mean(axis=0), loft.sections[-1].mean(axis=0)])
        verts = np.vstack([outer, centres])
        last = (n_sec - 1) * n
        faces.append(np.stack([np.full(n, c0), jn, j], -1))
        faces.append(np.stack([np.full(n, c1), last + j, last + jn], -1))
    else:
        inner = np.vstack(loft.inner_sections)
        off = len(outer)
        verts = np.vstack([outer, inner])
        faces.append(_side_faces(n_sec, n, off, reverse=True))
        last = (n_sec - 1) * n
        faces.append(np.stack([jn, j, off + j], -1))
        faces.append(np.stack([jn, off + j, off + jn], -1))
        faces.append(np.stack([last + j, last + jn, off + last + jn], -1))
        faces.append(np.stack([last + j, off + last + jn, off + last + j], -1))
    mesh = Mesh(verts, np.vstack(faces))
    if mesh.signed_volume() < 0:  # sections were ordered clockwise: flip
        mesh = Mesh(mesh.vertices, mesh.faces[:, ::-1])
    return mesh


def lofts_fingerprint(lofts: list[Loft], extra: bytes = b"") -> str:
    h = hashlib.blake2b(digest_size=12)
    for lo in lofts:
        lo.fingerprint(h)
    h.update(extra)
    return h.hexdigest()


# ---------------------------------------------------------------- helpers
def ellipse_loop(n: int, half_width: float, half_height: float) -> np.ndarray:
    """2D ellipse (y, z), starting at the top, counter-clockwise seen from +x."""
    t = np.linspace(0.0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([-half_width * np.sin(t), half_height * np.cos(t)])


def rect_loop(width: float, height: float) -> np.ndarray:
    w, h = width / 2, height / 2
    return np.array([[-w, -h], [w, -h], [w, h], [-w, h]])
