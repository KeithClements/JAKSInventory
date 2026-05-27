"""QBO.11 — Retry/backoff + idempotency helpers.

Usage patterns:

    key = make_idempotency_key("invoice", str(local_invoice_id))
    existing = get_prior_success(key)
    if existing:
        return {"success": True, "qbo_id": existing["qbo_id"], "action": "deduped"}

    record_attempt(key, "invoice", local_ref=str(local_invoice_id))
    try:
        qbo_id = retry_with_backoff(lambda: real_call())
    except QBOTransientError as e:
        record_failure(key, str(e))
        raise
    record_success(key, qbo_id=str(qbo_id))
    return {"success": True, "qbo_id": str(qbo_id), "action": "created"}

Retryable failures: HTTP 429, 5xx, ConnectionError, TimeoutError.
Non-retryable: 4xx other than 429 (bubble straight up).
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class QBOTransientError(Exception):
    """Raised when a retryable QBO error occurs (429 / 5xx / network)."""


class QBOPermanentError(Exception):
    """Raised when a QBO error is NOT retryable (4xx validation, auth, etc.)."""


_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _extract_status_code(exc: BaseException) -> int | None:
    """Best-effort status-code extraction from python-quickbooks / requests errors."""
    for attr in ("status_code", "code", "response_code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        val = getattr(resp, "status_code", None)
        if isinstance(val, int):
            return val
    return None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (QBOPermanentError,)):
        return False
    if isinstance(exc, (QBOTransientError, ConnectionError, TimeoutError)):
        return True
    status = _extract_status_code(exc)
    if status in _RETRYABLE_STATUS:
        return True
    # python-quickbooks wraps many errors as QuickbooksException; inspect msg.
    msg = str(exc).lower()
    for needle in ("timed out", "timeout", "connection reset",
                   "temporarily unavailable", "throttl", "rate limit"):
        if needle in msg:
            return True
    return False


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    jitter: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Invoke ``fn`` with exponential backoff on retryable QBO failures.

    Parameters
    ----------
    max_attempts:
        Total attempts including the first one (5 ⇒ up to 4 retries).
    base_delay:
        Seconds to wait after the first failure; doubles each retry.
    max_delay:
        Upper bound on any single sleep interval.
    jitter:
        When True, delay is multiplied by uniform(0.5, 1.5) to smear
        concurrent retries (thundering-herd mitigation).
    sleep:
        Injectable for tests.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == max_attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= 0.5 + random.random()  # 0.5x–1.5x
            logger.warning(
                "[QBO] retryable error (attempt %d/%d) in %s: %s — sleeping %.2fs",
                attempt, max_attempts, getattr(fn, "__name__", "<lambda>"),
                exc, delay,
            )
            sleep(delay)
    # Should be unreachable (raise inside loop), but satisfy type-checkers.
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Idempotency log (backed by qbo_sync_log — migration 051)
# ---------------------------------------------------------------------------

def make_idempotency_key(
    entity_type: str, local_ref: str, version_tag: str = "v1"
) -> str:
    """Return a stable sha256 key used to dedupe QBO writes."""
    payload = f"{entity_type}|{local_ref}|{version_tag}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_prior_success(key: str) -> dict | None:
    """Return the logged success row for ``key`` if one exists, else None.

    Never raises — on any DB error returns None so callers proceed as if
    no prior attempt was recorded (the backing SQL UNIQUE constraint
    still prevents true duplicates at insert time).
    """
    try:
        from db.database import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT qbo_id, status, attempts FROM qbo_sync_log "
                "WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
            if not row:
                return None
            d = dict(row) if hasattr(row, "keys") else {
                "qbo_id": row[0], "status": row[1], "attempts": row[2]
            }
            if d.get("status") == "success" and d.get("qbo_id"):
                return d
            return None
    except Exception as e:
        logger.debug("[QBO] get_prior_success ignored error: %s", e)
        return None


def record_attempt(key: str, entity_type: str, local_ref: str) -> None:
    """Upsert a pending row and bump the attempt counter."""
    try:
        from db.database import get_db
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id, attempts FROM qbo_sync_log "
                "WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
            if existing:
                row_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
                attempts = (existing[1] if not hasattr(existing, "keys")
                            else existing["attempts"]) or 0
                conn.execute(
                    "UPDATE qbo_sync_log SET attempts = %s, status = 'pending', "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (attempts + 1, row_id),
                )
            else:
                conn.execute(
                    "INSERT INTO qbo_sync_log "
                    "(entity_type, local_ref, idempotency_key, status, attempts) "
                    "VALUES (%s, %s, %s, 'pending', 1)",
                    (entity_type, local_ref, key),
                )
            conn.commit()
    except Exception as e:
        logger.debug("[QBO] record_attempt ignored error: %s", e)


def record_success(key: str, qbo_id: str) -> None:
    try:
        from db.database import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE qbo_sync_log SET status = 'success', qbo_id = %s, "
                "last_error = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE idempotency_key = %s",
                (str(qbo_id), key),
            )
            conn.commit()
    except Exception as e:
        logger.debug("[QBO] record_success ignored error: %s", e)


def record_failure(key: str, error: str) -> None:
    try:
        from db.database import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE qbo_sync_log SET status = 'failed', "
                "last_error = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE idempotency_key = %s",
                (str(error)[:2000], key),
            )
            conn.commit()
    except Exception as e:
        logger.debug("[QBO] record_failure ignored error: %s", e)


def idempotent_write(
    entity_type: str,
    local_ref: str,
    fn: Callable[[], str],
    *,
    version_tag: str = "v1",
    max_attempts: int = 5,
) -> dict:
    """Run ``fn`` with idempotency + retry.

    ``fn`` must return the QBO id as a string (or raise on failure).
    Returns dict: ``{"success": bool, "qbo_id": str, "action": str, "error"?: str}``
    where ``action`` is one of ``created`` | ``deduped`` | ``failed``.
    """
    key = make_idempotency_key(entity_type, local_ref, version_tag)
    prior = get_prior_success(key)
    if prior:
        return {
            "success": True,
            "qbo_id": prior["qbo_id"],
            "action": "deduped",
            "idempotency_key": key,
        }
    record_attempt(key, entity_type, local_ref)
    try:
        qbo_id = retry_with_backoff(fn, max_attempts=max_attempts)
    except Exception as exc:
        record_failure(key, str(exc))
        return {
            "success": False,
            "error": str(exc),
            "action": "failed",
            "idempotency_key": key,
        }
    record_success(key, qbo_id=str(qbo_id))
    return {
        "success": True,
        "qbo_id": str(qbo_id),
        "action": "created",
        "idempotency_key": key,
    }
