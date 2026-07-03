"""Customer Cores screen.

Tracks customer cores owed to JAK's: invoice, due date, status, days
open and the next action. Statuses are simplified per the May-2026 UX
spec to:

    Awaiting Return | Returned | Credited | Closed | Written Off |
    Voided

Row colour rules:
    Red     — overdue + still awaiting return
    Yellow  — due within 7 days, still awaiting
    Blue    — returned but not yet credited
    Green   — credited / closed
    Gray    — voided / written-off

All toolbar actions live on the right-click context menu (and as
double-click for "View Details").
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_all_customers,
    issue_core_credit,
    list_core_returns,
    receive_core,
    void_core_return,
)

from .core_dialog import CoreDialog, IssueCreditDialog, ReceiveCoreDialog

log = logging.getLogger(__name__)


# Status display + colour helpers.
_RAW_STATUS_LABEL = {
    "pending":   "Awaiting Return",
    "received":  "Returned",
    "credited":  "Credited",
    "closed":    "Closed",
    "voided":    "Voided",
    "void":      "Voided",
    "write_off": "Written Off",
}

_SIMPLE_STATUS_OPTIONS = (
    ("All Status",      None),
    ("Awaiting Return", "pending"),
    ("Returned",        "received"),
    ("Credited",        "credited"),
    ("Closed",          "closed"),
    ("Voided",          "voided"),
)

_COL_INVOICE     = 0
_COL_CUSTOMER    = 1
_COL_SKU         = 2
_COL_PRODUCT     = 3
_COL_CORE_CHARGE = 4
_COL_STATUS      = 5
_COL_DUE         = 6
_COL_DAYS_OPEN   = 7
_COL_RECEIVED    = 8
_COL_CREDIT      = 9
_COL_NEXT_ACTION = 10

_COLUMNS = (
    "Invoice #", "Customer", "SKU", "Product", "Core Charge",
    "Status", "Due Date", "Days Open", "Received", "Credit Amount",
    "Next Action",
)


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


class _NumItem(QTableWidgetItem):
    def __init__(self, value: float, fmt: str = "$"):
        super().__init__()
        self._v = float(value or 0)
        if fmt == "$":
            self.setText(f"${self._v:,.2f}" if self._v else "")
        elif fmt == "int":
            self.setText(str(int(self._v)) if self._v else "")
        else:
            self.setText(str(self._v))
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._v < other._v
        return super().__lt__(other)


class CustomerCoresScreen(QWidget):
    """Single-grid customer cores screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cores: list[dict] = []
        self._overdue_only = False
        self._build_ui()
        self._load_customers_combo()
        self._refresh()

        try:
            from .core_events import core_events
            core_events.cores_changed.connect(self._refresh)
        except Exception:
            pass

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("Customer Cores")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        header.addWidget(title)
        header.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  SKU / Invoice / Customer …")
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._apply_view)
        header.addWidget(self._search)
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Filter row
        filt = QHBoxLayout()
        filt.addWidget(QLabel("Status:"))
        self._status_filter = QComboBox()
        for label, val in _SIMPLE_STATUS_OPTIONS:
            self._status_filter.addItem(label, val)
        self._status_filter.currentIndexChanged.connect(self._refresh)
        filt.addWidget(self._status_filter)

        filt.addWidget(QLabel("Customer:"))
        self._customer_filter = QComboBox()
        self._customer_filter.addItem("All Customers", None)
        self._customer_filter.currentIndexChanged.connect(self._refresh)
        filt.addWidget(self._customer_filter)

        self._overdue_btn = QPushButton("🚨 Show Overdue Only")
        self._overdue_btn.setCheckable(True)
        self._overdue_btn.toggled.connect(self._on_overdue_toggled)
        filt.addWidget(self._overdue_btn)

        filt.addStretch(1)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color: #6B7280;")
        filt.addWidget(self._count_lbl)
        root.addLayout(filt)

        # Table
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(False)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.cellDoubleClicked.connect(lambda *_: self._on_view())
        hdr = self._table.horizontalHeader()
        for c in range(len(_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        root.addWidget(self._table, 1)

    def _load_customers_combo(self) -> None:
        try:
            customers = get_all_customers()
        except Exception as exc:
            log.debug("customer combo load failed: %s", exc)
            return
        for c in customers[:300]:
            name = str(c.get("name", ""))
            company = str(c.get("company", ""))
            display = f"{name} ({company})" if company else name
            self._customer_filter.addItem(display, c.get("id"))

    # ── Data ────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        status = self._status_filter.currentData()
        customer_id = self._customer_filter.currentData()
        try:
            self._cores = list_core_returns(
                status=status, customer_id=customer_id, limit=1000,
            )
        except Exception as exc:
            log.warning("customer-cores list load failed: %s", exc)
            self._cores = []
        self._apply_view()

    def _apply_view(self) -> None:
        rows = list(self._cores)

        # Overdue-only layer.
        if self._overdue_only:
            today = date.today()
            kept: list[dict] = []
            for c in rows:
                if str(c.get("status") or "").lower() != "pending":
                    continue
                due = _parse_date(c.get("due_date"))
                if due and due < today:
                    kept.append(c)
            rows = kept

        # Free-text search.
        q = (self._search.text() or "").strip().lower()
        if q:
            rows = [
                r for r in rows
                if q in str(r.get("sku") or "").lower()
                or q in str(r.get("invoice_number") or "").lower()
                or q in str(r.get("customer_name") or "").lower()
                or q in str(r.get("product_title") or "").lower()
            ]

        self._populate(rows)

    def _populate(self, rows: list[dict]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        today = date.today()

        for i, c in enumerate(rows):
            status = str(c.get("status") or "").lower()
            status_label = _RAW_STATUS_LABEL.get(status, status.title() or "—")
            due = _parse_date(c.get("due_date"))
            created = _parse_date(c.get("created_at") or "")
            recv = _parse_date(c.get("received_date") or "")
            charge = float(c.get("core_charge") or 0)
            credit = float(c.get("credit_issued") or 0)

            invoice = str(c.get("invoice_number") or "")
            customer = str(c.get("customer_name") or "")
            sku = str(c.get("sku") or "")
            product = str(c.get("product_title") or "")[:60]

            days_open = 0
            if created:
                ref = recv if status == "received" and recv else today
                days_open = max(0, (ref - created).days)

            # Row colour classification.
            brush: Optional[QBrush] = None
            if status in ("void", "voided", "write_off"):
                brush = QBrush(QColor("#e5e7eb"))  # gray
            elif status in ("credited", "closed"):
                brush = QBrush(QColor("#dcfce7"))  # green
            elif status == "received":
                brush = QBrush(QColor("#dbeafe"))  # blue
            elif status == "pending":
                if due and due < today:
                    brush = QBrush(QColor("#fee2e2"))  # red
                elif due and (due - today).days <= 7:
                    brush = QBrush(QColor("#fef3c7"))  # yellow

            def cell(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if brush is not None:
                    it.setBackground(brush)
                return it

            inv_item = cell(invoice)
            inv_item.setData(Qt.ItemDataRole.UserRole, c)
            self._table.setItem(i, _COL_INVOICE, inv_item)
            self._table.setItem(i, _COL_CUSTOMER, cell(customer))
            self._table.setItem(i, _COL_SKU, cell(sku))
            self._table.setItem(i, _COL_PRODUCT, cell(product))

            charge_item = _NumItem(charge)
            if brush is not None:
                charge_item.setBackground(brush)
            self._table.setItem(i, _COL_CORE_CHARGE, charge_item)

            self._table.setItem(i, _COL_STATUS, cell(status_label))

            due_item = cell(due.isoformat() if due else "")
            if status == "pending" and due and due < today:
                due_item.setText(f"⚠ {due.isoformat()}")
                due_item.setForeground(QColor("#a4453a"))
            self._table.setItem(i, _COL_DUE, due_item)

            days_item = _NumItem(days_open, fmt="int")
            if brush is not None:
                days_item.setBackground(brush)
            self._table.setItem(i, _COL_DAYS_OPEN, days_item)

            self._table.setItem(i, _COL_RECEIVED, cell(recv.isoformat() if recv else ""))

            credit_item = _NumItem(credit)
            if brush is not None:
                credit_item.setBackground(brush)
            self._table.setItem(i, _COL_CREDIT, credit_item)

            self._table.setItem(
                i, _COL_NEXT_ACTION, cell(self._next_action_for(status, due, today)),
            )

        self._table.setSortingEnabled(True)
        self._count_lbl.setText(f"{len(rows)} core(s)")

    @staticmethod
    def _next_action_for(status: str, due: Optional[date], today: date) -> str:
        if status == "pending":
            if due and due < today:
                return "⚠ Receive (overdue)"
            return "Receive Core"
        if status == "received":
            return "Issue Credit"
        if status in ("credited", "closed"):
            return "—"
        if status in ("void", "voided", "write_off"):
            return "—"
        return "—"

    # ── Selection helpers ──────────────────────────────────────────────
    def _selected_core(self) -> Optional[dict]:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, _COL_INVOICE)
        if item is None:
            return None
        c = item.data(Qt.ItemDataRole.UserRole)
        return c if isinstance(c, dict) else None

    def _on_overdue_toggled(self, checked: bool) -> None:
        self._overdue_only = bool(checked)
        if checked:
            self._overdue_btn.setText("✅ Showing Overdue")
            self._overdue_btn.setStyleSheet(
                "QPushButton { background:#c62828; color:white; "
                "padding:4px 10px; border-radius:4px; font-weight:bold; }"
            )
        else:
            self._overdue_btn.setText("🚨 Show Overdue Only")
            self._overdue_btn.setStyleSheet("")
        self._apply_view()

    # ── Context menu ────────────────────────────────────────────────────
    def _on_context_menu(self, pos) -> None:
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        self._table.selectRow(idx.row())
        core = self._selected_core()
        if not core:
            return
        status = str(core.get("status") or "").lower()
        menu = QMenu(self)
        a_view = menu.addAction("👁  View Details")
        menu.addSeparator()
        a_recv = menu.addAction("📦  Receive Core")
        a_recv.setEnabled(status == "pending")
        a_credit = menu.addAction("💰  Issue Credit")
        a_credit.setEnabled(status == "received")
        a_contact = menu.addAction("✉  Contact Customer")
        a_contact.setEnabled(bool(core.get("customer_id")))
        a_print = menu.addAction("🖨  Print Receipt")
        a_print.setEnabled(bool(core.get("invoice_id")))
        menu.addSeparator()
        a_wo = menu.addAction("🚫  Write Off")
        a_wo.setEnabled(status in ("pending", "received"))
        a_void = menu.addAction("❌  Void")
        a_void.setEnabled(status in ("pending", "received"))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is a_view:
            self._on_view()
        elif chosen is a_recv:
            self._on_receive()
        elif chosen is a_credit:
            self._on_issue_credit()
        elif chosen is a_contact:
            self._on_contact()
        elif chosen is a_print:
            self._on_print_receipt()
        elif chosen is a_wo:
            self._on_write_off()
        elif chosen is a_void:
            self._on_void()

    # ── Actions ────────────────────────────────────────────────────────
    def _on_view(self) -> None:
        core = self._selected_core()
        if not core:
            return
        try:
            CoreDialog(core, self).exec()
        except Exception as exc:
            QMessageBox.warning(self, "View", f"Could not open detail: {exc}")

    def _on_receive(self) -> None:
        core = self._selected_core()
        if not core:
            return
        dlg = ReceiveCoreDialog(core, self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        try:
            receive_core(
                core_id=core.get("id"),
                condition=data.get("condition", "good"),
                serial_number=data.get("serial_number"),
                notes=data.get("notes"),
                received_by=data.get("received_by"),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Receive", f"Failed: {exc}")
            return
        self._emit_changed()
        self._refresh()

    def _on_issue_credit(self) -> None:
        core = self._selected_core()
        if not core:
            return
        dlg = IssueCreditDialog(core, self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        try:
            issue_core_credit(
                core_id=core.get("id"),
                credit_amount=data.get("credit_amount", 0),
                credit_invoice_id=data.get("credit_invoice_id"),
                notes=data.get("notes"),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Issue Credit", f"Failed: {exc}")
            return
        self._emit_changed()
        self._refresh()

    def _on_void(self) -> None:
        core = self._selected_core()
        if not core:
            return
        if QMessageBox.question(
            self,
            "Void Core Return",
            f"Void core return for invoice {core.get('invoice_number') or 'N/A'}?",
        ) != QMessageBox.StandardButton.Yes:
            return
        reason, ok = QInputDialog.getText(
            self, "Void Reason", "Reason for voiding this core (optional):",
        )
        try:
            void_core_return(
                core.get("id"),
                notes="Voided by user",
                void_reason=reason.strip() if ok and reason else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Void", f"Failed: {exc}")
            return
        self._emit_changed()
        self._refresh()

    def _on_write_off(self) -> None:
        core = self._selected_core()
        if not core:
            return
        reason, ok = QInputDialog.getText(
            self, "Write Off", "Reason for writing this core off:",
        )
        if not ok:
            return
        # Customer-side write-off reuses the void path with a marker
        # reason; vendor-side write-off lives on the obligation.
        try:
            void_core_return(
                core.get("id"),
                notes="Written off",
                void_reason=(reason.strip() or "write_off"),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Write Off", f"Failed: {exc}")
            return
        self._emit_changed()
        self._refresh()

    def _on_contact(self) -> None:
        core = self._selected_core()
        if not core:
            return
        cust_id = core.get("customer_id")
        if not cust_id:
            QMessageBox.information(self, "Contact", "No customer linked.")
            return
        # Try to open the CRM/customer detail dialog if available.
        try:
            from .customer_360_dialog import Customer360Dialog
            Customer360Dialog(int(cust_id), self).exec()
            return
        except Exception:
            pass
        QMessageBox.information(
            self, "Contact Customer",
            f"Customer #{cust_id}: {core.get('customer_name') or ''}",
        )

    def _on_print_receipt(self) -> None:
        core = self._selected_core()
        if not core:
            return
        invoice_id = core.get("invoice_id")
        if not invoice_id:
            QMessageBox.warning(self, "No Invoice", "Core is not linked to an invoice.")
            return
        try:
            from ..utils.pdf_helpers import safe_generate_pdf
            from ..utils.pdf_core_receipt import generate_core_receipt_pdf
            safe_generate_pdf(generate_core_receipt_pdf, int(invoice_id), parent=self)
        except Exception as exc:
            QMessageBox.warning(self, "Print Receipt", str(exc))

    # ── Misc ───────────────────────────────────────────────────────────
    def _emit_changed(self) -> None:
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass

    def showEvent(self, event):  # noqa: D401
        super().showEvent(event)
        try:
            self._refresh()
        except Exception:
            pass

