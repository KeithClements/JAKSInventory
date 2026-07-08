"""Vendor Cores Overview Screen — Phase G (May 2026).

The new entry point under PURCHASING → "Vendor Cores". Shows the full
fleet of outstanding core obligations at a glance:

* Five clickable KPI tiles matching the 5-state lifecycle.
* A sortable per-vendor roll-up table (default sort: outstanding $ desc).
* Double-clicking a vendor row drills into ``VendorCoresBoard`` (kanban
  view scoped to that one vendor).

The screen contains no per-obligation actions — all state transitions
live on the Board. This keeps the Overview purely read-only so it loads
fast even with hundreds of obligations.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.core_handling import VCO_LIFECYCLE, list_vendor_core_summary


# Tile colors keyed by canonical status.
_TILE_COLORS = {
    "on_shelf":               "#5b6f44",  # olive
    "awaiting_customer_core": "#b08d2e",  # amber
    "pending_vendor_ship":    "#1f6f8b",  # teal
    "shipped":                "#5a4e8c",  # purple
    "credit_received":        "#2f7a4e",  # green
}


class _Tile(QFrame):
    """Clickable KPI tile. Emits ``clicked`` with its status key."""

    clicked = Signal(str)

    def __init__(self, status: str, label: str, color: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._status = status
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"background: {color}; border-radius: 6px; padding: 10px;"
        )
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("color: white; font-size: 10pt;")
        self._val = QLabel("0")
        self._val.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("$0.00")
        self._sub.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._sub)

    def set_values(self, count: int, dollars: float) -> None:
        self._val.setText(f"{count:,}")
        self._sub.setText(f"${dollars:,.2f}")

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._status)
        super().mousePressEvent(ev)


class VendorCoresOverviewScreen(QWidget):
    """Two-pane screen: Overview (this widget) + Board (drill-in).

    The Board is loaded lazily on first drill-in to keep startup fast.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack, 1)

        # Page 0 — overview
        self._overview = QWidget()
        self._build_overview_ui(self._overview)
        self._stack.addWidget(self._overview)

        # Page 1 — board (created on demand)
        self._board: Optional[QWidget] = None

        self.refresh()

    # ------------------------------------------------------------------
    # Overview page construction
    # ------------------------------------------------------------------
    def _build_overview_ui(self, page: QWidget) -> None:
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # Header row
        hdr = QHBoxLayout()
        title = QLabel("Vendor Cores")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        self._show_closed_cb = QCheckBox("Show vendors with no open cores")
        self._show_closed_cb.stateChanged.connect(lambda _s: self.refresh())
        hdr.addWidget(self._show_closed_cb)
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(refresh_btn)
        lay.addLayout(hdr)

        # KPI tiles row
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(8)
        self._tiles: dict[str, _Tile] = {}
        for status, label in VCO_LIFECYCLE:
            tile = _Tile(status, label, _TILE_COLORS.get(status, "#5b6f44"))
            tile.clicked.connect(self._on_tile_clicked)
            tiles_row.addWidget(tile, 1)
            self._tiles[status] = tile
        lay.addLayout(tiles_row)

        # Vendor roll-up table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "Vendor", "On Shelf", "Awaiting", "Pending Ship",
            "Shipped", "Credit Recv'd", "Outstanding $", "⚠",
        ])
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 8):
            hdr_view.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemDoubleClicked.connect(self._on_row_double_clicked)
        lay.addWidget(self._table, 1)

        # Empty-state hint
        self._empty_lbl = QLabel(
            "No outstanding vendor cores. \U0001F389 Either everything's "
            "shipped, or no PO has recorded a core charge yet."
        )
        self._empty_lbl.setStyleSheet("color: #888; padding: 16px;")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setVisible(False)
        lay.addWidget(self._empty_lbl)

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        include_closed = self._show_closed_cb.isChecked()
        try:
            rows = list_vendor_core_summary(include_closed=include_closed)
        except Exception as e:
            rows = []
            print(f"[VendorCoresOverview] summary load failed: {e}")

        # Aggregate tile totals.
        bucket_counts = {s: 0 for s, _ in VCO_LIFECYCLE}
        bucket_dollars = {s: 0.0 for s, _ in VCO_LIFECYCLE}
        for r in rows:
            for s, _ in VCO_LIFECYCLE:
                bucket_counts[s] += int(r.get(s, 0) or 0)
            # Outstanding $ rolls up across open buckets only.
            bucket_dollars["on_shelf"] += 0  # not used per-bucket

        # Compute outstanding $ broken down per bucket from raw obligations.
        per_bucket_dollars = self._compute_bucket_dollars()
        for s, _ in VCO_LIFECYCLE:
            self._tiles[s].set_values(bucket_counts[s], per_bucket_dollars.get(s, 0.0))

        # Fill the table.
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for r in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(str(r.get("vendor_name") or ""))
            name_item.setData(Qt.ItemDataRole.UserRole, int(r.get("vendor_id") or 0))
            self._table.setItem(row, 0, name_item)

            for col, key in enumerate(
                ("on_shelf", "awaiting_customer_core", "pending_vendor_ship",
                 "shipped", "credit_received"), start=1,
            ):
                v = int(r.get(key, 0) or 0)
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, v)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, item)

            dollars = float(r.get("outstanding_amount", 0.0) or 0.0)
            d_item = QTableWidgetItem()
            d_item.setData(Qt.ItemDataRole.DisplayRole, dollars)
            d_item.setText(f"${dollars:,.2f}")
            d_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 6, d_item)

            overdue = "⚠" if int(r.get("overdue", 0) or 0) else ""
            o_item = QTableWidgetItem(overdue)
            o_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 7, o_item)

        self._table.setSortingEnabled(True)
        # Default sort: outstanding $ desc.
        self._table.sortItems(6, Qt.SortOrder.DescendingOrder)

        self._empty_lbl.setVisible(self._table.rowCount() == 0)
        self._table.setVisible(self._table.rowCount() > 0)

    def _compute_bucket_dollars(self) -> dict[str, float]:
        """Per-bucket outstanding-deposit dollars, including closed buckets
        (so the credit_received tile still shows lifetime $).
        """
        from db import get_db  # local import to avoid cycle
        from db.core_handling import fold_status
        out = {s: 0.0 for s, _ in VCO_LIFECYCLE}
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT status, COALESCE(core_amount, 0) AS d, "
                    "       COALESCE(quantity, 1) AS u "
                    "  FROM vendor_core_obligations"
                ).fetchall()
            for r in rows:
                s_raw = (r["status"] if hasattr(r, "keys") else r[0]) or ""
                d = float((r["d"] if hasattr(r, "keys") else r[1]) or 0)
                u = int((r["u"] if hasattr(r, "keys") else r[2]) or 1)
                s = fold_status(s_raw)
                if s in out:
                    out[s] += d * max(u, 1)
        except Exception as e:
            print(f"[VendorCoresOverview] bucket-dollars load failed: {e}")
        return out

    # ------------------------------------------------------------------
    # Navigation — tile click / row double-click
    # ------------------------------------------------------------------
    def _on_tile_clicked(self, status: str) -> None:
        """For now, tile clicks act as a soft filter hint by jumping to
        the Board with no vendor selected — the board itself shows all
        vendors filtered by the chosen lifecycle column.

        Phase G future: also collapse the vendor table to vendors that
        have at least one obligation in the chosen bucket.
        """
        self._open_board(vendor_id=None, focus_status=status)

    def _on_row_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        name_item = self._table.item(row, 0)
        if not name_item:
            return
        vendor_id = int(name_item.data(Qt.ItemDataRole.UserRole) or 0)
        if vendor_id:
            self._open_board(vendor_id=vendor_id, focus_status=None)

    def _open_board(
        self,
        *,
        vendor_id: Optional[int],
        focus_status: Optional[str],
    ) -> None:
        from .vendor_cores_board import VendorCoresBoard
        if self._board is None:
            self._board = VendorCoresBoard()
            self._board.back_to_overview.connect(self._back_to_overview)
            self._stack.addWidget(self._board)
        self._board.load(vendor_id=vendor_id, focus_status=focus_status)
        self._stack.setCurrentWidget(self._board)

    def _back_to_overview(self) -> None:
        self.refresh()
        self._stack.setCurrentWidget(self._overview)
