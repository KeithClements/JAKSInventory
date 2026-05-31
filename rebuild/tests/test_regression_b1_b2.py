"""
tests/test_regression_b1_b2.py
==============================
Regression gate for the bug class that let B1/B2 ship into the owner's live
session. Each guard here would have failed *before* the owner ever saw the bug.

WHY THESE THREE (owner mandate, 2026-05-30)
-------------------------------------------
1. test_so_product_search_returns_active_product
   GET /sales-orders/_/product-search?q=<sku> must return >= 1 row for a seeded
   ACTIVE product. The shipped bug filtered on a phantom column (Product.name /
   Product.part_number -- neither exists on the model) and 500'd. A weaker
   "does not 500" probe would *still pass* if the query silently returned zero
   rows, so this asserts the seeded product actually appears in the rendered
   dropdown -- and that an INACTIVE product is correctly hidden.

2. test_receive_so_linked_po_writes_inventory_txn
   POST /purchase-orders/{id}/receive on a PO whose line is linked to an SO line
   must COMPLETE and write an InventoryTransaction. The shipped bug was a
   SOLineStatus NameError inside POService._allocate_to_linked_sos -- but the
   po_receive route wraps create_receipt in `except Exception:` and turns any
   failure into a 303 redirect with `?error=...`, so the route NEVER returns a
   500. The existing 125 tests never created an SO-linked PO line, so nothing
   exercised that path at all. This guard asserts BOTH that the redirect is
   `?ok=received` (no `?error=`) AND that the ledger rows + line state prove the
   linked-SO allocation actually ran end to end.

3. test_every_listworkspace_route_returns_200 / test_workspace_route_returns_200
   Every no-param GET route (every list / index / new-form / report / queue /
   settings / search page) must return 200, enumerated DYNAMICALLY from
   app.routes so a newly added route is covered with zero edits here. The gap
   that reached the owner was a route that 500'd but was never added to the
   hardcoded smoke list in test_smoke.py. Workspace ({id}) routes for each
   seeded entity are also asserted at exactly 200 (stronger than the <500 probe
   in test_smoke_subendpoints).

RUN
    .venv\\Scripts\\python.exe -m pytest tests/test_regression_b1_b2.py -v

DB ISOLATION
    Owns a private in-memory engine (see tests/conftest.py); never touches the
    live data/jaks.db.
"""
from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from starlette.routing import Route
from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

# ---------------------------------------------------------------------------
# DB isolation -- private in-memory engine, re-pointed at run time.
# ---------------------------------------------------------------------------

_ENGINE = fresh_engine()


@pytest.fixture(scope="module", autouse=True)
def _activate():
    activate(_ENGINE)


# ---------------------------------------------------------------------------
# Distinctive seed tokens (unambiguous so product-search assertions are exact)
# ---------------------------------------------------------------------------

_ACTIVE_SKU = "REGR-ACTIVE-001"
_ACTIVE_TITLE = "Regression Active Widget"
_INACTIVE_SKU = "REGR-INACTIVE-001"

_RECV_QTY = 5  # PO line qty == SO line demand, so the SO line is fully reserved


@pytest.fixture(scope="module")
def seed():
    """Seed exactly what the three guards need and return ids/tokens.

    REAL COLUMNS ONLY -- aggregates like subtotal/total/selling_price are
    @property, not mapped columns; setting them raises AttributeError.
    """
    from app.database import SessionLocal
    from app.constants import (
        FulfillmentSource, InvoiceStatus, PaymentMethod, PaymentStatus,
        POStatus, ProductStatus, QuoteStatus, RAStatus, SOLineStatus, SOStatus,
    )
    from app.models.customer import Customer
    from app.models.vendor import Vendor
    from app.models.product import Product
    from app.models.purchase_order import PurchaseOrder, POLine
    from app.models.quote import Quote, SalesOrder, SOLine
    from app.models.invoice import Invoice, Payment
    from app.models.returns import ReturnAuthorization

    db = SessionLocal()
    try:
        cust = Customer(company_name="Regression Co", contact_name="QA",
                        email="qa@regr.local")
        db.add(cust); db.flush()

        vendor = Vendor(name="Regression Vendor")
        db.add(vendor); db.flush()

        # ACTIVE product -- starts at 0 on-hand so the post-receive count is exact.
        active = Product(sku=_ACTIVE_SKU, title=_ACTIVE_TITLE,
                         description=_ACTIVE_TITLE, cost=30.0, markup_pct=40.0,
                         qty_on_hand=0, status=ProductStatus.ACTIVE, is_active=True)
        db.add(active); db.flush()

        # INACTIVE product -- must be excluded by the product-search is_active filter.
        inactive = Product(sku=_INACTIVE_SKU, title="Regression Inactive Widget",
                           description="Regression Inactive Widget", cost=10.0,
                           markup_pct=40.0, qty_on_hand=0,
                           status=ProductStatus.INACTIVE, is_active=False)
        db.add(inactive); db.flush()

        # PO must be SENT for receive to be allowed by the route status guard.
        po = PurchaseOrder(po_number="PO-REGR-001", vendor_id=vendor.id,
                           status=POStatus.SENT)
        db.add(po); db.flush()
        po_line = POLine(po_id=po.id, product_id=active.id,
                         description=_ACTIVE_TITLE, qty_ordered=_RECV_QTY,
                         unit_cost=30.0)
        db.add(po_line); db.flush()

        # SO line LINKED to the PO line -- this is the path the 125 tests never hit.
        so = SalesOrder(so_number="SO-REGR-001", customer_id=cust.id,
                        status=SOStatus.OPEN)
        db.add(so); db.flush()
        so_line = SOLine(so_id=so.id, product_id=active.id, qty_ordered=_RECV_QTY,
                         unit_price=100.0,
                         fulfillment_source=FulfillmentSource.LINKED_PO,
                         line_status=SOLineStatus.AWAITING_PO_RECEIPT,
                         linked_po_line_id=po_line.id)
        db.add(so_line); db.flush()

        # One of each remaining entity for the workspace-200 sweep.
        quote = Quote(quote_number="Q-REGR-001", customer_id=cust.id,
                      status=QuoteStatus.DRAFT)
        db.add(quote); db.flush()
        inv = Invoice(invoice_number="INV-REGR-001", customer_id=cust.id,
                      status=InvoiceStatus.DRAFT)
        db.add(inv); db.flush()
        pmt = Payment(customer_id=cust.id, amount_received=100.0,
                      payment_date=datetime.date.today(),
                      payment_method=PaymentMethod.CASH,
                      status=PaymentStatus.APPLIED)
        db.add(pmt); db.flush()
        ra = ReturnAuthorization(ra_number="RA-REGR-001", customer_id=cust.id,
                                 status=RAStatus.DRAFT)
        db.add(ra); db.flush()

        db.commit()
        return dict(
            customer_id=cust.id, vendor_id=vendor.id,
            active_product_id=active.id, inactive_product_id=inactive.id,
            po_id=po.id, po_line_id=po_line.id,
            so_id=so.id, so_line_id=so_line.id,
            quote_id=quote.id, invoice_id=inv.id,
            payment_id=pmt.id, ra_id=ra.id,
        )
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(seed):
    # raise_server_exceptions=True so a route that 500s surfaces its traceback
    # as the failure (more actionable than an opaque 500 body). seed runs first
    # so list pages render WITH data.
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# Guard 1 -- SO product-search returns real rows (would have caught Product.name)
# ===========================================================================

def test_so_product_search_returns_active_product(client, seed):
    r = client.get(f"/sales-orders/_/product-search?q=REGR-ACTIVE")
    assert r.status_code == 200, f"product-search -> {r.status_code}"
    assert _ACTIVE_SKU in r.text, (
        "seeded ACTIVE product is missing from the product-search results -- "
        "the endpoint returned zero rows (a phantom-column filter would do this "
        f"silently if it didn't 500 first):\n{r.text[:600]}"
    )
    assert "No products found" not in r.text


def test_so_product_search_excludes_inactive(client, seed):
    # q matches only the inactive SKU; the is_active filter must hide it entirely.
    r = client.get(f"/sales-orders/_/product-search?q=REGR-INACTIVE")
    assert r.status_code == 200, f"product-search -> {r.status_code}"
    assert _INACTIVE_SKU not in r.text, (
        "INACTIVE product leaked into product-search -- the is_active filter is "
        "not being applied"
    )
    assert "No products found" in r.text


# ===========================================================================
# Guard 2 -- receive on an SO-linked PO completes + writes the ledger
#            (would have caught the SOLineStatus NameError the route swallowed)
# ===========================================================================

def test_receive_so_linked_po_writes_inventory_txn(client, seed):
    from app.database import SessionLocal
    from app.constants import InventoryTxnType, SOLineStatus
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    from app.models.quote import SOLine

    po_id = seed["po_id"]
    po_line_id = seed["po_line_id"]
    so_line_id = seed["so_line_id"]
    product_id = seed["active_product_id"]

    # The route returns a 303 redirect either way; an internal exception is
    # swallowed into `?error=...`, so the redirect target is the real signal.
    r = client.post(
        f"/purchase-orders/{po_id}/receive",
        data={f"recv_{po_line_id}": str(_RECV_QTY)},
        follow_redirects=False,
    )
    assert r.status_code == 303, f"expected 303 redirect, got {r.status_code}\n{r.text[:400]}"
    loc = r.headers.get("location", "")
    assert "error=" not in loc, (
        "receive redirected with ?error= -- the handler swallowed an exception. "
        "This is exactly how the SOLineStatus NameError stayed invisible to a "
        f"status-code-only check:\n  location={loc}"
    )
    assert "ok=received" in loc, f"unexpected redirect target after receive: {loc}"

    # Behaviour-level proof that the linked-SO allocation path ran to completion.
    db = SessionLocal()
    try:
        recv_txns = (
            db.query(InventoryTransaction)
            .filter(InventoryTransaction.product_id == product_id,
                    InventoryTransaction.transaction_type == InventoryTxnType.PO_RECEIPT)
            .all()
        )
        assert len(recv_txns) >= 1, "no PO_RECEIPT inventory_transaction was written"
        assert any(t.qty_change == _RECV_QTY for t in recv_txns)

        commit_txns = (
            db.query(InventoryTransaction)
            .filter(InventoryTransaction.product_id == product_id,
                    InventoryTransaction.transaction_type == InventoryTxnType.SO_COMMITTED)
            .all()
        )
        assert len(commit_txns) >= 1, (
            "no SO_COMMITTED inventory_transaction -- _allocate_to_linked_sos did "
            "not complete (this is the exact line the SOLineStatus NameError broke)"
        )

        so_line = db.query(SOLine).filter(SOLine.id == so_line_id).first()
        assert so_line.qty_committed == _RECV_QTY, (
            f"SO line qty_committed={so_line.qty_committed}, expected {_RECV_QTY}"
        )
        assert so_line.line_status == SOLineStatus.RESERVED_STOCK, (
            f"SO line status={so_line.line_status!r}, expected RESERVED_STOCK"
        )

        product = db.query(Product).filter(Product.id == product_id).first()
        assert product.qty_on_hand == _RECV_QTY
        assert product.qty_committed == _RECV_QTY
    finally:
        db.close()


# ===========================================================================
# Guard 3 -- every list/workspace route returns 200 (would have caught the
#            TemplateResponse 500 on a route missing from the hardcoded list)
# ===========================================================================

# Enumerated dynamically: a new no-param GET route is covered with no edit here.
_NOPARAM_GET = sorted({
    r.path
    for r in app.routes
    if isinstance(r, Route) and r.methods and "GET" in r.methods and "{" not in r.path
})

# Framework-generated / non-HTML endpoints that are not app UI pages. Keep this
# list SHORT and justified -- every exclusion is a route nobody is smoke-testing.
_EXCLUDE_200 = {
    "/openapi.json",   # FastAPI-generated schema, not an app page
}

_PAGE_ROUTES = [p for p in _NOPARAM_GET if p not in _EXCLUDE_200]


@pytest.mark.parametrize("path", _PAGE_ROUTES, ids=_PAGE_ROUTES)
def test_every_listworkspace_route_returns_200(client, seed, path):
    """Every no-param GET route must return 200 (following legacy redirects)."""
    r = client.get(path, follow_redirects=True)
    assert r.status_code == 200, (
        f"GET {path} -> {r.status_code} (expected 200)\n{r.text[:400]}"
    )


_WORKSPACE_TEMPLATES = [
    ("customer detail",   "/customers/{customer_id}"),
    ("vendor detail",     "/vendors/{vendor_id}"),
    ("product detail",    "/products/{active_product_id}"),
    ("quote workspace",   "/quotes/{quote_id}"),
    ("SO workspace",      "/sales-orders/{so_id}"),
    ("invoice workspace", "/invoices/{invoice_id}"),
    ("PO workspace",      "/purchase-orders/{po_id}"),
    ("payment detail",    "/payments/{payment_id}"),
    ("return workspace",  "/returns/{ra_id}"),
]


@pytest.mark.parametrize("label,tpl", _WORKSPACE_TEMPLATES,
                         ids=[r[0] for r in _WORKSPACE_TEMPLATES])
def test_workspace_route_returns_200(client, seed, label, tpl):
    """Each entity workspace ({id}) page must return 200 for a seeded record."""
    url = tpl.format(**seed)
    r = client.get(url, follow_redirects=True)
    assert r.status_code == 200, f"[{label}] GET {url} -> {r.status_code}\n{r.text[:400]}"
