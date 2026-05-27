"""Work Orders Screen - Create and manage repair work orders."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_work_order,
    get_work_order,
    list_work_orders,
    update_work_order,
    add_wo_line,
    get_wo_lines,
    remove_wo_line,
    get_all_customers,
    list_vehicles,
    get_product_by_sku,
)


class _NewWODialog(QDialog):
    """Dialog to create a new work order."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Work Order")
        self.setMinimumWidth(500)

        form = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.addItem("(select customer)", 0)
        try:
            for c in get_all_customers():
                name = c.get("name", "")
                company = c.get("company", "")
                display = f"{name} — {company}" if company else name
                self.customer_combo.addItem(display, c.get("id"))
        except Exception:
            pass
        self.customer_combo.currentIndexChanged.connect(self._load_vehicles)
        form.addRow("Customer:", self.customer_combo)

        self.vehicle_combo = QComboBox()
        self.vehicle_combo.addItem("(none)", None)
        form.addRow("Vehicle:", self.vehicle_combo)

        self.complaint_edit = QTextEdit()
        self.complaint_edit.setMaximumHeight(80)
        self.complaint_edit.setPlaceholderText("Customer complaint / reason for service...")
        form.addRow("Complaint:", self.complaint_edit)

        self.technician_edit = QLineEdit()
        form.addRow("Technician:", self.technician_edit)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["normal", "high", "urgent"])
        form.addRow("Priority:", self.priority_combo)

        self.labor_rate_spin = QDoubleSpinBox()
        self.labor_rate_spin.setRange(0, 9999)
        self.labor_rate_spin.setValue(125.00)
        self.labor_rate_spin.setPrefix("$")
        form.addRow("Labor Rate:", self.labor_rate_spin)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(60)
        form.addRow("Notes:", self.notes_edit)

        btns = QHBoxLayout()
        ok = QPushButton("Create")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok)
        btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def _load_vehicles(self):
        self.vehicle_combo.clear()
        self.vehicle_combo.addItem("(none)", None)
        cid = self.customer_combo.currentData()
        if not cid:
            return
        try:
            vehicles = list_vehicles(customer_id=cid)
            for v in vehicles:
                display = f"{v.get('unit_number', '')} — {v.get('year', '')} {v.get('make', '')} {v.get('model', '')}"
                self.vehicle_combo.addItem(display.strip(), v.get("id"))
        except Exception:
            pass


class WorkOrdersScreen(QWidget):
    """Work orders management screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._orders: list[dict] = []
        self._current_wo_id: int | None = None
        self._lines: list[dict] = []

        # Top controls
        top_bar = QHBoxLayout()
        self._new_btn = QPushButton("➕ New Work Order")
        self._new_btn.clicked.connect(self._new_wo)
        self._status_filter = QComboBox()
        self._status_filter.addItems(["All", "open", "in_progress", "completed",
                                       "invoiced", "cancelled"])
        self._status_filter.currentIndexChanged.connect(self._refresh)
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh)
        top_bar.addWidget(self._new_btn)
        top_bar.addWidget(QLabel("Status:"))
        top_bar.addWidget(self._status_filter)
        top_bar.addWidget(self._refresh_btn)
        top_bar.addStretch()

        # WO list table
        self._wo_table = QTableWidget(0, 8)
        self._wo_table.setHorizontalHeaderLabels([
            "WO #", "Customer", "Vehicle", "Status", "Priority",
            "Technician", "Total", "Created"
        ])
        self._wo_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._wo_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._wo_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._wo_table.cellDoubleClicked.connect(self._on_wo_selected)

        # WO Detail section
        self._detail_label = QLabel("Select a work order to view details")
        self._detail_label.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 4px;")

        # Status buttons
        status_bar = QHBoxLayout()
        self._start_btn = QPushButton("▶ Start")
        self._start_btn.clicked.connect(lambda: self._set_status("in_progress"))
        self._complete_btn = QPushButton("✅ Complete")
        self._complete_btn.clicked.connect(lambda: self._set_status("completed"))
        self._cancel_wo_btn = QPushButton("❌ Cancel")
        self._cancel_wo_btn.clicked.connect(lambda: self._set_status("cancelled"))
        status_bar.addWidget(self._start_btn)
        status_bar.addWidget(self._complete_btn)
        status_bar.addWidget(self._cancel_wo_btn)
        status_bar.addStretch()

        # Add line controls
        line_bar = QHBoxLayout()
        self._line_type = QComboBox()
        self._line_type.addItems(["part", "labor"])
        self._line_sku = QLineEdit()
        self._line_sku.setPlaceholderText("SKU (for parts)")
        self._line_desc = QLineEdit()
        self._line_desc.setPlaceholderText("Description")
        self._line_qty = QSpinBox()
        self._line_qty.setRange(1, 9999)
        self._line_qty.setValue(1)
        self._line_price = QDoubleSpinBox()
        self._line_price.setRange(0, 999999)
        self._line_price.setPrefix("$")
        self._line_hours = QDoubleSpinBox()
        self._line_hours.setRange(0, 999)
        self._line_hours.setSingleStep(0.5)
        self._add_line_btn = QPushButton("➕ Add Line")
        self._add_line_btn.clicked.connect(self._add_line)
        line_bar.addWidget(QLabel("Type:"))
        line_bar.addWidget(self._line_type)
        line_bar.addWidget(self._line_sku)
        line_bar.addWidget(self._line_desc)
        line_bar.addWidget(QLabel("Qty:"))
        line_bar.addWidget(self._line_qty)
        line_bar.addWidget(QLabel("Price:"))
        line_bar.addWidget(self._line_price)
        line_bar.addWidget(QLabel("Hours:"))
        line_bar.addWidget(self._line_hours)
        line_bar.addWidget(self._add_line_btn)

        # Lines table
        self._line_table = QTableWidget(0, 7)
        self._line_table.setHorizontalHeaderLabels([
            "Type", "SKU", "Description", "Qty", "Unit Price", "Total", "Hours"
        ])
        self._line_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._line_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._line_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self._remove_line_btn = QPushButton("🗑 Remove Selected Line")
        self._remove_line_btn.clicked.connect(self._remove_line)

        self._totals_label = QLabel("")
        self._totals_label.setStyleSheet("font-size: 11pt; font-weight: bold; padding: 4px;")

        # Layout
        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(self._wo_table)
        layout.addWidget(self._detail_label)
        layout.addLayout(status_bar)
        layout.addLayout(line_bar)
        layout.addWidget(self._line_table, 1)
        bbar = QHBoxLayout()
        bbar.addWidget(self._remove_line_btn)
        bbar.addStretch()
        bbar.addWidget(self._totals_label)
        layout.addLayout(bbar)
        self.setLayout(layout)

        self._refresh()

    def _refresh(self):
        idx = self._status_filter.currentIndex()
        status_map = {0: None, 1: "open", 2: "in_progress", 3: "completed",
                      4: "invoiced", 5: "cancelled"}
        status = status_map.get(idx)
        try:
            self._orders = list_work_orders(status=status)
        except Exception:
            self._orders = []
        self._wo_table.setRowCount(len(self._orders))
        for i, wo in enumerate(self._orders):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._wo_table.setItem(i, 0, item(wo.get("wo_number", "")))
            cust = wo.get("customer_name", "")
            comp = wo.get("customer_company", "")
            self._wo_table.setItem(i, 1, item(f"{cust} — {comp}" if comp else cust))
            self._wo_table.setItem(i, 2, item(wo.get("unit_number") or "—"))

            st = str(wo.get("status", ""))
            st_item = item(st)
            color_map = {"open": "blue", "in_progress": "darkorange",
                         "completed": "green", "cancelled": "red", "invoiced": "purple"}
            if st in color_map:
                st_item.setForeground(QColor(color_map[st]))
            self._wo_table.setItem(i, 3, st_item)

            pri = str(wo.get("priority", ""))
            pri_item = item(pri)
            if pri == "urgent":
                pri_item.setBackground(QColor(255, 200, 200))
            elif pri == "high":
                pri_item.setBackground(QColor(255, 255, 200))
            self._wo_table.setItem(i, 4, pri_item)

            self._wo_table.setItem(i, 5, item(wo.get("technician") or "—"))
            total = float(wo.get("total") or 0)
            self._wo_table.setItem(i, 6, item(f"${total:,.2f}"))
            self._wo_table.setItem(i, 7, item(str(wo.get("created_at", ""))[:16]))
        self._wo_table.resizeColumnsToContents()

    def _on_wo_selected(self, row: int, _col: int):
        if row < 0 or row >= len(self._orders):
            return
        wo = self._orders[row]
        self._current_wo_id = wo.get("id")
        try:
            detail = get_work_order(self._current_wo_id)
        except Exception:
            detail = wo
        if detail:
            wo = detail
        cust = wo.get("customer_name", "")
        self._detail_label.setText(
            f"WO #{wo.get('wo_number', '')} — {cust} — {wo.get('status', '')}"
        )
        self._refresh_lines()

    def _refresh_lines(self):
        if not self._current_wo_id:
            return
        try:
            self._lines = get_wo_lines(self._current_wo_id)
        except Exception:
            self._lines = []
        self._line_table.setRowCount(len(self._lines))
        parts_total = 0.0
        labor_hours = 0.0
        for i, ln in enumerate(self._lines):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._line_table.setItem(i, 0, item(ln.get("line_type", "")))
            self._line_table.setItem(i, 1, item(ln.get("sku") or "—"))
            self._line_table.setItem(i, 2, item(ln.get("description") or ln.get("product_title") or ""))
            self._line_table.setItem(i, 3, item(ln.get("quantity", 0)))
            price = float(ln.get("unit_price") or 0)
            self._line_table.setItem(i, 4, item(f"${price:,.2f}"))
            total = float(ln.get("line_total") or 0)
            self._line_table.setItem(i, 5, item(f"${total:,.2f}"))
            hrs = float(ln.get("hours") or 0)
            self._line_table.setItem(i, 6, item(f"{hrs:.1f}" if hrs else "—"))
            parts_total += total
            labor_hours += hrs

        self._totals_label.setText(
            f"Parts: ${parts_total:,.2f}  |  Labor: {labor_hours:.1f} hrs  |  "
            f"Grand Total: ${parts_total:,.2f}"
        )

    def _new_wo(self):
        dlg = _NewWODialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cid = dlg.customer_combo.currentData()
            if not cid:
                QMessageBox.warning(self, "Missing", "Select a customer.")
                return
            try:
                result = create_work_order(
                    customer_id=cid,
                    vehicle_id=dlg.vehicle_combo.currentData(),
                    complaint=dlg.complaint_edit.toPlainText().strip() or None,
                    technician=dlg.technician_edit.text().strip() or None,
                    priority=dlg.priority_combo.currentText(),
                    labor_rate=dlg.labor_rate_spin.value(),
                    notes=dlg.notes_edit.toPlainText().strip() or None,
                )
                QMessageBox.information(
                    self, "Created",
                    f"Work Order {result.get('wo_number', '')} created."
                )
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _set_status(self, status: str):
        if not self._current_wo_id:
            return
        try:
            update_work_order(self._current_wo_id, status=status)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _add_line(self):
        if not self._current_wo_id:
            QMessageBox.information(self, "Info", "Select a work order first.")
            return
        line_type = self._line_type.currentText()
        product_id = None
        description = self._line_desc.text().strip()

        if line_type == "part":
            sku = self._line_sku.text().strip()
            if sku:
                try:
                    product = get_product_by_sku(sku)
                    if product:
                        product_id = product["id"]
                        if not description:
                            description = product.get("title", "")
                        if self._line_price.value() == 0:
                            self._line_price.setValue(float(product.get("selling_price") or 0))
                except Exception:
                    pass

        try:
            add_wo_line(
                wo_id=self._current_wo_id,
                line_type=line_type,
                product_id=product_id,
                description=description or None,
                quantity=self._line_qty.value(),
                unit_price=self._line_price.value(),
                hours=self._line_hours.value() if line_type == "labor" else 0,
            )
            self._line_sku.clear()
            self._line_desc.clear()
            self._line_price.setValue(0)
            self._line_hours.setValue(0)
            self._line_qty.setValue(1)
            self._refresh_lines()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _remove_line(self):
        row = self._line_table.currentRow()
        if row < 0 or row >= len(self._lines):
            return
        line_id = self._lines[row].get("id")
        if not line_id:
            return
        reply = QMessageBox.question(self, "Remove", "Remove this line?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_wo_line(line_id)
            self._refresh_lines()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
