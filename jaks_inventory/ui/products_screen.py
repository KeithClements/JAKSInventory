from __future__ import annotations

import csv
import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer, QEvent, QRect, QByteArray
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QKeySequence, QShortcut, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from db import (
    search_by_part_number,
    browse_products,
    get_all_manufacturers,
    get_all_categories,
    get_all_suppliers,
    get_inventory_transactions,
    get_sync_status,
    get_setting,
    set_setting,
    update_product,
    create_purchase_order,
    get_product_by_id,
    get_interchanges,
    get_product_locations,
    get_all_xrefs_for_product,
    get_last_scrape_run,
    get_product_by_sku,
    get_scrape_dates_for_product,
    delete_product,
    get_customers_who_bought,
    get_product_sales_stats,
    adjust_product_quantity,
    create_quote,
    add_quote_line,
    get_sales_summary_batch,
)

from .product_detail_dialog import ProductDetailDialog
from .edit_product_dialog import EditProductDialog
from .bulk_edit_dialog import BulkEditDialog
from .scraper_review_dialog import ScraperReviewDialog
from .quick_entry_dialog import QuickEntryDialog
from ..scraper import ScraperWorker


# ── Full-cell checkbox delegate ────────────────────────
class _FullCellCheckDelegate(QStyledItemDelegate):
    """Makes the entire cell a checkbox click target, not just the indicator."""

    def editorEvent(self, event, model, option, index):
        if (
            index.column() == COL_TAG
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            cur = model.data(index, Qt.ItemDataRole.CheckStateRole)
            new = (
                Qt.CheckState.Unchecked
                if cur == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            model.setData(index, new, Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


# ── Two-line Item delegate ─────────────────────────────
# Constant for the subtext role.
_SUBTEXT_ROLE = Qt.ItemDataRole.UserRole + 1


class _TwoLineItemDelegate(QStyledItemDelegate):
    """Paints the Item column with a bold title and a dim subtext below.

    Title comes from `Qt.DisplayRole`; subtext from `_SUBTEXT_ROLE`. Using a
    delegate (instead of a cell widget overlay) keeps sorting, filtering,
    selection painting and row tinting all working correctly.
    """

    def paint(self, painter, option, index):  # type: ignore[override]
        # Let the base class draw the cell background (alt rows, selection,
        # row tints set via Qt.BackgroundRole). We pass an empty option text
        # so it does NOT paint the display text \u2014 we draw it ourselves.
        opt = option.__class__(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else None
        if style is not None:
            from PySide6.QtWidgets import QStyle
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        else:
            super().paint(painter, option, index)

        sub = index.data(_SUBTEXT_ROLE) or ""
        rect: QRect = option.rect
        painter.save()
        # Choose text color based on selection state.
        is_selected = bool(option.state & getattr(option, "State_Selected",
                                                  option.state.__class__(0)))
        try:
            from PySide6.QtWidgets import QStyle
            is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        except Exception:
            pass
        if is_selected:
            title_color = QColor(option.palette.highlightedText().color())
            sub_color = QColor(option.palette.highlightedText().color())
            sub_color.setAlpha(180)
        else:
            title_color = QColor(_TEXT)
            sub_color = QColor(_TEXT_DIM)

        # Title line (bold).
        title_font = QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSize(max(9, title_font.pointSize()))
        painter.setFont(title_font)
        painter.setPen(QPen(title_color))
        pad_x = 8
        if sub:
            title_rect = QRect(rect.x() + pad_x, rect.y() + 3,
                               rect.width() - pad_x * 2, rect.height() // 2)
            sub_rect = QRect(rect.x() + pad_x, rect.y() + rect.height() // 2,
                             rect.width() - pad_x * 2,
                             rect.height() // 2 - 2)
        else:
            title_rect = QRect(rect.x() + pad_x, rect.y(),
                               rect.width() - pad_x * 2, rect.height())
            sub_rect = QRect()

        fm_title = painter.fontMetrics()
        title_elided = fm_title.elidedText(
            title or "", Qt.TextElideMode.ElideRight, title_rect.width()
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title_elided,
        )

        if sub:
            sub_font = QFont(option.font)
            sub_font.setPointSize(max(8, title_font.pointSize() - 1))
            painter.setFont(sub_font)
            painter.setPen(QPen(sub_color))
            fm_sub = painter.fontMetrics()
            sub_elided = fm_sub.elidedText(
                sub, Qt.TextElideMode.ElideRight, sub_rect.width()
            )
            painter.drawText(
                sub_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                sub_elided,
            )
        painter.restore()

    def sizeHint(self, option, index):  # type: ignore[override]
        base = super().sizeHint(option, index)
        sub = index.data(_SUBTEXT_ROLE) or ""
        if sub:
            return base.__class__(base.width(), max(base.height(), 38))
        return base


# ── Sortable numeric item ──────────────────────────────
class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically."""

    def __init__(self, value: float | int, display: str = "") -> None:
        super().__init__(display or str(value))
        self._sort_value = value
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumericItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)


# ── Column definitions ─────────────────────────────────
# Index constants for readability
COL_TAG = 0
COL_STATUS = 1           # status pill (renamed header to "Status")
COL_SKU = 2
COL_TITLE = 3            # rendered header: "Item"
COL_MFR = 4              # hidden by default
COL_SUPPLIER = 5         # rendered header: "Vendor"
COL_CATEGORY = 6
COL_QTY = 7              # rendered header: "On Hand"
COL_MIN = 8              # rendered header: "Reorder"
COL_MAX = 9              # hidden by default
COL_SUGGESTED = 10       # hidden by default
COL_COST = 11
COL_MARGIN = 12
COL_PRICE = 13           # rendered header: "List"
COL_MATCH = 14           # hidden
COL_TIER = 15            # hidden
COL_LAST_SCRAPE = 16     # hidden
COL_LAST_SOLD = 17       # hidden
COL_TIMES_SOLD = 18      # hidden
# New columns appended (mockup alignment):
COL_TYPE = 19            # NEW / REMAN / USED / CORE / KIT pill
COL_ON_ORDER = 20        # qty across open POs
COL_FLAGS = 21           # 📸 ♻ L 📄
COL_ALLOC = 22           # allocated qty (open SO lines) – best-effort 0 for now
COL_AVAIL = 23           # available bar (on_hand - alloc), rendered as progress bar
NUM_COLS = 24

HEADERS = [
    "",          # 0  Select
    "Status",    # 1  status pill
    "SKU",       # 2
    "Item",      # 3  was Product Name
    "Mfr / Platform", # 4 hidden
    "Vendor",    # 5  was Supplier
    "Category",  # 6
    "On Hand",   # 7  was Qty
    "Reorder",   # 8  was Min
    "Max",       # 9  hidden
    "Suggested", # 10 hidden
    "Cost",      # 11
    "Margin",    # 12
    "List",      # 13 was Price
    "Match",     # 14 hidden
    "Tier",      # 15 hidden
    "Last Scrape",   # 16 hidden
    "Last Sold",     # 17 hidden
    "Sold (12 mo)",  # 18 hidden
    "Type",      # 19 NEW
    "On Order",  # 20 NEW
    "Flags / Activity", # 21 attribute flags + activity dots
    "Alloc",     # 22 NEW
    "Avail",     # 23 NEW (progress bar)
]

VIEW_PRESETS: dict[str, set[int]] = {
    "browse": {
        COL_TAG, COL_STATUS, COL_SKU, COL_TITLE, COL_MFR,
        COL_QTY, COL_COST, COL_MARGIN, COL_PRICE,
        COL_LAST_SOLD, COL_TIMES_SOLD,
    },
    "reorder": {
        COL_TAG, COL_STATUS, COL_SKU, COL_TITLE, COL_SUPPLIER,
        COL_QTY, COL_MIN, COL_MAX, COL_SUGGESTED, COL_COST,
    },
    "pricing": {
        COL_SKU, COL_TITLE, COL_SUPPLIER, COL_PRICE,
        COL_COST, COL_MARGIN, COL_TIER, COL_LAST_SCRAPE,
    },
}

import html as _html_mod

# ── Supplier color palette ─────────────────────────────
_SUPPLIER_COLORS: dict[str, QColor] = {}


def _esc(text: str) -> str:
    """HTML-escape text for use in the detail drawer."""
    return _html_mod.escape(str(text)) if text else ""


log = logging.getLogger(__name__)


# ── Dark palette (matches inventory_mockup.html) ──────────
_BG         = "#0f1419"
_PANEL      = "#1a2128"
_PANEL2     = "#232c35"
_BORDER     = "#2d3a45"
_BORDER_HI  = "#3d4d5c"
_TEXT       = "#e6edf3"
_TEXT_DIM   = "#8b98a5"
_TEXT_FAINT = "#5d6b78"
_ACCENT     = "#ff8c42"
_ACCENT_DIM = "#b85d1f"
_GREEN      = "#3fb950"
_RED        = "#f85149"
_AMBER      = "#d29922"
_BLUE       = "#58a6ff"
_PURPLE     = "#bc8cff"
_TEAL       = "#39d0d8"


def _products_dark_qss() -> str:
    """Global dark-theme stylesheet for the Products screen."""
    return f"""
    ProductsScreen, ProductsScreen > QWidget {{
        background: {_BG};
        color: {_TEXT};
        font-family: "Segoe UI", system-ui, sans-serif;
        font-size: 12px;
    }}
    ProductsScreen QLabel {{ color: {_TEXT}; }}
    ProductsScreen QLabel[role="dim"] {{ color: {_TEXT_DIM}; }}
    ProductsScreen QLabel[role="faint"] {{ color: {_TEXT_FAINT}; }}

    /* Combos / line edits / search */
    ProductsScreen QLineEdit,
    ProductsScreen QComboBox,
    ProductsScreen QToolButton {{
        background: {_PANEL2};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 5px;
        padding: 5px 9px;
        selection-background-color: {_ACCENT};
    }}
    ProductsScreen QLineEdit:focus,
    ProductsScreen QComboBox:focus {{ border-color: {_ACCENT}; }}
    ProductsScreen QComboBox::drop-down {{ border: none; width: 18px; }}
    ProductsScreen QComboBox QAbstractItemView {{
        background: {_PANEL2}; color: {_TEXT};
        border: 1px solid {_BORDER_HI};
        selection-background-color: {_ACCENT};
    }}

    /* Generic push buttons */
    ProductsScreen QPushButton {{
        background: {_PANEL2};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 5px;
        padding: 6px 11px;
        font-weight: 600;
    }}
    ProductsScreen QPushButton:hover {{ border-color: {_BORDER_HI}; }}
    ProductsScreen QPushButton:disabled {{ color: {_TEXT_FAINT}; }}

    /* Primary orange CTA — opt in via property */
    ProductsScreen QPushButton[cta="primary"] {{
        background: {_ACCENT}; color: white; border: none;
    }}
    ProductsScreen QPushButton[cta="primary"]:hover {{ background: {_ACCENT_DIM}; }}

    /* AI buttons — purple/teal gradient */
    ProductsScreen QPushButton[cta="ai"],
    ProductsScreen QToolButton[cta="ai"] {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #6e3bff, stop:0.6 #bc8cff, stop:1 #39d0d8);
        color: white; border: none; font-weight: 700;
    }}
    ProductsScreen QPushButton[cta="ai-ghost"] {{
        background: rgba(110,59,255,0.10);
        color: {_PURPLE};
        border: 1px solid rgba(188,140,255,0.45);
    }}
    ProductsScreen QPushButton[cta="ai-ghost"]:hover {{
        background: rgba(110,59,255,0.22); color: {_TEXT};
        border-color: {_PURPLE};
    }}

    /* Table */
    ProductsScreen QTableWidget {{
        background: {_PANEL};
        gridline-color: {_BORDER};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        color: {_TEXT};
        alternate-background-color: #1f262e;
        selection-background-color: #2a3540;
        selection-color: {_TEXT};
    }}
    ProductsScreen QTableWidget::item:selected {{
        background: #2a3540; color: {_TEXT};
    }}
    ProductsScreen QTableWidget::item:hover {{
        background: #1d242c;
    }}
    ProductsScreen QHeaderView::section {{
        background: {_PANEL2}; color: {_TEXT_DIM};
        border: none; border-bottom: 1px solid {_BORDER};
        padding: 8px 10px;
        font-size: 10px; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.5px;
    }}
    ProductsScreen QHeaderView {{ background: {_PANEL2}; }}
    ProductsScreen QHeaderView::section:first {{
        background: {_PANEL2}; padding: 8px 0px;
    }}
    ProductsScreen QTableCornerButton::section {{
        background: {_PANEL2}; border: none;
    }}
    ProductsScreen QTableWidget {{
        background: {_PANEL}; border: none; outline: none;
    }}

    /* Splitter handle */
    ProductsScreen QSplitter::handle {{ background: {_BORDER}; height: 4px; }}

    /* Scrollbars */
    ProductsScreen QScrollBar:vertical, ProductsScreen QScrollBar:horizontal {{
        background: {_BG}; border: none; width: 12px; height: 12px;
    }}
    ProductsScreen QScrollBar::handle {{
        background: {_BORDER_HI}; border-radius: 6px; min-height: 30px;
    }}
    ProductsScreen QScrollBar::handle:hover {{ background: {_TEXT_DIM}; }}
    ProductsScreen QScrollBar::add-line, ProductsScreen QScrollBar::sub-line {{
        background: none; border: none; height: 0; width: 0;
    }}

    /* Menus */
    ProductsScreen QMenu {{
        background: {_PANEL2}; color: {_TEXT};
        border: 1px solid {_BORDER_HI};
    }}
    ProductsScreen QMenu::item:selected {{ background: {_ACCENT}; color: white; }}

    /* Detail / recent panels */
    ProductsScreen QFrame {{ background: {_PANEL}; border-color: {_BORDER}; }}
    ProductsScreen QTextBrowser {{
        background: {_PANEL}; color: {_TEXT}; border: none;
    }}

    /* Checkbox indicator (tag column) — unfilled box with an orange tick when checked */
    ProductsScreen QTableWidget::indicator {{
        width: 14px; height: 14px;
        border: 1.5px solid #4a5360;
        border-radius: 3px;
        background: transparent;
    }}
    ProductsScreen QTableWidget::indicator:hover {{ border-color: {_ACCENT}; }}
    ProductsScreen QTableWidget::indicator:checked {{
        background: transparent;
        border-color: {_ACCENT};
        image: url(data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M3 8.5l3.2 3.2L13 5' fill='none' stroke='%23ff8c42' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>);
    }}

    /* Progress bar */
    ProductsScreen QProgressBar {{
        background: {_PANEL2}; border: 1px solid {_BORDER};
        border-radius: 4px; text-align: center;
        color: {_TEXT}; font-size: 10px;
    }}
    ProductsScreen QProgressBar::chunk {{ background: {_PURPLE}; border-radius: 3px; }}
    """


class ProductsScreen(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._last_results: list[dict[str, Any]] = []
        self._displayed_results: list[dict[str, Any]] = []
        self._product_map: dict[int, dict[str, Any]] = {}  # id → product dict

        # ── Filter dropdowns ──
        self._mfr_combo = QComboBox()
        self._mfr_combo.addItem("All Manufacturers", "")
        self._mfr_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All Categories", "")
        self._cat_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._supplier_combo = QComboBox()
        self._supplier_combo.addItem("All Suppliers", "")
        self._supplier_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._stock_combo = QComboBox()
        for label, data in [
            ("All Stock Levels", "all"),
            ("In Stock", "in_stock"),
            ("Low Stock (Below Min)", "low"),
            ("Out of Stock", "out"),
            ("Pricing Review", "pricing_review"),
            ("Need Review", "need_review"),
            ("Tagged for PO", "tagged"),
        ]:
            self._stock_combo.addItem(label, data)
        self._stock_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._view_combo = QComboBox()
        self._view_combo.addItem("Browse", "browse")
        self._view_combo.addItem("Reorder", "reorder")
        self._view_combo.addItem("Pricing", "pricing")
        self._view_combo.currentIndexChanged.connect(self._on_view_mode_changed)

        # ── Search ──
        self._query = QLineEdit()
        self._query.setPlaceholderText("Search SKU, description, category, supplier, part numbers…")
        self._query.returnPressed.connect(self._on_search)

        # Live search with debounce (250ms)
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._on_search)
        self._query.textChanged.connect(self._on_search_text_changed)

        self._search_btn = QPushButton("🔍 Search")
        self._search_btn.clicked.connect(self._on_search)

        self._clear_btn = QPushButton("✕ Clear")
        self._clear_btn.setStyleSheet(
            "QPushButton { padding: 4px 8px; border: 1px solid #aaa; "
            "border-radius: 4px; font-size: 9pt; color: #c62828; }"
            "QPushButton:hover { background-color: #fce4ec; }"
        )
        self._clear_btn.setToolTip("Clear search and show all products")
        self._clear_btn.clicked.connect(self._on_clear_search)

        # ── Shared button styles ──
        _BTN_PRIMARY = (
            "QPushButton { background-color: #4a6741; color: white; "
            "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background-color: #5a7a51; }"
            "QPushButton:disabled { background-color: #ccc; color: #888; }"
        )
        _BTN_SECONDARY = (
            "QPushButton { padding: 4px 8px; border: 1px solid #aaa; "
            "border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background-color: #ececec; }"
        )
        _BTN_ACCENT = (
            "QPushButton { background-color: #1565c0; color: white; "
            "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background-color: #1976d2; }"
        )

        # ── Action buttons ──
        self._add_btn = QPushButton("➕ Add Product")
        self._add_btn.setStyleSheet(_BTN_PRIMARY)
        self._add_btn.clicked.connect(self._on_add_product)

        self._quick_entry_btn = QPushButton("⚡ Quick Entry")
        self._quick_entry_btn.setStyleSheet(_BTN_PRIMARY)
        self._quick_entry_btn.setToolTip("Compact keyboard-first product creation (Ctrl+Q)")
        self._quick_entry_btn.clicked.connect(self._on_quick_entry)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.setStyleSheet(_BTN_SECONDARY)
        self._edit_btn.clicked.connect(self._on_edit_selected)
        self._edit_btn.setEnabled(False)
        self._edit_btn.setToolTip("Edit selected product (Ctrl+E)")

        self._bulk_btn = QPushButton("✏️ Bulk Edit")
        self._bulk_btn.setStyleSheet(_BTN_SECONDARY)
        self._bulk_btn.clicked.connect(self._on_bulk_edit)
        self._bulk_btn.setEnabled(False)
        self._bulk_btn.setToolTip("Edit fields on 2+ selected products at once")

        self._create_po_btn = QPushButton("🛒 Create PO")
        self._create_po_btn.clicked.connect(self._on_create_po_from_tagged)
        self._create_po_btn.setToolTip("Disabled in Products screen. Use Purchase Orders workflow.")
        self._create_po_btn.setStyleSheet(_BTN_ACCENT)
        self._create_po_btn.setVisible(False)
        self._create_po_btn.setEnabled(False)

        self._export_btn = QPushButton("📥 Export")
        self._export_btn.setStyleSheet(_BTN_SECONDARY)
        self._export_btn.clicked.connect(self._export_to_csv)

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setStyleSheet(_BTN_SECONDARY)
        self._refresh_btn.clicked.connect(self._full_refresh)

        self._columns_btn = QToolButton()
        self._columns_btn.setText("Columns ▾")
        self._columns_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._columns_menu = QMenu(self)
        self._columns_btn.setMenu(self._columns_menu)

        self._results_label = QLabel("")
        self._results_label.setStyleSheet("font-weight: bold;")

        # ── Quick select tools (compact menu) ──
        self._bulk_select_btn = QToolButton()
        self._bulk_select_btn.setText("Bulk Select ▾")
        self._bulk_select_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._bulk_select_menu = QMenu(self)
        self._bulk_select_btn.setMenu(self._bulk_select_menu)

        self._act_sel_oos = QAction("Select Out of Stock", self)
        self._act_sel_oos.triggered.connect(self._select_out_of_stock)
        self._bulk_select_menu.addAction(self._act_sel_oos)

        self._act_sel_below = QAction("Select Below Min", self)
        self._act_sel_below.triggered.connect(self._select_below_min)
        self._bulk_select_menu.addAction(self._act_sel_below)

        self._act_sel_suggested = QAction("Select Suggested > 0", self)
        self._act_sel_suggested.triggered.connect(self._select_suggested)
        self._bulk_select_menu.addAction(self._act_sel_suggested)

        self._bulk_select_menu.addSeparator()

        self._act_sel_visible = QAction("Select All Visible", self)
        self._act_sel_visible.triggered.connect(self._select_all_visible)
        self._bulk_select_menu.addAction(self._act_sel_visible)

        self._act_sel_clear = QAction("Clear Selection", self)
        self._act_sel_clear.triggered.connect(self._table_clear_selection)
        self._bulk_select_menu.addAction(self._act_sel_clear)

        self._selection_info_label = QLabel("")
        self._selection_info_label.setStyleSheet("color: #555; font-size: 11px;")

        # ── Scrape Selected dropdown ────────────────────────
        self._scrape_btn = QToolButton()
        self._scrape_btn.setText("🕷️ Scrape Selected ▾")
        self._scrape_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._scrape_btn.setStyleSheet(
            "QToolButton { background-color: #6a1b9a; color: white; "
            "font-weight: bold; padding: 4px 10px; border-radius: 4px; }"
            "QToolButton:hover { background-color: #7b1fa2; }"
            "QToolButton::menu-indicator { image: none; }"
        )
        self._scrape_menu = QMenu(self)
        self._act_scrape_pricing = QAction("💲 Scrape Selected for Pricing", self)
        self._act_scrape_pricing.triggered.connect(lambda: self._scrape_selected("pricing"))
        self._scrape_menu.addAction(self._act_scrape_pricing)
        self._act_scrape_xref = QAction("🔗 Scrape Selected for Crosses", self)
        self._act_scrape_xref.triggered.connect(lambda: self._scrape_selected("xref"))
        self._scrape_menu.addAction(self._act_scrape_xref)
        self._act_scrape_both = QAction("🕷️ Scrape Selected for Both", self)
        self._act_scrape_both.triggered.connect(lambda: self._scrape_selected("both"))
        self._scrape_menu.addAction(self._act_scrape_both)
        self._scrape_menu.addSeparator()
        self._act_scrape_tagged = QAction("🏷️ Scrape All Tagged Products", self)
        self._act_scrape_tagged.triggered.connect(self._scrape_tagged)
        self._scrape_menu.addAction(self._act_scrape_tagged)
        self._scrape_btn.setMenu(self._scrape_menu)

        # Scrape progress bar (hidden until needed)
        self._scrape_progress = QProgressBar()
        self._scrape_progress.setMaximumHeight(16)
        self._scrape_progress.setMaximumWidth(260)
        self._scrape_progress.hide()
        self._scrape_status_label = QLabel("")
        self._scrape_status_label.setStyleSheet("color: #6a1b9a; font-size: 10px;")
        self._scrape_status_label.hide()
        self._scrape_worker: ScraperWorker | None = None
        self._scrape_queue: list[dict] = []
        self._scrape_job_type: str = "both"
        self._scrape_total: int = 0
        self._scrape_completed: int = 0

        # ── Summary strip (hidden — stats shown in results label) ──
        self._stat_total_btn = QPushButton("0 Products")
        self._stat_tagged_btn = QPushButton("0 Tagged")
        self._stat_out_btn = QPushButton("0 Out of Stock")
        self._stat_below_btn = QPushButton("0 Below Min")
        for btn in (
            self._stat_total_btn,
            self._stat_tagged_btn,
            self._stat_out_btn,
            self._stat_below_btn,
        ):
            btn.hide()

        self._stat_total_btn.clicked.connect(lambda: self._set_stock_filter("all"))
        self._stat_tagged_btn.clicked.connect(lambda: self._set_stock_filter("tagged"))
        self._stat_out_btn.clicked.connect(lambda: self._set_stock_filter("out"))
        self._stat_below_btn.clicked.connect(lambda: self._set_stock_filter("low"))

        # ── Main table ──
        self._table = QTableWidget(0, NUM_COLS)
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(40)
        self._table.verticalHeader().setVisible(False)
        self._table.setCornerButtonEnabled(False)
        self._table.setAlternatingRowColors(True)
        # NOTE: dark-theme row/selection colors come from _products_dark_qss().
        # Do not set a light-theme stylesheet here — it overrides the dark theme.
        # Override the global QPalette Highlight (orange accent) so selected rows
        # use a subtle tint instead of the loud orange that the global palette gives us.
        from PySide6.QtGui import QPalette
        _tbl_pal = self._table.palette()
        _tbl_pal.setColor(QPalette.ColorRole.Highlight, QColor("#2a3540"))
        _tbl_pal.setColor(QPalette.ColorRole.HighlightedText, QColor(_TEXT))
        _tbl_pal.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor("#232c35"))
        _tbl_pal.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, QColor(_TEXT))
        self._table.setPalette(_tbl_pal)
        self._table.verticalHeader().setDefaultSectionSize(26)  # slightly more room
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.cellDoubleClicked.connect(self._on_edit_from_dblclick)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellChanged.connect(self._on_cell_changed)

        # Full-cell checkbox delegate so clicking anywhere in COL_TAG toggles
        self._table.setItemDelegateForColumn(COL_TAG, _FullCellCheckDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TITLE, _TwoLineItemDelegate(self._table))

        # Column widths
        self._table.setColumnWidth(COL_TAG, 60)
        # Lock the tag/select column at a fixed width so the header
        # checkbox isn't clipped when the user resizes neighbouring columns.
        hdr.setSectionResizeMode(COL_TAG, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_STATUS, 110)
        self._table.setColumnWidth(COL_TITLE, 400)
        self._table.setColumnWidth(COL_MFR, 200)

        # Tooltip on select/tag column header
        self._table.horizontalHeaderItem(COL_TAG).setToolTip(
            "Check to select for bulk actions or tag for purchase order")

        # ── Select-All checkbox in header ──
        self._select_all_cb = QCheckBox(self._table.horizontalHeader())
        self._select_all_cb.setToolTip("Select / Deselect all visible rows")
        # Transparent background so the header section colour shows through;
        # without this the QCheckBox paints a solid dark square in the
        # top-left of the header.
        self._select_all_cb.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._select_all_cb.setStyleSheet(
            "QCheckBox { background: transparent; padding: 0; margin: 0; }"
            "QCheckBox::indicator { width: 14px; height: 14px;"
            " border: 1.5px solid #4a5360; border-radius: 3px;"
            " background: transparent; }"
            f"QCheckBox::indicator:hover {{ border-color: {_ACCENT}; }}"
            "QCheckBox::indicator:checked {"
            f" background: transparent; border-color: {_ACCENT};"
            " image: url(data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M3 8.5l3.2 3.2L13 5' fill='none' stroke='%23ff8c42' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/></svg>);"
            " }"
        )
        self._select_all_cb.stateChanged.connect(self._on_select_all_toggled)
        # Position the checkbox in the header (will be repositioned on resize)
        self._table.horizontalHeader().sectionResized.connect(self._reposition_select_all_cb)
        self._table.horizontalHeader().geometriesChanged.connect(self._reposition_select_all_cb)
        self._reposition_select_all_cb()

        # ── Recent Activity panel (collapsible, default collapsed) ──
        self._recent_frame = QFrame()
        self._recent_frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self._recent_frame.setMaximumHeight(130)
        self._recent_frame.setStyleSheet(f"QFrame {{ background-color: {_PANEL}; }}")

        self._recent_table = QTableWidget(0, 5)
        self._recent_table.setHorizontalHeaderLabels(["Time", "SKU", "Type", "Change", "Notes"])
        self._recent_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._recent_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._recent_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._recent_table.setMaximumHeight(90)
        self._recent_table.verticalHeader().setVisible(False)

        self._recent_toggle = QPushButton("▶ Recent Activity")
        self._recent_toggle.setFlat(True)
        self._recent_toggle.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; padding: 2px 6px; }"
        )
        self._recent_toggle.clicked.connect(self._toggle_recent)

        recent_layout = QVBoxLayout()
        recent_layout.setContentsMargins(5, 2, 5, 2)
        recent_layout.addWidget(self._recent_toggle)
        recent_layout.addWidget(self._recent_table)
        self._recent_frame.setLayout(recent_layout)

        # Default collapsed
        self._recent_table.setVisible(False)
        self._recent_frame.setMaximumHeight(28)

        # ── Sync status ──
        self._sync_status_label = QLabel()
        self._sync_status_label.setStyleSheet("padding: 4px 8px; border-radius: 4px;")

        # ── Group By dropdown ──
        self._group_combo = QComboBox()
        self._group_combo.addItem("No Grouping", "")
        self._group_combo.addItem("Group by Supplier", "supplier")
        self._group_combo.addItem("Group by Category", "category")
        self._group_combo.addItem("Group by Manufacturer", "manufacturer")
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)

        # ── Detail Drawer (below table) ──
        self._detail_panel = QFrame()
        self._detail_panel.setFrameStyle(QFrame.Shape.NoFrame)
        self._detail_panel.setStyleSheet(
            f"QFrame {{ background: {_PANEL}; border-left: 1px solid {_BORDER}; }}"
        )

        self._detail_toggle = QPushButton("▶ Product Detail")
        self._detail_toggle.setFlat(True)
        self._detail_toggle.setStyleSheet(
            f"QPushButton {{ text-align:left; font-weight:700; padding:2px 6px;"
            f" border:none; background:transparent; color:{_ACCENT}; font-size:12.5px; }}"
        )
        self._detail_toggle.clicked.connect(self._toggle_detail_drawer)

        # Close (X) button to dismiss the slide-over drawer
        self._detail_close_btn = QPushButton("✕")
        self._detail_close_btn.setFlat(True)
        self._detail_close_btn.setFixedSize(22, 22)
        self._detail_close_btn.setToolTip("Close detail drawer")
        self._detail_close_btn.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#8b98a5;"
            "font-size:14px;font-weight:600;}"
            "QPushButton:hover{color:#f85149;}"
        )
        self._detail_close_btn.clicked.connect(self._hide_detail_drawer)

        self._detail_browser = QTextBrowser()
        self._detail_browser.setOpenExternalLinks(True)
        self._detail_browser.setStyleSheet(
            f"QTextBrowser {{ border:none; background:{_PANEL}; color:{_TEXT};"
            f" font-size:12px; }}"
        )
        self._detail_browser.setHtml(
            f'<p style="color:{_TEXT_DIM};">Select a product row to see details here.</p>'
        )

        detail_layout = QVBoxLayout()
        detail_layout.setContentsMargins(6, 2, 6, 4)
        detail_header = QHBoxLayout()
        detail_header.setContentsMargins(0, 0, 0, 0)
        detail_header.addWidget(self._detail_toggle, 1)
        detail_header.addWidget(self._detail_close_btn)
        detail_layout.addLayout(detail_header)
        detail_layout.addWidget(self._detail_browser, 1)
        self._detail_panel.setLayout(detail_layout)

        # Default: drawer collapsed (slides in on row click)
        self._detail_expanded = False

        # ═══════════════════════════════════════
        #  LAYOUT (mockup-aligned: single filter+action row, footer summary)
        # ═══════════════════════════════════════
        # — Re-label combos to match mockup
        self._mfr_combo.setItemText(0, "All Brands")
        self._supplier_combo.setItemText(0, "All Vendors")
        self._query.setPlaceholderText(
            "🔍   Search SKU, OEM #, description, vendor SKU, ESN…")

        # — NEW: Type filter (NEW / REMAN / USED / CORE / KIT)
        self._type_combo = QComboBox()
        for label, data in [
            ("All Types", ""),
            ("NEW", "new"), ("REMAN", "reman"), ("USED", "used"),
            ("CORE", "core"), ("KIT", "kit"),
        ]:
            self._type_combo.addItem(label, data)
        self._type_combo.currentIndexChanged.connect(self._on_filter_changed)

        # — NEW: Warehouse filter (placeholder until multi-warehouse lands)
        self._wh_combo = QComboBox()
        self._wh_combo.addItem("All Warehouses", "")
        self._wh_combo.currentIndexChanged.connect(self._on_filter_changed)

        # — Hide widgets that moved into menus / footer / header
        for _w in (self._search_btn, self._clear_btn, self._add_btn,
                   self._quick_entry_btn):
            _w.hide()

        # — Single consolidated filter / action row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_row.addWidget(self._query, 1)
        filter_row.addWidget(self._cat_combo)
        filter_row.addWidget(self._type_combo)
        filter_row.addWidget(self._wh_combo)
        filter_row.addWidget(self._supplier_combo)   # "All Vendors"
        # Hide Brands and Stock Levels to declutter (mockup parity)
        self._mfr_combo.hide()
        self._stock_combo.hide()
        filter_row.addStretch()
        filter_row.addWidget(self._edit_btn)
        filter_row.addWidget(self._bulk_btn)
        # Scrape Selected + Columns moved into More ▾ overflow (declutter)
        self._scrape_btn.hide()
        self._columns_btn.hide()

        # Import Vendor Product — demoted into More menu
        self._from_vendor_btn = QPushButton("📥 Import Vendor Product")
        self._from_vendor_btn.clicked.connect(self._on_add_from_vendor)
        self._from_vendor_btn.hide()

        # "More" overflow menu for secondary actions
        self._more_btn = QToolButton()
        self._more_btn.setText("More ▾")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_btn.setStyleSheet(
            "QToolButton::menu-indicator { image: none; }"
        )
        more_menu = QMenu(self)
        more_menu.addAction("📥 Import Vendor Product…", self._on_add_from_vendor)
        more_menu.addAction("⚡ Quick Entry", self._on_quick_entry)
        more_menu.addAction("🔍 Part Finder", self._on_open_part_finder)
        more_menu.addSeparator()
        more_menu.addAction("📥 Export CSV", self._export_to_csv)
        more_menu.addAction("↻ Refresh", self._full_refresh)
        more_menu.addSeparator()
        # Group submenu (was a top-level combo)
        group_sub = more_menu.addMenu("📁 Group by")
        for label, data in [("None", ""), ("Supplier", "supplier"),
                             ("Category", "category"),
                             ("Manufacturer", "manufacturer")]:
            group_sub.addAction(label,
                lambda d=data: self._set_group_mode(d))
        # View preset submenu (was a top-level combo)
        view_sub = more_menu.addMenu("👁 View preset")
        for label, data in [("Browse", "browse"),
                             ("Reorder", "reorder"),
                             ("Pricing", "pricing")]:
            view_sub.addAction(label,
                lambda d=data: self._set_view_mode(d))
        more_menu.addSeparator()
        # Bulk Select submenu
        bulk_sel_sub = more_menu.addMenu("☑️ Bulk Select")
        bulk_sel_sub.addAction("Select Out of Stock", self._select_out_of_stock)
        bulk_sel_sub.addAction("Select Below Min", self._select_below_min)
        bulk_sel_sub.addAction("Select Suggested > 0", self._select_suggested)
        bulk_sel_sub.addSeparator()
        bulk_sel_sub.addAction("Select All Visible", self._select_all_visible)
        bulk_sel_sub.addAction("Clear Selection", self._table_clear_selection)
        more_menu.addSeparator()
        # AI helpers submenu (moved from the attention bar)
        ai_sub = more_menu.addMenu("✨ AI helpers")
        ai_sub.addAction("Find missing data",
            lambda: self._ai_stub("AI · Find missing data"))
        ai_sub.addAction("Suggest reorders",
            lambda: self._ai_stub("AI · Suggest reorders"))
        # Scraper submenu (moved from filter row)
        scrape_sub = more_menu.addMenu("🕷️ Scrape Selected")
        scrape_sub.addAction("Scrape for Pricing",
            lambda: self._scrape_selected("pricing"))
        scrape_sub.addAction("Scrape for Cross-References",
            lambda: self._scrape_selected("xref"))
        scrape_sub.addAction("Scrape for Both",
            lambda: self._scrape_selected("both"))
        more_menu.addSeparator()
        # Columns visibility (was its own button)
        more_menu.addAction("📊 Columns…",
            lambda: self._columns_menu.exec(QCursor.pos()))
        # Density toggle (additional suggestion)
        more_menu.addSeparator()
        density_sub = more_menu.addMenu("⇕ Row density")
        density_sub.addAction("Comfortable",
            lambda: self._set_row_density("comfortable"))
        density_sub.addAction("Compact",
            lambda: self._set_row_density("compact"))
        # Saved views (mockup §4)
        more_menu.addSeparator()
        self._saved_views_menu = more_menu.addMenu("📑 Saved views")
        self._rebuild_saved_views_menu()
        # Reorder simulator (mockup §4)
        more_menu.addAction("🔮 Reorder simulator…",
            self._open_reorder_simulator)
        self._more_btn.setMenu(more_menu)
        # Clear filters — reset search + all combos
        self._clear_btn = QPushButton("🧹 Clear")
        self._clear_btn.setToolTip("Reset all filters")
        self._clear_btn.setStyleSheet(
            f"QPushButton{{background:#2a1f1a;border:1px solid #5a3a2a;"
            f"color:{_ACCENT};font-weight:700;border-radius:5px;padding:6px 11px;}}"
            f"QPushButton:hover{{background:#3a2820;}}"
        )
        self._clear_btn.clicked.connect(self._clear_filters)
        filter_row.addWidget(self._clear_btn)
        filter_row.addWidget(self._more_btn)

        # — Footer (replaces the old gray status row)
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(2, 2, 2, 0)
        footer_row.addWidget(self._results_label, 1)
        footer_row.addWidget(self._scrape_progress)
        footer_row.addWidget(self._scrape_status_label)
        footer_row.addWidget(self._selection_info_label)
        footer_row.addWidget(self._sync_status_label)

        # Drawer removed per redesign — the table fills the full width.
        # `self._detail_panel` is still constructed (other code references it)
        # but is never added to a visible container.
        self._recent_frame.hide()
        self._detail_panel.hide()
        # Lightweight stand-in so existing references to `self._splitter` (e.g.
        # sizes(), setSizes()) still work without re-introducing a side panel.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._table)

        layout = QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._build_header_bar())
        layout.addWidget(self._build_kpi_strip())
        layout.addWidget(self._build_attention_bar())
        layout.addLayout(filter_row)
        layout.addWidget(self._splitter, 1)
        layout.addWidget(self._build_activity_legend())
        layout.addLayout(footer_row)
        self.setLayout(layout)
        self._results_label.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;background:transparent;")

        # Apply dark mockup theme
        self.setStyleSheet(_products_dark_qss())

        self._column_actions: dict[int, QAction] = {}
        self._build_columns_menu()
        self._load_saved_column_layout()
        # Apply mockup width defaults only if the user has no persisted
        # header state yet. Otherwise restore exactly what they last had
        # (column widths, order, hidden flags) so manual resizes survive
        # across launches.
        _saved_hdr_state = get_setting("products_header_state")
        if _saved_hdr_state:
            try:
                self._table.horizontalHeader().restoreState(
                    QByteArray.fromBase64(_saved_hdr_state.encode("ascii"))
                )
            except Exception:
                self._apply_mockup_column_layout()
        else:
            self._apply_mockup_column_layout()

        # Persist header state whenever the user resizes / reorders columns.
        _hdr = self._table.horizontalHeader()
        _hdr.sectionResized.connect(lambda *_: self._save_header_state())
        _hdr.sectionMoved.connect(lambda *_: self._save_header_state())

        self._setup_shortcuts()
        self._load_filters()           # populates combos + triggers initial browse
        self._update_sync_status()

    # — Hidden-widget shims so old code paths keep working ---------------
    def _set_view_mode(self, mode: str) -> None:
        idx = self._view_combo.findData(mode)
        if idx >= 0:
            self._view_combo.setCurrentIndex(idx)  # triggers _on_view_mode_changed

    def _set_group_mode(self, mode: str) -> None:
        idx = self._group_combo.findData(mode)
        if idx >= 0:
            self._group_combo.setCurrentIndex(idx)  # triggers _on_group_changed

    def _apply_mockup_column_layout(self) -> None:
        """Reorder + rename + hide columns to match the redesign mockup.

        Target (inventory_products_redesign.html, 14 columns):
            Select | SKU | Item | Type | Category | On Hand | Reorder |
            On Order | Cost | List | Margin | Vendor | Status | Flags
        """
        hdr = self._table.horizontalHeader()
        # Hide legacy / detail columns (Alloc + Avail are now hidden too —
        # the redesign drops them from the default grid; on-hand + reorder
        # are enough at-a-glance and the detail drawer covers the rest).
        for col in (COL_MFR, COL_MAX, COL_SUGGESTED, COL_MATCH, COL_TIER,
                    COL_LAST_SCRAPE, COL_LAST_SOLD, COL_TIMES_SOLD,
                    COL_ALLOC, COL_AVAIL):
            self._table.setColumnHidden(col, True)
        order = [COL_TAG, COL_SKU, COL_TITLE, COL_TYPE, COL_CATEGORY,
                 COL_QTY, COL_MIN, COL_ON_ORDER,
                 COL_COST, COL_PRICE, COL_MARGIN, COL_SUPPLIER, COL_STATUS,
                 COL_FLAGS]
        for col in order:
            self._table.setColumnHidden(col, False)
        for visual_index, logical in enumerate(order):
            cur_visual = hdr.visualIndex(logical)
            if cur_visual != visual_index:
                hdr.moveSection(cur_visual, visual_index)
        # Pleasant default widths (compact enough that Status + Flags fit on most screens)
        self._table.setColumnWidth(COL_TAG, 28)
        self._table.setColumnWidth(COL_SKU, 150)
        self._table.setColumnWidth(COL_TITLE, 280)
        self._table.setColumnWidth(COL_TYPE, 70)
        self._table.setColumnWidth(COL_CATEGORY, 130)
        self._table.setColumnWidth(COL_QTY, 70)
        self._table.setColumnWidth(COL_MIN, 70)
        self._table.setColumnWidth(COL_ON_ORDER, 75)
        self._table.setColumnWidth(COL_COST, 80)
        self._table.setColumnWidth(COL_PRICE, 80)
        self._table.setColumnWidth(COL_MARGIN, 75)
        self._table.setColumnWidth(COL_SUPPLIER, 130)
        self._table.setColumnWidth(COL_STATUS, 100)
        self._table.setColumnWidth(COL_FLAGS, 110)
        # Taller rows so the 2-line Item cell has room.
        self._table.verticalHeader().setDefaultSectionSize(38)

    # — Avail cell builder —
    def _make_avail_cell(self, avail: int, on_hand: int, reorder: int) -> QWidget:
        """Tiny widget rendering the avail count with a status-colored label.

        Earlier versions added a QProgressBar underneath, but the global
        ProductsScreen QProgressBar QSS won the cascade and made every bar
        render as a full purple chunk regardless of the value — which made
        the column look broken. A single colored numeric label is clearer.
        """
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 0, 8, 0)
        lay.setSpacing(0)
        if avail <= 0:
            color = _RED
        elif avail < max(reorder, 1):
            color = _AMBER
        else:
            color = _GREEN
        num = QLabel(str(avail))
        num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        num.setStyleSheet(
            f"color:{color};font-weight:700;font-size:13px;background:transparent;"
        )
        lay.addWidget(num)
        return w

    # — 2-line Item cell: rendered via _TwoLineItemDelegate now —

    # — Flags cell (4 colored mini-squares) —
    def _make_flags_cell(self, has_photo: bool, has_core: bool,
                         lot_tracked: bool, has_spec: bool) -> QWidget:
        """Render the redesign's 4-square flag chip group.

        Each flag is an 18\u00d718 rounded square with a colored fill when set
        and a muted dim style when not. Matches `.act` in the mockup.
        """
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(3)
        lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        on_style = (
            "border-radius:4px;color:white;font-weight:700;"
            "font-size:10px;min-width:18px;min-height:18px;"
            "max-width:18px;max-height:18px;"
            "padding:0;qproperty-alignment:'AlignCenter';"
        )
        off_style = (
            f"background:{_PANEL2};border:1px solid {_BORDER};"
            f"color:{_TEXT_FAINT};border-radius:4px;font-weight:600;"
            "font-size:10px;min-width:18px;min-height:18px;"
            "max-width:18px;max-height:18px;"
            "padding:0;qproperty-alignment:'AlignCenter';"
        )
        for glyph, on, color, tip_on, tip_off in [
            ("\U0001F4F8", has_photo,   _GREEN,  "Has photo",      "No photo"),
            ("\u267B",     has_core,    _TEAL,   "Has core",       "No core"),
            ("L",          lot_tracked, _BLUE,   "Lot / serial",   "Not lot-tracked"),
            ("\U0001F4C4", has_spec,    _PURPLE, "Has spec sheet", "No spec sheet"),
        ]:
            lbl = QLabel(glyph)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedSize(18, 18)
            if on:
                lbl.setStyleSheet(f"background:{color};{on_style}")
                lbl.setToolTip(tip_on)
            else:
                lbl.setStyleSheet(off_style)
                lbl.setToolTip(tip_off)
            lay.addWidget(lbl)
        return w

    # — Row density toggle (additional suggestion) —
    def _set_row_density(self, mode: str) -> None:
        """Compact or comfortable row height. Persisted via Qt settings if available."""
        height = 24 if mode == "compact" else 36
        vh = self._table.verticalHeader()
        vh.setDefaultSectionSize(height)
        for r in range(self._table.rowCount()):
            self._table.setRowHeight(r, height)
        try:
            from db.settings import set_setting  # type: ignore
            set_setting("products_row_density", mode)
        except Exception:
            pass

    # ────────────────────────────────────────────
    #  DARK-THEME HEADER:  KPI strip + Attention bar
    # ────────────────────────────────────────────
    def _build_header_bar(self) -> QWidget:
        """Top header: title + subtitle + AI Catalog Import + Add Product."""
        bar = QFrame()
        bar.setStyleSheet(f"QFrame{{background:{_BG};border:none;}}")
        row = QHBoxLayout(bar)
        row.setContentsMargins(2, 4, 2, 8)
        row.setSpacing(8)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("Products")
        title.setStyleSheet(
            f"color:{_TEXT};font-size:18px;font-weight:700;background:transparent;")
        sub = QLabel("SKU master · on-hand · reorder · pricing")
        sub.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;background:transparent;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        row.addLayout(title_box)
        row.addStretch()

        # Standard catalog actions (mockup image 2)
        csv_btn = QPushButton("📁 Import CSV")
        csv_btn.clicked.connect(self._on_import_csv_clicked)
        row.addWidget(csv_btn)

        export_btn = QPushButton("📤 Export")
        export_btn.clicked.connect(self._export_to_csv)
        row.addWidget(export_btn)

        labels_btn = QPushButton("🏷 Print Labels")
        labels_btn.clicked.connect(lambda: self._ai_stub("Print Labels"))
        row.addWidget(labels_btn)

        new_btn = QPushButton("+ New Product")
        new_btn.setProperty("cta", "primary")
        new_btn.clicked.connect(self._on_add_product)
        row.addWidget(new_btn)

        ai_import = QPushButton("✨ AI Catalog Import")
        ai_import.setProperty("cta", "ai")
        ai_import.clicked.connect(lambda: self._ai_stub("AI Catalog Import"))
        row.addWidget(ai_import)

        return bar

    def _on_import_csv_clicked(self) -> None:
        """Header 'Import CSV' — routes to existing bulk import screen if available."""
        mw = self.window()
        nav = getattr(mw, "_navigate", None) or getattr(mw, "navigate_to", None)
        if callable(nav):
            try:
                nav("inventory", "bulk_import")
                return
            except Exception:
                pass
        self._ai_stub("Import CSV")

    def _build_kpi_strip(self) -> QWidget:
        """7-card KPI strip across the top."""
        wrap = QFrame()
        wrap.setStyleSheet(f"QFrame{{background:{_BG};border:none;}}")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._kpi_labels: dict[str, QLabel] = {}
        self._kpi_subs: dict[str, QLabel] = {}

        kpis = [
            ("active",   "ACTIVE SKUS",     _BLUE,   "in catalog"),
            ("value",    "ON-HAND VALUE",   _GREEN,  "at avg cost"),
            ("low",      "LOW STOCK",       _AMBER,  "below reorder pt"),
            ("out",      "OUT OF STOCK",    _RED,    "qty = 0"),
            ("on_order", "ON ORDER (PO)",   _PURPLE, "across open POs"),
            ("dead",     "DEAD STOCK",      _ACCENT, "no sale 180d"),
            ("margin",   "AVG MARGIN",      _TEAL,   "trailing 30d"),
        ]
        for key, label, color, sub in kpis:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame{{background:{_PANEL};border:1px solid {_BORDER};"
                f"border-left:3px solid {color};border-radius:8px;}}"
                f"QFrame:hover{{border-color:{_BORDER_HI};}}"
            )
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(f"Click to drill into: {label}")
            card.mousePressEvent = (
                lambda ev, k=key: self._on_kpi_card_clicked(k)
            )
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(2)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color:{_TEXT_DIM};font-size:9px;font-weight:700;"
                f"letter-spacing:0.8px;background:transparent;border:none;")
            val = QLabel("—")
            val.setStyleSheet(
                f"color:{color};font-size:20px;font-weight:700;"
                f"background:transparent;border:none;")
            subl = QLabel(sub)
            subl.setStyleSheet(
                f"color:{_TEXT_FAINT};font-size:10px;background:transparent;border:none;")
            v.addWidget(lbl)
            v.addWidget(val)
            v.addWidget(subl)
            self._kpi_labels[key] = val
            self._kpi_subs[key] = subl
            layout.addWidget(card, 1)
        return wrap

    def _build_activity_legend(self) -> QWidget:
        """Legend strip explaining the per-row attribute flags & activity dots.

        Attribute flags on the left are live today. Activity dots (Q/S/P/A/R/K)
        require backing modules (quotes, sales orders, adjustments) that
        aren't wired yet, so we surface that honestly instead of leaving the
        legend dangling next to empty data.
        """
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame{{background:transparent;border-top:1px dashed {_BORDER};}}")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(6, 6, 6, 4)
        row.setSpacing(16)

        head = QLabel("Flags column legend:")
        head.setStyleSheet(
            f"color:{_TEXT_DIM};font-size:11px;font-weight:800;"
            f"background:transparent;letter-spacing:0.4px;")
        row.addWidget(head)

        for glyph, label in [
            ("\U0001F4F8", "photo"),
            ("\u267B",     "core"),
            ("L",          "lot"),
            ("\U0001F4C4", "spec"),
        ]:
            holder = QWidget()
            holder.setStyleSheet("background:transparent;")
            h = QHBoxLayout(holder)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            gl = QLabel(glyph)
            gl.setStyleSheet(f"color:{_TEXT};font-size:12px;background:transparent;")
            tl = QLabel(label)
            tl.setStyleSheet(f"color:{_TEXT_DIM};font-size:11px;background:transparent;")
            h.addWidget(gl)
            h.addWidget(tl)
            row.addWidget(holder)

        sep = QLabel("\u2502")
        sep.setStyleSheet(f"color:{_TEXT_FAINT};background:transparent;")
        row.addWidget(sep)

        pending = QLabel("Activity (Q\u00b7S\u00b7P\u00b7A\u00b7R\u00b7K): pending quote/SO modules")
        pending.setStyleSheet(
            f"color:{_TEXT_FAINT};font-style:italic;font-size:11px;"
            f"background:transparent;")
        row.addWidget(pending)

        row.addStretch(1)
        return wrap

    def _build_attention_bar(self) -> QWidget:
        """Needs Attention chip bar with AI buttons on the right."""
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame{{background:{_PANEL};border:1px solid {_BORDER};"
            f"border-radius:8px;}}")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(8)

        head = QLabel("⚠ NEEDS ATTENTION")
        head.setStyleSheet(
            f"color:{_AMBER};font-size:10px;font-weight:700;"
            f"letter-spacing:0.8px;background:transparent;")
        row.addWidget(head)

        self._chip_labels: dict[str, QLabel] = {}
        chips = [
            ("out_so",    "Out — open sales order", _RED),
            ("negative",  "Negative on-hand",       _PURPLE),
            ("low",       "Below reorder point",    _AMBER),
            ("no_cost",   "No cost on file",        _TEAL),
            ("no_photo",  "Missing photo",          _BLUE),
            ("price_lt",  "Price < cost",           _RED),
            ("dup_upc",   "Duplicate UPC",          _AMBER),
        ]
        for key, text, color in chips:
            chip = self._make_attn_chip(text, color)
            self._chip_labels[key] = chip
            # Make chip clickable (mockup §4 — attention chips drill into a list)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.mousePressEvent = (
                lambda ev, k=key: self._on_attention_chip_clicked(k)
            )
            row.addWidget(chip)

        row.addStretch()

        # AI helpers were here — moved into the Products "More ▾" overflow
        # menu to declutter the attention bar (mockup alignment).

        return wrap

    def _make_attn_chip(self, text: str, color: str) -> QLabel:
        chip = QLabel(f"●  0   {text}")
        chip.setProperty("_color", color)
        chip.setProperty("_text", text)
        chip.setStyleSheet(
            f"QLabel{{background:{_PANEL2};border:1px solid {_BORDER};"
            f"border-radius:12px;padding:3px 10px;font-size:11px;"
            f"font-weight:600;color:{_TEXT_FAINT};}}")
        return chip

    def _update_chip(self, key: str, count: int) -> None:
        chip = self._chip_labels.get(key)
        if not chip:
            return
        color = chip.property("_color") or _TEXT_DIM
        text = chip.property("_text") or ""
        if count <= 0:
            chip.setText(f"●  0   {text}")
            chip.setStyleSheet(
                f"QLabel{{background:{_PANEL2};border:1px solid {_BORDER};"
                f"border-radius:12px;padding:3px 10px;font-size:11px;"
                f"font-weight:600;color:{_TEXT_FAINT};}}")
        else:
            chip.setText(f"●  {count}   {text}")
            chip.setStyleSheet(
                f"QLabel{{background:{_PANEL2};border:1px solid {color};"
                f"border-radius:12px;padding:3px 10px;font-size:11px;"
                f"font-weight:700;color:{_TEXT};}}")

    def _update_kpis_and_chips(self) -> None:
        """Recompute KPI cards + attention chips from _last_results."""
        if not hasattr(self, "_kpi_labels"):
            return

        rows = self._last_results or []
        active = len(rows)
        stocked = out = low = neg = no_cost = low_marg = tagged = 0
        value = 0.0
        margins: list[float] = []

        for r in rows:
            qty = int(r.get("qty_on_hand", 0) or 0)
            mn = int(r.get("min_qty", 0) or 0)
            cost = float(r.get("pai_cost", 0) or 0) or float(r.get("cost", 0) or 0)
            price = float(r.get("selling_price", 0) or 0)

            if r.get("tagged_for_po"):
                tagged += 1
            if qty < 0:
                neg += 1
            if qty <= 0:
                out += 1
            else:
                stocked += 1
                if mn > 0 and qty < mn:
                    low += 1
            if qty > 0:
                value += qty * cost
            if cost <= 0:
                no_cost += 1
            if price > 0 and cost > 0:
                m = (price - cost) / price * 100
                margins.append(m)
                if m < 10:
                    low_marg += 1

        avg_marg = sum(margins) / len(margins) if margins else 0.0

        def _fmt_money(v: float) -> str:
            if v >= 1_000_000:
                return f"${v/1_000_000:.2f}M"
            if v >= 1_000:
                return f"${v/1_000:.1f}K"
            return f"${v:,.0f}"

        # Extra metrics not in current schema — best-effort approximations
        on_order = sum(int(r.get("on_order_qty", 0) or 0) for r in rows)
        dead = sum(1 for r in rows
                   if int(r.get("qty_on_hand", 0) or 0) > 0
                   and not r.get("last_sold_date"))
        price_lt_cost = sum(
            1 for r in rows
            if float(r.get("selling_price", 0) or 0) > 0
            and float(r.get("selling_price", 0) or 0)
               < float(r.get("pai_cost", 0) or r.get("cost", 0) or 0)
        )
        no_photo = sum(1 for r in rows if not r.get("image_url"))

        self._kpi_labels["active"].setText(f"{active:,}")
        self._kpi_labels["value"].setText(_fmt_money(value))
        self._kpi_labels["low"].setText(f"{low:,}")
        self._kpi_labels["out"].setText(f"{out:,}")
        self._kpi_labels["on_order"].setText(f"{on_order:,}")
        self._kpi_labels["dead"].setText(f"{dead:,}")
        self._kpi_labels["margin"].setText(
            f"{avg_marg:.1f}%" if margins else "—")

        self._update_chip("out_so", out)
        self._update_chip("negative", neg)
        self._update_chip("low", low)
        self._update_chip("no_cost", no_cost)
        self._update_chip("no_photo", no_photo)
        self._update_chip("price_lt", price_lt_cost)
        self._update_chip("dup_upc", 0)  # would need UPC dedup pass

    # ────────────────────────────────────────────
    #  AI BUTTON STUBS (to be wired to real services)
    # ────────────────────────────────────────────
    def _ai_stub(self, feature: str) -> None:
        QMessageBox.information(
            self,
            "AI Feature — Coming Soon",
            f"<b>{feature}</b><br><br>"
            "This AI assistant action is queued for development.<br><br>"
            "Planned integrations:<br>"
            "• PAI / Cummins / Holset vendor scraping<br>"
            "• OEM cross-reference lookup<br>"
            "• Auto-generated descriptions &amp; SEO copy<br>"
            "• Sales-velocity-based reorder suggestions<br>"
            "• Freight class / NMFC code lookup<br>"
            "• Vendor image &amp; spec-sheet retrieval",
        )

    # ────────────────────────────────────────────
    #  KEYBOARD SHORTCUTS
    # ────────────────────────────────────────────
    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self).activated.connect(self._full_refresh)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._focus_search)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._on_edit_selected)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._on_add_product)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self._on_bulk_edit)
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self._on_quick_entry)
        QShortcut(QKeySequence("Ctrl+A"), self._table).activated.connect(self._select_all_visible)
        QShortcut(QKeySequence("Return"), self._table).activated.connect(self._on_open_selected)
        QShortcut(QKeySequence("Space"), self._table).activated.connect(self._toggle_tag_selected)
        QShortcut(QKeySequence("Shift+Space"), self._table).activated.connect(self._toggle_tag_and_advance)
        # Escape: close the detail drawer if it's open.
        QShortcut(QKeySequence("Esc"), self).activated.connect(self._on_escape_pressed)

    def _focus_search(self) -> None:
        self._query.setFocus()
        self._query.selectAll()

    def _toggle_tag_selected(self) -> None:
        """Space bar toggles the Order checkbox on the selected row."""
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, COL_TAG)
        if item is None:
            return
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(new_state)  # triggers _on_cell_changed

    def _toggle_tag_and_advance(self) -> None:
        """Shift+Space — toggle Order checkbox and move down one row (rapid tagging)."""
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, COL_TAG)
        if item is not None:
            new_state = (
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            item.setCheckState(new_state)
        # Advance to next row
        if row + 1 < self._table.rowCount():
            self._table.setCurrentCell(row + 1, self._table.currentColumn())

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """Select the row when clicking in the checkbox column."""
        if col == COL_TAG:
            self._table.selectRow(row)

    # ────────────────────────────────────────────
    #  RIGHT-CLICK CONTEXT MENU
    # ────────────────────────────────────────────
    def _show_context_menu(self, pos) -> None:
        """Build and show the right-click context menu for a product row."""
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        product = self._product_for_row(row)
        if not product:
            return

        self._table.selectRow(row)
        menu = QMenu(self)
        sku = str(product.get("sku", "") or "")
        pid = product.get("id")
        tagged = bool(product.get("tagged_for_po", 0))

        # ── Core ──
        menu.addAction("📝  Edit Product", lambda: self._ctx_edit(sku))
        menu.addAction("📋  Duplicate Product", lambda: self._ctx_duplicate(product))
        menu.addAction("🔍  View Product Details", lambda: self._ctx_view_details(sku))
        menu.addAction("🗑️  Delete Product", lambda: self._ctx_delete(pid, sku))
        menu.addSeparator()

        # ── Sales ──
        sales = menu.addMenu("💰  Sales")
        sales.addAction("Add to Quote", lambda: self._ctx_add_to_quote(product))
        sales.addAction("View Sales History", lambda: self._ctx_sales_history(pid, sku))
        sales.addAction("View Who Bought This", lambda: self._ctx_who_bought(pid, sku))
        menu.addSeparator()

        # ── Cross-Reference / Diesel ──
        xref = menu.addMenu("🔁  Cross References")
        xref.addAction("View Cross References", lambda: self._ctx_view_xrefs(sku))
        xref.addAction("Add Cross Reference", lambda: self._ctx_add_xref(product))
        xref.addAction("Find Related Parts", lambda: self._ctx_find_related(product))
        menu.addSeparator()

        # ── Pricing & Margin ──
        pricing = menu.addMenu("📊  Pricing")
        pricing.addAction("Adjust Price…", lambda: self._ctx_adjust_price(row, product))
        pricing.addAction("Apply Margin %…", lambda: self._ctx_apply_margin(row, product))
        pricing.addSeparator()
        pricing.addAction("Mark for Pricing Review", lambda: self._ctx_mark_pricing_review(row, product))

        # ── Status ──
        status_menu = menu.addMenu("🚦  Status")
        inv_status = str(product.get("inventory_status", "") or "active").strip().lower()

        act_active = status_menu.addAction("🟢 Active (Auto Status)")
        act_active.setCheckable(True)
        act_active.setChecked(inv_status in ("", "active"))
        act_active.triggered.connect(lambda _=False: self._ctx_set_inventory_status(row, product, "active"))

        act_review = status_menu.addAction("🟠 Need Review")
        act_review.setCheckable(True)
        act_review.setChecked(inv_status == "need_review")
        act_review.triggered.connect(lambda _=False: self._ctx_set_inventory_status(row, product, "need_review"))

        act_pricing = status_menu.addAction("🔵 Pricing Review")
        act_pricing.setCheckable(True)
        act_pricing.setChecked(inv_status == "pricing_review")
        act_pricing.triggered.connect(lambda _=False: self._ctx_set_inventory_status(row, product, "pricing_review"))

        act_pre = status_menu.addAction("🟣 Pre-Inventory")
        act_pre.setCheckable(True)
        act_pre.setChecked(inv_status == "pre_inventory")
        act_pre.triggered.connect(lambda _=False: self._ctx_set_inventory_status(row, product, "pre_inventory"))

        menu.addSeparator()

        # ── Inventory ──
        inv = menu.addMenu("📦  Inventory")
        inv.addAction("Adjust Quantity…", lambda: self._ctx_adjust_qty(product))
        inv.addAction("Set Min / Max…", lambda: self._ctx_set_min_max(row, product))
        inv.addAction("View Locations", lambda: self._ctx_view_locations(pid, sku))
        inv.addAction("📊  Stock Movements…",
            lambda: self._open_stock_movements(product))
        tag_label = "Untag for PO" if tagged else "Tag for PO"
        inv.addAction(f"{'📌' if not tagged else '📍'}  {tag_label}", lambda: self._ctx_toggle_tag(row, product))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── Context menu action handlers ──────────────────────

    def _ctx_edit(self, sku: str) -> None:
        from .product_workbench_dialog import ProductWorkbenchDialog
        dlg = ProductWorkbenchDialog(mode="edit", sku=sku, parent=self)
        if dlg.exec():
            self._do_browse()

    def _ctx_view_details(self, sku: str) -> None:
        dlg = ProductDetailDialog(sku=sku, parent=self)
        dlg.exec()
        self._do_browse()

    def _ctx_duplicate(self, product: dict) -> None:
        """Create a copy of a product with a new SKU."""
        from db import create_product
        old_sku = str(product.get("sku", "") or "")
        new_sku, ok = QMessageBox.question(self, "", ""), False  # placeholder
        # Use input dialog for new SKU
        from PySide6.QtWidgets import QInputDialog
        new_sku, ok = QInputDialog.getText(
            self, "Duplicate Product",
            f"Enter SKU for the copy of {old_sku}:",
        )
        if not ok or not new_sku.strip():
            return
        fields = {}
        for key in [
            "title", "category", "manufacturer", "supplier", "engine",
            "condition", "cost", "pai_cost", "selling_price", "core_charge",
            "min_qty", "max_qty", "description_html", "brief_description",
            "invoice_description",
        ]:
            if product.get(key) is not None:
                fields[key] = product[key]
        result = create_product(sku=new_sku.strip(), **fields)
        if result.get("success"):
            QMessageBox.information(self, "Duplicated", f"Created {new_sku.strip()}")
            self._do_browse()
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Unknown error"))

    def _ctx_delete(self, pid: int, sku: str) -> None:
        # Check if multiple rows are checked — offer bulk delete
        checked = self._get_checked_products()
        if len(checked) > 1:
            skus = [str(p.get("sku", "?")) for p in checked]
            preview = "\n".join(f"  • {s}" for s in skus[:10])
            if len(skus) > 10:
                preview += f"\n  … and {len(skus) - 10} more"
            reply = QMessageBox.warning(
                self, "Delete Products",
                f"Delete {len(checked)} checked products?\n\n{preview}\n\n"
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            failed = 0
            for p in checked:
                r = delete_product(p.get("id"))
                if not r.get("success"):
                    failed += 1
            if failed:
                QMessageBox.warning(self, "Partial Failure",
                                    f"{failed} of {len(checked)} deletions failed.")
            self._do_browse()
            return

        # Single product delete
        reply = QMessageBox.warning(
            self, "Delete Product",
            f"Permanently delete {sku}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = delete_product(pid)
        if result.get("success"):
            self._do_browse()
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Delete failed"))

    def _ctx_add_to_quote(self, product: dict) -> None:
        """Open a new quote with this product pre-added."""
        from .quote_dialog import QuoteDialog
        pid = product.get("id")
        sku = str(product.get("sku", "") or "")
        title = str(product.get("title", "") or "")
        price = float(product.get("selling_price", 0) or 0)
        cost = float(product.get("pai_cost", 0) or 0) or float(product.get("cost", 0) or 0)
        core = float(product.get("core_charge", 0) or 0)
        supplier = str(product.get("supplier", "") or "")

        # Create quote in DB first, then open dialog on it
        result = create_quote(
            lines=[{
                "product_id": pid,
                "description": title,
                "supplier": supplier,
                "qty": 1,
                "unit_price": price,
                "unit_cost": cost,
                "core_charge": core,
            }],
        )
        if result.get("success"):
            dlg = QuoteDialog(quote_id=result["quote_id"], parent=self)
            dlg.exec()
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Failed to create quote"))

    def _ctx_sales_history(self, pid: int, sku: str) -> None:
        """Show sales history (inventory transactions) for this product."""
        txns = get_inventory_transactions(product_id=pid, limit=50)
        if not txns:
            QMessageBox.information(self, "Sales History", f"No transactions found for {sku}.")
            return
        lines = [f"<b>Transaction History — {_esc(sku)}</b><br><table border='1' cellpadding='4'>"]
        lines.append("<tr><th>Date</th><th>Type</th><th>Change</th><th>After</th><th>Ref</th><th>Notes</th></tr>")
        for t in txns:
            lines.append(
                f"<tr><td>{_esc(str(t.get('created_at','')))}</td>"
                f"<td>{_esc(str(t.get('transaction_type','')))}</td>"
                f"<td>{t.get('quantity_change',0):+d}</td>"
                f"<td>{t.get('quantity_after','')}</td>"
                f"<td>{_esc(str(t.get('reference','')))}</td>"
                f"<td>{_esc(str(t.get('notes','')))}</td></tr>"
            )
        lines.append("</table>")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Sales History")
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText("".join(lines))
        dlg.exec()

    def _ctx_who_bought(self, pid: int, sku: str) -> None:
        buyers = get_customers_who_bought(pid)
        if not buyers:
            QMessageBox.information(self, "Who Bought This", f"No purchase history for {sku}.")
            return
        lines = [f"<b>Customers who bought {_esc(sku)}</b><br><table border='1' cellpadding='4'>"]
        lines.append("<tr><th>Customer</th><th>Company</th><th>Total Qty</th><th>Last Purchase</th></tr>")
        for b in buyers:
            lines.append(
                f"<tr><td>{_esc(str(b.get('customer_name','')))}</td>"
                f"<td>{_esc(str(b.get('company','')))}</td>"
                f"<td>{b.get('total_qty',0)}</td>"
                f"<td>{_esc(str(b.get('last_date','')))}</td></tr>"
            )
        lines.append("</table>")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Who Bought This")
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText("".join(lines))
        dlg.exec()

    def _ctx_view_xrefs(self, sku: str) -> None:
        xrefs = get_all_xrefs_for_product(sku)
        if not xrefs:
            QMessageBox.information(self, "Cross References", f"No cross-references for {sku}.")
            return
        lines = [f"<b>Cross References — {_esc(sku)}</b><br><table border='1' cellpadding='4'>"]
        lines.append("<tr><th>Part Number</th><th>Type</th><th>Manufacturer</th></tr>")
        for x in xrefs:
            lines.append(
                f"<tr><td>{_esc(str(x.get('number','')))}</td>"
                f"<td>{_esc(str(x.get('type','')))}</td>"
                f"<td>{_esc(str(x.get('manufacturer','')))}</td></tr>"
            )
        lines.append("</table>")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Cross References")
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText("".join(lines))
        dlg.exec()

    def _ctx_add_xref(self, product: dict) -> None:
        """Quick-add a cross reference from the context menu."""
        from .product_workbench_dialog import ProductWorkbenchDialog
        sku = str(product.get("sku", "") or "")
        dlg = ProductWorkbenchDialog(
            mode="edit", sku=sku, initial_section="xref", parent=self,
        )
        dlg.exec()
        self._do_browse()

    def _ctx_find_related(self, product: dict) -> None:
        """Show related parts via cross-reference chains."""
        from db.xref_chain_service import find_related_products
        sku = str(product.get("sku", "") or "")
        # Try OEM part number first, then SKU without JAKS- prefix
        search_pn = str(product.get("oem_part_number", "") or "")
        if not search_pn:
            search_pn = sku.replace("JAKS-", "")
        related = find_related_products(search_pn)
        if not related:
            QMessageBox.information(self, "Related Parts", f"No related parts found for {sku}.")
            return
        lines = [f"<b>Related Parts — {_esc(sku)}</b><br><table border='1' cellpadding='4'>"]
        lines.append("<tr><th>OEM Part</th><th>OEM Mfr</th><th>Aftermarket</th><th>AM Mfr</th><th>Source</th></tr>")
        for r in related[:20]:
            lines.append(
                f"<tr><td>{_esc(str(r.oem_part_number))}</td>"
                f"<td>{_esc(str(r.oem_manufacturer or ''))}</td>"
                f"<td>{_esc(str(r.aftermarket_part_number))}</td>"
                f"<td>{_esc(str(r.aftermarket_manufacturer or ''))}</td>"
                f"<td>{_esc(str(r.source or ''))}</td></tr>"
            )
        lines.append("</table>")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Related Parts")
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText("".join(lines))
        dlg.exec()

    def _ctx_adjust_price(self, row: int, product: dict) -> None:
        """Quick price adjustment dialog."""
        from PySide6.QtWidgets import QInputDialog
        cur_price = float(product.get("selling_price", 0) or 0)
        new_price, ok = QInputDialog.getDouble(
            self, "Adjust Price",
            f"New selling price for {product.get('sku', '')}:",
            cur_price, 0, 999999, 2,
        )
        if not ok:
            return
        pid = product.get("id")
        update_product(pid, selling_price=new_price)
        product["selling_price"] = new_price
        self._table.blockSignals(True)
        pi = self._table.item(row, COL_PRICE)
        if pi:
            pi.setText(f"{new_price:,.2f}")
        self._table.blockSignals(False)
        self._refresh_margin_cell(row, product)

    def _ctx_apply_margin(self, row: int, product: dict) -> None:
        """Calculate price from cost + target margin %."""
        from PySide6.QtWidgets import QInputDialog
        cost = float(product.get("pai_cost", 0) or 0) or float(product.get("cost", 0) or 0)
        if cost <= 0:
            QMessageBox.warning(self, "Apply Margin", "Cost is zero — cannot calculate margin-based price.")
            return
        margin, ok = QInputDialog.getDouble(
            self, "Apply Margin %",
            f"Target margin % for {product.get('sku', '')} (cost: ${cost:,.2f}):",
            35.0, 0, 99.9, 1,
        )
        if not ok:
            return
        new_price = round(cost / (1 - margin / 100), 2)
        pid = product.get("id")
        update_product(pid, selling_price=new_price)
        product["selling_price"] = new_price
        self._table.blockSignals(True)
        pi = self._table.item(row, COL_PRICE)
        if pi:
            pi.setText(f"{new_price:,.2f}")
        self._table.blockSignals(False)
        self._refresh_margin_cell(row, product)
        QMessageBox.information(
            self, "Margin Applied",
            f"Price set to ${new_price:,.2f} ({margin:.1f}% margin on ${cost:,.2f} cost)",
        )

    def _ctx_mark_pricing_review(self, row: int, product: dict) -> None:
        """Mark product for pricing review so it stands out in the Status column."""
        self._ctx_set_inventory_status(row, product, "pricing_review")

    def _ctx_set_inventory_status(self, row: int, product: dict, new_status: str) -> None:
        """Set manual inventory status (active / pricing_review / need_review / pre_inventory)."""
        pid = product.get("id")
        if pid is None:
            return
        try:
            update_product(pid, inventory_status=new_status)
            product["inventory_status"] = new_status
            self._refresh_status_cell(row, product)
        except Exception as e:
            QMessageBox.warning(self, "Status", f"Failed to update status: {e}")

    def _ctx_adjust_qty(self, product: dict) -> None:
        """Open the Adjust Quantity dialog."""
        from .adjust_qty_dialog import AdjustQtyDialog
        sku = str(product.get("sku", "") or "")
        dlg = AdjustQtyDialog(sku=sku, parent=self)
        if dlg.exec():
            self._do_browse()

    def _ctx_set_min_max(self, row: int, product: dict) -> None:
        """Quick min/max dialog."""
        from PySide6.QtWidgets import QInputDialog
        pid = product.get("id")
        cur_min = int(product.get("min_qty", 0) or 0)
        cur_max = int(product.get("max_qty", 0) or 0)
        new_min, ok = QInputDialog.getInt(
            self, "Set Min", f"Minimum stock for {product.get('sku', '')}:", cur_min, 0, 99999,
        )
        if not ok:
            return
        new_max, ok = QInputDialog.getInt(
            self, "Set Max", f"Maximum stock for {product.get('sku', '')}:", cur_max, 0, 99999,
        )
        if not ok:
            return
        update_product(pid, min_qty=new_min, max_qty=new_max)
        product["min_qty"] = new_min
        product["max_qty"] = new_max
        self._table.blockSignals(True)
        mi = self._table.item(row, COL_MIN)
        if mi:
            mi.setText(str(new_min))
        mx = self._table.item(row, COL_MAX)
        if mx:
            mx.setText(str(new_max))
        self._table.blockSignals(False)
        self._refresh_status_cell(row, product)
        self._refresh_suggested_cell(row, product)

    def _ctx_view_locations(self, pid: int, sku: str) -> None:
        locs = get_product_locations(pid)
        if not locs:
            QMessageBox.information(self, "Locations", f"No locations assigned for {sku}.")
            return
        lines = [f"<b>Locations — {_esc(sku)}</b><br><table border='1' cellpadding='4'>"]
        lines.append("<tr><th>Location</th><th>Zone</th><th>Qty</th><th>Primary</th></tr>")
        for loc in locs:
            name = str(loc.get("name", "") or loc.get("code", ""))
            lines.append(
                f"<tr><td>{_esc(name)}</td>"
                f"<td>{_esc(str(loc.get('zone','')))}</td>"
                f"<td>{loc.get('quantity', 0)}</td>"
                f"<td>{'✓' if loc.get('is_primary') else ''}</td></tr>"
            )
        lines.append("</table>")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Inventory Locations")
        dlg.setTextFormat(Qt.TextFormat.RichText)
        dlg.setText("".join(lines))
        dlg.exec()

    def _ctx_toggle_tag(self, row: int, product: dict) -> None:
        """Toggle the tagged_for_po flag on a product."""
        item = self._table.item(row, COL_TAG)
        if item is None:
            return
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(new_state)  # triggers _on_cell_changed

    # ────────────────────────────────────────────
    #  SELECT-ALL CHECKBOX HELPERS
    # ────────────────────────────────────────────
    def _reposition_select_all_cb(self, *_args) -> None:
        """Keep the select-all checkbox centred in the COL_TAG header cell."""
        hdr = self._table.horizontalHeader()
        # Use the viewport position so horizontal scrolling doesn't push the
        # checkbox off-screen.
        x = hdr.sectionViewportPosition(COL_TAG)
        w = hdr.sectionSize(COL_TAG)
        cb_w = self._select_all_cb.sizeHint().width()
        cb_h = self._select_all_cb.sizeHint().height()
        self._select_all_cb.setGeometry(
            max(0, x + (w - cb_w) // 2),
            max(0, (hdr.height() - cb_h) // 2),
            cb_w,
            cb_h,
        )
        self._select_all_cb.raise_()

    def _on_select_all_toggled(self, state: int) -> None:
        """Toggle all visible row checkboxes when select-all is clicked."""
        checked = state == Qt.CheckState.Checked.value
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_TAG)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                # Also update the product dict + DB
                product = self._product_for_row(row)
                if product:
                    pid = product.get("id")
                    try:
                        update_product(pid, tagged_for_po=int(checked))
                        product["tagged_for_po"] = int(checked)
                    except Exception:
                        pass
                    self._style_tagged_row(row, checked)
        self._table.blockSignals(False)
        self._recount_tagged()
        self._update_checked_selection()

    def _get_checked_products(self) -> list[dict]:
        """Return product dicts for all rows with checked checkboxes."""
        products = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_TAG)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                p = self._product_for_row(row)
                if p:
                    products.append(p)
        return products

    def _update_checked_selection(self) -> None:
        """Enable bulk edit if enough rows are checked OR selected."""
        checked = self._get_checked_products()
        selected = self._table.selectionModel().selectedRows()
        # Use whichever is larger
        n = max(len(checked), len(selected))
        self._bulk_btn.setEnabled(n >= 2)
        if n >= 2:
            self._bulk_btn.setText(f"✏️ Bulk Edit ({n})")
        else:
            self._bulk_btn.setText("✏️ Bulk Edit")

    def _toggle_recent(self) -> None:
        """Toggle the recent activity panel visibility."""
        visible = not self._recent_table.isVisible()
        self._recent_table.setVisible(visible)
        if visible:
            self._recent_toggle.setText("▼ Recent Activity")
            self._recent_frame.setMaximumHeight(130)
        else:
            self._recent_toggle.setText("▶ Recent Activity")
            self._recent_frame.setMaximumHeight(28)

    # ────────────────────────────────────────────
    #  DETAIL DRAWER (right-side slide-over)
    # ────────────────────────────────────────────
    def _toggle_detail_drawer(self) -> None:
        self._detail_expanded = not self._detail_expanded
        self._detail_browser.setVisible(self._detail_expanded)
        if self._detail_expanded:
            self._detail_toggle.setText("▼ Product Detail")
            self._show_detail_drawer()
        else:
            self._detail_toggle.setText("▶ Product Detail")
            self._hide_detail_drawer()

    def _show_detail_drawer(self) -> None:
        """No-op: the right-hand drawer was removed per the redesign mockup."""
        return None
        # Legacy implementation kept below (unreachable) for reference.
        self._detail_panel.show()
        try:
            total = sum(self._splitter.sizes()) or self._splitter.width() or 1200
            drawer = max(380, int(total * 0.3))
            self._splitter.setSizes([total - drawer, drawer])
        except Exception:
            pass

    def _on_escape_pressed(self) -> None:
        """Esc closes the drawer if open, otherwise clears the search box.

        Order of priority is intentional: most users will hit Esc to dismiss
        the detail panel they just opened by clicking a row.
        """
        try:
            sizes = self._splitter.sizes()
            if len(sizes) >= 2 and sizes[1] > 0:
                self._hide_detail_drawer()
                return
        except Exception:
            pass
        if self._query.text():
            self._query.clear()

    def _hide_detail_drawer(self) -> None:
        """Collapse the drawer."""
        try:
            total = sum(self._splitter.sizes()) or self._splitter.width() or 1200
            self._splitter.setSizes([total, 0])
        except Exception:
            pass
        self._detail_panel.hide()

    def _populate_detail_drawer(self, product: dict[str, Any]) -> None:
        """Build HTML summary card for the selected product."""
        pid = product.get("id")
        sku = product.get("sku", "")
        title = product.get("title", "") or product.get("description", "") or ""

        # ── Status badge ──
        qty = int(product.get("qty_on_hand", 0) or 0)
        min_q = int(product.get("min_qty", 0) or 0)
        max_q = int(product.get("max_qty", 0) or 0)
        tagged = bool(product.get("tagged_for_po", 0))
        if tagged:
            badge_text, badge_bg = "Tagged for PO", "#e65100"
        else:
            status_text, status_color = self._compute_status_display(product)
            # Strip icon for compact badge text.
            badge_text = status_text.split(" ", 1)[1] if " " in status_text else status_text
            badge_bg = status_color.name()

        # ── Values ──
        mfr = product.get("manufacturer", "") or ""
        supplier = product.get("supplier", "") or ""
        category = product.get("category", "") or ""
        subcategory = product.get("subcategory", "") or ""
        condition = product.get("condition", "") or ""
        price = float(product.get("selling_price", 0) or product.get("price", 0) or 0)
        cost = float(product.get("pai_cost", 0) or product.get("cost", 0) or 0)
        margin = ((price - cost) / price * 100) if price > 0 and cost > 0 else 0
        vendor_sku = product.get("pai_sku", "") or product.get("vendor_sku", "") or ""
        oem_part = product.get("oem_part_number", "") or ""
        notes = product.get("notes", "") or ""
        engine = product.get("engine", "") or ""

        css = (
            "body { font-family: 'Segoe UI', sans-serif; font-size: 12px; margin: 0; padding: 0; color: #333; }"
            ".card { border: 1px solid #ddd; border-radius: 6px; padding: 10px 14px; background: #fff; }"
            ".hdr { display: flex; align-items: center; gap: 10px; padding-bottom: 8px; "
            "border-bottom: 1px solid #e0e0e0; margin-bottom: 8px; }"
            ".hdr-title { font-size: 14px; font-weight: bold; color: #222; }"
            ".hdr-sub { font-size: 11px; color: #666; }"
            ".badge { display: inline-block; font-size: 10px; font-weight: bold; color: white; "
            "padding: 2px 8px; border-radius: 10px; }"
            ".cols { display: flex; gap: 32px; flex-wrap: wrap; }"
            ".col { min-width: 160px; }"
            ".row { margin: 3px 0; font-size: 12px; }"
            ".row b { color: #555; font-weight: 600; }"
            ".row .val { color: #111; }"
            ".money { font-family: Consolas, monospace; }"
            ".margin-good { color: #2e7d32; font-weight: bold; }"
            ".margin-low { color: #e65100; font-weight: bold; }"
            ".margin-neg { color: #c62828; font-weight: bold; }"
            ".section-title { font-weight: 600; color: #444; margin: 8px 0 4px; font-size: 12px; "
            "border-top: 1px solid #eee; padding-top: 6px; }"
            ".xref-tag { display: inline-block; background: #eef2ee; border: 1px solid #ccd; "
            "border-radius: 3px; padding: 1px 6px; margin: 1px 2px; font-size: 11px; }"
            ".note { background: #fafafa; border-left: 3px solid #4a6741; padding: 4px 8px; "
            "margin: 4px 0; font-size: 11px; color: #555; }"
        )

        h = [f"<style>{css}</style>", '<div class="card">']

        # ── Header with status badge ──
        h.append('<div class="hdr">')
        h.append(f'<div><span class="hdr-title">{_esc(sku)}</span>')
        h.append(f'<br><span class="hdr-sub">{_esc(title)}</span></div>')
        h.append(f'<span class="badge" style="background:{badge_bg};">{badge_text}</span>')
        h.append('</div>')

        # ── Two-column grid ──
        h.append('<div class="cols">')

        # Left column — identity
        h.append('<div class="col">')
        if mfr:
            h.append(f'<div class="row"><b>Manufacturer:</b> <span class="val">{_esc(mfr)}</span></div>')
        if supplier:
            h.append(f'<div class="row"><b>Supplier:</b> <span class="val">{_esc(supplier)}</span></div>')
        cat_display = _esc(category)
        if subcategory:
            cat_display += f" › {_esc(subcategory)}"
        if category:
            h.append(f'<div class="row"><b>Category:</b> <span class="val">{cat_display}</span></div>')
        if condition:
            h.append(f'<div class="row"><b>Condition:</b> <span class="val">{_esc(condition)}</span></div>')
        if engine:
            h.append(f'<div class="row"><b>Engine:</b> <span class="val">{_esc(engine)}</span></div>')
        if vendor_sku:
            h.append(f'<div class="row"><b>Vendor SKU:</b> <span class="val">{_esc(vendor_sku)}</span></div>')
        if oem_part:
            h.append(f'<div class="row"><b>OEM Part #:</b> <span class="val">{_esc(oem_part)}</span></div>')
        h.append('</div>')

        # Right column — inventory & pricing
        h.append('<div class="col">')
        h.append(f'<div class="row"><b>Qty on Hand:</b> <span class="val">{qty}</span></div>')
        h.append(f'<div class="row"><b>Min / Max:</b> <span class="val">{min_q} / {max_q}</span></div>')
        if max_q > 0 and qty < max_q:
            suggested = max_q - qty
            h.append(f'<div class="row"><b>Suggested Order:</b> <span class="val">{suggested}</span></div>')
        h.append(f'<div class="row"><b>Price:</b> <span class="val money">${price:,.2f}</span></div>')
        h.append(f'<div class="row"><b>Cost:</b> <span class="val money">${cost:,.2f}</span></div>')
        if price > 0 and cost > 0:
            m_cls = "margin-good" if margin >= 30 else ("margin-low" if margin >= 15 else "margin-neg")
            h.append(f'<div class="row"><b>Margin:</b> <span class="{m_cls}">{margin:.1f}%</span></div>')
        h.append('</div>')

        h.append('</div>')  # end cols

        # ── Notes ──
        if notes:
            h.append(f'<div class="note">{_esc(notes)}</div>')

        # ── Interchanges ──
        try:
            xrefs = get_all_xrefs_for_product(sku) if sku else []
            if pid:
                interchanges = get_interchanges(pid)
                existing = {str(x.get("number", "")).upper() for x in xrefs}
                for ic in interchanges:
                    num = str(ic.get("interchange_number", "") or "")
                    if num.upper() not in existing:
                        xrefs.append({
                            "number": num,
                            "type": ic.get("interchange_type", ""),
                            "manufacturer": ic.get("manufacturer", "") or "",
                        })
                        existing.add(num.upper())
        except Exception:
            xrefs = []

        if xrefs:
            parts = []
            for x in xrefs[:20]:
                num = x.get("number", "") or x.get("interchange_number", "")
                xtype = x.get("type", "") or x.get("interchange_type", "")
                mfr_x = x.get("manufacturer", "")
                tip = ""
                if xtype:
                    tip += _esc(xtype)
                if mfr_x:
                    tip += f" - {_esc(mfr_x)}" if tip else _esc(mfr_x)
                title_attr = f' title="{tip}"' if tip else ""
                parts.append(f'<span class="xref-tag"{title_attr}>{_esc(num)}</span>')
            more = f" … +{len(xrefs) - 20} more" if len(xrefs) > 20 else ""
            h.append(
                f'<div class="section-title">Interchanges ({len(xrefs)})</div>'
                + "".join(parts) + more
            )

        # ── Locations ──
        try:
            locations = get_product_locations(pid) if pid else []
        except Exception:
            locations = []

        if locations:
            loc_parts = [_esc(loc.get("location_code", "") or loc.get("code", "") or loc.get("name", "")) for loc in locations]
            h.append(f'<div class="section-title">Locations</div>' + ", ".join(loc_parts))

        # ── Last scraped ──
        try:
            last_run = get_last_scrape_run(pid) if pid else None
        except Exception:
            last_run = None

        if last_run:
            run_at = last_run.get("created_at", "") or ""
            status = last_run.get("status", "") or ""
            h.append(
                f'<div style="color:#888;font-size:10px;margin-top:6px;">'
                f'Last scraped: {_esc(str(run_at))} ({_esc(status)})</div>'
            )

        h.append('</div>')  # end card
        self._detail_browser.setHtml("\n".join(h))
        self._detail_toggle.setText(f"▼ Product Detail — {_esc(sku)}")
        # Auto-slide the drawer open on row click (mockup behavior)
        self._detail_expanded = True
        self._detail_browser.setVisible(True)
        self._show_detail_drawer()

    # ────────────────────────────────────────────
    #  GROUP BY
    # ────────────────────────────────────────────
    def _on_group_changed(self) -> None:
        self._apply_stock_filter_and_populate()

    def _apply_grouping(self) -> None:
        """If a group-by field is selected, insert separator header rows."""
        field = self._group_combo.currentData() or ""
        if not field or not self._displayed_results:
            return

        # Determine sort key
        key_map = {
            "supplier": "supplier",
            "category": "category",
            "manufacturer": "manufacturer",
        }
        db_key = key_map.get(field)
        if not db_key:
            return

        # Build group → rows mapping
        groups: dict[str, list[int]] = {}
        for row in range(self._table.rowCount()):
            product = self._product_for_row(row)
            if product is None:
                continue
            val = str(product.get(db_key, "") or "") or "(None)"
            groups.setdefault(val, []).append(row)

        if len(groups) <= 1:
            return

        # Sort groups alphabetically, insert separator rows top-down
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)

        sorted_groups = sorted(groups.keys())
        # Collect all rows in group order
        ordered_pids: list[int] = []
        for g in sorted_groups:
            for row in groups[g]:
                product = self._product_for_row(row)
                if product:
                    ordered_pids.append(product["id"])

        # Rebuild table with group headers
        results_by_group: list[tuple[str, list[dict]]] = []
        for g in sorted_groups:
            group_products = []
            for row in groups[g]:
                product = self._product_for_row(row)
                if product:
                    group_products.append(product)
            results_by_group.append((g, group_products))

        # Count total rows needed: product rows + header rows
        total = sum(len(prods) for _, prods in results_by_group) + len(results_by_group)
        self._table.setRowCount(total)

        insert_row = 0
        header_bg = QColor("#e3f2fd")
        header_fg = QColor("#1565c0")

        for group_name, products in results_by_group:
            # Insert group header row
            header_text = f"  ── {group_name} ({len(products)}) ──"
            for col in range(NUM_COLS):
                hi = QTableWidgetItem(header_text if col == 0 else "")
                hi.setFlags(Qt.ItemFlag.ItemIsEnabled)
                hi.setBackground(header_bg)
                hi.setForeground(header_fg)
                if col == 0:
                    f = hi.font()
                    f.setBold(True)
                    hi.setFont(f)
                self._table.setItem(insert_row, col, hi)
            self._table.setSpan(insert_row, 0, 1, NUM_COLS)
            insert_row += 1

            # Re-populate product rows from stored data
            for product in products:
                self._fill_product_row(insert_row, product)
                insert_row += 1

        self._table.setSortingEnabled(False)  # Keep sorting off when grouped
        self._table.blockSignals(False)

    def _on_view_mode_changed(self) -> None:
        self._apply_view_mode(self._view_combo.currentData() or "browse")

    def _apply_view_mode(self, mode: str) -> None:
        visible_cols = VIEW_PRESETS.get(mode, VIEW_PRESETS["browse"])
        for col in range(NUM_COLS):
            self._set_column_visible(col, col in visible_cols)
        self._sync_column_actions()

    def _set_column_visible(self, col: int, visible: bool) -> None:
        self._table.setColumnHidden(col, not visible)

    def _sync_column_actions(self) -> None:
        for col, action in self._column_actions.items():
            action.blockSignals(True)
            action.setChecked(not self._table.isColumnHidden(col))
            action.blockSignals(False)

    def _build_columns_menu(self) -> None:
        self._columns_menu.clear()
        self._column_actions.clear()

        for col, label in enumerate(HEADERS):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(not self._table.isColumnHidden(col))
            action.toggled.connect(lambda checked, c=col: self._set_column_visible(c, checked))
            self._columns_menu.addAction(action)
            self._column_actions[col] = action

        self._columns_menu.addSeparator()
        save_action = QAction("Save current layout as default", self)
        save_action.triggered.connect(self._save_column_layout_default)
        self._columns_menu.addAction(save_action)

        reset_action = QAction("Reset to current view mode", self)
        reset_action.triggered.connect(lambda: self._apply_view_mode(self._view_combo.currentData() or "browse"))
        self._columns_menu.addAction(reset_action)

    def _save_header_state(self) -> None:
        """Persist horizontal-header widths/order/visibility so manual
        column resizes & reordering survive across launches."""
        try:
            blob = bytes(self._table.horizontalHeader().saveState().toBase64()).decode("ascii")
            set_setting("products_header_state", blob)
        except Exception:
            pass

    def _save_column_layout_default(self) -> None:
        visible_cols = [i for i in range(NUM_COLS) if not self._table.isColumnHidden(i)]
        payload = {
            "mode": self._view_combo.currentData() or "browse",
            "visible": visible_cols,
        }
        try:
            set_setting("products_columns_layout", json.dumps(payload))
            QMessageBox.information(self, "Columns", "Current layout saved as default.")
        except Exception as e:
            QMessageBox.warning(self, "Columns", f"Failed to save layout: {e}")

    def _load_saved_column_layout(self) -> None:
        raw = get_setting("products_columns_layout")
        if not raw:
            self._apply_view_mode("browse")
            return
        try:
            data = json.loads(raw)
            mode = data.get("mode", "browse")
            visible = set(int(c) for c in data.get("visible", []))
            idx = self._view_combo.findData(mode)
            if idx >= 0:
                self._view_combo.blockSignals(True)
                self._view_combo.setCurrentIndex(idx)
                self._view_combo.blockSignals(False)
            if visible:
                for col in range(NUM_COLS):
                    self._set_column_visible(col, col in visible)
            else:
                self._apply_view_mode(mode)
            self._sync_column_actions()
        except Exception:
            self._apply_view_mode("browse")

    def _set_stock_filter(self, key: str) -> None:
        for i in range(self._stock_combo.count()):
            if self._stock_combo.itemData(i) == key:
                self._stock_combo.setCurrentIndex(i)
                return

    # ────────────────────────────────────────────
    #  SYNC STATUS
    # ────────────────────────────────────────────
    def _update_sync_status(self) -> None:
        try:
            status = get_sync_status()
            pending = status.get("needs_update", 0)
            if pending > 0:
                self._sync_status_label.setText(f"⚠️ {pending} pending Shopify update")
                self._sync_status_label.setStyleSheet(
                    "padding: 4px 8px; border-radius: 4px; "
                    "background-color: #fff3cd; color: #856404; font-weight: bold;"
                )
            else:
                total = status.get("synced_products", 0)
                self._sync_status_label.setText(f"✅ {total} synced")
                self._sync_status_label.setStyleSheet(
                    "padding: 4px 8px; border-radius: 4px; "
                    "background-color: #d4edda; color: #155724;"
                )
        except Exception:
            self._sync_status_label.setText("")

    # ────────────────────────────────────────────
    #  RECENT TRANSACTIONS
    # ────────────────────────────────────────────
    def _load_recent_transactions(self) -> None:
        try:
            recent = get_inventory_transactions(limit=6)
        except Exception:
            recent = []

        self._recent_table.setRowCount(len(recent))
        for row_idx, txn in enumerate(recent):
            created = str(txn.get("created_at", "") or "")[:16]
            sku = str(txn.get("sku", "") or "")
            txn_type = str(txn.get("transaction_type", "") or "")
            change = int(txn.get("quantity_change", 0) or 0)
            change_str = f"+{change}" if change > 0 else str(change)
            reference = str(txn.get("reference", "") or "")
            notes = str(txn.get("notes", "") or "")

            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._recent_table.setItem(row_idx, 0, item(created))
            self._recent_table.setItem(row_idx, 1, item(sku))
            self._recent_table.setItem(row_idx, 2, item(txn_type))

            ci = item(change_str)
            if change > 0:
                ci.setForeground(QColor("green"))
            elif change < 0:
                ci.setForeground(QColor("red"))
            self._recent_table.setItem(row_idx, 3, ci)
            self._recent_table.setItem(row_idx, 4, item(f"{reference} {notes}".strip()))
        self._recent_table.resizeColumnsToContents()

    # ────────────────────────────────────────────
    #  FILTERS
    # ────────────────────────────────────────────
    def _load_filters(self) -> None:
        """Load filter options and refresh the grid."""
        self._mfr_combo.blockSignals(True)
        self._cat_combo.blockSignals(True)
        self._supplier_combo.blockSignals(True)
        try:
            while self._mfr_combo.count() > 1:
                self._mfr_combo.removeItem(1)
            while self._cat_combo.count() > 1:
                self._cat_combo.removeItem(1)
            while self._supplier_combo.count() > 1:
                self._supplier_combo.removeItem(1)

            for mfr in get_all_manufacturers():
                self._mfr_combo.addItem(mfr, mfr)
            for cat in get_all_categories():
                self._cat_combo.addItem(cat, cat)
            for sup in get_all_suppliers():
                self._supplier_combo.addItem(sup, sup)
        except Exception as e:
            self._results_label.setText(f"Error loading filters: {e}")
        finally:
            self._mfr_combo.blockSignals(False)
            self._cat_combo.blockSignals(False)
            self._supplier_combo.blockSignals(False)

        # Always populate the grid after loading filters
        self._do_browse()

    def _full_refresh(self) -> None:
        """Full refresh: reload filters + data + recent."""
        self._load_filters()
        self._load_recent_transactions()
        self._update_sync_status()

    def _on_filter_changed(self) -> None:
        self._do_browse()

    # ────────────────────────────────────────────
    #  SEARCH / BROWSE
    # ────────────────────────────────────────────
    def _on_search_text_changed(self, _text: str) -> None:
        """Restart debounce timer on each keystroke."""
        self._search_timer.start()

    def _on_search(self) -> None:
        self._search_timer.stop()  # Cancel pending debounce if manual trigger
        self._do_browse()

    def _on_clear_search(self) -> None:
        """Clear search box and all filters, refresh to show all products."""
        self._query.clear()
        self._mfr_combo.setCurrentIndex(0)
        self._cat_combo.setCurrentIndex(0)
        self._supplier_combo.setCurrentIndex(0)
        self._stock_combo.setCurrentIndex(0)
        self._do_browse()

    def _do_browse(self) -> None:
        mfr = self._mfr_combo.currentData() or None
        cat = self._cat_combo.currentData() or None
        sup = self._supplier_combo.currentData() or None
        search = (self._query.text() or "").strip() or None

        self._table.setRowCount(0)
        self._last_results = []

        try:
            results = browse_products(
                manufacturer=mfr,
                category=cat,
                supplier=sup,
                search=search,
                limit=500,
            )
        except Exception as e:
            self._results_label.setText(f"Browse failed: {e}")
            return

        self._last_results = list(results or [])
        self._apply_stock_filter_and_populate()

    def _apply_stock_filter_and_populate(self) -> None:
        """Apply the stock status filter, then populate."""
        stock_filter = self._stock_combo.currentData() or "all"
        if stock_filter == "all":
            filtered = self._last_results
        elif stock_filter == "in_stock":
            filtered = [r for r in self._last_results if int(r.get("qty_on_hand", 0) or 0) > 0]
        elif stock_filter == "low":
            filtered = []
            for r in self._last_results:
                qty = int(r.get("qty_on_hand", 0) or 0)
                mn = int(r.get("min_qty", 0) or 0)
                if qty > 0 and mn > 0 and qty < mn:
                    filtered.append(r)
                elif qty > 0 and mn == 0 and qty <= 5:
                    filtered.append(r)
        elif stock_filter == "out":
            filtered = [r for r in self._last_results if int(r.get("qty_on_hand", 0) or 0) <= 0]
        elif stock_filter == "pricing_review":
            filtered = [
                r for r in self._last_results
                if str(r.get("inventory_status", "") or "").strip().lower() == "pricing_review"
            ]
        elif stock_filter == "need_review":
            filtered = [
                r for r in self._last_results
                if str(r.get("inventory_status", "") or "").strip().lower() == "need_review"
            ]
        elif stock_filter == "tagged":
            filtered = [r for r in self._last_results if r.get("tagged_for_po")]
        else:
            filtered = self._last_results

        self._displayed_results = filtered

        # Build status text
        parts = []
        mfr = self._mfr_combo.currentData() or ""
        cat = self._cat_combo.currentData() or ""
        sup = self._supplier_combo.currentData() or ""
        if mfr:
            parts.append(f"Mfr: {mfr}")
        if cat:
            parts.append(f"Cat: {cat}")
        if sup:
            parts.append(f"Supplier: {sup}")
        if stock_filter != "all":
            parts.append(f"Stock: {self._stock_combo.currentText()}")
        label = " | ".join(parts) if parts else "All products"
        showing = len(filtered)
        total = len(self._last_results)
        if showing != total:
            self._results_label.setText(f"{label} — {showing} shown (of {total})")
        else:
            self._results_label.setText(f"{label} — {total} results")

        self._update_summary_strip()
        self._populate_table(filtered)
        self._apply_grouping()

    # ────────────────────────────────────────────
    #  POPULATE TABLE
    # ────────────────────────────────────────────
    def _populate_table(self, results: list[dict[str, Any]]) -> None:
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(results))

        # Enrich with sales stats (single batch query)
        pids = [r["id"] for r in results if r.get("id")]
        try:
            sales_map = get_sales_summary_batch(pids) if pids else {}
        except Exception:
            sales_map = {}
        for r in results:
            pid = r.get("id")
            info = sales_map.get(pid, {})
            r["_last_sold"] = info.get("last_sold") or ""
            r["_times_sold_12m"] = info.get("qty_12m", 0)

        # Cache "on order" quantities for the visible page (best-effort)
        self._on_order_map = {}
        try:
            from db import get_on_order_qty_batch  # type: ignore
            if pids:
                self._on_order_map = get_on_order_qty_batch(pids) or {}
        except Exception:
            self._on_order_map = {}

        # Build id → product lookup for sort-safe access
        self._product_map = {r["id"]: r for r in results if "id" in r}

        tagged_count = 0

        for row_idx, r in enumerate(results):
            if r.get("tagged_for_po"):
                tagged_count += 1
            self._fill_product_row(row_idx, r)

        self._table.resizeColumnsToContents()
        # Keep checkbox column slim after resize
        self._table.setColumnWidth(COL_TAG, 50)
        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)

        # Update tagged count badge
        self._update_tagged_count(tagged_count)

    def _fill_product_row(self, row_idx: int, r: dict[str, Any]) -> None:
        """Fill a single table row with product data."""
        sku = str(r.get("sku", "") or "")
        title = str(r.get("title", "") or "")
        manufacturer = str(r.get("manufacturer", "") or "")
        engine = str(r.get("engine", "") or "")
        truck_model = str(r.get("truck_model", "") or "")
        supplier = str(r.get("supplier", "") or "")
        category = str(r.get("category", "") or "")
        qty = int(r.get("qty_on_hand", 0) or 0)
        min_qty = int(r.get("min_qty", 0) or 0)
        max_qty = int(r.get("max_qty", 0) or 0)
        price = float(r.get("selling_price", 0) or 0)
        cost = float(r.get("pai_cost", 0) or 0) or float(r.get("cost", 0) or 0)
        match_part = str(r.get("matched_part", "") or "")
        tagged = bool(r.get("tagged_for_po", 0))

        # Suggested order: max - qty (only if max is set)
        if max_qty > 0 and qty < max_qty:
            suggested = max_qty - qty
        else:
            suggested = 0

        status_text, status_color = self._compute_status_display(r)

        def text_item(text: str) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return it

        # Col 0: Select checkbox — store product ID for sort-safe lookup
        tag_item = QTableWidgetItem()
        tag_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        tag_item.setCheckState(Qt.CheckState.Checked if tagged else Qt.CheckState.Unchecked)
        tag_item.setData(Qt.ItemDataRole.UserRole, r.get("id"))
        self._table.setItem(row_idx, COL_TAG, tag_item)

        # Col 1: Status badge
        si = text_item(status_text)
        si.setForeground(status_color)
        self._table.setItem(row_idx, COL_STATUS, si)

        # Col 2-6: Text columns
        sku_cell = text_item(sku)
        # Last-edited tooltip (mockup §4)
        _edit_at = r.get("local_modified_at") or r.get("updated_at") or ""
        if _edit_at:
            sku_cell.setToolTip(f"Last edited: {_edit_at}")
        self._table.setItem(row_idx, COL_SKU, sku_cell)
        try:
            _core_charge = float(r.get("core_charge", 0) or 0)
            _core_sell = float(r.get("core_sell_price", 0) or 0)
        except Exception:
            _core_charge = _core_sell = 0.0
        # Sub-line for the 2-line Item cell (OEM · mfr/engine · condition).
        sub_bits: list[str] = []
        oem = (r.get("oem_part_number") or r.get("oem_number") or "").strip()
        if oem:
            sub_bits.append(f"OEM {oem}")
        if engine:
            sub_bits.append(str(engine))
        elif truck_model:
            sub_bits.append(str(truck_model))
        cond = (r.get("condition") or "").strip()
        if cond and cond.lower() not in ("new",):
            sub_bits.append(cond)
        sub_text = " · ".join(sub_bits)

        display_title = title
        if _core_charge > 0 or _core_sell > 0:
            display_title = f"\U0001F504 {title}"
        title_item = text_item(display_title)
        if sub_text:
            title_item.setData(_SUBTEXT_ROLE, sub_text)
        if _core_charge > 0 or _core_sell > 0:
            _amt = _core_sell if _core_sell > 0 else _core_charge
            title_item.setToolTip(f"Has core charge: ${_amt:,.2f}")
        self._table.setItem(row_idx, COL_TITLE, title_item)
        platform_text = manufacturer
        if engine:
            platform_text = f"{manufacturer} / {engine}" if manufacturer else engine
        elif truck_model:
            platform_text = f"{manufacturer} / {truck_model}" if manufacturer else truck_model
        self._table.setItem(row_idx, COL_MFR, text_item(platform_text))

        # Supplier — EDITABLE inline
        sup_item = QTableWidgetItem(supplier)
        sup_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        sup_item.setToolTip("Double-click to edit supplier")
        self._table.setItem(row_idx, COL_SUPPLIER, sup_item)

        # Category — EDITABLE inline
        cat_item = QTableWidgetItem(category)
        cat_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        cat_item.setToolTip("Double-click to edit category")
        self._table.setItem(row_idx, COL_CATEGORY, cat_item)

        # Col 7: Qty
        self._table.setItem(row_idx, COL_QTY, _NumericItem(qty))

        # Col 8-9: Min/Max — EDITABLE inline
        min_item = QTableWidgetItem(str(min_qty))
        min_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        min_item.setToolTip("Click to edit minimum stock level")
        self._table.setItem(row_idx, COL_MIN, min_item)

        max_item = QTableWidgetItem(str(max_qty))
        max_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        max_item.setToolTip("Click to edit maximum stock level")
        self._table.setItem(row_idx, COL_MAX, max_item)

        # Col 10: Suggested — highlight if > 0
        sug_item = _NumericItem(suggested)
        if suggested > 0:
            sug_item.setBackground(QColor("#fff3e0"))
            sug_item.setForeground(QColor("#e65100"))
        self._table.setItem(row_idx, COL_SUGGESTED, sug_item)

        # Col 11-12: Price/Cost — EDITABLE inline
        price_item = QTableWidgetItem(f"{price:,.2f}")
        price_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        price_item.setToolTip("Double-click to edit price")
        self._table.setItem(row_idx, COL_PRICE, price_item)

        cost_item = QTableWidgetItem(f"{cost:,.2f}")
        cost_item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )
        cost_item.setToolTip("Double-click to edit cost")
        self._table.setItem(row_idx, COL_COST, cost_item)

        # Col 13: Match
        self._table.setItem(row_idx, COL_MATCH, text_item(match_part))

        # Col 14: Margin
        if price > 0 and cost > 0:
            margin_pct = ((price - cost) / price) * 100
            margin_text = f"{margin_pct:.1f}%"
        else:
            margin_text = ""
        self._table.setItem(row_idx, COL_MARGIN, text_item(margin_text))

        # Col 15: Tier pricing indicator
        tier_val = r.get("tier_pricing") or r.get("has_tier_pricing")
        tier_text = "Yes" if tier_val else ""
        self._table.setItem(row_idx, COL_TIER, text_item(tier_text))

        # Col 16: Last Scrape — per-source dates
        pid = r.get("id")
        if pid:
            try:
                dates = get_scrape_dates_for_product(pid)
                if dates:
                    scrape_text = " | ".join(f"{k}: {v}" for k, v in sorted(dates.items()))
                else:
                    scrape_text = ""
            except Exception:
                scrape_text = ""
        else:
            scrape_text = ""
        scrape_item = text_item(scrape_text)
        if scrape_text:
            scrape_item.setToolTip(scrape_text)
        self._table.setItem(row_idx, COL_LAST_SCRAPE, scrape_item)

        # Col 17: Last Sold date
        last_sold = str(r.get("_last_sold", "") or "")
        self._table.setItem(row_idx, COL_LAST_SOLD, text_item(last_sold))

        # Col 18: Times Sold (12 months)
        times_sold = int(r.get("_times_sold_12m", 0) or 0)
        ts_item = _NumericItem(times_sold)
        if times_sold > 0:
            ts_item.setForeground(QColor("#1b5e20"))
        self._table.setItem(row_idx, COL_TIMES_SOLD, ts_item)

        # Col 19: Type pill (NEW / REMAN / USED / CORE / KIT) — colors match mockup
        ptype_raw = str(r.get("product_type", "") or r.get("type", "") or "").upper()
        # Normalize common QBO/legacy values to mockup labels
        _type_normalize = {
            "INVENTORIED": "NEW", "INVENTORY": "NEW", "SERVICE": "NEW",
            "NONINVENTORY": "NEW", "NON_INVENTORY": "NEW",
        }
        ptype = _type_normalize.get(ptype_raw, ptype_raw) or "NEW"
        type_item = text_item(ptype)
        # Mockup colors: REMAN=purple, KIT=orange, NEW=green, USED=amber, CORE=blue
        _type_colors = {
            "NEW":   QColor("#3fb950"),
            "REMAN": QColor("#bc8cff"),
            "USED":  QColor("#d29922"),
            "CORE":  QColor("#58a6ff"),
            "KIT":   QColor("#f0a850"),
        }
        type_item.setForeground(_type_colors.get(ptype, QColor("#c9d1d9")))
        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row_idx, COL_TYPE, type_item)

        # Col 20: On Order (from open POs) — uses cached batch lookup if present
        on_order = 0
        try:
            on_order = int(self._on_order_map.get(r.get("id"), 0))
        except Exception:
            on_order = 0
        oo_item = _NumericItem(on_order)
        if on_order > 0:
            oo_item.setForeground(QColor("#58a6ff"))
        self._table.setItem(row_idx, COL_ON_ORDER, oo_item)

        # Col 21: Flags (📸 ♻ L 📄) — rendered as 4 colored mini-squares
        # per the redesign mockup. Activity dots (Q/S/P/A/R/K) are deferred
        # until the quote / SO / adjustment modules are wired up.
        has_photo = bool(
            r.get("image_url") or r.get("photo_url")
            or r.get("has_image") or (r.get("image_count") or 0) > 0
        )
        has_core = bool(
            r.get("core_required") or r.get("core_charge")
            or r.get("core_sell_price") or r.get("has_core")
        )
        lot_tracked = bool(
            r.get("lot_tracked") or r.get("lot_required")
            or r.get("requires_serial")
        )
        has_spec = bool(
            r.get("spec_sheet_url") or r.get("datasheet_url")
            or r.get("has_spec_sheet")
        )
        # Sortable backing item (sort by number of set flags).
        set_count = sum(1 for v in (has_photo, has_core, lot_tracked, has_spec) if v)
        flags_item = _NumericItem(set_count, "")
        flags_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        flags_item.setToolTip(
            "📸 photo · ♻ core · L lot/serial · 📄 spec sheet"
        )
        self._table.setItem(row_idx, COL_FLAGS, flags_item)
        self._table.setCellWidget(
            row_idx, COL_FLAGS,
            self._make_flags_cell(has_photo, has_core, lot_tracked, has_spec),
        )

        # Col 22: Alloc (allocated to open sales orders) – best-effort lookup
        alloc = 0
        try:
            alloc = int(self._alloc_map.get(r.get("id"), 0)) if hasattr(self, "_alloc_map") else 0
        except Exception:
            alloc = 0
        alloc_item = _NumericItem(alloc)
        if alloc > 0:
            alloc_item.setForeground(QColor("#d29922"))
        alloc_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row_idx, COL_ALLOC, alloc_item)

        # Col 23: Avail = OnHand - Alloc, rendered as text + tiny bar via widget
        avail = qty - alloc
        avail_widget = self._make_avail_cell(avail, qty, int(r.get("min_qty") or r.get("reorder_point") or 0))
        # Store a sortable item alongside the widget
        avail_item = _NumericItem(avail, str(avail))
        avail_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(row_idx, COL_AVAIL, avail_item)
        self._table.setCellWidget(row_idx, COL_AVAIL, avail_widget)

        # Apply tagged row highlight (wins over status tint)
        if tagged:
            self._style_tagged_row(row_idx, True)
        else:
            self._apply_status_row_color(row_idx, r)

    # ────────────────────────────────────────────
    #  SORT-SAFE ROW → PRODUCT LOOKUP
    # ────────────────────────────────────────────
    def _product_for_row(self, visual_row: int) -> dict[str, Any] | None:
        """Get the product dict for a visual table row (safe after sorting)."""
        tag_item = self._table.item(visual_row, COL_TAG)
        if tag_item is None:
            return None
        pid = tag_item.data(Qt.ItemDataRole.UserRole)
        if pid is None:
            return None
        return self._product_map.get(pid)

    # ────────────────────────────────────────────
    #  TAG FOR PO — checkbox handler
    # ────────────────────────────────────────────
    def _on_cell_changed(self, row: int, col: int) -> None:
        product = self._product_for_row(row)
        if product is None:
            return
        pid = product.get("id")
        if pid is None:
            return

        # ── Order checkbox toggled ──
        if col == COL_TAG:
            item = self._table.item(row, COL_TAG)
            if item is None:
                return
            checked = item.checkState() == Qt.CheckState.Checked
            try:
                update_product(pid, tagged_for_po=int(checked))
                product["tagged_for_po"] = int(checked)
                self._refresh_status_cell(row, product)
                self._style_tagged_row(row, checked)
                self._recount_tagged()
                self._update_checked_selection()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update tag: {e}")
            return

        # ── Inline Min edit ──
        if col == COL_MIN:
            item = self._table.item(row, COL_MIN)
            if item is None:
                return
            try:
                new_val = max(0, int(item.text()))
            except ValueError:
                new_val = int(product.get("min_qty", 0) or 0)
                item.setText(str(new_val))
                return
            try:
                update_product(pid, min_qty=new_val)
                product["min_qty"] = new_val
                item.setText(str(new_val))
                self._refresh_status_cell(row, product)
                self._refresh_suggested_cell(row, product)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update min: {e}")
            return

        # ── Inline Max edit ──
        if col == COL_MAX:
            item = self._table.item(row, COL_MAX)
            if item is None:
                return
            try:
                new_val = max(0, int(item.text()))
            except ValueError:
                new_val = int(product.get("max_qty", 0) or 0)
                item.setText(str(new_val))
                return
            try:
                update_product(pid, max_qty=new_val)
                product["max_qty"] = new_val
                item.setText(str(new_val))
                self._refresh_suggested_cell(row, product)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update max: {e}")
            return

        # ── Inline Price edit ──
        if col == COL_PRICE:
            item = self._table.item(row, COL_PRICE)
            if item is None:
                return
            raw = item.text().replace("$", "").replace(",", "").strip()
            try:
                new_val = max(0.0, float(raw))
            except ValueError:
                new_val = float(product.get("selling_price", 0) or 0)
            try:
                update_product(pid, selling_price=new_val)
                product["selling_price"] = new_val
                self._table.blockSignals(True)
                item.setText(f"{new_val:,.2f}")
                self._table.blockSignals(False)
                self._refresh_margin_cell(row, product)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update price: {e}")
            return

        # ── Inline Cost edit ──
        if col == COL_COST:
            item = self._table.item(row, COL_COST)
            if item is None:
                return
            raw = item.text().replace("$", "").replace(",", "").strip()
            try:
                new_val = max(0.0, float(raw))
            except ValueError:
                new_val = float(product.get("cost", 0) or product.get("pai_cost", 0) or 0)
            try:
                update_product(pid, cost=new_val)
                product["cost"] = new_val
                self._table.blockSignals(True)
                item.setText(f"{new_val:,.2f}")
                self._table.blockSignals(False)
                self._refresh_margin_cell(row, product)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update cost: {e}")
            return

        # ── Inline Supplier edit ──
        if col == COL_SUPPLIER:
            item = self._table.item(row, COL_SUPPLIER)
            if item is None:
                return
            new_val = item.text().strip()
            try:
                update_product(pid, supplier=new_val)
                product["supplier"] = new_val
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update supplier: {e}")
            return

        # ── Inline Category edit ──
        if col == COL_CATEGORY:
            item = self._table.item(row, COL_CATEGORY)
            if item is None:
                return
            new_val = item.text().strip()
            try:
                update_product(pid, category=new_val)
                product["category"] = new_val
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update category: {e}")
            return

    def _refresh_status_cell(self, row: int, product: dict) -> None:
        """Recalculate and update the status badge for a row."""
        status_text, status_color = self._compute_status_display(product)
        si = self._table.item(row, COL_STATUS)
        if si:
            si.setText(status_text)
            si.setForeground(status_color)
        # Re-apply row tint (tagged highlight wins; see _apply_status_row_color)
        if not bool(product.get("tagged_for_po", 0)):
            self._apply_status_row_color(row, product)

    def _status_row_bg(self, product: dict) -> QColor | None:
        """Row tint by status. Dark-theme aware — keeps rows legible.

        We intentionally return None (no tint) for the dark theme. The
        status pill in COL_STATUS already communicates the state, and
        setting cell backgrounds here also bleeds through QSS selection
        highlights when the table loses focus — producing the unreadable
        pale-green rows the user reported.
        """
        return None

    def _apply_status_row_color(self, row: int, product: dict) -> None:
        """Tint all cells of a row based on status. Skips tag/supplier/suggested columns."""
        bg = self._status_row_bg(product)
        if bg is None:
            bg = QColor(255, 255, 255, 0)
        for col in range(NUM_COLS):
            item = self._table.item(row, col)
            if item is None:
                continue
            # Skip cells whose own coloring should win.
            if col in (COL_TAG, COL_SUPPLIER, COL_SUGGESTED, COL_STATUS):
                continue
            item.setBackground(bg)

    def _compute_status_display(self, product: dict) -> tuple[str, QColor]:
        """Compute user-facing status label/color from manual and stock state."""
        inv_status = str(product.get("inventory_status", "") or "active").strip().lower()
        qty = int(product.get("qty_on_hand", 0) or 0)
        min_qty = int(product.get("min_qty", 0) or 0)

        if inv_status in ("discontinued", "inactive"):
            return "⚫ Discontinued", QColor("#616161")
        if inv_status == "pricing_review":
            return "🔵 Pricing Review", QColor("#1565c0")
        if inv_status == "need_review":
            return "🟠 Need Review", QColor("#ef6c00")
        if inv_status == "pre_inventory":
            return "🟣 Pre-Inventory", QColor("#6a1b9a")
        if qty <= 0:
            if min_qty <= 0:
                return "🟢 Out of Stock", QColor("#2e7d32")
            return "🔴 Out of Stock", QColor("#c62828")
        if min_qty > 0 and qty < min_qty:
            return "🟡 Low Stock", QColor("#f57c00")
        return "🟢 In Stock", QColor("#2e7d32")

    def _refresh_margin_cell(self, row: int, product: dict) -> None:
        """Recalculate and update the margin cell for a row."""
        price = float(product.get("selling_price", 0) or 0)
        cost = float(product.get("pai_cost", 0) or 0) or float(product.get("cost", 0) or 0)
        if price > 0 and cost > 0:
            margin_text = f"{((price - cost) / price) * 100:.1f}%"
        else:
            margin_text = ""
        mi = self._table.item(row, COL_MARGIN)
        if mi:
            self._table.blockSignals(True)
            mi.setText(margin_text)
            self._table.blockSignals(False)

    def _style_tagged_row(self, row: int, tagged: bool) -> None:
        """Apply or remove subtle highlight + bold for a tagged-for-PO row.

        Dark-theme tint — light green/blue legacy colors blew up readability.
        """
        bg = QColor("#1f3326") if tagged else QColor(255, 255, 255, 0)
        bold_font = QFont()
        bold_font.setBold(tagged)
        for col in range(NUM_COLS):
            item = self._table.item(row, col)
            if item is None:
                continue
            item.setFont(bold_font)
            # Keep checkbox cell on native palette so the check indicator stays visible.
            if col not in (COL_TAG, COL_SUPPLIER, COL_SUGGESTED, COL_STATUS):
                item.setBackground(bg)

    def _refresh_suggested_cell(self, row: int, product: dict) -> None:
        """Recalculate and update the suggested order qty for a row."""
        qty = int(product.get("qty_on_hand", 0) or 0)
        max_qty = int(product.get("max_qty", 0) or 0)
        suggested = (max_qty - qty) if (max_qty > 0 and qty < max_qty) else 0
        self._table.blockSignals(True)
        sug_item = _NumericItem(suggested)
        if suggested > 0:
            sug_item.setBackground(QColor("#fff3e0"))
            sug_item.setForeground(QColor("#e65100"))
        self._table.setItem(row, COL_SUGGESTED, sug_item)
        self._table.blockSignals(False)

    def _update_tagged_count(self, count: int | None = None) -> None:
        """Keep legacy PO button hidden; PO creation is handled in PO workflows."""
        self._create_po_btn.setVisible(False)
        self._create_po_btn.setEnabled(False)

    def _update_summary_strip(self) -> None:
        """Refresh summary counts and update tagged count."""
        total = len(self._last_results)
        tagged = 0
        out = 0
        below = 0
        for r in self._last_results:
            qty = int(r.get("qty_on_hand", 0) or 0)
            mn = int(r.get("min_qty", 0) or 0)
            if r.get("tagged_for_po"):
                tagged += 1
            if qty <= 0:
                out += 1
            elif mn > 0 and qty < mn:
                below += 1

        # Append health stats to results label
        existing = self._results_label.text().split("  ·")[0]  # base text before stats
        stats = existing
        if tagged:
            stats += f"  ·  {tagged} tagged"
        if out:
            stats += f"  ·  {out} out of stock"
        if below:
            stats += f"  ·  {below} below min"
        self._results_label.setText(stats)
        self._update_tagged_count(tagged)
        self._update_kpis_and_chips()

    def _recount_tagged(self) -> None:
        self._update_summary_strip()

    def _filter_to_tagged(self) -> None:
        """Click on tagged count → switch stock filter to 'Tagged for PO'."""
        self._set_stock_filter("tagged")

    # ────────────────────────────────────────────
    #  QUICK SELECT BUTTONS
    # ────────────────────────────────────────────
    def _select_rows_by_condition(self, predicate) -> int:
        """Select rows matching predicate(product) → bool. Returns count selected."""
        self._table.clearSelection()
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        count = 0
        for row in range(self._table.rowCount()):
            product = self._product_for_row(row)
            if product and predicate(product):
                self._table.selectRow(row)
                count += 1
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._update_selection_info()
        return count

    def _select_out_of_stock(self) -> None:
        """Select all rows where qty on hand = 0."""
        n = self._select_rows_by_condition(
            lambda p: int(p.get("qty_on_hand", 0) or 0) <= 0
        )
        self._selection_info_label.setText(f"{n} out-of-stock selected")

    def _select_below_min(self) -> None:
        """Select all rows where qty < min."""
        def below_min(p):
            qty = int(p.get("qty_on_hand", 0) or 0)
            mn = int(p.get("min_qty", 0) or 0)
            return mn > 0 and qty < mn
        n = self._select_rows_by_condition(below_min)
        self._selection_info_label.setText(f"{n} below-min selected")

    def _select_suggested(self) -> None:
        """Select all rows with suggested reorder > 0."""
        def has_suggested(p):
            qty = int(p.get("qty_on_hand", 0) or 0)
            mx = int(p.get("max_qty", 0) or 0)
            return mx > 0 and qty < mx
        n = self._select_rows_by_condition(has_suggested)
        self._selection_info_label.setText(f"{n} needing reorder selected")

    def _select_all_visible(self) -> None:
        """Select all currently visible rows."""
        self._table.selectAll()
        self._update_selection_info()

    def _table_clear_selection(self) -> None:
        """Clear all row selections."""
        self._table.clearSelection()
        self._selection_info_label.setText("")

    def _update_selection_info(self) -> None:
        """Update the 'X selected (Y checked, Z hidden)' label."""
        selected = len(self._table.selectionModel().selectedRows())
        checked = len(self._get_checked_products())
        total_displayed = self._table.rowCount()
        total_data = len(self._last_results)
        hidden = total_data - total_displayed
        parts = []
        if selected:
            parts.append(f"{selected} selected")
        if checked:
            parts.append(f"{checked} checked")
        if hidden > 0:
            parts.append(f"{hidden} hidden by filter")
        self._selection_info_label.setText(" · ".join(parts))

    # ────────────────────────────────────────────
    #  EXPORT CSV
    # ────────────────────────────────────────────
    def _export_to_csv(self) -> None:
        data = self._displayed_results or self._last_results
        if not data:
            QMessageBox.information(self, "Export", "No data to export.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Products to CSV",
            f"inventory_export_{timestamp}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Tagged", "Status", "SKU", "Product Name", "Manufacturer",
                    "Supplier", "Category", "Qty", "Min", "Max",
                    "Suggested", "Price", "Cost", "Margin", "Tier",
                ])
                for r in data:
                    qty = int(r.get("qty_on_hand", 0) or 0)
                    mn = int(r.get("min_qty", 0) or 0)
                    mx = int(r.get("max_qty", 0) or 0)
                    tagged = bool(r.get("tagged_for_po", 0))
                    if tagged:
                        st = "Tagged"
                    elif qty <= 0:
                        st = "Out of Stock"
                    elif mn > 0 and qty < mn:
                        st = "Below Min"
                    else:
                        st = "Healthy"
                    sug = (mx - qty) if mx > 0 and qty < mx else 0
                    price = float(r.get("selling_price", 0) or 0)
                    cost = float(r.get("pai_cost", 0) or 0) or float(r.get("cost", 0) or 0)
                    margin = ""
                    if price > 0 and cost > 0:
                        margin = f"{((price - cost) / price) * 100:.1f}%"
                    tier_val = r.get("tier_pricing") or r.get("has_tier_pricing")
                    writer.writerow([
                        "Yes" if tagged else "",
                        st,
                        r.get("sku", ""),
                        r.get("title", ""),
                        r.get("manufacturer", ""),
                        r.get("supplier", ""),
                        r.get("category", ""),
                        qty,
                        mn,
                        mx,
                        sug,
                        price,
                        cost,
                        margin,
                        "Yes" if tier_val else "",
                    ])
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {len(data)} products to:\n{path}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Error writing CSV:\n{e}")

    # ────────────────────────────────────────────
    #  ROW ACTIONS
    # ────────────────────────────────────────────
    def _on_open_selected(self) -> None:
        """Enter key → open product detail (read-only view)."""
        row = self._table.currentRow()
        product = self._product_for_row(row)
        if not product:
            return
        sku = str(product.get("sku", "") or "")
        if not sku:
            return
        dlg = ProductDetailDialog(sku=sku, parent=self)
        dlg.exec()
        self._do_browse()

    def _on_edit_from_dblclick(self, row: int, col: int) -> None:
        """Double-click → inline edit for editable cols, else open full editor."""
        # Editable inline columns – let Qt handle the cell editor
        if col in (COL_TAG, COL_MIN, COL_MAX, COL_PRICE, COL_COST, COL_SUPPLIER, COL_CATEGORY):
            return
        product = self._product_for_row(row)
        if not product:
            return
        sku = str(product.get("sku", "") or "")
        if not sku:
            return
        pid = product.get("id")
        inv_status = str(product.get("inventory_status", "") or "active").strip().lower()
        is_pricing_review = inv_status == "pricing_review"
        from .product_workbench_dialog import ProductWorkbenchDialog
        dlg = ProductWorkbenchDialog(
            mode="edit",
            sku=sku,
            initial_section="pricing" if is_pricing_review else None,
            parent=self,
        )
        if dlg.exec():
            if is_pricing_review and pid is not None:
                try:
                    # Confirming pricing sends the item back to normal auto stock status.
                    update_product(pid, inventory_status="active")
                    product["inventory_status"] = "active"
                except Exception as e:
                    QMessageBox.warning(
                        self,
                        "Pricing Review",
                        f"Saved, but failed to clear Pricing Review status: {e}",
                    )
            self._do_browse()

    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        checked = self._get_checked_products()
        n = max(len(checked), len(selected))
        self._edit_btn.setEnabled(len(selected) == 1)
        self._bulk_btn.setEnabled(n >= 2)
        if n >= 2:
            self._bulk_btn.setText(f"✏️ Bulk Edit ({n})")
        else:
            self._bulk_btn.setText("✏️ Bulk Edit")
        self._update_selection_info()

        # Populate detail drawer on single selection
        # (Drawer removed per redesign — double-click opens the full editor dialog.)
        if False and len(selected) == 1:
            product = self._product_for_row(selected[0].row())
            if product:
                self._populate_detail_drawer(product)
        elif len(selected) == 0:
            try:
                self._detail_browser.setHtml(
                    '<p style="color:#888;">Select a product row to see details here.</p>'
                )
                self._detail_toggle.setText("▼ Product Detail")
            except Exception:
                pass

    def _on_edit_selected(self) -> None:
        """Edit button / Ctrl+E → open Edit Product dialog."""
        row = self._table.currentRow()
        product = self._product_for_row(row)
        if not product:
            return
        sku = str(product.get("sku", "") or "")
        if not sku:
            return
        from .product_workbench_dialog import ProductWorkbenchDialog
        dlg = ProductWorkbenchDialog(mode="edit", sku=sku, parent=self)
        if dlg.exec():
            self._do_browse()

    def _on_add_product(self) -> None:
        """Open the unified Product Workbench in 'new' mode.

        Quick Entry remains available via the dedicated toolbar button
        (`_quick_entry_btn` -> `_on_quick_entry`).
        """
        from .product_workbench_dialog import ProductWorkbenchDialog

        while True:
            dlg = ProductWorkbenchDialog(mode="new", parent=self)
            ok = dlg.exec()
            if ok:
                self._load_filters()
                self._do_browse()
                if getattr(dlg, "should_add_another", False):
                    continue
            break
        return

    def _on_add_product_LEGACY(self) -> None:
        from .quick_entry_dialog import QuickEntryDialog

        # Simple 2-option chooser dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Product")
        dlg.setFixedSize(340, 160)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("How would you like to add a product?"))

        quick_btn = QPushButton("⚡  Quick Entry")
        quick_btn.setToolTip("Compact keyboard-first form for rapid product creation")
        quick_btn.setStyleSheet(
            "QPushButton { background-color: #4a6741; color: white; font-weight: bold; "
            "padding: 10px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background-color: #5a7a51; }"
        )
        detail_btn = QPushButton("📝  Detailed Entry")
        detail_btn.setToolTip("Full product editor with all fields")
        detail_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; font-weight: bold; "
            "padding: 10px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background-color: #1976d2; }"
        )

        quick_btn.clicked.connect(lambda: dlg.done(1))
        detail_btn.clicked.connect(lambda: dlg.done(2))
        lay.addWidget(quick_btn)
        lay.addWidget(detail_btn)

        choice = dlg.exec()
        if choice == 1:
            # Quick Entry
            while True:
                qe = QuickEntryDialog(parent=self)
                if qe.exec():
                    self._load_filters()
                    self._do_browse()
                    if qe.should_duplicate:
                        continue
                break
        elif choice == 2:
            # Detailed Entry (full workbench in 'new' mode)
            from .product_workbench_dialog import ProductWorkbenchDialog
            while True:
                ed = ProductWorkbenchDialog(mode="new", parent=self)
                if ed.exec():
                    self._load_filters()
                    if getattr(ed, "should_add_another", False):
                        continue
                break

    def _on_quick_entry(self) -> None:
        """Open compact Quick Entry dialog for rapid product creation."""
        while True:
            dlg = QuickEntryDialog(parent=self)
            if dlg.exec():
                self._load_filters()
                self._do_browse()
                if dlg.should_duplicate or dlg.should_open_new:
                    continue
            break

    def _on_bulk_edit(self) -> None:
        """Open bulk edit dialog for checked or selected products."""
        # Prefer checked (tagged) rows; fall back to Qt selection
        products = self._get_checked_products()
        if len(products) < 2:
            selected_rows = self._table.selectionModel().selectedRows()
            products = []
            for idx in selected_rows:
                p = self._product_for_row(idx.row())
                if p:
                    products.append(p)
        if len(products) < 2:
            QMessageBox.information(self, "Bulk Edit",
                                    "Check or select at least 2 products.")
            return
        dlg = BulkEditDialog(products, parent=self)
        if dlg.exec():
            self._do_browse()

    # ────────────────────────────────────────────
    #  CREATE PO FROM TAGGED
    # ────────────────────────────────────────────
    def _on_create_po_from_tagged(self) -> None:
        """PO creation from Products is intentionally disabled to prevent accidental PO generation."""
        QMessageBox.information(
            self,
            "PO Creation Moved",
            "Creating Purchase Orders from the Products screen is disabled.\n\n"
            "Use Purchase Orders -> Auto-Restock (wizard) or the Sales Order PO workflow instead.",
        )

    # ==================================================================
    # SCRAPER — Bulk scrape selected products
    # ==================================================================

    def _get_selected_products(self) -> list[dict]:
        """Return product dicts for all selected (highlighted) or checked rows."""
        seen_ids = set()
        products = []
        # Highlighted rows
        for idx in self._table.selectionModel().selectedRows():
            p = self._product_for_row(idx.row())
            if p and p.get("sku") and p.get("id") not in seen_ids:
                seen_ids.add(p["id"])
                products.append(p)
        # Checked rows (checkboxes in COL_TAG)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_TAG)
            if item and item.checkState() == Qt.CheckState.Checked:
                p = self._product_for_row(row)
                if p and p.get("sku") and p.get("id") not in seen_ids:
                    seen_ids.add(p["id"])
                    products.append(p)
        return products

    def _scrape_tagged(self) -> None:
        """Launch scrape for all tagged-for-PO products."""
        if self._scrape_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        tagged = [r for r in self._last_results if r.get("tagged_for_po") and r.get("sku")]
        if not tagged:
            QMessageBox.warning(self, "No Tagged Items", "No tagged products to scrape.")
            return

        reply = QMessageBox.question(
            self, "Confirm Scrape",
            f"Scrape {len(tagged)} tagged product(s) for pricing + cross-references?\n\n"
            "Results will go to Pending Review.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._scrape_job_type = "both"
        self._scrape_queue = list(tagged)
        self._scrape_total = len(tagged)
        self._scrape_completed = 0

        self._scrape_btn.setEnabled(False)
        self._scrape_progress.setValue(0)
        self._scrape_progress.setMaximum(self._scrape_total)
        self._scrape_progress.show()
        self._scrape_status_label.show()
        self._scrape_status_label.setText(f"Scraping 0/{self._scrape_total}…")

        self._scrape_next()

    def _scrape_selected(self, job_type: str) -> None:
        """Launch scrape for all selected products."""
        if self._scrape_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        products = self._get_selected_products()
        if not products:
            QMessageBox.warning(
                self, "No Selection",
                "Select one or more products to scrape.",
            )
            return

        reply = QMessageBox.question(
            self, "Confirm Scrape",
            f"Scrape {len(products)} product(s) for {job_type}?\n\n"
            "Results will go to Pending Review — nothing is written to live data automatically.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._scrape_job_type = job_type
        self._scrape_queue = list(products)
        self._scrape_total = len(products)
        self._scrape_completed = 0

        self._scrape_btn.setEnabled(False)
        self._scrape_progress.setValue(0)
        self._scrape_progress.setMaximum(self._scrape_total)
        self._scrape_progress.show()
        self._scrape_status_label.show()
        self._scrape_status_label.setText(f"Scraping 0/{self._scrape_total}…")

        self._scrape_next()

    def _scrape_next(self) -> None:
        """Pop the next product from the queue and start scraping."""
        if not self._scrape_queue:
            self._on_scrape_batch_done()
            return

        product = self._scrape_queue.pop(0)
        pid = product.get("id")
        # Resolve search value: vendor SKU → OEM part# → extract from SKU
        vendor_sku = str(product.get("pai_sku", "") or "").strip()
        oem_pn = str(product.get("oem_part_number", "") or "").strip()
        search_value = vendor_sku or oem_pn
        display_sku = str(product.get("sku", ""))

        # Collect alternate search terms for fallback
        alt_terms: list[str] = []
        if oem_pn and oem_pn != search_value:
            alt_terms.append(oem_pn)
        if vendor_sku and vendor_sku != search_value and vendor_sku not in alt_terms:
            alt_terms.append(vendor_sku)

        if not search_value:
            # Fall back: strip JAKS- prefix from SKU to get the part number
            raw = display_sku.strip()
            if raw.upper().startswith("JAKS-"):
                remainder = raw[5:]
                parts = remainder.split("-", 1)
                if len(parts) == 2 and parts[0].isalpha() and len(parts[0]) <= 6:
                    search_value = parts[1]
                else:
                    search_value = remainder
            else:
                search_value = raw
        if not search_value:
            self._scrape_completed += 1
            self._scrape_progress.setValue(self._scrape_completed)
            self._scrape_status_label.setText(
                f"Skipped {display_sku} — no vendor SKU or OEM part#"
            )
            self._scrape_next()
            return

        self._scrape_status_label.setText(
            f"Scraping {self._scrape_completed + 1}/{self._scrape_total}: {search_value}"
        )

        self._scrape_worker = ScraperWorker(
            self._scrape_job_type, search_value,
            alt_terms=alt_terms or None,
            product_id=pid,
        )
        self._scrape_worker.progress.connect(self._on_scrape_progress)
        self._scrape_worker.finished.connect(self._on_scrape_one_done)
        self._scrape_worker.error.connect(self._on_scrape_one_error)
        self._scrape_worker.start()

    def _on_scrape_progress(self, current: int, total: int, message: str) -> None:
        self._scrape_status_label.setText(
            f"[{self._scrape_completed + 1}/{self._scrape_total}] {message}"
        )

    def _on_scrape_one_done(self, _result: dict) -> None:
        self._scrape_worker = None
        self._scrape_completed += 1
        self._scrape_progress.setValue(self._scrape_completed)
        self._scrape_next()

    def _on_scrape_one_error(self, error_msg: str) -> None:
        self._scrape_worker = None
        self._scrape_completed += 1
        self._scrape_progress.setValue(self._scrape_completed)
        self._scrape_status_label.setText(f"⚠ Error: {error_msg[:80]}")
        log.warning("Scrape error: %s", error_msg)
        # Continue with next product despite error
        self._scrape_next()

    def _on_scrape_batch_done(self) -> None:
        """All products in the batch finished scraping."""
        self._scrape_btn.setEnabled(True)
        self._scrape_progress.hide()
        self._scrape_status_label.setText(
            f"✅ Scrape complete — {self._scrape_total} product(s) processed"
        )

        reply = QMessageBox.question(
            self, "Scrape Complete",
            f"Scraped {self._scrape_total} product(s).\n\n"
            "Open review dialog to accept/reject results?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            dlg = ScraperReviewDialog(product_id=None, parent=self)
            dlg.exec()

    # ------------------------------------------------------------------
    # Part Finder / Vendor Import
    # ------------------------------------------------------------------

    def _on_open_part_finder(self) -> None:
        """Open the Part Finder dialog."""
        from .part_finder_dialog import PartFinderDialog
        from .vendor_import_dialog import VendorImportDialog

        dlg = PartFinderDialog(parent=self)

        dlg.add_to_quote_requested.connect(self._pf_add_to_quote)
        dlg.open_product_requested.connect(self._pf_open_product)
        dlg.import_requested.connect(lambda data: self._pf_import(data, dlg))

        dlg.exec()
        self._full_refresh()

    def _on_add_from_vendor(self) -> None:
        """Open a blank vendor import dialog."""
        from .vendor_import_dialog import VendorImportDialog

        dlg = VendorImportDialog({}, parent=self)
        dlg.product_created.connect(lambda r: self._full_refresh())
        dlg.exec()

    def _pf_add_to_quote(self, product_info: dict) -> None:
        """Create a new quote with the product from Part Finder."""
        from .quote_dialog import QuoteDialog

        product_id = product_info.get("product_id")
        sku = product_info.get("sku", "")
        title = product_info.get("title", "")
        price = float(product_info.get("price") or 0)
        cost = float(product_info.get("cost") or 0)
        supplier = product_info.get("supplier", "")

        try:
            result = create_quote(lines=[{
                "product_id": product_id,
                "description": title,
                "supplier": supplier,
                "qty": 1,
                "unit_price": price,
                "unit_cost": cost,
            }])
            if result and result.get("id"):
                dlg = QuoteDialog(quote_id=result["id"], parent=self)
                dlg.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not create quote: {exc}")

    def _pf_open_product(self, product_id: int) -> None:
        """Open product detail from Part Finder."""
        dlg = ProductDetailDialog(product_id, parent=self)
        dlg.exec()
        self._full_refresh()

    def _pf_import(self, vendor_data: dict, finder_dlg=None) -> None:
        """Open vendor import from Part Finder result."""
        from .vendor_import_dialog import VendorImportDialog

        dlg = VendorImportDialog(vendor_data, parent=self)
        dlg.product_created.connect(lambda r: self._full_refresh())
        dlg.exec()

    # ────────────────────────────────────────────
    #  ADDITIONAL SUGGESTIONS — Section 4
    # ────────────────────────────────────────────

    # ── KPI card click → drill into the matching subset ──
    def _on_kpi_card_clicked(self, key: str) -> None:
        rows = self._last_results or []
        if key == "active":
            self._clear_filters()
            return
        subset: list[dict] = []
        if key == "value":
            subset = [
                r for r in rows
                if int(r.get("qty_on_hand", 0) or 0) > 0
                and (float(r.get("pai_cost", 0) or r.get("cost", 0) or 0) > 0)
            ]
            banner = "Drill: SKUs with on-hand value"
        elif key == "low":
            subset = [
                r for r in rows
                if int(r.get("qty_on_hand", 0) or 0) > 0
                and int(r.get("min_qty", 0) or 0) > 0
                and int(r.get("qty_on_hand", 0) or 0) < int(r.get("min_qty", 0) or 0)
            ]
            banner = "Drill: Low stock (below min)"
        elif key == "out":
            subset = [r for r in rows if int(r.get("qty_on_hand", 0) or 0) <= 0]
            banner = "Drill: Out of stock"
        elif key == "on_order":
            subset = [r for r in rows if int(r.get("on_order_qty", 0) or 0) > 0]
            banner = "Drill: On order (PO)"
        elif key == "dead":
            subset = [
                r for r in rows
                if int(r.get("qty_on_hand", 0) or 0) > 0
                and not r.get("last_sold_date")
            ]
            banner = "Drill: Dead stock (no sale 180d)"
        elif key == "margin":
            def _margin(r):
                p = float(r.get("selling_price", 0) or 0)
                c = float(r.get("pai_cost", 0) or r.get("cost", 0) or 0)
                return ((p - c) / p * 100) if p > 0 and c > 0 else 100.0
            subset = [r for r in rows if _margin(r) < 15]
            banner = "Drill: Margin < 15%"
        else:
            return
        self._render_subset(subset, banner=banner)

    # ── Clear all filters ──
    def _clear_filters(self) -> None:
        self._query.blockSignals(True)
        self._query.clear()
        self._query.blockSignals(False)
        for combo in (
            self._cat_combo, self._type_combo,
            self._wh_combo, self._supplier_combo,
        ):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._on_search()

    # ── Attention chip click → drill into the matching subset ──
    def _on_attention_chip_clicked(self, key: str) -> None:
        """Apply a view that surfaces the rows behind an attention chip."""
        rows = self._last_results or []

        def _flt(predicate):
            return [r for r in rows if predicate(r)]

        if key == "no_photo":
            self._open_photo_wizard()
            return

        subset: list[dict] = []
        if key == "out_so":
            subset = _flt(lambda r: int(r.get("qty_on_hand", 0) or 0) <= 0)
        elif key == "negative":
            subset = _flt(lambda r: int(r.get("qty_on_hand", 0) or 0) < 0)
        elif key == "low":
            def _is_low(r):
                qty = int(r.get("qty_on_hand", 0) or 0)
                mn = int(r.get("min_qty", 0) or 0)
                return qty > 0 and mn > 0 and qty < mn
            subset = _flt(_is_low)
        elif key == "no_cost":
            subset = _flt(
                lambda r: float(r.get("pai_cost", 0) or r.get("cost", 0) or 0) <= 0
            )
        elif key == "price_lt":
            def _bad(r):
                price = float(r.get("selling_price", 0) or 0)
                cost = float(r.get("pai_cost", 0) or r.get("cost", 0) or 0)
                return price > 0 and cost > 0 and price < cost
            subset = _flt(_bad)
        else:
            QMessageBox.information(
                self, "Attention",
                f"No drill-down configured for chip “{key}”."
            )
            return

        # Render filtered subset in-place
        self._render_subset(subset, banner=f"Filtered by attention chip: {key}")

    def _render_subset(self, rows: list[dict], banner: str = "") -> None:
        """Re-render the table with only the supplied rows (non-destructive).

        IMPORTANT: sorting must be disabled while filling rows or Qt will
        re-sort each row as it's added and scramble the data across rows
        (visible as mostly-empty rows with one populated row).
        """
        self._last_results = list(rows)
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        # Hard-clear: drop any existing cell widgets too.
        for r in range(self._table.rowCount()):
            for c in range(self._table.columnCount()):
                if self._table.cellWidget(r, c) is not None:
                    self._table.removeCellWidget(r, c)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            try:
                self._fill_product_row(i, r)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "render_subset: row %d failed: %s", i, exc
                )
        self._table.setSortingEnabled(was_sorting)
        if banner:
            self._results_label.setText(banner + f"  ·  {len(rows)} rows")
        self._update_kpis_and_chips()

    # ── Saved views (filter presets) ──
    def _capture_view(self) -> dict:
        return {
            "q": self._query.text(),
            "cat": self._cat_combo.currentData() or "",
            "type": self._type_combo.currentData() or "",
            "wh": self._wh_combo.currentData() or "",
            "sup": self._supplier_combo.currentData() or "",
        }

    def _apply_view(self, v: dict) -> None:
        self._query.blockSignals(True)
        self._query.setText(v.get("q", ""))
        self._query.blockSignals(False)
        for combo, key in (
            (self._cat_combo, "cat"),
            (self._type_combo, "type"),
            (self._wh_combo, "wh"),
            (self._supplier_combo, "sup"),
        ):
            idx = combo.findData(v.get(key, ""))
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        self._on_search()

    def _load_saved_views(self) -> list[dict]:
        try:
            raw = get_setting("products_saved_views", "") or ""
            return json.loads(raw) if raw else []
        except Exception:
            return []

    def _persist_saved_views(self, views: list[dict]) -> None:
        try:
            set_setting("products_saved_views", json.dumps(views))
        except Exception as exc:
            logging.getLogger(__name__).warning("save views: %s", exc)

    def _rebuild_saved_views_menu(self) -> None:
        menu = getattr(self, "_saved_views_menu", None)
        if menu is None:
            return
        menu.clear()
        menu.addAction("💾  Save current view…", self._save_current_view)
        menu.addAction("🗂  Manage views…", self._manage_saved_views)
        views = self._load_saved_views()
        if views:
            menu.addSeparator()
            for v in views:
                name = v.get("name", "(unnamed)")
                menu.addAction(name, lambda vv=v: self._apply_view(vv))

    def _save_current_view(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save view",
                                        "Name this view:")
        name = (name or "").strip()
        if not ok or not name:
            return
        views = self._load_saved_views()
        # replace by name if exists
        views = [v for v in views if v.get("name") != name]
        v = self._capture_view()
        v["name"] = name
        views.append(v)
        self._persist_saved_views(views)
        self._rebuild_saved_views_menu()

    def _manage_saved_views(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QListWidget, QListWidgetItem,
            QPushButton, QHBoxLayout,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Manage saved views")
        dlg.resize(380, 320)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        views = self._load_saved_views()
        for v in views:
            it = QListWidgetItem(v.get("name", "(unnamed)"))
            it.setData(Qt.ItemDataRole.UserRole, v)
            lst.addItem(it)
        lay.addWidget(lst)
        btn_row = QHBoxLayout()
        del_btn = QPushButton("Delete")
        close_btn = QPushButton("Close")
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        def _delete() -> None:
            row = lst.currentRow()
            if row < 0:
                return
            it = lst.takeItem(row)
            if it is None:
                return
            del_name = it.data(Qt.ItemDataRole.UserRole).get("name")
            kept = [v for v in self._load_saved_views()
                    if v.get("name") != del_name]
            self._persist_saved_views(kept)

        del_btn.clicked.connect(_delete)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()
        self._rebuild_saved_views_menu()

    # ── Stock Movements drawer ──
    def _open_stock_movements(self, product: dict) -> None:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
            QHeaderView, QLabel,
        )
        sku = str(product.get("sku", "") or "")
        pid = product.get("id")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Stock Movements — {sku}")
        dlg.resize(720, 480)
        lay = QVBoxLayout(dlg)
        hdr = QLabel(
            f"<b>{sku}</b> · {product.get('title','')}"
            f"<br><span style='color:#888'>Current on-hand: "
            f"{int(product.get('qty_on_hand', 0) or 0):,}</span>"
        )
        lay.addWidget(hdr)

        rows: list[tuple[str, str, str, str]] = []
        # 1) Purchase receipts
        try:
            from db.inventory import get_connection
            conn = get_connection()
            cur = conn.execute(
                """
                SELECT prl.received_qty,
                       prl.unit_cost,
                       pr.received_at,
                       pr.po_number
                  FROM purchase_receipt_lines prl
                  JOIN purchase_receipts pr ON pr.id = prl.receipt_id
                 WHERE prl.product_id = ?
                 ORDER BY pr.received_at DESC
                 LIMIT 200
                """,
                (pid,),
            )
            for qty, cost, ts, po in cur.fetchall():
                rows.append((
                    str(ts or ""),
                    "Receipt",
                    f"+{int(qty or 0):,}",
                    f"PO {po or '?'} @ ${float(cost or 0):,.2f}",
                ))
        except Exception as exc:
            logging.getLogger(__name__).info(
                "stock_movements: no receipts table or query failed: %s", exc
            )

        # 2) Synthetic markers from products table
        for col, label in (
            ("created_at", "Created"),
            ("updated_at", "Edited"),
            ("local_modified_at", "Local edit"),
            ("scraped_at", "Scraped"),
            ("uploaded_at", "Uploaded"),
        ):
            ts = product.get(col)
            if ts:
                rows.append((str(ts), label, "—", ""))

        rows.sort(key=lambda r: r[0], reverse=True)

        tbl = QTableWidget(len(rows), 4)
        tbl.setHorizontalHeaderLabels(["Timestamp", "Type", "Δ Qty", "Note"])
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for i, (ts, kind, delta, note) in enumerate(rows):
            tbl.setItem(i, 0, QTableWidgetItem(ts))
            tbl.setItem(i, 1, QTableWidgetItem(kind))
            qi = QTableWidgetItem(delta)
            if delta.startswith("+"):
                qi.setForeground(QColor("#3fb950"))
            elif delta.startswith("-"):
                qi.setForeground(QColor("#f85149"))
            tbl.setItem(i, 2, qi)
            tbl.setItem(i, 3, QTableWidgetItem(note))
        tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        tbl.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(tbl)

        if not rows:
            lay.addWidget(QLabel(
                "<i>No movement history found for this SKU.</i>"
            ))
        dlg.exec()

    # ── Reorder simulator ──
    def _open_reorder_simulator(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
            QHeaderView, QLabel, QPushButton, QHBoxLayout, QDoubleSpinBox,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Reorder simulator")
        dlg.resize(820, 520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Suggested PO quantities for SKUs at or below reorder point.<br>"
            "<span style='color:#888'>Formula: "
            "suggested = max(reorder × safety, min_qty) − on_hand − on_order</span>"
        ))
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Safety factor ×"))
        sb = QDoubleSpinBox()
        sb.setRange(1.0, 5.0)
        sb.setSingleStep(0.1)
        sb.setValue(1.5)
        ctrl.addWidget(sb)
        ctrl.addStretch()
        tag_btn = QPushButton("📌 Tag suggested for PO")
        ctrl.addWidget(tag_btn)
        lay.addLayout(ctrl)

        tbl = QTableWidget()
        tbl.setColumnCount(6)
        tbl.setHorizontalHeaderLabels(
            ["SKU", "Title", "On hand", "Reorder", "Min", "Suggested"]
        )
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setAlternatingRowColors(True)
        lay.addWidget(tbl)

        def _recompute() -> None:
            factor = sb.value()
            candidates = []
            for r in self._last_results or []:
                qty = int(r.get("qty_on_hand", 0) or 0)
                rp = int(r.get("reorder_point", 0) or 0)
                mn = int(r.get("min_qty", 0) or 0)
                oo = int(r.get("on_order_qty", 0) or 0)
                trigger = max(rp, mn)
                if trigger <= 0:
                    continue
                if qty + oo > trigger:
                    continue
                target = max(int(round(trigger * factor)), mn)
                suggested = max(target - qty - oo, 0)
                if suggested <= 0:
                    continue
                candidates.append((r, qty, rp, mn, suggested))
            tbl.setRowCount(len(candidates))
            for i, (r, qty, rp, mn, suggested) in enumerate(candidates):
                tbl.setItem(i, 0, QTableWidgetItem(str(r.get("sku", ""))))
                tbl.setItem(i, 1, QTableWidgetItem(str(r.get("title", ""))))
                tbl.setItem(i, 2, QTableWidgetItem(f"{qty:,}"))
                tbl.setItem(i, 3, QTableWidgetItem(f"{rp:,}"))
                tbl.setItem(i, 4, QTableWidgetItem(f"{mn:,}"))
                s_item = QTableWidgetItem(f"{suggested:,}")
                s_item.setForeground(QColor("#3fb950"))
                s_item.setData(Qt.ItemDataRole.UserRole, r.get("id"))
                tbl.setItem(i, 5, s_item)
            tbl.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            tbl.horizontalHeader().setStretchLastSection(True)

        sb.valueChanged.connect(lambda _v: _recompute())
        _recompute()

        def _tag_all() -> None:
            tagged = 0
            for i in range(tbl.rowCount()):
                it = tbl.item(i, 5)
                if not it:
                    continue
                pid = it.data(Qt.ItemDataRole.UserRole)
                if not pid:
                    continue
                try:
                    update_product(int(pid), {"tagged_for_po": 1})
                    tagged += 1
                except Exception:
                    pass
            QMessageBox.information(
                dlg, "Reorder simulator",
                f"Tagged {tagged} SKU(s) for PO."
            )
            self._full_refresh()

        tag_btn.clicked.connect(_tag_all)
        dlg.exec()

    # ── Missing-photo wizard ──
    def _open_photo_wizard(self) -> None:
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        )
        rows = [
            r for r in (self._last_results or [])
            if not (r.get("image_url") or int(r.get("image_count", 0) or 0) > 0)
        ]
        if not rows:
            QMessageBox.information(
                self, "Photo wizard",
                "No products in the current view are missing photos. 🎉"
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Missing-photo wizard")
        dlg.resize(560, 240)
        lay = QVBoxLayout(dlg)
        title_lbl = QLabel()
        title_lbl.setWordWrap(True)
        count_lbl = QLabel()
        count_lbl.setStyleSheet("color:#888;")
        lay.addWidget(count_lbl)
        lay.addWidget(title_lbl)
        lay.addStretch()
        btn_row = QHBoxLayout()
        prev_btn = QPushButton("← Previous")
        skip_btn = QPushButton("Skip")
        edit_btn = QPushButton("📝 Edit / Add photo")
        next_btn = QPushButton("Next →")
        close_btn = QPushButton("Close")
        for b in (prev_btn, skip_btn, edit_btn, next_btn, close_btn):
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        idx = [0]

        def _render() -> None:
            i = idx[0]
            r = rows[i]
            count_lbl.setText(f"{i+1} of {len(rows)} missing photos")
            title_lbl.setText(
                f"<h3>{r.get('sku','')}</h3>"
                f"<p>{r.get('title','')}</p>"
                f"<p style='color:#888'>{r.get('category','')} · "
                f"{r.get('supplier','')}</p>"
            )
            prev_btn.setEnabled(i > 0)
            next_btn.setEnabled(i < len(rows) - 1)

        def _next() -> None:
            if idx[0] < len(rows) - 1:
                idx[0] += 1
                _render()

        def _prev() -> None:
            if idx[0] > 0:
                idx[0] -= 1
                _render()

        def _edit() -> None:
            sku = str(rows[idx[0]].get("sku", "") or "")
            if not sku:
                return
            from .product_workbench_dialog import ProductWorkbenchDialog
            ed = ProductWorkbenchDialog(mode="edit", sku=sku, parent=dlg)
            ed.exec()

        prev_btn.clicked.connect(_prev)
        next_btn.clicked.connect(_next)
        skip_btn.clicked.connect(_next)
        edit_btn.clicked.connect(_edit)
        close_btn.clicked.connect(dlg.accept)
        _render()
        dlg.exec()
        self._full_refresh()
