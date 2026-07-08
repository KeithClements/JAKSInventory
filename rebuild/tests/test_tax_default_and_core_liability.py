"""
tests/test_tax_default_and_core_liability.py
============================================
Lane-B money-path fixes from the headed-browser QA report.

1. Taxable customers' new documents DEFAULT to taxable (not "Exempt").
   The document's taxable flag now follows the customer's ``is_tax_exempt`` ALONE
   — a non-exempt customer's quote / sales order / invoice defaults taxable even
   when the configured rate is 0 ("0% — no rate set"), and an exempt customer's
   document defaults exempt. (Previously the flag was gated on ``tax_rate > 0`` as
   well, so a taxable customer with a 0 rate was mislabeled "Exempt".)

2. D-1 authority is preserved: explicitly setting ``is_taxable=False`` still zeroes
   tax everywhere, including through finalize. The new default must not regress it.

3. Core Liability on a DRAFT invoice that carries a priced core line is non-zero.
   CoreCharge rows only exist after finalize, so the §5.8 Invoice Intelligence panel
   read $0.00 on a draft with a $250 core line. The metric now falls back to the
   invoice's own CORE_CHARGE lines before finalize.

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_tax_default_and_core_liability.py -q
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Patch BEFORE any app.* imports ──────────────────────────────────────────
import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

import pytest

from app.constants import InvoiceStatus, LineType, SOPaymentMode, UserRole
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.product import Product
from app.models.user import User
from app.services.invoice_service import InvoiceService
from app.services.invoice_metrics_service import InvoiceMetricsService
from app.services.quote_service import QuoteService
from app.services.sales_order_service import SalesOrderService

_counter = itertools.count(1)
_UID = 1


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin_tax",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Builders ────────────────────────────────────────────────────────────────

def _customer(db, *, is_tax_exempt: bool, tax_rate: float) -> Customer:
    n = next(_counter)
    c = Customer(
        company_name=f"TaxCo-{n}", is_active=True,
        tax_rate=tax_rate, is_tax_exempt=is_tax_exempt,
    )
    db.add(c); db.commit()
    return c


def _product(db, *, price: float = 100.0, has_core: bool = False,
             cust_core: float = 0.0, vend_core: float = 0.0) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"TAX-{n:06d}", title=f"Tax Widget {n}",
        qty_on_hand=100, qty_committed=0, cost=40.0, price_override=price,
        has_core=has_core, customer_core_charge=cust_core, vendor_core_charge=vend_core,
    )
    db.add(p); db.commit()
    return p


def _quote(db, customer_id: int):
    return QuoteService(db, _UID).create_quote(customer_id, {
        "lines": [{"product_id": None, "line_type": LineType.PRODUCT,
                   "description": "part", "qty": 1, "unit_price": 1000.0,
                   "unit_cost": 600.0, "discount_pct": 0.0}]})


# ══════════════════════════════════════════════════════════════════════════════
# Task 1 — taxable / exempt DEFAULTS at document creation
# ══════════════════════════════════════════════════════════════════════════════

# ── Quote ─────────────────────────────────────────────────────────────────────

def test_quote_taxable_customer_defaults_taxable_even_with_zero_rate(db):
    """A taxable customer whose rate is still 0 (system-default rate) gets a quote
    that DEFAULTS taxable — not "Exempt". This is the exact QA repro."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    q = _quote(db, cust.id)
    db.refresh(q)
    assert q.is_taxable is True


def test_quote_taxable_customer_with_rate_defaults_taxable(db):
    cust = _customer(db, is_tax_exempt=False, tax_rate=8.25)
    q = _quote(db, cust.id)
    db.refresh(q)
    assert q.is_taxable is True
    assert q.tax_rate_snapshot == pytest.approx(8.25)


def test_quote_exempt_customer_defaults_exempt(db):
    cust = _customer(db, is_tax_exempt=True, tax_rate=0.0)
    q = _quote(db, cust.id)
    db.refresh(q)
    assert q.is_taxable is False
    assert q.tax_amount == 0.0


# ── Invoice (workspace draft) ─────────────────────────────────────────────────

def test_invoice_draft_taxable_customer_defaults_taxable_even_with_zero_rate(db):
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    inv = InvoiceService(db, _UID).create_draft(cust.id)
    db.refresh(inv)
    assert inv.is_taxable is True
    assert inv.tax_exempt_snapshot is False


def test_invoice_draft_exempt_customer_defaults_exempt(db):
    cust = _customer(db, is_tax_exempt=True, tax_rate=0.0)
    inv = InvoiceService(db, _UID).create_draft(cust.id)
    db.refresh(inv)
    assert inv.is_taxable is False
    assert inv.tax_exempt_snapshot is True


# ── Invoice (legacy bulk create) ──────────────────────────────────────────────

def test_invoice_create_taxable_customer_defaults_taxable_with_zero_rate(db):
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    inv = InvoiceService(db, _UID).create_invoice(cust.id, data={}, lines=[])
    db.refresh(inv)
    assert inv.is_taxable is True


def test_invoice_create_exempt_customer_defaults_exempt(db):
    cust = _customer(db, is_tax_exempt=True, tax_rate=0.0)
    inv = InvoiceService(db, _UID).create_invoice(cust.id, data={}, lines=[])
    db.refresh(inv)
    assert inv.is_taxable is False


def test_invoice_create_explicit_flag_override_wins(db):
    """A caller-supplied is_taxable override still wins (quote→invoice carry, imports)."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=8.0)
    inv = InvoiceService(db, _UID).create_invoice(
        cust.id, data={"is_taxable": False}, lines=[])
    db.refresh(inv)
    assert inv.is_taxable is False


# ── Sales Order ───────────────────────────────────────────────────────────────

def test_so_taxable_customer_defaults_taxable_even_with_zero_rate(db):
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    so = SalesOrderService(db, _UID).create_sales_order(
        cust.id, payment_mode=SOPaymentMode.NONE, data={"lines": []})
    db.refresh(so)
    assert so.is_taxable is True


def test_so_exempt_customer_defaults_exempt(db):
    cust = _customer(db, is_tax_exempt=True, tax_rate=0.0)
    so = SalesOrderService(db, _UID).create_sales_order(
        cust.id, payment_mode=SOPaymentMode.NONE, data={"lines": []})
    db.refresh(so)
    assert so.is_taxable is False


# ══════════════════════════════════════════════════════════════════════════════
# Task 1b — D-1 authority must not regress
# ══════════════════════════════════════════════════════════════════════════════

def test_d1_uncheck_taxable_still_zeroes_tax_through_finalize(db):
    """Explicitly unchecking "Taxable" zeroes tax on every surface and survives
    finalize — the new taxable-default must not break D-1."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=8.0)
    prod = _product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)              # seeded taxable (non-exempt customer)
    svc.add_line(inv.id, prod.id, {"qty": 3})    # parts 300
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.is_taxable is True
    assert inv.tax_amount == 24.00               # 300 * 8%

    svc.update_header(inv.id, {"is_taxable": False})
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.is_taxable is False
    assert inv.tax_amount == 0.0
    assert svc.calculate_totals(inv.id)["tax_amount"] == 0.0

    svc.finalise(inv.id)
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.status == InvoiceStatus.OPEN
    assert inv.is_taxable is False
    assert inv.tax_amount == 0.0
    assert inv.total == 300.00


def test_taxable_zero_rate_invoice_finalizes_at_zero_tax(db):
    """A taxable customer with a 0 rate produces a taxable draft that FINALIZES
    cleanly at $0 tax (no "marked taxable but no rate" hard block) — the prior
    validation would have wedged every taxable customer whose rate is still 0."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    prod = _product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 2})    # parts 200
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.is_taxable is True                 # taxable, but no rate yet

    # No hard-block error from validation.
    assert svc.validate_for_finalise(inv.id) == []

    svc.finalise(inv.id)
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    assert inv.status == InvoiceStatus.OPEN
    assert inv.tax_amount == 0.0
    assert inv.total == 200.00


# ══════════════════════════════════════════════════════════════════════════════
# Task 3 — Core Liability on a DRAFT with a priced core line is non-zero
# ══════════════════════════════════════════════════════════════════════════════

def test_draft_core_liability_reflects_core_line(db):
    """A DRAFT invoice carrying a $250 core line reports Core Liability $250.00 in
    the §5.8 panel — before finalize creates any CoreCharge row."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    prod = _product(db, price=500.0, has_core=True, cust_core=250.0, vend_core=180.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 1})    # adds product + auto $250 core line
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    # Sanity: the draft actually carries a CORE_CHARGE line at $250.
    core_lines = [ln for ln in inv.lines if ln.line_type == LineType.CORE_CHARGE]
    assert len(core_lines) == 1
    assert core_lines[0].line_total == 250.0

    m = InvoiceMetricsService(db).intelligence_for(inv)
    assert m["core_liability"] == 250.0, (
        f"draft core liability must reflect the $250 core line, got {m['core_liability']}"
    )


def test_draft_core_liability_zero_without_core_line(db):
    """No core line on a draft → core liability stays $0.00 (no false positive)."""
    cust = _customer(db, is_tax_exempt=False, tax_rate=0.0)
    prod = _product(db, price=100.0)  # no core
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 1})
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    m = InvoiceMetricsService(db).intelligence_for(inv)
    assert m["core_liability"] == 0.0
