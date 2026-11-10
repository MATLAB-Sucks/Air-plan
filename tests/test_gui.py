"""GUI smoke test.  Needs a display with OpenGL (on Linux CI: xvfb-run)."""
import numpy as np
from PySide6.QtWidgets import QDockWidget
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
    w.fly()  # the Fly button opens the flight-test page on the active design (centre + right panel)
    assert w.page_keys[w.nav.currentRow()] == "simulator" and w.centre.currentWidget() is w.sim_window.embedded_centre
    assert w.sim_window.timer.isActive()
    w.show_page("mission")  # leaving the page pauses the simulator and restores the main 3-D view
    assert not w.sim_window.timer.isActive() and w.centre.currentWidget() is w.viewport
    w.open_wind_tunnel()
    assert w.page_keys[w.nav.currentRow()] == "tunnel"
    w.open_studio()
    assert w.page_keys[w.nav.currentRow()] == "studio"
    assert len([d for d in w.findChildren(QDockWidget) if d.isVisible()]) == 2  # only two side panels
    w.show_page("mission")
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


def test_design_studio_drag_and_verdict(app):
    from PySide6.QtCore import QPointF
    from aircraft_foundry.app.design_studio import DesignStudioWindow, Handle
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.show()
    app.processEvents()
    assert w.verdict.flies and w.fly_btn.isEnabled()
    tip = [h for h in w.top.scene().items() if isinstance(h, Handle) and "wing span" in h.toolTip()][0]
    span0 = w.params.wing.span
    tip.setPos(tip.pos() + QPointF(1.0, 3.0))
    tip.on_release()
    w.refresh_analysis()
    assert w.params.wing.span > span0 + 3  # dragging the tip stretched the wing
    tip.setPos(QPointF(tip.pos().x(), 900.0))  # absurd drag: clamped to the valid range, no crash
    assert w.params.wing.span <= 80.0
    got = []
    w.send_to_project.connect(got.append)
    w.add_btn.click()
    assert got and got[0].wing.span == w.params.wing.span
    w.set_config("tail_type", "v_tail")
    w.refresh_analysis()
    assert w.params.configuration.tail_type == "v_tail" and w.verdict is not None
    w.close()


def test_wind_tunnel_window(app):
    from aircraft_foundry.app.wind_tunnel import WindTunnelWindow
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.core.evaluator import evaluate
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    w = WindTunnelWindow(AircraftDesign(p, "wt"), m, evaluate(p, m))
    w.show()
    app.processEvents()
    assert w.wait_for_streamlines(120) and len(w.particles) > 20
    assert "CL" in w.readout.text() and "fails at" in w.readout.text()
    w.colour.setCurrentIndex(1)
    w.sliders["n"][0].setValue(int(5.0 / 0.1))
    w.recompute()
    wing = [pp for pp in w.parts if pp["id"].startswith("wing.right")][0]
    vals = np.array([wing["poly"].GetPointData().GetScalars().GetValue(i)
                     for i in range(wing["poly"].GetNumberOfPoints())])
    assert vals.max() > 1.0  # above the ultimate load, part of the wing shows as failed
    w.close()


def test_studio_stunt_plane_button(app):
    from aircraft_foundry.app.design_studio import DesignStudioWindow
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.show()
    app.processEvents()
    w.set_layout("aerobatic")
    w.refresh_analysis()
    assert w.mission.category == "aerobatic" and w.params.structure.limit_load_factor == 10.0
    assert w.verdict.flies
    w.close()


def test_studio_parts_drop_select_strength(app):
    from aircraft_foundry.app.design_studio import DesignStudioWindow, Handle
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.show()
    app.processEvents()

    def drop(kind, view, pick=lambda n: True):
        node = [n for n in w.nodes_for(kind, view) if pick(n)][0]
        w.drop_part(kind, node, view)
        for _ in range(4):
            app.processEvents()
        w.refresh_analysis()

    assert len(w.side.scene().items()) and w.selected == "wing"
    drop("jet_pair", "top", lambda n: n[1][0] == "rear")
    assert w.params.configuration.engine_layout == "rear_fuselage"
    w.balance()
    w.refresh_analysis()
    assert w.verdict.evaluation.metrics["static_margin"] > 0.02  # balanced again
    drop("wing", "side", lambda n: n[1] == ("wing_z", "high"))
    assert w.params.configuration.wing_position == "high"
    drop("t_tail", "top")
    assert w.params.configuration.tail_type == "t_tail"
    w.select("htail")
    w.remove_selected()
    assert w.params.configuration.layout == "flying_wing"
    drop("canard", "top")
    assert w.params.configuration.layout == "canard" and "canard" in w.parts_present()
    w.select("wing")
    app.processEvents()
    assert sum(isinstance(i, Handle) for i in w.top.scene().items()) > 3  # gizmos for the selected part only
    w.select("engines")
    assert sum(isinstance(i, Handle) for i in w.top.scene().items()) <= 1
    w.select("wing")
    w.set_value("structure.wing_strength", 2.0)
    w.refresh_analysis()
    assert "Breaks at" in w.strength_readout.text()
    w.top.show_nodes("jet_pair", w.nodes_for("jet_pair", "top")[0][0])
    assert len(w.top._node_items) == len(w.nodes_for("jet_pair", "top"))  # attachment nodes light up
    w.close()


def test_requirement_checklist_round_trip(app):
    from aircraft_foundry.app.mission_panel import MissionPanel
    from aircraft_foundry.core.mission import MissionRequirements
    p = MissionPanel()
    p.set_mission(MissionRequirements(ignore="airport_noise,cabin_noise", g_limit=4.0))
    assert not p.req_boxes["airport_noise"].isChecked() and p.req_boxes["span"].isChecked()
    m = p.mission()
    assert set(m.ignore.split(",")) == {"airport_noise", "cabin_noise"} and m.g_limit == 4.0


def test_parts_bin_really_drags_and_views_accept_drops(app):
    """Regression: QListView.setMovement(Static) had silently disabled dragging from the parts bin."""
    from PySide6.QtCore import QMimeData, QPoint, Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtWidgets import QAbstractItemView
    from aircraft_foundry.app.design_studio import MIME, DesignStudioWindow
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.show()
    app.processEvents()
    assert w.bin.dragEnabled() and w.bin.dragDropMode() == QAbstractItemView.DragOnly
    node = [n for n in w.nodes_for("jet_pair", "top") if n[1][0] == "rear"][0][0]
    pos = w.top.mapFromScene(node)
    md = QMimeData()
    md.setData(MIME, b"jet_pair")
    vp = w.top.viewport()
    app.sendEvent(vp, QDragEnterEvent(pos, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
    app.sendEvent(vp, QDropEvent(pos.toPointF(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
    app.processEvents()
    assert w.params.configuration.engine_layout == "rear_fuselage"  # the drop reached the editor and attached
    w.close()


def test_pages_follow_the_selected_aircraft(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from aircraft_foundry.app.main_window import MainWindow
    from aircraft_foundry.core.synthesis import baseline, configurations_for
    from aircraft_foundry.portable import DataLocation
    w = MainWindow(DataLocation(tmp_path / "d", False, "test"))
    w.show()
    app.processEvents()
    cfgs = configurations_for(w.project.mission)
    w.add_studio_design(baseline(w.project.mission, cfgs[0]))
    a = w.candidate
    w.add_studio_design(baseline(w.project.mission, cfgs[2]))
    b = w.candidate
    assert a.id != b.id and w.candidate is b
    w.show_page("studio")  # opens by itself, for the selected aircraft
    assert w.tool_windows["studio"].source_key[0] == b.id and b.name in w.pages.currentWidget().head.text()
    w.activate_candidate(a.id)  # choose another plane while the studio is showing
    studio = w.tool_windows["studio"]
    assert studio.source_key[0] == a.id
    w.show_page("simulator")  # built automatically for the selected plane
    assert w.tool_windows["simulator"].source_key[0] == a.id
    w.activate_candidate(b.id)
    assert w.tool_windows["simulator"].source_key[0] == b.id and w.sim_window.design.name == b.name
    # edit in the studio and apply to the selected plane
    w.activate_candidate(a.id)
    w.show_page("studio")
    studio = w.tool_windows["studio"]
    span = studio.params.wing.span + 2.0
    studio.set_value("wing.span", span)
    assert studio.modified() and studio.apply_btn.isEnabled()
    studio.apply_btn.click()
    assert a.params.wing.span == pytest.approx(span) and not w.tool_windows["studio"].modified()
    # unapplied changes when switching plane: the user is asked (here: discard)
    w.tool_windows["studio"].set_value("wing.span", span + 1.0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a_, **k: QMessageBox.Discard)
    w.activate_candidate(b.id)
    assert a.params.wing.span == pytest.approx(span) and w.tool_windows["studio"].source_key[0] == b.id
    # parameter edits make the tool pages rebuild when next shown
    w.show_page("simulator")
    before = w.sim_window
    w.show_page("parameters")
    w.edit_parameter("wing.span", b.params.wing.span + 1.5)
    w.show_page("simulator")
    assert w.sim_window is not before
    w.dirty = False
    w.close()


@pytest.mark.skipif(__import__("shutil").which("xdotool") is None, reason="needs xdotool for a real mouse drag")
def test_real_mouse_drag_from_parts_bin(app):
    """Drags a part with the actual mouse (X11 drag-and-drop), not by calling the drop handler."""
    import subprocess
    from PySide6.QtCore import QEventLoop, QTimer
    from aircraft_foundry.app.design_studio import DesignStudioWindow
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.resize(1500, 900)
    w.move(0, 0)
    w.show()
    for _ in range(20):
        app.processEvents()
    item = [w.bin.item(i) for i in range(w.bin.count()) if w.bin.item(i).data(256) == "jet_pair"][0]
    src = w.bin.viewport().mapToGlobal(w.bin.visualItemRect(item).center())
    node = [n for n in w.nodes_for("jet_pair", "top") if n[1][0] == "rear"][0][0]
    dst = w.top.viewport().mapToGlobal(w.top.mapFromScene(node))
    path = " ".join(f"mousemove {int(src.x() + (dst.x() - src.x()) * k / 12)} "
                    f"{int(src.y() + (dst.y() - src.y()) * k / 12)} sleep 0.05" for k in range(1, 13))
    subprocess.Popen(f"xdotool mousemove {src.x()} {src.y()} sleep 0.2 mousedown 1 sleep 0.2 "
                     f"mousemove {src.x() + 20} {src.y() + 20} sleep 0.1 {path} sleep 0.4 mouseup 1", shell=True)
    loop = QEventLoop()
    QTimer.singleShot(6000, loop.quit)
    loop.exec()
    assert w.params.configuration.engine_layout == "rear_fuselage"
    w.close()


def test_without_opengl_the_app_explains_instead_of_crashing(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel, QMessageBox
    from aircraft_foundry.app import viewport as vp
    from aircraft_foundry.app.main_window import MainWindow
    from aircraft_foundry.portable import DataLocation
    monkeypatch.setattr(vp.Viewport, "gl_ok", False)  # as on a VM / Remote Desktop with OpenGL 1.1 only
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    w = MainWindow(DataLocation(tmp_path / "d", False, "test"))
    w.show()
    app.processEvents()
    assert getattr(w.viewport, "disabled", False) and not w.viewport.vtk_widget.isVisible()
    assert any("OpenGL 3.2" in lbl.text() for lbl in w.viewport.findChildren(QLabel))
    from aircraft_foundry.core.synthesis import baseline, configurations_for
    w.add_studio_design(baseline(w.project.mission, configurations_for(w.project.mission)[0]))  # still works
    w.fly()
    assert warned and "3-D view unavailable" in warned[-1]
    w.dirty = False
    w.close()


def test_collapsible_panels_and_full_screen(app, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    from aircraft_foundry.app.main_window import MainWindow
    from aircraft_foundry.core.synthesis import baseline, configurations_for
    from aircraft_foundry.portable import DataLocation
    w = MainWindow(DataLocation(tmp_path / "d", False, "test"))
    w.show()
    app.processEvents()
    w.add_studio_design(baseline(w.project.mission, configurations_for(w.project.mission)[0]))
    assert w.gallery_dock.isVisible() and w.right_dock.isVisible()
    w.left_toggle.trigger()  # collapse the aircraft list
    app.processEvents()
    assert not w.gallery_dock.isVisible() and w.right_dock.isVisible()
    w.left_toggle.trigger()
    assert w.gallery_dock.isVisible()
    w.right_toggle.trigger()
    assert not w.right_dock.isVisible()  # collapsed before going full screen
    w.show_page("simulator")
    w.fullscreen_action.trigger()  # F11
    app.processEvents()
    assert w.isFullScreen() and not w.gallery_dock.isVisible() and not w.panels_toolbar.isVisible()
    assert not w.statusBar().isVisible() and not w.menuBar().isVisible()
    assert w.centre.currentWidget() is w.sim_window.embedded_centre  # only the flight view
    w.exit_fullscreen_action.trigger()  # Esc
    app.processEvents()
    assert not w.isFullScreen() and w.gallery_dock.isVisible() and not w.right_dock.isVisible()  # as before
    assert w.panels_toolbar.isVisible() and w.statusBar().isVisible()
    w.right_toggle.trigger()
    w.gallery_dock.hide()
    w.dirty = False
    w.close()
    w2 = MainWindow(DataLocation(tmp_path / "d", False, "test"))  # panels are remembered
    assert w2.gallery_dock.isHidden() and not w2.right_dock.isHidden()
    w2.dirty = False
    w2.close()


def test_crash_breaks_the_aircraft_apart_with_a_debrief(app):
    import re
    from aircraft_foundry.core.design import AircraftDesign
    from aircraft_foundry.core.evaluator import evaluate
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    from aircraft_foundry.simulator.window import SimulatorWindow
    m = MissionRequirements()
    p = baseline(m, _C("turbofan", "wing", "conventional", "low"))
    w = SimulatorWindow(AircraftDesign(p, "crash"), m, evaluate(p, m))
    w.timer.stop()
    w.show()
    rw = w.terrain.home
    n, e = rw.point(2500)
    w.sim.start_in_flight(150, rw.elevation + 400, rw.heading_deg, north=n, east=e)
    w.sim.controls.elevator = -1.0
    for _ in range(400):
        w.advance(0.05)
        if w.sim.crashed:
            break
    w.render_frame()
    for _ in range(300):
        w._update_debris(1 / 60)
    assert w.sim.crashed and len(w.debris) > 10
    for d in w.debris:  # every piece is on or above the terrain
        assert d["M"][2, 3] >= w.sim.ground(d["M"][1, 3], d["M"][0, 3]) + 0.2
    w.coach.update()
    text = re.sub("<[^>]+>", " ", w.coach_view.text())
    assert "Next time" in text and "Fix the design" in text
    w.restart("runway")
    assert not w.debris and not w.sim.crashed
    w.close()


def test_studio_glider_button(app):
    from aircraft_foundry.app.design_studio import DesignStudioWindow
    from aircraft_foundry.core.mission import MissionRequirements
    from aircraft_foundry.core.synthesis import _C, baseline
    m = MissionRequirements()
    w = DesignStudioWindow(baseline(m, _C("turbofan", "wing", "conventional", "low")), m)
    w.show()
    app.processEvents()
    w.set_layout("glider")
    for _ in range(3):
        app.processEvents()
    w.refresh_analysis()
    assert w.mission.category == "glider" and w.params.configuration.propulsion == "none"
    assert w.verdict.flies and "engines" not in w.parts_present()
    w.close()
