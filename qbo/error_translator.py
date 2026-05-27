"""Translate raw QBO / Intuit error strings into human-readable messages
plus a coarse error category for the Sync Failure Center.

The QBO REST API surfaces ``ValidationFault`` codes (4-digit) in error
bodies. Our integration layer typically captures them in the
``last_error`` / ``error_message`` columns of ``qbo_sync_log`` as a
string like::

    "QuickbooksException - Code 6240, Message: Duplicate Name Exists Error"
    "ValidationFault Code 6240 Duplicate Name Exists"

This module turns those into something the operator can act on, e.g.
``("duplicate", "Customer already exists in QuickBooks.")``.

Pure-Python, no side effects, fully unit-testable.
"""
from __future__ import annotations

import re
from typing import Tuple

# ---------------------------------------------------------------------------
# Public categories — kept short so they fit in a table column.
# ---------------------------------------------------------------------------

CAT_DUPLICATE        = "Duplicate"
CAT_MISSING_REF      = "Missing Reference"
CAT_INVALID_TAX      = "Invalid Tax Code"
CAT_INVALID_ACCOUNT  = "Invalid Account"
CAT_VALIDATION       = "Validation Error"
CAT_PERMISSION       = "Permission Denied"
CAT_AUTH             = "Authentication"
CAT_THROTTLED        = "Rate Limited"
CAT_NETWORK          = "Network Failure"
CAT_QBO_DOWN         = "QBO Unavailable"
CAT_NOT_FOUND        = "Not Found"
CAT_BUSINESS_RULE    = "Business Rule"
CAT_UNKNOWN          = "Other"


# Intuit error code → (category, friendly message).
# Reference: Intuit Developer "Error Codes" docs.
_CODE_MAP: dict[str, Tuple[str, str]] = {
    # 2xx — auth
    "270":  (CAT_AUTH,        "QuickBooks access token expired — reconnect QBO."),

    # 3xx — throttling / system
    "3001": (CAT_THROTTLED,   "QuickBooks rate limit hit — wait a moment and retry."),
    "3100": (CAT_QBO_DOWN,    "QuickBooks service is temporarily unavailable."),
    "3200": (CAT_AUTH,        "QuickBooks authentication failed — reconnect QBO."),

    # 4xx
    "4000": (CAT_VALIDATION,  "Required field is missing on this record."),
    "4001": (CAT_VALIDATION,  "A field on this record contains an invalid value."),

    # 5xx — duplicate / stale object
    "5010": (CAT_VALIDATION,  "Stale record — someone else updated it in QuickBooks. Refresh and try again."),
    "5030": (CAT_PERMISSION,  "Your QuickBooks user does not have permission to perform this action."),

    # 61xx / 62xx / 63xx — business object validation
    "6000": (CAT_VALIDATION,  "QuickBooks rejected this record as invalid."),
    "6140": (CAT_DUPLICATE,   "Duplicate document number — change the number and retry."),
    "6190": (CAT_VALIDATION,  "Transaction date is outside the open accounting period."),
    "6210": (CAT_INVALID_ACCOUNT, "The selected account is invalid or inactive in QuickBooks."),
    "6240": (CAT_DUPLICATE,   "This name already exists in QuickBooks."),
    "6260": (CAT_VALIDATION,  "Customer balance does not match — payment or credit is out of sync."),
    "6300": (CAT_INVALID_TAX, "The tax code on this line is invalid or inactive."),
    "6320": (CAT_INVALID_ACCOUNT, "Account type is not allowed for this transaction."),
    "6400": (CAT_VALIDATION,  "Required reference (customer / vendor / item) is missing."),
    "6500": (CAT_BUSINESS_RULE,  "This transaction is closed and cannot be edited."),
    "6560": (CAT_BUSINESS_RULE,  "Item is inactive in QuickBooks — re-activate it first."),
    "6580": (CAT_VALIDATION,  "Line item is missing required information."),
    "6600": (CAT_NOT_FOUND,   "Referenced object was not found in QuickBooks."),
    "610":  (CAT_NOT_FOUND,   "Object not found in QuickBooks."),
}

# Keyword/phrase fallbacks (case-insensitive substring match against the
# raw error string). First match wins, so order matters.
_PHRASE_MAP: list[Tuple[str, str, str]] = [
    # (lowercase needle, category, friendly)
    ("duplicate name exists",      CAT_DUPLICATE,        "This name already exists in QuickBooks."),
    ("duplicate document number",  CAT_DUPLICATE,        "Duplicate document number — change the number and retry."),
    ("already exists",             CAT_DUPLICATE,        "QuickBooks already has a record with this identifier."),
    ("stale object",               CAT_VALIDATION,       "Stale record — someone else updated it in QuickBooks. Refresh and try again."),
    ("tax code",                   CAT_INVALID_TAX,      "The tax code on this line is invalid or inactive."),
    ("invalid account",            CAT_INVALID_ACCOUNT,  "The selected account is invalid or inactive in QuickBooks."),
    ("account is invalid",         CAT_INVALID_ACCOUNT,  "The selected account is invalid or inactive in QuickBooks."),
    ("account type",               CAT_INVALID_ACCOUNT,  "Account type is not allowed for this transaction."),
    ("vendor not found",           CAT_MISSING_REF,      "Vendor does not exist in QuickBooks — create or import it first."),
    ("customer not found",         CAT_MISSING_REF,      "Customer does not exist in QuickBooks — create or import it first."),
    ("item not found",             CAT_MISSING_REF,      "Product/item does not exist in QuickBooks — push the product first."),
    ("product not found",          CAT_MISSING_REF,      "Product/item does not exist in QuickBooks — push the product first."),
    ("not found",                  CAT_NOT_FOUND,        "Referenced object was not found in QuickBooks."),
    ("inactive",                   CAT_BUSINESS_RULE,    "Referenced object is inactive in QuickBooks."),
    ("closed period",              CAT_BUSINESS_RULE,    "Transaction date falls in a closed accounting period."),
    ("authorization",              CAT_AUTH,             "QuickBooks authentication failed — reconnect QBO."),
    ("unauthorized",               CAT_AUTH,             "QuickBooks authentication failed — reconnect QBO."),
    ("token",                      CAT_AUTH,             "QuickBooks access token expired — reconnect QBO."),
    ("permission",                 CAT_PERMISSION,       "Your QuickBooks user does not have permission to perform this action."),
    ("throttle",                   CAT_THROTTLED,        "QuickBooks rate limit hit — wait a moment and retry."),
    ("rate limit",                 CAT_THROTTLED,        "QuickBooks rate limit hit — wait a moment and retry."),
    ("timed out",                  CAT_NETWORK,          "Network timeout reaching QuickBooks — check internet and retry."),
    ("timeout",                    CAT_NETWORK,          "Network timeout reaching QuickBooks — check internet and retry."),
    ("connection",                 CAT_NETWORK,          "Could not connect to QuickBooks — check internet."),
    ("dns",                        CAT_NETWORK,          "DNS lookup failed reaching QuickBooks."),
    ("unavailable",                CAT_QBO_DOWN,         "QuickBooks service is temporarily unavailable."),
    ("service is down",            CAT_QBO_DOWN,         "QuickBooks service is temporarily unavailable."),
    ("internal server error",      CAT_QBO_DOWN,         "QuickBooks returned an internal server error — retry later."),
    ("required field",             CAT_VALIDATION,       "Required field is missing on this record."),
    ("validation",                 CAT_VALIDATION,       "QuickBooks rejected this record as invalid."),
]


_CODE_RE = re.compile(r"\b(?:code[:\s]*)?(\d{3,4})\b", re.IGNORECASE)


def translate(raw_error: str | None) -> Tuple[str, str]:
    """Return ``(category, friendly_message)`` for an error string.

    Falls back to ``("Other", first_line_of_raw)`` when nothing matches
    so the UI never shows an empty cell.
    """
    if not raw_error:
        return (CAT_UNKNOWN, "Unknown error (no message captured).")

    text = str(raw_error).strip()
    lower = text.lower()

    # 1. Numeric code lookup (preferred — most reliable).
    for m in _CODE_RE.finditer(text):
        code = m.group(1)
        if code in _CODE_MAP:
            return _CODE_MAP[code]

    # 2. Phrase fallback.
    for needle, cat, friendly in _PHRASE_MAP:
        if needle in lower:
            return (cat, friendly)

    # 3. Last-ditch: surface the first line of the raw message, truncated.
    first = text.splitlines()[0].strip() or text
    if len(first) > 160:
        first = first[:157] + "…"
    return (CAT_UNKNOWN, first)


__all__ = [
    "translate",
    # categories
    "CAT_DUPLICATE", "CAT_MISSING_REF", "CAT_INVALID_TAX",
    "CAT_INVALID_ACCOUNT", "CAT_VALIDATION", "CAT_PERMISSION",
    "CAT_AUTH", "CAT_THROTTLED", "CAT_NETWORK", "CAT_QBO_DOWN",
    "CAT_NOT_FOUND", "CAT_BUSINESS_RULE", "CAT_UNKNOWN",
]
