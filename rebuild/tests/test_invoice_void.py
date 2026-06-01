"""
tests/test_invoice_void.py
==========================
Regression gate for voided-invoice money behaviour (TESTING_FEEDBACK §1.8).

Two assertions that must hold forever:
  1. balance_due == 0.0 on a voided invoice.
     Root cause that made this a gate: compute_invoice_totals had no VOID
     branch, so balance_due = total - 0 = total (no payments because void
     requires reversing them first). Fixed in invoice_totals.py by treating
     the full total as "settled" when status == VOID.

  2. Voided invoices are excluded from AR aging.
     Already enforced by report_service.py:117 but locked here so it cannot
     quietly regress as the query evolves.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_invoice_void.py -v
"""
from __future__ import annotations

import datetime
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _customer(db):
    from app.models.customer import Customer
    c = Customer(company_name="Void Test Co", contact_name="QA",
                 email="void@test.local")
    db.add(c); db.commit(); db.refresh(c)
    return c


def _open_invoice(db, customer_id: int, amount: float):
    """Create + finalise an OPEN invoice (no payments, no QBO id)."""
    from app.constants import InvoiceStatus, LineType
    from app.models.invoice import Invoice, InvoiceLine
    from app.services.invoice_service import InvoiceService

    inv = Invoice(
        invoice_number=f"INV-VOID-{id(customer_id)}",
        customer_id=customer_id,
        status=InvoiceStatus.DRAFT,
        tax_rate=0.0,
        tax_rate_snapshot=0.0,
        tax_exempt_snapshot=True,
    )
    db.add(inv); db.flush()
    line = InvoiceLine(
        invoice_id=inv.id,
        line_type=LineType.PRODUCT,
        description="Test part",
        qty=1,
        unit_price=amount,
        unit_cost=amount * 0.6,
        is_taxable=False,
    )
    db.add(line); db.commit()

    InvoiceService(db, _UID).finalise(inv.id)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


# ---------------------------------------------------------------------------
# 1. balance_due == 0 after void
# ---------------------------------------------------------------------------

def test_voided_invoice_balance_due_is_zero(db):
    """balance_due must report $0 after void — the invoice is cancelled."""
    from app.constants import InvoiceStatus
    from app.services.invoice_service import InvoiceService

    cust = _customer(db)
    inv = _open_invoice(db, cust.id, amount=200.00)

    assert inv.status == InvoiceStatus.OPEN
    assert inv.total == 200.00
    assert inv.balance_due == 200.00   # unpaid OPEN invoice owes full amount

    InvoiceService(db, _UID).void_invoice(inv.id, reason="test void")
    db.expire_all()

    from app.models.invoice import Invoice
    inv_r = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv_r.status == InvoiceStatus.VOID
    assert inv_r.balance_due == 0.0, (
        f"Voided invoice balance_due should be 0.0, got {inv_r.balance_due!r}. "
        "If this fails, compute_invoice_totals has no VOID branch."
    )


# ---------------------------------------------------------------------------
# 2. Voided invoice is excluded from AR aging
# ---------------------------------------------------------------------------

def test_voided_invoice_excluded_from_ar_aging(db):
    """A voided invoice must not appear in AR aging (balance_due excluded)."""
    from app.services.invoice_service import InvoiceService
    from app.services.report_service import ReportService

    cust = _customer(db)
    inv = _open_invoice(db, cust.id, amount=350.00)
    inv_id = inv.id

    # Before void — customer must appear in AR aging with balance_due == 350.
    aging_before = ReportService(db).get_ar_aging(as_of_date=datetime.date.today())
    row_before = next((r for r in aging_before["rows"] if r["customer_id"] == cust.id), None)
    assert row_before is not None, "OPEN invoice must show in AR aging before void"
    assert row_before["total"] == 350.00, (
        f"Expected AR aging total 350.0 before void; got {row_before['total']!r}"
    )

    InvoiceService(db, _UID).void_invoice(inv_id, reason="test void")
    db.expire_all()

    # After void — customer must NOT appear in AR aging (no unpaid balance).
    aging_after = ReportService(db).get_ar_aging(as_of_date=datetime.date.today())
    row_after = next((r for r in aging_after["rows"] if r["customer_id"] == cust.id), None)
    assert row_after is None or row_after["total"] == 0.0, (
        f"Voided invoice must be excluded from AR aging; "
        f"got total={row_after['total'] if row_after else 'N/A'!r}"
    )


# ---------------------------------------------------------------------------
# 3. Counter-case: un-voided (OPEN) invoice still has correct balance_due
# ---------------------------------------------------------------------------

def test_open_invoice_balance_due_unchanged(db):
    """Ensure the VOID branch does not accidentally affect OPEN invoices."""
    cust = _customer(db)
    inv = _open_invoice(db, cust.id, amount=150.00)

    assert inv.balance_due == 150.00, (
        "OPEN invoice balance_due should equal its total when unpaid; "
        f"got {inv.balance_due!r}"
    )
