"""
tests/test_invoice_discount.py
==============================
Regression tests pinning ONE definition of the invoice-level (header) discount.

Definition locked in here
-------------------------
``invoice.discount_pct`` is an INVOICE-LEVEL discount applied EXACTLY ONCE to the
parts subtotal (PRODUCT / MISC / WARRANTY lines). It is independent of any
per-line ``line.discount_pct`` (which is applied inside ``line_total``).  The
model properties (``subtotal`` / ``discount_amount`` / ``total`` / ``balance_due``),
``InvoiceService.calculate_totals``, and the print/pdf ``discount_amount`` must
all agree on this single definition.

Bugs these tests guard against (all real before the fix)
--------------------------------------------------------
1. ``Invoice.total`` / ``.balance_due`` ignored ``invoice.discount_pct`` entirely,
   so the List "Total" column, the row Preview panel, and the printed/PDF invoice
   showed a HIGHER (un-discounted) total than the workspace totals panel
   (which uses ``calculate_totals``). The customer-facing document was wrong.
2. The print/pdf routes computed ``discount_amount = gross - inv.subtotal`` where
   ``gross == inv.subtotal`` (both ignored the header discount), so the printed
   "Discount" line was ALWAYS $0.00.
3. ``add_line`` (and ``change_customer``) copied ``invoice.discount_pct`` onto each
   new product line's ``discount_pct``. Combined with ``calculate_totals`` applying
   the invoice discount again on top, a customer's standing discount was applied
   TWICE (e.g. a 10% customer discount became ~19% off).

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_invoice_discount.py -v

Uses an in-memory SQLite DB isolated from the live data/jaks.db. The module-level
patch of app.database must happen before any app imports (see test_workflow_series3).
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
from fastapi.testclient import TestClient

from app.constants import InvoiceStatus, LineType, UserRole
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.product import Product
from app.models.user import User
from app.services.invoice_service import InvoiceService

from app.main import app as _fastapi_app

_client = TestClient(_fastapi_app, raise_server_exceptions=False)

_counter = itertools.count(1)
_UID = 1


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    if not s.query(User).filter(User.id == 1).first():
        s.add(User(id=1, name="Test Admin", username="admin_disc",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


# ── Builders ────────────────────────────────────────────────────────────────

def _make_customer(db, discount_pct: float = 0.0, tax_rate: float = 0.0) -> Customer:
    n = next(_counter)
    c = Customer(
        company_name=f"DiscCo-{n}",
        is_active=True,
        discount_pct=discount_pct,
        tax_rate=tax_rate,
        is_tax_exempt=(tax_rate == 0.0),
    )
    db.add(c)
    db.commit()
    return c


def _make_product(db, price: float = 100.0) -> Product:
    n = next(_counter)
    p = Product(
        sku=f"DISC-{n:06d}",
        title=f"Disc Widget {n}",
        qty_on_hand=100,
        qty_committed=0,
        cost=40.0,
        price_override=price,   # selling_price is computed; price_override pins it
    )
    db.add(p)
    db.commit()
    return p


def _totals(db, invoice_id: int) -> dict:
    """Fresh service + fresh read so we compare against committed state."""
    return InvoiceService(db, _UID).calculate_totals(invoice_id)


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE required regression: model.total == calculate_totals()["total"]
#    for an invoice with a header-level discount and lines with NO line discount.
# ══════════════════════════════════════════════════════════════════════════════

def test_model_total_matches_calculate_totals_with_header_discount(db):
    """Header discount set AFTER lines exist (the reported repro).

    Customer has no standing discount, so add_line copies nothing. We then raise
    the header Disc % via update_header (which does NOT propagate to lines), so
    the lines keep discount_pct == 0 — exactly the reported state.
    """
    cust = _make_customer(db, discount_pct=0.0)          # non-taxable, no standing disc
    prod = _make_product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 2})            # line_total = 200.00, line disc 0
    svc.add_line(inv.id, prod.id, {"qty": 1})            # line_total = 100.00, line disc 0

    # Raise the header discount to 10% AFTER lines exist.
    svc.update_header(inv.id, {"discount_pct": 10.0})
    db.expire_all()

    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    # Every line still has NO per-line discount.
    assert all(ln.discount_pct == 0.0 for ln in inv.lines)

    totals = _totals(db, inv.id)

    # The crux: model total must equal the workspace (calculate_totals) total.
    assert inv.total == totals["total"]
    assert inv.subtotal == 300.00            # pre-discount parts subtotal
    assert inv.discount_amount == 30.00      # 10% of 300, applied ONCE
    assert inv.total == 270.00               # 300 - 30 (non-taxable)
    assert inv.balance_due == 270.00


def test_model_matches_calculate_totals_with_header_discount_and_tax(db):
    """Same agreement must hold for a taxable draft invoice."""
    cust = _make_customer(db, discount_pct=0.0, tax_rate=8.0)
    prod = _make_product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 4})            # parts = 400.00
    svc.update_header(inv.id, {"discount_pct": 25.0, "is_taxable": True, "tax_rate": 8.0})
    db.expire_all()

    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()
    totals = _totals(db, inv.id)

    assert inv.subtotal == 400.00
    assert inv.discount_amount == 100.00     # 25% of 400
    # Tax is charged on the DISCOUNTED parts base (300.00 * 8% = 24.00).
    assert inv.tax_amount == 24.00
    assert inv.total == 324.00               # 300 + 24
    # Model and workspace agree on every customer-facing number.
    assert inv.total == totals["total"]
    assert inv.tax_amount == totals["tax_amount"]
    assert inv.discount_amount == totals["discount_amount"]
    assert inv.balance_due == totals["balance_due"]


# ══════════════════════════════════════════════════════════════════════════════
# 2. The double-discount guard: a customer's standing discount must apply ONCE.
# ══════════════════════════════════════════════════════════════════════════════

def test_add_line_does_not_double_apply_customer_discount(db):
    """Customer has a 10% standing discount.

    create_draft seeds invoice.discount_pct = 10. Before the fix, add_line ALSO
    copied 10 onto the new line, and calculate_totals applied 10 again on top —
    a ~19% effective discount. After the fix the discount lives ONLY at the
    invoice level and is applied exactly once (10%).
    """
    cust = _make_customer(db, discount_pct=10.0)
    prod = _make_product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    assert inv.discount_pct == 10.0          # seeded from customer

    line = svc.add_line(inv.id, prod.id, {"qty": 1})
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    # The new line must NOT carry the header discount (no per-line copy).
    assert line.discount_pct == 0.0
    assert inv.subtotal == 100.00            # gross, line not pre-discounted
    assert inv.discount_amount == 10.00      # 10% ONCE
    assert inv.total == 90.00                # not 81.00 (double) and not 100.00 (ignored)

    totals = _totals(db, inv.id)
    assert inv.total == totals["total"]
    assert totals["discount_amount"] == 10.00


def test_change_customer_does_not_push_discount_into_lines(db):
    """Reassigning to a 15% customer (recalc) sets the header discount but must
    not also bake it into the lines (which would double-count)."""
    cust_a = _make_customer(db, discount_pct=0.0)
    cust_b = _make_customer(db, discount_pct=15.0)
    prod = _make_product(db, price=200.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust_a.id)
    svc.add_line(inv.id, prod.id, {"qty": 1})            # line disc 0

    svc.change_customer(inv.id, cust_b.id, recalc_pricing=True)
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    assert inv.discount_pct == 15.0
    assert all(ln.discount_pct == 0.0 for ln in inv.lines)   # not pushed into lines
    assert inv.subtotal == 200.00
    assert inv.discount_amount == 30.00                      # 15% ONCE
    assert inv.total == 170.00
    assert inv.total == _totals(db, inv.id)["total"]


# ══════════════════════════════════════════════════════════════════════════════
# 3. Independent layers: a per-line discount AND a header discount stack cleanly,
#    and the two engines still agree.
# ══════════════════════════════════════════════════════════════════════════════

def test_line_and_header_discount_stack_and_agree(db):
    cust = _make_customer(db, discount_pct=0.0)
    prod = _make_product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    line = svc.add_line(inv.id, prod.id, {"qty": 1})
    svc.update_line(line.id, {"discount_pct": 20.0})     # per-line 20% markdown
    svc.update_header(inv.id, {"discount_pct": 10.0})    # header 10% on top
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    # line_total already reflects the per-line 20% -> 80.00
    assert inv.subtotal == 80.00
    # header 10% applies to the (already line-discounted) parts subtotal
    assert inv.discount_amount == 8.00       # 10% of 80
    assert inv.total == 72.00
    assert inv.total == _totals(db, inv.id)["total"]


# ══════════════════════════════════════════════════════════════════════════════
# 4. The customer-facing document: print route honors the header discount.
# ══════════════════════════════════════════════════════════════════════════════

def test_print_route_applies_header_discount(db):
    cust = _make_customer(db, discount_pct=0.0)
    prod = _make_product(db, price=100.0)
    svc = InvoiceService(db, _UID)

    inv = svc.create_draft(cust.id)
    svc.add_line(inv.id, prod.id, {"qty": 3})            # parts 300.00
    svc.update_header(inv.id, {"discount_pct": 10.0})
    db.commit()
    inv_id = inv.id

    resp = _client.get(f"/invoices/{inv_id}/print")
    assert resp.status_code == 200
    body = resp.text
    # Non-zero discount line and the discounted grand total are both present.
    assert "30.00" in body          # discount_amount (10% of 300)
    assert "270.00" in body         # discounted total
