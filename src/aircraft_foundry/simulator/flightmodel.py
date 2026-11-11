"""Six-degree-of-freedom flight model generated from an Aircraft Foundry design.

Everything is derived from the design, not hand-tuned per aircraft:

* mass, CG and inertia (Roskam radii of gyration) from the evaluated weights;
* lift slope from the wing and every pitch surface; pitch stiffness from the
  computed static margin; pitch, roll and yaw damping from the tail arms;
* elevator / elevon / ruddervator / canard, aileron and rudder power from the
  actual control-surface spans and chords (flap-effectiveness theory);
* the drag polar is the evaluator's, including transonic and supersonic wave
  drag; thrust lapses with altitude and Mach (reheat for low-bypass engines);
* the aircraft sits on its real landing-gear positions (spring-damper wheels,
  rolling friction, brakes, nose-wheel steering).

Axes: NED earth frame (x north, y east, z down); body frame x forward,
y right, z down.  Design geometry (x aft, y right, z up) maps to body axes by
(x, y, z) -> (-x, y, -z) about the CG.

This is a conceptual-design flight model: linear aerodynamics with a stall
model, no aeroelasticity, ground effect or engine dynamics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import fsolve

from ..core.evaluator import Evaluation, _Model
from ..core.mission import MissionRequirements
from ..core.parameters import AircraftParameters
from ..geometry.arrangement import Arrangement
from ..numerics import aerodynamics as aero
from ..numerics import propulsion as prop
from ..numerics import sonic_boom as sb
from ..numerics.atmosphere import G0, isa

D2R = math.pi / 180


def flap_effectiveness(chord_fraction: float) -> float:
    """Thin-aerofoil control effectiveness tau for a plain flap of the given chord fraction."""
    th = math.acos(float(np.clip(2 * chord_fraction - 1, -1, 1)))
    return 1 - (th - math.sin(th)) / math.pi


@dataclass
class AeroModel:
    S: float
    b: float
    c: float
    mass: float
    inertia: np.ndarray
    x_cg: float
    CL0: float
    CLa: float
    CLq: float
    CLde: float
    CLmax: float
    Cm0: float
    Cma: float
    Cmq: float
    Cmde: float
    CYb: float
    CYdr: float
    Clb_dihedral: float
    Clb_sweep: float
    Clp: float
    Clr_per_CL: float
    Clda: float
    Cldr: float
    Cnb: float
    Cnp_per_CL: float
    Cnr: float
    Cnda_per_CL: float
    Cndr: float
    flap_dCL: float
    flap_dCD: float
    gear_dCD: float
    thrust_sl: float  # N, all engines
    bpr: float
    propulsion: str
    reheat: bool
    gear_points: list  # (body xyz of wheel contact, is_main)
    tailless: bool
    has_fins: bool
    airframe_points: list  # body-axis nose, tail and wing-tip points (terrain-strike checks)
    design_mach: float
    supersonic_ac_shift: float  # aerodynamic-centre shift (fraction of MAC) at Mach >= 1.6
    polar: object = field(repr=False, default=None)  # evaluator _Model for cd(cl, mach)
    fuel_capacity_kg: float = 0.0
    zero_fuel_mass: float = 0.0
    n_limit: float = 2.5
    n_ultimate: float = 3.75
    design_mass: float = 1.0
    q_dive: float = 1e9  # design dive dynamic pressure, Pa
    semi_span: float = 1.0
    n_negative_limit: float = 1.0  # transports -1 g; unlimited-aerobatic designs are rated symmetrically
    wing_strength: float = 1.0  # strength reserve factor chosen for the wing
    t_tail: bool = False  # deep-stall risk: the stalled wing's wake blankets a high tailplane
    CLmin: float = -1.0  # negative stall (inverted)
    glider: bool = False  # no engine: the flap control works the airbrakes


def build_aero_model(params: AircraftParameters, mission: MissionRequirements, evaluation: Evaluation,
                     fuel_fraction: float = 0.6) -> AeroModel:
    arr = Arrangement(params)
    if params.configuration.propulsion == "none":
        from ..core.glider import GliderPolar
        polar = GliderPolar(params)
    else:
        polar = _Model(params, mission)
    w = arr.wing
    S, b, c = w.area, w.span, w.mac
    m = evaluation.metrics
    weights = evaluation.weights
    fuel_capacity = weights.get("fuel", 0.0)
    mass = m["mtow_kg"] - (1 - fuel_fraction) * fuel_capacity
    x_cg = m["x_cg_m"]
    L = arr.fuselage.length
    # Roskam radii of gyration (transports): Rx 0.25 (span), Ry 0.38 (length), Rz 0.46 ((span+length)/2)
    inertia = np.diag([mass * (b * 0.25 / 2) ** 2, mass * (L * 0.38 / 2) ** 2, mass * ((b + L) / 2 * 0.46 / 2) ** 2])
    M_low = 0.3
    a_w = aero.lift_curve_slope(w.aspect_ratio, M_low, w.sweep_at(0.5))
    eps = min(2 * a_w / (math.pi * w.aspect_ratio), 0.6)
    CLa, CLq, Cmq = a_w, 0.0, -0.4 * a_w if arr.is_tailless else -0.1 * a_w
    for ps in arr.pitch_surfaces():
        t = ps.surface
        a_t = aero.lift_curve_slope(t.aspect_ratio, M_low, t.sweep_at(0.5))
        factor = 1 + 0.5 * eps if ps.role == "canard" else 1 - eps
        st = ps.area * ps.horizontal_fraction / S
        arm = (ps.x_ac - x_cg) / c
        CLa += 0.9 * a_t * factor * st
        CLq += 2 * 0.9 * a_t * st * abs(arm)
        Cmq -= 2 * 0.9 * a_t * st * arm**2
    sm = m.get("static_margin", 0.1)
    Cma = -CLa * sm
    # pitch control: every control surface on a pitch surface (or elevons)
    CLde = Cmde = 0.0
    Clda = 0.0
    for cs in arr.control_surfaces:
        surf = arr.surface(cs.surface)
        ps = arr.placed(cs.surface)
        tau = flap_effectiveness(cs.chord_fraction)
        a_s = aero.lift_curve_slope(surf.aspect_ratio, M_low, surf.sweep_at(0.5))
        strip = surf.strip_area(cs.eta0, cs.eta1) * (ps.horizontal_fraction if ps.role != "wing" else 1.0)
        em = 0.5 * (cs.eta0 + cs.eta1)
        x_cs = ps.placement.position[0] + float(surf.x_le(em)) + 0.25 * float(surf.chord(em))
        if cs.kind in ("elevator", "elevon_inner", "elevon_outer", "ruddervator"):
            dcl = 0.9 * a_s * tau * strip / S
            sign = 1.0 if ps.role == "canard" else -1.0  # pitch-up input: canard TE down, aft surfaces TE up
            CLde += sign * dcl
            Cmde += -sign * dcl * (x_cs - x_cg) / c
        if cs.kind in ("aileron", "elevon_outer"):
            ys = np.linspace(cs.eta0, cs.eta1, 20) * surf.semi_span
            integral = float(np.trapezoid(surf.chord(ys / surf.semi_span) * ys, ys))
            Clda += a_s * tau * integral / (S * b)  # each side; antisymmetric pair counted once per side
    # directional: fins, V-tail sin^2 share, fuselage Munk moment
    vol_fus = arr.fuselage.volume
    Cnb = -1.3 * vol_fus / (S * b) * (0.5 if arr.has_pod else 1.0)  # Munk moment; blended pods half-buried
    CYb = -0.3 * arr.fuselage.length * arr.fuselage.diameter / S
    Cnr = -0.02
    CYdr = Cndr = Cldr = 0.0
    has_fins = bool(arr.yaw_surfaces())
    for ps in arr.yaw_surfaces():
        t = ps.surface
        a_v = aero.lift_curve_slope(2 * t.aspect_ratio if ps.surface.vertical else t.aspect_ratio, M_low,
                                    t.sweep_at(0.5))
        sv = ps.area * ps.vertical_fraction / S
        lv = (ps.x_ac - x_cg) / b
        zv = 0.4 * t.semi_span / b
        Cnb += 0.9 * a_v * sv * lv
        CYb -= 0.9 * a_v * sv
        Cnr -= 2 * 0.9 * a_v * sv * lv**2
        rud = [cs for cs in arr.control_surfaces if cs.surface == ps.key and cs.kind in ("rudder", "ruddervator")]
        for cs in rud:
            tau = flap_effectiveness(cs.chord_fraction)
            frac = t.strip_area(cs.eta0, cs.eta1) / t.area
            dcy = 0.9 * a_v * tau * sv * frac * (0.5 if ps.role == "v_tail" else 1.0)
            CYdr += -dcy  # yaw-right input: rudder TE to starboard, side force to port at the tail
            Cndr += dcy * lv
            Cldr += dcy * zv
    for cs in arr.control_surfaces:  # split drag rudders on tailless aircraft
        if cs.kind == "drag_rudder":
            area = w.strip_area(cs.eta0, cs.eta1)
            Cndr += 1.2 * (area / S) * (0.9 * w.semi_span / b) / 2
    lam = w.taper_ratio
    gamma = w.dihedral_deg * D2R
    Clb_dihedral = -(a_w * gamma / 6) * (1 + 2 * lam) / (1 + lam) - (0.0003 / D2R
                                                                     if params.configuration.wing_position == "high"
                                                                     else 0.0)
    Clb_sweep = -0.25 * math.tan(w.sweep_at(0.25))
    Clp = -(a_w / 12) * (1 + 3 * lam) / (1 + lam)
    # high-lift and gear
    flap_frac = arr.flapped_area_fraction()
    flap_dCL = 1.0 * flap_frac / 0.61 * math.sqrt(max(params.controls.flap_chord, 0.05) / 0.3) if flap_frac else 0.0
    # landing gear contact points (body axes about the CG)
    pts = []
    for leg in arr.gear:
        bottom = leg.axle - np.array([0, 0, leg.wheel_diameter / 2])
        pts.append((np.array([-(bottom[0] - x_cg), bottom[1], -(bottom[2] - 0.0)]), leg.name.startswith("main")))
    fus = arr.fuselage
    geo_pts = [np.array([0.0, 0.0, float(fus.centre_z(0.0))]),
               np.array([fus.length, 0.0, float(fus.centre_z(fus.length))])]
    geo_pts += [arr.wing_tip_le(side) for side in (1, -1)]
    airframe = [np.array([-(g[0] - x_cg), g[1], -g[2]]) for g in geo_pts]
    cruise_cl = m.get("cruise_cl", 0.4)
    CL0 = 0.05 if mission.cruise_mach > 1 else 0.2
    alpha_c = (min(cruise_cl, 0.8) - CL0) / CLa
    Cm0 = -Cma * alpha_c
    return AeroModel(S=S, b=b, c=c, mass=mass, inertia=inertia, x_cg=x_cg, CL0=CL0, CLa=CLa, CLq=CLq,
                     # clmax_landing already includes the flaps: keep the clean value separate
                     CLde=CLde, CLmax=max(1.15, m.get("clmax_landing", 1.5) - flap_dCL), Cm0=Cm0, Cma=Cma, Cmq=Cmq,
                     Cmde=Cmde, CYb=CYb, CYdr=CYdr, Clb_dihedral=Clb_dihedral, Clb_sweep=Clb_sweep, Clp=Clp,
                     Clr_per_CL=0.25, Clda=Clda, Cldr=Cldr, Cnb=Cnb, Cnp_per_CL=-0.125, Cnr=Cnr,
                     Cnda_per_CL=-0.1 * Clda, Cndr=Cndr, flap_dCL=flap_dCL, flap_dCD=0.03 * flap_frac / 0.6,
                     gear_dCD=0.015, thrust_sl=len(arr.engines) * params.engines.thrust_kn * 1000.0,
                     bpr=params.engines.bypass_ratio, propulsion=params.configuration.propulsion,
                     reheat=params.engines.bypass_ratio <= 1.5 and mission.cruise_mach > 1.0,
                     gear_points=pts, tailless=arr.is_tailless, has_fins=has_fins, airframe_points=airframe,
                     design_mach=mission.cruise_mach,
                     supersonic_ac_shift=0.12 if mission.cruise_mach > 1 else 0.0, polar=polar,
                     fuel_capacity_kg=fuel_capacity, zero_fuel_mass=m["mtow_kg"] - fuel_capacity,
                     n_limit=params.structure.limit_load_factor,
                     n_ultimate=params.structure.limit_load_factor * params.structure.safety_factor,
                     design_mass=m["mtow_kg"], semi_span=w.semi_span,
                     wing_strength=params.structure.wing_strength,
                     n_negative_limit=abs(getattr(params.structure, "negative_load_factor", -1.0)),
                     t_tail=bool(arr.is_t_tail), glider=params.configuration.propulsion == "none",
                     CLmin=-(max(1.15, m.get("clmax_landing", 1.5) - flap_dCL) - 1.6 * params.wing.camber * 6.0),
                     # design dive speed V_D ~ 1.5 x cruise equivalent airspeed (A320: ~250 KEAS cruise,
                     # V_D ~ 381 KEAS); dynamic pressure is the structural measure
                     q_dive=(0.5 * 1.225 * (1.11 * m["vne_ms"]) ** 2 if "vne_ms" in m else  # gliders: from VNE
                             (1.5 ** 2) * 0.5 * isa(mission.cruise_altitude_m).density * m["cruise_speed_ms"] ** 2))


# ---------------------------------------------------------------------------------------------------- dynamics
def quat_to_dcm(q: np.ndarray) -> np.ndarray:
    """Body -> NED rotation matrix from quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def euler_to_quat(phi: float, theta: float, psi: float) -> np.ndarray:
    cp, sp = math.cos(phi / 2), math.sin(phi / 2)
    ct, st = math.cos(theta / 2), math.sin(theta / 2)
    cs, ss = math.cos(psi / 2), math.sin(psi / 2)
    return np.array([cp * ct * cs + sp * st * ss, sp * ct * cs - cp * st * ss,
                     cp * st * cs + sp * ct * ss, cp * ct * ss - sp * st * cs])


def quat_to_euler(q: np.ndarray) -> tuple[float, float, float]:
    w, x, y, z = q
    phi = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    theta = math.asin(float(np.clip(2 * (w * y - z * x), -1, 1)))
    psi = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return phi, theta, psi


@dataclass
class Controls:
    elevator: float = 0.0  # -1..1, +1 = pitch up
    aileron: float = 0.0  # +1 = roll right
    rudder: float = 0.0  # +1 = yaw right
    throttle: float = 0.0  # 0..1 (> 0.97 lights reheat on low-bypass engines)
    flaps: float = 0.0  # 0..1
    brakes: bool = False
    gear_down: bool = True
    sas: bool = True  # yaw damper / pitch damper (stability augmentation)
    pitch_trim: float = 0.0

    MAX_ELEVATOR = 20 * D2R
    MAX_AILERON = 20 * D2R
    MAX_RUDDER = 25 * D2R


class FlightSimulator:
    """Rigid-body integrator; ``state`` = [pos NED (3), vel body (3), quat (4), omega body (3)]."""

    def __init__(self, model: AeroModel, terrain=None):
        self.m = model
        self.terrain = terrain  # object with surface_height(north, east); None = flat ground at 0 m
        self.controls = Controls(sas=bool(model.tailless or not model.has_fins or model.Cnb < 0.02))
        self.state = np.zeros(13)
        self.state[6] = 1.0
        self.time = 0.0
        self.crashed = ""
        self.failure_msg = ""
        self.last_forces: dict = {}
        self.airborne_since: float | None = None
        self.touchdown: dict | None = None
        self.landing: dict | None = None
        self.fuel = max(model.mass - model.zero_fuel_mass, 0.0)
        self.wind_ned = np.zeros(3)  # steady wind (m/s, NED, the direction the air moves *to*)
        self.turbulence = 0.0  # rms gust velocity, m/s
        self.gust = np.zeros(3)
        self._rng = np.random.default_rng(11)
        self.failure = ""  # "", "left wing", "right wing", "tail"
        self.event: dict | None = None  # what happened at a crash / failure (for the debrief)
        self.launch: dict | None = None  # glider launch in progress (winch / aerotow)
        self.thermals: list = []  # (north, east, radius, core m/s, top altitude)
        self.updraft = 0.0
        self._sep, self._sep_target, self._sep_frozen = 1.0, 1.0, False
        self._buffet_dcl, self._near_stall = 0.0, 0.0
        self.stress = {"left": 0.0, "right": 0.0, "q": 0.0}
        self.distance_flown = 0.0
        self.fuel_flow = 0.0

    @property
    def mass(self) -> float:
        return self.m.zero_fuel_mass + max(self.fuel, 0.0)

    def set_fuel_fraction(self, fraction: float) -> None:
        self.fuel = float(np.clip(fraction, 0.0, 1.0)) * self.m.fuel_capacity_kg

    def ground(self, north: float, east: float) -> float:
        return self.terrain.surface_height(north, east) if self.terrain is not None else 0.0

    # ------------------------------------------------------------ aerodynamics & forces
    def forces(self, s: np.ndarray, ctl: Controls) -> tuple[np.ndarray, np.ndarray, dict]:
        md = self.m
        pos, v, q, w = s[0:3], s[3:6], s[6:10], s[10:13]
        alt = -pos[2]
        atm = isa(max(alt, -100.0))
        R0 = quat_to_dcm(q / np.linalg.norm(q))
        rising = np.array([0.0, 0.0, -self.updraft])  # thermals: NED "down" negative = rising air
        v_air = v - R0.T @ (self.wind_ned + self.gust + rising)  # aerodynamics see the air-relative velocity
        V = max(float(np.linalg.norm(v_air)), 0.1)
        u, vv, ww = v_air
        alpha = math.atan2(ww, max(u, 1e-3))
        beta = math.asin(float(np.clip(vv / V, -1, 1)))
        mach = V / atm.speed_of_sound
        qbar = 0.5 * atm.density * V * V
        p, qr, r = w
        ph, qh, rh = p * md.b / (2 * V), qr * md.c / (2 * V), r * md.b / (2 * V)
        de = (ctl.elevator + ctl.pitch_trim) * Controls.MAX_ELEVATOR
        da = ctl.aileron * Controls.MAX_AILERON
        dr = ctl.rudder * Controls.MAX_RUDDER
        if ctl.sas:
            # Stability augmentation as on fly-by-wire aircraft (B-2 style for tailless designs):
            # yaw-rate damping around the coordinated-turn rate, plus sideslip feedback that
            # supplies the weathervane stability a finless aircraft lacks.
            qn = q / np.linalg.norm(q)
            phi = math.atan2(2 * (qn[0] * qn[1] + qn[2] * qn[3]), 1 - 2 * (qn[1] ** 2 + qn[2] ** 2))
            rh_cmd = G0 * math.sin(phi) / V * md.b / (2 * V)
            dr += Controls.MAX_RUDDER * float(np.clip(-40 * (rh - rh_cmd) + 4.0 * beta, -1.0, 1.0))
            de += float(np.clip(-0.8 * qh * 40 * Controls.MAX_ELEVATOR, -0.4 * Controls.MAX_ELEVATOR,
                                0.4 * Controls.MAX_ELEVATOR))
        # lift: attached flow blends into separated (flat-plate) flow through the stall
        cl_lin = md.CL0 + md.CLa * alpha + md.CLq * qh + md.CLde * de + md.flap_dCL * ctl.flaps
        clmax = md.CLmax + md.flap_dCL * ctl.flaps
        clmin = md.CLmin
        lim = clmax if cl_lin >= 0 else -clmin
        width = 0.04 * lim  # transition centred just past CLmax, so the curve rounds over right at CLmax
        target = 1.0 / (1.0 + math.exp(max(min((abs(cl_lin) - 1.1 * lim) / width, 40.0), -40.0)))
        sep = min(self._sep, target) if self._sep_frozen else target  # separation memory (set in step)
        a_eff = cl_lin / md.CLa
        cl_att = math.copysign(lim - 0.08 * lim * math.log1p(math.exp(-(abs(cl_lin) - lim) / (0.08 * lim)))
                               if abs(cl_lin) > 0.7 * lim else abs(cl_lin), cl_lin)  # smooth cap at CLmax
        cl_sep = 0.9 * math.sin(2.0 * a_eff)  # flat plate
        cl = sep * cl_att + (1.0 - sep) * cl_sep + self._buffet_dcl
        stalled = sep < 0.5
        self._sep_target = target
        self._near_stall = abs(cl_lin) / max(lim, 1e-6)
        cd = md.polar.cd(min(abs(cl), 1.5), max(min(mach, 3.0), 0.05))[0]
        if md.glider:  # airbrakes: drag and spoiled lift (the "flaps" input)
            cd += 0.045 * ctl.flaps + (0.0015 if ctl.gear_down else 0.0)  # faired fixed wheel: little drag
            cl -= 0.18 * ctl.flaps * math.copysign(1.0, cl)
        else:
            cd += md.flap_dCD * ctl.flaps + (md.gear_dCD if ctl.gear_down else 0.0)
        cd += (1.0 - sep) * 1.8 * math.sin(a_eff) ** 2  # separated flow: drag of an inclined plate
        cd += 0.02 * abs(dr) if (not md.has_fins) else 0.0  # drag rudders open
        if self.failure in ("left wing", "right wing"):  # one wing gone: half the lift, violent roll
            side = 1.0 if self.failure == "right wing" else -1.0
            cl *= 0.5
            cd += 0.05
        cy = md.CYb * beta + md.CYdr * dr
        clb = md.Clb_dihedral + md.Clb_sweep * cl
        # stalled wings lose, then reverse, their roll damping (autorotation): sideslip or yaw drops a wing
        # Autorotation is self-limiting: the reversed damping only acts for moderate roll rates
        # (pb/2V up to ~0.1); beyond that the wing is damped again.  This is what sustains a spin.
        autorot = -1.6 * (1.0 - sep) * md.Clp * ph * math.exp(-(ph / 0.1) ** 2)
        # yawing right speeds up the left wing: the right wing stalls deeper and drops (spins follow the rudder)
        wing_drop = 0.04 * (1.0 - sep) * math.tanh(8.0 * rh - 3.0 * beta)
        c_l = clb * beta + md.Clp * ph + autorot + md.Clr_per_CL * cl * rh + md.Clda * da * (0.4 + 0.6 * sep) \
            + md.Cldr * dr + wing_drop
        ac_shift = md.supersonic_ac_shift * float(np.clip((mach - 0.9) / 0.7, 0, 1))
        cm = md.Cm0 + (md.Cma - md.CLa * ac_shift) * alpha + md.Cmq * qh + md.Cmde * de
        if md.t_tail:  # deep stall: far past the stall the wing's wake blankets the tailplane -> nose stays up
            deep = (1.0 - sep) * float(np.clip((abs(a_eff) - lim / md.CLa - 0.06) / 0.12, 0.0, 1.0))
            cm = (1.0 - deep) * cm + deep * (0.08 + 0.1 * md.Cmde * de)  # blanketed elevator: little authority
        else:  # conventional pitch break: the nose drops as the wing stalls
            cm -= 0.12 * (1.0 - sep) * math.copysign(1.0, cl_lin)
        if self.failure in ("left wing", "right wing"):
            c_l += side * 0.25 * abs(cl) + side * 0.15
        if self.failure == "tail":  # stabiliser gone: pitch-unstable, no elevator
            cm = 0.3 * alpha - md.Cmde * de + 0.02
        cn = md.Cnb * beta + md.Cnp_per_CL * cl * ph + md.Cnr * rh + md.Cnda_per_CL * cl * da + md.Cndr * dr
        ca, sa = math.cos(alpha), math.sin(alpha)
        lift, drag, side = qbar * md.S * cl, qbar * md.S * cd, qbar * md.S * cy
        F = np.array([-drag * ca + lift * sa, side, -drag * sa - lift * ca])
        M = qbar * md.S * np.array([md.b * c_l, md.c * cm, md.b * cn])
        # thrust
        lapse = prop.thrust_lapse(atm.sigma, mach, md.propulsion, md.bpr)
        reheat = md.reheat and ctl.throttle > 0.97
        thrust = md.thrust_sl * lapse * ctl.throttle * (1.25 if reheat else 1.0)
        if self.fuel <= 0:
            thrust = 0.0  # flame-out
        if self.failure in ("left wing", "right wing"):
            thrust *= 0.5  # the engines on the lost wing went with it
        F[0] += thrust
        # gravity
        R = R0
        mass = self.mass
        F += R.T @ np.array([0.0, 0.0, mass * G0])
        if self.launch is not None:  # winch cable or tow rope
            F += R.T @ self._launch_force(pos, R @ v, V)
        # landing gear
        on_ground = False
        for pt, is_main in md.gear_points if ctl.gear_down else []:
            p_ned = pos + R @ pt
            pen = p_ned[2] + self.ground(p_ned[0], p_ned[1])  # positive = below the terrain surface
            if pen <= 0:
                continue
            on_ground = True
            v_pt = R @ (v + np.cross(w, pt))
            k = md.mass * G0 / 0.25
            # spring + damper: v_pt[2] > 0 means the wheel is moving further into the ground
            n_force = max(k * pen + 0.6 * math.sqrt(k * md.mass) * v_pt[2], 0.0)
            heading = R @ np.array([1.0, 0.0, 0.0])
            fwd = np.array([heading[0], heading[1], 0.0])
            fwd /= max(np.linalg.norm(fwd), 1e-6)
            if not is_main:  # nose-wheel steering with the rudder pedals
                # positive rudder (yaw right) turns the nose wheel right (heading towards east from north)
                steer = ctl.rudder * 30 * D2R * float(np.clip(1 - V / 40, 0, 1))
                cs_, sn_ = math.cos(steer), math.sin(steer)
                fwd = np.array([cs_ * fwd[0] - sn_ * fwd[1], sn_ * fwd[0] + cs_ * fwd[1], 0.0])
            lat = np.array([-fwd[1], fwd[0], 0.0])
            mu = 0.45 if (ctl.brakes and is_main) else 0.02
            f_long = -mu * n_force * math.copysign(1.0, float(v_pt @ fwd)) if abs(v_pt @ fwd) > 0.05 else \
                -float(v_pt @ fwd) * mass * 0.5
            f_lat = float(np.clip(-(v_pt @ lat) * mass * 2.0, -0.7 * n_force, 0.7 * n_force))
            f_ned = np.array([0.0, 0.0, -n_force]) + f_long * fwd + f_lat * lat
            f_body = R.T @ f_ned
            F += f_body
            M += np.cross(pt, f_body)
        if md.glider:
            # wing-tip skids: a glider rests on one tip; the tip slides with friction (not a crash)
            for pt in md.airframe_points[2:4]:
                p_ned = pos + R @ pt
                pen = p_ned[2] + self.ground(p_ned[0], p_ned[1])
                if pen > 0:
                    v_pt = R @ (v + np.cross(w, pt))
                    fz = -(mass * G0 * 8.0 * min(pen, 0.3) + mass * 3.0 * max(v_pt[2], 0.0))
                    vh = v_pt[:2]
                    sp = float(np.linalg.norm(vh))
                    fh = -0.4 * abs(fz) * vh / sp if sp > 0.05 else -mass * 2.0 * vh
                    f_body = R.T @ np.array([fh[0], fh[1], fz])
                    F += f_body
                    M += np.cross(pt, f_body)
            if self.launch is not None and on_ground_now(self, pos) and V < 12.0:
                # the wing runner holds the wings level until the ailerons work
                phi = math.atan2(2 * (q[0] * q[1] + q[2] * q[3]), 1 - 2 * (q[1] ** 2 + q[2] ** 2))
                M[0] += -md.inertia[0, 0] * (25.0 * phi + 10.0 * w[0])
        info = {"alpha": alpha, "beta": beta, "mach": mach, "V": V, "qbar": qbar, "cl": cl, "cd": cd,
                "thrust": thrust, "reheat": reheat, "stalled": stalled, "on_ground": on_ground, "alt": alt,
                "lift": lift, "drag": drag, "roll_moment": qbar * md.S * md.b * md.Clda * da}
        return F, M, info

    def derivative(self, s: np.ndarray, ctl: Controls) -> np.ndarray:
        md = self.m
        F, M, info = self.forces(s, ctl)
        v, q, w = s[3:6], s[6:10], s[10:13]
        q = q / np.linalg.norm(q)
        R = quat_to_dcm(q)
        I = md.inertia * (self.mass / md.mass)
        ds = np.empty(13)
        ds[0:3] = R @ v
        ds[3:6] = F / self.mass - np.cross(w, v)
        p, qq, r = w
        omega = np.array([[0, -p, -qq, -r], [p, 0, r, -qq], [qq, -r, 0, p], [r, qq, -p, 0]])
        ds[6:10] = 0.5 * omega @ q
        ds[10:13] = np.linalg.solve(I, M - np.cross(w, I @ w))
        self.last_forces = info
        return ds

    def step(self, dt: float) -> None:
        if self.crashed:
            return
        s, ctl = self.state, self.controls
        k1 = self.derivative(s, ctl)
        k2 = self.derivative(s + 0.5 * dt * k1, ctl)
        k3 = self.derivative(s + 0.5 * dt * k2, ctl)
        k4 = self.derivative(s + dt * k3, ctl)
        self.state = s + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        # separation builds up / reattaches over ~0.3 s; buffet shakes the airframe near the stall
        self._sep += (self._sep_target - self._sep) * min(dt / 0.3, 1.0)
        self._sep_frozen = True
        nb = float(np.clip((self._near_stall - 0.88) / 0.12, 0.0, 1.0))
        self._buffet_dcl = 0.05 * nb * float(self._rng.standard_normal())
        self.state[6:10] /= np.linalg.norm(self.state[6:10])
        self.time += dt
        self.derivative(self.state, ctl)  # refresh telemetry at the new state
        f = self.last_forces
        tsfc = prop.tsfc_per_hour(f.get("mach", 0.3), self.m.propulsion, self.m.bpr)
        self.fuel_flow = f.get("thrust", 0.0) / G0 * tsfc / 3600.0 * (2.0 if f.get("reheat") else 1.0)  # kg/s
        self.fuel = max(self.fuel - self.fuel_flow * dt, 0.0)
        if self.turbulence > 0:  # first-order filtered gusts (~3 s correlation)
            self.gust += -self.gust * dt / 3.0 + self.turbulence * math.sqrt(2 * dt / 3.0) * self._rng.standard_normal(3)
            self.gust[2] *= 0.6
        R = quat_to_dcm(self.state[6:10])
        v_ned = R @ self.state[3:6]
        self.distance_flown += float(np.hypot(v_ned[0], v_ned[1])) * dt
        if self.launch is not None and self.launch.get("done"):
            if self.launch.get("weak"):
                self.release("weak link broke - land ahead or fly a short circuit")
            elif self.launch.get("stalled_release"):
                self.release("released after a stall on the cable - lower the nose")
            else:
                self.release("released at the top")
        p_ = self.state[0:3]
        self.updraft = self.updraft_at(p_[0], p_[1], -p_[2]) if self.thermals else 0.0
        self._check_structure()
        self._check_crash()
        if (self.crashed or self.failure) and self.event is None:  # snapshot for the debrief
            t = self.telemetry()
            f = self.last_forces
            v_ned = quat_to_dcm(self.state[6:10]) @ self.state[3:6]
            self.event = {**t, "kind": "failure" if self.failure else "crash", "message": self.crashed or self.failure_msg,
                          "failure": self.failure, "sink_ms": float(v_ned[2]), "agl": self.height_above_ground(),
                          "stalled": bool(f.get("stalled")), "cl": float(f.get("cl", 0.0)), "stress": dict(self.stress),
                          "mass": self.mass}

    def _check_structure(self) -> None:
        """Wing-root bending per side (load factor + aileron rolling moment) and overspeed."""
        if self.failure or self.crashed:
            return
        md, f = self.m, self.last_forces
        if not f or f.get("on_ground"):
            self.stress = {"left": 0.0, "right": 0.0, "q": 0.0}
            return
        n_z = f["lift"] * math.cos(f["alpha"]) / (self.mass * G0)
        y_cp = 0.42 * md.semi_span
        m0 = n_z * self.mass * G0 / 2 * y_cp  # root bending per side from lift
        dm = 0.5 * f.get("roll_moment", 0.0)  # rolling right loads the left wing more
        cap_pos = md.wing_strength * md.n_ultimate * md.design_mass * G0 / 2 * y_cp
        cap_neg = md.wing_strength * md.n_negative_limit * md.n_ultimate / md.n_limit * md.design_mass * G0 / 2 * y_cp
        def util(m):
            return m / cap_pos if m >= 0 else -m / cap_neg
        self.stress = {"left": util(m0 + dm), "right": util(m0 - dm), "q": f["qbar"] / md.q_dive}
        if self.stress["left"] > 1.0 or self.stress["right"] > 1.0:
            side = "left" if self.stress["left"] >= self.stress["right"] else "right"
            self.failure = f"{side} wing"
            if n_z >= 0:
                limits = f"ultimate {md.n_ultimate * md.wing_strength:.2f} g, limit {md.n_limit:.1f} g"
            else:
                limits = (f"negative ultimate {-md.n_negative_limit * md.n_ultimate / md.n_limit:.1f} g, "
                          f"limit {-md.n_negative_limit:.1f} g")
            self.failure_msg = f"STRUCTURAL FAILURE: {side} wing broke at {n_z:.1f} g ({limits})"
        elif self.stress["q"] > 1.21:  # ~10 % beyond V_D
            self.failure = "tail"
            self.failure_msg = (f"STRUCTURAL FAILURE: overspeed {f['V'] * 1.944:.0f} kt — tail flutter "
                                f"beyond the design dive speed")

    @property
    def max_stress(self) -> float:
        return max(self.stress["left"], self.stress["right"], self.stress["q"] / 1.21)

    def _check_crash(self) -> None:
        pos, q = self.state[0:3], self.state[6:10]
        R = quat_to_dcm(q)
        v_ned = R @ self.state[3:6]
        phi, theta, _ = quat_to_euler(q)
        on_ground = bool(self.last_forces.get("on_ground"))
        names = ("nose", "tail", "right wing tip", "left wing tip")
        for name, pt in zip(names, self.m.airframe_points):
            p = pos + R @ pt
            if self.m.glider and "wing tip" in name:  # tip skids: only a fast or hard contact is a crash
                v_tip = v_ned + R @ np.cross(self.state[10:13], pt)
                if np.linalg.norm(v_tip[:2]) < 25.0 and v_tip[2] < 3.0 and p[2] + self.ground(p[0], p[1]) < 0.6:
                    continue
            if p[2] + self.ground(p[0], p[1]) > 0.3:
                where = "the terrain" if not on_ground else "the ground"
                self.crashed = f"Crashed: the {name} hit {where}"
                return
        if pos[2] + self.ground(pos[0], pos[1]) > 0.5:
            self.crashed = "Crashed: flew into the ground"
        elif on_ground and v_ned[2] > 4.0:
            self.crashed = f"Crashed: touched down at {v_ned[2]:.1f} m/s sink rate (limit ~3 m/s)"
        elif on_ground and not self.m.glider and (abs(phi) > 15 * D2R or theta > 18 * D2R or theta < -10 * D2R):
            self.crashed = "Crashed: wing or tail strike"
        self._track_landing(on_ground, v_ned, phi, theta)

    def _track_landing(self, on_ground: bool, v_ned: np.ndarray, phi: float, theta: float) -> None:
        """Record the first touchdown after a real flight and rate it once the aircraft stops."""
        if not on_ground:
            if self.airborne_since is None:
                self.airborne_since = self.time
            return
        flown = self.airborne_since is not None and self.time - self.airborne_since > 8.0
        if flown and self.touchdown is None and not self.crashed:
            pos = self.state[0:3]
            self.touchdown = {"time": self.time, "sink_ms": float(v_ned[2]), "north": float(pos[0]),
                              "east": float(pos[1]), "speed": float(np.hypot(v_ned[0], v_ned[1])),
                              "pitch_deg": theta / D2R, "roll_deg": phi / D2R}
        if not flown:
            self.airborne_since = None  # bounced or never really left the ground
        if self.touchdown is not None and self.landing is None and np.hypot(v_ned[0], v_ned[1]) < 1.0:
            self.landing = rate_landing(self.touchdown, self.state[0:3],
                                        runways=getattr(self.terrain, "runways", None))

    # ------------------------------------------------------------ set-ups
    # ------------------------------------------------------------------ gliders: launches and thermals
    def start_launch(self, kind: str = "winch") -> bool:
        """Winch launch (cable to a winch ~1.1 km down the runway) or aerotow (to ~600 m)."""
        if not self.last_forces.get("on_ground") and self.height_above_ground() > 8:
            return False
        hdg = math.atan2(*(quat_to_dcm(self.state[6:10]) @ np.array([1.0, 0.0, 0.0]))[1::-1][::-1]) \
            if False else self.telemetry()["heading"] * D2R
        n, e = self.state[0], self.state[1]
        winch = np.array([n + 1100 * math.cos(hdg), e + 1100 * math.sin(hdg), 0.0])
        winch[2] = -self.ground(winch[0], winch[1])
        self.launch = {"kind": kind, "t0": self.time, "winch": winch, "heading": hdg}
        self.controls.brakes = False
        return True

    def release(self, why: str = "released") -> None:
        if self.launch is not None:
            self.launch_result = f"{self.launch['kind'].capitalize()} {why} at {self.height_above_ground():.0f} m"
        self.launch = None

    def _launch_force(self, pos, v_ned, V) -> np.ndarray:
        L = self.launch
        m = self.mass
        ramp = min((self.time - L["t0"]) / 2.0, 1.0)
        agl = -pos[2] - self.ground(pos[0], pos[1])
        if L["kind"] == "winch":
            d = L["winch"] - pos
            dist = float(np.linalg.norm(d))
            # cable angle below the horizon (NED: the winch is *below*, so d[2] > 0)
            elev = math.degrees(math.atan2(d[2], math.hypot(d[0], d[1])))
            ahead = d[0] * math.cos(L["heading"]) + d[1] * math.sin(L["heading"])
            # the hook back-releases once the cable gets steep or pulls from behind
            if dist < 60 or (agl > 40 and elev > 70) or ahead < 0 or agl > 600:
                L["done"] = True
                return np.zeros(3)
            # The winch driver holds launch speed (~1.6 x the 1-g stall speed: ~105 km/h for a K-21) and eases
            # the power off as the cable steepens: the cable's downward pull adds to the wing's load, so
            # full tension near the top would stall the wing at launch speed.
            md = self.m
            vs1 = math.sqrt(2 * m * G0 / (1.225 * md.S * md.CLmax))
            v_target = max(1.6 * vs1, 25.0)
            ease = max(0.25, math.cos(math.radians(max(elev, 0.0))) ** 1.5)
            L["v_target"], L["cable_deg"] = v_target, elev
            # baseline power eases off as the cable steepens; a speed deficit is answered firmly (up to 1.5 W,
            # just under the weak link)
            tension = m * G0 * float(np.clip(ease + 0.25 * (v_target - V), 0.0, 1.5))
            if self.last_forces.get("stalled") and agl > 20:
                L["stall_t"] = L.get("stall_t", self.time)
                if self.time - L["stall_t"] > 0.8:  # a stalled glider on the cable: the pilot releases
                    L["done"], L["stalled_release"] = True, True
                    return np.zeros(3)
            else:
                L.pop("stall_t", None)
            if tension * ramp > 1.6 * m * G0:  # weak link (well above normal launch loads)
                L["done"], L["weak"] = True, True
                return np.zeros(3)
            return d / dist * tension * ramp
        if agl > 600:
            L["done"] = True
            return np.zeros(3)
        h = L["heading"]
        drag = self.last_forces.get("drag", 0.0)
        pull = drag + m * G0 * 0.065 + 0.6 * m * (33.0 - V)
        return np.array([math.cos(h), math.sin(h), -0.12]) * max(pull, 0.0) * ramp

    def make_thermals(self, centre: tuple[float, float] = (0.0, 0.0), radius: float = 15000.0, n: int = 28,
                      seed: int = 5) -> None:
        rng = np.random.default_rng(seed)
        self.thermals = [(centre[0] + rng.uniform(-radius, radius), centre[1] + rng.uniform(-radius, radius),
                          rng.uniform(150, 320), rng.uniform(1.5, 4.0), rng.uniform(1300, 2100)) for _ in range(n)]

    def updraft_at(self, north: float, east: float, alt: float) -> float:
        """Rising air (m/s): thermal cores with a sinking ring, fading under cloud base; gentle sink between."""
        if not self.thermals:
            return 0.0
        drift = self.wind_ned[:2] * self.time
        agl = alt - self.ground(north, east)
        w = -0.4  # air between thermals sinks a little
        for tn, te, r, core, top in self.thermals:
            d = math.hypot(north - tn - drift[0], east - te - drift[1]) / r
            if d > 3.0:
                continue
            fade = max(0.0, min(1.0, (top - alt) / 250.0)) * max(0.2, min(1.0, agl / 150.0))
            w += fade * (core * math.exp(-d * d) - 0.6 * math.exp(-((d - 2.0) / 0.6) ** 2))
        return w

    def height_above_ground(self) -> float:
        pos = self.state[0:3]
        return float(-pos[2] - self.ground(pos[0], pos[1]))

    def place_on_runway(self, runway=None, along: float = 100.0) -> None:
        """Line up on a runway (default: home), on the centreline ``along`` metres from the threshold."""
        from .terrain import HOME
        rw = (runway or getattr(self.terrain, "home", HOME)).departure()  # one-way mountain strips: take off away from the terrain
        md = self.m
        mains = [p for p, main in md.gear_points if main]
        others = [p for p, main in md.gear_points if not main]
        ahead = [p for p in others if p[0] > mains[0][0]]
        nose = ahead[0] if ahead else others[0]  # nose wheel if there is one, else the tail wheel
        dx = nose[0] - mains[0][0]
        dz = nose[2] - mains[0][2]
        # both wheels on the ground: tan(theta) = dz / dx (atan, not atan2: a tail wheel is *behind* the main)
        theta = math.atan(dz / dx) if abs(dx) > 1e-6 else 0.0
        q = euler_to_quat(0.0, theta, math.radians(rw.heading_deg))
        R = quat_to_dcm(q)
        h = max((R @ p)[2] for p, _ in md.gear_points)
        n0, e0 = rw.point(along)
        self.state = np.concatenate([[n0, e0, -h + 0.02 - self.ground(n0, e0)], np.zeros(3), q, np.zeros(3)])
        self._sep, self._sep_frozen, self._buffet_dcl = 1.0, False, 0.0
        self.controls = Controls(sas=self.controls.sas, gear_down=True, throttle=0.0, brakes=True)
        self.crashed = self.failure = self.failure_msg = ""
        self.time = 0.0
        self.airborne_since = self.touchdown = self.landing = None
        self.event = None
        self.distance_flown = 0.0
        self.gust[:] = 0.0
        self.derivative(self.state, self.controls)  # fresh telemetry for the new position (never stale)

    def trim(self, speed: float, altitude: float, flaps: float = 0.0, gear: bool = False
             ) -> tuple[float, float, float, bool]:
        """Level, wings-level trim: returns (alpha, elevator input, throttle, converged)."""
        ctl = Controls(flaps=flaps, gear_down=gear, sas=False)

        def residual(x):
            alpha, de, thr = x
            ctl.elevator, ctl.throttle = de, thr
            q = euler_to_quat(0.0, alpha, 0.0)
            s = np.concatenate([[0, 0, -altitude], [speed * math.cos(alpha), 0, speed * math.sin(alpha)], q,
                                np.zeros(3)])
            F, M, _ = self.forces(s, ctl)
            R = quat_to_dcm(q)
            f_ned = R @ F
            return [f_ned[0] / (self.m.mass * G0), f_ned[2] / (self.m.mass * G0), M[1] / (self.m.mass * G0 * self.m.c)]

        sol, info, ok, _ = fsolve(residual, [0.04, 0.0, 0.5], full_output=True)
        return float(sol[0]), float(sol[1]), float(sol[2]), ok == 1

    def glide_trim(self, speed: float, altitude: float) -> tuple[float, float, float]:
        """Steady glide (no thrust): (alpha, elevator, flight-path angle)."""
        md = self.m
        rho = isa(max(altitude, 0.0)).density
        cl = 2 * self.mass * G0 / (rho * speed ** 2 * md.S)
        cd = md.polar.cd(cl, 0.1)[0]
        gamma = -math.atan2(cd, cl)
        cl *= math.cos(gamma)
        alpha = (cl - md.CL0) / md.CLa
        de = -(md.Cm0 + md.Cma * alpha) / md.Cmde if md.Cmde else 0.0
        return alpha, float(np.clip(de / Controls.MAX_ELEVATOR, -1, 1)), gamma

    def start_in_flight(self, speed: float, altitude: float, heading_deg: float = 0.0, north: float = 0.0,
                        east: float = 0.0, flaps: float = 0.0, gear: bool = False) -> bool:
        """Start trimmed in level flight (or a steady glide, for gliders) at any position and configuration."""
        if self.m.glider:
            alpha, de, gamma = self.glide_trim(speed, altitude)
            q = euler_to_quat(0.0, alpha + gamma, heading_deg * D2R)
            self.state = np.concatenate([[north, east, -altitude], [speed * math.cos(alpha), 0, speed * math.sin(alpha)],
                                         q, np.zeros(3)])
            self.controls = Controls(sas=self.controls.sas, gear_down=True, throttle=0.0, pitch_trim=de)  # fixed gear
            self.crashed = self.failure = self.failure_msg = ""
            self.time = 0.0
            self.airborne_since, self.touchdown, self.landing = 0.0, None, None
            self.distance_flown = 0.0
            self.event = None
            self._sep, self._sep_frozen, self._buffet_dcl = 1.0, False, 0.0
            self.derivative(self.state, self.controls)
            return True
        alpha, de, thr, ok = self.trim(speed, altitude, flaps, gear)
        q = euler_to_quat(0.0, alpha, heading_deg * D2R)
        self.state = np.concatenate([[north, east, -altitude], [speed * math.cos(alpha), 0, speed * math.sin(alpha)], q,
                                     np.zeros(3)])
        self.controls = Controls(sas=self.controls.sas, gear_down=gear, throttle=float(np.clip(thr, 0, 1)),
                                 pitch_trim=float(np.clip(de, -1, 1)), flaps=flaps)
        self.crashed = self.failure = self.failure_msg = ""
        self.time = 0.0
        self.airborne_since, self.touchdown, self.landing = 0.0, None, None
        self.event = None
        self.distance_flown = 0.0
        self._sep, self._sep_frozen, self._buffet_dcl = 1.0, False, 0.0
        self.derivative(self.state, self.controls)  # fresh telemetry for the new position
        return ok and 0 <= thr <= 1.0 and abs(de) <= 1.0

    # ------------------------------------------------------------ telemetry
    def telemetry(self) -> dict:
        s = self.state
        f = self.last_forces or {}
        R = quat_to_dcm(s[6:10])
        v_ned = R @ s[3:6]
        phi, theta, psi = quat_to_euler(s[6:10])
        n_z = (f.get("lift", 0.0) * math.cos(f.get("alpha", 0.0))) / (self.mass * G0) if f else 1.0
        return {"time": self.time, "airspeed": f.get("V", 0.0), "mach": f.get("mach", 0.0), "altitude": -s[2],
                "vs": -v_ned[2], "alpha": f.get("alpha", 0.0) / D2R, "beta": f.get("beta", 0.0) / D2R,
                "pitch": theta / D2R, "roll": phi / D2R, "heading": (psi / D2R) % 360, "load_factor": n_z,
                "throttle": self.controls.throttle, "reheat": f.get("reheat", False), "stalled": f.get("stalled", False), "updraft": self.updraft, "launch": (self.launch or {}).get("kind", ""),
                "launch_v_target": (self.launch or {}).get("v_target", 0.0), "cable_deg": (self.launch or {}).get("cable_deg", 0.0),
                "buffet": float(np.clip((self._near_stall - 0.88) / 0.12, 0, 1)),
                "on_ground": f.get("on_ground", False), "ground_speed": float(np.hypot(v_ned[0], v_ned[1])),
                "fuel_kg": self.fuel, "fuel_flow_kgh": self.fuel_flow * 3600, "mass_kg": self.mass,
                "distance_km": self.distance_flown / 1000, "stress": self.max_stress,
                "north": float(s[0]), "east": float(s[1]), "track": math.degrees(math.atan2(v_ned[1], v_ned[0])) % 360}

    def ground_boom_pldb(self) -> tuple[float, float]:
        """(PLdB on the ground, Mach cutoff) for the current flight condition."""
        t = self.telemetry()
        if t["mach"] <= 1.0:
            return 0.0, sb.mach_cutoff(max(t["altitude"], 0.0))
        r = self.m.polar.boom(self.m.mass * G0, t["mach"], max(t["altitude"], 1000.0))
        return sb.perceived_level_db(r.overpressure_psf, self.m.polar.shaping_credit(r)), r.mach_cutoff


def on_ground_now(sim, pos) -> bool:
    return pos[2] + sim.ground(pos[0], pos[1]) > -3.0


def rate_landing(td: dict, stop_pos: np.ndarray, runway=None, runways=None) -> dict:
    """Score a landing from the touchdown record and where the aircraft stopped (any runway)."""
    from .terrain import RUNWAYS
    RUNWAYS = runways or RUNWAYS
    if runway is None:
        def offset(rw):
            a, r = rw.local(td["north"], td["east"])
            return abs(r) + max(0.0, -a, a - rw.length)
        runway = min(RUNWAYS, key=offset)
    rw = runway
    along, cross = rw.local(td["north"], td["east"])
    stop_along, stop_cross = rw.local(float(stop_pos[0]), float(stop_pos[1]))
    sink = td["sink_ms"]
    on_rw = rw.contains(td["north"], td["east"])
    stopped_on = rw.contains(float(stop_pos[0]), float(stop_pos[1]))
    feel = "smooth" if sink < 1.0 else "good" if sink < 2.0 else "firm" if sink < 3.0 else "hard"
    score = 100.0
    score -= 12 * max(sink - 0.6, 0)
    score -= 0.6 * max(abs(cross) - 3, 0)
    score -= 0.02 * max(along - 600, 0) + 0.05 * max(150 - along, 0)
    score -= 1.5 * max(abs(td["roll_deg"]) - 2, 0)
    if not on_rw:
        score -= 40
    if not stopped_on:
        score -= 20
    verdict = "Landed off the runway" if not on_rw else ("Ran off the runway" if not stopped_on else "Landed")
    return {"verdict": verdict, "runway": rw.code, "feel": feel, "sink_ms": sink, "touchdown_from_threshold_m": along,
            "centreline_offset_m": cross, "roll_deg": td["roll_deg"], "stopped_after_m": stop_along - along,
            "score": max(0.0, round(score))}


# ---------------------------------------------------------------------------------------------------- modes
@dataclass
class Mode:
    name: str
    eigenvalue: complex
    omega_n: float
    zeta: float
    period_s: float | None
    time_const_s: float | None
    requirement: str
    ok: bool


def dynamic_modes(sim: FlightSimulator, speed: float, altitude: float) -> list[Mode]:
    """Linearise about level trim and classify the classic aircraft modes (MIL-F-8785C level-1 checks)."""
    alpha, de, thr, _ = sim.trim(speed, altitude)
    ctl = Controls(elevator=de, throttle=thr, gear_down=False, sas=False)

    def f(x):  # x = [u, v, w, p, q, r, phi, theta]
        u, v, w, p, q, r, phi, theta = x
        quat = euler_to_quat(phi, theta, 0.0)
        s = np.concatenate([[0, 0, -altitude], [u, v, w], quat, [p, q, r]])
        d = sim.derivative(s, ctl)
        phid = p + (q * math.sin(phi) + r * math.cos(phi)) * math.tan(theta)
        thetad = q * math.cos(phi) - r * math.sin(phi)
        return np.concatenate([d[3:6], d[10:13], [phid, thetad]])

    x0 = np.array([speed * math.cos(alpha), 0, speed * math.sin(alpha), 0, 0, 0, 0, alpha])
    n = len(x0)
    J = np.zeros((n, n))
    f0 = f(x0)
    for i in range(n):
        h = 1e-4 * max(1.0, abs(x0[i]))
        dx = np.zeros(n)
        dx[i] = h
        J[:, i] = (f(x0 + dx) - f(x0 - dx)) / (2 * h)
    del f0
    lon, lat = [0, 2, 4, 7], [1, 3, 5, 6]
    modes: list[Mode] = []

    def describe(ev):
        wn = abs(ev)
        zeta = -ev.real / wn if wn > 1e-9 else 0.0
        period = 2 * math.pi / abs(ev.imag) if abs(ev.imag) > 1e-6 else None
        tc = -1.0 / ev.real if abs(ev.real) > 1e-9 else None
        return wn, zeta, period, tc

    lon_ev = np.linalg.eigvals(J[np.ix_(lon, lon)])
    osc = sorted([e for e in lon_ev if e.imag > 1e-6], key=abs)
    reals = sorted([e for e in lon_ev if abs(e.imag) <= 1e-6], key=abs)
    if len(osc) == 2:
        sp, ph = osc[1], osc[0]
    elif len(osc) == 1:
        sp = osc[0] if abs(osc[0]) > 0.3 else (reals[-1] if reals else osc[0])
        ph = osc[0] if sp is not osc[0] else (reals[0] if reals else osc[0])
    else:
        sp, ph = (reals[-1], reals[0]) if len(reals) >= 2 else (lon_ev[0], lon_ev[-1])
    wn, z, per, tc = describe(sp)
    modes.append(Mode("Short period", sp, wn, z, per, tc, "damping ratio 0.35 - 1.3",
                      0.35 <= z <= 1.3 and sp.real < 0))
    wn, z, per, tc = describe(ph)
    modes.append(Mode("Phugoid", ph, wn, z, per, tc, "damping ratio >= 0.04", z >= 0.04 or ph.real < 0))
    lat_ev = np.linalg.eigvals(J[np.ix_(lat, lat)])
    osc = [e for e in lat_ev if e.imag > 1e-6]
    reals = sorted([e for e in lat_ev if abs(e.imag) <= 1e-6], key=lambda e: abs(e.real))
    if osc:
        dr = osc[0]
        wn, z, per, tc = describe(dr)
        modes.append(Mode("Dutch roll", dr, wn, z, per, tc, "damping ratio >= 0.08, natural freq >= 0.4 rad/s",
                          z >= 0.08 and wn >= 0.4))
    if reals:
        roll = reals[-1]
        wn, z, per, tc = describe(roll)
        modes.append(Mode("Roll subsidence", roll, wn, z, per, tc, "time constant <= 1.4 s",
                          roll.real < 0 and (tc or 99) <= 1.4))
        if len(reals) > 1:
            spiral = reals[0]
            t2 = math.log(2) / spiral.real if spiral.real > 1e-9 else None
            modes.append(Mode("Spiral", spiral, abs(spiral), 0.0, None, -1 / spiral.real if spiral.real else None,
                              "time to double >= 20 s (or stable)", spiral.real <= 0 or (t2 or 0) >= 20))
    return modes
