"""Flight-test window: fly the selected design in a 3D environment.

Keys (click the 3D view first):
    Down / Up arrow   pull up / push down          Left / Right   roll
    Q / E             yaw left / right (rudder, nose-wheel steering on the ground)
    W / S             throttle up / down (full throttle lights reheat on supersonic engines)
    F  flaps (0 / takeoff / landing)   G  gear   B  brakes   [ / ]  pitch trim
    A  stability augmentation on/off    C  camera    P  pause    R  restart
"""
from __future__ import annotations

import math
import time

import numpy as np
import vtkmodules.qt

vtkmodules.qt.PyQtImpl = "PySide6"
import vtkmodules.vtkInteractionStyle  # noqa: E402,F401
import vtkmodules.vtkRenderingOpenGL2  # noqa: E402,F401
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPolygonF  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout,  # noqa: E402
                               QGroupBox, QHBoxLayout, QLabel, QListWidget, QMainWindow, QPushButton, QScrollArea,
                               QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # noqa: E402
from vtkmodules.util.numpy_support import numpy_to_vtk  # noqa: E402
from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkDiskSource, vtkPlaneSource  # noqa: E402
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleUser  # noqa: E402
from vtkmodules.vtkCommonDataModel import vtkImageData  # noqa: E402
from vtkmodules.vtkRenderingCore import (vtkActor, vtkPolyDataMapper, vtkRenderer, vtkTextActor,  # noqa: E402
                                         vtkTexture)

from ..core.components import ComponentType as CT  # noqa: E402
from ..core.design import AircraftDesign  # noqa: E402
from ..core.evaluator import Evaluation  # noqa: E402
from ..core.mission import MissionRequirements  # noqa: E402
from ..geometry.arrangement import Arrangement  # noqa: E402
from ..geometry.builder import hinge_axis  # noqa: E402
from ..rendering.scene import disable_vtk_key_shortcuts, matrix_to_vtk, mesh_to_polydata  # noqa: E402
from ..structural_vis.styles import style_for  # noqa: E402
from ..geometry.mesh import Mesh  # noqa: E402
from ..numerics.atmosphere import G0, RHO0  # noqa: E402
from .flightmodel import D2R, FlightSimulator, build_aero_model, dynamic_modes, quat_to_dcm  # noqa: E402
from .terrain import HOME, RUNWAYS, WATER_LEVEL, Terrain, runway_by_code  # noqa: E402
from .autopilot import Autopilot, Waypoint  # noqa: E402

GEO_TO_BODY = np.diag([-1.0, 1.0, -1.0])
NED_TO_ENU = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])
CAMERAS = ("chase", "side", "tower", "cockpit")
KEY_HELP = ("Click the 3D view, then:\n"
            "↓ / ↑  pull up / push    ← / →  roll\n"
            "Q / E  yaw (rudder, nose-wheel steering)\n"
            "W / S  throttle (full = reheat if fitted)\n"
            "F flaps   G gear   B brakes   [ ] trim\n"
            "A stability augmentation   C camera\n"
            "P pause   R restart on runway")
FLAP_STEPS = (0.0, 0.4, 1.0)


def rotation_about(axis_point: np.ndarray, axis_dir: np.ndarray, angle: float) -> np.ndarray:
    k = axis_dir / np.linalg.norm(axis_dir)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * K @ K
    m = np.eye(4)
    m[:3, :3] = R
    m[:3, 3] = axis_point - R @ axis_point
    return m


class _Keys(QObject):
    """Tracks held keys on the 3D view (key auto-repeat ignored)."""

    def __init__(self, on_press):
        super().__init__()
        self.held: set[int] = set()
        self.on_press = on_press

    def eventFilter(self, obj, ev):
        # Every key event is consumed, *including auto-repeats while a key is held*.
        # Letting repeats through let VTK see them: its built-in shortcuts are
        # w = wireframe, s = surface, q/e = quit/exit, r = reset camera, f = fly-to,
        # so holding W for throttle turned the whole scene into wireframe.
        if ev.type() == QEvent.KeyPress:
            if not ev.isAutoRepeat():
                self.held.add(ev.key())
                self.on_press(ev.key())
            return True
        if ev.type() == QEvent.KeyRelease:
            if not ev.isAutoRepeat():
                self.held.discard(ev.key())
            return True
        return False


class SimulatorWindow(QMainWindow):
    def __init__(self, design: AircraftDesign, mission: MissionRequirements, evaluation: Evaluation, parent=None,
                 start: str = "runway"):
        super().__init__(parent)
        self.setWindowTitle(f"Flight test — {design.name}")
        self.resize(1400, 860)
        self.design, self.mission, self.evaluation = design, mission, evaluation
        self.params = design.params
        self.arr = Arrangement(self.params)
        self.model = build_aero_model(self.params, mission, evaluation)
        from .world import load_world
        from ..app.settings import settings as _settings
        self.terrain = load_world(_settings().value("sim/world") or None)
        self.sim = FlightSimulator(self.model, self.terrain)
        self._vario = []
        if self.model.glider:  # soaring: thermals around the home airfield
            self.sim.make_thermals(self.terrain.home.point(self.terrain.home.length / 2))
        self.trail: list[tuple[float, float]] = []
        self.coach = CircuitCoach(self)
        self.ap = Autopilot(self.sim, self.vref_for_mass(), climb_speed=1.6 * self.vref_for_mass(),
                            cruise_speed=evaluation.metrics.get("cruise_speed_ms", 200.0))
        self.debris: list[dict] = []
        self.pads = None  # ControllerManager (set by the main window) for game-controller flying
        self._rumble_state = {"stalled": False, "over": False, "touch": None, "lost": False}
        self.log: list[dict] = []
        self._log_t = -1.0
        self.wp_add_mode = False
        self.start_runway = self.terrain.home
        self.g_max, self.g_min = 1.0, 1.0
        self.paused = False
        self.camera_mode = "chase"
        self.time_scale = 1.0
        self.flap_index = 0
        self._last_wall = None
        self._boom = (0.0, 0.0)
        self._boom_t = 0.0
        self.cruise_speed = evaluation.metrics.get("cruise_speed_ms", 200.0)
        self.cruise_alt = mission.cruise_altitude_m

        # ---------------- 3D view
        self.vtk_widget = QVTKRenderWindowInteractor(self)
        self.renderer = vtkRenderer()
        self.renderer.GradientBackgroundOn()
        self.renderer.SetBackground(0.62, 0.74, 0.88)
        self.renderer.SetBackground2(0.20, 0.38, 0.66)
        self.vtk_widget.GetRenderWindow().AddRenderer(self.renderer)
        self.interactor = self.vtk_widget.GetRenderWindow().GetInteractor()
        style = vtkInteractorStyleUser()
        disable_vtk_key_shortcuts(style)  # keys go to the simulator, not to VTK's built-in shortcuts
        self.interactor.SetInteractorStyle(style)
        self._build_world()
        self._build_aircraft()
        self._build_hud()
        self.keys = _Keys(self._key_pressed)
        self.vtk_widget.installEventFilter(self.keys)
        self.vtk_widget.setFocusPolicy(Qt.StrongFocus)

        # ---------------- side panel
        panel = QWidget()
        lay = QVBoxLayout(panel)
        row = QHBoxLayout()
        b_runway, b_air = QPushButton("Start on runway"), QPushButton("Start in cruise (trimmed)")
        b_runway.clicked.connect(lambda: self.restart("runway"))
        b_air.clicked.connect(lambda: self.restart("cruise"))
        row.addWidget(b_runway)
        row.addWidget(b_air)
        lay.addLayout(row)
        form = QFormLayout()
        self.sas_box = QCheckBox("Stability augmentation (yaw / pitch damper)")
        self.sas_box.setChecked(self.sim.controls.sas)
        self.sas_box.toggled.connect(lambda on: setattr(self.sim.controls, "sas", on))
        self.cam_box = QComboBox()
        self.cam_box.addItems([c.title() for c in CAMERAS])
        self.cam_box.currentIndexChanged.connect(lambda i: setattr(self, "camera_mode", CAMERAS[i]))
        self.speed_box = QComboBox()
        self.speed_box.addItems(["1x", "2x", "4x", "8x"])
        self.speed_box.currentIndexChanged.connect(lambda i: setattr(self, "time_scale", float(2 ** i)))
        form.addRow(self.sas_box)
        form.addRow("Camera", self.cam_box)
        form.addRow("Time", self.speed_box)
        lay.addLayout(form)
        self.minimap = MiniMap(self)
        lay.addWidget(self.minimap)
        tabs = QTabWidget()
        # --- flight tab: coach, start options, weather, log
        ft = QWidget()
        fl = QVBoxLayout(ft)
        self.coach_view = QLabel()
        self.coach_view.setWordWrap(True)
        self.coach_view.setTextFormat(Qt.RichText)
        self.coach_view.setStyleSheet("background: palette(base); padding: 6px; border-radius: 4px;")
        fl.addWidget(self.coach_view)
        sf = QFormLayout()
        self.airport_box = QComboBox()
        self.airport_box.addItems([f"{r.code} — {r.name} (rwy {r.heading_deg:03.0f}, {r.elevation:.0f} m)" for r in self.terrain.runways])
        self.airport_box.currentIndexChanged.connect(lambda i: setattr(self, "start_runway", self.terrain.runways[i]))
        home_i = list(self.terrain.runways).index(self.terrain.home) if self.terrain.home in self.terrain.runways else 0
        self.airport_box.setCurrentIndex(home_i)  # start at the world's home airfield (Lasham)
        self.ap_dest_default = home_i
        self.fuel_box = QSpinBox()
        self.fuel_box.setRange(1, 100)
        self.fuel_box.setValue(60)
        self.fuel_box.setSuffix(" % fuel at start")
        self.wind_spd = QSpinBox()
        self.wind_spd.setRange(0, 60)
        self.wind_spd.setSuffix(" kt")
        self.wind_dir = QSpinBox()
        self.wind_dir.setRange(0, 359)
        self.wind_dir.setWrapping(True)
        self.wind_dir.setSuffix("° from")
        self.turb_box = QComboBox()
        self.turb_box.addItems(["No turbulence", "Light", "Moderate", "Severe"])
        for wdg in (self.wind_spd, self.wind_dir):
            wdg.valueChanged.connect(self._apply_weather)
        self.turb_box.currentIndexChanged.connect(self._apply_weather)
        from .world import AREAS, DETAILS, PROCEDURAL, SIZES, parse_key
        cur_area, cur_size, cur_detail = (("test-range", 40, "standard") if getattr(self.terrain, "name", "") == PROCEDURAL
                                          else parse_key(getattr(self.terrain, "name", "")))
        self.area_box = QComboBox()
        self._areas = [(a, t) for a, t in AREAS] + [(PROCEDURAL, "Test range (fictional, four airports)")]
        self.area_box.addItems([t for _, t in self._areas])
        self.area_box.setCurrentIndex(next((i for i, (a, _) in enumerate(self._areas) if a == cur_area), 0))
        self.size_box = QComboBox()
        self.size_box.addItems([f"{s_} km × {s_} km" for s_ in SIZES])
        self.size_box.setCurrentIndex(SIZES.index(cur_size) if cur_size in SIZES else 0)
        self.detail_box = QComboBox()
        self.detail_box.addItems(["Standard detail", "High detail (sharper terrain and imagery)"])
        self.detail_box.setCurrentIndex(DETAILS.index(cur_detail) if cur_detail in DETAILS else 0)
        self.world_btn = QPushButton()
        self.world_btn.clicked.connect(self._fly_world)
        for wdg in (self.area_box, self.size_box, self.detail_box):
            wdg.currentIndexChanged.connect(self._update_world_button)
        sf.addRow("Area", self.area_box)
        wrow_ = QHBoxLayout()
        wrow_.addWidget(self.size_box)
        wrow_.addWidget(self.detail_box)
        sf.addRow("Map", wrow_)
        sf.addRow(self.world_btn)
        self._update_world_button()
        sf.addRow("Start at", self.airport_box)
        sf.addRow("Fuel", self.fuel_box)
        wrow = QHBoxLayout()
        wrow.addWidget(self.wind_spd)
        wrow.addWidget(self.wind_dir)
        sf.addRow("Wind", wrow)
        sf.addRow("Turbulence", self.turb_box)
        fl.addLayout(sf)
        if getattr(self.terrain, "attribution", None):
            attr = QLabel("Map data: " + " · ".join(self.terrain.attribution))
            attr.setWordWrap(True)
            attr.setObjectName("hint")
            fl.addWidget(attr)
        if self.model.glider:
            gbox = QGroupBox("Glider")
            gl_ = QFormLayout(gbox)
            self.launch_box = QComboBox()
            self.launch_box.addItems(["Winch launch (to ~400 m)", "Aerotow (to ~600 m)"])
            b_launch = QPushButton("Launch  (L)")
            b_launch.setObjectName("primary")
            b_launch.clicked.connect(lambda: self._key_pressed(Qt.Key_L))
            self.show_thermals = QCheckBox("Show thermals on the map (learning aid)")
            self.show_thermals.setChecked(True)
            gl_.addRow("Launch", self.launch_box)
            gl_.addRow(b_launch)
            gl_.addRow(self.show_thermals)
            if self.model.motor_kw > 0:
                self.motor_btn = QPushButton("Engine: extend  (N)")
                self.motor_btn.clicked.connect(lambda: (self.sim.motor_toggle(), self._update_motor_button()))
                gl_.addRow("Engine", self.motor_btn)
            tips = QLabel("Release with L at the top. Look for lift on the variometer, then circle tightly "
                          "(30–45° bank) to stay in it. F works the airbrakes for landing.")
            tips.setWordWrap(True)
            tips.setObjectName("hint")
            gl_.addRow(tips)
            fl.addWidget(gbox)
        b_log = QPushButton("Export flight log (CSV)…")
        b_log.clicked.connect(self.export_log)
        fl.addWidget(b_log)
        fl.addStretch(1)
        tabs.addTab(ft, "Flight")
        # --- autopilot tab
        at = QWidget()
        al = QVBoxLayout(at)
        af = QFormLayout()
        self.ap_program = QComboBox()
        self.ap_programs = [("hold", "Hold altitude / heading / speed"), ("circuit", "Take off, fly a circuit, land"),
                            ("circuits", "Continuous circuits (touch-and-go until reserve)"),
                            ("flight_plan", "Fly flight plan → land at destination"), ("land", "Land now at destination")]
        self.ap_program.addItems([label for _, label in self.ap_programs])
        self.ap_dest = QComboBox()
        self.ap_dest.addItems([f"{r.code} — {r.name}" for r in self.terrain.runways])
        self.ap_dest.setCurrentIndex(getattr(self, "ap_dest_default", 0))
        self.ap_height = QSpinBox()
        self.ap_height.setRange(150, 3000)
        self.ap_height.setValue(450)
        self.ap_height.setSuffix(" m above field")
        self.ap_reserve = QSpinBox()
        self.ap_reserve.setRange(1, 90)
        self.ap_reserve.setValue(15)
        self.ap_reserve.setSuffix(" % fuel reserve")
        af.addRow("Program", self.ap_program)
        af.addRow("Destination", self.ap_dest)
        af.addRow("Circuit height", self.ap_height)
        af.addRow("Stop circuits at", self.ap_reserve)
        al.addLayout(af)
        row2 = QHBoxLayout()
        b_eng, b_dis = QPushButton("Engage autopilot"), QPushButton("Disconnect")
        b_eng.clicked.connect(self.engage_autopilot)
        b_dis.clicked.connect(lambda: self.ap.disengage("pilot"))
        row2.addWidget(b_eng)
        row2.addWidget(b_dis)
        al.addLayout(row2)
        wpbox = QGroupBox("Flight plan waypoints")
        wl = QVBoxLayout(wpbox)
        self.wp_list = QListWidget()
        self.wp_list.setMaximumHeight(110)
        wl.addWidget(self.wp_list)
        wrow2 = QHBoxLayout()
        self.wp_alt = QSpinBox()
        self.wp_alt.setRange(100, 15000)
        self.wp_alt.setValue(1500)
        self.wp_alt.setSuffix(" m")
        self.wp_spd = QSpinBox()
        self.wp_spd.setRange(60, 600)
        self.wp_spd.setValue(int(self.ap.climb_speed * 1.944))
        self.wp_spd.setSuffix(" kt")
        self.wp_click = QCheckBox("Click map to add")
        self.wp_click.toggled.connect(lambda on: setattr(self, "wp_add_mode", on))
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(self._clear_waypoints)
        for wdg in (self.wp_click, self.wp_alt, self.wp_spd, b_clear):
            wrow2.addWidget(wdg)
        wl.addLayout(wrow2)
        al.addWidget(wpbox)
        self.lap_view = QTextBrowser()
        self.lap_view.setMaximumHeight(140)
        al.addWidget(QLabel("<b>Lap log</b> (circuits: fuel, distance, how steadily height was held)"))
        al.addWidget(self.lap_view)
        tabs.addTab(at, "Autopilot")
        # --- analysis tab: flying qualities
        qt_ = QWidget()
        ql = QVBoxLayout(qt_)
        self.modes_view = QTextBrowser()
        ql.addWidget(QLabel("<b>Flying qualities</b> — bare airframe, linearised about cruise trim"))
        ql.addWidget(self.modes_view, 1)
        tabs.addTab(qt_, "Flying qualities")
        lay.addWidget(tabs, 1)
        help_lbl = QLabel(KEY_HELP)
        help_lbl.setFont(QFont("Menlo", 9))
        help_lbl.setStyleSheet("color: palette(mid);")
        lay.addWidget(help_lbl)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        panel = scroll
        dock = QDockWidget("Flight test", self)
        dock.setWidget(panel)
        dock.setMinimumWidth(430)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        # the 3-D view in a container, so the cockpit instrument panel can sit below it (never on top of OpenGL)
        self._view_box = QWidget()
        vb = QVBoxLayout(self._view_box)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)
        vb.addWidget(self.vtk_widget, 1)
        self.setCentralWidget(self._view_box)
        self._show_modes()
        self.restart(start)
        self._panel_t = 0.0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    # ------------------------------------------------------------ scene
    def _build_world(self):
        from vtkmodules.vtkCommonCore import vtkPoints, vtkUnsignedCharArray
        from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
        from vtkmodules.vtkRenderingCore import vtkLight
        T = self.terrain
        north, east, h = T.grid
        target = 700 if getattr(T, "detail", "standard") == "high" else 400
        step = max(1, int(np.ceil(len(north) / target)))  # render at most ~400-700 per side (physics: full grid)
        north, east, h = north[::step], east[::step], h[::step, ::step]
        n = len(north)
        NN, EE = np.meshgrid(north, east, indexing="ij")
        pts = np.column_stack([EE.ravel(), NN.ravel(), h.ravel()])  # ENU
        idx = np.arange(n * n).reshape(n, n)
        a_, b_, c_, d_ = idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()
        tris = np.concatenate([np.column_stack([a_, d_, c_]), np.column_stack([a_, c_, b_])])
        poly = mesh_to_polydata(Mesh(pts, tris))
        texture_path = getattr(T, "texture_path", None)
        if texture_path is not None:
            # satellite imagery draped over the real terrain
            from vtkmodules.vtkIOImage import vtkJPEGReader
            out_pts = np.array([poly.GetPoint(i) for i in range(poly.GetNumberOfPoints())])
            uv = np.column_stack([(out_pts[:, 0] - east[0]) / (east[-1] - east[0]),
                                  (out_pts[:, 1] - north[0]) / (north[-1] - north[0])])
            tc = numpy_to_vtk(np.ascontiguousarray(uv, dtype=np.float32), deep=True)
            tc.SetName("TextureCoordinates")
            poly.GetPointData().SetTCoords(tc)
            reader = vtkJPEGReader()
            reader.SetFileName(str(texture_path))
            tex = vtkTexture()
            tex.SetInputConnection(reader.GetOutputPort())
            tex.InterpolateOn()
            tex.MipmapOn()
            ground = self._actor(poly, (1, 1, 1), texture=tex)
            ground.GetMapper().ScalarVisibilityOff()
        else:
            col = T.colours()[::step, ::step].reshape(-1, 3)
            arr = numpy_to_vtk(np.ascontiguousarray(col, dtype=np.uint8), deep=True)
            arr.SetName("Colors")
            poly.GetPointData().SetScalars(arr)
            ground = self._actor(poly, (1, 1, 1))
            ground.GetMapper().SetScalarModeToUsePointData()
            ground.GetMapper().ScalarVisibilityOn()
        ground.GetProperty().SetAmbient(0.25)
        self.renderer.AddActor(ground)
        if texture_path is None:  # the procedural world's sea and lakes
            water = vtkPlaneSource()
            water.SetOrigin(east[0], north[0], WATER_LEVEL)
            water.SetPoint1(east[-1], north[0], WATER_LEVEL)
            water.SetPoint2(east[0], north[-1], WATER_LEVEL)
            wa = self._actor(water.GetOutputPort(), (0.28, 0.45, 0.62))
            wa.GetProperty().SetSpecular(0.6)
            self.renderer.AddActor(wa)
        # town: one merged mesh of boxes
        verts, faces = [], []
        box_f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                          [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        for k, (bn, be, w, d, ht) in enumerate(T.town()):
            z0 = T.height_at(bn, be)
            x0, x1, y0, y1 = be - w / 2, be + w / 2, bn - d / 2, bn + d / 2
            verts.append(np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                                   [x0, y0, z0 + ht], [x1, y0, z0 + ht], [x1, y1, z0 + ht], [x0, y1, z0 + ht]]))
            faces.append(box_f + 8 * k)
        if verts:
            self.renderer.AddActor(self._actor(mesh_to_polydata(Mesh(np.vstack(verts), np.vstack(faces))),
                                               (0.78, 0.74, 0.68)))
        # runways of every airport: surfaces, markings and edge lights merged per colour; PAPI per runway
        surf, white, lights = [], [], []
        self.papis = []  # (runway, [4 actors], aim point ENU)
        for rw in self.terrain.runways:
            hw, L, z = rw.half_width, rw.length, rw.elevation
            surf.append(oriented_box(rw, -20, L + 20, -hw - 4, hw + 4, z + 0.10, z + 0.16))
            for a_ in np.arange(400, L - 100, 60):
                white.append(oriented_box(rw, a_, a_ + 30, -0.5, 0.5, z + 0.05, z + 0.08))
            for k in range(8):
                r_ = -hw + 3 + k * (2 * hw - 6) / 7
                white.append(oriented_box(rw, 10, 50, r_ - 0.9, r_ + 0.9, z + 0.05, z + 0.08))
            for a_ in (300, 450):
                for r_ in (-hw / 2, hw / 2):
                    white.append(oriented_box(rw, a_, a_ + 30, r_ - 2, r_ + 2, z + 0.05, z + 0.08))
            for a_ in np.arange(0, L + 1, 60):
                for r_ in (-hw - 2, hw + 2):
                    lights.append(oriented_box(rw, a_ - 0.4, a_ + 0.4, r_ - 0.4, r_ + 0.4, z, z + 0.6))
            acts = []
            for k in range(4):  # PAPI left of the runway, abeam the aiming point
                r0 = -hw - 15 - 9 * k
                act = self._actor(mesh_to_polydata(oriented_box(rw, rw.aim_point - 1.5, rw.aim_point + 1.5,
                                                                  r0 - 1.5, r0 + 1.5, z, z + 1.5)), (1, 1, 1))
                act.GetProperty().SetAmbient(1.0)
                act.GetProperty().SetDiffuse(0.0)
                self.renderer.AddActor(act)
                acts.append(act)
            n_, e_ = rw.point(rw.aim_point, -hw - 30)
            self.papis.append((rw, acts, np.array([e_, n_, z])))
        for meshes, colour in ((surf, (0.22, 0.22, 0.24)), (white, (0.95, 0.95, 0.95)), (lights, (1.0, 0.95, 0.7))):
            act = self._actor(mesh_to_polydata(Mesh.merge(meshes)), colour)
            act.GetMapper().SetResolveCoincidentTopologyToPolygonOffset()  # drawn on top of the ground
            act.GetMapper().SetRelativeCoincidentTopologyPolygonOffsetParameters(-2, -6)
            self.renderer.AddActor(act)
        self.papi = self.papis[0][1]  # home runway PAPI (kept for tests / backwards compatibility)
        self.papi_angles = (3.5, 3.17, 2.83, 2.5)  # standard PAPI: lights white above their angle
        sun = vtkLight()
        sun.SetLightTypeToSceneLight()
        sun.SetPosition(-30000, -20000, 40000)
        sun.SetFocalPoint(0, 0, 0)
        sun.SetIntensity(0.9)
        self.renderer.AddLight(sun)
        shadow = vtkDiskSource()
        shadow.SetInnerRadius(0.0)
        shadow.SetOuterRadius(max(self.params.wing.span, self.params.fuselage.length) * 0.35)
        shadow.SetCircumferentialResolution(40)
        self.shadow = self._actor(shadow.GetOutputPort(), (0.1, 0.12, 0.1))
        self.shadow.GetProperty().SetOpacity(0.35)
        self.renderer.AddActor(self.shadow)

    @staticmethod
    def _actor(port_or_poly, colour, texture=None) -> vtkActor:
        mapper = vtkPolyDataMapper()
        if hasattr(port_or_poly, "GetNumberOfPoints"):
            mapper.SetInputData(port_or_poly)
        else:
            mapper.SetInputConnection(port_or_poly)
        a = vtkActor()
        a.SetMapper(mapper)
        a.GetProperty().SetColor(*colour)
        if texture is not None:
            a.SetTexture(texture)
        return a

    def _build_aircraft(self):
        """Actors in design coordinates; moving parts keep their hinge or spin axis."""
        self.parts: list[tuple[vtkActor, str, object]] = []  # (actor, kind, data)
        self.part_ids: list[str] = []
        cs_defs = {cs.id: cs for cs in self.arr.control_surfaces}
        props = {f"propeller.{e.index}": e for e in self.arr.engines}
        for c in self.design.components.values():
            if c.is_structure:  # internal structure and fittings are not visible in flight
                continue
            actor = self._actor(mesh_to_polydata(c.world_mesh()), style_for(c)[:3])
            actor.GetProperty().SetSpecular(0.3)
            self.renderer.AddActor(actor)
            if c.id in cs_defs:
                cs = cs_defs[c.id]
                surf, plc = self.arr.surface(cs.surface), self.arr.surface_placement(cs.surface)
                p0, p1 = (plc.apply(p[None, :])[0] for p in hinge_axis(surf, cs))
                em = 0.5 * (cs.eta0 + cs.eta1)
                te = plc.apply(surf.map_section(em, np.array([[1.0, 0.0]]), cs.side))[0]
                probe = rotation_about(p0, p1 - p0, 0.05) @ np.append(te, 1.0)
                moved = (probe[:3] - te)[1] if surf.vertical else (probe[:3] - te)[2]
                sense = -1.0 if moved > 0 else 1.0  # positive deflection: TE down / to port
                self.parts.append((actor, "control", (cs, p0, p1 - p0, sense)))
                self.part_ids.append(c.id)
                continue
            elif c.id in props:
                e = props[c.id]
                centre = np.array([e.prop_x, e.inlet[1], e.inlet[2]])
                self.parts.append((actor, "prop", centre))
                self.part_ids.append(c.id)
                continue
            elif c.type in (CT.GEAR_STRUT, CT.WHEEL):
                self.parts.append((actor, "gear", None))
            else:
                self.parts.append((actor, "static", None))
            self.part_ids.append(c.id)
        self.prop_angle = 0.0

    def _build_hud(self):
        self.hud = vtkTextActor()
        tp = self.hud.GetTextProperty()
        tp.SetFontFamilyToCourier()
        tp.SetFontSize(15)
        tp.SetColor(0.1, 1.0, 0.3)
        tp.SetBackgroundColor(0, 0, 0)
        tp.SetBackgroundOpacity(0.35)
        self.hud.SetPosition(12, 12)
        self.renderer.AddViewProp(self.hud)
        self.warn = vtkTextActor()
        wp = self.warn.GetTextProperty()
        wp.SetFontSize(26)
        wp.SetBold(True)
        wp.SetColor(1.0, 0.25, 0.2)
        wp.SetJustificationToCentered()
        self.warn.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        self.warn.SetPosition(0.5, 0.82)
        self.renderer.AddViewProp(self.warn)

    # ------------------------------------------------------------ flying
    def restart(self, where: str = "runway"):
        if hasattr(self, "fuel_box"):
            self.sim.set_fuel_fraction(self.fuel_box.value() / 100.0)
        if hasattr(self, "ap"):
            self.ap.disengage()
            self.ap.vref = self.vref_for_mass()
        self._restore_debris()
        self._broken_up = False
        self.log = []
        if where == "cruise":
            ok = self.sim.start_in_flight(self.cruise_speed, self.cruise_alt)
            self.flap_index = 0
            if not ok:
                self.warn.SetInput("Could not trim at cruise: insufficient thrust or control power")
        else:
            self.sim.place_on_runway(self.start_runway)
            self.flap_index = 1
            self.sim.controls.flaps = FLAP_STEPS[1]
        self.sim.controls.sas = self.sas_box.isChecked() if hasattr(self, "sas_box") else self.sim.controls.sas
        self.paused = False
        self._last_wall = None
        self.trail = []
        self.coach.reset(airborne_start=(where == "cruise"))
        self.g_max, self.g_min = 1.0, 1.0

    def _key_pressed(self, key: int):
        c = self.sim.controls
        if key == Qt.Key_F:
            self.flap_index = (self.flap_index + 1) % len(FLAP_STEPS)
            c.flaps = FLAP_STEPS[self.flap_index]
        elif key == Qt.Key_G:
            c.gear_down = not c.gear_down
        elif key == Qt.Key_B:
            c.brakes = not c.brakes
        elif key == Qt.Key_A:
            self.sas_box.setChecked(not self.sas_box.isChecked())
        elif key == Qt.Key_C:
            self.cam_box.setCurrentIndex((CAMERAS.index(self.camera_mode) + 1) % len(CAMERAS))
        elif key == Qt.Key_N and self.model.motor_kw > 0:  # motor glider: extend / start / stop / retract
            self.sim.motor_toggle()
        elif key == Qt.Key_L and self.model.glider:
            if self.sim.launch is not None:
                self.sim.release("released by the pilot")
            else:
                self.sim.start_launch("aerotow" if self.launch_box.currentIndex() == 1 else "winch")
        elif key == Qt.Key_F1:
            self._toggle_help()
        elif key == Qt.Key_P:
            self.paused = not self.paused
        elif key == Qt.Key_R:
            self.restart("runway")
        elif key == Qt.Key_BracketLeft:
            c.pitch_trim = max(-1.0, c.pitch_trim - 0.02)
        elif key == Qt.Key_BracketRight:
            c.pitch_trim = min(1.0, c.pitch_trim + 0.02)

    def _update_controls(self, dt: float):
        held, c = self.keys.held, self.sim.controls

        def axis(pos, neg, current, rate=2.5, centre=3.0):
            target = (1.0 if pos in held else 0.0) - (1.0 if neg in held else 0.0)
            r = rate if target != 0 else centre
            return float(np.clip(current + np.clip(target - current, -r * dt, r * dt), -1, 1))

        flight_keys = {Qt.Key_Down, Qt.Key_Up, Qt.Key_Left, Qt.Key_Right, Qt.Key_Q, Qt.Key_E, Qt.Key_W, Qt.Key_S}
        pad = self.pads.primary if self.pads is not None else None
        if self.ap.engaged:
            if held & flight_keys:
                self.ap.disengage("manual control input")  # like a real autopilot: any input takes over
            elif pad is not None:
                import dataclasses
                from ..input.controller import flight_inputs
                if "manual" in flight_inputs(pad, self.pads.settings, dataclasses.replace(c), 0.0):
                    self.ap.disengage("controller input")
                else:
                    return
            else:
                return
        if pad is not None and not (held & flight_keys):
            from ..input.controller import flight_inputs
            for action in flight_inputs(pad, self.pads.settings, c, dt):
                if action == "flaps":
                    self._key_pressed(Qt.Key_F)
                elif action == "gear":
                    self._key_pressed(Qt.Key_G)
                elif action == "camera":
                    self._key_pressed(Qt.Key_C)
                elif action == "sas":
                    self._key_pressed(Qt.Key_A)
                elif action == "pause":
                    self._key_pressed(Qt.Key_P)
                elif action == "restart":
                    self._key_pressed(Qt.Key_R)
            return
        c.elevator = axis(Qt.Key_Down, Qt.Key_Up, c.elevator)
        c.aileron = axis(Qt.Key_Right, Qt.Key_Left, c.aileron)
        c.rudder = axis(Qt.Key_E, Qt.Key_Q, c.rudder)
        if Qt.Key_W in held:
            c.throttle = min(1.0, c.throttle + 0.5 * dt)
        if Qt.Key_S in held:
            c.throttle = max(0.0, c.throttle - 0.5 * dt)

    def advance(self, seconds: float):
        """Integrate the flight model (also used by tests without a timer)."""
        steps = max(1, int(round(seconds * 200)))
        for _ in range(steps):
            self.ap.update(1 / 200)
            self.sim.step(1 / 200)
            if self.sim.time - self._log_t >= 0.5:
                self._record()
            if self.sim.crashed:
                break

    def _rumble_events(self):
        """Controller rumble on stall onset, overstress, touchdown (by sink rate) and loss of the aircraft."""
        if self.pads is None or self.pads.primary is None:
            return
        t, r = self.sim.telemetry(), self._rumble_state
        if t["stalled"] and not r["stalled"]:
            self.pads.rumble(0.35, 0.55, 300)
        r["stalled"] = bool(t["stalled"])
        over = t["stress"] > 1 / max(self.model.n_ultimate / self.model.n_limit, 1.0)
        if over and not r["over"]:
            self.pads.rumble(0.7, 0.9, 350)
        r["over"] = over
        td = self.sim.touchdown
        if td is not None and td is not r["touch"]:
            k = min(max(td["sink_ms"] / 3.0, 0.15), 1.0)
            self.pads.rumble(k, 0.6 * k, 250)
        r["touch"] = td
        lost = bool(self.sim.crashed or self.sim.failure)
        if lost and not r["lost"]:
            self.pads.rumble(1.0, 1.0, 800)
        r["lost"] = lost

    def _tick(self):
        now = time.perf_counter()
        dt = 0.0 if self._last_wall is None else min(now - self._last_wall, 0.05)
        self._last_wall = now
        self._update_controls(dt)
        if not self.paused and not self.sim.crashed:
            self.advance(dt * self.time_scale)
        self._rumble_events()
        self.render_frame()

    # ------------------------------------------------------------ drawing
    def aircraft_matrix(self) -> np.ndarray:
        s = self.sim.state
        R = quat_to_dcm(s[6:10])
        pos_enu = NED_TO_ENU @ s[0:3]
        m = np.eye(4)
        m[:3, :3] = NED_TO_ENU @ R @ GEO_TO_BODY
        cg = np.array([self.model.x_cg, 0.0, 0.0])
        m[:3, 3] = pos_enu - m[:3, :3] @ cg
        return m

    def _deflection(self, cs) -> float:
        c = self.sim.controls
        e, a, r = c.elevator + c.pitch_trim, c.aileron, c.rudder
        right = cs.side > 0
        k = cs.kind
        if k == "flap":
            return 40 * c.flaps
        if k == "aileron":
            return (-20 if right else 20) * a
        if k == "elevator":
            return (20 if cs.surface == "canard" else -20) * e
        if k in ("elevon_inner", "elevon_outer"):
            roll = (-15 if right else 15) * a if k == "elevon_outer" else 0.0
            return float(np.clip(-20 * e + roll, -30, 30))
        if k == "ruddervator":
            return float(np.clip(-20 * e + (15 if right else -15) * r, -30, 30))
        if k == "rudder":
            return -25 * r
        if k == "drag_rudder":
            return 45 * max(r if right else -r, 0.0)
        return 0.0

    def render_frame(self):
        M = self.aircraft_matrix()
        vm = matrix_to_vtk(M)
        self.prop_angle = (self.prop_angle + 40 * self.sim.controls.throttle + 3) % (2 * math.pi)
        if self.sim.failure and not self.debris:
            self._break_off(M)
        if self.sim.crashed and not getattr(self, "_broken_up", False):
            self._break_up(M)
        self._update_debris()
        for actor, kind, data in self.parts:
            if kind == "debris":
                continue
            if kind in ("static", "gear"):
                actor.SetUserMatrix(vm)
                if kind == "gear":
                    actor.SetVisibility(self.sim.controls.gear_down)
            elif kind == "control":
                cs, p0, axis, sense = data
                actor.SetUserMatrix(matrix_to_vtk(M @ rotation_about(p0, axis, sense * self._deflection(cs) * D2R)))
            else:
                actor.SetUserMatrix(matrix_to_vtk(M @ rotation_about(data, np.array([1.0, 0, 0]), self.prop_angle)))
        pos = M[:3, 3] + M[:3, :3] @ np.array([self.model.x_cg, 0, 0])
        self.shadow.SetPosition(pos[0], pos[1], self.sim.ground(pos[1], pos[0]) + 0.1)
        self._update_papi(pos)
        if not self.trail or np.hypot(self.trail[-1][0] - pos[1], self.trail[-1][1] - pos[0]) > 150:
            self.trail.append((pos[1], pos[0]))  # (north, east)
            self.trail = self.trail[-600:]
        if self.sim.time - self._panel_t > 0.25 or self.sim.crashed or self.paused:
            self._panel_t = self.sim.time
            self.coach.update()
            self.minimap.update()
            if self.ap.laps:
                self.lap_view.setHtml("<table cellspacing=3><tr><th>Lap</th><th>min</th><th>fuel kg</th>"
                                      "<th>left kg</th><th>km</th><th>alt rms m</th><th>touchdown</th></tr>" + "".join(
                    f"<tr><td>{L.lap}</td><td>{L.duration_s / 60:.1f}</td><td>{L.fuel_used_kg:.0f}</td>"
                    f"<td>{L.fuel_left_kg:.0f}</td><td>{L.distance_km:.1f}</td><td>{L.altitude_rms_m:.1f}</td>"
                    f"<td>{(L.landing or {}).get('sink_ms', 0):.1f} m/s</td></tr>" for L in self.ap.laps) + "</table>")
        self._place_camera(pos, M)
        self._update_hud()
        self.vtk_widget.GetRenderWindow().Render()

    # ------------------------------------------------------------ autopilot, weather, log, debris
    def vref_for_mass(self) -> float:
        md = self.model
        mass = self.sim.mass if hasattr(self, "sim") else md.mass
        return 1.23 * math.sqrt(2 * mass * G0 / (RHO0 * md.S * (md.CLmax + md.flap_dCL)))

    world_requested = Signal(str)

    HELP = ("<b>Flight controls</b> (F1 hides this)<br>"
            "↑ / ↓ pitch · ← / → roll · Q / E rudder · W / S throttle · Shift+W reheat<br>"
            "F flaps (airbrakes on gliders) · G gear · B brakes · T / Y trim · X stability aid<br>"
            "A autopilot on/off · any control key takes over from the autopilot<br>"
            "C camera · Space pause · 1–5 time speed · R restart · Esc / F11 full screen<br>"
            "<b>Gliders</b>: L launch (winch / aerotow) and release · N engine (motor gliders: extend, start, "
            "stop, retract) · watch the variometer, "
            "circle (30–45° bank) in lift<br>"
            "<b>Game controller</b>: sticks fly, triggers throttle, A/B flaps/gear, Start pauses")

    def _show_motor(self, out: bool) -> None:
        """The pylon and propeller appear when the engine is extended."""
        if getattr(self, "_motor_out", None) == out:
            return
        self._motor_out = out
        for k, cid in enumerate(self.part_ids):
            if cid == "detail.motor" and self.parts[k][1] != "debris":
                self.parts[k][0].SetVisibility(out)

    def _update_motor_button(self) -> None:
        b = getattr(self, "motor_btn", None)
        if b is None:
            return
        nxt = {"retracted": "extend", "extending": "extending…", "extended": "start", "running": "stop",
               "stopped": "retract", "retracting": "retracting…"}[self.sim.motor_state]
        b.setText(f"Engine: {nxt}  (N)")
        b.setEnabled(self.sim.motor_state not in ("extending", "retracting"))

    def _toggle_help(self) -> None:
        lab = getattr(self, "_help_label", None)
        if lab is None:
            lab = self._help_label = QLabel(self.HELP, self.vtk_widget)
            lab.setWordWrap(True)
            lab.setStyleSheet("background: rgba(15,18,24,215); color: white; padding: 12px 16px; "
                              "border-radius: 10px; font-size: 13px;")
            lab.setFixedWidth(min(620, max(self.vtk_widget.width() - 40, 300)))
            lab.adjustSize()
            lab.move(20, 20)
            lab.hide()
        lab.setVisible(not lab.isVisible())
        lab.raise_()

    def _chosen_world_key(self) -> str:
        from .world import DETAILS, PROCEDURAL, SIZES, world_key
        area = self._areas[self.area_box.currentIndex()][0]
        if area == PROCEDURAL:
            return PROCEDURAL
        return world_key(area, SIZES[self.size_box.currentIndex()], DETAILS[self.detail_box.currentIndex()])

    def _update_world_button(self, *_):
        from .world import PROCEDURAL, world_folder
        key = self._chosen_world_key()
        here = key == getattr(self.terrain, "name", None)
        self.size_box.setEnabled(key != PROCEDURAL)
        self.detail_box.setEnabled(key != PROCEDURAL)
        if here:
            self.world_btn.setText("You are flying here")
            self.world_btn.setEnabled(False)
        elif key == PROCEDURAL or world_folder(key) is not None:
            self.world_btn.setText("Fly here")
            self.world_btn.setEnabled(True)
        else:
            self.world_btn.setText("Download this map and fly here")
            self.world_btn.setEnabled(True)

    def _fly_world(self):
        from .world import PROCEDURAL, download_world, world_folder
        key = self._chosen_world_key()
        if key != PROCEDURAL and world_folder(key) is None:
            from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
            import threading
            dlg = QProgressDialog(f"Downloading the {self.area_box.currentText().split(' —')[0]} map…", "Cancel",
                                  0, 100, self)
            dlg.setWindowTitle("Map download")
            dlg.setMinimumDuration(0)
            state = {"done": 0, "total": 0, "error": None, "finished": False}

            def work():
                try:
                    download_world(key, progress=lambda d, t: state.update(done=d, total=t))
                except Exception as exc:
                    state["error"] = str(exc)
                state["finished"] = True
            threading.Thread(target=work, daemon=True).start()
            while not state["finished"]:
                if state["total"]:
                    dlg.setMaximum(state["total"])
                    dlg.setValue(min(state["done"], state["total"]))
                    dlg.setLabelText(f"Downloading the map… {state['done'] / 1e6:.1f} of {state['total'] / 1e6:.1f} MB")
                QApplication.processEvents()
                if dlg.wasCanceled():
                    return
                import time as _t
                _t.sleep(0.05)
            dlg.close()
            if state["error"]:
                QMessageBox.warning(self, "Map download", state["error"])
                return
        from ..app.settings import settings as _settings
        _settings().setValue("sim/world", key)
        if self.receivers(self.world_requested) > 0:
            self.world_requested.emit(key)
        else:
            self.coach_view.setText("<b>Map ready</b> — reopen the flight simulator to fly there.")

    def engage_autopilot(self):
        program = self.ap_programs[self.ap_program.currentIndex()][0]
        dest = self.terrain.runways[self.ap_dest.currentIndex()]
        if program == "flight_plan" and not self.ap.plan:
            self.ap.status = "Add waypoints first (tick 'Click map to add' and click on the map)"
            return
        self.ap.vref = self.vref_for_mass()
        self.ap.pattern_height = float(self.ap_height.value())
        self.ap.reserve_fraction = self.ap_reserve.value() / 100.0
        start_rw = self.target_runway() if not self.sim.telemetry()["on_ground"] else self.start_runway
        self.ap.engage(program, runway=start_rw if program != "land" else dest, destination=dest)
        self.vtk_widget.setFocus()

    def add_waypoint(self, north: float, east: float):
        wp = Waypoint(f"WP{len(self.ap.plan) + 1}", north, east, float(self.wp_alt.value()), self.wp_spd.value() / 1.944)
        self.ap.plan.append(wp)
        self.wp_list.addItem(f"{wp.name}: {north / 1000:+.1f} km N, {east / 1000:+.1f} km E, {wp.altitude:.0f} m, "
                             f"{wp.speed * 1.944:.0f} kt")
        self.minimap.update()

    def _clear_waypoints(self):
        self.ap.plan.clear()
        self.wp_list.clear()
        self.minimap.update()

    def _apply_weather(self, *_):
        spd = self.wind_spd.value() / 1.944
        to = math.radians(self.wind_dir.value() + 180.0)  # wind FROM a direction blows TO the opposite one
        self.sim.wind_ned = np.array([spd * math.cos(to), spd * math.sin(to), 0.0])
        self.sim.turbulence = (0.0, 1.0, 2.5, 5.0)[self.turb_box.currentIndex()]

    def _record(self):
        t = self.sim.telemetry()
        self._log_t = self.sim.time
        self.log.append({"time_s": round(t["time"], 2), "north_m": round(t["north"], 1), "east_m": round(t["east"], 1),
                         "altitude_m": round(t["altitude"], 1), "agl_m": round(self.sim.height_above_ground(), 1),
                         "tas_kt": round(t["airspeed"] * 1.944, 1), "mach": round(t["mach"], 3),
                         "vs_ms": round(t["vs"], 2), "heading": round(t["heading"], 1), "pitch": round(t["pitch"], 2),
                         "roll": round(t["roll"], 2), "alpha": round(t["alpha"], 2), "g": round(t["load_factor"], 3),
                         "throttle": round(t["throttle"], 3), "fuel_kg": round(t["fuel_kg"], 1),
                         "fuel_flow_kgh": round(t["fuel_flow_kgh"], 1), "distance_km": round(t["distance_km"], 3),
                         "stress": round(t["stress"], 3), "autopilot": self.ap.mode})

    def export_log(self, path: str | None = None) -> str | None:
        import csv
        if not self.log:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export flight log", "flight_log.csv", "CSV (*.csv)")
            if not path:
                return None
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(self.log[0]))
            w.writeheader()
            w.writerows(self.log)
        return path

    def _break_off(self, M: np.ndarray):
        """Detach the failed parts and let them fall away."""
        f = self.sim.failure
        side = "left" if f == "left wing" else "right" if f == "right wing" else None
        rng = np.random.default_rng(4)
        v_ned = quat_to_dcm(self.sim.state[6:10]) @ self.sim.state[3:6]
        for k, cid in enumerate(self.part_ids):
            actor, kind, data = self.parts[k]
            gone = False
            if side and (cid.startswith(f"wing.{side}") or cid.startswith(f"attach.wing.{side}")):
                gone = True
            if side and cid.split(".")[0] in ("nacelle", "pylon", "propeller", "attach"):
                # engines mounted on the failed wing go with it
                e = next((e for e in self.arr.engines if cid.endswith(f".{e.index}")), None)
                gone = gone or (e is not None and e.mount == "wing" and (e.side < 0) == (side == "left"))
            if f == "tail" and cid.split(".")[0] in ("htail", "vtail", "canard"):
                gone = True
            if gone:
                self.parts[k] = (actor, "debris", data)
                self.debris.append({"actor": actor, "M": M.copy(), "v": NED_TO_ENU @ v_ned * 0.9 + rng.normal(0, 3, 3),
                                    "w": rng.normal(0, 1.5, 3), "kind": kind, "data": data, "resting": False})

    def _break_up(self, M: np.ndarray):
        """Crash: the parts separate with the aircraft's momentum, scaled by how hard the impact was."""
        self._broken_up = True
        ev = self.sim.event or {}
        speed = float(np.hypot(ev.get("ground_speed", 0.0), ev.get("sink_ms", 0.0)))
        rng = np.random.default_rng(7)
        v_ned = quat_to_dcm(self.sim.state[6:10]) @ self.sim.state[3:6]
        v_enu = NED_TO_ENU @ v_ned
        for k, cid in enumerate(self.part_ids):
            actor, kind, data = self.parts[k]
            if kind == "debris":
                continue
            minor = kind in ("gear", "prop")
            if speed < 25 and not minor:
                continue  # a gentle crash (gear collapse / prop strike): the airframe stays together
            self.parts[k] = (actor, "debris", data)
            scatter = 0.25 * speed + 2.0
            v0 = v_enu * rng.uniform(0.55, 0.95) + rng.normal(0, scatter, 3)
            v0[2] = abs(v0[2]) * 0.4 + rng.uniform(0, 0.15 * speed)
            self.debris.append({"actor": actor, "M": M.copy(), "v": v0, "w": rng.normal(0, 0.05 * speed + 0.5, 3),
                                "kind": kind, "data": data, "resting": False})

    def _update_debris(self, dt: float = 1 / 60):
        """Debris tumbles under gravity, bounces off the terrain losing energy, and slides to a stop."""
        for d in self.debris:
            if d.get("resting"):
                continue
            d["v"][2] -= G0 * dt
            d["M"][:3, 3] += d["v"] * dt
            ground = self.sim.ground(d["M"][1, 3], d["M"][0, 3])
            if d["M"][2, 3] < ground + 0.3:
                d["M"][2, 3] = ground + 0.3
                if d["v"][2] < 0:
                    d["v"][2] = -0.3 * d["v"][2]  # bounce
                    d["v"][:2] *= 0.7  # scrape
                    d["w"] *= 0.6
                d["v"][:2] *= max(0.0, 1.0 - 2.5 * dt)  # sliding friction
                if np.linalg.norm(d["v"]) < 0.5 and np.linalg.norm(d["w"]) < 0.3:
                    d["resting"] = True
            ang = np.linalg.norm(d["w"]) * dt
            if ang > 0:
                c = d["M"][:3, 3].copy()
                R = rotation_about(np.zeros(3), d["w"], ang)[:3, :3]
                d["M"][:3, :3] = R @ d["M"][:3, :3]
                d["M"][:3, 3] = c
            d["actor"].SetUserMatrix(matrix_to_vtk(d["M"]))

    def _update_debris_unused(self, dt: float = 1 / 60):
        for d in self.debris:
            if d["M"][2, 3] < self.sim.ground(d["M"][1, 3], d["M"][0, 3]) - 2:
                continue
            d["v"][2] -= G0 * dt
            d["v"] *= 0.995
            d["M"][:3, 3] += d["v"] * dt
            ang = np.linalg.norm(d["w"]) * dt
            if ang > 0:
                c = d["M"][:3, 3].copy()
                R = rotation_about(np.zeros(3), d["w"], ang)[:3, :3]
                d["M"][:3, :3] = R @ d["M"][:3, :3]
                d["M"][:3, 3] = c
            d["actor"].SetUserMatrix(matrix_to_vtk(d["M"]))

    def _restore_debris(self):
        for k, (actor, kind, data) in enumerate(self.parts):
            if kind == "debris":
                orig = next((dd["kind"] for dd in self.debris if dd["actor"] is actor), "static")
                self.parts[k] = (actor, orig, data)
        self.debris = []

    def _update_papi(self, pos):
        for rw, acts, p0 in self.papis:
            dx = np.hypot(pos[0] - p0[0], pos[1] - p0[1])
            if dx > 25000:
                continue
            angle = math.degrees(math.atan2(pos[2] - p0[2] - 1.0, max(dx, 1.0)))
            for light, a in zip(acts, self.papi_angles):
                light.GetProperty().SetColor(*((1.0, 1.0, 1.0) if angle > a else (1.0, 0.1, 0.1)))

    def target_runway(self):
        """The autopilot's destination, otherwise the runway nearest the aircraft."""
        if self.ap.engaged:
            return self.ap.destination
        n, e = self.sim.state[0], self.sim.state[1]

        def dist(rw):
            a, r = rw.local(n, e)
            return abs(r) + max(0.0, -a, a - rw.length)
        return min(self.terrain.runways, key=dist)

    def approach_info(self) -> dict:
        """Distance to the aiming point, glide-slope and centreline deviation for the target runway."""
        rw = self.target_runway()
        n, e = self.sim.state[0], self.sim.state[1]
        alt = -self.sim.state[2]
        along, right = rw.local(n, e)
        dist = rw.aim_point - along
        gs_height = rw.elevation + math.tan(3.0 * D2R) * max(dist, 0.0)
        return {"runway": rw, "dist_m": dist, "east_m": right, "right_m": right, "gs_dev_m": alt - gs_height,
                "gs_height_m": gs_height}

    def vref(self) -> float:
        """Target approach speed: 1.23 x stall speed in the landing configuration."""
        md = self.model
        cl = md.CLmax + md.flap_dCL  # landing flaps
        return 1.23 * math.sqrt(2 * md.mass * G0 / (RHO0 * md.S * cl))

    def _place_camera(self, pos, M):
        cam = self.renderer.GetActiveCamera()
        fwd = M[:3, :3] @ np.array([-1.0, 0, 0])  # design -x is forward
        up = np.array([0, 0, 1.0])
        L = max(self.params.fuselage.length, self.params.wing.span)
        if self.camera_mode == "chase":
            flat = np.array([fwd[0], fwd[1], 0.0])
            flat /= max(np.linalg.norm(flat), 1e-6)
            eye = pos - flat * 1.7 * L + up * 0.55 * L
            focal = pos + flat * 1.5 * L  # look ahead of the aircraft so the runway is in view on final
        elif self.camera_mode == "side":
            side = np.cross(up, fwd)
            side /= max(np.linalg.norm(side), 1e-6)
            eye, focal = pos + side * 1.8 * L + up * 0.2 * L, pos
        elif self.camera_mode == "tower":
            eye, focal = np.array([110.0, 600.0, 30.0]), pos
        else:  # cockpit: the pilot's eye, under the canopy / on the flight deck
            fp = self.params.fuselage
            L_, D_ = fp.length, fp.diameter
            nose_len = fp.nose_fraction * L_
            x_eye = nose_len * 0.9 + 0.45 if D_ < 2.0 else 0.75 * nose_len
            if getattr(self, "_arr", None) is None:
                from ..geometry.arrangement import Arrangement
                self._arr = Arrangement(self.params)
            arr = self._arr
            cz = float(arr.fuselage.centre_z(np.array([x_eye]))[0]) if arr is not None else 0.0
            hh = float(arr.fuselage.half_height(np.array([x_eye]))[0]) if arr is not None else D_ / 2
            head = M[:3, :3] @ np.array([x_eye, 0, cz + 0.45 * hh]) + M[:3, 3]
            eye, focal = head, head + fwd * 100
            up = M[:3, :3] @ np.array([0, 0, 1.0])
        eye = np.array(eye, float)
        low = 0.6 if self.camera_mode == "cockpit" else 3.0  # the pilot's eye may be close to the ground
        eye[2] = max(eye[2], self.sim.ground(eye[1], eye[0]) + low)  # never put the camera under the hills
        self._cockpit_visibility(self.camera_mode == "cockpit")
        cam.SetPosition(*eye)
        cam.SetFocalPoint(*focal)
        cam.SetViewUp(*up)
        cam.SetViewAngle(50 if self.camera_mode == "cockpit" else 35)
        cam.SetClippingRange(0.5, 150000)

    def _cockpit_visibility(self, inside: bool) -> None:
        """From the seat you look out through the canopy: hide the own fuselage and glazing, show the wings."""
        if getattr(self, "_inside", None) == inside:
            return
        self._inside = inside
        for k, cid in enumerate(self.part_ids):
            actor = self.parts[k][0]
            if cid.startswith(("fuselage", "detail")) and cid != "detail.motor" and self.parts[k][1] != "debris":
                actor.SetVisibility(not inside)
        self.hud.SetVisibility(not inside)
        if inside and getattr(self, "panel", None) is None:
            # below the 3-D view in the layout, like a real panel under the windscreen
            # (never painted on top of the OpenGL view)
            from .cockpit import InstrumentPanel
            self.panel = InstrumentPanel()
            self._view_box.layout().addWidget(self.panel)
        if getattr(self, "panel", None) is not None:
            self.panel.setFixedHeight(max(int(self._view_box.height() * 0.24), 150) + 34)
            self.panel.setVisible(inside)

    def _update_hud(self):
        warn: list = []  # filled below when there is something to warn about
        t = self.sim.telemetry()
        c = self.sim.controls
        if self.sim.time - self._boom_t > 0.5:
            self._boom_t = self.sim.time
            try:
                self._boom = self.sim.ground_boom_pldb()
            except Exception:
                self._boom = (0.0, 0.0)
        boom, cutoff = self._boom
        if t["mach"] > 1.0:
            boom_txt = (f"BOOM ON GROUND {boom:5.1f} PLdB" if boom > 0
                        else f"NO BOOM ON GROUND (below Mach cutoff {cutoff:.2f})")
        else:
            boom_txt = f"subsonic - no boom   (Mach cutoff here {cutoff:.2f})"
        fl = self.sim.controls.flaps  # actual setting (the autopilot moves flaps too)
        flap_txt = "UP" if fl < 0.2 else ("TO" if fl < 0.7 else "LAND")
        agl = self.sim.height_above_ground()
        ap = self.approach_info()
        lines = [
            f"TAS     {t['airspeed'] * 1.944:6.0f} kt   MACH {t['mach']:5.2f}",
            f"ALT     {t['altitude'] * 3.281:7.0f} ft   V/S {t['vs'] * 196.9:+6.0f} fpm",
            f"HDG     {t['heading']:5.0f}      PITCH {t['pitch']:+5.1f}  ROLL {t['roll']:+6.1f}",
            f"AOA     {t['alpha']:+5.1f} deg  BETA {t['beta']:+5.1f}   G {t['load_factor']:4.2f}",
            f"THR {c.throttle * 100:4.0f}%{' REHEAT' if t['reheat'] else '       '}  FLAPS {flap_txt:4s} "
            f"GEAR {'DN' if c.gear_down else 'UP'}  BRK {'ON' if c.brakes else 'off'}",
            f"TRIM {c.pitch_trim:+.2f}  SAS {'ON' if c.sas else 'OFF'}  TIME {t['time']:6.1f}s",
            f"RADALT  {agl * 3.281:7.0f} ft   VREF {self.vref() * 1.944:4.0f} kt",
            boom_txt,
        ]
        rw = ap["runway"]
        facing = abs((t["heading"] - rw.heading_deg + 180) % 360 - 180) < 60
        if 0 < ap["dist_m"] < 20000 and abs(ap["right_m"]) < 4000 and not t["on_ground"] and facing:
            gs = ap["gs_dev_m"]
            r = ap["right_m"]
            lines.append(f"{rw.code} {rw.heading_deg:03.0f}  {ap['dist_m'] / 1852:4.1f} nm  G/S "
                         f"{'HIGH' if gs > 15 else 'LOW ' if gs < -15 else 'ON  '}{gs:+5.0f} m  LOC {r:+5.0f} m "
                         f"{'(fly left)' if r > 10 else '(fly right)' if r < -10 else ''}")
        endurance = f"{t['fuel_kg'] / t['fuel_flow_kgh'] * 60:5.0f}" if t["fuel_flow_kgh"] > 5 else "    —"
        if self.model.motor_kw > 0 and t.get("motor", "retracted") != "retracted":
            lines.append(f"ENGINE {t['motor'].upper():11s} FUEL {t['motor_fuel'] / 0.72:4.1f} l"
                         + ("   (extended, not running: costs height - start it or retract it)"
                            if t["motor"] in ("extended", "stopped") else ""))
        if self.model.motor_kw > 0:
            self._update_motor_button()
            self._show_motor(t.get("motor", "retracted") != "retracted")
        if self.model.glider:
            self._vario = (self._vario + [t["vs"]])[-300:]
            avg = sum(self._vario) / len(self._vario)
            lines.append(f"VARIO {t['vs']:+5.1f} m/s   AVERAGE {avg:+5.1f} m/s   "
                         f"{'▲ LIFT' if t['vs'] > 0.3 else ('▼ SINK' if t['vs'] < -1.5 else '')}"
                         + (f"   {t['launch'].upper()} LAUNCH" if t["launch"] else ""))
        if not self.model.glider:
            lines.append(f"FUEL {t['fuel_kg']:7.0f} kg  FLOW {t['fuel_flow_kgh']:6.0f} kg/h  "
                     f"ENDUR {endurance} min  "
                         f"DIST {t['distance_km']:6.1f} km")
        if not t["on_ground"]:
            self.g_max, self.g_min = max(self.g_max, t["load_factor"]), min(self.g_min, t["load_factor"])
        lines.append(f"G  now {t['load_factor']:+5.1f}  max {self.g_max:+5.1f}  min {self.g_min:+5.1f}  "
                     f"(limits {self.model.n_limit:+.1f} / {-self.model.n_negative_limit:+.1f})  "
                     f"ROLL RATE {math.degrees(self.sim.state[10]):+5.0f}°/s")
        stress = t["stress"]
        bar = "#" * int(min(stress, 1.2) * 20)
        lines.append(f"AIRFRAME STRESS {stress * 100:4.0f}% [{bar:<24s}]")
        if self.ap.engaged:
            lines.append(f"A/P {self.ap.status[:70]}")
        self.hud.SetInput("\n".join(lines))
        if getattr(self, "_inside", False) and getattr(self, "panel", None) is not None:
            avg = sum(self._vario) / len(self._vario) if self._vario else 0.0
            self.panel.set_state({**t, "time": self.sim.time}, self.model.glider, avg, list(warn))
        warn = []
        if self.sim.failure_msg:
            warn.append(self.sim.failure_msg)
        if self.sim.crashed:
            warn.append(self.sim.crashed + "  —  press R")
        elif self.paused:
            warn.append("PAUSED")
        else:
            if t["stalled"] and not t["on_ground"] and t["airspeed"] > 15:  # no "stall" while parked
                warn.append("STALL")
            if self.model.glider and t.get("launch") == "winch" and t.get("launch_v_target") \
                    and t["airspeed"] < 0.88 * t["launch_v_target"] and not t["on_ground"]:
                warn.append("LOW SPEED – LOWER THE NOSE")
            if not t["on_ground"] and self.sim.height_above_ground() < 150 and t["vs"] < -8:
                warn.append("SINK RATE — PULL UP")
            if self.sim.stress["q"] > 0.64:
                warn.append("OVERSPEED")
            if not self.sim.failure and t["stress"] > 1 / max(self.model.n_ultimate / self.model.n_limit, 1.0):
                warn.append(f"OVERSTRESS — {t['load_factor']:.1f} g (limit {self.model.n_limit:.1f} g)")
            if t["fuel_kg"] <= 0 and not self.model.glider:
                warn.append("FUEL EXHAUSTED — ENGINES OUT")
            elif t["fuel_kg"] < 0.1 * self.model.fuel_capacity_kg and not self.model.glider:
                warn.append("LOW FUEL")
            agl = self.sim.height_above_ground()
            if not t["on_ground"] and agl < 300 and t["vs"] < -2 and not c.gear_down:
                warn.append("TOO LOW — GEAR")
            if not t["on_ground"] and agl < 600 and t["vs"] < -0.25 * max(agl, 1) and agl > 30:
                warn.append("TERRAIN — PULL UP")
        if self.sim.landing:
            L = self.sim.landing
            warn.append(f"{L['verdict']} ({L['feel']}) — score {L['score']:.0f}/100")
        self.warn.SetInput("\n".join(warn))

    def _show_modes(self):
        try:
            modes = dynamic_modes(self.sim, self.cruise_speed, self.cruise_alt)
        except Exception as exc:  # pragma: no cover - degenerate designs
            self.modes_view.setHtml(f"<i>Could not linearise: {exc}</i>")
            return
        rows = ""
        for mo in modes:
            per = f"{mo.period_s:.1f} s" if mo.period_s else "—"
            tc = f"{mo.time_const_s:.2f} s" if mo.time_const_s else "—"
            colour = "#4caf50" if mo.ok else "#e0533d"
            rows += (f"<tr><td style='color:{colour}'>●</td><td>{mo.name}</td><td>{mo.omega_n:.3f}</td>"
                     f"<td>{mo.zeta:.2f}</td><td>{per}</td><td>{tc}</td></tr>"
                     f"<tr><td></td><td colspan=5 style='color:gray'>{mo.requirement}</td></tr>")
        note = ""
        if self.model.tailless or not self.model.has_fins:
            note = ("<p>Tailless / finless aircraft rely on stability augmentation (as the B-2 does). "
                    "Try switching it off with <b>A</b>.</p>")
        self.modes_view.setHtml("<table cellspacing=4><tr><th></th><th>Mode</th><th>ω<sub>n</sub> rad/s</th>"
                                "<th>ζ</th><th>Period</th><th>Time const.</th></tr>" + rows + "</table>" + note)

    def closeEvent(self, ev):
        self.timer.stop()
        self.vtk_widget.Finalize()
        super().closeEvent(ev)


class MiniMap(QWidget):
    """Moving map: terrain, all airports, the flight plan, track and aircraft; north up.

    Mouse wheel zooms; with "Click map to add" ticked, a click adds a waypoint.
    """

    def __init__(self, win: "SimulatorWindow"):
        super().__init__()
        self.win = win
        self.range = 12000.0
        self.setMinimumHeight(250)
        north, east, _ = win.terrain.grid
        col = win.terrain.colours()
        img = np.ascontiguousarray(col[::-1, :, :])  # row 0 = north edge
        self._img = QImage(img.data, img.shape[1], img.shape[0], 3 * img.shape[1], QImage.Format_RGB888).copy()
        self._extent = (north[0], north[-1], east[0], east[-1])

    def _scale(self):
        return min(self.width(), self.height()) / (2 * self.range)

    def _to_px(self, n, e):
        s = self.win.sim.state
        sc = self._scale()
        return QPointF(self.width() / 2 + (e - s[1]) * sc, self.height() / 2 - (n - s[0]) * sc)

    def _to_world(self, x, y):
        s = self.win.sim.state
        sc = self._scale()
        return s[0] - (y - self.height() / 2) / sc, s[1] + (x - self.width() / 2) / sc

    def wheelEvent(self, ev):
        self.range = float(np.clip(self.range * (0.8 if ev.angleDelta().y() > 0 else 1.25), 4000, 70000))
        self.update()

    def mousePressEvent(self, ev):
        if self.win.wp_add_mode:
            n, e = self._to_world(ev.position().x(), ev.position().y())
            self.win.add_waypoint(n, e)

    def paintEvent(self, _):
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n0, n1, e0, e1 = self._extent
        p.fillRect(self.rect(), QColor(48, 82, 125))
        p.drawImage(QRectF(self._to_px(n1, e0), self._to_px(n0, e1)), self._img)
        for rw in self.win.terrain.runways:
            a, b = self._to_px(*rw.point(0)), self._to_px(*rw.point(rw.length))
            p.setPen(QPen(QColor(20, 20, 20), 4))
            p.drawLine(a, b)
            p.setPen(QPen(QColor(245, 245, 245), 2))
            p.drawLine(a, b)
            p.setPen(QColor(255, 255, 255))
            p.drawText(b + QPointF(5, -5), rw.code)
        ap = self.win.ap
        if ap.plan:
            p.setPen(QPen(QColor(255, 80, 220), 2, Qt.DashLine))
            pts = [QPointF(self._to_px(self.win.sim.state[0], self.win.sim.state[1]))] + \
                [self._to_px(wp.north, wp.east) for wp in ap.plan]
            if ap.engaged:
                fix = ap.approach_fix(ap.destination)
                pts.append(self._to_px(fix.north, fix.east))
                pts.append(self._to_px(*ap.destination.point(0)))
            for q0, q1 in zip(pts[:-1], pts[1:]):
                p.drawLine(q0, q1)
            p.setBrush(QBrush(QColor(255, 80, 220)))
            for k, wp in enumerate(ap.plan):
                c = self._to_px(wp.north, wp.east)
                p.drawEllipse(c, 4, 4)
                p.drawText(c + QPointF(6, 4), wp.name)
        if getattr(self.win, "show_thermals", None) is not None and self.win.show_thermals.isChecked():
            drift = self.win.sim.wind_ned[:2] * self.win.sim.time
            for tn, te, r, core, top in self.win.sim.thermals:
                c_ = self._to_px(tn + drift[0], te + drift[1])
                rr = max(r * self._scale(), 3)
                p.setPen(QPen(QColor(60, 200, 90, 220), 1.5))
                p.setBrush(QBrush(QColor(60, 200, 90, int(40 + 25 * core))))
                p.drawEllipse(c_, rr, rr)
        if len(self.win.trail) > 1:
            p.setPen(QPen(QColor(255, 220, 60), 1.5))
            pts = [self._to_px(n, e) for n, e in self.win.trail]
            for q0, q1 in zip(pts[:-1], pts[1:]):
                p.drawLine(q0, q1)
        hdg = math.radians(self.win.sim.telemetry()["heading"])
        c = QPointF(w / 2, h / 2)
        tri = [QPointF(c.x() + 9 * math.sin(hdg), c.y() - 9 * math.cos(hdg)),
               QPointF(c.x() + 6 * math.sin(hdg + 2.5), c.y() - 6 * math.cos(hdg + 2.5)),
               QPointF(c.x() + 6 * math.sin(hdg - 2.5), c.y() - 6 * math.cos(hdg - 2.5))]
        p.setPen(QPen(QColor(0, 0, 0), 1))
        p.setBrush(QBrush(QColor(255, 60, 60)))
        p.drawPolygon(QPolygonF(tri))
        p.setPen(QColor(255, 255, 255))
        hint = "  · click to add waypoints" if self.win.wp_add_mode else "  · wheel to zoom"
        p.drawText(6, 14, f"N ↑   range {self.range / 1000:.0f} km{hint}")
        p.end()


class CircuitCoach:
    """Guides a manual take-off, circuit and landing on any runway, phase by phase."""

    PHASES = ("Take-off", "Climb-out", "Downwind", "Approach", "Final", "Rollout", "Done")

    def __init__(self, win: "SimulatorWindow"):
        self.win = win
        self.phase = 0

    def reset(self, airborne_start: bool = False):
        self.phase = 2 if airborne_start else 0

    @staticmethod
    def _glider_tip(sim, t, agl) -> str:
        """Glider guidance: launch, the winch climb, soaring, circuit and landing."""
        kmh = t["airspeed"] * 3.6
        if t["on_ground"] and sim.launch is None and kmh < 20:
            return ("<b>Glider — ready to launch</b><br>Choose winch or aerotow under <b>Glider</b>, then press "
                    "<b>L</b>. A wing runner holds the wings level for the first few seconds.")
        if sim.launch is not None and sim.launch["kind"] == "winch":
            vt = t.get("launch_v_target", 0) * 3.6
            return ("<b>Winch launch</b><br>Lift off gently, then rotate progressively to about 40–45° once "
                    f"clear of the ground. Keep about <b>{vt:.0f} km/h</b>: if slow, lower the nose. Near the top "
                    "ease the nose down; the hook back-releases (or press <b>L</b>).")
        if sim.launch is not None:
            return ("<b>Aerotow</b><br>Stay behind and slightly above the tug's wake; the tow releases at "
                    "~600 m (or press <b>L</b>).")
        if agl > 300:
            return ("<b>Soaring</b><br>Fly at best-glide speed between thermals; when the vario shows lift, "
                    "circle at 30–45° bank and centre on the strongest part. Keep within gliding range of an "
                    "airfield. <b>F1</b> lists the controls.")
        return ("<b>Circuit and landing</b><br>Join the circuit by ~300 m, approach at about 90–100 km/h, use "
                "the airbrakes (<b>F</b>) to control the approach angle, round out and hold off just above the "
                "ground.")

    def update(self):
        w = self.win
        sim = w.sim
        t = sim.telemetry()
        agl = sim.height_above_ground()
        if w.model.glider and not (sim.failure_msg or sim.crashed):
            w.coach_view.setText(self._glider_tip(sim, t, agl))
            return
        ap = w.approach_info()
        rw = ap["runway"]
        vr = 1.1 * w.vref() / 1.23 * 1.15
        rel = (t["heading"] - rw.heading_deg + 180) % 360 - 180  # heading relative to the runway
        down_hdg = (rw.heading_deg + 180) % 360
        if sim.failure_msg or sim.crashed:
            from .debrief import debrief, debrief_html
            if sim.event is not None:
                if getattr(self, "_debrief_for", None) is not sim.event:
                    self._debrief_for = sim.event
                    self._debrief_html = debrief_html(debrief(sim.event, w.model, w.params, w.vref()))
                w.coach_view.setText(self._debrief_html)
            else:
                w.coach_view.setText(f"<b style='color:#e0533d'>{sim.failure_msg or sim.crashed}</b><br>"
                                     "Press <b>R</b> to try again.")
            return
        if w.ap.engaged:
            w.coach_view.setText(f"<b>Autopilot flying</b><br>{w.ap.status}<br>"
                                 "<span style='color:gray'>Any flight control key takes over.</span>")
            return
        if self.phase == 0 and not t["on_ground"] and agl > 15:
            self.phase = 1
        if self.phase == 1 and agl > 350:
            self.phase = 2
        if self.phase == 2 and abs(rel) > 120:
            self.phase = 3
        if self.phase in (2, 3) and 1500 < ap["dist_m"] < 16000 and abs(ap["right_m"]) < 900 and abs(rel) < 30:
            self.phase = 4
        if self.phase == 4 and t["on_ground"]:
            self.phase = 5
        if self.phase == 5 and sim.landing:
            self.phase = 6
        steps = {
            0: f"Brakes off (<b>B</b>), full throttle (hold <b>W</b>). Keep straight with <b>Q/E</b>. "
               f"At <b>{vr * 1.944:.0f} kt</b> pull back (hold <b>↓</b>) to about 8–10° nose-up.",
            1: "Climb at about 10° pitch. Gear up (<b>G</b>) once climbing; flaps up (<b>F</b> twice) "
               "above ~1000 ft. Level off around <b>1500 ft</b> (RADALT) with a bit less throttle.",
            2: f"Fly the circuit: turn <b>right</b> through 90° then onto <b>{down_hdg:03.0f}</b> (downwind), "
               f"~3–4 km to the side of {rw.code}, 1500 ft above the field. The map shows the runway.",
            3: f"Past the runway, reduce power and descend slowly. ~10 km out, turn <b>right</b> onto final "
               f"heading <b>{rw.heading_deg:03.0f}</b>. Gear down (<b>G</b>), landing flaps (<b>F</b>).",
            4: f"Final: line up (LOC 0), fly the 3° glide path — PAPI <b>two white, two red</b>. Hold "
               f"<b>{w.vref() * 1.944:.0f} kt</b> (VREF) with throttle. At ~30 ft pull gently to flare, close "
               f"the throttle, touch down on the markers.",
            5: "On the ground: throttle idle (<b>S</b>), brakes (<b>B</b>), keep straight (<b>Q/E</b>) until stopped.",
        }
        rows = "".join(
            f"<span style='color:{'#4caf50' if i < self.phase else '#e8a33d' if i == self.phase else 'gray'}'>"
            f"{'✔' if i < self.phase else '➜' if i == self.phase else '○'} {name}</span>&nbsp;&nbsp;"
            for i, name in enumerate(self.PHASES[:-1]))
        if self.phase == 6 and sim.landing:
            L = sim.landing
            body = (f"<b>{L['verdict']} on {L['runway']} — {L['feel']} touchdown, score {L['score']:.0f}/100</b><br>"
                    f"sink {L['sink_ms']:.1f} m/s · {L['touchdown_from_threshold_m']:.0f} m past the threshold "
                    f"(ideal 300–600) · {L['centreline_offset_m']:+.1f} m off centre · stopped after "
                    f"{L['stopped_after_m']:.0f} m.<br>Press <b>R</b> to fly again.")
        else:
            body = steps[self.phase]
        w.coach_view.setText(f"<b>Circuit ({rw.code})</b><br>{rows}<br><br>{body}")


def oriented_box(rw, a0: float, a1: float, r0: float, r1: float, z0: float, z1: float) -> Mesh:
    """Box aligned with a runway (along a0..a1, right r0..r1, height z0..z1) in ENU coordinates."""
    corners = [rw.point(a0, r0), rw.point(a1, r0), rw.point(a1, r1), rw.point(a0, r1)]
    base = [[e, n] for n, e in corners]
    v = np.array([[x, y, z0] for x, y in base] + [[x, y, z1] for x, y in base])
    f = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                  [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    m = Mesh(v, f)
    if m.signed_volume() < 0:
        m = Mesh(v, f[:, ::-1])
    return m
