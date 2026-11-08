"""Game-controller manager (polling, routing, settings) and its workspace page."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QLabel,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..input.controller import AXES, BUTTONS, FLIGHT_HELP, UI_HELP, PadSettings, PadState, make_backend
from .settings import settings as app_settings


class ControllerManager(QObject):
    """Polls controllers at 60 Hz and hands the first one to whoever is listening."""
    pads_changed = Signal(list)
    updated = Signal(object)  # PadState of the primary pad (or None)

    def __init__(self, backend=None, parent=None, start_delay_ms: int = 1500):
        super().__init__(parent)
        from ..input.controller import NullBackend
        self.settings = self._load_settings()
        self.states: list[PadState] = []
        self._names: list[str] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        if backend is not None:
            self.backend = backend
            self._started()
        else:
            # SDL starts just after the window is up, never on the start-up path
            self.backend = NullBackend()
            self.backend.error = "Starting controller support…"
            QTimer.singleShot(start_delay_ms, self.start)

    def start(self) -> None:
        import os
        if os.environ.get("AF_NO_CONTROLLERS"):
            self.backend.error = "Controller support switched off (AF_NO_CONTROLLERS)."
        else:
            self.backend = make_backend()
        self._started()

    def _started(self) -> None:
        if self.backend.available:
            self.timer.start(16)
        self.pads_changed.emit(self._names)

    def _load_settings(self) -> PadSettings:
        s = app_settings()
        d = PadSettings()
        return PadSettings(float(s.value("pad/deadzone", d.deadzone)), float(s.value("pad/expo", d.expo)),
                           str(s.value("pad/invert", "false")).lower() == "true",
                           float(s.value("pad/sensitivity", d.sensitivity)),
                           str(s.value("pad/rumble", "true")).lower() == "true")

    def save_settings(self) -> None:
        s, p = app_settings(), self.settings
        s.setValue("pad/deadzone", p.deadzone)
        s.setValue("pad/expo", p.expo)
        s.setValue("pad/invert", "true" if p.invert_pitch else "false")
        s.setValue("pad/sensitivity", p.sensitivity)
        s.setValue("pad/rumble", "true" if p.rumble else "false")

    @property
    def primary(self) -> PadState | None:
        return self.states[0] if self.states else None

    def poll(self) -> None:
        self.states = self.backend.poll()
        names = [f"{st.name} ({st.kind()})" for st in self.states]
        if names != self._names:
            self._names = names
            self.pads_changed.emit(names)
        self.updated.emit(self.primary)

    def rumble(self, low: float, high: float, ms: int) -> None:
        if self.settings.rumble and self.primary is not None:
            self.backend.rumble(self.primary.index, low, high, ms)


class ControllerPage(QWidget):
    def __init__(self, manager: ControllerManager):
        super().__init__()
        self.m = manager
        lay = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        live = QGroupBox("Live input")
        grid = QGridLayout(live)
        self.bars = {}
        for i, a in enumerate(AXES):
            bar = QProgressBar()
            bar.setRange(-100, 100)
            bar.setTextVisible(False)
            grid.addWidget(QLabel({"lx": "Left stick X", "ly": "Left stick Y", "rx": "Right stick X",
                                   "ry": "Right stick Y", "lt": "Left trigger", "rt": "Right trigger"}[a]), i, 0)
            grid.addWidget(bar, i, 1)
            self.bars[a] = bar
        self.buttons_lbl = QLabel("Buttons: —")
        self.buttons_lbl.setWordWrap(True)
        grid.addWidget(self.buttons_lbl, len(AXES), 0, 1, 2)
        lay.addWidget(live)
        box = QGroupBox("Feel")
        form = QFormLayout(box)
        p = manager.settings
        self.dead = QDoubleSpinBox()
        self.dead.setRange(0.0, 0.4)
        self.dead.setSingleStep(0.01)
        self.dead.setValue(p.deadzone)
        self.expo = QDoubleSpinBox()
        self.expo.setRange(0.0, 1.0)
        self.expo.setSingleStep(0.05)
        self.expo.setValue(p.expo)
        self.sens = QDoubleSpinBox()
        self.sens.setRange(0.3, 2.0)
        self.sens.setSingleStep(0.05)
        self.sens.setValue(p.sensitivity)
        self.invert = QCheckBox("Invert pitch (push forward = nose up)")
        self.invert.setChecked(p.invert_pitch)
        self.rumble = QCheckBox("Rumble (stall, overstress, touchdown, crash)")
        self.rumble.setChecked(p.rumble)
        form.addRow("Dead zone", self.dead)
        form.addRow("Response curve (expo)", self.expo)
        form.addRow("Sensitivity", self.sens)
        form.addRow(self.invert)
        form.addRow(self.rumble)
        test = QPushButton("Test rumble")
        test.clicked.connect(lambda: manager.backend.rumble(manager.primary.index, 0.6, 0.9, 400)
                             if manager.primary else None)
        form.addRow(test)
        for w in (self.dead, self.expo, self.sens):
            w.valueChanged.connect(self._apply)
        for w in (self.invert, self.rumble):
            w.toggled.connect(self._apply)
        lay.addWidget(box)
        for title, rows in (("In the flight simulator", FLIGHT_HELP), ("Everywhere else", UI_HELP)):
            g = QGroupBox(title)
            gl = QVBoxLayout(g)
            t = QTableWidget(len(rows), 2)
            t.setHorizontalHeaderLabels(["Control", "Action"])
            t.verticalHeader().hide()
            t.horizontalHeader().setStretchLastSection(True)
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            for r, (a, b) in enumerate(rows):
                t.setItem(r, 0, QTableWidgetItem(a))
                t.setItem(r, 1, QTableWidgetItem(b))
            t.resizeColumnToContents(0)
            t.setMinimumHeight(28 * len(rows) + 30)
            gl.addWidget(t)
            lay.addWidget(g)
        note = QLabel("Pair the controller in your computer's Bluetooth settings first; it is picked up "
                      "automatically. Joy-Cons: connect both and they act as one controller. Buttons follow "
                      "position, so the bottom face button always means 'confirm'. On macOS, if a controller "
                      "isn't detected, allow Aircraft Foundry under Privacy & Security → Input Monitoring.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        manager.pads_changed.connect(self._pads)
        manager.updated.connect(self._live)
        self._pads(manager._names)

    def _apply(self, *_):
        p = self.m.settings
        p.deadzone, p.expo, p.sensitivity = self.dead.value(), self.expo.value(), self.sens.value()
        p.invert_pitch, p.rumble = self.invert.isChecked(), self.rumble.isChecked()
        self.m.save_settings()

    def _pads(self, names):
        if not self.m.backend.available:
            self.status.setText(f"<b>Controllers unavailable.</b> {self.m.backend.error}")
        elif names:
            self.status.setText("<b>Connected:</b> " + ", ".join(names))
        else:
            self.status.setText("No controller connected. Xbox, PlayStation (DualShock 4 / DualSense) and "
                                "Nintendo Switch (Pro, Joy-Cons) controllers are supported.")

    def _live(self, st):
        if not self.isVisible():
            return
        for a, bar in self.bars.items():
            bar.setValue(int(100 * (st.axes[a] if st else 0.0)))
        self.buttons_lbl.setText("Buttons: " + (", ".join(sorted(st.held)) if st and st.held else "—"))
