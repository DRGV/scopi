"""Light/dark theme and touch-friendly sizing (HiDPI scaling itself is handled by Qt6)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

TOUCH_QSS = """
QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { min-height: 36px; }
QMenu::item { padding: 10px 28px; }
QMenuBar::item { padding: 8px 12px; }
QTabBar::tab { min-height: 34px; padding: 4px 14px; }
QSlider::groove:horizontal { height: 8px; }
QSlider::handle:horizontal { width: 26px; margin: -10px 0; }
QHeaderView::section { min-height: 30px; }
QCheckBox::indicator { width: 22px; height: 22px; }
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(45, 45, 48))
    p.setColor(QPalette.ColorRole.WindowText, c(230, 230, 230))
    p.setColor(QPalette.ColorRole.Base, c(30, 30, 30))
    p.setColor(QPalette.ColorRole.AlternateBase, c(45, 45, 48))
    p.setColor(QPalette.ColorRole.Text, c(230, 230, 230))
    p.setColor(QPalette.ColorRole.Button, c(58, 58, 62))
    p.setColor(QPalette.ColorRole.ButtonText, c(230, 230, 230))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(60, 60, 60))
    p.setColor(QPalette.ColorRole.ToolTipText, c(230, 230, 230))
    p.setColor(QPalette.ColorRole.Highlight, c(42, 130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, c(255, 255, 255))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(140, 140, 140))
    return p


def apply_theme(app: QApplication, mode: str = "auto", touch: bool = False) -> None:
    """mode: 'light' | 'dark' | 'auto' (follow the system colour scheme)."""
    app.setStyle("Fusion")
    if mode == "auto":
        scheme = app.styleHints().colorScheme()
        from PySide6.QtCore import Qt

        mode = "dark" if scheme == Qt.ColorScheme.Dark else "light"
    app.setPalette(_dark_palette() if mode == "dark" else app.style().standardPalette())
    app.setStyleSheet(TOUCH_QSS if touch else "")


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128
