"""QBO Duplicate Check Dialog.

Shows comparison results between local products and QBO inventory.
Allows user to mark items as skip/update before export.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QPushButton,
    QProgressBar,
    QHeaderView,
    QMessageBox,
    QCheckBox,
    QWidget,
    QFrame,
)

# Theme colors (match upload_screen.py)
THEME_COLORS = {
    "bg": "#1E1E1E",
    "panel": "#252526",
    "header_bg": "#2D2D30",
    "border": "#3E3E42",
    "text": "#CCCCCC",
    "text_muted": "#808080",
    "primary": "#5A8A5A",
    "success": "#4A7A4A",
    "warning": "#8B7355",
    "danger": "#8B5555",
}


class QBODuplicateDialog(QDialog):
    """Dialog showing QBO duplicate check results."""
    
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("QuickBooks Duplicate Check")
        self.setMinimumSize(900, 600)
        self.resize(1000, 700)
        
        self._report: dict | None = None
        self._skip_skus: set[str] = set()  # SKUs marked to skip
        
        self._setup_ui()
        self._apply_theme()
    
    def _setup_ui(self):
        """Build dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header with summary
        self._header = QLabel("Checking QuickBooks inventory...")
        self._header.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(self._header)
        
        # Summary bar
        self._summary_frame = QFrame()
        self._summary_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {THEME_COLORS['header_bg']};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        summary_layout = QHBoxLayout(self._summary_frame)
        summary_layout.setContentsMargins(12, 8, 12, 8)
        
        self._new_label = QLabel("🆕 New: 0")
        self._new_label.setStyleSheet(f"color: {THEME_COLORS['success']}; font-weight: bold;")
        summary_layout.addWidget(self._new_label)
        
        self._dup_label = QLabel("⚠️ Duplicates: 0")
        self._dup_label.setStyleSheet(f"color: {THEME_COLORS['warning']}; font-weight: bold;")
        summary_layout.addWidget(self._dup_label)
        
        self._var_label = QLabel("🔀 Variants: 0")
        self._var_label.setStyleSheet(f"color: #6A9FB5; font-weight: bold;")
        summary_layout.addWidget(self._var_label)
        
        self._qbo_only_label = QLabel("📦 QBO Only: 0")
        self._qbo_only_label.setStyleSheet(f"color: {THEME_COLORS['text_muted']}; font-weight: bold;")
        summary_layout.addWidget(self._qbo_only_label)
        
        summary_layout.addStretch()
        
        self._mock_label = QLabel("🧪 Mock Mode")
        self._mock_label.setStyleSheet(f"color: {THEME_COLORS['warning']};")
        self._mock_label.setVisible(False)
        summary_layout.addWidget(self._mock_label)
        
        layout.addWidget(self._summary_frame)
        
        # Progress bar (shown during check)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        
        # Tab widget for categories
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)  # Stretch
        
        # New items tab
        self._new_table = self._create_table(["", "SKU", "Title", "Cost"])
        self._tabs.addTab(self._new_table, "🆕 New (0)")
        
        # Duplicates tab
        self._dup_table = self._create_table(["", "Local SKU", "Local Title", "QBO SKU", "QBO Name", "QBO Qty"])
        self._tabs.addTab(self._dup_table, "⚠️ Duplicates (0)")
        
        # Variants tab
        self._var_table = self._create_table(["", "Local SKU", "Local Title", "QBO SKU", "Matched By", "Confidence"])
        self._tabs.addTab(self._var_table, "🔀 Variants (0)")
        
        # QBO Only tab
        self._qbo_table = self._create_table(["QBO SKU", "QBO Name", "Qty On Hand"])
        self._tabs.addTab(self._qbo_table, "📦 QBO Only (0)")
        
        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        self._mark_skip_btn = QPushButton("Mark Selected: Skip Export")
        self._mark_skip_btn.clicked.connect(self._on_mark_skip)
        btn_layout.addWidget(self._mark_skip_btn)
        
        self._mark_update_btn = QPushButton("Mark Selected: Update QBO")
        self._mark_update_btn.clicked.connect(self._on_mark_update)
        btn_layout.addWidget(self._mark_update_btn)
        
        btn_layout.addStretch()
        
        self._refresh_btn = QPushButton("🔄 Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh)
        btn_layout.addWidget(self._refresh_btn)
        
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._close_btn)
        
        layout.addLayout(btn_layout)
    
    def _create_table(self, headers: list[str]) -> QTableWidget:
        """Create a styled table widget."""
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        if headers and headers[0] == "":
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            table.setColumnWidth(0, 40)
        
        return table
    
    def _apply_theme(self):
        """Apply dark theme to dialog."""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {THEME_COLORS['bg']};
                color: {THEME_COLORS['text']};
            }}
            QTabWidget::pane {{
                border: 1px solid {THEME_COLORS['border']};
                background-color: {THEME_COLORS['panel']};
            }}
            QTabBar::tab {{
                background-color: {THEME_COLORS['header_bg']};
                color: {THEME_COLORS['text']};
                padding: 8px 16px;
                border: 1px solid {THEME_COLORS['border']};
                border-bottom: none;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {THEME_COLORS['panel']};
                border-bottom: 1px solid {THEME_COLORS['panel']};
            }}
            QTableWidget {{
                background-color: {THEME_COLORS['panel']};
                color: {THEME_COLORS['text']};
                gridline-color: {THEME_COLORS['border']};
                border: none;
            }}
            QTableWidget::item {{
                padding: 4px;
            }}
            QTableWidget::item:selected {{
                background-color: {THEME_COLORS['primary']};
            }}
            QHeaderView::section {{
                background-color: {THEME_COLORS['header_bg']};
                color: {THEME_COLORS['text']};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {THEME_COLORS['border']};
            }}
            QPushButton {{
                background-color: {THEME_COLORS['header_bg']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                border-color: {THEME_COLORS['primary']};
            }}
            QProgressBar {{
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {THEME_COLORS['primary']};
            }}
        """)
    
    def set_progress(self, value: int, message: str = ""):
        """Update progress bar."""
        self._progress.setVisible(value < 100)
        self._progress.setValue(value)
        if message:
            self._progress.setFormat(f"{value}% - {message}")
            self._header.setText(message)
    
    def set_report(self, report: dict):
        """Populate dialog with report data."""
        self._report = report
        self._progress.setVisible(False)
        
        if report.get('error'):
            self._header.setText(f"❌ Error: {report['error']}")
            return
        
        summary = report.get('summary', {})
        new_count = summary.get('new', 0)
        dup_count = summary.get('duplicates', 0)
        var_count = summary.get('variants', 0)
        qbo_count = summary.get('qbo_only', 0)
        
        # Update header
        total = new_count + dup_count + var_count
        self._header.setText(f"Compared {total} local products against QuickBooks inventory")
        
        # Update summary labels
        self._new_label.setText(f"🆕 New: {new_count}")
        self._dup_label.setText(f"⚠️ Duplicates: {dup_count}")
        self._var_label.setText(f"🔀 Variants: {var_count}")
        self._qbo_only_label.setText(f"📦 QBO Only: {qbo_count}")
        
        # Show mock mode indicator
        self._mock_label.setVisible(report.get('is_mock', False))
        
        # Update tab titles
        self._tabs.setTabText(0, f"🆕 New ({new_count})")
        self._tabs.setTabText(1, f"⚠️ Duplicates ({dup_count})")
        self._tabs.setTabText(2, f"🔀 Variants ({var_count})")
        self._tabs.setTabText(3, f"📦 QBO Only ({qbo_count})")
        
        # Populate tables
        self._populate_new_table(report.get('new', []))
        self._populate_dup_table(report.get('duplicates', []))
        self._populate_var_table(report.get('variants', []))
        self._populate_qbo_table(report.get('qbo_only', []))
        
        # Switch to most relevant tab
        if dup_count > 0:
            self._tabs.setCurrentIndex(1)  # Duplicates
        elif var_count > 0:
            self._tabs.setCurrentIndex(2)  # Variants
        elif new_count > 0:
            self._tabs.setCurrentIndex(0)  # New
    
    def _populate_new_table(self, items: list[dict]):
        """Populate New items table."""
        self._new_table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(True)  # Default: include new items
            self._new_table.setCellWidget(row, 0, cb)
            
            # SKU
            sku_item = QTableWidgetItem(item.get('sku', ''))
            sku_item.setData(Qt.UserRole, item.get('sku', ''))
            self._new_table.setItem(row, 1, sku_item)
            
            # Title
            self._new_table.setItem(row, 2, QTableWidgetItem(item.get('title', '')[:60]))
            
            # Cost
            cost = item.get('cost', 0)
            cost_item = QTableWidgetItem(f"${cost:.2f}" if cost else "-")
            cost_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._new_table.setItem(row, 3, cost_item)
    
    def _populate_dup_table(self, items: list[dict]):
        """Populate Duplicates table."""
        self._dup_table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(False)  # Default: skip duplicates
            self._dup_table.setCellWidget(row, 0, cb)
            
            # Local SKU
            sku_item = QTableWidgetItem(item.get('sku', ''))
            sku_item.setData(Qt.UserRole, item.get('sku', ''))
            sku_item.setForeground(QColor(THEME_COLORS['warning']))
            self._dup_table.setItem(row, 1, sku_item)
            
            # Local Title
            self._dup_table.setItem(row, 2, QTableWidgetItem(item.get('title', '')[:40]))
            
            # QBO SKU
            self._dup_table.setItem(row, 3, QTableWidgetItem(item.get('qbo_sku', '')))
            
            # QBO Name
            self._dup_table.setItem(row, 4, QTableWidgetItem(item.get('qbo_name', '')[:40]))
            
            # QBO Qty
            qty = item.get('qbo_qty', 0)
            qty_item = QTableWidgetItem(str(int(qty)))
            qty_item.setTextAlignment(Qt.AlignCenter)
            self._dup_table.setItem(row, 5, qty_item)
    
    def _populate_var_table(self, items: list[dict]):
        """Populate Variants table."""
        self._var_table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(False)  # Default: skip variants (need review)
            self._var_table.setCellWidget(row, 0, cb)
            
            # Local SKU
            sku_item = QTableWidgetItem(item.get('sku', ''))
            sku_item.setData(Qt.UserRole, item.get('sku', ''))
            self._var_table.setItem(row, 1, sku_item)
            
            # Local Title
            self._var_table.setItem(row, 2, QTableWidgetItem(item.get('title', '')[:40]))
            
            # QBO SKU
            self._var_table.setItem(row, 3, QTableWidgetItem(item.get('qbo_sku', '')))
            
            # Matched By
            self._var_table.setItem(row, 4, QTableWidgetItem(item.get('matched_by', '')))
            
            # Confidence
            conf = item.get('confidence', 0)
            conf_item = QTableWidgetItem(f"{conf*100:.0f}%")
            conf_item.setTextAlignment(Qt.AlignCenter)
            self._var_table.setItem(row, 5, conf_item)
    
    def _populate_qbo_table(self, items: list[dict]):
        """Populate QBO Only table."""
        self._qbo_table.setRowCount(len(items))
        for row, item in enumerate(items):
            # QBO SKU
            self._qbo_table.setItem(row, 0, QTableWidgetItem(item.get('qbo_sku', '')))
            
            # QBO Name
            self._qbo_table.setItem(row, 1, QTableWidgetItem(item.get('qbo_name', '')))
            
            # Qty
            qty = item.get('qbo_qty', 0)
            qty_item = QTableWidgetItem(str(int(qty)))
            qty_item.setTextAlignment(Qt.AlignCenter)
            self._qbo_table.setItem(row, 2, qty_item)
    
    def _on_mark_skip(self):
        """Mark selected items to skip export."""
        current_table = self._get_current_table()
        if not current_table:
            return
        
        for row in range(current_table.rowCount()):
            if current_table.item(row, 1) and current_table.item(row, 1).isSelected():
                cb = current_table.cellWidget(row, 0)
                if isinstance(cb, QCheckBox):
                    cb.setChecked(False)
                    sku = current_table.item(row, 1).data(Qt.UserRole)
                    if sku:
                        self._skip_skus.add(sku)
    
    def _on_mark_update(self):
        """Mark selected items to update in QBO."""
        current_table = self._get_current_table()
        if not current_table:
            return
        
        for row in range(current_table.rowCount()):
            if current_table.item(row, 1) and current_table.item(row, 1).isSelected():
                cb = current_table.cellWidget(row, 0)
                if isinstance(cb, QCheckBox):
                    cb.setChecked(True)
                    sku = current_table.item(row, 1).data(Qt.UserRole)
                    if sku:
                        self._skip_skus.discard(sku)
    
    def _get_current_table(self) -> QTableWidget | None:
        """Get the currently active table."""
        idx = self._tabs.currentIndex()
        if idx == 0:
            return self._new_table
        elif idx == 1:
            return self._dup_table
        elif idx == 2:
            return self._var_table
        return None
    
    def _on_refresh(self):
        """Request a refresh of the QBO check."""
        # This will be handled by parent
        self.done(2)  # Custom return code for refresh
    
    def get_skip_skus(self) -> set[str]:
        """Get SKUs marked to skip."""
        return self._skip_skus.copy()
    
    def get_export_skus(self) -> list[str]:
        """Get SKUs to include in export (checked in New tab)."""
        skus = []
        for row in range(self._new_table.rowCount()):
            cb = self._new_table.cellWidget(row, 0)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                sku_item = self._new_table.item(row, 1)
                if sku_item:
                    sku = sku_item.data(Qt.UserRole)
                    if sku:
                        skus.append(sku)
        return skus
