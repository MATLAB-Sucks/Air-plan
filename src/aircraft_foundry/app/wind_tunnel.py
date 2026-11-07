"""Virtual wind tunnel: the design in a flow computed by the vortex-lattice method.

* streamlines traced through the computed 3-D velocity field (vortex lattice
  + fuselage source line), coloured by local speed, with animated smoke;
  wing-tip vortices and the downwash behind the wing are visible;
* surfaces coloured by lifting pressure (delta-Cp) or by structural stress
  at any load factor, from the evaluator's sized wing box, showing where the
  airframe would break first;
* angle of attack, sideslip, airspeed and control-surface deflections;
* readouts: CL, induced drag, pitching moment, lift in kN, span efficiency,
  lift slope and static margin (compared with the evaluator), and a spanwise
  lift plot against the ideal elliptic distribution.
"""
from __future__ import annotations

import math
import threading

import numpy as np
import vtkmodules.qt

vtkmodules.qt.PyQtImpl = "PySide6"
import vtkmodules.vtkInteractionStyle  # noqa: E402,F401
import vtkmodules.vtkRenderingOpenGL2  # noqa: E402,F401
from PySide6.QtCore import QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout, QHBoxLayout,  # noqa: E402
                               QLabel, QMainWindow, QScrollArea, QSlider, QVBoxLayout, QWidget)
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # noqa: E402
from vtkmodules.util.numpy_support import numpy_to_vtk  # noqa: E402
from vtkmodules.vtkCommonCore import vtkPoints  # noqa: E402
from vtkmodules.vtkCommonDataModel import vtkImageData, vtkPolyData  # noqa: E402
from vtkmodules.vtkFiltersCore import vtkGlyph3D, vtkTubeFilter  # noqa: E402
from vtkmodules.vtkFiltersFlowPaths import vtkStreamTracer  # noqa: E402
from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkSphereSource  # noqa: E402
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera  # noqa: E402
from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer  # noqa: E402
from vtkmodules.vtkCommonCore import vtkLookupTable  # noqa: E402

from ..core.design import AircraftDesign  # noqa: E402
from ..core.evaluator import Evaluation  # noqa: E402
from ..core.mission import MissionRequirements  # noqa: E402
from ..geometry.arrangement import Arrangement  # noqa: E402
from ..numerics.atmosphere import isa  # noqa: E402
from ..numerics.vlm import VLMModel  # noqa: E402
from ..rendering.scene import disable_vtk_key_shortcuts, mesh_to_polydata  # noqa: E402

COLOUR_MODES = ("Lifting pressure (ΔCp)", "Structural stress at load factor", "Plain")


def _lut(kind: str) -> vtkLookupTable:
    lut = vtkLookupTable()
    lut.SetNumberOfTableValues(256)
    for i in range(256):
        t = i / 255
        if kind == "speed":  # blue (slow) - white - red (fast)
            r, g, b = (t * 2, t * 2, 1.0) if t < 0.5 else (1.0, 2 - 2 * t, 2 - 2 * t)
        elif kind == "stress":  # green - yellow - red, magenta beyond failure (top 1/6)
            if t > 1 / 1.2:
                r, g, b = 0.9, 0.1, 0.9
            else:
                u = t * 1.2
                r, g, b = (min(1, 2 * u), min(1, 2 * (1 - u)) if u > 0.5 else 1.0, 0.15)
        else:  # pressure: blue (low loading) - cyan - yellow - red (high)
            r, g, b = (0.1, 0.3 + 1.4 * t, 1 - 0.8 * t) if t < 0.5 else (2 * t - 1 + 0.2, 1.4 - 1.2 * t, 0.2)
        lut.SetTableValue(i, min(r, 1), min(g, 1), min(b, 1), 1.0)
    lut.Build()
    return lut


class LoadingPlot(QWidget):
    """Spanwise lift per unit span (VLM) against an elliptic distribution with the same total lift."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.y = self.l = None

    def set_data(self, y, l):
        self.y, self.l = y, l
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self.palette().base())
        if self.y is None or len(self.y) < 2:
            return
        semi = max(abs(self.y).max(), 1e-6) * 1.04
        total = np.trapezoid(self.l, self.y)
        ys = np.linspace(-semi / 1.04, semi / 1.04, 80)
        ell = 4 * total / (math.pi * 2 * semi / 1.04) * np.sqrt(np.clip(1 - (ys / (semi / 1.04)) ** 2, 0, 1))
        top = max(self.l.max(), ell.max(), 1e-6) * 1.15

        def px(y, v):
            return QPointF(w / 2 + y / semi * (w / 2 - 8), h - 18 - v / top * (h - 34))
        p.setPen(QPen(QColor(150, 150, 150), 1, Qt.DashLine))
        for a, b in zip(ys[:-1], ys[1:]):
            p.drawLine(px(a, np.interp(a, ys, ell)), px(b, np.interp(b, ys, ell)))
        p.setPen(QPen(QColor(230, 120, 40), 2))
        for i in range(len(self.y) - 1):
            p.drawLine(px(self.y[i], self.l[i]), px(self.y[i + 1], self.l[i + 1]))
        p.setPen(self.palette().text().color())
        p.drawText(6, 14, "Wing lift per metre of span — orange: this design, dashed: ideal elliptic")
        p.drawText(6, h - 4, f"left tip  ←  {semi / 1.04:.1f} m  →  right tip      peak {self.l.max() / 1000:.1f} kN/m")
        p.end()


class WindTunnelWindow(QMainWindow):
    def __init__(self, design: AircraftDesign, mission: MissionRequirements, evaluation: Evaluation, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Wind tunnel — {design.name}")
        self.resize(1400, 880)
        self.design, self.mission, self.ev = design, mission, evaluation
        self.params = design.params
        self.arr = Arrangement(self.params)
        self.vlm = VLMModel(self.arr, evaluation.metrics["x_cg_m"])
        self.n_ult = self.params.structure.limit_load_factor * self.params.structure.safety_factor
        self.res = None
        self._field_thread = None
        self._field_result = None
        self._field_key = None
        self._pending = False
        self._stream_actors = []
        self.particles = []
        x0, x1 = self.arr.overall_length()
        self.L, self.x0, self.x1 = x1 - x0, x0, x1
        self.b = self.arr.wing.span
        self.z_w = float(np.mean(self.vlm.pan.centroid[[s == "wing" for s in self.vlm.pan.surface], 2]))

        # ---------------- 3D view
        self.vtk_widget = QVTKRenderWindowInteractor(self)
        self.renderer = vtkRenderer()
        self.renderer.GradientBackgroundOn()
        self.renderer.SetBackground(0.10, 0.11, 0.14)
        self.renderer.SetBackground2(0.22, 0.25, 0.30)
        self.vtk_widget.GetRenderWindow().AddRenderer(self.renderer)
        style = vtkInteractorStyleTrackballCamera()
        disable_vtk_key_shortcuts(style)
        self.vtk_widget.GetRenderWindow().GetInteractor().SetInteractorStyle(style)
        self._build_model()
        self._build_tunnel()
        self.setCentralWidget(self.vtk_widget)

        # ---------------- controls
        panel = QWidget()
        lay = QVBoxLayout(panel)
        form = QFormLayout()
        self.sliders = {}

        def slider(key, label, lo, hi, val, step=0.5):
            s = QSlider(Qt.Horizontal)
            s.setRange(int(lo / step), int(hi / step))
            s.setValue(int(val / step))
            lbl = QLabel()
            row = QHBoxLayout()
            row.addWidget(s, 1)
            row.addWidget(lbl)
            s.valueChanged.connect(lambda _: self._changed())
            self.sliders[key] = (s, step, lbl)
            form.addRow(label, row)
        slider("alpha", "Angle of attack", -6, 18, 4)
        slider("beta", "Sideslip", -12, 12, 0)
        slider("elevator", "Elevator (TE down +)", -25, 25, 0)
        slider("aileron", "Aileron (roll right +)", -20, 20, 0)
        slider("rudder", "Rudder", -25, 25, 0)
        slider("flap", "Flaps", 0, 40, 0, 1.0)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(20, 700)
        self.speed.setValue(round(evaluation.metrics.get("approach_speed_ms", 70) * 1.3))
        self.speed.setSuffix(" m/s")
        self.speed.valueChanged.connect(lambda _: self._changed())
        self.alt = QComboBox()
        self.alt.addItems(["Sea level", f"Cruise altitude ({mission.cruise_altitude_m:.0f} m)"])
        self.alt.currentIndexChanged.connect(lambda _: self._changed())
        form.addRow("Airspeed", self.speed)
        form.addRow("Air density", self.alt)
        self.colour = QComboBox()
        self.colour.addItems(COLOUR_MODES)
        self.colour.currentIndexChanged.connect(lambda _: self._changed())
        form.addRow("Colour surfaces by", self.colour)
        slider("n", "Load factor (stress view)", -2, round(self.n_ult * 1.4, 1), 1.0, 0.1)
        self.smoke = QCheckBox("Streamlines")
        self.smoke.setChecked(True)
        self.animate = QCheckBox("Animated smoke")
        self.animate.setChecked(True)
        self.smoke.toggled.connect(lambda _: self._changed(field=True))
        row = QHBoxLayout()
        row.addWidget(self.smoke)
        row.addWidget(self.animate)
        form.addRow(row)
        lay.addLayout(form)
        self.readout = QLabel()
        self.readout.setTextFormat(Qt.RichText)
        self.readout.setWordWrap(True)
        self.readout.setStyleSheet("background: palette(base); padding: 6px; border-radius: 4px;")
        lay.addWidget(self.readout)
        self.plot = LoadingPlot()
        lay.addWidget(self.plot)
        note = QLabel("Vortex-lattice method: inviscid, attached flow (no stall or separation), fixed flat wake; "
                      "the fuselage moment uses the evaluator's statistical term. Stress colours scale the sized "
                      "wing box's ultimate-load margins to the chosen load factor.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        dock = QDockWidget("Wind tunnel", self)
        dock.setWidget(scroll)
        dock.setMinimumWidth(420)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.recompute)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check_field)
        self._poll.start(100)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._animate)
        self._anim.start(33)
        cam = self.renderer.GetActiveCamera()
        cam.SetFocalPoint(0.5 * (self.x0 + self.x1), 0, self.z_w)
        cam.SetPosition(self.x0 - 0.9 * self.L, -1.5 * self.b, self.z_w + 0.8 * self.b)
        cam.SetViewUp(0, 0, 1)
        self.renderer.ResetCameraClippingRange()
        self._changed()  # fill in the slider value labels
        self.recompute()

    # ------------------------------------------------------------ scene
    def _build_model(self):
        self.parts = []
        for c in self.design.components.values():
            if c.is_structure:
                continue
            mesh = c.world_mesh()
            poly = mesh_to_polydata(mesh)
            mapper = vtkPolyDataMapper()
            mapper.SetInputData(poly)
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetSpecular(0.25)
            self.renderer.AddActor(actor)
            pts = np.array([poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())])
            self.parts.append({"id": c.id, "actor": actor, "poly": poly, "pts": pts, "mapper": mapper})
        from scipy.spatial import cKDTree
        self._tree = cKDTree(self.vlm.pan.centroid)

    def _build_tunnel(self):
        """Wire-frame test section around the model."""
        box = vtkCubeSource()
        box.SetBounds(self.x0 - 0.35 * self.L, self.x1 + 0.7 * self.L, -0.75 * self.b, 0.75 * self.b,
                      self.z_w - 0.45 * self.b, self.z_w + 0.45 * self.b)
        m = vtkPolyDataMapper()
        m.SetInputConnection(box.GetOutputPort())
        a = vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetRepresentationToWireframe()
        a.GetProperty().SetColor(0.45, 0.5, 0.6)
        a.GetProperty().SetOpacity(0.4)
        self.renderer.AddActor(a)

    # ------------------------------------------------------------ state
    def value(self, key):
        s, step, _ = self.sliders[key]
        return s.value() * step

    def deflections(self) -> dict:
        d = {"elevator": self.value("elevator"), "aileron": self.value("aileron"), "rudder": self.value("rudder")}
        for cs in self.arr.control_surfaces:
            if cs.kind == "flap":
                d[cs.id] = self.value("flap")
            if cs.kind == "rudder":
                d[cs.id] = -self.value("rudder")
            if cs.kind == "elevator":
                d[cs.id] = self.value("elevator") * (-1 if cs.surface == "canard" else 1)
        return d

    def density(self) -> float:
        return 1.225 if self.alt.currentIndex() == 0 else isa(self.mission.cruise_altitude_m).density

    def _changed(self, field: bool = False):
        for key, (s, step, lbl) in self.sliders.items():
            unit = " g" if key == "n" else "°"
            lbl.setText(f"{s.value() * step:+.1f}{unit}")
        self._debounce.start(120)

    # ------------------------------------------------------------ compute
    def recompute(self):
        a, b = self.value("alpha"), self.value("beta")
        V, rho = self.speed.value(), self.density()
        self.res = self.vlm.solve(a, b, self.deflections(), V, rho)
        r_plus = self.vlm.solve(a + 1.0, b, self.deflections(), V, rho)
        cla = (r_plus.CL - self.res.CL) / math.radians(1.0)
        cma = (r_plus.Cm - self.res.Cm) / math.radians(1.0)
        self._colour_surfaces()
        y, l = self.vlm.spanwise_loading(self.res)
        self.plot.set_data(y, l)
        q = self.res.q
        lift_kn = self.res.CL * q * self.vlm.S / 1000
        weight_kn = self.ev.metrics["mtow_kg"] * 9.80665 / 1000
        clmax = self.ev.metrics.get("clmax_landing", 1.5) if self.value("flap") > 5 else \
            max(1.1, self.ev.metrics.get("clmax_landing", 1.5) - 1.0)
        stall = self.res.CL > clmax
        sm_vlm, sm_ev = -cma / cla, self.ev.metrics["static_margin"]
        fail_n = self._failure_load()
        self.readout.setText(
            f"<b>CL</b> {self.res.CL:.3f} &nbsp; <b>CDi</b> {self.res.CDi:.4f} &nbsp; <b>Cm</b> {self.res.Cm:+.3f} "
            f"&nbsp; <b>CY</b> {self.res.CY:+.3f}<br>"
            f"Lift {lift_kn:,.0f} kN = {lift_kn / weight_kn:.2f} × take-off weight at {V:.0f} m/s<br>"
            f"Span efficiency e = {self.res.span_eff:.2f} &nbsp; Lift slope {cla:.2f} /rad<br>"
            f"Static margin: wind tunnel {sm_vlm:.0%} · evaluator {sm_ev:.0%} "
            f"{'(stable)' if sm_vlm > 0 else '<b style=color:#e0533d>(unstable)</b>'}<br>"
            + (f"<b style='color:#e0533d'>CL above the wing's CLmax ≈ {clmax:.2f}: a real wing would stall "
               f"here (this method assumes attached flow).</b><br>" if stall else "")
            + f"Wing box first fails at <b>{fail_n:.2f} g</b> (design ultimate {self.n_ult:.2f} g, "
              f"limit {self.params.structure.limit_load_factor:.1f} g)")
        if self.smoke.isChecked():
            self._start_field()
        else:
            self._clear_streamlines()
        self.vtk_widget.GetRenderWindow().Render()

    def _station_utilisation(self) -> tuple[np.ndarray, np.ndarray]:
        w = self.ev.structure.wing if self.ev.structure is not None else None
        if w is None:
            return np.array([0.0, 1.0]), np.array([0.0, 0.0])
        ms = np.minimum(np.minimum(np.asarray(w.ms_upper), np.asarray(w.ms_lower)), np.asarray(w.ms_web))
        return np.asarray(w.eta), 1.0 / (1.0 + ms)  # utilisation at the design ultimate load

    def _failure_load(self) -> float:
        _, u = self._station_utilisation()
        return self.n_ult / max(float(u.max()), 1e-6)

    def _colour_surfaces(self):
        mode = self.colour.currentIndex()
        n = self.value("n")
        etas, util = self._station_utilisation()
        semi = self.arr.wing.semi_span
        for part in self.parts:
            poly, pts, mapper = part["poly"], part["pts"], part["mapper"]
            cid = part["id"]
            if mode == 2:
                mapper.ScalarVisibilityOff()
                part["actor"].GetProperty().SetColor(0.82, 0.84, 0.88)
                continue
            lifting = cid.split(".")[0] in ("wing", "htail", "vtail", "canard", "fin", "fins", "v_tail", "vtails")
            if mode == 0 and not lifting:  # pressure loading only exists on the lifting surfaces
                scal = np.full(len(pts), -1.0)
                lut, rng = _lut("pressure"), (0.0, 1.0)
            elif mode == 0:
                d, idx = self._tree.query(pts)
                vals = np.where(d < 0.6 * self.vlm.c, np.abs(self.res.dCp[idx]), np.nan)
                top = max(float(np.nanpercentile(np.abs(self.res.dCp), 97)), 1e-6)
                scal = np.nan_to_num(vals / top, nan=-1.0)
                lut, rng = _lut("pressure"), (0.0, 1.0)
            else:
                if cid.startswith("wing"):
                    scal = np.interp(np.abs(pts[:, 1]) / semi, etas, util) * abs(n) / self.n_ult
                else:
                    scal = np.full(len(pts), -1.0)
                lut, rng = _lut("stress"), (0.0, 1.2)
            mapper.ScalarVisibilityOn()
            arr = numpy_to_vtk(np.asarray(scal, dtype=float), deep=True)
            arr.SetName("v")
            poly.GetPointData().SetScalars(arr)
            lut.SetBelowRangeColor(0.62, 0.64, 0.68, 1.0)
            lut.UseBelowRangeColorOn()
            mapper.SetLookupTable(lut)
            mapper.SetScalarRange(*rng)
            mapper.SetScalarModeToUsePointData()
            poly.Modified()

    # ------------------------------------------------------------ streamlines (field in a worker thread)
    def _grid(self):
        nx, ny, nz = 56, 40, 20
        xs = np.linspace(self.x0 - 0.3 * self.L, self.x1 + 0.65 * self.L, nx)
        ys = np.linspace(-0.72 * self.b, 0.72 * self.b, ny)
        zs = np.linspace(self.z_w - 0.42 * self.b, self.z_w + 0.42 * self.b, nz)
        return xs, ys, zs

    def _start_field(self):
        key = (round(self.res.alpha_deg, 2), round(self.res.beta_deg, 2), tuple(sorted(self.deflections().items())))
        if key == self._field_key or (self._field_thread and self._field_thread.is_alive()):
            self._pending = key != self._field_key
            return
        self._field_key = key
        self._pending = False
        res = self.res
        xs, ys, zs = self._grid()

        def work():
            X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
            pts = np.column_stack([X.ravel(order="F"), Y.ravel(order="F"), Z.ravel(order="F")])
            self._field_result = (xs, ys, zs, self.vlm.velocity(pts, res), np.linalg.norm(res.vinf))
        self._field_thread = threading.Thread(target=work, daemon=True)
        self._field_thread.start()

    def _check_field(self):
        if self._field_result is not None:
            xs, ys, zs, vel, vinf = self._field_result
            self._field_result = None
            self._show_streamlines(xs, ys, zs, vel, vinf)
            if getattr(self, "_pending", False):
                self._start_field()

    def _clear_streamlines(self):
        for a in getattr(self, "_stream_actors", []):
            self.renderer.RemoveActor(a)
        self._stream_actors = []
        self.particles = []

    def _show_streamlines(self, xs, ys, zs, vel, vinf):
        self._clear_streamlines()
        img = vtkImageData()
        img.SetDimensions(len(xs), len(ys), len(zs))
        img.SetOrigin(xs[0], ys[0], zs[0])
        img.SetSpacing(xs[1] - xs[0], ys[1] - ys[0], zs[1] - zs[0])
        v = numpy_to_vtk(np.ascontiguousarray(vel), deep=True)
        v.SetName("velocity")
        img.GetPointData().SetVectors(v)
        seeds = []
        xseed = xs[0] + 0.5 * (xs[1] - xs[0])
        for yy in np.linspace(-0.62 * self.b, 0.62 * self.b, 21):
            for dz in (-0.05, 0.015):
                seeds.append([xseed, yy, self.z_w + dz * self.b])
        tip_cps = [p for p, s in zip(self.vlm.pan.centroid, self.vlm.pan.surface) if s == "wing"]
        tip_cps = np.array(tip_cps)
        for side in (1, -1):
            tip = tip_cps[np.argmax(side * tip_cps[:, 1])]
            for k in range(10):  # a ring of seeds around each tip: shows the tip vortex winding up
                ang = 2 * math.pi * k / 10
                seeds.append([xseed, tip[1] - side * 0.02 * self.b + 0.035 * self.b * math.cos(ang),
                              tip[2] + 0.035 * self.b * math.sin(ang)])
        sp = vtkPoints()
        for s_ in seeds:
            sp.InsertNextPoint(*s_)
        seed_poly = vtkPolyData()
        seed_poly.SetPoints(sp)
        tracer = vtkStreamTracer()
        tracer.SetInputData(img)
        tracer.SetSourceData(seed_poly)
        tracer.SetIntegrationDirectionToForward()
        tracer.SetIntegratorTypeToRungeKutta45()
        tracer.SetMaximumPropagation(3.0 * self.L)
        tracer.SetInitialIntegrationStep(0.2)
        tracer.SetComputeVorticity(False)
        tracer.Update()
        out = tracer.GetOutput()
        speed = np.linalg.norm(np.array([out.GetPointData().GetVectors().GetTuple3(i)
                                         for i in range(out.GetNumberOfPoints())]), axis=1) if out.GetNumberOfPoints() else np.array([])
        sarr = numpy_to_vtk(speed / vinf if len(speed) else speed, deep=True)
        sarr.SetName("speed")
        out.GetPointData().SetScalars(sarr)
        tube = vtkTubeFilter()
        tube.SetInputData(out)
        tube.SetRadius(0.0035 * self.b)
        tube.SetNumberOfSides(6)
        m = vtkPolyDataMapper()
        m.SetInputConnection(tube.GetOutputPort())
        m.SetLookupTable(_lut("speed"))
        m.SetScalarRange(0.85, 1.15)
        m.ScalarVisibilityOn()
        a = vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetOpacity(0.85)
        self.renderer.AddActor(a)
        self._stream_actors = [a]
        # animated smoke: particles moving along each streamline
        lines = []
        cells = out.GetLines()
        cells.InitTraversal()
        from vtkmodules.vtkCommonCore import vtkIdList
        ids = vtkIdList()
        P = np.array([out.GetPoint(i) for i in range(out.GetNumberOfPoints())]) if out.GetNumberOfPoints() else None
        while cells.GetNextCell(ids):
            idx = [ids.GetId(k) for k in range(ids.GetNumberOfIds())]
            if len(idx) > 3:
                pts = P[idx]
                s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
                lines.append((pts, s))
        self.particles = [{"pts": pts, "s": s, "pos": k * s[-1] / 3.0} for pts, s in lines for k in range(3)]
        self._pp = vtkPoints()
        self._pp.SetNumberOfPoints(max(len(self.particles), 1))
        ppoly = vtkPolyData()
        ppoly.SetPoints(self._pp)
        sph = vtkSphereSource()
        sph.SetRadius(0.008 * self.b)
        g = vtkGlyph3D()
        g.SetInputData(ppoly)
        g.SetSourceConnection(sph.GetOutputPort())
        gm = vtkPolyDataMapper()
        gm.SetInputConnection(g.GetOutputPort())
        ga = vtkActor()
        ga.SetMapper(gm)
        ga.GetProperty().SetColor(0.95, 0.95, 0.95)
        self.renderer.AddActor(ga)
        self._stream_actors.append(ga)
        self._glyph_poly = ppoly
        self.vtk_widget.GetRenderWindow().Render()

    def _animate(self):
        if not self.particles or not self.animate.isChecked() or not self.isVisible():
            return
        step = 0.012 * self.L
        for k, p in enumerate(self.particles):
            p["pos"] = (p["pos"] + step) % p["s"][-1]
            i = int(np.searchsorted(p["s"], p["pos"]))
            i = min(max(i, 1), len(p["s"]) - 1)
            f = (p["pos"] - p["s"][i - 1]) / max(p["s"][i] - p["s"][i - 1], 1e-9)
            self._pp.SetPoint(k, *(p["pts"][i - 1] + f * (p["pts"][i] - p["pts"][i - 1])))
        self._pp.Modified()
        self._glyph_poly.Modified()
        self.vtk_widget.GetRenderWindow().Render()

    def wait_for_streamlines(self, timeout: float = 60.0) -> bool:
        """Block until the current flow field is shown (for tests and scripted screenshots)."""
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._field_thread is not None:
                self._field_thread.join(0.1)
            self._check_field()
            if self._stream_actors and (self._field_thread is None or not self._field_thread.is_alive()) \
                    and self._field_result is None:
                return True
        return False

    def closeEvent(self, ev):
        for t in (self._debounce, self._poll, self._anim):
            t.stop()
        self.vtk_widget.Finalize()
        super().closeEvent(ev)
