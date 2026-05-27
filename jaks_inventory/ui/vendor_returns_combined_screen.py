"""Vendor Returns — combined 4-tab screen (May-2026 UX consolidation).

Replaces the three separate sidebar items "Vendor Cores", "RGA Shipments"
and "Vendor Credits".

Layout
------
* Header (title + Refresh)
* 7 summary cards
* Tabs:
    1. Ready to Ship       — pick cores → Create RGA
    2. Open RGA / Shipment — RGAs in flight + drill-in lines
    3. Awaiting Vendor Credit — shipped, awaiting credit
    4. Credit History      — closed credits (replaces Vendor Credits)
"""
from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db.database import get_db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _r(row) -> dict[str, Any]:
    return dict(row) if hasattr(row, "keys") else dict(enumerate(row))


def _days_since(s: Any) -> int:
    if not s:
        return 0
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "").split(".")[0]).date()
        return max(0, (date.today() - d).days)
    except Exception:
        return 0


class _NumItem(QTableWidgetItem):
    def __init__(self, value: float, fmt: str = "$"):
        super().__init__()
        self._v = float(value or 0)
        if fmt == "$":
            self.setText(f"${self._v:,.2f}" if self._v else "$0.00")
        elif fmt == "int":
            self.setText(str(int(self._v)))
        else:
            self.setText(str(self._v))
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._v < other._v
        return super().__lt__(other)


class _SummaryCard(QFrame):
    def __init__(self, label: str, color: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: {color}; border-radius: 6px; padding: 4px; }}"
        )
        self.setMinimumHeight(70)
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("color: white; font-size: 9pt;")
        self._lbl.setWordWrap(True)
        self._val = QLabel("0")
        self._val.setStyleSheet("color: white; font-size: 18pt; font-weight: bold;")
        self._sub = QLabel("$0.00")
        self._sub.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._sub)

    def set_values(self, count: int, dollars: Optional[float] = None) -> None:
        self._val.setText(f"{count:,}")
        if dollars is None:
            self._sub.setText("")
        else:
            self._sub.setText(f"${dollars:,.2f}")


# ---------------------------------------------------------------------------
# Tab 1 — Ready to Ship
# ---------------------------------------------------------------------------

_READY_COLS = (
    "✓", "Vendor", "SKU", "Part #", "Product", "Customer",
    "Invoice #", "Core Value", "Condition", "Days Ready",
)


class _ReadyToShipTab(QWidget):
    """Pick eligible cores and start a vendor RGA."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Left grid
        left = QVBoxLayout()
        hint = QLabel("Tick the cores to bundle into a vendor RGA. "
                      "Only one vendor per RGA.")
        hint.setStyleSheet("color:#6B7280; font-size: 11px;")
        left.addWidget(hint)

        self._table = QTableWidget(0, len(_READY_COLS))
        self._table.setHorizontalHeaderLabels(list(_READY_COLS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for c in range(1, len(_READY_COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        left.addWidget(self._table, 1)

        # Right action panel
        right = QFrame()
        right.setStyleSheet(
            "QFrame { background:#f9f9f4; border-left:1px solid #d8d6c8; }"
        )
        right.setMinimumWidth(260)
        right.setMaximumWidth(320)
        r = QVBoxLayout(right)
        r.setContentsMargins(12, 12, 12, 12)
        r.setSpacing(6)

        title = QLabel("Selection")
        f = QFont(); f.setBold(True); f.setPointSize(11)
        title.setFont(f)
        r.addWidget(title)

        self._lbl_vendor = QLabel("Vendor: —")
        self._lbl_count = QLabel("Cores: 0")
        self._lbl_value = QLabel("Value: $0.00")
        for w in (self._lbl_vendor, self._lbl_count, self._lbl_value):
            w.setStyleSheet("padding: 2px 0;")
            r.addWidget(w)

        self._warn_lbl = QLabel("")
        self._warn_lbl.setStyleSheet("color:#a4453a; font-weight: bold;")
        self._warn_lbl.setWordWrap(True)
        r.addWidget(self._warn_lbl)

        r.addSpacing(8)

        self._btn_create = QPushButton("Create RGA")
        self._btn_create.clicked.connect(self._on_create_rga)
        self._btn_add = QPushButton("Add to Existing RGA")
        self._btn_add.clicked.connect(self._on_add_to_rga)
        self._btn_tags = QPushButton("Print Core Tags")
        self._btn_tags.clicked.connect(self._on_print_tags)
        for b in (self._btn_create, self._btn_add, self._btn_tags):
            b.setStyleSheet(
                "QPushButton { background:#6B8E23; color:white; border:none; "
                "padding:8px 14px; border-radius:4px; font-weight:500; }"
                "QPushButton:hover { background:#556B2F; }"
                "QPushButton:disabled { background:#c0c0c0; color:#666; }"
            )
            r.addWidget(b)

        r.addStretch(1)

        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self.refresh)
        r.addWidget(refresh)

        root.addLayout(left, 1)
        root.addWidget(right, 0)

        self._update_selection_panel()

    # ── Data ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        sql = """
            SELECT vco.id           AS vco_id,
                   vco.vendor_id    AS vendor_id,
                   v.name           AS vendor_name,
                   p.sku            AS sku,
                   COALESCE(p.oem_part_number, p.xref_part_number, '') AS part_number,
                   p.title          AS product,
                   cust.name        AS customer_name,
                   inv.invoice_number AS invoice_number,
                   vco.core_amount  AS deposit,
                   vco.quantity     AS quantity,
                   vco.condition    AS condition,
                   vco.created_at   AS created_at
              FROM vendor_core_obligations vco
              LEFT JOIN vendors v        ON v.id = vco.vendor_id
              LEFT JOIN products p       ON p.id = vco.product_id
              LEFT JOIN core_returns cr  ON cr.id = vco.customer_core_id
              LEFT JOIN customers cust   ON cust.id = cr.customer_id
              LEFT JOIN invoices inv     ON inv.id = cr.invoice_id
             WHERE LOWER(COALESCE(vco.status,'')) IN ('on_shelf','pending_vendor_ship')
               AND (vco.rga_id IS NULL OR vco.rga_id = 0)
             ORDER BY v.name, vco.created_at DESC
             LIMIT 1000
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            self._rows = [_r(x) for x in rows]
        except Exception:
            log.exception("Ready-to-Ship fetch failed")
            self._rows = []
        self._populate()

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            qty = int(r.get("quantity") or 1)
            value = float(r.get("deposit") or 0) * qty
            days = _days_since(r.get("created_at"))

            cb = QCheckBox()
            cb.stateChanged.connect(lambda _s: self._update_selection_panel())
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(cb)
            hl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setCellWidget(i, 0, holder)
            # Stash the row dict + checkbox on a hidden item.
            stash = QTableWidgetItem("")
            stash.setData(Qt.ItemDataRole.UserRole, (cb, r))
            self._table.setItem(i, 0, stash)

            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(i, 1, cell(r.get("vendor_name") or ""))
            self._table.setItem(i, 2, cell(r.get("sku") or ""))
            self._table.setItem(i, 3, cell(r.get("part_number") or ""))
            self._table.setItem(i, 4, cell((r.get("product") or "")[:60]))
            self._table.setItem(i, 5, cell(r.get("customer_name") or ""))
            self._table.setItem(i, 6, cell(r.get("invoice_number") or ""))
            self._table.setItem(i, 7, _NumItem(value))
            self._table.setItem(i, 8, cell(r.get("condition") or ""))
            self._table.setItem(i, 9, _NumItem(days, fmt="int"))
        self._table.setSortingEnabled(True)
        self._update_selection_panel()

    def _selected_rows(self) -> list[dict]:
        out: list[dict] = []
        for i in range(self._table.rowCount()):
            item = self._table.item(i, 0)
            if item is None:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            cb, row = data
            try:
                if cb.isChecked():
                    out.append(row)
            except RuntimeError:
                # Widget was deleted (table rebuilt) — skip.
                continue
        return out

    def _update_selection_panel(self) -> None:
        sel = self._selected_rows()
        vendors = {r.get("vendor_id") for r in sel if r.get("vendor_id")}
        total = sum(
            float(r.get("deposit") or 0) * int(r.get("quantity") or 1) for r in sel
        )
        single_vendor = (len(vendors) <= 1)
        vendor_name = "—"
        if vendors:
            for r in sel:
                if r.get("vendor_id") in vendors:
                    vendor_name = r.get("vendor_name") or "—"
                    break
        self._lbl_vendor.setText(f"Vendor: {vendor_name if single_vendor else 'MIXED!'}")
        self._lbl_count.setText(f"Cores: {len(sel)}")
        self._lbl_value.setText(f"Value: ${total:,.2f}")

        if not sel:
            self._warn_lbl.setText("")
        elif not single_vendor:
            self._warn_lbl.setText(
                "⚠ Selection spans multiple vendors. "
                "Uncheck cores so only one vendor remains."
            )
        else:
            self._warn_lbl.setText("")

        can_act = bool(sel and single_vendor)
        self._btn_create.setEnabled(can_act)
        self._btn_add.setEnabled(can_act)
        self._btn_tags.setEnabled(bool(sel))

    # ── Actions ─────────────────────────────────────────────────────
    def _on_create_rga(self) -> None:
        sel = self._selected_rows()
        if not sel:
            return
        vendor_id = sel[0].get("vendor_id")
        try:
            from db.inventory import attach_vendor_cores_to_rga, create_vendor_return
            new = create_vendor_return(
                vendor_id=int(vendor_id),
                reason="vendor_core_return",
                notes=f"Bundled from Ready-to-Ship ({len(sel)} cores)",
            )
            rga_id = int(new.get("id"))
            attach_vendor_cores_to_rga(
                rga_id, [int(r["vco_id"]) for r in sel], user="ui",
            )
            # Mark as vendor_core return_type if column exists.
            try:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE vendor_returns SET return_type=? WHERE id=?",
                        ("vendor_core", rga_id),
                    )
                    conn.commit()
            except Exception:
                log.debug("vendor_returns.return_type update skipped")
            QMessageBox.information(
                self, "Create RGA",
                f"Created RGA {new.get('rga_number')} with {len(sel)} core(s).",
            )
        except Exception as exc:
            log.exception("Create RGA failed")
            QMessageBox.critical(self, "Create RGA", f"Failed: {exc}")
            return
        self.refresh()
        # Bubble change so other tabs refresh too.
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def _on_add_to_rga(self) -> None:
        sel = self._selected_rows()
        if not sel:
            return
        vendor_id = sel[0].get("vendor_id")
        # Pick an existing open RGA for this vendor.
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, rga_number, status FROM vendor_returns "
                    "WHERE vendor_id=? AND COALESCE(status,'') NOT IN "
                    "  ('credited','closed','cancelled') "
                    "ORDER BY id DESC LIMIT 50",
                    (int(vendor_id),),
                ).fetchall()
            options = [(_r(x).get("rga_number") or f"#{_r(x).get('id')}",
                        int(_r(x).get("id")))
                       for x in rows]
        except Exception:
            log.exception("RGA list fetch failed")
            options = []
        if not options:
            QMessageBox.information(
                self, "Add to Existing RGA",
                "No open RGA for this vendor — use Create RGA instead.",
            )
            return
        labels = [o[0] for o in options]
        label, ok = QInputDialog.getItem(
            self, "Add to RGA", "Choose an open RGA:", labels, 0, False,
        )
        if not ok:
            return
        rga_id = options[labels.index(label)][1]
        try:
            from db.inventory import attach_vendor_cores_to_rga
            n = attach_vendor_cores_to_rga(
                rga_id, [int(r["vco_id"]) for r in sel], user="ui",
            )
            QMessageBox.information(self, "Add to RGA", f"Added {n} core(s).")
        except Exception as exc:
            QMessageBox.critical(self, "Add to RGA", f"Failed: {exc}")
            return
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def _on_print_tags(self) -> None:
        sel = self._selected_rows()
        if not sel:
            return
        QMessageBox.information(
            self, "Print Core Tags",
            f"Would print {len(sel)} core tag(s) — "
            "tag PDF generator not yet implemented.",
        )


# ---------------------------------------------------------------------------
# Tab 2 — Open RGA / Shipment
# ---------------------------------------------------------------------------

_RGA_COLS = (
    "RGA #", "Vendor", "# Cores", "Total Core Value", "Status",
    "Tracking", "Ship Date", "Days Open", "Next Action",
)
_RGA_LINE_COLS = ("SKU", "Part #", "Customer", "Invoice #", "Deposit", "Condition")


class _OpenRGATab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rgas: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        for label, slot in [
            ("📦 Pack && Ship",       self._on_pack_ship),
            ("🖨 Print Packing Slip", lambda: self._on_print("slip")),
            ("🏷 Print Labels",        lambda: self._on_print("label")),
            ("➕ Add Tracking",        self._on_add_tracking),
            ("✓ Mark Shipped",         self._on_mark_shipped),
            ("✕ Cancel RGA",           self._on_cancel_rga),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            b.setStyleSheet(
                "QPushButton { background:#6B8E23; color:white; border:none; "
                "padding:6px 12px; border-radius:4px; }"
                "QPushButton:hover { background:#556B2F; }"
            )
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._table = QTableWidget(0, len(_RGA_COLS))
        self._table.setHorizontalHeaderLabels(list(_RGA_COLS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_rga_selected)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._table)

        self._lines = QTableWidget(0, len(_RGA_LINE_COLS))
        self._lines.setHorizontalHeaderLabels(list(_RGA_LINE_COLS))
        self._lines.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._lines.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lines.setAlternatingRowColors(True)
        self._lines.verticalHeader().setVisible(False)
        splitter.addWidget(self._lines)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

    # ── Data ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        sql = """
            SELECT vr.id           AS rga_id,
                   vr.rga_number   AS rga_number,
                   v.name          AS vendor_name,
                   vr.status       AS status,
                   vr.tracking_number AS tracking_number,
                   vr.shipped_at   AS shipped_at,
                   vr.created_at   AS created_at,
                   COUNT(vco.id)   AS core_count,
                   COALESCE(SUM(vco.core_amount * COALESCE(vco.quantity, 1)), 0)
                     AS total_value
              FROM vendor_returns vr
              LEFT JOIN vendors v ON v.id = vr.vendor_id
              LEFT JOIN vendor_core_obligations vco ON vco.rga_id = vr.id
             WHERE COALESCE(vr.return_type, 'general') = 'vendor_core'
               AND COALESCE(vr.status, '') NOT IN ('credited','closed','cancelled')
             GROUP BY vr.id
             ORDER BY vr.created_at DESC
             LIMIT 500
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            self._rgas = [_r(x) for x in rows]
        except Exception:
            log.exception("Open RGA fetch failed")
            self._rgas = []
        self._populate()

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._rgas))
        for i, r in enumerate(self._rgas):
            status = (r.get("status") or "").lower()
            shipped = bool(r.get("shipped_at"))
            next_action = (
                "Apply Credit" if status in ("vendor_received", "credit_pending")
                else "Pack & Ship" if not shipped
                else "Add Tracking" if not r.get("tracking_number")
                else "Mark Shipped" if status not in ("shipped",)
                else "Awaiting Credit"
            )

            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            num_item = cell(r.get("rga_number") or f"#{r.get('rga_id')}")
            num_item.setData(Qt.ItemDataRole.UserRole, int(r.get("rga_id")))
            self._table.setItem(i, 0, num_item)
            self._table.setItem(i, 1, cell(r.get("vendor_name") or ""))
            self._table.setItem(i, 2, _NumItem(float(r.get("core_count") or 0), fmt="int"))
            self._table.setItem(i, 3, _NumItem(float(r.get("total_value") or 0)))
            self._table.setItem(i, 4, cell(status or ""))
            self._table.setItem(i, 5, cell(r.get("tracking_number") or ""))
            self._table.setItem(i, 6, cell(str(r.get("shipped_at") or "")[:10]))
            self._table.setItem(i, 7, _NumItem(_days_since(r.get("created_at")), fmt="int"))
            self._table.setItem(i, 8, cell(next_action))
        self._table.setSortingEnabled(True)
        self._lines.setRowCount(0)

    def _selected_rga_id(self) -> Optional[int]:
        row = self._table.currentRow()
        if row < 0:
            return None
        it = self._table.item(row, 0)
        if it is None:
            return None
        try:
            return int(it.data(Qt.ItemDataRole.UserRole))
        except Exception:
            return None

    def _on_rga_selected(self) -> None:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            self._lines.setRowCount(0)
            return
        sql = """
            SELECT p.sku                                       AS sku,
                   COALESCE(p.oem_part_number, p.xref_part_number, '') AS part_number,
                   cust.name                                   AS customer_name,
                   inv.invoice_number                          AS invoice_number,
                   vco.core_amount                             AS deposit,
                   vco.quantity                                AS quantity,
                   vco.condition                               AS condition
              FROM vendor_core_obligations vco
              LEFT JOIN products p      ON p.id = vco.product_id
              LEFT JOIN core_returns cr ON cr.id = vco.customer_core_id
              LEFT JOIN customers cust  ON cust.id = cr.customer_id
              LEFT JOIN invoices inv    ON inv.id = cr.invoice_id
             WHERE vco.rga_id = ?
             ORDER BY vco.id
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql, (rga_id,)).fetchall()
            rows = [_r(x) for x in rows]
        except Exception:
            log.exception("RGA line fetch failed")
            rows = []
        self._lines.setRowCount(len(rows))
        for i, l in enumerate(rows):
            qty = int(l.get("quantity") or 1)
            value = float(l.get("deposit") or 0) * qty
            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._lines.setItem(i, 0, cell(l.get("sku") or ""))
            self._lines.setItem(i, 1, cell(l.get("part_number") or ""))
            self._lines.setItem(i, 2, cell(l.get("customer_name") or ""))
            self._lines.setItem(i, 3, cell(l.get("invoice_number") or ""))
            self._lines.setItem(i, 4, _NumItem(value))
            self._lines.setItem(i, 5, cell(l.get("condition") or ""))

    # ── Actions ─────────────────────────────────────────────────────
    def _require_rga(self) -> Optional[int]:
        rga_id = self._selected_rga_id()
        if rga_id is None:
            QMessageBox.information(self, "Select RGA", "Pick an RGA row first.")
        return rga_id

    def _on_pack_ship(self) -> None:
        rga_id = self._require_rga()
        if rga_id is None:
            return
        # Pick first attached obligation to seed the dialog.
        try:
            with get_db() as conn:
                row = conn.execute(
                    "SELECT * FROM vendor_core_obligations WHERE rga_id=? "
                    "ORDER BY id LIMIT 1", (rga_id,),
                ).fetchone()
            obl = _r(row) if row else None
        except Exception:
            obl = None
        if not obl:
            QMessageBox.information(
                self, "Pack & Ship",
                "This RGA has no attached cores.",
            )
            return
        try:
            from .pack_and_ship_dialog import PackAndShipDialog
            PackAndShipDialog(self, obligation=obl).exec()
        except Exception as exc:
            log.exception("Pack & Ship failed")
            QMessageBox.critical(self, "Pack & Ship", f"Failed: {exc}")
        self.refresh()

    def _on_print(self, kind: str) -> None:
        rga_id = self._require_rga()
        if rga_id is None:
            return
        try:
            if kind == "slip":
                from ..utils.pdf_core_return import generate_core_packing_slip
                generate_core_packing_slip(rga_id)
            else:
                from ..utils.pdf_core_return import generate_core_freight_label
                generate_core_freight_label(rga_id)
            QMessageBox.information(self, "Print", f"Generated {kind} for RGA {rga_id}.")
        except Exception as exc:
            QMessageBox.warning(self, "Print", f"Failed: {exc}")

    def _on_add_tracking(self) -> None:
        rga_id = self._require_rga()
        if rga_id is None:
            return
        text, ok = QInputDialog.getText(self, "Add Tracking", "Tracking number:")
        if not ok or not text.strip():
            return
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_returns SET tracking_number=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (text.strip(), rga_id),
                )
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Tracking", f"Failed: {exc}")
            return
        self.refresh()

    def _on_mark_shipped(self) -> None:
        rga_id = self._require_rga()
        if rga_id is None:
            return
        if QMessageBox.question(
            self, "Mark Shipped",
            "Mark all attached cores as shipped?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_returns SET status='shipped', "
                    "shipped_at=COALESCE(shipped_at, CURRENT_TIMESTAMP), "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (rga_id,),
                )
                conn.execute(
                    "UPDATE vendor_core_obligations SET status='shipped', "
                    "shipped_date=COALESCE(shipped_date, CURRENT_TIMESTAMP), "
                    "updated_at=CURRENT_TIMESTAMP WHERE rga_id=?", (rga_id,),
                )
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Mark Shipped", f"Failed: {exc}")
            return
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def _on_cancel_rga(self) -> None:
        rga_id = self._require_rga()
        if rga_id is None:
            return
        if QMessageBox.question(
            self, "Cancel RGA",
            "Cancel this RGA and detach its cores back to On-Shelf?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_core_obligations SET rga_id=NULL, status='on_shelf', "
                    "updated_at=CURRENT_TIMESTAMP WHERE rga_id=?", (rga_id,),
                )
                conn.execute(
                    "UPDATE vendor_returns SET status='cancelled', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?", (rga_id,),
                )
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Cancel RGA", f"Failed: {exc}")
            return
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tab 3 — Awaiting Vendor Credit
# ---------------------------------------------------------------------------

_AWAIT_COLS = (
    "Vendor", "RGA #", "Tracking", "Ship Date", "SKU", "Part #",
    "Expected Credit", "Days Waiting", "Vendor Credit #", "Variance",
    "Next Action",
)


class _AwaitingCreditTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        for label, slot in [
            ("💳 Apply Vendor Credit", lambda: self._on_apply(partial=False)),
            ("💱 Partial Credit",      lambda: self._on_apply(partial=True)),
            ("⚠ Dispute",              self._on_dispute),
            ("🚫 Write Off Variance",  self._on_write_off),
            ("👁  View RGA",            self._on_view_rga),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            b.setStyleSheet(
                "QPushButton { background:#6B8E23; color:white; border:none; "
                "padding:6px 12px; border-radius:4px; }"
                "QPushButton:hover { background:#556B2F; }"
            )
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        root.addLayout(toolbar)

        self._table = QTableWidget(0, len(_AWAIT_COLS))
        self._table.setHorizontalHeaderLabels(list(_AWAIT_COLS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        for c in range(len(_AWAIT_COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        root.addWidget(self._table, 1)

    # ── Data ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        sql = """
            SELECT vco.id                  AS vco_id,
                   vco.rga_id              AS rga_id,
                   vco.vendor_id           AS vendor_id,
                   v.name                  AS vendor_name,
                   vr.rga_number           AS rga_number,
                   vco.tracking_number     AS tracking_number,
                   vco.shipped_date        AS shipped_date,
                   p.sku                   AS sku,
                   COALESCE(p.oem_part_number, p.xref_part_number, '') AS part_number,
                   vco.core_amount         AS deposit,
                   vco.quantity            AS quantity,
                   vco.credit_received     AS credit_received,
                   vco.vendor_credit_number AS vendor_credit_number
              FROM vendor_core_obligations vco
              LEFT JOIN vendors v        ON v.id = vco.vendor_id
              LEFT JOIN vendor_returns vr ON vr.id = vco.rga_id
              LEFT JOIN products p       ON p.id = vco.product_id
             WHERE LOWER(COALESCE(vco.status,'')) IN
                 ('shipped','vendor_received','credit_pending')
             ORDER BY COALESCE(vco.shipped_date, vco.updated_at) DESC
             LIMIT 1000
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            self._rows = [_r(x) for x in rows]
        except Exception:
            log.exception("Awaiting Credit fetch failed")
            self._rows = []
        self._populate()

    def _populate(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            qty = int(r.get("quantity") or 1)
            expected = float(r.get("deposit") or 0) * qty
            actual = float(r.get("credit_received") or 0)
            variance = actual - expected if actual else 0.0
            days = _days_since(r.get("shipped_date"))

            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            vendor_item = cell(r.get("vendor_name") or "")
            vendor_item.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(i, 0, vendor_item)
            self._table.setItem(i, 1, cell(r.get("rga_number") or ""))
            self._table.setItem(i, 2, cell(r.get("tracking_number") or ""))
            self._table.setItem(i, 3, cell(str(r.get("shipped_date") or "")[:10]))
            self._table.setItem(i, 4, cell(r.get("sku") or ""))
            self._table.setItem(i, 5, cell(r.get("part_number") or ""))
            self._table.setItem(i, 6, _NumItem(expected))
            self._table.setItem(i, 7, _NumItem(days, fmt="int"))
            self._table.setItem(i, 8, cell(r.get("vendor_credit_number") or ""))
            v_item = _NumItem(variance)
            if abs(variance) >= 0.01:
                v_item.setForeground(QColor("#a4453a") if variance < 0 else QColor("#2f7a4e"))
            self._table.setItem(i, 9, v_item)
            self._table.setItem(i, 10, cell("Apply Vendor Credit"))
        self._table.setSortingEnabled(True)

    def _selected(self) -> Optional[dict]:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    # ── Actions ─────────────────────────────────────────────────────
    def _on_apply(self, *, partial: bool) -> None:
        r = self._selected()
        if not r:
            QMessageBox.information(self, "Select", "Pick a row first.")
            return
        expected = float(r.get("deposit") or 0) * int(r.get("quantity") or 1)
        prompt = "Credit amount received" + (" (partial)" if partial else "")
        amt, ok = QInputDialog.getDouble(
            self, "Apply Vendor Credit", prompt + ":",
            value=expected if not partial else 0.0,
            min=0.0, max=1_000_000.0, decimals=2,
        )
        if not ok:
            return
        credit_no, ok = QInputDialog.getText(
            self, "Apply Vendor Credit", "Vendor credit number:",
        )
        if not ok:
            return
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_core_obligations SET "
                    "  status='credit_received', "
                    "  credit_received=?, "
                    "  vendor_credit_number=COALESCE(?, vendor_credit_number), "
                    "  credit_date=COALESCE(credit_date, CURRENT_TIMESTAMP), "
                    "  credit_received_date=COALESCE(credit_received_date, CURRENT_TIMESTAMP), "
                    "  updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (float(amt), credit_no.strip() or None, int(r["vco_id"])),
                )
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Apply Credit", f"Failed: {exc}")
            return
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def _on_dispute(self) -> None:
        r = self._selected()
        if not r:
            return
        note, ok = QInputDialog.getText(self, "Dispute", "Dispute note:")
        if not ok:
            return
        try:
            with get_db() as conn:
                conn.execute(
                    "UPDATE vendor_core_obligations SET notes = "
                    "COALESCE(notes,'') || ? || ' (disputed)', "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (note.strip(), int(r["vco_id"])),
                )
                conn.commit()
        except Exception as exc:
            QMessageBox.warning(self, "Dispute", f"Failed: {exc}")
            return
        QMessageBox.information(self, "Dispute", "Dispute note recorded.")
        self.refresh()

    def _on_write_off(self) -> None:
        r = self._selected()
        if not r:
            return
        if QMessageBox.question(
            self, "Write Off Variance",
            "Write off the variance and close this obligation?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from db.core_handling import transition_to_write_off
            transition_to_write_off(int(r["vco_id"]), reason="variance write-off")
        except Exception as exc:
            QMessageBox.warning(self, "Write Off", f"Failed: {exc}")
            return
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def _on_view_rga(self) -> None:
        r = self._selected()
        if not r or not r.get("rga_id"):
            QMessageBox.information(self, "View RGA", "No RGA linked.")
            return
        QMessageBox.information(
            self, "View RGA",
            f"Open the 'Open RGA / Shipment' tab and select "
            f"RGA {r.get('rga_number') or r.get('rga_id')}.",
        )


# ---------------------------------------------------------------------------
# Tab 4 — Credit History
# ---------------------------------------------------------------------------

_HIST_COLS = (
    "Credit Date", "Vendor", "RGA #", "Vendor Credit #", "SKU", "Part #",
    "Expected Credit", "Actual Credit", "Variance", "Closed Date",
)


class _CreditHistoryTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Filters
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Vendor:"))
        self._vendor_filter = QLineEdit()
        self._vendor_filter.setPlaceholderText("any")
        self._vendor_filter.setFixedWidth(160)
        self._vendor_filter.textChanged.connect(self._populate)
        filt.addWidget(self._vendor_filter)

        filt.addWidget(QLabel("RGA #:"))
        self._rga_filter = QLineEdit()
        self._rga_filter.setFixedWidth(120)
        self._rga_filter.textChanged.connect(self._populate)
        filt.addWidget(self._rga_filter)

        filt.addWidget(QLabel("Part #:"))
        self._part_filter = QLineEdit()
        self._part_filter.setFixedWidth(140)
        self._part_filter.textChanged.connect(self._populate)
        filt.addWidget(self._part_filter)

        filt.addWidget(QLabel("From:"))
        self._date_from = QLineEdit()
        self._date_from.setPlaceholderText("YYYY-MM-DD")
        self._date_from.setFixedWidth(110)
        self._date_from.textChanged.connect(self._populate)
        filt.addWidget(self._date_from)

        filt.addWidget(QLabel("To:"))
        self._date_to = QLineEdit()
        self._date_to.setPlaceholderText("YYYY-MM-DD")
        self._date_to.setFixedWidth(110)
        self._date_to.textChanged.connect(self._populate)
        filt.addWidget(self._date_to)

        self._var_only = QCheckBox("Show variances only")
        self._var_only.stateChanged.connect(self._populate)
        filt.addWidget(self._var_only)

        filt.addStretch(1)
        export_btn = QPushButton("⬇ Export CSV")
        export_btn.clicked.connect(self._on_export)
        filt.addWidget(export_btn)
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self.refresh)
        filt.addWidget(refresh_btn)
        root.addLayout(filt)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:#6B7280;")
        root.addWidget(self._count_lbl)

        self._table = QTableWidget(0, len(_HIST_COLS))
        self._table.setHorizontalHeaderLabels(list(_HIST_COLS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        for c in range(len(_HIST_COLS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        root.addWidget(self._table, 1)

    def refresh(self) -> None:
        sql = """
            SELECT vco.credit_date         AS credit_date,
                   v.name                  AS vendor_name,
                   vr.rga_number           AS rga_number,
                   vco.vendor_credit_number AS vendor_credit_number,
                   p.sku                   AS sku,
                   COALESCE(p.oem_part_number, p.xref_part_number, '') AS part_number,
                   vco.core_amount         AS deposit,
                   COALESCE(vco.quantity,1) AS quantity,
                   COALESCE(vco.credit_received,0) AS credit_received,
                   vco.updated_at          AS closed_at
              FROM vendor_core_obligations vco
              LEFT JOIN vendors v ON v.id = vco.vendor_id
              LEFT JOIN vendor_returns vr ON vr.id = vco.rga_id
              LEFT JOIN products p ON p.id = vco.product_id
             WHERE vco.status = 'credit_received'
             ORDER BY vco.credit_date DESC, vco.id DESC
             LIMIT 5000
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            self._rows = [_r(x) for x in rows]
        except Exception:
            log.exception("Credit history fetch failed")
            self._rows = []
        self._populate()

    def _populate(self) -> None:
        v_q = (self._vendor_filter.text() or "").strip().lower()
        rga_q = (self._rga_filter.text() or "").strip().lower()
        part_q = (self._part_filter.text() or "").strip().lower()
        d_from = (self._date_from.text() or "").strip()
        d_to = (self._date_to.text() or "").strip()
        var_only = self._var_only.isChecked()

        rows = []
        for r in self._rows:
            if v_q and v_q not in str(r.get("vendor_name") or "").lower():
                continue
            if rga_q and rga_q not in str(r.get("rga_number") or "").lower():
                continue
            if part_q and part_q not in (
                str(r.get("part_number") or "").lower()
                + " " + str(r.get("sku") or "").lower()
            ):
                continue
            cd = str(r.get("credit_date") or "")[:10]
            if d_from and cd and cd < d_from:
                continue
            if d_to and cd and cd > d_to:
                continue
            expected = float(r.get("deposit") or 0) * float(r.get("quantity") or 1)
            actual = float(r.get("credit_received") or 0)
            variance = actual - expected
            if var_only and abs(variance) < 0.01:
                continue
            r2 = dict(r)
            r2["_expected"] = expected
            r2["_variance"] = variance
            rows.append(r2)

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._table.setItem(i, 0, cell(str(r.get("credit_date") or "")[:10]))
            self._table.setItem(i, 1, cell(r.get("vendor_name") or ""))
            self._table.setItem(i, 2, cell(r.get("rga_number") or ""))
            self._table.setItem(i, 3, cell(r.get("vendor_credit_number") or ""))
            self._table.setItem(i, 4, cell(r.get("sku") or ""))
            self._table.setItem(i, 5, cell(r.get("part_number") or ""))
            self._table.setItem(i, 6, _NumItem(r["_expected"]))
            self._table.setItem(i, 7, _NumItem(float(r.get("credit_received") or 0)))
            v_item = _NumItem(r["_variance"])
            if abs(r["_variance"]) >= 0.01:
                v_item.setForeground(QColor("#a4453a") if r["_variance"] < 0 else QColor("#2f7a4e"))
            self._table.setItem(i, 8, v_item)
            self._table.setItem(i, 9, cell(str(r.get("closed_at") or "")[:10]))
        self._table.setSortingEnabled(True)
        self._count_lbl.setText(f"{len(rows)} credit(s)")

    def _on_export(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "Export", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Credit History", "vendor_credit_history.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(_HIST_COLS)
                for r in self._rows:
                    expected = float(r.get("deposit") or 0) * float(r.get("quantity") or 1)
                    actual = float(r.get("credit_received") or 0)
                    w.writerow([
                        str(r.get("credit_date") or "")[:10],
                        r.get("vendor_name") or "",
                        r.get("rga_number") or "",
                        r.get("vendor_credit_number") or "",
                        r.get("sku") or "",
                        r.get("part_number") or "",
                        f"{expected:.2f}",
                        f"{actual:.2f}",
                        f"{actual - expected:.2f}",
                        str(r.get("closed_at") or "")[:10],
                    ])
            QMessageBox.information(self, "Export", f"Saved to {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Failed: {exc}")


# ---------------------------------------------------------------------------
# Combined screen
# ---------------------------------------------------------------------------

class VendorReturnsCombinedScreen(QWidget):
    """4-tab vendor returns workflow with shared summary cards."""

    _CARD_DEFS = [
        ("on_shelf",                "On Shelf / Ready",         "#5b6f44"),
        ("awaiting_customer_core",  "Awaiting Customer Core",   "#b08d2e"),
        ("ready_to_ship",           "Ready to Ship",            "#2f7a4e"),
        ("shipped",                 "Shipped",                  "#1f6f8b"),
        ("awaiting_credit",         "Awaiting Credit",          "#5a4e8c"),
        ("credit_received",         "Credit Received",          "#2f7a4e"),
        ("outstanding",             "Outstanding $",            "#a4453a"),
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, _SummaryCard] = {}
        self._build_ui()
        self.refresh()
        try:
            from .core_events import core_events
            core_events.cores_changed.connect(self.refresh)
        except Exception:
            pass

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("Vendor Returns")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        header.addWidget(title)
        header.addStretch(1)
        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        root.addLayout(header)

        # Cards
        cards = QHBoxLayout()
        cards.setSpacing(6)
        for key, label, color in self._CARD_DEFS:
            c = _SummaryCard(label, color)
            cards.addWidget(c, 1)
            self._cards[key] = c
        root.addLayout(cards)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabBar::tab { padding: 8px 16px; font-size: 12px; }"
            "QTabBar::tab:selected { background:#6B8E23; color:white; font-weight:600; }"
        )
        self._ready_tab = _ReadyToShipTab(self)
        self._open_tab = _OpenRGATab(self)
        self._await_tab = _AwaitingCreditTab(self)
        self._hist_tab = _CreditHistoryTab(self)
        self._tabs.addTab(self._ready_tab, "Ready to Ship")
        self._tabs.addTab(self._open_tab,  "Open RGA / Shipment")
        self._tabs.addTab(self._await_tab, "Awaiting Vendor Credit")
        self._tabs.addTab(self._hist_tab,  "Credit History")
        root.addWidget(self._tabs, 1)

    # ── Data ────────────────────────────────────────────────────────
    def refresh(self) -> None:
        counts = {k: 0 for k, _, _ in self._CARD_DEFS}
        dollars = {k: 0.0 for k, _, _ in self._CARD_DEFS}
        sql = """
            SELECT COALESCE(LOWER(status),'') AS status,
                   COALESCE(rga_id, 0) AS rga_id,
                   COALESCE(core_amount, 0) * COALESCE(quantity, 1) AS amt
              FROM vendor_core_obligations
        """
        try:
            with get_db() as conn:
                rows = conn.execute(sql).fetchall()
            for raw in rows:
                r = _r(raw)
                s = r.get("status") or ""
                amt = float(r.get("amt") or 0)
                has_rga = int(r.get("rga_id") or 0) > 0
                if s == "on_shelf":
                    counts["on_shelf"] += 1; dollars["on_shelf"] += amt
                    if not has_rga:
                        counts["ready_to_ship"] += 1
                        dollars["ready_to_ship"] += amt
                elif s == "awaiting_customer_core":
                    counts["awaiting_customer_core"] += 1
                    dollars["awaiting_customer_core"] += amt
                elif s in ("pending_vendor_ship",):
                    if not has_rga:
                        counts["ready_to_ship"] += 1
                        dollars["ready_to_ship"] += amt
                elif s in ("shipped", "vendor_received"):
                    counts["shipped"] += 1; dollars["shipped"] += amt
                    counts["awaiting_credit"] += 1
                    dollars["awaiting_credit"] += amt
                elif s in ("credit_pending",):
                    counts["awaiting_credit"] += 1
                    dollars["awaiting_credit"] += amt
                elif s == "credit_received":
                    counts["credit_received"] += 1
                    dollars["credit_received"] += amt
                # Outstanding = anything not yet credited / written off.
                if s not in ("credit_received", "write_off", "cancelled"):
                    counts["outstanding"] += 1
                    dollars["outstanding"] += amt
        except Exception:
            log.exception("Vendor Returns card refresh failed")

        for key, _label, _color in self._CARD_DEFS:
            self._cards[key].set_values(counts[key], dollars[key])

        # Refresh active tab so the user sees current data.
        try:
            self._ready_tab.refresh()
            self._open_tab.refresh()
            self._await_tab.refresh()
            self._hist_tab.refresh()
        except Exception:
            log.exception("Vendor Returns tab refresh failed")

    def showEvent(self, event):  # noqa: D401
        super().showEvent(event)
        try:
            self.refresh()
        except Exception:
            pass
