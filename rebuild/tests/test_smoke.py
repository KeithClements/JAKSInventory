"""
tests/test_smoke.py
====================
27-route HTTP smoke test.

Every URL listed below must return 200 (or 302 redirect for legacy routes).
Run with:
    DATABASE_URL=sqlite:///./data/jaks_test_smoke.db .venv/Scripts/python.exe -m pytest tests/test_smoke.py -v

The test uses FastAPI's TestClient with raise_server_exceptions=True so any
unhandled server exception is surfaced as a test failure, not a 500 response.
A fresh DB is created from init_db() at the start of each session; the smoke DB
file is listed in .gitignore and separate from the live data/jaks.db.
"""
import pathlib
import sys

# Ensure the project root is on sys.path so `import app` works
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

# Dedicated isolated in-memory engine — never touches the live data/jaks.db.
_TEST_ENGINE = fresh_engine()


@pytest.fixture(scope="session")
def client():
    """Single TestClient for the whole session, bound to the isolated engine.
    activate() re-points the app's DB globals before startup runs init_db()."""
    activate(_TEST_ENGINE)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Page list — every entry must return HTTP 200
# ---------------------------------------------------------------------------

SMOKE_PAGES = [
    # Dashboard
    ("/", "dashboard"),

    # Products
    ("/products/", "products list"),
    ("/products/new", "new product form"),

    # Customers
    ("/customers/", "customers list"),
    ("/customers/new", "new customer form"),

    # Vendors
    ("/vendors/", "vendors list"),
    ("/vendors/new", "new vendor form"),

    # Purchase Orders
    ("/purchase-orders/", "PO list"),
    ("/purchase-orders/new", "new PO form"),

    # Quotes
    ("/quotes/", "quotes list"),
    ("/quotes/new", "new quote form"),

    # Sales Orders
    ("/sales-orders/", "SO list"),
    ("/sales-orders/new", "new SO form"),

    # Invoices
    ("/invoices/", "invoices list"),
    ("/invoices/new", "new invoice form"),

    # Payments
    ("/payments/", "payments list"),
    ("/payments/new", "new payment form"),

    # Core Charges
    ("/cores/", "cores list"),

    # Settings
    ("/settings/", "settings"),

    # Warranty
    ("/warranty/", "warranty list"),
    ("/warranty/new", "new warranty claim"),

    # Returns
    ("/returns/", "returns list"),
    ("/returns/new", "new return form"),

    # Reports
    ("/reports/", "reports home"),
    ("/reports/sales/", "sales report"),
    ("/reports/inventory/", "inventory report"),

    # Global search — the Ctrl+K overlay JSON endpoint (the legacy /search
    # header-dropdown page was removed with its template).
    ("/search/overlay?q=test", "search"),
]


@pytest.mark.parametrize("url,label", SMOKE_PAGES, ids=[p[1] for p in SMOKE_PAGES])
def test_page_200(client, url, label):
    """Each page must return 200 with raise_server_exceptions=True."""
    response = client.get(url, follow_redirects=True)
    assert response.status_code == 200, (
        f"[{label}] GET {url} → {response.status_code}\n"
        f"Body snippet: {response.text[:400]}"
    )
