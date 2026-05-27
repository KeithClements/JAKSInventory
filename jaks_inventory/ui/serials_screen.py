"""Serial/Lot number tracking screen (Phase I Part 2)."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QSplitter,
)

from db import (
    list_serials,
    delete_serial,
    get_available_serials,
    search_serials,
    get_all_products,
    sell_serial,
    return_serial,
)

from .serial_dialog import SerialDialog

log = logging.getLogger(__name__)


class SerialsScreen(QWidget):
    """Screen for managing serial/lot numbers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_serials()

    def _build_ui(self) -> None:
        # Filter row
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search serial #, lot #, or SKU...")
        self._search.returnPressed.connect(self._on_search)

        self._status_filter = QComboBox()
        self._status_filter.addItem("All Status", None)
        self._status_filter.addItem("✅ Available", "available")
        self._status_filter.addItem("📤 Sold", "sold")
        self._status_filter.addItem("🔒 Reserved", "reserved")
        self._status_filter.addItem("⚠️ Defective", "defective")
        self._status_filter.currentIndexChanged.connect(self._load_serials)

        self._product_filter = QComboBox()
        self._product_filter.addItem("All Products", None)
        self._load_products_combo()
        self._product_filter.currentIndexChanged.connect(self._load_serials)

        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self._on_search)

        self._add_btn = QPushButton("➕ Add Serial")
        self._add_btn.clicked.connect(self._on_add)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)

        self._sell_btn = QPushButton("📤 Mark Sold")
        self._sell_btn.clicked.connect(self._on_sell)
        self._sell_btn.setEnabled(False)

        self._return_btn = QPushButton("↩️ Return")
        self._return_btn.clicked.connect(self._on_return)
        self._return_btn.setEnabled(False)

        self._delete_btn = QPushButton("🗑️ Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        filter_row.addWidget(self._search, 1)
        filter_row.addWidget(search_btn)
        filter_row.addWidget(QLabel("Status:"))
        filter_row.addWidget(self._status_filter)
        filter_row.addWidget(QLabel("Product:"))
        filter_row.addWidget(self._product_filter)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._edit_btn)
        btn_row.addWidget(self._sell_btn)
        btn_row.addWidget(self._return_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()

        # Main table
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels([
            "Serial #", "Lot #", "SKU", "Product", "Status", 
            "Location", "Cost", "Received", "Warranty"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_edit)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        self._status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addLayout(filter_row)
        layout.addLayout(btn_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)
        self.setLayout(layout)

        self._serials: list[dict] = []

    def _load_products_combo(self) -> None:
        """Load products into filter combo."""
        try:
            products = get_all_products()
            for p in products[:200]:  # Limit for performance
                sku = str(p.get("sku", ""))
                title = str(p.get("title", ""))[:30]
                display = f"{sku} - {title}" if title else sku
                self._product_filter.addItem(display, p.get("id"))
        except Exception:
            pass

    def _load_serials(self) -> None:
        """Load serials from database with current filters."""
        status = self._status_filter.currentData()
        product_id = self._product_filter.currentData()
        
        try:
            self._serials = list_serials(
                product_id=product_id,
                status=status,
                limit=500
            )
        except Exception as e:
            self._serials = []
            log.warning("Serials load failed: %s", e)
        
        self._populate_table()

    def _populate_table(self) -> None:
        """Populate table with serial data."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._serials))

        status_colors = {
            "available": QColor("#2e7d32"),  # Green
            "sold": QColor("#1565c0"),       # Blue
            "reserved": QColor("#f57c00"),   # Orange
            "defective": QColor("#c62828"),  # Red
        }

        for row_idx, s in enumerate(self._serials):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(s.get("serial_number", "")))
            self._table.setItem(row_idx, 1, item(s.get("lot_number", "")))
            self._table.setItem(row_idx, 2, item(s.get("sku", "")))
            self._table.setItem(row_idx, 3, item(str(s.get("title", ""))[:40]))
            
            # Status with color
            status = str(s.get("status", ""))
            status_item = item(status.title())
            if status in status_colors:
                status_item.setForeground(status_colors[status])
            self._table.setItem(row_idx, 4, status_item)
            
            self._table.setItem(row_idx, 5, item(s.get("location_code", "")))
            
            cost = float(s.get("cost", 0) or 0)
            cost_item = item(f"${cost:,.2f}" if cost else "")
            self._table.setItem(row_idx, 6, cost_item)
            
            self._table.setItem(row_idx, 7, item(str(s.get("received_date", "") or "")[:10]))
            self._table.setItem(row_idx, 8, item(str(s.get("warranty_expiry", "") or "")[:10]))

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._serials)} serial numbers")

    def _on_search(self) -> None:
        """Search serials."""
        query = self._search.text().strip()
        if query:
            try:
                self._serials = search_serials(query)
                self._populate_table()
            except Exception as e:
                QMessageBox.warning(self, "Search Error", str(e))
        else:
            self._load_serials()

    def _on_selection_changed(self) -> None:
        """Update button states based on selection."""
        rows = self._table.selectionModel().selectedRows()
        has_selection = len(rows) > 0
        
        self._edit_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)
        
        if has_selection:
            row = rows[0].row()
            if row < len(self._serials):
                status = self._serials[row].get("status", "")
                self._sell_btn.setEnabled(status == "available")
                self._return_btn.setEnabled(status == "sold")
            else:
                self._sell_btn.setEnabled(False)
                self._return_btn.setEnabled(False)
        else:
            self._sell_btn.setEnabled(False)
            self._return_btn.setEnabled(False)

    def _on_add(self) -> None:
        """Add new serial number(s)."""
        dlg = SerialDialog(parent=self)
        if dlg.exec():
            self._load_serials()

    def _on_edit(self) -> None:
        """Edit selected serial."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._serials):
            return
        
        serial_id = self._serials[row].get("id")
        if serial_id:
            dlg = SerialDialog(serial_id=serial_id, parent=self)
            if dlg.exec():
                self._load_serials()

    def _on_sell(self) -> None:
        """Mark selected serial as sold."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._serials):
            return
        
        serial = self._serials[row]
        serial_id = serial.get("id")
        serial_num = serial.get("serial_number", "")
        
        reply = QMessageBox.question(
            self,
            "Mark as Sold",
            f"Mark serial '{serial_num}' as sold?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                sell_serial(serial_id)
                self._load_serials()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update: {e}")

    def _on_return(self) -> None:
        """Mark sold serial as returned."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._serials):
            return
        
        serial = self._serials[row]
        serial_id = serial.get("id")
        serial_num = serial.get("serial_number", "")
        
        reply = QMessageBox.question(
            self,
            "Return Serial",
            f"Mark serial '{serial_num}' as returned (available)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                return_serial(serial_id, notes="Returned via inventory screen")
                self._load_serials()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to update: {e}")

    def _on_delete(self) -> None:
        """Delete selected serial."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._serials):
            return
        
        serial = self._serials[row]
        serial_id = serial.get("id")
        serial_num = serial.get("serial_number", "")
        
        reply = QMessageBox.question(
            self,
            "Delete Serial",
            f"Delete serial '{serial_num}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_serial(serial_id)
                self._load_serials()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete: {e}")

    def refresh(self) -> None:
        """Public method to refresh the screen."""
        self._load_serials()
