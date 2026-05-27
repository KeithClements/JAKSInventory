"""Build deep links into the QuickBooks Online web UI.

The QBO web app uses different hosts for sandbox and production but
the same path scheme. Given an entity type + QBO id we can construct
a URL the user can click to view the record in QBO directly.

Used by:
  * The Sync Center failures dialog ("Open in QBO" hyperlinks).
  * Per-document overflow menus (quote / invoice / PO / bill).

This module is intentionally pure — no DB or network access — so it
can be unit-tested without QBO credentials.
"""
from __future__ import annotations

from typing import Optional


# QBO object type → URL slug used in app.qbo.intuit.com
# Sourced from Intuit's documented entity routes; keep in alphabetical
# order so additions are easy to spot in diffs.
_OBJECT_SLUGS: dict[str, str] = {
    "Bill":                "bill",
    "CreditMemo":          "creditmemo",
    "Customer":            "customerdetail",
    "Estimate":            "estimate",
    "Invoice":             "invoice",
    "InventoryAdjustment": "inventoryqtyadjust",
    "Item":                "items",
    "JournalEntry":        "journal",
    "Payment":             "recvpayment",
    "PurchaseOrder":       "purchaseorder",
    "RefundReceipt":       "refundreceipt",
    "SalesReceipt":        "salesreceipt",
    "Vendor":              "vendordetail",
    "VendorCredit":        "vendorcredit",
}


def _base_host(environment: Optional[str]) -> str:
    """Return the QBO web host for the given environment.

    Defaults to the production host so accidental ``None`` does not
    point users at the wrong tenant.
    """
    env = (environment or "production").strip().lower()
    if env in ("sandbox", "sb"):
        return "https://app.sandbox.qbo.intuit.com"
    return "https://app.qbo.intuit.com"


def qbo_web_url(
    realm_id: str,
    environment: str | None,
    object_type: str,
    qbo_id: str | int,
) -> str | None:
    """Return the QBO web URL for an entity, or ``None`` if not buildable.

    Returns ``None`` when any of the required pieces is missing or
    ``object_type`` is not in our known map — callers should treat that
    as "no deep link available" rather than as an error.

    Examples
    --------
    >>> qbo_web_url("1234567890", "production", "Invoice", "42")
    'https://app.qbo.intuit.com/app/invoice?txnId=42'
    >>> qbo_web_url("1234567890", "sandbox", "Customer", 7)
    'https://app.sandbox.qbo.intuit.com/app/customerdetail?nameId=7'
    """
    if not realm_id or not object_type or qbo_id is None:
        return None
    qbo_id_str = str(qbo_id).strip()
    if not qbo_id_str or qbo_id_str.lower().startswith("mock"):
        # Mock ids ("MOCK-INV-...") never resolve in the QBO web app.
        return None

    slug = _OBJECT_SLUGS.get(object_type)
    if not slug:
        return None

    host = _base_host(environment)
    # Name-list entities use `nameId`; transactions use `txnId`.
    is_name_list = object_type in ("Customer", "Vendor", "Item")
    param = "nameId" if is_name_list else "txnId"
    return f"{host}/app/{slug}?{param}={qbo_id_str}"


def qbo_company_url(realm_id: str, environment: str | None) -> str | None:
    """Return the QBO dashboard URL for a company (no entity)."""
    if not realm_id:
        return None
    host = _base_host(environment)
    return f"{host}/app/homepage"


__all__ = ["qbo_web_url", "qbo_company_url"]
