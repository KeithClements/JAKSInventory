"""Action bar widget for JAKS Scraper.

Contains selection controls, pricing controls, and export buttons.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QGroupBox,
    QPushButton,
    QComboBox,
    QCheckBox,
    QLineEdit,
    QLabel,
)

__all__ = ["ActionBar"]


class ActionBar(QWidget):
    """Action bar with selection, pricing, and export controls."""

    # Signals
    select_all_clicked = Signal()
    select_none_clicked = Signal()
    select_warnings_clicked = Signal()
    select_range_clicked = Signal()
    filters_changed = Signal()  # stage/supplier/condition/errors-only
    
    markup_changed = Signal()
    publish_changed = Signal(bool)
    include_core_changed = Signal(bool)
    validate_only_changed = Signal(bool)
    set_margin_clicked = Signal()
    adjust_cog_clicked = Signal()
    
    export_csv_clicked = Signal()
    export_qbo_clicked = Signal()
    undo_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Create the action bar layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Selection row
        selection_row = QHBoxLayout()
        selection_row.setSpacing(4)

        self.select_all_btn = QPushButton("Select all")
        self.select_all_btn.setEnabled(False)
        selection_row.addWidget(self.select_all_btn)

        self.select_warnings_btn = QPushButton("Select warnings")
        self.select_warnings_btn.setEnabled(False)
        selection_row.addWidget(self.select_warnings_btn)

        self.select_range_btn = QPushButton("Select Range...")
        self.select_range_btn.setEnabled(False)
        self.select_range_btn.setToolTip("Select rows by range (e.g., 5-50)")
        selection_row.addWidget(self.select_range_btn)

        selection_row.addSpacing(12)

        # Filter group (Phase 6)
        self.filter_stage_combo = QComboBox()
        self.filter_stage_combo.addItems([
            "All stages",
            "Stage 1",
            "Stage 2",
            "Stage 3",
            "Stage 4",
            "Stage 5",
            "Errors",
        ])
        self.filter_stage_combo.setToolTip("Filter table rows by stage")
        selection_row.addWidget(self.filter_stage_combo)

        self.filter_pai_combo = QComboBox()
        self.filter_pai_combo.addItems([
            "All PAI",
            "PAI Multi-Match",
            "PAI In-Stock Options",
            "PAI Exact",
            "PAI No Match",
            "No PAI Data",
        ])
        self.filter_pai_combo.setToolTip("Filter by PAI enrichment status")
        selection_row.addWidget(self.filter_pai_combo)

        self.filter_supplier_combo = QComboBox()
        self.filter_supplier_combo.addItems(["All suppliers"])
        self.filter_supplier_combo.setToolTip("Filter table rows by supplier")
        selection_row.addWidget(self.filter_supplier_combo)

        self.filter_condition_combo = QComboBox()
        self.filter_condition_combo.addItems(["All conditions", "New", "Reman"])
        self.filter_condition_combo.setToolTip("Filter table rows by condition")
        selection_row.addWidget(self.filter_condition_combo)

        self.filter_errors_only = QCheckBox("Errors only")
        self.filter_errors_only.setToolTip("Show only rows that have errors")
        selection_row.addWidget(self.filter_errors_only)

        self.clear_filters_btn = QPushButton("Clear Filters")
        self.clear_filters_btn.setToolTip("Reset all table filters")
        selection_row.addWidget(self.clear_filters_btn)

        selection_row.addStretch(1)
        
        # Progress indicator (right side)
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("color: #606050; font-size: 12px; padding-right: 8px;")
        selection_row.addWidget(self.progress_label)
        
        layout.addLayout(selection_row)

        # Pricing row
        pricing_row = QHBoxLayout()
        pricing_row.setSpacing(4)

        pricing_row.addWidget(QLabel("Markup %:"))
        self.markup_input = QLineEdit("30")
        self.markup_input.setFixedWidth(50)
        self.markup_input.setToolTip("Default markup percentage for pricing")
        pricing_row.addWidget(self.markup_input)

        self.include_core_checkbox = QCheckBox("Include core")
        self.include_core_checkbox.setChecked(True)
        self.include_core_checkbox.setToolTip("Add core charge to selling price")
        pricing_row.addWidget(self.include_core_checkbox)

        self.publish_checkbox = QCheckBox("Publish")
        self.publish_checkbox.setChecked(True)
        self.publish_checkbox.setToolTip("Publish products immediately (vs draft)")
        pricing_row.addWidget(self.publish_checkbox)

        self.set_margin_btn = QPushButton("Set Margin %")
        self.set_margin_btn.setToolTip("Set My Margin % for selected rows (or all if none selected)")
        pricing_row.addWidget(self.set_margin_btn)

        self.adjust_cog_btn = QPushButton("Adjust COG")
        self.adjust_cog_btn.setToolTip("Adjust My cost by % for selected rows (or all if none selected)")
        pricing_row.addWidget(self.adjust_cog_btn)

        pricing_row.addStretch(1)
        layout.addLayout(pricing_row)

        # Export row
        export_row = QHBoxLayout()
        export_row.setSpacing(4)

        self.export_csv_btn = QPushButton("Export CSV")
        self.export_csv_btn.setToolTip("Export all current products to CSV file")
        self.export_csv_btn.setEnabled(False)
        export_row.addWidget(self.export_csv_btn)

        self.export_qbo_btn = QPushButton("Export Shopify → QBO CSV")
        self.export_qbo_btn.setToolTip("Export Shopify products to QuickBooks Online format")
        export_row.addWidget(self.export_qbo_btn)

        self.upload_draft_btn = QPushButton("Upload as Draft")
        self.upload_draft_btn.setToolTip("Upload as draft (unpublished) to Shopify")
        self.upload_draft_btn.setEnabled(False)
        export_row.addWidget(self.upload_draft_btn)

        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setToolTip("Undo last edit batch (single step)")
        self.undo_btn.setEnabled(False)
        export_row.addWidget(self.undo_btn)

        export_row.addStretch(1)
        layout.addLayout(export_row)

    def _connect_signals(self) -> None:
        """Connect internal widget signals to external signals."""
        self.select_all_btn.clicked.connect(self.select_all_clicked)
        self.select_warnings_btn.clicked.connect(self.select_warnings_clicked)
        self.select_range_btn.clicked.connect(self.select_range_clicked)

        self.filter_stage_combo.currentIndexChanged.connect(lambda *_: self.filters_changed.emit())
        self.filter_pai_combo.currentIndexChanged.connect(lambda *_: self.filters_changed.emit())
        self.filter_supplier_combo.currentIndexChanged.connect(lambda *_: self.filters_changed.emit())
        self.filter_condition_combo.currentIndexChanged.connect(lambda *_: self.filters_changed.emit())
        self.filter_errors_only.stateChanged.connect(lambda *_: self.filters_changed.emit())
        self.clear_filters_btn.clicked.connect(self.reset_filters)

        self.markup_input.editingFinished.connect(self.markup_changed)
        self.publish_checkbox.toggled.connect(self.publish_changed)
        self.include_core_checkbox.toggled.connect(self.include_core_changed)
        self.set_margin_btn.clicked.connect(self.set_margin_clicked)
        self.adjust_cog_btn.clicked.connect(self.adjust_cog_clicked)

        self.export_csv_btn.clicked.connect(self.export_csv_clicked)
        self.export_qbo_btn.clicked.connect(self.export_qbo_clicked)
        self.undo_btn.clicked.connect(self.undo_clicked)

    # Public API
    def get_markup_pct(self) -> float:
        """Get the current markup percentage."""
        try:
            return float(self.markup_input.text() or "30")
        except ValueError:
            return 30.0

    def set_markup_pct(self, pct: float) -> None:
        """Set the markup percentage."""
        self.markup_input.setText(str(int(pct)))

    def is_publish(self) -> bool:
        """Check if publish is enabled."""
        return self.publish_checkbox.isChecked()

    def is_include_core(self) -> bool:
        """Check if core charge should be included."""
        return self.include_core_checkbox.isChecked()

    def set_selection_enabled(self, enabled: bool) -> None:
        """Enable or disable selection buttons."""
        self.select_all_btn.setEnabled(enabled)
        self.select_warnings_btn.setEnabled(enabled)
        self.select_range_btn.setEnabled(enabled)

    def set_export_enabled(self, enabled: bool) -> None:
        """Enable or disable export buttons."""
        self.export_csv_btn.setEnabled(enabled)

    def set_upload_draft_enabled(self, enabled: bool) -> None:
        """Enable or disable upload draft button."""
        self.upload_draft_btn.setEnabled(enabled)

    def set_qbo_enabled(self, enabled: bool) -> None:
        """Enable or disable QBO export button."""
        self.export_qbo_btn.setEnabled(enabled)

    def set_undo_enabled(self, enabled: bool) -> None:
        """Enable or disable undo button."""
        self.undo_btn.setEnabled(enabled)

    def set_supplier_options(self, suppliers: list[str]) -> None:
        """Populate supplier filter dropdown."""
        current = str(self.filter_supplier_combo.currentText() or "")
        self.filter_supplier_combo.blockSignals(True)
        try:
            self.filter_supplier_combo.clear()
            self.filter_supplier_combo.addItem("All suppliers")
            for s in suppliers:
                s2 = str(s or "").strip()
                if s2:
                    self.filter_supplier_combo.addItem(s2)
            # Restore if still present
            idx = self.filter_supplier_combo.findText(current)
            if idx >= 0:
                self.filter_supplier_combo.setCurrentIndex(idx)
        finally:
            self.filter_supplier_combo.blockSignals(False)

    def get_filter_state(self) -> dict[str, object]:
        """Return current filter state for the table."""
        return {
            "stage": str(self.filter_stage_combo.currentText() or ""),
            "pai_status": str(self.filter_pai_combo.currentText() or ""),
            "supplier": str(self.filter_supplier_combo.currentText() or ""),
            "condition": str(self.filter_condition_combo.currentText() or ""),
            "errors_only": bool(self.filter_errors_only.isChecked()),
        }

    def reset_filters(self) -> None:
        """Reset filters to defaults."""
        self.filter_stage_combo.setCurrentIndex(0)
        self.filter_pai_combo.setCurrentIndex(0)
        self.filter_supplier_combo.setCurrentIndex(0)
        self.filter_condition_combo.setCurrentIndex(0)
        self.filter_errors_only.setChecked(False)
        self.filters_changed.emit()

    def set_progress(self, text: str, color: str = "#606050") -> None:
        """Update the progress label text and color.
        
        Args:
            text: Progress text to display (e.g., "Scraping 5/20...")
            color: Text color (default gray, use green for success, red for error)
        """
        try:
            self.progress_label.setText(text)
            self.progress_label.setStyleSheet(f"color: {color}; font-size: 12px; padding-right: 8px;")
        except Exception:
            pass

    # GUI Cleanup methods
    def hide_pricing_controls(self) -> None:
        """Hide pricing-related controls (for scrape-only screen)."""
        self.markup_input.setVisible(False)
        self.markup_input.parentWidget()  # Label is separate
        self.include_core_checkbox.setVisible(False)
        self.publish_checkbox.setVisible(False)
        self.set_margin_btn.setVisible(False)
        self.adjust_cog_btn.setVisible(False)
        # Hide the "Markup %:" label - it's the first widget in pricing row
        # We'll just hide the whole row's visible widgets

    def hide_export_controls(self) -> None:
        """Hide export buttons (moved to File menu)."""
        self.export_csv_btn.setVisible(False)
        self.export_qbo_btn.setVisible(False)
        self.upload_draft_btn.setVisible(False)
        self.undo_btn.setVisible(False)