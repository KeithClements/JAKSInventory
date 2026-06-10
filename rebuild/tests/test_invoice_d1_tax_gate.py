"""
tests/test_invoice_d1_tax_gate.py
=================================
D-1 (Suite-D cert blocker): unchecking "Taxable" on the invoice workspace zeroes
Sales Tax on EVERY surface and survives finalize; re-checking restores it.

Repro: INV-2026-0021 showed $372.27 when "Taxable" was unchecked; it should have
shown $350.25 (the $22.02 tax should have dropped out). Root cause: the totals
engine's draft path re-enabled tax via an `or tax_rate_display > 0` fallback that
defeated an explicit uncheck, and finalise froze per-line tax from the per-line
flags (which the toggle does not clear) while reconciling the header UP.

These tests exercise the real workspace service flow
(``create_draft`` -> ``add_line`` -> ``update_header``). Because BOTH the Invoice
model money properties (List "Total" / row Preview / print/PDF) and
``InvoiceService.calculate_totals`` (the workspace totals panel) delegate to the
ONE engine (``app.invoice_totals.compute_invoice_totals``), asserting both the
model figure and the service figure proves all four customer-facing surfaces at
once.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_invoice_d1_tax_gate.py -v
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Patch BEFORE any app.* imports ──────────────────────────────────────────
import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

# ── App imports (safe after patch) ────────────────────────────────────────────
import pytest

from app.constants import InvoiceStatus, UserRole
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.user import User
from app.services.invoice_service import InvoiceService

_counter = itertools.count(1)
_UID = 1


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin_d1",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Builders ────────────────────────────────────────────────────────────────

def _taxable_customer(db, tax_rate: float = 8.0) -> Customer:
    n = next(_counter)
    c = Customer(
        company_name=f"D1Co-{n}", is_active=True,
        tax_rate=tax_rate, is_tax_exempt=False,
    )
    db.add(c); db.commit()
    return c


def _product(db, price: float = 100.0) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"D1-{n:06d}", title=f"D1 Widget {n}",
        qty_on_hand=100, qty_committed=0, cost=40.0, price_override=price,
    )
    db.add(p); db.commit()
    return p


def _totals(db, invoice_id: int) -> dict:
    """Workspace totals — fresh service + read against committed state."""
    return InvoiceService(db, _UID).calculate_totals(invoice_id)


# ══════════════════════════════════════════════════════════════════════════════

def test_uncheck_taxable_zeroes_tax_on_every_surface(db):
    """A taxable draft loses ALL Sales Tax the moment "Taxable" is unchecked —
    on the workspace (calculate_totals) AND the model properties that back the
    List total, the Preview panel, and the print/PDF."""
    cust = _taxable_customer(db, tax_rate=8.0)
    prod = _product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)              # is_taxable seeded True (8% customer)
    svc.add_line(inv.id, prod.id, {"qty": 3})    # parts = 300.00
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    # Baseline: taxable draft charges 300 * 8% = 24.00 on every surface.
    assert inv.is_taxable is True
    assert inv.tax_amount == 24.00
    assert _totals(db, inv.id)["tax_amount"] == 24.00
    assert inv.total == 324.00

    # The user unchecks "Taxable" (the workspace sends is_taxable=False).
    svc.update_header(inv.id, {"is_taxable": False})
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    t = _totals(db, inv.id)
    assert inv.is_taxable is False
    assert inv.tax_amount == 0.0, (
        f"model tax_amount must be 0 after uncheck (List/Preview/print); got {inv.tax_amount}"
    )
    assert t["tax_amount"] == 0.0, (
        f"workspace tax must be 0 after uncheck; got {t['tax_amount']}"
    )
    assert t["is_taxable_display"] is False
    assert inv.total == 300.00, "total must drop by exactly the removed tax"
    # Engine consistency: the model and the workspace agree on the headline total.
    assert inv.total == t["total"]


def test_recheck_taxable_restores_tax(db):
    """Re-checking "Taxable" restores the tax — the retained tax_rate / snapshot
    carries the rate, so the figure comes straight back."""
    cust = _taxable_customer(db, tax_rate=8.0)
    prod = _product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 3})       # parts 300

    svc.update_header(inv.id, {"is_taxable": False})  # off -> 0 tax
    db.expire_all()
    assert db.query(Invoice).get(inv.id).tax_amount == 0.0

    svc.update_header(inv.id, {"is_taxable": True})   # back on -> tax restored
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    assert inv.is_taxable is True
    assert inv.tax_amount == 24.00, "re-checking Taxable must restore the tax"
    assert _totals(db, inv.id)["tax_amount"] == 24.00


def test_uncheck_persists_through_finalize(db):
    """An unchecked header must keep Sales Tax at 0 AFTER finalize — the per-line
    freeze is gated on the header, and the header is not resurrected from the
    per-line is_taxable flags (which the toggle never cleared)."""
    cust = _taxable_customer(db, tax_rate=8.0)
    prod = _product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 3})
    svc.update_header(inv.id, {"is_taxable": False})

    svc.finalise(inv.id)
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    assert inv.status == InvoiceStatus.OPEN
    assert inv.is_taxable is False
    assert all(ln.tax_amount == 0.0 for ln in inv.lines)
    assert inv.tax_amount == 0.0
    assert inv.total == 300.00
