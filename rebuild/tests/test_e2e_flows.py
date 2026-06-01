"""
tests/test_e2e_flows.py
=======================
Cross-workflow end-to-end integration tests (TESTING_FEEDBACK §8 — "the real
proof"). Each flow runs a full lifecycle and confirms data stays consistent at
every hop. Zero prior functional confirmation as a chained flow.

Flows:
  a. new vendor + product -> PO -> receive -> inventory up + moving-avg cost updated
  b. in-stock SO -> fulfill -> invoice (OPEN) -> inventory down -> full payment -> PAID
  c. OOS SO (linked PO) -> deposit -> PO receive allocates -> fulfill -> invoice (deposit applied)
  d. core charge (invoice hook) -> customer return (credit) -> submit to vendor -> vendor credit
  e. overdue invoice -> appears in the right AR-aging bucket + statement page renders
  f. receive PO -> create vendor bill with exact qty/cost match -> AUTO-APPROVED
  g. receive PO -> bill with over-qty -> DISCREPANCY (not approved)
  h. receive PO -> bill with exact qty but cost variance -> DISCREPANCY (not approved)  ← #13

Strategy: module TestClient runs startup (admin user #1 = ADMIN, core locations),
so every service runs as a fully-permissioned user and core location routing works.
Services are called directly; assertions are behaviour-level (inventory, cost,
status, balances, ledger), never just HTTP 200.

── OWNER ACCEPTANCE TESTS ───────────────────────────────────────────────────
The §8 cross-workflow flows (b, c, d, f, g, h) are marked @pytest.mark.acceptance.
Run the acceptance suite against any fresh build with:

    .venv\\Scripts\\python.exe -m pytest tests/test_e2e_flows.py -m acceptance -v

These are the three core business spines the owner validates before go-live:
  sale→paid  :  pytest -m acceptance -k "instock_sale"
  OOS→invoice:  pytest -m acceptance -k "oos_linked"
  core→credit:  pytest -m acceptance -k "core_charge"

All tests use an isolated in-memory DB seeded fresh per run — never touch jaks.db.
"""
from __future__ import annotations

import datetime
import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from tests.conftest import activate, fresh_engine
from app.main import app

# Acceptance marker — see module docstring for how to run.
pytestmark_acceptance = pytest.mark.acceptance

_ENGINE = fresh_engine()
_UID = 1  # admin user seeded by startup
_counter = itertools.count(1)


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


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _customer(db, **kw):
    from app.models.customer import Customer
    c = Customer(company_name=f"E2E Cust {next(_counter)}", is_active=True, **kw)
    db.add(c); db.flush()
    return c


def _vendor(db):
    from app.models.vendor import Vendor
    v = Vendor(name=f"E2E Vendor {next(_counter)}")
    db.add(v); db.flush()
    return v


def _product(db, *, qty_on_hand=0, cost=10.0):
    from app.constants import ProductStatus
    from app.models.product import Product
    n = next(_counter)
    p = Product(sku=f"E2E-{n:05d}", title=f"E2E Part {n}", description="E2E Part",
                cost=cost, last_cost=cost, markup_pct=50.0, qty_on_hand=qty_on_hand,
                qty_committed=0, status=ProductStatus.ACTIVE, is_active=True)
    db.add(p); db.flush()
    return p


def _po_sent(db, vendor, product, *, qty=10, unit_cost=12.0):
    from app.constants import POStatus
    from app.models.purchase_order import PurchaseOrder, POLine
    po = PurchaseOrder(po_number=f"PO-E2E-{next(_counter)}", vendor_id=vendor.id,
                       status=POStatus.SENT)
    db.add(po); db.flush()
    line = POLine(po_id=po.id, product_id=product.id, description=product.title,
                  qty_ordered=qty, unit_cost=unit_cost)
    db.add(line); db.flush()
    return po, line


def _so_with_line(db, customer, product, *, qty=3, price=50.0,
                  fulfillment=None, line_status=None, linked_po_line_id=None,
                  committed=None, payment_mode=None):
    from app.constants import (FulfillmentSource, LineType, SOLineStatus,
                               SOPaymentMode, SOStatus)
    from app.models.quote import SalesOrder, SOLine
    so = SalesOrder(so_number=f"SO-E2E-{next(_counter)}", customer_id=customer.id,
                    status=SOStatus.OPEN,
                    payment_mode=payment_mode or SOPaymentMode.NONE,
                    deposit_amount=0.0)
    db.add(so); db.flush()
    committed = qty if committed is None else committed
    line = SOLine(so_id=so.id, product_id=product.id, line_type=LineType.PRODUCT,
                  description=product.title, qty_ordered=qty, qty_committed=committed,
                  qty_fulfilled=0, qty_invoiced=0, unit_price=price,
                  unit_cost=product.cost, source="stock",
                  fulfillment_source=fulfillment or FulfillmentSource.STOCK,
                  line_status=line_status or SOLineStatus.RESERVED_STOCK,
                  linked_po_line_id=linked_po_line_id)
    product.qty_committed += committed
    db.add(line); db.flush()
    return so, line


# ===========================================================================
# §8.a — new vendor + product -> PO -> receive -> inventory up + cost updated
# ===========================================================================

def test_e2e_a_po_receive_updates_inventory_and_cost(db):
    from app.constants import InventoryTxnType
    from app.models.inventory import InventoryTransaction
    from app.services.po_service import POService

    ven = _vendor(db)
    prod = _product(db, qty_on_hand=0, cost=10.0)  # no stock, old cost 10
    po, line = _po_sent(db, ven, prod, qty=10, unit_cost=12.0)
    db.commit()

    POService(db, _UID).create_receipt(
        vendor_id=ven.id, po_line_quantities={line.id: 10}, data={})

    db.expire_all()
    from app.models.product import Product
    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_on_hand == 10                       # inventory up
    assert prod_r.cost == 12.0                            # moving-avg from 0 stock -> new cost
    txn = (db.query(InventoryTransaction)
           .filter(InventoryTransaction.product_id == prod.id,
                   InventoryTransaction.transaction_type == InventoryTxnType.PO_RECEIPT)
           .first())
    assert txn is not None and txn.qty_change == 10


# ===========================================================================
# §8.b — in-stock SO -> fulfill -> invoice -> inventory down -> payment -> PAID
# ===========================================================================
@pytest.mark.acceptance

def test_e2e_b_instock_sale_to_paid(db):
    from app.constants import InvoiceStatus, PaymentMethod
    from app.models.invoice import Invoice
    from app.models.product import Product
    from app.services.sales_order_service import SalesOrderService
    from app.services.payment_service import PaymentService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=10, cost=10.0)
    so, line = _so_with_line(db, cust, prod, qty=3, price=50.0)
    db.commit()

    inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {line.id: 3})
    db.expire_all()
    assert inv.status == InvoiceStatus.OPEN
    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_on_hand == 7  # 10 - 3 shipped

    inv_total = inv.total
    PaymentService(db, _UID).record_payment(
        customer_id=cust.id, amount_received=inv_total,
        payment_method=PaymentMethod.CHECK, data={"notes": "full"},
        invoice_ids=[inv.id])

    db.expire_all()
    inv_r = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv_r.status == InvoiceStatus.PAID
    assert inv_r.balance_due == 0.0


# ===========================================================================
# §8.c — OOS SO + linked PO -> deposit -> receive allocates -> fulfill -> invoice
@pytest.mark.acceptance
# ===========================================================================

def test_e2e_c_oos_linkedpo_deposit_fulfill(db):
    from app.constants import (FulfillmentSource, InvoiceStatus, PaymentMethod,
                               SOLineStatus, SOPaymentMode)
    from app.models.product import Product
    from app.models.quote import SOLine
    from app.services.po_service import POService
    from app.services.sales_order_service import SalesOrderService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=0, cost=30.0)   # out of stock
    ven = _vendor(db)
    po, po_line = _po_sent(db, ven, prod, qty=5, unit_cost=30.0)
    # SO line linked to the PO line, awaiting receipt, nothing committed yet
    so, so_line = _so_with_line(
        db, cust, prod, qty=5, price=100.0, payment_mode=SOPaymentMode.DEPOSIT,
        fulfillment=FulfillmentSource.LINKED_PO,
        line_status=SOLineStatus.AWAITING_PO_RECEIPT, linked_po_line_id=po_line.id,
        committed=0)
    db.commit()

    # deposit on the SO
    SalesOrderService(db, _UID).collect_deposit(so.id, 100.0, PaymentMethod.CHECK)

    # receiving the linked PO allocates the received qty to the SO line
    POService(db, _UID).create_receipt(
        vendor_id=ven.id, po_line_quantities={po_line.id: 5}, data={})
    db.expire_all()
    so_line_r = db.query(SOLine).filter(SOLine.id == so_line.id).first()
    prod_r = db.query(Product).filter(Product.id == prod.id).first()
    assert prod_r.qty_on_hand == 5          # received into stock
    assert so_line_r.qty_committed == 5     # allocated to the SO line
    assert so_line_r.line_status == SOLineStatus.RESERVED_STOCK

    # fulfill the now-reserved line -> invoice, deposit auto-applied.
    # $100 deposit on a $500 invoice -> correctly PARTIAL (partially paid), not OPEN.
    inv = SalesOrderService(db, _UID).fulfill_and_invoice(so.id, {so_line.id: 5})
    db.expire_all()
    assert inv.status == InvoiceStatus.PARTIAL
    assert inv.amount_paid >= 99.99, "deposit should carry onto the invoice"
    assert inv.balance_due < inv.total, "deposit should reduce the balance due"


# ===========================================================================
# §8.d — core charge (invoice hook) -> customer return -> vendor return -> credit
# ===========================================================================
@pytest.mark.acceptance

def test_e2e_d_core_charge_to_vendor_credit(db):
    from app.constants import (CoreInspectionOutcome, CoreStatus, InvoiceStatus)
    from app.models.invoice import Invoice
    from app.models.vendor import VendorCredit
    from app.services.core_service import CoreService

    cust = _customer(db)
    prod = _product(db, qty_on_hand=1)
    ven = _vendor(db)
    inv = Invoice(invoice_number=f"INV-E2E-{next(_counter)}", customer_id=cust.id,
                  status=InvoiceStatus.DRAFT)
    db.add(inv); db.commit()
    start_balance = cust.credit_balance

    svc = CoreService(db, _UID)
    # invoice-finalize hook: one core charge per core line
    core = svc.create_core_charge(invoice_id=inv.id, invoice_line_id=None,
                                  product_id=prod.id, qty=1,
                                  customer_unit_charge=50.0, vendor_unit_charge=30.0)
    core.vendor_id = ven.id
    db.commit()

    # customer brings the old core back (accepted) -> account credit
    svc.record_customer_return(core.id, qty_returned=1,
                               inspection_outcome=CoreInspectionOutcome.ACCEPTED)
    # ship to vendor, vendor accepts and issues a credit
    svc.submit_to_vendor(core.id, tracking_number="1Z-E2E")
    svc.record_vendor_acceptance(core.id, credit_amount=30.0)

    db.expire_all()
    db.refresh(core); db.refresh(cust)
    assert core.status == CoreStatus.VENDOR_ACCEPTED
    assert cust.credit_balance == start_balance + 50.0       # customer credited
    vc = (db.query(VendorCredit)
          .filter(VendorCredit.vendor_id == ven.id, VendorCredit.amount == 30.0)
          .first())
    assert vc is not None, "vendor acceptance should create a VendorCredit"


# ===========================================================================
# §8.e — overdue invoice -> right AR-aging bucket + statement renders
# ===========================================================================

def test_e2e_e_overdue_invoice_aging_and_statement(db, client):
    from app.constants import InvoiceStatus
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.report_service import ReportService

    as_of = datetime.date.today()
    cust = _customer(db)
    inv = Invoice(invoice_number=f"INV-OD-{next(_counter)}", customer_id=cust.id,
                  status=InvoiceStatus.OPEN,
                  due_date=datetime.datetime.utcnow() - datetime.timedelta(days=45))
    db.add(inv); db.flush()
    db.add(InvoiceLine(invoice_id=inv.id, qty=1, unit_price=100.0, is_taxable=False))
    db.commit()

    data = ReportService(db).get_ar_aging(as_of_date=as_of)
    row = next((r for r in data["rows"] if r["customer_id"] == cust.id), None)
    assert row is not None, "overdue invoice's customer must appear in AR aging"
    assert row["31_60"] == 100.0, "45-days-overdue balance belongs in the 31_60 bucket"

    # customer statement page renders with the overdue invoice
    r = client.get(f"/customers/{cust.id}/statement")
    assert r.status_code == 200


# ===========================================================================
# §8.f — PO receive -> create vendor bill -> 3-way match AUTO-APPROVES on a
#        clean qty/cost match (TESTING_FEEDBACK §2.4e — #13). Proves the full
#        receive->bill chain end to end; the discrepancy branch is §8.g below.
# ===========================================================================
@pytest.mark.acceptance

def test_e2e_f_receive_then_bill_auto_approves(db):
    from app.constants import POStatus, VendorBillStatus
    from app.models.purchase_order import PurchaseOrder, VendorBill
    from app.services.po_service import POService

    ven = _vendor(db)
    prod = _product(db, qty_on_hand=0, cost=10.0)
    po, line = _po_sent(db, ven, prod, qty=10, unit_cost=12.0)
    db.commit()

    svc = POService(db, _UID)
    # Receive everything ordered, then bill exactly what was received at PO cost.
    svc.create_receipt(vendor_id=ven.id, po_line_quantities={line.id: 10}, data={})
    db.expire_all()

    bill = svc.create_vendor_bill(
        po_id=po.id, vendor_id=ven.id,
        bill_number=f"BILL-E2E-{next(_counter)}",
        bill_date=datetime.date.today(),
        due_date=datetime.date.today() + datetime.timedelta(days=30),
        lines=[{"po_line_id": line.id, "qty_billed": 10, "unit_cost": 12.0}],
    )

    db.expire_all()
    bill_r = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert bill_r.status == VendorBillStatus.APPROVED, (
        "a clean 3-way match (billed qty == received qty) must AUTO-APPROVE the "
        f"bill; got {bill_r.status}"
    )
    assert bill_r.total_amount == 120.0          # 10 * 12.00
    po_r = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
    assert po_r.status == POStatus.BILLED, (
        "a PO whose every received line is fully billed must advance to BILLED; "
        f"got {po_r.status}"
    )


# ===========================================================================
# §8.g — counter-case: billing MORE than was received flags a DISCREPANCY and
#        does NOT auto-approve (TESTING_FEEDBACK §2.4f — #13). Confirms approval
#        is driven by the match, not granted blindly.
# ===========================================================================
@pytest.mark.acceptance

def test_e2e_g_overbill_flags_discrepancy_not_approved(db):
    from app.constants import POStatus, VendorBillStatus
    from app.models.purchase_order import PurchaseOrder, VendorBill
    from app.services.po_service import POService

    ven = _vendor(db)
    prod = _product(db, qty_on_hand=0, cost=10.0)
    po, line = _po_sent(db, ven, prod, qty=10, unit_cost=12.0)
    db.commit()

    svc = POService(db, _UID)
    svc.create_receipt(vendor_id=ven.id, po_line_quantities={line.id: 10}, data={})
    db.expire_all()

    # Bill 12 against 10 received -> over-billed -> discrepancy, NOT auto-approved.
    bill = svc.create_vendor_bill(
        po_id=po.id, vendor_id=ven.id,
        bill_number=f"BILL-E2E-{next(_counter)}",
        bill_date=datetime.date.today(),
        due_date=datetime.date.today() + datetime.timedelta(days=30),
        lines=[{"po_line_id": line.id, "qty_billed": 12, "unit_cost": 12.0}],
    )

    db.expire_all()
    bill_r = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert bill_r.status == VendorBillStatus.DISCREPANCY, (
        "billing more than received must flag DISCREPANCY, not auto-approve; "
        f"got {bill_r.status}"
    )
    # A flagged bill must NOT advance the PO to BILLED.
    po_r = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
    assert po_r.status != POStatus.BILLED


# ===========================================================================
# §8.h — #13 cost-mismatch gap: exact qty received+billed, but the vendor
#        charges a DIFFERENT unit cost than the PO. Must flag DISCREPANCY
#        (not auto-approve). Previously untested — cost variance path was
#        implemented in create_vendor_bill but had no regression guard.
# ===========================================================================

@pytest.mark.acceptance
def test_e2e_h_cost_variance_flags_discrepancy(db):
    """Exact qty match + cost mismatch → DISCREPANCY, not APPROVED.

    This is the missing #13 guard. The quantity-only mismatch is §8.g.
    This test covers the `has_cost_discrepancy` branch (abs(billed - ordered)
    >= COST_VARIANCE_TOLERANCE). A vendor who billed exact qty but at a higher
    cost than the PO must NOT auto-approve — AP must review the overcharge.
    """
    from app.constants import POStatus, VendorBillStatus
    from app.models.purchase_order import PurchaseOrder, VendorBill
    from app.models.purchase_order import COST_VARIANCE_TOLERANCE
    from app.services.po_service import POService

    ven = _vendor(db)
    prod = _product(db, qty_on_hand=0, cost=10.0)
    # PO ordered at $12.00
    po, line = _po_sent(db, ven, prod, qty=10, unit_cost=12.0)
    db.commit()

    svc = POService(db, _UID)
    svc.create_receipt(vendor_id=ven.id, po_line_quantities={line.id: 10}, data={})
    db.expire_all()

    # Bill exact qty (10) but at $15.00 — $3.00 over the PO cost (>> tolerance).
    bill = svc.create_vendor_bill(
        po_id=po.id, vendor_id=ven.id,
        bill_number=f"BILL-E2E-{next(_counter)}",
        bill_date=datetime.date.today(),
        due_date=datetime.date.today() + datetime.timedelta(days=30),
        lines=[{"po_line_id": line.id, "qty_billed": 10, "unit_cost": 15.0}],
    )

    db.expire_all()
    bill_r = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert bill_r.status == VendorBillStatus.DISCREPANCY, (
        f"Exact qty but billed cost ($15.00) > PO cost ($12.00) by "
        f"${15.0 - 12.0:.2f} (COST_VARIANCE_TOLERANCE={COST_VARIANCE_TOLERANCE}); "
        f"must flag DISCREPANCY not auto-approve; got {bill_r.status!r}"
    )
    # PO must NOT advance to BILLED when the bill has a cost discrepancy.
    po_r = db.query(PurchaseOrder).filter(PurchaseOrder.id == po.id).first()
    assert po_r.status != POStatus.BILLED

    # Sanity: total is based on billed qty × billed cost (not PO cost).
    assert bill_r.total_amount == 150.0   # 10 × $15.00


@pytest.mark.acceptance
def test_e2e_h_within_cost_tolerance_still_approves(db):
    """Cost variance < COST_VARIANCE_TOLERANCE ($0.01) → still APPROVED.

    Rounding differences below the tolerance must not block auto-approval.
    """
    from app.constants import VendorBillStatus
    from app.models.purchase_order import VendorBill, COST_VARIANCE_TOLERANCE
    from app.services.po_service import POService

    ven = _vendor(db)
    prod = _product(db, qty_on_hand=0, cost=10.0)
    po, line = _po_sent(db, ven, prod, qty=5, unit_cost=10.0)
    db.commit()

    svc = POService(db, _UID)
    svc.create_receipt(vendor_id=ven.id, po_line_quantities={line.id: 5}, data={})
    db.expire_all()

    # Bill at $10.009 — delta of $0.009 is below the $0.01 tolerance.
    billed_cost = round(line.unit_cost + COST_VARIANCE_TOLERANCE - 0.001, 3)
    bill = svc.create_vendor_bill(
        po_id=po.id, vendor_id=ven.id,
        bill_number=f"BILL-E2E-{next(_counter)}",
        bill_date=datetime.date.today(),
        due_date=datetime.date.today() + datetime.timedelta(days=30),
        lines=[{"po_line_id": line.id, "qty_billed": 5, "unit_cost": billed_cost}],
    )

    db.expire_all()
    bill_r = db.query(VendorBill).filter(VendorBill.id == bill.id).first()
    assert bill_r.status == VendorBillStatus.APPROVED, (
        f"Cost delta {billed_cost - line.unit_cost:.4f} is below tolerance "
        f"{COST_VARIANCE_TOLERANCE}; must still auto-approve; got {bill_r.status!r}"
    )
