"""Suggested Sells Popup — shown when a product with configured suggestions
is added to a quote, sales order, or invoice."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import get_suggested_sells

try:
    from db import compute_price as _compute_price
except Exception:  # pragma: no cover - defensive
    _compute_price = None


def _resolve_live_price(suggestion: dict) -> float:
    """Return the live gross-margin engine price for a suggested product.

    Falls back to the stored ``selling_price`` if the engine returns
    nothing usable. Cached on the suggestion dict so each row only
    queries once.
    """
    if "_live_price" in suggestion:
        return float(suggestion["_live_price"])
    fallback = float(suggestion.get("selling_price") or 0)
    price = fallback
    pid = suggestion.get("suggested_product_id")
    if _compute_price is not None and pid:
        try:
            res = _compute_price(int(pid))
            lp = float((res or {}).get("list_price") or 0)
            if lp > 0:
                price = lp
        except Exception:
            price = fallback
    suggestion["_live_price"] = price
    return price


class SuggestedSellsPopup(QDialog):
    """Modal dialog that shows suggested products and lets the user pick
    which ones to add as extra line items.

    Usage::

        dlg = SuggestedSellsPopup(product_id, parent=self)
        if dlg.has_suggestions and dlg.exec() == QDialog.DialogCode.Accepted:
            for item in dlg.selected_items():
                # item = {"product_id": int, "sku": str, "title": str,
                #         "selling_price": float, "qty": int}
                ...
    """

    def __init__(self, product_id: int, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggested Add-Ons")
        self.setMinimumWidth(700)

        self._suggestions = get_suggested_sells(product_id)
        self._checkboxes: list[QCheckBox] = []
        self._qty_spins: list[QSpinBox] = []

        layout = QVBoxLayout(self)

        if not self._suggestions:
            layout.addWidget(QLabel("No suggestions configured."))
            self.has_suggestions = False
            return

        self.has_suggestions = True

        header = QLabel(
            "<b>These products are commonly purchased together.</b><br>"
            "<span style='color:#666;'>Check the ones you'd like to add:</span>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        # ── Table ──
        self._table = QTableWidget(len(self._suggestions), 6)
        self._table.setHorizontalHeaderLabels(
            ["", "SKU", "Description", "Type", "Price", "Qty"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #ddd; }"
            "QTableWidget::item { padding: 4px; }"
            "QTableWidget::item:alternate { background-color: #f9f9f5; }"
        )

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, 36)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(1, 120)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(3, 90)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(4, 90)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(5, 60)

        for row, s in enumerate(self._suggestions):
            rel_type = (s.get("relationship_type") or "optional").lower()

            # ── Column 0: Checkbox ──
            cb = QCheckBox()
            if rel_type == "required":
                cb.setChecked(True)
            cb.toggled.connect(self._update_total)
            self._checkboxes.append(cb)

            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            cb_layout.addWidget(cb)
            self._table.setCellWidget(row, 0, cb_widget)

            # ── Column 1: SKU ──
            self._table.setItem(row, 1, QTableWidgetItem(s.get("sku", "")))

            # ── Column 2: Description ──
            self._table.setItem(row, 2, QTableWidgetItem(s.get("title", "")))

            # ── Column 3: Type ──
            type_item = QTableWidgetItem(rel_type.title())
            type_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            if rel_type == "required":
                type_item.setForeground(QColor("#c62828"))
                type_item.setFont(QFont("", -1, QFont.Weight.Bold))
            elif rel_type == "recommended":
                type_item.setForeground(QColor("#1565c0"))
            else:
                type_item.setForeground(QColor("#666"))
            self._table.setItem(row, 3, type_item)

            # ── Column 4: Price ──
            price = _resolve_live_price(s)
            price_item = QTableWidgetItem(f"${price:,.2f}")
            price_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, 4, price_item)

            # ── Column 5: Qty ──
            qty_spin = QSpinBox()
            qty_spin.setMinimum(1)
            qty_spin.setMaximum(999)
            qty_spin.setValue(1)
            qty_spin.setFixedWidth(54)
            qty_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qty_spin.valueChanged.connect(self._update_total)
            self._qty_spins.append(qty_spin)

            qty_widget = QWidget()
            qty_lay = QHBoxLayout(qty_widget)
            qty_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            qty_lay.setContentsMargins(2, 0, 2, 0)
            qty_lay.addWidget(qty_spin)
            self._table.setCellWidget(row, 5, qty_widget)

        self._table.setMinimumHeight(min(42 * len(self._suggestions) + 34, 300))
        layout.addWidget(self._table)

        # ── Total bar ──
        total_row = QHBoxLayout()
        total_row.addStretch()
        total_label = QLabel("<b>Selected Total:</b>")
        total_row.addWidget(total_label)
        self._total_value = QLabel("$0.00")
        self._total_value.setStyleSheet(
            "font-size: 12pt; font-weight: bold; color: #4a6741;"
        )
        total_row.addWidget(self._total_value)
        layout.addLayout(total_row)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        add_btn = QPushButton("Add Selected")
        add_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1976d2; }"
        )
        add_btn.clicked.connect(self.accept)
        btn_row.addWidget(add_btn)

        skip_btn = QPushButton("Skip")
        skip_btn.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(skip_btn)
        layout.addLayout(btn_row)

        self._update_total()

    # ── Helpers ──

    def _update_total(self) -> None:
        total = 0.0
        for i, s in enumerate(self._suggestions):
            if self._checkboxes[i].isChecked():
                price = _resolve_live_price(s)
                qty = self._qty_spins[i].value()
                total += price * qty
        self._total_value.setText(f"${total:,.2f}")

    def selected_items(self) -> list[dict]:
        """Return list of dicts for checked suggestions."""
        result = []
        for i, s in enumerate(self._suggestions):
            if self._checkboxes[i].isChecked():
                result.append({
                    "product_id": s["suggested_product_id"],
                    "sku": s.get("sku", ""),
                    "title": s.get("title", ""),
                    "selling_price": _resolve_live_price(s),
                    "cost": float(s.get("cost") or 0),
                    "core_charge": float(s.get("core_charge") or 0),
                    "qty": self._qty_spins[i].value(),
                })
        return result
