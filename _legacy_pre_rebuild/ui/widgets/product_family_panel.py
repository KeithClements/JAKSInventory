"""Product Family Panel widget for JAKS Scraper.

Displays product family details including all variants/alternates.
Accessible via F4 key or double-click on Family column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QGroupBox,
    QFormLayout,
    QTextEdit,
    QSplitter,
    QWidget,
    QAbstractItemView,
)

if TYPE_CHECKING:
    from db.product_family_service import ProductFamily, ProductAlternate


__all__ = ["ProductFamilyPanel", "show_family_for_product"]


# Variant colors for visual distinction
VARIANT_COLORS = {
    "hp": QColor(46, 204, 113),      # Green - High Performance
    "economy": QColor(241, 196, 15),  # Yellow - Economy
    "standard": QColor(52, 152, 219), # Blue - Standard
    "010": QColor(155, 89, 182),      # Purple - Oversize
    "020": QColor(155, 89, 182),
    "030": QColor(155, 89, 182),
}


class ProductFamilyPanel(QDialog):
    """Dialog showing product family details and variants."""
    
    # Emitted when user selects a variant to view
    variant_selected = Signal(int)  # product_id
    
    def __init__(
        self,
        family: ProductFamily,
        current_product_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.family = family
        self.current_product_id = current_product_id
        
        self._setup_ui()
        self._populate_data()
    
    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        self.setWindowTitle(f"Product Family: {self.family.name}")
        self.setMinimumSize(700, 500)
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # Header section
        header_layout = QHBoxLayout()
        
        title_label = QLabel(self.family.name)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # Badge showing member count
        count = len(self.family.members)
        badge = QLabel(f"{count} variant{'s' if count != 1 else ''}")
        badge.setStyleSheet("""
            QLabel {
                background-color: #3498db;
                color: white;
                padding: 4px 8px;
                border-radius: 10px;
                font-weight: bold;
            }
        """)
        header_layout.addWidget(badge)
        
        layout.addLayout(header_layout)
        
        # Info section
        info_group = QGroupBox("Family Info")
        info_layout = QFormLayout(info_group)
        
        if self.family.base_part_number:
            info_layout.addRow("Base Part #:", QLabel(self.family.base_part_number))
        
        if self.family.oem_part_number:
            info_layout.addRow("OEM Part #:", QLabel(self.family.oem_part_number))
        
        if self.family.description:
            desc_label = QLabel(self.family.description)
            desc_label.setWordWrap(True)
            info_layout.addRow("Description:", desc_label)
        
        layout.addWidget(info_group)
        
        # Variants table
        variants_group = QGroupBox("Variants")
        variants_layout = QVBoxLayout(variants_group)
        
        self.variants_table = QTableWidget()
        self.variants_table.setColumnCount(7)
        self.variants_table.setHorizontalHeaderLabels([
            "SKU", "Title", "Variant", "Detail", "Cost", "Price", "Base?"
        ])
        self.variants_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.variants_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.variants_table.setAlternatingRowColors(True)
        self.variants_table.doubleClicked.connect(self._on_variant_double_click)
        
        variants_layout.addWidget(self.variants_table)
        
        # Quick actions
        actions_layout = QHBoxLayout()
        
        view_btn = QPushButton("View Selected")
        view_btn.clicked.connect(self._view_selected_variant)
        actions_layout.addWidget(view_btn)
        
        compare_btn = QPushButton("Compare All")
        compare_btn.setEnabled(False)  # Future feature
        compare_btn.setToolTip("Coming soon: Compare all variants side-by-side")
        actions_layout.addWidget(compare_btn)
        
        actions_layout.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions_layout.addWidget(close_btn)
        
        variants_layout.addLayout(actions_layout)
        
        layout.addWidget(variants_group)
        
        # Notes section (collapsible)
        if self.family.notes:
            notes_group = QGroupBox("Notes")
            notes_layout = QVBoxLayout(notes_group)
            notes_text = QTextEdit()
            notes_text.setPlainText(self.family.notes)
            notes_text.setReadOnly(True)
            notes_text.setMaximumHeight(80)
            notes_layout.addWidget(notes_text)
            layout.addWidget(notes_group)
    
    def _populate_data(self) -> None:
        """Populate the variants table with family members."""
        members = self.family.members
        self.variants_table.setRowCount(len(members))
        
        for row, member in enumerate(members):
            # SKU
            sku_item = QTableWidgetItem(member.product_sku or "—")
            if member.product_id == self.current_product_id:
                sku_item.setBackground(QColor(46, 204, 113, 50))  # Highlight current
                sku_font = sku_item.font()
                sku_font.setBold(True)
                sku_item.setFont(sku_font)
            self.variants_table.setItem(row, 0, sku_item)
            
            # Title
            title_item = QTableWidgetItem(member.product_title or "—")
            self.variants_table.setItem(row, 1, title_item)
            
            # Variant code with color
            variant_code = member.variant_code or "STD"
            variant_item = QTableWidgetItem(variant_code.upper())
            if variant_code.lower() in VARIANT_COLORS:
                variant_item.setForeground(VARIANT_COLORS[variant_code.lower()])
            variant_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.variants_table.setItem(row, 2, variant_item)
            
            # Detail
            detail_item = QTableWidgetItem(member.variant_detail or "Standard")
            self.variants_table.setItem(row, 3, detail_item)
            
            # Cost
            cost = member.product_cost
            cost_str = f"${cost:,.2f}" if cost else "—"
            cost_item = QTableWidgetItem(cost_str)
            cost_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.variants_table.setItem(row, 4, cost_item)
            
            # Price
            price = member.product_price
            price_str = f"${price:,.2f}" if price else "—"
            price_item = QTableWidgetItem(price_str)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.variants_table.setItem(row, 5, price_item)
            
            # Is base?
            base_item = QTableWidgetItem("✓" if member.is_base_variant else "")
            base_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.variants_table.setItem(row, 6, base_item)
            
            # Store product_id for selection
            sku_item.setData(Qt.ItemDataRole.UserRole, member.product_id)
    
    def _on_variant_double_click(self, index) -> None:
        """Handle double-click on a variant row."""
        self._view_selected_variant()
    
    def _view_selected_variant(self) -> None:
        """Emit signal to view the selected variant."""
        row = self.variants_table.currentRow()
        if row < 0:
            return
        
        item = self.variants_table.item(row, 0)
        if item:
            product_id = item.data(Qt.ItemDataRole.UserRole)
            if product_id:
                self.variant_selected.emit(product_id)
                self.close()


def show_family_for_product(
    product_id: int,
    parent: Optional[QWidget] = None,
) -> Optional[ProductFamilyPanel]:
    """Show the family panel for a product, if it has a family.
    
    Args:
        product_id: The product's database ID
        parent: Parent widget for the dialog
        
    Returns:
        The panel dialog if family found, else None
    """
    from db.product_family_service import get_product_family
    
    family = get_product_family(product_id)
    if not family:
        return None
    
    panel = ProductFamilyPanel(family, current_product_id=product_id, parent=parent)
    panel.exec()
    return panel


class FamilyGroupingWidget(QWidget):
    """Widget for grouping products by family (Ctrl+G toggle)."""
    
    group_by_family_toggled = Signal(bool)
    
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._grouped = False
        self._setup_ui()
    
    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.toggle_btn = QPushButton("Group by Family")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setToolTip("Group products by family (Ctrl+G)")
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)
    
    def _on_toggle(self) -> None:
        self._grouped = self.toggle_btn.isChecked()
        self.group_by_family_toggled.emit(self._grouped)
    
    @property
    def is_grouped(self) -> bool:
        return self._grouped
    
    def set_grouped(self, grouped: bool) -> None:
        self._grouped = grouped
        self.toggle_btn.setChecked(grouped)


class FamilyColumnDelegate:
    """Helper for rendering the Family column in the main table.
    
    Shows:
    - Family name if product belongs to a family
    - Variant badge (HP, E, etc.) if applicable
    - "—" if no family
    """
    
    @staticmethod
    def format_family_cell(
        family_name: Optional[str],
        variant_code: Optional[str] = None,
    ) -> str:
        """Format the family cell text."""
        if not family_name:
            return "—"
        
        if variant_code and variant_code.lower() not in ("std", "standard"):
            return f"{family_name} [{variant_code.upper()}]"
        
        return family_name
    
    @staticmethod
    def get_variant_color(variant_code: Optional[str]) -> Optional[QColor]:
        """Get the color for a variant code."""
        if not variant_code:
            return None
        return VARIANT_COLORS.get(variant_code.lower())
