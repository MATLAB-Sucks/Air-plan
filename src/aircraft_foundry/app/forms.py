"""Widgets generated from parameter metadata (bounds, units, options)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QWidget


def make_editor(meta: dict, value, parent: QWidget | None = None) -> QWidget:
    if "options" in meta:
        w = QComboBox(parent)
        w.addItems([str(o) for o in meta["options"]])
        w.setCurrentText(str(value))
        return w
    if "lo" not in meta:
        w = QLineEdit(str(value), parent)
        return w
    if meta.get("integer"):
        w = QSpinBox(parent)
        w.setRange(int(meta["lo"]), int(meta["hi"]))
        w.setSingleStep(int(max(meta.get("step", 1), 1)))
    else:
        w = QDoubleSpinBox(parent)
        span = meta["hi"] - meta["lo"]
        decimals = 4 if span < 1 else 3 if span < 20 else 1 if span < 2000 else 0
        w.setDecimals(decimals)
        w.setRange(meta["lo"], meta["hi"])
        w.setSingleStep(meta.get("step", span / 100))
    w.setKeyboardTracking(False)  # emit valueChanged on commit, not on every keystroke
    if meta.get("unit"):
        w.setSuffix(f" {meta['unit']}")
    set_value(w, value)
    return w


def set_value(w: QWidget, value) -> None:
    w.blockSignals(True)
    if isinstance(w, QComboBox):
        w.setCurrentText(str(value))
    elif isinstance(w, QSpinBox):
        w.setValue(int(value))
    elif isinstance(w, QDoubleSpinBox):
        w.setValue(float(value))
    elif isinstance(w, QLineEdit):
        w.setText(str(value))
    w.blockSignals(False)


def get_value(w: QWidget):
    if isinstance(w, QComboBox):
        return w.currentText()
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        return w.value()
    return w.text()


def changed_signal(w: QWidget):
    if isinstance(w, QComboBox):
        return w.currentTextChanged
    if isinstance(w, (QSpinBox, QDoubleSpinBox)):
        return w.valueChanged
    return w.editingFinished
