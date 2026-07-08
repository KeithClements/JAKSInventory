"""Retry helper for failed ``qbo_sync_log`` rows.

The Sync Center failures dialog and per-entity "Retry Failed" buttons
need a single, side-effect-safe way to take a list of failed sync_log
ids, look up the underlying source rows, re-invoke the matching push
helper, and (on success) mark the log rows resolved.

Design goals
------------
* **No new push code.** Reuse the existing ``qbo.push.push_*_to_qbo``
  functions so behaviour matches the original push exactly.
* **Idempotent.** All push helpers go through ``qbo._retry``'s
  ``idempotent_write`` so re-running is safe — duplicates are
  deduped by the SDK.
* **Per-row resolution.** Each log id is retried independently; one
  row's failure does not poison the rest of the batch.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-row loaders. Each maps a sync_log (source_type, source_id) pair
# back to the full local dict the corresponding push helper expects.
# ---------------------------------------------------------------------------

def _load_invoice(conn, source_id: int) -> dict | None:
    try:
        row = conn.execute(
            "SELECT * FROM invoices WHERE id = ?", (source_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    inv = dict(row) if hasattr(row, "keys") else None
    if inv is None:
        return None
    try:
        lines = conn.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
        inv["lines"] = [dict(r) for r in lines]
    except Exception:
        inv["lines"] = []
    return inv


def _load_po(conn, source_id: int) -> dict | None:
    try:
        row = conn.execute(
            "SELECT * FROM purchase_orders WHERE id = ?", (source_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    po = dict(row) if hasattr(row, "keys") else None
    if po is None:
        return None
    try:
        lines = conn.execute(
            "SELECT * FROM po_lines WHERE po_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
        po["lines"] = [dict(r) for r in lines]
    except Exception:
        po["lines"] = []
    return po


def _load_simple(conn, table: str, source_id: int) -> dict | None:
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (source_id,),
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None and hasattr(row, "keys") else None


def _load_quote(conn, source_id: int) -> dict | None:
    try:
        row = conn.execute(
            "SELECT * FROM quotes WHERE id = ?", (source_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    q = dict(row) if hasattr(row, "keys") else None
    if q is None:
        return None
    try:
        lines = conn.execute(
            "SELECT * FROM quote_lines WHERE quote_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
        q["lines"] = [dict(r) for r in lines]
    except Exception:
        q["lines"] = []
    return q


# (entity_type | source_type) → (loader, push_fn_name, batch_kwarg)
# The push helpers all take their iterable as the first positional arg.
_DISPATCH: dict[str, tuple] = {
    # entity / source type variations all map to one row loader + pusher
    "invoice":         (_load_invoice, "push_invoices_to_qbo"),
    "payment":         (_load_invoice, "push_payments_to_qbo"),
    "credit_memo":     (lambda c, i: _load_simple(c, "customer_credits", i), "push_credits_to_qbo"),
    "bill":            (_load_po,      "push_bills_to_qbo"),
    "purchase_order":  (_load_po,      "push_pos_to_qbo"),
    "estimate":        (_load_quote,   "push_quotes_to_qbo"),
    "estimate_update": (_load_quote,   "push_quotes_to_qbo"),
    "quote":           (_load_quote,   "push_quotes_to_qbo"),
}


def retry_log_ids(log_ids: Iterable[int]) -> dict:
    """Retry the failed push behind each ``qbo_sync_log`` id.

    Returns ``{"ok": int, "failed": int, "skipped": int,
    "unsupported": int, "details": [...]}``. Successfully-pushed log
    rows are stamped ``resolved_at = now()`` so they drop out of the
    Sync Center failure view.
    """
    ids = [int(x) for x in log_ids if x is not None]
    summary = {"ok": 0, "failed": 0, "skipped": 0, "unsupported": 0, "details": []}
    if not ids:
        return summary

    try:
        from db.database import get_db
    except Exception as exc:
        logger.warning("retry_log_ids: db unavailable: %s", exc)
        summary["failed"] = len(ids)
        return summary

    # Pull the log rows so we know which entity/source each one points at.
    placeholders = ",".join(["?"] * len(ids))
    try:
        with get_db() as conn:
            rows = conn.execute(
                f"SELECT id, entity_type, source_type, source_id, local_ref "
                f"FROM qbo_sync_log WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
            log_rows = [dict(r) if hasattr(r, "keys") else
                        {"id": r[0], "entity_type": r[1], "source_type": r[2],
                         "source_id": r[3], "local_ref": r[4]}
                        for r in rows]
    except Exception as exc:
        logger.warning("retry_log_ids: could not load log rows: %s", exc)
        summary["failed"] = len(ids)
        return summary

    # Group log rows by entity so we can call each push helper once
    # per group. We still report status per row.
    groups: dict[str, list[dict]] = {}
    for lr in log_rows:
        key = (lr.get("entity_type") or lr.get("source_type") or "").lower()
        groups.setdefault(key, []).append(lr)

    from qbo import push as _push_mod

    for entity, log_subset in groups.items():
        disp = _DISPATCH.get(entity)
        if disp is None:
            summary["unsupported"] += len(log_subset)
            for lr in log_subset:
                summary["details"].append({
                    "log_id": lr["id"], "status": "unsupported",
                    "entity": entity, "error": "no retry handler",
                })
            continue

        loader, push_fn_name = disp
        push_fn = getattr(_push_mod, push_fn_name, None)
        if push_fn is None:
            summary["unsupported"] += len(log_subset)
            continue

        # Load source rows from DB
        source_rows: list[dict] = []
        log_by_source: dict[int, dict] = {}
        try:
            with get_db() as conn:
                for lr in log_subset:
                    sid = lr.get("source_id")
                    if sid is None:
                        summary["skipped"] += 1
                        summary["details"].append({
                            "log_id": lr["id"], "status": "skipped",
                            "entity": entity, "error": "no source_id",
                        })
                        continue
                    src = loader(conn, int(sid))
                    if src is None:
                        summary["skipped"] += 1
                        summary["details"].append({
                            "log_id": lr["id"], "status": "skipped",
                            "entity": entity,
                            "error": f"source row {sid} not found",
                        })
                        continue
                    source_rows.append(src)
                    log_by_source[int(sid)] = lr
        except Exception as exc:
            logger.warning("retry_log_ids: loader failed for %s: %s", entity, exc)
            for lr in log_subset:
                summary["failed"] += 1
                summary["details"].append({
                    "log_id": lr["id"], "status": "failed",
                    "entity": entity, "error": str(exc),
                })
            continue

        if not source_rows:
            continue

        # Invoke the push helper
        try:
            result = push_fn(source_rows)
        except Exception as exc:
            logger.warning("retry_log_ids: %s raised: %s", push_fn_name, exc)
            for src in source_rows:
                sid = int(src.get("id") or 0)
                lr = log_by_source.get(sid)
                summary["failed"] += 1
                summary["details"].append({
                    "log_id": lr["id"] if lr else None,
                    "status": "failed", "entity": entity, "error": str(exc),
                })
            continue

        # Classify per-row outcomes from the push helper's ``details`` list.
        details = result.get("details") or []
        ok_source_ids: set[int] = set()
        for d in details:
            sid = d.get("id")
            try:
                sid_int = int(sid) if sid is not None else None
            except Exception:
                sid_int = None
            lr = log_by_source.get(sid_int) if sid_int is not None else None
            status = (d.get("status") or "").lower()
            if status == "ok":
                summary["ok"] += 1
                if sid_int is not None:
                    ok_source_ids.add(sid_int)
            elif status == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
            summary["details"].append({
                "log_id": lr["id"] if lr else None,
                "status": status or "failed",
                "entity": entity,
                "error": d.get("error"),
                "qbo_id": d.get("qbo_id"),
            })

        # Mark successful log rows resolved.
        if ok_source_ids:
            ok_log_ids = [
                int(log_by_source[sid]["id"]) for sid in ok_source_ids
                if sid in log_by_source
            ]
            if ok_log_ids:
                _mark_resolved(ok_log_ids)

    return summary


def _mark_resolved(log_ids: list[int]) -> None:
    if not log_ids:
        return
    try:
        from db.database import get_db
        from datetime import datetime
        now = datetime.utcnow().isoformat(timespec="seconds")
        placeholders = ",".join(["?"] * len(log_ids))
        with get_db() as conn:
            conn.execute(
                f"UPDATE qbo_sync_log SET resolved_at = ? "
                f"WHERE id IN ({placeholders})",
                (now, *log_ids),
            )
            try:
                conn.commit()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("retry_dispatch: mark resolved failed: %s", exc)


__all__ = ["retry_log_ids"]
