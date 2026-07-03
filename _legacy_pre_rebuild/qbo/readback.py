"""QBO read-back helpers (audit log support).

Provides a manual "read back from QBO" capability for audit log
entries: given the QBO Id we wrote, fetch the current canonical
record from QBO so we can compare what we sent vs what QBO now
holds. Used by the Audit Log UI's "Read Back From QBO" button.

All entry points respect mock mode: in mock mode they return a
stub dict and never touch the network.
"""
from __future__ import annotations

import logging
from typing import Any

from qbo.client import get_qbo_client
from qbo.config import is_mock_mode

logger = logging.getLogger(__name__)


_OBJECT_REGISTRY: dict[str, str] = {
    "Invoice": "quickbooks.objects.invoice:Invoice",
    "Bill": "quickbooks.objects.bill:Bill",
    "Payment": "quickbooks.objects.payment:Payment",
    "CreditMemo": "quickbooks.objects.creditmemo:CreditMemo",
    "JournalEntry": "quickbooks.objects.journalentry:JournalEntry",
    "Customer": "quickbooks.objects.customer:Customer",
    "Vendor": "quickbooks.objects.vendor:Vendor",
    "Item": "quickbooks.objects.item:Item",
}

# QBO/SDK-only fields that change on every save — exclude from diffs.
_DIFF_IGNORE_KEYS: frozenset[str] = frozenset({
    "SyncToken",
    "MetaData",
    "Id",
    "domain",
    "sparse",
    "_mock",
})


def _resolve_class(object_type: str):
    """Look up the python-quickbooks SDK class for a QBO object type."""
    spec = _OBJECT_REGISTRY.get(object_type)
    if not spec:
        raise ValueError(f"Unsupported QBO object type: {object_type!r}")
    module_path, cls_name = spec.split(":")
    mod = __import__(module_path, fromlist=[cls_name])
    return getattr(mod, cls_name)


def fetch(object_type: str, qbo_id: str) -> dict[str, Any]:
    """Fetch a single QBO record by Id and return it as a dict.

    Mock mode returns a stub ``{"_mock": True, "Id": qbo_id, ...}``.
    """
    if not qbo_id:
        raise ValueError("qbo_id is required")

    if is_mock_mode():
        return {
            "_mock": True,
            "Id": str(qbo_id),
            "Type": object_type,
            "_note": "Mock mode: read-back returns stub record.",
        }

    client = get_qbo_client()
    if not client.is_connected:
        client.connect()

    cls = _resolve_class(object_type)
    obj = cls.get(qbo_id, qb=client._client)

    # python-quickbooks objects expose to_dict / to_json
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    to_json = getattr(obj, "to_json", None)
    if callable(to_json):
        try:
            import json
            return json.loads(to_json())
        except Exception:
            pass
    # Last-ditch fallback: vars()
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def query(qbo_query: str) -> dict[str, Any]:
    """Run an ad-hoc QBO query (``SELECT ... FROM ...``). Mock-safe."""
    if is_mock_mode():
        return {"_mock": True, "query": qbo_query, "QueryResponse": {}}

    client = get_qbo_client()
    if not client.is_connected:
        client.connect()
    # python-quickbooks exposes the underlying intuit-oauth client at _client
    return client._client.query(qbo_query)


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

def _flatten(
    obj: Any,
    *,
    prefix: str = "",
    out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten nested dict/list into ``{"path.to.key": value}`` form."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            _flatten(v, prefix=key, out=out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            _flatten(v, prefix=key, out=out)
    else:
        out[prefix] = obj
    return out


def _is_ignored_path(path: str) -> bool:
    """True if any segment of ``path`` is in the ignore set."""
    # path is dot/bracket separated; split on '.' and strip [n] indices.
    for seg in path.split("."):
        bare = seg.split("[", 1)[0]
        if bare in _DIFF_IGNORE_KEYS:
            return True
    return False


def diff_payloads(
    request: dict[str, Any] | None,
    response: dict[str, Any] | None,
) -> list[tuple[str, Any, Any]]:
    """Return a list of ``(key_path, request_value, response_value)`` tuples
    for every field where request and response disagree.

    Ignores QBO-managed fields (``SyncToken``, ``MetaData``, ``Id``, etc.)
    so the diff focuses on real semantic differences.
    """
    req_flat = _flatten(request or {})
    resp_flat = _flatten(response or {})

    keys = set(req_flat.keys()) | set(resp_flat.keys())
    diffs: list[tuple[str, Any, Any]] = []
    for k in sorted(keys):
        if _is_ignored_path(k):
            continue
        r = req_flat.get(k, _MISSING)
        s = resp_flat.get(k, _MISSING)
        if r != s:
            diffs.append((
                k,
                None if r is _MISSING else r,
                None if s is _MISSING else s,
            ))
    return diffs


class _Missing:
    """Sentinel for fields absent from one side of a diff."""
    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<MISSING>"


_MISSING = _Missing()
