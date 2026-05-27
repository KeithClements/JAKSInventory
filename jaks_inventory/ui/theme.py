"""App-wide theming (light / dark).

Call :func:`apply_theme` once after constructing the :class:`QApplication`.
The current theme is persisted via ``db.get_setting`` /
``db.set_setting`` under the key ``"ui_theme"`` (values ``"light"`` or
``"dark"``).

Dark palette mirrors the May-2026 Quote UI mockup:

* Background      ``#1a1f24``  (charcoal)
* Panel           ``#222a30``
* Border          ``#2f3942``
* Text primary    ``#e6e9eb``
* Text dim        ``#9aa3ad``
* Accent (olive)  ``#7c8f4d``
* Accent hover    ``#8fa55a``
* Success         ``#5ed26a``
* Warning         ``#f0b454``
* Danger          ``#e06464``
"""
from __future__ import annotations

import logging
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

Theme = Literal["light", "dark"]
_SETTING_KEY = "ui_theme"

# ---------------------------------------------------------------------------
# Palette tokens
# ---------------------------------------------------------------------------

DARK = {
    "bg":           "#1a1f24",
    "panel":        "#222a30",
    "panel_alt":    "#1f262c",
    "border":       "#2f3942",
    "border_soft":  "#262d34",
    "text":         "#e6e9eb",
    "text_dim":     "#9aa3ad",
    "text_muted":   "#6b747e",
    "accent":       "#7c8f4d",
    "accent_hover": "#8fa55a",
    "accent_text":  "#ffffff",
    "success":      "#5ed26a",
    "warning":      "#f0b454",
    "danger":       "#e06464",
    "selection":    "#3a4a32",
    "row_alt":      "#1d242a",
    "header_bg":    "#161b20",
    "input_bg":     "#161b20",
}

LIGHT = {
    "bg":           "#f4f5f3",
    "panel":        "#ffffff",
    "panel_alt":    "#fafafa",
    "border":       "#d0d4d8",
    "border_soft":  "#e3e6e8",
    "text":         "#1c2226",
    "text_dim":     "#4a5159",
    "text_muted":   "#6b7280",
    "accent":       "#6B8E23",
    "accent_hover": "#556B2F",
    "accent_text":  "#ffffff",
    "success":      "#2e7d32",
    "warning":      "#C2410C",
    "danger":       "#b91c1c",
    "selection":    "#d8e3c0",
    "row_alt":      "#f7f7f4",
    "header_bg":    "#ececec",
    "input_bg":     "#ffffff",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def palette_for(theme: Theme) -> dict[str, str]:
    """Return the token dict for the named theme."""
    return DARK if theme == "dark" else LIGHT


def current_theme() -> Theme:
    """Read the persisted theme (defaults to ``"light"``)."""
    try:
        from db import get_setting
        val = (get_setting(_SETTING_KEY) or "light").strip().lower()
    except Exception:
        val = "light"
    return "dark" if val == "dark" else "light"


def set_theme_setting(theme: Theme) -> None:
    """Persist the user's theme choice."""
    try:
        from db import set_setting
        set_setting(_SETTING_KEY, theme)
    except Exception as exc:
        log.debug("set_theme_setting failed: %s", exc)


def apply_theme(app: QApplication, theme: Theme | None = None) -> Theme:
    """Apply the named theme to the live app. Returns the applied theme."""
    if theme is None:
        theme = current_theme()
    pal = palette_for(theme)

    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    if theme == "dark":
        _apply_dark_palette(app, pal)
    else:
        _apply_light_palette(app, pal)

    app.setStyleSheet(_build_stylesheet(pal, theme))
    log.info("Applied %s theme.", theme)
    return theme


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _apply_dark_palette(app: QApplication, p: dict[str, str]) -> None:
    pal = QPalette()
    bg, panel, text, dim = QColor(p["bg"]), QColor(p["panel"]), QColor(p["text"]), QColor(p["text_dim"])
    accent = QColor(p["accent"])
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, QColor(p["input_bg"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p["row_alt"]))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p["text_muted"]))
    pal.setColor(QPalette.ColorRole.Button, panel)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p["accent_text"]))
    pal.setColor(QPalette.ColorRole.Link, QColor(p["accent_hover"]))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, dim)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, dim)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, dim)
    app.setPalette(pal)


def _apply_light_palette(app: QApplication, p: dict[str, str]) -> None:
    # Reset to Qt default light palette, then nudge highlight color.
    pal = app.style().standardPalette() if app.style() else QPalette()
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p["accent_text"]))
    app.setPalette(pal)


def _build_stylesheet(p: dict[str, str], theme: Theme) -> str:
    is_dark = theme == "dark"
    # Common rules, parameterised by tokens.
    return f"""
/* ── Global ─────────────────────────────────────────────────────── */
QWidget {{
    color: {p["text"]};
    background: {p["bg"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["accent_text"]};
}}
QToolTip {{
    color: {p["text"]};
    background: {p["panel"]};
    border: 1px solid {p["border"]};
    padding: 4px 6px;
}}

/* ── Frames / Group boxes ───────────────────────────────────────── */
QFrame, QGroupBox, QDialog, QMainWindow, QScrollArea {{
    background: {p["bg"]};
}}
QGroupBox {{
    border: 1px solid {p["border"]};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {p["text_dim"]};
}}

/* ── Inputs ─────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
QDateEdit, QDateTimeEdit, QComboBox {{
    background: {p["input_bg"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 3px;
    padding: 4px 6px;
    selection-background-color: {p["accent"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QDateTimeEdit:focus, QComboBox:focus {{
    border: 1px solid {p["accent"]};
}}
QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QComboBox:disabled, QDateEdit:disabled {{
    color: {p["text_muted"]};
    background: {p["panel_alt"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background: {p["panel"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["accent_text"]};
}}

/* ── Buttons ────────────────────────────────────────────────────── */
QPushButton {{
    background: {p["panel"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: 4px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    background: {p["panel_alt"]};
    border-color: {p["accent"]};
}}
QPushButton:pressed {{
    background: {p["border_soft"]};
}}
QPushButton:disabled {{
    color: {p["text_muted"]};
    background: {p["panel_alt"]};
    border-color: {p["border_soft"]};
}}
QPushButton[primary="true"] {{
    background: {p["accent"]};
    color: {p["accent_text"]};
    border: 1px solid {p["accent"]};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background: {p["accent_hover"]};
    border-color: {p["accent_hover"]};
}}
QPushButton[danger="true"] {{
    background: {p["danger"]};
    color: white;
    border: 1px solid {p["danger"]};
}}

/* ── Tables ─────────────────────────────────────────────────────── */
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget, QListView {{
    background: {p["panel"]};
    alternate-background-color: {p["row_alt"]};
    color: {p["text"]};
    gridline-color: {p["border_soft"]};
    border: 1px solid {p["border"]};
    selection-background-color: {p["selection"]};
    selection-color: {p["text"]};
}}
QHeaderView::section {{
    background: {p["header_bg"]};
    color: {p["text_dim"]};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {p["border_soft"]};
    border-bottom: 1px solid {p["border"]};
    font-weight: 600;
}}
QTableCornerButton::section {{
    background: {p["header_bg"]};
    border: 1px solid {p["border"]};
}}

/* ── Tabs ───────────────────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {p["border"]};
    background: {p["panel"]};
    top: -1px;
}}
QTabBar::tab {{
    background: {p["bg"]};
    color: {p["text_dim"]};
    padding: 7px 14px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:hover {{
    color: {p["text"]};
}}
QTabBar::tab:selected {{
    background: {p["panel"]};
    color: {p["text"]};
    border-color: {p["border"]};
    font-weight: 600;
}}

/* ── Scrollbars ─────────────────────────────────────────────────── */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {p["bg"]};
    border: none;
    {'width: 11px;' if is_dark else 'width: 13px;'}
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p["border"]};
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background: {p["text_muted"]};
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── Menus ──────────────────────────────────────────────────────── */
QMenuBar {{
    background: {p["bg"]};
    color: {p["text"]};
}}
QMenuBar::item:selected {{
    background: {p["panel_alt"]};
}}
QMenu {{
    background: {p["panel"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
}}
QMenu::item:selected {{
    background: {p["accent"]};
    color: {p["accent_text"]};
}}
QMenu::separator {{
    height: 1px;
    background: {p["border_soft"]};
    margin: 4px 6px;
}}

/* ── Labels / status ────────────────────────────────────────────── */
QLabel[role="title"] {{
    font-size: 16pt;
    font-weight: 700;
    color: {p["text"]};
}}
QLabel[role="dim"] {{
    color: {p["text_dim"]};
}}
QLabel[pill="open"], QLabel[pill="draft"], QLabel[pill="day"] {{
    padding: 2px 8px;
    border-radius: 9px;
    font-size: 9pt;
    font-weight: 600;
}}
QLabel[pill="open"]   {{ background: {p["accent"]};  color: {p["accent_text"]}; }}
QLabel[pill="draft"]  {{ background: {p["warning"]}; color: #1a1a1a; }}
QLabel[pill="day"]    {{ background: {p["panel"]};   color: {p["text_dim"]}; border: 1px solid {p["border"]}; }}

/* ── Splitter ───────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {p["border_soft"]};
}}
QSplitter::handle:hover {{
    background: {p["accent"]};
}}
"""
