"""Reports Screen - Reorder, Sales, and Inventory Valuation Reports."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_low_stock_products,
    get_all_invoices,
    get_all_products,
    get_all_vendors,
    get_connection,
    get_velocity_report,
)


class ReportsScreen(QWidget):
    """Reports dashboard with multiple report types."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tab widget for different report types
        self._tabs = QTabWidget()
        
        # Add report tabs
        self._reorder_tab = ReorderReportTab()
        self._sales_tab = SalesReportTab()
        self._valuation_tab = InventoryValuationTab()
        self._dead_stock_tab = DeadStockTab()
        self._velocity_tab = VelocityReportTab()
        self._sales_tax_tab = SalesTaxCollectedTab()
        
        self._tabs.addTab(self._reorder_tab, "📦 Reorder Report")
        self._tabs.addTab(self._sales_tab, "💰 Sales Report")
        self._tabs.addTab(self._valuation_tab, "📊 Inventory Valuation")
        self._tabs.addTab(self._dead_stock_tab, "💀 Dead Stock")
        self._tabs.addTab(self._velocity_tab, "🚀 Sales Velocity")
        self._tabs.addTab(self._sales_tax_tab, "🧾 Sales Tax")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)
        self.setLayout(layout)

    def refresh(self) -> None:
        """Refresh all report tabs."""
        self._reorder_tab.refresh()
        self._sales_tab.refresh()
        self._valuation_tab.refresh()
        self._dead_stock_tab.refresh()
        self._velocity_tab.refresh()
        self._sales_tax_tab.refresh()


class ReorderReportTab(QWidget):
    """Reorder report - products below threshold with suggested PO."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Controls
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 100)
        self._threshold_spin.setValue(5)
        self._threshold_spin.setPrefix("Threshold ≤ ")
        self._threshold_spin.valueChanged.connect(self.refresh)

        self._reorder_qty_spin = QSpinBox()
        self._reorder_qty_spin.setRange(1, 100)
        self._reorder_qty_spin.setValue(10)
        self._reorder_qty_spin.setPrefix("Reorder Qty: ")

        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📥 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        self._create_po_btn = QPushButton("📋 Create PO from Selected")
        self._create_po_btn.setToolTip(
            "Beta — currently shows a placeholder summary instead of opening "
            "a pre-filled PO. Use the Purchase Orders screen to create POs."
        )
        self._create_po_btn.clicked.connect(self._create_po)
        self._create_po_btn.setEnabled(False)

        # Summary
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("font-size: 14pt; font-weight: bold;")

        # Table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Manufacturer", "Qty", "Threshold", "Reorder Qty", "Est. Cost"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # Layout
        controls = QHBoxLayout()
        controls.addWidget(self._threshold_spin)
        controls.addWidget(self._reorder_qty_spin)
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._export_btn)
        controls.addStretch()
        controls.addWidget(self._create_po_btn)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

        self._data: list[dict] = []

    def refresh(self) -> None:
        """Refresh the reorder report."""
        threshold = self._threshold_spin.value()
        reorder_qty = self._reorder_qty_spin.value()

        try:
            self._data = get_low_stock_products(threshold)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load data: {e}")
            return

        self._table.setRowCount(len(self._data))
        total_cost = Decimal("0.00")

        for row_idx, product in enumerate(self._data):
            sku = product.get("sku", "")
            title = product.get("title", "")
            manufacturer = product.get("manufacturer", "")
            qty = product.get("qty_on_hand", 0) or 0
            cost = Decimal(str(product.get("pai_cost", 0) or product.get("cost", 0) or 0))
            
            est_cost = cost * reorder_qty
            total_cost += est_cost

            items = [
                QTableWidgetItem(sku),
                QTableWidgetItem(title[:60] + "..." if len(title) > 60 else title),
                QTableWidgetItem(manufacturer or "—"),
                QTableWidgetItem(str(qty)),
                QTableWidgetItem(f"≤{threshold}"),
                QTableWidgetItem(str(reorder_qty)),
                QTableWidgetItem(f"${est_cost:.2f}"),
            ]

            # Color code qty
            qty_item = items[3]
            if qty == 0:
                qty_item.setBackground(QColor("#ffcccc"))
            elif qty <= threshold:
                qty_item.setBackground(QColor("#fff3cd"))

            for col_idx, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, product)
                self._table.setItem(row_idx, col_idx, item)

        self._summary_label.setText(
            f"⚠️ {len(self._data)} products need reorder | "
            f"Est. total: ${total_cost:.2f}"
        )

    def _on_selection_changed(self) -> None:
        """Enable/disable PO button based on selection."""
        selected = len(self._table.selectedItems()) > 0
        self._create_po_btn.setEnabled(selected)

    def _export_csv(self) -> None:
        """Export report to CSV."""
        if not self._data:
            QMessageBox.information(self, "No Data", "No data to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Reorder Report", 
            f"reorder_report_{date.today().isoformat()}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("SKU,Title,Manufacturer,Qty On Hand,Reorder Qty,Est Cost\n")
                reorder_qty = self._reorder_qty_spin.value()
                for product in self._data:
                    sku = product.get("sku", "")
                    title = product.get("title", "").replace(",", " ")
                    mfr = product.get("manufacturer", "")
                    qty = product.get("qty_on_hand", 0) or 0
                    cost = Decimal(str(product.get("pai_cost", 0) or 0))
                    est = cost * reorder_qty
                    f.write(f'"{sku}","{title}","{mfr}",{qty},{reorder_qty},{est:.2f}\n')
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")

    def _create_po(self) -> None:
        """Create purchase order from selected items."""
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            return

        # Get selected products
        products = []
        for row in selected_rows:
            item = self._table.item(row, 0)
            if item:
                product = item.data(Qt.ItemDataRole.UserRole)
                if product:
                    products.append(product)

        # TODO: Open PO dialog with these products pre-filled
        QMessageBox.information(
            self, "Create PO (Beta)",
            f"Would create PO with {len(products)} line items.\n\n"
            "This shortcut is still in beta — open the Purchase Orders "
            "screen to create the PO manually for now."
        )


class SalesReportTab(QWidget):
    """Sales report by date range."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Date range controls
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(date.today() - timedelta(days=30))

        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(date.today())

        self._refresh_btn = QPushButton("↻ Generate Report")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📥 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        # Summary panel
        self._summary_box = QGroupBox("Summary")
        summary_layout = QHBoxLayout()
        
        self._total_invoices_label = QLabel("Invoices: 0")
        self._total_revenue_label = QLabel("Revenue: $0.00")
        self._total_cost_label = QLabel("Cost: $0.00")
        self._total_margin_label = QLabel("Margin: $0.00 (0%)")
        
        for lbl in [self._total_invoices_label, self._total_revenue_label, 
                    self._total_cost_label, self._total_margin_label]:
            lbl.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 10px;")
            summary_layout.addWidget(lbl)
        
        self._summary_box.setLayout(summary_layout)

        # Table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "Invoice #", "Date", "Customer", "Items", "Total", "Status", "Paid"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # Layout
        controls = QHBoxLayout()
        controls.addWidget(QLabel("From:"))
        controls.addWidget(self._from_date)
        controls.addWidget(QLabel("To:"))
        controls.addWidget(self._to_date)
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._export_btn)
        controls.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self._summary_box)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

        self._data: list[dict] = []

    def refresh(self) -> None:
        """Generate sales report for date range."""
        from_date = self._from_date.date().toPython()
        to_date = self._to_date.date().toPython()

        try:
            # Get all invoices, filter by date range
            all_invoices = get_all_invoices(limit=1000)
            self._data = []
            for inv in all_invoices:
                inv_date = inv.get("invoice_date")
                if inv_date:
                    if isinstance(inv_date, str):
                        inv_date = date.fromisoformat(inv_date)
                    if from_date <= inv_date <= to_date:
                        self._data.append(inv)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load invoices: {e}")
            return

        # Calculate totals
        total_revenue = Decimal("0.00")
        total_paid = Decimal("0.00")
        paid_count = 0
        sent_count = 0

        self._table.setRowCount(len(self._data))
        for row_idx, inv in enumerate(self._data):
            inv_num = inv.get("invoice_number", "")
            inv_date = inv.get("invoice_date", "")
            customer_name = inv.get("customer_name", "—")
            total = Decimal(str(inv.get("total", 0) or 0))
            paid = Decimal(str(inv.get("amount_paid", 0) or 0))
            status = inv.get("status", "draft")
            line_count = inv.get("line_count", "—")

            total_revenue += total
            total_paid += paid
            if status == "paid":
                paid_count += 1
            elif status == "sent":
                sent_count += 1

            items = [
                QTableWidgetItem(inv_num),
                QTableWidgetItem(str(inv_date)),
                QTableWidgetItem(customer_name),
                QTableWidgetItem(str(line_count)),
                QTableWidgetItem(f"${total:.2f}"),
                QTableWidgetItem(status.upper()),
                QTableWidgetItem(f"${paid:.2f}"),
            ]

            # Color code status
            status_item = items[5]
            if status == "paid":
                status_item.setBackground(QColor("#d4edda"))
            elif status == "sent":
                status_item.setBackground(QColor("#cce5ff"))
            elif status == "void":
                status_item.setBackground(QColor("#f8d7da"))

            for col_idx, item in enumerate(items):
                self._table.setItem(row_idx, col_idx, item)

        # Update summary
        margin = total_paid  # Simplified - real margin would need cost data
        margin_pct = (margin / total_revenue * 100) if total_revenue > 0 else 0

        self._total_invoices_label.setText(f"Invoices: {len(self._data)}")
        self._total_revenue_label.setText(f"Revenue: ${total_revenue:.2f}")
        self._total_cost_label.setText(f"Paid: {paid_count} | Pending: {sent_count}")
        self._total_margin_label.setText(f"Collected: ${total_paid:.2f}")

    def _export_csv(self) -> None:
        """Export sales report to CSV."""
        if not self._data:
            QMessageBox.information(self, "No Data", "No data to export.")
            return

        from_str = self._from_date.date().toString("yyyy-MM-dd")
        to_str = self._to_date.date().toString("yyyy-MM-dd")

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sales Report",
            f"sales_report_{from_str}_to_{to_str}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("Invoice #,Date,Customer,Items,Total,Status,Paid\n")
                for inv in self._data:
                    inv_num = inv.get("invoice_number", "")
                    inv_date = inv.get("invoice_date", "")
                    customer = inv.get("customer_name", "").replace(",", " ")
                    lines = inv.get("line_count", 0)
                    total = inv.get("total", 0) or 0
                    status = inv.get("status", "")
                    paid = inv.get("amount_paid", 0) or 0
                    f.write(f'"{inv_num}","{inv_date}","{customer}",{lines},{total:.2f},"{status}",{paid:.2f}\n')
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")


class InventoryValuationTab(QWidget):
    """Inventory valuation report - total cost and retail value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Controls
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📥 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        # Summary panel
        self._summary_box = QGroupBox("Inventory Summary")
        summary_layout = QHBoxLayout()

        self._total_skus_label = QLabel("SKUs: 0")
        self._total_units_label = QLabel("Units: 0")
        self._total_cost_label = QLabel("Total Cost: $0.00")
        self._total_retail_label = QLabel("Total Retail: $0.00")
        self._total_margin_label = QLabel("Potential Margin: $0.00")

        for lbl in [self._total_skus_label, self._total_units_label,
                    self._total_cost_label, self._total_retail_label, self._total_margin_label]:
            lbl.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 10px;")
            summary_layout.addWidget(lbl)

        self._summary_box.setLayout(summary_layout)

        # Table - by category breakdown
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "Category", "SKUs", "Units", "Total Cost", "Total Retail", "Margin"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        # Layout
        controls = QHBoxLayout()
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._export_btn)
        controls.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self._summary_box)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

        self._data: dict[str, dict] = {}

    def refresh(self) -> None:
        """Calculate inventory valuation by category."""
        try:
            products = get_all_products(limit=10000)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load products: {e}")
            return

        # Aggregate by category
        self._data = {}
        total_skus = 0
        total_units = 0
        total_cost = Decimal("0.00")
        total_retail = Decimal("0.00")

        for p in products:
            category = p.get("category", "") or "Uncategorized"
            qty = p.get("qty_on_hand", 0) or 0
            cost = Decimal(str(p.get("pai_cost", 0) or p.get("cost", 0) or 0))
            retail = Decimal(str(p.get("selling_price", 0) or p.get("price", 0) or 0))

            if category not in self._data:
                self._data[category] = {
                    "skus": 0,
                    "units": 0,
                    "cost": Decimal("0.00"),
                    "retail": Decimal("0.00"),
                }

            self._data[category]["skus"] += 1
            self._data[category]["units"] += qty
            self._data[category]["cost"] += cost * qty
            self._data[category]["retail"] += retail * qty

            total_skus += 1
            total_units += qty
            total_cost += cost * qty
            total_retail += retail * qty

        # Update summary
        total_margin = total_retail - total_cost
        self._total_skus_label.setText(f"SKUs: {total_skus:,}")
        self._total_units_label.setText(f"Units: {total_units:,}")
        self._total_cost_label.setText(f"Total Cost: ${total_cost:,.2f}")
        self._total_retail_label.setText(f"Total Retail: ${total_retail:,.2f}")
        self._total_margin_label.setText(f"Potential Margin: ${total_margin:,.2f}")

        # Populate table
        categories = sorted(self._data.keys())
        self._table.setRowCount(len(categories))

        for row_idx, cat in enumerate(categories):
            data = self._data[cat]
            margin = data["retail"] - data["cost"]
            margin_pct = (margin / data["retail"] * 100) if data["retail"] > 0 else 0

            items = [
                QTableWidgetItem(cat),
                QTableWidgetItem(str(data["skus"])),
                QTableWidgetItem(str(data["units"])),
                QTableWidgetItem(f"${data['cost']:,.2f}"),
                QTableWidgetItem(f"${data['retail']:,.2f}"),
                QTableWidgetItem(f"${margin:,.2f} ({margin_pct:.1f}%)"),
            ]

            for col_idx, item in enumerate(items):
                self._table.setItem(row_idx, col_idx, item)

    def _export_csv(self) -> None:
        """Export valuation report to CSV."""
        if not self._data:
            QMessageBox.information(self, "No Data", "No data to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Inventory Valuation",
            f"inventory_valuation_{date.today().isoformat()}.csv",
            "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("Category,SKUs,Units,Total Cost,Total Retail,Margin,Margin %\n")
                for cat, data in sorted(self._data.items()):
                    margin = data["retail"] - data["cost"]
                    margin_pct = (margin / data["retail"] * 100) if data["retail"] > 0 else 0
                    f.write(f'"{cat}",{data["skus"]},{data["units"]},{data["cost"]:.2f},{data["retail"]:.2f},{margin:.2f},{margin_pct:.1f}\n')
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")


class DeadStockTab(QWidget):
    """Dead stock report — products with zero sales in N days."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Controls
        self._days_spin = QSpinBox()
        self._days_spin.setRange(30, 730)
        self._days_spin.setValue(90)
        self._days_spin.setSuffix(" days")

        self._refresh_btn = QPushButton("↻ Generate Report")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📥 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        # Summary
        self._summary_box = QGroupBox("Dead Stock Summary")
        summary_layout = QHBoxLayout()
        self._total_skus_label = QLabel("SKUs: 0")
        self._total_units_label = QLabel("Units: 0")
        self._tied_capital_label = QLabel("Tied-Up Capital: $0.00")
        self._retail_value_label = QLabel("Retail Value: $0.00")
        for lbl in [self._total_skus_label, self._total_units_label,
                     self._tied_capital_label, self._retail_value_label]:
            lbl.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 10px;")
            summary_layout.addWidget(lbl)
        self._summary_box.setLayout(summary_layout)

        # Table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Manufacturer", "On Hand", "Cost", "Retail", "Last Sold",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Layout
        controls = QHBoxLayout()
        controls.addWidget(QLabel("No sales in:"))
        controls.addWidget(self._days_spin)
        controls.addWidget(self._refresh_btn)
        controls.addWidget(self._export_btn)
        controls.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self._summary_box)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

        self._data: list[dict] = []

    def refresh(self) -> None:
        """Find products with no invoice line sales in N days."""
        days = self._days_spin.value()
        cutoff = (date.today() - timedelta(days=days)).isoformat()

        try:
            conn = get_connection()
            cursor = conn.execute("""
                SELECT p.id, p.sku, p.title, p.manufacturer,
                       p.qty_on_hand,
                       COALESCE(p.pai_cost, p.cost, 0) AS cost,
                       COALESCE(p.selling_price, p.price, 0) AS retail,
                       MAX(i.invoice_date) AS last_sold
                FROM products p
                LEFT JOIN invoice_lines il ON il.product_id = p.id
                LEFT JOIN invoices i ON i.id = il.invoice_id
                    AND i.status NOT IN ('void', 'draft')
                WHERE p.qty_on_hand > 0
                GROUP BY p.id
                HAVING MAX(i.invoice_date) IS NULL
                    OR MAX(i.invoice_date) < %s
                ORDER BY cost * p.qty_on_hand DESC
            """, (cutoff,))

            self._data = []
            for row in cursor.fetchall():
                self._data.append({
                    "sku": row[1], "title": row[2], "manufacturer": row[3],
                    "qty": row[4] or 0, "cost": float(row[5] or 0),
                    "retail": float(row[6] or 0), "last_sold": row[7] or "Never",
                })
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load data: {e}")
            return

        total_units = 0
        total_cost = Decimal("0.00")
        total_retail = Decimal("0.00")

        self._table.setRowCount(len(self._data))
        for row_idx, item in enumerate(self._data):
            qty = item["qty"]
            cost = Decimal(str(item["cost"]))
            retail = Decimal(str(item["retail"]))
            tied = cost * qty
            total_units += qty
            total_cost += tied
            total_retail += retail * qty

            cells = [
                QTableWidgetItem(item["sku"]),
                QTableWidgetItem(item["title"]),
                QTableWidgetItem(item["manufacturer"] or "—"),
                QTableWidgetItem(str(qty)),
                QTableWidgetItem(f"${cost:,.2f}"),
                QTableWidgetItem(f"${retail:,.2f}"),
                QTableWidgetItem(str(item["last_sold"])),
            ]

            # Highlight high-cost dead stock
            if tied > 500:
                cells[4].setBackground(QColor("#ffcccc"))
            elif tied > 100:
                cells[4].setBackground(QColor("#fff3cd"))

            for col_idx, cell in enumerate(cells):
                self._table.setItem(row_idx, col_idx, cell)

        self._total_skus_label.setText(f"SKUs: {len(self._data):,}")
        self._total_units_label.setText(f"Units: {total_units:,}")
        self._tied_capital_label.setText(f"Tied-Up Capital: ${total_cost:,.2f}")
        self._retail_value_label.setText(f"Retail Value: ${total_retail:,.2f}")

    def _export_csv(self) -> None:
        """Export dead stock report to CSV."""
        if not self._data:
            QMessageBox.information(self, "No Data", "Generate the report first.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Dead Stock Report",
            f"dead_stock_{date.today().isoformat()}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("SKU,Title,Manufacturer,On Hand,Unit Cost,Unit Retail,Last Sold\n")
                for item in self._data:
                    title = item["title"].replace(",", " ")
                    mfr = (item["manufacturer"] or "").replace(",", " ")
                    f.write(
                        f'"{item["sku"]}","{title}","{mfr}",'
                        f'{item["qty"]},{item["cost"]:.2f},{item["retail"]:.2f},'
                        f'"{item["last_sold"]}"\n'
                    )
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")


class VelocityReportTab(QWidget):
    """Sales velocity report — shows daily sell rate and recommended min/max."""

    _COLUMNS = ["SKU", "Title", "Vendor", "Qty", "Sold (Period)", "Velocity/Day",
                "Current Min", "Current Max", "Rec. Min", "Rec. Max"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[dict[str, Any]] = []

        # Controls
        self._days_spin = QSpinBox()
        self._days_spin.setRange(7, 365)
        self._days_spin.setValue(90)
        self._days_spin.setSuffix(" days")

        self._refresh_btn = QPushButton("↻ Generate")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📄 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        self._summary = QLabel()
        self._summary.setStyleSheet("font-size: 14pt; font-weight: bold;")

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        top = QHBoxLayout()
        top.addWidget(QLabel("Lookback:"))
        top.addWidget(self._days_spin)
        top.addWidget(self._refresh_btn)
        top.addWidget(self._export_btn)
        top.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self._summary)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

    def refresh(self) -> None:
        days = self._days_spin.value()
        try:
            rows = get_velocity_report(days)
        except Exception as e:
            self._summary.setText(f"Error: {e}")
            return

        self._data = list(rows or [])
        self._table.setRowCount(len(self._data))

        movers = sum(1 for r in self._data if (r.get("total_sold") or 0) > 0)
        self._summary.setText(
            f"📈 {len(self._data)} products  |  {movers} with sales in last {days} days"
        )

        for i, r in enumerate(self._data):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(i, 0, item(str(r.get("sku", ""))))
            self._table.setItem(i, 1, item(str(r.get("title", ""))))
            self._table.setItem(i, 2, item(str(r.get("vendor", "") or "")))
            self._table.setItem(i, 3, item(str(r.get("qty_on_hand", 0))))

            sold = r.get("total_sold") or 0
            velocity = r.get("daily_velocity") or 0.0
            self._table.setItem(i, 4, item(str(sold)))
            self._table.setItem(i, 5, item(f"{velocity:.3f}"))
            self._table.setItem(i, 6, item(str(r.get("current_min", 0))))
            self._table.setItem(i, 7, item(str(r.get("current_max", 0))))
            self._table.setItem(i, 8, item(str(r.get("recommended_min", 0))))
            self._table.setItem(i, 9, item(str(r.get("recommended_max", 0))))

            # Highlight rows where recommended differs from current
            rec_min = r.get("recommended_min") or 0
            rec_max = r.get("recommended_max") or 0
            cur_min = r.get("current_min") or 0
            cur_max = r.get("current_max") or 0
            if rec_min != cur_min or rec_max != cur_max:
                for c in range(len(self._COLUMNS)):
                    it = self._table.item(i, c)
                    if it:
                        it.setBackground(QColor(255, 255, 220))

        self._table.resizeColumnsToContents()

    def _export_csv(self) -> None:
        if not self._data:
            QMessageBox.information(self, "No Data", "Generate the report first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Velocity Report",
            f"velocity_report_{date.today().isoformat()}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(",".join(self._COLUMNS) + "\n")
                for r in self._data:
                    title = str(r.get("title", "")).replace(",", " ")
                    vendor = str(r.get("vendor", "") or "").replace(",", " ")
                    f.write(
                        f'"{r.get("sku", "")}","{title}","{vendor}",'
                        f'{r.get("qty_on_hand", 0)},{r.get("total_sold", 0)},'
                        f'{(r.get("daily_velocity") or 0):.3f},'
                        f'{r.get("current_min", 0)},{r.get("current_max", 0)},'
                        f'{r.get("recommended_min", 0)},{r.get("recommended_max", 0)}\n'
                    )
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")


class SalesTaxCollectedTab(QWidget):
    """Monthly sales tax collected report — filters sent/paid invoices with tax."""

    _COLUMNS = ["Month", "Invoices", "Taxable Subtotal", "Tax Rate", "Tax Collected"]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: list[dict[str, Any]] = []

        # Controls
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(date(date.today().year, 1, 1))
        self._from_date.setDisplayFormat("yyyy-MM-dd")

        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(date.today())
        self._to_date.setDisplayFormat("yyyy-MM-dd")

        self._refresh_btn = QPushButton("↻ Generate")
        self._refresh_btn.clicked.connect(self.refresh)

        self._export_btn = QPushButton("📄 Export CSV")
        self._export_btn.clicked.connect(self._export_csv)

        self._summary = QLabel()
        self._summary.setStyleSheet("font-size: 13pt; font-weight: bold;")

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        top = QHBoxLayout()
        top.addWidget(QLabel("From:"))
        top.addWidget(self._from_date)
        top.addWidget(QLabel("To:"))
        top.addWidget(self._to_date)
        top.addWidget(self._refresh_btn)
        top.addWidget(self._export_btn)
        top.addStretch()

        note = QLabel(
            "Includes invoices with status 'sent' or 'paid' that have a tax amount > 0. "
            "Grouped by calendar month."
        )
        note.setStyleSheet("color: #555; font-style: italic;")
        note.setWordWrap(True)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(note)
        layout.addWidget(self._summary)
        layout.addWidget(self._table, 1)
        self.setLayout(layout)

    def refresh(self) -> None:
        from_str = self._from_date.date().toString("yyyy-MM-dd")
        to_str = self._to_date.date().toString("yyyy-MM-dd")

        try:
            rows = self._query(from_str, to_str)
        except Exception as e:
            self._summary.setText(f"Error: {e}")
            return

        self._data = rows
        self._table.setRowCount(len(rows))

        total_tax = sum(r["tax_collected"] for r in rows)
        total_inv = sum(r["invoice_count"] for r in rows)
        self._summary.setText(
            f"🧾 {total_inv} invoices  |  Total tax collected: ${total_tax:,.2f}"
        )

        for i, r in enumerate(rows):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(i, 0, item(r["month"]))
            self._table.setItem(i, 1, item(str(r["invoice_count"])))
            self._table.setItem(i, 2, item(f"${r['taxable_subtotal']:,.2f}"))
            avg_rate = r["avg_tax_rate"] * 100 if r["avg_tax_rate"] else 0
            self._table.setItem(i, 3, item(f"{avg_rate:.3f}%"))
            self._table.setItem(i, 4, item(f"${r['tax_collected']:,.2f}"))

        self._table.resizeColumnsToContents()

    def _query(self, from_str: str, to_str: str) -> list[dict[str, Any]]:
        """Pull tax data grouped by month from the database."""
        sql = """
            SELECT
                strftime('%Y-%m', invoice_date) AS month,
                COUNT(*) AS invoice_count,
                SUM(COALESCE(
                    (SELECT SUM(CAST(quantity AS REAL) * CAST(unit_price AS REAL))
                     FROM invoice_lines il WHERE il.invoice_id = invoices.id), 0
                )) AS taxable_subtotal,
                AVG(CAST(tax_rate AS REAL)) AS avg_tax_rate,
                SUM(CAST(COALESCE(tax_amount, 0) AS REAL)) AS tax_collected
            FROM invoices
            WHERE status IN ('sent', 'paid')
              AND CAST(COALESCE(tax_amount, 0) AS REAL) > 0
              AND invoice_date >= ?
              AND invoice_date <= ?
            GROUP BY month
            ORDER BY month DESC
        """
        conn = get_connection()
        try:
            cur = conn.execute(sql, (from_str, to_str))
            cols = [d[0] for d in cur.description]
            rows = []
            for row in cur.fetchall():
                d = dict(zip(cols, row))
                d["taxable_subtotal"] = float(d.get("taxable_subtotal") or 0)
                d["avg_tax_rate"] = float(d.get("avg_tax_rate") or 0)
                d["tax_collected"] = float(d.get("tax_collected") or 0)
                d["invoice_count"] = int(d.get("invoice_count") or 0)
                rows.append(d)
            return rows
        finally:
            conn.close()

    def _export_csv(self) -> None:
        if not self._data:
            QMessageBox.information(self, "No Data", "Generate the report first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Sales Tax Report",
            f"sales_tax_{date.today().isoformat()}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self._COLUMNS)
                for r in self._data:
                    avg_rate = r["avg_tax_rate"] * 100 if r["avg_tax_rate"] else 0
                    writer.writerow([
                        r["month"],
                        r["invoice_count"],
                        f"{r['taxable_subtotal']:.2f}",
                        f"{avg_rate:.3f}%",
                        f"{r['tax_collected']:.2f}",
                    ])
            QMessageBox.information(self, "Exported", f"Saved to {path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Export failed: {e}")
