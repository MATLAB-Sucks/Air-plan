"""Mission specification editor and generation / evolution controls."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core.mission import MissionRequirements, mission_specs
from .forms import changed_signal, get_value, make_editor, set_value


class MissionPanel(QScrollArea):
    generate_requested = Signal()
    evolve_requested = Signal()
    cancel_requested = Signal()
    mission_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)

        box = QGroupBox("Mission requirements")
        form = QFormLayout(box)
        self.editors: dict[str, QWidget] = {}
        m = MissionRequirements()
        name_meta = {"label": "Mission name"}
        self.editors["name"] = make_editor(name_meta, m.name)
        form.addRow("Mission name", self.editors["name"])
        for name, meta in mission_specs():
            w = make_editor(meta, getattr(m, name))
            w.setToolTip(f"{meta.get('label', name)}")
            changed_signal(w).connect(self._on_changed)
            self.editors[name] = w
            form.addRow(meta.get("label", name), w)
        changed_signal(self.editors["name"]).connect(self._on_changed)
        self.derived = QLabel()
        self.derived.setWordWrap(True)
        self.derived.setStyleSheet("color: palette(mid);")
        form.addRow(self.derived)
        lay.addWidget(box)

        gen = QGroupBox("Aircraft generation")
        gf = QFormLayout(gen)
        self.count = QSpinBox(); self.count.setRange(1, 40); self.count.setValue(8)
        self.samples = QSpinBox(); self.samples.setRange(4, 400); self.samples.setValue(24)
        self.seed = QSpinBox(); self.seed.setRange(0, 99999); self.seed.setValue(1)
        gf.addRow("Candidates", self.count)
        gf.addRow("Samples / configuration", self.samples)
        gf.addRow("Random seed", self.seed)
        self.generate_btn = QPushButton("Generate candidates")
        self.generate_btn.clicked.connect(self.generate_requested)
        gf.addRow(self.generate_btn)
        lay.addWidget(gen)

        evo = QGroupBox("Evolution (selected candidates)")
        ef = QFormLayout(evo)
        self.generations = QSpinBox(); self.generations.setRange(1, 500); self.generations.setValue(25)
        self.population = QSpinBox(); self.population.setRange(5, 200); self.population.setValue(20)
        ef.addRow("Generations", self.generations)
        ef.addRow("Population", self.population)
        row = QHBoxLayout()
        self.evolve_btn = QPushButton("Evolve selected")
        self.evolve_btn.clicked.connect(self.evolve_requested)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_requested)
        row.addWidget(self.evolve_btn)
        row.addWidget(self.cancel_btn)
        ef.addRow(row)
        lay.addWidget(evo)
        lay.addStretch(1)
        self.setWidget(body)
        self._update_derived()

    def mission(self) -> MissionRequirements:
        m = MissionRequirements()
        for name, w in self.editors.items():
            v = get_value(w)
            setattr(m, name, v)
        return m

    def set_mission(self, m: MissionRequirements) -> None:
        for name, w in self.editors.items():
            set_value(w, getattr(m, name))
        self._update_derived()

    def set_busy(self, busy: bool) -> None:
        self.generate_btn.setEnabled(not busy)
        self.evolve_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)

    def _on_changed(self, *_):
        self._update_derived()
        self.mission_changed.emit()

    def _update_derived(self) -> None:
        m = self.mission()
        problems = m.validate()
        txt = (f"≈ {m.passengers} passengers, {m.seats_abreast} abreast, cabin {m.cabin_length_m:.1f} m, "
               f"fuselage Ø {m.fuselage_diameter_m:.2f} m")
        if problems:
            txt += "<br><span style='color:#d9534f'>" + "<br>".join(problems) + "</span>"
        self.derived.setText(txt)
