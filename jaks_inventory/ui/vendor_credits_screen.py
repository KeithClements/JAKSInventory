"""Vendor Credits screen (Phase H.5).

Simple two-tab ledger over `vendor_core_obligations` rows where
`status='credit_received'`. Tab 1 = per-credit ledger, Tab 2 = per-vendor totals.
"""
from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db.database import get_db

log = logging.getLogger(__name__)


_LEDGER_COLUMNS = [
    "Credit Date", "Vendor", "RGA #", "Vendor Credit #", "Part #",
    "SKU", "Deposit", "Credit $", "Variance",
]
_TOTAL_COLUMNS = ["Vendor", "# Credits", "Total Credit $", "Last Credit Date"]


class _NumItem(QTableWidgetItem):
    def __init__(self, value: float, fmt: str = "$"):
        super().__init__()
        self._v = value or 0.0
        if fmt == "$":
            self.setText(f"${self._v:,.2f}")
            self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            self.setText(str(int(self._v)))
            self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._v < other._v
        return super().__lt__(other)


class _LedgerTab(QWidget):
    """Tab 1 — Credits Ledger."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_rows: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search credit # / RGA # / vendor …")
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._filter)
        header.addWidget(self._search)
        header.addStretch()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        header.addWidget(self._count_label)
        export_btn = QPushButton("⬇ Export CSV")
        export_btn.clicked.connect(self._on_export)
        header.addWidget(export_btn)
        layout.addLayout(header)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_LEDGER_COLUMNS))
        self._table.setHorizontalHeaderLabels(_LEDGER_COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

    def refresh(self) -> None:
        sql = """
            SELECT
                vco.id,
                vco.credit_date,
                vco.vendor_id,
                v.name              AS vendor_name,
                vr.rga_number       AS rga_number,
                vco.vendor_credit_number,
                COALESCE(p.oem_part_number, p.xref_part_number, '') AS part_number,
                COALESCE(p.sku, '') AS sku,
                vco.core_amount,
                COALESCE(vco.quantity, 1) AS quantity,
                COALESCE(vco.credit_received, 0) AS credit_amount
            FROM vendor_core_obligations vco
            LEFT JOIN vendors v ON v.id = vco.vendor_id
            LEFT JOIN vendor_returns vr ON vr.id = vco.rga_id
            LEFT JOIN products p ON p.id = vco.product_id
            WHERE vco.status = 'credit_received'
            ORDER BY vco.credit_date DESC, vco.id DESC
            LIMIT 1000
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            self._all_rows = [
                dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows
            ]
        except Exception:
            log.exception("Vendor Credits ledger fetch failed")
            self._all_rows = []
        self._populate(self._all_rows)

    def _populate(self, rows: list[dict[str, Any]]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            deposit = float(r.get("core_amount") or 0) * float(r.get("quantity") or 1)
            credit = float(r.get("credit_amount") or 0)
            variance = credit - deposit
            self._table.setItem(i, 0, QTableWidgetItem(str(r.get("credit_date") or "")))
            self._table.setItem(i, 1, QTableWidgetItem(str(r.get("vendor_name") or "")))
            self._table.setItem(i, 2, QTableWidgetItem(str(r.get("rga_number") or "")))
            self._table.setItem(i, 3, QTableWidgetItem(str(r.get("vendor_credit_number") or "")))
            self._table.setItem(i, 4, QTableWidgetItem(str(r.get("part_number") or "")))
            self._table.setItem(i, 5, QTableWidgetItem(str(r.get("sku") or "")))
            self._table.setItem(i, 6, _NumItem(deposit))
            self._table.setItem(i, 7, _NumItem(credit))
            v_item = _NumItem(variance)
            if abs(variance) >= 0.01:
                v_item.setForeground(Qt.GlobalColor.darkRed if variance < 0 else Qt.GlobalColor.darkGreen)
            self._table.setItem(i, 8, v_item)
        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(rows)} credit(s)")

    def _filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        if not q:
            self._populate(self._all_rows)
            return
        filtered = [
            r for r in self._all_rows
            if q in (str(r.get("vendor_credit_number") or "")).lower()
            or q in (str(r.get("rga_number") or "")).lower()
            or q in (str(r.get("vendor_name") or "")).lower()
        ]
        self._populate(filtered)

    def _on_export(self) -> None:
        if not self._all_rows:
            QMessageBox.information(self, "Export", "No credits to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Vendor Credits", "vendor_credits.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(_LEDGER_COLUMNS)
                for r in self._all_rows:
                    deposit = float(r.get("core_amount") or 0) * float(r.get("quantity") or 1)
                    credit = float(r.get("credit_amount") or 0)
                    w.writerow([
                        r.get("credit_date") or "",
                        r.get("vendor_name") or "",
                        r.get("rga_number") or "",
                        r.get("vendor_credit_number") or "",
                        r.get("part_number") or "",
                        r.get("sku") or "",
                        f"{deposit:.2f}",
                        f"{credit:.2f}",
                        f"{credit - deposit:.2f}",
                    ])
            QMessageBox.information(self, "Export", f"Exported to {Path(path).name}")
        except Exception as exc:
            log.exception("Export failed")
            QMessageBox.critical(self, "Export", f"Export failed:\n{exc}")


class _TotalsTab(QWidget):
    """Tab 2 — Per-Vendor Totals."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        layout.addWidget(self._count_label)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_TOTAL_COLUMNS))
        self._table.setHorizontalHeaderLabels(_TOTAL_COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

    def refresh(self) -> None:
        ytd_cutoff = date(date.today().year, 1, 1).isoformat()
        sql = """
            SELECT
                v.name AS vendor_name,
                COUNT(vco.id) AS n_credits,
                COALESCE(SUM(vco.credit_received), 0) AS total_credit,
                MAX(vco.credit_date) AS last_date
            FROM vendor_core_obligations vco
            LEFT JOIN vendors v ON v.id = vco.vendor_id
            WHERE vco.status = 'credit_received'
              AND COALESCE(vco.credit_date, '0000') >= ?
            GROUP BY v.id
            ORDER BY total_credit DESC
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql, (ytd_cutoff,)).fetchall()
            rows = [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]
        except Exception:
            log.exception("Vendor Credits totals fetch failed")
            rows = []
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(r.get("vendor_name") or "")))
            self._table.setItem(i, 1, _NumItem(float(r.get("n_credits") or 0), fmt="int"))
            self._table.setItem(i, 2, _NumItem(float(r.get("total_credit") or 0)))
            self._table.setItem(i, 3, QTableWidgetItem(str(r.get("last_date") or "")))
        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(rows)} vendor(s) YTD")


class VendorCreditsScreen(QTabWidget):
    """Two-tab vendor credit ledger."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ledger = _LedgerTab(self)
        self._totals = _TotalsTab(self)
        self.addTab(self._ledger, "Credits Ledger")
        self.addTab(self._totals, "Per-Vendor Totals (YTD)")
        self.setStyleSheet(
            "QTabBar::tab { padding: 8px 16px; font-size: 12px; }"
            "QTabBar::tab:selected { background: #6B8E23; color: white; font-weight: 600; }"
        )

    def showEvent(self, event):  # noqa: D401
        super().showEvent(event)
        try:
            self._ledger.refresh()
            self._totals.refresh()
        except Exception:
            log.exception("Vendor Credits refresh failed")
