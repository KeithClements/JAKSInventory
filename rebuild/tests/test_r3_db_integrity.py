"""
tests/test_r3_db_integrity.py
=============================
R3 — DB integrity backstops + inventory cache resync.

  1. Unique-index backstops (model __table_args__ + the defensive live-DB
     creation batch in app/database.py):
       * product_vendor_sources — UNIQUE (product_id, vendor_id) WHERE is_active=1
         (soft-deleted history rows may repeat the pair);
       * cross_references — UNIQUE (product_id, ref_type, ref_number);
       * customers — UNIQUE (account_number) for non-blank values only;
       * vendor_bills — UNIQUE (vendor_id, bill_number) for non-blank values only.
     Plus the duplicate-probe path: a legacy DB that ALREADY contains duplicates
     must SKIP index creation with a WARNING (never crash startup, never delete).

  2. Statement migration lines — over_90 + snapshot_json land on a legacy
     customer_statements table via _apply_inline_migrations.

  3. Inventory resync — Product.qty_on_hand is a cache over the
     InventoryTransaction ledger; the admin routes recompute it FROM the ledger.
     Commitment rows (SO_COMMITTED / SO_RELEASED) move qty_committed only —
     committed stock is still physically on hand — so resync must IGNORE them.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_r3_db_integrity.py -q
"""
from __future__ import annotations

import itertools
import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

import app.database as _appdb
from app.constants import CrossRefType, InventoryTxnType, UserRole
from app.database import (
    _apply_inline_migrations,
    _apply_unique_index_migrations,
)
from app.main import app
from app.models.customer import Customer
from app.models.inventory import InventoryTransaction
from app.models.product import CrossReference, Product, ProductVendorSource
from app.models.purchase_order import VendorBill
from app.models.user import User
from app.models.vendor import Vendor
from app.services.product_service import ProductService
from tests.conftest import activate, bare_engine, fresh_engine

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


@pytest.fixture()
def admin_user(db):
    """User #1 as ADMIN — the id the test-env auth bypass resolves to."""
    user = db.query(User).filter(User.id == 1).first()
    if user is None:
        user = User(
            id=1, name="Test Admin", username="testadmin",
            password_hash="x", role=UserRole.ADMIN,
        )
        db.add(user)
        db.commit()
    elif user.role != UserRole.ADMIN:
        user.role = UserRole.ADMIN
        db.commit()
    return user


def _mk_product(db, **kw) -> Product:
    n = next(_seq)
    p = Product(sku=f"R3-SKU-{n}", title=f"R3 Part {n}", **kw)
    db.add(p)
    db.commit()
    return p


def _mk_vendor(db) -> Vendor:
    n = next(_seq)
    v = Vendor(name=f"R3 Vendor {n}")
    db.add(v)
    db.commit()
    return v


def _mk_customer(db, account_number: str = "") -> Customer:
    n = next(_seq)
    c = Customer(company_name=f"R3 Customer {n}", account_number=account_number)
    db.add(c)
    db.commit()
    return c


def _txn(db, product_id: int, txn_type: str, qty_change: int, qty_after: int = 0):
    db.add(InventoryTransaction(
        product_id=product_id,
        transaction_type=txn_type,
        qty_change=qty_change,
        qty_after=qty_after,
    ))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unique-index backstops — fresh engine (create_all picks the model indexes)
# ─────────────────────────────────────────────────────────────────────────────

def test_unique_active_vendor_source_rejected(db):
    """Two ACTIVE sources for the same (product, vendor) violate the partial
    unique index; a soft-deleted (is_active=0) duplicate is legitimate."""
    product, vendor = _mk_product(db), _mk_vendor(db)
    db.add(ProductVendorSource(product_id=product.id, vendor_id=vendor.id))
    db.commit()

    db.add(ProductVendorSource(product_id=product.id, vendor_id=vendor.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Partial scope: an INACTIVE duplicate of the pair is allowed (history row).
    db.add(ProductVendorSource(
        product_id=product.id, vendor_id=vendor.id, is_active=False,
    ))
    db.commit()  # must not raise


def test_unique_cross_reference_rejected(db):
    product = _mk_product(db)
    db.add(CrossReference(
        product_id=product.id, ref_type=CrossRefType.OEM, ref_number="3406E-123",
    ))
    db.commit()

    db.add(CrossReference(
        product_id=product.id, ref_type=CrossRefType.OEM, ref_number="3406E-123",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_unique_customer_account_number_rejected_blank_repeats(db):
    _mk_customer(db, account_number="ACCT-R3-1")
    with pytest.raises(IntegrityError):
        _mk_customer(db, account_number="ACCT-R3-1")
    db.rollback()

    # Blank account numbers are outside the partial index — repeat freely.
    _mk_customer(db, account_number="")
    _mk_customer(db, account_number="")


def test_unique_vendor_bill_number_rejected_blank_repeats(db):
    vendor = _mk_vendor(db)
    db.add(VendorBill(vendor_id=vendor.id, bill_number="BILL-R3-1"))
    db.commit()

    db.add(VendorBill(vendor_id=vendor.id, bill_number="BILL-R3-1"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Blank / NULL bill numbers are outside the partial index — repeat freely.
    db.add(VendorBill(vendor_id=vendor.id, bill_number=""))
    db.add(VendorBill(vendor_id=vendor.id, bill_number=""))
    db.add(VendorBill(vendor_id=vendor.id, bill_number=None))
    db.add(VendorBill(vendor_id=vendor.id, bill_number=None))
    db.commit()

    # A different vendor may reuse the same bill number.
    other = _mk_vendor(db)
    db.add(VendorBill(vendor_id=other.id, bill_number="BILL-R3-1"))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Defensive live-DB creation path (duplicate probe — startup never wedges)
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_probe_skips_index_and_startup_survives(caplog):
    """A legacy DB that already contains duplicate cross_references must NOT
    get the unique index (no data is deleted), must log a WARNING naming the
    table + duplicate count, and the migration batch must complete normally."""
    engine = bare_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cross_references ("
            "id INTEGER PRIMARY KEY, product_id INTEGER, "
            "ref_type TEXT, ref_number TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO cross_references (product_id, ref_type, ref_number) "
            "VALUES (1, 'oem', 'DUP-1'), (1, 'oem', 'DUP-1'), (2, 'oem', 'DUP-2'), "
            "(2, 'oem', 'DUP-2')"
        ))

    with caplog.at_level(logging.WARNING, logger="app.database"):
        _apply_unique_index_migrations(bind=engine)  # must not raise

    idx_names = {i["name"] for i in inspect(engine).get_indexes("cross_references")}
    assert "uq_cross_references_product_type_number" not in idx_names

    warning = next(
        (r for r in caplog.records
         if "uq_cross_references_product_type_number" in r.getMessage()),
        None,
    )
    assert warning is not None, "expected a WARNING naming the skipped index"
    assert warning.levelno == logging.WARNING
    assert "cross_references" in warning.getMessage()
    assert "2" in warning.getMessage()  # 2 duplicate key-groups

    # No rows were deleted — owner dedups, we never touch data.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM cross_references")).scalar()
    assert count == 4


def test_clean_legacy_db_gets_index_created():
    """Same legacy-DB path with NO duplicates: the backstop index is created
    and enforces uniqueness from then on."""
    engine = bare_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE cross_references ("
            "id INTEGER PRIMARY KEY, product_id INTEGER, "
            "ref_type TEXT, ref_number TEXT)"
        ))
        conn.execute(text(
            "INSERT INTO cross_references (product_id, ref_type, ref_number) "
            "VALUES (1, 'oem', 'A-1')"
        ))

    _apply_unique_index_migrations(bind=engine)

    idx_names = {i["name"] for i in inspect(engine).get_indexes("cross_references")}
    assert "uq_cross_references_product_type_number" in idx_names

    with pytest.raises(Exception):  # sqlite3.IntegrityError via DBAPI
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO cross_references (product_id, ref_type, ref_number) "
                "VALUES (1, 'oem', 'A-1')"
            ))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Statement migration lines — legacy engine picks up over_90 + snapshot_json
# ─────────────────────────────────────────────────────────────────────────────

def test_statement_columns_added_on_legacy_engine():
    engine = bare_engine()
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE customer_statements (id INTEGER PRIMARY KEY)"
        ))

    _apply_inline_migrations(bind=engine)

    cols = {c["name"]: c for c in inspect(engine).get_columns("customer_statements")}
    assert "over_90" in cols
    assert "snapshot_json" in cols

    # NOT NULL DEFAULT means a legacy insert without the columns still works.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO customer_statements (id) VALUES (1)"))
        row = conn.execute(text(
            "SELECT over_90, snapshot_json FROM customer_statements WHERE id = 1"
        )).one()
    assert row[0] == 0
    assert row[1] == ""


# ─────────────────────────────────────────────────────────────────────────────
# 4. Inventory resync — ledger is the source of truth, commitments ignored
# ─────────────────────────────────────────────────────────────────────────────

def _seed_ledgered_product(db) -> Product:
    """+10 received, -3 sold, -4 committed, +1 released → on-hand truth = 7,
    open commitment = 3."""
    product = _mk_product(db)
    _txn(db, product.id, InventoryTxnType.PO_RECEIPT, +10, qty_after=10)
    _txn(db, product.id, InventoryTxnType.INVOICE_SALE, -3, qty_after=7)
    _txn(db, product.id, InventoryTxnType.SO_COMMITTED, -4, qty_after=7)
    _txn(db, product.id, InventoryTxnType.SO_RELEASED, +1, qty_after=7)
    return product


def test_resync_corrects_drifted_qty_on_hand(client, db, admin_user):
    product = _seed_ledgered_product(db)
    product.qty_on_hand = 99  # deliberate cache drift
    db.commit()

    resp = client.post(f"/admin/inventory/resync/{product.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["old_qty_on_hand"] == 99
    assert body["new_qty_on_hand"] == 7
    assert body["delta"] == -92

    db.expire_all()
    assert db.query(Product).get(product.id).qty_on_hand == 7


def test_resync_ignores_commitments_counted_on_hand(client, db, admin_user):
    """SO-committed stock is still physically on the shelf: a fully-committed
    product must resync to the RECEIVED quantity, not received-minus-committed.
    The commitment itself is recoverable separately from the same ledger."""
    product = _mk_product(db)
    _txn(db, product.id, InventoryTxnType.PO_RECEIPT, +5, qty_after=5)
    _txn(db, product.id, InventoryTxnType.SO_COMMITTED, -5, qty_after=5)
    product.qty_on_hand = 0  # drifted: someone "resynced" including commitments
    db.commit()

    resp = client.post(f"/admin/inventory/resync/{product.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_qty_on_hand"] == 5  # committed ≠ gone

    service = ProductService(db)
    assert service.get_qty_on_hand(product.id) == 5
    assert service.get_qty_committed_from_ledger(product.id) == 5


def test_get_qty_committed_from_ledger_nets_releases(db):
    product = _seed_ledgered_product(db)
    service = ProductService(db)
    assert service.get_qty_committed_from_ledger(product.id) == 3  # 4 committed - 1 released
    # And a product with no commitment rows reads 0, not None.
    bare = _mk_product(db)
    assert service.get_qty_committed_from_ledger(bare.id) == 0


def test_resync_all_summary_and_limit(client, db, admin_user):
    drifted_a = _seed_ledgered_product(db)
    drifted_b = _seed_ledgered_product(db)
    drifted_a.qty_on_hand = 50
    drifted_b.qty_on_hand = -2
    db.commit()

    resp = client.post("/admin/inventory/resync-all")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checked"] >= 2
    corrected_ids = {c["product_id"] for c in body["corrections"]}
    assert {drifted_a.id, drifted_b.id} <= corrected_ids
    assert body["corrected"] == len(body["corrections"])
    for c in body["corrections"]:
        if c["product_id"] in (drifted_a.id, drifted_b.id):
            assert c["new_qty_on_hand"] == 7

    db.expire_all()
    assert db.query(Product).get(drifted_a.id).qty_on_hand == 7
    assert db.query(Product).get(drifted_b.id).qty_on_hand == 7

    # Second pass: nothing left to correct.
    resp2 = client.post("/admin/inventory/resync-all")
    assert resp2.json()["corrected"] == 0

    # Row limit is respected.
    resp3 = client.post("/admin/inventory/resync-all?limit=1")
    assert resp3.status_code == 200
    assert resp3.json()["checked"] == 1


def test_resync_unknown_product_404(client, db, admin_user):
    resp = client.post("/admin/inventory/resync/999999")
    assert resp.status_code == 404


def test_resync_blocked_for_non_admin(client, db, admin_user):
    product = _mk_product(db)
    admin_user.role = UserRole.SALES
    db.commit()
    try:
        resp = client.post(f"/admin/inventory/resync/{product.id}")
        assert resp.status_code == 403
        resp_all = client.post("/admin/inventory/resync-all")
        assert resp_all.status_code == 403
    finally:
        admin_user.role = UserRole.ADMIN
        db.commit()
