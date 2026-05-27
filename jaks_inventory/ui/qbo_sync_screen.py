"""QBO Sync screen for JAKS Inventory app."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QCheckBox,
    QSpinBox,
)
from PySide6.QtGui import QColor

# Import from parent package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

try:
    from qbo.client import QBOClient
    QBO_AVAILABLE = True
except ImportError:
    QBO_AVAILABLE = False

from db import browse_products, get_product_by_sku


class SyncWorker(QThread):
    """Background worker for QBO sync operations."""
    progress = Signal(int, str)  # (percent, message)
    finished = Signal(dict)  # result dict
    
    def __init__(self, operation: str, data: Any = None):
        super().__init__()
        self.operation = operation
        self.data = data
    
    def run(self):
        try:
            if self.operation == "fetch_qbo_items":
                self._fetch_qbo_items()
            elif self.operation == "compare":
                self._compare_items()
            elif self.operation == "sync_new_to_qbo":
                self._sync_new_to_qbo()
            elif self.operation == "sync_quantities":
                self._sync_quantities()
        except Exception as e:
            self.finished.emit({"success": False, "error": str(e)})
    
    def _fetch_qbo_items(self):
        self.progress.emit(10, "Connecting to QBO...")
        client = QBOClient()
        
        if not client.connect():
            self.finished.emit({"success": False, "error": "Could not connect to QBO"})
            return
        
        self.progress.emit(30, "Fetching items from QBO...")
        items = client.fetch_all_items()
        
        self.progress.emit(100, f"Found {len(items)} items")
        self.finished.emit({
            "success": True,
            "operation": "fetch_qbo_items",
            "items": items,
            "mock_mode": client.is_mock_mode,
        })
    
    def _compare_items(self):
        self.progress.emit(10, "Loading local products...")
        local_products = browse_products(limit=1000)
        
        self.progress.emit(30, "Connecting to QBO...")
        client = QBOClient()
        if not client.connect():
            self.finished.emit({"success": False, "error": "Could not connect to QBO"})
            return
        
        self.progress.emit(50, "Fetching QBO items...")
        qbo_items = client.fetch_all_items()
        
        # Build SKU lookup
        qbo_by_sku = {item["sku"].upper(): item for item in qbo_items if item.get("sku")}
        
        self.progress.emit(70, "Comparing...")
        
        comparison = []
        for product in local_products:
            sku = (product.get("sku") or "").upper()
            if not sku:
                continue
            
            qbo_item = qbo_by_sku.get(sku)
            local_qty = int(product.get("qty_on_hand", 0) or 0)
            local_price = float(product.get("selling_price", 0) or 0)
            
            if qbo_item:
                qbo_qty = int(qbo_item.get("qty_on_hand", 0) or 0)
                qbo_price = float(qbo_item.get("price", 0) or 0)
                status = "match" if local_qty == qbo_qty else "qty_diff"
                comparison.append({
                    "sku": sku,
                    "title": product.get("title", ""),
                    "local_qty": local_qty,
                    "qbo_qty": qbo_qty,
                    "local_price": local_price,
                    "qbo_price": qbo_price,
                    "qbo_id": qbo_item.get("id"),
                    "status": status,
                })
            else:
                comparison.append({
                    "sku": sku,
                    "title": product.get("title", ""),
                    "local_qty": local_qty,
                    "qbo_qty": None,
                    "local_price": local_price,
                    "qbo_price": None,
                    "qbo_id": None,
                    "status": "not_in_qbo",
                })
        
        self.progress.emit(100, f"Compared {len(comparison)} products")
        self.finished.emit({
            "success": True,
            "operation": "compare",
            "comparison": comparison,
            "mock_mode": client.is_mock_mode,
        })
    
    def _sync_new_to_qbo(self):
        """Push new items to QBO."""
        items = self.data or []
        if not items:
            self.finished.emit({"success": False, "error": "No items to sync"})
            return
        
        self.progress.emit(5, "Connecting to QBO...")
        client = QBOClient()
        
        if not client.connect():
            self.finished.emit({"success": False, "error": "Could not connect to QBO"})
            return
        
        created = []
        errors = []
        total = len(items)
        
        for idx, item_data in enumerate(items):
            pct = 10 + int((idx / max(total, 1)) * 85)
            self.progress.emit(pct, f"Creating {idx + 1}/{total}: {item_data.get('sku', '')}")
            
            result = client.create_item(
                sku=item_data.get("sku", ""),
                name=item_data.get("title", item_data.get("sku", "Unknown")),
                price=float(item_data.get("local_price", 0)),
                cost=float(item_data.get("cost", 0)),
                qty_on_hand=float(item_data.get("local_qty", 0)),
                description=item_data.get("description", ""),
            )
            
            if result:
                created.append(result)
            else:
                errors.append(item_data.get("sku", "Unknown"))
        
        self.progress.emit(100, f"Done! Created {len(created)}, errors: {len(errors)}")
        self.finished.emit({
            "success": True,
            "operation": "sync_new_to_qbo",
            "created": created,
            "errors": errors,
            "mock_mode": client.is_mock_mode,
        })
    
    def _sync_quantities(self):
        """Sync quantity differences to QBO."""
        items = self.data or []
        if not items:
            self.finished.emit({"success": False, "error": "No items to sync"})
            return
        
        self.progress.emit(5, "Connecting to QBO...")
        client = QBOClient()
        
        if not client.connect():
            self.finished.emit({"success": False, "error": "Could not connect to QBO"})
            return
        
        updated = []
        errors = []
        total = len(items)
        
        for idx, item_data in enumerate(items):
            pct = 10 + int((idx / max(total, 1)) * 85)
            self.progress.emit(pct, f"Updating {idx + 1}/{total}: {item_data.get('sku', '')}")
            
            qbo_id = item_data.get("qbo_id")
            if not qbo_id:
                errors.append(f"{item_data.get('sku', 'Unknown')}: No QBO ID")
                continue
            
            result = client.adjust_quantity(
                item_id=qbo_id,
                new_quantity=float(item_data.get("local_qty", 0)),
            )
            
            if result:
                updated.append(result)
            else:
                errors.append(item_data.get("sku", "Unknown"))
        
        self.progress.emit(100, f"Done! Updated {len(updated)}, errors: {len(errors)}")
        self.finished.emit({
            "success": True,
            "operation": "sync_quantities",
            "updated": updated,
            "errors": errors,
            "mock_mode": client.is_mock_mode,
        })


class QBOSyncScreen(QWidget):
    """Screen for QBO sync operations."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = None
        self._comparison: list[dict] = []
        self._build_ui()
        self._update_status()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Status bar
        status_box = QGroupBox("QBO Connection")
        status_layout = QHBoxLayout()
        
        self._status_label = QLabel("Checking...")
        self._connect_btn = QPushButton("🔌 Connect")
        self._connect_btn.clicked.connect(self._on_connect)
        
        status_layout.addWidget(self._status_label, 1)
        status_layout.addWidget(self._connect_btn)
        status_box.setLayout(status_layout)
        layout.addWidget(status_box)

        # Actions
        actions_box = QGroupBox("Sync Actions")
        actions_layout = QHBoxLayout()
        
        self._compare_btn = QPushButton("🔄 Compare Inventory")
        self._compare_btn.clicked.connect(self._on_compare)
        
        self._sync_new_btn = QPushButton("📤 Push New Items to QBO")
        self._sync_new_btn.clicked.connect(self._on_sync_new)
        self._sync_new_btn.setEnabled(False)
        
        self._sync_qty_btn = QPushButton("📊 Sync Quantities")
        self._sync_qty_btn.clicked.connect(self._on_sync_qty)
        self._sync_qty_btn.setEnabled(False)
        
        actions_layout.addWidget(self._compare_btn)
        actions_layout.addWidget(self._sync_new_btn)
        actions_layout.addWidget(self._sync_qty_btn)
        actions_layout.addStretch()
        actions_box.setLayout(actions_layout)
        layout.addWidget(actions_box)

        # Progress
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress_label = QLabel("")
        layout.addWidget(self._progress)
        layout.addWidget(self._progress_label)

        # Results table
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Local Qty", "QBO Qty", "Local Price", "QBO Price", "Status"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        layout.addWidget(self._table, 1)

        # Summary
        self._summary_label = QLabel("")
        layout.addWidget(self._summary_label)

        self.setLayout(layout)

    def _update_status(self) -> None:
        """Update connection status."""
        if not QBO_AVAILABLE:
            self._status_label.setText("❌ QBO SDK not installed")
            self._status_label.setStyleSheet("color: red;")
            return

        has_creds = all([
            os.environ.get("QBO_CLIENT_ID"),
            os.environ.get("QBO_CLIENT_SECRET"),
            os.environ.get("QBO_REALM_ID"),
        ])
        
        if has_creds:
            self._status_label.setText("✅ Credentials configured")
            self._status_label.setStyleSheet("color: green;")
        else:
            self._status_label.setText("⚠️ Mock mode (no credentials)")
            self._status_label.setStyleSheet("color: orange;")

    def _on_connect(self) -> None:
        """Test QBO connection."""
        if not QBO_AVAILABLE:
            QMessageBox.warning(self, "QBO Not Available", 
                "Install QBO SDK: pip install intuit-oauth quickbooks-python")
            return
        
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress_label.setText("Connecting...")
        
        self._worker = SyncWorker("fetch_qbo_items")
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.start()

    def _on_compare(self) -> None:
        """Compare local inventory with QBO."""
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress_label.setText("Comparing...")
        
        self._worker = SyncWorker("compare")
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_compare_finished)
        self._worker.start()

    def _on_sync_new(self) -> None:
        """Push new items to QBO."""
        new_items = [c for c in self._comparison if c["status"] == "not_in_qbo"]
        if not new_items:
            QMessageBox.information(self, "Nothing to Sync", "No new items to push to QBO.")
            return
        
        write_enabled = os.environ.get("QBO_WRITE_ENABLED", "0") == "1"
        if not write_enabled:
            QMessageBox.warning(
                self, "Writes Disabled",
                "QBO writes are disabled.\n\nSet QBO_WRITE_ENABLED=1 in .env to enable."
            )
            return
        
        # Confirm
        reply = QMessageBox.question(
            self, "Confirm Sync",
            f"Push {len(new_items)} new items to QuickBooks Online?\n\n"
            "This will create inventory items in QBO.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress_label.setText("Creating items in QBO...")
        
        self._worker = SyncWorker("sync_new_to_qbo", new_items)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_sync_new_finished)
        self._worker.start()

    def _on_sync_qty(self) -> None:
        """Sync quantity differences."""
        diff_items = [c for c in self._comparison if c["status"] == "qty_diff"]
        if not diff_items:
            QMessageBox.information(self, "Nothing to Sync", "All quantities match!")
            return
        
        write_enabled = os.environ.get("QBO_WRITE_ENABLED", "0") == "1"
        if not write_enabled:
            QMessageBox.warning(
                self, "Writes Disabled",
                "QBO writes are disabled.\n\nSet QBO_WRITE_ENABLED=1 in .env to enable."
            )
            return
        
        # Confirm
        reply = QMessageBox.question(
            self, "Confirm Sync",
            f"Update {len(diff_items)} item quantities in QuickBooks Online?\n\n"
            "This will update QBO quantities to match your local inventory.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._progress_label.setText("Syncing quantities to QBO...")
        
        self._worker = SyncWorker("sync_quantities", diff_items)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_sync_qty_finished)
        self._worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        """Handle progress update."""
        self._progress.setValue(percent)
        self._progress_label.setText(message)

    def _on_fetch_finished(self, result: dict) -> None:
        """Handle fetch completion."""
        self._progress.setVisible(False)
        
        if not result.get("success"):
            QMessageBox.critical(self, "Error", result.get("error", "Unknown error"))
            return
        
        items = result.get("items", [])
        mock = result.get("mock_mode", False)
        
        mode_str = " (mock)" if mock else ""
        QMessageBox.information(
            self, "Connected",
            f"✅ Connected to QBO{mode_str}\nFound {len(items)} items."
        )

    def _on_compare_finished(self, result: dict) -> None:
        """Handle comparison completion."""
        self._progress.setVisible(False)
        
        if not result.get("success"):
            QMessageBox.critical(self, "Error", result.get("error", "Unknown error"))
            return
        
        self._comparison = result.get("comparison", [])
        mock = result.get("mock_mode", False)
        
        self._populate_table()
        
        # Enable sync buttons
        has_new = any(c["status"] == "not_in_qbo" for c in self._comparison)
        has_diff = any(c["status"] == "qty_diff" for c in self._comparison)
        self._sync_new_btn.setEnabled(has_new)
        self._sync_qty_btn.setEnabled(has_diff)
        
        # Summary
        total = len(self._comparison)
        match = sum(1 for c in self._comparison if c["status"] == "match")
        diff = sum(1 for c in self._comparison if c["status"] == "qty_diff")
        new = sum(1 for c in self._comparison if c["status"] == "not_in_qbo")
        
        mode_str = " (mock data)" if mock else ""
        self._summary_label.setText(
            f"Total: {total} | ✅ Match: {match} | ⚠️ Qty Diff: {diff} | 🆕 Not in QBO: {new}{mode_str}"
        )

    def _populate_table(self) -> None:
        """Populate comparison table."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._comparison))

        status_colors = {
            "match": QColor(200, 255, 200),
            "qty_diff": QColor(255, 255, 200),
            "not_in_qbo": QColor(200, 200, 255),
        }

        for row_idx, c in enumerate(self._comparison):
            def item(text: str, bg: QColor = None) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if bg:
                    it.setBackground(bg)
                return it

            bg = status_colors.get(c["status"])
            
            self._table.setItem(row_idx, 0, item(c.get("sku", ""), bg))
            self._table.setItem(row_idx, 1, item(str(c.get("title", ""))[:40], bg))
            self._table.setItem(row_idx, 2, item(str(c.get("local_qty", 0))))
            
            qbo_qty = c.get("qbo_qty")
            self._table.setItem(row_idx, 3, item(str(qbo_qty) if qbo_qty is not None else "—"))
            
            self._table.setItem(row_idx, 4, item(f"${c.get('local_price', 0):.2f}"))
            
            qbo_price = c.get("qbo_price")
            self._table.setItem(row_idx, 5, item(f"${qbo_price:.2f}" if qbo_price is not None else "—"))
            
            status_text = {
                "match": "✅ Match",
                "qty_diff": "⚠️ Qty Diff",
                "not_in_qbo": "🆕 New",
            }.get(c["status"], c["status"])
            self._table.setItem(row_idx, 6, item(status_text, bg))

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)

    def _on_sync_new_finished(self, result: dict) -> None:
        """Handle new item sync completion."""
        self._progress.setVisible(False)
        
        if not result.get("success"):
            QMessageBox.critical(self, "Error", result.get("error", "Unknown error"))
            return
        
        created = result.get("created", [])
        errors = result.get("errors", [])
        mock = result.get("mock_mode", False)
        
        mode_str = " (mock mode)" if mock else ""
        
        msg = f"✅ Created {len(created)} items in QBO{mode_str}"
        if errors:
            msg += f"\n\n⚠️ {len(errors)} errors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... and {len(errors) - 10} more"
        
        QMessageBox.information(self, "Sync Complete", msg)
        
        # Refresh comparison
        self._on_compare()

    def _on_sync_qty_finished(self, result: dict) -> None:
        """Handle quantity sync completion."""
        self._progress.setVisible(False)
        
        if not result.get("success"):
            QMessageBox.critical(self, "Error", result.get("error", "Unknown error"))
            return
        
        updated = result.get("updated", [])
        errors = result.get("errors", [])
        mock = result.get("mock_mode", False)
        
        mode_str = " (mock mode)" if mock else ""
        
        msg = f"✅ Updated {len(updated)} quantities in QBO{mode_str}"
        if errors:
            msg += f"\n\n⚠️ {len(errors)} errors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... and {len(errors) - 10} more"
        
        QMessageBox.information(self, "Sync Complete", msg)
        
        # Refresh comparison  
        self._on_compare()
