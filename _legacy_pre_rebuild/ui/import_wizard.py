"""CSV/Excel Import Wizard for JAKS Scraper.

4-step wizard:
1. Select File + Vendor
2. Column Mapping
3. Cross-Reference Analysis
4. Import Options + Execute
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QWizard,
    QWizardPage,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QCheckBox,
    QProgressBar,
    QFrame,
    QScrollArea,
    QWidget,
    QHeaderView,
    QSpinBox,
    QDoubleSpinBox,
    QMessageBox,
)
from PySide6.QtGui import QFont

if TYPE_CHECKING:
    from sources.vendor_import import VendorImport, ImportAnalysis, ImportResult
    from db.vendor_service import Vendor

logger = logging.getLogger("ui.import_wizard")


class AnalysisWorker(QThread):
    """Background thread for cross-reference analysis."""
    progress = Signal(int, int)  # current, total
    finished = Signal(object)   # ImportAnalysis
    error = Signal(str)
    
    def __init__(self, importer: "VendorImport"):
        super().__init__()
        self.importer = importer
        
    def run(self):
        try:
            analysis = self.importer.analyze_cross_references(
                progress_callback=lambda c, t: self.progress.emit(c, t)
            )
            self.finished.emit(analysis)
        except Exception as e:
            self.error.emit(str(e))


class ImportWorker(QThread):
    """Background thread for actual import."""
    progress = Signal(int, int, str)  # current, total, message
    finished = Signal(object)         # ImportResult
    error = Signal(str)
    
    def __init__(self, importer: "VendorImport", options: dict):
        super().__init__()
        self.importer = importer
        self.options = options
        
    def run(self):
        try:
            result = self.importer.import_products(
                progress_callback=lambda c, t, m: self.progress.emit(c, t, m),
                **self.options
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class FileSelectionPage(QWizardPage):
    """Step 1: Select file and vendor."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Step 1: Select File")
        self.setSubTitle("Choose a CSV or Excel file to import and select the vendor.")
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # File selection
        file_group = QGroupBox("📁 Import File")
        file_layout = QHBoxLayout(file_group)
        
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Select a CSV or Excel file...")
        self.file_edit.setReadOnly(True)
        file_layout.addWidget(self.file_edit, 1)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)
        
        layout.addWidget(file_group)
        
        # Vendor selection
        vendor_group = QGroupBox("🏭 Vendor")
        vendor_layout = QHBoxLayout(vendor_group)
        
        vendor_layout.addWidget(QLabel("Select Vendor:"))
        
        self.vendor_combo = QComboBox()
        self.vendor_combo.setMinimumWidth(200)
        vendor_layout.addWidget(self.vendor_combo, 1)
        
        new_vendor_btn = QPushButton("+ New Vendor")
        new_vendor_btn.clicked.connect(self._add_new_vendor)
        vendor_layout.addWidget(new_vendor_btn)
        
        layout.addWidget(vendor_group)
        
        # Info
        info_label = QLabel("""
            <p><b>Supported formats:</b> .csv, .xlsx, .xls</p>
            <p>The wizard will analyze your file and help you map columns to product fields.</p>
        """)
        info_label.setStyleSheet("color: #7f8c8d;")
        layout.addWidget(info_label)
        
        layout.addStretch()
        
        # Register fields for wizard
        self.registerField("filepath*", self.file_edit)
        self.registerField("vendor_index", self.vendor_combo, "currentIndex")
        
    def _browse_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Import File",
            "",
            "Spreadsheet Files (*.csv *.xlsx *.xls);;CSV Files (*.csv);;Excel Files (*.xlsx *.xls);;All Files (*)",
        )
        if filepath:
            self.file_edit.setText(filepath)
            self.completeChanged.emit()
            
    def _add_new_vendor(self):
        # TODO: Open vendor creation dialog
        QMessageBox.information(
            self,
            "New Vendor",
            "Vendor creation dialog coming soon.\n\nFor now, add vendors through the Settings tab.",
        )
        
    def initializePage(self):
        """Called when page is shown."""
        # Load vendors from database
        wizard = self.wizard()
        if hasattr(wizard, 'db_connection') and wizard.db_connection:
            from db.vendor_service import VendorService
            service = VendorService(wizard.db_connection)
            service.ensure_defaults()
            vendors = service.get_all()
            
            self.vendor_combo.clear()
            for vendor in vendors:
                self.vendor_combo.addItem(f"{vendor.code} - {vendor.name}", vendor)
                
    def isComplete(self) -> bool:
        return bool(self.file_edit.text()) and self.vendor_combo.currentIndex() >= 0
        
    def get_filepath(self) -> str:
        return self.file_edit.text()
        
    def get_vendor(self) -> "Vendor | None":
        return self.vendor_combo.currentData()


class ColumnMappingPage(QWizardPage):
    """Step 2: Map CSV columns to product fields."""
    
    PRODUCT_FIELDS = [
        ("", "-- Skip --"),
        ("vendor_part_number", "Vendor Part Number *"),
        ("title", "Title/Description *"),
        ("cost", "Cost/Price"),
        ("oem_part_number", "OEM Part Number"),
        ("category", "Category"),
        ("engine", "Engine"),
        ("brand", "Brand/Manufacturer"),
        ("weight", "Weight"),
        ("dimensions", "Dimensions"),
        ("cross_ref_1", "Cross Reference 1"),
        ("cross_ref_2", "Cross Reference 2"),
        ("cross_ref_3", "Cross Reference 3"),
        ("cpl", "CPL Codes"),
        ("esn", "ESN Codes"),
        ("quantity", "Quantity/Stock"),
        ("description", "Long Description"),
    ]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Step 2: Column Mapping")
        self.setSubTitle("Map your file's columns to product fields.")
        
        self.combos: dict[str, QComboBox] = {}
        self.importer = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Preview table
        preview_group = QGroupBox("📋 Data Preview (first 5 rows)")
        preview_layout = QVBoxLayout(preview_group)
        
        self.preview_table = QTableWidget()
        self.preview_table.setMaximumHeight(200)
        preview_layout.addWidget(self.preview_table)
        
        layout.addWidget(preview_group)
        
        # Column mapping
        mapping_group = QGroupBox("🔗 Column Mapping")
        mapping_scroll = QScrollArea()
        mapping_scroll.setWidgetResizable(True)
        mapping_scroll.setMaximumHeight(300)
        
        self.mapping_widget = QWidget()
        self.mapping_layout = QVBoxLayout(self.mapping_widget)
        
        mapping_scroll.setWidget(self.mapping_widget)
        
        mapping_group_layout = QVBoxLayout(mapping_group)
        
        auto_btn = QPushButton("🔮 Auto-Detect Columns")
        auto_btn.clicked.connect(self._auto_detect)
        mapping_group_layout.addWidget(auto_btn)
        
        mapping_group_layout.addWidget(mapping_scroll)
        
        layout.addWidget(mapping_group, 1)
        
        # Save template option
        template_layout = QHBoxLayout()
        self.save_template_check = QCheckBox("Save mapping as template for this vendor")
        template_layout.addWidget(self.save_template_check)
        template_layout.addStretch()
        layout.addLayout(template_layout)
        
    def initializePage(self):
        """Called when page is shown - load file and build mapping UI."""
        wizard = self.wizard()
        file_page: FileSelectionPage = wizard.page(0)
        
        filepath = file_page.get_filepath()
        vendor = file_page.get_vendor()
        
        if not filepath or not vendor:
            return
            
        # Create importer
        from sources.vendor_import import VendorImport
        self.importer = VendorImport(vendor.code, wizard.db_connection)
        
        try:
            self.importer.load_file(filepath)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file:\n{e}")
            return
            
        # Build preview table
        self._build_preview_table()
        
        # Build mapping UI
        self._build_mapping_ui()
        
        # Try to load saved template
        if vendor.import_template:
            self._apply_template(vendor.import_template)
        else:
            # Auto-detect
            self._auto_detect()
            
    def _build_preview_table(self):
        """Build the preview table with data."""
        if not self.importer or not self.importer.data:
            return
            
        headers = self.importer.headers
        rows = self.importer.data[:5]
        
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setRowCount(len(rows))
        self.preview_table.setHorizontalHeaderLabels(headers)
        
        for i, row in enumerate(rows):
            for j, header in enumerate(headers):
                value = row.get(header, "")
                item = QTableWidgetItem(str(value)[:50])  # Truncate long values
                self.preview_table.setItem(i, j, item)
                
        self.preview_table.resizeColumnsToContents()
        
    def _build_mapping_ui(self):
        """Build the column mapping combo boxes."""
        # Clear existing
        while self.mapping_layout.count():
            child = self.mapping_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
                
        self.combos.clear()
        
        if not self.importer:
            return
            
        for header in self.importer.headers:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 2, 0, 2)
            
            label = QLabel(header)
            label.setMinimumWidth(150)
            row_layout.addWidget(label)
            
            row_layout.addWidget(QLabel("→"))
            
            combo = QComboBox()
            combo.setMinimumWidth(200)
            for field, display in self.PRODUCT_FIELDS:
                combo.addItem(display, field)
            row_layout.addWidget(combo, 1)
            
            self.combos[header] = combo
            self.mapping_layout.addWidget(row_widget)
            
        self.mapping_layout.addStretch()
        
    def _auto_detect(self):
        """Auto-detect column mappings."""
        if not self.importer:
            return
            
        mapping = self.importer.auto_detect_mapping()
        self._apply_template(mapping)
        
    def _apply_template(self, template: dict[str, str]):
        """Apply a mapping template."""
        for header, field in template.items():
            if header in self.combos:
                combo = self.combos[header]
                # Find the field in combo
                for i in range(combo.count()):
                    if combo.itemData(i) == field:
                        combo.setCurrentIndex(i)
                        break
                        
    def get_mapping(self) -> dict[str, str]:
        """Get the current mapping."""
        mapping = {}
        for header, combo in self.combos.items():
            field = combo.currentData()
            if field:  # Skip empty mappings
                mapping[header] = field
        return mapping
        
    def validatePage(self) -> bool:
        """Validate mapping before proceeding."""
        mapping = self.get_mapping()
        
        # Check required fields
        required = {'vendor_part_number', 'title'}
        mapped_fields = set(mapping.values())
        
        missing = required - mapped_fields
        if missing:
            QMessageBox.warning(
                self,
                "Missing Required Fields",
                f"Please map the following required fields:\n• " + "\n• ".join(missing),
            )
            return False
            
        # Save mapping to importer
        if self.importer:
            self.importer.set_mapping(mapping)
            
        # Optionally save template
        if self.save_template_check.isChecked():
            wizard = self.wizard()
            file_page: FileSelectionPage = wizard.page(0)
            vendor = file_page.get_vendor()
            
            if vendor and wizard.db_connection:
                from db.vendor_service import VendorService
                service = VendorService(wizard.db_connection)
                service.save_import_template(vendor.code, mapping)
                
        return True


class CrossRefAnalysisPage(QWizardPage):
    """Step 3: Analyze cross-references against existing database."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Step 3: Cross-Reference Analysis")
        self.setSubTitle("Analyzing products against existing database...")
        
        self.analysis: "ImportAnalysis | None" = None
        self.worker: AnalysisWorker | None = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Progress
        self.progress_label = QLabel("Analyzing products...")
        layout.addWidget(self.progress_label)
        
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        
        # Results
        self.results_widget = QWidget()
        results_layout = QVBoxLayout(self.results_widget)
        self.results_widget.hide()
        
        # Summary cards
        cards_layout = QHBoxLayout()
        
        self.exact_card = self._create_card("✅ Exact Matches", "0", "#27ae60")
        cards_layout.addWidget(self.exact_card)
        
        self.xref_card = self._create_card("🔀 Cross-References", "0", "#3498db")
        cards_layout.addWidget(self.xref_card)
        
        self.dup_card = self._create_card("⚠️ Potential Duplicates", "0", "#f39c12")
        cards_layout.addWidget(self.dup_card)
        
        self.new_card = self._create_card("🆕 New Products", "0", "#9b59b6")
        cards_layout.addWidget(self.new_card)
        
        results_layout.addLayout(cards_layout)
        
        # Details
        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        results_layout.addWidget(self.details_label)
        
        layout.addWidget(self.results_widget)
        layout.addStretch()
        
    def _create_card(self, title: str, value: str, color: str) -> QFrame:
        """Create a summary card."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 8px;
                padding: 12px;
            }}
            QLabel {{
                color: white;
            }}
        """)
        
        layout = QVBoxLayout(card)
        
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(title_label)
        
        value_label = QLabel(value)
        value_label.setObjectName("value")
        value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(value_label)
        
        return card
        
    def _update_card(self, card: QFrame, value: int):
        """Update a card's value."""
        label = card.findChild(QLabel, "value")
        if label:
            label.setText(str(value))
            
    def initializePage(self):
        """Start analysis when page is shown."""
        wizard = self.wizard()
        mapping_page: ColumnMappingPage = wizard.page(1)
        
        importer = mapping_page.importer
        if not importer:
            return
            
        # Reset UI
        self.progress_bar.setValue(0)
        self.progress_label.setText("Analyzing products...")
        self.results_widget.hide()
        
        # Start worker
        self.worker = AnalysisWorker(importer)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        
    def _on_progress(self, current: int, total: int):
        """Update progress bar."""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.progress_label.setText(f"Analyzing products... {current}/{total}")
        
    def _on_finished(self, analysis: "ImportAnalysis"):
        """Handle analysis completion."""
        self.analysis = analysis
        
        # Update cards
        self._update_card(self.exact_card, analysis.exact_match_count)
        self._update_card(self.xref_card, analysis.cross_ref_count)
        self._update_card(self.dup_card, analysis.duplicate_count)
        self._update_card(self.new_card, analysis.new_count)
        
        # Details
        total = analysis.total_rows
        self.details_label.setText(f"""
            <p><b>Analysis complete!</b></p>
            <p>Out of {total} products in your file:</p>
            <ul>
                <li><b>{analysis.exact_match_count}</b> match existing products exactly (same part number)</li>
                <li><b>{analysis.cross_ref_count}</b> match via cross-reference (different vendor's part number)</li>
                <li><b>{analysis.duplicate_count}</b> may be duplicates (similar title/category)</li>
                <li><b>{analysis.new_count}</b> are new products not in the database</li>
            </ul>
            <p>In the next step, you'll choose how to handle each category.</p>
        """)
        
        self.progress_label.setText("✅ Analysis complete!")
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.results_widget.show()
        
        self.completeChanged.emit()
        
    def _on_error(self, message: str):
        """Handle analysis error."""
        self.progress_label.setText(f"❌ Error: {message}")
        QMessageBox.critical(self, "Analysis Error", message)
        
    def isComplete(self) -> bool:
        return self.analysis is not None


class ImportOptionsPage(QWizardPage):
    """Step 4: Import options and execution."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Step 4: Import Options")
        self.setSubTitle("Configure how to handle different product categories and start the import.")
        
        self.import_result: "ImportResult | None" = None
        self.worker: ImportWorker | None = None
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Exact matches options
        exact_group = QGroupBox("✅ Exact Matches")
        exact_layout = QVBoxLayout(exact_group)
        
        self.exact_group = QButtonGroup(self)
        
        self.exact_skip = QRadioButton("Skip (don't update)")
        self.exact_group.addButton(self.exact_skip)
        exact_layout.addWidget(self.exact_skip)
        
        self.exact_update = QRadioButton("Update cost/pricing only")
        self.exact_update.setChecked(True)
        self.exact_group.addButton(self.exact_update)
        exact_layout.addWidget(self.exact_update)
        
        self.exact_alternate = QRadioButton("Create as vendor alternate")
        self.exact_group.addButton(self.exact_alternate)
        exact_layout.addWidget(self.exact_alternate)
        
        layout.addWidget(exact_group)
        
        # Cross-reference options
        xref_group = QGroupBox("🔀 Cross-References")
        xref_layout = QVBoxLayout(xref_group)
        
        self.xref_group = QButtonGroup(self)
        
        self.xref_skip = QRadioButton("Skip")
        self.xref_group.addButton(self.xref_skip)
        xref_layout.addWidget(self.xref_skip)
        
        self.xref_link = QRadioButton("Link to existing product (add as cross-reference)")
        self.xref_link.setChecked(True)
        self.xref_group.addButton(self.xref_link)
        xref_layout.addWidget(self.xref_link)
        
        self.xref_separate = QRadioButton("Create as separate product")
        self.xref_group.addButton(self.xref_separate)
        xref_layout.addWidget(self.xref_separate)
        
        layout.addWidget(xref_group)
        
        # New products options
        new_group = QGroupBox("🆕 New Products")
        new_layout = QVBoxLayout(new_group)
        
        self.create_new_check = QCheckBox("Create in database")
        self.create_new_check.setChecked(True)
        new_layout.addWidget(self.create_new_check)
        
        # Category
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("Default Category:"))
        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems([
            "", "engine_parts", "engine_rebuild_kits", "cylinder_head_components",
            "cooling_system", "fuel_system", "turbochargers", "injectors",
        ])
        cat_layout.addWidget(self.category_combo, 1)
        new_layout.addLayout(cat_layout)
        
        # Markup
        markup_layout = QHBoxLayout()
        markup_layout.addWidget(QLabel("Default Markup:"))
        self.markup_spin = QDoubleSpinBox()
        self.markup_spin.setRange(0, 200)
        self.markup_spin.setValue(25.0)
        self.markup_spin.setSuffix(" %")
        markup_layout.addWidget(self.markup_spin)
        markup_layout.addStretch()
        new_layout.addLayout(markup_layout)
        
        layout.addWidget(new_group)
        
        # Progress section (hidden until import starts)
        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        
        self.import_progress_label = QLabel("Ready to import")
        progress_layout.addWidget(self.import_progress_label)
        
        self.import_progress_bar = QProgressBar()
        progress_layout.addWidget(self.import_progress_bar)
        
        self.progress_widget.hide()
        layout.addWidget(self.progress_widget)
        
        # Results section (hidden until import completes)
        self.results_widget = QWidget()
        results_layout = QVBoxLayout(self.results_widget)
        
        self.results_label = QLabel()
        self.results_label.setWordWrap(True)
        results_layout.addWidget(self.results_label)
        
        self.results_widget.hide()
        layout.addWidget(self.results_widget)
        
        layout.addStretch()
        
    def initializePage(self):
        """Set up page when shown."""
        wizard = self.wizard()
        file_page: FileSelectionPage = wizard.page(0)
        vendor = file_page.get_vendor()
        
        if vendor:
            self.markup_spin.setValue(vendor.default_markup)
            
    def get_options(self) -> dict:
        """Get import options."""
        # Exact match action
        if self.exact_skip.isChecked():
            on_exact = "skip"
        elif self.exact_update.isChecked():
            on_exact = "update"
        else:
            on_exact = "alternate"
            
        # Cross-ref action
        if self.xref_skip.isChecked():
            on_xref = "skip"
        elif self.xref_link.isChecked():
            on_xref = "link"
        else:
            on_xref = "separate"
            
        return {
            "on_exact_match": on_exact,
            "on_cross_ref": on_xref,
            "on_potential_dup": "link",  # Default to link for potential dupes
            "category": self.category_combo.currentText(),
            "default_markup": self.markup_spin.value(),
        }
        
    def validatePage(self) -> bool:
        """Start import when user clicks Finish."""
        if self.import_result is not None:
            # Already imported
            return True
            
        # Get importer
        wizard = self.wizard()
        mapping_page: ColumnMappingPage = wizard.page(1)
        importer = mapping_page.importer
        
        if not importer:
            return False
            
        # Show progress
        self.progress_widget.show()
        self.import_progress_bar.setValue(0)
        self.import_progress_label.setText("Starting import...")
        
        # Start worker
        options = self.get_options()
        self.worker = ImportWorker(importer, options)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        
        return False  # Don't close yet, wait for import
        
    def _on_progress(self, current: int, total: int, message: str):
        """Update progress."""
        self.import_progress_bar.setMaximum(total)
        self.import_progress_bar.setValue(current)
        self.import_progress_label.setText(message)
        
    def _on_finished(self, result: "ImportResult"):
        """Handle import completion."""
        self.import_result = result
        
        self.import_progress_label.setText("✅ Import complete!")
        self.import_progress_bar.setValue(self.import_progress_bar.maximum())
        
        # Show results
        self.results_label.setText(f"""
            <h3>✅ Import Complete!</h3>
            <ul>
                <li><b>{result.imported}</b> new products created</li>
                <li><b>{result.linked}</b> products linked to existing</li>
                <li><b>{result.updated}</b> products updated</li>
                <li><b>{result.skipped}</b> products skipped</li>
            </ul>
            {f"<p style='color: #e74c3c;'><b>Errors:</b> {len(result.errors)}</p>" if result.errors else ""}
        """)
        self.results_widget.show()
        
        # Allow wizard to close
        self.completeChanged.emit()
        
    def _on_error(self, message: str):
        """Handle import error."""
        self.import_progress_label.setText(f"❌ Error: {message}")
        QMessageBox.critical(self, "Import Error", message)
        
    def isComplete(self) -> bool:
        return self.import_result is not None


class ImportWizard(QWizard):
    """4-step CSV/Excel import wizard."""
    
    import_complete = Signal(object)  # ImportResult
    
    def __init__(self, parent=None, db_connection=None):
        super().__init__(parent)
        self.db_connection = db_connection
        
        self.setWindowTitle("📁 Import Vendor Products")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(700, 600)
        
        # Add pages
        self.addPage(FileSelectionPage(self))
        self.addPage(ColumnMappingPage(self))
        self.addPage(CrossRefAnalysisPage(self))
        self.addPage(ImportOptionsPage(self))
        
        # Custom button text
        self.setButtonText(QWizard.WizardButton.NextButton, "Next →")
        self.setButtonText(QWizard.WizardButton.BackButton, "← Back")
        self.setButtonText(QWizard.WizardButton.FinishButton, "🚀 Import")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        
    def accept(self):
        """Handle wizard completion."""
        options_page: ImportOptionsPage = self.page(3)
        if options_page.import_result:
            self.import_complete.emit(options_page.import_result)
        super().accept()
