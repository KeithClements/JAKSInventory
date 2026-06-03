"""
tests/test_invoice_metrics.py
=============================
Phase 2 §5.8 / P2-D3 — InvoiceMetricsService (Invoice Intelligence panel).

Covers the contract keys the invoice_intelligence_panel macro reads:
profit / margin_pct / core_liability / warranty_exposure / customer_lifetime_sales.
Profit is parts-only and net of the invoice discount; cores/warranty surface in
their own cells; lifetime reuses the single P2-Q1 CustomerMetricsService number.
"""
from __future__ import annotations

import itertools
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import (
    InvoiceStatus, LineType, CoreDirection, CoreStatus, WarrantyStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.core import CoreCharge
from app.models.product import Product
from app.models.warranty import WarrantyClaim
from app.services.invoice_metrics_service import InvoiceMetricsService, INVOICE_METRIC_KEYS
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
    c = Customer(company_name=f"Inv Intel Co {next(_seq)}")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db):
    p = Product(sku=f"SKU{next(_seq)}", title="Part", cost=10.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, cust, status, lines, discount_pct=0.0):
    """lines = list of (line_type, qty, unit_price, unit_cost)."""
    inv = Invoice(invoice_number=f"INV-{next(_seq):05d}", customer_id=cust.id,
                  status=status, is_taxable=False, discount_pct=discount_pct)
    for lt, qty, price, cost in lines:
        inv.lines.append(InvoiceLine(line_type=lt, qty=qty, unit_price=price,
                                     unit_cost=cost, is_taxable=False))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


@pytest.fixture()
def scenario(db):
    cust = _customer(db)
    prod = _product(db)
    # Lifetime history: a separate POSTED invoice ($500). The target stays DRAFT
    # so it doesn't count toward lifetime — keeps the number deterministic.
    _invoice(db, cust, InvoiceStatus.OPEN, [(LineType.PRODUCT, 1, 500.0, 0.0)])

    target = _invoice(db, cust, InvoiceStatus.DRAFT, [
        (LineType.PRODUCT, 1, 100.0, 60.0),   # profit 40
        (LineType.PRODUCT, 2, 50.0, 30.0),    # rev 100, cost 60, profit 40
        (LineType.FREIGHT, 1, 25.0, 10.0),    # EXCLUDED from profit (not a parts line)
    ])
    # Open customer-owed core on the target's first line → core_liability 40
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, invoice_line_id=target.lines[0].id,
                      customer_unit_charge=40.0, qty_charged=1, qty_returned=0,
                      status=CoreStatus.OPEN))
    # Warranty: one open claim (counts), one closed (excluded) → exposure 150
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=cust.id,
                         invoice_id=target.id, status=WarrantyStatus.SUBMITTED_TO_VENDOR,
                         total_credit_amount=150.0))
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=cust.id,
                         invoice_id=target.id, status=WarrantyStatus.CLOSED,
                         total_credit_amount=999.0))
    db.commit()
    return cust, target


def test_all_contract_keys_present(db, scenario):
    _cust, target = scenario
    m = InvoiceMetricsService(db).intelligence_for(target)
    assert set(m.keys()) == set(INVOICE_METRIC_KEYS)


def test_profit_and_margin_parts_only(db, scenario):
    _cust, target = scenario
    m = InvoiceMetricsService(db).intelligence_for(target)
    # parts revenue 200, cost 120 → profit 80; freight (margin 15) excluded
    assert m["profit"] == 80.0
    assert m["margin_pct"] == 40.0


def test_core_liability(db, scenario):
    _cust, target = scenario
    assert InvoiceMetricsService(db).intelligence_for(target)["core_liability"] == 40.0


def test_warranty_exposure_excludes_terminal(db, scenario):
    _cust, target = scenario
    # only the SUBMITTED_TO_VENDOR claim counts; CLOSED 999 excluded
    assert InvoiceMetricsService(db).intelligence_for(target)["warranty_exposure"] == 150.0


def test_customer_lifetime_matches_customer_service(db, scenario):
    from app.services.customer_metrics_service import CustomerMetricsService
    cust, target = scenario
    m = InvoiceMetricsService(db).intelligence_for(target)
    assert m["customer_lifetime_sales"] == 500.0
    assert m["customer_lifetime_sales"] == \
        CustomerMetricsService(db).metrics_for(cust.id)["lifetime_sales"]


def test_invoice_discount_reduces_profit(db):
    cust = _customer(db)
    inv = _invoice(db, cust, InvoiceStatus.DRAFT,
                   [(LineType.PRODUCT, 1, 200.0, 120.0)], discount_pct=10.0)
    m = InvoiceMetricsService(db).intelligence_for(inv)
    # parts gross 200, discount 20 → net 180; cost 120 → profit 60; margin 33.3
    assert m["profit"] == 60.0
    assert m["margin_pct"] == 33.3


def test_empty_invoice_is_zero(db):
    cust = _customer(db)
    inv = _invoice(db, cust, InvoiceStatus.DRAFT, [])
    m = InvoiceMetricsService(db).intelligence_for(inv)
    assert m["profit"] == 0.0
    assert m["margin_pct"] == 0.0
    assert m["core_liability"] == 0.0
    assert m["warranty_exposure"] == 0.0


def test_missing_invoice_returns_zero_dict(db):
    m = InvoiceMetricsService(db).intelligence_for(999999)
    assert set(m.keys()) == set(INVOICE_METRIC_KEYS)
    assert all(v == 0.0 for v in m.values())
