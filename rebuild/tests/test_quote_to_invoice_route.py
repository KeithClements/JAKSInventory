"""
tests/test_quote_to_invoice_route.py
======================================
Regression guard for the Quote → Invoice conversion route wiring.

Bug history: the Quotes List "→ Invoice" button posted to
`/quotes/{id}/convert` (no such route) instead of the real
`/quotes/{id}/convert-to-invoice`, so clicking it returned a raw
{"detail":"Not Found"} JSON 404.

These HTTP-level tests assert the conversion path NEVER lands on a JSON 404:
the button URL must resolve, the route must 303-redirect, and the redirect
target must be a valid invoice workspace page.

Run with:
    DATABASE_URL=sqlite:///./data/jaks_test_smoke.db .venv/Scripts/python.exe -m pytest tests/test_quote_to_invoice_route.py -v
"""
import itertools
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# Dedicated smoke DB — never touches the live data/jaks.db
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/jaks_test_smoke.db")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.constants import LineType, QuoteStatus
from app.models.customer import Customer
from app.models.product import Product
from app.services.quote_service import QuoteService

_UID = 1  # single-user app — get_current_user_id() always returns 1
_counter = itertools.count(1)  # unique SKUs / company names across fixture calls


@pytest.fixture(scope="module")
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def quote_with_line():
    """Create a SENT quote with one product line; yield its id."""
    n = next(_counter)
    db = SessionLocal()
    try:
        cust = Customer(company_name=f"RouteTest Co {n}", is_active=True)
        db.add(cust)

        prod = Product(
            sku=f"RT-INV-{n:04d}",
            title="Route Test Widget",
            qty_on_hand=10,
            qty_committed=0,
            cost=10.0,
        )
        db.add(prod)
        db.flush()

        svc = QuoteService(db, _UID)
        quote = svc.create_quote(cust.id, {})
        # Button only renders for draft/sent/accepted quotes
        quote.status = QuoteStatus.SENT
        db.flush()
        svc.add_line(
            quote.id,
            prod.id,
            {"line_type": LineType.PRODUCT, "description": "Route Test Widget",
             "qty": 2, "unit_price": 50.0},
        )
        db.commit()
        qid = quote.id
    finally:
        db.close()
    return qid


def test_quotes_list_button_targets_real_route(client, quote_with_line):
    """The Quotes List '→ Invoice' button must post to the route that exists."""
    resp = client.get("/quotes/")
    assert resp.status_code == 200
    html = resp.text
    # Correct wiring present...
    assert f"/quotes/{quote_with_line}/convert-to-invoice" in html
    # ...and the old broken action is gone.
    assert f'action="/quotes/{quote_with_line}/convert"' not in html


def test_convert_to_invoice_redirects_not_404(client, quote_with_line):
    """POST the conversion route — must 303 to the invoice workspace, never JSON 404."""
    resp = client.post(
        f"/quotes/{quote_with_line}/convert-to-invoice",
        follow_redirects=False,
    )
    assert resp.status_code == 303, (
        f"expected 303 redirect, got {resp.status_code}: {resp.text[:300]}"
    )
    location = resp.headers["location"]
    assert re.fullmatch(r"/invoices/\d+", location), f"bad redirect target: {location}"
    # Never a raw JSON 'Not Found' body
    assert "application/json" not in resp.headers.get("content-type", "")
    assert "Not Found" not in resp.text


def test_conversion_path_lands_on_valid_workspace(client, quote_with_line):
    """Following the full path yields a 200 HTML invoice workspace — never JSON/404."""
    resp = client.post(
        f"/quotes/{quote_with_line}/convert-to-invoice",
        follow_redirects=True,
    )
    assert resp.status_code == 200, f"final page {resp.status_code}: {resp.text[:300]}"
    assert "text/html" in resp.headers.get("content-type", "")
    assert '{"detail"' not in resp.text  # not a raw FastAPI JSON error


def test_unknown_convert_route_is_the_only_404(client, quote_with_line):
    """Sanity: the OLD path really was a 404 — proving the fix moved off it."""
    resp = client.post(
        f"/quotes/{quote_with_line}/convert",
        follow_redirects=False,
    )
    assert resp.status_code == 404
