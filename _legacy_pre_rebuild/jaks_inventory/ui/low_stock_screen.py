"""Low Stock Report Screen - Shows products below reorder threshold."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import get_low_stock_products, auto_calculate_min_max

from .product_detail_dialog import ProductDetailDialog


class LowStockScreen(QWidget):
    """Screen showing products at or below stock threshold."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Threshold control
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 100)
        self._threshold_spin.setValue(5)
        self._threshold_spin.setPrefix("≤ ")
        self._threshold_spin.valueChanged.connect(self._refresh)

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh)

        self._auto_minmax_btn = QPushButton("🔄 Auto Min/Max")
        self._auto_minmax_btn.setToolTip(
            "Recalculate min/max for all products using sales velocity"
        )
        self._auto_minmax_btn.clicked.connect(self._on_auto_minmax)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("font-size: 14pt; font-weight: bold;")

        # Table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Manufacturer", "Qty", "Action"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)

        # Layout
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Threshold:"))
        top_row.addWidget(self._threshold_spin)
        top_row.addWidget(self._refresh_btn)
        top_row.addWidget(self._auto_minmax_btn)
        top_row.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

        self._last_results: list[dict[str, Any]] = []
        
        # Initial load
        self._refresh()

    def _refresh(self) -> None:
        """Refresh the low stock list."""
        threshold = self._threshold_spin.value()
        
        try:
            results = get_low_stock_products(threshold)
        except Exception as e:
            self._summary_label.setText(f"Error: {e}")
            return
        
        self._last_results = list(results or [])
        
        # Count out-of-stock vs low
        out_of_stock = sum(1 for r in self._last_results if (r.get("qty_on_hand") or 0) <= 0)
        low_stock = len(self._last_results) - out_of_stock
        
        if self._last_results:
            self._summary_label.setText(
                f"⚠️ {len(self._last_results)} products at or below {threshold} — "
                f"<span style='color: red'>{out_of_stock} out of stock</span>, "
                f"<span style='color: orange'>{low_stock} low stock</span>"
            )
        else:
            self._summary_label.setText(f"✅ All products above threshold ({threshold})")
        
        self._populate_table()

    def _populate_table(self) -> None:
        """Populate the table with low stock products."""
        self._table.setRowCount(len(self._last_results))
        
        for row_idx, r in enumerate(self._last_results):
            sku = str(r.get("sku", "") or "")
            title = str(r.get("title", "") or "")
            manufacturer = str(r.get("manufacturer", "") or "")
            qty = int(r.get("qty_on_hand", 0) or 0)

            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(sku))
            self._table.setItem(row_idx, 1, item(title))
            self._table.setItem(row_idx, 2, item(manufacturer))
            
            # Color-coded qty
            qty_item = item(str(qty))
            if qty <= 0:
                qty_item.setBackground(QColor(255, 200, 200))  # Light red
                qty_item.setForeground(QColor("darkred"))
            else:
                qty_item.setBackground(QColor(255, 255, 200))  # Light yellow
                qty_item.setForeground(QColor("darkorange"))
            self._table.setItem(row_idx, 3, qty_item)
            
            # Action button placeholder (we'll use double-click instead)
            self._table.setItem(row_idx, 4, item("Double-click to open"))

        self._table.resizeColumnsToContents()

    def _on_row_double_clicked(self, row: int, col: int) -> None:
        """Open product detail when row is double-clicked."""
        if row < 0 or row >= len(self._last_results):
            return
        sku = str((self._last_results[row] or {}).get("sku", "") or "")
        if not sku:
            return
        dlg = ProductDetailDialog(sku=sku, parent=self)
        dlg.exec()
        # Refresh after dialog closes (qty may have changed)
        self._refresh()

    def _on_auto_minmax(self) -> None:
        """Recalculate min/max for all products using sales velocity."""
        reply = QMessageBox.question(
            self, "Auto Min/Max",
            "This will recalculate reorder min/max levels for ALL products\n"
            "based on sales velocity and vendor lead times.\n\nContinue?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = auto_calculate_min_max()
            QMessageBox.information(
                self, "Done",
                f"Updated {result.get('updated', 0)} products.\n"
                f"Skipped {result.get('skipped', 0)} (no sales history)."
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Auto Min/Max failed: {e}")
