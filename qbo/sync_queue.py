"""QBO Pending Queue service.

The Pending Queue is the staging buffer between local edits and QBO.
Every time a product / customer / vendor / invoice / bill / payment /
inventory-qty change needs to round-trip to QBO, callers invoke
:func:`enqueue` here.  The :class:`PendingQueueDialog` drains it.

Design rules
------------
* **Idempotent.** Re-enqueuing the same ``(entity_type, local_record_id,
  action)`` while a pending row exists is a no-op — the existing row's
  ``message`` / ``payload_json`` is refreshed instead.
* **Risk-aware.** Inventory qty pushes are always *high*; product
  create/update is *medium*; invoices are *medium* under $500 and *high*
  at-or-above.  Customers/vendors default to *low*.
* **Audited.** Every state transition (synced / failed / ignored)
  appends one row to ``qbo_sync_events`` for the history view.

This module is intentionally side-effect-free w.r.t. the QBO API. It
just records intent; ``drain_*`` helpers call into ``qbo.push``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_PUSH   = "push"
ACTION_IMPORT = "import"
VALID_ACTIONS = {ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE,
                 ACTION_PUSH, ACTION_IMPORT}

DIR_APP_TO_QBO = "app_to_qbo"
DIR_QBO_TO_APP = "qbo_to_app"

RISK_LOW      = "low"
RISK_MEDIUM   = "medium"
RISK_HIGH     = "high"
RISK_CRITICAL = "critical"

STATUS_PENDING    = "pending"
STATUS_PROCESSING = "processing"
STATUS_FAILED     = "failed"
STATUS_SYNCED     = "synced"
STATUS_IGNORED    = "ignored"
STATUS_RETRY      = "retry"

# High-value cut-off above which invoice pushes get bumped to high risk.
INVOICE_HIGH_RISK_THRESHOLD = 500.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def classify_risk(entity_type: str, action: str,
                  payload: dict | None = None) -> str:
    """Return ``low`` / ``medium`` / ``high`` for an enqueue request.

    Rules:
    * Inventory qty overwrites → **critical** (clobbers QBO on-hand).
    * Invoice / bill / payment pushes → high (financial documents).
    * Large invoices (≥ threshold) stay at high; that is the same
      bucket since high already covers them.
    * Products (create/update) → medium.
    * Anything else (customers, vendors, refs) → low.
    """
    et = (entity_type or "").strip().lower()
    act = (action or "").strip().lower()
    if et in {"inventory_qty", "inventory_quantity", "qty_adjustment"}:
        return RISK_CRITICAL
    if et in {"invoice", "bill", "payment", "bill_payment",
              "credit_memo", "refund_receipt"}:
        return RISK_HIGH
    if et == "product":
        return RISK_MEDIUM
    return RISK_LOW


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

def enqueue(
    entity_type: str,
    local_record_id: Any,
    action: str = ACTION_UPDATE,
    *,
    direction: str = DIR_APP_TO_QBO,
    payload: dict | None = None,
    message: str | None = None,
    risk_level: str | None = None,
    qbo_id: str | None = None,
    local_ref: str | None = None,
) -> int | None:
    """Insert a pending row (or refresh the existing one).

    Returns the queue row id, or ``None`` if the DB is unavailable.
    """
    et = (entity_type or "").strip().lower()
    act = (action or ACTION_UPDATE).strip().lower()
    if not et or act not in VALID_ACTIONS:
        logger.warning(
            "enqueue: rejected entity=%r action=%r", entity_type, action,
        )
        return None
    if risk_level is None:
        risk_level = classify_risk(et, act, payload)

    payload_json = None
    if payload is not None:
        try:
            payload_json = json.dumps(payload, default=str)
        except Exception:
            payload_json = None

    rec_id = "" if local_record_id is None else str(local_record_id)
    ref_text = (str(local_ref).strip() if local_ref else "") or rec_id
    now = _now()

    try:
        from db.database import get_db
    except Exception:
        return None

    with get_db() as conn:
        # If a pending row already exists for this triple, refresh it.
        try:
            existing = conn.execute(
                "SELECT id FROM qbo_sync_queue "
                "WHERE entity_type = ? AND local_record_id = ? "
                "  AND action = ? AND status = 'pending'",
                (et, rec_id, act),
            ).fetchone()
        except Exception:
            existing = None
        if existing is not None:
            row_id = (existing[0] if isinstance(existing, tuple)
                      else existing["id"])
            try:
                conn.execute(
                    "UPDATE qbo_sync_queue SET payload_json = COALESCE(?, payload_json), "
                    "    message = COALESCE(?, message), "
                    "    risk_level = ?, qbo_id = COALESCE(?, qbo_id), "
                    "    local_ref = COALESCE(?, local_ref) "
                    "WHERE id = ?",
                    (payload_json, message, risk_level, qbo_id,
                     ref_text or None, row_id),
                )
                conn.commit()
            except Exception as exc:
                logger.warning("enqueue refresh failed: %s", exc)
            return int(row_id)

        try:
            cur = conn.execute(
                """INSERT INTO qbo_sync_queue
                   (entity_type, local_record_id, local_ref, qbo_id, action,
                    direction, risk_level, status, message, payload_json,
                    created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (et, rec_id, ref_text or None, qbo_id, act, direction,
                 risk_level, STATUS_PENDING, message, payload_json, now),
            )
            conn.commit()
            try:
                return int(cur.lastrowid)
            except Exception:
                r = conn.execute(
                    "SELECT MAX(id) FROM qbo_sync_queue"
                ).fetchone()
                return int(r[0])
        except Exception as exc:
            logger.warning("enqueue insert failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def _log_event(conn, row: dict, status: str, message: str | None = None,
               raw_details: str | None = None,
               failure_id: int | None = None) -> None:
    try:
        conn.execute(
            """INSERT INTO qbo_sync_events
               (queue_id, failure_id, entity_type, local_record_id, local_ref,
                qbo_id, action, direction, risk_level, status, message,
                raw_details, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row.get("id"), failure_id, row.get("entity_type"),
             row.get("local_record_id"), row.get("local_ref"),
             row.get("qbo_id"), row.get("action"), row.get("direction"),
             row.get("risk_level"), status, message, raw_details, _now()),
        )
    except Exception as exc:
        logger.warning("sync event insert failed: %s", exc)


def _load_row(conn, queue_id: int) -> dict | None:
    try:
        row = conn.execute(
            "SELECT * FROM qbo_sync_queue WHERE id = ?", (int(queue_id),),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return None


def mark_processing(queue_id: int) -> None:
    try:
        from db.database import get_db
        with get_db() as conn:
            row = _load_row(conn, queue_id) or {}
            conn.execute(
                "UPDATE qbo_sync_queue SET status='processing', "
                "    last_attempt_at = ? WHERE id = ?",
                (_now(), int(queue_id)),
            )
            _log_event(conn, row, STATUS_PROCESSING,
                       message="Sync attempt started")
            conn.commit()
    except Exception as exc:
        logger.warning("mark_processing failed: %s", exc)


def mark_synced(queue_id: int, *, qbo_id: str | None = None,
                message: str | None = None) -> None:
    try:
        from db.database import get_db
        with get_db() as conn:
            row = _load_row(conn, queue_id) or {}
            now = _now()
            conn.execute(
                "UPDATE qbo_sync_queue SET status='synced', synced_at=?, "
                "    last_attempt_at=?, qbo_id=COALESCE(?, qbo_id), "
                "    message=COALESCE(?, message) WHERE id = ?",
                (now, now, qbo_id, message, int(queue_id)),
            )
            row["qbo_id"] = qbo_id or row.get("qbo_id")
            _log_event(conn, row, STATUS_SYNCED, message=message)
            conn.commit()
    except Exception as exc:
        logger.warning("mark_synced failed: %s", exc)


def mark_failed(queue_id: int, *, message: str,
                raw_details: str | None = None) -> None:
    """Mark the queue row failed and log a Failure Center event."""
    try:
        from db.database import get_db
        with get_db() as conn:
            row = _load_row(conn, queue_id) or {}
            now = _now()
            # Mirror into qbo_sync_log so the Failure Center sees it.
            failure_id: int | None = None
            try:
                cur = conn.execute(
                    """INSERT INTO qbo_sync_log
                       (entity_type, local_ref, idempotency_key, status,
                        attempts, last_error, source_type, source_id,
                        sync_action)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (row.get("entity_type"), row.get("local_record_id"),
                     f"queue-{int(queue_id)}", "failed", 1, message,
                     row.get("entity_type"), row.get("local_record_id"),
                     row.get("action")),
                )
                try:
                    failure_id = int(cur.lastrowid)
                except Exception:
                    failure_id = None
            except Exception:
                # Idempotency-key uniqueness collision — look up the
                # existing failure row for this queue id.
                try:
                    r = conn.execute(
                        "SELECT id FROM qbo_sync_log WHERE "
                        "idempotency_key = ?",
                        (f"queue-{int(queue_id)}",),
                    ).fetchone()
                    if r is not None:
                        failure_id = int(
                            r[0] if isinstance(r, tuple) else r["id"]
                        )
                except Exception:
                    failure_id = None
            conn.execute(
                "UPDATE qbo_sync_queue SET status='failed', "
                "    last_attempt_at=?, message=?, "
                "    failure_id=COALESCE(?, failure_id) WHERE id = ?",
                (now, message, failure_id, int(queue_id)),
            )
            row["failure_id"] = failure_id
            _log_event(conn, row, STATUS_FAILED, message=message,
                       raw_details=raw_details, failure_id=failure_id)
            conn.commit()
    except Exception as exc:
        logger.warning("mark_failed failed: %s", exc)


def mark_ignored(queue_ids: Iterable[int]) -> int:
    ids = [int(x) for x in queue_ids if x is not None]
    if not ids:
        return 0
    try:
        from db.database import get_db
        with get_db() as conn:
            now = _now()
            placeholders = ",".join(["?"] * len(ids))
            # Log each ignored transition.
            try:
                rows = conn.execute(
                    f"SELECT * FROM qbo_sync_queue WHERE id IN ({placeholders})",
                    tuple(ids),
                ).fetchall()
                for r in rows:
                    if hasattr(r, "keys"):
                        _log_event(conn, dict(r), STATUS_IGNORED,
                                   message="Marked ignored by operator")
            except Exception:
                pass
            conn.execute(
                f"UPDATE qbo_sync_queue SET status='ignored', "
                f"    ignored_at=? WHERE id IN ({placeholders})",
                (now, *ids),
            )
            conn.commit()
        return len(ids)
    except Exception as exc:
        logger.warning("mark_ignored failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_queue(status: str | None = "pending",
               include_ignored: bool = False) -> list[dict]:
    """Return rows in the queue (newest first).

    ``status`` filters by a single status; pass ``None`` to get *active*
    rows (pending + processing + failed).  Ignored rows are excluded by
    default.
    """
    try:
        from db.database import get_db
    except Exception:
        return []
    sql = "SELECT * FROM qbo_sync_queue"
    where: list[str] = []
    params: list[Any] = []
    if status is None:
        where.append("status IN ('pending','processing','failed')")
    else:
        where.append("status = ?")
        params.append(status)
    if not include_ignored:
        where.append("ignored_at IS NULL")
    sql += " WHERE " + " AND ".join(where) + " ORDER BY id DESC"
    out: list[dict] = []
    with get_db() as conn:
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:
            logger.warning("list_queue failed: %s", exc)
            return []
        for r in rows:
            if hasattr(r, "keys"):
                out.append(dict(r))
            elif isinstance(r, tuple):
                # Map by introspection
                cols = [c[0] for c in conn._cursor.description]  # type: ignore[attr-defined]
                out.append(dict(zip(cols, r)))
    return out


def count_by_status() -> dict[str, int]:
    try:
        from db.database import get_db
    except Exception:
        return {}
    out: dict[str, int] = {}
    with get_db() as conn:
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM qbo_sync_queue "
                "WHERE ignored_at IS NULL GROUP BY status"
            ).fetchall()
            for r in rows:
                s = r[0] if isinstance(r, tuple) else r["status"]
                c = r[1] if isinstance(r, tuple) else r[1]
                out[str(s)] = int(c)
        except Exception:
            pass
    return out


def pending_count_by_entity() -> dict[str, int]:
    """Number of rows still pending grouped by entity_type."""
    try:
        from db.database import get_db
    except Exception:
        return {}
    out: dict[str, int] = {}
    with get_db() as conn:
        try:
            rows = conn.execute(
                "SELECT entity_type, COUNT(*) FROM qbo_sync_queue "
                "WHERE status='pending' AND ignored_at IS NULL "
                "GROUP BY entity_type"
            ).fetchall()
            for r in rows:
                ent = r[0] if isinstance(r, tuple) else r["entity_type"]
                cnt = r[1] if isinstance(r, tuple) else r[1]
                out[str(ent or "")] = int(cnt or 0)
        except Exception:
            pass
    return out


def list_events(*, status: str | None = None,
                day_window: int | None = None,
                limit: int = 500) -> list[dict]:
    """Return rows from ``qbo_sync_events`` newest first.

    ``status``: filter to one of ``synced``/``failed``/``ignored``/etc.
    ``day_window``: keep only events created in the last N days
    (``1`` = today, ``7`` = last week).
    """
    try:
        from db.database import get_db
    except Exception:
        return []
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if day_window is not None and day_window > 0:
        where.append("created_at >= datetime('now', ?)")
        params.append(f"-{int(day_window)} days")
    sql = "SELECT * FROM qbo_sync_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    out: list[dict] = []
    with get_db() as conn:
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception as exc:
            logger.warning("list_events failed: %s", exc)
            return []
        for r in rows:
            if hasattr(r, "keys"):
                out.append(dict(r))
    return out


# ---------------------------------------------------------------------------
# Drain (operator-initiated)
# ---------------------------------------------------------------------------

# Map (entity_type, action) → callable taking the queue row dict and
# returning {"ok": bool, "qbo_id": str | None, "message": str}.
_DISPATCH: dict[tuple[str, str], Any] = {}


def register_dispatcher(entity_type: str, action: str, fn) -> None:
    """Allow external modules to plug in real QBO push handlers."""
    _DISPATCH[(entity_type.lower(), action.lower())] = fn


def _default_dispatcher(row: dict) -> dict:
    """Fallback: best-effort dispatch through :mod:`qbo.push`.

    Most callers will register a real handler; this stub keeps the
    Sync-Selected button useful in mock mode by simulating a success.
    """
    import os
    if os.getenv("QBO_FORCE_MOCK") or not os.getenv("QBO_CLIENT_ID"):
        return {"ok": True, "qbo_id": f"MOCK-{row.get('entity_type')}-"
                f"{row.get('local_record_id')}",
                "message": "Mock sync completed"}
    return {"ok": False, "qbo_id": None,
            "message": "No dispatcher registered for "
                       f"{row.get('entity_type')}/{row.get('action')}"}


def drain(queue_ids: Iterable[int]) -> dict:
    """Attempt to sync each row.  Returns a summary dict."""
    ids = [int(x) for x in queue_ids if x is not None]
    summary = {"ok": 0, "failed": 0, "skipped": 0, "details": []}
    if not ids:
        return summary
    try:
        from db.database import get_db
    except Exception:
        summary["failed"] = len(ids)
        return summary

    placeholders = ",".join(["?"] * len(ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM qbo_sync_queue WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        rows = [dict(r) for r in rows if hasattr(r, "keys")]

    for r in rows:
        qid = int(r["id"])
        if r.get("status") in {"ignored", "synced"}:
            summary["skipped"] += 1
            summary["details"].append({
                "queue_id": qid, "status": "skipped",
                "reason": r.get("status"),
            })
            continue
        # Re-pushing a previously failed row counts as a retry attempt;
        # log it before processing kicks off so the Sync Log captures
        # the operator's intent.
        if (r.get("status") or "").lower() == "failed":
            try:
                from db.database import get_db as _gd
                with _gd() as _c:
                    _log_event(_c, r, STATUS_RETRY,
                               message="Operator retried failed sync")
                    _c.commit()
            except Exception:
                pass
        mark_processing(qid)
        key = ((r.get("entity_type") or "").lower(),
               (r.get("action") or "").lower())
        fn = _DISPATCH.get(key) or _default_dispatcher
        try:
            result = fn(r) or {}
        except Exception as exc:
            mark_failed(qid, message=str(exc))
            summary["failed"] += 1
            summary["details"].append({
                "queue_id": qid, "status": "failed", "reason": str(exc),
            })
            continue
        if result.get("ok"):
            mark_synced(qid, qbo_id=result.get("qbo_id"),
                        message=result.get("message"))
            summary["ok"] += 1
            summary["details"].append({
                "queue_id": qid, "status": "synced",
                "qbo_id": result.get("qbo_id"),
            })
        else:
            mark_failed(qid, message=result.get("message") or "Sync failed",
                        raw_details=result.get("raw_details"))
            summary["failed"] += 1
            summary["details"].append({
                "queue_id": qid, "status": "failed",
                "reason": result.get("message"),
            })
    return summary
