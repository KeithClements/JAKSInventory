"""Duplicate Detection Dialog for JAKS Scraper.

Shows when a potential duplicate product is detected during scraping or import.
Allows user to choose: Link, Merge, Create Separate, or Skip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QScrollArea,
    QWidget,
    QSizePolicy,
)
from PySide6.QtGui import QFont

if TYPE_CHECKING:
    from db.product_grouping import DuplicateCandidate


class DuplicateAction(Enum):
    """User's chosen action for handling a duplicate."""
    LINK = "link"           # Add part numbers to existing product
    MERGE = "merge"         # Merge all data into existing
    CREATE_SEPARATE = "separate"  # Create as new product anyway
    SKIP = "skip"           # Don't import this product


@dataclass
class DuplicateResolution:
    """Result of the duplicate resolution dialog."""
    action: DuplicateAction
    existing_product_id: int
    remember_choice: bool = False


class DuplicateDetectionDialog(QDialog):
    """Dialog shown when a duplicate product is detected.
    
    Displays:
    - New product info (title, part numbers, source)
    - Existing product info (SKU, title, matching numbers)
    - Match confidence and reason
    - Action options with radio buttons
    - Optional "remember for similar" checkbox
    """
    
    # Signal emitted with the user's resolution
    resolution_chosen = Signal(object)  # DuplicateResolution
    
    def __init__(
        self,
        parent: QWidget | None,
        new_product: dict,
        duplicate: "DuplicateCandidate",
    ):
        super().__init__(parent)
        self.new_product = new_product
        self.duplicate = duplicate
        self.result_action: DuplicateAction | None = None
        
        self._setup_ui()
        self._connect_signals()
        
    def _setup_ui(self):
        """Build the dialog UI."""
        self.setWindowTitle("⚠️ Potential Duplicate Detected")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # Header with warning icon and confidence
        header = self._create_header()
        layout.addWidget(header)
        
        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(12)
        
        # New product info
        new_group = self._create_new_product_group()
        scroll_layout.addWidget(new_group)
        
        # Match arrow
        arrow_label = QLabel("↕️ MATCHES")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow_label.setStyleSheet("font-weight: bold; color: #f39c12; font-size: 14px;")
        scroll_layout.addWidget(arrow_label)
        
        # Existing product info
        existing_group = self._create_existing_product_group()
        scroll_layout.addWidget(existing_group)
        
        # Match details
        match_group = self._create_match_details_group()
        scroll_layout.addWidget(match_group)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        
        # Action options
        action_group = self._create_action_options()
        layout.addWidget(action_group)
        
        # Remember checkbox
        self.remember_check = QCheckBox("Remember this choice for similar matches (same confidence level)")
        layout.addWidget(self.remember_check)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumWidth(100)
        button_layout.addWidget(self.cancel_btn)
        
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setMinimumWidth(100)
        self.apply_btn.setDefault(True)
        self.apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        button_layout.addWidget(self.apply_btn)
        
        layout.addLayout(button_layout)
        
    def _create_header(self) -> QWidget:
        """Create the header with confidence badge."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Warning icon and title
        title = QLabel("⚠️ Potential Duplicate Detected")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        layout.addStretch()
        
        # Confidence badge
        confidence = int(self.duplicate.confidence * 100)
        if confidence >= 90:
            color = "#27ae60"  # Green
            text = f"🎯 {confidence}% Match"
        elif confidence >= 70:
            color = "#f39c12"  # Orange
            text = f"🔍 {confidence}% Match"
        else:
            color = "#e74c3c"  # Red
            text = f"❓ {confidence}% Match"
            
        badge = QLabel(text)
        badge.setStyleSheet(f"""
            background-color: {color};
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-weight: bold;
        """)
        layout.addWidget(badge)
        
        return widget
        
    def _create_new_product_group(self) -> QGroupBox:
        """Create the 'New Product' info group."""
        group = QGroupBox("🆕 New Product")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #3498db;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }
        """)
        
        layout = QVBoxLayout(group)
        
        # Title
        title = self.new_product.get('title', 'Unknown')
        title_label = QLabel(f"<b>Title:</b> {title}")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        
        # Part numbers
        part_numbers = []
        for key in ('primary_pn', 'oem_pn', 'supplier_part_number', 'pai_sku'):
            val = self.new_product.get(key)
            if val:
                part_numbers.append(str(val))
        
        xrefs = self.new_product.get('xrefs') or []
        if xrefs:
            part_numbers.extend([str(x) for x in xrefs[:5]])  # Limit display
            
        pn_text = ", ".join(part_numbers) if part_numbers else "None"
        pn_label = QLabel(f"<b>Part Numbers:</b> {pn_text}")
        pn_label.setWordWrap(True)
        layout.addWidget(pn_label)
        
        # Source
        source = self.new_product.get('source', 'Unknown')
        source_label = QLabel(f"<b>Source:</b> {source}")
        layout.addWidget(source_label)
        
        # Category/Engine
        category = self.new_product.get('category', '')
        engine = self.new_product.get('engine', '')
        if category or engine:
            meta_label = QLabel(f"<b>Category:</b> {category}  |  <b>Engine:</b> {engine}")
            layout.addWidget(meta_label)
        
        return group
        
    def _create_existing_product_group(self) -> QGroupBox:
        """Create the 'Existing Product' info group."""
        group = QGroupBox("📦 Existing Product")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #27ae60;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }
        """)
        
        layout = QVBoxLayout(group)
        
        # SKU
        sku_label = QLabel(f"<b>SKU:</b> {self.duplicate.existing_sku}")
        layout.addWidget(sku_label)
        
        # Title
        title_label = QLabel(f"<b>Title:</b> {self.duplicate.existing_title}")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        
        # Matching numbers
        matching = ", ".join(self.duplicate.matching_numbers) if self.duplicate.matching_numbers else "N/A"
        match_label = QLabel(f"<b>Matching Numbers:</b> {matching}")
        match_label.setWordWrap(True)
        match_label.setStyleSheet("color: #27ae60;")
        layout.addWidget(match_label)
        
        return group
        
    def _create_match_details_group(self) -> QGroupBox:
        """Create the match details group."""
        group = QGroupBox("🔍 Match Details")
        layout = QVBoxLayout(group)
        
        # Match type
        match_type = self.duplicate.match_type.replace("_", " ").title()
        type_label = QLabel(f"<b>Match Type:</b> {match_type}")
        layout.addWidget(type_label)
        
        # Reason
        reason = getattr(self.duplicate, 'reason', '') or "Part number match detected"
        reason_label = QLabel(f"<b>Reason:</b> {reason}")
        reason_label.setWordWrap(True)
        layout.addWidget(reason_label)
        
        # Recommendation
        rec = self.duplicate.recommendation.upper()
        if rec == "LINK":
            rec_text = "✅ LINK - Add part numbers to existing product (recommended)"
            rec_color = "#27ae60"
        elif rec == "MERGE":
            rec_text = "🔄 MERGE - Combine all data into existing product"
            rec_color = "#3498db"
        elif rec == "SEPARATE":
            rec_text = "➕ SEPARATE - These appear to be different products"
            rec_color = "#e74c3c"
        else:
            rec_text = f"❓ {rec}"
            rec_color = "#7f8c8d"
            
        rec_label = QLabel(f"<b>Recommendation:</b> <span style='color: {rec_color}'>{rec_text}</span>")
        rec_label.setWordWrap(True)
        layout.addWidget(rec_label)
        
        return group
        
    def _create_action_options(self) -> QGroupBox:
        """Create the action selection options."""
        group = QGroupBox("What would you like to do?")
        layout = QVBoxLayout(group)
        
        self.action_group = QButtonGroup(self)
        
        # Link option (default for high confidence)
        self.link_radio = QRadioButton(
            "🔗 Link to existing product\n"
            "     Add new part numbers as cross-references to the existing product.\n"
            "     Best for: Same physical product, different vendor/part number."
        )
        self.link_radio.setStyleSheet("QRadioButton { padding: 8px 0; }")
        self.action_group.addButton(self.link_radio)
        layout.addWidget(self.link_radio)
        
        # Merge option
        self.merge_radio = QRadioButton(
            "🔄 Merge into existing\n"
            "     Combine all data (images, description, specs) into existing.\n"
            "     Best for: Better data in new product that should update existing."
        )
        self.merge_radio.setStyleSheet("QRadioButton { padding: 8px 0; }")
        self.action_group.addButton(self.merge_radio)
        layout.addWidget(self.merge_radio)
        
        # Create separate option
        self.separate_radio = QRadioButton(
            "➕ Create as separate product\n"
            "     I know these are different products despite the match.\n"
            "     Best for: False positive - different products with similar numbers."
        )
        self.separate_radio.setStyleSheet("QRadioButton { padding: 8px 0; }")
        self.action_group.addButton(self.separate_radio)
        layout.addWidget(self.separate_radio)
        
        # Skip option
        self.skip_radio = QRadioButton(
            "⏭️ Skip this product\n"
            "     Don't import this product at all."
        )
        self.skip_radio.setStyleSheet("QRadioButton { padding: 8px 0; }")
        self.action_group.addButton(self.skip_radio)
        layout.addWidget(self.skip_radio)
        
        # Set default based on recommendation
        if self.duplicate.recommendation == "link":
            self.link_radio.setChecked(True)
        elif self.duplicate.recommendation == "merge":
            self.merge_radio.setChecked(True)
        elif self.duplicate.recommendation == "separate":
            self.separate_radio.setChecked(True)
        else:
            self.link_radio.setChecked(True)  # Default to link
            
        return group
        
    def _connect_signals(self):
        """Connect button signals."""
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn.clicked.connect(self._on_apply)
        
    def _on_apply(self):
        """Handle apply button click."""
        if self.link_radio.isChecked():
            action = DuplicateAction.LINK
        elif self.merge_radio.isChecked():
            action = DuplicateAction.MERGE
        elif self.separate_radio.isChecked():
            action = DuplicateAction.CREATE_SEPARATE
        elif self.skip_radio.isChecked():
            action = DuplicateAction.SKIP
        else:
            action = DuplicateAction.LINK  # Default
            
        self.result_action = action
        resolution = DuplicateResolution(
            action=action,
            existing_product_id=self.duplicate.existing_product_id,
            remember_choice=self.remember_check.isChecked(),
        )
        self.resolution_chosen.emit(resolution)
        self.accept()
        
    def get_resolution(self) -> DuplicateResolution | None:
        """Get the user's resolution after dialog closes."""
        if self.result_action is None:
            return None
        return DuplicateResolution(
            action=self.result_action,
            existing_product_id=self.duplicate.existing_product_id,
            remember_choice=self.remember_check.isChecked(),
        )


class BatchDuplicateDialog(QDialog):
    """Dialog for handling multiple duplicates at once during batch import.
    
    Shows a summary and allows bulk actions.
    """
    
    def __init__(
        self,
        parent: QWidget | None,
        duplicates: list[tuple[dict, "DuplicateCandidate"]],
    ):
        super().__init__(parent)
        self.duplicates = duplicates
        self.resolutions: list[DuplicateResolution] = []
        
        self._setup_ui()
        
    def _setup_ui(self):
        """Build the batch dialog UI."""
        self.setWindowTitle(f"⚠️ {len(self.duplicates)} Duplicates Detected")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        
        layout = QVBoxLayout(self)
        
        # Summary header
        header = QLabel(f"""
            <h2>⚠️ {len(self.duplicates)} Potential Duplicates Found</h2>
            <p>The following products match existing items in your inventory.</p>
        """)
        layout.addWidget(header)
        
        # Count by recommendation
        link_count = sum(1 for _, d in self.duplicates if d.recommendation == "link")
        merge_count = sum(1 for _, d in self.duplicates if d.recommendation == "merge")
        separate_count = sum(1 for _, d in self.duplicates if d.recommendation == "separate")
        
        summary = QLabel(f"""
            <p>
            <span style='color: #27ae60'>🔗 {link_count} recommended to LINK</span> | 
            <span style='color: #3498db'>🔄 {merge_count} recommended to MERGE</span> | 
            <span style='color: #e74c3c'>➕ {separate_count} recommended SEPARATE</span>
            </p>
        """)
        layout.addWidget(summary)
        
        # Scroll area with list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        
        for i, (new_prod, dup) in enumerate(self.duplicates[:50]):  # Limit display
            item = self._create_duplicate_item(i, new_prod, dup)
            scroll_layout.addWidget(item)
            
        if len(self.duplicates) > 50:
            more_label = QLabel(f"... and {len(self.duplicates) - 50} more")
            more_label.setStyleSheet("color: #7f8c8d; font-style: italic;")
            scroll_layout.addWidget(more_label)
            
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        
        # Bulk action buttons
        action_layout = QHBoxLayout()
        
        link_all_btn = QPushButton(f"🔗 Link All ({link_count})")
        link_all_btn.clicked.connect(lambda: self._bulk_action(DuplicateAction.LINK))
        action_layout.addWidget(link_all_btn)
        
        use_recommended_btn = QPushButton("✅ Use Recommendations")
        use_recommended_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
            }
        """)
        use_recommended_btn.clicked.connect(self._use_recommendations)
        action_layout.addWidget(use_recommended_btn)
        
        skip_all_btn = QPushButton("⏭️ Skip All")
        skip_all_btn.clicked.connect(lambda: self._bulk_action(DuplicateAction.SKIP))
        action_layout.addWidget(skip_all_btn)
        
        layout.addLayout(action_layout)
        
        # Cancel/Review buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        review_btn = QPushButton("Review One-by-One")
        review_btn.clicked.connect(self._review_individually)
        button_layout.addWidget(review_btn)
        
        layout.addLayout(button_layout)
        
    def _create_duplicate_item(
        self, 
        index: int, 
        new_prod: dict, 
        dup: "DuplicateCandidate"
    ) -> QFrame:
        """Create a single duplicate item row."""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 4px;
                padding: 8px;
                margin: 2px 0;
            }
        """)
        
        layout = QHBoxLayout(frame)
        
        # New product title
        new_title = new_prod.get('title', 'Unknown')[:40]
        if len(new_prod.get('title', '')) > 40:
            new_title += "..."
        
        info = QLabel(f"""
            <b>{new_title}</b><br/>
            <small>→ matches: {dup.existing_sku}</small>
        """)
        info.setMinimumWidth(300)
        layout.addWidget(info, 1)
        
        # Confidence
        conf = int(dup.confidence * 100)
        conf_label = QLabel(f"{conf}%")
        conf_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(conf_label)
        
        # Recommendation badge
        rec = dup.recommendation.upper()
        if rec == "LINK":
            badge_style = "background-color: #27ae60; color: white;"
        elif rec == "MERGE":
            badge_style = "background-color: #3498db; color: white;"
        else:
            badge_style = "background-color: #e74c3c; color: white;"
            
        badge = QLabel(rec)
        badge.setStyleSheet(f"{badge_style} padding: 2px 8px; border-radius: 4px;")
        layout.addWidget(badge)
        
        return frame
        
    def _bulk_action(self, action: DuplicateAction):
        """Apply the same action to all duplicates."""
        self.resolutions = [
            DuplicateResolution(
                action=action,
                existing_product_id=dup.existing_product_id,
            )
            for _, dup in self.duplicates
        ]
        self.accept()
        
    def _use_recommendations(self):
        """Use the recommended action for each duplicate."""
        self.resolutions = []
        for _, dup in self.duplicates:
            if dup.recommendation == "link":
                action = DuplicateAction.LINK
            elif dup.recommendation == "merge":
                action = DuplicateAction.MERGE
            elif dup.recommendation == "separate":
                action = DuplicateAction.CREATE_SEPARATE
            else:
                action = DuplicateAction.LINK  # Default
                
            self.resolutions.append(DuplicateResolution(
                action=action,
                existing_product_id=dup.existing_product_id,
            ))
        self.accept()
        
    def _review_individually(self):
        """Open individual review dialogs for each duplicate."""
        self.resolutions = []
        for new_prod, dup in self.duplicates:
            dialog = DuplicateDetectionDialog(self, new_prod, dup)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                resolution = dialog.get_resolution()
                if resolution:
                    self.resolutions.append(resolution)
            else:
                # User cancelled - stop reviewing
                self.reject()
                return
        self.accept()
        
    def get_resolutions(self) -> list[DuplicateResolution]:
        """Get all resolutions after dialog closes."""
        return self.resolutions
