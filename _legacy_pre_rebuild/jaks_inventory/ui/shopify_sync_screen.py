"""Shopify Sync Screen - Manage sync between Shopify and local database."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QHeaderView,
    QGroupBox,
)

from db import (
    sync_from_shopify,
    push_all_pending_to_shopify,
    get_sync_status,
    get_connection,
)


class SyncWorker(QThread):
    """Background worker for sync operations."""
    progress = Signal(int, int, str)  # current, total, message
    finished = Signal(dict)  # result stats
    
    def __init__(self, operation: str):
        super().__init__()
        self.operation = operation
    
    def run(self):
        try:
            if self.operation == "pull":
                result = sync_from_shopify(
                    progress_callback=lambda c, t, m: self.progress.emit(c, t, m)
                )
            elif self.operation == "push":
                result = push_all_pending_to_shopify(
                    progress_callback=lambda c, t, m: self.progress.emit(c, t, m)
                )
            else:
                result = {"error": f"Unknown operation: {self.operation}"}
            
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit({"error": str(e)})


class ShopifySyncScreen(QWidget):
    """Screen for managing Shopify <-> Database synchronization."""
    
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: SyncWorker | None = None
        self._build_ui()
        self._refresh_status()
    
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # Header
        header = QLabel("🔄 Shopify Sync")
        header.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(header)
        
        desc = QLabel(
            "Sync products between Shopify (source of truth) and local database.\n"
            "Pull: Download all products from Shopify into local DB.\n"
            "Push: Upload local changes back to Shopify."
        )
        desc.setStyleSheet("color: #666;")
        layout.addWidget(desc)
        
        # Status panel
        status_group = QGroupBox("Sync Status")
        status_layout = QVBoxLayout(status_group)
        
        self._status_grid = QFrame()
        grid_layout = QHBoxLayout(self._status_grid)
        grid_layout.setSpacing(24)
        
        # Status indicators
        self._total_label = self._create_stat_box("Total Products", "0")
        self._synced_label = self._create_stat_box("Synced from Shopify", "0")
        self._pending_label = self._create_stat_box("Pending Updates", "0", highlight=True)
        self._last_sync_label = self._create_stat_box("Last Sync", "Never")
        
        grid_layout.addWidget(self._total_label)
        grid_layout.addWidget(self._synced_label)
        grid_layout.addWidget(self._pending_label)
        grid_layout.addWidget(self._last_sync_label)
        grid_layout.addStretch()
        
        status_layout.addWidget(self._status_grid)
        
        # Refresh status button
        refresh_btn = QPushButton("↻ Refresh Status")
        refresh_btn.clicked.connect(self._refresh_status)
        refresh_btn.setMaximumWidth(150)
        status_layout.addWidget(refresh_btn)
        
        layout.addWidget(status_group)
        
        # Sync actions
        actions_group = QGroupBox("Sync Actions")
        actions_layout = QVBoxLayout(actions_group)
        
        # Pull from Shopify
        pull_frame = QFrame()
        pull_layout = QHBoxLayout(pull_frame)
        pull_layout.setContentsMargins(0, 0, 0, 0)
        
        self._pull_btn = QPushButton("⬇️ Pull from Shopify")
        self._pull_btn.setMinimumHeight(40)
        self._pull_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
        self._pull_btn.clicked.connect(self._on_pull_from_shopify)
        
        pull_desc = QLabel("Download all products from Shopify into local database")
        pull_desc.setStyleSheet("color: #666;")
        
        pull_layout.addWidget(self._pull_btn)
        pull_layout.addWidget(pull_desc, 1)
        actions_layout.addWidget(pull_frame)
        
        # Push to Shopify
        push_frame = QFrame()
        push_layout = QHBoxLayout(push_frame)
        push_layout.setContentsMargins(0, 0, 0, 0)
        
        self._push_btn = QPushButton("⬆️ Push to Shopify")
        self._push_btn.setMinimumHeight(40)
        self._push_btn.setStyleSheet("font-size: 14px; font-weight: bold;")
        self._push_btn.clicked.connect(self._on_push_to_shopify)
        
        push_desc = QLabel("Upload local changes to Shopify (only pending items)")
        push_desc.setStyleSheet("color: #666;")
        
        push_layout.addWidget(self._push_btn)
        push_layout.addWidget(push_desc, 1)
        actions_layout.addWidget(push_frame)
        
        layout.addWidget(actions_group)
        
        # Progress bar
        self._progress_frame = QFrame()
        progress_layout = QVBoxLayout(self._progress_frame)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        
        self._progress_label = QLabel("Ready")
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        
        progress_layout.addWidget(self._progress_label)
        progress_layout.addWidget(self._progress_bar)
        
        self._progress_frame.setVisible(False)
        layout.addWidget(self._progress_frame)
        
        # Pending updates table
        pending_group = QGroupBox("Products Pending Shopify Update")
        pending_layout = QVBoxLayout(pending_group)
        
        self._pending_table = QTableWidget(0, 5)
        self._pending_table.setHorizontalHeaderLabels([
            "SKU", "Title", "Price", "Updated At", "Shopify ID"
        ])
        self._pending_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pending_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._pending_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        
        pending_layout.addWidget(self._pending_table)
        layout.addWidget(pending_group)
        
        layout.addStretch()
    
    def _create_stat_box(self, label: str, value: str, highlight: bool = False) -> QFrame:
        """Create a stat display box."""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setMinimumWidth(140)
        
        if highlight:
            frame.setStyleSheet("QFrame { background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; }")
        else:
            frame.setStyleSheet("QFrame { background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; }")
        
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setObjectName("value")
        
        text_label = QLabel(label)
        text_label.setStyleSheet("font-size: 11px; color: #666;")
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(value_label)
        layout.addWidget(text_label)
        
        return frame
    
    def _update_stat_box(self, frame: QFrame, value: str) -> None:
        """Update the value in a stat box."""
        value_label = frame.findChild(QLabel, "value")
        if value_label:
            value_label.setText(value)
    
    def _refresh_status(self) -> None:
        """Refresh sync status from database."""
        try:
            status_raw = get_sync_status()
            # Convert to dict to release any cursor
            status = dict(status_raw) if status_raw else {}
            
            self._update_stat_box(self._total_label, str(status.get("total_products", 0)))
            self._update_stat_box(self._synced_label, str(status.get("synced_products", 0)))
            self._update_stat_box(self._pending_label, str(status.get("needs_update", 0)))
            
            last_sync = status.get("last_sync")
            if last_sync:
                try:
                    dt = datetime.fromisoformat(last_sync)
                    last_sync_str = dt.strftime("%m/%d %H:%M")
                except:
                    last_sync_str = str(last_sync)[:16]
            else:
                last_sync_str = "Never"
            self._update_stat_box(self._last_sync_label, last_sync_str)
            
            # Update pending table
            self._load_pending_products()
            
        except Exception as e:
            # Log error but don't show message box on startup
            log.warning("Shopify sync status load failed: %s", e)
            self._update_stat_box(self._total_label, "?")
            self._update_stat_box(self._synced_label, "?")
            self._update_stat_box(self._pending_label, "?")
            self._update_stat_box(self._last_sync_label, "N/A")
    
    def _load_pending_products(self) -> None:
        """Load products that need Shopify update."""
        try:
            conn = get_connection()
            rows = conn.execute(
                """SELECT sku, title, selling_price, updated_at, shopify_id 
                   FROM products 
                   WHERE needs_shopify_update = TRUE OR needs_shopify_update = 1
                   ORDER BY updated_at DESC
                   LIMIT 100"""
            ).fetchall()
            
            self._pending_table.setRowCount(len(rows))
            
            for i, row in enumerate(rows):
                if hasattr(row, "keys"):
                    sku, title, price, updated, shopify_id = row["sku"], row["title"], row["selling_price"], row["updated_at"], row["shopify_id"]
                else:
                    sku, title, price, updated, shopify_id = row[0], row[1], row[2], row[3], row[4]
                
                self._pending_table.setItem(i, 0, QTableWidgetItem(str(sku or "")))
                self._pending_table.setItem(i, 1, QTableWidgetItem(str(title or "")[:50]))
                self._pending_table.setItem(i, 2, QTableWidgetItem(f"${price:.2f}" if price else "$0.00"))
                self._pending_table.setItem(i, 3, QTableWidgetItem(str(updated or "")[:16]))
                self._pending_table.setItem(i, 4, QTableWidgetItem(str(shopify_id or "—")))
                
        except Exception as e:
            log.warning("Shopify pending products load failed: %s", e)
    
    def _on_pull_from_shopify(self) -> None:
        """Start pull from Shopify."""
        reply = QMessageBox.question(
            self,
            "Pull from Shopify",
            "This will download all products from Shopify into the local database.\n\n"
            "Existing products will be updated with Shopify data.\n"
            "New products will be created.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._start_sync("pull")
    
    def _on_push_to_shopify(self) -> None:
        """Start push to Shopify."""
        try:
            status_raw = get_sync_status()
            status = dict(status_raw) if status_raw else {}
            pending = status.get("needs_update", 0)
        except Exception:
            pending = 0
        
        if pending == 0:
            QMessageBox.information(self, "Nothing to Push", "No products have pending changes.")
            return
        
        reply = QMessageBox.question(
            self,
            "Push to Shopify",
            f"This will upload {pending} products with local changes to Shopify.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        self._start_sync("push")
    
    def _start_sync(self, operation: str) -> None:
        """Start a sync operation in background."""
        self._pull_btn.setEnabled(False)
        self._push_btn.setEnabled(False)
        self._progress_frame.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"Starting {operation}...")
        
        self._worker = SyncWorker(operation)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()
    
    def _on_progress(self, current: int, total: int, message: str) -> None:
        """Handle progress update from worker."""
        if total > 0:
            percent = int((current / total) * 100)
            self._progress_bar.setValue(percent)
        self._progress_label.setText(message)
    
    def _on_finished(self, result: dict) -> None:
        """Handle sync completion."""
        self._pull_btn.setEnabled(True)
        self._push_btn.setEnabled(True)
        self._progress_frame.setVisible(False)
        
        if result.get("error"):
            QMessageBox.critical(self, "Sync Error", f"Sync failed: {result['error']}")
        else:
            synced = result.get("synced", result.get("pushed", 0))
            created = result.get("created", 0)
            updated = result.get("updated", 0)
            errors = result.get("errors", 0)
            
            msg = f"Sync complete!\n\n"
            if "created" in result:
                msg += f"• Created: {created}\n• Updated: {updated}\n"
            msg += f"• Total synced: {synced}\n• Errors: {errors}"
            
            QMessageBox.information(self, "Sync Complete", msg)
        
        self._refresh_status()
