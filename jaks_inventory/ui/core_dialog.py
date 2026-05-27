"""Core return dialogs (Phase I Part 3).

Dialogs for viewing core details, receiving cores, and issuing credits.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CoreDialog(QDialog):
    """Dialog for viewing core return details."""

    def __init__(self, core: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core
        self.setWindowTitle("Core Return Details")
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Status badge — widen color map and surface OVERDUE state.
        status_raw = str(self._core.get("status", "unknown") or "unknown").lower()
        status = status_raw.title()
        # Compute overdue: pending past due_date with no receipt.
        is_overdue = False
        try:
            from datetime import date
            due = str(self._core.get("due_date", "") or "")[:10]
            if status_raw == "pending" and due:
                is_overdue = due < date.today().isoformat()
        except Exception:
            is_overdue = False
        if is_overdue:
            status = "Overdue"
        status_colors = {
            "Pending": "#f57c00",
            "Open": "#f57c00",
            "Received": "#1565c0",
            "Credited": "#2e7d32",
            "Voided": "#666666",
            "Overdue": "#c62828",
        }
        color = status_colors.get(status, "#666")
        status_label = QLabel(f"<b style='color: {color}; font-size: 14px;'>{status}</b>")
        layout.addWidget(status_label)

        # Invoice/Customer info
        info_group = QGroupBox("Invoice Information")
        info_layout = QFormLayout()
        info_layout.addRow("Invoice #:", QLabel(str(self._core.get("invoice_number", ""))))
        info_layout.addRow("Customer:", QLabel(str(self._core.get("customer_name", ""))))
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Product info
        product_group = QGroupBox("Product")
        product_layout = QFormLayout()
        product_layout.addRow("SKU:", QLabel(str(self._core.get("sku", ""))))
        product_layout.addRow("Title:", QLabel(str(self._core.get("product_title", ""))))
        product_group.setLayout(product_layout)
        layout.addWidget(product_group)

        # Core charge info
        charge_group = QGroupBox("Core Charges")
        charge_layout = QFormLayout()
        
        core_charge = float(self._core.get("core_charge", 0) or 0)
        charge_layout.addRow("Core Charge:", QLabel(f"${core_charge:,.2f}"))
        charge_layout.addRow("Due Date:", QLabel(str(self._core.get("due_date", "") or "N/A")[:10]))
        
        if self._core.get("serial_number"):
            charge_layout.addRow("Serial #:", QLabel(str(self._core.get("serial_number", ""))))
        
        charge_group.setLayout(charge_layout)
        layout.addWidget(charge_group)

        # Receipt info (if received)
        if self._core.get("received_date"):
            receipt_group = QGroupBox("Receipt Information")
            receipt_layout = QFormLayout()
            receipt_layout.addRow("Received Date:", QLabel(str(self._core.get("received_date", ""))[:10]))
            receipt_layout.addRow("Condition:", QLabel(str(self._core.get("condition", "") or "N/A")))
            received_by = str(self._core.get("received_by", "") or "").strip()
            if received_by:
                receipt_layout.addRow("Received By:", QLabel(received_by))
            receipt_group.setLayout(receipt_layout)
            layout.addWidget(receipt_group)

        # Credit info (if credited)
        if self._core.get("credit_issued"):
            credit_group = QGroupBox("Credit Information")
            credit_layout = QFormLayout()
            credit_issued = float(self._core.get("credit_issued", 0) or 0)
            credit_layout.addRow("Credit Issued:", QLabel(f"${credit_issued:,.2f}"))
            credit_layout.addRow("Credit Date:", QLabel(str(self._core.get("credit_date", "") or "N/A")[:10]))
            credit_memo = str(self._core.get("credit_invoice_id", "") or "").strip()
            if credit_memo:
                credit_layout.addRow("Credit Memo #:", QLabel(credit_memo))
            credit_group.setLayout(credit_layout)
            layout.addWidget(credit_group)

        # Void info (if voided)
        if status_raw == "voided":
            void_group = QGroupBox("Void Information")
            void_layout = QFormLayout()
            void_reason = str(self._core.get("void_reason", "") or "").strip() or "—"
            void_layout.addRow("Void Reason:", QLabel(void_reason))
            void_group.setLayout(void_layout)
            layout.addWidget(void_group)

        # Notes
        if self._core.get("notes"):
            notes_group = QGroupBox("Notes")
            notes_layout = QVBoxLayout()
            notes_label = QLabel(str(self._core.get("notes", "")))
            notes_label.setWordWrap(True)
            notes_layout.addWidget(notes_label)
            notes_group.setLayout(notes_layout)
            layout.addWidget(notes_group)

        # Close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.setLayout(layout)


class ReceiveCoreDialog(QDialog):
    """Dialog for marking a core as received."""

    def __init__(self, core: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core
        self.setWindowTitle("Receive Core")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Header with core info
        header = QLabel(
            f"<b>Receiving core for Invoice {self._core.get('invoice_number', 'N/A')}</b><br>"
            f"Customer: {self._core.get('customer_name', 'N/A')}<br>"
            f"Product: {self._core.get('sku', '')} - {str(self._core.get('product_title', ''))[:40]}"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Form
        form_layout = QFormLayout()

        # Condition
        self._condition_combo = QComboBox()
        self._condition_combo.addItems(["Good", "Damaged", "Wrong Part", "Rebuildable", "Scrap"])
        form_layout.addRow("Condition:", self._condition_combo)

        # Received By (employee accountability) — defaults to last-used
        # value stored in settings so the rep doesn't retype every time.
        self._received_by_edit = QLineEdit()
        self._received_by_edit.setPlaceholderText("Employee name / initials")
        try:
            from db import get_setting  # local import to avoid cycle
            default_user = (get_setting("default_user_name", "") or "").strip()
        except Exception:
            default_user = ""
        if default_user:
            self._received_by_edit.setText(default_user)
        form_layout.addRow("Received By:", self._received_by_edit)

        # Serial number (optional)
        self._serial_edit = QLineEdit()
        self._serial_edit.setPlaceholderText("Optional serial number")
        if self._core.get("serial_number"):
            self._serial_edit.setText(str(self._core.get("serial_number", "")))
        form_layout.addRow("Serial #:", self._serial_edit)

        # Notes
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setMaximumHeight(100)
        self._notes_edit.setPlaceholderText("Notes about the returned core...")
        form_layout.addRow("Notes:", self._notes_edit)

        layout.addLayout(form_layout)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.setLayout(layout)

    def get_data(self) -> dict:
        """Get the entered data."""
        received_by = self._received_by_edit.text().strip() or None
        # Persist as the new default user name for convenience.
        if received_by:
            try:
                from db import set_setting
                set_setting("default_user_name", received_by)
            except Exception:
                pass
        return {
            "condition": self._condition_combo.currentText().lower(),
            "serial_number": self._serial_edit.text().strip() or None,
            "notes": self._notes_edit.toPlainText().strip() or None,
            "received_by": received_by,
        }


class IssueCreditDialog(QDialog):
    """Dialog for issuing credit for a returned core."""

    def __init__(self, core: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._core = core
        self.setWindowTitle("Issue Core Credit")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Header with core info
        core_charge = float(self._core.get("core_charge", 0) or 0)
        customer_price = float(self._core.get("customer_core_price", 0) or 0)
        # Customer paid amount = what they're owed back. Fall back to vendor
        # deposit for legacy rows that predate migration 067.
        full_credit = customer_price if customer_price > 0 else core_charge
        condition = str(self._core.get("condition", "good")).title()

        amount_lines = f"Customer Paid: <b>${full_credit:,.2f}</b>"
        if core_charge > 0 and abs(core_charge - full_credit) > 0.005:
            amount_lines += f"<br>Vendor Deposit: ${core_charge:,.2f}"

        header = QLabel(
            f"<b>Issue credit for Invoice {self._core.get('invoice_number', 'N/A')}</b><br>"
            f"Customer: {self._core.get('customer_name', 'N/A')}<br>"
            f"Product: {self._core.get('sku', '')} - {str(self._core.get('product_title', ''))[:40]}<br>"
            f"{amount_lines}<br>"
            f"Condition: {condition}"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # Form
        form_layout = QFormLayout()

        # Credit amount
        self._credit_spin = QDoubleSpinBox()
        self._credit_spin.setRange(0, 99999.99)
        self._credit_spin.setDecimals(2)
        self._credit_spin.setPrefix("$ ")
        self._credit_spin.setValue(full_credit)  # Default to full refund of what customer paid
        form_layout.addRow("Credit Amount:", self._credit_spin)

        # Quick credit buttons
        btn_row = QHBoxLayout()
        full_btn = QPushButton("Full Credit")
        full_btn.clicked.connect(lambda: self._credit_spin.setValue(full_credit))
        btn_row.addWidget(full_btn)

        half_btn = QPushButton("50%")
        half_btn.clicked.connect(lambda: self._credit_spin.setValue(full_credit * 0.5))
        btn_row.addWidget(half_btn)
        
        none_btn = QPushButton("No Credit")
        none_btn.clicked.connect(lambda: self._credit_spin.setValue(0))
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        
        form_layout.addRow("", btn_row)

        # Notes
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setMaximumHeight(80)
        self._notes_edit.setPlaceholderText("Reason for credit amount...")
        form_layout.addRow("Notes:", self._notes_edit)

        layout.addLayout(form_layout)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.setLayout(layout)

    def get_data(self) -> dict:
        """Get the entered data."""
        return {
            "credit_amount": self._credit_spin.value(),
            "credit_invoice_id": None,  # Future: link to credit memo
            "notes": self._notes_edit.toPlainText().strip() or None,
        }
