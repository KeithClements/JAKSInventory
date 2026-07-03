"""Shopify Orders Screen - Import orders and sync inventory."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class FetchOrdersWorker(QThread):
    """Background worker for fetching Shopify orders."""
    
    progress = Signal(int, str)
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(
        self,
        days_back: int = 7,
        status: str = "any",
        fulfillment_status: str = "any",
    ) -> None:
        super().__init__()
        self._days_back = days_back
        self._status = status
        self._fulfillment_status = fulfillment_status
    
    def run(self) -> None:
        try:
            from jaks_inventory.shopify import fetch_shopify_orders
            
            since = datetime.now() - timedelta(days=self._days_back)
            orders = fetch_shopify_orders(
                status=self._status,
                fulfillment_status=self._fulfillment_status,
                since_date=since,
                progress_callback=lambda p, msg: self.progress.emit(p, msg),
            )
            self.finished.emit(orders)
        except Exception as e:
            self.error.emit(str(e))


class ProcessOrdersWorker(QThread):
    """Background worker for processing orders (deducting inventory)."""
    
    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)
    
    def __init__(self, orders: list[dict], auto_deduct: bool = True) -> None:
        super().__init__()
        self._orders = orders
        self._auto_deduct = auto_deduct
    
    def run(self) -> None:
        try:
            from jaks_inventory.shopify import process_shopify_order
            
            results = {
                "processed": 0,
                "deductions": [],
                "errors": [],
                "skipped": [],
            }
            
            total = len(self._orders)
            for idx, order in enumerate(self._orders):
                order_num = order.get("order_number") or order.get("name", "?")
                self.progress.emit(idx + 1, f"Processing #{order_num} ({idx + 1}/{total})")
                
                result = process_shopify_order(order, auto_deduct=self._auto_deduct)
                results["processed"] += 1
                results["deductions"].extend(result.get("deductions", []))
                results["errors"].extend(result.get("errors", []))
                results["skipped"].extend(result.get("skipped", []))
            
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class SyncInventoryWorker(QThread):
    """Background worker for syncing inventory to Shopify."""
    
    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)
    
    def __init__(self, products: list[dict]) -> None:
        super().__init__()
        self._products = products
    
    def run(self) -> None:
        try:
            from jaks_inventory.shopify import sync_inventory_to_shopify
            
            result = sync_inventory_to_shopify(
                self._products,
                progress_callback=lambda p, msg: self.progress.emit(p, msg),
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class ShopifyOrdersScreen(QWidget):
    """Screen for managing Shopify orders and inventory sync."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tabs
        self._tabs = QTabWidget()
        
        self._orders_tab = OrdersImportTab()
        self._sync_tab = InventorySyncTab()
        
        self._tabs.addTab(self._orders_tab, "📥 Import Orders")
        self._tabs.addTab(self._sync_tab, "📤 Sync Inventory")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)
        self.setLayout(layout)


class OrdersImportTab(QWidget):
    """Tab for importing and processing Shopify orders."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Filters
        filters_box = QGroupBox("Fetch Options")
        filters_layout = QHBoxLayout()

        self._days_spin = QSpinBox()
        self._days_spin.setRange(1, 365)
        self._days_spin.setValue(7)
        self._days_spin.setPrefix("Last ")
        self._days_spin.setSuffix(" days")

        self._status_combo = QComboBox()
        self._status_combo.addItems(["any", "open", "closed", "cancelled"])

        self._fulfillment_combo = QComboBox()
        self._fulfillment_combo.addItems(["any", "unfulfilled", "partial", "shipped"])

        self._fetch_btn = QPushButton("📥 Fetch Orders")
        self._fetch_btn.clicked.connect(self._fetch_orders)

        filters_layout.addWidget(self._days_spin)
        filters_layout.addWidget(QLabel("Status:"))
        filters_layout.addWidget(self._status_combo)
        filters_layout.addWidget(QLabel("Fulfillment:"))
        filters_layout.addWidget(self._fulfillment_combo)
        filters_layout.addWidget(self._fetch_btn)
        filters_layout.addStretch()
        filters_box.setLayout(filters_layout)

        # Progress
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress_label = QLabel("")

        # Orders table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "Order #", "Date", "Customer", "Items", "Total", "Status", "Fulfillment"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # Actions
        actions_box = QGroupBox("Actions")
        actions_layout = QHBoxLayout()

        self._auto_deduct_check = QCheckBox("Auto-deduct inventory")
        self._auto_deduct_check.setChecked(True)

        self._process_btn = QPushButton("⚡ Process Selected Orders")
        self._process_btn.clicked.connect(self._process_orders)
        self._process_btn.setEnabled(False)

        self._process_all_btn = QPushButton("⚡ Process All Orders")
        self._process_all_btn.clicked.connect(self._process_all_orders)
        self._process_all_btn.setEnabled(False)

        actions_layout.addWidget(self._auto_deduct_check)
        actions_layout.addStretch()
        actions_layout.addWidget(self._process_btn)
        actions_layout.addWidget(self._process_all_btn)
        actions_box.setLayout(actions_layout)

        # Results log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(filters_box)
        layout.addWidget(self._progress)
        layout.addWidget(self._progress_label)
        layout.addWidget(self._table, 1)
        layout.addWidget(actions_box)
        layout.addWidget(QLabel("Processing Log:"))
        layout.addWidget(self._log)
        self.setLayout(layout)

        self._orders: list[dict] = []
        self._worker: FetchOrdersWorker | ProcessOrdersWorker | None = None

    def _fetch_orders(self) -> None:
        """Fetch orders from Shopify."""
        self._fetch_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # Indeterminate
        self._progress_label.setText("Fetching orders...")

        self._worker = FetchOrdersWorker(
            days_back=self._days_spin.value(),
            status=self._status_combo.currentText(),
            fulfillment_status=self._fulfillment_combo.currentText(),
        )
        self._worker.progress.connect(self._on_fetch_progress)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_fetch_progress(self, page: int, message: str) -> None:
        self._progress_label.setText(message)

    def _on_fetch_finished(self, orders: list[dict]) -> None:
        self._orders = orders
        self._fetch_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setText(f"Fetched {len(orders)} orders")

        self._populate_table()
        self._process_btn.setEnabled(len(orders) > 0)
        self._process_all_btn.setEnabled(len(orders) > 0)

        self._log.append(f"[{datetime.now().strftime('%H:%M:%S')}] Fetched {len(orders)} orders")

    def _on_fetch_error(self, error: str) -> None:
        self._fetch_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Fetch Error", f"Failed to fetch orders:\n{error}")

    def _populate_table(self) -> None:
        """Populate orders table."""
        self._table.setRowCount(len(self._orders))

        for row_idx, order in enumerate(self._orders):
            order_num = order.get("order_number") or order.get("name", "")
            created_at = order.get("created_at", "")[:10]
            customer = order.get("customer", {})
            customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or "Guest"
            line_items = order.get("line_items", [])
            total = Decimal(str(order.get("total_price", 0)))
            status = order.get("financial_status", "")
            fulfillment = order.get("fulfillment_status") or "unfulfilled"

            items = [
                QTableWidgetItem(str(order_num)),
                QTableWidgetItem(created_at),
                QTableWidgetItem(customer_name),
                QTableWidgetItem(str(len(line_items))),
                QTableWidgetItem(f"${total:.2f}"),
                QTableWidgetItem(status),
                QTableWidgetItem(fulfillment),
            ]

            # Color code fulfillment
            if fulfillment == "fulfilled":
                items[6].setBackground(QColor("#d4edda"))
            elif fulfillment == "partial":
                items[6].setBackground(QColor("#fff3cd"))
            else:
                items[6].setBackground(QColor("#f8d7da"))

            for col_idx, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, order)
                self._table.setItem(row_idx, col_idx, item)

    def _get_selected_orders(self) -> list[dict]:
        """Get selected orders from table."""
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())

        orders = []
        for row in selected_rows:
            item = self._table.item(row, 0)
            if item:
                order = item.data(Qt.ItemDataRole.UserRole)
                if order:
                    orders.append(order)
        return orders

    def _process_orders(self) -> None:
        """Process selected orders."""
        orders = self._get_selected_orders()
        if not orders:
            QMessageBox.information(self, "No Selection", "Select orders to process.")
            return
        self._run_process(orders)

    def _process_all_orders(self) -> None:
        """Process all fetched orders."""
        if not self._orders:
            return
        self._run_process(self._orders)

    def _run_process(self, orders: list[dict]) -> None:
        """Run the order processing worker."""
        auto_deduct = self._auto_deduct_check.isChecked()
        
        confirm = QMessageBox.question(
            self, "Confirm Processing",
            f"Process {len(orders)} orders?\n\n"
            f"Auto-deduct inventory: {'Yes' if auto_deduct else 'No'}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._process_btn.setEnabled(False)
        self._process_all_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(orders))
        self._progress.setValue(0)

        self._worker = ProcessOrdersWorker(orders, auto_deduct=auto_deduct)
        self._worker.progress.connect(self._on_process_progress)
        self._worker.finished.connect(self._on_process_finished)
        self._worker.error.connect(self._on_process_error)
        self._worker.start()

    def _on_process_progress(self, count: int, message: str) -> None:
        self._progress.setValue(count)
        self._progress_label.setText(message)

    def _on_process_finished(self, results: dict) -> None:
        self._process_btn.setEnabled(True)
        self._process_all_btn.setEnabled(True)
        self._progress.setVisible(False)

        deductions = results.get("deductions", [])
        errors = results.get("errors", [])
        skipped = results.get("skipped", [])

        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"\n[{timestamp}] Processing complete:")
        self._log.append(f"  ✅ Deductions: {len(deductions)}")
        self._log.append(f"  ⏭️ Skipped (not in inventory): {len(skipped)}")
        self._log.append(f"  ❌ Errors: {len(errors)}")

        if deductions:
            self._log.append("  Deducted:")
            for d in deductions[:10]:  # Show first 10
                self._log.append(f"    - {d['sku']}: -{d['quantity']}")
            if len(deductions) > 10:
                self._log.append(f"    ... and {len(deductions) - 10} more")

        if errors:
            self._log.append("  Errors:")
            for e in errors[:5]:
                self._log.append(f"    - {e['sku']}: {e['error']}")

        self._progress_label.setText(
            f"Processed {results.get('processed', 0)} orders, "
            f"{len(deductions)} deductions, {len(errors)} errors"
        )

    def _on_process_error(self, error: str) -> None:
        self._process_btn.setEnabled(True)
        self._process_all_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Process Error", f"Failed to process orders:\n{error}")


class InventorySyncTab(QWidget):
    """Tab for syncing inventory levels to Shopify."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Info box
        info_box = QGroupBox("Push Inventory to Shopify")
        info_layout = QVBoxLayout()
        info_layout.addWidget(QLabel(
            "This will update inventory levels in Shopify to match your local database.\n"
            "Only products with matching SKUs will be updated."
        ))
        info_box.setLayout(info_layout)

        # Options
        options_box = QGroupBox("Options")
        options_layout = QHBoxLayout()

        self._only_positive_check = QCheckBox("Only sync products with qty > 0")
        self._only_positive_check.setChecked(False)

        self._category_combo = QComboBox()
        self._category_combo.addItem("All Categories", None)

        options_layout.addWidget(self._only_positive_check)
        options_layout.addWidget(QLabel("Category:"))
        options_layout.addWidget(self._category_combo)
        options_layout.addStretch()
        options_box.setLayout(options_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        
        self._preview_btn = QPushButton("👁️ Preview Sync")
        self._preview_btn.clicked.connect(self._preview_sync)

        self._sync_btn = QPushButton("📤 Push to Shopify")
        self._sync_btn.clicked.connect(self._run_sync)

        btn_layout.addWidget(self._preview_btn)
        btn_layout.addWidget(self._sync_btn)
        btn_layout.addStretch()

        # Progress
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress_label = QLabel("")

        # Preview table
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["SKU", "Title", "Qty to Sync"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Results log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(info_box)
        layout.addWidget(options_box)
        layout.addLayout(btn_layout)
        layout.addWidget(self._progress)
        layout.addWidget(self._progress_label)
        layout.addWidget(self._table, 1)
        layout.addWidget(QLabel("Sync Log:"))
        layout.addWidget(self._log)
        self.setLayout(layout)

        self._products: list[dict] = []
        self._worker: SyncInventoryWorker | None = None

        # Load categories
        self._load_categories()

    def _load_categories(self) -> None:
        """Load categories for filter."""
        try:
            from db import get_all_categories
            categories = get_all_categories()
            for cat in categories:
                self._category_combo.addItem(cat, cat)
        except Exception:
            pass

    def _preview_sync(self) -> None:
        """Preview what will be synced."""
        try:
            from db import get_all_products
            
            products = get_all_products(limit=10000)
            
            # Filter
            only_positive = self._only_positive_check.isChecked()
            category = self._category_combo.currentData()
            
            filtered = []
            for p in products:
                qty = p.get("qty_on_hand", 0) or 0
                if only_positive and qty <= 0:
                    continue
                if category and p.get("category") != category:
                    continue
                filtered.append(p)
            
            self._products = filtered
            self._populate_table()
            
            self._log.append(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Preview: {len(filtered)} products to sync"
            )
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load products: {e}")

    def _populate_table(self) -> None:
        """Populate preview table."""
        self._table.setRowCount(len(self._products))

        for row_idx, product in enumerate(self._products):
            sku = product.get("sku", "")
            title = product.get("title", "")
            qty = product.get("qty_on_hand", 0) or 0

            items = [
                QTableWidgetItem(sku),
                QTableWidgetItem(title[:50] + "..." if len(title) > 50 else title),
                QTableWidgetItem(str(qty)),
            ]

            for col_idx, item in enumerate(items):
                self._table.setItem(row_idx, col_idx, item)

    def _run_sync(self) -> None:
        """Run the inventory sync to Shopify."""
        if not self._products:
            QMessageBox.information(self, "No Products", "Preview first to see what will be synced.")
            return

        confirm = QMessageBox.question(
            self, "Confirm Sync",
            f"Push inventory levels for {len(self._products)} products to Shopify?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._sync_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(self._products))
        self._progress.setValue(0)

        self._worker = SyncInventoryWorker(self._products)
        self._worker.progress.connect(self._on_sync_progress)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.error.connect(self._on_sync_error)
        self._worker.start()

    def _on_sync_progress(self, count: int, message: str) -> None:
        self._progress.setValue(count)
        self._progress_label.setText(message)

    def _on_sync_finished(self, results: dict) -> None:
        self._sync_btn.setEnabled(True)
        self._progress.setVisible(False)

        updated = results.get("updated", [])
        not_found = results.get("not_found", [])
        errors = results.get("errors", [])

        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log.append(f"\n[{timestamp}] Sync complete:")
        self._log.append(f"  ✅ Updated: {len(updated)}")
        self._log.append(f"  ⏭️ Not found in Shopify: {len(not_found)}")
        self._log.append(f"  ❌ Errors: {len(errors)}")

        self._progress_label.setText(
            f"Synced {len(updated)} products, "
            f"{len(not_found)} not found, {len(errors)} errors"
        )

        if not results.get("success"):
            error_msg = results.get("error", "Unknown error")
            QMessageBox.warning(self, "Sync Issue", f"Sync completed with issues:\n{error_msg}")

    def _on_sync_error(self, error: str) -> None:
        self._sync_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._progress_label.setText(f"Error: {error}")
        QMessageBox.warning(self, "Sync Error", f"Failed to sync inventory:\n{error}")
