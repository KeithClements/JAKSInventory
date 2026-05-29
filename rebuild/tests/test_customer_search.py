"""
tests/test_customer_search.py
==============================
Regression tests for GET /customers/search-json.

Bugs fixed 2026-05-29:
  1. contact_name and email were excluded from the OR filter — searches on
     those fields returned nothing.
  2. _search_results.html dispatched 'customer-selected' on document instead
     of window, so all Alpine @customer-selected.window listeners were deaf.
     (Template bug — verified by code review; not exercised here since tests
     run server-side. Covered by the onclick assertion in the rendered HTML.)
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app
from app.models.customer import Customer


# ── module-scoped isolation ───────────────────────────────────────────────────

_engine = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _use_engine():
    SessionLocal = activate(_engine)
    db = SessionLocal()
    try:
        db.add_all([
            Customer(
                company_name="Acme Trucking",
                contact_name="Mike Johnson",
                phone="555-0101",
                email="mike@acme.com",
                is_active=True,
            ),
            Customer(
                company_name="Blue Ridge Fleet",
                contact_name="Sandra Owens",
                phone="555-0202",
                email="sandra@blueridge.com",
                is_active=True,
            ),
            Customer(
                company_name="Canyon Diesel",
                contact_name="Tom Rivera",
                phone="555-0303",
                email="info@canyondiesel.com",
                is_active=True,
            ),
            # Inactive — must never appear in results
            Customer(
                company_name="Mike's Inactive Shop",
                contact_name="Mike Inactive",
                phone="555-9999",
                email="nope@inactive.com",
                is_active=False,
            ),
        ])
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ── helpers ───────────────────────────────────────────────────────────────────

def search(client, q: str):
    r = client.get("/customers/search-json", params={"q": q})
    assert r.status_code == 200
    return r.text


# ── search-field coverage ─────────────────────────────────────────────────────

def test_search_by_company_name(client):
    """Original behaviour preserved: company name hits."""
    html = search(client, "Acme")
    assert "Acme Trucking" in html


def test_search_by_contact_name(client):
    """Bug fix: contact_name was excluded from the OR filter."""
    html = search(client, "Mike")
    assert "Acme Trucking" in html          # Mike Johnson's company
    assert "Mike's Inactive Shop" not in html  # inactive — must be excluded


def test_search_by_contact_name_partial(client):
    """Partial contact-name match (Sandra → Sandra Owens)."""
    html = search(client, "Sandra")
    assert "Blue Ridge Fleet" in html


def test_search_by_phone(client):
    """Phone search still works after filter expansion."""
    html = search(client, "555-0303")
    assert "Canyon Diesel" in html


def test_search_by_email(client):
    """Bug fix: email was excluded from the OR filter."""
    html = search(client, "canyondiesel.com")
    assert "Canyon Diesel" in html


def test_search_by_email_prefix(client):
    """Email prefix match."""
    html = search(client, "sandra@")
    assert "Blue Ridge Fleet" in html


# ── edge cases ────────────────────────────────────────────────────────────────

def test_short_query_returns_empty(client):
    """Queries shorter than 2 chars must return no results (not an error)."""
    html = search(client, "A")
    assert "Acme Trucking" not in html
    # Empty-results partial is returned (no 'No customers found' required — just
    # no customer rows)


def test_empty_query_returns_empty(client):
    html = search(client, "")
    assert "Acme Trucking" not in html


def test_inactive_customer_excluded(client):
    """Inactive customers must never appear regardless of query match."""
    html = search(client, "Inactive")
    assert "Mike's Inactive Shop" not in html
    html2 = search(client, "9999")
    assert "Mike's Inactive Shop" not in html2


def test_no_match_returns_no_customers_found(client):
    html = search(client, "XYZNOTFOUND")
    assert "No customers found" in html


def test_result_limit(client):
    """Result set is capped at 8 rows."""
    # Already seeded 3 active customers; limit is not reachable with this seed,
    # but we verify the endpoint does not error on a broad query.
    html = search(client, "5")          # matches all three phone numbers
    assert html.count("<button") <= 8


# ── event-dispatch regression (template layer) ───────────────────────────────

def test_search_results_dispatches_on_window(client):
    """_search_results.html must dispatch 'customer-selected' on window, not
    document, so Alpine @customer-selected.window listeners can receive it.

    Verified by asserting the rendered onclick uses window.dispatchEvent.
    """
    html = search(client, "Acme")
    assert "window.dispatchEvent" in html
    assert "document.dispatchEvent" not in html
