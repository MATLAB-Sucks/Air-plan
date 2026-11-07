"""Procedural component generators.

Each generator declares the parameter groups it reads.  The design engine
re-runs only the generators whose groups changed, and the renderer then
updates only components whose fingerprint changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..core.components import Component, ComponentType as CT, Connection, Relation
from ..core.parameters import AircraftParameters
from .arrangement import HINGE_GAP, Arrangement, ControlSurfaceDef, EngineInstance, GearLeg
from .airfoil import NacaAirfoil
from .fuselage import FuselageShape
from .loft import Loft, ellipse_loop, rect_loop
from .mesh import Placement
from .planform import LiftingSurface

SKIN_POINTS = 40  # points per airfoil half
TAIL_SPARS = (0.15, 0.62)  # rear spar leaves room for elevator/rudder hinges
TAIL_RIB_T, TAIL_SPAR_T = 0.03, 0.04
INSET = 0.96  # structural members sit just inside the skin


@dataclass(frozen=True)
class Generator:
    name: str
    depends_on: frozenset[str]
    build: Callable[[Arrangement], list[Component]]


# ====================================================================== helpers
def _box(centre: np.ndarray, size: float) -> Loft:
    r = rect_loop(size, size)
    secs = [np.column_stack([np.full(4, centre[0] + dx), centre[1] + r[:, 0], centre[2] + r[:, 1]])
            for dx in (-size / 2, size / 2)]
    return Loft(secs, smooth=False)


def _inset_loop(af: NacaAirfoil, loop: np.ndarray, factor: float = INSET) -> np.ndarray:
    yc, _ = af.camber_line(loop[:, 0])
    return np.column_stack([loop[:, 0], yc + factor * (loop[:, 1] - yc)])


def surface_skin(surface: LiftingSurface, side: int, cutouts: list[ControlSurfaceDef] = ()) -> list[Loft]:
    """Fixed skin of one lifting-surface half, split spanwise wherever a
    control surface removes the trailing-edge region."""
    breaks = sorted({0.0, 1.0, *surface.breaks(), *[e for cs in cutouts for e in (cs.eta0, cs.eta1)]})
    base = surface.skin_etas()
    lofts = []
    for e0, e1 in zip(breaks[:-1], breaks[1:]):
        if e1 - e0 < 1e-4:
            continue
        mid = 0.5 * (e0 + e1)
        cut = next((cs for cs in cutouts if cs.eta0 <= mid <= cs.eta1), None)
        etas = [e0, *[e for e in base if e0 < e < e1], e1]
        if cut is None:
            secs = [surface.map_section(e, surface.airfoil(e).loop(SKIN_POINTS), side) for e in etas]
        else:
            secs = [surface.map_section(e, surface.airfoil(e).fore_loop(cut.hinge, SKIN_POINTS), side) for e in etas]
        lofts.append(Loft(secs, smooth=True, sharp_start=True))
    return lofts


def _rotate(points: np.ndarray, origin: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues rotation of points about an axis through origin."""
    k = axis / np.linalg.norm(axis)
    v = points - origin
    c, s_ = np.cos(angle), np.sin(angle)
    return origin + v * c + np.cross(k, v) * s_ + np.outer(v @ k, k) * (1 - c)


def hinge_axis(surface: LiftingSurface, cs: ControlSurfaceDef) -> tuple[np.ndarray, np.ndarray]:
    """End points of the hinge line (surface frame), on the local camber line."""
    pts = []
    for e in (cs.eta0, cs.eta1):
        af = surface.airfoil(e)
        zc = float(af.camber_line(np.array(cs.hinge))[0])
        pts.append(surface.map_section(e, np.array([[cs.hinge, zc]]), cs.side)[0])
    return pts[0], pts[1]


def hinge_count(surface: LiftingSurface, cs: ControlSurfaceDef) -> int:
    """Number of hinge fittings (one per ~2.5 m of hinge line, at least two)."""
    p0, p1 = hinge_axis(surface, cs)
    return max(2, int(round(float(np.linalg.norm(p1 - p0)) / 2.5)) + 1)


def control_surface_loft(surface: LiftingSurface, cs: ControlSurfaceDef) -> Loft:
    gap_eta = 0.01 / surface.semi_span  # 1 cm spanwise gap to the fixed structure
    etas = np.linspace(cs.eta0 + gap_eta, cs.eta1 - gap_eta, 5)
    secs, corners = [], None
    for e in etas:
        pts, corners = surface.airfoil(e).aft_loop(cs.hinge + HINGE_GAP)
        secs.append(surface.map_section(e, pts, cs.side))
    if abs(cs.deflection_deg) > 1e-9:
        p0, p1 = hinge_axis(surface, cs)
        # Calibrate the rotation sense with a small positive probe so that a
        # positive deflection always moves the trailing edge down (horizontal
        # surfaces) or to port (fin), whatever the side or hinge direction.
        te = secs[2][corners[1]]
        probe = _rotate(te[None, :], p0, p1 - p0, 0.05)[0] - te
        sense = -1.0 if (probe[1] if surface.vertical else probe[2]) > 0 else 1.0
        ang = sense * np.radians(cs.deflection_deg)
        secs = [_rotate(sec, p0, p1 - p0, ang) for sec in secs]
    return Loft(secs, smooth=True, corners=corners)


def rib_stations(surface: LiftingSurface, pitch: float, thickness: float) -> np.ndarray:
    n = max(2, int(round(surface.semi_span / pitch)) + 1)
    half = thickness / 2 / surface.semi_span
    return np.linspace(half, 1 - half, n)


def surface_structure(prefix: str, surface: LiftingSurface, placement: Placement, side: int, front: float,
                      rear: float, rib_pitch: float, rib_t: float, spar_t: float, groups: tuple[str, ...]
                      ) -> list[Component]:
    """Ribs and spars of one lifting-surface half, all in the surface frame."""
    comps: list[Component] = []
    spar_ids = (f"{prefix}.spar.front", f"{prefix}.spar.rear")
    for sid, frac, label in ((spar_ids[0], front, "Front spar"), (spar_ids[1], rear, "Rear spar")):
        secs = []
        for e in np.union1d(np.linspace(0.0, 1.0, 8), surface.breaks()):
            af = surface.airfoil(e)
            w = spar_t / float(surface.chord(e)) / 2
            zu, zl = af.z_upper_lower(frac)
            yc = float(af.camber_line(np.array(frac))[0])
            zu, zl = yc + INSET * (zu - yc), yc + INSET * (zl - yc)
            pts = np.array([[frac - w, zl], [frac + w, zl], [frac + w, zu], [frac - w, zu]])
            secs.append(surface.map_section(e, pts, side))
        comps.append(Component(sid, CT.SPAR, label, [Loft(secs, smooth=False)], placement, prefix,
                               [Connection(prefix, Relation.PART_OF)],
                               {"chord_fraction": frac, "web_thickness": spar_t, "length": surface.semi_span},
                               groups))
    dt = rib_t / surface.semi_span / 2
    for i, e in enumerate(rib_stations(surface, rib_pitch, rib_t)):
        af = surface.airfoil(e)
        # rib web between the spars, slightly overlapping the spar webs
        box = _inset_loop(af, af.box_loop(front, rear))
        secs = [surface.map_section(e - dt, box, side), surface.map_section(e + dt, box, side)]
        comps.append(Component(
            f"{prefix}.rib.{i:02d}", CT.RIB, f"Rib {i}", [Loft(secs, smooth=False)], placement, prefix,
            [Connection(prefix, Relation.PART_OF), Connection(spar_ids[0], Relation.BOUNDED_BY),
             Connection(spar_ids[1], Relation.BOUNDED_BY)],
            {"eta": round(float(e), 5), "station": round(float(e * surface.semi_span), 4),
             "chord": round(float(surface.chord(e)), 4), "thickness": rib_t}, groups))
    return comps


def frame_stations(fus: FuselageShape, pitch: float) -> np.ndarray:
    return np.arange(0.06 * fus.length, 0.94 * fus.length, pitch)


def nearest_frame(fus: FuselageShape, pitch: float, x: float) -> str:
    st = frame_stations(fus, pitch)
    return f"fuselage.frame.{int(np.argmin(np.abs(st - x))):02d}"


# ====================================================================== fuselage
def build_fuselage(arr: Arrangement) -> list[Component]:
    fus = arr.fuselage
    secs = [fus.section(x, 48) for x in fus.stations(30)]
    params = {**arr.params.group_dict("fuselage"), "wetted_area": round(fus.wetted_area(), 3),
              "volume": round(fus.volume, 3)}
    return [Component("fuselage", CT.FUSELAGE, "Fuselage", [Loft(secs)], parameters=params,
                      source_groups=("fuselage",))]


def build_fuselage_structure(arr: Arrangement) -> list[Component]:
    fus, fp = arr.fuselage, arr.params.fuselage
    comps = []
    t = 0.04 + 0.01 * fus.diameter
    for i, x in enumerate(frame_stations(fus, fp.frame_pitch)):
        depth = min(fp.frame_depth, 0.3 * float(fus.half_width(x)))
        outer = [fus.section(x + dx, 36, inset=0.01) for dx in (-t / 2, t / 2)]
        inner = [fus.section(x + dx, 36, inset=0.01 + depth) for dx in (-t / 2, t / 2)]
        comps.append(Component(f"fuselage.frame.{i:02d}", CT.FRAME, f"Frame {i}", [Loft(outer, inner)],
                               parent="fuselage", connections=[Connection("fuselage", Relation.PART_OF)],
                               parameters={"station": round(float(x), 4), "depth": depth, "thickness": t},
                               source_groups=("fuselage",)))
    xs = np.linspace(0.07 * fus.length, 0.93 * fus.length, 16)
    w = 0.02 + 0.012 * fus.diameter
    for k in range(fp.longerons):
        phi = 2 * np.pi * k / fp.longerons
        secs = []
        for x in xs:
            hw, hh = float(fus.half_width(x)), float(fus.half_height(x))
            d = 0.01 + fp.frame_depth * 0.5
            c = np.array([-(hw - d) * np.sin(phi), (hh - d) * np.cos(phi)])
            n = np.array([-hh * np.sin(phi), hw * np.cos(phi)])
            n /= np.linalg.norm(n)
            tng = np.array([-n[1], n[0]])
            r = rect_loop(w, w)
            yz = c + np.outer(r[:, 0], tng) + np.outer(r[:, 1], n)
            secs.append(np.column_stack([np.full(4, x), yz[:, 0], yz[:, 1] + float(fus.centre_z(x))]))
        comps.append(Component(f"fuselage.longeron.{k:02d}", CT.LONGERON, f"Longeron {k}", [Loft(secs, smooth=False)],
                               parent="fuselage", connections=[Connection("fuselage", Relation.PART_OF)],
                               parameters={"angle_deg": round(float(np.degrees(phi)), 2), "section": w},
                               source_groups=("fuselage",)))
    return comps


# ====================================================================== wing
WING_GROUPS = ("wing", "fuselage", "configuration")
WING_SKIN_GROUPS = WING_GROUPS + ("controls",)


def _side_id(side: int) -> str:
    return "right" if side > 0 else "left"


def wing_root_fittings(arr: Arrangement, side: int) -> list[tuple[str, float, float, np.ndarray]]:
    """(spar, chord fraction, eta, aircraft-frame point) where each spar meets the fuselage side."""
    w, p, fus = arr.wing, arr.params.wing, arr.fuselage
    eta = w.eta_of(float(fus.half_width(arr.wing_placement.position[0] + 0.4 * w.root_chord)))
    return [(spar, frac, eta, arr.wing_point(w.point(eta, frac, side=side)))
            for spar, frac in (("front", p.front_spar), ("rear", p.rear_spar))]


def build_wing(arr: Arrangement) -> list[Component]:
    w, p = arr.wing, arr.params.wing
    comps = []
    derived = {"area": round(w.area, 4), "aspect_ratio": round(w.aspect_ratio, 4), "mac": round(w.mac, 4),
               "tip_chord": round(w.tip_chord, 4)}
    for side in (1, -1):
        sid = f"wing.{_side_id(side)}"
        conns = [Connection("fuselage", Relation.ATTACHED_TO, tuple(np.round(pt, 5)), f"{spar} spar root fitting")
                 for spar, _, _, pt in wing_root_fittings(arr, side)]
        cuts = [cs for cs in arr.control_surfaces if cs.surface == "wing" and cs.side == side]
        comps.append(Component(sid, CT.WING, f"Wing ({_side_id(side)})", surface_skin(w, side, cuts),
                               arr.wing_placement, None, conns, {**arr.params.group_dict("wing"), **derived},
                               WING_SKIN_GROUPS))
    return comps


def build_wing_structure(arr: Arrangement) -> list[Component]:
    w, p = arr.wing, arr.params.wing
    comps = []
    for side in (1, -1):
        comps += surface_structure(f"wing.{_side_id(side)}", w, arr.wing_placement, side, p.front_spar,
                                   p.rear_spar, p.rib_pitch, p.rib_thickness, p.spar_thickness, WING_GROUPS)
    return comps


def build_wing_attachments(arr: Arrangement) -> list[Component]:
    w, p, fus = arr.wing, arr.params.wing, arr.fuselage
    size = max(0.08, 0.05 * fus.diameter)
    comps = []
    for side in (1, -1):
        s = _side_id(side)
        for spar, frac, eta, pt in wing_root_fittings(arr, side):
            frame = nearest_frame(fus, arr.params.fuselage.frame_pitch, pt[0])
            comps.append(Component(
                f"attach.wing.{s}.{spar}", CT.ATTACHMENT, f"Wing-fuselage fitting ({s}, {spar} spar)",
                [_box(np.zeros(3), size)], Placement(tuple(pt)), f"wing.{s}",
                [Connection(f"wing.{s}", Relation.CONNECTS, tuple(np.round(pt, 5))),
                 Connection(f"wing.{s}.spar.{spar}", Relation.CONNECTS),
                 Connection("fuselage", Relation.CONNECTS), Connection(frame, Relation.CONNECTS)],
                {"eta": round(eta, 5), "chord_fraction": frac, "frame": frame}, WING_GROUPS))
    return comps


# ====================================================================== tails, canards, fins, booms
TAIL_GROUPS = ("htail", "vtail", "canard", "booms", "wing", "fuselage", "configuration")
HTAIL_GROUPS = VTAIL_GROUPS = TAIL_GROUPS  # kept for backwards compatibility
CONTROL_GROUPS = ("controls",) + TAIL_GROUPS
SURFACE_TYPES = {"htail": CT.HORIZONTAL_TAIL, "canard": CT.CANARD, "fin": CT.VERTICAL_TAIL, "v_tail": CT.V_TAIL}


def build_tails(arr: Arrangement) -> list[Component]:
    """Every non-wing lifting surface: skins, ribs, spars and root fitting, whatever the layout."""
    comps: list[Component] = []
    rib_pitch = {"htail": arr.params.htail.rib_pitch, "vtail": arr.params.vtail.rib_pitch,
                 "canard": arr.params.canard.rib_pitch}
    for ps in arr.surfaces:
        if ps.role == "wing":
            continue
        t = ps.surface
        derived = {"area": round(t.area, 4), "aspect_ratio": round(t.aspect_ratio, 4), "mac": round(t.mac, 4),
                   "mounted_on": ps.attach_to, "role": ps.role}
        for side in ps.sides:
            cid = ps.component_id(side)
            cuts = [cs for cs in arr.control_surfaces if cs.surface == ps.key and cs.side == side]
            comps.append(Component(cid, SURFACE_TYPES[ps.role], ps.label + ("" if len(ps.sides) == 1 else
                                                                             f" ({_side_id(side)})"),
                                   surface_skin(t, side, cuts), ps.placement, None,
                                   [Connection(ps.attach_to, Relation.ATTACHED_TO, tuple(ps.placement.position))],
                                   {**arr.params.group_dict(ps.group), **derived}, TAIL_GROUPS + ("controls",)))
            comps += surface_structure(cid, t, ps.placement, side, *TAIL_SPARS, rib_pitch[ps.group], TAIL_RIB_T,
                                       TAIL_SPAR_T, TAIL_GROUPS)
        pos = np.array(ps.placement.position) + np.array([TAIL_SPARS[0] * t.root_chord, 0, 0])
        halves = [ps.component_id(sd) for sd in ps.sides]
        comps.append(Component(f"attach.{ps.key}", CT.ATTACHMENT, f"{ps.label} attachment",
                               [_box(np.zeros(3), max(0.05, 0.04 * t.root_chord))], Placement(tuple(pos)), halves[0],
                               [*(Connection(h, Relation.CONNECTS) for h in halves),
                                Connection(ps.attach_to, Relation.CONNECTS)], {}, TAIL_GROUPS))
    return comps


def build_booms(arr: Arrangement) -> list[Component]:
    comps = []
    for body in arr.bodies:
        if body.role != "boom":
            continue
        shape = body.shape
        secs = [shape.section(x, 28) for x in shape.stations(16)]
        side = "right" if body.offset[1] > 0 else "left"
        comps.append(Component(body.key, CT.BOOM, f"Tail boom ({side})", [Loft(secs)], body.placement, None,
                               [Connection(f"wing.{side}", Relation.ATTACHED_TO),
                                Connection(f"htail.{side}", Relation.CONNECTS),
                                Connection(f"vtail.{side}", Relation.CONNECTS)],
                               {"length": round(shape.length, 3), "diameter": shape.diameter}, TAIL_GROUPS))
    return comps


# ====================================================================== propulsion
ENGINE_GROUPS = ("engines", "wing", "fuselage", "configuration")  # incl. pusher/upper-aft layouts
NACELLE_U = np.array([0.0, 0.04, 0.12, 0.3, 0.5, 0.7, 0.85, 1.0])
NACELLE_R = np.array([0.86, 0.94, 0.99, 1.0, 0.98, 0.9, 0.78, 0.62])


def _nacelle(e: EngineInstance) -> Loft:
    secs = []
    for u, r in zip(NACELLE_U, NACELLE_R):
        loop = ellipse_loop(32, r * e.diameter / 2, r * e.diameter / 2)
        secs.append(np.column_stack([np.full(32, u * e.length), loop]))
    return Loft(secs)


def _propeller(e: EngineInstance, blades: int = 6) -> list[Loft]:
    rp = e.prop_diameter / 2
    rs = 0.38 * e.diameter / 2
    lofts = []
    sp = []
    for u, k in ((-0.9, 0.05), (-0.6, 0.6), (-0.3, 0.9), (0.0, 1.0), (0.15, 1.0)):
        loop = ellipse_loop(24, k * rs, k * rs)
        sp.append(np.column_stack([np.full(24, u * rs * 1.5), loop]))
    lofts.append(Loft(sp))
    # Blades use polygonal sections: OpenCascade mis-aligns the seams of periodic
    # splines on strongly twisting thin sections, collapsing the lofted volume.
    t = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    for b in range(blades):
        th = 2 * np.pi * b / blades
        er, et, ex = np.array([0, np.cos(th), np.sin(th)]), np.array([0, -np.sin(th), np.cos(th)]), np.array([1, 0, 0])
        secs = []
        for r in np.linspace(0.8 * rs, rp, 6):
            f = (r - 0.8 * rs) / (rp - 0.8 * rs)
            chord = rp * (0.16 - 0.07 * f)
            beta = np.radians(45 - 28 * f)
            dc = np.cos(beta) * et + np.sin(beta) * ex
            dn = -np.sin(beta) * et + np.cos(beta) * ex
            pts = r * er + np.outer(0.5 * chord * np.cos(t), dc) + np.outer(0.06 * chord * np.sin(t), dn)
            secs.append(pts)
        lofts.append(Loft(secs, smooth=False))
    return lofts


def _pylon(e: EngineInstance) -> Loft | None:
    af = NacaAirfoil(0.0, 0.4, 0.12)
    loop = af.loop(16)
    centreline = abs(e.inlet[1]) < 1e-6
    if centreline and e.prop_diameter > 0:
        return None  # pusher turboprop inside the tail cone
    if centreline:  # single jet on top of the aft fuselage: short vertical pylon
        chord = 0.5 * e.length
        x0 = e.inlet[0] + 0.3 * e.length
        return Loft([np.column_stack([x0 + loop[:, 0] * chord, -loop[:, 1] * chord, np.full(len(loop), z)])
                     for z in (e.attach[2] - 0.1, e.inlet[2] - 0.4 * e.diameter)], sharp_start=True)
    if e.mount == "wing" and e.attach[2] < e.inlet[2]:
        return None  # engine partly buried in the upper surface (flying wing / BWB)
    if e.mount == "fuselage":
        chord = 0.5 * e.length
        x0 = e.inlet[0] + 0.3 * e.length
        ys = [e.attach[1], e.inlet[1] - e.side * 0.45 * e.diameter]
        return Loft([np.column_stack([x0 + loop[:, 0] * chord, np.full(len(loop), y), e.inlet[2] + loop[:, 1] * chord])
                     for y in ys], sharp_start=True)
    if e.prop_diameter > 0:
        return None  # turboprop nacelle is attached directly to the wing box
    chord = 0.55 * e.length
    bottom_z = e.inlet[2] + 0.42 * e.diameter
    top_z = e.attach[2] + 0.1
    x_bottom = e.inlet[0] + 0.35 * e.length
    x_top = e.attach[0] - 0.25 * chord
    secs = []
    for x0, z in ((x_bottom, bottom_z), (x_top, top_z)):
        secs.append(np.column_stack([x0 + loop[:, 0] * chord, e.inlet[1] - loop[:, 1] * chord, np.full(len(loop), z)]))
    return Loft(secs, sharp_start=True)


def build_engines(arr: Arrangement) -> list[Component]:
    comps = []
    prop = arr.params.configuration.propulsion
    for e in arr.engines:
        parent = "fuselage" if e.mount == "fuselage" else f"wing.{_side_id(e.side)}"
        nid, pid = f"nacelle.{e.index}", f"pylon.{e.index}"
        pyl = _pylon(e)
        mount_target = pid if pyl is not None else parent
        eparams = {"thrust_kn": arr.params.engines.thrust_kn, "propulsion": prop, "diameter": round(e.diameter, 4),
                   "length": round(e.length, 4), "eta": round(e.eta, 4), "mount": e.mount}
        comps.append(Component(nid, CT.NACELLE, f"Engine {e.index}", [_nacelle(e)], Placement(tuple(e.inlet)), None,
                               [Connection(mount_target, Relation.MOUNTED_ON)], eparams, ENGINE_GROUPS))
        if pyl is not None:
            comps.append(Component(pid, CT.PYLON, f"Pylon {e.index}", [pyl], Placement(), None,
                                   [Connection(nid, Relation.CONNECTS), Connection(parent, Relation.ATTACHED_TO,
                                                                                   tuple(np.round(e.attach, 5)))],
                                   {"attach_point": [round(float(v), 4) for v in e.attach]}, ENGINE_GROUPS))
        if e.prop_diameter > 0:
            pos = np.array([e.prop_x, e.inlet[1], e.inlet[2]])
            comps.append(Component(f"propeller.{e.index}", CT.PROPELLER, f"Propeller {e.index}", _propeller(e),
                                   Placement(tuple(pos)), nid, [Connection(nid, Relation.MOUNTED_ON)],
                                   {"diameter": round(e.prop_diameter, 4), "blades": 6}, ENGINE_GROUPS))
        comps.append(Component(f"attach.engine.{e.index}", CT.ATTACHMENT, f"Engine {e.index} mount",
                               [_box(np.zeros(3), max(0.06, 0.08 * e.diameter))], Placement(tuple(e.attach)),
                               mount_target, [Connection(mount_target, Relation.CONNECTS),
                                              Connection(parent, Relation.CONNECTS)],
                               {"eta": round(e.eta, 4)}, ENGINE_GROUPS))
    return comps


# ====================================================================== landing gear
GEAR_GROUPS = ("gear", "wing", "fuselage", "configuration")
WHEEL_Y = np.linspace(-0.5, 0.5, 6)
WHEEL_R = np.array([0.62, 0.92, 1.0, 1.0, 0.92, 0.62])


def _wheel(leg: GearLeg) -> Loft:
    secs = []
    for yu, r in zip(WHEEL_Y, WHEEL_R):
        loop = ellipse_loop(32, r * leg.wheel_diameter / 2, r * leg.wheel_diameter / 2)
        secs.append(np.column_stack([loop[:, 0], np.full(32, yu * leg.wheel_width), loop[:, 1]]))
    return Loft(secs)


def build_gear(arr: Arrangement) -> list[Component]:
    comps = []
    for leg in arr.gear:
        sid = f"gear.{leg.name}.strut"
        rs = 0.04 + 0.05 * leg.wheel_diameter
        length = leg.strut_length
        loop = ellipse_loop(20, rs, rs)
        secs = [np.column_stack([loop[:, 1], loop[:, 0], np.full(20, z)]) for z in (0.0, -length)]
        comps.append(Component(sid, CT.GEAR_STRUT, f"{leg.name.replace('_', ' ').title()} gear strut", [Loft(secs)],
                               Placement(tuple(leg.top)), None, [Connection(leg.attached_to, Relation.ATTACHED_TO,
                                                                             tuple(np.round(leg.top, 5)))],
                               {"strut_length": round(leg.strut_length, 4), "wheels": leg.wheels}, GEAR_GROUPS))
        offsets = [0.0] if leg.wheels == 1 else [-(0.5 * leg.wheel_width + rs + 0.03), 0.5 * leg.wheel_width + rs + 0.03]
        for k, dy in enumerate(offsets):
            comps.append(Component(f"gear.{leg.name}.wheel.{k}", CT.WHEEL, f"{leg.name} wheel {k}", [_wheel(leg)],
                                   Placement(tuple(leg.axle + np.array([0, dy, 0]))), sid,
                                   [Connection(sid, Relation.MOUNTED_ON)],
                                   {"diameter": round(leg.wheel_diameter, 4)}, GEAR_GROUPS))
        comps.append(Component(f"attach.gear.{leg.name}", CT.ATTACHMENT, f"{leg.name} gear trunnion",
                               [_box(np.zeros(3), 2.4 * rs)], Placement(tuple(leg.top)), sid,
                               [Connection(sid, Relation.CONNECTS), Connection(leg.attached_to, Relation.CONNECTS)],
                               {}, GEAR_GROUPS))
    return comps


# ====================================================================== control surfaces
CS_TYPES = {"aileron": CT.AILERON, "flap": CT.FLAP, "elevator": CT.ELEVATOR, "rudder": CT.RUDDER,
            "elevon_inner": CT.ELEVON, "elevon_outer": CT.ELEVON, "ruddervator": CT.RUDDERVATOR,
            "drag_rudder": CT.DRAG_RUDDER}
CS_LABELS = {"elevon_inner": "Inboard elevon", "elevon_outer": "Outboard elevon", "drag_rudder": "Drag rudder"}


def rear_spar_of(arr: Arrangement, surface_key: str) -> float:
    return arr.params.wing.rear_spar if surface_key == "wing" else TAIL_SPARS[1]


def build_control_surfaces(arr: Arrangement) -> list[Component]:
    comps = []
    for cs in arr.control_surfaces:
        surf, plc = arr.surface(cs.surface), arr.surface_placement(cs.surface)
        p0, p1 = hinge_axis(surf, cs)
        span = float(np.linalg.norm(p1 - p0))
        area = surf.strip_area(cs.eta0, cs.eta1, cs.chord_fraction)
        rear = f"{cs.parent_id}.spar.rear"
        params = {"eta_start": round(cs.eta0, 4), "eta_end": round(cs.eta1, 4), "chord_fraction": cs.chord_fraction,
                  "hinge_chord_fraction": round(cs.hinge, 4), "deflection_deg": cs.deflection_deg,
                  "area_m2": round(area, 4), "hinge_length_m": round(span, 4),
                  "hinge_to_rear_spar_c": round(cs.hinge - rear_spar_of(arr, cs.surface), 4)}
        single = len(arr.placed(cs.surface).sides) == 1
        label = CS_LABELS.get(cs.kind, cs.kind.title()) + ("" if single else f" ({_side_id(cs.side)})")
        comps.append(Component(cs.id, CS_TYPES[cs.kind], label, [control_surface_loft(surf, cs)], plc, cs.parent_id,
                               [Connection(cs.parent_id, Relation.HINGED_TO,
                                           tuple(np.round(plc.apply(p0[None, :])[0], 5)), "inboard hinge"),
                                Connection(cs.parent_id, Relation.HINGED_TO,
                                           tuple(np.round(plc.apply(p1[None, :])[0], 5)), "outboard hinge")],
                               params, CONTROL_GROUPS))
        n_hinges = hinge_count(surf, cs)
        size = max(0.05, 0.03 * float(surf.chord(cs.eta0)))
        for k, u in enumerate(np.linspace(0.04, 0.96, n_hinges)):
            pt = plc.apply((p0 + u * (p1 - p0))[None, :])[0]
            comps.append(Component(
                f"attach.hinge.{cs.id}.{k}", CT.ATTACHMENT, f"{label} hinge {k}", [_box(np.zeros(3), size)],
                Placement(tuple(pt)), cs.id,
                [Connection(cs.id, Relation.CONNECTS), Connection(rear, Relation.CONNECTS),
                 Connection(cs.parent_id, Relation.CONNECTS)], {"hinge_station": round(float(u), 3)},
                CONTROL_GROUPS))
    return comps


GENERATORS: list[Generator] = [
    Generator("fuselage", frozenset({"fuselage"}), build_fuselage),
    Generator("fuselage_structure", frozenset({"fuselage"}), build_fuselage_structure),
    Generator("wing", frozenset(WING_SKIN_GROUPS), build_wing),
    Generator("wing_structure", frozenset(WING_GROUPS), build_wing_structure),
    Generator("wing_attachments", frozenset(WING_GROUPS), build_wing_attachments),
    Generator("tails", frozenset(TAIL_GROUPS + ("controls",)), build_tails),
    Generator("booms", frozenset(TAIL_GROUPS), build_booms),
    Generator("engines", frozenset(ENGINE_GROUPS), build_engines),
    Generator("gear", frozenset(GEAR_GROUPS), build_gear),
    Generator("control_surfaces", frozenset(CONTROL_GROUPS), build_control_surfaces),
]


def build_all(params: AircraftParameters) -> dict[str, list[Component]]:
    arr = Arrangement(params)
    return {g.name: g.build(arr) for g in GENERATORS}
