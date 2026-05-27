"""Reusable UI widgets for JAKS applications.

This module provides common widgets used across the scraper and inventory apps:
- StatusBadge: Colored pill badges for status display
- PriceInput: Currency-formatted input with $ prefix
- StockIndicator: Visual stock level indicator
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ui.theme import STATUS_BADGE_COLORS, THEME_COLORS


class StatusBadge(QLabel):
    """A colored pill badge for displaying status.
    
    Usage:
        badge = StatusBadge("ready")  # Green "READY" badge
        badge = StatusBadge("error", text="Failed")  # Red "FAILED" badge
        badge.set_status("pending")  # Update to yellow
    """
    
    def __init__(
        self,
        status: str = "draft",
        text: str | None = None,
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._status = status
        self._custom_text = text
        self._update_style()
    
    def set_status(self, status: str, text: str | None = None) -> None:
        """Update the badge status and optionally the text."""
        self._status = status
        if text is not None:
            self._custom_text = text
        self._update_style()
    
    def _update_style(self) -> None:
        """Apply colors based on current status."""
        colors = STATUS_BADGE_COLORS.get(
            self._status.lower(), 
            STATUS_BADGE_COLORS["draft"]
        )
        
        # Display text: custom or uppercase status
        display_text = self._custom_text or self._status.upper()
        self.setText(display_text)
        
        self.setStyleSheet(f"""
            QLabel {{
                background-color: {colors['bg']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }}
        """)


class PriceInput(QFrame):
    """Currency input with $ prefix and proper formatting.
    
    Usage:
        price_input = PriceInput(initial_value=299.99)
        price_input.valueChanged.connect(lambda v: print(f"New price: ${v}"))
        current_price = price_input.value()
    """
    
    valueChanged = Signal(float)
    
    def __init__(
        self,
        initial_value: float = 0.0,
        prefix: str = "$",
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._prefix = prefix
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Currency prefix label
        self._prefix_label = QLabel(prefix)
        self._prefix_label.setStyleSheet(f"""
            QLabel {{
                background-color: {THEME_COLORS['header_bg']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-right: none;
                border-top-left-radius: 4px;
                border-bottom-left-radius: 4px;
                padding: 5px 8px;
                font-weight: bold;
            }}
        """)
        layout.addWidget(self._prefix_label)
        
        # Input field
        self._input = QLineEdit()
        self._input.setPlaceholderText("0.00")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {THEME_COLORS['field']};
                color: {THEME_COLORS['text']};
                border: 1px solid {THEME_COLORS['border']};
                border-left: none;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
                padding: 5px 8px;
                min-width: 80px;
            }}
            QLineEdit:focus {{
                border-color: {THEME_COLORS['primary']};
            }}
        """)
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input)
        
        # Set initial value
        if initial_value > 0:
            self._input.setText(f"{initial_value:.2f}")
    
    def value(self) -> float:
        """Get the current value as a float."""
        try:
            return float(self._input.text() or 0)
        except ValueError:
            return 0.0
    
    def set_value(self, value: float) -> None:
        """Set the value."""
        self._input.setText(f"{value:.2f}")
    
    def _on_text_changed(self, text: str) -> None:
        """Emit valueChanged signal when text changes."""
        try:
            value = float(text or 0)
            self.valueChanged.emit(value)
        except ValueError:
            pass


class StockIndicator(QFrame):
    """Visual indicator for stock levels with color coding.
    
    Usage:
        indicator = StockIndicator(qty=5, low_threshold=10)
        indicator.set_quantity(0)  # Shows red "Out of Stock"
    """
    
    def __init__(
        self,
        qty: int = 0,
        low_threshold: int = 5,
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._qty = qty
        self._low_threshold = low_threshold
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        
        # Quantity label
        self._qty_label = QLabel()
        layout.addWidget(self._qty_label)
        
        # Status label
        self._status_label = QLabel()
        layout.addWidget(self._status_label)
        
        self._update_display()
    
    def set_quantity(self, qty: int) -> None:
        """Update the displayed quantity."""
        self._qty = qty
        self._update_display()
    
    def set_low_threshold(self, threshold: int) -> None:
        """Update the low stock threshold."""
        self._low_threshold = threshold
        self._update_display()
    
    def _update_display(self) -> None:
        """Update colors and text based on quantity."""
        self._qty_label.setText(str(self._qty))
        
        if self._qty <= 0:
            # Out of stock - red
            colors = STATUS_BADGE_COLORS["out_of_stock"]
            status_text = "Out of Stock"
        elif self._qty <= self._low_threshold:
            # Low stock - yellow/orange
            colors = STATUS_BADGE_COLORS["low_stock"]
            status_text = "Low Stock"
        else:
            # In stock - green
            colors = STATUS_BADGE_COLORS["ready"]
            status_text = "In Stock"
        
        self._status_label.setText(status_text)
        
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {colors['bg']};
                border: 1px solid {colors['border']};
                border-radius: 4px;
            }}
            QLabel {{
                background: transparent;
                color: {colors['text']};
                font-size: 12px;
                font-weight: 500;
            }}
        """)


class MarginDisplay(QFrame):
    """Display margin percentage with visual indicator.
    
    Shows profit margin with color coding:
    - Red: < 15% (low margin)
    - Yellow: 15-25% (acceptable)
    - Green: > 25% (good margin)
    """
    
    def __init__(
        self,
        cost: float = 0.0,
        price: float = 0.0,
        parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._cost = cost
        self._price = price
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        
        # Margin percentage
        self._margin_label = QLabel("0%")
        self._margin_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self._margin_label)
        
        # Profit amount
        self._profit_label = QLabel("($0.00)")
        self._profit_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        layout.addWidget(self._profit_label)
        
        self._update_display()
    
    def set_values(self, cost: float, price: float) -> None:
        """Update cost and price values."""
        self._cost = cost
        self._price = price
        self._update_display()
    
    def _update_display(self) -> None:
        """Calculate and display margin."""
        if self._price > 0 and self._cost > 0:
            profit = self._price - self._cost
            margin = (profit / self._price) * 100
        else:
            profit = 0
            margin = 0
        
        self._margin_label.setText(f"{margin:.1f}%")
        self._profit_label.setText(f"(${profit:.2f})")
        
        # Color based on margin
        if margin < 15:
            colors = STATUS_BADGE_COLORS["error"]
        elif margin < 25:
            colors = STATUS_BADGE_COLORS["pending"]
        else:
            colors = STATUS_BADGE_COLORS["ready"]
        
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {colors['bg']};
                border: 1px solid {colors['border']};
                border-radius: 4px;
            }}
        """)
        self._margin_label.setStyleSheet(f"color: {colors['text']}; font-weight: bold; font-size: 14px; background: transparent;")
