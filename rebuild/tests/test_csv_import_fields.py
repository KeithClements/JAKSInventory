"""
tests/test_csv_import_fields.py
================================
Regression guard for §1.2h — customer CSV/Excel import dropped phone+email.

Root cause: common CRM export headers like "Customer Name" (QuickBooks),
"Account Name" (Salesforce), "Client" (Sage), "Mobile Number" were not in the
_IMPORT_ALIASES dict, so the company-name column was never mapped → the entire
row was silently skipped with "missing company name", which appeared as though
phone and email were being dropped.

Fix: added the missing company-name and phone aliases.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.routers.customers import _IMPORT_ALIASES, _parse_rows, _read_csv


def _run(csv_text: str):
    valid, skipped = _parse_rows(_read_csv(csv_text.encode("utf-8")))
    return valid, skipped


# ── company_name alias coverage ───────────────────────────────────────────────

@pytest.mark.parametrize("header,expected_company", [
    ("Company Name,Phone,Email", "ACME Corp"),
    ("Customer Name,Phone,Email", "ACME Corp"),   # QuickBooks
    ("Account Name,Phone,Email", "ACME Corp"),    # Salesforce / HubSpot
    ("Client Name,Phone,Email", "ACME Corp"),     # Sage / Xero
    ("Client,Phone,Email", "ACME Corp"),           # Sage short form
    ("Vendor Name,Phone,Email", "ACME Corp"),     # Vendor-as-customer imports
    ("Organization,Phone,Email", "ACME Corp"),
])
def test_company_name_variants_map_correctly(header, expected_company):
    csv_text = f"{header}\n{expected_company},303-555-1234,bob@acme.com\n"
    valid, skipped = _run(csv_text)
    assert len(valid) == 1, f"row was skipped with header {header!r}: {skipped}"
    assert valid[0]["company_name"] == expected_company


# ── phone and email are preserved through import ──────────────────────────────

@pytest.mark.parametrize("phone_header,email_header", [
    ("Phone", "Email"),
    ("Phone Number", "Email Address"),
    ("Mobile Number", "Work Email"),     # previously missing
    ("Office Phone", "Email"),           # previously missing
    ("Direct", "Email"),                 # previously missing
    ("Tel", "E-mail"),
])
def test_phone_and_email_preserved(phone_header, email_header):
    csv_text = f"Company Name,{phone_header},{email_header}\nTest Co,555-1234,x@y.com\n"
    valid, skipped = _run(csv_text)
    assert len(valid) == 1, f"row skipped: {skipped}"
    assert valid[0]["phone"] == "555-1234", f"phone dropped for header {phone_header!r}"
    assert valid[0]["email"] == "x@y.com", f"email dropped for header {email_header!r}"


# ── confirm round-trip through JSON (preview → confirm path) ─────────────────

def test_json_round_trip_preserves_phone_email():
    import json
    csv_text = "Customer Name,Phone Number,Email Address\nACME,303-555-1234,bob@acme.com\n"
    valid, _ = _run(csv_text)
    assert valid
    serialized = json.dumps(valid)
    decoded = json.loads(serialized)
    assert decoded[0]["phone"] == "303-555-1234"
    assert decoded[0]["email"] == "bob@acme.com"
