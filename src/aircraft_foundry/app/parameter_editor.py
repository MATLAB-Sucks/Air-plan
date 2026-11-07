"""Grouped aircraft parameter editor generated from the parameter schema."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFormLayout, QLabel, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ..core.parameters import AircraftParameters, GROUP_TYPES, parameter_specs
from .forms import changed_signal, get_value, make_editor, set_value

GROUP_TITLES = {"configuration": "Config", "fuselage": "Fuselage", "wing": "Wing", "htail": "H-tail",
                "vtail": "V-tail", "engines": "Engines", "gear": "Gear", "controls": "Controls",
                "structure": "Structure", "canard": "Canard", "booms": "Booms"}


class ParameterEditor(QWidget):
    parameter_changed = Signal(str, object)
    complete_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.editors: dict[str, QWidget] = {}
        defaults = AircraftParameters()
        forms = {}
        for group in GROUP_TYPES:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            body = QWidget()
            forms[group] = QFormLayout(body)
            scroll.setWidget(body)
            self.tabs.addTab(scroll, GROUP_TITLES.get(group, group.replace("_", " ").title()))
        for path, meta in parameter_specs():
            group = path.split(".")[0]
            w = make_editor(meta, defaults.get(path))
            tip = f"{path}"
            if "lo" in meta:
                tip += f"\nrange {meta['lo']} – {meta['hi']} {meta.get('unit', '')}"
            if meta.get("evolvable"):
                tip += "\n(design variable used by evolution)"
            w.setToolTip(tip)
            changed_signal(w).connect(lambda *_, p=path, ed=w: self.parameter_changed.emit(p, get_value(ed)))
            label = meta.get("label", path) + (" ★" if meta.get("evolvable") else "")
            forms[group].addRow(label, w)
            self.editors[path] = w
        forms["gear"].addRow(QLabel("Gear positions are normally derived from the CG."))
        self.complete_btn = QPushButton("Re-derive gear from CG && clearances")
        self.complete_btn.clicked.connect(self.complete_requested)
        forms["gear"].addRow(self.complete_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)
        note = QLabel("★ = design variable used by evolution")
        note.setStyleSheet("color: palette(mid);")
        lay.addWidget(note)
        self.setEnabled(False)

    def set_parameters(self, params: AircraftParameters) -> None:
        for path, w in self.editors.items():
            set_value(w, params.get(path))
        self.setEnabled(True)
