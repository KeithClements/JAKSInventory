"""
tests/test_money_concurrency_inventory.py
==========================================
Lane A — money-path concurrency + single qty_on_hand writer + nightly resync.

Covers three shipped changes:

1. ATOMIC STATUS CLAIM (audit risk #10) — InvoiceService.finalise() and
   void_invoice() claim the status transition with one conditional UPDATE
   (rowcount==1) so a double submit / concurrent second caller raises instead
   of double-decrementing inventory or double-restoring it. The concurrent
   case is simulated with TWO sessions: the loser session holds a STALE
   identity-mapped invoice (status still DRAFT/OPEN) so it sails past the
   status validation — exactly the race window — and must die on the claim.

2. SINGLE qty_on_hand WRITER (audit risk #9) — the five direct cache writers
   (invoice finalize + void, PO receive, RA return-to-stock, vendor-return
   ship) now route through InventoryService.apply_stock_delta. Representative
   paths assert the SAME cache value + ledger row (type/qty_change/qty_after/
   reference/notes) as before the refactor, including the max(0, ...) clamp
   semantics (cache floors at 0, ledger records the FULL delta).

3. NIGHTLY RESYNC CONTRACT — InventoryService.resync_all_products() re-derives
   the cache from the ledger via ProductService.resync_qty_on_hand and returns
   {'checked','drifted','fixed'} (cross-lane scheduler contract).

Harness: house pattern — module-isolated in-memory engine (conftest
fresh_engine()/activate()) + module TestClient so startup seeds admin user #1.
Seeded stock always gets an INITIAL_COUNT ledger row so cache==ledger at seed
time (the resync test depends on module-wide ledger consistency).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

_ENGINE = fresh_engine()
_UID = 1  # admin user seeded by startup


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def db(client):
    from app.database import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Seed helpers ─────────────────────────────────────────────────────────────

def _customer(db, name: str):
    from app.models.customer import Customer
    c = Customer(company_name=name, contact_name="QA", email=f"{name.lower().replace(' ', '')}@test.local")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _vendor(db, name: str, code: str):
    from app.models.vendor import Vendor
    v = Vendor(name=name, vendor_code=code)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _product(db, sku: str, qty: int = 0, cost: float = 40.0, price: float = 100.0):
    """Seed a product; nonzero starting qty also gets an INITIAL_COUNT ledger
    row so the cache matches the ledger (resync test relies on module-wide
    consistency)."""
    from app.constants import InventoryTxnType, ProductStatus
    from app.models.inventory import InventoryTransaction
    from app.models.product import Product
    p = Product(
        sku=sku,
        title=f"Test part {sku}",
        description="",
        cost=cost,
        last_cost=cost,
        price_override=price,
        qty_on_hand=qty,
        qty_committed=0,
        status=ProductStatus.ACTIVE,
        is_active=True,
    )
    db.add(p)
    db.flush()
    if qty:
        db.add(InventoryTransaction(
            product_id=p.id,
            transaction_type=InventoryTxnType.INITIAL_COUNT,
            qty_change=qty,
            qty_after=qty,
            notes="test seed",
        ))
    db.commit()
    db.refresh(p)
    return p


def _draft_invoice(db, customer_id: int, product_id: int, qty: int = 2, price: float = 50.0) -> int:
    from app.services.invoice_service import InvoiceService
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(customer_id)
    svc.add_line(inv.id, product_id, {"qty": qty, "unit_price": price})
    return inv.id


def _txns(db, product_id: int, txn_type: str):
    from app.models.inventory import InventoryTransaction
    return (
        db.query(InventoryTransaction)
        .filter(
            InventoryTransaction.product_id == product_id,
            InventoryTransaction.transaction_type == txn_type,
        )
        .order_by(InventoryTransaction.id)
        .all()
    )


def _fresh_qty(db, product_id: int) -> int:
    from app.models.product import Product
    db.expire_all()
    return db.query(Product).filter(Product.id == product_id).one().qty_on_hand


# ============================================================================
# 1 — Atomic status claim: finalize
# ============================================================================

def test_finalize_double_submit_sequential(db):
    """Second finalise on the same session raises; inventory decremented ONCE."""
    from app.constants import InventoryTxnType
    from app.services.invoice_service import InvoiceService

    cust = _customer(db, "Claim Seq Trucking")
    prod = _product(db, "CLAIM-SEQ-1", qty=10)
    inv_id = _draft_invoice(db, cust.id, prod.id, qty=2)

    svc = InvoiceService(db, _UID)
    svc.finalise(inv_id)
    with pytest.raises(ValueError):
        svc.finalise(inv_id)

    assert _fresh_qty(db, prod.id) == 8, "double submit must decrement exactly once"
    sales = _txns(db, prod.id, InventoryTxnType.INVOICE_SALE)
    assert len(sales) == 1
    assert sales[0].qty_change == -2 and sales[0].qty_after == 8


def test_finalize_atomic_claim_blocks_stale_second_caller(db):
    """The race window itself: a second session whose invoice object is STALE
    (still DRAFT in its identity map) passes validation but must lose the
    atomic claim — no second decrement, no second ledger row."""
    from app.constants import InventoryTxnType
    from app.database import SessionLocal
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    cust = _customer(db, "Claim Race Trucking")
    prod = _product(db, "CLAIM-RACE-1", qty=10)
    inv_id = _draft_invoice(db, cust.id, prod.id, qty=2)

    sess_a = SessionLocal()  # the loser — loads the invoice while still DRAFT
    sess_b = SessionLocal()  # the winner
    try:
        stale = sess_a.query(Invoice).filter(Invoice.id == inv_id).one()
        assert stale.status == "draft"

        InvoiceService(sess_b, _UID).finalise(inv_id)  # winner commits OPEN

        # Loser: identity map still says DRAFT → validation passes → the
        # conditional UPDATE matches 0 rows → ValueError, before any mutation.
        assert stale.status == "draft", "precondition: loser must be stale"
        with pytest.raises(ValueError, match="already finalized|being finalized"):
            InvoiceService(sess_a, _UID).finalise(inv_id)
        sess_a.rollback()
    finally:
        sess_a.close()
        sess_b.close()

    assert _fresh_qty(db, prod.id) == 8, "loser must not decrement again"
    assert len(_txns(db, prod.id, InventoryTxnType.INVOICE_SALE)) == 1


# ============================================================================
# 2 — Atomic status claim: void
# ============================================================================

def test_void_double_submit_sequential(db):
    from app.constants import InventoryTxnType
    from app.services.invoice_service import InvoiceService

    cust = _customer(db, "Void Seq Trucking")
    prod = _product(db, "VOID-SEQ-1", qty=10)
    inv_id = _draft_invoice(db, cust.id, prod.id, qty=3)

    svc = InvoiceService(db, _UID)
    svc.finalise(inv_id)
    assert _fresh_qty(db, prod.id) == 7

    svc.void_invoice(inv_id, "wrong customer")
    with pytest.raises(ValueError):
        svc.void_invoice(inv_id, "again")

    assert _fresh_qty(db, prod.id) == 10, "void must restore exactly once"
    corrections = _txns(db, prod.id, InventoryTxnType.CORRECTION)
    assert len(corrections) == 1
    assert corrections[0].qty_change == 3 and corrections[0].qty_after == 10


def test_void_atomic_claim_blocks_stale_second_caller(db):
    from app.constants import InventoryTxnType
    from app.database import SessionLocal
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    cust = _customer(db, "Void Race Trucking")
    prod = _product(db, "VOID-RACE-1", qty=10)
    inv_id = _draft_invoice(db, cust.id, prod.id, qty=3)
    InvoiceService(db, _UID).finalise(inv_id)

    sess_a = SessionLocal()
    sess_b = SessionLocal()
    try:
        stale = sess_a.query(Invoice).filter(Invoice.id == inv_id).one()
        assert stale.status == "open"

        InvoiceService(sess_b, _UID).void_invoice(inv_id, "winner voids")

        assert stale.status == "open", "precondition: loser must be stale"
        with pytest.raises(ValueError, match="already void|being voided"):
            InvoiceService(sess_a, _UID).void_invoice(inv_id, "loser voids")
        sess_a.rollback()
    finally:
        sess_a.close()
        sess_b.close()

    assert _fresh_qty(db, prod.id) == 10, "inventory restored exactly once"
    assert len(_txns(db, prod.id, InventoryTxnType.CORRECTION)) == 1


# ============================================================================
# 3 — Single-writer parity: each routed path produces the pre-refactor
#     cache value + ledger row
# ============================================================================

def test_po_receive_writer_parity(db):
    from app.constants import EntityType, InventoryTxnType, POStatus
    from app.models.purchase_order import POLine, PurchaseOrder
    from app.services.po_service import POService

    ven = _vendor(db, "Writer Parity Supply", "WPS")
    prod = _product(db, "PO-WRITER-1", qty=0, cost=0.0)
    po = PurchaseOrder(po_number="PO-TEST-WRITER-1", vendor_id=ven.id, status=POStatus.SENT)
    db.add(po)
    db.flush()
    line = POLine(po_id=po.id, product_id=prod.id, description="part",
                  qty_ordered=3, unit_cost=25.0)
    db.add(line)
    db.commit()

    receipt = POService(db, _UID).create_receipt(
        vendor_id=ven.id, po_line_quantities={line.id: 3}, data={},
    )

    assert _fresh_qty(db, prod.id) == 3
    db.refresh(prod)
    assert prod.cost == 25.0, "moving-average update must still run post-refactor"
    txns = _txns(db, prod.id, InventoryTxnType.PO_RECEIPT)
    assert len(txns) == 1
    t = txns[0]
    assert t.qty_change == 3 and t.qty_after == 3
    assert t.reference_type == EntityType.PO_RECEIPT and t.reference_id == receipt.id
    assert t.notes == f"PO {po.po_number}, line {line.id}"
    assert t.performed_by_id == _UID


def test_ra_return_to_stock_writer_parity(db):
    from app.constants import EntityType, InventoryTxnType, ReturnDisposition
    from app.services.ra_service import RAService

    cust = _customer(db, "RA Writer Trucking")
    prod = _product(db, "RA-WRITER-1", qty=5)

    svc = RAService(db, _UID)
    ra = svc.create_ra(cust.id, "wrong part ordered", [{
        "product_id": prod.id, "description": "returned part",
        "qty": 2, "unit_price": 50.0,
        "disposition": ReturnDisposition.RETURN_TO_STOCK,
    }])
    svc.approve_ra(ra.id)
    line_id = ra.lines[0].id
    svc.receive_goods(ra.id, [{
        "line_id": line_id,
        "disposition": ReturnDisposition.RETURN_TO_STOCK,
        "qty_returned_to_stock": 2,
    }])

    assert _fresh_qty(db, prod.id) == 7
    txns = _txns(db, prod.id, InventoryTxnType.RETURN_TO_STOCK)
    assert len(txns) == 1
    t = txns[0]
    assert t.qty_change == 2 and t.qty_after == 7
    assert t.reference_type == EntityType.RETURN_AUTHORIZATION and t.reference_id == ra.id
    assert t.notes == f"Customer return — {ra.ra_number}"


def test_vendor_return_ship_clamp_parity(db):
    """Cache floors at 0 (pre-refactor max(0,...)) while the ledger row still
    records the FULL negative delta — the exact historical contract."""
    from app.constants import EntityType, InventoryTxnType
    from app.services.vendor_return_service import VendorReturnService

    ven = _vendor(db, "Clamp Parity Supply", "CPS")
    prod = _product(db, "VR-CLAMP-1", qty=1)

    svc = VendorReturnService(db, _UID)
    vr = svc.create_vendor_return(ven.id, [{
        "product_id": prod.id, "description": "damaged part",
        "qty": 3, "expected_unit_credit": 10.0,
    }], reason="damaged in transit")
    svc.ship_return(vr.id, tracking_number="1Z-CLAMP-TEST")

    assert _fresh_qty(db, prod.id) == 0, "cache clamps at zero"
    txns = [t for t in _txns(db, prod.id, InventoryTxnType.MANUAL_ADJUSTMENT)
            if t.reason == "vendor_return_shipped"]
    assert len(txns) == 1
    t = txns[0]
    assert t.qty_change == -3, "ledger records the FULL delta even when clamped"
    assert t.qty_after == 0
    assert t.reference_type == EntityType.VENDOR_RETURN and t.reference_id == vr.id
    assert t.notes == f"Shipped to vendor on VR {vr.vr_number}"


# ============================================================================
# 4 — Nightly resync contract (LAST: it normalizes every product in this
#     module's DB, including the intentional VR clamp divergence above)
# ============================================================================

def test_resync_all_products_detects_and_fixes_drift(db):
    from app.models.product import Product
    from app.services.inventory_service import InventoryService

    prod = _product(db, "RESYNC-1", qty=6)

    # Normalize the module DB (the VR clamp test above diverged on purpose),
    # then prove a clean pass reports zero drift.
    InventoryService(db).resync_all_products()
    clean = InventoryService(db).resync_all_products()
    total_products = db.query(Product).count()
    assert set(clean.keys()) == {"checked", "drifted", "fixed"}, "cross-lane contract keys"
    assert clean["checked"] == total_products
    assert clean["drifted"] == 0 and clean["fixed"] == 0

    # Poison one cache and prove the sweep finds + fixes exactly it.
    db.query(Product).filter(Product.id == prod.id).update({"qty_on_hand": 999})
    db.commit()

    # Exact scheduler-lane call shape: InventoryService(db).resync_all_products()
    report = InventoryService(db).resync_all_products()
    assert report == {"checked": total_products, "drifted": 1, "fixed": 1}
    assert _fresh_qty(db, prod.id) == 6, "cache re-derived from the ledger"
