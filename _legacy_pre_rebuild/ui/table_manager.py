"""Table management for JAKS Scraper.

Centralizes table column configuration, creation, and row management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QAction
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMenu,
    QWidget,
)

if TYPE_CHECKING:
    from models import Product

__all__ = [
    "ColumnConfig",
    "COLUMN_CONFIGS",
    "DEFAULT_HIDDEN_COLUMNS",
    "TableManager",
]


@dataclass
class ColumnConfig:
    """Configuration for a table column."""
    name: str
    width: int = 100
    editable: bool = False
    hidden_by_default: bool = False
    sortable: bool = True
    tooltip: str = ""


# Column configurations - order matters for display
COLUMN_CONFIGS: list[ColumnConfig] = [
    ColumnConfig("Select", width=55, editable=False, tooltip="Check to include in batch operations"),
    ColumnConfig("Family", width=140, editable=False, tooltip="Product family - groups products with same cross-refs"),
    ColumnConfig("SKU", width=120, editable=True, tooltip="Product SKU (editable)"),
    ColumnConfig("Title", width=250, editable=False, tooltip="Product title"),
    ColumnConfig("Manufacturer", width=100, editable=False),
    ColumnConfig("Supplier", width=80, editable=False, tooltip="Cost supplier (HHP or PAI)"),
    ColumnConfig("Handle", width=120, editable=False, hidden_by_default=True),
    ColumnConfig("Primary PN", width=100, editable=False),
    ColumnConfig("Secondary PN", width=100, editable=False, hidden_by_default=True),
    ColumnConfig("OEM PN", width=100, editable=False, hidden_by_default=True),
    ColumnConfig("PAI SKU", width=100, editable=False),
    ColumnConfig("PAI Status", width=80, editable=False),
    ColumnConfig("HHP price", width=80, editable=False, tooltip="Original HHP listing price"),
    ColumnConfig("HHP Margin", width=80, editable=False, hidden_by_default=True),
    ColumnConfig("ATL Price", width=85, editable=False, tooltip="ATL Diesel competitor price (Phase 2.5)"),
    ColumnConfig("Condition", width=80, editable=False),
    ColumnConfig("Core charge", width=80, editable=False),
    ColumnConfig("Images", width=60, editable=False, tooltip="Number of images"),
    ColumnConfig("Kit OK", width=60, editable=False, hidden_by_default=True),
    ColumnConfig("PAI Cost", width=80, editable=True, tooltip="PAI cost (editable; from Phase 3 enrichment)"),
    ColumnConfig("Selling Price", width=90, editable=True, tooltip="Calculated selling price (editable)"),
    ColumnConfig("My Margin", width=80, editable=True, tooltip="Your profit margin % (editable)"),
    ColumnConfig("PAI Cross", width=80, editable=False, hidden_by_default=True),
    ColumnConfig("PAI Link", width=60, editable=False, hidden_by_default=True),
    ColumnConfig("PAI Stock", width=100, editable=False),
    ColumnConfig("PAI Options", width=80, editable=False, hidden_by_default=True, tooltip="Alternate PAI SKUs"),
    ColumnConfig("PAI Alternates", width=100, editable=False, hidden_by_default=True),
    ColumnConfig("Use PAI Supplier", width=100, editable=False, hidden_by_default=True),
    ColumnConfig("Variant", width=80, editable=False, hidden_by_default=True, tooltip="Variant type (HP, E, 010, etc.)"),
    ColumnConfig("Category", width=120, editable=False),
    ColumnConfig("Template", width=100, editable=False, hidden_by_default=True),
    ColumnConfig("Part Review", width=80, editable=False, hidden_by_default=True),
    ColumnConfig("Update Status", width=90, editable=False, hidden_by_default=True),
    ColumnConfig("Scraped At", width=140, editable=False, hidden_by_default=True),
    ColumnConfig("In Shopify", width=85, editable=False, tooltip="✅ Active, 📝 Draft, or empty if not in Shopify"),
    ColumnConfig("Stage", width=60, editable=False, tooltip="Workflow stage (🔍🧱🧪🧠🚀)"),
]

# Columns hidden by default (can be shown via toggle)
DEFAULT_HIDDEN_COLUMNS = {cfg.name for cfg in COLUMN_CONFIGS if cfg.hidden_by_default}


class TableManager:
    """Manages the products table widget."""

    def __init__(self, table: QTableWidget) -> None:
        self.table = table
        self._column_indices: dict[str, int] = {}
        self._hidden_columns: set[str] = set(DEFAULT_HIDDEN_COLUMNS)
        self._setup_table()

    def _setup_table(self) -> None:
        """Initialize table with column configurations."""
        self.table.setColumnCount(len(COLUMN_CONFIGS))
        
        headers = [cfg.name for cfg in COLUMN_CONFIGS]
        self.table.setHorizontalHeaderLabels(headers)
        
        # Build index map
        self._column_indices = {cfg.name: i for i, cfg in enumerate(COLUMN_CONFIGS)}
        
        # Configure columns
        for i, cfg in enumerate(COLUMN_CONFIGS):
            self.table.setColumnWidth(i, cfg.width)
            
            # Set tooltip on header
            header_item = self.table.horizontalHeaderItem(i)
            if header_item and cfg.tooltip:
                header_item.setToolTip(cfg.tooltip)
            
            # Hide if default hidden
            if cfg.name in self._hidden_columns:
                self.table.setColumnHidden(i, True)
        
        # Table behavior
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table.setSortingEnabled(True)
        
        # Performance optimizations
        try:
            self.table.setUniformRowHeights(True)
        except Exception:
            pass
        
        try:
            vh = self.table.verticalHeader()
            vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            vh.setDefaultSectionSize(24)
        except Exception:
            pass
        
        try:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        except Exception:
            pass
        
        try:
            hh = self.table.horizontalHeader()
            hh.setSortIndicatorShown(True)
            hh.setSectionsClickable(True)
        except Exception:
            pass
        
        # Style the "In Shopify" column header with a distinct background
        try:
            in_shopify_idx = self._column_indices.get("In Shopify", -1)
            if in_shopify_idx >= 0:
                header_item = self.table.horizontalHeaderItem(in_shopify_idx)
                if header_item:
                    from PySide6.QtGui import QBrush, QColor
                    header_item.setBackground(QBrush(QColor("#E8F5E9")))  # Light green
                    header_item.setForeground(QBrush(QColor("#2E7D32")))  # Dark green text
        except Exception:
            pass

    def column_index(self, name: str) -> int:
        """Get column index by name."""
        return self._column_indices.get(name, -1)

    def show_column(self, name: str) -> None:
        """Show a hidden column."""
        idx = self.column_index(name)
        if idx >= 0:
            self.table.setColumnHidden(idx, False)
            self._hidden_columns.discard(name)

    def hide_column(self, name: str) -> None:
        """Hide a column."""
        idx = self.column_index(name)
        if idx >= 0:
            self.table.setColumnHidden(idx, True)
            self._hidden_columns.add(name)

    def toggle_column(self, name: str) -> bool:
        """Toggle column visibility. Returns new visibility state."""
        idx = self.column_index(name)
        if idx < 0:
            return False
        
        is_hidden = self.table.isColumnHidden(idx)
        self.table.setColumnHidden(idx, not is_hidden)
        
        if is_hidden:
            self._hidden_columns.discard(name)
        else:
            self._hidden_columns.add(name)
        
        return is_hidden  # Returns True if now visible

    def show_advanced_columns(self, show: bool = True) -> None:
        """Show or hide all advanced (hidden by default) columns."""
        for cfg in COLUMN_CONFIGS:
            if cfg.hidden_by_default:
                idx = self.column_index(cfg.name)
                if idx >= 0:
                    self.table.setColumnHidden(idx, not show)
                    if show:
                        self._hidden_columns.discard(cfg.name)
                    else:
                        self._hidden_columns.add(cfg.name)

    def get_hidden_columns(self) -> set[str]:
        """Get set of currently hidden column names."""
        return set(self._hidden_columns)

    def create_item(
        self,
        text: str = "",
        *,
        editable: bool = False,
        checkable: bool = False,
        checked: bool = False,
        data: Any = None,
        sort_data: Any = None,
        tooltip: str = "",
        background: QColor | None = None,
    ) -> QTableWidgetItem:
        """Create a table item with common configurations."""
        item = QTableWidgetItem(text)
        
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        if checkable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        
        item.setFlags(flags)
        
        if data is not None:
            item.setData(Qt.ItemDataRole.UserRole, data)
        
        if sort_data is not None:
            item.setData(Qt.ItemDataRole.EditRole, sort_data)
        
        if tooltip:
            item.setToolTip(tooltip)
        
        if background is not None:
            item.setBackground(background)
        
        return item

    def set_row_background(self, row: int, color: QColor) -> None:
        """Set background color for entire row."""
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setBackground(color)

    def clear_selection(self) -> None:
        """Clear all row selections (checkboxes)."""
        select_col = self.column_index("Select")
        if select_col < 0:
            return
        
        for row in range(self.table.rowCount()):
            item = self.table.item(row, select_col)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        
        try:
            self.table.clearSelection()
        except Exception:
            pass

    def select_all(self) -> None:
        """Select all rows (checkboxes)."""
        select_col = self.column_index("Select")
        if select_col < 0:
            return
        
        for row in range(self.table.rowCount()):
            item = self.table.item(row, select_col)
            if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                item.setCheckState(Qt.CheckState.Checked)

    def get_checked_rows(self) -> list[int]:
        """Get list of row indices that are checked."""
        select_col = self.column_index("Select")
        if select_col < 0:
            return []
        
        checked = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, select_col)
            if item and item.checkState() == Qt.CheckState.Checked:
                checked.append(row)
        
        return checked

    def get_product_at_row(self, row: int) -> Any | None:
        """Get the product object stored in a row."""
        # Try Select column first
        select_col = self.column_index("Select")
        if select_col >= 0:
            item = self.table.item(row, select_col)
            if item:
                prod = item.data(Qt.ItemDataRole.UserRole)
                if prod is not None:
                    return prod
        
        # Try SKU column
        sku_col = self.column_index("SKU")
        if sku_col >= 0:
            item = self.table.item(row, sku_col)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        
        return None

    def filter_rows(self, query: str, stage_filter: int | None = None) -> int:
        """
        Filter table rows based on query and optional stage.
        Returns number of visible rows.
        """
        query = query.lower().strip()
        
        sku_col = self.column_index("SKU")
        title_col = self.column_index("Title")
        cat_col = self.column_index("Category")
        select_col = self.column_index("Select")
        
        visible_count = 0
        
        for row in range(self.table.rowCount()):
            # Get searchable text
            sku_txt = ""
            title_txt = ""
            cat_txt = ""
            
            if sku_col >= 0:
                item = self.table.item(row, sku_col)
                if item:
                    sku_txt = (item.text() or "").lower()
            
            if title_col >= 0:
                item = self.table.item(row, title_col)
                if item:
                    title_txt = (item.text() or "").lower()
            
            if cat_col >= 0:
                item = self.table.item(row, cat_col)
                if item:
                    cat_txt = (item.text() or "").lower()
            
            haystack = f"{sku_txt} {title_txt} {cat_txt}"
            
            # Query match
            matches_query = not query or query in haystack
            
            # Stage match
            matches_stage = True
            if stage_filter is not None:
                prod = self.get_product_at_row(row)
                if prod:
                    try:
                        pricing = getattr(prod, "pricing", {}) or {}
                        prod_stage = int(pricing.get("stage", 1) or 1)
                        matches_stage = (prod_stage == stage_filter)
                    except Exception:
                        matches_stage = True
            
            is_visible = matches_query and matches_stage
            self.table.setRowHidden(row, not is_visible)
            
            if is_visible:
                visible_count += 1
        
        return visible_count

    def create_context_menu(
        self,
        row: int,
        *,
        on_view_details: Callable[[int], None] | None = None,
        on_open_url: Callable[[int], None] | None = None,
        on_copy_sku: Callable[[int], None] | None = None,
        on_open_pai: Callable[[int], None] | None = None,
        on_select_alternates: Callable[[int], None] | None = None,
        on_adopt_cross_ref: Callable[[int], None] | None = None,
    ) -> QMenu:
        """Create a context menu for a table row."""
        menu = QMenu()
        
        if on_view_details:
            action = QAction("View Details...", menu)
            action.triggered.connect(lambda: on_view_details(row))
            menu.addAction(action)
        
        menu.addSeparator()
        
        if on_open_url:
            action = QAction("Open HHP URL", menu)
            action.triggered.connect(lambda: on_open_url(row))
            menu.addAction(action)
        
        if on_open_pai:
            action = QAction("Open PAI Link", menu)
            action.triggered.connect(lambda: on_open_pai(row))
            menu.addAction(action)
        
        menu.addSeparator()
        
        if on_copy_sku:
            action = QAction("Copy SKU", menu)
            action.triggered.connect(lambda: on_copy_sku(row))
            menu.addAction(action)
        
        if on_select_alternates:
            action = QAction("Select PAI Alternate...", menu)
            action.triggered.connect(lambda: on_select_alternates(row))
            menu.addAction(action)
        
        if on_adopt_cross_ref:
            action = QAction("🔀 Adopt PAI Cross-Reference...", menu)
            action.triggered.connect(lambda: on_adopt_cross_ref(row))
            menu.addAction(action)
        
        return menu
