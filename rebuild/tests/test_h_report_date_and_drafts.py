"""
tests/test_h_report_date_and_drafts.py
======================================
Two bookkeeping-accuracy fixes (FULL_ERP_REVIEW_2026-07-04 HIGH items):

1. A/R aging must EXCLUDE draft invoices. A DRAFT carries its full total as
   balance_due (nothing zeroes it except VOID), so including it inflated A/R and
   the dashboard and disagreed with StatementService.
2. Sales-tax (and the sales reports) must bucket by the finalize date (locked_at),
   which is the QBO TxnDate — not created_at. An invoice drafted in one month and
   finalized in the next must land in the FINALIZE month so the ERP ties to QBO.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_h_report_date_and_drafts.py -q
"""
from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.services.report_service import ReportService
from tests.conftest import activate, fresh_engine


@pytest.fixture()
def db():
    activate(fresh_engine())
    from app.database import SessionLocal
    from app.routers.settings import seed_settings
    s = SessionLocal()
    seed_settings(s)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _customer(db) -> Customer:
    c = Customer(company_name=f"Co {uuid.uuid4().hex[:8]}", contact_name="")
    db.add(c)
    db.flush()
    return c


def _invoice(db, customer, *, status, created, locked=None, price=100.0, tax=0.0):
    inv = Invoice(
        invoice_number=f"INV-{uuid.uuid4().hex[:12]}",
        customer_id=customer.id,
        customer=customer,
        status=status,
        created_at=created,
        locked_at=locked,
        due_date=created.date() if hasattr(created, "date") else created,
    )
    db.add(inv)
    db.flush()
    line = InvoiceLine(
        invoice_id=inv.id, description="part", qty=1,
        unit_price=price, unit_cost=0.0, discount_pct=0.0,
        line_type="product", tax_amount=tax,
    )
    db.add(line)
    db.flush()
    return inv


# ── 1. drafts out of A/R aging ─────────────────────────────────────────────────

def test_ar_aging_excludes_draft_invoices(db):
    c = _customer(db)
    _invoice(db, c, status="open", created=datetime(2026, 6, 1), locked=datetime(2026, 6, 1), price=250.0)
    _invoice(db, c, status="draft", created=datetime(2026, 6, 2), price=999.99)  # must NOT count
    db.commit()

    aging = ReportService(db, current_user_id=1).get_ar_aging()
    # Only the $250 open invoice — the $999.99 draft is excluded.
    assert round(aging["totals"]["total"], 2) == 250.0, (
        f"draft invoice leaked into A/R aging: {aging['totals']['total']}")


# ── 2. sales tax buckets by finalize date (locked_at), not created_at ──────────

def test_sales_tax_buckets_by_finalize_date(db):
    c = _customer(db)
    # Drafted June 30, FINALIZED July 2 — QBO would post it with TxnDate July 2.
    _invoice(db, c, status="open",
             created=datetime(2026, 6, 30, 18, 0),
             locked=datetime(2026, 7, 2, 9, 0),
             price=100.0, tax=8.25)
    db.commit()

    svc = ReportService(db, current_user_id=1)
    july = svc.get_sales_tax_collected(date(2026, 7, 1), date(2026, 7, 31))
    june = svc.get_sales_tax_collected(date(2026, 6, 1), date(2026, 6, 30))

    assert round(july["totals"]["tax_collected"], 2) == 8.25, (
        "invoice finalized in July must appear in the July tax period")
    assert round(june["totals"]["tax_collected"], 2) == 0.0, (
        "invoice finalized in July must NOT appear in June (that was the QBO drift)")
