"""GUI smoke test.  Needs a display with OpenGL (on Linux CI: xvfb-run)."""
import os
import time

import pytest

if not os.environ.get("AF_GUI_TESTS"):
    pytest.skip("set AF_GUI_TESTS=1 to run GUI tests (needs a display)", allow_module_level=True)

import vtkmodules.qt  # noqa: E402

vtkmodules.qt.PyQtImpl = "PySide6"
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    QCoreApplication.setOrganizationName("AircraftFoundryTests")
    QCoreApplication.setApplicationName("AircraftFoundryTests")
    return QApplication.instance() or QApplication([])


def _wait(app, window, timeout=180):
    t0 = time.time()
    while window.task and window.task.running and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()


def test_generate_edit_pick_evolve_save(app, tmp_path):
    from aircraft_foundry.app.main_window import MainWindow
    from aircraft_foundry.core.project import Project
    from aircraft_foundry.portable import DataLocation
    w = MainWindow(DataLocation(tmp_path / "data", True, "test"))
    w.show()
    app.processEvents()
    w.mission_panel.count.setValue(4)
    w.mission_panel.samples.setValue(4)
    w.generate()
    _wait(app, w)
    assert w.gallery.rowCount() == 4 and w.design is not None
    assert len(w.viewport.scene.entries) == len(w.design.components)
    n_ribs = len([c for c in w.design.components if c.startswith("wing.right.rib.")])
    w.edit_parameter("wing.rib_pitch", 0.5)
    assert len([c for c in w.design.components if c.startswith("wing.right.rib.")]) > n_ribs
    assert set(w.viewport.scene.entries) == set(w.design.components)
    w._picked("wing.right.spar.front")
    assert "wing.right.spar.front" in w.inspector.details.toPlainText()
    w.gallery.selectRow(0)
    w.mission_panel.generations.setValue(2)
    w.mission_panel.population.setValue(6)
    rows = w.gallery.rowCount()
    w.evolve_selected()
    _wait(app, w)
    assert w.gallery.rowCount() > rows
    w.viewport.set_mode("stress")
    assert w.viewport.scene.utilisation  # structural results reach the renderer
    from PySide6.QtWidgets import QInputDialog
    QInputDialog.getText = staticmethod(lambda *a, **k: ("GUI test", True))
    w.save_to_library()
    assert w.library.count() == 1
    w.open_from_library(w.library.list()[0].id)
    assert w.candidate.name == "GUI test"
    w.fly()  # the Fly button opens the flight-test window on the active design
    assert w.sim_window.isVisible()
    w.sim_window.timer.stop()
    w.sim_window.close()
    w.project.path = tmp_path / "gui.afproj"
    assert w.save_project()
    assert len(Project.load(tmp_path / "gui.afproj").candidates) == w.gallery.rowCount()
    w.dirty = False
    w.close()


def test_flight_simulator_window(app):
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.core.evaluator import evaluate
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    from aircraft_foundry.simulator.window import SimulatorWindow
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    w = SimulatorWindow(AircraftDesign(p, "sim test"), m, evaluate(p, m))
    w.timer.stop()
    w.show()
    app.processEvents()
    c = w.sim.controls
    c.brakes, c.throttle = False, 1.0
    for i in range(600):  # 60 s: lift-off at ~30 s, then climb
        t = w.sim.telemetry()
        c.elevator = 0.5 if t["airspeed"] > 80 and t["pitch"] < 9 else 0.0
        w.advance(0.1)
        if i % 10 == 0:
            w.render_frame()  # the app draws ~60 frames/s; the map track is recorded per frame
    w.render_frame()
    assert w.sim.telemetry()["altitude"] > 50 and not w.sim.crashed
    assert "Short period" in w.modes_view.toPlainText()
    w.coach.update()
    w.minimap.repaint()
    assert w.coach.phase >= 1 and "Circuit" in w.coach_view.text()  # take-off ticked off
    assert len(w.trail) > 3  # moving-map track
    assert len(w.papi) == 4  # PAPI glide-slope lights
    assert any(kind == "control" for _, kind, _ in w.parts)
    w.restart("cruise")
    assert not w.sim.crashed and w.sim.telemetry()["altitude"] > 5000
    w.close()


def _send_held_key(app, widget, key, text, repeats=4):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    app.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text, False))
    for _ in range(repeats):  # holding the key: auto-repeat events
        app.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text, True))
    app.processEvents()


def _representations(renderer):
    return {a.GetProperty().GetRepresentation() for a in renderer.GetActors()}


def test_held_keys_do_not_trigger_vtk_shortcuts(app):
    """Regression: holding W (throttle) switched the whole simulator to wireframe (VTK's 'w' shortcut)."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.core.evaluator import evaluate
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    from aircraft_foundry.simulator.window import SimulatorWindow
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    w = SimulatorWindow(AircraftDesign(p, "keys"), m, evaluate(p, m))
    w.timer.stop()
    w.show()
    w.interactor.Initialize()
    app.processEvents()
    surface = _representations(w.renderer)
    assert surface == {2}  # all solid surfaces
    throttle0 = w.sim.controls.throttle
    _send_held_key(app, w.vtk_widget, Qt.Key_W, "w")
    for _ in range(10):
        w._update_controls(0.05)
    assert w.sim.controls.throttle > throttle0  # the key still works as throttle
    app.sendEvent(w.vtk_widget, QKeyEvent(QEvent.KeyRelease, Qt.Key_W, Qt.NoModifier, "w", False))
    for key, text in ((Qt.Key_S, "s"), (Qt.Key_Q, "q"), (Qt.Key_E, "e"), (Qt.Key_R, "r"), (Qt.Key_F, "f")):
        _send_held_key(app, w.vtk_widget, key, text)
        app.sendEvent(w.vtk_widget, QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier, text, False))
    app.processEvents()
    assert _representations(w.renderer) == surface  # no wireframe / points switch
    assert w.isVisible() and w.interactor.GetInitialized()  # q / e did not exit the view
    w.close()


def test_design_viewport_ignores_vtk_shortcuts(app, tmp_path):
    from PySide6.QtCore import Qt
    from aircraft_foundry.app.main_window import MainWindow
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.portable import DataLocation
    w = MainWindow(DataLocation(tmp_path / "d", False, "test"))
    w.show()
    app.processEvents()
    w.viewport.show_design(AircraftDesign())
    before = _representations(w.viewport.renderer)
    for key, text in ((Qt.Key_W, "w"), (Qt.Key_E, "e"), (Qt.Key_Q, "q")):
        _send_held_key(app, w.viewport.vtk_widget, key, text)
    assert _representations(w.viewport.renderer) == before  # only the toolbar changes the display mode
    assert w.viewport.interactor.GetInitialized()
    w.dirty = False
    w.close()
