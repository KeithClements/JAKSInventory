"""QBO integration module for JAKS Inventory.

This module is the ONLY place that should write to QuickBooks Online.
The scraper app uses QBO read-only (for duplicate detection).

Modules:
- items.py: Create/update QBO Items from products
- bills.py: Create QBO Bills from received purchase orders
- invoices.py: Create QBO Invoices from sales
"""

from .items import (
    create_qbo_item,
    update_qbo_item,
    sync_product_to_qbo,
)

from .bills import (
    create_qbo_bill,
)

from .invoices import (
    create_qbo_invoice,
)

__all__ = [
    # Items
    "create_qbo_item",
    "update_qbo_item",
    "sync_product_to_qbo",
    # Bills
    "create_qbo_bill",
    # Invoices
    "create_qbo_invoice",
]
