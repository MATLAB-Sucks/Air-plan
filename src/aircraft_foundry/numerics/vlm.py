"""Vortex-lattice method (VLM) for the wind tunnel.

Every lifting surface of the design (wings, tails, canards, fins, V-tails) is
split into chordwise x spanwise panels on its camber surface, using the same
planform mapping as the geometry (sweep, dihedral, twist, camber, cranks).
Each panel carries a horseshoe vortex: a bound segment on the panel's
quarter-chord line and two trailing legs streaming aft to "infinity".  The
flow-tangency condition at each panel's three-quarter-chord control point
gives a linear system for the circulations.  Control-surface deflections
tilt the boundary-condition normals of the panels aft of the hinge.

Outputs: lift / induced drag / side force / pitching moment coefficients, the
lifting pressure difference on every panel, spanwise loading, and the
velocity anywhere in space (freestream + all horseshoes + a slender-body
source line for the fuselage) for streamline tracing.

Axes are the design frame (x aft, y starboard, z up).  The freestream blows
aft (+x), tilted by angle of attack alpha (flow from below) and sideslip
beta (flow from the right).  Linear, inviscid, attached flow: no stall,
no thickness effects on the lifting surfaces, fixed planar wake.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..geometry.arrangement import Arrangement

CORE = 0.02  # vortex core radius as a fraction of the reference chord (removes singularities)


@dataclass
class Panels:
    a: np.ndarray  # (N,3) bound vortex start (smaller y, or lower z for fins)
    b: np.ndarray  # (N,3) bound vortex end
    cp: np.ndarray  # (N,3) control points
    normal: np.ndarray  # (N,3) unit normals (before deflection)
    chord_dir: np.ndarray  # (N,3) unit vectors aft along the local chord
    area: np.ndarray  # (N,)
    centroid: np.ndarray  # (N,3)
    surface: list  # surface key per panel
    side: np.ndarray  # +1 / -1
    eta: np.ndarray  # spanwise station of the panel
    xc: np.ndarray  # chord fraction of the panel centre
    corners: np.ndarray  # (N,4,3) for drawing
    strip: np.ndarray  # strip index (for spanwise loading)


def build_panels(arr: Arrangement, n_chord: int = 6, n_span: int = 14) -> Panels:
    A, B, CP, NRM, CD, AREA, CEN, SURF, SIDE, ETA, XC, CORN, STRIP = ([] for _ in range(13))
    strip_id = 0
    for ps in arr.surfaces:
        s = ps.surface
        nc = n_chord if ps.role == "wing" else max(3, n_chord - 2)
        ns = n_span if ps.role == "wing" else max(5, n_span // 2)
        etas = 0.5 * (1 - np.cos(np.linspace(0, math.pi, ns + 1)))  # cosine: refine root and tip
        if s.cranked:
            etas = np.union1d(etas, [s.crank_eta])
        xs = np.linspace(0.0, 1.0, nc + 1)
        for side in ps.sides:
            def pt(e, x):
                af = s.airfoil(float(e))
                zc = float(af.camber_line(np.array(x))[0])
                return ps.placement.apply(s.map_section(float(e), np.array([[x, zc]]), side))[0]
            grid = np.array([[pt(e, x) for x in xs] for e in etas])  # (ns+1, nc+1, 3)
            for i in range(len(etas) - 1):
                for j in range(nc):
                    p00, p01 = grid[i, j], grid[i, j + 1]  # inboard LE / TE of the panel
                    p10, p11 = grid[i + 1, j], grid[i + 1, j + 1]  # outboard
                    a = p00 + 0.25 * (p01 - p00)
                    b = p10 + 0.25 * (p11 - p10)
                    c_in, c_out = p00 + 0.75 * (p01 - p00), p10 + 0.75 * (p11 - p10)
                    cp = 0.5 * (c_in + c_out)
                    chord = 0.5 * ((p01 - p00) + (p11 - p10))
                    span = 0.5 * ((p10 - p00) + (p11 - p01))
                    n = np.cross(chord, span)
                    area = float(np.linalg.norm(n))
                    n = n / max(area, 1e-12)
                    if (s.vertical and n[1] < 0) or (not s.vertical and n[2] < 0):
                        n = -n
                    # bound vortex runs towards +y (or +z on a fin) so positive circulation = positive lift
                    key = 2 if s.vertical else 1
                    if b[key] < a[key]:
                        a, b = b, a
                    A.append(a)
                    B.append(b)
                    CP.append(cp)
                    NRM.append(n)
                    CD.append(chord / np.linalg.norm(chord))
                    AREA.append(area)
                    CEN.append(0.25 * (p00 + p01 + p10 + p11))
                    SURF.append(ps.key)
                    SIDE.append(side)
                    ETA.append(0.5 * (etas[i] + etas[i + 1]))
                    XC.append(0.5 * (xs[j] + xs[j + 1]))
                    CORN.append(np.array([p00, p01, p11, p10]))
                    STRIP.append(strip_id + i)
            strip_id += len(etas) - 1
    return Panels(np.array(A), np.array(B), np.array(CP), np.array(NRM), np.array(CD), np.array(AREA),
                  np.array(CEN), SURF, np.array(SIDE), np.array(ETA), np.array(XC), np.array(CORN), np.array(STRIP))


def _segment_velocity(p: np.ndarray, a: np.ndarray, b: np.ndarray, core: float) -> np.ndarray:
    """Velocity at points p (M,3) induced by unit-strength straight vortex segments a->b (N,3): (M,N,3)."""
    r1 = p[:, None, :] - a[None, :, :]
    r2 = p[:, None, :] - b[None, :, :]
    r0 = b - a
    cross = np.cross(r1, r2)
    cross2 = np.einsum("mnk,mnk->mn", cross, cross)
    n1 = np.linalg.norm(r1, axis=2)
    n2 = np.linalg.norm(r2, axis=2)
    l2 = np.einsum("nk,nk->n", r0, r0)[None, :]
    # Rankine-style core: cap the induced velocity near the vortex line
    denom = cross2 + (core**2) * l2
    k = (np.einsum("nk,mnk->mn", r0, r1) / np.maximum(n1, 1e-9)
         - np.einsum("nk,mnk->mn", r0, r2) / np.maximum(n2, 1e-9)) / (4 * math.pi * np.maximum(denom, 1e-12))
    return cross * k[:, :, None]


def horseshoe_velocity(p: np.ndarray, pan: Panels, core: float, far: float) -> np.ndarray:
    """Velocity per unit circulation of every horseshoe at points p: (M,N,3)."""
    far_dir = np.array([1.0, 0.0, 0.0])
    a_far = pan.a + far * far_dir
    b_far = pan.b + far * far_dir
    return (_segment_velocity(p, pan.a, pan.b, core) + _segment_velocity(p, pan.b, b_far, core)
            + _segment_velocity(p, a_far, pan.a, core))


@dataclass
class VLMResult:
    alpha_deg: float
    beta_deg: float
    gamma: np.ndarray
    panels: Panels
    vinf: np.ndarray
    forces: np.ndarray  # (N,3) per panel, newtons at the given q
    CL: float
    CDi: float
    CY: float
    Cm: float
    dCp: np.ndarray  # lifting pressure difference per panel (normal force / (q A))
    span_eff: float
    q: float
    extras: dict = field(default_factory=dict)


class VLMModel:
    """Panels + influence matrix for one geometry; solves any alpha / beta / deflection quickly."""

    def __init__(self, arr: Arrangement, x_cg: float, n_chord: int = 6, n_span: int = 14):
        self.arr = arr
        self.pan = build_panels(arr, n_chord, n_span)
        w = arr.wing
        self.S, self.b, self.c = w.area, w.span, w.mac
        self.x_cg = x_cg
        self.core = CORE * self.c
        x0, x1 = arr.overall_length()
        self.far = 50.0 * max(self.b, x1 - x0)
        V = horseshoe_velocity(self.pan.cp, self.pan, self.core, self.far)  # (N,N,3)
        self._V_cp = V
        mids = 0.5 * (self.pan.a + self.pan.b)
        self._V_mid = horseshoe_velocity(mids, self.pan, self.core, self.far)
        fus = arr.fuselage
        xs = np.linspace(0, fus.length, 41)
        area = np.pi * fus.half_width(xs) * fus.half_height(xs)
        self._src_x = 0.5 * (xs[1:] + xs[:-1])
        self._src_z = fus.centre_z(self._src_x)
        self._src_dA = np.diff(area)  # source strength per unit freestream speed
        # The lattice models lifting surfaces only.  The fuselage's destabilising (Munk) pitching moment
        # is added with the same statistical term the evaluator uses (Raymer), so both agree.
        from . import aerodynamics as aero
        kf = aero.fuselage_pitching_moment_factor(arr.x_ac_wing / fus.length)
        self.cma_fus = kf * fus.diameter ** 2 * fus.length / (self.S * self.c) * math.degrees(1)
        if arr.has_pod:
            self.cma_fus *= 0.5

    def _normals(self, deflections: dict[str, float]) -> np.ndarray:
        """Panel normals tilted by control-surface deflections {control-surface id: degrees, TE down +}."""
        n = self.pan.normal.copy()
        if not deflections:
            return n
        for cs in self.arr.control_surfaces:
            d = deflections.get(cs.id, deflections.get(cs.kind, 0.0))
            if cs.kind == "aileron" or cs.kind == "elevon_outer":
                d = deflections.get("aileron", 0.0) * (-cs.side) + deflections.get(cs.id, 0.0)
                d += deflections.get("elevator", 0.0) if cs.kind == "elevon_outer" else 0.0
            if cs.kind == "elevon_inner":
                d = deflections.get("elevator", 0.0)
            if cs.kind == "ruddervator":
                d = deflections.get("elevator", 0.0) + cs.side * deflections.get("rudder", 0.0)
            if abs(d) < 1e-9:
                continue
            surf_key = cs.surface
            mask = np.array([s == surf_key for s in self.pan.surface]) & (self.pan.side == cs.side) \
                & (self.pan.eta >= cs.eta0) & (self.pan.eta <= cs.eta1) & (self.pan.xc >= cs.hinge)
            dr = math.radians(d)
            n[mask] = n[mask] * math.cos(dr) + self.pan.chord_dir[mask] * math.sin(dr)
        return n / np.linalg.norm(n, axis=1, keepdims=True)

    def freestream(self, alpha_deg: float, beta_deg: float, speed: float = 1.0) -> np.ndarray:
        a, bt = math.radians(alpha_deg), math.radians(beta_deg)
        return speed * np.array([math.cos(a) * math.cos(bt), -math.sin(bt), math.sin(a) * math.cos(bt)])

    def solve(self, alpha_deg: float, beta_deg: float = 0.0, deflections: dict | None = None,
              speed: float = 100.0, rho: float = 1.225) -> VLMResult:
        pan = self.pan
        n = self._normals(deflections or {})
        vinf = self.freestream(alpha_deg, beta_deg, speed)
        AIC = np.einsum("ijk,ik->ij", self._V_cp, n)
        gamma = np.linalg.solve(AIC, -(n @ vinf))
        w_mid = np.einsum("ijk,j->ik", self._V_mid, gamma)
        l = pan.b - pan.a
        forces = rho * gamma[:, None] * np.cross(vinf[None, :] + w_mid, l)
        q = 0.5 * rho * speed**2
        F = forces.sum(axis=0)
        u = vinf / speed
        drag = float(F @ u)
        lift_dir = np.array([-u[2], 0.0, u[0]])  # perpendicular to the flow in the x-z plane (up)
        lift = float(F @ lift_dir)
        mids = 0.5 * (pan.a + pan.b)
        r = mids - np.array([self.x_cg, 0.0, 0.0])
        M = np.cross(r, forces).sum(axis=0)
        CL, CDi = lift / (q * self.S), drag / (q * self.S)
        M = M.copy()
        M[1] += self.cma_fus * math.radians(alpha_deg) * q * self.S * self.c  # fuselage (Munk) moment
        ar = self.b**2 / self.S
        e = CL**2 / (math.pi * ar * CDi) if CDi > 1e-9 else 1.0
        dcp = np.einsum("ik,ik->i", forces, n) / (q * pan.area)
        return VLMResult(alpha_deg, beta_deg, gamma, pan, vinf, forces, CL, CDi, float(F[1] / (q * self.S)),
                         float(M[1] / (q * self.S * self.c)), dcp, float(np.clip(e, 0, 1.5)), q)

    def velocity(self, points: np.ndarray, res: VLMResult, chunk: int = 1500) -> np.ndarray:
        """Flow velocity at arbitrary points (freestream + vortices + fuselage sources)."""
        out = np.empty_like(points, dtype=float)
        speed = float(np.linalg.norm(res.vinf))
        src = np.column_stack([self._src_x, np.zeros_like(self._src_x), self._src_z])
        for i in range(0, len(points), chunk):
            p = points[i:i + chunk]
            v = res.vinf + np.einsum("mnk,n->mk", horseshoe_velocity(p, self.pan, self.core, self.far), res.gamma)
            d = p[:, None, :] - src[None, :, :]
            r3 = np.maximum(np.linalg.norm(d, axis=2) ** 3, (0.3 * self.arr.fuselage.radius) ** 3)
            coef = speed * self._src_dA / (4 * math.pi) * (res.vinf[0] / speed)
            v += (coef[None, :, None] * d / r3[:, :, None]).sum(axis=1)  # fuselage source line
            out[i:i + chunk] = v
        return out

    def spanwise_loading(self, res: VLMResult, surface: str = "wing") -> tuple[np.ndarray, np.ndarray]:
        """(spanwise position y, lift per unit span) for a surface, both halves, sorted by y."""
        pan = res.panels
        mask = np.array([s == surface for s in pan.surface])
        ys, ls = [], []
        for sid in np.unique(pan.strip[mask]):
            m = mask & (pan.strip == sid)
            width = float(np.mean(np.abs(pan.b[m, 1] - pan.a[m, 1])))
            lift = float((res.forces[m] @ np.array([-res.vinf[2], 0, res.vinf[0]])).sum() / np.linalg.norm(res.vinf))
            ys.append(float(np.mean(pan.centroid[m, 1])))
            ls.append(lift / max(width, 1e-6))
        order = np.argsort(ys)
        return np.array(ys)[order], np.array(ls)[order]
