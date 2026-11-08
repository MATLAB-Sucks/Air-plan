"""Design studio: build an aircraft by dragging parts and handles, see if it flies, then fly it.

* **Parts palette** - pick a layout (conventional, canard, flying wing,
  blended wing body, twin boom, supersonic delta), a tail (conventional,
  T-tail, V-tail, twin fins), engines (under the wing, rear fuselage, on top,
  pusher; 2/3/4; jets or propellers) and the wing position (low/mid/high).
  Choosing a part re-sizes the design around it, keeping it consistent.
* **Top and side views** with drag handles:
  wings, tails and canards - move (◆), stretch the span and sweep the tip (●
  at the tip leading edge), root chord (■ at the root trailing edge) and taper
  (■ at the tip trailing edge); fuselage length and width; engines along the
  span; ailerons (inner / outer edge, chord), flaps (span, chord), elevator
  and rudder chord; fin height and sweep in the side view.
* **Live 3-D preview** and a **"Will it fly?"** verdict that updates as you
  drag, separating "can it fly at all" from "does it meet the mission".
* **Fly it**, **Wind tunnel**, **Add to project**.

Every handle edits a real design parameter (clamped to its valid range), so
whatever you build is always a consistent aircraft the evaluator, structures,
flight model and wind tunnel can analyse.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFormLayout, QGraphicsEllipseItem, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ..core import parameters as P
from ..core.design import AircraftDesign
from ..core.mission import MissionRequirements
from ..core.parameters import AircraftParameters
from ..geometry.arrangement import Arrangement

SPECS = dict(P.parameter_specs() if callable(P.parameter_specs) else P.parameter_specs)

LAYOUTS = [("Conventional", "conventional"), ("Canard", "canard"), ("Flying wing", "flying_wing"),
           ("Blended wing body", "blended_wing_body"), ("Twin boom", "twin_boom"),
           ("Supersonic delta", "supersonic_delta")]


def clamp(name: str, value: float) -> float:
    spec = SPECS.get(name, {})
    lo = spec.get("lo", spec.get("min", -math.inf))
    hi = spec.get("hi", spec.get("max", math.inf))
    return float(np.clip(value, lo, hi))


def options(name: str) -> tuple:
    return tuple(SPECS.get(name, {}).get("options", ()))


class Handle(QGraphicsEllipseItem):
    """A draggable handle; ``on_drag(scene_pos) -> constrained scene_pos`` edits the parameters."""

    def __init__(self, pos: QPointF, on_drag, on_release, shape: str = "circle", colour=QColor(255, 140, 0),
                 tip: str = ""):
        r = 5.0
        super().__init__(-r, -r, 2 * r, 2 * r)
        self.setPos(pos)
        self.on_drag, self.on_release = on_drag, on_release
        self.setBrush(QBrush(colour))
        self.setPen(QPen(QColor(30, 30, 30), 1))
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges
                      | QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(10)
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip(tip)
        self.shape_kind = shape
        self._busy = False

    def paint(self, painter, option, widget=None):
        painter.setBrush(self.brush())
        painter.setPen(self.pen())
        if self.shape_kind == "square":
            painter.drawRect(self.rect())
        elif self.shape_kind == "diamond":
            r = self.rect()
            painter.drawPolygon(QPolygonF([QPointF(r.center().x(), r.top()), QPointF(r.right(), r.center().y()),
                                           QPointF(r.center().x(), r.bottom()), QPointF(r.left(), r.center().y())]))
        else:
            painter.drawEllipse(self.rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not self._busy and self.scene() is not None:
            self._busy = True
            try:
                return self.on_drag(value)
            except Exception:
                return self.pos()  # an edit the design can't take: the handle simply doesn't move
            finally:
                self._busy = False
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)
        self.on_release()


class EditorView(QGraphicsView):
    def __init__(self, title: str):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(self.renderHints().__class__.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor(26, 30, 38)))
        self.title = title
        self._fitted = False

    def fit(self, rect: QRectF):
        self.fitInView(rect.adjusted(-0.08 * rect.width(), -0.15 * rect.height(), 0.08 * rect.width(),
                                     0.15 * rect.height()), Qt.KeepAspectRatio)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.scene().itemsBoundingRect().isValid():
            self.fit(self.scene().itemsBoundingRect())


class DesignStudioWindow(QMainWindow):
    send_to_project = Signal(object)  # AircraftParameters

    def __init__(self, params: AircraftParameters, mission: MissionRequirements, parent=None, name: str = "Studio design"):
        super().__init__(parent)
        self.setWindowTitle("Design studio — drag the parts, see if it flies")
        self.resize(1500, 920)
        self.mission = mission
        self.params = params.copy()
        self.name = name
        self.design = AircraftDesign(self.params.copy(), name)
        self.verdict = None
        self._dragging = False

        from .viewport import Viewport
        self.top = EditorView("Top view")
        self.side = EditorView("Side view")
        self.viewport = Viewport(self)
        editors = QSplitter(Qt.Vertical)
        for v, label in ((self.top, "Top view — drag ◆ to move, ● tip, ■ chords; orange = control surfaces"),
                         (self.side, "Side view — fuselage, fin, wing height")):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.addWidget(QLabel(label))
            bl.addWidget(v)
            editors.addWidget(box)
        editors.setSizes([620, 260])
        centre = QSplitter(Qt.Horizontal)
        centre.addWidget(editors)
        centre.addWidget(self.viewport)
        centre.setSizes([820, 520])
        self.setCentralWidget(centre)
        self._build_palette()
        self._build_verdict_panel()

        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self.refresh_analysis)
        self.rebuild_editors(fit=True)
        self.refresh_analysis()

    # ------------------------------------------------------------ palette
    def _build_palette(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)

        def group(title, items, handler):
            box = QGroupBox(title)
            gl = QVBoxLayout(box)
            for label, value in items:
                b = QPushButton(label)
                b.clicked.connect(lambda _=False, v=value: handler(v))
                gl.addWidget(b)
            lay.addWidget(box)
        group("Layout", LAYOUTS, self.set_layout)
        tails = [(t.replace("_", " ").title().replace("T Tail", "T-tail").replace("V Tail", "V-tail"), t)
                 for t in options("configuration.tail_type") if t != "none"]
        group("Tail", tails, lambda v: self.set_config("tail_type", v))
        engines = [(e.replace("_", " ").capitalize(), e) for e in options("configuration.engine_layout")]
        group("Engine position", engines, lambda v: self.set_config("engine_layout", v))
        form = QFormLayout()
        self.count = QComboBox()
        self.count.addItems(["1", "2", "3", "4"])
        self.count.setCurrentText(str(self.params.engines.count))
        self.count.currentTextChanged.connect(lambda t: self.set_value("engines.count", int(t), resize=True))
        self.prop = QComboBox()
        self.prop.addItems(list(options("configuration.propulsion")))
        self.prop.setCurrentText(self.params.configuration.propulsion)
        self.prop.currentTextChanged.connect(lambda t: self.set_config("propulsion", t))
        self.wpos = QComboBox()
        self.wpos.addItems(list(options("configuration.wing_position")))
        self.wpos.setCurrentText(self.params.configuration.wing_position)
        self.wpos.currentTextChanged.connect(lambda t: self.set_config("wing_position", t))
        form.addRow("Engines", self.count)
        form.addRow("Engine type", self.prop)
        form.addRow("Wing height", self.wpos)
        lay.addLayout(form)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        dock = QDockWidget("Parts", self)
        dock.setWidget(scroll)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_verdict_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)
        self.verdict_label = QLabel()
        self.verdict_label.setTextFormat(Qt.RichText)
        self.verdict_label.setWordWrap(True)
        self.verdict_label.setAlignment(Qt.AlignTop)
        lay.addWidget(self.verdict_label, 1)
        row = QHBoxLayout()
        self.fly_btn = QPushButton("✈ Fly it")
        self.wt_btn = QPushButton("🌬 Wind tunnel")
        self.add_btn = QPushButton("Add to project")
        self.fly_btn.clicked.connect(self.fly)
        self.wt_btn.clicked.connect(self.wind_tunnel)
        self.add_btn.clicked.connect(lambda: self.send_to_project.emit(self.params.copy()))
        for b in (self.fly_btn, self.wt_btn, self.add_btn):
            row.addWidget(b)
        lay.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        dock = QDockWidget("Will it fly?", self)
        dock.setWidget(scroll)
        dock.setMinimumWidth(380)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    # ------------------------------------------------------------ parameter edits
    def set_value(self, name: str, value, resize: bool = False, rebuild: bool = True):
        try:
            v = value if isinstance(value, str) or name == "engines.count" else clamp(name, value)
            self.params.set(name, v)
        except Exception:
            return
        if resize:
            self._resize_around()
        if rebuild:
            self.rebuild_editors(fit=resize)
        self.schedule_analysis()

    def _resize_around(self):
        """After a part change: re-size tails and finish the design (gear, structure...) consistently."""
        from ..core import synthesis as S
        try:
            S._size_tails(self.params, self.mission)
            S.complete_design(self.params, self.mission)
        except Exception:
            pass

    def set_config(self, key: str, value: str):
        self.set_value(f"configuration.{key}", value, resize=True)

    def set_layout(self, layout: str):
        """A new layout re-sizes a starting design for it (keeping the engine and wing choices where valid)."""
        from ..core import synthesis as S
        c = self.params.configuration
        tail = "none" if layout in ("flying_wing", "blended_wing_body") else (
            c.tail_type if c.tail_type != "none" else "conventional")
        eng = c.engine_layout if layout not in ("supersonic_delta",) else "under_wing"
        if layout in ("flying_wing", "blended_wing_body") and eng == "wing":
            eng = "upper_aft"
        try:
            self.params = S.baseline(self.mission, S._C(c.propulsion, eng, tail, c.wing_position, layout))
        except Exception:
            try:
                self.params = S.baseline(self.mission, S.configurations_for(self.mission)[0])
                self.params.set("configuration.layout", layout)
            except Exception:
                return
        self.rebuild_editors(fit=True)
        self.schedule_analysis()

    # ------------------------------------------------------------ editors
    def rebuild_editors(self, fit: bool = False):
        arr = Arrangement(self.params)
        self.arr = arr
        for view in (self.top, self.side):
            view.scene().clear()
        self._draw_top(arr)
        self._draw_side(arr)
        if fit:
            for view in (self.top, self.side):
                view.fit(view.scene().itemsBoundingRect())

    def _poly(self, scene, pts, fill, outline=QColor(220, 225, 235), z=0):
        item = QGraphicsPolygonItem(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
        item.setBrush(QBrush(fill))
        pen = QPen(outline, 0)
        item.setPen(pen)
        item.setZValue(z)
        scene.addItem(item)
        return item

    def _outline_xy(self, ps, side: int, n: int = 12):
        s = ps.surface
        etas = np.linspace(0, 1, n)
        le = [ps.placement.apply(s.map_section(e, np.array([[0.0, 0.0]]), side))[0] for e in etas]
        te = [ps.placement.apply(s.map_section(e, np.array([[1.0, 0.0]]), side))[0] for e in etas]
        return np.array(le), np.array(te)

    def _draw_top(self, arr: Arrangement):
        sc = self.top.scene()
        fus = arr.fuselage
        xs = np.linspace(0, fus.length, 60)
        hw = fus.half_width(xs)
        L = fus.length
        if arr.has_pod or arr.layout not in ("flying_wing",):
            self._poly(sc, list(zip(xs, hw)) + list(zip(xs[::-1], -hw[::-1])), QColor(90, 100, 120), z=1)
        for boom in getattr(arr, "bodies", [])[1:]:
            pass
        for ps in arr.surfaces:
            if ps.surface.vertical and ps.role not in ("v_tail",):
                continue
            colour = {"wing": QColor(70, 120, 190), "canard": QColor(120, 160, 90)}.get(ps.role, QColor(110, 140, 170))
            for side in ps.sides:
                le, te = self._outline_xy(ps, side)
                self._poly(sc, [(p[0], p[1]) for p in le] + [(p[0], p[1]) for p in te[::-1]], colour, z=2)
        # control surfaces
        for cs in arr.control_surfaces:
            ps = arr.surface(cs.surface) if hasattr(arr, "surface") else None
            placement = arr.surface_placement(cs.surface)
            if ps is None or ps.vertical:
                continue
            etas = np.linspace(cs.eta0, cs.eta1, 6)
            hinge = [placement.apply(ps.map_section(e, np.array([[cs.hinge, 0.0]]), cs.side))[0] for e in etas]
            te = [placement.apply(ps.map_section(e, np.array([[1.0, 0.0]]), cs.side))[0] for e in etas]
            self._poly(sc, [(p[0], p[1]) for p in hinge] + [(p[0], p[1]) for p in te[::-1]],
                       QColor(230, 140, 40, 200), z=3)
        for e in arr.engines:
            x, y = float(e.inlet[0]), float(e.inlet[1])
            rad = getattr(e, "radius", 0.17 * e.length)
            r = QGraphicsRectItem(x, y - rad, e.length, 2 * rad)
            r.setBrush(QBrush(QColor(170, 170, 180)))
            r.setPen(QPen(QColor(40, 40, 40), 0))
            r.setZValue(4)
            sc.addItem(r)
        self._top_handles(arr)

    def _draw_side(self, arr: Arrangement):
        sc = self.side.scene()
        fus = arr.fuselage
        xs = np.linspace(0, fus.length, 60)
        cz, hh = fus.centre_z(xs), fus.half_height(xs)
        top = list(zip(xs, -(cz + hh)))
        bot = list(zip(xs[::-1], -(cz - hh)[::-1]))
        self._poly(sc, top + bot, QColor(90, 100, 120), z=1)
        for ps in arr.surfaces:
            side = ps.sides[0]
            le, te = self._outline_xy(ps, side)
            colour = QColor(70, 120, 190) if not ps.surface.vertical else QColor(110, 140, 170)
            if ps.surface.vertical:
                self._poly(sc, [(p[0], -p[2]) for p in le] + [(p[0], -p[2]) for p in te[::-1]], colour, z=2)
            else:
                path = QPainterPath(QPointF(le[0][0], -le[0][2]))
                path.lineTo(QPointF(te[0][0], -te[0][2]))
                item = QGraphicsPathItem(path)
                item.setPen(QPen(colour, 0.25))
                item.setZValue(3)
                sc.addItem(item)
        g = QGraphicsSimpleTextItem("ground")
        g.setBrush(QBrush(QColor(140, 140, 140)))
        g.setPos(0, -arr.z_ground)
        g.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        sc.addItem(g)
        ground = QGraphicsPathItem(QPainterPath(QPointF(-2, -arr.z_ground)))
        p = QPainterPath(QPointF(-2, -arr.z_ground))
        p.lineTo(QPointF(fus.length + 2, -arr.z_ground))
        ground.setPath(p)
        ground.setPen(QPen(QColor(120, 120, 120), 0))
        sc.addItem(ground)
        self._side_handles(arr)

    # ------------------------------------------------------------ handles
    def _surface_points(self, key: str):
        ps = arr_ps = next(p for p in self.arr.surfaces if p.key == key)
        s = arr_ps.surface

        def at(e, xc):
            return arr_ps.placement.apply(s.map_section(e, np.array([[xc, 0.0]]), 1))[0]
        return ps, at(0, 0), at(0, 1), at(1, 0), at(1, 1)

    def _top_handles(self, arr: Arrangement):
        sc = self.top.scene()
        L = arr.fuselage.length
        groups = [("wing", "wing")] + [(ps.key, {"htail": "htail", "canard": "canard"}.get(ps.role))
                                        for ps in arr.surfaces if ps.role in ("htail", "canard") and ps.key != "wing"]
        seen = set()
        for key, group in groups:
            if group is None or group in seen:
                continue
            seen.add(group)
            try:
                ps, root_le, root_te, tip_le, tip_te = self._surface_points(key)
            except StopIteration:
                continue
            ox, oy = float(root_le[0]), float(root_le[1])
            x_param = f"{group}.x_position"

            def move(pos, ox=ox, x_param=x_param):
                frac = clamp(x_param, pos.x() / L)
                self.params.set(x_param, frac)
                self._live()
                return QPointF(frac * L, pos.y() * 0 + self._move_y)
            self._move_y = float(0.5 * (root_le[1] + tip_le[1]) * 0.15)
            sc.addItem(Handle(QPointF(ox, self._move_y), move, self._released, "diamond", QColor(255, 220, 60),
                              f"Move the {group} along the fuselage"))

            def tip(pos, ox=ox, oy=oy, group=group):
                semi = max(pos.y() - oy, 0.3)
                span = clamp(f"{group}.span", 2 * (semi + oy))
                self.params.set(f"{group}.span", span)
                sweep = clamp(f"{group}.sweep_le_deg", math.degrees(math.atan2(pos.x() - ox, max(span / 2 - oy, 0.1))))
                self.params.set(f"{group}.sweep_le_deg", sweep)
                if group == "wing" and self.params.wing.crank_eta <= 0:
                    self.params.set("wing.inner_sweep_le_deg", clamp("wing.inner_sweep_le_deg", sweep))
                self._live()
                return QPointF(ox + math.tan(math.radians(sweep)) * (span / 2 - oy), span / 2)
            sc.addItem(Handle(QPointF(tip_le[0], tip_le[1]), tip, self._released, "circle", QColor(255, 140, 0),
                              f"Drag to change the {group} span and sweep"))

            def root_chord(pos, ox=ox, oy=oy, group=group):
                c = clamp(f"{group}.root_chord", pos.x() - ox)
                self.params.set(f"{group}.root_chord", c)
                self._live()
                return QPointF(ox + c, oy)
            sc.addItem(Handle(QPointF(root_te[0], root_te[1]), root_chord, self._released, "square",
                              QColor(120, 200, 255), f"{group} root chord"))

            def taper(pos, group=group, tip_x=float(tip_le[0]), tip_y=float(tip_le[1])):
                rc = getattr(self.params, group).root_chord
                t = clamp(f"{group}.taper_ratio", (pos.x() - tip_x) / rc)
                self.params.set(f"{group}.taper_ratio", t)
                self._live()
                return QPointF(tip_x + t * rc, tip_y)
            sc.addItem(Handle(QPointF(tip_te[0], tip_te[1]), taper, self._released, "square", QColor(120, 200, 255),
                              f"{group} tip chord (taper)"))
        # fuselage length and width
        fus = arr.fuselage

        def length(pos):
            Lnew = clamp("fuselage.length", pos.x())
            self.params.set("fuselage.length", Lnew)
            self._live()
            return QPointF(Lnew, 0)
        sc.addItem(Handle(QPointF(fus.length, 0.0), length, self._released, "square", QColor(200, 200, 210),
                          "Fuselage length"))
        x_mid = 0.5 * fus.length

        def width(pos):
            d = clamp("fuselage.diameter", 2 * abs(pos.y()))
            self.params.set("fuselage.diameter", d)
            self._live()
            return QPointF(x_mid, d / 2)
        sc.addItem(Handle(QPointF(x_mid, float(fus.half_width(np.array([x_mid]))[0])), width, self._released,
                          "square", QColor(200, 200, 210), "Fuselage diameter"))
        # engines along the wing
        wing_eng = [e for e in arr.engines if e.mount == "wing" and e.side > 0]
        if wing_eng:
            e = wing_eng[0]
            semi = arr.wing.semi_span

            def eng(pos, x0=float(e.inlet[0])):
                f = clamp("engines.span_fraction", pos.y() / semi)
                self.params.set("engines.span_fraction", f)
                self._live()
                return QPointF(x0, f * semi)
            sc.addItem(Handle(QPointF(e.inlet[0], e.inlet[1]), eng, self._released, "circle", QColor(200, 200, 200),
                              "Slide the engines along the wing"))
        # ailerons and flaps (right wing)
        wing = next(p for p in arr.surfaces if p.key == "wing")

        def te_point(eta, xc=1.0):
            return wing.placement.apply(wing.surface.map_section(eta, np.array([[xc, 0.0]]), 1))[0]
        semi = wing.surface.semi_span
        c = self.params.controls

        def eta_handle(param, eta, tipname, lo_param=None):
            def drag(pos, param=param):
                e = clamp(f"controls.{param}", pos.y() / semi)
                if param == "aileron_start":
                    e = min(e, self.params.controls.aileron_end - 0.05)
                if param == "aileron_end":
                    e = max(e, self.params.controls.aileron_start + 0.05)
                if param == "flap_end":
                    e = min(e, self.params.controls.aileron_start - 0.01)
                self.params.set(f"controls.{param}", e)
                self._live()
                p = te_point(e)
                return QPointF(p[0], p[1])
            p = te_point(eta)
            sc.addItem(Handle(QPointF(p[0], p[1]), drag, self._released, "circle", QColor(230, 140, 40), tipname))
        if any(cs.kind in ("aileron", "elevon_outer") for cs in arr.control_surfaces):
            eta_handle("aileron_start", c.aileron_start, "Aileron inner edge")
            eta_handle("aileron_end", c.aileron_end, "Aileron outer edge")
        if any(cs.kind == "flap" for cs in arr.control_surfaces):
            eta_handle("flap_end", c.flap_end, "Flap outer edge")
        for param, eta, label in (("aileron_chord", 0.5 * (c.aileron_start + c.aileron_end), "Aileron chord"),
                                  ("flap_chord", 0.5 * c.flap_end, "Flap chord")):
            if (param == "flap_chord") and not any(cs.kind == "flap" for cs in arr.control_surfaces):
                continue

            def chord_drag(pos, param=param, eta=eta):
                lep, tep = te_point(eta, 0.0), te_point(eta, 1.0)
                frac = clamp(f"controls.{param}", (tep[0] - pos.x()) / max(tep[0] - lep[0], 1e-6))
                self.params.set(f"controls.{param}", frac)
                self._live()
                hp = te_point(eta, 1.0 - frac)
                return QPointF(hp[0], hp[1])
            hp = te_point(eta, 1.0 - getattr(c, param))
            sc.addItem(Handle(QPointF(hp[0], hp[1]), chord_drag, self._released, "square", QColor(230, 140, 40), label))

    def _side_handles(self, arr: Arrangement):
        sc = self.side.scene()
        fins = [p for p in arr.surfaces if p.surface.vertical and p.role in ("fin", "vtail")]
        if not fins:
            return
        ps = fins[0]
        s = ps.surface

        def at(e, xc):
            return ps.placement.apply(s.map_section(e, np.array([[xc, 0.0]]), ps.sides[0]))[0]
        root_le, tip_le, root_te = at(0, 0), at(1, 0), at(0, 1)

        def fin_tip(pos, root=root_le):
            h = clamp("vtail.span", -pos.y() - root[2])
            self.params.set("vtail.span", h)
            sweep = clamp("vtail.sweep_le_deg", math.degrees(math.atan2(pos.x() - root[0], max(h, 0.1))))
            self.params.set("vtail.sweep_le_deg", sweep)
            self._live()
            return QPointF(root[0] + math.tan(math.radians(sweep)) * h, -(root[2] + h))
        sc.addItem(Handle(QPointF(tip_le[0], -tip_le[2]), fin_tip, self._released, "circle", QColor(255, 140, 0),
                          "Fin height and sweep"))

        def fin_chord(pos, root=root_le):
            cnew = clamp("vtail.root_chord", pos.x() - root[0])
            self.params.set("vtail.root_chord", cnew)
            self._live()
            return QPointF(root[0] + cnew, -root[2])
        sc.addItem(Handle(QPointF(root_te[0], -root_te[2]), fin_chord, self._released, "square",
                          QColor(120, 200, 255), "Fin root chord"))

        def rudder(pos, root=root_le):
            rte = at(0.4, 1.0)
            rle = at(0.4, 0.0)
            frac = clamp("controls.rudder_chord", (rte[0] - pos.x()) / max(rte[0] - rle[0], 1e-6))
            self.params.set("controls.rudder_chord", frac)
            self._live()
            hp = at(0.4, 1.0 - frac)
            return QPointF(hp[0], -hp[2])
        hp = at(0.4, 1.0 - self.params.controls.rudder_chord)
        sc.addItem(Handle(QPointF(hp[0], -hp[2]), rudder, self._released, "square", QColor(230, 140, 40),
                          "Rudder chord"))

    # ------------------------------------------------------------ live update
    def _live(self):
        """During a drag: redraw the outlines only (handles stay under the mouse)."""
        self._dragging = True
        try:
            arr = Arrangement(self.params)
        except Exception:
            return
        self.arr = arr
        for view, draw in ((self.top, self._draw_top_outline), (self.side, self._draw_side_outline)):
            for item in list(view.scene().items()):
                if not isinstance(item, Handle):
                    view.scene().removeItem(item)
            draw(arr)
        self.schedule_analysis(400)

    def _draw_top_outline(self, arr):
        handles = [i for i in self.top.scene().items() if isinstance(i, Handle)]
        saved = self._top_handles
        self._top_handles = lambda a: None
        try:
            self._draw_top(arr)
        finally:
            self._top_handles = saved
        _ = handles

    def _draw_side_outline(self, arr):
        saved = self._side_handles
        self._side_handles = lambda a: None
        try:
            self._draw_side(arr)
        finally:
            self._side_handles = saved

    def _released(self):
        self._dragging = False
        QTimer.singleShot(0, lambda: self.rebuild_editors(fit=False))
        self.schedule_analysis(50)

    def schedule_analysis(self, ms: int = 250):
        self._update_timer.start(ms)

    def refresh_analysis(self):
        from ..core.verdict import flight_verdict
        try:
            first = not hasattr(self, "_shown_span")
            self.design = AircraftDesign(self.params.copy(), self.name)
            self.viewport.show_design(self.design)
            size = (round(self.params.wing.span), round(self.params.fuselage.length), self.params.configuration.layout)
            if first or size != self._shown_span:  # re-frame the 3-D view when the aircraft changes size
                self._shown_span = size
                self.viewport.renderer.ResetCamera()
                self.viewport.renderer.GetActiveCamera().Zoom(1.25)
                self.viewport.vtk_widget.GetRenderWindow().Render()
        except Exception as exc:
            self.verdict_label.setText(f"<b style='color:#e0533d'>Geometry problem:</b> {exc}")
            return
        try:
            self.verdict = flight_verdict(self.params, self.mission)
        except Exception as exc:
            self.verdict = None
            self.verdict_label.setText(f"<b style='color:#e0533d'>Can't analyse this shape:</b> {exc}")
            self.fly_btn.setEnabled(False)
            self.wt_btn.setEnabled(False)
            return
        v = self.verdict
        colour = "#4caf50" if v.flies and v.meets_mission else ("#e8a33d" if v.flies else "#e0533d")
        rows = "".join(
            f"<tr><td style='color:{'#e8a33d' if c.ok and c.warning else '#4caf50' if c.ok else '#e0533d'}'>"
            f"{'⚠' if c.ok and c.warning else '✔' if c.ok else '✘'}</td><td><b>{c.name}</b><br>"
            f"<span style='color:gray'>{c.detail}</span></td></tr>" for c in v.checks)
        mission = ""
        if v.mission_issues:
            mission = "<p><b>Mission requirements missed</b><br>" + "<br>".join(
                f"• {i}" for i in v.mission_issues[:10]) + "</p>"
        m = v.evaluation.metrics if v.evaluation and v.evaluation.converged else {}
        stats = (f"<p style='color:gray'>MTOW {m.get('mtow_kg', 0):,.0f} kg · span {self.params.wing.span:.1f} m · "
                 f"L/D {m.get('cruise_ld', 0):.1f} · range fuel {m.get('fuel_kg', 0):,.0f} kg</p>") if m else ""
        self.verdict_label.setText(f"<h3 style='color:{colour}'>{v.headline}</h3>{stats}"
                                   f"<table cellspacing=4>{rows}</table>{mission}")
        self.fly_btn.setEnabled(v.flies)
        self.wt_btn.setEnabled(bool(v.evaluation and v.evaluation.converged))

    # ------------------------------------------------------------ actions
    def fly(self):
        if not self.verdict or not self.verdict.flies:
            return
        from ..simulator.window import SimulatorWindow
        self._sim = SimulatorWindow(self.design, self.mission, self.verdict.evaluation, None)
        self._sim.show()
        self._sim.vtk_widget.setFocus()

    def wind_tunnel(self):
        if not self.verdict or not self.verdict.evaluation or not self.verdict.evaluation.converged:
            return
        from .wind_tunnel import WindTunnelWindow
        self._wt = WindTunnelWindow(self.design, self.mission, self.verdict.evaluation, None)
        self._wt.show()

    def closeEvent(self, ev):
        self._update_timer.stop()
        try:
            self.viewport.close()
        except Exception:
            pass
        super().closeEvent(ev)
