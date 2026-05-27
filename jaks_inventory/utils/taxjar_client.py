"""Thin TaxJar REST client for sales-tax rate lookups.

Used by `db.inventory.resolve_tax_rate()` to fetch live rates by ZIP/state,
falling back to the manual `tax_rates` table on any failure.

Endpoints: https://developers.taxjar.com/api/reference/#get-rates-for-a-location

Auth header:  Authorization: Bearer <API_KEY>
Sandbox host: https://api.sandbox.taxjar.com
Live host:    https://api.taxjar.com
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

LIVE_HOST = "https://api.taxjar.com"
SANDBOX_HOST = "https://api.sandbox.taxjar.com"
TIMEOUT_SEC = 6


def _host(sandbox: bool) -> str:
    return SANDBOX_HOST if sandbox else LIVE_HOST


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Accept": "application/json",
        "User-Agent": "JAKS-Inventory/1.0",
    }


def lookup_rate(api_key: str, zip_code: str, *,
                state: str | None = None,
                country: str = "US",
                sandbox: bool = False) -> dict[str, Any]:
    """Return TaxJar's rate payload for a ZIP (and optional state).

    Returns a dict with at least:
      - combined_rate (decimal float, e.g. 0.0865)
      - state, county, city components
      - zip
    Raises:
      ValueError on missing inputs.
      requests.HTTPError on 4xx/5xx.
      requests.RequestException on transport errors.
    """
    if not api_key or not api_key.strip():
        raise ValueError("TaxJar API key is empty")
    zip_code = (zip_code or "").strip()
    if not zip_code:
        raise ValueError("ZIP is required for TaxJar lookup")

    params = {"country": country}
    if state:
        params["state"] = state.strip().upper()[:2]

    url = f"{_host(sandbox)}/v2/rates/{zip_code}?{urlencode(params)}"
    resp = requests.get(url, headers=_headers(api_key), timeout=TIMEOUT_SEC)
    resp.raise_for_status()
    payload = resp.json() or {}
    rate = payload.get("rate") or {}
    if not rate:
        raise ValueError("TaxJar response missing 'rate' object")

    # Normalize numeric fields to floats so callers don't see strings
    for key in ("combined_rate", "state_rate", "county_rate", "city_rate",
                "combined_district_rate"):
        if key in rate:
            try:
                rate[key] = float(rate[key])
            except (TypeError, ValueError):
                rate[key] = 0.0
    return rate


def test_connection(api_key: str, *, sandbox: bool = False) -> tuple[bool, str]:
    """Ping TaxJar with a simple Beverly Hills lookup. Returns (ok, message)."""
    try:
        rate = lookup_rate(api_key, "90210", state="CA", sandbox=sandbox)
        combined = float(rate.get("combined_rate") or 0) * 100
        return True, f"OK — Beverly Hills 90210 returned {combined:.3f}%"
    except requests.HTTPError as e:
        body = ""
        try:
            body = json.dumps(e.response.json())[:200]
        except Exception:
            body = (e.response.text or "")[:200] if e.response is not None else ""
        return False, f"HTTP {e.response.status_code if e.response else '?'}: {body}"
    except requests.RequestException as e:
        return False, f"Network error: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
