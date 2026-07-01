"""
tests/test_dashboard_todays_cash.py
===================================
Regression for the headed-browser QA finding: the dashboard "Today's Cash" card
read $0 right after a same-day payment.

Root cause
----------
Payments are stamped with ``datetime.utcnow()`` (naive UTC). The dashboard
summed payments where ``func.date(payment_date) == date.today()`` — i.e. it
compared the payment's *UTC* calendar date against the server's *local* date.
For a US-Mountain shop, every payment taken after ~5–6pm local is stamped on the
*next* UTC date, so it dropped out of "today" and the card showed $0.

Fix under test
--------------
``app.routers.dashboard._local_day_utc_window`` computes the shop's LOCAL day,
then the half-open ``[utc_start, utc_end)`` window (in naive UTC) that bounds it.
The route filters ``utc_start <= payment_date < utc_end``.

These tests are timezone-robust: they never assume the CI box is Mountain time.
Expected windows are derived from the same local-offset logic the code uses, and
the "evening-local / next-day-UTC" case injects a pinned reference ``now``.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

import app.database as _appdb
from app.constants import PaymentDirection, PaymentStatus
from app.models.customer import Customer
from app.models.invoice import Payment
from app.routers.dashboard import _local_day_utc_window
from tests.conftest import activate, fresh_engine

_ENGINE = fresh_engine()
_seq = itertools.count(1)


@pytest.fixture(scope="module")
def client():
    activate(_ENGINE)
    from app.main import app
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
    c = Customer(company_name=f"Cash Co {next(_seq)}")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _payment(db, cust, *, amount, payment_date):
    p = Payment(
        customer_id=cust.id,
        amount_received=amount,
        status=PaymentStatus.APPLIED,
        direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
        payment_date=payment_date,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ── Unit: the windowing logic itself (timezone-robust, injected `now`) ─────────

def test_window_brackets_local_day_in_utc():
    """The returned UTC window must bound exactly the local calendar day."""
    # Pin a reference instant; derive expectations from the SAME offset logic.
    ref = datetime(2026, 6, 30, 14, 0, 0)  # arbitrary local wall-clock "now"
    local_today, utc_start, utc_end = _local_day_utc_window(now=ref)

    assert local_today == ref.date()
    # Window is exactly 24h wide and half-open.
    assert utc_end - utc_start == timedelta(days=1)
    # Both bounds are naive (same basis as utcnow()-stamped payment_date).
    assert utc_start.tzinfo is None and utc_end.tzinfo is None

    # Recompute the expected naive-UTC bounds independently from the OS offset.
    local_tz = ref.astimezone().tzinfo
    expect_start = (
        datetime(ref.year, ref.month, ref.day, tzinfo=local_tz)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    assert utc_start == expect_start
    assert utc_end == expect_start + timedelta(days=1)


def test_evening_local_payment_is_inside_window():
    """An evening-local payment (which may be next-day-UTC) is inside the window.

    This is the exact failure mode from the QA report: late-local payment whose
    naive-UTC stamp can roll to the next calendar date. We reconstruct the naive
    UTC instant for "today 23:30 local" and assert it lands inside the window.
    """
    ref = datetime(2026, 6, 30, 14, 0, 0)
    local_today, utc_start, utc_end = _local_day_utc_window(now=ref)

    local_tz = ref.astimezone().tzinfo
    # 11:30pm LOCAL today, expressed as the naive UTC instant we'd store.
    evening_local = datetime(
        local_today.year, local_today.month, local_today.day, 23, 30, tzinfo=local_tz
    )
    evening_utc_naive = evening_local.astimezone(timezone.utc).replace(tzinfo=None)

    assert utc_start <= evening_utc_naive < utc_end


# ── Route: Today's Cash sums the local day, not the UTC day ────────────────────

def _todays_cash_str(client) -> str:
    """Return the rendered 'Today's Cash' figure string (template uses {:,.0f}).

    We render the dashboard, then extract the value from the Today's Cash card so
    assertions don't false-match the same dollar string elsewhere on the page.
    """
    r = client.get("/")
    assert r.status_code == 200, r.text
    body = r.text
    marker = "Today's Cash"
    idx = body.index(marker)
    # The value is the next ``$<digits-with-commas>`` after the label.
    import re

    m = re.search(r"\$([0-9,]+)", body[idx:])
    assert m, "Today's Cash value not found in rendered dashboard"
    return m.group(1)


def test_todays_cash_counts_evening_local_payment(client, db):
    """A payment placed inside today's local window IS counted in Today's Cash.

    We place it 1 minute before the window's UTC end — i.e. the very last sliver
    of the local day, which under the old UTC-date compare was the part most
    likely to be mis-bucketed as 'tomorrow'. Template renders with {:,.0f}, so we
    use a whole-dollar amount and assert on the comma-grouped integer.
    """
    _local_today, _utc_start, utc_end = _local_day_utc_window()
    cust = _customer(db)
    amount = 1234.0
    inside = utc_end - timedelta(minutes=1)  # last minute of the local day (UTC basis)
    _payment(db, cust, amount=amount, payment_date=inside)

    assert _todays_cash_str(client) == "1,234"


def test_payment_on_a_different_local_day_is_excluded(client, db):
    """A payment clearly on a different local day is NOT in Today's Cash.

    Fresh isolated engine per assertion: this test runs first only if alone, so
    we assert the *delta* — Today's Cash must be unchanged by an out-of-window
    payment we add to a brand-new customer.
    """
    before = _todays_cash_str(client)
    _local_today, utc_start, _utc_end = _local_day_utc_window()
    cust = _customer(db)
    # 12 hours before this local day even began → unambiguously yesterday-local.
    outside = utc_start - timedelta(hours=12)
    _payment(db, cust, amount=9999.0, payment_date=outside)

    after = _todays_cash_str(client)
    assert after == before  # out-of-window payment did not move Today's Cash


def test_todays_cash_sums_only_in_window(client, db):
    """Mixed payments: in-window contributes to the total, out-of-window does not."""
    before_str = _todays_cash_str(client)
    before = int(before_str.replace(",", ""))

    _local_today, utc_start, _utc_end = _local_day_utc_window()
    cust = _customer(db)
    in_amt = 4321.0
    out_amt = 5000.0
    _payment(db, cust, amount=in_amt, payment_date=utc_start + timedelta(hours=2))
    _payment(db, cust, amount=out_amt, payment_date=utc_start - timedelta(days=1))

    after = int(_todays_cash_str(client).replace(",", ""))
    # Only the in-window payment moved the needle.
    assert after == before + int(in_amt)
