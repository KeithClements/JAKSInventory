"""Margin Analysis Screen - Product and customer profitability analysis."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from db import get_product_margins, get_customer_margins, get_margin_summary


class _KPICard(QFrame):
    def __init__(self, title: str, color: str = "#3d4a38", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"background: {color}; border-radius: 6px; padding: 8px;")
        self._title = QLabel(title)
        self._title.setStyleSheet("color: white; font-size: 10pt;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: #ccc; font-size: 9pt;")
        lay = QVBoxLayout()
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        self.setLayout(lay)

    def set_value(self, val: str, sub: str = ""):
        self._value.setText(str(val))
        self._sub.setText(sub)


class MarginScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()

        header = QLabel("📊 Margin Analysis")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        # KPIs
        kpi_row = QHBoxLayout()
        self._kpi_revenue = _KPICard("Total Revenue")
        self._kpi_cost = _KPICard("Total Cost")
        self._kpi_profit = _KPICard("Total Profit", "#2d5a2d")
        self._kpi_margin = _KPICard("Avg Margin %", "#4a6741")
        self._kpi_invoices = _KPICard("Invoices")
        for k in [self._kpi_revenue, self._kpi_cost, self._kpi_profit,
                   self._kpi_margin, self._kpi_invoices]:
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        # View toggle
        toolbar = QHBoxLayout()
        self._view_combo = QComboBox()
        self._view_combo.addItems(["Product Margins", "Customer Margins"])
        self._view_combo.currentTextChanged.connect(self._refresh_data)
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Margin %", "Revenue"])
        self._sort_combo.currentTextChanged.connect(self._refresh_data)
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(QLabel("View:"))
        toolbar.addWidget(self._view_combo)
        toolbar.addWidget(QLabel("Sort by:"))
        toolbar.addWidget(self._sort_combo)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Data table
        self._table = QTableWidget()
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        self.setLayout(layout)
        self._refresh()

    def _refresh(self):
        self._load_kpis()
        self._refresh_data()

    def _load_kpis(self):
        try:
            s = get_margin_summary()
            self._kpi_revenue.set_value(f"${s.get('total_revenue', 0):,.0f}")
            self._kpi_cost.set_value(f"${s.get('total_cost', 0):,.0f}")
            self._kpi_profit.set_value(f"${s.get('total_profit', 0):,.0f}")
            self._kpi_margin.set_value(f"{s.get('margin_pct', 0):.1f}%")
            self._kpi_invoices.set_value(str(s.get("invoice_count", 0)))
        except Exception:
            pass

    def _refresh_data(self):
        view = self._view_combo.currentText()
        if view == "Product Margins":
            self._load_product_margins()
        else:
            self._load_customer_margins()

    def _load_product_margins(self):
        sort_by = "margin_pct" if self._sort_combo.currentText() == "Margin %" else "total_revenue"
        try:
            data = get_product_margins(limit=200, sort_by=sort_by)
        except Exception:
            data = []
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Product", "List Price", "Cost", "Margin $", "Margin %",
            "Units Sold", "Total Revenue", "Total Profit"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setRowCount(len(data))
        for i, d in enumerate(data):
            self._table.setItem(i, 0, QTableWidgetItem(str(d.get("sku", ""))))
            self._table.setItem(i, 1, QTableWidgetItem(str(d.get("title", "") or "")[:40]))
            price = float(d.get("price", 0) or 0)
            self._table.setItem(i, 2, QTableWidgetItem(f"${price:,.2f}"))
            cost = float(d.get("cost", 0) or 0)
            self._table.setItem(i, 3, QTableWidgetItem(f"${cost:,.2f}"))
            margin_d = float(d.get("margin_dollars", 0) or 0)
            self._table.setItem(i, 4, QTableWidgetItem(f"${margin_d:,.2f}"))
            margin_p = float(d.get("margin_pct", 0) or 0)
            item = QTableWidgetItem(f"{margin_p:.1f}%")
            if margin_p < 10:
                item.setForeground(QColor("#FF4444"))
            elif margin_p > 40:
                item.setForeground(QColor("#44FF44"))
            self._table.setItem(i, 5, item)
            self._table.setItem(i, 6, QTableWidgetItem(str(d.get("units_sold", 0))))
            rev = float(d.get("total_revenue", 0) or 0)
            self._table.setItem(i, 7, QTableWidgetItem(f"${rev:,.2f}"))
            total_cost = float(d.get("total_cost", 0) or 0)
            self._table.setItem(i, 8, QTableWidgetItem(f"${rev - total_cost:,.2f}"))

    def _load_customer_margins(self):
        try:
            data = get_customer_margins(limit=100)
        except Exception:
            data = []
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "Customer", "Company", "Revenue", "Cost", "Profit", "Margin %", "Orders"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setRowCount(len(data))
        for i, d in enumerate(data):
            self._table.setItem(i, 0, QTableWidgetItem(str(d.get("name", "") or "")))
            self._table.setItem(i, 1, QTableWidgetItem(str(d.get("company", "") or "")))
            rev = float(d.get("total_revenue", 0) or 0)
            self._table.setItem(i, 2, QTableWidgetItem(f"${rev:,.2f}"))
            cost = float(d.get("total_cost", 0) or 0)
            self._table.setItem(i, 3, QTableWidgetItem(f"${cost:,.2f}"))
            profit = float(d.get("profit", 0) or 0)
            self._table.setItem(i, 4, QTableWidgetItem(f"${profit:,.2f}"))
            margin = float(d.get("margin_pct", 0) or 0)
            item = QTableWidgetItem(f"{margin:.1f}%")
            if margin < 10:
                item.setForeground(QColor("#FF4444"))
            elif margin > 40:
                item.setForeground(QColor("#44FF44"))
            self._table.setItem(i, 5, item)
            self._table.setItem(i, 6, QTableWidgetItem(str(d.get("order_count", 0))))
