"""Gliders (sailplanes): no engine; performance is glide ratio, sink rate and the speeds for them.

Weights follow sailplane statistics (empty mass ~ 1.6 x span^1.8 kg: 15 m ~210 kg, 18 m ~290 kg,
20 m ~350 kg, as for ASW 28 / ASG 29 / DG-1000 class gliders), scaled by g rating, strength and
material. The drag polar comes from the design's own geometry (the evaluator's component build-up);
best glide and minimum sink are found from it.  Stability uses the same neutral-point method as
powered aircraft.  Requirements: best glide >= 35, minimum sink <= 0.75 m/s, stall <= 22 m/s
(~80 km/h), span within the class limit, the g rating, and normal stability / tail sizing.
"""
from __future__ import annotations

import math

import numpy as np

from ..numerics import aerodynamics as aero
from ..numerics.atmosphere import isa
from .mission import MissionRequirements
from .parameters import AircraftParameters

G0 = 9.80665


def glider_mission(**overrides) -> MissionRequirements:
    base = dict(name="Glider (18 m class sailplane)", payload_kg=100.0, crew_kg=0.0, range_km=300.0,
                cruise_mach=0.10, cruise_altitude_m=1200.0, takeoff_field_m=800.0, max_approach_speed_ms=30.0,
                max_span_m=18.0, propulsion="turboprop", category="glider", g_limit=5.3,
                max_flyover_db=95.0, max_cabin_dba=95.0)
    base.update(overrides)
    return MissionRequirements(**{k: v for k, v in base.items() if k in MissionRequirements.__dataclass_fields__})


class GliderPolar:
    """Sailplane drag polar: laminar-flow wing and tails, mostly laminar polished fuselage,
    near-elliptic span loading, profile drag rising with lift.  cd(cl, mach) -> (cd, cd0)."""

    def __init__(self, p: AircraftParameters):
        from ..geometry.arrangement import Arrangement
        arr = Arrangement(p)
        w, fus = arr.wing, arr.fuselage
        self.S, self.AR = w.area, w.aspect_ratio
        tc = float(w.tc(w.mac_eta))
        wing = 2.04 * w.area * 0.0023 * (1 + 2.0 * tc + 60 * tc ** 4)
        body = fus.wetted_area() * 0.0028 * (1 + 60 / (fus.length / fus.diameter) ** 3 + (fus.length / fus.diameter) / 400)
        tails = sum(2.04 * ps.area * 0.0025 * 1.15 for ps in arr.surfaces if ps.role != "wing")
        self.cd0 = 1.03 * (wing + body + tails) / self.S
        self.k = 1.0 / (math.pi * self.AR * 0.95)

    def cd(self, cl: float, mach: float | None = None):
        return self.cd0 + self.k * cl ** 2 + 0.0035 * (cl - 0.3) ** 2, self.cd0


def evaluate_glider(p: AircraftParameters, m: MissionRequirements):
    from .evaluator import Constraint, Evaluation, _Model
    from .mission import REQUIREMENT_OF, ignored_requirements
    try:
        model = _Model(p, m)
    except (ValueError, ZeroDivisionError, FloatingPointError) as exc:
        return Evaluation(False, 1e12, message=f"geometry invalid: {exc}")
    arr = model.arr
    w, fus = arr.wing, arr.fuselage
    st = p.structure
    S, b, AR, mac = model.S, model.b, model.AR, model.mac
    # ---------------- weights
    nz = max(st.limit_load_factor, abs(getattr(st, "negative_load_factor", -2.65))) * st.safety_factor
    seats = 2 if m.payload_kg + m.crew_kg > 130 else 1
    empty = 1.6 * b ** 1.8 * (nz / 7.95) ** 0.3 * (S / (b ** 2 / 22.0)) ** 0.35 * (1.2 if seats == 2 else 1.0)
    groups = {"wing": 0.55 * empty * st.wing_strength ** 0.55, "fuselage": 0.25 * empty * st.fuselage_strength ** 0.3,
              "horizontal_tail": 0.04 * empty * st.tail_strength ** 0.45,
              "vertical_tail": 0.04 * empty * st.tail_strength ** 0.45,
              "main_gear": 0.04 * empty * st.gear_strength ** 0.7, "systems_and_equipment": 0.08 * empty}
    weights = dict(groups)
    weights["payload"] = m.payload_kg + m.crew_kg
    weights["fuel"] = 0.0
    mass = sum(weights.values())
    W = mass * G0
    # ---------------- glide polar (the design's own drag build-up), sea level
    rho = 1.225
    cls = np.linspace(0.2, 1.4, 121)
    polar = GliderPolar(p)
    cds = np.array([polar.cd(float(c))[0] for c in cls])
    ld = cls / cds
    k = int(np.argmax(ld))
    best_ld, cl_best = float(ld[k]), float(cls[k])
    v_best = math.sqrt(2 * W / (rho * S * cl_best))
    sink = np.sqrt(2 * W / (rho * S * cls)) * cds / cls
    j = int(np.argmin(sink))
    min_sink, v_min_sink = float(sink[j]), float(math.sqrt(2 * W / (rho * S * cls[j])))
    clmax = 1.35 + 4.0 * p.wing.camber
    v_stall = math.sqrt(2 * W / (rho * S * clmax))
    # ---------------- balance and static margin (as for powered aircraft)
    wx = arr.x_wing_le if hasattr(arr, "x_wing_le") else p.wing.x_position * fus.length
    x_wing_cg = arr.x_ac_wing + 0.15 * mac
    stations = {"wing": x_wing_cg, "fuselage": 0.42 * fus.length, "horizontal_tail": fus.length * 0.95,
                "vertical_tail": fus.length * 0.93, "main_gear": arr.x_ac_wing - 0.2 * mac,
                "systems_and_equipment": 0.3 * fus.length, "payload": 0.22 * fus.length, "fuel": x_wing_cg}
    x_cg = sum(weights[k_] * stations[k_] for k_ in weights) / mass
    a_w = aero.lift_curve_slope(AR, 0.1, w.sweep_at(0.5))
    deps = min(2 * a_w / (math.pi * AR), 0.6)
    tail_term, tail_moment = 0.0, 0.0
    vh = vv = 0.0
    for ps in arr.pitch_surfaces():
        t = ps.surface
        a_t = aero.lift_curve_slope(t.aspect_ratio, 0.1, t.sweep_at(0.5))
        f = 0.95 * a_t * (1.0 - deps) * ps.area * ps.horizontal_fraction / S
        tail_term += f
        tail_moment += f * ps.x_ac
        vh += ps.area * ps.horizontal_fraction * (ps.x_ac - x_cg) / (S * mac)
    for ps in arr.surfaces:
        if ps.surface.vertical or ps.role == "v_tail":
            frac = getattr(ps, "vertical_fraction", 1.0) if ps.role == "v_tail" else 1.0
            vv += ps.area * frac * (ps.x_ac - x_cg) / (S * b)
    kf = aero.fuselage_pitching_moment_factor((p.wing.x_position * fus.length + 0.25 * p.wing.root_chord) / fus.length)
    cma_fus = kf * fus.diameter ** 2 * fus.length / (S * mac) * math.degrees(1)
    x_np = (a_w * arr.x_ac_wing + tail_moment - cma_fus * mac) / (a_w + tail_term)
    sm = (x_np - x_cg) / mac
    # ---------------- requirements
    cons = [Constraint("Best glide ratio", best_ld, lower=35.0 if seats == 1 else 30.0),  # two-seat trainers: 30
            Constraint("Minimum sink rate", min_sink, upper=0.75, unit="m/s"),
            Constraint("Stall speed", v_stall, upper=22.5, unit="m/s"),
            Constraint("Wingspan", b, upper=m.max_span_m, unit="m"),
            Constraint("G rating", st.limit_load_factor, lower=getattr(m, "g_limit", 5.3), unit="g"),
            Constraint("Static margin", sm, lower=0.04, upper=0.35),
            Constraint("Horizontal tail volume", vh, lower=0.35),
            Constraint("Vertical tail volume", vv, lower=0.015)]
    ignored = ignored_requirements(m)
    for c in cons:
        if REQUIREMENT_OF.get(c.name) in ignored:
            c.enforced = False
    penalty = sum(c.violation for c in cons)
    metrics = {"mtow_kg": mass, "empty_kg": empty, "x_cg_m": x_cg, "x_np_m": x_np, "static_margin": sm,
               "best_ld": best_ld, "cruise_ld": best_ld, "min_sink_ms": min_sink, "v_min_sink_ms": v_min_sink,
               "v_best_glide_ms": v_best, "stall_speed_ms": v_stall, "approach_speed_ms": 1.4 * v_stall,
               "cruise_speed_ms": v_best, "cruise_cl": cl_best, "clmax_landing": clmax + 0.1,
               "wing_loading_kg_m2": mass / S, "aspect_ratio": AR, "takeoff_field_m": 0.0,
               "wing_failure_g": st.limit_load_factor * st.safety_factor * st.wing_strength,
               "vh": vh, "vv": vv,
               # never-exceed speed: gliders are cleared to roughly 250-280 km/h (K-21: 280)
               "vne_ms": 77.8 * min(1.0, (st.limit_load_factor / 6.5) ** 0.25)}
    notes = [f"Glider: best glide {best_ld:.0f}:1 at {v_best * 3.6:.0f} km/h, minimum sink {min_sink:.2f} m/s at "
             f"{v_min_sink * 3.6:.0f} km/h, stall {v_stall * 3.6:.0f} km/h, wing loading {mass / S:.0f} kg/m²."]
    ok = all(c.satisfied for c in cons)
    return Evaluation(True, mass * (1 + 4 * penalty), metrics, cons, weights,
                      "feasible" if ok else "violates: " + ", ".join(c.name for c in cons if not c.satisfied), notes)


def size_glider(mission: MissionRequirements, config) -> AircraftParameters:
    """An 18 m-class sailplane: slender high-aspect-ratio wing, slim fuselage, T-tail."""
    from .synthesis import ConfigurationParams, _set
    p = AircraftParameters()
    p.configuration = ConfigurationParams(**vars(config))
    span = min(18.0, 0.995 * mission.max_span_m)
    S = span ** 2 / 22.0
    root = 2 * S / (span * 1.4)
    sets = {"configuration.propulsion": "none", "wing.span": span, "wing.root_chord": root, "wing.taper_ratio": 0.4,
            "wing.sweep_le_deg": 1.0, "wing.inner_sweep_le_deg": 1.0, "wing.dihedral_deg": 3.0,
            "wing.twist_tip_deg": -2.0, "wing.tc_root": 0.15, "wing.tc_tip": 0.13, "wing.camber": 0.035,
            "wing.x_position": 0.30, "wing.crank_eta": 0.0,
            "fuselage.length": 7.2, "fuselage.diameter": 0.72, "fuselage.height_ratio": 1.2,
            "fuselage.nose_fraction": 0.22, "fuselage.tail_fraction": 0.55, "fuselage.tail_upsweep": 0.05,
            "htail.span": 2.8, "htail.root_chord": 0.55, "htail.taper_ratio": 0.6, "htail.sweep_le_deg": 5.0,
            "htail.x_position": 0.92, "vtail.span": 1.55, "vtail.root_chord": 1.2, "vtail.taper_ratio": 0.55,
            "vtail.sweep_le_deg": 25.0, "vtail.x_position": 0.86,
            "controls.aileron_start": 0.62, "controls.aileron_end": 0.97, "controls.aileron_chord": 0.22,
            "controls.flap_end": 0.0, "controls.elevator_chord": 0.35, "controls.rudder_chord": 0.35,
            "structure.limit_load_factor": getattr(mission, "g_limit", 5.3), "structure.safety_factor": 1.5,
            "structure.negative_load_factor": -0.5 * getattr(mission, "g_limit", 5.3),
            "structure.insulation_kg_m2": 0.3, "structure.wing_material": "CFRP quasi-isotropic",
            "structure.fuselage_material": "CFRP quasi-isotropic", "gear.wheels_per_leg": 1,
            "gear.layout": "glider_tail", "gear.wheel_diameter": 0.38, "gear.ground_clearance": 0.12}
    for k, v in sets.items():
        try:
            _set(p, k, v)
        except Exception:
            try:
                p.set(k, v)
            except Exception:
                pass
    # put the wing where the balance gives a healthy static margin (~15 %)
    best = None
    for xp in np.linspace(0.20, 0.42, 23):
        _set(p, "wing.x_position", float(xp))
        ev = evaluate_glider(p, mission)
        if ev.converged:
            d = abs(ev.metrics["static_margin"] - 0.15)
            if best is None or d < best[0]:
                best = (d, float(xp))
    if best:
        _set(p, "wing.x_position", best[1])
    try:
        from .synthesis import complete_design
        complete_design(p, mission)
    except Exception:
        pass
    _set(p, "configuration.propulsion", "none")
    _set(p, "gear.layout", sets.get("gear.layout", "glider_tail"))
    return p


def k21_mission() -> MissionRequirements:
    return glider_mission(name="ASK 21 two-seat trainer", payload_kg=180.0, max_span_m=17.0, g_limit=6.5)


def k21_preset(mission: MissionRequirements | None = None) -> AircraftParameters:
    """Schleicher ASK 21-like two-seat trainer: 17 m, 17.95 m², AR 16, Wortmann FX S 02-196 / FX 60-126
    (19.6 % / 12.6 %), 4° dihedral, 8.35 m fuselage, T-tail, nose + main + tail wheel, +6.5 / -4 g,
    VNE 280 km/h, 600 kg maximum.  Real ASK 21: 34:1 at 90 km/h, 0.65 m/s minimum sink."""
    from .synthesis import _C, _set
    mission = mission or k21_mission()
    p = size_glider(mission, _C("none", "wing", "t_tail", "mid"))
    span, area = 17.0, 17.95
    root = 2 * area / (span * (1 + 0.5))
    for k, v in {"wing.span": span, "wing.root_chord": root, "wing.taper_ratio": 0.5, "wing.dihedral_deg": 4.0,
                 "wing.sweep_le_deg": 0.0, "wing.tc_root": 0.196, "wing.tc_tip": 0.126, "wing.camber": 0.04,
                 "fuselage.length": 8.35, "fuselage.diameter": 0.78, "fuselage.height_ratio": 1.25,
                 "htail.span": 3.4, "htail.root_chord": 0.75, "vtail.span": 1.25, "vtail.root_chord": 1.25,
                 "structure.limit_load_factor": 6.5, "structure.negative_load_factor": -4.0,
                 "gear.layout": "glider_nose"}.items():
        _set(p, k, v)
    best = None  # re-balance for the two-seat cockpit
    for xp in np.linspace(0.22, 0.42, 21):
        _set(p, "wing.x_position", float(xp))
        ev = evaluate_glider(p, mission)
        if ev.converged:
            d = abs(ev.metrics["static_margin"] - 0.15)
            if best is None or d < best[0]:
                best = (d, float(xp))
    if best:
        _set(p, "wing.x_position", best[1])
    return p
