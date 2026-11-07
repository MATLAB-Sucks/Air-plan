"""Top-view planform thumbnails drawn from the same Arrangement the engine uses."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QPolygonF

from ..core.parameters import AircraftParameters
from ..geometry.arrangement import Arrangement


def _surface_outline(arr_surface, placement, sides=(1, -1)):
    """Planform outline, following any crank (flying wing / BWB)."""
    etas = [0.0, *arr_surface.breaks(), 1.0]
    polys = []
    for side in sides:
        le = [placement.apply(np.array([[float(arr_surface.x_le(e)), side * e * arr_surface.semi_span, 0]]))[0]
              for e in etas]
        te = [placement.apply(np.array([[float(arr_surface.x_le(e) + arr_surface.chord(e)),
                                         side * e * arr_surface.semi_span, 0]]))[0] for e in etas[::-1]]
        polys.append(np.array(le + te)[:, :2])
    return polys


def planform_pixmap(params: AircraftParameters, width: int = 132, height: int = 96,
                    feasible: bool = True) -> QPixmap:
    arr = Arrangement(params)
    fus = arr.fuselage
    xs = np.linspace(0, fus.length, 40)
    hw = fus.half_width(xs)
    shapes = []
    for body in arr.bodies:  # fuselage / pod and tail booms
        bxs = np.linspace(0, body.shape.length, 30)
        bhw = body.shape.half_width(bxs)
        x0, y0, _ = body.offset
        shapes.append(("fus", np.column_stack([x0 + np.r_[bxs, bxs[::-1]], y0 + np.r_[bhw, -bhw[::-1]]])))
    for ps in arr.surfaces:  # top view of every lifting surface (fins seen edge-on)
        if ps.surface.vertical:
            pos = ps.placement.position
            t = ps.surface
            shapes.append(("tail", np.array([[pos[0], pos[1] - 0.05], [pos[0] + t.root_chord, pos[1] - 0.05],
                                             [pos[0] + t.root_chord, pos[1] + 0.05], [pos[0], pos[1] + 0.05]])))
            continue
        kind = "wing" if ps.role == "wing" else "tail"
        shapes += [(kind, q) for q in _surface_outline(ps.surface, ps.placement, ps.sides)]
    for e in arr.engines:
        x0, y = e.inlet[0], e.inlet[1]
        shapes.append(("eng", np.array([[x0, y - e.diameter / 2], [x0 + e.length, y - e.diameter / 2],
                                        [x0 + e.length, y + e.diameter / 2], [x0, y + e.diameter / 2]])))
    allpts = np.vstack([s for _, s in shapes])
    lo, hi = allpts.min(0), allpts.max(0)
    margin = 6
    # nose up: aircraft x maps to screen y, aircraft y maps to screen x
    scale = min((width - 2 * margin) / max(hi[1] - lo[1], 1e-6), (height - 2 * margin) / max(hi[0] - lo[0], 1e-6))
    cx, cy = width / 2, (height - (hi[0] - lo[0]) * scale) / 2

    def to_screen(p):
        return QPointF(cx + p[1] * scale, cy + (p[0] - lo[0]) * scale)

    pm = QPixmap(width, height)
    pm.fill(QColor(36, 40, 46))
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    colours = {"fus": QColor(205, 210, 218), "wing": QColor(150, 170, 196), "tail": QColor(150, 170, 196),
               "eng": QColor(110, 118, 130)}
    for kind in ("wing", "tail", "fus", "eng"):
        for k, poly in shapes:
            if k != kind:
                continue
            painter.setBrush(QBrush(colours[k]))
            painter.setPen(QPen(QColor(20, 22, 26), 0.8))
            painter.drawPolygon(QPolygonF([to_screen(p) for p in poly]))
    if not feasible:
        painter.setPen(QPen(QColor(230, 90, 70), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(1, 1, width - 2, height - 2)
    painter.end()
    return pm
