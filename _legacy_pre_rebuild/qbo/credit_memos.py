"""QBO Credit Memo pull.

Push lives in :mod:`qbo.push` (``push_credits_to_qbo``). This module
adds the inbound path so credit memos issued in QuickBooks Online flow
back into JAKS as ``customer_credits`` rows.

Public API
----------
fetch_qbo_credit_memos(customer_qbo_id=None) \u2192 list[dict]
import_qbo_credit_memos_to_db(...)            \u2192 upsert into customer_credits
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


def _credit_line_to_dict(line) -> dict | None:
    detail_type = getattr(line, "DetailType", "") or ""
    if "SalesItemLine" not in detail_type:
        return None
    detail = getattr(line, "SalesItemLineDetail", None)
    item_ref = getattr(detail, "ItemRef", None) if detail else None
    qbo_item_name = getattr(item_ref, "name", None) if item_ref else None
    qty = float(getattr(detail, "Qty", 0) or 0) if detail else 0.0
    amt = float(getattr(line, "Amount", 0) or 0)
    return {
        "sku": (qbo_item_name or "").strip(),
        "quantity": qty,
        "amount": amt,
    }


def _credit_to_dict(cm) -> dict:
    cust_ref = getattr(cm, "CustomerRef", None)
    cust_id = getattr(cust_ref, "value", None) if cust_ref else None
    cust_name = getattr(cust_ref, "name", None) if cust_ref else None
    raw_lines = list(getattr(cm, "Line", None) or [])
    lines = [d for d in (_credit_line_to_dict(l) for l in raw_lines) if d]
    return {
        "id": str(cm.Id),
        "doc_number": getattr(cm, "DocNumber", "") or "",
        "customer_qbo_id": str(cust_id) if cust_id else None,
        "customer_name": cust_name or "",
        "txn_date": _date_str(getattr(cm, "TxnDate", None)),
        "total": float(getattr(cm, "TotalAmt", 0) or 0),
        "remaining_credit": float(getattr(cm, "RemainingCredit", 0) or 0),
        "private_note": getattr(cm, "PrivateNote", "") or "",
        "customer_memo": (
            getattr(getattr(cm, "CustomerMemo", None), "value", None) or ""
        ),
        "lines": lines,
    }


def _get_mock_qbo_credits() -> list[dict]:
    return [
        {
            "id": "CM-5001", "doc_number": "CM-1001",
            "customer_qbo_id": "C-100", "customer_name": "Acme Fleet Services",
            "txn_date": "2025-12-20",
            "total": 75.0, "remaining_credit": 75.0,
            "private_note": "", "customer_memo": "Core return credit",
            "lines": [
                {"sku": "CORE-CHARGE", "quantity": 1, "amount": 75.0},
            ],
        },
    ]


def fetch_qbo_credit_memos(customer_qbo_id: str | None = None) -> list[dict]:
    """Fetch credit memos from QBO."""
    client = _client()
    if client.is_mock_mode:
        rows = _get_mock_qbo_credits()
        if customer_qbo_id:
            return [r for r in rows if r["customer_qbo_id"] == str(customer_qbo_id)]
        return rows
    try:
        from quickbooks.objects.creditmemo import CreditMemo
        from qbo._throttle import throttle
        throttle()
        if customer_qbo_id:
            credits = CreditMemo.filter(CustomerRef=str(customer_qbo_id),
                                        qb=client._client, max_results=500)
        else:
            credits = CreditMemo.all(qb=client._client, max_results=1000)
        return [_credit_to_dict(c) for c in credits]
    except Exception as exc:
        logger.warning("[QBO] fetch_qbo_credit_memos failed: %s", exc)
        return []


def import_qbo_credit_memos_to_db(
    *,
    customer_qbo_id: str | None = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict[str, Any]:
    """Upsert QBO credit memos into ``customer_credits``.

    Identification key: ``customer_credits.qbo_credit_id`` (set on push
    via :func:`qbo.push.push_credits_to_qbo`, also set here on first
    inbound import).

    Side-effect (B4): when a CreditMemo carries a CORE-CHARGE / CORE-CREDIT
    line, any matching open ``core_returns`` rows for the customer are
    marked ``status='credited'`` with ``credit_issued=line.amount`` and
    ``qbo_credit_id`` stamped for traceability.

    Returns ``{total, created, updated, skipped, core_returns_linked, errors}``.
    """
    from db.database import get_db

    qbo_rows = fetch_qbo_credit_memos(customer_qbo_id=customer_qbo_id)
    total = len(qbo_rows)
    result: dict[str, Any] = {
        "total": total, "created": 0, "updated": 0, "skipped": 0,
        "core_returns_linked": 0, "errors": [],
    }
    if not qbo_rows:
        return result

    now_iso = datetime.now().isoformat()

    with get_db() as conn:
        for i, qc in enumerate(qbo_rows, 1):
            if progress_callback:
                progress_callback(i, total, qc.get("doc_number") or qc["id"])

            qbo_credit_id = str(qc["id"])
            qbo_cust = qc.get("customer_qbo_id")
            try:
                # Find local customer
                cust_row = None
                if qbo_cust:
                    cust_row = conn.execute(
                        "SELECT id FROM customers WHERE qbo_customer_id = %s LIMIT 1",
                        (str(qbo_cust),),
                    ).fetchone()
                if not cust_row:
                    result["skipped"] += 1
                    continue
                customer_id = (
                    cust_row["id"] if hasattr(cust_row, "keys") else cust_row[0]
                )

                # Find existing credit by qbo_credit_id
                existing = conn.execute(
                    "SELECT id, status FROM customer_credits "
                    "WHERE qbo_credit_id = %s LIMIT 1",
                    (qbo_credit_id,),
                ).fetchone()

                remaining = float(qc.get("remaining_credit") or 0)
                total_amt = float(qc.get("total") or 0)
                status = "applied" if remaining <= 0.005 and total_amt > 0 else "open"

                if existing:
                    ec_id = (
                        existing["id"] if hasattr(existing, "keys") else existing[0]
                    )
                    conn.execute(
                        """UPDATE customer_credits SET
                              amount = %s,
                              status = CASE WHEN status = 'void' THEN status
                                            ELSE %s END,
                              qbo_last_pulled_at = %s,
                              source = COALESCE(NULLIF(source,''), 'qbo'),
                              updated_at = CURRENT_TIMESTAMP
                           WHERE id = %s""",
                        (total_amt, status, now_iso, ec_id),
                    )
                    result["updated"] += 1
                else:
                    conn.execute(
                        """INSERT INTO customer_credits (
                              customer_id, source_type, source_id,
                              amount, status,
                              issue_date, notes,
                              qbo_credit_id, qbo_last_pulled_at, source
                           ) VALUES (%s, 'qbo_credit_memo', NULL,
                                     %s, %s, %s, %s,
                                     %s, %s, 'qbo')""",
                        (customer_id, total_amt, status,
                         qc.get("txn_date"),
                         (qc.get("customer_memo") or qc.get("private_note") or "")[:500],
                         qbo_credit_id, now_iso),
                    )
                    result["created"] += 1

                # B4: link core_returns if this CM has a CORE-CHARGE line.
                core_lines = [
                    ln for ln in (qc.get("lines") or [])
                    if (ln.get("sku") or "").upper().replace("_", "-")
                       in ("CORE-CHARGE", "CORE-CREDIT")
                ]
                if core_lines:
                    try:
                        linked = _link_core_returns(
                            conn, customer_id, qbo_credit_id,
                            core_lines, qc.get("txn_date"), now_iso,
                        )
                        result["core_returns_linked"] += linked
                    except Exception as exc:
                        logger.warning(
                            "[QBO] core_returns linkage failed for %s: %s",
                            qbo_credit_id, exc,
                        )
            except Exception as exc:
                result["errors"].append(
                    f"{qc.get('doc_number') or qbo_credit_id}: {exc}"
                )

        try:
            conn.commit()
        except Exception:
            pass

    logger.info(
        "[QBO] import_qbo_credit_memos_to_db: %d created, %d updated, "
        "%d core_returns_linked, %d errors",
        result["created"], result["updated"],
        result["core_returns_linked"], len(result["errors"]),
    )
    return result


def _link_core_returns(
    conn,
    customer_id: int,
    qbo_credit_id: str,
    core_lines: list[dict],
    txn_date: str | None,
    now_iso: str,
) -> int:
    """Mark open core_returns for this customer as 'credited'.

    Strategy: distribute the CM's core line amount FIFO across the
    customer's open core_returns rows (oldest first). Each row gets
    its full ``core_charge`` worth of credit until the CM amount is
    exhausted. Idempotent: rows already linked to ``qbo_credit_id``
    are skipped.
    """
    try:
        rows = conn.execute(
            "SELECT id, core_charge, credit_issued, status "
            "FROM core_returns "
            "WHERE customer_id = %s "
            "  AND status IN ('pending','received') "
            "  AND (qbo_credit_id IS NULL OR qbo_credit_id = '') "
            "ORDER BY created_at ASC, id ASC",
            (customer_id,),
        ).fetchall()
    except Exception:
        return 0
    if not rows:
        return 0

    remaining = sum(float(ln.get("amount") or 0) for ln in core_lines)
    if remaining <= 0:
        return 0

    linked = 0
    for r in rows:
        if remaining <= 0.005:
            break
        cr_id = r["id"] if hasattr(r, "keys") else r[0]
        charge = float((r["core_charge"] if hasattr(r, "keys") else r[1]) or 0)
        if charge <= 0:
            continue
        apply_amt = min(charge, remaining)
        try:
            conn.execute(
                """UPDATE core_returns SET
                      status = 'credited',
                      credit_issued = %s,
                      credit_date = %s,
                      qbo_credit_id = %s,
                      qbo_last_synced_at = %s,
                      updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (apply_amt, txn_date, qbo_credit_id, now_iso, cr_id),
            )
            remaining -= apply_amt
            linked += 1
        except Exception:
            continue
    return linked


__all__ = ["fetch_qbo_credit_memos", "import_qbo_credit_memos_to_db"]
