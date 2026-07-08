"""Conflict Resolution Dialog for Shopify sync conflicts.

Shows side-by-side comparison of local vs Shopify data when
differences are detected during sync operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QScrollArea,
    QWidget,
    QGridLayout,
    QGroupBox,
    QTextBrowser,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from ui.theme import THEME_COLORS


class ConflictField(QFrame):
    """A single field showing local vs Shopify value comparison."""
    
    def __init__(
        self,
        field_name: str,
        local_value: str,
        shopify_value: str,
        parent=None,
    ):
        super().__init__(parent)
        self._field_name = field_name
        self._local_value = local_value
        self._shopify_value = shopify_value
        self._has_conflict = str(local_value) != str(shopify_value)
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(12)
        
        # Field label
        label = QLabel(f"{self._field_name}:")
        label.setFixedWidth(100)
        label.setStyleSheet("font-weight: bold; color: #333;")
        layout.addWidget(label)
        
        # Local value
        local_lbl = QLabel(str(self._local_value) or "(empty)")
        local_lbl.setFixedWidth(200)
        local_lbl.setWordWrap(True)
        
        # Shopify value
        shopify_lbl = QLabel(str(self._shopify_value) or "(empty)")
        shopify_lbl.setFixedWidth(200)
        shopify_lbl.setWordWrap(True)
        
        # Highlight differences
        if self._has_conflict:
            self.setStyleSheet(f"background-color: #fff3cd; border-radius: 4px;")
            local_lbl.setStyleSheet("color: #856404;")
            shopify_lbl.setStyleSheet("color: #856404;")
        else:
            local_lbl.setStyleSheet("color: #28a745;")
            shopify_lbl.setStyleSheet("color: #28a745;")
        
        layout.addWidget(local_lbl)
        layout.addWidget(shopify_lbl)
        layout.addStretch()
    
    @property
    def has_conflict(self) -> bool:
        return self._has_conflict


class ConflictResolutionDialog(QDialog):
    """Dialog for resolving conflicts between local and Shopify data.
    
    Shows side-by-side comparison and allows user to choose which version to keep.
    
    Signals:
        resolution_chosen: Emitted when user makes a choice.
            - 'local': Use local values
            - 'shopify': Use Shopify values
            - 'merge': Keep both (mark for manual review)
            - 'skip': Skip this product
    """
    
    resolution_chosen = Signal(str, dict)  # choice, merged_data
    
    def __init__(
        self,
        sku: str,
        local_data: dict,
        shopify_data: dict,
        parent=None,
    ):
        super().__init__(parent)
        self._sku = sku
        self._local_data = local_data
        self._shopify_data = shopify_data
        self._conflict_fields: list[ConflictField] = []
        
        self.setWindowTitle(f"Resolve Conflict: {sku}")
        self.setMinimumSize(700, 500)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {THEME_COLORS['bg']};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 8px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }}
            QPushButton {{
                background-color: {THEME_COLORS['panel']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 500;
                min-width: 100px;
            }}
            QPushButton:hover {{
                background-color: {THEME_COLORS['bg']};
                border-color: {THEME_COLORS['primary']};
            }}
            QPushButton:pressed {{
                background-color: {THEME_COLORS['primary']};
                color: white;
            }}
        """)
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QLabel(f"⚠️ Conflict Detected: {self._sku}")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        layout.addWidget(header)
        
        subtitle = QLabel(
            "The local data differs from Shopify. "
            "Choose which version to keep or merge."
        )
        subtitle.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(subtitle)
        
        # Column headers
        header_row = QHBoxLayout()
        header_row.setContentsMargins(8, 0, 8, 0)
        
        field_header = QLabel("Field")
        field_header.setFixedWidth(100)
        field_header.setStyleSheet("font-weight: bold;")
        header_row.addWidget(field_header)
        
        local_header = QLabel("📁 Local")
        local_header.setFixedWidth(200)
        local_header.setStyleSheet("font-weight: bold; color: #007bff;")
        header_row.addWidget(local_header)
        
        shopify_header = QLabel("🛒 Shopify")
        shopify_header.setFixedWidth(200)
        shopify_header.setStyleSheet("font-weight: bold; color: #28a745;")
        header_row.addWidget(shopify_header)
        
        header_row.addStretch()
        layout.addLayout(header_row)
        
        # Scrollable comparison area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        
        # Compare common fields
        compare_fields = [
            ("Title", "title"),
            ("Price", "selling_price"),
            ("Compare At", "compare_at_price"),
            ("Cost", "cost"),
            ("Status", "shopify_status"),
            ("Qty On Hand", "qty_on_hand"),
            ("Category", "category"),
            ("Manufacturer", "manufacturer"),
        ]
        
        for display_name, field_key in compare_fields:
            local_val = self._local_data.get(field_key, "")
            shopify_val = self._shopify_data.get(field_key, "")
            
            # Format prices
            if "price" in field_key.lower() or field_key == "cost":
                try:
                    if local_val:
                        local_val = f"${float(local_val):,.2f}"
                    if shopify_val:
                        shopify_val = f"${float(shopify_val):,.2f}"
                except:
                    pass
            
            field_widget = ConflictField(display_name, local_val, shopify_val)
            self._conflict_fields.append(field_widget)
            content_layout.addWidget(field_widget)
        
        # Description comparison (HTML)
        local_desc = self._local_data.get("description_html", "")
        shopify_desc = self._shopify_data.get("body_html", "") or self._shopify_data.get("description_html", "")
        
        if local_desc != shopify_desc:
            desc_group = QGroupBox("📝 Description Comparison")
            desc_layout = QHBoxLayout(desc_group)
            
            local_browser = QTextBrowser()
            local_browser.setHtml(local_desc[:1000] if local_desc else "<i>(empty)</i>")
            local_browser.setMaximumHeight(150)
            
            shopify_browser = QTextBrowser()
            shopify_browser.setHtml(shopify_desc[:1000] if shopify_desc else "<i>(empty)</i>")
            shopify_browser.setMaximumHeight(150)
            
            desc_layout.addWidget(QLabel("Local:"))
            desc_layout.addWidget(local_browser)
            desc_layout.addWidget(QLabel("Shopify:"))
            desc_layout.addWidget(shopify_browser)
            
            content_layout.addWidget(desc_group)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # Summary
        conflict_count = sum(1 for f in self._conflict_fields if f.has_conflict)
        summary = QLabel(f"🔍 {conflict_count} field(s) with differences")
        summary.setStyleSheet("color: #856404; font-weight: bold; margin-top: 8px;")
        layout.addWidget(summary)
        
        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        use_local_btn = QPushButton("📁 Use Local")
        use_local_btn.setToolTip("Keep local values and push to Shopify")
        use_local_btn.setStyleSheet(use_local_btn.styleSheet() + "background-color: #e3f2fd;")
        use_local_btn.clicked.connect(lambda: self._on_resolution("local"))
        btn_layout.addWidget(use_local_btn)
        
        use_shopify_btn = QPushButton("🛒 Use Shopify")
        use_shopify_btn.setToolTip("Keep Shopify values and update local")
        use_shopify_btn.setStyleSheet(use_shopify_btn.styleSheet() + "background-color: #e8f5e9;")
        use_shopify_btn.clicked.connect(lambda: self._on_resolution("shopify"))
        btn_layout.addWidget(use_shopify_btn)
        
        merge_btn = QPushButton("🔀 Mark for Review")
        merge_btn.setToolTip("Keep both - flag for manual review")
        merge_btn.setStyleSheet(merge_btn.styleSheet() + "background-color: #fff3e0;")
        merge_btn.clicked.connect(lambda: self._on_resolution("merge"))
        btn_layout.addWidget(merge_btn)
        
        btn_layout.addStretch()
        
        skip_btn = QPushButton("⏭️ Skip")
        skip_btn.setToolTip("Skip this product without changes")
        skip_btn.clicked.connect(lambda: self._on_resolution("skip"))
        btn_layout.addWidget(skip_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
    
    def _on_resolution(self, choice: str):
        """Handle resolution choice."""
        merged_data = {}
        
        if choice == "local":
            merged_data = self._local_data.copy()
        elif choice == "shopify":
            merged_data = self._shopify_data.copy()
        elif choice == "merge":
            # Combine both - local takes precedence for most fields
            merged_data = {**self._shopify_data, **self._local_data}
            merged_data["needs_manual_review"] = True
        
        self.resolution_chosen.emit(choice, merged_data)
        self.accept()
    
    @classmethod
    def resolve_conflict(
        cls,
        sku: str,
        local_data: dict,
        shopify_data: dict,
        parent=None,
    ) -> tuple[str, dict]:
        """Show dialog and return resolution.
        
        Returns:
            Tuple of (choice, merged_data) where choice is:
            'local', 'shopify', 'merge', 'skip', or 'cancelled'
        """
        dialog = cls(sku, local_data, shopify_data, parent)
        
        result = {"choice": "cancelled", "data": {}}
        
        def on_choice(choice: str, data: dict):
            result["choice"] = choice
            result["data"] = data
        
        dialog.resolution_chosen.connect(on_choice)
        
        if dialog.exec() == QDialog.Accepted:
            return result["choice"], result["data"]
        
        return "cancelled", {}


class BatchConflictDialog(QDialog):
    """Dialog for handling multiple conflicts in batch operations.
    
    Shows a list of conflicting products and allows resolving all at once.
    """
    
    def __init__(
        self,
        conflicts: list[tuple[str, dict, dict]],  # List of (sku, local, shopify)
        parent=None,
    ):
        super().__init__(parent)
        self._conflicts = conflicts
        self._resolutions: dict[str, str] = {}  # sku -> resolution
        
        self.setWindowTitle(f"Resolve {len(conflicts)} Conflicts")
        self.setMinimumSize(600, 400)
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        # Header
        header = QLabel(f"⚠️ {len(self._conflicts)} products have conflicts")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        layout.addWidget(header)
        
        # Conflict list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        content = QWidget()
        content_layout = QVBoxLayout(content)
        
        for sku, local_data, shopify_data in self._conflicts:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background-color: #fff3cd;
                    border: 1px solid #ffc107;
                    border-radius: 4px;
                    padding: 8px;
                    margin: 2px;
                }}
            """)
            row_layout = QHBoxLayout(row)
            
            sku_label = QLabel(f"📦 {sku}")
            sku_label.setStyleSheet("font-weight: bold;")
            row_layout.addWidget(sku_label)
            
            local_price = local_data.get("selling_price", 0)
            shopify_price = shopify_data.get("selling_price", 0)
            
            diff_label = QLabel(f"Local: ${local_price:,.2f} | Shopify: ${shopify_price:,.2f}")
            diff_label.setStyleSheet("color: #666;")
            row_layout.addWidget(diff_label)
            
            row_layout.addStretch()
            
            detail_btn = QPushButton("View")
            detail_btn.setFixedWidth(60)
            detail_btn.clicked.connect(lambda checked, s=sku, l=local_data, sh=shopify_data: self._show_detail(s, l, sh))
            row_layout.addWidget(detail_btn)
            
            content_layout.addWidget(row)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        
        # Batch action buttons
        btn_layout = QHBoxLayout()
        
        all_local_btn = QPushButton("📁 All Use Local")
        all_local_btn.setToolTip("Use local values for all conflicts")
        all_local_btn.clicked.connect(lambda: self._resolve_all("local"))
        btn_layout.addWidget(all_local_btn)
        
        all_shopify_btn = QPushButton("🛒 All Use Shopify")
        all_shopify_btn.setToolTip("Use Shopify values for all conflicts")
        all_shopify_btn.clicked.connect(lambda: self._resolve_all("shopify"))
        btn_layout.addWidget(all_shopify_btn)
        
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        layout.addLayout(btn_layout)
    
    def _show_detail(self, sku: str, local_data: dict, shopify_data: dict):
        """Show detailed conflict dialog for single product."""
        choice, data = ConflictResolutionDialog.resolve_conflict(
            sku, local_data, shopify_data, self
        )
        
        if choice != "cancelled":
            self._resolutions[sku] = choice
    
    def _resolve_all(self, choice: str):
        """Apply same resolution to all conflicts."""
        for sku, _, _ in self._conflicts:
            self._resolutions[sku] = choice
        
        self.accept()
    
    def get_resolutions(self) -> dict[str, str]:
        """Get the resolution choices for each SKU."""
        return self._resolutions
