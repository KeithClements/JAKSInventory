"""Quick Navigation dialog – press F1 or type a shortcut code to jump to any screen."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

# Shortcut code → screen name mapping.
# Codes: first letter(s) of category + number for disambiguation, comma, child index.
# Two categories start with P → P1 = Purchasing, P2 = Pricing.
# Two categories start with S → S = Sales, SY = System.
_NAV_CODES: list[tuple[str, str, str]] = [
    # --- DASHBOARD ---
    ("D", "Dashboard", "Dashboard"),

    # --- SALES (S) ---
    ("S1", "Quotes", "Quotes"),
    ("S2", "Sales Orders", "Sales Orders"),
    ("S3", "Invoices", "Invoices"),
    ("S4", "CRM", "CRM"),
    ("S5", "Returns", "Returns"),
    ("S6", "Cores", "Cores"),

    # --- INVENTORY (I) ---
    ("I1", "Products", "Products"),
    ("I2", "Locations", "Locations"),
    ("I3", "Kits", "Kits"),
    ("I4", "Audit", "Audit"),

    # --- PURCHASING (P1) ---
    ("P1,1", "Purchase Orders", "Purchase Orders"),
    ("P1,2", "PO Receipts", "PO Receipts"),
    ("P1,3", "Vendors", "Vendors"),
    ("P1,4", "Vendor Returns", "Vendor Returns"),
    ("P1,5", "Low Stock & Reorder", "Low Stock & Reorder"),

    # --- PRICING (P2) ---
    ("P2,1", "Price Lists", "Price Lists"),
    ("P2,2", "Pricing Maintenance", "Pricing Maintenance"),
    ("P2,3", "Tiered Pricing", "Tiered Pricing"),

    # --- CUSTOMERS (C) ---
    ("C1", "Customer Maintenance", "Customer Maintenance"),
    ("C2", "New Customer", "New Customer"),
    ("C3", "Customer Core Report", "Customer Core Report"),

    # --- ACCOUNTING (A) ---
    ("A1", "Margins", "Margins"),
    ("A2", "QBO", "QBO"),
    ("A3", "Aging AR", "Aging AR"),
    ("A4", "Reports", "Reports"),

    # --- TOOLS (T) ---
    ("T1", "Barcodes", "Barcodes"),
    ("T2", "Import", "Import"),
    ("T3", "HHP Scraper", "HHP Scraper"),
    ("T4", "Scraper Admin", "Scraper Admin"),

    # --- SYSTEM (SY) ---
    ("SY1", "Settings", "Settings"),
]

# Build a lookup dict (case-insensitive)
_CODE_TO_SCREEN: dict[str, str] = {
    code.upper().replace(" ", ""): screen for code, _label, screen in _NAV_CODES
}


class QuickNavDialog(QDialog):
    """Popup that lists all shortcut codes and lets the user type one to navigate."""

    navigate_to = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Navigation  (F1)")
        self.setMinimumWidth(420)
        self.setMaximumHeight(600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Container with styled background
        container = QWidget()
        container.setStyleSheet(
            "QWidget { background: #2d3a28; border-radius: 8px; }"
        )
        cl = QVBoxLayout(container)
        cl.setContentsMargins(16, 12, 16, 12)
        cl.setSpacing(10)

        # Header
        header = QLabel("⚡ Quick Navigation")
        header.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #d4e4c0; "
            "background: transparent;"
        )
        cl.addWidget(header)

        hint = QLabel("Type a code and press Enter  (Esc to close)")
        hint.setStyleSheet("font-size: 11px; color: #a0b090; background: transparent;")
        cl.addWidget(hint)

        # Input
        self._input = QLineEdit()
        self._input.setPlaceholderText("e.g.  S1 → Quotes,  P2,1 → Price Lists")
        self._input.setStyleSheet(
            "QLineEdit { background: #1e2a18; color: white; border: 1px solid #6B8E23; "
            "border-radius: 4px; padding: 8px 12px; font-size: 14px; font-weight: bold; }"
        )
        self._input.returnPressed.connect(self._on_go)
        self._input.textChanged.connect(self._on_text_changed)
        cl.addWidget(self._input)

        # Status / match preview
        self._status = QLabel("")
        self._status.setStyleSheet(
            "font-size: 12px; color: #c0d0b0; background: transparent; padding: 2px 4px;"
        )
        cl.addWidget(self._status)

        # ── Reference grid ──
        grid_label = QLabel("─── Reference ───")
        grid_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid_label.setStyleSheet(
            "color: #708060; font-size: 10px; background: transparent; margin-top: 4px;"
        )
        cl.addWidget(grid_label)

        # Group codes by category
        categories = [
            ("SALES", ["S1", "S2", "S3", "S4", "S5", "S6"]),
            ("INVENTORY", ["I1", "I2", "I3", "I4"]),
            ("PURCHASING", ["P1,1", "P1,2", "P1,3", "P1,4", "P1,5"]),
            ("PRICING", ["P2,1", "P2,2", "P2,3"]),
            ("CUSTOMERS", ["C1", "C2", "C3"]),
            ("ACCOUNTING", ["A1", "A2", "A3", "A4"]),
            ("TOOLS", ["T1", "T2", "T3", "T4"]),
            ("SYSTEM", ["SY1"]),
        ]

        code_map = {code: label for code, label, _screen in _NAV_CODES}

        mono = QFont("Consolas", 9)

        for cat_name, codes in categories:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            cat_lbl = QLabel(f"{cat_name}")
            cat_lbl.setFixedWidth(90)
            cat_lbl.setStyleSheet(
                "color: #8fa07a; font-size: 10px; font-weight: bold; background: transparent;"
            )
            row_layout.addWidget(cat_lbl)

            entries = []
            for c in codes:
                name = code_map.get(c, "?")
                entries.append(f"<b style='color:#b0c890'>{c}</b>"
                               f"<span style='color:#8a9a7a'> {name}</span>")

            items_lbl = QLabel("  ".join(entries))
            items_lbl.setFont(mono)
            items_lbl.setStyleSheet("font-size: 10px; background: transparent;")
            items_lbl.setTextFormat(Qt.TextFormat.RichText)
            items_lbl.setWordWrap(True)
            row_layout.addWidget(items_lbl, 1)

            cl.addLayout(row_layout)

        root.addWidget(container)
        self._input.setFocus()

    def _on_text_changed(self, text: str):
        key = text.strip().upper().replace(" ", "")
        if not key:
            self._status.setText("")
            return
        match = _CODE_TO_SCREEN.get(key)
        if match:
            self._status.setText(f"✅  → {match}")
            self._status.setStyleSheet(
                "font-size: 12px; color: #90ee90; background: transparent; padding: 2px 4px;"
            )
        else:
            # Check partial matches
            partials = [
                f"{c} → {l}" for c, l, _s in _NAV_CODES
                if c.upper().startswith(key)
            ]
            if partials:
                self._status.setText("  ".join(partials[:4]))
                self._status.setStyleSheet(
                    "font-size: 12px; color: #c0d0b0; background: transparent; padding: 2px 4px;"
                )
            else:
                self._status.setText("❌  No match")
                self._status.setStyleSheet(
                    "font-size: 12px; color: #ee9090; background: transparent; padding: 2px 4px;"
                )

    def _on_go(self):
        key = self._input.text().strip().upper().replace(" ", "")
        screen = _CODE_TO_SCREEN.get(key)
        if screen:
            self.navigate_to.emit(screen)
            self.accept()
        else:
            self._status.setText("❌  Unknown code — see reference below")
            self._status.setStyleSheet(
                "font-size: 12px; color: #ee9090; background: transparent; padding: 2px 4px;"
            )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
