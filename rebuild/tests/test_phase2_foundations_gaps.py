"""
tests/test_phase2_foundations_gaps.py
=====================================
QA gap guards for the Phase-2 customer foundations (PHASE_2_PLAN.md §4 / §9).

These EXTEND — they do not duplicate — Backend's contract tests
(test_customer_metrics / _credit / _flags / _type_defaults / test_lost_sales).
Those pin each service's happy path in isolation; this file pins the
cross-service and money-fidelity invariants an implementer's own unit tests
tend to miss:

  • METRICS route through the ONE totals engine WITH tax + cores (every Backend
    metrics invoice is is_taxable=False, so a raw line-sum regression would slip
    past them) — P2-Q1's "match the workspace/print to the penny".
  • METRICS open_ar tracks a *reversed payment allocation* — the A/R-corruption
    guard that ties PaymentService.reverse_payment (Suite D-5) to the metrics
    aggregate. Backend's reversed payment carries no allocation, so the
    invoice.amount_paid → balance_due → open_ar chain is unguarded there.
  • METRICS open_ar reflects a *partial* payment (Backend only tests full-paid /
    fully-open).
  • set_flags (FULL replace — clears canonical TAX_EXEMPT / TEXT_PREFERRED too),
    which Backend tests never call (they test set_flag + set_stored_flags).
  • credit_status COMPOSITE warn (on-hold AND over-limit at once) + the exact
    would_exceed_credit boundary.
  • mark_lost on a MIXED quote (product + non-product lines) → only product
    lines are logged.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_phase2_foundations_gaps.py -v
"""
from __future__ import annotations

import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.constants import (
    CustomerFlag, InvoiceStatus, LineType, LostReason, PaymentMethod,
    PreferredContactMethod, QuoteOutcome, QuoteStatus,
)
from app.invoice_totals import compute_invoice_totals
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.quote import LostSaleLog, Quote, QuoteLine
from app.services.customer_metrics_service import CustomerMetricsService
from app.services.customer_service import CustomerService
from app.services.payment_service import PaymentService
from app.services.quote_service import QuoteService
from tests.conftest import activate, fresh_engine

_seq = itertools.count(1)


@pytest.fixture()
def db():
    """Pristine in-memory DB per test (metrics aggregate globally)."""
    SessionLocal = activate(fresh_engine())
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ── Seeding helpers ──────────────────────────────────────────────────────────

def _customer(db, **kw) -> Customer:
    c = Customer(company_name=f"Gap Co {next(_seq)}", contact_name="QA", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


def _invoice(db, customer_id, lines, *, status=InvoiceStatus.OPEN,
             is_taxable=False, tax_rate=0.0) -> Invoice:
    """lines = [(line_type, unit_price, is_taxable, is_core_line), ...]."""
    inv = Invoice(
        invoice_number=f"INV-GAP-{next(_seq):05d}", customer_id=customer_id,
        status=status, is_taxable=is_taxable, tax_rate=tax_rate,
        tax_rate_snapshot=tax_rate, tax_exempt_snapshot=not is_taxable,
    )
    for lt, price, taxable, core in lines:
        inv.lines.append(InvoiceLine(
            line_type=lt, qty=1, unit_price=price, unit_cost=0.0,
            is_taxable=taxable, is_core_line=core,
        ))
    db.add(inv); db.commit(); db.refresh(inv)
    return inv


def _simple_open_invoice(db, customer_id, amount) -> Invoice:
    return _invoice(db, customer_id, [(LineType.PRODUCT, amount, False, False)])


# ════════════════════════════════════════════════════════════════════════════
# METRICS × the totals engine (money fidelity — tax + cores)
# ════════════════════════════════════════════════════════════════════════════

def test_metrics_route_through_totals_engine_with_tax_and_cores(db):
    """gross_invoiced / open_ar must equal compute_invoice_totals to the penny:
    tax INCLUDED, the core line in the subtotal but EXCLUDED from the tax base.
    A raw sum of line unit_prices (150) would be wrong; the engine total is 160."""
    cust = _customer(db)
    inv = _invoice(
        db, cust.id,
        [(LineType.PRODUCT, 100.0, True, False),    # taxable part
         (LineType.CORE_CHARGE, 50.0, False, True)],  # core: never taxed
        is_taxable=True, tax_rate=10.0,
    )
    engine = compute_invoice_totals(inv)
    # Guard is only meaningful if tax actually moved the number off the line-sum.
    assert engine["tax_amount"] == 10.0
    assert engine["total"] == 160.0 != 150.0

    m = CustomerMetricsService(db).metrics_for(cust)
    assert m["gross_invoiced"] == engine["total"] == inv.total      # tax included
    assert m["open_ar"] == engine["balance_due"] == inv.balance_due


def test_metrics_open_ar_restored_after_payment_reversal(db):
    """Cross-service: paying an invoice drops it out of open_ar; REVERSING that
    payment must put the full balance back. If invoice.amount_paid ever stopped
    excluding reversed allocations, open_ar would silently under-report A/R."""
    cust = _customer(db)
    inv = _simple_open_invoice(db, cust.id, 300.0)
    svc = PaymentService(db, None)   # current_user_id=None → system event, REVERSE allowed

    pmt = svc.record_payment(cust.id, 300.0, PaymentMethod.CHECK, {}, invoice_ids=[inv.id])
    metrics = CustomerMetricsService(db)
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.PAID
    assert metrics.open_ar(cust.id) == 0.0
    assert metrics.metrics_for(cust)["gross_invoiced"] == 300.0

    svc.reverse_payment(pmt.id, "bounced")
    db.expire_all()
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.OPEN
    m = metrics.metrics_for(cust)
    assert m["open_ar"] == 300.0, "reversed payment must restore open AR"
    assert m["gross_invoiced"] == 300.0, "reversal must not change gross invoiced"
    assert m["lifetime_sales"] == 300.0


def test_metrics_open_ar_reflects_partial_payment(db):
    cust = _customer(db)
    inv = _simple_open_invoice(db, cust.id, 300.0)
    PaymentService(db, None).record_payment(
        cust.id, 120.0, PaymentMethod.CASH, {}, invoice_ids=[inv.id])
    db.expire_all()
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().status == InvoiceStatus.PARTIAL
    m = CustomerMetricsService(db).metrics_for(cust)
    assert m["open_ar"] == 180.0          # 300 - 120 still owed
    assert m["gross_invoiced"] == 300.0   # gross is unaffected by payment


# ════════════════════════════════════════════════════════════════════════════
# FLAGS — set_flags FULL replace (clears canonical too)
# ════════════════════════════════════════════════════════════════════════════

def test_set_flags_full_replace_turns_off_canonical_and_stored(db):
    """set_flags is the SOLE-SOURCE writer: any flag absent from the list is
    turned OFF, including the canonical TAX_EXEMPT / TEXT_PREFERRED columns.
    (Contrast set_stored_flags, which leaves those alone — guarded by Backend.)"""
    cust = _customer(db, is_tax_exempt=True,
                     preferred_contact_method=PreferredContactMethod.TEXT)
    svc = CustomerService(db)
    svc.set_flag(cust, CustomerFlag.REQUIRES_PO, True)
    db.commit()

    svc.set_flags(cust, [CustomerFlag.CREDIT_HOLD])   # CREDIT_HOLD is now the ONLY flag
    db.commit()

    flags = svc.flags_for(cust)
    assert flags == [CustomerFlag.CREDIT_HOLD]
    assert cust.is_tax_exempt is False                         # canonical cleared
    assert cust.preferred_contact_method == PreferredContactMethod.PHONE  # TEXT cleared
    assert CustomerFlag.REQUIRES_PO not in flags               # stored cleared


# ════════════════════════════════════════════════════════════════════════════
# CREDIT — composite warn + exact boundary
# ════════════════════════════════════════════════════════════════════════════

def test_credit_status_composite_hold_and_over_limit(db):
    """When a customer is BOTH on hold AND over limit, the single message must
    carry both reasons (the UI shows one banner)."""
    cust = _customer(db, credit_limit=1000.0)
    _simple_open_invoice(db, cust.id, 800.0)
    svc = CustomerService(db)
    svc.set_flag(cust, CustomerFlag.CREDIT_HOLD, True)
    db.commit()

    status = svc.credit_status(cust, prospective_amount=300.0)  # 800+300 > 1000
    assert status["on_hold"] is True
    assert status["over_limit"] is True
    assert status["warn"] is True
    assert "Credit Hold" in status["message"]
    assert "credit limit" in status["message"]


def test_would_exceed_credit_exact_boundary(db):
    """Landing exactly on the limit is NOT exceeding it (off-by-one / float guard)."""
    cust = _customer(db, credit_limit=1000.0)
    _simple_open_invoice(db, cust.id, 600.0)
    svc = CustomerService(db)
    assert svc.would_exceed_credit(cust, 400.0) is False     # 600+400 == 1000, at limit
    assert svc.would_exceed_credit(cust, 400.01) is True     # one cent over


# ════════════════════════════════════════════════════════════════════════════
# LOST SALES — mixed product / non-product lines
# ════════════════════════════════════════════════════════════════════════════

def test_mark_lost_logs_only_product_lines_in_mixed_quote(db):
    """A quote mixing a PRODUCT line with a non-product (freight) line must log
    exactly one LostSaleLog — for the product line only — so freight/labor never
    pollutes product-level lost-sale analytics."""
    cust = _customer(db)
    prod = Product(sku=f"GAP-{next(_seq)}", title="Turbo", cost=10.0)
    db.add(prod); db.commit(); db.refresh(prod)

    q = Quote(quote_number=f"Q-GAP-{next(_seq):05d}", customer_id=cust.id,
              status=QuoteStatus.SENT, outcome=QuoteOutcome.PENDING)
    q.lines.append(QuoteLine(line_type=LineType.PRODUCT, product_id=prod.id,
                             qty=1, unit_price=500.0, description="turbo"))
    q.lines.append(QuoteLine(line_type=LineType.FREIGHT, product_id=None,
                             qty=1, unit_price=75.0, description="freight"))
    db.add(q); db.commit(); db.refresh(q)

    QuoteService(db, None).mark_lost(q.id, LostReason.PRICE)

    logs = db.query(LostSaleLog).filter(LostSaleLog.quote_id == q.id).all()
    assert len(logs) == 1
    assert logs[0].product_id == prod.id
    assert logs[0].reason == LostReason.PRICE
