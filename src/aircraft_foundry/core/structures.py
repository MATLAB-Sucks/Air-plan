"""Conceptual structural sizing and stress analysis.

Loads, member sizes and stresses are derived from the same Arrangement the
geometry is built from, and results are keyed to component ids (ribs, spars,
frames, longerons, fittings, control surfaces) so they can be inspected and
colour-mapped in the viewport.

Methods (classical conceptual/preliminary design):

* Wing spanwise lift: Schrenk's approximation (mean of elliptic and planform
  distributions), with inertia relief from wing structure, fuel and engines.
  Two cases, MTOW with fuel and zero-fuel weight, at the limit manoeuvre
  load factor; envelope taken; ultimate = limit x safety factor.
* Wing box: two-cell-free idealised box between the spars.  Bending about the
  swept elastic axis.  Compression cover sized with Farrar's optimum
  stringer-panel efficiency (sigma = F sqrt(N E / L), F = 0.81, L = rib pitch
  along the stringers), tension cover with a damage-tolerance knockdown,
  -1 g reversal checked.  Spar webs sized for bending shear plus torsion shear
  flow and for shear buckling between rib stiffeners.
* Fuselage: pressurisation hoop stress (fatigue at 1.0 dP and ultimate at
  2.0 dP), manoeuvre bending of the fuselage cantilevered from the wing,
  stringer-stiffened shell (Farrar), frames checked with Shanley's criterion.
* Thicknesses are rounded up to standard gauges; margins of safety are
  computed at the gauged thickness.

Results are for trade studies and early sizing, not for certification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..geometry.arrangement import Arrangement, engine_dimensions
from ..numerics.atmosphere import G0, RHO0, isa
from ..numerics.materials import GAUGES_MM, MAX_GAUGE_MM, Material, material
from .parameters import AircraftParameters

FARRAR_F = 0.81  # Z-stringer panel efficiency
SHANLEY_CF = 1.0 / 16000.0
NON_OPTIMUM_WING = 1.45  # splices, fasteners, access holes, tank sealing, gear/pylon support ribs
NON_OPTIMUM_FUSELAGE = 1.40  # doors, windows, cut-out reinforcement, joints
AREAL_KG_M2 = {"leading_edge_slats": 10.0, "leading_edge": 6.5, "fixed_trailing_edge": 6.0, "flap": 22.0,
               "aileron": 15.0, "spoilers": 9.0}
FLOOR_KG_M2 = 9.0  # passenger floor incl. beams and seat tracks
CARGO_FLOOR_KG_M2 = 5.0
DOOR_KG = 110.0  # per passenger/service/cargo door incl. surround
WINDOW_KG = 3.0  # per window incl. frame
BULKHEAD_KG_M2 = 12.0
_GAUGES = np.array(GAUGES_MM) / 1000.0


def gauge(t: np.ndarray) -> np.ndarray:
    """Round required thickness (m) up to the next standard gauge (m)."""
    idx = np.searchsorted(_GAUGES, np.asarray(t) - 1e-12)
    return _GAUGES[np.clip(idx, 0, len(_GAUGES) - 1)]


def farrar_allowable(n_x: np.ndarray, mat: Material, panel_length: float) -> np.ndarray:
    """Allowable compression stress of an optimum stringer-stiffened panel."""
    return np.minimum(mat.fcy, FARRAR_F * np.sqrt(np.maximum(n_x, 1.0) * mat.E / panel_length))


def _cumtrapz_from_tip(f: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Integral of f from x[i] to x[-1] for every i."""
    seg = 0.5 * (f[1:] + f[:-1]) * np.diff(x)
    out = np.zeros_like(f)
    out[:-1] = np.cumsum(seg[::-1])[::-1]
    return out


@dataclass
class WingStations:
    y: np.ndarray
    eta: np.ndarray
    chord: np.ndarray
    h: np.ndarray
    width: np.ndarray
    V: np.ndarray  # ultimate shear, N
    M: np.ndarray  # ultimate bending about the swept axis, N m
    T: np.ndarray  # ultimate torsion, N m
    t_upper: np.ndarray  # required (m)
    t_lower: np.ndarray
    t_web: np.ndarray
    g_upper: np.ndarray  # gauged (m)
    g_lower: np.ndarray
    g_web: np.ndarray
    ms_upper: np.ndarray
    ms_lower: np.ndarray
    ms_web: np.ndarray

    @property
    def utilisation(self) -> np.ndarray:
        return 1.0 / (1.0 + np.minimum.reduce([self.ms_upper, self.ms_lower, self.ms_web]))

    def at(self, eta: float) -> dict:
        def f(a):
            return float(np.interp(eta, self.eta, a))
        return {
            "station_y_m": f(self.y), "shear_ult_kN": f(self.V) / 1e3, "bending_ult_kNm": f(self.M) / 1e3,
            "torsion_ult_kNm": f(self.T) / 1e3, "box_height_m": f(self.h), "box_width_m": f(self.width),
            "upper_cover_req_mm": f(self.t_upper) * 1e3, "upper_cover_gauge_mm": f(self.g_upper) * 1e3,
            "lower_cover_req_mm": f(self.t_lower) * 1e3, "lower_cover_gauge_mm": f(self.g_lower) * 1e3,
            "spar_web_req_mm": f(self.t_web) * 1e3, "spar_web_gauge_mm": f(self.g_web) * 1e3,
            "MS_upper_cover": f(self.ms_upper), "MS_lower_cover": f(self.ms_lower), "MS_spar_webs": f(self.ms_web),
        }


@dataclass
class FuselageStations:
    x: np.ndarray
    radius: np.ndarray
    M: np.ndarray  # ultimate bending, N m
    t_skin: np.ndarray  # gauged skin (m)
    t_stringer: np.ndarray  # smeared stringer thickness (m)
    sigma_bending: np.ndarray  # Pa at ultimate
    allow_bending: np.ndarray
    sigma_hoop: np.ndarray  # Pa at 1.0 dP
    shanley_util: np.ndarray

    def at(self, x: float) -> dict:
        def f(a):
            return float(np.interp(x, self.x, a))
        return {"station_x_m": x, "bending_ult_kNm": f(self.M) / 1e3, "skin_gauge_mm": f(self.t_skin) * 1e3,
                "stringer_smeared_mm": f(self.t_stringer) * 1e3, "bending_stress_ult_MPa": f(self.sigma_bending) / 1e6,
                "bending_allowable_MPa": f(self.allow_bending) / 1e6, "hoop_stress_1dP_MPa": f(self.sigma_hoop) / 1e6,
                "frame_stiffness_util_shanley": f(self.shanley_util)}


@dataclass
class StructuralResult:
    wing: WingStations
    fuselage: FuselageStations
    masses: dict[str, float]
    summary: dict[str, float]
    member_results: dict[str, dict] = field(default_factory=dict)
    utilisation: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def wing_mass(self) -> float:
        return self.masses["wing_total"]

    @property
    def fuselage_mass(self) -> float:
        return self.masses["fuselage_total"]


class StructuralModel:
    """Geometry-dependent part, computed once per parameter set."""

    def __init__(self, params: AircraftParameters, cruise_altitude_m: float, cruise_mach: float,
                 arr: Arrangement | None = None, n_stations: int = 61, passengers: int = 100):
        self.p = params
        self.passengers = passengers
        self.arr = arr = arr or Arrangement(params)
        sp = params.structure
        self.n_lim, self.sf = sp.limit_load_factor, sp.safety_factor
        self.tmin = sp.min_gauge_mm / 1000.0
        self.wmat, self.fmat = material(sp.wing_material), material(sp.fuselage_material)
        self.cruise_alt, self.mach = cruise_altitude_m, cruise_mach
        # ---------------- wing box geometry
        w, pw = arr.wing, params.wing
        s = w.semi_span
        self.y = np.linspace(0.0, s, n_stations)
        self.eta = self.y / s
        self.c = w.chord(self.eta)
        hf, hr = [], []
        for e in self.eta:
            af = w.airfoil(float(e))
            zu, zl = af.z_upper_lower(pw.front_spar)
            hf.append(zu - zl)
            zu, zl = af.z_upper_lower(pw.rear_spar)
            hr.append(zu - zl)
        self.h = 0.5 * (np.array(hf) + np.array(hr)) * self.c * 0.95  # cover centroid separation
        self.cos_ea = math.cos(w.sweep_at(0.5 * (pw.front_spar + pw.rear_spar)))
        self.width = (pw.rear_spar - pw.front_spar) * self.c * self.cos_ea
        self.area_box = self.h * self.width
        self.x_ea_frac = 0.5 * (pw.front_spar + pw.rear_spar)
        self.rib_pitch = pw.rib_pitch / self.cos_ea  # panel length along the stringers
        self.c_int = np.trapezoid(self.c, self.y)
        mask = self.eta <= 0.85
        self.fuel_shape = np.where(mask, self.c**2, 0.0)
        self.fuel_int = np.trapezoid(self.fuel_shape, self.y)
        self.n_ribs = max(2, int(round(s / pw.rib_pitch)) + 1)
        # engines on the wing (one side)
        dims = engine_dimensions(params.engines.thrust_kn, params.configuration.propulsion,
                                 params.engines.bypass_ratio)
        self.engines = [e for e in arr.engines if e.mount == "wing" and e.side > 0]
        self.engine_len = dims["length"]
        # ---------------- fuselage geometry
        fus = arr.fuselage
        self.x = np.linspace(0.0, fus.length, n_stations)
        self.r = np.sqrt(fus.half_width(self.x) * fus.half_height(self.x))
        self.perim = 2 * np.pi * self.r
        p_cab = isa(sp.cabin_altitude_m).pressure
        self.dp = max(0.0, p_cab - isa(cruise_altitude_m).pressure)

    # ------------------------------------------------------------------ wing
    def _wing_loads(self, weight_kg: float, fuel_kg: float, wing_mass: float, engine_mass: float):
        n = self.n_lim
        y, c, s = self.y, self.c, self.arr.wing.semi_span
        L_half = n * weight_kg * G0 / 2
        l_ell = 4 * L_half / (math.pi * s) * np.sqrt(np.clip(1 - self.eta**2, 0, 1))
        l_pl = L_half * c / self.c_int
        lift = 0.5 * (l_ell + l_pl)
        inertia = n * G0 * ((wing_mass / 2) * c / self.c_int + (fuel_kg / 2) * self.fuel_shape / max(self.fuel_int, 1e-9))
        q = lift - inertia
        V = _cumtrapz_from_tip(q, y)
        torque_aero = lift * (self.x_ea_frac - 0.25) * c
        T = np.abs(_cumtrapz_from_tip(torque_aero, y))
        for e in self.engines:
            ye = abs(e.inlet[1])
            inboard = y < ye
            V = V - np.where(inboard, n * G0 * engine_mass, 0.0)
            x_ea = float(self.arr.wing_point(self.arr.wing.point(e.eta, self.x_ea_frac))[0])
            T = T + np.where(inboard, n * G0 * engine_mass * abs(x_ea - e.centre[0]), 0.0)
        M = _cumtrapz_from_tip(V, y)
        return np.abs(V), np.abs(M) / self.cos_ea, T

    def wing(self, w0: float, fuel: float, wing_mass: float, engine_mass: float) -> WingStations:
        sf, mat, tmin = self.sf, self.wmat, self.tmin
        V1, M1, T1 = self._wing_loads(w0, fuel, wing_mass, engine_mass)
        V2, M2, T2 = self._wing_loads(w0 - fuel, 0.0, wing_mass, engine_mass)
        V, M, T = sf * np.maximum(V1, V2), sf * np.maximum(M1, M2), sf * np.maximum(T1, T2)
        h, b = np.maximum(self.h, 1e-3), np.maximum(self.width, 1e-3)
        n_x = M / h / b  # running load in the covers, N/m
        allow_c = farrar_allowable(n_x, mat, self.rib_pitch)
        t_u = np.maximum(n_x / allow_c, tmin)
        allow_t = mat.ftu * mat.tension_knockdown
        n_neg = n_x / self.n_lim  # -1 g reversal
        allow_neg = farrar_allowable(n_neg, mat, self.rib_pitch)
        t_l = np.maximum.reduce([n_x / allow_t, n_neg / allow_neg, np.full_like(n_x, tmin)])
        q = V / (2 * h) + T / (2 * h * b)
        pitch = np.minimum(h, self.rib_pitch)
        t_w = np.maximum.reduce([q / mat.fsu, (q * pitch**2 / (5.35 * mat.E)) ** (1 / 3), np.full_like(q, tmin)])
        g_u, g_l, g_w = gauge(t_u), gauge(t_l), gauge(t_w)
        tiny = 1e-9  # unloaded tip: avoid 0/0, margins become very large
        ms_u = allow_c * g_u / np.maximum(n_x, tiny) - 1
        ms_l = np.minimum(allow_t * g_l / np.maximum(n_x, tiny), allow_neg * g_l / np.maximum(n_neg, tiny)) - 1
        tau_allow = np.minimum(mat.fsu, 5.35 * mat.E * (g_w / pitch) ** 2)
        ms_w = tau_allow * g_w / np.maximum(q, tiny) - 1
        return WingStations(self.y, self.eta, self.c, self.h, self.width, V, M, T, t_u, t_l, t_w, g_u, g_l, g_w,
                            ms_u, ms_l, ms_w)

    def wing_mass(self, ws: WingStations) -> dict[str, float]:
        rho = self.wmat.density
        ds = self.y / self.cos_ea
        covers = 2 * rho * np.trapezoid((ws.g_upper + ws.g_lower) * ws.width, ds)
        webs = 2 * rho * np.trapezoid(2 * ws.g_web * ws.h, ds)
        t_rib = max(self.tmin, 0.0016)
        ribs = 2 * self.n_ribs * rho * t_rib * 1.6 * float(np.mean(0.85 * ws.h * ws.width))
        box = NON_OPTIMUM_WING * (covers + webs + ribs)
        arr, pw = self.arr, self.p.wing
        S = arr.wing.area
        slats = self.p.configuration.propulsion == "turbofan"
        le = pw.front_spar * S * AREAL_KG_M2["leading_edge_slats" if slats else "leading_edge"]
        flap_a, ail_a = arr.control_area("flap"), arr.control_area("aileron")
        te_area = (1 - pw.rear_spar) * S - flap_a - ail_a
        te = max(te_area, 0) * AREAL_KG_M2["fixed_trailing_edge"] + (0.08 * S * AREAL_KG_M2["spoilers"] if slats else 0)
        cs = flap_a * AREAL_KG_M2["flap"] + ail_a * AREAL_KG_M2["aileron"]
        return {"wing_covers": covers, "wing_spar_webs": webs, "wing_ribs": ribs,
                "wing_box_non_optimum": box - covers - webs - ribs, "wing_leading_edge": le,
                "wing_trailing_edge": te, "wing_control_surfaces": cs, "wing_total": box + le + te + cs}

    def tip_deflection_1g(self, ws: WingStations) -> float:
        E = self.wmat.E
        I = 2 * ws.width * 0.5 * (ws.g_upper + ws.g_lower) * (ws.h / 2) ** 2
        kappa = (ws.M / (self.sf * self.n_lim)) / (E * np.maximum(I, 1e-9))
        s = self.y / self.cos_ea
        slope = np.concatenate([[0], np.cumsum(0.5 * (kappa[1:] + kappa[:-1]) * np.diff(s))])
        return float(np.trapezoid(slope, s))

    # ------------------------------------------------------------------ fuselage
    def fuselage(self, w0: float, fuselage_mass: float, distributed_kg: float, crew_kg: float,
                 tail_kg: float, rear_engine_kg: float, nose_gear_kg: float, x_cg: float) -> FuselageStations:
        arr, fus, mat, sf, n = self.arr, self.arr.fuselage, self.fmat, self.sf, self.n_lim
        x, r = self.x, self.r
        cabin0, cabin1 = 0.55 * fus.x_nose_end, fus.x_tail_start + 0.35 * fus.tail_fraction * fus.length
        dist = fuselage_mass * self.perim / np.trapezoid(self.perim, x)
        in_cabin = (x >= cabin0) & (x <= cabin1)
        dist = dist + np.where(in_cabin, distributed_kg / (cabin1 - cabin0), 0.0)  # kg/m
        points = [(0.08 * fus.length, crew_kg), (arr.x_ac_htail, tail_kg), (arr.gear[0].axle[0], nose_gear_kg)]
        if rear_engine_kg:
            points.append((arr.engines[0].centre[0], rear_engine_kg))
        # horizontal-tail balancing load at the limit load factor
        lh = max(arr.x_ac_htail - arr.x_ac_wing, 1.0)
        tail_load_kg = abs(w0 * (x_cg - arr.x_ac_wing) / lh)
        points.append((arr.x_ac_htail, tail_load_kg))
        xs = arr.x_ac_wing  # fuselage supported by the wing box
        w = n * G0 * dist  # N/m
        M = np.zeros_like(x)
        fwd, aft = x <= xs, x > xs
        for i, xi in enumerate(x):
            if xi <= xs:
                sel = x <= xi
                m = np.trapezoid(w[sel] * (xi - x[sel]), x[sel]) if sel.sum() > 1 else 0.0
                m += sum(n * G0 * mk * (xi - xk) for xk, mk in points if xk <= xi)
            else:
                sel = x >= xi
                m = np.trapezoid(w[sel] * (x[sel] - xi), x[sel]) if sel.sum() > 1 else 0.0
                m += sum(n * G0 * mk * (xk - xi) for xk, mk in points if xk >= xi)
            M[i] = m
        del fwd, aft
        M = sf * M
        dp = self.dp
        t_hoop = np.maximum(dp * r / mat.fatigue_hoop, 2.0 * dp * r / (mat.ftu * mat.tension_knockdown))
        t_skin = gauge(np.maximum(t_hoop, self.tmin))
        frame_pitch = self.p.fuselage.frame_pitch
        rr = np.maximum(r, 0.05)
        n_x = M / (np.pi * rr**2)
        allow = farrar_allowable(n_x, mat, frame_pitch)
        t_eq = n_x / allow
        t_str = np.maximum(t_eq - 0.5 * t_skin, 0.0)
        sigma_b = n_x / np.maximum(0.5 * t_skin + t_str, 1e-6)
        sigma_h = dp * rr / t_skin
        d, tw, af = self.p.fuselage.frame_depth, 0.002, 0.03 * 0.002
        i_frame = tw * d**3 / 12 + 2 * af * (d / 2) ** 2
        ei_req = SHANLEY_CF * M * (2 * rr) ** 2 / frame_pitch
        shanley = ei_req / (mat.E * i_frame)
        return FuselageStations(x, r, M, t_skin, t_str, sigma_b, allow, sigma_h, shanley)

    def fuselage_mass(self, fs: FuselageStations) -> dict[str, float]:
        rho, fus = self.fmat.density, self.arr.fuselage
        skin = rho * np.trapezoid(fs.t_skin * self.perim, fs.x)
        stringers = rho * np.trapezoid(fs.t_stringer * self.perim, fs.x)
        pf = self.p.fuselage
        n_frames = len(np.arange(0.06 * fus.length, 0.94 * fus.length, pf.frame_pitch))
        a_frame = pf.frame_depth * 0.002 + 2 * 0.03 * 0.002
        frames = rho * a_frame * n_frames * float(np.mean(self.perim[(fs.x > 0.1 * fus.length) & (fs.x < 0.9 * fus.length)]))
        floor = FLOOR_KG_M2 * fus.cabin_length * 0.8 * fus.diameter
        cargo_floor = CARGO_FLOOR_KG_M2 * fus.cabin_length * 0.6 * fus.diameter if fus.diameter > 2.5 else 0.0
        bulkheads = (2 * BULKHEAD_KG_M2 * math.pi * fus.radius**2) if self.dp > 0 else 0.0
        doors = DOOR_KG * (2 + 2 * math.ceil(self.passengers / 80) + (2 if fus.diameter > 2.5 else 0))
        windows = WINDOW_KG * 2 * fus.cabin_length / 0.53
        shell = NON_OPTIMUM_FUSELAGE * (skin + stringers + frames)
        return {"fuselage_skin": skin, "fuselage_stringers": stringers, "fuselage_frames": frames,
                "fuselage_non_optimum": shell - skin - stringers - frames, "fuselage_floors": floor + cargo_floor,
                "fuselage_pressure_bulkheads": bulkheads, "fuselage_doors_windows": doors + windows,
                "fuselage_total": shell + floor + cargo_floor + bulkheads + doors + windows}

    # ------------------------------------------------------------------ public
    def masses(self, w0: float, fuel: float, wing_mass_guess: float, engine_mass: float, fus_args: dict) -> dict:
        ws = self.wing(w0, fuel, wing_mass_guess, engine_mass)
        fs = self.fuselage(w0=w0, **fus_args)
        return {**self.wing_mass(ws), **self.fuselage_mass(fs)}

    def analyse(self, w0: float, fuel: float, wing_mass: float, engine_mass: float, fus_args: dict,
                mission_speed: float) -> StructuralResult:
        ws = self.wing(w0, fuel, wing_mass, engine_mass)
        fs = self.fuselage(w0=w0, **fus_args)
        masses = {**self.wing_mass(ws), **self.fuselage_mass(fs)}
        res = StructuralResult(ws, fs, masses, {})
        self._member_results(res, w0, engine_mass, mission_speed)
        semi = self.arr.wing.semi_span
        delta = self.tip_deflection_1g(ws)
        res.summary = {
            "root_bending_ult_MNm": float(ws.M[0]) / 1e6, "root_shear_ult_kN": float(ws.V[0]) / 1e3,
            "root_upper_cover_mm": float(ws.g_upper[0]) * 1e3, "root_lower_cover_mm": float(ws.g_lower[0]) * 1e3,
            "root_spar_web_mm": float(ws.g_web[0]) * 1e3, "min_MS_wing": float(np.min(1 / ws.utilisation - 1)),
            "tip_deflection_1g_m": delta, "tip_deflection_1g_frac": delta / semi,
            "max_required_gauge_mm": float(max(ws.t_upper.max(), ws.t_lower.max(), ws.t_web.max()) * 1e3),
            "max_fuselage_bending_ult_MNm": float(fs.M.max()) / 1e6,
            "cabin_dp_kPa": self.dp / 1e3, "max_hoop_stress_MPa": float(fs.sigma_hoop.max()) / 1e6,
            "max_shanley_util": float(fs.shanley_util.max()),
            "min_MS_fuselage_bending": float(np.min(fs.allow_bending / np.maximum(fs.sigma_bending, 1) - 1)),
        }
        if res.summary["max_required_gauge_mm"] > MAX_GAUGE_MM:
            res.warnings.append("wing box needs plate thicker than the largest standard gauge")
        if res.summary["max_shanley_util"] > 1:
            res.warnings.append("fuselage frames too shallow for general-instability (Shanley) criterion")
        return res

    # ------------------------------------------------------------------ per-component results
    def _member_results(self, res: StructuralResult, w0: float, engine_mass: float, speed: float) -> None:
        from ..geometry.builder import TAIL_SPARS, frame_stations, hinge_count, rib_stations
        arr, p, ws, fs = self.arr, self.p, res.wing, res.fuselage
        out, util = res.member_results, res.utilisation
        wing = arr.wing
        for side in ("right", "left"):
            for i, e in enumerate(rib_stations(wing, p.wing.rib_pitch, p.wing.rib_thickness)):
                cid = f"wing.{side}.rib.{i:02d}"
                out[cid] = ws.at(float(e))
                util[cid] = float(np.interp(e, ws.eta, ws.utilisation))
            for spar in ("front", "rear"):
                cid = f"wing.{side}.spar.{spar}"
                k = int(np.argmin(ws.ms_web))
                out[cid] = {"max_shear_ult_kN": float(ws.V.max()) / 1e3, "web_gauge_root_mm": float(ws.g_web[0]) * 1e3,
                            "web_gauge_tip_mm": float(ws.g_web[-1]) * 1e3, "min_MS_web": float(ws.ms_web[k]),
                            "critical_station_y_m": float(ws.y[k])}
                util[cid] = float(1 / (1 + ws.ms_web.min()))
            util[f"wing.{side}"] = float(ws.utilisation.max())
            out[f"wing.{side}"] = {"root_bending_ult_kNm": float(ws.M[0]) / 1e3,
                                   "min_MS_cover": float(min(ws.ms_upper.min(), ws.ms_lower.min())),
                                   "material": p.structure.wing_material}
            # root fittings carry the vertical shear (bending goes through the carry-through box)
            for spar in ("front", "rear"):
                cid = f"attach.wing.{side}.{spar}"
                out[cid] = {"design_shear_ult_kN": float(ws.V[0]) / 2e3, "load_case": "limit manoeuvre x SF"}
        fus = arr.fuselage
        for i, x in enumerate(frame_stations(fus, p.fuselage.frame_pitch)):
            cid = f"fuselage.frame.{i:02d}"
            out[cid] = fs.at(float(x))
            u = max(float(np.interp(x, fs.x, fs.shanley_util)),
                    float(np.interp(x, fs.x, fs.sigma_hoop / self.fmat.fatigue_hoop)))
            util[cid] = u
        bend_util = fs.sigma_bending / fs.allow_bending
        k = int(np.argmax(bend_util))
        for j in range(p.fuselage.longerons):
            cid = f"fuselage.longeron.{j:02d}"
            area = float(fs.t_stringer[k] * 2 * np.pi * fs.radius[k] / p.fuselage.longerons)
            out[cid] = {"critical_station_x_m": float(fs.x[k]), "stringer_area_per_longeron_cm2": area * 1e4,
                        "axial_stress_ult_MPa": float(fs.sigma_bending[k]) / 1e6,
                        "allowable_MPa": float(fs.allow_bending[k]) / 1e6}
            util[cid] = float(bend_util[k])
        util["fuselage"] = float(max(bend_util.max(), (fs.sigma_hoop / self.fmat.fatigue_hoop).max()))
        out["fuselage"] = {"cabin_dp_kPa": self.dp / 1e3, "material": p.structure.fuselage_material,
                           "max_bending_ult_kNm": float(fs.M.max()) / 1e3}
        # engine mounts, gear trunnions
        n_ult = self.n_lim * self.sf
        for e in arr.engines:
            out[f"attach.engine.{e.index}"] = {"vertical_ult_kN": n_ult * G0 * engine_mass / 1e3,
                                               "thrust_ult_kN": self.sf * p.engines.thrust_kn}
        w_land = 0.85 * w0
        for leg in arr.gear:
            frac = 0.45 if leg.name.startswith("main") else 0.12
            out[f"attach.gear.{leg.name}"] = {"landing_vertical_ult_kN": frac * w_land * G0 * 2.0 * self.sf / 1e3,
                                              "note": "2 g landing reaction x SF"}
        # control-surface hinge moments at dive speed (flaps at flap-limit speed)
        atm = isa(self.cruise_alt)
        q_dive = 0.5 * atm.density * (1.25 * speed) ** 2
        vs = math.sqrt(2 * w0 * G0 / (RHO0 * wing.area * 2.4))
        q_flap = 0.5 * RHO0 * (1.8 * vs) ** 2
        dmax = {"aileron": 25, "flap": 40, "elevator": 25, "rudder": 30, "elevon_inner": 30, "elevon_outer": 30,
                "ruddervator": 30, "drag_rudder": 45}
        for cs in arr.control_surfaces:
            surf = arr.surface(cs.surface)
            area = surf.strip_area(cs.eta0, cs.eta1, cs.chord_fraction)
            c_cs = cs.chord_fraction * float(surf.chord(0.5 * (cs.eta0 + cs.eta1)))
            q = q_flap if cs.kind == "flap" else q_dive
            delta = math.radians(dmax[cs.kind])
            hinge = 0.55 * delta * q * area * c_cs
            n_h = hinge_count(surf, cs)
            normal = 1.5 * delta * q * area * self.sf
            out[cs.id] = {"design_q_kPa": q / 1e3, "max_deflection_deg": dmax[cs.kind],
                          "hinge_moment_kNm": hinge / 1e3, "normal_load_ult_kN": normal / 1e3,
                          "load_per_hinge_ult_kN": normal / n_h / 1e3,
                          "hinge_clearance_to_rear_spar_c": cs.hinge - (p.wing.rear_spar if cs.surface == "wing"
                                                                         else TAIL_SPARS[1])}
            for k in range(n_h):
                out[f"attach.hinge.{cs.id}.{k}"] = {"shear_ult_kN": normal / n_h / 1e3}
