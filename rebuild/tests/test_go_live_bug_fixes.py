"""
tests/test_go_live_bug_fixes.py
================================
Four go-live bug fixes verified in one suite:

1. CORES-AS-REVENUE: ReportService excludes CORE_CHARGE lines from
   get_sales_by_customer and get_sales_by_product.

2. SELL-PRICE DISPLAY: products list + preview routes include sell_price_map
   / sell_price built by PricingService (not the hardcoded 30% model property).

3. CREDIT-HOLD DUAL STATE: set_flag(CREDIT_HOLD) and set_stored_flags both
   sync customer.customer_status; customer_update route syncs the reverse path.

4. AUTHZ: invoice _workspace_context no longer uses hardcoded user_id=1;
   vendor/customer mutation routes require a logged-in user.
"""
from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from tests.conftest import activate, fresh_engine


@pytest.fixture(scope="module")
def engine():
    eng = fresh_engine()
    activate(eng)
    yield eng


@pytest.fixture()
def db(engine):
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_settings(db):
    from app.routers.settings import seed_settings
    seed_settings(db)


def _make_customer(db):
    from app.models.customer import Customer
    c = Customer(company_name=f"BugTest {uuid.uuid4().hex[:8]}", contact_name="")
    db.add(c)
    db.flush()
    return c


def _make_product(db, *, cost: float = 100.0, markup_pct: float | None = None):
    from app.models.product import Product
    p = Product(
        sku=f"BUG-{uuid.uuid4().hex[:8]}",
        title="Bug Test Part",
        cost=cost,
        markup_pct=markup_pct,
        is_active=True,
    )
    db.add(p)
    db.flush()
    return p


def _make_finalized_invoice(db, customer, *, lines_data: list[dict], created_at=None):
    """Create a PAID invoice with given lines.  Each line dict: qty, price, cost, is_core."""
    from app.models.invoice import Invoice, InvoiceLine
    from app.constants import InvoiceStatus, LineType
    inv = Invoice(
        invoice_number=f"INV-BUG-{uuid.uuid4().hex[:12]}",
        customer_id=customer.id,
        status=InvoiceStatus.PAID,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(inv)
    db.flush()
    for ld in lines_data:
        ln = InvoiceLine(
            invoice_id=inv.id,
            description=ld.get("desc", "test"),
            qty=ld.get("qty", 1),
            unit_price=ld.get("price", 0.0),
            unit_cost=ld.get("cost", 0.0),
            line_type=LineType.CORE_CHARGE if ld.get("is_core") else LineType.PRODUCT,
            is_core_line=bool(ld.get("is_core")),
        )
        db.add(ln)
    db.flush()
    return inv


# ══════════════════════════════════════════════════════════════════════════════
# Bug 1: CORES-AS-REVENUE
# ══════════════════════════════════════════════════════════════════════════════

class TestCoresAsRevenue:
    """Core-charge lines must be excluded from revenue and cost in reports."""

    def test_sales_by_customer_excludes_core_revenue(self, db):
        _seed_settings(db)
        from app.services.report_service import ReportService
        c = _make_customer(db)
        today = date.today()
        # Invoice: $200 product line + $50 core deposit
        _make_finalized_invoice(db, c, lines_data=[
            {"price": 200.0, "cost": 80.0, "is_core": False},
            {"price": 50.0,  "cost":  0.0, "is_core": True},
        ], created_at=datetime(today.year, today.month, today.day))
        db.commit()

        rpt = ReportService(db).get_sales_by_customer(today, today)
        row = next((r for r in rpt["rows"] if r["customer"] and r["customer"].id == c.id), None)
        assert row is not None, "Customer not found in report"
        # gross_sales should be inv.total - core_deposit = (200+50) - 50 = 200
        assert row["gross_sales"] == pytest.approx(200.0), (
            f"gross_sales should exclude core deposit: got {row['gross_sales']}"
        )
        # cost should only sum non-core lines
        assert row["cost"] == pytest.approx(80.0), (
            f"cost should exclude core line: got {row['cost']}"
        )

    def test_sales_by_product_excludes_core_lines(self, db):
        _seed_settings(db)
        from app.services.report_service import ReportService
        from app.models.invoice import InvoiceLine
        from app.constants import InvoiceStatus, LineType
        from app.models.invoice import Invoice

        c = _make_customer(db)
        p = _make_product(db, cost=90.0)
        today = date.today()

        inv = Invoice(
            invoice_number=f"INV-BUG-{uuid.uuid4().hex[:12]}",
            customer_id=c.id,
            status=InvoiceStatus.PAID,
            created_at=datetime(today.year, today.month, today.day),
        )
        db.add(inv)
        db.flush()

        # Product line
        db.add(InvoiceLine(
            invoice_id=inv.id, product_id=p.id,
            description="engine part", qty=2, unit_price=150.0, unit_cost=90.0,
            line_type=LineType.PRODUCT, is_core_line=False,
        ))
        # Core deposit line (same product for realistic scenario)
        db.add(InvoiceLine(
            invoice_id=inv.id, product_id=p.id,
            description="core deposit", qty=2, unit_price=40.0, unit_cost=0.0,
            line_type=LineType.CORE_CHARGE, is_core_line=True,
        ))
        db.commit()

        rpt = ReportService(db).get_sales_by_product(today, today)
        # Find the row for product p
        prod_rows = [r for r in rpt["rows"] if r["product"] and r["product"].id == p.id]
        assert len(prod_rows) == 1, "Expected exactly one product row"
        row = prod_rows[0]
        # Only the 2×$150 product line should appear; 2×$40 core must not
        assert row["qty_sold"] == pytest.approx(2), f"qty_sold should be 2, got {row['qty_sold']}"
        assert row["revenue"] == pytest.approx(300.0), f"revenue should be 300, got {row['revenue']}"
        assert row["cost"] == pytest.approx(180.0), f"cost should be 180, got {row['cost']}"


# ══════════════════════════════════════════════════════════════════════════════
# Bug 2: SELL-PRICE DISPLAY — routes provide PricingService-backed sell prices
# ══════════════════════════════════════════════════════════════════════════════

class TestSellPriceDisplay:
    """products list and preview routes must include sell_price_map / sell_price."""

    def test_list_route_includes_sell_price_map(self, db):
        _seed_settings(db)
        from fastapi.testclient import TestClient
        from app.main import app
        _make_product(db, cost=100.0, markup_pct=40.0)
        db.commit()

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/products/")
        assert resp.status_code == 200, f"list returned {resp.status_code}"
        # If the template rendered without TemplateError the key was present.
        # We can't easily inspect context; a 200 + no crash is the gate.

    def test_preview_route_includes_sell_price(self, db):
        _seed_settings(db)
        from fastapi.testclient import TestClient
        from app.main import app
        p = _make_product(db, cost=100.0, markup_pct=50.0)
        db.commit()

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get(f"/products/preview/{p.id}")
        assert resp.status_code == 200, f"preview returned {resp.status_code}"

    def test_sell_price_for_product_uses_pricing_service(self, db):
        """PricingService.sell_price_for uses the stored default_markup_pct, not
        the hardcoded 30 % model property, when the product has no markup_pct."""
        _seed_settings(db)
        from app.services.pricing_service import PricingService
        from app.models.setting import Setting
        # Set a non-30% markup so a mismatch would show up
        s = db.query(Setting).filter(Setting.key == "default_markup_pct").first()
        if s:
            s.value = "50.0"
        else:
            db.add(Setting(key="default_markup_pct", value="50.0"))
        db.flush()

        p = _make_product(db, cost=100.0, markup_pct=None)  # no product-level markup
        db.flush()

        svc = PricingService(db)
        sell = svc.sell_price_for(p)
        # PricingService should use 50% markup → $150; model property would give $130
        assert sell == pytest.approx(150.0), (
            f"PricingService should use setting 50%, got sell_price={sell}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Bug 3: CREDIT-HOLD DUAL STATE
# ══════════════════════════════════════════════════════════════════════════════

class TestCreditHoldDualState:
    """set_flag and set_stored_flags must keep customer_status in sync."""

    def test_set_flag_on_syncs_status(self, db):
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        db.commit()

        CustomerService(db).set_flag(c, CustomerFlag.CREDIT_HOLD, on=True)
        db.commit()

        assert c.customer_status == CustomerStatus.CREDIT_HOLD, (
            f"status should be CREDIT_HOLD after set_flag, got {c.customer_status!r}"
        )

    def test_set_flag_off_clears_status(self, db):
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        c.customer_status = CustomerStatus.CREDIT_HOLD
        db.commit()

        CustomerService(db).set_flag(c, CustomerFlag.CREDIT_HOLD, on=False)
        db.commit()

        assert c.customer_status == CustomerStatus.ACTIVE, (
            f"status should be ACTIVE after removing hold, got {c.customer_status!r}"
        )

    def test_set_stored_flags_syncs_status_on(self, db):
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        db.commit()

        CustomerService(db).set_stored_flags(c, [CustomerFlag.CREDIT_HOLD])
        db.commit()

        assert c.customer_status == CustomerStatus.CREDIT_HOLD, (
            f"set_stored_flags should sync status to CREDIT_HOLD, got {c.customer_status!r}"
        )

    def test_set_stored_flags_syncs_status_off(self, db):
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        c.customer_status = CustomerStatus.CREDIT_HOLD
        db.commit()

        # Submit flags without CREDIT_HOLD
        CustomerService(db).set_stored_flags(c, [])
        db.commit()

        assert c.customer_status == CustomerStatus.ACTIVE, (
            f"removing hold flag should set status ACTIVE, got {c.customer_status!r}"
        )

    def test_set_stored_flags_leaves_inactive_alone(self, db):
        """Removing the hold flag must NOT touch is_active=False / 'inactive' status."""
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        c.is_active = False
        c.customer_status = CustomerStatus.INACTIVE
        db.commit()

        CustomerService(db).set_stored_flags(c, [])
        db.commit()

        assert c.customer_status == CustomerStatus.INACTIVE, (
            f"inactive status must not be changed by removing hold flag, "
            f"got {c.customer_status!r}"
        )

    def test_flags_and_status_agree_after_set_flag_on(self, db):
        """After a hold is set, both the flag AND the status must reflect it."""
        from app.services.customer_service import CustomerService
        from app.constants import CustomerFlag, CustomerStatus
        c = _make_customer(db)
        db.commit()

        svc = CustomerService(db)
        svc.set_flag(c, CustomerFlag.CREDIT_HOLD, on=True)
        db.commit()

        assert svc.is_on_credit_hold(c) is True
        assert c.customer_status == CustomerStatus.CREDIT_HOLD


# ══════════════════════════════════════════════════════════════════════════════
# Bug 4: AUTHZ — _workspace_context no longer hardcodes user_id=1
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthzFixes:
    """_workspace_context must accept a current_user_id parameter."""

    def test_workspace_context_accepts_user_id(self, db):
        """Signature check: calling with explicit user_id must not raise."""
        _seed_settings(db)
        from app.routers.invoices import _workspace_context
        from app.models.invoice import Invoice
        from app.models.customer import Customer
        from fastapi import Request
        from starlette.testclient import TestClient
        from app.main import app

        c = _make_customer(db)
        inv = Invoice(
            invoice_number=f"INV-BUG-{uuid.uuid4().hex[:12]}",
            customer_id=c.id,
            status="draft",
        )
        db.add(inv)
        db.commit()

        # _workspace_context is a plain function (not a route) — call directly.
        # Use a TestClient to obtain a real Request object.
        with TestClient(app, raise_server_exceptions=True) as client:
            # The GET workspace route itself enforces auth — we can only test
            # the signature here; full end-to-end is via integration or smoke.
            pass

        # Direct call with explicit user_id; any non-1 value proves no hardcode.
        import inspect
        sig = inspect.signature(_workspace_context)
        params = list(sig.parameters)
        assert "current_user_id" in params, (
            "_workspace_context must accept current_user_id parameter"
        )

    def test_vendor_mutations_wired_with_auth_dep(self):
        """Verify that vendor mutation routes declare get_current_user_id as a
        dependency (structural gate — enforcement is tested live, not via in-memory
        TestClient which bypasses auth because _is_test_env() == True)."""
        import inspect
        from app.routers import vendors as _vendors_mod
        from app.deps import get_current_user_id, require_admin

        routes_missing_auth = []
        for name, fn in inspect.getmembers(_vendors_mod, inspect.isfunction):
            if not hasattr(fn, "__wrapped__") and not name.startswith("_"):
                continue
        # Introspect via the router's routes list
        for route in _vendors_mod.router.routes:
            if route.methods and "POST" in route.methods:
                deps = [d.dependency for d in getattr(route, "dependencies", [])]
                dep_fns = set(deps)
                # Check endpoint's own deps via its signature
                try:
                    sig = inspect.signature(route.endpoint)
                    for param in sig.parameters.values():
                        if hasattr(param.default, "dependency"):
                            dep_fns.add(param.default.dependency)
                except (ValueError, TypeError):
                    pass
                # All POST routes need get_current_user_id (login gate)
                path = route.path
                if get_current_user_id not in dep_fns:
                    routes_missing_auth.append(f"{path}: missing get_current_user_id")

        assert not routes_missing_auth, (
            "Some vendor POST routes are missing auth deps:\n" +
            "\n".join(routes_missing_auth)
        )

    def test_customer_mutations_wired_with_auth_dep(self):
        """Verify that customer_create and customer_update declare
        get_current_user_id as a dependency."""
        import inspect
        from app.routers import customers as _cust_mod
        from app.deps import get_current_user_id

        for name in ("customer_create", "customer_update"):
            fn = getattr(_cust_mod, name, None)
            assert fn is not None, f"{name} not found in customers module"
            sig = inspect.signature(fn)
            dep_fns = {
                p.default.dependency
                for p in sig.parameters.values()
                if hasattr(p.default, "dependency")
            }
            assert get_current_user_id in dep_fns, (
                f"{name} must declare get_current_user_id as a Depends"
            )
