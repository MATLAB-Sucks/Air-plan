"""Placement of every major component, derived from the parameters.

The arrangement is pure maths (no meshes) so it is cheap enough for the
optimiser to call thousands of times; the geometry builder uses the very same
object to place solids, which guarantees that what is analysed is what is
drawn and exported.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np

from ..core.parameters import AircraftParameters
from .fuselage import FuselageShape
from .mesh import Placement
from .planform import LiftingSurface


from ..numerics.propulsion import engine_dimensions  # noqa: E402  (re-exported)


@dataclass(frozen=True)
class EngineInstance:
    index: int
    side: int  # +1 starboard, -1 port
    mount: str  # "wing" or "fuselage"
    inlet: np.ndarray  # nacelle inlet centre, aircraft frame
    diameter: float
    length: float
    prop_diameter: float
    eta: float  # spanwise station for wing engines (0 for fuselage engines)
    attach: np.ndarray  # pylon attachment point on the parent structure
    pusher: bool = False  # propeller (if any) behind the nacelle

    @property
    def centre(self) -> np.ndarray:
        return self.inlet + np.array([self.length / 2, 0.0, 0.0])

    @property
    def prop_x(self) -> float:
        return float(self.inlet[0] + (1.06 * self.length if self.pusher else -0.06 * self.length))

    @property
    def lowest_z(self) -> float:
        r = max(self.diameter, self.prop_diameter) / 2
        return float(self.inlet[2] - r)


@dataclass(frozen=True)
class GearLeg:
    name: str  # "nose", "main_left", "main_right"
    top: np.ndarray  # trunnion / attachment point
    axle: np.ndarray
    wheel_diameter: float
    wheel_width: float
    wheels: int
    attached_to: str

    @property
    def strut_length(self) -> float:
        return float(self.top[2] - self.axle[2])


@dataclass(frozen=True)
class ControlSurfaceDef:
    """A movable surface on one side of a lifting surface (shared by builder and evaluator)."""

    kind: str  # aileron | flap | elevator | rudder | elevon_inner | elevon_outer | drag_rudder | ruddervator
    surface: str  # key of the PlacedSurface it is cut from (wing, htail, canard, vtail, vtail.right ...)
    side: int
    eta0: float
    eta1: float
    chord_fraction: float  # of local chord, from the trailing edge
    deflection_deg: float
    parent_id: str = ""  # component id of the skin it is hinged to

    @property
    def hinge(self) -> float:
        return 1.0 - self.chord_fraction

    @property
    def id(self) -> str:
        return f"{self.parent_id}.{self.kind}"


@dataclass(frozen=True)
class PlacedSurface:
    """A lifting surface placed on the aircraft.  The generic unit used by the
    builder (skins, ribs, spars, control surfaces), evaluator (drag, weight,
    stability) and structures, whatever the layout."""

    key: str  # component id prefix
    role: str  # wing | htail | canard | fin | v_tail
    surface: LiftingSurface
    placement: Placement
    sides: tuple[int, ...]  # (1, -1) symmetric halves or (1,) single
    group: str  # parameter group
    attach_to: str  # component id it is mounted on
    label: str

    def component_id(self, side: int) -> str:
        if len(self.sides) == 1:
            return self.key
        return f"{self.key}.{'right' if side > 0 else 'left'}"

    @property
    def x_ac(self) -> float:
        return self.placement.position[0] + self.surface.x_quarter_mac

    @property
    def area(self) -> float:
        return self.surface.area

    @property
    def vertical_fraction(self) -> float:
        """Share of the area acting in yaw (sin^2 of cant angle for a V-tail)."""
        if self.surface.vertical:
            return 1.0
        return float(np.sin(np.radians(self.surface.dihedral_deg)) ** 2) if self.role == "v_tail" else 0.0

    @property
    def horizontal_fraction(self) -> float:
        if self.surface.vertical:
            return 0.0
        return float(np.cos(np.radians(self.surface.dihedral_deg)) ** 2) if self.role == "v_tail" else 1.0


@dataclass(frozen=True)
class Body:
    """A streamlined body (fuselage, crew pod or tail boom)."""

    key: str
    role: str  # fuselage | boom
    shape: FuselageShape
    offset: tuple[float, float, float]  # placement of the body nose

    @property
    def placement(self) -> Placement:
        return Placement(self.offset)


TAILLESS = ("flying_wing", "blended_wing_body", "supersonic_delta")  # elevons, no horizontal tail
POD_LAYOUTS = ("flying_wing", "blended_wing_body")  # fuselage is a blended crew pod / cockpit fairing


HINGE_GAP = 0.01  # chord fraction between fixed surface and control surface


class Arrangement:
    """Placement of every component for any supported layout.

    Layouts: conventional (with conventional / T / V / twin-fin tails),
    canard, flying_wing, blended_wing_body and twin_boom.  Everything is
    expressed as generic ``PlacedSurface`` and ``Body`` objects so the builder,
    evaluator and structures do not special-case layouts.
    """

    def __init__(self, params: AircraftParameters):
        self.params = params

    # ------------------------------------------------------------ layout queries
    @property
    def layout(self) -> str:
        return self.params.configuration.layout

    @property
    def tail_type(self) -> str:
        t = self.params.configuration.tail_type
        if self.layout == "supersonic_delta":
            return "none" if t == "none" else "conventional"  # single fin on the fuselage
        if self.layout in POD_LAYOUTS and t not in ("twin_fin", "none"):
            return "none"
        if self.layout == "twin_boom":
            return "twin_fin"
        if self.layout == "canard" and t not in ("conventional", "twin_fin"):
            return "conventional"
        return t

    @property
    def is_t_tail(self) -> bool:
        return self.layout == "conventional" and self.tail_type == "t_tail"

    @property
    def is_tailless(self) -> bool:
        return self.layout in TAILLESS

    @property
    def has_pod(self) -> bool:
        """The fuselage is a short pod blended into the wing (flying wing, BWB)."""
        return self.layout in POD_LAYOUTS

    @property
    def has_htail(self) -> bool:
        return self.layout in ("conventional", "twin_boom") and self.tail_type != "v_tail"

    @property
    def has_canard(self) -> bool:
        return self.layout == "canard"

    # ------------------------------------------------------------ fuselage / pod
    @cached_property
    def base_fuselage(self) -> FuselageShape:
        """Fuselage before area ruling (used to place the wing, avoiding a circular dependency)."""
        f = self.params.fuselage
        return FuselageShape(f.length, f.diameter, f.height_ratio, f.nose_fraction, f.tail_fraction,
                             f.tail_upsweep, nose_profile=f.nose_profile)

    @cached_property
    def fuselage(self) -> FuselageShape:
        """Fuselage, waisted where the wing adds cross-section if area ruling is requested.

        Whitcomb's area rule: transonic/supersonic wave drag depends on how smoothly
        the *total* cross-sectional area grows and shrinks along the aircraft, so the
        fuselage is narrowed ("coke bottle") by a share of the wing's local area.
        """
        base = self.base_fuselage
        k = self.params.fuselage.area_ruling
        if k <= 0:
            return base
        xs = np.linspace(0.0, base.length, 121)
        aw = self._surface_area_at(xs, ("wing",))
        af = np.pi * base.half_width(xs) * base.half_height(xs)
        scale = np.clip((af - k * aw) / np.maximum(af, 1e-9), 0.45, 1.0)
        return FuselageShape(base.length, base.diameter, base.height_ratio, base.nose_fraction, base.tail_fraction,
                             base.tail_upsweep, waist=(tuple(xs.round(5)), tuple(scale.round(5))),
                             nose_profile=base.nose_profile)

    # ------------------------------------------------------------ area & lift distributions
    def _surface_area_at(self, xs: np.ndarray, roles=("wing", "htail", "canard", "fin", "v_tail"),
                         n_eta: int = 36, n_chord: int = 30) -> np.ndarray:
        """Normal cross-sectional area of lifting surfaces at stations xs (both halves)."""
        dx = xs[1] - xs[0]
        area = np.zeros_like(xs)
        surfaces = [PlacedSurface("wing", "wing", self.wing, self.wing_placement, (1, -1), "wing", "", "")] \
            if roles == ("wing",) else [s for s in self.surfaces if s.role in roles]
        fr = (np.arange(n_chord) + 0.5) / n_chord
        for ps in surfaces:
            t = ps.surface
            etas = (np.arange(n_eta) + 0.5) / n_eta
            for e in etas:
                c = float(t.chord(e))
                af = t.airfoil(float(e))
                _, zu, _, zl = af.surfaces(fr)
                thick = (zu - zl) * c
                width = t.semi_span / n_eta
                x_local = float(t.x_le(e)) + fr * c
                x_glob = ps.placement.position[0] + x_local
                w = thick * width * (c / n_chord) / dx * len(ps.sides)
                area += np.histogram(x_glob, bins=len(xs), range=(xs[0] - dx / 2, xs[-1] + dx / 2), weights=w)[0]
        return area

    def overall_length(self) -> tuple[float, float]:
        """(x_front, x_rear) of the whole aircraft."""
        x0, x1 = 0.0, self.fuselage.length
        for ps in self.surfaces:
            t = ps.surface
            x1 = max(x1, ps.placement.position[0] + float(max(t.x_le(e) + t.chord(e) for e in (0, 0.5, 1))))
        for b in self.bodies:
            x1 = max(x1, b.offset[0] + b.shape.length)
        for e in self.engines:
            x1 = max(x1, e.inlet[0] + e.length)
        return x0, x1

    def area_distribution(self, n: int = 300) -> tuple[np.ndarray, np.ndarray]:
        """Total normal cross-sectional area A(x): bodies + all lifting surfaces + nacelles."""
        x0, x1 = self.overall_length()
        xs = np.linspace(x0, x1, n)
        area = self._surface_area_at(xs)
        for b in self.bodies:
            xl = xs - b.offset[0]
            inside = (xl >= 0) & (xl <= b.shape.length)
            area += np.where(inside, np.pi * b.shape.half_width(np.clip(xl, 0, b.shape.length))
                             * b.shape.half_height(np.clip(xl, 0, b.shape.length)), 0.0)
        for e in self.engines:  # nacelle wall + core, inlet mass flow excluded (~half the frontal area)
            inside = (xs >= e.inlet[0]) & (xs <= e.inlet[0] + e.length)
            area += np.where(inside, 0.5 * np.pi * (e.diameter / 2) ** 2, 0.0)
        return xs, area

    def lift_distribution(self, xs: np.ndarray, total_lift_n: float, n_eta: int = 40) -> np.ndarray:
        """Lift per unit length along x (N/m): Schrenk spanwise, uniform chordwise (supersonic)."""
        w = self.wing
        dx = xs[1] - xs[0]
        etas = (np.arange(n_eta) + 0.5) / n_eta
        c = w.chord(etas)
        ell = np.sqrt(np.clip(1 - etas**2, 0, 1))
        span_load = 0.5 * (ell / np.trapezoid(ell, etas) + c / np.trapezoid(c, etas))
        strip = total_lift_n * span_load / n_eta  # both halves together
        out = np.zeros_like(xs)
        x_le = self.wing_placement.position[0] + w.x_le(etas)
        for le, ch, L in zip(x_le, c, strip):
            pts = le + (np.arange(20) + 0.5) / 20 * ch
            out += np.histogram(pts, bins=len(xs), range=(xs[0] - dx / 2, xs[-1] + dx / 2),
                                weights=np.full(20, L / 20 / dx))[0]
        return out

    # ------------------------------------------------------------ wing
    @cached_property
    def wing(self) -> LiftingSurface:
        w = self.params.wing
        crank = w.crank_eta if (w.crank_eta > 0.02) else 0.0
        return LiftingSurface(w.span, w.root_chord, w.taper_ratio, w.sweep_le_deg, w.dihedral_deg,
                              w.twist_tip_deg, w.tc_root, w.tc_tip, w.camber, w.camber_pos,
                              crank_eta=crank, crank_chord_ratio=w.crank_chord_ratio,
                              inner_sweep_le_deg=w.inner_sweep_le_deg if crank else None)

    @cached_property
    def wing_placement(self) -> Placement:
        w, fus = self.params.wing, self.base_fuselage
        x = w.x_position * fus.length
        xm = min(x + 0.5 * w.root_chord, fus.length)
        hh = float(fus.half_height(xm))
        frac = {"low": -0.72, "mid": 0.0, "high": 0.80}[self.params.configuration.wing_position]
        frac = float(np.clip(frac + getattr(self.params.wing, "vertical_shift", 0.0), -0.9, 0.95))
        if self.has_pod:
            frac = -0.15  # crew pod / cockpit fairing sits on top of the centre section
        z = float(fus.centre_z(xm)) + frac * hh
        return Placement((x, 0.0, z), (0.0, w.incidence_deg, 0.0))

    def wing_point(self, local: np.ndarray) -> np.ndarray:
        return self.wing_placement.apply(np.atleast_2d(local))[0]

    def wing_tip_le(self, side: int = 1) -> np.ndarray:
        return self.wing_point(self.wing.map_section(1.0, np.array([[0.0, 0.0]]), side)[0])

    # ------------------------------------------------------------ booms (twin-boom)
    @property
    def boom_y(self) -> float:
        return self.params.booms.lateral_station * self.wing.semi_span

    @cached_property
    def bodies(self) -> list[Body]:
        out = [Body("fuselage", "fuselage", self.fuselage, (0.0, 0.0, 0.0))]
        if self.layout != "twin_boom":
            return out
        b, w = self.params.booms, self.wing
        eta = self.params.booms.lateral_station
        le = self.wing_point(w.map_section(eta, np.array([[0.0, 0.0]]), 1)[0])
        x0 = le[0] - 0.12 * float(w.chord(eta)) - 0.6 * b.diameter
        tail = self.htail_placement.position[0] + 1.05 * self.params.htail.root_chord
        length = max(tail - x0, 2.0 * b.diameter)
        shape = FuselageShape(length, b.diameter, 1.0, 0.18, 0.25, 0.0)
        z = le[2] - 0.15 * b.diameter
        for side in (1, -1):
            out.append(Body(f"boom.{'right' if side > 0 else 'left'}", "boom", shape, (x0, side * self.boom_y, z)))
        return out

    # ------------------------------------------------------------ tail surfaces
    @cached_property
    def vtail(self) -> LiftingSurface:
        t = self.params.vtail
        if self.tail_type == "v_tail":  # symmetric canted surface
            return LiftingSurface(t.span, t.root_chord, t.taper_ratio, t.sweep_le_deg, t.dihedral_deg, 0.0, t.tc, t.tc)
        return LiftingSurface(t.span, t.root_chord, t.taper_ratio, t.sweep_le_deg, 0.0, 0.0, t.tc, t.tc,
                              symmetric=False, vertical=True)

    @cached_property
    def vtail_placement(self) -> Placement:
        t, fus = self.params.vtail, self.fuselage
        x = t.x_position * fus.length
        xm = min(x + 0.5 * t.root_chord, fus.length)
        frac = 0.35 if self.tail_type == "v_tail" else 0.55
        z = float(fus.centre_z(xm)) + frac * float(fus.half_height(xm))
        return Placement((x, 0.0, z))

    @cached_property
    def htail(self) -> LiftingSurface:
        t = self.params.htail
        span = 2 * self.boom_y if self.layout == "twin_boom" else t.span
        return LiftingSurface(span, t.root_chord, t.taper_ratio, t.sweep_le_deg, t.dihedral_deg, 0.0, t.tc, t.tc)

    @cached_property
    def htail_placement(self) -> Placement:
        t, fus = self.params.htail, self.fuselage
        if self.layout == "twin_boom":
            eta = self.params.booms.lateral_station
            le = self.wing_point(self.wing.map_section(eta, np.array([[0.0, 0.0]]), 1)[0])
            x = self.wing_placement.position[0] + self.params.booms.tail_arm
            return Placement((x, 0.0, le[2] + 0.1 * self.params.booms.diameter))
        if self.is_t_tail:
            v, vp = self.vtail, self.vtail_placement.position
            tip_le = vp[0] + float(v.x_le(1.0))
            x = tip_le + 0.5 * v.tip_chord - 0.35 * t.root_chord
            z = vp[2] + v.semi_span - 0.02 * v.tip_chord
            return Placement((x, 0.0, z))
        x = t.x_position * fus.length
        xm = min(x + 0.4 * t.root_chord, fus.length)
        z = float(fus.centre_z(xm)) + 0.1 * float(fus.half_height(xm))
        return Placement((x, 0.0, z))

    @cached_property
    def canard(self) -> LiftingSurface:
        t = self.params.canard
        return LiftingSurface(t.span, t.root_chord, t.taper_ratio, t.sweep_le_deg, t.dihedral_deg, 0.0, t.tc, t.tc,
                              camber=0.02, camber_pos=0.4)

    @cached_property
    def canard_placement(self) -> Placement:
        t, fus = self.params.canard, self.fuselage
        x = t.x_position * fus.length
        xm = x + 0.5 * t.root_chord
        z = float(fus.centre_z(xm)) + 0.35 * float(fus.half_height(xm))
        return Placement((x, 0.0, z))

    def _fin_positions(self) -> list[tuple[int, np.ndarray]]:
        """(side, root LE point) of each fin for twin-fin arrangements."""
        v = self.params.vtail
        out = []
        for side in (1, -1):
            if self.layout == "twin_boom":
                hp = self.htail_placement.position
                p = np.array([hp[0] - 0.15 * v.root_chord, side * self.boom_y, hp[2]])
            elif self.layout == "conventional":  # H-tail: fins on the stabiliser tips
                h, hp = self.htail, self.htail_placement.position
                tip_x = hp[0] + float(h.x_le(1.0)) + 0.5 * h.tip_chord
                p = np.array([tip_x - 0.5 * v.root_chord, side * h.semi_span,
                              hp[2] + float(h.lift_offset(1.0)) - 0.3 * v.span])
            elif self.layout == "blended_wing_body":  # on the centre-body, ahead of its trailing edge
                w, eta = self.wing, max(self.wing.crank_eta * 0.7, 0.08)
                te = self.wing_point(w.map_section(eta, np.array([[1.0, 0.0]]), side)[0])
                af = w.airfoil(eta)
                zu = af.z_upper_lower(0.75)[0] * float(w.chord(eta))
                p = np.array([te[0] - 1.1 * v.root_chord, te[1], te[2] + zu])
            else:  # canard / flying wing: wing-tip fins
                tip = self.wing_tip_le(side)
                p = tip + np.array([0.25 * self.wing.tip_chord, 0.0, 0.0])
            out.append((side, p))
        return out

    @cached_property
    def surfaces(self) -> list[PlacedSurface]:
        """Every lifting surface, main wing first."""
        out = [PlacedSurface("wing", "wing", self.wing, self.wing_placement, (1, -1), "wing", "fuselage", "Wing")]
        if self.has_htail:
            parent = "vtail" if self.is_t_tail else ("boom.right" if self.layout == "twin_boom" else "fuselage")
            out.append(PlacedSurface("htail", "htail", self.htail, self.htail_placement, (1, -1), "htail", parent,
                                     "Horizontal tail"))
        if self.has_canard:
            out.append(PlacedSurface("canard", "canard", self.canard, self.canard_placement, (1, -1), "canard",
                                     "fuselage", "Canard"))
        tt = self.tail_type
        if tt in ("conventional", "t_tail") and not self.has_pod:
            out.append(PlacedSurface("vtail", "fin", self.vtail, self.vtail_placement, (1,), "vtail", "fuselage",
                                     "Vertical tail"))
        elif tt == "v_tail":
            out.append(PlacedSurface("vtail", "v_tail", self.vtail, self.vtail_placement, (1, -1), "vtail",
                                     "fuselage", "V-tail"))
        elif tt == "twin_fin":
            for side, p in self._fin_positions():
                s = "right" if side > 0 else "left"
                parent = {"twin_boom": f"boom.{s}", "conventional": f"htail.{s}",
                          "blended_wing_body": f"wing.{s}"}.get(self.layout, f"wing.{s}")
                out.append(PlacedSurface(f"vtail.{s}", "fin", self.vtail, Placement(tuple(p)), (1,), "vtail",
                                         parent, f"Fin ({s})"))
        return out

    def placed(self, key: str) -> PlacedSurface:
        return next(s for s in self.surfaces if s.key == key)

    # backwards-compatible accessors -------------------------------------------
    def surface(self, key: str) -> LiftingSurface:
        return self.placed(key).surface

    def surface_placement(self, key: str) -> Placement:
        return self.placed(key).placement

    @property
    def x_ac_wing(self) -> float:
        return self.wing_placement.position[0] + self.wing.x_quarter_mac

    @property
    def x_ac_htail(self) -> float:
        """Aerodynamic centre of the pitch-stabilising surface (aft tail, V-tail or canard)."""
        for key in ("htail", "vtail", "canard"):
            if any(s.key == key and s.horizontal_fraction > 0 for s in self.surfaces):
                return self.placed(key).x_ac
        return self.x_ac_wing

    @property
    def x_ac_vtail(self) -> float:
        fins = [s for s in self.surfaces if s.vertical_fraction > 0]
        return float(np.mean([s.x_ac for s in fins])) if fins else self.x_ac_wing

    @property
    def wing_exposed_eta(self) -> float:
        hw = float(self.fuselage.half_width(self.wing_placement.position[0]))
        return self.wing.eta_of(hw) if not self.has_pod else self.wing.eta_of(0.3 * hw)

    def pitch_surfaces(self) -> list[PlacedSurface]:
        return [s for s in self.surfaces if s.role != "wing" and s.horizontal_fraction > 0]

    def yaw_surfaces(self) -> list[PlacedSurface]:
        return [s for s in self.surfaces if s.vertical_fraction > 0]

    def area_of(self, roles: tuple[str, ...], component: str = "horizontal") -> float:
        """Total effective area of surfaces with the given roles (both halves)."""
        tot = 0.0
        for s in self.surfaces:
            if s.role in roles:
                frac = s.horizontal_fraction if component == "horizontal" else s.vertical_fraction
                tot += s.area * frac
        return tot

    # ------------------------------------------------------------ cabin in a blended wing body
    def centrebody_floor_area(self, min_height: float = 2.1, front: float = 0.10, rear: float = 0.72) -> float:
        """Floor area inside the wing where the section is at least ``min_height`` deep."""
        w = self.wing
        etas = np.linspace(0.0, 0.6, 61)
        xs = np.linspace(front, rear, 31)
        width = []
        for e in etas:
            af = w.airfoil(float(e))
            _, zu, _, zl = af.surfaces(xs)
            c = float(w.chord(e))
            ok = (zu - zl) * c >= min_height
            width.append(np.trapezoid(ok.astype(float), xs) * c)
        return 2 * float(np.trapezoid(width, etas * w.semi_span))

    # ------------------------------------------------------------ control surfaces
    @cached_property
    def control_surfaces(self) -> list[ControlSurfaceDef]:
        c = self.params.controls
        out: list[ControlSurfaceDef] = []
        fus = self.fuselage
        root = self.wing_exposed_eta + 0.015
        for side in (1, -1):
            wid = f"wing.{'right' if side > 0 else 'left'}"
            if self.is_tailless:
                # elevons for pitch and roll; split drag rudders at the tips if there are no fins
                inner0 = max(root, (self.wing.crank_eta or 0.2) + 0.02)
                # finless aircraft give the outboard trailing edge to large split drag rudders (B-2)
                outer1 = min(c.aileron_end, 0.78 if self.tail_type == "none" else 0.96)
                mid = min(max(c.aileron_start, inner0 + 0.12), outer1 - 0.1)
                out.append(ControlSurfaceDef("elevon_inner", "wing", side, inner0, mid - 0.01, c.aileron_chord,
                                             c.elevator_deflection_deg, wid))
                out.append(ControlSurfaceDef("elevon_outer", "wing", side, mid, outer1, c.aileron_chord,
                                             c.elevator_deflection_deg + side * c.aileron_deflection_deg, wid))
                if self.tail_type == "none":
                    out.append(ControlSurfaceDef("drag_rudder", "wing", side, outer1 + 0.015, 0.985,
                                                 c.aileron_chord, max(0.0, side * c.rudder_deflection_deg), wid))
                continue
            a0 = max(c.aileron_start, root + 0.1)
            a1 = max(c.aileron_end, a0 + 0.05)
            f1 = min(c.flap_end, a0 - 0.015)
            if self.layout == "canard":
                f1 = min(f1, root + 0.3)  # canard aircraft: limited flaps (pitch trim)
            if f1 > root + 0.05:
                out.append(ControlSurfaceDef("flap", "wing", side, root, f1, c.flap_chord, c.flap_deflection_deg, wid))
            out.append(ControlSurfaceDef("aileron", "wing", side, a0, min(a1, 0.98), c.aileron_chord,
                                         side * c.aileron_deflection_deg, wid))
        for s in self.surfaces:
            if s.role == "htail":
                h = s.surface
                if self.is_t_tail or self.layout == "twin_boom":
                    h_root = 0.04
                else:
                    hx = s.placement.position[0] + 0.7 * h.root_chord
                    h_root = h.eta_of(float(fus.half_width(min(hx, fus.length)))) + 0.02
                for side in (1, -1):
                    out.append(ControlSurfaceDef("elevator", "htail", side, min(h_root, 0.3), 0.96, c.elevator_chord,
                                                 c.elevator_deflection_deg, s.component_id(side)))
            elif s.role == "canard":
                hx = s.placement.position[0] + 0.7 * s.surface.root_chord
                c_root = s.surface.eta_of(float(fus.half_width(hx))) + 0.03
                for side in (1, -1):
                    out.append(ControlSurfaceDef("elevator", "canard", side, min(c_root, 0.4), 0.95,
                                                 c.elevator_chord, c.elevator_deflection_deg, s.component_id(side)))
            elif s.role == "v_tail":
                for side in (1, -1):  # ruddervators mix pitch and yaw
                    out.append(ControlSurfaceDef("ruddervator", "vtail", side, 0.08, 0.94, c.elevator_chord,
                                                 c.elevator_deflection_deg + side * c.rudder_deflection_deg,
                                                 s.component_id(side)))
            elif s.role == "fin":
                end = 0.90 if self.is_t_tail else 0.94
                out.append(ControlSurfaceDef("rudder", s.key, 1, 0.05, end, c.rudder_chord, c.rudder_deflection_deg,
                                             s.key))
        return out

    def control_area(self, kind: str) -> float:
        return sum(self.surface(cs.surface).strip_area(cs.eta0, cs.eta1, cs.chord_fraction)
                   for cs in self.control_surfaces if cs.kind == kind)

    def wing_control_area(self) -> float:
        return sum(self.wing.strip_area(cs.eta0, cs.eta1, cs.chord_fraction)
                   for cs in self.control_surfaces if cs.surface == "wing")

    def flapped_area_fraction(self) -> float:
        """Wing reference area spanned by flaps (both sides) / S."""
        w = self.wing
        return sum(w.strip_area(cs.eta0, cs.eta1) for cs in self.control_surfaces if cs.kind == "flap") / w.area

    # ------------------------------------------------------------ engines
    @cached_property
    def engines(self) -> list[EngineInstance]:
        """Engine instances; none for a glider; all moved fore / aft by engines.x_shift."""
        if self.params.configuration.propulsion == "none":
            return []
        dx = float(getattr(self.params.engines, "x_shift", 0.0))
        out = self._engines_placed()
        if dx:
            import dataclasses
            out = [dataclasses.replace(e, inlet=e.inlet + np.array([dx, 0.0, 0.0]),
                                       attach=e.attach + (np.array([dx, 0.0, 0.0]) if e.mount != "wing" else 0.0))
                   for e in out]
        return out

    def _engines_placed(self) -> list[EngineInstance]:
        cfg, e = self.params.configuration, self.params.engines
        dims = engine_dimensions(e.thrust_kn, cfg.propulsion, e.bypass_ratio)
        d, length, dp = dims["diameter"], dims["length"], dims["prop_diameter"]
        fus = self.fuselage
        out: list[EngineInstance] = []
        pairs = max(1, int(round(e.count / 2)))
        layout = cfg.engine_layout
        if layout == "nose":  # single tractor propeller: engine buried in the nose, propeller ahead of it
            inlet = np.array([0.04 * fus.length, 0.0, float(fus.centre_z(0.0))])
            return [EngineInstance(1, 1, "fuselage", inlet, min(d, 0.9 * fus.diameter), length, dp, 0.0,
                                   inlet.copy(), pusher=False)]
        if layout == "pusher" and e.count == 1:
            if cfg.propulsion == "turboprop":  # engine in the tail cone, propeller behind it
                x_in = fus.length - 0.55 * length
                inlet = np.array([x_in, 0.0, float(fus.centre_z(fus.length))])
            else:  # single jet on top of the aft fuselage (V-tail keeps clear of the exhaust)
                xc = fus.x_tail_start + 0.15 * length
                inlet = np.array([xc - 0.5 * length, 0.0, float(fus.top_z(xc)) + 0.35 * d])
            attach = np.array([inlet[0] + 0.5 * length, 0.0, float(fus.top_z(min(inlet[0] + 0.5 * length,
                                                                                  fus.length)))])
            return [EngineInstance(1, 1, "fuselage", inlet, d, length, dp, 0.0, attach, pusher=True)]
        wing_pushers = layout == "pusher" and cfg.propulsion == "turboprop"  # Avanti / Starship style
        if layout == "rear_fuselage" or (layout == "pusher" and not wing_pushers):
            xc = fus.x_tail_start + 0.1 * length
            hw, hh = float(fus.half_width(xc)), float(fus.half_height(xc))
            for i, side in enumerate((1, -1)):
                y = side * (hw + 0.62 * max(d, dp) + 0.12)
                z = float(fus.centre_z(xc)) + 0.3 * hh
                attach = np.array([xc, side * hw * 0.95, z])
                out.append(EngineInstance(i + 1, side, "fuselage", np.array([xc - length / 2, y, z]), d, length, dp,
                                          0.0, attach, pusher=layout == "pusher"))
            return out
        wing = self.wing
        if layout == "upper_aft":  # partly buried on top of the wing centre section
            y0 = float(fus.half_width(self.wing_placement.position[0] + 0.5 * wing.root_chord)) + 0.6 * d
            idx = 1
            for k in range(pairs):
                y = y0 + k * 1.35 * d
                eta = wing.eta_of(y)
                for side in (1, -1):
                    te = self.wing_point(wing.surface_point(eta, 0.95, upper=True, side=side))
                    top = self.wing_point(wing.surface_point(eta, 0.6, upper=True, side=side))
                    inlet = np.array([te[0] - 0.95 * length, side * y, top[2] + 0.1 * d])
                    out.append(EngineInstance(idx, side, "wing", inlet, d, length, dp, eta, top))
                    idx += 1
            return out
        if layout == "under_wing":  # nacelles slung directly under the wing box, inlets well aft of the LE
            idx = 1
            for k in range(pairs):
                eta = float(min(e.span_fraction + k * e.spacing_fraction, 0.8))
                for side in (1, -1):
                    le = self.wing_point(wing.surface_point(eta, 0.0, upper=False, side=side))
                    c = float(wing.chord(eta))
                    attach = self.wing_point(wing.surface_point(eta, 0.5, upper=False, side=side))
                    inlet = np.array([le[0] + 0.30 * c, le[1], attach[2] - 0.55 * d])
                    out.append(EngineInstance(idx, side, "wing", inlet, d, length, dp, eta, attach))
                    idx += 1
            return out
        e_spacing = e.spacing_fraction
        etas = [e.span_fraction + k * e_spacing for k in range(pairs)]
        front = self.params.wing.front_spar
        idx = 1
        for eta in etas:
            eta = float(min(eta, 0.85))
            c = float(wing.chord(eta))
            for side in (1, -1):
                le = self.wing_point(wing.surface_point(eta, 0.0, upper=False, side=side))
                attach = self.wing_point(wing.surface_point(eta, front, upper=False, side=side))
                if cfg.propulsion == "turboprop":
                    if layout == "pusher":
                        te = self.wing_point(wing.surface_point(eta, 1.0, upper=False, side=side))
                        inlet = np.array([te[0] - 0.75 * length, le[1], le[2] - 0.05 * d])
                    else:
                        inlet = np.array([le[0] - 0.35 * length, le[1], le[2] - 0.05 * d])
                else:
                    exit_x = le[0] + 0.15 * c
                    inlet = np.array([exit_x - length, le[1], le[2] - 0.6 * d - 0.12 * c])
                out.append(EngineInstance(idx, side, "wing", inlet, d, length, dp, eta, attach, pusher=wing_pushers))
                idx += 1
        return out

    # ------------------------------------------------------------ landing gear
    @property
    def z_ground(self) -> float:
        fus = self.fuselage
        base = -fus.radius * fus.height_ratio
        if self.has_pod:  # the centre section may hang below the pod
            w = self.wing
            low = min(self.wing_point(w.surface_point(e, x, upper=False, side=1))[2]
                      for e in (0.0, 0.1, 0.2) for x in (0.3, 0.6))
            base = min(base, low)
        return base - self.params.gear.ground_clearance

    @cached_property
    def gear(self) -> list[GearLeg]:
        g, fus = self.params.gear, self.fuselage
        dw = g.wheel_diameter
        width = 0.35 * dw
        zg = self.z_ground
        legs = []
        xn = g.nose_x_fraction * fus.length
        dn = 0.75 * dw
        legs.append(GearLeg("nose", np.array([xn, 0.0, float(fus.bottom_z(xn)) + 0.15 * fus.radius]),
                            np.array([xn, 0.0, zg + dn / 2]), dn, 0.35 * dn, 2 if g.wheels_per_leg == 2 else 1,
                            "fuselage"))
        wp = self.wing_placement.position
        xm = wp[0] + g.main_chord_fraction * self.params.wing.root_chord
        high = self.params.configuration.wing_position == "high" and not self.has_pod
        for side, name in ((1, "main_right"), (-1, "main_left")):
            y = side * g.track / 2
            if high:
                top = np.array([xm, y, float(fus.bottom_z(xm)) + 0.25 * fus.radius])
                parent = "fuselage"
            else:
                eta = self.wing.eta_of(abs(y))
                pt = self.wing_point(self.wing.point(eta, 0.6, side=side))
                top = np.array([xm, y, pt[2]])
                parent = "wing.right" if side > 0 else "wing.left"
            legs.append(GearLeg(name, top, np.array([xm, y, zg + dw / 2]), dw, width, g.wheels_per_leg, parent))
        return legs

    # ------------------------------------------------------------ geometric checks
    def engine_ground_clearance(self) -> float:
        if not self.engines:
            return np.inf
        return min(e.lowest_z for e in self.engines) - self.z_ground

    def prop_fuselage_clearance(self) -> float:
        props = [e for e in self.engines if e.prop_diameter > 0 and abs(e.inlet[1]) > 1e-6]
        if not props:
            return np.inf
        fus = self.fuselage
        return min(abs(e.inlet[1]) - e.prop_diameter / 2 - float(fus.half_width(e.inlet[0])) for e in props)
