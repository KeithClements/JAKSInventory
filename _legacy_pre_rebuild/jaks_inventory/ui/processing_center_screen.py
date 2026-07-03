"""Processing Dashboard Screen.

Overview-only screen for the Core Processing workflow. Shows 5 KPI tiles
and a single "Needs Attention" table.  The kanban that previously lived
here has been retired; per-phase detail lives in the dedicated
Customer Cores and Vendor Returns screens.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.core_handling import (
    UNIFIED_PHASES,
    core_processing_kpis,
    list_unified_cores,
)


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

_TILE_COLORS = {
    "overdue":          "#a4453a",  # red
    "received_ready":   "#5b6f44",  # olive
    "awaiting_credit":  "#5a4e8c",  # purple
    "closed_30d":       "#2f7a4e",  # green
    "written_off":      "#7a6a3a",  # mute amber
}

_TILE_LABELS = {
    "overdue":          "Overdue",
    "received_ready":   "Received / Not Shipped",
    "awaiting_credit":  "Shipped — Awaiting Credit",
    "closed_30d":       "Closed (30 days)",
    "written_off":      "Written Off",
}

_COLUMN_COLORS = {
    "awaiting_customer": "#b08d2e",
    "received_ready":    "#5b6f44",
    "in_rga":            "#1f6f8b",
    "awaiting_credit":   "#5a4e8c",
    "closed":            "#2f7a4e",
}

# Tile filter → phase column key (None = "all overdue, no phase filter").
_TILE_TO_PHASE = {
    "overdue":          None,
    "received_ready":   "received_ready",
    "awaiting_credit":  "awaiting_credit",
    "closed_30d":       "closed",
    "written_off":      None,  # written-off is excluded from unified list
}


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class _KPITile(QFrame):
    """Clickable KPI tile. Emits ``clicked`` with its key."""

    clicked = Signal(str)

    def __init__(self, key: str, label: str, color: str,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._key = key
        self._active = False
        self._color = color
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(80)
        self._apply_style()

        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("color: white; font-size: 10pt;")
        self._val = QLabel("0")
        self._val.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("$0.00")
        self._sub.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 9pt;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._sub)

    def _apply_style(self) -> None:
        border = "3px solid #f5f7e8" if self._active else "3px solid transparent"
        self.setStyleSheet(
            f"background: {self._color}; border-radius: 6px;"
            f" padding: 4px; border: {border};"
        )

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def set_values(self, count: int, dollars: float) -> None:
        self._val.setText(f"{count:,}")
        self._sub.setText(f"${dollars:,.2f}")

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(ev)


class _AttentionRow(QTableWidgetItem):
    """Sortable wrapper that stores the originating row dict via UserRole."""

    def __init__(self, text: str, sort_value=None):
        super().__init__(text)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._sort = sort_value if sort_value is not None else text

    def __lt__(self, other):
        try:
            return self._sort < other._sort
        except Exception:
            return super().__lt__(other)



class CoreDetailPanel(QWidget):
    """Right-side drawer body shown inside a QDockWidget.

    Action buttons surface phase-appropriate transitions; the actual
    state changes are delegated to existing flows.
    """

    refresh_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._row: Optional[dict] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        self._title = QLabel("Select a core to view details")
        f = QFont(); f.setPointSize(12); f.setBold(True)
        self._title.setFont(f)
        self._title.setWordWrap(True)
        outer.addWidget(self._title)

        self._phase_badge = QLabel("")
        self._phase_badge.setStyleSheet(
            "background: #444; color: white; padding: 2px 8px;"
            " border-radius: 8px; font-size: 9pt;"
        )
        self._phase_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._phase_badge.setMaximumWidth(220)
        outer.addWidget(self._phase_badge)

        body = QFrame()
        self._form = QFormLayout(body)
        self._form.setContentsMargins(0, 8, 0, 8)
        outer.addWidget(body)

        # Action button row(s).
        self._action_box = QVBoxLayout()
        self._action_box.setSpacing(4)
        outer.addLayout(self._action_box)
        outer.addStretch(1)

        # Placeholder rows in the form.
        self._fields: dict[str, QLabel] = {}
        for label_key, label_text in [
            ("sku",        "SKU:"),
            ("customer",   "Customer:"),
            ("invoice",    "Invoice:"),
            ("vendor",     "Vendor:"),
            ("rga",        "RGA:"),
            ("deposit",    "Deposit:"),
            ("credit",     "Credit:"),
            ("status_cr",  "Customer status:"),
            ("status_vco", "Vendor status:"),
            ("due",        "Due date:"),
            ("age",        "Age:"),
        ]:
            lbl = QLabel("—")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._form.addRow(QLabel(label_text), lbl)
            self._fields[label_key] = lbl

    def clear(self) -> None:
        self._row = None
        self._title.setText("Select a core to view details")
        self._phase_badge.setText("")
        for v in self._fields.values():
            v.setText("—")
        self._clear_actions()

    def set_row(self, row: dict) -> None:
        self._row = row
        sku = row.get("sku") or "(no SKU)"
        self._title.setText(f"{sku} — {row.get('description') or ''}".strip(" —"))
        phase = row.get("lifecycle_phase") or "?"
        phase_label = dict(UNIFIED_PHASES).get(phase, phase)
        self._phase_badge.setText(phase_label)
        color = _COLUMN_COLORS.get(phase, "#444")
        self._phase_badge.setStyleSheet(
            f"background: {color}; color: white; padding: 2px 10px;"
            " border-radius: 8px; font-size: 9pt; font-weight: bold;"
        )
        self._fields["sku"].setText(sku)
        self._fields["customer"].setText(row.get("customer_name") or "—")
        self._fields["invoice"].setText(row.get("invoice_number") or "—")
        self._fields["vendor"].setText(row.get("vendor_name") or "—")
        self._fields["rga"].setText(row.get("rga_number") or "—")
        self._fields["deposit"].setText(f"${float(row.get('deposit') or 0):,.2f}")
        credit_amt = float(row.get("credit_received") or 0)
        self._fields["credit"].setText(
            f"${credit_amt:,.2f}" if credit_amt else "—"
        )
        self._fields["status_cr"].setText(row.get("cr_status") or "—")
        self._fields["status_vco"].setText(row.get("vco_status") or "—")
        self._fields["due"].setText(row.get("due_date") or "—")
        age = int(row.get("age_days") or 0)
        self._fields["age"].setText(
            f"{age}d  ⚠ overdue" if row.get("is_overdue") else f"{age}d"
        )
        self._build_phase_actions(phase)

    # ----- actions -----
    def _clear_actions(self) -> None:
        while self._action_box.count():
            item = self._action_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _build_phase_actions(self, phase: str) -> None:
        self._clear_actions()
        actions: list[tuple[str, str]] = []
        if phase == "awaiting_customer":
            actions = [("Receive Core", "receive")]
        elif phase == "received_ready":
            actions = [("Add to Vendor RGA", "add_to_rga")]
        elif phase == "in_rga":
            actions = [("Pack & Ship", "pack_ship")]
        elif phase == "awaiting_credit":
            actions = [("Apply Vendor Credit", "apply_credit")]
        elif phase == "closed":
            actions = [("View Credit", "view_credit")]
        for label, key in actions:
            btn = QPushButton(label)
            btn.setStyleSheet("padding: 6px 12px;")
            btn.clicked.connect(lambda _=False, k=key: self._on_action(k))
            self._action_box.addWidget(btn)

    def _on_action(self, key: str) -> None:
        # v1: simple stub — tells the user which existing screen to use.
        # Real wiring happens in later phases (H.3–H.5) when those
        # screens exist.  This keeps H.2 functional without blocking on
        # the other phases.
        hints = {
            "receive":      "Open the Customer Cores screen and Receive this core there.",
            "add_to_rga":   "Open Vendor Cores → board → add this obligation to an RGA.",
            "pack_ship":    "Open RGA Shipments → select the RGA → Pack & Ship.",
            "apply_credit": "Open Vendor Credits → enter the credit details.",
            "view_credit":  "Open Vendor Credits to see the credit ledger entry.",
        }
        QMessageBox.information(
            self,
            "Action",
            hints.get(key, "Action not wired in v1."),
        )


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class ProcessingCenterScreen(QWidget):
    """Top-level Processing Dashboard widget.

    Layout: header → 5 KPI tiles → "Needs Attention" table → side detail
    panel.  Drill-down lives on the dedicated Customer Cores and Vendor
    Returns screens.
    """

    _ATTN_COLUMNS = (
        "Priority", "Status", "SKU", "Part", "Customer",
        "Vendor", "Amount", "Age", "Next Action",
    )

    # Map lifecycle_phase → (next-action label, sort weight).
    _NEXT_ACTION = {
        "awaiting_customer": "Receive Core",
        "received_ready":    "Add to RGA",
        "in_rga":            "Pack & Ship",
        "awaiting_credit":   "Apply Vendor Credit",
        "closed":            "—",
    }

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self._current_tile_key: Optional[str] = None
        self._all_rows: list[dict] = []
        self._displayed_rows: list[dict] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main area (header + tiles + table).
        main = QWidget()
        root.addWidget(main, 1)

        layout = QVBoxLayout(main)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ----- header -----
        hdr = QHBoxLayout()
        title = QLabel("Processing Dashboard")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        self._new_btn = QPushButton("+ New Core")
        self._new_btn.setStyleSheet(
            "background: #cdd944; color: #2a3225; font-weight: bold;"
            " padding: 6px 14px; border-radius: 4px;"
        )
        self._new_btn.clicked.connect(self._on_new_core)
        hdr.addWidget(self._new_btn)
        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(self.refresh)
        hdr.addWidget(refresh)
        layout.addLayout(hdr)

        # ----- KPI tiles -----
        tiles_row = QHBoxLayout()
        tiles_row.setSpacing(8)
        self._tiles: dict[str, _KPITile] = {}
        for key, label in _TILE_LABELS.items():
            tile = _KPITile(key, label, _TILE_COLORS.get(key, "#444"))
            tile.clicked.connect(self._on_tile_clicked)
            tiles_row.addWidget(tile, 1)
            self._tiles[key] = tile
        layout.addLayout(tiles_row)

        # ----- Needs Attention table -----
        attn_label = QLabel("Needs Attention")
        f = QFont(); f.setPointSize(11); f.setBold(True)
        attn_label.setFont(f)
        attn_label.setStyleSheet("color: #2a3225; margin-top: 4px;")
        layout.addWidget(attn_label)

        self._table = QTableWidget(0, len(self._ATTN_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(self._ATTN_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        hdr_view = self._table.horizontalHeader()
        for c in range(len(self._ATTN_COLUMNS)):
            hdr_view.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr_view.setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self._table, 1)

        self._empty_lbl = QLabel("Nothing needs attention right now. \U0001F389")
        self._empty_lbl.setStyleSheet("color: #888; padding: 18px;")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setVisible(False)
        layout.addWidget(self._empty_lbl)

        # ----- detail drawer (in-screen side panel) -----
        self._detail = CoreDetailPanel()
        self._detail.refresh_requested.connect(self.refresh)
        self._detail_frame = QFrame()
        self._detail_frame.setStyleSheet(
            "QFrame { background: #f9f9f4; border-left: 1px solid #d8d6c8; }"
        )
        self._detail_frame.setMinimumWidth(320)
        self._detail_frame.setMaximumWidth(420)
        df_lay = QVBoxLayout(self._detail_frame)
        df_lay.setContentsMargins(0, 0, 0, 0)
        df_lay.addWidget(self._detail)
        root.addWidget(self._detail_frame, 0)

        # Initial load.
        self.refresh()

    # ------------------------------------------------------------------
    def showEvent(self, ev) -> None:  # type: ignore[override]
        super().showEvent(ev)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            kpis = core_processing_kpis()
        except Exception as e:
            print(f"[ProcessingDashboard] KPI load failed: {e}")
            kpis = {}
        for key, tile in self._tiles.items():
            bucket = kpis.get(key) or {"count": 0, "dollars": 0.0}
            tile.set_values(int(bucket.get("count") or 0),
                            float(bucket.get("dollars") or 0.0))

        try:
            self._all_rows = list_unified_cores(
                include_closed=True, limit=2000,
            )
        except Exception as e:
            print(f"[ProcessingDashboard] unified list load failed: {e}")
            self._all_rows = []

        self._apply_filter()

    # ------------------------------------------------------------------
    @classmethod
    def _priority_for(cls, row: dict) -> tuple[str, int, QColor]:
        """Return (label, rank, color) where lower rank = higher priority."""
        phase = row.get("lifecycle_phase") or ""
        if row.get("is_overdue"):
            return ("High", 0, QColor("#a4453a"))
        if phase in ("received_ready", "in_rga"):
            return ("Med", 1, QColor("#b08d2e"))
        if phase == "awaiting_credit":
            return ("Low", 2, QColor("#5a4e8c"))
        return ("Low", 3, QColor("#6B7280"))

    def _apply_filter(self) -> None:
        rows = list(self._all_rows)

        # Tile filter — show ONLY items that need attention. Closed rows
        # are always excluded from the Needs Attention table.
        if self._current_tile_key == "overdue":
            rows = [r for r in rows if r.get("is_overdue")]
        elif self._current_tile_key == "received_ready":
            rows = [r for r in rows if r.get("lifecycle_phase") == "received_ready"]
        elif self._current_tile_key == "awaiting_credit":
            rows = [r for r in rows if r.get("lifecycle_phase") == "awaiting_credit"]
        elif self._current_tile_key == "closed_30d":
            rows = [r for r in rows if r.get("lifecycle_phase") == "closed"]
        elif self._current_tile_key == "written_off":
            rows = []
        else:
            # Default view: drop closed bucket.
            rows = [r for r in rows if r.get("lifecycle_phase") != "closed"]

        # Sort by priority then age desc.
        def sort_key(r):
            _, rank, _ = self._priority_for(r)
            return (rank, -int(r.get("age_days") or 0))
        rows.sort(key=sort_key)
        self._displayed_rows = rows
        self._populate_table(rows)

    # ------------------------------------------------------------------
    def _populate_table(self, rows: list[dict]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        phase_labels = dict(UNIFIED_PHASES)
        for i, r in enumerate(rows):
            pri_label, pri_rank, pri_color = self._priority_for(r)
            phase = r.get("lifecycle_phase") or ""
            status_label = phase_labels.get(phase, phase or "—")
            sku = r.get("sku") or ""
            part = r.get("part_number") or ""
            cust = r.get("customer_name") or ""
            vendor = r.get("vendor_name") or ""
            amount = float(r.get("expected_credit") or r.get("deposit") or 0)
            age = int(r.get("age_days") or 0)
            next_act = self._NEXT_ACTION.get(phase, "—")

            # Priority cell with colored badge text.
            pri_item = _AttentionRow(pri_label, sort_value=pri_rank)
            pri_item.setForeground(pri_color)
            pri_font = QFont(); pri_font.setBold(True)
            pri_item.setFont(pri_font)
            pri_item.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(i, 0, pri_item)

            self._table.setItem(i, 1, _AttentionRow(status_label))
            self._table.setItem(i, 2, _AttentionRow(sku))
            self._table.setItem(i, 3, _AttentionRow(part))
            self._table.setItem(i, 4, _AttentionRow(cust))
            self._table.setItem(i, 5, _AttentionRow(vendor))
            amt_item = _AttentionRow(f"${amount:,.2f}", sort_value=amount)
            amt_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 6, amt_item)
            age_item = _AttentionRow(f"{age}d", sort_value=age)
            age_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if r.get("is_overdue"):
                age_item.setForeground(QColor("#a4453a"))
            self._table.setItem(i, 7, age_item)
            self._table.setItem(i, 8, _AttentionRow(next_act))

        self._table.setSortingEnabled(True)
        self._empty_lbl.setVisible(len(rows) == 0)
        self._table.setVisible(len(rows) > 0)

    # ------------------------------------------------------------------
    def _on_tile_clicked(self, key: str) -> None:
        if self._current_tile_key == key:
            self._current_tile_key = None
        else:
            self._current_tile_key = key
        for k, t in self._tiles.items():
            t.set_active(k == self._current_tile_key)
        self._apply_filter()

    def _on_row_selected(self) -> None:
        row_idx = self._table.currentRow()
        if row_idx < 0:
            self._detail.clear()
            return
        item = self._table.item(row_idx, 0)
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, dict):
            self._detail.set_row(row)

    def _on_new_core(self) -> None:
        try:
            dlg = NewCoreDialog(self)
        except Exception as exc:
            QMessageBox.critical(self, "New Core", f"Could not open dialog:\n{exc}")
            return
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()


# ---------------------------------------------------------------------------
# New Core dialog (Phase H.6)
# ---------------------------------------------------------------------------
class NewCoreDialog(QDialog):
    """Minimal Customer → Invoice → Line picker that creates a customer-core
    return via ``db.inventory.create_core_return``.

    Iteration target: a richer flow that reuses the in-invoice core picker
    (`process_core_return_for_invoice`). For v1 this dialog is intentionally
    small so it ships with the rest of the Phase H cluster.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Customer Core")
        self.setMinimumWidth(520)
        self._customers: list[dict] = []
        self._invoices: list[dict] = []
        self._lines: list[dict] = []
        self._build_ui()
        self._load_customers()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._customer_combo = QComboBox()
        self._customer_combo.setEditable(True)
        self._customer_combo.currentIndexChanged.connect(self._on_customer_changed)
        form.addRow("Customer:", self._customer_combo)

        self._invoice_combo = QComboBox()
        self._invoice_combo.currentIndexChanged.connect(self._on_invoice_changed)
        form.addRow("Invoice:", self._invoice_combo)

        self._line_combo = QComboBox()
        self._line_combo.currentIndexChanged.connect(self._on_line_changed)
        form.addRow("Line Item:", self._line_combo)

        self._charge_spin = QDoubleSpinBox()
        self._charge_spin.setRange(0.0, 100_000.0)
        self._charge_spin.setDecimals(2)
        self._charge_spin.setPrefix("$ ")
        form.addRow("Core Deposit:", self._charge_spin)

        self._due_edit = QLineEdit()
        self._due_edit.setPlaceholderText("YYYY-MM-DD (optional)")
        form.addRow("Due Date:", self._due_edit)

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("Notes (optional)")
        form.addRow("Notes:", self._notes_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── Data loaders ────────────────────────────────────────────────────
    def _load_customers(self) -> None:
        try:
            from db.database import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, name, COALESCE(company,'') AS company "
                    "FROM customers ORDER BY name LIMIT 1000"
                ).fetchall()
            self._customers = [
                dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows
            ]
        except Exception:
            self._customers = []
        self._customer_combo.clear()
        self._customer_combo.addItem("— select customer —", None)
        for c in self._customers:
            label = c.get("name") or ""
            company = c.get("company") or ""
            if company:
                label = f"{label}  ({company})"
            self._customer_combo.addItem(label, int(c["id"]))

    def _on_customer_changed(self, _idx: int) -> None:
        cust_id = self._customer_combo.currentData()
        self._invoice_combo.clear()
        self._line_combo.clear()
        if not cust_id:
            return
        try:
            from db.database import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, COALESCE(invoice_number, '#' || id) AS invoice_number, "
                    "       COALESCE(invoice_date, '') AS invoice_date "
                    "FROM invoices WHERE customer_id = ? "
                    "ORDER BY id DESC LIMIT 200",
                    (int(cust_id),),
                ).fetchall()
            self._invoices = [
                dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows
            ]
        except Exception:
            self._invoices = []
        self._invoice_combo.addItem("— select invoice —", None)
        for inv in self._invoices:
            label = f"{inv.get('invoice_number')}  {inv.get('invoice_date') or ''}".strip()
            self._invoice_combo.addItem(label, int(inv["id"]))

    def _on_invoice_changed(self, _idx: int) -> None:
        inv_id = self._invoice_combo.currentData()
        self._line_combo.clear()
        self._lines = []
        if not inv_id:
            return
        try:
            from db.database import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT il.id, il.product_id, il.description, il.quantity, "
                    "       COALESCE(il.core_charge, 0) AS core_charge, "
                    "       COALESCE(p.sku, '') AS sku "
                    "FROM invoice_lines il "
                    "LEFT JOIN products p ON p.id = il.product_id "
                    "WHERE il.invoice_id = ? "
                    "ORDER BY il.id",
                    (int(inv_id),),
                ).fetchall()
            self._lines = [
                dict(r) if hasattr(r, "keys") else dict(enumerate(r)) for r in rows
            ]
        except Exception:
            self._lines = []
        self._line_combo.addItem("— select line —", None)
        for ln in self._lines:
            label = f"{ln.get('sku') or ''} — {ln.get('description') or ''} (qty {ln.get('quantity')})".strip()
            self._line_combo.addItem(label, int(ln["id"]))

    def _on_line_changed(self, _idx: int) -> None:
        line_id = self._line_combo.currentData()
        if not line_id:
            return
        line = next((l for l in self._lines if int(l["id"]) == int(line_id)), None)
        if line:
            self._charge_spin.setValue(float(line.get("core_charge") or 0))

    # ── Accept ──────────────────────────────────────────────────────────
    def _on_accept(self) -> None:
        cust_id = self._customer_combo.currentData()
        inv_id = self._invoice_combo.currentData()
        line_id = self._line_combo.currentData()
        if not (cust_id and inv_id and line_id):
            QMessageBox.warning(self, "New Core", "Select customer, invoice, and line.")
            return
        line = next((l for l in self._lines if int(l["id"]) == int(line_id)), None)
        if not line:
            QMessageBox.warning(self, "New Core", "Invalid line selection.")
            return

        try:
            from db.inventory import create_core_return
            new_id = create_core_return(
                invoice_line_id=int(line_id),
                invoice_id=int(inv_id),
                product_id=int(line.get("product_id")) if line.get("product_id") else None,
                customer_id=int(cust_id),
                core_charge=float(self._charge_spin.value()),
                due_date=(self._due_edit.text().strip() or None),
                notes=(self._notes_edit.text().strip() or None),
            )
        except Exception as exc:
            QMessageBox.critical(self, "New Core", f"Failed to create core return:\n{exc}")
            return
        QMessageBox.information(self, "New Core", f"Core return #{new_id} created.")
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass
        self.accept()
