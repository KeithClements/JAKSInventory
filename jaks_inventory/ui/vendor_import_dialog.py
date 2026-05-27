"""Vendor Import Dialog — create inventory from a vendor search result.

Shows editable fields pre-filled from vendor data with duplicate warnings
and save options (Save, Save & Open, Save & Add to Quote).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QGroupBox,
)


class VendorImportDialog(QDialog):
    """Import preview/edit screen for vendor search results.

    Signals:
      product_created(dict) — {'id': int, 'sku': str, 'action': str}
        action is 'save', 'save_open', or 'save_quote'
    """

    product_created = Signal(dict)

    def __init__(self, vendor_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📥 Import to Inventory")
        self.setMinimumSize(600, 680)
        self.resize(650, 720)

        self._vendor_data = vendor_data
        self._build_ui()
        self._populate(vendor_data)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        title = QLabel("📥 Import to Inventory")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1F2937;")
        root.addWidget(title)

        # ── Duplicate warning banner (hidden by default) ──
        self._dup_banner = QFrame()
        self._dup_banner.setStyleSheet(
            "QFrame { background: #FEF3C7; border: 1px solid #F59E0B; "
            "border-radius: 6px; padding: 8px 12px; }"
        )
        dup_lay = QHBoxLayout(self._dup_banner)
        self._dup_label = QLabel("")
        self._dup_label.setStyleSheet("color: #92400E; font-size: 12px;")
        self._dup_label.setWordWrap(True)
        dup_lay.addWidget(self._dup_label)
        self._dup_banner.hide()
        root.addWidget(self._dup_banner)

        # ── Form ──
        form_group = QGroupBox("Product Details")
        form_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding-top: 18px; margin-top: 6px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        form = QFormLayout(form_group)
        form.setSpacing(8)
        form.setContentsMargins(12, 12, 12, 12)

        self._vendor_edit = QLineEdit()
        self._vendor_edit.setReadOnly(True)
        self._vendor_edit.setStyleSheet("background: #F3F4F6;")
        form.addRow("Vendor:", self._vendor_edit)

        self._vpn_edit = QLineEdit()
        self._vpn_edit.setPlaceholderText("Vendor Part Number")
        form.addRow("Vendor PN:", self._vpn_edit)

        self._oem_edit = QLineEdit()
        self._oem_edit.setPlaceholderText("OEM Part Number")
        form.addRow("OEM PN:", self._oem_edit)

        self._sku_edit = QLineEdit()
        self._sku_edit.setPlaceholderText("Auto-generated")
        self._sku_edit.setStyleSheet(
            "QLineEdit { background: #F0FDF4; border: 1px solid #6B8E23; padding: 4px; }"
        )
        form.addRow("SKU:", self._sku_edit)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Product Title")
        form.addRow("Title:", self._title_edit)

        self._desc_edit = QTextEdit()
        self._desc_edit.setMaximumHeight(60)
        self._desc_edit.setPlaceholderText("Description")
        form.addRow("Description:", self._desc_edit)

        self._category_edit = QLineEdit()
        form.addRow("Category:", self._category_edit)

        self._mfr_edit = QLineEdit()
        form.addRow("Manufacturer:", self._mfr_edit)

        self._condition_combo = QComboBox()
        self._condition_combo.addItems(["New", "Rebuilt", "Used", "Remanufactured", "Core"])
        form.addRow("Condition:", self._condition_combo)

        # Pricing row
        price_row = QHBoxLayout()
        self._cost_spin = QDoubleSpinBox()
        self._cost_spin.setPrefix("$")
        self._cost_spin.setMaximum(999999)
        self._cost_spin.setDecimals(2)
        price_row.addWidget(QLabel("Cost:"))
        price_row.addWidget(self._cost_spin)

        self._price_spin = QDoubleSpinBox()
        self._price_spin.setPrefix("$")
        self._price_spin.setMaximum(999999)
        self._price_spin.setDecimals(2)
        price_row.addWidget(QLabel("Price:"))
        price_row.addWidget(self._price_spin)

        self._core_spin = QDoubleSpinBox()
        self._core_spin.setPrefix("$")
        self._core_spin.setMaximum(99999)
        self._core_spin.setDecimals(2)
        price_row.addWidget(QLabel("Core:"))
        price_row.addWidget(self._core_spin)

        form.addRow("Pricing:", price_row)

        self._xref_edit = QTextEdit()
        self._xref_edit.setMaximumHeight(50)
        self._xref_edit.setPlaceholderText("One per line")
        form.addRow("Cross Refs:", self._xref_edit)

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("Notes")
        form.addRow("Notes:", self._notes_edit)

        root.addWidget(form_group)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton { padding: 8px 16px; border: 1px solid #D1D5DB; "
            "border-radius: 6px; } QPushButton:hover { background: #F3F4F6; }"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        btn_row.addStretch()

        self._save_btn = QPushButton("💾 Save Product")
        self._save_btn.setStyleSheet(self._btn_style("#374151"))
        self._save_btn.clicked.connect(lambda: self._save("save"))
        btn_row.addWidget(self._save_btn)

        self._save_open_btn = QPushButton("💾 Save && Open")
        self._save_open_btn.setStyleSheet(self._btn_style("#1E40AF"))
        self._save_open_btn.clicked.connect(lambda: self._save("save_open"))
        btn_row.addWidget(self._save_open_btn)

        self._save_quote_btn = QPushButton("💾 Save && Add to Quote")
        self._save_quote_btn.setStyleSheet(self._btn_style("#6B8E23"))
        self._save_quote_btn.clicked.connect(lambda: self._save("save_quote"))
        btn_row.addWidget(self._save_quote_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Populate from vendor data
    # ------------------------------------------------------------------
    def _populate(self, data: dict):
        self._vendor_edit.setText(data.get("vendor", ""))
        self._vpn_edit.setText(data.get("vendor_part_number", ""))
        self._oem_edit.setText(data.get("oem_part_number", ""))
        self._title_edit.setText(data.get("title", ""))
        self._desc_edit.setPlainText(data.get("description", ""))
        self._category_edit.setText(data.get("category", ""))
        self._mfr_edit.setText(data.get("manufacturer", ""))
        self._cost_spin.setValue(float(data.get("cost") or 0))
        self._price_spin.setValue(float(data.get("price") or 0))

        # Generate SKU preview
        try:
            from jaks_inventory.services.vendor_import_service import (
                VendorImportService,
                VendorImportData,
            )

            svc = VendorImportService()
            import_data = VendorImportData(
                vendor=data.get("vendor", ""),
                vendor_part_number=data.get("vendor_part_number", ""),
                oem_part_number=data.get("oem_part_number", ""),
            )
            prepared = svc.prepare_import(import_data)
            self._sku_edit.setText(prepared["sku_preview"])

            # Show duplicate warning if matches found
            mr = prepared["match_result"]
            if mr.has_match:
                matches_text = []
                for m in mr.matches[:3]:
                    matches_text.append(
                        f"  • {m.get('sku', '?')} — {m.get('title', '')[:60]} "
                        f"({m.get('match_reason', '')})"
                    )
                self._dup_label.setText(
                    f"⚠️ Possible duplicate(s) found:\n" + "\n".join(matches_text)
                )
                self._dup_banner.show()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _save(self, action: str):
        """Validate and create the product."""
        from jaks_inventory.services.vendor_import_service import (
            VendorImportService,
            VendorImportData,
        )

        xrefs = [
            line.strip()
            for line in self._xref_edit.toPlainText().split("\n")
            if line.strip()
        ]

        data = VendorImportData(
            vendor=self._vendor_edit.text().strip(),
            vendor_part_number=self._vpn_edit.text().strip(),
            oem_part_number=self._oem_edit.text().strip(),
            title=self._title_edit.text().strip(),
            description=self._desc_edit.toPlainText().strip(),
            category=self._category_edit.text().strip(),
            manufacturer=self._mfr_edit.text().strip(),
            condition=self._condition_combo.currentText(),
            cost=self._cost_spin.value(),
            price=self._price_spin.value(),
            core_charge=self._core_spin.value(),
            cross_references=xrefs,
            notes=self._notes_edit.text().strip(),
        )

        svc = VendorImportService()
        prepared = svc.prepare_import(data)

        # If duplicates, confirm
        mr = prepared["match_result"]
        if mr.has_match:
            reply = QMessageBox.question(
                self,
                "Possible Duplicate",
                f"A similar product already exists:\n\n"
                f"  {mr.best_match.get('sku', '')} — {mr.best_match.get('title', '')[:80]}\n\n"
                f"Create a new product anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        result = svc.execute_import(data)
        if not result.get("success"):
            QMessageBox.warning(self, "Import Failed", result.get("error", "Unknown error"))
            return

        self.product_created.emit({
            "id": result["id"],
            "sku": result["sku"],
            "action": action,
        })

        QMessageBox.information(
            self,
            "Product Created",
            f"Product {result['sku']} created successfully.",
        )
        self.accept()

    @staticmethod
    def _btn_style(color: str) -> str:
        return (
            f"QPushButton {{ background: {color}; color: white; font-weight: 600; "
            f"padding: 8px 16px; border-radius: 6px; font-size: 12px; }}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
        )
