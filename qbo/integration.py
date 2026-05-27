"""QuickBooks Online Integration Service.

Provides high-level QBO operations for JAKS Scraper:
- Item sync (create/update inventory items)
- Bill creation (for PAI purchases)
- Invoice creation (for sales)
- Account mapping

Environment Variables:
    QBO_CLIENT_ID: Intuit app client ID
    QBO_CLIENT_SECRET: Intuit app client secret  
    QBO_REALM_ID: Company ID (from OAuth callback)
    QBO_ENVIRONMENT: 'sandbox' or 'production'
    QBO_WRITE_ENABLED: Set to '1' to enable write operations
"""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Any

from qbo.client import get_qbo_client, QBOClient

logger = logging.getLogger(__name__)


def _is_write_enabled() -> bool:
    """Check if QBO writes are enabled (checked dynamically).

    QBO.9 — delegates to the centralized :mod:`qbo.config` toggle so the
    Settings UI (DB-backed) and env vars agree on a single source of truth.
    """
    from qbo.config import is_write_enabled
    return is_write_enabled()


# Cached account mappings
_account_cache: dict[str, dict] = {}


@dataclass
class QBOItemData:
    """Data for creating/updating a QBO inventory item."""
    sku: str
    name: str
    price: float
    cost: float = 0.0
    description: str = ""
    qty_on_hand: float = 0.0

    # 'Inventory' or 'Service' (mapped from local product_type)
    item_type: str = "Inventory"

    # Cross-reference data (stored in description)
    oem_part_number: Optional[str] = None
    pai_part_number: Optional[str] = None
    manufacturer: Optional[str] = None

    # Extended descriptive metadata (appended to description so QBO users
    # can see condition / category / core-charge info without a custom
    # field). Local DB remains the source of truth.
    category: Optional[str] = None
    condition: Optional[str] = None
    core_charge: Optional[float] = None

    # Local product PK + previously-stamped QBO id. Carrying these
    # through lets ``sync_item`` update the existing QBO item even
    # when the SKU has been edited locally (otherwise the SKU lookup
    # misses and we create a duplicate in QBO).
    local_id: Optional[int] = None
    qbo_id: Optional[str] = None


@dataclass
class QBOBillLine:
    """A line item for a QBO bill (purchase)."""
    sku: str
    description: str
    quantity: float
    unit_cost: float
    # Tier 3 — optional QBO TaxCode reference for this line.
    qbo_tax_code: Optional[str] = None
    
    @property
    def amount(self) -> float:
        return self.quantity * self.unit_cost


@dataclass
class QBOBillData:
    """Data for creating a QBO bill (accounts payable)."""
    vendor_name: str  # e.g., "PAI Industries"
    ref_number: str   # PO number
    bill_date: date
    due_date: Optional[date] = None
    lines: list[QBOBillLine] = field(default_factory=list)
    memo: str = ""
    # Tier 3 — ISO 4217 currency code (USD/CAD/EUR/...). Empty = home currency.
    currency_code: Optional[str] = None
    
    @property
    def total(self) -> float:
        return sum(line.amount for line in self.lines)


@dataclass
class QBOInvoiceLine:
    """A line item for a QBO invoice (sale)."""
    sku: str
    description: str
    quantity: float
    unit_price: float
    # Tier 3 — optional QBO TaxCode reference for this line. When unset,
    # falls back to the document-level default (qbo_default_tax_code
    # setting), then to ``NON``.
    qbo_tax_code: Optional[str] = None
    
    @property
    def amount(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class QBOInvoiceData:
    """Data for creating a QBO invoice (accounts receivable)."""
    customer_name: str
    invoice_number: str
    invoice_date: date
    due_date: Optional[date] = None
    lines: list[QBOInvoiceLine] = field(default_factory=list)
    memo: str = ""
    # Tier 3 — document-level overrides; ``None`` means use settings default.
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None
    
    @property
    def total(self) -> float:
        return sum(line.amount for line in self.lines)


@dataclass
class QBOPurchaseOrderLine:
    """A line item for a QBO PurchaseOrder."""
    sku: str
    description: str
    quantity: float
    unit_cost: float
    qbo_tax_code: Optional[str] = None

    @property
    def amount(self) -> float:
        return self.quantity * self.unit_cost


@dataclass
class QBOPurchaseOrderData:
    """Data for creating a QBO PurchaseOrder (Phase 8).

    A PurchaseOrder is the *pre-receipt* commitment to a vendor —
    separate from the eventual Bill (post-receipt accrual). Once the
    PO is received and a Bill is generated, pass the
    ``qbo_po_id`` to :meth:`QBOIntegrationService.create_bill` via
    ``linked_po_qbo_id`` so QBO links the documents.
    """
    vendor_name: str
    po_number: str
    po_date: date
    due_date: Optional[date] = None
    lines: list[QBOPurchaseOrderLine] = field(default_factory=list)
    memo: str = ""
    ship_to: str = ""
    vendor_message: str = ""
    # ``Open`` (default) or ``Closed`` — used when a PO has been fully
    # received and we want QBO to close it without going through Bill.
    po_status: str = "Open"
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None

    @property
    def total(self) -> float:
        return sum(line.amount for line in self.lines)


@dataclass
class QBOEstimateLine:
    """A line item for a QBO estimate (quote)."""
    sku: str
    description: str
    quantity: float
    unit_price: float
    qbo_tax_code: Optional[str] = None

    @property
    def amount(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class QBOEstimateData:
    """Data for creating a QBO Estimate (quote).

    Maps 1:1 to local ``quotes`` rows. Once the local quote is converted
    to an invoice, pass the resulting ``qbo_estimate_id`` to
    :meth:`QBOIntegrationService.create_invoice` via
    ``linked_estimate_qbo_id`` so QBO links the documents.
    """
    customer_name: str
    estimate_number: str
    estimate_date: date
    expiration_date: Optional[date] = None
    lines: list[QBOEstimateLine] = field(default_factory=list)
    memo: str = ""
    # One of ``Pending`` / ``Accepted`` / ``Closed`` / ``Rejected``.
    txn_status: str = "Pending"
    accepted_by: str = ""
    accepted_date: Optional[date] = None
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None

    @property
    def total(self) -> float:
        return sum(line.amount for line in self.lines)


@dataclass
class QBOPaymentData:
    """Data for creating a QBO ReceivePayment (D4).

    Represents a customer payment applied against a single invoice. The
    invoice must already exist in QBO (push it first); ``invoice_qbo_id``
    is the ``Id`` returned by ``create_invoice``.
    """
    customer_name: str
    invoice_number: str        # local invoice number (used as DocNumber)
    invoice_qbo_id: Optional[str]  # QBO invoice Id to apply against
    payment_date: date
    amount: float
    payment_method: str = ""
    memo: str = ""
    ref_number: str = ""       # optional check / txn ref


@dataclass
class QBOCreditMemoData:
    """Data for creating a QBO CreditMemo (D4).

    A credit memo represents money owed back to the customer (core
    return, manual credit, RMA). Single-line by design — credits in
    JAKS are flat-amount records, not multi-line documents.
    """
    customer_name: str
    credit_number: str         # CM-<id>
    issue_date: date
    amount: float
    sku: str = "CREDIT"        # item SKU stamped on the line
    description: str = "Customer credit"
    memo: str = ""
    # Tier 3
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None


@dataclass
class QBOSalesReceiptData:
    """Data for creating a QBO SalesReceipt (cash sale).

    A SalesReceipt represents an invoice + payment in one document —
    used when payment is received at point of sale. Unlike the
    invoice → payment flow it never touches A/R; cash lands directly
    in ``deposit_to_account_id`` (an Undeposited Funds or bank account).
    """
    customer_name: str
    receipt_number: str
    receipt_date: date
    lines: list[QBOInvoiceLine] = field(default_factory=list)
    payment_method: str = ""           # 'Cash', 'Check', 'Credit Card', ...
    deposit_to_account_id: Optional[str] = None  # falls back to undeposited
    ref_number: str = ""
    memo: str = ""
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None

    @property
    def total(self) -> float:
        return sum(line.amount for line in self.lines)


@dataclass
class QBORefundReceiptData:
    """Data for creating a QBO RefundReceipt (cash refund).

    A RefundReceipt is the cash counterpart to a CreditMemo — money
    actually leaves the bank to the customer (vs. a credit that just
    sits on file). Single-line by design, like CreditMemoData.
    """
    customer_name: str
    refund_number: str         # RR-<id>
    refund_date: date
    amount: float
    sku: str = "REFUND"
    description: str = "Customer refund"
    payment_method: str = ""               # 'Cash', 'Check', etc.
    deposit_from_account_id: Optional[str] = None
    ref_number: str = ""
    memo: str = ""
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None


@dataclass
class QBOBillPaymentData:
    """Data for creating a QBO BillPayment.

    Pays one or more Bills. ``bill_qbo_ids`` are the QBO ids of the
    Bills being paid (we resolve them from local POs via
    ``purchase_orders.qbo_bill_id``). Amount is the total disbursed
    across all linked bills.
    """
    vendor_name: str
    payment_number: str        # BP-<id>
    payment_date: date
    amount: float
    bill_qbo_ids: list[str] = field(default_factory=list)
    pay_type: str = "Check"     # 'Check' or 'CreditCard'
    bank_account_id: Optional[str] = None
    ref_number: str = ""
    memo: str = ""


@dataclass
class QBOVendorCreditData:
    """Data for creating a QBO VendorCredit (P8 — vendor core return).

    A VendorCredit represents money the vendor owes JAK's back. Used
    when JAK's ships an old/rebuildable core back to the supplier and
    the supplier credits the previously-paid core deposit.

    Single-line by design: each ``vendor_core_returns`` row maps to one
    VendorCredit posting against the ``VENDOR-CORE-CHARGE`` service
    item (which is mapped in QBO to the Vendor Core Receivable asset
    account configured via ``qbo.account.vendor_core_receivable``).
    """
    vendor_name: str
    credit_number: str          # VC-<id>
    txn_date: date
    amount: float
    sku: str = "VENDOR-CORE-CHARGE"
    description: str = "Vendor core credit"
    quantity: float = 1
    linked_bill_qbo_id: Optional[str] = None
    memo: str = ""
    qbo_tax_code: Optional[str] = None
    currency_code: Optional[str] = None


@dataclass
class QBOSyncResult:
    """Result of a QBO sync operation."""
    success: bool
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)


class QBOIntegrationService:
    """High-level service for QBO operations."""
    
    def __init__(self):
        self._client: Optional[QBOClient] = None
        self._vendors: dict[str, dict] = {}
        self._customers: dict[str, dict] = {}
        self._accounts: dict[str, dict] = {}
    
    @property
    def client(self) -> QBOClient:
        """Get or create QBO client."""
        if self._client is None:
            self._client = get_qbo_client()
        return self._client
    
    @property
    def is_connected(self) -> bool:
        return self.client.is_connected
    
    @property
    def is_mock_mode(self) -> bool:
        return self.client.is_mock_mode
    
    @property
    def write_enabled(self) -> bool:
        return _is_write_enabled()
    
    def connect(self) -> bool:
        """Connect to QBO."""
        return self.client.connect()

    # =========================================================================
    # Audit logging helpers (writes go through qbo.sync_log)
    # =========================================================================
    def _audit_start(
        self,
        *,
        source_type: str,
        source_id: int | None,
        qbo_object_type: str,
        request_payload: dict | None,
    ) -> int | None:
        from qbo import sync_log
        return sync_log.start_log(
            source_type=source_type,
            source_id=source_id,
            qbo_object_type=qbo_object_type,
            request_json=sync_log.to_json_text(request_payload or {}),
        )

    def _audit_success(self, log_id: int | None, saved_obj) -> None:
        from qbo import sync_log
        sync_log.update_request(
            log_id,
            request_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(saved_obj))
            if saved_obj is not None else "",
        )
        sync_log.complete_log(
            log_id,
            status="success",
            response_status=200,
            response_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(saved_obj)),
            qbo_object_id=str(getattr(saved_obj, "Id", "") or ""),
        )

    def _audit_mock(self, log_id: int | None, qbo_id: str) -> None:
        from qbo import sync_log
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": qbo_id, "_mock": True}),
            qbo_object_id=qbo_id,
        )

    def _audit_skipped(self, log_id: int | None, reason: str) -> None:
        from qbo import sync_log
        sync_log.complete_log(log_id, status="skipped", error_message=reason)

    def _audit_failed(self, log_id: int | None, error: str) -> None:
        from qbo import sync_log
        sync_log.complete_log(log_id, status="failed", error_message=error)
    
    def get_status(self) -> dict:
        """Get current QBO connection status."""
        token_path = Path(__file__).parent / ".qbo_tokens.json"
        
        status = {
            "configured": bool(os.environ.get("QBO_CLIENT_ID")),
            "environment": os.environ.get("QBO_ENVIRONMENT", "sandbox"),
            "write_enabled": self.write_enabled,
            "has_tokens": token_path.exists(),
            "is_mock": self.is_mock_mode,
            "is_connected": self.is_connected,
        }
        
        if token_path.exists():
            try:
                with open(token_path) as f:
                    tokens = json.load(f)
                    status["realm_id"] = tokens.get("realm_id", "unknown")
            except Exception:
                pass
        
        return status

    def _stamp_product_qbo_id(self, sku: str, qbo_id: str) -> None:
        """Persist the QBO Item Id + sync timestamp onto the local product row.

        Best-effort: silently skips when the migration hasn't run or the
        SKU has no matching row.
        """
        if not sku or not qbo_id:
            return
        try:
            from db.database import get_connection
            from datetime import datetime
            conn = get_connection()
            conn.execute(
                "UPDATE products SET qbo_id = ?, qbo_synced_at = ? WHERE sku = ?",
                (str(qbo_id), datetime.utcnow().isoformat(timespec="seconds"), sku),
            )
            try:
                conn.commit()
            except Exception:
                pass
        except Exception as exc:
            logger.debug(f"_stamp_product_qbo_id failed for {sku}: {exc}")

    def _throttled_save(self, obj):
        """Throttle then save. Returns the saved object's Id as a string.

        Used by idempotent_write lambdas to keep QBO write traffic under
        the per-realm rate cap.
        """
        from qbo._throttle import throttle
        throttle()
        obj.save(qb=self.client._client)
        return str(obj.Id)

    # ------------------------------------------------------------------
    # Tier 3 helpers: tax codes + currency
    # ------------------------------------------------------------------
    @staticmethod
    def _default_tax_code() -> str | None:
        """Read the workspace-wide default QBO TaxCode id (or None).

        Persisted as setting ``qbo.default_tax_code``. Used when a line
        doesn't carry its own ``qbo_tax_code`` override.
        """
        try:
            from db import get_setting
            val = (get_setting("qbo.default_tax_code") or "").strip()
            return val or None
        except Exception:
            return None

    @staticmethod
    def _default_currency() -> str | None:
        """Read the configured QBO home currency override (or None).

        Persisted as setting ``qbo.default_currency`` (ISO 4217, e.g.
        ``USD``). When unset, QBO uses the realm's home currency.
        """
        try:
            from db import get_setting
            val = (get_setting("qbo.default_currency") or "").strip().upper()
            return val or None
        except Exception:
            return None

    @classmethod
    def _resolve_tax_code(cls, doc_default: str | None,
                          line_code: str | None) -> str | None:
        """Pick a tax code: line override > doc override > setting."""
        for c in (line_code, doc_default, cls._default_tax_code()):
            if c and str(c).strip():
                return str(c).strip()
        return None

    @staticmethod
    def _apply_tax_code(line_detail, tax_code: str | None) -> None:
        """Attach a TaxCodeRef to a SalesItemLineDetail / ExpenseLineDetail."""
        if not tax_code:
            return
        try:
            from quickbooks.objects.base import Ref
        except Exception:
            return
        ref = Ref()
        ref.value = tax_code
        try:
            line_detail.TaxCodeRef = ref
        except Exception:
            pass

    @classmethod
    def _apply_currency(cls, txn, currency_code: str | None) -> None:
        """Attach a CurrencyRef to a transaction object."""
        code = (currency_code or cls._default_currency() or "").strip().upper()
        if not code:
            return
        try:
            from quickbooks.objects.base import Ref
        except Exception:
            return
        ref = Ref()
        ref.value = code
        try:
            txn.CurrencyRef = ref
        except Exception:
            pass

    @staticmethod
    def _item_content_hash(item_data) -> str:
        """Stable hash of QBO-relevant fields. Used for change detection.

        Two items with identical hashes are guaranteed to map to the same
        QBO payload, so we can skip the round-trip safely.
        """
        import hashlib
        payload = "|".join([
            (item_data.sku or "").strip(),
            (item_data.name or "").strip(),
            f"{float(item_data.price or 0):.4f}",
            f"{float(item_data.cost or 0):.4f}",
            f"{float(item_data.qty_on_hand or 0):.4f}",
            (item_data.description or "").strip(),
            (item_data.oem_part_number or "").strip(),
            (item_data.pai_part_number or "").strip(),
            (item_data.manufacturer or "").strip(),
            (item_data.item_type or "Inventory").strip(),
        ])
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _read_product_sync_state(self, sku: str) -> dict:
        """Return ``{qbo_id, qbo_content_hash}`` for a SKU, or empty dict."""
        try:
            from db.database import get_connection
            conn = get_connection()
            row = conn.execute(
                "SELECT qbo_id, qbo_content_hash FROM products WHERE sku = ?",
                (sku,),
            ).fetchone()
            if not row:
                return {}
            return {"qbo_id": row[0], "qbo_content_hash": row[1]}
        except Exception:
            return {}

    def _stamp_product_content_hash(self, sku: str, content_hash: str) -> None:
        """Persist the latest content hash so next sync can skip if unchanged."""
        if not sku or not content_hash:
            return
        try:
            from db.database import get_connection
            conn = get_connection()
            conn.execute(
                "UPDATE products SET qbo_content_hash = ? WHERE sku = ?",
                (content_hash, sku),
            )
            try:
                conn.commit()
            except Exception:
                pass
        except Exception as exc:
            logger.debug(f"_stamp_product_content_hash failed for {sku}: {exc}")
    
    # =========================================================================
    # Item Operations
    # =========================================================================
    
    def sync_item(self, item_data: QBOItemData) -> dict:
        """
        Sync a single item to QBO (create or update).
        
        Returns:
            {success: bool, action: 'created'|'updated'|'skipped', qbo_id: str, error: str}
        """
        log_id = self._audit_start(
            source_type="product",
            source_id=None,
            qbo_object_type="Item",
            request_payload={
                "sku": item_data.sku, "name": item_data.name,
                "price": item_data.price, "cost": item_data.cost,
                "qty_on_hand": item_data.qty_on_hand,
                "item_type": item_data.item_type,
            },
        )

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        # Change detection: if nothing material changed since the last
        # successful sync, skip the QBO round-trip entirely.
        content_hash = self._item_content_hash(item_data)
        prior = self._read_product_sync_state(item_data.sku)
        if prior.get("qbo_id") and prior.get("qbo_content_hash") == content_hash:
            self._audit_skipped(log_id, "Unchanged since last sync")
            return {
                "success": True,
                "action": "unchanged",
                "qbo_id": prior.get("qbo_id"),
                "reason": "Content hash matches last sync",
            }

        # Resolve the existing QBO item. Order of preference:
        #   1. qbo_id carried on the local row (survives SKU renames)
        #   2. prior sync state keyed by the current SKU
        #   3. live QBO SKU lookup
        # Without (1) a local SKU rename would miss (3) and create a
        # duplicate item in QBO.
        existing = None
        link_id = item_data.qbo_id or prior.get("qbo_id")
        if link_id:
            try:
                from quickbooks.objects.item import Item
                existing_item = Item.get(str(link_id), qb=self.client._client)
                if existing_item:
                    existing = {
                        "id": str(existing_item.Id),
                        "name": getattr(existing_item, "Name", ""),
                        "sku": getattr(existing_item, "Sku", ""),
                        "price": float(getattr(existing_item, "UnitPrice", 0) or 0),
                        "cost": float(getattr(existing_item, "PurchaseCost", 0) or 0),
                    }
            except Exception as exc:
                logger.debug(
                    "QBO item lookup by id %s failed (%s); falling back to SKU search",
                    link_id, exc,
                )
                existing = None
        if existing is None:
            existing = self.client.search_by_sku(item_data.sku)
        
        # Build description with cross-refs
        description = item_data.description
        if item_data.oem_part_number:
            description += f"\nOEM: {item_data.oem_part_number}"
        if item_data.pai_part_number:
            description += f"\nPAI: {item_data.pai_part_number}"
        if item_data.manufacturer:
            description += f"\nMfr: {item_data.manufacturer}"
        if item_data.category:
            description += f"\nCategory: {item_data.category}"
        if item_data.condition:
            description += f"\nCondition: {item_data.condition}"
        if item_data.core_charge and float(item_data.core_charge) > 0:
            description += f"\nCore Charge: ${float(item_data.core_charge):.2f}"
        
        if existing:
            # Update existing
            if not self.write_enabled:
                self._audit_skipped(log_id, "Write disabled (update)")
                return {
                    "success": True,
                    "action": "skipped",
                    "qbo_id": existing.get("id"),
                    "reason": "Write disabled",
                }
            
            result = self.client.update_item(
                existing["id"],
                name=item_data.name,
                sku=item_data.sku,
                price=item_data.price,
                cost=item_data.cost,
                description=description,
            )
            
            if result:
                from qbo import sync_log
                sync_log.complete_log(
                    log_id, status="success", response_status=200,
                    response_json=sync_log.to_json_text(result),
                    qbo_object_id=str(result.get("id") or ""),
                )
                return {"success": True, "action": "updated", "qbo_id": result.get("id")}
            self._audit_failed(log_id, "Update failed")
            return {"success": False, "error": "Update failed"}
        
        else:
            # Create new
            if not self.write_enabled:
                self._audit_skipped(log_id, "Write disabled (create)")
                return {
                    "success": True,
                    "action": "skipped",
                    "reason": "Write disabled (would create)",
                }

            # Pull configured account IDs from settings
            from qbo import config as _qbo_config
            from qbo._retry import retry_with_backoff
            mapping = _qbo_config.get_account_mapping()

            def _do_create():
                return self.client.create_item(
                    sku=item_data.sku,
                    name=item_data.name,
                    price=item_data.price,
                    cost=item_data.cost,
                    qty_on_hand=item_data.qty_on_hand,
                    description=description,
                    income_account_id=mapping.get("sales_income") or None,
                    expense_account_id=mapping.get("cogs") or None,
                    asset_account_id=mapping.get("inventory_asset") or None,
                    item_type=item_data.item_type or "Inventory",
                )

            try:
                result = retry_with_backoff(_do_create)
            except Exception as exc:
                self._audit_failed(log_id, f"{type(exc).__name__}: {exc}")
                return {"success": False, "error": str(exc)}

            if result:
                from qbo import sync_log
                sync_log.complete_log(
                    log_id, status="success", response_status=200,
                    response_json=sync_log.to_json_text(result),
                    qbo_object_id=str(result.get("id") or ""),
                )
                return {"success": True, "action": "created", "qbo_id": result.get("id")}
            self._audit_failed(log_id, "Create failed")
            return {"success": False, "error": "Create failed"}
    
    def batch_sync_items(
        self,
        items: list[QBOItemData],
        progress_callback: Optional[callable] = None,
    ) -> QBOSyncResult:
        """
        Sync multiple items to QBO.
        
        Args:
            items: List of items to sync
            progress_callback: Optional callback(current, total, message)
        
        Returns:
            QBOSyncResult with counts and details
        """
        result = QBOSyncResult(success=True)
        total = len(items)
        
        for i, item in enumerate(items):
            if progress_callback:
                progress_callback(i + 1, total, f"Syncing {item.sku}...")
            
            sync_result = self.sync_item(item)
            
            if sync_result.get("success"):
                action = sync_result.get("action")
                if action == "created":
                    result.created += 1
                elif action == "updated":
                    result.updated += 1
                else:
                    result.skipped += 1
                # Stamp the QBO id on the local product row (best effort).
                qbo_id = sync_result.get("qbo_id")
                if qbo_id and action in ("created", "updated"):
                    self._stamp_product_qbo_id(item.sku, str(qbo_id))
                    # Also store the content hash so next sync can skip.
                    self._stamp_product_content_hash(
                        item.sku, self._item_content_hash(item)
                    )
            else:
                result.errors.append(f"{item.sku}: {sync_result.get('error')}")
            
            result.details.append({
                "sku": item.sku,
                **sync_result,
            })
        
        if result.errors:
            result.success = False
        
        return result
    
    def sync_products_from_db(
        self,
        category: Optional[str] = None,
        manufacturer: Optional[str] = None,
        progress_callback: Optional[callable] = None,
    ) -> QBOSyncResult:
        """
        Sync products from local database to QBO.
        
        Args:
            category: Filter by category (optional)
            manufacturer: Filter by manufacturer (optional)
            progress_callback: Optional progress callback
        
        Returns:
            QBOSyncResult
        """
        from db.database import get_connection
        
        conn = get_connection()
        
        # Build query — include all products with a SKU.
        # NOTE: column names match the live schema (see PRAGMA table_info(products)).
        #   - pai_sku stores the PAI part number (no separate pai_part_number column)
        #   - there is no is_active column; use sku presence as the gate
        #   - id + qbo_id let ``sync_item`` update an existing QBO record
        #     when the local SKU was renamed (avoids duplicates).
        query = """
            SELECT sku, title, selling_price, COALESCE(pai_cost, cost) AS resolved_cost,
                   COALESCE(invoice_description, description_html) AS desc,
                   oem_part_number, pai_sku, manufacturer,
                   COALESCE(qty_on_hand, 0) AS qty,
                   COALESCE(product_type, 'inventoried') AS ptype,
                   category, condition, COALESCE(core_charge, 0) AS core_charge,
                   id, qbo_id
            FROM products
            WHERE sku IS NOT NULL AND sku != ''
        """
        params = []
        
        if category:
            query += " AND category = ?"
            params.append(category)
        
        if manufacturer:
            query += " AND manufacturer = ?"
            params.append(manufacturer)
        
        rows = conn.execute(query, params).fetchall()
        
        # Convert to QBOItemData
        items = []
        for row in rows:
            ptype = (row[9] or "inventoried").lower()
            item_type = "Inventory" if ptype == "inventoried" else "Service"
            items.append(QBOItemData(
                sku=row[0] or "",
                name=(row[1] or row[0] or "")[:100],
                price=float(row[2] or 0),
                cost=float(row[3] or 0),
                description=(row[4] or "")[:500],
                qty_on_hand=float(row[8] or 0) if item_type == "Inventory" else 0.0,
                oem_part_number=row[5],
                pai_part_number=row[6],
                manufacturer=row[7],
                item_type=item_type,
                category=row[10],
                condition=row[11],
                core_charge=float(row[12] or 0),
                local_id=row[13],
                qbo_id=(str(row[14]) if row[14] else None),
            ))
        
        return self.batch_sync_items(items, progress_callback)
    
    # =========================================================================
    # Bill Operations (Purchases from PAI)
    # =========================================================================
    
    def create_bill(self, bill_data: QBOBillData, *,
                    source_id: int | None = None,
                    linked_po_qbo_id: str | None = None) -> dict:
        """
        Create a bill in QBO (accounts payable).
        
        Returns:
            {success: bool, qbo_id: str, error: str}
        """
        request_preview = {
            "VendorRef": {"DisplayName": bill_data.vendor_name},
            "DocNumber": bill_data.ref_number,
            "TxnDate": bill_data.bill_date.isoformat() if bill_data.bill_date else None,
            "TotalAmt": bill_data.total,
            "Line": [
                {"sku": ln.sku, "qty": ln.quantity,
                 "unit_cost": ln.unit_cost, "amount": ln.amount,
                 "description": ln.description}
                for ln in bill_data.lines
            ],
        }
        log_id = self._audit_start(
            source_type="bill", source_id=source_id,
            qbo_object_type="Bill", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "vendor": bill_data.vendor_name,
                    "ref": bill_data.ref_number,
                    "total": bill_data.total,
                    "lines": len(bill_data.lines),
                }
            }
        
        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}
        
        if self.is_mock_mode:
            mock_id = f"MOCK-BILL-{bill_data.ref_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }
        
        try:
            from quickbooks.objects.bill import Bill
            from quickbooks.objects.vendor import Vendor
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import ItemBasedExpenseLine, ItemBasedExpenseLineDetail
            
            # Find or create vendor
            vendor = self._get_or_create_vendor(bill_data.vendor_name)
            if not vendor:
                self._audit_failed(log_id, f"Vendor not found: {bill_data.vendor_name}")
                return {"success": False, "error": f"Vendor not found: {bill_data.vendor_name}"}
            
            # Build bill
            bill = Bill()
            bill.VendorRef = vendor.to_ref()
            bill.DocNumber = bill_data.ref_number[:21]  # QBO limit
            bill.TxnDate = bill_data.bill_date.isoformat()
            if bill_data.due_date:
                bill.DueDate = bill_data.due_date.isoformat()
            if bill_data.memo:
                bill.PrivateNote = bill_data.memo[:4000]
            
            # Add line items
            lines = []
            for line in bill_data.lines:
                # Find item by SKU
                qbo_item = self.client.search_by_sku(line.sku)
                if not qbo_item:
                    logger.warning(f"Item not found in QBO: {line.sku}")
                    continue
                
                item_obj = Item.get(qbo_item["id"], qb=self.client._client)
                
                detail = ItemBasedExpenseLineDetail()
                detail.ItemRef = item_obj.to_ref()
                detail.Qty = line.quantity
                detail.UnitPrice = line.unit_cost
                # Tier 3: per-line TaxCodeRef with fallback to doc/setting default.
                self._apply_tax_code(
                    detail,
                    self._resolve_tax_code(None, getattr(line, "qbo_tax_code", None)),
                )
                
                exp_line = ItemBasedExpenseLine()
                exp_line.Amount = line.amount
                exp_line.Description = line.description[:4000]
                exp_line.ItemBasedExpenseLineDetail = detail
                
                lines.append(exp_line)
            
            bill.Line = lines

            if not lines:
                msg = ("Bill has no QBO-mapped line items. "
                       "Push products to QBO first (Settings → QBO → Sync Products).")
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            # Tier 3: multi-currency (no-op when blank or home currency).
            self._apply_currency(bill, getattr(bill_data, "currency_code", None))

            # Phase 8: link the Bill back to its originating PurchaseOrder
            # so QBO marks the PO closed and shows the linked bill in its UI.
            if linked_po_qbo_id:
                try:
                    from quickbooks.objects.detailline import LinkedTxn
                    link = LinkedTxn()
                    link.TxnId = str(linked_po_qbo_id)
                    link.TxnType = "PurchaseOrder"
                    existing = list(getattr(bill, "LinkedTxn", None) or [])
                    existing.append(link)
                    bill.LinkedTxn = existing
                except Exception as exc:
                    logger.debug("LinkedTxn(PurchaseOrder) attach failed: %s", exc)

            # Idempotency + retry guard — prevents duplicate QBO bills on retry.
            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="bill",
                local_ref=str(source_id) if source_id is not None else bill_data.ref_number,
                fn=lambda: self._throttled_save(bill),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, bill)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(bill.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }
            
        except Exception as e:
            logger.exception(f"Error creating bill: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Phase 8: PurchaseOrder push
    # ------------------------------------------------------------------
    def create_purchase_order(self, po_data: QBOPurchaseOrderData, *,
                              source_id: int | None = None) -> dict:
        """Create a PurchaseOrder in QBO (commitment, pre-receipt).

        Returns ``{success, qbo_id, action, idempotency_key, error}``.

        Mirrors :meth:`create_bill` but writes against the QBO
        ``PurchaseOrder`` entity. The resulting ``qbo_id`` should be
        stamped onto ``purchase_orders.qbo_po_id`` so that later Bill
        creation can pass it via ``linked_po_qbo_id``.
        """
        request_preview = {
            "VendorRef": {"DisplayName": po_data.vendor_name},
            "DocNumber": po_data.po_number,
            "TxnDate": po_data.po_date.isoformat() if po_data.po_date else None,
            "TotalAmt": po_data.total,
            "POStatus": po_data.po_status,
            "Line": [
                {"sku": ln.sku, "qty": ln.quantity,
                 "unit_cost": ln.unit_cost, "amount": ln.amount,
                 "description": ln.description}
                for ln in po_data.lines
            ],
        }
        log_id = self._audit_start(
            source_type="purchase_order", source_id=source_id,
            qbo_object_type="PurchaseOrder", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "vendor": po_data.vendor_name,
                    "po": po_data.po_number,
                    "total": po_data.total,
                    "lines": len(po_data.lines),
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-PO-{po_data.po_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.purchaseorder import PurchaseOrder
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import (
                ItemBasedExpenseLine,
                ItemBasedExpenseLineDetail,
            )

            vendor = self._get_or_create_vendor(po_data.vendor_name)
            if not vendor:
                self._audit_failed(
                    log_id, f"Vendor not found: {po_data.vendor_name}",
                )
                return {
                    "success": False,
                    "error": f"Vendor not found: {po_data.vendor_name}",
                }

            po = PurchaseOrder()
            po.VendorRef = vendor.to_ref()
            po.DocNumber = po_data.po_number[:21]  # QBO limit
            po.TxnDate = po_data.po_date.isoformat()
            if po_data.due_date:
                po.DueDate = po_data.due_date.isoformat()
            if po_data.memo:
                po.PrivateNote = po_data.memo[:4000]
            if po_data.vendor_message:
                # QBO uses CustomerMemo for vendor-facing notes on PO.
                try:
                    from quickbooks.objects.base import CustomerMemo
                    cm = CustomerMemo()
                    cm.value = po_data.vendor_message[:1000]
                    po.CustomerMemo = cm
                except Exception:
                    pass
            if po_data.po_status in ("Open", "Closed"):
                po.POStatus = po_data.po_status

            lines = []
            for line in po_data.lines:
                qbo_item = self.client.search_by_sku(line.sku)
                if not qbo_item:
                    logger.warning(f"Item not found in QBO: {line.sku}")
                    continue

                item_obj = Item.get(qbo_item["id"], qb=self.client._client)

                detail = ItemBasedExpenseLineDetail()
                detail.ItemRef = item_obj.to_ref()
                detail.Qty = line.quantity
                detail.UnitPrice = line.unit_cost
                self._apply_tax_code(
                    detail,
                    self._resolve_tax_code(
                        po_data.qbo_tax_code,
                        getattr(line, "qbo_tax_code", None),
                    ),
                )

                exp_line = ItemBasedExpenseLine()
                exp_line.Amount = line.amount
                exp_line.Description = (line.description or "")[:4000]
                exp_line.ItemBasedExpenseLineDetail = detail
                lines.append(exp_line)

            po.Line = lines

            if not lines:
                msg = ("PurchaseOrder has no QBO-mapped line items. "
                       "Push products to QBO first "
                       "(Settings → QBO → Sync Products).")
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            self._apply_currency(po, getattr(po_data, "currency_code", None))

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="purchase_order",
                local_ref=(
                    str(source_id) if source_id is not None
                    else po_data.po_number
                ),
                fn=lambda: self._throttled_save(po),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {
                    "success": False,
                    "error": outcome.get("error") or "save failed",
                }

            self._audit_success(log_id, po)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(po.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating purchase order: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    def _get_or_create_vendor(self, name: str):
        """Get vendor by name, or create if not exists.

        When creating, enriches the QBO Vendor from the local ``vendors``
        table (contact name/email/phone, address, payment terms, notes).
        """
        if name in self._vendors:
            return self._vendors[name]
        
        try:
            from quickbooks.objects.vendor import Vendor
            
            # Search for vendor
            vendors = Vendor.filter(DisplayName=name, qb=self.client._client)
            if vendors:
                self._vendors[name] = vendors[0]
                return vendors[0]
            
            # Create new vendor if write enabled
            if self.write_enabled:
                local = self._lookup_local_vendor(name)
                log_id = self._audit_start(
                    source_type="vendor", source_id=(local or {}).get("id"),
                    qbo_object_type="Vendor",
                    request_payload={"DisplayName": name, "enriched": bool(local)},
                )
                try:
                    vendor = Vendor()
                    vendor.DisplayName = name
                    self._apply_vendor_fields(vendor, local)
                    if self.is_mock_mode:
                        mock_id = f"MOCK-VEND-{name}"
                        self._audit_mock(log_id, mock_id)
                    else:
                        vendor.save(qb=self.client._client)
                        self._audit_success(log_id, vendor)
                    self._vendors[name] = vendor
                    return vendor
                except Exception as exc:
                    self._audit_failed(log_id, f"{type(exc).__name__}: {exc}")
                    raise
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting/creating vendor: {e}")
            return None

    def _lookup_local_vendor(self, name: str) -> dict | None:
        """Find a vendor row by display name. Returns the **full** row dict."""
        try:
            from db.database import get_connection
            conn = get_connection()
            row = conn.execute(
                """
                SELECT *
                FROM vendors
                WHERE name = ? OR contact_name = ?
                LIMIT 1
                """,
                (name, name),
            ).fetchone()
            if not row:
                return None
            if hasattr(row, "keys"):
                return dict(row)
            return None
        except Exception:
            return None

    def _apply_vendor_fields(self, vendor, local: dict | None) -> None:
        """Delegate to the shared payload builder in :mod:`qbo.vendors`."""
        try:
            from qbo.vendors import apply_vendor_fields
        except Exception:
            return
        apply_vendor_fields(vendor, local)
    
    # =========================================================================
    # Invoice Operations (Sales)
    # =========================================================================
    
    def create_invoice(self, invoice_data: QBOInvoiceData, *,
                       source_id: int | None = None,
                       linked_estimate_qbo_id: str | None = None) -> dict:
        """
        Create an invoice in QBO (accounts receivable).
        
        Returns:
            {success: bool, qbo_id: str, error: str}
        """
        request_preview = {
            "CustomerRef": {"DisplayName": invoice_data.customer_name},
            "DocNumber": invoice_data.invoice_number,
            "TxnDate": invoice_data.invoice_date.isoformat() if invoice_data.invoice_date else None,
            "DueDate": invoice_data.due_date.isoformat() if invoice_data.due_date else None,
            "PrivateNote": invoice_data.memo,
            "TotalAmt": invoice_data.total,
            "Line": [
                {"sku": ln.sku, "qty": ln.quantity,
                 "unit_price": ln.unit_price, "amount": ln.amount,
                 "description": ln.description}
                for ln in invoice_data.lines
            ],
        }
        log_id = self._audit_start(
            source_type="invoice", source_id=source_id,
            qbo_object_type="Invoice", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": invoice_data.customer_name,
                    "number": invoice_data.invoice_number,
                    "total": invoice_data.total,
                    "lines": len(invoice_data.lines),
                }
            }
        
        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}
        
        if self.is_mock_mode:
            mock_id = f"MOCK-INV-{invoice_data.invoice_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }
        
        try:
            from quickbooks.objects.invoice import Invoice
            from quickbooks.objects.customer import Customer
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail
            
            # Find or create customer
            customer = self._get_or_create_customer(invoice_data.customer_name)
            if not customer:
                self._audit_failed(log_id, f"Customer not found: {invoice_data.customer_name}")
                return {"success": False, "error": f"Customer not found: {invoice_data.customer_name}"}
            
            # Build invoice
            invoice = Invoice()
            invoice.CustomerRef = customer.to_ref()
            invoice.DocNumber = invoice_data.invoice_number[:21]
            invoice.TxnDate = invoice_data.invoice_date.isoformat()
            if invoice_data.due_date:
                invoice.DueDate = invoice_data.due_date.isoformat()
            if invoice_data.memo:
                invoice.PrivateNote = invoice_data.memo[:4000]
            
            # Add line items
            lines = []
            doc_tax_code = getattr(invoice_data, "qbo_tax_code", None)
            for line in invoice_data.lines:
                qbo_item = self.client.search_by_sku(line.sku)
                if not qbo_item:
                    logger.warning(f"Item not found in QBO: {line.sku}")
                    continue
                
                item_obj = Item.get(qbo_item["id"], qb=self.client._client)
                
                detail = SalesItemLineDetail()
                detail.ItemRef = item_obj.to_ref()
                detail.Qty = line.quantity
                detail.UnitPrice = line.unit_price
                # Tier 3: per-line TaxCodeRef.
                self._apply_tax_code(
                    detail,
                    self._resolve_tax_code(doc_tax_code,
                                           getattr(line, "qbo_tax_code", None)),
                )
                
                sales_line = SalesItemLine()
                sales_line.Amount = line.amount
                sales_line.Description = line.description[:4000]
                sales_line.SalesItemLineDetail = detail
                
                lines.append(sales_line)
            
            invoice.Line = lines

            if not lines:
                msg = ("Invoice has no QBO-mapped line items. "
                       "Push products to QBO first (Settings → QBO → Sync Products).")
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            # Tier 3: multi-currency.
            self._apply_currency(invoice, getattr(invoice_data, "currency_code", None))

            # Phase 5: link back to an originating Estimate so QBO marks
            # the estimate Closed and shows the linked invoice in its UI.
            if linked_estimate_qbo_id:
                try:
                    from quickbooks.objects.detailline import LinkedTxn
                    link = LinkedTxn()
                    link.TxnId = str(linked_estimate_qbo_id)
                    link.TxnType = "Estimate"
                    existing = list(getattr(invoice, "LinkedTxn", None) or [])
                    existing.append(link)
                    invoice.LinkedTxn = existing
                except Exception as exc:
                    logger.debug("LinkedTxn(Estimate) attach failed: %s", exc)

            # Idempotency + retry guard — prevents duplicate QBO invoices on retry.
            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="invoice",
                local_ref=str(source_id) if source_id is not None else invoice_data.invoice_number,
                fn=lambda: self._throttled_save(invoice),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, invoice)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(invoice.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }
            
        except Exception as e:
            logger.exception(f"Error creating invoice: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}
    
    def _get_or_create_customer(self, name: str):
        """Get customer by name, or create if not exists.

        When creating, enriches the QBO Customer from the local ``customers``
        table (company, email, phone, billing address, notes).
        """
        if name in self._customers:
            return self._customers[name]
        
        try:
            from quickbooks.objects.customer import Customer
            
            # Search for customer
            customers = Customer.filter(DisplayName=name, qb=self.client._client)
            if customers:
                self._customers[name] = customers[0]
                return customers[0]
            
            # Create new customer if write enabled
            if self.write_enabled:
                local = self._lookup_local_customer(name)
                log_id = self._audit_start(
                    source_type="customer", source_id=(local or {}).get("id"),
                    qbo_object_type="Customer",
                    request_payload={"DisplayName": name, "enriched": bool(local)},
                )
                try:
                    customer = Customer()
                    customer.DisplayName = name
                    self._apply_customer_fields(customer, local)
                    if self.is_mock_mode:
                        mock_id = f"MOCK-CUST-{name}"
                        self._audit_mock(log_id, mock_id)
                    else:
                        customer.save(qb=self.client._client)
                        self._audit_success(log_id, customer)
                    self._customers[name] = customer
                    return customer
                except Exception as exc:
                    self._audit_failed(log_id, f"{type(exc).__name__}: {exc}")
                    raise
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting/creating customer: {e}")
            return None

    def _lookup_local_customer(self, name: str) -> dict | None:
        """Find a customer row by display name (matches company or name).

        Returns the **full** row dict so the shared payload builder in
        :mod:`qbo.customers` can use every supported field (mobile, terms,
        ship_to_*, notes, etc.).
        """
        try:
            from db.database import get_connection
            conn = get_connection()
            row = conn.execute(
                """
                SELECT *
                FROM customers
                WHERE company = ? OR name = ?
                LIMIT 1
                """,
                (name, name),
            ).fetchone()
            if not row:
                return None
            if hasattr(row, "keys"):
                return dict(row)
            return dict(zip([d[0] for d in conn.execute(
                "PRAGMA table_info(customers)").fetchall()] or [], row))
        except Exception:
            return None

    def _apply_customer_fields(self, customer, local: dict | None) -> None:
        """Delegate to the shared payload builder so the two QBO push paths
        (this integration class + :func:`qbo.customers.sync_customer_to_qbo`)
        write the **same** fields. Kept as a thin pass-through for back-compat
        with existing call sites.
        """
        try:
            from qbo.customers import apply_customer_fields
        except Exception:
            return
        apply_customer_fields(customer, local)
    
    # =========================================================================
    # Payment Operations (D4)
    # =========================================================================

    def create_payment(self, payment_data: QBOPaymentData, *, source_id: int | None = None) -> dict:
        """Create a customer payment in QBO (ReceivePayment).

        Mirrors the contract of :meth:`create_invoice` /
        :meth:`create_bill`:
            * write-disabled → ``{success: True, action: "skipped",
              reason: "Write disabled", would_create: {...}}``
            * mock mode      → ``{success: True, action: "created",
              qbo_id: "MOCK-PMT-<invoice_number>", is_mock: True}``
            * live           → creates a ``Payment`` linked to the
              QBO invoice via ``LinkedTxn``.
        """
        request_preview = {
            "CustomerRef": {"DisplayName": payment_data.customer_name},
            "TxnDate": payment_data.payment_date.isoformat() if payment_data.payment_date else None,
            "TotalAmt": payment_data.amount,
            "PaymentMethod": payment_data.payment_method,
            "PaymentRefNum": payment_data.ref_number,
            "PrivateNote": payment_data.memo,
            "InvoiceQBOId": payment_data.invoice_qbo_id,
            "InvoiceNumber": payment_data.invoice_number,
        }
        log_id = self._audit_start(
            source_type="payment", source_id=source_id,
            qbo_object_type="Payment", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": payment_data.customer_name,
                    "invoice": payment_data.invoice_number,
                    "amount": payment_data.amount,
                    "method": payment_data.payment_method,
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-PMT-{payment_data.invoice_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.payment import Payment, PaymentLine
            from quickbooks.objects.detailline import LinkedTxn

            customer = self._get_or_create_customer(payment_data.customer_name)
            if not customer:
                self._audit_failed(log_id, f"Customer not found: {payment_data.customer_name}")
                return {
                    "success": False,
                    "error": f"Customer not found: {payment_data.customer_name}",
                }

            payment = Payment()
            payment.CustomerRef = customer.to_ref()
            payment.TxnDate = payment_data.payment_date.isoformat()
            payment.TotalAmt = payment_data.amount
            if payment_data.ref_number:
                payment.PaymentRefNum = payment_data.ref_number[:21]
            if payment_data.memo:
                payment.PrivateNote = payment_data.memo[:4000]

            if payment_data.invoice_qbo_id:
                linked = LinkedTxn()
                linked.TxnId = str(payment_data.invoice_qbo_id)
                linked.TxnType = "Invoice"

                line = PaymentLine()
                line.Amount = payment_data.amount
                line.LinkedTxn = [linked]
                payment.Line = [line]

            # Idempotency + retry guard — prevents duplicate QBO payments on retry.
            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="payment",
                local_ref=str(source_id) if source_id is not None else (payment_data.ref_number or payment_data.invoice_number),
                fn=lambda: self._throttled_save(payment),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, payment)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(payment.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating payment: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Credit-Memo Operations (D4)
    # =========================================================================

    def create_credit_memo(self, credit_data: QBOCreditMemoData, *, source_id: int | None = None) -> dict:
        """Create a CreditMemo in QBO.

        Same write-disabled / mock / live response shape as the other
        document creators. The credit is single-line (one ``SalesItemLine``
        with quantity 1 at ``credit_data.amount``); JAKS credits are
        flat-amount records, not multi-line documents.
        """
        request_preview = {
            "CustomerRef": {"DisplayName": credit_data.customer_name},
            "DocNumber": credit_data.credit_number,
            "TxnDate": credit_data.issue_date.isoformat() if credit_data.issue_date else None,
            "PrivateNote": credit_data.memo,
            "TotalAmt": credit_data.amount,
            "Line": [{
                "sku": credit_data.sku, "qty": 1,
                "unit_price": credit_data.amount, "amount": credit_data.amount,
                "description": credit_data.description,
            }],
        }
        log_id = self._audit_start(
            source_type="credit_memo", source_id=source_id,
            qbo_object_type="CreditMemo", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": credit_data.customer_name,
                    "number": credit_data.credit_number,
                    "amount": credit_data.amount,
                    "sku": credit_data.sku,
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-CM-{credit_data.credit_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.creditmemo import CreditMemo
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import (
                SalesItemLine,
                SalesItemLineDetail,
            )

            customer = self._get_or_create_customer(credit_data.customer_name)
            if not customer:
                self._audit_failed(log_id, f"Customer not found: {credit_data.customer_name}")
                return {
                    "success": False,
                    "error": f"Customer not found: {credit_data.customer_name}",
                }

            cm = CreditMemo()
            cm.CustomerRef = customer.to_ref()
            cm.DocNumber = credit_data.credit_number[:21]
            cm.TxnDate = credit_data.issue_date.isoformat()
            if credit_data.memo:
                cm.PrivateNote = credit_data.memo[:4000]

            qbo_item = self.client.search_by_sku(credit_data.sku)
            if not qbo_item:
                self._audit_failed(log_id, f"Credit item not found in QBO: {credit_data.sku}")
                return {
                    "success": False,
                    "error": f"Credit item not found in QBO: {credit_data.sku}",
                }
            item_obj = Item.get(qbo_item["id"], qb=self.client._client)

            detail = SalesItemLineDetail()
            detail.ItemRef = item_obj.to_ref()
            detail.Qty = 1
            detail.UnitPrice = credit_data.amount
            # Tier 3: tax code (override > setting default).
            self._apply_tax_code(
                detail, self._resolve_tax_code(getattr(credit_data, "qbo_tax_code", None), None)
            )

            line = SalesItemLine()
            line.Amount = credit_data.amount
            line.Description = credit_data.description[:4000]
            line.SalesItemLineDetail = detail

            cm.Line = [line]

            # Tier 3: multi-currency.
            self._apply_currency(cm, getattr(credit_data, "currency_code", None))

            # Idempotency + retry guard — prevents duplicate QBO credit memos on retry.
            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="credit_memo",
                local_ref=str(source_id) if source_id is not None else credit_data.credit_number,
                fn=lambda: self._throttled_save(cm),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, cm)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(cm.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating credit memo: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Sales Receipt (cash sale) — Tier 3
    # =========================================================================

    def create_sales_receipt(
        self,
        receipt_data: "QBOSalesReceiptData",
        *,
        source_id: int | None = None,
    ) -> dict:
        """Create a QBO SalesReceipt (invoice + payment in one document).

        Use this for cash sales / counter sales where payment is received
        at the time of sale. Money lands in ``deposit_to_account_id``
        (typically Undeposited Funds) instead of A/R.

        Mirrors :meth:`create_invoice`'s envelope (idempotency, audit,
        throttling, mock mode, tax/currency).
        """
        request_preview = {
            "CustomerRef": {"DisplayName": receipt_data.customer_name},
            "DocNumber": receipt_data.receipt_number,
            "TxnDate": receipt_data.receipt_date.isoformat() if receipt_data.receipt_date else None,
            "PrivateNote": receipt_data.memo,
            "PaymentMethod": receipt_data.payment_method,
            "DepositToAccountRef": receipt_data.deposit_to_account_id,
            "TotalAmt": receipt_data.total,
            "Line": [
                {"sku": ln.sku, "qty": ln.quantity,
                 "unit_price": ln.unit_price, "amount": ln.amount,
                 "description": ln.description}
                for ln in receipt_data.lines
            ],
        }
        log_id = self._audit_start(
            source_type="sales_receipt", source_id=source_id,
            qbo_object_type="SalesReceipt", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": receipt_data.customer_name,
                    "number": receipt_data.receipt_number,
                    "total": receipt_data.total,
                    "lines": len(receipt_data.lines),
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-SR-{receipt_data.receipt_number}"
            self._audit_mock(log_id, mock_id)
            return {"success": True, "action": "created", "qbo_id": mock_id, "is_mock": True}

        try:
            from quickbooks.objects.salesreceipt import SalesReceipt
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import SalesItemLine, SalesItemLineDetail
            from quickbooks.objects.base import Ref

            customer = self._get_or_create_customer(receipt_data.customer_name)
            if not customer:
                self._audit_failed(log_id, f"Customer not found: {receipt_data.customer_name}")
                return {"success": False, "error": f"Customer not found: {receipt_data.customer_name}"}

            sr = SalesReceipt()
            sr.CustomerRef = customer.to_ref()
            sr.DocNumber = receipt_data.receipt_number[:21]
            sr.TxnDate = receipt_data.receipt_date.isoformat()
            if receipt_data.memo:
                sr.PrivateNote = receipt_data.memo[:4000]
            if receipt_data.ref_number:
                sr.PaymentRefNum = receipt_data.ref_number[:21]

            # DepositToAccountRef — undeposited funds or a bank account.
            if receipt_data.deposit_to_account_id:
                acc_ref = Ref()
                acc_ref.value = str(receipt_data.deposit_to_account_id)
                sr.DepositToAccountRef = acc_ref

            # PaymentMethodRef — only by name lookup; safe to skip on miss.
            if receipt_data.payment_method:
                try:
                    from quickbooks.objects.paymentmethod import PaymentMethod
                    found = PaymentMethod.filter(
                        Name=receipt_data.payment_method, qb=self.client._client,
                    )
                    if found:
                        sr.PaymentMethodRef = found[0].to_ref()
                except Exception:
                    pass

            lines = []
            doc_tax_code = getattr(receipt_data, "qbo_tax_code", None)
            for line in receipt_data.lines:
                qbo_item = self.client.search_by_sku(line.sku)
                if not qbo_item:
                    logger.warning(f"Item not found in QBO: {line.sku}")
                    continue
                item_obj = Item.get(qbo_item["id"], qb=self.client._client)
                detail = SalesItemLineDetail()
                detail.ItemRef = item_obj.to_ref()
                detail.Qty = line.quantity
                detail.UnitPrice = line.unit_price
                self._apply_tax_code(
                    detail,
                    self._resolve_tax_code(doc_tax_code,
                                           getattr(line, "qbo_tax_code", None)),
                )
                sl = SalesItemLine()
                sl.Amount = line.amount
                sl.Description = line.description[:4000]
                sl.SalesItemLineDetail = detail
                lines.append(sl)

            sr.Line = lines
            if not lines:
                msg = ("Sales receipt has no QBO-mapped line items. "
                       "Push products to QBO first (Settings → QBO → Sync Products).")
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            self._apply_currency(sr, getattr(receipt_data, "currency_code", None))

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="sales_receipt",
                local_ref=str(source_id) if source_id is not None else receipt_data.receipt_number,
                fn=lambda: self._throttled_save(sr),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, sr)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(sr.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating sales receipt: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Refund Receipt (cash refund) — Phase C
    # =========================================================================

    def create_refund_receipt(
        self,
        refund_data: "QBORefundReceiptData",
        *,
        source_id: int | None = None,
    ) -> dict:
        """Create a QBO RefundReceipt (cash refund).

        Same envelope as :meth:`create_credit_memo` (write-disabled →
        skipped, mock mode → MOCK-<n> id). Single-line by design.
        """
        request_preview = {
            "CustomerRef": {"DisplayName": refund_data.customer_name},
            "DocNumber": refund_data.refund_number,
            "TxnDate": refund_data.refund_date.isoformat() if refund_data.refund_date else None,
            "TotalAmt": refund_data.amount,
            "PaymentMethod": refund_data.payment_method,
            "DepositFromAccountRef": refund_data.deposit_from_account_id,
            "PrivateNote": refund_data.memo,
            "Line": [{
                "sku": refund_data.sku, "qty": 1,
                "unit_price": refund_data.amount,
                "amount": refund_data.amount,
                "description": refund_data.description,
            }],
        }
        log_id = self._audit_start(
            source_type="refund_receipt", source_id=source_id,
            qbo_object_type="RefundReceipt", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": refund_data.customer_name,
                    "number": refund_data.refund_number,
                    "amount": refund_data.amount,
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-RR-{refund_data.refund_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.refundreceipt import RefundReceipt
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import (
                SalesItemLine,
                SalesItemLineDetail,
            )
            from quickbooks.objects.base import Ref

            customer = self._get_or_create_customer(refund_data.customer_name)
            if not customer:
                self._audit_failed(
                    log_id, f"Customer not found: {refund_data.customer_name}"
                )
                return {
                    "success": False,
                    "error": f"Customer not found: {refund_data.customer_name}",
                }

            rr = RefundReceipt()
            rr.CustomerRef = customer.to_ref()
            rr.DocNumber = refund_data.refund_number[:21]
            rr.TxnDate = refund_data.refund_date.isoformat()
            if refund_data.memo:
                rr.PrivateNote = refund_data.memo[:4000]
            if refund_data.ref_number:
                rr.PaymentRefNum = refund_data.ref_number[:21]

            if refund_data.deposit_from_account_id:
                acc_ref = Ref()
                acc_ref.value = str(refund_data.deposit_from_account_id)
                rr.DepositToAccountRef = acc_ref

            if refund_data.payment_method:
                try:
                    from quickbooks.objects.paymentmethod import PaymentMethod
                    found = PaymentMethod.filter(
                        Name=refund_data.payment_method,
                        qb=self.client._client,
                    )
                    if found:
                        rr.PaymentMethodRef = found[0].to_ref()
                except Exception:
                    pass

            qbo_item = self.client.search_by_sku(refund_data.sku)
            if not qbo_item:
                self._audit_failed(
                    log_id,
                    f"Refund item not found in QBO: {refund_data.sku}",
                )
                return {
                    "success": False,
                    "error": f"Refund item not found in QBO: {refund_data.sku}",
                }
            item_obj = Item.get(qbo_item["id"], qb=self.client._client)

            detail = SalesItemLineDetail()
            detail.ItemRef = item_obj.to_ref()
            detail.Qty = 1
            detail.UnitPrice = refund_data.amount
            self._apply_tax_code(
                detail,
                self._resolve_tax_code(
                    getattr(refund_data, "qbo_tax_code", None), None
                ),
            )

            sl = SalesItemLine()
            sl.Amount = refund_data.amount
            sl.Description = refund_data.description[:4000]
            sl.SalesItemLineDetail = detail
            rr.Line = [sl]

            self._apply_currency(
                rr, getattr(refund_data, "currency_code", None)
            )

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="refund_receipt",
                local_ref=str(source_id) if source_id is not None else refund_data.refund_number,
                fn=lambda: self._throttled_save(rr),
            )
            if not outcome.get("success"):
                self._audit_failed(
                    log_id, str(outcome.get("error") or "unknown")
                )
                return {
                    "success": False,
                    "error": outcome.get("error") or "save failed",
                }

            self._audit_success(log_id, rr)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(rr.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating refund receipt: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Vendor Credit (AP) — P8: vendor-core return → QBO VendorCredit
    # =========================================================================

    def create_vendor_credit(
        self,
        credit_data: "QBOVendorCreditData",
        *,
        source_id: int | None = None,
    ) -> dict:
        """Create a QBO VendorCredit (vendor refunded a core deposit).

        Same envelope as :meth:`create_bill` (write-disabled → skipped,
        mock mode → MOCK-<n> id, idempotent retry guarded). The credit
        is single-line by design: ``vendor_core_returns`` rows are
        one-row-per-part-returned, so one VendorCredit per row keeps
        the audit trail aligned with the local ledger.

        The line posts against the ``VENDOR-CORE-CHARGE`` service item
        which must be pre-configured in QBO with the Vendor Core
        Receivable asset account (see ``qbo.account.vendor_core_receivable``
        in ``qbo.config``).
        """
        request_preview = {
            "VendorRef": {"DisplayName": credit_data.vendor_name},
            "DocNumber": credit_data.credit_number,
            "TxnDate": credit_data.txn_date.isoformat() if credit_data.txn_date else None,
            "TotalAmt": credit_data.amount,
            "PrivateNote": credit_data.memo,
            "Line": [{
                "sku": credit_data.sku,
                "qty": credit_data.quantity,
                "unit_cost": credit_data.amount / max(credit_data.quantity, 1),
                "amount": credit_data.amount,
                "description": credit_data.description,
            }],
            "linked_bill": credit_data.linked_bill_qbo_id,
        }
        log_id = self._audit_start(
            source_type="vendor_credit", source_id=source_id,
            qbo_object_type="VendorCredit", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "vendor": credit_data.vendor_name,
                    "number": credit_data.credit_number,
                    "amount": credit_data.amount,
                    "sku": credit_data.sku,
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-VC-{credit_data.credit_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.vendorcredit import VendorCredit
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import (
                ItemBasedExpenseLine,
                ItemBasedExpenseLineDetail,
            )

            vendor = self._get_or_create_vendor(credit_data.vendor_name)
            if not vendor:
                self._audit_failed(
                    log_id, f"Vendor not found: {credit_data.vendor_name}"
                )
                return {
                    "success": False,
                    "error": f"Vendor not found: {credit_data.vendor_name}",
                }

            vc = VendorCredit()
            vc.VendorRef = vendor.to_ref()
            vc.DocNumber = credit_data.credit_number[:21]
            vc.TxnDate = credit_data.txn_date.isoformat()
            if credit_data.memo:
                vc.PrivateNote = credit_data.memo[:4000]

            qbo_item = self.client.search_by_sku(credit_data.sku)
            if not qbo_item:
                self._audit_failed(
                    log_id, f"Vendor credit item not found in QBO: {credit_data.sku}"
                )
                return {
                    "success": False,
                    "error": f"Vendor credit item not found in QBO: {credit_data.sku}",
                }
            item_obj = Item.get(qbo_item["id"], qb=self.client._client)

            detail = ItemBasedExpenseLineDetail()
            detail.ItemRef = item_obj.to_ref()
            detail.Qty = credit_data.quantity or 1
            detail.UnitPrice = credit_data.amount / max(credit_data.quantity, 1)
            self._apply_tax_code(
                detail,
                self._resolve_tax_code(getattr(credit_data, "qbo_tax_code", None), None),
            )

            line = ItemBasedExpenseLine()
            line.Amount = credit_data.amount
            line.Description = credit_data.description[:4000]
            line.ItemBasedExpenseLineDetail = detail
            vc.Line = [line]

            self._apply_currency(vc, getattr(credit_data, "currency_code", None))

            # Optional: link back to the originating Bill so QBO shows
            # the credit applied against the same bill in its UI.
            if credit_data.linked_bill_qbo_id:
                try:
                    from quickbooks.objects.detailline import LinkedTxn
                    link = LinkedTxn()
                    link.TxnId = str(credit_data.linked_bill_qbo_id)
                    link.TxnType = "Bill"
                    existing = list(getattr(vc, "LinkedTxn", None) or [])
                    existing.append(link)
                    vc.LinkedTxn = existing
                except Exception as exc:
                    logger.debug("LinkedTxn(Bill) attach failed on VendorCredit: %s", exc)

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="vendor_credit",
                local_ref=str(source_id) if source_id is not None else credit_data.credit_number,
                fn=lambda: self._throttled_save(vc),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, vc)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(vc.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating vendor credit: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Bill Payment (AP) — Phase C
    # =========================================================================

    def create_bill_payment(
        self,
        payment_data: "QBOBillPaymentData",
        *,
        source_id: int | None = None,
    ) -> dict:
        """Create a QBO BillPayment paying one or more Bills.

        Same envelope as :meth:`create_payment`. The payment is linked
        to each Bill QBO id in ``bill_qbo_ids`` via ``LinkedTxn``.
        ``pay_type`` controls which sub-payload (CheckPayment vs
        CreditCardPayment) carries the bank account reference.
        """
        request_preview = {
            "VendorRef": {"DisplayName": payment_data.vendor_name},
            "TxnDate": payment_data.payment_date.isoformat() if payment_data.payment_date else None,
            "TotalAmt": payment_data.amount,
            "PayType": payment_data.pay_type,
            "BankAccountRef": payment_data.bank_account_id,
            "PaymentRefNum": payment_data.ref_number,
            "PrivateNote": payment_data.memo,
            "LinkedBills": list(payment_data.bill_qbo_ids),
        }
        log_id = self._audit_start(
            source_type="bill_payment", source_id=source_id,
            qbo_object_type="BillPayment", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "vendor": payment_data.vendor_name,
                    "number": payment_data.payment_number,
                    "amount": payment_data.amount,
                    "bills": payment_data.bill_qbo_ids,
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-BP-{payment_data.payment_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        if not payment_data.bill_qbo_ids:
            self._audit_failed(log_id, "No bills linked to BillPayment")
            return {
                "success": False,
                "error": "No bills linked to BillPayment",
            }

        try:
            from quickbooks.objects.billpayment import (
                BillPayment, BillPaymentLine,
                CheckPayment, CreditCardPayment,
            )
            from quickbooks.objects.detailline import LinkedTxn
            from quickbooks.objects.base import Ref

            # Vendor lookup: prefer cached search_by_name pattern used
            # elsewhere; fall back to direct filter.
            vendor = None
            try:
                from quickbooks.objects.vendor import Vendor
                found = Vendor.filter(
                    DisplayName=payment_data.vendor_name,
                    qb=self.client._client,
                )
                if found:
                    vendor = found[0]
            except Exception:
                pass
            if not vendor:
                self._audit_failed(
                    log_id, f"Vendor not found: {payment_data.vendor_name}"
                )
                return {
                    "success": False,
                    "error": f"Vendor not found: {payment_data.vendor_name}",
                }

            bp = BillPayment()
            bp.VendorRef = vendor.to_ref()
            bp.TxnDate = payment_data.payment_date.isoformat()
            bp.TotalAmt = payment_data.amount
            bp.PayType = payment_data.pay_type or "Check"
            if payment_data.ref_number:
                bp.PrivateNote = (payment_data.memo or "")[:4000]

            if payment_data.bank_account_id:
                acc_ref = Ref()
                acc_ref.value = str(payment_data.bank_account_id)
                if bp.PayType == "CreditCard":
                    cc = CreditCardPayment()
                    cc.CCAccountRef = acc_ref
                    bp.CreditCardPayment = cc
                else:
                    chk = CheckPayment()
                    chk.BankAccountRef = acc_ref
                    bp.CheckPayment = chk

            # One BillPaymentLine per linked Bill — split amount evenly
            # by default. Callers can pre-split by passing only ids whose
            # sum equals ``amount``; otherwise we apply the full amount
            # to the first bill (matches QBO UI behaviour for single-bill
            # payments which is the dominant case).
            lines = []
            remaining = float(payment_data.amount)
            bills = list(payment_data.bill_qbo_ids)
            for i, bill_id in enumerate(bills):
                linked = LinkedTxn()
                linked.TxnId = str(bill_id)
                linked.TxnType = "Bill"
                bpl = BillPaymentLine()
                if i == len(bills) - 1:
                    bpl.Amount = remaining
                else:
                    portion = round(payment_data.amount / len(bills), 2)
                    bpl.Amount = portion
                    remaining -= portion
                bpl.LinkedTxn = [linked]
                lines.append(bpl)
            bp.Line = lines

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="bill_payment",
                local_ref=str(source_id) if source_id is not None else payment_data.payment_number,
                fn=lambda: self._throttled_save(bp),
            )
            if not outcome.get("success"):
                self._audit_failed(
                    log_id, str(outcome.get("error") or "unknown")
                )
                return {
                    "success": False,
                    "error": outcome.get("error") or "save failed",
                }

            self._audit_success(log_id, bp)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(bp.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating bill payment: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Reporting
    # =========================================================================
    
    def get_inventory_value_report(self) -> dict:
        """
        Get inventory value report.
        
        Returns:
            {
                total_items: int,
                total_value_at_cost: float,
                total_value_at_price: float,
                by_category: {category: {count, cost, price}},
            }
        """
        if not self.connect():
            return {"error": "Failed to connect"}
        
        items = self.client.fetch_all_items()
        
        report = {
            "total_items": len(items),
            "total_value_at_cost": 0.0,
            "total_value_at_price": 0.0,
            "items_with_stock": 0,
            "items_zero_stock": 0,
        }
        
        for item in items:
            qty = float(item.get("qty_on_hand", 0))
            cost = float(item.get("cost", 0))
            price = float(item.get("price", 0))
            
            if qty > 0:
                report["items_with_stock"] += 1
                report["total_value_at_cost"] += qty * cost
                report["total_value_at_price"] += qty * price
            else:
                report["items_zero_stock"] += 1
        
        return report

    # =========================================================================
    # Estimate Operations (Quotes — Phase 5)
    # =========================================================================

    def create_estimate(self, estimate_data: QBOEstimateData, *,
                        source_id: int | None = None) -> dict:
        """Create an Estimate in QBO (quote).

        Parallels :meth:`create_invoice` — same throttle / idempotency /
        audit envelope. Returns ``{success, action, qbo_id, error?}``.
        """
        request_preview = {
            "CustomerRef": {"DisplayName": estimate_data.customer_name},
            "DocNumber": estimate_data.estimate_number,
            "TxnDate": estimate_data.estimate_date.isoformat() if estimate_data.estimate_date else None,
            "ExpirationDate": estimate_data.expiration_date.isoformat() if estimate_data.expiration_date else None,
            "TxnStatus": estimate_data.txn_status,
            "PrivateNote": estimate_data.memo,
            "TotalAmt": estimate_data.total,
            "Line": [
                {"sku": ln.sku, "qty": ln.quantity,
                 "unit_price": ln.unit_price, "amount": ln.amount,
                 "description": ln.description}
                for ln in estimate_data.lines
            ],
        }
        log_id = self._audit_start(
            source_type="quote", source_id=source_id,
            qbo_object_type="Estimate", request_payload=request_preview,
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {
                "success": True,
                "action": "skipped",
                "reason": "Write disabled",
                "would_create": {
                    "customer": estimate_data.customer_name,
                    "number": estimate_data.estimate_number,
                    "total": estimate_data.total,
                    "lines": len(estimate_data.lines),
                },
            }

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            mock_id = f"MOCK-EST-{estimate_data.estimate_number}"
            self._audit_mock(log_id, mock_id)
            return {
                "success": True,
                "action": "created",
                "qbo_id": mock_id,
                "is_mock": True,
            }

        try:
            from quickbooks.objects.estimate import Estimate
            from quickbooks.objects.item import Item
            from quickbooks.objects.detailline import (
                SalesItemLine, SalesItemLineDetail,
            )

            customer = self._get_or_create_customer(estimate_data.customer_name)
            if not customer:
                msg = f"Customer not found: {estimate_data.customer_name}"
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            est = Estimate()
            est.CustomerRef = customer.to_ref()
            est.DocNumber = estimate_data.estimate_number[:21]
            est.TxnDate = estimate_data.estimate_date.isoformat()
            if estimate_data.expiration_date:
                est.ExpirationDate = estimate_data.expiration_date.isoformat()
            if estimate_data.txn_status:
                est.TxnStatus = estimate_data.txn_status
            if estimate_data.accepted_by:
                est.AcceptedBy = estimate_data.accepted_by[:1000]
            if estimate_data.accepted_date:
                est.AcceptedDate = estimate_data.accepted_date.isoformat()
            if estimate_data.memo:
                est.PrivateNote = estimate_data.memo[:4000]

            lines = []
            doc_tax_code = getattr(estimate_data, "qbo_tax_code", None)
            for line in estimate_data.lines:
                qbo_item = self.client.search_by_sku(line.sku)
                if not qbo_item:
                    logger.warning(f"Item not found in QBO: {line.sku}")
                    continue

                item_obj = Item.get(qbo_item["id"], qb=self.client._client)

                detail = SalesItemLineDetail()
                detail.ItemRef = item_obj.to_ref()
                detail.Qty = line.quantity
                detail.UnitPrice = line.unit_price
                self._apply_tax_code(
                    detail,
                    self._resolve_tax_code(doc_tax_code,
                                           getattr(line, "qbo_tax_code", None)),
                )

                sales_line = SalesItemLine()
                sales_line.Amount = line.amount
                sales_line.Description = (line.description or "")[:4000]
                sales_line.SalesItemLineDetail = detail
                lines.append(sales_line)

            est.Line = lines

            if not lines:
                msg = ("Estimate has no QBO-mapped line items. "
                       "Push products to QBO first.")
                self._audit_failed(log_id, msg)
                return {"success": False, "error": msg}

            self._apply_currency(est, getattr(estimate_data, "currency_code", None))

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="estimate",
                local_ref=str(source_id) if source_id is not None
                          else estimate_data.estimate_number,
                fn=lambda: self._throttled_save(est),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, est)
            return {
                "success": True,
                "action": outcome.get("action", "created"),
                "qbo_id": outcome.get("qbo_id") or str(est.Id),
                "idempotency_key": outcome.get("idempotency_key"),
            }

        except Exception as e:
            logger.exception(f"Error creating estimate: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}

    def update_estimate_status(self, qbo_estimate_id: str, *,
                               txn_status: str,
                               accepted_by: str | None = None,
                               accepted_date: date | None = None,
                               source_id: int | None = None) -> dict:
        """Sparse-update an existing QBO Estimate's ``TxnStatus``.

        Used when the local quote flips Draft → Sent → Accepted /
        Rejected / Closed. Requires the QBO Id from a prior
        :meth:`create_estimate` call (or webhook pull).
        """
        log_id = self._audit_start(
            source_type="quote", source_id=source_id,
            qbo_object_type="Estimate",
            request_payload={"Id": qbo_estimate_id, "TxnStatus": txn_status},
        )

        if not self.write_enabled:
            self._audit_skipped(log_id, "Write disabled")
            return {"success": True, "action": "skipped",
                    "reason": "Write disabled"}

        if not self.connect():
            self._audit_failed(log_id, "Failed to connect to QBO")
            return {"success": False, "error": "Failed to connect to QBO"}

        if self.is_mock_mode:
            self._audit_mock(log_id, qbo_estimate_id)
            return {"success": True, "action": "updated",
                    "qbo_id": qbo_estimate_id, "is_mock": True}

        try:
            from quickbooks.objects.estimate import Estimate
            from qbo._throttle import throttle
            throttle()
            est = Estimate.get(int(qbo_estimate_id), qb=self.client._client)
            if txn_status:
                est.TxnStatus = txn_status
            if accepted_by:
                est.AcceptedBy = accepted_by[:1000]
            if accepted_date:
                est.AcceptedDate = accepted_date.isoformat()

            from qbo._retry import idempotent_write
            outcome = idempotent_write(
                entity_type="estimate_update",
                local_ref=f"{qbo_estimate_id}:{txn_status}",
                fn=lambda: self._throttled_save(est),
            )
            if not outcome.get("success"):
                self._audit_failed(log_id, str(outcome.get("error") or "unknown"))
                return {"success": False, "error": outcome.get("error") or "save failed"}

            self._audit_success(log_id, est)
            return {
                "success": True,
                "action": outcome.get("action", "updated"),
                "qbo_id": qbo_estimate_id,
            }
        except Exception as e:
            logger.exception(f"Error updating estimate status: {e}")
            self._audit_failed(log_id, f"{type(e).__name__}: {e}")
            return {"success": False, "error": str(e)}


# Singleton instance
_qbo_service: Optional[QBOIntegrationService] = None


def get_qbo_service() -> QBOIntegrationService:
    """Get or create the QBO integration service singleton."""
    global _qbo_service
    if _qbo_service is None:
        _qbo_service = QBOIntegrationService()
    return _qbo_service
