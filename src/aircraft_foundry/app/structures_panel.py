"""Structural analysis panel: summary, mass comparison, spanwise/longitudinal plots, station table."""
from __future__ import annotations

import html

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QHeaderView, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.evaluator import Evaluation


class LinePlot(QWidget):
    """Minimal dependency-free line chart (one or two series sharing an x axis)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.series: list[tuple[str, np.ndarray, np.ndarray, QColor]] = []
        self.x_label = ""
        self.setMinimumHeight(170)

    def set_data(self, x_label: str, series):
        self.x_label = x_label
        self.series = series
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(46, 22, -10, -26)
        p.fillRect(self.rect(), self.palette().base())
        p.setPen(self.palette().text().color())
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 2, self.width(), 18), Qt.AlignCenter, self.title)
        p.setFont(self.font())
        if not self.series:
            p.end()
            return
        xs = np.concatenate([s[1] for s in self.series])
        x0, x1 = float(xs.min()), float(xs.max()) or 1.0
        p.setPen(QPen(QColor(128, 128, 128), 1))
        p.drawRect(r)
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 18), Qt.AlignCenter, self.x_label)
        p.drawText(QRectF(r.left() - 4, r.bottom() + 4, 40, 18), Qt.AlignLeft, f"{x0:.0f}")
        p.drawText(QRectF(r.right() - 40, r.bottom() + 4, 40, 18), Qt.AlignRight, f"{x1:.0f}")
        for i, (name, x, y, colour) in enumerate(self.series):
            ymax = float(np.max(np.abs(y))) or 1.0
            pts = [QPointF(r.left() + (xi - x0) / max(x1 - x0, 1e-9) * r.width(), r.bottom() - yi / ymax * r.height())
                   for xi, yi in zip(x, y)]
            p.setPen(QPen(colour, 2))
            p.drawPolyline(QPolygonF(pts))
            p.drawText(QRectF(r.left() + 6, r.top() + 4 + 16 * i, r.width(), 16), Qt.AlignLeft,
                       f"{name} (max {ymax:,.3g})")
        p.end()


class StructuresPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary = QTextBrowser()
        self.wing_plot = LinePlot("Wing: ultimate bending and shear along the span")
        self.cover_plot = LinePlot("Wing box: gauged cover and web thickness")
        self.fus_plot = LinePlot("Fuselage: ultimate bending along the length")
        plots = QWidget()
        pl = QVBoxLayout(plots)
        pl.setContentsMargins(0, 0, 0, 0)
        for w in (self.wing_plot, self.cover_plot, self.fus_plot):
            pl.addWidget(w)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["y m", "M kNm", "V kN", "T kNm", "upper mm", "lower mm", "web mm",
                                              "min MS", "util"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        tabs = QTabWidget()
        tabs.addTab(self.summary, "Summary")
        tabs.addTab(plots, "Plots")
        tabs.addTab(self.table, "Wing stations")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(tabs)

    def set_evaluation(self, ev: Evaluation | None) -> None:
        st = ev.structure if ev is not None else None
        if st is None:
            self.summary.setHtml("<i>No structural results (the design does not close).</i>")
            self.table.setRowCount(0)
            for pl in (self.wing_plot, self.cover_plot, self.fus_plot):
                pl.set_data("", [])
            return
        m = ev.metrics
        out = "<h3>Structural sizing</h3><table cellspacing=3>"
        for k, v in st.summary.items():
            out += f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td align=right>{v:,.3g}</td></tr>"
        out += "</table>"
        if st.warnings:
            out += "<p style='color:#e0533d'>" + "<br>".join(html.escape(w) for w in st.warnings) + "</p>"
        out += ("<h4>Weight: statistical vs. structural sizing</h4><table cellspacing=3>"
                "<tr><th></th><th>statistical</th><th>sized structure</th></tr>"
                f"<tr><td>Wing</td><td align=right>{m['wing_mass_statistical_kg']:,.0f} kg</td>"
                f"<td align=right>{m['wing_mass_structural_kg']:,.0f} kg</td></tr>"
                f"<tr><td>Fuselage</td><td align=right>{m['fuselage_mass_statistical_kg']:,.0f} kg</td>"
                f"<td align=right>{m['fuselage_mass_structural_kg']:,.0f} kg</td></tr></table>"
                "<p>The weight method used for closure is set in Parameters ▸ Structure.</p>"
                "<h4>Sized structure mass breakdown</h4><table cellspacing=2>")
        for k, v in st.masses.items():
            if not k.endswith("_total"):
                out += f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td align=right>{v:,.0f} kg</td></tr>"
        out += "</table>"
        self.summary.setHtml(out)
        w, f = st.wing, st.fuselage
        self.wing_plot.set_data("semi-span y (m)", [("bending kNm", w.y, w.M / 1e3, QColor(220, 80, 60)),
                                                    ("shear kN", w.y, w.V / 1e3, QColor(60, 120, 220))])
        self.cover_plot.set_data("semi-span y (m)", [("upper cover mm", w.y, w.g_upper * 1e3, QColor(230, 150, 40)),
                                                     ("spar web mm", w.y, w.g_web * 1e3, QColor(70, 170, 90))])
        self.fus_plot.set_data("station x (m)", [("bending kNm", f.x, f.M / 1e3, QColor(150, 90, 200))])
        rows = range(0, len(w.y), 3)
        self.table.setRowCount(len(rows))
        util = w.utilisation
        for r, i in enumerate(rows):
            ms = min(w.ms_upper[i], w.ms_lower[i], w.ms_web[i])
            vals = [w.y[i], w.M[i] / 1e3, w.V[i] / 1e3, w.T[i] / 1e3, w.g_upper[i] * 1e3, w.g_lower[i] * 1e3,
                    w.g_web[i] * 1e3, ms, util[i]]
            for c, v in enumerate(vals):
                item = QTableWidgetItem("∞" if not np.isfinite(v) or v > 1e6 else f"{v:,.2f}")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)
