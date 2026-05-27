"""Transfer dialogs (Phase I Part 4).

Dialogs for creating transfers and managing transfer line items.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt

log = logging.getLogger(__name__)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    list_locations,
    get_transfer_lines,
    add_transfer_line,
    remove_transfer_line,
    get_products_at_location,
    browse_products,
)


class TransferDialog(QDialog):
    """Dialog for creating a new transfer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Inventory Transfer")
        self.setMinimumWidth(450)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Form
        form_layout = QFormLayout()

        # From location
        self._from_combo = QComboBox()
        self._from_combo.addItem("-- Select Source Location --", None)
        self._load_locations(self._from_combo)
        form_layout.addRow("From Location:", self._from_combo)

        # To location
        self._to_combo = QComboBox()
        self._to_combo.addItem("-- Select Destination --", None)
        self._load_locations(self._to_combo)
        form_layout.addRow("To Location:", self._to_combo)

        # Requested by
        self._requested_by = QLineEdit()
        self._requested_by.setPlaceholderText("Your name (optional)")
        form_layout.addRow("Requested By:", self._requested_by)

        # Notes
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setMaximumHeight(80)
        self._notes_edit.setPlaceholderText("Transfer notes...")
        form_layout.addRow("Notes:", self._notes_edit)

        layout.addLayout(form_layout)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.setLayout(layout)

    def _load_locations(self, combo: QComboBox) -> None:
        """Load active locations into combo."""
        try:
            locations = list_locations(active_only=True)
            for loc in locations:
                code = str(loc.get("code", ""))
                name = str(loc.get("name", ""))
                display = f"{code} - {name}" if name else code
                combo.addItem(display, loc.get("id"))
        except Exception as e:
            log.debug("transfer dialog location combo load failed: %s", e)

    def _on_accept(self) -> None:
        """Validate and accept."""
        from_id = self._from_combo.currentData()
        to_id = self._to_combo.currentData()

        if not from_id:
            QMessageBox.warning(self, "Validation", "Please select a source location")
            return
        if not to_id:
            QMessageBox.warning(self, "Validation", "Please select a destination location")
            return
        if from_id == to_id:
            QMessageBox.warning(self, "Validation", "Source and destination must be different")
            return

        self.accept()

    def get_data(self) -> dict:
        """Get the entered data."""
        return {
            "from_location_id": self._from_combo.currentData(),
            "to_location_id": self._to_combo.currentData(),
            "requested_by": self._requested_by.text().strip() or None,
            "notes": self._notes_edit.toPlainText().strip() or None,
        }


class TransferLinesDialog(QDialog):
    """Dialog for managing transfer line items."""

    def __init__(self, transfer: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._transfer = transfer
        self._transfer_id = transfer.get("id")
        self._from_location_id = transfer.get("from_location_id")
        self._is_editable = transfer.get("status") in ("draft", "pending")
        
        self.setWindowTitle(f"Transfer {transfer.get('transfer_number', '')} - Lines")
        self.setMinimumSize(700, 500)
        self._build_ui()
        self._load_lines()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Transfer header info
        header = QGroupBox("Transfer Details")
        header_layout = QFormLayout()
        header_layout.addRow("Transfer #:", QLabel(str(self._transfer.get("transfer_number", ""))))
        header_layout.addRow("Status:", QLabel(str(self._transfer.get("status", "")).title()))
        header_layout.addRow("From:", QLabel(
            f"{self._transfer.get('from_location_code', '')} - {self._transfer.get('from_location_name', '')}"
        ))
        header_layout.addRow("To:", QLabel(
            f"{self._transfer.get('to_location_code', '')} - {self._transfer.get('to_location_name', '')}"
        ))
        header.setLayout(header_layout)
        layout.addWidget(header)

        # Add line section (only if editable)
        if self._is_editable:
            add_group = QGroupBox("Add Items")
            add_layout = QHBoxLayout()

            self._product_combo = QComboBox()
            self._product_combo.setMinimumWidth(300)
            self._product_combo.addItem("-- Select Product --", None)
            self._load_available_products()

            self._qty_spin = QSpinBox()
            self._qty_spin.setRange(1, 9999)
            self._qty_spin.setValue(1)

            add_btn = QPushButton("➕ Add")
            add_btn.clicked.connect(self._on_add_line)

            add_layout.addWidget(QLabel("Product:"))
            add_layout.addWidget(self._product_combo, 1)
            add_layout.addWidget(QLabel("Qty:"))
            add_layout.addWidget(self._qty_spin)
            add_layout.addWidget(add_btn)

            add_group.setLayout(add_layout)
            layout.addWidget(add_group)

        # Lines table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["SKU", "Product", "Qty", "Serial", ""])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self._table, 1)

        # Close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.accept)
        layout.addWidget(btn_box)

        self.setLayout(layout)

    def _load_available_products(self) -> None:
        """Load products available at the source location."""
        try:
            # Get products at the source location
            products = get_products_at_location(self._from_location_id)
            for p in products:
                sku = str(p.get("sku", ""))
                title = str(p.get("title", ""))[:35]
                qty = p.get("quantity", 0)
                display = f"{sku} - {title} (Avail: {qty})"
                self._product_combo.addItem(display, {
                    "product_id": p.get("product_id"),
                    "available": qty
                })
        except Exception as e:
            # Fall back to all products if location query fails
            log.debug("location product list failed, falling back to browse: %s", e)
            try:
                products = browse_products(limit=200)
                for p in products:
                    sku = str(p.get("sku", ""))
                    title = str(p.get("title", ""))[:35]
                    display = f"{sku} - {title}"
                    self._product_combo.addItem(display, {
                        "product_id": p.get("id"),
                        "available": 999
                    })
            except Exception as e2:
                log.warning("transfer fallback product list failed (combo will be empty): %s", e2)

    def _load_lines(self) -> None:
        """Load transfer line items."""
        try:
            lines = get_transfer_lines(self._transfer_id)
        except Exception as e:
            lines = []
            log.warning("Transfer lines load failed: %s", e)

        self._table.setRowCount(len(lines))

        for row_idx, line in enumerate(lines):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(line.get("sku", "")))
            self._table.setItem(row_idx, 1, item(str(line.get("product_title", ""))[:35]))
            self._table.setItem(row_idx, 2, item(str(line.get("quantity", 0))))
            self._table.setItem(row_idx, 3, item(line.get("serial_number", "")))

            # Remove button (only if editable)
            if self._is_editable:
                remove_btn = QPushButton("🗑️")
                remove_btn.setMaximumWidth(40)
                line_id = line.get("id")
                remove_btn.clicked.connect(lambda checked, lid=line_id: self._on_remove_line(lid))
                self._table.setCellWidget(row_idx, 4, remove_btn)
            else:
                self._table.setItem(row_idx, 4, item(""))

        self._table.resizeColumnsToContents()

    def _on_add_line(self) -> None:
        """Add a line item to the transfer."""
        data = self._product_combo.currentData()
        if not data:
            QMessageBox.warning(self, "Validation", "Please select a product")
            return

        product_id = data.get("product_id")
        available = data.get("available", 0)
        qty = self._qty_spin.value()

        if qty > available:
            QMessageBox.warning(
                self, "Validation",
                f"Quantity ({qty}) exceeds available ({available})"
            )
            return

        try:
            add_transfer_line(
                transfer_id=self._transfer_id,
                product_id=product_id,
                quantity=qty
            )
            self._load_lines()
            self._qty_spin.setValue(1)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to add line: {e}")

    def _on_remove_line(self, line_id: int) -> None:
        """Remove a line item."""
        try:
            remove_transfer_line(line_id)
            self._load_lines()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to remove line: {e}")
