"""OpenCascade solids (via CadQuery) built from the shared :class:`Loft` sections.

Imported lazily: the rest of the application runs without CadQuery, only
STEP export needs it.
"""
from __future__ import annotations

import numpy as np

from .loft import Loft
from .mesh import Placement


def cadquery_available() -> bool:
    try:
        import cadquery  # noqa: F401
        return True
    except Exception:  # pragma: no cover - depends on the environment
        return False


def _tangents(points: np.ndarray) -> list:
    """Unit tangents along an open airfoil polyline (one-sided at the ends)."""
    import cadquery as cq
    t = np.empty_like(points)
    t[1:-1] = points[2:] - points[:-2]
    t[0] = points[1] - points[0]
    t[-1] = points[-1] - points[-2]
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    return [cq.Vector(*map(float, v)) for v in t]


def _wire(points: np.ndarray, smooth: bool, sharp_start: bool):
    import cadquery as cq
    vecs = [cq.Vector(*map(float, p)) for p in points]
    if not smooth:
        return cq.Wire.makePolygon(vecs, close=True)
    if sharp_start:
        # Airfoil: two splines (upper TE->LE, lower LE->TE) share the leading-edge
        # point and its tangent (G1 nose); a straight base closes the trailing edge.
        # Tangents are prescribed at every point (central differences) so the
        # splines cannot overshoot between the densely clustered stations.
        le = len(points) // 2  # airfoil loops put the leading edge at n/2 (see Loft)
        tans = _tangents(points)
        upper = cq.Edge.makeSpline(vecs[: le + 1], tangents=tans[: le + 1], scale=True)
        lower = cq.Edge.makeSpline(vecs[le:], tangents=tans[le:], scale=True)
        base = cq.Edge.makeLine(vecs[-1], vecs[0])  # finite trailing-edge base
        return cq.Wire.assembleEdges([upper, lower, base])
    return cq.Wire.assembleEdges([cq.Edge.makeSpline(vecs, periodic=True)])


def _cornered_wire(points: np.ndarray, corners: tuple[int, ...]):
    """Closed wire: spline pieces between corners, lines between adjacent corners."""
    import cadquery as cq
    n = len(points)
    edges = []
    for i, c0 in enumerate(corners):
        c1 = corners[(i + 1) % len(corners)]
        idx = list(range(c0, c1 + 1)) if c1 > c0 else list(range(c0, n)) + list(range(0, c1 + 1))
        piece = points[idx]
        vecs = [cq.Vector(*map(float, q)) for q in piece]
        if len(piece) == 2:
            edges.append(cq.Edge.makeLine(vecs[0], vecs[1]))
        else:
            edges.append(cq.Edge.makeSpline(vecs, tangents=_tangents(piece), scale=True))
    return cq.Wire.assembleEdges(edges)


def _section_wire(points: np.ndarray, loft: Loft):
    if loft.corners:
        return _cornered_wire(points, loft.corners)
    return _wire(points, loft.smooth, loft.sharp_start)


def loft_to_solid(loft: Loft):
    """Return a ``cadquery.Solid`` for the loft (hollow lofts are cut)."""
    import cadquery as cq
    outer = cq.Solid.makeLoft([_section_wire(s, loft) for s in loft.sections], ruled=False)
    if loft.inner_sections is None:
        return outer
    inner = cq.Solid.makeLoft([_section_wire(s, loft) for s in loft.inner_sections], ruled=False)
    result = outer.cut(inner)
    solids = result.Solids()
    return solids[0] if len(solids) == 1 else result


def placement_location(placement: Placement):
    import cadquery as cq
    from OCP.gp import gp_Trsf
    m = placement.matrix()
    t = gp_Trsf()
    t.SetValues(*[float(v) for v in m[:3, :].ravel()])
    return cq.Location(t)


def component_shape(component, world: bool = True):
    """Compound of all lofts of a component, optionally moved to the aircraft frame."""
    import cadquery as cq
    solids = [loft_to_solid(lo) for lo in component.lofts]
    shape = solids[0] if len(solids) == 1 else cq.Compound.makeCompound(solids)
    if world:
        shape = shape.moved(placement_location(component.placement))
    return shape
