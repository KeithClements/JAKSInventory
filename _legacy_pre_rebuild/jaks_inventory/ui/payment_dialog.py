"""Payment recording dialog for invoices."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QFrame,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QDateEdit,
    QPushButton,
    QMessageBox,
    QDoubleSpinBox,
    QLabel,
)

from db import get_invoice
from db.inventory import record_payment
from .workflow_shell import build_bottom_action_bar, style_action_button


class PaymentDialog(QDialog):
    """Dialog for recording payment against an invoice."""

    def __init__(self, invoice_id: int, current_balance: float = 0.0, parent=None):
        super().__init__(parent)
        self._invoice_id = invoice_id
        self._current_balance = current_balance
        self._invoice = None

        if invoice_id:
            self._invoice = dict(get_invoice(invoice_id)) if get_invoice(invoice_id) else None

        self.setWindowTitle(f"Record Payment - Invoice")
        self.setMinimumWidth(500)
        self.resize(600, 400)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        header_frame = QFrame()
        header_frame.setStyleSheet(
            "QFrame { background: #F8FAFC; border: 1px solid #E5E7EB; border-radius: 6px; padding: 12px; }"
        )
        header_lay = QVBoxLayout(header_frame)
        header_lay.setContentsMargins(12, 8, 12, 8)
        header_lay.setSpacing(6)

        inv_number = self._invoice.get("invoice_number", "Unknown") if self._invoice else "Unknown"
        title = QLabel(f"Invoice: {inv_number}")
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #1F2937;")
        header_lay.addWidget(title)

        # Compute core total from invoice lines (customer-facing CORE- SKUs)
        core_total = 0.0
        core_units = 0
        if self._invoice:
            for ln in self._invoice.get("lines", []) or []:
                sku = (ln.get("sku") or "").upper()
                if sku.startswith("CORE-"):
                    core_total += float(ln.get("line_total") or 0)
                    core_units += int(ln.get("quantity") or 0)
        self._core_total = core_total

        balance_label = QLabel(f"Current Balance Due: ${self._current_balance:,.2f}")
        balance_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        header_lay.addWidget(balance_label)

        if core_total > 0:
            non_core = max(self._current_balance - core_total, 0.0)
            breakdown = QLabel(
                f"  • Parts / Services: ${non_core:,.2f}\n"
                f"  • Core Charges ({core_units} core{'s' if core_units != 1 else ''}, "
                f"refundable on return): ${core_total:,.2f}"
            )
            breakdown.setStyleSheet(
                "font-size: 11px; color: #92400E; background: #FEF3C7; "
                "border: 1px solid #FCD34D; border-radius: 4px; padding: 6px 8px;"
            )
            header_lay.addWidget(breakdown)

            note = QLabel(
                "Core charges are collected up front and credited back to the "
                "customer when the core is returned."
            )
            note.setWordWrap(True)
            note.setStyleSheet("font-size: 10px; color: #6B7280; font-style: italic;")
            header_lay.addWidget(note)

        root.addWidget(header_frame)

        # ── Store-credit banner (G1) ─────────────────────────────
        # Surface any open customer credits + a one-click apply button so
        # counter staff don't have to bounce out to the Customer screen.
        self._credit_frame = QFrame()
        self._credit_frame.setStyleSheet(
            "QFrame { background:#ECFDF5; border:1px solid #6EE7B7; "
            "border-radius:6px; padding:8px; }"
        )
        clay = QHBoxLayout(self._credit_frame)
        clay.setContentsMargins(10, 6, 10, 6)
        self._credit_label = QLabel("")
        self._credit_label.setTextFormat(Qt.TextFormat.RichText)
        clay.addWidget(self._credit_label, 1)
        self._apply_credit_btn = QPushButton("Apply Credit…")
        self._apply_credit_btn.setStyleSheet(style_action_button("primary"))
        self._apply_credit_btn.clicked.connect(self._on_apply_credit)
        clay.addWidget(self._apply_credit_btn)
        root.addWidget(self._credit_frame)
        self._refresh_credit_banner()

        # Form
        form_frame = QFrame()
        form_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 6px; padding: 12px; }"
        )
        form_lay = QFormLayout(form_frame)
        form_lay.setContentsMargins(16, 12, 16, 12)
        form_lay.setSpacing(10)

        form_lay.addRow(QLabel("Payment Details"))

        # Payment amount
        self._amount_spin = QDoubleSpinBox()
        self._amount_spin.setRange(0, 999999.99)
        self._amount_spin.setDecimals(2)
        self._amount_spin.setSuffix(" USD")
        self._amount_spin.setValue(self._current_balance)
        self._amount_spin.setStyleSheet(
            "QDoubleSpinBox { padding: 6px; border: 1px solid #D1D5DB; border-radius: 4px; }"
        )
        form_lay.addRow("Payment Amount:", self._amount_spin)

        # Payment date
        self._payment_date = QDateEdit()
        self._payment_date.setCalendarPopup(True)
        self._payment_date.setDate(date.today())
        self._payment_date.setStyleSheet(
            "QDateEdit { padding: 6px; border: 1px solid #D1D5DB; border-radius: 4px; }"
        )
        form_lay.addRow("Payment Date:", self._payment_date)

        # Payment method
        self._method_combo = QComboBox()
        self._method_combo.addItems([
            "Cash",
            "Check",
            "Credit Card",
            "Debit Card",
            "ACH / Bank Transfer",
            "Wire Transfer",
            "PayPal",
            "Other",
        ])
        self._method_combo.setStyleSheet(
            "QComboBox { padding: 6px; border: 1px solid #D1D5DB; border-radius: 4px; }"
        )
        form_lay.addRow("Payment Method:", self._method_combo)

        # Reference (check number, transaction ID, etc.)
        self._reference_edit = QLineEdit()
        self._reference_edit.setPlaceholderText("Check #, Transaction ID, Reference, etc.")
        self._reference_edit.setStyleSheet(
            "QLineEdit { padding: 6px; border: 1px solid #D1D5DB; border-radius: 4px; }"
        )
        form_lay.addRow("Reference:", self._reference_edit)

        # Notes
        form_lay.addRow(QLabel("\nNotes (optional)"))
        self._notes_edit = QTextEdit()
        self._notes_edit.setPlaceholderText("Any additional notes about this payment...")
        self._notes_edit.setMinimumHeight(80)
        self._notes_edit.setStyleSheet(
            "QTextEdit { padding: 6px; border: 1px solid #D1D5DB; border-radius: 4px; font-size: 11px; }"
        )
        form_lay.addRow(self._notes_edit)

        root.addWidget(form_frame, 1)

        # Bottom action bar
        bar, lay = build_bottom_action_bar()

        save_btn = QPushButton("💰 Record Payment")
        save_btn.setStyleSheet(style_action_button("success"))
        save_btn.clicked.connect(self._on_save)
        lay.addWidget(save_btn)

        lay.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(style_action_button("neutral"))
        cancel_btn.clicked.connect(self.reject)
        lay.addWidget(cancel_btn)

        root.addWidget(bar)

    def _on_save(self) -> None:
        """Save the payment record."""
        amount = self._amount_spin.value()
        if amount <= 0:
            QMessageBox.warning(self, "Invalid Amount", "Payment amount must be greater than 0.")
            return

        if amount > self._current_balance * 1.1:
            response = QMessageBox.question(
                self,
                "Overpayment",
                f"Payment amount (${amount:.2f}) exceeds balance due (${self._current_balance:.2f}).\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        method = self._method_combo.currentText()
        reference = self._reference_edit.text().strip()
        notes_text = self._notes_edit.toPlainText().strip()
        combined_notes = " | ".join(p for p in (reference and f"Ref: {reference}", notes_text) if p)

        try:
            ok = record_payment(
                invoice_id=self._invoice_id,
                amount=amount,
                payment_method=method or None,
                notes=combined_notes or None,
            )
            if not ok:
                QMessageBox.critical(self, "Save Error", "Invoice not found or could not be updated.")
                return
            # ``record_payment`` returns a truthy result that may carry an
            # overpayment credit id when payment exceeded the balance —
            # surface it so counter staff know the excess landed on the
            # customer's credit ledger and isn't lost.
            overpay_amount = float(getattr(ok, "overpay_amount", 0) or 0)
            credit_id = getattr(ok, "overpayment_credit_id", None)
            if overpay_amount > 0:
                msg = (
                    f"Payment of ${amount:,.2f} recorded against invoice.\n\n"
                    f"Overpayment of ${overpay_amount:,.2f} added to "
                    f"customer credit ledger"
                )
                if credit_id:
                    msg += f" (CR-{credit_id})"
                msg += "."
                QMessageBox.information(self, "Payment + Credit", msg)
            else:
                QMessageBox.information(
                    self,
                    "Payment Recorded",
                    f"Payment of ${amount:,.2f} recorded against invoice.",
                )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not record payment:\n{e}")

    # ── G1: Store-credit application ─────────────────────────────

    def _customer_id(self) -> int | None:
        try:
            return int((self._invoice or {}).get("customer_id") or 0) or None
        except Exception:
            return None

    def _refresh_credit_banner(self) -> None:
        """Show / hide the green credit banner based on open credits."""
        cid = self._customer_id()
        open_total = 0.0
        rows: list[dict] = []
        if cid:
            try:
                from db import list_customer_credits, get_open_credit_total
                open_total = float(get_open_credit_total(cid) or 0)
                rows = [
                    r for r in (list_customer_credits(cid, status="open") or [])
                    if float(r.get("amount") or 0) > 0
                ]
            except Exception:
                open_total = 0.0
                rows = []
        if open_total <= 0 or not rows:
            self._credit_frame.setVisible(False)
            return
        self._credit_frame.setVisible(True)
        n = len(rows)
        self._credit_label.setText(
            f"<b>Customer has "
            f"<span style='color:#047857;'>${open_total:,.2f}</span> "
            f"in open store credit</b> "
            f"({n} credit{'s' if n != 1 else ''} on file) — "
            f"click <i>Apply Credit…</i> to apply against this invoice."
        )

    def _on_apply_credit(self) -> None:
        """Pick an open credit and apply it to the current invoice."""
        from PySide6.QtWidgets import QInputDialog
        cid = self._customer_id()
        if not cid or not self._invoice_id:
            return
        try:
            from db import list_customer_credits, apply_credit_to_invoice
            credits = [
                r for r in (list_customer_credits(cid, status="open") or [])
                if float(r.get("amount") or 0) > 0
            ]
        except Exception as exc:
            QMessageBox.warning(self, "Apply Credit", str(exc))
            return
        if not credits:
            QMessageBox.information(
                self, "Apply Credit",
                "No open credits found for this customer.",
            )
            self._refresh_credit_banner()
            return
        labels = [
            f"CR-{int(c['id'])}  "
            f"${float(c.get('amount') or 0):,.2f}  "
            f"({c.get('source_type') or 'credit'}, "
            f"issued {str(c.get('issue_date') or '')[:10]})"
            for c in credits
        ]
        choice, ok = QInputDialog.getItem(
            self, "Apply Credit",
            f"Apply credit to invoice "
            f"{(self._invoice or {}).get('invoice_number') or self._invoice_id}:",
            labels, 0, False,
        )
        if not ok or not choice:
            return
        credit = credits[labels.index(choice)]
        try:
            apply_credit_to_invoice(int(credit["id"]), int(self._invoice_id))
        except Exception as exc:
            QMessageBox.warning(self, "Apply Failed", str(exc))
            return
        # Reload invoice + balance so the form reflects the new state.
        try:
            inv = get_invoice(self._invoice_id)
            self._invoice = dict(inv) if inv else self._invoice
            new_total = float((self._invoice or {}).get("total") or 0)
            new_paid = float((self._invoice or {}).get("amount_paid") or 0)
            self._current_balance = max(0.0, new_total - new_paid)
            self._amount_spin.setMaximum(max(999999.99, self._current_balance))
            self._amount_spin.setValue(self._current_balance)
        except Exception:
            pass
        self._refresh_credit_banner()
        if self._current_balance <= 0.005:
            QMessageBox.information(
                self, "Invoice Paid",
                "Credit fully covered the balance. Invoice marked paid.",
            )
            self.accept()
        else:
            QMessageBox.information(
                self, "Credit Applied",
                f"Credit applied. Remaining balance: "
                f"${self._current_balance:,.2f}",
            )
