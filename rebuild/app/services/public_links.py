"""
app/services/public_links.py
============================
§22.7 — signed, expiring, no-login links so a customer can open their quote /
sales-order / invoice PDF from a text or email.

Security model (mirrors app.auth):
  * The token is the credential — an itsdangerous URLSafeTimedSerializer payload
    {"t": doc_type, "id": doc_id} signed with the app secret (a dedicated salt
    namespaces it apart from session cookies). It cannot be forged or enumerated
    (opaque), and it EXPIRES (default 30 days).
  * The public route (app/routers/public_docs.py) is the ONLY thing that reads it,
    serves exactly that one PDF, and requires no login.
  * Links are only generated when ``public_base_url`` is configured — until the app
    is internet-reachable a link would be useless, so we emit none.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import _get_secret
from app.settings_utils import get_setting_value_db

_SALT = "jaks.publicdoc.v1"
DEFAULT_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
VALID_TYPES = frozenset({"quote", "invoice", "sales_order"})


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt=_SALT)


def make_doc_token(doc_type: str, doc_id: int) -> str:
    if doc_type not in VALID_TYPES:
        raise ValueError(f"Unknown doc_type {doc_type!r}")
    return _serializer().dumps({"t": doc_type, "id": int(doc_id)})


def read_doc_token(token: str, max_age: int = DEFAULT_MAX_AGE) -> tuple[str, int] | None:
    """Return (doc_type, doc_id) for a valid, unexpired token, else None."""
    try:
        data = _serializer().loads(token or "", max_age=max_age)
    except (BadSignature, SignatureExpired, Exception):  # noqa: BLE001 — any decode failure → invalid
        return None
    if not isinstance(data, dict):          # a signed non-dict payload must not raise
        return None
    t, i = data.get("t"), data.get("id")
    if t in VALID_TYPES and isinstance(i, int):
        return t, i
    return None


def public_base_url(db: Session) -> str:
    """The configured internet-reachable base, e.g. https://axle.jaksdiesel.com
    (trailing slash stripped). Empty until the owner sets it."""
    return (get_setting_value_db(db, "public_base_url", "") or "").strip().rstrip("/")


def public_doc_url(db: Session, doc_type: str, doc_id: int) -> str | None:
    """Full signed view URL, or None when public links aren't configured."""
    base = public_base_url(db)
    if not base or doc_type not in VALID_TYPES:
        return None
    return f"{base}/p/d/{make_doc_token(doc_type, doc_id)}"
