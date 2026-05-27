"""Pricing Overview – Excel-style sortable table of all product pricing."""
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
    """Table item that sorts numerically."""

    def __init__(self, value: float, fmt: str = ""):
        super().__init__()
        self._value = value
        if fmt == "$":
            self.setText(f"${value:,.2f}")
        elif fmt == "%":
            self.setText(f"{value:.1f}%")
        else:
            self.setText(f"{value:,.0f}")

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._value < other._value
        return super().__lt__(other)


_COLUMNS = [
    "SKU", "Title", "Cost", "Selling Price", "Margin %",
    "Compare At", "Core Charge", "Qty", "Last Updated",
]


class PricingOverviewScreen(QWidget):
    """Excel-style view of all products with pricing columns, sortable."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        QTimer.singleShot(200, self._refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("💲 Pricing Overview")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍  Search SKU / Title …")
        self._search.setFixedWidth(260)
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

        count_row = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 12px; color: #6B7280;")
        count_row.addWidget(self._count_label)
        count_row.addStretch()
        layout.addLayout(count_row)

        # ── Table ──
        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #e5e7eb; font-size: 12px; }"
            "QHeaderView::section { background: #f3f4f6; padding: 4px; "
            "border: 1px solid #d1d5db; font-weight: 600; font-size: 11px; }"
        )
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT sku, title, pai_cost, selling_price, compare_at_price, "
                    "core_charge, qty_on_hand, updated_at FROM products ORDER BY sku"
                ).fetchall()
        except Exception:
            rows = []

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            cost = float(row["pai_cost"] or 0)
            price = float(row["selling_price"] or 0)
            margin = ((price - cost) / price * 100) if price else 0.0

            self._table.setItem(i, 0, QTableWidgetItem(row["sku"] or ""))
            self._table.setItem(i, 1, QTableWidgetItem(row["title"] or ""))
            self._table.setItem(i, 2, _NumItem(cost, "$"))
            self._table.setItem(i, 3, _NumItem(price, "$"))
            self._table.setItem(i, 4, _NumItem(margin, "%"))
            self._table.setItem(i, 5, _NumItem(float(row["compare_at_price"] or 0), "$"))
            self._table.setItem(i, 6, _NumItem(float(row["core_charge"] or 0), "$"))
            self._table.setItem(i, 7, _NumItem(float(row["qty_on_hand"] or 0)))
            self._table.setItem(i, 8, QTableWidgetItem(str(row["updated_at"] or "")))

            # Colour-code low margins
            if 0 < margin < 20:
                self._table.item(i, 4).setForeground(Qt.GlobalColor.red)
            elif margin >= 30:
                self._table.item(i, 4).setForeground(Qt.GlobalColor.darkGreen)

        self._table.setSortingEnabled(True)
        self._count_label.setText(f"{len(rows)} products")

    # ------------------------------------------------------------------
    def _filter_rows(self, text: str) -> None:
        text = text.lower()
        for r in range(self._table.rowCount()):
            match = any(
                text in (self._table.item(r, c).text().lower() if self._table.item(r, c) else "")
                for c in (0, 1)
            )
            self._table.setRowHidden(r, not match)
