"""Core returns tracking screen (Phase I Part 3).

Manages core deposits and returns for heavy duty diesel parts.
Workflow: pending → received → credited (or voided)
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt

log = logging.getLogger(__name__)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QGridLayout,
)

from db import (
    list_core_returns,
    get_core_return,
    receive_core,
    issue_core_credit,
    void_core_return,
    delete_core_return,
    get_pending_cores,
    get_core_summary,
    get_all_customers,
)

from .core_dialog import CoreDialog, ReceiveCoreDialog, IssueCreditDialog


class CoresScreen(QWidget):
    """Screen for managing core returns."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overdue_only = False  # D2: layered filter, not a hard replace
        self._build_ui()
        self._load_cores()
        self._load_summary()
        # Cross-screen refresh — invoice finalize / customer-detail
        # actions emit cores_changed so the list stays current without
        # forcing a tab switch.
        try:
            from .core_events import core_events
            core_events.cores_changed.connect(self._refresh)
        except Exception:
            pass

    def showEvent(self, event):  # noqa: D401
        """Refresh when this screen is shown so newly-finalized cores appear."""
        super().showEvent(event)
        try:
            self._refresh()
        except Exception:
            pass

    def _build_ui(self) -> None:
        # Summary cards
        self._summary_group = QGroupBox("Core Returns Summary")
        summary_layout = QGridLayout()
        
        self._pending_label = QLabel("0")
        self._pending_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #f57c00;")
        self._pending_value_label = QLabel("$0.00")
        
        self._received_label = QLabel("0")
        self._received_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1565c0;")
        self._received_value_label = QLabel("$0.00")
        
        self._credited_label = QLabel("0")
        self._credited_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2e7d32;")
        self._credited_value_label = QLabel("$0.00")
        
        self._voided_label = QLabel("0")
        self._voided_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #c62828;")
        
        summary_layout.addWidget(QLabel("⏳ Pending:"), 0, 0)
        summary_layout.addWidget(self._pending_label, 0, 1)
        summary_layout.addWidget(self._pending_value_label, 0, 2)
        
        summary_layout.addWidget(QLabel("📦 Received:"), 0, 3)
        summary_layout.addWidget(self._received_label, 0, 4)
        summary_layout.addWidget(self._received_value_label, 0, 5)
        
        summary_layout.addWidget(QLabel("💰 Credited:"), 0, 6)
        summary_layout.addWidget(self._credited_label, 0, 7)
        summary_layout.addWidget(self._credited_value_label, 0, 8)
        
        summary_layout.addWidget(QLabel("❌ Voided:"), 0, 9)
        summary_layout.addWidget(self._voided_label, 0, 10)
        
        self._summary_group.setLayout(summary_layout)

        # Filter row
        self._status_filter = QComboBox()
        self._status_filter.addItem("All Status", None)
        self._status_filter.addItem("⏳ Pending", "pending")
        self._status_filter.addItem("📦 Received", "received")
        self._status_filter.addItem("💰 Credited", "credited")
        self._status_filter.addItem("❌ Voided", "voided")
        self._status_filter.currentIndexChanged.connect(self._load_cores)

        self._customer_filter = QComboBox()
        self._customer_filter.addItem("All Customers", None)
        self._load_customers_combo()
        self._customer_filter.currentIndexChanged.connect(self._load_cores)

        self._overdue_btn = QPushButton("🚨 Show Overdue")
        self._overdue_btn.clicked.connect(self._show_overdue)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)

        # Action buttons
        self._receive_btn = QPushButton("📦 Receive Core")
        self._receive_btn.clicked.connect(self._on_receive)
        self._receive_btn.setEnabled(False)

        self._credit_btn = QPushButton("💰 Issue Credit")
        self._credit_btn.clicked.connect(self._on_issue_credit)
        self._credit_btn.setEnabled(False)

        self._void_btn = QPushButton("❌ Void")
        self._void_btn.clicked.connect(self._on_void)
        self._void_btn.setEnabled(False)

        self._view_btn = QPushButton("👁️ View Details")
        self._view_btn.clicked.connect(self._on_view)
        self._view_btn.setEnabled(False)

        self._delete_btn = QPushButton("🗑️ Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)

        self._receipt_btn = QPushButton("🖨 Print Receipt")
        self._receipt_btn.clicked.connect(self._on_print_receipt)
        self._receipt_btn.setEnabled(False)
        self._receipt_btn.setToolTip(
            "Print the customer-signable Core Return Receipt for the selected core's invoice."
        )

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Status:"))
        filter_row.addWidget(self._status_filter)
        filter_row.addWidget(QLabel("Customer:"))
        filter_row.addWidget(self._customer_filter)
        filter_row.addWidget(self._overdue_btn)
        filter_row.addStretch()
        filter_row.addWidget(refresh_btn)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._receive_btn)
        btn_row.addWidget(self._credit_btn)
        btn_row.addWidget(self._void_btn)
        btn_row.addWidget(self._view_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addWidget(self._receipt_btn)
        btn_row.addStretch()
        # Main table
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels([
            "Invoice #", "Customer", "SKU", "Product", "Core Charge",
            "Status", "Due Date", "Received", "Credit", "Open (Prod)"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_view)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_menu)

        self._status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addWidget(self._summary_group)
        layout.addLayout(filter_row)
        layout.addLayout(btn_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)
        self.setLayout(layout)

        self._cores: list[dict] = []

    def _load_customers_combo(self) -> None:
        """Load customers into filter combo."""
        try:
            customers = get_all_customers()
            for c in customers[:200]:
                name = str(c.get("name", ""))
                company = str(c.get("company", ""))
                display = f"{name} ({company})" if company else name
                self._customer_filter.addItem(display, c.get("id"))
        except Exception as e:
            log.debug("cores customer combo load failed: %s", e)

    def _load_summary(self) -> None:
        """Load summary statistics."""
        try:
            summary = get_core_summary()
            
            pending = summary.get("pending", {})
            self._pending_label.setText(str(pending.get("count", 0)))
            self._pending_value_label.setText(f"${pending.get('charge', 0):,.2f}")
            
            received = summary.get("received", {})
            self._received_label.setText(str(received.get("count", 0)))
            self._received_value_label.setText(f"${received.get('charge', 0):,.2f}")
            
            credited = summary.get("credited", {})
            self._credited_label.setText(str(credited.get("count", 0)))
            self._credited_value_label.setText(f"${credited.get('credited', 0):,.2f}")
            
            voided = summary.get("voided", {})
            self._voided_label.setText(str(voided.get("count", 0)))
        except Exception as e:
            log.warning("Cores summary load failed: %s", e)

    def _load_cores(self) -> None:
        """Load core returns from database with current filters."""
        status = self._status_filter.currentData()
        customer_id = self._customer_filter.currentData()
        
        try:
            self._cores = list_core_returns(
                status=status,
                customer_id=customer_id,
                limit=500
            )
        except Exception as e:
            self._cores = []
            log.warning("Cores list load failed: %s", e)

        # D2: Show-Overdue layer — applied on top of the status / customer
        # filters rather than replacing them.
        if self._overdue_only:
            from datetime import date
            today = date.today()
            kept: list[dict] = []
            for c in self._cores:
                if str(c.get("status") or "").lower() != "pending":
                    continue
                due = str(c.get("due_date") or "")[:10]
                if not due:
                    continue
                try:
                    if date.fromisoformat(due) < today:
                        kept.append(c)
                except ValueError:
                    pass
            self._cores = kept

        self._populate_table()

    def _populate_table(self) -> None:
        """Populate table with core return data."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._cores))

        status_colors = {
            "pending": QColor("#f57c00"),    # Orange
            "received": QColor("#1565c0"),   # Blue
            "credited": QColor("#2e7d32"),   # Green
            "voided": QColor("#c62828"),     # Red
        }

        for row_idx, c in enumerate(self._cores):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(c.get("invoice_number", "")))
            self._table.setItem(row_idx, 1, item(c.get("customer_name", "")))
            self._table.setItem(row_idx, 2, item(c.get("sku", "")))
            self._table.setItem(row_idx, 3, item(str(c.get("product_title", ""))[:35]))
            
            core_charge = float(c.get("core_charge", 0) or 0)
            charge_item = item(f"${core_charge:,.2f}")
            self._table.setItem(row_idx, 4, charge_item)
            
            # Status with color
            status = str(c.get("status", ""))
            status_item = item(status.title())
            if status in status_colors:
                status_item.setForeground(status_colors[status])
            self._table.setItem(row_idx, 5, status_item)
            
            # Due date - highlight if overdue
            due_date = str(c.get("due_date", "") or "")[:10]
            due_item = item(due_date)
            if due_date and status == "pending":
                from datetime import date
                try:
                    due = date.fromisoformat(due_date)
                    if due < date.today():
                        due_item.setForeground(QColor("#c62828"))
                        due_item.setText(f"⚠️ {due_date}")
                except ValueError:
                    pass
            self._table.setItem(row_idx, 6, due_item)
            
            self._table.setItem(row_idx, 7, item(str(c.get("received_date", "") or "")[:10]))
            
            credit = float(c.get("credit_issued", 0) or 0)
            credit_item = item(f"${credit:,.2f}" if credit else "")
            self._table.setItem(row_idx, 8, credit_item)

            open_qty = int(c.get("open_qty_for_product") or 0)
            open_item = item(str(open_qty) if open_qty else "")
            open_item.setToolTip(
                "Total cores still open (pending or received) for this "
                "product across all customers."
            )
            self._table.setItem(row_idx, 9, open_item)

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._cores)} core returns")

    def _on_selection_changed(self) -> None:
        """Handle table selection change."""
        row = self._table.currentRow()
        has_selection = row >= 0
        
        self._view_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)
        self._receipt_btn.setEnabled(has_selection)
        
        # Enable/disable action buttons based on status
        if has_selection and row < len(self._cores):
            core = self._cores[row]
            status = core.get("status", "")
            
            self._receive_btn.setEnabled(status == "pending")
            self._credit_btn.setEnabled(status == "received")
            self._void_btn.setEnabled(status in ("pending", "received"))
        else:
            self._receive_btn.setEnabled(False)
            self._credit_btn.setEnabled(False)
            self._void_btn.setEnabled(False)

    def _show_overdue(self) -> None:
        """Toggle the Show-Overdue layer on top of the current filters."""
        self._overdue_only = not self._overdue_only
        try:
            if self._overdue_only:
                self._overdue_btn.setText("\u2705 Showing Overdue")
                self._overdue_btn.setStyleSheet(
                    "QPushButton { background:#c62828; color:white; "
                    "padding:4px 10px; border-radius:4px; font-weight:bold; }"
                )
            else:
                self._overdue_btn.setText("\U0001f6a8 Show Overdue")
                self._overdue_btn.setStyleSheet("")
        except Exception:
            pass
        self._load_cores()
        if self._overdue_only:
            self._status_label.setText(
                f"{len(self._cores)} overdue core returns"
            )

    def _refresh(self) -> None:
        """Refresh all data."""
        self._load_cores()
        self._load_summary()

    def _on_receive(self) -> None:
        """Mark selected core as received."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        
        core = self._cores[row]
        core_id = core.get("id")
        
        dlg = ReceiveCoreDialog(core, self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                receive_core(
                    core_id=core_id,
                    condition=data.get("condition", "good"),
                    serial_number=data.get("serial_number"),
                    notes=data.get("notes"),
                    received_by=data.get("received_by"),
                )
                # UAT 1.8 #5 — after receive, the row would otherwise be
                # filtered out of the "Pending" view, hiding the "Issue
                # Credit" action. Broaden the filter to "All Status" so
                # the user sees the freshly-received core in place and
                # can act on it without hunting through filters.
                try:
                    if self._status_filter.currentData() == "pending":
                        # Index 0 is "All Status" (data=None).
                        self._status_filter.setCurrentIndex(0)
                except Exception:
                    pass
                self._refresh()
                # Re-select the same core so Issue Credit is enabled.
                self._select_core_id(core_id)
                # Notify other screens (customer detail) that cores changed.
                try:
                    from .core_events import core_events
                    core_events.cores_changed.emit()
                except Exception:
                    pass
                QMessageBox.information(self, "Success", "Core marked as received")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to receive core: {e}")

    def _select_core_id(self, core_id: int | None) -> None:
        """Select the row matching ``core_id`` if currently visible."""
        if not core_id:
            return
        try:
            for r, c in enumerate(self._cores):
                if c.get("id") == core_id:
                    self._table.selectRow(r)
                    return
        except Exception:
            pass

    def _on_issue_credit(self) -> None:
        """Issue credit for a received core."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        
        core = self._cores[row]
        core_id = core.get("id")
        cust_id = core.get("customer_id") or 0
        
        dlg = IssueCreditDialog(core, self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                issue_core_credit(
                    core_id=core_id,
                    credit_amount=data.get("credit_amount", 0),
                    credit_invoice_id=data.get("credit_invoice_id"),
                    notes=data.get("notes")
                )
                self._refresh()
                # Notify any open Customer Detail dialog so the credit
                # appears immediately on the customer's account
                # (UAT 1.8 #7).
                try:
                    from .core_events import core_events
                    core_events.core_credit_issued.emit(int(cust_id or 0), int(core_id or 0))
                    core_events.cores_changed.emit()
                except Exception:
                    pass
                QMessageBox.information(self, "Success", "Credit issued for core return")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to issue credit: {e}")

    def _on_void(self) -> None:
        """Void selected core return."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        
        core = self._cores[row]
        core_id = core.get("id")
        invoice = core.get("invoice_number", "N/A")
        
        reply = QMessageBox.question(
            self,
            "Void Core Return",
            f"Void core return for invoice {invoice}?\n\n"
            "This indicates the customer will not be returning the core.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Capture a void reason so the receipt and audit trail can
            # explain *why* the core was written off later.
            from PySide6.QtWidgets import QInputDialog
            reason, ok = QInputDialog.getText(
                self,
                "Void Reason",
                "Reason for voiding this core (optional):",
            )
            void_reason = reason.strip() if ok and reason else None
            try:
                void_core_return(
                    core_id,
                    notes="Voided by user",
                    void_reason=void_reason,
                )
                self._refresh()
                try:
                    from .core_events import core_events
                    core_events.cores_changed.emit()
                except Exception:
                    pass
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to void core: {e}")

    def _on_view(self) -> None:
        """View core return details."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        
        core = self._cores[row]
        dlg = CoreDialog(core, self)
        dlg.exec()

    def _on_table_menu(self, pos) -> None:
        """Right-click on a core row."""
        from PySide6.QtWidgets import QMenu, QApplication
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        if row < 0 or row >= len(self._cores):
            return
        self._table.selectRow(row)
        core = self._cores[row]
        status = str(core.get("status") or "").lower()

        menu = QMenu(self)
        a_view = menu.addAction("\U0001f441 View Details")
        a_recv = menu.addAction("\U0001f4e6 Mark Received")
        a_recv.setEnabled(status == "pending")
        a_credit = menu.addAction("\U0001f4b0 Issue Credit")
        a_credit.setEnabled(status == "received")
        a_void = menu.addAction("\u274c Void")
        a_void.setEnabled(status in ("pending", "received"))
        menu.addSeparator()
        a_print = menu.addAction("\U0001f5a8 Print Receipt")
        a_print.setEnabled(bool(core.get("invoice_id")))
        a_copy = menu.addAction("\U0001f4cb Copy SKU")
        a_copy.setEnabled(bool(core.get("sku")))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is a_view:
            self._on_view()
        elif chosen is a_recv:
            self._on_receive()
        elif chosen is a_credit:
            self._on_issue_credit()
        elif chosen is a_void:
            self._on_void()
        elif chosen is a_print:
            self._on_print_receipt()
        elif chosen is a_copy:
            try:
                QApplication.clipboard().setText(str(core.get("sku") or ""))
            except Exception:
                pass

    def _on_print_receipt(self) -> None:
        """Reprint the customer-signable Core Return Receipt for the selected core's invoice."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        core = self._cores[row]
        invoice_id = core.get("invoice_id")
        if not invoice_id:
            QMessageBox.warning(self, "No Invoice", "This core is not linked to an invoice.")
            return
        from ..utils.pdf_helpers import safe_generate_pdf
        from ..utils.pdf_core_receipt import generate_core_receipt_pdf
        try:
            safe_generate_pdf(generate_core_receipt_pdf, int(invoice_id), parent=self)
        except Exception as exc:
            QMessageBox.warning(self, "Core Receipt Failed", str(exc))

    def _on_delete(self) -> None:
        """Delete selected core return."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._cores):
            return
        
        core = self._cores[row]
        core_id = core.get("id")
        
        reply = QMessageBox.question(
            self,
            "Delete Core Return",
            "Permanently delete this core return record?\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_core_return(core_id)
                self._refresh()
                try:
                    from .core_events import core_events
                    core_events.cores_changed.emit()
                except Exception:
                    pass
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete: {e}")
