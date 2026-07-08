"""Bulk quantity adjustment dialog for multiple products."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QComboBox,
    QSpinBox,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QDialogButtonBox,
    QGroupBox,
    QMessageBox,
)

from db import bulk_adjust_quantity


class BulkAdjustDialog(QDialog):
    """Dialog for adjusting quantity on multiple products at once."""

    def __init__(self, products: list[dict], parent=None):
        """Initialize with list of products to adjust.
        
        Args:
            products: List of product dicts (must have 'id', 'sku', 'title', 'qty_on_hand')
            parent: Parent widget
        """
        super().__init__(parent)
        self._products = products
        self._result = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"Bulk Adjust Quantity ({len(self._products)} products)")
        self.setMinimumWidth(450)

        layout = QVBoxLayout()

        # Products summary
        summary_box = QGroupBox("Selected Products")
        summary_layout = QVBoxLayout()
        
        # Show first few products
        display_products = self._products[:5]
        for p in display_products:
            sku = p.get("sku", "?")
            title = p.get("title", "")[:40]
            qty = p.get("qty_on_hand", 0)
            summary_layout.addWidget(QLabel(f"• {sku}: {title}... (Qty: {qty})"))
        
        if len(self._products) > 5:
            summary_layout.addWidget(QLabel(f"... and {len(self._products) - 5} more"))
        
        summary_box.setLayout(summary_layout)
        layout.addWidget(summary_box)

        # Adjustment form
        form_box = QGroupBox("Adjustment")
        form_layout = QFormLayout()

        self._type_combo = QComboBox()
        self._type_combo.addItem("📥 Receive (add stock)", "receive")
        self._type_combo.addItem("📤 Sell (remove stock)", "sell")
        self._type_combo.addItem("📊 Adjust (set delta)", "adjust")
        self._type_combo.addItem("↩️ Return (add back)", "return")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        form_layout.addRow("Type:", self._type_combo)

        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 9999)
        self._qty_spin.setValue(1)
        self._qty_label = QLabel("Quantity to add:")
        form_layout.addRow(self._qty_label, self._qty_spin)

        self._reference = QLineEdit()
        self._reference.setPlaceholderText("PO#, Invoice#, etc.")
        form_layout.addRow("Reference:", self._reference)

        self._notes = QTextEdit()
        self._notes.setPlaceholderText("Optional notes...")
        self._notes.setMaximumHeight(60)
        form_layout.addRow("Notes:", self._notes)

        form_box.setLayout(form_layout)
        layout.addWidget(form_box)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _on_type_changed(self, index: int) -> None:
        """Update label based on transaction type."""
        txn_type = self._type_combo.currentData()
        if txn_type in ("receive", "return"):
            self._qty_label.setText("Quantity to add:")
        else:
            self._qty_label.setText("Quantity to remove:")

    def _on_accept(self) -> None:
        """Apply the bulk adjustment."""
        txn_type = self._type_combo.currentData()
        qty = self._qty_spin.value()
        reference = self._reference.text().strip() or None
        notes = self._notes.toPlainText().strip() or None

        # Determine delta based on type
        if txn_type in ("sell",):
            delta = -qty
        else:
            delta = qty

        product_ids = [p["id"] for p in self._products]

        # Confirm
        action = "add" if delta > 0 else "remove"
        msg = f"This will {action} {abs(delta)} from {len(product_ids)} products.\n\nContinue?"
        reply = QMessageBox.question(
            self,
            "Confirm Bulk Adjustment",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Execute
        result = bulk_adjust_quantity(
            product_ids=product_ids,
            delta=delta,
            txn_type=txn_type,
            reference=reference,
            notes=notes,
        )

        self._result = result

        if result["failed"] == 0:
            QMessageBox.information(
                self,
                "Bulk Adjustment Complete",
                f"Successfully adjusted {result['success']} products.",
            )
            self.accept()
        else:
            error_msg = f"Success: {result['success']}, Failed: {result['failed']}\n\n"
            for err in result["errors"][:5]:
                error_msg += f"• Product {err['product_id']}: {err['error']}\n"
            QMessageBox.warning(self, "Partial Success", error_msg)
            self.accept()

    def get_result(self) -> dict | None:
        """Get the result of the bulk adjustment."""
        return self._result
