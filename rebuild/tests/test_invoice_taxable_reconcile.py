"""
tests/test_invoice_taxable_reconcile.py
=======================================
D-1: the invoice-level ``is_taxable`` header flag is the AUTHORITATIVE tax gate
at finalize time.

``InvoiceService.finalise``:
  * gates the per-line ``tax_amount`` freeze on the header flag, so a cleared
    header freezes 0 tax on every line even when the lines still carry
    ``is_taxable=True`` (the workspace "Taxable" toggle clears only the header,
    not each line); and
  * reconciles the header with ``invoice.is_taxable AND any(line.is_taxable)`` —
    it may only NARROW the flag (a taxable header with no taxable line becomes
    non-taxable), never RE-ENABLE a header the user cleared.

History / supersession
----------------------
The eac2ed8 hardening reconciled the header UP
(``is_taxable = any(line.is_taxable ...)``) so a per-line-taxed invoice carrying
a stale False header could not lie on the print view / finalized label. D-1
supersedes that DIRECTION: the divergence is now closed by HONORING the header
(lines freeze to 0 tax when the header is off), not by overwriting the header
from the lines. Both eliminate the divergence; D-1 keeps user intent
authoritative so unchecking "Taxable" zeroes tax everywhere and survives finalize.
``tax_rate_snapshot`` is left intact throughout so re-checking restores the rate.
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


def _draft(db, *, is_taxable, line_taxable, unit_price=100.0, number="INV-TAXREC"):
    """A committed DRAFT invoice with one product line and a real frozen rate."""
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine

    cust = _customer(db)
    inv = Invoice(
        invoice_number=number,
        customer_id=cust.id,
        status=InvoiceStatus.DRAFT,
        is_taxable=is_taxable,
        tax_rate=7.0,
        tax_rate_snapshot=7.0,        # a real frozen rate, always retained
        tax_exempt_snapshot=False,
    )
    db.add(inv); db.flush()
    db.add(InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Part",
        qty=1,
        unit_price=unit_price,
        unit_cost=60.0,
        is_taxable=line_taxable,
    ))
    db.commit()
    return inv.id


def test_finalise_taxable_invoice_freezes_tax_and_keeps_header(db):
    """Normal taxable invoice (header ON, line taxable): finalise freezes the
    per-line tax and the header stays True."""
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    inv_id = _draft(db, is_taxable=True, line_taxable=True, number="INV-TAXREC-OK")

    InvoiceService(db, _UID).finalise(inv_id)
    db.expire_all()

    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    assert inv.is_taxable is True
    assert inv.lines[0].tax_amount == 7.00          # $100 * 7%
    assert inv.tax_amount == 7.00


def test_finalise_respects_unchecked_header(db):
    """D-1 core: header is_taxable=False (user unchecked) but the line still
    carries is_taxable=True. Finalise must freeze 0 tax on the line AND leave the
    header False — tax must not resurrect from the per-line flags."""
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    inv_id = _draft(db, is_taxable=False, line_taxable=True, number="INV-TAXREC-OFF")

    InvoiceService(db, _UID).finalise(inv_id)
    db.expire_all()

    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    assert inv.is_taxable is False, (
        "an unchecked header must stay unchecked through finalize (D-1); the old "
        f"reconcile-up behavior would have set it True. got {inv.is_taxable!r}"
    )
    assert all(ln.tax_amount == 0.0 for ln in inv.lines), (
        "no line may freeze tax when the invoice-level header is unchecked"
    )
    assert inv.tax_amount == 0.0
    # The rate snapshot is retained so re-checking "Taxable" can restore the rate.
    assert inv.tax_rate_snapshot == 7.0


def test_finalise_narrows_header_when_no_taxable_line(db):
    """Header was True but no line is taxable (e.g. a non-taxable product / a
    cores-only invoice): finalise NARROWS the header to False so the raw column
    can never claim taxable while 0 tax is frozen. This preserves the residual
    eac2ed8 intent (the raw column cannot contradict the lines)."""
    from app.models.invoice import Invoice
    from app.services.invoice_service import InvoiceService

    inv_id = _draft(db, is_taxable=True, line_taxable=False, number="INV-TAXREC-NARROW")

    InvoiceService(db, _UID).finalise(inv_id)
    db.expire_all()

    inv = db.query(Invoice).filter(Invoice.id == inv_id).first()
    assert inv.is_taxable is False
    assert inv.tax_amount == 0.0
