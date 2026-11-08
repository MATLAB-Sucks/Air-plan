"""Candidate comparison gallery: planform thumbnails plus sortable metrics."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem

from ..core.generator import Candidate
from .thumbnails import planform_pixmap

COLUMNS = [
    ("", None, None),
    ("Name", None, None),
    ("Configuration", None, None),
    ("Status", None, None),
    ("Score", "score", "{:,.0f}"),
    ("MTOW kg", "mtow_kg", "{:,.0f}"),
    ("Trip fuel kg", "trip_fuel_kg", "{:,.0f}"),
    ("Fuel/pax/100km", "fuel_per_pax_100km_kg", "{:.2f}"),
    ("Cruise L/D", "cruise_ld", "{:.1f}"),
    ("Span m", "span", "{:.1f}"),
    ("Wing area m²", "wing_area_m2", "{:.0f}"),
    ("Static margin", "static_margin", "{:.1%}"),
    ("Boom PLdB", "boom_pldb", "{:.0f}"),
    ("Airport dB", "flyover_noise_db", "{:.0f}"),
    ("Cabin dB(A)", "cabin_noise_dba", "{:.0f}"),
    ("Origin", None, None),
]


class NumericItem(QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(Qt.UserRole), other.data(Qt.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return super().__lt__(other)


class CandidateGallery(QTableWidget):
    candidate_activated = Signal(str)
    selection_changed_ids = Signal(list)

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setIconSize(QSize(132, 96))
        self.verticalHeader().setDefaultSectionSize(100)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.cellDoubleClicked.connect(lambda r, _c: self._activate(r))
        self.cellClicked.connect(lambda r, _c: self._activate(r))  # one click shows the aircraft
        self.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.horizontalHeader().customContextMenuRequested.connect(self._column_menu)
        self.setToolTip("Click an aircraft to show it. Right-click the header to choose which figures to compare.")
        self.itemSelectionChanged.connect(lambda: self.selection_changed_ids.emit(self.selected_ids()))

    COMPACT = ("", "Name", "Status", "MTOW kg")

    def set_compact(self, compact: bool = True) -> None:
        """Narrow side-panel view: thumbnail, name, status and MTOW (other columns via the header menu)."""
        for i, (label, _, _) in enumerate(COLUMNS):
            self.setColumnHidden(i, compact and label not in self.COMPACT)
        self.setIconSize(QSize(96, 70) if compact else QSize(132, 96))
        self.verticalHeader().setDefaultSectionSize(76 if compact else 100)

    def _column_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        for i, (label, _, _) in enumerate(COLUMNS):
            if not label:
                continue
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not self.isColumnHidden(i))
            act.toggled.connect(lambda on, i=i: self.setColumnHidden(i, not on))
        menu.exec(self.horizontalHeader().mapToGlobal(pos))

    def _activate(self, row: int) -> None:
        item = self.item(row, 1)
        if item:
            self.candidate_activated.emit(item.data(Qt.UserRole + 1))

    def selected_ids(self) -> list[str]:
        rows = sorted({i.row() for i in self.selectedItems()})
        return [self.item(r, 1).data(Qt.UserRole + 1) for r in rows if self.item(r, 1)]

    def set_candidates(self, cands: list[Candidate], active: str | None = None) -> None:
        self.setSortingEnabled(False)
        self.setRowCount(0)
        for c in cands:
            self._add_row(c)
        self.setSortingEnabled(True)
        if active:
            self.mark_active(active)

    def update_candidate(self, c: Candidate) -> None:
        # Sorting must be off while a row is (re)filled, otherwise Qt re-sorts
        # after the first cell and the remaining cells land in another row.
        self.setSortingEnabled(False)
        try:
            for r in range(self.rowCount()):
                item = self.item(r, 1)
                if item is not None and item.data(Qt.UserRole + 1) == c.id:
                    self._fill_row(r, c)
                    return
            self._add_row(c)
        finally:
            self.setSortingEnabled(True)

    def mark_active(self, cid: str) -> None:
        for r in range(self.rowCount()):
            it = self.item(r, 1)
            if it is None:
                continue
            f = it.font()
            f.setBold(it.data(Qt.UserRole + 1) == cid)
            it.setFont(f)

    def _add_row(self, c: Candidate) -> None:
        r = self.rowCount()
        self.insertRow(r)
        self._fill_row(r, c)

    def _fill_row(self, r: int, c: Candidate) -> None:
        ev = c.evaluation
        feasible = bool(ev and ev.feasible)
        icon_item = QTableWidgetItem()
        icon_item.setIcon(QIcon(planform_pixmap(c.params, feasible=feasible)))
        self.setItem(r, 0, icon_item)
        for col, (title, key, fmt) in enumerate(COLUMNS[1:], start=1):
            item = NumericItem()
            if title == "Name":
                item.setText(c.name or c.id)
                item.setData(Qt.UserRole + 1, c.id)
            elif title == "Configuration":
                item.setText(c.config_label)
            elif title == "Status":
                if ev is None or not ev.converged:
                    item.setText("does not close")
                    item.setForeground(QColor(220, 80, 60))
                elif ev.feasible:
                    item.setText("feasible")
                    item.setForeground(QColor(70, 170, 90))
                else:
                    item.setText(f"{len(ev.violated)} violation(s)")
                    item.setForeground(QColor(230, 150, 40))
                if ev is not None:
                    item.setToolTip(ev.message.replace(", ", "\n"))
            elif title == "Origin":
                item.setText(c.origin)
            else:
                if key == "score":
                    val = ev.score if ev else None
                elif key == "span":
                    val = c.params.wing.span
                else:
                    val = ev.metrics.get(key) if ev else None
                if val is not None:
                    item.setText(fmt.format(val))
                    item.setData(Qt.UserRole, float(val))
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(r, col, item)
