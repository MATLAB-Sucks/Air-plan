"""Cockpit instrument panel drawn over the simulator's cockpit view: airspeed, artificial horizon,
altimeter, variometer, compass and slip ball - and a yaw string on the canopy for gliders."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class InstrumentPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # opaque: never translucent over the OpenGL view
        self.t: dict = {}
        self.glider = False
        self.vario_avg = 0.0
        self.warnings: list[str] = []

    def set_state(self, telemetry: dict, glider: bool, vario_avg: float, warnings: list[str]) -> None:
        self.t, self.glider, self.vario_avg, self.warnings = telemetry, glider, vario_avg, warnings
        self.update()

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, _):
        if not self.t:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        top = 34  # warnings band above the instruments
        ph = h - top
        p.fillRect(self.rect(), QColor(28, 30, 34))
        p.fillRect(QRectF(0, 0, w, top), QColor(18, 19, 22))  # glare shield
        p.setPen(QPen(QColor(55, 58, 64), 2))
        p.drawLine(QPointF(0, top), QPointF(w, top))
        t = self.t
        n = 6
        info_w = max(230.0, w * 0.26)  # the digital information column on the right
        gw = w - info_w
        r = min(ph * 0.40, gw / (2 * n + 1.2))
        cy = top + ph * 0.55
        xs = [gw / 2 + (i - (n - 1) / 2) * (2.18 * r) for i in range(n)]
        g = self.glider
        spd = t.get("airspeed", 0.0) * (3.6 if g else 1.944)
        self._dial(p, xs[0], cy, r, spd, 0, 300 if g else 400, "km/h" if g else "KT", "AIRSPEED", major=50)
        self._horizon(p, xs[1], cy, r, t.get("pitch", 0.0), t.get("roll", 0.0))
        alt_ft = t.get("altitude", 0.0) * 3.281
        self._altimeter(p, xs[2], cy, r, alt_ft, t.get("altitude", 0.0))
        vs = t.get("vs", 0.0)
        if g:
            self._vario(p, xs[3], cy, r, vs, self.vario_avg, 5.0, "m/s")
        else:
            self._vario(p, xs[3], cy, r, vs * 196.9 / 1000, None, 4.0, "×1000 ft/min")
        self._compass(p, xs[4], cy, r, t.get("heading", 0.0))
        self._slip(p, xs[5], cy, r, t.get("beta", 0.0), t.get("load_factor", 1.0))
        if g:  # yaw string on the glare shield, streaming opposite to the sideslip
            ax, ay = w / 2 - 160, 4
            ang = math.radians(max(-60.0, min(60.0, t.get("beta", 0.0) * 4)))
            p.setPen(QPen(QColor(230, 60, 60), 3, Qt.SolidLine, Qt.RoundCap))
            prev = QPointF(ax, ay)
            for k in range(1, 4):
                pt = QPointF(ax - math.sin(ang) * k * 9, ay + k * 9 * math.cos(ang))
                p.drawLine(prev, pt)
                prev = pt
            p.setPen(QColor(150, 150, 150))
            p.drawText(QRectF(ax + 10, 2, 80, top - 4), Qt.AlignVCenter | Qt.AlignLeft, "yaw string")
        self._info(p, w - info_w + 6, top + 8, info_w - 14, ph - 14)
        if self.warnings:
            f = QFont()
            f.setPointSize(13)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(255, 80, 60))
            p.drawText(QRectF(0, 2, w, top - 4), Qt.AlignCenter, "   ".join(self.warnings))
        p.end()

    def _bezel(self, p, x, y, r, label):
        p.setBrush(QColor(12, 12, 14))
        p.setPen(QPen(QColor(90, 92, 98), 3))
        p.drawEllipse(QPointF(x, y), r, r)
        f = QFont()
        f.setPointSizeF(max(6.0, r / 9))
        p.setFont(f)
        p.setPen(QColor(170, 175, 185))
        p.drawText(QRectF(x - r, y + r * 0.38, 2 * r, r * 0.3), Qt.AlignCenter, label)

    def _needle(self, p, x, y, r, frac, colour=QColor(255, 255, 255), length=0.82, width=3):
        a = math.radians(-225 + 270 * max(0.0, min(1.0, frac)))
        p.setPen(QPen(colour, width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(x, y), QPointF(x + math.cos(a) * r * length, y + math.sin(a) * r * length))

    def _dial(self, p, x, y, r, value, lo, hi, unit, label, major=50):
        self._bezel(p, x, y, r, label)
        p.setPen(QPen(QColor(220, 220, 220), 2))
        f = QFont()
        f.setPointSizeF(max(6.0, r / 10))
        p.setFont(f)
        v = lo
        while v <= hi:
            a = math.radians(-225 + 270 * (v - lo) / (hi - lo))
            p.drawLine(QPointF(x + math.cos(a) * r * 0.82, y + math.sin(a) * r * 0.82),
                       QPointF(x + math.cos(a) * r * 0.95, y + math.sin(a) * r * 0.95))
            p.drawText(QRectF(x + math.cos(a) * r * 0.62 - 16, y + math.sin(a) * r * 0.62 - 8, 32, 16),
                       Qt.AlignCenter, f"{v:.0f}")
            v += major
        self._needle(p, x, y, r, (value - lo) / (hi - lo))
        p.setPen(QColor(200, 205, 215))
        p.drawText(QRectF(x - r, y - r * 0.38, 2 * r, r * 0.3), Qt.AlignCenter, f"{value:.0f} {unit}")

    def _horizon(self, p, x, y, r, pitch, roll):
        p.save()
        clip = QPainterPath()
        clip.addEllipse(QPointF(x, y), r * 0.95, r * 0.95)
        p.setClipPath(clip)
        p.translate(x, y)
        p.rotate(-roll)
        off = pitch * r / 30.0
        p.fillRect(QRectF(-2 * r, -2 * r + off, 4 * r, 2 * r), QColor(60, 130, 210))
        p.fillRect(QRectF(-2 * r, off, 4 * r, 2 * r), QColor(140, 95, 55))
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(-2 * r, off), QPointF(2 * r, off))
        for d in (-20, -10, 10, 20):
            yy = off - d * r / 30.0
            p.drawLine(QPointF(-r * (0.25 if abs(d) == 10 else 0.4), yy), QPointF(r * (0.25 if abs(d) == 10 else 0.4), yy))
        p.restore()
        p.setPen(QPen(QColor(255, 200, 40), 4))
        p.drawLine(QPointF(x - r * 0.5, y), QPointF(x - r * 0.15, y))
        p.drawLine(QPointF(x + r * 0.15, y), QPointF(x + r * 0.5, y))
        p.drawEllipse(QPointF(x, y), 3, 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(90, 92, 98), 3))
        p.drawEllipse(QPointF(x, y), r, r)

    def _altimeter(self, p, x, y, r, ft, m):
        self._bezel(p, x, y, r, "ALTITUDE")
        p.setPen(QPen(QColor(220, 220, 220), 2))
        for k in range(10):
            a = math.radians(-90 + 36 * k)
            p.drawLine(QPointF(x + math.cos(a) * r * 0.8, y + math.sin(a) * r * 0.8),
                       QPointF(x + math.cos(a) * r * 0.95, y + math.sin(a) * r * 0.95))
        for frac, ln, wd in (((ft % 1000) / 1000, 0.85, 3), (((ft / 1000) % 10) / 10, 0.55, 5)):
            a = math.radians(-90 + 360 * frac)
            p.setPen(QPen(QColor(255, 255, 255), wd, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x, y), QPointF(x + math.cos(a) * r * ln, y + math.sin(a) * r * ln))
        p.setPen(QColor(200, 205, 215))
        p.drawText(QRectF(x - r, y - r * 0.42, 2 * r, r * 0.3), Qt.AlignCenter, f"{ft:,.0f} ft")
        p.drawText(QRectF(x - r, y + r * 0.12, 2 * r, r * 0.3), Qt.AlignCenter, f"{m:,.0f} m")

    def _vario(self, p, x, y, r, value, avg, full, unit):
        self._bezel(p, x, y, r, "VARIO" if avg is not None else "CLIMB")
        p.setPen(QPen(QColor(220, 220, 220), 2))
        f = QFont()
        f.setPointSizeF(max(6.0, r / 10))
        p.setFont(f)
        for k in range(-int(full), int(full) + 1):
            a = math.radians(180 + 160 * k / full)
            p.drawLine(QPointF(x + math.cos(a) * r * 0.82, y + math.sin(a) * r * 0.82),
                       QPointF(x + math.cos(a) * r * 0.95, y + math.sin(a) * r * 0.95))
            if k % (1 if full <= 5 else 2) == 0:
                p.drawText(QRectF(x + math.cos(a) * r * 0.64 - 12, y + math.sin(a) * r * 0.64 - 8, 24, 16),
                           Qt.AlignCenter, f"{abs(k)}")
        for val, colour, wd in ((avg, QColor(80, 200, 120), 2), (value, QColor(255, 255, 255), 3)):
            if val is None:
                continue
            a = math.radians(180 + 160 * max(-1.0, min(1.0, val / full)))
            p.setPen(QPen(colour, wd, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x, y), QPointF(x + math.cos(a) * r * 0.82, y + math.sin(a) * r * 0.82))
        p.setPen(QColor(200, 205, 215))
        p.drawText(QRectF(x - r * 0.2, y - r * 0.15, r * 1.1, r * 0.3), Qt.AlignCenter, unit)

    def _compass(self, p, x, y, r, hdg):
        self._bezel(p, x, y, r, "HEADING")
        f = QFont()
        f.setPointSizeF(max(6.0, r / 9))
        f.setBold(True)
        p.setFont(f)
        for k in range(0, 360, 30):
            a = math.radians(k - hdg - 90)
            txt = {0: "N", 90: "E", 180: "S", 270: "W"}.get(k, f"{k // 10}")
            p.setPen(QColor(255, 255, 255) if k % 90 == 0 else QColor(190, 190, 190))
            p.drawText(QRectF(x + math.cos(a) * r * 0.72 - 12, y + math.sin(a) * r * 0.72 - 9, 24, 18),
                       Qt.AlignCenter, txt)
        p.setPen(QPen(QColor(255, 200, 40), 3))
        p.drawLine(QPointF(x, y - r * 0.95), QPointF(x, y - r * 0.55))
        p.setPen(QColor(200, 205, 215))
        p.drawText(QRectF(x - r, y - r * 0.15, 2 * r, r * 0.3), Qt.AlignCenter, f"{hdg:03.0f}°")

    def _slip(self, p, x, y, r, beta, g):
        self._bezel(p, x, y, r, f"SLIP   {g:+.1f} g")
        p.setPen(QPen(QColor(220, 220, 220), 2))
        p.setBrush(Qt.NoBrush)
        tube = QRectF(x - r * 0.75, y - r * 0.12, r * 1.5, r * 0.24)
        p.drawRoundedRect(tube, r * 0.12, r * 0.12)
        p.drawLine(QPointF(x - r * 0.14, y - r * 0.16), QPointF(x - r * 0.14, y + r * 0.16))
        p.drawLine(QPointF(x + r * 0.14, y - r * 0.16), QPointF(x + r * 0.14, y + r * 0.16))
        bx = x + max(-1.0, min(1.0, -beta / 12.0)) * r * 0.62
        p.setBrush(QBrush(QColor(15, 15, 15)))
        p.setPen(QPen(QColor(240, 240, 240), 1))
        p.drawEllipse(QPointF(bx, y), r * 0.1, r * 0.1)



def _fmt_info(t: dict, glider: bool) -> list[tuple[str, str, object]]:
    rows = []
    k = 3.6 if glider else 1.944
    unit = "km/h" if glider else "kt"
    rows.append(("IAS / TAS", f"{t.get('ias', 0) * k:.0f} / {t.get('airspeed', 0) * k:.0f} {unit}", None))
    rows.append(("Ground speed", f"{t.get('gs', 0) * k:.0f} {unit}", None))
    if t.get("wind_spd", 0) > 0.3:
        rows.append(("Wind", f"{t['wind_from']:03.0f}° / {t['wind_spd'] * k:.0f} {unit}", None))
    else:
        rows.append(("Wind", "calm", None))
    if glider:
        n = t.get("netto", 0.0)
        rows.append(("Netto (air)", f"{n:+.1f} m/s", QColor(90, 220, 120) if n > 0.2 else
                     (QColor(240, 90, 80) if n < -0.8 else None)))
    fg = t.get("final_glide", 0.0)
    if "home" in t:
        rows.append((f"Home {t['home']}", f"{t['home_dist'] / 1000:.1f} km at {t['home_brg']:03.0f}°", None))
        rows.append(("Final glide" if glider else "Above glide home", f"{fg:+.0f} m",
                     QColor(90, 220, 120) if fg > 0 else QColor(240, 90, 80)))
    if glider:
        rows.append(("Airbrakes", f"{t.get('flaps', 0):.0%}", None))
        if t.get("motor") not in (None, "retracted"):
            rows.append(("Engine", f"{t['motor']} · {t.get('motor_fuel', 0) / 0.72:.1f} l", None))
    else:
        rows.append(("Flaps · gear", f"{t.get('flaps', 0):.0%} · {'down' if t.get('gear') else 'up'}", None))
        rows.append(("Throttle · fuel", f"{t.get('throttle', 0):.0%} · {t.get('fuel_kg', 0):,.0f} kg", None))
    return rows


def _info(self, p, x, y, w, h):
    """Digital readouts: speeds, wind (arrow relative to the nose), netto, home and final glide."""
    p.setPen(QPen(QColor(70, 74, 82), 1))
    p.setBrush(QColor(16, 17, 20))
    p.drawRoundedRect(QRectF(x, y, w, h), 8, 8)
    rows = _fmt_info(self.t, self.glider)
    f = QFont()
    f.setPointSizeF(max(7.5, min(11.0, h / (len(rows) + 1) * 0.55)))
    p.setFont(f)
    lh = (h - 12) / max(len(rows), 1)
    for k, (label, value, colour) in enumerate(rows):
        yy = y + 6 + k * lh
        p.setPen(QColor(150, 155, 165))
        p.drawText(QRectF(x + 10, yy, w * 0.48, lh), Qt.AlignVCenter | Qt.AlignLeft, label)
        p.setPen(colour or QColor(235, 238, 245))
        p.drawText(QRectF(x + w * 0.45, yy, w * 0.53, lh), Qt.AlignVCenter | Qt.AlignRight, value)
    if self.t.get("wind_spd", 0) > 0.3:  # wind arrow relative to the nose (top = ahead)
        rel = math.radians(self.t["wind_from"] - self.t.get("heading", 0.0) + 180.0)
        lh = (h - 12) / max(len(rows), 1)
        cx, cy, L = x + w * 0.40, y + 6 + 2.5 * lh, min(9.0, lh * 0.38)  # beside the Wind row
        p.setPen(QPen(QColor(120, 190, 255), 2.5))
        tip = QPointF(cx + math.sin(rel) * L, cy - math.cos(rel) * L)
        p.drawLine(QPointF(cx - math.sin(rel) * L, cy + math.cos(rel) * L), tip)
        p.setBrush(QColor(120, 190, 255))
        p.drawEllipse(tip, 3, 3)


InstrumentPanel._info = _info
