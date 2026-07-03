"""Storage Locations management screen (Phase I)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QSplitter,
    QGroupBox,
    QFormLayout,
)

from db import (
    list_locations,
    delete_location,
    get_products_at_location,
    get_location_utilization,
)

from .location_dialog import LocationDialog


class LocationsScreen(QWidget):
    """Screen for managing storage locations and viewing location contents."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_locations()

    def _build_ui(self) -> None:
        # Search row
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search locations by code, name, zone...")
        self._search.textChanged.connect(self._filter_table)

        self._show_inactive = QCheckBox("Show inactive")
        self._show_inactive.stateChanged.connect(self._load_locations)

        self._add_btn = QPushButton("➕ Add Location")
        self._add_btn.clicked.connect(self._on_add)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)

        self._delete_btn = QPushButton("🗑️ Deactivate")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        search_row.addWidget(self._search, 1)
        search_row.addWidget(self._show_inactive)
        search_row.addStretch()
        search_row.addWidget(self._add_btn)
        search_row.addWidget(self._edit_btn)
        search_row.addWidget(self._delete_btn)

        # Main locations table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "Code", "Name", "Zone", "Aisle", "Shelf", "Bin", "Products", "Qty"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_edit)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # Products at location panel (right side)
        self._products_group = QGroupBox("Products at Selected Location")
        self._products_table = QTableWidget(0, 4)
        self._products_table.setHorizontalHeaderLabels([
            "SKU", "Title", "Qty at Location", "Total Qty"
        ])
        self._products_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._products_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        products_layout = QVBoxLayout()
        products_layout.addWidget(self._products_table)
        self._products_group.setLayout(products_layout)

        # Splitter for main table and products panel
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._table)
        
        splitter.addWidget(left_widget)
        splitter.addWidget(self._products_group)
        splitter.setSizes([600, 400])

        self._status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._status_label)
        self.setLayout(layout)

        self._locations: list[dict] = []

    def _load_locations(self) -> None:
        """Load locations from database with utilization stats."""
        active_only = not self._show_inactive.isChecked()
        self._locations = get_location_utilization() if active_only else list_locations(active_only=False)
        self._populate_table()

    def _populate_table(self) -> None:
        """Populate table with location data."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._locations))

        for row_idx, loc in enumerate(self._locations):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            def num_item(val: int) -> QTableWidgetItem:
                it = QTableWidgetItem()
                it.setData(Qt.ItemDataRole.DisplayRole, val)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(str(loc.get("code", "") or "")))
            self._table.setItem(row_idx, 1, item(str(loc.get("name", "") or "")))
            self._table.setItem(row_idx, 2, item(str(loc.get("zone", "") or "")))
            self._table.setItem(row_idx, 3, item(str(loc.get("aisle", "") or "")))
            self._table.setItem(row_idx, 4, item(str(loc.get("shelf", "") or "")))
            self._table.setItem(row_idx, 5, item(str(loc.get("bin", "") or "")))
            
            # Product count and total qty from utilization query
            product_count = int(loc.get("product_count", 0) or 0)
            total_qty = int(loc.get("total_quantity", 0) or 0)
            self._table.setItem(row_idx, 6, num_item(product_count))
            self._table.setItem(row_idx, 7, num_item(total_qty))

            # Color code based on utilization
            if product_count > 0:
                for col in range(8):
                    item_widget = self._table.item(row_idx, col)
                    if item_widget:
                        item_widget.setBackground(Qt.GlobalColor.darkGreen)
                        item_widget.setForeground(Qt.GlobalColor.white)

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._locations)} locations")

    def _filter_table(self) -> None:
        """Filter table based on search text."""
        query = self._search.text().lower()
        for row in range(self._table.rowCount()):
            match = False
            for col in range(self._table.columnCount()):
                item = self._table.item(row, col)
                if item and query in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

    def _on_selection_changed(self) -> None:
        """Enable/disable buttons and load products at selected location."""
        has_selection = len(self._table.selectionModel().selectedRows()) > 0
        self._edit_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)
        
        if has_selection:
            self._load_products_at_location()
        else:
            self._products_table.setRowCount(0)

    def _load_products_at_location(self) -> None:
        """Load products stored at the selected location."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._locations):
            return
        
        location_id = self._locations[row].get("id")
        if not location_id:
            return
        
        products = get_products_at_location(location_id)
        self._products_table.setRowCount(len(products))

        for row_idx, p in enumerate(products):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._products_table.setItem(row_idx, 0, item(str(p.get("sku", "") or "")))
            self._products_table.setItem(row_idx, 1, item(str(p.get("title", "") or "")[:50]))
            
            loc_qty = QTableWidgetItem()
            loc_qty.setData(Qt.ItemDataRole.DisplayRole, int(p.get("location_qty", 0) or 0))
            self._products_table.setItem(row_idx, 2, loc_qty)
            
            total_qty = QTableWidgetItem()
            total_qty.setData(Qt.ItemDataRole.DisplayRole, int(p.get("qty_on_hand", 0) or 0))
            self._products_table.setItem(row_idx, 3, total_qty)

        self._products_table.resizeColumnsToContents()
        
        loc_code = self._locations[row].get("code", "")
        self._products_group.setTitle(f"Products at {loc_code} ({len(products)} items)")

    def _on_add(self) -> None:
        """Open dialog to add new location."""
        dlg = LocationDialog(parent=self)
        if dlg.exec():
            self._load_locations()

    def _on_edit(self) -> None:
        """Open dialog to edit selected location."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._locations):
            return
        
        location_id = self._locations[row].get("id")
        if not location_id:
            return
        
        dlg = LocationDialog(location_id=location_id, parent=self)
        if dlg.exec():
            self._load_locations()

    def _on_delete(self) -> None:
        """Deactivate selected location."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._locations):
            return

        loc = self._locations[row]
        location_id = loc.get("id")
        code = loc.get("code", "")

        # Check if location has products
        products = get_products_at_location(location_id)
        if products:
            QMessageBox.warning(
                self,
                "Cannot Deactivate",
                f"Location {code} has {len(products)} products assigned.\n"
                "Remove products from this location first."
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm Deactivate",
            f"Deactivate location '{code}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_location(location_id)
            self._load_locations()

    def refresh(self) -> None:
        """Public method to refresh the screen."""
        self._load_locations()
