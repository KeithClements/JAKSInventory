"""Add New Product dialog — dark mockup layout.

Matches mockups/inventory_mockup.html with a 10-section scrolling form,
AI assistant banner, and per-section AI stub buttons.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import create_product
from jaks_inventory.services import ai_product_assistant as ai

logger = logging.getLogger(__name__)


class _AIWorker(QThread):
    """Runs an AI assistant function in the background."""
    done = Signal(dict, str)  # (result, task_label)
    failed = Signal(str, str)  # (error_message, task_label)

    def __init__(self, fn: Callable[[dict], dict], ctx: dict,
                 task_label: str, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._ctx = ctx
        self._label = task_label

    def run(self) -> None:
        try:
            result = self._fn(self._ctx) or {}
            self.done.emit(result, self._label)
        except Exception as exc:
            logger.exception("AI task '%s' failed", self._label)
            self.failed.emit(str(exc), self._label)


# ── Palette (matches inventory_mockup.html) ────────────────
_BG         = "#0f1419"
_PANEL      = "#1a2128"
_PANEL2     = "#232c35"
_BORDER     = "#2d3a45"
_BORDER_HI  = "#3d4d5c"
_TEXT       = "#e6edf3"
_DIM        = "#8b98a5"
_FAINT      = "#5d6b78"
_ACCENT     = "#ff8c42"
_ACCENT_DIM = "#b85d1f"
_GREEN      = "#3fb950"
_RED        = "#f85149"
_AMBER      = "#d29922"
_PURPLE     = "#bc8cff"
_TEAL       = "#39d0d8"


def _dialog_qss() -> str:
    return f"""
    QDialog, QDialog > QWidget {{
        background: {_BG};
        color: {_TEXT};
        font-family: "Segoe UI", system-ui, sans-serif;
        font-size: 12px;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: {_BG}; border: none;
    }}
    QLabel {{ color: {_TEXT}; background: transparent; }}
    QLabel[role="dim"]   {{ color: {_DIM}; }}
    QLabel[role="faint"] {{ color: {_FAINT}; }}
    QLabel[role="help"]  {{ color: {_FAINT}; font-size: 10px; }}
    QLabel[role="fieldlabel"] {{
        color: {_DIM}; font-size: 10px; font-weight: 700;
        letter-spacing: 0.4px;
    }}
    QLineEdit, QComboBox, QTextEdit {{
        background: {_BG}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 5px;
        padding: 6px 10px;
        selection-background-color: {_ACCENT};
        selection-color: white;
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
        border-color: {_ACCENT};
        background: #11171d;
        color: #ffffff;
    }}
    QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {{
        color: {_DIM};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {_PANEL2}; color: {_TEXT};
        border: 1px solid {_BORDER_HI};
        selection-background-color: {_ACCENT};
    }}
    QCheckBox {{ color: {_TEXT}; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {_BORDER_HI}; border-radius: 3px;
        background: {_BG};
    }}
    QCheckBox::indicator:checked {{
        background: {_ACCENT}; border-color: {_ACCENT};
    }}
    QPushButton {{
        background: {_PANEL2}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 5px;
        padding: 7px 12px; font-weight: 600;
    }}
    QPushButton:hover {{ border-color: {_BORDER_HI}; }}
    QPushButton[cta="primary"]  {{ background: {_ACCENT}; color: white; border: none; }}
    QPushButton[cta="primary"]:hover {{ background: {_ACCENT_DIM}; }}
    QPushButton[cta="green"]    {{ background: {_GREEN}; color: white; border: none; }}
    QPushButton[cta="green"]:hover {{ background: #2ea043; }}
    QPushButton[cta="red"]      {{ background: transparent; color: {_RED}; border: 1px solid {_BORDER}; }}
    QPushButton[cta="ai"] {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #6e3bff, stop:0.6 #bc8cff, stop:1 #39d0d8);
        color: white; border: none; font-weight: 700;
    }}
    QPushButton[cta="ai-sm"] {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #6e3bff, stop:1 #bc8cff);
        color: white; border: none; font-weight: 700;
        font-size: 10px; padding: 4px 9px; border-radius: 4px;
    }}
    QPushButton[cta="ai-ghost"] {{
        background: rgba(110,59,255,0.10);
        color: {_PURPLE};
        border: 1px solid rgba(188,140,255,0.45);
    }}
    QTableWidget {{
        background: {_BG}; color: {_TEXT};
        gridline-color: {_BORDER};
        border: 1px solid {_BORDER}; border-radius: 6px;
    }}
    QHeaderView::section {{
        background: {_PANEL2}; color: {_DIM};
        border: none; border-bottom: 1px solid {_BORDER};
        padding: 6px 8px; font-size: 9px; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.5px;
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: {_BG}; border: none; width: 12px; height: 12px;
    }}
    QScrollBar::handle {{
        background: {_BORDER_HI}; border-radius: 6px; min-height: 30px;
    }}
    QScrollBar::handle:hover {{ background: {_DIM}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ background: none; height: 0; width: 0; }}
    """


def _ai_btn(text: str, on_click) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("cta", "ai-sm")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(on_click)
    return b


def _ghost_ai_btn(text: str, on_click) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("cta", "ai-ghost")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(on_click)
    return b


class _Section(QFrame):
    """Numbered section card with header (number badge + title + AI slot)."""

    def __init__(self, num: int, title: str, ai_buttons: list[QPushButton] | None = None):
        super().__init__()
        self.setStyleSheet(
            f"_Section{{background:#161c22;border:1px solid {_BORDER};"
            f"border-radius:8px;}}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)

        badge = QLabel(str(num))
        badge.setFixedSize(20, 20)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background:{_ACCENT};color:white;border-radius:10px;"
            f"font-size:10px;font-weight:700;")
        head.addWidget(badge)

        ttl = QLabel(title.upper())
        ttl.setStyleSheet(
            f"color:{_ACCENT};font-size:11px;font-weight:700;"
            f"letter-spacing:0.8px;background:transparent;")
        head.addWidget(ttl)
        head.addStretch()

        if ai_buttons:
            for b in ai_buttons:
                head.addWidget(b)

        outer.addLayout(head)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self.body_layout = QGridLayout(body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setHorizontalSpacing(14)
        self.body_layout.setVerticalSpacing(12)
        outer.addWidget(body)


def _field(label: str, widget: QWidget, *, required: bool = False,
           help_text: str | None = None) -> QWidget:
    """Wraps a widget with a small uppercase label above and optional help below."""
    wrap = QWidget()
    wrap.setStyleSheet("background:transparent;")
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    lbl_text = label
    if required:
        lbl_text = f"{label}  <span style='color:{_ACCENT}'>*</span>"
    lbl = QLabel(lbl_text)
    lbl.setProperty("role", "fieldlabel")
    lbl.setStyleSheet(
        f"color:{_DIM};font-size:10px;font-weight:700;"
        f"letter-spacing:0.4px;background:transparent;")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    v.addWidget(lbl)
    v.addWidget(widget)
    if help_text:
        h = QLabel(help_text)
        h.setStyleSheet(f"color:{_FAINT};font-size:10px;background:transparent;")
        v.addWidget(h)
    return wrap


class AddProductDialog(QDialog):
    """Detailed Add New Product dialog matching the inventory mockup."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add New Product")
        self.resize(1180, 880)
        self.setStyleSheet(_dialog_qss())

        # Track if save succeeded for the products screen to refresh
        self.created_id: int | None = None
        self.should_add_another: bool = False

        # ── Fields we collect ──
        self.f: dict[str, QWidget] = {}

        # Build layout
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_ai_banner())

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(18, 4, 18, 18)
        body_layout.setSpacing(12)

        body_layout.addWidget(self._build_section_1_identity())
        body_layout.addWidget(self._build_section_2_category())
        body_layout.addWidget(self._build_section_3_pricing())
        body_layout.addWidget(self._build_section_4_vendors())
        body_layout.addWidget(self._build_section_5_inventory())
        body_layout.addWidget(self._build_section_6_core_warranty())
        body_layout.addWidget(self._build_section_7_shipping())
        body_layout.addWidget(self._build_section_8_accounting())
        body_layout.addWidget(self._build_section_9_images())
        body_layout.addWidget(self._build_section_10_notes())
        body_layout.addStretch()

        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        root.addWidget(self._build_footer())

        # Re-apply the dialog stylesheet AFTER all children exist so it wins
        # against any cascaded global theme rules from the parent QMainWindow.
        self.setStyleSheet(_dialog_qss())

        # Land focus on the first real field so typing works immediately.
        first = self.f.get("oem_part_number") or self.f.get("sku")
        if first is not None:
            first.setFocus()

    # ─── Header ────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame{{background:{_PANEL};border-bottom:1px solid {_BORDER};}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 12, 18, 12)
        title = QLabel("Add New Product  "
                       f"<span style='color:{_DIM};font-size:11px;"
                       "font-weight:400;'>· auto SKU will be assigned on save</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet(f"color:{_TEXT};font-size:15px;font-weight:700;")
        h.addWidget(title)
        h.addStretch()

        shortcut = QPushButton("⌨ Keyboard shortcuts")
        shortcut.clicked.connect(lambda: QMessageBox.information(
            self, "Shortcuts",
            "<b>Ctrl+S</b> — Save &amp; Close<br>"
            "<b>Ctrl+Enter</b> — Save Draft<br>"
            "<b>Esc</b> — Cancel<br>"
            "<b>Tab</b> — Next field<br>"))
        h.addWidget(shortcut)

        cancel = QPushButton("✕ Cancel")
        cancel.setProperty("cta", "red")
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        return bar

    # ─── AI banner ─────────────────────────────────────────
    def _build_ai_banner(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 rgba(110,59,255,0.18), stop:1 rgba(57,208,216,0.10));"
            f"border-bottom:1px solid rgba(188,140,255,0.35);}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 10, 18, 10)
        h.setSpacing(10)

        title = QLabel("✨ AI ASSISTANT")
        title.setStyleSheet(
            f"color:{_PURPLE};font-size:12px;font-weight:700;"
            f"letter-spacing:0.4px;background:transparent;")
        h.addWidget(title)

        self._ai_input = QLineEdit()
        self._ai_input.setPlaceholderText(
            "Paste a PAI / Cummins / Holset URL, OEM #, or vendor invoice PDF — "
            "I'll auto-fill everything…")
        h.addWidget(self._ai_input, 1)

        scrape = QPushButton("🌐 Scrape & Fill")
        scrape.setProperty("cta", "ai")
        scrape.clicked.connect(lambda: self._ai_stub("Scrape & Fill"))
        h.addWidget(scrape)

        ocr = _ghost_ai_btn("📷 OCR Invoice", lambda: self._ai_stub("OCR Invoice"))
        xref = _ghost_ai_btn("🔍 Cross-ref Lookup", lambda: self._ai_stub("Cross-ref Lookup"))
        h.addWidget(ocr); h.addWidget(xref)
        return bar

    # ─── Section ① Identity ────────────────────────────────
    def _build_section_1_identity(self) -> QWidget:
        sec = _Section(1, "Identity", [
            _ai_btn("✨ Auto-fill from OEM #",
                    lambda: self._ai_stub("Auto-fill from OEM #")),
            _ai_btn("✨ Find Cross-refs",
                    lambda: self._ai_stub("Find Cross-refs")),
        ])
        g = sec.body_layout

        self.f["sku"]                  = QLineEdit()
        self.f["oem_part_number"]      = QLineEdit()
        self.f["manufacturer"]         = QComboBox(); self.f["manufacturer"].setEditable(True)
        self.f["manufacturer"].addItems(
            ["PAI Industries", "Cummins", "Holset", "Fleetguard",
             "Victor Reinz", "JAKS (in-house)"])
        self.f["item_type"]            = QComboBox()
        self.f["item_type"].addItems(
            ["Stock part", "Serialized unit (reman engine, head, injector)",
             "Kit / assembly", "Service / labor", "Freight charge"])

        self.f["title"]                = QLineEdit()
        self.f["title"].setPlaceholderText("appears on quotes / invoices / labels")
        self.f["inventory_status"]     = QComboBox()
        self.f["inventory_status"].addItems(
            ["Active", "New (hidden)", "Discontinued", "Superseded"])
        self.f["replaced_by"]          = QLineEdit()
        self.f["replaced_by"].setPlaceholderText("optional SKU lookup…")

        self.f["description_html"]     = QTextEdit()
        self.f["description_html"].setMinimumHeight(70)
        self.f["description_html"].setPlaceholderText(
            "Long description — appears on web and detailed quotes")

        self.f["upc"]                  = QLineEdit()
        self.f["upc"].setPlaceholderText("optional")
        self.f["xref_part_number"]     = QLineEdit()
        self.f["xref_part_number"].setPlaceholderText("comma separated cross-refs")
        self.f["country_of_origin"]    = QComboBox()
        self.f["country_of_origin"].addItems(["USA", "Mexico", "India", "China", "Other"])
        self.f["hs_code"]              = QLineEdit()

        g.addWidget(_field("SKU", self.f["sku"], required=True,
                           help_text="auto-generated from brand + OEM, editable"), 0, 0)
        g.addWidget(_field("OEM Part #", self.f["oem_part_number"], required=True), 0, 1)
        g.addWidget(_field("Brand / Mfr", self.f["manufacturer"], required=True), 0, 2)
        g.addWidget(_field("Item Type", self.f["item_type"]), 0, 3)

        g.addWidget(_field("Short Description", self.f["title"], required=True),
                    1, 0, 1, 2)
        g.addWidget(_field("Catalog Status", self.f["inventory_status"]), 1, 2)
        g.addWidget(_field("Replaced By", self.f["replaced_by"]), 1, 3)

        # Long description with AI buttons row above the textarea
        long_wrap = QWidget()
        long_wrap.setStyleSheet("background:transparent;")
        lv = QVBoxLayout(long_wrap); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(3)
        ldr = QHBoxLayout(); ldr.setContentsMargins(0, 0, 0, 0); ldr.setSpacing(6)
        ldr_label = QLabel(f"Long Description "
            f"<span style='color:{_PURPLE};font-size:9px;background:rgba(110,59,255,0.15);"
            f"border:1px solid rgba(188,140,255,0.35);border-radius:3px;padding:1px 5px;'>"
            f"✨ AI GENERATED</span>")
        ldr_label.setTextFormat(Qt.TextFormat.RichText)
        ldr_label.setStyleSheet(
            f"color:{_DIM};font-size:10px;font-weight:700;letter-spacing:0.4px;")
        ldr.addWidget(ldr_label); ldr.addStretch()
        for txt, lbl in [("✨ Regenerate", "Regenerate"),
                         ("✨ Shorter", "Make shorter"),
                         ("✨ Bullets", "Add bullets"),
                         ("✨ Translate ES", "Translate ES")]:
            ldr.addWidget(_ai_btn(txt, lambda _=False, n=lbl: self._ai_stub(n)))
        lv.addLayout(ldr)
        lv.addWidget(self.f["description_html"])
        g.addWidget(long_wrap, 2, 0, 1, 4)

        g.addWidget(_field("UPC / EAN", self.f["upc"]), 3, 0)
        g.addWidget(_field("Cross-ref / Alt #s", self.f["xref_part_number"],
                           help_text="comma separated — used in part search"), 3, 1)
        g.addWidget(_field("Country of Origin", self.f["country_of_origin"]), 3, 2)
        g.addWidget(_field("HS / Tariff Code", self.f["hs_code"]), 3, 3)

        return sec

    # ─── Section ② Categorization & Fitment ────────────────
    def _build_section_2_category(self) -> QWidget:
        sec = _Section(2, "Categorization & Fitment", [
            _ai_btn("✨ Suggest Category",
                    lambda: self._ai_stub("Suggest Category")),
            _ai_btn("✨ Suggest Fitment",
                    lambda: self._ai_stub("Suggest Fitment")),
            _ai_btn("✨ Suggest Tags",
                    lambda: self._ai_stub("Suggest Tags")),
        ])
        g = sec.body_layout

        self.f["category"]    = QComboBox(); self.f["category"].setEditable(True)
        self.f["category"].addItems(
            ["Inframe Kits", "Injectors", "Turbos", "EGR Coolers",
             "Gasket Sets", "Cylinder Heads", "Water Pumps", "Filters"])
        self.f["subcategory"] = QLineEdit()
        self.f["engine"]      = QComboBox(); self.f["engine"].setEditable(True)
        self.f["engine"].addItems(
            ["CAT C15", "Cummins ISX15", "Detroit DD15", "PACCAR MX-13",
             "Cummins X15", "Detroit DD13"])
        self.f["year_range"]  = QLineEdit()
        self.f["year_range"].setPlaceholderText("e.g. 1999 – 2006")

        self.f["truck_model"]    = QLineEdit()
        self.f["truck_model"].setPlaceholderText("Class-8 OTR · marine · industrial")
        self.f["position"]       = QComboBox()
        self.f["position"].addItems(
            ["—", "Left", "Right", "Front", "Rear", "Inboard", "Outboard"])
        self.f["fitment_notes"]  = QLineEdit()
        self.f["fitment_notes"].setPlaceholderText("e.g. pre-EGR only")

        g.addWidget(_field("Category", self.f["category"], required=True), 0, 0)
        g.addWidget(_field("Sub-Category", self.f["subcategory"]), 0, 1)
        g.addWidget(_field("Engine Family", self.f["engine"]), 0, 2)
        g.addWidget(_field("Year Range", self.f["year_range"]), 0, 3)

        g.addWidget(_field("Vehicle / Equipment Fitment", self.f["truck_model"]),
                    1, 0, 1, 2)
        g.addWidget(_field("Position / Side", self.f["position"]), 1, 2)
        g.addWidget(_field("Notes / Restrictions", self.f["fitment_notes"]), 1, 3)

        return sec

    # ─── Section ③ Costing & Pricing ───────────────────────
    def _build_section_3_pricing(self) -> QWidget:
        sec = _Section(3, "Costing & Pricing", [
            _ai_btn("✨ Suggest Tier Pricing",
                    lambda: self._ai_stub("Suggest Tier Pricing")),
            _ai_btn("✨ Competitor Scan",
                    lambda: self._ai_stub("Competitor Scan")),
            _ai_btn("✨ MAP Check",
                    lambda: self._ai_stub("MAP Check")),
        ])
        g = sec.body_layout

        self.f["costing_method"] = QComboBox()
        self.f["costing_method"].addItems(
            ["Average (default)", "FIFO", "Last Cost", "Standard"])
        self.f["cost"]           = QLineEdit(); self.f["cost"].setPlaceholderText("$0.00")
        self.f["pai_cost"]       = QLineEdit(); self.f["pai_cost"].setPlaceholderText("$0.00")
        self.f["avg_cost"]       = QLineEdit(); self.f["avg_cost"].setPlaceholderText("$0.00")

        self.f["selling_price"]   = QLineEdit(); self.f["selling_price"].setPlaceholderText("$0.00")
        self.f["compare_at_price"] = QLineEdit()
        self.f["compare_at_price"].setPlaceholderText("$0.00")
        self.f["floor_price"]     = QLineEdit(); self.f["floor_price"].setPlaceholderText("$0.00")
        self.f["target_margin"]   = QLineEdit(); self.f["target_margin"].setPlaceholderText("30.0%")

        self.f["tier1_price"] = QLineEdit(); self.f["tier1_price"].setPlaceholderText("Fleet $")
        self.f["tier2_price"] = QLineEdit(); self.f["tier2_price"].setPlaceholderText("Dealer $")
        self.f["tier3_price"] = QLineEdit(); self.f["tier3_price"].setPlaceholderText("Wholesale $")
        self.f["promo_price"] = QLineEdit(); self.f["promo_price"].setPlaceholderText("Promo $")

        g.addWidget(_field("Costing Method", self.f["costing_method"]), 0, 0)
        g.addWidget(_field("Standard Cost", self.f["cost"]), 0, 1)
        g.addWidget(_field("Last Cost", self.f["pai_cost"]), 0, 2)
        g.addWidget(_field("Average Cost", self.f["avg_cost"]), 0, 3)

        g.addWidget(_field("List Price", self.f["selling_price"], required=True), 1, 0)
        g.addWidget(_field("MAP Price", self.f["compare_at_price"],
                           help_text="minimum advertised"), 1, 1)
        g.addWidget(_field("Floor Price", self.f["floor_price"],
                           help_text="never sell below"), 1, 2)
        g.addWidget(_field("Target Margin %", self.f["target_margin"]), 1, 3)

        g.addWidget(_field("Tier 1 — Fleet",     self.f["tier1_price"]), 2, 0)
        g.addWidget(_field("Tier 2 — Dealer",    self.f["tier2_price"]), 2, 1)
        g.addWidget(_field("Tier 3 — Wholesale", self.f["tier3_price"]), 2, 2)
        g.addWidget(_field("Special / Promo",    self.f["promo_price"]), 2, 3)

        # Live margin indicator
        self._margin_box = QFrame()
        self._margin_box.setStyleSheet(
            f"QFrame{{background:{_BG};border:1px solid {_BORDER};border-radius:6px;}}")
        mb = QHBoxLayout(self._margin_box)
        mb.setContentsMargins(12, 10, 12, 10); mb.setSpacing(20)
        self._margin_widgets: dict[str, QLabel] = {}
        for key, label, color in [("margin_pct", "Margin @ List", _GREEN),
                                  ("markup",      "Markup",        _TEXT),
                                  ("profit",      "$ Profit",      _GREEN),
                                  ("floor_marg",  "Floor Margin",  _AMBER)]:
            col = QVBoxLayout(); col.setSpacing(2)
            k = QLabel(label.upper())
            k.setStyleSheet(f"color:{_DIM};font-size:9px;letter-spacing:0.5px;")
            v = QLabel("—")
            v.setStyleSheet(f"color:{color};font-size:14px;font-weight:700;")
            col.addWidget(k); col.addWidget(v)
            self._margin_widgets[key] = v
            mb.addLayout(col)
        mb.addStretch()
        g.addWidget(self._margin_box, 3, 0, 1, 4)

        # Recompute on edits
        for k in ("cost", "selling_price", "floor_price"):
            self.f[k].textChanged.connect(self._recompute_margin)

        return sec

    def _recompute_margin(self) -> None:
        def _num(s: str) -> float:
            s = (s or "").replace("$", "").replace(",", "").replace("%", "").strip()
            try: return float(s)
            except ValueError: return 0.0
        cost = _num(self.f["cost"].text())
        price = _num(self.f["selling_price"].text())
        floor = _num(self.f["floor_price"].text())
        if price > 0 and cost > 0:
            m = (price - cost) / price * 100
            mk = (price - cost) / cost * 100
            prof = price - cost
            self._margin_widgets["margin_pct"].setText(f"{m:.1f}%")
            self._margin_widgets["markup"].setText(f"{mk:.1f}%")
            self._margin_widgets["profit"].setText(f"${prof:,.2f}")
            color = _GREEN if m >= 25 else (_AMBER if m >= 10 else _RED)
            self._margin_widgets["margin_pct"].setStyleSheet(
                f"color:{color};font-size:14px;font-weight:700;")
        else:
            for w in self._margin_widgets.values():
                w.setText("—")
        if floor > 0 and cost > 0:
            fm = (floor - cost) / floor * 100
            self._margin_widgets["floor_marg"].setText(f"{fm:.1f}%")

    # ─── Section ④ Vendors ─────────────────────────────────
    def _build_section_4_vendors(self) -> QWidget:
        self._pai_scrape_btn = _ai_btn(
            "🌐 PAI Scrape", lambda: self._on_pai_scrape())
        sec = _Section(4, "Vendors & Sourcing", [
            self._pai_scrape_btn,
            _ai_btn("🌐 Cummins Scrape",
                    lambda: self._ai_stub("Cummins Scrape")),
            _ai_btn("🌐 Holset Scrape",
                    lambda: self._ai_stub("Holset Scrape")),
            _ai_btn("✨ Find Alt Vendors",
                    lambda: self._ai_stub("Find Alt Vendors")),
        ])

        self._vendor_table = QTableWidget(2, 7)
        self._vendor_table.setHorizontalHeaderLabels(
            ["Pref", "Vendor", "Vendor SKU", "Cost",
             "Min Qty", "Lead Time", "Notes"])
        self._vendor_table.verticalHeader().setVisible(False)
        self._vendor_table.setMinimumHeight(120)
        self._vendor_table.horizontalHeader().setStretchLastSection(True)
        self._vendor_table.setColumnWidth(0, 80)
        self._vendor_table.setColumnWidth(1, 180)
        self._vendor_table.setColumnWidth(2, 150)
        self._vendor_table.setColumnWidth(3, 90)
        self._vendor_table.setColumnWidth(4, 70)
        self._vendor_table.setColumnWidth(5, 90)

        # Primary row
        prim = QTableWidgetItem("PRIMARY")
        prim.setForeground(Qt.GlobalColor.white)
        prim.setBackground(Qt.GlobalColor.darkMagenta)
        prim.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vendor_table.setItem(0, 0, prim)

        add_btn = QPushButton("＋ Add vendor source")
        add_btn.setStyleSheet(
            f"QPushButton{{color:{_ACCENT};background:transparent;"
            f"border:1px dashed {_BORDER};padding:6px;}}")
        add_btn.clicked.connect(self._add_vendor_row)

        sec.body_layout.addWidget(self._vendor_table, 0, 0, 1, 4)
        sec.body_layout.addWidget(add_btn, 1, 0, 1, 4)
        return sec

    def _add_vendor_row(self) -> None:
        r = self._vendor_table.rowCount()
        self._vendor_table.insertRow(r)

    # ─── Section ⑤ Inventory & Reorder ─────────────────────
    def _build_section_5_inventory(self) -> QWidget:
        sec = _Section(5, "Inventory & Reorder", [
            _ai_btn("✨ Suggest Reorder Pt",
                    lambda: self._ai_stub("Suggest Reorder Pt")),
            _ai_btn("✨ Suggest Bin",
                    lambda: self._ai_stub("Suggest Bin")),
        ])
        g = sec.body_layout

        self.f["track_inventory"] = QComboBox()
        self.f["track_inventory"].addItems(
            ["Yes — track qty", "No — service/labor",
             "Drop-ship only (no on-hand)"])
        self.f["uom"]            = QComboBox()
        self.f["uom"].addItems(["each", "set", "kit", "pair", "case", "hr"])
        self.f["warehouse"]      = QComboBox()
        self.f["warehouse"].addItems(["Main · Milwaukee", "West · Phoenix"])
        self.f["bin"]            = QLineEdit()
        self.f["bin"].setPlaceholderText("A-12")

        self.f["opening_qty"]    = QLineEdit(); self.f["opening_qty"].setPlaceholderText("0")
        self.f["min_qty"]        = QLineEdit(); self.f["min_qty"].setPlaceholderText("4")
        self.f["reorder_qty"]    = QLineEdit(); self.f["reorder_qty"].setPlaceholderText("6")
        self.f["max_qty"]        = QLineEdit(); self.f["max_qty"].setPlaceholderText("12")

        g.addWidget(_field("Track inventory?", self.f["track_inventory"]), 0, 0)
        g.addWidget(_field("Unit of Measure", self.f["uom"]), 0, 1)
        g.addWidget(_field("Default Warehouse", self.f["warehouse"]), 0, 2)
        g.addWidget(_field("Default Bin / Location", self.f["bin"]), 0, 3)

        g.addWidget(_field("Opening Qty", self.f["opening_qty"]), 1, 0)
        g.addWidget(_field("Reorder Point", self.f["min_qty"]), 1, 1)
        g.addWidget(_field("Reorder Qty", self.f["reorder_qty"]), 1, 2)
        g.addWidget(_field("Max Stock", self.f["max_qty"]), 1, 3)

        # Flags row
        flags = QWidget(); flags.setStyleSheet("background:transparent;")
        fl = QHBoxLayout(flags); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(18)
        for label, key in [
            ("Lot tracked", "lot_tracked"),
            ("Serial tracked", "requires_serial"),
            ("Cycle-count quarterly", "cycle_count_quarterly"),
            ("Reorder via auto-PO", "auto_po"),
            ("Hazmat", "hazmat"),
            ("Allow backorder", "allow_backorder"),
            ("Allow negative on-hand", "allow_negative"),
        ]:
            cb = QCheckBox(label)
            self.f[key] = cb
            fl.addWidget(cb)
        fl.addStretch()

        flags_wrap = _field("Tracking flags", flags)
        g.addWidget(flags_wrap, 2, 0, 1, 4)
        return sec

    # ─── Section ⑥ Core & Warranty ─────────────────────────
    def _build_section_6_core_warranty(self) -> QWidget:
        sec = _Section(6, "Core & Warranty", [
            _ai_btn("✨ Suggest Core Deposit",
                    lambda: self._ai_stub("Suggest Core Deposit")),
        ])
        g = sec.body_layout

        self.f["core_required"]   = QComboBox()
        self.f["core_required"].addItems(
            ["Yes — require core", "No core",
             "Optional (deposit waived if returned)"])
        self.f["core_charge"]     = QLineEdit(); self.f["core_charge"].setPlaceholderText("$0.00")
        self.f["core_window"]     = QLineEdit(); self.f["core_window"].setText("60 days")
        self.f["core_disposition"] = QComboBox()
        self.f["core_disposition"].addItems(
            ["Reman in-house", "Send to upstream vendor", "Scrap"])

        self.f["warranty_term"]   = QComboBox()
        self.f["warranty_term"].addItems(
            ["1 year / 100k mi (std)", "2 year / 200k mi",
             "3 year / 300k mi", "No warranty"])
        self.f["supplier_warranty_value"]  = QLineEdit()
        self.f["supplier_warranty_value"].setPlaceholderText("$0.00")
        self.f["jaks_warranty_charge"]     = QLineEdit()
        self.f["jaks_warranty_charge"].setPlaceholderText("$0.00")
        self.f["returnable"]      = QComboBox()
        self.f["returnable"].addItems(
            ["Yes — 30 days, restock 15%", "No returns"])

        g.addWidget(_field("Core deposit?", self.f["core_required"]), 0, 0)
        g.addWidget(_field("Core Deposit Amount", self.f["core_charge"]), 0, 1)
        g.addWidget(_field("Core Return Window", self.f["core_window"]), 0, 2)
        g.addWidget(_field("Core Disposition", self.f["core_disposition"]), 0, 3)

        g.addWidget(_field("Warranty", self.f["warranty_term"]), 1, 0)
        g.addWidget(_field("Std Warranty Price", self.f["supplier_warranty_value"]), 1, 1)
        g.addWidget(_field("Extended Warranty Price", self.f["jaks_warranty_charge"]), 1, 2)
        g.addWidget(_field("Returnable?", self.f["returnable"]), 1, 3)
        return sec

    # ─── Section ⑦ Shipping ────────────────────────────────
    def _build_section_7_shipping(self) -> QWidget:
        sec = _Section(7, "Shipping & Packaging", [
            _ai_btn("✨ Lookup Freight Class",
                    lambda: self._ai_stub("Lookup Freight Class")),
            _ai_btn("✨ NMFC Lookup",
                    lambda: self._ai_stub("NMFC Lookup")),
            _ai_btn("✨ Estimate Weight/Dims",
                    lambda: self._ai_stub("Estimate Weight/Dims")),
        ])
        g = sec.body_layout

        self.f["weight"]  = QLineEdit(); self.f["weight"].setPlaceholderText("lbs")
        self.f["length"]  = QLineEdit(); self.f["length"].setPlaceholderText("in")
        self.f["width"]   = QLineEdit(); self.f["width"].setPlaceholderText("in")
        self.f["height"]  = QLineEdit(); self.f["height"].setPlaceholderText("in")

        self.f["freight_class"] = QComboBox()
        self.f["freight_class"].addItems(["85", "92.5", "100", "125", "150", "175"])
        self.f["nmfc"]          = QLineEdit()
        self.f["ship_method"]   = QComboBox()
        self.f["ship_method"].addItems(
            ["LTL Freight", "FedEx Ground", "UPS Ground", "Customer pickup"])
        self.f["carrier"]       = QComboBox()
        self.f["carrier"].addItems(["SAIA", "R+L Carriers", "FedEx Freight", "XPO"])

        self.f["handling_notes"] = QLineEdit()
        self.f["pkg_type"]       = QComboBox()
        self.f["pkg_type"].addItems(["Pallet", "Crate", "Box"])
        self.f["units_per_pkg"]  = QLineEdit(); self.f["units_per_pkg"].setText("1")

        g.addWidget(_field("Weight (lbs)", self.f["weight"]), 0, 0)
        g.addWidget(_field("Length (in)", self.f["length"]), 0, 1)
        g.addWidget(_field("Width (in)",  self.f["width"]),  0, 2)
        g.addWidget(_field("Height (in)", self.f["height"]), 0, 3)

        g.addWidget(_field("Freight Class", self.f["freight_class"]), 1, 0)
        g.addWidget(_field("NMFC #", self.f["nmfc"]), 1, 1)
        g.addWidget(_field("Default Ship Method", self.f["ship_method"]), 1, 2)
        g.addWidget(_field("Default Carrier", self.f["carrier"]), 1, 3)

        g.addWidget(_field("Handling Notes", self.f["handling_notes"]), 2, 0, 1, 2)
        g.addWidget(_field("Pkg Type", self.f["pkg_type"]), 2, 2)
        g.addWidget(_field("Units / Pkg", self.f["units_per_pkg"]), 2, 3)
        return sec

    # ─── Section ⑧ Accounting ──────────────────────────────
    def _build_section_8_accounting(self) -> QWidget:
        sec = _Section(8, "Accounting & QuickBooks", [
            _ai_btn("✨ Suggest GL Accounts",
                    lambda: self._ai_stub("Suggest GL Accounts")),
            _ai_btn("✨ HS / Tax Lookup",
                    lambda: self._ai_stub("HS / Tax Lookup")),
        ])
        g = sec.body_layout

        self.f["income_account"] = QComboBox()
        self.f["income_account"].addItems(
            ["4000 · Parts Sales", "4010 · Reman Sales", "4100 · Core Charges"])
        self.f["cogs_account"]   = QComboBox()
        self.f["cogs_account"].addItems(
            ["5000 · Cost of Parts Sold", "5010 · Reman COGS"])
        self.f["asset_account"]  = QComboBox()
        self.f["asset_account"].addItems(["1300 · Inventory Asset"])
        self.f["tax_code"]       = QComboBox()
        self.f["tax_code"].addItems(["Taxable (Auto)", "Non-taxable", "Resale Cert"])

        self.f["qbo_item_id"]    = QLineEdit()
        self.f["qbo_item_id"].setPlaceholderText("will sync on save")
        self.f["qbo_item_id"].setReadOnly(True)
        self.f["qbo_sync"]       = QComboBox()
        self.f["qbo_sync"].addItems(
            ["Sync on save (default)", "Do not sync", "Sync when active"])
        self.f["shopify_sku"]    = QLineEdit()
        self.f["web_visibility"] = QComboBox()
        self.f["web_visibility"].addItems(["Public", "Logged-in only", "Hidden"])

        g.addWidget(_field("Income Account", self.f["income_account"]), 0, 0)
        g.addWidget(_field("COGS Account",   self.f["cogs_account"]),   0, 1)
        g.addWidget(_field("Asset Account",  self.f["asset_account"]),  0, 2)
        g.addWidget(_field("Tax Code",       self.f["tax_code"]),       0, 3)

        g.addWidget(_field("QBO Item ID",  self.f["qbo_item_id"]),    1, 0)
        g.addWidget(_field("QBO Sync",     self.f["qbo_sync"]),       1, 1)
        g.addWidget(_field("Shopify SKU",  self.f["shopify_sku"]),    1, 2)
        g.addWidget(_field("Web visibility", self.f["web_visibility"]), 1, 3)
        return sec

    # ─── Section ⑨ Images & Docs ───────────────────────────
    def _build_section_9_images(self) -> QWidget:
        sec = _Section(9, "Images & Documents", [
            _ai_btn("🌐 Fetch from Vendor Site",
                    lambda: self._ai_stub("Fetch from Vendor Site")),
            _ai_btn("✨ Google Image Search",
                    lambda: self._ai_stub("Google Image Search")),
            _ai_btn("✨ AI Background Remove",
                    lambda: self._ai_stub("AI Background Remove")),
            _ai_btn("✨ Find Spec Sheet PDF",
                    lambda: self._ai_stub("Find Spec Sheet PDF")),
        ])

        # Two drop zones
        zones = QWidget(); zones.setStyleSheet("background:transparent;")
        zh = QHBoxLayout(zones); zh.setContentsMargins(0, 0, 0, 0); zh.setSpacing(12)
        for icon, title, sub in [
            ("📁", "Drop product photos here or click to upload",
             "JPG / PNG · up to 10 MB each · first image is the hero"),
            ("📄", "Drop spec sheets, install guides, MSDS, COA…",
             "PDF preferred · attached to invoice when sold"),
        ]:
            z = QLabel(
                f"<div style='font-size:14px;color:{_TEXT};'>{icon} {title}</div>"
                f"<div style='color:{_FAINT};font-size:10px;margin-top:4px;'>{sub}</div>")
            z.setTextFormat(Qt.TextFormat.RichText)
            z.setAlignment(Qt.AlignmentFlag.AlignCenter)
            z.setMinimumHeight(70)
            z.setStyleSheet(
                f"QLabel{{border:2px dashed {_BORDER};border-radius:8px;"
                f"background:{_BG};padding:14px;}}"
                f"QLabel:hover{{border-color:{_ACCENT};}}")
            z.setCursor(Qt.CursorShape.PointingHandCursor)
            zh.addWidget(z, 1)
        sec.body_layout.addWidget(zones, 0, 0, 1, 4)
        return sec

    # ─── Section ⑩ Notes ───────────────────────────────────
    def _build_section_10_notes(self) -> QWidget:
        sec = _Section(10, "Internal Notes & Substitutions", [
            _ai_btn("✨ Find Substitutes",
                    lambda: self._ai_stub("Find Substitutes")),
            _ai_btn("✨ Common Mistakes",
                    lambda: self._ai_stub("Common Mistakes")),
        ])
        g = sec.body_layout

        self.f["notes_internal"] = QTextEdit()
        self.f["notes_internal"].setMinimumHeight(60)
        self.f["notes_internal"].setPlaceholderText(
            "purchasing notes, vendor quirks, common mistakes…")
        self.f["notes_public"]   = QTextEdit()
        self.f["notes_public"].setMinimumHeight(60)
        self.f["notes_public"].setPlaceholderText(
            "appears on quote / invoice line if filled")
        self.f["substitutes"]    = QLineEdit()
        self.f["substitutes"].setPlaceholderText("comma-separated alternative SKUs")

        g.addWidget(_field("Internal Notes (not customer-facing)",
                           self.f["notes_internal"]), 0, 0, 1, 2)
        g.addWidget(_field("Customer-facing Notes",
                           self.f["notes_public"]), 0, 2, 1, 2)
        g.addWidget(_field("Substitutes / Alternatives",
                           self.f["substitutes"]), 1, 0, 1, 4)
        return sec

    # ─── Footer ────────────────────────────────────────────
    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame{{background:#161c22;border-top:1px solid {_BORDER};}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 10, 18, 10); h.setSpacing(8)

        self._status_label = QLabel(
            f"<span style='color:{_GREEN}'>●</span> Ready to save")
        self._status_label.setTextFormat(Qt.TextFormat.RichText)
        self._status_label.setStyleSheet(f"color:{_DIM};font-size:11px;")
        h.addWidget(self._status_label)
        h.addStretch()

        draft = QPushButton("Save Draft")
        draft.clicked.connect(lambda: self._save(close_after=False, draft=True))
        another = QPushButton("Save & Add Another")
        another.clicked.connect(lambda: self._save(close_after=True, add_another=True))
        dup = QPushButton("Save & Duplicate")
        dup.clicked.connect(lambda: self._save(close_after=True, add_another=True))
        close = QPushButton("✓ Save & Close  (Ctrl+S)")
        close.setProperty("cta", "green")
        close.clicked.connect(lambda: self._save(close_after=True))

        for b in (draft, another, dup, close):
            h.addWidget(b)
        return bar

    # AI dispatch (Grok)
    def _ai_dispatch_table(self) -> dict:
        return {
            "Scrape & Fill":            ai.auto_fill_from_oem,
            "OCR Invoice":              None,
            "Cross-ref Lookup":         ai.find_cross_refs,
            "Auto-fill from OEM #":     ai.auto_fill_from_oem,
            "Find Cross-refs":          ai.find_cross_refs,
            "Regenerate":               lambda c: ai.generate_long_description(c, "regenerate"),
            "Make shorter":             lambda c: ai.generate_long_description(c, "shorter"),
            "Add bullets":              lambda c: ai.generate_long_description(c, "bullets"),
            "Translate ES":             lambda c: ai.generate_long_description(c, "translate_es"),
            "Suggest Category":         ai.suggest_category,
            "Suggest Fitment":          ai.suggest_fitment,
            "Suggest Tags":             ai.suggest_tags,
            "Suggest Tier Pricing":     ai.suggest_tier_pricing,
            "Competitor Scan":          ai.competitor_scan,
            "MAP Check":                ai.map_check,
            "Cummins Scrape":           None,
            "Holset Scrape":            None,
            "Find Alt Vendors":         ai.find_cross_refs,
            "Suggest Reorder Pt":       ai.suggest_reorder_point,
            "Suggest Bin":              ai.suggest_bin,
            "Suggest Core Deposit":     ai.suggest_core_deposit,
            "Lookup Freight Class":     ai.lookup_freight_class,
            "NMFC Lookup":              ai.lookup_nmfc,
            "Estimate Weight/Dims":     ai.estimate_dimensions,
            "Suggest GL Accounts":      ai.suggest_gl_accounts,
            "HS / Tax Lookup":          ai.hs_tax_lookup,
            "Fetch from Vendor Site":   None,
            "Google Image Search":      None,
            "AI Background Remove":     None,
            "Find Spec Sheet PDF":      None,
            "Find Substitutes":         ai.find_substitutes,
            "Common Mistakes":          ai.common_mistakes,
        }

    def _ai_stub(self, feature: str) -> None:
        fn = self._ai_dispatch_table().get(feature)
        if fn is None:
            QMessageBox.information(
                self, "Vendor-side AI — Coming Soon",
                f"<b>{feature}</b><br><br>"
                "Needs a dedicated vendor scraper / image model.<br><br>"
                "Planned integrations:<br>"
                "&nbsp;• Playwright PAI / Cummins / Holset scrapers<br>"
                "&nbsp;• OCR via Tesseract or Grok-Vision<br>"
                "&nbsp;• Image fetch + bg-remove via replicate.com<br>"
                "&nbsp;• Spec-sheet PDF via Google Programmable Search")
            return
        if not ai.is_available():
            QMessageBox.warning(
                self, "AI Not Configured",
                "No XAI_API_KEY found in .env file.")
            return
        if getattr(self, "_ai_thread", None) and self._ai_thread.isRunning():
            return
        ctx = self._collect_ctx()
        self._status_label.setText(
            "<span style='color:#bc8cff'>AI</span> thinking - <i>" + feature + "</i>...")
        self._ai_thread = _AIWorker(fn, ctx, feature, parent=self)
        self._ai_thread.done.connect(self._on_ai_done)
        self._ai_thread.failed.connect(self._on_ai_failed)
        self._ai_thread.start()

    def _on_ai_done(self, result: dict, label: str) -> None:
        if "_error" in result:
            self._status_label.setText(
                "<span style='color:#f85149'>!</span> " + label + ": " + str(result["_error"]))
            QMessageBox.warning(self, label, str(result["_error"]))
            return
        applied = self._apply_ai_result(result)
        note = result.get("_notes", "")
        msg = "<span style='color:#3fb950'>OK</span> <b>" + label + "</b> - filled " + str(applied) + " field(s)"
        if note:
            msg += " - <i>" + str(note) + "</i>"
        self._status_label.setText(msg)

    def _on_ai_failed(self, err: str, label: str) -> None:
        self._status_label.setText(
            "<span style='color:#f85149'>!</span> " + label + " failed")
        QMessageBox.critical(self, label + " - failed",
                             "AI request failed:<br><br>" + str(err))

    def _collect_ctx(self) -> dict:
        out: dict = {}
        for key, w in self.f.items():
            v: Any = None
            if isinstance(w, QLineEdit):
                v = w.text().strip()
            elif isinstance(w, QTextEdit):
                v = w.toPlainText().strip()
            elif isinstance(w, QComboBox):
                v = w.currentText().strip()
            elif isinstance(w, QCheckBox):
                v = 1 if w.isChecked() else 0
            if v not in (None, "", 0):
                out[key] = v
        return out

    def _apply_ai_result(self, result: dict) -> int:
        applied = 0
        for key, val in result.items():
            if key.startswith("_"):
                continue
            w = self.f.get(key)
            if w is None or val in (None, ""):
                continue
            try:
                if isinstance(w, QLineEdit):
                    w.setText(str(val))
                elif isinstance(w, QTextEdit):
                    w.setPlainText(str(val))
                elif isinstance(w, QComboBox):
                    sv = str(val).strip()
                    idx = w.findText(sv, Qt.MatchFlag.MatchFixedString)
                    if idx >= 0:
                        w.setCurrentIndex(idx)
                    elif w.isEditable():
                        w.setEditText(sv)
                    else:
                        w.addItem(sv)
                        w.setCurrentIndex(w.count() - 1)
                elif isinstance(w, QCheckBox):
                    truthy = str(val).lower() not in ("0", "false", "no", "")
                    w.setChecked(bool(val) and truthy)
                applied += 1
            except Exception:
                logger.exception("Failed to apply %s=%r", key, val)
        if any(k in result for k in ("cost", "selling_price", "floor_price")):
            try:
                self._recompute_margin()
            except Exception:
                pass
        return applied

    # PAI Scrape (real Playwright lookup)
    def _on_pai_scrape(self) -> None:
        pn = (self._value("oem_part_number") or
              (self._ai_input.text().strip() if hasattr(self, "_ai_input") else ""))
        if not pn:
            QMessageBox.information(
                self, "PAI Scrape",
                "Enter an OEM Part # (section 1) or paste one in the AI bar first.")
            return
        if getattr(self, "_pai_worker", None) is not None:
            QMessageBox.information(self, "Busy", "A PAI lookup is already running.")
            return
        try:
            from jaks_inventory.ui.edit_product_dialog import _PAILookupWorker
        except Exception as exc:
            QMessageBox.critical(self, "PAI Scrape",
                                 f"Could not load PAI worker: {exc}")
            return
        product_name = self._value("title") or self._value("manufacturer") or ""
        self._pai_scrape_btn.setEnabled(False)
        self._pai_scrape_btn.setText("⏳ Searching PAI…")
        self._status_label.setText(
            "<span style='color:#bc8cff'>PAI</span> searching portal…")
        self._pai_worker = _PAILookupWorker(pn, product_name=str(product_name),
                                            parent=self)
        self._pai_worker.status_update.connect(self._on_pai_status)
        self._pai_worker.finished.connect(self._on_pai_done)
        self._pai_worker.multi_match.connect(self._on_pai_multi)
        self._pai_worker.error.connect(self._on_pai_error)
        self._pai_worker.start()

    def _on_pai_status(self, msg: str) -> None:
        short = msg if len(msg) <= 60 else msg[:57] + "…"
        self._status_label.setText(
            f"<span style='color:#bc8cff'>PAI</span> {short}")
        self._pai_scrape_btn.setText(f"⏳ {short[:24]}")

    def _on_pai_done(self, result: dict) -> None:
        self._pai_worker = None
        self._pai_scrape_btn.setEnabled(True)
        self._pai_scrape_btn.setText("🌐 PAI Scrape")
        self._apply_pai_result(result)

    def _on_pai_multi(self, matches: list) -> None:
        self._pai_worker = None
        self._pai_scrape_btn.setEnabled(True)
        self._pai_scrape_btn.setText("🌐 PAI Scrape")
        if not matches:
            self._status_label.setText(
                "<span style='color:#d29922'>!</span> PAI: no matches")
            return
        # Pick first by default; surface count in status.
        self._apply_pai_result(matches[0])
        self._status_label.setText(
            f"<span style='color:#d29922'>!</span> PAI returned {len(matches)} "
            "matches — first one applied. Refine OEM # for a unique hit.")

    def _on_pai_error(self, msg: str) -> None:
        self._pai_worker = None
        self._pai_scrape_btn.setEnabled(True)
        self._pai_scrape_btn.setText("🌐 PAI Scrape")
        self._status_label.setText(
            "<span style='color:#f85149'>!</span> PAI lookup failed")
        QMessageBox.warning(self, "PAI Lookup Failed", msg)

    def _apply_pai_result(self, r: dict) -> None:
        """Populate the vendor row + key form fields from a PAI result dict."""
        try:
            cost = float(r.get("cost") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            list_price = float(r.get("list_price") or 0.0)
        except (TypeError, ValueError):
            list_price = 0.0
        pai_sku = str(r.get("pai_sku") or r.get("sku") or "").strip()
        desc    = str(r.get("description") or "").strip()
        oem     = str(r.get("oem_number") or "").strip()
        url     = str(r.get("url") or r.get("source") or "").strip()
        weight  = str(r.get("weight") or "").strip()
        stock_total = r.get("total_available") or 0
        not_avail = bool(r.get("not_available"))
        in_stock = bool(r.get("in_stock"))

        # Vendor table primary row
        t = self._vendor_table
        def _set(col: int, text: str):
            it = QTableWidgetItem(text)
            t.setItem(0, col, it)
        _set(1, "PAI Industries")
        _set(2, pai_sku)
        _set(3, f"${cost:,.2f}" if cost else "")
        _set(4, "1")
        _set(5, "3-5 days" if in_stock else ("Backorder" if not_avail else ""))
        notes_parts = []
        if stock_total: notes_parts.append(f"{stock_total} on hand")
        if url:         notes_parts.append(url)
        _set(6, " · ".join(notes_parts))

        # Form fields
        filled = 0
        def _put(key: str, val):
            nonlocal filled
            if val in (None, "", 0, 0.0):
                return
            w = self.f.get(key)
            if w is None: return
            try:
                if isinstance(w, QLineEdit):
                    if not w.text().strip():
                        w.setText(str(val)); filled += 1
                elif isinstance(w, QTextEdit):
                    if not w.toPlainText().strip():
                        w.setPlainText(str(val)); filled += 1
            except Exception:
                pass
        if cost:        _put("cost", f"{cost:.2f}")
        if list_price:  _put("selling_price", f"{list_price:.2f}")
        if oem:         _put("oem_part_number", oem)
        if desc:
            _put("title", desc[:160])
            _put("description_html", desc)
        if weight:      _put("weight", re.sub(r"[^0-9.]", "", weight) or weight)

        try: self._recompute_margin()
        except Exception: pass

        reasoning = r.get("grok_reasoning") or ""
        msg = (f"<span style='color:#3fb950'>OK</span> "
               f"PAI — SKU <b>{pai_sku}</b> · cost ${cost:,.2f} · "
               f"list ${list_price:,.2f} · filled {filled} field(s)")
        if reasoning:
            msg += f" · <i>{reasoning[:80]}</i>"
        self._status_label.setText(msg)

    # ─── Save ──────────────────────────────────────────────
    def _value(self, key: str) -> Any:
        w = self.f.get(key)
        if w is None: return None
        if isinstance(w, QLineEdit):    return w.text().strip() or None
        if isinstance(w, QTextEdit):    return w.toPlainText().strip() or None
        if isinstance(w, QComboBox):    return w.currentText().strip() or None
        if isinstance(w, QCheckBox):    return 1 if w.isChecked() else 0
        return None

    def _num(self, key: str) -> float | None:
        v = self._value(key)
        if v is None: return None
        s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
        try: return float(s)
        except ValueError: return None

    def _save(self, *, close_after: bool, draft: bool = False,
              add_another: bool = False) -> None:
        sku = self._value("sku")
        if not sku:
            QMessageBox.warning(self, "Missing SKU",
                                "SKU is required to save a product.")
            return

        # Map collected fields → create_product() allowed kwargs
        payload: dict[str, Any] = {
            "sku": sku,
            "title":              self._value("title"),
            "category":           self._value("category"),
            "subcategory":        self._value("subcategory"),
            "manufacturer":       self._value("manufacturer"),
            "engine":             self._value("engine"),
            "truck_model":        self._value("truck_model"),
            "oem_part_number":    self._value("oem_part_number"),
            "xref_part_number":   self._value("xref_part_number"),
            "description_html":   self._value("description_html"),
            "inventory_status":   "draft" if draft else "active",
            "notes_internal":     self._value("notes_internal"),
            "notes_public":       self._value("notes_public"),
            "cost":               self._num("cost"),
            "pai_cost":           self._num("pai_cost"),
            "selling_price":      self._num("selling_price"),
            "compare_at_price":   self._num("compare_at_price"),
            "core_charge":        self._num("core_charge"),
            "min_qty":            int(self._num("min_qty") or 0) or None,
            "max_qty":            int(self._num("max_qty") or 0) or None,
            "weight":             self._num("weight"),
            "length":             self._num("length"),
            "width":              self._num("width"),
            "height":             self._num("height"),
            "supplier_warranty_value": self._num("supplier_warranty_value"),
            "jaks_warranty_charge":    self._num("jaks_warranty_charge"),
            "requires_serial":   1 if (self.f.get("requires_serial")
                                       and self.f["requires_serial"].isChecked()) else 0,
        }
        # strip None
        payload = {k: v for k, v in payload.items() if v is not None}

        try:
            result = create_product(**payload)
        except Exception as e:
            QMessageBox.critical(self, "Save Failed",
                                 f"Database error:<br><br>{e}")
            return

        if not result.get("success"):
            QMessageBox.warning(self, "Save Failed",
                                result.get("error") or "Unknown error")
            return

        self.created_id = int(result.get("id") or 0) or None
        self.should_add_another = add_another

        if close_after:
            self.accept()
        else:
            self._status_label.setText(
                f"<span style='color:{_GREEN}'>●</span> "
                f"Saved as #{self.created_id} (draft) — continue editing")
