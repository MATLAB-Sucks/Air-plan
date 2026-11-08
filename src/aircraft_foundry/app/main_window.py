"""Main window: wires the GUI panels to the design engine.

The window owns no engineering logic.  It holds the Project, the active
AircraftDesign, and forwards edits to the engine, then pushes the resulting
ChangeSet to the renderer and re-evaluation to the gallery and inspector.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..core.design import AircraftDesign
from ..core.generator import Candidate, EvolutionSettings, evolve, generate_candidates
from ..core.project import SUFFIX, Project
from ..core.synthesis import complete_design
from ..fileio.exporter import run_export
from ..fileio.json_io import import_design_json
from ..library import DesignLibrary, LibraryReadOnly
from ..portable import DataLocation, host_description, locate
from .library_panel import LibraryPanel
from .settings import configure as configure_settings
from .settings import settings as app_settings
from .structures_panel import StructuresPanel
from .thumbnails import planform_pixmap
from .export_dialog import ExportDialog
from .gallery import CandidateGallery
from .inspector import Inspector
from .mission_panel import MissionPanel
from .parameter_editor import ParameterEditor
from .tasks import Task
from .viewport import Viewport


class MainWindow(QMainWindow):
    def __init__(self, location: DataLocation | None = None):
        super().__init__()
        self.location = (location or locate()).ensure()
        configure_settings(str(self.location.settings_path))
        self.library = DesignLibrary(self.location.library_path, host_description())
        self.setWindowTitle("Aircraft Foundry")
        self.resize(1500, 950)
        self.project = Project()
        self.design: AircraftDesign | None = None
        self.candidate: Candidate | None = None
        self.selected_component: str | None = None
        self.task: Task | None = None
        self.dirty = False

        # ---------------- layout: left = generated aircraft, centre = view, right = one switchable panel
        self.viewport = Viewport(self)
        self.centre = QStackedWidget()
        self.centre.addWidget(self.viewport)
        self.setCentralWidget(self.centre)
        for label, tip, slot in (("✈ Fly", "Flight-test the selected design (F5)", lambda: self.fly()),
                                 ("🛠 Studio", "Build or reshape a design by dragging parts (F6)", self.open_studio),
                                 ("🌬 Wind tunnel", "Airflow, pressure and stress on the design (F7)",
                                  self.open_wind_tunnel)):
            act_ = self.viewport.toolbar.addAction(label)
            act_.setToolTip(tip)
            act_.triggered.connect(slot)

        self.gallery = CandidateGallery()
        self.gallery.set_compact(True)
        self.gallery_dock = self._dock("Generated aircraft", self.gallery, Qt.LeftDockWidgetArea)
        self.mission_panel = MissionPanel()
        self.inspector = Inspector()
        self.param_editor = ParameterEditor()
        self.structures = StructuresPanel()
        where = "Portable (USB)" if self.location.portable else "This computer"
        self.library_panel = LibraryPanel(self.library, f"{where}: {self.location.root}")
        self.pages = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(150)
        self.page_keys: list[str] = []
        self.tool_windows: dict[str, object] = {}  # studio / wind tunnel / simulator behind their pages
        for key, title, widget in (("mission", "🎯  Mission", self.mission_panel),
                                   ("parameters", "🔧  Parameters", self.param_editor),
                                   ("inspector", "🔍  Inspector", self.inspector),
                                   ("structures", "🏗  Structures", self.structures),
                                   ("library", "📚  Library", self.library_panel),
                                   ("studio", "🛠  Design studio", self._placeholder("studio")),
                                   ("tunnel", "🌬  Wind tunnel", self._placeholder("tunnel")),
                                   ("simulator", "✈  Flight simulator", self._placeholder("simulator"))):
            self.nav.addItem(title)
            self.pages.addWidget(self._page(title.split("  ", 1)[1], widget))
            self.page_keys.append(key)
        self.nav.currentRowChanged.connect(self._page_changed)
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.nav)
        rl.addWidget(self.pages, 1)
        self.right_dock = self._dock("Workspace", right, Qt.RightDockWidgetArea)
        self.right_dock.setMinimumWidth(560)
        for d in (self.gallery_dock, self.right_dock):
            d.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.gallery_dock.setMinimumWidth(300)
        self.nav.setCurrentRow(0)

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        self.status_label = QLabel("Define a mission and press Generate.")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        data_label = QLabel(("💾 Portable data: " if self.location.portable else "Data: ") + str(self.location.root))
        data_label.setToolTip(f"Found via {self.location.source}")
        self.statusBar().addPermanentWidget(data_label)

        self._build_menus()
        self.mission_panel.generate_requested.connect(self.generate)
        self.mission_panel.evolve_requested.connect(self.evolve_selected)
        self.mission_panel.cancel_requested.connect(self.cancel_task)
        self.mission_panel.mission_changed.connect(self._mark_dirty)
        self.gallery.candidate_activated.connect(self.activate_candidate)
        self.inspector.component_selected.connect(self.select_component)
        self.viewport.component_picked.connect(self._picked)
        self.param_editor.parameter_changed.connect(self.edit_parameter)
        self.param_editor.complete_requested.connect(self.rederive_gear)
        self.library_panel.open_requested.connect(self.open_from_library)
        self.library_panel.save_requested.connect(self.save_to_library)
        self._restore_settings()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_started", False):
            self._started = True
            self.viewport.start()

    # ------------------------------------------------------------ ui scaffolding
    # ------------------------------------------------------------ pages
    def _page(self, title: str, widget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 6, 10, 6)
        head = QLabel(title)
        head.setObjectName("pageTitle")
        lay.addWidget(head)
        lay.addWidget(widget, 1)
        page.body = widget
        return page

    def _placeholder(self, key: str) -> QWidget:
        text = {"studio": "Select a generated aircraft (left) — or open the studio to start a new design.",
                "tunnel": "Select a generated aircraft whose weights close to put it in the wind tunnel.",
                "simulator": "Select a generated aircraft whose weights close to fly it."}[key]
        w = QWidget()
        lay = QVBoxLayout(w)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setObjectName("hint")
        lay.addWidget(lbl)
        btn = QPushButton({"studio": "Open the design studio", "tunnel": "Open the wind tunnel",
                           "simulator": "Fly the selected aircraft"}[key])
        btn.setObjectName("primary")
        btn.clicked.connect({"studio": self.open_studio, "tunnel": self.open_wind_tunnel,
                             "simulator": lambda: self.fly()}[key])
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    def show_page(self, key: str) -> None:
        self.nav.setCurrentRow(self.page_keys.index(key))

    def _page_changed(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        key = self.page_keys[row]
        tool = self.tool_windows.get(key)
        self.centre.setCurrentWidget(tool.embedded_centre if tool is not None else self.viewport)
        sim = self.tool_windows.get("simulator")
        if sim is not None:  # the simulator only runs while its page is showing
            (sim.timer.start(16) if key == "simulator" else sim.timer.stop())
            if key == "simulator":
                sim.vtk_widget.setFocus()

    def embed_tool(self, key: str, window) -> None:
        """Show a tool (QMainWindow with one central view and side docks) as a page of this window."""
        old = self.tool_windows.pop(key, None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
            self.centre.removeWidget(old.embedded_centre)
            old.embedded_centre.deleteLater()
        centre = window.takeCentralWidget()
        docks = window.findChildren(QDockWidget)
        if len(docks) == 1:
            side = docks[0].widget()
        else:
            side = QTabWidget()
            for d in docks:
                side.addTab(d.widget(), d.windowTitle())
        window.embedded_centre = centre
        self.centre.addWidget(centre)
        idx = self.page_keys.index(key)
        page = self.pages.widget(idx)
        page.layout().removeWidget(page.body)
        page.body.hide()  # hide and detach now: deleteLater only runs on a later event-loop pass
        page.body.setParent(None)
        page.body.deleteLater()
        page.body = side
        page.layout().addWidget(side, 1)
        self.tool_windows[key] = window
        if self.nav.currentRow() == idx:
            self._page_changed(idx)
        else:
            self.show_page(key)

    def _dock(self, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(title)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _build_menus(self):
        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        fm = self.menuBar().addMenu("&File")
        act(fm, "New project", self.new_project, QKeySequence.New)
        act(fm, "Open project…", self.open_project, QKeySequence.Open)
        act(fm, "Save project", self.save_project, QKeySequence.Save)
        act(fm, "Save project as…", lambda: self.save_project(as_new=True), QKeySequence.SaveAs)
        fm.addSeparator()
        act(fm, "Import design JSON…", self.import_design)
        act(fm, "Export…", self.export, "Ctrl+E")
        act(fm, "Save viewport image…", self.save_screenshot)
        fm.addSeparator()
        act(fm, "Quit", self.close, QKeySequence.Quit)
        lm = self.menuBar().addMenu("&Library")
        act(lm, "Save current design to library…", self.save_to_library, "Ctrl+L")
        act(lm, "Show library", lambda: self.show_page("library"))
        sm = self.menuBar().addMenu("&Simulate")
        act(sm, "✈ Fly this design (flight test)…", self.fly, "F5")
        act(sm, "✈ Fly from cruise (trimmed)…", lambda: self.fly("cruise"))
        sm.addSeparator()
        act(sm, "🛠 Design studio (drag and drop)…", self.open_studio, "F6")
        act(sm, "🌬 Wind tunnel…", self.open_wind_tunnel, "F7")
        vm = self.menuBar().addMenu("&View")
        for name in ("iso", "front", "side", "top", "rear"):
            act(vm, f"{name.title()} view", lambda _=False, n=name: self.viewport.view(n))
        hm = self.menuBar().addMenu("&Help")
        act(hm, "About Aircraft Foundry", self.about)

    def _set_busy(self, busy: bool, text: str = ""):
        self.mission_panel.set_busy(busy)
        self.progress.setVisible(busy)
        self.progress.setValue(0)
        if text:
            self.status_label.setText(text)

    def _mark_dirty(self, *_):
        self.dirty = True
        name = self.project.path.name if self.project.path else "Untitled"
        self.setWindowTitle(f"Aircraft Foundry — {name} •")

    def _mark_clean(self):
        self.dirty = False
        name = self.project.path.name if self.project.path else "Untitled"
        self.setWindowTitle(f"Aircraft Foundry — {name}")

    def _run(self, fn, on_done, label: str):
        if self.task and self.task.running:
            return
        self.task = Task(fn, self)
        self.task.progress.connect(lambda f, m: (self.progress.setValue(int(f * 1000)), self.status_label.setText(m)))
        self.task.finished.connect(lambda r: (self._set_busy(False), on_done(r)))
        self.task.failed.connect(self._task_failed)
        self._set_busy(True, label)
        self.task.start()

    def _task_failed(self, msg: str):
        self._set_busy(False, "Failed")
        QMessageBox.critical(self, "Aircraft Foundry", msg.split("\n\n")[0], QMessageBox.Ok)

    def cancel_task(self):
        if self.task:
            self.task.cancel()
            self.status_label.setText("Cancelling…")

    # ------------------------------------------------------------ generation & evolution
    def generate(self):
        mission = self.mission_panel.mission()
        problems = mission.validate()
        if problems:
            QMessageBox.warning(self, "Mission", "\n".join(problems))
            return
        mp = self.mission_panel
        count, seed, samples = mp.count.value(), mp.seed.value(), mp.samples.value()

        def job(progress, cancel):
            return generate_candidates(mission, count, seed, samples, progress=progress, cancel=cancel)

        def done(cands):
            self.project.mission = mission
            self.project.candidates = [c for c in self.project.candidates if c.origin.startswith("evolved")] + cands
            self.gallery.set_candidates(self.project.candidates)
            self.status_label.setText(f"{len(cands)} candidates generated "
                                      f"({sum(c.evaluation.feasible for c in cands)} feasible)")
            if cands:
                self.activate_candidate(cands[0].id)
            self._mark_dirty()

        self._run(job, done, "Sizing configurations…")

    def evolve_selected(self):
        ids = self.gallery.selected_ids() or ([self.candidate.id] if self.candidate else [])
        seeds = [c for c in (self.project.candidate(i) for i in ids) if c and c.evaluation and c.evaluation.converged]
        if not seeds:
            QMessageBox.information(self, "Evolve", "Select one or more converged candidates in the gallery.")
            return
        mission = self.project.mission
        mp = self.mission_panel
        settings = EvolutionSettings(generations=mp.generations.value(), population=mp.population.value(),
                                     seed=mp.seed.value())

        def job(progress, cancel):
            return evolve(seeds, mission, settings, progress, cancel)

        def done(new):
            self.project.candidates += new
            for c in new:
                self.gallery.update_candidate(c)
            best = min((c.evaluation.score for c in new), default=None)
            self.status_label.setText(f"Evolution produced {len(new)} candidates"
                                      + (f", best score {best:,.0f}" if best else ""))
            if new:
                self.activate_candidate(new[0].id)
            self._mark_dirty()

        self._run(job, done, f"Evolving {len(seeds)} design(s)…")

    # ------------------------------------------------------------ active design
    def activate_candidate(self, cid: str):
        c = self.project.candidate(cid)
        if c is None:
            return
        self.candidate = c
        self.project.active_id = cid
        if self.design is None:
            self.design = AircraftDesign(c.params, c.name or c.id)
        else:  # reuse the design object so the renderer only updates what differs
            self.design.name = c.name or c.id
            self.design.replace_parameters(c.params)
        if c.evaluation is None:
            c.evaluate(self.project.mission)
        stats = self.viewport.show_design(self.design)
        self.param_editor.set_parameters(self.design.params)
        self.inspector.set_design(self.design, c.evaluation)
        self._show_structure(c.evaluation)
        self.gallery.mark_active(cid)
        self.status_label.setText(f"{c.name}: {c.evaluation.message} — {len(self.design.components)} components "
                                  f"({stats.added + stats.geometry_updated} rebuilt, {stats.reused} reused)")

    def edit_parameter(self, path: str, value):
        if self.design is None:
            return
        try:
            cs = self.design.set_parameter(path, value)
        except ValueError as exc:
            QMessageBox.warning(self, "Parameter", str(exc))
            self.param_editor.set_parameters(self.design.params)
            return
        self._commit_design_change(f"{path} = {value}: {len(cs.added)} added, {len(cs.updated)} updated, "
                                   f"{len(cs.removed)} removed, {cs.unchanged} unchanged "
                                   f"(ran {', '.join(cs.generators_run)})")

    def rederive_gear(self):
        if self.design is None:
            return
        p = complete_design(self.design.params.copy(), self.project.mission)
        self.design.replace_parameters(p)
        self.param_editor.set_parameters(self.design.params)
        self._commit_design_change("Landing gear re-derived from CG and clearances")

    def _commit_design_change(self, message: str):
        c = self.candidate
        c.params = self.design.params.copy()
        if "edited" not in c.origin:
            c.origin += " (edited)"
        c.evaluate(self.project.mission)
        self.viewport.show_design(self.design)
        self.inspector.set_design(self.design, c.evaluation)
        self._show_structure(c.evaluation)
        self.gallery.update_candidate(c)
        self.gallery.mark_active(c.id)
        self.status_label.setText(f"{message} — {c.evaluation.message}")
        self._mark_dirty()

    def _show_structure(self, ev):
        self.structures.set_evaluation(ev)
        self.viewport.set_utilisation(ev.structure.utilisation if ev is not None and ev.structure else {})

    # ------------------------------------------------------------ shared library
    def _thumbnail_png(self, c: Candidate) -> bytes:
        pm = planform_pixmap(c.params, feasible=bool(c.evaluation and c.evaluation.feasible))
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "PNG")
        return bytes(data)

    def save_to_library(self):
        c = self.candidate
        if c is None:
            QMessageBox.information(self, "Library", "Load or generate a design first.")
            return
        name, ok = QInputDialog.getText(self, "Save to library", "Design name:", text=c.name or "Design")
        if not ok:
            return
        try:
            self.library.save(c.params, self.project.mission, name, configuration=c.config_label,
                              evaluation=c.evaluation, thumbnail_png=self._thumbnail_png(c), origin=c.origin)
        except LibraryReadOnly as exc:
            QMessageBox.warning(self, "Library", f"{exc}.\nIs the USB stick write-protected?")
            return
        except Exception as exc:  # stick removed etc.
            QMessageBox.critical(self, "Library", f"Could not save: {exc}")
            return
        self.library_panel.refresh()
        self.status_label.setText(f"Saved '{name}' to the library ({self.library.path})")

    def open_from_library(self, design_id: str):
        try:
            params, mission, entry = self.library.load(design_id)
        except Exception as exc:
            QMessageBox.critical(self, "Library", f"Could not open design: {exc}")
            return
        if not self.project.candidates:
            self.project.mission = mission
            self.mission_panel.set_mission(mission)
        c = Candidate(params, name=entry.name, origin=f"library ({entry.created_on})")
        c.evaluate(self.project.mission)
        self.project.candidates.append(c)
        self.gallery.update_candidate(c)
        self.activate_candidate(c.id)
        self._mark_dirty()

    def open_studio(self):
        """Design studio page, starting from the active design (or a fresh one for the current mission)."""
        from .design_studio import DesignStudioWindow
        c = self.candidate
        if c is not None:
            params = c.params
        else:
            from ..core.synthesis import baseline, configurations_for
            params = baseline(self.project.mission, configurations_for(self.project.mission)[0])
        studio = DesignStudioWindow(params, self.project.mission, None,
                                    name=(c.name + " (studio)") if c is not None else "Studio design")
        studio.send_to_project.connect(self.add_studio_design)
        for btn, slot in ((studio.fly_btn, lambda: self._open_sim(studio.design, studio.mission,
                                                                   studio.verdict.evaluation)),
                          (studio.wt_btn, lambda: self._open_tunnel(studio.design, studio.mission,
                                                                     studio.verdict.evaluation))):
            btn.clicked.disconnect()
            btn.clicked.connect(slot)
        self.studio = studio
        self.embed_tool("studio", studio)

    def add_studio_design(self, params):
        n = sum(1 for c in self.project.candidates if c.origin == "design studio") + 1
        c = Candidate(params, name=f"Studio design {n}", origin="design studio")
        c.evaluate(self.project.mission)
        self.project.candidates.append(c)
        self.gallery.update_candidate(c)
        self.activate_candidate(c.id)
        self._mark_dirty()
        self.status_label.setText(f"Added '{c.name}' from the design studio to the project")

    def open_wind_tunnel(self):
        c = self.candidate
        if self.design is None or c is None or c.evaluation is None or not c.evaluation.converged:
            self.show_page("tunnel")
            self.status_label.setText("Select a generated aircraft whose weights close to use the wind tunnel.")
            return
        self._open_tunnel(self.design, self.project.mission, c.evaluation)

    def _open_tunnel(self, design, mission, evaluation):
        from .wind_tunnel import WindTunnelWindow
        if evaluation is None or not evaluation.converged:
            return
        self.wt_window = WindTunnelWindow(design, mission, evaluation, None)
        self.embed_tool("tunnel", self.wt_window)

    def _open_sim(self, design, mission, evaluation, start: str = "runway"):
        from ..simulator.window import SimulatorWindow
        if evaluation is None or not evaluation.converged:
            return
        try:
            self.sim_window = SimulatorWindow(design, mission, evaluation, None, start)
        except Exception as exc:
            QMessageBox.critical(self, "Flight test", f"Could not build a flight model for this design:\n{exc}")
            return
        self.sim_window.timer.stop()
        self.embed_tool("simulator", self.sim_window)

    def fly(self, start: str = "runway"):
        """Open the flight-test simulator page with the active design."""
        c = self.candidate
        if self.design is None or c is None or c.evaluation is None or not c.evaluation.converged:
            self.show_page("simulator")
            self.status_label.setText("Select a generated aircraft whose weights close to fly it.")
            return
        self._open_sim(self.design, self.project.mission, c.evaluation, start)

    def select_component(self, cid: str | None):
        self.selected_component = cid
        self.viewport.highlight(cid)

    def _picked(self, cid):
        self.selected_component = cid
        self.viewport.highlight(cid)
        self.inspector.select(cid)

    # ------------------------------------------------------------ files
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes to the current project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if not self._confirm_discard():
            return
        self._load_project(Project())

    def open_project(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._last_dir(), f"Aircraft Foundry (*{SUFFIX})")
        if path:
            try:
                self._load_project(Project.load(path))
            except Exception as exc:
                QMessageBox.critical(self, "Open project", str(exc))

    def _load_project(self, project: Project):
        self.project = project
        self.mission_panel.set_mission(project.mission)
        self.gallery.set_candidates(project.candidates, project.active_id)
        self.design, self.candidate = None, None
        self.viewport.clear()
        self.inspector.design = None
        self.inspector.select(None)
        if project.active_id and project.candidate(project.active_id):
            self.activate_candidate(project.active_id)
        elif project.candidates:
            self.activate_candidate(project.candidates[0].id)
        self._mark_clean()

    def save_project(self, as_new: bool = False) -> bool:
        if as_new or self.project.path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save project", self._last_dir(), f"Aircraft Foundry (*{SUFFIX})")
            if not path:
                return False
            self.project.path = Path(path)
        self.project.mission = self.mission_panel.mission()
        try:
            saved = self.project.save()
        except OSError as exc:
            QMessageBox.critical(self, "Save project", str(exc))
            return False
        app_settings().setValue("last_dir", str(saved.parent))
        self._mark_clean()
        self.status_label.setText(f"Saved {saved}")
        return True

    def import_design(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import design", self._last_dir(), "JSON (*.json)")
        if not path:
            return
        try:
            design, mission = import_design_json(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import design", str(exc))
            return
        if mission is not None and not self.project.candidates:
            self.project.mission = mission
            self.mission_panel.set_mission(mission)
        c = Candidate(design.params, name=design.name, origin=f"imported from {Path(path).name}")
        c.evaluate(self.project.mission)
        self.project.candidates.append(c)
        self.gallery.update_candidate(c)
        self.activate_candidate(c.id)
        self._mark_dirty()

    def export(self):
        if self.design is None:
            QMessageBox.information(self, "Export", "Generate or load a design first.")
            return
        last_export = app_settings().value("export_dir", None)
        if not (last_export and Path(str(last_export)).is_dir()):
            last_export = str(self.location.exports_dir)
        dlg = ExportDialog(self, self.selected_component, self.design.name, last_export)
        if not dlg.exec():
            return
        req = dlg.request()
        if not req.formats:
            return
        app_settings().setValue("export_dir", str(req.directory))
        design, mission, ev = self.design, self.project.mission, self.candidate.evaluation

        def job(progress, cancel):
            return run_export(req, design, mission, ev, progress)

        def done(paths):
            self.status_label.setText(f"Exported {len(paths)} file(s) to {req.directory}")
            QMessageBox.information(self, "Export", "Written:\n" + "\n".join(str(p) for p in paths[:20])
                                    + ("\n…" if len(paths) > 20 else ""))

        self._run(job, done, "Exporting…")

    def save_screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save image", self._last_dir(), "PNG (*.png)")
        if path:
            self.viewport.screenshot(path)

    def _last_dir(self) -> str:
        last = app_settings().value("last_dir", None)
        if last and Path(str(last)).is_dir():
            return str(last)
        return str(self.location.projects_dir)  # on a USB stick: the stick's projects folder

    def about(self):
        QMessageBox.about(self, "Aircraft Foundry",
                          f"<b>Aircraft Foundry {__version__}</b><p>Procedural conceptual aircraft design.</p>"
                          "<p>Analysis methods are conceptual-level statistics (Raymer, DATCOM, Korn) intended "
                          "for trade studies, not for certification or detailed design.</p>")

    # ------------------------------------------------------------ lifecycle
    def _restore_settings(self):
        s = app_settings()
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = s.value("window_state")
        if state is not None:
            pass  # dock layout is fixed (two panels); older saved layouts are ignored

    def closeEvent(self, event):
        if self.task and self.task.running:
            self.task.cancel()
        if not self._confirm_discard():
            event.ignore()
            return
        for tool in list(self.tool_windows.values()):  # stop embedded tools' timers before their views go
            try:
                tool.close()
            except Exception:
                pass
        s = app_settings()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("window_state", self.saveState())
        self.viewport.close()
        super().closeEvent(event)
