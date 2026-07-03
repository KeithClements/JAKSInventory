"""Pre-send confirmation dialog for the quote's local delivery / fuel surcharge.

Shown automatically when the user prints/sends a quote PDF if:
  * the customer has a ZIP, AND
  * the quote's current ``fuel_surcharge`` is 0.

It also can be opened ad hoc from the Shipping tab via "Recalculate from ZIP".
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QDoubleSpinBox, QRadioButton, QButtonGroup, QPushButton, QFrame,
)

from engine.shipping_fuel import suggest_fuel_surcharge


# Tier table: (label, default_amount, reason_substring_match)
_TIERS = [
    ("Local (same ZIP-3)",          15.0,  "Local"),
    ("<100 mi (adjacent ZIP-3)",    45.0,  "<100"),
    ("<250 mi (regional)",          95.0,  "<250"),
    ("Long-haul (cross-region)",   175.0,  "Long-haul"),
    ("Custom",                       0.0,  "Custom"),
]


class DeliveryFuelDialog(QDialog):
    """Confirm or override the local delivery / fuel surcharge.

    Use ``DeliveryFuelDialog.run(parent, ship_to_zip, current_amount) ->
    (apply: bool, amount: float, reason: str)``.

    Returns ``(False, _, _)`` if the user cancels.
    """

    def __init__(self, parent=None, ship_to_zip: str = "", current_amount: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("Delivery & Fuel Surcharge")
        self.setModal(True)
        self.setMinimumWidth(440)

        self._result_apply = False
        self._result_amount = float(current_amount or 0.0)
        self._result_reason = ""

        suggested_amt, suggested_reason = suggest_fuel_surcharge(ship_to_zip)

        outer = QVBoxLayout(self)

        # Heading.
        heading = QLabel("<b>Confirm local delivery / fuel surcharge</b>")
        heading.setStyleSheet("font-size: 12pt;")
        outer.addWidget(heading)

        sub = QLabel(
            "We'll add this once to the quote total. Based on the customer's "
            "ZIP, we suggest the tier below — accept it, override the amount, "
            "or skip if no surcharge applies."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #555;")
        outer.addWidget(sub)

        # ZIP row.
        form = QFormLayout()
        self._zip_edit = QLineEdit(ship_to_zip or "")
        self._zip_edit.setMaxLength(10)
        self._zip_edit.setPlaceholderText("Customer ZIP (e.g. 80020)")
        self._zip_edit.editingFinished.connect(self._on_zip_changed)
        form.addRow("Ship-to ZIP:", self._zip_edit)

        self._zip_reason_lbl = QLabel(suggested_reason or "Unknown ZIP")
        self._zip_reason_lbl.setStyleSheet("color: #1565c0; font-style: italic;")
        form.addRow("Suggested tier:", self._zip_reason_lbl)
        outer.addLayout(form)

        # Tier radios.
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #ddd;")
        outer.addWidget(line)

        self._tier_group = QButtonGroup(self)
        self._tier_radios: list[QRadioButton] = []
        for i, (label, amount, _) in enumerate(_TIERS):
            rb = QRadioButton(f"{label} — ${amount:,.2f}" if amount > 0 else label)
            self._tier_group.addButton(rb, i)
            self._tier_radios.append(rb)
            outer.addWidget(rb)
        self._tier_group.idToggled.connect(self._on_tier_toggled)

        # Amount spinbox.
        amt_row = QHBoxLayout()
        amt_row.addWidget(QLabel("Amount: $"))
        self._amount_spin = QDoubleSpinBox()
        self._amount_spin.setRange(0.0, 9999.0)
        self._amount_spin.setDecimals(2)
        self._amount_spin.setSingleStep(5.0)
        self._amount_spin.setMaximumWidth(120)
        amt_row.addWidget(self._amount_spin)
        amt_row.addStretch()
        outer.addLayout(amt_row)

        # Pre-select tier from suggestion (or current amount if non-zero).
        initial_idx = self._match_tier_index(suggested_reason)
        if current_amount and current_amount > 0:
            # Pre-fill amount and pick "Custom" if it doesn't match suggestion.
            self._amount_spin.setValue(float(current_amount))
            if abs(current_amount - suggested_amt) < 0.01:
                self._tier_radios[initial_idx].setChecked(True)
            else:
                self._tier_radios[len(_TIERS) - 1].setChecked(True)  # Custom
        else:
            self._tier_radios[initial_idx].setChecked(True)
            self._amount_spin.setValue(_TIERS[initial_idx][1])

        # Buttons.
        btn_row = QHBoxLayout()
        skip_btn = QPushButton("Skip (no surcharge)")
        skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        apply_btn = QPushButton("Apply && Continue")
        apply_btn.setDefault(True)
        apply_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; "
            "font-weight:bold; padding:6px 14px; border-radius:3px; }"
        )
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(apply_btn)
        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    @staticmethod
    def _match_tier_index(reason: str) -> int:
        r = (reason or "").lower()
        for i, (_, _, key) in enumerate(_TIERS):
            if key.lower() in r:
                return i
        return 0  # default Local

    def _on_zip_changed(self) -> None:
        zip_code = self._zip_edit.text().strip()
        amt, reason = suggest_fuel_surcharge(zip_code)
        self._zip_reason_lbl.setText(reason or "Unknown ZIP")
        idx = self._match_tier_index(reason)
        self._tier_radios[idx].setChecked(True)
        self._amount_spin.setValue(amt)

    def _on_tier_toggled(self, idx: int, checked: bool) -> None:
        if not checked:
            return
        # "Custom" leaves the amount as-is for user editing; others snap.
        if idx < len(_TIERS) - 1:
            self._amount_spin.setValue(_TIERS[idx][1])

    def _selected_reason(self) -> str:
        idx = self._tier_group.checkedId()
        if idx < 0:
            return ""
        return _TIERS[idx][0]

    def _on_apply(self) -> None:
        self._result_apply = True
        self._result_amount = float(self._amount_spin.value())
        self._result_reason = self._selected_reason()
        self.accept()

    def _on_skip(self) -> None:
        self._result_apply = True
        self._result_amount = 0.0
        self._result_reason = "Skipped"
        self.accept()

    # ------------------------------------------------------------------
    @classmethod
    def run(cls, parent, ship_to_zip: str = "",
            current_amount: float = 0.0) -> tuple[bool, float, str]:
        dlg = cls(parent, ship_to_zip=ship_to_zip, current_amount=current_amount)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False, float(current_amount or 0.0), ""
        return dlg._result_apply, dlg._result_amount, dlg._result_reason
