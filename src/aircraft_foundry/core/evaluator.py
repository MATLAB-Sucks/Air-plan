"""Conceptual performance evaluation of a candidate design.

The evaluation closes the take-off weight (W0 = payload + crew + empty(W0) +
fuel(W0)) with SciPy's ``brentq``, builds a component drag polar, estimates
the neutral point and centre of gravity, and checks constraints.  It uses the
same :class:`Arrangement` the geometry builder uses, so analysed and drawn
geometry are identical.  All methods are conceptual-level statistics: good
for ranking and trend studies, not for certification.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.optimize import brentq

from ..geometry.arrangement import Arrangement
from ..numerics import aerodynamics as aero
from ..numerics import noise as nz
from ..numerics import propulsion as prop
from ..numerics import sonic_boom as sb
from ..numerics import supersonic as ss
from ..numerics import weights as wt
from ..numerics.atmosphere import G0, RHO0, isa
from .mission import MissionRequirements
from .parameters import AircraftParameters
from .structures import StructuralModel, StructuralResult

NZ_ULT = 3.75
FUEL_DENSITY = 800.0
SEGMENT_FRACTIONS = {"takeoff": 0.970, "climb": 0.985, "landing": 0.995}
RESERVE_LOITER_S = 1800.0
TRAPPED_RESERVE = 1.06


@dataclass
class Constraint:
    name: str
    value: float
    lower: float = -math.inf
    upper: float = math.inf
    unit: str = ""
    enforced: bool = True  # False: the user chose not to design around this requirement

    @property
    def satisfied(self) -> bool:
        if not self.enforced:
            return True
        return self.lower - 1e-9 <= self.value <= self.upper + 1e-9

    @property
    def met(self) -> bool:
        """Whether the value is within limits, regardless of enforcement (for reporting ignored ones)."""
        return self.lower - 1e-9 <= self.value <= self.upper + 1e-9

    @property
    def violation(self) -> float:
        """Normalised amount by which the constraint is violated (0 if satisfied)."""
        if not self.enforced or not np.isfinite(self.value):  # ignored, or e.g. a propeller that does not exist
            return 0.0
        if self.value < self.lower:
            return (self.lower - self.value) / max(abs(self.lower), 1e-3)
        if self.value > self.upper:
            return (self.value - self.upper) / max(abs(self.upper), 1e-3)
        return 0.0


@dataclass
class Evaluation:
    converged: bool
    score: float
    metrics: dict = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    message: str = ""
    notes: list[str] = field(default_factory=list)
    structure: StructuralResult | None = field(default=None, repr=False, compare=False)

    @property
    def feasible(self) -> bool:
        return self.converged and all(c.satisfied for c in self.constraints)

    @property
    def violated(self) -> list[Constraint]:
        return [c for c in self.constraints if not c.satisfied]

    def to_dict(self) -> dict:
        return {"converged": self.converged, "feasible": self.feasible, "score": self.score,
                "metrics": self.metrics, "weights": self.weights, "message": self.message,
                "constraints": [{**asdict(c), "satisfied": c.satisfied} for c in self.constraints]}


class _Model:
    """Geometry-derived quantities that do not depend on W0."""

    def __init__(self, params: AircraftParameters, mission: MissionRequirements):
        self.p, self.m = params, mission
        self.arr = arr = Arrangement(params)
        self.prop = params.configuration.propulsion
        self.atm = isa(mission.cruise_altitude_m)
        self.V = mission.cruise_mach * self.atm.speed_of_sound
        self.q = 0.5 * self.atm.density * self.V**2
        w, fus = arr.wing, arr.fuselage
        self.S, self.b, self.AR, self.mac = w.area, w.span, w.aspect_ratio, w.mac
        self.sweep_le = math.radians(w.sweep_le_deg)
        self.sweep_qc, self.sweep_hc = w.sweep_at(0.25), w.sweep_at(0.5)
        self.sweep_hc_mdd = self.sweep_hc
        self.tc_mac = float(w.tc(w.mac_eta))
        if w.cranked:
            # compressibility is governed by the outer wing; a BWB/flying-wing centre-body
            # is thick but runs at low local lift coefficient
            e_out = 0.5 * (w.crank_eta + 1.0)
            self.tc_mdd = float(w.tc(e_out))
            self.sweep_hc_mdd = math.atan(math.tan(math.radians(w.sweep_le_deg))
                                          - 0.5 * (w.crank_chord - w.tip_chord) / ((1 - w.crank_eta) * w.semi_span))
        else:
            self.tc_mdd = self.tc_mac
        eta_e = arr.wing_exposed_eta
        self.S_exp = 2 * w.strip_area(eta_e, 1.0)
        self.engines = arr.engines
        self.n_eng = len(self.engines)
        self.thrust_n = params.engines.thrust_kn * 1000.0
        M, rho, mu = mission.cruise_mach, self.atm.density, self.atm.viscosity

        def comp(length, swet, ff, q_int):
            re = rho * self.V * length / mu
            return aero.skin_friction(re, M) * ff * q_int * swet

        self.e = aero.oswald_efficiency(self.AR, self.sweep_le)
        if arr.is_tailless:
            self.e *= 0.92  # elevon trim and washout distort the span loading
        if arr.layout == "supersonic_delta":
            self.e *= 0.85  # slender delta: little leading-edge suction
        parts = {"wing": comp(self.mac, aero.wetted_area_surface(self.S_exp, self.tc_mac),
                              aero.form_factor_wing(self.tc_mac, M, w.sweep_at(0.3)), 1.0)}
        for ps in arr.surfaces[1:]:  # tails, canards, fins: whatever the layout has
            t = ps.surface
            exposed = 0.92 if ps.role in ("htail", "canard") and ps.attach_to == "fuselage" else 1.0
            parts[ps.key] = comp(t.mac, aero.wetted_area_surface(exposed * t.area, t.tc_root),
                                 aero.form_factor_wing(t.tc_root, M, t.sweep_at(0.3)), 1.04)
        for body in arr.bodies:
            bs = body.shape
            buried = 0.55 if arr.has_pod and body.role == "fuselage" else 1.0  # pod blended into the wing
            parts[body.key] = comp(bs.length, buried * bs.wetted_area(), aero.form_factor_body(bs.length / bs.diameter),
                                   1.0 if body.role == "fuselage" else 1.1)
        nac = 0.0
        for e in self.engines:
            buried = e.mount == "wing" and e.attach[2] < e.inlet[2]
            q_int = 1.2 if buried else (1.3 if e.mount == "wing" else 1.5)
            nac += comp(e.length, (0.5 if buried else 0.85) * math.pi * e.diameter * e.length,
                        aero.form_factor_nacelle(e.length / e.diameter), q_int)
        parts["nacelles"] = nac
        if not arr.has_pod:
            upsweep = math.atan2(params.fuselage.tail_upsweep * fus.diameter,
                                 params.fuselage.tail_fraction * fus.length)
            parts["upsweep"] = aero.upsweep_drag_area(upsweep, math.pi * fus.radius**2 * fus.height_ratio)
        self.drag_areas = parts
        self.CD0 = 1.05 * sum(parts.values()) / self.S  # +5 % leakage & protuberance
        self.K = 1 / (math.pi * self.AR * self.e)
        self.kappa = 0.95 if self.prop == "turbofan" else 0.87
        self.flapped_fraction = arr.flapped_area_fraction()
        slats = self.prop == "turbofan" and not arr.is_tailless
        self.clmax_land = aero.clmax_landing(self.sweep_qc, slats=slats, flapped_fraction=self.flapped_fraction,
                                             flap_chord=params.controls.flap_chord)
        if arr.is_tailless:
            self.clmax_land *= 0.85  # elevons must trim: no flaps and a nose-down trim penalty
        slat_area = 0.12 * self.S if slats else 0.0
        self.wing_control_area = arr.wing_control_area() + slat_area
        self.struct = StructuralModel(params, mission.cruise_altitude_m, mission.cruise_mach, arr,
                                      passengers=mission.passengers)
        self.structural_weights = params.structure.weight_method == "structural"
        if arr.layout == "supersonic_delta":
            self.clmax_land = 1.1  # slender delta: vortex lift at high angle of attack, no flaps
        self.clmax_to = 0.8 * self.clmax_land
        self.bpr = params.engines.bypass_ratio
        self.c_cruise = prop.tsfc_per_hour(mission.cruise_mach, self.prop, self.bpr) / 3600.0
        self.c_loiter = 0.85 * prop.tsfc_per_hour(0.5, self.prop, self.bpr) / 3600.0
        self.LDmax = 0.5 * math.sqrt(math.pi * self.AR * self.e / self.CD0)
        # ---- supersonic: area-rule wave drag and sonic-boom geometry (computed once per design)
        self.supersonic = mission.cruise_mach > 1.0
        self.boom_x, self.boom_avol = arr.area_distribution(240)
        self.dq_wave_m1 = ss.area_rule_wave_drag_area(self.boom_x, self.boom_avol)
        self.boom_lift_unit = arr.lift_distribution(self.boom_x, 1.0)
        self.boom_kernel = sb.f_matrix(self.boom_x) if self.supersonic else None
        self._boom_cache: dict = {}

    # ------------------------------------------------------------ sonic boom
    def boom(self, weight_n: float, mach: float | None = None, altitude: float | None = None) -> sb.BoomResult:
        mach = self.m.cruise_mach if mach is None else mach
        altitude = self.m.cruise_altitude_m if altitude is None else altitude
        key = (round(weight_n, -2), round(mach, 3), round(altitude, -1))
        if key not in self._boom_cache:
            kernel = self.boom_kernel if self.boom_kernel is not None else sb.f_matrix(self.boom_x)
            lift = self.boom_lift_unit * weight_n
            ae = self.boom_avol + sb.lift_equivalent_area(self.boom_x, lift, max(mach, 1.01), altitude)
            self._boom_cache[key] = sb.n_wave(self.boom_x, ae, mach, altitude, kernel=kernel)
        return self._boom_cache[key]

    def boom_pldb(self, weight_n: float, mach: float | None = None, altitude: float | None = None) -> float:
        r = self.boom(weight_n, mach, altitude)
        return sb.perceived_level_db(r.overpressure_psf, self.shaping_credit(r))

    def shaping_credit(self, r: sb.BoomResult) -> float:
        fus = self.arr.fuselage
        nose_fineness = fus.x_nose_end / fus.diameter
        on_top = any(e.inlet[2] > float(fus.centre_z(e.centre[0])) for e in self.engines)
        return sb.shaping_credit_db(nose_fineness, on_top, r.front_loading)

    def overland_mach(self, weight_n: float) -> tuple[float, str]:
        """Fastest overland Mach that respects the ground boom limit."""
        M = self.m.cruise_mach
        if not self.supersonic or self.m.overland_fraction <= 0:
            return M, "same as cruise"
        level = self.boom_pldb(weight_n)
        from .mission import ignored_requirements
        if "boom" in ignored_requirements(self.m):
            return M, f"sonic boom over land not considered: cruise Mach overland (boom {level:.0f} PLdB)"
        if level <= self.m.max_boom_pldb:
            return M, f"supersonic overland: boom {level:.0f} PLdB within the {self.m.max_boom_pldb:.0f} PLdB limit"
        mc = sb.mach_cutoff(self.m.cruise_altitude_m)
        m_cut = mc - 0.02
        ld_cut = self.cruise_ld(weight_n, m_cut)[0]
        ld_sub = self.cruise_ld(weight_n, 0.90)[0]
        if m_cut > 1.0 and ld_cut > 0.75 * ld_sub:
            return m_cut, (f"boom {level:.0f} PLdB exceeds the {self.m.max_boom_pldb:.0f} PLdB limit: flies Mach "
                           f"{m_cut:.2f} over land (below Mach cutoff {mc:.2f}, so no boom reaches the ground)")
        return 0.90, (f"boom {level:.0f} PLdB exceeds the {self.m.max_boom_pldb:.0f} PLdB limit: flies Mach 0.90 "
                      f"over land")

    # ------------------------------------------------------------ polar
    def supersonic_wave_cd(self, mach: float) -> float:
        return ss.WAVE_DRAG_REALISM * self.dq_wave_m1 * ss.mach_sweep_factor(mach, self.arr.wing.sweep_le_deg) / self.S

    def cd(self, cl: float, mach: float | None = None) -> tuple[float, float]:
        """Drag coefficient across subsonic, transonic and supersonic flight."""
        M = self.m.cruise_mach if mach is None else mach
        mdd = aero.drag_divergence_mach(self.tc_mdd, cl, self.sweep_hc_mdd, self.kappa)
        if M <= 0.95:
            return self.CD0 + self.K * cl**2 + aero.wave_drag(M, mdd), mdd
        sweep = self.arr.wing.sweep_le_deg
        k_sup = ss.drag_due_to_lift_supersonic(self.AR, max(M, 1.2), sweep)
        if M >= 1.2:
            return self.CD0 + k_sup * cl**2 + self.supersonic_wave_cd(M), mdd
        # transonic: wave drag rises from the subsonic drag-divergence value to a peak near Mach 1.05
        w12 = self.supersonic_wave_cd(1.2)
        w_sub = aero.wave_drag(0.95, mdd)
        if M < 1.05:
            t = (M - 0.95) / 0.10
            cdw = w_sub + (1.1 * w12 - w_sub) * t * t * (3 - 2 * t)
        else:
            cdw = w12 * (1.1 - 0.1 * (M - 1.05) / 0.15)
        tk = (M - 0.95) / 0.25
        return self.CD0 + ((1 - tk) * self.K + tk * k_sup) * cl**2 + cdw, mdd

    def q_at(self, mach: float, altitude: float | None = None) -> float:
        atm = isa(self.m.cruise_altitude_m if altitude is None else altitude)
        return 0.5 * atm.density * (mach * atm.speed_of_sound) ** 2

    def cruise_ld(self, weight_n: float, mach: float | None = None) -> tuple[float, float, float]:
        M = self.m.cruise_mach if mach is None else mach
        cl = weight_n / (self.q_at(M) * self.S)
        cd, mdd = self.cd(cl, M)
        return cl / cd, cl, mdd

    # ------------------------------------------------------------ weights
    def tail_weights(self, w0: float) -> dict[str, float]:
        """Weight of every non-wing surface and boom, grouped (Raymer tail equations)."""
        arr = self.arr
        out = {"horizontal_tail": 0.0, "vertical_tail": 0.0, "canard": 0.0, "booms": 0.0}
        for ps in arr.surfaces[1:]:
            t = ps.surface
            arm = abs(ps.x_ac - arr.x_ac_wing)
            if ps.role in ("htail", "canard", "v_tail"):
                key = "canard" if ps.role == "canard" else "horizontal_tail"
                out[key] += wt.htail_weight(w0, self.nz_ult, t.area, arm, t.aspect_ratio, t.sweep_at(0.25))
            else:  # fin
                out["vertical_tail"] += wt.vtail_weight(w0, self.nz_ult, t.area, arm, t.aspect_ratio, t.sweep_at(0.25),
                                                        t.tc_root, arr.is_t_tail)
        for body in arr.bodies:
            if body.role == "boom":
                out["booms"] += 12.0 * body.shape.wetted_area()  # kg/m^2 incl. tail-load structure
        return {k: v for k, v in out.items() if v > 0}

    def tail_stations(self) -> dict[str, float]:
        arr, acc = self.arr, {}
        for ps in arr.surfaces[1:]:
            key = {"htail": "horizontal_tail", "v_tail": "horizontal_tail", "canard": "canard"}.get(ps.role,
                                                                                               "vertical_tail")
            t = ps.surface
            acc.setdefault(key, []).append((ps.placement.position[0] + t.x_mac_le + 0.4 * t.mac, t.area))
        for body in arr.bodies:
            if body.role == "boom":
                acc.setdefault("booms", []).append((body.offset[0] + 0.5 * body.shape.length, 1.0))
        return {k: float(np.average([x for x, _ in v], weights=[a for _, a in v])) for k, v in acc.items()}

    @property
    def nz_ult(self) -> float:
        """Ultimate load factor of *this* design (limit x safety factor): 3.75 for airliners, 15 for +10 g stunt planes."""
        return self.p.structure.limit_load_factor * self.p.structure.safety_factor

    @property
    def light_aircraft(self) -> bool:
        """Light-aircraft weight statistics: aerobatic designs and small propeller aircraft."""
        m = self.m
        return getattr(m, "category", "transport") == "aerobatic" or (
            self.prop == "turboprop" and m.payload_kg <= 1000 and m.cruise_mach <= 0.45)

    def group_weights(self, w0: float) -> dict:
        p, arr = self.p, self.arr
        fus = arr.fuselage
        w_land = 0.85 * w0
        vs = math.sqrt(2 * w_land * G0 / (RHO0 * self.S * self.clmax_land))
        legs = {g.name: g for g in arr.gear}
        out = {
            "wing": wt.wing_weight(w0, self.nz_ult, self.S, self.AR, self.tc_mac, p.wing.taper_ratio, self.sweep_qc,
                                   self.wing_control_area),
            **self.tail_weights(w0),
            "fuselage": wt.fuselage_weight(w0, self.nz_ult, fus.length, fus.diameter, fus.wetted_area(), self.b,
                                           p.wing.taper_ratio, self.sweep_qc,
                                           p.configuration.wing_position == "high"),
            "main_gear": wt.main_gear_weight(w_land, legs["main_right"].strut_length, legs["main_right"].wheels, vs),
            "nose_gear": wt.nose_gear_weight(w_land, legs["nose"].strut_length, legs["nose"].wheels),
            "engines_installed": 1.6 * self.n_eng * wt.engine_dry_weight(p.engines.thrust_kn, self.prop),
            "systems_and_equipment": wt.systems_and_equipment(w0),
            "furnishings_and_operator_items": wt.furnishings_and_operator_items(self.m.passengers),
            "acoustic_insulation": p.structure.insulation_kg_m2 * self.cabin_wall_area(),
        }
        if self.light_aircraft:
            out.update(self.light_aircraft_weights(w0, out))
        self.apply_strength(out)
        if arr.layout == "blended_wing_body":
            # The pressurised cabin lives in the centre-body.  Approximation: the outer
            # wing panels by Raymer's wing equation, plus a non-circular pressure shell
            # 15 % heavier than the equivalent tube fuselage for the same cabin.
            w = arr.wing
            s_out = 2 * w.strip_area(w.crank_eta, 1.0) if w.cranked else self.S
            out["wing"] = wt.wing_weight(w0, self.nz_ult, s_out, self.AR, self.tc_mac, p.wing.taper_ratio, self.sweep_qc,
                                         self.wing_control_area)
            m = self.m
            l_eq = m.cabin_length_m / (1 - 0.55 * 0.12 - 0.65 * 0.26)
            d_eq = m.fuselage_diameter_m
            wet_eq = math.pi * d_eq * l_eq * 0.8
            out["centrebody_pressure_shell"] = 1.15 * wt.fuselage_weight(w0, self.nz_ult, l_eq, d_eq, wet_eq, self.b,
                                                                         p.wing.taper_ratio, self.sweep_qc, False)
        if self.structural_weights:  # replace the statistical wing/fuselage with sized structure
            sm = self.struct.masses(w0, self.fuel(w0)[0], out["wing"], self.engine_mass_each(),
                                    self.fuselage_load_args(w0, out))
            out["wing"], out["fuselage"] = sm["wing_total"], sm["fuselage_total"]
        return out

    def apply_strength(self, out: dict) -> None:
        """Per-component strength reserve and material choice.

        Structure sized for k x the load is heavier by roughly k^n (n from the statistical load
        exponents: wing ~0.55, tails ~0.45, gear ~0.7, fuselage ~0.3 - much of a fuselage is sized by
        pressure, handling and fatigue rather than flight load).  A stronger-for-its-weight material
        (higher strength / density than the default aluminium) makes the wing and fuselage lighter.
        """
        from ..numerics.materials import material
        st = self.p.structure

        def specific(name):
            mt = material(name)
            return mt.ftu * mt.tension_knockdown / mt.density
        f_wing = (specific("Al 7075-T6") / specific(st.wing_material)) ** 0.6
        f_fus = (specific("Al 2024-T3") / specific(st.fuselage_material)) ** 0.5
        if "wing" in out:
            out["wing"] *= st.wing_strength ** 0.55 * f_wing
        if "fuselage" in out:
            out["fuselage"] *= st.fuselage_strength ** 0.3 * f_fus
        for k in ("horizontal_tail", "vertical_tail"):
            if k in out:
                out[k] *= st.tail_strength ** 0.45 * f_wing
        for k in ("main_gear", "nose_gear"):
            if k in out:
                out[k] *= st.gear_strength ** 0.7

    def light_aircraft_weights(self, w0: float, transport: dict) -> dict:
        """Raymer's general-aviation equations (transport statistics badly overestimate light aircraft)."""
        p, arr = self.p, self.arr
        fus = arr.fuselage
        nz = self.nz_ult
        q = self.q
        fuel = self.fuel(w0)[0]
        out = {"wing": wt.ga_wing_weight(w0, nz, self.S, self.AR, self.tc_mac, p.wing.taper_ratio, self.sweep_qc,
                                         q, fuel)}
        ht = vt = 0.0
        for ps in arr.surfaces:
            if ps.role == "wing":
                continue
            t = ps.surface
            area = ps.area
            if t.vertical:
                vt += wt.ga_vtail_weight(w0, nz, area, t.aspect_ratio, t.sweep_at(0.25), 0.11, 0.6, q,
                                         arr.is_t_tail)
            else:
                ht += wt.ga_htail_weight(w0, nz, area, t.aspect_ratio, t.sweep_at(0.25), 0.10, 0.6, q)
        if "horizontal_tail" in transport:
            out["horizontal_tail"] = ht
        if "vertical_tail" in transport:
            out["vertical_tail"] = vt
        tail_arm = max(fus.length * 0.5, 1.0)
        out["fuselage"] = wt.ga_fuselage_weight(w0, nz, fus.wetted_area(), tail_arm, fus.length, fus.diameter, q)
        legs = {g.name: g for g in arr.gear}
        main, nose = wt.ga_gear_weight(0.95 * w0, legs["main_right"].strut_length, legs["nose"].strut_length)
        out["main_gear"], out["nose_gear"] = main, nose
        out["systems_and_equipment"] = wt.ga_systems_weight(w0, nz, fuel, fus.length, self.b, self.n_eng)
        out["furnishings_and_operator_items"] = wt.ga_furnishings_weight(w0)
        out["acoustic_insulation"] = 0.0 if getattr(self.m, "category", "") == "aerobatic" else \
            transport.get("acoustic_insulation", 0.0)
        out["engines_installed"] = 1.35 * self.n_eng * wt.engine_dry_weight(p.engines.thrust_kn, self.prop)
        return out

    def roll_rate_deg_s(self, speed: float, max_deflection_deg: float = 20.0) -> float:
        """Steady roll rate at full aileron (strip theory, Raymer 16.6): Clda / Clp."""
        w = self.arr.wing
        cla = self.cla_low_speed if hasattr(self, "cla_low_speed") else 2 * math.pi * self.AR / (
            2 + math.sqrt(4 + self.AR ** 2 * (1 + math.tan(w.sweep_at(0.5)) ** 2)))
        clda = 0.0
        for cs in self.arr.control_surfaces:
            if cs.surface != "wing" or cs.kind not in ("aileron", "elevon_outer"):
                continue
            th = math.acos(2 * cs.chord_fraction - 1)
            tau = 1 - (th - math.sin(th)) / math.pi
            etas = np.linspace(cs.eta0, cs.eta1, 20)
            ys = etas * w.semi_span
            cs_ = np.array([w.chord(e) for e in etas])
            clda += cla * tau * np.trapezoid(cs_ * ys, ys) / (self.S * self.b)
        lam = self.p.wing.taper_ratio
        clp = -cla / 12 * (1 + 3 * lam) / (1 + lam)
        p_hat = clda * math.radians(max_deflection_deg) / max(-clp, 1e-6)
        return math.degrees(p_hat * 2 * speed / self.b)

    def cabin_wall_area(self) -> float:
        fus = self.arr.fuselage
        if self.arr.layout == "blended_wing_body":
            return 2.4 * self.m.cabin_floor_area_m2  # floor-to-ceiling walls and roof of the centre-body cabin
        return math.pi * fus.diameter * min(fus.cabin_length, fus.length)

    def engine_mass_each(self) -> float:
        return 1.6 * wt.engine_dry_weight(self.p.engines.thrust_kn, self.prop)

    def fuselage_load_args(self, w0: float, groups: dict, x_cg: float | None = None) -> dict:
        rear = self.engine_mass_each() * self.n_eng if self.p.configuration.engine_layout == "rear_fuselage" else 0.0
        return {"fuselage_mass": groups["fuselage"],
                "distributed_kg": groups["systems_and_equipment"] + groups["furnishings_and_operator_items"]
                + self.m.payload_kg,
                "crew_kg": self.m.crew_kg,
                "tail_kg": sum(groups.get(k, 0.0) for k in ("horizontal_tail", "vertical_tail", "canard", "booms")),
                "rear_engine_kg": rear, "nose_gear_kg": groups["nose_gear"],
                "x_cg": x_cg if x_cg is not None else self.arr.x_ac_wing - 0.15 * self.mac}

    def fuel(self, w0: float) -> tuple[float, float, float, float]:
        """Return (total fuel incl. reserves, trip fuel, cruise L/D, cruise CL).

        The route is split into an overwater leg at the cruise Mach and an overland
        leg whose Mach is limited by the allowed sonic boom on the ground.
        """
        f_to, f_cl, f_ld = SEGMENT_FRACTIONS["takeoff"], SEGMENT_FRACTIONS["climb"], SEGMENT_FRACTIONS["landing"]
        if self.supersonic:
            f_cl = 0.965  # climb and transonic acceleration
        w_start = w0 * f_to * f_cl
        w_mid = 0.93 * w_start * G0
        ld, cl, _ = self.cruise_ld(w_mid)
        fo = self.m.overland_fraction if self.supersonic else 0.0
        r = self.m.range_km * 1000
        f_cr = math.exp(-r * (1 - fo) * self.c_cruise / (self.V * ld))
        if fo > 0:
            m_o, _ = self.overland_mach(w_mid)
            ld_o = self.cruise_ld(w_mid, m_o)[0]
            v_o = m_o * self.atm.speed_of_sound
            c_o = prop.tsfc_per_hour(m_o, self.prop, self.bpr) / 3600.0
            f_cr *= math.exp(-r * fo * c_o / (v_o * ld_o))
        f_lo = math.exp(-RESERVE_LOITER_S * self.c_loiter / self.LDmax)
        trip = (1 - f_to * f_cl * f_cr * f_ld) * w0
        total = TRAPPED_RESERVE * (1 - f_to * f_cl * f_cr * f_lo * f_ld) * w0
        return total, trip, ld, cl


def evaluate(params: AircraftParameters, mission: MissionRequirements) -> Evaluation:
    try:
        model = _Model(params, mission)
    except (ValueError, ZeroDivisionError, FloatingPointError) as exc:
        return Evaluation(False, 1e12, message=f"geometry invalid: {exc}")
    fixed = mission.payload_kg + mission.crew_kg

    def residual(w0: float) -> float:
        empty = sum(model.group_weights(w0).values())
        fuel = model.fuel(w0)[0]
        return fixed + empty + fuel - w0

    # Bracket the first sign change scanning upward: at very high weights the
    # wing operates far past drag divergence and the residual turns positive
    # again, so a single wide bracket is not safe.
    lo = fixed * 1.05
    try:
        f_lo = residual(lo)
        if f_lo <= 0:
            return Evaluation(False, 1e12, message="weight estimate failed: empty weight model inconsistent")
        hi, f_hi = lo, f_lo
        while f_hi > 0 and hi < fixed * 60:
            lo, f_lo = hi, f_hi
            hi *= 1.25
            f_hi = residual(hi)
    except (ValueError, OverflowError, ZeroDivisionError):
        return Evaluation(False, 1e12, message="weight estimate failed")
    if f_hi > 0:
        return Evaluation(False, 1e12, message="weights do not close: the design cannot carry its own fuel")
    w0 = brentq(residual, lo, hi, xtol=0.5)
    groups = model.group_weights(w0)
    fuel_total, trip_fuel, ld, cl = model.fuel(w0)
    empty = sum(groups.values())
    _, _, mdd = model.cruise_ld(0.93 * w0 * 0.955 * G0)
    arr, p, m = model.arr, params, mission

    # ------------------------------------------------ CG & neutral point
    fus, w = arr.fuselage, arr.wing
    wx = arr.wing_placement.position[0]
    x_wing_cg = wx + w.x_mac_le + 0.4 * w.mac
    if arr.layout == "blended_wing_body":
        x_cabin = wx + 0.38 * w.root_chord  # cabin in the centre-body
    else:
        x_cabin = 0.5 * (fus.x_nose_end + fus.x_tail_start)
    legs = {g.name: g for g in arr.gear}
    x_main, x_nose = float(legs["main_right"].axle[0]), float(legs["nose"].axle[0])
    stations = {
        "wing": x_wing_cg,
        **model.tail_stations(),
        "centrebody_pressure_shell": x_cabin,
        "fuselage": 0.45 * fus.length,
        "main_gear": x_main,
        "nose_gear": x_nose,
        "engines_installed": float(np.mean([e.centre[0] for e in arr.engines])),
        "systems_and_equipment": 0.45 * fus.length,
        "furnishings_and_operator_items": x_cabin,
        "acoustic_insulation": x_cabin,
    }
    masses = dict(groups)
    masses["crew"], stations["crew"] = m.crew_kg, 0.08 * fus.length
    masses["payload"], stations["payload"] = m.payload_kg, x_cabin
    # tailless aircraft arrange their tanks forward to trim (fuel CG ahead of the wing-box centroid)
    masses["fuel"], stations["fuel"] = fuel_total, x_wing_cg - (0.12 * w.mac if arr.is_tailless else 0.0)
    x_cg = sum(masses[k] * stations[k] for k in masses) / sum(masses.values())

    M = m.cruise_mach
    M_stab = min(M, 0.8)  # static margin is assessed subsonically; supersonic AC shift is trimmed by fuel transfer
    a_w = aero.lift_curve_slope(model.AR, M_stab, model.sweep_hc)
    deps = min(2 * a_w / (math.pi * model.AR), 0.6)  # downwash gradient behind the wing
    tail_terms = []
    for ps in arr.pitch_surfaces():
        t = ps.surface
        a_t = aero.lift_curve_slope(t.aspect_ratio, M_stab, t.sweep_at(0.5))
        if ps.role == "canard":  # ahead of the wing: sees upwash, clean flow
            factor, eta_t = 1.0 + 0.5 * deps, 1.0
        else:
            factor, eta_t = 1.0 - deps, (0.95 if arr.is_t_tail or arr.layout == "twin_boom" else 0.9)
        tail_terms.append((eta_t * a_t * factor * ps.area * ps.horizontal_fraction / model.S, ps.x_ac))
    tail_term = sum(f for f, _ in tail_terms)
    kf = aero.fuselage_pitching_moment_factor((wx + 0.25 * p.wing.root_chord) / fus.length)
    cma_fus = kf * fus.diameter**2 * fus.length / (model.S * model.mac) * math.degrees(1)
    if arr.has_pod:
        cma_fus *= 0.5  # pod blended into the centre section
    x_np = (a_w * arr.x_ac_wing + sum(f * x for f, x in tail_terms) - cma_fus * model.mac) / (a_w + tail_term)
    static_margin = (x_np - x_cg) / model.mac

    # ------------------------------------------------ performance
    w0n = w0 * G0
    total_thrust = model.n_eng * model.thrust_n
    tw = total_thrust / w0n
    ws_si = w0n / model.S
    # landing weight: take-off weight less most of the trip fuel (A320 ~0.84 W0, Concorde ~0.66 W0)
    land_frac = float(np.clip(1 - 0.75 * trip_fuel / w0, 0.55, 0.90))
    vs = math.sqrt(2 * land_frac * w0n / (RHO0 * model.S * model.clmax_land))
    v_app = 1.23 * vs
    to_reheat = 1.2 if (model.supersonic and model.bpr <= 1.5) else 1.0  # reheat for take-off (Concorde)
    top_lbft2 = (ws_si / 47.88) / (model.clmax_to * tw * to_reheat)
    tofl = 37.5 * top_lbft2 * 0.3048 if model.prop == "turbofan" else 30.0 * top_lbft2 * 0.3048
    cl2 = model.clmax_to / 1.44
    cd2 = model.CD0 + 0.025 + model.K * cl2**2
    n = model.n_eng
    oei_gradient = (n - 1) / n * tw * to_reheat - cd2 / cl2 if n > 1 else 0.0
    oei_required = {2: 0.024, 3: 0.027, 4: 0.030}.get(n, 0.03)
    lapse = prop.thrust_lapse(model.atm.sigma, M, model.prop, model.bpr)
    # subsonic jets: thrust must cover drag at the start of cruise; supersonic transports
    # cruise-climb as fuel burns (Concorde 50 000 -> 60 000 ft), so check at mid-cruise weight
    w_cruise_check = (w0 - 0.5 * trip_fuel) * G0 if model.supersonic else 0.955 * w0n * 0.97
    cruise_drag = 0.5 * model.atm.density * model.V**2 * model.S * model.cd(w_cruise_check / (model.q * model.S))[0]
    thrust_ratio = total_thrust * lapse / cruise_drag
    fuel_capacity = 2 * w.box_volume(p.wing.front_spar, p.wing.rear_spar, 0.0, 0.85) * 0.85 * FUEL_DENSITY
    if (m.passengers <= 19 or model.supersonic) and not arr.has_pod:
        # business/light aircraft and supersonic transports (Concorde's trim tanks) use fuselage tanks
        cabin_vol = math.pi * fus.radius**2 * fus.height_ratio * min(fus.cabin_length, fus.length)
        fuel_capacity += 0.25 * max(fus.volume - cabin_vol, 0.0) * FUEL_DENSITY
    aft = [ps for ps in arr.pitch_surfaces() if ps.role != "canard"]
    vh = sum(ps.area * ps.horizontal_fraction * (ps.x_ac - arr.x_ac_wing) for ps in aft) / (model.S * model.mac)
    vv = sum(ps.area * ps.vertical_fraction * (ps.x_ac - arr.x_ac_wing) for ps in arr.yaw_surfaces()) / (model.S * model.b)
    z_cg = 0.0
    tipback = math.degrees(math.atan2(x_main - x_cg, z_cg - arr.z_ground))
    nose_load = (x_main - x_cg) / max(x_main - x_nose, 1e-6)
    cabin = fus.cabin_length

    notes: list[str] = []
    sm_band = (0.02, 0.15) if arr.is_tailless else (0.05, 0.30)
    cons = [
        Constraint("Wingspan", model.b, upper=m.max_span_m, unit="m"),
        Constraint("Approach speed", v_app, upper=m.max_approach_speed_ms, unit="m/s"),
        Constraint("Take-off field length", tofl, upper=m.takeoff_field_m, unit="m"),
        # supersonic transports cruise at maximum dry thrust (Concorde); subsonic jets keep a climb margin
        Constraint("Cruise thrust margin", thrust_ratio, lower=1.02 if model.supersonic else 1.15),
        Constraint("Static margin", static_margin, *sm_band),
        Constraint("Wing fuel capacity ratio", fuel_capacity / fuel_total, lower=1.0),
        Constraint("Tip-back angle", tipback, lower=15.0, unit="deg"),
        Constraint("Nose gear load fraction", nose_load, 0.06, 0.20),
        Constraint("Engine ground clearance", arr.engine_ground_clearance(),
                   lower=0.45 if model.prop == "turbofan" else 0.30, unit="m"),
    ]
    if M <= 0.9:
        cons.append(Constraint("Drag-divergence margin", mdd - M, lower=-0.02))
    # ---- noise: people on the ground and in the cabin
    climb = tw - cd2 / cl2
    fly_r = nz.flyover_distance_m(tofl, climb)
    flyover = nz.flyover_level_db(n, model.thrust_n, model.bpr, fly_r, model.prop)
    cruise_thrust_each = cruise_drag / max(n, 1)
    cab_d, shielded = 2.0, False
    if arr.engines:
        e0 = arr.engines[0]
        cab_d = max(abs(e0.centre[1]) - fus.radius, 0.6 * fus.radius, 0.8)
        shielded = e0.mount == "wing" and e0.attach[2] < e0.inlet[2]  # buried above the wing
    eng_wall = nz.engine_level_at_wall_db(n, cruise_thrust_each, model.bpr, cab_d, shielded)
    if M > 1.0:
        eng_wall -= 25.0  # supersonic: the aircraft outruns its own jet noise (only structure-borne noise remains)
    if model.prop == "turboprop":
        eng_wall += 8.0  # propeller blade-passage tones
    wall_mass = nz.SKIN_AND_TRIM_KG_M2 + p.structure.insulation_kg_m2
    cabin = nz.cabin_level_dba(model.q, eng_wall, wall_mass, M)
    insulation_needed = nz.insulation_needed_kg_m2(m.max_cabin_dba - 0.5, model.q, eng_wall, M)
    cons.append(Constraint("Airport noise (take-off flyover)", flyover, upper=m.max_flyover_db, unit="dB"))
    cons.append(Constraint("Cabin noise", cabin, upper=m.max_cabin_dba, unit="dB(A)"))
    # ---- supersonic: transonic acceleration, sonic boom
    boom_psf = boom_level = 0.0
    m_over, over_note = M, ""
    if model.supersonic:
        alt_t = min(11000.0, m.cruise_altitude_m)
        atm_t = isa(alt_t)
        w_t = 0.95 * w0n
        q_t = 0.5 * atm_t.density * (1.1 * atm_t.speed_of_sound) ** 2
        drag_t = q_t * model.S * model.cd(w_t / (q_t * model.S), 1.1)[0]
        reheat = 1.25 if model.bpr <= 1.5 else 1.0  # low-bypass engines light reheat to cross Mach 1 (Concorde)
        avail_t = reheat * total_thrust * prop.thrust_lapse(atm_t.sigma, 1.1, model.prop, model.bpr)
        cons.append(Constraint("Transonic acceleration margin (Mach 1.1)", avail_t / drag_t, lower=1.1))
        w_mid = 0.93 * w0n * 0.95
        br = model.boom(w_mid)
        boom_psf, boom_level = br.overpressure_psf, model.boom_pldb(w_mid)
        m_over, over_note = model.overland_mach(w_mid)
        notes.append(f"Sonic boom at cruise: {boom_psf:.2f} psf, about {boom_level:.0f} PLdB on the ground "
                     f"(Concorde ~105, NASA X-59 design target ~75). Mach cutoff at this altitude: {br.mach_cutoff:.2f}.")
        if m.overland_fraction > 0:
            notes.append("Overland: " + over_note + ".")
        if m.overland_fraction > 0 and m_over == M:
            cons.append(Constraint("Sonic boom overland", boom_level, upper=m.max_boom_pldb, unit="PLdB"))
    if getattr(m, "category", "transport") == "aerobatic":
        n_lim = p.structure.limit_load_factor
        vs_clean = math.sqrt(2 * w0 * G0 / (RHO0 * model.S * max(1.1, model.clmax_land - 0.3)))
        v_a = vs_clean * math.sqrt(n_lim)  # manoeuvring speed: full controls allowed up to here
        roll = model.roll_rate_deg_s(v_a)
        tw_static = model.n_eng * p.engines.thrust_kn * 1000 / (w0 * G0)
        cons.append(Constraint("Roll rate at manoeuvring speed", roll, lower=240.0, unit="deg/s"))
        cons.append(Constraint("Static thrust / weight", tw_static, lower=0.45))
        cons.append(Constraint("Symmetric aerofoil (inverted flight)", p.wing.camber, upper=0.005))
        notes.append(f"Aerobatic: ±{n_lim:.0f} g, roll rate {roll:.0f}°/s at {v_a * 1.944:.0f} kt "
                     f"(Extra 330: ±10 g, ~400°/s); static thrust/weight {tw_static:.2f}.")
        extra_metrics = {"roll_rate_deg_s": roll, "manoeuvring_speed_ms": v_a, "static_tw": tw_static}
    else:
        extra_metrics = {}
    cons.append(Constraint("G rating", p.structure.limit_load_factor, lower=getattr(m, "g_limit", 2.5), unit="g"))
    extra_metrics["wing_failure_g"] = (p.structure.limit_load_factor * p.structure.safety_factor
                                       * p.structure.wing_strength)
    if n > 1:
        cons.append(Constraint("OEI climb gradient", oei_gradient, lower=oei_required))
    else:
        notes.append("Single engine: one-engine-inoperative climb does not apply (light-aircraft rules).")
    if arr.layout == "blended_wing_body":
        cons.append(Constraint("Centre-body cabin floor area", arr.centrebody_floor_area(),
                               lower=m.cabin_floor_area_m2, unit="m²"))
    else:
        cons.append(Constraint("Cabin length", cabin, lower=m.cabin_length_m, unit="m"))
    # ---- tail sizing and proportions (layout dependent)
    if aft:
        cons.append(Constraint("Horizontal tail volume", vh, 0.5, 1.3))
        cons.append(Constraint("Tail area / wing area", sum(ps.area * ps.horizontal_fraction for ps in aft)
                               / model.S, upper=0.40))
    if arr.has_canard:
        cons.append(Constraint("Canard area / wing area", arr.canard.area / model.S, upper=0.35))
    if arr.yaw_surfaces():
        cons.append(Constraint("Vertical tail volume", vv, 0.02 if arr.is_tailless or arr.has_canard else 0.035,
                               0.12))
        fus_fins = [ps for ps in arr.yaw_surfaces() if ps.attach_to == "fuselage"]
        if fus_fins:
            height = max(ps.surface.semi_span * (math.sin(math.radians(ps.surface.dihedral_deg))
                                                 if ps.role == "v_tail" else 1.0) for ps in fus_fins)
            cons.append(Constraint("Fin height / fuselage length", height / fus.length, upper=0.25))
    else:
        notes.append("No vertical surfaces: yaw stability and control rely on split drag rudders and "
                     "active (fly-by-wire) control, as on the B-2.")
    if arr.is_tailless:
        cons.append(Constraint("Tailless trim: outer-wing sweep", p.wing.sweep_le_deg, lower=20.0, unit="deg"))
        cons.append(Constraint("Tailless trim: tip washout", -p.wing.twist_tip_deg, lower=2.0, unit="deg"))
        notes.append("Tailless pitch trim modelled through sweep, washout and forward fuel; reflex camber is "
                     "not modelled.")
    if model.prop == "turboprop":
        cons.append(Constraint("Propeller-fuselage clearance", arr.prop_fuselage_clearance(), lower=0.3, unit="m"))

    # ------------------------------------------------ structure (sized from the same geometry)
    struct = model.struct.analyse(w0, fuel_total, groups["wing"], model.engine_mass_each(),
                                  model.fuselage_load_args(w0, groups, x_cg), model.V)
    from ..geometry.builder import TAIL_SPARS
    hinge_clear = min(cs.hinge - (p.wing.rear_spar if cs.surface == "wing" else TAIL_SPARS[1])
                      for cs in arr.control_surfaces)
    cons += [
        Constraint("Wing box max required gauge", struct.summary["max_required_gauge_mm"], upper=40.0, unit="mm"),
        Constraint("Fuselage frame stiffness (Shanley)", struct.summary["max_shanley_util"], upper=1.0),
        # low-aspect-ratio deltas act like plates (chordwise load paths) - the spanwise beam overpredicts flex
        Constraint("Wing tip deflection at 1 g", struct.summary["tip_deflection_1g_frac"],
                   upper=0.10 if model.AR >= 3 else 0.20, unit="of semi-span"),
        Constraint("Control-surface hinge clearance", hinge_clear, lower=0.03, unit="c"),
    ]

    objective = w0 if m.objective == "min_mtow" else trip_fuel * 8.0
    from .mission import REQUIREMENT_OF, ignored_requirements
    ignored = ignored_requirements(m)
    for c in cons:  # requirements the user switched off: reported, but not designed around
        if REQUIREMENT_OF.get(c.name) in ignored:
            c.enforced = False
    if ignored:
        notes.append("Not designed around: " + ", ".join(sorted(ignored)) + ".")
    penalty = sum(c.violation for c in cons)
    score = objective * (1 + 4 * penalty)
    metrics = {
        "mtow_kg": w0, "empty_kg": empty, "fuel_kg": fuel_total, "trip_fuel_kg": trip_fuel,
        "empty_fraction": empty / w0, "fuel_fraction": fuel_total / w0,
        "fuel_per_pax_100km_kg": trip_fuel / m.passengers / (m.range_km / 100),
        "wing_area_m2": model.S, "aspect_ratio": model.AR, "mac_m": model.mac,
        "wing_loading_kg_m2": w0 / model.S, "thrust_to_weight": tw,
        "cd0": model.CD0, "oswald_e": model.e, "cruise_cl": cl, "cruise_ld": ld, "ld_max": model.LDmax,
        "mdd": mdd, "cruise_speed_ms": model.V, "landing_weight_fraction": land_frac, "approach_speed_ms": v_app, "takeoff_field_m": tofl,
        "x_cg_m": x_cg, "x_np_m": x_np, "static_margin": static_margin, **extra_metrics,
        "htail_volume": vh, "vtail_volume": vv, "fuel_capacity_kg": fuel_capacity,
        "clmax_landing": model.clmax_land,
        "flyover_noise_db": flyover, "cabin_noise_dba": cabin, "insulation_needed_kg_m2": insulation_needed,
        "boom_overpressure_psf": boom_psf, "boom_pldb": boom_level, "overland_mach": m_over,
        "wave_drag_area_m2": model.dq_wave_m1, "bypass_ratio": model.bpr,
        "trip_time_h": (m.range_km * 1000 * (1 - (m.overland_fraction if model.supersonic else 0)) / model.V
                        + m.range_km * 1000 * (m.overland_fraction if model.supersonic else 0)
                        / (m_over * model.atm.speed_of_sound)) / 3600.0, "flapped_area_fraction": model.flapped_fraction,
        "wing_mass_structural_kg": struct.masses["wing_total"],
        "fuselage_mass_structural_kg": struct.masses["fuselage_total"],
        "wing_mass_statistical_kg": wt.wing_weight(w0, model.nz_ult, model.S, model.AR, model.tc_mac, p.wing.taper_ratio,
                                                   model.sweep_qc, model.wing_control_area),
        "fuselage_mass_statistical_kg": wt.fuselage_weight(w0, model.nz_ult, fus.length, fus.diameter, fus.wetted_area(),
                                                           model.b, p.wing.taper_ratio, model.sweep_qc,
                                                           p.configuration.wing_position == "high"),
        "tip_deflection_1g_m": struct.summary["tip_deflection_1g_m"],
    }
    return Evaluation(True, float(score), {k: float(v) for k, v in metrics.items()}, cons,
                      {k: float(v) for k, v in masses.items()},
                      notes=notes, structure=struct, message=
                      "feasible" if all(c.satisfied for c in cons) else
                      "violates: " + ", ".join(c.name for c in cons if not c.satisfied))
