"""
tests/test_lost_sales.py
========================
Phase 2 §9 / P2-D7 — structured lost-sales capture on quote mark-lost.

Covers the structured reason + note + competitor fields, the per-product-line
log rows, the quote-level row when there are no product lines, legacy free-text
back-compat (→ OTHER, preserved in the note), and the router endpoint.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import LineType, LostReason, QuoteOutcome, QuoteStatus
from app.models.customer import Customer
from app.models.product import Product
from app.models.quote import LostSaleLog, Quote, QuoteLine
from app.services.quote_service import QuoteService
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _customer(db):
    c = Customer(company_name=f"Lost Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    p = Product(sku=f"P{next(_seq)}", title="Injector", cost=50.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _quote(db, cust, *, with_products=2):
    q = Quote(quote_number=f"Q-{next(_seq):05d}", customer_id=cust.id,
              status=QuoteStatus.SENT, outcome=QuoteOutcome.PENDING)
    for _ in range(with_products):
        prod = _product(db)
        q.lines.append(QuoteLine(line_type=LineType.PRODUCT, product_id=prod.id,
                                 qty=1, unit_price=100.0, description="line"))
    db.add(q); db.commit(); db.refresh(q)
    return q


def _logs_for(db, quote_id):
    return db.query(LostSaleLog).filter(LostSaleLog.quote_id == quote_id).all()


def test_mark_lost_sets_outcome_and_status(db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    QuoteService(db, None).mark_lost(q.id, LostReason.PRICE)
    db.refresh(q)
    assert q.outcome == QuoteOutcome.LOST
    assert q.status == QuoteStatus.DECLINED
    assert q.lost_reason.startswith(LostReason.PRICE)


def test_logs_one_row_per_product_line(db):
    c = _customer(db)
    q = _quote(db, c, with_products=3)
    QuoteService(db, None).mark_lost(q.id, LostReason.LEAD_TIME)
    logs = _logs_for(db, q.id)
    assert len(logs) == 3
    assert all(lg.reason == LostReason.LEAD_TIME for lg in logs)
    assert all(lg.product_id is not None for lg in logs)


def test_competitor_fields_captured(db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    QuoteService(db, None).mark_lost(
        q.id, LostReason.COMPETITOR,
        competitor_name="Acme Diesel", competitor_price=88.50,
    )
    log = _logs_for(db, q.id)[0]
    assert log.reason == LostReason.COMPETITOR
    assert log.competitor_name == "Acme Diesel"
    assert log.competitor_price == 88.50


def test_competitor_fields_cleared_for_non_competitor_reason(db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    # competitor info supplied but reason isn't COMPETITOR → must be dropped
    QuoteService(db, None).mark_lost(
        q.id, LostReason.PRICE,
        competitor_name="Acme Diesel", competitor_price=88.50,
    )
    log = _logs_for(db, q.id)[0]
    assert log.competitor_name is None
    assert log.competitor_price is None


def test_note_is_recorded(db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    QuoteService(db, None).mark_lost(q.id, LostReason.NO_RESPONSE, note="Left 2 VMs")
    db.refresh(q)
    assert "Left 2 VMs" in q.lost_reason
    assert _logs_for(db, q.id)[0].notes == "Left 2 VMs"


def test_quote_with_no_product_lines_still_logs_once(db):
    c = _customer(db)
    q = _quote(db, c, with_products=0)
    QuoteService(db, None).mark_lost(q.id, LostReason.NO_LONGER_NEEDED)
    logs = _logs_for(db, q.id)
    assert len(logs) == 1
    assert logs[0].product_id is None
    assert logs[0].reason == LostReason.NO_LONGER_NEEDED


def test_legacy_freetext_reason_maps_to_other(db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    # Legacy callers passed a free string; must not crash, maps to OTHER and the
    # text is preserved (in the note) rather than lost.
    QuoteService(db, None).mark_lost(q.id, "Customer ghosted us")
    db.refresh(q)
    log = _logs_for(db, q.id)[0]
    assert log.reason == LostReason.OTHER
    assert "Customer ghosted us" in q.lost_reason
    assert "Customer ghosted us" in log.notes


def test_mark_lost_route(client, db):
    c = _customer(db)
    q = _quote(db, c, with_products=1)
    r = client.post(
        f"/quotes/{q.id}/mark-lost",
        data={"lost_reason": LostReason.COMPETITOR.value,
              "note": "cheaper elsewhere", "competitor_name": "BudgetParts",
              "competitor_price": "75"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    q2 = db.query(Quote).get(q.id)
    assert q2.outcome == QuoteOutcome.LOST
    log = _logs_for(db, q.id)[0]
    assert log.reason == LostReason.COMPETITOR
    assert log.competitor_name == "BudgetParts"
    assert log.competitor_price == 75.0
