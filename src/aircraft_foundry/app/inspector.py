"""Component tree and structured detail view (identity, placement, relations, parameters)."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core.design import AircraftDesign
from ..core.evaluator import Evaluation
from ..structural_vis.summary import structure_summary


class Inspector(QWidget):
    component_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Component", "Type"])
        self.tree.setColumnWidth(0, 210)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(lambda url: self.select(url.toString().removeprefix("comp:"), emit=True))
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.details)
        split.setSizes([300, 300])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)
        self.design: AircraftDesign | None = None
        self.evaluation: Evaluation | None = None
        self._items: dict[str, QTreeWidgetItem] = {}
        self._current: str | None = None

    def set_design(self, design: AircraftDesign, evaluation: Evaluation | None = None) -> None:
        self.design, self.evaluation = design, evaluation
        expanded = {cid for cid, it in self._items.items() if it.isExpanded()}
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items.clear()
        comps = design.components
        # top-level components; structure grouped under its parent assembly
        for cid, c in comps.items():
            if c.parent is None or c.parent not in comps:
                self._items[cid] = QTreeWidgetItem(self.tree, [c.name, c.type.value])
        for cid, c in comps.items():
            if cid in self._items:
                continue
            parent = self._items.get(c.parent)
            if parent is None:
                parent = self._items[c.parent] = QTreeWidgetItem(self.tree, [c.parent, ""])
            self._items[cid] = QTreeWidgetItem(parent, [c.name, c.type.value])
        for cid, it in self._items.items():
            it.setData(0, Qt.UserRole, cid)
            it.setExpanded(cid in expanded)
        self.tree.blockSignals(False)
        if self._current in comps:
            self.select(self._current)
        else:
            self._show_overview()

    def select(self, cid: str | None, emit: bool = False) -> None:
        self._current = cid
        if cid and cid in self._items:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self._items[cid])
            self.tree.scrollToItem(self._items[cid])
            self.tree.blockSignals(False)
            self._show(cid)
            if emit:
                self.component_selected.emit(cid)
        else:
            self.tree.clearSelection()
            self._show_overview()

    def _tree_selected(self) -> None:
        items = self.tree.selectedItems()
        if items:
            cid = items[0].data(0, Qt.UserRole)
            self._current = cid
            self._show(cid)
            self.component_selected.emit(cid)

    def _show_overview(self) -> None:
        if not self.design:
            self.details.setHtml("<i>No design loaded.</i>")
            return
        rows = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(', '.join(f'{v} {k}' for k, v in s.items()))}</td></tr>"
                       for a, s in structure_summary(self.design).items())
        out = f"<h3>{html.escape(self.design.name)}</h3><p>{len(self.design.components)} components</p>"
        out += f"<h4>Structure</h4><table cellspacing=4>{rows}</table>"
        ev = self.evaluation
        if ev is not None and ev.notes:
            out += "<h4>Design notes</h4><ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in ev.notes) + "</ul>"
        if ev is not None and ev.converged:
            out += "<h4>Constraints</h4><table cellspacing=3>"
            for c in ev.constraints:
                colour = "#4caf50" if c.satisfied else "#e0533d"
                lim = []
                if c.lower > -1e30:
                    lim.append(f"≥ {c.lower:.3g}")
                if c.upper < 1e30:
                    lim.append(f"≤ {c.upper:.3g}")
                out += (f"<tr><td style='color:{colour}'>●</td><td>{html.escape(c.name)}</td>"
                        f"<td align=right>{c.value:.3g} {c.unit}</td><td>{' '.join(lim)}</td></tr>")
            out += "</table><h4>Mass breakdown (kg)</h4><table cellspacing=3>"
            for k, v in sorted(ev.weights.items(), key=lambda kv: -kv[1]):
                out += f"<tr><td>{k.replace('_', ' ')}</td><td align=right>{v:,.0f}</td></tr>"
            out += "</table>"
        elif ev is not None:
            out += f"<p style='color:#e0533d'>{html.escape(ev.message)}</p>"
        self.details.setHtml(out)

    def _show(self, cid: str) -> None:
        c = self.design.components[cid]
        pos = ", ".join(f"{v:.3f}" for v in c.placement.position)
        rot = ", ".join(f"{v:.2f}" for v in c.placement.rotation_deg)
        out = (f"<h3>{html.escape(c.name)}</h3><table cellspacing=3>"
               f"<tr><td><b>ID</b></td><td><code>{html.escape(c.id)}</code></td></tr>"
               f"<tr><td><b>Type</b></td><td>{c.type.value}</td></tr>"
               f"<tr><td><b>Position</b></td><td>{pos} m</td></tr>"
               f"<tr><td><b>Rotation</b></td><td>{rot} deg (roll, pitch, yaw)</td></tr>"
               f"<tr><td><b>Generated from</b></td><td>{', '.join(c.source_groups)}</td></tr>")
        if c.parent:
            out += f"<tr><td><b>Parent</b></td><td><a href='comp:{c.parent}'>{html.escape(c.parent)}</a></td></tr>"
        mesh = c.local_mesh()
        out += f"<tr><td><b>Mesh</b></td><td>{mesh.n_triangles:,} triangles, volume {mesh.signed_volume():.4f} m³</td></tr></table>"
        if c.connections:
            out += "<h4>Relationships</h4><ul>"
            for k in c.connections:
                pt = f" at ({', '.join(f'{v:.2f}' for v in k.point)})" if k.point else ""
                note = f" — {html.escape(k.note)}" if k.note else ""
                out += f"<li>{k.relation.value} <a href='comp:{k.target}'>{html.escape(k.target)}</a>{pt}{note}</li>"
            out += "</ul>"
        back = [o for o in self.design.connected(cid) if o not in {k.target for k in c.connections}]
        if back:
            out += "<h4>Referenced by</h4><p>" + ", ".join(f"<a href='comp:{o}'>{html.escape(o)}</a>" for o in back[:40])
            out += " …" if len(back) > 40 else ""
            out += "</p>"
        st = self.evaluation.structure if self.evaluation is not None else None
        if st is not None and cid in st.member_results:
            u = st.utilisation.get(cid)
            out += "<h4>Structural analysis</h4>"
            if u is not None:
                colour = "#4caf50" if u < 0.8 else "#e8a33d" if u <= 1.0 else "#e0533d"
                out += f"<p>Utilisation <b style='color:{colour}'>{u:.2f}</b> (margin of safety {1 / u - 1:+.2f})</p>" \
                    if u > 0 else ""
            out += "<table cellspacing=2>"
            for k, v in st.member_results[cid].items():
                vs = f"{v:,.3g}" if isinstance(v, float) else html.escape(str(v))
                out += f"<tr><td>{html.escape(k.replace('_', ' '))}</td><td align=right>{vs}</td></tr>"
            out += "</table>"
        if c.parameters:
            out += "<h4>Parameters</h4><table cellspacing=2>"
            for k, v in c.parameters.items():
                vs = f"{v:.4g}" if isinstance(v, float) else html.escape(str(v))
                out += f"<tr><td>{html.escape(k)}</td><td>{vs}</td></tr>"
            out += "</table>"
        self.details.setHtml(out)
