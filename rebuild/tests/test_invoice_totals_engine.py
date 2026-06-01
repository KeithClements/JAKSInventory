"""
tests/test_invoice_totals_engine.py
====================================
Regression tests pinning the ONE invoice-totals engine
(``app.invoice_totals.compute_invoice_totals``) that backs BOTH:

  * the Invoice model money properties (``subtotal`` / ``discount_amount`` /
    ``tax_amount`` / ``total`` / ``balance_due``) — read by the List "Total"
    column, the row Preview panel, and the print/PDF, and
  * ``InvoiceService.calculate_totals`` — read by the workspace totals panel.

The invariant locked in here: for ANY invoice shape,
``invoice.total == InvoiceService.calculate_totals(id)["total"]`` (and the same
for every other money figure). The discount fix already aligned the two engines
for parts/freight invoices (``tests/test_invoice_discount.py``); these tests
finish the job for the two remaining divergences.

Divergences these tests guard against (both real before the fix)
----------------------------------------------------------------
A. CORE TAX — the model's ``tax_amount`` taxed the entire ``subtotal`` (cores
   included), while ``calculate_totals`` excluded cores. So a taxable invoice
   with a core charge showed a HIGHER total on the List/Preview/print than in the
   workspace. Cores are a refundable DEPOSIT, never a sale
   (``app.constants.NON_TAXABLE_LINE_TYPES``) → never taxed. The model was the
   outlier; it now matches the service.
B. FEE LINES DROPPED — ``calculate_totals`` bucketed only parts / cores / freight
   and SILENTLY dropped every other line type (MISC_FEE, RESTOCKING_FEE, NSF_FEE,
   …) from the total, while the model included them. This is reachable in
   production: ``PaymentService.process_nsf`` creates an invoice whose only line
   is an NSF_FEE — its workspace total rendered ``$0.00`` while the List/print
   showed the real fee. Now every line counts toward the total.

Run with:
    .venv\\Scripts\\python.exe -m pytest tests/test_invoice_totals_engine.py -v

Uses an in-memory SQLite DB isolated from the live data/jaks.db. The module-level
patch of app.database must happen before any app imports.
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# ── Patch BEFORE any app.* imports ──────────────────────────────────────────
import app.database as _appdb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_appdb.engine = _TEST_ENGINE
_appdb.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

from app.models import __all_models__  # noqa: F401
from app.database import Base

Base.metadata.create_all(bind=_TEST_ENGINE)

# ── App imports (safe after patch) ────────────────────────────────────────────
import pytest

from app.constants import InvoiceStatus, LineType, UserRole
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.user import User
from app.services.invoice_service import InvoiceService

_counter = itertools.count(1)
_UID = 1


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _activate_db():
    from tests.conftest import activate
    activate(_TEST_ENGINE)
    yield


@pytest.fixture()
def db(_activate_db):
    session = _appdb.SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True, scope="module")
def _seed_admin_user(_activate_db):
    session = _appdb.SessionLocal()
    try:
        if not session.query(User).filter(User.id == 1).first():
            session.add(User(
                id=1,
                name="Test Admin",
                username="admin_totals",
                password_hash="[test-no-auth]",
                role=UserRole.ADMIN,
            ))
            session.commit()
    finally:
        session.close()


# ── Builders ────────────────────────────────────────────────────────────────

def _make_customer(db, discount_pct: float = 0.0, tax_rate: float = 0.0) -> Customer:
    n = next(_counter)
    c = Customer(
        company_name=f"TotCo-{n}",
        is_active=True,
        discount_pct=discount_pct,
        tax_rate=tax_rate,
        is_tax_exempt=(tax_rate == 0.0),
    )
    db.add(c)
    db.commit()
    return c


def _make_invoice(db, customer, lines, **header) -> Invoice:
    """Create a committed DRAFT invoice with explicit lines (no product needed)."""
    svc = InvoiceService(db, _UID)
    inv = svc.create_invoice(customer.id, dict(header), lines=lines)
    db.expire_all()
    return db.query(Invoice).filter(Invoice.id == inv.id).first()


def _totals(db, invoice_id: int) -> dict:
    """Fresh service + fresh read so we compare against committed state."""
    return InvoiceService(db, _UID).calculate_totals(invoice_id)


def _assert_engines_agree(db, inv: Invoice) -> dict:
    """The core invariant: every model money property equals the service figure."""
    totals = _totals(db, inv.id)
    assert inv.subtotal == totals["subtotal"]
    assert inv.discount_amount == totals["discount_amount"]
    assert inv.tax_amount == totals["tax_amount"]
    assert inv.total == totals["total"]
    assert inv.balance_due == totals["balance_due"]
    return totals


# ══════════════════════════════════════════════════════════════════════════════
# Divergence A — CORE TAX: cores are a refundable deposit, never taxed.
# ══════════════════════════════════════════════════════════════════════════════

def test_taxable_core_charge_model_matches_service(db):
    """A taxable invoice with a core charge: the core is NOT taxed, and the model
    (List/Preview/print) agrees with the workspace on the total.

    Before the fix the model taxed the whole subtotal (150 * 10% = 15 → total 165)
    while the service excluded the core (100 * 10% = 10 → total 160). They now agree.
    """
    cust = _make_customer(db, tax_rate=10.0)            # taxable @ 10%
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Pump", "qty": 1, "unit_price": 100.0},
            {"line_type": LineType.CORE_CHARGE, "description": "Core deposit", "qty": 1, "unit_price": 50.0},
        ],
        is_taxable=True, tax_rate=10.0,
    )

    totals = _assert_engines_agree(db, inv)

    assert totals["parts_subtotal"] == 100.00
    assert totals["core_subtotal"] == 50.00            # surfaced separately
    assert inv.subtotal == 150.00
    # The crux: tax is on PARTS ONLY — the $50 core is excluded from the base.
    assert totals["taxable_amount"] == 100.00
    assert inv.tax_amount == 10.00                     # 100 * 10%, NOT 150 * 10%
    assert inv.total == 160.00                         # 150 + 10 (core not taxed)


def test_core_line_never_carries_tax_on_frozen_path(db):
    """Frozen path (finalized invoice): per-line tax_amount is the source of truth.
    The core line's frozen tax is 0, so it stays out of the taxable base and total,
    and both engines agree."""
    cust = _make_customer(db, tax_rate=10.0)
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Head", "qty": 1, "unit_price": 100.0},
            {"line_type": LineType.CORE_CHARGE, "description": "Core deposit", "qty": 1, "unit_price": 50.0},
        ],
        is_taxable=True, tax_rate=10.0,
    )
    # Simulate InvoiceService.finalise freezing per-line tax: product taxed, core not.
    inv.tax_rate_snapshot = 10.0
    for ln in inv.lines:
        if ln.line_type == LineType.PRODUCT:
            ln.tax_amount = 10.00
        else:
            ln.tax_amount = 0.0
    db.commit()
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == inv.id).first()

    core = next(ln for ln in inv.lines if ln.line_type == LineType.CORE_CHARGE)
    assert core.tax_amount == 0.0                      # core never taxed
    assert core.is_taxable is False

    totals = _assert_engines_agree(db, inv)
    assert totals["taxable_amount"] == 100.00          # core excluded
    assert inv.tax_amount == 10.00
    assert inv.total == 160.00


# ══════════════════════════════════════════════════════════════════════════════
# Divergence B — FEE LINES: every line counts toward the total (none dropped).
# ══════════════════════════════════════════════════════════════════════════════

def test_restocking_fee_counts_toward_total(db):
    """A RESTOCKING_FEE line must be included in the total by BOTH engines.

    Before the fix calculate_totals dropped it (total = 200) while the model
    included it (total = 225)."""
    cust = _make_customer(db)                           # non-taxable
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Filter", "qty": 1, "unit_price": 200.0},
            {"line_type": LineType.RESTOCKING_FEE, "description": "Restocking 15%", "qty": 1, "unit_price": 25.0},
        ],
    )

    totals = _assert_engines_agree(db, inv)
    assert totals["parts_subtotal"] == 200.00
    assert totals["other_subtotal"] == 25.00           # the fee bucket
    assert inv.subtotal == 225.00
    assert inv.total == 225.00                          # fee NOT dropped


def test_nsf_fee_only_invoice_is_not_zero(db):
    """The reachable production bug: PaymentService.process_nsf creates an invoice
    whose ONLY line is an NSF_FEE. The workspace total used to render $0.00 (fee
    bucket dropped) while the List/print showed the real fee. Now both = the fee."""
    cust = _make_customer(db)
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.NSF_FEE, "description": "NSF / Returned Check Fee", "qty": 1, "unit_price": 35.0},
        ],
        notes="NSF fee — reversed payment",
    )

    totals = _assert_engines_agree(db, inv)
    assert totals["other_subtotal"] == 35.00
    assert totals["total"] == 35.00                     # was $0.00 before the fix
    assert inv.total == 35.00


def test_misc_fee_counts_and_is_not_taxed(db):
    """A MISC_FEE on a taxable invoice is included in the total but excluded from
    the tax base (it's a fee, not a sale)."""
    cust = _make_customer(db, tax_rate=8.0)
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Belt", "qty": 1, "unit_price": 100.0},
            {"line_type": LineType.MISC_FEE, "description": "Handling", "qty": 1, "unit_price": 20.0},
        ],
        is_taxable=True, tax_rate=8.0,
    )

    totals = _assert_engines_agree(db, inv)
    assert totals["other_subtotal"] == 20.00
    assert totals["taxable_amount"] == 100.00          # fee not taxed
    assert inv.tax_amount == 8.00                       # 100 * 8%
    assert inv.subtotal == 120.00
    assert inv.total == 128.00                          # 120 + 8 (fee in total, not taxed)


# ══════════════════════════════════════════════════════════════════════════════
# Kitchen sink — every bucket at once; the two engines agree on EVERY figure.
# ══════════════════════════════════════════════════════════════════════════════

def test_all_line_types_together_agree_on_every_figure(db):
    """Parts + core + freight + misc-fee + header discount + tax in one invoice.
    The model and the service must agree on every money figure they both expose."""
    cust = _make_customer(db, tax_rate=8.0)
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Turbo", "qty": 2, "unit_price": 100.0},   # parts 200
            {"line_type": LineType.CORE_CHARGE, "description": "Core", "qty": 1, "unit_price": 50.0},  # core 50
            {"line_type": LineType.FREIGHT, "description": "Freight", "qty": 1, "unit_price": 30.0},    # freight 30
            {"line_type": LineType.MISC_FEE, "description": "Shop fee", "qty": 1, "unit_price": 15.0},  # other 15
        ],
        discount_pct=10.0, is_taxable=True, tax_rate=8.0,
    )

    totals = _assert_engines_agree(db, inv)

    # Breakdown (draft fallback path: tax on discounted parts + freight; cores never).
    assert totals["parts_subtotal"] == 200.00
    assert totals["core_subtotal"] == 50.00
    assert totals["freight_subtotal"] == 30.00
    assert totals["other_subtotal"] == 15.00
    assert inv.subtotal == 295.00                       # 200 + 50 + 30 + 15
    assert inv.discount_amount == 20.00                 # 10% of parts (200)
    assert totals["taxable_amount"] == 210.00           # (200 - 20) + 30 freight; core & fee excluded
    assert inv.tax_amount == 16.80                       # 210 * 8%
    assert inv.total == 291.80                           # 295 - 20 + 16.80

    # Belt-and-suspenders: the figures the service exposes that the model doesn't
    # still tie back to the model's headline numbers.
    assert totals["discount_amount"] == inv.discount_amount
    assert totals["tax_amount"] == inv.tax_amount


def test_non_taxable_invoice_with_fee_and_core_agree(db):
    """Non-taxable account: discount on parts, core + fee pass through untaxed."""
    cust = _make_customer(db, discount_pct=10.0)        # non-taxable, 10% standing disc
    inv = _make_invoice(
        db, cust,
        lines=[
            {"line_type": LineType.PRODUCT, "description": "Gasket", "qty": 1, "unit_price": 300.0},
            {"line_type": LineType.CORE_CHARGE, "description": "Core", "qty": 1, "unit_price": 75.0},
            {"line_type": LineType.RESTOCKING_FEE, "description": "Restock", "qty": 1, "unit_price": 10.0},
        ],
        discount_pct=10.0,
    )

    totals = _assert_engines_agree(db, inv)
    assert inv.subtotal == 385.00                        # 300 + 75 + 10
    assert inv.discount_amount == 30.00                  # 10% of parts (300) only
    assert inv.tax_amount == 0.0
    assert inv.total == 355.00                           # 385 - 30
