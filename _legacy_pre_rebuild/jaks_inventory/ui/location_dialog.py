"""Dialog for adding/editing storage locations (Phase I)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from db import (
    get_location,
    get_location_by_code,
    create_location,
    update_location,
)


class LocationDialog(QDialog):
    """Dialog for creating or editing a storage location."""

    def __init__(
        self,
        location_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._location_id = location_id
        self._is_edit = location_id is not None

        self.setWindowTitle("Edit Location" if self._is_edit else "Add Location")
        self.setMinimumWidth(450)
        self._build_ui()

        if self._is_edit:
            self._load_location()

    def _build_ui(self) -> None:
        # Form fields
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g., Main Warehouse Shelf A1")

        self._code_edit = QLineEdit()
        self._code_edit.setPlaceholderText("e.g., WH-A1-01 (unique code)")
        self._code_edit.setMaxLength(20)
        
        self._zone_edit = QLineEdit()
        self._zone_edit.setPlaceholderText("e.g., MAIN, OVERFLOW, STAGING")

        self._aisle_edit = QLineEdit()
        self._aisle_edit.setPlaceholderText("e.g., A, B, 1, 2")
        self._aisle_edit.setMaxLength(10)

        self._shelf_edit = QLineEdit()
        self._shelf_edit.setPlaceholderText("e.g., 1, 2, TOP, BOTTOM")
        self._shelf_edit.setMaxLength(10)

        self._bin_edit = QLineEdit()
        self._bin_edit.setPlaceholderText("e.g., 01, 02, LEFT, RIGHT")
        self._bin_edit.setMaxLength(10)

        self._capacity_spin = QSpinBox()
        self._capacity_spin.setRange(0, 99999)
        self._capacity_spin.setValue(0)
        self._capacity_spin.setSpecialValueText("No limit")

        self._active_check = QCheckBox("Active")
        self._active_check.setChecked(True)

        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText("Optional notes about this location...")
        self._notes_edit.setMaximumHeight(80)

        # Form layout
        form = QFormLayout()
        form.addRow("Name*:", self._name_edit)
        form.addRow("Code*:", self._code_edit)
        form.addRow("Zone:", self._zone_edit)
        form.addRow("Aisle:", self._aisle_edit)
        form.addRow("Shelf:", self._shelf_edit)
        form.addRow("Bin:", self._bin_edit)
        form.addRow("Capacity:", self._capacity_spin)
        form.addRow("", self._active_check)
        form.addRow("Notes:", self._notes_edit)

        # Help text
        help_label = QLabel(
            "💡 Tip: Use a consistent naming convention like ZONE-AISLE-SHELF-BIN\n"
            "Example: WH-A-3-01 = Warehouse, Aisle A, Shelf 3, Bin 01"
        )
        help_label.setStyleSheet("color: gray; font-size: 10px;")

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)

        # Main layout
        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(help_label)
        layout.addWidget(button_box)
        self.setLayout(layout)

    def _load_location(self) -> None:
        """Load existing location data."""
        if not self._location_id:
            return

        loc = get_location(self._location_id)
        if not loc:
            QMessageBox.warning(self, "Error", "Location not found.")
            self.reject()
            return

        self._name_edit.setText(str(loc.get("name", "") or ""))
        self._code_edit.setText(str(loc.get("code", "") or ""))
        self._zone_edit.setText(str(loc.get("zone", "") or ""))
        self._aisle_edit.setText(str(loc.get("aisle", "") or ""))
        self._shelf_edit.setText(str(loc.get("shelf", "") or ""))
        self._bin_edit.setText(str(loc.get("bin", "") or ""))
        self._capacity_spin.setValue(int(loc.get("capacity", 0) or 0))
        self._active_check.setChecked(bool(loc.get("is_active", True)))
        self._notes_edit.setPlainText(str(loc.get("notes", "") or ""))

        # Don't allow editing code for existing locations (it's a unique identifier)
        self._code_edit.setEnabled(False)

    def _on_save(self) -> None:
        """Validate and save the location."""
        name = self._name_edit.text().strip()
        code = self._code_edit.text().strip().upper()

        # Validation
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            self._name_edit.setFocus()
            return

        if not code:
            QMessageBox.warning(self, "Validation Error", "Code is required.")
            self._code_edit.setFocus()
            return

        # Check for duplicate code (only for new locations)
        if not self._is_edit:
            existing = get_location_by_code(code)
            if existing:
                QMessageBox.warning(
                    self,
                    "Duplicate Code",
                    f"A location with code '{code}' already exists."
                )
                self._code_edit.setFocus()
                return

        # Gather field values
        zone = self._zone_edit.text().strip() or None
        aisle = self._aisle_edit.text().strip() or None
        shelf = self._shelf_edit.text().strip() or None
        bin_val = self._bin_edit.text().strip() or None
        capacity = self._capacity_spin.value()
        is_active = self._active_check.isChecked()
        notes = self._notes_edit.toPlainText().strip() or None

        try:
            if self._is_edit:
                update_location(
                    location_id=self._location_id,
                    name=name,
                    zone=zone,
                    aisle=aisle,
                    shelf=shelf,
                    bin=bin_val,
                    capacity=capacity,
                    is_active=is_active,
                    notes=notes,
                )
            else:
                create_location(
                    name=name,
                    code=code,
                    zone=zone,
                    aisle=aisle,
                    shelf=shelf,
                    bin=bin_val,
                    capacity=capacity,
                    notes=notes,
                )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save location: {e}")
