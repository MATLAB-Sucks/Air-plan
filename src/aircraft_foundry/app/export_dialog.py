"""Export manager: formats, scope (assembly / selected component), structure, per-component files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QVBoxLayout)

from ..fileio.common import ExportSelection
from ..fileio.exporter import ExportRequest
from ..geometry.cad import cadquery_available


class ExportDialog(QDialog):
    def __init__(self, parent=None, selected_component: str | None = None, basename: str = "aircraft",
                 directory: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export manager")
        lay = QVBoxLayout(self)

        fmt_box = QGroupBox("Formats")
        fl = QVBoxLayout(fmt_box)
        has_cad = cadquery_available()
        self.fmt = {
            "step": QCheckBox("STEP — B-rep solids for CAD (assembly with named parts)"),
            "stl": QCheckBox("STL — triangulated mesh"),
            "obj": QCheckBox("OBJ — mesh with one named object per component"),
            "json": QCheckBox("JSON — editable design parameters + component manifest"),
        }
        for k, cb in self.fmt.items():
            cb.setChecked(k != "step" or has_cad)
            fl.addWidget(cb)
        if not has_cad:
            self.fmt["step"].setEnabled(False)
            self.fmt["step"].setToolTip("CadQuery is not installed")
        lay.addWidget(fmt_box)

        scope_box = QGroupBox("Scope")
        sl = QVBoxLayout(scope_box)
        self.whole = QRadioButton("Complete aircraft assembly")
        self.single = QRadioButton(f"Selected component: {selected_component}" if selected_component
                                   else "Selected component (none selected)")
        self.single.setEnabled(selected_component is not None)
        self.whole.setChecked(True)
        self.structure = QCheckBox("Include internal structure (ribs, spars, frames, longerons, fittings)")
        self.children = QCheckBox("Include the selected component's sub-components")
        self.children.setChecked(True)
        self.per_component = QCheckBox("Also write one file per component")
        for w in (self.whole, self.single, self.structure, self.children, self.per_component):
            sl.addWidget(w)
        lay.addWidget(scope_box)
        self._selected = selected_component

        form = QFormLayout()
        self.basename = QLineEdit(basename)
        row = QHBoxLayout()
        self.directory = QLineEdit(directory or str(Path.home() / "Aircraft Foundry Exports"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.directory, 1)
        row.addWidget(browse)
        form.addRow("File base name", self.basename)
        form.addRow("Folder", row)
        lay.addLayout(form)
        note = QLabel("STEP of a full aircraft with structure can take a minute; it runs in the background.")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Export folder", self.directory.text())
        if d:
            self.directory.setText(d)

    def request(self) -> ExportRequest:
        ids = [self._selected] if self.single.isChecked() and self._selected else None
        sel = ExportSelection(ids, self.structure.isChecked(), self.children.isChecked())
        formats = tuple(k for k, cb in self.fmt.items() if cb.isChecked() and cb.isEnabled())
        return ExportRequest(Path(self.directory.text()), formats, sel, self.per_component.isChecked(),
                             self.basename.text() or "aircraft")
