"""QBO Terms (SalesTerm) name↔Id cache.

QBO stores payment terms as ``Term`` records — both Customers and
Vendors reference them via a ``SalesTermRef`` containing the Term's
``Id``. JAKS stores the term as free text on the local row
(e.g. ``"Net 30"``), so to push terms through we need to translate
``"Net 30"`` → ``{"value": "3"}``.

This module:

* Loads all QBO ``Term`` records on first use and builds a
  case-insensitive name→Id map.
* Caches the map per-service-instance (the cache is cleared when
  the underlying QuickBooks client is rotated).
* Returns ``None`` when the requested term doesn't exist in QBO so
  the caller can skip the field rather than crash. We deliberately
  do NOT auto-create Terms in QBO — that's a master-data decision
  the user should make once in their own books.

Mock mode returns a small built-in catalogue so unit tests work
without touching the network.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


# Built-in catalogue used in mock mode + as a last-resort fallback
# when the live ``Term.all`` call fails.
_MOCK_TERMS: dict[str, str] = {
    "due on receipt":  "MOCK-TERM-DUE",
    "net 15":          "MOCK-TERM-N15",
    "net 30":          "MOCK-TERM-N30",
    "net 45":          "MOCK-TERM-N45",
    "net 60":          "MOCK-TERM-N60",
}


# Per-(client-id) cache so multiple threads / dialog instances share
# the same map without re-querying QBO. Keyed by ``id(client._client)``
# so a rotated SDK client invalidates automatically.
_cache_lock = threading.Lock()
_cache: dict[int, dict[str, str]] = {}


def _normalize(name: Any) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _client():
    """Return the QBO client without importing the SDK at module load."""
    from qbo.client import get_qbo_client  # type: ignore
    return get_qbo_client()


def _load_live(client) -> dict[str, str]:
    """Fetch every Term from QBO. Errors → empty dict (graceful)."""
    try:
        from quickbooks.objects.term import Term
        from qbo._throttle import throttle
        throttle()
        terms = Term.all(qb=client._client, max_results=200)
    except Exception as exc:
        logger.warning("[QBO terms] Term.all failed: %s", exc)
        return {}

    out: dict[str, str] = {}
    for t in terms or []:
        name = _normalize(getattr(t, "Name", None))
        tid = getattr(t, "Id", None)
        if name and tid is not None:
            out[name] = str(tid)
    return out


def _get_map() -> dict[str, str]:
    """Get (and cache) the active term map. Mock-mode → built-in catalogue."""
    try:
        client = _client()
    except Exception as exc:
        logger.debug("[QBO terms] no client available: %s", exc)
        return dict(_MOCK_TERMS)

    if getattr(client, "is_mock_mode", False):
        return dict(_MOCK_TERMS)

    inner = getattr(client, "_client", None)
    cache_key = id(inner) if inner is not None else 0

    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached
        loaded = _load_live(client)
        # Always cache even if empty so repeated misses don't hammer the API.
        _cache[cache_key] = loaded
    return loaded


def clear_cache() -> None:
    """Forget all cached maps. Call after token rotation or tests."""
    with _cache_lock:
        _cache.clear()


def resolve_term_id(name: str | None) -> str | None:
    """Map a free-text payment-term string (e.g. ``'Net 30'``) to a QBO Term Id.

    Returns ``None`` when the term is unknown, empty, or QBO returned
    no Terms. Callers should treat ``None`` as "skip the SalesTermRef
    field on this payload".
    """
    if not name:
        return None
    key = _normalize(name)
    if not key:
        return None
    m = _get_map()
    return m.get(key)


def sales_term_ref(name: str | None) -> dict | None:
    """Convenience: build a ``SalesTermRef`` dict or return ``None``.

    Use as::

        ref = sales_term_ref(local.get("payment_terms"))
        if ref:
            cust.SalesTermRef = ref
    """
    tid = resolve_term_id(name)
    if not tid:
        return None
    return {"value": tid}


def resolve_term_name(qbo_id: str | None) -> str | None:
    """Inverse: map a QBO Term Id back to its display name.

    Used by importers so JAKS stores a human-readable term string.
    Returns ``None`` when the id isn't in the cache.
    """
    if qbo_id is None:
        return None
    target = str(qbo_id)
    m = _get_map()
    for name, tid in m.items():
        if tid == target:
            # Re-title-case for readability ("net 30" → "Net 30")
            return name.title() if name.startswith("net ") else name.capitalize()
    return None


__all__ = [
    "clear_cache",
    "resolve_term_id",
    "resolve_term_name",
    "sales_term_ref",
]
