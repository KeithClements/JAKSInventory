"""UI widgets subpackage for JAKS Scraper.

This package contains specialized widgets:
- ProductFamilyPanel: Dialog for viewing product families and variants
- FamilyGroupingWidget: Toggle for grouping by family
"""

from ui.widgets.product_family_panel import (
    ProductFamilyPanel,
    FamilyGroupingWidget,
    FamilyColumnDelegate,
    VARIANT_COLORS,
    show_family_for_product,
)

# Re-export from parent widgets.py for backward compat
from ui.widgets_core import (
    StatusBadge,
    PriceInput,
    StockIndicator,
    MarginDisplay,
)

__all__ = [
    # Product family
    "ProductFamilyPanel",
    "FamilyGroupingWidget",
    "FamilyColumnDelegate",
    "VARIANT_COLORS",
    "show_family_for_product",
    # Legacy widgets
    "StatusBadge",
    "PriceInput",
    "StockIndicator",
    "MarginDisplay",
]
