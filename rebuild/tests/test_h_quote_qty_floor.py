"""
tests/test_h_quote_qty_floor.py
===============================
FULL_ERP_REVIEW_2026-07-04 HIGH — the qty cell accepted -3 / 0 on a quote line,
producing a negative/zero LINE TOTAL that cascaded to the quote total and (because
a negative quote converts straight into an SO/invoice) into the money path.
QuoteService now floors line qty at 1.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_h_quote_qty_floor.py -q
"""
from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.models.customer import Customer
from app.models.quote import Quote, QuoteLine
from app.services.quote_service import QuoteService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    from app.database import SessionLocal
    from app.routers.settings import seed_settings
    s = SessionLocal()
    seed_settings(s)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _quote_with_line(db):
    c = Customer(company_name=f"Co {uuid.uuid4().hex[:8]}", contact_name="")
    db.add(c)
    db.flush()
    q = Quote(quote_number=f"Q-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="draft")
    db.add(q)
    db.flush()
    line = QuoteLine(
        quote_id=q.id, description="part", qty=1,
        unit_price=140.0, unit_cost=0.0, discount_pct=0.0, line_type="product",
    )
    db.add(line)
    db.commit()
    return q, line


@pytest.mark.parametrize("bad_qty", [-3, 0, "-3", "0"])
def test_update_line_rejects_non_positive_qty(db, bad_qty):
    q, line = _quote_with_line(db)
    svc = QuoteService(db, current_user_id=1)
    with pytest.raises(ValueError):
        svc.update_line(line.id, {"qty": bad_qty})
    # The stored line must be untouched.
    db.refresh(line)
    assert line.qty == 1


def test_update_line_accepts_positive_qty(db):
    q, line = _quote_with_line(db)
    svc = QuoteService(db, current_user_id=1)
    svc.update_line(line.id, {"qty": 4})
    db.refresh(line)
    assert line.qty == 4
