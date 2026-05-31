"""
tests/test_smoke_subendpoints.py
=================================
Expanded smoke gate -- covers HTMX sub-endpoints, workspace GETs, preview
panels, and mutating POSTs that the top-level list smoke does NOT touch.

WHY THIS FILE EXISTS
  The owner's session was crashed by a 500 on /sales-orders/_/product-search
  (sales_orders.py:382 references Product.part_number -- no such column).
  The existing test_smoke.py only hits top-level list routes and would never
  have caught this.

  This suite catches that class of breakage by exercising every sub-endpoint
  the workspaces depend on.

STABLE SERVER (no --reload)
  Concurrent lane edits to app/ can hot-reload the server mid-test and produce
  spurious 500s or corrupt test state.  Always run the server reload-OFF:

    # Start once in a dedicated terminal:
    cd C:\\Users\\keith\\JAKSInventory\\rebuild
    .venv\\Scripts\\python.exe -m uvicorn app.main:app ^
        --host 127.0.0.1 --port 8001 --no-reload

    # Run tests (TestClient -- in-process, no network needed):
    .venv\\Scripts\\pytest tests/test_smoke_subendpoints.py -v

  NEVER use --reload during QA passes.

RE-BASELINE RULE
  payments_list pixel baseline must be re-captured once the dock fix lands
  (current baseline was captured with the preview_dock_shell commented out).
  Run after backend confirms /payments/preview/{id} is registered:
    python tests/visual/capture_baselines.py --force --only payments_list
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tests.conftest import activate, fresh_engine
from app.main import app
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# DB isolation
# ---------------------------------------------------------------------------

_ENGINE = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)


# ---------------------------------------------------------------------------
# Seed -- REAL COLUMNS ONLY (no @property attributes)
#
# Many model aggregates (subtotal, total, tax, amount_unallocated,
# selling_price, qty_available) are @property, not stored columns.
# Setting them raises AttributeError -- use only mapped_column fields.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ids():
    """Create one of each entity type; return a dict of primary-key IDs."""
    from app.database import SessionLocal
    db = SessionLocal()

    # Customer
    from app.models.customer import Customer
    cust = Customer(company_name="Smoke Test Co", contact_name="QA", email="qa@test.local")
    db.add(cust); db.flush()

    # Vendor
    from app.models.vendor import Vendor
    vendor = Vendor(name="Smoke Vendor")
    db.add(vendor); db.flush()

    # Product -- no selling_price / qty_available (both @property)
    from app.models.product import Product
    prod = Product(sku="SMOKE-001", title="Smoke Part", description="QA part",
                   cost=5.0, markup_pct=100.0, qty_on_hand=5, is_active=True)
    db.add(prod); db.flush()

    # Purchase Order
    from app.models.purchase_order import PurchaseOrder, POStatus
    po = PurchaseOrder(po_number="PO-SMOKE-001", vendor_id=vendor.id, status=POStatus.DRAFT)
    db.add(po); db.flush()

    # Quote -- subtotal/total/is_expired are @property; do NOT pass them
    from app.models.quote import Quote, QuoteStatus
    quote = Quote(quote_number="Q-SMOKE-001", customer_id=cust.id,
                  status=QuoteStatus.DRAFT)
    db.add(quote); db.flush()

    # Sales Order -- SOStatus has no DRAFT; starts at OPEN
    # subtotal/total are @property; do NOT pass them
    from app.models.quote import SalesOrder
    from app.constants import SOStatus
    so = SalesOrder(so_number="SO-SMOKE-001", customer_id=cust.id,
                    status=SOStatus.OPEN)
    db.add(so); db.flush()

    # Invoice -- subtotal/total/tax are @property; do NOT pass them
    from app.models.invoice import Invoice, InvoiceStatus
    inv = Invoice(invoice_number="INV-SMOKE-001", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.flush()

    # Payment -- amount_unallocated is @property; do NOT pass it
    from app.models.invoice import Payment, PaymentMethod, PaymentStatus
    pmt = Payment(customer_id=cust.id, amount_received=100.0,
                  payment_date=datetime.date.today(),
                  payment_method=PaymentMethod.CASH,
                  status=PaymentStatus.APPLIED)
    db.add(pmt); db.flush()

    # Return Authorization -- RAStatus.DRAFT exists
    from app.models.returns import ReturnAuthorization, RAStatus
    ra = ReturnAuthorization(ra_number="RA-SMOKE-001", customer_id=cust.id,
                             status=RAStatus.DRAFT)
    db.add(ra); db.flush()

    db.commit()
    result = dict(customer_id=cust.id, vendor_id=vendor.id, product_id=prod.id,
                  po_id=po.id, quote_id=quote.id, so_id=so.id,
                  invoice_id=inv.id, payment_id=pmt.id, ra_id=ra.id)
    db.close()
    return result


@pytest.fixture(scope="module")
def client(ids):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _ok(resp, label: str) -> None:
    """Assert the response is not a 5xx server error."""
    assert resp.status_code < 500, (
        f"\n{label}\n"
        f"  HTTP {resp.status_code}\n"
        f"  url: {resp.url}\n"
        f"  body:\n{resp.text[:600]}"
    )


# ---------------------------------------------------------------------------
# 1. Product-search HTMX endpoints
#    The §8H line-adder migration retired the per-document search endpoints: every
#    workspace's shared adder (line_items/_line_adder.html) now calls the canonical
#    JSON endpoint GET /line-items/product-search (asserted in §1b below, and
#    contract-locked in test_line_item_builder.py).
#
#    /quotes/product-search, /sales-orders/_/product-search and
#    /invoices/_/product-search are now ORPHANED (no template calls them) and are
#    slated for Backend deletion (Contract §4) — their smoke cases are dropped so
#    the suite won't 404 post-cleanup.
#
#    /purchase-orders/_/product-search is RETAINED on purpose: products/detail.html
#    (the PO-line product search widget, ~line 663) still drives this endpoint and
#    has NOT migrated. Do NOT drop these cases or let Backend delete the route until
#    that widget is moved to /line-items/product-search.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,label", [
    ("/purchase-orders/_/product-search",             "PO product-search empty"),
    ("/purchase-orders/_/product-search?q=SMOKE",     "PO product-search match"),
], ids=lambda x: x if x.startswith("/") else x.replace(" ", "_"))
def test_product_search(client, ids, url, label):
    """Legacy per-doc product-search must not 500. Only the PO endpoint remains —
    still used by products/detail.html (see the §1 note above)."""
    _ok(client.get(url), label)


# ---------------------------------------------------------------------------
# 1b. Canonical line-item product search  (GET /line-items/product-search → JSON)
#     The single endpoint every §8H workspace adder points at. Returns JSON (not an
#     HTML partial), so assert shape + that a real query finds the seed. This is a
#     liveness guard; the deep JSON contract is locked in test_line_item_builder.py.
# ---------------------------------------------------------------------------

def test_line_items_product_search(client, ids):
    # Min-length guard: <2 chars → empty list (no 500, no results).
    assert client.get("/line-items/product-search").json() == []
    assert client.get("/line-items/product-search?q=s").json() == []
    # A real query returns a JSON list and finds the seeded product (exact
    # normalized SKU match), carrying the keys the line-adder reads off each hit.
    resp = client.get("/line-items/product-search?q=SMOKE-001")
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert isinstance(body, list) and body, f"expected >=1 hit, got {body!r}"
    hit = next((h for h in body if h["product_id"] == ids["product_id"]), None)
    assert hit is not None, f"seeded product not in results: {body!r}"
    assert {"product_id", "sku", "title"}.issubset(hit), hit


# ---------------------------------------------------------------------------
# 2. Preview panel HTMX endpoints  (/preview/{id} dock body)
# ---------------------------------------------------------------------------

_PREVIEWS = [
    ("quote preview",   "/quotes/preview/{quote_id}"),
    ("SO preview",      "/sales-orders/preview/{so_id}"),
    ("invoice preview", "/invoices/preview/{invoice_id}"),
    ("PO preview",      "/purchase-orders/preview/{po_id}"),
    ("payment preview", "/payments/preview/{payment_id}"),
    ("return preview",  "/returns/preview/{ra_id}"),
]


@pytest.mark.parametrize("label,tpl", _PREVIEWS, ids=[r[0] for r in _PREVIEWS])
def test_preview_panel(client, ids, label, tpl):
    _ok(client.get(tpl.format(**ids)), label)


# ---------------------------------------------------------------------------
# 3. Workspace GET routes  (/{id} full page)
# ---------------------------------------------------------------------------

_WORKSPACES = [
    ("quote workspace",   "/quotes/{quote_id}"),
    ("SO workspace",      "/sales-orders/{so_id}"),
    ("invoice workspace", "/invoices/{invoice_id}"),
    ("PO workspace",      "/purchase-orders/{po_id}"),
    ("payment detail",    "/payments/{payment_id}"),
    ("return workspace",  "/returns/{ra_id}"),
]


@pytest.mark.parametrize("label,tpl", _WORKSPACES, ids=[r[0] for r in _WORKSPACES])
def test_workspace_get(client, ids, label, tpl):
    _ok(client.get(tpl.format(**ids), follow_redirects=True), label)


# ---------------------------------------------------------------------------
# 4. HTMX sub-panel GETs  (totals, lines tbody, etc.)
# ---------------------------------------------------------------------------

_SUBPANELS = [
    ("quote totals panel", "/quotes/{quote_id}/totals"),
    ("quote lines tbody",  "/quotes/{quote_id}/lines"),
]


@pytest.mark.parametrize("label,tpl", _SUBPANELS, ids=[r[0] for r in _SUBPANELS])
def test_htmx_subpanel(client, ids, label, tpl):
    _ok(client.get(tpl.format(**ids)), label)


# ---------------------------------------------------------------------------
# 5. Mutating POST probes  (minimal payload -- verifies route is live)
#    Accepts 200 / 302 / 4xx.  Fails only on 5xx.
# ---------------------------------------------------------------------------

_POSTS = [
    ("quote autosave",   "/quotes/{quote_id}/autosave",     {}),
    ("quote header",     "/quotes/{quote_id}/header",       {}),
    ("SO header",        "/sales-orders/{so_id}/header",    {}),
    ("invoice header",   "/invoices/{invoice_id}/header",   {}),
    ("PO header",        "/purchase-orders/{po_id}/header", {}),
]


@pytest.mark.parametrize("label,tpl,data", _POSTS, ids=[r[0] for r in _POSTS])
def test_mutating_post_smoke(client, ids, label, tpl, data):
    """Minimal POST -- verifies route is registered and handler doesn't 500.
    A 422 (validation) or 400 (bad request) is acceptable.
    """
    _ok(client.post(tpl.format(**ids), data=data, follow_redirects=False), label)


# ---------------------------------------------------------------------------
# 6. Add-line POSTs
# ---------------------------------------------------------------------------

_ADD_LINES = [
    ("quote add-line",   "/quotes/{quote_id}/lines",        {"product_id": "{product_id}", "qty": "1", "unit_price": "10.00"}),
    ("SO add-line",      "/sales-orders/{so_id}/lines",     {"product_id": "{product_id}", "qty": "1", "unit_price": "10.00"}),
    ("invoice add-line", "/invoices/{invoice_id}/lines",    {"product_id": "{product_id}", "qty": "1", "unit_price": "10.00"}),
    ("PO add-line",      "/purchase-orders/{po_id}/lines",  {"product_id": "{product_id}", "qty": "1", "unit_cost": "5.00"}),
]


@pytest.mark.parametrize("label,tpl,data_tpl", _ADD_LINES, ids=[r[0] for r in _ADD_LINES])
def test_add_line(client, ids, label, tpl, data_tpl):
    url = tpl.format(**ids)
    data = {k: str(v).format(**{kk: str(vv) for kk, vv in ids.items()})
            for k, v in data_tpl.items()}
    _ok(client.post(url, data=data, follow_redirects=False), label)


# ---------------------------------------------------------------------------
# 7. Non-existent ID -- must 404, never 500
# ---------------------------------------------------------------------------

_BIG_ID = 999_999_999

_NOT_FOUND = [
    ("quote preview 404",   f"/quotes/preview/{_BIG_ID}"),
    ("SO preview 404",      f"/sales-orders/preview/{_BIG_ID}"),
    ("invoice preview 404", f"/invoices/preview/{_BIG_ID}"),
    ("PO preview 404",      f"/purchase-orders/preview/{_BIG_ID}"),
    ("payment preview 404", f"/payments/preview/{_BIG_ID}"),
    ("return preview 404",  f"/returns/preview/{_BIG_ID}"),
]


@pytest.mark.parametrize("label,url", _NOT_FOUND, ids=[r[0] for r in _NOT_FOUND])
def test_preview_nonexistent_not_500(client, label, url):
    """Preview routes must respond 404/200-empty, never 500, for unknown IDs."""
    r = client.get(url, follow_redirects=False)
    assert r.status_code != 500, (
        f"{label}: HTTP 500 for non-existent ID (should be 404 or empty 200)\n"
        f"body: {r.text[:300]}"
    )
