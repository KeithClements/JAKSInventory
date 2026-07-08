"""Price Lists Screen - Manage pricing tiers and customer-specific pricing."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from db import (
    list_price_tiers, create_price_tier, update_price_tier, delete_price_tier,
    get_all_customers, get_customer_pricing_rules, set_customer_pricing,
    delete_customer_pricing,
)


class _TierDialog(QDialog):
    def __init__(self, tier=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Price Tier" if tier else "New Price Tier")
        self.setMinimumWidth(350)
        form = QFormLayout()
        self.name_edit = QLineEdit(tier.get("name", "") if tier else "")
        form.addRow("Tier Name:", self.name_edit)
        self.desc_edit = QLineEdit(tier.get("description", "") if tier else "")
        form.addRow("Description:", self.desc_edit)
        self.markup_spin = QDoubleSpinBox()
        self.markup_spin.setRange(-100, 500)
        self.markup_spin.setSuffix(" %")
        self.markup_spin.setValue(float(tier.get("markup_pct", 0) or 0) if tier else 0)
        form.addRow("Markup %:", self.markup_spin)
        self.discount_spin = QDoubleSpinBox()
        self.discount_spin.setRange(0, 100)
        self.discount_spin.setSuffix(" %")
        self.discount_spin.setValue(float(tier.get("discount_pct", 0) or 0) if tier else 0)
        form.addRow("Discount %:", self.discount_spin)
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100)
        self.priority_spin.setValue(int(tier.get("priority", 0) or 0) if tier else 0)
        form.addRow("Priority:", self.priority_spin)
        btns = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "description": self.desc_edit.text().strip(),
            "markup_pct": self.markup_spin.value(),
            "discount_pct": self.discount_spin.value(),
            "priority": self.priority_spin.value(),
        }


class PriceListsScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()

        header = QLabel("💰 Price Lists & Customer Pricing")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        # ── Tiers section ──
        tier_header = QHBoxLayout()
        tier_header.addWidget(QLabel("Price Tiers"))
        add_tier_btn = QPushButton("+ New Tier")
        add_tier_btn.clicked.connect(self._add_tier)
        edit_tier_btn = QPushButton("Edit")
        edit_tier_btn.clicked.connect(self._edit_tier)
        del_tier_btn = QPushButton("Delete")
        del_tier_btn.clicked.connect(self._delete_tier)
        tier_header.addStretch()
        tier_header.addWidget(add_tier_btn)
        tier_header.addWidget(edit_tier_btn)
        tier_header.addWidget(del_tier_btn)
        layout.addLayout(tier_header)

        self._tier_table = QTableWidget()
        self._tier_table.setColumnCount(5)
        self._tier_table.setHorizontalHeaderLabels(["Name", "Description", "Markup %", "Discount %", "Priority"])
        self._tier_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tier_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tier_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tier_table.setMaximumHeight(200)
        layout.addWidget(self._tier_table)

        # ── Customer pricing section ──
        cust_header = QHBoxLayout()
        cust_header.addWidget(QLabel("Customer Pricing Rules"))
        self._customer_combo = QComboBox()
        self._customer_combo.setMinimumWidth(250)
        self._customer_combo.currentIndexChanged.connect(self._load_customer_pricing)
        cust_header.addWidget(self._customer_combo)
        assign_btn = QPushButton("+ Assign Tier")
        assign_btn.clicked.connect(self._assign_tier)
        del_rule_btn = QPushButton("Remove Rule")
        del_rule_btn.clicked.connect(self._delete_rule)
        cust_header.addStretch()
        cust_header.addWidget(assign_btn)
        cust_header.addWidget(del_rule_btn)
        layout.addLayout(cust_header)

        self._pricing_table = QTableWidget()
        self._pricing_table.setColumnCount(5)
        self._pricing_table.setHorizontalHeaderLabels(["Tier/Type", "Product", "Custom Price", "Effective", "Expires"])
        self._pricing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._pricing_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pricing_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._pricing_table)

        self.setLayout(layout)
        self._tiers = []
        self._pricing_rules = []
        self._refresh()

    def _refresh(self):
        self._load_tiers()
        self._load_customers()

    def _load_tiers(self):
        try:
            self._tiers = list_price_tiers()
        except Exception:
            self._tiers = []
        self._tier_table.setRowCount(len(self._tiers))
        for i, t in enumerate(self._tiers):
            self._tier_table.setItem(i, 0, QTableWidgetItem(str(t.get("name", ""))))
            self._tier_table.setItem(i, 1, QTableWidgetItem(str(t.get("description", "") or "")))
            self._tier_table.setItem(i, 2, QTableWidgetItem(f"{float(t.get('markup_pct', 0) or 0):.1f}"))
            self._tier_table.setItem(i, 3, QTableWidgetItem(f"{float(t.get('discount_pct', 0) or 0):.1f}"))
            self._tier_table.setItem(i, 4, QTableWidgetItem(str(t.get("priority", 0))))

    def _load_customers(self):
        self._customer_combo.blockSignals(True)
        self._customer_combo.clear()
        self._customer_combo.addItem("-- Select Customer --", None)
        try:
            customers = get_all_customers()
            for c in customers:
                label = c.get("company") or c.get("name", f"ID {c.get('id')}")
                self._customer_combo.addItem(label, c.get("id"))
        except Exception:
            pass
        self._customer_combo.blockSignals(False)

    def _load_customer_pricing(self):
        cid = self._customer_combo.currentData()
        if not cid:
            self._pricing_table.setRowCount(0)
            return
        try:
            self._pricing_rules = get_customer_pricing_rules(cid)
        except Exception:
            self._pricing_rules = []
        self._pricing_table.setRowCount(len(self._pricing_rules))
        for i, r in enumerate(self._pricing_rules):
            tier_name = r.get("tier_name", "")
            if r.get("custom_price") is not None:
                ttype = "Custom Price"
            elif tier_name:
                ttype = f"Tier: {tier_name}"
            else:
                ttype = "—"
            self._pricing_table.setItem(i, 0, QTableWidgetItem(ttype))
            self._pricing_table.setItem(i, 1, QTableWidgetItem(
                r.get("sku", r.get("product_title", "All Products")) or "All Products"))
            cp = r.get("custom_price")
            self._pricing_table.setItem(i, 2, QTableWidgetItem(f"${cp:.2f}" if cp else "—"))
            self._pricing_table.setItem(i, 3, QTableWidgetItem(str(r.get("effective_date", "") or "")))
            self._pricing_table.setItem(i, 4, QTableWidgetItem(str(r.get("expiry_date", "") or "No expiry")))

    def _add_tier(self):
        dlg = _TierDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["name"]:
                QMessageBox.warning(self, "Error", "Name is required.")
                return
            try:
                create_price_tier(**data)
                self._load_tiers()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _edit_tier(self):
        row = self._tier_table.currentRow()
        if row < 0 or row >= len(self._tiers):
            return
        tier = self._tiers[row]
        dlg = _TierDialog(tier=tier, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                update_price_tier(tier["id"], **data)
                self._load_tiers()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_tier(self):
        row = self._tier_table.currentRow()
        if row < 0 or row >= len(self._tiers):
            return
        tier = self._tiers[row]
        if QMessageBox.question(self, "Confirm", f"Delete tier '{tier.get('name')}'?") == QMessageBox.StandardButton.Yes:
            delete_price_tier(tier["id"])
            self._load_tiers()

    def _assign_tier(self):
        cid = self._customer_combo.currentData()
        if not cid:
            QMessageBox.warning(self, "Error", "Select a customer first.")
            return
        if not self._tiers:
            QMessageBox.warning(self, "Error", "Create a price tier first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign Price Tier")
        form = QFormLayout()
        tier_combo = QComboBox()
        for t in self._tiers:
            tier_combo.addItem(t.get("name", ""), t.get("id"))
        form.addRow("Tier:", tier_combo)
        btns = QHBoxLayout()
        save_btn = QPushButton("Assign")
        save_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        lay = QVBoxLayout()
        lay.addLayout(form)
        lay.addLayout(btns)
        dlg.setLayout(lay)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                set_customer_pricing(cid, tier_id=tier_combo.currentData())
                self._load_customer_pricing()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_rule(self):
        row = self._pricing_table.currentRow()
        if row < 0 or row >= len(self._pricing_rules):
            return
        rule = self._pricing_rules[row]
        delete_customer_pricing(rule["id"])
        self._load_customer_pricing()
