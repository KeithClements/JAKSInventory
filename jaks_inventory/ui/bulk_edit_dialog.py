"""Bulk Edit dialog — apply field changes to multiple products at once."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QScrollArea,
    QWidget,
)

from db import (
    get_all_categories,
    get_all_manufacturers,
    get_all_vendors,
    get_subcategories_for_category,
    update_product,
)


class BulkEditDialog(QDialog):
    """Dialog for bulk editing non-quantity fields on multiple products."""

    def __init__(self, products: list[dict], parent=None):
        super().__init__(parent)
        self._products = products
        self.setWindowTitle(f"Bulk Edit ({len(products)} products)")
        self.setMinimumSize(520, 550)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout()

        # Summary
        summary = QGroupBox(f"Selected Products ({len(self._products)})")
        summary_layout = QVBoxLayout()
        for p in self._products[:6]:
            sku = p.get("sku", "?")
            title = (p.get("title") or "")[:45]
            summary_layout.addWidget(QLabel(f"• {sku}: {title}"))
        if len(self._products) > 6:
            summary_layout.addWidget(QLabel(f"… and {len(self._products) - 6} more"))
        summary.setLayout(summary_layout)
        outer.addWidget(summary)

        info = QLabel("Check a box to apply that field. Unchecked fields are left unchanged.")
        info.setStyleSheet("color: #555; font-size: 9pt; padding: 2px 0 4px 0;")
        outer.addWidget(info)

        # Scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        form_widget = QWidget()
        form = QVBoxLayout(form_widget)

        # ── Category ──
        self._cat_cb = QCheckBox("Change Category")
        self._cat_combo = QComboBox()
        self._cat_combo.setEditable(True)
        self._cat_combo.setEnabled(False)
        self._cat_combo.addItem("")
        try:
            for c in get_all_categories():
                self._cat_combo.addItem(c)
        except Exception:
            pass
        completer = QCompleter([self._cat_combo.itemText(i) for i in range(self._cat_combo.count())])
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._cat_combo.setCompleter(completer)
        self._cat_cb.toggled.connect(self._cat_combo.setEnabled)
        self._cat_combo.currentTextChanged.connect(self._on_cat_changed)
        row = QHBoxLayout()
        row.addWidget(self._cat_cb)
        row.addWidget(self._cat_combo, 1)
        form.addLayout(row)

        # ── Subcategory (cascades from Category) ──
        self._subcat_cb = QCheckBox("Change Subcategory")
        self._subcat_combo = QComboBox()
        self._subcat_combo.setEditable(True)
        self._subcat_combo.setEnabled(False)
        self._subcat_combo.addItem("")
        self._subcat_cb.toggled.connect(self._subcat_combo.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._subcat_cb)
        row.addWidget(self._subcat_combo, 1)
        form.addLayout(row)

        # ── Condition ──
        self._cond_cb = QCheckBox("Change Condition")
        self._cond_combo = QComboBox()
        self._cond_combo.setEnabled(False)
        self._cond_combo.addItems([
            "", "New Engine Parts", "Remanufactured", "Used", "Core", "New",
        ])
        self._cond_cb.toggled.connect(self._cond_combo.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._cond_cb)
        row.addWidget(self._cond_combo, 1)
        form.addLayout(row)

        # ── Supplier ──
        self._sup_cb = QCheckBox("Change Supplier")
        self._sup_combo = QComboBox()
        self._sup_combo.setEnabled(False)
        self._sup_combo.addItem("-- Select Vendor --", None)
        try:
            for v in get_all_vendors():
                self._sup_combo.addItem(v.get("name", ""), v.get("id"))
        except Exception:
            pass
        self._sup_cb.toggled.connect(self._sup_combo.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._sup_cb)
        row.addWidget(self._sup_combo, 1)
        form.addLayout(row)

        # ── Manufacturer ──
        self._mfr_cb = QCheckBox("Change Manufacturer")
        self._mfr_combo = QComboBox()
        self._mfr_combo.setEditable(True)
        self._mfr_combo.setEnabled(False)
        self._mfr_combo.addItem("")
        try:
            for m in get_all_manufacturers():
                self._mfr_combo.addItem(m)
        except Exception:
            pass
        self._mfr_cb.toggled.connect(self._mfr_combo.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._mfr_cb)
        row.addWidget(self._mfr_combo, 1)
        form.addLayout(row)

        # ── Engine Manufacturer ──
        self._eng_mfr_cb = QCheckBox("Change Engine Mfr")
        self._eng_mfr_edit = QLineEdit()
        self._eng_mfr_edit.setEnabled(False)
        self._eng_mfr_edit.setPlaceholderText("e.g. Cummins, Caterpillar")
        self._eng_mfr_cb.toggled.connect(self._eng_mfr_edit.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._eng_mfr_cb)
        row.addWidget(self._eng_mfr_edit, 1)
        form.addLayout(row)

        # ── Engine Model ──
        self._eng_model_cb = QCheckBox("Change Engine Model")
        self._eng_model_edit = QLineEdit()
        self._eng_model_edit.setEnabled(False)
        self._eng_model_edit.setPlaceholderText("e.g. ISX15, C15, DD15")
        self._eng_model_cb.toggled.connect(self._eng_model_edit.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._eng_model_cb)
        row.addWidget(self._eng_model_edit, 1)
        form.addLayout(row)

        # ── Truck Manufacturer ──
        self._truck_mfr_cb = QCheckBox("Change Truck Mfr")
        self._truck_mfr_edit = QLineEdit()
        self._truck_mfr_edit.setEnabled(False)
        self._truck_mfr_edit.setPlaceholderText("e.g. Kenworth, Peterbilt")
        self._truck_mfr_cb.toggled.connect(self._truck_mfr_edit.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._truck_mfr_cb)
        row.addWidget(self._truck_mfr_edit, 1)
        form.addLayout(row)

        # ── Truck Model ──
        self._truck_model_cb = QCheckBox("Change Truck Model")
        self._truck_model_edit = QLineEdit()
        self._truck_model_edit.setEnabled(False)
        self._truck_model_edit.setPlaceholderText("e.g. T680, 579, Cascadia")
        self._truck_model_cb.toggled.connect(self._truck_model_edit.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._truck_model_cb)
        row.addWidget(self._truck_model_edit, 1)
        form.addLayout(row)

        # ── Min Qty ──
        self._min_cb = QCheckBox("Change Min Qty")
        self._min_spin = QSpinBox()
        self._min_spin.setRange(0, 9999)
        self._min_spin.setEnabled(False)
        self._min_cb.toggled.connect(self._min_spin.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._min_cb)
        row.addWidget(self._min_spin)
        row.addStretch()
        form.addLayout(row)

        # ── Max Qty ──
        self._max_cb = QCheckBox("Change Max Qty")
        self._max_spin = QSpinBox()
        self._max_spin.setRange(0, 9999)
        self._max_spin.setEnabled(False)
        self._max_cb.toggled.connect(self._max_spin.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._max_cb)
        row.addWidget(self._max_spin)
        row.addStretch()
        form.addLayout(row)

        # ── Selling Price ──
        self._price_cb = QCheckBox("Change Sell Price")
        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0, 999999.99)
        self._price_spin.setDecimals(2)
        self._price_spin.setPrefix("$")
        self._price_spin.setEnabled(False)
        self._price_cb.toggled.connect(self._price_spin.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._price_cb)
        row.addWidget(self._price_spin)
        row.addStretch()
        form.addLayout(row)

        # ── SKU Prefix ──
        self._sku_cb = QCheckBox("Change SKU Prefix")
        self._sku_cb.setStyleSheet("color: #c62828;")  # red = dangerous
        sku_inner = QHBoxLayout()
        self._sku_old = QLineEdit()
        self._sku_old.setPlaceholderText("Old prefix")
        self._sku_old.setEnabled(False)
        self._sku_old.setMaximumWidth(100)
        self._sku_new = QLineEdit()
        self._sku_new.setPlaceholderText("New prefix")
        self._sku_new.setEnabled(False)
        self._sku_new.setMaximumWidth(100)
        self._sku_cb.toggled.connect(self._sku_old.setEnabled)
        self._sku_cb.toggled.connect(self._sku_new.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self._sku_cb)
        row.addWidget(QLabel("Replace"))
        row.addWidget(self._sku_old)
        row.addWidget(QLabel("→"))
        row.addWidget(self._sku_new)
        row.addStretch()
        form.addLayout(row)

        form.addStretch()
        scroll.setWidget(form_widget)
        outer.addWidget(scroll, 1)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.setLayout(outer)

    def _on_cat_changed(self, text: str) -> None:
        """Refresh subcategory options when category changes."""
        cat = text.strip()
        self._subcat_combo.clear()
        self._subcat_combo.addItem("")
        if cat:
            try:
                subs = get_subcategories_for_category(cat)
                for s in subs:
                    self._subcat_combo.addItem(s)
            except Exception:
                pass

    def _on_accept(self) -> None:
        """Apply checked field changes to all selected products."""
        changes: dict = {}

        if self._cat_cb.isChecked():
            changes["category"] = self._cat_combo.currentText().strip()
        if self._subcat_cb.isChecked():
            changes["subcategory"] = self._subcat_combo.currentText().strip()
        if self._cond_cb.isChecked():
            changes["condition"] = self._cond_combo.currentText()
        if self._sup_cb.isChecked():
            sup = self._sup_combo.currentText()
            if sup == "-- Select Vendor --":
                sup = ""
            changes["supplier"] = sup
            changes["preferred_vendor_id"] = self._sup_combo.currentData()
        if self._mfr_cb.isChecked():
            changes["manufacturer"] = self._mfr_combo.currentText().strip()
        if self._eng_mfr_cb.isChecked():
            changes["oem_manufacturer"] = self._eng_mfr_edit.text().strip()
        if self._eng_model_cb.isChecked():
            changes["engine"] = self._eng_model_edit.text().strip()
        if self._truck_mfr_cb.isChecked():
            changes["truck_manufacturer"] = self._truck_mfr_edit.text().strip()
        if self._truck_model_cb.isChecked():
            changes["truck_model"] = self._truck_model_edit.text().strip()
        if self._min_cb.isChecked():
            changes["min_qty"] = self._min_spin.value()
        if self._max_cb.isChecked():
            changes["max_qty"] = self._max_spin.value()
        if self._price_cb.isChecked():
            changes["selling_price"] = self._price_spin.value()

        sku_rename = self._sku_cb.isChecked()

        if not changes and not sku_rename:
            QMessageBox.information(self, "Nothing Selected", "Check at least one field to change.")
            return

        # SKU prefix — extra confirmation
        if sku_rename:
            old_pfx = self._sku_old.text().strip()
            new_pfx = self._sku_new.text().strip()
            if not old_pfx or not new_pfx:
                QMessageBox.warning(self, "SKU Prefix", "Enter both old and new prefix.")
                return
            reply = QMessageBox.warning(
                self, "⚠️ SKU Prefix Change",
                f"This will modify SKU structure on {len(self._products)} products.\n\n"
                f"Replace '{old_pfx}' → '{new_pfx}'\n\n"
                "This affects part numbers, Shopify links, and search.\n\n"
                "Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Confirm
        field_names = list(changes.keys())
        if sku_rename:
            field_names.append("sku (prefix)")
        msg = (
            f"Apply the following changes to {len(self._products)} products?\n\n"
            f"Fields: {', '.join(field_names)}"
        )
        reply = QMessageBox.question(
            self, "Confirm Bulk Edit", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Execute
        success = 0
        failed = 0
        for p in self._products:
            pid = p.get("id")
            if not pid:
                failed += 1
                continue
            try:
                product_changes = dict(changes)
                # SKU prefix rename
                if sku_rename:
                    old_pfx = self._sku_old.text().strip()
                    new_pfx = self._sku_new.text().strip()
                    old_sku = p.get("sku", "")
                    if old_sku.startswith(old_pfx):
                        product_changes["sku"] = new_pfx + old_sku[len(old_pfx):]

                if product_changes:
                    update_product(pid, **product_changes)
                success += 1
            except Exception:
                failed += 1

        if failed == 0:
            QMessageBox.information(
                self, "Bulk Edit Complete",
                f"Successfully updated {success} products."
            )
        else:
            QMessageBox.warning(
                self, "Partial Success",
                f"Updated: {success}, Failed: {failed}"
            )
        self.accept()
