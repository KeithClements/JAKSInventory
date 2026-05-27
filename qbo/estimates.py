"""QBO Estimate pull.

Push lives in :mod:`qbo.push` (``push_quotes_to_qbo``) — converts local
``quotes`` rows to QBO ``Estimate`` objects and stamps
``quotes.qbo_estimate_id``.

This module adds the inbound path: estimates entered or modified
directly in QBO sync their header (status, total, dates) back to the
linked local quote. Match key: ``quotes.qbo_estimate_id``.

Lines are intentionally **not** overwritten on pull — local lines carry
product_id / serial / warranty / cost links that QBO doesn't model and
clobbering them would break SO conversion.

Public API
----------
fetch_qbo_estimates(customer_qbo_id=None) → list[dict]
import_qbo_estimates_to_db(...)           → reconcile linked quotes
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


def _date_str(val) -> str | None:
    if val is None or val == "":
        return None
    if isinstance(val, str):
        return val[:10]
    try:
        return val.strftime("%Y-%m-%d")
    except Exception:
        return str(val)[:10]


def _estimate_to_dict(est) -> dict:
    cust_ref = getattr(est, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    return {
        "id": str(est.Id),
        "doc_number": getattr(est, "DocNumber", "") or "",
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(est, "TxnDate", None)),
        "expiration_date": _date_str(getattr(est, "ExpirationDate", None)),
        "total": float(getattr(est, "TotalAmt", 0) or 0),
        "txn_status": (getattr(est, "TxnStatus", "") or "").strip(),
        "private_note": getattr(est, "PrivateNote", "") or "",
    }


def _get_mock_qbo_estimates() -> list[dict]:
    return [
        {
            "id": "EST-4001", "doc_number": "Q-1042",
            "customer_qbo_id": "C-100",
            "customer_name": "Acme Fleet Services",
            "txn_date": "2026-01-15", "expiration_date": "2026-02-14",
            "total": 2150.0, "txn_status": "Accepted",
            "private_note": "",
        },
    ]


def fetch_qbo_estimates(customer_qbo_id: str | None = None) -> list[dict]:
    """Fetch estimates from QBO."""
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_estimates()
        if customer_qbo_id:
            return [r for r in rows
                    if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.estimate import Estimate
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            ests = Estimate.filter(
                CustomerRef=str(customer_qbo_id),
                qb=client._client, max_results=500,
            )
        else:
            ests = Estimate.all(qb=client._client, max_results=1000)
        return [_estimate_to_dict(e) for e in ests]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_estimates failed: %s", exc)
        return []


# QBO TxnStatus → local quotes.status
_QBO_TXN_STATUS_TO_LOCAL = {
    "Pending": "sent",
    "Accepted": "accepted",
    "Closed": "invoiced",
    "Rejected": "rejected",
}


def import_qbo_estimates_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Reconcile QBO Estimate header changes onto linked local quotes.

    Match rule: ``quotes.qbo_estimate_id`` == ``Estimate.Id``.

    Estimates with no matching local quote are tracked in ``skipped``.
    We never auto-create local quotes from QBO (they require
    customer_id linkage + product_id lookups that QBO doesn't carry).

    Updates: ``quotes.total``, ``quotes.status`` (when QBO advances
    Pending → Accepted/Closed/Rejected), ``qbo_estimate_pulled_at``.

    Returns ``{total, matched, skipped, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_estimates(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "matched": 0, "skipped": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, qe in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qe.get("doc_number") or qe["id"])
            qbo_id = str(qe["id"])
            try:
                row = conn.execute(
                    "SELECT id, status FROM quotes WHERE qbo_estimate_id = %s LIMIT 1",
                    (qbo_id,),
                ).fetchone()
                if not row:
                    result["skipped"] += 1
                    continue
                q_id = row["id"] if hasattr(row, "keys") else row[0]
                local_status = (row["status"] if hasattr(row, "keys")
                                else row[1]) or "draft"

                qbo_status = qe.get("txn_status") or ""
                mapped = _QBO_TXN_STATUS_TO_LOCAL.get(qbo_status)
                # Only advance status forward — don't roll an invoiced
                # quote back to "sent" because someone reopened it in QBO.
                terminal = {"invoiced", "rejected", "lost", "void"}
                if mapped and local_status not in terminal:
                    new_status = mapped
                else:
                    new_status = local_status

                conn.execute(
                    """UPDATE quotes SET
                          total = %s,
                          status = %s,
                          qbo_estimate_pulled_at = %s,
                          updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (qe["total"], new_status, now_iso, q_id),
                )
                result["matched"] += 1
            except Exception as exc:
                result["errors"].append(
                    f"{qe.get('doc_number') or qbo_id}: {exc}"
                )

        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_estimates_to_db: %d matched, %d skipped, %d errors",
        result["matched"], result["skipped"], len(result["errors"]),
    )
    return result


__all__ = ["fetch_qbo_estimates", "import_qbo_estimates_to_db"]
