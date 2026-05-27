"""Inventory Adjustments screen — central hub for quantity changes.

Shows recent inventory transactions and provides single-product
and bulk adjustment tools.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    browse_products,
    get_inventory_transactions,
)

from .adjust_qty_dialog import AdjustQtyDialog
from .bulk_adjust_dialog import BulkAdjustDialog


class AdjustmentsScreen(QWidget):
    """Screen for managing inventory quantity adjustments."""

    _TXN_COLS = [
        ("Date", 150),
        ("SKU", 120),
        ("Title", 220),
        ("Type", 90),
        ("Change", 80),
        ("Before", 70),
        ("After", 70),
        ("Reference", 120),
        ("Notes", 200),
        ("User", 80),
    ]

    _TYPE_COLORS = {
        "receive": "#2e7d32",
        "sale": "#c62828",
        "adjust": "#1565c0",
        "return": "#6a1b9a",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_transactions()

    # ------------------------------------------------------------------
    #  UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Quick adjust bar ──────────────────────────────────
        quick_group = QGroupBox("Quick Adjust")
        quick_layout = QHBoxLayout()

        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Enter SKU…")
        self._sku_input.setMinimumWidth(180)
        self._sku_input.returnPressed.connect(self._on_quick_adjust)
        quick_layout.addWidget(QLabel("SKU:"))
        quick_layout.addWidget(self._sku_input)

        adjust_btn = QPushButton("⚖️ Adjust Qty")
        adjust_btn.clicked.connect(self._on_quick_adjust)
        quick_layout.addWidget(adjust_btn)

        quick_layout.addStretch()

        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # ── Filters ───────────────────────────────────────────
        filter_row = QHBoxLayout()

        self._type_filter = QComboBox()
        self._type_filter.addItem("All Types", None)
        self._type_filter.addItem("📥 Receive", "receive")
        self._type_filter.addItem("📤 Sale", "sale")
        self._type_filter.addItem("📊 Adjust", "adjust")
        self._type_filter.addItem("↩️ Return", "return")
        self._type_filter.currentIndexChanged.connect(self._load_transactions)
        filter_row.addWidget(QLabel("Type:"))
        filter_row.addWidget(self._type_filter)

        self._sku_filter = QLineEdit()
        self._sku_filter.setPlaceholderText("Filter by SKU…")
        self._sku_filter.setMaximumWidth(200)
        self._sku_filter.textChanged.connect(self._load_transactions)
        filter_row.addWidget(QLabel("SKU:"))
        filter_row.addWidget(self._sku_filter)

        filter_row.addStretch()

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._load_transactions)
        filter_row.addWidget(refresh_btn)

        layout.addLayout(filter_row)

        # ── Transactions table ────────────────────────────────
        self._table = QTableWidget()
        self._table.setColumnCount(len(self._TXN_COLS))
        self._table.setHorizontalHeaderLabels([c[0] for c in self._TXN_COLS])
        for i, (_, w) in enumerate(self._TXN_COLS):
            self._table.setColumnWidth(i, w)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, stretch=1)

        # ── Status bar ────────────────────────────────────────
        self._status = QLabel("Ready")
        layout.addWidget(self._status)

    # ------------------------------------------------------------------
    #  Data loading
    # ------------------------------------------------------------------
    def _load_transactions(self) -> None:
        """Load and display inventory transactions."""
        try:
            rows = get_inventory_transactions(limit=500)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        # Apply local filters
        type_filter = self._type_filter.currentData()
        sku_filter = self._sku_filter.text().strip().upper()

        filtered = []
        for r in rows:
            if type_filter and r.get("transaction_type") != type_filter:
                continue
            if sku_filter and sku_filter not in (r.get("sku") or "").upper():
                continue
            filtered.append(r)

        self._table.setRowCount(len(filtered))

        for row_idx, txn in enumerate(filtered):
            txn_type = txn.get("transaction_type", "")
            change = txn.get("quantity_change", 0) or 0
            color = QColor(self._TYPE_COLORS.get(txn_type, "#333"))

            vals = [
                (txn.get("created_at") or "")[:19],
                txn.get("sku", ""),
                (txn.get("title") or "")[:40],
                txn_type,
                f"{'+' if change > 0 else ''}{change}",
                str(txn.get("quantity_before", "")),
                str(txn.get("quantity_after", "")),
                txn.get("reference") or "",
                txn.get("notes") or "",
                txn.get("created_by") or "",
            ]

            for col_idx, val in enumerate(vals):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 3:  # Type column
                    item.setForeground(color)
                if col_idx == 4:  # Change column
                    item.setForeground(QColor("#2e7d32") if change > 0 else QColor("#c62828"))
                self._table.setItem(row_idx, col_idx, item)

        self._status.setText(f"{len(filtered)} transactions shown")

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------
    def _on_quick_adjust(self) -> None:
        """Open single-product adjustment dialog."""
        sku = self._sku_input.text().strip()
        if not sku:
            QMessageBox.information(self, "SKU Required", "Enter a SKU to adjust.")
            return

        dlg = AdjustQtyDialog(sku, parent=self)
        if dlg.exec():
            self._sku_input.clear()
            self._load_transactions()
