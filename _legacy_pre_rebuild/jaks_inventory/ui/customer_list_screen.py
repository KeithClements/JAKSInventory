"""Customer List – Excel-style sortable view of all customers (category overview)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db.database import get_db
from .customer_detail_dialog import CustomerDetailDialog


class _NumItem(QTableWidgetItem):
    def __init__(self, value: float, fmt: str = ""):
        super().__init__()
        self._value = value
        if fmt == "$":
            self.setText(f"${value:,.2f}")
        else:
            self.setText(f"{value:,.0f}" if value == int(value) else f"{value:,.2f}")

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._value < other._value
        return super().__lt__(other)


_COLUMNS = [
    "ID", "Name", "Company", "City", "State", "Zip",
    "Phone", "Terms", "Total Spend", "Invoices",
]


class CustomerListScreen(QWidget):
    """Excel-style customer table sortable by city, zip, spend, etc."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        QTimer.singleShot(200, self._refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("👥 All Customers")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search name / company / city …")
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._filter_rows)
        header.addWidget(self._search)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; border: none; "
            "padding: 8px 16px; border-radius: 4px; font-weight: 500; }"
            "QPushButton:hover { background-color: #556B2F; }"
        )
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        layout.addWidget(self._count_label)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(0, True)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #e5e7eb; font-size: 12px; }"
            "QHeaderView::section { background: #f3f4f6; padding: 4px; "
            "border: 1px solid #d1d5db; font-weight: 600; font-size: 11px; }"
        )
        self._table.doubleClicked.connect(self._on_detail)
        layout.addWidget(self._table)

    def _refresh(self) -> None:
        try:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT c.id, c.name, c.company, c.city, c.state, c.zip,
                           c.phone, c.payment_terms,
                           COALESCE(s.total_spend, 0) AS total_spend,
                           COALESCE(s.inv_count, 0) AS inv_count
                    FROM customers c
                    LEFT JOIN (
                        SELECT customer_id,
                               SUM(total) AS total_spend,
                               COUNT(*) AS inv_count
                        FROM invoices
                        WHERE status NOT IN ('void', 'draft')
                        GROUP BY customer_id
                    ) s ON s.customer_id = c.id
                    ORDER BY c.name
                """).fetchall()
        except Exception:
            rows = []

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row["id"])))
            self._table.setItem(i, 1, QTableWidgetItem(row["name"] or ""))
            self._table.setItem(i, 2, QTableWidgetItem(row["company"] or ""))
            self._table.setItem(i, 3, QTableWidgetItem(row["city"] or ""))
            self._table.setItem(i, 4, QTableWidgetItem(row["state"] or ""))
            self._table.setItem(i, 5, QTableWidgetItem(row["zip"] or ""))
            self._table.setItem(i, 6, QTableWidgetItem(row["phone"] or ""))
            self._table.setItem(i, 7, QTableWidgetItem(row["payment_terms"] or ""))
            self._table.setItem(i, 8, _NumItem(float(row["total_spend"] or 0), "$"))
            self._table.setItem(i, 9, _NumItem(float(row["inv_count"] or 0)))

        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(rows)} customers")

    def _on_detail(self) -> None:
        """Open full customer profile on double-click."""
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if not item:
            return
        customer_id = int(item.text())
        dlg = CustomerDetailDialog(customer_id, parent=self)
        dlg.exec()
        self._refresh()

    def _filter_rows(self, text: str) -> None:
        text = text.lower()
        for r in range(self._table.rowCount()):
            match = any(
                text in (self._table.item(r, c).text().lower() if self._table.item(r, c) else "")
                for c in (1, 2, 3, 4, 5)
            )
            self._table.setRowHidden(r, not match)
