"""QBO Settings and Status Widget for JAKS Scraper.

Provides:
- Connection status display
- OAuth connect button
- Write enable toggle
- Quick sync actions
"""

from __future__ import annotations

import os
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGroupBox,
    QFormLayout,
    QCheckBox,
    QWidget,
    QFrame,
    QMessageBox,
    QProgressBar,
)


__all__ = ["QBOStatusWidget", "QBOSettingsDialog", "show_qbo_settings"]


class StatusIndicator(QLabel):
    """Colored status indicator dot."""
    
    def __init__(self, status: str = "disconnected", parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.set_status(status)
    
    def set_status(self, status: str):
        """Set status: 'connected', 'mock', 'disconnected', 'error'."""
        colors = {
            "connected": "#2ecc71",     # Green
            "mock": "#f39c12",          # Orange
            "disconnected": "#95a5a6",  # Gray
            "error": "#e74c3c",         # Red
        }
        color = colors.get(status, colors["disconnected"])
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                border-radius: 8px;
                border: 2px solid {color};
            }}
        """)


class QBOStatusWidget(QWidget):
    """Compact QBO status display for toolbars/status bars."""
    
    settings_requested = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._refresh_status()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        
        # Status indicator
        self._indicator = StatusIndicator()
        layout.addWidget(self._indicator)
        
        # Label
        self._label = QLabel("QBO")
        self._label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._label)
        
        # Status text
        self._status_text = QLabel("...")
        self._status_text.setStyleSheet("color: #666;")
        layout.addWidget(self._status_text)
        
        # Settings button
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(24, 24)
        settings_btn.setToolTip("QBO Settings")
        settings_btn.clicked.connect(self.settings_requested.emit)
        layout.addWidget(settings_btn)
    
    def _refresh_status(self):
        """Refresh the status display."""
        try:
            from qbo.integration import get_qbo_service
            
            service = get_qbo_service()
            status = service.get_status()
            
            if not status.get("configured"):
                self._indicator.set_status("disconnected")
                self._status_text.setText("Not configured")
            elif status.get("is_mock"):
                self._indicator.set_status("mock")
                self._status_text.setText("Mock mode")
            elif status.get("has_tokens"):
                self._indicator.set_status("connected")
                env = status.get("environment", "sandbox")
                self._status_text.setText(f"Connected ({env})")
            else:
                self._indicator.set_status("disconnected")
                self._status_text.setText("Not connected")
                
        except Exception as e:
            self._indicator.set_status("error")
            self._status_text.setText(str(e)[:20])
    
    def refresh(self):
        """Public method to refresh status."""
        self._refresh_status()


class QBOSettingsDialog(QDialog):
    """Full QBO settings and status dialog."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QuickBooks Online Settings")
        self.setMinimumWidth(500)
        self._setup_ui()
        self._refresh_status()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Connection Status Group
        status_group = QGroupBox("Connection Status")
        status_layout = QFormLayout(status_group)
        
        # Status row
        status_row = QHBoxLayout()
        self._status_indicator = StatusIndicator()
        status_row.addWidget(self._status_indicator)
        self._status_label = QLabel("Checking...")
        self._status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        status_layout.addRow("Status:", status_row)
        
        # Environment
        self._env_label = QLabel("-")
        status_layout.addRow("Environment:", self._env_label)
        
        # Realm ID
        self._realm_label = QLabel("-")
        status_layout.addRow("Company ID:", self._realm_label)
        
        # Write status
        self._write_label = QLabel("-")
        status_layout.addRow("Write Mode:", self._write_label)
        
        layout.addWidget(status_group)
        
        # Configuration Group
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout(config_group)
        
        # Instructions
        instructions = QLabel(
            "To connect QuickBooks:\n"
            "1. Set QBO_CLIENT_ID and QBO_CLIENT_SECRET in .env\n"
            "2. Click 'Connect to QuickBooks' below\n"
            "3. Log in and authorize the app\n"
            "4. Set QBO_WRITE_ENABLED=1 to enable write operations"
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #666; padding: 10px; background: #f5f5f5; border-radius: 4px;")
        config_layout.addWidget(instructions)
        
        # Buttons row
        btn_layout = QHBoxLayout()
        
        self._connect_btn = QPushButton("🔗 Connect to QuickBooks")
        self._connect_btn.clicked.connect(self._on_connect)
        btn_layout.addWidget(self._connect_btn)
        
        self._refresh_btn = QPushButton("🔄 Refresh Status")
        self._refresh_btn.clicked.connect(self._refresh_status)
        btn_layout.addWidget(self._refresh_btn)
        
        config_layout.addLayout(btn_layout)
        
        layout.addWidget(config_group)
        
        # Actions Group
        actions_group = QGroupBox("Quick Actions")
        actions_layout = QVBoxLayout(actions_group)
        
        # Test connection
        test_btn = QPushButton("🧪 Test Connection")
        test_btn.clicked.connect(self._on_test_connection)
        actions_layout.addWidget(test_btn)
        
        # View items
        view_btn = QPushButton("📋 View QBO Items")
        view_btn.clicked.connect(self._on_view_items)
        actions_layout.addWidget(view_btn)
        
        # Inventory report
        report_btn = QPushButton("📊 Inventory Value Report")
        report_btn.clicked.connect(self._on_inventory_report)
        actions_layout.addWidget(report_btn)
        
        layout.addWidget(actions_group)
        
        # Close button
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)
    
    def _refresh_status(self):
        """Refresh all status displays."""
        try:
            from qbo.integration import get_qbo_service
            
            service = get_qbo_service()
            status = service.get_status()
            
            # Update indicator
            if not status.get("configured"):
                self._status_indicator.set_status("disconnected")
                self._status_label.setText("Not Configured")
            elif status.get("is_mock"):
                self._status_indicator.set_status("mock")
                self._status_label.setText("Mock Mode (no credentials)")
            elif status.get("has_tokens"):
                self._status_indicator.set_status("connected")
                self._status_label.setText("Connected")
            else:
                self._status_indicator.set_status("disconnected")
                self._status_label.setText("Not Connected")
            
            # Update labels
            self._env_label.setText(status.get("environment", "sandbox").title())
            self._realm_label.setText(status.get("realm_id", "Not set"))
            
            if status.get("write_enabled"):
                self._write_label.setText("✅ Enabled (writes to QBO)")
                self._write_label.setStyleSheet("color: #27ae60; font-weight: bold;")
            else:
                self._write_label.setText("🔒 Disabled (read-only)")
                self._write_label.setStyleSheet("color: #e67e22;")
                
        except Exception as e:
            self._status_indicator.set_status("error")
            self._status_label.setText(f"Error: {e}")
    
    def _on_connect(self):
        """Start OAuth flow."""
        # Check if credentials configured
        if not os.environ.get("QBO_CLIENT_ID"):
            QMessageBox.warning(
                self,
                "Missing Credentials",
                "QBO_CLIENT_ID not set in environment.\n\n"
                "Please add to your .env file:\n"
                "QBO_CLIENT_ID=your_client_id\n"
                "QBO_CLIENT_SECRET=your_client_secret\n"
                "QBO_ENVIRONMENT=sandbox"
            )
            return
        
        # Open OAuth URL
        try:
            # Start local server first (webapp should be running)
            webbrowser.open("http://localhost:5000/qbo/auth")
            
            QMessageBox.information(
                self,
                "OAuth Flow Started",
                "A browser window has opened for QuickBooks login.\n\n"
                "After authorizing, return here and click 'Refresh Status'."
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open browser: {e}")
    
    def _on_test_connection(self):
        """Test the QBO connection."""
        try:
            from qbo.integration import get_qbo_service
            
            service = get_qbo_service()
            
            if service.connect():
                if service.is_mock_mode:
                    QMessageBox.information(
                        self,
                        "Mock Mode",
                        "Connection successful (Mock mode - no real QBO access)"
                    )
                else:
                    # Try to fetch items
                    items = service.client.fetch_all_items()
                    QMessageBox.information(
                        self,
                        "Connection Successful",
                        f"Connected to QuickBooks!\n\nFound {len(items)} items."
                    )
            else:
                QMessageBox.warning(self, "Connection Failed", "Could not connect to QuickBooks")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Connection test failed: {e}")
    
    def _on_view_items(self):
        """Show QBO items in a simple list."""
        try:
            from qbo.integration import get_qbo_service
            
            service = get_qbo_service()
            
            if not service.connect():
                QMessageBox.warning(self, "Error", "Could not connect to QBO")
                return
            
            items = service.client.fetch_all_items()
            
            # Build simple text report
            lines = [f"QBO Inventory Items ({len(items)} total)\n" + "=" * 50 + "\n"]
            
            for item in sorted(items, key=lambda x: x.get("sku", "")):
                lines.append(
                    f"{item.get('sku', 'N/A'):20} | "
                    f"{item.get('name', '')[:40]:40} | "
                    f"Qty: {item.get('qty_on_hand', 0):>5} | "
                    f"${item.get('price', 0):>8.2f}"
                )
            
            # Show in message box (or could open a dialog)
            from PySide6.QtWidgets import QTextEdit
            
            dlg = QDialog(self)
            dlg.setWindowTitle("QBO Items")
            dlg.resize(800, 500)
            
            layout = QVBoxLayout(dlg)
            text = QTextEdit()
            text.setPlainText("\n".join(lines))
            text.setReadOnly(True)
            text.setStyleSheet("font-family: monospace;")
            layout.addWidget(text)
            
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dlg.close)
            layout.addWidget(close_btn)
            
            dlg.exec()
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch items: {e}")
    
    def _on_inventory_report(self):
        """Show inventory value report."""
        try:
            from qbo.integration import get_qbo_service
            
            service = get_qbo_service()
            
            if not service.connect():
                QMessageBox.warning(self, "Error", "Could not connect to QBO")
                return
            
            report = service.get_inventory_value_report()
            
            if "error" in report:
                QMessageBox.warning(self, "Error", report["error"])
                return
            
            msg = f"""
Inventory Value Report
======================

Total Items: {report['total_items']}
Items with Stock: {report['items_with_stock']}
Items Zero Stock: {report['items_zero_stock']}

Total Value (at Cost): ${report['total_value_at_cost']:,.2f}
Total Value (at Price): ${report['total_value_at_price']:,.2f}
Potential Margin: ${report['total_value_at_price'] - report['total_value_at_cost']:,.2f}
"""
            
            QMessageBox.information(self, "Inventory Report", msg)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate report: {e}")


def show_qbo_settings(parent=None) -> QBOSettingsDialog:
    """Show the QBO settings dialog."""
    dlg = QBOSettingsDialog(parent)
    dlg.exec()
    return dlg
