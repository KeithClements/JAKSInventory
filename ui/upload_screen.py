"""Upload Screen - Phase 4/5 UI for pricing, review, and Shopify upload.

Split-pane layout with:
- Left: Product list with filters and bulk actions
- Right: Detail panel with pricing editor, Shopify status, inventory sync
- Bottom: Upload controls and progress bar
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable
from decimal import Decimal
from pathlib import Path

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
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QProgressBar,
    QGroupBox,
    QFormLayout,
    QScrollArea,
    QSizePolicy,
    QAbstractItemView,
    QInputDialog,
    QMessageBox,
    QDialog,
    QTextBrowser,
    QGridLayout,
    QMenu,
)
from PySide6.QtCore import Qt, Signal, QSize, QUrl, QEvent, QTimer
from PySide6.QtGui import QFont, QColor, QIcon, QPixmap, QDesktopServices
from PySide6.QtWidgets import QApplication

from ui.theme import THEME_COLORS, STATUS_BADGE_COLORS, get_status_badge_style

# Categories and Manufacturers - same as scraper screen
DEFAULT_CATEGORIES = [
    "All Categories",
    "Cooling System",
    "Crankshaft Damper",
    "Cylinder Head Components",
    "Engine Parts",
    "Engine Rebuild Kits",
    "Exhaust System",
    "Fuel Injectors",
    "Fuel System",
    "Piston Cylinder Kit & Components",
    "Turbochargers",
]

DEFAULT_MANUFACTURERS = [
    "All Manufacturers",
    "Caterpillar",
    "Cummins",
    "Detroit Diesel",
    "International",
    "Mack",
    "Navistar",
    "PACCAR",
    "Volvo",
]

if TYPE_CHECKING:
    from models import Product


class CheckmarkWidget(QCheckBox):
    """A styled checkbox that shows a clear checkmark when checked."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QCheckBox {
                spacing: 0px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                border: 2px solid #888;
                border-radius: 3px;
                background-color: white;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #4A7A4A;
                border-radius: 3px;
                background-color: #4A7A4A;
            }
        """)


class CollapsibleSection(QWidget):
    """A collapsible section widget with header and expandable content."""
    
    def __init__(self, title: str, parent=None, initially_expanded: bool = True):
        super().__init__(parent)
        self._expanded = initially_expanded
        self._title = title
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header button (clickable to toggle)
        self._header = QPushButton()
        self._header.setCheckable(True)
        self._header.setChecked(self._expanded)
        self._update_header_text()
        self._header.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['header_bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 8px 12px;
                text-align: left;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['primary_hover']};
            }}
            QPushButton:checked {{
                border-bottom-left-radius: 0;
                border-bottom-right-radius: 0;
            }}
        """)
        self._header.clicked.connect(self._toggle)
        layout.addWidget(self._header)
        
        # Content container
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content.setStyleSheet(f"""
            QWidget {{
                background-color: {THEME_COLORS['bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-top: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
        """)
        self._content.setVisible(self._expanded)
        layout.addWidget(self._content)
    
    def _update_header_text(self):
        arrow = "▼" if self._expanded else "▶"
        self._header.setText(f"{arrow}  {self._title}")
    
    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._header.setChecked(self._expanded)
        self._update_header_text()
    
    def set_content(self, widget: QWidget):
        """Set the content widget for this section."""
        # Clear existing content
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self._content_layout.addWidget(widget)
    
    def add_widget(self, widget: QWidget):
        """Add a widget to the content area."""
        self._content_layout.addWidget(widget)
    
    def content_layout(self) -> QVBoxLayout:
        """Return the content layout for adding widgets."""
        return self._content_layout
    
    def set_expanded(self, expanded: bool):
        """Programmatically expand or collapse the section."""
        if self._expanded != expanded:
            self._toggle()


class UploadScreen(QWidget):
    """Main upload/publish screen for Phase 4/5 workflow."""
    
    # Signals
    back_requested = Signal()  # User clicked Back button
    upload_requested = Signal(list)  # Upload selected SKUs
    upload_all_requested = Signal()  # Upload all ready products
    sync_shopify_requested = Signal()  # Legacy - kept for compatibility
    shopify_management_requested = Signal()  # Open Shopify Management Screen
    export_qbo_requested = Signal()
    check_qbo_requested = Signal()  # Check QBO duplicates
    save_to_db_requested = Signal(dict)  # Save products to database
    preview_requested = Signal(str)  # Preview single SKU
    price_changed = Signal(str, float)  # SKU, new price
    cost_changed = Signal(str, float)  # SKU, new cost
    import_orphaned_requested = Signal(list)  # Import orphaned SKUs from Shopify
    push_modified_requested = Signal(list)  # Push modified SKUs to Shopify
    pull_from_shopify_requested = Signal(list)  # Pull Shopify values to local
    mass_margin_requested = Signal(float)  # Apply margin % to all selected
    
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._products: dict[str, Product] = {}
        self._selected_sku: str | None = None
        self._setup_ui()
    
    def eventFilter(self, obj, event):
        """Select all text in spinbox line edits when they receive focus."""
        if event.type() == QEvent.FocusIn:
            # Check if it's one of our spinbox line edits
            if hasattr(self, '_selling_price') and obj == self._selling_price.lineEdit():
                self._selling_price.lineEdit().selectAll()
                return False
            if hasattr(self, '_compare_price') and obj == self._compare_price.lineEdit():
                self._compare_price.lineEdit().selectAll()
                return False
            if hasattr(self, '_pai_cost_input') and obj == self._pai_cost_input.lineEdit():
                self._pai_cost_input.lineEdit().selectAll()
                return False
            if hasattr(self, '_margin_input') and obj == self._margin_input.lineEdit():
                self._margin_input.lineEdit().selectAll()
                return False
        return super().eventFilter(obj, event)
    
    def keyPressEvent(self, event):
        """Handle keyboard shortcuts for quick workflow."""
        from PySide6.QtCore import Qt as QtCore_Qt
        key = event.key()
        modifiers = event.modifiers()
        
        # Arrow Down / Page Down - Next product
        if key in (QtCore_Qt.Key_Down, QtCore_Qt.Key_PageDown, QtCore_Qt.Key_J):
            if modifiers == QtCore_Qt.NoModifier:
                self._select_next_product()
                return
        
        # Arrow Up / Page Up - Previous product
        if key in (QtCore_Qt.Key_Up, QtCore_Qt.Key_PageUp, QtCore_Qt.Key_K):
            if modifiers == QtCore_Qt.NoModifier:
                self._select_prev_product()
                return
        
        # Enter / R - Mark Ready and move to next
        if key in (QtCore_Qt.Key_Return, QtCore_Qt.Key_Enter, QtCore_Qt.Key_R):
            if modifiers == QtCore_Qt.NoModifier and self._mark_ready_btn.isEnabled():
                self._on_mark_ready()
                return
        
        # Space - Toggle checkbox on current product
        if key == QtCore_Qt.Key_Space:
            if modifiers == QtCore_Qt.NoModifier:
                self._toggle_current_checkbox()
                return
        
        # B - Beat competition (if button visible)
        if key == QtCore_Qt.Key_B:
            if modifiers == QtCore_Qt.NoModifier and hasattr(self, '_beat_competition_btn'):
                if self._beat_competition_btn.isVisible():
                    self._on_beat_competition()
                    return
        
        # P - Preview
        if key == QtCore_Qt.Key_P:
            if modifiers == QtCore_Qt.NoModifier:
                self._on_preview_upload()
                return
        
        super().keyPressEvent(event)
    
    def _select_next_product(self):
        """Select next product in table."""
        current_row = self._product_table.currentRow()
        if current_row < self._product_table.rowCount() - 1:
            self._product_table.selectRow(current_row + 1)
    
    def _select_prev_product(self):
        """Select previous product in table."""
        current_row = self._product_table.currentRow()
        if current_row > 0:
            self._product_table.selectRow(current_row - 1)
    
    def _toggle_current_checkbox(self):
        """Toggle checkbox on current row."""
        current_row = self._product_table.currentRow()
        if current_row >= 0:
            checkbox_item = self._product_table.item(current_row, 0)
            if checkbox_item:
                new_state = Qt.Unchecked if checkbox_item.checkState() == Qt.Checked else Qt.Checked
                checkbox_item.setCheckState(new_state)
    
    def _setup_ui(self):
        """Build the upload screen layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header bar
        layout.addWidget(self._create_header())
        
        # Toolbar
        layout.addWidget(self._create_toolbar())
        
        # Sync Results Panel (collapsible, hidden by default)
        self._sync_panel = self._create_sync_results_panel()
        self._sync_panel.setVisible(False)
        layout.addWidget(self._sync_panel)
        
        # Main content (splitter)
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setHandleWidth(1)
        self._splitter.setStyleSheet(f"QSplitter::handle {{ background: {THEME_COLORS['border']}; }}")
        
        # Left panel - product list
        self._splitter.addWidget(self._create_product_list_panel())
        
        # Right panel - detail view
        self._splitter.addWidget(self._create_detail_panel())
        
        # Set splitter sizes (60/40 split)
        self._splitter.setSizes([600, 400])
        layout.addWidget(self._splitter, 1)
        
        # Bottom controls
        layout.addWidget(self._create_upload_controls())
    
    def _create_header(self) -> QWidget:
        """Create the header bar with title and back button."""
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['primary']};
                border: none;
            }}
            QLabel {{
                color: white;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton {{
                background-color: transparent;
                color: white;
                border: 1px solid rgba(255,255,255,0.3);
                border-radius: 4px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: rgba(255,255,255,0.1);
            }}
        """)
        header.setFixedHeight(40)
        
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 0, 12, 0)
        
        # Back button
        self._back_btn = QPushButton("← Back to Data")
        self._back_btn.clicked.connect(self.back_requested.emit)
        layout.addWidget(self._back_btn)
        
        layout.addStretch()
        
        # Title
        title = QLabel("Upload & Publish")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        layout.addStretch()
        
        # Placeholder for symmetry
        spacer = QWidget()
        spacer.setFixedWidth(100)
        layout.addWidget(spacer)
        
        return header
    
    def _create_toolbar(self) -> QWidget:
        """Create the action toolbar."""
        toolbar = QFrame()
        toolbar.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['header_bg']};
                border-bottom: 1px solid {THEME_COLORS['border']};
            }}
            QPushButton {{
                background-color: {THEME_COLORS['panel']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['bg']};
                border-color: {THEME_COLORS['primary']};
            }}
        """)
        toolbar.setFixedHeight(36)
        
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(6)
        
        # Shopify Management button (replaces Sync Shopify - sync moved to management screen)
        self._shopify_mgmt_btn = QPushButton("🛒 Shopify Management")
        self._shopify_mgmt_btn.setToolTip("Open Shopify Management Screen (sync, price check, status changes)")
        self._shopify_mgmt_btn.clicked.connect(self.shopify_management_requested.emit)
        layout.addWidget(self._shopify_mgmt_btn)
        
        # Check QBO Duplicates
        self._check_qbo_btn = QPushButton("📋 Check QBO")
        self._check_qbo_btn.setToolTip("Check for duplicate SKUs in QuickBooks before export")
        self._check_qbo_btn.clicked.connect(self.check_qbo_requested.emit)
        layout.addWidget(self._check_qbo_btn)
        
        # Export QBO
        self._export_qbo_btn = QPushButton("💾 Export QBO")
        self._export_qbo_btn.clicked.connect(self.export_qbo_requested.emit)
        layout.addWidget(self._export_qbo_btn)
        
        # Save to Database
        self._save_db_btn = QPushButton("🗄️ Save to Database")
        self._save_db_btn.setToolTip("Sync all products to local SQLite database")
        self._save_db_btn.clicked.connect(self._on_save_to_database)
        layout.addWidget(self._save_db_btn)
        
        # Import Pricing
        self._import_btn = QPushButton("📥 Import Pricing")
        self._import_btn.setEnabled(False)  # TODO: Implement
        layout.addWidget(self._import_btn)
        
        # Preview Site
        self._preview_btn = QPushButton("👁 Preview Site")
        self._preview_btn.clicked.connect(self._on_preview_site)
        layout.addWidget(self._preview_btn)
        
        layout.addStretch()
        
        return toolbar
    
    def _create_sync_results_panel(self) -> QWidget:
        """Create collapsible panel showing Shopify sync results."""
        panel = QFrame()
        panel.setStyleSheet(f"""
            QFrame {{
                background-color: #f8f9fa;
                border-bottom: 2px solid {THEME_COLORS['border']};
            }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        
        # Header row with title and close button
        header_row = QHBoxLayout()
        
        self._sync_title = QLabel("🔄 Shopify Sync Results")
        self._sync_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #333;")
        header_row.addWidget(self._sync_title)
        
        header_row.addStretch()
        
        # Last sync time
        self._sync_time_label = QLabel("")
        self._sync_time_label.setStyleSheet("font-size: 11px; color: #666;")
        header_row.addWidget(self._sync_time_label)
        
        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 14px;
                color: #666;
            }
            QPushButton:hover {
                color: #333;
                background: #e0e0e0;
                border-radius: 12px;
            }
        """)
        close_btn.clicked.connect(lambda: self._sync_panel.setVisible(False))
        header_row.addWidget(close_btn)
        
        layout.addLayout(header_row)
        
        # Stats row with clickable cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        
        # Synced card (green)
        self._synced_card = self._create_sync_stat_card("✓ Synced", "0", "#28a745", "synced")
        stats_row.addWidget(self._synced_card)
        
        # Modified card (orange)
        self._modified_card = self._create_sync_stat_card("⚠ Modified", "0", "#fd7e14", "modified")
        stats_row.addWidget(self._modified_card)
        
        # New card (blue)
        self._new_card = self._create_sync_stat_card("🆕 Not Uploaded", "0", "#007bff", "new")
        stats_row.addWidget(self._new_card)
        
        # Orphaned card (gray)
        self._orphaned_card = self._create_sync_stat_card("⊗ Orphaned", "0", "#6c757d", "orphaned")
        stats_row.addWidget(self._orphaned_card)
        
        stats_row.addStretch()
        
        # Refresh button
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #a08620;
            }}
        """)
        refresh_btn.clicked.connect(self.sync_shopify_requested.emit)
        stats_row.addWidget(refresh_btn)
        
        layout.addLayout(stats_row)
        
        return panel
    
    def _create_sync_stat_card(self, title: str, count: str, color: str, filter_key: str) -> QFrame:
        """Create a clickable stat card for sync results."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: white;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                border-left: 4px solid {color};
            }}
            QFrame:hover {{
                background-color: #f5f5f5;
                border-color: {color};
            }}
        """)
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(60)
        card.setMinimumWidth(120)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        
        title_label = QLabel(title)
        title_label.setStyleSheet(f"font-size: 11px; color: #666; font-weight: 500;")
        layout.addWidget(title_label)
        
        count_label = QLabel(count)
        count_label.setObjectName(f"sync_count_{filter_key}")
        count_label.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color};")
        layout.addWidget(count_label)
        
        # Make card clickable to filter
        card.mousePressEvent = lambda e: self._filter_by_sync_status(filter_key)
        
        return card
    
    def _filter_by_sync_status(self, status: str):
        """Filter table by sync status when clicking a stat card.
        
        For modified/orphaned, shows a details dialog instead of filtering.
        """
        # For modified and orphaned, show details dialog
        if status in ("modified", "orphaned") and hasattr(self, '_sync_result') and self._sync_result:
            dialog = SyncDetailsDialog(self._sync_result, status, self)
            if status == "orphaned":
                dialog.import_orphaned.connect(self._on_import_orphaned)
            elif status == "modified":
                dialog.push_to_shopify.connect(self._on_push_to_shopify)
                dialog.pull_from_shopify.connect(self._on_pull_from_shopify)
            dialog.exec()
            return
        
        # For synced and new, filter the table
        filter_map = {
            "synced": "✓ Synced",
            "modified": "⚠ Modified",
            "new": "🆕 New",
            "orphaned": "All",  # Orphaned aren't in our local list
        }
        filter_text = filter_map.get(status, "All")
        idx = self._status_filter.findText(filter_text)
        if idx >= 0:
            self._status_filter.setCurrentIndex(idx)
    
    def _on_import_orphaned(self, skus: list[str]):
        """Handle import of orphaned products from Shopify."""
        # Emit signal so main window can handle the import
        self.import_orphaned_requested.emit(skus)
    
    def _on_push_to_shopify(self, skus: list[str]):
        """Handle push of modified products to Shopify."""
        # Emit signal so main window can handle the upload
        self.push_modified_requested.emit(skus)
    
    def _on_pull_from_shopify(self, skus: list[str]):
        """Handle pull of Shopify values to local (refresh/overwrite local)."""
        # Emit signal so main window can handle the pull
        self.pull_from_shopify_requested.emit(skus)
    
    def _update_sync_panel_counts(self, synced: int, modified: int, new: int, orphaned: int):
        """Update the sync panel stat card counts."""
        count_map = {
            "synced": synced,
            "modified": modified,
            "new": new,
            "orphaned": orphaned
        }
        for key, count in count_map.items():
            label = self.findChild(QLabel, f"sync_count_{key}")
            if label:
                label.setText(str(count))
    
    def _create_product_list_panel(self) -> QWidget:
        """Create the left panel with product list."""
        panel = QFrame()
        panel.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['panel']};
                border-right: 1px solid {THEME_COLORS['border']};
            }}
        """)
        
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Search box
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 Search SKU, title, part number...")
        self._search_input.textChanged.connect(self._apply_filters)
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                padding: 8px;
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                background-color: {THEME_COLORS['bg']};
            }}
            QLineEdit:focus {{
                border-color: {THEME_COLORS['primary']};
            }}
        """)
        search_row.addWidget(self._search_input)
        layout.addLayout(search_row)
        
        # Filters row
        filters = QHBoxLayout()
        filters.setSpacing(8)
        
        self._status_filter = QComboBox()
        self._status_filter.addItems(["All", "Ready", "Uploaded", "Draft", "---", "✓ Synced", "⚠ Modified", "🆕 New"])
        self._status_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(QLabel("Status:"))
        filters.addWidget(self._status_filter)
        
        self._category_filter = QComboBox()
        self._category_filter.addItem("All Categories")
        self._category_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(QLabel("Category:"))
        filters.addWidget(self._category_filter)
        
        self._manufacturer_filter = QComboBox()
        self._manufacturer_filter.addItem("All Manufacturers")
        self._manufacturer_filter.currentTextChanged.connect(self._apply_filters)
        filters.addWidget(QLabel("Manufacturer:"))
        filters.addWidget(self._manufacturer_filter)
        
        filters.addStretch()
        layout.addLayout(filters)
        
        # Product table
        self._product_table = QTableWidget()
        self._product_table.setColumnCount(10)
        self._product_table.setHorizontalHeaderLabels(["☑", "Family", "SKU", "Cost", "Price", "🚦", "Margin", "PAI Stock", "Status", "🏷"])
        self._product_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)  # Checkbox
        self._product_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Family
        self._product_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)  # SKU
        self._product_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Cost
        self._product_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Price
        self._product_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)  # Competition indicator
        self._product_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)  # Margin
        self._product_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)  # PAI Stock
        self._product_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)  # Status
        self._product_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Fixed)  # Tags
        self._product_table.setColumnWidth(0, 30)
        self._product_table.setColumnWidth(5, 40)  # Competition column width
        self._product_table.setColumnWidth(9, 40)  # Tags
        self._product_table.verticalHeader().setVisible(False)
        self._product_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._product_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._product_table.setSortingEnabled(True)  # Enable column sorting
        self._product_table.itemSelectionChanged.connect(self._on_product_selected)
        # Enable right-click context menu
        self._product_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._product_table.customContextMenuRequested.connect(self._show_context_menu)
        self._product_table.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
            }}
            QTableWidget::item:selected {{
                background-color: rgba(0, 123, 255, 0.1);
                color: {THEME_COLORS['text']};
                border-left: 3px solid {THEME_COLORS['primary']};
            }}
            QHeaderView::section {{
                background-color: {THEME_COLORS['header_bg']};
                padding: 6px;
                border: none;
                border-right: 1px solid {THEME_COLORS['border']};
                border-bottom: 1px solid {THEME_COLORS['border']};
            }}
            QHeaderView::section:hover {{
                background-color: {THEME_COLORS['primary_hover']};
            }}
        """)
        layout.addWidget(self._product_table, 1)
        
        # Select All checkbox + Summary row
        select_summary = QHBoxLayout()
        self._select_all_checkbox = QCheckBox("Select All")
        self._select_all_checkbox.setStyleSheet("font-weight: bold;")
        self._select_all_checkbox.stateChanged.connect(self._on_select_all_changed)
        select_summary.addWidget(self._select_all_checkbox)
        
        select_summary.addStretch()
        
        self._summary_label = QLabel("Selected: 0 | Total: 0 | Ready: 0")
        self._summary_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 12px;")
        select_summary.addWidget(self._summary_label)
        
        layout.addLayout(select_summary)
        
        # Bulk actions
        layout.addWidget(self._create_bulk_actions())
        
        return panel
    
    def _create_bulk_actions(self) -> QGroupBox:
        """Create bulk actions panel."""
        group = QGroupBox("Bulk Actions")
        group.setStyleSheet(f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 12px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }}
        """)
        
        layout = QHBoxLayout(group)
        layout.setSpacing(6)
        
        btn_style = f"""
            QPushButton {{
                background-color: {THEME_COLORS['bg']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {THEME_COLORS['primary']};
            }}
            QPushButton:disabled {{
                background-color: {THEME_COLORS['header_bg']};
                color: {THEME_COLORS['text_muted']};
            }}
        """
        
        self._set_price_btn = QPushButton("Set Price")
        self._set_price_btn.setStyleSheet(btn_style)
        self._set_price_btn.setEnabled(False)  # Disabled until selection
        self._set_price_btn.clicked.connect(self._on_set_price)
        layout.addWidget(self._set_price_btn)
        
        self._set_margin_btn = QPushButton("Set Margin %")
        self._set_margin_btn.setStyleSheet(btn_style)
        self._set_margin_btn.setEnabled(False)  # Disabled until selection
        self._set_margin_btn.clicked.connect(self._on_set_margin)
        layout.addWidget(self._set_margin_btn)
        
        self._plus_10_btn = QPushButton("+10%")
        self._plus_10_btn.setStyleSheet(btn_style)
        self._plus_10_btn.setEnabled(False)  # Disabled until selection
        self._plus_10_btn.clicked.connect(lambda: self._apply_markup(0.10))
        layout.addWidget(self._plus_10_btn)
        
        self._minus_10_btn = QPushButton("-10%")
        self._minus_10_btn.setStyleSheet(btn_style)
        self._minus_10_btn.setEnabled(False)  # Disabled until selection
        self._minus_10_btn.clicked.connect(lambda: self._apply_markup(-0.10))
        layout.addWidget(self._minus_10_btn)
        
        self._match_hhp_btn = QPushButton("Match HHP")
        self._match_hhp_btn.setStyleSheet(btn_style)
        self._match_hhp_btn.setEnabled(False)  # Disabled until selection
        self._match_hhp_btn.clicked.connect(self._on_match_hhp)
        layout.addWidget(self._match_hhp_btn)
        
        # Separator
        layout.addSpacing(10)
        
        # Tag and collection buttons
        self._add_tag_btn = QPushButton("🏷 Add Tag")
        self._add_tag_btn.setStyleSheet(btn_style)
        self._add_tag_btn.setEnabled(False)  # Disabled until selection
        self._add_tag_btn.setToolTip("Add a tag to selected products")
        self._add_tag_btn.clicked.connect(self._on_add_tag)
        layout.addWidget(self._add_tag_btn)
        
        self._add_collection_btn = QPushButton("📁 Add Collection")
        self._add_collection_btn.setStyleSheet(btn_style)
        self._add_collection_btn.setEnabled(False)  # Disabled until selection
        self._add_collection_btn.setToolTip("Assign selected products to a collection")
        self._add_collection_btn.clicked.connect(self._on_add_collection)
        layout.addWidget(self._add_collection_btn)
        
        return group
    
    def _create_detail_panel(self) -> QWidget:
        """Create the right panel with product details."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {THEME_COLORS['bg']};
                border: none;
            }}
        """)
        
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Product header
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        
        self._detail_header = QLabel("Select a product")
        self._detail_header.setStyleSheet(f"""
            font-size: 18px;
            font-weight: bold;
            color: {THEME_COLORS['text']};
        """)
        header_row.addWidget(self._detail_header)
        
        header_row.addStretch()
        
        # Copy Part Number button (without JAKS- prefix)
        self._copy_pn_btn = QPushButton("📋 Copy PN")
        self._copy_pn_btn.setToolTip("Copy part number without JAKS- prefix")
        self._copy_pn_btn.setFixedWidth(85)
        self._copy_pn_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #0056b3;
            }}
        """)
        self._copy_pn_btn.clicked.connect(self._on_copy_part_number)
        self._copy_pn_btn.setVisible(False)
        header_row.addWidget(self._copy_pn_btn)
        
        layout.addLayout(header_row)
        
        self._detail_subtitle = QLabel("")
        self._detail_subtitle.setStyleSheet(f"color: {THEME_COLORS['text_muted']};")
        layout.addWidget(self._detail_subtitle)
        
        # Collapsible sections for better UX
        # Images section - expanded by default
        self._images_section = CollapsibleSection("📷 Images", initially_expanded=True)
        self._images_section.set_content(self._create_image_section())
        layout.addWidget(self._images_section)
        
        # Pricing section - expanded by default
        self._pricing_section = CollapsibleSection("💰 Pricing", initially_expanded=True)
        self._pricing_section.set_content(self._create_pricing_section())
        layout.addWidget(self._pricing_section)
        
        # Market intel section - collapsed by default
        self._market_section = CollapsibleSection("📊 Market Intel", initially_expanded=False)
        self._market_section.set_content(self._create_market_intel_section())
        layout.addWidget(self._market_section)
        
        # Shopify status section - collapsed by default
        self._shopify_section = CollapsibleSection("🛒 Shopify Status", initially_expanded=False)
        self._shopify_section.set_content(self._create_shopify_section())
        layout.addWidget(self._shopify_section)
        
        # Inventory section - collapsed by default
        self._inventory_section = CollapsibleSection("📦 Inventory", initially_expanded=False)
        self._inventory_section.set_content(self._create_inventory_section())
        layout.addWidget(self._inventory_section)
        
        layout.addStretch()
        
        scroll.setWidget(panel)
        return scroll
    
    def _create_image_section(self) -> QWidget:
        """Create image thumbnails section with vendor folder tabs and selection."""
        container = QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['bg']};
                border: none;
            }}
        """)
        
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)
        
        # Large preview image at top
        self._preview_image_label = QLabel()
        self._preview_image_label.setFixedHeight(200)
        self._preview_image_label.setAlignment(Qt.AlignCenter)
        self._preview_image_label.setStyleSheet(f"""
            QLabel {{
                background-color: {THEME_COLORS['bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
            }}
        """)
        self._preview_image_label.setText("No image selected")
        main_layout.addWidget(self._preview_image_label)
        
        # Header row with folder buttons
        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        
        # Image source folder buttons
        self._image_source_buttons = {}
        folder_btn_style = f"""
            QPushButton {{
                background-color: {THEME_COLORS['bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['panel']};
                border-color: {THEME_COLORS['primary']};
            }}
            QPushButton:checked {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                border-color: {THEME_COLORS['primary']};
            }}
        """
        
        for source in ["HHP", "ATL", "PAI"]:
            btn = QPushButton(f"📁 {source}")
            btn.setCheckable(True)
            btn.setStyleSheet(folder_btn_style)
            btn.setToolTip(f"Show {source} images - click to filter")
            btn.clicked.connect(lambda checked, s=source: self._on_image_source_clicked(s))
            self._image_source_buttons[source] = btn
            header_row.addWidget(btn)
        
        header_row.addStretch()
        
        # Image count label
        self._image_count_label = QLabel("No images")
        self._image_count_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 11px;")
        header_row.addWidget(self._image_count_label)
        
        main_layout.addLayout(header_row)
        
        # Scrollable thumbnails area with grid layout
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFixedHeight(105)  # Enough for one row + scrollbar
        scroll_area.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollArea > QWidget > QWidget {{
                background: transparent;
            }}
        """)
        
        thumb_container = QWidget()
        thumb_layout = QHBoxLayout(thumb_container)
        thumb_layout.setSpacing(6)
        thumb_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create 30 thumbnail slots with checkboxes (supports larger product image sets)
        self._image_slots = []
        self._image_labels = []  # Keep for backward compat
        for i in range(30):
            slot = QFrame()
            slot.setFixedSize(70, 85)
            slot.setStyleSheet(f"""
                QFrame {{
                    background-color: {THEME_COLORS['bg']};
                    border: 1px solid {THEME_COLORS['border']};
                    border-radius: 4px;
                }}
                QFrame:hover {{
                    border-color: {THEME_COLORS['primary']};
                }}
            """)
            slot_layout = QVBoxLayout(slot)
            slot_layout.setContentsMargins(2, 2, 2, 2)
            slot_layout.setSpacing(2)
            
            # Image label - clickable
            img_label = QLabel()
            img_label.setFixedSize(64, 60)
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setStyleSheet("border: none;")
            img_label.setCursor(Qt.PointingHandCursor)
            # Store index for click handling
            img_label.setProperty("slot_index", i)
            img_label.mousePressEvent = lambda event, idx=i: self._on_thumbnail_clicked(idx)
            slot_layout.addWidget(img_label)
            
            # Checkbox for selection
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            checkbox.setStyleSheet("margin-left: 22px;")
            checkbox.setToolTip("Include in Shopify upload")
            slot_layout.addWidget(checkbox)
            
            slot.setVisible(False)
            thumb_layout.addWidget(slot)
            self._image_labels.append(img_label)
            self._image_slots.append({
                "frame": slot,
                "checkbox": checkbox,
                "label": img_label,
                "path": None,
                "source": None
            })
        
        thumb_layout.addStretch()
        scroll_area.setWidget(thumb_container)
        main_layout.addWidget(scroll_area)
        
        # Quick action row
        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        
        self._select_all_images_btn = QPushButton("✓ All")
        self._select_all_images_btn.setFixedWidth(50)
        self._select_all_images_btn.setToolTip("Select all images")
        self._select_all_images_btn.clicked.connect(self._on_select_all_images)
        action_row.addWidget(self._select_all_images_btn)
        
        self._select_none_images_btn = QPushButton("✗ None")
        self._select_none_images_btn.setFixedWidth(55)
        self._select_none_images_btn.setToolTip("Deselect all images")
        self._select_none_images_btn.clicked.connect(self._on_select_no_images)
        action_row.addWidget(self._select_none_images_btn)
        
        action_row.addStretch()
        
        self._image_source_label = QLabel("")
        self._image_source_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 10px;")
        action_row.addWidget(self._image_source_label)
        
        main_layout.addLayout(action_row)
        
        # Store current image source for display
        self._current_image_source = "ALL"
        
        return container
    
    def _on_select_all_images(self):
        """Select all visible images."""
        for slot in self._image_slots:
            if slot["frame"].isVisible():
                slot["checkbox"].setChecked(True)
    
    def _on_select_no_images(self):
        """Deselect all images."""
        for slot in self._image_slots:
            if slot["frame"].isVisible():
                slot["checkbox"].setChecked(False)
    
    def _on_thumbnail_clicked(self, slot_index: int):
        """Handle clicking on a thumbnail - show it in the preview."""
        if slot_index >= len(self._image_slots):
            return
        
        slot = self._image_slots[slot_index]
        img_path = slot.get("path")
        if not img_path or not Path(img_path).exists():
            return
        
        # Load and display in preview
        self._show_preview_image(img_path)
        
        # Highlight the selected thumbnail
        for i, s in enumerate(self._image_slots):
            if s["frame"].isVisible():
                if i == slot_index:
                    s["frame"].setStyleSheet(f"""
                        QFrame {{
                            background-color: {THEME_COLORS['bg']};
                            border: 2px solid {THEME_COLORS['primary']};
                            border-radius: 4px;
                        }}
                    """)
                else:
                    s["frame"].setStyleSheet(f"""
                        QFrame {{
                            background-color: {THEME_COLORS['bg']};
                            border: 1px solid {THEME_COLORS['border']};
                            border-radius: 4px;
                        }}
                        QFrame:hover {{
                            border-color: {THEME_COLORS['primary']};
                        }}
                    """)
    
    def _show_preview_image(self, img_path):
        """Display image in the large preview area."""
        try:
            pixmap = QPixmap(str(img_path))
            if not pixmap.isNull():
                # Scale to fit while maintaining aspect ratio
                scaled = pixmap.scaled(
                    self._preview_image_label.width() - 4,
                    self._preview_image_label.height() - 4,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self._preview_image_label.setPixmap(scaled)
            else:
                self._preview_image_label.setText("Failed to load image")
        except Exception as e:
            self._preview_image_label.setText(f"Error: {e}")
    
    def _on_image_source_clicked(self, source: str):
        """Handle clicking an image source folder button."""
        # Toggle behavior - clicking same source shows all
        if self._current_image_source == source:
            # Clicking already-selected source resets to ALL
            self._current_image_source = "ALL"
            for btn in self._image_source_buttons.values():
                btn.setChecked(False)
        else:
            # Select this source
            self._current_image_source = source
            for src, btn in self._image_source_buttons.items():
                btn.setChecked(src == source)
        
        # Reload images with new filter
        if self._selected_sku and self._selected_sku in self._products:
            product = self._products[self._selected_sku]
            self._load_product_images(product)
    
    def _create_pricing_section(self) -> QGroupBox:
        """Create pricing editor section."""
        group = QGroupBox("Pricing")
        group.setStyleSheet(self._group_style())
        
        layout = QFormLayout(group)
        layout.setSpacing(8)
        
        # PAI Cost - editable for override
        self._pai_cost_input = QDoubleSpinBox()
        self._pai_cost_input.setPrefix("$")
        self._pai_cost_input.setRange(0, 99999)
        self._pai_cost_input.setDecimals(2)
        self._pai_cost_input.setToolTip("PAI cost (editable to override) - Click to select all")
        self._pai_cost_input.valueChanged.connect(self._on_pai_cost_edited)
        self._pai_cost_input.lineEdit().installEventFilter(self)
        layout.addRow("PAI Cost:", self._pai_cost_input)
        
        # PAI Stock display
        self._pai_stock_label = QLabel("—")
        self._pai_stock_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']};")
        self._pai_stock_label.setToolTip("PAI stock availability")
        layout.addRow("PAI Stock:", self._pai_stock_label)
        
        self._hhp_price_label = QLabel("$0.00")
        layout.addRow("HHP Price:", self._hhp_price_label)
        
        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"background-color: {THEME_COLORS['border']};")
        layout.addRow(divider)
        
        # Editable fields
        self._selling_price = QDoubleSpinBox()
        self._selling_price.setPrefix("$")
        self._selling_price.setRange(0, 99999)
        self._selling_price.setDecimals(2)
        self._selling_price.setToolTip("Click to select all text and type new value")
        self._selling_price.valueChanged.connect(self._on_price_edited)
        # Make text selection easier - select all on focus
        self._selling_price.lineEdit().installEventFilter(self)
        layout.addRow("Selling Price:", self._selling_price)
        
        self._compare_price = QDoubleSpinBox()
        self._compare_price.setPrefix("$")
        self._compare_price.setRange(0, 99999)
        self._compare_price.setDecimals(2)
        self._compare_price.lineEdit().installEventFilter(self)
        layout.addRow("Compare At:", self._compare_price)
        
        # Margin % - editable
        self._margin_input = QDoubleSpinBox()
        self._margin_input.setSuffix("%")
        self._margin_input.setRange(0, 500)
        self._margin_input.setDecimals(1)
        self._margin_input.setSingleStep(5)
        self._margin_input.setToolTip("Set margin % (recalculates selling price) - Click to select all")
        self._margin_input.valueChanged.connect(self._on_margin_edited)
        self._margin_input.lineEdit().installEventFilter(self)
        layout.addRow("Margin %:", self._margin_input)
        
        self._profit_label = QLabel("$0.00")
        layout.addRow("Profit:", self._profit_label)
        
        return group
    
    def _create_market_intel_section(self) -> QGroupBox:
        """Create market intel / competitor pricing section."""
        group = QGroupBox("📊 Market Intel")
        group.setStyleSheet(self._group_style())
        
        layout = QVBoxLayout(group)
        layout.setSpacing(6)
        
        # ATL Diesel pricing row
        atl_row = QHBoxLayout()
        atl_row.setSpacing(8)
        
        atl_label = QLabel("ATL Diesel:")
        atl_label.setStyleSheet("font-weight: bold;")
        atl_row.addWidget(atl_label)
        
        self._atl_price_label = QLabel("—")
        self._atl_price_label.setStyleSheet("font-size: 14px;")
        atl_row.addWidget(self._atl_price_label)
        
        atl_row.addStretch()
        
        self._atl_link_btn = QPushButton("🔗")
        self._atl_link_btn.setFixedSize(24, 24)
        self._atl_link_btn.setToolTip("Open ATL Diesel product page")
        self._atl_link_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; font-size: 14px; }
            QPushButton:hover { background: #e0e0e0; border-radius: 4px; }
        """)
        self._atl_link_btn.clicked.connect(self._on_open_atl_link)
        self._atl_link_btn.setVisible(False)
        atl_row.addWidget(self._atl_link_btn)
        
        layout.addLayout(atl_row)
        
        # Market position indicator (vs ATL) with Beat Competition button
        position_row = QHBoxLayout()
        position_row.setSpacing(8)
        
        self._market_position_label = QLabel("")
        self._market_position_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        position_row.addWidget(self._market_position_label)
        
        position_row.addStretch()
        
        # Beat Competition button - sets price to beat ATL by 1%
        self._beat_competition_btn = QPushButton("🏆 Beat")
        self._beat_competition_btn.setToolTip("Set price 1% below ATL Diesel")
        self._beat_competition_btn.setFixedWidth(70)
        self._beat_competition_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['success']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #1a7a1a;
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
            }}
        """)
        self._beat_competition_btn.clicked.connect(self._on_beat_competition)
        self._beat_competition_btn.setVisible(False)
        position_row.addWidget(self._beat_competition_btn)
        
        layout.addLayout(position_row)
        
        # HHP price comparison (reference)
        self._hhp_comparison_label = QLabel("")
        self._hhp_comparison_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 11px;")
        layout.addWidget(self._hhp_comparison_label)
        
        # Suggested price range
        self._suggested_price_label = QLabel("")
        self._suggested_price_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 11px;")
        layout.addWidget(self._suggested_price_label)
        
        # Manual ATL override row (for when auto-search finds wrong product)
        override_row = QHBoxLayout()
        override_row.setSpacing(4)
        
        self._manual_atl_btn = QPushButton("✏️ Manual ATL")
        self._manual_atl_btn.setToolTip("Manually set ATL price/URL if auto-search found wrong product")
        self._manual_atl_btn.setFixedWidth(100)
        self._manual_atl_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 10px;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['panel']};
                border-color: {THEME_COLORS['primary']};
            }}
        """)
        self._manual_atl_btn.clicked.connect(self._on_manual_atl_override)
        override_row.addWidget(self._manual_atl_btn)
        
        self._clear_atl_btn = QPushButton("🗑️")
        self._clear_atl_btn.setToolTip("Clear ATL data (wrong match)")
        self._clear_atl_btn.setFixedSize(24, 24)
        self._clear_atl_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['bg']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #ffdddd;
                border-color: {THEME_COLORS['danger']};
            }}
        """)
        self._clear_atl_btn.clicked.connect(self._on_clear_atl_data)
        override_row.addWidget(self._clear_atl_btn)
        
        override_row.addStretch()
        layout.addLayout(override_row)
        
        return group
    
    def _on_manual_atl_override(self):
        """Show dialog to manually enter ATL price and URL."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        pricing = getattr(product, "pricing", {}) or {}
        current_price = float(pricing.get("atl_price", 0) or 0)
        current_url = getattr(product, "atl_url", "") or ""
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Manual ATL Override")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # Instructions
        info = QLabel("Enter ATL Diesel price and URL manually.\nUse this when auto-search found wrong product.")
        info.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(info)
        
        form = QFormLayout()
        
        # Price input
        price_input = QDoubleSpinBox()
        price_input.setPrefix("$")
        price_input.setRange(0, 99999)
        price_input.setDecimals(2)
        price_input.setValue(current_price)
        form.addRow("ATL Price:", price_input)
        
        # URL input
        url_input = QLineEdit()
        url_input.setPlaceholderText("https://atldiesel.com/products/...")
        url_input.setText(current_url)
        form.addRow("ATL URL:", url_input)
        
        layout.addLayout(form)
        
        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(f"background-color: {THEME_COLORS['primary']}; color: white; font-weight: bold;")
        save_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(save_btn)
        
        layout.addLayout(btn_row)
        
        if dialog.exec() == QDialog.Accepted:
            # Save the manual values
            new_price = price_input.value()
            new_url = url_input.text().strip()
            
            pricing["atl_price"] = new_price
            pricing["atl_manual"] = True  # Flag as manually entered
            setattr(product, "pricing", pricing)
            setattr(product, "atl_url", new_url)
            
            # Refresh display
            self._update_detail_panel(product)
            self._update_table_row_for_sku(self._selected_sku)
            
            # Emit signal to persist
            self.price_changed.emit(self._selected_sku, self._get_selling_price(product))
    
    def _on_clear_atl_data(self):
        """Clear ATL data for current product (when wrong match)."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        reply = QMessageBox.question(
            self,
            "Clear ATL Data",
            "Clear ATL Diesel data for this product?\n\n"
            "Use this if auto-search matched the wrong product.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            pricing = getattr(product, "pricing", {}) or {}
            pricing["atl_price"] = 0
            pricing.pop("atl_manual", None)
            setattr(product, "pricing", pricing)
            setattr(product, "atl_url", "")
            setattr(product, "atl_sku", "")
            setattr(product, "atl_title", "")
            
            # Refresh display
            self._update_detail_panel(product)
            self._update_table_row_for_sku(self._selected_sku)

    def _on_open_atl_link(self):
        """Open ATL Diesel product page in browser."""
        if self._selected_sku and self._selected_sku in self._products:
            product = self._products[self._selected_sku]
            atl_url = getattr(product, "atl_url", None)
            if atl_url:
                QDesktopServices.openUrl(QUrl(atl_url))
    
    def _on_copy_part_number(self):
        """Copy part number to clipboard without JAKS- prefix."""
        if not self._selected_sku:
            return
        
        # Remove JAKS- prefix
        pn = self._selected_sku
        if pn.startswith("JAKS-"):
            pn = pn[5:]
        
        # Copy to clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(pn)
        
        # Visual feedback - briefly change button text
        self._copy_pn_btn.setText("✓ Copied!")
        QTimer.singleShot(1500, lambda: self._copy_pn_btn.setText("📋 Copy PN"))
    
    def _on_beat_competition(self):
        """Set price to beat both ATL Diesel and HHP by 1%."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        pricing = getattr(product, "pricing", {}) or {}
        atl_price = float(pricing.get("atl_price", 0) or 0)
        hhp_price = float(pricing.get("hhp_price", 0) or pricing.get("price", 0) or 0)
        
        # Find the lowest competitor price to beat
        competitor_prices = [p for p in [atl_price, hhp_price] if p > 0]
        if not competitor_prices:
            return
        
        lowest_competitor = min(competitor_prices)
        
        # Set price to 1% below the lowest competitor
        new_price = round(lowest_competitor * 0.99, 2)
        
        # Update selling price input (this triggers price update cascade)
        self._selling_price.setValue(new_price)
    
    def _create_shopify_section(self) -> QGroupBox:
        """Create Shopify status section."""
        group = QGroupBox("Shopify Status")
        group.setStyleSheet(self._group_style())
        
        layout = QFormLayout(group)
        layout.setSpacing(8)
        
        self._shopify_status = QLabel("Not on Shopify")
        layout.addRow("Status:", self._shopify_status)
        
        self._shopify_sync = QLabel("-")
        layout.addRow("Last Sync:", self._shopify_sync)
        
        self._shopify_uploaded = QLabel("-")
        layout.addRow("Uploaded:", self._shopify_uploaded)
        
        self._shopify_handle = QLabel("-")
        layout.addRow("Handle:", self._shopify_handle)
        
        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        
        self._view_shopify_btn = QPushButton("🛒 View on Shopify")
        self._view_shopify_btn.setEnabled(False)
        self._view_shopify_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['primary_hover']};
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
                color: #666666;
            }}
        """)
        self._view_shopify_btn.clicked.connect(self._on_view_shopify)
        btn_row.addWidget(self._view_shopify_btn)
        
        self._preview_upload_btn = QPushButton("👁 Preview")
        self._preview_upload_btn.setEnabled(False)
        self._preview_upload_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['primary_hover']};
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
                color: #666666;
            }}
        """)
        self._preview_upload_btn.clicked.connect(self._on_preview_upload)
        btn_row.addWidget(self._preview_upload_btn)
        
        layout.addRow("", btn_row)
        
        # Mark Ready button - advance stage to 4 (Ready for Upload)
        self._mark_ready_btn = QPushButton("✓ Ready for Upload")
        self._mark_ready_btn.setEnabled(False)
        self._mark_ready_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['success']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #1e7e34;
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
            }}
        """)
        self._mark_ready_btn.setToolTip("Advance product to Stage 4 (Ready) - confirms pricing is reviewed")
        self._mark_ready_btn.clicked.connect(self._on_mark_ready)
        layout.addRow("", self._mark_ready_btn)
        
        return group
    
    def _create_inventory_section(self) -> QGroupBox:
        """Create inventory sync section."""
        group = QGroupBox("Inventory Sync")
        group.setStyleSheet(self._group_style())
        
        layout = QFormLayout(group)
        layout.setSpacing(8)
        
        self._qbo_item = QLabel("-")
        layout.addRow("QBO Item:", self._qbo_item)
        
        self._qbo_cost = QLabel("-")
        layout.addRow("QBO Cost:", self._qbo_cost)
        
        self._sync_qbo_btn = QPushButton("Sync to QBO")
        self._sync_qbo_btn.setEnabled(False)
        layout.addRow("", self._sync_qbo_btn)
        
        return group
    
    def _create_upload_controls(self) -> QWidget:
        """Create bottom upload controls."""
        controls = QFrame()
        controls.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['header_bg']};
                border-top: 1px solid {THEME_COLORS['border']};
            }}
        """)
        controls.setFixedHeight(80)
        
        layout = QVBoxLayout(controls)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)
        
        # Options row
        options = QHBoxLayout()
        options.setSpacing(16)
        
        self._publish_now = QCheckBox("Publish immediately")
        self._publish_now.setChecked(True)
        options.addWidget(self._publish_now)
        
        self._include_core = QCheckBox("Include core charge")
        self._include_core.setChecked(True)
        options.addWidget(self._include_core)
        
        options.addWidget(QLabel("Default Markup:"))
        self._markup_spin = QSpinBox()
        self._markup_spin.setRange(0, 100)
        self._markup_spin.setValue(30)
        self._markup_spin.setSuffix("%")
        options.addWidget(self._markup_spin)
        
        options.addStretch()
        layout.addLayout(options)
        
        # Buttons and progress
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        
        self._upload_selected_btn = QPushButton("▶ Upload Selected (0)")
        self._upload_selected_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['primary']};
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['primary_hover']};
            }}
            QPushButton:disabled {{
                background-color: {THEME_COLORS['border']};
            }}
        """)
        self._upload_selected_btn.clicked.connect(self._on_upload_selected)
        bottom.addWidget(self._upload_selected_btn)
        
        self._upload_all_btn = QPushButton("▶ Upload All Ready (0)")
        self._upload_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['success']};
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3A6A3A;
            }}
            QPushButton:disabled {{
                background-color: {THEME_COLORS['border']};
            }}
        """)
        self._upload_all_btn.clicked.connect(self.upload_all_requested.emit)
        bottom.addWidget(self._upload_all_btn)
        
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {THEME_COLORS['panel']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                padding: 6px 14px;
                border-radius: 4px;
            }}
        """)
        self._cancel_btn.setEnabled(False)
        bottom.addWidget(self._cancel_btn)
        
        bottom.addSpacing(20)
        
        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                text-align: center;
                background-color: {THEME_COLORS['panel']};
            }}
            QProgressBar::chunk {{
                background-color: {THEME_COLORS['primary']};
                border-radius: 3px;
            }}
        """)
        bottom.addWidget(self._progress, 1)
        
        layout.addLayout(bottom)
        
        return controls
    
    def _group_style(self) -> str:
        """Return common group box style."""
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 6px;
                margin-top: 12px;
                padding: 12px;
                padding-top: 24px;
                background-color: {THEME_COLORS['panel']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }}
        """
    
    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────
    
    def set_products(self, products: dict[str, "Product"]):
        """Load products into the upload screen."""
        self._products = products
        self._update_category_filter()
        self._update_manufacturer_filter()
        self._refresh_product_table()
        self._update_summary()
    
    def get_checked_skus(self) -> list[str]:
        """Return list of checked SKUs."""
        skus = []
        for row in range(self._product_table.rowCount()):
            checkbox = self._product_table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                sku_item = self._product_table.item(row, 2)  # SKU column (after Family)
                if sku_item:
                    # Get original key stored in UserRole, fallback to display text
                    original_key = sku_item.data(Qt.UserRole)
                    skus.append(original_key if original_key else sku_item.text())
        return skus
    
    def set_progress(self, value: int, message: str = ""):
        """Update progress bar."""
        self._progress.setValue(value)
        if message:
            self._progress.setFormat(f"{value}% - {message}")
        else:
            self._progress.setFormat(f"{value}%")
    
    def update_sync_status(self, sync_result: dict):
        """
        Update UI with Shopify sync status.
        
        Args:
            sync_result: Dict with keys: synced, modified, not_uploaded, orphaned, details
        """
        if not sync_result:
            return
        
        # Store sync result for detail panel updates
        self._sync_result = sync_result
        
        # Update Status column in table with sync icons
        synced_skus = set(sync_result.get('synced', []))
        modified_skus = set(sync_result.get('modified', []))
        not_uploaded_skus = set(sync_result.get('not_uploaded', []))
        
        for row in range(self._product_table.rowCount()):
            sku_item = self._product_table.item(row, 2)  # SKU column (after Family)
            status_item = self._product_table.item(row, 8)  # Status column
            
            if not sku_item or not status_item:
                continue
            
            sku = str(sku_item.data(Qt.UserRole) or sku_item.text()).strip().upper()
            
            # Determine sync status
            current_text = status_item.text()
            # Remove old sync prefix if present
            for prefix in ["✓ ", "⚠ ", "⊕ ", "📤 ", "📝 ", "🆕 "]:
                if current_text.startswith(prefix):
                    current_text = current_text[len(prefix):]
                    break
            
            # Add new sync prefix
            if sku in synced_skus:
                new_text = "✓ " + current_text  # Synced with Shopify
                status_item.setForeground(QColor("#228B22"))  # Green
            elif sku in modified_skus:
                new_text = "⚠ " + current_text  # Modified from Shopify
                status_item.setForeground(QColor("#DAA520"))  # Gold
            elif sku in not_uploaded_skus:
                new_text = "🆕 " + current_text  # Not on Shopify
                status_item.setForeground(QColor("#4169E1"))  # Royal blue
            else:
                new_text = current_text
            
            status_item.setText(new_text)
        
        # Update summary labels if they exist
        if hasattr(self, '_sync_summary_label'):
            summary = (
                f"✓ Synced: {len(synced_skus)} | "
                f"⚠ Modified: {len(modified_skus)} | "
                f"🆕 New: {len(not_uploaded_skus)}"
            )
            self._sync_summary_label.setText(summary)
        
        # Update sync results panel with counts
        orphaned_skus = set(sync_result.get('orphaned', []))
        self._update_sync_panel_counts(
            synced=len(synced_skus),
            modified=len(modified_skus),
            new=len(not_uploaded_skus),
            orphaned=len(orphaned_skus)
        )
        
        # Show the sync results panel
        if hasattr(self, '_sync_panel'):
            self._sync_panel.setVisible(True)
        
        # Update last sync time
        if hasattr(self, '_last_sync_label'):
            from datetime import datetime
            self._last_sync_label.setText(f"Last sync: {datetime.now().strftime('%H:%M:%S')}")

    def set_uploading(self, uploading: bool):
        """Enable/disable UI during upload."""
        self._upload_selected_btn.setEnabled(not uploading)
        self._upload_all_btn.setEnabled(not uploading)
        self._cancel_btn.setEnabled(uploading)
        self._product_table.setEnabled(not uploading)
    
    # ─────────────────────────────────────────────────────────────────────
    # Internal handlers
    # ─────────────────────────────────────────────────────────────────────
    
    def _refresh_product_table(self):
        """Rebuild product table from products dict."""
        self._product_table.setSortingEnabled(False)  # Disable during population
        self._product_table.setRowCount(0)
        
        status_filter = self._status_filter.currentText()
        category_filter = self._category_filter.currentText()
        manufacturer_filter = self._manufacturer_filter.currentText()
        search_text = self._search_input.text().lower().strip()
        
        # Check if filtering by sync status
        sync_filter = None
        if status_filter in ("✓ Synced", "⚠ Modified", "🆕 New"):
            sync_filter = status_filter
        
        # Get sync result for sync-based filtering
        sync_result = getattr(self, '_sync_result', None) or {}
        synced_skus = set(sync_result.get('synced', []))
        modified_skus = set(sync_result.get('modified', []))
        not_uploaded_skus = set(sync_result.get('not_uploaded', []))
        
        # First pass: collect products that pass all filters
        filtered_products = []
        for sku, product in self._products.items():
            # Apply search filter
            if search_text:
                sku_lower = sku.lower()
                title_lower = (getattr(product, "title", "") or "").lower()
                primary_pn = (getattr(product, "primary_pn", "") or "").lower()
                pai_sku = (getattr(product, "pai_sku", "") or "").lower()
                
                if not any(search_text in field for field in [sku_lower, title_lower, primary_pn, pai_sku]):
                    continue
            
            # Apply sync status filter
            if sync_filter:
                sku_upper = str(sku).strip().upper()
                if sync_filter == "✓ Synced" and sku_upper not in synced_skus:
                    continue
                elif sync_filter == "⚠ Modified" and sku_upper not in modified_skus:
                    continue
                elif sync_filter == "🆕 New" and sku_upper not in not_uploaded_skus:
                    continue
            elif status_filter != "All" and status_filter != "---":
                # Apply status filter (stage-based)
                stage = self._get_stage(product)
                status = self._stage_to_status(stage)
                if status != status_filter:
                    continue
            
            # Apply category filter
            category = getattr(product, "category", "") or ""
            if category_filter != "All Categories" and category != category_filter:
                continue
            
            # Apply manufacturer filter
            manufacturer = getattr(product, "manufacturer", "") or ""
            if manufacturer_filter != "All Manufacturers" and manufacturer != manufacturer_filter:
                continue
            
            # Get family_id for sorting
            family_id = getattr(product, "family_id", "") or ""
            if not family_id:
                pricing_dict = getattr(product, "pricing", {}) or {}
                family_id = pricing_dict.get("family_id", "") or ""
            
            # Add to filtered list
            filtered_products.append((sku, product, family_id))
        
        # Sort by family_id (products with family first, grouped together), then by SKU
        def sort_key(item):
            sku, product, family_id = item
            # Products with family_id sort first, grouped by family
            # Products without family (—) sort to end
            if family_id and family_id.startswith("FAM-"):
                return (0, family_id, sku)  # Group by family
            else:
                return (1, "", sku)  # No family, sort by SKU
        
        filtered_products.sort(key=sort_key)
        
        # Now add products to table in sorted order
        for sku, product, family_id in filtered_products:
            row = self._product_table.rowCount()
            self._product_table.insertRow(row)
            
            # Checkbox - use custom checkmark widget for clear visual
            checkbox = CheckmarkWidget()
            checkbox.stateChanged.connect(self._update_summary)
            self._product_table.setCellWidget(row, 0, checkbox)
            
            # Family column - show FAM-ID if product belongs to a family
            family_display = family_id if family_id and family_id.startswith("FAM-") else "—"
            family_item = QTableWidgetItem(family_display)
            family_item.setTextAlignment(Qt.AlignCenter)
            if family_display != "—":
                family_item.setForeground(QColor("#1565C0"))  # Blue for family members
                family_item.setToolTip(f"Family: {family_id}")
            self._product_table.setItem(row, 1, family_item)
            
            # SKU - handle cases where SKU might be a URL (data quality issue)
            display_sku = sku
            if sku.startswith("http"):
                # Extract from URL or use product's sku field
                product_sku = getattr(product, "sku", "") or ""
                if product_sku and not product_sku.startswith("http"):
                    display_sku = product_sku
                else:
                    # Try to extract part number from URL
                    import re
                    match = re.search(r'/product/([^/-]+)', sku)
                    if match:
                        display_sku = f"JAKS-{match.group(1)}"
                    else:
                        display_sku = "⚠ Bad SKU"
            
            sku_item = QTableWidgetItem(display_sku)
            sku_item.setData(Qt.UserRole, sku)  # Store original key for lookup
            self._product_table.setItem(row, 2, sku_item)
            
            # Cost (PAI cost preferred, fallback to scraped cost)
            cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
            cost_item = QTableWidgetItem(f"${cost:.2f}")
            cost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if cost == 0:
                cost_item.setForeground(QColor("#cc0000"))  # Red if no cost
            self._product_table.setItem(row, 3, cost_item)
            
            # Price
            price = self._get_selling_price(product)
            price_item = QTableWidgetItem(f"${price:.2f}")
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._product_table.setItem(row, 4, price_item)
            
            # Competition indicator (🟢 beating / 🟠 close / 🔴 way off / ⚪ no data)
            pricing = getattr(product, "pricing", {}) or {}
            atl_price = float(pricing.get("atl_price", 0) or 0)
            comp_icon = "⚪"  # No data
            comp_tooltip = "No ATL price data"
            comp_sort_value = 999  # Sort no-data to end
            
            if atl_price > 0 and price > 0:
                # Calculate how our price compares to ATL
                price_diff_pct = ((price - atl_price) / atl_price) * 100
                
                if price < atl_price:
                    # We're cheaper - green (beating)
                    comp_icon = "🟢"
                    comp_tooltip = f"Beating ATL by ${atl_price - price:.2f} ({abs(price_diff_pct):.1f}% lower)"
                    comp_sort_value = 1
                elif price_diff_pct <= 5:
                    # Within 5% - orange (close)
                    comp_icon = "🟠"
                    comp_tooltip = f"Close to ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                    comp_sort_value = 2
                elif price_diff_pct <= 15:
                    # 5-15% higher - orange warning
                    comp_icon = "🟠"
                    comp_tooltip = f"Above ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                    comp_sort_value = 3
                else:
                    # Way off - red
                    comp_icon = "🔴"
                    comp_tooltip = f"Way above ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                    comp_sort_value = 4
            
            comp_item = QTableWidgetItem(comp_icon)
            comp_item.setTextAlignment(Qt.AlignCenter)
            comp_item.setToolTip(comp_tooltip)
            comp_item.setData(Qt.UserRole, comp_sort_value)  # For sorting
            self._product_table.setItem(row, 5, comp_item)
            
            # Margin % - calculate and color-code
            margin_pct = 0.0
            margin_icon = "⚠️"
            margin_color = "#cc0000"  # Red by default
            if cost > 0 and price > 0:
                margin_pct = ((price - cost) / price) * 100
                if margin_pct >= 30:
                    margin_icon = "✅"
                    margin_color = "#228B22"  # Green
                elif margin_pct >= 20:
                    margin_icon = "🟡"
                    margin_color = "#DAA520"  # Gold
                elif margin_pct > 0:
                    margin_icon = "🟠"
                    margin_color = "#FF8C00"  # Orange
                else:
                    margin_icon = "❌"
                    margin_color = "#cc0000"  # Red
            
            margin_text = f"{margin_icon} {margin_pct:.1f}%"
            margin_item = QTableWidgetItem(margin_text)
            margin_item.setTextAlignment(Qt.AlignCenter)
            margin_item.setData(Qt.UserRole, margin_pct)  # Store numeric for sorting
            margin_item.setForeground(QColor(margin_color))
            self._product_table.setItem(row, 6, margin_item)
            
            # PAI Stock
            pai_stock = getattr(product, "pai_stock", None) or pricing.get("pai_stock")
            stock_text = self._format_stock_display(pai_stock)
            stock_item = QTableWidgetItem(stock_text)
            stock_item.setTextAlignment(Qt.AlignCenter)
            if "In Stock" in stock_text or "Available" in stock_text:
                stock_item.setForeground(QColor("#228B22"))  # Green
            elif stock_text == "—" or "Out" in stock_text:
                stock_item.setForeground(QColor("#999999"))  # Gray
            self._product_table.setItem(row, 7, stock_item)
            
            # Status - compute from stage with colored badge
            stage = self._get_stage(product)
            status = self._stage_to_status(stage)
            status_item = QTableWidgetItem(self._status_icon(status) + " " + status)
            status_item.setTextAlignment(Qt.AlignCenter)
            # Apply status badge colors
            badge_colors = STATUS_BADGE_COLORS.get(status.lower(), STATUS_BADGE_COLORS["draft"])
            status_item.setForeground(QColor(badge_colors["text"]))
            status_item.setBackground(QColor(badge_colors["bg"]))
            self._product_table.setItem(row, 8, status_item)
            
            # Tags indicator (count)
            tags = getattr(product, "tags", []) or []
            tag_count = len(tags) if isinstance(tags, list) else 0
            tag_item = QTableWidgetItem(str(tag_count) if tag_count else "-")
            tag_item.setTextAlignment(Qt.AlignCenter)
            self._product_table.setItem(row, 9, tag_item)
        
        # Apply family cell merging for consecutive rows with same family
        self._merge_family_cells()
        
        self._product_table.setSortingEnabled(True)  # Re-enable after population
    
    def _merge_family_cells(self):
        """Merge cells in Family column for consecutive rows with same family_id."""
        if self._product_table.rowCount() == 0:
            return
        
        # First, collect family info and sort rows by family
        row_data = []
        for row in range(self._product_table.rowCount()):
            family_item = self._product_table.item(row, 1)
            family_id = family_item.text() if family_item else "—"
            row_data.append((row, family_id))
        
        # Clear any existing spans
        for row in range(self._product_table.rowCount()):
            self._product_table.setSpan(row, 1, 1, 1)
        
        # Find consecutive runs of same family and merge
        i = 0
        while i < len(row_data):
            current_family = row_data[i][1]
            
            # Skip single products without family
            if current_family == "—":
                i += 1
                continue
            
            # Find how many consecutive rows have same family
            span_count = 1
            while i + span_count < len(row_data) and row_data[i + span_count][1] == current_family:
                span_count += 1
            
            # If we found multiple rows with same family, merge them
            if span_count > 1:
                self._product_table.setSpan(i, 1, span_count, 1)
                # Update the cell to show family ID centered in merged area
                family_item = self._product_table.item(i, 1)
                if family_item:
                    family_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            
            i += span_count

    def _update_category_filter(self):
        """Update category filter dropdown with all available categories."""
        # Collect categories from products (for ones not in default list)
        product_cats = set()
        for product in self._products.values():
            cat = getattr(product, "category", "") or ""
            if cat:
                product_cats.add(cat)
        
        # Merge with default categories
        all_cats = set(DEFAULT_CATEGORIES[1:])  # Skip "All Categories"
        all_cats.update(product_cats)
        
        current = self._category_filter.currentText()
        self._category_filter.clear()
        self._category_filter.addItem("All Categories")
        for cat in sorted(all_cats):
            self._category_filter.addItem(cat)
        
        # Restore selection
        idx = self._category_filter.findText(current)
        if idx >= 0:
            self._category_filter.setCurrentIndex(idx)
    
    def _update_manufacturer_filter(self):
        """Update manufacturer filter dropdown with all available manufacturers."""
        # Collect manufacturers from products (for ones not in default list)
        product_mfrs = set()
        for product in self._products.values():
            mfr = getattr(product, "manufacturer", "") or ""
            if mfr:
                product_mfrs.add(mfr)
        
        # Merge with default manufacturers
        all_mfrs = set(DEFAULT_MANUFACTURERS[1:])  # Skip "All Manufacturers"
        all_mfrs.update(product_mfrs)
        
        current = self._manufacturer_filter.currentText()
        self._manufacturer_filter.clear()
        self._manufacturer_filter.addItem("All Manufacturers")
        for mfr in sorted(all_mfrs):
            self._manufacturer_filter.addItem(mfr)
        
        # Restore selection
        idx = self._manufacturer_filter.findText(current)
        if idx >= 0:
            self._manufacturer_filter.setCurrentIndex(idx)
    
    def _update_summary(self):
        """Update the summary label and enable/disable bulk action buttons."""
        selected = len(self.get_checked_skus())
        total = self._product_table.rowCount()
        
        ready = 0
        for product in self._products.values():
            stage = self._get_stage(product)
            if stage >= 4:
                ready += 1
        
        self._summary_label.setText(
            f"Selected: {selected} | Total: {total} | Ready: {ready}"
        )
        
        self._upload_selected_btn.setText(f"▶ Upload Selected ({selected})")
        # Always keep buttons enabled - let user click and show message if nothing selected
        self._upload_selected_btn.setEnabled(True)
        self._upload_all_btn.setText(f"▶ Upload All Ready ({ready})")
        self._upload_all_btn.setEnabled(True)
        
        # Enable/disable bulk action buttons based on selection
        has_selection = selected > 0
        self._set_price_btn.setEnabled(has_selection)
        self._set_margin_btn.setEnabled(has_selection)
        self._plus_10_btn.setEnabled(has_selection)
        self._minus_10_btn.setEnabled(has_selection)
        self._match_hhp_btn.setEnabled(has_selection)
        self._add_tag_btn.setEnabled(has_selection)
        self._add_collection_btn.setEnabled(has_selection)
    
    def _on_select_all_changed(self, state):
        """Handle Select All checkbox state change."""
        # In PySide6, stateChanged can pass int (2=checked, 0=unchecked) or Qt.CheckState
        # Handle both cases
        if isinstance(state, int):
            check_state = (state == 2)
        else:
            check_state = (state == Qt.CheckState.Checked)
        
        # Update all row checkboxes (both QCheckBox and CheckmarkWidget)
        for row in range(self._product_table.rowCount()):
            checkbox = self._product_table.cellWidget(row, 0)
            if checkbox and hasattr(checkbox, 'setChecked'):
                checkbox.blockSignals(True)  # Prevent triggering _update_summary for each
                checkbox.setChecked(check_state)
                checkbox.blockSignals(False)
        
        # Now update summary with the new state
        self._update_summary()
    
    def _apply_filters(self):
        """Apply filters to product table."""
        self._refresh_product_table()
        self._update_summary()
    
    def _on_product_selected(self):
        """Handle product selection in table."""
        rows = self._product_table.selectionModel().selectedRows()
        if not rows:
            self._selected_sku = None
            self._update_detail_panel(None)
            return
        
        row = rows[0].row()
        sku_item = self._product_table.item(row, 2)  # SKU column (after Family)
        if sku_item:
            # Get original key stored in UserRole, fallback to display text
            original_key = sku_item.data(Qt.UserRole)
            sku = original_key if original_key else sku_item.text()
            self._selected_sku = sku
            product = self._products.get(sku)
            self._update_detail_panel(product)
    
    def _show_context_menu(self, pos):
        """Show right-click context menu with edit options."""
        row = self._product_table.rowAt(pos.y())
        if row < 0:
            return
        
        sku_item = self._product_table.item(row, 2)  # SKU column
        if not sku_item:
            return
        
        original_key = sku_item.data(Qt.UserRole) or sku_item.text()
        product = self._products.get(original_key)
        if not product:
            return
        
        menu = QMenu(self)
        
        # Edit Product (full dialog)
        edit_action = menu.addAction("✏️ Edit Product...")
        edit_action.triggered.connect(lambda: self._show_edit_dialog(original_key))
        
        menu.addSeparator()
        
        # Quick edit actions
        change_sku_action = menu.addAction("🔤 Change SKU...")
        change_sku_action.triggered.connect(lambda: self._quick_change_sku(original_key))
        
        change_manufacturer_action = menu.addAction("🏭 Change Manufacturer...")
        change_manufacturer_action.triggered.connect(lambda: self._quick_change_manufacturer(original_key))
        
        change_category_action = menu.addAction("📁 Change Category...")
        change_category_action.triggered.connect(lambda: self._quick_change_category(original_key))
        
        menu.addSeparator()
        
        # PAI actions
        pai_sku = str(getattr(product, "pai_sku", "") or "").strip()
        if pai_sku:
            apply_pai_action = menu.addAction(f"🔀 Apply PAI SKU → JAKS-{pai_sku}")
            apply_pai_action.triggered.connect(lambda: self._apply_pai_sku(original_key, pai_sku))
        
        set_pai_cost_action = menu.addAction("💰 Set PAI Cost...")
        set_pai_cost_action.triggered.connect(lambda: self._quick_set_pai_cost(original_key))
        
        menu.addSeparator()
        
        # Pricing actions
        set_price_action = menu.addAction("💵 Set Selling Price...")
        set_price_action.triggered.connect(lambda: self._quick_set_price(original_key))
        
        set_margin_action = menu.addAction("📊 Set Margin %...")
        set_margin_action.triggered.connect(lambda: self._quick_set_margin(original_key))
        
        menu.addSeparator()
        
        # URL actions
        hhp_url = str(getattr(product, "url", "") or getattr(product, "source_url", "") or "").strip()
        if hhp_url:
            open_hhp_action = menu.addAction("🌐 Open HHP URL")
            open_hhp_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(hhp_url)))
        
        shopify_id = getattr(product, "shopify_id", None)
        handle = getattr(product, "handle", "") or ""
        if shopify_id or handle:
            open_shopify_action = menu.addAction("🛒 Open Shopify")
            open_shopify_action.triggered.connect(lambda: self._open_shopify_url(product))
        
        menu.addSeparator()
        
        # Copy actions
        copy_sku_action = menu.addAction("📋 Copy SKU")
        copy_sku_action.triggered.connect(lambda: QApplication.clipboard().setText(original_key))
        
        primary_pn = str(getattr(product, "primary_pn", "") or getattr(product, "primary_part_number", "") or "").strip()
        if primary_pn:
            copy_pn_action = menu.addAction(f"📋 Copy Part Number ({primary_pn})")
            copy_pn_action.triggered.connect(lambda: QApplication.clipboard().setText(primary_pn))
        
        menu.exec(self._product_table.viewport().mapToGlobal(pos))
    
    def _show_edit_dialog(self, sku: str):
        """Show full edit dialog for a product."""
        product = self._products.get(sku)
        if not product:
            return
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit Product: {sku}")
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        
        # Current values
        title = str(getattr(product, "title", "") or "").strip()
        manufacturer = str(getattr(product, "manufacturer", "") or "").strip()
        category = str(getattr(product, "category", "") or "").strip()
        primary_pn = str(getattr(product, "primary_pn", "") or getattr(product, "primary_part_number", "") or "").strip()
        pricing = getattr(product, "pricing", {}) or {}
        pai_cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or pricing.get("cost", 0) or 0)
        supplier = str(pricing.get("supplier", "") or "").strip()
        
        # Header
        header = QLabel(f"<b>{title[:80]}...</b>" if len(title) > 80 else f"<b>{title}</b>")
        header.setWordWrap(True)
        layout.addWidget(header)
        
        # Form
        form_group = QGroupBox("Product Fields")
        form_layout = QFormLayout(form_group)
        
        # SKU (editable)
        sku_edit = QLineEdit(sku)
        sku_edit.setPlaceholderText("JAKS-XXXXXX")
        form_layout.addRow("SKU:", sku_edit)
        
        # Manufacturer
        manufacturer_combo = QComboBox()
        manufacturer_combo.setEditable(True)
        manufacturer_combo.addItems(["", "Caterpillar", "Cummins", "Detroit Diesel", "International", "Mack", "Navistar", "Paccar", "Volvo"])
        manufacturer_combo.setCurrentText(manufacturer)
        form_layout.addRow("Manufacturer:", manufacturer_combo)
        
        # Category
        category_combo = QComboBox()
        category_combo.setEditable(True)
        category_combo.addItems(["", "Engine Rebuild Kits", "Engine Parts", "Cylinder Head Components", "Cooling System", "Fuel System", "Turbochargers"])
        category_combo.setCurrentText(category)
        form_layout.addRow("Category:", category_combo)
        
        # Primary Part Number
        pn_edit = QLineEdit(primary_pn)
        pn_edit.setPlaceholderText("e.g., 85138502")
        form_layout.addRow("Primary PN:", pn_edit)
        
        # Title
        title_edit = QLineEdit(title)
        title_edit.setMaxLength(200)
        form_layout.addRow("Title:", title_edit)
        
        # PAI Cost
        cost_spin = QDoubleSpinBox()
        cost_spin.setRange(0, 99999.99)
        cost_spin.setDecimals(2)
        cost_spin.setPrefix("$")
        cost_spin.setValue(pai_cost)
        form_layout.addRow("PAI Cost:", cost_spin)
        
        # Supplier
        supplier_edit = QLineEdit(supplier)
        supplier_edit.setPlaceholderText("e.g., PAI, McBee, OEM")
        form_layout.addRow("Supplier:", supplier_edit)
        
        layout.addWidget(form_group)
        
        # Buttons
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("💾 Save Changes")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        cancel_btn.clicked.connect(dialog.reject)
        
        def save_changes():
            new_sku = sku_edit.text().strip()
            new_manufacturer = manufacturer_combo.currentText().strip()
            new_category = category_combo.currentText().strip()
            new_pn = pn_edit.text().strip()
            new_title = title_edit.text().strip()
            new_cost = cost_spin.value()
            new_supplier = supplier_edit.text().strip()
            
            # Update product
            if new_title:
                setattr(product, "title", new_title)
            if new_manufacturer:
                setattr(product, "manufacturer", new_manufacturer)
            if new_category:
                setattr(product, "category", new_category)
            if new_pn:
                setattr(product, "primary_pn", new_pn)
                setattr(product, "primary_part_number", new_pn)
            
            # Update pricing
            if not hasattr(product, "pricing") or not isinstance(product.pricing, dict):
                product.pricing = {}
            product.pricing["cost"] = new_cost
            product.pricing["supplier"] = new_supplier
            product.pricing["user_edited"] = True
            setattr(product, "pai_cost", new_cost)
            setattr(product, "cost", new_cost)
            
            # Handle SKU change
            if new_sku and new_sku != sku:
                setattr(product, "sku", new_sku)
                # Update products dict with new key
                del self._products[sku]
                self._products[new_sku] = product
            
            # Refresh
            self._refresh_product_table()
            self._update_detail_panel(product)
            dialog.accept()
            QMessageBox.information(self, "Saved", f"Product {new_sku} updated successfully.")
        
        save_btn.clicked.connect(save_changes)
        dialog.exec()
    
    def _quick_change_sku(self, sku: str):
        """Quick change SKU dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        new_sku, ok = QInputDialog.getText(
            self, "Change SKU", f"Enter new SKU for {sku}:", text=sku
        )
        if ok and new_sku and new_sku != sku:
            setattr(product, "sku", new_sku)
            del self._products[sku]
            self._products[new_sku] = product
            self._refresh_product_table()
    
    def _quick_change_manufacturer(self, sku: str):
        """Quick change manufacturer dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        current = str(getattr(product, "manufacturer", "") or "").strip()
        items = ["Caterpillar", "Cummins", "Detroit Diesel", "International", "Mack", "Navistar", "Paccar", "Volvo"]
        idx = items.index(current) if current in items else 0
        
        new_mfr, ok = QInputDialog.getItem(
            self, "Change Manufacturer", "Select manufacturer:", items, idx, True
        )
        if ok and new_mfr:
            setattr(product, "manufacturer", new_mfr)
            self._refresh_product_table()
            self._update_detail_panel(product)
    
    def _quick_change_category(self, sku: str):
        """Quick change category dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        current = str(getattr(product, "category", "") or "").strip()
        items = ["Engine Rebuild Kits", "Engine Parts", "Cylinder Head Components", "Cooling System", "Fuel System", "Turbochargers"]
        idx = items.index(current) if current in items else 0
        
        new_cat, ok = QInputDialog.getItem(
            self, "Change Category", "Select category:", items, idx, True
        )
        if ok and new_cat:
            setattr(product, "category", new_cat)
            self._refresh_product_table()
            self._update_detail_panel(product)
    
    def _apply_pai_sku(self, sku: str, pai_sku: str):
        """Apply PAI SKU as the new JAKS SKU."""
        product = self._products.get(sku)
        if not product:
            return
        
        new_sku = f"JAKS-{pai_sku}"
        reply = QMessageBox.question(
            self, "Apply PAI SKU",
            f"Change SKU from {sku} to {new_sku}?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            setattr(product, "sku", new_sku)
            del self._products[sku]
            self._products[new_sku] = product
            self._refresh_product_table()
    
    def _quick_set_pai_cost(self, sku: str):
        """Quick set PAI cost dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        current = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
        new_cost, ok = QInputDialog.getDouble(
            self, "Set PAI Cost", f"Enter PAI cost for {sku}:",
            current, 0, 99999.99, 2
        )
        if ok:
            setattr(product, "pai_cost", new_cost)
            setattr(product, "cost", new_cost)
            if not hasattr(product, "pricing") or not isinstance(product.pricing, dict):
                product.pricing = {}
            product.pricing["cost"] = new_cost
            self._refresh_product_table()
            self._update_detail_panel(product)
    
    def _quick_set_price(self, sku: str):
        """Quick set selling price dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        current = self._get_selling_price(product)
        new_price, ok = QInputDialog.getDouble(
            self, "Set Selling Price", f"Enter selling price for {sku}:",
            current, 0, 99999.99, 2
        )
        if ok:
            if not hasattr(product, "pricing") or not isinstance(product.pricing, dict):
                product.pricing = {}
            product.pricing["selling_price"] = new_price
            product.pricing["price_override"] = new_price
            self._refresh_product_table()
            self._update_detail_panel(product)
    
    def _quick_set_margin(self, sku: str):
        """Quick set margin % dialog."""
        product = self._products.get(sku)
        if not product:
            return
        
        cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
        if cost <= 0:
            QMessageBox.warning(self, "No Cost", "Cannot set margin without a cost value.")
            return
        
        margin, ok = QInputDialog.getDouble(
            self, "Set Margin %", f"Enter margin % for {sku}:",
            30.0, 1.0, 90.0, 1
        )
        if ok:
            # Calculate price from margin: price = cost / (1 - margin/100)
            new_price = cost / (1 - margin / 100)
            if not hasattr(product, "pricing") or not isinstance(product.pricing, dict):
                product.pricing = {}
            product.pricing["selling_price"] = new_price
            product.pricing["price_override"] = new_price
            self._refresh_product_table()
            self._update_detail_panel(product)
    
    def _open_shopify_url(self, product):
        """Open Shopify product page."""
        handle = getattr(product, "handle", "") or ""
        if handle:
            url = f"https://www.jaksdiesel.com/products/{handle}"
            QDesktopServices.openUrl(QUrl(url))

    def _update_detail_panel(self, product: "Product | None"):
        """Update detail panel with product data."""
        if not product:
            self._detail_header.setText("Select a product")
            self._detail_subtitle.setText("")
            self._copy_pn_btn.setVisible(False)
            self._pai_cost_input.blockSignals(True)
            self._pai_cost_input.setValue(0)
            self._pai_cost_input.blockSignals(False)
            self._pai_stock_label.setText("—")
            self._hhp_price_label.setText("$0.00")
            self._selling_price.setValue(0)
            self._compare_price.setValue(0)
            self._margin_input.blockSignals(True)
            self._margin_input.setValue(0)
            self._margin_input.blockSignals(False)
            self._profit_label.setText("$0.00")
            self._clear_thumbnails()
            self._view_shopify_btn.setEnabled(False)
            self._preview_upload_btn.setEnabled(False)
            self._mark_ready_btn.setEnabled(False)
            self._beat_competition_btn.setVisible(False)
            # Reset market intel
            self._atl_price_label.setText("—")
            self._atl_price_label.setStyleSheet(f"font-size: 14px; color: {THEME_COLORS['text_muted']};")
            self._atl_link_btn.setVisible(False)
            self._market_position_label.setText("")
            self._hhp_comparison_label.setText("")
            self._suggested_price_label.setText("")
            return
        
        sku = getattr(product, "sku", "") or ""
        title = getattr(product, "title", "") or sku
        category = getattr(product, "category", "") or ""
        manufacturer = getattr(product, "manufacturer", "") or ""
        
        self._detail_header.setText(sku)
        self._detail_subtitle.setText(f"{manufacturer} • {category}\n{title}")
        self._copy_pn_btn.setVisible(True)
        
        # Load image thumbnails
        self._load_product_images(product)
        
        # Pricing - HHP price is in pricing dict, not top-level
        pricing = getattr(product, "pricing", {}) or {}
        pai_cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
        hhp_price = float(pricing.get("hhp_price", 0) or pricing.get("price", 0) or 0)
        selling = self._get_selling_price(product)
        compare = self._get_compare_price(product)
        
        # PAI Cost - editable
        self._pai_cost_input.blockSignals(True)
        self._pai_cost_input.setValue(pai_cost)
        self._pai_cost_input.blockSignals(False)
        
        # PAI Stock display
        pai_stock = getattr(product, "pai_stock", None) or pricing.get("pai_stock")
        stock_text = self._format_stock_display(pai_stock)
        self._pai_stock_label.setText(stock_text)
        if "In Stock" in stock_text or "Available" in stock_text:
            self._pai_stock_label.setStyleSheet(f"color: {THEME_COLORS['success']}; font-weight: bold;")
        elif stock_text == "—" or "Out" in stock_text:
            self._pai_stock_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']};")
        else:
            self._pai_stock_label.setStyleSheet("")
        
        self._hhp_price_label.setText(f"${hhp_price:.2f}")
        
        # Block signals while updating
        self._selling_price.blockSignals(True)
        self._compare_price.blockSignals(True)
        self._selling_price.setValue(selling)
        self._compare_price.setValue(compare)
        self._selling_price.blockSignals(False)
        self._compare_price.blockSignals(False)
        
        self._update_margin_display(pai_cost, selling)
        
        # Shopify status
        shopify_id = getattr(product, "shopify_id", None)
        if shopify_id:
            self._shopify_status.setText("✓ On Shopify")
            self._shopify_status.setStyleSheet(f"color: {THEME_COLORS['success']};")
            self._view_shopify_btn.setEnabled(True)
        else:
            self._shopify_status.setText("Not on Shopify")
            self._shopify_status.setStyleSheet("")
            self._view_shopify_btn.setEnabled(False)
        
        # Uploaded date
        uploaded_at = getattr(product, "uploaded_at", None) or pricing.get("uploaded_at")
        if uploaded_at:
            # Format the date nicely
            try:
                from datetime import datetime
                if isinstance(uploaded_at, str):
                    dt = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
                else:
                    dt = uploaded_at
                self._shopify_uploaded.setText(dt.strftime("%Y-%m-%d %H:%M"))
                self._shopify_uploaded.setStyleSheet(f"color: {THEME_COLORS['success']};")
            except Exception:
                self._shopify_uploaded.setText(str(uploaded_at))
        else:
            self._shopify_uploaded.setText("-")
            self._shopify_uploaded.setStyleSheet("")
        
        # Update Shopify sync status from last sync result
        sync_info = "-"
        if hasattr(self, '_sync_result') and self._sync_result:
            details = self._sync_result.get('details', {})
            sku_upper = str(sku).strip().upper()
            if sku_upper in details:
                detail = details[sku_upper]
                status = detail.get('status', '')
                if status == 'synced':
                    sync_info = "✓ Synced with Shopify"
                    self._shopify_sync.setStyleSheet(f"color: {THEME_COLORS['success']};")
                elif status == 'modified':
                    diffs = detail.get('diffs', [])
                    sync_info = f"⚠ Modified ({len(diffs)} diff{'s' if len(diffs) != 1 else ''})"
                    self._shopify_sync.setStyleSheet(f"color: {THEME_COLORS['warning']};")
                    # Enable view button if there's a shopify_id from sync
                    if detail.get('shopify_id'):
                        self._view_shopify_btn.setEnabled(True)
                elif status == 'not_uploaded':
                    sync_info = "🆕 Not yet on Shopify"
                    self._shopify_sync.setStyleSheet(f"color: {THEME_COLORS['primary']};")
            else:
                sync_info = "Not synced yet"
                self._shopify_sync.setStyleSheet("")
        else:
            self._shopify_sync.setStyleSheet("")
        
        self._shopify_sync.setText(sync_info)
        
        # Preview upload always available when product selected
        self._preview_upload_btn.setEnabled(True)
        
        handle = getattr(product, "handle", "") or "-"
        self._shopify_handle.setText(handle)
        
        # Mark Ready button - enable if stage < 4
        stage = self._get_stage(product)
        if stage >= 4:
            self._mark_ready_btn.setEnabled(False)
            self._mark_ready_btn.setText("✓ Already Ready")
        else:
            self._mark_ready_btn.setEnabled(True)
            self._mark_ready_btn.setText("✓ Ready for Upload")
        
        # Update Market Intel section with ATL pricing
        self._update_market_intel(product, pricing, hhp_price, selling)
    
    def _update_margin_display(self, cost: float, price: float):
        """Update margin input and profit labels."""
        if cost > 0 and price > 0:
            margin = ((price - cost) / price) * 100
            profit = price - cost
            
            color = THEME_COLORS['success'] if margin >= 20 else THEME_COLORS['warning'] if margin >= 10 else THEME_COLORS['danger']
            
            # Update margin input (block signals to avoid recursion)
            self._margin_input.blockSignals(True)
            self._margin_input.setValue(margin)
            self._margin_input.blockSignals(False)
            self._margin_input.setStyleSheet(f"color: {color}; font-weight: bold;")
            
            self._profit_label.setText(f"${profit:.2f}")
        else:
            self._margin_input.blockSignals(True)
            self._margin_input.setValue(0)
            self._margin_input.blockSignals(False)
            self._margin_input.setStyleSheet("")
            self._profit_label.setText("$0.00")
    
    def _update_market_intel(self, product: "Product", pricing: dict, hhp_price: float, selling: float):
        """Update Market Intel section with ATL pricing data."""
        atl_price = float(pricing.get("atl_price", 0) or 0)
        atl_url = getattr(product, "atl_url", None)
        
        # ATL Price display
        if atl_price > 0:
            self._atl_price_label.setText(f"${atl_price:.2f}")
            self._atl_price_label.setStyleSheet("font-size: 14px; font-weight: bold;")
            self._atl_link_btn.setVisible(bool(atl_url))
        else:
            self._atl_price_label.setText("—")
            self._atl_price_label.setStyleSheet(f"font-size: 14px; color: {THEME_COLORS['text_muted']};")
            self._atl_link_btn.setVisible(False)
        
        # Determine lowest competitor price to beat
        competitor_prices = [p for p in [atl_price, hhp_price] if p > 0]
        lowest_competitor = min(competitor_prices) if competitor_prices else 0
        lowest_source = "ATL" if atl_price > 0 and atl_price <= hhp_price else "HHP"
        
        # Market position - compare against LOWEST competitor
        if lowest_competitor > 0 and selling > 0:
            diff = selling - lowest_competitor
            diff_pct = (diff / lowest_competitor) * 100
            
            if selling < lowest_competitor:
                # Beating competition - hide beat button
                self._market_position_label.setText(f"🟢 JAKS ${selling:.2f} beats {lowest_source} by ${abs(diff):.2f} ({abs(diff_pct):.1f}%)")
                self._market_position_label.setStyleSheet(f"font-weight: bold; color: {THEME_COLORS['success']};")
                self._beat_competition_btn.setVisible(False)
            elif diff_pct <= 5:
                # Close - show beat button
                self._market_position_label.setText(f"🟠 JAKS ${selling:.2f} is ${diff:.2f} above {lowest_source} ({diff_pct:.1f}%)")
                self._market_position_label.setStyleSheet(f"font-weight: bold; color: #DAA520;")
                self._beat_competition_btn.setVisible(True)
            elif diff_pct <= 15:
                # Moderately higher - show beat button
                self._market_position_label.setText(f"🟠 JAKS ${selling:.2f} is ${diff:.2f} above {lowest_source} ({diff_pct:.1f}%)")
                self._market_position_label.setStyleSheet(f"font-weight: bold; color: #FF8C00;")
                self._beat_competition_btn.setVisible(True)
            else:
                # Way off - show beat button
                self._market_position_label.setText(f"🔴 JAKS ${selling:.2f} is ${diff:.2f} above {lowest_source} ({diff_pct:.1f}%)")
                self._market_position_label.setStyleSheet(f"font-weight: bold; color: {THEME_COLORS['danger']};")
                self._beat_competition_btn.setVisible(True)
        elif selling > 0:
            # No competitor data but have selling price
            self._market_position_label.setText(f"JAKS: ${selling:.2f} (no competitor data)")
            self._market_position_label.setStyleSheet(f"font-weight: bold; color: {THEME_COLORS['text']};")
            self._beat_competition_btn.setVisible(False)
        else:
            self._market_position_label.setText("")
            self._beat_competition_btn.setVisible(False)
        
        # HHP comparison line
        if hhp_price > 0:
            if selling > 0:
                hhp_diff = selling - hhp_price
                hhp_pct = (hhp_diff / hhp_price) * 100
                sign = "+" if hhp_diff >= 0 else ""
                status = "✓ beating" if hhp_diff < 0 else "above"
                self._hhp_comparison_label.setText(f"HHP: ${hhp_price:.2f} (JAKS {status} by {sign}${hhp_diff:.2f})")
            else:
                self._hhp_comparison_label.setText(f"HHP: ${hhp_price:.2f}")
        else:
            self._hhp_comparison_label.setText("")
        
        # Suggested price - beat the lowest competitor
        if lowest_competitor > 0:
            suggested = round(lowest_competitor * 0.99, 2)  # 1% below lowest
            self._suggested_price_label.setText(f"💡 Beat competition: ${suggested:.2f} (1% under {lowest_source})")
        else:
            self._suggested_price_label.setText("")

    def _format_stock_display(self, stock: object) -> str:
        """Format PAI stock info for display."""
        if not stock:
            return "—"
        if isinstance(stock, dict):
            # Check for special status
            if stock.get("_status") == "not_available":
                return "❌ Not Available"
            
            # Sum up stock from all warehouses
            total = 0
            in_stock_count = 0
            low_stock_count = 0
            out_stock_count = 0
            details = []
            
            for wh, val in stock.items():
                if wh.startswith("_"):  # Skip internal keys
                    continue
                if isinstance(val, int):
                    total += val
                    if val > 0:
                        details.append(f"{wh}: {val}")
                        in_stock_count += 1
                elif isinstance(val, str):
                    val_lower = val.lower()
                    if val_lower == "low_stock":
                        details.append(f"⚠️ {wh}")
                        low_stock_count += 1
                    elif val_lower in ("in_stock", "available"):
                        details.append(f"✓ {wh}")
                        in_stock_count += 1
                    elif val_lower == "out_of_stock":
                        out_stock_count += 1
            
            if total > 0:
                return f"In Stock ({total})"
            elif low_stock_count > 0 and in_stock_count == 0:
                return f"⚠️ Low Stock ({low_stock_count})"
            elif in_stock_count > 0:
                if low_stock_count > 0:
                    return f"In Stock ({in_stock_count}) ⚠️ Low ({low_stock_count})"
                return f"In Stock ({in_stock_count})"
            elif out_stock_count > 0:
                return "❌ Out of Stock"
            return "—"
        elif isinstance(stock, (int, float)):
            if stock > 0:
                return f"In Stock ({int(stock)})"
            return "Out of Stock"
        elif isinstance(stock, str):
            return stock
        return "—"
    
    def _on_pai_cost_edited(self):
        """Handle PAI cost edit from detail panel."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        new_cost = self._pai_cost_input.value()
        
        # Update the product
        setattr(product, "cost", new_cost)
        setattr(product, "pai_cost", new_cost)
        
        # Update pricing dict
        pricing = getattr(product, "pricing", {}) or {}
        pricing["cost"] = new_cost
        pricing["pai_cost_override"] = new_cost  # Mark as user override
        setattr(product, "pricing", pricing)
        
        # Recalculate margin display
        selling = self._selling_price.value()
        self._update_margin_display(new_cost, selling)
        
        # Emit signal for persistence
        self.cost_changed.emit(self._selected_sku, new_cost)
        
        # Update table row
        self._update_table_row_for_sku(self._selected_sku)
    
    def _on_margin_edited(self):
        """Handle margin % edit - recalculate selling price."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        margin_pct = self._margin_input.value()
        cost = self._pai_cost_input.value()
        
        if cost <= 0 or margin_pct <= 0:
            return
        
        # Calculate price from margin: price = cost / (1 - margin/100)
        # margin% = ((price - cost) / price) * 100
        # margin/100 = (price - cost) / price
        # margin/100 * price = price - cost
        # cost = price - margin/100 * price
        # cost = price * (1 - margin/100)
        # price = cost / (1 - margin/100)
        if margin_pct >= 100:
            margin_pct = 99  # Cap at 99%
        
        new_price = cost / (1 - margin_pct / 100)
        
        # Update selling price (will trigger _on_price_edited)
        self._selling_price.setValue(new_price)
    
    def _update_product_price(self, sku: str, new_price: float):
        """Update product price locally and emit signal."""
        product = self._products.get(sku)
        if product:
            # Update local product pricing dict
            pricing = getattr(product, "pricing", {}) or {}
            pricing["selling_price"] = new_price
            setattr(product, "pricing", pricing)
            # Emit signal to main.py for persistence
            self.price_changed.emit(sku, new_price)
            # Update the table row to reflect the change
            self._update_table_row_for_sku(sku)
    
    def _update_table_row_for_sku(self, sku: str):
        """Update a specific row in the table after price/stage change."""
        product = self._products.get(sku)
        if not product:
            return
        
        # Find the row with this SKU
        for row in range(self._product_table.rowCount()):
            item = self._product_table.item(row, 2)  # SKU column (after Family)
            if item and item.data(Qt.UserRole) == sku:
                # Update Cost column
                cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
                cost_item = self._product_table.item(row, 3)
                if cost_item:
                    cost_item.setText(f"${cost:.2f}")
                
                # Update Price column
                price = self._get_selling_price(product)
                price_item = self._product_table.item(row, 4)
                if price_item:
                    price_item.setText(f"${price:.2f}")
                
                # Update Competition column (col 5)
                pricing = getattr(product, "pricing", {}) or {}
                atl_price = float(pricing.get("atl_price", 0) or 0)
                comp_icon = "⚪"
                comp_tooltip = "No ATL price data"
                comp_sort_value = 999
                
                if atl_price > 0 and price > 0:
                    price_diff_pct = ((price - atl_price) / atl_price) * 100
                    if price < atl_price:
                        comp_icon = "🟢"
                        comp_tooltip = f"Beating ATL by ${atl_price - price:.2f} ({abs(price_diff_pct):.1f}% lower)"
                        comp_sort_value = 1
                    elif price_diff_pct <= 5:
                        comp_icon = "🟠"
                        comp_tooltip = f"Close to ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                        comp_sort_value = 2
                    elif price_diff_pct <= 15:
                        comp_icon = "🟠"
                        comp_tooltip = f"Above ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                        comp_sort_value = 3
                    else:
                        comp_icon = "🔴"
                        comp_tooltip = f"Way above ATL: +${price - atl_price:.2f} ({price_diff_pct:.1f}% higher)"
                        comp_sort_value = 4
                
                comp_item = self._product_table.item(row, 5)
                if comp_item:
                    comp_item.setText(comp_icon)
                    comp_item.setToolTip(comp_tooltip)
                    comp_item.setData(Qt.UserRole, comp_sort_value)
                
                # Update Margin column (col 6)
                margin_pct = 0.0
                margin_icon = "⚠️"
                margin_color = "#cc0000"
                if cost > 0 and price > 0:
                    margin_pct = ((price - cost) / price) * 100
                    if margin_pct >= 30:
                        margin_icon = "✅"
                        margin_color = "#228B22"
                    elif margin_pct >= 20:
                        margin_icon = "🟡"
                        margin_color = "#DAA520"
                    elif margin_pct > 0:
                        margin_icon = "🟠"
                        margin_color = "#FF8C00"
                    else:
                        margin_icon = "❌"
                        margin_color = "#cc0000"
                
                margin_item = self._product_table.item(row, 6)
                if margin_item:
                    margin_item.setText(f"{margin_icon} {margin_pct:.1f}%")
                    margin_item.setData(Qt.UserRole, margin_pct)
                    margin_item.setForeground(QColor(margin_color))
                
                # Update PAI Stock column (col 7)
                pai_stock = getattr(product, "pai_stock", None) or pricing.get("pai_stock")
                stock_text = self._format_stock_display(pai_stock)
                stock_item = self._product_table.item(row, 7)
                if stock_item:
                    stock_item.setText(stock_text)
                    if "In Stock" in stock_text:
                        stock_item.setForeground(QColor("#228B22"))
                    else:
                        stock_item.setForeground(QColor("#999999"))
                
                # Update Status column (col 8)
                stage = self._get_stage(product)
                status = self._stage_to_status(stage)
                status_item = self._product_table.item(row, 8)
                if status_item:
                    status_item.setText(self._status_icon(status) + " " + status)
                
                break
    
    def _on_save_to_database(self):
        """Save all products to SQLite database."""
        if not self._products:
            QMessageBox.information(self, "No Products", "No products to save.")
            return
        
        count = len(self._products)
        reply = QMessageBox.question(
            self,
            "Save to Database",
            f"Save {count} product(s) to local database?\n\n"
            "This will sync all product data including pricing, PAI info, and cross-references.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            self.save_to_db_requested.emit(self._products)
    
    def _on_price_edited(self):
        """Handle selling price edit."""
        if not self._selected_sku:
            return
        
        new_price = self._selling_price.value()
        product = self._products.get(self._selected_sku)
        if product:
            cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
            self._update_margin_display(cost, new_price)
            self._update_product_price(self._selected_sku, new_price)
    
    def _on_mark_ready(self):
        """Mark selected product as Ready (Stage 4), check the box, and move to next."""
        if not self._selected_sku:
            return
        
        product = self._products.get(self._selected_sku)
        if not product:
            return
        
        # Get current stage
        stage = self._get_stage(product)
        if stage >= 4:
            QMessageBox.information(self, "Already Ready", "Product is already at Stage 4+ (Ready/Uploaded).")
            return
        
        # Update stage to 4
        pricing = getattr(product, "pricing", {}) or {}
        pricing["stage"] = 4
        setattr(product, "pricing", pricing)
        
        # Check the checkbox for this product
        current_row = -1
        for row in range(self._product_table.rowCount()):
            item = self._product_table.item(row, 2)  # SKU column (after Family)
            if item and item.data(Qt.UserRole) == self._selected_sku:
                current_row = row
                # Check the checkbox in column 0
                checkbox_item = self._product_table.item(row, 0)
                if checkbox_item:
                    checkbox_item.setCheckState(Qt.Checked)
                break
        
        # Update table and detail panel
        self._update_table_row_for_sku(self._selected_sku)
        self._update_summary()
        
        # Update Mark Ready button state
        self._mark_ready_btn.setEnabled(False)
        self._mark_ready_btn.setText("✓ Ready for Upload")
        
        # Emit signal to persist the change
        self.price_changed.emit(self._selected_sku, self._get_selling_price(product))
        
        # Move to next product in table
        if current_row >= 0 and current_row < self._product_table.rowCount() - 1:
            next_row = current_row + 1
            self._product_table.selectRow(next_row)
    
    def _on_upload_selected(self):
        """Handle upload selected button."""
        skus = self.get_checked_skus()
        if skus:
            self.upload_requested.emit(skus)
    
    def _on_preview_site(self):
        """Handle preview site button."""
        if self._selected_sku:
            self.preview_requested.emit(self._selected_sku)
    
    def _on_view_shopify(self):
        """Open product on Shopify in browser."""
        if not self._selected_sku or self._selected_sku not in self._products:
            return
        
        product = self._products[self._selected_sku]
        handle = getattr(product, "handle", "") or ""
        
        if not handle:
            QMessageBox.information(self, "No Handle", "Product has no handle set.")
            return
        
        # Construct Shopify URL - assuming jaksdiesel.com store
        url = f"https://jaksdiesel.com/products/{handle}"
        QDesktopServices.openUrl(QUrl(url))
    
    def _on_preview_upload(self):
        """Show rich preview dialog mimicking jaksdiesel.com website style."""
        if not self._selected_sku or self._selected_sku not in self._products:
            return
        
        product = self._products[self._selected_sku]
        
        # Get only the selected (checked) images
        selected_images = self.get_selected_image_paths()
        
        dialog = ProductPreviewDialog(product, self, selected_images=selected_images)
        dialog.exec()
    
    def _on_set_price(self):
        """Handle set price bulk action."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        
        price, ok = QInputDialog.getDouble(
            self,
            "Set Price",
            f"Set price for {len(skus)} product(s):",
            0.0,      # value
            0.0,      # min
            99999.99, # max
            2         # decimals
        )
        if ok and price > 0:
            for sku in skus:
                self._update_product_price(sku, price)
                self._update_table_row_for_sku(sku)
    
    def _on_set_margin(self):
        """Handle set margin bulk action."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        
        margin, ok = QInputDialog.getDouble(
            self,
            "Set Margin",
            f"Set margin % for {len(skus)} product(s):\n(Price = Cost / (1 - margin%))",
            30.0,  # value
            1.0,   # min
            90.0,  # max
            1      # decimals
        )
        if ok:
            margin_decimal = margin / 100.0
            for sku in skus:
                product = self._products.get(sku)
                if product:
                    cost = float(getattr(product, "pai_cost", 0) or getattr(product, "cost", 0) or 0)
                    if cost > 0:
                        # Price = Cost / (1 - margin)
                        new_price = cost / (1 - margin_decimal)
                        self._update_product_price(sku, new_price)
                        self._update_table_row_for_sku(sku)
    
    def _apply_markup(self, pct: float):
        """Apply markup to selected products."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        for sku in skus:
            product = self._products.get(sku)
            if product:
                current = self._get_selling_price(product)
                new_price = current * (1 + pct)
                self._update_product_price(sku, new_price)
                self._update_table_row_for_sku(sku)
    
    def _on_match_hhp(self):
        """Match selling price to HHP scraped price."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        for sku in skus:
            product = self._products.get(sku)
            if product:
                hhp_price = float(getattr(product, "scraped_price", 0) or 0)
                if hhp_price > 0:
                    self._update_product_price(sku, hhp_price)
                    self._update_table_row_for_sku(sku)
    
    def _on_add_tag(self):
        """Add a tag to selected products."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        
        # Common tags to suggest
        common_tags = [
            "new-arrival",
            "best-seller",
            "on-sale",
            "featured",
            "heavy-duty",
            "oem-quality",
            "remanufactured",
            "aftermarket",
            "core-required",
            "free-shipping",
        ]
        
        tag, ok = QInputDialog.getItem(
            self,
            "Add Tag",
            f"Select or enter tag for {len(skus)} product(s):",
            common_tags,
            editable=True,
        )
        
        if ok and tag:
            tag = tag.strip().lower().replace(" ", "-")
            count = 0
            for sku in skus:
                product = self._products.get(sku)
                if product:
                    tags = getattr(product, "tags", None)
                    if tags is None:
                        tags = []
                        setattr(product, "tags", tags)
                    elif isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",") if t.strip()]
                        setattr(product, "tags", tags)
                    
                    if tag not in tags:
                        tags.append(tag)
                        count += 1
            
            QMessageBox.information(self, "Tags Updated", f"Added tag '{tag}' to {count} product(s).")
    
    def _on_add_collection(self):
        """Assign selected products to a collection."""
        skus = self.get_checked_skus()
        if not skus:
            QMessageBox.information(self, "No Selection", "Check some products first.")
            return
        
        # Common collections
        collections = [
            "Turbochargers",
            "Fuel Injectors",
            "Oil Pumps & Coolers",
            "Gaskets",
            "Cylinder Heads",
            "Pistons & Liners",
            "Exhaust Systems",
            "Caterpillar",
            "Cummins",
            "Detroit Diesel",
            "International",
            "Volvo/Mack",
            "PACCAR",
            "New Arrivals",
            "Sale Items",
        ]
        
        collection, ok = QInputDialog.getItem(
            self,
            "Add to Collection",
            f"Select collection for {len(skus)} product(s):",
            collections,
            editable=True,
        )
        
        if ok and collection:
            count = 0
            for sku in skus:
                product = self._products.get(sku)
                if product:
                    colls = getattr(product, "collections", None)
                    if colls is None:
                        colls = []
                        setattr(product, "collections", colls)
                    elif isinstance(colls, str):
                        colls = [c.strip() for c in colls.split(",") if c.strip()]
                        setattr(product, "collections", colls)
                    
                    if collection not in colls:
                        colls.append(collection)
                        count += 1
            
            QMessageBox.information(self, "Collections Updated", f"Added to '{collection}': {count} product(s).")
    
    # ─────────────────────────────────────────────────────────────────────
    # Image helpers
    # ─────────────────────────────────────────────────────────────────────
    
    def _clear_thumbnails(self):
        """Clear and hide all image thumbnails."""
        # Clear preview image
        if hasattr(self, '_preview_image_label'):
            self._preview_image_label.setPixmap(QPixmap())
            self._preview_image_label.setText("No image selected")
        
        # Clear new slot structure
        if hasattr(self, '_image_slots'):
            for slot in self._image_slots:
                slot["label"].setPixmap(QPixmap())
                slot["label"].setText("")
                slot["label"].setToolTip("")
                slot["frame"].setVisible(False)
                slot["checkbox"].setChecked(True)  # Reset to default
                slot["path"] = None
                slot["source"] = None
                # Reset border style
                slot["frame"].setStyleSheet(f"""
                    QFrame {{
                        background-color: {THEME_COLORS['bg']};
                        border: 1px solid {THEME_COLORS['border']};
                        border-radius: 4px;
                    }}
                    QFrame:hover {{
                        border-color: {THEME_COLORS['primary']};
                    }}
                """)
        
        # Clear legacy labels
        for label in self._image_labels:
            label.setPixmap(QPixmap())
            label.setText("")
            label.setToolTip("")
            label.setVisible(False)
        
        self._image_count_label.setText("No images")
        
        # Reset folder buttons
        if hasattr(self, '_image_source_buttons'):
            for src, btn in self._image_source_buttons.items():
                btn.setText(f"📁 {src} (0)")
                btn.setEnabled(False)
                btn.setChecked(False)
        
        # Reset source label
        if hasattr(self, '_image_source_label'):
            self._image_source_label.setText("")
    
    def _load_product_images(self, product: "Product"):
        """Load product images into thumbnails, supporting multi-vendor sources."""
        self._clear_thumbnails()
        
        # Get handle for image folder
        handle = getattr(product, "handle", "") or ""
        if not handle:
            sku = getattr(product, "sku", "") or ""
            handle = sku.replace("JAKS-", "")
        
        # Get category and manufacturer for folder path
        category = getattr(product, "category", "") or ""
        manufacturer = getattr(product, "manufacturer", "") or ""
        
        # Normalize for folder names (lowercase, underscores)
        cat_folder = category.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        mfr_folder = manufacturer.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
        
        # Handle might have hyphens but folder uses underscores
        handle_underscore = handle.lower().replace("-", "_")
        
        # Collect images from all sources
        source_images: dict[str, list[Path]] = {"HHP": [], "ATL": [], "PAI": []}
        source_counts: dict[str, int] = {"HHP": 0, "ATL": 0, "PAI": 0}
        
        # Find the product folder first
        product_dir = None
        possible_product_dirs = [
            Path("output") / cat_folder / mfr_folder / f"jaks_{handle_underscore}",
            Path("output") / cat_folder / mfr_folder / f"jaks_{handle}",
            Path("output") / cat_folder / mfr_folder / handle,
            Path("output") / cat_folder / f"jaks_{handle_underscore}",
        ]
        
        for candidate in possible_product_dirs:
            if candidate.exists():
                product_dir = candidate
                break
        
        if product_dir:
            # Check for multi-vendor subfolders
            hhp_dir = product_dir / "images" / "hhp"
            atl_dir = product_dir / "images" / "atl"
            pai_dir = product_dir / "images" / "pai"
            main_images_dir = product_dir / "images"
            
            # Load from vendor subfolders if they exist
            if hhp_dir.exists():
                source_images["HHP"] = sorted(hhp_dir.glob("*.jpg")) + sorted(hhp_dir.glob("*.png"))
            if atl_dir.exists():
                source_images["ATL"] = sorted(atl_dir.glob("*.jpg")) + sorted(atl_dir.glob("*.png"))
            if pai_dir.exists():
                source_images["PAI"] = sorted(pai_dir.glob("*.jpg")) + sorted(pai_dir.glob("*.png"))
            
            # If no vendor subfolders, load from main images folder (assumed HHP)
            if main_images_dir.exists() and not any(source_images.values()):
                main_images = sorted(main_images_dir.glob("*.jpg")) + sorted(main_images_dir.glob("*.png"))
                source_images["HHP"] = main_images
        
        # Also check legacy path: output/images/<handle>/
        legacy_dir = Path("output/images") / handle
        if legacy_dir.exists() and not source_images["HHP"]:
            source_images["HHP"] = sorted(legacy_dir.glob("*.jpg")) + sorted(legacy_dir.glob("*.png"))
        
        # Also check for images listed in product model fields
        for attr, src in [("images_hhp", "HHP"), ("images_atl", "ATL"), ("images_pai", "PAI")]:
            model_images = getattr(product, attr, []) or []
            if model_images:
                for img_path_str in model_images:
                    p = Path(img_path_str)
                    if p.exists() and p not in source_images[src]:
                        source_images[src].append(p)
        
        # Update counts
        for src in source_images:
            source_counts[src] = len(source_images[src])
        
        # Update folder button labels with counts
        for src, btn in self._image_source_buttons.items():
            count = source_counts[src]
            btn.setText(f"📁 {src} ({count})")
            btn.setEnabled(count > 0)
        
        # Determine which images to display based on current filter
        current_source = getattr(self, '_current_image_source', 'ALL')
        
        if current_source == "ALL":
            # Show all images, prioritized: HHP first, then ATL, then PAI
            display_images = source_images["HHP"] + source_images["ATL"] + source_images["PAI"]
        else:
            display_images = source_images.get(current_source, [])
        
        total_count = sum(source_counts.values())
        
        if not display_images:
            # Show count from product data even if files not found locally
            img_count_wm = getattr(product, "image_count_watermarked", 0) or 0
            img_count_raw = getattr(product, "image_count_raw", 0) or 0
            
            if img_count_wm > 0:
                self._image_count_label.setText(f"{img_count_wm} images (not local)")
            elif img_count_raw > 0:
                self._image_count_label.setText(f"{img_count_raw} raw (not processed)")
            else:
                self._image_count_label.setText("No images")
            return
        
        # Update count label
        if current_source == "ALL":
            self._image_count_label.setText(f"{total_count} images total")
        else:
            self._image_count_label.setText(f"{len(display_images)} {current_source} images")
        
        # Update source label
        if hasattr(self, '_image_source_label'):
            sources_with_images = [s for s, c in source_counts.items() if c > 0]
            self._image_source_label.setText(" | ".join(sources_with_images) if sources_with_images else "")
        
        # Determine source for each image
        def get_image_source(img_path: Path) -> str:
            path_str = str(img_path).lower()
            if "/atl/" in path_str or "\\atl\\" in path_str:
                return "ATL"
            elif "/pai/" in path_str or "\\pai\\" in path_str:
                return "PAI"
            return "HHP"
        
        # Load thumbnails - show up to 30 images with checkboxes (scrollable)
        max_slots = len(self._image_slots)
        first_image_shown = False
        for i, img_path in enumerate(display_images[:max_slots]):
            try:
                pixmap = QPixmap(str(img_path))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        64, 60, 
                        Qt.KeepAspectRatio, 
                        Qt.SmoothTransformation
                    )
                    slot = self._image_slots[i]
                    slot["label"].setPixmap(scaled)
                    slot["label"].setText("")
                    slot["label"].setToolTip(f"{img_path.name}\n({get_image_source(img_path)})")
                    slot["frame"].setVisible(True)
                    slot["checkbox"].setChecked(True)  # Default selected
                    slot["path"] = img_path
                    slot["source"] = get_image_source(img_path)
                    
                    # Show first image in the preview
                    if not first_image_shown:
                        self._show_preview_image(img_path)
                        # Highlight first thumbnail
                        slot["frame"].setStyleSheet(f"""
                            QFrame {{
                                background-color: {THEME_COLORS['bg']};
                                border: 2px solid {THEME_COLORS['primary']};
                                border-radius: 4px;
                            }}
                        """)
                        first_image_shown = True
                    
                    # Also update legacy labels for compatibility
                    if i < len(self._image_labels):
                        self._image_labels[i].setPixmap(scaled)
                        self._image_labels[i].setVisible(True)
            except Exception:
                pass
    
    def get_selected_image_paths(self) -> list[Path]:
        """Return list of image paths that are checked for upload."""
        selected = []
        for slot in self._image_slots:
            if slot["frame"].isVisible() and slot["checkbox"].isChecked() and slot["path"]:
                selected.append(slot["path"])
        return selected
    
    # ─────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────
    
    def _get_stage(self, product: "Product") -> int:
        """Get product stage."""
        pricing = getattr(product, "pricing", {}) or {}
        return int(pricing.get("stage", 1) or 1)
    
    def _stage_to_status(self, stage: int) -> str:
        """Convert stage number to status string."""
        if stage >= 5:
            return "Uploaded"
        elif stage >= 4:
            return "Ready"
        elif stage >= 3:
            return "Review"
        else:
            return "Draft"
    
    def _status_icon(self, status: str) -> str:
        """Return emoji icon for status."""
        return {
            "Ready": "✓",
            "Review": "⚠",
            "Uploaded": "☁",
            "Draft": "○",
        }.get(status, "○")
    
    def _get_selling_price(self, product: "Product") -> float:
        """Get selling price from product, auto-suggesting competitive price if not set."""
        pricing = getattr(product, "pricing", {}) or {}
        
        # If explicit selling_price already set, use it
        explicit_selling = float(pricing.get("selling_price", 0) or 0)
        if explicit_selling > 0:
            return explicit_selling
        
        # Otherwise, calculate competitive price (beat lowest competitor by 1%)
        hhp_price = float(pricing.get("hhp_price", 0) or pricing.get("price", 0) or 0)
        atl_price = float(pricing.get("atl_price", 0) or 0)
        
        competitor_prices = [p for p in [hhp_price, atl_price] if p > 0]
        if competitor_prices:
            lowest = min(competitor_prices)
            # Return price 1% below lowest competitor
            return round(lowest * 0.99, 2)
        
        # Fallback to HHP price if no competitor data
        return hhp_price
    
    def _get_compare_price(self, product: "Product") -> float:
        """Get compare-at price from product."""
        pricing = getattr(product, "pricing", {}) or {}
        return float(pricing.get("compare_at_price", 0) or 0)


# ─────────────────────────────────────────────────────────────────────────────
# Sync Details Dialog - Shows modified/orphaned product details
# ─────────────────────────────────────────────────────────────────────────────

class SyncDetailsDialog(QDialog):
    """Dialog showing sync status details - modified diffs and orphaned products."""
    
    import_orphaned = Signal(list)  # Signal to import selected orphaned SKUs
    push_to_shopify = Signal(list)  # Signal to push selected modified SKUs to Shopify
    pull_from_shopify = Signal(list)  # Signal to pull Shopify values to local
    
    def __init__(self, sync_result: dict, status_type: str, parent=None):
        super().__init__(parent)
        self._sync_result = sync_result
        self._status_type = status_type  # "modified" or "orphaned"
        self._setup_ui()
        self._load_data()
    
    def _setup_ui(self):
        """Create dialog layout."""
        titles = {
            "modified": "⚠ Modified Products - Local vs Shopify",
            "orphaned": "⊗ Orphaned Products - On Shopify, Not Local"
        }
        self.setWindowTitle(titles.get(self._status_type, "Sync Details"))
        self.setMinimumSize(800, 500)
        self.resize(900, 600)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header with count
        header = QLabel()
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        self._header_label = header
        layout.addWidget(header)
        
        # Table for products
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setStyleSheet("""
            QTableWidget { 
                border: 1px solid #ddd; 
                gridline-color: #eee;
                font-size: 13px;
            }
            QTableWidget::item { padding: 8px; }
            QHeaderView::section { 
                background-color: #f5f5f5; 
                padding: 8px; 
                font-weight: bold;
                border: none;
                border-bottom: 1px solid #ddd;
            }
        """)
        layout.addWidget(self._table, 1)
        
        # Footer with buttons
        footer = QHBoxLayout()
        
        if self._status_type == "modified":
            # Select all checkbox
            self._select_all = QCheckBox("Select All")
            self._select_all.stateChanged.connect(self._toggle_select_all)
            footer.addWidget(self._select_all)
            
            # Push to Shopify button (orange)
            push_btn = QPushButton("📤 Push to Shopify")
            push_btn.setToolTip("Update Shopify with local values")
            push_btn.setStyleSheet("""
                QPushButton {
                    background-color: #fd7e14;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 10px 16px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #e76a00; }
            """)
            push_btn.clicked.connect(self._on_push_selected)
            footer.addWidget(push_btn)
            
            # Pull from Shopify button (blue)
            pull_btn = QPushButton("📥 Pull from Shopify")
            pull_btn.setToolTip("Overwrite local values with Shopify values")
            pull_btn.setStyleSheet("""
                QPushButton {
                    background-color: #007bff;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 10px 16px;
                    font-weight: bold;
                    font-size: 12px;
                }
                QPushButton:hover { background-color: #0056b3; }
            """)
            pull_btn.clicked.connect(self._on_pull_selected)
            footer.addWidget(pull_btn)
        
        elif self._status_type == "orphaned":
            # Select all checkbox
            self._select_all = QCheckBox("Select All")
            self._select_all.stateChanged.connect(self._toggle_select_all)
            footer.addWidget(self._select_all)
            
            # Import button
            import_btn = QPushButton("📥 Import Selected to Local")
            import_btn.setStyleSheet("""
                QPushButton {
                    background-color: #28a745;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 10px 20px;
                    font-weight: bold;
                    font-size: 13px;
                }
                QPushButton:hover { background-color: #218838; }
            """)
            import_btn.clicked.connect(self._on_import_selected)
            footer.addWidget(import_btn)
        
        footer.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #5a6268; }
        """)
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        
        layout.addLayout(footer)
    
    def _load_data(self):
        """Load products into the table based on status type."""
        details = self._sync_result.get('details', {})
        
        if self._status_type == "modified":
            skus = self._sync_result.get('modified', [])
            self._header_label.setText(f"⚠ {len(skus)} Modified Products (local differs from Shopify)")
            
            self._table.setColumnCount(5)
            self._table.setHorizontalHeaderLabels(["☑", "SKU", "Shopify Title", "Changes", "Shopify Status"])
            self._table.setColumnWidth(0, 40)
            self._table.setRowCount(len(skus))
            
            for row, sku in enumerate(skus):
                detail = details.get(sku, {})
                
                # Checkbox
                checkbox = QCheckBox()
                checkbox.setProperty("sku", sku)
                self._table.setCellWidget(row, 0, checkbox)
                
                # SKU
                sku_item = QTableWidgetItem(sku)
                sku_item.setForeground(QColor("#007bff"))
                self._table.setItem(row, 1, sku_item)
                
                # Title
                self._table.setItem(row, 2, QTableWidgetItem(detail.get('shopify_title', '')))
                
                # Diffs - highlight what changed
                diffs = detail.get('diffs', [])
                diffs_text = "\n".join(diffs) if diffs else "No differences"
                diffs_item = QTableWidgetItem(diffs_text)
                diffs_item.setForeground(QColor("#fd7e14"))
                self._table.setItem(row, 3, diffs_item)
                
                # Status
                status = detail.get('shopify_status', 'unknown')
                status_item = QTableWidgetItem(status)
                if status == "active":
                    status_item.setForeground(QColor("#28a745"))
                elif status == "draft":
                    status_item.setForeground(QColor("#6c757d"))
                self._table.setItem(row, 4, status_item)
        
        elif self._status_type == "orphaned":
            skus = self._sync_result.get('orphaned', [])
            self._header_label.setText(f"⊗ {len(skus)} Orphaned Products (on Shopify, not in local inventory)")
            
            self._table.setColumnCount(5)
            self._table.setHorizontalHeaderLabels(["☑", "SKU", "Shopify Title", "Price", "Status"])
            self._table.setColumnWidth(0, 40)
            self._table.setRowCount(len(skus))
            
            for row, sku in enumerate(skus):
                detail = details.get(sku, {})
                
                # Checkbox
                checkbox = QCheckBox()
                checkbox.setProperty("sku", sku)
                self._table.setCellWidget(row, 0, checkbox)
                
                # SKU
                sku_item = QTableWidgetItem(sku)
                sku_item.setForeground(QColor("#6c757d"))
                self._table.setItem(row, 1, sku_item)
                
                # Title
                self._table.setItem(row, 2, QTableWidgetItem(detail.get('shopify_title', '')))
                
                # Price
                price = detail.get('shopify_price', '')
                price_item = QTableWidgetItem(f"${price}" if price else "N/A")
                self._table.setItem(row, 3, price_item)
                
                # Status
                status = detail.get('shopify_status', 'unknown')
                status_item = QTableWidgetItem(status)
                if status == "active":
                    status_item.setForeground(QColor("#28a745"))
                elif status == "draft":
                    status_item.setForeground(QColor("#6c757d"))
                self._table.setItem(row, 4, status_item)
        
        self._table.resizeColumnsToContents()
    
    def _toggle_select_all(self, state):
        """Toggle all checkboxes."""
        checked = state == Qt.Checked
        for row in range(self._table.rowCount()):
            checkbox = self._table.cellWidget(row, 0)
            if checkbox:
                checkbox.setChecked(checked)
    
    def _on_import_selected(self):
        """Emit signal with selected SKUs for import."""
        selected_skus = []
        for row in range(self._table.rowCount()):
            checkbox = self._table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                sku = checkbox.property("sku")
                if sku:
                    selected_skus.append(sku)
        
        if not selected_skus:
            QMessageBox.warning(self, "No Selection", "Please select at least one product to import.")
            return
        
        self.import_orphaned.emit(selected_skus)
        self.accept()
    
    def _on_push_selected(self):
        """Emit signal with selected SKUs to push to Shopify."""
        selected_skus = []
        for row in range(self._table.rowCount()):
            checkbox = self._table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                sku = checkbox.property("sku")
                if sku:
                    selected_skus.append(sku)
        
        if not selected_skus:
            QMessageBox.warning(self, "No Selection", "Please select at least one product to update on Shopify.")
            return
        
        self.push_to_shopify.emit(selected_skus)
        self.accept()
    
    def _on_pull_selected(self):
        """Emit signal with selected SKUs to pull from Shopify (overwrite local)."""
        selected_skus = []
        for row in range(self._table.rowCount()):
            checkbox = self._table.cellWidget(row, 0)
            if checkbox and checkbox.isChecked():
                sku = checkbox.property("sku")
                if sku:
                    selected_skus.append(sku)
        
        if not selected_skus:
            QMessageBox.warning(self, "No Selection", "Please select at least one product to refresh from Shopify.")
            return
        
        self.pull_from_shopify.emit(selected_skus)
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Product Preview Dialog - Mimics jaksdiesel.com website style + Shopify Data
# ─────────────────────────────────────────────────────────────────────────────

class ProductPreviewDialog(QDialog):
    """Rich preview dialog showing product as it would appear on jaksdiesel.com + Shopify payload."""
    
    def __init__(self, product: "Product", parent=None, selected_images: list = None):
        super().__init__(parent)
        self._product = product
        self._selected_images = selected_images or []  # List of Path objects
        self._setup_ui()
        self._load_product_data()
    
    def _setup_ui(self):
        """Create dialog layout with tabs for Website Preview and Shopify Data."""
        self.setWindowTitle("Product Preview - jaksdiesel.com")
        self.setMinimumSize(1100, 800)
        self.resize(1200, 900)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header bar
        header = QFrame()
        header.setStyleSheet("QFrame { background-color: #1a1a1a; }")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)
        
        logo_label = QLabel("JAK'S DIESEL")
        logo_label.setStyleSheet("color: #c9a227; font-size: 24px; font-weight: bold; font-family: 'Georgia', serif;")
        header_layout.addWidget(logo_label)
        header_layout.addStretch()
        
        preview_badge = QLabel("⚠ PREVIEW MODE")
        preview_badge.setStyleSheet("color: #ff9800; font-size: 12px; font-weight: bold; background-color: rgba(255, 152, 0, 0.2); padding: 4px 12px; border-radius: 4px;")
        header_layout.addWidget(preview_badge)
        layout.addWidget(header)
        
        # Tab widget for Website Preview vs Shopify Data
        from PySide6.QtWidgets import QTabWidget
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: white; }
            QTabBar::tab { padding: 10px 20px; font-weight: bold; }
            QTabBar::tab:selected { background: #c9a227; color: white; }
            QTabBar::tab:!selected { background: #f0f0f0; }
        """)
        
        # Tab 1: Website Preview
        self._tabs.addTab(self._create_website_preview_tab(), "🌐 Website Preview")
        
        # Tab 2: Shopify Data (what gets uploaded)
        self._tabs.addTab(self._create_shopify_data_tab(), "📤 Shopify Data")
        
        layout.addWidget(self._tabs, 1)
        
        # Footer with buttons
        footer = QFrame()
        footer.setStyleSheet(f"QFrame {{ background-color: {THEME_COLORS['header_bg']}; border-top: 1px solid {THEME_COLORS['border']}; }}")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)
        
        open_btn = QPushButton("🌐 Open in Browser")
        open_btn.clicked.connect(self._open_in_browser)
        footer_layout.addWidget(open_btn)
        
        footer_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet(f"QPushButton {{ background-color: {THEME_COLORS['primary']}; color: white; font-weight: bold; padding: 8px 24px; border-radius: 4px; }}")
        footer_layout.addWidget(close_btn)
        
        layout.addWidget(footer)
    
    def _create_website_preview_tab(self) -> QWidget:
        """Create the website preview tab mimicking jaksdiesel.com."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #ffffff; border: none; }")
        
        content = QWidget()
        content.setStyleSheet("background-color: #ffffff;")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(40, 30, 40, 30)
        content_layout.setSpacing(40)
        
        # Left side - Images
        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)
        
        self._main_image = QLabel()
        self._main_image.setFixedSize(450, 450)
        self._main_image.setAlignment(Qt.AlignCenter)
        self._main_image.setStyleSheet("QLabel { background-color: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 4px; }")
        self._main_image.setText("No Image")
        left_panel.addWidget(self._main_image)
        
        # Thumbnail row (2 rows of 4)
        self._thumb_labels = []
        for row_idx in range(2):
            thumb_row = QHBoxLayout()
            thumb_row.setSpacing(8)
            for i in range(4):
                thumb = QLabel()
                thumb.setFixedSize(100, 100)
                thumb.setAlignment(Qt.AlignCenter)
                thumb.setStyleSheet("QLabel { background-color: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 4px; } QLabel:hover { border-color: #c9a227; }")
                thumb.setVisible(False)
                thumb_row.addWidget(thumb)
                self._thumb_labels.append(thumb)
            thumb_row.addStretch()
            left_panel.addLayout(thumb_row)
        
        left_panel.addStretch()
        content_layout.addLayout(left_panel)
        
        # Right side - Product info
        right_panel = QVBoxLayout()
        right_panel.setSpacing(12)
        
        # SKU line (above title like website)
        self._sku_top = QLabel()
        self._sku_top.setStyleSheet("font-size: 13px; color: #888888; font-weight: 500;")
        right_panel.addWidget(self._sku_top)
        
        # Vendor/Brand
        self._vendor_label = QLabel("JAK'S DIESEL")
        self._vendor_label.setStyleSheet("font-size: 11px; color: #666666; letter-spacing: 1px; font-weight: 600;")
        right_panel.addWidget(self._vendor_label)
        
        # Title
        self._title_label = QLabel()
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet("font-size: 26px; font-weight: bold; color: #1a1a1a; font-family: 'Georgia', serif; margin-top: 8px;")
        right_panel.addWidget(self._title_label)
        
        # Price section
        price_frame = QFrame()
        price_frame.setStyleSheet("QFrame { background-color: #fafafa; border-radius: 8px; margin-top: 12px; }")
        price_layout = QVBoxLayout(price_frame)
        price_layout.setContentsMargins(16, 16, 16, 16)
        
        self._price_label = QLabel()
        self._price_label.setStyleSheet("font-size: 32px; font-weight: bold; color: #1a1a1a;")
        price_layout.addWidget(self._price_label)
        
        self._compare_label = QLabel()
        self._compare_label.setStyleSheet("font-size: 14px; color: #999999; text-decoration: line-through;")
        price_layout.addWidget(self._compare_label)
        
        right_panel.addWidget(price_frame)
        
        # Quantity + Add to Cart (visual only)
        cart_row = QHBoxLayout()
        qty_frame = QFrame()
        qty_frame.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 4px; }")
        qty_layout = QHBoxLayout(qty_frame)
        qty_layout.setContentsMargins(8, 8, 8, 8)
        qty_layout.addWidget(QLabel("—"))
        qty_layout.addWidget(QLabel("1"))
        qty_layout.addWidget(QLabel("+"))
        cart_row.addWidget(qty_frame)
        
        cart_btn = QPushButton("Add to cart")
        cart_btn.setFixedHeight(44)
        cart_btn.setStyleSheet("QPushButton { background-color: white; color: #1a1a1a; font-size: 14px; border: 1px solid #1a1a1a; border-radius: 4px; padding: 0 40px; }")
        cart_btn.setEnabled(False)
        cart_row.addWidget(cart_btn)
        cart_row.addStretch()
        right_panel.addLayout(cart_row)
        
        # Shop Pay button (visual)
        shop_btn = QPushButton("Buy with Shop Pay")
        shop_btn.setFixedHeight(44)
        shop_btn.setStyleSheet("QPushButton { background-color: #5a31f4; color: white; font-size: 14px; font-weight: bold; border: none; border-radius: 4px; }")
        shop_btn.setEnabled(False)
        right_panel.addWidget(shop_btn)
        
        # Description header
        desc_header = QLabel("Premium Product from JAK's Diesel")
        desc_header.setStyleSheet("font-size: 18px; font-weight: bold; color: #6b6b47; margin-top: 20px;")
        right_panel.addWidget(desc_header)
        
        # Description content
        self._desc_browser = QTextBrowser()
        self._desc_browser.setOpenExternalLinks(True)
        self._desc_browser.setMinimumHeight(250)
        self._desc_browser.setStyleSheet("QTextBrowser { background-color: #ffffff; border: none; font-size: 14px; color: #666666; }")
        right_panel.addWidget(self._desc_browser)
        
        # Kit Details box (if applicable)
        self._kit_details_frame = QFrame()
        self._kit_details_frame.setStyleSheet("QFrame { background-color: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px; margin-top: 16px; }")
        kit_layout = QVBoxLayout(self._kit_details_frame)
        kit_layout.setContentsMargins(16, 16, 16, 16)
        
        kit_header = QLabel("JAK's Diesel Rebuild Kit Details")
        kit_header.setStyleSheet("font-size: 16px; font-weight: bold; color: #1a1a1a;")
        kit_layout.addWidget(kit_header)
        
        self._cpl_label = QLabel()
        self._cpl_label.setStyleSheet("font-size: 14px; color: #333;")
        kit_layout.addWidget(self._cpl_label)
        
        self._kit_contents_label = QLabel()
        self._kit_contents_label.setWordWrap(True)
        self._kit_contents_label.setStyleSheet("font-size: 13px; color: #666;")
        kit_layout.addWidget(self._kit_contents_label)
        
        self._xrefs_label = QLabel()
        self._xrefs_label.setWordWrap(True)
        self._xrefs_label.setStyleSheet("font-size: 13px; color: #666;")
        kit_layout.addWidget(self._xrefs_label)
        
        self._kit_details_frame.setVisible(False)
        right_panel.addWidget(self._kit_details_frame)
        
        right_panel.addStretch()
        content_layout.addLayout(right_panel, 1)
        
        scroll.setWidget(content)
        return scroll
    
    def _create_shopify_data_tab(self) -> QWidget:
        """Create tab showing exactly what gets sent to Shopify."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #f8f9fa; border: none; }")
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 24, 24, 24)
        content_layout.setSpacing(16)
        
        # Title
        title = QLabel("📤 Shopify Upload Payload")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1a1a1a;")
        content_layout.addWidget(title)
        
        subtitle = QLabel("This shows exactly what data will be sent to Shopify when you upload this product.")
        subtitle.setStyleSheet("font-size: 13px; color: #666;")
        content_layout.addWidget(subtitle)
        
        # Product Info Section
        content_layout.addWidget(self._create_section("Product Info", [
            ("title", "Title"),
            ("sku", "SKU"),
            ("handle", "Handle"),
            ("product_type", "Product Type"),
            ("template_suffix", "Template"),
            ("vendor", "Vendor"),
            ("status", "Status"),
            ("images", "Images to Upload"),
        ]))
        
        # Pricing Section
        content_layout.addWidget(self._create_section("Pricing", [
            ("price", "Selling Price"),
            ("cost", "Cost (Variant)"),
            ("compare_at", "Compare At Price"),
        ]))
        
        # Collections Section
        self._collections_section = self._create_list_section("Collections", "collections")
        content_layout.addWidget(self._collections_section)
        
        # Tags Section
        self._tags_section = self._create_list_section("Tags", "tags")
        content_layout.addWidget(self._tags_section)
        
        # Metafields Section
        self._metafields_section = self._create_list_section("Metafields (namespace: jaks)", "metafields")
        content_layout.addWidget(self._metafields_section)
        
        # Description HTML Section
        desc_group = QGroupBox("Description HTML (body_html)")
        desc_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; padding-top: 16px; }")
        desc_layout = QVBoxLayout(desc_group)
        
        self._shopify_desc_browser = QTextBrowser()
        self._shopify_desc_browser.setMinimumHeight(200)
        self._shopify_desc_browser.setStyleSheet("QTextBrowser { background-color: white; border: 1px solid #ddd; font-family: monospace; font-size: 12px; }")
        desc_layout.addWidget(self._shopify_desc_browser)
        content_layout.addWidget(desc_group)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        return scroll
    
    def _create_section(self, title: str, fields: list) -> QGroupBox:
        """Create a grouped section with field labels."""
        group = QGroupBox(title)
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; padding-top: 16px; }")
        layout = QFormLayout(group)
        layout.setSpacing(8)
        
        for field_key, field_label in fields:
            label = QLabel("-")
            label.setStyleSheet("color: #333; font-family: 'Consolas', monospace;")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            setattr(self, f"_shopify_{field_key}", label)
            layout.addRow(f"{field_label}:", label)
        
        return group
    
    def _create_list_section(self, title: str, field_key: str) -> QGroupBox:
        """Create a section for lists (tags, collections, metafields)."""
        group = QGroupBox(title)
        group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; padding-top: 16px; }")
        layout = QVBoxLayout(group)
        
        label = QLabel("-")
        label.setWordWrap(True)
        label.setStyleSheet("color: #333; font-family: 'Consolas', monospace; font-size: 12px;")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        setattr(self, f"_shopify_{field_key}", label)
        layout.addWidget(label)
        
        return group
    
    def _load_product_data(self):
        """Populate both tabs with product data."""
        self._load_website_preview()
        self._load_shopify_data()
        self._load_images()
    
    def _load_website_preview(self):
        """Load website preview tab data."""
        product = self._product
        
        # SKU at top
        sku = getattr(product, "sku", "") or ""
        self._sku_top.setText(sku)
        
        # Title
        title = getattr(product, "title", "") or "No Title"
        self._title_label.setText(title)
        
        # Price
        pricing = getattr(product, "pricing", {}) or {}
        price = float(pricing.get("selling_price", 0) or pricing.get("hhp_price", 0) or pricing.get("price", 0) or 0)
        self._price_label.setText(f"${price:,.2f} USD")
        
        compare = float(pricing.get("compare_at_price", 0) or 0)
        if compare > price:
            self._compare_label.setText(f"${compare:,.2f}")
            self._compare_label.setVisible(True)
        else:
            self._compare_label.setVisible(False)
        
        # Description
        desc = getattr(product, "description_jaks_html", "") or getattr(product, "description", "") or ""
        if desc:
            styled_desc = f"""
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.8; color: #666666; }}
                h2 {{ color: #6b6b47; font-size: 16px; margin-top: 20px; font-weight: bold; }}
                ul {{ margin: 10px 0; padding-left: 20px; }}
                li {{ margin: 6px 0; }}
                table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
                th, td {{ padding: 8px 12px; border: 1px solid #ddd; text-align: left; }}
                th {{ background-color: #f5f5f5; }}
            </style>
            {desc}
            """
            self._desc_browser.setHtml(styled_desc)
        else:
            self._desc_browser.setHtml("<p><i>No description available</i></p>")
        
        # Kit details box
        cpl_prefix = getattr(product, "cpl_prefix", "") or ""
        cpl_list = getattr(product, "cpl_list", []) or []
        kit_contents = getattr(product, "kit_contents", []) or []
        xrefs = getattr(product, "xrefs", []) or []
        
        show_kit_box = cpl_prefix or cpl_list or kit_contents or xrefs
        if show_kit_box:
            self._kit_details_frame.setVisible(True)
            
            if cpl_prefix or cpl_list:
                # Prefer cpl_list (all prefixes) over cpl_prefix (single)
                cpl_display = ', '.join(cpl_list) if cpl_list else cpl_prefix
                cpl_text = f"<b>CPL/ESN Prefix:</b> <span style='color: #6b6b47;'>{cpl_display}</span>"
                self._cpl_label.setText(cpl_text)
                self._cpl_label.setVisible(True)
            else:
                self._cpl_label.setVisible(False)
            
            if kit_contents:
                contents = "<b>Kit Contents Include:</b><br>" + "<br>".join(f"• {c}" for c in kit_contents[:10])
                self._kit_contents_label.setText(contents)
                self._kit_contents_label.setVisible(True)
            else:
                self._kit_contents_label.setVisible(False)
            
            if xrefs:
                xref_text = f"<b>Cross References:</b> {', '.join(xrefs[:15])}"
                self._xrefs_label.setText(xref_text)
                self._xrefs_label.setVisible(True)
            else:
                self._xrefs_label.setVisible(False)
        else:
            self._kit_details_frame.setVisible(False)
    
    def _load_shopify_data(self):
        """Load Shopify data tab with exactly what gets uploaded."""
        product = self._product
        
        # Import uploader functions to compute real payload
        try:
            from phases.uploader import infer_tags, build_desired_metafields
            from engine.template_inference import infer_product_type, infer_template_suffix
        except ImportError:
            pass
        
        # Basic fields
        title = getattr(product, "title", "") or ""
        sku = getattr(product, "sku", "") or ""
        handle = getattr(product, "handle", "") or ""
        category = getattr(product, "category", "") or ""
        manufacturer = getattr(product, "manufacturer", "") or ""
        brand = getattr(product, "brand", "") or ""
        engine = getattr(product, "engine", "") or ""
        
        # Computed fields
        try:
            product_type = infer_product_type(title)
        except:
            product_type = "Part"
        
        try:
            template_suffix = infer_template_suffix(title, category, product_type)
        except:
            template_suffix = ""
        
        # Pricing
        pricing = getattr(product, "pricing", {}) or {}
        price = float(pricing.get("selling_price", 0) or pricing.get("hhp_price", 0) or pricing.get("price", 0) or 0)
        
        # Cost - use PAI cost if available, else regular cost
        pai_cost = float(getattr(product, "pai_cost", 0) or 0)
        cost = pai_cost if pai_cost > 0 else float(getattr(product, "cost", 0) or 0)
        
        compare_at = float(pricing.get("compare_at_price", 0) or 0)
        
        # Set Product Info fields
        self._shopify_title.setText(title or "-")
        self._shopify_sku.setText(sku or "-")
        self._shopify_handle.setText(handle or "-")
        self._shopify_product_type.setText(product_type or "-")
        self._shopify_template_suffix.setText(template_suffix or "(default)")
        self._shopify_vendor.setText("JAK's Diesel")
        self._shopify_status.setText("active" if True else "draft")
        
        # Show selected image count
        image_count = len(self._selected_images) if self._selected_images else 0
        self._shopify_images.setText(f"{image_count} image(s) selected" if image_count else "No images selected")
        
        # Set Pricing fields
        self._shopify_price.setText(f"${price:,.2f}" if price else "-")
        self._shopify_cost.setText(f"${cost:,.2f}" if cost else "-")
        self._shopify_compare_at.setText(f"${compare_at:,.2f}" if compare_at else "-")
        
        # Collections
        collections = []
        if category:
            collections.append(f"📁 {category}")
        if manufacturer and manufacturer.lower() not in {"all", "unknown"}:
            collections.append(f"🏭 {manufacturer}")
        if product_type == "Turbocharger":
            collections.append("📁 Turbochargers")
        self._shopify_collections.setText("\n".join(collections) if collections else "(none)")
        
        # Tags
        try:
            cpl_list = getattr(product, "cpl_list", None)
            engine_model_tags = getattr(product, "engine_model_tags", None)
            tags_str = infer_tags(title, brand, engine, category, cpl_list, engine_model_tags, manufacturer=manufacturer)
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]
            
            # Format tags nicely
            tag_lines = []
            for i, tag in enumerate(tags):
                if i < 20:  # Limit display
                    tag_lines.append(f"🏷️ {tag}")
            if len(tags) > 20:
                tag_lines.append(f"... and {len(tags) - 20} more")
            self._shopify_tags.setText("\n".join(tag_lines) if tag_lines else "(none)")
        except:
            self._shopify_tags.setText("(error computing tags)")
        
        # Metafields
        try:
            desired_mf, _ = build_desired_metafields(product)
            mf_lines = []
            for key, value in desired_mf.items():
                if value:
                    # Truncate long values
                    display_val = str(value)[:100] + ("..." if len(str(value)) > 100 else "")
                    mf_lines.append(f"jaks.{key}: {display_val}")
            self._shopify_metafields.setText("\n".join(mf_lines) if mf_lines else "(none)")
        except:
            self._shopify_metafields.setText("(error computing metafields)")
        
        # Description HTML (raw)
        desc_html = getattr(product, "description_jaks_html", "") or ""
        if desc_html:
            # Show raw HTML, truncated
            display_html = desc_html[:2000] + ("..." if len(desc_html) > 2000 else "")
            self._shopify_desc_browser.setPlainText(display_html)
        else:
            self._shopify_desc_browser.setPlainText("(no description)")
    
    def _load_images(self):
        """Load product images - uses selected images if provided, otherwise discovers from folder."""
        # Use provided selected images if available
        if self._selected_images:
            image_files = self._selected_images
        else:
            # Fallback: discover images from folder (original behavior)
            product = self._product
            handle = getattr(product, "handle", "") or ""
            if not handle:
                sku = getattr(product, "sku", "") or ""
                handle = sku.replace("JAKS-", "")
            
            category = getattr(product, "category", "") or ""
            manufacturer = getattr(product, "manufacturer", "") or ""
            
            cat_folder = category.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
            mfr_folder = manufacturer.lower().replace(" ", "_").replace("&", "_").replace("-", "_")
            
            # Handle might have hyphens but folder uses underscores
            handle_underscore = handle.lower().replace("-", "_")
            
            image_files = []
            for candidate in [
                Path("output") / cat_folder / mfr_folder / f"jaks_{handle_underscore}" / "images",
                Path("output") / cat_folder / mfr_folder / f"jaks_{handle}" / "images",
                Path("output") / cat_folder / mfr_folder / handle / "images",
                Path("output/images") / handle,
                Path("output") / cat_folder / f"jaks_{handle_underscore}" / "images",
            ]:
                if candidate.exists():
                    image_files = sorted(candidate.glob("*.jpg")) + sorted(candidate.glob("*.png"))
                    if image_files:
                        break
        
        if not image_files:
            self._main_image.setText("No Images Available")
            return
        
        # Load main image (first selected image)
        if image_files:
            pixmap = QPixmap(str(image_files[0]))
            if not pixmap.isNull():
                scaled = pixmap.scaled(440, 440, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self._main_image.setPixmap(scaled)
                self._main_image.setText("")
        
        # Load thumbnails (up to 8)
        for i, img_path in enumerate(image_files[:8]):
            if i >= len(self._thumb_labels):
                break
            try:
                pixmap = QPixmap(str(img_path))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._thumb_labels[i].setPixmap(scaled)
                    self._thumb_labels[i].setVisible(True)
            except Exception:
                pass
    
    def _open_in_browser(self):
        """Open product page in browser."""
        handle = getattr(self._product, "handle", "") or ""
        if handle:
            url = f"https://www.jaksdiesel.com/products/{handle}"
            QDesktopServices.openUrl(QUrl(url))