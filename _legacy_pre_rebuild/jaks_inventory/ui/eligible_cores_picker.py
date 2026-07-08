"""Eligible Vendor Cores Picker (Vendor Returns / Vendor Cores RGA Phase 3).

A picker dialog showing pending vendor core obligations that are eligible
to be added to a Return Goods Authorization (RGA). Multi-select returns the
list of vendor_core_returns ids selected.

Columns
-------
Vendor | Product | SKU | Customer Core Source | Invoice # | PO # |
Vendor Core Deposit | Customer Core Charge | Date Received From Customer |
Days Since Received | Status

NOTE: This dialog only proposes vendor-side cores. It does NOT touch the
customer-side core refund workflow (which lives on the invoice / customer
core screens).
"""
from __future__ import annotations

from datetime import datetime, date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from db import list_eligible_vendor_cores_for_rga


_COLS = [
    "Vendor", "Product", "SKU", "Cust Core ID", "Invoice #",
    "PO #", "Vendor Deposit", "Cust Core $",
    "Cust Received", "Days", "Status",
]


def _to_date(val):
    if not val:
        return None
    if isinstance(val, (date, datetime)):
        return val.date() if isinstance(val, datetime) else val
    s = str(val)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


class EligibleCoresPickerDialog(QDialog):
    """Multi-select picker for pending vendor cores eligible for an RGA."""

    def __init__(self, parent=None, vendor_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Vendor Cores to RGA")
        self.resize(1100, 520)
        self._all_rows: list[dict] = []
        self._initial_vendor_id = vendor_id

        root = QVBoxLayout(self)

        # Filter row
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("SKU / product / vendor / invoice / PO")
        self._search.textChanged.connect(self._apply_filter)
        filt.addWidget(self._search)
        filt.addWidget(QLabel("Vendor:"))
        self._vendor_filter = QComboBox()
        self._vendor_filter.addItem("All", None)
        self._vendor_filter.currentIndexChanged.connect(self._apply_filter)
        filt.addWidget(self._vendor_filter)
        filt.addStretch()
        root.addLayout(filt)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self._table)

        self._summary = QLabel("0 cores selected · $0.00 expected credit")
        self._summary.setStyleSheet("padding: 4px; color: #555;")
        root.addWidget(self._summary)
        self._table.itemSelectionChanged.connect(self._update_summary)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Add Selected")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._load()

    # ── Loading ──────────────────────────────────────────────────────
    def _load(self):
        try:
            rows = list_eligible_vendor_cores_for_rga(
                vendor_id=self._initial_vendor_id
            )
        except Exception:
            rows = []
        self._all_rows = list(rows)

        # Build vendor dropdown.
        seen_vendors = {}
        for r in self._all_rows:
            vid = r.get("vendor_id")
            if vid and vid not in seen_vendors:
                seen_vendors[vid] = r.get("vendor_name") or f"Vendor {vid}"
        self._vendor_filter.blockSignals(True)
        self._vendor_filter.clear()
        self._vendor_filter.addItem("All", None)
        for vid, vname in sorted(seen_vendors.items(), key=lambda kv: kv[1] or ""):
            self._vendor_filter.addItem(vname, vid)
        if self._initial_vendor_id:
            for i in range(self._vendor_filter.count()):
                if self._vendor_filter.itemData(i) == self._initial_vendor_id:
                    self._vendor_filter.setCurrentIndex(i)
                    break
        self._vendor_filter.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self):
        needle = (self._search.text() or "").strip().lower()
        vendor_filter = self._vendor_filter.currentData()
        rows = []
        for r in self._all_rows:
            if vendor_filter and r.get("vendor_id") != vendor_filter:
                continue
            if needle:
                haystack = " ".join(str(r.get(k) or "") for k in (
                    "sku", "product_title", "vendor_name", "po_number",
                    "source_invoice_number", "customer_core_id_join",
                )).lower()
                if needle not in haystack:
                    continue
            rows.append(r)
        self._render(rows)

    def _render(self, rows: list[dict]):
        self._table.setRowCount(len(rows))
        today = date.today()
        for i, r in enumerate(rows):
            qty = int(r.get("qty") or 1)
            deposit = float(r.get("core_charge") or 0)
            cust_charge = float(r.get("customer_core_charge") or 0)
            recv = _to_date(r.get("customer_received_at"))
            days = (today - recv).days if recv else ""
            values = [
                r.get("vendor_name") or "",
                r.get("product_title") or "",
                r.get("sku") or "",
                str(r.get("customer_core_id_join") or ""),
                r.get("source_invoice_number") or "",
                r.get("po_number") or "",
                f"${deposit * qty:,.2f}",
                f"${cust_charge:,.2f}",
                str(recv) if recv else "",
                str(days) if days != "" else "",
                r.get("status") or "",
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(i, c, item)
            # Stash vcr id + expected credit on the first cell.
            first = self._table.item(i, 0)
            if first is not None:
                first.setData(Qt.ItemDataRole.UserRole, {
                    "id": r.get("id"),
                    "expected_credit": deposit * qty,
                })
        self._update_summary()

    def _update_summary(self):
        sel_ids = self.selected_vcr_ids()
        total = 0.0
        for r in self._table.selectionModel().selectedRows():
            it = self._table.item(r.row(), 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data:
                total += float(data.get("expected_credit") or 0)
        self._summary.setText(
            f"{len(sel_ids)} cores selected · ${total:,.2f} expected credit"
        )

    # ── Public ───────────────────────────────────────────────────────
    def selected_vcr_ids(self) -> list[int]:
        ids: list[int] = []
        seen = set()
        for idx in self._table.selectionModel().selectedRows():
            it = self._table.item(idx.row(), 0)
            data = it.data(Qt.ItemDataRole.UserRole) if it else None
            if data and data.get("id") and data["id"] not in seen:
                seen.add(data["id"])
                ids.append(int(data["id"]))
        return ids
