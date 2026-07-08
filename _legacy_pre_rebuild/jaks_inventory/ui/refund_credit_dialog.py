"""Refund a customer credit out to the customer.

Small modal used from the Customer Profile → Credit tab. The user picks
a disbursement method (check / cash / card refund / ACH), enters a
reference (check #, txn id, …), optional notes, and confirms.

The actual ledger update is performed by
``db.refund_customer_credit`` which sets ``status='refunded'`` and
populates ``refund_method`` / ``refund_reference`` / ``refund_date``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QButtonGroup,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import refund_customer_credit


_METHOD_LABELS = [
    ("check", "Check"),
    ("cash", "Cash"),
    ("card_refund", "Card Refund"),
    ("ach", "ACH / Wire"),
]

_REFERENCE_HINTS = {
    "check": "Check Number",
    "cash": "Drawer / Receipt #",
    "card_refund": "Processor Txn ID",
    "ach": "ACH / Wire Trace #",
}


class RefundCreditDialog(QDialog):
    """Disburse a single open customer credit."""

    def __init__(self, credit: dict, parent=None):
        super().__init__(parent)
        self._credit = dict(credit or {})
        self.setWindowTitle("Refund Customer Credit")
        self.setMinimumWidth(420)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        amt = float(self._credit.get("amount") or 0)
        cid = int(self._credit.get("id") or 0)
        header = QLabel(
            f"<b>Refunding credit CR-{cid}</b><br>"
            f"Amount: <span style='color:#2e7d32;font-size:14pt'>"
            f"${amt:,.2f}</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(header)

        form = QFormLayout()

        # Method radios
        method_box = QWidget()
        mb_layout = QVBoxLayout(method_box)
        mb_layout.setContentsMargins(0, 0, 0, 0)
        mb_layout.setSpacing(2)
        self._method_group = QButtonGroup(self)
        self._method_buttons: dict[str, QRadioButton] = {}
        for key, label in _METHOD_LABELS:
            rb = QRadioButton(label)
            mb_layout.addWidget(rb)
            self._method_group.addButton(rb)
            self._method_buttons[key] = rb
        form.addRow("Method:", method_box)

        self._reference_label = QLabel(_REFERENCE_HINTS["check"] + ":")
        self._reference_edit = QLineEdit()
        self._reference_edit.setPlaceholderText("e.g. 10421")
        form.addRow(self._reference_label, self._reference_edit)

        # Wire toggled signals AFTER _reference_label exists, then set
        # the default — otherwise the initial setChecked fires the
        # handler before the label is built.
        for rb in self._method_buttons.values():
            rb.toggled.connect(self._on_method_changed)
        self._method_buttons["check"].setChecked(True)

        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText("Optional internal note…")
        self._notes_edit.setMaximumHeight(70)
        form.addRow("Notes:", self._notes_edit)

        root.addLayout(form)

        warn = QLabel(
            "This marks the credit as <b>refunded</b> on the customer's "
            "ledger. Cut the actual check / process the card refund "
            "outside the system before recording it here."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "color: #6d4c00; background: #fff8e1; padding: 6px; "
            "border-radius: 4px; font-size: 9pt;"
        )
        root.addWidget(warn)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Record Refund")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ------------------------------------------------------------------
    def _selected_method(self) -> str:
        for key, rb in self._method_buttons.items():
            if rb.isChecked():
                return key
        return "check"

    def _on_method_changed(self, checked: bool) -> None:  # noqa: ARG002
        m = self._selected_method()
        self._reference_label.setText(_REFERENCE_HINTS.get(m, "Reference") + ":")

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        method = self._selected_method()
        ref = self._reference_edit.text().strip()
        notes = self._notes_edit.toPlainText().strip()

        # Cash refunds and card refunds typically don't need a ref #
        # (you have the receipt / processor record), but for check + ACH
        # we strongly recommend it for audit.
        if method in ("check", "ach") and not ref:
            resp = QMessageBox.question(
                self,
                "No Reference Number",
                f"No {_REFERENCE_HINTS.get(method, 'reference')} entered.\n"
                "Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return

        try:
            refund_customer_credit(
                credit_id=int(self._credit.get("id")),
                method=method,
                reference=ref or None,
                notes=notes or None,
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Refund Error", f"Could not record refund:\n{e}"
            )
            return

        self.accept()
