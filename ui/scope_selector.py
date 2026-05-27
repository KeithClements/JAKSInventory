"""Scope selection widget for category/manufacturer filtering.

This widget manages the scope (category + manufacturer) selection that determines
which inventory file is loaded and where data is saved.
"""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QComboBox,
    QLabel,
    QPushButton,
    QFrame,
)

# Default categories and manufacturers (can be overridden)
DEFAULT_CATEGORIES = [
    "All",
    "Cylinder Head Components",
    "Engine Parts",
    "Engine Rebuild Kits",
    "Turbochargers",
]

DEFAULT_MANUFACTURERS = [
    "Caterpillar",
    "Cummins",
    "Detroit Diesel",
    "International",
    "Mack",
    "Navistar",
    "PACCAR",
    "Volvo",
]


class ScopeSelector(QWidget):
    """Widget for selecting category and manufacturer scope.
    
    Emits scope_changed when either dropdown changes.
    The parent should connect to this signal to load the appropriate data.
    """
    
    # Signals
    scope_changed = Signal(str, str)  # (category, manufacturer)
    refresh_requested = Signal()
    
    def __init__(
        self,
        parent=None,
        categories: list[str] | None = None,
        manufacturers: list[str] | None = None,
    ):
        super().__init__(parent)
        
        self._categories = categories or DEFAULT_CATEGORIES
        self._manufacturers = manufacturers or DEFAULT_MANUFACTURERS
        self._suppress_signals = False
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self) -> None:
        """Create and layout child widgets."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Category selector
        cat_label = QLabel("Category:")
        cat_label.setStyleSheet("font-weight: bold;")
        self.category_combo = QComboBox()
        self.category_combo.addItems(self._categories)
        self.category_combo.setMinimumWidth(180)
        self.category_combo.setToolTip("Select product category")
        
        # Manufacturer selector
        man_label = QLabel("Manufacturer:")
        man_label.setStyleSheet("font-weight: bold;")
        self.manufacturer_combo = QComboBox()
        self.manufacturer_combo.addItems(self._manufacturers)
        self.manufacturer_combo.setMinimumWidth(140)
        self.manufacturer_combo.setToolTip("Select manufacturer")
        
        # Product count display
        self.count_label = QLabel("—")
        self.count_label.setMinimumWidth(100)
        self.count_label.setStyleSheet("color: #666; font-style: italic;")
        
        # Refresh button
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setFixedWidth(30)
        self.refresh_btn.setToolTip("Refresh data (F5)")
        
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        
        # Layout - Manufacturer first (makes more sense for workflow)
        layout.addWidget(man_label)
        layout.addWidget(self.manufacturer_combo)
        layout.addSpacing(16)
        layout.addWidget(cat_label)
        layout.addWidget(self.category_combo)
        layout.addSpacing(16)
        layout.addWidget(separator)
        layout.addSpacing(8)
        layout.addWidget(self.count_label)
        layout.addWidget(self.refresh_btn)
        layout.addStretch(1)
    
    def _connect_signals(self) -> None:
        """Wire up internal signals."""
        self.category_combo.currentTextChanged.connect(self._on_combo_changed)
        self.manufacturer_combo.currentTextChanged.connect(self._on_combo_changed)
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
    
    def _on_combo_changed(self) -> None:
        """Handle combo box changes."""
        if self._suppress_signals:
            return
        category = self.category_combo.currentText()
        manufacturer = self.manufacturer_combo.currentText()
        self.scope_changed.emit(category, manufacturer)
    
    # Public API
    
    def get_scope(self) -> tuple[str, str]:
        """Return current (category, manufacturer) selection."""
        return (
            self.category_combo.currentText(),
            self.manufacturer_combo.currentText(),
        )
    
    def set_scope(self, category: str, manufacturer: str) -> None:
        """Set the scope without emitting signals."""
        self._suppress_signals = True
        try:
            idx = self.category_combo.findText(category)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
            
            idx = self.manufacturer_combo.findText(manufacturer)
            if idx >= 0:
                self.manufacturer_combo.setCurrentIndex(idx)
        finally:
            self._suppress_signals = False
    
    def update_count(self, count: int, visible: int | None = None) -> None:
        """Update the product count display."""
        if visible is not None and visible != count:
            self.count_label.setText(f"{visible} / {count} products")
        elif count > 0:
            self.count_label.setText(f"{count} products")
        else:
            self.count_label.setText("No products")
    
    def is_all_scope(self) -> bool:
        """Return True if either dropdown is set to 'All'."""
        cat, man = self.get_scope()
        return cat == "All" or man == "All"
    
    def get_category(self) -> str:
        """Return current category."""
        return self.category_combo.currentText()
    
    def get_manufacturer(self) -> str:
        """Return current manufacturer."""
        return self.manufacturer_combo.currentText()
