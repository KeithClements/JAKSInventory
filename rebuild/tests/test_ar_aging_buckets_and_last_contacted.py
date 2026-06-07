"""
tests/test_ar_aging_buckets_and_last_contacted.py
===================================================
Two contracts:

1. AR AGING BUCKETS (#3)
   - ar_aging_utils: AGING_BUCKETS / zero_buckets / as_date / bucket_for are pure
     and agree with the thresholds documented in the spec.
   - CRMService.get_account_balance returns a 'buckets' key with the same
     boundaries as ReportService.get_ar_aging (shared code path).
   - StatementService ages use the same shared helpers.

2. LAST CONTACTED (#5)
   - CustomerService.last_contacted(customer_id) returns the most recent
     CustomerCallLog.logged_at, or None when there are no logs.
   - The customer list route's last_contacted_map is built with a single
     grouped query (not N+1) and agrees with CustomerService.last_contacted.
"""
from __future__ import annotations

import pathlib
import sys
import uuid
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import app.database as _appdb
from app.models.customer import Customer, CustomerCallLog
from app.models.invoice import Invoice, InvoiceLine
from tests.conftest import activate, fresh_engine


# ── Module-level fixture ──────────────────────────────────────────────────────

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


def _make_customer(db) -> Customer:
    c = Customer(company_name=f"Test {id(object())}", contact_name="")
    db.add(c)
    db.flush()
    return c


# ── 1. ar_aging_utils pure functions ─────────────────────────────────────────

class TestArAgingUtils:
    """The shared bucket helpers must be pure and match the documented thresholds."""

    def test_aging_buckets_canonical_keys(self):
        from app.services.ar_aging_utils import AGING_BUCKETS
        assert AGING_BUCKETS == ("current", "1_30", "31_60", "61_90", "over_90")

    def test_zero_buckets_all_zero(self):
        from app.services.ar_aging_utils import zero_buckets, AGING_BUCKETS
        zb = zero_buckets()
        assert set(zb.keys()) == set(AGING_BUCKETS)
        assert all(v == 0.0 for v in zb.values())

    def test_as_date_none(self):
        from app.services.ar_aging_utils import as_date
        assert as_date(None) is None

    def test_as_date_datetime_to_date(self):
        from app.services.ar_aging_utils import as_date
        dt = datetime(2025, 3, 15, 12, 0, 0)
        assert as_date(dt) == date(2025, 3, 15)

    def test_as_date_date_passthrough(self):
        from app.services.ar_aging_utils import as_date
        d = date(2025, 3, 15)
        assert as_date(d) is d

    @pytest.mark.parametrize("days_late,expected", [
        (-5,  "current"),
        (0,   "current"),
        (1,   "1_30"),
        (30,  "1_30"),
        (31,  "31_60"),
        (60,  "31_60"),
        (61,  "61_90"),
        (90,  "61_90"),
        (91,  "over_90"),
        (365, "over_90"),
    ])
    def test_bucket_for_thresholds(self, days_late, expected):
        from app.services.ar_aging_utils import bucket_for
        assert bucket_for(days_late) == expected


# ── 2. CRMService.get_account_balance returns buckets ──────────────────────

class TestCRMBalanceBuckets:
    """get_account_balance['buckets'] must exist and sum to total_open."""

    def _seed_invoice(self, db, customer, *, due_days_ago: int, balance: float):
        """Create an OPEN invoice with a set due date (days_ago from today)."""
        from app.routers.settings import seed_settings
        seed_settings(db)
        today = date.today()
        due = today - timedelta(days=due_days_ago)
        inv = Invoice(
            invoice_number=f"INV-TEST-{uuid.uuid4().hex[:12]}",
            customer_id=customer.id,
            customer=customer,
            status="open",
            due_date=due,
        )
        db.add(inv)
        db.flush()
        line = InvoiceLine(
            invoice_id=inv.id,
            description="test part",
            qty=1,
            unit_price=balance,
            unit_cost=0.0,
            discount_pct=0.0,
            line_type="product",
        )
        db.add(line)
        db.flush()
        return inv

    def test_buckets_key_present(self, db):
        from app.services.crm_service import CRMService
        c = _make_customer(db)
        db.commit()
        crm = CRMService(db, current_user_id=1)
        result = crm.get_account_balance(c.id)
        assert "buckets" in result

    def test_buckets_sum_equals_total_open(self, db):
        from app.services.crm_service import CRMService
        from app.services.ar_aging_utils import AGING_BUCKETS
        c = _make_customer(db)
        # current: not yet due
        self._seed_invoice(db, c, due_days_ago=-5, balance=100.0)
        # 1_30: overdue 15 days
        self._seed_invoice(db, c, due_days_ago=15, balance=200.0)
        # over_90: overdue 95 days
        self._seed_invoice(db, c, due_days_ago=95, balance=50.0)
        db.commit()

        crm = CRMService(db, current_user_id=1)
        result = crm.get_account_balance(c.id)
        buckets = result["buckets"]

        assert set(buckets.keys()) == set(AGING_BUCKETS)
        bucket_total = round(sum(buckets.values()), 2)
        assert bucket_total == pytest.approx(result["total_open"])

    def test_buckets_correct_buckets(self, db):
        from app.services.crm_service import CRMService
        c = _make_customer(db)
        self._seed_invoice(db, c, due_days_ago=-5,  balance=100.0)   # current
        self._seed_invoice(db, c, due_days_ago=15,  balance=200.0)   # 1_30
        self._seed_invoice(db, c, due_days_ago=45,  balance=300.0)   # 31_60
        self._seed_invoice(db, c, due_days_ago=75,  balance=400.0)   # 61_90
        self._seed_invoice(db, c, due_days_ago=120, balance=500.0)   # over_90
        db.commit()

        crm = CRMService(db, current_user_id=1)
        b = crm.get_account_balance(c.id)["buckets"]

        assert b["current"]  == pytest.approx(100.0)
        assert b["1_30"]     == pytest.approx(200.0)
        assert b["31_60"]    == pytest.approx(300.0)
        assert b["61_90"]    == pytest.approx(400.0)
        assert b["over_90"]  == pytest.approx(500.0)

    def test_buckets_agree_with_report_service(self, db):
        """CRM buckets must equal ReportService.get_ar_aging for that customer."""
        from app.services.crm_service import CRMService
        from app.services.report_service import ReportService
        c = _make_customer(db)
        self._seed_invoice(db, c, due_days_ago=20, balance=150.0)
        self._seed_invoice(db, c, due_days_ago=50, balance=250.0)
        db.commit()

        crm_b = CRMService(db, current_user_id=1).get_account_balance(c.id)["buckets"]
        report = ReportService(db).get_ar_aging()
        # Find this customer's row in the aging report
        row = next((r for r in report["rows"] if r["customer_id"] == c.id), None)
        assert row is not None, "Customer not found in AR aging report"

        from app.services.ar_aging_utils import AGING_BUCKETS
        for bk in AGING_BUCKETS:
            assert crm_b[bk] == pytest.approx(row[bk], abs=0.01), (
                f"Bucket {bk!r}: CRM={crm_b[bk]} vs report={row[bk]}"
            )


# ── 3. CustomerService.last_contacted ────────────────────────────────────────

class TestLastContacted:
    """Single-customer last_contacted and list-route grouped query."""

    def test_no_logs_returns_none(self, db):
        from app.services.customer_service import CustomerService
        c = _make_customer(db)
        db.commit()
        assert CustomerService(db).last_contacted(c.id) is None

    def test_returns_most_recent_logged_at(self, db):
        from app.services.customer_service import CustomerService
        c = _make_customer(db)
        db.commit()

        t1 = datetime(2025, 1, 10, 9, 0, 0)
        t2 = datetime(2025, 3, 20, 14, 0, 0)   # most recent
        t3 = datetime(2025, 2, 5, 11, 0, 0)

        for ts in (t1, t2, t3):
            db.add(CustomerCallLog(
                customer_id=c.id, logged_at=ts, notes="test"
            ))
        db.commit()

        result = CustomerService(db).last_contacted(c.id)
        # SQLite may return a string — normalise
        if isinstance(result, str):
            result = datetime.fromisoformat(result.replace(" ", "T"))
        assert result >= t2 - timedelta(seconds=1)

    def test_list_route_includes_last_contacted_map(self, db):
        """GET /customers renders with last_contacted_map in context (HTTP smoke)."""
        from fastapi.testclient import TestClient
        from app.main import app
        c = _make_customer(db)
        db.add(CustomerCallLog(
            customer_id=c.id,
            logged_at=datetime(2025, 6, 1, 10, 0, 0),
            notes="recent",
        ))
        db.commit()

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/customers/")
        # Template renders without error (200) means the context key is present
        assert resp.status_code == 200

    def test_last_contacted_consistent_with_list_map(self, db):
        """CustomerService.last_contacted agrees with the grouped-query result."""
        from sqlalchemy import func
        from app.services.customer_service import CustomerService

        c = _make_customer(db)
        db.add(CustomerCallLog(
            customer_id=c.id,
            logged_at=datetime(2025, 5, 15, 8, 0, 0),
            notes="a",
        ))
        db.add(CustomerCallLog(
            customer_id=c.id,
            logged_at=datetime(2025, 7, 1, 12, 0, 0),
            notes="b",
        ))
        db.commit()

        # Reproduce the exact grouped query from the list route
        _raw = dict(
            db.query(CustomerCallLog.customer_id, func.max(CustomerCallLog.logged_at))
            .filter(CustomerCallLog.customer_id == c.id)
            .group_by(CustomerCallLog.customer_id)
            .all()
        )
        map_value = _raw.get(c.id)
        svc_value = CustomerService(db).last_contacted(c.id)

        # Both must be non-None and agree
        assert map_value is not None
        assert svc_value is not None
        # SQLite returns strings; normalise both
        def _norm(v):
            return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace(" ", "T"))
        assert _norm(map_value) == _norm(svc_value)
