"""Accounts Receivable Aging Report screen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLabel, QAbstractItemView, QFrame, QTabWidget,
    QMessageBox,
)
from PySide6.QtGui import QColor, QFont

from db import get_aging_report


class AgingARScreen(QWidget):
    """Accounts Receivable Aging Report with summary + customer + invoice views."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_data()

    # ── UI Setup ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Accounts Receivable Aging</b>"))
        toolbar.addStretch()

        self._refresh_btn = QPushButton("🔄 Refresh")
        self._refresh_btn.clicked.connect(self._load_data)
        toolbar.addWidget(self._refresh_btn)
        layout.addLayout(toolbar)

        # Summary cards row
        self._cards_layout = QHBoxLayout()
        self._card_widgets: dict[str, QLabel] = {}
        for key, label, color in [
            ("current", "Current", "#27ae60"),
            ("days_1_30", "1-30 Days", "#f39c12"),
            ("days_31_60", "31-60 Days", "#e67e22"),
            ("days_61_90", "61-90 Days", "#e74c3c"),
            ("days_over_90", "90+ Days", "#c0392b"),
            ("total", "Total AR", "#3d4a38"),
        ]:
            card = self._make_card(label, "$0.00", color)
            self._card_widgets[key] = card
            self._cards_layout.addWidget(card)
        layout.addLayout(self._cards_layout)

        # Tabs: Customer Summary | Invoice Detail
        self._tabs = QTabWidget()

        # Customer summary tab
        self._cust_table = QTableWidget(0, 9)
        self._cust_table.setHorizontalHeaderLabels([
            "Customer", "Company", "Current", "1-30", "31-60",
            "61-90", "90+", "Total", "# Inv",
        ])
        self._cust_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cust_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cust_table.setSortingEnabled(True)
        self._cust_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._cust_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._cust_table, "👥 By Customer")

        # Invoice detail tab
        self._inv_table = QTableWidget(0, 9)
        self._inv_table.setHorizontalHeaderLabels([
            "Invoice #", "Customer", "Date", "Due", "Total",
            "Paid", "Balance", "Days Over", "Bucket",
        ])
        self._inv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._inv_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._inv_table.setSortingEnabled(True)
        self._inv_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._inv_table, "🧾 Invoice Detail")

        layout.addWidget(self._tabs)
        self.setLayout(layout)

    def _make_card(self, title: str, value: str, color: str) -> QLabel:
        """Create a summary card label."""
        card = QLabel()
        card.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.setMinimumHeight(70)
        card.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: white;
                border-radius: 6px;
                padding: 8px;
                font-size: 11pt;
            }}
        """)
        card.setText(f"<b>{title}</b><br/>{value}")
        return card

    def _update_card(self, key: str, title: str, amount: float) -> None:
        """Update a summary card's displayed value."""
        card = self._card_widgets.get(key)
        if card:
            card.setText(f"<b>{title}</b><br/>${amount:,.2f}")

    # ── Data Loading ────────────────────────────────────────────

    def _load_data(self) -> None:
        """Load the aging report and populate all views."""
        try:
            report = get_aging_report()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load aging report:\n{e}")
            return

        summary = report.get("summary", {})
        customers = report.get("customers", [])
        invoices = report.get("invoices", [])

        # Update summary cards
        self._update_card("current", "Current", summary.get("current", 0))
        self._update_card("days_1_30", "1-30 Days", summary.get("days_1_30", 0))
        self._update_card("days_31_60", "31-60 Days", summary.get("days_31_60", 0))
        self._update_card("days_61_90", "61-90 Days", summary.get("days_61_90", 0))
        self._update_card("days_over_90", "90+ Days", summary.get("days_over_90", 0))
        self._update_card("total", "Total AR", summary.get("total", 0))

        # Populate customer table
        self._populate_customer_table(customers)

        # Populate invoice table
        self._populate_invoice_table(invoices)

    def _populate_customer_table(self, customers: list[dict]) -> None:
        self._cust_table.setSortingEnabled(False)
        self._cust_table.setRowCount(len(customers))

        for row, cust in enumerate(customers):
            total = float(cust.get("total") or 0)

            # Color-code by worst bucket
            if cust.get("days_over_90", 0) > 0:
                bg = QColor(255, 210, 210)
            elif cust.get("days_61_90", 0) > 0:
                bg = QColor(255, 225, 210)
            elif cust.get("days_31_60", 0) > 0:
                bg = QColor(255, 240, 210)
            elif cust.get("days_1_30", 0) > 0:
                bg = QColor(255, 250, 220)
            else:
                bg = QColor(220, 255, 220)

            def item(text: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setBackground(bg)
                it.setTextAlignment(int(align) | int(Qt.AlignmentFlag.AlignVCenter))
                return it

            self._cust_table.setItem(row, 0, item(cust.get("customer_name", "")))
            self._cust_table.setItem(row, 1, item(cust.get("company", "")))
            self._cust_table.setItem(row, 2, item(f"${cust.get('current', 0):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 3, item(f"${cust.get('days_1_30', 0):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 4, item(f"${cust.get('days_31_60', 0):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 5, item(f"${cust.get('days_61_90', 0):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 6, item(f"${cust.get('days_over_90', 0):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 7, item(f"${total:,.2f}", Qt.AlignmentFlag.AlignRight))
            self._cust_table.setItem(row, 8, item(str(cust.get("invoice_count", 0)), Qt.AlignmentFlag.AlignCenter))

        self._cust_table.setSortingEnabled(True)
        self._cust_table.resizeColumnsToContents()

    def _populate_invoice_table(self, invoices: list[dict]) -> None:
        self._inv_table.setSortingEnabled(False)
        self._inv_table.setRowCount(len(invoices))

        bucket_labels = {
            "current": "Current",
            "days_1_30": "1-30 Days",
            "days_31_60": "31-60 Days",
            "days_61_90": "61-90 Days",
            "days_over_90": "90+ Days",
        }
        bucket_colors = {
            "current": QColor(220, 255, 220),
            "days_1_30": QColor(255, 250, 220),
            "days_31_60": QColor(255, 240, 210),
            "days_61_90": QColor(255, 225, 210),
            "days_over_90": QColor(255, 210, 210),
        }

        for row, inv in enumerate(invoices):
            bucket = inv.get("aging_bucket", "current")
            bg = bucket_colors.get(bucket, QColor(255, 255, 255))

            def item(text: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setBackground(bg)
                it.setTextAlignment(int(align) | int(Qt.AlignmentFlag.AlignVCenter))
                return it

            customer_name = inv.get("customer_name", "")
            if inv.get("customer_company"):
                customer_name = f"{customer_name} ({inv.get('customer_company')})"

            inv_date = str(inv.get("invoice_date", ""))[:10] if inv.get("invoice_date") else ""
            due_date = str(inv.get("due_date", ""))[:10] if inv.get("due_date") else ""

            self._inv_table.setItem(row, 0, item(inv.get("invoice_number", "")))
            self._inv_table.setItem(row, 1, item(customer_name))
            self._inv_table.setItem(row, 2, item(inv_date))
            self._inv_table.setItem(row, 3, item(due_date))
            self._inv_table.setItem(row, 4, item(f"${float(inv.get('total', 0)):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._inv_table.setItem(row, 5, item(f"${float(inv.get('amount_paid', 0)):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._inv_table.setItem(row, 6, item(f"${float(inv.get('balance_due', 0)):,.2f}", Qt.AlignmentFlag.AlignRight))
            self._inv_table.setItem(row, 7, item(str(inv.get("days_overdue", 0)), Qt.AlignmentFlag.AlignCenter))
            self._inv_table.setItem(row, 8, item(bucket_labels.get(bucket, bucket)))

        self._inv_table.setSortingEnabled(True)
        self._inv_table.resizeColumnsToContents()

    def refresh(self) -> None:
        """Public method to refresh the report."""
        self._load_data()
