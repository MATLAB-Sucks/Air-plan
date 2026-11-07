"""Design library browser: the shared database of previously made aircraft."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..library import DesignLibrary, LibraryReadOnly

COLS = ["", "Name", "Configuration", "Mission", "MTOW kg", "Status", "Modified (UTC)", "Created on", "Tags"]


class LibraryPanel(QWidget):
    open_requested = Signal(str)
    save_requested = Signal()

    def __init__(self, library: DesignLibrary, location_text: str, parent=None):
        super().__init__(parent)
        self.library = library
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, tags, notes, configuration, mission…")
        self.search.textChanged.connect(self.refresh)
        self.feasible_only = QCheckBox("Feasible only")
        self.feasible_only.toggled.connect(self.refresh)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(110, 80))
        self.table.verticalHeader().setDefaultSectionSize(84)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(lambda *_: self._open())
        self.table.itemSelectionChanged.connect(self._selected)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes for the selected design")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("tags, comma separated")
        self.location = QLabel(location_text)
        self.location.setStyleSheet("color: palette(mid);")
        self.location.setTextInteractionFlags(Qt.TextSelectableByMouse)

        b_open, b_save, b_meta, b_del = (QPushButton(t) for t in
                                         ("Open in project", "Save current design…", "Save notes && tags", "Delete"))
        b_open.clicked.connect(self._open)
        b_save.clicked.connect(self.save_requested)
        b_meta.clicked.connect(self._save_meta)
        b_del.clicked.connect(self._delete)
        self._write_buttons = (b_save, b_meta, b_del)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.feasible_only)
        btns = QHBoxLayout()
        for b in (b_open, b_save, b_meta, b_del):
            btns.addWidget(b)
        btns.addStretch(1)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("Tags"))
        sl.addWidget(self.tags)
        sl.addWidget(QLabel("Notes"))
        sl.addWidget(self.notes, 1)
        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(side)
        split.setSizes([900, 260])
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(btns)
        lay.addWidget(self.location)
        self.refresh()

    def selected_id(self) -> str | None:
        rows = {i.row() for i in self.table.selectedItems()}
        if not rows:
            return None
        return self.table.item(rows.pop(), 1).data(Qt.UserRole)

    def refresh(self, *_):
        for b in self._write_buttons:
            b.setEnabled(not self.library.read_only)
        try:
            entries = self.library.list(self.search.text().strip(), self.feasible_only.isChecked())
        except Exception as exc:  # unplugged stick, damaged file
            self.location.setText(f"Library unavailable: {exc}")
            return
        self.table.setRowCount(0)
        for e in entries:
            r = self.table.rowCount()
            self.table.insertRow(r)
            icon = QTableWidgetItem()
            if e.thumbnail_png:
                pm = QPixmap()
                pm.loadFromData(e.thumbnail_png, "PNG")
                icon.setIcon(QIcon(pm))
            self.table.setItem(r, 0, icon)
            status = "feasible" if e.feasible else "infeasible"
            vals = [e.name, e.configuration, e.mission_name, f"{e.mtow_kg:,.0f}" if e.mtow_kg else "—", status,
                    e.modified_utc.replace("T", " ").replace("+00:00", ""), e.created_on, e.tags]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                if c == 1:
                    it.setData(Qt.UserRole, e.id)
                    it.setData(Qt.UserRole + 1, e.notes)
                if c == 5:
                    it.setForeground(QColor(70, 170, 90) if e.feasible else QColor(230, 150, 40))
                self.table.setItem(r, c, it)
        ro = " (read-only)" if self.library.read_only else ""
        self.location.setText(f"{len(entries)} design(s) · {self.library.path}{ro}")

    def _selected(self):
        rows = {i.row() for i in self.table.selectedItems()}
        if rows:
            r = rows.pop()
            self.notes.setPlainText(self.table.item(r, 1).data(Qt.UserRole + 1) or "")
            self.tags.setText(self.table.item(r, 8).text())

    def _open(self):
        did = self.selected_id()
        if did:
            self.open_requested.emit(did)

    def _save_meta(self):
        did = self.selected_id()
        if not did:
            return
        try:
            self.library.update(did, notes=self.notes.toPlainText(), tags=self.tags.text())
        except LibraryReadOnly as exc:
            QMessageBox.warning(self, "Library", str(exc))
        self.refresh()

    def _delete(self):
        did = self.selected_id()
        if not did:
            return
        if QMessageBox.question(self, "Delete design", "Delete the selected design from the shared library?") \
                != QMessageBox.Yes:
            return
        try:
            self.library.delete(did)
        except LibraryReadOnly as exc:
            QMessageBox.warning(self, "Library", str(exc))
        self.refresh()
