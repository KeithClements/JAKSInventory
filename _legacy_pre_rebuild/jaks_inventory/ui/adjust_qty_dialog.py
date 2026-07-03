"""Dialog for adjusting product quantity (receive/sell/adjust)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from db import adjust_product_quantity, get_product_by_sku


class AdjustQtyDialog(QDialog):
    """Dialog for adjusting inventory quantity."""

    def __init__(self, sku: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Adjust Inventory Quantity")
        self.setMinimumWidth(400)

        self._sku = sku
        self._product = get_product_by_sku(sku)
        
        if not self._product:
            QMessageBox.warning(self, "Error", f"Product not found: {sku}")
            self.reject()
            return

        current_qty = self._product.get("qty_on_hand", 0) or 0
        title = self._product.get("title", "") or sku

        # Header
        header = QLabel(f"<b>{sku}</b><br/>{title}")
        header.setWordWrap(True)

        # Current quantity display
        self._current_label = QLabel(f"<b style='font-size: 18pt'>{current_qty}</b>")
        self._current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Transaction type
        self._txn_type = QComboBox()
        self._txn_type.addItem("Receive (add stock)", "receive")
        self._txn_type.addItem("Sell (remove stock)", "sale")
        self._txn_type.addItem("Adjust (+/-)", "adjust")
        self._txn_type.addItem("Return (add back)", "return")
        self._txn_type.currentIndexChanged.connect(self._update_preview)

        # Quantity change
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(-9999, 9999)
        self._qty_spin.setValue(1)
        self._qty_spin.valueChanged.connect(self._update_preview)

        # Reference (optional)
        self._reference = QLineEdit()
        self._reference.setPlaceholderText("PO#, Invoice#, etc. (optional)")

        # Notes
        self._notes = QTextEdit()
        self._notes.setPlaceholderText("Notes (optional)")
        self._notes.setMaximumHeight(60)

        # Preview label
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("font-size: 14pt; padding: 10px;")

        # Buttons
        self._btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._btn_box.accepted.connect(self._on_accept)
        self._btn_box.rejected.connect(self.reject)

        # Layout
        form = QFormLayout()
        form.addRow("Current Qty:", self._current_label)
        form.addRow("Transaction:", self._txn_type)
        form.addRow("Quantity:", self._qty_spin)
        form.addRow("Reference:", self._reference)
        form.addRow("Notes:", self._notes)

        layout = QVBoxLayout()
        layout.addWidget(header)
        layout.addLayout(form)
        layout.addWidget(self._preview)
        layout.addWidget(self._btn_box)
        self.setLayout(layout)

        # Initial preview
        self._current_qty = current_qty
        self._update_preview()

    def _update_preview(self) -> None:
        """Update the preview showing what qty will become."""
        txn_type = self._txn_type.currentData()
        qty_value = self._qty_spin.value()

        # Update notes placeholder based on type
        if txn_type == "adjust":
            self._notes.setPlaceholderText("Reason for adjustment (REQUIRED)")
        else:
            self._notes.setPlaceholderText("Notes (optional)")

        # Calculate delta based on transaction type
        if txn_type == "sale":
            delta = -abs(qty_value)
        elif txn_type in ("receive", "return"):
            delta = abs(qty_value)
        else:  # adjust
            delta = qty_value

        new_qty = self._current_qty + delta

        # Color code based on validity
        if new_qty < 0:
            color = "red"
            status = "⚠️ Invalid"
        elif new_qty == 0:
            color = "orange"
            status = "Zero stock"
        else:
            color = "green"
            status = "OK"

        self._preview.setText(
            f"<span style='color: {color}'>"
            f"{self._current_qty} → <b>{new_qty}</b> ({status})"
            f"</span>"
        )
        self._delta = delta
        self._new_qty = new_qty

    def _on_accept(self) -> None:
        """Process the quantity adjustment."""
        if self._new_qty < 0:
            QMessageBox.warning(
                self,
                "Invalid Quantity",
                "Cannot reduce quantity below zero."
            )
            return

        txn_type = self._txn_type.currentData()
        reference = self._reference.text().strip()
        notes = self._notes.toPlainText().strip()

        # Adjustments require a reason
        if txn_type == "adjust" and not notes:
            QMessageBox.warning(
                self,
                "Reason Required",
                "A reason/note is required for manual inventory adjustments."
            )
            self._notes.setFocus()
            return

        result = adjust_product_quantity(
            sku=self._sku,
            delta=self._delta,
            txn_type=txn_type,
            notes=notes,
            reference=reference,
            user="inventory_app"
        )

        if result.get("success"):
            QMessageBox.information(
                self,
                "Success",
                f"Quantity updated: {result['qty_before']} → {result['qty_after']}"
            )
            self.accept()
        else:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to update quantity: {result.get('error', 'Unknown error')}"
            )
