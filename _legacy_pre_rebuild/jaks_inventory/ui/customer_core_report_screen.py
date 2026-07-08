"""Customer Core Report – Core return stats grouped by customer."""
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


class _NumItem(QTableWidgetItem):
    def __init__(self, value: float, fmt: str = ""):
        super().__init__()
        self._value = value
        if fmt == "$":
            self.setText(f"${value:,.2f}")
        else:
            self.setText(str(int(value)))

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._value < other._value
        return super().__lt__(other)


_COLUMNS = [
    "Customer", "Company", "Total Cores", "Pending", "Received",
    "Credited", "Overdue", "Core Charges $", "Credits Issued $",
]


class CustomerCoreReportScreen(QWidget):
    """Report showing core return statistics per customer."""

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
        title = QLabel("📋 Customer Core Report")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search customer …")
        self._search.setFixedWidth(240)
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
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #e5e7eb; font-size: 12px; }"
            "QHeaderView::section { background: #f3f4f6; padding: 4px; "
            "border: 1px solid #d1d5db; font-weight: 600; font-size: 11px; }"
        )
        layout.addWidget(self._table)

    def _refresh(self) -> None:
        try:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT
                        c.name,
                        c.company,
                        COUNT(cr.id) AS total,
                        SUM(CASE WHEN cr.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                        SUM(CASE WHEN cr.status = 'received' THEN 1 ELSE 0 END) AS received,
                        SUM(CASE WHEN cr.status = 'credited' THEN 1 ELSE 0 END) AS credited,
                        SUM(CASE WHEN cr.status = 'pending' AND cr.due_date < DATE('now') THEN 1 ELSE 0 END) AS overdue,
                        COALESCE(SUM(cr.core_charge), 0) AS charges,
                        COALESCE(SUM(cr.credit_issued), 0) AS credits
                    FROM core_returns cr
                    JOIN customers c ON c.id = cr.customer_id
                    GROUP BY c.id
                    ORDER BY total DESC
                """).fetchall()
        except Exception:
            rows = []

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(row["name"] or ""))
            self._table.setItem(i, 1, QTableWidgetItem(row["company"] or ""))
            self._table.setItem(i, 2, _NumItem(float(row["total"] or 0)))
            self._table.setItem(i, 3, _NumItem(float(row["pending"] or 0)))
            self._table.setItem(i, 4, _NumItem(float(row["received"] or 0)))
            self._table.setItem(i, 5, _NumItem(float(row["credited"] or 0)))
            self._table.setItem(i, 6, _NumItem(float(row["overdue"] or 0)))
            self._table.setItem(i, 7, _NumItem(float(row["charges"] or 0), "$"))
            self._table.setItem(i, 8, _NumItem(float(row["credits"] or 0), "$"))

            # Highlight overdue
            if row["overdue"] and int(row["overdue"]) > 0:
                self._table.item(i, 6).setForeground(Qt.GlobalColor.red)

        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(rows)} customers with cores")

    def _filter_rows(self, text: str) -> None:
        text = text.lower()
        for r in range(self._table.rowCount()):
            match = any(
                text in (self._table.item(r, c).text().lower() if self._table.item(r, c) else "")
                for c in (0, 1)
            )
            self._table.setRowHidden(r, not match)
