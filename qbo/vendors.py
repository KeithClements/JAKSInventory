"""QBO Vendor sync + import.

Mirror of :mod:`qbo.customers` for the ``vendors`` table.

Public API
----------
fetch_qbo_vendors()             — list rich vendor dicts from QBO
sync_vendor_to_qbo(vendor_id)   — push one local vendor (create + update)
sync_all_vendors(...)           — bulk push
import_vendors_from_qbo(...)    — bulk pull QBO → local
apply_vendor_fields(vendor, local)
                                — shared payload builder used by this
                                  module AND :class:`qbo.integration.QBOIntegration`
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from qbo.client import QBOClient, get_qbo_client
from qbo.config import is_mock_mode, is_write_enabled
from qbo._retry import idempotent_write
from qbo import terms as _terms

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client() -> QBOClient:
    c = get_qbo_client()
    if not c.is_connected:
        c.connect()
    return c


def _normalize_name(name: Any) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _truncate(val: Any, n: int) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return s[:n]


def _addr_to_dict(addr) -> dict | None:
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


# ---------------------------------------------------------------------------
# Shared payload builder — single source of truth for Vendor fields
# ---------------------------------------------------------------------------

def apply_vendor_fields(vendor, local: dict | None) -> None:
    """Copy supported local vendor row fields onto a QBO Vendor SDK object.

    Used by :func:`_create_qbo_vendor_raw` here and by
    :meth:`qbo.integration.QBOIntegration._apply_vendor_fields` (which
    delegates to this function) so the two push paths can't drift apart.
    """
    if not local:
        return
    try:
        from quickbooks.objects.base import Address, EmailAddress, PhoneNumber
    except Exception:  # pragma: no cover
        return

    name = (local.get("name") or "").strip()
    contact = (local.get("contact_name") or "").strip()
    display = name or contact
    if display:
        vendor.DisplayName = display[:500]
    if name:
        vendor.CompanyName = name[:100]
    if contact:
        parts = contact.split(None, 1)
        vendor.GivenName = parts[0][:25]
        if len(parts) > 1:
            vendor.FamilyName = parts[1][:25]

    email = _truncate(local.get("contact_email"), 100)
    if email:
        ea = EmailAddress()
        ea.Address = email
        vendor.PrimaryEmailAddr = ea

    phone = _truncate(local.get("contact_phone"), 30)
    if phone:
        pn = PhoneNumber()
        pn.FreeFormNumber = phone
        vendor.PrimaryPhone = pn

    # Address — try structured columns first, fall back to free-form ``address``.
    line1 = (local.get("address") or "").strip()
    city = (local.get("city") or "").strip()
    state = (local.get("state") or "").strip()
    zip_ = (local.get("zip") or "").strip()
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
        vendor.BillAddr = addr

    term_ref = _terms.sales_term_ref(local.get("payment_terms"))
    if term_ref:
        vendor.TermRef = term_ref
    elif local.get("payment_terms"):
        logger.info(
            "[QBO] payment term %r not found in QBO; skipping TermRef "
            "for vendor id=%s",
            local.get("payment_terms"), local.get("id"),
        )

    notes = _truncate(local.get("notes"), 2000)
    if notes:
        vendor.Notes = notes

    # 1099 + account-number passthrough
    if local.get("is_1099"):
        try:
            vendor.Vendor1099 = True
        except Exception:
            pass
    acct = _truncate(local.get("account_number"), 25)
    if acct:
        try:
            vendor.AcctNum = acct
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Read helpers (QBO → dict)
# ---------------------------------------------------------------------------

def _vendor_to_dict(v) -> dict:
    """Map a QBO Vendor SDK object to the importer's rich shape."""
    bill = _addr_to_dict(getattr(v, "BillAddr", None))
    term_ref = getattr(v, "TermRef", None)
    term_id = getattr(term_ref, "value", None) if term_ref else None
    return {
        "id": str(v.Id),
        "display_name": getattr(v, "DisplayName", "") or "",
        "company_name": getattr(v, "CompanyName", "") or "",
        "given_name": getattr(v, "GivenName", "") or "",
        "family_name": getattr(v, "FamilyName", "") or "",
        "email": (getattr(getattr(v, "PrimaryEmailAddr", None),
                          "Address", None) or ""),
        "phone": (getattr(getattr(v, "PrimaryPhone", None),
                          "FreeFormNumber", None) or ""),
        "bill_addr": bill,
        "terms_id": str(term_id) if term_id else None,
        "terms_name": _terms.resolve_term_name(term_id) if term_id else None,
        "balance": float(getattr(v, "Balance", 0) or 0),
        "active": bool(getattr(v, "Active", True)),
        "notes": getattr(v, "Notes", "") or "",
        "acct_num": getattr(v, "AcctNum", "") or "",
        "is_1099": bool(getattr(v, "Vendor1099", False)),
    }


def _get_mock_qbo_vendors() -> list[dict]:
    """Deterministic stub list used in mock mode."""
    return [
        {
            "id": "V-200", "display_name": "Diesel Parts Wholesale",
            "company_name": "Diesel Parts Wholesale",
            "given_name": "", "family_name": "",
            "email": "[email protected]", "phone": "555-0200",
            "bill_addr": {"line1": "1 Warehouse Way", "city": "Memphis",
                          "state": "TN", "zip": "38103", "country": "USA"},
            "terms_id": None, "terms_name": "Net 30",
            "balance": 0.0, "active": True, "notes": "",
            "acct_num": "", "is_1099": False,
        },
        {
            "id": "V-201", "display_name": "Cummins OEM",
            "company_name": "Cummins OEM",
            "given_name": "", "family_name": "",
            "email": "[email protected]", "phone": "555-0201",
            "bill_addr": None,
            "terms_id": None, "terms_name": "Net 15",
            "balance": 1200.00, "active": True, "notes": "",
            "acct_num": "", "is_1099": False,
        },
    ]


def fetch_qbo_vendors() -> list[dict]:
    """Return all QBO vendors as rich dicts (see :func:`_vendor_to_dict`)."""
    client = _client()
    if client.is_mock_mode:
        return _get_mock_qbo_vendors()
    try:
        from quickbooks.objects.vendor import Vendor
        from qbo._throttle import throttle
        throttle()
        vendors = Vendor.all(qb=client._client, max_results=1000)
        return [_vendor_to_dict(v) for v in vendors]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_vendors failed: %s", exc)
        return []


def _find_qbo_vendor_by_id(qbo_id: str) -> dict | None:
    if not qbo_id:
        return None
    client = _client()
    if client.is_mock_mode:
        for v in _get_mock_qbo_vendors():
            if v["id"] == str(qbo_id):
                return v
        if str(qbo_id).startswith("MOCK-VEND-"):
            return {"id": str(qbo_id), "display_name": "", "active": True,
                    "synthetic": True}
        return None
    try:
        from quickbooks.objects.vendor import Vendor
        v = Vendor.get(str(qbo_id), qb=client._client)
        return _vendor_to_dict(v)
    except Exception:
        return None


def _find_qbo_vendor_by_name(name: str) -> dict | None:
    if not name:
        return None
    target = _normalize_name(name)
    for v in fetch_qbo_vendors():
        if _normalize_name(v.get("display_name", "")) == target:
            return v
    return None


# ---------------------------------------------------------------------------
# Push: local → QBO
# ---------------------------------------------------------------------------

def _create_qbo_vendor_raw(local: dict) -> str:
    """Create a QBO Vendor from a local vendors row. Returns new QBO id."""
    from qbo import sync_log
    client = _client()
    display_name = (local.get("name") or local.get("contact_name") or "").strip()[:500]
    request_payload = {
        "DisplayName": display_name,
        "name": local.get("name"),
        "contact_email": local.get("contact_email"),
        "contact_phone": local.get("contact_phone"),
        "address": local.get("address"),
        "city": local.get("city"),
        "state": local.get("state"),
        "zip": local.get("zip"),
        "payment_terms": local.get("payment_terms"),
    }
    log_id = sync_log.start_log(
        source_type="vendor", source_id=local.get("id"),
        qbo_object_type="Vendor",
        request_json=sync_log.to_json_text(request_payload),
    )
    if client.is_mock_mode:
        mock_id = f"MOCK-VEND-{local.get('id')}"
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": mock_id, "_mock": True}),
            qbo_object_id=mock_id,
        )
        return mock_id
    try:
        from quickbooks.objects.vendor import Vendor
        v = Vendor()
        apply_vendor_fields(v, local)
        if not getattr(v, "DisplayName", None):
            v.DisplayName = (display_name or f"Vendor {local.get('id')}")[:500]
        sync_log.update_request(
            log_id,
            request_json=sync_log.to_json_text(sync_log.qbo_obj_to_dict(v)),
        )
        saved = v.save(qb=client._client)
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


def _update_qbo_vendor_raw(local: dict, qbo_id: str) -> str:
    """Update an existing QBO Vendor with current local fields."""
    from qbo import sync_log
    client = _client()
    log_id = sync_log.start_log(
        source_type="vendor", source_id=local.get("id"),
        qbo_object_type="Vendor",
        request_json=sync_log.to_json_text({"qbo_id": qbo_id, "op": "update"}),
    )
    if client.is_mock_mode:
        sync_log.complete_log(
            log_id, status="success", response_status=200,
            response_json=sync_log.to_json_text({"Id": qbo_id, "_mock": True}),
            qbo_object_id=str(qbo_id),
        )
        return str(qbo_id)
    try:
        from quickbooks.objects.vendor import Vendor
        from qbo._throttle import throttle
        throttle()
        existing = Vendor.get(str(qbo_id), qb=client._client)
        apply_vendor_fields(existing, local)
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


def sync_vendor_to_qbo(vendor_id: int) -> dict[str, Any]:
    """Push one local vendor to QBO. Idempotent."""
    from db.inventory import get_vendor, update_vendor

    if not is_write_enabled() and not is_mock_mode():
        return {"success": False, "error": "QBO writes disabled"}

    local = get_vendor(vendor_id)
    if not local:
        return {"success": False, "error": f"Vendor {vendor_id} not found"}

    existing_qbo_id = (local.get("qbo_vendor_id") or "").strip()
    if existing_qbo_id:
        remote = _find_qbo_vendor_by_id(existing_qbo_id)
        if remote:
            try:
                _update_qbo_vendor_raw(local, existing_qbo_id)
                return {"success": True, "action": "updated",
                        "qbo_id": existing_qbo_id}
            except Exception as exc:
                return {"success": True, "action": "already_linked",
                        "qbo_id": existing_qbo_id, "warning": str(exc)}
        logger.info("[QBO] Stale qbo_vendor_id for vendor %s; re-syncing",
                    vendor_id)

    display = local.get("name") or local.get("contact_name") or ""
    remote = _find_qbo_vendor_by_name(display)
    if remote:
        update_vendor(vendor_id, qbo_vendor_id=remote["id"])
        return {"success": True, "action": "linked_existing",
                "qbo_id": remote["id"]}

    outcome = idempotent_write(
        entity_type="vendor",
        local_ref=str(vendor_id),
        fn=lambda: _create_qbo_vendor_raw(local),
    )
    if outcome.get("success") and outcome.get("qbo_id"):
        try:
            update_vendor(vendor_id, qbo_vendor_id=outcome["qbo_id"])
        except Exception as exc:
            logger.warning("[QBO] wrote QBO vendor but failed to update local link: %s", exc)
        return {"success": True, "action": outcome.get("action", "created"),
                "qbo_id": outcome["qbo_id"]}
    return {"success": False, "error": outcome.get("error") or "unknown"}


def sync_all_vendors(
    *,
    active_only: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Push every local vendor missing or with a stale qbo_vendor_id to QBO."""
    from db.inventory import get_all_vendors

    vendors = get_all_vendors(active_only=active_only)
    qbo_rows = fetch_qbo_vendors()
    valid_qbo_ids = {v["id"] for v in qbo_rows}

    pending_no_id = [v for v in vendors if not (v.get("qbo_vendor_id") or "").strip()]
    pending_stale = [
        v for v in vendors
        if (v.get("qbo_vendor_id") or "").strip()
        and v["qbo_vendor_id"] not in valid_qbo_ids
    ]
    pending_update = [
        v for v in vendors
        if (v.get("qbo_vendor_id") or "").strip()
        and v["qbo_vendor_id"] in valid_qbo_ids
    ]
    pending = pending_no_id + pending_stale + pending_update
    logger.info(
        "[QBO] sync_all_vendors: %d new, %d stale, %d updates → %d total",
        len(pending_no_id), len(pending_stale), len(pending_update),
        len(pending),
    )

    total = len(pending)
    result = {"total": total, "created": 0, "linked": 0, "updated": 0,
              "skipped": 0, "errors": []}
    for i, v in enumerate(pending, 1):
        if progress_callback:
            progress_callback(i, total, v.get("name") or str(v.get("id")))
        r = sync_vendor_to_qbo(v["id"])
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
            result["errors"].append(f"{v.get('name') or v.get('id')}: {err}")
            if "401" in str(err) or "auth" in str(err).lower():
                result["errors"].append(
                    "Aborted: authentication failure. Re-connect QBO in Settings."
                )
                break
    return result


# ---------------------------------------------------------------------------
# Pull: QBO → local
# ---------------------------------------------------------------------------

def _apply_qbo_vendor_to_local(local_id: int, qv: dict) -> None:
    """Write a rich QBO vendor dict onto the local ``vendors`` row.

    Shared between :func:`import_vendors_from_qbo` and the webhook
    handler. Skips empty fields so local data isn't blown away by
    blanks from QBO.
    """
    from db.inventory import update_vendor

    fields: dict[str, Any] = {"qbo_last_pulled_at": datetime.now().isoformat()}

    if qv.get("display_name") or qv.get("company_name"):
        fields["name"] = qv.get("company_name") or qv.get("display_name")
    # Combine given+family into contact_name when present.
    full_contact = " ".join(
        p for p in [qv.get("given_name"), qv.get("family_name")] if p
    ).strip()
    if full_contact:
        fields["contact_name"] = full_contact
    if qv.get("email"):
        fields["contact_email"] = qv["email"]
    if qv.get("phone"):
        fields["contact_phone"] = qv["phone"]
    bill = qv.get("bill_addr") or {}
    if bill.get("line1"):
        fields["address"] = bill["line1"]
    if bill.get("city"):
        fields["city"] = bill["city"]
    if bill.get("state"):
        fields["state"] = bill["state"]
    if bill.get("zip"):
        fields["zip"] = bill["zip"]
    if qv.get("terms_name"):
        fields["payment_terms"] = qv["terms_name"]
    if "balance" in qv:
        fields["balance"] = float(qv.get("balance") or 0)
    if qv.get("notes"):
        fields["notes"] = qv["notes"]
    if "active" in qv:
        fields["is_active"] = bool(qv["active"])
    if qv.get("acct_num"):
        fields["account_number"] = qv["acct_num"]
    if "is_1099" in qv:
        fields["is_1099"] = bool(qv["is_1099"])

    try:
        update_vendor(local_id, **fields)
    except Exception as exc:
        logger.warning("[QBO] _apply_qbo_vendor_to_local id=%s failed: %s",
                       local_id, exc)


def import_vendors_from_qbo(
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Pull every QBO vendor and create / update the local row."""
    from db.inventory import create_vendor, get_all_vendors, update_vendor

    qbo_rows = fetch_qbo_vendors()
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "linked": 0, "updated": 0,
        "skipped": 0, "errors": [],
    }
    if not qbo_rows:
        result["errors"].append("No vendors returned from QBO — check connection.")
        return result

    local_rows = get_all_vendors(active_only=False)
    by_qbo_id: dict[str, dict] = {
        r["qbo_vendor_id"]: r for r in local_rows if r.get("qbo_vendor_id")
    }
    by_name: dict[str, dict] = {
        _normalize_name(r.get("name") or ""): r for r in local_rows
    }

    for i, qv in enumerate(qbo_rows, 1):
        qbo_id = str(qv["id"])
        display = (qv.get("display_name") or qv.get("company_name") or "").strip()
        if progress_callback:
            progress_callback(i, total, display)

        if qbo_id in by_qbo_id:
            local = by_qbo_id[qbo_id]
            try:
                _apply_qbo_vendor_to_local(local["id"], qv)
                result["updated"] += 1
            except Exception as exc:
                result["errors"].append(f"{display}: update failed — {exc}")
            continue

        norm = _normalize_name(display)
        if norm and norm in by_name:
            local = by_name[norm]
            try:
                update_vendor(local["id"], qbo_vendor_id=qbo_id)
                _apply_qbo_vendor_to_local(local["id"], qv)
                by_qbo_id[qbo_id] = local
                result["linked"] += 1
            except Exception as exc:
                result["errors"].append(f"{display}: link failed — {exc}")
            continue

        try:
            create_result = create_vendor(
                name=display or f"QBO Vendor {qbo_id}",
                contact_email=qv.get("email") or None,
                contact_phone=qv.get("phone") or None,
                qbo_vendor_id=qbo_id,
            )
            if not create_result.get("success"):
                result["errors"].append(
                    f"{display}: create failed — {create_result.get('error')}"
                )
                continue
            new_id = create_result["vendor_id"]
            _apply_qbo_vendor_to_local(new_id, qv)
            by_qbo_id[qbo_id] = {"id": new_id}
            if norm:
                by_name[norm] = {"id": new_id}
            result["created"] += 1
        except Exception as exc:
            result["errors"].append(f"{display}: create failed — {exc}")

    logger.info(
        "[QBO] import_vendors_from_qbo: %d created, %d linked, %d updated, "
        "%d errors", result["created"], result["linked"], result["updated"],
        len(result["errors"]),
    )
    return result


# Back-compat alias matching the plan's naming.
import_qbo_vendors_to_db = import_vendors_from_qbo


__all__ = [
    "apply_vendor_fields",
    "fetch_qbo_vendors",
    "sync_vendor_to_qbo",
    "sync_all_vendors",
    "import_vendors_from_qbo",
    "import_qbo_vendors_to_db",
    "_apply_qbo_vendor_to_local",
    "_vendor_to_dict",
]
