"""
tests/test_r2_quote_actions.py
==============================
R2 — two counter-workflow gaps on quotes:

1. POST /quotes/{id}/duplicate — wires the previously-dead
   QuoteService.duplicate_quote into a route + "Duplicate Quote" More-menu
   item. The copy is a fresh DRAFT with all lines (incl. prices) cloned, the
   original is untouched, and the action is allowed from ANY status —
   re-quoting a converted/declined quote is the whole point.
2. Structured Mark-Lost popover — the form now posts the real route params
   (lost_reason from the LostReason enum + competitor_name/competitor_price +
   note) so competitor intel lands in LostSaleLog. The route must tolerate
   exactly what a browser form posts: a blank <input type=number> submits
   competitor_price="" (a `float | None` Form param would 422 on that).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r2_quote_actions.py -q
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
    c = Customer(company_name=f"R2 Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    p = Product(sku=f"R2P{next(_seq)}", title="Turbo", cost=50.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _quote(db, cust, *, status=QuoteStatus.SENT, line_prices=(111.11, 222.22)):
    q = Quote(quote_number=f"Q-R2-{next(_seq):05d}", customer_id=cust.id,
              status=status, outcome=QuoteOutcome.PENDING)
    for i, price in enumerate(line_prices):
        prod = _product(db)
        q.lines.append(QuoteLine(line_type=LineType.PRODUCT, product_id=prod.id,
                                 qty=i + 1, unit_price=price, unit_cost=40.0 + i,
                                 description=f"r2 line {i}", sort_order=i))
    db.add(q); db.commit(); db.refresh(q)
    return q


def _dup_of(db, original_id):
    return (
        db.query(Quote)
        .filter(Quote.is_duplicate_of_quote_id == original_id)
        .first()
    )


def _logs_for(db, quote_id):
    return db.query(LostSaleLog).filter(LostSaleLog.quote_id == quote_id).all()


# ── (1) Duplicate Quote route ─────────────────────────────────────────────────

def test_duplicate_creates_new_draft_with_copied_lines(client, db):
    q = _quote(db, _customer(db), status=QuoteStatus.SENT)
    r = client.post(f"/quotes/{q.id}/duplicate", follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    dup = _dup_of(db, q.id)
    assert dup is not None
    # Redirects to the NEW quote's workspace with the created flash param
    assert r.headers["location"] == f"/quotes/{dup.id}?created=1"

    assert dup.status == QuoteStatus.DRAFT
    assert dup.outcome == QuoteOutcome.PENDING
    assert dup.quote_number != q.quote_number
    assert dup.quote_number.startswith("Q-")
    assert dup.customer_id == q.customer_id

    # All lines cloned — including prices, costs, and quantities
    orig = sorted(q.lines, key=lambda ln: ln.sort_order)
    copied = sorted(dup.lines, key=lambda ln: ln.sort_order)
    assert len(copied) == len(orig) == 2
    for o, c in zip(orig, copied):
        assert c.id != o.id
        assert c.quote_id == dup.id
        assert c.product_id == o.product_id
        assert c.unit_price == o.unit_price
        assert c.unit_cost == o.unit_cost
        assert c.qty == o.qty
        assert c.description == o.description

    # Original untouched
    db.refresh(q)
    assert q.status == QuoteStatus.SENT
    assert len(q.lines) == 2


def test_duplicate_from_declined_quote_allowed(client, db):
    """Duplicating a DEAD quote into a fresh draft is the daily use case —
    the service has no status guard, so the route must work from DECLINED."""
    q = _quote(db, _customer(db), status=QuoteStatus.DECLINED)
    r = client.post(f"/quotes/{q.id}/duplicate", follow_redirects=False)
    assert r.status_code == 303

    db.expire_all()
    dup = _dup_of(db, q.id)
    assert dup is not None
    assert dup.status == QuoteStatus.DRAFT
    assert len(dup.lines) == 2
    # The dead original stays declined
    db.refresh(q)
    assert q.status == QuoteStatus.DECLINED


# ── (2) Structured Mark Lost ──────────────────────────────────────────────────

def test_mark_lost_competitor_persists_intel(client, db):
    q = _quote(db, _customer(db), status=QuoteStatus.SENT)
    r = client.post(
        f"/quotes/{q.id}/mark-lost",
        data={
            "lost_reason": LostReason.COMPETITOR.value,
            "note": "beat us by $200",
            "competitor_name": "HHP Diesel",
            "competitor_price": "1234.56",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    db.expire_all()
    q2 = db.query(Quote).get(q.id)
    assert q2.status == QuoteStatus.DECLINED
    assert q2.outcome == QuoteOutcome.LOST

    logs = _logs_for(db, q.id)
    assert len(logs) == 2  # one row per product line
    for lg in logs:
        assert lg.reason == LostReason.COMPETITOR
        assert lg.competitor_name == "HHP Diesel"
        assert lg.competitor_price == 1234.56


def test_mark_lost_plain_reason_with_browser_blank_fields(client, db):
    """The popover always submits the competitor/note inputs; untouched they
    arrive as EMPTY STRINGS. A blank number input must not 422 and competitor
    fields must stay None on the log rows."""
    q = _quote(db, _customer(db), status=QuoteStatus.SENT)
    r = client.post(
        f"/quotes/{q.id}/mark-lost",
        data={
            "lost_reason": LostReason.NO_RESPONSE.value,
            "note": "",
            "competitor_name": "",
            "competitor_price": "",  # blank <input type=number> submits ""
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    db.expire_all()
    q2 = db.query(Quote).get(q.id)
    assert q2.outcome == QuoteOutcome.LOST

    logs = _logs_for(db, q.id)
    assert logs
    assert all(lg.reason == LostReason.NO_RESPONSE for lg in logs)
    assert all(lg.competitor_name is None for lg in logs)
    assert all(lg.competitor_price is None for lg in logs)


# ── Header-actions render ─────────────────────────────────────────────────────

def test_header_renders_duplicate_item_on_sent(client, db):
    q = _quote(db, _customer(db), status=QuoteStatus.SENT)
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200
    assert "Duplicate Quote" in r.text
    assert f"/quotes/{q.id}/duplicate" in r.text


def test_header_renders_duplicate_item_on_other_statuses(client, db):
    """R2 — Duplicate must be reachable from DRAFT / CONVERTED / DECLINED too
    (the More menu used to disappear entirely outside DRAFT/SENT)."""
    for status in (QuoteStatus.DRAFT, QuoteStatus.CONVERTED, QuoteStatus.DECLINED):
        q = _quote(db, _customer(db), status=status)
        r = client.get(f"/quotes/{q.id}")
        assert r.status_code == 200, status
        assert f"/quotes/{q.id}/duplicate" in r.text, status
        assert "Duplicate Quote" in r.text, status


def test_mark_lost_popover_is_structured(client, db):
    """The popover posts the route's REAL param names: a lost_reason <select>
    built from the LostReason enum plus competitor_name / competitor_price /
    note inputs (competitor fields revealed for the competitor reason)."""
    q = _quote(db, _customer(db), status=QuoteStatus.SENT)
    r = client.get(f"/quotes/{q.id}")
    assert r.status_code == 200

    assert 'name="lost_reason"' in r.text
    for reason in LostReason:
        assert f'value="{reason.value}"' in r.text, reason
    # Competitor intel inputs + note, bound to the route's real names
    assert 'name="competitor_name"' in r.text
    assert 'name="competitor_price"' in r.text
    assert 'name="note"' in r.text
    # The conditional reveal keys off the competitor reason value
    assert "reason === 'competitor'" in r.text
