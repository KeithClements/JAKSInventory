"""QBO.10 — Core-charge → QBO line-item mapping.

Decision (codified here — do not re-litigate without updating this docstring):

    Every core charge rides as a **separate SalesItemLine** on the QBO
    invoice (never bundled into the part's unit price). The line references
    a dedicated QBO **non-inventory Service Item** named
    ``"Core Charge"`` (SKU ``CORE-CHARGE``).

Rationale
~~~~~~~~~
- Customer-paid core deposits are an **Other Current Liability** in QBO,
  not income: until the customer returns the physical core (or forfeits
  the deposit), JAK's owes that money back. The mapping is configured
  via ``qbo.account.core_liability`` in :mod:`qbo.config`; legacy
  installs may still set ``QBO_CORE_CHARGE_LIABILITY`` (or the older
  ``QBO_CORE_CHARGE_INCOME``) as an env override.
- Keeps COGS / revenue accounts clean: the part-line hits Sales Of Product
  Income + Inventory Asset + COGS; the core-line posts to the
  Core Deposits liability account.
- When the customer later returns the core, the **credit memo** references
  the same Core Charge service item so the offset nets to zero in QBO
  without any inventory gymnastics. Forfeiture is handled with a
  journal entry that moves the residual liability to "Core Forfeiture
  Income".
- Reporting: "Outstanding cores (dollars)" rolls up cleanly from QBO's
  liability balance plus this app's own snapshot reports.

What this module provides
~~~~~~~~~~~~~~~~~~~~~~~~~
``build_core_charge_line(sku, qty, core_amount, ...)``
    Return a dict shaped for :func:`create_qbo_invoice`'s ``line_items``
    parameter for **customer** invoices.

``expand_invoice_lines_with_cores(lines)``
    Convenience: takes raw invoice-line dicts and yields a fully
    expanded list with a follow-on core line for each parent line that
    has a nonzero core charge.

``build_vendor_core_bill_line(sku, qty, core_amount, ...)``
    Vendor-side analogue: when JAK's pays the supplier a core deposit
    on inbound stock, the bill carries a separate line that posts to
    the **Vendor Core Receivable** asset account (configured via
    ``qbo.account.vendor_core_receivable``). The receivable is cleared
    when the rebuilt/old core ships back and the vendor credits us.

``expand_bill_lines_with_cores(lines)``
    Vendor-side analogue of :func:`expand_invoice_lines_with_cores` for
    purchase order → bill conversion.

``get_core_charge_item_ref()`` / ``get_vendor_core_item_ref()``
    Look up (or create in mock mode) the corresponding QBO items.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

from qbo.client import get_qbo_client

logger = logging.getLogger(__name__)


# Public constants.
CORE_CHARGE_SKU = "CORE-CHARGE"
CORE_CHARGE_NAME = "Core Charge"
CORE_CHARGE_TYPE = "Service"  # non-inventory; toggles accounts required

VENDOR_CORE_SKU = "VENDOR-CORE-CHARGE"
VENDOR_CORE_NAME = "Vendor Core Deposit"


def _core_liability_account_id() -> str:
    """QBO **Other Current Liability** account id for customer core deposits.

    Resolution order:
      1. ``QBO_CORE_CHARGE_LIABILITY`` env var (operator override).
      2. ``QBO_CORE_CHARGE_INCOME`` env var (legacy alias — kept so older
         installs don't break, but the semantic is liability).
      3. ``qbo.account.core_liability`` from :mod:`qbo.config`.
      4. Hard fallback ``"1"`` so mock-mode tests stay deterministic.
    """
    override = (
        os.environ.get("QBO_CORE_CHARGE_LIABILITY", "").strip()
        or os.environ.get("QBO_CORE_CHARGE_INCOME", "").strip()
    )
    if override:
        return override
    try:
        from qbo.config import get_account_mapping
        mapped = (get_account_mapping() or {}).get("core_liability")
        if mapped:
            return str(mapped).strip()
    except Exception:
        pass
    return "1"


# Legacy alias retained for any in-tree imports that pre-date P2.
_core_income_account_id = _core_liability_account_id


def _vendor_core_receivable_account_id() -> str:
    """QBO **Other Current Asset** account id for vendor core deposits paid.

    Resolution order:
      1. ``QBO_VENDOR_CORE_RECEIVABLE`` env var (operator override).
      2. ``qbo.account.vendor_core_receivable`` from :mod:`qbo.config`.
      3. Hard fallback ``"1"`` for mock-mode tests.
    """
    override = os.environ.get("QBO_VENDOR_CORE_RECEIVABLE", "").strip()
    if override:
        return override
    try:
        from qbo.config import get_account_mapping
        mapped = (get_account_mapping() or {}).get("vendor_core_receivable")
        if mapped:
            return str(mapped).strip()
    except Exception:
        pass
    return "1"


def get_core_charge_item_ref() -> dict[str, Any]:
    """Return the QBO item reference for the Core Charge line item.

    In mock mode this returns a stable fake id so tests are deterministic.
    In live mode, looks up by SKU; does NOT auto-create (a service item
    with the right account mappings must exist in QBO — a one-time setup
    step).
    """
    client = get_qbo_client()
    if not client.is_connected:
        client.connect()

    if client.is_mock_mode:
        return {"id": "MOCK-CORE-ITEM", "sku": CORE_CHARGE_SKU,
                "name": CORE_CHARGE_NAME, "mock": True}

    existing = client.search_by_sku(CORE_CHARGE_SKU)
    if existing:
        return {
            "id": existing.get("id"),
            "sku": CORE_CHARGE_SKU,
            "name": existing.get("name") or CORE_CHARGE_NAME,
        }
    logger.warning(
        "[QBO] Core Charge item '%s' not found in QBO. Create it as a "
        "Service item mapped to the Core Charge income account.",
        CORE_CHARGE_SKU,
    )
    return {"id": None, "sku": CORE_CHARGE_SKU, "name": CORE_CHARGE_NAME,
            "missing": True}


def build_core_charge_line(
    parent_sku: str,
    quantity: float,
    core_amount: float,
    *,
    description_suffix: str = "",
) -> dict[str, Any]:
    """Build a single QBO invoice line representing a core deposit.

    Parameters
    ----------
    parent_sku:
        The SKU of the part the core rides along with (for trace/memo only).
    quantity:
        Units sold — same qty as the parent line (one core per unit).
    core_amount:
        Per-unit core charge in dollars.
    description_suffix:
        Optional extra text appended to the line description.

    Returns
    -------
    dict shaped for :func:`qbo.invoices.create_qbo_invoice`'s ``line_items``
    list: ``{sku, qbo_item_id, description, quantity, unit_price, amount}``.
    """
    qty = float(quantity or 0)
    amt = float(core_amount or 0)
    if qty <= 0 or amt <= 0:
        # Caller is responsible for filtering, but defend anyway.
        raise ValueError("quantity and core_amount must both be > 0")

    ref = get_core_charge_item_ref()
    desc = f"Core deposit for {parent_sku}"
    if description_suffix:
        desc = f"{desc} — {description_suffix}"
    return {
        "sku": CORE_CHARGE_SKU,
        "qbo_item_id": ref.get("id"),
        "description": desc[:4000],
        "quantity": qty,
        "unit_price": amt,
        "amount": round(qty * amt, 2),
        "is_core_charge": True,
    }


def expand_invoice_lines_with_cores(lines: Iterable[dict]) -> list[dict]:
    """Expand raw invoice lines so that each parent with a ``core_charge``
    gets a follow-on core-charge line.

    Input line schema (subset): ``{sku, qty | quantity, core_charge, ...}``.
    Output preserves the original order with the core line inserted
    immediately after its parent.
    """
    out: list[dict] = []
    for line in lines or []:
        out.append(line)
        core = float(line.get("core_charge") or 0)
        qty = float(line.get("qty") or line.get("quantity") or 0)
        parent_sku = line.get("sku") or ""
        if core > 0 and qty > 0:
            try:
                out.append(build_core_charge_line(parent_sku, qty, core))
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------------------
# P3 — Vendor-side (purchase) core symmetry.
# ---------------------------------------------------------------------------
def get_vendor_core_item_ref() -> dict[str, Any]:
    """Return the QBO item reference for the Vendor Core Deposit item.

    A separate item (distinct from the customer-facing ``CORE-CHARGE``)
    is used so QBO's Purchases-by-Item / Sales-by-Item reports keep the
    two flows audit-distinct: customer cores are a **liability** until
    refunded, vendor cores are an **asset** (receivable from supplier)
    until the old core ships back and the vendor issues credit.
    """
    client = get_qbo_client()
    if not client.is_connected:
        client.connect()

    if client.is_mock_mode:
        return {"id": "MOCK-VENDOR-CORE-ITEM", "sku": VENDOR_CORE_SKU,
                "name": VENDOR_CORE_NAME, "mock": True}

    existing = client.search_by_sku(VENDOR_CORE_SKU)
    if existing:
        return {
            "id": existing.get("id"),
            "sku": VENDOR_CORE_SKU,
            "name": existing.get("name") or VENDOR_CORE_NAME,
        }
    logger.warning(
        "[QBO] Vendor Core item '%s' not found in QBO. Create it as a "
        "Service item mapped to the Vendor Core Receivable asset account.",
        VENDOR_CORE_SKU,
    )
    return {"id": None, "sku": VENDOR_CORE_SKU, "name": VENDOR_CORE_NAME,
            "missing": True}


def build_vendor_core_bill_line(
    parent_sku: str,
    quantity: float,
    core_amount: float,
    *,
    description_suffix: str = "",
) -> dict[str, Any]:
    """Build a single QBO bill line representing a vendor core deposit.

    Posts to the Vendor Core Receivable account (current asset). Caller
    appends the returned dict to its bill ``line_items`` list — analogue
    of :func:`build_core_charge_line` for the purchasing side.
    """
    qty = float(quantity or 0)
    amt = float(core_amount or 0)
    if qty <= 0 or amt <= 0:
        raise ValueError("quantity and core_amount must both be > 0")

    ref = get_vendor_core_item_ref()
    desc = f"Vendor core deposit for {parent_sku}"
    if description_suffix:
        desc = f"{desc} — {description_suffix}"
    return {
        "sku": VENDOR_CORE_SKU,
        "qbo_item_id": ref.get("id"),
        "description": desc[:4000],
        "quantity": qty,
        # The vendor-side bill builder in jaks_inventory/qbo/bills.py reads
        # ``unit_cost`` (purchase semantics); the customer-side invoice
        # builder reads ``unit_price``. Emit both keys so this dict drops
        # cleanly into either pipeline.
        "unit_price": amt,
        "unit_cost": amt,
        "amount": round(qty * amt, 2),
        "is_vendor_core_charge": True,
    }


def expand_bill_lines_with_cores(lines: Iterable[dict]) -> list[dict]:
    """Expand raw bill/PO lines so each parent with a ``core_charge``
    gets a follow-on vendor-core-deposit line.

    Mirrors :func:`expand_invoice_lines_with_cores` but for bills.
    """
    out: list[dict] = []
    for line in lines or []:
        out.append(line)
        core = float(line.get("core_charge") or 0)
        qty = float(line.get("qty") or line.get("quantity") or 0)
        parent_sku = line.get("sku") or ""
        if core > 0 and qty > 0:
            try:
                out.append(build_vendor_core_bill_line(parent_sku, qty, core))
            except ValueError:
                continue
    return out
