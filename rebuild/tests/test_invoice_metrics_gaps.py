"""
tests/test_invoice_metrics_gaps.py
==================================
QA gap guards for Invoice Intelligence (PHASE_2_PLAN.md §5.8 / P2-D3).

EXTENDS Backend's contract test (tests/test_invoice_metrics.py), which pins the
happy path on a known invoice (profit 80 / margin 40%, core 40, warranty 150,
discount-reduces-profit, empty/missing). This file adds the edge + fidelity
cases that one is silent on:

  • NEGATIVE margin — selling below cost must surface as a negative number, not
    clamp to 0 (a real "we lost money on this" signal); plus the revenue_net<=0
    division guard.
  • profit is TAX-INVARIANT — it reads line_total, never invoice.total, so making
    the invoice taxable must not move profit/margin a cent.
  • core_liability — a PARTIAL core with a partial return counts its OUTSTANDING
    qty; CLOSED and vendor-side cores are excluded; and a core on ANOTHER invoice
    must not leak in (the invoice_line_id join).
  • warranty_exposure — sums MULTIPLE non-terminal claims and excludes a terminal
    status that ISN'T closed (CUSTOMER_CREDITED), proving the whole terminal set.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_invoice_metrics_gaps.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.constants import (
    CoreDirection, CoreStatus, InvoiceStatus, LineType, WarrantyStatus,
)
from app.models.core import CoreCharge
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.warranty import WarrantyClaim
from app.services.invoice_metrics_service import InvoiceMetricsService
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Seeding ──────────────────────────────────────────────────────────────────

def _customer(db) -> Customer:
    c = Customer(company_name=f"Intel Gap Co {next(_seq)}", contact_name="QA")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _product(db) -> Product:
    p = Product(sku=f"IG-{next(_seq)}", title="Part", cost=10.0)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _invoice(db, customer_id, lines, *, discount_pct=0.0, is_taxable=False,
             tax_rate=0.0, status=InvoiceStatus.DRAFT) -> Invoice:
    """lines = [(line_type, qty, unit_price, unit_cost), ...]."""
    inv = Invoice(
        invoice_number=f"INV-IG-{next(_seq):05d}", customer_id=customer_id,
        status=status, discount_pct=discount_pct, is_taxable=is_taxable,
        tax_rate=tax_rate, tax_rate_snapshot=tax_rate, tax_exempt_snapshot=not is_taxable,
    )
    for lt, qty, price, cost in lines:
        inv.lines.append(InvoiceLine(
            line_type=lt, qty=qty, unit_price=price, unit_cost=cost,
            is_taxable=is_taxable,
        ))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


# ── Negative margin + division guard ─────────────────────────────────────────

def test_intelligence_surfaces_negative_margin_sold_below_cost(db):
    cust = _customer(db)
    # Revenue 100, cost 150 → we lost $50; margin must read -50%, not 0.
    inv = _invoice(db, cust.id, [(LineType.PRODUCT, 1, 100.0, 150.0)])
    m = InvoiceMetricsService(db).intelligence_for(inv)
    assert m["profit"] == -50.0
    assert m["margin_pct"] == -50.0, "below-cost sale must surface a negative margin"


def test_intelligence_zero_revenue_does_not_divide_by_zero(db):
    cust = _customer(db)
    # A free line that still cost us money → revenue_net 0; margin guards to 0.0.
    inv = _invoice(db, cust.id, [(LineType.PRODUCT, 1, 0.0, 50.0)])
    m = InvoiceMetricsService(db).intelligence_for(inv)
    assert m["profit"] == -50.0
    assert m["margin_pct"] == 0.0   # guarded (revenue_net not > 0), not a crash


# ── Profit is tax-invariant (reads line_total, never invoice.total) ──────────

def test_intelligence_profit_is_tax_invariant(db):
    cust = _customer(db)
    lines = [(LineType.PRODUCT, 1, 100.0, 60.0)]      # profit 40, margin 40%
    no_tax = InvoiceMetricsService(db).intelligence_for(
        _invoice(db, cust.id, lines, is_taxable=False))
    taxed = InvoiceMetricsService(db).intelligence_for(
        _invoice(db, cust.id, lines, is_taxable=True, tax_rate=10.0))
    assert no_tax["profit"] == taxed["profit"] == 40.0
    assert no_tax["margin_pct"] == taxed["margin_pct"] == 40.0


# ── core_liability: partial returns, status + direction filter, join isolation ─

def test_core_liability_partial_returns_and_cross_invoice_isolation(db):
    cust = _customer(db)
    prod = _product(db)
    inv_a = _invoice(db, cust.id, [(LineType.PRODUCT, 1, 200.0, 100.0)])
    inv_b = _invoice(db, cust.id, [(LineType.PRODUCT, 1, 200.0, 100.0)])

    # On invoice A: a PARTIAL customer core with 2 of 3 still outstanding (40×2=80),
    # plus a CLOSED core and a vendor-side core that must both be excluded.
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, invoice_line_id=inv_a.lines[0].id,
                      customer_unit_charge=40.0, qty_charged=3, qty_returned=1,
                      status=CoreStatus.PARTIAL))
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, invoice_line_id=inv_a.lines[0].id,
                      customer_unit_charge=100.0, qty_charged=1, qty_returned=0,
                      status=CoreStatus.CLOSED))          # excluded (settled)
    db.add(CoreCharge(direction=CoreDirection.VENDOR_OWES_CREDIT, customer_id=cust.id,
                      product_id=prod.id, invoice_line_id=inv_a.lines[0].id,
                      customer_unit_charge=88.0, qty_charged=1, qty_returned=0,
                      status=CoreStatus.OPEN))            # excluded (vendor side)
    # On invoice B: an OPEN customer core that must NOT bleed into A's liability.
    db.add(CoreCharge(direction=CoreDirection.CUSTOMER_OWES_RETURN, customer_id=cust.id,
                      product_id=prod.id, invoice_line_id=inv_b.lines[0].id,
                      customer_unit_charge=500.0, qty_charged=1, qty_returned=0,
                      status=CoreStatus.OPEN))
    db.commit()

    assert InvoiceMetricsService(db).intelligence_for(inv_a)["core_liability"] == 80.0


# ── warranty_exposure: sum non-terminal, exclude a non-CLOSED terminal ───────

def test_warranty_exposure_sums_open_excludes_nonclosed_terminal(db):
    cust = _customer(db)
    inv = _invoice(db, cust.id, [(LineType.PRODUCT, 1, 100.0, 60.0)])
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=cust.id,
                         invoice_id=inv.id, status=WarrantyStatus.SUBMITTED_TO_VENDOR,
                         total_credit_amount=150.0))      # non-terminal → counts
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=cust.id,
                         invoice_id=inv.id, status=WarrantyStatus.VENDOR_APPROVED,
                         total_credit_amount=75.0))        # non-terminal → counts
    db.add(WarrantyClaim(claim_number=f"W-{next(_seq)}", customer_id=cust.id,
                         invoice_id=inv.id, status=WarrantyStatus.CUSTOMER_CREDITED,
                         total_credit_amount=999.0))       # terminal (not CLOSED) → excluded
    db.commit()

    assert InvoiceMetricsService(db).intelligence_for(inv)["warranty_exposure"] == 225.0
