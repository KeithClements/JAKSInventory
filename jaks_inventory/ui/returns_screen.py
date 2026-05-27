"""Returns & Warranty Screen - Manage RMAs and warranty returns."""
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
    create_return,
    list_returns,
    update_return,
    get_return,
    refund_return_as_credit,
    get_all_customers,
    get_product_by_sku,
)


class _NewReturnDialog(QDialog):
    """Dialog to create a new return/RMA."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Return / RMA")
        self.setMinimumWidth(450)

        form = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.addItem("(select customer)", None)
        try:
            for c in get_all_customers():
                name = c.get("name", "")
                company = c.get("company", "")
                display = f"{name} — {company}" if company else name
                self.customer_combo.addItem(display, c.get("id"))
        except Exception:
            pass
        form.addRow("Customer:", self.customer_combo)

        self.sku_edit = QLineEdit()
        self.sku_edit.setPlaceholderText("Product SKU")
        form.addRow("Product SKU:", self.sku_edit)

        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 9999)
        self.qty_spin.setValue(1)
        form.addRow("Quantity:", self.qty_spin)

        self.reason_combo = QComboBox()
        self.reason_combo.addItems([
            "Defective", "Wrong Part", "Warranty Claim", "Customer Return",
            "Damaged in Shipping", "Core Return", "Other"
        ])
        form.addRow("Reason:", self.reason_combo)

        self.warranty_edit = QLineEdit()
        self.warranty_edit.setPlaceholderText("YYYY-MM-DD (optional)")
        form.addRow("Warranty Expiry:", self.warranty_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(60)
        form.addRow("Notes:", self.notes_edit)

        btns = QHBoxLayout()
        ok = QPushButton("Create RMA")
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


class ReturnsScreen(QWidget):
    """Returns & warranty management screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._returns: list[dict] = []

        # Controls
        top = QHBoxLayout()
        self._new_btn = QPushButton("➕ New Return")
        self._new_btn.clicked.connect(self._new_return)
        self._status_filter = QComboBox()
        self._status_filter.addItems([
            "All", "pending", "received", "inspecting", "approved",
            "refunded", "replaced", "rejected", "vendor_claim"
        ])
        self._status_filter.currentIndexChanged.connect(self._refresh)
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh)
        top.addWidget(self._new_btn)
        top.addWidget(QLabel("Status:"))
        top.addWidget(self._status_filter)
        top.addWidget(self._refresh_btn)
        top.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 11pt; padding: 4px;")

        # Table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "RMA #", "Customer", "Product", "Qty", "Reason",
            "Status", "Refund $", "Created"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        # Action panel
        action_bar = QHBoxLayout()
        self._action_combo = QComboBox()
        self._action_combo.addItems([
            "received", "inspecting", "approved", "refunded",
            "replaced", "rejected", "vendor_claim"
        ])
        self._set_status_btn = QPushButton("Set Status")
        self._set_status_btn.clicked.connect(self._set_status)
        self._refund_spin = QDoubleSpinBox()
        self._refund_spin.setRange(0, 999999)
        self._refund_spin.setPrefix("$")
        self._set_refund_btn = QPushButton("Set Refund")
        self._set_refund_btn.clicked.connect(self._set_refund)
        action_bar.addWidget(QLabel("Action:"))
        action_bar.addWidget(self._action_combo)
        action_bar.addWidget(self._set_status_btn)
        action_bar.addStretch()
        action_bar.addWidget(QLabel("Refund:"))
        action_bar.addWidget(self._refund_spin)
        action_bar.addWidget(self._set_refund_btn)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self._count_label)
        layout.addWidget(self._table, 1)
        layout.addLayout(action_bar)
        self.setLayout(layout)

        self._refresh()

    def _refresh(self):
        idx = self._status_filter.currentIndex()
        status_map = {0: None, 1: "pending", 2: "received", 3: "inspecting",
                      4: "approved", 5: "refunded", 6: "replaced",
                      7: "rejected", 8: "vendor_claim"}
        status = status_map.get(idx)
        try:
            self._returns = list_returns(status=status)
        except Exception:
            self._returns = []
        self._count_label.setText(f"{len(self._returns)} return(s)")
        self._table.setRowCount(len(self._returns))
        for i, r in enumerate(self._returns):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._table.setItem(i, 0, item(r.get("rma_number", "")))
            cust = r.get("customer_name") or r.get("customer_company") or "—"
            self._table.setItem(i, 1, item(cust))
            prod = r.get("sku") or r.get("product_title") or "—"
            self._table.setItem(i, 2, item(prod))
            self._table.setItem(i, 3, item(r.get("quantity", 0)))
            self._table.setItem(i, 4, item(r.get("reason") or "—"))

            st = str(r.get("status", ""))
            st_item = item(st)
            color_map = {"pending": "blue", "received": "darkcyan",
                         "approved": "green", "refunded": "purple",
                         "rejected": "red", "vendor_claim": "darkorange"}
            if st in color_map:
                st_item.setForeground(QColor(color_map[st]))
            self._table.setItem(i, 5, st_item)

            refund = float(r.get("refund_amount") or 0)
            self._table.setItem(i, 6, item(f"${refund:,.2f}" if refund else "—"))
            self._table.setItem(i, 7, item(str(r.get("created_at", ""))[:16]))
        self._table.resizeColumnsToContents()

    def _on_double_click(self, row: int, _col: int):
        if row < 0 or row >= len(self._returns):
            return
        ret = self._returns[row]
        rid = ret.get("id")
        try:
            full = get_return(rid)
        except Exception:
            full = ret
        if full:
            info = (
                f"RMA: {full.get('rma_number', '')}\n"
                f"Customer: {full.get('customer_name', '')}\n"
                f"Product: {full.get('sku', '')} — {full.get('product_title', '')}\n"
                f"Qty: {full.get('quantity', 0)}\n"
                f"Reason: {full.get('reason', '')}\n"
                f"Status: {full.get('status', '')}\n"
                f"Warranty Expiry: {full.get('warranty_expiry') or 'N/A'}\n"
                f"Refund: ${float(full.get('refund_amount') or 0):,.2f}\n"
                f"Notes: {full.get('notes') or '—'}"
            )
            QMessageBox.information(self, "Return Details", info)

    def _new_return(self):
        dlg = _NewReturnDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cid = dlg.customer_combo.currentData()
            sku = dlg.sku_edit.text().strip()
            product_id = None
            if sku:
                try:
                    p = get_product_by_sku(sku)
                    if p:
                        product_id = p["id"]
                except Exception:
                    pass
            try:
                result = create_return(
                    customer_id=cid,
                    product_id=product_id,
                    quantity=dlg.qty_spin.value(),
                    reason=dlg.reason_combo.currentText(),
                    warranty_expiry=dlg.warranty_edit.text().strip() or None,
                    notes=dlg.notes_edit.toPlainText().strip() or None,
                )
                QMessageBox.information(
                    self, "Created",
                    f"Return {result.get('rma_number', '')} created."
                )
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _set_status(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._returns):
            QMessageBox.information(self, "Info", "Select a return first.")
            return
        rid = self._returns[row].get("id")
        status = self._action_combo.currentText()
        try:
            if status == "refunded":
                # G3: Posting a refund must create a customer_credits row
                # so the credit appears on the customer's ledger and can
                # be applied from the payment dialog.
                existing_amt = float(
                    self._returns[row].get("refund_amount") or 0
                )
                amt = existing_amt if existing_amt > 0 else self._refund_spin.value()
                if amt <= 0:
                    QMessageBox.warning(
                        self, "Refund amount required",
                        "Set a refund amount before marking the RMA refunded.",
                    )
                    return
                refund_return_as_credit(rid, amount=amt)
            else:
                update_return(rid, status=status)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _set_refund(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._returns):
            QMessageBox.information(self, "Info", "Select a return first.")
            return
        rid = self._returns[row].get("id")
        amount = self._refund_spin.value()
        try:
            # Route through refund_return_as_credit so a customer_credits
            # ledger row is created/updated alongside the returns row.
            if amount > 0:
                refund_return_as_credit(rid, amount=amount)
            else:
                update_return(rid, refund_amount=amount, status="refunded")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
