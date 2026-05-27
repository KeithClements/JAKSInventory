"""Inventory transfers screen (Phase I Part 4).

Manages stock transfers between storage locations.
Workflow: draft → pending → approved → completed (or cancelled)
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
    list_transfers,
    get_transfer,
    create_transfer,
    update_transfer_status,
    complete_transfer,
    cancel_transfer,
    delete_transfer,
    get_transfer_summary,
    list_locations,
)

from .transfer_dialog import TransferDialog, TransferLinesDialog


class TransfersScreen(QWidget):
    """Screen for managing inventory transfers between locations."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_transfers()
        self._load_summary()

    def _build_ui(self) -> None:
        # Summary cards
        self._summary_group = QGroupBox("Transfer Summary")
        summary_layout = QGridLayout()
        
        self._draft_label = QLabel("0")
        self._draft_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #757575;")
        
        self._pending_label = QLabel("0")
        self._pending_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #f57c00;")
        
        self._approved_label = QLabel("0")
        self._approved_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #1565c0;")
        
        self._completed_label = QLabel("0")
        self._completed_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2e7d32;")
        
        summary_layout.addWidget(QLabel("📝 Draft:"), 0, 0)
        summary_layout.addWidget(self._draft_label, 0, 1)
        summary_layout.addWidget(QLabel("⏳ Pending:"), 0, 2)
        summary_layout.addWidget(self._pending_label, 0, 3)
        summary_layout.addWidget(QLabel("✅ Approved:"), 0, 4)
        summary_layout.addWidget(self._approved_label, 0, 5)
        summary_layout.addWidget(QLabel("📦 Completed:"), 0, 6)
        summary_layout.addWidget(self._completed_label, 0, 7)
        
        self._summary_group.setLayout(summary_layout)

        # Filter row
        self._status_filter = QComboBox()
        self._status_filter.addItem("All Status", None)
        self._status_filter.addItem("📝 Draft", "draft")
        self._status_filter.addItem("⏳ Pending", "pending")
        self._status_filter.addItem("✅ Approved", "approved")
        self._status_filter.addItem("📦 Completed", "completed")
        self._status_filter.addItem("❌ Cancelled", "cancelled")
        self._status_filter.currentIndexChanged.connect(self._load_transfers)

        self._from_filter = QComboBox()
        self._from_filter.addItem("All From Locations", None)
        self._load_location_combos()
        self._from_filter.currentIndexChanged.connect(self._load_transfers)

        self._to_filter = QComboBox()
        self._to_filter.addItem("All To Locations", None)
        self._load_location_combos(self._to_filter)
        self._to_filter.currentIndexChanged.connect(self._load_transfers)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh)

        # Action buttons
        self._new_btn = QPushButton("➕ New Transfer")
        self._new_btn.clicked.connect(self._on_new)

        self._view_btn = QPushButton("👁️ View Lines")
        self._view_btn.clicked.connect(self._on_view_lines)
        self._view_btn.setEnabled(False)

        self._submit_btn = QPushButton("📤 Submit")
        self._submit_btn.clicked.connect(self._on_submit)
        self._submit_btn.setEnabled(False)

        self._approve_btn = QPushButton("✅ Approve")
        self._approve_btn.clicked.connect(self._on_approve)
        self._approve_btn.setEnabled(False)

        self._complete_btn = QPushButton("📦 Complete")
        self._complete_btn.clicked.connect(self._on_complete)
        self._complete_btn.setEnabled(False)

        self._cancel_btn = QPushButton("❌ Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)

        self._delete_btn = QPushButton("🗑️ Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Status:"))
        filter_row.addWidget(self._status_filter)
        filter_row.addWidget(QLabel("From:"))
        filter_row.addWidget(self._from_filter)
        filter_row.addWidget(QLabel("To:"))
        filter_row.addWidget(self._to_filter)
        filter_row.addStretch()
        filter_row.addWidget(refresh_btn)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._view_btn)
        btn_row.addWidget(self._submit_btn)
        btn_row.addWidget(self._approve_btn)
        btn_row.addWidget(self._complete_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()

        # Main table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "Transfer #", "From Location", "To Location", "Status",
            "Items", "Total Qty", "Requested", "Completed"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_view_lines)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        self._status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addWidget(self._summary_group)
        layout.addLayout(filter_row)
        layout.addLayout(btn_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)
        self.setLayout(layout)

        self._transfers: list[dict] = []

    def _load_location_combos(self, combo: QComboBox = None) -> None:
        """Load locations into filter combos."""
        if combo is None:
            combo = self._from_filter
        try:
            locations = list_locations(active_only=True)
            for loc in locations:
                code = str(loc.get("code", ""))
                name = str(loc.get("name", ""))
                display = f"{code} - {name}" if name else code
                combo.addItem(display, loc.get("id"))
        except Exception as e:
            log.debug("transfers location combo load failed: %s", e)

    def _load_summary(self) -> None:
        """Load summary statistics."""
        try:
            summary = get_transfer_summary()
            self._draft_label.setText(str(summary.get("draft", 0)))
            self._pending_label.setText(str(summary.get("pending", 0)))
            self._approved_label.setText(str(summary.get("approved", 0)))
            self._completed_label.setText(str(summary.get("completed", 0)))
        except Exception as e:
            log.warning("Transfers summary load failed: %s", e)

    def _load_transfers(self) -> None:
        """Load transfers from database with current filters."""
        status = self._status_filter.currentData()
        from_loc = self._from_filter.currentData()
        to_loc = self._to_filter.currentData()
        
        try:
            self._transfers = list_transfers(
                status=status,
                from_location_id=from_loc,
                to_location_id=to_loc,
                limit=500
            )
        except Exception as e:
            self._transfers = []
            log.warning("Transfers list load failed: %s", e)
        
        self._populate_table()

    def _populate_table(self) -> None:
        """Populate table with transfer data."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._transfers))

        status_colors = {
            "draft": QColor("#757575"),      # Gray
            "pending": QColor("#f57c00"),    # Orange
            "approved": QColor("#1565c0"),   # Blue
            "completed": QColor("#2e7d32"),  # Green
            "cancelled": QColor("#c62828"),  # Red
        }

        for row_idx, t in enumerate(self._transfers):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._table.setItem(row_idx, 0, item(t.get("transfer_number", "")))
            
            from_loc = f"{t.get('from_location_code', '')} - {t.get('from_location_name', '')}"
            self._table.setItem(row_idx, 1, item(from_loc))
            
            to_loc = f"{t.get('to_location_code', '')} - {t.get('to_location_name', '')}"
            self._table.setItem(row_idx, 2, item(to_loc))
            
            # Status with color
            status = str(t.get("status", ""))
            status_item = item(status.title())
            if status in status_colors:
                status_item.setForeground(status_colors[status])
            self._table.setItem(row_idx, 3, status_item)
            
            self._table.setItem(row_idx, 4, item(str(t.get("line_count", 0))))
            self._table.setItem(row_idx, 5, item(str(t.get("total_qty", 0) or 0)))
            self._table.setItem(row_idx, 6, item(str(t.get("requested_date", "") or "")[:10]))
            self._table.setItem(row_idx, 7, item(str(t.get("completed_date", "") or "")[:10]))

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._transfers)} transfers")

    def _on_selection_changed(self) -> None:
        """Handle table selection change."""
        row = self._table.currentRow()
        has_selection = row >= 0
        
        self._view_btn.setEnabled(has_selection)
        
        # Enable/disable action buttons based on status
        if has_selection and row < len(self._transfers):
            transfer = self._transfers[row]
            status = transfer.get("status", "")
            
            self._submit_btn.setEnabled(status == "draft")
            self._approve_btn.setEnabled(status == "pending")
            self._complete_btn.setEnabled(status == "approved")
            self._cancel_btn.setEnabled(status in ("draft", "pending", "approved"))
            self._delete_btn.setEnabled(status in ("draft", "cancelled"))
        else:
            self._submit_btn.setEnabled(False)
            self._approve_btn.setEnabled(False)
            self._complete_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)

    def _refresh(self) -> None:
        """Refresh all data."""
        self._load_transfers()
        self._load_summary()

    def _on_new(self) -> None:
        """Create a new transfer."""
        dlg = TransferDialog(parent=self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                transfer_id = create_transfer(
                    from_location_id=data["from_location_id"],
                    to_location_id=data["to_location_id"],
                    requested_by=data.get("requested_by"),
                    notes=data.get("notes")
                )
                self._refresh()
                
                # Open lines dialog to add items
                transfer = get_transfer(transfer_id)
                if transfer:
                    lines_dlg = TransferLinesDialog(transfer, self)
                    lines_dlg.exec()
                    self._refresh()
                    
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to create transfer: {e}")

    def _on_view_lines(self) -> None:
        """View/edit transfer line items."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        # Refresh transfer data
        transfer = get_transfer(transfer_id)
        if transfer:
            dlg = TransferLinesDialog(transfer, self)
            dlg.exec()
            self._refresh()

    def _on_submit(self) -> None:
        """Submit draft transfer for approval."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        try:
            update_transfer_status(transfer_id, "pending")
            self._refresh()
            QMessageBox.information(self, "Success", "Transfer submitted for approval")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to submit: {e}")

    def _on_approve(self) -> None:
        """Approve a pending transfer."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        reply = QMessageBox.question(
            self,
            "Approve Transfer",
            f"Approve transfer {transfer.get('transfer_number')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                update_transfer_status(transfer_id, "approved", by_user="user")
                self._refresh()
                QMessageBox.information(self, "Success", "Transfer approved")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to approve: {e}")

    def _on_complete(self) -> None:
        """Complete an approved transfer (moves inventory)."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        reply = QMessageBox.question(
            self,
            "Complete Transfer",
            f"Complete transfer {transfer.get('transfer_number')}?\n\n"
            "This will move inventory between locations.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                complete_transfer(transfer_id, completed_by="user")
                self._refresh()
                QMessageBox.information(self, "Success", "Transfer completed - inventory moved")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to complete: {e}")

    def _on_cancel(self) -> None:
        """Cancel a transfer."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        reply = QMessageBox.question(
            self,
            "Cancel Transfer",
            f"Cancel transfer {transfer.get('transfer_number')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                cancel_transfer(transfer_id)
                self._refresh()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to cancel: {e}")

    def _on_delete(self) -> None:
        """Delete a draft/cancelled transfer."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._transfers):
            return
        
        transfer = self._transfers[row]
        transfer_id = transfer.get("id")
        
        reply = QMessageBox.question(
            self,
            "Delete Transfer",
            "Permanently delete this transfer?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_transfer(transfer_id)
                self._refresh()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to delete: {e}")
