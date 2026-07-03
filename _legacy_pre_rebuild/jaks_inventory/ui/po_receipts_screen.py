"""Full warehouse receiving workflow — PO Receipts screen.

Layout: QSplitter (left ~35% / right ~65%)
  Left:  QTabWidget with 4 tabs
           0  Ready to Receive
           1  Draft Receipts
           2  Receipt History
           3  Voided
  Right: Receipt editor panel (header + grid + summary + actions)
         or placeholder label when nothing is selected.
"""
from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_receipt_draft,
    finalize_receipt,
    get_purchase_order,
    get_receipt,
    get_setting,
    list_receivable_pos,
    list_receipts,
    list_locations,
    save_receipt_draft,
    void_receipt,
)
from core.badges import PO_BADGES, badge_colors
from core.format import format_date, format_money

from .po_dialog import PODialog

# ── Column indices for the receiving grid ────────────────────────────────────
_GC_SKU = 0
_GC_DESC = 1
_GC_ORDERED = 2
_GC_PREV = 3
_GC_REMAINING = 4
_GC_RECV = 5
_GC_DAMAGED = 6
_GC_BACKORDER = 7
_GC_LOCATION = 8
_GC_SERIAL = 9
_GC_CORE = 10
_GC_COUNT = 11

# ── Row colours ──────────────────────────────────────────────────────────────
_RED = "#ffcdd2"      # late
_ORANGE = "#ffe0b2"   # partial
_GREEN = "#c8e6c9"    # ready/normal
_FG_DARK = "#212121"


def _ro_item(text: str) -> QTableWidgetItem:
    """Read-only table cell."""
    it = QTableWidgetItem(str(text) if text is not None else "")
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class POReceiptsWorkflow(QWidget):
    """Full-featured PO receiving workflow widget.

    Emits ``receipts_changed`` after any finalize / void so parent screens
    can refresh PO list totals.
    """

    receipts_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_po: dict | None = None
        self._current_receipt_id: int | None = None
        self._po_lines_meta: list[dict] = []   # PO lines used to build grid
        self._locations: list[dict] = []
        self._read_only = False

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ── Left panel ───────────────────────────────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.setFixedHeight(28)
        refresh_btn.clicked.connect(self.refresh)
        left_lay.addWidget(refresh_btn)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_left_tab_changed)

        self._ready_table = self._make_list_table([
            "PO #", "Vendor", "Status", "Expected", "Outstanding", "Linked SOs", "Invoice #", "Priority"
        ])
        self._draft_table = self._make_list_table([
            "Receipt #", "PO #", "Vendor", "Started", "Lines"
        ])
        self._history_table = self._make_list_table([
            "Receipt #", "PO #", "Vendor", "Date", "Received By", "Total"
        ])
        self._voided_table = self._make_list_table([
            "Receipt #", "PO #", "Vendor", "Voided", "Reason"
        ])

        self._tabs.addTab(self._ready_table, "📥 Ready to Receive")
        self._tabs.addTab(self._draft_table, "📝 Draft Receipts")
        self._tabs.addTab(self._history_table, "📦 Receipt History")
        self._tabs.addTab(self._voided_table, "🚫 Voided")

        left_lay.addWidget(self._tabs, 1)
        self._left_status = QLabel("")
        left_lay.addWidget(self._left_status)

        # Connect selection signals
        self._ready_table.itemSelectionChanged.connect(
            lambda: self._on_list_selection(self._ready_table, "ready")
        )
        self._draft_table.itemSelectionChanged.connect(
            lambda: self._on_list_selection(self._draft_table, "draft")
        )
        self._history_table.itemSelectionChanged.connect(
            lambda: self._on_list_selection(self._history_table, "history")
        )
        self._voided_table.itemSelectionChanged.connect(
            lambda: self._on_list_selection(self._voided_table, "voided")
        )

        splitter.addWidget(left)

        # ── Right panel ──────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        placeholder = QLabel("Select a PO or receipt to begin receiving.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #888; font-size: 14px;")
        self._stack.addWidget(placeholder)          # index 0: placeholder

        self._editor = self._build_editor()
        scroll = QScrollArea()
        scroll.setWidget(self._editor)
        scroll.setWidgetResizable(True)
        self._stack.addWidget(scroll)               # index 1: editor

        splitter.addWidget(self._stack)
        splitter.setSizes([380, 820])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(splitter)

        # Initial load
        self.refresh()

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _make_list_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setSortingEnabled(True)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    # ── Left panel loading ───────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload all four tabs and refresh cached locations."""
        try:
            self._locations = list_locations(active_only=True)
        except Exception:
            self._locations = []
        self._load_ready()
        self._load_drafts()
        self._load_history()
        self._load_voided()

    def _load_ready(self) -> None:
        pos = list_receivable_pos()
        t = self._ready_table
        t.setSortingEnabled(False)
        t.setRowCount(len(pos))
        for row, po in enumerate(pos):
            status = (po.get("status") or "").lower()
            priority = po.get("priority", "normal")
            exp = po.get("expected_date") or ""
            if priority == "LATE":
                bg = _RED
            elif status == "partial":
                bg = _ORANGE
            else:
                bg = _GREEN

            cells = [
                po.get("po_number") or "",
                po.get("vendor_name") or "",
                status.upper(),
                format_date(exp),
                str(po.get("outstanding_qty") or 0),
                str(po.get("linked_so_count") or 0),
                po.get("vendor_invoice_number") or "",
                priority,
            ]
            for col, val in enumerate(cells):
                it = _ro_item(val)
                it.setBackground(QColor(bg))
                it.setForeground(QColor(_FG_DARK))
                t.setItem(row, col, it)
            t.item(row, 0).setData(Qt.ItemDataRole.UserRole, po.get("id"))
        t.resizeColumnsToContents()
        t.setSortingEnabled(True)
        self._left_status.setText(f"{len(pos)} PO(s) ready for receiving")

    def _load_drafts(self) -> None:
        receipts = list_receipts(status="draft")
        t = self._draft_table
        t.setSortingEnabled(False)
        t.setRowCount(len(receipts))
        for row, r in enumerate(receipts):
            cells = [
                r.get("receipt_number") or "",
                r.get("po_number") or "",
                r.get("vendor_name") or "",
                format_date(r.get("created_at")),
                "",  # lines count filled below
            ]
            for col, val in enumerate(cells):
                it = _ro_item(val)
                t.setItem(row, col, it)
            t.item(row, 0).setData(Qt.ItemDataRole.UserRole, r.get("id"))
        t.resizeColumnsToContents()
        t.setSortingEnabled(True)

    def _load_history(self) -> None:
        receipts = list_receipts(status="finalized")
        t = self._history_table
        t.setSortingEnabled(False)
        t.setRowCount(len(receipts))
        for row, r in enumerate(receipts):
            total_cost = sum(
                float(ln.get("unit_cost") or 0) * int(ln.get("qty_received") or 0)
                for ln in (r.get("lines") or [])
            ) if "lines" in r else 0
            cells = [
                r.get("receipt_number") or "",
                r.get("po_number") or "",
                r.get("vendor_name") or "",
                format_date(r.get("finalized_at")),
                r.get("finalized_by") or r.get("received_by") or "",
                format_money(total_cost),
            ]
            for col, val in enumerate(cells):
                it = _ro_item(val)
                t.setItem(row, col, it)
            t.item(row, 0).setData(Qt.ItemDataRole.UserRole, r.get("id"))
        t.resizeColumnsToContents()
        t.setSortingEnabled(True)

    def _load_voided(self) -> None:
        receipts = list_receipts(status="voided")
        t = self._voided_table
        t.setSortingEnabled(False)
        t.setRowCount(len(receipts))
        for row, r in enumerate(receipts):
            cells = [
                r.get("receipt_number") or "",
                r.get("po_number") or "",
                r.get("vendor_name") or "",
                format_date(r.get("voided_at")),
                r.get("void_reason") or "",
            ]
            for col, val in enumerate(cells):
                it = _ro_item(val)
                t.setItem(row, col, it)
            t.item(row, 0).setData(Qt.ItemDataRole.UserRole, r.get("id"))
        t.resizeColumnsToContents()
        t.setSortingEnabled(True)

    def _on_left_tab_changed(self, _index: int) -> None:
        if hasattr(self, "_stack"):
            self._stack.setCurrentIndex(0)  # back to placeholder on tab switch

    def _on_list_selection(self, table: QTableWidget, mode: str) -> None:
        row = table.currentRow()
        if row < 0:
            return
        id_item = table.item(row, 0)
        if not id_item:
            return
        obj_id = id_item.data(Qt.ItemDataRole.UserRole)
        if not obj_id:
            return

        if mode == "ready":
            # PO id — create/find draft receipt
            result = create_receipt_draft(po_id=obj_id)
            if not result.get("success"):
                QMessageBox.warning(self, "Error", result.get("error", "Could not create receipt"))
                return
            receipt_id = result["receipt_id"]
            self._load_receipt_editor(receipt_id, read_only=False)
        else:
            # receipt id
            read_only = mode in ("history", "voided")
            self._load_receipt_editor(obj_id, read_only=read_only)

    # ── Editor panel ─────────────────────────────────────────────────────────

    def _build_editor(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # ── Header group ─────────────────────────────────────────────────────
        hdr_group = QGroupBox("Receipt Header")
        hdr_form = QFormLayout(hdr_group)
        hdr_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._hdr_receipt_num = QLabel("—")
        self._hdr_po_num = QLabel("—")
        self._hdr_vendor = QLabel("—")
        self._hdr_po_status = QLabel("—")
        self._hdr_expected = QLabel("—")

        self._hdr_invoice = QLineEdit()
        self._hdr_invoice.setPlaceholderText("Vendor Invoice #")

        self._hdr_packing = QLineEdit()
        self._hdr_packing.setPlaceholderText("Packing Slip #")

        self._hdr_received_by = QLineEdit()
        self._hdr_received_by.setPlaceholderText("Your name")

        self._hdr_date = QDateEdit()
        self._hdr_date.setDate(date.today())
        self._hdr_date.setCalendarPopup(True)
        self._hdr_date.setDisplayFormat("yyyy-MM-dd")

        hdr_form.addRow("Receipt #:", self._hdr_receipt_num)
        hdr_form.addRow("PO #:", self._hdr_po_num)
        hdr_form.addRow("Vendor:", self._hdr_vendor)
        hdr_form.addRow("PO Status:", self._hdr_po_status)
        hdr_form.addRow("Expected:", self._hdr_expected)
        hdr_form.addRow("Vendor Invoice #:", self._hdr_invoice)
        hdr_form.addRow("Packing Slip #:", self._hdr_packing)
        hdr_form.addRow("Received By:", self._hdr_received_by)
        hdr_form.addRow("Receipt Date:", self._hdr_date)

        lay.addWidget(hdr_group)

        # ── Receiving grid ────────────────────────────────────────────────────
        grid_group = QGroupBox("Lines")
        grid_lay = QVBoxLayout(grid_group)
        grid_lay.setContentsMargins(4, 4, 4, 4)

        recv_all_btn = QPushButton("⬇ Receive All")
        recv_all_btn.setFixedHeight(26)
        recv_all_btn.clicked.connect(self._on_receive_all)
        grid_lay.addWidget(recv_all_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._grid = QTableWidget(0, _GC_COUNT)
        self._grid.setHorizontalHeaderLabels([
            "SKU", "Description", "Ordered", "Prev Recv", "Remaining",
            "Receive Now", "Damaged", "Backordered", "Location/Bin", "Serial", "Core $"
        ])
        self._grid.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                   QAbstractItemView.EditTrigger.SelectedClicked)
        self._grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._grid.verticalHeader().setVisible(False)
        self._grid.horizontalHeader().setSectionResizeMode(
            _GC_DESC, QHeaderView.ResizeMode.Stretch
        )
        self._grid.setMinimumHeight(200)
        grid_lay.addWidget(self._grid)
        lay.addWidget(grid_group, 1)

        # ── Summary ───────────────────────────────────────────────────────────
        sum_group = QGroupBox("Landed Cost Summary")
        sum_form = QFormLayout(sum_group)
        sum_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._freight = QDoubleSpinBox()
        self._freight.setRange(0, 99999)
        self._freight.setDecimals(2)
        self._freight.setPrefix("$")
        self._freight.valueChanged.connect(self._update_summary_totals)

        self._duty = QDoubleSpinBox()
        self._duty.setRange(0, 99999)
        self._duty.setDecimals(2)
        self._duty.setPrefix("$")
        self._duty.valueChanged.connect(self._update_summary_totals)

        self._other_fees = QDoubleSpinBox()
        self._other_fees.setRange(0, 99999)
        self._other_fees.setDecimals(2)
        self._other_fees.setPrefix("$")
        self._other_fees.valueChanged.connect(self._update_summary_totals)

        self._landed_cost_label = QLabel("$0.00")
        self._core_total_label = QLabel("$0.00")

        sum_form.addRow("Freight:", self._freight)
        sum_form.addRow("Duty:", self._duty)
        sum_form.addRow("Other Fees:", self._other_fees)
        sum_form.addRow("Landed Cost Total:", self._landed_cost_label)
        sum_form.addRow("Vendor Core Total:", self._core_total_label)

        lay.addWidget(sum_group)

        # ── Actions ───────────────────────────────────────────────────────────
        act_row = QHBoxLayout()
        self._save_draft_btn = QPushButton("💾 Save Draft")
        self._save_draft_btn.clicked.connect(self._on_save_draft)

        self._finalize_btn = QPushButton("✅ Finalize Receipt")
        self._finalize_btn.setStyleSheet("font-weight: bold;")
        self._finalize_btn.clicked.connect(self._on_finalize)

        self._view_po_btn = QPushButton("📋 View PO")
        self._view_po_btn.clicked.connect(self._on_view_po)

        self._void_btn = QPushButton("🚫 Void Receipt")
        self._void_btn.setStyleSheet("color: #c62828;")
        self._void_btn.clicked.connect(self._on_void)

        act_row.addWidget(self._save_draft_btn)
        act_row.addWidget(self._finalize_btn)
        act_row.addStretch()
        act_row.addWidget(self._view_po_btn)
        act_row.addWidget(self._void_btn)
        lay.addLayout(act_row)

        return w

    def _load_receipt_editor(self, receipt_id: int, *, read_only: bool) -> None:
        """Populate the editor panel from a receipt record."""
        receipt = get_receipt(receipt_id)
        if not receipt:
            return

        self._current_receipt_id = receipt_id
        self._read_only = read_only

        # Populate header
        self._hdr_receipt_num.setText(receipt.get("receipt_number") or "—")
        self._hdr_po_num.setText(receipt.get("po_number") or "—")
        self._hdr_vendor.setText(receipt.get("vendor_name") or "—")
        po_status = ""
        if receipt.get("po_id"):
            po_data = get_purchase_order(receipt["po_id"])
            po_status = (po_data or {}).get("status", "")
            self._current_po = po_data
            self._po_lines_meta = (po_data or {}).get("lines", [])
        self._hdr_po_status.setText(po_status.upper() if po_status else "—")
        exp = (self._current_po or {}).get("expected_date") or ""
        self._hdr_expected.setText(format_date(exp))

        inv = receipt.get("vendor_invoice_number") or ""
        self._hdr_invoice.setText(inv)

        ps = receipt.get("packing_slip_number") or ""
        self._hdr_packing.setText(ps)

        rb = receipt.get("received_by") or ""
        self._hdr_received_by.setText(rb)

        rd = receipt.get("received_date") or date.today().isoformat()
        try:
            from PySide6.QtCore import QDate
            self._hdr_date.setDate(QDate.fromString(str(rd)[:10], "yyyy-MM-dd"))
        except Exception:
            pass

        self._freight.setValue(float(receipt.get("freight_amount") or 0))
        self._duty.setValue(float(receipt.get("duty_amount") or 0))
        self._other_fees.setValue(float(receipt.get("other_landed_fees") or 0))

        # Build receiving grid from PO lines, overlaying any saved receipt lines
        saved_by_po_line: dict[int, dict] = {
            ln.get("po_line_id"): ln for ln in (receipt.get("lines") or [])
        }
        self._build_grid(saved_by_po_line, read_only=read_only)
        self._update_summary_totals()

        # Button state
        is_draft = receipt.get("status") == "draft"
        is_finalized = receipt.get("status") == "finalized"
        self._save_draft_btn.setEnabled(is_draft)
        self._finalize_btn.setEnabled(is_draft)
        self._void_btn.setEnabled(is_finalized)

        # Make header fields read-only when needed
        for w in (self._hdr_invoice, self._hdr_packing, self._hdr_received_by):
            w.setReadOnly(read_only or not is_draft)
        self._hdr_date.setEnabled(not read_only and is_draft)
        self._freight.setEnabled(not read_only and is_draft)
        self._duty.setEnabled(not read_only and is_draft)
        self._other_fees.setEnabled(not read_only and is_draft)

        self._stack.setCurrentIndex(1)

    def _build_grid(self, saved: dict[int, dict], *, read_only: bool) -> None:
        """Populate the receiving grid from PO lines + any saved receipt line qtys."""
        po_lines = self._po_lines_meta
        self._grid.setRowCount(0)
        self._grid.setRowCount(len(po_lines))

        loc_options = [("", "— No location —")] + [
            (loc.get("id"), f"{loc.get('code','?')} — {loc.get('name','')}")
            for loc in self._locations
        ]

        for row, pl in enumerate(po_lines):
            ordered = int(pl.get("quantity_ordered") or 0)
            prev = int(pl.get("quantity_received") or 0)
            remaining = max(ordered - prev, 0)
            saved_ln = saved.get(pl.get("id")) or {}
            recv_qty = int(saved_ln.get("qty_received") or 0)
            damaged = int(saved_ln.get("qty_damaged") or 0)
            backorder = int(saved_ln.get("qty_backordered") or 0)
            serial = saved_ln.get("serial_number") or ""
            core_amt = float(saved_ln.get("vendor_core_amount") or pl.get("core_charge") or 0)

            self._grid.setItem(row, _GC_SKU, _ro_item(pl.get("sku") or ""))
            self._grid.setItem(row, _GC_DESC, _ro_item(pl.get("title") or pl.get("description") or ""))
            self._grid.setItem(row, _GC_ORDERED, _ro_item(str(ordered)))
            self._grid.setItem(row, _GC_PREV, _ro_item(str(prev)))
            self._grid.setItem(row, _GC_REMAINING, _ro_item(str(remaining)))

            # Editable spinboxes
            recv_spin = QSpinBox()
            recv_spin.setRange(0, max(remaining, recv_qty))
            recv_spin.setValue(recv_qty)
            recv_spin.setEnabled(not read_only and remaining > 0)
            recv_spin.valueChanged.connect(self._update_summary_totals)
            self._grid.setCellWidget(row, _GC_RECV, recv_spin)

            dmg_spin = QSpinBox()
            dmg_spin.setRange(0, 9999)
            dmg_spin.setValue(damaged)
            dmg_spin.setEnabled(not read_only)
            self._grid.setCellWidget(row, _GC_DAMAGED, dmg_spin)

            bo_spin = QSpinBox()
            bo_spin.setRange(0, 9999)
            bo_spin.setValue(backorder)
            bo_spin.setEnabled(not read_only)
            self._grid.setCellWidget(row, _GC_BACKORDER, bo_spin)

            # Location combo
            loc_combo = QComboBox()
            saved_loc = saved_ln.get("location_id")
            for loc_id, loc_label in loc_options:
                loc_combo.addItem(loc_label, loc_id)
                if saved_loc and loc_id == saved_loc:
                    loc_combo.setCurrentIndex(loc_combo.count() - 1)
            loc_combo.setEnabled(not read_only)
            self._grid.setCellWidget(row, _GC_LOCATION, loc_combo)

            # Serial
            serial_edit = QLineEdit(serial)
            serial_edit.setEnabled(not read_only)
            serial_edit.setPlaceholderText("S/N")
            self._grid.setCellWidget(row, _GC_SERIAL, serial_edit)

            # Core amount
            core_spin = QDoubleSpinBox()
            core_spin.setRange(0, 99999)
            core_spin.setDecimals(2)
            core_spin.setPrefix("$")
            core_spin.setValue(core_amt)
            core_spin.setEnabled(not read_only)
            core_spin.valueChanged.connect(self._update_summary_totals)
            self._grid.setCellWidget(row, _GC_CORE, core_spin)

            # Store po_line_id and product_id in the SKU item's UserRole
            sku_item = self._grid.item(row, _GC_SKU)
            sku_item.setData(Qt.ItemDataRole.UserRole, {
                "po_line_id": pl.get("id"),
                "product_id": pl.get("product_id"),
                "unit_cost": float(pl.get("unit_cost") or 0),
                "sku": pl.get("sku") or "",
                "description": pl.get("title") or pl.get("description") or "",
            })

        self._grid.resizeColumnsToContents()

    # ── Summary helpers ──────────────────────────────────────────────────────

    def _update_summary_totals(self) -> None:
        extra = self._freight.value() + self._duty.value() + self._other_fees.value()
        line_cost = 0.0
        core_total = 0.0
        for row in range(self._grid.rowCount()):
            meta_item = self._grid.item(row, _GC_SKU)
            if not meta_item:
                continue
            meta = meta_item.data(Qt.ItemDataRole.UserRole) or {}
            recv_w = self._grid.cellWidget(row, _GC_RECV)
            core_w = self._grid.cellWidget(row, _GC_CORE)
            qty = recv_w.value() if recv_w else 0
            core = core_w.value() if core_w else 0
            line_cost += float(meta.get("unit_cost", 0)) * qty
            core_total += core * qty

        self._landed_cost_label.setText(format_money(line_cost + extra))
        self._core_total_label.setText(format_money(core_total))

    # ── Action handlers ──────────────────────────────────────────────────────

    def _on_receive_all(self) -> None:
        """Fill all Receive Now spinboxes to their remaining quantity."""
        for row in range(self._grid.rowCount()):
            remaining_item = self._grid.item(row, _GC_REMAINING)
            recv_w = self._grid.cellWidget(row, _GC_RECV)
            if remaining_item and recv_w and isinstance(recv_w, QSpinBox):
                rem = int(remaining_item.text() or 0)
                if rem > 0 and recv_w.isEnabled():
                    recv_w.setValue(rem)

    def _collect_lines(self) -> list[dict]:
        """Collect receipt line data from the grid."""
        lines = []
        for row in range(self._grid.rowCount()):
            meta_item = self._grid.item(row, _GC_SKU)
            if not meta_item:
                continue
            meta = meta_item.data(Qt.ItemDataRole.UserRole) or {}
            recv_w = self._grid.cellWidget(row, _GC_RECV)
            dmg_w = self._grid.cellWidget(row, _GC_DAMAGED)
            bo_w = self._grid.cellWidget(row, _GC_BACKORDER)
            loc_w = self._grid.cellWidget(row, _GC_LOCATION)
            ser_w = self._grid.cellWidget(row, _GC_SERIAL)
            core_w = self._grid.cellWidget(row, _GC_CORE)

            lines.append({
                "po_line_id": meta.get("po_line_id"),
                "product_id": meta.get("product_id"),
                "sku": meta.get("sku"),
                "description": meta.get("description"),
                "unit_cost": meta.get("unit_cost", 0),
                "qty_received": recv_w.value() if recv_w else 0,
                "qty_damaged": dmg_w.value() if dmg_w else 0,
                "qty_backordered": bo_w.value() if bo_w else 0,
                "location_id": loc_w.currentData() if loc_w else None,
                "serial_number": ser_w.text() if ser_w else "",
                "vendor_core_amount": core_w.value() if core_w else 0,
            })
        return lines

    def _collect_header(self) -> dict:
        return {
            "vendor_invoice_number": self._hdr_invoice.text().strip() or None,
            "packing_slip_number": self._hdr_packing.text().strip() or None,
            "received_by": self._hdr_received_by.text().strip() or None,
            "received_date": self._hdr_date.date().toString("yyyy-MM-dd"),
            "freight_amount": self._freight.value(),
            "duty_amount": self._duty.value(),
            "other_landed_fees": self._other_fees.value(),
        }

    def _on_save_draft(self) -> None:
        if not self._current_receipt_id:
            return
        result = save_receipt_draft(
            self._current_receipt_id,
            header=self._collect_header(),
            lines=self._collect_lines(),
        )
        if result.get("success"):
            QMessageBox.information(self, "Saved", "Receipt draft saved.")
            self._load_drafts()
        else:
            QMessageBox.warning(self, "Save Failed", result.get("error", "Unknown error"))

    def _on_finalize(self) -> None:
        if not self._current_receipt_id:
            return
        lines = self._collect_lines()
        total_recv = sum(ln["qty_received"] for ln in lines)
        if total_recv == 0:
            QMessageBox.warning(
                self, "Nothing to Receive",
                "Enter at least one quantity in the 'Receive Now' column before finalizing."
            )
            return

        # Auto-save first
        save_receipt_draft(
            self._current_receipt_id,
            header=self._collect_header(),
            lines=lines,
        )

        reply = QMessageBox.question(
            self, "Finalize Receipt",
            f"Finalize this receipt?\n\nThis will update inventory for {total_recv} unit(s) "
            "and cannot be undone (only voided).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        rb = self._hdr_received_by.text().strip() or None
        result = finalize_receipt(self._current_receipt_id, finalized_by=rb)
        if result.get("success"):
            msg = f"Receipt {result.get('receipt_number')} finalized.\nPO status: {result.get('po_status','')}"
            if result.get("errors"):
                msg += "\n\nWarnings:\n" + "\n".join(result["errors"])
            QMessageBox.information(self, "Finalized", msg)
            self.refresh()
            # Re-open in read-only history view
            self._load_receipt_editor(self._current_receipt_id, read_only=True)
            self.receipts_changed.emit()
        else:
            QMessageBox.warning(self, "Finalize Failed", result.get("error", "Unknown error"))

    def _on_view_po(self) -> None:
        po = self._current_po
        if not po:
            return
        dlg = PODialog(po_id=po["id"], parent=self)
        dlg.exec()
        # Reload the editor to pick up any changes made in PODialog
        if self._current_receipt_id:
            self._load_receipt_editor(self._current_receipt_id, read_only=self._read_only)
        self.refresh()

    def _on_void(self) -> None:
        if not self._current_receipt_id:
            return
        receipt = get_receipt(self._current_receipt_id)
        if not receipt or receipt.get("status") != "finalized":
            QMessageBox.warning(self, "Cannot Void", "Only finalized receipts can be voided.")
            return

        # Ask for reason
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QTextEdit
        dlg = QDialog(self)
        dlg.setWindowTitle("Void Receipt")
        dlg.setMinimumWidth(400)
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel(
            f"<b>Void receipt {receipt.get('receipt_number')}?</b><br>"
            "This will reverse all inventory changes. Enter a reason:"
        ))
        reason_edit = QTextEdit()
        reason_edit.setMaximumHeight(80)
        vl.addWidget(reason_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vl.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        reason = reason_edit.toPlainText().strip() or "No reason given"

        result = void_receipt(self._current_receipt_id, reason=reason)
        if result.get("success"):
            QMessageBox.information(
                self, "Voided",
                f"Receipt {result.get('receipt_number')} has been voided and inventory reversed."
            )
            self.refresh()
            self._stack.setCurrentIndex(0)
            self.receipts_changed.emit()
        else:
            QMessageBox.warning(self, "Void Failed", result.get("error", "Unknown error"))
