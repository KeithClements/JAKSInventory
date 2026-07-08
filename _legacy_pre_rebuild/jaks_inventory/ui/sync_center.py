"""QBO Sync Center — Phase 6 consolidated sync dashboard.

A single screen that replaces the scattered "Sync X" buttons in the
Settings → QBO tab with one entity-grid view. Each row shows the
count of local rows for that entity, how many have a QBO crosswalk
id, pending and failed sync-log counts, last sync timestamp, and
per-row actions (Push / Pull / Replay-Failed / View Failures).

Per-entity enable toggles are persisted under settings as
``qbo.entity.<key>.enabled`` (default ``1``); existing background
sync timers and Settings-tab push buttons should check this flag
before running, so the user can disable an entity without losing
its configuration.

The screen is intentionally lightweight: it reads counts from the
DB and the sync-log table on demand. Heavy push/pull operations
still delegate to ``qbo.integration`` / ``qbo.push`` helpers so the
business logic stays in one place.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entity registry — single source of truth for what the Sync Center shows.
# ---------------------------------------------------------------------------

class _Entity:
    """One row in the Sync Center grid."""

    __slots__ = ("key", "label", "table", "qbo_id_col", "direction",
                 "log_object_type")

    def __init__(self, key: str, label: str, table: str | None,
                 qbo_id_col: str | None, direction: str,
                 log_object_type: str | None) -> None:
        self.key = key
        self.label = label
        self.table = table
        self.qbo_id_col = qbo_id_col
        self.direction = direction
        self.log_object_type = log_object_type


_ENTITIES: list[_Entity] = [
    _Entity("item",         "Items / Products",   "products",        "qbo_id",                 "Both", "Item"),
    _Entity("customer",     "Customers",          "customers",       "qbo_id",                 "Both", "Customer"),
    _Entity("vendor",       "Vendors",            "vendors",         "qbo_id",                 "Both", "Vendor"),
    _Entity("invoice",      "Invoices",           "invoices",        "qbo_invoice_id",         "Push", "Invoice"),
    _Entity("payment",      "Payments",           "invoices",        "qbo_payment_id",         "Push", "Payment"),
    _Entity("credit_memo",  "Credit Memos",       "customer_credits","qbo_credit_memo_id",     "Push", "CreditMemo"),
    _Entity("estimate",     "Estimates (Quotes)", "quotes",          "qbo_estimate_id",        "Both", "Estimate"),
    _Entity("bill",         "Bills (POs)",        "purchase_orders", "qbo_bill_id",            "Push", "Bill"),
    _Entity("journal",      "Journal Entries",    "invoices",        "qbo_journal_entry_id",   "Push", "JournalEntry"),
    _Entity("sales_receipt","Sales Receipts",     None,              None,                      "Push", "SalesReceipt"),
    _Entity("inventory_adj","Inventory Adjust.",  None,              None,                      "Push", "InventoryAdjustment"),
]


# ---------------------------------------------------------------------------
# Setting helpers
# ---------------------------------------------------------------------------

def _is_entity_enabled(key: str) -> bool:
    try:
        from db import get_setting
        val = get_setting(f"qbo.entity.{key}.enabled")
        if val is None or val == "":
            return True  # default on
        return str(val).strip() not in ("0", "false", "False", "no")
    except Exception:
        return True


def _set_entity_enabled(key: str, enabled: bool) -> None:
    try:
        from db import set_setting
        set_setting(f"qbo.entity.{key}.enabled", "1" if enabled else "0")
    except Exception as exc:
        logger.warning("set_entity_enabled(%s) failed: %s", key, exc)


# ---------------------------------------------------------------------------
# DB count helpers — defensive: missing tables / columns → 0
# ---------------------------------------------------------------------------

def _safe_scalar(conn, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        return int(row[0] if not hasattr(row, "keys") else list(row)[0])
    except Exception:
        return 0


def _local_count(conn, entity: _Entity) -> int:
    if not entity.table:
        return 0
    return _safe_scalar(conn, f"SELECT COUNT(*) FROM {entity.table}")


def _synced_count(conn, entity: _Entity) -> int:
    if not entity.table or not entity.qbo_id_col:
        return 0
    return _safe_scalar(
        conn,
        f"SELECT COUNT(*) FROM {entity.table} "
        f"WHERE COALESCE({entity.qbo_id_col}, '') <> ''",
    )


def _failed_count(conn, entity: _Entity) -> int:
    if not entity.log_object_type:
        return 0
    return _safe_scalar(
        conn,
        "SELECT COUNT(*) FROM qbo_sync_log "
        "WHERE qbo_object_type = ? AND status = 'failed' "
        "  AND (resolved_at IS NULL OR resolved_at = '')",
        (entity.log_object_type,),
    )


def _last_success(conn, entity: _Entity) -> str:
    """Most recent successful sync timestamp for this entity."""
    if not entity.log_object_type:
        return ""
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM qbo_sync_log "
            "WHERE qbo_object_type = ? AND status = 'success'",
            (entity.log_object_type,),
        ).fetchone()
        if row is None:
            return ""
        val = row[0] if not hasattr(row, "keys") else list(row)[0]
        return (str(val) or "")[:19] if val else ""
    except Exception:
        return ""


# Backwards-compatible alias (older callers used this name).
_last_sync = _last_success


def _last_attempt(conn, entity: _Entity) -> str:
    """Most recent attempt (any status) for this entity."""
    if not entity.log_object_type:
        return ""
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM qbo_sync_log "
            "WHERE qbo_object_type = ?",
            (entity.log_object_type,),
        ).fetchone()
        if row is None:
            return ""
        val = row[0] if not hasattr(row, "keys") else list(row)[0]
        return (str(val) or "")[:19] if val else ""
    except Exception:
        return ""


def _last_failure_info(conn, entity: _Entity) -> dict:
    """Return ``{when, error, qbo_id, local_ref}`` for the most recent
    unresolved failure of this entity. Empty dict when none."""
    if not entity.log_object_type:
        return {}
    try:
        row = conn.execute(
            "SELECT updated_at, error_message, qbo_id, local_ref "
            "FROM qbo_sync_log "
            "WHERE qbo_object_type = ? AND status = 'failed' "
            "  AND (resolved_at IS NULL OR resolved_at = '') "
            "ORDER BY updated_at DESC LIMIT 1",
            (entity.log_object_type,),
        ).fetchone()
        if not row:
            return {}
        if hasattr(row, "keys"):
            return {
                "when": str(row["updated_at"] or "")[:19],
                "error": str(row["error_message"] or ""),
                "qbo_id": str(row["qbo_id"] or ""),
                "local_ref": str(row["local_ref"] or ""),
            }
        return {
            "when": str(row[0] or "")[:19],
            "error": str(row[1] or ""),
            "qbo_id": str(row[2] or ""),
            "local_ref": str(row[3] or ""),
        }
    except Exception:
        return {}


def _pending_webhook_count() -> int:
    try:
        from qbo.webhook_worker import pending_count
        return int(pending_count() or 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Visual helpers: status pills, row-health tinting
# ---------------------------------------------------------------------------

# (bg, fg) for each status pill. Kept compact for ERP-style density.
_PILL_COLORS: dict[str, tuple[str, str]] = {
    "Synced":   ("#e6f4ea", "#1b5e20"),  # green
    "Failed":   ("#fdecea", "#b71c1c"),  # red
    "Pending":  ("#fff8e1", "#8d6e00"),  # yellow
    "Disabled": ("#eceff1", "#546e7a"),  # gray
    "Idle":     ("#eef2f7", "#37474f"),  # gray-blue
}


def _make_pill_cell(text: str, color_key: str) -> QWidget:
    """Return a centered, rounded \"pill\" badge as a cell widget."""
    bg, fg = _PILL_COLORS.get(color_key, _PILL_COLORS["Idle"])
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(4, 1, 4, 1)
    lay.setSpacing(0)
    pill = QLabel(text)
    pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pill.setStyleSheet(
        f"QLabel {{ background: {bg}; color: {fg}; "
        f"border: 1px solid {fg}; border-radius: 9px; "
        f"padding: 1px 8px; font-weight: 600; font-size: 9pt; }}"
    )
    pill.setToolTip({
        "Synced":   "All local rows have a QBO id and no failures.",
        "Failed":   "One or more unresolved sync failures.",
        "Pending":  "Local rows still need to be pushed to QBO.",
        "Disabled": "Background sync is disabled for this entity.",
        "Idle":     "No data to sync yet.",
    }.get(color_key, ""))
    lay.addWidget(pill, 0, Qt.AlignmentFlag.AlignCenter)
    return wrap


def _row_health(
    local: int, synced: int, failed: int, enabled: bool
) -> tuple[str, str, QColor | None]:
    """Pick the status pill key, its color key, and an optional row tint.

    Priority: Disabled > Failed > Pending > Synced > Idle.
    """
    if not enabled:
        return "Disabled", "Disabled", QColor("#f5f6f8")
    if failed > 0:
        return "Failed", "Failed", QColor("#fdecea")
    if local <= 0:
        return "Idle", "Idle", None
    if synced < local:
        return "Pending", "Pending", QColor("#fffbe6")
    return "Synced", "Synced", QColor("#f1f8f3")


def _bold_font() -> QFont:
    f = QFont()
    f.setBold(True)
    return f


# ---------------------------------------------------------------------------
# Screen widget
# ---------------------------------------------------------------------------

class QBOSyncCenterScreen(QWidget):
    """Sync Center: single-pager view of every QBO entity's sync state."""

    # Emitted when the user clicks one of the Manual Sync Actions buttons.
    # The string payload is an operation key (e.g. "sync_products",
    # "sync_invoices", "push_inventory_qty"). Main window listens and
    # navigates to Settings → QBO tab and triggers the matching action.
    navigate_to_settings = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        # Header
        title = QLabel("QBO Sync Center")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(16)
        title.setFont(title_font)
        root.addWidget(title)

        subtitle = QLabel(
            "Per-entity sync status. Toggle an entity off to disable its "
            "background sync without losing its configuration."
        )
        subtitle.setStyleSheet("color: #555;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Status bar
        status_bar = QFrame()
        status_bar.setStyleSheet(
            "QFrame { background: #f5f7fa; border: 1px solid #e0e4ea; "
            "border-radius: 6px; padding: 8px; }"
        )
        status_row = QHBoxLayout(status_bar)
        status_row.setContentsMargins(10, 6, 10, 6)
        status_row.setSpacing(16)

        self._conn_label = QLabel("QBO: …")
        status_row.addWidget(self._conn_label)
        self._authority_label = QLabel("Primary System: \u2026")
        self._authority_label.setToolTip(
            "Which system is the source of truth. Drives whether QBO edits\n"
            "are imported on top of local data or vice-versa."
        )
        status_row.addWidget(self._authority_label)
        self._pending_label = QLabel("Webhook pending: …")
        status_row.addWidget(self._pending_label)
        status_row.addStretch()

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        status_row.addWidget(refresh_btn)

        failures_btn = QPushButton("⚠ View Failures…")
        failures_btn.clicked.connect(self._on_view_failures)
        status_row.addWidget(failures_btn)

        drain_btn = QPushButton("📥 Drain Webhook Queue")
        drain_btn.setToolTip(
            "Process all pending QBO webhook events now instead of "
            "waiting for the background timer."
        )
        drain_btn.clicked.connect(self._on_drain_webhooks)
        status_row.addWidget(drain_btn)

        backfill_btn = QPushButton("⤓ Historical Backfill…")
        backfill_btn.setToolTip(
            "Pull QBO data for a date range and stamp QBO Ids onto "
            "matching local rows. Supports a dry-run preview."
        )
        backfill_btn.clicked.connect(self._on_backfill)
        status_row.addWidget(backfill_btn)

        root.addWidget(status_bar)

        # B2: inline progress strip (hidden until a long action starts).
        self._progress_frame = QFrame()
        self._progress_frame.setStyleSheet(
            "QFrame { background: #FFF3E0; border: 1px solid #FFB74D; "
            "border-radius: 6px; padding: 6px; }"
        )
        prog_row = QHBoxLayout(self._progress_frame)
        prog_row.setContentsMargins(10, 4, 10, 4)
        prog_row.setSpacing(10)
        self._progress_label = QLabel("Working…")
        self._progress_label.setStyleSheet("color: #E65100; font-weight: 600;")
        prog_row.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate by default
        self._progress_bar.setMaximumHeight(14)
        prog_row.addWidget(self._progress_bar, 1)
        self._progress_frame.setVisible(False)
        root.addWidget(self._progress_frame)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels([
            "Entity", "Status", "Enabled", "Direction",
            "Local", "Synced", "Failed",
            "Last Success", "Last Attempt",
            "QBO Object", "Actions",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        # Keep rows compact for ERP-style density.
        vhdr = self._table.verticalHeader()
        vhdr.setDefaultSectionSize(26)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)     # Entity
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)       # Status pill
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)       # Enabled
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)       # Direction
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)       # Local
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)       # Synced
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)       # Failed
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)       # Last Success
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)       # Last Attempt
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)       # QBO Object
        hdr.setSectionResizeMode(10, QHeaderView.ResizeMode.Fixed)      # Actions
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(2, 70)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 70)
        self._table.setColumnWidth(5, 90)
        self._table.setColumnWidth(6, 70)
        self._table.setColumnWidth(7, 140)
        self._table.setColumnWidth(8, 140)
        self._table.setColumnWidth(9, 110)
        self._table.setColumnWidth(10, 130)
        root.addWidget(self._table, 1)

        # Manual Sync Actions — primary entry points for routine push/pull.
        # Per UI restructure plan: Sync Center owns operational actions;
        # Settings is configuration-only. Clicking a button navigates to
        # the (collapsed) legacy buttons in Settings → QBO and triggers
        # the corresponding sync.
        manual_group = QGroupBox("Manual Sync Actions")
        manual_group.setStyleSheet(
            "QGroupBox { font-weight: 700; font-size: 12pt; "
            "border: 1px solid #c5cae9; border-radius: 6px; "
            "margin-top: 10px; padding-top: 10px; } "
            "QGroupBox::title { subcontrol-origin: margin; "
            "left: 10px; padding: 0 4px; color: #1a237e; }"
        )
        manual_v = QVBoxLayout()
        manual_hint = QLabel(
            "Trigger an immediate sync. "
            "<span style='color:#1b5e20;'>Green = push to QBO</span> · "
            "<span style='color:#0d47a1;'>Blue = import from QBO</span> · "
            "<span style='color:#e65100;'>Orange = overwrites QBO data</span>."
        )
        manual_hint.setWordWrap(True)
        manual_hint.setStyleSheet("color: #555; font-style: italic;")
        manual_v.addWidget(manual_hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        GREEN = (
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: 600; padding: 6px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1b5e20; }"
        )
        BLUE = (
            "QPushButton { background-color: #1565c0; color: white; "
            "font-weight: 600; padding: 6px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #0d47a1; }"
        )
        ORANGE = (
            "QPushButton { background-color: #ef6c00; color: white; "
            "font-weight: 600; padding: 6px 12px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #e65100; }"
        )

        def _mk(label: str, op_key: str, css: str) -> QPushButton:
            btn = QPushButton(label)
            btn.setStyleSheet(css)
            btn.clicked.connect(lambda _checked=False, k=op_key: self.navigate_to_settings.emit(k))
            return btn

        # Row 0: outbound pushes (App → QBO)
        grid.addWidget(_mk("⬆ Sync Products → QBO", "sync_products", GREEN), 0, 0)
        grid.addWidget(_mk("⬆ Sync Invoices → QBO", "sync_invoices", GREEN), 0, 1)
        grid.addWidget(_mk("⬆ Sync Customers → QBO", "sync_customers", GREEN), 0, 2)
        grid.addWidget(_mk("⬆ Sync Vendors → QBO", "sync_vendors", GREEN), 0, 3)

        # Row 1: inbound pulls (QBO → App)
        grid.addWidget(_mk("⬇ Import Customers", "import_customers", BLUE), 1, 0)
        grid.addWidget(_mk("⬇ Import Vendors", "import_vendors", BLUE), 1, 1)
        grid.addWidget(_mk("⬇ Import Products", "import_products", BLUE), 1, 2)
        grid.addWidget(_mk("⚠ Push Inventory Qty → QBO", "push_inventory_qty", ORANGE), 1, 3)

        manual_v.addLayout(grid)
        manual_group.setLayout(manual_v)
        root.addWidget(manual_group)

        # Footer
        footer = QLabel(
            "Configuration (credentials, account mappings, sync authority) "
            "lives in <b>Settings → QBO</b>. Compare/repair tools live in "
            "<b>Accounting → QBO Reconciliation</b>."
        )
        footer.setStyleSheet("color: #888; font-size: 9pt;")
        footer.setTextFormat(Qt.TextFormat.RichText)
        footer.setWordWrap(True)
        root.addWidget(footer)

    # ------------------------------------------------------------------
    # Data refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Reload connection status + per-entity counts from the DB."""
        self._refresh_status()
        self._refresh_table()

    def _refresh_status(self) -> None:
        # Connection state
        try:
            from qbo.integration import get_qbo_service
            svc = get_qbo_service()
            status = svc.get_status()
        except Exception as exc:
            self._conn_label.setText(f"QBO: error ({exc})")
            self._authority_label.setText("Authority: ?")
            self._pending_label.setText("Webhook pending: ?")
            return

        parts = []
        if status.get("is_mock"):
            parts.append("MOCK")
        elif status.get("is_connected"):
            parts.append("CONNECTED")
        elif status.get("has_tokens"):
            parts.append("tokens OK, not connected")
        else:
            parts.append("not connected")
        parts.append(status.get("environment", "?"))
        if status.get("write_enabled"):
            parts.append("write ✓")
        else:
            parts.append("read-only")
        self._conn_label.setText("QBO: " + "  ·  ".join(parts))

        try:
            from qbo.config import get_sync_authority
            raw = str(get_sync_authority() or "").strip().lower()
            pretty = {
                "local_master": "JAKS Inventory (Local \u2192 QBO)",
                "qbo_master":   "QuickBooks Online (QBO \u2192 JAKS)",
                "local":        "JAKS Inventory (Local \u2192 QBO)",
                "qbo":          "QuickBooks Online (QBO \u2192 JAKS)",
                "both":         "Bidirectional (Local \u2194 QBO)",
            }.get(raw, raw or "?")
            self._authority_label.setText(f"Primary System: {pretty}")
        except Exception:
            self._authority_label.setText("Primary System: ?")

        pending = _pending_webhook_count()
        self._pending_label.setText(f"Webhook pending: {pending}")

    def _refresh_table(self) -> None:
        rows: list[dict[str, Any]] = []
        try:
            from db.database import get_db
            with get_db() as conn:
                for ent in _ENTITIES:
                    rows.append({
                        "entity": ent,
                        "local": _local_count(conn, ent),
                        "synced": _synced_count(conn, ent),
                        "failed": _failed_count(conn, ent),
                        "last_success": _last_success(conn, ent),
                        "last_attempt": _last_attempt(conn, ent),
                        "fail_info": _last_failure_info(conn, ent),
                    })
        except Exception as exc:
            logger.warning("sync center refresh failed: %s", exc)
            rows = [{"entity": e, "local": 0, "synced": 0, "failed": 0,
                     "last_success": "", "last_attempt": "",
                     "fail_info": {}} for e in _ENTITIES]

        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            ent: _Entity = row["entity"]
            local = int(row["local"])
            synced = int(row["synced"])
            failed = int(row["failed"])
            enabled = _is_entity_enabled(ent.key)
            status_key, status_color, row_tint = _row_health(
                local, synced, failed, enabled
            )

            # 0 — Entity label
            lbl = QTableWidgetItem(ent.label)
            lbl.setToolTip(f"local table: {ent.table or '(no table)'}")
            self._table.setItem(r, 0, lbl)

            # 1 — Status pill
            self._table.setCellWidget(
                r, 1, _make_pill_cell(status_key, status_color)
            )

            # 2 — Enabled checkbox
            cb = QCheckBox()
            cb.setChecked(enabled)
            cb.stateChanged.connect(
                lambda state, key=ent.key: self._on_enable_toggled(key, bool(state))
            )
            cb_wrap = QWidget()
            cb_lay = QHBoxLayout(cb_wrap)
            cb_lay.setContentsMargins(0, 0, 0, 0)
            cb_lay.addWidget(cb, 0, Qt.AlignmentFlag.AlignCenter)
            self._table.setCellWidget(r, 2, cb_wrap)

            # 3 — Direction
            dir_item = QTableWidgetItem(ent.direction)
            dir_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 3, dir_item)

            # 4 — Local count
            local_item = QTableWidgetItem(str(local))
            local_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 4, local_item)

            # 5 — Synced (numeric + % gloss)
            if local > 0:
                pct = (synced / local) * 100.0
                stext = f"{synced}  ({pct:.0f}%)"
            else:
                stext = str(synced)
            sync_item = QTableWidgetItem(stext)
            sync_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if local > 0 and synced < local:
                sync_item.setForeground(QBrush(QColor("#b8860b")))
            self._table.setItem(r, 5, sync_item)

            # 6 — Failed count (with rich tooltip)
            fail_item = QTableWidgetItem(str(failed))
            fail_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if failed > 0:
                fail_item.setForeground(QBrush(QColor("#c62828")))
                fail_item.setFont(_bold_font())
                info = row.get("fail_info") or {}
                err = (info.get("error") or "").strip()
                # Truncate long failure text so tooltips stay readable.
                if len(err) > 400:
                    err = err[:400] + "…"
                when = info.get("when") or "—"
                obj_id = info.get("qbo_id") or info.get("local_ref") or "—"
                fail_item.setToolTip(
                    f"<b>Last failure</b><br>"
                    f"<b>When:</b> {when}<br>"
                    f"<b>Object:</b> {obj_id}<br>"
                    f"<b>Reason:</b> {err or '(no message)'}"
                )
            else:
                fail_item.setToolTip("No unresolved failures.")
            self._table.setItem(r, 6, fail_item)

            # 7 — Last Success
            ls_item = QTableWidgetItem(row["last_success"] or "—")
            ls_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ls_item.setToolTip("Most recent successful sync for this entity.")
            self._table.setItem(r, 7, ls_item)

            # 8 — Last Attempt
            la_item = QTableWidgetItem(row["last_attempt"] or "—")
            la_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            la_item.setToolTip(
                "Most recent attempt for this entity, regardless of outcome."
            )
            self._table.setItem(r, 8, la_item)

            # 9 — QBO object type
            obj_item = QTableWidgetItem(ent.log_object_type or "—")
            obj_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 9, obj_item)

            # 10 — Manage ▼ dropdown
            self._table.setCellWidget(
                r, 10, self._make_manage_button(ent, enabled, failed)
            )

            # Row health tint — applied as item background on plain cells.
            if row_tint is not None:
                tint_brush = QBrush(row_tint)
                for col in (0, 3, 4, 5, 6, 7, 8, 9):
                    item = self._table.item(r, col)
                    if item is not None:
                        item.setBackground(tint_brush)

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------
    def _on_enable_toggled(self, key: str, enabled: bool) -> None:
        _set_entity_enabled(key, enabled)
        # Refresh so the row repaints with the right health tint + pill.
        self.refresh()

    # Operation-key maps used by the Manage menu's Push/Pull items. Entities
    # without a mapped op fall back to a disabled menu item with a tooltip.
    _PUSH_OP_KEYS: dict[str, str] = {
        "item":     "sync_products",
        "customer": "sync_customers",
        "vendor":   "sync_vendors",
        "invoice":  "sync_invoices",
    }
    _PULL_OP_KEYS: dict[str, str] = {
        "item":     "import_products",
        "customer": "import_customers",
        "vendor":   "import_vendors",
    }

    def _make_manage_button(
        self, entity: _Entity, enabled: bool, failed: int
    ) -> QWidget:
        """Build the per-row "Manage ▼" dropdown button + menu."""
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(0)

        btn = QToolButton()
        btn.setText("Manage")
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setStyleSheet(
            "QToolButton { padding: 2px 10px; border: 1px solid #b0b4ba; "
            "border-radius: 3px; background: #f6f7f9; }"
            "QToolButton:hover { background: #e8ebef; }"
            "QToolButton::menu-indicator { "
            "subcontrol-origin: padding; subcontrol-position: right center; "
            "right: 4px; }"
        )
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setToolTip(f"Row actions for {entity.label}")

        menu = QMenu(btn)

        act_details = QAction("View Details", menu)
        act_details.setToolTip("Open the failures dialog filtered to this entity.")
        act_details.triggered.connect(lambda _checked=False, e=entity: self._on_view_failures_for(e))
        menu.addAction(act_details)

        act_retry = QAction(f"Retry Sync ({failed})" if failed else "Retry Sync", menu)
        act_retry.setEnabled(failed > 0)
        act_retry.setToolTip(
            "Re-attempt every unresolved failure for this entity."
            if failed else "No unresolved failures to retry."
        )
        act_retry.triggered.connect(lambda _checked=False, e=entity: self._on_retry_entity(e))
        menu.addAction(act_retry)

        menu.addSeparator()

        push_key = self._PUSH_OP_KEYS.get(entity.key)
        act_push = QAction("Push To QBO\u2026", menu)
        if push_key and entity.direction in ("Push", "Both"):
            act_push.triggered.connect(
                lambda _checked=False, k=push_key: self.navigate_to_settings.emit(k)
            )
            act_push.setToolTip("Run an immediate push to QBO for this entity.")
        else:
            act_push.setEnabled(False)
            act_push.setToolTip("Push not available for this entity.")
        menu.addAction(act_push)

        pull_key = self._PULL_OP_KEYS.get(entity.key)
        act_pull = QAction("Pull From QBO\u2026", menu)
        if pull_key and entity.direction in ("Pull", "Both"):
            act_pull.triggered.connect(
                lambda _checked=False, k=pull_key: self.navigate_to_settings.emit(k)
            )
            act_pull.setToolTip("Import the latest data for this entity from QBO.")
        else:
            act_pull.setEnabled(False)
            act_pull.setToolTip("Pull not available for this entity.")
        menu.addAction(act_pull)

        menu.addSeparator()

        act_failures = QAction("View Failures\u2026", menu)
        act_failures.triggered.connect(lambda _checked=False, e=entity: self._on_view_failures_for(e))
        menu.addAction(act_failures)

        act_backfill = QAction("Historical Backfill\u2026", menu)
        act_backfill.setToolTip("Pull QBO data for a date range and stamp QBO Ids onto local rows.")
        act_backfill.triggered.connect(lambda _checked=False: self._on_backfill())
        menu.addAction(act_backfill)

        menu.addSeparator()

        act_toggle = QAction(
            "Disable Sync" if enabled else "Enable Sync", menu
        )
        act_toggle.triggered.connect(
            lambda _checked=False, k=entity.key, e=enabled: self._on_enable_toggled(k, not e)
        )
        menu.addAction(act_toggle)

        btn.setMenu(menu)
        lay.addWidget(btn)
        return wrap

    # ------------------------------------------------------------------
    # Inline progress helpers (B2)
    # ------------------------------------------------------------------
    def _begin_progress(self, label: str) -> None:
        self._progress_label.setText(label)
        self._progress_bar.setRange(0, 0)
        self._progress_frame.setVisible(True)
        # Force a paint so the user sees activity before the
        # blocking call kicks off.
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
        except Exception:
            pass

    def _end_progress(self) -> None:
        self._progress_frame.setVisible(False)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_view_failures(self) -> None:
        try:
            from jaks_inventory.ui.settings_screen import _QBOFailuresDialog
            dlg = _QBOFailuresDialog(self)
            dlg.exec()
        except Exception as exc:
            QMessageBox.warning(
                self, "Failures dialog unavailable",
                f"Could not open the failures dialog:\n{exc}",
            )

    def _on_view_failures_for(self, entity: _Entity) -> None:
        try:
            from jaks_inventory.ui.settings_screen import _QBOFailuresDialog
            dlg = _QBOFailuresDialog(self, entity_filter=entity.log_object_type)
        except TypeError:
            # Older constructor signature — fall back to unfiltered.
            from jaks_inventory.ui.settings_screen import _QBOFailuresDialog
            dlg = _QBOFailuresDialog(self)
        except Exception as exc:
            QMessageBox.warning(
                self, "Failures dialog unavailable",
                f"Could not open the failures dialog:\n{exc}",
            )
            return
        dlg.exec()
        self.refresh()

    def _on_retry_entity(self, entity: _Entity) -> None:
        """Retry every unresolved failure for one entity."""
        if not entity.log_object_type:
            return
        try:
            from db.database import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id FROM qbo_sync_log "
                    "WHERE qbo_object_type = ? AND status = 'failed' "
                    "  AND (resolved_at IS NULL OR resolved_at = '')",
                    (entity.log_object_type,),
                ).fetchall()
                log_ids = [int(r[0] if not hasattr(r, 'keys') else r['id'])
                           for r in rows]
        except Exception as exc:
            QMessageBox.warning(
                self, "Retry failed",
                f"Could not load failures: {exc}",
            )
            return
        if not log_ids:
            QMessageBox.information(
                self, "Nothing to retry",
                f"No unresolved failures for {entity.label}.",
            )
            return

        self._begin_progress(f"Retrying {len(log_ids)} {entity.label} failure(s)…")
        try:
            from qbo.retry_dispatch import retry_log_ids
            result = retry_log_ids(log_ids)
        except Exception as exc:
            self._end_progress()
            QMessageBox.warning(
                self, "Retry failed",
                f"{type(exc).__name__}: {exc}",
            )
            return
        self._end_progress()

        QMessageBox.information(
            self, f"Retry: {entity.label}",
            f"OK:          {result.get('ok', 0)}\n"
            f"Failed:      {result.get('failed', 0)}\n"
            f"Skipped:     {result.get('skipped', 0)}\n"
            f"Unsupported: {result.get('unsupported', 0)}",
        )
        self.refresh()

    def _on_drain_webhooks(self) -> None:
        self._begin_progress("Draining QBO webhook queue…")
        try:
            from qbo.webhook_worker import process_pending
            result = process_pending(limit=500)
        except Exception as exc:
            self._end_progress()
            QMessageBox.warning(
                self, "Drain failed",
                f"{type(exc).__name__}: {exc}",
            )
            self.refresh()
            return
        self._end_progress()
        QMessageBox.information(
            self, "Webhook queue drained",
            f"Processed: {result.get('processed', 0)}\n"
            f"Failed:    {result.get('failed', 0)}\n"
            f"Skipped:   {result.get('skipped', 0)}",
        )
        self.refresh()

    def _on_backfill(self) -> None:
        try:
            from jaks_inventory.ui.qbo_backfill_dialog import QBOBackfillWizard
            dlg = QBOBackfillWizard(self)
            dlg.exec()
        except Exception as exc:
            QMessageBox.warning(
                self, "Backfill unavailable",
                f"Could not open the backfill wizard:\n{type(exc).__name__}: {exc}",
            )
        self.refresh()
