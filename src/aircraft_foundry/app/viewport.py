"""VTK 3D viewport hosted in Qt, with camera presets, display modes and picking."""
from __future__ import annotations

import vtkmodules.qt

vtkmodules.qt.PyQtImpl = "PySide6"  # must be set before importing the Qt interactor

import vtkmodules.vtkInteractionStyle  # noqa: E402,F401  (registers interactor styles)
import vtkmodules.vtkRenderingOpenGL2  # noqa: E402,F401  (registers the OpenGL backend)
from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtGui import QAction, QActionGroup  # noqa: E402
from PySide6.QtWidgets import QLabel, QToolBar, QVBoxLayout, QWidget  # noqa: E402
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # noqa: E402
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera  # noqa: E402
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget  # noqa: E402
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor  # noqa: E402
from vtkmodules.vtkRenderingCore import vtkPropPicker, vtkRenderer  # noqa: E402

from ..core.design import AircraftDesign  # noqa: E402
from ..rendering.lighting import VIEWS, set_view, setup_renderer  # noqa: E402
from ..rendering.scene import AircraftScene, SyncStats, disable_vtk_key_shortcuts  # noqa: E402
from ..structural_vis.styles import Layer, ViewMode  # noqa: E402


class Viewport(QWidget):
    component_picked = Signal(object)  # str id or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vtk_widget = QVTKRenderWindowInteractor(self)
        self.renderer = vtkRenderer()
        setup_renderer(self.renderer)
        rw = self.vtk_widget.GetRenderWindow()
        rw.SetAlphaBitPlanes(1)
        rw.SetMultiSamples(0)  # required for depth peeling
        rw.AddRenderer(self.renderer)
        self.interactor = rw.GetInteractor()
        style = vtkInteractorStyleTrackballCamera()
        disable_vtk_key_shortcuts(style)  # w/s/q/e/r/f would bypass the toolbar (or exit the view)
        self.interactor.SetInteractorStyle(style)
        self.scene = AircraftScene(self.renderer)
        self._press = None
        self.interactor.AddObserver("LeftButtonPressEvent", self._on_press, 1.0)
        self.interactor.AddObserver("LeftButtonReleaseEvent", self._on_release, 1.0)
        axes = vtkAxesActor()
        self._marker = vtkOrientationMarkerWidget()
        self._marker.SetOrientationMarker(axes)
        self._marker.SetInteractor(self.interactor)
        self._marker.SetViewport(0.0, 0.0, 0.14, 0.2)
        self._first = True

        self.toolbar = QToolBar()
        mode_group = QActionGroup(self)
        for mode, label in ((ViewMode.SOLID, "Solid"), (ViewMode.TRANSPARENT, "Transparent"),
                            (ViewMode.WIREFRAME, "Wireframe"), (ViewMode.STRUCTURE, "Structure"),
                            (ViewMode.STRESS, "Stress")):
            act = QAction(label, self, checkable=True)
            act.setChecked(mode == ViewMode.SOLID)
            act.triggered.connect(lambda _=False, m=mode: self.set_mode(m))
            mode_group.addAction(act)
            self.toolbar.addAction(act)
        self.toolbar.addSeparator()
        self.layer_actions = {}
        for layer, label in ((Layer.SKIN, "Skins"), (Layer.CONTROLS, "Control surfaces"),
                             (Layer.STRUCTURE, "Structure members"), (Layer.ATTACHMENTS, "Fittings"),
                             (Layer.SYSTEMS, "Engines && gear")):
            act = QAction(label, self, checkable=True)
            act.setChecked(True)
            act.toggled.connect(lambda on, l=layer: self.set_layer(l, on))
            self.layer_actions[layer] = act
            self.toolbar.addAction(act)
        self.toolbar.addSeparator()
        for name in VIEWS:
            act = QAction(name.title(), self)
            act.triggered.connect(lambda _=False, n=name: self.view(n))
            self.toolbar.addAction(act)

        self.legend = QLabel(
            "<b>Stress view</b> — utilisation = ultimate stress / allowable at the gauged thickness: "
            "<span style='color:#3359d9'>■ &lt;0.25</span> <span style='color:#33b373'>■ 0.5</span> "
            "<span style='color:#f2d933'>■ 0.8</span> <span style='color:#f27326'>■ 1.0</span> "
            "<span style='color:#d91a1a'>■ &gt;1.1</span>  · grey = not analysed (tails use statistical weights)")
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet("padding: 3px 6px;")
        self.legend.hide()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.legend)
        lay.addWidget(self.vtk_widget, 1)

    def start(self) -> None:
        self.interactor.Initialize()
        self._marker.SetEnabled(1)
        self._marker.InteractiveOff()

    # ------------------------------------------------------------ scene
    def show_design(self, design: AircraftDesign) -> SyncStats:
        stats = self.scene.sync(design)
        if self._first and design.components:
            set_view(self.renderer, "iso")
            self._first = False
        self.render()
        return stats

    def clear(self) -> None:
        self.scene.clear()
        self._first = True
        self.render()

    def set_mode(self, mode: ViewMode) -> None:
        self.legend.setVisible(ViewMode(mode) == ViewMode.STRESS)
        self.scene.set_mode(mode)
        self.render()

    def set_utilisation(self, util: dict[str, float]) -> None:
        self.scene.set_utilisation(util)
        self.render()

    def set_layer(self, layer: Layer, on: bool) -> None:
        self.scene.set_layer(layer, on)
        self.render()

    def highlight(self, cid: str | None) -> None:
        self.scene.highlight(cid)
        self.render()

    def view(self, name: str) -> None:
        set_view(self.renderer, name)
        self.render()

    def render(self) -> None:
        self.renderer.ResetCameraClippingRange()
        self.vtk_widget.GetRenderWindow().Render()

    def screenshot(self, path: str) -> None:
        from vtkmodules.vtkIOImage import vtkPNGWriter
        from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
        w2i = vtkWindowToImageFilter()
        w2i.SetInput(self.vtk_widget.GetRenderWindow())
        w2i.ReadFrontBufferOff()
        w2i.Update()
        writer = vtkPNGWriter()
        writer.SetFileName(path)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()

    # ------------------------------------------------------------ picking (click without drag)
    def _on_press(self, obj, _event):
        self._press = obj.GetEventPosition()

    def _on_release(self, obj, _event):
        if self._press is None:
            return
        x, y = obj.GetEventPosition()
        px, py = self._press
        self._press = None
        if abs(x - px) + abs(y - py) > 4:
            return  # it was a camera drag
        picker = vtkPropPicker()
        picker.Pick(x, y, 0, self.renderer)
        cid = self.scene.component_for_actor(picker.GetActor())
        self.component_picked.emit(cid)

    def closeEvent(self, event):
        self.vtk_widget.Finalize()
        super().closeEvent(event)
