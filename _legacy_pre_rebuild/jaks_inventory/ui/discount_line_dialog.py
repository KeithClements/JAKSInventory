"""Shared "Add Discount Line" dialog used by PO, Quote, SO and Invoice editors.

The dialog lets the user enter a discount as either a percentage of the
current document's product subtotal or as a fixed dollar amount, with a
live preview of the resulting negative line total.

Callers should construct with the current document subtotal so the
percent-mode preview is accurate, then read `.result_payload` after the
dialog is accepted to get a dict suitable for ``db.add_discount_line``:

    {
        "description": str,
        "discount_amount": float | None,
        "discount_pct": float | None,
    }
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
)


class DiscountLineDialog(QDialog):
    """Collect a manual discount (% or $) for the current document."""

    def __init__(
        self,
        parent=None,
        *,
        doc_type: str = "po",
        product_subtotal: float = 0.0,
        default_description: str = "Volume discount",
    ) -> None:
        super().__init__(parent)
        self._doc_type = doc_type
        self._product_subtotal = float(product_subtotal or 0)
        self.result_payload: dict | None = None

        self.setWindowTitle("Add Discount Line")
        self.setMinimumWidth(360)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Context label — show the user what subtotal a % would be computed against.
        ctx = QLabel(
            f"Current {self._doc_label()} subtotal: "
            f"<b>${self._product_subtotal:,.2f}</b>"
        )
        ctx.setStyleSheet("color: #444; padding-bottom: 6px;")
        layout.addWidget(ctx)

        form = QFormLayout()

        # Description.
        self._desc_edit = QLineEdit(default_description)
        self._desc_edit.setPlaceholderText("e.g. 5% volume discount")
        form.addRow("Description:", self._desc_edit)

        # Mode selector.
        mode_row = QHBoxLayout()
        self._pct_radio = QRadioButton("Percent (%)")
        self._amt_radio = QRadioButton("Fixed amount ($)")
        self._pct_radio.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._pct_radio, 0)
        self._mode_group.addButton(self._amt_radio, 1)
        mode_row.addWidget(self._pct_radio)
        mode_row.addWidget(self._amt_radio)
        mode_row.addStretch()
        form.addRow("Type:", mode_row)

        # Value spinbox — single widget, label switches with mode.
        self._value_spin = QDoubleSpinBox()
        self._value_spin.setRange(0.0, 1_000_000.0)
        self._value_spin.setDecimals(2)
        self._value_spin.setSingleStep(1.0)
        self._value_spin.setValue(5.0)
        self._value_label = QLabel("Percent:")
        form.addRow(self._value_label, self._value_spin)

        layout.addLayout(form)

        # Live preview.
        self._preview_label = QLabel()
        self._preview_label.setStyleSheet(
            "QLabel { background-color: #fff8e0; border: 1px solid #e0c060; "
            "border-radius: 4px; padding: 8px; font-weight: 600; color: #b00; }"
        )
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._preview_label)

        # Buttons.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Wire updates.
        self._pct_radio.toggled.connect(self._on_mode_changed)
        self._value_spin.valueChanged.connect(self._update_preview)
        self._update_preview()

    # ------------------------------------------------------------------
    def _doc_label(self) -> str:
        return {
            "po": "PO",
            "quote": "quote",
            "so": "sales order",
            "invoice": "invoice",
        }.get(self._doc_type, "document")

    def _on_mode_changed(self, _checked: bool) -> None:
        if self._pct_radio.isChecked():
            self._value_label.setText("Percent:")
            self._value_spin.setSuffix(" %")
            self._value_spin.setMaximum(100.0)
            if self._value_spin.value() == 0:
                self._value_spin.setValue(5.0)
        else:
            self._value_label.setText("Amount:")
            self._value_spin.setSuffix("")
            self._value_spin.setPrefix("$ ")
            self._value_spin.setMaximum(1_000_000.0)
            if self._value_spin.value() == 0:
                self._value_spin.setValue(50.0)
        self._update_preview()

    def _resolved_amount(self) -> float:
        v = float(self._value_spin.value() or 0)
        if self._pct_radio.isChecked():
            return round(self._product_subtotal * (v / 100.0), 2)
        return round(v, 2)

    def _update_preview(self) -> None:
        amount = self._resolved_amount()
        if self._pct_radio.isChecked():
            v = self._value_spin.value()
            self._preview_label.setText(
                f"− ${amount:,.2f}  ({v:.2f}% of ${self._product_subtotal:,.2f})"
            )
        else:
            self._preview_label.setText(f"− ${amount:,.2f}")

    def _on_accept(self) -> None:
        amount = self._resolved_amount()
        if amount <= 0:
            return
        desc = self._desc_edit.text().strip() or "Discount"
        if self._pct_radio.isChecked():
            self.result_payload = {
                "description": desc,
                "discount_amount": None,
                "discount_pct": float(self._value_spin.value()),
            }
        else:
            self.result_payload = {
                "description": desc,
                "discount_amount": amount,
                "discount_pct": None,
            }
        self.accept()
