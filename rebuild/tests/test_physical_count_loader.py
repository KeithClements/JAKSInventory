"""
tests/test_physical_count_loader.py
===================================
FULL_ERP_REVIEW_2026-07-04 HIGH / go-live gating task — a bulk physical-count
loader so the trial can start with real on-hand (the live DB had 4 SKUs / 6 units).
InventoryService.apply_physical_count sets on-hand to counted qty via ledger-backed
adjustments; scripts/load_physical_count.py is the CSV front-end.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_physical_count_loader.py -q
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.models.product import Product
from app.models.inventory import InventoryTransaction
from app.services.inventory_service import InventoryService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _product(db, sku, qty):
    p = Product(sku=sku, title=f"Part {sku}", cost=5.0, qty_on_hand=qty)
    db.add(p)
    db.flush()
    return p


def test_dry_run_computes_plan_without_writing(db):
    _product(db, "A1", 3)
    _product(db, "B2", 10)
    db.commit()
    svc = InventoryService(db, current_user_id=None)

    summary = svc.apply_physical_count(
        [("A1", 12), ("B2", 10), ("GHOST", 5)], dry_run=True)

    assert summary["applied"] == 1          # A1 changes (3→12)
    assert summary["unchanged"] == 1        # B2 already 10
    assert summary["not_found"] == ["GHOST"]
    # Nothing written.
    db.refresh(db.query(Product).filter_by(sku="A1").first())
    assert db.query(Product).filter_by(sku="A1").first().qty_on_hand == 3
    assert db.query(InventoryTransaction).count() == 0


def test_apply_sets_on_hand_and_writes_ledger(db):
    _product(db, "A1", 3)
    _product(db, "B2", 10)
    db.commit()
    svc = InventoryService(db, current_user_id=None)

    summary = svc.apply_physical_count([("A1", 12), ("B2", 4)], dry_run=False)
    assert summary["applied"] == 2

    a1 = db.query(Product).filter_by(sku="A1").first()
    b2 = db.query(Product).filter_by(sku="B2").first()
    assert a1.qty_on_hand == 12
    assert b2.qty_on_hand == 4

    # A ledger row per change, carrying the signed delta.
    deltas = sorted(t.qty_change for t in db.query(InventoryTransaction).all())
    assert deltas == [-6, 9]   # B2 10→4 = -6, A1 3→12 = +9


def test_negative_count_is_rejected(db):
    _product(db, "A1", 3)
    db.commit()
    svc = InventoryService(db, current_user_id=None)
    with pytest.raises(ValueError):
        svc.apply_physical_count([("A1", -1)], dry_run=False)


def test_csv_reader_accepts_flexible_headers(tmp_path):
    from scripts.load_physical_count import read_counts
    p = tmp_path / "counts.csv"
    p.write_text("Part Number,On Hand\nA1,12\nB2,4\n", encoding="utf-8")
    # "part_number" and "on_hand" are recognized aliases.
    assert read_counts(str(p)) == [("A1", "12"), ("B2", "4")]
