"""Restock Wizard — generate draft POs from below-min-stock products."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from db import get_reorder_suggestions, get_all_vendors, create_purchase_order


class RestockWizardDialog(QDialog):
    """Dialog that shows below-min products grouped by vendor and creates draft POs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate Restock POs")
        self.setMinimumSize(900, 600)
        self._suggestions: list[dict] = []
        self._checkboxes: list[QCheckBox] = []
        self._qty_spins: list[QSpinBox] = []
        self._created_pos: list[str] = []
        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Header
        header = QLabel("Generate Restock Purchase Orders")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 4px;")
        layout.addWidget(header)

        desc = QLabel(
            "Products below minimum stock, grouped by preferred vendor. "
            "Adjust order quantities, uncheck items to skip, then generate draft POs."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; padding: 0 4px 8px 4px;")
        layout.addWidget(desc)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Vendor:"))
        self._vendor_filter = QComboBox()
        self._vendor_filter.addItem("All Vendors", None)
        for v in get_all_vendors():
            self._vendor_filter.addItem(v.get("name", ""), v.get("id"))
        self._vendor_filter.currentIndexChanged.connect(self._load_data)
        filter_row.addWidget(self._vendor_filter)

        self._select_all_cb = QCheckBox("Select All")
        self._select_all_cb.setChecked(True)
        self._select_all_cb.toggled.connect(self._toggle_all)
        filter_row.addWidget(self._select_all_cb)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("font-weight: bold; color: #6B8E23;")
        filter_row.addStretch()
        filter_row.addWidget(self._summary_label)
        layout.addLayout(filter_row)

        # Table
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels([
            "✓", "Vendor", "SKU", "Title", "On Hand", "Min", "Max", "Need", "Order Qty"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        self._print_btn = QPushButton("🖨️ Copy Restock List")
        self._print_btn.clicked.connect(self._copy_list)
        btn_row.addWidget(self._print_btn)
        btn_row.addStretch()

        self._generate_btn = QPushButton("📦 Generate Draft POs")
        self._generate_btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; "
            "padding: 10px 24px; font-weight: bold; font-size: 11pt; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        self._generate_btn.clicked.connect(self._generate_pos)
        btn_row.addWidget(self._generate_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _load_data(self) -> None:
        vendor_id = self._vendor_filter.currentData()
        self._suggestions = get_reorder_suggestions(vendor_id=vendor_id)
        self._populate_table()

    def _populate_table(self) -> None:
        self._checkboxes.clear()
        self._qty_spins.clear()
        self._table.setRowCount(len(self._suggestions))

        current_vendor = None
        vendor_color_a = QColor(45, 58, 40)  # darker olive
        vendor_color_b = QColor(50, 65, 45)
        use_a = True

        for i, s in enumerate(self._suggestions):
            vendor = s.get("vendor_name") or "(No Vendor)"
            if vendor != current_vendor:
                current_vendor = vendor
                use_a = not use_a
            bg = vendor_color_a if use_a else vendor_color_b

            # Checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cb.toggled.connect(self._update_summary)
            self._checkboxes.append(cb)
            self._table.setCellWidget(i, 0, cb)

            def item(text, color=None):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if color:
                    it.setBackground(color)
                return it

            self._table.setItem(i, 1, item(vendor, bg))
            self._table.setItem(i, 2, item(s.get("sku", "")))
            self._table.setItem(i, 3, item(s.get("title", "")))

            qty = int(s.get("qty_on_hand") or 0)
            mn = int(s.get("min_qty") or 0)
            mx = int(s.get("max_qty") or 0)
            need = max(0, mn - qty)
            suggested = int(s.get("suggested_qty") or need)

            qty_item = item(qty)
            qty_item.setForeground(QColor("#ff6b6b"))
            self._table.setItem(i, 4, qty_item)
            self._table.setItem(i, 5, item(mn))
            self._table.setItem(i, 6, item(mx if mx else "—"))
            self._table.setItem(i, 7, item(need))

            # Editable order qty spin
            spin = QSpinBox()
            spin.setRange(0, 99999)
            spin.setValue(suggested)
            spin.valueChanged.connect(self._update_summary)
            self._qty_spins.append(spin)
            self._table.setCellWidget(i, 8, spin)

        self._table.resizeColumnsToContents()
        # Give title column more room
        self._table.setColumnWidth(3, max(200, self._table.columnWidth(3)))
        self._update_summary()

    def _toggle_all(self, checked: bool) -> None:
        for cb in self._checkboxes:
            cb.setChecked(checked)

    def _update_summary(self) -> None:
        items = 0
        vendors = set()
        total_cost = 0.0
        for i, s in enumerate(self._suggestions):
            if i < len(self._checkboxes) and self._checkboxes[i].isChecked():
                qty = self._qty_spins[i].value() if i < len(self._qty_spins) else 0
                if qty > 0:
                    items += 1
                    vendors.add(s.get("vendor_name") or "(No Vendor)")
                    cost = float(s.get("cost") or 0)
                    total_cost += qty * cost
        self._summary_label.setText(
            f"{items} items  |  {len(vendors)} vendor(s)  |  Est. ${total_cost:,.2f}"
        )
        self._generate_btn.setEnabled(items > 0)

    def _get_checked_items(self) -> list[dict]:
        """Return checked items with their override order qty."""
        result = []
        for i, s in enumerate(self._suggestions):
            if i < len(self._checkboxes) and self._checkboxes[i].isChecked():
                qty = self._qty_spins[i].value() if i < len(self._qty_spins) else 0
                if qty > 0:
                    item = dict(s)
                    item["order_qty"] = qty
                    result.append(item)
        return result

    def _copy_list(self) -> None:
        """Copy restock list to clipboard."""
        items = self._get_checked_items()
        if not items:
            QMessageBox.information(self, "Empty", "No items selected.")
            return

        lines = ["RESTOCK LIST", "=" * 60]
        by_vendor: dict[str, list] = {}
        for item in items:
            v = item.get("vendor_name") or "(No Vendor)"
            by_vendor.setdefault(v, []).append(item)

        for vendor, vitems in sorted(by_vendor.items()):
            vtotal = sum(it["order_qty"] * float(it.get("cost") or 0) for it in vitems)
            lines.append(f"\n--- {vendor} (Est. ${vtotal:,.2f}) ---")
            for it in vitems:
                lines.append(
                    f"  {it['sku']:20s}  {str(it.get('title',''))[:30]:30s}  "
                    f"Qty: {it['order_qty']:4d}  @ ${float(it.get('cost',0)):,.2f}"
                )
        lines.append(f"\nTotal: {len(items)} items, {len(by_vendor)} vendor(s)")

        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self, "Copied", "Restock list copied to clipboard.")

    def _generate_pos(self) -> None:
        items = self._get_checked_items()
        if not items:
            return

        # Group by vendor
        by_vendor: dict[int | None, list] = {}
        for item in items:
            vid = item.get("preferred_vendor_id")
            by_vendor.setdefault(vid, []).append(item)

        vendor_count = len(by_vendor)
        reply = QMessageBox.question(
            self, "Generate POs",
            f"Create {vendor_count} draft Purchase Order(s) "
            f"with {len(items)} total line items?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._created_pos.clear()
        errors = []

        for vid, vitems in by_vendor.items():
            if not vid:
                errors.append("Skipped items with no preferred vendor")
                continue

            # Check for existing draft POs for this vendor
            from db import find_open_purchase_orders_for_vendor
            existing = [
                po for po in find_open_purchase_orders_for_vendor(vid)
                if (po.get("status") or "").lower() == "draft"
            ]
            if existing:
                po_nums = ", ".join(po.get("po_number", str(po.get("id"))) for po in existing)
                vendor_name = vitems[0].get("preferred_vendor_name") or f"Vendor #{vid}"
                dup_reply = QMessageBox.question(
                    self,
                    "Existing Draft PO",
                    f"{vendor_name} already has open draft PO(s): {po_nums}\n\n"
                    "Create a NEW draft PO anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if dup_reply != QMessageBox.StandardButton.Yes:
                    continue

            lines = []
            for it in vitems:
                lines.append({
                    "product_id": it.get("id"),
                    "quantity_ordered": it["order_qty"],
                    "unit_cost": float(it.get("cost") or 0),
                })

            result = create_purchase_order(
                vendor_id=vid,
                notes="Auto-generated restock PO",
                lines=lines,
            )
            if result.get("success"):
                self._created_pos.append(result.get("po_number", "?"))
            else:
                errors.append(f"Vendor {vid}: {result.get('error', 'Unknown')}")

        msg = f"Created {len(self._created_pos)} draft PO(s):\n"
        msg += ", ".join(self._created_pos) if self._created_pos else "(none)"
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors)

        QMessageBox.information(self, "Restock Complete", msg)
        self.accept()
