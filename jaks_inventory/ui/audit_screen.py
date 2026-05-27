"""Inventory Audit Screen - Cycle counting and variance management."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
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
    create_audit,
    get_audit,
    list_audits,
    add_audit_line,
    update_audit_line,
    get_audit_lines,
    finalize_audit,
    cancel_audit,
    list_locations,
    get_all_products,
    get_product_by_sku,
)


class _NewAuditDialog(QDialog):
    """Dialog to create a new audit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Inventory Audit")
        self.setMinimumWidth(400)

        form = QFormLayout()
        self.location_combo = QComboBox()
        self.location_combo.addItem("All Locations", None)
        try:
            for loc in list_locations():
                self.location_combo.addItem(
                    str(loc.get("name", "")), loc.get("id")
                )
        except Exception:
            pass
        form.addRow("Location:", self.location_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["full", "cycle", "spot"])
        form.addRow("Count Type:", self.type_combo)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(80)
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


class AuditScreen(QWidget):
    """Inventory audit / cycle count screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._audits: list[dict] = []
        self._current_audit_id: int | None = None
        self._lines: list[dict] = []

        # --- Top: Audit list ---
        top_bar = QHBoxLayout()
        self._new_btn = QPushButton("➕ New Audit")
        self._new_btn.clicked.connect(self._new_audit)
        self._status_filter = QComboBox()
        self._status_filter.addItems(["All", "in_progress", "completed", "cancelled"])
        self._status_filter.currentIndexChanged.connect(self._refresh_audits)
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh_audits)
        top_bar.addWidget(self._new_btn)
        top_bar.addWidget(QLabel("Status:"))
        top_bar.addWidget(self._status_filter)
        top_bar.addWidget(self._refresh_btn)
        top_bar.addStretch()

        self._audit_table = QTableWidget(0, 5)
        self._audit_table.setHorizontalHeaderLabels([
            "ID", "Location", "Type", "Status", "Created"
        ])
        self._audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._audit_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._audit_table.setMaximumHeight(200)
        self._audit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._audit_table.cellDoubleClicked.connect(self._on_audit_selected)

        # --- Bottom: Audit lines ---
        self._audit_info = QLabel("Select an audit above to view/edit lines")
        self._audit_info.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 4px;")

        line_bar = QHBoxLayout()
        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Enter SKU to add...")
        self._sku_input.returnPressed.connect(self._add_line_by_sku)
        self._add_btn = QPushButton("Add SKU")
        self._add_btn.clicked.connect(self._add_line_by_sku)
        self._add_all_btn = QPushButton("Add All Products")
        self._add_all_btn.clicked.connect(self._add_all_products)
        self._finalize_btn = QPushButton("✅ Finalize Audit")
        self._finalize_btn.clicked.connect(self._finalize)
        self._cancel_btn = QPushButton("❌ Cancel Audit")
        self._cancel_btn.clicked.connect(self._cancel)
        line_bar.addWidget(self._sku_input)
        line_bar.addWidget(self._add_btn)
        line_bar.addWidget(self._add_all_btn)
        line_bar.addStretch()
        line_bar.addWidget(self._finalize_btn)
        line_bar.addWidget(self._cancel_btn)

        self._line_table = QTableWidget(0, 6)
        self._line_table.setHorizontalHeaderLabels([
            "SKU", "Title", "System Qty", "Counted", "Variance", "Variance $"
        ])
        self._line_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._line_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self._variance_label = QLabel("")
        self._variance_label.setStyleSheet("font-size: 11pt; padding: 4px;")

        # Layout
        layout = QVBoxLayout()
        layout.addLayout(top_bar)
        layout.addWidget(self._audit_table)
        layout.addWidget(self._audit_info)
        layout.addLayout(line_bar)
        layout.addWidget(self._line_table, 1)
        layout.addWidget(self._variance_label)
        self.setLayout(layout)

        self._refresh_audits()

    def _refresh_audits(self):
        idx = self._status_filter.currentIndex()
        status_map = {0: None, 1: "in_progress", 2: "completed", 3: "cancelled"}
        status = status_map.get(idx)
        try:
            self._audits = list_audits(status=status)
        except Exception:
            self._audits = []
        self._audit_table.setRowCount(len(self._audits))
        for i, a in enumerate(self._audits):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._audit_table.setItem(i, 0, item(a.get("id", "")))
            self._audit_table.setItem(i, 1, item(a.get("location_name") or "All"))
            self._audit_table.setItem(i, 2, item(a.get("count_type", "")))
            st = str(a.get("status", ""))
            st_item = item(st)
            if st == "completed":
                st_item.setForeground(QColor("green"))
            elif st == "cancelled":
                st_item.setForeground(QColor("red"))
            self._audit_table.setItem(i, 3, st_item)
            self._audit_table.setItem(i, 4, item(str(a.get("created_at", ""))[:16]))

    def _on_audit_selected(self, row: int, _col: int):
        if row < 0 or row >= len(self._audits):
            return
        audit = self._audits[row]
        self._current_audit_id = audit.get("id")
        status = audit.get("status", "")
        self._audit_info.setText(
            f"Audit #{self._current_audit_id} — {audit.get('count_type', '')} "
            f"— {audit.get('location_name') or 'All Locations'} — {status}"
        )
        editable = status == "in_progress"
        self._sku_input.setEnabled(editable)
        self._add_btn.setEnabled(editable)
        self._add_all_btn.setEnabled(editable)
        self._finalize_btn.setEnabled(editable)
        self._cancel_btn.setEnabled(editable)
        self._refresh_lines()

    def _refresh_lines(self):
        if not self._current_audit_id:
            return
        try:
            self._lines = get_audit_lines(self._current_audit_id)
        except Exception:
            self._lines = []
        self._line_table.setRowCount(len(self._lines))
        total_var = 0
        total_var_val = 0.0
        for i, ln in enumerate(self._lines):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._line_table.setItem(i, 0, item(ln.get("sku", "")))
            self._line_table.setItem(i, 1, item(ln.get("title", "")))
            self._line_table.setItem(i, 2, item(ln.get("system_qty", 0)))

            # Counted qty — editable spin
            counted = ln.get("counted_qty")
            counted_spin = QSpinBox()
            counted_spin.setRange(-9999, 99999)
            counted_spin.setValue(int(counted) if counted is not None else 0)
            line_id = ln.get("id")
            counted_spin.valueChanged.connect(
                lambda val, lid=line_id: self._on_counted_changed(lid, val)
            )
            self._line_table.setCellWidget(i, 3, counted_spin)

            variance = int(ln.get("variance") or 0)
            var_item = item(variance)
            if variance < 0:
                var_item.setForeground(QColor("red"))
            elif variance > 0:
                var_item.setForeground(QColor("green"))
            self._line_table.setItem(i, 4, var_item)

            var_val = float(ln.get("variance_value") or 0)
            val_item = item(f"${var_val:,.2f}")
            if var_val < 0:
                val_item.setForeground(QColor("red"))
            self._line_table.setItem(i, 5, val_item)

            if variance != 0:
                total_var += 1
                total_var_val += var_val

        self._variance_label.setText(
            f"Total variances: {total_var} | Value: ${total_var_val:,.2f}"
        )

    def _on_counted_changed(self, line_id: int, value: int):
        try:
            update_audit_line(line_id, value)
            self._refresh_lines()
        except Exception:
            pass

    def _new_audit(self):
        dlg = _NewAuditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            loc_id = dlg.location_combo.currentData()
            try:
                aid = create_audit(
                    location_id=loc_id,
                    count_type=dlg.type_combo.currentText(),
                    notes=dlg.notes_edit.toPlainText().strip() or None,
                )
                QMessageBox.information(self, "Created", f"Audit #{aid} created.")
                self._refresh_audits()
                self._current_audit_id = aid
                self._audit_info.setText(f"Audit #{aid} — in_progress")
                self._lines = []
                self._line_table.setRowCount(0)
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _add_line_by_sku(self):
        if not self._current_audit_id:
            return
        sku = self._sku_input.text().strip()
        if not sku:
            return
        try:
            product = get_product_by_sku(sku)
            if not product:
                QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
                return
            add_audit_line(
                self._current_audit_id,
                product_id=product["id"],
                system_qty=int(product.get("qty_on_hand") or 0),
            )
            self._sku_input.clear()
            self._refresh_lines()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _add_all_products(self):
        if not self._current_audit_id:
            return
        reply = QMessageBox.question(
            self, "Add All",
            "Add ALL products to this audit? This may take a moment.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            products = get_all_products()
            for p in products:
                try:
                    add_audit_line(
                        self._current_audit_id,
                        product_id=p["id"],
                        system_qty=int(p.get("qty_on_hand") or 0),
                    )
                except Exception:
                    pass
            self._refresh_lines()
            QMessageBox.information(self, "Done", f"Added {len(products)} products.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _finalize(self):
        if not self._current_audit_id:
            return
        reply = QMessageBox.question(
            self, "Finalize",
            "Finalize this audit and apply all variances to inventory?\n"
            "This cannot be undone.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = finalize_audit(self._current_audit_id)
            QMessageBox.information(
                self, "Finalized",
                f"Adjusted: {result.get('adjusted', 0)}\n"
                f"Variances: {result.get('total_variances', 0)}\n"
                f"Value: ${result.get('variance_value', 0):,.2f}\n"
                f"Errors: {len(result.get('errors', []))}"
            )
            self._refresh_audits()
            self._refresh_lines()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _cancel(self):
        if not self._current_audit_id:
            return
        reply = QMessageBox.question(self, "Cancel", "Cancel this audit?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            cancel_audit(self._current_audit_id)
            self._refresh_audits()
            self._current_audit_id = None
            self._line_table.setRowCount(0)
            self._audit_info.setText("Audit cancelled.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
