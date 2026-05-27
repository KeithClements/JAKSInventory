"""UI theme stylesheet."""

from __future__ import annotations

__all__ = [
    "THEME_COLORS",
    "STATUS_BADGE_COLORS",
    "BUTTON_STYLES",
    "get_theme_stylesheet",
    "get_status_badge_style",
    "get_button_style",
]

# Diesel-shop dark palette (orange accent).
# Re-pointing these tokens propagates the theme to every screen that calls
# get_theme_stylesheet() / get_button_style() / get_status_badge_style().
THEME_COLORS = {
    "primary": "#ff8c42",        # Orange accent — primary actions
    "primary_hover": "#b85d1f",
    "success": "#3fb950",        # Green
    "warning": "#d29922",        # Amber
    "danger": "#f85149",         # Red
    "danger_hover": "#d83a3a",
    "bg": "#0f1419",             # App background
    "panel": "#1a2128",          # Card / panel background
    "field": "#232c35",          # Input field background
    "border": "#2d3a45",         # Subtle border / divider
    "text": "#e6edf3",           # Primary text
    "text_muted": "#8b98a5",     # Secondary text
    "header_bg": "#232c35",      # Table headers, toolbars
}

# Status badge color schemes — dark-friendly (translucent fill + bright text).
STATUS_BADGE_COLORS = {
    "ready":        {"bg": "rgba(63,185,80,0.18)",   "text": "#3fb950", "border": "#3fb950"},
    "uploaded":     {"bg": "rgba(88,166,255,0.18)",  "text": "#58a6ff", "border": "#58a6ff"},
    "pending":      {"bg": "rgba(210,153,34,0.18)",  "text": "#d29922", "border": "#d29922"},
    "review":       {"bg": "rgba(210,153,34,0.18)",  "text": "#d29922", "border": "#d29922"},
    "error":        {"bg": "rgba(248,81,73,0.18)",   "text": "#f85149", "border": "#f85149"},
    "draft":        {"bg": "rgba(139,152,165,0.15)", "text": "#8b98a5", "border": "#3d4d5c"},
    "modified":     {"bg": "rgba(188,140,255,0.18)", "text": "#bc8cff", "border": "#bc8cff"},
    "synced":       {"bg": "rgba(88,166,255,0.18)",  "text": "#58a6ff", "border": "#58a6ff"},
    "low_stock":    {"bg": "rgba(210,153,34,0.18)",  "text": "#d29922", "border": "#d29922"},
    "out_of_stock": {"bg": "rgba(248,81,73,0.18)",   "text": "#f85149", "border": "#f85149"},
}

# Button variant styles — dark theme.
BUTTON_STYLES = {
    "primary": {
        "bg": "#ff8c42",
        "bg_hover": "#b85d1f",
        "text": "#FFFFFF",
        "border": "none",
    },
    "secondary": {
        "bg": "#232c35",
        "bg_hover": "#3d4d5c",
        "text": "#e6edf3",
        "border": "1px solid #2d3a45",
    },
    "success": {
        "bg": "#3fb950",
        "bg_hover": "#2ea043",
        "text": "#0f1419",
        "border": "none",
    },
    "danger": {
        "bg": "#f85149",
        "bg_hover": "#d83a3a",
        "text": "#FFFFFF",
        "border": "none",
    },
    "warning": {
        "bg": "#d29922",
        "bg_hover": "#b88420",
        "text": "#0f1419",
        "border": "none",
    },
}


def get_status_badge_style(status: str) -> str:
    """Get CSS style for a status badge."""
    colors = STATUS_BADGE_COLORS.get(status.lower(), STATUS_BADGE_COLORS["draft"])
    return f"""
        background-color: {colors['bg']};
        color: {colors['text']};
        border: 1px solid {colors['border']};
        border-radius: 10px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: bold;
    """


def get_button_style(variant: str = "primary") -> str:
    """Get stylesheet for a button variant.
    
    Args:
        variant: One of 'primary', 'secondary', 'success', 'danger', 'warning'
    """
    style = BUTTON_STYLES.get(variant, BUTTON_STYLES["primary"])
    return f"""
        QPushButton {{
            background-color: {style['bg']};
            color: {style['text']};
            border: {style['border']};
            border-radius: 4px;
            padding: 8px 16px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background-color: {style['bg_hover']};
        }}
        QPushButton:disabled {{
            background-color: #1a2128;
            color: #5d6b78;
            border: 1px solid #2d3a45;
        }}
    """


def get_theme_stylesheet(*, dark: bool = False) -> str:
    """Return olive green theme stylesheet.
    
    Args:
        dark: If True, return dark mode variant (currently not implemented).
    """
    c = THEME_COLORS

    # Build absolute path to checkmark SVG for QSS image: url(...)
    import pathlib as _pathlib
    _check_svg = _pathlib.Path(__file__).resolve().parent.parent / "jaks_inventory" / "assets" / "checkmark.svg"
    _check_url = str(_check_svg).replace("\\", "/")
    
    return f"""
QMainWindow, QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
    font-family: 'Segoe UI', Arial, sans-serif;
}}

QLabel {{
    color: {c['text']};
}}

QLineEdit, QComboBox, QSpinBox {{
    background-color: {c['field']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 5px 8px;
    min-height: 20px;
}}

QLineEdit:focus, QComboBox:focus {{
    border-color: {c['primary']};
}}

QTextEdit {{
    background-color: {c['field']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 4px;
}}

QTableWidget {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    gridline-color: {c['border']};
    alternate-background-color: #1f262e;
}}

QTableWidget::item {{
    padding: 6px 8px;
}}

QTableWidget::item:selected {{
    background-color: {c['primary']};
    color: white;
}}

QTableWidget::item:hover:!selected {{
    background-color: #2a323c;
}}

QPushButton {{
    background-color: {c['primary']};
    color: white;
    border: none;
    border-radius: 4px;
    padding: 8px 16px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {c['primary_hover']};
}}

QPushButton:pressed {{
    background-color: #9a4e1a;
}}

QPushButton:disabled {{
    background-color: #1a2128;
    color: #5d6b78;
    border: 1px solid #2d3a45;
}}

QPushButton:checked {{
    background-color: {c['success']};
}}

QPushButton#phase_button {{
    background-color: {c['success']};
}}

QPushButton#phase_button:hover {{
    background-color: #2ea043;
}}

QTabWidget::pane {{
    border: 1px solid {c['border']};
    background: {c['panel']};
    border-radius: 4px;
}}

QTabBar::tab {{
    background: {c['header_bg']};
    border: 1px solid {c['border']};
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}

QTabBar::tab:selected {{
    background: {c['panel']};
    border-bottom-color: {c['panel']};
}}

QTabBar::tab:hover {{
    background: {c['panel']};
}}

QGroupBox {{
    font-weight: bold;
    border: 1px solid {c['border']};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {c['text']};
}}

QProgressBar {{
    border: 1px solid {c['border']};
    border-radius: 4px;
    text-align: center;
    background: {c['field']};
}}

QProgressBar::chunk {{
    background-color: {c['primary']};
    border-radius: 3px;
}}

QScrollBar:vertical {{
    border: none;
    background: {c['bg']};
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {c['border']};
    min-height: 20px;
    border-radius: 5px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    border: none;
    background: {c['bg']};
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {c['border']};
    min-width: 20px;
    border-radius: 5px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QHeaderView::section {{
    background-color: {c['header_bg']};
    color: {c['text']};
    padding: 8px;
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    font-weight: 500;
}}

QCheckBox {{
    color: {c['text']};
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c['border']};
    border-radius: 4px;
    background: {c['field']};
}}

QCheckBox::indicator:checked {{
    background-color: {c['primary']};
    border-color: {c['primary']};
    image: url("{_check_url}");
}}

QCheckBox::indicator:checked:hover {{
    background-color: {c['primary_hover']};
    border-color: {c['primary_hover']};
}}

QCheckBox::indicator:hover {{
    border-color: {c['primary']};
}}

QCheckBox::indicator:disabled {{
    background: #1a2128;
    border-color: #2d3a45;
}}

QCheckBox::indicator:checked:disabled {{
    background: #5d6b78;
    border-color: #5d6b78;
}}

QTableWidget::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c['border']};
    border-radius: 4px;
    background: {c['field']};
}}

QTableWidget::indicator:checked {{
    background-color: {c['primary']};
    border-color: {c['primary']};
    image: url("{_check_url}");
}}

QTableWidget::indicator:unchecked {{
    background: {c['field']};
    border-color: {c['border']};
}}

QToolBar {{
    background: {c['header_bg']};
    border: none;
    spacing: 4px;
    padding: 4px;
}}

QToolButton {{
    background: transparent;
    border: none;
    padding: 6px;
    border-radius: 4px;
}}

QToolButton:hover {{
    background: {c['panel']};
}}

QToolButton:pressed {{
    background: {c['border']};
}}

QMenu {{
    background: {c['panel']};
    border: 1px solid {c['border']};
    padding: 4px;
}}

QMenu::item {{
    padding: 8px 24px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background: {c['primary']};
    color: white;
}}

QMenuBar {{
    background: {c['header_bg']};
    border-bottom: 1px solid {c['border']};
}}

QMenuBar::item {{
    padding: 8px 12px;
}}

QMenuBar::item:selected {{
    background: {c['panel']};
}}
"""
