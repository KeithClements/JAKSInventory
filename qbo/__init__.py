"""QuickBooks Online integration module.

Provides:
- QBO API client for reading/writing inventory items
- Duplicate detection against existing QBO inventory
- Sync logic for bidirectional updates
- Customer sync + crosswalk reconciliation (QBO.8)
- Centralized mock/write toggle (QBO.9)
- Core-charge line mapping (QBO.10)
- Idempotency keys + retry/backoff (QBO.11)
"""

from .client import QBOClient, get_qbo_client
from .sync import (
    build_qbo_duplicate_report,
    normalize_sku_for_matching,
)
from .health import get_qbo_health
from . import config  # noqa: F401 — central toggle
from .customers import (
    sync_customer_to_qbo,
    sync_all_customers,
    reconcile_customer_crosswalk,
    fetch_qbo_customers,
)
from .core_charges import (
    CORE_CHARGE_SKU,
    VENDOR_CORE_SKU,
    build_core_charge_line,
    expand_invoice_lines_with_cores,
    get_core_charge_item_ref,
    build_vendor_core_bill_line,
    expand_bill_lines_with_cores,
    get_vendor_core_item_ref,
)
from ._retry import (
    idempotent_write,
    make_idempotency_key,
    retry_with_backoff,
    QBOTransientError,
    QBOPermanentError,
)
from . import sync_status  # noqa: F401 — P8 sync status helpers

__all__ = [
    "QBOClient",
    "get_qbo_client",
    "build_qbo_duplicate_report",
    "normalize_sku_for_matching",
    "config",
    # QBO.8
    "sync_customer_to_qbo",
    "sync_all_customers",
    "reconcile_customer_crosswalk",
    "fetch_qbo_customers",
    # QBO.10
    "CORE_CHARGE_SKU",
    "VENDOR_CORE_SKU",
    "build_vendor_core_bill_line",
    "expand_bill_lines_with_cores",
    "get_vendor_core_item_ref",
    "build_core_charge_line",
    "expand_invoice_lines_with_cores",
    "get_core_charge_item_ref",
    # QBO.11
    "idempotent_write",
    "make_idempotency_key",
    "retry_with_backoff",
    "QBOTransientError",
    "QBOPermanentError",
    # D5
    "get_qbo_health",
    # P8
    "sync_status",
]
