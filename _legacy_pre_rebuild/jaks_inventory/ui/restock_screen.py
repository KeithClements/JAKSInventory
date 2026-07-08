"""Min/Max Restock Interface - Manage reorder points and generate POs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
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

from db import (
    get_restock_products,
    get_restock_summary,
    update_min_max,
    get_all_vendors,
    get_reorder_suggestions,
)


class _KPICard(QFrame):
    """Small KPI card for restock summary."""

    def __init__(self, title: str, color: str = "#3d4a38", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"background: {color}; border-radius: 6px; padding: 8px;")
        self._title = QLabel(title)
        self._title.setStyleSheet("color: white; font-size: 10pt;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: #ccc; font-size: 9pt;")
        lay = QVBoxLayout()
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        self.setLayout(lay)

    def set_value(self, val: str, sub: str = ""):
        self._value.setText(val)
        self._sub.setText(sub)


class RestockScreen(QWidget):
    """Min/Max restock management screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[dict] = []

        # KPI cards
        self._card_below = _KPICard("Below Min", "#b22222")
        self._card_at_min = _KPICard("At Min", "#cc8800")
        self._card_above = _KPICard("Above Max", "#4169e1")
        self._card_ok = _KPICard("OK", "#2e7d32")

        cards = QHBoxLayout()
        for c in [self._card_below, self._card_at_min, self._card_above, self._card_ok]:
            cards.addWidget(c)

        # Filter
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["Below Min", "At Min", "Above Max", "OK", "All"])
        self._filter_combo.currentIndexChanged.connect(self._refresh)

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh)

        self._suggest_btn = QPushButton("📋 PO Suggestions")
        self._suggest_btn.clicked.connect(self._show_reorder_suggestions)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        filter_row.addWidget(self._filter_combo)
        filter_row.addWidget(self._refresh_btn)
        filter_row.addWidget(self._suggest_btn)
        filter_row.addStretch()

        # Table
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Manufacturer", "On Hand", "Min",
            "Max", "Reorder Qty", "Cost", "Vendor"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        # Edit controls
        edit_frame = QFrame()
        edit_frame.setFrameShape(QFrame.Shape.StyledPanel)
        edit_lay = QGridLayout()
        edit_lay.addWidget(QLabel("Selected SKU:"), 0, 0)
        self._edit_sku_label = QLabel("—")
        self._edit_sku_label.setStyleSheet("font-weight: bold;")
        edit_lay.addWidget(self._edit_sku_label, 0, 1)

        edit_lay.addWidget(QLabel("Min Qty:"), 1, 0)
        self._min_spin = QSpinBox()
        self._min_spin.setRange(0, 99999)
        edit_lay.addWidget(self._min_spin, 1, 1)

        edit_lay.addWidget(QLabel("Max Qty:"), 1, 2)
        self._max_spin = QSpinBox()
        self._max_spin.setRange(0, 99999)
        edit_lay.addWidget(self._max_spin, 1, 3)

        edit_lay.addWidget(QLabel("Vendor:"), 2, 0)
        self._vendor_combo = QComboBox()
        self._vendor_combo.addItem("(none)", 0)
        edit_lay.addWidget(self._vendor_combo, 2, 1, 1, 2)

        self._save_btn = QPushButton("💾 Save Min/Max")
        self._save_btn.clicked.connect(self._save_min_max)
        edit_lay.addWidget(self._save_btn, 2, 3)
        edit_frame.setLayout(edit_lay)

        # Main layout
        layout = QVBoxLayout()
        layout.addLayout(cards)
        layout.addLayout(filter_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(edit_frame)
        self.setLayout(layout)

        self._selected_product_id = None
        self._load_vendors()
        self._refresh()

    def _load_vendors(self):
        try:
            vendors = get_all_vendors()
            for v in vendors:
                self._vendor_combo.addItem(
                    str(v.get("name", "")), int(v.get("id", 0))
                )
        except Exception:
            pass

    def _refresh(self) -> None:
        filter_map = {0: "below_min", 1: "at_min", 2: "above_max", 3: "ok", 4: "all"}
        status = filter_map.get(self._filter_combo.currentIndex(), "below_min")
        try:
            self._results = get_restock_products(status)
            summary = get_restock_summary()
            self._card_below.set_value(
                str(summary.get("below_min", 0)),
                f"${summary.get('below_min_cost', 0):,.2f} to restock"
            )
            self._card_at_min.set_value(str(summary.get("at_min", 0)))
            self._card_above.set_value(str(summary.get("above_max", 0)))
            self._card_ok.set_value(str(summary.get("ok", 0)))
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            self._results = []
        self._populate_table()

    def _populate_table(self):
        self._table.setRowCount(len(self._results))
        for i, r in enumerate(self._results):
            qty = int(r.get("qty_on_hand") or 0)
            mn = int(r.get("min_qty") or 0)
            mx = int(r.get("max_qty") or 0)
            reorder = max(0, mn - qty) if qty < mn else 0

            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(i, 0, item(r.get("sku", "")))
            self._table.setItem(i, 1, item(r.get("title", "")))
            self._table.setItem(i, 2, item(r.get("manufacturer", "")))

            qty_item = item(qty)
            if qty < mn:
                qty_item.setBackground(QColor(255, 200, 200))
                qty_item.setForeground(QColor("darkred"))
            elif mx > 0 and qty > mx:
                qty_item.setBackground(QColor(200, 200, 255))
                qty_item.setForeground(QColor("darkblue"))
            self._table.setItem(i, 3, qty_item)
            self._table.setItem(i, 4, item(mn))
            self._table.setItem(i, 5, item(mx if mx else "—"))

            ro_item = item(reorder if reorder else "—")
            if reorder > 0:
                ro_item.setBackground(QColor(255, 255, 200))
            self._table.setItem(i, 6, ro_item)

            cost = float(r.get("cost") or 0)
            self._table.setItem(i, 7, item(f"${cost:,.2f}"))
            self._table.setItem(i, 8, item(r.get("vendor_name") or "—"))

        self._table.resizeColumnsToContents()

    def _on_double_click(self, row: int, _col: int):
        if row < 0 or row >= len(self._results):
            return
        r = self._results[row]
        self._selected_product_id = r.get("id")
        self._edit_sku_label.setText(str(r.get("sku", "")))
        self._min_spin.setValue(int(r.get("min_qty") or 0))
        self._max_spin.setValue(int(r.get("max_qty") or 0))
        vid = int(r.get("preferred_vendor_id") or 0)
        idx = self._vendor_combo.findData(vid)
        if idx >= 0:
            self._vendor_combo.setCurrentIndex(idx)

    def _save_min_max(self):
        if not self._selected_product_id:
            QMessageBox.information(self, "Info", "Double-click a row to select a product first.")
            return
        vid = self._vendor_combo.currentData()
        try:
            update_min_max(
                self._selected_product_id,
                min_qty=self._min_spin.value(),
                max_qty=self._max_spin.value(),
                preferred_vendor_id=vid if vid else None,
            )
            QMessageBox.information(self, "Saved", "Min/Max updated.")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _show_reorder_suggestions(self):
        """Show products that need reordering, grouped by vendor."""
        try:
            suggestions = get_reorder_suggestions()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if not suggestions:
            QMessageBox.information(self, "Reorder", "No products below min stock levels.")
            return
        # Group by vendor
        by_vendor = {}
        for s in suggestions:
            vendor = s.get("vendor_name") or "(No Vendor)"
            by_vendor.setdefault(vendor, []).append(s)
        lines = [f"\n=== PO SUGGESTIONS ({len(suggestions)} items) ===\n"]
        for vendor, items in sorted(by_vendor.items()):
            total = sum(int(i.get('suggested_qty', 0) or 0) * float(i.get('cost', 0) or 0) for i in items)
            lines.append(f"\n--- {vendor} (Est. ${total:,.2f}) ---")
            for item in items:
                lines.append(
                    f"  {item.get('sku','')}: {item.get('title','')[:35]}  "
                    f"On Hand: {item.get('qty_on_hand',0)}  "
                    f"Min: {item.get('min_qty',0)}  "
                    f"Order Qty: {item.get('suggested_qty',0)}  "
                    f"@ ${float(item.get('cost',0) or 0):,.2f}")
        QMessageBox.information(self, "Reorder Suggestions", "\n".join(lines))
