"""Customer return / RMA dialog launched from a finalized invoice.

Lets the operator pick which lines (and how many of each) to return,
choose a reason and condition, then writes the changes back to the
invoice and stock via :func:`db.process_invoice_return`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from db import get_invoice, process_invoice_return


_RETURN_REASONS = [
    "Customer changed mind",
    "Wrong part ordered",
    "Defective / DOA",
    "Damaged in transit",
    "Doesn't fit",
    "Warranty claim",
    "Other",
]

_CONDITIONS = [
    "new",
    "used-good",
    "used-damaged",
    "scrap",
]


class InvoiceReturnDialog(QDialog):
    """Pick lines to return from a finalized invoice."""

    def __init__(self, invoice_id: int, parent=None) -> None:
        super().__init__(parent)
        self._invoice_id = invoice_id
        self._invoice = get_invoice(invoice_id) or {}

        self.setWindowTitle(f"Return — Invoice {self._invoice.get('invoice_number', '')}")
        self.resize(820, 480)

        self._build_ui()
        self._populate_lines()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        header = QLabel(
            f"<b>Invoice {self._invoice.get('invoice_number', '')}</b> · "
            f"Status: {(self._invoice.get('status') or '').upper()} · "
            f"Total: ${float(self._invoice.get('total') or 0):.2f}"
        )
        lay.addWidget(header)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["✓", "SKU", "Description", "Sold", "Already Returned", "Return Qty"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._table, stretch=1)

        form = QFormLayout()
        self._reason_combo = QComboBox()
        self._reason_combo.addItems(_RETURN_REASONS)
        self._reason_combo.setEditable(True)
        form.addRow("Reason:", self._reason_combo)

        self._condition_combo = QComboBox()
        self._condition_combo.addItems([""] + _CONDITIONS)
        form.addRow("Condition:", self._condition_combo)

        self._restock_check = QCheckBox("Restock to inventory (uncheck for scrap)")
        self._restock_check.setChecked(True)
        form.addRow("", self._restock_check)

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("Optional notes")
        form.addRow("Notes:", self._notes_edit)
        lay.addLayout(form)

        self._summary = QLabel("Refund total: $0.00")
        self._summary.setStyleSheet("font-weight: 600; padding: 4px;")
        lay.addWidget(self._summary)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Process Return")
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)

        self._condition_combo.currentTextChanged.connect(self._on_condition_changed)

    # ------------------------------------------------------------------
    def _on_condition_changed(self, value: str) -> None:
        self._restock_check.setChecked(value not in ("used-damaged", "scrap"))

    # ------------------------------------------------------------------
    def _populate_lines(self) -> None:
        lines = self._invoice.get("lines", []) or []

        # Index already-returned qty per source line via RTN- adjustments.
        already_returned: dict[int, int] = {}
        for ln in lines:
            sku = (ln.get("sku") or "").upper()
            notes = ln.get("notes") or ""
            if sku.startswith("RTN-") and notes.startswith("return_of:"):
                try:
                    src = int(notes.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                already_returned[src] = already_returned.get(src, 0) + abs(int(ln.get("quantity") or 0))

        # Filter to original lines only (skip RTN-/CORE-).
        original_lines = []
        for ln in lines:
            sku = (ln.get("sku") or "").upper()
            if sku.startswith("RTN-") or sku.startswith("CORE-"):
                continue
            original_lines.append(ln)

        self._table.setRowCount(len(original_lines))
        self._line_meta: list[dict] = []
        for row, ln in enumerate(original_lines):
            line_id = int(ln.get("id"))
            sold = int(ln.get("quantity") or 0)
            done = already_returned.get(line_id, 0)
            remaining = max(0, sold - done)

            chk = QCheckBox()
            chk.setEnabled(remaining > 0)
            chk.stateChanged.connect(self._update_summary)
            self._table.setCellWidget(row, 0, chk)

            self._table.setItem(row, 1, QTableWidgetItem(ln.get("sku") or ""))
            self._table.setItem(row, 2, QTableWidgetItem(ln.get("description") or ""))
            self._table.setItem(row, 3, QTableWidgetItem(str(sold)))
            self._table.setItem(row, 4, QTableWidgetItem(str(done)))

            spin = QSpinBox()
            spin.setRange(0, remaining)
            spin.setValue(remaining)
            spin.setEnabled(remaining > 0)
            spin.valueChanged.connect(self._update_summary)
            self._table.setCellWidget(row, 5, spin)

            self._line_meta.append({
                "line_id": line_id,
                "unit_price": float(ln.get("unit_price") or 0),
                "remaining": remaining,
                "checkbox": chk,
                "spinbox": spin,
            })

        self._update_summary()

    # ------------------------------------------------------------------
    def _selected_items(self) -> list[dict]:
        items = []
        for meta in self._line_meta:
            if not meta["checkbox"].isChecked():
                continue
            qty = meta["spinbox"].value()
            if qty <= 0:
                continue
            items.append({"line_id": meta["line_id"], "qty": qty,
                          "unit_price": meta["unit_price"]})
        return items

    def _update_summary(self) -> None:
        items = self._selected_items()
        total = sum(it["qty"] * it["unit_price"] for it in items)
        self._summary.setText(
            f"Refund total: ${total:.2f}  ·  {len(items)} line(s) selected"
        )

    # ------------------------------------------------------------------
    def _on_accept(self) -> None:
        items = self._selected_items()
        if not items:
            QMessageBox.warning(self, "Nothing Selected", "Pick at least one line to return.")
            return

        reason = self._reason_combo.currentText().strip() or None
        condition = self._condition_combo.currentText().strip() or None
        restock = self._restock_check.isChecked()
        notes_extra = self._notes_edit.text().strip()
        if notes_extra:
            reason = f"{reason}: {notes_extra}" if reason else notes_extra

        result = process_invoice_return(
            invoice_id=self._invoice_id,
            items=[{"line_id": it["line_id"], "qty": it["qty"]} for it in items],
            reason=reason,
            restock=restock,
            condition=condition,
        )
        if not result.get("success"):
            QMessageBox.critical(self, "Return Failed", result.get("error") or "Unknown error.")
            return

        rmas = result.get("rmas", [])
        rma_lines = "\n".join(f"  • {r['rma_number']} — {r['sku']} x{r['qty']} (${r['refund_amount']:.2f})"
                              for r in rmas)
        QMessageBox.information(
            self, "Return Processed",
            f"Refund total: ${result.get('refund_total', 0):.2f}\n\n"
            f"RMAs created:\n{rma_lines or '  (none)'}",
        )
        self.accept()
