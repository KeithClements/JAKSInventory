"""
app/auth.py
===========
Minimal authentication for the single-user / small-team ERP (O2 — last 1A
go-live gate).  No external dependencies beyond itsdangerous (already vendored).

- **Passwords**: stdlib ``hashlib.pbkdf2_hmac`` — salted, iterated, constant-time
  compare.  Stored as ``pbkdf2_<algo>$<iterations>$<salt_hex>$<hash_hex>`` (well
  under the User.password_hash 256-char column).
- **Session**: a signed, timed cookie (itsdangerous ``URLSafeTimedSerializer``)
  carrying the user id.  No server-side session store needed for the cookie; the
  user_sessions table can layer on later if we want server-side revocation.

The signing secret lives in the settings table (key ``session_secret_key``),
auto-generated on first startup (see app/main.py) so cookies survive restarts.
"""
from __future__ import annotations

import hashlib
import hmac
import os

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# ── Password hashing ──────────────────────────────────────────────────────────
_ALGO = "sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16

# ── Session cookie ────────────────────────────────────────────────────────────
SESSION_COOKIE = "jaks_session"
SESSION_MAX_AGE = 60 * 60 * 12          # 12 hours
_SECRET_SETTING_KEY = "session_secret_key"
_SERIALIZER_SALT = "jaks.session.v1"    # itsdangerous namespacing, not the pw salt
# Used only if the settings row is somehow blank (pre-seed). Startup seeds a real
# random secret; this fallback is intentionally NOT cached so the seeded value
# takes over as soon as it exists.
_FALLBACK_SECRET = "jaks-insecure-default-change-me"

_secret_cache: str | None = None


def hash_password(password: str) -> str:
    """Return a self-describing salted PBKDF2 hash string."""
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_ALGO, password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verify a password against a stored hash. False on any
    malformed/empty hash (e.g. the old ``[single-user-mode-no-auth]`` placeholder)."""
    if not stored:
        return False
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$")
        algo = scheme.split("_", 1)[1]
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, IndexError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


# ── Session token (signed cookie value) ───────────────────────────────────────

def _get_secret() -> str:
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    from app.settings_utils import get_setting_value
    secret = get_setting_value(_SECRET_SETTING_KEY, "")
    if secret:
        _secret_cache = secret   # cache only a real, seeded secret
        return secret
    return _FALLBACK_SECRET      # not cached — picked up once startup seeds it


def reset_secret_cache() -> None:
    """Force re-read of the secret (secret rotation / tests pointing at a new DB)."""
    global _secret_cache
    _secret_cache = None


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_get_secret(), salt=_SERIALIZER_SALT)


def make_session_token(user_id: int) -> str:
    """Sign a cookie value carrying the user id."""
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str | None, max_age: int = SESSION_MAX_AGE) -> int | None:
    """Return the user id from a valid, unexpired token, else None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return uid if isinstance(uid, int) else None
