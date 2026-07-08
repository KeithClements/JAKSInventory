"""
tests/test_discount_clamp.py
============================
Discount-percent clamp on the invoice/quote money paths.

calc_line_total is pure math: a negative discount_pct INFLATES the line total
(calc_line_total(100, 1, -50) == 150.0) and over-collects tax, and the invoice
then finalizes and LOCKS with the bad total. The services now:

  1. Reject discount_pct outside [0, 100] on every line/header write path
     (None/blank normalizes to 0.0) — InvoiceService and QuoteService both.
  2. Hard-block finalize when a bad value is already stored on a line or the
     invoice header (i.e. written around the service), with an error naming
     the offending line.
  3. app/routers/quotes.py catches the service ValueError instead of 500ing:
     header save → 303 with error= flash; line update → 400 HTML fragment;
     convert-to-so of a legacy poisoned LINE → 303 with error=. And a customer
     whose STORED standing discount is poisoned (written around the clamp) can
     still get a quote: POST /quotes/new creates it with header discount 0 and
     the add-line copy site sanitizes the poisoned default to 0
     (_safe_customer_discount treats out-of-range as UNSET).

Run:
    .venv\\Scripts\\python.exe -m pytest tests/test_discount_clamp.py -q
"""
import itertools
import pathlib
import sys
from urllib.parse import unquote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine

from app.models import __all_models__  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from app.constants import InvoiceStatus, QuoteOutcome, UserRole
from app.main import app
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.quote import Quote, QuoteLine
from app.models.user import User
from app.services.invoice_service import InvoiceService
from app.services.quote_service import QuoteService

_counter = itertools.count(1)
_UID = 1


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    activate(fresh_engine())
    s = _appdb.SessionLocal()
    # Real ADMIN row: finalise gates on Permission.FINALIZE_INVOICE, and seeded
    # User rows are always role-enforced even under JAKS_SKIP_AUTH.
    if not s.query(User).filter(User.id == _UID).first():
        s.add(User(id=_UID, name="Test Admin", username="admin_disc",
                   password_hash="[test-no-auth]", role=UserRole.ADMIN))
        s.commit()
    try:
        yield s
    finally:
        s.close()


def _customer(db) -> Customer:
    n = next(_counter)
    c = Customer(company_name=f"DiscCo-{n}", is_active=True)
    db.add(c)
    db.commit()
    return c


def _line(pct=..., **overrides) -> dict:
    data = {"description": "part", "qty": 1, "unit_price": 100.0}
    if pct is not ...:
        data["discount_pct"] = pct
    data.update(overrides)
    return data


# ── Invoice line add ──────────────────────────────────────────────────────────

def test_negative_discount_rejected_on_invoice_line_add(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.add_line(inv.id, None, _line(-50))
    db.rollback()
    assert db.query(InvoiceLine).filter(InvoiceLine.invoice_id == inv.id).count() == 0


def test_discount_over_100_rejected_on_invoice_line_add(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.add_line(inv.id, None, _line(150))
    db.rollback()
    assert db.query(InvoiceLine).filter(InvoiceLine.invoice_id == inv.id).count() == 0


def test_non_numeric_discount_rejected_on_invoice_line_add(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.add_line(inv.id, None, _line("half off"))
    db.rollback()


# ── Invoice line update ───────────────────────────────────────────────────────

def test_negative_discount_rejected_on_invoice_line_update(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    line = svc.add_line(inv.id, None, _line(10))
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_line(line.id, {"discount_pct": -1})
    db.rollback()
    db.expire_all()
    stored = db.query(InvoiceLine).filter(InvoiceLine.id == line.id).first()
    assert stored.discount_pct == 10.0  # rejected write left the line untouched


def test_discount_over_100_rejected_on_invoice_line_update(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    line = svc.add_line(inv.id, None, _line(10))
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_line(line.id, {"discount_pct": 150})
    db.rollback()
    db.expire_all()
    stored = db.query(InvoiceLine).filter(InvoiceLine.id == line.id).first()
    assert stored.discount_pct == 10.0


# ── Boundaries + blank normalization ──────────────────────────────────────────

def test_boundaries_0_and_100_accepted_on_invoice_lines(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    line0 = svc.add_line(inv.id, None, _line(0))
    line100 = svc.add_line(inv.id, None, _line(100))
    assert line0.discount_pct == 0.0
    assert line100.discount_pct == 100.0
    assert svc.update_line(line0.id, {"discount_pct": 100}).discount_pct == 100.0
    assert svc.update_line(line100.id, {"discount_pct": 0}).discount_pct == 0.0


def test_none_and_blank_discount_normalize_to_zero(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    assert svc.add_line(inv.id, None, _line(None)).discount_pct == 0.0
    assert svc.add_line(inv.id, None, _line("  ")).discount_pct == 0.0
    line = svc.add_line(inv.id, None, _line(25))
    assert svc.update_line(line.id, {"discount_pct": ""}).discount_pct == 0.0


# ── Invoice header ────────────────────────────────────────────────────────────

def test_invoice_header_discount_rejected_outside_range(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_header(inv.id, {"discount_pct": -5})
    db.rollback()
    with pytest.raises(ValueError, match="between 0 and 100"):
        svc.update_header(inv.id, {"discount_pct": 150})
    db.rollback()
    svc.update_header(inv.id, {"discount_pct": 12.5})
    db.expire_all()
    assert db.query(Invoice).filter(Invoice.id == inv.id).first().discount_pct == 12.5


def test_create_invoice_header_discount_rejected(db):
    cust = _customer(db)
    with pytest.raises(ValueError, match="between 0 and 100"):
        InvoiceService(db, _UID).create_invoice(cust.id, data={"discount_pct": -10})
    db.rollback()


# ── Finalize hard block on bad STORED values ──────────────────────────────────

def test_bad_stored_line_discount_blocks_finalize(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    line = svc.add_line(inv.id, None, _line(description="inflated part"))
    # Written around the service (legacy row / hand edit) — the write-path
    # validator never saw it.
    line.discount_pct = -50.0
    db.commit()

    errors = svc.validate_for_finalise(inv.id)
    bad = [e for e in errors if "discount must be between 0 and 100" in e]
    assert bad, f"expected a discount hard-block, got: {errors}"
    assert any("Line 1" in e and "inflated part" in e for e in bad), (
        f"error must name the offending line: {bad}"
    )

    with pytest.raises(ValueError, match="discount must be between 0 and 100"):
        svc.finalise(inv.id)
    db.rollback()
    db.expire_all()
    assert (
        db.query(Invoice).filter(Invoice.id == inv.id).first().status
        == InvoiceStatus.DRAFT
    )  # never locked


def test_bad_stored_header_discount_blocks_finalize(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    svc.add_line(inv.id, None, _line())
    stored = db.query(Invoice).filter(Invoice.id == inv.id).first()
    stored.discount_pct = 150.0
    db.commit()

    errors = svc.validate_for_finalise(inv.id)
    assert any("Invoice discount must be between 0 and 100" in e for e in errors)

    with pytest.raises(ValueError, match="Invoice discount must be between 0 and 100"):
        svc.finalise(inv.id)
    db.rollback()


def test_good_discounts_still_finalize(db):
    svc = InvoiceService(db, _UID)
    inv = svc.create_draft(_customer(db).id)
    svc.add_line(inv.id, None, _line(10))
    assert svc.validate_for_finalise(inv.id) == []
    svc.finalise(inv.id)
    db.expire_all()
    assert (
        db.query(Invoice).filter(Invoice.id == inv.id).first().status
        == InvoiceStatus.OPEN
    )


# ── Quote-side mirror ─────────────────────────────────────────────────────────

def test_quote_line_add_rejects_out_of_range_discount(db):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {})
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.add_line(quote.id, None, _line(-20))
    db.rollback()
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.add_line(quote.id, None, _line(101))
    db.rollback()
    lines = qsvc.add_line(quote.id, None, _line(100))
    assert lines[0].discount_pct == 100.0


def test_quote_line_update_rejects_out_of_range_discount(db):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {})
    line = qsvc.add_line(quote.id, None, _line(5))[0]
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.update_line(line.id, {"discount_pct": -0.01})
    db.rollback()
    updated, _cascaded = qsvc.update_line(line.id, {"discount_pct": 0})
    assert updated.discount_pct == 0.0


def test_quote_update_line_discount_rejects_out_of_range(db):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {})
    line = qsvc.add_line(quote.id, None, _line(0))[0]
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.update_line_discount(line.id, -5)
    db.rollback()
    assert qsvc.update_line_discount(line.id, 100).discount_pct == 100.0


def test_quote_header_and_create_discount_rejected(db):
    cust = _customer(db)
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(cust.id, {})
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.update_header(quote.id, {"discount_pct": 200})
    db.rollback()
    with pytest.raises(ValueError, match="between 0 and 100"):
        qsvc.create_quote(cust.id, {"discount_pct": -1})
    db.rollback()


# ── Quote ROUTES catch the ValueError (no 500s, no stored damage) ─────────────

@pytest.fixture()
def client(db):
    """No lifespan context: the db fixture already activated this test's
    in-memory engine and seeded ADMIN id 1, and in the in-memory test env
    get_current_user_id resolves cookie-less requests to DEFAULT_USER_ID=1 —
    so every route call below acts as that ADMIN (RBAC never interferes)."""
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def test_route_header_discount_150_bounces_303_with_error(db, client):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {"discount_pct": 5})

    r = client.post(f"/quotes/{quote.id}", data={"discount_pct": "150"})
    assert r.status_code == 303, (
        f"header save with discount 150 must bounce, not {r.status_code} "
        "(500 = the route lost its ValueError catch)")
    loc = unquote(r.headers.get("location", ""))
    assert f"/quotes/{quote.id}" in loc and "error=" in loc, loc
    assert "between 0 and 100" in loc

    db.expire_all()
    stored = db.query(Quote).filter(Quote.id == quote.id).first()
    assert stored.discount_pct == 5.0  # rejected write left the header untouched


def test_route_line_discount_negative_returns_400_fragment(db, client):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {})
    line = qsvc.add_line(quote.id, None, _line(10))[0]

    r = client.post(
        f"/quotes/{quote.id}/lines/{line.id}", data={"discount_pct": "-50"},
    )
    assert r.status_code == 400, (
        f"line update with discount -50 must return the 400 error fragment, "
        f"not {r.status_code}")
    assert "between 0 and 100" in r.text

    db.expire_all()
    stored = db.query(QuoteLine).filter(QuoteLine.id == line.id).first()
    assert stored.discount_pct == 10.0


def test_route_new_quote_for_poisoned_customer_creates_with_zero(db, client):
    """A customer whose STORED standing discount was written around the clamp
    (legacy row) must not brick '+ Quote': the quote is created with header
    discount 0 (out-of-range = UNSET), and the add-line copy site sanitizes
    the poisoned default instead of copying 150 onto the line."""
    cust = _customer(db)
    cust.discount_pct = 150.0
    db.commit()

    r = client.post("/quotes/new", data={"customer_id": str(cust.id)})
    assert r.status_code == 303, (
        f"POST /quotes/new 500'd for a poisoned customer: {r.status_code}")
    loc = r.headers.get("location", "")
    assert loc.startswith("/quotes/"), loc
    quote_id = int(loc.rstrip("/").split("/")[-1].split("?")[0])

    db.expire_all()
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    assert quote is not None
    assert quote.discount_pct == 0.0, (
        f"poisoned customer default leaked onto the quote header: "
        f"{quote.discount_pct}")

    # Copy-site sanitizer (_safe_customer_discount): a line added with NO
    # explicit discount auto-applies the customer default — 150 must land as 0.
    lines = QuoteService(db, _UID).add_line(quote.id, None, _line())
    assert lines[0].discount_pct == 0.0, (
        f"add_line copied the raw poisoned customer discount: "
        f"{lines[0].discount_pct}")


def test_route_convert_to_so_with_poisoned_line_bounces_not_500(db, client):
    qsvc = QuoteService(db, _UID)
    quote = qsvc.create_quote(_customer(db).id, {})
    line = qsvc.add_line(quote.id, None, _line(0))[0]
    # Legacy poisoned LINE discount, written around the validator.
    line.discount_pct = 150.0
    db.commit()

    r = client.post(f"/quotes/{quote.id}/convert-to-so", data={})
    assert r.status_code == 303, (
        f"convert-to-so of a poisoned quote must bounce with a flash, "
        f"not {r.status_code}")
    loc = unquote(r.headers.get("location", ""))
    assert f"/quotes/{quote.id}" in loc and "error=" in loc, loc

    db.expire_all()
    fresh = db.query(Quote).filter(Quote.id == quote.id).first()
    assert fresh.converted_to_so_id is None, "quote converted despite the error"
    assert fresh.outcome != QuoteOutcome.WON
