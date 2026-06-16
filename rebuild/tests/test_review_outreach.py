"""
tests/test_review_outreach.py
=============================
Review-seeding kit (app/services/review_outreach_service.py + the two
/customers/review-requests/*.csv routes).

The storefront has ~0 reviews; this feature mines finalized sales history into
Judge.me (product) and Google (company) review-request lists. These tests pin
the behaviour that matters for trust + compliance:

  - only finalized sales feed the lists (no drafts/voids)
  - consent is respected (do_not_contact / opted-out / email-off are excluded)
  - non-product lines and un-linked products never produce a Judge.me row
  - the (customer, product) dedupe keeps the most recent purchase
  - the Judge.me date is dd/mm/yyyy and product_id is the numeric Shopify id
  - paid_only toggles open/partial inclusion
  - both CSV routes stream the right headers + rows

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_review_outreach.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import InvoiceStatus, LineType
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.services.review_outreach_service import ReviewOutreachService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Builders ───────────────────────────────────────────────────────────────--

def _customer(db, **kw) -> Customer:
    defaults = dict(
        company_name=f"Diesel Co {next(_seq)}",
        contact_name="Shop Tech",
        email=f"buyer{next(_seq)}@test.local",
        allow_email=True,
    )
    defaults.update(kw)
    c = Customer(**defaults)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db, *, title="Cat C15 Cylinder Head", shopify_id="gid://shopify/Product/777") -> Product:
    p = Product(sku=f"JAKS-C15-INJ-9{next(_seq):04d}", title=title, shopify_product_id=shopify_id)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, customer_id, lines, *, status=InvoiceStatus.PAID, created_at=None) -> Invoice:
    inv = Invoice(
        invoice_number=f"INV-REV-{next(_seq):05d}",
        customer_id=customer_id,
        status=status,
        tax_rate=0.0, tax_rate_snapshot=0.0, tax_exempt_snapshot=True,
    )
    if created_at is not None:
        inv.created_at = created_at
    db.add(inv); db.flush()
    for ln in lines:
        db.add(InvoiceLine(invoice_id=inv.id, **ln))
    db.commit(); db.refresh(inv)
    return inv


def _pline(product_id, *, qty=1, price=1000.0) -> dict:
    return dict(line_type=LineType.PRODUCT, product_id=product_id, description="part",
                qty=qty, unit_price=price, unit_cost=price * 0.6, is_taxable=False)


# ── judgeme_rows ─────────────────────────────────────────────────────────────

def test_judgeme_basic_row(db):
    c = _customer(db, email="happy@test.local")
    p = _product(db, shopify_id="gid://shopify/Product/12345")
    _invoice(db, c.id, [_pline(p.id, qty=3)], created_at=datetime(2026, 3, 9))

    rows = ReviewOutreachService(db).judgeme_rows()
    mine = [r for r in rows if r["reviewer_email"] == "happy@test.local"]
    assert len(mine) == 1
    r = mine[0]
    assert r["product_id"] == "12345"          # numeric id parsed from GID
    assert r["fulfilled_at"] == "09/03/2026"    # dd/mm/yyyy
    assert r["quantity"] == 3
    assert r["reviewer_name"]                    # display name present


def test_judgeme_excludes_do_not_contact_and_optout(db):
    dnc = _customer(db, email="dnc@test.local", do_not_contact=True)
    opted = _customer(db, email="opted@test.local", opt_out_at=datetime(2026, 1, 1))
    p = _product(db)
    _invoice(db, dnc.id, [_pline(p.id)])
    _invoice(db, opted.id, [_pline(p.id)])

    emails = {r["reviewer_email"] for r in ReviewOutreachService(db).judgeme_rows()}
    assert "dnc@test.local" not in emails
    assert "opted@test.local" not in emails


def test_judgeme_excludes_email_off(db):
    c = _customer(db, email="noemail@test.local", allow_email=False)
    p = _product(db)
    _invoice(db, c.id, [_pline(p.id)])
    emails = {r["reviewer_email"] for r in ReviewOutreachService(db).judgeme_rows()}
    assert "noemail@test.local" not in emails


def test_judgeme_excludes_nonproduct_and_unlinked(db):
    c = _customer(db, email="mixed@test.local")
    linked = _product(db, shopify_id="gid://shopify/Product/55501")
    unlinked = _product(db, shopify_id="")  # not synced to Shopify
    _invoice(db, c.id, [
        _pline(linked.id),
        _pline(unlinked.id),
        dict(line_type=LineType.FREIGHT, description="Freight", qty=1,
             unit_price=120.0, unit_cost=120.0, is_taxable=False),
    ])

    rows = [r for r in ReviewOutreachService(db).judgeme_rows()
            if r["reviewer_email"] == "mixed@test.local"]
    assert {r["product_id"] for r in rows} == {"55501"}  # only the linked product


def test_judgeme_dedupes_to_most_recent(db):
    c = _customer(db, email="repeat@test.local")
    p = _product(db, shopify_id="gid://shopify/Product/99001")
    _invoice(db, c.id, [_pline(p.id)], created_at=datetime(2026, 1, 5))
    _invoice(db, c.id, [_pline(p.id)], created_at=datetime(2026, 4, 20))

    rows = [r for r in ReviewOutreachService(db).judgeme_rows()
            if r["reviewer_email"] == "repeat@test.local" and r["product_id"] == "99001"]
    assert len(rows) == 1
    assert rows[0]["fulfilled_at"] == "20/04/2026"  # newer purchase wins


def test_judgeme_paid_only_toggle(db):
    c = _customer(db, email="openonly@test.local")
    p = _product(db, shopify_id="gid://shopify/Product/40404")
    _invoice(db, c.id, [_pline(p.id)], status=InvoiceStatus.OPEN)

    svc = ReviewOutreachService(db)
    paid = {r["reviewer_email"] for r in svc.judgeme_rows(paid_only=True)}
    allf = {r["reviewer_email"] for r in svc.judgeme_rows(paid_only=False)}
    assert "openonly@test.local" not in paid       # OPEN excluded when paid_only
    assert "openonly@test.local" in allf           # included when paid_only=False


def test_judgeme_excludes_draft(db):
    c = _customer(db, email="draft@test.local")
    p = _product(db, shopify_id="gid://shopify/Product/30303")
    _invoice(db, c.id, [_pline(p.id)], status=InvoiceStatus.DRAFT)
    svc = ReviewOutreachService(db)
    assert "draft@test.local" not in {r["reviewer_email"] for r in svc.judgeme_rows(paid_only=False)}


# ── google_outreach_rows ─────────────────────────────────────────────────────

def test_google_aggregates_and_sorts(db):
    big = _customer(db, company_name="Big Fleet LLC", email="big@test.local")
    small = _customer(db, company_name="Small Op", email="small@test.local")
    p = _product(db)
    _invoice(db, big.id, [_pline(p.id, price=5000.0)], created_at=datetime(2026, 2, 1))
    _invoice(db, big.id, [_pline(p.id, price=3000.0)], created_at=datetime(2026, 5, 1))
    _invoice(db, small.id, [_pline(p.id, price=200.0)], created_at=datetime(2026, 3, 1))

    rows = ReviewOutreachService(db).google_outreach_rows()
    by_co = {r["company_name"]: r for r in rows}
    assert by_co["Big Fleet LLC"]["orders"] == 2
    assert float(by_co["Big Fleet LLC"]["total_spent"]) == pytest.approx(8000.0)
    assert by_co["Big Fleet LLC"]["last_purchase"] == "2026-05-01"
    # best customer sorts ahead of the small one
    cos = [r["company_name"] for r in rows]
    assert cos.index("Big Fleet LLC") < cos.index("Small Op")


def test_google_includes_sms_only_customer(db):
    c = _customer(
        db, company_name="Text Only Co", email="", phone="720-555-0143",
        allow_email=False, allow_sms=True, sms_consent_at=datetime(2026, 1, 1),
    )
    p = _product(db)
    _invoice(db, c.id, [_pline(p.id)])

    row = next(r for r in ReviewOutreachService(db).google_outreach_rows()
               if r["company_name"] == "Text Only Co")
    assert row["allow_sms"] == "yes"
    assert row["allow_email"] == "no"
    assert row["phone"] == "720-555-0143"
    assert row["email"] == ""


def test_google_excludes_do_not_contact(db):
    c = _customer(db, company_name="DNC Co", email="dnc2@test.local", do_not_contact=True)
    p = _product(db)
    _invoice(db, c.id, [_pline(p.id)])
    cos = {r["company_name"] for r in ReviewOutreachService(db).google_outreach_rows()}
    assert "DNC Co" not in cos


# ── HTTP routes ──────────────────────────────────────────────────────────────

def test_judgeme_csv_route(db, client):
    c = _customer(db, email="route1@test.local")
    p = _product(db, shopify_id="gid://shopify/Product/61616")
    _invoice(db, c.id, [_pline(p.id)], created_at=datetime(2026, 6, 1))

    resp = client.get("/customers/review-requests/judgeme.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "judgeme_review_requests.csv" in resp.headers["content-disposition"]
    body = resp.text
    assert body.splitlines()[0] == "reviewer_name,reviewer_email,product_id,fulfilled_at,quantity"
    assert "route1@test.local" in body
    assert "61616" in body


def test_google_csv_route(db, client):
    c = _customer(db, company_name="Route Co", email="route2@test.local")
    p = _product(db)
    _invoice(db, c.id, [_pline(p.id)])

    resp = client.get("/customers/review-requests/google.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "google_review_outreach.csv" in resp.headers["content-disposition"]
    header = resp.text.splitlines()[0]
    assert header == ("customer_name,company_name,email,phone,allow_email,"
                      "allow_sms,orders,total_spent,last_purchase,sample_product")
    assert "route2@test.local" in resp.text
