"""
tests/test_customer_metrics.py
==============================
Phase 2 §4.4 / P2-Q1 — CustomerMetricsService.

P2-Q1 (locked): Lifetime / YTD = net invoiced incl. open, minus returns/credits;
YTD = calendar year. Returns/credits flow through CreditMemo here.

Covers the full metric dict, the draft/void exclusion, the calendar-YTD split,
open-AR / available-credit, outstanding core credits, last-activity dates, the
batch method, and the empty-customer (no-activity) shape.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, CreditMemoStatus, CoreDirection, CoreStatus,
    PaymentStatus, PaymentDirection,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine, Payment, PaymentAllocation
from app.models.credit_memo import CreditMemo
from app.models.core import CoreCharge
from app.models.product import Product
from app.models.quote import Quote
from app.services.customer_metrics_service import CustomerMetricsService, METRIC_KEYS
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)
THIS_YEAR = datetime.utcnow().year


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


def _customer(db, **kw):
    c = Customer(company_name=f"Metrics Co {next(_seq)}", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    p = Product(sku=f"SKU{next(_seq)}", title="Widget", cost=10.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, cust, *, status, amount, created_at, paid=0.0):
    """Invoice with one non-taxable PRODUCT line of `amount`, optionally `paid`."""
    inv = Invoice(
        invoice_number=f"INV-{next(_seq):05d}",
        customer_id=cust.id, status=status, is_taxable=False,
        created_at=created_at,
    )
    inv.lines.append(InvoiceLine(
        line_type=LineType.PRODUCT, qty=1, unit_price=amount, is_taxable=False,
    ))
    db.add(inv); db.commit(); db.refresh(inv)
    if paid:
        p = Payment(
            customer_id=cust.id, amount_received=paid, status=PaymentStatus.APPLIED,
            direction=PaymentDirection.INCOMING_FROM_CUSTOMER, payment_date=created_at,
        )
        db.add(p); db.flush()
        db.add(PaymentAllocation(payment_id=p.id, invoice_id=inv.id, amount_applied=paid))
        db.commit()
    return inv


@pytest.fixture()
def rich_customer(db):
    """A customer with a deliberately mixed history; values asserted below."""
    c = _customer(db, credit_limit=1000.0, credit_balance=25.0)
    prod = _product(db)

    _invoice(db, c, status=InvoiceStatus.OPEN, amount=200.0,
             created_at=datetime(THIS_YEAR, 6, 1))                       # open, unpaid
    _invoice(db, c, status=InvoiceStatus.PAID, amount=300.0,
             created_at=datetime(THIS_YEAR, 6, 2), paid=300.0)           # paid
    _invoice(db, c, status=InvoiceStatus.OPEN, amount=1000.0,
             created_at=datetime(THIS_YEAR - 1, 6, 1))                   # last year, open
    _invoice(db, c, status=InvoiceStatus.DRAFT, amount=500.0,
             created_at=datetime(THIS_YEAR, 6, 5))                       # EXCLUDED (draft)
    _invoice(db, c, status=InvoiceStatus.VOID, amount=777.0,
             created_at=datetime(THIS_YEAR, 6, 6))                       # EXCLUDED (void)

    # Credit memos (returns/credits) — one this year, one last year, one reversed
    db.add(CreditMemo(cm_number=f"CM-{next(_seq)}", customer_id=c.id,
                      total_amount=50.0, status=CreditMemoStatus.OPEN,
                      created_at=datetime(THIS_YEAR, 6, 3)))
    db.add(CreditMemo(cm_number=f"CM-{next(_seq)}", customer_id=c.id,
                      total_amount=30.0, status=CreditMemoStatus.OPEN,
                      created_at=datetime(THIS_YEAR - 1, 3, 1)))
    db.add(CreditMemo(cm_number=f"CM-{next(_seq)}", customer_id=c.id,
                      total_amount=999.0, status=CreditMemoStatus.REVERSED,
                      created_at=datetime(THIS_YEAR, 6, 3)))             # ignored

    # Cores — one outstanding customer core, one closed, one vendor-side
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=c.id,
                      product_id=prod.id, customer_unit_charge=40.0,
                      qty_charged=2, qty_returned=1, status=CoreStatus.OPEN))
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=c.id,
                      product_id=prod.id, customer_unit_charge=99.0,
                      qty_charged=1, qty_returned=0, status=CoreStatus.CLOSED))
    db.add(CoreCharge(direction=CoreDirection.VENDOR_OWES_CREDIT, customer_id=c.id,
                      product_id=prod.id, customer_unit_charge=88.0,
                      qty_charged=1, qty_returned=0, status=CoreStatus.OPEN))

    # Payments: a reversed one LATER than the applied one must not be "last payment"
    db.add(Payment(customer_id=c.id, amount_received=10.0, status=PaymentStatus.REVERSED,
                   direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
                   payment_date=datetime(THIS_YEAR, 7, 1)))
    # Quotes
    db.add(Quote(quote_number=f"Q-{next(_seq)}", customer_id=c.id,
                 created_at=datetime(THIS_YEAR, 4, 1)))
    db.add(Quote(quote_number=f"Q-{next(_seq)}", customer_id=c.id,
                 created_at=datetime(THIS_YEAR, 5, 1)))
    db.commit()
    return c


def test_metric_dict_has_all_keys(db, rich_customer):
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    assert set(m.keys()) == set(METRIC_KEYS)


def test_gross_and_lifetime_and_ytd(db, rich_customer):
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    assert m["gross_invoiced"] == 1500.0          # 200 + 300 + 1000 (draft/void excluded)
    assert m["invoice_count"] == 3
    assert m["aov"] == 500.0                       # 1500 / 3
    assert m["lifetime_sales"] == 1420.0           # 1500 - (50 + 30) credits
    assert m["ytd_sales"] == 450.0                 # (200+300) this year - 50 credit this year


def test_open_ar_and_available_credit(db, rich_customer):
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    assert m["open_ar"] == 1200.0                  # 200 + 0 (paid) + 1000 (last-yr open)
    assert m["credit_limit"] == 1000.0
    assert m["available_credit"] == -200.0         # 1000 - 1200 (over limit)
    assert m["credit_balance"] == 25.0


def test_outstanding_core_credits(db, rich_customer):
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    # only the open CUSTOMER_OWES_RETURN core: 40 * (2 - 1) = 40
    assert m["outstanding_core_credits"] == 40.0


def test_last_activity_dates(db, rich_customer):
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    assert m["last_invoice_date"] == datetime(THIS_YEAR, 6, 2)   # last posted invoice
    assert m["last_payment_date"] == datetime(THIS_YEAR, 6, 2)   # applied, not the reversed 7/1
    assert m["last_quote_date"] == datetime(THIS_YEAR, 5, 1)


def test_open_ar_helper_matches_metrics(db, rich_customer):
    svc = CustomerMetricsService(db)
    assert svc.open_ar(rich_customer.id) == svc.metrics_for(rich_customer)["open_ar"]


def test_batch_matches_single(db, rich_customer):
    svc = CustomerMetricsService(db)
    batch = svc.metrics_for_batch([rich_customer.id])
    assert batch[rich_customer.id] == svc.metrics_for(rich_customer)


def test_ui_panel_aliases(db, rich_customer):
    # The intelligence-panel macro reads avg_order_value / open_ar_balance /
    # credit_hold — they must carry the same values as aov / open_ar / the flag.
    m = CustomerMetricsService(db).metrics_for(rich_customer)
    assert m["avg_order_value"] == m["aov"]
    assert m["open_ar_balance"] == m["open_ar"]
    assert m["credit_hold"] is False         # rich_customer has no credit-hold flag


def test_credit_hold_alias_reflects_flag(db):
    from app.constants import CustomerFlag
    from app.services.customer_service import CustomerService
    c = _customer(db)
    CustomerService(db).set_flag(c, CustomerFlag.CREDIT_HOLD, True)
    db.commit()
    assert CustomerMetricsService(db).metrics_for(c)["credit_hold"] is True


def test_empty_customer_is_all_zero(db):
    c = _customer(db)   # no limit, no activity
    m = CustomerMetricsService(db).metrics_for(c)
    assert m["lifetime_sales"] == 0.0
    assert m["ytd_sales"] == 0.0
    assert m["invoice_count"] == 0
    assert m["aov"] == 0.0
    assert m["open_ar"] == 0.0
    assert m["available_credit"] is None           # no credit limit set
    assert m["outstanding_core_credits"] == 0.0
    assert m["last_invoice_date"] is None


def test_batch_none_covers_active_customers(db):
    # metrics_for_batch(None) should return a dict keyed by every active customer.
    c = _customer(db)
    out = CustomerMetricsService(db).metrics_for_batch(None)
    assert c.id in out
    assert set(out[c.id].keys()) == set(METRIC_KEYS)
