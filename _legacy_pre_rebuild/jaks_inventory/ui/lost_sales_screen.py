"""Lost Sales Report screen.

Shows quotes with status='lost' plus aggregate analytics so the sales
team can see how much revenue is walking out the door and why.

Reads from db.inventory: get_lost_sales, get_lost_sales_summary,
get_win_loss_ratio.

Double-click a row in the "Lost Quotes" table to open the QuoteDialog
(where the user can review or reopen the quote).
"""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from db.inventory import (
    get_lost_sales,
    get_lost_sales_summary,
    get_lost_reasons,
    get_win_loss_ratio,
)

from .quote_dialog import QuoteDialog
from .quotes_screen import _LOST_REASONS


class LostSalesScreen(QWidget):
    """Lost-sales analytics dashboard."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_data()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Toolbar: date range + reason filter + refresh ──
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Lost Sales Report</b>"))
        toolbar.addSpacing(20)

        toolbar.addWidget(QLabel("From:"))
        self._from_edit = QDateEdit()
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat("MM/dd/yyyy")
        from PySide6.QtCore import QDate
        ninety_days_ago = date.today() - timedelta(days=90)
        self._from_edit.setDate(QDate(ninety_days_ago.year, ninety_days_ago.month, ninety_days_ago.day))
        toolbar.addWidget(self._from_edit)

        toolbar.addWidget(QLabel("To:"))
        self._to_edit = QDateEdit()
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat("MM/dd/yyyy")
        today = date.today()
        self._to_edit.setDate(QDate(today.year, today.month, today.day))
        toolbar.addWidget(self._to_edit)

        toolbar.addWidget(QLabel("Reason:"))
        self._reason_combo = QComboBox()
        self._reason_combo.addItem("— Any —", "")
        try:
            _reasons = [r.get("label") for r in get_lost_reasons(active_only=False) if r.get("label")]
        except Exception:
            _reasons = list(_LOST_REASONS)
        for r in _reasons:
            self._reason_combo.addItem(r, r)
        toolbar.addWidget(self._reason_combo)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._load_data)
        toolbar.addWidget(apply_btn)

        toolbar.addStretch()

        self._refresh_btn = QPushButton("🔄 Refresh")
        self._refresh_btn.clicked.connect(self._load_data)
        toolbar.addWidget(self._refresh_btn)
        layout.addLayout(toolbar)

        # ── Summary cards ──
        cards = QHBoxLayout()
        self._card_count = self._make_card("Lost Quotes", "0", "#c0392b")
        self._card_value = self._make_card("Lost $", "$0.00", "#8E1B1B")
        self._card_avg_days = self._make_card("Avg Days Open", "0", "#D97706")
        self._card_win_rate = self._make_card("Win Rate", "—", "#2E7D32")
        for c in (self._card_count, self._card_value, self._card_avg_days, self._card_win_rate):
            cards.addWidget(c)
        layout.addLayout(cards)

        # ── Tabs: By Reason | By Customer | Lost Quotes ──
        self._tabs = QTabWidget()

        self._reason_table = QTableWidget(0, 3)
        self._reason_table.setHorizontalHeaderLabels(["Reason", "# Quotes", "Value"])
        self._reason_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._reason_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._reason_table.setSortingEnabled(True)
        self._reason_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._reason_table, "🏷 By Reason")

        self._cust_table = QTableWidget(0, 4)
        self._cust_table.setHorizontalHeaderLabels(["Customer", "Account", "# Lost", "Value"])
        self._cust_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cust_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cust_table.setSortingEnabled(True)
        self._cust_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._cust_table, "👥 Top Customers")

        self._month_table = QTableWidget(0, 3)
        self._month_table.setHorizontalHeaderLabels(["Month", "# Quotes", "Value"])
        self._month_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._month_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._month_table.setSortingEnabled(True)
        self._month_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._month_table, "📅 By Month")

        self._quotes_table = QTableWidget(0, 7)
        self._quotes_table.setHorizontalHeaderLabels([
            "Quote #", "Customer", "Lost Date", "Days Open", "Reason", "Notes", "Value",
        ])
        self._quotes_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._quotes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._quotes_table.setSortingEnabled(True)
        self._quotes_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._quotes_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._quotes_table.doubleClicked.connect(self._on_quote_dbl_click)
        self._tabs.addTab(self._quotes_table, "📋 Lost Quotes")

        layout.addWidget(self._tabs)

        # Store quote IDs for each row so double-click can resolve them
        # (sorting shuffles rows, so we also store id on item.UserRole).
        self._quote_ids: list[int] = []

    def _make_card(self, title: str, value: str, color: str) -> QLabel:
        card = QLabel()
        card.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.setMinimumHeight(70)
        card.setStyleSheet(
            f"QLabel {{ background-color: {color}; color: white; "
            f"border-radius: 6px; padding: 8px; font-size: 11pt; }}"
        )
        card.setText(f"<b>{title}</b><br/>{value}")
        return card

    def _set_card(self, card: QLabel, title: str, value: str) -> None:
        card.setText(f"<b>{title}</b><br/>{value}")

    # ── Data ────────────────────────────────────────────────────

    def _filters(self) -> tuple[str, str, str]:
        d_from = self._from_edit.date().toString("yyyy-MM-dd")
        d_to = self._to_edit.date().toString("yyyy-MM-dd")
        reason = self._reason_combo.currentData() or ""
        return d_from, d_to, reason

    def _load_data(self) -> None:
        d_from, d_to, reason = self._filters()
        try:
            summary = get_lost_sales_summary(d_from, d_to)
            ratio = get_win_loss_ratio(d_from, d_to)
            quotes = get_lost_sales(d_from, d_to, reason=reason or None)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load lost sales:\n{e}")
            return

        # Cards
        self._set_card(self._card_count, "Lost Quotes", f"{summary['count']:,}")
        self._set_card(self._card_value, "Lost $", f"${summary['total_value']:,.2f}")
        self._set_card(self._card_avg_days, "Avg Days Open", f"{summary['avg_days_open']:.1f}")
        wr = ratio["win_rate_count"]
        wr_txt = "—" if (ratio["won"]["count"] + ratio["lost"]["count"]) == 0 else f"{wr * 100:.1f}%"
        won_n = ratio["won"]["count"]
        lost_n = ratio["lost"]["count"]
        self._set_card(
            self._card_win_rate, "Win Rate",
            f"{wr_txt}<br/><span style='font-size:8pt'>{won_n} won / {lost_n} lost</span>",
        )

        self._populate_reason_table(summary.get("by_reason", []))
        self._populate_customer_table(summary.get("top_customers", []))
        self._populate_month_table(summary.get("by_month", []))
        self._populate_quotes_table(quotes)

    def _populate_reason_table(self, rows: list[dict]) -> None:
        self._reason_table.setSortingEnabled(False)
        self._reason_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._reason_table.setItem(i, 0, _cell(r.get("reason", "")))
            self._reason_table.setItem(i, 1, _cell_int(r.get("count", 0)))
            self._reason_table.setItem(i, 2, _cell_money(r.get("value", 0)))
        self._reason_table.setSortingEnabled(True)

    def _populate_customer_table(self, rows: list[dict]) -> None:
        self._cust_table.setSortingEnabled(False)
        self._cust_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._cust_table.setItem(i, 0, _cell(r.get("customer_name", "")))
            self._cust_table.setItem(i, 1, _cell(str(r.get("customer_id") or "")))
            self._cust_table.setItem(i, 2, _cell_int(r.get("count", 0)))
            self._cust_table.setItem(i, 3, _cell_money(r.get("value", 0)))
        self._cust_table.setSortingEnabled(True)

    def _populate_month_table(self, rows: list[dict]) -> None:
        self._month_table.setSortingEnabled(False)
        self._month_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._month_table.setItem(i, 0, _cell(r.get("month", "")))
            self._month_table.setItem(i, 1, _cell_int(r.get("count", 0)))
            self._month_table.setItem(i, 2, _cell_money(r.get("value", 0)))
        self._month_table.setSortingEnabled(True)

    def _populate_quotes_table(self, quotes: list[dict]) -> None:
        self._quotes_table.setSortingEnabled(False)
        self._quotes_table.setRowCount(len(quotes))
        self._quote_ids = []
        for i, q in enumerate(quotes):
            qid = q.get("id")
            self._quote_ids.append(int(qid) if qid else 0)

            created = (q.get("created_at") or "")[:10]
            lost_on = (q.get("updated_at") or "")[:10]
            days_open = _days_between(created, lost_on)
            value = q.get("calc_total") or q.get("header_total") or 0

            customer = q.get("customer_company") or q.get("customer_name") or "(no customer)"

            qnum = _cell(q.get("quote_number", ""))
            qnum.setData(Qt.ItemDataRole.UserRole, int(qid) if qid else 0)
            self._quotes_table.setItem(i, 0, qnum)
            self._quotes_table.setItem(i, 1, _cell(customer))
            self._quotes_table.setItem(i, 2, _cell(lost_on))
            self._quotes_table.setItem(i, 3, _cell_int(days_open))
            self._quotes_table.setItem(i, 4, _cell(q.get("lost_reason") or ""))
            self._quotes_table.setItem(i, 5, _cell(q.get("lost_notes") or ""))
            self._quotes_table.setItem(i, 6, _cell_money(value))
        self._quotes_table.setSortingEnabled(True)
        self._quotes_table.resizeColumnsToContents()

    # ── Row actions ─────────────────────────────────────────────

    def _on_quote_dbl_click(self, index) -> None:
        row = index.row()
        if row < 0:
            return
        item = self._quotes_table.item(row, 0)
        if not item:
            return
        qid = item.data(Qt.ItemDataRole.UserRole)
        if not qid:
            return
        dlg = QuoteDialog(quote_id=int(qid), parent=self)
        dlg.exec()
        self._load_data()


# ── Helpers ─────────────────────────────────────────────────────

def _cell(text) -> QTableWidgetItem:
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


def _cell_int(n) -> QTableWidgetItem:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    it = QTableWidgetItem(f"{n:,}")
    it.setData(Qt.ItemDataRole.EditRole, n)  # numeric sort
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


def _cell_money(v) -> QTableWidgetItem:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0.0
    it = QTableWidgetItem(f"${v:,.2f}")
    it.setData(Qt.ItemDataRole.EditRole, v)
    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


def _days_between(created: str, lost: str) -> int:
    try:
        from datetime import datetime
        c = datetime.strptime(created, "%Y-%m-%d")
        l = datetime.strptime(lost, "%Y-%m-%d")
        return max(0, (l - c).days)
    except Exception:
        return 0
