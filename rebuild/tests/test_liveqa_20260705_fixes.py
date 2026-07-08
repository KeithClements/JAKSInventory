"""
tests/test_liveqa_20260705_fixes.py
===================================
Live-QA (2026-07-05, Playwright pass) findings, locked with real assertions:

  1. HIGH — the quote header Disc% saved to an inert column no total property read.
     It flashed "Saved" and did nothing. QuoteService.update_header now cascades the
     blanket discount onto every discountable, non-overridden line so the Quote Total
     actually moves and the discount carries forward on conversion.

  2. MED — line qty had a floor (>=1) but NO ceiling, so 999999999 produced a
     multi-trillion-dollar line. A shared validate_line_qty now caps every document's
     qty at MAX_LINE_QTY.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_liveqa_20260705_fixes.py -q
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
from app.utils import MAX_LINE_QTY, validate_line_qty
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


def _quote(db, *, n_lines=2, price=100.0, customer_discount=0.0):
    c = Customer(
        company_name=f"Co {uuid.uuid4().hex[:8]}", contact_name="",
        discount_pct=customer_discount,
    )
    db.add(c)
    db.flush()
    q = Quote(
        quote_number=f"Q-{uuid.uuid4().hex[:10]}", customer_id=c.id, status="draft",
        is_taxable=False, tax_rate_snapshot=0.0,   # keep totals = subtotal (no tax noise)
    )
    db.add(q)
    db.flush()
    for i in range(n_lines):
        db.add(QuoteLine(
            quote_id=q.id, description=f"part {i}", qty=1,
            unit_price=price, unit_cost=0.0, discount_pct=0.0, line_type="product",
        ))
    db.commit()
    return q


# ─────────────────────────────────────────────────────────────────────────────
# Fix #2 — qty ceiling
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_line_qty_contract():
    assert validate_line_qty(1) == 1
    assert validate_line_qty(MAX_LINE_QTY) == MAX_LINE_QTY
    for bad in [0, -3, MAX_LINE_QTY + 1, 999_999_999, "x", None]:
        with pytest.raises(ValueError):
            validate_line_qty(bad)


def test_update_line_rejects_absurd_qty(db):
    q = _quote(db, n_lines=1)
    line = q.lines[0]
    svc = QuoteService(db, current_user_id=1)
    with pytest.raises(ValueError):
        svc.update_line(line.id, {"qty": 999_999_999})
    db.refresh(line)
    assert line.qty == 1  # untouched


def test_add_line_rejects_absurd_qty(db):
    q = _quote(db, n_lines=0)
    svc = QuoteService(db, current_user_id=1)
    with pytest.raises(ValueError):
        svc.add_line(q.id, None, {"description": "misc", "qty": 999_999_999,
                                  "line_type": "misc_charge", "unit_price": 5.0})


def test_update_line_accepts_ceiling_boundary(db):
    q = _quote(db, n_lines=1)
    line = q.lines[0]
    svc = QuoteService(db, current_user_id=1)
    svc.update_line(line.id, {"qty": MAX_LINE_QTY})
    db.refresh(line)
    assert line.qty == MAX_LINE_QTY


# ─────────────────────────────────────────────────────────────────────────────
# Fix #1 — header Disc% is a real blanket discount
# ─────────────────────────────────────────────────────────────────────────────

def test_header_discount_moves_the_total(db):
    q = _quote(db, n_lines=2, price=100.0)          # subtotal 200, total 200
    assert q.subtotal == 200.0 and q.total == 200.0
    svc = QuoteService(db, current_user_id=1)
    svc.update_header(q.id, {"discount_pct": 25})
    db.refresh(q)
    assert q.discount_pct == 25
    assert all(ln.discount_pct == 25 for ln in q.lines)   # cascaded to lines
    assert q.subtotal == 150.0                            # 200 − 25%
    assert q.total == 150.0                               # the reported bug: was 200


def test_header_discount_respects_manual_line_override(db):
    q = _quote(db, n_lines=2, price=100.0)
    # Clerk manually overrides line 0 to 10%.
    q.lines[0].discount_pct = 10.0
    q.lines[0].discount_overridden = True
    db.commit()
    svc = QuoteService(db, current_user_id=1)
    svc.update_header(q.id, {"discount_pct": 40})
    db.refresh(q)
    overridden = [ln for ln in q.lines if ln.discount_overridden][0]
    blanket = [ln for ln in q.lines if not ln.discount_overridden][0]
    assert overridden.discount_pct == 10.0   # manual override wins
    assert blanket.discount_pct == 40.0       # blanket applied
    # total = 100*(1-.10) + 100*(1-.40) = 90 + 60 = 150
    assert q.total == 150.0


def test_header_discount_skips_non_discountable_lines(db):
    q = _quote(db, n_lines=1, price=100.0)
    core = QuoteLine(
        quote_id=q.id, description="Core", qty=1, unit_price=50.0, unit_cost=0.0,
        discount_pct=0.0, line_type="core_charge", is_core_line=True,
    )
    db.add(core)
    db.commit()
    svc = QuoteService(db, current_user_id=1)
    svc.update_header(q.id, {"discount_pct": 30})
    db.refresh(core)
    assert core.discount_pct == 0.0   # cores are never discounted


def test_header_discount_cascade_flag_only_on_change(db):
    q = _quote(db, n_lines=1)
    svc = QuoteService(db, current_user_id=1)
    changed = svc.update_header(q.id, {"discount_pct": 15})
    assert getattr(changed, "_discount_cascaded", False) is True
    # Saving the SAME value again must not re-cascade (no spurious UI refresh).
    again = svc.update_header(q.id, {"discount_pct": 15})
    assert getattr(again, "_discount_cascaded", False) is False


def test_new_line_inherits_active_blanket_discount(db):
    q = _quote(db, n_lines=1, price=100.0)
    svc = QuoteService(db, current_user_id=1)
    svc.update_header(q.id, {"discount_pct": 20})
    lines = svc.add_line(q.id, None, {"description": "added later", "qty": 1,
                                      "line_type": "misc_charge", "unit_price": 100.0})
    assert lines[0].discount_pct == 20.0   # joined the blanket instead of full price
