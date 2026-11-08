"""Mission-driven sizing: configurations, baselines, and derived parameters.

``complete_design`` enforces the parameter relationships that a designer
would never leave free (landing-gear position from the CG, gear height from
engine clearance, wheel size from weight).  It is applied to every candidate
the engine generates or evolves, but never silently to manual GUI edits.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq, minimize

from ..geometry.arrangement import Arrangement
from ..numerics import aerodynamics as aero
from ..numerics.atmosphere import G0, RHO0, isa
from .evaluator import evaluate
from .mission import MissionRequirements
from .parameters import AircraftParameters, ConfigurationParams, field_meta

TARGET_STATIC_MARGIN = 0.12
_TRACE = False
TIPBACK_DEG = 16.5


LAYOUT_LABELS = {"conventional": "conventional", "canard": "canard", "flying_wing": "flying wing",
                 "blended_wing_body": "blended wing body", "twin_boom": "twin-boom"}


def _C(prop, engines, tail, wing, layout="conventional") -> ConfigurationParams:
    return ConfigurationParams(propulsion=prop, engine_layout=engines, tail_type=tail, wing_position=wing,
                               layout=layout)


def configurations_for(mission: MissionRequirements) -> list[ConfigurationParams]:
    """Every layout worth sizing for this mission (topology is chosen here, then held fixed)."""
    if getattr(mission, "category", "transport") == "aerobatic":
        # stunt planes: single tractor propeller in the nose, conventional tail (low- or mid-wing)
        return [_C("turboprop", "nose", "conventional", "low"), _C("turboprop", "nose", "conventional", "mid"),
                _C("turboprop", "nose", "t_tail", "mid")]
    if mission.propulsion == "auto":
        if mission.cruise_mach < 0.5:
            props = ["turboprop"]
        elif mission.cruise_mach <= 0.65:
            props = ["turboprop", "turbofan"]
        else:
            props = ["turbofan"]
    else:
        props = [mission.propulsion]
    pay = mission.payload_kg
    conv: list[ConfigurationParams] = []
    unconv: list[ConfigurationParams] = []
    if mission.cruise_mach > 1.0:  # supersonic: slender deltas and low-boom shapes only
        sup = [_C("turbofan", "under_wing", "conventional", "low", "supersonic_delta"),  # Concorde / Overture style
               _C("turbofan", "pusher", "t_tail", "low")]  # X-59 style low-boom: long nose, dorsal engine, T-tail
        return sup if mission.explore != "conventional only" else sup[:1]
    for prop in props:
        if prop == "turbofan":
            conv += [_C("turbofan", "wing", "conventional", "low"), _C("turbofan", "wing", "t_tail", "low")]
            if pay <= 25000:
                conv.append(_C("turbofan", "rear_fuselage", "t_tail", "low"))
            if pay <= 3000:  # personal / business jet with engines clear of a V-tail
                unconv.append(_C("turbofan", "pusher", "v_tail", "low"))
            if pay <= 8000:
                unconv.append(_C("turbofan", "rear_fuselage", "twin_fin", "low", "canard"))
            if pay <= 12000:
                unconv.append(_C("turbofan", "upper_aft", "none", "mid", "flying_wing"))
            if pay >= 10000:
                unconv.append(_C("turbofan", "upper_aft", "twin_fin", "mid", "blended_wing_body"))
        else:
            conv += [_C("turboprop", "wing", "conventional", "high"), _C("turboprop", "wing", "t_tail", "high"),
                     _C("turboprop", "wing", "conventional", "low")]
            if pay <= 6000:
                unconv.append(_C("turboprop", "pusher", "twin_fin", "high", "twin_boom"))
            if pay <= 4000:
                unconv.append(_C("turboprop", "pusher", "twin_fin", "mid", "canard"))
            if pay <= 3000:  # H-tail (twin fins on the tailplane tips)
                unconv.append(_C("turboprop", "wing", "twin_fin", "high"))
    if mission.explore == "conventional only":
        return conv
    if mission.explore == "unconventional only":
        return unconv or conv
    return conv + unconv


def engine_count(mission: MissionRequirements, config: ConfigurationParams | None = None) -> int:
    if mission.engines != "auto":
        n = int(mission.engines)
    elif mission.payload_kg > 60000:
        n = 4
    elif config is not None and config.engine_layout == "pusher" and mission.payload_kg <= (
            2000 if mission.cruise_mach > 1 else 1500):
        n = 1
    elif config is not None and config.layout == "supersonic_delta" and mission.payload_kg > 8000:
        n = 4
    else:
        n = 2
    if config is not None and config.engine_layout != "pusher" and n == 1:
        n = 2  # wing / upper-aft / rear-fuselage engines come in pairs
    return n


def _clip(path: str, value: float) -> float:
    group, name = path.split(".")
    from .parameters import GROUP_TYPES
    meta = field_meta(GROUP_TYPES[group], name)
    return float(np.clip(value, meta["lo"], meta["hi"]))


def _set(params: AircraftParameters, path: str, value) -> None:
    params.set(path, _clip(path, value) if not isinstance(value, str) else value)


def complete_design(params: AircraftParameters, mission: MissionRequirements) -> AircraftParameters:
    """Set derived parameters (gear geometry) consistently with the rest of the design."""
    p = params
    fus_d = p.fuselage.diameter
    jet = p.configuration.propulsion == "turbofan"
    ev = evaluate(p, mission)
    w0 = ev.metrics.get("mtow_kg", mission.payload_kg * 4)
    # wheels from the load per tyre (Raymer eq. 11.1, transport)
    wheels = 2 if w0 > 20000 else 1
    load_lb = 0.9 * w0 / (2 * wheels) * 2.20462
    _set(p, "gear.wheels_per_leg", wheels)
    _set(p, "gear.wheel_diameter", 1.63 * load_lb**0.315 * 0.0254)
    _set(p, "gear.track", max(0.21 * p.wing.span, 1.15 * fus_d))
    # gear height: fuselage clearance and engine/propeller ground clearance
    _set(p, "gear.ground_clearance", 0.25 * fus_d + (0.35 if jet else 0.2))
    arr = Arrangement(p)
    need = (0.5 if jet else 0.35) - arr.engine_ground_clearance()
    if need > 0:
        _set(p, "gear.ground_clearance", p.gear.ground_clearance + need)
    # main gear behind the CG at the tip-back angle, nose gear for 8-15 % load
    arr = Arrangement(p)
    ev = evaluate(p, mission)
    x_cg = ev.metrics.get("x_cg_m", arr.x_ac_wing)
    # main gear: behind the CG by the tip-back angle AND far enough that the nose wheel
    # carries >= ~9 % of the weight (long aircraft need the larger of the two)
    x_nose = 0.11 * p.fuselage.length
    d_tipback = math.tan(math.radians(TIPBACK_DEG)) * (0.0 - arr.z_ground)
    d_nose_load = 0.09 / 0.91 * max(x_cg - x_nose, 0.0)
    x_main = x_cg + max(d_tipback, d_nose_load)
    wx = arr.wing_placement.position[0]
    _set(p, "gear.main_chord_fraction", (x_main - wx) / p.wing.root_chord)
    x_main = wx + p.gear.main_chord_fraction * p.wing.root_chord
    # cabin insulation just sufficient for the passengers' noise limit
    from .mission import ignored_requirements
    if "cabin_noise" in ignored_requirements(mission):
        _set(p, "structure.insulation_kg_m2", 0.3)  # cabin noise not designed for: minimum trim insulation
    elif ev.converged:
        _set(p, "structure.insulation_kg_m2", ev.metrics.get("insulation_needed_kg_m2", p.structure.insulation_kg_m2))
    d = x_main - x_cg
    if d > 0:
        x_nose_max = x_main - d / 0.08
        x_nose = min(0.11 * p.fuselage.length, x_nose_max)
        _set(p, "gear.nose_x_fraction", x_nose / p.fuselage.length)
    return p


def _tail_arm_target(p: AircraftParameters, mac: float) -> float:
    return (3.5 if p.configuration.propulsion == "turbofan" else 2.8) * mac


def _size_tails(p: AircraftParameters, mission: MissionRequirements) -> None:
    """Size every tail surface the layout has from volume coefficients."""
    jet = p.configuration.propulsion == "turbofan"
    L = p.fuselage.length
    layout = p.configuration.layout
    for _ in range(4):
        arr = Arrangement(p)
        w = arr.wing
        S, b, mac = w.area, w.span, w.mac
        vv = 0.085 if jet else 0.08
        tt = arr.tail_type
        if layout == "conventional":
            cv = p.vtail.root_chord
            _set(p, "vtail.x_position", (L - 1.02 * cv) / L)
            arr = Arrangement(p)
            lv = max(arr.vtail_placement.position[0] + 0.35 * cv - arr.x_ac_wing, 0.2 * L)
            lh = max(arr.x_ac_htail - arr.x_ac_wing if tt != "v_tail" else lv, 0.2 * L)
            sv = vv * S * b / lv
            sh = (0.85 if tt == "t_tail" else 0.95) * S * mac / lh
            if tt == "v_tail":
                gamma = math.degrees(math.atan(math.sqrt(sv / sh)))
                area = sh + sv  # Purser-Campbell: same total area, split by cant angle
                span = math.sqrt(4.0 * area)
                _set(p, "vtail.dihedral_deg", gamma)
                _set(p, "vtail.span", span)
                _set(p, "vtail.root_chord", 2 * area / (span * (1 + p.vtail.taper_ratio)))
                continue
            bh = math.sqrt(4.6 * sh)
            _set(p, "htail.span", bh)
            _set(p, "htail.root_chord", 2 * sh / (bh * (1 + p.htail.taper_ratio)))
            _set(p, "htail.x_position", (L - 1.1 * p.htail.root_chord) / L)
            if tt == "twin_fin":
                h = math.sqrt(1.4 * sv / 2)
                _set(p, "vtail.span", h)
                _set(p, "vtail.root_chord", sv / (h * (1 + p.vtail.taper_ratio)))
            else:
                h = math.sqrt(1.6 * sv)
                _set(p, "vtail.span", h)
                _set(p, "vtail.root_chord", 2 * sv / (h * (1 + p.vtail.taper_ratio)))
        elif layout == "twin_boom":
            lt = _tail_arm_target(p, mac)
            # the tail must also sit behind the pod (and any pusher propeller at its tail)
            wx = arr.wing_placement.position[0]
            props = [e for e in arr.engines if e.pusher and abs(e.inlet[1]) < 1e-6]
            pod_end = max([L] + [e.prop_x + 0.3 for e in props])
            arm = max(w.x_quarter_mac + lt - 0.25 * p.htail.root_chord, pod_end + 0.6 - wx)
            _set(p, "booms.tail_arm", arm)
            lt = max(lt, arm - w.x_quarter_mac + 0.25 * p.htail.root_chord)
            sh = 0.8 * S * mac / lt
            span = 2 * arr.boom_y
            _set(p, "htail.span", span)
            _set(p, "htail.taper_ratio", 1.0)
            _set(p, "htail.sweep_le_deg", 0.0)
            _set(p, "htail.root_chord", sh / span)
            sv_each = 0.5 * vv * S * b / lt
            h = math.sqrt(1.4 * sv_each)
            _set(p, "vtail.span", h)
            _set(p, "vtail.root_chord", 2 * sv_each / (h * (1 + p.vtail.taper_ratio)))
        elif layout == "canard":
            lc = max(arr.x_ac_wing - (arr.canard_placement.position[0] + arr.canard.x_quarter_mac), 0.2 * L)
            sc = 0.16 * S * mac / lc
            bc = math.sqrt(6.0 * sc)
            _set(p, "canard.span", bc)
            _set(p, "canard.root_chord", 2 * sc / (bc * (1 + p.canard.taper_ratio)))
            fins = arr.yaw_surfaces()
            lv = max((np.mean([f.x_ac for f in fins]) if fins else L) - arr.x_ac_wing, 0.12 * b)
            sv = 0.05 * S * b / lv
            if tt == "twin_fin":
                h = math.sqrt(1.5 * sv / 2)
                _set(p, "vtail.root_chord", sv / (h * (1 + p.vtail.taper_ratio)))
            else:
                h = math.sqrt(1.6 * sv)
                _set(p, "vtail.root_chord", 2 * sv / (h * (1 + p.vtail.taper_ratio)))
                _set(p, "vtail.x_position", (L - 1.02 * p.vtail.root_chord) / L)
            _set(p, "vtail.span", h)
        elif layout == "blended_wing_body":
            fins = arr.yaw_surfaces()
            lv = max((np.mean([f.x_ac for f in fins]) if fins else 0.0) - arr.x_ac_wing, 0.1 * b)
            sv_each = 0.5 * 0.03 * S * b / lv
            h = math.sqrt(1.5 * sv_each)
            _set(p, "vtail.span", h)
            _set(p, "vtail.root_chord", 2 * sv_each / (h * (1 + p.vtail.taper_ratio)))
        elif layout == "supersonic_delta":  # single fuselage fin, no horizontal tail
            cv = p.vtail.root_chord
            _set(p, "vtail.x_position", (L - 1.02 * cv) / L)
            arr = Arrangement(p)
            lv = max(arr.vtail_placement.position[0] + 0.35 * cv - arr.x_ac_wing, 0.12 * L)
            sv = 0.06 * S * b / lv
            h = math.sqrt(1.3 * sv)
            _set(p, "vtail.span", h)
            _set(p, "vtail.root_chord", 2 * sv / (h * (1 + p.vtail.taper_ratio)))
        # flying_wing: no tail surfaces at all


def target_static_margin(p: AircraftParameters) -> float:
    return 0.07 if p.configuration.layout in ("flying_wing", "blended_wing_body", "supersonic_delta") \
        else TARGET_STATIC_MARGIN


def place_wing_for_stability(p: AircraftParameters, mission: MissionRequirements,
                             target: float | None = None) -> None:
    """Solve the wing station that gives the target static margin (SciPy brentq)."""
    target = target_static_margin(p) if target is None else target

    def margin(xf: float) -> float:
        q = p.copy()
        q.wing.x_position = xf
        ev = evaluate(q, mission)
        return ev.metrics.get("static_margin", np.nan) - target

    lo, hi = (0.0, 0.7) if p.configuration.layout != "conventional" else (0.16, 0.60)
    try:
        f_lo, f_hi = margin(lo), margin(hi)
        if np.isfinite(f_lo) and np.isfinite(f_hi) and f_lo * f_hi < 0:
            _set(p, "wing.x_position", brentq(margin, lo, hi, xtol=1e-3))
            return
    except ValueError:
        pass
    if p.configuration.layout not in ("flying_wing", "blended_wing_body", "supersonic_delta"):
        return
    # Tailless: almost all mass moves with the wing, so sliding it barely changes the
    # margin.  The designer's lever is outer-wing sweep, which moves the aerodynamic
    # centre relative to the centre-body.
    inner_gap = p.wing.inner_sweep_le_deg - p.wing.sweep_le_deg

    def margin_sweep(sweep: float) -> float:
        q = p.copy()
        q.wing.sweep_le_deg = sweep
        q.wing.inner_sweep_le_deg = min(sweep + inner_gap, 68.0)
        ev = evaluate(q, mission)
        return ev.metrics.get("static_margin", np.nan) - target

    try:
        a, b = margin_sweep(15.0), margin_sweep(50.0)
        if np.isfinite(a) and np.isfinite(b) and a * b < 0:
            sw = brentq(margin_sweep, 15.0, 50.0, xtol=0.1)
            _set(p, "wing.sweep_le_deg", sw)
            _set(p, "wing.inner_sweep_le_deg", min(sw + inner_gap, 68.0))
    except ValueError:
        pass


def _size_fuselage(p: AircraftParameters, mission: MissionRequirements) -> None:
    layout = p.configuration.layout
    D = mission.fuselage_diameter_m
    if mission.cruise_mach > 1.0:
        # slender body: wave drag ~ (A_max / L)^2.  Low-boom shapes add a very long nose (X-59).
        low_boom = layout != "supersonic_delta"
        nf, tf = (0.36, 0.22) if low_boom else (0.17, 0.22)
        fineness = 24.0 if low_boom else 21.0  # Concorde ~21
        L = max(mission.cabin_length_m / (1 - 0.55 * nf - 0.65 * tf), fineness * D)
        _set(p, "fuselage.diameter", D)
        _set(p, "fuselage.length", L)
        _set(p, "fuselage.nose_fraction", nf)
        _set(p, "fuselage.tail_fraction", tf)
        _set(p, "fuselage.tail_upsweep", 0.08)
        _set(p, "fuselage.height_ratio", 1.0)
        _set(p, "fuselage.area_ruling", 0.6)
        _set(p, "fuselage.nose_profile", "ogive")
        _set(p, "fuselage.frame_pitch", 0.53 if D > 2.5 else 0.45)
        _set(p, "fuselage.frame_depth", 0.03 * D + 0.03)
        return
    if layout == "flying_wing":  # crew/payload pod blended into the centre section
        _set(p, "fuselage.diameter", D)
        _set(p, "fuselage.height_ratio", 0.9)
        _set(p, "fuselage.nose_fraction", 0.25)
        _set(p, "fuselage.tail_fraction", 0.30)
        _set(p, "fuselage.tail_upsweep", 0.0)
        _set(p, "fuselage.length", max(mission.cabin_length_m / (1 - 0.55 * 0.25 - 0.65 * 0.30), 3.2 * D))
    elif layout == "blended_wing_body":  # cockpit fairing; the cabin is in the wing
        d = min(0.75 * D, 4.0)
        _set(p, "fuselage.diameter", d)
        _set(p, "fuselage.height_ratio", 0.65)
        _set(p, "fuselage.nose_fraction", 0.35)
        _set(p, "fuselage.tail_fraction", 0.40)
        _set(p, "fuselage.tail_upsweep", 0.0)
        _set(p, "fuselage.length", 3.2 * d)
    else:
        nf, tf = (0.12, 0.26) if D > 2.5 else (0.16, 0.30)
        if layout == "twin_boom":
            tf = 0.22  # short pod: the booms carry the tail
        L = max(mission.cabin_length_m / (1 - 0.55 * nf - 0.65 * tf), 4.5 * D)
        _set(p, "fuselage.diameter", D)
        _set(p, "fuselage.length", L)
        _set(p, "fuselage.nose_fraction", nf)
        _set(p, "fuselage.tail_fraction", tf)
    D = p.fuselage.diameter
    _set(p, "fuselage.frame_pitch", 0.53 if D > 2.5 else 0.45)
    _set(p, "fuselage.frame_depth", 0.03 * D + 0.03)


def _stretch_for_tail_arm(p: AircraftParameters, mission: MissionRequirements) -> None:
    """Conventional layouts: lengthen the fuselage until the tail has a proper moment arm.

    Without this, a big wing on a short cabin (e.g. a long-range two-seater)
    gets a tail arm of a couple of metres and the tail-volume rule produces a
    fin as tall as the fuselage is long.
    """
    if p.configuration.layout != "conventional":
        return
    arr = Arrangement(p)
    w = arr.wing
    lt = _tail_arm_target(p, w.mac)
    xpos = p.wing.x_position
    l_arm = (w.x_quarter_mac + lt + 0.8 * p.htail.root_chord) / max(1 - xpos, 0.3)
    L_cabin = mission.cabin_length_m / (1 - 0.55 * p.fuselage.nose_fraction - 0.65 * p.fuselage.tail_fraction)
    _set(p, "fuselage.length", max(L_cabin, 4.5 * p.fuselage.diameter, l_arm))


def _shape_wing(p: AircraftParameters, mission: MissionRequirements, S: float, b: float) -> None:
    """Set span and chords for the layout's planform so that the area is S."""
    w = p.wing
    semi = b / 2
    if w.crank_eta > 0.02:  # cranked planform (tailless, supersonic deltas and arrows)
        ek, r, lam = w.crank_eta, w.crank_chord_ratio, w.taper_ratio
        cr = S / (semi * (ek * (1 + r) + (1 - ek) * (r + lam)))
        _set(p, "wing.span", b)
        _set(p, "wing.root_chord", cr)
        return
    _set(p, "wing.span", b)
    _set(p, "wing.root_chord", 2 * S / (b * (1 + w.taper_ratio)))


def _size_bwb_centrebody(p: AircraftParameters, mission: MissionRequirements, S: float, b: float) -> float:
    """Choose the centre-body so that the cabin floor fits; returns the resulting wing area."""
    need = mission.cabin_floor_area_m2 * 1.03
    semi = b / 2

    def floor(cr: float) -> float:
        q = p.copy()
        yc = min(0.38 * cr, 0.55 * semi)
        q.wing.crank_eta = yc / semi
        q.wing.root_chord = cr
        q.wing.span = b
        return Arrangement(q).centrebody_floor_area() - need

    try:
        cr = brentq(floor, 4.0, 90.0, xtol=0.05) if floor(90.0) > 0 > floor(4.0) else 30.0
    except ValueError:
        cr = 30.0
    cr = float(np.clip(cr, 4.0, 90.0))
    ek = min(0.38 * cr, 0.55 * semi) / semi
    _set(p, "wing.crank_eta", ek)
    _set(p, "wing.root_chord", cr)
    _set(p, "wing.span", b)
    r = p.wing.crank_chord_ratio
    lam = (S / (semi * cr) - ek * (1 + r)) / (1 - ek) - r
    _set(p, "wing.taper_ratio", max(lam, 0.06))
    return Arrangement(p).wing.area


def baseline(mission: MissionRequirements, config: ConfigurationParams) -> AircraftParameters:
    """Initial sizing of one configuration (layout + propulsion) from the mission requirements."""
    if getattr(mission, "category", "transport") == "aerobatic":
        return _size_aerobatic(mission, config)
    p = AircraftParameters()
    p.configuration = ConfigurationParams(**vars(config))
    _set(p, "structure.limit_load_factor", getattr(mission, "g_limit", 2.5))  # structure sized for the g rating
    layout = config.layout
    jet = config.propulsion == "turbofan"
    tailless = layout in ("flying_wing", "blended_wing_body")
    M = mission.cruise_mach if jet else min(mission.cruise_mach, 0.68)
    _size_fuselage(p, mission)
    supersonic = mission.cruise_mach > 1.0

    # ---- planform family
    sweep_qc = math.radians(float(np.clip((M - 0.62) * 140, 0, 32))) if jet else 0.0
    if supersonic:
        # keep the leading edge inside the Mach cone (subsonic leading edge) -> low wave drag due to lift
        mu = math.degrees(math.asin(1.0 / mission.cruise_mach))
        sweep_le = float(np.clip(90 - mu + 4, 48, 72))
        delta = layout == "supersonic_delta"
        _set(p, "wing.taper_ratio", 0.06 if delta else 0.15)
        _set(p, "wing.crank_eta", 0.38 if delta else 0.30)
        _set(p, "wing.crank_chord_ratio", 0.45 if delta else 0.5)
        _set(p, "wing.sweep_le_deg", sweep_le - (8 if delta else 4))
        _set(p, "wing.inner_sweep_le_deg", min(sweep_le + 6, 78))
        _set(p, "wing.tc_root", 0.030 if delta else 0.035)  # Concorde: ~3 % root, ~2 % tip
        _set(p, "wing.tc_tip", 0.025)
        _set(p, "wing.camber", 0.005)
        _set(p, "wing.twist_tip_deg", -3.0)
        _set(p, "wing.dihedral_deg", 0.0)
        _set(p, "wing.x_position", 0.40 if delta else 0.45)
        _set(p, "engines.bypass_ratio", 1.0)
        _set(p, "structure.weight_method", "structural")  # thin supersonic wings: size the actual box
        if delta:  # multi-spar delta: integral tanks almost from leading to trailing edge (Concorde)
            _set(p, "wing.front_spar", 0.08)
            _set(p, "wing.rear_spar", 0.78)
            _set(p, "controls.aileron_chord", 0.19)
            _set(p, "controls.aileron_start", 0.45)
        ar = 1.9 if delta else 2.6
        clmax = 1.1 if delta else 1.3
        p.htail.sweep_le_deg = 55.0
        p.vtail.sweep_le_deg = 60.0
        _set(p, "vtail.tc", 0.04)
        _set(p, "htail.tc", 0.04)
        p.engines.count = engine_count(mission, config)
        if delta:
            _set(p, "engines.span_fraction", 0.32)
            _set(p, "engines.spacing_fraction", 0.10)
        vs = mission.max_approach_speed_ms / 1.23 * 0.97
        ws_land = 0.5 * RHO0 * vs**2 * clmax / G0 / 0.66  # supersonic transports land at ~2/3 of MTOW
        atm = isa(mission.cruise_altitude_m)
        q = 0.5 * atm.density * (mission.cruise_mach * atm.speed_of_sound) ** 2
        ws_cruise = q * math.sqrt(math.pi * ar * 0.75 * 0.016) / G0
        return _size_supersonic(p, mission, config, min(ws_land, ws_cruise), ar)
    if tailless:
        sweep_qc = max(sweep_qc, math.radians(28))  # sweep provides the tail's moment arm
        _set(p, "wing.taper_ratio", 0.12 if layout == "flying_wing" else 0.10)
        _set(p, "wing.crank_eta", 0.25)
        _set(p, "wing.crank_chord_ratio", 0.45 if layout == "flying_wing" else 0.33)
        _set(p, "wing.twist_tip_deg", -4.0)
        _set(p, "wing.camber", 0.01)  # low camber stands in for reflexed sections
        _set(p, "wing.x_position", 0.15 if layout == "flying_wing" else 0.25)
        ar = 6.5 if layout == "flying_wing" else 5.5
    elif layout == "canard":
        _set(p, "wing.taper_ratio", 0.35)
        _set(p, "wing.x_position", 0.55)
        _set(p, "canard.x_position", 0.07)
        ar = 8.5
    else:
        _set(p, "wing.taper_ratio", 0.25 if jet else 0.5)
        ar = 9.5 if jet else 11.5
        if layout == "twin_boom":
            _set(p, "wing.x_position", 0.30)
    lam = p.wing.taper_ratio
    sweep_le = math.degrees(math.atan(math.tan(sweep_qc) + (1 - lam) / (ar * (1 + lam))))
    _set(p, "wing.sweep_le_deg", sweep_le)
    if tailless:
        _set(p, "wing.inner_sweep_le_deg", min(sweep_le + 15, 62))
    sweep_hc = math.atan(math.tan(sweep_qc) - (1 - lam) / (ar * (1 + lam)))
    c = math.cos(sweep_hc)
    kappa = 0.95 if jet else 0.87
    tc = float(np.clip((kappa / c - (M + 0.02)) * c**2 - 0.5 / (10 * c), 0.10, 0.17))
    _set(p, "wing.tc_root", 0.17 if layout == "blended_wing_body" else tc * 1.2)
    _set(p, "wing.tc_tip", tc * 0.85)
    wing_pos = config.wing_position
    _set(p, "wing.dihedral_deg", 5.0 if wing_pos == "low" else (2.5 if wing_pos == "mid" else 1.5))
    if wing_pos == "high":
        _set(p, "wing.incidence_deg", 2.5)
    clmax = aero.clmax_landing(sweep_qc, slats=jet and not tailless) * (0.85 if tailless else 1.0)
    vs = mission.max_approach_speed_ms / 1.23 * 0.97
    ws_land = 0.5 * RHO0 * vs**2 * clmax / G0 / 0.85  # kg/m^2 at MTOW
    atm = isa(mission.cruise_altitude_m)
    q = 0.5 * atm.density * (M * atm.speed_of_sound) ** 2
    ws_cruise = q * math.sqrt(math.pi * ar * 0.8 * 0.02) / G0 * 1.15
    ws = min(ws_land, ws_cruise)

    # ---- tails follow the wing sweep
    p.htail.sweep_le_deg = float(np.clip(sweep_le + 5, 0, 45)) if jet else 8.0
    p.vtail.sweep_le_deg = float(np.clip(sweep_le + 12, 15, 48)) if jet else 30.0
    if layout == "canard":
        p.canard.sweep_le_deg = float(np.clip(sweep_le - 5, 0, 35))
    n_eng = engine_count(mission, config)
    p.engines.count = n_eng

    return _size_loop(p, mission, config, ws, ar, tw=(0.30 if n_eng <= 2 else 0.27) if jet else 0.32)


def _size_loop(p: AircraftParameters, mission: MissionRequirements, config: ConfigurationParams, ws: float,
               ar: float, tw: float, supersonic: bool = False) -> AircraftParameters:
    """Iterate wing area, thrust, tails and weight to a converged, balanced design."""
    layout = config.layout
    jet = config.propulsion == "turbofan"
    n_eng = p.engines.count
    M = mission.cruise_mach if jet else min(mission.cruise_mach, 0.68)
    atm = isa(mission.cruise_altitude_m)
    fixed = mission.payload_kg + mission.crew_kg
    tsfc = (1.0 if supersonic else 0.56 if jet else 0.45) / 3600
    v = M * atm.speed_of_sound
    ff = 1.08 * (1 - 0.95 * math.exp(-mission.range_km * 1000 * tsfc / (v * 16)))
    # first guess: fuel and empty fractions, with a floor for very small payloads where
    # systems, engines and structure dominate (a 2-seat jet is not 5x its payload)
    w0 = max(fixed / max(1 - 0.5 - ff, 0.12), (2500.0 if jet else 1200.0) / max(1 - ff, 0.3))
    s_min = 0.0  # minimum area from fuel volume / approach speed, recomputed every pass (never compounded)
    thrust_kn = None
    for _ in range(18):
        S = max(w0 / ws, s_min)
        b = min(math.sqrt(ar * S), 0.995 * mission.max_span_m)
        if layout == "blended_wing_body":
            S = _size_bwb_centrebody(p, mission, S, b)
        else:
            _shape_wing(p, mission, S, b)
        if thrust_kn is None:  # first pass: statistical thrust-to-weight; afterwards a direct fixed point
            thrust_kn = tw * w0 * G0 / max(n_eng, 1) / 1000
        _set(p, "engines.thrust_kn", thrust_kn)
        if config.propulsion == "turboprop":
            arr = Arrangement(p)
            if arr.engines and abs(arr.engines[0].inlet[1]) > 1e-6:  # keep propellers clear of the fuselage
                e = arr.engines[0]
                need = (arr.fuselage.radius + e.prop_diameter / 2 + 0.6) / arr.wing.semi_span
                _set(p, "engines.span_fraction", max(0.3, need))
            if layout == "twin_boom" and arr.engines:  # pusher disc between the booms
                rp = arr.engines[0].prop_diameter / 2
                _set(p, "booms.lateral_station", max(0.25, (rp + 0.35 + p.booms.diameter / 2) / arr.wing.semi_span))
                _set(p, "booms.diameter", float(np.clip(0.07 * math.sqrt(S), 0.3, 1.2)))
        _size_tails(p, mission)
        _stretch_for_tail_arm(p, mission)
        _size_tails(p, mission)
        complete_design(p, mission)
        ev = evaluate(p, mission)
        scale = 1.0
        if ev.converged:
            m_ = ev.metrics
            cons = {c.name: c for c in ev.constraints}
            tr = "Transonic acceleration margin (Mach 1.1)"
            scale = max(m_["takeoff_field_m"] / (0.97 * mission.takeoff_field_m),
                        (1.05 if supersonic else 1.2) / max(cons["Cruise thrust margin"].value, 1e-3),
                        0.0 if cons.get("OEI climb gradient") is None or cons["OEI climb gradient"].satisfied else 1.1,
                        1.15 / cons[tr].value if tr in cons else 0.0)
            # airport noise: a higher bypass ratio slows the jet (quieter) at some supersonic cost
            if jet and m_["flyover_noise_db"] > mission.max_flyover_db and p.engines.bypass_ratio < 11.5:
                _set(p, "engines.bypass_ratio", p.engines.bypass_ratio + (0.5 if supersonic else 1.0))
            # rescale the *actual* engines by what this evaluation needs (not T/W x a lagging weight guess)
            thrust_kn = p.engines.thrust_kn * float(np.clip(scale, 0.8, 1.25))
            if _TRACE:
                print(f"  W0 {m_['mtow_kg']:8.0f} S {Arrangement(p).wing.area:6.0f} T {p.engines.thrust_kn:5.0f} "
                      f"T/W {m_['thrust_to_weight']:.3f} scale {scale:.2f} "
                      f"[tofl {m_['takeoff_field_m'] / (0.97 * mission.takeoff_field_m):.2f} cruise "
                      f"{cons['Cruise thrust margin'].value:.2f} trans "
                      f"{cons[tr].value if tr in cons else 0:.2f}] L/D {m_['cruise_ld']:.2f} vapp {m_['approach_speed_ms']:.1f} "
                      f"fuelratio {cons['Wing fuel capacity ratio'].value:.2f}")
            s_now = Arrangement(p).wing.area
            fuel_ratio = cons["Wing fuel capacity ratio"].value
            s_fuel = s_now * fuel_ratio ** (-2 / 3) * 1.02  # tank volume ~ area^1.5 at fixed shape
            s_app = s_now * (m_["approach_speed_ms"] / mission.max_approach_speed_ms) ** 2 * 1.01
            area_short = fuel_ratio < 1 or s_app > s_now * 1.005
            s_min = max(0.7 * s_min, s_fuel, s_app) if area_short else 0.0
        else:
            area_short = True
        new = ev.metrics.get("mtow_kg", w0 * 1.6)
        if abs(new - w0) / w0 < 0.005 and abs(scale - 1.0) < 0.02 and not area_short:
            break
        w0 = 0.5 * w0 + 0.5 * new
    for _ in range(3):  # wing station, tail size and CG are coupled
        place_wing_for_stability(p, mission)
        _size_tails(p, mission)
        _stretch_for_tail_arm(p, mission)
        complete_design(p, mission)
    return p


SUPERSONIC_SIZING_CONSTRAINTS = ("Approach speed", "Take-off field length", "Cruise thrust margin",
                                 "Transonic acceleration margin (Mach 1.1)", "Airport noise (take-off flyover)",
                                 "Wing fuel capacity ratio", "OEI climb gradient", "Wingspan", "Cabin length")


def _size_supersonic(p: AircraftParameters, mission: MissionRequirements, config: ConfigurationParams,
                     ws: float, ar: float) -> AircraftParameters:
    """Supersonic sizing as a small constrained optimisation.

    With fuel ~half the take-off weight, supersonic designs are so sensitive that
    fixed-point iteration overshoots.  Instead SciPy's Nelder-Mead searches wing area,
    thrust and bypass ratio for the lightest design meeting the sizing constraints,
    starting from the best point of a coarse scan.
    """
    n = max(p.engines.count, 1)
    fixed = mission.payload_kg + mission.crew_kg
    w_guess = max(14.0 * fixed, 9000.0)

    def build(z) -> AircraftParameters:
        S, T, bpr = math.exp(z[0]), math.exp(z[1]), float(np.clip(z[2], 0.3, 6.0))
        q = p.copy()
        _set(q, "engines.bypass_ratio", bpr)
        _set(q, "engines.thrust_kn", T)
        b = min(math.sqrt(ar * S), 0.995 * mission.max_span_m)
        _shape_wing(q, mission, S, b)
        _size_tails(q, mission)
        _stretch_for_tail_arm(q, mission)
        return q

    def cost(z) -> float:
        q = build(z)
        ev = evaluate(q, mission)
        if not ev.converged:
            return 1e12
        cons = {c.name: c for c in ev.constraints}
        pen = sum(cons[k].violation for k in SUPERSONIC_SIZING_CONSTRAINTS if k in cons)
        # stiff (quadratic) penalty: a violated constraint must cost more than the weight it saves,
        # otherwise the optimiser trades thrust/area shortfalls for lower take-off weight
        return ev.metrics["mtow_kg"] * (1 + 10 * pen + 100 * pen**2)

    s0 = w_guess / ws
    t0 = 0.33 * w_guess * G0 / n / 1000
    best = None
    for fs in (0.6, 1.0, 1.6, 2.5):  # coarse scan: find a region where the design closes
        for ft in (0.6, 1.0, 1.6, 2.5):
            z = np.array([math.log(s0 * fs), math.log(t0 * ft), 1.0])
            c = cost(z)
            if best is None or c < best[0]:
                best = (c, z)
    res = minimize(cost, best[1], method="Nelder-Mead",
                   options={"xatol": 0.01, "fatol": 1.0, "maxfev": 90,
                            "initial_simplex": [best[1], best[1] + [0.25, 0, 0], best[1] + [0, 0.25, 0],
                                                best[1] + [0, 0, 0.8]]})
    z = np.array(res.x if res.fun <= best[0] else best[1], dtype=float)
    # polish: close small residual shortfalls in approach speed / thrust directly
    for _ in range(3):
        ev = evaluate(build(z), mission)
        if not ev.converged:
            break
        cons = {c.name: c for c in ev.constraints}
        app, thr = cons["Approach speed"], cons["Cruise thrust margin"]
        changed = False
        if not app.satisfied and app.value < 1.08 * app.upper:
            z[0] += 2 * math.log(app.value / app.upper) + 0.01
            changed = True
        if not thr.satisfied and thr.value > 0.9 * thr.lower:
            z[1] += math.log(thr.lower / thr.value) + 0.01
            changed = True
        if not changed:
            break
    q = build(z)
    p.__dict__.update(q.__dict__)
    for _ in range(2):
        complete_design(p, mission)
        place_wing_for_stability(p, mission)
        _size_tails(p, mission)
    complete_design(p, mission)
    # final polish on the finished design: close small approach-speed / thrust shortfalls directly
    for _ in range(4):
        ev = evaluate(p, mission)
        if not ev.converged:
            break
        cons = {c.name: c for c in ev.constraints}
        app, thr = cons["Approach speed"], cons["Cruise thrust margin"]
        changed = False
        if not app.satisfied and app.value < 1.08 * app.upper:
            area = Arrangement(p).wing.area * (app.value / app.upper) ** 2 * 1.01
            _shape_wing(p, mission, area, p.wing.span)
            changed = True
        if not thr.satisfied and thr.value > 0.9 * thr.lower:
            _set(p, "engines.thrust_kn", p.engines.thrust_kn * thr.lower / thr.value * 1.01)
            changed = True
        if not changed:
            break
        complete_design(p, mission)
    return p


def aerobatic_mission(**overrides) -> MissionRequirements:
    """A stunt-plane mission: one or two pilots, unlimited-category aerobatics (Extra 330 class)."""
    base = dict(name="Aerobatic (stunt plane)", payload_kg=180.0, range_km=600.0, cruise_mach=0.25,
                cruise_altitude_m=2500.0, takeoff_field_m=500.0, max_approach_speed_ms=38.0, max_span_m=9.0,
                propulsion="turboprop", category="aerobatic", max_flyover_db=95.0, max_cabin_dba=95.0, g_limit=10.0)
    base.update(overrides)
    return MissionRequirements(**base)


def _size_aerobatic(mission: MissionRequirements, config: ConfigurationParams) -> AircraftParameters:
    """Stunt plane: symmetric wing, near-full-span ailerons, big tail and controls, +-10 g, high power.

    Starting proportions follow unlimited-category aerobatic aircraft (Extra 330, Sukhoi Su-26):
    wing aspect ratio ~5.6, wing loading ~85 kg/m^2, static thrust ~60 % of weight, tail areas
    ~30 % (horizontal) and ~14 % (vertical) of the wing (35 %-chord elevator and rudder).  Wing area and thrust are then iterated
    until the weight closes with the light-aircraft statistics.
    """
    p = AircraftParameters()
    p.configuration = ConfigurationParams(**vars(config))
    sets = {
        "fuselage.length": 7.0, "fuselage.diameter": 1.05, "fuselage.height_ratio": 1.15, "fuselage.nose_fraction": 0.2,
        "fuselage.tail_fraction": 0.45, "fuselage.tail_upsweep": 0.1,
        "wing.taper_ratio": 0.5, "wing.sweep_le_deg": 3.0, "wing.inner_sweep_le_deg": 3.0, "wing.dihedral_deg": 0.0,
        "wing.twist_tip_deg": 0.0, "wing.incidence_deg": 0.0, "wing.tc_root": 0.16, "wing.tc_tip": 0.12,
        "wing.camber": 0.0, "wing.x_position": 0.27, "wing.crank_eta": 0.0,
        "controls.aileron_start": 0.12, "controls.aileron_end": 0.97, "controls.aileron_chord": 0.30,
        "controls.flap_end": 0.0, "controls.elevator_chord": 0.35, "controls.rudder_chord": 0.35,
        "structure.limit_load_factor": getattr(mission, "g_limit", 10.0), "structure.safety_factor": 1.5,
        "structure.insulation_kg_m2": 0.0,
        "engines.count": 1, "htail.sweep_le_deg": 5.0, "htail.taper_ratio": 0.6, "htail.dihedral_deg": 0.0,
        "htail.x_position": 0.80, "vtail.sweep_le_deg": 25.0, "vtail.taper_ratio": 0.6, "vtail.x_position": 0.84,
        "gear.wheels_per_leg": 1,
    }
    for k, v in sets.items():
        try:
            p.set(k, v)
        except Exception:
            pass
    mtow = 950.0
    from .evaluator import evaluate as _evaluate
    for _ in range(8):
        S = mtow / 85.0
        span = min(math.sqrt(5.6 * S), 0.995 * mission.max_span_m)
        _shape_wing(p, mission, S, span)
        sh, sv = 0.30 * S, 0.14 * S  # big tails: control power with hinges behind the tail spar
        hspan = math.sqrt(4.0 * sh)
        _set(p, "htail.span", hspan)
        _set(p, "htail.root_chord", 2 * sh / (hspan * 1.6))
        vh = math.sqrt(1.4 * sv)
        _set(p, "vtail.span", vh)
        _set(p, "vtail.root_chord", 2 * sv / (vh * 1.6))
        _set(p, "engines.thrust_kn", 0.6 * mtow * G0 / 1000.0)
        _set(p, "fuselage.length", float(np.clip(5.0 + 2.2 * span / 8.0, 6.0, 9.5)))
        complete_design(p, mission)
        place_wing_for_stability(p, mission)  # nose engine + big tail: put the wing where the CG works
        complete_design(p, mission)
        ev = _evaluate(p, mission)
        if not ev.converged:
            break
        new = ev.metrics["mtow_kg"]
        if abs(new - mtow) < 2.0:
            mtow = new
            break
        mtow = 0.5 * mtow + 0.5 * new
    return p
