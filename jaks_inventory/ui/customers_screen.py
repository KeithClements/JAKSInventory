"""Customers management screen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLineEdit,
    QMessageBox,
    QAbstractItemView,
    QMenu,
)
from PySide6.QtGui import QColor, QAction

import os
import sys

from db import (
    get_all_customers,
    delete_customer,
    get_customer_invoices,
    get_customer_grid_data,
)
from .customer_dialog import CustomerDialog
from .customer_360_dialog import Customer360Dialog
from .customer_detail_dialog import CustomerDetailDialog
from ..utils.pdf_statement import generate_statement_pdf


class _NumItem(QTableWidgetItem):
    """Sortable numeric table item."""

    def __init__(self, value: float, fmt: str = ""):
        super().__init__()
        self._value = value
        if fmt == "$":
            self.setText(f"${value:,.2f}" if value else "$0.00")
        elif fmt == "int":
            self.setText(str(int(value)))
        else:
            self.setText(str(value) if value else "")
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def __lt__(self, other):
        if isinstance(other, _NumItem):
            return self._value < other._value
        return super().__lt__(other)


class CustomersScreen(QWidget):
    """Screen for managing customers."""

    _COLUMNS = [
        "ID", "Name", "Company", "Phone", "Customer Type",
        "Balance", "Available Credit", "Last Order", "Open Cores",
        "Core Value", "Terms",
    ]
    # Column indices
    _COL_ID = 0
    _COL_NAME = 1
    _COL_COMPANY = 2
    _COL_PHONE = 3
    _COL_TYPE = 4
    _COL_BALANCE = 5
    _COL_AVAIL_CREDIT = 6
    _COL_LAST_ORDER = 7
    _COL_CORES = 8
    _COL_CORE_VAL = 9
    _COL_TERMS = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_customers()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Toolbar
        toolbar = QHBoxLayout()
        
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍 Search customers...")
        self._search_edit.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_edit, 1)
        
        self._add_btn = QPushButton("➕ Add Customer")
        self._add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(self._add_btn)
        
        self._view360_btn = QPushButton("🔄 360° View")
        self._view360_btn.clicked.connect(self._on_360)
        self._view360_btn.setEnabled(False)
        self._view360_btn.setToolTip("Full customer profile: CRM, Sales, Invoices, Deliveries")
        toolbar.addWidget(self._view360_btn)

        self._edit_btn = QPushButton("✏️ Edit")
        self._edit_btn.clicked.connect(self._on_edit)
        self._edit_btn.setEnabled(False)
        toolbar.addWidget(self._edit_btn)
        
        self._stmt_btn = QPushButton("📄 Statement")
        self._stmt_btn.clicked.connect(self._on_statement)
        self._stmt_btn.setEnabled(False)
        self._stmt_btn.setToolTip("Generate PDF statement for selected customer")
        toolbar.addWidget(self._stmt_btn)

        self._delete_btn = QPushButton("🗑️ Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        toolbar.addWidget(self._delete_btn)
        
        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setToolTip("Refresh")
        self._refresh_btn.clicked.connect(self._load_customers)
        toolbar.addWidget(self._refresh_btn)
        
        layout.addLayout(toolbar)

        # Table
        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setSectionResizeMode(
            self._COL_NAME, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            self._COL_COMPANY, QHeaderView.ResizeMode.Stretch
        )
        self._table.setColumnHidden(self._COL_ID, True)  # Hide ID column
        self._table.setAlternatingRowColors(True)
        
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_detail)

        # Right-click context menu
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        
        layout.addWidget(self._table)
        self.setLayout(layout)

    def _load_customers(self) -> None:
        """Load all customers with enriched data into the table."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        
        try:
            customers = get_customer_grid_data()
        except Exception:
            # Fallback to basic data if grid query fails
            customers_raw = get_all_customers(active_only=True)
            customers = [dict(c) for c in customers_raw] if customers_raw else []
        
        self._table.setRowCount(len(customers))
        
        for row_idx, customer in enumerate(customers):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, self._COL_ID, item(customer.get("id")))
            self._table.setItem(row_idx, self._COL_NAME, item(customer.get("name")))
            self._table.setItem(row_idx, self._COL_COMPANY, item(customer.get("company")))
            self._table.setItem(row_idx, self._COL_PHONE, item(customer.get("phone")))

            # Customer Type
            ctype = customer.get("customer_type") or ""
            type_item = item(ctype)
            self._table.setItem(row_idx, self._COL_TYPE, type_item)

            # Balance (numeric sortable)
            balance = float(customer.get("balance") or 0)
            bal_item = _NumItem(balance, "$")
            if balance > 0:
                bal_item.setForeground(QColor("#c62828"))
            self._table.setItem(row_idx, self._COL_BALANCE, bal_item)

            # Available Credit
            avail = float(customer.get("available_credit") or 0)
            credit_limit = float(customer.get("eff_credit_limit") or customer.get("credit_limit") or 0)
            avail_item = _NumItem(avail, "$")
            if credit_limit > 0 and avail <= 0:
                avail_item.setForeground(QColor("#c62828"))
            elif credit_limit > 0:
                avail_item.setForeground(QColor("#2e7d32"))
            self._table.setItem(row_idx, self._COL_AVAIL_CREDIT, avail_item)

            # Last Order Date
            last_date = str(customer.get("last_order_date") or "")[:10]
            self._table.setItem(row_idx, self._COL_LAST_ORDER, item(last_date))

            # Open Cores Count
            core_count = int(customer.get("open_core_count") or 0)
            core_item = _NumItem(core_count, "int")
            if core_count > 0:
                core_item.setForeground(QColor("#f57c00"))
            self._table.setItem(row_idx, self._COL_CORES, core_item)

            # Core Value
            core_value = float(customer.get("open_core_value") or 0)
            cv_item = _NumItem(core_value, "$")
            if core_value > 0:
                cv_item.setForeground(QColor("#f57c00"))
            self._table.setItem(row_idx, self._COL_CORE_VAL, cv_item)

            # Terms
            self._table.setItem(row_idx, self._COL_TERMS, item(customer.get("payment_terms")))

            # Row highlighting for on-hold customers
            if customer.get("on_hold"):
                for col in range(len(self._COLUMNS)):
                    it = self._table.item(row_idx, col)
                    if it:
                        it.setBackground(QColor(255, 230, 230))
        
        self._table.setSortingEnabled(True)
        self._table.resizeColumnsToContents()

    # ------ Context Menu (Right-Click) ------

    def _on_context_menu(self, pos) -> None:
        """Show right-click context menu for customer rows."""
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return

        row = idx.row()
        id_item = self._table.item(row, self._COL_ID)
        if not id_item:
            return

        customer_id = int(id_item.text())
        name_item = self._table.item(row, self._COL_NAME)
        customer_name = name_item.text() if name_item else ""

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { padding: 4px; }"
            "QMenu::item { padding: 6px 20px; }"
            "QMenu::item:selected { background: #e3f2fd; }"
        )

        open_profile = menu.addAction("👤 Open Profile")
        open_profile.triggered.connect(lambda: self._open_profile(customer_id))

        quote_cust = menu.addAction("📝 Quote Customer")
        quote_cust.triggered.connect(lambda: self._quote_customer(customer_id))

        menu.addSeparator()

        view_cores = menu.addAction("🔩 View Core Report")
        view_cores.triggered.connect(lambda: self._view_core_report(customer_id))

        view_history = menu.addAction("📜 View History")
        view_history.triggered.connect(lambda: self._view_history(customer_id))

        menu.addSeparator()

        gen_stmt = menu.addAction("📄 Generate Statement")
        gen_stmt.triggered.connect(self._on_statement)

        edit_act = menu.addAction("✏️ Edit")
        edit_act.triggered.connect(self._on_edit)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _open_profile(self, customer_id: int) -> None:
        """Open the customer detail/profile dialog."""
        dlg = CustomerDetailDialog(customer_id, parent=self)
        dlg.exec()
        self._load_customers()

    def _quote_customer(self, customer_id: int) -> None:
        """Open a new quote dialog for this customer."""
        from .quote_dialog import QuoteDialog
        dlg = QuoteDialog(customer_id=customer_id, parent=self)
        dlg.exec()

    def _view_core_report(self, customer_id: int) -> None:
        """Open the customer profile with Cores tab active."""
        dlg = CustomerDetailDialog(customer_id, parent=self)
        # Switch to Cores tab (index 3 after Profile, Metrics, Employees)
        tabs = dlg.findChild(type(dlg.findChildren(object)[0].__class__.__bases__[0])
                             if False else None)
        # Use simpler approach: find the tab widget and set it
        from PySide6.QtWidgets import QTabWidget
        for child in dlg.findChildren(QTabWidget):
            for i in range(child.count()):
                if "Core" in child.tabText(i):
                    child.setCurrentIndex(i)
                    break
            break
        dlg.exec()
        self._load_customers()

    def _view_history(self, customer_id: int) -> None:
        """Open the customer profile with History tab active."""
        dlg = CustomerDetailDialog(customer_id, parent=self)
        from PySide6.QtWidgets import QTabWidget
        for child in dlg.findChildren(QTabWidget):
            for i in range(child.count()):
                if "History" in child.tabText(i):
                    child.setCurrentIndex(i)
                    break
            break
        dlg.exec()
        self._load_customers()

    def _on_search(self, text: str) -> None:
        """Filter table by search text."""
        search = text.lower()
        for row in range(self._table.rowCount()):
            match = False
            for col in (self._COL_NAME, self._COL_COMPANY, self._COL_PHONE, self._COL_TYPE):
                item = self._table.item(row, col)
                if item and search in item.text().lower():
                    match = True
                    break
            self._table.setRowHidden(row, not match)

    def _on_selection_changed(self) -> None:
        """Update button states based on selection."""
        has_selection = len(self._table.selectedItems()) > 0
        self._view360_btn.setEnabled(has_selection)
        self._edit_btn.setEnabled(has_selection)
        self._stmt_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _get_selected_customer_id(self) -> int | None:
        """Get the ID of the selected customer."""
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self._table.item(row, 0)
        return int(id_item.text()) if id_item else None

    def _on_360(self) -> None:
        """Show Customer detail / 360 view."""
        customer_id = self._get_selected_customer_id()
        if not customer_id:
            return
        dlg = CustomerDetailDialog(customer_id, parent=self)
        dlg.exec()
        self._load_customers()

    def _on_statement(self) -> None:
        """Generate a PDF statement for the selected customer."""
        customer_id = self._get_selected_customer_id()
        if not customer_id:
            return
        try:
            pdf_path = generate_statement_pdf(customer_id)
            if sys.platform == "win32":
                os.startfile(pdf_path)
            self.window().statusBar().showMessage(f"Statement saved: {pdf_path}")
        except Exception as e:
            QMessageBox.critical(self, "Statement Error",
                                 f"Failed to generate statement:\n{e}")

    def _on_add(self) -> None:
        """Add a new customer (full detail dialog)."""
        dialog = CustomerDetailDialog(None, parent=self)
        if dialog.exec():
            self._load_customers()

    def _on_edit(self) -> None:
        """Edit selected customer (full detail dialog)."""
        customer_id = self._get_selected_customer_id()
        if not customer_id:
            return

        dialog = CustomerDetailDialog(customer_id, parent=self)
        if dialog.exec():
            self._load_customers()

    def _on_detail(self) -> None:
        """Open full customer profile (double-click handler)."""
        customer_id = self._get_selected_customer_id()
        if not customer_id:
            return
        dlg = CustomerDetailDialog(customer_id, parent=self)
        dlg.exec()
        self._load_customers()

    def _on_delete(self) -> None:
        """Delete selected customer."""
        customer_id = self._get_selected_customer_id()
        if not customer_id:
            return
        
        # Check for invoices - convert to list to release cursor
        invoices_raw = get_customer_invoices(customer_id)
        invoices = list(invoices_raw) if invoices_raw else []
        if invoices:
            QMessageBox.warning(
                self, "Cannot Delete",
                f"This customer has {len(invoices)} invoice(s) and cannot be deleted.\n"
                "Archive the customer instead."
            )
            return
        
        reply = QMessageBox.question(
            self, "Confirm Delete",
            "Are you sure you want to delete this customer?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            delete_customer(customer_id)
            self._load_customers()

    def refresh(self) -> None:
        """Public method to refresh the customer list."""
        self._load_customers()
