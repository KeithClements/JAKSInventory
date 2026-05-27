"""Cross-Reference Viewer Dialog for JAKS Scraper.

Shows all part numbers and vendor options for a product.
Allows viewing and managing cross-references and vendor pricing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QTabWidget,
    QWidget,
    QLineEdit,
    QComboBox,
    QMessageBox,
)
from PySide6.QtGui import QFont, QColor

if TYPE_CHECKING:
    from models import Product


class CrossRefViewerDialog(QDialog):
    """Dialog for viewing and managing product cross-references.
    
    Shows:
    - All part numbers (OEM, vendor, cross-refs)
    - Vendor pricing comparison
    - Product group members (if grouped)
    """
    
    # Emitted when cross-refs are modified
    cross_refs_changed = Signal(int)  # product_id
    
    def __init__(
        self,
        parent: QWidget | None,
        product: "Product",
        db_connection=None,
    ):
        super().__init__(parent)
        self.product = product
        self.conn = db_connection
        
        self._setup_ui()
        self._load_data()
        
    def _setup_ui(self):
        """Build the dialog UI."""
        self.setWindowTitle(f"🔗 Cross-References: {self.product.sku}")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        # Product header
        header = self._create_header()
        layout.addWidget(header)
        
        # Tabs
        tabs = QTabWidget()
        
        # Part Numbers tab
        pn_tab = self._create_part_numbers_tab()
        tabs.addTab(pn_tab, "📝 Part Numbers")
        
        # Vendor Pricing tab
        pricing_tab = self._create_vendor_pricing_tab()
        tabs.addTab(pricing_tab, "💰 Vendor Pricing")
        
        # Product Group tab (if grouped)
        if self._has_group():
            group_tab = self._create_group_tab()
            tabs.addTab(group_tab, "🔗 Product Group")
            
        layout.addWidget(tabs, 1)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
        
    def _create_header(self) -> QWidget:
        """Create the product header."""
        widget = QFrame()
        widget.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        
        layout = QVBoxLayout(widget)
        
        # SKU and title
        sku_label = QLabel(f"<h2>{self.product.sku}</h2>")
        layout.addWidget(sku_label)
        
        title_label = QLabel(self.product.title or "No title")
        title_label.setWordWrap(True)
        title_label.setStyleSheet("color: #bdc3c7;")
        layout.addWidget(title_label)
        
        # Quick stats
        stats_layout = QHBoxLayout()
        
        # Count part numbers
        pn_count = self._count_part_numbers()
        pn_badge = QLabel(f"📝 {pn_count} part numbers")
        pn_badge.setStyleSheet("background-color: #3498db; color: white; padding: 4px 8px; border-radius: 4px;")
        stats_layout.addWidget(pn_badge)
        
        # Count vendors
        vendor_count = self._count_vendors()
        vendor_badge = QLabel(f"🏭 {vendor_count} vendors")
        vendor_badge.setStyleSheet("background-color: #27ae60; color: white; padding: 4px 8px; border-radius: 4px;")
        stats_layout.addWidget(vendor_badge)
        
        stats_layout.addStretch()
        layout.addLayout(stats_layout)
        
        return widget
        
    def _create_part_numbers_tab(self) -> QWidget:
        """Create the part numbers tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Part numbers table
        self.pn_table = QTableWidget()
        self.pn_table.setColumnCount(4)
        self.pn_table.setHorizontalHeaderLabels(["Part Number", "Type", "Source", "Primary"])
        self.pn_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.pn_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.pn_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.pn_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.pn_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.pn_table, 1)
        
        # Add part number section
        add_group = QGroupBox("➕ Add Part Number")
        add_layout = QHBoxLayout(add_group)
        
        self.new_pn_edit = QLineEdit()
        self.new_pn_edit.setPlaceholderText("Enter part number...")
        add_layout.addWidget(self.new_pn_edit, 1)
        
        self.new_pn_type = QComboBox()
        self.new_pn_type.addItems(["cross_ref", "oem", "vendor", "alternate"])
        add_layout.addWidget(self.new_pn_type)
        
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_part_number)
        add_layout.addWidget(add_btn)
        
        layout.addWidget(add_group)
        
        return widget
        
    def _create_vendor_pricing_tab(self) -> QWidget:
        """Create the vendor pricing comparison tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Description
        desc = QLabel("""
            <p>Compare pricing across different vendors for this product.</p>
            <p>★ indicates the preferred/best price vendor.</p>
        """)
        desc.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(desc)
        
        # Vendor pricing table
        self.pricing_table = QTableWidget()
        self.pricing_table.setColumnCount(5)
        self.pricing_table.setHorizontalHeaderLabels(["Vendor", "Part Number", "Cost", "Stock", "Updated"])
        self.pricing_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pricing_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.pricing_table, 1)
        
        # Actions
        action_layout = QHBoxLayout()
        
        refresh_btn = QPushButton("🔄 Refresh All Prices")
        refresh_btn.setEnabled(False)  # TODO: Implement
        action_layout.addWidget(refresh_btn)
        
        action_layout.addStretch()
        
        set_preferred_btn = QPushButton("⭐ Set as Preferred")
        set_preferred_btn.setEnabled(False)  # TODO: Implement
        action_layout.addWidget(set_preferred_btn)
        
        layout.addLayout(action_layout)
        
        return widget
        
    def _create_group_tab(self) -> QWidget:
        """Create the product group tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        desc = QLabel("""
            <p>This product is part of a group - multiple products that represent 
            the same physical item from different vendors.</p>
        """)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(desc)
        
        # Group members table
        self.group_table = QTableWidget()
        self.group_table.setColumnCount(4)
        self.group_table.setHorizontalHeaderLabels(["SKU", "Title", "Source", "Master"])
        self.group_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.group_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.group_table, 1)
        
        return widget
        
    def _load_data(self):
        """Load data into tables."""
        self._load_part_numbers()
        self._load_vendor_pricing()
        
        if self._has_group():
            self._load_group_members()
            
    def _load_part_numbers(self):
        """Load part numbers into table."""
        part_numbers = []
        
        # Get family ID for highlighting
        family_id = getattr(self.product, 'family_id', '') or ''
        # Extract the core number from FAM-XXX-NNNN or FAM-CUMNNNN format
        family_number = ''
        if family_id.startswith('FAM-'):
            # Remove FAM- prefix and any manufacturer code (e.g., FAM-CUM3901430 -> 3901430)
            family_number = family_id[4:]
            # If it starts with letters (manufacturer code), strip them
            import re
            match = re.search(r'(\d+)$', family_number)
            if match:
                family_number = match.group(1)
        
        # From product fields
        if self.product.sku:
            # Extract base from JAKS-XXXXX
            base = self.product.sku
            if base.startswith("JAKS-"):
                base = base[5:]
            part_numbers.append((base, "sku", "Product SKU", True))
            
        if self.product.oem_part_number:
            part_numbers.append((self.product.oem_part_number, "oem", "OEM", False))
            
        if self.product.pai_sku:
            part_numbers.append((self.product.pai_sku, "vendor", "PAI", False))
            
        if self.product.supplier_part_number:
            part_numbers.append((self.product.supplier_part_number, "vendor", 
                               self.product.supplier or "Vendor", False))
                               
        # From xrefs - show ALL cross-references, not just first 10
        if self.product.xrefs:
            for xref in self.product.xrefs:
                part_numbers.append((xref, "cross_ref", "Cross-Ref", False))
                
        # From database part_numbers table
        if self.conn:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT number, type, manufacturer, is_primary
                FROM part_numbers
                WHERE product_id = ?
                ORDER BY is_primary DESC, type
            """, (self.product.db_id,))
            
            for row in cursor.fetchall():
                # Avoid duplicates
                if row[0] not in [pn[0] for pn in part_numbers]:
                    part_numbers.append((row[0], row[1] or "other", row[2] or "", bool(row[3])))
                    
        # Populate table
        self.pn_table.setRowCount(len(part_numbers))
        
        for i, (number, pn_type, source, is_primary) in enumerate(part_numbers):
            # Check if this part number is the family ID number
            is_family_id = family_number and number == family_number
            
            num_item = QTableWidgetItem(number)
            type_item = QTableWidgetItem(pn_type)
            source_item = QTableWidgetItem(source)
            
            # Highlight family ID xref with green background
            if is_family_id:
                highlight_color = QColor("#2ecc71")  # Green
                num_item.setBackground(highlight_color)
                type_item.setBackground(highlight_color)
                source_item.setBackground(highlight_color)
                source_item.setText("Family ID ★")
            
            self.pn_table.setItem(i, 0, num_item)
            self.pn_table.setItem(i, 1, type_item)
            self.pn_table.setItem(i, 2, source_item)
            
            primary_item = QTableWidgetItem("★" if is_primary else "")
            primary_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_primary:
                primary_item.setForeground(QColor("#f1c40f"))
            if is_family_id:
                primary_item.setBackground(QColor("#2ecc71"))
            self.pn_table.setItem(i, 3, primary_item)
            
    def _load_vendor_pricing(self):
        """Load vendor pricing into table."""
        pricing_data = []
        
        # From product's vendor_pricing field (if exists)
        vendor_pricing = getattr(self.product, 'vendor_pricing', None)
        if vendor_pricing and isinstance(vendor_pricing, dict):
            for vendor, data in vendor_pricing.items():
                pricing_data.append({
                    'vendor': vendor,
                    'part_number': data.get('part_number', ''),
                    'cost': data.get('cost', 0),
                    'stock': data.get('stock', '—'),
                    'updated': data.get('updated', ''),
                })
                
        # Add PAI if we have it
        if self.product.pai_cost and self.product.pai_cost > 0:
            if not any(p['vendor'] == 'PAI' for p in pricing_data):
                pricing_data.append({
                    'vendor': 'PAI',
                    'part_number': self.product.pai_sku or '',
                    'cost': self.product.pai_cost,
                    'stock': '—',
                    'updated': '',
                })
                
        # Add main cost
        if self.product.cost and self.product.cost > 0:
            supplier = self.product.supplier or 'Primary'
            if not any(p['vendor'] == supplier for p in pricing_data):
                pricing_data.append({
                    'vendor': supplier,
                    'part_number': self.product.supplier_part_number or '',
                    'cost': self.product.cost,
                    'stock': '—',
                    'updated': '',
                })
                
        # Sort by cost
        pricing_data.sort(key=lambda x: x['cost'] if x['cost'] else float('inf'))
        
        # Populate table
        self.pricing_table.setRowCount(len(pricing_data))
        
        for i, data in enumerate(pricing_data):
            # Vendor (star for best price)
            vendor_text = data['vendor']
            if i == 0 and data['cost'] > 0:
                vendor_text = f"★ {vendor_text}"
            self.pricing_table.setItem(i, 0, QTableWidgetItem(vendor_text))
            
            self.pricing_table.setItem(i, 1, QTableWidgetItem(data['part_number']))
            
            cost_text = f"${data['cost']:.2f}" if data['cost'] else "—"
            self.pricing_table.setItem(i, 2, QTableWidgetItem(cost_text))
            
            self.pricing_table.setItem(i, 3, QTableWidgetItem(str(data['stock'])))
            self.pricing_table.setItem(i, 4, QTableWidgetItem(data['updated']))
            
        if not pricing_data:
            self.pricing_table.setRowCount(1)
            self.pricing_table.setItem(0, 0, QTableWidgetItem("No vendor pricing data"))
            self.pricing_table.setSpan(0, 0, 1, 5)
            
    def _load_group_members(self):
        """Load product group members."""
        if not self.conn:
            return
            
        # TODO: Query product_groups and product_group_members tables
        # For now, just show this product
        self.group_table.setRowCount(1)
        self.group_table.setItem(0, 0, QTableWidgetItem(self.product.sku))
        self.group_table.setItem(0, 1, QTableWidgetItem(self.product.title or ""))
        self.group_table.setItem(0, 2, QTableWidgetItem(self.product.supplier or "HHP"))
        
        master_item = QTableWidgetItem("★ Master")
        master_item.setForeground(QColor("#f1c40f"))
        self.group_table.setItem(0, 3, master_item)
        
    def _add_part_number(self):
        """Add a new part number."""
        pn = self.new_pn_edit.text().strip()
        if not pn:
            return
            
        pn_type = self.new_pn_type.currentText()
        
        if self.conn and self.product.db_id:
            from db.product_grouping import ProductGroupingService
            service = ProductGroupingService(self.conn)
            
            if service.link_part_number_to_product(
                self.product.db_id,
                pn,
                pn_type,
                source="manual",
            ):
                QMessageBox.information(self, "Success", f"Added part number: {pn}")
                self.new_pn_edit.clear()
                self._load_part_numbers()
                self.cross_refs_changed.emit(self.product.db_id)
            else:
                QMessageBox.warning(self, "Error", "Failed to add part number")
        else:
            QMessageBox.warning(self, "Error", "No database connection")
            
    def _count_part_numbers(self) -> int:
        """Count total part numbers."""
        count = 0
        if self.product.sku:
            count += 1
        if self.product.oem_part_number:
            count += 1
        if self.product.pai_sku:
            count += 1
        if self.product.supplier_part_number:
            count += 1
        if self.product.xrefs:
            count += len(self.product.xrefs)
        return count
        
    def _count_vendors(self) -> int:
        """Count vendors with pricing."""
        count = 0
        if self.product.pai_cost and self.product.pai_cost > 0:
            count += 1
        if self.product.cost and self.product.cost > 0:
            count += 1
        vendor_pricing = getattr(self.product, 'vendor_pricing', None)
        if vendor_pricing:
            count += len(vendor_pricing)
        return max(count, 1)
        
    def _has_group(self) -> bool:
        """Check if product is part of a group."""
        # TODO: Query database for group membership
        return False


class QuickCrossRefPopup(QFrame):
    """Quick popup showing cross-references for a product.
    
    Used for inline display in the main table.
    """
    
    def __init__(self, parent: QWidget | None, product: "Product"):
        super().__init__(parent)
        self.product = product
        
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border: 1px solid #3498db;
                border-radius: 8px;
                padding: 8px;
            }
        """)
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        
        # Header
        header = QLabel(f"<b>🔗 {self.product.sku}</b>")
        layout.addWidget(header)
        
        # Part numbers
        numbers = []
        
        if self.product.oem_part_number:
            numbers.append(f"OEM: {self.product.oem_part_number}")
        if self.product.pai_sku:
            numbers.append(f"PAI: {self.product.pai_sku}")
        if self.product.supplier_part_number and self.product.supplier:
            numbers.append(f"{self.product.supplier}: {self.product.supplier_part_number}")
        if self.product.xrefs:
            for xref in self.product.xrefs[:3]:
                numbers.append(f"XRef: {xref}")
            if len(self.product.xrefs) > 3:
                numbers.append(f"... +{len(self.product.xrefs) - 3} more")
                
        if numbers:
            for num in numbers:
                label = QLabel(num)
                label.setStyleSheet("color: #bdc3c7; font-size: 11px;")
                layout.addWidget(label)
        else:
            label = QLabel("No cross-references")
            label.setStyleSheet("color: #7f8c8d; font-style: italic;")
            layout.addWidget(label)


def format_xrefs_for_table(product: "Product", max_display: int = 2) -> str:
    """Format cross-references for display in table cell.
    
    Returns a short string like "5473069, ISX119-145 +3"
    """
    xrefs = []
    
    if product.oem_part_number:
        xrefs.append(product.oem_part_number)
    if product.pai_sku and product.pai_sku not in xrefs:
        xrefs.append(product.pai_sku)
    if product.xrefs:
        for x in product.xrefs:
            if x not in xrefs:
                xrefs.append(x)
                
    if not xrefs:
        return "—"
        
    displayed = xrefs[:max_display]
    remaining = len(xrefs) - max_display
    
    result = ", ".join(displayed)
    if remaining > 0:
        result += f" +{remaining}"
        
    return result
