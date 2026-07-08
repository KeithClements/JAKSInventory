"""QBO reference-data pull (Phase D).

Caches three QBO catalogs locally so the UI can render dropdowns and
push code can validate ``class_id`` / ``location_id`` / ``qbo_tax_code``
without round-tripping to the API on every keystroke.

Catalogs:
    * TaxCode  → ``qbo_tax_codes``
    * Class    → ``qbo_classes``
    * Location → ``qbo_locations``

All three follow the same shape: ``fetch_qbo_<x>()`` returns a list of
dicts from QBO (mock-aware), and ``import_qbo_<x>_to_db()`` upserts
into the local cache table keyed by ``qbo_id``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from qbo.client import QBOClient, get_qbo_client

logger = logging.getLogger(__name__)


def _client() -> QBOClient:
    c = get_qbo_client()
    if not c.is_connected:
        c.connect()
    return c


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------

def _mock_tax_codes() -> list[dict]:
    return [
        {"qbo_id": "TAX",  "name": "TAX",  "description": "Taxable",
         "taxable": True,  "rate": 7.25,  "active": True},
        {"qbo_id": "NON",  "name": "NON",  "description": "Non-taxable",
         "taxable": False, "rate": 0.0,   "active": True},
        {"qbo_id": "OUT",  "name": "OUT_OF_STATE",
         "description": "Out of state — no tax",
         "taxable": False, "rate": 0.0,   "active": True},
    ]


def _mock_classes() -> list[dict]:
    return [
        {"qbo_id": "C-1", "name": "Retail",    "fully_qualified_name": "Retail",
         "parent_qbo_id": None, "active": True},
        {"qbo_id": "C-2", "name": "Wholesale", "fully_qualified_name": "Wholesale",
         "parent_qbo_id": None, "active": True},
        {"qbo_id": "C-3", "name": "Online",
         "fully_qualified_name": "Retail:Online",
         "parent_qbo_id": "C-1", "active": True},
    ]


def _mock_locations() -> list[dict]:
    return [
        {"qbo_id": "L-1", "name": "Main Yard",
         "fully_qualified_name": "Main Yard",
         "parent_qbo_id": None, "active": True},
        {"qbo_id": "L-2", "name": "Warehouse B",
         "fully_qualified_name": "Warehouse B",
         "parent_qbo_id": None, "active": True},
    ]


# ---------------------------------------------------------------------------
# Fetch (live + mock)
# ---------------------------------------------------------------------------

def _safe_filter_all(sdk_cls, qb_client) -> list:
    from qbo._throttle import throttle
    throttle()
    try:
        return list(sdk_cls.all(qb=qb_client, max_results=1000))
    except Exception as exc:
        logger.warning(
            "[QBO] %s.all failed: %s", sdk_cls.__name__, exc
        )
        return []


def fetch_qbo_tax_codes() -> list[dict]:
    """Return active QBO TaxCode catalog (mock-aware)."""
    client = _client()
    if client.is_mock_mode:
        return _mock_tax_codes()
    try:
        from quickbooks.objects.taxcode import TaxCode
        rows = _safe_filter_all(TaxCode, client._client)
        out = []
        for tc in rows:
            rate = None
            try:
                # TaxRateList → first SalesTaxRateDetail.TaxRateRef.value;
                # rate lives on TaxRate itself. We surface only an
                # informational summary — push uses the TaxCode id.
                detail_list = getattr(
                    getattr(tc, "SalesTaxRateList", None), "TaxRateDetail", []
                )
                if detail_list:
                    rate_ref = getattr(detail_list[0], "TaxRateRef", None)
                    if rate_ref and getattr(rate_ref, "name", None):
                        # Best-effort numeric rate parse, ignore failure.
                        try:
                            from quickbooks.objects.taxrate import TaxRate
                            tr = TaxRate.get(rate_ref.value, qb=client._client)
                            rate = float(getattr(tr, "RateValue", 0) or 0)
                        except Exception:
                            rate = None
            except Exception:
                rate = None
            out.append({
                "qbo_id": str(getattr(tc, "Id", "")),
                "name": getattr(tc, "Name", "") or "",
                "description": getattr(tc, "Description", "") or "",
                "taxable": bool(getattr(tc, "Taxable", False)),
                "rate": rate,
                "active": bool(getattr(tc, "Active", True)),
            })
        return out
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_tax_codes failed: %s", exc)
        return []


def fetch_qbo_classes() -> list[dict]:
    """Return active QBO Class catalog (mock-aware)."""
    client = _client()
    if client.is_mock_mode:
        return _mock_classes()
    try:
        from quickbooks.objects.classes import Class
    except Exception:
        # Older quickbooks-python uses qbo class location
        try:
            from quickbooks.objects.class_ import Class  # type: ignore
        except Exception:
            logger.warning("[QBO] Class SDK class unavailable")
            return []
    rows = _safe_filter_all(Class, client._client)
    out = []
    for c in rows:
        parent = getattr(c, "ParentRef", None)
        out.append({
            "qbo_id": str(getattr(c, "Id", "")),
            "name": getattr(c, "Name", "") or "",
            "fully_qualified_name": getattr(c, "FullyQualifiedName", "") or "",
            "parent_qbo_id": str(parent.value) if parent else None,
            "active": bool(getattr(c, "Active", True)),
        })
    return out


def fetch_qbo_locations() -> list[dict]:
    """Return active QBO Department/Location catalog (mock-aware)."""
    client = _client()
    if client.is_mock_mode:
        return _mock_locations()
    try:
        from quickbooks.objects.department import Department
    except Exception:
        logger.warning("[QBO] Department SDK class unavailable")
        return []
    rows = _safe_filter_all(Department, client._client)
    out = []
    for d in rows:
        parent = getattr(d, "ParentRef", None)
        out.append({
            "qbo_id": str(getattr(d, "Id", "")),
            "name": getattr(d, "Name", "") or "",
            "fully_qualified_name": getattr(d, "FullyQualifiedName", "") or "",
            "parent_qbo_id": str(parent.value) if parent else None,
            "active": bool(getattr(d, "Active", True)),
        })
    return out


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------

def _upsert_ref_rows(
    conn, table: str, rows: list[dict], columns: list[str],
) -> dict[str, int]:
    """Upsert a list of dicts into a reference table keyed by qbo_id.

    All non-key columns are overwritten on every pull (catalog mirror —
    if QBO disabled a tax code we want that reflected locally).
    """
    now = datetime.utcnow().isoformat()
    created = updated = 0
    for row in rows:
        qbo_id = str(row.get("qbo_id") or "").strip()
        if not qbo_id:
            continue
        existing = conn.execute(
            f"SELECT id FROM {table} WHERE qbo_id = %s LIMIT 1",
            (qbo_id,),
        ).fetchone()
        values: list = []
        sets: list[str] = []
        for c in columns:
            v = row.get(c)
            if isinstance(v, bool):
                v = 1 if v else 0
            values.append(v)
            sets.append(f"{c}=%s")
        if existing:
            conn.execute(
                f"UPDATE {table} SET {', '.join(sets)}, "
                f"qbo_pulled_at=%s, updated_at=%s WHERE qbo_id=%s",
                (*values, now, now, qbo_id),
            )
            updated += 1
        else:
            col_list = ["qbo_id", *columns, "qbo_pulled_at"]
            placeholders = ", ".join("%s" for _ in col_list)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(col_list)}) "
                f"VALUES ({placeholders})",
                (qbo_id, *values, now),
            )
            created += 1
    try:
        conn.commit()
    except Exception:
        pass
    return {"created": created, "updated": updated}


def import_qbo_tax_codes_to_db(
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Mirror the QBO TaxCode catalog into ``qbo_tax_codes``."""
    from db.database import get_db
    rows = fetch_qbo_tax_codes()
    cols = ["name", "description", "taxable", "rate", "active"]
    with get_db() as conn:
        stats = _upsert_ref_rows(conn, "qbo_tax_codes", rows, cols)
    return {"total": len(rows), **stats, "skipped": 0, "errors": []}


def import_qbo_classes_to_db(
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Mirror the QBO Class catalog into ``qbo_classes``."""
    from db.database import get_db
    rows = fetch_qbo_classes()
    cols = ["name", "fully_qualified_name", "parent_qbo_id", "active"]
    with get_db() as conn:
        stats = _upsert_ref_rows(conn, "qbo_classes", rows, cols)
    return {"total": len(rows), **stats, "skipped": 0, "errors": []}


def import_qbo_locations_to_db(
    *,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Mirror the QBO Department/Location catalog into ``qbo_locations``."""
    from db.database import get_db
    rows = fetch_qbo_locations()
    cols = ["name", "fully_qualified_name", "parent_qbo_id", "active"]
    with get_db() as conn:
        stats = _upsert_ref_rows(conn, "qbo_locations", rows, cols)
    return {"total": len(rows), **stats, "skipped": 0, "errors": []}
