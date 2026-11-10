"""Design studio, Kerbal-style: drag parts onto the aircraft, click a part to shape it and set its strength.

* **Parts bin** - drag a wing, tailplane, T-tail, V-tail, twin fins, canard, jet or propeller engines,
  twin booms or a flying-wing / blended-body kit into the top or side view.  While you drag, the
  places that part can attach light up as green nodes; the nearest turns yellow; drop to attach.
* **Click a part** (or pick it from the list) to select it: it is outlined, its drag handles appear
  (move, span/sweep, chords, control surfaces), and the properties panel shows every dimension of that
  part plus its **strength** - material and reserve factor - with a live "breaks at ... g" readout.
  The *Aircraft* entry holds the g rating and safety factor.
* **Delete** removes the selected part where that still leaves an aircraft (no tail -> flying wing,
  no canard -> conventional).
* **Will it fly?** updates as you build; **Fly it**, **Wind tunnel**, **Add to project**.

Every part and handle edits real design parameters (clamped to valid ranges), so whatever you build
stays a consistent aircraft for the evaluator, structures, flight model and wind tunnel.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout,
                               QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
                               QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from ..core import parameters as P
from ..core.design import AircraftDesign
from ..core.mission import MissionRequirements
from ..core.parameters import AircraftParameters
from ..geometry.arrangement import Arrangement

SPECS = dict(P.parameter_specs() if callable(P.parameter_specs) else P.parameter_specs)

LAYOUTS = [("Conventional", "conventional"), ("Canard", "canard"), ("Flying wing", "flying_wing"),
           ("Blended wing body", "blended_wing_body"), ("Twin boom", "twin_boom"),
           ("Supersonic delta", "supersonic_delta"), ("Stunt plane (aerobatic)", "aerobatic"),
           ("Glider (sailplane)", "glider"), ("K-21 two-seat trainer (preset)", "k21")]


def clamp(name: str, value: float) -> float:
    spec = SPECS.get(name, {})
    lo = spec.get("lo", spec.get("min", -math.inf))
    hi = spec.get("hi", spec.get("max", math.inf))
    return float(np.clip(value, lo, hi))


def options(name: str) -> tuple:
    return tuple(SPECS.get(name, {}).get("options", ()))


# ----------------------------------------------------------------------------- parts catalogue
PARTS = [  # (kind, label, tooltip)
    ("wing", "🪽 Main wing", "Drop on the fuselage to move the wing; in the side view, choose low / mid / high"),
    ("tailplane", "▭ Tailplane", "Conventional tail at the back of the fuselage"),
    ("t_tail", "⊤ T-tail", "Tailplane on top of the fin"),
    ("v_tail", "∨ V-tail", "Two angled surfaces do both pitch and yaw"),
    ("twin_fin", "‖ Twin fins", "A fin at each tailplane tip"),
    ("canard", "◁ Canard", "Foreplane near the nose (the wing moves aft)"),
    ("jet_pair", "🔥 Jet engines", "Drop on the wing, the rear fuselage, on top, or the tail cone"),
    ("prop_pair", "🌀 Propeller engines", "Drop on the wing for a pair of turboprops"),
    ("nose_prop", "🌀 Nose propeller", "Single tractor propeller in the nose (light / aerobatic aircraft)"),
    ("pusher_prop", "🌀 Pusher propeller", "Single propeller behind the tail cone"),
    ("booms", "‖ Twin booms", "Booms from the wing carry the tail"),
    ("flying_wing", "◆ Flying-wing kit", "Remove fuselage and tail: an all-wing aircraft"),
    ("bwb", "◆ Blended body kit", "Lifting centre-body blended into the wing"),
    ("glider", "🪁 Glider kit", "No engine, long slender wing: a sailplane"),
]
PART_NAMES = {"aircraft": "Whole aircraft", "wing": "Main wing", "htail": "Tailplane", "vtail": "Fin",
              "canard": "Canard", "fuselage": "Fuselage", "engines": "Engines", "gear": "Landing gear",
              "booms": "Booms"}
PART_PROPS = {
    "aircraft": [("structure.limit_load_factor", "Max positive g (limit)"),
                 ("structure.negative_load_factor", "Max negative g (limit)"), ("structure.safety_factor", "Safety factor"),
                 ("configuration.wing_position", "Wing height"), ("configuration.propulsion", "Engine type")],
    "wing": [("wing.span", "Span"), ("wing.root_chord", "Root chord"), ("wing.taper_ratio", "Taper"),
             ("wing.sweep_le_deg", "Sweep"), ("wing.dihedral_deg", "Dihedral"), ("wing.tc_root", "Thickness (root)"),
             ("wing.tc_tip", "Thickness (tip)"), ("wing.camber", "Camber (0 = symmetric)"),
             ("wing.twist_tip_deg", "Tip twist"), ("wing.x_position", "Position along fuselage"),
             ("wing.vertical_shift", "Height on the fuselage (fine)"),
             ("controls.aileron_start", "Aileron inner edge"), ("controls.aileron_end", "Aileron outer edge"),
             ("controls.aileron_chord", "Aileron chord"), ("controls.flap_end", "Flap span (0 = none)"),
             ("controls.flap_chord", "Flap chord")],
    "htail": [("htail.span", "Span"), ("htail.root_chord", "Root chord"), ("htail.taper_ratio", "Taper"),
              ("htail.sweep_le_deg", "Sweep"), ("htail.dihedral_deg", "Dihedral"), ("htail.x_position", "Position"),
              ("controls.elevator_chord", "Elevator chord")],
    "vtail": [("vtail.span", "Height"), ("vtail.root_chord", "Root chord"), ("vtail.taper_ratio", "Taper"),
              ("vtail.sweep_le_deg", "Sweep"), ("vtail.x_position", "Position"), ("controls.rudder_chord", "Rudder chord")],
    "canard": [("canard.span", "Span"), ("canard.root_chord", "Root chord"), ("canard.taper_ratio", "Taper"),
               ("canard.sweep_le_deg", "Sweep"), ("canard.x_position", "Position"),
               ("controls.elevator_chord", "Elevator chord")],
    "fuselage": [("fuselage.length", "Length"), ("fuselage.diameter", "Diameter"),
                 ("fuselage.height_ratio", "Height / width"), ("fuselage.nose_fraction", "Nose length (fraction)"),
                 ("fuselage.tail_fraction", "Tail cone (fraction)"), ("fuselage.nose_profile", "Nose shape")],
    "engines": [("engines.count", "Number of engines"), ("engines.thrust_kn", "Thrust each"),
                ("engines.bypass_ratio", "Bypass ratio (jets)"), ("engines.span_fraction", "Position along the wing"),
                ("engines.x_shift", "Moved forward (−) / aft (+)"), ("engines.spacing_fraction", "Outer pair offset"),
                ("configuration.engine_layout", "Mounting")],
    "gear": [("gear.wheel_diameter", "Wheel diameter"), ("gear.wheels_per_leg", "Wheels per leg"),
             ("gear.track", "Track"), ("gear.ground_clearance", "Ground clearance")],
    "booms": [("booms.lateral_station", "Spanwise station"), ("booms.diameter", "Diameter"),
              ("booms.tail_arm", "Tail arm")],
}
STRENGTH = {"wing": ("structure.wing_strength", "structure.wing_material", "wing"),
            "htail": ("structure.tail_strength", None, "horizontal_tail"),
            "vtail": ("structure.tail_strength", None, "vertical_tail"),
            "canard": ("structure.tail_strength", None, "horizontal_tail"),
            "fuselage": ("structure.fuselage_strength", "structure.fuselage_material", "fuselage"),
            "gear": ("structure.gear_strength", None, "main_gear")}
MIME = "application/x-aircraft-foundry-part"


class PartsBin(QListWidget):
    """Tiles you drag onto the editors."""

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setGridSize(self.gridSize().__class__(132, 46))
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMovement(QListWidget.Static)
        # setMovement(Static) silently turns dragging off in Qt: enable it afterwards, as drag-only
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setMinimumHeight(250)
        for kind, label, tip in PARTS:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, kind)
            it.setToolTip(tip)
            self.addItem(it)

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None:
            return
        md = QMimeData()
        md.setData(MIME, it.data(Qt.UserRole).encode())
        md.setText(it.text())
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


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
    """Top or side view: shows the parts, accepts dropped parts (with attachment nodes) and clicks."""

    def __init__(self, title: str, studio=None, view: str = "top"):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(self.renderHints().__class__.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor(28, 33, 41)))
        self.title = title
        self.studio = studio
        self.view = view
        self._fitted = False
        self._node_items = []
        self.setAcceptDrops(True)

    # ---- drag and drop of parts
    def _kind(self, ev):
        md = ev.mimeData()
        return bytes(md.data(MIME)).decode() if md.hasFormat(MIME) else None

    def dragEnterEvent(self, ev):
        if self._kind(ev):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        kind = self._kind(ev)
        if not kind or self.studio is None:
            return
        ev.acceptProposedAction()
        self.show_nodes(kind, self.mapToScene(ev.position().toPoint()))

    def dragLeaveEvent(self, ev):
        self.clear_nodes()

    def dropEvent(self, ev):
        kind = self._kind(ev)
        pos = self.mapToScene(ev.position().toPoint())
        node = self.nearest_node(kind, pos) if kind else None
        self.clear_nodes()
        if kind and self.studio is not None:
            ev.acceptProposedAction()
            self.studio.drop_part(kind, node, self.view)

    def nearest_node(self, kind, pos):
        nodes = self.studio.nodes_for(kind, self.view)
        if not nodes:
            return None
        scale = max(self.transform().m11(), 1e-6)
        best = min(nodes, key=lambda n: (n[0].x() - pos.x()) ** 2 + (n[0].y() - pos.y()) ** 2)
        d_px = math.hypot(best[0].x() - pos.x(), best[0].y() - pos.y()) * scale
        return best if d_px < 60 else None

    def show_nodes(self, kind, pos):
        self.clear_nodes()
        near = self.nearest_node(kind, pos)
        for pt, node_id in self.studio.nodes_for(kind, self.view):
            hot = near is not None and near[1] == node_id and near[0] == pt
            r = 9 if hot else 6
            it = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
            it.setPos(pt)
            it.setBrush(QBrush(QColor(250, 210, 60) if hot else QColor(90, 210, 120, 200)))
            it.setPen(QPen(QColor(20, 20, 20), 1))
            it.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            it.setZValue(20)
            self.scene().addItem(it)
            self._node_items.append(it)

    def clear_nodes(self):
        for it in self._node_items:
            if it.scene() is not None:
                self.scene().removeItem(it)
        self._node_items = []

    # ---- click to select a part
    def mousePressEvent(self, ev):
        item = self.itemAt(ev.position().toPoint())
        if isinstance(item, Handle) or self.studio is None:
            return super().mousePressEvent(ev)
        part = item.data(0) if item is not None else None
        if part:
            self.studio.select(part)
        super().mousePressEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.studio is not None:
            self.studio.remove_selected()
            return
        super().keyPressEvent(ev)

    def fit(self, rect: QRectF):
        self.fitInView(rect.adjusted(-0.08 * rect.width(), -0.15 * rect.height(), 0.08 * rect.width(),
                                     0.15 * rect.height()), Qt.KeepAspectRatio)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.scene().itemsBoundingRect().isValid():
            self.fit(self.scene().itemsBoundingRect())


class DesignStudioWindow(QMainWindow):
    send_to_project = Signal(object)  # AircraftParameters: save as a new aircraft
    apply_to_selected = Signal(object)  # AircraftParameters: save into the aircraft being edited

    def __init__(self, params: AircraftParameters, mission: MissionRequirements, parent=None, name: str = "Studio design"):
        super().__init__(parent)
        self.setWindowTitle("Design studio — drag the parts, see if it flies")
        self.resize(1500, 920)
        self.mission = mission
        self.params = params.copy()
        self._saved = params.copy()
        self.name = name
        self.target_name = None
        self.design = AircraftDesign(self.params.copy(), name)
        self.verdict = None
        self._dragging = False
        self.selected = "wing"

        from .viewport import Viewport
        self.top = EditorView("Top view", self, "top")
        self.side = EditorView("Side view", self, "side")
        self.viewport = Viewport(self)
        editors = QSplitter(Qt.Vertical)
        for v, label in ((self.top, "Top view — drag parts in from the bin · click a part to select it · "
                                    "Delete removes it"),
                         (self.side, "Side view — wing height, fin, T-tail")):
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
        centre.setStretchFactor(0, 3)  # editors / 3-D preview share space on small screens too
        centre.setStretchFactor(1, 2)
        centre.setChildrenCollapsible(False)
        self.viewport.setMinimumWidth(200)
        self.setCentralWidget(centre)
        self._build_side_panel()

        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self.refresh_analysis)
        self.rebuild_editors(fit=True)
        self.refresh_analysis()
        self._fill_part_list()
        self._build_properties()

    # ------------------------------------------------------------ palette
    def _build_side_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)
        bin_box = QGroupBox("Parts — drag onto the aircraft")
        bl = QVBoxLayout(bin_box)
        self.bin = PartsBin()
        bl.addWidget(self.bin)
        lay.addWidget(bin_box)
        props = QGroupBox("Selected part")
        pl = QVBoxLayout(props)
        self.part_box = QComboBox()
        self.part_box.currentIndexChanged.connect(lambda i: self.select(self.part_box.itemData(i)))
        pl.addWidget(self.part_box)
        self.props_form = QFormLayout()
        pl.addLayout(self.props_form)
        self.strength_box = QGroupBox("Strength")
        self.strength_form = QFormLayout(self.strength_box)
        pl.addWidget(self.strength_box)
        self.remove_btn = QPushButton("Remove part")
        self.remove_btn.clicked.connect(self.remove_selected)
        pl.addWidget(self.remove_btn)
        lay.addWidget(props)
        vbox = QGroupBox("Will it fly?")
        vl = QVBoxLayout(vbox)
        self.verdict_label = QLabel()
        self.verdict_label.setTextFormat(Qt.RichText)
        self.verdict_label.setWordWrap(True)
        self.verdict_label.setAlignment(Qt.AlignTop)
        vl.addWidget(self.verdict_label)
        row = QHBoxLayout()
        self.fly_btn = QPushButton("✈ Fly it")
        self.fly_btn.setObjectName("primary")
        self.wt_btn = QPushButton("🌬 Wind tunnel")
        self.add_btn = QPushButton("Save as new plane")
        from PySide6.QtGui import QKeySequence, QShortcut
        self.undo_btn = QPushButton("↶ Undo")
        self.redo_btn = QPushButton("↷ Redo")
        self.undo_btn.setToolTip("Undo (Ctrl+Z)")
        self.redo_btn.setToolTip("Redo (Ctrl+Y / Ctrl+Shift+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn.clicked.connect(self.redo)
        self._undo_row = QHBoxLayout()
        self._undo_row.addWidget(self.undo_btn)
        self._undo_row.addWidget(self.redo_btn)
        for seq, fn in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        QTimer.singleShot(0, self._record_history)  # the starting point
        self.apply_btn = QPushButton("Apply to selected plane")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(lambda: self.apply_to_selected.emit(self.params.copy()))
        self.fly_btn.clicked.connect(self.fly)
        self.wt_btn.clicked.connect(self.wind_tunnel)
        self.add_btn.clicked.connect(lambda: self.send_to_project.emit(self.params.copy()))
        for b_ in (self.fly_btn, self.wt_btn, self.add_btn):
            row.addWidget(b_)
        vl.addLayout(self._undo_row)
        vl.addWidget(self.apply_btn)
        self.balance_btn = QPushButton("⚖ Balance (move the wing for stability)")
        self.balance_btn.setToolTip("Moves the wing so the centre of gravity sits a healthy distance ahead of the "
                                    "neutral point, then re-sizes the tails and landing gear")
        self.balance_btn.clicked.connect(self.balance)
        vl.addWidget(self.balance_btn)
        vl.addLayout(row)
        lay.addWidget(vbox)
        lay.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        dock = QDockWidget("Design studio", self)
        dock.setWidget(scroll)
        dock.setMinimumWidth(400)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self._fill_part_list()

    # ------------------------------------------------------------ parts present / selection / properties
    def parts_present(self) -> list[str]:
        arr = getattr(self, "arr", None) or Arrangement(self.params)
        roles = {ps.role for ps in arr.surfaces}
        out = ["aircraft", "wing"]
        if arr.layout not in ("flying_wing",):
            out.append("fuselage")
        if roles & {"htail", "v_tail"}:
            out.append("htail")
        if any(ps.surface.vertical and ps.role in ("fin", "vtail") for ps in arr.surfaces):
            out.append("vtail")
        if "canard" in roles:
            out.append("canard")
        if arr.layout == "twin_boom":
            out.append("booms")
        out += (["gear"] if self.params.configuration.propulsion == "none" else ["engines", "gear"])
        return out

    def _fill_part_list(self):
        self.part_box.blockSignals(True)
        self.part_box.clear()
        for key in self.parts_present():
            self.part_box.addItem(PART_NAMES[key], key)
        i = self.part_box.findData(self.selected)
        self.part_box.setCurrentIndex(max(i, 0))
        self.part_box.blockSignals(False)
        if i < 0:
            self.selected = self.part_box.itemData(0)

    def select(self, part: str | None):
        if not part or part == self.selected and self.props_form.rowCount():
            return
        self.selected = part
        i = self.part_box.findData(part)
        if i >= 0 and self.part_box.currentIndex() != i:
            self.part_box.blockSignals(True)
            self.part_box.setCurrentIndex(i)
            self.part_box.blockSignals(False)
        self.rebuild_editors(fit=False)
        self._build_properties()

    def _clear_form(self, form):
        while form.rowCount():
            form.removeRow(0)

    def _editor(self, name: str):
        spec = SPECS.get(name, {})
        value = self.params.get(name) if hasattr(self.params, "get") else None
        if value is None:
            grp, fld = name.split(".")
            value = getattr(getattr(self.params, grp), fld)
        if "options" in spec:
            w = QComboBox()
            w.addItems([str(o) for o in spec["options"]])
            w.setCurrentText(str(value))
            w.currentTextChanged.connect(lambda t, n=name: self.set_value(n, t, resize=n.startswith("configuration")))
            return w
        if isinstance(value, int) and not isinstance(value, bool):
            w = QSpinBox()
            w.setRange(int(spec.get("lo", 0)), int(spec.get("hi", 100)))
            w.setValue(int(value))
            w.valueChanged.connect(lambda v, n=name: self.set_value(n, int(v), resize=n == "engines.count"))
            return w
        w = QDoubleSpinBox()
        lo, hi = spec.get("lo", -1e6), spec.get("hi", 1e6)
        step = spec.get("step", (hi - lo) / 100 if math.isfinite(hi - lo) else 0.1)
        w.setDecimals(3 if step < 0.01 else 2)
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setValue(float(value))
        if spec.get("unit"):
            w.setSuffix(" " + spec["unit"])
        w.setKeyboardTracking(False)
        w.valueChanged.connect(lambda v, n=name: self.set_value(n, float(v)))
        return w

    def _build_properties(self):
        self._clear_form(self.props_form)
        self._clear_form(self.strength_form)
        part = self.selected
        for name, label in PART_PROPS.get(part, []):
            if name in SPECS:
                self.props_form.addRow(label, self._editor(name))
        st = STRENGTH.get(part)
        self.strength_box.setVisible(st is not None)
        if st:
            factor, mat, group = st
            self.strength_form.addRow("Reserve factor", self._editor(factor))
            if mat:
                self.strength_form.addRow("Material", self._editor(mat))
            self.strength_readout = QLabel()
            self.strength_readout.setWordWrap(True)
            self.strength_form.addRow(self.strength_readout)
            self._update_strength_readout()
        self.remove_btn.setEnabled(part in ("htail", "vtail", "canard", "booms"))

    def _update_strength_readout(self):
        st = STRENGTH.get(self.selected)
        lbl = getattr(self, "strength_readout", None)
        if not st or lbl is None:
            return
        factor, _, group = st
        s_ = self.params.structure
        k = getattr(s_, factor.split(".")[1])
        ev = self.verdict.evaluation if self.verdict and self.verdict.evaluation else None
        mass = (ev.weights.get(group, 0.0) if ev is not None and ev.converged else 0.0)
        if self.selected == "wing":
            neg = abs(getattr(s_, "negative_load_factor", -1.0))
            text = (f"Breaks at <b>+{s_.limit_load_factor * s_.safety_factor * k:.1f} g</b> and "
                    f"<b>−{neg * s_.safety_factor * k:.1f} g</b> (limits +{s_.limit_load_factor:.1f} / −{neg:.1f} g "
                    f"× safety {s_.safety_factor:.2f} × reserve {k:.2f})")
        else:
            text = f"Designed for {s_.limit_load_factor * s_.safety_factor * k:.1f} g ultimate"
        if mass:
            text += f" · this part weighs <b>{mass:,.0f} kg</b>"
        lbl.setText(text)

    # ------------------------------------------------------------ parts: where they attach, and attaching them
    def nodes_for(self, kind: str, view: str) -> list:
        arr = self.arr
        fus = arr.fuselage
        L = fus.length
        wing = next(p for p in arr.surfaces if p.key == "wing")

        def wing_pt(eta, side=1, xc=0.35):
            p = wing.placement.apply(wing.surface.map_section(eta, np.array([[xc, 0.0]]), side))[0]
            return p
        nodes = []
        if view == "top":
            if kind == "wing":
                nodes = [(QPointF(f * L, 0.0), ("wing_x", round(f, 3))) for f in np.arange(0.15, 0.66, 0.05)]
            elif kind in ("tailplane", "v_tail", "twin_fin", "t_tail"):
                nodes = [(QPointF(0.9 * L, 0.0), ("tail", kind))]
            elif kind == "canard":
                nodes = [(QPointF(0.12 * L, 0.0), ("canard",))]
            elif kind in ("jet_pair", "prop_pair"):
                for eta in (0.22, 0.3, 0.38, 0.46):
                    for side in (1, -1):
                        p = wing_pt(eta, side, 0.0)
                        nodes.append((QPointF(p[0], p[1]), ("wing_eng", eta)))
                if kind == "jet_pair":
                    xr = 0.78 * L
                    hw = float(fus.half_width(np.array([xr]))[0])
                    nodes += [(QPointF(xr, s_ * (hw + 1.0)), ("rear",)) for s_ in (1, -1)]
                    nodes.append((QPointF(L, 0.0), ("pusher",)))
            elif kind == "nose_prop":
                nodes = [(QPointF(0.0, 0.0), ("nose",))]
            elif kind == "pusher_prop":
                nodes = [(QPointF(L, 0.0), ("pusher",))]
            elif kind == "booms":
                nodes = [(QPointF(wing_pt(e, s_)[0], wing_pt(e, s_)[1]), ("booms", e)) for e in (0.25, 0.32) for s_ in (1, -1)]
            elif kind in ("flying_wing", "bwb", "glider"):
                nodes = [(QPointF(0.45 * L, 0.0), (kind,))]
        else:
            x_w = float(wing_pt(0.0, 1, 0.3)[0])
            if kind == "wing":
                for pos, frac in (("high", 0.85), ("mid", 0.0), ("low", -0.85)):
                    z = float(fus.centre_z(np.array([x_w]))[0] + frac * fus.half_height(np.array([x_w]))[0])
                    nodes.append((QPointF(x_w, -z), ("wing_z", pos)))
            elif kind in ("t_tail", "tailplane"):
                fins = [p for p in arr.surfaces if p.surface.vertical]
                if fins:
                    f = fins[0]
                    tip = f.placement.apply(f.surface.map_section(1.0, np.array([[0.3, 0.0]]), f.sides[0]))[0]
                    root = f.placement.apply(f.surface.map_section(0.0, np.array([[0.3, 0.0]]), f.sides[0]))[0]
                    nodes = [(QPointF(tip[0], -tip[2]), ("tail", "t_tail")),
                             (QPointF(root[0], -root[2]), ("tail", "tailplane"))]
            elif kind == "jet_pair":
                z = float(fus.top_z(np.array([x_w + 0.4 * wing.surface.root_chord]))[0]) + 0.6
                nodes = [(QPointF(x_w + 0.4 * wing.surface.root_chord, -z), ("upper_aft",))]
        return nodes

    def drop_part(self, kind: str, node, view: str) -> None:
        """Attach a dropped part at a node (or say where it can go)."""
        if node is None:
            self.status.setText(f"Drop the part on one of the highlighted green attachment points.")
            return
        _, nid = node
        c = self.params.configuration
        layout = c.layout
        tailless = layout in ("flying_wing", "blended_wing_body")
        msg = ""
        if kind == "wing" and nid[0] == "wing_x":
            self.set_value("wing.x_position", nid[1], rebuild=False)
            msg = f"Wing moved to {nid[1]:.0%} of the fuselage length."
        elif kind == "wing" and nid[0] == "wing_z":
            self.set_value("configuration.wing_position", nid[1], resize=True, rebuild=False)
            msg = f"{nid[1].capitalize()} wing."
        elif nid[0] == "tail":
            tail = {"tailplane": "conventional", "t_tail": "t_tail", "v_tail": "v_tail", "twin_fin": "twin_fin"}[nid[1]]
            if tailless:
                self.set_layout("conventional")
            self.set_value("configuration.tail_type", tail, resize=True, rebuild=False)
            msg = f"{PART_NAMES.get('htail')} set to {tail.replace('_', ' ')}."
        elif kind == "canard":
            self.set_layout("canard")
            msg = "Canard added (the wing moves aft to keep it balanced)."
        elif kind in ("jet_pair", "prop_pair", "nose_prop", "pusher_prop"):
            total = c and self.params.engines.count * self.params.engines.thrust_kn
            prop = "turbofan" if kind == "jet_pair" else "turboprop"
            mount = {"wing_eng": "under_wing" if layout == "supersonic_delta" else "wing", "rear": "rear_fuselage",
                     "upper_aft": "upper_aft", "pusher": "pusher", "nose": "nose"}[nid[0]]
            count = 1 if kind in ("nose_prop", "pusher_prop") or (mount == "pusher" and prop == "turbofan") else 2
            if mount == "wing" and c.engine_layout == "wing" and c.propulsion == prop and \
                    abs(self.params.engines.span_fraction - nid[1]) > 0.05 and self.params.engines.count == 2:
                count = 4  # a second pair further out on the wing
            self.params.set("configuration.propulsion", prop)
            self.params.set("configuration.engine_layout", mount)
            self.params.set("engines.count", count)
            self.params.set("engines.thrust_kn", clamp("engines.thrust_kn", total / count))
            if nid[0] == "wing_eng":
                self.params.set("engines.span_fraction", clamp("engines.span_fraction", nid[1]))
            self._resize_around()
            msg = f"{count} {'jet' if prop == 'turbofan' else 'propeller'} engine{'s' if count > 1 else ''} " \
                  f"({mount.replace('_', ' ')})."
            self.selected = "engines"
        elif kind == "booms":
            self.set_layout("twin_boom")
            msg = "Twin booms carry the tail."
        elif kind == "glider":
            self.set_layout("glider")
            msg = "Glider: no engine, an 18 m slender wing and a T-tail."
        elif kind in ("flying_wing", "bwb"):
            self.set_layout("flying_wing" if kind == "flying_wing" else "blended_wing_body")
            msg = "All-wing aircraft." if kind == "flying_wing" else "Blended wing body."
        self.status.setText(msg)
        self._fill_part_list()
        self.rebuild_editors(fit=True)
        self._build_properties()
        self.schedule_analysis(50)

    def modified(self) -> bool:
        return self.params.to_dict() != self._saved.to_dict()

    def mark_saved(self) -> None:
        self._saved = self.params.copy()
        self._update_apply_button()

    def set_target_name(self, name: str | None) -> None:
        self.target_name = name
        self._update_apply_button()

    def _update_apply_button(self) -> None:
        if not hasattr(self, "apply_btn"):
            return
        if self.target_name:
            self.apply_btn.setText(f"Apply changes to '{self.target_name}'" + (" •" if self.modified() else ""))
            self.apply_btn.setEnabled(self.modified())
        else:
            self.apply_btn.setText("Apply to selected plane")
            self.apply_btn.setEnabled(False)

    def balance(self) -> None:
        from ..core import synthesis as S
        try:
            S.place_wing_for_stability(self.params, self.mission)
            S._size_tails(self.params, self.mission)
            S.complete_design(self.params, self.mission)
        except Exception as exc:
            self.status.setText(f"Couldn't balance this layout: {exc}")
            return
        self.status.setText(f"Wing moved to {self.params.wing.x_position:.0%} of the fuselage for stability.")
        self.rebuild_editors(fit=False)
        self._build_properties()
        self.schedule_analysis(30)

    def remove_selected(self) -> None:
        part = self.selected
        if part in ("htail", "vtail"):
            self.set_layout("flying_wing")
            self.status.setText("Tail removed: without a tail the aircraft becomes a flying wing "
                                "(swept, washed-out wing with elevons and drag rudders).")
        elif part == "canard":
            self.set_layout("conventional")
            self.status.setText("Canard removed: conventional layout with a tailplane.")
        elif part == "booms":
            self.set_layout("conventional")
            self.status.setText("Booms removed: conventional layout.")
        else:
            self.status.setText("The wing, fuselage, engines and landing gear can be reshaped but not removed.")
            return
        self.selected = "wing"
        self._fill_part_list()
        self._build_properties()

    # ------------------------------------------------------------ parameter edits
    def set_value(self, name: str, value, resize: bool = False, rebuild: bool = True):
        try:
            v = value if isinstance(value, str) or name == "engines.count" else clamp(name, value)
            self.params.set(name, v)
        except Exception:
            return
        if resize:
            self._resize_around()
            QTimer.singleShot(0, self._refresh_panel)
        if rebuild:
            self.rebuild_editors(fit=resize)
        self.schedule_analysis()

    def _refresh_panel(self):
        self._fill_part_list()
        self._build_properties()

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
        if layout in ("glider", "k21"):
            self.mission = S.k21_mission() if layout == "k21" else S.glider_mission()
            self.params = S.k21_preset(self.mission) if layout == "k21" else \
                S.baseline(self.mission, S.configurations_for(self.mission)[0])
            self.rebuild_editors(fit=True)
            self.schedule_analysis()
            QTimer.singleShot(0, self._refresh_panel)
            return
        if getattr(self.mission, "category", "transport") == "glider":
            self.mission = MissionRequirements()
        if layout == "aerobatic":
            # a stunt plane needs a stunt-plane mission: +-10 g, roll rate, symmetric wing, light-aircraft rules
            self.mission = S.aerobatic_mission()
            self.params = S.baseline(self.mission, S.configurations_for(self.mission)[0])
            self.rebuild_editors(fit=True)
            self.schedule_analysis()
            QTimer.singleShot(0, self._refresh_panel)
            return
        if getattr(self.mission, "category", "transport") == "aerobatic":
            self.mission = MissionRequirements()  # back to a transport mission for the other layouts
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
        QTimer.singleShot(0, self._refresh_panel)

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

    def _poly(self, scene, pts, fill, outline=QColor(220, 225, 235), z=0, part=None):
        item = QGraphicsPolygonItem(QPolygonF([QPointF(float(x), float(y)) for x, y in pts]))
        selected = part is not None and part == self.selected
        if selected:
            fill = QColor(fill).lighter(125)
        item.setBrush(QBrush(fill))
        pen = QPen(QColor(255, 200, 70) if selected else outline, 2.5 if selected else 0)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(z)
        item.setData(0, part)
        if part:
            item.setCursor(Qt.PointingHandCursor)
            item.setToolTip(f"{PART_NAMES.get(part, part)} — click to select")
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
            self._poly(sc, list(zip(xs, hw)) + list(zip(xs[::-1], -hw[::-1])), QColor(90, 100, 120), z=1,
                       part="fuselage")
        for boom in getattr(arr, "bodies", [])[1:]:
            pass
        for ps in arr.surfaces:
            if ps.surface.vertical and ps.role not in ("v_tail",):
                continue
            colour = {"wing": QColor(70, 120, 190), "canard": QColor(120, 160, 90)}.get(ps.role, QColor(110, 140, 170))
            part = {"wing": "wing", "canard": "canard"}.get(ps.role, "htail")
            for side in ps.sides:
                le, te = self._outline_xy(ps, side)
                self._poly(sc, [(p[0], p[1]) for p in le] + [(p[0], p[1]) for p in te[::-1]], colour, z=2,
                           part=part)
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
                       QColor(230, 140, 40, 200), z=3, part="wing" if cs.surface == "wing" else
                       ("canard" if cs.surface == "canard" else "htail"))
        for e in arr.engines:
            x, y = float(e.inlet[0]), float(e.inlet[1])
            rad = getattr(e, "radius", 0.17 * e.length)
            r = QGraphicsRectItem(x, y - rad, e.length, 2 * rad)
            sel = self.selected == "engines"
            r.setBrush(QBrush(QColor(200, 200, 210) if sel else QColor(170, 170, 180)))
            pen = QPen(QColor(255, 200, 70) if sel else QColor(40, 40, 40), 2.5 if sel else 0)
            pen.setCosmetic(True)
            r.setPen(pen)
            r.setZValue(4)
            r.setData(0, "engines")
            r.setCursor(Qt.PointingHandCursor)
            r.setToolTip("Engines — click to select")
            sc.addItem(r)
        self._top_handles(arr)

    def _draw_side(self, arr: Arrangement):
        sc = self.side.scene()
        fus = arr.fuselage
        xs = np.linspace(0, fus.length, 60)
        cz, hh = fus.centre_z(xs), fus.half_height(xs)
        top = list(zip(xs, -(cz + hh)))
        bot = list(zip(xs[::-1], -(cz - hh)[::-1]))
        self._poly(sc, top + bot, QColor(90, 100, 120), z=1, part="fuselage")
        for ps in arr.surfaces:
            side = ps.sides[0]
            le, te = self._outline_xy(ps, side)
            colour = QColor(70, 120, 190) if not ps.surface.vertical else QColor(110, 140, 170)
            if ps.surface.vertical:
                self._poly(sc, [(p[0], -p[2]) for p in le] + [(p[0], -p[2]) for p in te[::-1]], colour, z=2,
                           part="vtail" if ps.role in ("fin", "vtail") else "htail")
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
            if group is None or group in seen or group != self.selected:
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
        if self.selected not in ("fuselage", "engines", "wing"):
            return

        def length(pos):
            Lnew = clamp("fuselage.length", pos.x())
            self.params.set("fuselage.length", Lnew)
            self._live()
            return QPointF(Lnew, 0)
        if self.selected == "fuselage":
            sc.addItem(Handle(QPointF(fus.length, 0.0), length, self._released, "square", QColor(200, 200, 210),
                              "Fuselage length"))
        x_mid = 0.5 * fus.length

        def width(pos):
            d = clamp("fuselage.diameter", 2 * abs(pos.y()))
            self.params.set("fuselage.diameter", d)
            self._live()
            return QPointF(x_mid, d / 2)
        if self.selected == "fuselage":
            sc.addItem(Handle(QPointF(x_mid, float(fus.half_width(np.array([x_mid]))[0])), width, self._released,
                              "square", QColor(200, 200, 210), "Fuselage diameter"))
        # engines along the wing
        wing_eng = [e for e in arr.engines if e.mount == "wing" and e.side > 0]
        if wing_eng and self.selected == "engines":
            e = wing_eng[0]
            semi = arr.wing.semi_span

            base_x = float(e.inlet[0]) - float(self.params.engines.x_shift)

            def eng(pos, base_x=base_x):  # drag along the span AND fore / aft
                f = clamp("engines.span_fraction", pos.y() / semi)
                dx = clamp("engines.x_shift", pos.x() - base_x)
                self.params.set("engines.span_fraction", f)
                self.params.set("engines.x_shift", dx)
                self._live()
                return QPointF(base_x + dx, f * semi)
            sc.addItem(Handle(QPointF(e.inlet[0], e.inlet[1]), eng, self._released, "circle", QColor(200, 200, 200),
                              "Drag the engines along the wing, or forward / aft"))
        # ailerons and flaps (right wing)
        if self.selected != "wing":
            return
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
        if not fins or self.selected != "vtail":
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

    # ------------------------------------------------------------ undo / redo
    def _snapshot(self):
        import json
        return json.dumps(self.params.to_dict(), sort_keys=True, default=str)

    def _record_history(self) -> None:
        """Called (debounced) after changes: one undo step per burst of edits, e.g. a whole drag."""
        if getattr(self, "_restoring", False):
            return
        snap = self._snapshot()
        hist = self.__dict__.setdefault("_history", [])
        pos = getattr(self, "_hist_pos", -1)
        if hist and pos >= 0 and hist[pos] == snap:
            return
        del hist[pos + 1:]
        hist.append(snap)
        if len(hist) > 200:
            del hist[0]
        self._hist_pos = len(hist) - 1
        self._update_undo_buttons()

    def _restore(self, snap: str) -> None:
        import json
        from ..core.parameters import AircraftParameters
        self._restoring = True
        try:
            self.params = AircraftParameters.from_dict(json.loads(snap))
            self.rebuild_editors()
            self.schedule_analysis()
            if hasattr(self, "_build_properties"):
                self._build_properties()
        finally:
            self._restoring = False
        self._update_undo_buttons()

    def undo(self) -> None:
        if getattr(self, "_hist_pos", 0) > 0:
            self._hist_pos -= 1
            self._restore(self._history[self._hist_pos])

    def redo(self) -> None:
        hist = getattr(self, "_history", [])
        if getattr(self, "_hist_pos", -1) < len(hist) - 1:
            self._hist_pos += 1
            self._restore(hist[self._hist_pos])

    def _update_undo_buttons(self) -> None:
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(getattr(self, "_hist_pos", 0) > 0)
            self.redo_btn.setEnabled(getattr(self, "_hist_pos", -1) < len(getattr(self, "_history", [])) - 1)

    def schedule_analysis(self, ms: int = 250):
        if not getattr(self, "_restoring", False):
            from PySide6.QtCore import QTimer as _QT
            if not hasattr(self, "_hist_timer"):
                self._hist_timer = _QT(self)
                self._hist_timer.setSingleShot(True)
                self._hist_timer.timeout.connect(self._record_history)
            self._hist_timer.start(400)
        self._update_timer.start(ms)
        self._update_apply_button()  # the Apply button reacts at once; the verdict follows shortly

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
        self._update_strength_readout()
        self._update_apply_button()

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
