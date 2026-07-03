"""QBO.8 — Customer sync + crosswalk reconciliation.

Two-way(ish) reconciliation between ``customers.qbo_customer_id`` and
QBO Customer records. This module is **idempotent** (wraps writes with
:mod:`qbo._retry`) and respects the central mock / write toggle in
:mod:`qbo.config`.

Public API
----------
sync_customer_to_qbo(customer_id)         — push one local customer to QBO
sync_all_customers(...)                    — bulk push
reconcile_customer_crosswalk()             — diff-only audit report
fetch_qbo_customers()                      — cached list of QBO customers
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from qbo.client import QBOClient, get_qbo_client
from qbo.config import is_mock_mode, is_write_enabled
from qbo._retry import idempotent_write
from qbo import terms as _terms

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared payload builder — single source of truth for QBO Customer fields.
# ---------------------------------------------------------------------------

def _truncate(val: Any, n: int) -> str | None:
    """Return ``str(val)`` clipped to ``n`` chars or ``None`` when empty."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return s[:n]


def apply_customer_fields(customer, local: dict | None) -> None:
    """Copy every supported field from a local ``customers`` row onto a
    ``quickbooks.objects.customer.Customer`` instance.

    Used by both the standalone push path (:func:`_create_qbo_customer_raw`)
    AND the integration layer's :class:`QBOIntegration._get_or_create_customer`
    so the two paths can't drift apart again. The function is **additive**:
    it never clears a QBO field — it only writes when the local row has a
    non-empty value. That keeps a re-sync safe when the local DB has less
    information than QBO (e.g. user enriched a customer directly in QBO).
    """
    if not local:
        return
    try:
        from quickbooks.objects.base import Address, EmailAddress, PhoneNumber
    except Exception:  # pragma: no cover — SDK missing
        return

    # --- Display / company / contact name ---------------------------------
    contact = (local.get("name") or "").strip()
    company = (local.get("company") or "").strip()
    display = company or contact
    if display:
        customer.DisplayName = display[:500]
    if company:
        customer.CompanyName = company[:100]
    if contact:
        parts = contact.split(None, 1)
        customer.GivenName = parts[0][:25]
        if len(parts) > 1:
            customer.FamilyName = parts[1][:25]

    # --- Email -------------------------------------------------------------
    email = _truncate(local.get("email"), 100)
    if email:
        ea = EmailAddress()
        ea.Address = email
        customer.PrimaryEmailAddr = ea

    # --- Primary + Mobile phone -------------------------------------------
    phone = _truncate(local.get("phone") or local.get("business_phone"), 30)
    if phone:
        pn = PhoneNumber()
        pn.FreeFormNumber = phone
        customer.PrimaryPhone = pn
    mobile = _truncate(local.get("mobile_phone"), 30)
    if mobile:
        mp = PhoneNumber()
        mp.FreeFormNumber = mobile
        customer.Mobile = mp

    # --- Billing address ---------------------------------------------------
    line1 = (local.get("address") or local.get("bill_to_address") or "").strip()
    city = (local.get("city") or local.get("bill_to_city") or "").strip()
    state = (local.get("state") or local.get("bill_to_state") or "").strip()
    zip_ = (local.get("zip") or local.get("bill_to_zip") or "").strip()
    country = (local.get("country") or "").strip()
    if any([line1, city, state, zip_]):
        addr = Address()
        if line1:
            addr.Line1 = line1[:500]
        if city:
            addr.City = city[:100]
        if state:
            addr.CountrySubDivisionCode = state[:50]
        if zip_:
            addr.PostalCode = zip_[:30]
        if country:
            addr.Country = country[:100]
        customer.BillAddr = addr

    # --- Shipping address (falls back to billing) -------------------------
    s_line1 = (local.get("ship_to_address") or line1 or "").strip()
    s_city = (local.get("ship_to_city") or city or "").strip()
    s_state = (local.get("ship_to_state") or state or "").strip()
    s_zip = (local.get("ship_to_zip") or zip_ or "").strip()
    if any([s_line1, s_city, s_state, s_zip]):
        ship = Address()
        if s_line1:
            ship.Line1 = s_line1[:500]
        if s_city:
            ship.City = s_city[:100]
        if s_state:
            ship.CountrySubDivisionCode = s_state[:50]
        if s_zip:
            ship.PostalCode = s_zip[:30]
        if country:
            ship.Country = country[:100]
        customer.ShipAddr = ship

    # --- Payment terms via SalesTermRef -----------------------------------
    term_ref = _terms.sales_term_ref(local.get("payment_terms"))
    if term_ref:
        customer.SalesTermRef = term_ref
    elif local.get("payment_terms"):
        logger.info(
            "[QBO] payment term %r not found in QBO; skipping SalesTermRef "
            "for customer id=%s",
            local.get("payment_terms"), local.get("id"),
        )

    # --- Notes + Taxable flag ---------------------------------------------
    notes = _truncate(local.get("notes"), 2000)
    if notes:
        customer.Notes = notes
    if local.get("tax_exempt"):
        customer.Taxable = False


# Back-compat alias (private name used inside this module historically).
_apply_customer_fields = apply_customer_fields


def _full_local_customer(customer_id: int | None) -> dict | None:
    """Fetch the rich row used by the payload builder + importers."""
    if not customer_id:
        return None
    try:
        from db.inventory import get_customer
        return get_customer(int(customer_id))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _client() -> QBOClient:
    c = get_qbo_client()
    if not c.is_connected:
        c.connect()
    return c


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy crosswalk matching."""
    if not name:
        return ""
    return " ".join(str(name).strip().lower().split())


def _get_mock_qbo_customers() -> list[dict]:
    """Deterministic stub list used in mock mode."""
    return [
        {
            "id": "C-100", "display_name": "Acme Fleet Services",
            "company_name": "Acme Fleet Services",
            "email": "[email protected]",
            "phone": "555-0100", "mobile": "",
            "bill_addr": {"line1": "100 Acme Way", "city": "Dallas",
                          "state": "TX", "zip": "75201", "country": "USA"},
            "ship_addr": None,
            "terms_id": None, "terms_name": "Net 30",
            "balance": 0.0, "active": True, "notes": "",
        },
        {
            "id": "C-101", "display_name": "Big Rig Repair LLC",
            "company_name": "Big Rig Repair LLC",
            "email": "[email protected]",
            "phone": "555-0101", "mobile": "",
            "bill_addr": {"line1": "200 Big Rig Rd", "city": "Houston",
                          "state": "TX", "zip": "77001", "country": "USA"},
            "ship_addr": None,
            "terms_id": None, "terms_name": "Net 30",
            "balance": 250.50, "active": True, "notes": "",
        },
        {
            "id": "C-102", "display_name": "Diesel Depot",
            "company_name": "Diesel Depot",
            "email": "[email protected]",
            "phone": "555-0102", "mobile": "",
            "bill_addr": None, "ship_addr": None,
            "terms_id": None, "terms_name": "Due on receipt",
            "balance": 0.0, "active": True, "notes": "",
        },
    ]


def _addr_to_dict(addr) -> dict | None:
    """Convert a QBO Address object to a flat dict (or ``None``)."""
    if not addr:
        return None
    out = {
        "line1": getattr(addr, "Line1", "") or "",
        "city": getattr(addr, "City", "") or "",
        "state": getattr(addr, "CountrySubDivisionCode", "") or "",
        "zip": getattr(addr, "PostalCode", "") or "",
        "country": getattr(addr, "Country", "") or "",
    }
    if not any(out.values()):
        return None
    return out


def _customer_to_dict(c) -> dict:
    """Map a QBO Customer SDK object to the importer's rich shape."""
    bill = _addr_to_dict(getattr(c, "BillAddr", None))
    ship = _addr_to_dict(getattr(c, "ShipAddr", None))
    terms_ref = getattr(c, "SalesTermRef", None)
    terms_id = getattr(terms_ref, "value", None) if terms_ref else None
    return {
        "id": str(c.Id),
        "display_name": c.DisplayName or "",
        "company_name": getattr(c, "CompanyName", "") or "",
        "email": (getattr(getattr(c, "PrimaryEmailAddr", None),
                          "Address", None) or ""),
        "phone": (getattr(getattr(c, "PrimaryPhone", None),
                          "FreeFormNumber", None) or ""),
        "mobile": (getattr(getattr(c, "Mobile", None),
                           "FreeFormNumber", None) or ""),
        "bill_addr": bill,
        "ship_addr": ship,
        "terms_id": str(terms_id) if terms_id else None,
        "terms_name": _terms.resolve_term_name(terms_id) if terms_id else None,
        "balance": float(getattr(c, "Balance", 0) or 0),
        "active": bool(getattr(c, "Active", True)),
        "notes": getattr(c, "Notes", "") or "",
    }


def fetch_qbo_customers() -> list[dict]:
    """Return all QBO customers as a list of rich dicts.

    Each entry has: ``id, display_name, company_name, email, phone, mobile,
    bill_addr, ship_addr, terms_id, terms_name, balance, active, notes``.
    ``bill_addr`` / ``ship_addr`` are either ``None`` or
    ``{line1, city, state, zip, country}``. The shape is intentionally
    JSON-friendly so the importer + webhook handler share one
    transformation.
    """
    client = _client()
    if client.is_mock_mode:
        return _get_mock_qbo_customers()
    try:
        from quickbooks.objects.customer import Customer
        from qbo._throttle import throttle
        throttle()
        customers = Customer.all(qb=client._client, max_results=1000)
        return [_customer_to_dict(c) for c in customers]
    except Exception as e:
        logger.warning("[QBO] fetch_qbo_customers failed: %s", e)
        return []


def _find_qbo_customer_by_id(qbo_id: str) -> dict | None:
    if not qbo_id:
        return None
    client = _client()
    if client.is_mock_mode:
        # Stub catalog
        for c in _get_mock_qbo_customers():
            if c["id"] == str(qbo_id):
                return c
        # Synthetic ids minted by sync_customer_to_qbo in mock mode use the
        # stable "MOCK-CUST-<local_id>" format — treat them as "exists" so
        # idempotent re-sync dedupes instead of re-creating.
        if str(qbo_id).startswith("MOCK-CUST-"):
            return {"id": str(qbo_id), "display_name": "", "email": "",
                    "active": True, "synthetic": True}
        return None
    try:
        from quickbooks.objects.customer import Customer
        c = Customer.get(str(qbo_id), qb=client._client)
        return _customer_to_dict(c)
    except Exception:
        return None


def _find_qbo_customer_by_name(name: str) -> dict | None:
    if not name:
        return None
    target = _normalize_name(name)
    for c in fetch_qbo_customers():
        if _normalize_name(c.get("display_name", "")) == target:
            return c
    return None


# ---------------------------------------------------------------------------
# Write: create / update single customer
# ---------------------------------------------------------------------------

def _create_qbo_customer_raw(local: dict) -> str:
    """Create a QBO Customer from a local customers row. Returns new QBO id."""
    from qbo import sync_log
    client = _client()
    # Build a dict snapshot of the would-be payload for the audit log.
    display_name = (local.get("company") or local.get("name") or "").strip()[:500]
    request_payload = {
        "DisplayName": display_name,
        "company": local.get("company"),
        "email": local.get("email"),
        "phone": local.get("phone"),
        "mobile_phone": local.get("mobile_phone"),
        "address": local.get("address"),
        "city": local.get("city"),
        "state": local.get("state"),
        "zip": local.get("zip"),
        "payment_terms": local.get("payment_terms"),
    }
    log_id = sync_log.start_log(
        source_type="customer",
        source_id=local.get("id"),
        qbo_object_type="Customer",
        request_json=sync_log.to_json_text(request_payload),
    )
    if client.is_mock_mode:
        mock_id = f"MOCK-CUST-{local.get('id')}"
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": mock_id, "_mock": True}),
            qbo_object_id=mock_id,
        )
        return mock_id
    try:
        from quickbooks.objects.customer import Customer
        cust = Customer()
        # Single source of truth: apply every supported field via the shared builder.
        apply_customer_fields(cust, local)
        # Defensive: ensure DisplayName is set even if local row was empty.
        if not getattr(cust, "DisplayName", None):
            cust.DisplayName = (display_name or f"Customer {local.get('id')}")[:500]
        sync_log.update_request(
            log_id,
            request_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(cust)),
        )
        saved = cust.save(qb=client._client)
        qbo_id = str(saved.Id)
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(saved)),
            qbo_object_id=qbo_id,
        )
        return qbo_id
    except Exception as exc:
        sync_log.complete_log(
            log_id, status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise


def _update_qbo_customer_raw(local: dict, qbo_id: str) -> str:
    """Update an existing QBO Customer with the latest local fields.

    Returns the QBO id (unchanged on success). Used by
    :func:`sync_customer_to_qbo` when the local row is already linked so a
    re-sync actually pushes address/phone/term/etc updates instead of being
    a no-op.
    """
    from qbo import sync_log
    client = _client()
    request_payload = {"qbo_id": qbo_id, "op": "update",
                       "company": local.get("company"),
                       "phone": local.get("phone"),
                       "city": local.get("city"),
                       "payment_terms": local.get("payment_terms")}
    log_id = sync_log.start_log(
        source_type="customer", source_id=local.get("id"),
        qbo_object_type="Customer",
        request_json=sync_log.to_json_text(request_payload),
    )
    if client.is_mock_mode:
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": qbo_id, "_mock": True}),
            qbo_object_id=str(qbo_id),
        )
        return str(qbo_id)
    try:
        from quickbooks.objects.customer import Customer
        from qbo._throttle import throttle
        throttle()
        existing = Customer.get(str(qbo_id), qb=client._client)
        apply_customer_fields(existing, local)
        throttle()
        saved = existing.save(qb=client._client)
        new_id = str(saved.Id)
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(saved)),
            qbo_object_id=new_id,
        )
        return new_id
    except Exception as exc:
        sync_log.complete_log(
            log_id, status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
        )
        raise


def sync_customer_to_qbo(customer_id: int) -> dict[str, Any]:
    """Push a local customer to QBO (create if missing, link qbo_customer_id).

    Idempotent: a second call with the same ``customer_id`` returns the
    already-linked QBO id without a second write.
    """
    from db import get_customer, update_customer

    if not is_write_enabled() and not is_mock_mode():
        return {"success": False, "error": "QBO writes disabled"}

    local = get_customer(customer_id)
    if not local:
        return {"success": False, "error": f"Customer {customer_id} not found"}

    # Already linked? Verify the QBO record still exists.
    existing_qbo_id = (local.get("qbo_customer_id") or "").strip()
    if existing_qbo_id:
        remote = _find_qbo_customer_by_id(existing_qbo_id)
        if remote:
            # Push latest local fields (address / phone / terms) into QBO.
            # Synthetic mock ids skip the live update path automatically.
            try:
                _update_qbo_customer_raw(local, existing_qbo_id)
                return {
                    "success": True,
                    "action": "updated",
                    "qbo_id": existing_qbo_id,
                }
            except Exception as exc:
                logger.warning("[QBO] update failed for customer %s: %s",
                               customer_id, exc)
                return {
                    "success": True,
                    "action": "already_linked",
                    "qbo_id": existing_qbo_id,
                    "warning": str(exc),
                }
        # Stale link — fall through to recreate.
        logger.info("[QBO] Stale qbo_customer_id for customer %s; re-syncing",
                    customer_id)

    # Look for an existing QBO customer with the same name (pre-link).
    display_name = local.get("company") or local.get("name") or ""
    remote = _find_qbo_customer_by_name(display_name)
    if remote:
        update_customer(customer_id, qbo_customer_id=remote["id"])
        return {"success": True, "action": "linked_existing", "qbo_id": remote["id"]}

    # Create via idempotency + retry guard.
    outcome = idempotent_write(
        entity_type="customer",
        local_ref=str(customer_id),
        fn=lambda: _create_qbo_customer_raw(local),
    )
    if outcome.get("success") and outcome.get("qbo_id"):
        try:
            update_customer(customer_id, qbo_customer_id=outcome["qbo_id"])
        except Exception as e:
            logger.warning("[QBO] wrote QBO but failed to update local link: %s", e)
        return {
            "success": True,
            "action": outcome.get("action", "created"),
            "qbo_id": outcome["qbo_id"],
        }
    return {"success": False, "error": outcome.get("error") or "unknown"}


def sync_all_customers(
    *,
    active_only: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Push every local customer missing or with a stale ``qbo_customer_id`` to QBO."""
    from db import get_all_customers

    customers = get_all_customers(active_only=active_only)

    # Fetch all valid QBO IDs once so we can classify stale links cheaply
    # without an individual API call per customer.
    qbo_rows = fetch_qbo_customers()
    valid_qbo_ids = {c["id"] for c in qbo_rows}

    pending_no_id = [c for c in customers if not (c.get("qbo_customer_id") or "").strip()]
    pending_stale = [
        c for c in customers
        if (c.get("qbo_customer_id") or "").strip()
        and c["qbo_customer_id"] not in valid_qbo_ids
    ]
    pending_update = [
        c for c in customers
        if (c.get("qbo_customer_id") or "").strip()
        and c["qbo_customer_id"] in valid_qbo_ids
    ]
    # Push every local customer on each run so edits flow to QBO. Order:
    # creates first, then stale relinks, then updates of already-linked rows.
    pending = pending_no_id + pending_stale + pending_update
    logger.info(
        "[QBO] sync_all_customers: %d new, %d stale, %d updates → %d total",
        len(pending_no_id), len(pending_stale), len(pending_update),
        len(pending),
    )

    total = len(pending)
    result = {"total": total, "created": 0, "linked": 0, "updated": 0,
              "skipped": 0, "errors": []}
    for i, c in enumerate(pending, 1):
        if progress_callback:
            progress_callback(i, total, c.get("name") or str(c.get("id")))
        r = sync_customer_to_qbo(c["id"])
        if r.get("success"):
            action = r.get("action")
            if action == "linked_existing":
                result["linked"] += 1
            elif action in ("created", "deduped"):
                result["created"] += 1
            elif action == "updated":
                result["updated"] += 1
            else:
                result["skipped"] += 1
        else:
            err = r.get("error") or ""
            result["errors"].append(
                f"{c.get('name') or c.get('id')}: {err}"
            )
            # Abort immediately on auth failure — no point hammering QBO
            if "401" in str(err) or "auth" in str(err).lower():
                result["errors"].append("Aborted: authentication failure. Re-connect QBO in Settings.")
                break
    return result


# ---------------------------------------------------------------------------
# Crosswalk reconciliation report
# ---------------------------------------------------------------------------

def reconcile_customer_crosswalk(*, active_only: bool = True) -> dict[str, Any]:
    """Audit the customers ↔ QBO link without writing anything.

    Returns a report dict::

        {
            "summary": { ... counts ... },
            "missing_link":       [{id, name, company}]     # local w/o qbo_id
            "stale_link":         [{id, name, qbo_id}]      # qbo_id no longer in QBO
            "orphan_in_qbo":      [{qbo_id, display_name}]  # QBO rec w/ no local match
            "name_mismatch":      [{id, local, remote, qbo_id}]
        }
    """
    from db import get_all_customers

    local_rows = get_all_customers(active_only=active_only)
    qbo_rows = fetch_qbo_customers()
    qbo_by_id = {c["id"]: c for c in qbo_rows}
    qbo_by_name = {
        _normalize_name(c["display_name"]): c
        for c in qbo_rows
        if c.get("display_name")
    }
    linked_qbo_ids: set[str] = set()

    missing_link: list[dict] = []
    stale_link: list[dict] = []
    name_mismatch: list[dict] = []

    for row in local_rows:
        qbo_id = (row.get("qbo_customer_id") or "").strip()
        name = row.get("company") or row.get("name") or ""
        if not qbo_id:
            missing_link.append({
                "id": row["id"],
                "name": row.get("name"),
                "company": row.get("company"),
            })
            continue
        remote = qbo_by_id.get(qbo_id)
        if not remote:
            stale_link.append({
                "id": row["id"],
                "name": name,
                "qbo_id": qbo_id,
            })
            continue
        linked_qbo_ids.add(qbo_id)
        if _normalize_name(remote["display_name"]) != _normalize_name(name):
            name_mismatch.append({
                "id": row["id"],
                "local": name,
                "remote": remote["display_name"],
                "qbo_id": qbo_id,
            })

    orphan_in_qbo: list[dict] = []
    local_names = {_normalize_name(r.get("company") or r.get("name") or "")
                   for r in local_rows}
    for c in qbo_rows:
        if c["id"] in linked_qbo_ids:
            continue
        if _normalize_name(c.get("display_name") or "") in local_names:
            # Would-be-linkable but not yet linked — still flag as orphan so
            # the user can trigger sync_all_customers.
            pass
        orphan_in_qbo.append({
            "qbo_id": c["id"],
            "display_name": c.get("display_name"),
        })

    return {
        "summary": {
            "local_total": len(local_rows),
            "qbo_total": len(qbo_rows),
            "linked": len(linked_qbo_ids),
            "missing_link": len(missing_link),
            "stale_link": len(stale_link),
            "orphan_in_qbo": len(orphan_in_qbo),
            "name_mismatch": len(name_mismatch),
        },
        "missing_link": missing_link,
        "stale_link": stale_link,
        "orphan_in_qbo": orphan_in_qbo,
        "name_mismatch": name_mismatch,
    }


# ---------------------------------------------------------------------------
# Import QBO → local
# ---------------------------------------------------------------------------

def _apply_qbo_customer_to_local(local_id: int, qc: dict) -> None:
    """Write a rich QBO customer dict onto the local ``customers`` row.

    Skips empty fields so we don't blow away locally-entered data with
    blanks from QBO. Stamps ``qbo_last_pulled_at`` for diagnostics.
    Shared between :func:`import_customers_from_qbo` and the webhook
    handler so they can't diverge.
    """
    from db.inventory import update_customer
    from datetime import datetime

    fields: dict[str, Any] = {"qbo_last_pulled_at": datetime.now().isoformat()}

    if qc.get("company_name"):
        fields["company"] = qc["company_name"]
    # Fall back to display name for ``name`` when no contact name is known.
    if qc.get("display_name"):
        fields["name"] = qc["display_name"]
    if qc.get("email"):
        fields["email"] = qc["email"]
    if qc.get("phone"):
        fields["phone"] = qc["phone"]
    if qc.get("mobile"):
        fields["mobile_phone"] = qc["mobile"]
    bill = qc.get("bill_addr") or {}
    if bill.get("line1"):
        fields["address"] = bill["line1"]
    if bill.get("city"):
        fields["city"] = bill["city"]
    if bill.get("state"):
        fields["state"] = bill["state"]
    if bill.get("zip"):
        fields["zip"] = bill["zip"]
    if bill.get("country"):
        fields["country"] = bill["country"]
    if qc.get("terms_name"):
        fields["payment_terms"] = qc["terms_name"]
    if "balance" in qc:
        fields["balance"] = float(qc.get("balance") or 0)
    if qc.get("notes"):
        fields["notes"] = qc["notes"]
    if "active" in qc:
        fields["is_active"] = bool(qc["active"])

    try:
        update_customer(local_id, **fields)
    except Exception as exc:
        logger.warning("[QBO] _apply_qbo_customer_to_local id=%s failed: %s",
                       local_id, exc)


def import_customers_from_qbo(
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Pull every QBO customer and create / update the local row.

    For each QBO customer:
      * If a local row already has ``qbo_customer_id`` matching → update it
        in place with the latest address / phone / terms / balance.
      * Else if a local row's display name matches → link **and** update.
      * Otherwise → create a new local row, link it, and stamp all fields.

    Returns ``{total, created, linked, updated, skipped, errors}``.
    """
    from db.inventory import create_customer, get_all_customers, update_customer

    qbo_rows = fetch_qbo_customers()
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "linked": 0, "updated": 0,
        "skipped": 0, "errors": [],
    }

    if not qbo_rows:
        result["errors"].append("No customers returned from QBO — check connection.")
        return result

    logger.info("[QBO] import_customers_from_qbo: %d QBO customers fetched", total)

    local_rows = get_all_customers(active_only=False)
    logger.info("[QBO] import_customers_from_qbo: %d local customers in DB",
                len(local_rows))
    by_qbo_id: dict[str, dict] = {
        r["qbo_customer_id"]: r
        for r in local_rows
        if r.get("qbo_customer_id")
    }
    by_name: dict[str, dict] = {
        _normalize_name(r.get("company") or r.get("name") or ""): r
        for r in local_rows
    }

    for i, qc in enumerate(qbo_rows, 1):
        qbo_id = str(qc["id"])
        display_name = (qc.get("display_name") or qc.get("company_name") or "").strip()
        if progress_callback:
            progress_callback(i, total, display_name)

        # Already linked → update in place.
        if qbo_id in by_qbo_id:
            local = by_qbo_id[qbo_id]
            try:
                _apply_qbo_customer_to_local(local["id"], qc)
                result["updated"] += 1
            except Exception as exc:
                result["errors"].append(f"{display_name}: update failed — {exc}")
            continue

        # Local record with matching name → link + update.
        norm = _normalize_name(display_name)
        if norm and norm in by_name:
            local = by_name[norm]
            try:
                update_customer(local["id"], qbo_customer_id=qbo_id)
                _apply_qbo_customer_to_local(local["id"], qc)
                by_qbo_id[qbo_id] = local
                result["linked"] += 1
            except Exception as exc:
                result["errors"].append(f"{display_name}: link failed — {exc}")
            continue

        # Create new local customer + apply all fields.
        try:
            new_id = create_customer(
                name=display_name or f"QBO Customer {qbo_id}",
                email=qc.get("email") or None,
            )
            update_customer(new_id, qbo_customer_id=qbo_id)
            _apply_qbo_customer_to_local(new_id, qc)
            by_qbo_id[qbo_id] = {"id": new_id}
            if norm:
                by_name[norm] = {"id": new_id}
            result["created"] += 1
        except Exception as exc:
            result["errors"].append(f"{display_name}: create failed — {exc}")

    logger.info(
        "[QBO] import_customers_from_qbo complete: %d created, %d linked, "
        "%d updated, %d skipped, %d errors",
        result["created"], result["linked"], result["updated"],
        result["skipped"], len(result["errors"]),
    )
    return result


# Back-compat alias matching the plan's naming.
import_qbo_customers_to_db = import_customers_from_qbo
