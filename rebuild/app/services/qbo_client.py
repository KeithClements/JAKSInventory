"""
app/services/qbo_client.py
==========================
QuickBooks Online OAuth2 + REST client (Phase 1B).

Hand-rolled — no `intuit-oauth` / `python-quickbooks` dependency; this is plain
OAuth2 authorization-code + refresh over httpx (already a dependency). Tokens and
config live in the `settings` table (same place `qbo_client_id`/`qbo_client_secret`
already live), read/written via `app.settings_utils`.

SAFETY: nothing here is called during invoice finalize/payment. It is only invoked
explicitly (OAuth routes + one-click push). Reads are lazy, so an unconfigured /
disconnected QBO never affects app startup or the money paths.

Token encryption is a deliberate follow-up: `cryptography` is not installed, and
`qbo_client_secret` is already stored in settings in clear on this 2-user LAN box.
Flagged in the morning report — add Fernet-at-rest before this touches production.
"""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.settings_utils import get_setting_value_db, set_setting_value_db

# ── Intuit endpoints ──────────────────────────────────────────────────────────
AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
SCOPE_ACCOUNTING = "com.intuit.quickbooks.accounting"
MINOR_VERSION = "70"

_API_BASE = {
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
    "production": "https://quickbooks.api.intuit.com",
}
# Default redirect — the owner must register this EXACT URI in their Intuit app.
DEFAULT_REDIRECT_URI = "http://localhost:8000/qbo/callback"
_EXPIRY_SKEW_SECONDS = 90  # refresh a little early so a call never races the expiry


class QBOError(RuntimeError):
    """Any QBO connection/API failure. Callers in the push path catch this and
    record it via mark_sync_failed — it must never bubble into a money route."""


class QBONotConnected(QBOError):
    """Raised when no usable token/realm is configured yet."""


@dataclass
class QBOConfig:
    client_id: str
    client_secret: str
    realm_id: str
    environment: str          # "sandbox" | "production"
    redirect_uri: str
    access_token: str
    refresh_token: str
    token_expires_at: str     # ISO8601, or ""

    @property
    def api_base(self) -> str:
        return _API_BASE.get(self.environment, _API_BASE["sandbox"])

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def is_connected(self) -> bool:
        return bool(self.access_token and self.refresh_token and self.realm_id)


def load_config(db: Session) -> QBOConfig:
    g = lambda k, d="": get_setting_value_db(db, k, d)  # noqa: E731
    return QBOConfig(
        client_id=g("qbo_client_id").strip(),
        client_secret=g("qbo_client_secret").strip(),
        realm_id=g("qbo_realm_id").strip(),
        environment=(g("qbo_environment", "sandbox").strip().lower() or "sandbox"),
        redirect_uri=(g("qbo_redirect_uri", DEFAULT_REDIRECT_URI).strip() or DEFAULT_REDIRECT_URI),
        access_token=g("qbo_access_token").strip(),
        refresh_token=g("qbo_refresh_token").strip(),
        token_expires_at=g("qbo_token_expires_at").strip(),
    )


def is_connected(db: Session) -> bool:
    return load_config(db).is_connected


# ── OAuth dance ───────────────────────────────────────────────────────────────

def authorize_url(db: Session) -> str:
    """Build the Intuit consent URL and persist a fresh CSRF state."""
    cfg = load_config(db)
    if not cfg.has_credentials:
        raise QBONotConnected(
            "QBO Client ID / Secret are not set. Enter them in Settings first."
        )
    state = secrets.token_urlsafe(24)
    set_setting_value_db(db, "qbo_oauth_state", state, "QBO OAuth CSRF state (transient)")
    db.commit()  # MUST persist: the callback runs in a separate request/session
    params = httpx.QueryParams(
        {
            "client_id": cfg.client_id,
            "response_type": "code",
            "scope": SCOPE_ACCOUNTING,
            "redirect_uri": cfg.redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{params}"


def _basic_auth_header(cfg: QBOConfig) -> str:
    raw = f"{cfg.client_id}:{cfg.client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _store_tokens(db: Session, payload: dict) -> None:
    """Persist the token response. expires_in is seconds from now."""
    access = payload.get("access_token", "")
    refresh = payload.get("refresh_token", "")
    expires_in = int(payload.get("expires_in", 3600) or 3600)
    expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat(timespec="seconds")
    set_setting_value_db(db, "qbo_access_token", access, "QBO Access Token")
    if refresh:  # Intuit rotates refresh tokens; keep the latest, never blank it
        set_setting_value_db(db, "qbo_refresh_token", refresh, "QBO Refresh Token")
    set_setting_value_db(db, "qbo_token_expires_at", expires_at, "QBO access token expiry (ISO)")


def exchange_code(db: Session, code: str, realm_id: str, state: str) -> None:
    """Callback handler: verify state, swap the auth code for tokens, store realm."""
    expected = get_setting_value_db(db, "qbo_oauth_state", "")
    if not expected or state != expected:
        raise QBOError("OAuth state mismatch — possible CSRF; reconnect from Settings.")
    cfg = load_config(db)
    resp = httpx.post(
        TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(cfg),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise QBOError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    _store_tokens(db, resp.json())
    set_setting_value_db(db, "qbo_realm_id", str(realm_id), "QBO Realm ID")
    set_setting_value_db(db, "qbo_oauth_state", "", "QBO OAuth CSRF state (transient)")
    set_setting_value_db(
        db, "qbo_connected_at",
        datetime.utcnow().isoformat(timespec="seconds"), "QBO connected at (ISO)",
    )
    db.commit()


def refresh_access_token(db: Session) -> QBOConfig:
    """Use the refresh token to mint a new access token; persist + return config."""
    cfg = load_config(db)
    if not cfg.refresh_token:
        raise QBONotConnected("No QBO refresh token — connect from Settings.")
    resp = httpx.post(
        TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(cfg),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": cfg.refresh_token},
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise QBOError(f"Token refresh failed ({resp.status_code}): {resp.text[:300]}")
    _store_tokens(db, resp.json())
    db.commit()
    return load_config(db)


def disconnect(db: Session) -> None:
    """Clear stored tokens/realm so the app forgets the connection. Best-effort
    revoke of the refresh token with Intuit; local clear always happens."""
    cfg = load_config(db)
    if cfg.refresh_token:
        try:
            httpx.post(
                REVOKE_URL,
                headers={
                    "Authorization": _basic_auth_header(cfg),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={"token": cfg.refresh_token},
                timeout=20.0,
            )
        except Exception:
            pass  # local clear below is what matters
    for k in ("qbo_access_token", "qbo_refresh_token", "qbo_token_expires_at",
              "qbo_connected_at"):
        set_setting_value_db(db, k, "")
    db.commit()


# ── REST client ───────────────────────────────────────────────────────────────

class QBOClient:
    """Thin authenticated REST client for the QBO Accounting API.

    Auto-refreshes the access token when expired or on a 401. All failures raise
    QBOError; the push layer catches it and records mark_sync_failed.
    """

    def __init__(self, db: Session):
        self.db = db
        self.cfg = load_config(db)
        if not self.cfg.is_connected:
            raise QBONotConnected("QuickBooks is not connected. Connect it in Settings.")

    # -- token lifecycle --
    def _token_expired(self) -> bool:
        exp = self.cfg.token_expires_at
        if not exp:
            return True
        try:
            return datetime.utcnow() >= (
                datetime.fromisoformat(exp) - timedelta(seconds=_EXPIRY_SKEW_SECONDS)
            )
        except ValueError:
            return True

    def _ensure_token(self) -> None:
        if self._token_expired():
            self.cfg = refresh_access_token(self.db)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.cfg.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.cfg.api_base}/v3/company/{self.cfg.realm_id}/{path}"

    def request(self, method: str, path: str, *, json: dict | None = None,
                params: dict | None = None, _retried: bool = False) -> dict:
        self._ensure_token()
        p = dict(params or {})
        p.setdefault("minorversion", MINOR_VERSION)
        try:
            resp = httpx.request(
                method, self._url(path), headers=self._headers(),
                json=json, params=p, timeout=45.0,
            )
        except httpx.HTTPError as exc:  # network/timeout
            raise QBOError(f"QBO network error: {exc}") from exc

        if resp.status_code == 401 and not _retried:
            # token went stale mid-flight — refresh once and retry
            self.cfg = refresh_access_token(self.db)
            return self.request(method, path, json=json, params=params, _retried=True)
        if resp.status_code >= 400:
            raise QBOError(f"QBO {method} {path} → {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    # -- convenience --
    def query(self, sql: str) -> list[dict]:
        """Run a QBO SQL-ish query; return the list of entity dicts (any type)."""
        data = self.request("GET", "query", params={"query": sql})
        qr = data.get("QueryResponse", {}) if isinstance(data, dict) else {}
        for v in qr.values():
            if isinstance(v, list):
                return v
        return []

    def create(self, entity: str, payload: dict) -> dict:
        """POST a new entity (Invoice, Customer, Item, Payment …). Returns the
        created entity dict (unwrapped from its top-level key)."""
        data = self.request("POST", entity.lower(), json=payload)
        return data.get(entity, data) if isinstance(data, dict) else {}
