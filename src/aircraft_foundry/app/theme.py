"""A calm, consistent look: soft neutral surfaces, one muted accent, clear grouping.

Light and dark variants follow the operating system's colour scheme.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = dict(window="#f2f3f5", base="#ffffff", alt="#f7f8fa", text="#1f2328", muted="#6b7280", border="#d5d9df",
             button="#ffffff", button_hover="#eef1f5", accent="#3b6ea5", accent_text="#ffffff", group="#fafbfc",
             nav_sel="#e3ebf5")
DARK = dict(window="#202327", base="#2a2e33", alt="#30343a", text="#e6e8eb", muted="#9aa1ab", border="#3d434b",
            button="#30353b", button_hover="#3a4047", accent="#6c9bd2", accent_text="#11161c", group="#262a2f",
            nav_sel="#33414f")


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def stylesheet(c: dict) -> str:
    return f"""
QWidget {{ font-size: 13px; }}
QMainWindow, QDialog {{ background: {c['window']}; }}
QGroupBox {{
    background: {c['group']}; border: 1px solid {c['border']}; border-radius: 7px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['text']}; }}
QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: normal; }}
QPushButton {{
    background: {c['button']}; border: 1px solid {c['border']}; border-radius: 5px; padding: 5px 12px;
    color: {c['text']};
}}
QPushButton:hover {{ background: {c['button_hover']}; }}
QPushButton:pressed {{ background: {c['nav_sel']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']};
                       font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent']}; border-color: {c['text']}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {c['base']}; border: 1px solid {c['border']}; border-radius: 4px; padding: 3px 6px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 6px; top: -1px; background: {c['base']}; }}
QTabBar::tab {{ padding: 6px 12px; border: 1px solid transparent; border-bottom: none; color: {c['muted']}; }}
QTabBar::tab:selected {{ color: {c['text']}; background: {c['base']}; border-color: {c['border']};
                         border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: 600; }}
QDockWidget {{ font-weight: 600; }}
QDockWidget::title {{ background: {c['window']}; padding: 6px 8px; border-bottom: 1px solid {c['border']}; }}
QListWidget#nav {{ background: {c['window']}; border: none; border-right: 1px solid {c['border']}; outline: 0; }}
QListWidget#nav::item {{ padding: 9px 10px; margin: 1px 4px; border-radius: 6px; color: {c['text']}; }}
QListWidget#nav::item:hover {{ background: {c['button_hover']}; }}
QListWidget#nav::item:selected {{ background: {c['nav_sel']}; color: {c['text']}; font-weight: 600; }}
QLabel#hint {{ color: {c['muted']}; font-size: 12px; }}
QLabel#pageTitle {{ font-size: 17px; font-weight: 600; padding: 4px 2px 8px 2px; }}
QHeaderView::section {{ background: {c['alt']}; border: none; border-bottom: 1px solid {c['border']};
                        padding: 4px 6px; font-weight: 600; }}
QTableView, QTreeView, QListView {{ background: {c['base']}; alternate-background-color: {c['alt']};
                                    border: 1px solid {c['border']}; border-radius: 6px; }}
QScrollArea {{ border: none; background: transparent; }}
QToolBar {{ background: {c['window']}; border: none; spacing: 4px; padding: 3px; }}
QToolButton {{ border-radius: 5px; padding: 4px 8px; }}
QToolButton:hover {{ background: {c['button_hover']}; }}
QToolButton:checked {{ background: {c['nav_sel']}; }}
QStatusBar {{ background: {c['window']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; height: 14px; margin: -6px 0;
                              border-radius: 7px; }}
QProgressBar {{ border: 1px solid {c['border']}; border-radius: 4px; text-align: center; background: {c['base']}; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
"""


def apply_theme(app) -> dict:
    app.setStyle("Fusion")
    c = DARK if is_dark() else LIGHT
    pal = QPalette()
    for role, key in ((QPalette.Window, "window"), (QPalette.Base, "base"), (QPalette.AlternateBase, "alt"),
                      (QPalette.Text, "text"), (QPalette.WindowText, "text"), (QPalette.Button, "button"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"),
                      (QPalette.HighlightedText, "accent_text"), (QPalette.ToolTipBase, "base"),
                      (QPalette.ToolTipText, "text"), (QPalette.PlaceholderText, "muted"), (QPalette.Mid, "border")):
        pal.setColor(role, QColor(c[key]))
    app.setPalette(pal)
    f = QFont(app.font())
    f.setPointSizeF(max(f.pointSizeF(), 10.0))
    app.setFont(f)
    app.setStyleSheet(stylesheet(c))
    return c
