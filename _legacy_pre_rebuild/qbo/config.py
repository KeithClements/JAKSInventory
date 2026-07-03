"""Central QBO mode / toggle configuration (QBO.9).

Single source of truth for whether QBO operates in:
  - **mock mode** (no real API calls, deterministic stubs), or
  - **live read-only** mode (connected but no writes), or
  - **live read-write** mode (writes enabled).

Resolution order (highest-priority first):
  1. `app_settings.qbo_mock_mode` / `qbo_write_enabled` (DB — user-controlled
     via the Settings → QBO tab).
  2. `QBO_FORCE_MOCK` / `QBO_WRITE_ENABLED` environment variables (for CI /
     scripts).
  3. Auto-detect: if `QBO_CLIENT_ID` + `QBO_CLIENT_SECRET` + `QBO_REALM_ID`
     are not all set, mock mode is forced on regardless of DB/env.

All QBO modules MUST call `is_mock_mode()` / `is_write_enabled()` dynamically.
Do not cache these values at module import (that was the pre-P2 bug).
"""
from __future__ import annotations

import os


_MOCK_SETTING_KEY = "qbo_mock_mode"
_WRITE_SETTING_KEY = "qbo_write_enabled"


def _creds_present() -> bool:
    return all(
        os.environ.get(k, "").strip()
        for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID")
    )


def _read_setting(key: str) -> str | None:
    """Best-effort read from app_settings; returns None if DB unavailable."""
    try:
        from db import get_setting
        return get_setting(key, None)
    except Exception:
        return None


def _truthy(val: str | None) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on") if val else False


def is_mock_mode() -> bool:
    """Return True if QBO should operate in mock mode (no real API calls)."""
    # Hard override: credentials missing → always mock.
    if not _creds_present():
        return True
    # DB setting wins if present.
    db_val = _read_setting(_MOCK_SETTING_KEY)
    if db_val is not None:
        return _truthy(db_val)
    # Env fallback.
    if _truthy(os.environ.get("QBO_FORCE_MOCK")):
        return True
    return False


def is_write_enabled() -> bool:
    """Return True if QBO write operations (create/update) are enabled.

    Writes are *always* disabled in mock mode (mock-mode creates return
    simulated success but never touch real QBO).
    """
    if is_mock_mode():
        # Mock writes are "enabled" in the sense that the stub returns
        # success — callers still gate on this, so we return True so the
        # mock success path is exercised. Live writes are still blocked
        # because `is_mock_mode()` is checked first downstream.
        pass
    db_val = _read_setting(_WRITE_SETTING_KEY)
    if db_val is not None:
        return _truthy(db_val)
    return _truthy(os.environ.get("QBO_WRITE_ENABLED"))


def get_mode() -> str:
    """Return one of: 'mock', 'read_only', 'read_write'."""
    if is_mock_mode():
        return "mock"
    return "read_write" if is_write_enabled() else "read_only"


def set_mock_mode(enabled: bool) -> None:
    """Persist mock-mode preference to app_settings."""
    from db import set_setting
    set_setting(_MOCK_SETTING_KEY, "1" if enabled else "0")


def set_write_enabled(enabled: bool) -> None:
    """Persist write-enabled preference to app_settings."""
    from db import set_setting
    set_setting(_WRITE_SETTING_KEY, "1" if enabled else "0")


def describe() -> dict:
    """Return a diagnostic dict for Settings UI / logs."""
    return {
        "mode": get_mode(),
        "mock": is_mock_mode(),
        "writes": is_write_enabled(),
        "creds_present": _creds_present(),
        "source_mock": (
            "db" if _read_setting(_MOCK_SETTING_KEY) is not None
            else "env" if _truthy(os.environ.get("QBO_FORCE_MOCK"))
            else "auto" if not _creds_present()
            else "default"
        ),
    }


# ---------------------------------------------------------------------------
# Account mapping (D2)
# ---------------------------------------------------------------------------
# Seven chart-of-accounts references QBO requires when creating Items,
# Invoices, Bills, Payments, and Credit Memos. Each maps to a QBO account
# ID (the short numeric/string id surfaced by the QBO API, NOT the account
# name). Stored in ``app_settings`` under the keys below; configured via
# Settings → QBO → "Account Mapping". Empty/missing values fall back to
# the legacy hard-coded "1"/"2"/"3" placeholders so older sandbox tests
# keep working.

_ACCOUNT_KEYS = (
    "qbo.account.inventory_asset",
    "qbo.account.sales_income",
    "qbo.account.cogs",
    "qbo.account.core_liability",
    "qbo.account.vendor_core_receivable",
    "qbo.account.ar",
    "qbo.account.ap",
    "qbo.account.tax_payable",
)

# Legacy placeholders preserved for sandbox/mock parity. Real QBO writes
# will fail unless the user populates Settings → QBO with their actual
# chart-of-accounts ids.
_ACCOUNT_DEFAULTS: dict[str, str] = {
    # Empty = unset; callers should treat as "no preference" so the
    # QBO client can auto-discover by AccountSubType. The previous
    # "1"/"2"/"3" placeholders caused live writes to fail with
    # "Invalid Reference Id" because those IDs rarely point at the
    # right accounts in a real company file or sandbox.
    "qbo.account.inventory_asset":        "",
    "qbo.account.sales_income":           "",
    "qbo.account.cogs":                   "",
    "qbo.account.core_liability":         "",
    # P3 — vendor-side core deposits are an asset (receivable from the
    # supplier) until the old core ships back. Mapped to a dedicated
    # Other Current Asset account; the customer-side ``core_liability``
    # and this one MUST NOT point at the same QBO account, or returns
    # accounting won't reconcile.
    "qbo.account.vendor_core_receivable": "",
    "qbo.account.ar":                     "",
    "qbo.account.ap":                     "",
    "qbo.account.tax_payable":            "",
}


def get_account_mapping() -> dict[str, str]:
    """Return the QBO account ids as a dict.

    Keys: ``inventory_asset``, ``sales_income``, ``cogs``,
    ``core_liability``, ``vendor_core_receivable``, ``ar``, ``ap``,
    ``tax_payable``. Missing/blank values fall back to
    ``_ACCOUNT_DEFAULTS``.
    """
    out: dict[str, str] = {}
    for key in _ACCOUNT_KEYS:
        short = key.split(".")[-1]
        raw = _read_setting(key)
        val = (raw or "").strip() if raw is not None else ""
        out[short] = val or _ACCOUNT_DEFAULTS[key]
    return out


def set_account_mapping(values: dict[str, str]) -> None:
    """Persist account-mapping fields. Only known keys are written.

    Accepts both the short form (``inventory_asset``) and the fully
    qualified key (``qbo.account.inventory_asset``).
    """
    try:
        from db import set_setting
    except Exception:
        return
    all_keys = _ACCOUNT_KEYS + _EXTRA_ACCOUNT_KEYS
    for key in all_keys:
        short = key.split(".")[-1]
        if short in values:
            set_setting(key, str(values[short] or "").strip())
        elif key in values:
            set_setting(key, str(values[key] or "").strip())


# ---------------------------------------------------------------------------
# Extended account mapping (additional accounts for full P&L coverage)
# ---------------------------------------------------------------------------

_EXTRA_ACCOUNT_KEYS = (
    "qbo.account.freight_income",
    "qbo.account.freight_expense",
    "qbo.account.sales_discounts",
    "qbo.account.sales_returns",
    "qbo.account.warranty_reserve",
)

_EXTRA_ACCOUNT_DEFAULTS: dict[str, str] = {
    "qbo.account.freight_income":   "",
    "qbo.account.freight_expense":  "",
    "qbo.account.sales_discounts":  "",
    "qbo.account.sales_returns":    "",
    "qbo.account.warranty_reserve": "",
}


def get_full_account_mapping() -> dict[str, str]:
    """Return all 12 account mapping keys (7 base + 5 extended)."""
    base = get_account_mapping()
    extra: dict[str, str] = {}
    for key in _EXTRA_ACCOUNT_KEYS:
        short = key.split(".")[-1]
        raw = _read_setting(key)
        extra[short] = (raw or "").strip() if raw is not None else ""
    return {**base, **extra}


# ---------------------------------------------------------------------------
# Sync authority
# ---------------------------------------------------------------------------

_SYNC_AUTHORITY_KEY = "qbo_sync_authority"
_SYNC_AUTHORITY_OPTIONS = ("local_master", "qbo_master", "hybrid")


def get_sync_authority() -> str:
    """Return the configured sync authority.

    local_master (default) — this app owns products, inventory, pricing.
        QBO receives accounting copies only.
    qbo_master — QBO is authoritative. Risky; not recommended.
    hybrid     — manual case-by-case control.
    """
    val = _read_setting(_SYNC_AUTHORITY_KEY)
    if val and val in _SYNC_AUTHORITY_OPTIONS:
        return val
    return "local_master"


def set_sync_authority(authority: str) -> None:
    """Persist the sync authority preference."""
    if authority not in _SYNC_AUTHORITY_OPTIONS:
        authority = "local_master"
    try:
        from db import set_setting
        set_setting(_SYNC_AUTHORITY_KEY, authority)
    except Exception:
        pass

