"""Dialog for adding/editing serial numbers (Phase I Part 2)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QGroupBox,
)
from PySide6.QtCore import QDate

from db import (
    get_serial,
    get_serial_by_number,
    create_serial,
    update_serial,
    bulk_create_serials,
    list_locations,
    get_all_products,
)


class SerialDialog(QDialog):
    """Dialog for creating or editing serial numbers."""

    def __init__(
        self,
        serial_id: int | None = None,
        product_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._serial_id = serial_id
        self._preset_product_id = product_id
        self._is_edit = serial_id is not None

        self.setWindowTitle("Edit Serial" if self._is_edit else "Add Serial Numbers")
        self.setMinimumWidth(500)
        self._build_ui()

        if self._is_edit:
            self._load_serial()
        elif self._preset_product_id:
            # Pre-select product
            idx = self._product_combo.findData(self._preset_product_id)
            if idx >= 0:
                self._product_combo.setCurrentIndex(idx)

    def _build_ui(self) -> None:
        # Product selection
        self._product_combo = QComboBox()
        self._product_combo.setMinimumWidth(300)
        self._load_products()

        # Single serial entry (for edit) or bulk entry (for add)
        if self._is_edit:
            self._serial_edit = QLineEdit()
            self._serial_edit.setPlaceholderText("Serial number")
            self._serial_edit.setEnabled(False)  # Can't change serial on edit
        else:
            self._serial_edit = QPlainTextEdit()
            self._serial_edit.setPlaceholderText(
                "Enter serial numbers (one per line):\n"
                "SN-001\n"
                "SN-002\n"
                "SN-003"
            )
            self._serial_edit.setMaximumHeight(120)

        # Common fields
        self._lot_edit = QLineEdit()
        self._lot_edit.setPlaceholderText("Lot/Batch number (optional)")

        self._location_combo = QComboBox()
        self._location_combo.addItem("-- No Location --", None)
        self._load_locations()

        self._cost_spin = QDoubleSpinBox()
        self._cost_spin.setRange(0, 999999.99)
        self._cost_spin.setDecimals(2)
        self._cost_spin.setPrefix("$")

        self._received_date = QDateEdit()
        self._received_date.setCalendarPopup(True)
        self._received_date.setDate(QDate.currentDate())

        self._expiry_date = QDateEdit()
        self._expiry_date.setCalendarPopup(True)
        self._expiry_date.setSpecialValueText("No expiry")
        self._expiry_date.setDate(QDate(2000, 1, 1))  # Special value

        self._warranty_date = QDateEdit()
        self._warranty_date.setCalendarPopup(True)
        self._warranty_date.setSpecialValueText("No warranty")
        self._warranty_date.setDate(QDate(2000, 1, 1))  # Special value

        self._status_combo = QComboBox()
        self._status_combo.addItem("Available", "available")
        self._status_combo.addItem("Sold", "sold")
        self._status_combo.addItem("Reserved", "reserved")
        self._status_combo.addItem("Defective", "defective")
        if not self._is_edit:
            self._status_combo.setEnabled(False)  # New serials are always available

        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Notes...")
        self._notes_edit.setMaximumHeight(60)

        # Form layout
        form = QFormLayout()
        form.addRow("Product*:", self._product_combo)
        
        if self._is_edit:
            form.addRow("Serial #:", self._serial_edit)
        else:
            form.addRow("Serial #s*:", self._serial_edit)
        
        form.addRow("Lot/Batch:", self._lot_edit)
        form.addRow("Location:", self._location_combo)
        form.addRow("Unit Cost:", self._cost_spin)
        form.addRow("Received:", self._received_date)
        form.addRow("Expiry:", self._expiry_date)
        form.addRow("Warranty Until:", self._warranty_date)
        
        if self._is_edit:
            form.addRow("Status:", self._status_combo)
        
        form.addRow("Notes:", self._notes_edit)

        # Help text for bulk entry
        if not self._is_edit:
            help_label = QLabel(
                "💡 Enter one serial number per line. "
                "Duplicates will be skipped."
            )
            help_label.setStyleSheet("color: gray; font-size: 10px;")
            form.addRow("", help_label)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)

        # Main layout
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def _load_products(self) -> None:
        """Load products into combo."""
        self._product_combo.clear()
        self._product_combo.addItem("-- Select Product --", None)
        try:
            products = get_all_products()
            for p in products:
                sku = str(p.get("sku", ""))
                title = str(p.get("title", ""))[:40]
                display = f"{sku} - {title}" if title else sku
                self._product_combo.addItem(display, p.get("id"))
        except Exception:
            pass

    def _load_locations(self) -> None:
        """Load locations into combo."""
        try:
            locations = list_locations(active_only=True)
            for loc in locations:
                code = str(loc.get("code", ""))
                name = str(loc.get("name", ""))
                display = f"{code} - {name}" if name else code
                self._location_combo.addItem(display, loc.get("id"))
        except Exception:
            pass

    def _load_serial(self) -> None:
        """Load existing serial data."""
        if not self._serial_id:
            return

        serial = get_serial(self._serial_id)
        if not serial:
            QMessageBox.warning(self, "Error", "Serial not found.")
            self.reject()
            return

        # Set product (find in combo)
        product_id = serial.get("product_id")
        idx = self._product_combo.findData(product_id)
        if idx >= 0:
            self._product_combo.setCurrentIndex(idx)
        self._product_combo.setEnabled(False)  # Can't change product on edit

        self._serial_edit.setText(str(serial.get("serial_number", "")))
        self._lot_edit.setText(str(serial.get("lot_number", "") or ""))

        # Location
        location_id = serial.get("location_id")
        idx = self._location_combo.findData(location_id)
        if idx >= 0:
            self._location_combo.setCurrentIndex(idx)

        self._cost_spin.setValue(float(serial.get("cost", 0) or 0))

        # Dates
        received = str(serial.get("received_date", "") or "")
        if received and received != "None":
            self._received_date.setDate(QDate.fromString(received[:10], "yyyy-MM-dd"))

        expiry = str(serial.get("expiry_date", "") or "")
        if expiry and expiry != "None":
            self._expiry_date.setDate(QDate.fromString(expiry[:10], "yyyy-MM-dd"))

        warranty = str(serial.get("warranty_expiry", "") or "")
        if warranty and warranty != "None":
            self._warranty_date.setDate(QDate.fromString(warranty[:10], "yyyy-MM-dd"))

        # Status
        status = str(serial.get("status", "available"))
        idx = self._status_combo.findData(status)
        if idx >= 0:
            self._status_combo.setCurrentIndex(idx)

        self._notes_edit.setPlainText(str(serial.get("notes", "") or ""))

    def _on_save(self) -> None:
        """Validate and save."""
        product_id = self._product_combo.currentData()
        if not product_id:
            QMessageBox.warning(self, "Validation Error", "Please select a product.")
            return

        # Get common values
        lot_number = self._lot_edit.text().strip() or None
        location_id = self._location_combo.currentData()
        cost = self._cost_spin.value()
        notes = self._notes_edit.toPlainText().strip() or None

        # Dates (None if at special value)
        received_date = None
        if self._received_date.date().year() > 2000:
            received_date = self._received_date.date().toString("yyyy-MM-dd")

        expiry_date = None
        if self._expiry_date.date().year() > 2000:
            expiry_date = self._expiry_date.date().toString("yyyy-MM-dd")

        warranty_expiry = None
        if self._warranty_date.date().year() > 2000:
            warranty_expiry = self._warranty_date.date().toString("yyyy-MM-dd")

        try:
            if self._is_edit:
                # Update existing
                status = self._status_combo.currentData()
                update_serial(
                    serial_id=self._serial_id,
                    lot_number=lot_number,
                    status=status,
                    location_id=location_id if location_id else 0,
                    cost=cost,
                    expiry_date=expiry_date,
                    warranty_expiry=warranty_expiry,
                    notes=notes,
                )
                self.accept()
            else:
                # Bulk create
                text = self._serial_edit.toPlainText().strip()
                if not text:
                    QMessageBox.warning(self, "Validation Error", "Enter at least one serial number.")
                    return

                serial_numbers = [s.strip() for s in text.split("\n") if s.strip()]
                if not serial_numbers:
                    QMessageBox.warning(self, "Validation Error", "Enter at least one serial number.")
                    return

                created = bulk_create_serials(
                    product_id=product_id,
                    serial_numbers=serial_numbers,
                    lot_number=lot_number,
                    location_id=location_id,
                    cost=cost,
                )

                if created < len(serial_numbers):
                    skipped = len(serial_numbers) - created
                    QMessageBox.information(
                        self,
                        "Serials Created",
                        f"Created {created} serial numbers.\n{skipped} duplicates were skipped."
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Serials Created",
                        f"Created {created} serial numbers."
                    )
                self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save: {e}")
