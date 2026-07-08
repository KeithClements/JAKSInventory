"""
tests/test_count_sessions.py
============================
§24 — Inventory Count Sessions. Covers the §24.8 must-cover matrix:

  1. mid-count sale preserved (the net-change proof)   — test_mid_count_sale_preserved
  2. blind hides frozen_qty during entry               — test_blind_hides_book_in_entry
  3. recount threshold (qty + $)                        — test_recount_threshold_*
  4. post is atomic / zero-variance writes no row       — test_zero_variance_no_ledger_row
  5. OPENING from an empty baseline                     — test_opening_from_zero_baseline
  6. FULL won't zero uncounted without confirm          — test_full_zero_uncounted_*
  7. permission split (enter vs post)                   — test_sales_can_enter_not_post
  8. re-post idempotency rejected                       — test_repost_rejected
  9. nightly resync zero-drift after post               — test_resync_zero_drift_after_post

Run: .venv\\Scripts\\python.exe -m pytest tests/test_count_sessions.py -q
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.constants import (
    AdjustmentReason,
    CountLineStatus,
    CountStatus,
    CountType,
    EntityType,
    InventoryTxnType,
    UserRole,
)
from app.main import app
from tests.conftest import activate, fresh_engine

_UID = 1  # seeded ADMIN under the client; unknown/system in pure-service context


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def engine():
    return fresh_engine()


@pytest.fixture()
def client(engine):
    activate(engine)  # point app DB at this engine BEFORE startup seeds admin id=1
    with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _product(db, sku, *, qty=0, cost=10.0, bin_location="", is_active=True, category_id=None):
    from app.models.product import Product

    p = Product(
        sku=sku, title=f"Part {sku}", cost=cost, qty_on_hand=qty,
        bin_location=bin_location, is_active=is_active, category_id=category_id,
    )
    db.add(p)
    db.flush()
    return p


def _seed_stock_via_ledger(db, product, qty):
    """Give a product a LEDGER-backed opening balance (as a real receipt would),
    so ledger==cache assertions are meaningful."""
    from app.services.inventory_service import InventoryService

    InventoryService(db, _UID).apply_stock_delta(
        product, qty, InventoryTxnType.PO_RECEIPT, EntityType.PURCHASE_ORDER, 1, notes="seed"
    )
    db.flush()


def _sell_two(db, product):
    """Simulate a mid-count sale of 2 pieces through the inventory writer."""
    from app.services.inventory_service import InventoryService

    InventoryService(db, _UID).apply_stock_delta(
        product, -2, InventoryTxnType.INVOICE_SALE, EntityType.INVOICE, 999, notes="mid-count sale"
    )
    db.commit()


def _svc(db):
    from app.services.count_service import CountService

    return CountService(db, _UID)


def _seed_user(db, role, username):
    from app.auth import hash_password
    from app.models.user import User

    u = User(name=username, username=username,
             password_hash=hash_password(f"{username}-pw-x"), role=role, is_active=True)
    db.add(u)
    db.commit()
    return u.id


def _as(client, user_id):
    from app.auth import SESSION_COOKIE, make_session_token

    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, make_session_token(user_id))


# ── 1. The net-change proof ───────────────────────────────────────────────────

def test_mid_count_sale_preserved(db):
    """Counted 10, then 2 sold mid-count → final on-hand 8, NOT reset to 10."""
    p = _product(db, "NET-1")
    _seed_stock_via_ledger(db, p, 10)
    db.commit()

    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["NET-1"]})
    svc.open_session(s.id)
    line = svc.find_line(s.id, product_id=p.id)
    assert line.frozen_qty == 10

    svc.record_count(s.id, 10, product_id=p.id)   # count matches book
    _sell_two(db, p)                                # a real sale lands mid-count
    db.refresh(p)
    assert p.qty_on_hand == 8

    summary = svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 8            # the sale is preserved, not erased
    assert summary["posted"] == 0        # zero variance → no adjustment written

    # ledger == cache (no drift introduced)
    from app.services.product_service import ProductService
    assert ProductService(db, _UID).get_qty_on_hand(p.id) == 8


def test_shrinkage_posts_negative_delta(db):
    p = _product(db, "SHR-1", qty=10)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["SHR-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 7, product_id=p.id)
    summary = svc.post_session(s.id)

    db.refresh(p)
    assert p.qty_on_hand == 7
    assert summary["posted"] == 1

    from app.models.inventory import InventoryTransaction
    txn = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.product_id == p.id,
                InventoryTransaction.reference_type == EntityType.COUNT_SESSION)
        .first()
    )
    assert txn is not None
    assert txn.transaction_type == InventoryTxnType.INITIAL_COUNT
    assert txn.qty_change == -3
    assert int(txn.reference_id) == s.id
    assert txn.reason == AdjustmentReason.CYCLE_COUNT_SHORT


def test_sale_before_count_not_double_subtracted(db):
    """Sale happens BEFORE the shelf is counted (the realistic blind case):
    frozen 10 → sell 3 (cache 7) → counter counts the shelf = 7 → post must be
    a no-op (final 7), NOT 7−3=4. Reconciles against book-at-count, not snapshot."""
    p = _product(db, "SBC-1")
    _seed_stock_via_ledger(db, p, 10)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["SBC-1"]})
    svc.open_session(s.id)

    _sell_two(db, p)  # actually sells 2 → cache 8 (helper name is historical)
    db.refresh(p)
    assert p.qty_on_hand == 8

    line = svc.record_count(s.id, 8, product_id=p.id)  # counter sees the post-sale shelf
    assert line.variance == 0            # book-at-count (8) == counted (8): no discrepancy
    summary = svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 8            # preserved — NOT double-subtracted to 6
    assert summary["posted"] == 0


def test_accept_recount_survives_review_roundtrip(db):
    """Accept a flagged variance → reopen → back to review: the accept must hold
    (to_review must not silently re-flag it)."""
    p = _product(db, "ACC-1", qty=100)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["ACC-1"]},
                           recount_threshold_qty=5)
    svc.open_session(s.id)
    line = svc.record_count(s.id, 80, product_id=p.id)  # -20, flagged
    assert line.needs_recount is True
    svc.to_review(s.id)
    svc.accept_recount(s.id, line.id)
    svc.reopen(s.id)
    svc.to_review(s.id)                 # must NOT undo the accept
    line = svc.find_line(s.id, product_id=p.id)
    assert line.needs_recount is False
    svc.post_session(s.id)             # not blocked
    db.refresh(p)
    assert p.qty_on_hand == 80


def test_accept_recount_rejected_on_posted(db):
    p = _product(db, "ACCP-1", qty=5)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["ACCP-1"]})
    svc.open_session(s.id)
    line = svc.record_count(s.id, 6, product_id=p.id)
    svc.post_session(s.id)
    with pytest.raises(ValueError):
        svc.accept_recount(s.id, line.id)


def test_full_zero_already_zero_not_double_tallied(db):
    """A FULL zero-fill of a line already at 0 counts as zeroed, not also unchanged."""
    a = _product(db, "FZZ-A", qty=5)
    b = _product(db, "FZZ-B", qty=0)  # already 0, uncounted
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.FULL, scope={"mode": "filtered", "sku_list": ["FZZ-A", "FZZ-B"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 5, product_id=a.id)  # a matches; b uncounted
    summary = svc.post_session(s.id, zero_uncounted=True)
    # b: zeroed (already 0) → counted once as zeroed, NOT also unchanged
    total = summary["posted"] + summary["unchanged"] + summary["zeroed"] + summary["skipped"]
    assert summary["zeroed"] == 1
    assert total == s.line_count  # no double-tally


def test_variance_plus_concurrent_movement(db):
    p = _product(db, "COMBO-1", qty=10)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["COMBO-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 7, product_id=p.id)  # counted 7
    _sell_two(db, p)                             # then 2 more sold
    svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 5  # 7 counted − 2 sold-since


# ── 5. OPENING ────────────────────────────────────────────────────────────────

def test_opening_from_zero_baseline(db):
    p = _product(db, "OPEN-1", qty=0, cost=0.0)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.OPENING, scope={"mode": "all"})
    svc.open_session(s.id)
    svc.record_count(s.id, 25, product_id=p.id)
    svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 25

    from app.models.inventory import InventoryTransaction
    txn = db.query(InventoryTransaction).filter(InventoryTransaction.product_id == p.id).first()
    assert txn.reason == AdjustmentReason.INITIAL_INVENTORY_LOAD


# ── 4. Zero variance writes no ledger row ─────────────────────────────────────

def test_zero_variance_no_ledger_row(db):
    p = _product(db, "ZERO-1", qty=5)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["ZERO-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 5, product_id=p.id)
    summary = svc.post_session(s.id)
    assert summary["posted"] == 0
    assert summary["unchanged"] == 1

    from app.models.inventory import InventoryTransaction
    n = db.query(InventoryTransaction).filter(
        InventoryTransaction.reference_type == EntityType.COUNT_SESSION
    ).count()
    assert n == 0
    line = svc.find_line(s.id, product_id=p.id)
    assert line.line_status == CountLineStatus.POSTED
    assert line.posted_txn_id is None


# ── 3. Recount thresholds ─────────────────────────────────────────────────────

def test_recount_threshold_qty_flags_and_blocks_post(db):
    p = _product(db, "THQ-1", qty=100)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.AUDIT, scope={"mode": "filtered", "sku_list": ["THQ-1"]},
                           recount_threshold_qty=5)
    svc.open_session(s.id)
    line = svc.record_count(s.id, 90, product_id=p.id)  # variance -10 > 5
    assert line.needs_recount is True

    with pytest.raises(ValueError, match="recount"):
        svc.post_session(s.id)

    svc.accept_recount(s.id, line.id)
    svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 90


def test_recount_threshold_value_flags(db):
    p = _product(db, "THV-1", qty=100, cost=50.0)
    db.commit()
    svc = _svc(db)
    # off by 3 units × $50 = $150 value variance > $100 threshold, qty threshold off
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["THV-1"]},
                           recount_threshold_value=100.0)
    svc.open_session(s.id)
    line = svc.record_count(s.id, 97, product_id=p.id)
    assert line.needs_recount is True


def test_recount_threshold_within_limit_not_flagged(db):
    p = _product(db, "THOK-1", qty=100)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["THOK-1"]},
                           recount_threshold_qty=5)
    svc.open_session(s.id)
    line = svc.record_count(s.id, 98, product_id=p.id)  # off by 2, under 5
    assert line.needs_recount is False


# ── 6. FULL zero-uncounted ────────────────────────────────────────────────────

def test_full_skips_uncounted_by_default(db):
    a = _product(db, "F-A", qty=5)
    b = _product(db, "F-B", qty=8)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.FULL, scope={"mode": "filtered", "sku_list": ["F-A", "F-B"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 5, product_id=a.id)  # b uncounted
    summary = svc.post_session(s.id)
    db.refresh(b)
    assert b.qty_on_hand == 8
    assert summary["skipped"] == 1


def test_full_zero_uncounted_when_confirmed(db):
    a = _product(db, "FZ-A", qty=5)
    b = _product(db, "FZ-B", qty=8)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.FULL, scope={"mode": "filtered", "sku_list": ["FZ-A", "FZ-B"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 5, product_id=a.id)
    summary = svc.post_session(s.id, zero_uncounted=True)
    db.refresh(b)
    assert b.qty_on_hand == 0
    assert summary["zeroed"] == 1


def test_cycle_zero_uncounted_ignored(db):
    """zero_uncounted only applies to FULL — a CYCLE count never zeroes."""
    a = _product(db, "CZ-A", qty=5)
    b = _product(db, "CZ-B", qty=8)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["CZ-A", "CZ-B"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 5, product_id=a.id)
    summary = svc.post_session(s.id, zero_uncounted=True)  # ignored for cycle
    db.refresh(b)
    assert b.qty_on_hand == 8
    assert summary["skipped"] == 1
    assert summary["zeroed"] == 0


# ── 8. Idempotency ────────────────────────────────────────────────────────────

def test_repost_rejected(db):
    p = _product(db, "IDEM-1", qty=3)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["IDEM-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 4, product_id=p.id)
    svc.post_session(s.id)
    with pytest.raises(ValueError):
        svc.post_session(s.id)


def test_cancel_makes_no_ledger_change(db):
    p = _product(db, "CAN-1", qty=5)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["CAN-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 99, product_id=p.id)
    svc.cancel_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 5
    s2 = svc.get_session(s.id)
    assert s2.status == CountStatus.CANCELLED


# ── Scope resolution ──────────────────────────────────────────────────────────

def test_open_empty_scope_raises(db):
    _product(db, "X-1", qty=1)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["DOES-NOT-EXIST"]})
    with pytest.raises(ValueError, match="matched no products"):
        svc.open_session(s.id)


def test_scope_by_bin_prefix(db):
    _product(db, "BIN-A1", qty=1, bin_location="A-01")
    _product(db, "BIN-A2", qty=1, bin_location="A-02")
    _product(db, "BIN-B1", qty=1, bin_location="B-01")
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "bin_prefix": "A-"})
    svc.open_session(s.id)
    s2 = svc.get_session(s.id)
    skus = {l.sku for l in s2.lines}
    assert skus == {"BIN-A1", "BIN-A2"}


def test_found_item_adds_line(db):
    _product(db, "SC-1", qty=1)
    found = _product(db, "SC-2", qty=4)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["SC-1"]})
    svc.open_session(s.id)
    line = svc.add_found_line(s.id, "SC-2", counted_qty=4)
    assert line.is_found is True
    assert line.frozen_qty == 4


# ── 9. Nightly resync agrees after post ───────────────────────────────────────

def test_resync_zero_drift_after_post(db):
    p = _product(db, "RES-1")
    _seed_stock_via_ledger(db, p, 20)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["RES-1"]})
    svc.open_session(s.id)
    svc.record_count(s.id, 17, product_id=p.id)  # -3 variance
    svc.post_session(s.id)
    db.refresh(p)
    assert p.qty_on_hand == 17

    from app.services.inventory_service import InventoryService
    result = InventoryService(db, _UID).resync_all_products()
    assert result["drifted"] == 0
    db.refresh(p)
    assert p.qty_on_hand == 17


# ── 2. Blind ──────────────────────────────────────────────────────────────────

def test_blind_hides_book_in_entry(client, db):
    p = _product(db, "BL-1", qty=10)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["BL-1"]},
                           blind=True)
    svc.open_session(s.id)
    svc.record_count(s.id, 6, product_id=p.id)  # variance -4

    r = client.get(f"/counts/{s.id}")
    assert r.status_code == 200
    assert "vs book" not in r.text            # blind: no book value / variance in entry
    # After review, the book value is revealed in the variance table.
    svc.to_review(s.id)
    r2 = client.get(f"/counts/{s.id}")
    assert r2.status_code == 200
    assert "Variances" in r2.text


def test_non_blind_shows_book_in_entry(client, db):
    p = _product(db, "NB-1", qty=10)
    db.commit()
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["NB-1"]},
                           blind=False)
    svc.open_session(s.id)
    svc.record_count(s.id, 6, product_id=p.id)
    r = client.get(f"/counts/{s.id}")
    assert "vs book" in r.text


# ── Route rendering + full flow ───────────────────────────────────────────────

def test_list_and_new_render(client, db):
    _product(db, "R-1", qty=1)
    db.commit()
    assert client.get("/counts/").status_code == 200
    assert client.get("/counts/new").status_code == 200
    assert client.get("/counts/new?type=full").status_code == 200


def test_full_flow_via_routes(client, db):
    _product(db, "FL-1", qty=10, bin_location="A-1")
    db.commit()

    # create
    r = client.post("/counts/", data={"count_type": "cycle", "scope_mode": "filtered",
                                      "sku_list": "FL-1", "blind": "on"})
    assert r.status_code == 303 and "/counts/" in r.headers["location"]
    from app.models.count_session import CountSession
    db.expire_all()
    s = db.query(CountSession).first()
    assert s is not None and s.status == CountStatus.DRAFT

    # draft workspace renders
    assert client.get(f"/counts/{s.id}").status_code == 200

    # open
    r = client.post(f"/counts/{s.id}/open")
    assert r.status_code == 303
    db.expire_all()
    s = db.get(CountSession, s.id)
    assert s.status == CountStatus.OPEN and s.line_count == 1

    # count via HTMX → returns the line card + oob progress
    line_id = s.lines[0].id
    pid = s.lines[0].product_id
    r = client.post(f"/counts/{s.id}/count",
                    data={"product_id": pid, "counted_qty": 7},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert f"line-{line_id}" in r.text
    assert "count-progress" in r.text

    # review
    assert client.post(f"/counts/{s.id}/review").status_code == 303
    assert client.get(f"/counts/{s.id}").status_code == 200

    # sheet renders
    assert client.get(f"/counts/{s.id}/sheet").status_code == 200

    # post
    r = client.post(f"/counts/{s.id}/post")
    assert r.status_code == 303
    db.expire_all()
    from app.models.product import Product
    prod = db.query(Product).filter(Product.sku == "FL-1").first()
    assert prod.qty_on_hand == 7


# ── 7. Permission split ───────────────────────────────────────────────────────

def test_sales_can_enter_not_post(client, db):
    p = _product(db, "PERM-1", qty=10)
    sales_id = _seed_user(db, UserRole.SALES, "clerk")
    db.commit()

    # Admin (default cookie-less → id 1) creates + opens.
    svc = _svc(db)
    s = svc.create_session(CountType.CYCLE, scope={"mode": "filtered", "sku_list": ["PERM-1"]})
    svc.open_session(s.id)

    # SALES can record a count (INVENTORY_COUNT).
    _as(client, sales_id)
    r = client.post(f"/counts/{s.id}/count", data={"product_id": p.id, "counted_qty": 6})
    assert r.status_code == 303 and "error=" not in r.headers["location"]

    # SALES CANNOT post (needs INVENTORY_COUNT_APPROVE).
    r = client.post(f"/counts/{s.id}/post")
    assert r.status_code == 303 and "error=" in r.headers["location"]
    db.expire_all()
    from app.models.count_session import CountSession
    s2 = db.get(CountSession, s.id)
    assert s2.status != CountStatus.POSTED  # not posted

    # Admin can post.
    _as(client, 1)
    r = client.post(f"/counts/{s.id}/post")
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    db.expire_all()
    s3 = db.get(CountSession, s.id)
    assert s3.status == CountStatus.POSTED
