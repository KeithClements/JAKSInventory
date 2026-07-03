"""RGA Shipments screen (Phase H.4).

Surfaces only vendor returns with `return_type='vendor_core'`, grouped by RGA.
Selecting a row reveals the attached vendor-core obligations and exposes
toolbar actions (Pack & Ship, Mark Credit Received, Print).
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.database import get_db

log = logging.getLogger(__name__)


_RGA_COLUMNS = ["RGA #", "Vendor", "# Cores", "Total $", "Status", "Tracking", "Ship Date"]
_LINE_COLUMNS = ["VCO ID", "SKU", "Part #", "Deposit $", "Status", "Customer"]


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


class RGAShipmentsScreen(QWidget):
    """Grouped-by-RGA view of vendor-core shipments."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._refresh()

    def showEvent(self, event):  # noqa: D401
        super().showEvent(event)
        try:
            self._refresh()
        except Exception:
            log.exception("RGA Shipments refresh failed")

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("🚚 RGA Shipments — Vendor Cores")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()
        self._refresh_btn = QPushButton("🔄 Refresh")
        self._refresh_btn.clicked.connect(self._refresh)
        header.addWidget(self._refresh_btn)
        layout.addLayout(header)

        # Toolbar
        toolbar = QHBoxLayout()
        self._pack_btn = QPushButton("📦 Pack && Ship")
        self._pack_btn.clicked.connect(self._on_pack_and_ship)
        self._credit_btn = QPushButton("💳 Mark Credit Received")
        self._credit_btn.clicked.connect(self._on_mark_credit)
        self._slip_btn = QPushButton("🖨 Print Slip")
        self._slip_btn.clicked.connect(lambda: self._on_print("slip"))
        self._label_btn = QPushButton("🏷 Print Label")
        self._label_btn.clicked.connect(lambda: self._on_print("label"))
        for btn in (self._pack_btn, self._credit_btn, self._slip_btn, self._label_btn):
            btn.setStyleSheet(
                "QPushButton { background-color: #6B8E23; color: white; border: none; "
                "padding: 6px 14px; border-radius: 4px; font-weight: 500; }"
                "QPushButton:hover { background-color: #556B2F; }"
                "QPushButton:disabled { background-color: #c0c0c0; color: #666; }"
            )
            toolbar.addWidget(btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        layout.addWidget(self._count_label)

        # Splitter: RGA table (top) + line drill-down (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._rga_table = QTableWidget()
        self._rga_table.setColumnCount(len(_RGA_COLUMNS))
        self._rga_table.setHorizontalHeaderLabels(_RGA_COLUMNS)
        self._rga_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._rga_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rga_table.setSortingEnabled(True)
        self._rga_table.setAlternatingRowColors(True)
        self._rga_table.itemSelectionChanged.connect(self._on_rga_selected)
        self._rga_table.horizontalHeader().setStretchLastSection(True)
        self._rga_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._rga_table)

        self._lines_table = QTableWidget()
        self._lines_table.setColumnCount(len(_LINE_COLUMNS))
        self._lines_table.setHorizontalHeaderLabels(_LINE_COLUMNS)
        self._lines_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lines_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._lines_table.setAlternatingRowColors(True)
        self._lines_table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self._lines_table)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    # ── Data ────────────────────────────────────────────────────────────
    def _fetch_rgas(self) -> list[dict[str, Any]]:
        sql = """
            SELECT
                vr.id           AS rga_id,
                vr.rga_number   AS rga_number,
                vr.vendor_id    AS vendor_id,
                v.name          AS vendor_name,
                vr.status       AS status,
                vr.tracking_number AS tracking_number,
                vr.shipped_at   AS shipped_at,
                COUNT(vco.id)   AS core_count,
                COALESCE(SUM(vco.core_amount * COALESCE(vco.quantity, 1)), 0) AS total_dollars
            FROM vendor_returns vr
            LEFT JOIN vendors v ON v.id = vr.vendor_id
            LEFT JOIN vendor_core_obligations vco ON vco.rga_id = vr.id
            WHERE COALESCE(vr.return_type, 'general') = 'vendor_core'
            GROUP BY vr.id
            ORDER BY vr.created_at DESC
            LIMIT 500
        """
        with get_db() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]

    def _fetch_lines(self, rga_id: int) -> list[dict[str, Any]]:
        sql = """
            SELECT
                vco.id           AS vco_id,
                COALESCE(p.sku, '')                                  AS sku,
                COALESCE(p.oem_part_number, p.xref_part_number, '')  AS part_number,
                vco.core_amount  AS core_amount,
                vco.quantity     AS quantity,
                vco.status       AS status,
                cr.customer_id   AS customer_id,
                cust.name        AS customer_name
            FROM vendor_core_obligations vco
            LEFT JOIN products p ON p.id = vco.product_id
            LEFT JOIN core_returns cr ON cr.id = vco.customer_core_id
            LEFT JOIN customers cust ON cust.id = cr.customer_id
            WHERE vco.rga_id = ?
            ORDER BY vco.id
        """
        with get_db() as conn:
            rows = conn.execute(sql, (rga_id,)).fetchall()
        return [dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows]

    # ── Slots ───────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        try:
            rgas = self._fetch_rgas()
        except Exception:
            log.exception("Could not fetch RGAs")
            rgas = []

        self._rga_table.setSortingEnabled(False)
        self._rga_table.setRowCount(len(rgas))
        for i, r in enumerate(rgas):
            rga_id = r.get("rga_id")
            number_item = QTableWidgetItem(str(r.get("rga_number") or f"#{rga_id}"))
            number_item.setData(Qt.ItemDataRole.UserRole, rga_id)
            self._rga_table.setItem(i, 0, number_item)
            self._rga_table.setItem(i, 1, QTableWidgetItem(str(r.get("vendor_name") or "")))
            self._rga_table.setItem(i, 2, _NumItem(float(r.get("core_count") or 0), fmt="int"))
            self._rga_table.setItem(i, 3, _NumItem(float(r.get("total_dollars") or 0)))
            self._rga_table.setItem(i, 4, QTableWidgetItem(str(r.get("status") or "")))
            self._rga_table.setItem(i, 5, QTableWidgetItem(str(r.get("tracking_number") or "")))
            self._rga_table.setItem(i, 6, QTableWidgetItem(str(r.get("shipped_at") or "")))
        self._rga_table.setSortingEnabled(True)

        self._count_label.setText(f"{len(rgas)} vendor-core RGA(s)")
        self._lines_table.setRowCount(0)

    def _selected_rga_id(self) -> int | None:
        rows = self._rga_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._rga_table.item(rows[0].row(), 0)
        if not item:
            return None
        val = item.data(Qt.ItemDataRole.UserRole)
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    def _on_rga_selected(self) -> None:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            self._lines_table.setRowCount(0)
            return
        try:
            lines = self._fetch_lines(rga_id)
        except Exception:
            log.exception("Could not fetch RGA lines")
            lines = []
        self._lines_table.setRowCount(len(lines))
        for i, l in enumerate(lines):
            self._lines_table.setItem(i, 0, QTableWidgetItem(str(l.get("vco_id") or "")))
            self._lines_table.setItem(i, 1, QTableWidgetItem(str(l.get("sku") or "")))
            self._lines_table.setItem(i, 2, QTableWidgetItem(str(l.get("part_number") or "")))
            amount = float(l.get("core_amount") or 0) * float(l.get("quantity") or 1)
            self._lines_table.setItem(i, 3, _NumItem(amount))
            self._lines_table.setItem(i, 4, QTableWidgetItem(str(l.get("status") or "")))
            self._lines_table.setItem(i, 5, QTableWidgetItem(str(l.get("customer_name") or "")))

    # ── Actions (stubs — wire to existing helpers in follow-up) ─────────
    def _on_pack_and_ship(self) -> None:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            QMessageBox.information(self, "Pack & Ship", "Select an RGA row first.")
            return
        try:
            from .pack_and_ship_dialog import PackAndShipDialog
        except ImportError:
            QMessageBox.warning(self, "Pack & Ship", "PackAndShipDialog not available.")
            return
        try:
            dlg = PackAndShipDialog(rga_id, self)
            dlg.exec()
            self._refresh()
        except Exception as exc:
            log.exception("Pack & Ship failed")
            QMessageBox.critical(self, "Pack & Ship", f"Could not open dialog:\n{exc}")

    def _on_mark_credit(self) -> None:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            QMessageBox.information(self, "Mark Credit Received", "Select an RGA row first.")
            return
        QMessageBox.information(
            self,
            "Mark Credit Received",
            "Credit-entry dialog will be wired in H.5 to use "
            "`transition_to_credit_received()`.",
        )

    def _on_print(self, kind: str) -> None:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            QMessageBox.information(self, "Print", "Select an RGA row first.")
            return
        QMessageBox.information(
            self,
            "Print",
            f"Reprint {kind} for RGA {rga_id} — will be wired to "
            f"`generate_core_packing_slip` / `generate_core_freight_label`.",
        )
