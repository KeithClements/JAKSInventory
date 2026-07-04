"""
tests/test_pricing_audit.py
===========================
Locked-price margin alert: a hand-locked price whose margin fell below the floor
(because vendor cost rose underneath it) is flagged — so it never silently sinks
below cost. See app/services/pricing_audit.py.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

import app.database as _appdb
from app.models.product import Product
from app.services.pricing_audit import audit_locked_margins, scan_locked_below_margin
from app.settings_utils import get_setting_value_db
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _mk(db, *, price, cost, locked=True, active=True):
    n = next(_seq)
    p = Product(sku=f"JAKS-PAI-MG{n:04d}", title="Part",
                price_override=price, price_override_locked=locked,
                cost=cost, cost_source="receipt", is_active=active)
    db.add(p)
    db.commit()
    return p


def test_flags_locked_price_below_floor(db):
    # $100 sell on $90 cost = 10% margin < 15% floor → flagged.
    p = _mk(db, price=100.0, cost=90.0)
    rows = scan_locked_below_margin(db, 15.0)
    assert len(rows) == 1 and rows[0]["id"] == p.id
    assert rows[0]["margin_pct"] == 10.0 and rows[0]["below_cost"] is False


def test_healthy_margin_not_flagged(db):
    _mk(db, price=100.0, cost=50.0)   # 50% margin
    assert scan_locked_below_margin(db, 15.0) == []


def test_below_cost_is_flagged_and_marked(db):
    p = _mk(db, price=80.0, cost=100.0)   # negative margin
    rows = scan_locked_below_margin(db, 15.0)
    assert len(rows) == 1 and rows[0]["below_cost"] is True


def test_unlocked_prices_are_ignored(db):
    _mk(db, price=100.0, cost=95.0, locked=False)   # scraper manages it → not our concern
    assert scan_locked_below_margin(db, 15.0) == []


def test_no_cost_yet_is_skipped(db):
    # effective_cost 0 (never received/quoted) → margin unknowable, not "below floor".
    _mk(db, price=100.0, cost=0.0)
    assert scan_locked_below_margin(db, 15.0) == []


def test_apply_flags_needs_review_and_records_count(db):
    p = _mk(db, price=100.0, cost=92.0)
    res = audit_locked_margins(db, floor_pct=15.0, apply=True)
    assert res["count"] == 1 and res["applied"] is True
    db.refresh(p)
    assert p.needs_review is True
    assert get_setting_value_db(db, "shopify_locked_margin_alert_count", "") == "1"


def test_report_mode_does_not_flag(db):
    p = _mk(db, price=100.0, cost=92.0)
    res = audit_locked_margins(db, floor_pct=15.0, apply=False)
    assert res["count"] == 1 and res["applied"] is False
    db.refresh(p)
    assert p.needs_review is False
