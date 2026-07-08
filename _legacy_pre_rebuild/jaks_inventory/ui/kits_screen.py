"""Kits / Assemblies Screen - Build and manage kit definitions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from db import (
    list_kits, get_kit, create_kit, update_kit, delete_kit,
    add_kit_component, remove_kit_component, get_kit_components,
    get_kit_availability, get_product_by_sku,
)


class _KitDialog(QDialog):
    def __init__(self, kit=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Kit" if kit else "New Kit")
        self.setMinimumWidth(400)
        form = QFormLayout()
        self.sku_edit = QLineEdit(kit.get("sku", "") if kit else "")
        form.addRow("Kit SKU:", self.sku_edit)
        self.name_edit = QLineEdit(kit.get("name", "") if kit else "")
        form.addRow("Name:", self.name_edit)
        self.desc_edit = QTextEdit()
        self.desc_edit.setMaximumHeight(60)
        self.desc_edit.setPlainText(kit.get("description", "") if kit else "")
        form.addRow("Description:", self.desc_edit)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 999999)
        self.price_spin.setPrefix("$")
        self.price_spin.setDecimals(2)
        self.price_spin.setValue(float(kit.get("kit_price", 0) or 0) if kit else 0)
        form.addRow("Kit Price:", self.price_spin)
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
            "sku": self.sku_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "description": self.desc_edit.toPlainText().strip(),
            "kit_price": self.price_spin.value(),
        }


class KitsScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()

        header = QLabel("🔧 Kits / Assemblies")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        # Kits toolbar
        toolbar = QHBoxLayout()
        new_btn = QPushButton("+ New Kit")
        new_btn.clicked.connect(self._new_kit)
        edit_btn = QPushButton("Edit Kit")
        edit_btn.clicked.connect(self._edit_kit)
        del_btn = QPushButton("Delete Kit")
        del_btn.clicked.connect(self._delete_kit)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(del_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Kits table
        self._kits_table = QTableWidget()
        self._kits_table.setColumnCount(6)
        self._kits_table.setHorizontalHeaderLabels(
            ["SKU", "Name", "Kit Price", "Components", "Total Parts", "Available Kits"])
        self._kits_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._kits_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._kits_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._kits_table.currentCellChanged.connect(
            lambda row, _c, _pr, _pc: self._on_kit_selected(row))
        layout.addWidget(self._kits_table)

        # Components section
        comp_header = QHBoxLayout()
        comp_header.addWidget(QLabel("Kit Components"))
        self._comp_sku = QLineEdit()
        self._comp_sku.setPlaceholderText("Component SKU")
        self._comp_sku.setMaximumWidth(150)
        self._comp_qty = QSpinBox()
        self._comp_qty.setRange(1, 9999)
        self._comp_qty.setMaximumWidth(80)
        add_comp_btn = QPushButton("+ Add")
        add_comp_btn.clicked.connect(self._add_component)
        rm_comp_btn = QPushButton("- Remove")
        rm_comp_btn.clicked.connect(self._remove_component)
        comp_header.addWidget(self._comp_sku)
        comp_header.addWidget(self._comp_qty)
        comp_header.addWidget(add_comp_btn)
        comp_header.addWidget(rm_comp_btn)
        comp_header.addStretch()
        layout.addLayout(comp_header)

        self._comp_table = QTableWidget()
        self._comp_table.setColumnCount(6)
        self._comp_table.setHorizontalHeaderLabels(
            ["SKU", "Product", "Qty Needed", "On Hand", "Unit Cost", "Can Build"])
        self._comp_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._comp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._comp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._comp_table.setMaximumHeight(200)
        layout.addWidget(self._comp_table)

        # Availability summary
        self._avail_label = QLabel("")
        self._avail_label.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 4px;")
        layout.addWidget(self._avail_label)

        self.setLayout(layout)
        self._kits = []
        self._current_kit_id = None
        self._components = []
        self._refresh()

    def _refresh(self):
        self._load_kits()

    def _load_kits(self):
        try:
            self._kits = list_kits(active_only=False)
        except Exception:
            self._kits = []
        self._kits_table.setRowCount(len(self._kits))
        for i, k in enumerate(self._kits):
            self._kits_table.setItem(i, 0, QTableWidgetItem(str(k.get("sku", ""))))
            self._kits_table.setItem(i, 1, QTableWidgetItem(str(k.get("name", ""))))
            price = float(k.get("kit_price", 0) or 0)
            self._kits_table.setItem(i, 2, QTableWidgetItem(f"${price:,.2f}"))
            self._kits_table.setItem(i, 3, QTableWidgetItem(str(k.get("component_count", 0))))
            self._kits_table.setItem(i, 4, QTableWidgetItem(str(k.get("total_parts", 0))))
            # Get availability
            try:
                avail = get_kit_availability(k["id"])
                self._kits_table.setItem(i, 5, QTableWidgetItem(str(avail.get("available", 0))))
            except Exception:
                self._kits_table.setItem(i, 5, QTableWidgetItem("?"))

    def _on_kit_selected(self, row):
        if row < 0 or row >= len(self._kits):
            self._comp_table.setRowCount(0)
            self._current_kit_id = None
            self._avail_label.setText("")
            return
        self._current_kit_id = self._kits[row].get("id")
        self._load_components()

    def _load_components(self):
        if not self._current_kit_id:
            return
        try:
            self._components = get_kit_components(self._current_kit_id)
            avail = get_kit_availability(self._current_kit_id)
        except Exception:
            self._components = []
            avail = {"available": 0, "components": []}
        self._comp_table.setRowCount(len(self._components))
        avail_map = {c.get("sku"): c for c in avail.get("components", [])}
        for i, c in enumerate(self._components):
            sku = str(c.get("sku", ""))
            self._comp_table.setItem(i, 0, QTableWidgetItem(sku))
            self._comp_table.setItem(i, 1, QTableWidgetItem(str(c.get("product_title", "") or "")))
            self._comp_table.setItem(i, 2, QTableWidgetItem(str(c.get("quantity", 1))))
            on_hand = int(c.get("qty_on_hand", 0) or 0)
            self._comp_table.setItem(i, 3, QTableWidgetItem(str(on_hand)))
            cost = float(c.get("cost", 0) or 0)
            self._comp_table.setItem(i, 4, QTableWidgetItem(f"${cost:,.2f}"))
            ac = avail_map.get(sku, {})
            self._comp_table.setItem(i, 5, QTableWidgetItem(str(ac.get("available_kits", 0))))
        total_avail = avail.get("available", 0)
        self._avail_label.setText(f"✅ Can build {total_avail} complete kit(s) from current inventory")

    def _new_kit(self):
        dlg = _KitDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["sku"] or not data["name"]:
                QMessageBox.warning(self, "Error", "SKU and Name are required.")
                return
            try:
                create_kit(**data)
                self._load_kits()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _edit_kit(self):
        row = self._kits_table.currentRow()
        if row < 0 or row >= len(self._kits):
            return
        kit = self._kits[row]
        dlg = _KitDialog(kit=kit, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                update_kit(kit["id"], **data)
                self._load_kits()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_kit(self):
        row = self._kits_table.currentRow()
        if row < 0 or row >= len(self._kits):
            return
        kit = self._kits[row]
        if QMessageBox.question(self, "Confirm", f"Delete kit '{kit.get('name')}'?") == QMessageBox.StandardButton.Yes:
            delete_kit(kit["id"])
            self._load_kits()

    def _add_component(self):
        if not self._current_kit_id:
            QMessageBox.warning(self, "Error", "Select a kit first.")
            return
        sku = self._comp_sku.text().strip()
        if not sku:
            return
        try:
            product = get_product_by_sku(sku)
            if not product:
                QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
                return
            add_kit_component(self._current_kit_id, product["id"],
                              quantity=self._comp_qty.value())
            self._comp_sku.clear()
            self._load_components()
            self._load_kits()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _remove_component(self):
        row = self._comp_table.currentRow()
        if row < 0 or row >= len(self._components):
            return
        try:
            remove_kit_component(self._components[row]["id"])
            self._load_components()
            self._load_kits()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
