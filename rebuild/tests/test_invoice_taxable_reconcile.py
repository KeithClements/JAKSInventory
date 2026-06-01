"""
tests/test_invoice_taxable_reconcile.py
=======================================
Hardening seam: InvoiceService.finalise reconciles the invoice-level
``is_taxable`` flag with the frozen per-line taxation so the raw column can never
contradict the lines for any downstream consumer (print view, finalized label,
reports/exports).

Root cause it closes: an invoice taxed per-line but carrying ``is_taxable=False``
on the header made the print view and the finalized tax label lie. The totals
engine derives display flags from ``tax_amount``, but this fixes it at the source.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import app.database as _appdb
from app.main import app
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_UID = 1  # admin, seeded by startup hook


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


def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name="Tax Reconcile Co", contact_name="QA",
                 email="tax@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def test_finalise_with_taxable_lines_leaves_is_taxable_true(db):
    """Finalising an invoice whose line is taxable must leave is_taxable=True,
    even when the header column was seeded False (the divergence we're closing)."""
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    cust = _customer(db)
    inv = Invoice(
        invoice_number="INV-TAXREC-1",
        customer_id=cust.id,
        status=InvoiceStatus.DRAFT,
        is_taxable=False,            # deliberately wrong header value
        tax_rate=7.0,
        tax_rate_snapshot=7.0,       # a real frozen rate
        tax_exempt_snapshot=False,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Taxable part",
        qty=1,
        unit_price=100.0,
        unit_cost=60.0,
        is_taxable=True,             # the line IS taxable
    ))
    db.commit()

    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()

    inv_r = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv_r.is_taxable is True, (
        "finalise must reconcile is_taxable to True when any line is taxable; "
        f"got {inv_r.is_taxable!r}"
    )
    # And the per-line tax actually froze (sanity: $100 * 7% = $7.00)
    assert inv_r.lines[0].tax_amount == 7.00
