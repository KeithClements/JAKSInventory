"""Shopify Management Screen - Dedicated UI for managing products on Shopify.

Features:
- View ONLY products that are already uploaded to Shopify (have shopify_id)
- Full sync / Quick sync (24h incremental)
- Price check (Phase 1 + 2.5 ATL style)
- Description regeneration via Grok API
- Push/Pull individual or bulk products
- Status management (active/draft/archived)
- Quick View panel with auto-scroll preview
"""

from __future__ import annotations

import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QGroupBox,
    QFormLayout,
    QScrollArea,
    QSizePolicy,
    QAbstractItemView,
    QMessageBox,
    QCheckBox,
    QTextBrowser,
    QMenu,
    QSpinBox,
    QDoubleSpinBox,
    QFileDialog,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon

from ui.theme import THEME_COLORS, get_status_badge_style

if TYPE_CHECKING:
    from models import Product


# Status icons and colors
STATUS_ICONS = {
    "active": ("🟢", "#28a745"),
    "draft": ("🟡", "#ffc107"),
    "archived": ("🔴", "#dc3545"),
    "unknown": ("⚪", "#6c757d"),
}


class ShopifyManagementScreen(QWidget):
    """Dedicated screen for managing Shopify products."""
    
    # Signals for main window to connect
    sync_full_requested = Signal()
    sync_quick_requested = Signal()  # Last 24h only
    check_prices_requested = Signal(list)  # Run Phase 1 + 2.5 for selected SKUs
    push_selected_requested = Signal(list)  # SKUs to push
    pull_selected_requested = Signal(list)  # SKUs to pull
    status_change_requested = Signal(list, str)  # SKUs, new_status
    regenerate_description_requested = Signal(list)  # SKU(s) for description regen
    inventory_adjusted = Signal(str, int, str, str)  # SKU, delta, txn_type, notes
    export_requested = Signal(str, list)  # format ('csv'/'json'), SKUs (empty for all)
    import_requested = Signal(str)  # file_path to import
    launch_inventory_program = Signal()  # Open jaks_inventory app
    back_requested = Signal()  # Go back to main screen
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._products: list[dict] = []
        self._current_product: Optional[dict] = None
        self._auto_scroll_timer: Optional[QTimer] = None
        self._auto_scroll_index = 0
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        """Build the screen layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header toolbar
        layout.addWidget(self._create_header_toolbar())
        
        # Main content area with splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        
        # Left panel: Product list
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel: Detail & Quick View
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        # Set initial sizes (60% left, 40% right)
        splitter.setSizes([600, 400])
        
        layout.addWidget(splitter)
        
        # Bottom: Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }}
            QProgressBar::chunk {{
                background-color: {THEME_COLORS['primary']};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(self._progress_bar)
    
    def _create_header_toolbar(self) -> QWidget:
        """Create the top toolbar with action buttons."""
        toolbar = QFrame()
        toolbar.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['header_bg']};
                border-bottom: 2px solid {THEME_COLORS['primary']};
            }}
            QPushButton {{
                background-color: {THEME_COLORS['panel']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['bg']};
                border-color: {THEME_COLORS['primary']};
            }}
            QPushButton:pressed {{
                background-color: {THEME_COLORS['primary']};
                color: white;
            }}
        """)
        toolbar.setFixedHeight(44)
        
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        
        # Back button
        back_btn = QPushButton("← Back")
        back_btn.clicked.connect(self.back_requested.emit)
        layout.addWidget(back_btn)
        
        layout.addSpacing(20)
        
        # Title
        title = QLabel("🛒 Shopify Management")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333; background: transparent; border: none;")
        layout.addWidget(title)
        
        layout.addStretch()
        
        # Full Sync button
        self._sync_full_btn = QPushButton("🔄 Full Sync")
        self._sync_full_btn.setToolTip("Sync all products from Shopify")
        self._sync_full_btn.clicked.connect(self.sync_full_requested.emit)
        layout.addWidget(self._sync_full_btn)
        
        # Quick Sync (24h)
        self._sync_quick_btn = QPushButton("⚡ Quick Sync")
        self._sync_quick_btn.setToolTip("Sync products modified in last 24 hours")
        self._sync_quick_btn.clicked.connect(self.sync_quick_requested.emit)
        layout.addWidget(self._sync_quick_btn)
        
        layout.addSpacing(10)
        
        # Price Check button
        self._check_prices_btn = QPushButton("💰 Check Prices Ph1+2.5")
        self._check_prices_btn.setToolTip("Check HHP prices for selected products (or all if none selected)")
        self._check_prices_btn.clicked.connect(self._on_check_prices)
        layout.addWidget(self._check_prices_btn)
        
        # Detect Conflicts button
        self._detect_conflicts_btn = QPushButton("🔍 Detect Conflicts")
        self._detect_conflicts_btn.setToolTip("Find products with local/Shopify differences")
        self._detect_conflicts_btn.clicked.connect(self._on_detect_conflicts)
        layout.addWidget(self._detect_conflicts_btn)
        
        layout.addSpacing(10)
        
        # Inventory Program button
        self._inventory_btn = QPushButton("📦 Inventory Program")
        self._inventory_btn.setToolTip("Launch the JAKS Inventory application")
        self._inventory_btn.clicked.connect(self.launch_inventory_program.emit)
        layout.addWidget(self._inventory_btn)
        
        return toolbar
    
    def _create_left_panel(self) -> QWidget:
        """Create the left panel with filters and product table."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Filter row
        filter_row = QHBoxLayout()
        
        # Search
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍 Search SKU, title...")
        self._search_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self._search_edit)
        
        # Category filter
        self._category_combo = QComboBox()
        self._category_combo.addItem("All Categories")
        self._category_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._category_combo)
        
        # Status filter
        self._status_combo = QComboBox()
        self._status_combo.addItems(["All Status", "🟢 Active", "🟡 Draft", "🔴 Archived"])
        self._status_combo.currentIndexChanged.connect(self._apply_filters)
        filter_row.addWidget(self._status_combo)
        
        layout.addLayout(filter_row)
        
        # Stats row
        self._stats_label = QLabel("0 products on Shopify")
        self._stats_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._stats_label)
        
        # Product table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Category", "Price", "Status", "Modified", "Shopify ID"
        ])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        
        layout.addWidget(self._table)
        
        # Bulk action bar
        bulk_bar = QHBoxLayout()
        
        self._select_all_btn = QPushButton("☐ Select All")
        self._select_all_btn.clicked.connect(self._table.selectAll)
        bulk_bar.addWidget(self._select_all_btn)
        
        self._push_btn = QPushButton("📤 Push Selected")
        self._push_btn.setToolTip("Push local changes to Shopify")
        self._push_btn.clicked.connect(self._on_push_selected)
        bulk_bar.addWidget(self._push_btn)
        
        self._pull_btn = QPushButton("📥 Pull Selected")
        self._pull_btn.setToolTip("Pull Shopify data to local")
        self._pull_btn.clicked.connect(self._on_pull_selected)
        bulk_bar.addWidget(self._pull_btn)
        
        bulk_bar.addStretch()
        
        self._selection_label = QLabel("0 selected")
        self._selection_label.setStyleSheet("color: #666;")
        bulk_bar.addWidget(self._selection_label)
        
        layout.addLayout(bulk_bar)
        
        return panel
    
    def _create_right_panel(self) -> QWidget:
        """Create the right panel with detail view and Quick View."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # ============ SHOPIFY STATUS PANEL ============
        status_group = QGroupBox("📊 Shopify Status")
        status_layout = QFormLayout(status_group)
        status_layout.setSpacing(6)
        
        # Status badge
        self._status_badge = QLabel("⚪ Unknown")
        self._status_badge.setStyleSheet("font-weight: bold; padding: 4px 8px; border-radius: 4px;")
        status_layout.addRow("Status:", self._status_badge)
        
        # Shopify ID
        self._shopify_id_label = QLabel("-")
        status_layout.addRow("Shopify ID:", self._shopify_id_label)
        
        # Last synced
        self._last_sync_label = QLabel("-")
        status_layout.addRow("Last Synced:", self._last_sync_label)
        
        # Action buttons row
        action_row = QHBoxLayout()
        
        self._regen_desc_btn = QPushButton("🔄 Regen Desc")
        self._regen_desc_btn.setToolTip("Regenerate AI description using Grok")
        self._regen_desc_btn.clicked.connect(self._on_regenerate_description)
        action_row.addWidget(self._regen_desc_btn)
        
        self._open_shopify_btn = QPushButton("🔗 Open Shopify")
        self._open_shopify_btn.setToolTip("Open this product in Shopify admin")
        self._open_shopify_btn.clicked.connect(self._on_open_shopify)
        action_row.addWidget(self._open_shopify_btn)
        
        action_row.addStretch()
        status_layout.addRow(action_row)
        
        layout.addWidget(status_group)
        
        # ============ PRICING PANEL ============
        pricing_group = QGroupBox("💰 Pricing")
        pricing_layout = QFormLayout(pricing_group)
        
        # Editable local price
        price_edit_row = QHBoxLayout()
        self._price_edit = QDoubleSpinBox()
        self._price_edit.setRange(0, 999999.99)
        self._price_edit.setDecimals(2)
        self._price_edit.setPrefix("$")
        self._price_edit.setToolTip("Edit local price - save and push to update Shopify")
        price_edit_row.addWidget(self._price_edit)
        
        self._price_save_btn = QPushButton("💾")
        self._price_save_btn.setFixedWidth(32)
        self._price_save_btn.setToolTip("Save price to database")
        self._price_save_btn.clicked.connect(self._on_save_price)
        price_edit_row.addWidget(self._price_save_btn)
        
        pricing_layout.addRow("Local Price:", price_edit_row)
        
        self._price_shopify = QLabel("-")
        pricing_layout.addRow("Shopify Price:", self._price_shopify)
        
        self._price_diff = QLabel("-")
        pricing_layout.addRow("Difference:", self._price_diff)
        
        layout.addWidget(pricing_group)
        
        # ============ INVENTORY PANEL ============
        inventory_group = QGroupBox("📦 Inventory Adjustment")
        inventory_layout = QFormLayout(inventory_group)
        
        # Current qty display
        self._inv_qty_label = QLabel("0")
        self._inv_qty_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        inventory_layout.addRow("Current Qty:", self._inv_qty_label)
        
        # Delta spinbox
        self._inv_delta_spin = QSpinBox()
        self._inv_delta_spin.setRange(-1000, 1000)
        self._inv_delta_spin.setValue(0)
        self._inv_delta_spin.setToolTip("Positive to add stock, negative to remove")
        inventory_layout.addRow("Adjustment:", self._inv_delta_spin)
        
        # Transaction type
        self._inv_type_combo = QComboBox()
        self._inv_type_combo.addItems(["receive", "sale", "adjust", "return", "damage", "transfer"])
        inventory_layout.addRow("Type:", self._inv_type_combo)
        
        # Notes
        self._inv_notes_edit = QLineEdit()
        self._inv_notes_edit.setPlaceholderText("Optional notes...")
        inventory_layout.addRow("Notes:", self._inv_notes_edit)
        
        # Apply button
        inv_btn_row = QHBoxLayout()
        self._inv_apply_btn = QPushButton("Apply Adjustment")
        self._inv_apply_btn.setToolTip("Apply inventory adjustment with audit trail")
        self._inv_apply_btn.clicked.connect(self._on_apply_inventory_adjustment)
        inv_btn_row.addWidget(self._inv_apply_btn)
        inv_btn_row.addStretch()
        inventory_layout.addRow(inv_btn_row)
        
        layout.addWidget(inventory_group)
        
        # ============ EXPORT/IMPORT PANEL ============
        export_group = QGroupBox("📁 Export / Import")
        export_layout = QVBoxLayout(export_group)
        
        export_btn_row = QHBoxLayout()
        
        self._export_csv_btn = QPushButton("📤 Export CSV")
        self._export_csv_btn.setToolTip("Export selected (or all) products to CSV")
        self._export_csv_btn.clicked.connect(lambda: self._on_export("csv"))
        export_btn_row.addWidget(self._export_csv_btn)
        
        self._export_json_btn = QPushButton("📤 Export JSON")
        self._export_json_btn.setToolTip("Export selected (or all) products to JSON")
        self._export_json_btn.clicked.connect(lambda: self._on_export("json"))
        export_btn_row.addWidget(self._export_json_btn)
        
        export_layout.addLayout(export_btn_row)
        
        import_btn_row = QHBoxLayout()
        
        self._import_btn = QPushButton("📥 Import Updates...")
        self._import_btn.setToolTip("Import product updates from CSV or JSON file")
        self._import_btn.clicked.connect(self._on_import)
        import_btn_row.addWidget(self._import_btn)
        import_btn_row.addStretch()
        
        export_layout.addLayout(import_btn_row)
        
        layout.addWidget(export_group)
        
        # ============ QUICK VIEW PANEL ============
        quick_view_group = QGroupBox("🛒 QUICK VIEW")
        quick_view_layout = QVBoxLayout(quick_view_group)
        
        # Image preview
        self._quick_image = QLabel()
        self._quick_image.setFixedSize(200, 200)
        self._quick_image.setAlignment(Qt.AlignCenter)
        self._quick_image.setStyleSheet("border: 1px solid #ddd; background: #f9f9f9;")
        self._quick_image.setText("No Image")
        quick_view_layout.addWidget(self._quick_image, alignment=Qt.AlignCenter)
        
        # Title preview
        self._quick_title = QLabel("-")
        self._quick_title.setWordWrap(True)
        self._quick_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        quick_view_layout.addWidget(self._quick_title)
        
        # Description preview (truncated)
        self._quick_desc = QTextBrowser()
        self._quick_desc.setMaximumHeight(100)
        self._quick_desc.setStyleSheet("border: 1px solid #ddd; background: #fafafa;")
        quick_view_layout.addWidget(self._quick_desc)
        
        # Navigation buttons
        nav_row = QHBoxLayout()
        
        self._prev_btn = QPushButton("◀ Previous")
        self._prev_btn.clicked.connect(self._on_prev_product)
        nav_row.addWidget(self._prev_btn)
        
        self._auto_scroll_check = QCheckBox("Auto-scroll")
        self._auto_scroll_check.setToolTip("Automatically cycle through products")
        self._auto_scroll_check.toggled.connect(self._on_auto_scroll_toggled)
        nav_row.addWidget(self._auto_scroll_check)
        
        self._next_btn = QPushButton("Next ▶")
        self._next_btn.clicked.connect(self._on_next_product)
        nav_row.addWidget(self._next_btn)
        
        quick_view_layout.addLayout(nav_row)
        
        layout.addWidget(quick_view_group)
        
        layout.addStretch()
        
        return panel
    
    def _connect_signals(self):
        """Connect internal signals."""
        pass
    
    # ============ PUBLIC METHODS ============
    
    def populate_from_database(self, products: list[dict]):
        """Load products into the table.
        
        NOTE: Only products with shopify_id should be passed here.
        """
        self._products = products
        self._refresh_table()
        self._update_stats()
        self._update_category_filter()
    
    def show_progress(self, value: int, message: str = ""):
        """Show or update progress bar."""
        self._progress_bar.setVisible(value < 100)
        self._progress_bar.setValue(value)
        if message:
            self._progress_bar.setFormat(f"{message} - %p%")
    
    def log_message(self, message: str):
        """Log a message (can be connected to status bar)."""
        # For now just print, main window can override
        print(f"[ShopifyMgmt] {message}")
    
    # ============ PRIVATE METHODS ============
    
    def _refresh_table(self):
        """Refresh the product table."""
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._products))
        
        for row, product in enumerate(self._products):
            # SKU
            sku = product.get("sku", "")
            self._table.setItem(row, 0, QTableWidgetItem(sku))
            
            # Title
            title = product.get("title", "")[:50]
            self._table.setItem(row, 1, QTableWidgetItem(title))
            
            # Category
            category = product.get("category", "")
            self._table.setItem(row, 2, QTableWidgetItem(category))
            
            # Price
            price = product.get("selling_price", 0)
            price_item = QTableWidgetItem(f"${price:,.2f}" if price else "-")
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, 3, price_item)
            
            # Status
            status = product.get("shopify_status", "draft")
            icon, color = STATUS_ICONS.get(status, STATUS_ICONS["unknown"])
            status_item = QTableWidgetItem(f"{icon} {status.title()}")
            status_item.setForeground(QColor(color))
            self._table.setItem(row, 4, status_item)
            
            # Modified (needs update flag)
            needs_update = product.get("needs_shopify_update", False)
            mod_text = "⚠️ Modified" if needs_update else "✓ Synced"
            mod_item = QTableWidgetItem(mod_text)
            if needs_update:
                mod_item.setForeground(QColor("#ffc107"))
            else:
                mod_item.setForeground(QColor("#28a745"))
            self._table.setItem(row, 5, mod_item)
            
            # Shopify ID
            shopify_id = product.get("shopify_id", "")
            self._table.setItem(row, 6, QTableWidgetItem(str(shopify_id) if shopify_id else "-"))
    
    def _update_stats(self):
        """Update the stats label."""
        total = len(self._products)
        active = sum(1 for p in self._products if p.get("shopify_status") == "active")
        draft = sum(1 for p in self._products if p.get("shopify_status") == "draft")
        modified = sum(1 for p in self._products if p.get("needs_shopify_update"))
        
        self._stats_label.setText(
            f"{total} products on Shopify | "
            f"🟢 {active} active | 🟡 {draft} draft | "
            f"⚠️ {modified} modified"
        )
    
    def _update_category_filter(self):
        """Populate category filter from products."""
        categories = sorted(set(p.get("category", "") for p in self._products if p.get("category")))
        self._category_combo.clear()
        self._category_combo.addItem("All Categories")
        self._category_combo.addItems(categories)
    
    def _apply_filters(self):
        """Apply search and filter to table."""
        search_text = self._search_edit.text().lower()
        category_filter = self._category_combo.currentText()
        status_filter = self._status_combo.currentText()
        
        for row in range(self._table.rowCount()):
            show = True
            
            # Search filter
            if search_text:
                sku = (self._table.item(row, 0).text() or "").lower()
                title = (self._table.item(row, 1).text() or "").lower()
                if search_text not in sku and search_text not in title:
                    show = False
            
            # Category filter
            if show and category_filter != "All Categories":
                category = self._table.item(row, 2).text() or ""
                if category != category_filter:
                    show = False
            
            # Status filter
            if show and status_filter != "All Status":
                status_text = self._table.item(row, 4).text() or ""
                if status_filter[2:].strip().lower() not in status_text.lower():
                    show = False
            
            self._table.setRowHidden(row, not show)
    
    def _on_selection_changed(self):
        """Handle table selection change."""
        selected = self._table.selectedItems()
        selected_rows = set(item.row() for item in selected)
        count = len(selected_rows)
        
        self._selection_label.setText(f"{count} selected")
        
        # Update detail panel with first selected product
        if selected_rows:
            first_row = min(selected_rows)
            sku = self._table.item(first_row, 0).text()
            product = next((p for p in self._products if p.get("sku") == sku), None)
            if product:
                self._update_detail_panel(product)
    
    def _update_detail_panel(self, product: dict):
        """Update the right panel with product details."""
        self._current_product = product
        
        # Status badge
        status = product.get("shopify_status", "unknown")
        icon, color = STATUS_ICONS.get(status, STATUS_ICONS["unknown"])
        self._status_badge.setText(f"{icon} {status.title()}")
        self._status_badge.setStyleSheet(f"font-weight: bold; padding: 4px 8px; border-radius: 4px; background: {color}20; color: {color};")
        
        # Shopify ID
        shopify_id = product.get("shopify_id", "-")
        self._shopify_id_label.setText(str(shopify_id) if shopify_id else "-")
        
        # Last sync
        synced_at = product.get("shopify_synced_at", "")
        if synced_at:
            try:
                # Handle both datetime objects and strings
                if hasattr(synced_at, 'strftime'):
                    # Already a datetime object
                    self._last_sync_label.setText(synced_at.strftime("%Y-%m-%d %H:%M"))
                elif isinstance(synced_at, str):
                    dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
                    self._last_sync_label.setText(dt.strftime("%Y-%m-%d %H:%M"))
                else:
                    self._last_sync_label.setText(str(synced_at)[:19])
            except Exception:
                self._last_sync_label.setText(str(synced_at)[:19] if synced_at else "-")
        else:
            self._last_sync_label.setText("-")
        
        # Pricing - use editable spin box
        local_price = product.get("selling_price", 0) or 0
        shopify_price = product.get("shopify_price", local_price) or 0
        
        self._price_edit.blockSignals(True)
        self._price_edit.setValue(float(local_price))
        self._price_edit.blockSignals(False)
        
        self._price_shopify.setText(f"${shopify_price:,.2f}" if shopify_price else "-")
        
        diff = local_price - shopify_price if local_price and shopify_price else 0
        if abs(diff) > 0.01:
            color = "#28a745" if diff > 0 else "#dc3545"
            self._price_diff.setText(f"{'+'if diff > 0 else ''}{diff:,.2f}")
            self._price_diff.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            self._price_diff.setText("No difference")
            self._price_diff.setStyleSheet("color: #666;")
        
        # Inventory qty
        qty = product.get("qty_on_hand", 0) or 0
        self._inv_qty_label.setText(str(qty))
        
        # Quick View
        self._quick_title.setText(product.get("title", "-")[:100])
        
        desc = product.get("description_html", "")
        if desc:
            self._quick_desc.setHtml(desc[:500] + ("..." if len(desc) > 500 else ""))
        else:
            self._quick_desc.setText("No description")
        
        # Try to load image
        self._load_quick_image(product)
    
    def _load_quick_image(self, product: dict):
        """Load product image for Quick View."""
        # Try to find local image first
        sku = product.get("sku", "")
        handle = product.get("handle", "")
        
        # Check common image locations
        possible_paths = [
            Path("output") / "images" / handle,
            Path("output") / product.get("category", "") / product.get("manufacturer", "") / sku / "images",
        ]
        
        for base_path in possible_paths:
            if base_path.exists():
                images = list(base_path.glob("*.jpg")) + list(base_path.glob("*.png"))
                if images:
                    pixmap = QPixmap(str(images[0]))
                    if not pixmap.isNull():
                        self._quick_image.setPixmap(
                            pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                        return
        
        # Try to fetch from Shopify if we have shopify_id
        shopify_id = product.get("shopify_id")
        if shopify_id:
            self._fetch_shopify_image(shopify_id)
        else:
            self._quick_image.setText("No Image")
    
    def _fetch_shopify_image(self, shopify_id: int):
        """Fetch product image from Shopify API."""
        import os
        import requests
        from PySide6.QtCore import QByteArray
        
        try:
            store = os.getenv("SHOPIFY_STORE", "jaks-heavy-duty")
            token = os.getenv("SHOPIFY_ACCESS_TOKEN", os.getenv("ACCESS_TOKEN", ""))
            
            if not token:
                self._quick_image.setText("No API Token")
                return
            
            url = f"https://{store}.myshopify.com/admin/api/2024-01/products/{shopify_id}/images.json"
            headers = {"X-Shopify-Access-Token": token}
            
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                images = data.get("images", [])
                if images:
                    # Get the first image
                    img_url = images[0].get("src", "")
                    if img_url:
                        # Fetch the actual image
                        img_resp = requests.get(img_url, timeout=10)
                        if img_resp.status_code == 200:
                            pixmap = QPixmap()
                            pixmap.loadFromData(QByteArray(img_resp.content))
                            if not pixmap.isNull():
                                self._quick_image.setPixmap(
                                    pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                )
                                return
            
            self._quick_image.setText("No Image")
        except Exception as e:
            print(f"Error fetching Shopify image: {e}")
            self._quick_image.setText("Load Error")
    
    def _get_selected_skus(self) -> list[str]:
        """Get list of selected SKUs."""
        selected_rows = set(item.row() for item in self._table.selectedItems())
        return [self._table.item(row, 0).text() for row in selected_rows if self._table.item(row, 0)]
    
    def _show_context_menu(self, pos):
        """Show context menu for product row."""
        item = self._table.itemAt(pos)
        if not item:
            return
        
        menu = QMenu(self)
        
        push_action = menu.addAction("📤 Push to Shopify")
        pull_action = menu.addAction("📥 Pull from Shopify")
        menu.addSeparator()
        regen_action = menu.addAction("🔄 Regenerate Description")
        menu.addSeparator()
        
        status_menu = menu.addMenu("Change Status")
        active_action = status_menu.addAction("🟢 Set Active")
        draft_action = status_menu.addAction("🟡 Set Draft")
        archive_action = status_menu.addAction("🔴 Archive")
        
        menu.addSeparator()
        open_action = menu.addAction("🔗 Open in Shopify")
        
        action = menu.exec_(self._table.viewport().mapToGlobal(pos))
        
        skus = self._get_selected_skus()
        if not skus:
            return
        
        if action == push_action:
            self.push_selected_requested.emit(skus)
        elif action == pull_action:
            self.pull_selected_requested.emit(skus)
        elif action == regen_action:
            self.regenerate_description_requested.emit(skus)
        elif action == active_action:
            self.status_change_requested.emit(skus, "active")
        elif action == draft_action:
            self.status_change_requested.emit(skus, "draft")
        elif action == archive_action:
            self.status_change_requested.emit(skus, "archived")
        elif action == open_action:
            self._on_open_shopify()
    
    # ============ SLOT HANDLERS ============
    
    def _on_push_selected(self):
        """Push selected products to Shopify."""
        skus = self._get_selected_skus()
        if skus:
            self.push_selected_requested.emit(skus)
        else:
            QMessageBox.information(self, "No Selection", "Please select products to push.")
    
    def _on_pull_selected(self):
        """Pull selected products from Shopify."""
        skus = self._get_selected_skus()
        if skus:
            self.pull_selected_requested.emit(skus)
        else:
            QMessageBox.information(self, "No Selection", "Please select products to pull.")
    
    def _on_check_prices(self):
        """Run price check on selected (or all) products."""
        skus = self._get_selected_skus()
        if not skus:
            # If nothing selected, check all
            skus = [p.get("sku") for p in self._products if p.get("sku")]
        self.check_prices_requested.emit(skus)
    
    def _on_regenerate_description(self):
        """Regenerate description for current product."""
        if self._current_product:
            self.regenerate_description_requested.emit([self._current_product.get("sku")])
        else:
            QMessageBox.information(self, "No Selection", "Please select a product first.")
    
    def _on_open_shopify(self):
        """Open current product in Shopify admin."""
        if not self._current_product:
            return
        
        shopify_id = self._current_product.get("shopify_id")
        if shopify_id:
            url = f"https://jaks-heavy-duty.myshopify.com/admin/products/{shopify_id}"
            webbrowser.open(url)
    
    def _on_prev_product(self):
        """Select previous product in list."""
        current_row = self._table.currentRow()
        if current_row > 0:
            self._table.selectRow(current_row - 1)
    
    def _on_next_product(self):
        """Select next product in list."""
        current_row = self._table.currentRow()
        if current_row < self._table.rowCount() - 1:
            self._table.selectRow(current_row + 1)
    
    def _on_auto_scroll_toggled(self, checked: bool):
        """Toggle auto-scroll through products."""
        if checked:
            self._auto_scroll_timer = QTimer(self)
            self._auto_scroll_timer.timeout.connect(self._auto_scroll_next)
            self._auto_scroll_timer.start(3000)  # 3 seconds per product
        else:
            if self._auto_scroll_timer:
                self._auto_scroll_timer.stop()
                self._auto_scroll_timer = None
    
    def _auto_scroll_next(self):
        """Auto-scroll to next product."""
        if self._table.rowCount() == 0:
            return
        
        self._auto_scroll_index = (self._auto_scroll_index + 1) % self._table.rowCount()
        self._table.selectRow(self._auto_scroll_index)
    
    def _on_save_price(self):
        """Save edited price to database and mark for Shopify update."""
        if not self._current_product:
            QMessageBox.warning(self, "No Selection", "Please select a product first.")
            return
        
        sku = self._current_product.get("sku")
        new_price = self._price_edit.value()
        old_price = self._current_product.get("selling_price", 0) or 0
        
        if abs(new_price - old_price) < 0.01:
            QMessageBox.information(self, "No Change", "Price has not been changed.")
            return
        
        # Save to database
        try:
            from db.database import get_connection
            conn = get_connection()
            now = datetime.now().isoformat()
            conn.execute(
                """UPDATE products 
                   SET selling_price = %s, needs_shopify_update = TRUE, updated_at = %s 
                   WHERE sku = %s""",
                (new_price, now, sku)
            )
            conn.commit()
            
            # Update local cache
            self._current_product["selling_price"] = new_price
            
            # Update table display
            for row in range(self._table.rowCount()):
                sku_item = self._table.item(row, 0)
                if sku_item and sku_item.text() == sku:
                    self._table.item(row, 3).setText(f"${new_price:,.2f}")
                    break
            
            # Update diff display
            shopify_price = self._current_product.get("shopify_price", 0) or 0
            diff = new_price - shopify_price
            if abs(diff) > 0.01:
                color = "#28a745" if diff > 0 else "#dc3545"
                self._price_diff.setText(f"{'+'if diff > 0 else ''}{diff:,.2f}")
                self._price_diff.setStyleSheet(f"color: {color}; font-weight: bold;")
            else:
                self._price_diff.setText("No difference")
                self._price_diff.setStyleSheet("color: #666;")
            
            QMessageBox.information(
                self, 
                "Price Saved", 
                f"Price updated: ${old_price:,.2f} → ${new_price:,.2f}\n\nUse 📤 Push Selected to update Shopify."
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save price: {e}")
    
    def _on_apply_inventory_adjustment(self):
        """Apply inventory adjustment to current product."""
        if not self._current_product:
            QMessageBox.warning(self, "No Selection", "Please select a product first.")
            return
        
        sku = self._current_product.get("sku")
        delta = self._inv_delta_spin.value()
        
        if delta == 0:
            QMessageBox.information(self, "No Change", "Adjustment quantity is 0.")
            return
        
        txn_type = self._inv_type_combo.currentText()
        notes = self._inv_notes_edit.text().strip()
        
        # Emit signal to handle in main window thread
        self.inventory_adjusted.emit(sku, delta, txn_type, notes)
        
        # Reset inputs
        self._inv_delta_spin.setValue(0)
        self._inv_notes_edit.clear()
    
    def _on_export(self, fmt: str):
        """Export products to CSV or JSON."""
        skus = self._get_selected_skus()
        
        # If no selection, export all
        if not skus:
            reply = QMessageBox.question(
                self,
                "Export All?",
                "No products selected. Export ALL products?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return
            skus = []  # Empty = all
        
        self.export_requested.emit(fmt, skus)
    
    def _on_import(self):
        """Open file dialog and import product updates."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Product Updates",
            "",
            "CSV Files (*.csv);;JSON Files (*.json);;All Files (*)",
        )
        
        if file_path:
            self.import_requested.emit(file_path)
    
    def update_inventory_display(self, qty: int):
        """Update the inventory qty label for current product."""
        self._inv_qty_label.setText(str(qty))
    
    def _on_detect_conflicts(self):
        """Detect and show products with local/Shopify differences."""
        from ui.conflict_resolution_dialog import ConflictResolutionDialog, BatchConflictDialog
        
        # Find products that have needs_shopify_update=True (indicating local changes)
        conflicts = []
        
        for product in self._products:
            if product.get("needs_shopify_update"):
                sku = product.get("sku", "")
                # For now, we only have local data - actual Shopify comparison
                # would require fetching from Shopify API
                local_data = product
                # Placeholder for Shopify data - in real implementation,
                # this would come from the sync worker
                shopify_data = {
                    "title": product.get("shopify_title", product.get("title")),
                    "selling_price": product.get("shopify_price", product.get("selling_price")),
                    "description_html": product.get("shopify_description", ""),
                    "shopify_status": product.get("shopify_status", "draft"),
                }
                conflicts.append((sku, local_data, shopify_data))
        
        if not conflicts:
            QMessageBox.information(
                self,
                "No Conflicts",
                "No products have pending local changes.\n\n"
                "Products with the 'Modified' flag will appear here."
            )
            return
        
        if len(conflicts) == 1:
            # Single conflict - show detailed dialog
            sku, local, shopify = conflicts[0]
            choice, data = ConflictResolutionDialog.resolve_conflict(
                sku, local, shopify, self
            )
            
            if choice == "local":
                self.push_selected_requested.emit([sku])
            elif choice == "shopify":
                self.pull_selected_requested.emit([sku])
            # 'merge' and 'skip' require no action
        else:
            # Multiple conflicts - show batch dialog
            dialog = BatchConflictDialog(conflicts, self)
            
            if dialog.exec():
                resolutions = dialog.get_resolutions()
                
                push_skus = [sku for sku, choice in resolutions.items() if choice == "local"]
                pull_skus = [sku for sku, choice in resolutions.items() if choice == "shopify"]
                
                if push_skus:
                    self.push_selected_requested.emit(push_skus)
                if pull_skus:
                    self.pull_selected_requested.emit(pull_skus)
