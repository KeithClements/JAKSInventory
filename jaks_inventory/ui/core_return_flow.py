"""Shared core-return processing flow.

Used by the Invoices screen (toolbar button + right-click) and the
Invoice dialog so users can run the full Receive → Issue Credit
workflow from anywhere an invoice is visible — without first
navigating to the Cores screen.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from db import get_core_returns_for_invoice, get_invoice
from db.inventory import (
    get_core_return,
    issue_core_credit,
    receive_core,
)

from .core_dialog import IssueCreditDialog, ReceiveCoreDialog


_STATUS_LABELS = {
    "pending": "⏳ Pending",
    "received": "📦 Received",
    "credited": "✅ Credited",
    "voided": "❌ Voided",
}


class _CorePickerDialog(QDialog):
    """Pick one core return row from a list of cores tied to an invoice."""

    def __init__(self, cores: list[dict], invoice_number: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Process Core Return — {invoice_number}")
        self.setMinimumWidth(540)
        self.setMinimumHeight(360)
        self._cores = cores
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Select the core to process. Pending cores can be received; "
            "received cores can have credit issued."
        ))

        self._list = QListWidget()
        for core in self._cores:
            status = str(core.get("status") or "pending").lower()
            sku = core.get("sku") or ""
            title = (core.get("product_title") or "")[:40]
            customer_paid = float(core.get("customer_core_price") or 0)
            if customer_paid <= 0:
                customer_paid = float(core.get("core_charge") or 0)
            label = (
                f"  #{core.get('id')}   {_STATUS_LABELS.get(status, status.title())}   "
                f"{sku}  {title}   —   ${customer_paid:,.2f}"
            )
            item = QListWidgetItem(label)
            item.setData(0x0100 + 1, core.get("id"))  # Qt.UserRole
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        layout.addWidget(self._list, 1)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Process Selected")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def selected_core_id(self) -> int | None:
        item = self._list.currentItem()
        if not item:
            return None
        return item.data(0x0100 + 1)


def process_core_return_for_invoice(
    invoice_id: int,
    parent: QWidget | None = None,
    invoice_line_id: int | None = None,
) -> bool:
    """Open the Receive / Issue Credit flow for a core tied to ``invoice_id``.

    When ``invoice_line_id`` is provided the picker is suppressed and only
    cores tied to that specific line are considered. This is what the
    "click the green Core charge cell" affordance on the invoice line
    table uses so the user is taken straight into that line's workflow.

    Returns ``True`` when the user completed at least one state change.
    """
    if not invoice_id:
        QMessageBox.warning(parent, "No Invoice", "No invoice selected.")
        return False

    try:
        cores = get_core_returns_for_invoice(int(invoice_id)) or []
    except Exception as exc:
        QMessageBox.critical(parent, "Error", f"Failed to load core returns:\n{exc}")
        return False

    if invoice_line_id is not None:
        cores = [c for c in cores if int(c.get("invoice_line_id") or 0) == int(invoice_line_id)]

    if not cores:
        QMessageBox.information(
            parent,
            "No Cores",
            "This invoice has no core returns linked to it.",
        )
        return False

    inv = get_invoice(int(invoice_id)) or {}
    inv_num = inv.get("invoice_number") or f"#{invoice_id}"
    customer_name = inv.get("customer_company") or inv.get("customer_name") or ""

    # Pick a core. If only one is actionable, skip the picker.
    actionable = [c for c in cores if str(c.get("status") or "").lower() in ("pending", "received")]
    if not actionable:
        QMessageBox.information(
            parent,
            "Nothing To Process",
            "All cores on this invoice are already credited or voided.",
        )
        return False

    if len(cores) == 1:
        core_id = cores[0].get("id")
    else:
        picker = _CorePickerDialog(cores, inv_num, parent)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return False
        core_id = picker.selected_core_id()
        if not core_id:
            return False

    return _run_core_workflow(int(core_id), customer_name, parent)


def _run_core_workflow(core_id: int, customer_name: str, parent: QWidget | None) -> bool:
    """Drive a single core through Receive → Issue Credit as appropriate."""
    core = get_core_return(core_id)
    if not core:
        QMessageBox.warning(parent, "Not Found", f"Core return #{core_id} not found.")
        return False

    # Annotate display fields the dialogs expect.
    core.setdefault("customer_name", customer_name or core.get("customer_name") or "")

    status = str(core.get("status") or "").lower()
    changed = False

    if status == "pending":
        recv_dlg = ReceiveCoreDialog(core, parent)
        if recv_dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        data = recv_dlg.get_data()
        try:
            receive_core(
                core_id=core_id,
                condition=data.get("condition", "good"),
                serial_number=data.get("serial_number"),
                notes=data.get("notes"),
            )
            changed = True
        except Exception as exc:
            QMessageBox.critical(parent, "Receive Failed", str(exc))
            return False

        # Re-load with updated status so the credit dialog has condition info.
        core = get_core_return(core_id) or core
        core.setdefault("customer_name", customer_name or "")
        status = str(core.get("status") or "").lower()

        ask = QMessageBox.question(
            parent,
            "Issue Credit Now?",
            "Core marked as received.\n\nIssue credit to the customer now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ask != QMessageBox.StandardButton.Yes:
            return changed

    if status == "received":
        credit_dlg = IssueCreditDialog(core, parent)
        if credit_dlg.exec() != QDialog.DialogCode.Accepted:
            return changed
        data = credit_dlg.get_data()
        try:
            issue_core_credit(
                core_id=core_id,
                credit_amount=float(data.get("credit_amount") or 0),
                credit_invoice_id=data.get("credit_invoice_id"),
                notes=data.get("notes"),
            )
            QMessageBox.information(
                parent,
                "Credit Issued",
                f"Credit of ${float(data.get('credit_amount') or 0):,.2f} issued for this core.",
            )
            changed = True
        except Exception as exc:
            QMessageBox.critical(parent, "Credit Failed", str(exc))
            return changed
    elif status in ("credited", "voided"):
        QMessageBox.information(
            parent,
            "Already Processed",
            f"This core is already {status}.",
        )

    return changed
